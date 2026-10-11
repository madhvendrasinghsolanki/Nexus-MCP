"""In-process token-bucket rate limiting (single-tenant server: one global bucket per kind)."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Literal

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import RateLimited

Kind = Literal["read", "write"]


class TokenBucket:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock()
        self._lock = threading.Lock()

    def take(self) -> float:
        """Consume one token. Returns 0 on success, else seconds until one is available."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            return (1.0 - self._tokens) / self.rate


class RateLimiter:
    def __init__(
        self, reads_per_min: int, writes_per_min: int, clock: Callable[[], float] = time.monotonic
    ):
        self._buckets: dict[Kind, TokenBucket | None] = {
            "read": TokenBucket(reads_per_min, clock) if reads_per_min > 0 else None,
            "write": TokenBucket(writes_per_min, clock) if writes_per_min > 0 else None,
        }

    @classmethod
    def from_settings(cls, s: Settings) -> RateLimiter:
        return cls(s.rate_limit_reads_per_min, s.rate_limit_writes_per_min)

    def check(self, kind: Kind) -> None:
        bucket = self._buckets[kind]
        if bucket is None:
            return
        wait = bucket.take()
        if wait > 0:
            raise RateLimited(
                f"Too many {kind} requests.", hint=f"Retry in {math.ceil(wait)} second(s)."
            )
