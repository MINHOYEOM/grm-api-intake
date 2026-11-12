#!/usr/bin/env python3
"""[원문 갱신 규칙 R1·R2 2026-11-12] raw_versioning(함수 호출) · raw_signals_rebaseline(지문 재매김)."""

from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

import raw_signals_rebaseline as rb
import raw_versioning as rv

_URL = "https://e.supabase.co"
_KEY = "k"


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _row(rid, source, kind, raw_text, sha=None, doc=None):
    return {"raw_signal_id": rid, "source": source, "source_kind": kind, "document_id": doc or rid,
            "raw_sha256": sha if sha is not None else _sha(raw_text), "raw_json": raw_text,
            "row_json": "{}"}




class CanonicalRawTest(unittest.TestCase):
    def test_canonical_text_and_sha(self):
        text, sha = rv.canonical_raw({"b": 2, "a": "가"})
        self.assertEqual(text, '{"a":"가","b":2}')
        self.assertEqual(sha, _sha(text))


class ApplyVersionTest(unittest.TestCase):
    def _call(self, post, **kw):
        rec = {"raw_json": '{"a":1}', "row_json": "{}", "raw_sha256": _sha('{"a":1}')}
        return rv.apply_version(_URL, _KEY, raw_signal_id="rawsig-1", expected_raw_sha256="0" * 64,
                                record=rec, change_kind=kw.get("kind", "repair"), changed_by="t",
                                post=post)

    def test_posts_rpc_and_returns_status(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"status": "applied", "version_no": 2}
        post = mock.Mock(return_value=resp)
        self.assertEqual(self._call(post)["status"], "applied")
        url = post.call_args.args[0]
        self.assertTrue(url.endswith("/rest/v1/rpc/raw_signal_apply_version"))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["p_expected_raw_sha256"], "0" * 64)
        self.assertEqual(body["p_change_kind"], "repair")

    def test_rejects_unknown_kind_and_missing_fields(self):
        with self.assertRaises(ValueError):
            self._call(mock.Mock(), kind="nope")
        with self.assertRaises(ValueError):
            rv.apply_version(_URL, _KEY, raw_signal_id="r", expected_raw_sha256="0" * 64,
                             record={"raw_json": "{}"}, change_kind="repair", changed_by="t",
                             post=mock.Mock())

    def test_http_error_is_reported_without_body(self):
        resp = mock.Mock(status_code=400, text="secret detail")
        out = self._call(mock.Mock(return_value=resp))
        self.assertEqual(out, {"status": "error", "error": "http_400"})

    def test_timeout_retries_once(self):
        import requests
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"status": "conflict"}
        post = mock.Mock(side_effect=[requests.exceptions.Timeout(), resp])
        self.assertEqual(self._call(post)["status"], "conflict")
        self.assertEqual(post.call_count, 2)


class RebaselineTest(unittest.TestCase):

    def _fetch_factory(self, rows, attendees):
        def fetch(base, key, table, **kw):
            if table == "raw_signals":
                src = kw["extra_params"]["source"][3:]
                return [r for r in rows if r["source"] == src]
            return attendees
        return fetch

    def test_dry_run_finds_only_mismatched_and_writes_nothing(self):
        ok = _row("rawsig-ok", "FDA 483", "483", '{"a":1}')
        bad = _row("rawsig-bad", "FDA 483", "483", '{"b": 2, "a": 1}', sha="0" * 64)
        apply_fn, sync_fn = mock.Mock(), mock.Mock()
        rep = rb.run(_URL, _KEY, sources=["FDA 483"], apply=False, changed_by="t",
                     fetch=self._fetch_factory([ok, bad], []), apply_fn=apply_fn, sync_fn=sync_fn)
        self.assertEqual(rep.mismatched, {"FDA 483": 1})
        self.assertEqual(rep.scanned, {"FDA 483": 2})
        apply_fn.assert_not_called()
        sync_fn.assert_not_called()

    def test_apply_sends_canonical_text_and_resyncs_mfds_attendees(self):
        mf = _row("rawsig-m", "MFDS", "gmp-inspection", '{"attachment_text": "x", "z": 1}',
                  sha="1" * 64, doc="mfds-doc")
        f4 = _row("rawsig-f", "FDA 483", "483", '{"b": 2}', sha="2" * 64)
        apply_fn = mock.Mock(return_value={"status": "applied"})
        sync_fn = mock.Mock()
        rep = rb.run(_URL, _KEY, sources=["FDA 483", "MFDS"], apply=True, changed_by="t",
                     fetch=self._fetch_factory([mf, f4], []), apply_fn=apply_fn, sync_fn=sync_fn)
        self.assertEqual(rep.applied, 2)
        sent = {c.kwargs["raw_signal_id"]: c.kwargs for c in apply_fn.call_args_list}
        self.assertEqual(sent["rawsig-m"]["record"]["raw_json"], '{"attachment_text":"x","z":1}')
        self.assertEqual(sent["rawsig-m"]["expected_raw_sha256"], "1" * 64)
        self.assertEqual(sent["rawsig-m"]["change_kind"], "baseline")
        self.assertEqual(sent["rawsig-m"]["record"]["raw_sha256"],
                         _sha('{"attachment_text":"x","z":1}'))
        # MFDS 실사만 참석자 재동기화 — 새 지문으로
        self.assertEqual(sync_fn.call_count, 1)
        self.assertEqual(sync_fn.call_args.args[2]["raw_sha256"],
                         _sha('{"attachment_text":"x","z":1}'))
        self.assertEqual(rep.attendees_resynced, 1)

    def test_conflict_skips_attendee_resync(self):
        mf = _row("rawsig-m", "MFDS", "gmp-inspection", '{"a": 1}', sha="1" * 64)
        sync_fn = mock.Mock()
        rep = rb.run(_URL, _KEY, sources=["MFDS"], apply=True, changed_by="t",
                     fetch=self._fetch_factory([mf], []),
                     apply_fn=mock.Mock(return_value={"status": "conflict"}), sync_fn=sync_fn)
        self.assertEqual(rep.conflict, ["rawsig-m"])
        sync_fn.assert_not_called()

    def test_attendee_comparison_counts(self):
        mf = _row("rawsig-m", "MFDS", "gmp-inspection", '{"attachment_text": ""}', sha="1" * 64)
        rep = rb.run(_URL, _KEY, sources=["MFDS"], apply=False, changed_by="t",
                     fetch=self._fetch_factory([mf], []))
        self.assertEqual((rep.attendees_docs, rep.attendees_missing), (1, 1))

    def test_invalid_json_is_reported_not_touched(self):
        broken = _row("rawsig-x", "FDA 483", "483", "{not json", sha="3" * 64)
        rep = rb.run(_URL, _KEY, sources=["FDA 483"], apply=True, changed_by="t",
                     fetch=self._fetch_factory([broken], []), apply_fn=mock.Mock())
        self.assertEqual(rep.invalid_json, ["rawsig-x"])
        self.assertEqual(rep.applied, 0)


if __name__ == "__main__":
    unittest.main()
