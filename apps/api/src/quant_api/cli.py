import argparse
from pathlib import Path

from quant_core.providers import YFinanceProvider, load_universe_csv

from quant_api.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Quant Trend Lab 개인 연구 데이터 도구")
    parser.add_argument("universe", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/research/market.parquet"))
    args = parser.parse_args()
    settings = get_settings()
    provider = YFinanceProvider(settings.app_mode)
    assets = load_universe_csv(args.universe)
    from datetime import date

    frame = provider.fetch(assets, date.fromisoformat(args.start), date.fromisoformat(args.end))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(args.output)


if __name__ == "__main__":
    main()
