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
from datetime import datetime, timedelta, timezone
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


def _format_china_published_at(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        from zoneinfo import ZoneInfo
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S 北京时间"
        )
    except (TypeError, ValueError):
        return value


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_alerts (
                    url TEXT PRIMARY KEY,
                    level TEXT NOT NULL,
                    notified_at TEXT NOT NULL
                )
                """
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

    def claim_alert(self, url: str, level: str = "P0") -> bool:
        """Atomically reserve a one-time alert for a news URL."""
        if not url:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO news_alerts (url, level, notified_at) "
                "VALUES (?, ?, ?)",
                (url, level, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            return cursor.rowcount > 0

    def release_alert(self, url: str) -> None:
        """Allow retry when notification delivery fails."""
        with self._connect() as conn:
            conn.execute("DELETE FROM news_alerts WHERE url = ?", (url,))

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
        self._rss_returned = False

    @property
    def is_available(self):
        return True

    def _load_rss_results(self):
        """Load fresh RSS items, falling back to recently persisted articles.

        The default freshness window is 72 hours and can be overridden with
        NEWS_MAX_AGE_HOURS. Items without a publication timestamp use their
        collection timestamp when read back from SQLite.
        """
        if self._rss_results is not None:
            return self._rss_results

        try:
            articles = self.collector.collect_once() or []
        except Exception:
            logger.exception("[RSS] 采集异常，尝试使用本地新闻缓存")
            articles = []

        try:
            if articles:
                self.store.save_many(articles)
        except Exception:
            logger.exception("[RSS] 新闻写入数据库失败，不影响本轮分析")

        candidates = []
        now = datetime.now(timezone.utc)
        try:
            max_age_hours = max(
                1, min(int(os.getenv("NEWS_MAX_AGE_HOURS", "72")), 24 * 30)
            )
        except (TypeError, ValueError):
            max_age_hours = 72
        cutoff = now - timedelta(hours=max_age_hours)

        def add_candidate(title, summary, url, source, published_at, collected_at=None):
            timestamp_value = published_at or collected_at
            timestamp = None
            if timestamp_value:
                try:
                    timestamp = datetime.fromisoformat(
                        str(timestamp_value).replace("Z", "+00:00")
                    )
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    timestamp = timestamp.astimezone(timezone.utc)
                except (TypeError, ValueError):
                    return
            elif collected_at is None:
                # A just-fetched feed item without a publication date is
                # treated as newly observed, but persisted unknown-date rows
                # are judged by collected_at on subsequent runs.
                timestamp = now
            if timestamp and (timestamp < cutoff or timestamp > now + timedelta(minutes=10)):
                return
            candidates.append((
                timestamp or datetime.min.replace(tzinfo=timezone.utc),
                SearchResult(
                    title=title or "",
                    snippet=summary or "",
                    url=url or "",
                    source=source or "未知来源",
                    published_date=_format_china_published_at(published_at),
                ),
            ))

        for item in articles:
            add_candidate(
                item.title, item.summary, item.url, item.source,
                item.published_at,
            )

        # Merge recent persisted items as a resilience path. URL deduplication
        # keeps the live copy when the same article exists in both sources.
        try:
            stored = self.store.recent(limit=300) or []
            if not isinstance(stored, (list, tuple)):
                stored = []
        except Exception:
            logger.exception("[RSS] 读取本地新闻缓存失败")
            stored = []
        for item in stored:
            add_candidate(
                item.get("title"), item.get("summary"), item.get("url"),
                item.get("source"), item.get("published_at"),
                item.get("collected_at"),
            )

        candidates.sort(key=lambda pair: pair[0], reverse=True)
        results = []
        seen = set()
        for _, result in candidates:
            key = (result.url or "").strip() or result.title.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            results.append(result)
        self._rss_results = results
        logger.info(
            "[RSS] 可用于分析的新闻 %d 条（时效窗口 %d 小时）",
            len(results), max_age_hours,
        )
        return self._rss_results

    def search_comprehensive_intel(
        self,
        stock_code: str,
        stock_name: str,
        max_searches: int = 3,
    ):
        """Merge exact-name/code RSS matches into the existing stock-intel result."""
        results = {}
        if self.fallback and self.fallback.is_available:
            results = self.fallback.search_comprehensive_intel(
                stock_code=stock_code,
                stock_name=stock_name,
                max_searches=max_searches,
            ) or {}

        matched = []
        name_key = (stock_name or "").strip().lower()
        code_key = (stock_code or "").strip().lower()
        for item in self._load_rss_results():
            title = (item.title or "").lower()
            snippet = (item.snippet or "").lower()
            # Stock codes must be a complete numeric token. Short Chinese
            # names (one or two characters) are accepted in titles only to
            # reduce accidental matches in generic article summaries.
            code_match = bool(
                code_key
                and re.search(rf"(?<!\\d){re.escape(code_key)}(?!\\d)", f"{title} {snippet}")
            )
            name_match = bool(
                name_key
                and (
                    name_key in title
                    or (len(name_key) >= 3 and name_key in snippet)
                )
            )
            if name_match or code_match:
                matched.append(item)

        if matched:
            existing = results.get("latest_news")
            combined = list(matched)
            if existing and existing.results:
                combined.extend(existing.results)
            deduplicated = []
            seen = set()
            for item in combined:
                key = (item.url or "").strip() or item.title.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    deduplicated.append(item)
            results["latest_news"] = SearchResponse(
                query=f"{stock_name} {stock_code} RSS 新闻",
                results=deduplicated[:6],
                provider=(
                    f"RSS+{existing.provider}"
                    if existing and existing.provider else "RSS"
                ),
                success=True,
            )
        elif "latest_news" not in results:
            results["latest_news"] = SearchResponse(
                query=f"{stock_name} {stock_code} RSS 新闻",
                results=[],
                provider="RSS",
                success=True,
            )
        return results

    def format_intel_report(self, intel_results, stock_name: str) -> str:
        if self.fallback and hasattr(self.fallback, "format_intel_report"):
            return self.fallback.format_intel_report(intel_results, stock_name)
        lines = [f"【{stock_name} 情报搜索结果】"]
        for dimension, response in intel_results.items():
            lines.append(f"\n{dimension}（来源：{response.provider}）")
            for index, item in enumerate(response.results[:6], 1):
                lines.append(
                    f"{index}. [{item.source}] {item.title} "
                    f"[{item.published_date or '发布时间未提供'}]"
                )
                if item.snippet:
                    lines.append(f"   {item.snippet[:240]}")
                if item.url:
                    lines.append(f"   原文：{item.url}")
            if not response.results:
                lines.append("未找到相关信息")
        return "\n".join(lines)

    def search_stock_news(
        self,
        stock_code: str,
        stock_name: str,
        max_results: int = 5,
        focus_keywords=None,
    ):
        rss_results = []
        if stock_code == "market" and not self._rss_returned:
            rss_results = self._load_rss_results()
            self._rss_returned = True
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



P0_KEYWORDS = (
    "重大合同", "重大资产重组", "立案调查", "行政处罚",
    "业绩预告", "业绩快报", "停牌", "退市风险", "重大诉讼",
)


def classify_news_importance(article: NewsArticle) -> str:
    """Conservative title-only rule; AI review is deliberately not run in the poll loop."""
    title = article.title or ""
    if any(keyword in title for keyword in P0_KEYWORDS):
        return "P0"
    if article.category in ("stock", "macro") or any(
        keyword in title for keyword in ("政策", "中标", "订单", "涨价", "降价")
    ):
        return "P1"
    return "P2"


class NewsMonitor:
    def __init__(
        self,
        collector: Optional[NewsFeedCollector] = None,
        store: Optional[NewsStore] = None,
        notifier=None,
    ):
        self.collector = collector or NewsFeedCollector()
        self.store = store or NewsStore()
        self.notifier = notifier

    def collect_once(self) -> List[NewsArticle]:
        articles = self.collector.collect_once()
        inserted = self.store.save_many(articles)
        logger.info(
            "[新闻监控] 本轮采集 %d 条，新增入库 %d 条",
            len(articles),
            inserted,
        )
        return articles

    def notify_critical(self, articles: Iterable[NewsArticle], lookback_seconds: int = 600) -> int:
        if not self.notifier or not self.notifier.is_available():
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - max(60, lookback_seconds)
        sent = 0
        for article in articles:
            if classify_news_importance(article) != "P0" or not article.published_at:
                continue
            try:
                published = datetime.fromisoformat(
                    article.published_at.replace("Z", "+00:00")
                )
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published.timestamp() < cutoff:
                    continue
            except (TypeError, ValueError):
                continue
            if not self.store.claim_alert(article.url, "P0"):
                continue
            message = (
                "【DSA 盘中重要新闻提醒】\n"
                f"等级：P0\n来源：{article.source}\n"
                f"发布时间：{article.published_at}\n"
                f"标题：{article.title}\n"
                f"摘要：{article.summary or '暂无摘要'}\n"
                f"原文：{article.url}\n"
                "说明：规则筛选结果，仍需核验原文及行情反应。"
            )
            try:
                delivered = self.notifier.send(message)
            except Exception:
                logger.exception("[新闻监控] P0 告警推送异常：%s", article.url)
                delivered = False
            if delivered:
                sent += 1
            else:
                self.store.release_alert(article.url)
        return sent


def _is_a_share_monitor_window(now: datetime) -> bool:
    """Weekday/time gate only; exchange holiday calendar will be added separately."""
    local_time = now.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
    if local_time.weekday() >= 5:
        return False
    current = local_time.time()
    from datetime import time as time_type
    return (
        time_type(8, 45) <= current <= time_type(11, 35)
        or time_type(12, 55) <= current <= time_type(15, 10)
    )


def run_watch(interval_seconds: int = 180):
    """Poll RSS during A-share sessions; P0 alerts are deduplicated by article URL."""
    from src.notification import NotificationService

    monitor = NewsMonitor(notifier=NotificationService())
    interval = max(60, int(interval_seconds))
    logger.info("[新闻监控] 启动，轮询间隔 %d 秒", interval)
    while True:
        now = datetime.now(timezone.utc)
        if _is_a_share_monitor_window(now):
            articles = monitor.collect_once()
            alerted = monitor.notify_critical(
                articles,
                lookback_seconds=max(interval * 2, 600),
            )
            logger.info("[新闻监控] 本轮 P0 告警 %d 条", alerted)
            time.sleep(interval)
        else:
            time.sleep(min(interval, 300))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="DSA 免费 RSS 新闻采集与盘中监控")
    parser.add_argument("--once", action="store_true", help="采集一次并写入 SQLite，不发送告警")
    parser.add_argument("--watch", action="store_true", help="在 A 股工作日交易时段持续采集并推送 P0")
    parser.add_argument("--interval", type=int, default=180, help="盘中轮询间隔秒数，默认 180")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if args.watch:
        run_watch(args.interval)
        return
    monitor = NewsMonitor()
    articles = monitor.collect_once()
    print(f"采集完成：{len(articles)} 条；数据库：{monitor.store.db_path}")


if __name__ == "__main__":
    main()
