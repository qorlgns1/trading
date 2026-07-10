from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from quant_core.enums import (
    CandidateState,
    DataSource,
    DataStatus,
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
