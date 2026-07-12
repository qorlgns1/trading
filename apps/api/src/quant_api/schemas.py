from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from quant_core.enums import (
    CandidateEventType,
    CandidateState,
    DataSource,
    DataStatus,
    PaperAccountStatus,
    PeerGroup,
    QualityResolution,
    QualitySeverity,
    QualityStatus,
    RunStatus,
    Sleeve,
    SnapshotState,
    SyncTrigger,
)


class SleeveWeights(BaseModel):
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


class ReplayCreate(BaseModel):
    sleeve_weights_bps: SleeveWeights = Field(default_factory=SleeveWeights)


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


class ForwardAccountResponse(BaseModel):
    account_id: str
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
