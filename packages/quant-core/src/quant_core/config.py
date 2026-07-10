from dataclasses import dataclass, field

from quant_core.enums import PeerGroup, Sleeve

TREND_SCORE_VERSION = "trend-score-v1.0.0"
PORTFOLIO_VERSION = "portfolio-v1.0.0"
COST_MODEL_VERSION = "cost-v1.0.0"


@dataclass(frozen=True)
class TrendScoreConfig:
    version: str = TREND_SCORE_VERSION
    candidate_threshold: float = 65.0
    strong_candidate_threshold: float = 80.0
    retention_threshold: float = 60.0
    minimum_peer_count: int = 30
    order_to_adv_limit: float = 0.001
    minimum_adv: dict[PeerGroup, float] = field(
        default_factory=lambda: {
            PeerGroup.US_STOCK: 5_000_000.0,
            PeerGroup.US_EQUITY_ETF: 10_000_000.0,
            PeerGroup.KR_KOSPI: 5_000_000_000.0,
            PeerGroup.KR_KOSDAQ: 3_000_000_000.0,
            PeerGroup.KR_DOMESTIC_EQUITY_ETF: 2_000_000_000.0,
            PeerGroup.KR_OVERSEAS_EQUITY_ETF: 2_000_000_000.0,
        }
    )
    planned_order_value: dict[PeerGroup, float] = field(
        default_factory=lambda: {
            PeerGroup.US_STOCK: 3_200.0,
            PeerGroup.US_EQUITY_ETF: 3_200.0,
            PeerGroup.KR_KOSPI: 4_200_000.0,
            PeerGroup.KR_KOSDAQ: 4_200_000.0,
            PeerGroup.KR_DOMESTIC_EQUITY_ETF: 4_200_000.0,
            PeerGroup.KR_OVERSEAS_EQUITY_ETF: 4_200_000.0,
        }
    )


@dataclass(frozen=True)
class PortfolioConfig:
    version: str = PORTFOLIO_VERSION
    initial_capital_krw: float = 50_000_000.0
    sleeve_weights_bps: dict[Sleeve, int] = field(
        default_factory=lambda: {
            Sleeve.US_STOCK: 2500,
            Sleeve.KR_STOCK: 2500,
            Sleeve.US_ETF: 2500,
            Sleeve.KR_ETF: 2500,
        }
    )
    us_trade_cost: float = 0.0015
    kr_trade_cost: float = 0.0025
    initial_fx_cost: float = 0.0025
    entry_score: float = 65.0
    exit_score: float = 60.0

    def __post_init__(self) -> None:
        if set(self.sleeve_weights_bps) != set(Sleeve):
            raise ValueError("네 가지 자산군 비중이 모두 필요합니다.")
        if sum(self.sleeve_weights_bps.values()) != 10_000:
            raise ValueError("자산군 비중 합계는 10,000bp여야 합니다.")
        if any(weight < 0 for weight in self.sleeve_weights_bps.values()):
            raise ValueError("자산군 비중은 음수일 수 없습니다.")


PEER_GROUP_SLEEVE: dict[PeerGroup, Sleeve] = {
    PeerGroup.US_STOCK: Sleeve.US_STOCK,
    PeerGroup.KR_KOSPI: Sleeve.KR_STOCK,
    PeerGroup.KR_KOSDAQ: Sleeve.KR_STOCK,
    PeerGroup.US_EQUITY_ETF: Sleeve.US_ETF,
    PeerGroup.KR_DOMESTIC_EQUITY_ETF: Sleeve.KR_ETF,
    PeerGroup.KR_OVERSEAS_EQUITY_ETF: Sleeve.KR_ETF,
}

PEER_GROUP_SLOTS: dict[PeerGroup, int] = {
    PeerGroup.US_STOCK: 3,
    PeerGroup.KR_KOSPI: 2,
    PeerGroup.KR_KOSDAQ: 1,
    PeerGroup.US_EQUITY_ETF: 3,
    PeerGroup.KR_DOMESTIC_EQUITY_ETF: 2,
    PeerGroup.KR_OVERSEAS_EQUITY_ETF: 1,
}
