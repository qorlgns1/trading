import os
from urllib.parse import urlsplit

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker
from celery.result import allow_join_result
from quant_api.rate_limit import RateLimitExceeded, RedisRateLimiter
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

pytestmark = pytest.mark.integration

VALKEY_GUARD_KEY = "quant-trend-lab:test-guard"


def _test_valkey_url(database: int) -> str:
    raw_url = os.getenv("TEST_VALKEY_URL")
    if not raw_url:
        raise RuntimeError("Valkey 통합 테스트는 make test-integration으로 실행하세요.")

    parsed = urlsplit(raw_url)
    return parsed._replace(path=f"/{database}").geturl()


def _expected_valkey_guard() -> str:
    guard = os.getenv("TEST_VALKEY_GUARD")
    if not guard:
        raise RuntimeError("통합 테스트 Valkey의 일회용 컨테이너 guard가 없습니다.")
    return guard


def _verify_valkey_guard() -> None:
    client = Redis.from_url(_test_valkey_url(0), decode_responses=True)
    try:
        if client.get(VALKEY_GUARD_KEY) != _expected_valkey_guard():
            raise RuntimeError("일회용 통합 테스트 Valkey가 아닙니다.")
    finally:
        client.close()


async def _verify_valkey_guard_async() -> None:
    client = AsyncRedis.from_url(_test_valkey_url(0), decode_responses=True)
    try:
        if await client.get(VALKEY_GUARD_KEY) != _expected_valkey_guard():
            raise RuntimeError("일회용 통합 테스트 Valkey가 아닙니다.")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_redis_rate_limiter_enforces_active_and_hourly_limits() -> None:
    await _verify_valkey_guard_async()
    limiter = RedisRateLimiter(_test_valkey_url(3))
    key = "valkey-integration-client"
    active_key = f"backtest:active:{key}"
    count_key = f"backtest:hour:{key}"
    await limiter.redis.flushdb()

    try:
        await limiter.acquire(key)
        assert await limiter.redis.get(active_key) == "1"
        assert await limiter.redis.get(count_key) == "1"

        with pytest.raises(RateLimitExceeded, match="이미 실행 중") as active_error:
            await limiter.acquire(key)
        assert active_error.value.retry_after == 30
        assert await limiter.redis.get(count_key) == "1"

        await limiter.release(key)
        assert not await limiter.redis.exists(active_key)

        for _ in range(4):
            await limiter.acquire(key)
            await limiter.release(key)

        with pytest.raises(RateLimitExceeded, match="시간당") as hourly_error:
            await limiter.acquire(key)

        ttl = await limiter.redis.ttl(count_key)
        assert not await limiter.redis.exists(active_key)
        assert await limiter.redis.get(count_key) == "6"
        assert 1 <= ttl <= 3600
        assert 1 <= hourly_error.value.retry_after <= 3600
    finally:
        try:
            await limiter.redis.flushdb()
        finally:
            await limiter.redis.aclose()


def _add(left: int, right: int) -> int:
    return left + right


def test_celery_redis_transport_and_backend_round_trip() -> None:
    _verify_valkey_guard()
    broker_url = _test_valkey_url(1)
    backend_url = _test_valkey_url(2)
    broker_client = Redis.from_url(broker_url, decode_responses=True)
    backend_client = Redis.from_url(backend_url, decode_responses=True)
    app = Celery("valkey-integration", broker=broker_url, backend=backend_url)
    app.conf.update(
        accept_content=["json"],
        broker_connection_retry_on_startup=False,
        result_serializer="json",
        task_serializer="json",
        task_track_started=True,
        worker_prefetch_multiplier=1,
    )
    add_task = app.task(name="integration.add")(_add)
    broker_client.flushdb()
    backend_client.flushdb()

    try:
        with start_worker(
            app,
            concurrency=1,
            pool="solo",
            perform_ping_check=False,
            shutdown_timeout=10.0,
        ), allow_join_result():
            result = add_task.delay(20, 22)
            assert result.get(timeout=10.0) == 42
            assert result.successful()
            assert result.state == "SUCCESS"
    finally:
        try:
            broker_client.flushdb()
            backend_client.flushdb()
        finally:
            broker_client.close()
            backend_client.close()
            app.close()
