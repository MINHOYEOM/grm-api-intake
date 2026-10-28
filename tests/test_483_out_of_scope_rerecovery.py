"""`out-of-scope` 표식 행의 정식 재회수 경로 (2026-10-28).

`out-of-scope` 는 회수가 게이트 드롭을 보고 찍는 **확정** 표식이라 어떤 모드의 후보에도 걸리지
않는다(그래야 예약 실행이 진짜 범위 밖 문서를 매일 다시 받지 않는다). 그런데 게이트가 양식
상용구에 속아 의약품 483 을 드롭했고(test_fda483_gate_boilerplate), 그 오판 표식을 되돌릴
길이 없었다. `--doc-ids` 로 문서를 **이름으로 지목**하면 상한 표식 모드에서 그 행도 다시 읽는다.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 형제 테스트의 픽스처 재사용

import backfill_483_ocr_recovery as rec
from test_483_ocr_recovery import _nrow, _row, _run  # noqa: E402

_OOS = {"fda483_text_status": rec.OUT_OF_SCOPE_STATUS}


class ExplicitCandidateTest(unittest.TestCase):

    def test_out_of_scope_is_not_a_candidate_by_default(self):
        for mode in rec.RECOVERY_MODES:
            if mode == rec.MODE_MISSING_OBSERVATIONS:
                continue   # 관찰 키 부재만 보는 모드 — 표식과 무관
            self.assertFalse(rec.is_recovery_candidate(_OOS, mode), mode)

    def test_explicit_opens_capped_modes_only(self):
        self.assertTrue(rec.is_recovery_candidate(_OOS, rec.MODE_RECOVERABLE, explicit=True))
        self.assertTrue(rec.is_recovery_candidate(_OOS, rec.MODE_COLLECTION_CAPPED, explicit=True))
        self.assertFalse(rec.is_recovery_candidate(_OOS, rec.MODE_OCR_UNAVAILABLE, explicit=True))

    def test_explicit_does_not_open_other_definitive_reasons(self):
        for status in ("scan-no-text", "fetch-fail:HTTP GET final failure: (404 Client Error)"):
            self.assertFalse(rec.is_recovery_candidate(
                {"fda483_text_status": status}, rec.MODE_RECOVERABLE, explicit=True), status)

    def test_server_narrowing_includes_marker_only_when_explicit(self):
        seen = {}

        def fake_pages(base, key, table, **kw):
            seen.clear()
            seen.update(kw.get("extra_params") or {})
            return []

        with mock.patch.object(rec.fsb, "_fetch_all_pages", fake_pages):
            for mode in (rec.MODE_RECOVERABLE, rec.MODE_COLLECTION_CAPPED):
                rec._fetch_raw_signals("https://x", "k", mode=mode)
                self.assertNotIn(rec.OUT_OF_SCOPE_STATUS, seen.get("or", ""), mode)
                rec._fetch_raw_signals("https://x", "k", mode=mode, include_out_of_scope=True)
                self.assertIn(f"raw_json.like.*{rec.OUT_OF_SCOPE_STATUS}*", seen.get("or", ""),
                              mode)


class RerecoveryRunTest(unittest.TestCase):

    def _rows(self):
        import findings_store
        rid = findings_store.raw_signal_from_intake_item(
            rec._rebuild_item(_nrow(), "", rec.OUT_OF_SCOPE_STATUS),
            collected_at="")["raw_signal_id"]
        return rid, [_row(rid, status=rec.OUT_OF_SCOPE_STATUS)]

    def test_scheduled_run_skips_out_of_scope(self):
        _rid, rows = self._rows()
        fetched = []
        report, _code, upsert, _ = _run(
            mode=rec.MODE_RECOVERABLE, dry_run=False, rows=rows,
            fetch_text=lambda url: fetched.append(url) or ("", "x"))
        self.assertEqual(report.marked, 0)
        self.assertEqual(fetched, [])
        upsert.assert_not_called()

    def test_doc_ids_rerecovers_and_clears_marker(self):
        _rid, rows = self._rows()
        seen_kwargs = {}

        def fetch(*a, **k):
            seen_kwargs.update(k)
            return list(rows)

        report, code, upsert, _ = _run(
            mode=rec.MODE_RECOVERABLE, dry_run=False, rows=rows,
            fetch_raw_signals=fetch, doc_ids=["fda483-555001"])
        self.assertEqual(code, 0)
        self.assertTrue(seen_kwargs.get("include_out_of_scope"))
        self.assertEqual(report.candidates, 1)
        self.assertEqual(report.recovered, 1)
        raw = rec._json_object(upsert.call_args[0][2]["raw_json"])
        self.assertNotIn("fda483_text_status", raw)       # 본문 확보 → 표식 해제
        self.assertTrue(raw.get("fda_483_observations"))

    def test_doc_ids_still_out_of_scope_keeps_marker(self):
        """다시 읽어도 범위 밖이면 표식은 out-of-scope 그대로(멱등)."""
        _rid, rows = self._rows()
        real_rebuild = rec._rebuild_item
        report, _code, upsert, _ = _run(
            mode=rec.MODE_RECOVERABLE, dry_run=False, rows=rows, doc_ids=["fda483-555001"],
            rebuild_item=lambda n, t, s: None if t else real_rebuild(n, t, s))
        self.assertEqual(report.gate_dropped, 1)
        raw = rec._json_object(upsert.call_args[0][2]["raw_json"])
        self.assertEqual(raw["fda483_text_status"], rec.OUT_OF_SCOPE_STATUS)


if __name__ == "__main__":
    unittest.main()
