#!/usr/bin/env python3
"""GRM 워치리스트 주간 이메일 통지 서비스(watchlist_notify_service.py) 테스트.

All HTTP is mocked via `watchlist_notify_service.requests.request` -- no real
network access, no real Supabase/Brevo project (mirrors
tests/test_findings_translate_apply_service.py's convention). Covers:
  · 이중 시간조건(ingested_at 7일 + published_date 21일 AND) 필터
  · 멱등(notification_log 에 이미 있는 (user, finding) 쌍 제외)
  · 사용자별 그룹핑(여러 업체 → 1통 요약 이메일)
  · 발송 상한 500(초과분 절단 + 리포트 명시)
  · 마스킹(이메일/user_id 가 리포트에 원본으로 노출되지 않음)
  · 키 미노출 계약(SUPABASE_SERVICE_ROLE_KEY/Brevo 키가 리포트/예외 경로에 등장 금지)
  · dry-run(발송·로그 기록 0)
  · 원문 미포함(이메일 HTML/텍스트에 finding_text 류가 절대 없음)
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

import watchlist_notify_service as svc


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"
_BREVO_KEY = "brevo-secret-api-key"


class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.content = b"x"  # truthy -- forces .json() to be attempted

    def json(self):
        return self._payload


# ── 마스킹 ────────────────────────────────────────────────────────────────────
class MaskingTest(unittest.TestCase):
    def test_mask_email_matches_spec_example(self) -> None:
        self.assertEqual(svc.mask_email("ab@domain.com"), "ab***@d***.com")

    def test_mask_email_short_local_part(self) -> None:
        self.assertEqual(svc.mask_email("a@x.co.kr"), "a***@x***.kr")

    def test_mask_email_no_at_sign(self) -> None:
        self.assertEqual(svc.mask_email("not-an-email"), "***")

    def test_mask_email_empty(self) -> None:
        self.assertEqual(svc.mask_email(""), "***")

    def test_mask_user_id_keeps_only_first_segment(self) -> None:
        self.assertEqual(
            svc.mask_user_id("3fa85f64-5717-4562-b3fc-2c963f66afa6"), "3fa85f64-***"
        )

    def test_mask_user_id_empty(self) -> None:
        self.assertEqual(svc.mask_user_id(""), "***")


# ── 이중 시간조건(순수) ───────────────────────────────────────────────────────
class TimeWindowTest(unittest.TestCase):
    def test_window_params_derive_from_now(self) -> None:
        now = datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc)
        w = svc.time_window_params(now)
        self.assertEqual(w["ingested_since"], "2026-07-05T10:00:00Z")
        self.assertEqual(w["published_since"], "2026-06-21")


class FilterCandidateFindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.since = svc.time_window_params(
            datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc)
        )

    def _f(self, **over):
        base = {
            "finding_id": "f1", "firm_key": "acme", "firm_name": "ACME",
            "published_date": "2026-07-10", "ingested_at": "2026-07-11T00:00:00Z",
            "scope_status": "ok",
        }
        base.update(over)
        return base

    def test_within_both_windows_kept(self) -> None:
        out = svc.filter_candidate_findings(
            [self._f()], ingested_since=self.since["ingested_since"],
            published_since=self.since["published_since"],
        )
        self.assertEqual(len(out), 1)

    def test_old_ingested_at_excluded_even_if_recently_published(self) -> None:
        # 백필 방어: published_date 는 최근이지만 ingested_at 이 창 밖(예: 8일 전 적재)
        # -- 노이즈 방어의 핵심 케이스.
        old = self._f(ingested_at="2026-07-01T00:00:00Z", published_date="2026-07-10")
        out = svc.filter_candidate_findings(
            [old], ingested_since=self.since["ingested_since"],
            published_since=self.since["published_since"],
        )
        self.assertEqual(out, [])

    def test_old_published_date_excluded_even_if_recently_ingested(self) -> None:
        old = self._f(ingested_at="2026-07-11T00:00:00Z", published_date="2026-05-01")
        out = svc.filter_candidate_findings(
            [old], ingested_since=self.since["ingested_since"],
            published_since=self.since["published_since"],
        )
        self.assertEqual(out, [])

    def test_non_ok_scope_status_excluded(self) -> None:
        bad = self._f(scope_status="non_pharma")
        out = svc.filter_candidate_findings(
            [bad], ingested_since=self.since["ingested_since"],
            published_since=self.since["published_since"],
        )
        self.assertEqual(out, [])

    def test_boundary_exactly_at_ingested_since_kept(self) -> None:
        boundary = self._f(ingested_at=self.since["ingested_since"])
        out = svc.filter_candidate_findings(
            [boundary], ingested_since=self.since["ingested_since"],
            published_since=self.since["published_since"],
        )
        self.assertEqual(len(out), 1)


# ── 사용자 그룹핑(순수) ───────────────────────────────────────────────────────
class BuildUserFirmMatchesTest(unittest.TestCase):
    def test_groups_by_user_then_firm(self) -> None:
        watchlist = [
            {"user_id": "u1", "firm_key": "acme", "firm_display": "ACME Pharma"},
            {"user_id": "u1", "firm_key": "beta", "firm_display": "Beta Labs"},
            {"user_id": "u2", "firm_key": "acme", "firm_display": "ACME Pharma"},
        ]
        findings = [
            {"finding_id": "f1", "firm_key": "acme", "firm_name": "ACME", "published_date": "2026-07-10"},
            {"finding_id": "f2", "firm_key": "acme", "firm_name": "ACME", "published_date": "2026-07-11"},
            {"finding_id": "f3", "firm_key": "beta", "firm_name": "Beta", "published_date": "2026-07-09"},
        ]
        out = svc.build_user_firm_matches(watchlist, findings)
        self.assertEqual(set(out.keys()), {"u1", "u2"})
        self.assertEqual(set(out["u1"].keys()), {"acme", "beta"})
        self.assertEqual(out["u1"]["acme"].finding_ids, ["f1", "f2"])
        self.assertEqual(out["u1"]["acme"].latest_published_date, "2026-07-11")
        self.assertEqual(out["u2"]["acme"].finding_ids, ["f1", "f2"])

    def test_unwatched_firm_produces_no_match(self) -> None:
        watchlist = [{"user_id": "u1", "firm_key": "acme", "firm_display": "ACME"}]
        findings = [{"finding_id": "f1", "firm_key": "gamma", "firm_name": "Gamma",
                    "published_date": "2026-07-10"}]
        out = svc.build_user_firm_matches(watchlist, findings)
        self.assertEqual(out, {})

    def test_firm_display_falls_back_to_firm_name_then_firm_key(self) -> None:
        watchlist = [{"user_id": "u1", "firm_key": "acme", "firm_display": ""}]
        findings = [{"finding_id": "f1", "firm_key": "acme", "firm_name": "ACME Inc",
                    "published_date": "2026-07-10"}]
        out = svc.build_user_firm_matches(watchlist, findings)
        self.assertEqual(out["u1"]["acme"].firm_display, "ACME Inc")


# ── 멱등(로그 제외) ───────────────────────────────────────────────────────────
class ExcludeAlreadyNotifiedTest(unittest.TestCase):
    def test_excludes_logged_pairs_only(self) -> None:
        matches = {
            "u1": {"acme": svc.FirmMatch(firm_key="acme", firm_display="ACME",
                                         finding_ids=["f1", "f2"], latest_published_date="2026-07-11")},
        }
        out = svc.exclude_already_notified(matches, {("u1", "f1")})
        self.assertEqual(out["u1"]["acme"].finding_ids, ["f2"])

    def test_drops_firm_when_all_findings_already_notified(self) -> None:
        matches = {
            "u1": {"acme": svc.FirmMatch(firm_key="acme", firm_display="ACME",
                                         finding_ids=["f1"], latest_published_date="2026-07-11")},
        }
        out = svc.exclude_already_notified(matches, {("u1", "f1")})
        self.assertEqual(out, {})

    def test_drops_user_when_no_firms_remain(self) -> None:
        matches = {
            "u1": {"acme": svc.FirmMatch(firm_key="acme", firm_display="ACME",
                                         finding_ids=["f1"], latest_published_date="2026-07-11")},
            "u2": {"beta": svc.FirmMatch(firm_key="beta", firm_display="Beta",
                                         finding_ids=["f2"], latest_published_date="2026-07-11")},
        }
        out = svc.exclude_already_notified(matches, {("u1", "f1")})
        self.assertEqual(set(out.keys()), {"u2"})

    def test_different_user_same_finding_not_excluded(self) -> None:
        # 멱등 키는 (user_id, finding_id) 쌍 -- 다른 사용자에겐 재발송 대상 유지.
        matches = {
            "u2": {"acme": svc.FirmMatch(firm_key="acme", firm_display="ACME",
                                         finding_ids=["f1"], latest_published_date="2026-07-11")},
        }
        out = svc.exclude_already_notified(matches, {("u1", "f1")})
        self.assertEqual(out["u2"]["acme"].finding_ids, ["f1"])


# ── 발송 상한 ─────────────────────────────────────────────────────────────────
class ApplySendCapTest(unittest.TestCase):
    def test_under_cap_all_kept(self) -> None:
        matches = {"u1": {}, "u2": {}}
        kept, skipped = svc.apply_send_cap(matches, cap=500)
        self.assertEqual(set(kept.keys()), {"u1", "u2"})
        self.assertEqual(skipped, [])

    def test_over_cap_truncates_deterministically(self) -> None:
        matches = {f"u{i}": {} for i in range(5)}
        kept, skipped = svc.apply_send_cap(matches, cap=2)
        self.assertEqual(sorted(kept.keys()), ["u0", "u1"])
        self.assertEqual(skipped, ["u2", "u3", "u4"])

    def test_default_cap_is_500(self) -> None:
        self.assertEqual(svc.MAX_SENDS_PER_RUN, 500)


# ── 이메일 조립(원문 미포함·프로파일 링크) ────────────────────────────────────
class BuildEmailTest(unittest.TestCase):
    def test_single_firm_subject(self) -> None:
        firms = {"acme": svc.FirmMatch(firm_key="acme", firm_display="ACME Pharma",
                                       finding_ids=["f1", "f2"], latest_published_date="2026-07-11")}
        self.assertEqual(svc.build_subject(firms), "관심 업체 규제 동향 — ACME Pharma")

    def test_multi_firm_subject_uses_count_suffix(self) -> None:
        firms = {
            "acme": svc.FirmMatch(firm_key="acme", firm_display="ACME Pharma",
                                  finding_ids=["f1"], latest_published_date="2026-07-11"),
            "beta": svc.FirmMatch(firm_key="beta", firm_display="Beta Labs",
                                  finding_ids=["f2"], latest_published_date="2026-07-09"),
        }
        subj = svc.build_subject(firms)
        self.assertIn("관심 업체 규제 동향 —", subj)
        self.assertIn("외 1개 업체", subj)

    def test_profile_link_uses_findings_firm_index_pattern(self) -> None:
        url = svc.firm_profile_url("https://grm-solutions.com", "acme")
        self.assertEqual(url, "https://grm-solutions.com/findings/firm/index.html?key=acme")

    def test_email_contains_no_original_finding_text(self) -> None:
        firms = {"acme": svc.FirmMatch(firm_key="acme", firm_display="ACME Pharma",
                                       finding_ids=["f1", "f2"], latest_published_date="2026-07-11")}
        mail = svc.build_email(firms, site_base_url="https://grm-solutions.com")
        for forbidden in ("finding_text", "evidence_url", "f1", "f2"):
            self.assertNotIn(forbidden, mail["html"])
            self.assertNotIn(forbidden, mail["text"])

    def test_email_contains_unsubscribe_hint_and_disclosure(self) -> None:
        firms = {"acme": svc.FirmMatch(firm_key="acme", firm_display="ACME Pharma",
                                       finding_ids=["f1"], latest_published_date="2026-07-11")}
        mail = svc.build_email(firms, site_base_url="https://grm-solutions.com")
        self.assertIn("마이페이지에서 관심 업체를 삭제", mail["html"])
        self.assertIn(svc.DISCLOSURE_KO, mail["html"])
        self.assertIn(svc.DISCLOSURE_EN, mail["html"])

    def test_email_body_shows_count_and_latest_date(self) -> None:
        firms = {"acme": svc.FirmMatch(firm_key="acme", firm_display="ACME Pharma",
                                       finding_ids=["f1", "f2", "f3"], latest_published_date="2026-07-11")}
        mail = svc.build_email(firms, site_base_url="https://grm-solutions.com")
        self.assertIn("새 지적 3건", mail["html"])
        self.assertIn("2026-07-11", mail["html"])


# ── run() 통합(HTTP 전량 모킹) ────────────────────────────────────────────────
def _admin_users(*pairs):
    return {"users": [{"id": uid, "email": email} for uid, email in pairs]}


class _Dispatcher:
    """URL 접미사로 분기하는 페이크 requests.request. 페이지네이션은 항상 1페이지
    (배치 크기 < limit) 로 종료되도록 소량 데이터만 반환한다."""

    def __init__(self, *, watchlist, findings, notified_log, admin_users):
        self.watchlist = watchlist
        self.findings = findings
        self.notified_log = notified_log
        self.admin_users = admin_users
        self.posted_smtp: list[dict] = []
        self.posted_log: list[list[dict]] = []

    def __call__(self, method, url, headers=None, params=None, json=None, timeout=None):
        if url.endswith("/rest/v1/firm_watchlist"):
            return _FakeResponse(200, self.watchlist)
        if url.endswith("/rest/v1/findings"):
            return _FakeResponse(200, self.findings)
        if url.endswith("/rest/v1/firm_watch_notification_log") and method == "GET":
            return _FakeResponse(200, self.notified_log)
        if url.endswith("/rest/v1/firm_watch_notification_log") and method == "POST":
            self.posted_log.append(json)
            return _FakeResponse(201, [])
        if url.endswith("/auth/v1/admin/users"):
            return _FakeResponse(200, self.admin_users)
        if url.endswith("/smtp/email"):
            self.posted_smtp.append({"headers": headers, "json": json})
            return _FakeResponse(201, {"messageId": "fake"})
        raise AssertionError(f"unexpected URL in test dispatcher: {method} {url}")


def _base_fixture():
    watchlist = [{"user_id": "u1", "firm_key": "acme", "firm_display": "ACME Pharma"}]
    findings = [{
        "finding_id": "f1", "firm_key": "acme", "firm_name": "ACME Pharma",
        "published_date": "2026-07-10", "ingested_at": "2026-07-11T00:00:00Z",
        "scope_status": "ok",
    }]
    return watchlist, findings


class RunDryRunTest(unittest.TestCase):
    def test_dry_run_issues_no_post_and_no_send(self) -> None:
        watchlist, findings = _base_fixture()
        disp = _Dispatcher(watchlist=watchlist, findings=findings, notified_log=[],
                          admin_users=_admin_users(("u1", "user1@example.com")))
        with mock.patch("watchlist_notify_service.requests.request", side_effect=disp):
            report = svc.run(
                supabase_url=_BASE_URL, service_role_key=_SERVICE_KEY,
                brevo_api_key=_BREVO_KEY, sender_name="GRM", sender_email="noreply@grm.example",
                site_base_url="https://grm-solutions.com", dry_run=True,
                now=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
            )
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["users_notified"], 1)
        self.assertEqual(report["findings_notified_total"], 1)
        self.assertEqual(disp.posted_smtp, [])
        self.assertEqual(disp.posted_log, [])
        # 리포트는 마스킹된 user_id 만 담는다(원본 미노출).
        self.assertEqual(report["recipients"][0]["user_id"], "u1-***")


class RunSendTest(unittest.TestCase):
    def test_successful_send_writes_log_and_masks_report(self) -> None:
        watchlist, findings = _base_fixture()
        disp = _Dispatcher(watchlist=watchlist, findings=findings, notified_log=[],
                          admin_users=_admin_users(("u1", "user1@example.com")))
        with mock.patch("watchlist_notify_service.requests.request", side_effect=disp):
            report = svc.run(
                supabase_url=_BASE_URL, service_role_key=_SERVICE_KEY,
                brevo_api_key=_BREVO_KEY, sender_name="GRM", sender_email="noreply@grm.example",
                site_base_url="https://grm-solutions.com", dry_run=False,
                now=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
            )
        self.assertEqual(report["emails_sent"], 1)
        self.assertEqual(report["emails_failed"], 0)
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(disp.posted_smtp), 1)
        self.assertEqual(disp.posted_smtp[0]["json"]["to"], [{"email": "user1@example.com"}])
        self.assertEqual(len(disp.posted_log), 1)
        self.assertEqual(disp.posted_log[0], [{"user_id": "u1", "finding_id": "f1"}])
        # 리포트엔 원본 이메일이 아니라 마스킹된 형태만.
        report_text = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("user1@example.com", report_text)
        self.assertIn("us***@e***.com", report_text)

    def test_idempotent_skip_when_already_notified(self) -> None:
        watchlist, findings = _base_fixture()
        disp = _Dispatcher(watchlist=watchlist, findings=findings,
                          notified_log=[{"user_id": "u1", "finding_id": "f1"}],
                          admin_users=_admin_users(("u1", "user1@example.com")))
        with mock.patch("watchlist_notify_service.requests.request", side_effect=disp):
            report = svc.run(
                supabase_url=_BASE_URL, service_role_key=_SERVICE_KEY,
                brevo_api_key=_BREVO_KEY, sender_name="GRM", sender_email="noreply@grm.example",
                site_base_url="https://grm-solutions.com", dry_run=False,
                now=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
            )
        self.assertEqual(report["emails_sent"], 0)
        self.assertEqual(disp.posted_smtp, [])
        self.assertEqual(disp.posted_log, [])

    def test_cap_truncates_and_reports(self) -> None:
        watchlist = [
            {"user_id": "u1", "firm_key": "acme", "firm_display": "ACME"},
            {"user_id": "u2", "firm_key": "acme", "firm_display": "ACME"},
        ]
        findings = [{
            "finding_id": "f1", "firm_key": "acme", "firm_name": "ACME",
            "published_date": "2026-07-10", "ingested_at": "2026-07-11T00:00:00Z",
            "scope_status": "ok",
        }]
        disp = _Dispatcher(watchlist=watchlist, findings=findings, notified_log=[],
                          admin_users=_admin_users(("u1", "u1@example.com"),
                                                    ("u2", "u2@example.com")))
        with mock.patch("watchlist_notify_service.requests.request", side_effect=disp):
            report = svc.run(
                supabase_url=_BASE_URL, service_role_key=_SERVICE_KEY,
                brevo_api_key=_BREVO_KEY, sender_name="GRM", sender_email="noreply@grm.example",
                site_base_url="https://grm-solutions.com", dry_run=False,
                now=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
                cap=1,
            )
        self.assertEqual(report["users_capped_skipped"], 1)
        self.assertEqual(report["emails_sent"], 1)
        self.assertTrue(any("발송 상한" in e for e in report["errors"]))

    def test_missing_brevo_credentials_reports_error_no_network_send(self) -> None:
        watchlist, findings = _base_fixture()
        disp = _Dispatcher(watchlist=watchlist, findings=findings, notified_log=[],
                          admin_users=_admin_users(("u1", "user1@example.com")))
        with mock.patch("watchlist_notify_service.requests.request", side_effect=disp):
            report = svc.run(
                supabase_url=_BASE_URL, service_role_key=_SERVICE_KEY,
                brevo_api_key="", sender_name="GRM", sender_email="",
                site_base_url="https://grm-solutions.com", dry_run=False,
                now=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
            )
        self.assertTrue(any("Brevo" in e for e in report["errors"]))
        self.assertEqual(disp.posted_smtp, [])

    def test_no_candidates_is_clean_noop(self) -> None:
        disp = _Dispatcher(watchlist=[], findings=[], notified_log=[], admin_users=_admin_users())
        with mock.patch("watchlist_notify_service.requests.request", side_effect=disp):
            report = svc.run(
                supabase_url=_BASE_URL, service_role_key=_SERVICE_KEY,
                brevo_api_key=_BREVO_KEY, sender_name="GRM", sender_email="noreply@grm.example",
                site_base_url="https://grm-solutions.com", dry_run=False,
                now=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
            )
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["emails_sent"], 0)


# ── 키 미노출 계약 ────────────────────────────────────────────────────────────
class KeySecrecyTest(unittest.TestCase):
    def test_service_key_never_in_report_on_http_error(self) -> None:
        def _boom(method, url, headers=None, params=None, json=None, timeout=None):
            return _FakeResponse(500)

        with mock.patch("watchlist_notify_service.requests.request", side_effect=_boom):
            report = svc.run(
                supabase_url=_BASE_URL, service_role_key=_SERVICE_KEY,
                brevo_api_key=_BREVO_KEY, sender_name="GRM", sender_email="noreply@grm.example",
                site_base_url="https://grm-solutions.com", dry_run=True,
                now=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
            )
        report_text = json.dumps(report)
        self.assertNotIn(_SERVICE_KEY, report_text)
        self.assertNotIn(_BREVO_KEY, report_text)
        self.assertTrue(any("http_500" in e for e in report["errors"]))

    def test_service_key_never_in_report_on_exception_path(self) -> None:
        import requests as _requests

        def _raise(method, url, headers=None, params=None, json=None, timeout=None):
            raise _requests.exceptions.ConnectionError(f"key={_SERVICE_KEY} leaked")

        with mock.patch("watchlist_notify_service.requests.request", side_effect=_raise):
            report = svc.run(
                supabase_url=_BASE_URL, service_role_key=_SERVICE_KEY,
                brevo_api_key=_BREVO_KEY, sender_name="GRM", sender_email="noreply@grm.example",
                site_base_url="https://grm-solutions.com", dry_run=True,
                now=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
            )
        report_text = json.dumps(report)
        self.assertNotIn(_SERVICE_KEY, report_text)
        self.assertTrue(any("ConnectionError" in e for e in report["errors"]))


# ── CLI ───────────────────────────────────────────────────────────────────────
class CliTest(unittest.TestCase):
    def test_missing_credentials_exits_2(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            with mock.patch("watchlist_notify_service.requests.request") as req:
                rc = svc.main(["--dry-run"])
        self.assertEqual(rc, 2)
        req.assert_not_called()

    def test_dry_run_cli_reports_zero_when_no_watchlist(self) -> None:
        disp = _Dispatcher(watchlist=[], findings=[], notified_log=[], admin_users=_admin_users())
        env = {
            "SUPABASE_URL": _BASE_URL, "SUPABASE_SERVICE_ROLE_KEY": _SERVICE_KEY,
            "NEWSLETTER_API_KEY": _BREVO_KEY, "GRM_NEWSLETTER_SENDER_EMAIL": "noreply@grm.example",
        }
        with mock.patch.dict(os.environ, env):
            with mock.patch("watchlist_notify_service.requests.request", side_effect=disp):
                rc = svc.main(["--dry-run"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
