import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9.\-]+$")
_MAX_RATE_LIMIT_RETRIES = 3


class TossApiError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TossKoreanMarketDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    liquidation_trading: bool = Field(alias="liquidationTrading")
    nxt_supported: bool = Field(alias="nxtSupported")
    krx_trading_suspended: bool = Field(alias="krxTradingSuspended")
    nxt_trading_suspended: bool | None = Field(default=None, alias="nxtTradingSuspended")


class TossStockInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    symbol: str
    name: str
    english_name: str | None = Field(default=None, alias="englishName")
    isin_code: str | None = Field(default=None, alias="isinCode")
    market: str
    security_type: str = Field(alias="securityType")
    is_common_share: bool = Field(alias="isCommonShare")
    status: str
    currency: str
    list_date: date | None = Field(default=None, alias="listDate")
    delist_date: date | None = Field(default=None, alias="delistDate")
    shares_outstanding: str | None = Field(default=None, alias="sharesOutstanding")
    leverage_factor: str | None = Field(default=None, alias="leverageFactor")
    korean_market_detail: TossKoreanMarketDetail | None = Field(
        default=None, alias="koreanMarketDetail"
    )


class TossCandle(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    timestamp: datetime
    open_price: Decimal = Field(alias="openPrice")
    high_price: Decimal = Field(alias="highPrice")
    low_price: Decimal = Field(alias="lowPrice")
    close_price: Decimal = Field(alias="closePrice")
    volume: int
    currency: str


class TossCandlePage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    candles: list[TossCandle]
    next_before: datetime | None = Field(default=None, alias="nextBefore")


class TossMarketDataClient:
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._token_valid_until = 0.0
        self._token_lock = asyncio.Lock()
        self._timeout = httpx.Timeout(10.0, connect=5.0)

    async def get_stocks(self, symbols: list[str]) -> list[TossStockInfo]:
        normalized = [self._validate_symbol(symbol) for symbol in symbols]
        if not 1 <= len(normalized) <= 200:
            raise ValueError("토스 종목 조회는 한 번에 1~200개 심볼을 지원합니다.")
        payload = await self._authorized_get(
            "/api/v1/stocks", params={"symbols": ",".join(normalized)}
        )
        result = payload.get("result")
        if not isinstance(result, list):
            raise TossApiError("invalid-response", "토스 종목 응답 형식이 올바르지 않습니다.")
        try:
            return [TossStockInfo.model_validate(item) for item in result]
        except ValidationError as error:
            raise TossApiError(
                "invalid-response", "토스 종목 응답 형식이 올바르지 않습니다."
            ) from error

    async def get_daily_candles(
        self,
        symbol: str,
        *,
        count: int = 100,
        before: datetime | None = None,
        adjusted: bool = True,
    ) -> TossCandlePage:
        normalized = self._validate_symbol(symbol)
        if not 1 <= count <= 200:
            raise ValueError("토스 캔들 조회 개수는 1~200이어야 합니다.")
        params: dict[str, str | int | bool] = {
            "symbol": normalized,
            "interval": "1d",
            "count": count,
            "adjusted": adjusted,
        }
        if before is not None:
            params["before"] = before.isoformat()
        payload = await self._authorized_get("/api/v1/candles", params=params)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise TossApiError("invalid-response", "토스 캔들 응답 형식이 올바르지 않습니다.")
        try:
            return TossCandlePage.model_validate(result)
        except ValidationError as error:
            raise TossApiError(
                "invalid-response", "토스 캔들 응답 형식이 올바르지 않습니다."
            ) from error

    async def _authorized_get(
        self, path: str, *, params: dict[str, str | int | bool]
    ) -> dict[str, Any]:
        token = await self._access_token()
        refreshed = False
        rate_limit_retries = 0
        while True:
            response = await self._send(
                "GET",
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and not refreshed:
                await self._invalidate_token(token)
                token = await self._access_token()
                refreshed = True
                continue
            if response.status_code == 429 and rate_limit_retries < _MAX_RATE_LIMIT_RETRIES:
                await self._sleep(self._retry_delay(response, rate_limit_retries))
                rate_limit_retries += 1
                continue
            if response.is_success:
                return self._json_object(response)
            raise self._api_error(response)

    async def _access_token(self) -> str:
        if self._token is not None and self._clock() < self._token_valid_until:
            return self._token
        async with self._token_lock:
            if self._token is not None and self._clock() < self._token_valid_until:
                return self._token
            token, expires_in = await self._issue_token()
            safety_window = min(300.0, max(1.0, expires_in * 0.1))
            self._token = token
            self._token_valid_until = self._clock() + max(1.0, expires_in - safety_window)
            return token

    async def _issue_token(self) -> tuple[str, float]:
        rate_limit_retries = 0
        while True:
            response = await self._send(
                "POST",
                "/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code == 429 and rate_limit_retries < _MAX_RATE_LIMIT_RETRIES:
                await self._sleep(self._retry_delay(response, rate_limit_retries))
                rate_limit_retries += 1
                continue
            if not response.is_success:
                raise self._api_error(response)
            payload = self._json_object(response)
            token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if not isinstance(token, str) or not token or not isinstance(expires_in, (int, float)):
                raise TossApiError(
                    "invalid-response", "토스 인증 응답 형식이 올바르지 않습니다."
                )
            return token, float(expires_in)

    async def _invalidate_token(self, token: str) -> None:
        async with self._token_lock:
            if self._token == token:
                self._token = None
                self._token_valid_until = 0.0

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                headers={"Accept": "application/json"},
            ) as client:
                return await client.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            raise TossApiError("timeout", "토스 Open API 응답 시간이 초과되었습니다.") from error
        except httpx.HTTPError as error:
            raise TossApiError("network-error", "토스 Open API에 연결할 수 없습니다.") from error

    @staticmethod
    def _validate_symbol(symbol: str) -> str:
        normalized = symbol.strip().upper()
        if not normalized or _SYMBOL_PATTERN.fullmatch(normalized) is None:
            raise ValueError("토스 종목 심볼 형식이 올바르지 않습니다.")
        return normalized

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise TossApiError(
                "invalid-response", "토스 Open API 응답을 해석할 수 없습니다."
            ) from error
        if not isinstance(payload, dict):
            raise TossApiError("invalid-response", "토스 Open API 응답 형식이 올바르지 않습니다.")
        return payload

    @staticmethod
    def _retry_delay(response: httpx.Response, retry_index: int) -> float:
        raw_value = response.headers.get("Retry-After")
        try:
            retry_after = float(raw_value) if raw_value is not None else 2**retry_index
        except ValueError:
            retry_after = 2**retry_index
        return min(30.0, max(0.0, retry_after))

    @classmethod
    def _api_error(cls, response: httpx.Response) -> TossApiError:
        code = "http-error"
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                code = error["code"]
            elif isinstance(error, str):
                code = error
        except ValueError:
            pass

        if response.status_code == 401:
            message = "토스 Open API 인증에 실패했습니다."
        elif response.status_code == 403:
            message = "토스 Open API 접근이 거부되었습니다. 허용 IP와 권한을 확인하세요."
        elif response.status_code == 404:
            message = "토스 Open API에서 요청한 데이터를 찾지 못했습니다."
        elif response.status_code == 429:
            message = "토스 Open API 요청 한도를 초과했습니다."
        elif response.status_code >= 500:
            message = "토스 Open API가 일시적으로 응답하지 않습니다."
        else:
            message = "토스 Open API 요청을 처리하지 못했습니다."
        return TossApiError(code, message, status_code=response.status_code)
