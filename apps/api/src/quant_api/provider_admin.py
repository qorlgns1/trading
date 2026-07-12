import time
from datetime import UTC, datetime

from quant_core.enums import DataSource
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quant_api.database import ProviderConnectionStatusModel, SessionFactory
from quant_api.schemas import (
    ProviderConnectionState,
    ProviderId,
    ProviderListResponse,
    ProviderStatusResponse,
    ResearchStatusResponse,
)
from quant_api.settings import Settings, get_settings
from quant_api.toss_market_data import TossApiError, TossMarketDataClient


class ProviderConnectionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = SessionFactory) -> None:
        self.session_factory = session_factory

    async def get(self, provider: ProviderId) -> ProviderConnectionStatusModel | None:
        async with self.session_factory() as session:
            return await session.get(ProviderConnectionStatusModel, provider.value)

    async def save(
        self,
        *,
        provider: ProviderId,
        status: ProviderConnectionState,
        checked_at: datetime,
        latency_ms: int | None,
        error_code: str | None,
        message: str,
    ) -> ProviderConnectionStatusModel:
        async with self.session_factory() as session:
            model = await session.get(ProviderConnectionStatusModel, provider.value)
            if model is None:
                model = ProviderConnectionStatusModel(
                    provider=provider.value,
                    status=status.value,
                    checked_at=checked_at,
                    latency_ms=latency_ms,
                    error_code=error_code[:64] if error_code else None,
                    message=message[:500],
                )
                session.add(model)
            else:
                model.status = status.value
                model.checked_at = checked_at
                model.latency_ms = latency_ms
                model.error_code = error_code[:64] if error_code else None
                model.message = message[:500]
            await session.commit()
            await session.refresh(model)
            return model


class ProviderAdminService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: ProviderConnectionRepository | None = None,
        toss_client: TossMarketDataClient | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or ProviderConnectionRepository()
        self._toss_client = toss_client

    async def providers(self, research: ResearchStatusResponse) -> ProviderListResponse:
        self._require_local()
        yfinance_active = (
            research.data_source == DataSource.YFINANCE and research.data_version is not None
        )
        krx_configured = bool(
            research.universe_version
            or (
                self.settings.krx_id
                and self.settings.krx_pw
                and self.settings.krx_id.get_secret_value()
                and self.settings.krx_pw.get_secret_value()
            )
            or (
                self.settings.research_krx_stock_csv
                and self.settings.research_krx_etf_csv
            )
        )
        krx_active = research.universe_version is not None
        toss = await self._toss_status()
        return ProviderListResponse(
            items=[
                ProviderStatusResponse(
                    provider=ProviderId.YFINANCE,
                    display_name="Yahoo Finance",
                    role="가격·거래량 주 공급자",
                    description="수정주가, 거래량과 기업행사를 수집해 추세 점수를 계산합니다.",
                    enabled=True,
                    configured=True,
                    used_in_pipeline=True,
                    status=(
                        ProviderConnectionState.ACTIVE
                        if yfinance_active
                        else ProviderConnectionState.NOT_CHECKED
                    ),
                    capabilities=["일봉 가격", "거래량", "배당·분할"],
                    last_checked_at=research.last_success_at,
                    message=(
                        "현재 활성 실데이터 스냅샷에서 사용 중입니다."
                        if yfinance_active
                        else "아직 정상 실데이터 스냅샷이 없습니다."
                    ),
                ),
                ProviderStatusResponse(
                    provider=ProviderId.KRX,
                    display_name="KRX",
                    role="한국 종목군 공급자",
                    description="KOSPI·KOSDAQ 주식과 한국 ETF의 지원 종목 목록을 만듭니다.",
                    enabled=True,
                    configured=krx_configured,
                    used_in_pipeline=True,
                    status=(
                        ProviderConnectionState.ACTIVE
                        if krx_active
                        else ProviderConnectionState.NOT_CHECKED
                    ),
                    capabilities=["한국 상장 종목", "시장·상품 분류"],
                    last_checked_at=research.last_success_at if krx_active else None,
                    message=(
                        "현재 활성 종목군 스냅샷에서 사용 중입니다."
                        if krx_active
                        else "아직 정상 종목군 스냅샷이 없습니다."
                    ),
                ),
                toss,
            ]
        )

    async def check_toss(self) -> ProviderStatusResponse:
        self._require_local()
        if not self._toss_configured() or not self.settings.tossinvest_enabled:
            return self._not_configured_toss()

        started_at = time.perf_counter()
        checked_at = datetime.now(UTC)
        try:
            stocks = await self._client().get_stocks(["005930", "AAPL"])
            stock_states = {stock.symbol: stock.status for stock in stocks}
            if stock_states != {"005930": "ACTIVE", "AAPL": "ACTIVE"}:
                raise TossApiError(
                    "unexpected-stock-response",
                    "토스에서 국내·미국 대표 종목을 모두 확인하지 못했습니다.",
                )
            status = ProviderConnectionState.AVAILABLE
            error_code = None
            message = (
                "국내·미국 대표 종목 조회에 성공했습니다. "
                "현재 데이터 흐름에는 사용하지 않습니다."
            )
        except TossApiError as error:
            status = ProviderConnectionState.UNAVAILABLE
            error_code = error.code
            message = str(error)
        except Exception:
            status = ProviderConnectionState.UNAVAILABLE
            error_code = "unexpected-error"
            message = "토스 Open API 연결 확인 중 예상하지 못한 오류가 발생했습니다."

        latency_ms = max(0, round((time.perf_counter() - started_at) * 1000))
        model = await self.repository.save(
            provider=ProviderId.TOSS,
            status=status,
            checked_at=checked_at,
            latency_ms=latency_ms,
            error_code=error_code,
            message=message,
        )
        return self._toss_response(model)

    async def _toss_status(self) -> ProviderStatusResponse:
        if not self._toss_configured() or not self.settings.tossinvest_enabled:
            return self._not_configured_toss()
        model = await self.repository.get(ProviderId.TOSS)
        if model is None:
            return ProviderStatusResponse(
                **self._toss_fields(),
                status=ProviderConnectionState.NOT_CHECKED,
                message="아직 연결을 확인하지 않았습니다.",
            )
        return self._toss_response(model)

    def _toss_response(
        self, model: ProviderConnectionStatusModel
    ) -> ProviderStatusResponse:
        try:
            state = ProviderConnectionState(model.status)
        except ValueError:
            state = ProviderConnectionState.UNAVAILABLE
        return ProviderStatusResponse(
            **self._toss_fields(),
            status=state,
            last_checked_at=model.checked_at,
            latency_ms=model.latency_ms,
            message=model.message,
        )

    def _not_configured_toss(self) -> ProviderStatusResponse:
        configured = self._toss_configured()
        message = (
            "환경 설정에서 토스 연결이 비활성화되어 있습니다."
            if configured
            else "토스 Client ID와 Client Secret이 설정되지 않았습니다."
        )
        return ProviderStatusResponse(
            **self._toss_fields(),
            status=ProviderConnectionState.NOT_CONFIGURED,
            message=message,
        )

    def _toss_fields(self) -> dict[str, object]:
        return {
            "provider": ProviderId.TOSS,
            "display_name": "토스증권 Open API",
            "role": "연결 대기 중인 보조 공급자",
            "description": "향후 시세 조회나 공급자 교차 검증에 사용할 연결 기반입니다.",
            "enabled": self.settings.tossinvest_enabled,
            "configured": self._toss_configured(),
            "used_in_pipeline": False,
            "capabilities": ["국내·미국 종목 정보", "일봉 가격"],
        }

    def _toss_configured(self) -> bool:
        return bool(
            self.settings.tossinvest_client_id
            and self.settings.tossinvest_client_secret
            and self.settings.tossinvest_client_id.get_secret_value()
            and self.settings.tossinvest_client_secret.get_secret_value()
        )

    def _client(self) -> TossMarketDataClient:
        if self._toss_client is None:
            if not self._toss_configured():
                raise RuntimeError("토스 Open API 자격증명이 없습니다.")
            assert self.settings.tossinvest_client_id is not None
            assert self.settings.tossinvest_client_secret is not None
            self._toss_client = TossMarketDataClient(
                base_url=self.settings.tossinvest_base_url,
                client_id=self.settings.tossinvest_client_id.get_secret_value(),
                client_secret=self.settings.tossinvest_client_secret.get_secret_value(),
            )
        return self._toss_client

    def _require_local(self) -> None:
        if self.settings.app_mode != "local_research":
            raise PermissionError("공급자 관리는 로컬 연구 모드에서만 사용할 수 있습니다.")


provider_admin_service = ProviderAdminService(get_settings())
