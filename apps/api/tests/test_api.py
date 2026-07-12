from fastapi.testclient import TestClient
from quant_api.main import app


def test_meta_and_screener_expose_demo_contract() -> None:
    with TestClient(app) as client:
        meta = client.get("/api/v1/meta")
        assert meta.status_code == 200
        assert meta.json()["app_mode"] == "public_demo"
        assert meta.json()["data_source"] == "SYNTHETIC"
        assert meta.json()["can_sync"] is False

        research_status = client.get("/api/v1/research/status")
        assert research_status.status_code == 200
        assert research_status.json()["snapshot_state"] == "READY"

        blocked_sync = client.post("/api/v1/research/sync")
        assert blocked_sync.status_code == 403

        quality = client.get("/api/v1/research/quality")
        assert quality.status_code == 403
        quality_issues = client.get("/api/v1/research/quality/issues")
        assert quality_issues.status_code == 403
        quality_csv = client.get("/api/v1/research/quality/issues.csv")
        assert quality_csv.status_code == 403
        assert client.post("/api/v1/research/replays", json={}).status_code == 403
        assert client.get("/api/v1/research/replays/missing").status_code == 403
        assert client.get("/api/v1/research/candidate-history").status_code == 403
        assert client.post("/api/v1/forward/accounts", json={}).status_code == 403
        assert client.get("/api/v1/forward/accounts/current").status_code == 403
        assert client.get("/api/v1/admin/providers").status_code == 403
        assert client.post("/api/v1/admin/providers/toss/check").status_code == 403

        response = client.get(
            "/api/v1/screener",
            params={"peer_group": "US_STOCK", "minimum_score": 65, "limit": 5},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data_version"].startswith("demo-market")
        assert len(body["items"]) <= 5
        assert all(item["peer_group"] == "US_STOCK" for item in body["items"])

        paper = client.get("/api/v1/paper-portfolio")
        assert paper.status_code == 200
        assert len(paper.json()["positions"]) <= 12

        any_asset = client.get("/api/v1/screener", params={"limit": 1}).json()["items"][0]
        detail = client.get(f"/api/v1/assets/{any_asset['asset_id']}")
        assert detail.status_code == 200
        assert detail.json()["asset"]["asset_id"] == any_asset["asset_id"]
        assert detail.json()["price_history"]

        missing = client.get("/api/v1/assets/UNKNOWN:ASSET")
        assert missing.status_code == 404


def test_screener_search_and_server_pagination() -> None:
    with TestClient(app) as client:
        first = client.get(
            "/api/v1/screener",
            params={"peer_group": "US_STOCK", "page": 1, "page_size": 2},
        )
        assert first.status_code == 200
        body = first.json()
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert body["total"] > 2
        assert body["total_pages"] > 1
        assert len(body["items"]) == 2

        symbol = body["items"][0]["symbol"]
        searched = client.get(
            "/api/v1/screener",
            params={"q": symbol.lower(), "page_size": 20},
        )
        assert searched.status_code == 200
        assert any(item["symbol"] == symbol for item in searched.json()["items"])

        official = client.get(
            "/api/v1/screener",
            params={"official_only": True, "page_size": 200},
        )
        assert official.status_code == 200
        assert official.json()["total"] > 0
        assert all(item["official_candidate"] for item in official.json()["items"])


def test_invalid_weights_are_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/backtests",
            json={
                "sleeve_weights_bps": {
                    "us_stock": 5000,
                    "kr_stock": 5000,
                    "us_etf": 5000,
                    "kr_etf": 5000,
                }
            },
        )
        assert response.status_code == 422


def test_backtest_runs_and_produces_downloadable_artifacts() -> None:
    with TestClient(app, raise_server_exceptions=True) as client:
        accepted = client.post("/api/v1/backtests", json={})
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        result = client.get(f"/api/v1/backtests/{run_id}")
        assert result.status_code == 200
        assert result.json()["status"] == "SUCCEEDED"
        assert result.json()["result"]["metrics"]["final_value_krw"] > 0

        artifacts = client.get(f"/api/v1/backtests/{run_id}/artifacts")
        assert artifacts.status_code == 200
        names = {item["name"] for item in artifacts.json()}
        assert names == {"report.html", "trades.csv", "result.json", "equity.parquet"}
        report_url = next(
            item["download_url"] for item in artifacts.json() if item["name"] == "report.html"
        )
        report = client.get(report_url)
        assert report.status_code == 200
        assert "가상 시장 데이터" in report.text

        cached = client.post("/api/v1/backtests", json={})
        assert cached.status_code == 202
        assert cached.json() == {"run_id": run_id, "status": "SUCCEEDED", "cached": True}
