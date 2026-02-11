"""CLI entry point — ``python -m weather``."""

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config
from .simmer_client import SimmerClient, get_api_key
from .state import TradingState
from .strategy import run_weather_strategy


def _setup_logging(level: str = "INFO", json_log: bool = False) -> None:
    """Configure structured logging."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    if json_log:
        fmt = logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'
        )
    else:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    root.addHandler(handler)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="weather",
        description="Simmer Weather Trading Bot — trades Polymarket weather markets using NOAA forecasts",
    )
    parser.add_argument("--live", action="store_true", help="Execute real trades (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Show opportunities without trading")
    parser.add_argument("--positions", action="store_true", help="Show current positions only")
    parser.add_argument("--config", action="store_true", help="Show current config")
    parser.add_argument(
        "--set", action="append", metavar="KEY=VALUE",
        help="Set config value (e.g., --set entry_threshold=0.20)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--json-log", action="store_true", help="Output structured JSON logs")
    parser.add_argument("--smart-sizing", action="store_true", help="Use portfolio-based position sizing (Kelly criterion)")
    parser.add_argument("--no-safeguards", action="store_true", help="Disable context safeguards")
    parser.add_argument("--no-trends", action="store_true", help="Disable price trend detection")

    args = parser.parse_args()

    # Determine config directory (same as this package)
    config_dir = str(Path(__file__).parent)

    # Load config
    config = Config.load(config_dir)

    # Handle --set updates
    if args.set:
        updates: dict = {}
        for item in args.set:
            if "=" in item:
                key, value = item.split("=", 1)
                updates[key] = value
        if updates:
            config.update(updates)
            config.save(config_dir)
            print(f"Config updated: {updates}")

    # Setup logging
    log_level = "DEBUG" if args.verbose else config.log_level
    _setup_logging(level=log_level, json_log=args.json_log)

    logger = logging.getLogger(__name__)

    # Resolve state file path relative to config dir
    state_path = config.state_file
    if not Path(state_path).is_absolute():
        state_path = str(Path(config_dir) / state_path)

    # Show config only
    if args.config:
        run_weather_strategy(
            client=None,  # type: ignore[arg-type]
            config=config,
            state=TradingState(),
            show_config=True,
            state_path=state_path,
        )
        return

    # Get API key and build client
    api_key = get_api_key()
    client = SimmerClient(
        api_key=api_key,
        max_retries=config.max_retries,
        base_delay=config.retry_base_delay,
    )

    # Load persistent state
    state = TradingState.load(state_path)

    dry_run = not args.live

    run_weather_strategy(
        client=client,
        config=config,
        state=state,
        dry_run=dry_run,
        positions_only=args.positions,
        show_config=False,
        use_safeguards=not args.no_safeguards,
        use_trends=not args.no_trends,
        state_path=state_path,
    )


if __name__ == "__main__":
    main()
