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
    token_id: str
    side: str
    price: float
    size: float
    order_id: str = ""
    neg_risk: bool = False
    timestamp: str = ""
    memo: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class TradingState:
    trades: dict[str, TradeRecord] = field(default_factory=dict)
    last_run: str = ""
    pnl_history: list[dict] = field(default_factory=list)
    predictions: dict[str, dict] = field(default_factory=dict)
    daily_pnl: dict[str, float] = field(default_factory=dict)

    def record_trade(self, **kwargs) -> None:
        kwargs.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        rec = TradeRecord(**kwargs)
        self.trades[rec.market_id] = rec

    def remove_trade(self, market_id: str) -> None:
        self.trades.pop(market_id, None)

    def open_positions(self) -> list[TradeRecord]:
        return list(self.trades.values())

    # ── Calibration tracking ──────────────────────────────────────────

    def record_prediction(
        self, market_id: str, our_prob: float, market_price: float,
    ) -> None:
        self.predictions[market_id] = {
            "our_prob": our_prob,
            "market_price": market_price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resolved": False,
            "outcome": None,
        }

    def resolve_prediction(self, market_id: str, outcome: bool) -> None:
        if market_id in self.predictions:
            self.predictions[market_id]["resolved"] = True
            self.predictions[market_id]["outcome"] = int(outcome)

    def get_calibration(self) -> dict:
        """Compute Brier + Log score on resolved predictions."""
        from .scoring import brier_score, log_score

        preds, outcomes = [], []
        for p in self.predictions.values():
            if p.get("resolved") and p.get("outcome") is not None:
                preds.append(p["our_prob"])
                outcomes.append(p["outcome"])

        if not preds:
            return {"brier": None, "log": None, "n": 0}

        return {
            "brier": round(brier_score(preds, outcomes), 6),
            "log": round(log_score(preds, outcomes), 6),
            "n": len(preds),
        }

    # ── Daily PnL tracking ───────────────────────────────────────────

    def record_daily_pnl(self, amount: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily_pnl[today] = self.daily_pnl.get(today, 0.0) + amount

    def get_today_pnl(self) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.daily_pnl.get(today, 0.0)

    def save(self, path: str) -> None:
        data = {
            "trades": {mid: rec.to_dict() for mid, rec in self.trades.items()},
            "last_run": datetime.now(timezone.utc).isoformat(),
            "pnl_history": self.pnl_history,
            "predictions": self.predictions,
            "daily_pnl": self.daily_pnl,
        }
        dir_name = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_name)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
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
            logger.info("No state file at %s, starting fresh", path)
            return cls()
        try:
            with open(p) as f:
                data = json.load(f)
            trades = {
                mid: TradeRecord.from_dict(rec)
                for mid, rec in data.get("trades", {}).items()
            }
            return cls(
                trades=trades,
                last_run=data.get("last_run", ""),
                pnl_history=data.get("pnl_history", []),
                predictions=data.get("predictions", {}),
                daily_pnl=data.get("daily_pnl", {}),
            )
        except (json.JSONDecodeError, IOError, KeyError) as exc:
            logger.warning("Failed to load state from %s: %s — starting fresh", path, exc)
            return cls()
