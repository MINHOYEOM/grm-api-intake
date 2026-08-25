"""[483 심층분석 과거주차 소급 2026-08-25] `backfill_deep_483_past_weeks` 순수 로직 검사.

이 도구가 채우는 두 구멍만 검사한다 — 나머지(게이트·수리·병합)는 운영 경로를 그대로
호출하므로 그쪽 테스트가 이미 지킨다.
  ① 과거 주차 deep 델타 → fan-out 작업 계약(운영 build_jobs 는 handoff 를 받는데 과거
     handoff 는 CI 아티팩트라 만료됐다)
  ② 병합 전 `deep_analysis` placeholder 삽입(#453 이 못 세운 fan-out 표지)

★대상 선별이 이 도구의 안전장치다 — 원문 없는 카드·483 아닌 카드·이미 심층분석이 있는
카드에 표지를 세우면 없는 근거로 생성을 요구하거나 사람 백필분을 덮어쓰게 된다.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_deep_483_past_weeks as bf  # noqa: E402

_SOURCE = "OBSERVATION 1: " + ("Personnel failed to follow procedures. " * 30)


def _card(doc_id: str, card_type: str = bf.CARD_TYPE_483, **extra):
    return {"id": doc_id, "card_type": card_type, **extra}


def _brief(*cards):
    return {"cards": list(cards)}


class SelectTargetsTest(unittest.TestCase):
    def test_picks_483_card_with_source_and_no_deep(self) -> None:
        brief = _brief(_card("fda483-1"))
        deep = {"fda483-1": {"source_text": _SOURCE}}
        self.assertEqual([t["document_id"] for t in bf.select_targets(brief, deep)],
                         ["fda483-1"])

    def test_skips_card_without_source_text(self) -> None:
        """★원문이 없으면 대상이 아니다 — 근거 없는 생성을 요구하지 않는다."""
        brief = _brief(_card("fda483-1"))
        for payload in ({}, {"source_text": ""}, {"source_text": "짧은 원문"}):
            with self.subTest(payload=payload):
                self.assertEqual(bf.select_targets(brief, {"fda483-1": payload}), [])

    def test_skips_card_that_already_has_deep_analysis(self) -> None:
        """사람 백필분을 덮어쓰지 않는다."""
        brief = _brief(_card("fda483-1", deep_analysis={"key_violations": []}))
        self.assertEqual(bf.select_targets(brief, {"fda483-1": {"source_text": _SOURCE}}), [])

    def test_skips_when_delta_already_carries_generated_deep(self) -> None:
        brief = _brief(_card("fda483-1"))
        deep = {"fda483-1": {"source_text": _SOURCE, "deep_analysis": {"x": 1}}}
        self.assertEqual(bf.select_targets(brief, deep), [])

    def test_skips_non_483_card_types(self) -> None:
        """★유형 범위 가드 — WL·행정처분은 이 소급의 대상이 아니다(다른 프롬프트를 쓴다)."""
        brief = _brief(_card("wl-1", card_type="Warning Letter"),
                       _card("adm-1", card_type="행정처분"))
        deep = {"wl-1": {"source_text": _SOURCE}, "adm-1": {"source_text": _SOURCE}}
        self.assertEqual(bf.select_targets(brief, deep), [])

    def test_skips_id_absent_from_brief(self) -> None:
        self.assertEqual(bf.select_targets(_brief(), {"fda483-x": {"source_text": _SOURCE}}), [])

    def test_order_is_deterministic(self) -> None:
        brief = _brief(_card("fda483-3"), _card("fda483-1"), _card("fda483-2"))
        deep = {k: {"source_text": _SOURCE} for k in ("fda483-3", "fda483-1", "fda483-2")}
        self.assertEqual([t["document_id"] for t in bf.select_targets(brief, deep)],
                         ["fda483-1", "fda483-2", "fda483-3"])


class BuildJobsTest(unittest.TestCase):
    def test_job_matches_fanout_contract(self) -> None:
        """운영 `deep_analysis_fanout.Job.to_dict()` 와 같은 키·같은 의미여야 한다."""
        import deep_analysis_fanout as fan
        targets = bf.select_targets(_brief(_card("fda483-1")),
                                    {"fda483-1": {"source_text": _SOURCE}})
        job = bf.build_jobs(targets)[0]
        reference = fan.Job(document_id="fda483-1", body_full=_SOURCE,
                            card_type=bf.JOB_CARD_TYPE).to_dict()
        self.assertEqual(job, reference)

    def test_body_full_is_the_source_text_verbatim(self) -> None:
        targets = bf.select_targets(_brief(_card("fda483-1")),
                                    {"fda483-1": {"source_text": _SOURCE}})
        self.assertEqual(bf.build_jobs(targets)[0]["body_full"], _SOURCE)


class BuildDeltasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.targets = bf.select_targets(_brief(_card("fda483-1"), _card("fda483-2")),
                                         {"fda483-1": {"source_text": _SOURCE},
                                          "fda483-2": {"source_text": _SOURCE}})

    def test_splits_observations_ko_from_the_four_sections(self) -> None:
        """게이트는 4섹션만 본다 — 관찰 국문은 별도 델타 키로 실린다(운영 규약 동형)."""
        da = {"key_violations": [], "observations_ko": [{"number": "1", "ko": "가"}]}
        deltas, missing = bf.build_deltas(self.targets, {"fda483-1": da})
        self.assertEqual(missing, ["fda483-2"])
        entry = deltas["fda483-1"]
        self.assertNotIn("observations_ko", entry["deep_analysis"])
        self.assertEqual(entry["observations_ko"], [{"number": "1", "ko": "가"}])
        self.assertEqual(entry["source_text"], _SOURCE)

    def test_response_object_is_not_mutated(self) -> None:
        """★입력을 파괴하지 않는다 — 같은 응답으로 재실행해도 결과가 같아야 한다."""
        da = {"key_violations": [], "observations_ko": [{"number": "1"}]}
        bf.build_deltas(self.targets, {"fda483-1": da})
        self.assertIn("observations_ko", da)

    def test_non_dict_response_counts_as_missing(self) -> None:
        for bad in (None, "문자열", 42, []):
            with self.subTest(bad=bad):
                deltas, missing = bf.build_deltas(self.targets, {"fda483-1": bad})
                self.assertEqual(deltas, {})
                self.assertIn("fda483-1", missing)


class MarkFanoutTargetsTest(unittest.TestCase):
    def test_inserts_placeholder_only_for_selected_ids(self) -> None:
        brief = _brief(_card("fda483-1"), _card("fda483-2"))
        targets = bf.select_targets(brief, {k: {"source_text": _SOURCE}
                                            for k in ("fda483-1", "fda483-2")})
        self.assertEqual(bf.mark_fanout_targets(targets, {"fda483-1"}), 1)
        cards = {c["id"]: c for c in brief["cards"]}
        self.assertIsNone(cards["fda483-1"]["deep_analysis"])
        self.assertNotIn("deep_analysis", cards["fda483-2"])   # 응답 없는 카드엔 표지 없음

    def test_existing_key_is_left_alone(self) -> None:
        """★이미 표지가 있으면 건드리지 않는다(값을 None 으로 되돌리지 않는다)."""
        existing = {"key_violations": [{"citation": "21 CFR 211.100"}]}
        brief = _brief(_card("fda483-1", deep_analysis=existing))
        targets = [{"document_id": "fda483-1", "source_text": _SOURCE,
                    "card": brief["cards"][0]}]
        self.assertEqual(bf.mark_fanout_targets(targets, {"fda483-1"}), 0)
        self.assertEqual(brief["cards"][0]["deep_analysis"], existing)


class EndToEndMergeTest(unittest.TestCase):
    """★배관 검사 — 표지 삽입 → 운영 게이트 → 병합이 실제로 이어지는지."""

    def _fixture(self):
        deficiency = ("Personnel were observed conducting aseptic manipulations where "
                      "the movement of first air in the ISO 5 area is blocked.")
        detail = ("Specifically, on 05/05/2026 a compounding technician was observed "
                  "blocking the bag port and needle during preparation of an IV bag.")
        # ★`select_targets` 의 원문 최소길이(_MIN_SOURCE_LEN)를 넘겨야 대상이 된다 — 짧은
        # 픽스처는 조용히 0건이 돼 배관 검사가 아무것도 안 재게 된다(이 테스트를 처음 쓸 때
        # 실제로 그렇게 통과할 뻔했다).
        filler = ("Additional observations regarding environmental monitoring and "
                  "personnel qualification were recorded during the inspection. " * 5)
        source = (f"OBSERVATION 1: {deficiency}\n{detail}\n\n"
                  f"OBSERVATION 2: Smoke studies were not performed. {filler}")
        self.assertGreater(len(source), bf._MIN_SOURCE_LEN)   # 픽스처 자체를 검사한다
        brief = _brief(_card("fda483-1"))
        deep = {"fda483-1": {"source_text": source}}
        da = {
            "key_violations": [{"citation": "21 CFR 211.42",
                                "observation": "무균조작 중 first air 차단이 관찰됐다.",
                                "original": f"{deficiency} {detail}",
                                "risk": "제품 오염 위험이 증가한다."}],
            "inspectional_significance": "무균보증 체계 결함으로 볼 수 있는 관찰이다.",
            "required_remediation": {"deadline": "15영업일", "items": ["동선 재교육"]},
            "administrative_risks": "미시정 시 Warning Letter 로 이어질 수 있다.",
        }
        return brief, deep, da, deficiency

    def test_placeholder_then_gate_then_merge(self) -> None:
        import inject_slots
        brief, deep, da, _ = self._fixture()
        targets = bf.select_targets(brief, deep)
        deltas, _ = bf.build_deltas(targets, {"fda483-1": da})
        bf.mark_fanout_targets(targets, set(deltas))
        report = inject_slots.inject_deep_analysis(brief, deltas)
        self.assertEqual(report.errors, [])
        self.assertEqual(brief["cards"][0]["deep_analysis"], da)

    def test_without_placeholder_merge_is_refused(self) -> None:
        """★표지 삽입이 왜 필요한지를 고정한다 — 이것이 없으면 69장이 조용히 안 실린다."""
        import inject_slots
        brief, deep, da, _ = self._fixture()
        targets = bf.select_targets(brief, deep)
        deltas, _ = bf.build_deltas(targets, {"fda483-1": da})
        report = inject_slots.inject_deep_analysis(brief, deltas)   # 표지 삽입 생략
        self.assertNotIn("deep_analysis", brief["cards"][0])
        self.assertTrue(any("대상이 아님" in w for w in report.warnings), report.warnings)

    def test_truncated_original_is_repaired_by_operational_path(self) -> None:
        """운영 경로의 D5b 결정론 수리가 이 소급에도 그대로 적용된다(#790)."""
        import inject_slots
        brief, deep, da, deficiency = self._fixture()
        da = {**da, "key_violations": [{**da["key_violations"][0],
                                        "original": deficiency}]}   # ← 절단본
        targets = bf.select_targets(brief, deep)
        deltas, _ = bf.build_deltas(targets, {"fda483-1": da})
        bf.mark_fanout_targets(targets, set(deltas))
        inject_slots.inject_deep_analysis(brief, deltas)
        merged = brief["cards"][0]["deep_analysis"]
        self.assertIsNotNone(merged)
        self.assertIn("Specifically", merged["key_violations"][0]["original"])


if __name__ == "__main__":
    unittest.main()
