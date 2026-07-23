# -*- coding: utf-8 -*-
"""collect_eu_ncr_backfill 회귀.

collect_eu_gmp_ncr(실네트워크·PDF 아카이브) 와 append(PostgREST) 는 둘 다 대역으로
치환한다 — 이 스크립트의 책임은 '수집 결과를 멱등 적재하고 정확히 집계/종료코드화'뿐이라
배관 자체는 각자 모듈 테스트가 이미 커버한다.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_eu_ncr_backfill as mod

START, END = date(2015, 1, 1), date(2026, 7, 23)
COLLECTED_AT = "2026-07-23T00:00:00+00:00"


class _Item:
    def __init__(self, doc_ref):
        self.document_id = doc_ref


class _Res:
    def __init__(self, status, errors=()):
        self.status = status
        self.errors = tuple(errors)


def _collector(items, err=None):
    return lambda s, e: (list(items), err)


class TestDryRun(unittest.TestCase):
    def test_dry_run_never_appends(self):
        items = [_Item("EEA-0001"), _Item("EEA-0002")]
        appender = mock.Mock()
        report, code = mod.run(
            start=START, end=END, dry_run=True, base_url="", service_key="",
            collected_at=COLLECTED_AT, collector=_collector(items), appender=appender,
        )
        self.assertEqual(code, 0)
        self.assertEqual(report.collected, 2)
        self.assertEqual(report.appended, 0)
        self.assertEqual(report.would_append, ["EEA-0001", "EEA-0002"])
        appender.assert_not_called()


class TestAppendClassification(unittest.TestCase):
    def test_all_inserted(self):
        items = [_Item("A"), _Item("B"), _Item("C")]
        appender = mock.Mock(side_effect=[_Res("inserted"), _Res("inserted"),
                                          _Res("raw_signal_inserted")])
        report, code = mod.run(
            start=START, end=END, dry_run=False, base_url="https://x", service_key="k",
            collected_at=COLLECTED_AT, collector=_collector(items), appender=appender,
        )
        self.assertEqual(code, 0)
        self.assertEqual(report.appended, 3)
        self.assertEqual(report.failed, 0)
        # collected_at 전달 확인.
        _, kwargs = appender.call_args
        self.assertEqual(kwargs["collected_at"], COLLECTED_AT)

    def test_duplicate_is_idempotent_success(self):
        items = [_Item("A"), _Item("B")]
        appender = mock.Mock(side_effect=[_Res("duplicate"), _Res("duplicate")])
        report, code = mod.run(
            start=START, end=END, dry_run=False, base_url="https://x", service_key="k",
            collected_at=COLLECTED_AT, collector=_collector(items), appender=appender,
        )
        self.assertEqual(code, 0)
        self.assertEqual(report.duplicate, 2)
        self.assertEqual(report.appended, 0)
        self.assertEqual(report.failed, 0)

    def test_partial_counts_as_appended_with_warning(self):
        items = [_Item("A")]
        appender = mock.Mock(return_value=_Res("partial", errors=("finding row POST failed",)))
        report, code = mod.run(
            start=START, end=END, dry_run=False, base_url="https://x", service_key="k",
            collected_at=COLLECTED_AT, collector=_collector(items), appender=appender,
        )
        self.assertEqual(code, 0)
        self.assertEqual(report.partial, 1)
        self.assertEqual(report.appended, 1)
        self.assertTrue(any("append_partial" in e for e in report.errors))

    def test_error_status_fails_run(self):
        items = [_Item("A"), _Item("B")]
        appender = mock.Mock(side_effect=[_Res("inserted"),
                                          _Res("error", errors=("raw_signals POST failed: http_500",))])
        report, code = mod.run(
            start=START, end=END, dry_run=False, base_url="https://x", service_key="k",
            collected_at=COLLECTED_AT, collector=_collector(items), appender=appender,
        )
        self.assertEqual(code, 1)
        self.assertEqual(report.appended, 1)
        self.assertEqual(report.failed, 1)
        self.assertTrue(any("append_failed(B)" in e for e in report.errors))

    def test_append_exception_is_caught_and_counted(self):
        items = [_Item("A")]
        appender = mock.Mock(side_effect=RuntimeError("boom"))
        report, code = mod.run(
            start=START, end=END, dry_run=False, base_url="https://x", service_key="k",
            collected_at=COLLECTED_AT, collector=_collector(items), appender=appender,
        )
        self.assertEqual(code, 1)
        self.assertEqual(report.failed, 1)
        self.assertTrue(any("append_raised(A)" in e for e in report.errors))


class TestCollectorFailureNotSilent(unittest.TestCase):
    def test_collect_error_exits_2(self):
        appender = mock.Mock()
        report, code = mod.run(
            start=START, end=END, dry_run=False, base_url="https://x", service_key="k",
            collected_at=COLLECTED_AT,
            collector=_collector([], err="세션/검색 실패"), appender=appender,
        )
        self.assertEqual(code, 2)
        self.assertEqual(report.collected, 0)
        self.assertTrue(any("collect_failed" in e for e in report.errors))
        appender.assert_not_called()


class TestMainCredGuard(unittest.TestCase):
    def test_real_load_requires_creds(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        with mock.patch.dict(os.environ, env, clear=True):
            code = mod.main(["--from-date", "2015-01-01"])
        self.assertEqual(code, 2)

    def test_dry_run_needs_no_creds(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(mod, "collect_eu_gmp_ncr", _collector([_Item("A")])):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = mod.main(["--dry-run", "--from-date", "2015-01-01"])
        self.assertEqual(code, 0)
        self.assertIn("would_append", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
