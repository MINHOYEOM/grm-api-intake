"""[사람 PDF 확인 2026-11-25] pdf_verified — 원본 PDF 로 쓴 국문은 숫자·코드·로트 대조만 면제한다.

실측: fda483-195280(손글씨)은 운영 영문이 "pmfelop set arpepd M the 1007 …" 수준이라 국문을 원본 PDF 로
써야 하는데, 그러면 OCR 영문의 깨진 숫자·코드와 맞지 않아 strict 가 영원히 실격시킨다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import findings_detail_qa as qa  # noqa: E402

EN = ("pmfelop set arpepd M the 1007, fitted tntentdon 3 Shatle Mluden Ow Nok odepynlily Prerad xo de "
      "Uisucd infp2titon OYtin She Freez ef titirve Ysur Vaticlelion booch")
KO = "귀사는 공정 중 시험을 문서화하지 않았다. 직원은 절차를 따르지 않았다."
REC = {"by": "품질 검토자", "at": "2026-11-26", "source": "https://www.fda.gov/media/195280/download", "pages": "1"}


class PdfVerifiedTest(unittest.TestCase):
    def test_without_record_number_check_still_fails(self):
        """표시가 없는 행은 종전대로 — 숫자 대조 실격."""
        probs = qa.check(EN, KO, strict=True)
        self.assertTrue(any(p.startswith("numbers_missing") for p in probs), probs)

    def test_record_exempts_only_number_code_lot(self):
        probs = qa.check(EN, KO, strict=True, pdf_verified=REC)
        self.assertFalse([p for p in probs if p.startswith(qa.PDF_VERIFIED_EXEMPT)], probs)
        # 읽기 좋음·한자·가림 개수 검사는 그대로
        bad = qa.check(EN, KO + " 試驗 (b) (4)", strict=True, pdf_verified=REC)
        self.assertIn("hanja", bad)
        self.assertTrue(any(p.startswith("redact_variant") for p in bad), bad)
        en_r = EN + " [redacted] [redacted]"
        self.assertTrue(any(p.startswith("redacted_count") for p in
                            qa.check(en_r, KO, strict=True, pdf_verified=REC)))

    def test_incomplete_record_fails(self):
        for key, val in (("by", ""), ("at", "11/26/2026"), ("source", "https://example.com/x.pdf"), ("pages", "")):
            rec = dict(REC, **{key: val})
            probs = qa.check(EN, KO, strict=True, pdf_verified=rec)
            self.assertIn("pdf_verified_invalid:" + key, probs)
        self.assertIn("pdf_verified_invalid:not_a_dict", qa.check(EN, KO, strict=True, pdf_verified=True))

    def test_check_items_reads_record_from_batch_item(self):
        item = {"finding_id": "f1", "detail_text": EN, "detail_text_ko": KO}
        self.assertIn("f1", qa.check_items([item], strict=True))
        self.assertEqual(qa.check_items([dict(item, pdf_verified=REC)], strict=True), {})

    def test_human_review_list_is_well_formed(self):
        """사람 확인 목록(docs/specs/483_pdf_review_queue.json)의 항목은 문서·원본 주소·사유를 갖는다."""
        import json
        items = json.loads((ROOT / "docs" / "specs" / "483_pdf_review_queue.json").read_text(encoding="utf-8"))
        self.assertTrue(items)
        for it in items:
            self.assertTrue(it["finding_id"].startswith("finding-"), it)
            self.assertRegex(it["source"], qa._PDF_SOURCE_RE)
            self.assertTrue(it["reason"].strip())
            # todo = 아직 초안 없음 · review = 초안 있음, 사람이 PDF 와 대조 대기 · done = 반영 끝
            self.assertIn(it["status"], ("todo", "review", "done"))
            if it["status"] == "review":
                # 초안은 사람 확인 전이라 pdf_verified 를 달지 않는다. 가림은 [redacted] 로 — (b)(4) 꼴은 strict 실격.
                self.assertNotIn("pdf_verified", it)
                self.assertTrue(it["draft_ko"].strip() and it["draft_basis"].strip())
                self.assertEqual(qa.readability(it["draft_ko"]), [])


if __name__ == "__main__":
    unittest.main()
