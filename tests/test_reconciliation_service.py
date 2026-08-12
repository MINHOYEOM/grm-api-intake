#!/usr/bin/env python3
"""reconciliation_service 판정 로직 테스트 — 특히 과잉경보 방지 불변식.

단일 소스를 격리 검증할 땐 monitored 를 그 소스로 한정한다(전체 기본 monitored 를
쓰면 current 에 없는 다른 고volume 소스가 0건=silent_drop 으로 정상 발화하기 때문 —
그 동작 자체는 test_missing_source_treated_as_zero 로 따로 검증).
"""
import unittest

from reconciliation_service import (
    MONITORED_FLOORS,
    classify_evidence_url,
    detect_coverage_anomalies,
    summarize_url_contamination,
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

    # ── 기본 monitored = 관측된 전 소스 (2026-08-12 감시 확대) ────────────────────
    # 종전 이 자리엔 test_no_alert_for_unmonitored_source 가 있었고 "EMA·PIC/S 는 기본
    # 집합에 없으니 0건이어도 무발화"를 **정상 동작으로 고정**하고 있었다. 그게 바로
    # 결함이었다 — 손목록에 없는 소스는 죽어도 아무도 모른다. 실측(08-12) PIC/S 13일·
    # MHRA Inspectorate 20일 무음이 그렇게 무발화였다.

    def test_source_absent_from_floors_is_still_monitored(self):
        # ★회귀 가드: MONITORED_FLOORS 에 없는 소스도 이력이 충분하면 감시된다.
        self.assertNotIn("EMA", MONITORED_FLOORS)
        current = {"EMA": 0}
        history = {"EMA": [3, 2, 4]}          # median 3 >= _SILENT_MIN_BASELINE
        anomalies = detect_coverage_anomalies(current, history)  # monitored 미지정
        self.assertEqual([(a.source, a.severity) for a in anomalies],
                         [("EMA", "silent_drop")])

    def test_new_source_with_thin_history_and_no_floor_is_skipped(self):
        # 과잉경보 방지의 반대쪽: 갓 붙은 소스(이력 얇음 + floor 없음)는 무발화.
        current = {"어떤 신규 소스": 0}
        history = {"어떤 신규 소스": [4]}      # < _MIN_HISTORY_WEEKS, floor 없음
        self.assertEqual(detect_coverage_anomalies(current, history), [])

    def test_quiet_source_still_silent_when_monitored_by_default(self):
        # 기본 감시가 넓어져도 "원래 조용한 소스"는 여전히 무발화(중앙값 0 → skip).
        current = {"PIC/S": 0}
        history = {"PIC/S": [0, 0, 1]}         # median 0
        self.assertEqual(detect_coverage_anomalies(current, history), [])

    def test_healthy_week_no_alert_across_all_observed_sources(self):
        # 모든 관측 소스가 정상이면 기본 monitored 가 넓어도 무발화여야 한다.
        current = {"OpenFDA Recall": 12, "MFDS": 26, "FDA 483": 14,
                   "FDA Warning Letter": 3, "EMA": 3, "PIC/S": 2}
        history = {k: [v, v, v] for k, v in current.items()}
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


class ClassifyEvidenceUrlTest(unittest.TestCase):
    def test_service_key_flagged(self):
        url = "https://apis.data.go.kr/1471000/DrugRecall?serviceKey=SECRET123&pageNo=1"
        self.assertEqual(classify_evidence_url(url), "api-key-url")

    def test_apis_data_go_kr_endpoint_flagged(self):
        self.assertEqual(
            classify_evidence_url("https://apis.data.go.kr/1471000/MdcinGmpInfoService06"),
            "api-endpoint",
        )

    def test_data_go_kr_dataset_page_flagged(self):
        self.assertEqual(
            classify_evidence_url("https://www.data.go.kr/data/15127880/openapi.do"),
            "dataset-page",
        )
        self.assertEqual(
            classify_evidence_url("https://data.go.kr/data/15127880/openapi.do"),
            "dataset-page",
        )

    def test_non_http_flagged(self):
        self.assertEqual(classify_evidence_url("ftp://example.com/foo"), "non-http")
        self.assertEqual(classify_evidence_url("그냥 문자열"), "non-http")

    def test_empty_flagged(self):
        self.assertEqual(classify_evidence_url(""), "empty")
        self.assertEqual(classify_evidence_url("   "), "empty")

    def test_normal_urls_pass(self):
        # nedrug 원본 상세 페이지
        self.assertEqual(
            classify_evidence_url(
                "https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=12345"
            ),
            "",
        )
        # FDA 483/미디어 PDF
        self.assertEqual(
            classify_evidence_url("https://www.fda.gov/media/123456/download"),
            "",
        )


class SummarizeUrlContaminationTest(unittest.TestCase):
    def test_mixed_rows_aggregate_by_source_and_reason(self):
        rows = [
            {
                "source": "MFDS",
                "document_type": "행정처분",
                "evidence_url": "https://apis.data.go.kr/1471000/x?serviceKey=SECRET123",
            },
            {
                "source": "MFDS",
                "document_type": "행정처분",
                "evidence_url": "https://apis.data.go.kr/1471000/y?serviceKey=SECRET456",
            },
            {
                "source": "MFDS",
                "document_type": "GMP실사",
                "evidence_url": "https://www.data.go.kr/data/15127880/openapi.do",
            },
            {
                # 정상 행 — 집계에서 제외되어야 함
                "source": "FDA 483",
                "document_type": "483",
                "evidence_url": "https://www.fda.gov/media/123456/download",
            },
        ]
        summary = summarize_url_contamination(rows)
        self.assertEqual(len(summary), 2)

        by_reason = {(g["source"], g["document_type"], g["reason"]): g for g in summary}
        key = ("MFDS", "행정처분", "api-key-url")
        self.assertIn(key, by_reason)
        self.assertEqual(by_reason[key]["n"], 2)
        self.assertIn("sample_url", by_reason[key])

        key2 = ("MFDS", "GMP실사", "dataset-page")
        self.assertIn(key2, by_reason)
        self.assertEqual(by_reason[key2]["n"], 1)

        # 정상행(FDA 483)은 어떤 그룹에도 나타나지 않음
        self.assertFalse(any(g["source"] == "FDA 483" for g in summary))

    def test_no_contamination_returns_empty(self):
        rows = [
            {"source": "FDA 483", "document_type": "483",
             "evidence_url": "https://www.fda.gov/media/1/download"},
        ]
        self.assertEqual(summarize_url_contamination(rows), [])

    def test_service_key_masked_in_sample_url(self):
        rows = [
            {
                "source": "MFDS",
                "document_type": "행정처분",
                "evidence_url": "https://apis.data.go.kr/x?serviceKey=SECRET123&pageNo=1",
            },
        ]
        summary = summarize_url_contamination(rows)
        self.assertEqual(len(summary), 1)
        sample = summary[0]["sample_url"]
        self.assertNotIn("SECRET123", sample)
        self.assertIn("serviceKey=***", sample)
        # 전체 문자열 표현(리포트 라인 조립 시 실 키 미노출 확인)
        rendered = str(summary)
        self.assertNotIn("SECRET123", rendered)


if __name__ == "__main__":
    unittest.main()
