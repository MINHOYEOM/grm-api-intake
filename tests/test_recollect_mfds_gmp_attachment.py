#!/usr/bin/env python3
"""식약처 GMP 첨부 재수집 CLI — 순수 부분·가드·멱등(네트워크·DB 없음)."""
import json
from contextlib import redirect_stdout
from io import StringIO
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mfds_gmp_inspection as g  # noqa: E402
import recollect_mfds_gmp_attachment as r  # noqa: E402

_NONE_TEXT = ("의약품 제조소 GMP 정기실태조사 결과 평가 결과 지적(보완)사항(Deficiencies) 없음 "
              + "본문 " * 20)


def _att(text=_NONE_TEXT, status="pdf-ok", deficiency=None, rows=None, table_status="empty",
         error=""):
    deficiency = deficiency or (g._assess_deficiency(text) if text else "unknown")
    return g._AttachmentParse(
        status=status, file_format="pdf", text=text, deficiency=deficiency,
        deficiency_excerpt=g._extract_deficiency_excerpt(text), bytes_downloaded=1234,
        pages=2, deficiencies=rows or [], deficiency_table_status=table_status, error=error)


def _row(payload: dict, rid="rawsig-1"):
    return {"raw_signal_id": rid, "document_id": "gmpinspect-1PvoSBbO1to",
            "source_kind": "gmp-inspection", "raw_sha256": "sha0",
            "raw_json": json.dumps(payload, ensure_ascii=False)}


# 천일가스형: 목록 키만 있고 첨부를 한 번도 못 받은 행
_BARE = {"seq": "1", "doc_id": "1PvoSBbO1to", "manufacturer": "(주)천일가스",
         "download_url": "https://nedrug.mfds.go.kr/cmn/edms/down/1PvoSBbO1to",
         "registered_date": "2026-05-29"}


class NormalizeIdsTest(unittest.TestCase):
    def test_prefix_dedupe_and_blanks(self):
        self.assertEqual(
            r.normalize_ids("1PvoSBbO1to, gmpinspect-1PvoSBbO1to ,,gmpinspect-1PutNZjPRZ9\n1P1"),
            ["1PvoSBbO1to", "1PutNZjPRZ9", "1P1"])

    def test_empty(self):
        self.assertEqual(r.normalize_ids(""), [])


class PlanRecollectTest(unittest.TestCase):
    def test_bare_row_gets_attachment_keys_and_keeps_list_keys(self):
        plan = r.plan_recollect(_row(_BARE), _att())
        self.assertEqual(plan["kind"], "update")
        out = plan["raw_json"]
        self.assertEqual(out["manufacturer"], "(주)천일가스")          # 목록 키 보존
        self.assertEqual(out["attachment_text"], _NONE_TEXT)
        self.assertEqual(out["attachment_deficiency_assessment"], "none")
        self.assertEqual(out["gmp_deficiency_table_status"], "empty")
        self.assertFalse(out["manual_review_required"])

    def test_same_function_as_collector(self):
        """수집기 `attachment_payload_fields` 와 같은 키를 쓴다(복제 금지)."""
        att = _att()
        expected = g.attachment_payload_fields(att, g.attachment_manual_review(att))
        out = r.plan_recollect(_row(_BARE), att)["raw_json"]
        for k, v in expected.items():
            self.assertEqual(out[k], v, k)

    def test_download_fail_is_no_text_not_written(self):
        plan = r.plan_recollect(_row(_BARE), _att(text="", status="download-fail",
                                                   table_status="", error="HTTP 403"))
        self.assertEqual(plan["kind"], "no_text")
        self.assertEqual(plan["status"], "download-fail")

    def test_shorter_new_text_is_blocked(self):
        """★85637 사례 — 옛 본문이 있는데 새 본문이 더 짧으면 쓰지 않는다."""
        old = dict(_BARE, attachment_text="가" * 900,
                   attachment_deficiency_assessment="unknown")
        plan = r.plan_recollect(_row(old), _att(text="나" * 300, deficiency="unknown"))
        self.assertEqual(plan["kind"], "length_regress")
        self.assertEqual((plan["old_len"], plan["new_len"]), (900, 300))

    def test_longer_new_text_is_allowed(self):
        old = dict(_BARE, attachment_text="짧은 본문",
                   attachment_deficiency_assessment="unknown")
        self.assertEqual(r.plan_recollect(_row(old), _att())["kind"], "update")

    def test_known_assessment_change_is_blocked(self):
        old = dict(_BARE, attachment_text="x", attachment_deficiency_assessment="present")
        plan = r.plan_recollect(_row(old), _att())          # 새 판정 none
        self.assertEqual(plan["kind"], "assess_regress")
        self.assertEqual((plan["old"], plan["new"]), ("present", "none"))

    def test_unknown_to_known_is_allowed(self):
        old = dict(_BARE, attachment_text="x", attachment_deficiency_assessment="unknown")
        self.assertEqual(r.plan_recollect(_row(old), _att())["kind"], "update")

    def test_rerun_is_idempotent(self):
        first = r.plan_recollect(_row(_BARE), _att())
        second = r.plan_recollect(_row(first["raw_json"]), _att())
        self.assertEqual(second["kind"], "unchanged")

    def test_stale_parse_error_key_is_removed(self):
        old = dict(_BARE, attachment_parse_error="옛 오류", attachment_text="x",
                   attachment_deficiency_assessment="unknown")
        out = r.plan_recollect(_row(old), _att())["raw_json"]
        self.assertNotIn("attachment_parse_error", out)

    def test_raw_json_text_column_and_dict_both_accepted(self):
        row = {"raw_signal_id": "a", "raw_sha256": "s", "raw_json": dict(_BARE)}
        self.assertEqual(r.plan_recollect(row, _att())["kind"], "update")


class RunTest(unittest.TestCase):
    def _run(self, apply, rows, attachments, apply_fn=None):
        calls = []

        def default_apply(base, key, row, payload):
            calls.append((row["raw_signal_id"], payload))

        report = r.run(
            list(attachments), apply, rows=rows,
            parse_attachment=lambda d: attachments[d],
            apply_fn=apply_fn or default_apply, creds=("http://x", "k"))
        return report, calls

    def test_dry_run_never_writes(self):
        report, calls = self._run(False, {"A": _row(_BARE)}, {"A": _att()})
        self.assertEqual((report.mode, report.updated, report.applied), ("dry_run", 1, 0))
        self.assertEqual(calls, [])

    def test_apply_writes_only_updates_and_reports_guards(self):
        rows = {"A": _row(_BARE, "rid-A"),
                "B": _row(_BARE, "rid-B"),
                "C": _row(dict(_BARE, attachment_text="가" * 900), "rid-C")}
        atts = {"A": _att(), "B": _att(text="", status="download-fail", table_status=""),
                "C": _att(text="나" * 10, deficiency="unknown")}
        report, calls = self._run(True, rows, atts)
        self.assertEqual([c[0] for c in calls], ["rid-A"])
        self.assertEqual(report.applied, 1)
        self.assertEqual(report.no_text, ["B"])
        self.assertEqual(report.length_regress_blocked, ["C"])
        self.assertEqual(report.guard_hits, 2)

    def test_missing_row_is_reported(self):
        report, _ = self._run(False, {}, {"Z": _att()})
        self.assertEqual(report.not_found, ["Z"])

    def test_apply_failure_is_counted_not_raised(self):
        def boom(base, key, row, payload):
            raise RuntimeError("conflict")
        report, _ = self._run(True, {"A": _row(_BARE)}, {"A": _att()}, apply_fn=boom)
        self.assertEqual((report.applied, report.failed), (0, 1))
        self.assertIn("conflict", report.errors[0])


class MainTest(unittest.TestCase):
    def test_missing_attachment_fails_then_next_approved_run_fills_it(self):
        doc_id = _BARE["doc_id"]
        row = _row(_BARE)

        def save_attachment(base, key, stored_row, payload):
            stored_row["raw_json"] = json.dumps(payload, ensure_ascii=False)

        output = StringIO()
        with (
            patch.dict(os.environ, {"ENABLE_GMP_DEFICIENCY_TABLE": "true"}),
            patch.object(r, "_creds", return_value=("fake-db", "fake-key")),
            patch.object(r, "fetch_rows", return_value={doc_id: row}),
            patch.object(
                g, "_parse_attachment",
                side_effect=[_att(text="", status="download-fail"), _att()],
            ),
            patch.object(r, "_apply", side_effect=save_attachment) as apply,
            redirect_stdout(output),
        ):
            self.assertEqual(r.main(["--ids", doc_id]), 1)
            self.assertEqual(json.loads(output.getvalue())["no_text"], [doc_id])
            apply.assert_not_called()
            self.assertNotIn("attachment_text", json.loads(row["raw_json"]))

            output.seek(0)
            output.truncate()
            self.assertEqual(r.main(["--ids", doc_id, "--apply"]), 0)
            self.assertEqual(json.loads(output.getvalue())["applied"], 1)
            apply.assert_called_once()
            self.assertEqual(json.loads(row["raw_json"])["attachment_text"], _NONE_TEXT)

    def test_empty_ids_is_usage_error(self):
        self.assertEqual(r.main(["--ids", " , "]), 2)

    def test_too_many_ids_is_usage_error(self):
        ids = ",".join(f"d{i}" for i in range(r._MAX_IDS + 1))
        self.assertEqual(r.main(["--ids", ids]), 2)


if __name__ == "__main__":
    unittest.main()
