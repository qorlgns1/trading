from datetime import date
from itertools import pairwise

import polars as pl
from quant_core.enums import DataStatus, PeerGroup, Sleeve
from quant_core.market_portfolio import (
    PortfolioPosition,
    plan_weekly_orders,
    run_market_replay,
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
