from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any, cast

from quant_core.enums import PeerGroup, Sleeve


@dataclass(frozen=True)
class Trade:
    date: date
    asset_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    notional: float
    cost: float
    currency: str
    reason: str


@dataclass(frozen=True)
class PositionSnapshot:
    asset_id: str
    symbol: str
    name: str
    peer_group: PeerGroup
    sleeve: Sleeve
    quantity: int
    price: float
    market_value_krw: float
    score: float


@dataclass
class BacktestResult:
    run_id: str
    data_version: str
    score_version: str
    portfolio_version: str
    config_hash: str
    started_on: date
    ended_on: date
    metrics: dict[str, float]
    equity_curve: list[dict[str, Any]]
    drawdown_curve: list[dict[str, Any]]
    trades: list[Trade]
    final_positions: list[PositionSnapshot]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        def jsonable(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, dict):
                return {key: jsonable(item) for key, item in value.items()}
            if isinstance(value, list):
                return [jsonable(item) for item in value]
            return value

        return cast(dict[str, Any], jsonable(asdict(self)))
