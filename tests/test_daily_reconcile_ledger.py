from datetime import date

import pytest

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


def test_builds_one_row_per_source_using_api_total_and_db_count():
    rows = build_daily_rows(
        DAY,
        {"mfds_admin": _health(10, filtered=4), "mfds_recall": _health(5)},
        {"mfds_admin": 0, "mfds_recall": 7},
    )

    assert rows == [
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
    ]


@pytest.mark.parametrize("total_count", [0, None, True, -1, "10"])
def test_unverified_source_total_cannot_produce_a_difference(total_count):
    row, _ = build_daily_rows(DAY, {"mfds_admin": _health(total_count)}, {"mfds_admin": 0})

    assert row["source_total"] == (0 if type(total_count) is int and total_count == 0 else None)
    assert row["difference"] is None
    assert row["measurement_status"] == "unavailable"


def test_missing_source_or_db_count_is_recorded():
    admin, recall = build_daily_rows(DAY, {"mfds_recall": _health()}, {"mfds_admin": 0})

    assert admin["source_total"] is None
    assert admin["db_count"] == 0
    assert recall["source_total"] == 10
    assert recall["db_count"] is None
    assert all(row["measurement_status"] == "unavailable" for row in (admin, recall))
    assert all(row["difference"] is None for row in (admin, recall))


@pytest.mark.parametrize(
    ("changes", "warning_count"),
    [({"truncated": True}, 0), ({"page_warnings": ["page=2 실패"]}, 1)],
)
def test_partial_collection_is_visible_even_if_counts_match(changes, warning_count):
    row, _ = build_daily_rows(DAY, {"mfds_admin": _health(**changes)}, {"mfds_admin": 10})

    assert row["difference"] == 0
    assert row["measurement_status"] == "incomplete"
    assert row["page_warning_count"] == warning_count
    assert row["truncated"] is changes.get("truncated", False)


def test_no_successful_page_cannot_produce_a_difference():
    row, _ = build_daily_rows(DAY, {"mfds_admin": _health(pages_seen=0)}, {"mfds_admin": 10})

    assert row["difference"] is None
    assert row["measurement_status"] == "unavailable"
