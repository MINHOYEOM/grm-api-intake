"""EMA 판정표 CLI의 합성 입력과 버전 RPC 요청을 검증한다."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import ema_relevance_apply as cli
import raw_versioning as rv


def _row(suffix, relevance="Pending", *, source="EMA"):
    raw = {
        "article_excerpt": "원문 전체입니다. — " * 5000,
        "nested": {"list": ["처음", "끝"]},
        cli.REVIEW_KEY: {"evidence": "이전 근거"},
    }
    raw_json, sha = rv.canonical_raw(raw)
    return {
        "source": source,
        "document_id": "ema-review-" + suffix,
        "raw_signal_id": "rawsig-" + suffix,
        "raw_sha256": sha,
        "raw_json": raw_json,
        "row_json": json.dumps({"qa_relevance": relevance, "osd_relevance": "Pending"}),
    }


def _decision(doc, verdict="likely", evidence="원문 근거"):
    return {"doc": doc, "verdict": verdict, "evidence": evidence}


class EmaRelevanceApplyTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "decisions.json"
        env = patch.dict(os.environ, {
            "SUPABASE_URL": "https://synthetic.invalid",
            "SUPABASE_SERVICE_ROLE_KEY": "synthetic",
        })
        env.start()
        self.addCleanup(env.stop)
        fetch = patch.object(cli.fsb, "_fetch_all_pages")
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)
        post = patch.object(rv.requests, "post")
        self.post = post.start()
        self.addCleanup(post.stop)
        self.post.return_value = Mock(status_code=200)
        self.post.return_value.json.return_value = {"status": "applied"}

    def run_cli(self, decisions, rows, *flags):
        self.path.write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
        self.fetch.return_value = rows
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            status = cli.main([str(self.path), *flags])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_shows_markdown_before_and_after_without_writing(self):
        row = _row("abcdef")
        other_source = _row("xyz", source="MHRA")
        status, output, _ = self.run_cli(
            [_decision("ema-review", "possible", "근거 | 첫 줄\n둘째 줄")],
            [row, other_source], "--markdown",
        )
        self.assertEqual(status, 0)
        self.assertIn("미리보기", output)
        self.assertIn("| would&#95;apply | Pending | Possible | 이전 근거 | 근거 &#124; 첫 줄<br>둘째 줄 |", output)
        self.assertEqual(self.fetch.call_args.kwargs["extra_params"], {"source": "eq.EMA"})
        self.post.assert_not_called()

    def test_apply_versions_full_evidence_and_changes_only_existing_relevance_field(self):
        row = _row("abcdef")
        evidence = "  근거 — " * 3000 + "끝\n"
        status, output, _ = self.run_cli(
            [_decision(row["raw_signal_id"], "unrelated", evidence)], [row], "--apply",
        )
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["rows"][0]["status"], "applied")
        self.post.assert_called_once()
        args, kwargs = self.post.call_args
        self.assertTrue(args[0].endswith("/rpc/raw_signal_apply_version"))
        rpc = kwargs["json"]
        self.assertEqual(rpc["p_expected_raw_sha256"], row["raw_sha256"])
        self.assertEqual(rpc["p_raw_signal_id"], row["raw_signal_id"])
        record = rpc["p_record"]
        self.assertEqual(json.loads(record["row_json"]), {
            "qa_relevance": "Unrelated", "osd_relevance": "Pending",
        })
        saved_raw = json.loads(record["raw_json"])
        self.assertEqual(saved_raw.pop(cli.REVIEW_KEY), {
            "verdict": "Unrelated", "evidence": evidence, "previous_raw_sha256": row["raw_sha256"],
        })
        old_raw = json.loads(row["raw_json"])
        old_raw.pop(cli.REVIEW_KEY)
        self.assertEqual(saved_raw, old_raw)
        self.assertNotEqual(record["raw_sha256"], row["raw_sha256"])

    def test_unchanged_verdict_skips_even_when_evidence_differs(self):
        row = _row("abcdef", "Likely")
        status, output, _ = self.run_cli([_decision(row["document_id"])], [row], "--apply")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["rows"][0]["status"], "skipped")
        self.post.assert_not_called()

    def test_ambiguous_prefix_stops_at_that_entry(self):
        first, second = _row("abcdef"), _row("xyzdef")
        status, output, _ = self.run_cli(
            [_decision(first["document_id"]), _decision("ema-review"), _decision(second["document_id"])],
            [first, second], "--apply",
        )
        self.assertEqual(status, 1)
        results = json.loads(output)["rows"]
        self.assertEqual([result["status"] for result in results], ["applied", "ambiguous"])
        self.assertEqual(results[-1]["matches"], [first["document_id"], second["document_id"]])
        self.post.assert_called_once()

    def test_missing_and_short_prefix_do_not_write(self):
        row = _row("abcdef")
        status, output, _ = self.run_cli([_decision("ema-revie")], [row], "--apply")
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)["rows"][0]["status"], "not_found")
        self.post.assert_not_called()

    def test_rpc_conflict_or_unchanged_cannot_report_success(self):
        row = _row("abcdef")
        for rpc_status in ("conflict", "unchanged"):
            with self.subTest(rpc_status=rpc_status):
                self.post.return_value.json.return_value = {"status": rpc_status}
                status, output, _ = self.run_cli(
                    [_decision(row["document_id"]), _decision("ema-review")], [row], "--apply",
                )
                self.assertEqual(status, 1)
                self.assertEqual([result["status"] for result in json.loads(output)["rows"]], [rpc_status])

    def test_invalid_input_rejected_before_lookup(self):
        status, _, error = self.run_cli([_decision("ema-review", "maybe")], [], "--apply")
        self.assertEqual(status, 2)
        self.assertIn("1행", error)
        self.fetch.assert_not_called()
        self.post.assert_not_called()
