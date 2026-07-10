import csv
import hashlib
import io
import json
import os
import re
import threading
from collections.abc import Callable, Iterable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlencode

import httpx
import polars as pl
from quant_core.enums import DataStatus, PeerGroup
from quant_core.providers import DEFAULT_BENCHMARK, UniverseAsset

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
NASDAQ_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
KRX_OTP_URL = "https://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
KRX_DOWNLOAD_URL = "https://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
KRX_STOCK_REPORT = "dbms/MDC/STAT/standard/MDCSTAT01901"
KRX_ETF_REPORT = "dbms/MDC/STAT/standard/MDCSTAT04601"

UNSUPPORTED_STOCK_TERMS = (
    " preferred",
    " warrant",
    " right",
    " unit",
    " notes",
    " note due",
    " depositary shares",
    " acquisition corp",
    " acquisition co",
    " blank check",
    " spac",
)
UNSUPPORTED_ETF_TERMS = (
    "2x",
    "3x",
    "-1x",
    "-2x",
    "ultra",
    "ultrapro",
    "inverse",
    "short",
    "bear",
    "treasury",
    "bond",
    "fixed income",
    "municipal",
    "commodity",
    "gold",
    "silver",
    "bitcoin",
    "ether",
    "currency",
    "futures",
    "autocallable",
    "barrier",
    "covered call",
    "option income",
    "option strategy",
    "buffer",
    "managed futures",
    "multi-asset",
    "securitized",
    "credit",
    "floating rate",
    "loan",
    "mortgage",
    "t-bill",
    "convertible",
    "preferred",
    "agriculture",
    "base metals",
    "precious metals",
)
EQUITY_ETF_TERMS = (
    "equity",
    "stock",
    "dividend",
    "growth",
    "value",
    "small cap",
    "mid cap",
    "large cap",
    "total market",
    "s&p",
    "nasdaq",
    "russell",
    "dow jones",
    "msci",
    "ftse",
    "stoxx",
    "companies",
    "sector",
    "technology",
    "health care",
    "financial",
    "industrial",
    "consumer",
    "utilities",
    "materials",
    "real estate",
    "semiconductor",
    "biotech",
    "software",
    "internet",
    "emerging markets",
    "developed markets",
)
KNOWN_EQUITY_ETFS = {"DIA", "IWM", "QQQ", "SPY", "VTI", "VT", "VOO"}
KR_UNSUPPORTED_TERMS = (
    "레버리지",
    "인버스",
    "2X",
    "2차전지채권",
    "국채",
    "회사채",
    "단기채",
    "금선물",
    "은선물",
    "원유",
    "달러선물",
    "엔선물",
    "채권",
)
KR_STOCK_UNSUPPORTED_TERMS = ("스팩", "기업인수목적", "우선주")
_PYKRX_LOCK = threading.Lock()


@dataclass(frozen=True)
class UniverseSnapshot:
    version: str
    path: Path
    manifest_path: Path
    assets: list[UniverseAsset]
    sources: dict[str, Any]
    counts: dict[str, int]


def _rows(text: str) -> list[dict[str, str]]:
    lines = [
        line
        for line in text.splitlines()
        if line and not line.startswith("File Creation Time")
    ]
    if not lines:
        return []
    return list(csv.DictReader(lines, delimiter="|"))


def _field(row: dict[str, str], *names: str) -> str:
    normalized = {
        key.strip().replace(" ", ""): (value or "").strip()
        for key, value in row.items()
        if key is not None
    }
    for name in names:
        value = normalized.get(name.replace(" ", ""))
        if value:
            return value
    return ""


def _yahoo_us_symbol(symbol: str) -> str:
    return symbol.strip().replace(".", "-")


def _unsupported_reason(name: str, terms: tuple[str, ...]) -> str | None:
    lowered = f" {name.lower()}"
    return next((term.strip() for term in terms if term.lower() in lowered), None)


def _classify_us_etf(ticker: str, name: str) -> tuple[bool, str | None]:
    unsupported = _unsupported_reason(name, UNSUPPORTED_ETF_TERMS)
    if unsupported is not None:
        return False, f"지원하지 않는 ETF 유형: {unsupported}"
    lowered = name.lower()
    if ticker in KNOWN_EQUITY_ETFS or any(term in lowered for term in EQUITY_ETF_TERMS):
        return True, None
    return False, "상품 유형 확인 필요"


def parse_nasdaq_symbol_files(nasdaq_text: str, other_text: str) -> list[UniverseAsset]:
    assets: dict[str, UniverseAsset] = {}
    for source, rows in (
        ("NASDAQ_LISTED", _rows(nasdaq_text)),
        ("NASDAQ_OTHER", _rows(other_text)),
    ):
        for row in rows:
            if _field(row, "Test Issue", "TestIssue") == "Y":
                continue
            raw_symbol = _field(row, "Symbol", "ACT Symbol", "NASDAQ Symbol")
            name = _field(row, "Security Name", "SecurityName")
            if not raw_symbol or not name or re.search(r"[$+^/]", raw_symbol):
                continue
            is_etf = _field(row, "ETF").upper() == "Y"
            ticker = _yahoo_us_symbol(raw_symbol)
            exchange = _field(row, "Exchange", "Market Category") or "NASDAQ"
            if is_etf:
                group = PeerGroup.US_EQUITY_ETF
                supported, reason = _classify_us_etf(ticker, name)
            else:
                unsupported = _unsupported_reason(name, UNSUPPORTED_STOCK_TERMS)
                reason = (
                    f"지원하지 않는 상품 유형: {unsupported}"
                    if unsupported is not None
                    else None
                )
                group = PeerGroup.US_STOCK
                supported = reason is None
            assets[f"{group.value}:{ticker}"] = UniverseAsset(
                ticker=ticker,
                name=name,
                peer_group=group,
                currency="USD",
                benchmark_ticker=DEFAULT_BENCHMARK[group],
                is_supported=supported,
                data_status=DataStatus.READY if supported else DataStatus.UNSUPPORTED,
                status_reason=None if supported else reason,
                exchange=exchange,
                source=source,
            )
    return sorted(assets.values(), key=lambda asset: (asset.peer_group.value, asset.ticker))


def _decode_krx(content: bytes) -> str:
    for encoding in ("euc-kr", "cp949", "utf-8-sig"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _csv_rows(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(_decode_krx(content))))


def parse_krx_stock_csv(content: bytes) -> list[UniverseAsset]:
    assets: list[UniverseAsset] = []
    for row in _csv_rows(content):
        raw_symbol = _field(row, "단축코드", "종목코드", "단축종목코드")
        symbol = raw_symbol.zfill(6) if raw_symbol else ""
        name = _field(row, "한글 종목약명", "한글종목약명", "한글 종목명", "종목명")
        market = _field(row, "시장구분", "시장")
        stock_type = _field(row, "주식종류", "증권구분", "종목구분")
        if not symbol or not name or market not in {"KOSPI", "KOSDAQ"}:
            continue
        unsupported_term = next(
            (term for term in KR_STOCK_UNSUPPORTED_TERMS if term in name), None
        )
        is_common = bool(stock_type) and "보통" in stock_type and unsupported_term is None
        group = PeerGroup.KR_KOSPI if market == "KOSPI" else PeerGroup.KR_KOSDAQ
        ticker = f"{symbol}.{'KS' if market == 'KOSPI' else 'KQ'}"
        assets.append(
            UniverseAsset(
                ticker=ticker,
                name=name,
                peer_group=group,
                currency="KRW",
                benchmark_ticker=DEFAULT_BENCHMARK[group],
                is_supported=is_common,
                data_status=DataStatus.READY if is_common else DataStatus.UNSUPPORTED,
                status_reason=(
                    None
                    if is_common
                    else "상품 유형 확인 필요"
                    if not stock_type
                    else f"지원하지 않는 상품 유형: {unsupported_term}"
                    if unsupported_term is not None
                    else "보통주가 아닌 종목입니다."
                ),
                exchange=market,
                source="KRX_STOCK",
            )
        )
    return assets


def parse_krx_etf_csv(content: bytes) -> list[UniverseAsset]:
    assets: list[UniverseAsset] = []
    for row in _csv_rows(content):
        raw_symbol = _field(row, "단축코드", "종목코드", "단축종목코드")
        symbol = raw_symbol.zfill(6) if raw_symbol else ""
        name = _field(row, "한글종목약명", "한글 종목약명", "한글종목명", "종목명")
        base_market = _field(row, "기초시장분류", "기초시장")
        base_asset = _field(row, "기초자산분류", "기초자산")
        multiplier = _field(row, "추적배수", "추적배율")
        if not symbol or not name:
            continue
        overseas = "해외" in base_market or any(
            marker in name.upper() for marker in ("미국", "차이나", "일본", "유럽", "글로벌")
        )
        group = (
            PeerGroup.KR_OVERSEAS_EQUITY_ETF
            if overseas
            else PeerGroup.KR_DOMESTIC_EQUITY_ETF
        )
        unsupported_term = next((term for term in KR_UNSUPPORTED_TERMS if term in name), None)
        is_equity = bool(base_asset) and "주식" in base_asset
        is_one_x = not multiplier or multiplier in {"1", "1.0", "1배"}
        supported = is_equity and is_one_x and unsupported_term is None
        reason = None
        if not base_asset:
            reason = "상품 유형 확인 필요"
        elif not is_equity:
            reason = "주식형 ETF가 아닙니다."
        elif not is_one_x or unsupported_term is not None:
            reason = "레버리지·인버스 또는 비주식형 ETF입니다."
        assets.append(
            UniverseAsset(
                ticker=f"{symbol}.KS",
                name=name,
                peer_group=group,
                currency="KRW",
                benchmark_ticker=DEFAULT_BENCHMARK[group],
                is_supported=supported,
                data_status=DataStatus.READY if supported else DataStatus.UNSUPPORTED,
                status_reason=reason,
                exchange="KRX_ETF",
                source="KRX_ETF",
            )
        )
    return assets


def _record_value(row: dict[str, str], key: str) -> str:
    return str(row.get(key, "") or "").strip()


def parse_pykrx_stock_records(records: Iterable[dict[str, str]]) -> list[UniverseAsset]:
    assets: list[UniverseAsset] = []
    for row in records:
        raw_symbol = _record_value(row, "ISU_SRT_CD")
        symbol = raw_symbol.zfill(6) if raw_symbol else ""
        name = _record_value(row, "ISU_ABBRV")
        raw_market = _record_value(row, "MKT_TP_NM")
        market = "KOSPI" if raw_market.startswith("KOSPI") else "KOSDAQ"
        if not symbol or not name or not raw_market.startswith(("KOSPI", "KOSDAQ")):
            continue

        security_type = _record_value(row, "SECUGRP_NM")
        stock_type = _record_value(row, "KIND_STKCERT_TP_NM")
        unsupported_term = next(
            (term for term in KR_STOCK_UNSUPPORTED_TERMS if term in name), None
        )
        is_common = (
            security_type == "주권" and stock_type == "보통주" and unsupported_term is None
        )
        reason = None
        if unsupported_term is not None:
            reason = f"지원하지 않는 상품 유형: {unsupported_term}"
        elif security_type != "주권":
            reason = f"지원하지 않는 증권 유형: {security_type or '확인 필요'}"
        elif stock_type != "보통주":
            reason = "보통주가 아닌 종목입니다."

        group = PeerGroup.KR_KOSPI if market == "KOSPI" else PeerGroup.KR_KOSDAQ
        assets.append(
            UniverseAsset(
                ticker=f"{symbol}.{'KS' if market == 'KOSPI' else 'KQ'}",
                name=name,
                peer_group=group,
                currency="KRW",
                benchmark_ticker=DEFAULT_BENCHMARK[group],
                is_supported=is_common,
                data_status=DataStatus.READY if is_common else DataStatus.UNSUPPORTED,
                status_reason=reason,
                exchange=market,
                source="PYKRX_STOCK",
            )
        )
    return assets


def parse_pykrx_etf_records(records: Iterable[dict[str, str]]) -> list[UniverseAsset]:
    assets: list[UniverseAsset] = []
    for row in records:
        raw_symbol = _record_value(row, "ISU_SRT_CD")
        symbol = raw_symbol.zfill(6) if raw_symbol else ""
        name = _record_value(row, "ISU_ABBRV")
        base_market = _record_value(row, "IDX_MKT_CLSS_NM")
        base_asset = _record_value(row, "IDX_ASST_CLSS_NM")
        objective = _record_value(row, "ETF_OBJ_IDX_NM")
        if not symbol or not name:
            continue

        group = (
            PeerGroup.KR_OVERSEAS_EQUITY_ETF
            if "해외" in base_market
            else PeerGroup.KR_DOMESTIC_EQUITY_ETF
        )
        product_text = f"{name} {objective}".upper()
        unsupported_term = next(
            (term for term in KR_UNSUPPORTED_TERMS if term.upper() in product_text), None
        )
        is_equity = base_asset == "주식"
        supported = is_equity and unsupported_term is None
        reason = None
        if not base_asset:
            reason = "상품 유형 확인 필요"
        elif not is_equity:
            reason = "주식형 ETF가 아닙니다."
        elif unsupported_term is not None:
            reason = "레버리지·인버스 ETF는 지원하지 않습니다."

        assets.append(
            UniverseAsset(
                ticker=f"{symbol}.KS",
                name=name,
                peer_group=group,
                currency="KRW",
                benchmark_ticker=DEFAULT_BENCHMARK[group],
                is_supported=supported,
                data_status=DataStatus.READY if supported else DataStatus.UNSUPPORTED,
                status_reason=reason,
                exchange="KRX_ETF",
                source="PYKRX_ETF",
            )
        )
    return assets


class KrxUniverseProvider(Protocol):
    def fetch_records(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]: ...


class PykrxUniverseProvider:
    """Authenticated KRX basic-info access isolated behind the pinned pykrx adapter."""

    def __init__(self, login_id: str, login_password: str) -> None:
        self._login_id = login_id
        self._login_password = login_password

    @staticmethod
    def _records(frame: Any) -> list[dict[str, str]]:
        raw_rows = cast(
            list[dict[str, Any]], frame.fillna("").to_dict(orient="records")
        )
        return [
            {str(key): str(value).strip() for key, value in row.items()} for row in raw_rows
        ]

    def fetch_records(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        if not self._login_id or not self._login_password:
            raise RuntimeError("pykrx KRX 수집에는 KRX_ID와 KRX_PW가 모두 필요합니다.")

        # pykrx initializes its authenticated session at import time and prints the login ID.
        # Keep credentials in the process environment for session refresh, but suppress its output.
        with _PYKRX_LOCK:
            os.environ["KRX_ID"] = self._login_id
            os.environ["KRX_PW"] = self._login_password
            captured_output = io.StringIO()
            try:
                with redirect_stdout(captured_output), redirect_stderr(captured_output):
                    auth_module = import_module("pykrx.website.comm.auth")
                    market_module = import_module("pykrx.website.krx.market.core")
                    etf_module = import_module("pykrx.website.krx.etx.core")
                    if auth_module.get_auth_session() is None:
                        raise RuntimeError("KRX 인증에 실패했습니다.")
                    stock_frame = market_module.전종목기본정보().fetch(
                        mktId="ALL", segTpCd="ALL"
                    )
                    etf_frame = etf_module.ETF_전종목기본종목().fetch()
            except Exception as error:
                raise RuntimeError(
                    "pykrx를 통한 KRX 로그인 또는 기본정보 수집에 실패했습니다. "
                    "KRX 계정 상태와 KRX_ID·KRX_PW를 확인하세요."
                ) from error

        stocks = self._records(stock_frame)
        etfs = self._records(etf_frame)
        if not stocks or not etfs:
            raise RuntimeError("pykrx KRX 기본정보 응답이 비어 있습니다.")
        return stocks, etfs


class ExchangeUniverseClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        krx_provider: KrxUniverseProvider | None = None,
        krx_id: str | None = None,
        krx_password: str | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            timeout=45,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 QuantTrendLab/0.1 (personal research)",
                "Referer": "http://data.krx.co.kr/",
            },
        )
        self.krx_provider = krx_provider
        if self.krx_provider is None and krx_id and krx_password:
            self.krx_provider = PykrxUniverseProvider(krx_id, krx_password)

    def fetch_nasdaq(self) -> tuple[list[UniverseAsset], dict[str, Any]]:
        nasdaq = self.client.get(NASDAQ_LISTED_URL)
        other = self.client.get(NASDAQ_OTHER_URL)
        nasdaq.raise_for_status()
        other.raise_for_status()
        return parse_nasdaq_symbol_files(nasdaq.text, other.text), {
            "nasdaq_listed": NASDAQ_LISTED_URL,
            "nasdaq_other": NASDAQ_OTHER_URL,
        }

    def _krx_report(self, report: str, params: dict[str, str]) -> bytes:
        payload = {"name": "fileDown", "csvxls_isNo": "false", "url": report, **params}
        otp = self.client.post(
            KRX_OTP_URL,
            content=urlencode(payload),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        otp.raise_for_status()
        code = otp.text.strip()
        if not code or code == "LOGOUT":
            raise RuntimeError(
                "KRX가 비로그인 CSV 요청을 허용하지 않았습니다. KRX_ID·KRX_PW를 설정하거나 "
                "전종목 기본정보 CSV를 RESEARCH_KRX_STOCK_CSV와 "
                "RESEARCH_KRX_ETF_CSV에 지정하세요."
            )
        response = self.client.post(
            KRX_DOWNLOAD_URL,
            content=urlencode({"code": code}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        if not response.content:
            raise RuntimeError("KRX CSV 응답이 비어 있습니다. 로컬 KRX CSV 경로를 확인하세요.")
        return response.content

    def fetch_krx(
        self, stock_csv: Path | None = None, etf_csv: Path | None = None
    ) -> tuple[list[UniverseAsset], dict[str, Any]]:
        if (stock_csv is None) != (etf_csv is None):
            raise RuntimeError(
                "KRX CSV 대체 경로를 사용할 때는 주식과 ETF 파일을 모두 지정해야 합니다."
            )

        provider_error: RuntimeError | None = None
        if self.krx_provider is not None:
            try:
                stock_records, etf_records = self.krx_provider.fetch_records()
                stocks = parse_pykrx_stock_records(stock_records)
                etfs = parse_pykrx_etf_records(etf_records)
                if not stocks or not etfs:
                    raise RuntimeError("pykrx 응답에서 KRX 주식·ETF 종목을 찾지 못했습니다.")
                return stocks + etfs, {
                    "krx_stock": "pykrx authenticated KRX basic information",
                    "krx_etf": "pykrx authenticated KRX basic information",
                }
            except RuntimeError as error:
                provider_error = error
                if stock_csv is None or etf_csv is None:
                    raise

        if stock_csv is not None and etf_csv is not None:
            stock_content = stock_csv.read_bytes()
            etf_content = etf_csv.read_bytes()
            stocks = parse_krx_stock_csv(stock_content)
            etfs = parse_krx_etf_csv(etf_content)
            if not stocks:
                raise RuntimeError("KRX 주식 CSV에서 KOSPI·KOSDAQ 종목을 찾지 못했습니다.")
            if not etfs:
                raise RuntimeError("KRX ETF CSV에서 종목을 찾지 못했습니다.")
            sources: dict[str, Any] = {
                "krx_stock": str(stock_csv),
                "krx_etf": str(etf_csv),
            }
            if provider_error is not None:
                sources["krx_fallback"] = "local CSV used after pykrx failure"
            return stocks + etfs, sources

        stock_content = (
            stock_csv.read_bytes()
            if stock_csv is not None
            else self._krx_report(KRX_STOCK_REPORT, {"mktId": "ALL", "share": "1"})
        )
        etf_content = (
            etf_csv.read_bytes()
            if etf_csv is not None
            else self._krx_report(KRX_ETF_REPORT, {})
        )
        stocks = parse_krx_stock_csv(stock_content)
        etfs = parse_krx_etf_csv(etf_content)
        if not stocks:
            raise RuntimeError("KRX 주식 CSV에서 KOSPI·KOSDAQ 종목을 찾지 못했습니다.")
        if not etfs:
            raise RuntimeError("KRX ETF CSV에서 종목을 찾지 못했습니다.")
        return stocks + etfs, {
            "krx_stock": str(stock_csv) if stock_csv else KRX_STOCK_REPORT,
            "krx_etf": str(etf_csv) if etf_csv else KRX_ETF_REPORT,
        }


def _asset_row(asset: UniverseAsset) -> dict[str, Any]:
    row = asdict(asset)
    row["peer_group"] = asset.peer_group.value
    row["data_status"] = asset.data_status.value
    row["asset_id"] = f"{asset.peer_group.value}:{asset.ticker}"
    row["symbol"] = asset.ticker
    return row


def build_universe_snapshot(
    root: Path,
    *,
    client: ExchangeUniverseClient,
    stock_csv: Path | None = None,
    etf_csv: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> UniverseSnapshot:
    us_assets, us_sources = client.fetch_nasdaq()
    kr_assets, kr_sources = client.fetch_krx(stock_csv, etf_csv)
    unique = {
        f"{asset.peer_group.value}:{asset.ticker}": asset for asset in us_assets + kr_assets
    }
    assets = sorted(unique.values(), key=lambda item: (item.peer_group.value, item.ticker))
    if not assets:
        raise RuntimeError("거래소 종목 목록이 비어 있습니다.")
    rows = [_asset_row(asset) for asset in assets]
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode()
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    created = clock()
    version = f"universe-{created:%Y%m%d}-{digest}"
    universe_dir = root / "universes" / version
    universe_dir.mkdir(parents=True, exist_ok=True)
    path = universe_dir / "universe.csv"
    pl.DataFrame(rows).write_csv(path)
    counts: dict[str, int] = {}
    for asset in assets:
        key = asset.peer_group.value
        counts[key] = counts.get(key, 0) + 1
    sources = {**us_sources, **kr_sources, "fetched_at": created.isoformat()}
    manifest_path = universe_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"version": version, "sources": sources, "counts": counts, "sha256": digest},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return UniverseSnapshot(version, path, manifest_path, assets, sources, counts)


def supported_assets(assets: Iterable[UniverseAsset]) -> list[UniverseAsset]:
    return [asset for asset in assets if asset.is_supported]
