from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from quant_core.enums import (
    CandidateEventType,
    CandidateState,
    DataSource,
    DataStatus,
    ExperimentObjective,
    ExperimentRunRole,
    ForwardAccountType,
    MarketGateMode,
    PaperAccountStatus,
    PeerGroup,
    PositionSizing,
    QualityResolution,
    QualitySeverity,
    QualityStatus,
    ReplacementPolicy,
    ResearchCollectionMode,
    ReviewFrequency,
    RunStatus,
    Sleeve,
    SnapshotState,
    SyncTrigger,
    UniverseMode,
)


class SleeveWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    us_stock: int = Field(default=2500, ge=0, le=10_000)
    kr_stock: int = Field(default=2500, ge=0, le=10_000)
    us_etf: int = Field(default=2500, ge=0, le=10_000)
    kr_etf: int = Field(default=2500, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_total(self) -> "SleeveWeights":
        if self.us_stock + self.kr_stock + self.us_etf + self.kr_etf != 10_000:
            raise ValueError("네 자산군 비중 합계는 10,000bp여야 합니다.")
        return self

    def as_domain(self) -> dict[Sleeve, int]:
        return {
            Sleeve.US_STOCK: self.us_stock,
            Sleeve.KR_STOCK: self.kr_stock,
            Sleeve.US_ETF: self.us_etf,
            Sleeve.KR_ETF: self.kr_etf,
        }


class BacktestCreate(BaseModel):
    sleeve_weights_bps: SleeveWeights = Field(default_factory=SleeveWeights)


class BacktestAccepted(BaseModel):
    run_id: str
    status: RunStatus
    cached: bool = False


class ArtifactResponse(BaseModel):
    name: str
    content_type: str
    size_bytes: int
    download_url: str


class BacktestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    config: dict[str, Any]
    result: dict[str, Any] | None = None
    error_message: str | None = None


class FrozenReplayModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ReplayScoreWeights(FrozenReplayModel):
    long_term_trend: int = Field(default=3000, ge=0, le=10_000)
    absolute_momentum: int = Field(default=2500, ge=0, le=10_000)
    relative_strength: int = Field(default=2000, ge=0, le=10_000)
    high_proximity: int = Field(default=1000, ge=0, le=10_000)
    volatility_stability: int = Field(default=1000, ge=0, le=10_000)
    trading_activity: int = Field(default=500, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_total(self) -> "ReplayScoreWeights":
        if sum(self.model_dump().values()) != 10_000:
            raise ValueError("점수 구성요소 가중치 합계는 10,000bp여야 합니다.")
        return self


class ReplayPeerThreshold(FrozenReplayModel):
    entry_score: int = Field(ge=50, le=100, multiple_of=5)
    exit_score: int = Field(ge=50, le=95, multiple_of=5)

    @model_validator(mode="after")
    def validate_gap(self) -> "ReplayPeerThreshold":
        if self.exit_score > self.entry_score - 5:
            raise ValueError("해제 점수는 진입 점수보다 5점 이상 낮아야 합니다.")
        return self


class ReplaySignalConfig(FrozenReplayModel):
    entry_score: int = Field(default=65, ge=50, le=100, multiple_of=5)
    exit_score: int = Field(default=60, ge=50, le=95, multiple_of=5)
    peer_overrides: dict[PeerGroup, ReplayPeerThreshold] = Field(default_factory=dict)
    component_weights_bps: ReplayScoreWeights = Field(default_factory=ReplayScoreWeights)
    require_above_sma200: bool = True
    require_positive_six_month: bool = True
    require_absolute_liquidity: bool = True
    require_order_size_liquidity: bool = True
    minimum_adv_multiplier: float = Field(default=1.0, ge=0.5, le=2.0)
    market_gate_mode: MarketGateMode = MarketGateMode.BLOCK_NEW_ENTRIES_BELOW_SMA200

    @model_validator(mode="after")
    def validate_gap(self) -> "ReplaySignalConfig":
        if self.exit_score > self.entry_score - 5:
            raise ValueError("해제 점수는 진입 점수보다 5점 이상 낮아야 합니다.")
        return self


class ReplayPeerSlots(FrozenReplayModel):
    us_stock: int = Field(default=3, ge=0, le=10)
    us_equity_etf: int = Field(default=3, ge=0, le=10)
    kr_kospi: int = Field(default=2, ge=0, le=10)
    kr_kosdaq: int = Field(default=1, ge=0, le=10)
    kr_domestic_equity_etf: int = Field(default=2, ge=0, le=10)
    kr_overseas_equity_etf: int = Field(default=1, ge=0, le=10)

    @model_validator(mode="after")
    def validate_total(self) -> "ReplayPeerSlots":
        if not 1 <= sum(self.model_dump().values()) <= 30:
            raise ValueError("전체 보유 슬롯은 1~30이어야 합니다.")
        return self


class ReplayPortfolioConfig(FrozenReplayModel):
    initial_capital_krw: int = Field(default=50_000_000, ge=1_000_000, le=1_000_000_000)
    sleeve_weights_bps: SleeveWeights = Field(default_factory=SleeveWeights)
    peer_group_slots: ReplayPeerSlots = Field(default_factory=ReplayPeerSlots)
    position_sizing: PositionSizing = PositionSizing.EQUAL_SLOT
    replacement_policy: ReplacementPolicy = ReplacementPolicy.FILL_VACANCIES
    replacement_score_gap: int = Field(default=5, ge=0, le=20)


class ReplayRiskConfig(FrozenReplayModel):
    fixed_stop_loss: float | None = Field(default=None, ge=0.05, le=0.30)
    trailing_stop_loss: float | None = Field(default=None, ge=0.05, le=0.30)


class ReplayExecutionConfig(FrozenReplayModel):
    review_frequency: ReviewFrequency = ReviewFrequency.WEEKLY
    execution_delay_sessions: int = Field(default=1, ge=1, le=5)
    us_buy_cost: float = Field(default=0.0015, ge=0, le=0.01)
    us_sell_cost: float = Field(default=0.0015, ge=0, le=0.01)
    kr_buy_cost: float = Field(default=0.0025, ge=0, le=0.01)
    kr_sell_cost: float = Field(default=0.0025, ge=0, le=0.01)
    initial_fx_cost: float = Field(default=0.0025, ge=0, le=0.01)
    slippage_bps: float = Field(default=0, ge=0, le=200)


class ReplayDataConfig(FrozenReplayModel):
    peer_groups: list[PeerGroup] = Field(default_factory=lambda: list(PeerGroup), min_length=1)
    start_date: date
    split_date: date
    end_date: date
    universe_mode: UniverseMode = UniverseMode.CURRENT_LISTED

    @model_validator(mode="after")
    def validate_dates(self) -> "ReplayDataConfig":
        if len(set(self.peer_groups)) != len(self.peer_groups):
            raise ValueError("활성 비교군은 중복될 수 없습니다.")
        if not self.start_date < self.split_date < self.end_date:
            raise ValueError("시작일, 분할일, 종료일 순서가 올바르지 않습니다.")
        return self


class ReplayValidationConfig(FrozenReplayModel):
    walk_forward_train_years: int = Field(default=3, ge=2, le=5)
    walk_forward_test_years: int = Field(default=1, ge=1, le=2)
    walk_forward_step_years: int = Field(default=1, ge=1, le=2)


class ReplayStrategyConfig(FrozenReplayModel):
    version: str = "replay-strategy-v2.0.0"
    data: ReplayDataConfig
    signal: ReplaySignalConfig = Field(default_factory=ReplaySignalConfig)
    portfolio: ReplayPortfolioConfig = Field(default_factory=ReplayPortfolioConfig)
    risk: ReplayRiskConfig = Field(default_factory=ReplayRiskConfig)
    execution: ReplayExecutionConfig = Field(default_factory=ReplayExecutionConfig)
    validation: ReplayValidationConfig = Field(default_factory=ReplayValidationConfig)


class ReplayCreate(BaseModel):
    sleeve_weights_bps: SleeveWeights | None = None
    strategy: ReplayStrategyConfig | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "ReplayCreate":
        if self.strategy is not None and self.sleeve_weights_bps is not None:
            raise ValueError("기존 비중 요청과 v2 전략 설정을 동시에 보낼 수 없습니다.")
        return self


class ReplayAccepted(BaseModel):
    run_id: str
    status: RunStatus
    cached: bool = False


class ReplayEquityPoint(BaseModel):
    date: date
    portfolio: float
    benchmark: float
    exposure_matched_benchmark: float | None = None
    no_cost_portfolio: float | None = None


class ReplayDrawdownPoint(BaseModel):
    date: date
    drawdown: float


class ReplayPosition(BaseModel):
    asset_id: str
    symbol: str
    name: str
    peer_group: PeerGroup
    sleeve: Sleeve
    quantity: int
    price: float
    market_value_krw: float
    score: float


class ReplayHeadline(BaseModel):
    title: str
    summary: str
    largest_effect_label: str
    largest_effect: float
    largest_gap_period: str
    largest_gap_excess_return: float


class ReplayGapAnalysis(BaseModel):
    full_benchmark_return: float
    exposure_matched_return: float
    no_cost_strategy_return: float
    actual_strategy_return: float
    exposure_effect: float
    selection_execution_effect: float
    cost_effect: float
    reconciliation_error: float


class ReplayCostSummary(BaseModel):
    initial_fx_cost_krw: float
    trade_cost_krw: float
    explicit_cost_krw: float
    compounded_cost_drag_krw: float
    cost_drag: float


class ReplayPeriod(BaseModel):
    period: str
    strategy_return: float
    benchmark_return: float
    exposure_matched_return: float
    excess_return: float
    max_drawdown: float | None = None
    average_exposure: float | None = None
    trade_count: int | None = None
    cost_krw: float | None = None


class ReplaySleeveAttribution(BaseModel):
    sleeve: Sleeve
    initial_allocation_krw: float
    ending_value_krw: float
    pnl_krw: float
    return_: float = Field(alias="return", serialization_alias="return")
    contribution: float
    average_exposure: float
    trade_count: int
    cost_krw: float
    dividend_krw: float


class ReplayTradeStats(BaseModel):
    closed_count: int
    open_count: int
    win_rate: float
    average_gain: float
    average_loss: float
    payoff_ratio: float
    profit_factor: float
    median_holding_days: float
    net_pnl_krw: float


class ReplayTradeGroup(ReplayTradeStats):
    sleeve: Sleeve | None = None
    band: str | None = None
    reason: str | None = None


class ReplayRoundTrip(BaseModel):
    asset_id: str
    symbol: str
    name: str
    peer_group: PeerGroup
    sleeve: Sleeve
    currency: str
    status: str
    entry_date: date
    exit_date: date | None
    entry_score: float
    exit_score: float | None
    quantity: int
    entry_price: float
    exit_price: float | None
    entry_notional_krw: float
    exit_value_krw: float
    dividends_krw: float
    costs_krw: float
    net_pnl_krw: float
    net_return: float
    holding_days: int
    exit_reason: str


class ReplayTradeAnalysis(BaseModel):
    overall: ReplayTradeStats
    by_sleeve: list[ReplayTradeGroup]
    by_entry_score: list[ReplayTradeGroup]
    by_exit_reason: list[ReplayTradeGroup]
    best_trades: list[ReplayRoundTrip]
    worst_trades: list[ReplayRoundTrip]


class ReplayMarketRegime(BaseModel):
    peer_group: PeerGroup
    review_count: int
    entry_allowed_count: int
    entry_blocked_count: int
    entry_allowed_rate: float
    average_candidate_count: float
    planned_buy_count: int
    planned_sell_count: int
    average_held_count: float


class ReplayIntegrityCheck(BaseModel):
    code: str
    label: str
    status: str
    severity: str
    detail: str


class ReplayAnalysis(BaseModel):
    version: str
    headline: ReplayHeadline
    gap_analysis: ReplayGapAnalysis
    cost_summary: ReplayCostSummary
    annual_periods: list[ReplayPeriod]
    monthly_periods: list[ReplayPeriod]
    sleeve_attribution: list[ReplaySleeveAttribution]
    trade_analysis: ReplayTradeAnalysis
    market_regimes: list[ReplayMarketRegime]
    integrity_checks: list[ReplayIntegrityCheck]


class ReplayResultSummary(BaseModel):
    data_version: str
    score_version: str
    portfolio_version: str
    started_on: date
    ended_on: date
    metrics: dict[str, float]
    equity_curve: list[ReplayEquityPoint]
    drawdown_curve: list[ReplayDrawdownPoint]
    final_positions: list[ReplayPosition]
    review_required_assets: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    cache_key: str | None = None
    analysis: ReplayAnalysis | None = None
    strategy_config: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    walk_forward: dict[str, Any] | None = None
    stress_tests: dict[str, Any] | None = None


class ReplayResponse(BaseModel):
    run_id: str
    status: RunStatus
    stage: str
    completed_units: int
    total_units: int
    progress_percent: float
    data_version: str
    created_at: datetime
    updated_at: datetime
    config: dict[str, Any]
    result: ReplayResultSummary | None = None
    error_message: str | None = None
    bias_warning: str = "현재 상장 종목 기준으로 생존편향이 포함됩니다."


class ReplaySuccessCriteria(FrozenReplayModel):
    minimum_cagr_improvement_pp: float = 0.0
    minimum_mdd_improvement_pp: float = 0.0
    minimum_cost_reduction_ratio: float = 0.0
    minimum_sharpe_improvement: float = 0.0
    maximum_cagr_degradation_pp: float = 0.0
    maximum_mdd_degradation_pp: float = 0.0


class ReplayExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=500)
    objective: ExperimentObjective
    success_criteria: ReplaySuccessCriteria | None = None
    baseline_label: str = Field(default="기준 전략", min_length=1, max_length=80)
    baseline_strategy: ReplayStrategyConfig


class ReplayExperimentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None


class ReplayExperimentRunCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    role: ExperimentRunRole = ExperimentRunRole.CHALLENGER
    strategy: ReplayStrategyConfig


class ReplaySweepAxis(BaseModel):
    path: str = Field(min_length=1, max_length=120)
    values: list[Any] = Field(min_length=2, max_length=20)


class ReplaySweepCreate(BaseModel):
    label: str = Field(default="민감도 분석", min_length=1, max_length=80)
    base_strategy: ReplayStrategyConfig
    axes: list[ReplaySweepAxis] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_combinations(self) -> "ReplaySweepCreate":
        combinations = 1
        for axis in self.axes:
            combinations *= len(axis.values)
        if combinations > 100:
            raise ValueError("민감도 분석은 최대 100개 조합까지 실행할 수 있습니다.")
        if len({axis.path for axis in self.axes}) != len(self.axes):
            raise ValueError("민감도 분석 축은 중복될 수 없습니다.")
        return self


class ReplayExperimentSummary(BaseModel):
    experiment_id: str
    name: str
    hypothesis: str
    objective: ExperimentObjective
    status: str
    data_version: str
    universe_mode: UniverseMode
    run_count: int = 0
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class ReplayExperimentResponse(ReplayExperimentSummary):
    notes: str | None = None
    success_criteria: ReplaySuccessCriteria
    period: ReplayDataConfig
    runs: list[dict[str, Any]] = Field(default_factory=list)


class ReplayExperimentListResponse(BaseModel):
    total: int
    items: list[ReplayExperimentSummary]


class ReplayComparisonResponse(BaseModel):
    experiment_id: str
    baseline_run_id: str | None = None
    runs: list[dict[str, Any]] = Field(default_factory=list)
    success_assessments: list[dict[str, Any]] = Field(default_factory=list)


class ReplayPromotionCreate(BaseModel):
    account_type: ForwardAccountType
    name: str = Field(min_length=1, max_length=80)
    experiment_id: str | None = Field(default=None, max_length=36)


class ReplayOptionsResponse(BaseModel):
    data_version: str
    raw_history_start: date
    raw_history_end: date
    supports_point_in_time: bool
    universe_modes: list[UniverseMode]
    default_strategy: ReplayStrategyConfig
    limits: dict[str, Any]


class ReplaySweepResponse(BaseModel):
    run_id: str
    experiment_id: str
    status: RunStatus
    stage: str
    completed_units: int
    total_units: int
    progress_percent: float
    result: dict[str, Any] | None = None
    error_message: str | None = None


class ScoreComponents(BaseModel):
    long_term_trend: float
    absolute_momentum: float
    relative_strength: float
    high_proximity: float
    volatility_stability: float
    trading_activity: float


class ScreenerItem(BaseModel):
    asset_id: str
    symbol: str
    name: str
    peer_group: PeerGroup
    as_of: date
    score: float | None
    state: CandidateState
    percentile: float | None
    reasons: list[str]
    warnings: list[str]
    exclusions: list[str]
    components: ScoreComponents
    adv60: float | None = None
    data_status: DataStatus = DataStatus.READY
    data_status_reason: str | None = None
    official_candidate: bool = False


class PeerCoverage(BaseModel):
    peer_group: PeerGroup
    listed_assets: int
    supported_assets: int
    ready_assets: int
    as_of: date | None = None


class ScreenerResponse(BaseModel):
    data_version: str
    score_version: str
    as_of: date
    total: int
    page: int = 1
    page_size: int = 100
    total_pages: int = 1
    coverage: list[PeerCoverage] = Field(default_factory=list)
    items: list[ScreenerItem]


class AssetDetail(BaseModel):
    asset: ScreenerItem
    price_history: list[dict[str, Any]]
    score_history: list[dict[str, Any]]
    score_trace: "ScoreTrace | None" = None


class ScoreTrace(BaseModel):
    data_version: str
    score_version: str
    score_config_hash: str
    close: float | None = None
    adjusted_close: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    r63: float | None = None
    r126: float | None = None
    r12_1: float | None = None
    high_ratio: float | None = None
    vol60: float | None = None
    adv60: float | None = None
    relative_strength_rank: float | None = None
    volatility_rank: float | None = None
    activity_rank: float | None = None
    data_eligible: bool
    absolute_liquidity_eligible: bool
    order_size_eligible: bool
    candidate_eligible: bool
    component_sum: float
    final_score: float | None = None


class QualityTotals(BaseModel):
    rows: int
    assets: int
    issues: int
    quarantined_assets: int
    repaired_assets: int
    warning_issues: int
    blocking_issues: int


class QualityGroup(BaseModel):
    peer_group: PeerGroup
    listed_assets: int
    supported_assets: int
    ready_assets: int
    ready_rate: float
    quarantined_assets: int
    download_failed_assets: int
    insufficient_history_assets: int
    stale_assets: int
    unsupported_assets: int


class QualityCheck(BaseModel):
    check_id: str
    label: str
    severity: QualitySeverity
    status: QualityStatus
    affected_count: int


class QualityReportResponse(BaseModel):
    policy_version: str
    data_version: str | None = None
    universe_version: str | None = None
    checked_at: datetime
    status: QualityStatus
    totals: QualityTotals
    groups: list[QualityGroup]
    checks: list[QualityCheck]


class QualityIssue(BaseModel):
    check_id: str
    severity: QualitySeverity
    resolution: QualityResolution
    scope: str
    asset_id: str | None = None
    symbol: str | None = None
    name: str | None = None
    peer_group: PeerGroup | None = None
    first_date: date | None = None
    last_date: date | None = None
    row_count: int
    message: str
    observed_value: str | None = None


class QualityIssuesResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[QualityIssue]


class PaperPortfolioResponse(BaseModel):
    as_of: date
    initial_capital_krw: float
    cash_krw: float
    invested_krw: float
    positions: list[dict[str, Any]]
    note: str


class MetaResponse(BaseModel):
    app_name: str = "Quant Trend Lab"
    app_mode: str
    data_version: str
    score_version: str
    portfolio_version: str
    disclaimer: str
    data_source: DataSource = DataSource.SYNTHETIC
    snapshot_state: SnapshotState = SnapshotState.READY
    can_sync: bool = False


class ProviderId(StrEnum):
    YFINANCE = "YFINANCE"
    KRX = "KRX"
    TOSS = "TOSS"


class ProviderConnectionState(StrEnum):
    ACTIVE = "ACTIVE"
    AVAILABLE = "AVAILABLE"
    NOT_CHECKED = "NOT_CHECKED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"


class ProviderStatusResponse(BaseModel):
    provider: ProviderId
    display_name: str
    role: str
    description: str
    enabled: bool
    configured: bool
    used_in_pipeline: bool
    status: ProviderConnectionState
    capabilities: list[str] = Field(default_factory=list)
    last_checked_at: datetime | None = None
    latency_ms: int | None = None
    message: str


class ProviderListResponse(BaseModel):
    items: list[ProviderStatusResponse]


class ResearchSyncResponse(BaseModel):
    sync_id: str
    trigger: SyncTrigger
    status: RunStatus
    stage: str
    collection_mode: ResearchCollectionMode | None = None
    completed_batches: int
    total_batches: int
    progress_percent: float
    universe_version: str | None = None
    data_version: str | None = None
    failed_tickers: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ResearchSyncAccepted(BaseModel):
    sync_id: str
    status: RunStatus
    reused: bool = False


class ResearchStatusResponse(BaseModel):
    app_mode: str
    data_source: DataSource
    snapshot_state: SnapshotState
    data_version: str | None = None
    universe_version: str | None = None
    last_success_at: datetime | None = None
    coverage: list[PeerCoverage] = Field(default_factory=list)
    last_sync: ResearchSyncResponse | None = None
    can_sync: bool


class CandidateHistoryItem(BaseModel):
    as_of: date
    data_version: str
    event_type: CandidateEventType
    asset_id: str
    symbol: str
    name: str
    peer_group: PeerGroup
    score: float | None = None
    previous_score: float | None = None


class CandidateHistoryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[CandidateHistoryItem]


class ForwardAccountCreate(BaseModel):
    sleeve_weights_bps: SleeveWeights = Field(default_factory=SleeveWeights)
    name: str = Field(default="기준 포트폴리오", min_length=1, max_length=80)


class ForwardAccountResponse(BaseModel):
    account_id: str
    account_type: ForwardAccountType = ForwardAccountType.BASELINE
    name: str = "기준 포트폴리오"
    status: PaperAccountStatus
    initial_capital_krw: float
    sleeve_weights_bps: dict[str, int]
    baseline_data_version: str
    last_data_version: str | None = None
    last_review_date: date | None = None
    created_at: datetime
    started_at: datetime | None = None
    archived_at: datetime | None = None
    current_value_krw: float
    cash_krw: float
    invested_krw: float
    cumulative_return: float
    max_drawdown: float
    observation_count: int
    annualized_metrics: dict[str, float] | None = None
    market_dates: dict[str, str] = Field(default_factory=dict)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    pending_orders: list[dict[str, Any]] = Field(default_factory=list)
    review_required_assets: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    strategy_config: dict[str, Any] | None = None
    strategy_config_hash: str | None = None
    source_experiment_id: str | None = None
    source_run_id: str | None = None
    common_period_metrics: dict[str, Any] | None = None


class ForwardAccountsResponse(BaseModel):
    total: int
    common_start_date: date | None = None
    accounts: list[ForwardAccountResponse] = Field(default_factory=list)


class ForwardActivityResponse(BaseModel):
    account_id: str
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[dict[str, Any]]


class ForwardActionResponse(BaseModel):
    account_id: str
    status: PaperAccountStatus
