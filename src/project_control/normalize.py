from __future__ import annotations

import json
from typing import Any

from .security import redact


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
    clean = redact(value)
    encoded = lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), default=str).encode()
    if len(encoded(clean)) <= budget_bytes:
        return clean
    clean = dict(clean)
    clean["truncation"] = {"truncated": True, "budget_bytes": budget_bytes}
    for key in sorted(clean):
        item = clean[key]
        if isinstance(item, list) and len(item) > 3:
            clean[key] = item[: max(1, len(item) // 2)]
    while len(encoded(clean)) > budget_bytes:
        candidates = [(key, item) for key, item in clean.items() if isinstance(item, list) and len(item) > 1]
        if candidates:
            key, item = max(candidates, key=lambda pair: len(encoded(pair[1])))
            clean[key] = item[:-1]
            continue
        strings = [(key, item) for key, item in clean.items() if isinstance(item, str) and len(item) > 64]
        if strings:
            key, item = max(strings, key=lambda pair: len(pair[1]))
            clean[key] = item[: max(64, len(item) // 2)] + "…"
            continue
        break
    return clean
