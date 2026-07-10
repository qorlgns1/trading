from functools import lru_cache

import numpy as np
import polars as pl

from quant_core.enums import PeerGroup

DEMO_DATA_VERSION = "demo-market-v1.0.0"
DEMO_START = np.datetime64("2016-01-04")
DEMO_END = np.datetime64("2026-06-30")
ASSETS_PER_GROUP = 40
SEED = 20260710

GROUP_PREFIX: dict[PeerGroup, str] = {
    PeerGroup.US_STOCK: "UST",
    PeerGroup.KR_KOSPI: "KSP",
    PeerGroup.KR_KOSDAQ: "KDQ",
    PeerGroup.US_EQUITY_ETF: "UET",
    PeerGroup.KR_DOMESTIC_EQUITY_ETF: "KDE",
    PeerGroup.KR_OVERSEAS_EQUITY_ETF: "KOE",
}

GROUP_LABEL: dict[PeerGroup, str] = {
    PeerGroup.US_STOCK: "가상 미국 주식",
    PeerGroup.KR_KOSPI: "가상 KOSPI 주식",
    PeerGroup.KR_KOSDAQ: "가상 KOSDAQ 주식",
    PeerGroup.US_EQUITY_ETF: "가상 미국 ETF",
    PeerGroup.KR_DOMESTIC_EQUITY_ETF: "가상 국내 ETF",
    PeerGroup.KR_OVERSEAS_EQUITY_ETF: "가상 해외형 ETF",
}


def _business_dates() -> np.ndarray:
    dates = np.arange(DEMO_START, DEMO_END + np.timedelta64(1, "D"), dtype="datetime64[D]")
    return dates[np.is_busday(dates)]


def _regime_returns(size: int, rng: np.random.Generator, group_index: int) -> np.ndarray:
    returns = rng.normal(0.00026 + group_index * 0.000005, 0.0085, size)
    crash_start = int(size * 0.40)
    returns[crash_start : crash_start + 26] += rng.normal(-0.007, 0.018, 26)
    recovery_start = crash_start + 26
    returns[recovery_start : recovery_start + 90] += 0.0022
    weak_start = int(size * 0.70)
    returns[weak_start : weak_start + 160] -= 0.0008
    return returns


@lru_cache(maxsize=1)
def generate_demo_market() -> pl.DataFrame:
    """Generate a deterministic, fictional market used by every public-demo surface."""
    rng = np.random.default_rng(SEED)
    dates = _business_dates()
    size = len(dates)
    fx_returns = rng.normal(0.00002, 0.0026, size)
    fx = np.clip(1_180 * np.exp(np.cumsum(fx_returns)), 950, 1_650)
    frames: list[pl.DataFrame] = []

    for group_index, peer_group in enumerate(PeerGroup):
        benchmark_returns = _regime_returns(size, rng, group_index)
        benchmark = 100 * np.exp(np.cumsum(benchmark_returns))
        is_us = peer_group in {PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF}
        is_etf = "ETF" in peer_group.value

        for asset_index in range(ASSETS_PER_GROUP):
            symbol = f"{GROUP_PREFIX[peer_group]}{asset_index + 1:03d}"
            asset_id = f"{peer_group.value}:{symbol}"
            quality = (asset_index - (ASSETS_PER_GROUP - 1) / 2) / ASSETS_PER_GROUP
            idio_vol = 0.006 + (asset_index % 7) * 0.0012
            idio = rng.normal(0.00003 + quality * 0.00016, idio_vol, size)
            if asset_index == 34:
                idio += rng.normal(0, 0.025, size)
            total_return = benchmark_returns * (0.72 + (asset_index % 5) * 0.06) + idio
            adjusted_close = (35 + asset_index * 2.7) * np.exp(np.cumsum(total_return))
            if not is_us:
                adjusted_close *= 500

            split_factor = np.ones(size)
            split_ratio = np.ones(size)
            if asset_index == 33:
                split_day = int(size * 0.58)
                split_factor[split_day:] = 0.5
                split_ratio[split_day] = 2.0

            dividend = np.zeros(size)
            cumulative_distribution = np.ones(size)
            distribution_yield = 0.003 if is_etf else 0.001
            distribution_days = range(315 + asset_index % 20, size, 63)
            multiplier = 1.0
            previous_raw = adjusted_close[0]
            for day in range(size):
                if day in distribution_days:
                    dividend[day] = previous_raw * distribution_yield
                    multiplier *= 1 + distribution_yield
                cumulative_distribution[day] = multiplier
                previous_raw = adjusted_close[day] / multiplier * split_factor[day]

            close = adjusted_close / cumulative_distribution * split_factor
            overnight = rng.normal(0, 0.0018 if is_etf else 0.0028, size)
            open_price = close * (1 + overnight)

            us_volume = 1_000_000 if is_etf else 650_000
            kr_volume = 900_000 if is_etf else 500_000
            base_volume = us_volume if is_us else kr_volume
            volume = base_volume * np.exp(rng.normal(0, 0.32, size))
            if asset_index == 38:
                volume *= 0.01
            volume = volume / split_factor

            is_suspended = np.zeros(size, dtype=bool)
            if asset_index == 37:
                is_suspended[-8:] = True
            is_supported = np.full(size, asset_index != 39, dtype=bool)
            delisted = np.zeros(size, dtype=bool)
            recovery_value = np.full(size, np.nan)
            if asset_index == 35:
                delist_day = int(size * 0.76)
                delisted[delist_day:] = True
                recovery_value[delist_day] = close[delist_day - 1] * 0.2
                close[delist_day:] = np.nan
                open_price[delist_day:] = np.nan
                adjusted_close[delist_day:] = np.nan
                volume[delist_day:] = 0
                is_suspended[delist_day:] = True
            if asset_index == 36:
                adjusted_close[-5:] = np.nan
                close[-5:] = np.nan
                open_price[-5:] = np.nan

            frames.append(
                pl.DataFrame(
                    {
                        "date": dates,
                        "asset_id": np.full(size, asset_id),
                        "symbol": np.full(size, symbol),
                        "name": np.full(size, f"{GROUP_LABEL[peer_group]} {asset_index + 1:02d}"),
                        "peer_group": np.full(size, peer_group.value),
                        "currency": np.full(size, "USD" if is_us else "KRW"),
                        "open": open_price,
                        "close": close,
                        "adjusted_close": adjusted_close,
                        "volume": volume,
                        "is_suspended": is_suspended,
                        "is_supported": is_supported,
                        "benchmark_close": benchmark,
                        "fx_krw_per_usd": fx,
                        "split_ratio": split_ratio,
                        "dividend": dividend,
                        "delisted": delisted,
                        "recovery_value": recovery_value,
                    },
                    nan_to_null=True,
                )
            )
    return pl.concat(frames, rechunk=True).sort(["date", "asset_id"])


def demo_universe() -> pl.DataFrame:
    return (
        generate_demo_market()
        .select("asset_id", "symbol", "name", "peer_group", "currency")
        .unique(subset=["asset_id"], maintain_order=True)
        .sort(["peer_group", "symbol"])
    )
