# -*- coding: utf-8 -*-
"""Tests for the RSS adapter used by market review."""

import unittest
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
            [item.title for item in second.results],
            ["搜索补充新闻"],
        )
        collector.collect_once.assert_called_once()
        store.save_many.assert_called_once_with([article])
        self.assertEqual(fallback.search_stock_news.call_count, 2)

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
