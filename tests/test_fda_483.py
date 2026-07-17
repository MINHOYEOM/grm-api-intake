"""FDA 483 수집기 회귀 — 현행 OII HTML/DataTables + Observation 상세보기.

검증: HTML/DataTables 행 파싱·Record Type=483 필터(EIR 제외)·Publish 윈도우·노이즈/수의/기기
게이트·dedup(media id)·PDF excerpt(483 앵커·graceful)·Observation 구조 추출(opt-in)·Tier·Country
매핑·구조변경 sentinel·flag/토큰 wiring.

무네트워크: _fetch_html_rows·http_get_bytes·_extract_pdf_text 스텁.
"""
import json
import os
import re
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f
import collect_mfds_gmp_inspection as g
import collect_intake as ci

# 윈도우 [2026-05-01, 2026-05-31]
START = date(2026, 5, 1)
END = date(2026, 5, 31)

EIR_TYPE = "Establishment Inspection Report (EIR)"
_HEADERS = ["Record Date", "Company Name", "FEI Number", "Record Type", "State",
            "Country", "Establishment Type", "Publish Date", "Excerpt"]


def _json_row(media_id, rtype="483", company="Acme Pharma Ltd",
              est="Drug Manufacturer", record_date="04/17/2026",
              publish="05/27/2026", state="Florida", fei="1234567", node_mid=None):
    """DataTables JSON 레코드 1건(probe 채록 구조 — Country 필드 없음·node mid≠media id)."""
    return {
        "mid": str(node_mid if node_mid is not None else media_id),
        "field_record_date": record_date,
        "field_fein": fei,
        "field_company_name_1": company,
        "field_foia_record_type_1": f'<a href="/media/{media_id}/download">{rtype}</a>',
        "field_state_1": state,
        "field_establishment_type_1": est,
        "field_publish_date": publish,
        "field_foia_record_type": rtype,
        "changed": "<time>x</time>",
    }


def _html_row(media_id, rtype="483", company="Acme Pharma Ltd", est="Drug Manufacturer",
              record_date="04/17/2026", publish="05/27/2026", state="Florida",
              country="", fei="1234567"):
    return dict(media_id=str(media_id), rtype=rtype, company=company, est=est,
                record_date=record_date, publish=publish, state=state,
                country=country, fei=fei)


def _tr(r):
    rt = f'<a href="/media/{r["media_id"]}/download">{r["rtype"]}</a>'
    cells = [r["record_date"], r["company"], r["fei"], rt, r["state"],
             r["country"], r["est"], r["publish"], ""]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _html(rows):
    header = "<tr>" + "".join(f"<th>{h}</th>" for h in _HEADERS) + "</tr>"
    body = "".join(_tr(r) for r in rows)
    return (f'<table class="lcds-datatable table table-bordered cols-9" '
            f'id="datatable">{header}{body}</table>')


class _StubBytes:
    def __init__(self, raise_exc=None):
        self.raise_exc = raise_exc
        self.urls = []

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        if self.raise_exc:
            raise self.raise_exc
        return b"%PDF-1.7 fake"


def _stub_pdf(text, status="pdf-ok"):
    def _inner(data, **kwargs):   # max_chars 등 kwarg 무시 — 스텁은 상한 무관하게 전체 반환
        return text, status
    return _inner


class _Patched:
    """HTML/DataTables 행·PDF fetch·텍스트 추출 스텁 + delay 0.

    기존 JSON fixture 입력도 HTML 행으로 변환해 테스트 데이터 재사용.
    """
    def __init__(self, json_rows=None, html_rows=None,
                 pdf_text="OBSERVATION 1 Sterile defect.", pdf_status="pdf-ok",
                 bytes_exc=None, json_exc=None, html_exc=None, source_degraded=False):
        self.json_rows = json_rows
        self.json_exc = json_exc
        self.source_degraded = source_degraded
        if html_exc:
            self.rows = []
            self.total = 0
        else:
            specs = html_rows
            if (specs is None or specs == []) and isinstance(json_rows, list):
                specs = [self._json_to_html_row(r) for r in json_rows if isinstance(r, dict)]
            if isinstance(specs, str):
                self.rows, self.total = f._html_norm_rows(specs)
            else:
                self.rows, self.total = f._html_norm_rows(_html(specs or []))
        self.html_exc = html_exc
        self.bytes = _StubBytes(raise_exc=bytes_exc)
        self.pdf_text = pdf_text
        self.pdf_status = pdf_status

    @staticmethod
    def _json_to_html_row(r):
        rt_cell = str(r.get("field_foia_record_type_1", ""))
        return _html_row(
            f._media_id_from(rt_cell),
            rtype=f._strip(r.get("field_foia_record_type")) or f._strip(rt_cell),
            company=f._strip(r.get("field_company_name_1")),
            est=f._strip(r.get("field_establishment_type_1")),
            record_date=f._strip(r.get("field_record_date")),
            publish=f._strip(r.get("field_publish_date")),
            state=f._strip(r.get("field_state_1")),
            country="",
            fei=f._strip(r.get("field_fein")),
        )

    def _stub_json(self, url, **kwargs):
        if self.json_exc:
            raise self.json_exc
        return self.json_rows

    def _stub_rows(self, start_date=None):
        return list(self.rows), self.total, self.source_degraded

    def __enter__(self):
        self._p = [
            patch.object(f, "http_get_json", self._stub_json),
            patch.object(f, "_fetch_html_rows", self._stub_rows),
            patch.object(f, "http_get_bytes", self.bytes),
            patch.object(g, "_extract_pdf_text", _stub_pdf(self.pdf_text, self.pdf_status)),
            patch.object(f, "FDA483_EXCERPT_DELAY_SECONDS", 0),
        ]
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self._p:
            p.stop()
        return False


class CompletenessTest(unittest.TestCase):
    def test_html_datatables_gives_window_rows_not_static_subset(self):
        rows = [
            _html_row(1, company="BPI Labs", record_date="04/17/2026", publish="05/27/2026"),
            _html_row(2, company="Wells Pharma", record_date="04/13/2026", publish="05/27/2026"),
            _html_row(3, company="Intas", state="", record_date="09/17/2025", publish="05/26/2026"),
            _html_row(4, company="Dabur India", state="", record_date="01/16/2026", publish="05/26/2026"),
            _html_row(5, company="Excel Vision", state="", record_date="01/22/2026", publish="05/15/2026"),
        ]
        with _Patched(html_rows=rows, pdf_text="OBSERVATION 1 x"):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual({it.document_id for it in items},
                         {"fda483-1", "fda483-2", "fda483-3", "fda483-4", "fda483-5"})
        self.assertFalse(f.LAST_HEALTH["source_degraded"])

    def test_node_mid_differs_from_media_id(self):
        # JSON node mid ≠ media id(href) — dedup·PDF 는 href media id 사용.
        json_rows = [_json_row(192689, node_mid="70123", company="Intas")]
        with _Patched(json_rows=json_rows, html_rows=[]):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].document_id, "fda483-192689")
        self.assertEqual(items[0].raw_payload["media_id"], "192689")


class RecordTypeFilterTest(unittest.TestCase):
    def test_only_483_kept(self):
        json_rows = [
            _json_row(1001, "483"),
            _json_row(1002, EIR_TYPE),
            _json_row(1003, "483 Response"),
            _json_row(1004, "Consent Decree"),
            _json_row(1006, "Amended 483"),
        ]
        with _Patched(json_rows=json_rows, html_rows=[]):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual({it.document_id for it in items}, {"fda483-1001"})

    def test_eir_is_out_of_scope(self):
        with _Patched(json_rows=[_json_row(2001, "483"), _json_row(2002, EIR_TYPE)],
                      html_rows=[]):
            items, _ = f.collect_fda_483(START, END)
        by_id = {it.document_id: it for it in items}
        self.assertEqual(by_id["fda483-2001"].type_or_class, "483")
        self.assertNotIn("fda483-2002", by_id)


class WindowFilterTest(unittest.TestCase):
    def test_publish_date_window(self):
        json_rows = [
            _json_row(3001, publish="05/27/2026"),   # in
            _json_row(3002, publish="01/17/2024"),   # out (old)
            _json_row(3003, publish="06/15/2026"),   # out (future)
        ]
        with _Patched(json_rows=json_rows, html_rows=[]):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual({it.document_id for it in items}, {"fda483-3001"})

    def test_empty_window_is_normal(self):
        with _Patched(json_rows=[_json_row(3101, publish="01/17/2024")], html_rows=[]):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNone(err)


class CountryMappingTest(unittest.TestCase):
    def test_us_state_only_maps_to_united_states(self):
        with _Patched(json_rows=[_json_row(4102, state="Florida")], html_rows=[]):
            items, _ = f.collect_fda_483(START, END)
        it = items[0]
        self.assertEqual(it.site_country, "United States")     # State 는 소재국 아님
        self.assertEqual(it.raw_payload["site_state"], "Florida")

    def test_foreign_country_enriched_from_html(self):
        # JSON 은 country 없음 → HTML Country 컬럼(media_id→country)으로 보강.
        json_rows = [_json_row(4201, company="Eugia", state="")]
        html_rows = [_html_row(4201, company="Eugia", state="", country="India")]
        with _Patched(json_rows=json_rows, html_rows=html_rows):
            items, _ = f.collect_fda_483(START, END)
        it = items[0]
        self.assertEqual(it.site_country, "India")
        self.assertEqual(it.raw_payload["country"], "India")

    def test_foreign_gap_row_is_blank_site_country(self):
        # 해외인데 HTML 에 없으면(완전성-갭 행) site_country=""(미상 — State 오기입 아님).
        with _Patched(json_rows=[_json_row(4202, company="Intas", state="")], html_rows=[]):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].site_country, "")

    def test_site_country_helper(self):
        self.assertEqual(f._site_country("India", ""), "India")
        self.assertEqual(f._site_country("", "Texas"), "United States")
        self.assertEqual(f._site_country("", ""), "")
        self.assertEqual(f._site_country("Canada", "X"), "Canada")   # Country 우선


class SourceDegradeTest(unittest.TestCase):
    def test_datatables_failure_static_html_fallback_is_marked(self):
        html_rows = [_html_row(5201, company="BPI", country="")]
        with _Patched(html_rows=html_rows, source_degraded=True):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual({it.document_id for it in items}, {"fda483-5201"})
        self.assertTrue(f.LAST_HEALTH["source_degraded"])   # 완전성 미보장 표면화

    def test_static_html_fallback_recovers(self):
        with _Patched(html_rows=[_html_row(5202)], source_degraded=True):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual({it.document_id for it in items}, {"fda483-5202"})
        self.assertTrue(f.LAST_HEALTH["source_degraded"])

    def test_both_sources_fail_is_error(self):
        with _Patched(json_exc=RuntimeError("boom"), html_exc=RuntimeError("boom")):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("수집 실패", err)

    def test_datatables_ok_no_degrade(self):
        with _Patched(json_rows=[_json_row(5203)], html_rows=[]):
            f.collect_fda_483(START, END)
        self.assertFalse(f.LAST_HEALTH["source_degraded"])


def _drupal_settings_html(rows):
    """정적 표 + 유효 DataTables 설정(drupal-settings-json) 포함 리딩룸 HTML."""
    settings = {"datatables": {"view-x": {"ajax": {
        "url": "/datatables/views/ajax",
        "data": {"view_name": "ora_foia_electronic_reading_room_solr",
                 "view_display_id": "reading_room", "total_items": 100},
    }}}}
    return (_html(rows)
            + '<script type="application/json" data-drupal-selector="drupal-settings-json">'
            + json.dumps(settings) + "</script>")


def _dt_row(media_id, rtype="483", publish="05/27/2026"):
    """DataTables AJAX data 배열 행(9컬럼·col3 에 media href)."""
    rt = f'<a href="/media/{media_id}/download">{rtype}</a>'
    return ["04/17/2026", "Acme Pharma Ltd", "1234567", rt, "Florida", "",
            "Drug Manufacturer", publish, ""]


_APOLOGY_HTML = "<html><head><title>Page Not Found | FDA</title></head><body>apology</body></html>"


class BackboneChainTest(unittest.TestCase):
    """백본 3단 폴백 체인(_fetch_html_rows 실경로) — 2026-07-17 Akamai 봇차단 대응.

    1차 DataTables(HTML 설정) → 2차 전수 JSON(구 backbone 부활) → 3차 정적 HTML 10행(부분).
    실측 장애 모드: 리딩룸 HTML 이 봇매니저 미끼 302→apology 404 로 대체되어 설정/표가 모두
    없는 200 응답이 온다(fetch 예외 없음) — 이때 2차 JSON 이 전수 수집을 대신해야 한다.
    """

    def _run(self, html=None, html_exc=None, dt_pages=None, dt_exc=None,
             json_data=None, json_exc=None):
        calls = {"json": 0}

        def stub_html(url, **kw):
            if html_exc:
                raise html_exc
            return html or ""

        def stub_dt(config, *, start, length, draw):
            if dt_exc:
                raise dt_exc
            pages = dt_pages or []
            idx = start // length
            data = pages[idx] if idx < len(pages) else []
            return {"data": data, "recordsFiltered": sum(len(p) for p in pages)}

        def stub_json(url, **kw):
            calls["json"] += 1
            if json_exc:
                raise json_exc
            return json_data if json_data is not None else []

        with patch.object(f, "http_get_html", stub_html), \
             patch.object(f, "_fetch_datatable_page", stub_dt), \
             patch.object(f, "http_get_json", stub_json):
            rows, count, degraded = f._fetch_html_rows(START)
        return rows, count, degraded, calls

    def test_akamai_blocked_html_falls_back_to_full_json(self):
        # 봇차단 apology 200(설정·표 없음) → 전수 JSON 백본이 전수 수집(EIR 은 제외).
        rows, count, degraded, calls = self._run(
            html=_APOLOGY_HTML,
            json_data=[_json_row(9001), _json_row(9002, EIR_TYPE), _json_row(9003)])
        self.assertEqual([r["media_id"] for r in rows], ["9001", "9003"])
        self.assertEqual(count, 3)
        self.assertFalse(degraded)          # 전수 백본 — 부분 fallback 아님
        self.assertEqual(calls["json"], 1)

    def test_html_fetch_exception_falls_back_to_full_json(self):
        rows, _, degraded, _ = self._run(
            html_exc=RuntimeError("boom"), json_data=[_json_row(9101)])
        self.assertEqual([r["media_id"] for r in rows], ["9101"])
        self.assertFalse(degraded)

    def test_datatables_healthy_short_circuits_json(self):
        # 1차 정상 → 2차 JSON 은 호출조차 없음(현행 정상경로 불변).
        rows, _, degraded, calls = self._run(
            html=_drupal_settings_html([_html_row(1)]),
            dt_pages=[[_dt_row(9201)]])
        self.assertEqual([r["media_id"] for r in rows], ["9201"])
        self.assertFalse(degraded)
        self.assertEqual(calls["json"], 0)

    def test_datatables_error_falls_back_to_json_before_static(self):
        rows, _, degraded, calls = self._run(
            html=_drupal_settings_html([_html_row(9301)]),
            dt_exc=RuntimeError("503"), json_data=[_json_row(9302)])
        self.assertEqual([r["media_id"] for r in rows], ["9302"])
        self.assertFalse(degraded)
        self.assertEqual(calls["json"], 1)

    def test_datatables_zero_rows_falls_back_to_json(self):
        rows, _, degraded, _ = self._run(
            html=_drupal_settings_html([_html_row(9401)]),
            dt_pages=[[]], json_data=[_json_row(9402)])
        self.assertEqual([r["media_id"] for r in rows], ["9402"])
        self.assertFalse(degraded)

    def test_json_dead_falls_back_to_static_html_partial(self):
        # 2차까지 죽으면 기존 3차(정적 10행·부분) 동작 보존 — degraded 표면화.
        rows, count, degraded, _ = self._run(
            html=_html([_html_row(9501)]), json_exc=RuntimeError("404"))
        self.assertEqual([r["media_id"] for r in rows], ["9501"])
        self.assertEqual(count, 1)
        self.assertTrue(degraded)

    def test_json_non_list_falls_back_to_static(self):
        rows, _, degraded, _ = self._run(
            html=_html([_html_row(9601)]), json_data={"unexpected": "shape"})
        self.assertEqual([r["media_id"] for r in rows], ["9601"])
        self.assertTrue(degraded)

    def test_stale_json_backbone_is_marked_degraded(self):
        # 2차 JSON 이 살아있되 갱신 정지(최신 publish < 윈도우 시작) → 행은 쓰되
        # degraded=True 로 완전성 리스크 표면화(침묵 누락 금지).
        rows, _, degraded, _ = self._run(
            html=_APOLOGY_HTML, json_data=[_json_row(9701, publish="01/17/2024")])
        self.assertEqual([r["media_id"] for r in rows], ["9701"])
        self.assertTrue(degraded)
        self.assertEqual(f._LAST_BACKBONE, f.BACKBONE_LEGACY_JSON)

    def test_fresh_json_backbone_is_not_degraded(self):
        rows, _, degraded, _ = self._run(
            html=_APOLOGY_HTML, json_data=[_json_row(9702, publish="05/27/2026")])
        self.assertEqual([r["media_id"] for r in rows], ["9702"])
        self.assertFalse(degraded)

    def test_backbone_marker_records_active_tier(self):
        self._run(html=_drupal_settings_html([_html_row(1)]), dt_pages=[[_dt_row(9801)]])
        self.assertEqual(f._LAST_BACKBONE, f.BACKBONE_DATATABLES)
        self._run(html=_APOLOGY_HTML, json_data=[_json_row(9802)])
        self.assertEqual(f._LAST_BACKBONE, f.BACKBONE_LEGACY_JSON)
        self._run(html=_html([_html_row(9803)]), json_exc=RuntimeError("404"))
        self.assertEqual(f._LAST_BACKBONE, f.BACKBONE_STATIC_HTML)

    def test_all_backbones_dead_yields_collect_error(self):
        # 3단 전부 사망 → collect_fda_483 이 error 로 표면화(침묵 금지 불변).
        def stub_html(url, **kw):
            return _APOLOGY_HTML

        def stub_json(url, **kw):
            raise RuntimeError("404")

        with patch.object(f, "http_get_html", stub_html), \
             patch.object(f, "http_get_json", stub_json):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("수집 실패", err)
        self.assertEqual(f.LAST_HEALTH["backbone"], f.BACKBONE_STATIC_HTML)


class NoiseGateTest(unittest.TestCase):
    def test_veterinary_dropped(self):
        json_rows = [_json_row(4001, company="VetMeds Inc", est="Veterinary Drug Manufacturer")]
        with _Patched(json_rows=json_rows, html_rows=[]):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNone(err)

    def test_medical_device_dropped(self):
        json_rows = [_json_row(4002, company="DeviceCo", est="Medical Device Manufacturer")]
        with _Patched(json_rows=json_rows, html_rows=[], pdf_text="OBSERVATION 1 device issue."):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])

    def test_drug_manufacturer_kept(self):
        with _Patched(json_rows=[_json_row(4003, est="Drug Manufacturer")], html_rows=[]):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(len(items), 1)


class DedupTest(unittest.TestCase):
    def test_dedup_by_media_id(self):
        json_rows = [_json_row(5001), _json_row(5001, company="Dup")]
        with _Patched(json_rows=json_rows, html_rows=[]):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].document_id, "fda483-5001")


class ExcerptTest(unittest.TestCase):
    def test_excerpt_extracted_and_feeds_raw(self):
        text = ("Cover FEI 1234567. This document lists observations. "
                "DURING AN INSPECTION OF YOUR FIRM I/WE OBSERVED: "
                "OBSERVATION 1 Aseptic processing was deficient and media fills failed.")
        with _Patched(json_rows=[_json_row(6001)], html_rows=[], pdf_text=text):
            items, _ = f.collect_fda_483(START, END)
        it = items[0]
        self.assertIn("fda483_excerpt", it.raw_payload)
        self.assertTrue(it.raw_payload["fda483_excerpt"].lower().startswith("observation 1"))
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["ok"], 1)

    def test_excerpt_anchor_priority_observation1(self):
        text = ("preamble specifically, foo. During an inspection of your firm bar. "
                "OBSERVATION 1 the real finding.")
        self.assertTrue(f._extract_fda483_excerpt(text).lower().startswith("observation 1"))

    def test_excerpt_no_anchor_returns_empty(self):
        self.assertEqual(f._extract_fda483_excerpt("just a cover page with address"), "")

    def test_graceful_fetch_fail_keeps_item(self):
        with _Patched(json_rows=[_json_row(6002)], html_rows=[],
                      bytes_exc=RuntimeError("HTTP 403 for ...")):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("fda483_excerpt", items[0].raw_payload)
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["failed"], 1)

    def test_graceful_encrypted_pdf(self):
        with _Patched(json_rows=[_json_row(6003)], html_rows=[],
                      pdf_text="", pdf_status="pdf-encrypted"):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("fda483_excerpt", items[0].raw_payload)

    def test_graceful_anchor_miss(self):
        with _Patched(json_rows=[_json_row(6004)], html_rows=[],
                      pdf_text="cover page only, no findings section"):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(len(items), 1)
        self.assertNotIn("fda483_excerpt", items[0].raw_payload)
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["failed"], 1)

    def test_excerpt_cap(self):
        json_rows = [_json_row(7000 + i, publish="05/2%d/2026" % (i % 9)) for i in range(3)]
        with _Patched(json_rows=json_rows, html_rows=[]), \
                patch.object(f, "FDA483_EXCERPT_MAX_ITEMS", 2):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["attempted"], 2)
        self.assertTrue(f.LAST_HEALTH["fda483_excerpt"]["capped"])
        self.assertEqual(len(items), 3)        # cap 은 excerpt 만 제한, 항목은 전부 유지


class ObservationExtractionTest(unittest.TestCase):
    SAMPLE = (
        "Cover. I/WE OBSERVED: OBSERVATION 1 There is a failure to thoroughly review "
        "unexplained discrepancies. The investigation did not extend to other batches. "
        "OBSERVATION 2 Established sampling plans are not documented at the time of "
        "performance. Additional examples were observed. SEE REVERSE FORM FDA 483"
    )

    def test_text_observations_split_deterministically(self):
        rows = f._extract_483_observations_from_text(self.SAMPLE)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertEqual(rows[0]["deficiency"],
                         "There is a failure to thoroughly review unexplained discrepancies.")
        self.assertIn("other batches", rows[0]["detail"])
        self.assertEqual(rows[1]["deficiency"],
                         "Established sampling plans are not documented at the time of performance.")

    def test_footer_signature_block_stripped_from_detail(self):
        # [2026-07 실측 결함] 스캔 OCR 이 483 페이지 하단 서명/양식 푸터를 Observation detail
        # 자리로 흘려보내 garbage(EMPLOYEE(S) SIGNATURE ... FORM FDA 483 ...)가 노출됐다.
        # 옛 정규식은 EMPLOYEE\(S\)\b 의 후행 \b 가 ')' 뒤에서 성립 안 해 못 잡았고 OCR 변형에
        # 취약했다. 새 클리너는 footer 를 절단하고, 본문이 통째로 footer 로 대체된 관찰은 detail 을 비운다.
        garbage_only = ("Specifically, EMPI..OYEE(S) SIGNAT\\JRE SEE Muna Algharibeh, "
                        "I nvestigator 07/24/2025 REVERSE OF Tiffani , Veterinary THIS PAGE "
                        "Medical Offi cer , Branch Chief ~ FORM FDA 4&3 (09/08) PREVIOUS.EDmON")
        self.assertEqual(f._clean_observation_detail(garbage_only), "")  # detail 통째 garbage → 빈값

        legit_plus_footer = ("Specifically, Your firm's batch records do not include complete "
                             "documentation of each significant step. EMPLOYEE(S) SIGNA~ SEE "
                             "Muna Algharibeh, Investigator FORM FDA483 (09/0S)")
        cleaned = f._clean_observation_detail(legit_plus_footer)
        self.assertIn("batch records do not include", cleaned)         # 실질 본문 보존
        self.assertNotIn("EMPLOYEE", cleaned)                          # 서명블록 제거
        self.assertNotIn("SIGNA", cleaned)
        self.assertNotIn("FORM FDA", cleaned)

        # ($) OCR 변형 + 소문자 'employees' 산문 오탐 방지 동시 확인
        quva = ("Specifically, on 4/20/2026, I observed paint peeling off the ISO 7 Cleanroom. "
                "EMPLOYEE($) SIGNATURE DATE lSSUEO")
        self.assertIn("paint peeling", f._clean_observation_detail(quva))
        self.assertNotIn("EMPLOYEE", f._clean_observation_detail(quva))
        prose = ("Specifically, the minimum garb is required. However, employees were observed "
                 "donning gloves upon entry through the back door.")
        self.assertIn("employees were observed", f._clean_observation_detail(prose))  # 산문 미절단

    def test_footer_ocr_mangled_employee_marker_stripped(self):
        # [2026-07-12 Catalent Indiana 실측] OCR 이 EMPLOYEE(S) SIGNATURE 를
        # "I EMPi.OY1:E($) SIGJ'IAl\lRE /I SEE" 로 깨뜨림 — 옛 정규식은 "OYEE" 리터럴이 깨져
        # (OY1:E) 못 잡았다. 선행 고립 "I " 도 함께 절단되고 마침표는 보존돼야 한다.
        mangled = ("...you continue to observe mammalian hair in finished drug products. "
                   "I EMPi.OY1:E($) SIGJ'IAl\\lRE /I SEE")
        cleaned = f._clean_observation_detail(mangled)
        self.assertTrue(cleaned.endswith("finished drug products."), cleaned)
        for residue in ("EMP", "OY1", "SIGJ"):
            self.assertNotIn(residue, cleaned)
        self.assertFalse(re.search(r"(?:^|\s)[IlL]$", cleaned), cleaned)  # 고립 낱자 I/l 잔존 없음

    def test_footer_add_continuation_page_marker_stripped(self):
        # [2026-07-12 실측] Observation 별 "Add Continuation Page" 연속페이지 마커도 절단 대상.
        text = ("...inadequate to prevent cross-contamination. Add Continuation Page")
        cleaned = f._clean_observation_detail(text)
        self.assertTrue(cleaned.endswith("cross-contamination."), cleaned)
        self.assertNotIn("Continuation", cleaned)

    def test_footer_clean_employee_marker_still_stripped_regression(self):
        # 회귀: 깨지지 않은 정상 폼 푸터도 여전히 절단돼야 한다.
        text = "...text here EMPLOYEE(S) SIGNATURE SEE REVERSE"
        cleaned = f._clean_observation_detail(text)
        self.assertNotIn("EMPLOYEE", cleaned)
        self.assertNotIn("SIGNATURE", cleaned)

    def test_footer_lowercase_employees_prose_not_truncated(self):
        # 오탐 방지: 괄호 없는 소문자 "employees" 산문은 절단되면 안 된다.
        text = ("Specifically, the firm failed to ensure employees followed gowning procedures "
                "before entering the aseptic core.")
        cleaned = f._clean_observation_detail(text)
        self.assertIn("employees followed gowning procedures", cleaned)
        self.assertTrue(cleaned.endswith("aseptic core."), cleaned)

    def test_footer_ocr_missing_open_paren_marker_stripped(self):
        # [2026-07-12 Catalent(2번째 483) obs#8 실측] OCR 이 "EMPLOYEE(S)" 의 여는 괄호 '(' 까지
        # 삼켜 "EMPt..oYEECS)" 로 깨뜨림(닫는 ')' 만 남음). 옛 패턴은 여는 괄호 `\(` 를 필수로
        # 요구해 이 변형을 통째로 놓쳤다(서명블록·조사관 실명이 detail 에 그대로 노출).
        garbage_tail = ("EMPt..oYEECS) SIGNATURE SEE Joohi Castelvetere, "
                        "Investigator 04/24/2026 R")
        # 실질 본문이 얇으면(§_DETAIL_MIN_ALPHA) 잔여 전체를 비운다 — 그 자체가 안전한 결과
        # (서명블록 잔재가 절대 노출되지 않음).
        thin = f"...<Redacted B4> {garbage_tail}"
        cleaned_thin = f._clean_observation_detail(thin)
        for residue in ("EMP", "SIGNATURE", "Castelvetere", "Investigator"):
            self.assertNotIn(residue, cleaned_thin)

        # 실질 본문이 충분하면 footer 마커 직전까지만 남고 그 뒤는 전부 절단.
        rich = ("Specifically, the firm failed to document the operator who performed the "
                "batch record correction after the deviation was discovered during the "
                f"inspection. {garbage_tail}")
        cleaned_rich = f._clean_observation_detail(rich)
        self.assertIn("batch record correction", cleaned_rich)
        for residue in ("EMP", "SIGNATURE", "Castelvetere", "Investigator"):
            self.assertNotIn(residue, cleaned_rich)

        # 오탐 방지 회귀: 소문자 "our employees)" 산문(괄호 있어도 소문자 EMP 는 대문자 고정
        # (?-i:EMP) 에 안 걸린다)은 절단되면 안 된다.
        lower_prose = ("Specifically, our employees) were not properly trained on the "
                       "corrective procedure outlined in the current SOP revision.")
        cleaned_prose = f._clean_observation_detail(lower_prose)
        self.assertIn("our employees) were not properly trained", cleaned_prose)

    def test_observation_flag_off_does_not_write_raw(self):
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "false"}), \
                _Patched(json_rows=[_json_row(6101)], html_rows=[], pdf_text=self.SAMPLE):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertNotIn("fda_483_observations", items[0].raw_payload)
        self.assertFalse(f.LAST_HEALTH["fda_483_observations"]["enabled"])

    def test_observation_flag_on_writes_raw_and_health(self):
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "true"}), \
                _Patched(json_rows=[_json_row(6102)], html_rows=[], pdf_text=self.SAMPLE):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        obs = items[0].raw_payload["fda_483_observations"]
        self.assertEqual(len(obs), 2)
        self.assertEqual(obs[0]["number"], "1")
        self.assertEqual(f.LAST_HEALTH["fda_483_observations"]["attempted"], 1)
        self.assertEqual(f.LAST_HEALTH["fda_483_observations"]["extracted"], 1)
        self.assertEqual(f.LAST_HEALTH["fda_483_observations"]["failed"], 0)

    def test_observation_gate_degrades_to_summary_card(self):
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "true"}), \
                _Patched(json_rows=[_json_row(6103)], html_rows=[],
                         pdf_text="cover page with no observation anchors"):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertNotIn("fda_483_observations", items[0].raw_payload)
        self.assertEqual(f.LAST_HEALTH["fda_483_observations"]["failed"], 1)


class PageHeaderScrubTest(unittest.TestCase):
    """FIND-1 M10a — 483 Observation 이 페이지 경계에 걸쳐 헤더 라벨-값 인터리브(STREET
    ADDRESS/FEI NUMBER/TYPE OF ESTABLISHMENT INSPECTED 등)가 deficiency 앞에 접두사로 섞여
    들어오는 라이브 오염(VA San Diego Healthcare Systems, doc fda483-193454) 회귀 가드.
    """

    HEADER_BLOCK = (
        "STREET ADDRESS 4/27/26-5/1/26, 5/4/26-5/6/26, 5/8/26 FEI NUMBER 2071629 "
        "3350 La Jolla Village Dr TYPE OF ESTABLISHMENT INSPECTED Producer of Sterile "
        "Drug Products "
    )

    def test_extract_from_text_scrubs_header_with_hints(self):
        text = (
            "I/WE OBSERVED: OBSERVATION 1 " + self.HEADER_BLOCK +
            "Personnel engaged in aseptic processing were observed wearing "
            "non-sterile gloves."
        )
        hints = {
            "establishment_type": "Producer of Sterile Drug Products",
            "fei_number": "2071629",
            "firm_name": "VA San Diego Healthcare Systems",
        }
        rows = f._extract_483_observations_from_text(text, hints)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["deficiency"],
            "Personnel engaged in aseptic processing were observed wearing non-sterile gloves.",
        )

    def test_extract_from_text_without_hints_still_strips_label_date_digits_address(self):
        # header_hints=None(기본값, 후방호환) 이어도 라벨/날짜범위/FEI 숫자런/미국식 주소는 제거된다.
        text = (
            "I/WE OBSERVED: OBSERVATION 1 " + self.HEADER_BLOCK +
            "Personnel engaged in aseptic processing were observed wearing "
            "non-sterile gloves."
        )
        rows = f._extract_483_observations_from_text(text)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("STREET ADDRESS", rows[0]["deficiency"])
        self.assertNotIn("2071629", rows[0]["deficiency"])
        self.assertNotIn("3350 La Jolla Village Dr", rows[0]["deficiency"])

    def test_collect_loop_wires_nrow_hints_into_observation_scrub(self):
        # 수집 루프가 nrow(establishment_type/fei/company)를 header_hints 로 그대로 넘겨
        # deficiency 오염을 제거하는지 엔드투엔드로 확인.
        pdf_text = (
            "I/WE OBSERVED: OBSERVATION 1 " + self.HEADER_BLOCK +
            "Personnel engaged in aseptic processing were observed wearing "
            "non-sterile gloves."
        )
        json_rows = [_json_row(
            6401, est="Producer of Sterile Drug Products", fei="2071629",
            company="VA San Diego Healthcare Systems",
        )]
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "true"}), \
                _Patched(json_rows=json_rows, html_rows=[], pdf_text=pdf_text):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        obs = items[0].raw_payload["fda_483_observations"]
        self.assertEqual(len(obs), 1)
        self.assertEqual(
            obs[0]["deficiency"],
            "Personnel engaged in aseptic processing were observed wearing non-sterile gloves.",
        )


class DeepBodyFullTest(unittest.TestCase):
    """[483 분석층 2026-07-02] ENABLE_FDA_483_DEEP on 일 때만 PDF 전문을 raw.fda483_body_full 로
    보존해 deep_analysis fan-out 입력으로 쓴다. 결정론 Observation(ENABLE_FDA_483_OBSERVATIONS)과
    독립. 파싱 불가(스캔본/표지-only)면 body_full 미기록(graceful — 요약카드·결정론 상세 유지)."""

    SAMPLE = ("Cover. I/WE OBSERVED: OBSERVATION 1 There is a failure to review unexplained "
              "discrepancies. OBSERVATION 2 Sampling plans are not documented at performance. "
              "SEE REVERSE FORM FDA 483")

    def test_deep_flag_off_no_body_full(self):
        with patch.dict(os.environ, {"ENABLE_FDA_483_DEEP": "false"}), \
                _Patched(json_rows=[_json_row(6201)], html_rows=[], pdf_text=self.SAMPLE):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertNotIn("fda483_body_full", items[0].raw_payload)
        self.assertFalse(f.LAST_HEALTH["fda_483_deep"]["enabled"])
        self.assertEqual(f.LAST_HEALTH["fda_483_deep"]["stored"], 0)

    def test_deep_flag_on_stores_full_text_and_health(self):
        with patch.dict(os.environ, {"ENABLE_FDA_483_DEEP": "true"}), \
                _Patched(json_rows=[_json_row(6202)], html_rows=[], pdf_text=self.SAMPLE):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(items[0].raw_payload["fda483_body_full"], self.SAMPLE)
        h = f.LAST_HEALTH["fda_483_deep"]
        self.assertTrue(h["enabled"])
        self.assertEqual((h["attempted"], h["stored"], h["failed"]), (1, 1, 0))

    def test_deep_independent_of_observations_flag(self):
        # deep on·observations off → 전문(body_full)은 저장되지만 결정론 상세 키는 부재(독립).
        with patch.dict(os.environ, {"ENABLE_FDA_483_DEEP": "true",
                                     "ENABLE_FDA_483_OBSERVATIONS": "false"}), \
                _Patched(json_rows=[_json_row(6203)], html_rows=[], pdf_text=self.SAMPLE):
            items, err = f.collect_fda_483(START, END)
        self.assertIn("fda483_body_full", items[0].raw_payload)
        self.assertNotIn("fda_483_observations", items[0].raw_payload)

    def test_deep_garbage_pdf_degrades_gracefully(self):
        # 앵커 없는 표지-only(파싱 0) → body_full 미기록·failed=1, 요약카드/결정론 상세 유지.
        with patch.dict(os.environ, {"ENABLE_FDA_483_DEEP": "true"}), \
                _Patched(json_rows=[_json_row(6204)], html_rows=[],
                         pdf_text="cover page with no observation anchors"):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertNotIn("fda483_body_full", items[0].raw_payload)
        self.assertEqual(f.LAST_HEALTH["fda_483_deep"]["failed"], 1)

    def test_fetch_pdf_text_uses_full_483_cap_by_default(self):
        # ★라이브 경로 절단 회귀: _fetch_fda483_pdf_text 는 기본으로 483 전용 200000 상한을
        #   _extract_pdf_text 에 넘긴다(공유 GMP 12000 기본이 아님). 이걸 되돌리면(=default 없이
        #   호출) 결정론 Observation·deep 전문이 다시 앞 2~3건에서 잘린다(PR #57 미해결분).
        captured = {}

        def _cap_stub(data, max_chars=None):
            captured["max_chars"] = max_chars
            return "text", "pdf-ok"
        with patch.object(g, "_extract_pdf_text", _cap_stub), \
                patch.object(f, "http_get_bytes", _StubBytes()):
            f._fetch_fda483_pdf_text("https://x/media/1/download")   # 기본 상한
        self.assertEqual(captured["max_chars"], f.FDA483_TEXT_MAX_CHARS)

    def test_live_loop_long_483_extracts_all_observations(self):
        # ★엔드투엔드 절단 회귀: 라이브 수집 루프(_fetch→_extract_from_text)가 8쪽+ 483 의
        #   Observation 을 전건 추출해야 한다. max_chars 를 실제로 존중하는 스텁으로 절단 경계를
        #   재현 — 12000 이면 앞 2~3건만, 200000 이면 6건 전부(수정 없으면 이 테스트가 실패한다).
        filler = " The inspection team reviewed additional batch records in detail." * 50
        long_text = "I/WE OBSERVED: " + "".join(
            f"OBSERVATION {n} Deficiency {n} concerns inadequate process control.{filler}"
            for n in range(1, 7))
        self.assertGreater(long_text.index("OBSERVATION 6"), 12000)   # 6번째는 12000자 이후

        def _honor_cap(data, max_chars=12000):        # 실 엔진처럼 상한을 존중(GMP 기본=12000)
            return long_text[:max_chars], "pdf-ok"
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "true"}), \
                _Patched(json_rows=[_json_row(6301)], html_rows=[], pdf_text=long_text), \
                patch.object(g, "_extract_pdf_text", _honor_cap):   # _Patched 의 무시-스텁 위에 덮어씀
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        obs = items[0].raw_payload["fda_483_observations"]
        self.assertEqual([o["number"] for o in obs], ["1", "2", "3", "4", "5", "6"])


class ObservationTruncationTest(unittest.TestCase):
    """긴 483(8쪽+·2만자↑)에서 GMP용 12000자 상한이 뒤 Observation 을 자르던 버그 가드.

    483 경로는 FDA483_TEXT_MAX_CHARS(200000)로 PDF 텍스트를 읽어 뒤 Observation 을 보존하고,
    상한 도달 시 조용한 유실 대신 WARN 을 남긴다. GMP/WHO 경로는 기본 12000 그대로.
    """
    _FILLER = (" The inspection team reviewed additional batch records and quality "
               "data relating to this observation in detail.") * 50

    def _long_483_text(self) -> str:
        blocks = [
            f"OBSERVATION {n} Deficiency {n} concerns inadequate control of the "
            f"manufacturing process.{self._FILLER}"
            for n in range(1, 7)
        ]
        return "Cover page. I/WE OBSERVED: " + "".join(blocks)

    def test_long_doc_all_six_observations_extracted(self):
        text = self._long_483_text()
        self.assertGreater(len(text), 12000)                     # GMP 상한을 넘는 긴 문서
        self.assertGreater(text.index("OBSERVATION 6"), 12000)   # 6번째는 12000자 이후
        rows = f._extract_483_observations_from_text(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2", "3", "4", "5", "6"])

    def test_483_reads_beyond_gmp_cap(self):
        # 483 경로는 GMP 12000 이 아닌 200000 으로 PDF 를 읽어 뒤 Observation 을 보존.
        self.assertGreater(f.FDA483_TEXT_MAX_CHARS, g.MAX_ATTACHMENT_TEXT_CHARS)
        full = self._long_483_text()
        captured = {}

        def fake_extract(data, max_chars=g.MAX_ATTACHMENT_TEXT_CHARS):
            captured["max_chars"] = max_chars
            return full[:max_chars], "pdf-ok"   # 실 엔진과 동일하게 max_chars 로 절단

        with patch.object(g, "_extract_pdf_text", fake_extract):
            rows = f._extract_483_observations(b"%PDF-1.4 fake")
        self.assertEqual(captured["max_chars"], f.FDA483_TEXT_MAX_CHARS)
        self.assertEqual([r["number"] for r in rows], ["1", "2", "3", "4", "5", "6"])

    def test_gmp_default_cap_unchanged(self):
        # GMP(및 WHO·483 excerpt) 경로는 기본값 12000 그대로 — 회귀 방지.
        import inspect
        self.assertEqual(g.MAX_ATTACHMENT_TEXT_CHARS, 12000)
        default = inspect.signature(g._extract_pdf_text).parameters["max_chars"].default
        self.assertEqual(default, g.MAX_ATTACHMENT_TEXT_CHARS)

    def test_cap_reached_logs_warning(self):
        # 상한 도달 시 조용한 유실 대신 WARN(수동 확인 신호) — silent loss 방지.
        at_cap = "OBSERVATION 1 Deficiency one is noted. " + "x" * f.FDA483_TEXT_MAX_CHARS
        logged: list[tuple[str, str]] = []

        def fake_extract(data, max_chars=g.MAX_ATTACHMENT_TEXT_CHARS):
            return at_cap[:max_chars], "pdf-ok"

        with patch.object(g, "_extract_pdf_text", fake_extract), \
                patch.object(f, "log", lambda level, msg: logged.append((level, msg))):
            f._extract_483_observations(b"%PDF fake")
        self.assertTrue(any(lvl == "WARN" and "상한 도달" in msg for lvl, msg in logged))


class TierTest(unittest.TestCase):
    def test_483_tier3(self):
        json_rows = [_json_row(8001, "483", est="Drug Manufacturer")]
        with _Patched(json_rows=json_rows, html_rows=[], pdf_text="OBSERVATION 1 generic."):
            items, _ = f.collect_fda_483(START, END)
        by_id = {it.document_id: it for it in items}
        self.assertEqual(by_id["fda483-8001"].signal_tier, "Tier 3")

    def test_sterile_483_floor_tier3(self):
        json_rows = [_json_row(8003, "483", est="Producer of Sterile Drug Products")]
        with _Patched(json_rows=json_rows, html_rows=[], pdf_text="OBSERVATION 1 aseptic."):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].signal_tier, "Tier 3")

    def test_distributor_only_tier_down(self):
        with _Patched(json_rows=[_json_row(8004, est="Distributor")], html_rows=[],
                      pdf_text="cover only"):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].signal_tier, "Tier 2")


class StructureSentinelTest(unittest.TestCase):
    def test_no_483_anywhere_errors(self):
        # HTML/DataTables 483 0행 → 구조 변경 의심 error(침묵 0건 금지).
        json_rows = [_json_row(9001, "Consent Decree"), _json_row(9002, "Recall Record")]
        with _Patched(json_rows=json_rows, html_rows=[]):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)

    def test_static_fallback_recovers(self):
        with _Patched(html_rows=[_html_row(9004)], source_degraded=True):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual({it.document_id for it in items}, {"fda483-9004"})
        self.assertTrue(f.LAST_HEALTH["source_degraded"])


class ItemShapeTest(unittest.TestCase):
    def test_item_fields(self):
        json_rows = [_json_row(1, company="BPI Labs, LLC", fei="3015156709",
                               state="Florida", est="Outsourcing Facility")]
        with _Patched(json_rows=json_rows, html_rows=[], pdf_text="OBSERVATION 1 aseptic."):
            items, _ = f.collect_fda_483(START, END)
        it = items[0]
        self.assertEqual(it.source, "FDA 483")
        self.assertEqual(it.official_url, "https://www.fda.gov/media/1/download")
        self.assertEqual(it.site_country, "United States")
        self.assertEqual(it.raw_payload["fei_number"], "3015156709")
        self.assertEqual(it.date_iso, "2026-05-27")
        self.assertEqual(it.region_jurisdiction, "USA (FDA)")


class HtmlEntityContractTest(unittest.TestCase):
    """엔티티가 수집기를 통과하지 못하게 고정(2026-07-16 라이브 사고 회귀).

    사고: `_strip` 이 태그만 제거하고 복원을 안 해 `H &amp; P Industries, Inc.` 가 그대로
    Supabase findings.firm_name/site_name 439행에 적재됐다(전량 source='FDA 483').
    화면(findings.js)은 textContent 렌더라 엔티티가 literal 로 노출됐다.
    원인은 FDA 원본이 escape 해서 내려주는 것 — 라이브 3079행 중 217셀에 실재
    (`&amp;` 129 / `&#039;` 95 / `&quot;` 2). JSON 경로만 새고 HTML 경로는 HTMLParser 가
    이미 복원하므로 무사했다 → 아래 테스트는 **JSON 경로**(현행 라이브)를 직접 친다.
    """

    # 엔티티 형태 일반 탐지 — 특정 엔티티 화이트리스트가 아니라 '엔티티 자체'를 금지한다.
    ENTITY_RE = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});")

    def test_strip_unescapes_entities(self):
        # 라이브 실측 표기 그대로.
        self.assertEqual(f._strip("H &amp; P Industries, Inc."), "H & P Industries, Inc.")
        self.assertEqual(f._strip("Dr. Reddy&#039;s Laboratories Ltd."),
                         "Dr. Reddy's Laboratories Ltd.")
        self.assertEqual(f._strip("Aunt Mid&#039;s Produce Company"),
                         "Aunt Mid's Produce Company")
        self.assertEqual(f._strip("A &quot;B&quot; Pharma"), 'A "B" Pharma')

    def test_strip_order_tag_then_unescape(self):
        # 복원이 태그 제거보다 뒤여야 `&lt;b&gt;` 가 태그로 오인돼 삭제되지 않는다.
        self.assertEqual(f._strip("<b>Acme</b> &lt;b&gt;X&lt;/b&gt;"), "Acme <b>X</b>")
        # 복원된 &nbsp;(\xa0) 는 뒤따르는 공백 축약이 흡수한다.
        self.assertEqual(f._strip("Acme&nbsp;&nbsp;Pharma"), "Acme Pharma")

    def test_strip_single_level_unescape_only(self):
        # 이중 복원 금지 — `&amp;amp;` 는 한 단계만 풀려 리터럴 `&amp;` 로 남아야 한다.
        self.assertEqual(f._strip("A &amp;amp; B"), "A &amp; B")

    def test_datatable_norm_rows_unescape(self):
        # 현행 라이브 경로(DataTables `data` 셀) — 여기서 새던 구멍.
        raw = [[
            "04/17/2026",
            "H &amp; P Industries, Inc.",
            "1234567",
            '<a href="/media/9001/download">483</a>',
            "Wisconsin",
            "",
            "Drug Manufacturer",
            "05/27/2026",
            "",
        ]]
        rows = f._datatable_norm_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "H & P Industries, Inc.")

    def test_json_norm_rows_unescape(self):
        rows = f._json_norm_rows([_json_row(9002, company="Nature&#039;s Pharmacy &amp; Co")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Nature's Pharmacy & Co")

    def test_no_entity_survives_to_item(self):
        # 계약: 수집기 산출물(업체명·헤드라인·본문)에 엔티티가 단 하나도 남지 않는다.
        raw = [[
            "04/17/2026", "H &amp; P Industries, Inc.", "1234567",
            '<a href="/media/9003/download">483</a>', "Wisconsin", "",
            "Drug Manufacturer", "05/27/2026", "",
        ]]
        nrows = f._datatable_norm_rows(raw)
        with _Patched(html_rows=[], pdf_text="OBSERVATION 1 aseptic."):
            with patch.object(f, "_fetch_html_rows", lambda start_date=None: (nrows, 1, False)):
                items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it.firm, "H & P Industries, Inc.")
        for field in (it.firm, it.headline, it.body):
            self.assertIsNone(self.ENTITY_RE.search(field),
                              f"엔티티가 산출물에 남았다: {field!r}")


class OrchestrationWiringTest(unittest.TestCase):
    def test_source_token_registered(self):
        self.assertIn("fda483", ci._SOURCE_CHOICES)
        self.assertEqual(ci._SOURCE_TOKEN_TO_NOTION["fda483"], "FDA 483")

    def test_flag_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_FDA_483", None)
            enabled = (os.environ.get("ENABLE_FDA_483", "false").lower() == "true")
        self.assertFalse(enabled)

    def test_observation_flag_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_FDA_483_OBSERVATIONS", None)
            self.assertFalse(f._observations_enabled())

    def test_transient_scope_includes_fda483(self):
        self.assertIn("fda483", ci._GLOBAL_PUBLIC_SOURCE_CODES)
        self.assertTrue(ci._is_transient_source_error("fda483", "HTTP 403 Forbidden"))
        self.assertTrue(ci._is_transient_source_error("fda483", "connection reset"))


if __name__ == "__main__":
    unittest.main()
