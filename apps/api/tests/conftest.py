import os
import shutil
from pathlib import Path

os.environ.update(
    {
        "APP_ENV": "test",
        "APP_MODE": "public_demo",
        "DATABASE_URL": "sqlite+aiosqlite:///./data/test-quant.db",
        "ARTIFACT_BACKEND": "local",
        "ARTIFACT_ROOT": ".test-artifacts",
        "BACKTEST_EAGER": "true",
        "AUTO_CREATE_SCHEMA": "true",
        "VALKEY_URL": "",
        "RESEARCH_AUTO_SYNC": "false",
        "RESEARCH_ROOT": ".test-research",
    }
)


def pytest_sessionstart() -> None:
    Path("data/test-quant.db").unlink(missing_ok=True)
    shutil.rmtree(".test-artifacts", ignore_errors=True)
    shutil.rmtree(".test-research", ignore_errors=True)


def pytest_sessionfinish() -> None:
    Path("data/test-quant.db").unlink(missing_ok=True)
    shutil.rmtree(".test-artifacts", ignore_errors=True)
    shutil.rmtree(".test-research", ignore_errors=True)
