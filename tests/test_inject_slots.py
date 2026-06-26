"""inject_slots 테스트 — v16 LLM 델타 → scaffold 빈슬롯 브리프 주입(grm-web-card/v1).

검증: card.id 정합·positional 번역(비KO 채움·KO null 보존)·길이 어긋남 거부·마크업 값
거부·누락 카드 경고·코드필드 불변·결정론 + render.py 연계 스모크(산문 렌더). 추가만 —
card_scaffold/render/골든 불변(이 테스트는 신규 모듈만 행사).
"""
from __future__ import annotations

import copy
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

import card_scaffold as cs
import inject_slots as inj

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")

# 주입으로 바뀔 수 있는 LLM 슬롯(이 외 카드 필드는 코드 verbatim — 불변이어야 함).
_MUTABLE_SLOTS = {"title_issue", "summary", "key_facts", "implication", "checks"}


def _load_input(name: str) -> dict:
    with io.open(os.path.join(GOLDEN, name + ".input.json"), encoding="utf-8") as f:
        return json.load(f)


def _build_scaffold() -> dict:
    """§5 소형 scaffold: 비KO Evidence A(quotes 2) · KO Evidence A(quotes 2) · Evidence B."""
    cards = []
    for name in ("guidance_fr", "gmp_inspection_biologic", "mfds_notice"):
        fx = _load_input(name)
        cards.append(cs.build_card_scaffold(fx["row"], fx["raw"]))
    return cs.assemble_web_brief(cards, {
        "run_date_kst": "2026-06-22", "window": "2026-06-15 ~ 2026-06-22",
        "publish_date": "2026-06-22", "intake_total": 3,
    })


def _ids(brief: dict) -> dict[str, dict]:
    """evidence/quotes 특성으로 카드를 골라 쓰기 쉽게 id 매핑."""
    out = {}
    for c in brief["cards"]:
        if c["evidence_level"] == "A" and c["quotes"] and c["quotes"][0]["translation"] == "":
            out["nonko_a"] = c["id"]
        elif c["evidence_level"] == "A" and c["quotes"] and c["quotes"][0]["translation"] is None:
            out["ko_a"] = c["id"]
        elif c["evidence_level"] == "B":
            out["b"] = c["id"]
    return out


def _strip_slots(card: dict) -> dict:
    """LLM 슬롯(+quote translation)을 제거한 코드 필드만 — 불변 비교용."""
    d = {k: v for k, v in card.items() if k not in _MUTABLE_SLOTS}
    d["quotes"] = [{k: v for k, v in q.items() if k != "translation"} for q in card["quotes"]]
    return d


class InjectHappyPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = _build_scaffold()
        self.id = _ids(self.brief)
        self.delta = {
            "cards": {
                self.id["nonko_a"]: {
                    "title_issue": "신규 산업 가이던스 발표",
                    "summary": "FDA 가 품질 기대치를 명확히 했다.",
                    "key_facts": ["발효 즉시", "대상 = 무균 제조"],
                    "implication": "국내 제조소 SOP 점검 필요.",
                    "checks": ["해당 품목 식별", "SOP 갭 분석"],
                    "quotes_translation": ["첫 문장 번역.", "둘째 문장 번역."],
                },
                self.id["ko_a"]: {
                    "title_issue": "GMP 실사 결과 공개",
                    "summary": "생물학적제제 제조소 점검 결과.",
                    "checks": ["실사 대상 확인", "후속 조치 추적", "사내 공유"],
                    # KO 인용 — quotes_translation 미제공(null 유지).
                },
                self.id["b"]: {
                    "title_issue": "식약처 고시 개정",
                    "implication": "고시 변경 모니터링.",
                },
            },
            "tldr": ["이번 주 핵심 1", "이번 주 핵심 2", "이번 주 핵심 3"],
        }

    def test_slots_injected(self) -> None:
        out = inj.inject_llm_slots(self.brief, self.delta)
        by_id = {c["id"]: c for c in out["cards"]}
        a = by_id[self.id["nonko_a"]]
        self.assertEqual(a["title_issue"], "신규 산업 가이던스 발표")
        self.assertEqual(a["key_facts"], ["발효 즉시", "대상 = 무균 제조"])
        self.assertEqual(a["checks"], ["해당 품목 식별", "SOP 갭 분석"])
        self.assertEqual([q["translation"] for q in a["quotes"]],
                         ["첫 문장 번역.", "둘째 문장 번역."])
        self.assertEqual(out["brief"]["tldr"],
                         ["이번 주 핵심 1", "이번 주 핵심 2", "이번 주 핵심 3"])

    def test_ko_translation_stays_null(self) -> None:
        out = inj.inject_llm_slots(self.brief, self.delta)
        ko = next(c for c in out["cards"] if c["id"] == self.id["ko_a"])
        self.assertEqual([q["translation"] for q in ko["quotes"]], [None, None])

    def test_unprovided_keys_untouched(self) -> None:
        # Evidence B 카드는 summary/key_facts/checks 미제공 → scaffold 빈값 유지.
        out = inj.inject_llm_slots(self.brief, self.delta)
        b = next(c for c in out["cards"] if c["id"] == self.id["b"])
        self.assertEqual(b["summary"], "")
        self.assertEqual(b["key_facts"], [])
        self.assertEqual(b["checks"], [])
        self.assertEqual(b["implication"], "고시 변경 모니터링.")

    def test_code_fields_byte_identical(self) -> None:
        before = {c["id"]: _strip_slots(c) for c in self.brief["cards"]}
        out = inj.inject_llm_slots(self.brief, self.delta)
        after = {c["id"]: _strip_slots(c) for c in out["cards"]}
        self.assertEqual(json.dumps(before, ensure_ascii=False, sort_keys=True),
                         json.dumps(after, ensure_ascii=False, sort_keys=True))

    def test_input_not_mutated(self) -> None:
        snapshot = copy.deepcopy(self.brief)
        inj.inject_llm_slots(self.brief, self.delta)
        self.assertEqual(self.brief, snapshot)

    def test_determinism(self) -> None:
        a = json.dumps(inj.inject_llm_slots(self.brief, self.delta), ensure_ascii=False, sort_keys=True)
        b = json.dumps(inj.inject_llm_slots(self.brief, self.delta), ensure_ascii=False, sort_keys=True)
        self.assertEqual(a, b)


class InjectGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = _build_scaffold()
        self.id = _ids(self.brief)

    def _delta(self, card_slots: dict, *, tldr=None) -> dict:
        d = {"cards": {self.id["b"]: card_slots}}
        if tldr is not None:
            d["tldr"] = tldr
        return d

    def test_markup_value_rejected(self) -> None:
        for bad in ("**굵게**", "{{TITLE}}", "### 머리말", "<callout>x"):
            with self.subTest(bad=bad):
                with self.assertRaises(inj.SlotInjectionError):
                    inj.inject_llm_slots(self.brief, self._delta({"summary": bad}))

    def test_leading_bullet_and_quote_rejected(self) -> None:
        for bad in ("- 항목", "> 인용문"):
            with self.subTest(bad=bad):
                with self.assertRaises(inj.SlotInjectionError):
                    inj.inject_llm_slots(self.brief, self._delta({"summary": bad}))

    def test_inline_hyphen_allowed(self) -> None:
        # 문장 중간 하이픈/부등호는 마크업 아님 — 허용.
        out = inj.inject_llm_slots(self.brief, self._delta({"summary": "A-B 비교는 5 < 10."}))
        b = next(c for c in out["cards"] if c["id"] == self.id["b"])
        self.assertEqual(b["summary"], "A-B 비교는 5 < 10.")

    def test_title_issue_length_rejected(self) -> None:
        with self.assertRaises(inj.SlotInjectionError):
            inj.inject_llm_slots(self.brief, self._delta({"title_issue": "가" * 26}))

    def test_key_facts_over_limit_rejected(self) -> None:
        with self.assertRaises(inj.SlotInjectionError):
            inj.inject_llm_slots(self.brief, self._delta({"key_facts": ["a", "b", "c", "d", "e"]}))

    def test_checks_count_rejected(self) -> None:
        for bad in (["only one"], ["a", "b", "c", "d"]):
            with self.subTest(n=len(bad)):
                with self.assertRaises(inj.SlotInjectionError):
                    inj.inject_llm_slots(self.brief, self._delta({"checks": bad}))

    def test_tldr_length_rejected(self) -> None:
        with self.assertRaises(inj.SlotInjectionError):
            inj.inject_llm_slots(self.brief, self._delta({}, tldr=["one", "two"]))

    def test_quotes_translation_length_mismatch_rejected(self) -> None:
        # 비KO Evidence A(quotes 2)에 길이 1 번역 → positional 어긋남.
        delta = {"cards": {self.id["nonko_a"]: {"quotes_translation": ["only one"]}}}
        with self.assertRaises(inj.SlotInjectionError):
            inj.inject_llm_slots(self.brief, delta)

    def test_translation_into_ko_slot_rejected(self) -> None:
        # KO(null) 자리에 번역 주입 시도 → 거부.
        delta = {"cards": {self.id["ko_a"]: {"quotes_translation": ["불가", "불가"]}}}
        with self.assertRaises(inj.SlotInjectionError):
            inj.inject_llm_slots(self.brief, delta)

    def test_missing_card_warns_not_fails(self) -> None:
        # 일부 카드만 델타 제공 → 나머지는 경고(비차단), 슬롯 빈 채 유지.
        delta = {"cards": {self.id["b"]: {"summary": "하나만."}}}
        report = inj.validate_injection(self.brief, delta)
        self.assertTrue(report.ok)               # 누락은 error 아님
        self.assertTrue(any("델타에 산문 없음" in w for w in report.warnings))
        out = inj.inject_llm_slots(self.brief, delta)  # 차단 안 됨
        self.assertEqual(len(out["cards"]), 3)

    def test_ghost_delta_key_warns(self) -> None:
        delta = {"cards": {"no-such-id": {"summary": "x"}, self.id["b"]: {"summary": "y"}}}
        report = inj.validate_injection(self.brief, delta)
        self.assertTrue(any("브리프에 없는 카드 id" in w for w in report.warnings))

    def test_lax_mode_best_effort(self) -> None:
        # strict=False 면 검증 실패라도 가능한 슬롯 주입(운영 기본은 strict).
        delta = {"cards": {self.id["b"]: {"summary": "**bad**", "implication": "good"}}}
        out = inj.inject_llm_slots(self.brief, delta, strict=False)
        b = next(c for c in out["cards"] if c["id"] == self.id["b"])
        self.assertEqual(b["implication"], "good")


class InjectRenderSmokeTest(unittest.TestCase):
    """DoD #3 — 완성 브리프 → render.py 빌드 → 산문(요약·시사점·점검·tldr) 렌더."""

    def test_prose_renders(self) -> None:
        web_dir = pathlib.Path(__file__).resolve().parent.parent / "web"
        sys.path.insert(0, str(web_dir))
        import render  # noqa: E402

        brief = _build_scaffold()
        cid = _ids(brief)
        delta = {
            "cards": {cid["nonko_a"]: {
                "summary": "주입된요약문장입니다",
                "implication": "주입된시사점입니다",
                "checks": ["점검항목하나", "점검항목둘"],
            }},
            "tldr": ["요약헤드라인하나", "요약헤드라인둘", "요약헤드라인셋"],
        }
        out = inj.inject_llm_slots(brief, delta)

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = pathlib.Path(tmp) / "data"
            data_dir.mkdir()
            (data_dir / "brief_web_2026_06_22.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            out_dir = pathlib.Path(tmp) / "dist"
            render.render_site(data_dir=data_dir, out_dir=out_dir)
            html = (out_dir / "briefs" / "2026-06-22" / "index.html").read_text(encoding="utf-8")

        self.assertIn("주입된요약문장입니다", html)
        self.assertIn("주입된시사점입니다", html)
        self.assertIn("점검항목하나", html)
        self.assertIn("요약헤드라인하나", html)


if __name__ == "__main__":
    unittest.main()
