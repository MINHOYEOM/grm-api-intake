"""EMA 원문 전문(기사 HTML·문서 PDF) — 공통 판독기·저장 게이트·빈칸 감시 (11-25 #8).

계기: EMA 발췌 빈칸 106행(최근 60일 46 + 60일 밖 60)은 원문이 PDF 문서였다. 수집기와 백필이 둘 다
HTML `<p>` 만 읽었다. 대표 결정: 전문은 전부 저장하고, 저장 단계에서는 자르지 않는다.

이 시험이 막는 것
  ① EMA PDF 행이 빈 발췌로 저장되는 것(수집기·백필 모두) — 빈 채로 남으면 감시가 센다
  ② 저장 전문이 원문 추출 길이보다 짧은 것(절단·분할 손실)
  ③ 수집기와 백필이 서로 다른 규칙을 쓰는 것(같은 함수를 불러야 한다)
"""
import json
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_blank_excerpts as b  # noqa: E402
import collect_intake as ci  # noqa: E402
import content_completeness as cc  # noqa: E402
import ema_document as ed  # noqa: E402
from collect_mfds_gmp_inspection import _extract_pdf_text  # noqa: E402

DOC_URL = "https://www.ema.europa.eu/en/documents/scientific-guideline/guideline-process-validation_en.pdf"
SYS_URL = "https://www.ema.europa.eu/system/files/documents/other/concept-paper_en.pdf"
NEWS_URL = "https://www.ema.europa.eu/en/news/chmp-highlights"


def make_pdf(pages: int = 3, lines_per_page: int = 40) -> bytes:
    import fitz
    doc = fitz.open()
    for n in range(pages):
        page = doc.new_page()
        text = "\n".join(f"Page {n + 1} line {i}: validation of the manufacturing process."
                         for i in range(lines_per_page))
        page.insert_text((40, 40), text, fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def blank_pdf() -> bytes:
    import fitz
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def pdf_resp(data: bytes, code: int = 200):
    return SimpleNamespace(status_code=code, content=data, text="",
                           headers={"Content-Type": "application/pdf"})


def html_resp(html: str, code: int = 200):
    return SimpleNamespace(status_code=code, content=html.encode(), text=html,
                           headers={"Content-Type": "text/html"})


class DocumentUrlTest(unittest.TestCase):
    def test_document_links(self):
        self.assertTrue(ed.is_document_url(DOC_URL))
        self.assertTrue(ed.is_document_url(SYS_URL))
        self.assertTrue(ed.is_document_url("https://ema.europa.eu/en/documents/other/x_en.pdf"))
        self.assertFalse(ed.is_document_url(NEWS_URL))
        self.assertFalse(ed.is_document_url("https://example.org/en/documents/x.pdf"))
        self.assertFalse(ed.is_document_url(""))


class PdfFulltextTest(unittest.TestCase):
    def setUp(self):
        self.data = make_pdf()
        self.engine_text, _ = _extract_pdf_text(self.data, max_chars=None)

    def _read(self, resp):
        with patch.object(ed.requests, "get", return_value=resp) as get:
            doc = ed.read_ema_fulltext(DOC_URL, delay=False)
        return doc, get

    def test_pdf_is_read_in_full_with_pages(self):
        doc, _ = self._read(pdf_resp(self.data))
        self.assertEqual(doc.status, ed.STATUS_PDF_OK)
        self.assertEqual(doc.format, "pdf")
        self.assertEqual(doc.pages, 3)
        self.assertIn("Page 3 line 39", doc.text)          # 마지막 쪽 끝까지
        self.assertGreater(len(doc.text), ci.ECA_ARTICLE_EXCERPT_MAX_CHARS)
        self.assertEqual(doc.text, self.engine_text)        # 엔진 추출 그대로(상한 없음)

    def test_pdf_detected_by_magic_even_without_content_type(self):
        resp = SimpleNamespace(status_code=200, content=self.data, text="", headers={})
        doc, _ = self._read(resp)
        self.assertEqual(doc.format, "pdf")

    def test_failures_carry_reason(self):
        doc, _ = self._read(pdf_resp(b"", 404))
        self.assertFalse(doc.ok)
        self.assertEqual(doc.status, "http-404")
        with patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "false"}):
            doc, _ = self._read(pdf_resp(blank_pdf()))
        self.assertFalse(doc.ok)
        self.assertTrue(doc.status.startswith("scan-no-text"), doc.status)

    def test_html_news_is_read_without_cap(self):
        long = "word " * 1000
        doc, _ = self._read(html_resp(f"<nav><p>menu</p></nav><p>{long}</p>"))
        self.assertEqual(doc.status, ed.STATUS_HTML_OK)
        self.assertEqual(doc.text, long.strip())

    def test_429_retry_after_then_reads(self):
        first = SimpleNamespace(status_code=429, headers={"Retry-After": "7"}, content=b"", text="")
        sleep = Mock()
        with patch.object(ed.requests, "get", side_effect=[first, pdf_resp(self.data)]):
            doc = ed.read_ema_fulltext(DOC_URL, sleeper=sleep)
        self.assertTrue(doc.ok)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],
                         [ed.BACKFILL_ARTICLE_DELAY_SECONDS, 7.0])
        self.assertEqual(ed.BACKFILL_ARTICLE_DELAY_SECONDS, 2.0)


def _zip(files: dict) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


class OfficeFulltextTest(unittest.TestCase):
    """빈칸 106행 중 일부는 .docx·.xlsx 문서다 — 같은 원칙(전문)으로 읽는다."""

    def _read(self, data):
        resp = SimpleNamespace(status_code=200, content=data, text="", headers={})
        with patch.object(ed.requests, "get", return_value=resp):
            return ed.read_ema_fulltext(DOC_URL.replace(".pdf", ".docx"), delay=False)

    def test_docx(self):
        body = "".join(f"<w:p><w:r><w:t>Paragraph {i}</w:t></w:r><w:r><w:t> tail</w:t></w:r></w:p>" for i in range(300))
        data = _zip({"word/document.xml": f"<w:document {W_NS}><w:body>{body}</w:body></w:document>"})
        doc = self._read(data)
        self.assertEqual((doc.format, doc.status), ("docx", "docx-ok"))
        self.assertTrue(doc.text.endswith("Paragraph 299 tail"))
        self.assertEqual(doc.text.count("\n"), 299)

    def test_xlsx_all_rows_and_shared_strings(self):
        rows = "".join(f'<row><c t="s"><v>0</v></c><c><v>{i}</v></c></row>' for i in range(500))
        data = _zip({
            "xl/workbook.xml": f"<workbook {S_NS}/>",
            "xl/sharedStrings.xml": f"<sst {S_NS}><si><t>Product</t></si></sst>",
            "xl/worksheets/sheet1.xml": f"<worksheet {S_NS}><sheetData>{rows}</sheetData></worksheet>",
        })
        doc = self._read(data)
        self.assertEqual((doc.format, doc.status), ("xlsx", "xlsx-ok"))
        self.assertEqual(doc.text.splitlines()[-1], "Product\t499")
        self.assertEqual(len(doc.text.splitlines()), 500)


class StorageGateTest(unittest.TestCase):
    """② 저장 전문 ≥ 원문 추출 길이. 한도를 넘으면 나눠 담고, 이어 붙이면 원문과 같다."""

    def test_patch_keeps_full_text(self):
        text = "가" * 5000
        doc = ed.EmaDocument(DOC_URL, text, ed.STATUS_PDF_OK, "pdf", 4, len(text))
        patch_ = ed.raw_patch(doc)
        self.assertEqual(patch_[ed.TEXT_KEY], text)
        self.assertNotIn(ed.PARTS_KEY, patch_)
        self.assertEqual(patch_[ed.META_KEY]["chars"], 5000)
        self.assertEqual(patch_[ed.META_KEY]["pages"], 4)

    def test_over_limit_is_split_not_cut(self):
        text = "abcdefghij" * 25 + "END"
        doc = ed.EmaDocument(DOC_URL, text, ed.STATUS_PDF_OK, "pdf", 1, len(text))
        patch_ = ed.raw_patch(doc, part_chars=100)
        self.assertEqual(len(patch_[ed.TEXT_KEY]), 100)
        self.assertEqual(len(patch_[ed.PARTS_KEY]), 2)
        self.assertEqual(patch_[ed.META_KEY]["parts"], 3)
        self.assertEqual(ed.fulltext(patch_), text)
        self.assertTrue(ed.fulltext(patch_).endswith("END"))

    def test_stored_shorter_than_extracted_is_refused(self):
        doc = ed.EmaDocument(DOC_URL, "short", ed.STATUS_PDF_OK, "pdf", 1, extracted_chars=50)
        with self.assertRaises(ValueError):
            ed.raw_patch(doc)

    def test_failure_patch_has_reason_and_no_body_key(self):
        doc = ed.EmaDocument(DOC_URL, "", "http-404")
        patch_ = ed.raw_patch(doc)
        self.assertNotIn(ed.TEXT_KEY, patch_)
        self.assertEqual(patch_[ed.META_KEY]["status"], "http-404")
        self.assertEqual(ed.blank_document_gap(DOC_URL, patch_), "http-404")
        self.assertEqual(ed.blank_document_gap(DOC_URL, {}), "unmarked")
        self.assertEqual(ed.blank_document_gap(NEWS_URL, {}), "")

    def test_existing_body_is_not_overwritten(self):
        raw = {ed.TEXT_KEY: "existing"}
        doc = ed.EmaDocument(DOC_URL, "new", ed.STATUS_PDF_OK, "pdf", 1, 3)
        self.assertEqual(ed.apply_to_raw(raw, doc), {})
        self.assertEqual(raw, {ed.TEXT_KEY: "existing"})


def _ema_feed(url: str) -> str:
    return ('<rss version="2.0"><channel><item>'
            "<title>Guideline on good manufacturing practice process validation</title>"
            f"<link>{url}</link><pubDate>Wed, 16 Sep 2026 01:00:00 +0000</pubDate>"
            "<description></description><guid>g-1</guid></item></channel></rss>")


def _xml_for(url: str):
    served = {"n": 0}

    def _fn(*a, **k):
        served["n"] += 1
        return ET.fromstring(_ema_feed(url) if served["n"] == 1
                             else '<rss version="2.0"><channel></channel></rss>')
    return _fn


class CollectorGateTest(unittest.TestCase):
    """① 일일 수집기 — EMA PDF 문서가 전문으로 저장된다. 못 읽으면 사유가 남는다."""

    def _collect(self, resp):
        with patch.dict(os.environ, {"ENABLE_EMA_ARTICLE_EXCERPT": "true"}), \
                patch.object(ci, "http_get_xml", _xml_for(DOC_URL)), \
                patch.object(ci.requests, "get", return_value=resp), \
                patch.object(ci.time, "sleep"):
            items, err = ci.collect_ema_rss(date(2026, 9, 14), date(2026, 9, 21))
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        return items[0].raw_payload

    def test_pdf_document_is_stored_in_full(self):
        data = make_pdf(pages=5)
        extracted, _ = _extract_pdf_text(data, max_chars=None)
        raw = self._collect(pdf_resp(data))
        stored = ed.fulltext(raw)
        self.assertTrue(stored, "EMA PDF 행이 빈 발췌로 저장됐다")
        self.assertGreaterEqual(len(stored), len(extracted), "저장 전문이 원문 추출보다 짧다")
        self.assertEqual(raw[ed.META_KEY]["pages"], 5)
        self.assertEqual(ed.blank_document_gap(DOC_URL, raw), "")

    def test_unreadable_pdf_leaves_reason_for_the_monitor(self):
        raw = self._collect(pdf_resp(b"", 404))
        self.assertNotIn(ed.TEXT_KEY, raw)
        self.assertEqual(ed.blank_document_gap(DOC_URL, raw), "http-404")

    def test_pending_document_is_read_but_pending_news_is_not(self):
        """빈칸 106행은 전부 미판정(Pending)이었다 — 문서는 읽고, 미판정 뉴스는 종전대로 건너뛴다."""
        title = "Annual report on the European Medicines Agency budget"
        self.assertEqual(ci.compute_relevance(title, "", ""), "Pending")
        for url, expect_calls in ((DOC_URL, 1), (NEWS_URL, 0)):
            feed = ('<rss version="2.0"><channel><item>'
                    f"<title>{title}</title><link>{url}</link>"
                    "<pubDate>Wed, 16 Sep 2026 01:00:00 +0000</pubDate><guid>g</guid></item></channel></rss>")
            served = {"n": 0}

            def _xml(*a, **k):
                served["n"] += 1
                return ET.fromstring(feed if served["n"] == 1 else '<rss version="2.0"><channel></channel></rss>')
            with patch.dict(os.environ, {"ENABLE_EMA_ARTICLE_EXCERPT": "true"}), \
                    patch.object(ci, "http_get_xml", _xml), \
                    patch.object(ci.requests, "get", return_value=pdf_resp(make_pdf(1))) as get, \
                    patch.object(ci.time, "sleep"):
                items, _ = ci.collect_ema_rss(date(2026, 9, 14), date(2026, 9, 21))
            self.assertEqual(items[0].qa_relevance, "Pending")
            self.assertEqual(get.call_count, expect_calls, url)

    def test_collector_calls_the_shared_reader(self):
        doc = ed.EmaDocument(DOC_URL, "body", ed.STATUS_PDF_OK, "pdf", 1, 4)
        with patch.object(ci.ema_document, "read_ema_fulltext", return_value=doc) as reader:
            raw = self._collect(pdf_resp(b""))
        reader.assert_called_once_with(DOC_URL)
        self.assertEqual(raw[ed.TEXT_KEY], "body")


def _row(raw, *, url=DOC_URL, **metadata):
    return {"source": b.SOURCE_EMA, "document_id": "ema-doc", "official_url": url,
            "raw_json": json.dumps(raw), "row_json": json.dumps(metadata)}


class BackfillGateTest(unittest.TestCase):
    """① 백필 — 같은 함수로 PDF 전문을 채우고, 저장 값 = 원문 추출."""

    def setUp(self):
        self.data = make_pdf(pages=4)
        self.extracted, _ = _extract_pdf_text(self.data, max_chars=None)

    def _readers(self):
        def ema(url):
            return ed.read_ema_fulltext(url, delay=False)
        return b.Readers(Mock(), Mock(return_value=""), Mock(), ema)

    def test_pdf_row_is_filled_with_full_text(self):
        write = Mock()
        old_meta = {ed.META_KEY: {"status": "http-429"}, "keep": 1}
        with patch.object(ed.requests, "get", return_value=pdf_resp(self.data)):
            results = b.backfill([_row(old_meta, qa_relevance="Possible")], self._readers(),
                                 apply=True, write=write)
        (r,) = results
        self.assertEqual(r["status"], "applied")
        self.assertTrue(r["complete"])
        self.assertEqual((r["format"], r["pages"], r["chars"]), ("pdf", 4, len(self.extracted)))
        saved = write.call_args.args[1]
        self.assertEqual(ed.fulltext(saved), self.extracted)
        self.assertEqual(saved["keep"], 1)
        self.assertEqual(saved[ed.META_KEY]["status"], ed.STATUS_PDF_OK)   # 옛 실패 메타 교체
        self.assertIn(ed.TEXT_KEY, write.call_args.args[2])

    def test_unreadable_pdf_is_reported_not_written(self):
        write = Mock()
        with patch.object(ed.requests, "get", return_value=pdf_resp(b"", 403)):
            (r,) = b.backfill([_row({})], self._readers(), apply=True, write=write)
        self.assertEqual(r["status"], "http-403")
        self.assertTrue(r["document_link"])
        write.assert_not_called()

    def test_backfill_main_wires_the_shared_reader(self):
        self.assertIs(b._extract_full_article_excerpt, ed.extract_html_text)
        with patch.object(b.ema_document, "read_ema_fulltext", return_value="doc") as reader:
            self.assertEqual(b._read_ema(DOC_URL), "doc")
        reader.assert_called_once_with(DOC_URL)


class MonitorGateTest(unittest.TestCase):
    """① 매일 감시(content_completeness) — EMA 문서 행 빈 발췌를 사유별로 세고 이슈를 연다."""

    def _rows(self):
        return [
            {"source": b.SOURCE_EMA, "document_id": "blank-doc", "official_url": DOC_URL,
             "raw_json": "{}", "row_json": json.dumps({"qa_relevance": "Likely"})},
            {"source": b.SOURCE_EMA, "document_id": "failed-doc", "official_url": "",
             "raw_json": json.dumps({"link": SYS_URL, ed.META_KEY: {"status": "http-404"}}),
             "row_json": "{}"},
            {"source": b.SOURCE_EMA, "document_id": "unrelated", "official_url": DOC_URL,
             "raw_json": "{}", "row_json": json.dumps({"qa_relevance": "Unrelated"})},
            {"source": b.SOURCE_EMA, "document_id": "filled", "official_url": DOC_URL,
             "raw_json": json.dumps({ed.TEXT_KEY: "full text"}), "row_json": "{}"},
            {"source": b.SOURCE_EMA, "document_id": "news", "official_url": NEWS_URL,
             "raw_json": "{}", "row_json": "{}"},
        ]

    def test_gap_counts_by_reason(self):
        gaps = cc.ema_document_gaps(self._rows())
        self.assertEqual(gaps["blank"], 2)
        self.assertEqual(gaps["by_reason"], {"http-404": 1, "unmarked": 1})
        self.assertEqual(gaps["docs"], ["blank-doc", "failed-doc"])

    def test_gap_opens_issue(self):
        rep = cc.build_report([], {}, today=date(2026, 11, 25), ema_gaps=cc.ema_document_gaps(self._rows()))
        self.assertEqual(rep["counts"]["ema_document_gaps"], 2)
        (plan,) = rep["issues"]
        self.assertEqual(plan["action"], "open")
        self.assertIn("EMA 문서(PDF) 본문 빈칸", plan["body"])
        clean = cc.build_report([], {}, today=date(2026, 11, 25), ema_gaps=cc.ema_document_gaps([]))
        self.assertEqual(clean["issues"][0]["action"], "close")


class BackfillWorkflowContractTest(unittest.TestCase):
    def test_exit_1_is_captured_not_fatal_under_bash_e(self):
        """exit 1 = 못 채운 행 있음. bash -e 에서 `cmd; rc=$?` 는 rc 에 닿기 전에 단계를 죽인다."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".github", "workflows", "grm-blank-excerpts-backfill.yml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('backfill_blank_excerpts.py "${args[@]}" > /dev/null || rc=$?', text)


if __name__ == "__main__":
    unittest.main()
