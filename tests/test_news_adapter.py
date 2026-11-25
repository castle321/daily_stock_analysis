# -*- coding: utf-8 -*-
"""Tests for the RSS adapter used by market review."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from src.news_feed import (
    NewsArticle,
    RssNewsSearchAdapter,
)
from src.search_service import SearchResponse, SearchResult


class RssNewsSearchAdapterTests(unittest.TestCase):
    def test_rss_is_loaded_once_and_fallback_search_is_preserved(self):
        article = NewsArticle(
            title="宏观政策新闻",
            summary="政策摘要",
            url="https://example.com/news/1",
            source="测试 RSS",
            category="macro",
            published_at="2026-11-24T01:30:00+00:00",
        )
        collector = Mock()
        collector.collect_once.return_value = [article]
        store = Mock()
        fallback = Mock()
        fallback.is_available = True
        fallback.search_stock_news.return_value = SearchResponse(
            query="A股",
            results=[
                SearchResult(
                    title="搜索补充新闻",
                    snippet="补充摘要",
                    url="https://example.com/search/1",
                    source="搜索服务",
                )
            ],
            provider="Tavily",
        )
        adapter = RssNewsSearchAdapter(
            fallback=fallback,
            collector=collector,
            store=store,
        )

        first = adapter.search_stock_news(
            "market", "大盘", max_results=3, focus_keywords=["A股"]
        )
        second = adapter.search_stock_news(
            "market", "大盘", max_results=3, focus_keywords=["股市"]
        )

        self.assertEqual(
            [item.title for item in first.results],
            ["宏观政策新闻", "搜索补充新闻"],
        )
        self.assertEqual(
            first.results[0].published_date,
            "2026-11-24 09:30:00 北京时间",
        )
        self.assertEqual(
            [item.title for item in second.results],
            ["搜索补充新闻"],
        )
        collector.collect_once.assert_called_once()
        store.save_many.assert_called_once_with([article])
        self.assertEqual(fallback.search_stock_news.call_count, 2)

    def test_stock_intel_includes_exact_name_rss_matches_without_api_keys(self):
        article = NewsArticle(
            title="英维克液冷产品订单进展",
            summary="英维克相关订单新闻",
            url="https://example.com/yingweike",
            source="测试 RSS",
            category="stock",
        )
        collector = Mock()
        collector.collect_once.return_value = [article]
        adapter = RssNewsSearchAdapter(
            collector=collector,
            store=Mock(),
        )

        result = adapter.search_comprehensive_intel(
            stock_code="002837",
            stock_name="英维克",
        )
        report = adapter.format_intel_report(result, "英维克")

        self.assertIn("latest_news", result)
        self.assertEqual(result["latest_news"].results[0].title, article.title)
        self.assertIn("英维克液冷产品订单进展", report)
        self.assertIn("测试 RSS", report)

    def test_stale_rss_items_are_excluded_and_recent_sqlite_items_are_reused(self):
        stale_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        fresh_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        stale_article = NewsArticle(
            title="五天前的旧新闻",
            summary="旧摘要",
            url="https://example.com/old",
            source="测试 RSS",
            category="market",
            published_at=stale_time,
        )
        collector = Mock()
        collector.collect_once.return_value = [stale_article]
        store = Mock()
        store.recent.return_value = [
            {
                "title": "数据库中的新新闻",
                "summary": "缓存摘要",
                "url": "https://example.com/cached",
                "source": "缓存来源",
                "published_at": fresh_time,
                "collected_at": fresh_time,
            }
        ]
        adapter = RssNewsSearchAdapter(collector=collector, store=store)

        response = adapter.search_stock_news("market", "大盘", max_results=5)

        self.assertEqual(
            [item.title for item in response.results],
            ["数据库中的新新闻"],
        )
        self.assertNotIn("五天前的旧新闻", [item.title for item in response.results])
        store.recent.assert_called_once_with(limit=300)

    def test_short_stock_name_in_summary_alone_does_not_match(self):
        article = NewsArticle(
            title="市场观察：行业走势分析",
            summary="文章提到平安，但并非相关公司新闻",
            url="https://example.com/unrelated",
            source="测试 RSS",
            category="stock",
        )
        collector = Mock()
        collector.collect_once.return_value = [article]
        adapter = RssNewsSearchAdapter(collector=collector, store=Mock())

        result = adapter.search_comprehensive_intel(
            stock_code="000001",
            stock_name="平安",
        )

        self.assertEqual(result["latest_news"].results, [])

    def test_stock_code_match_requires_numeric_token_boundary(self):
        article = NewsArticle(
            title="其他代码16005190相关市场消息",
            summary="不包含完整的600519代码",
            url="https://example.com/wrong-code",
            source="测试 RSS",
            category="stock",
        )
        collector = Mock()
        collector.collect_once.return_value = [article]
        adapter = RssNewsSearchAdapter(collector=collector, store=Mock())

        result = adapter.search_comprehensive_intel(
            stock_code="600519",
            stock_name="贵州茅台",
        )

        self.assertEqual(result["latest_news"].results, [])

    def test_rss_works_without_paid_or_keyed_search_provider(self):
        article = NewsArticle(
            title="财经快讯",
            summary="快讯摘要",
            url="https://example.com/news/2",
            source="测试 RSS",
            category="market",
        )
        collector = Mock()
        collector.collect_once.return_value = [article]
        adapter = RssNewsSearchAdapter(
            collector=collector,
            store=Mock(),
        )

        response = adapter.search_stock_news("market", "大盘", max_results=3)

        self.assertTrue(response.success)
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].title, "财经快讯")


if __name__ == "__main__":
    unittest.main()
