import pytest

from nexus_mcp.core.errors import RateLimited
from nexus_mcp.core.ratelimit import RateLimiter, TokenBucket


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_bucket_allows_burst_then_blocks_then_refills() -> None:
    clock = Clock()
    b = TokenBucket(60, clock)  # 1 token/second, burst 60
    assert all(b.take() == 0 for _ in range(60))
    wait = b.take()
    assert 0.9 < wait <= 1.0
    clock.t += 1.0
    assert b.take() == 0
    clock.t += 1000  # never exceeds capacity
    assert sum(1 for _ in range(100) if b.take() == 0) == 60


def test_limiter_kinds_are_independent_and_error_has_retry_hint() -> None:
    clock = Clock()
    lim = RateLimiter(2, 1, clock)
    lim.check("read")
    lim.check("read")
    with pytest.raises(RateLimited) as e:
        lim.check("read")
    assert e.value.code.value == "rate_limited" and "Retry in" in e.value.to_client_message()
    lim.check("write")  # separate bucket
    with pytest.raises(RateLimited):
        lim.check("write")


def test_zero_disables_a_limit() -> None:
    lim = RateLimiter(0, 0)
    for _ in range(10_000):
        lim.check("read")
        lim.check("write")
