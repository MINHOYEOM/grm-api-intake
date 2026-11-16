#!/usr/bin/env python3
"""링크드인 사건 해부 GRM 판(v3 시범) 테스트 — 순수 층만(Chrome·네트워크 0).

CI 는 `tests/test_web_linkedin_grm.py` shim 으로 순회. 직접: python web/tests/test_linkedin_grm.py
"""
from __future__ import annotations

import json
from pathlib import Path
import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import linkedin_anatomy as la  # noqa: E402
import linkedin_cards as lc  # noqa: E402
import linkedin_grm as lg  # noqa: E402

BRIEF = WEB_DIR / "data" / "briefs" / "brief_web_2026_11_02.json"
SLOTS = WEB_DIR / "data" / "linkedin_anatomy" / "2026-11-02.v3.json"
# 11-02 시범 슬롯의 숫자(예: s05 ‘태그로는 35건’)는 그 주 DB 에서 센 값이다. 라이브 findings_docs.json 을 읽으면
# 주간 재측정(GRM Findings Doc Pages Refresh)이 새 서한·복원 본문을 들일 때마다 이 테스트가 깨져 자동 PR 이 막힌다
# (11-07·11-14). 그래서 그 주 말뭉치에서 이 덱이 읽는 조각만 동결한 스냅숏으로 검사한다 — 갱신하지 않는다.
FINDINGS_SNAPSHOT = pathlib.Path(__file__).resolve().parent / "fixtures" / "linkedin_grm_findings_2026-11-02.json.gz"
FINDINGS = lg.load_findings(FINDINGS_SNAPSHOT)


def _doc() -> dict:
    return json.loads(BRIEF.read_text(encoding="utf-8"))


def _slots() -> dict:
    return json.loads(SLOTS.read_text(encoding="utf-8"))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.card = la.pick_anatomy_card(_doc())

    def _p(self, mutate):
        s = _slots()
        mutate(s)
        return lg.validate_v3(self.card, s, FINDINGS)

    def test_pilot_passes(self):
        self.assertEqual(lg.validate_v3(self.card, _slots(), FINDINGS), [])

    def test_finding_rows_need_verbatim_evidence_and_matching_numbers(self):
        p = self._p(lambda s: s["s02"]["rows"][0].update(evidence=["90건 넘는 균을 동정하지 않았다"]))
        self.assertTrue(any("s02.rows[0]" in x and "원천에 없다" in x for x in p), p)
        p = self._p(lambda s: s["s02"]["rows"][0].update(text="ISO 5 검출 130건 넘게, 90건은 ‘유사 집락’으로만 기록"))
        self.assertTrue(any("130" in x for x in p), p)

    def test_reply_pair_needs_evidence(self):
        p = self._p(lambda s: s["s03"]["pairs"][0].pop("evidence"))
        self.assertTrue(any("s03.pairs[0]" in x for x in p), p)

    def test_every_fda_request_covered(self):
        p = self._p(lambda s: s["s04"]["items"].pop())
        self.assertTrue(any("FDA 요구" in x for x in p), p)

    def test_db_comment_numbers_must_come_from_db(self):
        p = self._p(lambda s: s["s05"].update(comment="서한 전에 483을 받은 업체가 99곳이다."))
        self.assertTrue(any("99" in x for x in p), p)

    def test_dots_must_match_evidence(self):
        p = self._p(lambda s: s["cover"]["dots"].update(marked=3))
        self.assertTrue(any("cover.dots" in x for x in p), p)

    def test_ai_tell_and_firm_leak_blocked(self):
        p = self._p(lambda s: s["caption"].update(lead="Northland 는 조사가 부족했다는 신호다."))
        self.assertTrue(any("AI 티" in x for x in p) and any("실명" in x for x in p), p)

    def test_not_stated_needs_basis_and_question_form(self):
        p = self._p(lambda s: s["s06"]["not_stated"][0].pop("basis"))
        self.assertTrue(any("not_stated" in x for x in p), p)
        p = self._p(lambda s: s["s06"].update(question="동정 건수를 확인한다"))
        self.assertTrue(any("질문형" in x for x in p), p)


class DbSimilarTest(unittest.TestCase):
    def setUp(self):
        self.db = lg.db_similar(FINDINGS, la.pick_anatomy_card(_doc()))

    def test_counts_derive_from_documents(self):
        em = [d for d in FINDINGS["documents"] if d["source"] == "FDA Warning Letter" and "환경모니터링" in d["categories"]]
        self.assertEqual(self.db["em_n"], len(em))
        self.assertEqual(sum(self.db["em_years"].values()), len(em))
        self.assertLessEqual(self.db["em_483_before"], self.db["em_firms"])

    def test_cases_labeled_by_level_and_verified_against_text(self):
        labels = {v[0]: k for k, v in lg.FACETS.items()}
        self.assertEqual(len(self.db["cases"]), 3)
        self.assertEqual([c["level"] for c in self.db["cases"]], sorted((c["level"] for c in self.db["cases"]), key=list(lg.LEVELS).index))
        for c in self.db["cases"]:
            doc = next(d for d in FINDINGS["documents"] if d["slug"] == c["slug"])
            self.assertEqual(doc["source"], "FDA Warning Letter")
            self.assertEqual(c["label"], lg.LEVELS[c["level"]])
            sents = [s for f in doc["findings"] for s in lg._sentences(f.get("text_orig"))]
            for lab in c["facets"]:
                self.assertTrue(any(lg.FACETS[labels[lab]][1](s) for s in sents), (c["slug"], lab))
            if c["level"] == "pattern":
                self.assertGreaterEqual(len(c["facets"]), 2)
            if c["level"] in ("both", "clause"):
                refs = [r for f in doc["findings"] for r in (f.get("cfr_refs") or [])]
                self.assertTrue(c["cfr"] in refs, c)
        again = lg.db_similar(FINDINGS, la.pick_anatomy_card(_doc()))
        self.assertEqual(self.db["cases"], again["cases"])

    def test_cases_do_not_name_firms(self):
        out = " ".join(lg.case_lines(self.db, polite=True))
        for c in self.db["cases"]:
            doc = next(d for d in FINDINGS["documents"] if d["slug"] == c["slug"])
            for t in lg._firm_tokens(doc["firm_name"]):
                self.assertNotIn(t.lower(), out.lower())

    def test_never_writes_zero_or_shortage(self):
        out = " ".join(lg.case_lines(self.db, polite=True) + lg.case_lines(self.db))
        self.assertNotIn("0건", out)
        self.assertNotIn("유사 사례 부족", out)
        self.assertEqual(self.db["id_n"], 0)
        self.assertIn("넓혀", out)

    def test_widening_fills_when_exact_matches_missing(self):
        tiers = dict(both=0, pattern=0, clause=0)
        cases, got, _ = lg.similar_cases([], "", 3)
        self.assertEqual((cases, got), ([], tiers))


class DeckTest(unittest.TestCase):
    def setUp(self):
        self.deck = lg.build_grm_deck(_doc(), _slots(), FINDINGS)

    def test_fixed_format_order(self):
        self.assertEqual([s["kind"] for s in self.deck["slides"]],
                         ["g_cover", "g_find", "g_reply", "g_req", "g_site", "g_sim", "g_end"])
        self.assertEqual([s.get("corner") for s in self.deck["slides"][1:]],
                         ["지적 내용", "회신 평가", "시정 요구", "유사 사례", "DB 연관", "서한에 없는 것"])

    def test_caption_paragraph_order_and_no_hashtag(self):
        # 2026-11-16 배치 개편: 첫 단락(사건)은 소제목 없이, 이후 ■ 소제목 단락. 한 줄에 한 문장.
        cap = self.deck["caption"]
        heads = [ln[2:] for ln in cap.splitlines() if ln.startswith("■ ")]
        self.assertEqual(heads, ["지적 내용", "회사 회신과 FDA 판단", "FDA 요구", "GRM 지적사항 DB의 비슷한 사례",
                                 "관련 용어", "서한에 없는 것", "전체 해설"][:len(heads)] if "■ 관련 용어" in cap else
                         ["지적 내용", "회사 회신과 FDA 판단", "FDA 요구", "GRM 지적사항 DB의 비슷한 사례", "서한에 없는 것", "전체 해설"])
        self.assertFalse(cap.startswith("■"))
        self.assertTrue(cap.split("■ 지적 내용\n", 1)[1].startswith("지적은"))
        self.assertNotIn("#", cap.split("■ 전체 해설")[0])

    def test_caption_urls_on_their_own_lines(self):
        # 2026-11-16 대표 지적: 링크가 잘 안 걸린다 — 주소는 문장·괄호와 섞지 않고 자기 줄에 둔다.
        import re
        for ln in self.deck["caption"].splitlines():
            if "grm-solutions.com" in ln:
                self.assertRegex(ln, r"^https://grm-solutions\.com/\S+$")

    def test_every_site_url_in_caption_carries_utm(self):
        # 2026-11-04 — 글자만 적힌 주소도 스킴+표식(linkedin/social/{pub}_weekly) 이 붙는다.
        import re
        urls = re.findall(r"\S*grm-solutions\.com\S*", self.deck["caption"])
        self.assertTrue(urls)
        for u in urls:
            self.assertIn("https://grm-solutions.com", u)
            self.assertIn("utm_source=linkedin&utm_medium=social&utm_campaign=", u)

    def test_no_firm_name_and_ai_notice_kept(self):
        out = lg.render_html(self.deck, font_links=False) + self.deck["caption"]
        self.assertNotIn("Northland", out)
        for s in self.deck["slides"]:
            self.assertEqual(s["ai"], lc.AI_NOTE)

    def test_invalid_slots_raise(self):
        s = _slots()
        s["cover"]["dots"]["marked"] = 9
        with self.assertRaises(la.AnatomyError):
            lg.build_grm_deck(_doc(), s, FINDINGS)

    def test_no_paid_api_and_live_module_untouched(self):
        self.assertNotIn("import anthropic", (WEB_DIR / "linkedin_grm.py").read_text(encoding="utf-8"))
        self.assertNotIn("linkedin_grm", (WEB_DIR / "linkedin_cards.py").read_text(encoding="utf-8"))



class SiteCasesTest(unittest.TestCase):
    def setUp(self):
        self.sim = json.loads((WEB_DIR / "data" / "linkedin_anatomy" / "2026-11-02.similar.json").read_text(encoding="utf-8"))
        self.rows = lg.site_cases(self.sim, FINDINGS)

    def test_one_row_per_finding_with_label_and_doc_link(self):
        self.assertEqual([r["q"] for r in self.rows], [1, 2, 3, 4])
        for r in self.rows:
            self.assertIn(r["label"], lg.SITE_LEVELS.values())
            self.assertTrue(r["slug"])

    def test_rows_come_from_the_site_engine_capture(self):
        texts = {i["text"] for i in self.sim["items"]}
        self.assertTrue(all(r["text"] in texts for r in self.rows))

    def test_smoke_row_is_fact_match(self):
        r4 = next(r for r in self.rows if r["q"] == 4)
        self.assertEqual(r4["level"], "fact")

    def test_items_missing_from_db_copy_are_not_picked(self):
        sim = dict(self.sim, items=[dict(i, finding_id="finding-not-in-db") for i in self.sim["items"]])
        self.assertEqual(lg.site_cases(sim, FINDINGS), [])

    def test_missing_capture_falls_back(self):
        self.assertEqual(lg.site_cases(None, FINDINGS), [])


class FetchSimilarTest(unittest.TestCase):
    def _rpc(self, calls):
        def rpc(url, key, query, limit):
            calls.append((query, limit))
            return [dict(finding_id=f"f{limit}", score=0.1, source="FDA Warning Letter", firm_name="Acme", published_date="2026-01-01", text="t")]
        return rpc

    def test_calls_engine_per_finding_and_builds_capture(self):
        calls = []
        sim = lg.fetch_similar(_slots(), url="https://x", key="k", rpc=self._rpc(calls))
        self.assertEqual(len(calls), 4)  # 지적마다 limit 50 한 번(상위 5 + 경고서한 3 을 거기서 고른다)
        self.assertEqual({it["q"] for it in sim["items"]}, {1, 2, 3, 4})
        self.assertEqual(sim["rules"]["4"][0], "fact")

    def test_failure_or_empty_gives_none(self):
        def boom(*a):
            raise OSError("down")
        self.assertIsNone(lg.fetch_similar(_slots(), url="u", key="k", rpc=boom))
        self.assertIsNone(lg.fetch_similar(_slots(), url="u", key="k", rpc=lambda *a: []))

    def test_one_finding_failing_keeps_the_rest(self):
        def flaky(url, key, query, limit):
            if "스모크" in query:
                raise OSError("timeout")
            return [dict(finding_id="f", score=0.1, source="FDA 483", firm_name="A", published_date="2026-01-01", text="t")]
        sim = lg.fetch_similar(_slots(), url="u", key="k", rpc=flaky)
        self.assertEqual({it["q"] for it in sim["items"]}, {1, 2, 3})
        self.assertEqual(sim["failed_findings"], 1)

    def test_saved_capture_wins_and_rpc_is_not_called(self):
        """[2026-11-09] 배포마다 findings_similar 를 다시 부르던 것이 anon 3초 타임아웃 500 의
        주 발생원이었다 — 저장본이 있으면 엔진을 부르지 않는다."""
        calls = []
        sim = lg.load_similar(_slots(), "2026-11-02", url="https://x", key="k",
                              fetch=lambda *a, **k: calls.append(1) or {"items": [1]})
        self.assertEqual(calls, [])
        self.assertEqual(sim["source"][:8], "supabase")  # 커밋된 2026-11-02.similar.json
        self.assertTrue(sim["items"])

    def test_no_saved_capture_calls_rpc_only_with_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            calls = []
            fetch = lambda *a, **k: calls.append(1) or {"items": [{"q": 1}]}  # noqa: E731
            got = lg.load_similar(_slots(), "2099-01-05", url="u", key="k", fetch=fetch, slots_dir=Path(d))
            self.assertEqual(calls, [1])
            self.assertEqual(got, {"items": [{"q": 1}]})
            self.assertIsNone(lg.load_similar(_slots(), "2099-01-05", url="", key="", fetch=fetch,
                                              slots_dir=Path(d)))
            self.assertEqual(calls, [1])
            (Path(d) / "2099-01-05.similar.json").write_text('{"items": []}', encoding="utf-8")
            lg.load_similar(_slots(), "2099-01-05", url="u", key="k", fetch=fetch, slots_dir=Path(d))
            self.assertEqual(calls, [1, 1], "빈 저장본은 저장본이 아니다 — 엔진을 부른다")

    def test_deck_without_similar_drops_only_that_card(self):
        deck = lg.build_grm_deck(_doc(), _slots(), FINDINGS, similar={})
        kinds = [s["kind"] for s in deck["slides"]]
        self.assertNotIn("g_site", kinds)
        self.assertEqual(kinds[0], "g_cover")
        self.assertEqual(kinds[-1], "g_end")
        self.assertNotRegex(deck["caption"], r"(?<!\d)0건")


BRIEF_1116 = WEB_DIR / "data" / "briefs" / "brief_web_2026_11_16.json"
SLOTS_1116 = WEB_DIR / "data" / "linkedin_anatomy" / "2026-11-16.v3.json"


class Weekly1116Test(unittest.TestCase):
    """[2026-11-16] 첫 주간 슬롯 — 분모 없는 서한(원문 한 줄 표지)·반복 위반 없는 서한·다른 주제(무균공정).
    s05.comment 에 숫자를 쓰지 않아 라이브 지적사항 DB 가 주마다 바뀌어도 검증이 흔들리지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(BRIEF_1116.read_text(encoding="utf-8"))
        cls.findings = lg.load_findings()
        cls.card = la.pick_anatomy_card(cls.doc)

    def _slots(self) -> dict:
        return json.loads(SLOTS_1116.read_text(encoding="utf-8"))

    def _p(self, mutate) -> list[str]:
        s = self._slots()
        mutate(s)
        return lg.validate_v3(self.card, s, self.findings)

    def test_rule_picks_the_slot_card_dates_are_not_numbers(self):
        # 날짜(년·월·일)를 수치로 세던 때는 기록 제출 거부 서한(7e7545ebb190, 지적 2건)이 뽑혔다.
        self.assertEqual(self.card["id"], self._slots()["card_id"])
        qiancan = next(c for c in self.doc["cards"] if c["id"] == "7e7545ebb190")
        self.assertEqual(la.anatomy_score(qiancan)[1], 0)

    def test_slot_passes(self):
        self.assertEqual(lg.validate_v3(self.card, self._slots(), self.findings), [])

    def test_deck_quote_cover_and_topic(self):
        deck = lg.build_grm_deck(self.doc, self._slots(), self.findings)
        self.assertEqual(len(deck["slides"]), 7)
        cover = deck["slides"][0]
        self.assertTrue(cover["quote"]["en"] and not cover["dots"])
        html_ = lg.render_html(deck, font_links=False)
        self.assertIn('class="g-quote"', html_)
        self.assertNotIn("212 → 211", html_)          # 11-02 PET 문구가 다른 서한에 붙지 않는다
        self.assertIn("‘무균보증/무균공정’", deck["caption"])
        self.assertNotIn("USV", html_ + deck["caption"])
        self.assertTrue(deck["caption"].startswith(self._slots()["caption"]["lead"].split(".")[0]))

    def test_cover_needs_exactly_one_of_dots_or_quote(self):
        p = self._p(lambda s: s["cover"].update(dots={"total": 3, "marked": 3, "evidence": ["x"], "caption": ""}))
        self.assertTrue(any("정확히 하나" in x for x in p), p)
        p = self._p(lambda s: s["cover"]["quote"].update(en="Airflow was observed to be fine."))
        self.assertTrue(any("cover.quote.en" in x for x in p), p)

    def test_repeat_is_optional(self):
        self.assertEqual(self._p(lambda s: s["s03"].pop("repeat")), [])

    def test_topic_required_for_new_weeks(self):
        p = self._p(lambda s: s["s05"].pop("topic"))
        self.assertTrue(any("s05.topic" in x for x in p), p)

    def test_hook_rules(self):
        lead = lambda t: (lambda s: s["caption"].update(lead=t))  # noqa: E731
        p = self._p(lead("FDA는 한 무균 주사제 제조소에 경고서한을 보냈습니다. 기류에 난류가 있었습니다."))
        self.assertTrue(any("사실로 시작" in x for x in p), p)
        p = self._p(lead("기류 시각화 영상에서 심한 난류가 관찰됐는데도 회사가 기류는 적절하다고 결론을 내린 사건이 있었습니다."))
        self.assertTrue(any("모바일 접힘" in x for x in p), p)
        p = self._p(lead("기류 영상에 심한 난류가 찍혔는데도 회사는 적절하다고 봤습니다."))
        self.assertTrue(any("숫자나 원문 인용" in x for x in p), p)
        p = self._p(lambda s: s["cover"].update(headline=["무균공정 설계 결함·", "육안검사 미흡"]))
        self.assertTrue(any("라벨로 끝난다" in x for x in p), p)

    def test_candidates_cli_material(self):
        got = lg.candidates(self.doc, SLOTS_1116)
        self.assertEqual(got["pick"], self.card["id"])
        self.assertTrue(got["material"]["key_violations"][0]["original"])
        self.assertEqual([r["id"] for r in got["ranked"]][0], got["pick"])


class PickFactTest(unittest.TestCase):
    def test_does_not_split_inside_a_sentence_on_da(self):
        t = "귀사는 멸균 의약품 및 자재를 ISO-5 품질보다 낮은 공기에 노출시켰다. 구체적으로, 작업자의 팔이 막았다."
        self.assertEqual(lg._pick_fact(t), "멸균 의약품 및 자재를 ISO-5 품질보다 낮은 공기에 노출시켰다.")

    def test_cut_drops_unclosed_parenthesis(self):
        t = "ISO 5 구역 내 일방향 기류(unidirectional airflow)를 입증하기 위한 동적 조건(dynamic conditions)에서의 적절한 스모크 스터디(smoke study)를 수행하지 않았다."
        out = lg._pick_fact(t)
        self.assertTrue(out.endswith("…"), out)
        self.assertNotIn("(smoke", out)


class DistTest(unittest.TestCase):
    def test_dist_writes_under_briefs_pub(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rc = lg.main(["--brief", str(BRIEF), "--dist", td, "--no-pdf", "--findings", str(FINDINGS_SNAPSHOT)])
            self.assertEqual(rc, 0)
            self.assertTrue((Path(td) / "briefs" / "2026-11-02" / "linkedin_grm.txt").exists())

    def test_missing_slots_writes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rc = lg.main(["--brief", str(BRIEF), "--slots", str(Path(td) / "none.json"), "--dist", td, "--no-pdf"])
            self.assertEqual(rc, 0)
            self.assertFalse((Path(td) / "briefs").exists())

    def test_requires_out_or_dist(self):
        with self.assertRaises(SystemExit):
            lg.main(["--brief", str(BRIEF)])


if __name__ == "__main__":
    unittest.main()
