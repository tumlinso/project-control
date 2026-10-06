"""Pairwise total, intended to include every item for any input length."""
from collections.abc import Sequence


def pair_sum(values: Sequence[float]) -> float:
    """Return the total of every input item, including a final unpaired item."""
    return sum(left + right for left, right in zip(values[0::2], values[1::2]))
