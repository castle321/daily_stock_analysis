# -*- coding: utf-8 -*-
"""Regression tests for missing market data handling."""

import unittest
from types import SimpleNamespace

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher
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

    def test_sector_ranking_normalization_filters_invalid_rows_and_sorts(self):
        source = pd.DataFrame([
            {"板块名称": "行业A", "涨跌幅": 2.5},
            {"板块名称": "行业B", "涨跌幅": -3.2},
            {"板块名称": "行业C", "涨跌幅": float("inf")},
            {"板块名称": "", "涨跌幅": 8.0},
            {"板块名称": "行业D", "涨跌幅": "invalid"},
        ])

        top, bottom = AkshareFetcher._normalize_sector_rankings(source, n=2)

        self.assertEqual(top, [{"name": "行业A", "change_pct": 2.5}])
        self.assertEqual(bottom, [{"name": "行业B", "change_pct": -3.2}])

    def test_sector_rankings_falls_back_to_push2_after_akshare_failure(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        expected = (
            [{"name": "行业A", "change_pct": 3.0}],
            [{"name": "行业B", "change_pct": -2.0}],
        )
        calls = []

        def fail_em(n):
            calls.append("em")
            raise RuntimeError("AkShare EM unavailable")

        def push2(n):
            calls.append("push2")
            return expected

        def ths(n):
            calls.append("ths")
            return None

        fetcher._get_sector_rankings_from_em = fail_em
        fetcher._get_sector_rankings_from_push2 = push2
        fetcher._get_sector_rankings_from_ths = ths

        self.assertEqual(fetcher.get_sector_rankings(5), expected)
        self.assertEqual(calls, ["em", "push2"])

    def test_sector_rankings_returns_none_when_all_free_sources_fail(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._get_sector_rankings_from_em = lambda n: None
        fetcher._get_sector_rankings_from_push2 = lambda n: None
        fetcher._get_sector_rankings_from_ths = lambda n: None

        self.assertIsNone(fetcher.get_sector_rankings(5))


if __name__ == "__main__":
    unittest.main()
