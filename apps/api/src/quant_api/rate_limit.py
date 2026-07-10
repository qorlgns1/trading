import asyncio
import hashlib
import hmac
import time
from dataclasses import dataclass, field

from redis.asyncio import Redis

from quant_api.settings import Settings


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int, message: str) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def client_key(ip_address: str, secret: str) -> str:
    return hmac.new(secret.encode(), ip_address.encode(), hashlib.sha256).hexdigest()


class RateLimiter:
    async def acquire(self, key: str) -> None:
        raise NotImplementedError

    async def release(self, key: str) -> None:
        raise NotImplementedError


@dataclass
class MemoryRateLimiter(RateLimiter):
    attempts: dict[str, list[float]] = field(default_factory=dict)
    active: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self, key: str) -> None:
        async with self.lock:
            now = time.monotonic()
            recent = [stamp for stamp in self.attempts.get(key, []) if now - stamp < 3600]
            if key in self.active:
                raise RateLimitExceeded(30, "이미 실행 중인 백테스트가 있습니다.")
            if len(recent) >= 5:
                retry = max(1, int(3600 - (now - recent[0])))
                raise RateLimitExceeded(retry, "시간당 백테스트 실행 한도를 초과했습니다.")
            recent.append(now)
            self.attempts[key] = recent
            self.active.add(key)

    async def release(self, key: str) -> None:
        async with self.lock:
            self.active.discard(key)


class RedisRateLimiter(RateLimiter):
    def __init__(self, url: str) -> None:
        self.redis = Redis.from_url(url, decode_responses=True)

    async def acquire(self, key: str) -> None:
        active_key = f"backtest:active:{key}"
        count_key = f"backtest:hour:{key}"
        acquired = await self.redis.set(active_key, "1", ex=900, nx=True)
        if not acquired:
            raise RateLimitExceeded(30, "이미 실행 중인 백테스트가 있습니다.")
        count = await self.redis.incr(count_key)
        if count == 1:
            await self.redis.expire(count_key, 3600)
        if count > 5:
            ttl = max(1, await self.redis.ttl(count_key))
            await self.redis.delete(active_key)
            raise RateLimitExceeded(ttl, "시간당 백테스트 실행 한도를 초과했습니다.")

    async def release(self, key: str) -> None:
        await self.redis.delete(f"backtest:active:{key}")


def create_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.valkey_url:
        return RedisRateLimiter(settings.valkey_url)
    return MemoryRateLimiter()
