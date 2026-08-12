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

    def test_silent_week_recorded_as_zero_not_dropped(self):
        # ★[침묵의 0 2026-08-12] 수집 0건인 주는 raw 행이 없다. 종전 구현은 그 주를
        # 이력에서 **통째로 빼서** 중앙값을 부풀렸다(활동한 주들만의 중앙값).
        rows = (
            [{"source": "ISPE", "ingested_at": _d(1)}] * 2      # week 0
            + [{"source": "ISPE", "ingested_at": _d(15)}] * 6   # week 2 (week 1 은 무음)
            + [{"source": "ISPE", "ingested_at": _d(22)}] * 4   # week 3
        )
        current, history = ra._bucket_by_week(rows, _NOW)
        self.assertEqual(current, {"ISPE": 2})
        # week 1 이 0 으로 보존돼야 한다 — [6, 4] 가 아니라 [0, 6, 4].
        self.assertEqual(history, {"ISPE": [0, 6, 4]})

    def test_weeks_before_first_observation_are_not_zero_filled(self):
        # 반대쪽 과잉: 수집 시작 **전**까지 0 으로 세면 신생 소스가 매주 발화한다.
        # 관측된 가장 오래된 주까지만 메운다.
        rows = [{"source": "MHRA GMP NCR", "ingested_at": _d(8)}] * 5   # week 1 뿐
        _current, history = ra._bucket_by_week(rows, _NOW)
        self.assertEqual(history, {"MHRA GMP NCR": [5]})

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


class ReportCoversExpectedSourcesTest(unittest.TestCase):
    """★[행이 사라지는 사각지대 2026-08-12] 창(9주) 전체가 0건인 소스는 raw 행이 하나도
    없어 `current`·`history` 어디에도 키가 안 생긴다 — 종전 리포트는 두 dict 의 합집합만
    돌아서 **그 소스의 행 자체가 표에서 사라졌다.** 사람이 봐도 "원래 없던 소스인지,
    죽어서 사라졌는지" 구별할 수 없다(가장 오래 사는 침묵이 이 모양이다).

    #727 이 감시 대상과 0-메움을 고쳤지만 그건 **행이 하나라도 있는** 소스 이야기고,
    이 사각지대는 남아 있었다. 기대 소스는 손목록이 아니라 수집 토큰 레지스트리 파생인
    `COVERAGE_SOURCE_LABELS` 에서 온다 — 소스를 추가하면 여기까지 자동으로 따라온다."""

    # ★실제 프로덕션 함수를 부른다 — 테스트가 리포트 로직을 복제하면 호출부는 영영
    #   미검사로 남는다(이 저장소가 #619/#655·#715 에서 반복해 당한 함정).
    _report = staticmethod(lambda current, history: ra.coverage_report_lines(current, history))

    def test_expected_sources_derived_from_registry(self):
        """0건 가드 — 파생이 비면 이 검사는 아무것도 지키지 못한다."""
        self.assertGreaterEqual(
            len({s for s, _ in ra.COVERAGE_SOURCE_LABELS}), 10,
            "기대 소스 파생이 비었다 — COVERAGE_SOURCE_LABELS 배선을 확인할 것")

    def test_source_silent_for_whole_window_still_gets_a_row(self):
        lines = self._report({"FDA 483": 40}, {"FDA 483": [38, 41, 39]})
        for source, _ in ra.COVERAGE_SOURCE_LABELS:
            with self.subTest(source=source):
                self.assertTrue(any(line.startswith(f"- {source}:") for line in lines),
                                f"{source} 행이 리포트에서 사라졌다")
        silent = [ln for ln in lines if "창 전체 0건" in ln]
        self.assertTrue(silent, "창 전체 0건 소스에 표시가 붙지 않았다")
        self.assertFalse([ln for ln in lines if ln.startswith("- FDA 483:") and "창 전체 0건" in ln],
                         "관측된 소스에 '창 전체 0건' 표시가 붙었다")

    def test_unregistered_observed_source_still_listed(self):
        """레지스트리에 없는 소스가 관측되면 그것도 빠지지 않는다(합집합)."""
        lines = self._report({"Mystery Source": 3}, {})
        self.assertTrue(any(ln.startswith("- Mystery Source:") for ln in lines))


if __name__ == "__main__":
    unittest.main()
