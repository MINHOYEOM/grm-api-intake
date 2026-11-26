"""해외 기준 조항 국문 참고 번역(web/clause_translation.py · clause_translate_kit.py) — 검사 규칙·outbox 게이트·덧씌우기.

outbox 게이트: translations/clause_outbox/*.json 의 모든 항목은 지금 원문과 해시가 같으면 검사를 통과해야 한다.
(원문이 바뀌어 해시가 다른 항목은 빌드가 버리므로 여기서도 실패로 보지 않는다 — 다시 큐로 간다.)
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))

import clause_text_page as ct  # noqa: E402
import clause_translation as ctr  # noqa: E402

EN = ("(a) There shall be a quality control unit that shall approve or reject all components and drug product "
      "closures. Records shall be kept as specified in §§ 211.180 and 211.182.\n\n"
      "(b) Potable water shall meet the standards set forth in 40 CFR part 141 and HEPA filters shall be validated.")
KO = ("(a) 품질관리부서를 두어야 하며, 품질관리부서는 모든 원료와 의약품 마개를 승인하거나 거부하여야 한다. "
      "기록은 §§ 211.180 및 211.182 에 규정된 대로 보관하여야 한다.\n\n"
      "(b) 음용수는 40 CFR part 141 에 규정된 기준에 맞아야 하며, HEPA 필터는 밸리데이션하여야 한다.")


def kinds(probs):
    return {p.split(":", 1)[0] for p in probs}


class CheckTest(unittest.TestCase):
    def test_good_translation_passes(self):
        self.assertEqual(ctr.check(EN, KO, "Responsibilities.", "책임"), [])

    def test_paragraph_dropped_or_merged(self):
        self.assertIn("para_count", kinds(ctr.check(EN, KO.split("\n\n")[0])))
        self.assertIn("para_count", kinds(ctr.check(EN, KO.replace("\n\n", " "))))

    def test_number_citation_marker_code(self):
        self.assertIn("numbers_missing", kinds(ctr.check(EN, KO.replace("211.182", "211"))))
        self.assertIn("citation_missing", kinds(ctr.check(EN, KO.replace("§§ 211.180", "제211.180조"))))
        self.assertIn("citation_missing", kinds(ctr.check(EN, KO.replace("40 CFR part 141", "40 CFR 141"))))
        self.assertIn("marker", kinds(ctr.check(EN, KO.replace("(b) ", ""))))
        self.assertIn("codes_missing", kinds(ctr.check(EN, KO.replace("HEPA", "헤파"))))

    def test_glossary_terms(self):
        probs = ctr.check(EN, KO.replace("품질관리부서", "품질 담당 조직"))
        self.assertTrue(any(p.startswith("term:quality control unit") for p in probs), probs)
        self.assertIn("term", kinds(ctr.check(EN, KO.replace("밸리데이션하여야", "검증하여야"))))

    def test_untranslated_hanja_ratio_title(self):
        bad = KO.replace("기록은", "Records shall be kept as specified in the rule and")
        self.assertIn("untranslated", kinds(ctr.check(EN, bad)))
        self.assertIn("hanja", kinds(ctr.check(EN, KO + " 試驗")))
        self.assertEqual(ctr.check(EN, EN), ["no_hangul"])
        short = "(a) 품질관리부서 원료 마개 §§ 211.180 211.182.\n\n(b) 40 CFR part 141 HEPA 밸리데이션."
        self.assertIn("ratio", kinds(ctr.check(EN, short)))
        self.assertIn("title_missing", kinds(ctr.check(EN, KO, "Responsibilities.", "")))

    def test_quoted_english_and_long_paragraph_floor(self):
        en = "Rodenticides shall be used in accordance with the Federal Insecticide, Fungicide, and Rodenticide Act (7 U.S.C. 135)."
        ko = "살서제는 연방 살충제·살균제·살서제법(Federal Insecticide, Fungicide, and Rodenticide Act)(7 U.S.C. 135)에 맞게 사용하여야 한다."
        self.assertEqual(ctr.check(en, ko), [])
        self.assertIn("citation_missing", kinds(ctr.check(en, ko.replace("7 U.S.C. 135", "미국법전 제7편 135"))))


class OutboxGateTest(unittest.TestCase):
    """CI 게이트 — 새 배치 파일이 검사를 하나라도 어기면 빨갛다."""

    @classmethod
    def setUpClass(cls):
        cls.orig = ctr.foreign_clauses(ct.load_sources(ct.DATA_DIR))
        cls.files = sorted(ctr.OUTBOX_DIR.glob("*.json"))

    def test_outbox_exists_with_pilot(self):
        self.assertTrue(self.files, "translations/clause_outbox 가 비었다")

    def test_every_current_item_passes(self):
        bad = {}
        for f in self.files:
            items = json.loads(f.read_text(encoding="utf-8"))
            self.assertIsInstance(items, list, f.name)
            ids = [it.get("id") for it in items]
            self.assertEqual(len(ids), len(set(ids)), f"{f.name}: 같은 조항이 두 번")
            for it in items:
                self.assertIn(it.get("id"), self.orig, f"{f.name}: 번역 대상이 아닌 id {it.get('id')}")
                c = self.orig[it["id"]]
                if it.get("sha") != c["sha"]:
                    continue  # 원문이 바뀐 낡은 번역 — 빌드가 버리고 다시 큐로 간다
                p = ctr.check(c["text"], it.get("text_ko") or "", c.get("title") or "", it.get("title_ko") or "")
                if p:
                    bad[f"{f.name}:{it['id']}"] = p
        self.assertEqual(bad, {}, "조항 번역 검사 실격 — docs/specs/clause_translation_brief.md")


class OverlayTest(unittest.TestCase):
    def test_build_carries_translation_and_google_reason_follows(self):
        over = ctr.load_outbox()
        view = ct.build(ct.load_sources())
        n_tr = 0
        for d, c in ct.pages(view):
            if d["source"]["lang"] == "ko":
                continue
            it = over.get(f"{d['source_key']}/{d['key']}/{c['slug']}")
            reason = ct.google_noindex_reason(d, c, "ko")
            if c["text_ko"]:
                n_tr += 1
                self.assertEqual(c["ko_paras"], ctr.paragraphs(c["text_ko"]))
                self.assertEqual(reason, "" if len(c["text_ko"]) >= ct.GOOGLE_MIN_CHARS else "short")
            else:
                self.assertFalse(it and it.get("sha") == c.get("sha"), f"통과 번역이 실리지 않았다: {c['code']}")
                self.assertEqual(reason, "untranslated")
        self.assertGreaterEqual(n_tr, 1)

    def test_stale_sha_and_korean_source_are_not_applied(self):
        src = ct.load_sources(ct.DATA_DIR)
        cfr = next(o for o in src if o["source"]["key"] == "cfr21")
        mfds = next(o for o in src if o["source"]["key"] == "mfds_gmp")
        c0, m0 = cfr["clauses"][0], mfds["clauses"][0]
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Z.json").write_text(json.dumps([
                {"id": c0["id"], "sha": "0000", "text_ko": "낡은 번역"},
                {"id": m0["id"], "sha": m0["sha"], "text_ko": "국문 원천은 대상 아님"},
            ], ensure_ascii=False), encoding="utf-8")
            srcs = ct.load_sources(ct.DATA_DIR, translations_dir=Path(tmp))
        self.assertNotIn("text_ko", next(o for o in srcs if o["source"]["key"] == "cfr21")["clauses"][0])
        self.assertNotIn("text_ko", next(o for o in srcs if o["source"]["key"] == "mfds_gmp")["clauses"][0])

    def test_later_file_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "A.json").write_text(json.dumps([{"id": "x", "sha": "1", "text_ko": "앞"}]), encoding="utf-8")
            Path(tmp, "B.json").write_text(json.dumps([{"id": "x", "sha": "1", "text_ko": "뒤"}]), encoding="utf-8")
            self.assertEqual(ctr.load_outbox(Path(tmp))["x"]["text_ko"], "뒤")


class KitTest(unittest.TestCase):
    def test_export_skips_done_and_keeps_full_text(self):
        import clause_translate_kit as kit
        st = kit.state()
        self.assertEqual(set(st), set(kit.originals()))
        with tempfile.TemporaryDirectory() as tmp:
            paths = kit.export(Path(tmp), 50, source="cfr21", limit=60)
            rows = [r for p in paths for r in json.loads(p.read_text(encoding="utf-8"))]
        self.assertEqual(len(paths), 2)
        orig = kit.originals()
        for r in rows:
            self.assertNotEqual(st[r["id"]], "done")
            self.assertEqual(r["text"], orig[r["id"]]["text"])   # 원문 무절단


if __name__ == "__main__":
    unittest.main()
