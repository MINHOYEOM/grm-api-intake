#!/usr/bin/env python3
"""[원문 갱신 규칙 R3 2026-11-12] 원문을 바꾸던 수리 도구 4개가 raw_signal_apply_version(148) 경유인지.

- 공용 함수 raw_versioning.replace_raw / apply_record: 지문을 정규화해 계산하고, 읽은 시점 지문을
  expected 로 넘기며, applied/unchanged 가 아니면 실패로 돌려준다(조용한 성공 금지).
- 식약처 평가 도구: 지문이 바뀌므로 실사자 표를 새 지문으로 다시 맞춘다(R2 와 같은 처리).
네트워크 없음.
"""
import hashlib
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_483_detail_refresh as detail  # noqa: E402
import backfill_mfds_gmp_assessment as mfds  # noqa: E402
import backfill_wl_body_recovery as wl  # noqa: E402
import grm_findings as gf  # noqa: E402
import raw_versioning as rv  # noqa: E402

_ROW = {"raw_signal_id": "rawsig-1", "raw_sha256": "a" * 64, "row_json": '{"k":1}'}


def _fake_post(status="applied"):
    calls = []

    def post(url, headers=None, json=None, timeout=None):  # noqa: A002 — requests 와 같은 이름
        calls.append((url, json))
        return mock.MagicMock(status_code=200, json=lambda: {"status": status})
    return post, calls


class ReplaceRawTest(unittest.TestCase):

    def test_sends_canonical_text_and_matching_fingerprint(self):
        post, calls = _fake_post()
        res = rv.replace_raw("https://x.supabase.co", "k", _ROW, {"b": 2, "a": "가"},
                             change_kind="repair", changed_by="t:1", post=post)
        body = calls[0][1]
        text = body["p_record"]["raw_json"]
        self.assertEqual(text, gf.canonical_json({"b": 2, "a": "가"}))
        self.assertEqual(body["p_record"]["raw_sha256"],
                         hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(body["p_expected_raw_sha256"], "a" * 64)
        self.assertEqual(body["p_record"]["row_json"], '{"k":1}')   # PATCH 시절처럼 row_json 유지
        self.assertEqual(res["new_raw_sha256"], body["p_record"]["raw_sha256"])

    def test_conflict_raises(self):
        post, _ = _fake_post("conflict")
        with self.assertRaises(rv.ApplyVersionError) as cm:
            rv.replace_raw("https://x.supabase.co", "k", _ROW, {"a": 1},
                           change_kind="repair", changed_by="t", post=post)
        self.assertEqual(cm.exception.status, "conflict")

    def test_missing_read_fingerprint_refuses_without_calling(self):
        post, calls = _fake_post()
        with self.assertRaises(rv.ApplyVersionError):
            rv.replace_raw("https://x.supabase.co", "k", {"raw_signal_id": "r"}, {"a": 1},
                           change_kind="repair", changed_by="t", post=post)
        self.assertEqual(calls, [])

    def test_run_label_uses_actions_run_id(self):
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "123"}):
            self.assertEqual(rv.run_label("tool"), "tool:123")


class ToolsUseFunctionTest(unittest.TestCase):

    def test_483_detail_refresh_patch_goes_through_function(self):
        post, calls = _fake_post()
        with mock.patch("raw_versioning.requests.post", post):
            detail._patch("https://x.supabase.co", "k", _ROW, {"fda_483_observations": []})
        self.assertTrue(calls[0][0].endswith("/rpc/raw_signal_apply_version"))
        self.assertEqual(calls[0][1]["p_change_kind"], "reextract")

    def test_wl_body_recovery_upsert_goes_through_function(self):
        text = gf.canonical_json({"wl_body_full": "x"})
        record = {"raw_signal_id": "rawsig-1", "raw_json": text, "row_json": "{}",
                  "raw_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        post, calls = _fake_post()
        with mock.patch("raw_versioning.requests.post", post):
            got = wl._upsert_raw_signal("https://x.supabase.co", "k", record, row=_ROW)
        self.assertEqual(got[2], "")
        self.assertEqual(calls[0][1]["p_expected_raw_sha256"], "a" * 64)

    def test_mfds_assessment_resyncs_attendees_with_new_fingerprint(self):
        row = dict(_ROW, document_id="mfds-1", source_kind="gmp-inspection")
        post, calls = _fake_post()
        synced = []
        with mock.patch("raw_versioning.requests.post", post), \
                mock.patch.object(mfds.mas, "sync_attendees",
                                  lambda b, k, rec: synced.append(rec)):
            mfds._patch("https://x.supabase.co", "k", row, {"attachment_text": "적합"})
        self.assertEqual(len(synced), 1)
        self.assertEqual(synced[0]["raw_sha256"], calls[0][1]["p_record"]["raw_sha256"])
        self.assertEqual(synced[0]["document_id"], "mfds-1")
        self.assertEqual(json.loads(synced[0]["raw_json"]), {"attachment_text": "적합"})

    def test_mfds_assessment_unchanged_does_not_resync(self):
        post, _ = _fake_post("unchanged")
        synced = []
        with mock.patch("raw_versioning.requests.post", post), \
                mock.patch.object(mfds.mas, "sync_attendees",
                                  lambda b, k, rec: synced.append(rec)):
            mfds._patch("https://x.supabase.co", "k", dict(_ROW, document_id="d"), {"a": 1})
        self.assertEqual(synced, [])


if __name__ == "__main__":
    unittest.main()
