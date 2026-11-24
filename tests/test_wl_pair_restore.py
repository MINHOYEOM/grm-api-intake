import hashlib
import json
from pathlib import Path

import pytest

import findings_translate_apply_service as translate_apply
import raw_body_followup
import raw_update
import wl_pair_restore


OLD = "Heading\nOld body."
NEW = "Heading\nMoved body."


@pytest.fixture
def plan(monkeypatch):
    live = {
        "finding_id": "wl-finding",
        "raw_signal_id": "wl-signal",
        "source": "FDA Warning Letter",
        "finding_text": OLD,
        "review_status": "approved",
        "finding_text_ko": "검수된 번역",
        "translation_method": "human",
    }
    pairs = raw_update.pair_findings(
        [live], [{**live, "finding_text": NEW}], {live["finding_id"]: live}
    )
    _, document = raw_body_followup._finish(
        {"findings": {}, "supersede_waiting": {}},
        {"schema_version": raw_update.RESTORE_PLAN_SCHEMA_VERSION, "items": pairs["restore"]},
    )
    document["items"][0].update(
        review_status="rejected", finding_text_ko="다른 번역", translation_method="machine"
    )
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: json.dumps(document))
    return document, live


def test_default_previews_only_source_text(plan, monkeypatch, capsys):
    def unexpected_send(*args, **kwargs):
        pytest.fail("dry run contacted the database")

    monkeypatch.setattr(translate_apply, "_send_findings_request", unexpected_send)
    assert wl_pair_restore.main(["--plan", "plan.json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "apply": False,
        "changes": [{
            "finding_id": "wl-finding",
            "raw_signal_id": "wl-signal",
            "before_md5": {"finding_text": hashlib.md5(OLD.encode()).hexdigest()},
            "after": {"finding_text": NEW},
        }],
    }


def test_apply_preserves_human_fields_and_is_idempotent(plan, monkeypatch, capsys):
    _, live = plan
    original = live.copy()
    patches = []

    def send(verb, base_url, key, *, params, json_body=None):
        assert params["finding_id"] == "eq.wl-finding"
        assert params["raw_signal_id"] == "eq.wl-signal"
        assert params["source"] == "eq.FDA Warning Letter"
        if verb == "get":
            return 200, [{"finding_text": live["finding_text"]}], ""
        assert params["finding_text"] == f"eq.{live['finding_text']}"
        patches.append(json_body)
        live.update(json_body)
        return 200, [{"finding_id": "wl-finding"}], ""

    monkeypatch.setattr(translate_apply, "_send_findings_request", send)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    for expected in ("applied", "already_applied"):
        assert wl_pair_restore.main(["--plan", "plan.json", "--apply"]) == 0
        assert json.loads(capsys.readouterr().out)["results"][0]["status"] == expected
        assert live == {**original, "finding_text": NEW}
    assert patches == [{"finding_text": NEW}]


def test_empty_plan_is_noop_without_credentials(plan, monkeypatch, capsys):
    document, _ = plan
    document["items"].clear()
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    for flags in ([], ["--apply"]):
        assert wl_pair_restore.main(["--plan", "plan.json", *flags]) == 0
        assert json.loads(capsys.readouterr().out) == {"apply": bool(flags), "changes": []}


@pytest.mark.parametrize(("rows", "expected"), [
    ([], "no_match"),
    ([{"finding_text": "Newer edit"}], "stale"),
])
def test_missing_or_stale_row_is_not_patched(plan, rows, expected):
    def send(verb, *args, **kwargs):
        assert verb == "get"
        return 200, rows, ""

    change, = wl_pair_restore.load_changes("plan.json")
    assert wl_pair_restore.apply_change(change, "https://example.invalid", "key", send=send) == expected


def test_concurrent_change_is_not_overwritten(plan):
    def send(verb, *args, params, **kwargs):
        if verb == "get":
            return 200, [{"finding_text": OLD}], ""
        assert params["finding_text"] == f"eq.{OLD}"
        return 200, [], ""

    change, = wl_pair_restore.load_changes("plan.json")
    assert wl_pair_restore.apply_change(change, "https://example.invalid", "key", send=send) == "concurrent_change"
