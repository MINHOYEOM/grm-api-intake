#!/usr/bin/env python3
"""FIND-1 M2c static search export -> offline HTML viewer tests."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import unittest
from html.parser import HTMLParser

import findings_search_export as search_export
import findings_search_page as search_page
import findings_store as store
import grm_findings as gf


def _pair(
    *,
    source: str,
    document_id: str,
    date: str,
    firm: str,
    category_code: str,
    evidence_level: str,
    review_status: str,
    finding_text: str,
    site_country: str = "US",
) -> tuple[dict, dict]:
    row = {
        "source": source,
        "document_id": document_id,
        "date": date,
        "headline": f"[{source}] {firm}",
        "firm": firm,
        "type_or_class": "483" if "FDA" in source else "gmp-inspection",
        "site_country": site_country,
        "modality": "Drug",
        "source_url": f"https://example.com/{document_id}",
        "official_url": f"https://example.com/official/{document_id}",
    }
    raw = {"firm": firm, "detail": "sample raw payload"}
    raw_signal = gf.raw_signal_from_row(row, raw, collected_at="2026-07-01T00:00:00+00:00")
    finding = gf.finding_from_raw_signal(
        raw_signal,
        finding_text=finding_text,
        category_code=category_code,
        evidence_level=evidence_level,
        review_status=review_status,
    )
    return raw_signal, finding


def _sample_pairs() -> list[tuple[dict, dict]]:
    return [
        _pair(
            source="FDA 483",
            document_id="fda-1",
            date="2026-07-05",
            firm="Acme Pharma",
            category_code="data_integrity",
            evidence_level="A",
            review_status="accepted",
            finding_text="Failure to review batch records.",
        ),
        _pair(
            source="MFDS",
            document_id="mfds-1",
            date="2026-06-20",
            firm="Korea BioPharma",
            category_code="cleaning_validation",
            evidence_level="A",
            review_status="accepted",
            finding_text="세척 밸리데이션 잔류 기준 미달.",
            site_country="KR",
        ),
        _pair(
            source="WHO",
            document_id="who-1",
            date="2026-05-15",
            firm="Global Vax",
            category_code="environmental_monitoring",
            evidence_level="C",
            review_status="rejected",
            finding_text="Environmental monitoring excursion noted.",
            site_country="ZA",
        ),
    ]


def _seed_db(db_path: str, pairs: list[tuple[dict, dict]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        store.ensure_findings_schema(conn)
        for raw_signal, finding in pairs:
            result = store.append_raw_signal_with_findings(conn, raw_signal, [finding])
            assert result.findings_invalid == 0, result.errors
        conn.commit()
    finally:
        conn.close()


class _ScriptTagCounter(HTMLParser):
    """Counts real <script> elements the way a browser's HTML parser would.

    HTMLParser treats <script> as CDATA content (like a browser does): once
    inside a <script> element it only looks for a literal "</script" close
    sequence, so a payload string like "<script>" embedded in the JSON data
    island does not register as a second start tag.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.script_tag_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.script_tag_count += 1


def _extract_embedded_json(html_text: str) -> str:
    match = re.search(
        r'<script type="application/json" id="findings-data">(.*?)</script>',
        html_text,
        re.DOTALL,
    )
    assert match is not None, "findings-data script tag not found"
    return match.group(1)


class BuildSearchPageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        _seed_db(self.db_path, _sample_pairs())
        self.export = search_export.build_search_export(self.db_path)

    def test_deterministic_across_repeated_builds(self) -> None:
        first = search_page.build_search_page(self.export)
        second = search_page.build_search_page(self.export)
        self.assertEqual(first, second)

    def test_rejects_wrong_schema_version(self) -> None:
        bad = dict(self.export)
        bad["schema_version"] = "grm-findings-search/v0"
        with self.assertRaises(ValueError):
            search_page.build_search_page(bad)

    def test_rejects_not_ready_for_viewer(self) -> None:
        bad = json.loads(json.dumps(self.export))
        bad["report"]["ready_for_viewer"] = False
        with self.assertRaises(ValueError):
            search_page.build_search_page(bad)

    def test_script_tag_close_sequence_is_escaped(self) -> None:
        export = json.loads(json.dumps(self.export))
        export["records"][0]["finding_text"] = (
            "Malicious payload </script><script>alert(1)</script> in finding text."
        )

        page = search_page.build_search_page(export)

        embedded = _extract_embedded_json(page)
        self.assertNotIn("</script", embedded)
        self.assertIn("<\\/script", embedded)

        # Exactly two real <script> elements: the JSON data island + the app JS.
        # (A literal "<script>" substring embedded in the payload text does not
        # count as a real tag because HTMLParser -- like a browser -- treats
        # <script> content as CDATA and only looks for a literal "</script"
        # close sequence, which we've neutralized above.)
        counter = _ScriptTagCounter()
        counter.feed(page)
        self.assertEqual(counter.script_tag_count, 2)

    def test_findings_data_script_round_trips_records(self) -> None:
        page = search_page.build_search_page(self.export)

        embedded = _extract_embedded_json(page)
        records = json.loads(embedded)

        self.assertEqual(len(records), len(self.export["records"]))
        self.assertEqual(
            {r["finding_id"] for r in records},
            {r["finding_id"] for r in self.export["records"]},
        )

    def test_required_ui_markers_present(self) -> None:
        page = search_page.build_search_page(self.export)

        self.assertIn('lang="ko"', page)
        self.assertIn('charset="utf-8"', page)
        self.assertIn('id="search-input"', page)
        self.assertIn('id="result-count"', page)
        for select_id in (
            "filter-agency",
            "filter-category",
            "filter-source",
            "filter-evidence",
            "filter-review",
            "filter-month",
        ):
            self.assertIn(f'id="{select_id}"', page)

    def test_category_option_uses_korean_label_and_code(self) -> None:
        page = search_page.build_search_page(self.export)
        self.assertIn("데이터 완전성 (data_integrity)", page)
        self.assertIn("세척밸리데이션 (cleaning_validation)", page)

    def test_no_external_resource_references_in_chrome_markup(self) -> None:
        page = search_page.build_search_page(self.export)
        embedded = _extract_embedded_json(page)
        chrome_markup = page.replace(embedded, "")

        self.assertNotIn('src="http', chrome_markup)
        self.assertNotIn("src='http", chrome_markup)
        self.assertNotIn('href="http', chrome_markup)
        self.assertNotIn("href='http", chrome_markup)
        self.assertNotIn("cdn.", chrome_markup)

    def test_no_innerhtml_usage(self) -> None:
        page = search_page.build_search_page(self.export)
        self.assertNotIn("innerHTML", page)

    def test_empty_state_message_present(self) -> None:
        page = search_page.build_search_page(self.export)
        self.assertIn('id="empty-message"', page)
        self.assertIn("조건에 맞는 finding이 없습니다.", page)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        _seed_db(self.db_path, _sample_pairs())
        self.export_path = os.path.join(self._tmp.name, "findings_search_export.json")
        rc = search_export.main(["--db-path", self.db_path, "--output", self.export_path])
        assert rc == 0

    def test_cli_writes_html_output(self) -> None:
        out = os.path.join(self._tmp.name, "grm-findings-search.html")

        rc = search_page.main(["--input", self.export_path, "--output", out])

        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8", newline="") as f:
            content = f.read()
        self.assertIn("<!doctype html>", content)
        self.assertIn('id="findings-data"', content)
        self.assertNotIn("\r\n", content)

    def test_cli_missing_input_exits_2(self) -> None:
        missing = os.path.join(self._tmp.name, "missing.json")
        out = os.path.join(self._tmp.name, "grm-findings-search.html")

        rc = search_page.main(["--input", missing, "--output", out])

        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
