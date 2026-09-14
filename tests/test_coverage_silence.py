"""브리프 coverage 줄에서 **무음**과 **한산한 주**를 구별하는지 못박는다(§3 2026-09-14).

2026-09-14호는 6개 소스가 멈춘 상태를 `MHRA 0 · PIC/S 0 · ICH 0 · WHO 0 · HC 0 · EU NCR 0`
으로, 즉 한산한 주와 구별되지 않는 모습으로 발행했다. 무음 감시가 잡은 소스는
`PIC/S 0(점검필요)` 로 찍혀야 하고, 발행 게이트(`brief_lint.lint_coverage_counts`)는 그
표식을 파싱해야 한다 — 종전 앵커 정규식은 표식의 `)` 에서 끊겨 그 뒤 소스가 전부
"누락" 으로 오판됐다(이 파일의 round-trip 테스트가 그 회귀를 잡는다).
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from datetime import date, datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import brief_lint
import collect_intake as ci
import grm_handoff
from grm_health import source_enabled_map
from source_silence import evaluate_silence

RUN = date(2026, 9, 14)
GEN_AT = datetime(2026, 9, 14, 9, 0, 0)


class CoverageSilentMarkTest(unittest.TestCase):
    def test_silent_source_is_marked_and_others_are_not(self) -> None:
        cov = grm_handoff.build_coverage_collected(
            {ci.SOURCE_MFDS: 3}, silent_sources={ci.SOURCE_PICS, ci.SOURCE_ICH})
        self.assertIn("PIC/S 0(점검필요)", cov["md"])
        self.assertIn("ICH 0(점검필요)", cov["md"])
        self.assertIn("MFDS 3 ·", cov["md"])
        self.assertNotIn("MFDS 3(점검필요)", cov["md"])
        by_label = {it["label"]: it for it in cov["items"]}
        self.assertTrue(by_label["PIC/S"]["silent"])
        self.assertFalse(by_label["MFDS"]["silent"])
        self.assertEqual(cov["total"], 3)

    def test_no_silent_sources_is_byte_identical_to_before(self) -> None:
        """빈 집합이면 md 가 종전과 바이트 동일 — 골든·발행 프롬프트 전사 불변."""
        counts = {ci.SOURCE_MFDS: 3, ci.SOURCE_FR: 2}
        self.assertEqual(grm_handoff.build_coverage_collected(counts)["md"],
                         grm_handoff.build_coverage_collected(counts, silent_sources=())["md"])
        self.assertNotIn("(점검필요)", grm_handoff.build_coverage_collected(counts)["md"])

    def test_v2_payload_carries_marker_and_key_only_when_silent(self) -> None:
        with_silent = ci.build_routine_handoff_payload_v2(
            [], RUN, 7, GEN_AT, silent_sources={ci.SOURCE_WHO})
        self.assertIn("WHO 0(점검필요)", with_silent["coverage_collected_md"])
        self.assertEqual(with_silent["coverage_silent_sources"], [ci.SOURCE_WHO])
        without = ci.build_routine_handoff_payload_v2([], RUN, 7, GEN_AT)
        self.assertNotIn("coverage_silent_sources", without, "빈 집합이면 payload 키 불변(골든)")
        self.assertNotIn("(점검필요)", without["coverage_collected_md"])


class PublishGateParsesMarkerTest(unittest.TestCase):
    """발행 게이트가 표식을 깨지 않고 파싱하는지 — round-trip 으로 못박는다."""

    def test_parse_reads_marker_and_keeps_sources_after_it(self) -> None:
        text = ("Intake row 5건 (FR 2 · PIC/S 0(점검필요) · MFDS 3) · 병합 1 · "
                "WebSearch 0")
        parsed = brief_lint.parse_collected_coverage(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["total"], 5)
        # ★표식 뒤의 MFDS 3 이 살아 있어야 한다 — 종전 앵커는 여기서 끊겼다.
        self.assertEqual(parsed["items"], {"FR": 2, "PIC/S": 0, "MFDS": 3})
        self.assertEqual(parsed["flagged"], {"PIC/S": "점검필요"})

    def test_round_trip_with_silent_sources_yields_no_findings(self) -> None:
        counts = {ci.SOURCE_FR: 2, ci.SOURCE_MFDS: 30, ci.SOURCE_FDA_483: 4}
        expected = grm_handoff.build_coverage_collected(
            counts, silent_sources={ci.SOURCE_PICS, ci.SOURCE_MHRA, ci.SOURCE_ICH,
                                    ci.SOURCE_WHO, ci.SOURCE_HC, ci.SOURCE_EU_GMP_NCR})
        published = expected["md"] + " · 병합 0 · WebSearch 2"
        findings = brief_lint.lint_coverage_counts(expected, published)
        self.assertEqual([f.code for f in findings], [],
                         "무음 표식이 붙은 coverage 줄을 게이트가 불일치로 오판하면 안 된다")

    def test_marker_does_not_hide_a_real_mismatch(self) -> None:
        """표식이 게이트를 무력화하면 안 된다 — 표식 뒤의 건수 오기는 여전히 FAIL."""
        counts = {ci.SOURCE_FR: 2, ci.SOURCE_MFDS: 30}
        expected = grm_handoff.build_coverage_collected(counts, silent_sources={ci.SOURCE_PICS})
        published = expected["md"].replace("MFDS 30", "MFDS 29")
        codes = [f.code for f in brief_lint.lint_coverage_counts(expected, published)]
        self.assertIn("PL15-COVERAGE-SOURCE", codes)

    def test_plain_line_without_marker_parses_as_before(self) -> None:
        parsed = brief_lint.parse_collected_coverage(
            "Intake row 36건 (FR 2건 · Recall 1건 · FDA WL 3건 · MFDS 30건)")
        self.assertEqual(parsed["items"], {"FR": 2, "Recall": 1, "FDA WL": 3, "MFDS": 30})
        self.assertEqual(parsed["flagged"], {})


class SilentSetUsesTheSameEnabledMapTest(unittest.TestCase):
    """coverage 줄의 무음 집합은 health 와 **같은 활성 매핑**으로 판정된다.

    끈 소스(예: ENABLE_ICH=false)가 오래됐다는 이유로 `ICH 0(점검필요)` 로 찍히면
    거짓 경보다 — 매핑을 안 넘기면 기본 True 라 그렇게 된다(뮤테이션 확인).
    """

    def _enabled(self, **over):
        base = dict(active={"fr"}, enable_search=False, enable_mfds=False,
                    enable_mfds_law=False, enable_mfds_recall=False, enable_mfds_admin=False,
                    enable_mfds_gmp_cert=False, enable_mfds_safety_letter=False,
                    enable_mfds_gmp_inspection=False, enable_ich=False, enable_who=False,
                    enable_hc=False, enable_fda483=False)
        base.update(over)
        return source_enabled_map(**base)

    def test_disabled_source_is_not_in_the_silent_set(self) -> None:
        seen = {ci.SOURCE_WHO: date(2026, 6, 1)}   # 105일 > 10일
        off = {f.notion_source for f in evaluate_silence(
            last_seen=seen, run_date=RUN, source_enabled=self._enabled(enable_who=False))}
        on = {f.notion_source for f in evaluate_silence(
            last_seen=seen, run_date=RUN, source_enabled=self._enabled(enable_who=True))}
        self.assertNotIn(ci.SOURCE_WHO, off)
        self.assertIn(ci.SOURCE_WHO, on)

    def test_map_covers_every_registry_prefix(self) -> None:
        """레지스트리에 prefix 를 더하고 매핑을 빠뜨리면 여기서 걸린다."""
        keys = set(self._enabled())
        for spec in ci.INTAKE_SOURCE_SPECS:
            self.assertIn(spec.prefix, keys, f"{spec.prefix} 가 source_enabled_map 에 없다")


class WatchdogRunsBeforeHandoffTest(unittest.TestCase):
    """무음 조회가 handoff **앞**에 있어야 coverage 줄에 실린다 — 순서를 못박는다."""

    def test_query_precedes_handoff_emit_in_main(self) -> None:
        src = inspect.getsource(ci.main)
        q = src.find("query_last_seen(")
        e = src.find("emit_routine_handoff(")
        h = src.find("_evaluate_health(")
        self.assertGreater(q, 0)
        self.assertGreater(e, 0)
        self.assertLess(q, e, "무음 조회가 handoff 뒤에 있으면 coverage 줄이 무음을 모른다")
        self.assertLess(e, h)
        # handoff 호출에 무음 집합이 실제로 넘어가야 한다(조회만 앞당기고 안 넘기면 무의미).
        self.assertIn("silent_sources=coverage_silent_sources", src)


if __name__ == "__main__":
    unittest.main()
