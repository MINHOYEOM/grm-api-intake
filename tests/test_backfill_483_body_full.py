"""FDA 483 전문 소급(backfill_483_body_full) — 네트워크 없이 판정·쓰기 경로를 확인한다."""
from __future__ import annotations

import json
import unittest

import backfill_483_body_full as bb
import raw_versioning as rv


def _row(doc: str, raw: dict, rid: str | None = None) -> dict:
    text, sha = rv.canonical_raw(raw)
    return {"raw_signal_id": rid or f"rid-{doc}", "document_id": doc, "published_date": "2026-10-20",
            "raw_json": text, "row_json": json.dumps({"headline": doc}), "raw_sha256": sha}


_BASE_RAW = {"media_id": "1", "firm": "Acme", "fda_483_observations": [{"number": "1"}],
             "fda483_excerpt": "x"}


def _run(rows, *, apply=False, probe_pdf=False, index=None, notion=None, pdf=None, ledger=()):
    calls: list[dict] = []

    def applier(**kw):
        calls.append(kw)
        return {"status": "applied"}

    rep = bb.run(rows, set(ledger), apply=apply, probe_pdf=probe_pdf, notion_index=index or {},
                 since="2026-06-01",
                 notion_body=notion or (lambda _pid: ""),
                 pdf_body=pdf or (lambda _raw, _doc: ("", "fetch-fail")),
                 applier=applier if apply else None, sleeper=lambda _s: None)
    return rep, calls


class SelectTargetsTest(unittest.TestCase):
    def test_skips_filled_out_of_scope_and_ledger(self) -> None:
        rows = [_row("fda483-1", dict(_BASE_RAW)),
                _row("fda483-2", dict(_BASE_RAW, fda483_body_full="already")),
                _row("fda483-3", dict(_BASE_RAW, fda483_text_status="out-of-scope")),
                _row("fda483-4", dict(_BASE_RAW))]
        rep, _ = _run(rows, ledger={"fda483-4"})
        self.assertEqual((rep.scanned, rep.targets), (4, 1))
        self.assertEqual((rep.skipped_out_of_scope, rep.skipped_ledger), (1, 1))


class DryRunTest(unittest.TestCase):
    def test_dry_counts_paths_without_writing_or_pdf(self) -> None:
        pdf_calls = []
        rows = [_row("fda483-1", dict(_BASE_RAW)), _row("fda483-2", dict(_BASE_RAW))]
        rep, calls = _run(rows, index={"fda483-1": "p1"}, notion=lambda pid: "FULL TEXT",
                          pdf=lambda raw, doc: pdf_calls.append(doc) or ("", "x"))
        self.assertEqual((rep.via_notion, rep.notion_missing, rep.pdf_not_probed), (1, 1, 1))
        self.assertEqual(calls, [])
        self.assertEqual(pdf_calls, [])

    def test_probe_pdf_reads_but_does_not_write(self) -> None:
        rep, calls = _run([_row("fda483-2", dict(_BASE_RAW))], probe_pdf=True,
                          pdf=lambda raw, doc: ("PDF TEXT", "ok"))
        self.assertEqual(rep.via_pdf, 1)
        self.assertEqual(calls, [])


class ApplyTest(unittest.TestCase):
    def test_adds_only_body_key_and_uses_expected_sha(self) -> None:
        row = _row("fda483-1", dict(_BASE_RAW))
        rep, calls = _run([row], apply=True, index={"fda483-1": "p1"}, notion=lambda pid: "FULL")
        self.assertEqual(rep.applied, 1)
        (call,) = calls
        self.assertEqual(call["expected_raw_sha256"], row["raw_sha256"])
        self.assertEqual(call["change_kind"], "repair")
        new_raw = json.loads(call["record"]["raw_json"])
        self.assertEqual(new_raw, dict(_BASE_RAW, fda483_body_full="FULL"))
        self.assertEqual(call["record"]["raw_sha256"], rv.canonical_raw(new_raw)[1])
        self.assertEqual(call["record"]["row_json"], row["row_json"])
        self.assertEqual(call["diff_summary"]["via"], "notion")

    def test_pdf_fallback_and_no_observation_is_not_written(self) -> None:
        rows = [_row("fda483-1", dict(_BASE_RAW)), _row("fda483-2", dict(_BASE_RAW))]
        results = {"fda483-1": ("PDF", "ok"), "fda483-2": ("", "no-observations")}
        rep, calls = _run(rows, apply=True, pdf=lambda raw, doc: results[doc])
        self.assertEqual((rep.via_pdf, rep.pdf_no_observations, rep.applied), (1, 1, 1))
        self.assertEqual([c["raw_signal_id"] for c in calls], ["rid-fda483-1"])
        self.assertEqual(calls[0]["diff_summary"]["via"], "pdf")

    def test_conflict_and_errors_are_counted(self) -> None:
        rows = [_row("fda483-1", dict(_BASE_RAW)), _row("fda483-2", dict(_BASE_RAW))]
        answers = iter([{"status": "conflict"}, {"status": "error", "error": "http_500"}])
        rep = bb.run(rows, set(), apply=True, probe_pdf=False, notion_index={},
                     since="2026-06-01", notion_body=lambda p: "",
                     pdf_body=lambda raw, doc: ("PDF", "ok"),
                     applier=lambda **kw: next(answers), sleeper=lambda _s: None)
        self.assertEqual(rep.conflict, 1)
        self.assertEqual(rep.errors, ["apply(fda483-2):http_500"])

    def test_notion_exception_falls_back_to_pdf(self) -> None:
        def boom(_pid):
            raise RuntimeError("x")
        rep, calls = _run([_row("fda483-1", dict(_BASE_RAW))], apply=True,
                          index={"fda483-1": "p1"}, notion=boom, pdf=lambda raw, doc: ("PDF", "ok"))
        self.assertEqual((rep.via_pdf, rep.applied), (1, 1))
        self.assertEqual(rep.errors, ["notion(fda483-1):RuntimeError"])


if __name__ == "__main__":
    unittest.main()
