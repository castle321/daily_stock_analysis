# -*- coding: utf-8 -*-
"""行情日期与实时行情一致性校验。"""

from typing import Any, Dict, Optional


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def assess_market_data_quality(
    market_context: Dict[str, Any],
    daily_data: Dict[str, Any],
    realtime_data: Dict[str, Any],
    change_pct_tolerance: float = 1.0,
) -> Dict[str, Any]:
    """
    校验最新日K是否覆盖当前交易日，以及同日涨跌幅是否明显冲突。

    不比较日K价格与实时价格的绝对值：日K可能是前复权价格，
    实时行情通常是未复权价格，两者不一定处于相同价格基准。
    """
    market_context = market_context or {}
    daily_data = daily_data or {}
    realtime_data = realtime_data or {}

    data_date = (
        market_context.get("data_date")
        or daily_data.get("date")
    )
    analysis_date = str(market_context.get("as_of") or "")[:10] or None
    phase = market_context.get("phase")
    issues = []

    if phase == "session_unverified":
        if data_date:
            issues.append(
                f"当前为工作日A股交易时段，但最新日K日期为{data_date}，"
                f"与分析日期{analysis_date or '未知'}不一致；日K均线、涨跌幅和量能"
                "仅代表该历史交易日，不能作为当前交易日信号"
            )
        else:
            issues.append(
                "当前为工作日A股交易时段，但最新日K日期缺失；"
                "无法确认技术指标和量能对应的交易日"
            )

    daily_change = _to_float(daily_data.get("pct_chg"))
    realtime_change = _to_float(realtime_data.get("change_pct"))
    same_day_bar = bool(market_context.get("daily_bar_matches_today"))

    if (
        same_day_bar
        and market_context.get("trading_day_in_progress") is True
        and daily_change is not None
        and realtime_change is not None
        and abs(daily_change - realtime_change) > change_pct_tolerance
    ):
        issues.append(
            "当日日K涨跌幅与实时行情涨跌幅明显冲突："
            f"日K {daily_change:.2f}%、实时行情 {realtime_change:.2f}%，"
            f"差值 {abs(daily_change - realtime_change):.2f} 个百分点"
        )

    blocks_actionable_decision = bool(issues)
    return {
        "status": "blocked" if blocks_actionable_decision else "ok",
        "blocks_actionable_decision": blocks_actionable_decision,
        "analysis_date": analysis_date,
        "daily_bar_date": str(data_date)[:10] if data_date else None,
        "realtime_source": realtime_data.get("source"),
        "issues": issues,
        "reason": "；".join(issues),
        "change_pct_tolerance": change_pct_tolerance,
    }
