"""Source-bound assistance development and evaluation helpers.

The evaluation helpers import :mod:`project_control.as1_jobs`, which itself
loads assistance extensions from this package. Keep these public convenience
exports lazy so importing an extension such as ``assistance.frames`` does not
re-enter ``as1_jobs`` while it is still being initialized.
"""

from importlib import import_module

__all__ = [
    "EvaluationResult",
    "ExperimentLedger",
    "ScriptedStep",
    "classify_failure",
    "run_source_evaluation",
    "source_observation",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    evaluation = import_module(".evaluation", __name__)
    value = getattr(evaluation, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
