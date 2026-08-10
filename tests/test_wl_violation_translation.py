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
"""
import json
import pathlib
import sys
import tempfile
import unittest

import delta_bridge as db
import inject_slots as inj


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
