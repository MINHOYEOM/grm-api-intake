"""수집 완전성 N2 — 소스별 "완전한 행" 계약(field_contract.py).

합격 조건(설계서 N2): 필수 칸이 비고 근거도 없는 행만 결손으로 센다. 결손은 조용히 빠지지 않고
번호와 함께 집계에 남는다. 범위 밖·블로그·무관 판정은 계약 밖이라 세지 않는다.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import Mock

import field_contract as fc
from grm_common import (SOURCE_ECA, SOURCE_EMA, SOURCE_EU_GMP_NCR, SOURCE_FDA_483, SOURCE_FDA_WL,
                        SOURCE_MFDS, SOURCE_MHRA, SOURCE_WHO)

PHARMA = "Finished Pharmaceutical Manufacturer"


def row(source, doc, raw, *, kind="", url="", meta=None, rid=None):
    return {"source": source, "document_id": doc, "raw_signal_id": rid or f"rs-{doc}", "source_kind": kind,
            "official_url": url, "raw_json": json.dumps(raw), "row_json": json.dumps(meta or {})}


def r483(doc, *, body="FULL", inspectors=("A. Kim",), obs=True, status=None, est=PHARMA):
    raw = {"establishment_type": est}
    if body:
        raw["fda483_body_full"] = body
    if inspectors:
        raw["fda483_inspectors"] = list(inspectors)
    if obs:
        raw["fda_483_observations"] = [{"number": "1", "deficiency": "Procedures not followed", "detail": "x"}]
    if status:
        raw["fda483_text_status"] = status
    return row(SOURCE_FDA_483, doc, raw, kind="483")


IN_SCOPE = {"in_scope": True, "count": 3, "inspectors": False}


class Fda483Test(unittest.TestCase):
    def test_complete(self):
        self.assertEqual(fc.judge("fda483", r483("a"), IN_SCOPE)[0], fc.COMPLETE)

    def test_body_missing_is_incomplete(self):
        status, missing, _ = fc.judge("fda483", r483("a", body=""), IN_SCOPE)
        self.assertEqual((status, missing), (fc.INCOMPLETE, ["fda483_body_full"]))

    def test_inspectors_from_findings_count(self):
        verdict = fc.judge("fda483", r483("a", inspectors=()), {**IN_SCOPE, "inspectors": True})
        self.assertEqual(verdict[0], fc.COMPLETE)
        self.assertEqual(fc.judge("fda483", r483("a", inspectors=()), IN_SCOPE)[1], ["inspectors"])

    def test_404_is_reasoned(self):
        status, _m, reason = fc.judge("fda483", r483("a", body="", status="fetch-fail:HTTP GET (404 Client Error)"),
                                      IN_SCOPE)
        self.assertEqual((status, reason), (fc.REASONED, "원문 접근 불가(404)"))

    def test_scan_no_text_is_not_a_reason(self):
        self.assertEqual(fc.judge("fda483", r483("a", body="", status="scan-no-text"), IN_SCOPE)[0], fc.INCOMPLETE)

    def test_out_of_scope_not_counted(self):
        self.assertIsNone(fc.judge("fda483", r483("a", body=""), {"in_scope": False, "count": 2}))
        self.assertIsNone(fc.judge("fda483", r483("a", body="", status="out-of-scope"), IN_SCOPE))

    def test_no_findings_uses_facility_scope(self):
        egg = r483("egg", body="", obs=False, inspectors=(), est="Shell Egg Producer")
        self.assertIsNone(fc.judge("fda483", egg, None))
        drug = r483("drug", body="", obs=False, inspectors=())
        status, missing, _ = fc.judge("fda483", drug, None)
        self.assertEqual(status, fc.INCOMPLETE)
        self.assertIn("observations", missing)

    def test_eir_not_counted(self):
        self.assertIsNone(fc.judge("fda483", row(SOURCE_FDA_483, "e", {}, kind="EIR"), IN_SCOPE))


class OtherSourcesTest(unittest.TestCase):
    def test_wl(self):
        self.assertEqual(fc.judge("fda_wl", row(SOURCE_FDA_WL, "w", {"wl_body_full": ""}))[0], fc.INCOMPLETE)
        unmarked = row(SOURCE_FDA_WL, "w", {"wl_body_full": "Dear Sir, " * 50})
        self.assertEqual(fc.judge("fda_wl", unmarked)[1], ["wl_violations"])
        filled = row(SOURCE_FDA_WL, "w", {"wl_body_full": "body", "wl_violations": [{"cfr": "211.22"}]})
        self.assertEqual(fc.judge("fda_wl", filled)[0], fc.COMPLETE)

    def test_mfds_gmp(self):
        ok = {"attachment_text": "t", "attachment_deficiency_assessment": "none",
              "gmp_deficiency_table_status": "empty"}
        self.assertEqual(fc.judge("mfds_gmp", row(SOURCE_MFDS, "g", ok, kind="gmp-inspection"))[0], fc.COMPLETE)
        bad = {"attachment_parse_status": "download-fail", "attachment_deficiency_assessment": "unknown"}
        status, missing, _ = fc.judge("mfds_gmp", row(SOURCE_MFDS, "g", bad, kind="gmp-inspection"))
        self.assertEqual(status, fc.INCOMPLETE)
        self.assertEqual(missing, ["attachment_text", "attachment_deficiency_assessment",
                                   "gmp_deficiency_table_status"])
        self.assertIsNone(fc.judge("mfds_gmp", row(SOURCE_MFDS, "r", ok, kind="recall-quality")))

    def test_mfds_admin_recall(self):
        self.assertEqual(fc.judge("mfds_admin", row(SOURCE_MFDS, "a", {"ADM_DISPS_NAME": "업무정지"},
                                                    kind="admin-action"))[1], ["EXPOSE_CONT"])
        self.assertEqual(fc.judge("mfds_recall", row(SOURCE_MFDS, "r", {"RTRVL_RESN": "함량 부적합"},
                                                     kind="recall-quality"))[0], fc.COMPLETE)

    def test_ema(self):
        blank = row(SOURCE_EMA, "e", {"title": "t"}, meta={"qa_relevance": "Pending"})
        self.assertEqual(fc.judge("ema", blank)[0], fc.INCOMPLETE)
        self.assertIsNone(fc.judge("ema", row(SOURCE_EMA, "e", {}, meta={"qa_relevance": "Unrelated"})))
        failed = row(SOURCE_EMA, "e", {"ema_document": {"status": "http-404"}})
        self.assertEqual(fc.judge("ema", failed)[:3:2], (fc.REASONED, "http-404"))

    def test_mhra_blog_not_counted(self):
        blog = row(SOURCE_MHRA, "b", {}, url="https://mhrainspectorate.blog.gov.uk/2026/11/01/x/")
        self.assertIsNone(fc.judge("mhra_alert", blog))
        alert = row(SOURCE_MHRA, "m", {}, url="https://www.gov.uk/drug-device-alerts/class-2-x")
        self.assertEqual(fc.judge("mhra_alert", alert)[0], fc.INCOMPLETE)

    def test_eca_who_ncr(self):
        self.assertEqual(fc.judge("eca", row(SOURCE_ECA, "c", {}))[0], fc.INCOMPLETE)
        self.assertIsNone(fc.judge("who_pir", row(SOURCE_WHO, "n", {"channel": "news"})))
        self.assertEqual(fc.judge("who_pir", row(SOURCE_WHO, "who-whopir-1", {"whopir_report": {"x": 1}}))[0],
                         fc.COMPLETE)
        self.assertEqual(fc.judge("eu_ncr", row(SOURCE_EU_GMP_NCR, "n", {"ncr_nature": ""}))[1], ["ncr_nature"])


class MeasureTest(unittest.TestCase):
    def test_injected_blank_is_counted_with_id(self):
        """빠짐 주입: 완전한 행들 사이에 본문 빈 483 하나를 넣으면 결손 1·번호가 실린다."""
        rows = [r483(f"fda483-{i}") for i in range(5)] + [r483("fda483-99", body="")]
        findings = fc.summarize_findings(
            [{"raw_signal_id": r["raw_signal_id"], "scope_status": "ok", "inspector_names": []} for r in rows])
        got = fc.measure(rows, findings)["fda483"]
        self.assertEqual((got["rows"], got[fc.COMPLETE], got[fc.INCOMPLETE]), (6, 5, 1))
        self.assertEqual(got["incomplete_ids"], ["fda483-99"])
        self.assertEqual(got["by_field"], {"fda483_body_full": 1})

    def test_mfds_row_split_by_kind(self):
        rows = [row(SOURCE_MFDS, "a", {"ADM_DISPS_NAME": "x", "EXPOSE_CONT": "y"}, kind="admin-action"),
                row(SOURCE_MFDS, "r", {}, kind="recall-quality")]
        got = fc.measure(rows, {})
        self.assertEqual(got["mfds_admin"]["rows"], 1)
        self.assertEqual(got["mfds_recall"][fc.INCOMPLETE], 1)
        self.assertEqual(got["mfds_gmp"]["rows"], 0)

    def test_regression_1126_baseline_shape(self):
        """11-26 전 기간 결산의 산식: 범위 = findings 에 ok 한 건, 실사관 = raw 또는 findings 이름."""
        rows = [r483("in-ok", inspectors=()), r483("in-nobody", body=""), r483("oos", body="")]
        findings = fc.summarize_findings([
            {"raw_signal_id": "rs-in-ok", "scope_status": "ok", "inspector_names": ["B. Lee"]},
            {"raw_signal_id": "rs-in-nobody", "scope_status": "ok", "inspector_names": None},
            {"raw_signal_id": "rs-in-nobody", "scope_status": "non_pharma", "inspector_names": []},
            {"raw_signal_id": "rs-oos", "scope_status": "non_pharma", "inspector_names": []},
        ])
        got = fc.measure(rows, findings)["fda483"]
        self.assertEqual(got["rows"], 2)
        self.assertEqual(got[fc.INCOMPLETE], 1)
        self.assertEqual(got["by_field"], {"fda483_body_full": 1})

    def test_ema_pending_kept_out_of_incomplete(self):
        rows = [row(SOURCE_EMA, "e", {"article_excerpt": "x"}, meta={"qa_relevance": "Pending"})]
        payload = fc.build_payload(rows, [], now=datetime(2026, 11, 26, 23, 0, tzinfo=fc.KST))
        self.assertEqual(payload["ema_pending_review"], 1)
        self.assertEqual(payload["total_incomplete"], 0)
        self.assertIn("결손 합계 0", fc.render_markdown(payload))


class SaveTest(unittest.TestCase):
    def _resp(self, body):
        r = Mock()
        r.raise_for_status = Mock()
        r.json = Mock(return_value=body)
        return r

    def test_patch_existing_row(self):
        http = Mock()
        http.patch.return_value = self._resp([{"day": "2026-11-26"}])
        self.assertEqual(fc.save("https://x.supabase.co", "k", "2026-11-26", {"a": 1}, session=http), "updated")
        http.post.assert_not_called()
        self.assertEqual(http.patch.call_args.kwargs["json"], {"incomplete": {"a": 1}})

    def test_insert_placeholder_when_ledger_absent(self):
        http = Mock()
        http.patch.return_value = self._resp([])
        http.post.return_value = self._resp(None)
        self.assertEqual(fc.save("https://x.supabase.co", "k", "2026-11-26", {"a": 1}, session=http), "inserted")
        body = http.post.call_args.kwargs["json"]
        self.assertEqual(body["mfds_admin"], {"measurement_status": "not_measured"})
        self.assertEqual(body["incomplete"], {"a": 1})


if __name__ == "__main__":
    unittest.main()
