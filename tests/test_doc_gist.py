"""국문 짧은 문서 'GRM 요지'(web/doc_gist.py · doc_gist_kit.py) — 검사 규칙·outbox 게이트·화면 뷰.

outbox 게이트: translations/doc_gist_outbox/*.json 의 항목은 지금 문서와 지적 본문 해시가 같으면 검사를 통과해야 한다.
(문서가 갱신돼 해시가 달라진 요지는 빌드가 싣지 않으므로 실패로 보지 않는다 — 다시 큐로 간다.)
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

import doc_gist as dg  # noqa: E402

DOC = {
    "document_id": "fda483-1", "source": "FDA 483", "agency": "FDA", "firm_name": "Acme Labs",
    "published_date": "2026-11-13", "inspection_date": "2026-06-10", "categories": ["설비/시설"],
    "findings": [
        {"finding_id": "f1", "text_ko": "설비와 기구가 오염을 방지하기 위한 적절한 주기로 세척·유지보수되지 않고 있다.",
         "category_label_ko": "설비/시설"},
        {"finding_id": "f2", "text_ko": "건물이 양호한 보수 상태로 유지되고 있지 않다.", "category_label_ko": "설비/시설"},
        {"finding_id": "f3", "text_ko": "21 CFR 211.113(b) 무균 표방 의약품의 미생물 오염 방지 절차가 없다.",
         "category_label_ko": "무균보증/무균공정", "cfr_refs": ["21 CFR 211.113"]},
    ],
}
GOOD = {"document_id": "fda483-1", "src_sha": dg.src_sha(DOC), "basis": [1, 2, 3],
        "gist_ko": "2026년 6월 실사의 FDA 483 지적 3건이다. 설비 관련이 2건으로 세척·유지보수 주기와 건물 보수 상태를 "
                   "지적했고, 나머지 1건은 무균 표방 의약품의 미생물 오염 방지 절차(21 CFR 211.113)가 없다는 점이다."}


def kinds(probs):
    return {p.split(":", 1)[0] for p in probs}


class CheckTest(unittest.TestCase):
    def test_good_gist_passes(self):
        self.assertEqual(dg.check(DOC, GOOD), [])

    def test_invented_number_name_judgement(self):
        self.assertIn("numbers_not_in_doc", kinds(dg.check(DOC, {**GOOD, "gist_ko": GOOD["gist_ko"].replace("211.113", "211.192")})))
        self.assertIn("numbers_not_in_doc", kinds(dg.check(DOC, {**GOOD, "gist_ko": GOOD["gist_ko"] .replace("2026년", "2019년")})))
        self.assertIn("latin_not_in_doc", kinds(dg.check(DOC, {**GOOD, "gist_ko": GOOD["gist_ko"].replace("FDA 483", "FDA 483 (Pfizer)")})))
        self.assertIn("judgement", kinds(dg.check(DOC, {**GOOD, "gist_ko": GOOD["gist_ko"].replace("지적했고", "심각하게 지적했고")})))
        self.assertIn("judgement", kinds(dg.check(DOC, {**GOOD, "gist_ko": GOOD["gist_ko"] + " 절차를 보완해야 한다."})))

    def test_count_length_sentences_basis_copy(self):
        self.assertIn("count_missing", kinds(dg.check(DOC, {**GOOD, "gist_ko": GOOD["gist_ko"].replace("지적 3건", "지적 여러 건")})))
        self.assertIn("length", kinds(dg.check(DOC, {**GOOD, "gist_ko": "FDA 483 지적 3건이다."})))
        many = "지적 3건이다. 설비가 문제였다. 건물도 문제였다. 무균 절차도 없었다."
        self.assertIn("sentences", kinds(dg.check(DOC, {**GOOD, "gist_ko": many + " 2026년 6월 실사다."})))
        self.assertIn("basis_invalid", kinds(dg.check(DOC, {**GOOD, "basis": [0, 4]})))
        self.assertIn("basis_missing", kinds(dg.check(DOC, {**GOOD, "basis": []})))
        copy = "FDA 483 지적 3건이다. " + DOC["findings"][0]["text_ko"]
        self.assertIn("copies_finding", kinds(dg.check(DOC, {**GOOD, "gist_ko": copy})))

    def test_words_in_source_are_allowed(self):
        doc = {**DOC, "findings": DOC["findings"] + [{"finding_id": "f4", "text_ko": "중대한 일탈을 조사하지 않았다."}]}
        g = {**GOOD, "src_sha": dg.src_sha(doc), "gist_ko": GOOD["gist_ko"].replace("지적 3건", "지적 4건") + " 중대한 일탈 조사도 빠졌다."}
        self.assertEqual(dg.check(doc, g), [])

    def test_view_requires_matching_sha_and_passing_check(self):
        self.assertIsNotNone(dg.view(DOC, {"fda483-1": GOOD}))
        self.assertIsNone(dg.view(DOC, {"fda483-1": {**GOOD, "src_sha": "x"}}))
        self.assertIsNone(dg.view(DOC, {"fda483-1": {**GOOD, "basis": []}}))
        v = dg.view(DOC, {"fda483-1": GOOD})
        self.assertEqual(v["note"], dg.NOTE.format(n=3))


class OutboxGateTest(unittest.TestCase):
    """CI 게이트 — 새 요지 배치가 검사를 하나라도 어기면 빨갛다."""

    def test_every_current_item_passes(self):
        files = sorted(dg.OUTBOX_DIR.glob("*.json"))
        self.assertTrue(files, "translations/doc_gist_outbox 가 비었다")
        docs = {d["document_id"]: d for d in json.loads(
            (ROOT / "web" / "data" / "findings_docs.json").read_text(encoding="utf-8"))["documents"]}
        bad, n_live = {}, 0
        for f in files:
            items = json.loads(f.read_text(encoding="utf-8"))
            ids = [it.get("document_id") for it in items]
            self.assertEqual(len(ids), len(set(ids)), f"{f.name}: 같은 문서가 두 번")
            for it in items:
                d = docs.get(it.get("document_id"))
                if d is None or it.get("src_sha") != dg.src_sha(d):
                    continue  # 문서가 갱신·제외됨 — 빌드가 싣지 않고 다시 큐로 간다
                n_live += 1
                p = dg.check(d, it)
                if p:
                    bad[f"{f.name}:{it['document_id']}"] = p
        self.assertEqual(bad, {}, "GRM 요지 검사 실격 — docs/specs/doc_gist_brief.md")
        self.assertGreater(n_live, 0)

    def test_later_file_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "A.json").write_text(json.dumps([{"document_id": "d", "gist_ko": "앞"}]), encoding="utf-8")
            Path(tmp, "B.json").write_text(json.dumps([{"document_id": "d", "gist_ko": "뒤"}]), encoding="utf-8")
            self.assertEqual(dg.load_outbox(Path(tmp))["d"]["gist_ko"], "뒤")


class KitTest(unittest.TestCase):
    def test_targets_follow_render_predicates_and_export_is_unclipped(self):
        try:
            import doc_gist_kit as kit
            import render  # noqa: F401
        except ImportError as e:  # jinja2 없는 로컬
            self.skipTest(str(e))
        docs = kit.load_documents()
        tg = kit.targets(docs)
        ratios = render.doc_duplicate_ratios(docs, "ko")
        for d in tg:
            self.assertFalse(render.google_noindex_doc_page(d, "ko", ratios))
            self.assertLess(sum(len(render.finding_body(f, "ko")) for f in d["findings"]), kit.SHORT_BODY_CHARS)
        self.assertGreater(len(tg), 0)
        row = kit.export_row(tg[0])
        self.assertEqual([f["text_ko"] for f in row["findings"]], [f.get("text_ko") or "" for f in tg[0]["findings"]])


if __name__ == "__main__":
    unittest.main()
