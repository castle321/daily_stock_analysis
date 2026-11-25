# -*- coding: utf-8 -*-
"""Integration tests for RSS news flowing into market and stock analysis."""

import unittest
from unittest.mock import Mock

from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType
from src.market_analyzer import MarketAnalyzer, MarketOverview
from src.news_feed import NewsArticle, RssNewsSearchAdapter
from src.search_service import SearchResponse, SearchResult


class RssMarketReviewIntegrationTests(unittest.TestCase):
    def test_rss_and_fallback_news_reach_market_review_prompt_once(self):
        rss_articles = [
            NewsArticle(
                title="A股宏观政策与市场观察",
                summary="来自 RSS 的宏观新闻摘要",
                url="https://example.com/rss/market-1",
                source="测试 RSS",
                category="macro",
                published_at="2026-11-24T01:30:00+00:00",
            )
        ]
        collector = Mock()
        collector.collect_once.return_value = rss_articles

        fallback = Mock()
        fallback.is_available = True
        fallback.search_stock_news.side_effect = [
            SearchResponse(
                query="A股 大盘 复盘",
                results=[
                    SearchResult(
                        title="搜索服务补充新闻一",
                        snippet="补充摘要一",
                        url="https://example.com/search/1",
                        source="测试搜索",
                    )
                ],
                provider="MockSearch",
            ),
            SearchResponse(
                query="股市 行情 分析",
                results=[
                    SearchResult(
                        title="搜索服务补充新闻一（重复）",
                        snippet="重复摘要",
                        url="https://example.com/search/1",
                        source="测试搜索",
                    ),
                    SearchResult(
                        title="搜索服务补充新闻二",
                        snippet="补充摘要二",
                        url="https://example.com/search/2",
                        source="测试搜索",
                    ),
                ],
                provider="MockSearch",
            ),
            SearchResponse(
                query="A股 市场 热点 板块",
                results=[
                    SearchResult(
                        title="搜索服务补充新闻二（重复）",
                        snippet="重复摘要",
                        url="https://example.com/search/2",
                        source="测试搜索",
                    ),
                    SearchResult(
                        title="搜索服务补充新闻三",
                        snippet="补充摘要三",
                        url="https://example.com/search/3",
                        source="测试搜索",
                    ),
                ],
                provider="MockSearch",
            ),
        ]
        adapter = RssNewsSearchAdapter(
            fallback=fallback,
            collector=collector,
            store=Mock(),
        )

        market_analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        market_analyzer.search_service = adapter
        overview = MarketOverview(
            date="2026-11-24",
            capture_time="2026-11-24 15:05:00",
        )

        news = market_analyzer.search_market_news()
        prompt = market_analyzer._build_review_prompt(overview, news)

        titles = [item.title for item in news]
        self.assertEqual(
            titles,
            [
                "A股宏观政策与市场观察",
                "搜索服务补充新闻一",
                "搜索服务补充新闻二",
                "搜索服务补充新闻三",
            ],
        )
        self.assertIn("来自 RSS 的宏观新闻摘要", prompt)
        self.assertIn("测试 RSS", prompt)
        self.assertIn("https://example.com/rss/market-1", prompt)
        self.assertIn("搜索服务补充新闻三", prompt)
        self.assertEqual(len(titles), len(set(titles)))
        collector.collect_once.assert_called_once()
        self.assertEqual(fallback.search_stock_news.call_count, 3)


class RssStockAnalysisIntegrationTests(unittest.TestCase):
    def test_rss_stock_news_is_passed_to_analyzer_and_saved(self):
        article = NewsArticle(
            title="贵州茅台发布经营业务进展",
            summary="贵州茅台相关经营信息摘要",
            url="https://example.com/stock/600519",
            source="测试 RSS",
            category="stock",
            published_at="2026-11-24T01:30:00+00:00",
        )
        collector = Mock()
        collector.collect_once.return_value = [article]
        rss_adapter = RssNewsSearchAdapter(
            collector=collector,
            store=Mock(),
        )

        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.fetcher_manager = Mock()
        pipeline.fetcher_manager.get_realtime_quote.return_value = None
        pipeline.fetcher_manager.get_chip_distribution.return_value = None
        pipeline.db = Mock()
        pipeline.db.get_analysis_context.return_value = None
        pipeline.search_service = rss_adapter
        pipeline.analyzer = Mock()
        analysis_result = object()
        pipeline.analyzer.analyze.return_value = analysis_result
        pipeline._enhance_context = Mock(return_value={"stock_name": "贵州茅台"})
        pipeline.query_id = None
        pipeline.query_source = "system"
        pipeline.source_message = None
        pipeline.save_context_snapshot = True

        result = pipeline.analyze_stock("600519", ReportType.SIMPLE)

        self.assertIs(result, analysis_result)
        analyzer_kwargs = pipeline.analyzer.analyze.call_args.kwargs
        news_context = analyzer_kwargs["news_context"]
        self.assertIn("贵州茅台发布经营业务进展", news_context)
        self.assertIn("测试 RSS", news_context)
        self.assertIn("https://example.com/stock/600519", news_context)
        pipeline.db.save_news_intel.assert_called_once()
        pipeline.db.save_analysis_history.assert_called_once()
        self.assertEqual(
            pipeline.db.save_analysis_history.call_args.kwargs["news_content"],
            news_context,
        )
        collector.collect_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
