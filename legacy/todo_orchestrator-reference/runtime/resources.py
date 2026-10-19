"""Small, stable vocabulary for host-global resource priority.

This module names policy; it is deliberately not a second scheduler.  The host
coordinator remains responsible only for physical ownership and preemption
signals while project task state stays in todo-orchestrator.
"""

from __future__ import annotations

from enum import IntEnum


class ResourcePriority(IntEnum):
    """Fixed CORE4 ordering for competing host resource owners."""

    IDLE_MODEL_RESIDENCY = 20
    BACKGROUND_CUDA = 40
    FOREGROUND_GPU = 60
    ACTIVE_LOCAL_DELEGATION = 80
    CLEAN_CUDA_FOREGROUND = 100


PRIORITY_CLASSES = {
    "idle_model_residency": ResourcePriority.IDLE_MODEL_RESIDENCY,
    "background_cuda": ResourcePriority.BACKGROUND_CUDA,
    "foreground_gpu": ResourcePriority.FOREGROUND_GPU,
    "active_local_delegation": ResourcePriority.ACTIVE_LOCAL_DELEGATION,
    "clean_cuda_foreground": ResourcePriority.CLEAN_CUDA_FOREGROUND,
}


def normalize_priority_class(value: str | ResourcePriority) -> str:
    """Return the stable wire name for a supported priority class."""

    if isinstance(value, ResourcePriority):
        for name, priority in PRIORITY_CLASSES.items():
            if priority == value:
                return name
    if isinstance(value, str) and value in PRIORITY_CLASSES:
        return value
    raise ValueError(f"unknown resource priority class: {value!r}")


def priority_value(value: str | ResourcePriority) -> int:
    return int(PRIORITY_CLASSES[normalize_priority_class(value)])
