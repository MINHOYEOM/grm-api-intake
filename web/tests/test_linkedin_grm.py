#!/usr/bin/env python3
"""링크드인 사건 해부 GRM 판(v3 시범) 테스트 — 순수 층만(Chrome·네트워크 0).

CI 는 `tests/test_web_linkedin_grm.py` shim 으로 순회. 직접: python web/tests/test_linkedin_grm.py
"""
from __future__ import annotations

import json
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
FINDINGS = json.loads(lg.FINDINGS.read_text(encoding="utf-8"))


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
        paras = self.deck["caption"].strip().split("\n\n")
        self.assertEqual(len(paras), 7)
        self.assertTrue(paras[1].startswith("지적은"))
        self.assertTrue(paras[4].startswith("우리 지적사항 DB"))
        self.assertTrue(paras[5].startswith("서한에 없는 것"))
        self.assertTrue(paras[6].startswith("전체 해설"))
        self.assertNotIn("#", "\n".join(paras[:6]))

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
        self.assertEqual(len(calls), 8)  # 지적 4개 × (상위 5 + 경고서한용 50)
        self.assertEqual({it["q"] for it in sim["items"]}, {1, 2, 3, 4})
        self.assertEqual(sim["rules"]["4"][0], "fact")

    def test_failure_or_empty_gives_none(self):
        def boom(*a):
            raise OSError("down")
        self.assertIsNone(lg.fetch_similar(_slots(), url="u", key="k", rpc=boom))
        self.assertIsNone(lg.fetch_similar(_slots(), url="u", key="k", rpc=lambda *a: []))

    def test_deck_without_similar_drops_only_that_card(self):
        deck = lg.build_grm_deck(_doc(), _slots(), FINDINGS, similar={})
        kinds = [s["kind"] for s in deck["slides"]]
        self.assertNotIn("g_site", kinds)
        self.assertEqual(kinds[0], "g_cover")
        self.assertEqual(kinds[-1], "g_end")
        self.assertNotRegex(deck["caption"], r"(?<!\d)0건")


if __name__ == "__main__":
    unittest.main()
