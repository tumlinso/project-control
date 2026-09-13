from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .security import redact_output


def stable_unique(items: list[dict[str, Any]], key: str = "id") -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for item in sorted(items, key=lambda value: str(value.get(key, ""))):
        identity = str(item.get(key, json.dumps(item, sort_keys=True, default=str)))
        if identity not in seen:
            seen.add(identity)
            result.append(item)
    return result


def bounded_payload(value: dict[str, Any], budget_bytes: int) -> dict[str, Any]:
    """Deterministically truncate lists/strings until JSON fits the byte budget."""
    clean = redact_output(value)
    encoded = lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), default=str).encode()
    if len(encoded(clean)) <= budget_bytes:
        return clean
    clean = dict(clean)

    def collections(node: Any, *, root: bool = False):
        lists = []
        strings = []
        dictionaries = []
        if isinstance(node, dict):
            if not root:
                dictionaries.append(node)
            for key, item in node.items():
                if key == "truncation":
                    continue
                if isinstance(item, str):
                    strings.append((node, key, item))
                else:
                    child_lists, child_strings, child_dicts = collections(item)
                    lists.extend(child_lists)
                    strings.extend(child_strings)
                    dictionaries.extend(child_dicts)
        elif isinstance(node, list):
            lists.append(node)
            for item in node:
                child_lists, child_strings, child_dicts = collections(item)
                lists.extend(child_lists)
                strings.extend(child_strings)
                dictionaries.extend(child_dicts)
        return lists, strings, dictionaries

    considered = sum(len(item) for item in collections(clean, root=True)[0])
    clean["truncation"] = {
        "truncated": True,
        "budget_bytes": budget_bytes,
        "items_considered": considered,
        "items_returned": considered,
        "historical_items_omitted": int(clean.get("ranking", {}).get("historical_items_omitted", 0)) if isinstance(clean.get("ranking"), dict) else 0,
    }

    iterations = 0
    while len(encoded(clean)) > budget_bytes:
        iterations += 1
        if iterations > 10_000:
            break
        lists, strings, dictionaries = collections(clean, root=True)
        list_candidates = [item for item in lists if len(item) > 1]
        if list_candidates:
            item = max(list_candidates, key=lambda candidate: len(encoded(candidate)))
            del item[-max(1, len(item) // 2):]
            continue
        string_candidates = [item for item in strings if len(item[2]) > 64]
        if string_candidates:
            parent, key, item = max(string_candidates, key=lambda candidate: len(candidate[2]))
            # Always shorten. A 64-character floor used to turn a 65-character
            # string into 64 characters plus an ellipsis forever.
            shortened = max(32, min(len(item) - 2, len(item) // 2))
            parent[key] = item[:shortened] + "…"
            continue
        dict_candidates = [item for item in dictionaries if len(item) > 1 and "truncation" not in item]
        if dict_candidates:
            selected = max(dict_candidates, key=lambda candidate: len(encoded(candidate)))
            removable = [key for key in sorted(selected, reverse=True) if key not in {"id", "type", "status", "relevance"}]
            if removable:
                selected.pop(removable[0])
                continue
        break
    clean["truncation"]["items_returned"] = sum(len(item) for item in collections(clean, root=True)[0])
    return clean


def bounded_envelope(value: Any, budget_bytes: int, *, essential_data_keys: tuple[str, ...] = ()) -> Any:
    """Fit a read result to a UTF-8 canonical-JSON *envelope* budget.

    Individual services used to budget only ``data``.  That made a compact
    answer unexpectedly large when a project had many worktrees or warnings.
    The cursor remains the exact expansion/refresh route; this helper removes
    duplicated detail before trimming the payload, rather than substituting a
    newer snapshot or hiding authority state.
    """
    encoded = lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    result = value.model_copy(deep=True)
    result.data = dict(result.data)
    result.data.setdefault("response_coverage", {
        "budget_bytes": budget_bytes,
        "measurement": "canonical_json_utf8_full_envelope",
        "expansion_cursor": "top_level.cursor",
        "stale_cursor_behavior": "typed_refresh_required",
    })

    # Worktree tables are useful in detailed reads but duplicate the cursor and
    # can dominate an otherwise small overview.  Preserve repository heads and
    # fingerprints, which are the decisive identity/refresh facts.
    for identity in result.project.repositories.values():
        identity.worktrees = {}
    result.cursor.worktrees = {}

    def fits() -> bool:
        return len(encoded(result.model_dump(mode="json"))) <= budget_bytes

    if fits():
        return result
    essential = {
        key: deepcopy(result.data[key]) for key in ("response_coverage", *essential_data_keys)
        if key in result.data
    }
    payload = {key: value for key, value in result.data.items() if key not in essential}
    base = result.model_dump(mode="json")
    base["data"] = {}
    available = max(0, budget_bytes - len(encoded(base)) - len(encoded(essential)) - 32)
    while True:
        result.data = {**bounded_payload(payload, max(0, available)), **essential}
        if fits() or available <= 128:
            break
        available = max(128, available // 2)

    # Warnings are authoritative signals, but repeated verbose provider text
    # is not.  Retain deterministic codes/first messages when the envelope is
    # otherwise unable to fit.
    while not fits() and len(result.warnings) > 1:
        result.warnings.pop()
    while not fits() and result.warnings and len(result.warnings[0]) > 48:
        result.warnings[0] = result.warnings[0][: max(24, len(result.warnings[0]) // 2)] + "…"
    return result
