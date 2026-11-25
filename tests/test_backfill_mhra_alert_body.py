import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import requests

import backfill_mhra_alert_body as backfill


IDS = ["aaf399958637", "b8a0bd4d4f4c", "ef50339bb834"]
CREDS = {"SUPABASE_URL": "https://example.invalid", "SUPABASE_SERVICE_ROLE_KEY": "fake"}
LONG_BODY = "설명" * 300 + " full text ends here"


def _row(short_id, *, tail="a" * 12, before="이전 본문"):
    return {
        "raw_signal_id": "rawsig-" + tail + short_id,
        "document_id": short_id,
        "raw_sha256": "old-hash",
        "source": backfill.SOURCE_MHRA,
        "row_json": '{"metadata":true}',
        "raw_json": json.dumps({"mhra_alert_body": before, "title": "alert"}),
        "official_url": backfill.MHRA_ALERT_PREFIX + short_id,
    }


def _response(body):
    response = Mock()
    response.json.return_value = {"details": {"body": body}}
    return response


class BackfillMHRAAlertBodyTests(unittest.TestCase):
    def test_dry_run_reports_three_ids_and_never_writes(self):
        rows = [_row(rid) for rid in IDS]
        output = io.StringIO()
        with (patch.dict("os.environ", CREDS), redirect_stdout(output),
              patch.object(backfill.fsb, "_fetch_all_pages", return_value=rows) as load,
              patch("collect_intake.requests.get", side_effect=[
                  _response(f"<p>{LONG_BODY}</p>"), _response("<p> </p>"), requests.Timeout(),
              ]) as get, patch.object(backfill.rv, "replace_raw") as write):
            code = backfill.main(["--ids", *IDS])

        self.assertEqual(code, 1)
        write.assert_not_called()
        self.assertEqual(load.call_args.kwargs["extra_params"], {
            "source": f"eq.{backfill.SOURCE_MHRA}",
            "or": "(" + ",".join(f"document_id.eq.{rid}" for rid in IDS) + ")",
        })
        self.assertEqual([call.args[0] for call in get.call_args_list], [
            f"https://www.gov.uk/api/content/drug-device-alerts/{rid}" for rid in IDS
        ])
        report = output.getvalue()
        self.assertIn("dry-run", report)
        for rid in IDS:
            self.assertIn(f"| {rid} |", report)
        self.assertIn(f'| 5 | "이전 본문" | {len(LONG_BODY)} | "{LONG_BODY[:200]}" | 변경 예정 |', report)
        self.assertNotIn("full text ends here", report)
        self.assertIn("본문 비어 있음", report)
        self.assertIn("Content API 조회 실패 (Timeout)", report)

    def test_apply_preserves_all_text_and_uses_version_write(self):
        rows = [_row(IDS[0]), _row(IDS[1]), _row(IDS[2], before=LONG_BODY)]
        response = Mock(status_code=200)
        response.json.return_value = {"status": "applied"}
        output = io.StringIO()
        with (patch.dict("os.environ", CREDS), redirect_stdout(output),
              patch.object(backfill, "_fetch_rows", return_value=rows),
              patch.object(backfill, "_read_mhra_alert_body", side_effect=[
                  (LONG_BODY, ""), (" ", ""), (LONG_BODY, ""),
              ]), patch.object(backfill.rv.requests, "post", return_value=response) as post):
            code = backfill.main(["--ids", rows[0]["raw_signal_id"], *IDS, "--apply"])

        self.assertEqual(code, 1)
        post.assert_called_once()
        self.assertTrue(post.call_args.args[0].endswith("/rpc/raw_signal_apply_version"))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["p_raw_signal_id"], rows[0]["raw_signal_id"])
        self.assertEqual(payload["p_expected_raw_sha256"], rows[0]["raw_sha256"])
        self.assertEqual(payload["p_record"]["row_json"], rows[0]["row_json"])
        self.assertEqual(json.loads(payload["p_record"]["raw_json"]), {
            "mhra_alert_body": LONG_BODY, "title": "alert",
        })
        self.assertIn("저장 완료", output.getvalue())
        self.assertIn("변경 없음", output.getvalue())

    def test_missing_and_ambiguous_ids_skip_the_content_api(self):
        rows = [_row(IDS[0]), _row(IDS[0], tail="b" * 12)]
        output = io.StringIO()
        with (patch.dict("os.environ", CREDS), redirect_stdout(output),
              patch.object(backfill, "_fetch_rows", return_value=rows),
              patch.object(backfill, "_read_mhra_alert_body") as fetch):
            self.assertEqual(backfill.main(["--ids", *IDS[:2]]), 1)
        fetch.assert_not_called()
        self.assertIn("ID가 여러 행과 일치", output.getvalue())
        self.assertIn("일치하는 행 없음", output.getvalue())

    def test_save_conflict_is_reported(self):
        write = Mock(side_effect=backfill.rv.ApplyVersionError("conflict"))
        result = backfill._compare(_row(IDS[0]), lambda url: (LONG_BODY, ""), write)
        self.assertEqual(result, ("이전 본문", LONG_BODY, "저장 실패 (conflict)", False))
