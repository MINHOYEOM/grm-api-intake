"""조항 원문 수집기(clause_text_collect) — 파서 순수 함수 + 커밋된 정적 데이터 불변식.

PDF·네트워크 없이 돈다(파서는 줄/문단 목록만 받는다)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import clause_text_collect as m  # noqa: E402

DATA = ROOT / "web" / "data" / "clauses"


def P(text: str, small: bool = False):
    return (text, m.Line(0, 0, 0, 0, 11, text), small)


class EcfrParse(unittest.TestCase):
    XML = """<DIV5 N="211" TYPE="PART"><HEAD>PART 211</HEAD>
<DIV6 N="J" TYPE="SUBPART"><HEAD>Subpart J&#x2014;Records and Reports</HEAD>
<DIV8 N="211.192" TYPE="SECTION"><HEAD>&#xA7; 211.192 Production record review.</HEAD>
<P>All drug product <E T="03">production</E> records shall be reviewed.</P>
<P>(a) Second paragraph.</P>
<CITA>[43 FR 45077, Sept. 29, 1978]</CITA></DIV8>
<DIV8 N="211.193" TYPE="SECTION"><HEAD>&#xA7; 211.193 [Reserved]</HEAD></DIV8>
</DIV6></DIV5>"""

    def test_section_text_title_path_notes(self):
        secs = m.parse_ecfr_part(self.XML, "211")
        s = secs[0]
        self.assertEqual(s["number"], "211.192")
        self.assertEqual(s["title"], "Production record review.")
        self.assertEqual(s["text"], "All drug product production records shall be reviewed.\n\n"
                                    "(a) Second paragraph.")
        self.assertEqual(s["path"], ["21 CFR Part 211", "Subpart J—Records and Reports"])
        self.assertEqual(s["notes"], ["[43 FR 45077, Sept. 29, 1978]"])
        self.assertTrue(secs[1]["reserved"])


class SplitClauses(unittest.TestCase):
    def test_eu_chapter_principle_heading_and_list_numbers(self):
        paras = [P("Principle"), P("The holder must comply."),
                 P("Quality Management"), P("1.1 Quality Management is a concept."),
                 P("3. Reviewing records is not a clause."), P("1.2 GMP applies."),
                 P("1 Footnote text", small=True)]
        r = m.split_clauses(paras, chapter=1, principle=True, top_mode="none")
        nums = [c["number"] for c in r["clauses"]]
        self.assertEqual(nums, ["Principle", "1.1", "1.2"])
        c11 = r["clauses"][1]
        self.assertEqual(c11["path"], ["Quality Management"])
        # 본문 속 번호 목록은 앞 조항 본문에 남는다(버리지 않는다).
        self.assertIn("3. Reviewing records", c11["text"])
        self.assertEqual(r["clauses"][2]["notes"], ["1 Footnote text"])

    def test_q7_decimal_order_and_upper_headings(self):
        paras = [P("1. INTRODUCTION"), P("1.1 Objective"), P("This is the objective."),
                 P("2."), P("QUALITY MANAGEMENT"), P("2.1 Principles"),
                 P("2.10 Quality should be everyone's responsibility."),
                 P("2.22 The quality unit should not delegate."),
                 P("2.3 Responsibility for Production Activities"),
                 P("1. Preparing instructions is a list item."),
                 P("3. PERSONNEL Personnel text follows here.")]
        r = m.split_clauses(paras, top_mode="upper", decimal=True)
        nums = [c["number"] for c in r["clauses"]]
        self.assertEqual(nums, ["1", "1.1", "2", "2.1", "2.10", "2.22", "2.3", "3"])
        by = {c["number"]: c for c in r["clauses"]}
        self.assertEqual(by["2"]["title"], "QUALITY MANAGEMENT")
        self.assertEqual(by["3"]["title"], "PERSONNEL")
        self.assertEqual(by["3"]["text"], "Personnel text follows here.")
        self.assertIn("1. Preparing instructions", by["2.3"]["text"])

    def test_appendix_and_no_space_number(self):
        paras = [P("1 Scope"), P("Scope text."), P("2 Principle"), P("2.1 First."),
                 P("2.2APS should not be used."), P("Glossary"), P("Airlock – a space.")]
        r = m.split_clauses(paras, top_mode="nodot")
        nums = [c["number"] for c in r["clauses"]]
        self.assertEqual(nums, ["1", "2", "2.1", "2.2", "Glossary"])
        self.assertEqual(r["clauses"][3]["text"], "APS should not be used.")
        self.assertEqual(r["clauses"][4]["text"], "Airlock – a space.")

    def test_body_start_skips_table_of_contents(self):
        paras = [P("Cover"), P("1. INTRODUCTION"), P("2. SCOPE"), P("1. INTRODUCTION"), P("Body.")]
        self.assertEqual(m.find_body_start(paras, "INTRODUCTION"), 3)


class MfdsParse(unittest.TestCase):
    def test_articles_strip_label_and_keep_paragraphs(self):
        html = ('<p>[시행 2024. 12. 30.] [식품의약품안전처고시 제2024-87호, 2024. 12. 30., 일부개정]</p>'
                '<div class="pgroup"><p class="x"><label for="a">제3조(유효기간 등)</label> ① 첫째 항.</p>'
                '<p>② 둘째 &amp; 항.</p></div>')
        r = m.parse_mfds_articles(html)
        self.assertEqual(r["meta"]["effective"], "2024. 12. 30.")
        a = r["articles"][0]
        self.assertEqual((a["number"], a["title"]), ("제3조", "유효기간 등"))
        self.assertEqual(a["text"], "① 첫째 항.\n\n② 둘째 & 항.")

    def test_byl_numbering_items_and_hangul_wrap(self):
        L = lambda t: m.Line(0, 0, 0, 0, 11, t)  # noqa: E731
        lines = [L("[별표 9]"), L("컴퓨터화 시스템"), L("1. 원칙"), L("가. 이 별표는 모든 시스"),
                 L("템을 대상으로 한다."), L("2. 일반사항"), L("2.1 위험관리"), L("위험관리는 적용되어야 한다."),
                 L("1. 이것은 목록이다"), L("3.2 건너뛴 번호는 받지 않는다")]
        r = m.split_kor_byl(lines)
        self.assertEqual([c["number"] for c in r["clauses"]], ["1", "2", "2.1"])
        self.assertEqual(r["clauses"][0]["text"], "가. 이 별표는 모든 시스템을 대상으로 한다.")
        self.assertEqual(r["clauses"][2]["path"], ["2. 일반사항"])
        self.assertIn("1. 이것은 목록이다", r["clauses"][2]["text"])
        self.assertEqual(r["front_matter"], "[별표 9] 컴퓨터화 시스템")


class CommittedData(unittest.TestCase):
    """커밋된 web/data/clauses/*.json 불변식 — 화면이 기대는 계약."""

    @classmethod
    def setUpClass(cls):
        cls.files = {}
        for key in ("cfr21", "eu_gmp", "ich", "mfds_gmp"):
            p = DATA / f"{key}.json"
            if p.is_file():
                cls.files[key] = json.loads(p.read_text(encoding="utf-8"))

    def test_schema_ids_and_hash(self):
        self.assertEqual(set(self.files), {"cfr21", "eu_gmp", "ich", "mfds_gmp"})
        seen = set()
        for key, obj in self.files.items():
            self.assertEqual(obj["schema"], m.SCHEMA)
            src = obj["source"]
            for f in ("title", "publisher", "url", "license", "license_url", "retrieved"):
                self.assertTrue(src.get(f), (key, f))
            docs = {d["key"] for d in obj["docs"]}
            for c in obj["clauses"]:
                self.assertNotIn(c["id"], seen)
                seen.add(c["id"])
                self.assertIn(c["doc"], docs)
                self.assertEqual(c["id"], f"{key}/{c['doc']}/{c['slug']}")
                self.assertEqual(c["chars"], len(c["text"]))
                self.assertEqual(c["sha"], m._sha(c["text"]))
                self.assertTrue(c["url"].startswith("https://"))

    def test_first_batch_coverage(self):
        """첫 묶음 범위가 통째로 빠지면 실패 — 파서가 조용히 0 건을 내는 회귀 방지."""
        def nums(key, doc):
            return {c["number"] for c in self.files[key]["clauses"] if c["doc"] == doc}
        self.assertTrue({"211.22", "211.100", "211.192", "211.194"} <= nums("cfr21", "211"))
        self.assertTrue({"210.1", "210.3"} <= nums("cfr21", "210"))
        for ch, last in ((1, 13), (2, 23), (3, 44), (4, 32), (5, 71), (6, 41), (7, 17), (8, 31), (9, 3)):
            got = nums("eu_gmp", f"ch{ch}")
            self.assertTrue({f"{ch}.{i}" for i in range(1, last + 1)} <= got, ch)
            self.assertIn("Principle", got)
        self.assertTrue({"2.1", "4.23", "8.123", "9.35", "Glossary"} <= nums("eu_gmp", "annex1"))
        self.assertTrue({"2.10", "2.3", "12.30", "20"} <= nums("ich", "q7"))
        self.assertTrue({"4.1", "4.6", "Annex I"} <= nums("ich", "q9"))
        self.assertTrue({"1.5.1", "3.2.2", "Annex 1"} <= nums("ich", "q10"))
        # 식약처 규정: 조문 제1조~제5조 + 별표 1~18(2의2 포함) 전부.
        self.assertTrue({"제1조", "제2조의2", "제4조", "제5조"} <= nums("mfds_gmp", "rule"))
        docs = {d["key"] for d in self.files["mfds_gmp"]["docs"]}
        self.assertEqual(docs, {"rule", "byl2-2"} | {f"byl{i}" for i in range(1, 19)})
        self.assertTrue({"2.1", "2.3", "4.13"} <= nums("mfds_gmp", "byl9"))
        self.assertTrue({"1.1", "5.8"} <= nums("mfds_gmp", "byl17"))

    def test_translation_queue_matches_text(self):
        q = json.loads((DATA / "translation_queue.json").read_text(encoding="utf-8"))
        # 국문 원문(식약처)은 번역 대기열에 넣지 않는다.
        texts = {c["id"]: c for obj in self.files.values() if obj["source"].get("lang") != "ko"
                 for c in obj["clauses"] if c["text"]}
        self.assertEqual({r["id"] for r in q["items"]}, set(texts))
        for r in q["items"]:
            self.assertEqual(r["sha"], texts[r["id"]]["sha"])


if __name__ == "__main__":
    unittest.main()
