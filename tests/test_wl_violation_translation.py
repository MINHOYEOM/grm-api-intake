"""[WL 위반항목 국문 병기 2026-08-10] `statement_ko` 를 채우는 층이 없던 것을 수리한 회귀 테스트.

`statement_ko` 는 2026-07-20 WL 위반항목 블록 신설 때부터 **스캐폴드와 렌더 템플릿 양쪽에
준비돼 있었는데, 그 값을 채워 넣는 병합층이 어디에도 없었다.** 그래서 WL 위반항목 상세는
도입 이래 줄곧 영문 단독으로 발행됐다(07-20·07-27·08-10 발행본 전부 `{number, statement,
citation}` 뿐). 483 은 `render.validate_483_observations` 가 fail-closed 라 누락이 곧 발행
실패로 드러나지만, WL 은 템플릿이 영문으로 **조용히 degrade** 해 아무도 못 봤다.

여기서 고정하는 것:
  1. `violations_ko` 가 번호로 1:1 매칭돼 `statement_ko` 로 병합된다(원문 `statement` 불변).
  2. 다른 결정론 블록(483 등)은 건드리지 않는다.
  3. `inject_deep_analysis` 가 심층분석 없는 번역 전용 항목도 이 층으로 라우팅한다.
  4. `delta_bridge._gate_deep_analysis` 가 `violations_ko` 단독 항목을 drop 하지 않는다.
  5. 렌더가 실제로 원문+국문을 함께 낸다(슬롯만 있고 안 나오던 게 이 결함의 본질이라
     데이터 병합만 검사하면 같은 함정을 또 밟는다).

[라우틴 생산 채널 2026-08-24] 병합층(위 1~5)만으로는 부족했다 — **라우틴에 산출 지시·입력이
없어** 채널이 세 주 연속(08-10~08-24) 비었고, WL 템플릿의 조용한 영문 degrade 가 결손을
가렸다(08-24 발행분 5카드·14표제문 전건 영문 단독). 추가로 고정하는 것:
  6. `translation_fields()` 가 WL 카드에 `wl_violation_translation_ready`/`_input` 을 방출하고,
     그 입력이 발행 카드 결정론 블록과 글자 단위로 같다(같은 producer — 짝 어긋남 불가능).
  7. fanout `build_wl_translation_jobs`/`assemble_wl_translation_deltas` — 번호 짝 맞춤 게이트.
  8. 브릿지가 4섹션 안에 중첩 예치된 번역층을 entry 층으로 끌어올리고, 분석 게이트 FAIL 시에도
     번역/원문 층을 보존한다(종전엔 entry 통째 drop — "번역은 별도 층으로 산다"는 프롬프트
     약속이 이 경로에서 거짓이었다).
  9. 결손이 소리 나게 막힌다 — 조립 게이트 6(`_lint_wl_violation_ko`) + 배포 fail-closed
     (`render.validate_wl_violations`). WARN 은 아무도 안 읽는다.
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import date, datetime

import card_scaffold as cs
import deep_analysis_fanout as fan
import delta_bridge as db
import grm_handoff as gh
import inject_slots as inj
from assemble_publish_brief import _lint_wl_violation_ko

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def _load_golden(name):
    with open(os.path.join(GOLDEN, f"{name}.input.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _wl_card():
    return {
        "id": "wl-1",
        "card_type": "Warning Letter",
        "deterministic_detail": {
            "type": "wl_violations",
            "count": 2,
            "violations": [
                {"number": "1", "citation": "21 CFR 211.22(a)",
                 "statement": "Your firm failed to establish an adequate quality control unit."},
                {"number": "2", "citation": "21 CFR 211.100(a)",
                 "statement": "Your firm failed to establish adequate written procedures."},
            ],
        },
    }


class MergeWlViolationTranslationsTest(unittest.TestCase):

    def test_merges_by_number_and_keeps_original(self):
        card = _wl_card()
        rep = inj.InjectionReport()
        inj._merge_wl_violation_translations(card, [
            {"number": "2", "statement_ko": "귀사는 적절한 문서화된 절차를 수립하지 않았다."},
            {"number": "1", "statement_ko": "귀사는 적절한 품질관리부서를 두지 않았다."},
        ], rep, "wl-1")
        v = card["deterministic_detail"]["violations"]
        # 번호로 매칭 — 델타의 순서가 아니라 번호가 기준이다.
        self.assertEqual(v[0]["statement_ko"], "귀사는 적절한 품질관리부서를 두지 않았다.")
        self.assertEqual(v[1]["statement_ko"], "귀사는 적절한 문서화된 절차를 수립하지 않았다.")
        # 원문·조항은 불변(additive 병합)
        self.assertEqual(v[0]["statement"],
                         "Your firm failed to establish an adequate quality control unit.")
        self.assertEqual(v[0]["citation"], "21 CFR 211.22(a)")
        self.assertTrue(any("violations_ko" in w for w in rep.warnings))

    def test_unmatched_number_skipped_silently(self):
        card = _wl_card()
        inj._merge_wl_violation_translations(
            card, [{"number": "9", "statement_ko": "없는 번호."}], inj.InjectionReport(), "wl-1")
        for v in card["deterministic_detail"]["violations"]:
            self.assertNotIn("statement_ko", v)

    def test_other_detail_types_untouched(self):
        card = {"id": "x", "deterministic_detail": {
            "type": "fda_483_observations", "count": 1,
            "observations": [{"number": "1", "deficiency": "en"}]}}
        inj._merge_wl_violation_translations(
            card, [{"number": "1", "statement_ko": "국문"}], inj.InjectionReport(), "x")
        self.assertNotIn("statement_ko", card["deterministic_detail"]["observations"][0])

    def test_empty_or_malformed_delta_is_noop(self):
        for bad in (None, [], {}, "x", [None, 3]):
            card = _wl_card()
            inj._merge_wl_violation_translations(card, bad, inj.InjectionReport(), "wl-1")
            for v in card["deterministic_detail"]["violations"]:
                self.assertNotIn("statement_ko", v)

    def test_inject_deep_analysis_routes_violations_ko(self):
        """심층분석 없는 번역 전용 항목도 `inject_deep_analysis` 가 처리한다."""
        brief = {"cards": [_wl_card()]}
        rep = inj.inject_deep_analysis(
            brief, {"wl-1": {"violations_ko": [{"number": "1", "statement_ko": "국문 해석."}]}})
        self.assertEqual(
            brief["cards"][0]["deterministic_detail"]["violations"][0]["statement_ko"],
            "국문 해석.")
        self.assertFalse(rep.errors)


class BridgeGateAllowsViolationsKoTest(unittest.TestCase):
    """손열거 허용목록이 낡아 새 번역층이 조용히 drop 되던 함정(이번에 실제로 밟았다)."""

    def test_violations_only_entry_survives(self):
        deep = {"wl-1": {"violations_ko": [{"number": "1", "statement_ko": "국문."}]}}
        self.assertEqual(db._gate_deep_analysis(deep), deep)

    def test_allowlist_covers_every_known_translation_layer(self):
        for key in ("observations_ko", "ncr_ko", "violations_ko", "source_text"):
            self.assertIn(key, db._TRANSLATION_ONLY_KEYS, f"{key} 가 허용목록에서 빠졌다")

    def test_empty_entry_still_dropped(self):
        self.assertIsNone(db._gate_deep_analysis({"x": {"nonsense": 1}}))


class WlTranslationEmissionTest(unittest.TestCase):
    """§6 — handoff 방출: WL 카드만, 발행 카드 결정론 블록과 같은 원문을."""

    def _scaffold(self, name):
        data = _load_golden(name)
        return cs.build_card_scaffold(data["row"], data["raw"])

    def test_wl_card_emits_translation_input(self):
        card = self._scaffold("warning_letter_violations")
        fields = card.translation_fields()
        self.assertTrue(fields.get("wl_violation_translation_ready"))
        self.assertEqual(fields.get("kind"), card.kind)
        rows = fields["wl_violation_translation_input"]
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(set(r), {"number", "statement"})

    def test_input_matches_published_detail_verbatim(self):
        """번역 입력 = 발행 카드 결정론 블록과 글자 단위 동일(같은 producer 확인)."""
        card = self._scaffold("warning_letter_violations")
        detail = card.to_web_card()["deterministic_detail"]
        rows = card.translation_fields()["wl_violation_translation_input"]
        self.assertEqual(
            [(r["number"], r["statement"]) for r in rows],
            [(v["number"], v["statement"]) for v in detail["violations"]])

    def test_non_wl_cards_do_not_emit(self):
        for name in ("fda_483_observations", "eu_gmp_ncr"):
            with self.subTest(name=name):
                fields = self._scaffold(name).translation_fields()
                self.assertNotIn("wl_violation_translation_ready", fields)

    def test_both_serializers_carry_the_fields(self):
        """`to_dict()` 와 실제 Notion handoff v2 빌더가 둘 다 싣는다(직렬화기 분열 재발 방지)."""
        data = _load_golden("warning_letter_violations")
        card = cs.build_card_scaffold(data["row"], data["raw"])
        self.assertTrue(card.to_dict().get("wl_violation_translation_ready"))
        row = dict(data["row"], raw=data["raw"])
        payload = gh.build_routine_handoff_payload_v2(
            [row], date(2026, 8, 24), 7, datetime(2026, 8, 24, 7, 30))
        rows = payload.get("rows") or []
        self.assertTrue(rows, "handoff v2 행이 비었다")
        self.assertTrue(rows[0].get("wl_violation_translation_ready"),
                        "handoff v2 에 WL 번역 입력이 실리지 않았다")
        self.assertTrue(rows[0].get("wl_violation_translation_input"))


class WlTranslationJobsTest(unittest.TestCase):
    """§7 — fanout 작업 변환 + 번호 짝 맞춤 게이트."""

    HANDOFF = {"cards": [
        {"card_id": "FDA WL::wl-1", "kind": "warning-letter",
         "wl_violation_translation_ready": True,
         "wl_violation_translation_input": [
             {"number": "1", "statement": "Your firm failed A."},
             {"number": "2", "statement": "Your firm failed B."}]},
        {"card_id": "EudraGMDP::186339", "kind": "eu-gmp-ncr",
         "ncr_translation_ready": True,
         "ncr_translation_input": {"nature": "Critical deficiencies."}},
    ]}

    def test_build_picks_only_wl(self):
        jobs = fan.build_wl_translation_jobs(self.HANDOFF)
        self.assertEqual([j.document_id for j in jobs], ["wl-1"])
        self.assertEqual([v["number"] for v in jobs[0].violations], ["1", "2"])

    def test_ncr_jobs_unaffected(self):
        jobs = fan.build_translation_jobs(self.HANDOFF)
        self.assertEqual([j.document_id for j in jobs], ["186339"])

    def test_assemble_produces_violations_ko(self):
        jobs = fan.build_wl_translation_jobs(self.HANDOFF)
        out = fan.assemble_wl_translation_deltas(jobs, {"wl-1": [
            {"number": "2", "statement_ko": "국문 B."},
            {"number": "1", "statement_ko": "국문 A."}]})
        self.assertEqual(out, {"wl-1": {"violations_ko": [
            {"number": "2", "statement_ko": "국문 B."},
            {"number": "1", "statement_ko": "국문 A."}]}})

    def test_assemble_drops_numbers_without_source(self):
        """작업에 없는 번호의 번역은 버린다 — 근거 없는 국문 차단(NCR 게이트와 동형)."""
        jobs = fan.build_wl_translation_jobs(self.HANDOFF)
        out = fan.assemble_wl_translation_deltas(jobs, {"wl-1": [
            {"number": "1", "statement_ko": "국문 A."},
            {"number": "9", "statement_ko": "지어낸 문장."},
            {"number": "2", "statement_ko": "   "}]})
        self.assertEqual(out["wl-1"]["violations_ko"],
                         [{"number": "1", "statement_ko": "국문 A."}])

    def test_missing_response_is_not_an_error(self):
        jobs = fan.build_wl_translation_jobs(self.HANDOFF)
        self.assertEqual(fan.assemble_wl_translation_deltas(jobs, {}), {})

    def test_jobs_roundtrip_through_json(self):
        jobs = fan.build_wl_translation_jobs(self.HANDOFF)
        raw = json.loads(json.dumps([j.to_dict() for j in jobs]))
        out = fan.assemble_wl_translation_deltas(
            raw, {"wl-1": [{"number": "1", "statement_ko": "국문 A."}]})
        self.assertIn("wl-1", out)


class BridgeTranslationSurvivalTest(unittest.TestCase):
    """§8 — 중첩 예치 리프팅 + 분석 게이트 FAIL 시 번역/원문 층 보존."""

    _BAD_DEEP = {"key_violations": []}   # 4섹션 미충족 — D1 FAIL 확정

    def test_nested_violations_ko_lifted_and_survives_gate_fail(self):
        deep = {"wl-1": {"deep_analysis": dict(
            self._BAD_DEEP, violations_ko=[{"number": "1", "statement_ko": "국문."}]),
            "source_text": "Your firm failed A."}}
        kept = db._gate_deep_analysis(deep)
        self.assertIsNotNone(kept)
        self.assertNotIn("deep_analysis", kept["wl-1"])
        self.assertEqual(kept["wl-1"]["violations_ko"],
                         [{"number": "1", "statement_ko": "국문."}])
        self.assertEqual(kept["wl-1"]["source_text"], "Your firm failed A.")

    def test_entry_level_translation_survives_gate_fail(self):
        deep = {"wl-1": {"deep_analysis": dict(self._BAD_DEEP),
                         "violations_ko": [{"number": "1", "statement_ko": "국문."}]}}
        kept = db._gate_deep_analysis(deep)
        self.assertEqual(kept, {"wl-1": {"violations_ko":
                                         [{"number": "1", "statement_ko": "국문."}]}})

    def test_observations_ko_survives_gate_fail_too(self):
        """483 도 같은 결함이었다 — 프롬프트의 "별도 층으로 산다" 약속이 이 경로에서 거짓이면
        render fail-closed 게이트가 그 주 브리프 전체를 막는 데까지 번진다."""
        deep = {"fda483-1": {"deep_analysis": dict(self._BAD_DEEP),
                             "observations_ko": [{"number": "1", "deficiency_ko": "국문."}]}}
        kept = db._gate_deep_analysis(deep)
        self.assertIn("observations_ko", kept["fda483-1"])
        self.assertNotIn("deep_analysis", kept["fda483-1"])

    def test_analysis_only_fail_entry_still_dropped(self):
        deep = {"wl-1": {"deep_analysis": dict(self._BAD_DEEP)}}
        self.assertIsNone(db._gate_deep_analysis(deep))


class WlViolationKoGateTest(unittest.TestCase):
    """§9 — 결손은 소리 나게: 조립 게이트 6 + 배포 fail-closed."""

    def test_assemble_gate_flags_missing_ko(self):
        errs = _lint_wl_violation_ko([_wl_card()])
        self.assertEqual(len(errs), 2)
        self.assertTrue(all("statement_ko 없음" in e for e in errs))

    def test_assemble_gate_passes_when_complete(self):
        card = _wl_card()
        for v in card["deterministic_detail"]["violations"]:
            v["statement_ko"] = "국문 해석."
        self.assertEqual(_lint_wl_violation_ko([card]), [])

    def test_assemble_gate_ignores_non_wl_and_blockless(self):
        cards = [
            {"id": "a", "deterministic_detail": {"type": "fda_483_observations",
                                                 "observations": [{"number": "1"}]}},
            {"id": "b", "card_type": "Warning Letter"},   # 위반 블록 없는 WL(추출 0건)
        ]
        self.assertEqual(_lint_wl_violation_ko(cards), [])

    def _render_mod(self):
        web_dir = pathlib.Path(__file__).resolve().parent.parent / "web"
        sys.path.insert(0, str(web_dir))
        import render  # noqa: E402
        return render

    def test_render_validate_flags_missing_ko(self):
        render = self._render_mod()
        brief = {"brief": {"publish_date": "2026-08-24"}, "cards": [_wl_card()]}
        out = render.validate_wl_violations([brief])
        self.assertEqual(len(out), 2)
        self.assertTrue(all("MISSING_STATEMENT_KO" in v for v in out))
        self.assertIn("2026-08-24", out[0])

    def test_render_validate_passes_when_complete(self):
        render = self._render_mod()
        card = _wl_card()
        for v in card["deterministic_detail"]["violations"]:
            v["statement_ko"] = "국문 해석."
        self.assertEqual(render.validate_wl_violations([card]), [])


class WlViolationRenderSmokeTest(unittest.TestCase):
    """슬롯이 있어도 렌더가 안 내면 의미가 없다 — 이 결함의 본질이 정확히 그것이었다."""

    def _render(self, card):
        """실제 사이트 렌더를 태워 HTML 을 얻는다(기존 DeepAnalysisRenderSmokeTest 와 동형).

        헬퍼 부재로 skip 하면 정작 이 결함(슬롯은 있는데 값이 안 나옴)을 검사하는 테스트가
        침묵 미실행이 된다 — 그건 이 수리가 막으려는 실패 양상 그 자체다."""
        web_dir = pathlib.Path(__file__).resolve().parent.parent / "web"
        sys.path.insert(0, str(web_dir))
        import render  # noqa: E402
        brief = {
            "schema_version": "grm-web-card/v1",
            "brief": {"run_date_kst": "2026-07-01", "window": "2026-06-24 ~ 2026-07-01",
                      "publish_date": "2026-07-01", "intake_total": 1, "tldr": []},
            "cards": [card],
        }
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = pathlib.Path(tmp) / "data"
            data_dir.mkdir()
            (data_dir / "brief_web_2026_07_01.json").write_text(
                json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
            out_dir = pathlib.Path(tmp) / "dist"
            render.render_site(data_dir=data_dir, out_dir=out_dir)
            return (out_dir / "briefs" / "2026-07-01" / "index.html").read_text(encoding="utf-8")

    def test_ko_and_original_both_rendered(self):
        card = _wl_card()
        card["deterministic_detail"]["violations"][0]["statement_ko"] = "귀사는 품질관리부서를 두지 않았다."
        html = self._render(card)
        self.assertIn("귀사는 품질관리부서를 두지 않았다.", html)
        self.assertIn("Your firm failed to establish an adequate quality control unit.", html)
        self.assertIn("국문 해석", html)

    def test_without_ko_falls_back_to_english_only(self):
        html = self._render(_wl_card())
        self.assertIn("Your firm failed to establish an adequate quality control unit.", html)
        self.assertNotIn("국문 해석", html)


if __name__ == "__main__":
    unittest.main()
