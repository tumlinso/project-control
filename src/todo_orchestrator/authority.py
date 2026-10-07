"""Canonical logical fingerprints for optimistic authority operations.

SQLite's serialized image includes page layout and WAL/checkpoint details.  It
is therefore not an authority identity: two unchanged read connections can
produce different bytes.  The workflow uses this canonical logical image when
an operation must detect a semantic change between prepare and apply.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


_DERIVED_TABLES = frozenset({"projection_status"})


def logical_authority_fingerprint(conn: sqlite3.Connection) -> str:
    """Hash stable schema/table/row content, independent of SQLite page layout."""
    tables = [str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ) if str(row[0]) not in _DERIVED_TABLES]
    image: dict[str, Any] = {"format": "todo-logical-authority/1", "tables": {}}
    for table in tables:
        columns = [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]
        # Table names originate in sqlite_master; identifiers are quoted here
        # solely to retain compatibility with migration-created names.
        rows = [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
        image["tables"][table] = {
            "columns": columns,
            "rows": sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)),
        }
    encoded = json.dumps(image, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
