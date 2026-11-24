import json
from pathlib import Path

import pytest

import audit_483_inspector_blanks as tool
import findings_supabase_backfill as fsb


SIGNATURE = "EMPLOYEE(S) SIGNATURE\n"


@pytest.mark.parametrize(("body", "reason", "requires_human"), [
    ("OBSERVATION 1\nIncomplete batch records. Operator (b)(6) was present.",
     "no_signature_block", False),
    (SIGNATURE + "(b) (6), Investigator\n3/5/2026", "redacted", False),
    (SIGNATURE + "<Redacted B6>, Investigator\n3/5/2026", "redacted", False),
    (SIGNATURE + "~~ #@? ~~\n", "ocr_unreadable", True),
    (SIGNATURE + "Avery Sharpe, Investigator\n3/5/2026\n"
     + SIGNATURE + "Avery Sharp, Investigator\n3/5/2026", "needs_human", True),
    (SIGNATURE + "Avery Sharpe, Investigator\n3/5/2026", "needs_human", True),
])
def test_blank_reasons(body, reason, requires_human):
    report = tool.audit([{"document_id": "case", "raw_signal_id": "raw", "body": body}])

    assert report == {
        "mode": "dry-run",
        "scanned": 1,
        "counts": {reason: 1},
        "rows": [{"raw_signal_id": "raw", "document_id": "case",
                  "reason": reason, "requires_human": requires_human}],
    }


def test_only_blank_fda_483_rows_with_stored_text_are_reported():
    body = "OBSERVATION 1\nIncomplete batch records."
    rows = [
        {"document_id": "target", "raw_json": json.dumps({"fda483_body_full": body})},
        {"document_id": "filled", "raw_json": {"fda483_body_full": body,
                                              "fda483_inspectors": ["Avery Sharpe"]}},
        {"document_id": "out-of-scope", "body": body, "st": "out-of-scope"},
        {"document_id": "other-source", "body": body, "source": "other"},
        {"document_id": "no-body", "raw_json": {}},
        {"document_id": "whitespace", "body": " \n"},
    ]

    report = tool.audit(rows)

    assert report["scanned"] == len(rows)
    assert [row["document_id"] for row in report["rows"]] == ["target"]


def test_fixture_cli_prints_dry_report(monkeypatch, capsys):
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps([
        {"document_id": "fixture", "body": "OBSERVATION 1"},
    ]))

    assert tool.main(["--rows-json", "fixture.json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"no_signature_block": 1}


def test_read_db_does_not_use_service_role_key(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://fixture.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fixture-service-key")
    monkeypatch.delenv("SUPABASE_READ_ONLY_KEY", raising=False)
    monkeypatch.setattr(tool, "_fetch_rows", lambda *_: pytest.fail("unexpected DB read"))

    with pytest.raises(SystemExit) as exc:
        tool.main(["--read-db"])

    assert exc.value.code == 2
    assert "use --rows-json locally" in capsys.readouterr().err


def test_read_db_uses_filtered_get_with_read_only_key(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://fixture.invalid/")
    monkeypatch.setenv("SUPABASE_READ_ONLY_KEY", "fixture-read-only-key")

    class FixtureResponse:
        status_code = 200
        headers = {"Content-Range": "0-0/1"}

        def json(self):
            return [{"document_id": "stored", "raw_json": {"fda483_body_full": "OBSERVATION 1"}}]

    def get(url, *, headers, params, timeout):
        assert url == "https://fixture.invalid/rest/v1/raw_signals"
        assert headers["Authorization"] == "Bearer fixture-read-only-key"
        assert params == {
            "source": "eq.FDA 483",
            "raw_json": "like.*fda483_body_full*",
            "select": "raw_signal_id,document_id,raw_json",
            "order": "raw_signal_id.asc",
        }
        return FixtureResponse()

    monkeypatch.setattr(fsb.requests, "get", get)

    assert tool.main(["--read-db"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["document_id"] == "stored"


def test_apply_refuses_before_reading(monkeypatch, capsys):
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("unexpected fixture read"))

    with pytest.raises(SystemExit) as exc:
        tool.main(["--rows-json", "fixture.json", "--apply"])

    assert exc.value.code == 2
    assert "--apply is unavailable" in capsys.readouterr().err
