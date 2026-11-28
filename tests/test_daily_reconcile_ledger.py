import io
import json
import re
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import collect_mfds_admin_action
import collect_mfds_recall
import daily_reconcile_ledger as ledger
import grm_common
from tests.test_migration_function_grants import _statements, _violations


DAY = date(2026, 11, 25)
DB_ENV = {"SUPABASE_URL": "https://db.example", "SUPABASE_SERVICE_ROLE_KEY": "test"}
MIGRATION = Path(__file__).resolve().parents[1] / "web/migrations/171_coverage_daily.sql"


def _health(**changes):
    return {
        "total_count": 10,
        "raw_count": 10,
        "window_count": 6,
        "collected": 3,
        "excluded_count": 2,
        "duplicate_count": 1,
        "window_start": "2026-10-26",
        "window_end": DAY.isoformat(),
        "pages_seen": 1,
        "truncated": False,
        "page_warnings": [],
        **changes,
    }


class DailyReconcileLedgerTest(unittest.TestCase):
    def test_counts_only_source_window_and_separates_exclusions_and_duplicates(self):
        admin, recall = ledger.build_daily_rows(
            DAY, {"mfds_admin": _health(), "mfds_recall": _health()},
            {"mfds_admin": 3, "mfds_recall": 4},
        )
        self.assertEqual(admin["source_total"], 10)
        self.assertEqual(admin["source_count"], 6)
        self.assertEqual(admin["excluded_count"], 2)
        self.assertEqual(admin["duplicate_count"], 1)
        self.assertEqual(admin["difference"], 3)
        self.assertEqual(admin["unexplained_difference"], 0)
        self.assertEqual(admin["measurement_status"], "measured")
        self.assertEqual(recall["unexplained_difference"], -1)

    def test_missing_or_failed_collection_is_unavailable_even_if_counts_match(self):
        rows = ledger.build_daily_rows(
            DAY, {"mfds_admin": _health(failed=True)}, {"mfds_admin": 3, "mfds_recall": 0},
        )
        for row in rows:
            with self.subTest(source=row["source"]):
                self.assertEqual(row["measurement_status"], "unavailable")
                self.assertIsNone(row["difference"])
                self.assertIsNone(row["unexplained_difference"])

    def test_unverified_source_total_or_missing_db_count_is_unavailable(self):
        for total in (None, 0, True, -1, "10"):
            with self.subTest(total=total):
                row, _ = ledger.build_daily_rows(
                    DAY, {"mfds_admin": _health(total_count=total)}, {"mfds_admin": 3},
                )
                self.assertIsNone(row["difference"])
                self.assertEqual(row["measurement_status"], "unavailable")

        row, _ = ledger.build_daily_rows(DAY, {"mfds_admin": _health()}, {})
        self.assertEqual(row["measurement_status"], "unavailable")

    def test_partial_collection_cannot_raise_a_mismatch(self):
        for change in ({"truncated": True}, {"page_warnings": ["page=2 실패"]}, {"raw_count": 9}):
            with self.subTest(change=change):
                row, _ = ledger.build_daily_rows(
                    DAY, {"mfds_admin": _health(**change)}, {"mfds_admin": 3},
                )
                self.assertIsNone(row["unexplained_difference"])
                self.assertEqual(row["measurement_status"], "incomplete")

    def test_collectors_count_rows_after_date_filter_and_before_dedup(self):
        for module, collect in (
            (collect_mfds_admin_action, collect_mfds_admin_action.collect_mfds_admin_actions),
            (collect_mfds_recall, collect_mfds_recall.collect_mfds_recall),
        ):
            with self.subTest(module=module.__name__):
                outside = {"date": "2026-10-25"}
                inside = {"date": "2026-10-26"}
                paginator = Mock(total_count=4, truncated=False)
                paginator.__iter__ = Mock(return_value=iter([([outside, inside, inside, inside], "")]))
                # 실제 _DatagoPaginator 처럼 상한 건강값(dict)을 돌려준다(N4-1 cap_health).
                paginator.health = Mock(
                    side_effect=lambda label: grm_common.cap_health(label, 4, 1),
                )
                item = SimpleNamespace(document_id="same", signal_tier="Tier 3")
                with (
                    patch.object(module, "LAST_HEALTH", {}),
                    patch.object(module, "datago_paginate", return_value=paginator),
                    patch.object(module, "_item_date", side_effect=lambda raw: raw["date"]),
                    patch.object(module, "_to_item", side_effect=[None, item, item]),
                    patch.object(module, "log"),
                ):
                    items, error = collect(date(2026, 10, 26), DAY, "test")
                    self.assertIsNone(error)
                    self.assertEqual(items, [item])
                    self.assertEqual(module.LAST_HEALTH["raw_count"], 4)
                    self.assertEqual(module.LAST_HEALTH["window_count"], 3)
                    self.assertEqual(module.LAST_HEALTH["excluded_count"], 1)
                    self.assertEqual(module.LAST_HEALTH["duplicate_count"], 1)

    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(dir=Path.cwd()))
        self.health_path = Path(directory) / "grm-health.json"
        self.health = {
            "run_date_kst": DAY.isoformat(),
            "mfds_reconcile_health": {"mfds_admin": _health(), "mfds_recall": _health()},
        }
        self.enterContext(patch.dict(ledger.os.environ, DB_ENV, clear=True))
        self.head = self.enterContext(patch.object(ledger.requests, "head"))
        self.head.return_value.headers = {"Content-Range": "0-2/3"}
        self.post = self.enterContext(patch.object(ledger.requests, "post"))
        self.alert = self.enterContext(patch.object(ledger, "_alert"))

    def _run(self, *args):
        self.health_path.write_text(json.dumps(self.health), encoding="utf-8")
        with io.TextIOWrapper(io.BytesIO(), encoding="ascii") as output:
            with redirect_stdout(output):
                self.assertEqual(ledger.main(["--health", str(self.health_path), *args]), 0)
            self.assertEqual(output.encoding, "utf-8")
            output.flush()
            return json.loads(output.buffer.getvalue())

    def test_default_dry_run_reads_windowed_counts_without_writing_or_alerting(self):
        self.head.return_value.headers = {"Content-Range": "0-1/2"}
        result = self._run()
        self.assertEqual(self.head.call_count, 2)
        for source, call in zip(ledger.SUPPORTED_SOURCES, self.head.call_args_list):
            self.assertEqual(call.args, ("https://db.example/rest/v1/raw_signals",))
            self.assertEqual(call.kwargs["params"], {
                "select": "raw_signal_id",
                "source": "eq.MFDS",
                "official_url": f"eq.{ledger.DATASET_URLS[source]}",
                "and": "(published_date.gte.2026-10-26,published_date.lte.2026-11-25)",
            })
            self.assertEqual(call.kwargs["headers"]["Prefer"], "count=exact")
        self.assertEqual(result["mfds_admin"]["unexplained_difference"], 1)
        self.post.assert_not_called()
        self.alert.assert_not_called()

    def test_apply_saves_one_row_and_alerts_only_unexplained_difference(self):
        self.head.side_effect = [
            Mock(headers={"Content-Range": "0-2/3"}),
            Mock(headers={"Content-Range": "*/0"}),
        ]
        result = self._run("--apply")
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.post.call_args.args, ("https://db.example/rest/v1/coverage_daily",))
        self.assertEqual(self.post.call_args.kwargs["params"], {"on_conflict": "day"})
        self.assertEqual(self.post.call_args.kwargs["json"], result)
        self.assertEqual(set(result), {"day", "mfds_admin", "mfds_recall"})
        self.alert.assert_called_once_with(DAY, [result["mfds_recall"]])

    def test_collection_failure_still_saves_daily_unavailable_row(self):
        result = self._run("--apply", "--collection-failed")
        self.head.assert_not_called()
        self.alert.assert_not_called()
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["json"], result)
        self.assertEqual(result["day"], DAY.isoformat())
        for source in ledger.SUPPORTED_SOURCES:
            self.assertEqual(result[source]["measurement_status"], "unavailable")

    def test_intake_dry_run_never_writes_even_with_apply(self):
        self.health["dry_run"] = True
        self._run("--apply")
        self.post.assert_not_called()
        self.alert.assert_not_called()

    def test_sql_enables_rls_and_grants_no_client_access(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        statements = [" ".join(statement.lower().split()) for statement, _ in _statements(sql)]
        self.assertIn("alter table public.coverage_daily enable row level security", statements)
        self.assertIn(
            "revoke all on table public.coverage_daily from public, anon, authenticated",
            statements,
        )
        self.assertEqual(
            [statement for statement in statements if re.match(r"^grant\b", statement)],
            ["grant select, insert, update on table public.coverage_daily to service_role"],
        )
        self.assertFalse(_violations([(MIGRATION.name, sql)]))


class DailyReconcileAlertTest(unittest.TestCase):
    def test_alert_reuses_issue_route_and_skips_existing_daily_issue(self):
        row, _ = ledger.build_daily_rows(DAY, {"mfds_admin": _health()}, {"mfds_admin": 2})
        with patch.object(ledger.subprocess, "run") as gh:
            gh.return_value.stdout = "[]"
            ledger._alert(DAY, [row])

            command = gh.call_args.args[0]
            self.assertEqual(command[:3], ["gh", "issue", "create"])
            title = command[command.index("--title") + 1]
            self.assertTrue(title.startswith("[GRM 문제]"))
            self.assertIn("grm-intake,automation-warning", command)
            self.assertIn("설명되지 않은 차이 1건", command[command.index("--body") + 1])

            gh.reset_mock()
            gh.return_value.stdout = json.dumps([{"title": title}])
            ledger._alert(DAY, [row])
            gh.assert_called_once()
            self.assertEqual(gh.call_args.args[0][:3], ["gh", "issue", "list"])
