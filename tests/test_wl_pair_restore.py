import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import requests

import raw_body_followup
import raw_update
import wl_pair_restore


OLD = "Heading\nOld body."
NEW = "Heading\nMoved body."


class TestWlPairRestore(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.plan_path = Path(directory) / "plan.json"
        self.live = {
            "finding_id": "wl-finding",
            "raw_signal_id": "wl-signal",
            "source": "FDA Warning Letter",
            "finding_text": OLD,
            "review_status": "approved",
            "finding_text_ko": "검수된 번역",
            "translation_method": "human",
        }
        pairs = raw_update.pair_findings(
            [self.live], [{**self.live, "finding_text": NEW}],
            {self.live["finding_id"]: self.live},
        )
        _, self.document = raw_body_followup._finish(
            {"findings": {}, "supersede_waiting": {}},
            {"schema_version": raw_update.RESTORE_PLAN_SCHEMA_VERSION, "items": pairs["restore"]},
        )
        self.document["items"][0].update(
            review_status="rejected", finding_text_ko="다른 번역", translation_method="machine"
        )
        self.plan_path.write_text(json.dumps(self.document), encoding="utf-8")

    def run_cli(self, *flags):
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(wl_pair_restore.main(["--plan", str(self.plan_path), *flags]), 0)
        return json.loads(stdout.getvalue())

    def test_default_previews_only_source_text(self):
        with patch.object(
            requests, "post",
            side_effect=AssertionError("dry run contacted the database"),
        ) as send:
            result = self.run_cli()
            send.assert_not_called()
        self.assertEqual(result, {
            "apply": False,
            "changes": [{
                "finding_id": "wl-finding",
                "raw_signal_id": "wl-signal",
                "before_md5": {"finding_text": hashlib.md5(OLD.encode()).hexdigest()},
                "after": {"finding_text": NEW},
            }],
        })

    def test_apply_sends_only_source_text_and_reports_repeat_status(self):
        with patch.object(requests, "post") as post, patch.dict(
            os.environ, SUPABASE_URL="https://example.invalid", SUPABASE_SERVICE_ROLE_KEY="test-key"
        ):
            post.return_value.status_code = 200
            post.return_value.json.side_effect = ["applied", "already_applied"]
            for expected in ("applied", "already_applied"):
                with self.subTest(expected=expected):
                    result = self.run_cli("--apply")
                    self.assertEqual(result["results"][0]["status"], expected)
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["json"], {
                "p_finding_id": self.live["finding_id"],
                "p_raw_signal_id": self.live["raw_signal_id"],
                "p_old_md5": hashlib.md5(OLD.encode()).hexdigest(),
                "p_new_text": NEW,
            })

    def test_empty_plan_is_noop_without_credentials(self):
        self.document["items"].clear()
        self.plan_path.write_text(json.dumps(self.document), encoding="utf-8")
        with patch.dict(os.environ):
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            for flags in ([], ["--apply"]):
                with self.subTest(flags=flags):
                    self.assertEqual(self.run_cli(*flags), {"apply": bool(flags), "changes": []})

    def test_long_body_uses_one_rpc_with_full_replacement_in_json(self):
        old_text = "한" * 4000
        new_text = "새 본문\n" + "글" * 5000
        old_hash = hashlib.md5(old_text.encode("utf-8")).hexdigest()
        self.document["items"][0].update(finding_text_old_md5=old_hash, finding_text=new_text)
        self.plan_path.write_text(json.dumps(self.document), encoding="utf-8")

        with (
            patch.dict(os.environ, SUPABASE_URL="https://example.invalid", SUPABASE_SERVICE_ROLE_KEY="test-key"),
            patch.object(requests, "get", side_effect=AssertionError("unexpected GET")),
            patch.object(requests, "patch", side_effect=AssertionError("unexpected PATCH")),
            patch.object(requests, "post") as post,
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = "applied"
            result = self.run_cli("--apply")

        self.assertEqual(result["results"], [{"finding_id": "wl-finding", "status": "applied"}])
        post.assert_called_once()
        self.assertEqual(post.call_args.args, (
            "https://example.invalid/rest/v1/rpc/grm_wl_pair_restore",
        ))
        self.assertNotIn("params", post.call_args.kwargs)
        self.assertEqual(post.call_args.kwargs["json"], {
            "p_finding_id": "wl-finding",
            "p_raw_signal_id": "wl-signal",
            "p_old_md5": old_hash,
            "p_new_text": new_text,
        })

    def test_reports_rpc_status_for_skipped_updates(self):
        change, = wl_pair_restore.load_changes(self.plan_path)
        for status in ("no_match", "stale", "concurrent_change"):
            with self.subTest(status=status), patch.object(requests, "post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = status
                self.assertEqual(
                    wl_pair_restore.apply_change(change, "https://example.invalid", "key"),
                    status,
                )
                post.assert_called_once()
