import json
import sqlite3

from project_evidence import EvidenceStore

from project_control.legacy_import import import_legacy_evidence


def test_legacy_import_reads_a_copy_and_preserves_advisory_attribution(tmp_path):
    copied_db = tmp_path / "copied-packets.sqlite3"
    with sqlite3.connect(copied_db) as db:
        db.executescript("""
            CREATE TABLE bodies(hash TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE packets(id TEXT PRIMARY KEY, alias TEXT UNIQUE NOT NULL,
                metadata TEXT NOT NULL, hash TEXT NOT NULL, expired INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE knowledge_notes(note_id TEXT PRIMARY KEY, project TEXT NOT NULL,
                metadata TEXT NOT NULL, access_scope TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
        """)
        payload = '{"answer":"recorded packet"}'
        db.execute("INSERT INTO bodies VALUES (?, ?)", ("body-1", payload))
        db.execute("INSERT INTO packets VALUES (?, ?, ?, ?, ?)",
            ("packet-1", "prior-answer", json.dumps({"access_scope": {"project": "demo"}}), "body-1", 1))
        db.execute("INSERT INTO knowledge_notes VALUES (?, ?, ?, ?, ?, ?)",
            ("note-1", "demo", json.dumps({"text": "advisory note"}), "{}", 1.0, 1.0))
    before = copied_db.read_bytes()

    result = import_legacy_evidence(copied_db, tmp_path / "evidence.sqlite3",
        tmp_path / "artifacts")
    repeated = import_legacy_evidence(copied_db, tmp_path / "evidence.sqlite3",
        tmp_path / "artifacts")

    store = EvidenceStore(tmp_path / "evidence.sqlite3", tmp_path / "artifacts")
    packet = store.get_observation("legacy-packet:packet-1")
    note = store.get_observation("legacy-note:note-1")
    assert result["packets_imported"] == 1 and result["notes_imported"] == 1
    assert repeated["already_present"] == 2
    assert copied_db.read_bytes() == before
    packet_payload = packet["payload"] if isinstance(packet["payload"], dict) else json.loads(packet["payload"])
    note_payload = note["payload"] if isinstance(note["payload"], dict) else json.loads(note["payload"])
    assert packet_payload["expired"] is True
    assert packet_payload["independent_evidence"] is False
    assert note_payload["advisory"] is True
    assert note_payload["independent_evidence"] is False
