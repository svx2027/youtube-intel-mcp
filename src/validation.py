"""Input validation shared by every server.py tool wrapper.

This server accepts arbitrary client-supplied dicts/lists over the MCP
protocol, with no filesystem or network access of its own - so the only
trust boundary is the shape and size of what the client sends. scoring.py,
demand.py, and taxonomy.py all treat a missing field as "default to
something reasonable" (see their own .get() fallbacks), which is correct
engine behavior for an internal batch job, but none of them were ever asked
to defend against a malformed or oversized MCP request: a string where
zscores() expects a float raises deep inside scoring.py with no context
about which candidate caused it, and nothing bounds how large a batch a
client can hand this stateless server in one call.

Every check here raises ValueError, naming the offending field/index so the
caller can fix its request; FastMCP turns that into a clean MCP tool error
(see tests/test_server.py) instead of a raw traceback several frames deep
in the scoring engine, or - for the size caps - instead of silently walking
every O(n) and O(n log n) pass in scoring.py/demand.py on an unbounded list.

Deliberately shallow: shape, type, and size checks only, never a business
rule (a candidate's engine-level correctness is scoring.py/demand.py's
problem, not this layer's).
"""
from __future__ import annotations

from typing import Any

# A generous cap, not a tuned one: large enough for any realistic single
# competitor-scan batch, small enough that a client cannot hand this
# stateless server a multi-million-item list for free.
MAX_ITEMS = 5000

_NUMERIC_CANDIDATE_FIELDS = (
    "vph", "comment_vph", "like_vph", "eng_rate", "hours_since",
    "demand_gap_score", "calendar_boost",
)
_STRING_CANDIDATE_FIELDS = ("title", "channel_id", "video_id", "post_url", "id", "topic", "kind")
_VALID_RELATIONS = {"direct", "indirect", "self"}


def require_list(value: Any, name: str, max_items: int = MAX_ITEMS) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list, got {type(value).__name__}")
    if len(value) > max_items:
        raise ValueError(f"{name} has {len(value)} items, over the {max_items} limit")
    return value


def _require_dicts(items: list, name: str) -> None:
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{name}[{i}] must be an object, got {type(item).__name__}")


def require_optional_dict(value: Any, name: str) -> dict | None:
    if value is not None and not isinstance(value, dict):
        raise ValueError(f"{name} must be an object (or omitted), got {type(value).__name__}")
    return value


def require_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    return value


def validate_string_list(value: Any, name: str, max_items: int = MAX_ITEMS) -> list[str]:
    items = require_list(value, name, max_items)
    for i, item in enumerate(items):
        if not isinstance(item, str):
            raise ValueError(f"{name}[{i}] must be a string, got {type(item).__name__}")
    return items


def validate_candidates(candidates: Any, name: str = "candidates",
                         max_items: int = MAX_ITEMS) -> list[dict]:
    """Shared shape check for every tool that takes a candidates batch.

    Field-level checks only fire for a field a candidate actually HAS -
    every candidate field is optional at this layer (scoring.py/demand.py/
    taxonomy.py each document their own default for an absent field); this
    only rejects a field that is present but the wrong type, since that is
    what actually crashes the engine downstream.
    """
    items = require_list(candidates, name, max_items)
    _require_dicts(items, name)
    for i, c in enumerate(items):
        for field in _NUMERIC_CANDIDATE_FIELDS:
            if field in c and c[field] is not None:
                if isinstance(c[field], bool) or not isinstance(c[field], (int, float)):
                    raise ValueError(
                        f"{name}[{i}].{field} must be a number, got {type(c[field]).__name__}")
        for field in _STRING_CANDIDATE_FIELDS:
            if field in c and c[field] is not None and not isinstance(c[field], str):
                raise ValueError(
                    f"{name}[{i}].{field} must be a string, got {type(c[field]).__name__}")
        if "relation" in c and c["relation"] is not None and c["relation"] not in _VALID_RELATIONS:
            raise ValueError(
                f"{name}[{i}].relation must be one of {sorted(_VALID_RELATIONS)}, "
                f"got {c['relation']!r}")
        if "flags" in c and c["flags"] is not None and not isinstance(c["flags"], list):
            raise ValueError(
                f"{name}[{i}].flags must be a list, got {type(c['flags']).__name__}")
    return items


def validate_vidiq_keywords(value: Any, name: str = "vidiq_keywords",
                             max_items: int = MAX_ITEMS) -> list[dict] | None:
    if value is None:
        return None
    items = require_list(value, name, max_items)
    _require_dicts(items, name)
    return items


def validate_taxonomy(value: Any, name: str = "taxonomy_labels",
                       max_items: int = MAX_ITEMS) -> list[dict]:
    items = require_list(value, name, max_items)
    _require_dicts(items, name)
    for i, entry in enumerate(items):
        label = entry.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(f"{name}[{i}].label must be a non-empty string")
        hints = entry.get("hints", [])
        if not isinstance(hints, list) or not all(isinstance(h, str) for h in hints):
            raise ValueError(f"{name}[{i}].hints must be a list of strings")
    return items


def validate_str_str_map(value: Any, name: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object (or omitted), got {type(value).__name__}")
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(f"{name} must map string ids to string labels")
    return value


def validate_str_number_map(value: Any, name: str) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object (or omitted), got {type(value).__name__}")
    for k, v in value.items():
        if not isinstance(k, str):
            raise ValueError(f"{name} keys must be strings")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{name}[{k!r}] must be a number, got {type(v).__name__}")
    return value
