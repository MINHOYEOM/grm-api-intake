import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import mark_483_out_of_scope as tool


DOC = "fda483-101"
QUOTE = "The facility manufactures medical devices."
ENTRY = {"doc": DOC, "evidence": QUOTE, "category": "device"}
BODY = f"FOOD AND DRUG ADMINISTRATION\n{QUOTE}"


class Mark483OutOfScopeTests(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "fda483_body_full": BODY,
            "fda483_text_status": "not-attempted",
            "fda_483_observations": [{"text": "보존 — " * 4000}],
        }
        self.row = {
            "document_id": DOC,
            "source": "FDA 483",
            "raw_signal_id": "raw-101",
            "raw_sha256": "read-time-hash",
            "row_json": '{"metadata":"preserve verbatim — 한글"}',
        }
        self.fetch = self.enterContext(patch.object(tool.fsb, "_fetch_all_pages"))
        self.post = self.enterContext(patch.object(tool.rv.requests, "post"))
        self.post.return_value = Mock(status_code=200, **{"json.return_value": {"status": "applied"}})
        self.enterContext(patch.object(tool, "resolve_supabase_service_credentials",
                                       return_value=("https://example.invalid", "fake-key")))
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_cli(self, *flags, entries=None):
        self.row["raw_json"] = json.dumps(self.raw, ensure_ascii=False)
        self.fetch.return_value = [self.row]
        path = self.directory / "review.json"
        path.write_text(json.dumps([ENTRY] if entries is None else entries), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as output:
            code = tool.main([str(path), *flags])
        return code, list(csv.DictReader(io.StringIO(output.getvalue()), delimiter="\t"))

    def test_default_previews_before_and_after_without_writing(self):
        code, (line,) = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual((line["before"], line["after"], line["outcome"]),
                         ("not-attempted", "out-of-scope", "would_apply"))
        self.assertIn(QUOTE, line["detail"])
        self.assertEqual(self.fetch.call_args.kwargs["extra_params"],
                         {"source": "eq.FDA 483", "document_id": f"eq.{DOC}"})
        self.post.assert_not_called()

    def test_apply_writes_only_through_version_rpc_and_preserves_full_raw(self):
        quote = "The facility  manufactures\nmedical devices."
        self.raw["fda483_body_full"] += "\n" + ("Entire document — 원문.\n" * 4000)
        code, (line,) = self.run_cli("--apply", entries=[{**ENTRY, "evidence": quote}])
        self.assertEqual((code, line["outcome"]), (0, "applied"))
        self.post.assert_called_once()
        self.assertTrue(self.post.call_args.args[0].endswith("/rpc/raw_signal_apply_version"))
        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["p_raw_signal_id"], self.row["raw_signal_id"])
        self.assertEqual(payload["p_expected_raw_sha256"], self.row["raw_sha256"])
        self.assertEqual(payload["p_change_kind"], "repair")
        self.assertEqual(payload["p_diff_summary"],
                         {"reason": "483_status_marker", "category": "device", "evidence": quote})
        record = payload["p_record"]
        self.assertEqual(record["row_json"], self.row["row_json"])
        self.assertEqual(json.loads(record["raw_json"]), {**self.raw, "fda483_text_status": "out-of-scope"})

    def test_already_marked_skips(self):
        self.raw = {"fda483_text_status": "out-of-scope"}
        code, (line,) = self.run_cli("--apply")
        self.assertEqual((code, line["outcome"]), (0, "skipped"))
        self.assertEqual((line["before"], line["after"]), ("out-of-scope", "out-of-scope"))
        self.post.assert_not_called()

    def test_blank_evidence_rejects_before_lookup(self):
        code, (line,) = self.run_cli("--apply", entries=[{**ENTRY, "evidence": " \n\t"}])
        self.assertEqual((code, line["outcome"], line["detail"]), (1, "rejected", "missing evidence"))
        self.fetch.assert_not_called()
        self.post.assert_not_called()

    def test_drug_clues_in_evidence_or_anywhere_in_full_text_reject(self):
        for clue in ("Drug", "OTC Drug", "21 CFR 210/211", "21 C.F.R. Part 211", "§ 211.22"):
            for location in ("evidence", "full text"):
                with self.subTest(clue=clue, location=location):
                    evidence = QUOTE if location == "full text" else f"{QUOTE} {clue}"
                    self.raw["fda483_body_full"] = BODY + "\n" + ("Inspection details.\n" * 1000) + clue
                    code, (line,) = self.run_cli("--apply", entries=[{**ENTRY, "evidence": evidence}])
                    self.assertEqual((code, line["outcome"]), (1, "rejected"))
                    self.assertEqual(line["before"], line["after"])
                    self.assertIn(f"drug clue in {location}", line["detail"])
        self.post.assert_not_called()

    def test_missing_full_text_and_unverified_quote_reject(self):
        for body, detail in (("", "missing full text"), ("No matching quote.", "quote not found")):
            with self.subTest(body=body):
                self.raw["fda483_body_full"] = body
                code, (line,) = self.run_cli()
                self.assertEqual((code, line["outcome"]), (1, "rejected"))
                self.assertIn(detail, line["detail"])
        self.post.assert_not_called()

    def test_duplicate_input_rejects_all_entries_for_that_document(self):
        code, lines = self.run_cli("--apply", entries=[ENTRY, ENTRY])
        self.assertEqual(code, 1)
        self.assertEqual([line["outcome"] for line in lines], ["rejected", "rejected"])
        self.fetch.assert_not_called()
        self.post.assert_not_called()

    def test_version_conflict_is_reported_without_overwriting(self):
        self.post.return_value.json.return_value = {"status": "conflict"}
        code, (line,) = self.run_cli("--apply")
        self.assertEqual((code, line["outcome"], line["detail"]), (1, "error", "version write: conflict"))
        self.post.assert_called_once()
