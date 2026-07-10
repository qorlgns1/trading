import math
from functools import cached_property
from typing import Any

import polars as pl
from quant_core.config import PEER_GROUP_SLOTS, TREND_SCORE_VERSION
from quant_core.enums import (
    CandidateState,
    DataStatus,
    PeerGroup,
    QualityResolution,
    QualitySeverity,
)
from quant_core.scoring import explain_result, score_trends
from quant_core.synthetic import ASSETS_PER_GROUP, DEMO_DATA_VERSION, generate_demo_market

from quant_api.research_store import ResearchSnapshotMissing, ResearchSnapshotStore
from quant_api.schemas import (
    AssetDetail,
    PaperPortfolioResponse,
    PeerCoverage,
    QualityIssue,
    QualityIssuesResponse,
    QualityReportResponse,
    ScoreComponents,
    ScoreTrace,
    ScreenerItem,
    ScreenerResponse,
)
from quant_api.settings import Settings, get_settings


class LocalFeatureUnavailable(RuntimeError):
    pass


class ResearchService:
    def __init__(self, settings: Settings, store: ResearchSnapshotStore) -> None:
        self.settings = settings
        self.store = store

    @property
    def is_local_research(self) -> bool:
        return self.settings.app_mode == "local_research"

    def reload(self) -> None:
        for name in ("bars", "scores", "latest_date", "latest_rows"):
            self.__dict__.pop(name, None)

    @cached_property
    def bars(self) -> pl.DataFrame:
        if self.is_local_research:
            raise LocalFeatureUnavailable(
                "실데이터 모드의 전체 이력 백테스트는 아직 지원하지 않습니다."
            )
        return generate_demo_market()

    @cached_property
    def scores(self) -> pl.DataFrame:
        if self.is_local_research:
            return self.store.score_history().collect()
        return score_trends(self.bars, data_version=DEMO_DATA_VERSION)

    @cached_property
    def latest_date(self) -> Any:
        return self.latest_frame.get_column("date").max()

    @property
    def latest_frame(self) -> pl.DataFrame:
        if self.is_local_research:
            return self.store.latest_scores()
        latest = self.scores.get_column("date").max()
        return self.scores.filter(pl.col("date") == latest)

    @cached_property
    def latest_rows(self) -> list[dict[str, Any]]:
        return self.latest_frame.to_dicts()

    @property
    def data_version(self) -> str:
        if not self.is_local_research:
            return DEMO_DATA_VERSION
        manifest = self.store.current_manifest()
        if manifest is None:
            raise ResearchSnapshotMissing("정상 실데이터 스냅샷이 없습니다.")
        return str(manifest["data_version"])

    def coverage(self) -> list[PeerCoverage]:
        if not self.is_local_research:
            return [
                PeerCoverage(
                    peer_group=group,
                    listed_assets=ASSETS_PER_GROUP,
                    supported_assets=ASSETS_PER_GROUP,
                    ready_assets=ASSETS_PER_GROUP,
                    as_of=self.latest_date,
                )
                for group in PeerGroup
            ]
        manifest = self.store.current_manifest()
        if manifest is None:
            return []
        return [
            PeerCoverage(
                peer_group=PeerGroup(group),
                listed_assets=int(values.get("listed_assets", 0)),
                supported_assets=int(values.get("supported_assets", 0)),
                ready_assets=int(values.get("ready_assets", 0)),
                as_of=values.get("as_of"),
            )
            for group, values in manifest.get("coverage", {}).items()
        ]

    def _item(self, row: dict[str, Any]) -> ScreenerItem:
        explanation = explain_result(row)
        rank = row.get("relative_strength_rank")
        components = ScoreComponents(
            long_term_trend=float(row.get("long_term_trend_score") or 0),
            absolute_momentum=float(row.get("absolute_momentum_score") or 0),
            relative_strength=float(row.get("relative_strength_score") or 0),
            high_proximity=float(row.get("high_proximity_score") or 0),
            volatility_stability=float(row.get("volatility_score") or 0),
            trading_activity=float(row.get("activity_score") or 0),
        )
        return ScreenerItem(
            asset_id=row["asset_id"],
            symbol=row["symbol"],
            name=row["name"],
            peer_group=PeerGroup(row["peer_group"]),
            as_of=row["date"],
            score=float(row["trend_score"]) if row.get("trend_score") is not None else None,
            state=CandidateState(row["candidate_state"]),
            percentile=round((1 - float(rank)) * 100, 1) if rank is not None else None,
            reasons=explanation["reasons"][:3],
            warnings=explanation["warnings"][:2],
            exclusions=explanation["exclusions"],
            components=components,
            adv60=float(row["adv60"]) if row.get("adv60") is not None else None,
            data_status=DataStatus(row.get("data_status") or DataStatus.READY.value),
            data_status_reason=row.get("status_reason"),
            official_candidate=bool(
                row.get(
                    "official_candidate",
                    row["candidate_state"]
                    in {
                        CandidateState.CANDIDATE.value,
                        CandidateState.STRONG_CANDIDATE.value,
                    },
                )
            ),
        )

    def screener(
        self,
        peer_group: PeerGroup | None,
        state: CandidateState | None,
        minimum_score: float,
        page: int,
        page_size: int,
        query: str | None = None,
        official_only: bool = False,
    ) -> ScreenerResponse:
        rows = self.latest_rows
        if peer_group is not None:
            rows = [row for row in rows if row["peer_group"] == peer_group.value]
        if state is not None:
            rows = [row for row in rows if row["candidate_state"] == state.value]
        if official_only:
            rows = [
                row
                for row in rows
                if bool(
                    row.get(
                        "official_candidate",
                        row["candidate_state"]
                        in {
                            CandidateState.CANDIDATE.value,
                            CandidateState.STRONG_CANDIDATE.value,
                        },
                    )
                )
            ]
        if query:
            needle = query.casefold().strip()
            rows = [
                row
                for row in rows
                if needle in str(row["symbol"]).casefold()
                or needle in str(row["name"]).casefold()
            ]
        rows = [
            row
            for row in rows
            if (
                row.get("trend_score") is not None
                and float(row["trend_score"]) >= minimum_score
            )
            or (row.get("trend_score") is None and minimum_score == 0)
        ]
        rows.sort(
            key=lambda row: (
                row.get("trend_score") is None,
                -float(row.get("trend_score") or 0),
                row["symbol"],
            )
        )
        total = len(rows)
        total_pages = max(1, math.ceil(total / page_size))
        offset = (page - 1) * page_size
        items = [self._item(row) for row in rows[offset : offset + page_size]]
        return ScreenerResponse(
            data_version=self.data_version,
            score_version=TREND_SCORE_VERSION,
            as_of=self.latest_date,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            coverage=self.coverage(),
            items=items,
        )

    def asset_detail(self, asset_id: str) -> AssetDetail | None:
        latest = next((row for row in self.latest_rows if row["asset_id"] == asset_id), None)
        if latest is None:
            return None
        history = self.scores.filter(pl.col("asset_id") == asset_id).sort("date")
        if history.is_empty():
            return AssetDetail(
                asset=self._item(latest),
                price_history=[],
                score_history=[],
                score_trace=None,
            )
        sampled = history.gather_every(5 if self.is_local_research else 21)
        if sampled.get_column("date")[-1] != history.get_column("date")[-1]:
            sampled = pl.concat([sampled, history.tail(1)])
        price_history = [
            {
                "date": row["date"].isoformat(),
                "close": round(float(row["close"]), 2) if row["close"] is not None else None,
                "sma50": round(float(row["sma50"]), 2) if row["sma50"] is not None else None,
                "sma200": round(float(row["sma200"]), 2)
                if row["sma200"] is not None
                else None,
            }
            for row in sampled.select("date", "close", "sma50", "sma200").iter_rows(named=True)
        ]
        score_history = [
            {
                "date": row["date"].isoformat(),
                "score": round(float(row["trend_score"]), 1)
                if row["trend_score"] is not None
                else None,
                "state": row["candidate_state"],
            }
            for row in sampled.select("date", "trend_score", "candidate_state").iter_rows(
                named=True
            )
        ]
        component_sum = sum(
            float(latest.get(key) or 0)
            for key in (
                "long_term_trend_score",
                "absolute_momentum_score",
                "relative_strength_score",
                "high_proximity_score",
                "volatility_score",
                "activity_score",
            )
        )
        score_trace = None
        if latest.get("trend_score") is not None and latest.get("score_config_hash"):
            score_trace = ScoreTrace(
                data_version=str(latest["data_version"]),
                score_version=str(latest["score_version"]),
                score_config_hash=str(latest["score_config_hash"]),
                close=self._optional_float(latest.get("close")),
                adjusted_close=self._optional_float(latest.get("adjusted_close")),
                sma50=self._optional_float(latest.get("sma50")),
                sma200=self._optional_float(latest.get("sma200")),
                r63=self._optional_float(latest.get("r63")),
                r126=self._optional_float(latest.get("r126")),
                r12_1=self._optional_float(latest.get("r12_1")),
                high_ratio=self._optional_float(latest.get("high_ratio")),
                vol60=self._optional_float(latest.get("vol60")),
                adv60=self._optional_float(latest.get("adv60")),
                relative_strength_rank=self._optional_float(
                    latest.get("relative_strength_rank")
                ),
                volatility_rank=self._optional_float(latest.get("volatility_rank")),
                activity_rank=self._optional_float(latest.get("activity_rank")),
                data_eligible=bool(latest.get("data_eligible")),
                absolute_liquidity_eligible=bool(
                    latest.get("absolute_liquidity_eligible")
                ),
                order_size_eligible=bool(latest.get("order_size_eligible")),
                candidate_eligible=bool(latest.get("candidate_eligible")),
                component_sum=round(component_sum, 1),
                final_score=self._optional_float(latest.get("trend_score")),
            )
        return AssetDetail(
            asset=self._item(latest),
            price_history=price_history,
            score_history=score_history,
            score_trace=score_trace,
        )

    def quality_report(self, sync_id: str | None = None) -> QualityReportResponse:
        if not self.is_local_research:
            raise LocalFeatureUnavailable("데이터 품질 보고서는 로컬 연구 모드 전용입니다.")
        return QualityReportResponse.model_validate(self.store.quality_report(sync_id))

    def quality_issues(
        self,
        *,
        page: int,
        page_size: int,
        peer_group: PeerGroup | None = None,
        severity: QualitySeverity | None = None,
        resolution: QualityResolution | None = None,
        query: str | None = None,
        sync_id: str | None = None,
    ) -> QualityIssuesResponse:
        frame = self._quality_issue_frame(
            peer_group=peer_group,
            severity=severity,
            resolution=resolution,
            query=query,
            sync_id=sync_id,
        )
        total = frame.height
        total_pages = max(1, math.ceil(total / page_size))
        offset = (page - 1) * page_size
        items = [
            QualityIssue.model_validate(row)
            for row in frame.slice(offset, page_size).to_dicts()
        ]
        return QualityIssuesResponse(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            items=items,
        )

    def quality_issues_csv(
        self,
        *,
        peer_group: PeerGroup | None = None,
        severity: QualitySeverity | None = None,
        resolution: QualityResolution | None = None,
        query: str | None = None,
    ) -> str:
        return self._quality_issue_frame(
            peer_group=peer_group,
            severity=severity,
            resolution=resolution,
            query=query,
        ).write_csv()

    def _quality_issue_frame(
        self,
        *,
        peer_group: PeerGroup | None,
        severity: QualitySeverity | None,
        resolution: QualityResolution | None,
        query: str | None,
        sync_id: str | None = None,
    ) -> pl.DataFrame:
        if not self.is_local_research:
            raise LocalFeatureUnavailable("데이터 품질 보고서는 로컬 연구 모드 전용입니다.")
        frame = self.store.quality_issues(sync_id)
        if peer_group is not None:
            frame = frame.filter(pl.col("peer_group") == peer_group.value)
        if severity is not None:
            frame = frame.filter(pl.col("severity") == severity.value)
        if resolution is not None:
            frame = frame.filter(pl.col("resolution") == resolution.value)
        if query:
            needle = query.casefold().strip()
            frame = frame.filter(
                pl.col("symbol").fill_null("").str.to_lowercase().str.contains(needle, literal=True)
                | pl.col("name").fill_null("").str.to_lowercase().str.contains(needle, literal=True)
                | pl.col("check_id").str.to_lowercase().str.contains(needle, literal=True)
            )
        return frame.sort(
            ["severity", "resolution", "peer_group", "symbol"],
            descending=[False, False, False, False],
            nulls_last=True,
        )

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        number = float(value)  # type: ignore[arg-type]
        return number if math.isfinite(number) else None

    def paper_portfolio(self) -> PaperPortfolioResponse:
        if self.is_local_research:
            raise LocalFeatureUnavailable(
                "실데이터 모드의 모의 포트폴리오는 다음 단계에서 제공합니다."
            )
        capital = 50_000_000.0
        latest_fx = float(self.latest_rows[0]["fx_krw_per_usd"])
        positions: list[dict[str, Any]] = []
        invested = 0.0
        for group in PeerGroup:
            candidates = [
                row
                for row in self.latest_rows
                if row["peer_group"] == group.value
                and bool(row["candidate_eligible"])
                and float(row.get("trend_score") or 0) >= 65
            ]
            candidates.sort(key=lambda row: -float(row["trend_score"]))
            target_krw = capital * 0.25 / 3
            for row in candidates[: PEER_GROUP_SLOTS[group]]:
                native_price = float(row["close"])
                price_krw = native_price * (latest_fx if row["currency"] == "USD" else 1)
                quantity = math.floor(target_krw / price_krw)
                value = quantity * price_krw
                invested += value
                positions.append(
                    {
                        "asset_id": row["asset_id"],
                        "symbol": row["symbol"],
                        "name": row["name"],
                        "peer_group": row["peer_group"],
                        "quantity": quantity,
                        "market_value_krw": round(value, 0),
                        "score": round(float(row["trend_score"]), 1),
                    }
                )
        return PaperPortfolioResponse(
            as_of=self.latest_date,
            initial_capital_krw=capital,
            cash_krw=round(capital - invested, 0),
            invested_krw=round(invested, 0),
            positions=positions,
            note="기본 25% 자산군 비중을 현재 가상 후보에 적용한 읽기 전용 포트폴리오입니다.",
        )


settings = get_settings()
research_store = ResearchSnapshotStore(settings.research_root)
research_service = ResearchService(settings, research_store)
