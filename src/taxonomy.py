"""Pure rule-based topic tagging, adapted from
svx2027/yt-competitor-swipe's src/taxonomy.py: the taxonomy is passed in as
a plain list of {"label": str, "hints": [str, ...]} dicts instead of being
read from a YAML file, so tagging needs no filesystem access.
"""
from __future__ import annotations


def valid_labels(taxonomy: list[dict]) -> set[str]:
    return {e["label"] for e in taxonomy}


def tag_title(title: str, taxonomy: list[dict]) -> str:
    """Label whose LONGEST hint substring-matches the title; "" if none.
    Specificity wins ("pending syllabus" beats "syllabus"); ties resolve by
    taxonomy order (earlier entry wins)."""
    t = f" {(title or '').lower()} "
    best_label, best_len, best_idx = "", 0, len(taxonomy)
    for idx, entry in enumerate(taxonomy):
        for hint in entry.get("hints", []):
            if hint in t and (len(hint) > best_len
                              or (len(hint) == best_len and idx < best_idx)):
                best_label, best_len, best_idx = entry["label"], len(hint), idx
    return best_label


def tag_candidates(candidates: list[dict], taxonomy: list[dict],
                    session_labels: dict[str, str] | None = None) -> list[dict]:
    """Tag a batch of candidates in place: rule-based hints first, then an
    optional caller-supplied `session_labels` map (id -> label, e.g. from an
    earlier human/model judgment pass) for anything the rules leave blank.
    Never overwrites a candidate's existing non-empty `topic`, matching the
    source engine's "tags are immutable once written" ledger rule."""
    session_labels = session_labels or {}
    valid = valid_labels(taxonomy)
    for c in candidates:
        if (c.get("topic") or "").strip():
            continue
        key = c.get("video_id") or c.get("post_url") or c.get("id") or ""
        rule = tag_title(c.get("title", ""), taxonomy)
        if rule:
            c["topic"] = rule
            continue
        label = session_labels.get(key, "")
        c["topic"] = label if label in valid else ""
    return candidates
