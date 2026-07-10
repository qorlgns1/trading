from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
from quant_core.config import TrendScoreConfig
from quant_core.enums import CandidateState, DataStatus
from quant_core.scoring import config_hash, score_trends

from quant_api.research_pipeline import PriceBuildResult
from quant_api.research_store import ResearchSnapshotStore
from quant_api.universe import UniverseSnapshot

CANDIDATE_STATES = {CandidateState.CANDIDATE.value, CandidateState.STRONG_CANDIDATE.value}


def _official_state(row: dict[str, Any], was_candidate: bool) -> str:
    current = str(row.get("candidate_state") or CandidateState.NOT_AVAILABLE.value)
    if current in {CandidateState.NOT_AVAILABLE.value, CandidateState.EXCLUDED.value}:
        return current
    score = float(row.get("trend_score") or 0)
    threshold = 60.0 if was_candidate else 65.0
    if bool(row.get("candidate_eligible")) and score >= threshold:
        return (
            CandidateState.STRONG_CANDIDATE.value
            if score >= 80
            else CandidateState.CANDIDATE.value
        )
    if score >= 50:
        return CandidateState.WATCH.value
    return CandidateState.WEAK.value


def _placeholder(
    asset: dict[str, Any], *, as_of: date, status: DataStatus, reason: str
) -> dict[str, Any]:
    return {
        "date": as_of,
        "asset_id": asset["asset_id"],
        "symbol": asset.get("symbol") or asset.get("ticker"),
        "name": asset["name"],
        "peer_group": asset["peer_group"],
        "currency": asset["currency"],
        "adjusted_close": None,
        "close": None,
        "sma50": None,
        "sma200": None,
        "trend_score": None,
        "candidate_state": CandidateState.NOT_AVAILABLE.value,
        "raw_candidate_state": CandidateState.NOT_AVAILABLE.value,
        "candidate_eligible": False,
        "data_eligible": False,
        "relative_strength_rank": None,
        "long_term_trend_score": 0.0,
        "absolute_momentum_score": 0.0,
        "relative_strength_score": 0.0,
        "high_proximity_score": 0.0,
        "volatility_score": 0.0,
        "activity_score": 0.0,
        "adv60": None,
        "valid_253": 0,
        "valid_60": 0,
        "is_supported": bool(asset.get("is_supported")),
        "is_suspended": False,
        "data_status": status.value,
        "status_reason": reason,
        "official_candidate": False,
    }


class ResearchScorer:
    def __init__(
        self,
        *,
        store: ResearchSnapshotStore,
        lookback_sessions: int = 400,
        config: TrendScoreConfig | None = None,
    ) -> None:
        self.store = store
        self.lookback_sessions = lookback_sessions
        self.config = config or TrendScoreConfig()

    def score(
        self,
        build: PriceBuildResult,
        universe: UniverseSnapshot,
        quarantined: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        quarantined = quarantined or {}
        bars = self._recent_bars(build.staging_path, build.manifest)
        if quarantined:
            bars = bars.filter(~pl.col("asset_id").is_in(list(quarantined)))
        scored = score_trends(bars, self.config, data_version=build.data_version)
        latest = (
            scored.sort(["asset_id", "date"])
            .group_by("asset_id", maintain_order=True)
            .tail(1)
        )
        previous = self._previous_states()
        universe_rows = {row["asset_id"]: row for row in pl.read_csv(universe.path).to_dicts()}
        group_as_of = {
            group: date.fromisoformat(values["as_of"])
            for group, values in build.manifest["coverage"].items()
            if values.get("as_of")
        }
        failed = {item["ticker"]: item for item in build.failed}
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in latest.to_dicts():
            asset_id = str(row["asset_id"])
            seen.add(asset_id)
            asset = universe_rows[asset_id]
            group_date = group_as_of.get(str(row["peer_group"]), cast(date, row["date"]))
            raw_state = str(row["candidate_state"])
            row["raw_candidate_state"] = raw_state
            status, reason = self._data_status(
                row, asset, group_date, failed, quarantined
            )
            row["data_status"] = status.value
            row["status_reason"] = reason
            if status is not DataStatus.READY:
                row["candidate_state"] = CandidateState.NOT_AVAILABLE.value
            else:
                row["candidate_state"] = _official_state(
                    row, previous.get(asset_id, "") in CANDIDATE_STATES
                )
            row["official_candidate"] = row["candidate_state"] in CANDIDATE_STATES
            rows.append(row)

        fallback_date = max(group_as_of.values()) if group_as_of else date.today()
        for asset_id, asset in universe_rows.items():
            if asset_id in seen:
                continue
            ticker = str(asset.get("ticker") or asset.get("symbol"))
            if not bool(asset.get("is_supported")):
                status = DataStatus.UNSUPPORTED
                reason = str(asset.get("status_reason") or "상품 유형 확인이 필요합니다.")
            elif asset_id in quarantined:
                status = DataStatus.INVALID_DATA
                reason = quarantined[asset_id]
            elif ticker in failed:
                status = DataStatus.DOWNLOAD_FAILED
                reason = str(failed[ticker].get("reason") or "가격 수집에 실패했습니다.")
            else:
                status = DataStatus.INSUFFICIENT_HISTORY
                reason = "점수 계산에 필요한 253거래일 가격 이력이 부족합니다."
            as_of = group_as_of.get(str(asset["peer_group"]), fallback_date)
            rows.append(_placeholder(asset, as_of=as_of, status=status, reason=reason))

        latest_frame = pl.DataFrame(rows, strict=False).sort(
            ["peer_group", "trend_score", "symbol"], descending=[False, True, False]
        )
        score_root = build.staging_path / "scores"
        score_root.mkdir(parents=True, exist_ok=True)
        latest_frame.write_parquet(score_root / "latest.parquet", compression="zstd")
        scored.write_parquet(score_root / "history.parquet", compression="zstd")
        return {
            **build.manifest,
            "score_version": self.config.version,
            "score_config_hash": config_hash(self.config),
            "latest_score_rows": latest_frame.height,
        }

    def _recent_bars(self, staging: Path, manifest: dict[str, Any]) -> pl.DataFrame:
        dates = [
            date.fromisoformat(values["as_of"])
            for values in manifest["coverage"].values()
            if values.get("as_of")
        ]
        if not dates:
            raise RuntimeError("점수를 계산할 가격 기준일이 없습니다.")
        cutoff = max(dates) - timedelta(days=self.lookback_sessions * 2)
        pattern = staging / "bars" / "peer_group=*" / "year=*" / "bars.parquet"
        frame = (
            pl.scan_parquet(str(pattern), hive_partitioning=True)
            .filter(pl.col("date") >= cutoff)
            .collect()
            .sort(["asset_id", "date"])
            .group_by("asset_id", maintain_order=True)
            .tail(self.lookback_sessions)
        )
        if frame.is_empty():
            raise RuntimeError("점수를 계산할 최근 가격 데이터가 없습니다.")
        return frame

    def _previous_states(self) -> dict[str, str]:
        try:
            frame = self.store.latest_scores()
        except (FileNotFoundError, RuntimeError):
            return {}
        return {
            str(row["asset_id"]): str(row["candidate_state"])
            for row in frame.select("asset_id", "candidate_state").iter_rows(named=True)
        }

    def _data_status(
        self,
        row: dict[str, Any],
        asset: dict[str, Any],
        group_as_of: date,
        failed: dict[str, dict[str, Any]],
        quarantined: dict[str, str],
    ) -> tuple[DataStatus, str | None]:
        asset_id = str(asset["asset_id"])
        ticker = str(asset.get("ticker") or asset.get("symbol"))
        if not bool(asset.get("is_supported")):
            return DataStatus.UNSUPPORTED, str(
                asset.get("status_reason") or "상품 유형 확인이 필요합니다."
            )
        if asset_id in quarantined:
            return DataStatus.INVALID_DATA, quarantined[asset_id]
        if ticker in failed:
            return DataStatus.DOWNLOAD_FAILED, str(
                failed[ticker].get("reason") or "가격 수집에 실패했습니다."
            )
        if int(row.get("valid_253") or 0) < 253:
            return (
                DataStatus.INSUFFICIENT_HISTORY,
                "점수 계산에 필요한 253거래일 가격 이력이 부족합니다.",
            )
        if cast(date, row["date"]) < group_as_of:
            return DataStatus.STALE, "해당 종목의 최신 종가가 비교군 기준일보다 오래됐습니다."
        return DataStatus.READY, None
