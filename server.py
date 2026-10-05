"""youtube-intel-mcp: an MCP server packaging the competitor-intelligence
scoring engine from svx2027/yt-competitor-swipe as stateless tools any MCP
client (Claude, another agent, a script) can call directly.

Every tool below is a thin wrapper over the pure functions in src/ - no
network call, no file read, no API key. You bring the candidate data (from
wherever you fetched it) and get back the same data annotated with scores,
demand-gap flags, and topic tags. That is a deliberate scope cut from the
full pipeline this engine is drawn from, which also fetches from the YouTube
Data API, calls vidIQ and Gemini, and writes a committed report + ledger.
See README.md "Honest scope" for the full list of what this scaffold does
not do (yet).

Run it over stdio (the default, for a desktop MCP client):
    pip install -r requirements.txt
    python3 server.py

Run it over Streamable HTTP (for a remote/web MCP client):
    python3 server.py --transport streamable-http --port 8000
    # client connects to http://127.0.0.1:8000/mcp
"""
from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from src import demand, scoring, taxonomy

mcp = FastMCP(
    "youtube-intel-mcp",
    instructions=(
        "Tools for scoring and tagging a batch of YouTube video/post "
        "candidates you have already fetched. Call score_candidates with "
        "your videos and a config (or no config for sane defaults) to get "
        "back a ranked Opportunity Score per video. Call compute_demand_gap "
        "first if you want DEMAND_GAP flags folded into that score. Call "
        "tag_topics to attach rule-based topic labels. Call cluster_titles "
        "on its own if you just want subtopic grouping, no scoring."
    ),
)


@mcp.tool()
def score_candidates(candidates: list[dict[str, Any]],
                      config: dict[str, Any] | None = None,
                      baselines: dict[str, float] | None = None) -> dict[str, Any]:
    """Score a batch of video candidates on the blended 0-100 Opportunity
    Score (views-per-hour percentile, outlier multiple, engagement-velocity
    z-scores, demand gap, title-cluster convergence; the calendar-tailwind
    component is always 0 in this scaffold, see README).

    Each candidate should have at least: title (str), vph (float,
    views-per-hour), comment_vph (float), like_vph (float), eng_rate
    (float), channel_id (str), relation ("direct" | "indirect" | "self").
    Optional: hours_since (float), demand_gap_score (float, 0-100 - run
    compute_demand_gap first if you want this filled in honestly).

    `config` overrides weights/relation_weights/engagement_subweights/flags/
    convergence/niche.cluster_anchor_tokens; any key you omit falls back to
    this engine's own defaults (see src/scoring.py). `baselines` is an
    optional {channel_id: median_vph} map for a fairer outlier multiple than
    the same-batch-median fallback.

    Returns {"candidates": [...sorted by opportunity_score desc...],
    "meta": {"n_candidates": int, "cluster_method": "local"}}.
    """
    return scoring.score_candidates(candidates, config, baselines)


@mcp.tool()
def compute_demand_gap(candidates: list[dict[str, Any]], keywords: list[str],
                        vidiq_keywords: list[dict[str, Any]] | None = None,
                        demand_gap_min_score: float = demand.DEFAULT_DEMAND_GAP_MIN_SCORE,
                        demand_gap_max_competition: float = demand.DEFAULT_DEMAND_GAP_MAX_COMPETITION
                        ) -> dict[str, Any]:
    """Attach a demand-alignment score and DEMAND_GAP flag to each candidate,
    and rank your keyword list by how under-served it is.

    `keywords` is your plain keyword universe (a niche's tracked search
    terms). `vidiq_keywords` is optional richer data in vidIQ's
    keyword_research shape (list of {"keyword", "volume", "competition",
    "overall_score", "est_monthly_searches"}); without it, every keyword
    gets a neutral demand baseline rather than a fabricated one.

    A candidate only gets the DEMAND_GAP flag when its matched keyword's
    overall score clears `demand_gap_min_score` AND that keyword's
    competition is below `demand_gap_max_competition` - alignment alone is
    a ranking input, not a flag, exactly as in the source engine.

    Returns {"candidates": [...annotated, same order as input...],
    "keyword_gaps": [...ranked by gap_score desc...]}. Feed the returned
    candidates straight into score_candidates() to fold demand_gap_score
    into the blended Opportunity Score.
    """
    annotated = demand.annotate_candidates(
        list(candidates), keywords, vidiq_keywords,
        demand_gap_min_score, demand_gap_max_competition,
    )
    gaps = demand.build_demand_section(candidates, keywords, vidiq_keywords)
    return {"candidates": annotated, "keyword_gaps": gaps}


@mcp.tool()
def tag_topics(candidates: list[dict[str, Any]], taxonomy_labels: list[dict[str, Any]],
                session_labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Attach a rule-based topic label to each candidate from a title-hint
    taxonomy: [{"label": str, "hints": [str, ...]}, ...], most specific
    entries first (see config.example.yaml's taxonomy section for the shape).

    Rules run first (longest matching hint wins; ties go to the earlier
    taxonomy entry). Anything the rules leave unlabeled falls back to
    `session_labels` (an optional {candidate_id: label} map you supply from
    an earlier human or model judgment pass), then "" if that has no entry
    either - this tool never guesses a label. A candidate that already has
    a non-empty "topic" is left untouched, matching the source engine's
    "tags are immutable once written" rule.

    Returns the candidates list, each with a "topic" key set.
    """
    return taxonomy.tag_candidates(list(candidates), taxonomy_labels, session_labels)


@mcp.tool()
def cluster_titles(titles: list[str], anchor_tokens: list[str] | None = None,
                    max_df: float = scoring.DEFAULT_CLUSTER_MAX_DF) -> list[dict[str, Any]]:
    """Group a list of video titles into subtopic clusters by their rarest
    shared significant token - no external model call, fully deterministic.

    `anchor_tokens` are domain words to keep as cluster anchors even when
    they are common across the whole corpus (e.g. a subject or product-line
    word that is still a meaningful subtopic). `max_df` (0-1) is the
    corpus-frequency ceiling above which a non-anchor token is dropped
    before clustering, so a channel's own brand name or filler word can't
    connect every title into one mega-cluster.

    Returns clusters sorted largest-first: [{"label": str, "indices": [int,
    ...]}, ...], where each index refers back into the input `titles` list.
    """
    return scoring.cluster_titles(titles, anchor_tokens, max_df=max_df)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport", choices=["stdio", "streamable-http", "sse"], default="stdio",
        help="stdio (default, for a local desktop client) or streamable-http "
             "(for a remote/web client; listens on --host:--port, path /mcp)",
    )
    parser.add_argument("--host", default="127.0.0.1",
                         help="bind host for --transport streamable-http/sse (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                         help="bind port for --transport streamable-http/sse (default 8000)")
    return parser.parse_args()


def _apply_transport_args(server: FastMCP, args: argparse.Namespace) -> None:
    """Push --host/--port onto the server's settings for a network transport.
    Split out from __main__ so the CLI wiring is testable without actually
    binding a socket or blocking on mcp.run().
    """
    if args.transport != "stdio":
        server.settings.host = args.host
        server.settings.port = args.port


if __name__ == "__main__":
    args = _parse_args()
    _apply_transport_args(mcp, args)
    mcp.run(transport=args.transport)
