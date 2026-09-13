"""Project Control's single typed facade over the Todo workflow kernel.

The kernel remains the semantic writer.  This package deliberately adds only
small, versioned contracts needed by Project Control callers; it is not a
second workflow implementation.
"""

from .profiles import WorkProfile
from .retirement import RetirementRequest, retire_run_batch

__all__ = ("RetirementRequest", "WorkProfile", "retire_run_batch")
