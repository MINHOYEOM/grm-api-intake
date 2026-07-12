#!/usr/bin/env python3
"""reconciliation_audit 의 주(週) 버킷팅 로직 테스트(네트워크 없음)."""
import unittest
from datetime import datetime, timedelta, timezone

import reconciliation_audit as ra

_NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)


def _d(days: int) -> str:
    return (_NOW - timedelta(days=days)).isoformat()


class BucketByWeekTest(unittest.TestCase):
    def test_current_and_history_buckets(self):
        rows = (
            [{"source": "MFDS", "ingested_at": _d(1)}] * 3      # 이번 주(week 0)
            + [{"source": "MFDS", "ingested_at": _d(8)}] * 10   # 1주 전
            + [{"source": "MFDS", "ingested_at": _d(15)}] * 12  # 2주 전
        )
        current, history = ra._bucket_by_week(rows, _NOW)
        self.assertEqual(current, {"MFDS": 3})
        self.assertEqual(history, {"MFDS": [10, 12]})

    def test_junk_rows_dropped(self):
        rows = [
            {"source": "", "ingested_at": _d(1)},          # 빈 source
            {"source": "MFDS", "ingested_at": "bad-ts"},   # 파싱 불가 시각
            {"source": "MFDS", "ingested_at": _d(1)},      # 유효
        ]
        current, history = ra._bucket_by_week(rows, _NOW)
        self.assertEqual(current, {"MFDS": 1})
        self.assertEqual(history, {})

    def test_future_timestamp_counts_as_current(self):
        # 시계 오차로 미래 시각이 와도 음수 days → week 0 으로 흡수(방어).
        rows = [{"source": "FDA 483", "ingested_at": _d(-1)}]
        current, _ = ra._bucket_by_week(rows, _NOW)
        self.assertEqual(current, {"FDA 483": 1})

    def test_beyond_lookback_ignored(self):
        # 조회 윈도우(_LOOKBACK_WEEKS)보다 오래된 건 history 에서 제외.
        old = _d(7 * (ra._LOOKBACK_WEEKS + 2))
        rows = [{"source": "MFDS", "ingested_at": old}]
        current, history = ra._bucket_by_week(rows, _NOW)
        self.assertEqual(current, {})
        self.assertEqual(history, {})

    def test_date_only_timestamp_parses(self):
        # ingested_at 이 날짜만이어도(방어) 파싱된다.
        parsed = ra._parse_iso("2026-07-11")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
