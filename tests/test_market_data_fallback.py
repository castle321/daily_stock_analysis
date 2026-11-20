# -*- coding: utf-8 -*-
"""Regression tests for missing market data handling."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_template_reports_sector_ranking_source(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        overview = MarketOverview(
            date="2026-11-20",
            top_sectors=[{"name": "行业A", "change_pct": 2.5}],
            sector_source="东方财富-Push2",
        )

        report = analyzer._generate_template_review(overview, [])

        self.assertIn("**数据来源**: 东方财富-Push2", report)

    def test_ai_prompt_forbids_fund_flow_inference_without_data(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        overview = MarketOverview(date="2026-11-19")

        prompt = analyzer._build_review_prompt(overview, [])

        self.assertIn("输入没有资金流向数据时", prompt)
        self.assertIn("不得根据新闻推断当日市场涨跌方向", prompt)
        self.assertIn("后市观察", prompt)


    def test_manager_routes_constituent_count_to_ranking_fetcher(self):
        calls = []

        fetcher = SimpleNamespace(
            name="AkShare",
            priority=1,
            last_sector_source="AkShare-东方财富",
            get_sector_rankings=lambda n: (
                [{"name": "行业A", "change_pct": 3.0}],
                [{"name": "行业B", "change_pct": -2.0}],
            ),
            get_sector_constituent_count=lambda name: (
                calls.append(name) or 2
            ),
        )

        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = [fetcher]
        manager.last_sector_source = None

        manager.get_sector_rankings(5)
        count = manager.get_sector_constituent_count("行业A")

        self.assertEqual(count, 2)
        self.assertEqual(calls, ["行业A"])

    def test_manager_skips_constituent_count_for_ths_source(self):
        calls = []

        fetcher = SimpleNamespace(
            name="AkShare",
            priority=1,
            last_sector_source="AkShare-同花顺",
            get_sector_rankings=lambda n: (
                [{"name": "行业A", "change_pct": 3.0}],
                [],
            ),
            get_sector_constituent_count=lambda name: (
                calls.append(name) or 2
            ),
        )

        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = [fetcher]
        manager.last_sector_source = None

        manager.get_sector_rankings(5)
        count = manager.get_sector_constituent_count("行业A")

        self.assertIsNone(count)
        self.assertEqual(calls, [])

    def test_analyzer_queries_only_top_three_and_bottom_three_unique_sectors(self):
        calls = []

        top = [
            {"name": "领涨A", "change_pct": 5.0},
            {"name": "领涨B", "change_pct": 4.0},
            {"name": "领涨C", "change_pct": 3.0},
            {"name": "领涨D", "change_pct": 2.0},
        ]

        bottom = [
            {"name": "领跌A", "change_pct": -5.0},
            {"name": "领跌B", "change_pct": -4.0},
            {"name": "领涨B", "change_pct": -3.0},
            {"name": "领跌D", "change_pct": -2.0},
        ]

        manager = SimpleNamespace(
            last_sector_source="AkShare-东方财富",
            get_sector_rankings=lambda n: (top, bottom),
            get_sector_constituent_count=lambda name: (
                calls.append(name) or 2
            ),
        )

        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        analyzer.data_manager = manager

        overview = MarketOverview(date="2026-11-20")
        analyzer._get_sector_rankings(overview)

        self.assertEqual(
            calls,
            ["领涨A", "领涨B", "领涨C", "领跌A", "领跌B"],
        )
        self.assertEqual(len(calls), 5)
        self.assertTrue(
            all(
                sector.get("small_sample") is True
                for sector in overview.top_sectors[:3]
                + overview.bottom_sectors[:3]
            )
        )
        self.assertNotIn("constituent_count", overview.top_sectors[3])
        self.assertNotIn("constituent_count", overview.bottom_sectors[3])


    def test_sector_constituent_count_returns_unique_stock_count(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._enforce_rate_limit = lambda: None

        source = pd.DataFrame({
            "代码": ["301313", "603466", "301313"],
            "名称": ["凡拓数创", "风语筑", "凡拓数创"],
        })

        with patch(
            "akshare.stock_board_industry_cons_em",
            return_value=source,
        ):
            count = fetcher.get_sector_constituent_count("其他数字媒体")

        self.assertEqual(count, 2)

        
    def test_sector_constituent_count_uses_cache(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._enforce_rate_limit = lambda: None

        source = pd.DataFrame({
            "代码": ["301313", "603466"],
            "名称": ["凡拓数创", "风语筑"],
        })

        with patch(
            "akshare.stock_board_industry_cons_em",
            return_value=source,
        ) as mocked_api:
            first_count = fetcher.get_sector_constituent_count(
                "缓存测试行业"
            )
            second_count = fetcher.get_sector_constituent_count(
                "缓存测试行业"
            )

        self.assertEqual(first_count, 2)
        self.assertEqual(second_count, 2)
        self.assertEqual(mocked_api.call_count, 1)

    def test_sector_constituent_count_does_not_cache_failures(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._enforce_rate_limit = lambda: None

        with patch(
            "akshare.stock_board_industry_cons_em",
            side_effect=[
                RuntimeError("接口超时"),
                pd.DataFrame({
                    "代码": ["301313"],
                    "名称": ["凡拓数创"],
                }),
            ],
        ) as mocked_api:
            first_count = fetcher.get_sector_constituent_count(
                "失败重试测试行业"
            )
            second_count = fetcher.get_sector_constituent_count(
                "失败重试测试行业"
            )

        self.assertIsNone(first_count)
        self.assertEqual(second_count, 1)
        self.assertEqual(mocked_api.call_count, 2)


    def test_sector_constituent_count_returns_none_on_empty_data(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._enforce_rate_limit = lambda: None

        with patch(
            "akshare.stock_board_industry_cons_em",
            return_value=pd.DataFrame(),
        ):
            count = fetcher.get_sector_constituent_count("空板块")

        self.assertIsNone(count)

    def test_sector_constituent_count_returns_none_on_api_error(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._enforce_rate_limit = lambda: None

        with patch(
            "akshare.stock_board_industry_cons_em",
            side_effect=RuntimeError("接口超时"),
        ):
            count = fetcher.get_sector_constituent_count("行业A")

        self.assertIsNone(count)

    def test_small_sample_sector_is_marked_in_template(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)

        overview = MarketOverview(
            date="2026-11-20",
            top_sectors=[
                {
                    "name": "其他数字媒体",
                    "change_pct": 15.0,
                    "constituent_count": 2,
                    "small_sample": True,
                },
            ],
        )

        report = analyzer._generate_template_review(overview, [])

        self.assertIn("仅2只成分股", report)
        self.assertIn("小样本", report)
        self.assertIn("参考意义有限", report)

    def test_ai_prompt_mentions_small_sample_sector(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)

        overview = MarketOverview(
            date="2026-11-20",
            top_sectors=[
                {
                    "name": "其他数字媒体",
                    "change_pct": 15.0,
                    "constituent_count": 2,
                    "small_sample": True,
                },
            ],
        )

        prompt = analyzer._build_review_prompt(overview, [])

        self.assertIn("其他数字媒体(+15.00%)", prompt)
        self.assertIn("仅2只成分股", prompt)
        self.assertIn("代表性有限", prompt)

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
            fetcher.last_sector_source = "东方财富-Push2"
            return expected

        def ths(n):
            calls.append("ths")
            return None

        fetcher._get_sector_rankings_from_em = fail_em
        fetcher._get_sector_rankings_from_push2 = push2
        fetcher._get_sector_rankings_from_ths = ths

        self.assertEqual(fetcher.get_sector_rankings(5), expected)
        self.assertEqual(fetcher.last_sector_source, "东方财富-Push2")
        self.assertEqual(calls, ["em", "push2"])

    def test_push2_keeps_top_rankings_when_bottom_request_fails(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._enforce_rate_limit = lambda: None

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        top_payload = {
            "rc": 0,
            "data": {
                "diff": [
                    {"f12": "BK001", "f14": "行业A", "f3": 5.2},
                    {"f12": "BK002", "f14": "行业B", "f3": 3.1},
                ]
            },
        }

        with patch(
            "requests.get",
            side_effect=[FakeResponse(top_payload), RuntimeError("跌幅榜请求超时")],
        ) as mocked_get:
            top, bottom = fetcher._get_sector_rankings_from_push2(2)

        self.assertEqual(
            top,
            [
                {"name": "行业A", "change_pct": 5.2},
                {"name": "行业B", "change_pct": 3.1},
            ],
        )
        self.assertEqual(bottom, [])
        self.assertEqual(mocked_get.call_count, 2)

    def test_push2_keeps_bottom_rankings_when_top_request_fails(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._enforce_rate_limit = lambda: None

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "rc": 0,
                    "data": {
                        "diff": [
                            {"f12": "BK003", "f14": "行业C", "f3": -4.5},
                            {"f12": "BK004", "f14": "行业D", "f3": -2.8},
                        ]
                    },
                }

        with patch(
            "requests.get",
            side_effect=[RuntimeError("涨幅榜请求超时"), FakeResponse()],
        ) as mocked_get:
            top, bottom = fetcher._get_sector_rankings_from_push2(2)

        self.assertEqual(top, [])
        self.assertEqual(
            bottom,
            [
                {"name": "行业C", "change_pct": -4.5},
                {"name": "行业D", "change_pct": -2.8},
            ],
        )
        self.assertEqual(mocked_get.call_count, 2)

    def test_sector_rankings_returns_none_when_all_free_sources_fail(self):
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._get_sector_rankings_from_em = lambda n: None
        fetcher._get_sector_rankings_from_push2 = lambda n: None
        fetcher._get_sector_rankings_from_ths = lambda n: None

        self.assertIsNone(fetcher.get_sector_rankings(5))


if __name__ == "__main__":
    unittest.main()
