"""Trading bot — generic strategy runner using Polymarket CLOB direct."""

from .config import Config
from .scoring import brier_score, calibration_curve, edge_confidence, log_score
from .signals import Signal, scan_for_signals
from .sizing import check_risk_limits, kelly_fraction, position_size
from .state import TradingState, TradeRecord

__all__ = [
    "Config",
    "Signal",
    "TradeRecord",
    "TradingState",
    "brier_score",
    "calibration_curve",
    "check_risk_limits",
    "edge_confidence",
    "kelly_fraction",
    "log_score",
    "position_size",
    "scan_for_signals",
]
