#!/usr/bin/env python3
"""web/growth_weekly.py 테스트 — 순수 빌더(결정론·네트워크 0) + CLI(fixture payload 경로는
네트워크 0) + Brevo 어댑터 요청 형태(fake requests.post) + 워크플로 YAML 텍스트 가드.

CI(`unittest discover -s tests`)는 `tests/test_web_growth_weekly.py` shim 으로 순회. 직접:
  python web/tests/test_growth_weekly.py
"""
from __future__ import annotations

import copy
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from unittest import mock

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent          # …/web
sys.path.insert(0, str(WEB_DIR))
import growth_weekly  # noqa: E402  (web/growth_weekly.py)

REPO_ROOT = WEB_DIR.parent
FIXTURE = WEB_DIR / "tests" / "fixtures" / "growth_weekly_payload.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "grm-growth-weekly.yml"

# CI shim(tests/test_web_growth_weekly.py)은 이 모듈의 TestCase 하위클래스를 **전수 자동**
# 수집한다(수동 __all__ 목록은 새 클래스를 빠뜨리면 조용히 미실행됐다 — 렌더 shim 선례).


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _payload_with_week_diffs() -> dict:
    """첫 주가 아닌(직전 스냅샷 있음) 상태 — touch_week/paths_week 가 채워진 사본."""
    p = copy.deepcopy(_payload())
    p["touch_week"] = {
        "by_channel": [
            {"channel": "utm:linkedin", "submits": 3},
            {"channel": "direct", "submits": 1},
            {"channel": "mystery_channel", "submits": 1},
        ],
        "rows": [
            {"key": "k1", "channel": "utm:linkedin", "source": "linkedin", "medium": "social",
             "campaign": "2026-09-21_weekly", "ref_host": None, "landing_zone": "findings", "submits": 3},
        ],
        "total": 5,
    }
    p["paths_week"] = {
        "rows": [
            {"key": "p1", "path": "/findings/", "submits": 2},
            {"key": "p2", "path": "/glossary/expiry-date/", "submits": 1},
        ],
        "total": 3,
    }
    return p


_NEWSLETTER_STATUS_STUB = {
    "publish_date": "2026-09-14", "verdict": "sent", "campaign_status": "sent",
    "reason": "캠페인 123 상태=sent",
}


# ── 순수 빌더 ─────────────────────────────────────────────────────────────────
class SubjectTest(unittest.TestCase):
    def test_subject_format(self):
        self.assertEqual(
            growth_weekly.build_subject(_payload()),
            "📈 GRM 주간 성장 리포트 · 2026-09-14~2026-09-20")


class PickWeekBriefTest(unittest.TestCase):
    def test_picks_latest_within_range(self):
        dates = ["2026-09-10", "2026-09-15", "2026-09-18", "2026-09-25"]
        self.assertEqual(growth_weekly.pick_week_brief(dates, "2026-09-14", "2026-09-20"), "2026-09-18")

    def test_inclusive_boundaries(self):
        self.assertEqual(growth_weekly.pick_week_brief(["2026-09-14"], "2026-09-14", "2026-09-20"), "2026-09-14")
        self.assertEqual(growth_weekly.pick_week_brief(["2026-09-20"], "2026-09-14", "2026-09-20"), "2026-09-20")

    def test_none_when_no_match(self):
        self.assertIsNone(growth_weekly.pick_week_brief(["2026-08-01"], "2026-09-14", "2026-09-20"))

    def test_none_on_empty_list(self):
        self.assertIsNone(growth_weekly.pick_week_brief([], "2026-09-14", "2026-09-20"))

    def test_ignores_falsy_dates(self):
        self.assertEqual(growth_weekly.pick_week_brief(["", None, "2026-09-16"], "2026-09-14", "2026-09-20"),
                         "2026-09-16")


class PrecisionLabelTest(unittest.TestCase):
    def test_exact_at_and_below_threshold(self):
        self.assertEqual(growth_weekly.precision_label(1.0), "정확")
        self.assertEqual(growth_weekly.precision_label(1.5), "정확")

    def test_sampled_above_threshold(self):
        self.assertEqual(growth_weekly.precision_label(2.0), "표본 약 2.0배")
        self.assertEqual(growth_weekly.precision_label(10), "표본 약 10.0배")

    def test_unknown_on_none(self):
        self.assertEqual(growth_weekly.precision_label(None), "미상")

    def test_unknown_flag_overrides_value(self):
        self.assertEqual(growth_weekly.precision_label(1.0, precision_unknown=True), "미상")


class LabelDictsTest(unittest.TestCase):
    def test_zone_labels_known(self):
        self.assertEqual(growth_weekly.ZONE_LABELS["glossary"], "용어사전")
        self.assertEqual(growth_weekly.ZONE_LABELS["findings"], "지적사항")
        self.assertEqual(growth_weekly.ZONE_LABELS["home"], "홈")

    def test_zone_label_unknown_passthrough(self):
        self.assertEqual(growth_weekly._zone_label("mystery_zone"), "mystery_zone")

    def test_channel_labels_known(self):
        self.assertEqual(growth_weekly.CHANNEL_LABELS["utm:linkedin"], "링크드인(UTM)")
        self.assertEqual(growth_weekly.CHANNEL_LABELS["naver"], "네이버")
        self.assertEqual(growth_weekly.CHANNEL_LABELS["direct"], "직접")

    def test_channel_label_unknown_passthrough(self):
        self.assertEqual(growth_weekly._channel_label("mystery_channel"), "mystery_channel")


class DeltaFormatTest(unittest.TestCase):
    def test_positive_gets_plus_sign(self):
        self.assertEqual(growth_weekly._fmt_delta(3), "+3")
        self.assertEqual(growth_weekly._fmt_delta(0), "+0")

    def test_negative_uses_unicode_minus(self):
        out = growth_weekly._fmt_delta(-1)
        self.assertEqual(out, "−1")
        self.assertNotIn("-", out)          # 하이픈(U+002D) 이 아니라 U+2212 여야 한다


class ReportHtmlSectionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = _payload()
        cls.html = growth_weekly.build_report_html(cls.payload, _NEWSLETTER_STATUS_STUB)

    def test_all_eight_headings_present(self):
        self.assertEqual(len(growth_weekly.SECTION_HEADINGS), 8)
        for heading in growth_weekly.SECTION_HEADINGS:
            self.assertIn(heading, self.html, f"섹션 헤딩 누락: {heading}")

    def test_footer_mentions_source_and_operator_only(self):
        self.assertIn("growth_weekly_report(089)", self.html)
        self.assertIn("운영자에게만 발송됩니다", self.html)

    def test_first_week_note_for_null_touch_week_and_paths_week(self):
        self.assertIsNone(self.payload["touch_week"])
        self.assertIsNone(self.payload["paths_week"])
        note = growth_weekly._first_week_note(self.payload["touch"]["total_submits"])
        self.assertIn(note, self.html)
        self.assertIn("첫 주라 주간값이 없습니다", self.html)

    def test_no_email_address_leaked(self):
        self.assertNotIn("@", self.html)

    def test_generated_at_in_footer(self):
        self.assertIn(self.payload["generated_at_kst"], self.html)


class ReportHtmlPopulatedWeekDiffsTest(unittest.TestCase):
    """touch_week/paths_week 가 채워진 주(첫 주가 아님) — 표 렌더 경로."""

    @classmethod
    def setUpClass(cls):
        cls.payload = _payload_with_week_diffs()
        cls.html = growth_weekly.build_report_html(cls.payload, _NEWSLETTER_STATUS_STUB)

    def test_no_first_week_note(self):
        self.assertNotIn("첫 주라 주간값이 없습니다", self.html)

    def test_channel_rows_rendered_with_labels(self):
        self.assertIn("링크드인(UTM)", self.html)
        self.assertIn("직접", self.html)
        self.assertIn("mystery_channel", self.html)       # 미등재 채널 = 원문 그대로

    def test_paths_week_rows_rendered(self):
        self.assertIn("/findings/", self.html)
        self.assertIn("/glossary/expiry-date/", self.html)

    def test_cumulative_channel_line_present(self):
        self.assertIn("누적 채널 신청", self.html)


class ZoneRenderTest(unittest.TestCase):
    def test_zone_table_uses_labels(self):
        html = growth_weekly.build_report_html(_payload(), _NEWSLETTER_STATUS_STUB)
        self.assertIn("용어사전", html)                    # ZONE_LABELS["glossary"]
        self.assertIn("지적사항", html)                    # ZONE_LABELS["findings"]

    def test_zone_precision_label_reflects_high_sample_interval(self):
        # fixture 의 구역 sample_interval_max 는 전부 1.5 초과(표본 구간) — "표본 약" 이어야 한다.
        html = growth_weekly.build_report_html(_payload(), _NEWSLETTER_STATUS_STUB)
        self.assertIn("표본 약", html)


class ResolveRecipientsTest(unittest.TestCase):
    def _run(self, env: dict) -> list[str]:
        base = dict(env)
        for k in ("GRM_GROWTH_REPORT_TO", "GRM_NEWSLETTER_TEST_EMAILS"):
            base.setdefault(k, "")
        with mock.patch.dict(os.environ, base, clear=False):
            return growth_weekly.resolve_recipients()

    def test_growth_report_to_wins(self):
        out = self._run({"GRM_GROWTH_REPORT_TO": "a@x.com, b@x.com",
                         "GRM_NEWSLETTER_TEST_EMAILS": "c@x.com"})
        self.assertEqual(out, ["a@x.com", "b@x.com"])

    def test_falls_back_to_first_test_email(self):
        out = self._run({"GRM_NEWSLETTER_TEST_EMAILS": "c@x.com;d@x.com"})
        self.assertEqual(out, ["c@x.com"])

    def test_empty_when_neither_set(self):
        self.assertEqual(self._run({}), [])


class SendEmailTest(unittest.TestCase):
    def test_request_shape(self):
        fake_resp = mock.Mock(status_code=200)
        with mock.patch("requests.post", return_value=fake_resp) as post:
            growth_weekly.send_email("api-key-1", "GRM", "ops@grm.example", ["yeom@example.com"],
                                     "제목", "<p>본문</p>")
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(kwargs["headers"]["api-key"], "api-key-1")
        body = kwargs["json"]
        self.assertEqual(body["sender"], {"name": "GRM", "email": "ops@grm.example"})
        self.assertEqual(body["subject"], "제목")
        self.assertEqual(body["htmlContent"], "<p>본문</p>")
        self.assertEqual(body["to"], [{"email": "yeom@example.com"}])

    def test_raises_on_error_status(self):
        fake_resp = mock.Mock(status_code=500)
        with mock.patch("requests.post", return_value=fake_resp):
            with self.assertRaises(RuntimeError):
                growth_weekly.send_email("k", "GRM", "ops@grm.example", ["a@b.com"], "S", "<p>h</p>")


# ── CLI(payload 파일 경로 — 네트워크 0) ─────────────────────────────────────────
class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.empty_data_dir = self.tmp / "briefs_empty"
        self.empty_data_dir.mkdir()
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        for k in ("NEWSLETTER_API_KEY", "GRM_GROWTH_REPORT_TO", "GRM_NEWSLETTER_TEST_EMAILS",
                  "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "GRM_NEWSLETTER_SENDER_EMAIL"):
            os.environ.pop(k, None)

    def tearDown(self):
        self._env_patch.stop()
        self._tmp.cleanup()

    def test_dry_run_with_payload_and_out_writes_html_exits_0(self):
        out_path = self.tmp / "report.html"
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = growth_weekly.main([
                "--mode", "dry-run", "--payload", str(FIXTURE), "--out", str(out_path),
                "--data", str(self.empty_data_dir),
            ])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertTrue(out_path.is_file())
        html = out_path.read_text(encoding="utf-8")
        for heading in growth_weekly.SECTION_HEADINGS:
            self.assertIn(heading, html)
        # 로그에 숫자를 안 찍는다 — 구조만.
        self.assertIn("섹션 8개 생성", out.getvalue())

    def test_send_with_no_recipients_exits_3(self):
        out_path = self.tmp / "report2.html"
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = growth_weekly.main([
                "--mode", "send", "--payload", str(FIXTURE), "--out", str(out_path),
                "--data", str(self.empty_data_dir),
            ])
        self.assertEqual(rc, 3)


class DefaultWeekEndTest(unittest.TestCase):
    """089 `today_kst - extract(isodow from today_kst)` 와 같은 값이어야 멱등 조회가 맞는 행을 본다."""

    def test_monday_is_yesterday(self):
        self.assertEqual(growth_weekly.default_week_end(date(2026, 9, 28)), "2026-09-27")

    def test_sunday_is_previous_sunday(self):
        self.assertEqual(growth_weekly.default_week_end(date(2026, 9, 27)), "2026-09-20")

    def test_midweek(self):
        self.assertEqual(growth_weekly.default_week_end(date(2026, 9, 30)), "2026-09-27")


class AlreadySentTest(unittest.TestCase):
    def test_request_shape_and_true_when_row(self):
        fake = mock.Mock(status_code=200)
        fake.json.return_value = [{"week_end": "2026-09-27"}]
        with mock.patch("requests.get", return_value=fake) as get:
            self.assertTrue(growth_weekly.already_sent("https://x.supabase.co/", "k", "2026-09-27"))
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://x.supabase.co/rest/v1/growth_weekly_reports")
        self.assertEqual(kwargs["params"]["week_end"], "eq.2026-09-27")

    def test_false_when_empty(self):
        fake = mock.Mock(status_code=200)
        fake.json.return_value = []
        with mock.patch("requests.get", return_value=fake):
            self.assertFalse(growth_weekly.already_sent("https://x.supabase.co", "k", "2026-09-27"))

    def test_raises_on_error_status(self):
        with mock.patch("requests.get", return_value=mock.Mock(status_code=503)):
            with self.assertRaises(RuntimeError):
                growth_weekly.already_sent("https://x.supabase.co", "k", "2026-09-27")


class SkipIfSentCliTest(unittest.TestCase):
    def setUp(self):
        self._env_patch = mock.patch.dict(os.environ, {
            "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"}, clear=False)
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = growth_weekly.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_skips_without_rpc_when_already_sent(self):
        with (
            mock.patch.object(growth_weekly, "already_sent", return_value=True) as sent,
            mock.patch.object(growth_weekly, "fetch_payload") as fetch,
        ):
            rc, out, _ = self._run(["--mode", "send", "--skip-if-sent", "--week-end", "2026-09-27"])
        self.assertEqual(rc, 0)
        sent.assert_called_once_with("https://x.supabase.co", "k", "2026-09-27")
        fetch.assert_not_called()          # RPC(=persist) 도 안 부른다
        self.assertIn("건너뜀", out)

    def test_lookup_failure_still_sends(self):
        # 판정 불가면 보낸다 — RPC 까지 진행(여기선 RPC 실패로 rc=2 에서 멈추게 해 발송 0).
        with (
            mock.patch.object(growth_weekly, "already_sent", side_effect=RuntimeError("x")),
            mock.patch.object(growth_weekly, "fetch_payload", side_effect=RuntimeError("y")) as fetch,
        ):
            rc, _, err = self._run(["--mode", "send", "--skip-if-sent", "--week-end", "2026-09-27"])
        fetch.assert_called_once()
        self.assertEqual(rc, 2)
        self.assertIn("그대로 발송", err)

    def test_flag_absent_never_looks_up(self):
        # 수동 실행(플래그 없음)은 조회 없이 곧장 RPC — 재발송이 막히지 않는다.
        with (
            mock.patch.object(growth_weekly, "already_sent") as sent,
            mock.patch.object(growth_weekly, "fetch_payload", side_effect=RuntimeError("y")),
        ):
            rc, _, _ = self._run(["--mode", "send", "--week-end", "2026-09-27"])
        sent.assert_not_called()
        self.assertEqual(rc, 2)

    def test_dry_run_ignores_flag(self):
        with (
            mock.patch.object(growth_weekly, "already_sent") as sent,
            mock.patch.object(growth_weekly, "fetch_payload", side_effect=RuntimeError("y")),
        ):
            self._run(["--mode", "dry-run", "--skip-if-sent"])
        sent.assert_not_called()


# ── 워크플로 YAML 텍스트 가드 ────────────────────────────────────────────────────
class WorkflowYamlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_cron_is_monday_three_slots_from_0923_kst(self):
        self.assertIn("'23 0,2,4 * * 1'", self.text)

    def test_schedule_passes_skip_if_sent(self):
        # 다회차 예약이 같은 주를 두 번 보내지 않게 — 예약 실행에만 멱등 플래그.
        self.assertIn('if [ "$EVENT_NAME" = "schedule" ]; then', self.text)
        self.assertIn("--skip-if-sent", self.text)

    def test_mode_flag_present(self):
        self.assertIn("--mode", self.text)

    def test_secret_names_present(self):
        for name in ("SUPABASE_SERVICE_ROLE_KEY", "NEWSLETTER_API_KEY"):
            self.assertIn(name, self.text)

    def test_issues_write_permission(self):
        self.assertIn("issues: write", self.text)

    def test_no_numbers_promised_in_failure_issue_body(self):
        # 실패 이슈 본문에 방문/구독자 등 숫자 필드를 실어 나르지 않는다(공개 저장소 계약).
        self.assertNotIn("visits", self.text)
        self.assertNotIn("subscribers", self.text)


if __name__ == "__main__":
    unittest.main()
