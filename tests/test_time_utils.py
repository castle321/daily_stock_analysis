import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.time_utils import CHINA_TIMEZONE, china_time_tuple, now_china


class ChinaTimeUtilsTestCase(unittest.TestCase):
    def test_now_china_uses_asia_shanghai_timezone(self):
        current = now_china()
        self.assertEqual(current.tzinfo, ZoneInfo("Asia/Shanghai"))
        self.assertEqual(current.utcoffset().total_seconds(), 8 * 60 * 60)

    def test_logging_timestamp_is_converted_from_utc_to_china_time(self):
        utc_timestamp = datetime(
            2026, 11, 20, 7, 21, 16, tzinfo=timezone.utc
        ).timestamp()
        local_time = china_time_tuple(utc_timestamp)
        self.assertEqual(
            (local_time.tm_year, local_time.tm_mon, local_time.tm_mday,
             local_time.tm_hour, local_time.tm_min, local_time.tm_sec),
            (2026, 11, 20, 15, 21, 16),
        )

    def test_china_timezone_has_fixed_utc_plus_eight_offset(self):
        self.assertEqual(
            now_china().astimezone(CHINA_TIMEZONE).utcoffset().total_seconds(),
            8 * 60 * 60,
        )


if __name__ == "__main__":
    unittest.main()
