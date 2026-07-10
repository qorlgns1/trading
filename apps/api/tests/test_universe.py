import json
from pathlib import Path

import httpx
import pytest
from quant_api.universe import (
    ExchangeUniverseClient,
    UniverseSnapshot,
    build_universe_snapshot,
    parse_krx_etf_csv,
    parse_krx_stock_csv,
    parse_nasdaq_symbol_files,
    parse_pykrx_etf_records,
    parse_pykrx_stock_records,
)
from quant_core.enums import DataStatus, PeerGroup

FIXTURES = Path(__file__).parent / "fixtures"


def test_nasdaq_parser_keeps_supported_assets_and_marks_uncertain_types() -> None:
    assets = parse_nasdaq_symbol_files(
        (FIXTURES / "nasdaqlisted.txt").read_text(),
        (FIXTURES / "otherlisted.txt").read_text(),
    )
    by_ticker = {asset.ticker: asset for asset in assets}

    assert by_ticker["AAPL"].peer_group is PeerGroup.US_STOCK
    assert by_ticker["BRK-B"].is_supported
    assert by_ticker["QQQ"].peer_group is PeerGroup.US_EQUITY_ETF
    assert by_ticker["SPY"].is_supported
    assert by_ticker["PSQ"].data_status is DataStatus.UNSUPPORTED
    assert by_ticker["TLT"].data_status is DataStatus.UNSUPPORTED
    assert by_ticker["MYST"].status_reason == "상품 유형 확인 필요"
    assert by_ticker["SPCX"].data_status is DataStatus.UNSUPPORTED
    assert "TEST" not in by_ticker


def test_krx_parsers_separate_common_stock_and_equity_etf_groups() -> None:
    stocks = parse_krx_stock_csv((FIXTURES / "krx-stocks.csv").read_bytes())
    etfs = parse_krx_etf_csv((FIXTURES / "krx-etfs.csv").read_bytes())
    by_ticker = {asset.ticker: asset for asset in stocks + etfs}

    assert by_ticker["005930.KS"].peer_group is PeerGroup.KR_KOSPI
    assert by_ticker["005935.KS"].data_status is DataStatus.UNSUPPORTED
    assert by_ticker["048260.KQ"].peer_group is PeerGroup.KR_KOSDAQ
    assert by_ticker["069500.KS"].peer_group is PeerGroup.KR_DOMESTIC_EQUITY_ETF
    assert by_ticker["360750.KS"].peer_group is PeerGroup.KR_OVERSEAS_EQUITY_ETF
    assert by_ticker["122630.KS"].data_status is DataStatus.UNSUPPORTED
    assert by_ticker["143260.KS"].data_status is DataStatus.UNSUPPORTED


def test_pykrx_records_use_official_security_and_asset_types() -> None:
    stocks = parse_pykrx_stock_records(
        [
            {
                "ISU_SRT_CD": "005930",
                "ISU_ABBRV": "삼성전자",
                "MKT_TP_NM": "KOSPI",
                "SECUGRP_NM": "주권",
                "KIND_STKCERT_TP_NM": "보통주",
            },
            {
                "ISU_SRT_CD": "005935",
                "ISU_ABBRV": "삼성전자우",
                "MKT_TP_NM": "KOSPI",
                "SECUGRP_NM": "주권",
                "KIND_STKCERT_TP_NM": "구형우선주",
            },
            {
                "ISU_SRT_CD": "048260",
                "ISU_ABBRV": "오스템임플란트",
                "MKT_TP_NM": "KOSDAQ GLOBAL",
                "SECUGRP_NM": "주권",
                "KIND_STKCERT_TP_NM": "보통주",
            },
        ]
    )
    etfs = parse_pykrx_etf_records(
        [
            {
                "ISU_SRT_CD": "069500",
                "ISU_ABBRV": "KODEX 200",
                "IDX_MKT_CLSS_NM": "국내",
                "IDX_ASST_CLSS_NM": "주식",
                "ETF_OBJ_IDX_NM": "코스피 200",
            },
            {
                "ISU_SRT_CD": "360750",
                "ISU_ABBRV": "TIGER 미국S&P500",
                "IDX_MKT_CLSS_NM": "해외",
                "IDX_ASST_CLSS_NM": "주식",
                "ETF_OBJ_IDX_NM": "S&P 500",
            },
            {
                "ISU_SRT_CD": "122630",
                "ISU_ABBRV": "KODEX 레버리지",
                "IDX_MKT_CLSS_NM": "국내",
                "IDX_ASST_CLSS_NM": "주식",
                "ETF_OBJ_IDX_NM": "코스피 200 2X",
            },
            {
                "ISU_SRT_CD": "143260",
                "ISU_ABBRV": "TIGER 국채3년",
                "IDX_MKT_CLSS_NM": "국내",
                "IDX_ASST_CLSS_NM": "채권",
                "ETF_OBJ_IDX_NM": "국채 3년",
            },
        ]
    )
    by_ticker = {asset.ticker: asset for asset in stocks + etfs}

    assert by_ticker["005930.KS"].is_supported
    assert by_ticker["005935.KS"].data_status is DataStatus.UNSUPPORTED
    assert by_ticker["048260.KQ"].peer_group is PeerGroup.KR_KOSDAQ
    assert by_ticker["069500.KS"].peer_group is PeerGroup.KR_DOMESTIC_EQUITY_ETF
    assert by_ticker["360750.KS"].peer_group is PeerGroup.KR_OVERSEAS_EQUITY_ETF
    assert by_ticker["122630.KS"].data_status is DataStatus.UNSUPPORTED
    assert by_ticker["143260.KS"].status_reason == "주식형 ETF가 아닙니다."


class FixtureUniverseClient:
    def fetch_nasdaq(self):  # type: ignore[no-untyped-def]
        return parse_nasdaq_symbol_files(
            (FIXTURES / "nasdaqlisted.txt").read_text(),
            (FIXTURES / "otherlisted.txt").read_text(),
        ), {"nasdaq": "fixture"}

    def fetch_krx(self, stock_csv=None, etf_csv=None):  # type: ignore[no-untyped-def]
        del stock_csv, etf_csv
        return (
            parse_krx_stock_csv((FIXTURES / "krx-stocks.csv").read_bytes())
            + parse_krx_etf_csv((FIXTURES / "krx-etfs.csv").read_bytes()),
            {"krx": "fixture"},
        )


class FixtureKrxProvider:
    def fetch_records(self):  # type: ignore[no-untyped-def]
        return (
            [
                {
                    "ISU_SRT_CD": "005930",
                    "ISU_ABBRV": "삼성전자",
                    "MKT_TP_NM": "KOSPI",
                    "SECUGRP_NM": "주권",
                    "KIND_STKCERT_TP_NM": "보통주",
                }
            ],
            [
                {
                    "ISU_SRT_CD": "069500",
                    "ISU_ABBRV": "KODEX 200",
                    "IDX_MKT_CLSS_NM": "국내",
                    "IDX_ASST_CLSS_NM": "주식",
                    "ETF_OBJ_IDX_NM": "코스피 200",
                }
            ],
        )


def test_exchange_client_prefers_authenticated_pykrx_provider() -> None:
    client = ExchangeUniverseClient(krx_provider=FixtureKrxProvider())
    assets, sources = client.fetch_krx()

    assert {asset.ticker for asset in assets} == {"005930.KS", "069500.KS"}
    assert sources["krx_stock"].startswith("pykrx authenticated")
    client.client.close()


def test_universe_snapshot_is_versioned_and_manifested(tmp_path: Path) -> None:
    snapshot = build_universe_snapshot(
        tmp_path,
        client=FixtureUniverseClient(),  # type: ignore[arg-type]
    )

    assert isinstance(snapshot, UniverseSnapshot)
    assert snapshot.path.is_file()
    assert snapshot.manifest_path.is_file()
    assert snapshot.version.startswith("universe-")
    assert json.loads(snapshot.manifest_path.read_text())["counts"]["US_STOCK"] >= 1


def test_krx_login_response_has_actionable_local_csv_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="LOGOUT", request=request)
    )
    client = ExchangeUniverseClient(httpx.Client(transport=transport))
    with pytest.raises(RuntimeError, match="RESEARCH_KRX_STOCK_CSV"):
        client.fetch_krx()
    client.client.close()
