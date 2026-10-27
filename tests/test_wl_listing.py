"""FDA WL 목록 절단 회귀 — 정적 표(최신 10행)만 읽던 일일 수집(2026-10-27 감사).

FDA 는 화요일에 경고서한을 한꺼번에 게시하고 그 수가 10건을 넘는다(09/29 21건). 정적
WL 페이지의 HTML 표는 최신 10행만 서버 렌더하므로, 10행 밖 편지는 수집기가 존재조차
몰랐다 — 10-05 호에서 09/29 게시 cGMP 경고서한 4건이 Notion 에도 안 들어오고 빠졌다.

- 목록은 DataTables 전수 뷰(`warning_letter_solr_index`, 백필과 같은 뷰)를 먼저 읽는다.
- DataTables 가 죽으면 정적 표로 폴백하되, 10행이 전부 창 안이면 **절단 의심**을 남긴다.
- health: `wl-listing-truncated` · `wl-body-full-degraded`(전문 실패 — 종전 집계 0) warning.
"""
import json
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci
from grm_health import _evaluate_health

_WIN_START = date(2026, 9, 28)
_WIN_END = date(2026, 10, 5)
_AJAX_URL = "https://www.fda.gov/datatables/views/ajax"


def _static_row(i: int, posted: str = "09/29/2026") -> str:
    return (f"<tr><td>{posted}</td><td>09/22/2026</td>"
            f"<td><a href=\"/warning-letters/firm-{i}\">Static Firm {i}</a></td>"
            "<td>Center for Drug Evaluation and Research (CDER)</td>"
            "<td>CGMP/Finished Pharmaceuticals/Adulterated</td></tr>")


def _list_html(n_rows: int = 10, *, with_settings: bool = True, posted: str = "09/29/2026") -> str:
    settings = {"datatables": {"x": {"ajax": {
        "url": "/datatables/views/ajax",
        "data": {"view_name": "warning_letter_solr_index", "view_display_id": "page_1"},
    }}}}
    script = ('<script type="application/json" data-drupal-selector="drupal-settings-json">'
              + json.dumps(settings) + "</script>") if with_settings else ""
    return (script + "<table class=\"lcds-datatable-warning-letters table\">"
            "<tr><th>Posted Date</th><th>Letter Issue Date</th><th>Company Name</th>"
            "<th>Issuing Office</th><th>Subject</th></tr>"
            + "".join(_static_row(i, posted) for i in range(n_rows)) + "</table>")


def _dt_row(name: str, posted: str = "09/29/2026") -> list[str]:
    return [f"<time>{posted}</time>", "09/22/2026",
            f'<a href="/warning-letters/{name.lower().replace(" ", "-")}">{name} &amp; Co</a>',
            "Center for Drug Evaluation and Research (CDER)",
            "CGMP/Finished Pharmaceuticals/Adulterated", ""]


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _router(list_html: str, dt_pages: list[list[list[str]]] | Exception | None):
    calls: list[str] = []

    def _get(url, *args, **kwargs):
        calls.append(url)
        if url == ci.FDA_WL_URL:
            return _Resp(list_html)
        if url.startswith(_AJAX_URL):
            if isinstance(dt_pages, Exception):
                raise dt_pages
            page = len([c for c in calls if c.startswith(_AJAX_URL)]) - 1
            rows = dt_pages[page] if dt_pages and page < len(dt_pages) else []
            return _Resp(json.dumps({"data": rows, "recordsTotal": 999}))
        raise AssertionError(f"예상 밖 요청: {url}")
    return _get, calls


class WlListingDatatablesTest(unittest.TestCase):
    def _collect(self, list_html, dt_pages):
        get, calls = _router(list_html, dt_pages)
        with patch.dict(os.environ, {"ENABLE_WL_BODY": "false", "ENABLE_WL_BODY_FULL": "false"}), \
                patch.object(ci.requests, "get", side_effect=get):
            items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        return items, err, calls

    def test_datatables_sees_letters_beyond_static_ten(self) -> None:
        # 정적 표는 10행뿐이지만 그날 게시는 14건 — 전부 수집돼야 한다(실사고 재현).
        dt = [[_dt_row(f"Firm {i}") for i in range(14)] + [_dt_row("Old", "09/15/2026")]]
        items, err, _ = self._collect(_list_html(10), dt)
        self.assertIsNone(err)
        self.assertEqual(len(items), 14)
        self.assertEqual(ci.LAST_WL_HEALTH["listing"]["backbone"], "datatables")
        self.assertFalse(ci.LAST_WL_HEALTH["listing"]["truncated"])
        # 업체 셀 엔티티 복원 + 상대 href 정규화(정적 경로와 같은 결과)
        self.assertEqual(items[0].firm, "Firm 0 & Co")
        self.assertTrue(items[0].official_url.startswith("https://www.fda.gov/warning-letters/"))

    def test_same_doc_id_as_static_path(self) -> None:
        # 경로가 바뀌어도 dedup 키가 같아야 기존 Notion 행과 중복이 안 생긴다.
        static_items, _, _ = self._collect(_list_html(1, with_settings=False), None)
        dt = [[["09/29/2026", "09/22/2026",
                '<a href="/warning-letters/firm-0">Static Firm 0</a>',
                "Center for Drug Evaluation and Research (CDER)",
                "CGMP/Finished Pharmaceuticals/Adulterated", ""]]]
        dt_items, _, _ = self._collect(_list_html(1), dt)
        self.assertEqual([i.document_id for i in static_items], [i.document_id for i in dt_items])

    def test_pages_until_window_start(self) -> None:
        page1 = [_dt_row(f"A{i}") for i in range(ci.WL_LISTING_PAGE_LENGTH)]
        page2 = [_dt_row("B0"), _dt_row("Old", "09/01/2026")]
        items, err, calls = self._collect(_list_html(10), [page1, page2])
        self.assertIsNone(err)
        self.assertEqual(len([c for c in calls if c.startswith(_AJAX_URL)]), 2)
        self.assertEqual(len(items), ci.WL_LISTING_PAGE_LENGTH + 1)

    def test_page_cap_falls_back_and_flags(self) -> None:
        full = [_dt_row(f"A{i}") for i in range(ci.WL_LISTING_PAGE_LENGTH)]
        items, err, _ = self._collect(_list_html(10), [full] * ci.WL_LISTING_MAX_PAGES)
        self.assertIsNone(err)
        listing = ci.LAST_WL_HEALTH["listing"]
        self.assertEqual(listing["backbone"], "static-html")
        self.assertIn("page-cap", listing["detail"])
        self.assertTrue(listing["truncated"])
        self.assertEqual(len(items), 10)

    def test_datatables_failure_falls_back_to_static_with_truncation_flag(self) -> None:
        items, err, _ = self._collect(_list_html(10), requests.Timeout("slow"))
        self.assertIsNone(err)
        self.assertEqual(len(items), 10)
        listing = ci.LAST_WL_HEALTH["listing"]
        self.assertEqual(listing["backbone"], "static-html")
        self.assertTrue(listing["truncated"])          # 10행 전부 창 안 — 더 있을 수 있다
        self.assertIn("Timeout", listing["detail"])

    def test_static_fallback_covering_window_is_not_truncated(self) -> None:
        html = _list_html(3, with_settings=False).replace(
            "</table>", _static_row(99, "09/10/2026") + "</table>")
        items, err, _ = self._collect(html, None)
        self.assertIsNone(err)
        self.assertEqual(len(items), 3)
        self.assertFalse(ci.LAST_WL_HEALTH["listing"]["truncated"])


class WlStaticTruncationPredicateTest(unittest.TestCase):
    def test_all_rows_inside_window(self) -> None:
        rows = [{"_cols": ["09/29/2026", "", "x"]}, {"_cols": ["10/01/2026", "", "y"]}]
        self.assertTrue(ci.wl_static_listing_may_be_truncated(rows, _WIN_START))

    def test_oldest_row_before_window(self) -> None:
        rows = [{"_cols": ["09/29/2026", "", "x"]}, {"_cols": ["09/20/2026", "", "y"]}]
        self.assertFalse(ci.wl_static_listing_may_be_truncated(rows, _WIN_START))

    def test_empty_is_not_truncated(self) -> None:
        self.assertFalse(ci.wl_static_listing_may_be_truncated([], _WIN_START))


class WlHealthWarningsTest(unittest.TestCase):
    def _codes(self, **overrides) -> set[str]:
        stats = ci.CollectionStats()
        for k, v in overrides.items():
            setattr(stats, k, v)
        health = _evaluate_health(
            stats=stats, active={"wl"}, enable_search=False,
            enable_mfds=False, enable_mfds_law=False, enable_mfds_recall=False,
            enable_mfds_admin=False, enable_mfds_gmp_cert=False,
            enable_mfds_safety_letter=False, enable_mfds_gmp_inspection=False,
            enable_ich=False, enable_who=False, enable_hc=False, enable_fda483=False,
            enable_moleg_api=False, enable_scrape=False, event_name="schedule",
            emit_routine_handoff=False, handoff_emitted=False, handoff_failed=False,
            handoff_error_msg="",
        )
        return {w.code for w in health.warnings}

    def test_listing_truncated_warns(self) -> None:
        codes = self._codes(wl_fetched=10, wl_listing_backbone="static-html",
                            wl_listing_truncated=True, wl_listing_detail="Timeout: slow")
        self.assertIn("wl-listing-truncated", codes)

    def test_body_full_failure_warns(self) -> None:
        codes = self._codes(wl_fetched=3, wl_body_full_attempted=3, wl_body_full_failed=1)
        self.assertIn("wl-body-full-degraded", codes)

    def test_clean_run_has_neither(self) -> None:
        codes = self._codes(wl_fetched=3, wl_listing_backbone="datatables",
                            wl_body_full_attempted=3)
        self.assertNotIn("wl-listing-truncated", codes)
        self.assertNotIn("wl-body-full-degraded", codes)


if __name__ == "__main__":
    unittest.main()
