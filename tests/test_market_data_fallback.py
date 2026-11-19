# -*- coding: utf-8 -*-
"""Regression tests for missing market data handling."""

import unittest
from types import SimpleNamespace

from data_provider.base import DataFetcherManager
from src.market_analyzer import MarketAnalyzer, MarketOverview


class MarketDataFallbackTests(unittest.TestCase):
    def test_invalid_market_stats_falls_through_to_valid_provider(self):
        invalid = SimpleNamespace(
            name="invalid",
            get_market_stats=lambda: {
                "up_count": 100, "down_count": 200, "flat_count": 10,
                "total_amount": float("inf"),
            },
        )
        valid_stats = {
            "up_count": 100, "down_count": 200, "flat_count": 10,
            "total_amount": 1234.5,
        }
        valid = SimpleNamespace(
            name="valid",
            get_market_stats=lambda: valid_stats,
        )

        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = [invalid, valid]

        self.assertEqual(manager.get_market_stats(), valid_stats)

    def test_index_data_is_merged_and_invalid_prices_are_ignored(self):
        first = SimpleNamespace(
            name="first",
            get_main_indices=lambda: [
                {"code": "000001.SH", "current": 3000, "prev_close": 2990},
                {"code": "399001.SZ", "current": 0, "prev_close": 10000},
            ],
        )
        second = SimpleNamespace(
            name="second",
            get_main_indices=lambda: [
                {"code": "000001.SH", "current": 3010, "prev_close": 3000},
                {"code": "399001.SZ", "current": 10010, "prev_close": 10000},
            ],
        )

        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = [first, second]

        indices = manager.get_main_indices()
        by_code = {item["code"]: item for item in indices}

        self.assertEqual(by_code["sh000001"]["current"], 3000)
        self.assertEqual(by_code["sh000001"]["change_pct"], (3000 - 2990) / 2990 * 100)
        self.assertEqual(by_code["sz399001"]["current"], 10010)

    def test_template_does_not_invent_market_mood_without_indices(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        overview = MarketOverview(date="2026-11-19")

        report = analyzer._generate_template_review(overview, [])

        self.assertIn("行情数据缺失，暂无法判断", report)
        self.assertIn("指数行情未获取", report)
        self.assertIn("**领涨**: 暂无数据", report)
        self.assertNotIn("震荡整理", report)

    def test_ai_prompt_forbids_fund_flow_inference_without_data(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        overview = MarketOverview(date="2026-11-19")

        prompt = analyzer._build_review_prompt(overview, [])

        self.assertIn("输入没有资金流向数据时", prompt)
        self.assertIn("不得根据新闻推断当日市场涨跌方向", prompt)
        self.assertIn("后市观察", prompt)


if __name__ == "__main__":
    unittest.main()
