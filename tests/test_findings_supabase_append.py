#!/usr/bin/env python3
"""FIND-1 M4a Supabase(PostgREST) direct append boundary tests.

All HTTP is mocked via `findings_supabase_append.requests.post` — no real
network access. These tests cover the transport/status contract only; record
construction itself is covered by test_findings_store.py and
test_findings_extractors.py.
"""

from __future__ import annotations

import unittest
from unittest import mock

import collect_intake as ci
import findings_supabase_append as fsa
import grm_findings as gf


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"


def _item(**overrides) -> ci.IntakeItem:
    base = dict(
        source="FDA 483",
        document_id="fda483-192439",
        date_iso="2026-05-27",
        headline="[FDA 483] BPI Labs, LLC",
        official_url="https://www.fda.gov/media/192439/download",
        type_or_class="483",
        firm="BPI Labs, LLC",
        body="There is a failure to review unexplained discrepancies.",
        qa_relevance="Likely",
        osd_relevance="Direct",
        signal_tier="Tier 3",
        raw_payload={
            "firm": "BPI Labs, LLC",
            "media_id": "192439",
            "fda_483_observations": [
                {"number": "1", "deficiency": "Failure to investigate discrepancies one."},
                {"number": "2", "deficiency": "Failure to investigate discrepancies two."},
            ],
        },
    )
    base.update(overrides)
    return ci.IntakeItem(**base)


class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload

    def json(self):
        return self._payload


class AppendRawSignalTest(unittest.TestCase):
    def test_inserted_when_response_array_nonempty(self) -> None:
        with mock.patch("findings_supabase_append.requests.post",
                         return_value=_FakeResponse(201, [{"raw_signal_id": "x"}])) as post:
            result = fsa.append_intake_item_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        self.assertEqual(result.status, "inserted")
        self.assertTrue(result.inserted)
        post.assert_called_once()
        _, kwargs = post.call_args
        self.assertEqual(kwargs["params"], {"on_conflict": "raw_signal_id"})

    def test_duplicate_when_response_array_empty(self) -> None:
        with mock.patch("findings_supabase_append.requests.post",
                         return_value=_FakeResponse(200, [])):
            result = fsa.append_intake_item_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        self.assertEqual(result.status, "duplicate")

    def test_invalid_local_validation_never_posts(self) -> None:
        with mock.patch("findings_supabase_append.requests.post") as post:
            result = fsa.append_intake_item_to_supabase(
                _BASE_URL, _SERVICE_KEY, _item(raw_payload=["not", "an", "object"]),
            )

        self.assertEqual(result.status, "invalid")
        post.assert_not_called()

    def test_https_guard_rejects_non_https_base_url(self) -> None:
        with mock.patch("findings_supabase_append.requests.post") as post:
            result = fsa.append_intake_item_to_supabase("http://example.supabase.co", _SERVICE_KEY, _item())

        self.assertEqual(result.status, "invalid")
        post.assert_not_called()

    def test_5xx_retries_once_then_succeeds(self) -> None:
        responses = [_FakeResponse(503), _FakeResponse(201, [{"raw_signal_id": "x"}])]
        with mock.patch("findings_supabase_append.requests.post", side_effect=responses) as post:
            result = fsa.append_intake_item_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        self.assertEqual(result.status, "inserted")
        self.assertEqual(post.call_count, 2)

    def test_5xx_exhausted_retry_returns_error(self) -> None:
        responses = [_FakeResponse(503), _FakeResponse(503)]
        with mock.patch("findings_supabase_append.requests.post", side_effect=responses) as post:
            result = fsa.append_intake_item_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        self.assertEqual(result.status, "error")
        self.assertEqual(post.call_count, 2)

    def test_raw_signal_payload_keeps_raw_json_row_json_as_text(self) -> None:
        with mock.patch("findings_supabase_append.requests.post",
                         return_value=_FakeResponse(201, [{"raw_signal_id": "x"}])) as post:
            fsa.append_intake_item_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        _, kwargs = post.call_args
        body = kwargs["json"]
        self.assertEqual(len(body), 1)
        self.assertIsInstance(body[0]["raw_json"], str)
        self.assertIsInstance(body[0]["row_json"], str)


class AppendRawSignalWithFindingsTest(unittest.TestCase):
    def test_findings_batch_409_falls_back_to_row_level(self) -> None:
        raw_resp = _FakeResponse(201, [{"raw_signal_id": "x"}])
        batch_conflict_resp = _FakeResponse(409)
        row_inserted_resp = _FakeResponse(201, [{"finding_id": "f1"}])
        row_duplicate_resp = _FakeResponse(409)

        with mock.patch(
            "findings_supabase_append.requests.post",
            side_effect=[raw_resp, batch_conflict_resp, row_inserted_resp, row_duplicate_resp],
        ) as post:
            result = fsa.append_intake_item_with_findings_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        self.assertEqual(post.call_count, 4)
        self.assertEqual(result.raw_signal_status, "inserted")
        self.assertEqual(result.findings_inserted, 1)
        self.assertEqual(result.findings_duplicate, 1)
        self.assertEqual(result.findings_invalid, 0)
        self.assertEqual(result.status, "inserted")

    def test_findings_jsonb_columns_stay_python_lists_in_request_body(self) -> None:
        raw_resp = _FakeResponse(201, [{"raw_signal_id": "x"}])
        findings_resp = _FakeResponse(201, [{"finding_id": "f1"}, {"finding_id": "f2"}])

        with mock.patch(
            "findings_supabase_append.requests.post", side_effect=[raw_resp, findings_resp],
        ) as post:
            fsa.append_intake_item_with_findings_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        findings_call = post.call_args_list[1]
        _, kwargs = findings_call
        body = kwargs["json"]
        self.assertEqual(kwargs["params"], {"on_conflict": "finding_id"})
        for row in body:
            self.assertIsInstance(row["cfr_refs"], list)
            self.assertIsInstance(row["inspector_names"], list)
            self.assertIsInstance(row["mfds_refs"], list)
            self.assertIsInstance(row["confidence"], float)

    def test_raw_signal_invalid_skips_findings_entirely(self) -> None:
        with mock.patch("findings_supabase_append.requests.post") as post:
            result = fsa.append_intake_item_with_findings_to_supabase(
                _BASE_URL, _SERVICE_KEY, _item(raw_payload=["not", "an", "object"]),
            )

        self.assertEqual(result.status, "invalid")
        post.assert_not_called()

    def test_raw_signal_error_skips_findings_entirely(self) -> None:
        responses = [_FakeResponse(503), _FakeResponse(503)]
        with mock.patch("findings_supabase_append.requests.post", side_effect=responses) as post:
            result = fsa.append_intake_item_with_findings_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        self.assertEqual(result.status, "error")
        self.assertEqual(result.raw_signal_status, "error")
        self.assertEqual(post.call_count, 2)  # only the raw_signal attempts, no findings POST

    def test_https_guard_rejects_non_https_base_url(self) -> None:
        with mock.patch("findings_supabase_append.requests.post") as post:
            result = fsa.append_intake_item_with_findings_to_supabase(
                "http://example.supabase.co", _SERVICE_KEY, _item(),
            )

        self.assertEqual(result.status, "invalid")
        post.assert_not_called()


class ServiceKeySecrecyTest(unittest.TestCase):
    """The service-role key must never surface in a result or an exception summary."""

    def test_service_key_absent_from_error_result_on_request_exception(self) -> None:
        import requests as _requests

        def _boom(*_args, **_kwargs):
            raise _requests.exceptions.RequestException(f"connection reset apikey={_SERVICE_KEY}")

        with mock.patch("findings_supabase_append.requests.post", side_effect=_boom):
            result = fsa.append_intake_item_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        self.assertEqual(result.status, "error")
        self.assertNotIn(_SERVICE_KEY, str(result))
        for err in result.errors:
            self.assertNotIn(_SERVICE_KEY, err)

    def test_service_key_absent_from_result_on_success(self) -> None:
        with mock.patch("findings_supabase_append.requests.post",
                         return_value=_FakeResponse(201, [{"raw_signal_id": "x"}])):
            result = fsa.append_intake_item_to_supabase(_BASE_URL, _SERVICE_KEY, _item())

        self.assertNotIn(_SERVICE_KEY, str(result))


class ValidateFindingSanityTest(unittest.TestCase):
    """Sanity check that the extractor output used by this module stays schema-valid."""

    def test_findings_from_item_are_schema_valid(self) -> None:
        import findings_extractors
        import findings_store as store

        record = store.raw_signal_from_intake_item(_item())
        findings = findings_extractors.findings_from_raw_signal(record)
        self.assertEqual(len(findings), 2)
        for finding in findings:
            self.assertEqual(gf.validate_finding(finding), [])


if __name__ == "__main__":
    unittest.main()
