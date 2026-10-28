"""483 회수 `recoverable` 모드 회귀 (2026-10-27 FDA 결손 감사).

예약 회수(`collection-capped`)는 상한 표식(not-attempted·scan-ocr-budget)만 골랐다. 그런데
다시 읽으면 본문이 나올 수 있는 결손이 둘 더 있었다 — 어떤 예약 잡의 후보에도 안 걸려
석 달째 본문 없이 남아 있던 것(raw_signals 실측: 엔진 부재 8건·일시 fetch 실패 2건).

또 `collection-capped` 는 회수 실행이 엔진 부재 상태를 다시 받으면 그걸 **확정 사유**로
보고 표식을 바꿔, 그 문서를 후보에서 영영 빼 버렸다(엔진 없는 실행 1회가 결손을 영구화).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 형제 테스트의 픽스처 재사용

import backfill_483_ocr_recovery as rec
from test_483_ocr_recovery import _nrow, _row, _run  # noqa: E402


class RecoverablePredicateTest(unittest.TestCase):
    def test_targets_every_retryable_marker(self):
        for status in ("not-attempted", "scan-ocr-budget", "scan-ocr-unavailable:no tessdata",
                       "fetch-fail:HTTP GET final failure: 503 Server Error",
                       "fetch-fail:ReadTimeout"):
            self.assertTrue(rec.is_recoverable_row({"fda483_text_status": status}), status)

    def test_skips_definitive_reasons(self):
        for status in ("scan-no-text", "scan-ocr-empty", "pdf-encrypted",
                       "fetch-fail:HTTP GET final failure: (404 Client Error)", "pdf-ok", ""):
            self.assertFalse(rec.is_recoverable_row({"fda483_text_status": status}), status)
        self.assertFalse(rec.is_recoverable_row({"fda483_excerpt": "OBSERVATION 1"}))

    def test_superset_of_collection_capped(self):
        for status in rec.CAPPED_TEXT_STATUSES:
            row = {"fda483_text_status": status}
            self.assertTrue(rec.is_collection_capped_row(row))
            self.assertTrue(rec.is_recoverable_row(row))

    def test_mode_dispatch_and_cli(self):
        row = {"fda483_text_status": "fetch-fail:ReadTimeout"}
        self.assertTrue(rec.is_recovery_candidate(row, rec.MODE_RECOVERABLE))
        self.assertFalse(rec.is_recovery_candidate(row, rec.MODE_COLLECTION_CAPPED))
        self.assertEqual(
            rec.build_arg_parser().parse_args(["--mode", "recoverable"]).mode,
            rec.MODE_RECOVERABLE)

    def test_fetch_narrows_on_server(self):
        seen = {}

        def fake_pages(base, key, table, **kw):
            seen.update(kw.get("extra_params") or {})
            return []

        with mock.patch.object(rec.fsb, "_fetch_all_pages", fake_pages):
            rec._fetch_raw_signals("https://x", "k", mode=rec.MODE_RECOVERABLE)
        for token in ("not-attempted", "scan-ocr-budget", "scan-ocr-unavailable", "fetch-fail"):
            self.assertIn(f"raw_json.like.*{token}*", seen.get("or", ""))


class RecoverableRunTest(unittest.TestCase):
    def _run(self, status, **over):
        import findings_store
        rid = findings_store.raw_signal_from_intake_item(
            rec._rebuild_item(_nrow(), "", status), collected_at="")["raw_signal_id"]
        over.setdefault("rows", [_row(rid, status=status)])
        return _run(mode=rec.MODE_RECOVERABLE, **over)

    def test_transient_fetch_fail_row_is_recovered(self):
        report, code, upsert, _ = self._run(
            "fetch-fail:HTTP GET final failure: 503 Server Error", dry_run=False)
        self.assertEqual(code, 0)
        self.assertEqual(report.marked, 1)
        self.assertEqual(report.recovered, 1)
        self.assertNotIn("fda483_text_status", upsert.call_args[0][2]["raw_json"])

    def test_definitive_result_replaces_marker(self):
        report, _code, upsert, _ = self._run(
            "scan-ocr-unavailable:no tessdata", dry_run=False,
            fetch_text=lambda url: ("", "scan-ocr-empty"))
        self.assertEqual(report.status_resolved, 1)
        upsert.assert_called_once()

    def test_engine_missing_again_keeps_marker(self):
        """회수 러너에도 엔진이 없으면 표식을 그대로 둔다 — 다음 실행이 다시 시도한다."""
        report, _code, upsert, _ = self._run(
            "not-attempted", dry_run=False,
            fetch_text=lambda url: ("", "scan-ocr-unavailable:no tessdata"))
        self.assertEqual(report.status_resolved, 0)
        upsert.assert_not_called()


class CollectionCappedEngineMissingTest(unittest.TestCase):
    """종전 `collection-capped` 도 엔진 부재를 확정 사유로 보지 않는다(결손 영구화 차단)."""

    def test_engine_missing_is_not_definitive(self):
        self.assertFalse(rec._is_definitive_empty("scan-ocr-unavailable:no tessdata"))
        self.assertTrue(rec._is_definitive_empty("scan-no-text"))


if __name__ == "__main__":
    unittest.main()


class OutOfScopeMarkerTest(unittest.TestCase):
    """[2026-10-28 첫 회수 실측] 다시 읽어 보니 범위 밖이면 상한 표식을 `out-of-scope` 로 바꾼다.

    바꾸지 않으면 그 문서는 회수 후보로 남아 **매일 다시 받아 OCR** 된다(152건 중 71건이
    범위 밖이었고, 상한 표식 38건이 그대로 남았다)."""

    def _run(self, mode, **over):
        import findings_store
        rid = findings_store.raw_signal_from_intake_item(
            rec._rebuild_item(_nrow(), "", "not-attempted"), collected_at="")["raw_signal_id"]
        real_rebuild = rec._rebuild_item

        def rebuild(nrow, text, status):
            # 본문이 있으면 범위 밖 판정(None), 빈 본문 재구성은 정상 — 실제 게이트와 같은 모양
            return None if text else real_rebuild(nrow, text, status)

        over.setdefault("rows", [_row(rid, status="not-attempted")])
        return _run(mode=mode, dry_run=False, rebuild_item=rebuild, **over)

    def test_recoverable_marks_out_of_scope(self):
        report, _code, upsert, _ = self._run(rec.MODE_RECOVERABLE)
        self.assertEqual(report.gate_dropped, 1)
        self.assertEqual(report.status_resolved, 1)
        raw = rec._json_object(upsert.call_args[0][2]["raw_json"])
        self.assertEqual(raw["fda483_text_status"], rec.OUT_OF_SCOPE_STATUS)
        self.assertFalse(rec.is_recoverable_row(raw))          # 다음 실행 후보에서 빠진다
        self.assertFalse(rec.is_collection_capped_row(raw))

    def test_collection_capped_marks_too(self):
        report, _code, upsert, _ = self._run(rec.MODE_COLLECTION_CAPPED)
        self.assertEqual(report.status_resolved, 1)
        upsert.assert_called_once()

    def test_other_modes_do_not_write(self):
        report, _code, upsert, _ = self._run(rec.MODE_MISSING_OBSERVATIONS)
        self.assertEqual(report.gate_dropped, 1)
        upsert.assert_not_called()
