"""Pure scoring functions: outlier multiplier, engagement-velocity z-scores,
local title clustering, and the blended 0-100 Opportunity Score.

This is a stateless, MCP-friendly adaptation of the scoring engine from
svx2027/yt-competitor-swipe's src/score.py. The math and the shape of the
Opportunity Score are unchanged; everything that read or wrote a file on
disk there (baselines cache, history ledger, calendar-phase-by-today's-date)
is replaced here with plain function arguments, so a single call can score
one batch of candidates with no filesystem or network access at all.

Left out of this scaffold on purpose (see README "Honest scope"):
Gemini-assisted clustering (local token-anchor clustering is the only
clustering method here), the calendar-tailwind signal (it depends on
"today's date" and a seasonal calendar file), and the sleeper/new-format
history signals (they depend on a multi-day ledger). All three are real,
disclosed gaps, not silently dropped signals.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9']+")

# Cross-niche English filler words - same fixed default as the source engine.
# A deployment on a different-language corpus would pass its own set via the
# `generic_tokens` config key instead of relying on this default.
DEFAULT_GENERIC_TOKENS = {
    "the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "how", "your",
    "you", "is", "are", "with", "this", "that", "best", "new", "full", "video",
    "live", "session", "class", "ep", "part", "free", "by", "from", "what",
    "official", "time", "vs", "top", "review",
}

DEFAULT_WEIGHTS = {
    "vph": 35, "outlier": 25, "engagement": 15,
    "demand_gap": 10, "convergence": 10, "calendar": 5,
}
DEFAULT_RELATION_WEIGHTS = {"direct": 1.0, "indirect": 0.8, "self": 0.0}
DEFAULT_ENGAGEMENT_SUBWEIGHTS = {
    "comment_vph_z": 0.50, "like_vph_z": 0.30, "eng_rate_z": 0.20,
}
DEFAULT_FLAGS_CFG = {
    "breakout_outlier_min": 2.0,
    "hot_engagement_z_min": 1.5,
}
DEFAULT_OUTLIER_CAP = 5.0
DEFAULT_CONVERGENCE_WINDOW_HOURS = 48
DEFAULT_CONVERGENCE_MIN_CLUSTER_SIZE = 3
DEFAULT_CLUSTER_MAX_DF = 0.4


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def zscores(values: list[float]) -> list[float]:
    """Population z-scores. Returns 0.0 for every element if std == 0."""
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(var)
    if std == 0:
        return [0.0] * n
    return [(v - mean) / std for v in values]


def pct_rank(value: float, sorted_vals: list[float]) -> float:
    if not sorted_vals:
        return 0.0
    below = sum(1 for x in sorted_vals if x < value)
    return 100.0 * below / len(sorted_vals)


def compute_outliers(videos: list[dict], baselines: dict[str, float] | None = None,
                      outlier_display_cap: float = 20.0) -> None:
    """Outlier multiplier = views-per-hour vs the channel's own expected pace.

    `baselines` is an optional {channel_id: median_vph} map computed by the
    caller from that channel's recent-upload history. Without one, this
    falls back to the weaker same-batch median (the source engine's own
    documented fallback order: vidIQ score > stored baseline > same-run
    median). Mutates each video dict in place, adding `outlier` and
    `outlier_source`.
    """
    baselines = baselines or {}
    by_channel: dict[str, list[dict]] = defaultdict(list)
    for v in videos:
        by_channel[v.get("channel_id", "")].append(v)
    same_run_median: dict[str, float] = {}
    for cid, vs in by_channel.items():
        vphs = sorted(x.get("vph", 0.0) for x in vs if x.get("vph", 0.0) > 0)
        if vphs:
            same_run_median[cid] = vphs[len(vphs) // 2]
    for v in videos:
        cid = v.get("channel_id", "")
        vph = v.get("vph", 0.0)
        base = baselines.get(cid)
        if base and base > 0:
            v["outlier"] = round(min(vph / base, outlier_display_cap), 2)
            v["outlier_source"] = "baseline"
            continue
        med = same_run_median.get(cid, 0)
        v["outlier"] = round(min(vph / med, outlier_display_cap), 2) if med > 0 else 1.0
        v["outlier_source"] = "same-run"


def _sig_tokens(title: str, keep: set[str], generic: set[str]) -> set[str]:
    toks = set()
    for t in _TOKEN_RE.findall((title or "").lower()):
        if t in keep:
            toks.add(t)
        elif t not in generic and len(t) > 2 and not t.isdigit():
            toks.add(t)
    return toks


def _filtered_token_sets(titles: list[str], keep: set[str], generic: set[str],
                          max_df: float) -> list[set]:
    """Significant tokens per title, with corpus-ubiquitous tokens removed.

    A token appearing in more than `max_df` of titles (a channel's own brand
    name, a recurring filler word) carries no subtopic signal and would
    connect everything, so it is dropped unless it is an explicit anchor.
    """
    raw = [_sig_tokens(t, keep, generic) for t in titles]
    n = len(raw)
    if n == 0:
        return raw
    df: Counter = Counter()
    for s in raw:
        df.update(s)
    drop = {t for t, c in df.items() if t not in keep and c > max_df * n}
    return [s - drop for s in raw]


def _local_clusters(token_sets: list[set]) -> list[list[int]]:
    """Group titles by their single most-distinctive (rarest) token.

    Anchor grouping instead of single-link union-find: union-find chains
    titles transitively through common bridge tokens and collapses a large
    corpus into one mega-cluster. Grouping each title under its rarest
    significant token yields tight, specific subtopics and cannot do that.
    """
    df: Counter = Counter()
    for s in token_sets:
        df.update(s)
    groups: dict[Any, list[int]] = defaultdict(list)
    for i, s in enumerate(token_sets):
        if not s:
            groups[("__singleton__", i)].append(i)
        else:
            anchor = min(s, key=lambda t: (df[t], t))
            groups[anchor].append(i)
    return list(groups.values())


def _cluster_label(token_sets: list[set], idxs: list[int]) -> str:
    """Top-3 most frequent tokens across the cluster's titles.

    Sets iterate in an order tied to Python's per-process string-hash seed,
    so counting straight from each set makes count-tie resolution (and
    therefore which 3 tokens make the label) vary run to run on identical
    input. Sorting each set before counting fixes that.
    """
    counter: Counter = Counter()
    for i in idxs:
        counter.update(sorted(token_sets[i]))
    common_toks = [t for t, _ in counter.most_common(3)]
    return " / ".join(common_toks) if common_toks else "misc"


def cluster_titles(titles: list[str], anchor_tokens: list[str] | None = None,
                    generic_tokens: list[str] | None = None,
                    max_df: float = DEFAULT_CLUSTER_MAX_DF) -> list[dict]:
    """Local, deterministic title clustering (no external model call).

    Returns one entry per cluster: {"label": str, "indices": [int, ...]},
    indices referring back into the input `titles` list, sorted by cluster
    size descending. This is the same fallback method the source engine
    uses whenever its optional Gemini-assisted path is unavailable.
    """
    keep = set(anchor_tokens or [])
    generic = set(generic_tokens) if generic_tokens is not None else DEFAULT_GENERIC_TOKENS
    token_sets = _filtered_token_sets(titles, keep, generic, max_df)
    groups = _local_clusters(token_sets)
    out = [
        {"label": _cluster_label(token_sets, idxs), "indices": sorted(idxs)}
        for idxs in groups
    ]
    out.sort(key=lambda c: len(c["indices"]), reverse=True)
    return out


def _apply_convergence(videos: list[dict], anchor_tokens: list[str],
                        generic_tokens: list[str] | None, max_df: float,
                        window_hours: float, min_cluster_size: int) -> None:
    """Cluster the non-self candidates and flag CONVERGENCE (3+ competitors
    on the same subtopic within `window_hours`). Mutates in place."""
    for v in videos:
        v.setdefault("cluster_id", None)
        v.setdefault("cluster_label", None)
        v.setdefault("convergence_size", 0)
        v.setdefault("flags", [])
    comp = [v for v in videos if v.get("relation") != "self"]
    if not comp:
        return
    titles = [v.get("title", "") for v in comp]
    clusters = cluster_titles(titles, anchor_tokens, generic_tokens, max_df)
    for gi, cluster in enumerate(clusters):
        idxs = cluster["indices"]
        recent = [i for i in idxs
                  if comp[i].get("hours_since") is not None
                  and comp[i]["hours_since"] <= window_hours]
        channels = {comp[i].get("channel_id") for i in recent}
        conv = len(channels)
        for i in idxs:
            comp[i]["cluster_id"] = gi
            comp[i]["cluster_label"] = cluster["label"]
            comp[i]["convergence_size"] = conv
            if conv >= min_cluster_size and "CONVERGENCE" not in comp[i]["flags"]:
                comp[i]["flags"].append("CONVERGENCE")


def score_candidates(candidates: list[dict], config: dict | None = None,
                      baselines: dict[str, float] | None = None) -> dict:
    """Score a batch of video candidates 0-100 on the blended Opportunity
    Score. Each candidate needs at minimum: title, vph, comments-derived
    comment_vph, like_vph, eng_rate, channel_id, relation
    ("direct" | "indirect" | "self"), and optionally hours_since,
    demand_gap_score (0 if not computed separately, see demand.py) and
    flags (list, created if absent).

    Returns {"candidates": [...scored, sorted...], "meta": {...}}. Does not
    mutate the input list's dicts by reference contract, but the returned
    candidates ARE the same dict objects, annotated further, to avoid a
    needless deep copy of caller-owned data.
    """
    cfg = config or {}
    weights = {**DEFAULT_WEIGHTS, **cfg.get("weights", {})}
    relation_weights = {**DEFAULT_RELATION_WEIGHTS, **cfg.get("relation_weights", {})}
    ew = {**DEFAULT_ENGAGEMENT_SUBWEIGHTS, **cfg.get("engagement_subweights", {})}
    flags_cfg = {**DEFAULT_FLAGS_CFG, **cfg.get("flags", {})}
    conv_cfg = cfg.get("convergence", {})
    window_hours = conv_cfg.get("window_hours", DEFAULT_CONVERGENCE_WINDOW_HOURS)
    min_cluster_size = conv_cfg.get("min_cluster_size", DEFAULT_CONVERGENCE_MIN_CLUSTER_SIZE)
    max_df = conv_cfg.get("cluster_max_df", DEFAULT_CLUSTER_MAX_DF)
    anchor_tokens = cfg.get("niche", {}).get("cluster_anchor_tokens", [])
    generic_tokens = cfg.get("generic_tokens")
    outlier_cap = cfg.get("outlier_score_cap", DEFAULT_OUTLIER_CAP)
    conv_cap = min_cluster_size + 2

    videos = list(candidates)
    for v in videos:
        v.setdefault("flags", [])
        v.setdefault("relation", "direct")

    if videos:
        vph_z = zscores([v.get("vph", 0.0) for v in videos])
        cvph_z = zscores([v.get("comment_vph", 0.0) for v in videos])
        lvph_z = zscores([v.get("like_vph", 0.0) for v in videos])
        er_z = zscores([v.get("eng_rate", 0.0) for v in videos])
        for i, v in enumerate(videos):
            v["vph_z"] = round(vph_z[i], 2)
            v["comment_vph_z"] = round(cvph_z[i], 2)
            v["like_vph_z"] = round(lvph_z[i], 2)
            v["eng_rate_z"] = round(er_z[i], 2)
            v["engagement_velocity"] = round(
                ew["comment_vph_z"] * cvph_z[i] + ew["like_vph_z"] * lvph_z[i]
                + ew["eng_rate_z"] * er_z[i], 3)

    compute_outliers(videos, baselines)
    _apply_convergence(videos, anchor_tokens, generic_tokens, max_df,
                        window_hours, min_cluster_size)

    for v in videos:
        if (v.get("outlier") or 0) >= flags_cfg["breakout_outlier_min"] and "BREAKOUT" not in v["flags"]:
            v["flags"].append("BREAKOUT")
        if (v.get("comment_vph_z") or 0) >= flags_cfg["hot_engagement_z_min"] and "HOT_ENGAGEMENT" not in v["flags"]:
            v["flags"].append("HOT_ENGAGEMENT")

    sorted_vph = sorted(v.get("vph", 0.0) for v in videos)
    for v in videos:
        c_vph = pct_rank(v.get("vph", 0.0), sorted_vph)
        c_out = clamp((v.get("outlier") or 0) / outlier_cap * 100, 0, 100)
        c_eng = clamp(50 + 20 * (v.get("engagement_velocity") or 0), 0, 100)
        c_dem = v.get("demand_gap_score") or 0
        c_conv = clamp((v.get("convergence_size") or 0) / conv_cap * 100, 0, 100)
        c_cal = (v.get("calendar_boost") or 0) * 100
        raw = (weights["vph"] * c_vph + weights["outlier"] * c_out
               + weights["engagement"] * c_eng + weights["demand_gap"] * c_dem
               + weights["convergence"] * c_conv + weights["calendar"] * c_cal) / 100.0
        v["opportunity_raw"] = round(raw, 1)
        v["opportunity_score"] = round(raw * relation_weights.get(v["relation"], 1.0), 1)
        v["score_components"] = {
            "vph": round(c_vph, 1), "outlier": round(c_out, 1),
            "engagement": round(c_eng, 1), "demand_gap": round(c_dem, 1),
            "convergence": round(c_conv, 1), "calendar": round(c_cal, 1),
        }

    videos.sort(key=lambda v: v["opportunity_score"], reverse=True)
    return {
        "candidates": videos,
        "meta": {"n_candidates": len(videos), "cluster_method": "local"},
    }
