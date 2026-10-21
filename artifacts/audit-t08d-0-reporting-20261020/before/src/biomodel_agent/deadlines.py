"""Monotonic elapsed-time budgets shared across bounded scientific phases."""
from __future__ import annotations

import math
import time


class DeadlineExceeded(TimeoutError):
    pass


def positive_seconds(value, name='timeout_s', maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite positive number')
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f'{name} must be finite') from exc
    if not math.isfinite(value) or value <= 0 or (maximum is not None and value > maximum):
        raise ValueError(f'{name} must be positive and finite' + (f', at most {maximum}' if maximum else ''))
    return value


class Deadline:
    def __init__(self, timeout_s, *, maximum=None):
        self.timeout_s = positive_seconds(timeout_s, maximum=maximum)
        self.started = time.monotonic()

    def elapsed(self):
        return max(0., time.monotonic() - self.started)

    def remaining(self, cap=None):
        remaining = self.timeout_s - self.elapsed()
        if remaining <= 0:
            raise DeadlineExceeded('scientific execution deadline exhausted')
        return min(remaining, positive_seconds(cap)) if cap is not None else remaining
