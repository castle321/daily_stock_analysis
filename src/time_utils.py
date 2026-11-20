"""统一的中国标准时间工具。"""

from datetime import datetime
from zoneinfo import ZoneInfo


CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def now_china() -> datetime:
    """返回带 Asia/Shanghai 时区信息的当前时间。"""
    return datetime.now(CHINA_TIMEZONE)


def china_time_tuple(timestamp: float):
    """将 logging 的 Unix 时间戳转换为中国标准时间。"""
    return datetime.fromtimestamp(timestamp, CHINA_TIMEZONE).timetuple()
