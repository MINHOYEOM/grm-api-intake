import unittest
from datetime import date

from daily_reconcile_ledger import build_daily_rows


DAY = date(2026, 11, 25)


def _health(total_count=10, **changes):
    return {
        "total_count": total_count,
        "collected": 2,
        "pages_seen": 1,
        "truncated": False,
        "page_warnings": [],
        **changes,
    }


class DailyReconcileLedgerTest(unittest.TestCase):
    def test_builds_one_row_per_source_using_api_total_and_db_count(self):
        rows = build_daily_rows(
            DAY,
            {"mfds_admin": _health(10, filtered=4), "mfds_recall": _health(5)},
            {"mfds_admin": 0, "mfds_recall": 7},
        )

        self.assertEqual(rows, [
            {
                "day": "2026-11-25", "source": "mfds_admin", "source_total": 10,
                "db_count": 0, "difference": 10, "measurement_status": "measured",
                "pages_seen": 1, "truncated": False, "page_warning_count": 0,
            },
            {
                "day": "2026-11-25", "source": "mfds_recall", "source_total": 5,
                "db_count": 7, "difference": -2, "measurement_status": "measured",
                "pages_seen": 1, "truncated": False, "page_warning_count": 0,
            },
        ])

    def test_unverified_source_total_cannot_produce_a_difference_zero(self):
        self._assert_unverified_source_total_cannot_produce_a_difference(0)

    def test_unverified_source_total_cannot_produce_a_difference_missing(self):
        self._assert_unverified_source_total_cannot_produce_a_difference(None)

    def test_unverified_source_total_cannot_produce_a_difference_boolean(self):
        self._assert_unverified_source_total_cannot_produce_a_difference(True)

    def test_unverified_source_total_cannot_produce_a_difference_negative(self):
        self._assert_unverified_source_total_cannot_produce_a_difference(-1)

    def test_unverified_source_total_cannot_produce_a_difference_string(self):
        self._assert_unverified_source_total_cannot_produce_a_difference('10')

    def _assert_unverified_source_total_cannot_produce_a_difference(self, total_count):
        row, _ = build_daily_rows(DAY, {"mfds_admin": _health(total_count)}, {"mfds_admin": 0})

        self.assertEqual(row["source_total"], (0 if type(total_count) is int and total_count == 0 else None))
        self.assertIsNone(row["difference"])
        self.assertEqual(row["measurement_status"], "unavailable")

    def test_missing_source_or_db_count_is_recorded(self):
        admin, recall = build_daily_rows(DAY, {"mfds_recall": _health()}, {"mfds_admin": 0})

        self.assertIsNone(admin["source_total"])
        self.assertEqual(admin["db_count"], 0)
        self.assertEqual(recall["source_total"], 10)
        self.assertIsNone(recall["db_count"])
        for row in (admin, recall):
            with self.subTest(source=row["source"]):
                self.assertEqual(row["measurement_status"], "unavailable")
                self.assertIsNone(row["difference"])

    def test_none_health_marks_only_that_source_unavailable(self):
        admin, recall = build_daily_rows(
            DAY,
            {"mfds_admin": None, "mfds_recall": _health(5)},
            {"mfds_admin": 3, "mfds_recall": 7},
        )

        self.assertEqual(admin, {
            "day": "2026-11-25", "source": "mfds_admin", "source_total": None,
            "db_count": 3, "difference": None, "measurement_status": "unavailable",
            "pages_seen": None, "truncated": None, "page_warning_count": None,
        })
        self.assertEqual(recall["source"], "mfds_recall")
        self.assertEqual(recall["measurement_status"], "measured")
        self.assertEqual(recall["difference"], -2)

    def test_partial_collection_is_visible_even_if_counts_match_when_truncated(self):
        self._assert_partial_collection_is_visible_even_if_counts_match({"truncated": True}, 0)

    def test_partial_collection_is_visible_even_if_counts_match_with_page_warning(self):
        self._assert_partial_collection_is_visible_even_if_counts_match({"page_warnings": ['page=2 실패']}, 1)

    def _assert_partial_collection_is_visible_even_if_counts_match(self, changes, warning_count):
        row, _ = build_daily_rows(DAY, {"mfds_admin": _health(**changes)}, {"mfds_admin": 10})

        self.assertEqual(row["difference"], 0)
        self.assertEqual(row["measurement_status"], "incomplete")
        self.assertEqual(row["page_warning_count"], warning_count)
        self.assertIs(row["truncated"], changes.get("truncated", False))

    def test_no_successful_page_cannot_produce_a_difference(self):
        row, _ = build_daily_rows(DAY, {"mfds_admin": _health(pages_seen=0)}, {"mfds_admin": 10})

        self.assertIsNone(row["difference"])
        self.assertEqual(row["measurement_status"], "unavailable")
