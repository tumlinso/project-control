"""Executed AS1 contract behavior; these are not downstream product claims."""
import copy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from project_control import as1_contracts as c
from project_control.models import ProposalEnvelope, ToolEnvelope

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "planning/adaptive-surface-v1"
STAMP = "2026-10-04T17:28:22Z"
HASH = "a" * 64
LOC = dict(project="project-control", repository="project-control", path="src/project_control/models.py", content_sha256=HASH)


@pytest.mark.as1_case("CON-01")
def test_role_contract_matches_ledger_and_enforces_roles():
    expected = json.loads((PACKAGE / "contracts/surface.json").read_text())
    assert c.SURFACE == expected
    assert c.ToolEnvelope is ToolEnvelope and c.ProposalEnvelope is ProposalEnvelope
    for role, policy in expected["profiles"].items():
        assert not policy["automatic_overview"]
        assert set(c.SHARED_INFORMATION_TOOLS) <= set(policy["tools"])
        for tool in policy["tools"]:
            c.require_tool(role, tool)
        for hidden in expected["removed_default_names"] + ["delegate_task", "collect_delegation"]:
            with pytest.raises(ValueError):
                c.require_tool(role, hidden)
        if role != "observer":
            for tool, detail in [("read", "compact"), ("skill", "compact"), ("overview", "extended")]:
                with pytest.raises(ValueError):
                    c.require_tool(role, tool, detail)
    c.require_tool("codex", "next_task")
    with pytest.raises(ValueError):
        c.require_tool("observer", "plan")
    assert "WorkflowProtocol" in c.BACKEND_REUSE["workflow"]
    assert "Todo" in c.BACKEND_REUSE["semantic_authority"]


@pytest.mark.as1_case("CON-01")
def test_exact_search_never_falls_through_and_discovery_survives():
    calls = []
    def exact(kind, target):
        calls.append((kind, target))
        return {"resolution": "not_found"}
    def discovery(query):
        calls.append(query)
        return {"matches": [query]}
    assert c.route_search({"kind": "task", "target": "TASK-1"}, exact_lookup=exact, discovery=discovery) == {"resolution": "not_found"}
    assert calls == [("task", "TASK-1")]
    assert c.route_search("discover architecture", exact_lookup=exact, discovery=discovery)["matches"] == ["discover architecture"]
    with pytest.raises(ValidationError):
        c.route_search({"kind": "task", "target": "TASK-1", "query": "fallback"}, exact_lookup=exact, discovery=discovery)


@pytest.mark.as1_case("CON-01", "CON-03")
def test_wire_roundtrips_and_json_schema_compatibility():
    payload = {"finding": "évidence"}
    samples = {
        "locator": (c.SourceLocator, LOC),
        "packet": (c.InformationPacket, dict(packet_id="packet-01", alias="amber-river", created_at=STAMP, tool="search", payload=payload, payload_sha256=c.canonical_digest(payload), sources=[LOC], parents=[], access_scope={"project": "project-control"})),
        "job": (c.DurableJob, dict(job_id="job-0001", mode="skill", question="Select applicable resource", hints=[], status="queued_after_eviction", attempt=2, created_at=STAMP, findings=[{"text": "exact excerpt selected", "evidence_packets": ["packet-01"]}], evidence_packets=["packet-01"], unresolved_questions=["resource freshness"])),
        "skill-selection": (c.SkillSelection, dict(selections=[dict(skill="cuda", resource="cuda/SKILL.md", content_sha256=HASH, line_start=1, line_end=2, reason="required route")], synthesis="secondary guidance")),
        "registration": (c.ProjectAmendment, dict(project="skills", action="record_skill_use", intent="Applied relevant route", expected_revision=888, operation_id="operation-01", payload={"applied": True}, mode="preview")),
        "impact-edge": (c.ImpactEdge, dict(source=dict(project_uuid="pc", repository="pc", kind="task", id="TASK-1"), target=dict(project_uuid="sk", repository="skills", kind="interface", id="INTERFACE-1"), relation="consumes", origin="project_declared", provider="todo", generation="revision-888", input_manifest=[dict(kind="semantic_revision", key="skills", digest=HASH)], resolution="resolved", change_conditions=["interface changes"]))
    }
    for name, (model, args) in samples.items():
        produced = model(**args).model_dump(mode="json", exclude_none=True)
        schema = json.loads((PACKAGE / f"contracts/{name}.schema.json").read_text())
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(produced)
        assert model.model_validate_json(json.dumps(produced)).model_dump(exclude_none=True) == produced
    # Semantic entities have identity, not invented file paths or byte hashes.
    assert "path" not in samples["impact-edge"][0](**samples["impact-edge"][1]).model_dump(exclude_none=True)["source"]
    broken = dict(samples["packet"][1], payload={"tampered": True})
    with pytest.raises(ValidationError, match="hash mismatch"):
        c.InformationPacket(**broken)
    for path in ["../private", "/absolute", "dir/../escape", "C:private", "dir\\escape", "null\0byte"]:
        with pytest.raises(ValidationError):
            c.SourceLocator(**dict(LOC, path=path))


@pytest.mark.as1_case("CON-02")
def test_existing_work_adoption_is_read_only_and_evidence_bound():
    legacy = json.loads((PACKAGE / "planning/legacy-disposition.json").read_text())
    paths = [ROOT / "src/project_control/models.py", ROOT / "src/project_control/workflow_tools.py", ROOT / "planning/pce2/evidence/validation.json"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    verified = {"PC-PCE2-RUNTIME": [c.SourceLocator(**dict(LOC, path="src/project_control/workflow_tools.py", content_sha256=before[str(paths[1])]))]}
    snapshot = copy.deepcopy(legacy["mappings"])
    decisions = c.reconcile_existing_work(mappings=legacy["mappings"], verified_sources=verified)
    assert legacy["mappings"] == snapshot
    assert decisions[0].disposition == "adopt_verified_backend"
    assert all(not row.authority_to_mutate for row in decisions)
    assert all(row.disposition == "needs_current_authority" for row in decisions[1:])
    assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == before
    assert not {"PCU-SK-40", "C4Q-01"} & {row.old_task for row in decisions}
    observed = json.loads((ROOT / "docs/as1-reconciliation-observation.json").read_text())["observations"]
    assert {o["data"]["target"] for o in observed} == {row["old_task"] for row in snapshot}
    assert all(o["status"] == "ok" and o["data"]["matches"][0]["active_claim"] is None for o in observed)


def receipt(uuid="pc-authority"):
    return c.ProducerReceipt(producer_project_uuid=uuid, producer_task_id="PC-AS1-CONTRACT", gate_ids=["PC-AS1-CONTRACT-ACCEPT"], source_identity={"commit": "1" * 40}, contract_hashes={"contracts": HASH}, artifact_locators=[c.SourceLocator(**LOC)], checked_at=STAMP, status="passed")


@pytest.mark.as1_case("CON-03")
def test_receipt_fences_separate_authority_and_stale_proof():
    r = c.ProducerReceipt.model_validate_json(receipt().model_dump_json())
    checks = []
    def current(value):
        checks.append(value.producer_project_uuid)
        return True
    kwargs = dict(producer_project_uuid="pc-authority", producer_task_id="PC-AS1-CONTRACT", contract_hashes={"contracts": HASH})
    r.require_current(**kwargs, verify_authority=current)
    assert checks == ["pc-authority"]
    for override in [{"producer_project_uuid": "skills-authority"}, {"contract_hashes": {"contracts": "b" * 64}}, {"producer_task_id": "OTHER"}]:
        with pytest.raises(ValueError):
            r.require_current(**dict(kwargs, **override), verify_authority=current)
    with pytest.raises(ValueError, match="not current"):
        r.require_current(**kwargs, verify_authority=lambda _: False)
    components = {"project_control": dict(project_uuid="pc-authority", repository="project-control", commit="1" * 40, runtime_identity={"verified": True}, receipt=receipt()), "skills": dict(project_uuid="skills-authority", repository="skills", commit="2" * 40, runtime_identity={"verified": True}, receipt=receipt("skills-authority"))}
    pairing = c.PairedReleaseManifest(**components, checked_at=STAMP)
    assert pairing.project_control.project_uuid != pairing.skills.project_uuid
    with pytest.raises(ValidationError):
        c.PairedReleaseManifest(**dict(components, skills=components["project_control"]), checked_at=STAMP)
    plan = json.loads((PACKAGE / "planning/project-control.todo-plan.json").read_text())
    assert "SK-AS1-" not in json.dumps([task.get("depends_on") for task in plan["tasks"]])


def timestamp_document(kind, value):
    if kind == "packet":
        return c.InformationPacket(packet_id="packet-01", alias="amber-river", created_at=value,
                                   tool="search", payload={}, payload_sha256=c.canonical_digest({}),
                                   sources=[], parents=[], access_scope={})
    if kind == "job":
        return c.DurableJob(job_id="job-0001", mode="investigate", question="Inspect source",
                            hints=[], status="queued", attempt=0, created_at=value,
                            findings=[], evidence_packets=[], unresolved_questions=[])
    return c.ProducerReceipt.model_validate(dict(receipt().model_dump(), checked_at=value))


@pytest.mark.as1_case("CON-01", "CON-03")
@pytest.mark.parametrize("kind", ["packet", "job", "receipt"])
@pytest.mark.parametrize("invalid", [
    "2026-10-04X17:28:22+00:00",
    "2026-10-04T17:28:22+00:00:30",
    "2026-10-04 17:28:22Z",
    "2026-10-04T17:28:22",
    "2026-02-30T17:28:22Z",
    "2026-10-04T25:28:22Z",
    "2026-10-04T17:28:22+00:60",
    "2026-10-04T17:28:22+24:00",
])
def test_timestamp_contract_rejects_non_rfc3339(kind, invalid):
    with pytest.raises(ValidationError):
        timestamp_document(kind, invalid)


@pytest.mark.as1_case("CON-01", "CON-03")
@pytest.mark.parametrize("kind", ["packet", "job", "receipt"])
@pytest.mark.parametrize("valid", [
    "2026-10-04T17:28:22Z", "2026-10-04t17:28:22z",
    "2026-10-04T17:28:22.123+05:30", "2026-10-04T17:28:22-00:00",
])
def test_timestamp_contract_preserves_valid_wire_values(kind, valid):
    produced = timestamp_document(kind, valid).model_dump()
    assert produced["checked_at" if kind == "receipt" else "created_at"] == valid
