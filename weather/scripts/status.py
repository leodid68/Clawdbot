#!/usr/bin/env python3
"""Simmer Account Status — shows wallet balance, positions, and activity.

Usage:
    python -m weather.scripts.status
    python -m weather.scripts.status --positions
"""

import argparse
import logging
import os
import sys

# Allow running as a standalone script or as a package module
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from weather.simmer_client import SimmerClient

logger = logging.getLogger(__name__)


def format_usd(amount: float) -> str:
    return f"${amount:,.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Simmer account status")
    parser.add_argument("--positions", action="store_true", help="Show detailed positions")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        print("Error: SIMMER_API_KEY environment variable not set")
        print("Get your API key from: https://simmer.markets/dashboard")
        sys.exit(1)

    client = SimmerClient(api_key=api_key)

    logger.info("Fetching account status...")
    portfolio = client.get_portfolio()

    if not portfolio:
        print("Failed to fetch portfolio")
        sys.exit(1)

    balance = portfolio.get("balance_usdc", 0)
    exposure = portfolio.get("total_exposure", 0)
    positions_count = portfolio.get("positions_count", 0)
    pnl_total = portfolio.get("pnl_total")
    pnl_24h = portfolio.get("pnl_24h")

    print("=" * 50)
    print("ACCOUNT SUMMARY")
    print("=" * 50)
    print(f"  Available Balance:  {format_usd(balance)}")
    print(f"  Total Exposure:     {format_usd(exposure)}")
    print(f"  Open Positions:     {positions_count}")

    if pnl_total is not None:
        print(f"  Total PnL:          {format_usd(pnl_total)}")
    if pnl_24h is not None:
        print(f"  24h PnL:            {format_usd(pnl_24h)}")

    concentration = portfolio.get("concentration", {})
    top_market_pct = concentration.get("top_market_pct", 0)
    if top_market_pct > 0.5:
        print(f"\n  WARNING: High concentration: {top_market_pct:.0%} in top market")

    by_source = portfolio.get("by_source", {})
    if by_source:
        print("\n  By Source:")
        for source, data in by_source.items():
            src_positions = data.get("positions", 0)
            src_exposure = data.get("exposure", 0)
            print(f"      {source}: {src_positions} positions, {format_usd(src_exposure)}")

    print("=" * 50)

    if args.positions:
        print("\nOPEN POSITIONS")
        print("=" * 50)
        positions = client.get_positions()

        if not positions:
            print("  No open positions")
        else:
            for pos in positions:
                question = pos.get("question", pos.get("market_id", "Unknown"))
                if len(question) > 50:
                    question = question[:47] + "..."

                shares_yes = pos.get("shares_yes", 0)
                shares_no = pos.get("shares_no", 0)
                current_price = pos.get("current_price", 0)
                cost_basis = pos.get("cost_basis", 0)
                pnl = pos.get("pnl", 0)

                if shares_yes > 0:
                    side, shares = "YES", shares_yes
                elif shares_no > 0:
                    side, shares = "NO", shares_no
                else:
                    continue

                print(f"\n  {question}")
                print(f"    {side}: {shares:.2f} shares, cost ${cost_basis:.2f}")
                print(f"    Current: {current_price:.1%} | PnL: {format_usd(pnl)}")

        print("\n" + "=" * 50)

    if balance == 0:
        print("\nTip: Deposit funds at https://simmer.markets/dashboard")

    print()


if __name__ == "__main__":
    main()
