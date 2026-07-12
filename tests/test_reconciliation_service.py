#!/usr/bin/env python3
"""reconciliation_service 판정 로직 테스트 — 특히 과잉경보 방지 불변식.

단일 소스를 격리 검증할 땐 monitored 를 그 소스로 한정한다(전체 기본 monitored 를
쓰면 current 에 없는 다른 고volume 소스가 0건=silent_drop 으로 정상 발화하기 때문 —
그 동작 자체는 test_missing_source_treated_as_zero 로 따로 검증).
"""
import unittest

from reconciliation_service import (
    MONITORED_FLOORS,
    detect_coverage_anomalies,
)


class ReconciliationServiceTest(unittest.TestCase):
    def test_silent_drop_fires_with_history_baseline(self):
        current = {"MFDS": 0}
        history = {"MFDS": [30, 24, 28]}
        anomalies = detect_coverage_anomalies(current, history, monitored={"MFDS"})
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].source, "MFDS")
        self.assertEqual(anomalies[0].severity, "silent_drop")
        self.assertEqual(anomalies[0].current, 0)

    def test_silent_drop_fires_on_floor_when_history_thin(self):
        # 이력이 얇아도(1주) floor 로 0건을 잡는다 — 초기 몇 주 방어.
        current = {"OpenFDA Recall": 0}
        history = {"OpenFDA Recall": [14]}  # < _MIN_HISTORY_WEEKS
        anomalies = detect_coverage_anomalies(
            current, history, monitored={"OpenFDA Recall"})
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].severity, "silent_drop")

    def test_no_alert_when_source_normally_quiet(self):
        # 과거 중앙값이 0(원래 조용) → floor 폴백 안 함, 무발화.
        current = {"FDA Warning Letter": 0}
        history = {"FDA Warning Letter": [0, 0, 0]}
        self.assertEqual(
            detect_coverage_anomalies(
                current, history, monitored={"FDA Warning Letter"}),
            [])

    def test_no_alert_for_unmonitored_source(self):
        # EMA·PIC/S 등은 기본 monitored 집합에 없으므로 0건이어도 무발화.
        current = {"EMA": 0, "PIC/S": 0, "MHRA Inspectorate": 0}
        history = {"EMA": [3, 2, 4], "PIC/S": [1, 0, 2]}
        # 완전한 current(모든 고volume 소스 정상)면 기본 monitored 로도 무발화여야 함
        current.update({"OpenFDA Recall": 12, "MFDS": 26,
                        "FDA 483": 14, "FDA Warning Letter": 3})
        history.update({"OpenFDA Recall": [12, 12], "MFDS": [26, 26],
                        "FDA 483": [14, 14], "FDA Warning Letter": [3, 3]})
        self.assertEqual(detect_coverage_anomalies(current, history), [])

    def test_healthy_week_no_alert(self):
        current = {"MFDS": 26, "FDA 483": 14, "OpenFDA Recall": 12,
                   "FDA Warning Letter": 3}
        history = {k: [v, v, v] for k, v in current.items()}
        self.assertEqual(detect_coverage_anomalies(current, history), [])

    def test_volume_drop_soft_warn(self):
        # baseline 30, 이번주 5 (< 30*0.3=9) → volume_drop (0 은 아니므로 silent 아님)
        current = {"MFDS": 5}
        history = {"MFDS": [30, 32, 28]}
        anomalies = detect_coverage_anomalies(current, history, monitored={"MFDS"})
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].severity, "volume_drop")

    def test_volume_drop_not_fired_for_small_baseline(self):
        # baseline 이 작으면(< _SOFT_MIN_BASELINE=8) 부분 낙차는 노이즈라 무발화.
        current = {"FDA Warning Letter": 1}
        history = {"FDA Warning Letter": [4, 3, 5]}  # median 4 < 8
        self.assertEqual(
            detect_coverage_anomalies(
                current, history, monitored={"FDA Warning Letter"}),
            [])

    def test_silent_drop_sorted_first(self):
        current = {"MFDS": 0, "FDA 483": 3}
        history = {"MFDS": [30, 24, 28], "FDA 483": [30, 28, 32]}  # 483: 3 < 30*0.3
        anomalies = detect_coverage_anomalies(
            current, history, monitored={"MFDS", "FDA 483"})
        self.assertEqual([a.severity for a in anomalies],
                         ["silent_drop", "volume_drop"])

    def test_missing_source_treated_as_zero(self):
        # current 에 아예 키가 없으면 0건으로 간주(수집 블록이 통째로 안 돈 경우).
        current: dict[str, int] = {}
        history = {"MFDS": [30, 24, 28]}
        anomalies = detect_coverage_anomalies(current, history, monitored={"MFDS"})
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].severity, "silent_drop")

    def test_floors_cover_expected_high_volume_sources(self):
        for src in ("OpenFDA Recall", "MFDS", "FDA 483", "FDA Warning Letter"):
            self.assertIn(src, MONITORED_FLOORS)


if __name__ == "__main__":
    unittest.main()
