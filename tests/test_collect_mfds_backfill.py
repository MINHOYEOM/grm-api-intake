# -*- coding: utf-8 -*-
"""collect_mfds_backfill 회귀.

data.go.kr 수집기(실네트워크)와 append(PostgREST)는 둘 다 대역으로 치환한다 — 이
스크립트의 책임은 '수집 결과를 멱등 적재하고 정확히 집계/종료코드화' + '페이지 상한을
런타임에 덮어쓰기' + '상한 도달을 수집 실패와 구분' 뿐이라 배관 자체는 각 모듈 테스트가
이미 커버한다.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mfds_admin_action
import collect_mfds_recall
import collect_mfds_backfill as mod

START, END = date(2025, 2, 1), date(2026, 8, 5)
COLLECTED_AT = "2026-08-05T00:00:00+00:00"


class _Item:
    def __init__(self, doc_ref, date_iso="2026-01-15"):
        self.document_id = doc_ref
        self.date_iso = date_iso


class _Res:
    def __init__(self, status, errors=()):
        self.status = status
        self.errors = tuple(errors)


def _collector(items, err=None):
    """수집기 대역. ★MFDS collector 는 인자 3개(start, end, service_key)다."""
    return lambda s, e, k: (list(items), err)


def _run(**kw):
    base = dict(
        source="admin-action", start=START, end=END, dry_run=False,
        base_url="https://x", service_key="k", datago_key="dk",
        collected_at=COLLECTED_AT,
    )
    base.update(kw)
    return mod.run(**base)


class TestDryRun(unittest.TestCase):
    def test_dry_run_never_appends(self):
        items = [_Item("admin-1", "2025-03-04"), _Item("admin-2", "2026-01-15")]
        appender = mock.Mock()
        report, code = _run(dry_run=True, collector=_collector(items), appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.collected, 2)
        self.assertEqual(report.appended, 0)
        self.assertEqual(report.would_append, ["admin-1", "admin-2"])
        appender.assert_not_called()

    def test_dry_run_reports_window_coverage(self):
        """건수만으로는 '창을 실제로 덮었는지'를 못 본다 — 실측 날짜 범위/월별 분포를 낸다."""
        items = [_Item("a", "2025-03-04"), _Item("b", "2025-03-20"), _Item("c", "2026-01-15")]
        report, _ = _run(dry_run=True, collector=_collector(items), appender=mock.Mock())
        self.assertEqual(report.date_min, "2025-03-04")
        self.assertEqual(report.date_max, "2026-01-15")
        self.assertEqual(report.by_month, {"2025-03": 2, "2026-01": 1})


class TestSourceSelection(unittest.TestCase):
    def test_admin_action_uses_admin_collector(self):
        with mock.patch.object(collect_mfds_admin_action, "collect_mfds_admin_actions",
                               _collector([_Item("admin-1")])) as _:
            report, code = _run(source="admin-action", dry_run=True, appender=mock.Mock())
        self.assertEqual(code, 0)
        self.assertEqual(report.would_append, ["admin-1"])

    def test_recall_uses_recall_collector(self):
        with mock.patch.object(collect_mfds_recall, "collect_mfds_recall",
                               _collector([_Item("recall-1")])):
            report, code = _run(source="recall", dry_run=True, appender=mock.Mock())
        self.assertEqual(code, 0)
        self.assertEqual(report.source, "recall")
        self.assertEqual(report.would_append, ["recall-1"])

    def test_unknown_source_exits_2(self):
        appender = mock.Mock()
        report, code = _run(source="nope", collector=_collector([_Item("x")]), appender=appender)
        self.assertEqual(code, 2)
        self.assertTrue(any("bad_source" in e for e in report.errors))
        appender.assert_not_called()

    def test_collector_receives_service_key(self):
        seen = {}

        def _spy(s, e, k):
            seen.update(start=s, end=e, key=k)
            return [], None

        _run(dry_run=True, collector=_spy, appender=mock.Mock(), datago_key="SVCKEY")
        self.assertEqual(seen, {"start": START, "end": END, "key": "SVCKEY"})


class TestPageCap(unittest.TestCase):
    def test_override_applies_during_collect_and_restores_after(self):
        original = collect_mfds_admin_action.MAX_PAGES
        seen = {}

        def _spy(s, e, k):
            seen["cap"] = collect_mfds_admin_action.MAX_PAGES
            return [], None

        report, _ = _run(dry_run=True, collector=_spy, appender=mock.Mock(), max_pages=120)
        self.assertEqual(seen["cap"], 120)
        self.assertEqual(report.max_pages, 120)
        self.assertEqual(collect_mfds_admin_action.MAX_PAGES, original)

    def test_no_override_keeps_collector_default(self):
        original = collect_mfds_admin_action.MAX_PAGES
        report, _ = _run(dry_run=True, collector=_collector([]), appender=mock.Mock())
        self.assertEqual(report.max_pages, original)
        self.assertEqual(collect_mfds_admin_action.MAX_PAGES, original)

    def test_cap_restored_even_if_collector_raises(self):
        original = collect_mfds_recall.MAX_PAGES

        def _boom(s, e, k):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            _run(source="recall", dry_run=True, collector=_boom,
                 appender=mock.Mock(), max_pages=99)
        self.assertEqual(collect_mfds_recall.MAX_PAGES, original)


class TestTruncationIsNotCollectFailure(unittest.TestCase):
    """★상한 도달은 (items, err) 로 **둘 다** 돌아온다. err 만 보고 버리면 수집물을 통째로
    잃는다(EU NCR 템플릿을 그대로 복제하면 여기서 걸린다)."""

    def test_truncated_still_appends_and_exits_3(self):
        items = [_Item("A"), _Item("B")]
        err = "MFDS admin-action API max_pages=20 도달 — truncated (수집 2건, totalCount=9999)"
        appender = mock.Mock(side_effect=[_Res("inserted"), _Res("inserted")])
        report, code = _run(collector=_collector(items, err=err), appender=appender)
        self.assertEqual(code, 3)
        self.assertTrue(report.truncated)
        self.assertEqual(report.appended, 2)
        self.assertTrue(any("collect_truncated" in e for e in report.errors))

    def test_truncated_dry_run_also_exits_3(self):
        err = "MFDS recall API max_pages=25 도달 — truncated (수집 1건, totalCount=500)"
        report, code = _run(source="recall", dry_run=True,
                            collector=_collector([_Item("A")], err=err), appender=mock.Mock())
        self.assertEqual(code, 3)
        self.assertTrue(report.truncated)

    def test_append_failure_outranks_truncation(self):
        err = "MFDS admin-action API max_pages=20 도달 — truncated (수집 1건)"
        appender = mock.Mock(return_value=_Res("error", errors=("http_500",)))
        report, code = _run(collector=_collector([_Item("A")], err=err), appender=appender)
        self.assertEqual(code, 1)
        self.assertTrue(report.truncated)
        self.assertEqual(report.failed, 1)


class TestAppendClassification(unittest.TestCase):
    def test_all_inserted(self):
        items = [_Item("A"), _Item("B"), _Item("C")]
        appender = mock.Mock(side_effect=[_Res("inserted"), _Res("inserted"),
                                          _Res("raw_signal_inserted")])
        report, code = _run(collector=_collector(items), appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.appended, 3)
        self.assertEqual(report.failed, 0)
        _, kwargs = appender.call_args
        self.assertEqual(kwargs["collected_at"], COLLECTED_AT)

    def test_duplicate_is_idempotent_success(self):
        appender = mock.Mock(side_effect=[_Res("duplicate"), _Res("duplicate")])
        report, code = _run(collector=_collector([_Item("A"), _Item("B")]), appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.duplicate, 2)
        self.assertEqual(report.appended, 0)
        self.assertEqual(report.failed, 0)

    def test_partial_counts_as_appended_with_warning(self):
        appender = mock.Mock(return_value=_Res("partial", errors=("finding row POST failed",)))
        report, code = _run(collector=_collector([_Item("A")]), appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.partial, 1)
        self.assertEqual(report.appended, 1)
        self.assertTrue(any("append_partial" in e for e in report.errors))

    def test_error_status_fails_run(self):
        appender = mock.Mock(side_effect=[_Res("inserted"),
                                          _Res("error", errors=("raw_signals POST failed",))])
        report, code = _run(collector=_collector([_Item("A"), _Item("B")]), appender=appender)
        self.assertEqual(code, 1)
        self.assertEqual(report.appended, 1)
        self.assertEqual(report.failed, 1)
        self.assertTrue(any("append_failed(B)" in e for e in report.errors))

    def test_append_exception_is_caught_and_counted(self):
        appender = mock.Mock(side_effect=RuntimeError("boom"))
        report, code = _run(collector=_collector([_Item("A")]), appender=appender)
        self.assertEqual(code, 1)
        self.assertEqual(report.failed, 1)
        self.assertTrue(any("append_raised(A)" in e for e in report.errors))


class TestCollectorFailureNotSilent(unittest.TestCase):
    def test_collect_error_exits_2(self):
        appender = mock.Mock()
        report, code = _run(collector=_collector([], err="DATA_GO_KR_SERVICE_KEY 환경변수 필요"),
                            appender=appender)
        self.assertEqual(code, 2)
        self.assertEqual(report.collected, 0)
        self.assertTrue(any("collect_failed" in e for e in report.errors))
        appender.assert_not_called()


class TestUrlVerifyGuard(unittest.TestCase):
    """nedrug 은 해외 IP 차단 — 러너에서 건별 verify 가 켜지면 전건이 느리게 실패한다."""

    def test_flag_on_aborts_before_collect(self):
        collector = mock.Mock()
        appender = mock.Mock()
        with mock.patch.dict(os.environ, {"ENABLE_MFDS_URL_VERIFY": "true"}):
            report, code = _run(collector=collector, appender=appender)
        self.assertEqual(code, 2)
        self.assertTrue(any("guard_violation" in e for e in report.errors))
        collector.assert_not_called()
        appender.assert_not_called()


class TestMainCli(unittest.TestCase):
    def test_real_load_requires_supabase_creds(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        with mock.patch.dict(os.environ, env, clear=True):
            code = mod.main(["--from-date", "2025-02-01"])
        self.assertEqual(code, 2)

    def test_dry_run_needs_no_supabase_creds(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(collect_mfds_admin_action, "collect_mfds_admin_actions",
                                  _collector([_Item("A")])):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = mod.main(["--dry-run", "--from-date", "2025-02-01"])
        self.assertEqual(code, 0)
        self.assertIn("would_append", buf.getvalue())

    def test_max_pages_from_env(self):
        seen = {}

        def _spy(s, e, k):
            seen["cap"] = collect_mfds_admin_action.MAX_PAGES
            return [], None

        with mock.patch.dict(os.environ, {"GRM_MFDS_BACKFILL_MAX_PAGES": "77"}), \
                mock.patch.object(collect_mfds_admin_action, "collect_mfds_admin_actions", _spy):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = mod.main(["--dry-run", "--from-date", "2025-02-01"])
        self.assertEqual(code, 0)
        self.assertEqual(seen["cap"], 77)

    def test_reversed_window_exits_2(self):
        code = mod.main(["--dry-run", "--from-date", "2026-08-05", "--to-date", "2025-01-01"])
        self.assertEqual(code, 2)

    def test_service_key_falls_back_to_env(self):
        seen = {}

        def _spy(s, e, k):
            seen["key"] = k
            return [], None

        with mock.patch.dict(os.environ, {"DATA_GO_KR_SERVICE_KEY": "ENVKEY"}), \
                mock.patch.object(collect_mfds_admin_action, "collect_mfds_admin_actions", _spy):
            buf = io.StringIO()
            with redirect_stdout(buf):
                mod.main(["--dry-run", "--from-date", "2025-02-01"])
        self.assertEqual(seen["key"], "ENVKEY")


if __name__ == "__main__":
    unittest.main()
