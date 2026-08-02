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


class ObservationCrossReferenceTest(unittest.TestCase):
    """본문 속 "Please refer to Observation N" 상호참조가 표제로 오인돼 관찰이 찢기던 결함 가드.

    2026-07-20 fda483-193490(실측): 관찰 1 의 하위항목 a./b./c./d. 사이에 상호참조가 섞여 있어
    앵커가 `1,1,3,4,2,3,4` 로 잡혔다 → ① 관찰 1 이 4조각으로 분해 ② 조각의 deficiency 가 참조문
    끝 마침표뿐인 "." ③ **번호 중복** 탓에 하류 번역 병합(number 키)이 오배치. 발행 게이트가
    브리프 전체를 막아 그 주 발행이 멈췄다.
    """
    # 실측 구조 그대로 축약 — 상호참조 뒤에 `: .` (참조문의 종결 마침표)만 남고 다음 줄부터
    # 하위항목이 이어지는 형태가 핵심이다. 콜론·앞선 빈 줄은 진짜 표제와 **구별되지 않는다**.
    SAMPLE = (
        "I/WE OBSERVED:\n\n"
        "OBSERVATION 1: The responsibilities applicable to the quality control unit "
        "are not fully followed. Specifically, your Quality Unit failed to: "
        "a. Ensure the timely implementation of corrective actions for the settle "
        "plates used for environmental monitoring. Please refer to\n\n"
        "OBSERVATION 1: .\nb. Conduct adequate root-cause analyses for the ongoing "
        "Environmental Monitoring excursions across the classified areas. Please refer to "
        "OBSERVATlON 2 and\n\n"
        "OBSERVATION 3: .\nc. Investigate the root cause of visual inspection failure "
        "during the initial full inspection of the filled syringes. Please refer to\n\n"
        "OBSERVATION 4: .\nd. Evaluate air flow patterns under dynamic conditions in all "
        "ISO 5 classified cabinets in the clean room.\n\n"
        "OBSERVATION 2: Procedures designed to prevent microbiological contamination "
        "of drug products purporting to be sterile are not followed. Specifically, the "
        "environmental monitoring program failed to address repeated contamination.\n\n"
        "OBSERVATION 3: Production personnel were not practicing good sanitation and "
        "health habits. Specifically, your firm failed to maintain adequate controls.\n\n"
        "OBSERVATION 4: There is a failure to thoroughly review any unexplained "
        "discrepancy. Specifically, your firm failed to document an investigation.\n\n"
    )

    def test_cross_references_do_not_split_observations(self):
        rows = f._extract_483_observations_from_text(self.SAMPLE)
        self.assertEqual([r["number"] for r in rows], ["1", "2", "3", "4"])

    def test_numbers_are_unique(self):
        # 중복 번호는 하류 번역 병합(number 키)을 오배치시키므로 계약으로 고정한다.
        nums = [r["number"] for r in f._extract_483_observations_from_text(self.SAMPLE)]
        self.assertEqual(len(nums), len(set(nums)))

    def test_no_punctuation_only_deficiency(self):
        # 찢긴 조각의 표식이던 deficiency="." 가 사라졌는지.
        for row in f._extract_483_observations_from_text(self.SAMPLE):
            self.assertGreater(len(re.sub(r"[^A-Za-z]", "", row["deficiency"])), 10)

    def test_sub_items_stay_with_their_observation(self):
        # 하위항목 a~d 는 관찰 1 본문에 남아야 한다(기각된 참조문과 함께 흡수).
        rows = f._extract_483_observations_from_text(self.SAMPLE)
        body = rows[0]["deficiency"] + " " + rows[0]["detail"]
        for marker in ("a. Ensure", "b. Conduct", "c. Investigate", "d. Evaluate"):
            self.assertIn(marker, body)

    def test_non_sequential_numbering_is_preserved(self):
        """번호가 순차가 아니어도 진짜 표제는 전부 남는다 — fda483-193616 실측(원문에 1 과 3 만).

        "1부터 +1 증가할 때만 표제" 규칙을 쓰면 관찰 3 이 통째로 유실된다. 상호참조는
        번호 순서가 아니라 **뒤따르는 실질 문장의 유무**로 가려야 한다는 근거 테스트.
        """
        text = ("I/WE OBSERVED:\n\n"
                "OBSERVATION 1: The phlebotomy site is not prepared by a method that "
                "gives maximum assurance of a sterile container of blood.\n\n"
                "OBSERVATION 3: Written standard operating procedures including all "
                "steps to be followed in the collection of blood are not maintained.\n\n")
        rows = f._extract_483_observations_from_text(text)
        self.assertEqual([r["number"] for r in rows], ["1", "3"])

    def test_reference_phrase_rejects_even_with_substantive_text(self):
        # 신호② 가 못 잡는 경우(참조 뒤에 실질 문장이 이어짐)는 신호①(앞선 참조 문구)이 잡는다.
        text = ("I/WE OBSERVED:\n\n"
                "OBSERVATION 1: The quality unit failed to follow its procedures. "
                "The firm did not document the deviation. Please refer to "
                "OBSERVATION 2 for the related environmental monitoring findings.\n\n")
        rows = f._extract_483_observations_from_text(text)
        self.assertEqual([r["number"] for r in rows], ["1"])

    def test_ordinary_heading_without_colon_still_accepted(self):
        # 콜론은 판별 신호가 아니다(상호참조에도 붙는다) — 콜론 없는 표제도 정상 인식.
        text = ("I/WE OBSERVED: OBSERVATION 1 Deficiency one concerns inadequate "
                "control of the process. Body one.\n\n"
                "OBSERVATION 2 Deficiency two concerns inadequate cleaning "
                "validation. Body two.\n\n")
        rows = f._extract_483_observations_from_text(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])


class FooterGarbageMarkerTest(unittest.TestCase):
    """서명블록이 OCR 로 완전히 파괴돼도 AMENDMENT 스탬프로 절단되는지(2026-07-20 실측 3종)."""
    # 셋 다 193490 원문 그대로 — 앞 둘은 옛 마커가 전부 실패했다.
    GARBAGE = (
        "AMENDMENT 1 Et,40LOYE£ SIS G•.,-.n,,~ oi:.1e 1ssueo",
        "AMENDMENT 1 EMPLOYEE(S, $1GNA':'UR: OATE ISSUED",
        "AMENDMENTl EJ·.tP!.OYEE{S) Sa'.:;!l.\\'ATI..RE OA\"E SSUED",
    )

    def test_collector_cuts_all_ocr_variants(self):
        for tail in self.GARBAGE:
            chunk = ("The firm failed to evaluate air flow patterns under dynamic "
                     "conditions in the clean room. " + tail)
            cleaned = f._clean_observation_chunk(chunk)
            self.assertNotIn("AMENDMENT", cleaned.upper(), f"미절단: {tail!r}")
            self.assertIn("air flow patterns", cleaned)   # 본문은 보존

    def test_lowercase_amendment_prose_not_cut(self):
        # 소문자 "amendment"(규정 개정 언급)는 산문이므로 절단하지 않는다 — 오탐 가드.
        chunk = ("Your firm did not implement the amendment to the standard operating "
                 "procedure within the required timeframe after approval.")
        self.assertIn("amendment", f._clean_observation_chunk(chunk))


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


# 483 마지막 장 정형 고지문(실측 발췌 — 스캔본 21건이 전부 이 한 장만 텍스트였다).
_NOTICE_ONLY = (
    "The observations of objectionable conditions and practices listed on the front of "
    "this form are reported:\n1. Pursuant to Section 704(b) of the Federal Food, Drug and "
    "Cosmetic Act, or\n2. To assist firms inspected in complying with the Acts and "
    "regulations enforced by the Food and Drug Administration."
)


class ScannedPdfOcrFallbackTest(unittest.TestCase):
    """[스캔 483 OCR 2026-07-27] 뒷장 고지문만 있는 스캔본을 '정상 텍스트 PDF' 로 오분류하던
    구멍과 그 OCR 폴백. 이 오분류가 2026-07-27 디제스트 오발행("원문이 제공되지 않아")의
    직접 원인이었다 — 원문에는 관찰이 스캔 이미지로 멀쩡히 들어 있었다."""

    def test_notice_only_text_is_not_body(self):
        self.assertTrue(f._is_notice_only(_NOTICE_ONLY))

    def test_real_observation_text_is_body(self):
        real = _NOTICE_ONLY + "\nOBSERVATION 1\nAseptic processing was deficient."
        self.assertFalse(f._is_notice_only(real))

    def test_we_observed_variant_is_body(self):
        self.assertFalse(f._is_notice_only(
            "The observations of objectionable conditions ... WE OBSERVED the operator"))

    def test_needs_ocr_on_empty_and_notice_only(self):
        with patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
            self.assertTrue(f._needs_ocr(""))
            self.assertTrue(f._needs_ocr(_NOTICE_ONLY))
            self.assertFalse(f._needs_ocr("OBSERVATION 1 — something real"))

    def test_ocr_disabled_never_triggers(self):
        with patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "false"}):
            self.assertFalse(f._needs_ocr(""))
            self.assertFalse(f._needs_ocr(_NOTICE_ONLY))

    def test_ocr_flag_defaults_on(self):
        """다른 483 플래그와 달리 **기본 on** — off 면 FOIA 483 대다수가 계속 결손이다."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_FDA_483_OCR", None)
            self.assertTrue(f._ocr_enabled())

    def test_notice_only_pdf_falls_back_to_ocr_and_replaces_status(self):
        """고지문만 있는 PDF → OCR 산출로 교체되고 status 가 `pdf-ok-ocr` 가 된다."""
        recovered = "OBSERVATION 1\nSterility assurance was not established."
        with patch.object(f, "http_get_bytes", lambda *a, **k: b"%PDF-1.7 scan"), \
                patch.object(g, "_extract_pdf_text",
                             lambda data, max_chars=None: (_NOTICE_ONLY, "pdf-ok")), \
                patch.object(f, "_ocr_483_pdf_text",
                             lambda data, max_chars=None: (recovered, "pdf-ok-ocr")), \
                patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
            text, status = f._fetch_fda483_pdf_text("https://x/media/1/download")
        self.assertEqual(status, "pdf-ok-ocr")
        self.assertIn("OBSERVATION 1", text)

    def test_ocr_failure_on_notice_only_reports_our_side_reason(self):
        """OCR 이 못 살리면 고지문을 본문으로 내보내지 않는다 — 사유는 우리 쪽 실패 코드.

        고지문을 그대로 넘기면 하류가 '텍스트 확보'로 오인해 관찰 0건을 소스 탓으로 돌린다.
        """
        with patch.object(f, "http_get_bytes", lambda *a, **k: b"%PDF-1.7 scan"), \
                patch.object(g, "_extract_pdf_text",
                             lambda data, max_chars=None: (_NOTICE_ONLY, "pdf-ok")), \
                patch.object(f, "_ocr_483_pdf_text",
                             lambda data, max_chars=None: ("", "scan-ocr-unavailable:x")), \
                patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
            text, status = f._fetch_fda483_pdf_text("https://x/media/1/download")
        self.assertEqual(text, "")
        self.assertTrue(status.startswith("scan-ocr-unavailable"))

    def test_real_text_pdf_never_touches_ocr(self):
        """텍스트층이 온전한 483 은 OCR 경로에 들어가지 않는다(오인식 덧씌움 금지)."""
        def _boom(*a, **k):
            raise AssertionError("OCR 이 호출되면 안 된다")
        with patch.object(f, "http_get_bytes", lambda *a, **k: b"%PDF-1.7 real"), \
                patch.object(g, "_extract_pdf_text",
                             lambda data, max_chars=None: ("OBSERVATION 1 real", "pdf-ok")), \
                patch.object(f, "_ocr_483_pdf_text", _boom), \
                patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
            text, status = f._fetch_fda483_pdf_text("https://x/media/1/download")
        self.assertEqual(status, "pdf-ok")
        self.assertEqual(text, "OBSERVATION 1 real")

    def test_ocr_engine_missing_degrades_gracefully(self):
        """PyMuPDF/tesseract 부재는 수집을 멈추지 않는다 — 사유만 남는다."""
        text, status = f._ocr_483_pdf_text(b"not a pdf at all")
        self.assertEqual(text, "")
        self.assertTrue(status.startswith(("scan-ocr-unavailable", "pdf-parse-fail")),
                        f"예상 밖 status: {status!r}")

    def test_absent_reason_labels_cover_new_statuses(self):
        """새 상태코드가 사람이 읽는 사유로 반드시 번역된다(부재 어휘 규율)."""
        import card_scaffold as cs
        for code in ("scan-ocr-unavailable", "scan-ocr-empty", "scan-ocr-budget",
                     "pdf-encrypted"):
            self.assertIn(code, cs._ABSENT_REASON_LABELS)
            self.assertTrue(cs._absent_reason({"fda483_text_status": f"{code}:detail"}))

    def test_ocr_page_budget_stops_further_ocr(self):
        """실행당 OCR 페이지 예산이 소진되면 더 이상 OCR 하지 않고 사유를 남긴다."""
        saved = dict(f._OCR_BUDGET)
        try:
            f._OCR_BUDGET["remaining"] = 0
            text, status = f._ocr_483_pdf_text(b"%PDF-1.7 anything")
            self.assertEqual((text, status), ("", "scan-ocr-budget"))
        finally:
            f._OCR_BUDGET.update(saved)


class OcrEngineObservabilityTest(unittest.TestCase):
    """[침묵 제거 2026-07-30] 엔진 부재를 **실행 단위로** 센다.

    종전에는 `_ocr_483_pdf_text` 가 status 문자열만 돌려주고 끝났다 — 그 문자열은
    raw_payload 에 묻히고, 워크플로는 초록이고, health 경보에도 없었다. 엔진 부재는 문서
    한 건의 사정이 아니라 **런타임 전체의 사정**이고, 환경을 고치면 되찾을 수 있는 유일한
    결손 종류다. 그래서 `scan-no-text`·`scan-ocr-empty`·`scan-ocr-budget` 와 갈라 센다.
    """

    def setUp(self):
        f.reset_ocr_health()

    def tearDown(self):
        f.reset_ocr_health()

    def test_engine_unavailable_is_discriminated_from_other_absences(self):
        self.assertTrue(f.is_ocr_engine_unavailable("scan-ocr-unavailable:pymupdf"))
        self.assertTrue(f.is_ocr_engine_unavailable(
            "scan-ocr-unavailable:No tessdata specified and Tesseract is not installed"))
        for other in ("scan-no-text", "scan-ocr-empty", "scan-ocr-budget", "pdf-ok",
                      "pdf-ok-ocr", "fetch-fail:timeout", "", None):
            self.assertFalse(f.is_ocr_engine_unavailable(other), f"오분류: {other!r}")

    def test_engine_failure_is_counted_with_reason(self):
        """모든 반환 경로가 계수 래퍼를 지난다 — 여기서 세지 않으면 아무도 모른다."""
        with patch.object(f, "_ocr_483_pdf_text_uncounted",
                          lambda data, max_chars: ("", "scan-ocr-unavailable:no tessdata")):
            f._ocr_483_pdf_text(b"%PDF-1.7 scan")
            f._ocr_483_pdf_text(b"%PDF-1.7 scan")
        health = f.ocr_health()
        self.assertEqual(health["engine_unavailable"], 2)
        self.assertEqual(health["engine_reason"], "scan-ocr-unavailable:no tessdata")
        self.assertEqual(health["ok"], 0)

    def test_success_and_budget_are_counted_separately(self):
        with patch.object(f, "_ocr_483_pdf_text_uncounted",
                          lambda data, max_chars: ("OBSERVATION 1 x", "pdf-ok-ocr")):
            f._ocr_483_pdf_text(b"%PDF-1.7 scan")
        with patch.object(f, "_ocr_483_pdf_text_uncounted",
                          lambda data, max_chars: ("", "scan-ocr-budget")):
            f._ocr_483_pdf_text(b"%PDF-1.7 scan")
        health = f.ocr_health()
        self.assertEqual(health["ok"], 1)
        self.assertEqual(health["budget_skipped"], 1)
        self.assertEqual(health["engine_unavailable"], 0)

    def test_collect_run_resets_counters(self):
        """실행별 리셋 — 이전 실행의 부재가 다음 실행 경보로 새면 안 된다."""
        with patch.object(f, "_ocr_483_pdf_text_uncounted",
                          lambda data, max_chars: ("", "scan-ocr-unavailable:x")):
            f._ocr_483_pdf_text(b"%PDF-1.7 scan")
        self.assertEqual(f.ocr_health()["engine_unavailable"], 1)
        rows = FetchCoverageTest._norm_rows(1)
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "false",
                                     "ENABLE_FDA_483_DEEP": "false"}), \
                patch.object(f, "FDA483_EXCERPT_DELAY_SECONDS", 0), \
                patch.object(f, "_fetch_fda483_pdf_text",
                             lambda url: ("OBSERVATION 1 x", "pdf-ok")), \
                patch.object(f, "_fetch_html_rows",
                             lambda start_date=None: (list(rows), 1, False)):
            f.collect_fda_483(START, END)
        self.assertEqual(f.LAST_HEALTH["fda_483_ocr"]["engine_unavailable"], 0)

    def test_last_health_carries_engine_counters(self):
        """LAST_HEALTH 가 엔진 카운터를 실어야 collect_intake→grm_health 로 이어진다."""
        rows = FetchCoverageTest._norm_rows(2)
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "false",
                                     "ENABLE_FDA_483_DEEP": "false",
                                     "ENABLE_FDA_483_OCR": "true"}), \
                patch.object(f, "FDA483_EXCERPT_DELAY_SECONDS", 0), \
                patch.object(f, "_ocr_483_pdf_text_uncounted",
                             lambda data, max_chars: ("", "scan-ocr-unavailable:no tessdata")), \
                patch.object(f, "http_get_bytes", lambda *a, **k: b"%PDF-1.7 scan"), \
                patch.object(g, "_extract_pdf_text",
                             lambda data, max_chars=None: ("", "scan-no-text")), \
                patch.object(f, "_fetch_html_rows",
                             lambda start_date=None: (list(rows), 2, False)):
            f.collect_fda_483(START, END)
        ocr = f.LAST_HEALTH["fda_483_ocr"]
        self.assertEqual(ocr["engine_unavailable"], 2)
        self.assertIn("tessdata", ocr["engine_reason"])


class FetchCoverageTest(unittest.TestCase):
    """[수집 사각 2026-07-27] PDF 상한 밖 문서가 **몇 건 통째로 빠졌는지** 세고 표면화한다.

    종전 상한 40 은 윈도우 후보(실측 108)의 1/3 이었고, `and not capped` 단락 평가 때문에
    상한 도달 후에는 분기 자체를 안 타서 미시도 건수를 아무도 몰랐다. 그렇게 빠진 문서가
    "원문 없음" 카드로 발행됐다(2026-07-27 소급 복구 24건 중 10건).
    """

    def test_default_cap_covers_observed_window_volume(self):
        """기본 상한이 실측 윈도우 후보 수에 비해 터무니없이 작지 않아야 한다."""
        self.assertGreaterEqual(f.FDA483_EXCERPT_MAX_ITEMS, 60)

    def test_cap_and_budget_are_env_tunable(self):
        import importlib
        with patch.dict(os.environ, {"FDA483_PDF_MAX_ITEMS": "7",
                                     "FDA483_OCR_PAGE_BUDGET": "11"}):
            mod = importlib.reload(f)
            self.assertEqual(mod.FDA483_EXCERPT_MAX_ITEMS, 7)
            self.assertEqual(mod.FDA483_OCR_PAGE_BUDGET, 11)
        importlib.reload(f)      # 원복 — 다른 테스트에 새지 않게
        self.assertGreaterEqual(f.FDA483_EXCERPT_MAX_ITEMS, 60)

    @staticmethod
    def _norm_rows(n):
        """정규화 행 n개(_fetch_html_rows 산출 형태) — media id 고유·전건 윈도우 내."""
        raw = [["04/17/2026", f"Firm {i}", f"100000{i}",
                f'<a href="/media/{9100 + i}/download">483</a>', "Wisconsin", "",
                "Drug Manufacturer", "05/27/2026", ""] for i in range(n)]
        return f._datatable_norm_rows(raw)

    def test_health_reports_every_skipped_document(self):
        """상한 밖 문서가 전건 집계된다 — 1건만 세고 마는 종전 단락 평가 회귀 차단."""
        rows = self._norm_rows(6)
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "false",
                                     "ENABLE_FDA_483_DEEP": "false",
                                     "ENABLE_FDA_483_OCR": "false"}), \
                patch.object(f, "FDA483_EXCERPT_MAX_ITEMS", 2), \
                patch.object(f, "FDA483_EXCERPT_DELAY_SECONDS", 0), \
                patch.object(f, "_fetch_fda483_pdf_text",
                             lambda url: ("OBSERVATION 1 x", "pdf-ok")), \
                patch.object(f, "_fetch_html_rows",
                             lambda start_date=None: (list(rows), len(rows), False)):
            f.collect_fda_483(START, END)
        health = f.LAST_HEALTH["fda483_excerpt"]
        self.assertTrue(health["capped"])
        self.assertEqual(health["attempted"], 2)
        self.assertEqual(health["skipped_no_attempt"], 4,
                         "상한 밖 문서가 전건 집계되지 않았다")

    def test_health_exposes_ocr_budget_usage(self):
        rows = self._norm_rows(1)
        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "false",
                                     "ENABLE_FDA_483_DEEP": "false"}), \
                patch.object(f, "FDA483_EXCERPT_DELAY_SECONDS", 0), \
                patch.object(f, "_fetch_fda483_pdf_text",
                             lambda url: ("OBSERVATION 1 x", "pdf-ok")), \
                patch.object(f, "_fetch_html_rows",
                             lambda start_date=None: (list(rows), 1, False)):
            f.collect_fda_483(START, END)
        ocr = f.LAST_HEALTH["fda_483_ocr"]
        self.assertEqual(ocr["pages_used"], 0)
        self.assertFalse(ocr["exhausted"])
        self.assertEqual(ocr["budget"], f.FDA483_OCR_PAGE_BUDGET)
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["skipped_no_attempt"], 0)


if __name__ == "__main__":
    unittest.main()


class ObservationLegibilityTest(unittest.TestCase):
    """[OCR 판독 잡음 2026-07-27] 스캔 여백 파편이 관찰 1건으로 발행되던 구멍.

    실측: `fda483-193759` obs 6 의 표제가 `"/T"` 였다. 표제는 문장이지 기호가 아니다 —
    OCR 폴백이 들어오면서 페이지 여백 잡음이 `OBSERVATION n` 앵커 뒤에 걸린 결과다.
    """

    def test_symbol_fragment_is_not_a_deficiency(self):
        for junk in ("/T", "‘T", ".", "|", "X", "a b"):
            self.assertFalse(f._is_legible_deficiency(junk), junk)

    def test_real_heading_is_legible(self):
        self.assertTrue(f._is_legible_deficiency(
            "Aseptic processing areas are deficient."))

    def test_parser_drops_noise_observation(self):
        text = ("WE OBSERVED\n"
                "OBSERVATION 1\nAseptic processing operations were deficient.\n"
                "Specifically, first air was blocked.\n"
                "OBSERVATION 2\n/T\n"
                "OBSERVATION 3\nEnvironmental monitoring was inadequate.\n"
                "Specifically, excursions were not investigated.")
        nums = [o["number"] for o in f._extract_483_observations_from_text(text)]
        self.assertEqual(nums, ["1", "3"], "잡음 관찰이 걸러지지 않았다")

    def test_scaffold_applies_the_same_bar(self):
        """낡은 raw 를 든 스캐폴드도 같은 기준 — 파서만 고치면 굳은 스캐폴드는 못 고친다."""
        import card_scaffold as cs
        raw = {"fda_483_observations": [
            {"number": "1", "deficiency": "Aseptic processing was deficient.", "detail": ""},
            {"number": "2", "deficiency": "/T", "detail": ""}]}
        dd = cs._detail_fda_483_observations({}, raw)
        self.assertEqual(dd["count"], 1)
        self.assertEqual([o["number"] for o in dd["observations"]], ["1"])


class FooterMarkerParityTest(unittest.TestCase):
    """[2026-07-27] 수집기 절단 마커와 발행 게이트 마커의 비대칭 = 발행 직전 차단.

    실측 2건으로 동시에 드러났다:
      · `fda483-193759` obs#8 — detail 끝에 "! a Mae SIGNATURE |" 잔재. 게이트에는
        SIGNATURE 마커가 있는데 수집기에는 없어, 못 자른 채 발행 단계에서 브리프 전체 차단.
      · `fda483-192342` obs#5 — "Investigator Piechocki noted…" 라는 **관찰 산문**을
        게이트가 푸터로 오인. 서명블록은 `<이름>, Investigator` 어순이라 쉼표가 앞에 온다.
    """

    def test_collector_cuts_signature_residue(self):
        detail = ("Specifically, the reference material has not been calibrated. "
                  "! a Mae SIGNATURE |")
        self.assertNotIn("SIGNATURE", f._clean_observation_detail(detail))

    def test_collector_keeps_investigator_prose(self):
        detail = ("Specifically, Investigator Piechocki noted materials came off "
                  "loose along the frames in the Grade A filling room ceiling.")
        self.assertIn("Piechocki", f._clean_observation_detail(detail))

    def test_collector_cuts_signature_block_title(self):
        detail = ("Specifically, the operator blocked first air during filling. "
                  "Juanelma H Palmer, Investigator")
        out = f._clean_observation_detail(detail)
        self.assertIn("first air", out)
        self.assertNotIn("Investigator", out)

    def test_gate_agrees_with_collector(self):
        """수집기가 통과시킨 detail 은 게이트도 통과해야 한다(비대칭 0)."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "web"))
        import render
        for raw in (
            "Specifically, Investigator Piechocki noted materials came off loose.",
            "Specifically, the reference material has not been calibrated. ! a Mae SIGNATURE |",
            "Specifically, the operator blocked first air. Juanelma H Palmer, Investigator",
        ):
            cleaned = f._clean_observation_detail(raw)
            if not cleaned:
                continue
            card = {"id": "x", "deterministic_detail": {
                "type": "fda_483_observations", "count": 1,
                "observations": [{"number": "1", "deficiency": "Aseptic failure observed.",
                                  "deficiency_ko": "무균 실패.", "detail": cleaned,
                                  "detail_ko": "국문."}]}}
            self.assertEqual(render.validate_483_observations([card]), [],
                             f"수집기 통과분을 게이트가 막았다: {cleaned!r}")


class AnnotationsSectionTest(unittest.TestCase):
    """[2026-07-27] "Annotations to Observations" 절이 관찰로 파싱돼 번호가 중복되던 결함.

    483 양식의 그 절은 관찰이 아니라 **어느 관찰을 시정하기로 했는지에 대한 주석**이고,
    그 안에서 관찰 번호가 다시 열거된다("8. Promised to correct." — fda483-193541 실측).
    같은 번호가 두 번 만들어지면 국문 병기가 번호로 매칭되므로 번역이 주석 쪽에만 붙고
    진짜 관찰은 미번역으로 남아 **발행이 막힌다**.
    """

    BODY = ("WE OBSERVED\n"
            "OBSERVATION 8\nPest activity was observed throughout the warehouse.\n"
            "Specifically, rodent excreta pellets were found on pallets.\n"
            "Annotations to Observations\n"
            "OBSERVATION 8\nPromised to correct.\n")

    def test_annotations_section_is_not_an_observation(self):
        rows = f._extract_483_observations_from_text(self.BODY)
        self.assertEqual([r["number"] for r in rows], ["8"], "번호가 중복 생성됐다")
        self.assertIn("Pest activity", rows[0]["deficiency"])

    def test_detail_does_not_leak_the_annotations_heading(self):
        rows = f._extract_483_observations_from_text(self.BODY)
        self.assertNotIn("Annotations", rows[0]["detail"])
        self.assertNotIn("Promised to correct", rows[0]["detail"])

    def test_normal_body_unaffected(self):
        body = ("WE OBSERVED\nOBSERVATION 1\nAseptic processing was deficient.\n"
                "Specifically, first air was blocked.\n"
                "OBSERVATION 2\nEnvironmental monitoring was inadequate.\n"
                "Specifically, excursions were not investigated.")
        self.assertEqual(
            [r["number"] for r in f._extract_483_observations_from_text(body)], ["1", "2"])


class InspectorExtractionTest(unittest.TestCase):
    """[실사관 추출 2026-07-30] `_extract_483_inspectors` — 프로덕션 DB 실측 원문 변형(A~F)
    + 오탐/거부 픽스처(G~L). 정밀도 최우선 — 확증 없으면 형태가 맞아도 버린다."""

    def test_a_two_signature_blocks_same_page(self):
        text = ("OYEE(S) SIGNATURE\nDATE ISSUED\nSEE REVERSE| Jose F Velez,\n"
                "Investigator\n2/27/2026\nOF THIS PAGE |} Ivis L Negron,\n"
                "Investigator\n2/27/2026")
        self.assertEqual(f._extract_483_inspectors(text), ["Jose F Velez", "Ivis L Negron"])

    def test_b_single_signature_block(self):
        text = ("S) SIGNATURE\nDATE ISSUED\nSEE REVERSE | Jolanna A Norton,\n"
                "Investigator\n3/6/2026\nOF THIS PAGE")
        self.assertEqual(f._extract_483_inspectors(text), ["Jolanna A Norton"])

    def test_c_middle_initial_with_period(self):
        text = ("a L. Flores - piavaly signed by bisa.\nSEE\nLisa L. Flores, Investigator\n"
                "S\nDoes siz10 140022 | 12/10/2025\nREV")
        self.assertEqual(f._extract_483_inspectors(text), ["Lisa L. Flores"])

    def test_d_employee_marker_same_line(self):
        text = (" OF THIS PAGE\nEMPLOYEE(S) SIGNATURE Christina K Theodorou, Investigator\n"
                "DATE ISSUED 6/18/2026")
        self.assertEqual(f._extract_483_inspectors(text), ["Christina K Theodorou"])

    def test_e_four_token_name_trailing_form_marker(self):
        text = ("DATE ISSUED\nSEE REVERSE\nOF THIS PAGE\nLesley Mae P Lutao, Investigator\n\n"
                "X\n9/9/2025\n\nFORM FDA 483 (09/08)")
        self.assertEqual(f._extract_483_inspectors(text), ["Lesley Mae P Lutao"])

    def test_f_ocr_truncated_employee_marker(self):
        text = ("MPLOYEE(S) SIGNATURE\nDATE ISSUED\nSEE REVERSE| Yaharn Su,\n"
                "Investigator\n8/14/2025\nOF THIS PAGE")
        self.assertEqual(f._extract_483_inspectors(text), ["Yaharn Su"])

    def test_g_prose_false_positive_title_before_name_rejected(self):
        # 직함이 앞, 이름이 뒤 — 서명블록과 정반대 어순. 절대 잡히면 안 된다.
        text = ("Specifically, Investigator Piechocki noted materials came off loose "
                "during the inspection of the filling line on 3/4/2026.")
        self.assertEqual(f._extract_483_inspectors(text), [])

    def test_h_ocr_corrupted_name_rejected(self):
        text = ("\n05/29/2026\nSEE REVERSE\nOigi1.aUy i,.gned by AnnetRa,an\n"
                "Investigator\nOF THIS PAGE")
        self.assertEqual(f._extract_483_inspectors(text), [])

    def test_i_repeated_footer_deduplicated(self):
        block = ("SEE REVERSE| DATE ISSUED\nMaria T Gomez,\nInvestigator\n1/2/2026\n"
                  "OF THIS PAGE\n")
        text = block * 3
        self.assertEqual(f._extract_483_inspectors(text), ["Maria T Gomez"])

    def test_j_no_confirmation_rejected(self):
        # 형태(이름, 직함)는 맞지만 날짜도 서명블록 마커도 근처에 없다 — 확증 부재로 거부.
        text = "Report prepared by John A Smith, Investigator for internal circulation."
        self.assertEqual(f._extract_483_inspectors(text), [])

    def test_k_title_allowlist_consumer_safety_officer(self):
        text = ("SEE REVERSE| Priya K Anand,\nConsumer Safety Officer\n4/4/2026\n"
                "OF THIS PAGE")
        self.assertEqual(f._extract_483_inspectors(text), ["Priya K Anand"])

    def test_k_title_allowlist_microbiologist(self):
        text = ("SEE REVERSE| David R Chen,\nMicrobiologist\n6/6/2026\nOF THIS PAGE")
        self.assertEqual(f._extract_483_inspectors(text), ["David R Chen"])

    def test_k_title_allowlist_chemist_and_analyst(self):
        chemist = "SEE REVERSE| Wendy O Park,\nChemist\n7/7/2026\nOF THIS PAGE"
        analyst = "SEE REVERSE| Tomas B Reyes,\nAnalyst\n8/8/2026\nOF THIS PAGE"
        self.assertEqual(f._extract_483_inspectors(chemist), ["Wendy O Park"])
        self.assertEqual(f._extract_483_inspectors(analyst), ["Tomas B Reyes"])

    def test_k_title_allowlist_biologist_and_fda_center_employee(self):
        # [2026-07-30 교정] EMPLOYEE(S) SIGNATURE 서명자는 전원 그 실사의 FDA 인력 —
        # "FDA Center Employee" 서명자를 빠뜨리면 불완전한 기록이 된다.
        biologist = "SEE REVERSE| DATE ISSUED\nOtis N Vega,\nBiologist\n9/9/2026\nOF THIS PAGE"
        fda_emp = "SEE REVERSE| DATE ISSUED\nSarah E Venti,\nFDA Center Employee\n9/9/2026\n"
        self.assertEqual(f._extract_483_inspectors(biologist), ["Otis N Vega"])
        self.assertEqual(f._extract_483_inspectors(fda_emp), ["Sarah E Venti"])

    # ── [2026-07-30 교정 M~P] 프로덕션 재실측 다중 서명자 원문 — 코디네이터 보정 지시 ──────
    # 공통 함정: 두 번째 이후 서명자는 직함 뒤 날짜가 없거나 OCR 로 깨져(`0227-2026` 등
    # 슬래시 없음) 확증 규칙 (a)가 못 잡는다 — 오직 (b)(같은 블록의 서명 마커, 200자
    # 룩비하인드)만으로 구제돼야 한다. 이게 이번 교정의 핵심 검증 지점이다.

    def test_m_hispanic_surname_two_words_and_slash_compound_title(self):
        text = ("ical products. Filme ProneSid EMPLOYEE(S) SIGNATURE DATE ISSUED "
                "SEE REVERSE| Jose F Velez, Investigator 2/27/2026 OF THIS PAGE |} "
                "Ivis L Negron Torres, Chemist/Biologist a : 2000547088 xX wwe "
                "0227-2026 FOOD AND DRUGADMINISTRATION")
        self.assertEqual(f._extract_483_inspectors(text),
                          ["Jose F Velez", "Ivis L Negron Torres"])

    def test_n_three_signers_including_fda_center_employee(self):
        text = ("ainst the CoC specifications. EMPLOYEE(S) SIGNATURE DATE ISSUED "
                "SEE REVERSE | Demario L Walls, Investigator 3/20/2026 OF THIS PAGE | "
                "Nelson N Ayangho, Investigator Sarah E Venti, FDA Center Employee Xx "
                "FOOD AND DRUG ADMINISTRATION")
        self.assertEqual(f._extract_483_inspectors(text),
                          ["Demario L Walls", "Nelson N Ayangho", "Sarah E Venti"])

    def test_o_second_signer_no_date_at_all(self):
        text = ("this equipment in the last 12 EMPLOYEE(S) SIGNATURE DATE ISSUED "
                "SEE REVERSE | Pearl C Ozuruigbo, Investigator 3/18/2026 OF THIS PAGE | "
                "Tareq W Haddad, Investigator Bee cw wacom x Bate pect 1-5 "
                "DEPARTMENT OF HEALTH AND HUMAN SERVICES")
        self.assertEqual(f._extract_483_inspectors(text),
                          ["Pearl C Ozuruigbo", "Tareq W Haddad"])

    def test_p_second_signer_ocr_broken_hyphen_date(self):
        text = (" was not extended to evaluate EMPLOYEE(S) SIGNATURE DATE ISSUED "
                "SEE REVERSE | Anthony J Donato, Investigator 4/17/2026 OF THIS PAGE | "
                "Daniel T Lee, Investigator fap Date Sigrod 0417-2026 x 1e3600 "
                "FOOD AND DRUG ADMINISTRATION")
        self.assertEqual(f._extract_483_inspectors(text),
                          ["Anthony J Donato", "Daniel T Lee"])

    def test_valid_inspector_name_allows_two_word_surname(self):
        # 교정 1 확인 — "2~4개 토큰"은 유지되고, 4번째 토큰(복성)까지 허용된다.
        self.assertTrue(f._valid_inspector_name("Ivis L Negron Torres"))

    def test_l_empty_and_whitespace_input(self):
        self.assertEqual(f._extract_483_inspectors(""), [])
        self.assertEqual(f._extract_483_inspectors("   \n\t  "), [])

    def test_no_exception_on_none_like_input(self):
        # 타입힌트는 str 이지만 상류 방어를 한 겹 더 둔다 — None 이 들어와도 예외 없이 [].
        self.assertEqual(f._extract_483_inspectors(None), [])

    def test_cap_at_six(self):
        # ★이름은 서로 **충분히 달라야** 한다 — 문서 내 합의 게이트(_inspector_names_are_
        #   consistent)는 거의 같은 이름이 두 철자로 나오면 그 문서를 통째로 버리므로,
        #   기계적으로 접미사만 바꾼 합성 이름(PersonA…/PersonB…)을 쓰면 게이트가 발화해
        #   상한이 아니라 게이트를 시험하게 된다. 서로 무관한 실명형 8개를 쓴다.
        names = ["Jose F Velez", "Amy A Johnson", "Timothy H Vo", "Pearl C Ozuruigbo",
                 "Lisa R Hilliard", "Yaharn Su", "Cynthia J Tsui", "Demario L Walls"]
        text = "".join(
            f"SEE REVERSE| DATE ISSUED\n{name},\nInvestigator\n{i % 9 + 1}/1/2026\n"
            "OF THIS PAGE\n"
            for i, name in enumerate(names)
        )
        result = f._extract_483_inspectors(text)
        self.assertEqual(len(result), 6)
        self.assertEqual(result, names[:6])

    def test_valid_inspector_name_rejects_form_vocab_and_single_token(self):
        self.assertFalse(f._valid_inspector_name("Of This Page"))
        self.assertFalse(f._valid_inspector_name("Solo"))
        self.assertTrue(f._valid_inspector_name("Jose F Velez"))

    def test_space_before_comma_is_tolerated(self):
        # [2026-07-30 프로덕션 실측] 스캔 OCR 이 쉼표를 이름에서 한 칸 떼어놓는 변형이
        # 38문서 존재한다(정상 쉼표 421문서 대비 ~9%). Catalent 실측 원문 기준.
        # ★같은 블록의 "Joohi Castelvetere , Investigat or" 는 **직함 자체가 OCR 로 깨져**
        #  ("Investigat or") 여전히 누락되는 게 정답이다 — 정밀도 우선 계약상 추측 복원은
        #  하지 않는다. 이 테스트는 그 경계를 함께 고정한다.
        text = ("EM\"'-OYEE(S) SIGNATURE SEE Joohi Castelvetere , Investigat or 04/24/2026 "
                "REVERSE OF Robert J Ham, Investigator THIS PAGE "
                "Brandy N LePage , Investigator FORM FDA 413")
        self.assertEqual(f._extract_483_inspectors(text),
                         ["Robert J Ham", "Brandy N LePage"])

    def test_space_before_comma_does_not_admit_form_vocab(self):
        # 공백-쉼표 허용이 양식 어휘를 이름으로 승격시키지 않는지(오탐 회귀 가드).
        text = "EMPLOYEE(S) SIGNATURE DATE ISSUED SEE REVERSE OF THIS PAGE , Investigator 3/5/2026"
        self.assertEqual(f._extract_483_inspectors(text), [])

    # ── OCR 신뢰도 게이트 [2026-07-30 백필 실측 결함] ─────────────────────────
    def test_gate1_rejects_ocr_case_confusion_tokens(self):
        """토큰 중간 대문자는 OCR 대소문자 혼동의 흔적(실측: JUetlne·HUrpny·BiswaS)."""
        for tok in ("JUetlne", "HUrpny", "BiswaS", "HeItmeier"):
            self.assertFalse(f._inspector_token_shape_ok(tok), tok)

    def test_gate1_allows_real_internal_caps(self):
        """실존 이름의 내부 대문자는 Mc/Mac/Le/De/O'/D' 접두나 하이픈·어퍼스트로피 뒤에만."""
        for tok in ("McDonald", "MacLeod", "LePage", "DeSilva", "O'Brien", "D'Angelo",
                    "Wilimczyk-Macri", "LaBounty", "DeJesus", "DiCarlo", "VanBuren",
                    "Velez", "Hernandez"):
            self.assertTrue(f._inspector_token_shape_ok(tok), tok)

    def test_gate1_prefix_exemption_is_per_position_not_whole_token(self):
        """★[2026-07-30 프로덕션 감사 실측 구멍] 접두 면제를 **토큰 전체**에 주면
        `DemitTia`(De 로 시작) 같은 OCR 오인식이 그대로 통과한다 — 실제로 프로덕션에
        `DemitTia J. Argiropoulos` 가 적재됐다. 면제는 **대문자가 나온 그 자리**에만
        주고(접두가 정확히 거기서 끝날 때), 대소문자를 구분해야 한다."""
        self.assertFalse(f._inspector_token_shape_ok("DemitTia"))
        self.assertFalse(f._inspector_token_shape_ok("LesLie"))
        self.assertFalse(f._inspector_token_shape_ok("MacDoNald"))
        # 접두로 시작하지만 내부 대문자가 없는 평범한 이름은 정상 통과해야 한다.
        for tok in ("Demitria", "Denise", "Leslie", "Lauren", "Macey", "Devon", "Larry"):
            self.assertTrue(f._inspector_token_shape_ok(tok), tok)

    def test_gate1_rejects_bare_initial_as_given_name(self):
        """첫 토큰이 홑이니셜이면 이름이 잘려나간 조각(실측: I. Gaul·P. Cintron·A. Rusin).
        483 서명블록은 항상 이름을 온전히 적으므로 이건 조각이 맞다."""
        for name in ("I. Gaul", "P. Cintron", "A. Rusin", "H. Hunt"):
            self.assertFalse(f._valid_inspector_name(name), name)
        for name in ("Jose F Velez", "Eileen A. Liu", "Ivis L Negron Torres"):
            self.assertTrue(f._valid_inspector_name(name), name)

    def test_gate2_rejects_document_with_contradictory_spellings(self):
        """같은 이름이 두 철자로 읽힌 문서는 통째로 버린다 — 어느 쪽이 옳은지 알 수 없다.
        실측 원문(Immacule): Damaris Y. Hernandez / Damaris Y. Hemandez (rn→m 오인식)."""
        self.assertFalse(f._inspector_names_are_consistent(
            ["Damaris Y. Hernandez", "Angelica M. Hernandez", "Damaris Y. Hemandez"]))
        self.assertFalse(f._inspector_names_are_consistent(
            ["Unnee Ranjan", "Lata Mathew", "Onnee Ranjan"]))

    def test_gate2_allows_distinct_people_with_shared_surname(self):
        """성이 같은 서로 다른 사람은 병합·거부되지 않아야 한다(오탐 방지)."""
        self.assertTrue(f._inspector_names_are_consistent(
            ["Damaris Y. Hernandez", "Angelica M. Hernandez"]))
        self.assertTrue(f._inspector_names_are_consistent(
            ["Jose F Velez", "Ivis L Negron Torres", "Sarah E Venti"]))

    def test_gates_reject_whole_document_end_to_end(self):
        """실측 불량 원문(Delta Pharma 계열)이 최종 산출에서 통째로 비는지 — 한 사람이
        세 철자로 읽힌 문서다. 틀린 실명 노출보다 빈 결과가 정답이다."""
        text = ("EMPLOYEE(S) SIGNATURE DATE ISSUED SEE REVERSE "
                "Brandon C. Hcitmcier, Investigator 1/17/2024 OF THIS PAGE "
                "Brandon C. Heitrueier, Investigator 1/17/2024 "
                "Brandon C. Heianeier, Investigator 1/17/2024")
        self.assertEqual(f._extract_483_inspectors(text), [])

    def test_gates_preserve_clean_multi_signer_document(self):
        """게이트 추가가 정상 다중 서명자 문서를 깨뜨리지 않는지(회귀)."""
        text = ("EMPLOYEE(S) SIGNATURE DATE ISSUED SEE REVERSE | Demario L Walls, "
                "Investigator 3/20/2026 OF THIS PAGE | Nelson N Ayangho, Investigator "
                "Sarah E Venti, FDA Center Employee Xx")
        self.assertEqual(f._extract_483_inspectors(text),
                         ["Demario L Walls", "Nelson N Ayangho", "Sarah E Venti"])

    def test_dedupe_absorbs_period_variants_and_trailing_fragments(self):
        # [2026-07-30 백필 dry-run 실측] 한 문서에서 실제로 나온 목록. 3명인데 6명으로
        # 보였다 — 마침표 변형(Barbara A. Rusin / Barbara A Rusin)과 전자서명 레이어가
        # 남긴 뒤쪽 조각(A. Rusin / D. Fowlkes)이 섞인 탓.
        self.assertEqual(
            f._dedupe_inspector_names([
                "Barbara A. Rusin", "L'Oreal D. Fowlkes", "Sherri J. Blessman",
                "Barbara A Rusin", "A. Rusin", "D. Fowlkes",
            ]),
            ["Barbara A. Rusin", "L'Oreal D. Fowlkes", "Sherri J. Blessman"],
        )

    def test_dedupe_keeps_longer_form_when_fragment_comes_first(self):
        self.assertEqual(f._dedupe_inspector_names(["A. Rusin", "Barbara A. Rusin"]),
                         ["Barbara A. Rusin"])

    def test_dedupe_does_not_merge_distinct_people(self):
        # 접미 관계가 아닌 서로 다른 이름은 절대 병합되지 않는다.
        names = ["Jose F Velez", "Ivis L Negron Torres", "Amy A Johnson"]
        self.assertEqual(f._dedupe_inspector_names(names), names)

    def test_dedupe_is_order_preserving_and_safe_on_empty(self):
        self.assertEqual(f._dedupe_inspector_names([]), [])
        self.assertEqual(f._dedupe_inspector_names(["Amy A Johnson"]), ["Amy A Johnson"])

    def test_candidate_cap_applies_after_dedupe_not_before(self):
        """★상한(6)이 조각 정리보다 **먼저** 걸리면 조각이 자리를 차지해 진짜 이름이
        밀려난다. 조각 4개를 앞세우고 실명 3개를 뒤에 둬(원시 7개 > 상한 6) 그 순서를
        고정한다 — 상한이 먼저 걸렸다면 마지막 'Amy A Johnson' 이 소실되고 조각
        'A Johnson' 이 남았을 것이다."""
        parts = ["F Velez", "L Negron Torres", "Negron Torres", "A Johnson",
                 "Jose F Velez", "Ivis L Negron Torres", "Amy A Johnson"]
        block = ("EMPLOYEE(S) SIGNATURE DATE ISSUED SEE REVERSE "
                 + " ".join(f"{p}, Investigator 3/5/2026" for p in parts))
        self.assertEqual(f._extract_483_inspectors(block),
                         ["Jose F Velez", "Ivis L Negron Torres", "Amy A Johnson"])


class InspectorWiringTest(unittest.TestCase):
    """수집 라인 배선 — 원시 text 에서 뽑은 실사관이 raw_payload 에 조건부로 실리는지.
    ENABLE_FDA_483_OBSERVATIONS/DEEP 플래그와 무관하게(둘 다 기본 off) 항상 시도된다.
    """

    def test_inspectors_present_when_signature_block_found(self):
        text = ("Cover. WE OBSERVED OBSERVATION 1 Aseptic processing was deficient.\n"
                "SEE REVERSE| DATE ISSUED\nJose F Velez,\nInvestigator\n2/27/2026\n"
                "OF THIS PAGE")
        with _Patched(json_rows=[_json_row(9101)], html_rows=[], pdf_text=text):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].raw_payload.get("fda483_inspectors"), ["Jose F Velez"])
        self.assertEqual(f.LAST_HEALTH["fda483_inspectors"]["extracted"], 1)

    def test_inspectors_key_absent_when_none_found(self):
        text = "Cover page only, no findings section, no signature block."
        with _Patched(json_rows=[_json_row(9102)], html_rows=[], pdf_text=text):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("fda483_inspectors", items[0].raw_payload)
        self.assertEqual(f.LAST_HEALTH["fda483_inspectors"]["failed"], 1)

    def test_inspectors_independent_of_observations_and_deep_flags(self):
        # 두 플래그 모두 기본 off 인 상태에서도 실사관은 추출·배선된다(독립 순수 파서).
        text = ("SEE REVERSE| DATE ISSUED\nLisa L. Flores,\nInvestigator\n12/10/2025\n"
                "OF THIS PAGE")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_FDA_483_OBSERVATIONS", None)
            os.environ.pop("ENABLE_FDA_483_DEEP", None)
            with _Patched(json_rows=[_json_row(9103)], html_rows=[], pdf_text=text):
                items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].raw_payload.get("fda483_inspectors"), ["Lisa L. Flores"])

    def test_graceful_fetch_fail_keeps_inspectors_absent(self):
        with _Patched(json_rows=[_json_row(9104)], html_rows=[],
                      bytes_exc=RuntimeError("HTTP 403 for ...")):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("fda483_inspectors", items[0].raw_payload)


# ── [관찰 회수 경로 2026-08-01] 본문은 있는데 관찰 0건인 483 되찾기 ─────────────
class Fda483ObservationRecoveryTest(unittest.TestCase):
    """라이브 실측(2026-08-01): FDA 483 문서 2,000건 중 444건이 findings 0건이고, 그중
    192건은 본문(excerpt)을 이미 갖고 있었다. 원인은 앵커 3종이었다 —
      ① `WE OBSERVED` 마커가 관찰 표제 **뒤**에 있어 컷이 관찰을 통째로 버림(83305)
      ② `OBS ERVAT ION 1` — 스캔 텍스트층이 단어 안에 공백 삽입(188080)
      ③ `WE OBSERVED` 뒤가 `1. 2. 3.` 번호 목록이고 "OBSERVATION" 단어가 없음(133048 등)

    ★불가침: 회수 경로는 **정상 경로가 0건일 때만** 돈다. 오늘 관찰이 나오는 문서의 출력은
    byte 단위로 불변이어야 한다(측정이 아니라 구조로 보장 — 표본 40문서 실측 회귀 0)."""

    HINTS = {"establishment_type": "", "fei_number": "", "firm_name": ""}

    def _obs(self, text):
        return f._extract_483_observations_from_text(text, self.HINTS)

    # ── 정상 경로 불변 ────────────────────────────────────────────────────────
    def test_normal_document_untouched_by_recovery(self):
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "OBSERVATION 1 There is a failure to thoroughly review unexplained "
                "discrepancies. Specifically, your firm did not investigate. "
                "OBSERVATION 2 Written production procedures are not followed. "
                "Specifically, batch records were incomplete for three lots.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertTrue(rows[0]["deficiency"].startswith("There is a failure"))

    def test_recovery_does_not_run_when_primary_yields(self):
        """정상 경로가 1건이라도 내면 회수 경로에 진입조차 하지 않는다 — 느슨한 앵커가
        정상 문서에 끼어들 경로 자체를 없앤다."""
        text = ("WE OBSERVED: OBSERVATION 1 Equipment used in manufacturing is not "
                "maintained in a clean condition as required by procedure. "
                "1. This numbered line must not become a second observation row.")
        rows = self._obs(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], "1")

    # ── ① 비파괴 컷 ──────────────────────────────────────────────────────────
    def test_marker_after_anchor_no_longer_discards_observations(self):
        """실측 83305: 앵커가 마커보다 앞에 있어, 마커에서 자르면 관찰이 0이 됐다."""
        text = ("During an inspection of your firm (I)(We) observed: Observation 1 "
                "Drug products are not stored under appropriate conditions of temperature. "
                "Specifically, the warehouse exceeded the labeled range on three days. "
                "IF YOU WISH TO DISCUSS WE OBSERVED THE FOLLOWING FORM TEXT.")
        rows = self._obs(text)
        self.assertEqual(len(rows), 1)
        self.assertIn("not stored under appropriate conditions", rows[0]["deficiency"])

    # ── ② 느슨한 앵커(OCR 공백) ──────────────────────────────────────────────
    def test_spaced_observation_word_is_recovered(self):
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "OBS ERVAT ION 1 Laboratory controls do not include the establishment "
                "of scientifically sound test procedures. Specifically, the method was "
                "never validated for the finished product assay.")
        rows = self._obs(text)
        self.assertEqual(len(rows), 1)
        self.assertIn("Laboratory controls do not include", rows[0]["deficiency"])

    # ── ③ 번호 목록 ─────────────────────────────────────────────────────────
    def test_numbered_list_without_observation_word_is_recovered(self):
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "1. Media fills were not performed that closely simulate aseptic "
                "production operations. Specifically, only one run was conducted. "
                "2. Your examination and testing of samples did not assure that the "
                "drug product conforms to specifications. Specifically, no assay ran.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])

    def test_numbered_fallback_requires_the_marker(self):
        """마커가 없는 문서에서 번호 목록을 관찰로 보면 목차·별첨까지 관찰이 된다."""
        text = ("TABLE OF CONTENTS 1. Introduction to the facility and its operations. "
                "2. Scope of the review performed by the corporate quality group.")
        self.assertEqual(self._obs(text), [])

    # ── 품질 게이트 ──────────────────────────────────────────────────────────
    def test_gate_blocks_form_boilerplate(self):
        """실측 192341: 회수 경로가 양식 뒷면 안내문을 관찰 표제로 만들었다."""
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "1. To assist firms inspected in complying with the Acts and regulations "
                "enforced by the Food and Drug Administration this form is provided.")
        self.assertEqual(self._obs(text), [])

    def test_gate_blocks_ocr_garbled_heading(self):
        """실측 190693: 'ass~re' 처럼 단어 속 기호가 섞인 표제는 공개하지 않는다."""
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "1. Thl ttiliv director failed to ass~re that all experimentaf data "
                "were accurately recorded in the notebooks maintained on site.")
        self.assertEqual(self._obs(text), [])

    def test_gate_keeps_redaction_markers(self):
        """`<Redacted B4>` · `(b) (4)` 는 FDA 의 정상 마스킹이지 OCR 깨짐이 아니다 —
        지우지 않고 기호 검사를 돌리면 멀쩡한 관찰이 통째로 기각된다."""
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "1. Your firm failed to clean the <Redacted B4> used to hold drug "
                "components within the (b) (4) cleanroom after each production shift.")
        rows = self._obs(text)
        self.assertEqual(len(rows), 1)

    def test_gate_helpers_are_pure_and_scoped_to_recovery(self):
        self.assertTrue(f._is_recovered_deficiency_publishable(
            "Drug products are not stored under appropriate conditions of temperature."))
        self.assertFalse(f._is_recovered_deficiency_publishable(
            "Pursuant to Section 704(b) of the Federal Food, Drug and Cosmetic Act."))
        self.assertFalse(f._is_recovered_deficiency_publishable("short"))
        # 단어 속 기호는 **절대 건수**로 본다 — 비율이면 긴 문장에서 희석돼 통과한다.
        long_clean = " ".join(["controls"] * 40)
        self.assertEqual(f._deficiency_garble(long_clean)[0], 0)
        self.assertEqual(f._deficiency_garble(long_clean + " ass~re")[0], 1)
        self.assertFalse(f._is_recovered_deficiency_publishable(long_clean + " ass~re"))

    def test_redaction_stripper_is_pure(self):
        self.assertNotIn("Redacted", f._strip_redaction_markers("a <Redacted B4> b"))
        self.assertNotIn("(b) (4)", f._strip_redaction_markers("a (b) (4) b"))
        self.assertIn("Consolidation", f._strip_redaction_markers("Consolidation: ARC"))


# ── [OCR 사유 보존 2026-08-01] 텍스트층이 빈 스캔본의 실패 사유 ────────────────
class Fda483RecoveryMarkerFallbackTest(unittest.TestCase):
    """★2026-08-02 RCA. 회수 실패의 최대 단일 원인은 정규식이 아니라 **배선**이었다.

    `_WE_OBSERVED_RE`(`\\bWE\\s+OBSERVED\\b`)는 양식 마커 `(I) (WE) OBSERVED` 를 괄호 때문에
    못 잡는 대신 **관찰문 본문 산문 속의 "we observed"** 를 잡는다. 회수 경로가
    `marker = we_observed or _OBS_MARKER_RECOVERY_RE.search(body)` 로 그것을 무조건
    우선하는 바람에, 스코프가 문서 꼬리로 밀려 관찰 목록 전체가 잘려 나갔다. 즉 관대한
    마커를 만들어 놓고 낡은 마커에 가려 못 쓰고 있었다.

    실측(로컬 원문 95건): 151790 은 진짜 마커 @1215 vs 산문 @21993 로 23,366자 중 마지막
    1,362자만 스코프가 됐다. 156917(@670/@7126)·88475·83323·92680·78991 도 같은 모양이며,
    수리 후 6문서 19건이 회수됐다.

    ★수리 방식이 핵심이다. "더 이른 매치를 고른다"로 고치면 **회귀가 난다** — 스코프를
    앞당기면 읽는 텍스트가 늘어 오탐도 는다(실측 82987 American Red Cross 가 8→32건으로
    부풀며 `REDACTION (b)(6) (entire last name)` 이 지적사항이 됐다). 그래서 교체가 아니라
    **폴백**으로 둔다: 첫 후보는 종전과 완전히 동일하고, 그 사다리가 0건일 때만 두 번째
    후보로 한 번 더 돈다. 회귀 불가능성이 측정이 아니라 제어 흐름의 성질로 보장된다.
    """

    HINTS = {"establishment_type": "", "fei_number": "", "firm_name": ""}

    def _obs(self, text):
        return f._extract_483_observations_from_text(text, self.HINTS)

    def test_prose_we_observed_no_longer_swallows_the_list(self):
        """양식 마커가 앞, 산문 "we observed" 가 뒤 — 종전엔 뒤를 골라 목록 전체를 잃었다."""
        text = (
            "DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED:\n"
            "1. Written procedures for cleaning and maintenance of equipment are not "
            "followed. Specifically, cleaning logs were missing for two vessels.\n"
            "2. Laboratory controls do not include scientifically sound test procedures. "
            "During the walkthrough we observed that the balance was uncalibrated.\n")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertIn("cleaning and maintenance", rows[0]["deficiency"])

    def test_first_candidate_is_unchanged_so_working_docs_cannot_regress(self):
        """★무회귀의 근거. 첫 후보는 `we_observed or recovery` 로 종전과 동일하다 —
        첫 패스가 무언가를 내면 그대로 반환되고 두 번째 후보는 시도조차 되지 않는다."""
        import re
        we = re.search(r"\bWE\s+OBSERVED\b", "X WE OBSERVED Y")
        rec = re.search(r"\bOBSERVED\b", "X WE OBSERVED Y")
        self.assertIs(f._marker_candidates(we, rec)[0], we)
        self.assertIs(f._marker_candidates(None, rec)[0], rec)
        self.assertEqual(f._marker_candidates(None, None), [None])

    def test_candidates_never_include_a_bare_whole_document_scope(self):
        """마커 없이 전문을 훑는 후보는 만들지 않는다 — 목차·별첨까지 관찰이 된다."""
        import re
        we = re.search(r"\bWE\s+OBSERVED\b", "A WE OBSERVED B")
        self.assertNotIn(None, f._marker_candidates(we, None))

    def test_same_position_markers_are_not_retried(self):
        """두 마커가 같은 자리를 가리키면 두 번 돌 이유가 없다(무의미한 재시도 방지)."""
        import re
        we = re.search(r"\bWE\s+OBSERVED\b", "A WE OBSERVED B")
        rec = re.search(r"\bWE\s+OBSERVED\b", "A WE OBSERVED B")
        self.assertEqual(len(f._marker_candidates(we, rec)), 1)


class Fda483PageHeaderFooterCutTest(unittest.TestCase):
    """관찰문이 페이지 경계를 넘으면 머리말 블록이 **문장 한가운데** 삽입된다.

    실측 88475 obs#2: "…purporting to be sterile are not / OFFICE ADDRESS ANO NUMSER /
    Dallas District Office / 4040 North Central Expressway, Suite 400 / Dallas, TX 75204…"
    블록 안의 `DEPARTMENT OF HEALTH` 는 OCR 이 `OEPARTIIENT Of HEALTH` 로 깨뜨려 기존
    마커가 실패했다. 블록 **첫 줄**인 `OFFICE ADDRESS` 를 마커로 넣어 더 이르게 자른다.
    전 코퍼스 실측(137문서 중 46문서 112회) 전부 양식 라벨이고 산문 용례가 0이다.
    """

    HINTS = {"establishment_type": "", "fei_number": "", "firm_name": ""}

    def test_page_header_block_is_cut_from_the_deficiency(self):
        text = (
            "DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED:\n"
            "1. Procedures designed to prevent microbiological contamination of drug "
            "products purporting to be sterile are not\n\n"
            "OFFICE ADDRESS ANO\nNUMSER\nDallas District Office\n"
            "4040 North Central Expressway, Suite 400\nDallas, TX 75204\n214-253-5200\n")
        rows = f._extract_483_observations_from_text(text, self.HINTS)
        self.assertEqual(len(rows), 1)
        deficiency = rows[0]["deficiency"]
        self.assertIn("purporting to be sterile", deficiency)
        for leaked in ("Dallas District Office", "4040 North Central", "214-253-5200",
                       "OFFICE ADDRESS"):
            self.assertNotIn(leaked, deficiency, f"양식 머리말이 지적사항에 샜다: {leaked}")

    def test_marker_is_case_fixed_so_prose_is_not_cut(self):
        """대문자 고정 — 산문 속 소문자 'office address' 는 자르지 않는다."""
        self.assertIsNone(f._FDA483_FOOTER_RE.search("mailed to the office address on file"))
        self.assertIsNotNone(f._FDA483_FOOTER_RE.search("DISTRICT OFFICE ADDRESS AND PHONE"))


class Fda483ArtifactOnlyTextLayerTest(unittest.TestCase):
    """★2026-08-02. **"글자가 있다 ≠ 본문이 있다."**

    본문이 100% 스캔 이미지인데 텍스트층에 FOIA 리댁션 주석·페이지 라벨·접근성 고지만
    실린 483 이 32건 있었다. 7자(`(b) (4)`)짜리 문서까지 `text.strip()` 이 참이라
    `pdf-ok` 로 통과했고 OCR 을 **한 번도 안 돌렸다**.

    ★고칠 곳이 두 군데다. `_needs_ocr` 만 고치면 페이지 루프의 `if native.strip()` 이
      전 페이지를 건너뛰어 아무것도 달라지지 않는다(실측 175834 는 15쪽 전부, 193692 는
      4쪽 전부가 그렇게 통과했다). 이 클래스가 두 곳을 함께 고정한다.

    임계 근거(실측): 관찰문이 정상 산출되는 대조군 42문서의 텍스트 길이 **최소 1,841자**,
    결손군 41문서는 최소 7자. 페이지 단위로는 결손군 252쪽 중 120쪽이 걸리고 대조군
    263쪽 중 1쪽만 걸리는데, 그 1쪽도 `(b)(4)` 뿐인 진짜 이미지 페이지다(오탐 아님).
    """

    def test_redaction_markers_alone_are_not_a_body(self):
        for text in ("(b) (4)",
                     "(b) (4)\n" * 40,
                     "(b)(6)\n(b)(6)\n(b)(6)",
                     "redacted text",
                     "Page 1 of 4\nPage 2 of 4\nPage 3 of 4\nPage 4 of 4"):
            with self.subTest(text=text[:28]):
                self.assertFalse(f._is_substantive_text(text))

    def test_fda_accessibility_notice_is_not_a_body(self):
        """FDA 가 스스로 '이 페이지는 대체텍스트를 제공할 수 없다'고 적은 고지 —
        본문이 이미지라는 1차 사료적 증거다(102220·102644 실측)."""
        text = ("Unfortunately, we cannot provide alternative text for this page. "
                "Persons with disabilities having problems accessing this page may "
                "call (301) 796-3634 for assistance.")
        self.assertFalse(f._is_substantive_text(text))

    def test_real_observation_text_is_substantive(self):
        text = ("OBSERVATION 1 Procedures designed to prevent microbiological "
                "contamination of drug products purporting to be sterile are not "
                "established, written, or followed.")
        self.assertTrue(f._is_substantive_text(text))

    def test_redaction_marker_never_eats_the_surrounding_sentence(self):
        """리댁션 마커가 섞인 **진짜 지적문**은 실질로 남아야 한다 — 마커만 걷어낸다."""
        self.assertTrue(f._is_substantive_text(
            "Equipment used by (b)(4) operators was not cleaned at appropriate intervals."))
        self.assertEqual(
            f._text_residue_after_artifacts("(b)(7)(C) equipment was dirty"),
            "equipmentwasdirty")

    def test_trailing_subletter_group_does_not_swallow_the_next_marker(self):
        """★실측 함정. 꼬리 하위표기를 `[A-Za-z]` 로 두면 다음 마커의 `(b)` 를 먹어치워
        그 뒤 `(4)` 가 잔여로 남는다 — 175834 의 2,133자가 잔여 132자(전부 '4')였다.
        패턴 전체가 re.I 라 `[A-Z]` 만으로도 부족하고 `(?-i:...)` 가 필요하다."""
        self.assertEqual(f._text_residue_after_artifacts("(b) (4)\n" * 30), "")

    def test_needs_ocr_fires_on_artifact_only_text(self):
        with patch.object(f, "_ocr_enabled", lambda: True):
            self.assertTrue(f._needs_ocr("(b) (4)"))
            self.assertTrue(f._needs_ocr("(b) (4)\n" * 40))
            self.assertTrue(f._needs_ocr(""))
            self.assertFalse(f._needs_ocr(
                "OBSERVATION 1 Written procedures are not followed by employees "
                "engaged in the manufacture of drug products at this facility."))

    def test_ocr_disabled_still_short_circuits(self):
        """플래그가 꺼져 있으면 어떤 텍스트에도 OCR 을 요구하지 않는다(기존 계약)."""
        with patch.object(f, "_ocr_enabled", lambda: False):
            self.assertFalse(f._needs_ocr(""))
            self.assertFalse(f._needs_ocr("(b) (4)"))


class Fda483ArtifactPageLoopTest(unittest.TestCase):
    """페이지 루프가 아티팩트 페이지를 **빈 페이지처럼** 다루는지 고정한다.

    ★여기가 32건을 실제로 막던 지점이다. 종전 조건 `if native.strip()` 은 `(b) (4)`
      한 줄만 있어도 페이지를 건너뛰었다.
    """

    class _Page:
        def __init__(self, native, ocr="", images=1):
            self._native, self._ocr, self._images = native, ocr, images
            self.ocr_called = False

        def get_text(self, _kind, textpage=None):
            return self._ocr if textpage is not None else self._native

        def get_images(self, full=False):
            return [object()] * self._images

        def get_textpage_ocr(self, **_kw):
            self.ocr_called = True
            return object()

    class _Doc:
        needs_pass = False
        is_encrypted = False

        def __init__(self, pages):
            self._pages = pages

        def __iter__(self):
            return iter(self._pages)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def _run(self, pages):
        import types
        fake = types.SimpleNamespace(open=lambda **_kw: self._Doc(pages))
        with patch.dict("sys.modules", {"fitz": fake}), \
             patch.object(f, "_ensure_tessdata_prefix", lambda: None), \
             patch.object(f, "_tessdata_dir", lambda: "/x"), \
             patch.dict(f._OCR_BUDGET, {"remaining": 50, "used": 0}):
            return f._ocr_483_pdf_text_uncounted(b"%PDF", 200000)

    def test_artifact_page_is_ocred_not_skipped(self):
        page = self._Page("(b) (4)\n(b) (4)", ocr="OBSERVATION 1 Equipment was not cleaned.")
        text, status = self._run([page])
        self.assertTrue(page.ocr_called, "아티팩트 페이지가 건너뛰어졌다")
        self.assertIn("Equipment was not cleaned", text)
        self.assertEqual(status, "pdf-ok-ocr")

    def test_substantive_page_is_never_overwritten_by_ocr(self):
        """★가장 위험한 방향. 멀쩡한 텍스트 페이지를 OCR 결과로 덮어쓰면 안 된다."""
        real = ("OBSERVATION 1 Written procedures are not followed by employees engaged "
                "in the manufacture of drug products.")
        page = self._Page(real, ocr="G4RBL3D 0CR 0UTPUT")
        text, _status = self._run([page])
        self.assertFalse(page.ocr_called)
        self.assertIn("Written procedures are not followed", text)
        self.assertNotIn("G4RBL3D", text)

    def test_artifact_page_without_images_is_left_alone(self):
        """아티팩트뿐인데 이미지도 없으면 OCR 이 살릴 것이 없다 — 예산을 쓰지 않는다."""
        page = self._Page("(b) (4)", ocr="x", images=0)
        text, _status = self._run([page])
        self.assertFalse(page.ocr_called)
        self.assertIn("(b) (4)", text)

    def test_budget_exhaustion_never_shrinks_todays_output(self):
        """★예산이 없어 OCR 을 못 해도 종전 산출을 줄이지 않는다 — 종전에는 이 페이지가
        `if native.strip()` 분기에서 이미 append 됐다."""
        page = self._Page("(b) (4)", ocr="x")
        import types
        fake = types.SimpleNamespace(open=lambda **_kw: self._Doc([page]))
        with patch.dict("sys.modules", {"fitz": fake}), \
             patch.object(f, "_ensure_tessdata_prefix", lambda: None), \
             patch.object(f, "_tessdata_dir", lambda: "/x"), \
             patch.dict(f._OCR_BUDGET, {"remaining": 1, "used": 0}), \
             patch.object(f, "FDA483_OCR_MAX_PAGES", 0):
            text, _status = f._ocr_483_pdf_text_uncounted(b"%PDF", 200000)
        self.assertFalse(page.ocr_called)
        self.assertIn("(b) (4)", text)

    def test_empty_ocr_result_falls_back_to_native(self):
        page = self._Page("(b) (4)", ocr="   ")
        text, _status = self._run([page])
        self.assertTrue(page.ocr_called)
        self.assertIn("(b) (4)", text)


class Fda483OcrReasonPropagationTest(unittest.TestCase):
    """`_fetch_fda483_pdf_text` 는 OCR 이 못 살렸을 때 **왜 못 살렸는지**를 하류에
    넘겨야 한다. 종전엔 `_is_notice_only(text)` 일 때만 OCR 사유로 바꿔 돌려줘서,
    텍스트층이 **완전히 빈** 스캔본(= `_is_notice_only("")` 는 False)은 원래 status
    (`scan-no-text`)가 그대로 나갔다 — `scan-ocr-budget`(아직 안 함)·`scan-ocr-empty`
    (했지만 글자 0)·`scan-ocr-unavailable`(엔진 없음)이 한 값으로 뭉개졌다.

    ★영향: `backfill_483_ocr_recovery.is_ocr_unavailable_row` 는 `scan-no-text` 를
    "엔진을 붙여도 결과가 같다"고 보고 복구 대상에서 뺀다. 사유가 뭉개지면 **예산 때문에
    밀린 문서가 영구히 복구 대상 밖**이 된다. "우리가 못 받았다"와 "원문에 없다"는 서로
    다른 말이어야 한다."""

    def _patched(self, primary, ocr):
        """(_extract_pdf_text, _ocr_483_pdf_text) 를 갈아끼운 상태로 fetch 실행."""
        import collect_mfds_gmp_inspection as gmp
        with patch.object(gmp, "_extract_pdf_text", lambda data, max_chars=0: primary), \
             patch.object(f, "_ocr_483_pdf_text", lambda data, max_chars=0: ocr), \
             patch.object(f, "http_get_bytes", lambda *a, **k: b"%PDF-1.4"):
            return f._fetch_fda483_pdf_text("https://www.fda.gov/media/1/download")

    def test_empty_text_layer_keeps_budget_reason(self):
        """예산 소진은 '아직 안 함'이다 — scan-no-text 로 덮이면 다시 시도되지 않는다."""
        _, status = self._patched(("", "scan-no-text"), ("", "scan-ocr-budget"))
        self.assertEqual(status, "scan-ocr-budget")

    def test_empty_text_layer_keeps_engine_missing_reason(self):
        _, status = self._patched(("", "scan-no-text"), ("", "scan-ocr-unavailable:no tessdata"))
        self.assertEqual(status, "scan-ocr-unavailable:no tessdata")

    def test_empty_text_layer_keeps_ocr_empty_reason(self):
        """OCR 이 실제로 돌았는데 글자가 0이면 그건 '시도했지만 안 됨' — 다른 사유다."""
        _, status = self._patched(("", "scan-no-text"), ("", "scan-ocr-empty"))
        self.assertEqual(status, "scan-ocr-empty")

    def test_notice_only_path_unchanged(self):
        """고지문 전용 경로는 종전 그대로(회귀 없음)."""
        notice = ("This document lists observations of objectionable conditions made by "
                  "the FDA representative(s) during the inspection.")
        self.assertTrue(f._is_notice_only(notice))
        _, status = self._patched((notice, "pdf-ok"), ("", "scan-ocr-empty"))
        self.assertEqual(status, "scan-ocr-empty")

    def test_real_text_is_never_overwritten_by_ocr_failure(self):
        """본문이 실제로 있으면 OCR 실패 사유로 덮지 않는다 — 있는 본문을 버리면 안 된다."""
        body = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: OBSERVATION 1 "
                "Written procedures are not followed for cleaning of equipment.")
        text, status = self._patched((body, "pdf-ok"), ("", "scan-ocr-budget"))
        self.assertEqual(status, "pdf-ok")
        self.assertIn("OBSERVATION 1", text)

    def test_ocr_success_still_wins(self):
        recovered = "OBSERVATION 1 Equipment cleaning records are incomplete for three lots."
        text, status = self._patched(("", "scan-no-text"), (recovered, "pdf-ok-ocr"))
        self.assertEqual(status, "pdf-ok-ocr")
        self.assertIn("OBSERVATION 1", text)

    def test_budget_is_env_configurable_for_one_off_runs(self):
        """일일 수집의 벽시계를 지키는 기본값(200)은 1회성 회수에서 스캔본 ~20건에
        소진된다 — 워크플로가 실행별로 올릴 수 있어야 한다."""
        self.assertEqual(f.FDA483_OCR_PAGE_BUDGET, f._env_int("FDA483_OCR_PAGE_BUDGET", 200))
        wf = (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           ".github", "workflows", "grm-fda483-ocr-recovery.yml"))
        src = open(wf, encoding="utf-8").read()
        self.assertIn("ocr_page_budget:", src)
        self.assertIn("FDA483_OCR_PAGE_BUDGET: ${{ inputs.ocr_page_budget }}", src)


# ── [표제 선행 잡음 2026-08-01] 관찰 표제 앞 OCR/양식 파편 제거 ────────────────
class Fda483LeadingNoiseTest(unittest.TestCase):
    """스캔 483 을 OCR 하면 표 테두리·괘선·글머리 기호가 본문 첫 글자 앞에 남는다
    (라이브 실측 106건: "| There is a failure…", "_ |Procedures describing…",
    "· • ·· The Quality Unit…"). 내용은 멀쩡하니 버릴 게 아니라 떼어낸다.

    ★`_TRAILING_STRAY_LETTER_RE` 의 앞쪽 짝이며, **모든 경로**에 적용한다 — OCR 로 되살린
    문서는 정상 앵커 경로로도 파싱되므로 회수 경로에만 걸면 잡음이 그대로 남는다."""

    def test_strips_table_border_and_bullet_debris(self):
        for raw, want in (
            ("| There is a failure to review discrepancies.",
             "There is a failure to review discrepancies."),
            ("_ |Procedures describing the calibration of instruments are absent.",
             "Procedures describing the calibration of instruments are absent."),
            ("· • ·· The Quality Unit does not review documents.",
             "The Quality Unit does not review documents."),
            ("— Equipment and utensils are not cleaned at intervals.",
             "Equipment and utensils are not cleaned at intervals."),
            ("!· ; There is a lack of written procedures.",
             "There is a lack of written procedures."),
        ):
            self.assertEqual(f.strip_leading_observation_noise(raw), want)

    def test_strips_subitem_marker_and_stray_letter(self):
        self.assertEqual(
            f.strip_leading_observation_noise("i The quality control unit lacks authority."),
            "The quality control unit lacks authority.")
        self.assertEqual(
            f.strip_leading_observation_noise("b. Written procedures are not followed."),
            "Written procedures are not followed.")

    def test_keeps_legitimate_sentence_starts(self):
        """관사 A/a·대명사 I 로 시작하는 진짜 표제를 깎으면 안 된다."""
        for keep in (
            "A written procedure for cleaning was not established.",
            "a written procedure for cleaning was not established.",
            "I observed that the operator did not record the time.",
            '"Sterile" products were not tested for endotoxin.',
            "(b)(4) filters were not integrity tested after use.",
        ):
            self.assertEqual(f.strip_leading_observation_noise(keep), keep)

    def test_keeps_redaction_marker_at_start(self):
        """★첫 측정에서 실제로 밟은 오탐: `<Redacted B4>` 의 여는 꺾쇠를 떼면 표기가 깨진다
        (실측 153584)."""
        raw = "<Redacted B4> testing to the <Redacted B4> was not performed."
        self.assertEqual(f.strip_leading_observation_noise(raw), raw)

    def test_never_truncates_content(self):
        """상한을 넘게 깎이면 잡음 제거가 아니라 내용 절단 — 원본을 그대로 둔다."""
        long_noise = "." * (f.FDA483_LEADING_NOISE_MAX_STRIP + 5) + "Procedures are absent."
        self.assertEqual(f.strip_leading_observation_noise(long_noise), long_noise)

    def test_never_returns_empty(self):
        self.assertEqual(f.strip_leading_observation_noise("|||"), "|||")
        self.assertEqual(f.strip_leading_observation_noise(""), "")

    def test_applied_on_every_extraction_path(self):
        """정상 앵커 경로에서도 걸려야 한다 — 회수 경로 전용이면 OCR 문서의 잡음이 남는다."""
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "OBSERVATION 1 | There is a failure to thoroughly review any unexplained "
                "discrepancy. Specifically, no investigation was opened for three lots.")
        rows = f._extract_483_observations_from_text(
            text, {"establishment_type": "", "fei_number": "", "firm_name": ""})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["deficiency"].startswith("There is a failure"),
                        rows[0]["deficiency"])

    def test_public_api_for_backfill_reuse(self):
        """이미 저장된 표제 재청소(backfill)도 같은 함수를 써야 코드와 데이터가 안 갈린다
        (`_clean_observation_detail` 과 동일 관례)."""
        self.assertFalse(f.strip_leading_observation_noise.__name__.startswith("_"))
        self.assertIn("strip_leading_observation_noise", dir(f))


# ── [OCR 마커·번호 변형 2026-08-01] 스캔 483 실측 표기 ────────────────────────
class Fda483OcrAnchorVariantTest(unittest.TestCase):
    """★전처리(해상도·이진화)가 아니라 **정규식**이 막고 있었다.

    회수 실패 235건 중 171건은 OCR 이 이미 **읽을 수 있는 텍스트**를 뽑아냈는데도 관찰이
    0건이었다. 저장된 excerpt 실측(171건 기준):
      · `(I) (WE) OBSERVED`  93건 — 정상 마커 `WE\s+OBSERVED` 는 괄호 때문에 못 잡는다
      · `| OBSERVED:`        19건 — OCR 이 대문자 I 를 `|` 로 읽는다
      · `WE OBSERVED` 평문    15건
      · `OBSERVATION #1`     42건 — 공백만 허용하던 앵커가 `#` 를 못 넘는다
    마커를 못 잡으면 번호목록 폴백이 아예 켜지지 않아 문서 전체가 통째로 유실됐다.

    ★격리 원칙: 정상 경로가 쓰는 `_WE_OBSERVED_RE` 는 건드리지 않는다(넓히면 오늘 잘 되는
    문서의 컷 위치가 달라진다). 회수 경로 전용 `_OBS_MARKER_RECOVERY_RE` 를 따로 둔다."""

    H = {"establishment_type": "", "fei_number": "", "firm_name": ""}

    def _obs(self, text):
        return f._extract_483_observations_from_text(text, self.H)

    # ── 마커 변형 ────────────────────────────────────────────────────────────
    def test_paren_i_we_observed_marker(self):
        """실측 최다(93건): `DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED:`"""
        text = ("DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED: Quality system: "
                "1. The responsibilities and procedures applicable to the quality control "
                "unit are not fully followed. Specifically, section 4.3 was not applied. "
                "2. Written procedures are not established for equipment cleaning. "
                "Specifically, no cleaning record existed for three lots.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertIn("responsibilities and procedures", rows[0]["deficiency"])

    def test_lowercase_paren_i_we_observed(self):
        """실측: `During an inspection of your firm (i) (we) observed: Observation #1 …`"""
        text = ("During an inspection of your firm (i) (we) observed: Observation #1 "
                "Your firm has failed to thoroughly and adequately investigate biological "
                "product deviations. Specifically, three deviations had no root cause.")
        rows = self._obs(text)
        self.assertEqual(len(rows), 1)
        self.assertIn("failed to thoroughly", rows[0]["deficiency"])

    def test_pipe_observed_marker_from_ocr_i(self):
        """실측(19건): OCR 이 대문자 I 를 `|` 로 읽는다 — `FIRM | OBSERVED:`"""
        text = ("DURING AN INSPECTION OF YOUR FIRM | OBSERVED: Laboratory Controls: "
                "1. Your laboratory failure investigations are inadequate in that you "
                "re-test without any scientific justification until passing results are "
                "obtained. 2. Records are not maintained for the retest decisions.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])

    # ── OBSERVATION #N ───────────────────────────────────────────────────────
    def test_observation_hash_number(self):
        """실측(42건): `OBSERVATION #1 – …` · `Observation #1: …`"""
        for marker in ("OBSERVATION #1 –", "Observation #1:", "OBSERVATION # 1 -"):
            text = ("DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED: " + marker +
                    " Your firm has failed to establish adequate procedures for conducting "
                    "appropriate media fill simulations. Specifically, the last run failed.")
            rows = self._obs(text)
            self.assertEqual(len(rows), 1, marker)
            self.assertIn("media fill simulations", rows[0]["deficiency"], marker)

    # ── 정상 경로 불변(격리 확인) ────────────────────────────────────────────
    def test_primary_marker_regex_untouched(self):
        """`_WE_OBSERVED_RE` 는 확장하지 않는다 — 정상 경로 컷 위치가 달라지면 안 된다."""
        self.assertIsNone(f._WE_OBSERVED_RE.search("(I) (WE) OBSERVED:"))
        self.assertIsNotNone(f._WE_OBSERVED_RE.search("WE OBSERVED:"))
        self.assertIsNotNone(f._OBS_MARKER_RECOVERY_RE.search("(I) (WE) OBSERVED:"))

    def test_normal_document_still_untouched(self):
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "OBSERVATION 1 There is a failure to thoroughly review unexplained "
                "discrepancies. Specifically, no investigation was opened. "
                "OBSERVATION 2 Written production procedures are not followed. "
                "Specifically, batch records were incomplete.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertTrue(rows[0]["deficiency"].startswith("There is a failure"))

    def test_numbered_fallback_still_requires_a_marker(self):
        """마커가 전혀 없으면 번호목록을 관찰로 보지 않는다(목차·별첨 오탐 방지)."""
        text = ("TABLE OF CONTENTS 1. Introduction to the facility and its operations. "
                "2. Scope of the review performed by the corporate quality group.")
        self.assertEqual(self._obs(text), [])

    def test_quality_gate_still_applies_to_recovered_rows(self):
        """마커를 넓혀도 품질 게이트는 그대로 — 양식 문구는 여전히 기각된다."""
        text = ("DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED: "
                "1. To assist firms inspected in complying with the Acts and regulations "
                "enforced by the Food and Drug Administration this form is provided.")
        self.assertEqual(self._obs(text), [])


# ── [닫는 괄호 번호 · (WE) 마커 2026-08-01] 잔여 회수 2차 ─────────────────────
class Fda483ParenNumberingTest(unittest.TestCase):
    """앵커 변형 1차 수정(#512) 후 남은 39건(본문 있음·제약 범위)을 다시 분해한 결과:
      · `1)` · `1.)` 닫는 괄호 번호   22건  ← 최대 덩어리
      · `(WE) OBSERVED`(앞 `(I)` 없음) 3건
      · OCR 로 깨진 번호/OBSERVED      3건  ← 개별 케이스, 손대지 않는다
      · 고지문으로 시작                8건  ← 전체 텍스트가 있어야 판정 가능
    앞의 둘만 고친다 — 확실하고 다수인 것만."""

    H = {"establishment_type": "", "fei_number": "", "firm_name": ""}

    def _obs(self, text):
        return f._extract_483_observations_from_text(text, self.H)

    def test_paren_numbering(self):
        """실측 157407 · 78991: `1) Procedures designed to prevent…`"""
        text = ("DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED: "
                "1) Procedures designed to prevent microbiological contamination of drug "
                "products purporting to be sterile are not written and/or followed. "
                "2) Records are not maintained for equipment cleaning and use.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])

    def test_period_paren_numbering_after_subheading(self):
        """실측 119171: 소제목 바로 뒤에 `1.)` — 문장부호 경계가 없다."""
        text = ("DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED: PRODUCTION SYSTEM "
                "1.) Blending of in-specification with out-of-specification intermediate "
                "batches is performed. Specifically, one batch tested out of specification. "
                "2.) Cleaning records were not retained for the blending vessel.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertTrue(rows[0]["deficiency"].startswith("Blending of in-specification"))

    def test_paren_we_marker_without_leading_i(self):
        """실측 151790: `FIRM (WE) OBSERVED 1. …` — 괄호가 WE 와 OBSERVED 를 끊는다."""
        text = ("DURING AN INSPECTION OF YOUR FIRM (WE) OBSERVED 1. There are no "
                "comprehensive risk assessments conducted at this multiproduct facility to "
                "justify the containment strategy. Specifically, no assessment existed.")
        rows = self._obs(text)
        self.assertEqual(len(rows), 1)
        self.assertIn("comprehensive risk assessments", rows[0]["deficiency"])
        self.assertIsNotNone(f._OBS_MARKER_RECOVERY_RE.search("FIRM (WE) OBSERVED 1."))

    # ── 오탐 방어 ────────────────────────────────────────────────────────────
    def test_redaction_marker_is_not_a_number_anchor(self):
        """`(b) (4)` 의 `4)` 를 번호로 보면 마스킹마다 가짜 관찰이 생긴다 — 여는 괄호가
        앞에 있어 경계를 만족하지 않는다."""
        self.assertIsNone(f._PAREN_NUMBERED_OBS_RE.search("the (b) (4) System was used"))
        self.assertIsNone(f._PAREN_NUMBERED_OBS_RE.search("within (4) Days of receipt"))

    def test_prose_numbers_are_not_anchors(self):
        """산문 속 숫자는 닫는 괄호가 없어 매치되지 않는다."""
        for prose in ("completed within 5 days The batch", "for 3 lots Specifically"):
            self.assertIsNone(f._PAREN_NUMBERED_OBS_RE.search(prose), prose)

    def test_lowercase_after_number_is_not_an_anchor(self):
        self.assertIsNone(f._PAREN_NUMBERED_OBS_RE.search("see 1) the attached list"))

    def test_paren_fallback_only_when_period_form_yields_nothing(self):
        """마침표 번호가 이미 나오면 괄호 폴백은 돌지 않는다(중복 앵커 방지)."""
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "1. Written procedures are not followed for equipment cleaning. "
                "Specifically, the log showed 2) entries missing for March.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1"])

    def test_paren_numbering_still_requires_marker(self):
        text = ("APPENDIX 1) Facility layout diagram and equipment list. "
                "2) Organizational chart of the quality unit.")
        self.assertEqual(self._obs(text), [])

    def test_quality_gate_still_applies(self):
        text = ("DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED: "
                "1) To assist firms inspected in complying with the Acts and regulations "
                "enforced by the Food and Drug Administration this form is provided.")
        self.assertEqual(self._obs(text), [])

    def test_normal_document_unchanged(self):
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED: "
                "OBSERVATION 1 There is a failure to thoroughly review unexplained "
                "discrepancies. Specifically, no investigation was opened. "
                "OBSERVATION 2 Written production procedures are not followed. "
                "Specifically, batch records were incomplete.")
        rows = self._obs(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])


# ── [렌더 DPI 실행별 조절 2026-08-02] ─────────────────────────────────────────
class Fda483OcrDpiKnobTest(unittest.TestCase):
    """잔여 회수 대상 52건의 PDF 를 직접 열어 본 결과 **원본 스캔이 대부분 ~163dpi** 였다
    (2026-08-01 실측: 10건 중 7건). 현재 코드는 300dpi 로 렌더해 OCR 하는데, 저해상도
    원본에서는 렌더를 키워 글리프를 크게 하는 것이 인식률에 도움이 되는 경우가 있다.
    1회성 회수 워크플로가 실행별로 시험할 수 있도록 env 로 뺀다.

    ★기본값 300 은 그대로다 — 일상 수집 경로는 무영향이어야 한다."""

    def test_default_is_unchanged(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FDA483_OCR_DPI", None)
            self.assertEqual(f._env_int("FDA483_OCR_DPI", 300), 300)

    def test_env_overrides_within_bounds(self):
        with patch.dict(os.environ, {"FDA483_OCR_DPI": "400"}):
            self.assertEqual(min(max(f._env_int("FDA483_OCR_DPI", 300), 72), 600), 400)

    def test_clamped_both_ends(self):
        """업스케일은 정보를 늘리지 않는다 — 무한정 올리면 렌더 시간만 늘어난다."""
        with patch.dict(os.environ, {"FDA483_OCR_DPI": "5000"}):
            self.assertEqual(min(max(f._env_int("FDA483_OCR_DPI", 300), 72), 600), 600)
        with patch.dict(os.environ, {"FDA483_OCR_DPI": "10"}):
            self.assertEqual(min(max(f._env_int("FDA483_OCR_DPI", 300), 72), 600), 72)

    def test_ocr_call_uses_the_constant(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "collect_fda_483.py"), encoding="utf-8").read()
        self.assertIn("dpi=FDA483_OCR_DPI", src)
        self.assertIn('FDA483_OCR_DPI = min(max(_env_int("FDA483_OCR_DPI", 300), 72), 600)', src)

    def test_workflow_exposes_dpi_input(self):
        wf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          ".github", "workflows", "grm-fda483-ocr-recovery.yml")
        src = open(wf, encoding="utf-8").read()
        self.assertIn("ocr_dpi:", src)
        self.assertIn("FDA483_OCR_DPI: ${{ inputs.ocr_dpi }}", src)
        self.assertIn("default: '300'", src)
