"""FDA 483/EIR 수집기 회귀 — WHY-1 #3.

검증: JSON 파싱·Record Type 필터(483/EIR 만)·Publish Date 윈도우·노이즈/수의/기기 게이트·
dedup(media id)·PDF excerpt 추출(483 앵커)·graceful degrade(fetch 실패·403·암호화·앵커
미스)·Tier(483=3·EIR=2·sterile floor)·구조변경 sentinel·flag/토큰 wiring.

무네트워크: http_get_json·http_get_bytes·_extract_pdf_text 스텁.
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


def _row(media_id, rtype="483", company="Acme Pharma Ltd",
         est="Drug Manufacturer", record_date="04/17/2026",
         publish="05/27/2026", state="Florida", fei="1234567"):
    """OII JSON 레코드 1건(probe 채록 구조)."""
    rt_plain = rtype
    return {
        "mid": str(media_id),
        "field_record_date": record_date,
        "field_fein": fei,
        "field_company_name_1": company,
        "field_foia_record_type_1": f'<a href="/media/{media_id}/download">{rt_plain}</a>',
        "field_state_1": state,
        "field_establishment_type_1": est,
        "field_publish_date": publish,
        "field_foia_record_type": rt_plain,
        "changed": "<time>x</time>",
    }


EIR_TYPE = "Establishment Inspection Report (EIR)"


class _StubJson:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        return self.rows


class _StubBytes:
    """http_get_bytes 스텁 — 고정 PDF 바이트 또는 예외."""
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
    """JSON·PDF fetch·텍스트 추출 스텁 + delay 0(무네트워크·고속)."""
    def __init__(self, rows, pdf_text="OBSERVATION 1 Sterile defect.",
                 pdf_status="pdf-ok", bytes_exc=None):
        self.json = _StubJson(rows)
        self.bytes = _StubBytes(raise_exc=bytes_exc)
        self.pdf_text = pdf_text
        self.pdf_status = pdf_status

    def __enter__(self):
        self._p = [
            patch.object(f, "http_get_json", self.json),
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


class RecordTypeFilterTest(unittest.TestCase):
    def test_only_483_and_eir_kept(self):
        rows = [
            _row(1001, "483"),
            _row(1002, EIR_TYPE),
            _row(1003, "483 Response"),
            _row(1004, "Consent Decree"),
            _row(1005, "Recall Record"),
            _row(1006, "Amended 483"),
        ]
        with _Patched(rows):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        kept = {it.document_id for it in items}
        self.assertEqual(kept, {"fda483-1001", "fda483-1002"})

    def test_type_or_class_483_vs_eir(self):
        rows = [_row(2001, "483"), _row(2002, EIR_TYPE)]
        with _Patched(rows):
            items, _ = f.collect_fda_483(START, END)
        by_id = {it.document_id: it for it in items}
        self.assertEqual(by_id["fda483-2001"].type_or_class, "483")
        self.assertEqual(by_id["fda483-2002"].type_or_class, "EIR")


class WindowFilterTest(unittest.TestCase):
    def test_publish_date_window(self):
        rows = [
            _row(3001, "483", publish="05/27/2026"),   # in
            _row(3002, "483", publish="01/17/2024"),   # out (old)
            _row(3003, "483", publish="06/15/2026"),   # out (future)
        ]
        with _Patched(rows):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual({it.document_id for it in items}, {"fda483-3001"})

    def test_empty_window_is_normal(self):
        rows = [_row(3101, "483", publish="01/17/2024")]
        with _Patched(rows):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNone(err)            # 윈도우 내 0건 = 정상(저빈도)


class NoiseGateTest(unittest.TestCase):
    def test_veterinary_dropped(self):
        rows = [_row(4001, "483", company="VetMeds Inc",
                     est="Veterinary Drug Manufacturer")]
        with _Patched(rows):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNone(err)

    def test_medical_device_dropped(self):
        rows = [_row(4002, "483", company="DeviceCo",
                     est="Medical Device Manufacturer")]
        with _Patched(rows, pdf_text="OBSERVATION 1 device packaging issue."):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])

    def test_drug_manufacturer_kept(self):
        rows = [_row(4003, "483", est="Drug Manufacturer")]
        with _Patched(rows):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(len(items), 1)


class DedupTest(unittest.TestCase):
    def test_dedup_by_media_id(self):
        rows = [_row(5001, "483"), _row(5001, "483", company="Dup")]
        with _Patched(rows):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].document_id, "fda483-5001")


class ExcerptTest(unittest.TestCase):
    def test_excerpt_extracted_and_feeds_raw(self):
        rows = [_row(6001, "483")]
        text = ("Cover page FEI 1234567 District Office. This document lists observations. "
                "DURING AN INSPECTION OF YOUR FIRM I/WE OBSERVED: "
                "OBSERVATION 1 Aseptic processing was deficient and media fills failed.")
        with _Patched(rows, pdf_text=text):
            items, _ = f.collect_fda_483(START, END)
        it = items[0]
        self.assertIn("fda483_excerpt", it.raw_payload)
        self.assertTrue(it.raw_payload["fda483_excerpt"].lower().startswith("observation 1"))
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["ok"], 1)

    def test_excerpt_anchor_priority_observation1(self):
        text = ("preamble specifically, foo. During an inspection of your firm bar. "
                "OBSERVATION 1 the real finding.")
        out = f._extract_fda483_excerpt(text)
        self.assertTrue(out.lower().startswith("observation 1"))

    def test_excerpt_no_anchor_returns_empty(self):
        self.assertEqual(f._extract_fda483_excerpt("just a cover page with address"), "")

    def test_graceful_fetch_fail_keeps_item(self):
        rows = [_row(6002, "483")]
        with _Patched(rows, bytes_exc=RuntimeError("HTTP 403 for ...")):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)                       # 항목은 메타 카드로 유지
        self.assertNotIn("fda483_excerpt", items[0].raw_payload)
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["failed"], 1)

    def test_graceful_encrypted_pdf(self):
        rows = [_row(6003, "483")]
        with _Patched(rows, pdf_text="", pdf_status="pdf-encrypted"):
            items, err = f.collect_fda_483(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("fda483_excerpt", items[0].raw_payload)

    def test_graceful_anchor_miss(self):
        rows = [_row(6004, "483")]
        with _Patched(rows, pdf_text="cover page only, no findings section"):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(len(items), 1)
        self.assertNotIn("fda483_excerpt", items[0].raw_payload)
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["failed"], 1)

    def test_excerpt_cap(self):
        rows = [_row(7000 + i, "483", publish="05/2%d/2026" % (i % 9)) for i in range(3)]
        with _Patched(rows), patch.object(f, "FDA483_EXCERPT_MAX_ITEMS", 2):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(f.LAST_HEALTH["fda483_excerpt"]["attempted"], 2)
        self.assertTrue(f.LAST_HEALTH["fda483_excerpt"]["capped"])
        self.assertEqual(len(items), 3)        # cap 은 excerpt 만 제한, 항목은 전부 유지


class TierTest(unittest.TestCase):
    def test_483_tier3_eir_tier2(self):
        rows = [_row(8001, "483", est="Drug Manufacturer"),
                _row(8002, EIR_TYPE, est="Drug Manufacturer")]
        with _Patched(rows, pdf_text="OBSERVATION 1 a generic finding."):
            items, _ = f.collect_fda_483(START, END)
        by_id = {it.document_id: it for it in items}
        self.assertEqual(by_id["fda483-8001"].signal_tier, "Tier 3")
        self.assertEqual(by_id["fda483-8002"].signal_tier, "Tier 2")

    def test_sterile_eir_floor_tier3(self):
        rows = [_row(8003, EIR_TYPE, est="Producer of Sterile Drug Products")]
        with _Patched(rows, pdf_text="OBSERVATION 1 aseptic issue."):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].signal_tier, "Tier 3")

    def test_distributor_only_tier_down(self):
        rows = [_row(8004, "483", est="Distributor")]
        with _Patched(rows, pdf_text="cover only"):
            items, _ = f.collect_fda_483(START, END)
        self.assertEqual(items[0].signal_tier, "Tier 2")   # 483 Tier3 → 하향


class StructureSentinelTest(unittest.TestCase):
    def test_no_483_eir_types_is_error(self):
        rows = [_row(9001, "Consent Decree"), _row(9002, "Recall Record")]
        with _Patched(rows):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("구조 변경", err)

    def test_non_list_json_is_error(self):
        with _Patched({"data": []}):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)

    def test_json_fetch_exception_is_error(self):
        with patch.object(f, "http_get_json", side_effect=RuntimeError("boom")):
            items, err = f.collect_fda_483(START, END)
        self.assertEqual(items, [])
        self.assertIn("수집 실패", err)


class ItemShapeTest(unittest.TestCase):
    def test_item_fields(self):
        rows = [_row(1, "483", company="BPI Labs, LLC", fei="3015156709",
                     state="Florida", est="Outsourcing Facility")]
        with _Patched(rows, pdf_text="OBSERVATION 1 aseptic deficiency."):
            items, _ = f.collect_fda_483(START, END)
        it = items[0]
        self.assertEqual(it.source, "FDA 483")
        self.assertEqual(it.official_url, "https://www.fda.gov/media/1/download")
        self.assertEqual(it.site_country, "Florida")
        self.assertEqual(it.raw_payload["fei_number"], "3015156709")
        self.assertEqual(it.date_iso, "2026-05-27")
        self.assertEqual(it.region_jurisdiction, "USA (FDA)")


class OrchestrationWiringTest(unittest.TestCase):
    def test_source_token_registered(self):
        self.assertIn("fda483", ci._SOURCE_CHOICES)
        self.assertEqual(ci._SOURCE_TOKEN_TO_NOTION["fda483"], "FDA 483")

    def test_flag_default_off(self):
        # ENABLE_FDA_483 미설정 + --sources 미지정 → 수집 비활성(orchestrator 게이트).
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
