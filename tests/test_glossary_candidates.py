"""용어사전 후보 추출(`glossary_candidates.py`) — 순수 함수·결정론.

후보만 낸다(정의는 세션이 공식 출처로 쓴다). 여기서 고정하는 것: 브리프가 스스로 병기한
짝을 잡는다 · 표제어 앞에 딸려 온 문장 조각을 떼어 낸다 · 이미 사전에 있는 말(동의어·
괄호 속 약어 포함)은 뺀다 · 기관·법인·식별번호는 후보가 아니다 · 순위가 결정론이다.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import glossary_candidates as gc  # noqa: E402


def _brief(pub: str, texts: list[str]) -> dict:
    return {"brief": {"publish_date": pub},
            "cards": [{"id": f"c{i}", "title_issue": f"카드{i}", "summary": t, "sources": [{"url": "x (IGNORED)"}]}
                      for i, t in enumerate(texts)]}


class TrimAndCleanTest(unittest.TestCase):
    def test_trim_drops_leading_sentence_fragments(self):
        self.assertEqual(gc.trim_ko("귀사의 품질부서"), "품질부서")
        self.assertEqual(gc.trim_ko("를 준수하고 확인"), "확인")
        self.assertEqual(gc.trim_ko("일까지 위탁생산시설"), "위탁생산시설")
        self.assertEqual(gc.trim_ko("층류 후드"), "층류 후드")
        self.assertEqual(gc.trim_ko("용출 규격 부적합"), "용출 규격 부적합")

    def test_trim_keeps_at_most_three_words(self):
        self.assertEqual(gc.trim_ko("가 나 다 라"), "나 다 라")

    def test_clean_en_drops_identifier_tail(self):
        self.assertEqual(gc.clean_en("Outsourcing Facility, FEI 3011430551"), "Outsourcing Facility")
        self.assertEqual(gc.clean_en("media fill."), "media fill")


class ExtractPairsTest(unittest.TestCase):
    def test_korean_with_english_gloss(self):
        pairs = gc.extract_pairs("무균공정에서 배지충진(media fill) 결과와 층류 후드(LAFH) 점검")
        self.assertIn(("배지충진", "media fill"), pairs)
        self.assertIn(("층류 후드", "LAFH"), pairs)

    def test_english_phrase_with_acronym(self):
        pairs = gc.extract_pairs("See the Summary of Product Characteristics (SmPC) section.")
        self.assertIn(("", "Summary of Product Characteristics (SmPC)"), pairs)

    def test_agencies_corporations_and_numbers_are_not_terms(self):
        text = "미국 식품의약국(FDA)과 제조사(Acme Pharma Ltd.) 그리고 번호(1234567)"
        self.assertEqual(gc.extract_pairs(text), [])


class CandidatesTest(unittest.TestCase):
    GLOSSARY = [
        {"id": "capa", "term_ko": "시정 및 예방조치", "term_en": "Corrective and Preventive Action (CAPA)",
         "aliases": ["시정·예방조치"]},
    ]

    def test_existing_terms_are_excluded_by_name_alias_or_acronym(self):
        briefs = [_brief("2026-09-21", ["시정·예방조치(CAPA) 미흡", "예방조치(CAPA) 누락", "배지충진(media fill) 실패"])]
        rows = gc.candidates(briefs, self.GLOSSARY)
        self.assertEqual([r["term_ko"] for r in rows], ["배지충진"])

    def test_ranking_cards_then_weeks_then_latest(self):
        briefs = [
            _brief("2026-09-14", ["배지충진(media fill) A", "개입(intervention) B"]),
            _brief("2026-09-21", ["배지충진(media fill) C", "층류 후드(LAFH) D"]),
            _brief("2026-09-28", ["층류 후드(LAFH) E"]),
        ]
        rows = gc.candidates(briefs, self.GLOSSARY)
        names = [r["term_ko"] for r in rows]
        # 배지충진·층류 후드 둘 다 카드 2·주 2 → 최신 등장(09-28)이 먼저
        self.assertEqual(names[:2], ["층류 후드", "배지충진"])
        self.assertEqual(names[2], "개입")
        self.assertEqual(rows[0]["cards"], 2)
        self.assertEqual(rows[0]["weeks"], 2)

    def test_one_count_per_card_and_sources_ignored(self):
        briefs = [_brief("2026-09-28", ["배지충진(media fill) 그리고 또 배지충진(media fill)"])]
        rows = gc.candidates(briefs, self.GLOSSARY)
        self.assertEqual(rows[0]["cards"], 1)
        self.assertNotIn("IGNORED", json.dumps(rows, ensure_ascii=False))

    def test_cli_reads_latest_n_briefs(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_gcand_"))
        (tmp / "briefs").mkdir()
        for pub, text in (("2026_09_14", "개입(intervention)"), ("2026_09_21", "배지충진(media fill)")):
            (tmp / "briefs" / f"brief_web_{pub}.json").write_text(
                json.dumps(_brief(pub.replace("_", "-"), [text]), ensure_ascii=False), encoding="utf-8")
        (tmp / "glossary.json").write_text(json.dumps(self.GLOSSARY, ensure_ascii=False), encoding="utf-8")
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            rc = gc.main(["--briefs", str(tmp / "briefs"), "--glossary", str(tmp / "glossary.json"),
                          "--weeks", "1", "--json"])
        self.assertEqual(rc, 0)
        rows = json.loads(out.getvalue())
        self.assertEqual([r["term_ko"] for r in rows], ["배지충진"])


if __name__ == "__main__":
    unittest.main()
