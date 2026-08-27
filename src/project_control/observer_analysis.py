"""Optional claimless observer-analysis seam.

No qualifying provider exists in this release.  The default cannot create
claims, children, sessions, GPU work, or repository writes.
"""

from __future__ import annotations

from typing import Any, Protocol


class ObserverAnalysisProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def analyze(self, immutable_packet: dict[str, Any]) -> dict[str, Any]: ...


class DisabledObserverAnalysisProvider:
    available = False

    def analyze(self, immutable_packet: dict[str, Any]) -> dict[str, Any]:
        del immutable_packet
        return {
            "status": "unavailable",
            "reason": "observer_analysis_disabled",
            "mutation_authority": False,
            "transcript": None,
        }
