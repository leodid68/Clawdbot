"""Persistent trading state between runs."""

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    market_id: str
    outcome_name: str
    side: str
    cost_basis: float
    shares: float
    timestamp: str
    location: str = ""
    forecast_date: str = ""
    forecast_temp: float | None = None

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "outcome_name": self.outcome_name,
            "side": self.side,
            "cost_basis": self.cost_basis,
            "shares": self.shares,
            "timestamp": self.timestamp,
            "location": self.location,
            "forecast_date": self.forecast_date,
            "forecast_temp": self.forecast_temp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        return cls(
            market_id=d["market_id"],
            outcome_name=d.get("outcome_name", ""),
            side=d.get("side", "yes"),
            cost_basis=d.get("cost_basis", 0.0),
            shares=d.get("shares", 0.0),
            timestamp=d.get("timestamp", ""),
            location=d.get("location", ""),
            forecast_date=d.get("forecast_date", ""),
            forecast_temp=d.get("forecast_temp"),
        )


@dataclass
class TradingState:
    trades: dict[str, TradeRecord] = field(default_factory=dict)  # market_id → TradeRecord
    analyzed_markets: set[str] = field(default_factory=set)
    last_run: str = ""

    def record_trade(
        self,
        market_id: str,
        outcome_name: str,
        side: str,
        cost_basis: float,
        shares: float,
        location: str = "",
        forecast_date: str = "",
        forecast_temp: float | None = None,
    ) -> None:
        self.trades[market_id] = TradeRecord(
            market_id=market_id,
            outcome_name=outcome_name,
            side=side,
            cost_basis=cost_basis,
            shares=shares,
            timestamp=datetime.now(timezone.utc).isoformat(),
            location=location,
            forecast_date=forecast_date,
            forecast_temp=forecast_temp,
        )

    def remove_trade(self, market_id: str) -> None:
        self.trades.pop(market_id, None)

    def get_cost_basis(self, market_id: str) -> float | None:
        rec = self.trades.get(market_id)
        return rec.cost_basis if rec else None

    def mark_analyzed(self, market_id: str) -> None:
        self.analyzed_markets.add(market_id)

    def was_analyzed(self, market_id: str) -> bool:
        return market_id in self.analyzed_markets

    def save(self, path: str) -> None:
        """Atomic save: write to temp file then rename (prevents corruption on crash)."""
        data = {
            "trades": {mid: rec.to_dict() for mid, rec in self.trades.items()},
            "analyzed_markets": sorted(self.analyzed_markets),
            "last_run": datetime.now(timezone.utc).isoformat(),
        }
        dir_name = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_name)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)  # Atomic on POSIX
        except BaseException:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.debug("State saved to %s", path)

    @classmethod
    def load(cls, path: str) -> "TradingState":
        p = Path(path)
        if not p.exists():
            logger.info("No state file found at %s, starting fresh", path)
            return cls()
        try:
            with open(p) as f:
                data = json.load(f)
            trades = {
                mid: TradeRecord.from_dict(rec)
                for mid, rec in data.get("trades", {}).items()
            }
            analyzed = set(data.get("analyzed_markets", []))
            return cls(trades=trades, analyzed_markets=analyzed, last_run=data.get("last_run", ""))
        except (json.JSONDecodeError, IOError, KeyError) as exc:
            logger.warning("Failed to load state from %s: %s — starting fresh", path, exc)
            return cls()
