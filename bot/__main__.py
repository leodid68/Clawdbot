"""CLI entry point — ``python3 -m bot``."""

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config
from .state import TradingState
from .strategy import run_strategy


def _setup_logging(level: str = "INFO", json_log: bool = False) -> None:
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
        prog="python3 -m bot",
        description="Trading bot — Polymarket CLOB direct",
    )
    parser.add_argument("--live", action="store_true", help="Execute real trades")
    parser.add_argument("--positions", action="store_true", help="Show open positions and exit")
    parser.add_argument("--config", action="store_true", help="Show config and exit")
    parser.add_argument(
        "--set", action="append", metavar="KEY=VALUE",
        help="Override config value (e.g. --set entry_threshold=0.12)",
    )
    parser.add_argument("--scan", action="store_true", help="Show tradeable markets and exit")
    parser.add_argument("--signals", action="store_true", help="Show detected signals and exit")
    parser.add_argument("--calibration", action="store_true", help="Show calibration stats and exit")
    parser.add_argument("--verbose", action="store_true", help="DEBUG logging")
    parser.add_argument("--json-log", action="store_true", help="Structured JSON logs (for OpenClaw)")

    args = parser.parse_args()
    config_dir = str(Path(__file__).parent)
    config = Config.load(config_dir)

    log_level = "DEBUG" if args.verbose else config.log_level
    _setup_logging(level=log_level, json_log=args.json_log)
    logger = logging.getLogger(__name__)

    # --set overrides
    if args.set:
        updates = {}
        for item in args.set:
            if "=" in item:
                k, v = item.split("=", 1)
                updates[k] = v
        if updates:
            config.update(updates)
            config.save(config_dir)
            logger.info("Config updated: %s", updates)

    # State path
    state_path = config.state_file
    if not Path(state_path).is_absolute():
        state_path = str(Path(config_dir) / state_path)

    # --config
    if args.config:
        from dataclasses import fields
        data = {f.name: getattr(config, f.name) for f in fields(config)}
        data.pop("private_key", None)
        print(json.dumps(data, indent=2))
        return

    # --positions
    if args.positions:
        state = TradingState.load(state_path)
        positions = state.open_positions()
        if not positions:
            print("No open positions.")
            return
        for p in positions:
            print(f"  {p.token_id[:16]}...  {p.side}  price={p.price:.4f}  size={p.size:.2f}")
        print(f"\n{len(positions)} position(s) | last run: {state.last_run}")
        return

    # --calibration
    if args.calibration:
        state = TradingState.load(state_path)
        cal = state.get_calibration()
        if cal["n"] == 0:
            print("No resolved predictions yet.")
            return
        print(f"Brier score: {cal['brier']:.6f}")
        print(f"Log score:   {cal['log']:.6f}")
        print(f"Predictions: {cal['n']}")
        from .scoring import calibration_curve
        preds = [p["our_prob"] for p in state.predictions.values()
                 if p.get("resolved") and p.get("outcome") is not None]
        outcomes = [p["outcome"] for p in state.predictions.values()
                    if p.get("resolved") and p.get("outcome") is not None]
        if preds:
            curve = calibration_curve(preds, outcomes)
            print("\nCalibration curve:")
            print(f"  {'Bin':<12} {'Predicted':>10} {'Actual':>10} {'Count':>6}")
            for b, p, a, c in zip(curve["bins"], curve["predicted"], curve["actual"], curve["count"]):
                print(f"  {b:<12} {p:>10.3f} {a:>10.3f} {c:>6}")
        return

    # Resolve private key
    if not config.private_key:
        print("Error: set POLY_PRIVATE_KEY env var or --set private_key=0x...")
        sys.exit(1)

    # Build Polymarket client
    from polymarket.client import PolymarketClient

    api_creds = config.load_api_creds(config_dir)
    client = PolymarketClient(
        private_key=config.private_key,
        api_creds=api_creds,
    )

    # --scan
    if args.scan:
        from .scanner import compute_book_metrics, filter_tradeable, scan_markets
        try:
            markets = scan_markets(client, limit=config.scan_limit)
            for m in markets:
                for tok in m.get("tokens", []):
                    try:
                        book = client.get_orderbook(tok["token_id"])
                        tok["metrics"] = compute_book_metrics(book)
                    except Exception:
                        tok["metrics"] = {"liquidity_grade": "D"}
                grades = [tok.get("metrics", {}).get("liquidity_grade", "D")
                          for tok in m.get("tokens", [])]
                m["liquidity_grade"] = min(grades, key=lambda g: "ABCD".index(g)) if grades else "D"
            tradeable = filter_tradeable(markets, min_liquidity=config.min_liquidity_grade)
            print(f"{'Question':<60} {'Grade':>5} {'Spread':>8} {'Mid':>6}")
            print("-" * 82)
            for m in tradeable:
                q = m.get("question", "")[:58]
                grade = m.get("liquidity_grade", "?")
                tok = m.get("tokens", [{}])[0]
                metrics = tok.get("metrics", {})
                spread = metrics.get("spread", 0)
                mid = metrics.get("mid_price", 0)
                print(f"  {q:<58} {grade:>5} {spread:>7.4f} {mid:>6.3f}")
            print(f"\n{len(tradeable)} tradeable market(s)")
        finally:
            client.close()
        return

    # --signals
    if args.signals:
        from .scanner import compute_book_metrics, filter_tradeable, scan_markets
        from .signals import scan_for_signals
        try:
            markets = scan_markets(client, limit=config.scan_limit)
            for m in markets:
                for tok in m.get("tokens", []):
                    try:
                        book = client.get_orderbook(tok["token_id"])
                        tok["metrics"] = compute_book_metrics(book)
                    except Exception:
                        tok["metrics"] = {"liquidity_grade": "D"}
                grades = [tok.get("metrics", {}).get("liquidity_grade", "D")
                          for tok in m.get("tokens", [])]
                m["liquidity_grade"] = min(grades, key=lambda g: "ABCD".index(g)) if grades else "D"
            tradeable = filter_tradeable(markets, min_liquidity=config.min_liquidity_grade)
            token_ids = [tok["token_id"] for m in tradeable for tok in m.get("tokens", [])]
            signals = scan_for_signals(client, token_ids, config)
            if not signals:
                print("No signals detected.")
            else:
                print(f"{'Side':<5} {'Token':<18} {'Price':>6} {'Edge':>6} {'Method':<16} {'Conf':>5}")
                print("-" * 60)
                for s in signals:
                    print(
                        f"  {s.side:<4} {s.token_id[:16]:<18} {s.market_price:>5.3f} "
                        f"{s.edge:>6.4f} {s.method:<16} {s.confidence:>5.2f}"
                    )
                print(f"\n{len(signals)} signal(s)")
        finally:
            client.close()
        return

    state = TradingState.load(state_path)
    dry_run = not args.live

    if dry_run:
        logger.info("DRY RUN — no trades will be executed")

    try:
        run_strategy(
            client=client,
            config=config,
            state=state,
            dry_run=dry_run,
            state_path=state_path,
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
