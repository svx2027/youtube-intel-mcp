"""Pure demand-supply gap functions, adapted from
svx2027/yt-competitor-swipe's src/keyword_demand.py for MCP use: keyword
metrics and the keyword universe are passed in as plain arguments instead of
being read from a cache file, so a single call is fully self-contained.

`build_demand_section` ranks keywords by how under-served they are (strong
demand, thin or stale competitor supply). `annotate_candidates` attaches a
demand-alignment score to each candidate and flags a genuine gap (strong
query AND low competition; alignment alone does not flag).
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "how",
         "your", "you", "is", "are", "with"}

# Both defaults match the source engine's own hardcoded fallbacks in
# src/keyword_demand.py (config.yaml's own demo only sets the min_score key;
# max_competition's 32 default lives in code there too, not in that file).
DEFAULT_DEMAND_GAP_MIN_SCORE = 55
DEFAULT_DEMAND_GAP_MAX_COMPETITION = 32


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 1}


def _required_overlap(kw_tok: set[str]) -> int:
    """Minimum overlapping tokens to count a keyword as matched. Scales with
    the keyword's own length (floor 2 for anything long enough to have one)
    so a single-word keyword isn't structurally unmatchable, while a longer
    keyword still needs a real chunk of itself present."""
    return min(len(kw_tok), max(2, len(kw_tok) // 2))


def _keyword_metrics_by_lower(vidiq_keywords: list[dict] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for it in (vidiq_keywords or []):
        kw = (it.get("keyword") or it.get("term") or "").strip()
        if kw:
            out[kw.lower()] = it
    return out


def supply_for_keyword(keyword: str, candidates: list[dict]) -> dict:
    """How well-served is this query by the given candidate videos?"""
    kw_tok = _tokens(keyword)
    if not kw_tok:
        return {"matches": 0, "max_vph": 0.0, "newest_hours": None}
    required = _required_overlap(kw_tok)
    matches, max_vph, newest = 0, 0.0, None
    for c in candidates:
        if c.get("kind", "video") != "video":
            continue
        overlap = kw_tok & _tokens(c.get("title", ""))
        if len(overlap) >= required:
            matches += 1
            max_vph = max(max_vph, c.get("vph", 0.0))
            h = c.get("hours_since")
            if h is not None and (newest is None or h < newest):
                newest = h
    return {"matches": matches, "max_vph": round(max_vph, 1), "newest_hours": newest}


def best_keyword_match(title: str, kw_metrics: dict[str, dict],
                        universe: list[str]) -> tuple[str, float]:
    """Return (keyword, demand_alignment 0-100) for a candidate title.

    Alignment is the matched keyword's vidIQ overall score when known, or a
    modest computed value otherwise. This is a ranking input; the genuine
    DEMAND_GAP flag is decided in annotate_candidates using competition too.
    """
    title_tok = _tokens(title)
    if not title_tok:
        return "", 0.0
    best_kw, best_overlap = "", 0
    pool = list(kw_metrics.keys()) + [k.lower() for k in universe]
    for kw in pool:
        kw_tok = _tokens(kw)
        if not kw_tok:
            continue
        ov = len(title_tok & kw_tok)
        if ov >= _required_overlap(kw_tok) and ov > best_overlap:
            best_overlap, best_kw = ov, kw
    if not best_kw:
        return "", 0.0
    m = kw_metrics.get(best_kw)
    if m:
        return best_kw, round(float(m.get("overall_score", 0) or 0), 1)
    return best_kw, round(clamp(30 + 8 * best_overlap, 0, 60), 1)


def build_demand_section(candidates: list[dict], keywords: list[str],
                          vidiq_keywords: list[dict] | None = None) -> list[dict]:
    """Rank keywords by gap_score: strong/rising demand with thin or stale
    supply from `candidates` ranks highest. `vidiq_keywords` is an optional
    list of {"keyword", "volume", "competition", "overall_score",
    "est_monthly_searches"} dicts (matching vidIQ's keyword_research shape);
    without it, every keyword gets a neutral 50.0 demand baseline."""
    kw_metrics = _keyword_metrics_by_lower(vidiq_keywords)
    keys = list(kw_metrics.keys()) if kw_metrics else [k.lower() for k in keywords]
    rows = []
    for kw in keys:
        m = kw_metrics.get(kw, {})
        supply = supply_for_keyword(kw, candidates)
        vol = float(m.get("volume", 0) or 0)
        raw_comp = m.get("competition")
        comp = float(raw_comp) if raw_comp is not None else 0.0
        score = float(m.get("overall_score", 0) or 0)
        demand = score if kw_metrics else 50.0
        newest = supply["newest_hours"]
        fresh = supply["matches"] > 0 and newest is not None and newest < 48
        supply_strength = min(supply["matches"] * 20 + (30 if fresh else 0), 100)
        gap = clamp(demand - 0.5 * supply_strength + (10 if comp < 40 else 0), 0, 100)
        stale = supply["matches"] == 0 or newest is None or newest > 168
        rows.append({
            "keyword": kw,
            "volume": vol, "competition": comp, "overall_score": score,
            "est_monthly_searches": m.get("est_monthly_searches"),
            "supply_matches": supply["matches"],
            "supply_newest_hours": supply["newest_hours"],
            "supply_max_vph": supply["max_vph"],
            "stale_or_thin": stale,
            "gap_score": round(gap, 1),
            "has_vidiq": bool(m),
        })
    rows.sort(key=lambda r: (r["gap_score"], r["overall_score"]), reverse=True)
    return rows


def annotate_candidates(candidates: list[dict], keywords: list[str],
                         vidiq_keywords: list[dict] | None = None,
                         demand_gap_min_score: float = DEFAULT_DEMAND_GAP_MIN_SCORE,
                         demand_gap_max_competition: float = DEFAULT_DEMAND_GAP_MAX_COMPETITION
                         ) -> list[dict]:
    """Attach demand_keyword + demand_gap_score to each candidate, and flag
    DEMAND_GAP only for a genuine gap (matched keyword's overall score at or
    above threshold AND its competition below the max). Returns the same
    list, mutated in place, for convenience chaining into score_candidates."""
    kw_metrics = _keyword_metrics_by_lower(vidiq_keywords)
    for c in candidates:
        c.setdefault("flags", [])
        if c.get("kind", "video") != "video":
            c["demand_gap_score"] = 0.0
            continue
        kw, align = best_keyword_match(c.get("title", ""), kw_metrics, keywords)
        c["demand_keyword"] = kw
        c["demand_gap_score"] = align
        m = kw_metrics.get(kw, {})
        raw_comp = m.get("competition")
        comp = float(raw_comp) if raw_comp is not None else 100.0
        if m and align >= demand_gap_min_score and comp < demand_gap_max_competition \
                and "DEMAND_GAP" not in c["flags"]:
            c["flags"].append("DEMAND_GAP")
    return candidates
