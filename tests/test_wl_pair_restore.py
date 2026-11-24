import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import findings_translate_apply_service as translate_apply
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
            translate_apply, "_send_findings_request",
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

    def test_apply_preserves_human_fields_and_is_idempotent(self):
        original = self.live.copy()
        patches = []

        def send(verb, base_url, key, *, params, json_body=None):
            self.assertEqual(params["finding_id"], "eq.wl-finding")
            self.assertEqual(params["raw_signal_id"], "eq.wl-signal")
            self.assertEqual(params["source"], "eq.FDA Warning Letter")
            if verb == "get":
                return 200, [{"finding_text": self.live["finding_text"]}], ""
            self.assertEqual(params["finding_text"], f"eq.{self.live['finding_text']}")
            patches.append(json_body)
            self.live.update(json_body)
            return 200, [{"finding_id": "wl-finding"}], ""

        with patch.object(translate_apply, "_send_findings_request", send), patch.dict(
            os.environ, SUPABASE_URL="https://example.invalid", SUPABASE_SERVICE_ROLE_KEY="test-key"
        ):
            for expected in ("applied", "already_applied"):
                with self.subTest(expected=expected):
                    result = self.run_cli("--apply")
                    self.assertEqual(result["results"][0]["status"], expected)
                    self.assertEqual(self.live, {**original, "finding_text": NEW})
        self.assertEqual(patches, [{"finding_text": NEW}])

    def test_empty_plan_is_noop_without_credentials(self):
        self.document["items"].clear()
        self.plan_path.write_text(json.dumps(self.document), encoding="utf-8")
        with patch.dict(os.environ):
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            for flags in ([], ["--apply"]):
                with self.subTest(flags=flags):
                    self.assertEqual(self.run_cli(*flags), {"apply": bool(flags), "changes": []})

    def assert_unmatched_change_status(self, rows, expected):
        def send(verb, *args, **kwargs):
            self.assertEqual(verb, "get")
            return 200, rows, ""

        change, = wl_pair_restore.load_changes(self.plan_path)
        self.assertEqual(
            wl_pair_restore.apply_change(change, "https://example.invalid", "key", send=send),
            expected,
        )

    def test_missing_row_is_not_patched(self):
        self.assert_unmatched_change_status([], "no_match")

    def test_stale_row_is_not_patched(self):
        self.assert_unmatched_change_status([{"finding_text": "Newer edit"}], "stale")

    def test_concurrent_change_is_not_overwritten(self):
        def send(verb, *args, params, **kwargs):
            if verb == "get":
                return 200, [{"finding_text": OLD}], ""
            self.assertEqual(params["finding_text"], f"eq.{OLD}")
            return 200, [], ""

        change, = wl_pair_restore.load_changes(self.plan_path)
        self.assertEqual(
            wl_pair_restore.apply_change(change, "https://example.invalid", "key", send=send),
            "concurrent_change",
        )
