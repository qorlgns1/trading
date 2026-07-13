from datetime import date
from itertools import pairwise

import numpy as np
import polars as pl
from quant_core.config import PortfolioConfig
from quant_core.enums import (
    DataStatus,
    PeerGroup,
    PositionSizing,
    ReplacementPolicy,
    Sleeve,
)
from quant_core.market_portfolio import (
    PortfolioPosition,
    PreparedMarketReplay,
    plan_weekly_orders,
    run_market_replay,
    simulate_prepared_replay,
    slice_prepared_replay,
)


def _signal(
    asset_id: str,
    group: PeerGroup,
    score: float,
    *,
    data_status: DataStatus = DataStatus.READY,
) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "symbol": asset_id,
        "name": asset_id,
        "peer_group": group.value,
        "currency": "USD" if group.value.startswith("US_") else "KRW",
        "trend_score": score,
        "relative_momentum": 0.2,
        "vol60": 0.2,
        "data_eligible": True,
        "candidate_eligible": True,
        "benchmark_close": 110.0,
        "benchmark_sma200": 100.0,
        "data_status": data_status.value,
    }


def _position(asset_id: str, group: PeerGroup, sleeve: Sleeve) -> PortfolioPosition:
    return PortfolioPosition(
        asset_id=asset_id,
        symbol=asset_id,
        name=asset_id,
        peer_group=group,
        sleeve=sleeve,
        currency="USD" if group.value.startswith("US_") else "KRW",
        quantity=10,
        last_score=80,
    )


def test_review_holds_bad_data_and_does_not_replace_valid_positions() -> None:
    positions = {
        "A": _position("A", PeerGroup.US_STOCK, Sleeve.US_STOCK),
        "B": _position("B", PeerGroup.US_STOCK, Sleeve.US_STOCK),
        "C": _position("C", PeerGroup.US_STOCK, Sleeve.US_STOCK),
    }
    rows = [
        _signal("A", PeerGroup.US_STOCK, 90),
        _signal("B", PeerGroup.US_STOCK, 20, data_status=DataStatus.INVALID_DATA),
        _signal("C", PeerGroup.US_STOCK, 80),
        _signal("HIGHER", PeerGroup.US_STOCK, 99),
    ]

    plan = plan_weekly_orders(rows, positions)

    assert plan.orders == []
    assert plan.review_required_assets == {"B"}


def test_review_sells_only_valid_position_below_exit_threshold() -> None:
    positions = {
        "EXIT": _position("EXIT", PeerGroup.KR_KOSPI, Sleeve.KR_STOCK),
        "HOLD": _position("HOLD", PeerGroup.KR_KOSPI, Sleeve.KR_STOCK),
    }
    rows = [
        _signal("EXIT", PeerGroup.KR_KOSPI, 59),
        _signal("HOLD", PeerGroup.KR_KOSPI, 61),
        _signal("NEW", PeerGroup.KR_KOSPI, 99),
    ]

    plan = plan_weekly_orders(rows, positions)

    assert [(order.side, order.asset_id) for order in plan.orders] == [("SELL", "EXIT")]


def test_top_score_policy_replaces_only_when_configured_gap_is_met() -> None:
    positions = {
        "HELD": _position("HELD", PeerGroup.US_STOCK, Sleeve.US_STOCK),
        "B": _position("B", PeerGroup.US_STOCK, Sleeve.US_STOCK),
        "C": _position("C", PeerGroup.US_STOCK, Sleeve.US_STOCK),
    }
    rows = [
        _signal("HELD", PeerGroup.US_STOCK, 70),
        _signal("B", PeerGroup.US_STOCK, 75),
        _signal("C", PeerGroup.US_STOCK, 80),
        _signal("NEW", PeerGroup.US_STOCK, 76),
    ]
    config = PortfolioConfig(
        replacement_policy=ReplacementPolicy.TOP_SCORE_REBALANCE,
        replacement_score_gap=5,
    )

    plan = plan_weekly_orders(rows, positions, config=config)

    assert [(order.side, order.asset_id) for order in plan.orders] == [
        ("SELL", "HELD"),
        ("BUY", "NEW"),
    ]


def test_peer_specific_threshold_overrides_global_threshold() -> None:
    slots = {group: 0 for group in PeerGroup}
    slots[PeerGroup.KR_KOSPI] = 1
    weights = {sleeve: 0 for sleeve in Sleeve}
    weights[Sleeve.KR_STOCK] = 10_000
    config = PortfolioConfig(
        sleeve_weights_bps=weights,
        peer_group_slots=slots,
        entry_score=65,
        exit_score=60,
        peer_entry_scores={PeerGroup.KR_KOSPI: 80},
        peer_exit_scores={PeerGroup.KR_KOSPI: 70},
    )
    rows = [
        _signal("LOW", PeerGroup.KR_KOSPI, 79),
        _signal("HIGH", PeerGroup.KR_KOSPI, 81),
    ]

    plan = plan_weekly_orders(rows, {}, config=config)

    assert [(order.side, order.asset_id) for order in plan.orders] == [("BUY", "HIGH")]


def test_independent_slice_renormalizes_benchmarks_at_new_start() -> None:
    dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    empty = np.empty((3, 0))
    prepared = PreparedMarketReplay(
        signals_by_review={},
        review_dates=[],
        dates=dates,
        candidate_ids=[],
        metadata={},
        asset_index={},
        open_prices=empty,
        close_prices=empty,
        split_ratios=empty,
        dividends=empty,
        recovery_values=empty,
        fx_by_date={current: 1_350.0 for current in dates},
        benchmark_indexes={sleeve: np.asarray([1.0, 1.2, 1.5]) for sleeve in Sleeve},
    )

    sliced = slice_prepared_replay(prepared, start=dates[1], end=dates[2])

    assert sliced.benchmark_indexes[Sleeve.US_STOCK].tolist() == [1.0, 1.25]


def test_inverse_volatility_sizes_lower_volatility_entry_larger() -> None:
    dates = [date(2025, 1, 3), date(2025, 1, 6)]
    low = {**_signal("LOW", PeerGroup.US_STOCK, 80), "vol60": 0.10}
    high = {**_signal("HIGH", PeerGroup.US_STOCK, 80), "vol60": 0.40}
    metadata = {
        asset_id: {
            "asset_id": asset_id,
            "symbol": asset_id,
            "name": asset_id,
            "peer_group": PeerGroup.US_STOCK.value,
            "currency": "USD",
        }
        for asset_id in ("LOW", "HIGH")
    }
    ones = np.ones((2, 2))
    zeros = np.zeros((2, 2))
    slots = {group: 0 for group in PeerGroup}
    slots[PeerGroup.US_STOCK] = 2
    weights = {sleeve: 0 for sleeve in Sleeve}
    weights[Sleeve.US_STOCK] = 10_000
    config = PortfolioConfig(
        sleeve_weights_bps=weights,
        peer_group_slots=slots,
        position_sizing=PositionSizing.INVERSE_VOLATILITY,
    )
    prepared = PreparedMarketReplay(
        signals_by_review={dates[0]: [low, high]},
        review_dates=[dates[0]],
        dates=dates,
        candidate_ids=["LOW", "HIGH"],
        metadata=metadata,
        asset_index={"LOW": 0, "HIGH": 1},
        open_prices=ones * 100,
        close_prices=ones * 100,
        split_ratios=ones,
        dividends=zeros,
        recovery_values=np.full((2, 2), np.nan),
        fx_by_date={current: 1_350.0 for current in dates},
        benchmark_indexes={sleeve: np.ones(2) for sleeve in Sleeve},
    )

    run = simulate_prepared_replay(
        prepared,
        data_version="fixture-v2",
        score_version="trend-score-v2.0.0",
        portfolio_config=config,
        prices_are_split_adjusted=True,
    )

    quantities = {trade.asset_id: trade.quantity for trade in run.result.trades}
    assert quantities["LOW"] > quantities["HIGH"] * 3


def test_replay_uses_next_market_open_after_common_weekly_review() -> None:
    review_date = date(2025, 7, 4)
    groups = list(PeerGroup)
    signals = pl.DataFrame(
        [
            {
                **_signal(f"ASSET-{group.value}", group, 75),
                "review_date": review_date,
            }
            for group in groups
        ]
    )
    market_dates = {
        "US": [date(2025, 7, 3), date(2025, 7, 7), date(2025, 7, 8)],
        "KR": [date(2025, 7, 4), date(2025, 7, 7), date(2025, 7, 8)],
    }
    bars: list[dict[str, object]] = []
    reference: list[dict[str, object]] = []
    for group in groups:
        market = "US" if group.value.startswith("US_") else "KR"
        currency = "USD" if market == "US" else "KRW"
        for index, current in enumerate(market_dates[market]):
            bars.append(
                {
                    "date": current,
                    "asset_id": f"ASSET-{group.value}",
                    "symbol": f"ASSET-{group.value}",
                    "name": group.value,
                    "peer_group": group.value,
                    "currency": currency,
                    "open": 100.0 + index,
                    "close": 101.0 + index,
                    "split_ratio": (
                        5.0 if group is PeerGroup.US_STOCK and current == date(2025, 7, 8) else 1.0
                    ),
                    "dividend": 0.0,
                    "recovery_value": None,
                }
            )
            reference.append(
                {
                    "date": current,
                    "peer_group": group.value,
                    "benchmark_close": 100.0 + index,
                    "fx_krw_per_usd": 1_350.0,
                }
            )

    progress: list[tuple[int, int]] = []
    result = run_market_replay(
        pl.DataFrame(bars),
        signals,
        pl.DataFrame(reference),
        data_version="fixture-v1",
        score_version="trend-score-v1.0.0",
        prices_are_split_adjusted=True,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    entries = [trade for trade in result.trades if trade.side == "BUY"]
    assert len(entries) == len(groups)
    assert {trade.date for trade in entries} == {date(2025, 7, 7)}
    assert all(trade.date > review_date for trade in entries)
    assert progress[-1][0] == progress[-1][1]
    values = [point["portfolio"] for point in result.equity_curve]
    daily_returns = [current / previous - 1 for previous, current in pairwise(values)]
    assert max(daily_returns) < 0.1


def test_close_stop_and_slippage_fill_at_the_following_market_open() -> None:
    review_date = date(2025, 1, 3)
    dates = [review_date, date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8)]
    signals = pl.DataFrame(
        [
            {
                **_signal(f"STOP-{group.value}", group, 80),
                "review_date": review_date,
            }
            for group in PeerGroup
        ]
    )
    bars: list[dict[str, object]] = []
    reference: list[dict[str, object]] = []
    for group in PeerGroup:
        currency = "USD" if group.value.startswith("US_") else "KRW"
        for current in dates:
            open_price = 79.0 if current == dates[-1] else 80.0 if current == dates[2] else 100.0
            close_price = 80.0 if current == dates[2] else 79.0 if current == dates[-1] else 100.0
            bars.append(
                {
                    "date": current,
                    "asset_id": f"STOP-{group.value}",
                    "symbol": f"STOP-{group.value}",
                    "name": group.value,
                    "peer_group": group.value,
                    "currency": currency,
                    "open": open_price,
                    "close": close_price,
                    "split_ratio": 1.0,
                    "dividend": 0.0,
                    "recovery_value": None,
                }
            )
            reference.append(
                {
                    "date": current,
                    "peer_group": group.value,
                    "benchmark_close": 100.0,
                    "fx_krw_per_usd": 1_350.0,
                }
            )

    result = run_market_replay(
        pl.DataFrame(bars),
        signals,
        pl.DataFrame(reference),
        data_version="fixture-v2",
        score_version="trend-score-v2.0.0",
        portfolio_config=PortfolioConfig(fixed_stop_loss=0.10, slippage_bps=100),
        prices_are_split_adjusted=True,
    )

    us_trades = [trade for trade in result.trades if trade.asset_id == "STOP-US_STOCK"]
    assert [(trade.side, trade.date, trade.reason) for trade in us_trades] == [
        ("BUY", date(2025, 1, 6), "WEEKLY_ENTRY"),
        ("SELL", date(2025, 1, 8), "FIXED_STOP"),
    ]
    assert us_trades[0].price == 101.0
    assert us_trades[1].price == 78.21
