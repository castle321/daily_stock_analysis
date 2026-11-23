# -*- coding: utf-8 -*-
"""Tests for market-data freshness and decision safety guards."""

import unittest

from src.analyzer import AnalysisResult, GeminiAnalyzer
from src.market_data_quality import assess_market_data_quality
from src.core.pipeline import StockAnalysisPipeline
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch


class MarketDataQualityTests(unittest.TestCase):
    def test_stale_daily_bar_during_current_session_blocks_decisions(self):
        quality = assess_market_data_quality(
            market_context={
                "phase": "session_unverified",
                "session_phase": "lunch_break",
                "as_of": "2026-11-23 12:37:41",
                "data_date": "2026-11-20",
                "daily_bar_matches_today": False,
                "trading_day_in_progress": None,
            },
            daily_data={"date": "2026-11-20", "pct_chg": 1.75},
            realtime_data={
                "price": 1444.84,
                "change_pct": -2.75,
                "source": "tencent",
            },
        )

        self.assertEqual(quality["status"], "blocked")
        self.assertTrue(quality["blocks_actionable_decision"])
        self.assertEqual(quality["daily_bar_date"], "2026-11-20")
        self.assertIn("不能作为当前交易日信号", quality["reason"])

    def test_same_day_percentage_conflict_blocks_decisions(self):
        quality = assess_market_data_quality(
            market_context={
                "phase": "morning_session",
                "as_of": "2026-11-23 10:30:00",
                "data_date": "2026-11-23",
                "daily_bar_matches_today": True,
                "trading_day_in_progress": True,
            },
            daily_data={"date": "2026-11-23", "pct_chg": 1.75},
            realtime_data={"change_pct": -2.75, "source": "tencent"},
        )

        self.assertTrue(quality["blocks_actionable_decision"])
        self.assertIn("明显冲突", quality["reason"])

    def test_does_not_compare_absolute_prices_across_adjustment_bases(self):
        quality = assess_market_data_quality(
            market_context={
                "phase": "morning_session",
                "as_of": "2026-11-23 10:30:00",
                "data_date": "2026-11-23",
                "daily_bar_matches_today": True,
                "trading_day_in_progress": True,
            },
            daily_data={"date": "2026-11-23", "close": 1485.75, "pct_chg": -2.75},
            realtime_data={
                "price": 1444.84,
                "change_pct": -2.70,
                "source": "tencent",
            },
        )

        self.assertFalse(quality["blocks_actionable_decision"])
        self.assertEqual(quality["status"], "ok")

    def test_pipeline_attaches_quality_and_suppresses_stale_trend_signal(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        quote = SimpleNamespace(
            name="贵州茅台",
            price=1444.84,
            change_pct=-2.75,
            volume_ratio=1.74,
            turnover_rate=0.3,
            source="tencent",
        )
        trend = SimpleNamespace(
            trend_status=SimpleNamespace(value="多头排列"),
            ma_alignment="MA5>MA10>MA20",
            trend_strength=75,
            bias_ma5=1.76,
            bias_ma10=2.9,
            volume_status=SimpleNamespace(value="放量上涨"),
            volume_trend="放量",
            buy_signal=SimpleNamespace(value="买入"),
            signal_score=70,
            signal_reasons=["历史均线多头"],
            risk_factors=[],
        )
        context = {
            "code": "600519",
            "date": "2026-11-20",
            "today": {"date": "2026-11-20", "pct_chg": 1.75},
            "yesterday": {"date": "2026-11-19"},
            "volume_change_ratio": 1.57,
            "price_change_ratio": 1.75,
        }

        with patch(
            "src.market_session.now_china",
            return_value=datetime(2026, 11, 23, 12, 37),
        ):
            enhanced = pipeline._enhance_context(
                context=context,
                realtime_quote=quote,
                chip_data=None,
                trend_result=trend,
                stock_name="贵州茅台",
            )

        self.assertTrue(enhanced["market_data_quality"]["blocks_actionable_decision"])
        self.assertNotIn("trend_analysis", enhanced)
        self.assertNotIn("volume_change_ratio", enhanced)
        self.assertNotIn("price_change_ratio", enhanced)
        self.assertEqual(enhanced["realtime"]["change_pct"], -2.75)

    def test_ai_buy_result_is_overridden_when_data_quality_blocks(self):
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=70,
            trend_prediction="看多",
            operation_advice="买入",
            decision_type="buy",
            confidence_level="中",
            dashboard={
                "core_conclusion": {},
                "data_perspective": {
                    "trend_status": {},
                    "price_position": {"current_price": 1485.75},
                    "volume_analysis": {},
                },
                "battle_plan": {
                    "sniper_points": {},
                    "position_strategy": {},
                },
            },
        )
        context = {
            "market_context": {
                "phase": "session_unverified",
                "as_of": "2026-11-23 12:37:41",
                "data_date": "2026-11-20",
            },
            "market_data_quality": {
                "blocks_actionable_decision": True,
                "reason": "最新日K日期为2026-11-20，与分析日期不一致",
            },
            "realtime": {"price": 1444.84},
        }

        guarded = GeminiAnalyzer._apply_market_data_safety_guard(result, context)

        self.assertEqual(guarded.operation_advice, "观望")
        self.assertEqual(guarded.decision_type, "hold")
        self.assertLessEqual(guarded.sentiment_score, 50)
        self.assertIn("数据质量校验未通过", guarded.risk_warning)
        self.assertEqual(
            guarded.dashboard["battle_plan"]["position_strategy"]["suggested_position"],
            "0成（数据核验前暂停新交易）",
        )
        self.assertIn("暂停提供", guarded.dashboard["battle_plan"]["sniper_points"]["ideal_buy"])

    def test_prompt_marks_stale_daily_bar_as_historical_and_blocks_trades(self):
        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-11-20",
            "today": {"close": 1485.75, "pct_chg": 1.75, "ma5": 1460.04},
            "realtime": {"price": 1444.84, "change_pct": -2.75, "source": "tencent"},
            "market_context": {
                "phase": "session_unverified",
                "phase_label": "午间休市（当日交易尚未结束；当日日K日期未核验）",
                "as_of": "2026-11-23 12:37:41",
                "data_date": "2026-11-20",
                "trading_day_in_progress": None,
            },
            "market_data_quality": {
                "blocks_actionable_decision": True,
                "daily_bar_date": "2026-11-20",
                "reason": "最新日K日期为2026-11-20，未覆盖分析日期",
            },
        }

        prompt = analyzer._format_prompt(context, "贵州茅台")

        self.assertIn("最新可用日K收盘价（2026-11-20历史数据，非当前行情）", prompt)
        self.assertIn("实时行情涨跌幅 | -2.75%", prompt)
        self.assertIn("禁止输出买入、加仓、减仓或卖出结论", prompt)
        self.assertIn("不得生成具体建仓价", prompt)
        self.assertIn("历史日K均线系统（截至2026-11-20）", prompt)
        self.assertIn("历史日K量价变化", prompt)


if __name__ == "__main__":
    unittest.main()
