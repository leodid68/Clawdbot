"""Tests for bot.gamma — Gamma API client, parsing, multi-choice grouping, and resolution."""

import unittest

from bot.gamma import (
    GammaMarket,
    MultiChoiceGroup,
    _parse_market,
    gamma_to_scanner_format,
    group_multi_choice,
    resolve_pending_predictions,
)
from bot.signals import detect_multi_choice_arbitrage
from bot.state import TradingState


class TestParseMarket(unittest.TestCase):
    def _raw(self, **overrides):
        base = {
            "id": "12345",
            "question": "Will X happen?",
            "conditionId": "0xabc",
            "slug": "will-x-happen",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.60", "0.40"]',
            "clobTokenIds": '["tok_yes", "tok_no"]',
            "volume": "50000",
            "volume24hr": "1200",
            "liquidity": "3000",
            "bestBid": "0.58",
            "bestAsk": "0.62",
            "endDate": "2026-06-01T00:00:00Z",
            "active": True,
            "closed": False,
            "negRisk": False,
            "groupItemTitle": "",
            "events": [{"id": "100", "title": "Event X"}],
        }
        base.update(overrides)
        return base

    def test_basic_parsing(self):
        gm = _parse_market(self._raw())
        self.assertEqual(gm.id, "12345")
        self.assertEqual(gm.question, "Will X happen?")
        self.assertEqual(gm.condition_id, "0xabc")
        self.assertEqual(gm.outcomes, ["Yes", "No"])
        self.assertAlmostEqual(gm.outcome_prices[0], 0.60)
        self.assertAlmostEqual(gm.outcome_prices[1], 0.40)
        self.assertEqual(gm.clob_token_ids, ["tok_yes", "tok_no"])
        self.assertAlmostEqual(gm.volume, 50000.0)
        self.assertAlmostEqual(gm.volume_24hr, 1200.0)
        self.assertAlmostEqual(gm.liquidity, 3000.0)
        self.assertAlmostEqual(gm.best_bid, 0.58)
        self.assertAlmostEqual(gm.best_ask, 0.62)
        self.assertAlmostEqual(gm.spread, 0.04)
        self.assertFalse(gm.neg_risk)
        self.assertEqual(gm.event_id, "100")
        self.assertEqual(gm.event_title, "Event X")

    def test_list_format_prices(self):
        """Gamma sometimes returns lists instead of JSON strings."""
        gm = _parse_market(self._raw(
            outcomes=["Yes", "No"],
            outcomePrices=["0.70", "0.30"],
            clobTokenIds=["t1", "t2"],
        ))
        self.assertEqual(gm.outcomes, ["Yes", "No"])
        self.assertAlmostEqual(gm.outcome_prices[0], 0.70)

    def test_empty_events(self):
        gm = _parse_market(self._raw(events=[]))
        self.assertEqual(gm.event_id, "")
        self.assertEqual(gm.event_title, "")

    def test_missing_prices(self):
        gm = _parse_market(self._raw(bestBid=None, bestAsk=None))
        self.assertEqual(gm.best_bid, 0.0)
        self.assertEqual(gm.best_ask, 0.0)
        self.assertEqual(gm.spread, 0.0)

    def test_neg_risk(self):
        gm = _parse_market(self._raw(negRisk=True))
        self.assertTrue(gm.neg_risk)


class TestGroupMultiChoice(unittest.TestCase):
    def _make_gm(self, event_id, yes_price, **kw):
        return GammaMarket(
            id=kw.get("id", "1"),
            question=kw.get("question", "Q?"),
            condition_id=kw.get("condition_id", "0x1"),
            slug="slug",
            outcomes=["Yes", "No"],
            outcome_prices=[yes_price, 1.0 - yes_price],
            clob_token_ids=["tok_yes", "tok_no"],
            volume=10000,
            volume_24hr=500,
            liquidity=1000,
            best_bid=yes_price - 0.01,
            best_ask=yes_price + 0.01,
            spread=0.02,
            end_date="2026-12-31",
            active=True,
            closed=False,
            neg_risk=True,
            group_item_title=kw.get("group_item_title", ""),
            event_id=event_id,
            event_title=kw.get("event_title", "Test Event"),
        )

    def test_groups_by_event(self):
        markets = [
            self._make_gm("ev1", 0.30, id="1", group_item_title="<250k"),
            self._make_gm("ev1", 0.50, id="2", group_item_title="250-500k"),
            self._make_gm("ev1", 0.20, id="3", group_item_title=">500k"),
            self._make_gm("ev2", 0.60, id="4"),
            self._make_gm("ev2", 0.40, id="5"),
        ]
        groups = group_multi_choice(markets)
        self.assertEqual(len(groups), 2)

    def test_yes_sum_correct(self):
        markets = [
            self._make_gm("ev1", 0.30),
            self._make_gm("ev1", 0.50),
            self._make_gm("ev1", 0.20),
        ]
        groups = group_multi_choice(markets)
        self.assertEqual(len(groups), 1)
        self.assertAlmostEqual(groups[0].yes_sum, 1.0)
        self.assertAlmostEqual(groups[0].deviation, 0.0)

    def test_underpriced_group(self):
        markets = [
            self._make_gm("ev1", 0.25),
            self._make_gm("ev1", 0.40),
            self._make_gm("ev1", 0.15),
        ]
        groups = group_multi_choice(markets)
        self.assertAlmostEqual(groups[0].yes_sum, 0.80)
        self.assertAlmostEqual(groups[0].deviation, -0.20)

    def test_overpriced_group(self):
        markets = [
            self._make_gm("ev1", 0.40),
            self._make_gm("ev1", 0.50),
            self._make_gm("ev1", 0.20),
        ]
        groups = group_multi_choice(markets)
        self.assertAlmostEqual(groups[0].yes_sum, 1.10)
        self.assertAlmostEqual(groups[0].deviation, 0.10)

    def test_single_market_excluded(self):
        markets = [self._make_gm("ev1", 0.50)]
        groups = group_multi_choice(markets)
        self.assertEqual(len(groups), 0)

    def test_non_neg_risk_excluded(self):
        m = self._make_gm("ev1", 0.50)
        m.neg_risk = False
        groups = group_multi_choice([m, self._make_gm("ev1", 0.30)])
        # Only 1 neg_risk market → group has < 2 members
        self.assertEqual(len(groups), 0)

    def test_sorted_by_deviation(self):
        markets = [
            self._make_gm("ev1", 0.30, event_title="Small dev"),
            self._make_gm("ev1", 0.71, event_title="Small dev"),
            self._make_gm("ev2", 0.60, event_title="Big dev"),
            self._make_gm("ev2", 0.60, event_title="Big dev"),
        ]
        groups = group_multi_choice(markets)
        # ev2 has deviation 0.20, ev1 has 0.01
        self.assertGreater(abs(groups[0].deviation), abs(groups[1].deviation))


class TestMultiChoiceArbitrage(unittest.TestCase):
    def _make_group(self, yes_prices, event_title="Test"):
        markets = []
        for i, yp in enumerate(yes_prices):
            markets.append(GammaMarket(
                id=str(i),
                question=f"Outcome {i}?",
                condition_id=f"0x{i}",
                slug=f"outcome-{i}",
                outcomes=["Yes", "No"],
                outcome_prices=[yp, 1.0 - yp],
                clob_token_ids=[f"yes_{i}", f"no_{i}"],
                volume=10000,
                volume_24hr=500,
                liquidity=1000,
                best_bid=yp - 0.01,
                best_ask=yp + 0.01,
                spread=0.02,
                end_date="2026-12-31",
                active=True,
                closed=False,
                neg_risk=True,
                group_item_title=f"Option {i}",
                event_id="ev1",
                event_title=event_title,
            ))
        yes_sum = sum(yes_prices)
        return MultiChoiceGroup(
            event_id="ev1",
            event_title=event_title,
            markets=markets,
            yes_sum=round(yes_sum, 4),
            deviation=round(yes_sum - 1.0, 4),
        )

    def test_no_arb_when_fair(self):
        group = self._make_group([0.30, 0.50, 0.20])
        signals = detect_multi_choice_arbitrage(group)
        self.assertEqual(len(signals), 0)

    def test_buy_all_yes_when_underpriced(self):
        # Sum = 0.80, deviation = -0.20 → buy all YES
        group = self._make_group([0.25, 0.35, 0.20])
        signals = detect_multi_choice_arbitrage(group)
        self.assertGreater(len(signals), 0)
        for s in signals:
            self.assertEqual(s.side, "BUY")
            self.assertEqual(s.method, "multi_choice_arb")
            self.assertEqual(s.meta["type"], "buy_all_yes")
            self.assertTrue(s.token_id.startswith("yes_"))

    def test_buy_all_no_when_overpriced(self):
        # Sum = 1.30, deviation = +0.30 → sell all YES (buy NOs)
        group = self._make_group([0.50, 0.50, 0.30])
        signals = detect_multi_choice_arbitrage(group)
        self.assertGreater(len(signals), 0)
        for s in signals:
            self.assertEqual(s.side, "BUY")
            self.assertEqual(s.method, "multi_choice_arb")
            self.assertEqual(s.meta["type"], "buy_all_no")
            self.assertTrue(s.token_id.startswith("no_"))

    def test_edge_per_outcome(self):
        # 3 outcomes, deviation = -0.30, edge_per = 0.10
        group = self._make_group([0.20, 0.30, 0.20])
        signals = detect_multi_choice_arbitrage(group)
        self.assertEqual(len(signals), 3)
        for s in signals:
            self.assertAlmostEqual(s.edge, 0.10)

    def test_below_min_edge_ignored(self):
        # Sum ≈ 1.0, tiny deviation
        group = self._make_group([0.333, 0.334, 0.333])
        signals = detect_multi_choice_arbitrage(group, min_edge_bps=20)
        self.assertEqual(len(signals), 0)

    def test_confidence_scales_with_deviation(self):
        group = self._make_group([0.10, 0.10, 0.10])  # sum=0.30, dev=-0.70
        signals = detect_multi_choice_arbitrage(group)
        self.assertGreater(len(signals), 0)
        self.assertEqual(signals[0].confidence, 1.0)  # capped at 1.0

    def test_meta_contains_event_info(self):
        group = self._make_group([0.20, 0.30, 0.20], event_title="Test Event")
        signals = detect_multi_choice_arbitrage(group)
        for s in signals:
            self.assertEqual(s.meta["event_title"], "Test Event")
            self.assertEqual(s.meta["n_outcomes"], 3)
            self.assertAlmostEqual(s.meta["yes_sum"], 0.70)

    # ── Fee rate tests (Bug 4) ──

    def test_fees_eliminate_small_arb(self):
        """Bug 4: fee_rate should eliminate marginal arb opportunities."""
        # 3 outcomes, deviation = -0.06, edge_per = 0.02
        group = self._make_group([0.30, 0.34, 0.30])
        # Without fees: edge_per = 0.02 > 0 → signals
        signals = detect_multi_choice_arbitrage(group, fee_rate=0.0)
        self.assertGreater(len(signals), 0)

        # With 2% fee: net_edge = 0.02 - 0.02 = 0 → no signals
        signals = detect_multi_choice_arbitrage(group, fee_rate=0.02)
        self.assertEqual(len(signals), 0)

    def test_fees_reduce_edge(self):
        """With fees, edge_per_outcome is reduced by fee_rate."""
        # 3 outcomes, deviation = -0.30, edge_per = 0.10
        group = self._make_group([0.20, 0.30, 0.20])
        # fee_rate = 0.02 → net_edge = 0.10 - 0.02 = 0.08
        signals = detect_multi_choice_arbitrage(group, fee_rate=0.02)
        self.assertEqual(len(signals), 3)
        for s in signals:
            self.assertAlmostEqual(s.edge, 0.08)

    def test_fees_from_config_bps(self):
        """polymarket_fee_bps=200 → fee_rate=0.02."""
        from bot.config import Config
        config = Config(polymarket_fee_bps=200)
        fee_rate = config.polymarket_fee_bps / 10_000
        self.assertAlmostEqual(fee_rate, 0.02)


class TestResolvePendingPredictions(unittest.TestCase):
    """Bug 7: Test that predictions are resolved via Gamma check_resolution."""

    class FakeGamma:
        def __init__(self, resolutions):
            self._resolutions = resolutions

        def check_resolution(self, condition_id):
            return self._resolutions.get(condition_id)

    def test_resolves_closed_markets(self):
        state = TradingState()
        state.record_prediction("cid1", 0.7, 0.5)
        state.record_prediction("cid2", 0.3, 0.4)

        gamma = self.FakeGamma({
            "cid1": {"resolved": True, "outcome": True},
            "cid2": None,  # still open
        })

        count = resolve_pending_predictions(state, gamma)
        self.assertEqual(count, 1)
        self.assertTrue(state.predictions["cid1"]["resolved"])
        self.assertEqual(state.predictions["cid1"]["outcome"], 1)
        self.assertFalse(state.predictions["cid2"]["resolved"])

    def test_no_double_resolve(self):
        state = TradingState()
        state.record_prediction("cid1", 0.7, 0.5)
        state.resolve_prediction("cid1", True)

        gamma = self.FakeGamma({"cid1": {"resolved": True, "outcome": False}})
        count = resolve_pending_predictions(state, gamma)
        # Already resolved → should not re-resolve
        self.assertEqual(count, 0)
        self.assertEqual(state.predictions["cid1"]["outcome"], 1)  # stays True

    def test_empty_predictions(self):
        state = TradingState()
        gamma = self.FakeGamma({})
        count = resolve_pending_predictions(state, gamma)
        self.assertEqual(count, 0)

    def test_outcome_false(self):
        state = TradingState()
        state.record_prediction("cid1", 0.2, 0.3)
        gamma = self.FakeGamma({"cid1": {"resolved": True, "outcome": False}})
        count = resolve_pending_predictions(state, gamma)
        self.assertEqual(count, 1)
        self.assertEqual(state.predictions["cid1"]["outcome"], 0)

    def test_calibration_works_after_resolve(self):
        """After resolving, calibration scores should be computable."""
        state = TradingState()
        state.record_prediction("cid1", 0.8, 0.5)
        state.record_prediction("cid2", 0.2, 0.4)

        gamma = self.FakeGamma({
            "cid1": {"resolved": True, "outcome": True},
            "cid2": {"resolved": True, "outcome": False},
        })
        resolve_pending_predictions(state, gamma)

        cal = state.get_calibration()
        self.assertEqual(cal["n"], 2)
        self.assertIsNotNone(cal["brier"])
        self.assertIsNotNone(cal["log"])


class TestGammaToScannerFormat(unittest.TestCase):
    def _make_gm(self):
        return GammaMarket(
            id="123",
            question="Will X?",
            condition_id="0xabc",
            slug="will-x",
            outcomes=["Yes", "No"],
            outcome_prices=[0.60, 0.40],
            clob_token_ids=["tok_yes", "tok_no"],
            volume=50000,
            volume_24hr=1200,
            liquidity=3000,
            best_bid=0.59,
            best_ask=0.61,
            spread=0.02,
            end_date="2026-12-31",
            active=True,
            closed=False,
            neg_risk=False,
            group_item_title="",
            event_id="100",
            event_title="Event X",
        )

    def test_format_conversion(self):
        result = gamma_to_scanner_format([self._make_gm()])
        self.assertEqual(len(result), 1)
        m = result[0]
        self.assertEqual(m["condition_id"], "0xabc")
        self.assertEqual(m["question"], "Will X?")
        self.assertEqual(len(m["tokens"]), 2)
        self.assertEqual(m["tokens"][0]["token_id"], "tok_yes")
        self.assertEqual(m["tokens"][0]["outcome"], "Yes")
        self.assertAlmostEqual(m["tokens"][0]["price"], 0.60)

    def test_gamma_metadata(self):
        result = gamma_to_scanner_format([self._make_gm()])
        gamma = result[0]["gamma"]
        self.assertAlmostEqual(gamma["volume"], 50000)
        self.assertAlmostEqual(gamma["volume_24hr"], 1200)
        self.assertAlmostEqual(gamma["liquidity"], 3000)
        self.assertEqual(gamma["event_id"], "100")

    def test_liquidity_grade_from_spread(self):
        gm = self._make_gm()
        # spread=0.02, mid=0.60, bps = 0.02/0.60 * 10000 ≈ 333 → grade D
        result = gamma_to_scanner_format([gm])
        self.assertEqual(result[0]["liquidity_grade"], "D")

    def test_tight_spread_grade_a(self):
        gm = self._make_gm()
        gm.best_bid = 0.599
        gm.best_ask = 0.601
        gm.spread = 0.002
        result = gamma_to_scanner_format([gm])
        # bps = 0.002 / 0.600 * 10000 ≈ 33.3 → A
        self.assertEqual(result[0]["liquidity_grade"], "A")


if __name__ == "__main__":
    unittest.main()
