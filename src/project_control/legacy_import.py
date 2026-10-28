"""Explicit compatibility import for copied v0 evidence databases."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def import_legacy_evidence(source_db: str | Path, evidence_db: str | Path,
                           artifact_dir: str | Path, *, limit: int = 10_000) -> dict[str, Any]:
    """Copy packets and advisory notes into Project Evidence without changing source.

    The LegacyReader opens the source in SQLite read-only/query-only mode. Each
    record retains its old ID and metadata; note imports are explicitly
    advisory and never become assessments or proof.
    """
    from project_evidence import EvidenceStore, LegacyReader, Observation
    if not 1 <= limit <= 100_000:
        raise ValueError("legacy_import_limit_out_of_bounds")
    source = Path(source_db).expanduser().resolve(strict=True)
    if source.resolve() == Path(evidence_db).expanduser().resolve():
        raise ValueError("legacy_source_and_destination_must_differ")
    reader = LegacyReader(source)
    store = EvidenceStore(evidence_db, artifact_dir)
    packets = notes = skipped = 0
    for packet in reader.packets(limit=limit):
        identity = f"legacy-packet:{packet.packet_id}"
        if store.get_observation(identity) is not None:
            skipped += 1
            continue
        payload_bytes = packet.payload.encode("utf-8") if packet.payload is not None else None
        artifacts = (store.put_artifact(payload_bytes, "application/json"),) if payload_bytes is not None else ()
        scope = packet.metadata.get("access_scope", {}) if isinstance(packet.metadata, dict) else {}
        owner = scope.get("project") if isinstance(scope, dict) else None
        owner = owner if isinstance(owner, str) else "legacy:unknown"
        store.record_observation(Observation(identity, owner, "legacy_packet", packet.packet_id,
            artifact_refs=artifacts, payload={"legacy_packet_id": packet.packet_id,
                "alias": packet.alias, "metadata": dict(packet.metadata), "expired": packet.expired,
                "provenance": packet.provenance, "independent_evidence": False}))
        packets += 1
    for note in reader.notes(limit=limit):
        identity = f"legacy-note:{note.note_id}"
        if store.get_observation(identity) is not None:
            skipped += 1
            continue
        store.record_observation(Observation(identity, note.project, "legacy_note", note.note_id,
            payload={"legacy_note_id": note.note_id, "metadata": dict(note.metadata),
                "advisory": True, "independent_evidence": False,
                "provenance": note.provenance}))
        notes += 1
    return {"status": "ok", "source_db": str(source), "packets_imported": packets,
        "notes_imported": notes, "already_present": skipped,
        "limit_per_record_type": limit}
