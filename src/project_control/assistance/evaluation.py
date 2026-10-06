"""Small, source-mode harness over Project Control's real durable job path.

This harness is deliberately CPU-only. It injects scripted worker results into
``JobService`` and uses private temporary SQLite stores; it never opens the
configured observer backend or a production service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping

from ..as1_contracts import SourceLocator
from ..as1_jobs import JobService
from ..as1_packets import SQLitePacketStore


DEFAULT_SCOPE = {"principal": "pa1-evaluation", "profile": "source-evaluation", "project": "fixture"}
_LAYERS = {"cpu", "trace", "warm_model", "serving", "release"}


class FakeClock:
    """Thread-safe, explicitly advanced wall clock for deterministic trials."""

    def __init__(self, value: float = 1_800_000_000.0):
        self._value = float(value)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("fake clock cannot move backwards")
        with self._lock:
            self._value += float(seconds)
            return self._value


@dataclass(frozen=True)
class ScriptedStep:
    """One visible scripted event; this has no hidden-reasoning field by design."""

    observation: Mapping[str, Any] | None = None
    result: Mapping[str, Any] | None = None
    delay_seconds: float = 0.0


@dataclass
class EvaluationResult:
    job_id: str
    status: str
    answer: str | None
    findings: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    visible_trace: list[dict[str, Any]]
    job_database: str
    packet_database: str
    source_modules: dict[str, dict[str, str]]
    deadline_epoch: float
    retry_verified: bool = False
    production_state_used: bool = False
    backend_used: bool = False
    inference_used: bool = False

    def model_dump(self) -> dict[str, Any]:
        """Return the public trace/result only; no hidden reasoning is represented."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "answer": self.answer,
            "findings": self.findings,
            "observations": self.observations,
            "visible_trace": self.visible_trace,
            "job_database": self.job_database,
            "packet_database": self.packet_database,
            "source_modules": self.source_modules,
            "deadline_epoch": self.deadline_epoch,
            "retry_verified": self.retry_verified,
            "production_state_used": self.production_state_used,
            "backend_used": self.backend_used,
            "inference_used": self.inference_used,
        }


class ScriptedWorkerFactory:
    """Inject deterministic responses at JobService's existing worker seam."""

    skills: dict[str, Any] = {}

    def __init__(self, steps: Iterable[ScriptedStep], *, clock: FakeClock):
        self.steps = tuple(steps)
        if not self.steps or not any(step.result is not None for step in self.steps):
            raise ValueError("script requires at least one terminal result")
        self.clock = clock
        self.visible_trace: list[dict[str, Any]] = []

    def __call__(self, service: JobService, job: Any):
        factory = self

        class Worker:
            def run(self, request: Mapping[str, Any]) -> dict[str, Any]:
                # Only the public request and accepted broker observations are
                # retained. Script authors cannot attach or replay chain-of-thought.
                factory.visible_trace.append({
                    "event": "worker_request",
                    "question": request["question"],
                    "attempt": request["attempt"],
                    "observation_packet_ids": [
                        item.get("packet_id") for item in request.get("observations", [])
                        if isinstance(item, dict) and isinstance(item.get("packet_id"), str)
                    ],
                })
                for step in factory.steps:
                    if step.delay_seconds:
                        factory.clock.advance(step.delay_seconds)
                    if step.observation is not None:
                        ident = service.observe(job.job_id, job.attempt, dict(step.observation), tool="source")
                        factory.visible_trace.append({
                            "event": "observation_accepted",
                            "packet_id": ident,
                            "source_paths": [
                                source.path for source in service.packets.lookup(ident, access_scope=job.scope).packet.sources
                            ],
                        })
                    if step.result is not None:
                        result = dict(step.result)
                        factory.visible_trace.append({
                            "event": "worker_result",
                            "status": result.get("status"),
                            "reason": result.get("reason"),
                        })
                        return result
                raise AssertionError("script did not produce a result")

        return Worker()


def _source_identity(source_root: Path) -> dict[str, dict[str, str]]:
    modules: dict[str, dict[str, str]] = {}
    for name in ("as1_jobs", "as1_packets", "as1_contracts"):
        module = importlib.import_module(f"project_control.{name}")
        path = Path(module.__file__).resolve()
        expected = (source_root / "src/project_control" / f"{name}.py").resolve()
        if path != expected:
            raise RuntimeError(f"source import mismatch for {name}: {path}")
        modules[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return modules


def source_observation(source_root: str | Path, relative_path: str, *, project: str = "fixture",
                       repository: str = "fixture") -> dict[str, Any]:
    """Read one confined fixture file and return a packet-ready source observation."""
    root = Path(source_root).resolve(strict=True)
    candidate = (root / relative_path).resolve(strict=True)
    if not candidate.is_file() or not candidate.is_relative_to(root):
        raise ValueError("source path must name a file inside the evaluation fixture")
    content = candidate.read_bytes()
    text = content.decode("utf-8")
    locator = SourceLocator(
        project=project,
        repository=repository,
        path=candidate.relative_to(root).as_posix(),
        content_sha256=hashlib.sha256(content).hexdigest(),
        revision="fixture-snapshot",
    )
    return {"text": text, "sources": [locator.model_dump()]}


def run_source_evaluation(directory: str | Path, *, question: str, steps: Iterable[ScriptedStep],
                          scope: Mapping[str, Any] = DEFAULT_SCOPE,
                          clock: FakeClock | None = None, source_root: str | Path | None = None,
                          timeout_seconds: float = 5.0, poll_interval: float = 0.005) -> EvaluationResult:
    """Run the real JobService dispatcher against isolated SQLite and scripted turns.

    ``directory`` must live below the OS temporary directory. The service is
    started and stopped within this call. No configured backend is supplied.
    """
    work = Path(directory).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not work.is_relative_to(temp_root) or work == temp_root:
        raise ValueError("evaluation state must be beneath the OS temporary directory")
    work.mkdir(parents=True, exist_ok=True)
    source = Path(source_root).resolve() if source_root else Path(__file__).resolve().parents[3]
    identities = _source_identity(source)
    fake_clock = clock or FakeClock()
    factory = ScriptedWorkerFactory(steps, clock=fake_clock)
    packets = SQLitePacketStore(work / "packets", namespace="pa1-source-evaluation", clock=fake_clock)
    service = JobService(work / "jobs", packets=packets, worker_factory=factory,
                         backend=None, clock=fake_clock, lease_seconds=max(120.0, timeout_seconds + 1))
    try:
        service.start()
        accepted = service.submit(question=question, access_scope=dict(scope), request_id="source-evaluation")
        if not accepted.get("accepted"):
            raise RuntimeError(f"source evaluation was not accepted: {accepted.get('reason')}")
        job_id = accepted["job_id"]
        original = service.lookup(job_id, access_scope=dict(scope))["job"]
        retry = service.submit(question=question, access_scope=dict(scope), request_id="source-evaluation")
        retried = service.lookup(job_id, access_scope=dict(scope))["job"]
        retry_verified = (
            retry.get("accepted") is True
            and retry.get("retry") is True
            and retry.get("job_id") == job_id
            and retried["deadline_epoch"] == original["deadline_epoch"]
        )
        if not retry_verified:
            raise AssertionError("identical retry changed job identity or deadline")
        deadline = time.monotonic() + timeout_seconds
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = service.lookup(job_id, access_scope=dict(scope))
            if snapshot.get("status") != "ok":
                raise RuntimeError(f"isolated evaluation lookup failed: {snapshot.get('status')}")
            if snapshot["job"]["status"] in {"completed", "partial", "failed", "cancelled"}:
                break
            time.sleep(poll_interval)
        else:
            raise TimeoutError("scripted source evaluation exceeded its bounded wait")
        job = snapshot["job"]
        return EvaluationResult(
            job_id=job_id,
            status=job["status"],
            answer=job.get("answer"),
            findings=job.get("findings", []),
            observations=snapshot.get("observations", []),
            visible_trace=list(factory.visible_trace),
            job_database=str(service.path),
            packet_database=str(packets.path),
            source_modules=identities,
            deadline_epoch=job["deadline_epoch"],
            retry_verified=retry_verified,
        )
    finally:
        if service._thread is not None:
            service.shutdown(timeout=max(1.0, timeout_seconds))


def classify_failure(layer: str, reason: str) -> dict[str, str]:
    """Keep failure attribution explicit and separate from model-quality scores."""
    if layer not in _LAYERS:
        raise ValueError(f"unknown evaluation layer: {layer}")
    if not reason or not isinstance(reason, str):
        raise ValueError("failure reason must be nonempty text")
    return {"layer": layer, "reason": reason[:500]}


@dataclass
class ExperimentLedger:
    """Finite in-memory ledger with policy stop rules for each candidate branch."""

    max_entries: int = 12
    max_cases: int = 12
    max_wall_seconds: float = 900.0
    entries: list[dict[str, Any]] = field(default_factory=list)
    _started_at: float = field(default_factory=time.monotonic, repr=False)
    _cases: set[str] = field(default_factory=set, repr=False)
    _branch_scores: dict[str, tuple[float, int]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.max_entries < 1 or self.max_cases < 1 or self.max_wall_seconds <= 0:
            raise ValueError("experiment limits must be positive")

    def check_admission(self, case_count: int | None = None, branch_id: str = "default", *,
                        case_ids: Iterable[str] | None = None, entries: int = 1) -> None:
        """Preflight budget before dispatch; this method does not reserve capacity.

        Callers must record every dispatched trial, including failures, before
        asking for another admission. This keeps the ledger single-owner and
        makes a failed candidate call consume the same declared budget.
        """
        if entries < 1:
            raise ValueError("entries must be positive")
        if case_count is not None and (not isinstance(case_count, int) or case_count < 1):
            raise ValueError("case_count must be a positive integer")
        if not branch_id:
            raise ValueError("branch_id is required")
        if len(self.entries) + entries > self.max_entries:
            raise RuntimeError("experiment_entry_budget_exhausted")
        if time.monotonic() - self._started_at > self.max_wall_seconds:
            raise RuntimeError("experiment_wall_budget_exhausted")
        if case_ids is not None:
            proposed = list(case_ids)
            if not proposed or any(not isinstance(case, str) or not case for case in proposed):
                raise ValueError("case_ids must contain nonempty strings")
            total_cases = len(self._cases | set(proposed))
        elif case_count is not None:
            total_cases = len(self._cases) + case_count
        else:
            raise ValueError("provide case_count or case_ids")
        if total_cases > self.max_cases:
            raise RuntimeError("experiment_case_budget_exhausted")
        prior = self._branch_scores.get(branch_id)
        if prior is not None and prior[1] >= 2:
            raise RuntimeError("experiment_branch_stopped_after_two_non_improvements")

    def record(self, *, case_id: str, configuration_id: str, outcome: str,
               elapsed_seconds: float, score: float | None = None,
               branch_id: str | None = None,
               failure: Mapping[str, str] | None = None) -> dict[str, Any]:
        if not case_id or not configuration_id or not outcome:
            raise ValueError("case, configuration and outcome are required")
        if elapsed_seconds < 0:
            raise ValueError("elapsed time cannot be negative")
        if score is not None and not isinstance(score, (int, float)):
            raise ValueError("score must be numeric")
        branch = branch_id or configuration_id
        self.check_admission(case_ids=[case_id], branch_id=branch)
        new_cases = self._cases | {case_id}
        previous = self._branch_scores.get(branch)
        streak = 0
        best = float(score) if score is not None else None
        if score is not None and previous is not None:
            previous_best, previous_streak = previous
            best = max(previous_best, float(score))
            streak = previous_streak + 1 if float(score) <= previous_best else 0
        entry = {
            "case_id": case_id,
            "configuration_id": configuration_id,
            "branch_id": branch,
            "outcome": outcome,
            "elapsed_seconds": float(elapsed_seconds),
            "score": float(score) if score is not None else None,
            "consecutive_non_improvements": streak,
            "failure": dict(failure) if failure is not None else None,
        }
        self.entries.append(entry)
        self._cases = new_cases
        if score is not None:
            self._branch_scores[branch] = (best, streak)
        return dict(entry)
