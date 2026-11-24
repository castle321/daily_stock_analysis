# -*- coding: utf-8 -*-
"""Regression tests ensuring market review reports retain sourced news."""

import unittest
from types import SimpleNamespace

from src.market_analyzer import MarketAnalyzer, MarketOverview


class MarketNewsReportTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        self.overview = MarketOverview(
            date="2026-11-24",
            capture_time="2026-11-24 15:05:00",
        )
        self.news = [
            SimpleNamespace(
                title="液冷产业链出现新订单",
                snippet="新闻摘要内容",
                source="新浪财经",
                published_date="2026-11-24T06:30:00+00:00",
                url="https://example.com/news/1",
            )
        ]

    def test_template_report_includes_news_source_time_and_link(self):
        report = self.analyzer._generate_template_review(
            self.overview,
            self.news,
        )

        self.assertIn("新闻与事件驱动", report)
        self.assertIn("新浪财经", report)
        self.assertIn("液冷产业链出现新订单", report)
        self.assertIn("2026-11-24T06:30:00+00:00", report)
        self.assertIn("https://example.com/news/1", report)

    def test_ai_prompt_requires_fact_check_and_market_cross_validation(self):
        prompt = self.analyzer._build_review_prompt(
            self.overview,
            self.news,
        )

        self.assertIn("新闻与事件驱动", prompt)
        self.assertIn("已确认事实与媒体推测", prompt)
        self.assertIn("原文链接", prompt)
        self.assertIn("是否已被当日行情验证", prompt)


if __name__ == "__main__":
    unittest.main()
