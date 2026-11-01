"""Pure citation-overlap proxies and R-7 linear percentiles."""

from __future__ import annotations

import math


def _check_k(k: int) -> None:
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")


def precision_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Unique gold overlap in the ranked prefix divided by the requested k.

    Short result lists still use k as the denominator. Duplicate retrieved IDs
    occupy rank positions but cannot earn additional credit.
    """
    _check_k(k)
    return len(set(retrieved_ids[:k]).intersection(gold_ids)) / k


def recall_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Unique gold overlap in the ranked prefix; empty gold returns zero."""
    _check_k(k)
    return len(set(retrieved_ids[:k]).intersection(gold_ids)) / max(len(gold_ids), 1)


def percentile(values: list[float], p: float) -> float | None:
    """R-7 interpolation, sorting a copy without changing the caller's list."""
    if not math.isfinite(p) or not 0 <= p <= 100:
        raise ValueError("p must be finite and between 0 and 100")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Percentile samples must be finite")
    s = sorted(values)
    if not s:
        return None
    if len(s) == 1:
        return s[0]
    h = (len(s) - 1) * (p / 100)
    lo = math.floor(h)
    if lo == len(s) - 1:
        return s[lo]
    return s[lo] + (h - lo) * (s[lo + 1] - s[lo])
