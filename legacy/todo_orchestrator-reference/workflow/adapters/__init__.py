"""Lazy, shell-free adapters for specialized workflow execution engines."""

from .ctxpp import CtxppAdapter
from .cuda import CudaAdapter
from .local_worker import LocalWorkerAdapter

__all__ = ["CtxppAdapter", "CudaAdapter", "LocalWorkerAdapter"]
