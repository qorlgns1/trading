import asyncio

import httpx
import pytest
from quant_api.toss_market_data import TossApiError, TossMarketDataClient


def _stock_payload() -> dict[str, object]:
    return {
        "result": [
            {
                "symbol": "005930",
                "name": "삼성전자",
                "englishName": "SamsungElec",
                "isinCode": "KR7005930003",
                "market": "KOSPI",
                "securityType": "STOCK",
                "isCommonShare": True,
                "status": "ACTIVE",
                "currency": "KRW",
                "listDate": "1975-06-11",
                "delistDate": None,
                "sharesOutstanding": "5919637922",
                "leverageFactor": None,
                "koreanMarketDetail": {
                    "liquidationTrading": False,
                    "nxtSupported": True,
                    "krxTradingSuspended": False,
                    "nxtTradingSuspended": False,
                },
            },
            {
                "symbol": "AAPL",
                "name": "애플",
                "englishName": "APPLE INC",
                "isinCode": "US0378331005",
                "market": "NASDAQ",
                "securityType": "STOCK",
                "isCommonShare": True,
                "status": "ACTIVE",
                "currency": "USD",
                "listDate": "1980-12-12",
                "delistDate": None,
                "sharesOutstanding": "14702703000",
                "leverageFactor": None,
                "koreanMarketDetail": None,
            },
        ]
    }


def _client(
    handler: httpx.MockTransport,
    *,
    sleep: object = asyncio.sleep,
    clock: object | None = None,
) -> TossMarketDataClient:
    kwargs: dict[str, object] = {
        "base_url": "https://openapi.example.test",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "transport": handler,
        "sleep": sleep,
    }
    if clock is not None:
        kwargs["clock"] = clock
    return TossMarketDataClient(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_token_is_cached_and_concurrent_requests_share_refresh() -> None:
    calls = {"token": 0, "stocks": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            await asyncio.sleep(0)
            return httpx.Response(
                200,
                json={"access_token": "access-token", "token_type": "Bearer", "expires_in": 3600},
            )
        calls["stocks"] += 1
        assert request.headers["Authorization"] == "Bearer access-token"
        return httpx.Response(200, json=_stock_payload())

    client = _client(httpx.MockTransport(handler))
    results = await asyncio.gather(*(client.get_stocks(["005930", "AAPL"]) for _ in range(5)))

    assert calls == {"token": 1, "stocks": 5}
    assert all([stock.symbol for stock in result] == ["005930", "AAPL"] for result in results)


@pytest.mark.asyncio
async def test_token_is_refreshed_before_expiration() -> None:
    now = [100.0]
    token_calls = 0

    def clock() -> float:
        return now[0]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/oauth2/token":
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{token_calls}",
                    "token_type": "Bearer",
                    "expires_in": 1000,
                },
            )
        return httpx.Response(200, json=_stock_payload())

    client = _client(httpx.MockTransport(handler), clock=clock)
    await client.get_stocks(["005930"])
    now[0] = 999.0
    await client.get_stocks(["005930"])
    now[0] = 1001.0
    await client.get_stocks(["005930"])

    assert token_calls == 2


@pytest.mark.asyncio
async def test_unauthorized_request_refreshes_token_once() -> None:
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/oauth2/token":
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{token_calls}",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        if request.headers["Authorization"] == "Bearer token-1":
            return httpx.Response(401, json={"error": {"code": "expired-token"}})
        return httpx.Response(200, json=_stock_payload())

    client = _client(httpx.MockTransport(handler))
    stocks = await client.get_stocks(["005930", "AAPL"])

    assert token_calls == 2
    assert len(stocks) == 2


@pytest.mark.asyncio
async def test_rate_limit_uses_retry_after_and_stops_after_success() -> None:
    stock_calls = 0
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stock_calls
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200,
                json={"access_token": "token", "token_type": "Bearer", "expires_in": 3600},
            )
        stock_calls += 1
        if stock_calls < 3:
            return httpx.Response(
                429,
                headers={"Retry-After": "0.25"},
                json={"error": {"code": "rate-limit-exceeded"}},
            )
        return httpx.Response(200, json=_stock_payload())

    client = _client(httpx.MockTransport(handler), sleep=sleep)
    await client.get_stocks(["005930", "AAPL"])

    assert stock_calls == 3
    assert delays == [0.25, 0.25]


@pytest.mark.asyncio
async def test_daily_candles_are_converted_to_typed_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200,
                json={"access_token": "token", "token_type": "Bearer", "expires_in": 3600},
            )
        assert request.url.params["interval"] == "1d"
        assert request.url.params["adjusted"] == "true"
        return httpx.Response(
            200,
            json={
                "result": {
                    "candles": [
                        {
                            "timestamp": "2026-07-10T09:00:00+09:00",
                            "openPrice": "62000.5",
                            "highPrice": "63000",
                            "lowPrice": "61000",
                            "closePrice": "62500",
                            "volume": "1234567",
                            "currency": "KRW",
                        }
                    ],
                    "nextBefore": "2026-07-09T09:00:00+09:00",
                }
            },
        )

    client = _client(httpx.MockTransport(handler))
    page = await client.get_daily_candles("005930", count=1)

    assert str(page.candles[0].open_price) == "62000.5"
    assert page.candles[0].volume == 1_234_567
    assert page.next_before is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 500])
async def test_errors_do_not_expose_credentials_or_tokens(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                status_code,
                json={
                    "error": "invalid_client",
                    "error_description": "client-secret must never escape",
                },
            )
        raise AssertionError("market request should not run")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(TossApiError) as captured:
        await client.get_stocks(["005930"])

    message = str(captured.value)
    assert "client-secret" not in message
    assert "client-id" not in message


@pytest.mark.asyncio
async def test_timeout_is_reported_as_sanitized_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret transport detail", request=request)

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(TossApiError, match="응답 시간이 초과") as captured:
        await client.get_stocks(["AAPL"])

    assert captured.value.code == "timeout"
    assert "secret transport detail" not in str(captured.value)
