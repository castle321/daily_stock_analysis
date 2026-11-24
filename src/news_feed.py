# -*- coding: utf-8 -*-
"""Free RSS news collection and persistence for the DSA news subsystem."""

import hashlib
import html
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from src.search_service import SearchResponse, SearchResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsFeedSource:
    source_id: str
    name: str
    url: str
    category: str


@dataclass
class NewsArticle:
    title: str
    url: str
    source: str
    category: str
    summary: str = ""
    published_at: Optional[str] = None
    guid: str = ""


DEFAULT_NEWS_FEEDS = (
    NewsFeedSource(
        "sina_finance",
        "新浪财经-财经要闻",
        "http://rss.sina.com.cn/roll/finance/hot_roll.xml",
        "market",
    ),
    NewsFeedSource(
        "sina_stock",
        "新浪财经-股票要闻",
        "http://rss.sina.com.cn/roll/stock/hot_roll.xml",
        "stock",
    ),
    NewsFeedSource(
        "sina_jsy",
        "新浪财经-股市及时雨",
        "http://rss.sina.com.cn/finance/jsy.xml",
        "market",
    ),
    NewsFeedSource(
        "chinanews_finance",
        "中国新闻网-财经",
        "https://www.chinanews.com.cn/rss/finance.xml",
        "macro",
    ),
)


class _HTMLTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data and data.strip():
            self.parts.append(data.strip())


def _plain_text(value: str, limit: int = 2000) -> str:
    parser = _HTMLTextParser()
    try:
        parser.feed(value or "")
        text = " ".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:limit]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def _first_text(element, names: Iterable[str]) -> str:
    wanted = {name.lower() for name in names}
    for child in element.iter():
        if child is element:
            continue
        if _local_name(child.tag) in wanted and child.text:
            return child.text.strip()
    return ""


def _entry_link(element) -> str:
    for child in element:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text:
            return child.text.strip()
    return ""


def _parse_published_at(value: str) -> Optional[str]:
    if not value:
        return None
    parsed = None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_rss(xml_content: bytes, source: NewsFeedSource) -> List[NewsArticle]:
    """Parse RSS 2.0, RSS 1.0 and Atom feeds without an extra dependency."""
    root = ElementTree.fromstring(xml_content)
    entries = [
        node for node in root.iter()
        if _local_name(node.tag) in ("item", "entry")
    ]
    articles = []
    for entry in entries:
        title = _plain_text(_first_text(entry, ("title",)), 300)
        link = _entry_link(entry)
        if not link:
            link = _first_text(entry, ("guid", "id"))
        if not title or not link:
            continue
        summary = _first_text(
            entry,
            ("description", "summary", "encoded", "content"),
        )
        published = _first_text(
            entry,
            ("pubdate", "published", "updated", "date"),
        )
        guid = _first_text(entry, ("guid", "id")) or link
        articles.append(
            NewsArticle(
                title=title,
                url=link.strip(),
                source=source.name,
                category=source.category,
                summary=_plain_text(summary),
                published_at=_parse_published_at(published),
                guid=guid.strip(),
            )
        )
    return articles


class NewsStore:
    """Small durable SQLite store for source articles; raw facts stay immutable."""

    def __init__(self, db_path: Optional[str] = None):
        configured = db_path or os.getenv(
            "NEWS_DATABASE_PATH", "./data/news_monitor.db"
        )
        self.db_path = str(Path(configured).expanduser())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL UNIQUE,
                    guid TEXT NOT NULL DEFAULT '',
                    published_at TEXT,
                    collected_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_news_articles_published "
                "ON news_articles(published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_news_articles_category "
                "ON news_articles(category, published_at)"
            )

    def save_many(self, articles: Iterable[NewsArticle]) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        inserted = 0
        with self._connect() as conn:
            for article in articles:
                normalized_url = article.url.strip()
                if not normalized_url:
                    continue
                digest = hashlib.sha256(
                    (article.title + "\n" + article.summary).encode("utf-8")
                ).hexdigest()
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO news_articles
                    (source, category, title, summary, url, guid,
                     published_at, collected_at, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article.source,
                        article.category,
                        article.title[:300],
                        article.summary,
                        normalized_url,
                        article.guid,
                        article.published_at,
                        now,
                        digest,
                    ),
                )
                inserted += max(cursor.rowcount, 0)
        return inserted

    def recent(self, limit: int = 100) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source, category, title, summary, url, guid,
                       published_at, collected_at
                FROM news_articles
                ORDER BY COALESCE(published_at, collected_at) DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]


class NewsFeedCollector:
    def __init__(
        self,
        sources: Iterable[NewsFeedSource] = DEFAULT_NEWS_FEEDS,
        session=None,
        timeout: int = 12,
    ):
        self.sources = tuple(sources)
        self.session = session or requests.Session()
        self.timeout = timeout

    def collect_feed(self, source: NewsFeedSource) -> List[NewsArticle]:
        try:
            response = self.session.get(
                source.url,
                timeout=self.timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; DSA-NewsMonitor/1.0; "
                        "+https://github.com/castle321/daily_stock_analysis)"
                    ),
                    "Accept": "application/rss+xml, application/atom+xml, "
                              "application/xml, text/xml, */*",
                },
            )
            response.raise_for_status()
            articles = parse_rss(response.content, source)
            logger.info(
                "[新闻采集] %s 获取 %d 条", source.name, len(articles)
            )
            return articles
        except Exception as exc:
            logger.warning("[新闻采集] %s 失败: %s", source.name, exc)
            return []

    def collect_once(self) -> List[NewsArticle]:
        collected = []
        seen_urls = set()
        for source in self.sources:
            for article in self.collect_feed(source):
                if article.url in seen_urls:
                    continue
                seen_urls.add(article.url)
                collected.append(article)
        return collected


class RssNewsSearchAdapter:
    """Expose free RSS articles through the existing MarketAnalyzer search contract."""

    def __init__(
        self,
        fallback=None,
        collector: Optional[NewsFeedCollector] = None,
        store: Optional[NewsStore] = None,
    ):
        self.fallback = fallback
        self.collector = collector or NewsFeedCollector()
        self.store = store or NewsStore()
        self._rss_results = None

    @property
    def is_available(self):
        return True

    def _load_rss_results(self):
        if self._rss_results is not None:
            return self._rss_results
        articles = self.collector.collect_once()
        self.store.save_many(articles)
        self._rss_results = [
            SearchResult(
                title=item.title,
                snippet=item.summary,
                url=item.url,
                source=item.source,
                published_date=item.published_at,
            )
            for item in articles
        ]
        return self._rss_results

    def search_stock_news(
        self,
        stock_code: str,
        stock_name: str,
        max_results: int = 5,
        focus_keywords=None,
    ):
        rss_results = self._load_rss_results() if stock_code == "market" else []
        fallback_results = []
        fallback_provider = ""
        if self.fallback and self.fallback.is_available:
            response = self.fallback.search_stock_news(
                stock_code=stock_code,
                stock_name=stock_name,
                max_results=max_results,
                focus_keywords=focus_keywords,
            )
            if response and response.results:
                fallback_results = response.results
                fallback_provider = response.provider

        combined = []
        seen = set()
        for item in list(rss_results) + list(fallback_results):
            key = (item.url or "").strip() or item.title.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            combined.append(item)
            if len(combined) >= max_results:
                break

        providers = ["RSS"] if rss_results else []
        if fallback_provider:
            providers.append(fallback_provider)
        return SearchResponse(
            query=" ".join(focus_keywords or [stock_name]),
            results=combined,
            provider="+".join(providers) or "RSS",
            success=True,
        )
