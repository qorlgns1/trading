import logging
import math
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
import yfinance as yf

from quant_core.enums import DataStatus, PeerGroup

YFINANCE_PROVIDER_VERSION = "yfinance-1.5.1-repair-v1"


class _YFinanceRateLimitHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage().lower()
        if (
            "yfratelimiterror" in message
            or "rate limit" in message
            or "too many requests" in message
            or "http 429" in message
        ):
            _YFINANCE_RATE_LIMITED.set()


_YFINANCE_RATE_LIMITED = threading.Event()
_YFINANCE_RATE_LIMIT_HANDLER = _YFinanceRateLimitHandler()
_YFINANCE_HANDLER_LOCK = threading.Lock()


def _ensure_yfinance_rate_limit_handler() -> None:
    logger = logging.getLogger("yfinance")
    with _YFINANCE_HANDLER_LOCK:
        if _YFINANCE_RATE_LIMIT_HANDLER not in logger.handlers:
            logger.addHandler(_YFINANCE_RATE_LIMIT_HANDLER)


@dataclass(frozen=True)
class UniverseAsset:
    ticker: str
    name: str
    peer_group: PeerGroup
    currency: str
    benchmark_ticker: str
    is_supported: bool = True
    data_status: DataStatus = DataStatus.READY
    status_reason: str | None = None
    exchange: str | None = None
    source: str | None = None


DEFAULT_BENCHMARK: dict[PeerGroup, str] = {
    PeerGroup.US_STOCK: "^GSPC",
    PeerGroup.US_EQUITY_ETF: "^GSPC",
    PeerGroup.KR_KOSPI: "^KS11",
    PeerGroup.KR_KOSDAQ: "^KQ11",
    PeerGroup.KR_DOMESTIC_EQUITY_ETF: "^KS11",
    PeerGroup.KR_OVERSEAS_EQUITY_ETF: "ACWI",
}


def _csv_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_universe_csv(path: Path) -> list[UniverseAsset]:
    frame = pl.read_csv(path)
    required = {"ticker", "name", "peer_group", "currency"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"유니버스 CSV 필수 열이 없습니다: {sorted(missing)}")
    assets: list[UniverseAsset] = []
    for row in frame.iter_rows(named=True):
        peer_group = PeerGroup(row["peer_group"])
        benchmark = row.get("benchmark_ticker") or DEFAULT_BENCHMARK[peer_group]
        assets.append(
            UniverseAsset(
                ticker=row["ticker"],
                name=row["name"],
                peer_group=peer_group,
                currency=row["currency"],
                benchmark_ticker=benchmark,
                is_supported=_csv_bool(row.get("is_supported")),
                data_status=DataStatus(row.get("data_status") or DataStatus.READY.value),
                status_reason=row.get("status_reason"),
                exchange=row.get("exchange"),
                source=row.get("source"),
            )
        )
    return assets


class YFinanceProvider:
    """Personal-research adapter. It is deliberately disabled in public-demo mode."""

    def __init__(self, app_mode: str) -> None:
        if app_mode != "local_research":
            raise RuntimeError("yfinance 어댑터는 local_research 모드에서만 사용할 수 있습니다.")
        _ensure_yfinance_rate_limit_handler()
        self._serial_mode = threading.Event()

    def consume_rate_limit_signal(self) -> bool:
        detected = _YFINANCE_RATE_LIMITED.is_set()
        if detected:
            _YFINANCE_RATE_LIMITED.clear()
        return detected

    def enable_serial_mode(self) -> None:
        self._serial_mode.set()

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        if not assets:
            raise ValueError("조회할 종목이 없습니다.")
        asset_tickers = [asset.ticker for asset in assets]
        benchmark_tickers = {asset.benchmark_ticker for asset in assets}
        tickers = sorted(set(asset_tickers) | benchmark_tickers | {"KRW=X"})
        raw = yf.download(
            tickers=tickers,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
            actions=True,
            repair=True,
            group_by="ticker",
            progress=False,
            threads=not self._serial_mode.is_set(),
        )
        if raw is None or raw.empty:
            return pl.DataFrame()
        metadata = {asset.ticker: asset for asset in assets}

        def ticker_frame(ticker: str):  # type: ignore[no-untyped-def]
            if len(tickers) == 1:
                return raw
            try:
                return raw[ticker]
            except KeyError:
                return None

        def finite(value: object) -> float | None:
            try:
                number = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None

        def repaired(value: object) -> bool:
            if value is None:
                return False
            if isinstance(value, bool):
                return value
            try:
                number = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return str(value).strip().lower() in {"1", "true", "yes"}
            return math.isfinite(number) and number != 0

        aligned_benchmarks = {}
        for ticker in benchmark_tickers:
            benchmark_frame = ticker_frame(ticker)
            if benchmark_frame is None or "Close" not in benchmark_frame:
                raise RuntimeError(f"벤치마크 데이터를 받지 못했습니다: {ticker}")
            aligned_benchmarks[ticker] = benchmark_frame["Close"].reindex(raw.index).ffill()
        fx_frame = ticker_frame("KRW=X")
        if fx_frame is None or "Close" not in fx_frame:
            raise RuntimeError("원달러 환율 데이터를 받지 못했습니다.")
        aligned_fx = fx_frame["Close"].reindex(raw.index).ffill()
        records: list[dict[str, object]] = []
        for ticker in asset_tickers:
            asset_frame = ticker_frame(ticker)
            if asset_frame is None or "Close" not in asset_frame:
                continue
            asset = metadata[ticker]
            benchmark = aligned_benchmarks[asset.benchmark_ticker]
            for timestamp, row in asset_frame.iterrows():
                close = finite(row.get("Close"))
                adjusted = finite(row.get("Adj Close")) or close
                benchmark_close = finite(benchmark.loc[timestamp])
                fx = finite(aligned_fx.loc[timestamp])
                if close is None or adjusted is None or benchmark_close is None or fx is None:
                    continue
                split_value = finite(row.get("Stock Splits")) or 0.0
                open_price = finite(row.get("Open")) or close
                volume = finite(row.get("Volume")) or 0.0
                dividend = finite(row.get("Dividends")) or 0.0
                records.append(
                    {
                        "date": timestamp.date(),
                        "asset_id": f"{asset.peer_group.value}:{ticker}",
                        "symbol": ticker,
                        "name": asset.name,
                        "peer_group": asset.peer_group.value,
                        "currency": asset.currency,
                        "open": open_price,
                        "close": close,
                        "adjusted_close": adjusted,
                        "volume": volume,
                        "split_ratio": split_value if split_value > 0 else 1.0,
                        "dividend": dividend,
                        "is_suspended": False,
                        "is_supported": asset.is_supported,
                        "benchmark_close": benchmark_close,
                        "fx_krw_per_usd": fx,
                        "delisted": False,
                        "recovery_value": None,
                        "provider_repaired": repaired(row.get("Repaired?")),
                    }
                )
        return pl.DataFrame(records) if records else pl.DataFrame()
