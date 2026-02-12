"""Strategy runner — full pipeline.

One main function called once per run, no internal loop.
Schedule with cron or OpenClaw for periodic execution.

Pipeline:
    1. scanner.scan_markets()        → candidate markets
    2. scanner.filter_tradeable()    → liquidity filter
    3. signals.scan_for_signals()    → edge detection (3 methods)
    4. For each signal:
       a. sizing.check_risk_limits() → verify limits
       b. sizing.position_size()     → Kelly sizing
       c. client.post_order()        → execute (if --live)
       d. state.record_trade()       → persist
       e. state.record_prediction()  → calibration tracking
    5. Check exits on open positions
    6. state.save()
    7. Log calibration stats
"""

import logging

from polymarket.client import PolymarketClient

from .config import Config
from .scanner import compute_book_metrics, filter_tradeable, scan_markets
from .signals import scan_for_signals
from .sizing import check_risk_limits, dynamic_exit_threshold, position_size
from .state import TradingState

logger = logging.getLogger(__name__)


def run_strategy(
    client: PolymarketClient,
    config: Config,
    state: TradingState,
    dry_run: bool = True,
    state_path: str = "state.json",
) -> None:
    """Main entry point — called once per run."""
    trades_this_run = 0

    # ── 1. Check exits on existing positions ─────────────────────────
    positions = state.open_positions()
    logger.info("Open positions: %d", len(positions))

    for pos in positions:
        try:
            price_info = client.get_price(pos.token_id)
            current = float(price_info.get("price", 0))
        except Exception:
            logger.warning("Failed to get price for %s, skipping exit check", pos.token_id[:16])
            continue

        threshold = dynamic_exit_threshold(pos.price, hours_to_resolution=48)

        if current >= threshold and pos.side == "BUY":
            pnl = (current - pos.price) * pos.size
            logger.info(
                "EXIT: %s @ %.4f (entry %.4f, pnl $%.2f)",
                pos.token_id[:16], current, pos.price, pnl,
            )
            if not dry_run:
                try:
                    client.post_order(
                        pos.token_id, "SELL", current, pos.size,
                        neg_risk=pos.neg_risk,
                    )
                    state.record_daily_pnl(pnl)
                    state.remove_trade(pos.market_id)
                except Exception as exc:
                    logger.error("Exit order failed: %s", exc)

    # ── 2. Scan markets ──────────────────────────────────────────────
    try:
        raw_markets = scan_markets(client, limit=config.scan_limit)
    except Exception as exc:
        logger.error("Market scan failed: %s", exc)
        state.save(state_path)
        return

    # ── 3. Compute book metrics and filter ───────────────────────────
    for m in raw_markets:
        best_grade = "D"
        for tok in m.get("tokens", []):
            try:
                book = client.get_orderbook(tok["token_id"])
                metrics = compute_book_metrics(book)
                tok["metrics"] = metrics
                if _grade_rank(metrics["liquidity_grade"]) < _grade_rank(best_grade):
                    best_grade = metrics["liquidity_grade"]
            except Exception:
                tok["metrics"] = {"liquidity_grade": "D"}
        m["liquidity_grade"] = best_grade

    tradeable = filter_tradeable(raw_markets, min_liquidity=config.min_liquidity_grade)

    # ── 4. Detect signals ────────────────────────────────────────────
    token_ids = []
    for m in tradeable:
        for tok in m.get("tokens", []):
            token_ids.append(tok["token_id"])

    signals = scan_for_signals(client, token_ids, config)
    logger.info("Signals detected: %d", len(signals))

    # ── 5. Execute on signals ────────────────────────────────────────
    for sig in signals:
        if trades_this_run >= config.max_trades_per_run:
            logger.info("Max trades per run reached (%d)", config.max_trades_per_run)
            break

        size_usd = position_size(
            probability=sig.fair_value,
            price=sig.market_price,
            bankroll=config.max_total_exposure,
            max_position=config.max_position_usd,
            kelly_frac=config.kelly_fraction,
        )
        if size_usd <= 0:
            continue

        allowed, reason = check_risk_limits(state, config, size_usd)
        if not allowed:
            logger.info("Risk limit: %s — skipping %s", reason, sig.token_id[:16])
            continue

        # Find the condition_id for this token
        condition_id = _find_condition_id(tradeable, sig.token_id)

        logger.info(
            "TRADE: %s %s @ %.4f, size=$%.2f, edge=%.4f [%s]",
            sig.side, sig.token_id[:16], sig.market_price, size_usd,
            sig.edge, sig.method,
        )

        if not dry_run:
            try:
                shares = size_usd / sig.market_price if sig.market_price > 0 else 0
                result = client.post_order(
                    sig.token_id, sig.side, sig.market_price, shares,
                )
                state.record_trade(
                    market_id=condition_id or sig.token_id,
                    token_id=sig.token_id,
                    side=sig.side,
                    price=sig.market_price,
                    size=shares,
                    order_id=result.get("orderID", ""),
                )
            except Exception as exc:
                logger.error("Order failed: %s", exc)
                continue
        else:
            state.record_trade(
                market_id=condition_id or sig.token_id,
                token_id=sig.token_id,
                side=sig.side,
                price=sig.market_price,
                size=size_usd / sig.market_price if sig.market_price > 0 else 0,
                memo="dry_run",
            )

        state.record_prediction(
            condition_id or sig.token_id, sig.fair_value, sig.market_price,
        )
        trades_this_run += 1

    logger.info("Trades this run: %d (max %d)", trades_this_run, config.max_trades_per_run)

    # ── 6. Log calibration ───────────────────────────────────────────
    cal = state.get_calibration()
    if cal["n"] > 0:
        logger.info(
            "Calibration: Brier=%.4f  Log=%.4f  (n=%d)",
            cal["brier"], cal["log"], cal["n"],
        )

    # ── 7. Persist state ─────────────────────────────────────────────
    state.save(state_path)
    logger.info("Done.")


def _grade_rank(grade: str) -> int:
    return {"A": 0, "B": 1, "C": 2, "D": 3}.get(grade, 3)


def _find_condition_id(markets: list[dict], token_id: str) -> str:
    for m in markets:
        for tok in m.get("tokens", []):
            if tok.get("token_id") == token_id:
                return m.get("condition_id", "")
    return ""
