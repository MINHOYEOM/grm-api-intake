import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import backfill_blank_excerpts as b


LONG_TEXT = "x" * 2400 + " END"
ALERT_URL = b.MHRA_ALERT_PREFIX + "medicines-recall"


def row(source, document_id, raw, *, url="", **metadata):
    return {
        "source": source,
        "document_id": document_id,
        "official_url": url,
        "raw_json": json.dumps(raw),
        "row_json": json.dumps(metadata),
    }


@pytest.fixture
def readers():
    return b.Readers(
        Mock(return_value=("new excerpt", {"outcome": "report"}, "ok")),
        Mock(return_value=LONG_TEXT),
        Mock(return_value=LONG_TEXT),
    )


def test_report_9_3_selection_and_default_dry_run(readers):
    rows = [
        row(b.SOURCE_WHO, "who-whopir-1", {"pdf_url": "https://who.int/1.pdf",
                                          "whopir_excerpt": "existing"}),
        row(b.SOURCE_WHO, "who-whopir-2", {"whopir_report": {"report": "existing"}}),
        row(b.SOURCE_WHO, "who-news-1", {"channel": "news"}),
        row(b.SOURCE_EMA, "ema-reviewed", {}, url="https://ema.europa.eu/excluded",
            qa_relevance="Unrelated"),
        row(b.SOURCE_EMA, "ema-undecided", {"article_excerpt": " "},
            official_url="https://ema.europa.eu/news"),
        row(b.SOURCE_EMA, "ema-reviewed-in", {}, url="https://ema.europa.eu/likely",
            qa_relevance="Likely"),
        row(b.SOURCE_EMA, "ema-filled", {"article_excerpt": "existing"}),
        row(b.SOURCE_ECA, "eca", {"link": "https://gmp-compliance.org/news"}),
        row(b.SOURCE_ECA, "eca-filled", {"eca_article_excerpt": "existing"}),
        row(b.SOURCE_MHRA, "alert", {"link": ALERT_URL}),
        row(b.SOURCE_MHRA, "alert-filled", {"mhra_alert_body": "existing"}, url=ALERT_URL),
        row(b.SOURCE_MHRA, "blog", {}, url="https://mhrainspectorate.blog.gov.uk/post"),
    ]
    write = Mock()

    results = b.backfill(rows, readers, write=write)

    assert {r["document_id"]: r["status"] for r in results} == {
        "who-whopir-1": "would_apply",
        "ema-reviewed": "excluded_unrelated",
        "ema-undecided": "would_apply",
        "ema-reviewed-in": "would_apply",
        "eca": "would_apply",
        "alert": "would_apply",
    }
    assert results[0]["fields"] == ["whopir_report"]
    readers.whopir.assert_called_once_with("https://who.int/1.pdf")
    assert [call.args for call in readers.article.call_args_list] == [
        ("https://ema.europa.eu/news", "EMA"),
        ("https://ema.europa.eu/likely", "EMA"),
        ("https://gmp-compliance.org/news", "ECA"),
    ]
    readers.mhra.assert_called_once_with(ALERT_URL)
    write.assert_not_called()


@pytest.mark.parametrize(("source", "field", "url"), [
    (b.SOURCE_EMA, "article_excerpt", "https://ema.europa.eu/news"),
    (b.SOURCE_ECA, "eca_article_excerpt", "https://gmp-compliance.org/news"),
    (b.SOURCE_MHRA, "mhra_alert_body", ALERT_URL),
])
def test_apply_preserves_full_text_and_existing_payload(readers, source, field, url):
    original = row(source, "doc", {"metadata": {"retain": True}}, url=url)
    write = Mock()

    results = b.backfill([original], readers, apply=True, write=write)

    assert results[0]["status"] == "applied"
    write.assert_called_once_with(original, {"metadata": {"retain": True}, field: LONG_TEXT}, [field])
    assert json.loads(original["raw_json"]) == {"metadata": {"retain": True}}


def test_whopir_partial_and_empty_results(readers):
    readers.whopir.return_value = ("excerpt only", None, "no-structure")
    readers.article.return_value = " "
    rows = [
        row(b.SOURCE_WHO, "whopir", {"channel": "whopir", "pdf_url": "https://who.int/1.pdf"}),
        row(b.SOURCE_ECA, "eca", {}, url="https://gmp-compliance.org/news"),
        row(b.SOURCE_EMA, "missing-url", {}),
    ]
    write = Mock()

    results = b.backfill(rows, readers, apply=True, write=write)

    assert [r["status"] for r in results] == ["applied", "empty_excerpt", "missing_url"]
    assert results[0]["complete"] is False
    assert write.call_args.args[1]["whopir_excerpt"] == "excerpt only"
    assert write.call_count == 1


def test_version_conflict_is_reported_and_other_rows_continue(readers):
    rows = [row(b.SOURCE_MHRA, "conflict", {}, url=ALERT_URL),
            row(b.SOURCE_MHRA, "next", {}, url=ALERT_URL)]
    write = Mock(side_effect=[b.rv.ApplyVersionError("conflict"), None])

    results = b.backfill(rows, readers, apply=True, write=write)

    assert [r["status"] for r in results] == ["write_failed", "applied"]
    assert results[0]["reason"] == "conflict"


def test_article_adapter_reuses_collector_without_1200_character_limit(monkeypatch):
    response = SimpleNamespace(text=f"<article><p>{LONG_TEXT}</p></article>",
                               status_code=200, raise_for_status=Mock())
    get = Mock(return_value=response)
    monkeypatch.setattr(b.intake.requests, "get", get)
    monkeypatch.setattr(b.time, "sleep", Mock())

    assert b._read_article("https://gmp-compliance.org/news", "ECA") == LONG_TEXT
    get.assert_called_once()
