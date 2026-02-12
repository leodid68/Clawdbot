"""Market scanning, order-book metrics, and liquidity filtering.

Fetches markets from the Polymarket CLOB API, computes microstructure
metrics from the order book, and filters for tradeable opportunities.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket.client import PolymarketClient

logger = logging.getLogger(__name__)

_GRADE_BPS = {"A": 50, "B": 100, "C": 200}


def scan_markets(client: PolymarketClient, **filters) -> list[dict]:
    """Fetch active markets and extract token IDs for each outcome.

    Returns a list of dicts, each with at least:
        condition_id, question, tokens: [{token_id, outcome}], active, ...
    """
    raw = client.get_markets(**filters)
    markets: list[dict] = []

    for m in raw:
        if not m.get("active", True):
            continue
        if m.get("closed", False):
            continue

        tokens = m.get("tokens", [])
        if not tokens:
            continue

        markets.append({
            "condition_id": m.get("condition_id", ""),
            "question": m.get("question", ""),
            "tokens": tokens,
            "active": True,
            "neg_risk": m.get("neg_risk", False),
            "end_date_iso": m.get("end_date_iso", ""),
        })

    logger.info("Scanned %d active markets (from %d raw)", len(markets), len(raw))
    return markets


def compute_book_metrics(book: dict) -> dict:
    """Compute microstructure metrics from an order-book snapshot.

    Returns dict with: mid_price, spread, spread_bps, depth_bid_5,
    depth_ask_5, imbalance, kyle_lambda, liquidity_grade.
    """
    bids = book.get("bids") or []
    asks = book.get("asks") or []

    empty = {
        "mid_price": 0.0, "spread": 1.0, "spread_bps": 10_000,
        "depth_bid_5": 0.0, "depth_ask_5": 0.0,
        "imbalance": 0.0, "kyle_lambda": 0.0,
        "liquidity_grade": "D",
    }

    if not bids or not asks:
        return empty

    best_bid = float(bids[0]["price"])
    best_ask = float(asks[0]["price"])
    mid = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid

    if mid <= 0:
        return empty

    spread_bps = (spread / mid) * 10_000

    depth_bid = sum(float(b.get("size", 0)) for b in bids[:5])
    depth_ask = sum(float(a.get("size", 0)) for a in asks[:5])
    total_depth = depth_bid + depth_ask
    imbalance = (depth_bid - depth_ask) / total_depth if total_depth > 0 else 0.0
    kyle_lambda = spread / total_depth if total_depth > 0 else 0.0

    # Liquidity grade
    if spread_bps < _GRADE_BPS["A"]:
        grade = "A"
    elif spread_bps < _GRADE_BPS["B"]:
        grade = "B"
    elif spread_bps < _GRADE_BPS["C"]:
        grade = "C"
    else:
        grade = "D"

    return {
        "mid_price": round(mid, 4),
        "spread": round(spread, 4),
        "spread_bps": round(spread_bps, 1),
        "depth_bid_5": round(depth_bid, 2),
        "depth_ask_5": round(depth_ask, 2),
        "imbalance": round(imbalance, 4),
        "kyle_lambda": round(kyle_lambda, 6),
        "liquidity_grade": grade,
    }


def filter_tradeable(
    markets: list[dict], min_liquidity: str = "C",
) -> list[dict]:
    """Keep only markets whose best token meets the minimum liquidity grade.

    Grade ordering: A > B > C > D.  Markets graded below *min_liquidity*
    are excluded.
    """
    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    cutoff = grade_order.get(min_liquidity, 2)

    result = []
    for m in markets:
        grade = m.get("liquidity_grade", "D")
        if grade_order.get(grade, 3) <= cutoff:
            result.append(m)

    logger.info(
        "Filtered to %d tradeable markets (min grade %s)", len(result), min_liquidity,
    )
    return result
