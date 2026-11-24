# -*- coding: utf-8 -*-
"""Unit tests for rule-based intraday news alerting."""

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from src.news_feed import NewsArticle, NewsMonitor, classify_news_importance


class NewsMonitorTests(unittest.TestCase):
    def test_classify_critical_and_regular_news(self):
        critical = NewsArticle(
            title="上市公司收到立案调查通知",
            url="https://example.com/p0",
            source="媒体",
            category="stock",
        )
        regular = NewsArticle(
            title="市场午间行情观察",
            url="https://example.com/p2",
            source="媒体",
            category="market",
        )

        self.assertEqual(classify_news_importance(critical), "P0")
        self.assertEqual(classify_news_importance(regular), "P2")

    def test_only_recent_p0_news_is_notified_once(self):
        now = datetime.now(timezone.utc)
        article = NewsArticle(
            title="上市公司披露重大合同",
            summary="合同摘要",
            url="https://example.com/contract",
            source="测试源",
            category="stock",
            published_at=now.isoformat(timespec="seconds"),
        )
        store = Mock()
        store.claim_alert.return_value = True
        notifier = Mock()
        notifier.is_available.return_value = True
        notifier.send.return_value = True
        monitor = NewsMonitor(
            collector=Mock(),
            store=store,
            notifier=notifier,
        )

        sent = monitor.notify_critical([article], lookback_seconds=600)

        self.assertEqual(sent, 1)
        store.claim_alert.assert_called_once_with(article.url, "P0")
        notifier.send.assert_called_once()
        self.assertIn(article.url, notifier.send.call_args.args[0])

    def test_failed_notification_releases_deduplication_claim(self):
        article = NewsArticle(
            title="公司业绩预告",
            url="https://example.com/earnings",
            source="测试源",
            category="stock",
            published_at=datetime.now(timezone.utc).isoformat(),
        )
        store = Mock()
        store.claim_alert.return_value = True
        notifier = Mock()
        notifier.is_available.return_value = True
        notifier.send.return_value = False
        monitor = NewsMonitor(collector=Mock(), store=store, notifier=notifier)

        self.assertEqual(monitor.notify_critical([article]), 0)
        store.release_alert.assert_called_once_with(article.url)

    def test_old_critical_news_is_not_pushed(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        article = NewsArticle(
            title="公司重大合同公告",
            url="https://example.com/old",
            source="测试源",
            category="stock",
            published_at=old.isoformat(),
        )
        store = Mock()
        notifier = Mock()
        notifier.is_available.return_value = True
        monitor = NewsMonitor(collector=Mock(), store=store, notifier=notifier)

        self.assertEqual(monitor.notify_critical([article]), 0)
        store.claim_alert.assert_not_called()
        notifier.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
