import pytest
from quant_api.rate_limit import MemoryRateLimiter, RateLimitExceeded, client_key


@pytest.mark.asyncio
async def test_only_one_active_run_is_allowed() -> None:
    limiter = MemoryRateLimiter()
    key = client_key("127.0.0.1", "test-secret")
    await limiter.acquire(key)
    with pytest.raises(RateLimitExceeded, match="실행 중"):
        await limiter.acquire(key)
    await limiter.release(key)
    await limiter.acquire(key)


@pytest.mark.asyncio
async def test_hourly_limit_is_enforced_without_storing_plain_ip() -> None:
    limiter = MemoryRateLimiter()
    key = client_key("203.0.113.7", "test-secret")
    assert "203.0.113.7" not in key
    for _ in range(5):
        await limiter.acquire(key)
        await limiter.release(key)
    with pytest.raises(RateLimitExceeded, match="시간당"):
        await limiter.acquire(key)
