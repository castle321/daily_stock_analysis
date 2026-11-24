# -*- coding: utf-8 -*-
"""Unit tests for the RSS news collector and SQLite store."""

import os
import tempfile
import unittest
from unittest.mock import Mock

from src.news_feed import (
    NewsArticle,
    NewsFeedCollector,
    NewsFeedSource,
    NewsStore,
    parse_rss,
)


class NewsFeedTests(unittest.TestCase):
    def setUp(self):
        self.source = NewsFeedSource(
            "test", "测试新闻源", "https://example.com/rss.xml", "market"
        )

    def test_parse_rss_items_and_normalize_dates(self):
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item>
            <title><![CDATA[液冷行业出现新进展]]></title>
            <link>https://example.com/article/1</link>
            <description><![CDATA[<p>行业新闻摘要</p>]]></description>
            <pubDate>Tue, 24 Nov 2026 09:30:00 +0800</pubDate>
            <guid>article-1</guid>
          </item>
        </channel></rss>"""

        articles = parse_rss(payload, self.source)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "液冷行业出现新进展")
        self.assertEqual(articles[0].summary, "行业新闻摘要")
        self.assertEqual(articles[0].url, "https://example.com/article/1")
        self.assertEqual(articles[0].published_at, "2026-11-24T01:30:00+00:00")

    def test_parse_atom_feed(self):
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>公司公告</title>
            <link href="https://example.com/atom/1" rel="alternate"/>
            <summary>公告摘要</summary>
            <updated>2026-11-24T10:00:00+08:00</updated>
            <id>tag:example.com,2026:1</id>
          </entry>
        </feed>"""

        articles = parse_rss(payload, self.source)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].url, "https://example.com/atom/1")
        self.assertEqual(articles[0].summary, "公告摘要")
        self.assertEqual(articles[0].guid, "tag:example.com,2026:1")

    def test_store_deduplicates_by_url(self):
        with tempfile.TemporaryDirectory() as directory:
            store = NewsStore(os.path.join(directory, "news.db"))
            article = NewsArticle(
                title="标题",
                summary="摘要",
                url="https://example.com/1",
                source="测试源",
                category="market",
            )

            self.assertEqual(store.save_many([article, article]), 1)
            self.assertEqual(store.save_many([article]), 0)
            recent = store.recent()

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["title"], "标题")

    def test_collector_continues_when_one_feed_fails(self):
        session = Mock()
        good_response = Mock()
        good_response.content = b"""<rss><channel><item>
          <title>可用新闻</title><link>https://example.com/good</link>
        </item></channel></rss>"""
        good_response.raise_for_status.return_value = None
        session.get.side_effect = [RuntimeError("temporary failure"), good_response]

        second = NewsFeedSource(
            "test2", "备用源", "https://example.com/second.xml", "market"
        )
        collector = NewsFeedCollector(
            sources=(self.source, second), session=session
        )

        articles = collector.collect_once()

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "可用新闻")
        self.assertEqual(session.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
