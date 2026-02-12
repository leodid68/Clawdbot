"""Tests for bot.scanner — market scanning and filtering."""

import unittest

from bot.scanner import compute_book_metrics, filter_tradeable, scan_markets


class TestComputeBookMetrics(unittest.TestCase):
    def _book(self, bids, asks):
        return {
            "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
            "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        }

    def test_basic_metrics(self):
        book = self._book(
            bids=[(0.50, 100), (0.49, 50)],
            asks=[(0.52, 80), (0.53, 40)],
        )
        m = compute_book_metrics(book)
        self.assertAlmostEqual(m["mid_price"], 0.51)
        self.assertAlmostEqual(m["spread"], 0.02)
        self.assertEqual(m["depth_bid_5"], 150.0)
        self.assertEqual(m["depth_ask_5"], 120.0)
        self.assertIn(m["liquidity_grade"], ("A", "B", "C", "D"))

    def test_tight_spread_grade_a(self):
        book = self._book(
            bids=[(0.500, 100)],
            asks=[(0.502, 100)],
        )
        m = compute_book_metrics(book)
        # spread = 0.002, mid = 0.501, bps = 0.002/0.501 * 10000 ≈ 39.9
        self.assertEqual(m["liquidity_grade"], "A")

    def test_wide_spread_grade_d(self):
        book = self._book(
            bids=[(0.30, 10)],
            asks=[(0.70, 10)],
        )
        m = compute_book_metrics(book)
        self.assertEqual(m["liquidity_grade"], "D")

    def test_empty_book(self):
        m = compute_book_metrics({"bids": [], "asks": []})
        self.assertEqual(m["liquidity_grade"], "D")
        self.assertEqual(m["mid_price"], 0.0)

    def test_imbalance_direction(self):
        book = self._book(
            bids=[(0.50, 200)],
            asks=[(0.52, 50)],
        )
        m = compute_book_metrics(book)
        self.assertGreater(m["imbalance"], 0)  # more bids

    def test_kyle_lambda(self):
        book = self._book(
            bids=[(0.50, 100)],
            asks=[(0.52, 100)],
        )
        m = compute_book_metrics(book)
        # lambda = spread / total_depth = 0.02 / 200 = 0.0001
        self.assertAlmostEqual(m["kyle_lambda"], 0.0001)


class TestFilterTradeable(unittest.TestCase):
    def test_filter_by_grade(self):
        markets = [
            {"question": "Q1", "liquidity_grade": "A"},
            {"question": "Q2", "liquidity_grade": "B"},
            {"question": "Q3", "liquidity_grade": "C"},
            {"question": "Q4", "liquidity_grade": "D"},
        ]
        result = filter_tradeable(markets, min_liquidity="B")
        self.assertEqual(len(result), 2)  # A and B only

    def test_filter_grade_c(self):
        markets = [
            {"question": "Q1", "liquidity_grade": "A"},
            {"question": "Q2", "liquidity_grade": "D"},
        ]
        result = filter_tradeable(markets, min_liquidity="C")
        self.assertEqual(len(result), 1)

    def test_all_pass(self):
        markets = [{"question": f"Q{i}", "liquidity_grade": "A"} for i in range(5)]
        result = filter_tradeable(markets, min_liquidity="D")
        self.assertEqual(len(result), 5)

    def test_empty(self):
        self.assertEqual(filter_tradeable([]), [])


class TestScanMarkets(unittest.TestCase):
    def test_filters_inactive(self):
        class FakeClient:
            def get_markets(self, **kw):
                return [
                    {"condition_id": "c1", "question": "Q1", "tokens": [{"token_id": "t1"}],
                     "accepting_orders": True, "enable_order_book": True},
                    {"condition_id": "c2", "question": "Q2", "tokens": [{"token_id": "t2"}],
                     "accepting_orders": False},
                    {"condition_id": "c3", "question": "Q3", "tokens": [{"token_id": "t3"}],
                     "closed": True},
                    {"condition_id": "c4", "question": "Q4", "tokens": []},
                ]

        result = scan_markets(FakeClient())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["condition_id"], "c1")


if __name__ == "__main__":
    unittest.main()
