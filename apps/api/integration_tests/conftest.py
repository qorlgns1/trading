import os
import shutil
from pathlib import Path

from sqlalchemy.engine import make_url

test_database_url = os.getenv("TEST_DATABASE_URL")
if not test_database_url:
    raise RuntimeError("PostgreSQL 통합 테스트는 make test-integration으로 실행하세요.")

parsed_url = make_url(test_database_url)
if (
    parsed_url.get_backend_name() != "postgresql"
    or parsed_url.host not in {"127.0.0.1", "localhost"}
    or not (parsed_url.database or "").endswith("_test")
):
    raise RuntimeError(
        "통합 테스트 DB는 로컬 PostgreSQL이며 데이터베이스 이름이 _test로 끝나야 합니다."
    )

os.environ.update(
    {
        "APP_ENV": "test",
        "APP_MODE": "public_demo",
        "DATABASE_URL": test_database_url,
        "ARTIFACT_BACKEND": "local",
        "ARTIFACT_ROOT": ".integration-test-artifacts",
        "BACKTEST_EAGER": "true",
        "AUTO_CREATE_SCHEMA": "false",
        "VALKEY_URL": "",
        "RESEARCH_AUTO_SYNC": "false",
        "RESEARCH_ROOT": ".integration-test-research",
    }
)


def pytest_sessionstart() -> None:
    shutil.rmtree(".integration-test-artifacts", ignore_errors=True)
    shutil.rmtree(".integration-test-research", ignore_errors=True)


def pytest_sessionfinish() -> None:
    shutil.rmtree(".integration-test-artifacts", ignore_errors=True)
    shutil.rmtree(".integration-test-research", ignore_errors=True)
    Path("data/test-quant.db").unlink(missing_ok=True)
