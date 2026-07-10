from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from quant_core.enums import PeerGroup
from quant_core.providers import (
    DEFAULT_BENCHMARK,
    UniverseAsset,
    YFinanceProvider,
    load_universe_csv,
)
from quant_core.synthetic import demo_universe


def test_public_mode_cannot_create_real_data_provider() -> None:
    with pytest.raises(RuntimeError, match="local_research"):
        YFinanceProvider("public_demo")


def test_yfinance_enables_price_repair_and_maps_repaired_rows(monkeypatch) -> None:
    index = pd.to_datetime(["2026-07-09"])
    raw = pd.DataFrame(
        {
            ("TEST", "Open"): [99.0],
            ("TEST", "Close"): [100.0],
            ("TEST", "Adj Close"): [100.0],
            ("TEST", "Volume"): [1_000_000.0],
            ("TEST", "Dividends"): [0.0],
            ("TEST", "Stock Splits"): [0.0],
            ("TEST", "Repaired?"): [True],
            ("^GSPC", "Close"): [6_200.0],
            ("KRW=X", "Close"): [1_370.0],
        },
        index=index,
    )
    raw.columns = pd.MultiIndex.from_tuples(raw.columns)
    received: dict[str, object] = {}

    def fake_download(**kwargs):
        received.update(kwargs)
        return raw

    monkeypatch.setattr("quant_core.providers.yf.download", fake_download)
    asset = UniverseAsset(
        ticker="TEST",
        name="Test Asset",
        peer_group=PeerGroup.US_STOCK,
        currency="USD",
        benchmark_ticker=DEFAULT_BENCHMARK[PeerGroup.US_STOCK],
    )

    frame = YFinanceProvider("local_research").fetch(
        [asset], date(2026, 7, 9), date(2026, 7, 10)
    )

    assert received["repair"] is True
    assert frame.get_column("provider_repaired").to_list() == [True]


def test_versioned_universe_csv_is_parsed(tmp_path: Path) -> None:
    path = tmp_path / "universe.csv"
    path.write_text(
        "ticker,name,peer_group,currency\nTEST,Test Asset,US_STOCK,USD\n",
        encoding="utf-8",
    )
    assets = load_universe_csv(path)
    assert len(assets) == 1
    assert assets[0].peer_group is PeerGroup.US_STOCK
    assert assets[0].benchmark_ticker == "^GSPC"


def test_universe_can_override_group_benchmark(tmp_path: Path) -> None:
    path = tmp_path / "universe.csv"
    path.write_text(
        "ticker,name,peer_group,currency,benchmark_ticker\n"
        "TEST,Test Asset,KR_OVERSEAS_EQUITY_ETF,KRW,VT\n",
        encoding="utf-8",
    )
    assert load_universe_csv(path)[0].benchmark_ticker == "VT"


def test_universe_csv_accepts_local_research_status_fields(tmp_path: Path) -> None:
    path = tmp_path / "universe.csv"
    path.write_text(
        "ticker,name,peer_group,currency,is_supported,data_status,status_reason\n"
        "TEST,Test Asset,US_STOCK,USD,false,UNSUPPORTED,상품 유형 확인 필요\n",
        encoding="utf-8",
    )
    asset = load_universe_csv(path)[0]
    assert not asset.is_supported
    assert asset.data_status.value == "UNSUPPORTED"
    assert asset.status_reason == "상품 유형 확인 필요"


def test_invalid_universe_csv_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "universe.csv"
    path.write_text("ticker,name\nTEST,Test Asset\n", encoding="utf-8")
    with pytest.raises(ValueError, match="필수 열"):
        load_universe_csv(path)


def test_demo_universe_contains_six_complete_peer_groups() -> None:
    universe = demo_universe()
    assert universe.height == 240
    assert universe.get_column("peer_group").n_unique() == 6
