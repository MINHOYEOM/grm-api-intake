"""WL 본문 앞 결손(저장 88행) — 원인 수리 + 재수집 도구 가드 (2026-11-24).

원인: 본문 시작 앵커 'specifically,'(1차 앵커)가 서한 **어느 단락에서든** 걸린다. 임상시험자·GLP·허위광고 서한은
위반 서술 뒤 '회신이 부적절하다' 단락에서야 처음 나와(실측 11,348자 중 8,319번째) 거기서 자르면 본문이
'Specifically,' 로 시작하고 앞의 위반 서술을 통째로 잃었다. 저장 단계의 글자 수 절단이 아니라 **시작점** 결함이다.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_wl_body_recovery as rec  # noqa: E402
import collect_intake as ci  # noqa: E402
import findings_store  # noqa: E402
from tests.test_wl_body import _Resp  # noqa: E402

# 실서한(임상시험자 BIMO 형식)의 구조를 줄인 것: 인사말 → 위반 서술(앵커 없음) → 회신 부적절 단락의 'Specifically,'.
_CI_LETTER = (
    "<html><body><p>Dear Dr. Example:</p>"
    "<p>This Warning Letter informs you of objectionable conditions observed during the FDA inspection "
    "conducted at your clinical site. You failed to ensure that the investigation was conducted according to "
    "the investigational plan [21 CFR 312.60]. Two subjects were enrolled although they did not meet the "
    "eligibility criteria.</p>"
    "<p>We acknowledge your response, but your written response is inadequate because it does not provide "
    "sufficient details. Specifically, two of the nine subjects were ineligible for enrollment.</p>"
    "<p>Sincerely, FDA</p></body></html>"
)


class AnchorStartTest(unittest.TestCase):
    def test_weak_specifically_anchor_does_not_cut_the_violation_narrative(self):
        full = ci._extract_wl_body_full(_CI_LETTER)
        self.assertNotRegex(full, r"^\s*Specifically,")
        self.assertIn("You failed to ensure that the investigation", full)       # 앞의 위반 서술이 남는다
        self.assertIn("Specifically, two of the nine subjects", full)            # 뒤 단락도 그대로
        self.assertFalse(ci._WL_BODY_HEAD_TRUNCATED_RE.match(full))              # 수집기 자신의 결손 판정에 안 걸린다

    def test_excerpt_uses_same_start(self):
        self.assertNotRegex(ci._extract_wl_body_excerpt(_CI_LETTER), r"^\s*Specifically,")

    def test_specifically_alone_still_works_when_nothing_precedes_it(self):
        """폴백 앵커가 앞에 없고 'Specifically,' 만 있는 글은 종전대로 그 문장부터(오늘보다 나빠지지 않는다)."""
        html = "<html><body><p>Hello world text. Specifically, the firm did not test each lot.</p></body></html>"
        self.assertTrue(ci._extract_wl_body_full(html).startswith("Specifically,"))

    def test_strong_primary_anchor_still_wins(self):
        html = ("<html><body><p>Dear Mr. X: During our inspection of your facility, we found problems with "
                "records. Specifically, the firm did not test each lot.</p></body></html>")
        self.assertTrue(ci._extract_wl_body_full(html).startswith("During our inspection"))


class BotBlockTest(unittest.TestCase):
    def test_apology_redirect_is_named_and_not_retried(self):
        resp = _Resp("", 404)
        resp.url = "https://www.fda.gov/apology_objects/abuse-detection-apology.html"
        with mock.patch.object(ci.requests, "get", return_value=resp) as g, \
                mock.patch.object(ci, "_wl_retry_sleep", lambda _s: None):
            html, status = ci._fetch_wl_letter_html("https://www.fda.gov/x")
        self.assertEqual((html, status), ("", ci.WL_BODY_STATUS_BOT_BLOCK))
        self.assertEqual(g.call_count, 1)                       # 즉시 재시도는 차단만 길게 한다
        self.assertTrue(status.startswith("fetch-fail"))        # 하류 사유 매핑(접두) 불변

    def test_status_constant_is_shared_with_recovery_tool(self):
        self.assertEqual(rec.BOT_BLOCK_STATUS, ci.WL_BODY_STATUS_BOT_BLOCK)

    def test_plain_404_is_not_called_a_block(self):
        resp = _Resp("", 404)
        resp.url = "https://www.fda.gov/missing"
        with mock.patch.object(ci.requests, "get", return_value=resp):
            _html, status = ci._fetch_wl_letter_html("https://www.fda.gov/missing")
        self.assertNotEqual(status, ci.WL_BODY_STATUS_BOT_BLOCK)


def _row(doc: str, body: str, **extra) -> dict:
    raw = {"firm": f"Dr Example {doc}", "posted_date": "2026-03-27", "letter_date": "", "issuing_office": "CDER",
           "subject": "Clinical Investigator", "url": f"https://www.fda.gov/wl/{doc}", "wl_body_full": body}
    raw.update(extra)
    # 재구성이 같은 id 를 내야 쓴다(id 불일치 가드) — 같은 firm·날짜·url 로 라이브와 같은 id 를 얻는다.
    item = rec._rebuild_item(raw, ("", "no-anchor"), (body, "ok"))
    rec_ = findings_store.raw_signal_from_intake_item(item, collected_at="2026-04-01T00:00:00Z")
    return {"raw_signal_id": rec_["raw_signal_id"], "document_id": rec_["document_id"],
            "collected_at": "2026-04-01T00:00:00Z", "raw_sha256": "old",
            "raw_json": json.dumps(raw, ensure_ascii=False), "row_json": "{}"}


OLD_HEAD = "Specifically, two of the nine subjects were ineligible for enrollment."


class RecollectTest(unittest.TestCase):
    def _run(self, rows, fetch_letter, **kw):
        upsert = mock.MagicMock(return_value=(201, [], ""))
        report, code = rec.run(
            base_url="https://x.supabase.co", service_key="k", dry_run=kw.pop("dry_run", False), delay_seconds=0,
            fetch_raw_signals=lambda base, key, **k: list(rows), fetch_letter=fetch_letter,
            upsert_raw_signal=upsert, sleeper=lambda _s: None, rebuild_body_start=True, **kw)
        return report, code, upsert

    def _fetch(self, html):
        def f(url):
            return ci._extract_wl_body_excerpt(html), ci._extract_wl_body_full(html), "ok"
        return f

    def test_specifically_head_rows_are_candidates_without_naming_them(self):
        ok = _row("d1", OLD_HEAD)
        other = _row("d2", "During our inspection we found a problem.")
        report, _c, _u = self._run([ok, other], self._fetch(_CI_LETTER), dry_run=True)
        self.assertEqual(report.candidates, 1)

    def test_recollect_writes_longer_body_and_reports_old_to_new_length(self):
        row = _row("d1", OLD_HEAD)
        report, code, upsert = self._run([row], self._fetch(_CI_LETTER))
        self.assertEqual((report.recovered, report.body_shrunk, report.body_diverged, code), (1, 0, 0, 0))
        written = json.loads(upsert.call_args.args[2]["raw_json"])
        self.assertTrue(written["wl_body_full"].startswith("This Warning Letter informs you"))
        self.assertEqual(report.body_changes[0]["old_chars"], len(OLD_HEAD))
        self.assertGreater(report.body_changes[0]["new_chars"], len(OLD_HEAD))
        self.assertEqual(sum(report.head_rebuild.values()), 1)

    def test_shrinking_row_is_never_written(self):
        long_old = OLD_HEAD + " " + "More text of the old body. " * 80
        report, _c, upsert = self._run([_row("d1", long_old)], self._fetch(_CI_LETTER))
        upsert.assert_not_called()
        self.assertEqual((report.body_shrunk, report.recovered), (1, 0))
        self.assertEqual(report.body_changes[0]["skipped"], "shrunk")

    def test_different_letter_is_not_written(self):
        """다시 받은 글이 옛 본문 앞부분을 품지 않으면(다른 글·페이지 개편) 쓰지 않는다."""
        other = ("<html><body><p>Dear Dr. Z: This Warning Letter informs you of something else entirely, and it "
                 "is long enough to be longer than the old text we stored for this row earlier.</p></body></html>")
        report, _c, upsert = self._run([_row("d1", OLD_HEAD)], self._fetch(other))
        upsert.assert_not_called()
        self.assertEqual((report.body_diverged, report.recovered), (1, 0))

    def test_bot_block_stops_the_run_at_once(self):
        rows = [_row(f"d{i}", OLD_HEAD) for i in range(5)]
        calls = []

        def fetch(url):
            calls.append(url)
            return "", "", ci.WL_BODY_STATUS_BOT_BLOCK
        report, _c, upsert = self._run(rows, fetch)
        self.assertEqual(len(calls), 1)                         # 차단 감지 뒤 더 두드리지 않는다
        self.assertTrue(report.bot_blocked)
        self.assertIn(report.stopped_at, [r["document_id"] for r in rows])
        upsert.assert_not_called()

    def test_blocked_refetch_keeps_existing_body(self):
        report, _c, upsert = self._run([_row("d1", OLD_HEAD)], lambda url: ("", "", "fetch-403"))
        upsert.assert_not_called()
        self.assertEqual(report.still_empty, 1)


if __name__ == "__main__":
    unittest.main()
