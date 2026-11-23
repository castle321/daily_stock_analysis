# -*- coding: utf-8 -*-
"""A股交易时段上下文（按中国标准时间推算）。"""

from datetime import datetime, time
from typing import Any, Dict, Optional

from src.time_utils import CHINA_TIMEZONE, now_china


def get_a_share_market_context(
    now: Optional[datetime] = None,
    data_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    返回用于行情分析的A股交易时段上下文。

    只有最新日K日期与中国当前日期一致时，才确认当日日K正在盘中更新。
    常规交易时段内若日期不匹配或缺失，返回 None 作为未确认状态。

    注意：这里只按周一至周五和常规交易时段推算，不包含法定节假日、
    临时休市及特殊交易安排；不能替代官方交易日历。
    """
    current = now or now_china()
    if current.tzinfo is None:
        current = current.replace(tzinfo=CHINA_TIMEZONE)
    else:
        current = current.astimezone(CHINA_TIMEZONE)

    current_time = current.time().replace(tzinfo=None)
    is_weekday = current.weekday() < 5
    is_in_standard_hours = (
        is_weekday and time(9, 30) <= current_time < time(15, 0)
    )
    normalized_data_date = str(data_date)[:10] if data_date else None
    daily_bar_matches_today = normalized_data_date == current.date().isoformat()

    if is_in_standard_hours and not daily_bar_matches_today:
        phase = "session_unverified"
        phase_label = "工作日常规交易时段（当日日K日期未核验）"
    elif not is_weekday:
        phase = "weekend"
        phase_label = "周末休市"
    elif current_time < time(9, 30):
        phase = "pre_open"
        phase_label = "开盘前"
    elif current_time < time(11, 30):
        phase = "morning_session"
        phase_label = "上午交易时段"
    elif current_time < time(13, 0):
        phase = "lunch_break"
        phase_label = "午间休市（当日交易尚未结束）"
    elif current_time < time(15, 0):
        phase = "afternoon_session"
        phase_label = "下午交易时段"
    else:
        phase = "post_close"
        phase_label = "收盘后"

    # 午间休市期间，当天日K仍未最终定稿，因此整个 09:30–15:00
    # 区间均按“交易日进行中”处理；但必须核验日K日期。
    if is_in_standard_hours and daily_bar_matches_today:
        trading_day_in_progress = True
    elif is_in_standard_hours:
        trading_day_in_progress = None
    else:
        trading_day_in_progress = False

    return {
        "as_of": current.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "phase": phase,
        "phase_label": phase_label,
        "is_weekday": is_weekday,
        "data_date": normalized_data_date,
        "daily_bar_matches_today": daily_bar_matches_today,
        "trading_day_in_progress": trading_day_in_progress,
        "calendar_note": (
            "按周一至周五及A股常规交易时间推算，未接入法定节假日、"
            "临时休市日历；节假日或特殊交易安排需以数据源实际交易日期为准。"
        ),
    }
