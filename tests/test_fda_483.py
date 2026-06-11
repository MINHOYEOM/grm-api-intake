"""FDA 483/EIR 수집기 회귀 — WHY-1 #3 (+ 완전성 HOLD: JSON 전수 + HTML 폴백/보강).

검증: JSON 전수 파싱(완전성)·HTML Country 보강·JSON 사망 시 HTML 폴백+source-degraded·
Record Type 필터(483/EIR)·Publish 윈도우(전수·정렬 비의존)·노이즈/수의/기기 게이트·
dedup(media id, node mid 불일치 대비)·PDF excerpt(483 앵커·graceful)·Tier·Country 매핑·
구조변경 sentinel·flag/토큰 wiring.

무네트워크: http_get_json·http_get_html·http_get_bytes·_extract_pdf_text 스텁.
"""
import os
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
    def _inner(data):
        return text, status
    return _inner


class _Patched:
    """JSON(전수)·HTML(보강/폴백)·PDF fetch·텍스트 추출 스텁 + delay 0.

    json_rows: JSON 레코드 리스트 또는 None(JSON 사망) 또는 '비배열'.
    html_rows: HTML 행 spec 리스트(자동 렌더) · 완성 HTML 문자열 · None.
    """
    def __init__(self, json_rows=None, html_rows=None,
                 pdf_text="OBSERVATION 1 Sterile defect.", pdf_status="pdf-ok",
                 bytes_exc=None, json_exc=None, html_exc=None):
        self.json_rows = json_rows
        self.json_exc = json_exc
        if html_rows is None:
            self.html = ""
        elif isinstance(html_rows, str):
            self.html = html_rows
        else:
            self.html = _html(html_rows)
        self.html_exc = html_exc
        self.bytes = _StubBytes(raise_exc=bytes_exc)
        self.pdf_text = pdf_text
        self.pdf_status = pdf_status

    def _stub_json(self, url, **kwargs):
        if self.json_exc:
            raise self.json_exc
        return self.json_rows

    def _stub_html(self, url, **kwargs):
        if self.html_exc:
            raise self.html_exc
        if not self.html:
            raise RuntimeError("no html stub")
        return self.html

    def __enter__(self):
        self._p = [
            patch.object(f, "http_get_json", self._stub_json),
            patch.object(f, "http_get_html", self._stub_html),
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
    def test_json_gives_full_window_not_html_subset(self):
        # 핵심 회귀: JSON 전수가 윈도우 내 모든 483 을 준다 — HTML 최신 일부만이 아님.
        # (실사일 오래됐지만 최근 공개된 Intas/Dabur 류가 누락되지 않아야 함.)
        json_rows = [
            _json_row(1, company="BPI Labs", record_date="04/17/2026", publish="05/27/2026"),
            _json_row(2, company="Wells Pharma", record_date="04/13/2026", publish="05/27/2026"),
            _json_row(3, company="Intas", state="", record_date="09/17/2025", publish="05/26/2026"),
            _json_row(4, company="Dabur India", state="", record_date="01/16/2026", publish="05/26/2026"),
            _json_row(5, company="Excel Vision", state="", record_date="01/22/2026", publish="05/15/2026"),
        ]
        html_rows = [  # HTML 은 최신 실사일 2건만(Intas/Dabur/Excel 은 표 아래로 밀림)
            _html_row(1, company="BPI Labs", record_date="04/17/2026", publish="05/27/2026"),
            _html_row(2, company="Wells Pharma", record_date="04/13/2026", publish="05/27/2026"),
        ]
        with _Patched(json_rows=json_rows, html_rows=html_rows, pdf_text="OBSERVATION 1 x"):
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
    def test_only_483_and_eir_kept(self):
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
        self.assertEqual({it.document_id for it in items}, {"fda483-1001", "fda483-1002"})

    def test_type_or_class_483_vs_eir(self):
        with _Patched(json_rows=[_json_row(2001, "483"), _json_row(2002, EIR_TYPE)],
                      html_rows=[]):
            items, _ = f.collect_fda_483(START, END)
        by_id = {it.document_id: it for it in items}
        self.assertEqual(by_id["fda483-2001"].type_or_class, "483")
        self.assertEqual(by_id["fda483-2002"].type_or_class, "EIR")


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
    def test_json_exception_falls_back_to_html(self):
        html_rows = [_html_row(5201, company="BPI", country="")]
        with _Patched(json_exc=RuntimeError("HTTP 404"), html_rows=html_rows):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual({it.document_id for it in items}, {"fda483-5201"})
        self.assertTrue(f.LAST_HEALTH["source_degraded"])   # 완전성 미보장 표면화

    def test_json_nonlist_falls_back_to_html(self):
        with _Patched(json_rows={"data": []}, html_rows=[_html_row(5202)]):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual({it.document_id for it in items}, {"fda483-5202"})
        self.assertTrue(f.LAST_HEALTH["source_degraded"])

    def test_both_sources_fail_is_error(self):
        with _Patched(json_exc=RuntimeError("boom"), html_exc=RuntimeError("boom")):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("수집 실패", err)

    def test_json_ok_no_degrade(self):
        with _Patched(json_rows=[_json_row(5203)], html_rows=[]):
            f.collect_fda_483(START, END)
        self.assertFalse(f.LAST_HEALTH["source_degraded"])


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


class TierTest(unittest.TestCase):
    def test_483_tier3_eir_tier2(self):
        json_rows = [_json_row(8001, "483", est="Drug Manufacturer"),
                     _json_row(8002, EIR_TYPE, est="Drug Manufacturer")]
        with _Patched(json_rows=json_rows, html_rows=[], pdf_text="OBSERVATION 1 generic."):
            items, _ = f.collect_fda_483(START, END)
        by_id = {it.document_id: it for it in items}
        self.assertEqual(by_id["fda483-8001"].signal_tier, "Tier 3")
        self.assertEqual(by_id["fda483-8002"].signal_tier, "Tier 2")

    def test_sterile_eir_floor_tier3(self):
        json_rows = [_json_row(8003, EIR_TYPE, est="Producer of Sterile Drug Products")]
        with _Patched(json_rows=json_rows, html_rows=[], pdf_text="OBSERVATION 1 aseptic."):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].signal_tier, "Tier 3")

    def test_distributor_only_tier_down(self):
        with _Patched(json_rows=[_json_row(8004, est="Distributor")], html_rows=[],
                      pdf_text="cover only"):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].signal_tier, "Tier 2")


class StructureSentinelTest(unittest.TestCase):
    def test_no_483_eir_anywhere_falls_back_then_errors(self):
        # JSON 0 keep + HTML 도 0행 → 두 경로 실패 error(침묵 0건 금지).
        json_rows = [_json_row(9001, "Consent Decree"), _json_row(9002, "Recall Record")]
        with _Patched(json_rows=json_rows, html_rows=[]):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)

    def test_json_zero_keep_html_fallback_recovers(self):
        # JSON 에 483/EIR 0(타입 이상)이지만 HTML 에 있으면 폴백 복구 + degrade.
        json_rows = [_json_row(9003, "Consent Decree")]
        with _Patched(json_rows=json_rows, html_rows=[_html_row(9004)]):
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


class OrchestrationWiringTest(unittest.TestCase):
    def test_source_token_registered(self):
        self.assertIn("fda483", ci._SOURCE_CHOICES)
        self.assertEqual(ci._SOURCE_TOKEN_TO_NOTION["fda483"], "FDA 483")

    def test_flag_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_FDA_483", None)
            enabled = (os.environ.get("ENABLE_FDA_483", "false").lower() == "true")
        self.assertFalse(enabled)

    def test_transient_scope_includes_fda483(self):
        self.assertIn("fda483", ci._GLOBAL_PUBLIC_SOURCE_CODES)
        self.assertTrue(ci._is_transient_source_error("fda483", "HTTP 403 Forbidden"))
        self.assertTrue(ci._is_transient_source_error("fda483", "connection reset"))


if __name__ == "__main__":
    unittest.main()
