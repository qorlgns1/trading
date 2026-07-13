from dataclasses import dataclass, field

from quant_core.enums import (
    MarketGateMode,
    PeerGroup,
    PositionSizing,
    ReplacementPolicy,
    ReviewFrequency,
    Sleeve,
)

TREND_SCORE_VERSION = "trend-score-v1.0.0"
PORTFOLIO_VERSION = "portfolio-v1.0.0"
COST_MODEL_VERSION = "cost-v1.0.0"
REPLAY_STRATEGY_VERSION = "replay-strategy-v2.0.0"
REPLAY_PORTFOLIO_VERSION = "portfolio-v2.0.0"
REPLAY_SCORE_VERSION = "trend-score-v2.0.0"

SCORE_COMPONENTS = (
    "long_term_trend",
    "absolute_momentum",
    "relative_strength",
    "high_proximity",
    "volatility_stability",
    "trading_activity",
)


def default_score_weights_bps() -> dict[str, int]:
    return {
        "long_term_trend": 3000,
        "absolute_momentum": 2500,
        "relative_strength": 2000,
        "high_proximity": 1000,
        "volatility_stability": 1000,
        "trading_activity": 500,
    }


@dataclass(frozen=True)
class TrendScoreConfig:
    version: str = TREND_SCORE_VERSION
    candidate_threshold: float = 65.0
    strong_candidate_threshold: float = 80.0
    retention_threshold: float = 60.0
    minimum_peer_count: int = 30
    order_to_adv_limit: float = 0.001
    component_weights_bps: dict[str, int] = field(default_factory=default_score_weights_bps)
    require_above_sma200: bool = True
    require_positive_six_month: bool = True
    require_absolute_liquidity: bool = True
    require_order_size_liquidity: bool = True
    minimum_adv_multiplier: float = 1.0
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

    def __post_init__(self) -> None:
        if set(self.component_weights_bps) != set(SCORE_COMPONENTS):
            raise ValueError("여섯 가지 점수 구성요소 가중치가 모두 필요합니다.")
        if sum(self.component_weights_bps.values()) != 10_000:
            raise ValueError("점수 구성요소 가중치 합계는 10,000bp여야 합니다.")
        if any(weight < 0 for weight in self.component_weights_bps.values()):
            raise ValueError("점수 구성요소 가중치는 음수일 수 없습니다.")
        if not 0.5 <= self.minimum_adv_multiplier <= 2.0:
            raise ValueError("최소 거래대금 배수는 0.5~2.0이어야 합니다.")


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
    peer_entry_scores: dict[PeerGroup, float] = field(default_factory=dict)
    peer_exit_scores: dict[PeerGroup, float] = field(default_factory=dict)
    peer_group_slots: dict[PeerGroup, int] = field(default_factory=lambda: dict(PEER_GROUP_SLOTS))
    review_frequency: ReviewFrequency = ReviewFrequency.WEEKLY
    execution_delay_sessions: int = 1
    position_sizing: PositionSizing = PositionSizing.EQUAL_SLOT
    replacement_policy: ReplacementPolicy = ReplacementPolicy.FILL_VACANCIES
    replacement_score_gap: float = 5.0
    market_gate_mode: MarketGateMode = MarketGateMode.BLOCK_NEW_ENTRIES_BELOW_SMA200
    fixed_stop_loss: float | None = None
    trailing_stop_loss: float | None = None
    us_buy_cost: float | None = None
    us_sell_cost: float | None = None
    kr_buy_cost: float | None = None
    kr_sell_cost: float | None = None
    slippage_bps: float = 0.0

    def __post_init__(self) -> None:
        if set(self.sleeve_weights_bps) != set(Sleeve):
            raise ValueError("네 가지 자산군 비중이 모두 필요합니다.")
        if sum(self.sleeve_weights_bps.values()) != 10_000:
            raise ValueError("자산군 비중 합계는 10,000bp여야 합니다.")
        if any(weight < 0 for weight in self.sleeve_weights_bps.values()):
            raise ValueError("자산군 비중은 음수일 수 없습니다.")
        if not 50 <= self.entry_score <= 100:
            raise ValueError("진입 점수는 50~100이어야 합니다.")
        if not 50 <= self.exit_score <= 95 or self.exit_score > self.entry_score - 5:
            raise ValueError("해제 점수는 50~95이고 진입 점수보다 5점 이상 낮아야 합니다.")
        if set(self.peer_entry_scores) != set(self.peer_exit_scores):
            raise ValueError("비교군별 진입·해제 점수는 같은 비교군에 함께 설정해야 합니다.")
        for group, entry in self.peer_entry_scores.items():
            exit_score = self.peer_exit_scores[group]
            if not 50 <= entry <= 100 or not 50 <= exit_score <= 95:
                raise ValueError("비교군별 점수는 허용 범위 안이어야 합니다.")
            if exit_score > entry - 5:
                raise ValueError("비교군별 해제 점수는 진입 점수보다 5점 이상 낮아야 합니다.")
        if set(self.peer_group_slots) != set(PeerGroup):
            raise ValueError("여섯 비교군의 슬롯 수가 모두 필요합니다.")
        if any(slot < 0 or slot > 10 for slot in self.peer_group_slots.values()):
            raise ValueError("비교군별 슬롯은 0~10이어야 합니다.")
        if not 1 <= sum(self.peer_group_slots.values()) <= 30:
            raise ValueError("전체 보유 슬롯은 1~30이어야 합니다.")
        for sleeve in Sleeve:
            sleeve_slots = sum(
                self.peer_group_slots[group]
                for group, mapped_sleeve in PEER_GROUP_SLEEVE.items()
                if mapped_sleeve is sleeve
            )
            if self.sleeve_weights_bps[sleeve] > 0 and sleeve_slots == 0:
                raise ValueError("비중이 있는 자산군에는 한 개 이상의 비교군 슬롯이 필요합니다.")
        if not 1 <= self.execution_delay_sessions <= 5:
            raise ValueError("체결 지연은 1~5거래일이어야 합니다.")
        if not 0 <= self.replacement_score_gap <= 20:
            raise ValueError("교체 점수 차이는 0~20이어야 합니다.")
        for stop in (self.fixed_stop_loss, self.trailing_stop_loss):
            if stop is not None and not 0.05 <= stop <= 0.30:
                raise ValueError("손절 비율은 5~30%여야 합니다.")
        if not 0 <= self.slippage_bps <= 200:
            raise ValueError("슬리피지는 0~200bp여야 합니다.")
        for cost in (
            self.us_buy_cost,
            self.us_sell_cost,
            self.kr_buy_cost,
            self.kr_sell_cost,
        ):
            # User-facing strategy inputs are capped at 1%. The engine permits
            # 2% so the required exact 2x cost stress can be replayed.
            if cost is not None and not 0 <= cost <= 0.02:
                raise ValueError("거래 비용률은 0~2%여야 합니다.")

    def entry_score_for(self, group: PeerGroup) -> float:
        return self.peer_entry_scores.get(group, self.entry_score)

    def exit_score_for(self, group: PeerGroup) -> float:
        return self.peer_exit_scores.get(group, self.exit_score)

    def trade_cost(self, currency: str, side: str) -> float:
        if currency == "USD":
            override = self.us_buy_cost if side == "BUY" else self.us_sell_cost
            return self.us_trade_cost if override is None else override
        override = self.kr_buy_cost if side == "BUY" else self.kr_sell_cost
        return self.kr_trade_cost if override is None else override


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
