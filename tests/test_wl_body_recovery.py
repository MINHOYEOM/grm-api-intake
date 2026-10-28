"""backfill_wl_body_recovery — 본문 없이 적재된 FDA WL 행의 예약 회수(2026-10-28).

핵심 불변식: 회수가 만든 행은 **같은 본문을 처음부터 받았을 때 라이브 수집이 만들었을 행과
바이트 동일**하다(raw_signal_id·raw_sha256·raw_json). 회수가 raw 를 손으로 기우지 않고 라이브와
같은 `build_wl_item` 을 쓰기 때문에 성립하며, 이 테스트가 그 전제를 고정한다.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_wl_body_recovery as rec  # noqa: E402
import collect_intake as ci  # noqa: E402
import findings_store  # noqa: E402
from tests.test_wl_body import _WIN_END, _WIN_START, _WL_LIST_HTML, _Resp  # noqa: E402

# 위반 표제가 뽑히는 본문(번호 + 21 CFR 인용).
_LETTER_HTML = (
    "<html><body><div class='letter'><p>WARNING LETTER</p>"
    "<p>During our inspection of your drug manufacturing facility, our investigators observed "
    "specific violations including, but not limited to, the following.</p>"
    "<p>1. Your firm failed to establish adequate written procedures for production and "
    "process control (21 CFR 211.100(a)).</p>"
    "<p>2. Your firm failed to thoroughly investigate unexplained discrepancies "
    "(21 CFR 211.192).</p></div></body></html>"
)
_BODY_KEYS = ("wl_body_excerpt", "wl_body_full", "wl_violations", "wl_body_status")


def _live_record(letter: _Resp) -> dict:
    """라이브 수집(두 플래그 on — 프로덕션 설정) → raw_signals 레코드."""
    def _get(url, *a, **k):
        return _Resp(_WL_LIST_HTML) if url == ci.FDA_WL_URL else letter

    with patch.dict(os.environ, {"ENABLE_WL_BODY": "true", "ENABLE_WL_BODY_FULL": "true"}), \
            patch.object(ci.requests, "get", side_effect=_get), \
            patch.object(ci, "_wl_retry_sleep", lambda _s: None):
        items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
    assert err is None and len(items) == 1
    return findings_store.raw_signal_from_intake_item(items[0], collected_at="2026-06-10T21:00:00Z")


def _letter_from_html(html: str):
    def _fetch(url):
        ex = ci._extract_wl_body_excerpt(html)
        full = ci._extract_wl_body_full(html)
        return ex, full, ("ok" if full else "no-anchor")
    return _fetch


def _run(rows, *, fetch_letter, dry_run=False, upsert=None, **kw):
    upsert = upsert or mock.MagicMock(return_value=(201, [], ""))
    report, code = rec.run(
        base_url="https://x.supabase.co", service_key="k", dry_run=dry_run, delay_seconds=0,
        fetch_raw_signals=lambda base, key: list(rows), fetch_letter=fetch_letter,
        upsert_raw_signal=upsert, sleeper=lambda _s: None, **kw)
    return report, code, upsert


class CandidateTest(unittest.TestCase):

    def test_selection(self):
        cases = [
            ({"wl_body_full": "x"}, False),
            ({"wl_body_status": "no-anchor"}, False),
            ({"wl_body_status": "fetch-fail:404 Client Error"}, False),
            ({"wl_body_status": "fetch-403"}, True),
            ({"wl_body_status": "fetch-fail:503 Server Error"}, True),
            ({"wl_body_status": "fetch-fail:Read timed out"}, True),
            ({"wl_body_excerpt": "x"}, True),     # 전문 플래그 도입 전 적재분(사유 없음)
            ({}, True),                            # 백필 페치 예외 경로(사유 없음)
        ]
        for raw, want in cases:
            with self.subTest(raw=raw):
                self.assertIs(rec.is_recovery_candidate(raw), want)

    def test_known_keys_cover_every_key_the_live_builder_writes(self):
        rec_live = _live_record(_Resp(_LETTER_HTML))
        raw = json.loads(rec_live["raw_json"])
        self.assertEqual(sorted(set(raw) - rec.KNOWN_RAW_KEYS), [])


class RecoveryRoundTripTest(unittest.TestCase):

    def test_recovered_row_equals_live_row_byte_for_byte(self):
        """403 으로 본문 없이 적재된 행 → 회수 → 처음부터 본문을 받은 라이브 행과 동일."""
        good = _live_record(_Resp(_LETTER_HTML))
        failed = _live_record(_Resp("", 403))
        self.assertEqual(failed["raw_signal_id"], good["raw_signal_id"])
        self.assertEqual(json.loads(failed["raw_json"])["wl_body_status"], "fetch-403")
        row = {"raw_signal_id": failed["raw_signal_id"], "document_id": failed["document_id"],
               "collected_at": "2026-06-10T21:00:00Z", "raw_json": failed["raw_json"]}

        report, code, upsert = _run([row], fetch_letter=_letter_from_html(_LETTER_HTML))
        self.assertEqual(code, 0)
        self.assertEqual((report.candidates, report.recovered, report.id_mismatch), (1, 1, 0))
        self.assertEqual(report.violations_recovered, 2)
        written = upsert.call_args.args[2]
        for key in ("raw_signal_id", "document_id", "raw_sha256", "raw_json", "row_json",
                    "collected_at"):
            with self.subTest(key=key):
                self.assertEqual(written[key], good[key])

    def test_retry_layer_is_the_live_fetch(self):
        """기본 fetch 경로는 수집기의 재시도 GET 을 쓴다 — 503 한 번은 회수 안에서 넘긴다."""
        seq = [_Resp("", 503), _Resp(_LETTER_HTML)]
        with patch.object(ci.requests, "get", side_effect=lambda *a, **k: seq.pop(0)), \
                patch.object(ci, "_wl_retry_sleep", lambda _s: None):
            ex, full, status = rec._fetch_letter("https://www.fda.gov/x")
        self.assertEqual(status, "ok")
        self.assertTrue(full and ex)


class RecoveryGuardTest(unittest.TestCase):

    def _failed_row(self):
        failed = _live_record(_Resp("", 403))
        return {"raw_signal_id": failed["raw_signal_id"], "document_id": failed["document_id"],
                "collected_at": "2026-06-10T21:00:00Z", "raw_json": failed["raw_json"]}

    def test_still_blocked_writes_nothing(self):
        report, code, upsert = _run([self._failed_row()],
                                    fetch_letter=lambda url: ("", "", "fetch-403"))
        self.assertEqual((report.still_empty, report.recovered), (1, 0))
        upsert.assert_not_called()
        # 소표본(3건 미만)의 still_empty 는 경보하지 않는다 — 끈질긴 403 편지 한 통이 매일
        # 예약 실패 이슈를 울리지 않게. 다음 실행에서 다시 후보가 된다.
        self.assertEqual(code, 0)

    def test_definitive_404_replaces_marker_only(self):
        row = self._failed_row()
        report, code, upsert = _run(
            [row], fetch_letter=lambda url: ("", "", "fetch-fail:404 Client Error"))
        self.assertEqual((report.status_resolved, report.recovered), (1, 0))
        written = json.loads(upsert.call_args.args[2]["raw_json"])
        self.assertEqual(written["wl_body_status"], "fetch-fail:404 Client Error")
        self.assertFalse(any(written.get(k) for k in ("wl_body_full", "wl_body_excerpt")))
        # 다음 실행에서는 후보가 아니다(재시도가 끊긴다).
        self.assertFalse(rec.is_recovery_candidate(written))

    def test_definitive_status_keeps_existing_excerpt(self):
        row = self._failed_row()
        raw = json.loads(row["raw_json"])
        raw.pop("wl_body_status")
        raw["wl_body_excerpt"] = "During our inspection, earlier excerpt."
        row["raw_json"] = json.dumps(raw)
        report, _code, upsert = _run([row], fetch_letter=lambda url: ("", "", "no-anchor"))
        written = json.loads(upsert.call_args.args[2]["raw_json"])
        self.assertEqual(written["wl_body_excerpt"], "During our inspection, earlier excerpt.")
        self.assertEqual(written["wl_body_status"], "no-anchor")

    def test_unknown_raw_key_is_left_alone(self):
        row = self._failed_row()
        raw = json.loads(row["raw_json"])
        raw["translated_by_someone_else"] = "keep me"
        row["raw_json"] = json.dumps(raw)
        report, _code, upsert = _run([row], fetch_letter=_letter_from_html(_LETTER_HTML))
        self.assertEqual((report.unknown_keys, report.attempted), (1, 0))
        upsert.assert_not_called()

    def test_id_mismatch_is_left_alone(self):
        row = self._failed_row()
        row["raw_signal_id"] = "rawsig-something-else"
        report, _code, upsert = _run([row], fetch_letter=_letter_from_html(_LETTER_HTML))
        self.assertEqual((report.id_mismatch, report.recovered), (1, 0))
        upsert.assert_not_called()

    def test_dry_run_counts_but_does_not_write(self):
        report, code, upsert = _run([self._failed_row()], dry_run=True,
                                    fetch_letter=_letter_from_html(_LETTER_HTML))
        self.assertEqual((report.recovered, code), (1, 0))
        upsert.assert_not_called()

    def test_rows_with_full_body_or_definitive_marker_are_not_fetched(self):
        good = _live_record(_Resp(_LETTER_HTML))
        no_anchor = _live_record(_Resp("<html><body><p>nothing</p></body></html>"))
        rows = [{"raw_signal_id": r["raw_signal_id"], "document_id": r["document_id"],
                 "collected_at": "", "raw_json": r["raw_json"]} for r in (good, no_anchor)]
        fetch = mock.MagicMock()
        report, _code, upsert = _run(rows, fetch_letter=fetch)
        self.assertEqual((report.candidates, report.skipped_definitive), (0, 1))
        fetch.assert_not_called()

    def test_broad_block_exits_nonzero(self):
        rows = []
        for i in range(4):
            row = self._failed_row()
            rows.append({**row, "raw_signal_id": f"{row['raw_signal_id']}-{i}"})
        report, code, _u = _run(rows, fetch_letter=lambda url: ("", "", "fetch-403"))
        self.assertEqual((report.attempted, report.still_empty, code), (4, 4, 1))


if __name__ == "__main__":
    unittest.main()
