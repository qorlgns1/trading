import json
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from quant_core.enums import (
    DataStatus,
    PeerGroup,
    QualityResolution,
    QualitySeverity,
    QualityStatus,
)

from quant_api.universe import UniverseSnapshot

QUALITY_POLICY_VERSION = "data-quality-v1.0.0"
QUALITY_RUN_RETENTION = 10

CHECK_DEFINITIONS: dict[str, tuple[str, QualitySeverity]] = {
    "REQUIRED_SCHEMA": ("필수 데이터 구조", QualitySeverity.ERROR),
    "UNIQUE_ASSET_DATE": ("종목·날짜 중복", QualitySeverity.ERROR),
    "DATE_RANGE": ("요청 기간", QualitySeverity.ERROR),
    "UNIVERSE_CONSISTENCY": ("종목군 일치", QualitySeverity.ERROR),
    "REFERENCE_DATA_VALID": ("환율·벤치마크", QualitySeverity.ERROR),
    "PRICE_NON_POSITIVE": ("가격 유효성", QualitySeverity.ERROR),
    "VOLUME_NEGATIVE": ("거래량 유효성", QualitySeverity.ERROR),
    "CORPORATE_ACTION_INVALID": ("기업행사 유효성", QualitySeverity.ERROR),
    "PROVIDER_REPAIRED": ("공급자 자동 복구", QualitySeverity.WARNING),
    "EXTREME_DISTRIBUTION": ("과도한 배당·분배", QualitySeverity.WARNING),
    "EXTREME_RETURN": ("과도한 일간 수익률", QualitySeverity.WARNING),
    "DOWNLOAD_FAILED": ("가격 수집 실패", QualitySeverity.WARNING),
    "INSUFFICIENT_HISTORY": ("가격 이력 부족", QualitySeverity.WARNING),
    "STALE_DATA": ("최신 거래일 지연", QualitySeverity.WARNING),
    "SCORE_INVARIANTS": ("점수 계산 규칙", QualitySeverity.ERROR),
    "COVERAGE_MINIMUM": ("최소 비교군 규모", QualitySeverity.ERROR),
    "COVERAGE_REGRESSION": ("산출 가능 비율 변화", QualitySeverity.WARNING),
    "QUARANTINE_LIMIT": ("격리 종목 한도", QualitySeverity.ERROR),
}

RAW_REQUIRED_COLUMNS = {
    "asset_id",
    "date",
    "symbol",
    "name",
    "peer_group",
    "currency",
    "open",
    "close",
    "adjusted_close",
    "volume",
    "dividend",
    "split_ratio",
    "benchmark_close",
    "fx_krw_per_usd",
    "provider_repaired",
}

ISSUE_SCHEMA = {
    "check_id": pl.String,
    "severity": pl.String,
    "resolution": pl.String,
    "scope": pl.String,
    "asset_id": pl.String,
    "symbol": pl.String,
    "name": pl.String,
    "peer_group": pl.String,
    "first_date": pl.String,
    "last_date": pl.String,
    "row_count": pl.Int64,
    "message": pl.String,
    "observed_value": pl.String,
}


class QualityGateError(RuntimeError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


@dataclass
class RawQualityResult:
    issues: list[dict[str, Any]]
    repair_asset_ids: set[str]
    stats: dict[str, Any]

    @property
    def blockers(self) -> list[dict[str, Any]]:
        return [
            issue
            for issue in self.issues
            if issue["resolution"] == QualityResolution.BLOCKED.value
        ]

    def quarantine_reasons(self) -> dict[str, str]:
        reasons: dict[str, list[str]] = {}
        for issue in self.issues:
            if issue["resolution"] != QualityResolution.QUARANTINED.value:
                continue
            asset_id = issue.get("asset_id")
            if asset_id:
                reasons.setdefault(str(asset_id), []).append(str(issue["message"]))
        return {
            asset_id: "데이터 품질 검사 실패: " + "; ".join(dict.fromkeys(messages))
            for asset_id, messages in reasons.items()
        }


def _issue(
    check_id: str,
    *,
    resolution: QualityResolution,
    message: str,
    scope: str = "DATASET",
    severity: QualitySeverity | None = None,
    asset_id: str | None = None,
    symbol: str | None = None,
    name: str | None = None,
    peer_group: str | None = None,
    first_date: object | None = None,
    last_date: object | None = None,
    row_count: int = 1,
    observed_value: str | None = None,
) -> dict[str, Any]:
    default_severity = CHECK_DEFINITIONS[check_id][1]
    return {
        "check_id": check_id,
        "severity": (severity or default_severity).value,
        "resolution": resolution.value,
        "scope": scope,
        "asset_id": asset_id,
        "symbol": symbol,
        "name": name,
        "peer_group": peer_group,
        "first_date": str(first_date) if first_date is not None else None,
        "last_date": str(last_date) if last_date is not None else None,
        "row_count": int(row_count),
        "message": message,
        "observed_value": observed_value,
    }


def _invalid_number(column: str, *, positive: bool) -> pl.Expr:
    value = pl.col(column)
    invalid = value.is_null() | ~value.is_finite()
    return invalid | (value <= 0 if positive else value < 0)


class ResearchQualityValidator:
    def __init__(self, root: Path, *, minimum_group_assets: int = 30) -> None:
        self.root = root
        self.minimum_group_assets = minimum_group_assets

    def inspect_raw(
        self,
        staging: Path,
        universe: UniverseSnapshot,
        manifest: dict[str, Any],
    ) -> RawQualityResult:
        paths = sorted((staging / "bars").rglob("*.parquet"))
        if not paths:
            issue = _issue(
                "REQUIRED_SCHEMA",
                resolution=QualityResolution.BLOCKED,
                message="가격 Parquet 파일이 없습니다.",
            )
            return RawQualityResult([issue], set(), {"rows": 0, "assets": 0})

        scan = pl.scan_parquet([str(path) for path in paths])
        columns = set(scan.collect_schema().names())
        missing = sorted(RAW_REQUIRED_COLUMNS - columns)
        if missing:
            issue = _issue(
                "REQUIRED_SCHEMA",
                resolution=QualityResolution.BLOCKED,
                message="필수 열이 없습니다: " + ", ".join(missing),
            )
            return RawQualityResult([issue], set(), {"rows": 0, "assets": 0})

        stats = scan.select(
            pl.len().alias("rows"),
            pl.col("asset_id").n_unique().alias("assets"),
        ).collect().to_dicts()[0]
        issues: list[dict[str, Any]] = []

        duplicates = (
            scan.group_by(["asset_id", "date"])
            .len()
            .filter(pl.col("len") > 1)
            .select((pl.col("len") - 1).sum().fill_null(0).alias("count"))
            .collect()
            .item()
        )
        if duplicates:
            issues.append(
                _issue(
                    "UNIQUE_ASSET_DATE",
                    resolution=QualityResolution.BLOCKED,
                    message=f"중복된 종목·날짜 행이 {duplicates:,}개 있습니다.",
                    row_count=int(duplicates),
                )
            )

        history_start = str(manifest["history_start"])
        requested_end = str(manifest["requested_end"])
        invalid_dates = scan.filter(
            (pl.col("date") < pl.lit(history_start).str.to_date())
            | (pl.col("date") >= pl.lit(requested_end).str.to_date())
        ).select(pl.len()).collect().item()
        if invalid_dates:
            issues.append(
                _issue(
                    "DATE_RANGE",
                    resolution=QualityResolution.BLOCKED,
                    message=f"요청 기간 밖의 가격 행이 {invalid_dates:,}개 있습니다.",
                    row_count=int(invalid_dates),
                )
            )

        invalid_references = scan.filter(
            _invalid_number("benchmark_close", positive=True)
            | _invalid_number("fx_krw_per_usd", positive=True)
        ).select(pl.len()).collect().item()
        if invalid_references:
            issues.append(
                _issue(
                    "REFERENCE_DATA_VALID",
                    resolution=QualityResolution.BLOCKED,
                    message=f"환율 또는 벤치마크 오류가 {invalid_references:,}개 있습니다.",
                    row_count=int(invalid_references),
                )
            )

        universe_rows = {
            str(row["asset_id"]): row for row in pl.read_csv(universe.path).to_dicts()
        }
        metadata = scan.select(
            "asset_id", "symbol", "name", "peer_group", "currency"
        ).unique().collect()
        mismatches = 0
        for row in metadata.iter_rows(named=True):
            expected = universe_rows.get(str(row["asset_id"]))
            if expected is None or any(
                str(row[key]) != str(expected[key])
                for key in ("symbol", "peer_group", "currency")
            ):
                mismatches += 1
        if mismatches:
            issues.append(
                _issue(
                    "UNIVERSE_CONSISTENCY",
                    resolution=QualityResolution.BLOCKED,
                    message=f"종목군 메타데이터 불일치가 {mismatches:,}개 있습니다.",
                    row_count=mismatches,
                )
            )

        asset_checks = [
            (
                "PRICE_NON_POSITIVE",
                _invalid_number("open", positive=True)
                | _invalid_number("close", positive=True)
                | _invalid_number("adjusted_close", positive=True),
                "0 이하이거나 유효하지 않은 가격이 있습니다.",
            ),
            (
                "VOLUME_NEGATIVE",
                _invalid_number("volume", positive=False),
                "음수이거나 유효하지 않은 거래량이 있습니다.",
            ),
            (
                "CORPORATE_ACTION_INVALID",
                _invalid_number("split_ratio", positive=True)
                | _invalid_number("dividend", positive=False),
                "배당 또는 분할 값이 유효하지 않습니다.",
            ),
        ]
        repair_asset_ids: set[str] = set()
        for check_id, condition, message in asset_checks:
            rows = self._asset_aggregates(scan, condition)
            for row in rows:
                asset_id = str(row["asset_id"])
                repair_asset_ids.add(asset_id)
                issues.append(
                    _issue(
                        check_id,
                        resolution=QualityResolution.QUARANTINED,
                        scope="ASSET",
                        asset_id=asset_id,
                        symbol=str(row["symbol"]),
                        name=str(row["name"]),
                        peer_group=str(row["peer_group"]),
                        first_date=row["first_date"],
                        last_date=row["last_date"],
                        row_count=int(row["row_count"]),
                        message=message,
                    )
                )

        repaired_rows = self._asset_aggregates(scan, pl.col("provider_repaired").fill_null(False))
        for row in repaired_rows:
            issues.append(
                _issue(
                    "PROVIDER_REPAIRED",
                    severity=QualitySeverity.WARNING,
                    resolution=QualityResolution.REPAIRED,
                    scope="ASSET",
                    asset_id=str(row["asset_id"]),
                    symbol=str(row["symbol"]),
                    name=str(row["name"]),
                    peer_group=str(row["peer_group"]),
                    first_date=row["first_date"],
                    last_date=row["last_date"],
                    row_count=int(row["row_count"]),
                    message="공급자가 가격 또는 기업행사 데이터를 자동 복구했습니다.",
                )
            )

        distributions = self._asset_aggregates(
            scan,
            (pl.col("close") > 0)
            & (pl.col("dividend") > 0)
            & ((pl.col("dividend") / pl.col("close")) >= 0.5),
        )
        for row in distributions:
            issues.append(
                _issue(
                    "EXTREME_DISTRIBUTION",
                    resolution=QualityResolution.WARN_ONLY,
                    scope="ASSET",
                    asset_id=str(row["asset_id"]),
                    symbol=str(row["symbol"]),
                    name=str(row["name"]),
                    peer_group=str(row["peer_group"]),
                    first_date=row["first_date"],
                    last_date=row["last_date"],
                    row_count=int(row["row_count"]),
                    message="배당·분배금이 같은 날 종가의 50% 이상입니다.",
                )
            )

        return RawQualityResult(issues, repair_asset_ids, stats)

    def finalize(
        self,
        raw: RawQualityResult,
        *,
        initial_repair_ids: set[str],
        latest: pl.DataFrame,
        score_history_path: Path,
        universe: UniverseSnapshot,
        manifest: dict[str, Any],
        previous_manifest: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        issues = list(raw.issues)
        final_invalid = raw.repair_asset_ids
        repaired_ids = initial_repair_ids - final_invalid
        universe_rows = {
            str(row["asset_id"]): row for row in pl.read_csv(universe.path).to_dicts()
        }
        for asset_id in sorted(repaired_ids):
            asset = universe_rows[asset_id]
            issues.append(
                _issue(
                    "PROVIDER_REPAIRED",
                    severity=QualitySeverity.WARNING,
                    resolution=QualityResolution.REPAIRED,
                    scope="ASSET",
                    asset_id=asset_id,
                    symbol=str(asset["symbol"]),
                    name=str(asset["name"]),
                    peer_group=str(asset["peer_group"]),
                    message="전체 이력을 재수집하여 가격 오류가 해소됐습니다.",
                )
            )

        issues.extend(self._score_issues(latest, score_history_path, len(universe.assets)))
        groups, threshold_issues = self._group_quality(
            latest, universe, previous_manifest
        )
        issues.extend(threshold_issues)
        report = self._report(manifest, raw.stats, groups, issues)
        return report, issues

    def blocked_report(
        self,
        raw: RawQualityResult,
        manifest: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return self._report(manifest, raw.stats, [], raw.issues), raw.issues

    def write_report(
        self,
        run_id: str,
        report: dict[str, Any],
        issues: list[dict[str, Any]],
        *,
        staging: Path | None = None,
    ) -> Path:
        run_path = self.root / "quality-runs" / run_id
        self._write_directory(run_path, report, issues)
        if staging is not None:
            self._write_directory(staging / "quality", report, issues)
        self._retain_quality_runs()
        return run_path

    def ensure_passable(self, report: dict[str, Any]) -> None:
        if report["status"] == QualityStatus.FAIL.value:
            failed = [
                item["label"] for item in report["checks"] if item["status"] == "FAIL"
            ]
            raise QualityGateError(
                "데이터 품질 검사 실패: " + ", ".join(failed), report
            )

    @staticmethod
    def _asset_aggregates(scan: pl.LazyFrame, condition: pl.Expr) -> list[dict[str, Any]]:
        return (
            scan.filter(condition.fill_null(False))
            .group_by(["asset_id", "symbol", "name", "peer_group"])
            .agg(
                pl.len().alias("row_count"),
                pl.col("date").min().alias("first_date"),
                pl.col("date").max().alias("last_date"),
            )
            .collect()
            .to_dicts()
        )

    def _score_issues(
        self, latest: pl.DataFrame, history_path: Path, universe_count: int
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        required = {
            "asset_id",
            "trend_score",
            "candidate_state",
            "candidate_eligible",
            "official_candidate",
            "data_status",
            "relative_strength_rank",
            "long_term_trend_score",
            "absolute_momentum_score",
            "relative_strength_score",
            "high_proximity_score",
            "volatility_score",
            "activity_score",
        }
        missing = required - set(latest.columns)
        if missing:
            return [
                _issue(
                    "SCORE_INVARIANTS",
                    resolution=QualityResolution.BLOCKED,
                    message="점수 결과 필수 열이 없습니다: " + ", ".join(sorted(missing)),
                )
            ]

        duplicate_assets = latest.group_by("asset_id").len().filter(pl.col("len") > 1).height
        if latest.height != universe_count or duplicate_assets:
            issues.append(
                _issue(
                    "SCORE_INVARIANTS",
                    resolution=QualityResolution.BLOCKED,
                    message="종목별 최신 점수 행 수가 종목군 스냅샷과 일치하지 않습니다.",
                    row_count=abs(latest.height - universe_count) + duplicate_assets,
                )
            )

        component_sum = pl.sum_horizontal(
            "long_term_trend_score",
            "absolute_momentum_score",
            "relative_strength_score",
            "high_proximity_score",
            "volatility_score",
            "activity_score",
        )
        invalid_scores = latest.filter(
            pl.col("trend_score").is_not_null()
            & (
                ~pl.col("trend_score").is_finite()
                | (pl.col("trend_score") < 0)
                | (pl.col("trend_score") > 100)
                | ((component_sum - pl.col("trend_score")).abs() > 0.11)
            )
        ).height
        invalid_candidates = latest.filter(
            pl.col("official_candidate")
            & (
                (pl.col("data_status") != DataStatus.READY.value)
                | ~pl.col("candidate_eligible").fill_null(False)
                | (pl.col("trend_score") < 60)
            )
        ).height
        invalid_ranks = latest.filter(
            pl.col("relative_strength_rank").is_not_null()
            & (
                (pl.col("relative_strength_rank") < 0)
                | (pl.col("relative_strength_rank") > 1)
            )
        ).height
        invariant_count = invalid_scores + invalid_candidates + invalid_ranks
        if invariant_count:
            issues.append(
                _issue(
                    "SCORE_INVARIANTS",
                    resolution=QualityResolution.BLOCKED,
                    message=f"점수 또는 후보 판정 불일치가 {invariant_count:,}개 있습니다.",
                    row_count=invariant_count,
                )
            )

        status_checks = {
            DataStatus.DOWNLOAD_FAILED.value: "DOWNLOAD_FAILED",
            DataStatus.INSUFFICIENT_HISTORY.value: "INSUFFICIENT_HISTORY",
            DataStatus.STALE.value: "STALE_DATA",
        }
        for status, check_id in status_checks.items():
            for row in latest.filter(pl.col("data_status") == status).iter_rows(named=True):
                issues.append(
                    _issue(
                        check_id,
                        resolution=QualityResolution.WARN_ONLY,
                        scope="ASSET",
                        asset_id=str(row["asset_id"]),
                        symbol=str(row["symbol"]),
                        name=str(row["name"]),
                        peer_group=str(row["peer_group"]),
                        first_date=row["date"],
                        last_date=row["date"],
                        message=str(row.get("status_reason") or "데이터 상태 확인이 필요합니다."),
                    )
                )

        if history_path.is_file():
            history = pl.scan_parquet(history_path)
            extreme = self._asset_aggregates(
                history,
                (pl.col("log_return") > math.log(4))
                | (pl.col("log_return") < math.log(0.25)),
            )
            for row in extreme:
                issues.append(
                    _issue(
                        "EXTREME_RETURN",
                        resolution=QualityResolution.WARN_ONLY,
                        scope="ASSET",
                        asset_id=str(row["asset_id"]),
                        symbol=str(row["symbol"]),
                        name=str(row["name"]),
                        peer_group=str(row["peer_group"]),
                        first_date=row["first_date"],
                        last_date=row["last_date"],
                        row_count=int(row["row_count"]),
                        message="일간 수정주가 변화가 +300% 또는 -75% 범위를 벗어났습니다.",
                    )
                )
        return issues

    def _group_quality(
        self,
        latest: pl.DataFrame,
        universe: UniverseSnapshot,
        previous_manifest: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        groups: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        previous_quality = (previous_manifest or {}).get("quality", {})
        previous_groups = {
            str(group["peer_group"]): group
            for group in previous_quality.get("groups", [])
        }
        previous_coverage = (previous_manifest or {}).get("coverage", {})
        for group in PeerGroup:
            group_assets = [asset for asset in universe.assets if asset.peer_group is group]
            supported = sum(asset.is_supported for asset in group_assets)
            rows = latest.filter(pl.col("peer_group") == group.value)
            counts = {
                status: rows.filter(pl.col("data_status") == status).height
                for status in DataStatus
            }
            ready = counts[DataStatus.READY]
            quarantined = counts[DataStatus.INVALID_DATA]
            ready_rate = ready / supported if supported else 0.0
            groups.append(
                {
                    "peer_group": group.value,
                    "listed_assets": len(group_assets),
                    "supported_assets": supported,
                    "ready_assets": ready,
                    "ready_rate": round(ready_rate, 4),
                    "quarantined_assets": quarantined,
                    "download_failed_assets": counts[DataStatus.DOWNLOAD_FAILED],
                    "insufficient_history_assets": counts[DataStatus.INSUFFICIENT_HISTORY],
                    "stale_assets": counts[DataStatus.STALE],
                    "unsupported_assets": counts[DataStatus.UNSUPPORTED],
                }
            )
            if ready < self.minimum_group_assets:
                issues.append(
                    _issue(
                        "COVERAGE_MINIMUM",
                        resolution=QualityResolution.BLOCKED,
                        message=f"{group.value} 산출 가능 종목이 {ready}개뿐입니다.",
                        peer_group=group.value,
                    )
                )
            quarantine_limit = max(5, math.ceil(supported * 0.01))
            if quarantined > quarantine_limit:
                issues.append(
                    _issue(
                        "QUARANTINE_LIMIT",
                        resolution=QualityResolution.BLOCKED,
                        message=(
                            f"{group.value} 격리 종목 {quarantined}개가 "
                            f"허용 한도 {quarantine_limit}개를 초과했습니다."
                        ),
                        peer_group=group.value,
                        row_count=quarantined,
                    )
                )

            previous = previous_groups.get(group.value)
            if previous is not None:
                previous_rate = float(previous.get("ready_rate") or 0)
            else:
                coverage = previous_coverage.get(group.value, {})
                previous_supported = int(coverage.get("supported_assets") or 0)
                previous_rate = (
                    int(coverage.get("ready_assets") or 0) / previous_supported
                    if previous_supported
                    else ready_rate
                )
            drop = previous_rate - ready_rate
            if drop >= 0.20:
                issues.append(
                    _issue(
                        "COVERAGE_REGRESSION",
                        severity=QualitySeverity.ERROR,
                        resolution=QualityResolution.BLOCKED,
                        message=f"{group.value} 산출 가능 비율이 {drop:.1%}p 하락했습니다.",
                        peer_group=group.value,
                    )
                )
            elif drop >= 0.05:
                issues.append(
                    _issue(
                        "COVERAGE_REGRESSION",
                        resolution=QualityResolution.WARN_ONLY,
                        message=f"{group.value} 산출 가능 비율이 {drop:.1%}p 하락했습니다.",
                        peer_group=group.value,
                    )
                )
        return groups, issues

    def _report(
        self,
        manifest: dict[str, Any],
        stats: dict[str, Any],
        groups: list[dict[str, Any]],
        issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        for check_id, (label, severity) in CHECK_DEFINITIONS.items():
            related = [issue for issue in issues if issue["check_id"] == check_id]
            if any(
                issue["resolution"] == QualityResolution.BLOCKED.value
                for issue in related
            ):
                status = QualityStatus.FAIL.value
            elif related:
                status = QualityStatus.WARN.value
            else:
                status = QualityStatus.PASS.value
            checks.append(
                {
                    "check_id": check_id,
                    "label": label,
                    "severity": severity.value,
                    "status": status,
                    "affected_count": sum(int(issue["row_count"]) for issue in related),
                }
            )
        if any(check["status"] == QualityStatus.FAIL.value for check in checks):
            status = QualityStatus.FAIL
        elif issues:
            status = QualityStatus.WARN
        else:
            status = QualityStatus.PASS
        return {
            "policy_version": QUALITY_POLICY_VERSION,
            "data_version": manifest.get("data_version"),
            "universe_version": manifest.get("universe_version"),
            "checked_at": datetime.now(UTC).isoformat(),
            "status": status.value,
            "totals": {
                "rows": int(stats.get("rows") or 0),
                "assets": int(stats.get("assets") or 0),
                "issues": len(issues),
                "quarantined_assets": len(
                    {
                        issue["asset_id"]
                        for issue in issues
                        if issue["resolution"] == QualityResolution.QUARANTINED.value
                        and issue.get("asset_id")
                    }
                ),
                "repaired_assets": len(
                    {
                        issue["asset_id"]
                        for issue in issues
                        if issue["resolution"] == QualityResolution.REPAIRED.value
                        and issue.get("asset_id")
                    }
                ),
                "warning_issues": sum(
                    issue["resolution"]
                    in {
                        QualityResolution.WARN_ONLY.value,
                        QualityResolution.REPAIRED.value,
                        QualityResolution.QUARANTINED.value,
                    }
                    for issue in issues
                ),
                "blocking_issues": sum(
                    issue["resolution"] == QualityResolution.BLOCKED.value
                    for issue in issues
                ),
            },
            "groups": groups,
            "checks": checks,
        }

    @staticmethod
    def _write_directory(
        path: Path, report: dict[str, Any], issues: list[dict[str, Any]]
    ) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pl.DataFrame(issues, schema=ISSUE_SCHEMA, strict=False).write_parquet(
            path / "issues.parquet", compression="zstd"
        )

    def _retain_quality_runs(self) -> None:
        root = self.root / "quality-runs"
        if not root.is_dir():
            return
        runs = sorted(
            (path for path in root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in runs[QUALITY_RUN_RETENTION:]:
            shutil.rmtree(path, ignore_errors=True)
