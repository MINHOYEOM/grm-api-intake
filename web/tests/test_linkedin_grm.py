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
                                 "관련 용어", "서한에 없는 것", "전체 해설", "GRM 주간 브리프"][:len(heads)] if "■ 관련 용어" in cap else
                         ["지적 내용", "회사 회신과 FDA 판단", "FDA 요구", "GRM 지적사항 DB의 비슷한 사례", "서한에 없는 것", "전체 해설",
                          "GRM 주간 브리프"])
        # [구독 유입 2026-11-17 대책 4] 맨 끝 단락은 구독 — 그 언어 홈의 구독 칸으로, 같은 표식.
        tail = cap.split("■ GRM 주간 브리프\n", 1)[1].splitlines()
        self.assertEqual(tail[0], "매주 FDA·EU·식약처 실사 지적 소식을 받아 보세요.")
        self.assertRegex(tail[1], r"^https://grm-solutions\.com/\?utm_source=linkedin&utm_medium=social&utm_campaign=\S+_weekly#subscribe$")
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


class English1116Test(unittest.TestCase):
    """[2026-11-16] 영문 해부판 — 근거는 FDA 영문 원문 verbatim(위반 original + 서한 전문 letter_en.txt),
    그림·본문 한글 0자, 본문 배치는 국문과 같고 주소는 /en/ 경로."""

    @classmethod
    def setUpClass(cls):
        import linkedin_grm_en as lge
        cls.lge = lge
        cls.doc = json.loads(BRIEF_1116.read_text(encoding="utf-8"))
        cls.findings = lg.load_findings()
        cls.card = la.pick_anatomy_card(cls.doc)
        cls.letter = lge.load_letter("2026-11-16")

    def _slots(self) -> dict:
        return json.loads(SLOTS_1116.read_text(encoding="utf-8"))

    def _p(self, mutate) -> list[str]:
        s = self._slots()
        mutate(s)
        return self.lge.validate_en(self.card, s, self.findings, self.letter)

    def test_slot_passes(self):
        self.assertEqual(self._p(lambda s: None), [])

    def test_letter_has_no_signature_block(self):
        self.assertTrue(self.letter)
        self.assertNotIn("Sincerely", self.letter)  # 서명·참조인 개인 이름은 저장소에 두지 않는다

    def test_deck_has_no_hangul_and_uses_en_paths(self):
        import re
        deck = self.lge.build_en_deck(self.doc, self._slots(), self.findings, self.letter)
        html_ = lg.render_html(deck, font_links=False)
        self.assertEqual(len(deck["slides"]), 7)
        self.assertIsNone(re.search(r"[가-힣]", html_ + deck["caption"]))
        self.assertIn('<html lang="en">', html_)
        cap = deck["caption"]
        self.assertFalse(cap.startswith("■"))
        heads = [ln[2:] for ln in cap.splitlines() if ln.startswith("■ ")]
        self.assertEqual(heads[0], "What FDA cited")
        self.assertEqual(heads[-2:], ["Full analysis", "GRM Weekly Brief"])
        self.assertIn("https://grm-solutions.com/en/?utm_source=linkedin", cap)
        self.assertIn("#subscribe", cap)
        for ln in cap.splitlines():
            if "grm-solutions.com" in ln:
                self.assertRegex(ln, r"^https://grm-solutions\.com/en/\S+utm_source=linkedin\S*$")
        self.assertNotIn("USV", html_ + cap)
        self.assertNotIn("Private Limited", html_ + cap)

    def test_evidence_must_be_verbatim_english(self):
        p = self._p(lambda s: s["en"]["s03"]["pairs"][0].update(evidence=["The firm hired a consultant."]))
        self.assertTrue(any("not verbatim" in x for x in p), p)
        p = self._p(lambda s: s["en"]["cover"]["quote"].update(text="Airflow was fine."))
        self.assertTrue(any("en.cover.quote" in x for x in p), p)

    def test_hangul_firm_name_and_ai_tells_blocked(self):
        p = self._p(lambda s: s["en"]["s05"].update(comment="같은 조항이 인용됐다."))
        self.assertTrue(any("한글" in x for x in p), p)
        p = self._p(lambda s: s["en"]["caption"].update(similar="USV Private Limited was cited before."))
        self.assertTrue(any("firm name" in x for x in p), p)
        p = self._p(lambda s: s["en"]["caption"].update(similar="This case underscores the importance of airflow."))
        self.assertTrue(any("AI-sounding" in x for x in p), p)

    def test_hook_rules_en(self):
        lead = lambda t: (lambda s: s["en"]["caption"].update(lead=t))  # noqa: E731
        p = self._p(lead("FDA issued a warning letter to a sterile injectable maker. Airflow was poor."))
        self.assertTrue(any("start with the fact" in x for x in p), p)
        p = self._p(lead("Airflow videos showed heavy turbulence. The firm disagreed."))
        self.assertTrue(any("number or a quoted" in x for x in p), p)
        p = self._p(lambda s: s["en"]["cover"].update(headline=["Aseptic design flaws", "and inspection deficiencies"]))
        self.assertTrue(any("ends with a label" in x for x in p), p)

    def test_missing_letter_blocks(self):
        p = self.lge.validate_en(self.card, self._slots(), self.findings, "")
        self.assertTrue(any("letter_en.txt" in x for x in p), p)


class MultiSlotTest(unittest.TestCase):
    """[2026-11-16 대표 결정] 수요일=1위(a1)·금요일=2위(a2). 기록 요청형(704(a)(4)) 서한은 회신 평가 원문이 없어 해부하지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(BRIEF_1116.read_text(encoding="utf-8"))

    def test_records_review_letters_are_not_anatomized(self):
        ranked = [c["id"] for c in la.ranked_anatomy_cards(self.doc)]
        self.assertEqual(ranked, ["bfbb158a522f"])  # 2~4위(공급업체 관리·기록 제출 거부·기록 접근 거부)는 모두 704(a)(4)
        got = lg.candidates(self.doc)
        self.assertEqual([p["rank"] for p in got["picks"]], [1])
        self.assertEqual({s["id"] for s in got["records_review_skipped"]}, {"0f65bd3c5c70", "7e7545ebb190", "00ae76e635bb"})

    def test_legacy_single_slot_is_a1_and_sidecars_follow_slot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "2099-01-05.v3.json").write_text("{}", encoding="utf-8")
            (d / "2099-01-05.a2.v3.json").write_text("{}", encoding="utf-8")
            got = lg.slot_paths("2099-01-05", d)
            self.assertEqual([(k, p.name) for k, p in got], [(1, "2099-01-05.v3.json"), (2, "2099-01-05.a2.v3.json")])
            self.assertEqual(lg.sidecar(got[0][1], "similar.json").name, "2099-01-05.similar.json")
            self.assertEqual(lg.sidecar(got[1][1], "letter_en.txt").name, "2099-01-05.a2.letter_en.txt")
            (d / "2099-01-05.a1.v3.json").write_text("{}", encoding="utf-8")
            self.assertEqual(lg.slot_paths("2099-01-05", d)[0][1].name, "2099-01-05.a1.v3.json")

    def test_rank_must_match_rule(self):
        s = json.loads(SLOTS_1116.read_text(encoding="utf-8"))
        self.assertEqual(lg.rank_problems(self.doc, s, 1), [])
        self.assertTrue(lg.rank_problems(self.doc, s, 2))
        s["card_id"] = "0f65bd3c5c70"
        self.assertTrue(lg.rank_problems(self.doc, s, 1))

    def test_a1_outputs_and_legacy_copies(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rc = lg.main(["--brief", str(BRIEF_1116), "--out", td, "--no-pdf"])
            self.assertEqual(rc, 0)
            names = {p.name for p in Path(td).iterdir()}
            for n in ("linkedin_grm_a1.txt", "linkedin_grm_a1_en.txt", "linkedin_grm.txt", "linkedin_grm_en.txt"):
                self.assertIn(n, names)
            self.assertNotIn("linkedin_grm_a2.txt", names)
            self.assertEqual((Path(td) / "linkedin_grm_a1_en.txt").read_text(encoding="utf-8"),
                             (Path(td) / "linkedin_grm_en.txt").read_text(encoding="utf-8"))


class CaptionLimitTest(unittest.TestCase):
    """[2026-11-16] 링크드인 게시물 한도 3,000자 — 영문 3,699자로 게시 버튼이 막혔다. 상한 2,900(주소는 utm 포함 전체)."""

    def test_1116_captions_fit_both_languages(self):
        import linkedin_grm_en as lge
        doc = json.loads(BRIEF_1116.read_text(encoding="utf-8"))
        s = json.loads(SLOTS_1116.read_text(encoding="utf-8"))
        f = lg.load_findings()
        ko = lg.build_grm_deck(doc, s, f)["caption"]
        en = lge.build_en_deck(doc, s, f, lge.load_letter("2026-11-16"))["caption"]
        self.assertLessEqual(len(ko), lg.CAPTION_MAX)
        self.assertLessEqual(len(en), lg.CAPTION_MAX)
        self.assertIn("Firm A", en)  # 사례를 통째로 버리지 않고 뒤에서부터 뺀다

    def test_order_drop_tail_cases_then_shorten_quotes(self):
        site = [dict(alias=a, fact="x" * 150) for a in "ABCD"]
        make = lambda rows: "".join(r["alias"] + r["fact"] for r in rows)  # noqa: E731
        self.assertEqual(lg.fit_caption(make, site, limit=1000), make(site))           # 들어가면 그대로
        self.assertEqual(lg.fit_caption(make, site, limit=460), make(site[:3]))       # ① 뒤에서부터 뺀다
        got = lg.fit_caption(make, site, limit=200)                                    # ② 2건 + 인용 80자
        self.assertTrue(got.startswith("A") and "B" in got and "C" not in got)
        self.assertLessEqual(len(got), 200)
        with self.assertRaises(la.AnatomyError):
            lg.fit_caption(lambda rows: "y" * 300, site, limit=200)                   # 슬롯 문장이 길면 거부

    def test_short_fact_cuts_on_word_boundary(self):
        src = "Your firm failed to perform adequate smoke studies under dynamic conditions to demonstrate unidirectional airflow"
        out = lg._short_fact(src)
        self.assertTrue(out.endswith("…") and len(out) <= lg.SHORT_FACT + 1)
        self.assertIn(out[:-1].split()[-1], src.split())  # 마지막 낱말이 잘리지 않았다


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
