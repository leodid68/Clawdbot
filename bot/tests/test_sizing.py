"""Tests for bot.sizing — Kelly criterion and risk management."""

import unittest
from unittest.mock import MagicMock

from bot.sizing import (
    check_risk_limits,
    dynamic_exit_threshold,
    kelly_fraction,
    position_size,
)
from bot.config import Config
from bot.state import TradingState


class TestKellyFraction(unittest.TestCase):
    def test_positive_edge(self):
        # p=0.6, price=0.5 → f* = (0.6-0.5)/(1-0.5) = 0.2
        frac = kelly_fraction(0.6, 0.5, fraction=1.0)
        self.assertAlmostEqual(frac, 0.2)

    def test_quarter_kelly(self):
        frac = kelly_fraction(0.6, 0.5, fraction=0.25)
        self.assertAlmostEqual(frac, 0.05)

    def test_no_edge(self):
        frac = kelly_fraction(0.5, 0.5)
        self.assertEqual(frac, 0.0)

    def test_negative_edge(self):
        frac = kelly_fraction(0.3, 0.5)
        self.assertEqual(frac, 0.0)

    def test_boundary_price_zero(self):
        self.assertEqual(kelly_fraction(0.5, 0.0), 0.0)

    def test_boundary_price_one(self):
        self.assertEqual(kelly_fraction(0.5, 1.0), 0.0)

    def test_boundary_prob_zero(self):
        self.assertEqual(kelly_fraction(0.0, 0.5), 0.0)

    def test_boundary_prob_one(self):
        self.assertEqual(kelly_fraction(1.0, 0.5), 0.0)


class TestPositionSize(unittest.TestCase):
    def test_basic_sizing(self):
        size = position_size(0.7, 0.5, bankroll=100, max_position=20, kelly_frac=1.0)
        # f* = (0.7-0.5)/(1-0.5) = 0.4 → $40, capped at $20
        self.assertAlmostEqual(size, 20.0)

    def test_min_trade_filter(self):
        size = position_size(0.52, 0.5, bankroll=100, max_position=20, min_trade=5.0)
        # Quarter-Kelly of small edge → likely < $5
        self.assertEqual(size, 0.0)

    def test_no_edge(self):
        size = position_size(0.5, 0.5, bankroll=100, max_position=20)
        self.assertEqual(size, 0.0)

    def test_caps_at_bankroll(self):
        size = position_size(0.9, 0.1, bankroll=10, max_position=100, kelly_frac=1.0)
        self.assertLessEqual(size, 10.0)


class TestDynamicExitThreshold(unittest.TestCase):
    def test_far_resolution(self):
        threshold = dynamic_exit_threshold(0.30, hours_to_resolution=100)
        self.assertGreater(threshold, 0.35)

    def test_near_resolution(self):
        threshold = dynamic_exit_threshold(0.30, hours_to_resolution=3)
        near = threshold
        threshold_far = dynamic_exit_threshold(0.30, hours_to_resolution=100)
        self.assertLess(near, threshold_far)

    def test_minimum_profit(self):
        # Always at least 5c above cost
        threshold = dynamic_exit_threshold(0.50, hours_to_resolution=1)
        self.assertGreaterEqual(threshold, 0.55)


class TestCheckRiskLimits(unittest.TestCase):
    def _make_state(self, n_positions=0, today_pnl=0.0):
        state = TradingState()
        for i in range(n_positions):
            state.record_trade(
                market_id=f"m{i}", token_id=f"t{i}",
                side="BUY", price=0.5, size=10,
            )
        if today_pnl != 0:
            state.record_daily_pnl(today_pnl)
        return state

    def test_allowed(self):
        state = self._make_state(n_positions=2)
        config = Config()
        allowed, reason = check_risk_limits(state, config, 5.0)
        self.assertTrue(allowed)

    def test_exposure_limit(self):
        state = self._make_state(n_positions=5)
        config = Config(max_total_exposure=20.0)
        allowed, reason = check_risk_limits(state, config, 5.0)
        self.assertFalse(allowed)
        self.assertIn("exposure", reason.lower())

    def test_position_limit(self):
        state = self._make_state(n_positions=10)
        config = Config(max_open_positions=10)
        allowed, reason = check_risk_limits(state, config, 5.0)
        self.assertFalse(allowed)
        self.assertIn("limit", reason.lower())

    def test_daily_loss_limit(self):
        state = self._make_state(today_pnl=-15.0)
        config = Config(max_daily_loss=10.0)
        allowed, reason = check_risk_limits(state, config, 5.0)
        self.assertFalse(allowed)
        self.assertIn("daily", reason.lower())


if __name__ == "__main__":
    unittest.main()
