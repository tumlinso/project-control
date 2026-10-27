"""AS1 runtime consumers use the source-verified Project Control receiver."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from project_control.as1_jobs import TrustedObserverFactory
from project_control.as1_surface import (
    QUALIFIED_OBSERVER_RUNTIME_SHA256,
    _analysis_runtime_identity,
    _observer_runtime_digest,
)
from project_control.runtime_binding import (
    RuntimeBindingError,
    import_local_worker_supervisor,
    local_runtime_identity,
)
import project_control.runtime_binding as runtime_binding
import project_control.as1_surface as surface_module


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "src/project_control/local_runtime"


@pytest.fixture(autouse=True)
def use_source_candidate_without_production_release_environment(monkeypatch):
    """Exercise the checkout receiver independently of an installed release."""
    monkeypatch.delenv("PROJECT_CONTROL_RELEASE_MANIFEST", raising=False)
    monkeypatch.delenv("PROJECT_CONTROL_RELEASE_DIGEST", raising=False)
    original_runtime_modules = {
        name: module for name, module in tuple(sys.modules.items())
        if name == "local_worker" or name.startswith("local_worker.")
    }
    for name in original_runtime_modules:
        sys.modules.pop(name, None)
    original_meta_path = list(sys.meta_path)
    sys.meta_path[:] = [finder for finder in sys.meta_path
                        if not isinstance(finder, runtime_binding._ManifestFinder)]
    for name in ("_BOUND_FINGERPRINT", "_BOUND_FINDER", "_BOUND_MANIFEST_SHA256"):
        monkeypatch.setattr(runtime_binding, name, None)
    yield
    for name in tuple(sys.modules):
        if name == "local_worker" or name.startswith("local_worker."):
            sys.modules.pop(name, None)
    sys.modules.update(original_runtime_modules)
    sys.meta_path[:] = original_meta_path


class Backend:
    pass


def make_factory(root: Path, digest: str, *, roots=()) -> TrustedObserverFactory:
    return TrustedObserverFactory(root, digest, backend=Backend(), roots=roots,
                                  tools=lambda *args: {}, skills={})


def test_trusted_observer_factory_uses_receiver_manifest_and_observer_digest():
    identity = local_runtime_identity()
    observer = identity.root / "local_worker/observer_runtime.py"
    digest = hashlib.sha256(observer.read_bytes()).hexdigest()
    receiver_manifest = json.loads(
        (identity.root / runtime_binding.RECEIVER_MANIFEST).read_text(encoding="utf-8")
    )

    assert digest == receiver_manifest["files"]["local_worker/observer_runtime.py"]
    assert digest != QUALIFIED_OBSERVER_RUNTIME_SHA256
    factory = make_factory(identity.root, digest)

    assert factory.root == identity.root
    assert factory.path == observer
    assert factory.digest == digest
    assert factory.runtime_identity.manifest_sha256 == identity.manifest_sha256


def test_source_observer_digest_uses_dynamic_inventory_not_physical_manifest(monkeypatch):
    identity = local_runtime_identity()
    expected = runtime_binding._source_receiver_files(identity.root)[
        "local_worker/observer_runtime.py"
    ]

    def reject_physical_manifest(_root):
        pytest.fail("source observer digest must not read the checked-in manifest")

    monkeypatch.setattr(surface_module, "_read_receiver_manifest", reject_physical_manifest)
    assert identity.source_commit == "working-tree"
    assert _observer_runtime_digest(identity) == expected


def test_source_observer_digest_rejects_inventory_change_during_resolution(monkeypatch):
    identity = local_runtime_identity()
    files = runtime_binding._source_receiver_files(identity.root)
    files["local_worker/observer_runtime.py"] = "0" * 64
    monkeypatch.setattr(surface_module, "_source_receiver_files", lambda _root: files)

    with pytest.raises(ValueError, match="receiver changed during observer digest resolution"):
        _observer_runtime_digest(identity)


def test_worker_factory_imports_only_the_canonical_receiver_namespace():
    identity = local_runtime_identity()
    observer = identity.root / "local_worker/observer_runtime.py"
    digest = hashlib.sha256(observer.read_bytes()).hexdigest()
    supervisor = import_local_worker_supervisor(root=identity.root)
    module = sys.modules["local_worker.observer_runtime"]
    assert Path(supervisor.__file__).resolve() == identity.package_root / "supervisor.py"
    factory = make_factory(identity.root, digest, roots=[Path(__file__).resolve().parents[2]])

    service = SimpleNamespace(preparation_read_scope=lambda *_args, **_kwargs: None)
    worker = factory(service, SimpleNamespace(job_id="job", attempt=1, scope={}))

    assert type(worker).__module__ == "local_worker.observer_runtime"
    assert Path(module.__file__).resolve() == observer.resolve()
    assert "pc_trusted_observer_runtime" not in sys.modules
    assert factory.load_runtime_module() is module
    assert type(worker.command) is module.ReadOnlyCommandRunner


def test_trusted_observer_factory_rejects_stale_supplier_pin_and_foreign_root():
    stale_supplier_pin = QUALIFIED_OBSERVER_RUNTIME_SHA256
    with pytest.raises(ValueError, match="observer runtime receipt mismatch"):
        make_factory(RUNTIME_ROOT, stale_supplier_pin)

    with pytest.raises(RuntimeBindingError, match="runtime_root_override_rejected"):
        make_factory(RUNTIME_ROOT.parent, QUALIFIED_OBSERVER_RUNTIME_SHA256)


def test_analysis_cache_epoch_is_bound_to_receiver_identity_and_pin():
    identity = local_runtime_identity()
    observer = identity.root / "local_worker/observer_runtime.py"
    digest = hashlib.sha256(observer.read_bytes()).hexdigest()

    first = _analysis_runtime_identity(identity.root, digest)
    assert first == _analysis_runtime_identity(identity.root, digest)
    assert first != _analysis_runtime_identity(identity.root, digest,
                                               source_verification_state="source_unavailable")
    with pytest.raises(ValueError, match="observer runtime receipt mismatch"):
        _analysis_runtime_identity(identity.root, "0" * 64)
