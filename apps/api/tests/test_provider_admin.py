from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from quant_api.database import Base
from quant_api.main import app
from quant_api.provider_admin import ProviderAdminService, ProviderConnectionRepository
from quant_api.schemas import ProviderConnectionState, ProviderId
from quant_api.settings import Settings
from quant_api.toss_market_data import TossApiError, TossStockInfo
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class MemoryProviderRepository:
    def __init__(self) -> None:
        self.model: SimpleNamespace | None = None

    async def get(self, provider: ProviderId) -> SimpleNamespace | None:
        if self.model is not None and self.model.provider == provider.value:
            return self.model
        return None

    async def save(
        self,
        *,
        provider: ProviderId,
        status: ProviderConnectionState,
        checked_at: datetime,
        latency_ms: int | None,
        error_code: str | None,
        message: str,
    ) -> SimpleNamespace:
        self.model = SimpleNamespace(
            provider=provider.value,
            status=status.value,
            checked_at=checked_at,
            latency_ms=latency_ms,
            error_code=error_code,
            message=message,
        )
        return self.model


class SuccessfulTossClient:
    async def get_stocks(self, symbols: list[str]) -> list[TossStockInfo]:
        assert symbols == ["005930", "AAPL"]
        return [
            TossStockInfo.model_validate(
                {
                    "symbol": symbol,
                    "name": "삼성전자" if symbol == "005930" else "애플",
                    "market": "KOSPI" if symbol == "005930" else "NASDAQ",
                    "securityType": "STOCK",
                    "isCommonShare": True,
                    "status": "ACTIVE",
                    "currency": "KRW" if symbol == "005930" else "USD",
                }
            )
            for symbol in symbols
        ]


class FailedTossClient:
    async def get_stocks(self, symbols: list[str]) -> list[TossStockInfo]:
        del symbols
        raise TossApiError("invalid_client", "토스 Open API 인증에 실패했습니다.")


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_mode": "local_research",
        "research_auto_sync": False,
        "tossinvest_enabled": True,
        "tossinvest_client_id": "test-client-id",
        "tossinvest_client_secret": "test-client-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_local_admin_api_lists_roles_and_checks_toss(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = MemoryProviderRepository()
    service = ProviderAdminService(
        _settings(),
        repository=repository,  # type: ignore[arg-type]
        toss_client=SuccessfulTossClient(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("quant_api.main.provider_admin_service", service)

    with TestClient(app) as client:
        providers = client.get("/api/v1/admin/providers")
        assert providers.status_code == 200
        items = providers.json()["items"]
        assert [item["provider"] for item in items] == ["YFINANCE", "KRX", "TOSS"]
        toss = items[2]
        assert toss["status"] == "NOT_CHECKED"
        assert toss["used_in_pipeline"] is False

        checked = client.post("/api/v1/admin/providers/toss/check")
        assert checked.status_code == 200
        assert checked.json()["status"] == "AVAILABLE"
        assert checked.json()["latency_ms"] >= 0
        assert "test-client" not in checked.text

        persisted = client.get("/api/v1/admin/providers").json()["items"][2]
        assert persisted["status"] == "AVAILABLE"
        assert persisted["last_checked_at"] is not None


def test_toss_failure_is_a_sanitized_status_not_an_api_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProviderAdminService(
        _settings(),
        repository=MemoryProviderRepository(),  # type: ignore[arg-type]
        toss_client=FailedTossClient(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("quant_api.main.provider_admin_service", service)

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/providers/toss/check")

    assert response.status_code == 200
    assert response.json()["status"] == "UNAVAILABLE"
    assert "test-client-id" not in response.text
    assert "test-client-secret" not in response.text


def test_disabled_toss_provider_does_not_make_a_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProviderAdminService(
        _settings(tossinvest_enabled=False),
        repository=MemoryProviderRepository(),  # type: ignore[arg-type]
        toss_client=FailedTossClient(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("quant_api.main.provider_admin_service", service)

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/providers/toss/check")

    assert response.status_code == 200
    assert response.json()["status"] == "NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_provider_status_persists_in_sqlite(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'provider.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    first_repository = ProviderConnectionRepository(session_factory)
    checked_at = datetime(2026, 7, 11, 9, 30, tzinfo=UTC)
    await first_repository.save(
        provider=ProviderId.TOSS,
        status=ProviderConnectionState.AVAILABLE,
        checked_at=checked_at,
        latency_ms=142,
        error_code=None,
        message="연결 성공",
    )

    second_repository = ProviderConnectionRepository(session_factory)
    restored = await second_repository.get(ProviderId.TOSS)
    await engine.dispose()

    assert restored is not None
    assert restored.status == "AVAILABLE"
    assert restored.latency_ms == 142
    assert restored.message == "연결 성공"
