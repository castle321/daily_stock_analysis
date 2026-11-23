# -*- coding: utf-8 -*-
"""Tests for A-share trading-session context and intraday price wording."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from src.analyzer import GeminiAnalyzer
from src.core.pipeline import StockAnalysisPipeline
from src.market_session import get_a_share_market_context


class AShareMarketSessionTests(unittest.TestCase):
    def test_morning_session_is_marked_as_intraday(self):
        context = get_a_share_market_context(datetime(2026, 11, 23, 10, 26))
        self.assertEqual(context["phase"], "morning_session")
        self.assertTrue(context["trading_day_in_progress"])

    def test_lunch_break_is_still_marked_as_intraday_day(self):
        context = get_a_share_market_context(datetime(2026, 11, 23, 12, 0))
        self.assertEqual(context["phase"], "lunch_break")
        self.assertTrue(context["trading_day_in_progress"])

    def test_afternoon_session_is_marked_as_intraday(self):
        context = get_a_share_market_context(datetime(2026, 11, 23, 14, 0))
        self.assertEqual(context["phase"], "afternoon_session")
        self.assertTrue(context["trading_day_in_progress"])

    def test_pre_open_and_after_close_are_not_intraday(self):
        before = get_a_share_market_context(datetime(2026, 11, 23, 9, 0))
        after = get_a_share_market_context(datetime(2026, 11, 23, 15, 1))
        self.assertFalse(before["trading_day_in_progress"])
        self.assertFalse(after["trading_day_in_progress"])
        self.assertEqual(before["phase"], "pre_open")
        self.assertEqual(after["phase"], "post_close")

    def test_weekend_is_not_marked_as_trading_day(self):
        context = get_a_share_market_context(datetime(2026, 11, 22, 10, 0))
        self.assertEqual(context["phase"], "weekend")
        self.assertFalse(context["trading_day_in_progress"])

    def test_market_context_is_attached_to_analysis_context(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        quote = SimpleNamespace(
            name="贵州茅台",
            price=1448.82,
            volume_ratio=2.74,
            turnover_rate=0.22,
            source="tencent",
        )
        with patch(
            "src.market_session.now_china",
            return_value=datetime(2026, 11, 23, 10, 26),
        ):
            enhanced = pipeline._enhance_context(
                context={"code": "600519", "today": {}},
                realtime_quote=quote,
                chip_data=None,
                trend_result=None,
                stock_name="贵州茅台",
            )

        self.assertTrue(enhanced["market_context"]["trading_day_in_progress"])
        self.assertEqual(
            enhanced["market_context"]["phase_label"], "上午交易时段"
        )
        self.assertEqual(enhanced["market_context"]["as_of"], "2026-11-23 10:26:00")

    def test_intraday_prompt_never_labels_daily_close_as_final_close(self):
        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-11-23",
            "today": {
                "close": 1448.82,
                "open": 1475.35,
                "high": 1478.0,
                "low": 1428.0,
                "pct_chg": -2.49,
                "volume": 28000,
                "amount": 4045000000,
            },
            "realtime": {"price": 1448.82},
            "market_context": {
                "as_of": "2026-11-23 10:26:00",
                "phase_label": "上午交易时段",
                "trading_day_in_progress": True,
            },
        }

        prompt = analyzer._format_prompt(context, "贵州茅台")

        self.assertIn("日K最新价（盘中暂值，非收盘价）", prompt)
        self.assertIn("盘中最新价（行情接口快照）", prompt)
        self.assertIn("盘中累计成交量", prompt)
        self.assertIn("严禁称为今日收盘价", prompt)
        self.assertNotIn("| 收盘价 | 1448.82 元 |", prompt)


if __name__ == "__main__":
    unittest.main()
