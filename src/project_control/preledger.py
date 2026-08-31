"""Deterministic lowering from Project Control pre-ledgers to native Todo plans.

The compiler is deliberately a frontend only.  It selects one repository
authority, preserves source provenance, and emits fields already understood by
Todo Orchestrator's plan schema.  Validation and transaction semantics remain
owned by Todo Orchestrator.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PRELEDGER_FORMAT = "project-control-preledger"
PRELEDGER_SCHEMA_VERSION = 1
NATIVE_TODO_PLAN_SCHEMA_VERSION = 2
COMPILER_VERSION = 2

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_TASK_KINDS = {
    "epic", "workstream", "task", "integration", "integration_task",
    "validation", "validation_task",
}
_PROVENANCE_FIELDS = (
    "workstream",
    "biological_motivation",
    "compiler_architectural_reason",
    "implementation_mechanism",
    "invariants",
    "forbidden_shortcuts",
    "validation",
    "performance_evidence",
    "completion_condition",
    "experimental",
    "negative_result_acceptable",
)


class PreledgerError(ValueError):
    """A pre-ledger package cannot be compiled without guessing."""


@dataclass(frozen=True)
class CompiledPreledger:
    native_todo_plan: dict[str, Any]
    package_digest: str
    plan_digest: str
    selected_task_count: int
    excluded_task_count: int
    internal_dependency_count: int
    external_dependencies: tuple[dict[str, Any], ...]
    interfaces_imported: int
    warnings: tuple[str, ...]
    source_files: tuple[str, ...]

    def canonical_plan_json(self) -> str:
        """Return the byte-stable representation used for ``plan_digest``."""

        return canonical_json(self.native_todo_plan)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PreledgerError(f"cannot read {label}: {path.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PreledgerError(f"invalid JSON in {label}: {path.name}: {exc}") from exc


def _package_file(root: Path, reference: object, label: str) -> Path:
    if not isinstance(reference, str) or not reference.strip():
        raise PreledgerError(f"{label} must be a non-empty relative file name")
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts:
        raise PreledgerError(f"{label} must stay within the package directory")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PreledgerError(f"{label} must stay within the package directory") from exc
    if not candidate.is_file():
        raise PreledgerError(f"missing {label}: {relative.as_posix()}")
    return candidate


def _records(value: Any, keys: Sequence[str], label: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict):
        records = next((value[key] for key in keys if key in value), None)
    else:
        records = None
    if not isinstance(records, list):
        raise PreledgerError(f"{label} must be an array or contain an array named {keys[0]}")
    if not all(isinstance(item, dict) for item in records):
        raise PreledgerError(f"{label} records must be JSON objects")
    return records


def _validate_manifest(root: Path, path: Path) -> None:
    seen = 0
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if not match:
            raise PreledgerError(f"invalid manifest entry at {path.name}:{line_number}")
        expected, reference = match.groups()
        candidate = _package_file(root, reference.strip(), "manifest entry")
        actual = _digest_bytes(candidate.read_bytes())
        if actual.lower() != expected.lower():
            raise PreledgerError(f"manifest hash mismatch for {reference.strip()}")
        seen += 1
    if not seen:
        raise PreledgerError(f"manifest contains no file hashes: {path.name}")


def _semantic_digest(root: Path, files: Iterable[Path]) -> str:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _digest_bytes(path.read_bytes()),
        }
        for path in sorted(set(files), key=lambda item: item.relative_to(root).as_posix())
    ]
    payload = {"compiler_version": COMPILER_VERSION, "semantic_files": entries}
    return _digest_bytes(canonical_json(payload).encode("utf-8"))


def _nonempty_string(record: Mapping[str, Any], field: str, *, task: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PreledgerError(f"task {task} requires non-empty {field}")
    return value.strip()


def _flatten_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_strings(item))
        return result
    return []


def _scope_paths(value: Any, keys: Sequence[str]) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for key in keys:
            values.extend(_flatten_strings(value.get(key)))
        return values
    return _flatten_strings(value)


def _clean_paths(values: Iterable[str]) -> list[str]:
    cleaned: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = value.strip()
        if normalized.startswith("[proposed] "):
            normalized = normalized[len("[proposed] "):].strip()
        if normalized:
            cleaned.add(normalized)
    return sorted(cleaned)


def _explicit_source_paths(values: Iterable[str]) -> list[str]:
    """Select path-shaped entries from fields that may also name concepts.

    The authoritative JBC package intentionally mixes exact source paths and
    architectural concepts in ``permitted_read_scope``.  Whitespace-free
    repository-relative values are paths in that format; prose remains in the
    provenance notes instead of becoming false Todo ownership scope.
    """

    return _clean_paths(
        value for value in values
        if isinstance(value, str) and value.strip() and not any(character.isspace() for character in value.replace("[proposed] ", "", 1))
    )


def _dependency_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_dependency_ids(item))
        return result
    if isinstance(value, dict):
        for key in ("task_id", "prerequisite_task_id", "checkpoint_id", "id"):
            if key in value:
                return _dependency_ids(value[key])
    return []


def _produced_checkpoints(task: Mapping[str, Any]) -> list[str]:
    value = task.get("produces_checkpoint")
    if value is True or value is False or value is None:
        return []
    return _dependency_ids(value)


def _csv_dependencies(path: Path) -> tuple[list[tuple[str, str]], bool]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise PreledgerError(f"dependency index has no header: {path.name}")
            fields = set(reader.fieldnames)
            pairs = (
                (("task_id", "dependent_task_id", "consumer_task_id", "consumer_task"),
                 ("prerequisite_task_id", "dependency_id", "depends_on", "producer_task")),
                (("to_task_id", "target_task_id", "to_id"),
                 ("from_task_id", "source_task_id", "from_id")),
            )
            columns = next(
                ((next((name for name in left if name in fields), None),
                  next((name for name in right if name in fields), None))
                 for left, right in pairs
                 if any(name in fields for name in left) and any(name in fields for name in right)),
                None,
            )
            if not columns or not all(columns):
                raise PreledgerError(f"unsupported dependency index columns in {path.name}")
            dependent_column, prerequisite_column = columns
            result: list[tuple[str, str]] = []
            for line_number, row in enumerate(reader, 2):
                dependent = (row.get(dependent_column) or "").strip()
                prerequisite = (row.get(prerequisite_column) or "").strip()
                if not dependent or not prerequisite:
                    raise PreledgerError(f"empty dependency at {path.name}:{line_number}")
                result.append((dependent, prerequisite))
            complete_compatibility_index = (
                dependent_column == "consumer_task" and prerequisite_column == "producer_task"
            )
            return result, complete_compatibility_index
    except OSError as exc:
        raise PreledgerError(f"cannot read dependency index: {path.name}: {exc}") from exc


def _csv_external_dependencies(path: Path) -> list[tuple[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            expected = {"checkpoint_interface_or_receipt", "consumer_task"}
            if not reader.fieldnames or not expected.issubset(reader.fieldnames):
                raise PreledgerError(f"unsupported external dependency columns in {path.name}")
            result: list[tuple[str, str]] = []
            for line_number, row in enumerate(reader, 2):
                dependent = (row.get("consumer_task") or "").strip()
                prerequisite = (row.get("checkpoint_interface_or_receipt") or "").strip()
                if not dependent or not prerequisite:
                    raise PreledgerError(f"empty external dependency at {path.name}:{line_number}")
                result.append((dependent, prerequisite))
            return result
    except OSError as exc:
        raise PreledgerError(f"cannot read external dependency index: {path.name}: {exc}") from exc


def _lower_interface(record: Mapping[str, Any], selected_ids: set[str]) -> dict[str, Any] | None:
    required = ("id", "owner_task_id", "state", "version", "contract_paths", "content_hash")
    if any(field not in record or record[field] is None for field in required):
        return None
    if not all(isinstance(record[field], str) and record[field].strip() for field in ("id", "owner_task_id", "state", "content_hash")):
        return None
    if record["owner_task_id"] not in selected_ids:
        return None
    paths = record["contract_paths"]
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path.strip() for path in paths):
        return None
    version = record["version"]
    if not isinstance(version, (str, int)) or isinstance(version, bool):
        return None
    return {
        "id": record["id"].strip(),
        "owner_task_id": record["owner_task_id"].strip(),
        "state": record["state"].strip(),
        "version": str(version),
        "contract_paths": _clean_paths(paths),
        "content_hash": record["content_hash"].strip(),
    }


def compile_preledger(
    package_path: str | Path,
    *,
    target_repository: str,
) -> CompiledPreledger:
    """Compile one repository authority from a pre-ledger directory.

    No Todo state is read or written.  The returned schema-v2 plan is suitable
    for Todo Orchestrator's own validation/diff/application pipeline.
    """

    if not isinstance(target_repository, str) or not target_repository.strip():
        raise PreledgerError("target_repository must be a non-empty string")
    target_repository = target_repository.strip()
    root = Path(package_path).expanduser().resolve()
    if not root.is_dir():
        raise PreledgerError(f"pre-ledger package is not a directory: {root}")

    descriptor_path = root / "preledger.json"
    descriptor: dict[str, Any] | None = None
    source_files: set[Path] = set()
    semantic_files: set[Path] = set()
    if descriptor_path.is_file():
        loaded = _load_json(descriptor_path, "pre-ledger descriptor")
        if not isinstance(loaded, dict):
            raise PreledgerError("preledger.json must be a JSON object")
        if loaded.get("format") != PRELEDGER_FORMAT:
            raise PreledgerError(f"unsupported pre-ledger format: {loaded.get('format')!r}")
        if loaded.get("schema_version") != PRELEDGER_SCHEMA_VERSION:
            raise PreledgerError(f"unsupported pre-ledger schema_version: {loaded.get('schema_version')!r}")
        descriptor = loaded
        source_files.add(descriptor_path)
        semantic_files.add(descriptor_path)
        tasks_path = _package_file(root, loaded.get("tasks"), "tasks file")
        optional = {
            key: (_package_file(root, loaded[key], f"{key} file") if loaded.get(key) else None)
            for key in ("interfaces", "dependency_index", "external_dependencies", "summary", "manifest")
        }
    else:
        tasks_path = _package_file(root, "proposed_todos.json", "tasks file")
        optional = {
            "interfaces": root / "interface_catalog.json" if (root / "interface_catalog.json").is_file() else None,
            "dependency_index": root / "dependency_edges.csv" if (root / "dependency_edges.csv").is_file() else None,
            "external_dependencies": root / "external_dependency_receipts.csv" if (root / "external_dependency_receipts.csv").is_file() else None,
            "summary": root / "plan_summary.json" if (root / "plan_summary.json").is_file() else None,
            "manifest": root / "MANIFEST.sha256" if (root / "MANIFEST.sha256").is_file() else None,
        }

    source_files.add(tasks_path)
    semantic_files.add(tasks_path)
    for key, path in optional.items():
        if path is not None:
            source_files.add(path)
            if key in {"interfaces", "dependency_index", "external_dependencies"}:
                semantic_files.add(path)
    if optional["manifest"] is not None:
        _validate_manifest(root, optional["manifest"])
    package_digest = _semantic_digest(root, semantic_files)

    tasks = _records(_load_json(tasks_path, "tasks file"), ("tasks", "proposed_todos"), "tasks file")
    by_id: dict[str, dict[str, Any]] = {}
    repository_by_id: dict[str, str] = {}
    checkpoint_owner: dict[str, str] = {}
    for index, task in enumerate(tasks):
        raw_id = task.get("id")
        label = str(raw_id) if raw_id is not None else f"at index {index}"
        task_id = _nonempty_string(task, "id", task=label)
        if not _TASK_ID.fullmatch(task_id):
            raise PreledgerError(f"task {task_id!r} has malformed id")
        if task_id in by_id:
            raise PreledgerError(f"duplicate task id: {task_id}")
        _nonempty_string(task, "title", task=task_id)
        repository = _nonempty_string(task, "repository", task=task_id)
        by_id[task_id] = task
        repository_by_id[task_id] = repository
        for checkpoint in _produced_checkpoints(task):
            if checkpoint in checkpoint_owner and checkpoint_owner[checkpoint] != task_id:
                raise PreledgerError(f"duplicate produced checkpoint id: {checkpoint}")
            checkpoint_owner[checkpoint] = task_id

    selected_ids = {task_id for task_id, repository in repository_by_id.items() if repository == target_repository}
    warnings: list[str] = []
    dependency_pairs: dict[tuple[str, str], set[str]] = {}
    for task_id, task in by_id.items():
        for field in ("builds_on", "prerequisite_tasks_or_checkpoints", "prerequisites"):
            for prerequisite in _dependency_ids(task.get(field)):
                dependency_pairs.setdefault((task_id, prerequisite), set()).add(field)
    if optional["dependency_index"] is not None:
        indexed_dependencies, complete_compatibility_index = _csv_dependencies(optional["dependency_index"])
        task_local_dependencies = {
            pair for pair in dependency_pairs
            if pair[0] in by_id and pair[1] in by_id
        }
        if complete_compatibility_index and set(indexed_dependencies) != task_local_dependencies:
            missing = sorted(task_local_dependencies - set(indexed_dependencies))
            extra = sorted(set(indexed_dependencies) - task_local_dependencies)
            raise PreledgerError(
                "dependency index disagrees with task-local prerequisites: "
                f"missing={missing[:8]!r}, extra={extra[:8]!r}"
            )
        for dependent, prerequisite in indexed_dependencies:
            pair = (dependent, prerequisite)
            if pair not in dependency_pairs:
                warnings.append(f"dependency index supplemented task-local dependencies: {dependent} -> {prerequisite}")
            dependency_pairs.setdefault(pair, set()).add("dependency_edges.csv")
    if optional["external_dependencies"] is not None:
        indexed_external = _csv_external_dependencies(optional["external_dependencies"])
        task_local_receipts = {
            pair for pair in dependency_pairs if pair[1].startswith("receipt:")
        }
        if set(indexed_external) != task_local_receipts:
            missing = sorted(task_local_receipts - set(indexed_external))
            extra = sorted(set(indexed_external) - task_local_receipts)
            raise PreledgerError(
                "external dependency index disagrees with task-local receipts: "
                f"missing={missing[:8]!r}, extra={extra[:8]!r}"
            )
        for dependent, prerequisite in indexed_external:
            pair = (dependent, prerequisite)
            if pair not in dependency_pairs:
                warnings.append(f"external dependency index supplemented task-local dependencies: {dependent} -> {prerequisite}")
            dependency_pairs.setdefault(pair, set()).add("external_dependency_receipts.csv")

    native_tasks: list[dict[str, Any]] = []
    external_dependencies: list[dict[str, Any]] = []
    internal_dependency_count = 0
    native_dependencies: dict[str, list[dict[str, str]]] = {task_id: [] for task_id in selected_ids}
    for (dependent, prerequisite), sources in sorted(dependency_pairs.items()):
        if dependent not in by_id:
            raise PreledgerError(f"dependency names unknown consumer task: {dependent}")
        if dependent not in selected_ids:
            continue
        if prerequisite in selected_ids:
            native_dependencies[dependent].append({"type": "task", "task_id": prerequisite})
            internal_dependency_count += 1
        elif prerequisite in checkpoint_owner and checkpoint_owner[prerequisite] in selected_ids:
            native_dependencies[dependent].append({"type": "checkpoint", "checkpoint_id": prerequisite})
            internal_dependency_count += 1
        else:
            owner = checkpoint_owner.get(prerequisite, prerequisite.removeprefix("receipt:"))
            external_dependencies.append({
                "task_id": dependent,
                "dependency_id": prerequisite,
                "dependency_repository": repository_by_id.get(owner),
                "source": sorted(sources),
            })

    for task_id in sorted(selected_ids):
        source = by_id[task_id]
        purpose = source.get("purpose", "")
        if purpose is not None and not isinstance(purpose, str):
            raise PreledgerError(f"task {task_id} purpose must be a string")
        kind = source.get("task_kind", "task")
        if kind not in _TASK_KINDS:
            if kind not in (None, ""):
                warnings.append(f"task {task_id} unsupported task_kind {kind!r} lowered to 'task'")
            kind = "task"
        provenance: dict[str, Any] = {
            "preledger_package_digest": package_digest,
            "original_task_id": task_id,
        }
        for field in _PROVENANCE_FIELDS:
            if field in source and source[field] not in (None, "", [], {}):
                provenance[field] = source[field]
        aliases = {
            "mechanism": "implementation_mechanism",
            "cold_vs_hot_path": "hot_vs_cold_path",
            "complexity_expectations": "complexity_expectation",
            "failure_cases_and_fallback": "failure_cases",
            "expected_inputs": "inputs",
            "expected_outputs": "outputs",
        }
        for source_field, provenance_field in aliases.items():
            if source_field in source and source[source_field] not in (None, "", [], {}):
                provenance[provenance_field] = source[source_field]
        for field in (
            "category", "classification", "subsystem", "suggested_lane", "parallelism",
            "integration_point", "explicit_out_of_scope", "permitted_read_scope",
            "data_flow_and_ownership", "hot_vs_cold_path", "complexity_expectation",
        ):
            if field in source and source[field] not in (None, "", [], {}):
                provenance[field] = source[field]
        native: dict[str, Any] = {
            "id": task_id,
            "kind": kind,
            "title": source["title"].strip(),
            "objective": (purpose or "").strip(),
            "notes": "preledger_provenance=" + json.dumps(provenance, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        }
        exclusive = _clean_paths(_scope_paths(source.get("write_scope"), ("exclusive_paths", "write_paths", "paths")))
        read_paths = _explicit_source_paths(
            _flatten_strings(source.get("existing_source_paths"))
            + _flatten_strings(source.get("source_paths"))
            + _flatten_strings(source.get("permitted_read_scope"))
            + _flatten_strings(source.get("existing_code_extended"))
        )
        if exclusive or read_paths:
            native["scope"] = {}
            if exclusive:
                native["scope"]["exclusive_paths"] = exclusive
            if read_paths:
                native["scope"]["read_paths"] = read_paths
        dependencies = sorted(native_dependencies[task_id], key=lambda item: canonical_json(item))
        if dependencies:
            native["depends_on"] = dependencies
        checkpoints = _produced_checkpoints(source)
        if checkpoints:
            native["checkpoints"] = [{"id": item, "title": item} for item in sorted(set(checkpoints))]
        native_tasks.append(native)

    interfaces: list[dict[str, Any]] = []
    if optional["interfaces"] is not None:
        interface_records = _records(
            _load_json(optional["interfaces"], "interface catalog"),
            ("interfaces", "interface_catalog"),
            "interface catalog",
        )
        for index, record in enumerate(interface_records):
            lowered = _lower_interface(record, selected_ids)
            if lowered is None:
                interface_id = record.get("id", f"index {index}")
                warnings.append(f"unsupported interface record: {interface_id}")
            else:
                interfaces.append(lowered)
        seen_interfaces: set[str] = set()
        duplicate_interfaces: set[str] = set()
        for item in interfaces:
            if item["id"] in seen_interfaces:
                duplicate_interfaces.add(item["id"])
            seen_interfaces.add(item["id"])
        if duplicate_interfaces:
            raise PreledgerError(f"duplicate interface ids: {sorted(duplicate_interfaces)}")

    plan: dict[str, Any] = {
        "schema_version": NATIVE_TODO_PLAN_SCHEMA_VERSION,
        "project": {"name": target_repository},
        "tasks": native_tasks,
        "interfaces": sorted(interfaces, key=lambda item: item["id"]),
    }
    plan_json = canonical_json(plan)
    return CompiledPreledger(
        native_todo_plan=plan,
        package_digest=package_digest,
        plan_digest=_digest_bytes(plan_json.encode("utf-8")),
        selected_task_count=len(selected_ids),
        excluded_task_count=len(tasks) - len(selected_ids),
        internal_dependency_count=internal_dependency_count,
        external_dependencies=tuple(external_dependencies),
        interfaces_imported=len(interfaces),
        warnings=tuple(sorted(set(warnings))),
        source_files=tuple(sorted(path.relative_to(root).as_posix() for path in source_files)),
    )
