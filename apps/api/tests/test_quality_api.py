from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from quant_api.main import app
from quant_api.research import ResearchService
from quant_api.research_quality import ResearchQualityValidator
from quant_api.research_store import ResearchSnapshotStore
from quant_api.settings import Settings


def _quality_report() -> dict[str, object]:
    return {
        "policy_version": "data-quality-v1.0.0",
        "data_version": "yf-api-test",
        "universe_version": "universe-api-test",
        "checked_at": datetime(2026, 7, 10, tzinfo=UTC).isoformat(),
        "status": "WARN",
        "totals": {
            "rows": 100,
            "assets": 2,
            "issues": 1,
            "quarantined_assets": 1,
            "repaired_assets": 0,
            "warning_issues": 1,
            "blocking_issues": 0,
        },
        "groups": [
            {
                "peer_group": "KR_KOSPI",
                "listed_assets": 2,
                "supported_assets": 2,
                "ready_assets": 1,
                "ready_rate": 0.5,
                "quarantined_assets": 1,
                "download_failed_assets": 0,
                "insufficient_history_assets": 0,
                "stale_assets": 0,
                "unsupported_assets": 0,
            }
        ],
        "checks": [
            {
                "check_id": "PRICE_NON_POSITIVE",
                "label": "가격 유효성",
                "severity": "ERROR",
                "status": "WARN",
                "affected_count": 1,
            }
        ],
    }


def _local_service(tmp_path: Path) -> ResearchService:
    root = tmp_path / "research"
    store = ResearchSnapshotStore(root)
    staging = store.create_staging("active")
    issue = {
        "check_id": "PRICE_NON_POSITIVE",
        "severity": "ERROR",
        "resolution": "QUARANTINED",
        "scope": "ASSET",
        "asset_id": "KR_KOSPI:005930.KS",
        "symbol": "005930.KS",
        "name": "삼성전자",
        "peer_group": "KR_KOSPI",
        "first_date": "2026-07-09",
        "last_date": "2026-07-09",
        "row_count": 1,
        "message": "0 이하이거나 유효하지 않은 가격이 있습니다.",
        "observed_value": None,
    }
    ResearchQualityValidator(root).write_report(
        "failed-run", _quality_report(), [issue], staging=staging
    )
    manifest = {
        "data_version": "yf-api-test",
        "universe_version": "universe-api-test",
        "created_at": datetime(2026, 7, 10, tzinfo=UTC).isoformat(),
    }
    store.activate(staging, manifest)
    settings = Settings(
        app_mode="local_research",
        research_root=root,
        research_auto_sync=False,
        _env_file=None,
    )
    return ResearchService(settings, store)


def test_local_quality_api_supports_filters_csv_and_failed_run_report(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("quant_api.main.research_service", _local_service(tmp_path))

    with TestClient(app) as client:
        report = client.get("/api/v1/research/quality")
        assert report.status_code == 200
        assert report.json()["policy_version"] == "data-quality-v1.0.0"

        issues = client.get(
            "/api/v1/research/quality/issues",
            params={
                "peer_group": "KR_KOSPI",
                "severity": "ERROR",
                "resolution": "QUARANTINED",
                "q": "삼성",
            },
        )
        assert issues.status_code == 200
        assert issues.json()["total"] == 1
        assert issues.json()["items"][0]["asset_id"] == "KR_KOSPI:005930.KS"

        empty = client.get(
            "/api/v1/research/quality/issues", params={"peer_group": "US_STOCK"}
        )
        assert empty.status_code == 200
        assert empty.json()["total"] == 0

        csv = client.get("/api/v1/research/quality/issues.csv")
        assert csv.status_code == 200
        assert "text/csv" in csv.headers["content-type"]
        assert "005930.KS" in csv.text

        failed_report = client.get("/api/v1/research/sync/failed-run/quality")
        assert failed_report.status_code == 200
        assert failed_report.json()["status"] == "WARN"
