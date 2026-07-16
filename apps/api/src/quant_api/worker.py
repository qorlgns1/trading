import asyncio

from celery import Celery

from quant_api.backtests import execute_backtest
from quant_api.replay_experiments import execute_sweep
from quant_api.research_replays import execute_replay
from quant_api.settings import get_settings

settings = get_settings()
celery_app = Celery(
    "quant_trend_lab",
    broker=settings.valkey_url or "redis://localhost:6379/0",
    backend=settings.valkey_url or "redis://localhost:6379/1",
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1,
    task_time_limit=900,
)


@celery_app.task(name="quant_api.run_backtest")  # type: ignore[untyped-decorator]
def run_backtest_task(run_id: str, rate_key: str) -> None:
    from quant_api.rate_limit import create_rate_limiter

    limiter = create_rate_limiter(settings)
    asyncio.run(execute_backtest(run_id, limiter=limiter, rate_key=rate_key))


@celery_app.task(name="quant_api.run_replay")  # type: ignore[untyped-decorator]
def run_replay_task(run_id: str) -> None:
    asyncio.run(execute_replay(run_id))


@celery_app.task(name="quant_api.run_replay_sweep")  # type: ignore[untyped-decorator]
def run_sweep_task(run_id: str) -> None:
    asyncio.run(execute_sweep(run_id))
