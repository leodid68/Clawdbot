"""Edge detection via three independent methods.

1. **Longshot bias** — empirical mispricing at price extremes (Becker 2024).
2. **Arbitrage** — YES + NO orderbook violations of the $1 invariant.
3. **Microstructure** — order-book imbalance and spread analysis.

Each method returns a Signal dataclass (or None) when an edge is detected.
The orchestrator ``scan_for_signals`` runs all three on each token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket.client import PolymarketClient
    from .config import Config

logger = logging.getLogger(__name__)

# ── Longshot-bias correction table (Becker 2024, 72.1M Kalshi trades) ──
_BIAS_TABLE: list[tuple[float, float, float]] = [
    # (lo, hi, bias)  — negative = market overprices YES
    (0.00, 0.05, -0.008),
    (0.05, 0.15, -0.005),
    (0.15, 0.25, -0.003),
    (0.75, 0.85, +0.003),
    (0.85, 0.95, +0.005),
    (0.95, 1.00, +0.008),
]


@dataclass
class Signal:
    token_id: str
    side: str              # "BUY" or "SELL"
    fair_value: float      # Our estimated probability
    market_price: float
    edge: float            # fair_value - market_price (or inverse for SELL)
    method: str            # "longshot_bias" | "arbitrage" | "microstructure"
    confidence: float      # 0–1
    meta: dict = field(default_factory=dict)


# ── Method 1: Longshot bias ─────────────────────────────────────────────

def detect_longshot_bias(
    token_id: str, market_price: float, min_edge: float = 0.03,
) -> Signal | None:
    """Detect mispricing from longshot/favourite bias.

    Applies empirical correction from Becker 2024.  Returns a Signal when
    the bias-adjusted fair value diverges from market price by >= *min_edge*.
    """
    bias = 0.0
    for lo, hi, b in _BIAS_TABLE:
        if lo <= market_price <= hi:
            bias = b
            break

    if bias == 0.0:
        return None

    fair_value = market_price - bias  # remove the bias
    edge = abs(fair_value - market_price)

    if edge < min_edge:
        return None

    # Negative bias means market overprices this side → SELL
    side = "SELL" if bias < 0 else "BUY"

    return Signal(
        token_id=token_id,
        side=side,
        fair_value=fair_value,
        market_price=market_price,
        edge=edge,
        method="longshot_bias",
        confidence=min(1.0, edge / 0.02),  # higher edge → more confident
        meta={"bias": bias},
    )


# ── Method 2: YES/NO arbitrage ──────────────────────────────────────────

def detect_arbitrage(
    book_yes: dict, book_no: dict, min_edge_bps: int = 20,
) -> Signal | None:
    """Detect arbitrage when YES + NO prices violate the $1 invariant.

    * best_ask_yes + best_ask_no < 1.0 → buy both = risk-free profit
    * best_bid_yes + best_bid_no > 1.0 → sell both = risk-free profit

    Requires >= *min_edge_bps* basis points after fees.
    """
    asks_yes = book_yes.get("asks") or []
    asks_no = book_no.get("asks") or []
    bids_yes = book_yes.get("bids") or []
    bids_no = book_no.get("bids") or []

    # Buy-both arbitrage
    if asks_yes and asks_no:
        best_ask_yes = float(asks_yes[0]["price"])
        best_ask_no = float(asks_no[0]["price"])
        cost = best_ask_yes + best_ask_no
        if cost < 1.0:
            edge = 1.0 - cost
            if edge * 10_000 >= min_edge_bps:
                # Convention: report the YES side token
                token_id = book_yes.get("asset_id", "")
                return Signal(
                    token_id=token_id,
                    side="BUY",
                    fair_value=best_ask_yes + edge / 2,
                    market_price=best_ask_yes,
                    edge=edge,
                    method="arbitrage",
                    confidence=min(1.0, edge / 0.01),
                    meta={
                        "type": "buy_both",
                        "ask_yes": best_ask_yes,
                        "ask_no": best_ask_no,
                        "cost": cost,
                    },
                )

    # Sell-both arbitrage
    if bids_yes and bids_no:
        best_bid_yes = float(bids_yes[0]["price"])
        best_bid_no = float(bids_no[0]["price"])
        revenue = best_bid_yes + best_bid_no
        if revenue > 1.0:
            edge = revenue - 1.0
            if edge * 10_000 >= min_edge_bps:
                token_id = book_yes.get("asset_id", "")
                return Signal(
                    token_id=token_id,
                    side="SELL",
                    fair_value=best_bid_yes - edge / 2,
                    market_price=best_bid_yes,
                    edge=edge,
                    method="arbitrage",
                    confidence=min(1.0, edge / 0.01),
                    meta={
                        "type": "sell_both",
                        "bid_yes": best_bid_yes,
                        "bid_no": best_bid_no,
                        "revenue": revenue,
                    },
                )

    return None


# ── Method 3: Microstructure ────────────────────────────────────────────

def detect_microstructure_edge(
    book: dict, imbalance_threshold: float = 0.30,
) -> Signal | None:
    """Detect edge from order-book imbalance and spread.

    Imbalance I = (depth_bid - depth_ask) / (depth_bid + depth_ask).
    |I| > threshold signals directional pressure.
    """
    bids = book.get("bids") or []
    asks = book.get("asks") or []

    if not bids or not asks:
        return None

    best_bid = float(bids[0]["price"])
    best_ask = float(asks[0]["price"])
    mid = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid

    if spread <= 0:
        return None

    depth_bid = sum(float(b.get("size", 0)) for b in bids[:5])
    depth_ask = sum(float(a.get("size", 0)) for a in asks[:5])
    total_depth = depth_bid + depth_ask

    if total_depth == 0:
        return None

    imbalance = (depth_bid - depth_ask) / total_depth
    kyle_lambda = spread / total_depth if total_depth > 0 else 0.0

    if abs(imbalance) < imbalance_threshold:
        return None

    # Imbalance > 0 → buying pressure → price likely to rise → BUY
    side = "BUY" if imbalance > 0 else "SELL"
    edge = abs(imbalance) * spread  # proxy for expected move

    token_id = book.get("asset_id", "")
    return Signal(
        token_id=token_id,
        side=side,
        fair_value=mid + (spread * imbalance / 2),
        market_price=mid,
        edge=edge,
        method="microstructure",
        confidence=min(1.0, abs(imbalance)),
        meta={
            "imbalance": round(imbalance, 4),
            "spread": round(spread, 4),
            "kyle_lambda": round(kyle_lambda, 6),
            "depth_bid": round(depth_bid, 2),
            "depth_ask": round(depth_ask, 2),
        },
    )


# ── Orchestrator ─────────────────────────────────────────────────────────

def scan_for_signals(
    client: PolymarketClient, token_ids: list[str], config: Config,
) -> list[Signal]:
    """Run all detection methods on each token and return sorted signals."""
    signals: list[Signal] = []

    for tid in token_ids:
        try:
            price_info = client.get_price(tid)
            price = float(price_info.get("price", 0))
        except Exception:
            logger.debug("Failed to get price for %s, skipping", tid[:16])
            continue

        # Method 1: Longshot bias
        if config.longshot_bias:
            sig = detect_longshot_bias(tid, price, min_edge=config.min_ev_threshold)
            if sig:
                signals.append(sig)

        # Method 2: Arbitrage (needs both YES/NO books — skip for individual tokens)
        # Arbitrage is handled separately in strategy via paired tokens

        # Method 3: Microstructure
        if config.microstructure:
            try:
                book = client.get_orderbook(tid)
                book["asset_id"] = tid
                sig = detect_microstructure_edge(
                    book, imbalance_threshold=config.imbalance_threshold,
                )
                if sig and sig.edge >= config.min_ev_threshold:
                    signals.append(sig)
            except Exception:
                logger.debug("Failed to get book for %s, skipping", tid[:16])

    # Sort by edge * confidence descending
    signals.sort(key=lambda s: s.edge * s.confidence, reverse=True)
    return signals
