"""Offline regression tests for the FDA request stop and manual-run warning."""

from io import StringIO
from pathlib import Path
from runpy import run_path
from unittest.mock import Mock

import pytest
import requests

import collect_fda_483
import collect_intake
import grm_common
from grm_common import FDA_BOT_BLOCK, FdaBotBlockError

FDA_URL = "https://www.fda.gov/media/1/download"


@pytest.fixture(autouse=True)
def fresh_fda_run(monkeypatch):
    monkeypatch.setattr(grm_common, "_fda_blocked", False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def response(url=FDA_URL, body=b"", status=404):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = body
    return result


@pytest.mark.parametrize("blocked", [
    response(url="https://www.fda.gov/apology_objects/abuse-detection-apology.html"),
    response(body=b"<html>ABUSE-DETECTION-APOLOGY</html>", status=200),
    response(body=b"<html>apology_objects</html>"),
])
def test_first_block_stops_retries_and_later_fda_requests(monkeypatch, tmp_path, blocked):
    summary = tmp_path / "summary"
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    other_url = "https://example.org/"
    get = Mock(side_effect=[blocked, response(other_url, b"ok", 200)])
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(grm_common.time, "sleep", Mock(side_effect=AssertionError("retried")))

    with pytest.raises(FdaBotBlockError, match="^fda-bot-block$"):
        grm_common.http_get_bytes(FDA_URL, retries=3)
    assert collect_intake._fetch_wl_letter_html(FDA_URL) == ("", FDA_BOT_BLOCK)
    assert get.call_count == 1
    assert grm_common.http_get_html(other_url) == "ok"
    assert summary.read_text(encoding="utf-8").count(FDA_BOT_BLOCK) == 1
    assert output.read_text(encoding="utf-8") == "fda_bot_block=true\n"


def test_plain_404_does_not_stop_fda_requests(monkeypatch):
    get = Mock(side_effect=[response(), response(body=b"ok", status=200)])
    monkeypatch.setattr(requests, "get", get)

    with pytest.raises(RuntimeError, match="404") as exc:
        grm_common.http_get_html(FDA_URL, retries=0)
    assert FDA_BOT_BLOCK not in str(exc.value)
    assert grm_common.http_get_html(FDA_URL) == "ok"
    assert get.call_count == 2


def test_blocked_483_listing_reports_failure_without_fallback(monkeypatch):
    from datetime import date

    get = Mock(return_value=response(body=b"apology_objects"))
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(collect_fda_483, "_fetch_legacy_json_rows",
                        Mock(side_effect=AssertionError("continued to fallback")))
    day = date(2026, 11, 25)

    assert collect_fda_483.collect_fda_483(day, day) == ([], FDA_BOT_BLOCK)
    assert collect_fda_483.LAST_HEALTH["status"] == FDA_BOT_BLOCK
    assert get.call_count == 1


@pytest.mark.parametrize("prior_count", [2, 3])
def test_manual_warning_counts_only_prior_same_kst_day(monkeypatch, prior_count):
    script = Path(__file__).resolve().parents[1] / ".github/scripts/fda_manual_run_warning.py"
    warn = run_path(str(script))["warn_if_needed"]
    before_kst_midnight = {"databaseId": 1, "createdAt": "2026-11-24T14:59:59Z"}
    current = {"databaseId": 5, "createdAt": "2026-11-24T15:10:00Z"}
    later = {"databaseId": 6, "createdAt": "2026-11-24T15:10:01Z"}
    prior = [
        {"databaseId": 2, "createdAt": "2026-11-24T15:00:00Z"},
        {"databaseId": 3, "createdAt": "2026-11-24T15:04:00Z"},
        {"databaseId": 4, "createdAt": "2026-11-24T15:09:00Z"},
    ]
    runs_by_workflow = {
        "grm-fda-citations.yml": [before_kst_midnight, later, *prior[:1]],
        "grm-intake.yml": [current, *prior[1:prior_count]],
    }

    def gh(*args):
        if args[0] == "view":
            assert args[1] == "5"
            return current
        assert args[:2] == ("list", "--workflow")
        assert args[3:8] == ("--event", "workflow_dispatch", "--all", "--created",
                             "2026-11-24T15:00:00Z..2026-11-24T15:10:00Z")
        return runs_by_workflow.get(args[2], [])

    monkeypatch.setitem(warn.__globals__, "_gh_json", gh)
    summary = StringIO()
    warn(5, summary)
    assert ("3건 이상" in summary.getvalue()) == (prior_count >= 3)
    assert len(summary.getvalue().splitlines()) <= 1
