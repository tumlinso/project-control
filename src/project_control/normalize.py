from __future__ import annotations

import json
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

    while len(encoded(clean)) > budget_bytes:
        lists, strings, dictionaries = collections(clean, root=True)
        list_candidates = [item for item in lists if len(item) > 1]
        if list_candidates:
            item = max(list_candidates, key=lambda candidate: len(encoded(candidate)))
            del item[-max(1, len(item) // 2):]
            continue
        string_candidates = [item for item in strings if len(item[2]) > 64]
        if string_candidates:
            parent, key, item = max(string_candidates, key=lambda candidate: len(candidate[2]))
            parent[key] = item[: max(64, len(item) // 2)] + "…"
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
