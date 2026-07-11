from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from quant_core.config import PORTFOLIO_VERSION, TREND_SCORE_VERSION
from quant_core.enums import (
    CandidateEventType,
    CandidateState,
    PeerGroup,
    QualityResolution,
    QualitySeverity,
    RunStatus,
    SyncTrigger,
)

from quant_api.backtests import (
    artifact_responses,
    artifact_store,
    create_run,
    execute_backtest,
    repository,
    response_from_model,
)
from quant_api.database import create_schema
from quant_api.forward import forward_service
from quant_api.rate_limit import RateLimitExceeded, client_key, create_rate_limiter
from quant_api.research import LocalFeatureUnavailable, research_service
from quant_api.research_replays import (
    artifact_responses as replay_artifact_responses,
)
from quant_api.research_replays import (
    create_replay,
    execute_replay,
    recover_interrupted_replays,
)
from quant_api.research_replays import (
    repository as replay_repository,
)
from quant_api.research_replays import (
    response_from_model as replay_response_from_model,
)
from quant_api.research_store import ResearchSnapshotMissing
from quant_api.research_sync import research_sync_manager
from quant_api.schemas import (
    ArtifactResponse,
    AssetDetail,
    BacktestAccepted,
    BacktestCreate,
    BacktestResponse,
    CandidateHistoryResponse,
    ForwardAccountCreate,
    ForwardAccountResponse,
    ForwardActivityResponse,
    MetaResponse,
    PaperPortfolioResponse,
    QualityIssuesResponse,
    QualityReportResponse,
    ReplayAccepted,
    ReplayCreate,
    ReplayResponse,
    ResearchStatusResponse,
    ResearchSyncAccepted,
    ResearchSyncResponse,
    ScreenerResponse,
)
from quant_api.settings import get_settings

settings = get_settings()
rate_limiter = create_rate_limiter(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    del app
    if settings.auto_create_schema:
        await create_schema()
    await recover_interrupted_replays()
    await research_sync_manager.startup()
    try:
        yield
    finally:
        await research_sync_manager.shutdown()


app = FastAPI(
    title="Quant Trend Lab API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(ResearchSnapshotMissing)
async def research_snapshot_missing(_: Request, error: ResearchSnapshotMissing) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": str(error), "code": "RESEARCH_SNAPSHOT_MISSING"},
    )


@app.exception_handler(LocalFeatureUnavailable)
async def local_feature_unavailable(_: Request, error: LocalFeatureUnavailable) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(error)})


@app.exception_handler(PermissionError)
async def permission_error(_: Request, error: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(error)})


@app.get("/health/live", tags=["health"])
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def health_ready() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/api/v1/meta", response_model=MetaResponse, tags=["meta"])
async def meta() -> MetaResponse:
    research_status = await research_sync_manager.status()
    return MetaResponse(
        app_mode=settings.app_mode,
        data_version=research_status.data_version or "not-ready",
        score_version=TREND_SCORE_VERSION,
        portfolio_version=PORTFOLIO_VERSION,
        disclaimer="추세 점수는 투자 추천이나 미래 상승 확률이 아닙니다.",
        data_source=research_status.data_source,
        snapshot_state=research_status.snapshot_state,
        can_sync=research_status.can_sync,
    )


@app.get(
    "/api/v1/research/status",
    response_model=ResearchStatusResponse,
    tags=["research-sync"],
)
async def research_status() -> ResearchStatusResponse:
    return await research_sync_manager.status()


@app.post(
    "/api/v1/research/sync",
    response_model=ResearchSyncAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["research-sync"],
)
async def start_research_sync() -> ResearchSyncAccepted:
    try:
        run, reused = await research_sync_manager.request(SyncTrigger.MANUAL)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return ResearchSyncAccepted(sync_id=run.id, status=RunStatus(run.status), reused=reused)


@app.get(
    "/api/v1/research/sync/{sync_id}",
    response_model=ResearchSyncResponse,
    tags=["research-sync"],
)
async def get_research_sync(sync_id: str) -> ResearchSyncResponse:
    result = await research_sync_manager.get_sync(sync_id)
    if result is None:
        raise HTTPException(status_code=404, detail="동기화 실행을 찾을 수 없습니다.")
    return result


@app.get(
    "/api/v1/research/quality",
    response_model=QualityReportResponse,
    tags=["research-quality"],
)
async def research_quality() -> QualityReportResponse:
    return research_service.quality_report()


@app.get(
    "/api/v1/research/quality/issues",
    response_model=QualityIssuesResponse,
    tags=["research-quality"],
)
async def research_quality_issues(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    peer_group: PeerGroup | None = None,
    severity: QualitySeverity | None = None,
    resolution: QualityResolution | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> QualityIssuesResponse:
    return research_service.quality_issues(
        page=page,
        page_size=page_size,
        peer_group=peer_group,
        severity=severity,
        resolution=resolution,
        query=q,
    )


@app.get("/api/v1/research/quality/issues.csv", tags=["research-quality"])
async def research_quality_issues_csv(
    peer_group: PeerGroup | None = None,
    severity: QualitySeverity | None = None,
    resolution: QualityResolution | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Response:
    content = research_service.quality_issues_csv(
        peer_group=peer_group,
        severity=severity,
        resolution=resolution,
        query=q,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="data-quality-issues.csv"'},
    )


@app.get(
    "/api/v1/research/sync/{sync_id}/quality",
    response_model=QualityReportResponse,
    tags=["research-quality"],
)
async def research_sync_quality(sync_id: str) -> QualityReportResponse:
    return research_service.quality_report(sync_id)


@app.post(
    "/api/v1/research/replays",
    response_model=ReplayAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["research-replays"],
)
async def create_research_replay(
    payload: ReplayCreate, background_tasks: BackgroundTasks
) -> ReplayAccepted:
    try:
        model, cached = await create_replay(payload)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if not cached:
        background_tasks.add_task(execute_replay, model.id)
    return ReplayAccepted(
        run_id=model.id,
        status=RunStatus.SUCCEEDED if cached else RunStatus.QUEUED,
        cached=cached,
    )


@app.get(
    "/api/v1/research/replays/{run_id}",
    response_model=ReplayResponse,
    tags=["research-replays"],
)
async def get_research_replay(run_id: str) -> ReplayResponse:
    if settings.app_mode != "local_research":
        raise PermissionError("실데이터 과거 재생은 local_research 모드에서만 사용할 수 있습니다.")
    model = await replay_repository.get(run_id)
    if model is None or model.run_kind != "REAL_REPLAY":
        raise HTTPException(status_code=404, detail="과거 시뮬레이션 실행을 찾을 수 없습니다.")
    return replay_response_from_model(model)


@app.get(
    "/api/v1/research/replays/{run_id}/artifacts",
    response_model=list[ArtifactResponse],
    tags=["research-replays"],
)
async def get_research_replay_artifacts(run_id: str) -> list[ArtifactResponse]:
    if settings.app_mode != "local_research":
        raise PermissionError("실데이터 과거 재생은 local_research 모드에서만 사용할 수 있습니다.")
    model = await replay_repository.get(run_id)
    if model is None or model.run_kind != "REAL_REPLAY":
        raise HTTPException(status_code=404, detail="과거 시뮬레이션 실행을 찾을 수 없습니다.")
    return [
        ArtifactResponse.model_validate(item) for item in await replay_artifact_responses(run_id)
    ]


@app.get(
    "/api/v1/research/candidate-history",
    response_model=CandidateHistoryResponse,
    tags=["forward-research"],
)
async def candidate_history(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    peer_group: PeerGroup | None = None,
    event_type: CandidateEventType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> CandidateHistoryResponse:
    return await forward_service.candidate_history(
        page=page,
        page_size=page_size,
        peer_group=peer_group,
        event_type=event_type,
        date_from=date_from,
        date_to=date_to,
    )


@app.post(
    "/api/v1/forward/accounts",
    response_model=ForwardAccountResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["forward-research"],
)
async def create_forward_account(payload: ForwardAccountCreate) -> ForwardAccountResponse:
    try:
        return await forward_service.create_account(payload)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get(
    "/api/v1/forward/accounts/current",
    response_model=ForwardAccountResponse,
    tags=["forward-research"],
)
async def current_forward_account() -> ForwardAccountResponse:
    account = await forward_service.current_account()
    if account is None:
        raise HTTPException(status_code=404, detail="활성 포워드 계좌가 없습니다.")
    return account


@app.get(
    "/api/v1/forward/accounts/{account_id}/activity",
    response_model=ForwardActivityResponse,
    tags=["forward-research"],
)
async def forward_account_activity(
    account_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ForwardActivityResponse:
    try:
        return await forward_service.activity(account_id, page=page, page_size=page_size)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="포워드 계좌를 찾을 수 없습니다.") from error


@app.post(
    "/api/v1/forward/accounts/{account_id}/archive",
    response_model=ForwardAccountResponse,
    tags=["forward-research"],
)
async def archive_forward_account(account_id: str) -> ForwardAccountResponse:
    try:
        return await forward_service.archive_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="포워드 계좌를 찾을 수 없습니다.") from error


@app.post(
    "/api/v1/forward/accounts/{account_id}/retry",
    response_model=ForwardAccountResponse,
    tags=["forward-research"],
)
async def retry_forward_account(account_id: str) -> ForwardAccountResponse:
    try:
        return await forward_service.retry(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="포워드 계좌를 찾을 수 없습니다.") from error


@app.get("/api/v1/screener", response_model=ScreenerResponse, tags=["research"])
async def screener(
    peer_group: PeerGroup | None = None,
    candidate_state: CandidateState | None = None,
    minimum_score: Annotated[float, Query(ge=0, le=100)] = 0,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 100,
    q: Annotated[str | None, Query(max_length=100)] = None,
    official_only: bool = False,
    limit: Annotated[int | None, Query(ge=1, le=200, deprecated=True)] = None,
) -> ScreenerResponse:
    effective_size = limit or page_size
    return research_service.screener(
        peer_group,
        candidate_state,
        minimum_score,
        page,
        effective_size,
        q,
        official_only,
    )


@app.get("/api/v1/assets/{asset_id:path}", response_model=AssetDetail, tags=["research"])
async def asset_detail(asset_id: str) -> AssetDetail:
    detail = research_service.asset_detail(asset_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
    return detail


@app.get(
    "/api/v1/paper-portfolio", response_model=PaperPortfolioResponse, tags=["portfolio"]
)
async def paper_portfolio() -> PaperPortfolioResponse:
    return research_service.paper_portfolio()


def _request_ip(request: Request) -> str:
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


@app.post(
    "/api/v1/backtests",
    response_model=BacktestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["backtests"],
)
async def create_backtest(
    payload: BacktestCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    response: Response,
) -> BacktestAccepted:
    if settings.app_mode == "local_research":
        raise HTTPException(
            status_code=403,
            detail="실데이터 백테스트는 시점별 과거 종목군을 확보한 다음 단계에서 제공합니다.",
        )
    model, cached = await create_run(payload)
    if cached:
        return BacktestAccepted(run_id=model.id, status=RunStatus.SUCCEEDED, cached=True)
    key = client_key(_request_ip(request), settings.rate_limit_secret)
    try:
        await rate_limiter.acquire(key)
    except RateLimitExceeded as error:
        await repository.set_failed(model.id, "RATE_LIMITED")
        response.headers["Retry-After"] = str(error.retry_after)
        raise HTTPException(status_code=429, detail=str(error)) from error
    if settings.backtest_eager:
        background_tasks.add_task(execute_backtest, model.id, rate_limiter, key)
    else:
        from quant_api.worker import run_backtest_task

        run_backtest_task.delay(model.id, key)
    return BacktestAccepted(run_id=model.id, status=RunStatus.QUEUED)


@app.get(
    "/api/v1/backtests/{run_id}", response_model=BacktestResponse, tags=["backtests"]
)
async def get_backtest(run_id: str) -> BacktestResponse:
    model = await repository.get(run_id)
    if model is None:
        raise HTTPException(status_code=404, detail="백테스트 실행을 찾을 수 없습니다.")
    return response_from_model(model)


@app.get(
    "/api/v1/backtests/{run_id}/artifacts",
    response_model=list[ArtifactResponse],
    tags=["backtests"],
)
async def get_artifacts(run_id: str) -> list[ArtifactResponse]:
    if await repository.get(run_id) is None:
        raise HTTPException(status_code=404, detail="백테스트 실행을 찾을 수 없습니다.")
    return [ArtifactResponse.model_validate(item) for item in await artifact_responses(run_id)]


@app.get("/api/v1/artifacts/local/{object_key:path}", include_in_schema=False)
async def local_artifact(object_key: str) -> FileResponse:
    if object_key.startswith("research-runs/") and settings.app_mode != "local_research":
        raise HTTPException(status_code=403, detail="실데이터 산출물은 로컬 모드 전용입니다.")
    path = artifact_store.local_path(object_key)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="산출물을 찾을 수 없습니다.")
    return FileResponse(path)
