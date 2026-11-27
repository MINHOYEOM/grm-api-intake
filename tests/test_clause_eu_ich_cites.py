"""EU GMP·ICH 조항 인용 매처 + 구글 noindex 판정 연동 가드 (2026-11-27).

정밀도 우선: 문서 이름과 조항 번호가 함께 적힌 표기만 잇고, 애매하면 잇지 않는다.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))

import clause_text_page as ctp  # noqa: E402

POSITIVE = [
    ("per EU GMP Chapter 5, 5.21 was not met", [("eu_gmp", "ch5", "5.21")]),
    ("EU GMP Part I, Chapter 4 paragraph 4.1 requires", [("eu_gmp", "ch4", "4.1")]),
    ("EudraLex Vol. 4 Part I Chapter 1: 1.4 (vi)", [("eu_gmp", "ch1", "1.4")]),
    ("not compliant with Annex 1 of the EU GMP, 4.20", [("eu_gmp", "annex1", "4.20")]),
    ("Annex 1 §8.123 states", [("eu_gmp", "annex1", "8.123")]),
    ("ICH Q7 section 6.5 requires", [("ich", "q7", "6.5")]),
    ("ICH Q10 paragraph 3.2.1 is vague", []),  # 세 단계 번호는 잇지 않는다
]
# 오연결 0 — 실제 지적에서 뽑은 문서 단위 표기·번호가 우연히 붙은 표기
NEGATIVE = [
    "Annex 1 of the EU GMP does not comply",
    "fail to meet the limits set out in Annex 1 of the EU GMP.",
    "e.g. Annex1 gap assessment and the contamination control strategy",
    "FDA considers the expectations outlined in ICH Q7 when determining",
    "ICH Q10 Pharmaceutical Quality System at https://www.fda.gov/media/71553/download",
    "EU GMP Chapter 5 3.2",          # 장과 번호가 어긋남 + 구분자 없음
    "EU GMP Chapter 5, 3.2",         # 장 5 인데 번호는 3.x
    "Annex 10 4.2",                  # Annex 1 이 아님
    "Annex 1 5.2 mg",                # 구분자 없는 숫자
    "ICH Q7A 5.1",
    "ICH Q9 4.1",                    # Q9 는 일부러 제외(R1 개정으로 번호가 갈린다)
    "Part II EU GMP guidelines 3.1",
    "",
]


class MatcherTest(unittest.TestCase):
    def test_positive(self):
        for text, want in POSITIVE:
            self.assertEqual(ctp.eu_ich_clause_refs(text), want, text)

    def test_negative_no_mislink(self):
        for text in NEGATIVE:
            self.assertEqual(ctp.eu_ich_clause_refs(text), [], text)


class CitingWiringTest(unittest.TestCase):
    def setUp(self):
        self.view = ctp.build(ctp.load_sources())

    def _docs(self, text, n=1):
        return [{"document_id": f"d{i}", "slug": f"d{i}", "agency": "X", "published_date": "2026-01-01",
                 "findings": [{"finding_id": f"f{i}", "text_orig": text, "text_ko": text}]}
                for i in range(n)]

    def test_cited_clause_gets_block_and_leaves_bare(self):
        text = "The site did not comply with EU GMP Chapter 5, 5.21 on cross-contamination."
        out = ctp.citing_findings(self._docs(text), self.view, lambda f: f["text_orig"])
        c = ctp._find(self.view, "eu_gmp", "ch5", "5.21")
        self.assertIn(c["href"], out)
        self.assertEqual(out[c["href"]]["documents"], 1)
        # 같은 판정 함수: 인용이 있으면 bare 가 아니다
        d = self.view["index"][("eu_gmp", "ch5")]
        n = out[c["href"]]["documents"]
        self.assertTrue(ctp.has_grm_addition(n, None, ""))
        self.assertNotEqual(ctp.google_noindex_reason(d, c, "en", n, bare=not ctp.has_grm_addition(n, None, "")), "bare")
        self.assertEqual(ctp.google_noindex_reason(d, c, "en", 0, bare=not ctp.has_grm_addition(0, None, "")) in ("bare", "short"), True)

    def test_nonexistent_clause_not_linked(self):
        out = ctp.citing_findings(self._docs("EU GMP Chapter 5, 5.999 x"), self.view, lambda f: f["text_orig"])
        self.assertEqual(out, {})

    def test_document_level_mention_not_linked(self):
        out = ctp.citing_findings(self._docs("Annex 1 of the EU GMP was not implemented"), self.view,
                                  lambda f: f["text_orig"])
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()


class KeyTermsAndTopicTest(unittest.TestCase):
    """[조항 덧붙임 2026-11-27] 핵심 용어(3개 미만이면 비움) · 같은 주제 지적(고정 표만) · 같은 판정 함수."""

    @classmethod
    def setUpClass(cls):
        import json
        cls.view = ctp.build(ctp.load_sources())
        gp = pathlib.Path(__file__).resolve().parent.parent / "web" / "data" / "glossary.json"
        cls.gloss = json.loads(gp.read_text(encoding="utf-8"))

    def test_min_terms_rule(self):
        out = ctp.key_terms_by_href(self.view, self.gloss, "en")
        self.assertTrue(out)
        for href, rows in out.items():
            self.assertGreaterEqual(len(rows), ctp.KEY_TERMS_MIN, href)
            self.assertLessEqual(len(rows), ctp.KEY_TERMS_MAX, href)
            for r in rows:
                self.assertTrue(r["gist"], (href, r["id"]))

    def test_common_terms_not_counted(self):
        out = ctp.key_terms_by_href(self.view, self.gloss, "en")
        ids = {r["id"] for rows in out.values() for r in rows}
        self.assertNotIn("quality", {i.lower() for i in ids})

    def test_term_ids_filter(self):
        out = ctp.key_terms_by_href(self.view, self.gloss, "en", term_ids={"nonexistent"})
        self.assertEqual(out, {})

    def test_topic_table_is_fixed_and_unmapped_is_empty(self):
        self.assertEqual(ctp.topic_labels("eu_gmp", "ch8", "8.5"), ["불만/회수"])
        self.assertEqual(ctp.topic_labels("eu_gmp", "annex1", "9.3"), ["환경모니터링"])
        self.assertEqual(ctp.topic_labels("eu_gmp", "annex1", "1"), [])        # 범위 — 비움
        self.assertEqual(ctp.topic_labels("eu_gmp", "ch9", "9.1"), [])         # 표에 없음
        self.assertEqual(ctp.topic_labels("ich", "q9", "4.1"), [])
        self.assertEqual(ctp.topic_labels("mfds_gmp", "rule", "제4조"), [])

    def test_topic_cases_deterministic_distinct_firms(self):
        docs = [{"document_id": f"d{i}", "slug": f"d{i}", "agency": "X", "firm_name": f"F{i}",
                 "published_date": "2026-01-0%d" % (i % 9 + 1),
                 "findings": [{"finding_id": f"f{i}", "category_label_ko": "불만/회수", "text_orig": f"Complaint {i}."}]}
                for i in range(10)]
        a = ctp.topic_cases_by_href(docs, self.view, lambda f: f["text_orig"])
        b = ctp.topic_cases_by_href(docs, self.view, lambda f: f["text_orig"])
        self.assertEqual(a, b)
        href = ctp._find(self.view, "eu_gmp", "ch8", "8.5")["href"]
        self.assertEqual(len(a[href]), ctp.TOPIC_CASES_N)
        self.assertEqual(len({r["firm_name"] for r in a[href]}), ctp.TOPIC_CASES_N)
        self.assertNotIn(ctp._find(self.view, "eu_gmp", "ch9", "9.1")["href"], a)

    def test_topic_cases_need_n(self):
        docs = [{"document_id": "d", "slug": "d", "firm_name": "F", "findings": [
            {"finding_id": "f", "category_label_ko": "불만/회수", "text_orig": "x"}]}]
        self.assertEqual(ctp.topic_cases_by_href(docs, self.view, lambda f: f["text_orig"]), {})

    def test_blocks_lift_bare_via_same_function(self):
        self.assertTrue(ctp.has_grm_addition(0, None, "", [{"id": "a"}], None))
        self.assertTrue(ctp.has_grm_addition(0, None, "", None, [{"b": 1}]))
        self.assertFalse(ctp.has_grm_addition(0, None, "", None, None))
        self.assertFalse(ctp.has_grm_addition(0, None, ""))                    # 종전 호출 불변
        d = self.view["index"][("eu_gmp", "ch8")]
        c = ctp._find(self.view, "eu_gmp", "ch8", "8.5")
        long_c = dict(c, chars=999)
        self.assertEqual(ctp.google_noindex_reason(d, long_c, "en", 0, bare=True), "bare")
        self.assertEqual(ctp.google_noindex_reason(
            d, long_c, "en", 0, bare=not ctp.has_grm_addition(0, None, "", None, [1])), "")
