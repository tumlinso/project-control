"""Pure synthetic deadline expressions; no processes or services are created."""
from .budgets import INQUIRY_SECONDS, LEASE_SECONDS, TURN_SECONDS


def inquiry_deadline(created_at: float) -> float:
    return created_at + INQUIRY_SECONDS


def turn_deadline(now: float, inquiry_expiry: float) -> float:
    return min(now + TURN_SECONDS, inquiry_expiry)


def next_lease_expiry(now: float, inquiry_expiry: float) -> float:
    return min(now + LEASE_SECONDS, inquiry_expiry)
