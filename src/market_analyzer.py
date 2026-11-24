# -*- coding: utf-8 -*-
"""
===================================
大盘复盘分析模块
===================================

职责：
1. 获取大盘指数数据（上证、深证、创业板）
2. 搜索市场新闻形成复盘情报
3. 使用大模型生成每日大盘复盘报告
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

import pandas as pd

from src.config import get_config
from src.search_service import SearchService
from data_provider.base import DataFetcherManager
from src.time_utils import now_china

logger = logging.getLogger(__name__)
SMALL_SECTOR_CONSTITUENT_THRESHOLD = 3

@dataclass
class MarketIndex:
    """大盘指数数据"""
    code: str                    # 指数代码
    name: str                    # 指数名称
    current: float = 0.0         # 当前点位
    change: float = 0.0          # 涨跌点数
    change_pct: float = 0.0      # 涨跌幅(%)
    open: float = 0.0            # 开盘点位
    high: float = 0.0            # 最高点位
    low: float = 0.0             # 最低点位
    prev_close: float = 0.0      # 昨收点位
    volume: float = 0.0          # 成交量（手）
    amount: float = 0.0          # 成交额（元）
    amplitude: float = 0.0       # 振幅(%)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'current': self.current,
            'change': self.change,
            'change_pct': self.change_pct,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'volume': self.volume,
            'amount': self.amount,
            'amplitude': self.amplitude,
        }


@dataclass
class MarketOverview:
    """市场概览数据"""
    date: str                           # 日期
    capture_time: str = ""
    indices: List[MarketIndex] = field(default_factory=list)  # 主要指数
    up_count: Optional[int] = None      # 上涨家数；None 表示数据缺失
    down_count: Optional[int] = None    # 下跌家数；None 表示数据缺失
    flat_count: Optional[int] = None    # 平盘家数；None 表示数据缺失
    limit_up_count: Optional[int] = None    # 涨停家数；None 表示数据缺失
    limit_down_count: Optional[int] = None  # 跌停家数；None 表示数据缺失
    total_amount: Optional[float] = None    # 两市成交额（亿元）；None 表示数据缺失
    # north_flow: float = 0.0           # 北向资金净流入（亿元）- 已废弃，接口不可用
    
    # 板块涨幅榜
    top_sectors: List[Dict] = field(default_factory=list)     # 涨幅前5板块
    bottom_sectors: List[Dict] = field(default_factory=list)  # 跌幅前5板块
    sector_source: Optional[str] = None  # 板块排行实际数据来源


class MarketAnalyzer:
    """
    大盘复盘分析器
    
    功能：
    1. 获取大盘指数实时行情
    2. 获取市场涨跌统计
    3. 获取板块涨跌榜
    4. 搜索市场新闻
    5. 生成大盘复盘报告
    """
    
    def __init__(self, search_service: Optional[SearchService] = None, analyzer=None):
        """
        初始化大盘分析器

        Args:
            search_service: 搜索服务实例
            analyzer: AI分析器实例（用于调用LLM）
        """
        self.config = get_config()
        self.search_service = search_service
        self.analyzer = analyzer
        self.data_manager = DataFetcherManager()

    def get_market_overview(self) -> MarketOverview:
        """
        获取市场概览数据
        
        Returns:
            MarketOverview: 市场概览数据对象
        """
        now = now_china()
        today = now.strftime('%Y-%m-%d')

        overview = MarketOverview(
            date=today,
            capture_time=now.strftime('%Y-%m-%d %H:%M:%S')
        )
        
        # 1. 获取主要指数行情
        overview.indices = self._get_main_indices()
        
        # 2. 获取涨跌统计
        self._get_market_statistics(overview)
        
        # 3. 获取板块涨跌榜
        self._get_sector_rankings(overview)
        
        # 4. 获取北向资金（可选）
        # self._get_north_flow(overview)
        
        return overview

    
    def _get_main_indices(self) -> List[MarketIndex]:
        """获取主要指数实时行情"""
        indices = []

        try:
            logger.info("[大盘] 获取主要指数实时行情...")

            # 使用 DataFetcherManager 获取指数行情
            # Manager 会自动尝试：Akshare -> Tushare -> Yfinance
            data_list = self.data_manager.get_main_indices()

            if data_list:
                for item in data_list:
                    index = MarketIndex(
                        code=item['code'],
                        name=item['name'],
                        current=item['current'],
                        change=item['change'],
                        change_pct=item['change_pct'],
                        open=item.get('open', 0.0) or 0.0,
                        high=item.get('high', 0.0) or 0.0,
                        low=item.get('low', 0.0) or 0.0,
                        prev_close=item.get('prev_close', 0.0) or 0.0,
                        volume=item.get('volume', 0.0) or 0.0,
                        amount=item.get('amount', 0.0) or 0.0,
                        amplitude=item.get('amplitude', 0.0) or 0.0
                    )
                    indices.append(index)

            if not indices:
                logger.warning("[大盘] 所有行情数据源失败，将依赖新闻搜索进行分析")
            else:
                logger.info(f"[大盘] 获取到 {len(indices)} 个指数行情")

        except Exception as e:
            logger.error(f"[大盘] 获取指数行情失败: {e}")

        return indices

    def _get_market_statistics(self, overview: MarketOverview):
        """获取市场涨跌统计"""
        try:
            logger.info("[大盘] 获取市场涨跌统计...")

            stats = self.data_manager.get_market_stats()

            if stats:
                overview.up_count = stats.get('up_count')
                overview.down_count = stats.get('down_count')
                overview.flat_count = stats.get('flat_count')
                overview.limit_up_count = stats.get('limit_up_count')
                overview.limit_down_count = stats.get('limit_down_count')
                overview.total_amount = stats.get('total_amount')

                amount_text = f"{overview.total_amount:.0f}亿" if overview.total_amount is not None else "暂无数据"
                logger.info(
                    f"[大盘] 涨:{overview.up_count} 跌:{overview.down_count} 平:{overview.flat_count} "
                    f"涨停:{overview.limit_up_count} 跌停:{overview.limit_down_count} 成交额:{amount_text}"
                )

        except Exception as e:
            logger.error(f"[大盘] 获取涨跌统计失败: {e}")

    def _get_sector_rankings(self, overview: MarketOverview):
        """获取板块涨跌榜"""
        overview.top_sectors = []
        overview.bottom_sectors = []
        overview.sector_source = None

        try:
            logger.info("[大盘] 获取板块涨跌榜...")

            top_sectors, bottom_sectors = self.data_manager.get_sector_rankings(5)

            if top_sectors or bottom_sectors:
                overview.top_sectors = top_sectors or []
                overview.bottom_sectors = bottom_sectors or []
                overview.sector_source = getattr(
                    self.data_manager, "last_sector_source", None
                )

                logger.info(
                    f"[大盘] 板块排行数据来源: "
                    f"{overview.sector_source or '未知'}"
                )
                logger.info(
                    f"[大盘] 领涨板块: "
                    f"{[s['name'] for s in overview.top_sectors]}"
                )
                logger.info(
                    f"[大盘] 领跌板块: "
                    f"{[s['name'] for s in overview.bottom_sectors]}"
                )

                if overview.sector_source in (
                    "AkShare-东方财富",
                    "东方财富-Push2",
                ):
                    self._enrich_sector_constituent_counts(overview)

            else:
                logger.warning("[大盘] 板块排行无有效数据")

        except Exception as e:
            logger.error(f"[大盘] 获取板块涨跌榜失败: {e}")
    
    # def _get_north_flow(self, overview: MarketOverview):
    #     """获取北向资金流入"""
    #     try:
    #         logger.info("[大盘] 获取北向资金...")
            
    #         # 获取北向资金数据
    #         df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            
    #         if df is not None and not df.empty:
    #             # 取最新一条数据
    #             latest = df.iloc[-1]
    #             if '当日净流入' in df.columns:
    #                 overview.north_flow = float(latest['当日净流入']) / 1e8  # 转为亿元
    #             elif '净流入' in df.columns:
    #                 overview.north_flow = float(latest['净流入']) / 1e8
                    
    #             logger.info(f"[大盘] 北向资金净流入: {overview.north_flow:.2f}亿")
                
    #     except Exception as e:
    #         logger.warning(f"[大盘] 获取北向资金失败: {e}")

    def _enrich_sector_constituent_counts(self, overview: MarketOverview):
        """为报告展示的板块补充成分股数量，不影响原始排行。"""
        get_count = getattr(
            self.data_manager,
            "get_sector_constituent_count",
            None,
        )

        if not callable(get_count):
            return

        # 只查询报告实际展示的前三名，两个榜单合计最多 6 个。
        sectors = (
            overview.top_sectors[:3]
            + overview.bottom_sectors[:3]
        )

        queried = {}

        for sector in sectors:
            name = sector.get("name")
            if not name:
                continue

            if name not in queried:
                try:
                    queried[name] = get_count(name)
                except Exception as e:
                    logger.warning("[大盘] 查询板块 %s 成分股数量失败：%s", name, e)
                    queried[name] = None

            count = queried[name]
            if count is not None and count > 0:
                sector["constituent_count"] = count
                sector["small_sample"] = (
                    count <= SMALL_SECTOR_CONSTITUENT_THRESHOLD
                )
    
    def search_market_news(self) -> List[Dict]:
        """
        搜索市场新闻
        
        Returns:
            新闻列表
        """
        if not self.search_service:
            logger.warning("[大盘] 搜索服务未配置，跳过新闻搜索")
            return []
        
        all_news = []
        today = now_china()
        date_str = today.strftime('%Y年%m月%d日')

        # 多维度搜索
        search_queries = [
            "A股 大盘 复盘",
            "股市 行情 分析",
            "A股 市场 热点 板块",
        ]
        
        try:
            logger.info("[大盘] 开始搜索市场新闻...")
            
            for query in search_queries:
                # 使用 search_stock_news 方法，传入"大盘"作为股票名
                response = self.search_service.search_stock_news(
                    stock_code="market",
                    stock_name="大盘",
                    max_results=3,
                    focus_keywords=query.split()
                )
                if response and response.results:
                    all_news.extend(response.results)
                    logger.info(f"[大盘] 搜索 '{query}' 获取 {len(response.results)} 条结果")
            
            logger.info(f"[大盘] 共获取 {len(all_news)} 条市场新闻")
            
        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")
        
        return all_news
    
    def generate_market_review(self, overview: MarketOverview, news: List) -> str:
        """
        使用大模型生成大盘复盘报告
        
        Args:
            overview: 市场概览数据
            news: 市场新闻列表 (SearchResult 对象列表)
            
        Returns:
            大盘复盘报告文本
        """
        if not self.analyzer or not self.analyzer.is_available():
            logger.warning("[大盘] AI分析器未配置或不可用，使用模板生成报告")
            return self._generate_template_review(overview, news)
        
        # 构建 Prompt
        prompt = self._build_review_prompt(overview, news)
        
        try:
            logger.info("[大盘] 调用大模型生成复盘报告...")
            
            generation_config = {
                'temperature': 0.7,
                'max_output_tokens': 2048,
            }
            
            # 根据 analyzer 使用的 API 类型调用
            if self.analyzer._use_openai:
                # 使用 OpenAI 兼容 API
                review = self.analyzer._call_openai_api(prompt, generation_config)
            else:
                # 使用 Gemini API
                response = self.analyzer._model.generate_content(
                    prompt,
                    generation_config=generation_config,
                )
                review = response.text.strip() if response and response.text else None
            
            if review:
                logger.info(f"[大盘] 复盘报告生成成功，长度: {len(review)} 字符")
                return review
            else:
                logger.warning("[大盘] 大模型返回为空")
                return self._generate_template_review(overview, news)
                
        except Exception as e:
            logger.error(f"[大盘] 大模型生成复盘报告失败: {e}")
            return self._generate_template_review(overview, news)
    
    def _build_review_prompt(self, overview: MarketOverview, news: List) -> str:
        """构建复盘报告 Prompt"""
        # 指数行情信息（简洁格式，不用emoji）
        indices_text = ""
        for idx in overview.indices:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            indices_text += f"- {idx.name}: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%)\n"
        
        # 板块信息
        def format_prompt_sector(sector):
            text = f"{sector['name']}({sector['change_pct']:+.2f}%)"

            count = sector.get("constituent_count")
            if sector.get("small_sample") and count is not None:
                text += f"[仅{count}只成分股，小样本]"

            return text

        top_sectors_text = ", ".join(
            format_prompt_sector(s) for s in overview.top_sectors[:3]
        )

        bottom_sectors_text = ", ".join(
            format_prompt_sector(s) for s in overview.bottom_sectors[:3]
        )
        sector_source_text = overview.sector_source or "未知"

        total_amount_text = (
            f"{overview.total_amount:.0f} 亿元"
            if overview.total_amount is not None else "暂无数据"
        )

        # 新闻信息：保留来源、源站发布时间和原文链接，便于核验。
        news_text = ""
        for i, n in enumerate(news[:9], 1):
            def news_value(key, default=""):
                return getattr(n, key, default) if hasattr(n, key) else n.get(key, default)

            title = str(news_value("title") or "")[:100]
            snippet = str(news_value("snippet") or "")[:240]
            source = str(news_value("source") or "未知来源")[:80]
            published = str(news_value("published_date") or "发布时间未提供")[:40]
            url = str(news_value("url") or "")
            news_text += (
                f"{i}. [{source}] {title}\\n"
                f"   源站发布时间：{published}\\n"
                f"   摘要：{snippet}\\n"
            )
            if url:
                news_text += f"   原文链接：{url}\\n"

        prompt = f"""你是一位专业的A/H/美股市场分析师，请根据以下数据生成一份简洁的大盘复盘报告。

【重要】输出要求：
- 必须输出纯 Markdown 文本格式
- 禁止输出 JSON 格式
- 禁止输出代码块
- emoji 仅在标题处少量使用（每个标题最多1个）
- 严禁把“暂无数据”或缺失字段当成 0；不得编造成交额、涨跌家数、资金流向或板块信息
- 成交额、涨跌统计或板块数据缺失时，明确说明数据未获取，不得据此推断资金流向或市场风格
- 输入没有资金流向数据时，不得描述主力资金、北向资金或行业资金净流入/流出；成交额只能用于描述成交规模，不能代表资金流向
- 如果主要指数行情缺失，不得使用“上涨、下跌、震荡整理、强势”等词判断当日市场方向；必须明确写明指数行情未获取
- 如果指数行情、涨跌统计和成交额均缺失，后市展望只能列出待观察事项，不得给出方向性预测
- 只分析输入中实际存在且有效的数据；新闻只能作为新闻信息，不能替代缺失的行情统计
- 所有时间字段均为北京时间（Asia/Shanghai，UTC+08:00），不得按 UTC 或模型默认时区重新解释
- 报告必须注明行情采集时间，不得将盘中行情描述为收盘行情
- 如果行情采集时间不为空，应原样保留该时间
- 不得仅凭未经确认的时区或时间数字判断盘前、盘中或盘后；交易时段判断必须以明确标注的北京时间为准
- 对标记为小样本的板块，必须明确说明成分股数量较少、代表性有限，不得仅凭其涨跌幅推断整个行业的普遍走势

---

# 今日市场数据

## 日期与采集时间

交易日期：{overview.date}
行情采集时间（北京时间，UTC+08:00）：{overview.capture_time or "未知"}

## 主要指数
{indices_text if indices_text else "暂无指数数据（接口异常）"}

## 市场概况
- 上涨: {overview.up_count if overview.up_count is not None else "暂无数据"} 家 | 下跌: {overview.down_count if overview.down_count is not None else "暂无数据"} 家 | 平盘: {overview.flat_count if overview.flat_count is not None else "暂无数据"} 家
- 涨停: {overview.limit_up_count if overview.limit_up_count is not None else "暂无数据"} 家 | 跌停: {overview.limit_down_count if overview.limit_down_count is not None else "暂无数据"} 家
- 两市成交额: {total_amount_text}

## 板块表现
数据来源: {sector_source_text if top_sectors_text or bottom_sectors_text else "暂无数据"}
领涨: {top_sectors_text if top_sectors_text else "暂无数据"}
领跌: {bottom_sectors_text if bottom_sectors_text else "暂无数据"}

## 市场新闻
{news_text if news_text else "暂无相关新闻"}

{"注意：主要指数行情未获取。必须明确说明指数数据缺失；不得根据新闻推断当日市场涨跌方向、市场情绪或资金动向。" if not indices_text else ""}

---

# 输出格式模板（请严格按此格式输出）

## 📊 {overview.date} 大盘复盘

### 一、市场总结
（仅概括已获取的指数和市场统计；缺失的数据明确标注未获取，不推测市场方向）

### 二、指数点评
（仅点评实际提供的指数数据；没有指数数据时明确说明未获取）

### 三、成交情况
（仅在成交额有效时描述成交规模；没有资金流向数据时不得推断资金流入或流出）

### 四、热点解读
（仅在板块排行有效时分析板块；没有板块数据时明确说明未获取）

### 五、新闻与事件驱动
（区分公司公告、权威媒体报道和一般市场消息；归纳宏观政策、行业产业链和上市公司事件；说明涉及行业/公司、潜在影响方向及影响周期；明确区分已确认事实与媒体推测；新闻没有明确证据时标注待核实；每条重要新闻保留来源、发布时间和原文链接；不得仅凭新闻标题推断利好或利空）

### 六、后市观察
（结合有效行情和新闻列出观察事项；关键行情缺失时不作方向性预测；说明新闻是否已被当日行情验证或可能已提前反映）

### 七、风险提示
（需要关注的风险点）

---

请直接输出复盘报告内容，不要输出其他说明文字。
"""
        return prompt
    
    def _generate_template_review(self, overview: MarketOverview, news: List) -> str:
        """使用模板生成复盘报告（无大模型时的备选方案）"""
        
        # 判断市场走势
        sh_index = next(
            (idx for idx in overview.indices
             if idx.code in ('000001', 'sh000001', '000001.SH') or idx.name == '上证指数'),
            None
        )
        if sh_index:
            if sh_index.change_pct > 1:
                market_mood = "强势上涨"
            elif sh_index.change_pct > 0:
                market_mood = "小幅上涨"
            elif sh_index.change_pct > -1:
                market_mood = "小幅下跌"
            else:
                market_mood = "明显下跌"
        else:
            market_mood = "行情数据缺失，暂无法判断"
        
        # 指数行情（简洁格式）
        indices_text = ""
        for idx in overview.indices:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            indices_text += f"- **{idx.name}**: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%)\n"
        
        # 板块信息
        def format_sector(sector):
            text = sector["name"]

            count = sector.get("constituent_count")
            if sector.get("small_sample") and count is not None:
                text += f"（仅{count}只成分股，小样本，参考意义有限）"

            return text

        top_text = "、".join(
            format_sector(s) for s in overview.top_sectors[:3]
        ) or "暂无数据"

        bottom_text = "、".join(
            format_sector(s) for s in overview.bottom_sectors[:3]
        ) or "暂无数据"
        sector_source_text = overview.sector_source or "未知"

        total_amount_text = (
            f"{overview.total_amount:.0f}亿"
            if overview.total_amount is not None else "暂无数据"
        )

        news_lines = []
        for item in news[:9]:
            def news_value(key, default=""):
                return getattr(item, key, default) if hasattr(item, key) else item.get(key, default)

            title = str(news_value("title") or "").strip()
            source = str(news_value("source") or "未知来源").strip()
            published = str(news_value("published_date") or "发布时间未提供").strip()
            snippet = str(news_value("snippet") or "").strip()
            url = str(news_value("url") or "").strip()
            if not title:
                continue
            line = f"- **[{source}] {title}**（{published}）"
            if snippet:
                line += f"\\n  {snippet[:240]}"
            if url:
                line += f"\\n  原文：{url}"
            news_lines.append(line)
        news_text = "\\n".join(news_lines) or "未获取到可用新闻，请检查 RSS 和搜索服务状态。"

        report = f"""## 📊 {overview.date} 大盘复盘

### 一、市场总结
今日A股市场：**{market_mood}**。

### 二、主要指数
{indices_text if indices_text else "指数行情未获取，暂无法进行指数点评。"}

### 三、涨跌统计
| 指标 | 数值 |
|------|------|
| 上涨家数 | {overview.up_count if overview.up_count is not None else "暂无数据"} |
| 下跌家数 | {overview.down_count if overview.down_count is not None else "暂无数据"} |
| 涨停 | {overview.limit_up_count if overview.limit_up_count is not None else "暂无数据"} |
| 跌停 | {overview.limit_down_count if overview.limit_down_count is not None else "暂无数据"} |
| 两市成交额 | {total_amount_text} |

### 四、板块表现
- **数据来源**: {sector_source_text if top_text != "暂无数据" or bottom_text != "暂无数据" else "暂无数据"}
- **领涨**: {top_text}
- **领跌**: {bottom_text}

### 五、新闻与事件驱动
{news_text}

说明：本章节仅整理新闻事实和来源，不将媒体报道或未经核实的信息直接视为公司公告或确定性利好利空。

### 六、风险提示
市场有风险，投资需谨慎。以上数据仅供参考，不构成投资建议。

---
*行情采集时间（北京时间，UTC+08:00）: {overview.capture_time or "未知"}*
*报告生成时间（北京时间，UTC+08:00）: {now_china().strftime('%Y-%m-%d %H:%M:%S')}*
"""
        return report
    
    def run_daily_review(self) -> str:
        """
        执行每日大盘复盘流程
        
        Returns:
            复盘报告文本
        """
        logger.info("========== 开始大盘复盘分析 ==========")
        
        # 1. 获取市场概览
        overview = self.get_market_overview()
        
        # 2. 搜索市场新闻
        news = self.search_market_news()
        
        # 3. 生成复盘报告
        report = self.generate_market_review(overview, news)
        
        logger.info("========== 大盘复盘分析完成 ==========")
        
        return report


# 测试入口
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '.')
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    )
    
    analyzer = MarketAnalyzer()
    
    # 测试获取市场概览
    overview = analyzer.get_market_overview()
    print(f"\n=== 市场概览 ===")
    print(f"日期: {overview.date}")
    print(f"指数数量: {len(overview.indices)}")
    for idx in overview.indices:
        print(f"  {idx.name}: {idx.current:.2f} ({idx.change_pct:+.2f}%)")
    print(f"上涨: {overview.up_count} | 下跌: {overview.down_count}")
    print(f"成交额: {overview.total_amount:.0f}亿")
    
    # 测试生成模板报告
    report = analyzer._generate_template_review(overview, [])
    print(f"\n=== 复盘报告 ===")
    print(report)
