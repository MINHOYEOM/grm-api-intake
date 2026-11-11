#!/usr/bin/env python3
"""식약처 GMP 적합판정서 월간 스냅샷(mfds_gmp_cert_snapshot.py · migration 142) 오프라인 계약."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mfds_gmp_cert_snapshot as m  # noqa: E402

RAW = {"BSSH_NM": "(주)예시제약", "FCTR_ADDR": "충청북도 청주시 " + "가" * 3000,
       "KGMP_BGMP_NAME": "완제의약품", "GMP_INGR_MM_GROUP_NAME": None,
       "VLD_PRD_YMD": "2028-05-24", "BIZRNO": "1234567890", "NEW_FIELD": "새 칸"}


def page(items, total, page_no=1, code="00"):
    return {"header": {"resultCode": code, "resultMsg": "NORMAL SERVICE."},
            "body": {"pageNo": page_no, "numOfRows": 100, "totalCount": total, "items": items}}


class RowTests(unittest.TestCase):
    def test_keeps_every_source_field_and_full_text(self):
        row = m.to_row(RAW, "2026-11-11")
        self.assertEqual(row["raw"], RAW)                     # 모르는 칸도 그대로
        self.assertEqual(row["factory_addr"], RAW["FCTR_ADDR"].strip())  # 자르지 않음
        self.assertEqual(row["bizrno"], "1234567890")
        self.assertEqual(row["valid_until"], "2028-05-24")
        self.assertEqual(row["dosage_group"], "")

    def test_cert_id_is_order_independent_and_field_sensitive(self):
        a = m.cert_id(RAW)
        self.assertEqual(a, m.cert_id(dict(reversed(list(RAW.items())))))
        self.assertNotEqual(a, m.cert_id({**RAW, "VLD_PRD_YMD": "2031-05-24"}))
        self.assertRegex(a, r"^gmpcert-[0-9a-f]{40}$")       # 142 의 check 제약과 같은 모양

    def test_parse_ymd(self):
        self.assertEqual(m.parse_ymd("20280524"), "2028-05-24")
        self.assertIsNone(m.parse_ymd("2028-02-30"))
        self.assertIsNone(m.parse_ymd(""))


class FetchTests(unittest.TestCase):
    def _get(self, pages):
        calls = []

        def http_get(url, params):
            calls.append(params["pageNo"])
            r = pages[params["pageNo"]]
            if isinstance(r, Exception):
                raise r
            return r
        return http_get, calls

    def test_fetches_all_pages_from_total_count(self):
        pages = {1: page([{"BSSH_NM": str(i)} for i in range(100)], 250),
                 2: page([{"BSSH_NM": str(i)} for i in range(100)], 250, 2),
                 3: page([{"BSSH_NM": str(i)} for i in range(50)], 250, 3)}
        get, calls = self._get(pages)
        got = m.fetch_all("KEY", http_get=get)
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(len(got["items"]), 250)
        self.assertTrue(got["complete"])

    def test_page_failure_is_incomplete_and_masks_key(self):
        pages = {1: page([{"x": 1}] * 100, 200), 2: RuntimeError("boom serviceKey=KEY")}
        get, _ = self._get(pages)
        got = m.fetch_all("KEY", http_get=get)
        self.assertFalse(got["complete"])
        self.assertNotIn("KEY", " ".join(got["errors"]).replace("serviceKey", ""))

    def test_bad_status_is_error(self):
        get, _ = self._get({1: page([], 0, code="30"), 2: page([], 0, code="30"),
                            3: page([], 0, code="30")})
        got = m.fetch_all("KEY", http_get=get)
        self.assertFalse(got["complete"])
        self.assertTrue(got["errors"])

    def test_hard_max_stops(self):
        pages = {i: page([{"i": i}] * 100, 10_000, i) for i in range(1, 4)}
        get, calls = self._get(pages)
        got = m.fetch_all("KEY", http_get=get, hard_max=3)
        self.assertEqual(calls, [1, 2, 3])
        self.assertFalse(got["complete"])

    def test_probe_report_counts_filled(self):
        rep = m.probe_report([RAW, {**RAW, "BIZRNO": ""}], 2)
        self.assertEqual(rep["keys"]["BIZRNO"], {"present": 2, "filled": 1})
        self.assertEqual(rep["keys"]["GMP_INGR_MM_GROUP_NAME"]["filled"], 0)


class MigrationContract(unittest.TestCase):
    SQL = (ROOT / "web" / "migrations" / "142_mfds_gmp_cert.sql").read_text(encoding="utf-8")

    def test_tables_closed_and_public_rpc_hides_bizrno(self):
        self.assertIn("revoke all on table public.mfds_gmp_cert from public, anon, authenticated", self.SQL)
        pub = self.SQL.split("create or replace function public.mfds_gmp_cert_public()")[1]
        self.assertNotIn("bizrno", pub.split("$$")[1])
        self.assertNotIn("raw", pub.split("$$")[1].replace("raw_", ""))

    def test_link_function_service_role_only(self):
        self.assertIn("revoke all on function public.grm_link_mfds_gmp_cert() from public, anon, authenticated",
                      self.SQL)

    def test_link_methods_match_script_vocabulary(self):
        for meth in ("bizrno", "exact_name", "kr_name_normalized"):
            self.assertIn(f"'{meth}'", self.SQL)
        for note in ("bizrno_conflict", "ambiguous_name", "no_firm_node"):
            self.assertIn(f"'{note}'", self.SQL)

    def test_workflow_never_touches_notion(self):
        wf = (ROOT / ".github" / "workflows" / "grm-mfds-gmp-cert-monthly.yml").read_text(encoding="utf-8")
        self.assertNotIn("NOTION", wf)
        self.assertIn("mfds_gmp_cert_snapshot.py", wf)
        src = (ROOT / "mfds_gmp_cert_snapshot.py").read_text(encoding="utf-8")
        self.assertNotIn("import grm_notion", src)
        self.assertNotIn("collect_intake", src)


if __name__ == "__main__":
    unittest.main()
