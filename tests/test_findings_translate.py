#!/usr/bin/env python3
"""FIND-1 M6b translation export/apply tool tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

import findings_store as store
import findings_supabase as fs
import findings_translate as translate
import grm_findings as gf


def _pair(
    *,
    document_id: str,
    firm: str,
    finding_text: str,
    date: str = "2026-07-01",
    source: str = "FDA 483",
    finding_text_ko: str = "",
    translation_method: str = "",
) -> tuple[dict, dict]:
    row = {
        "source": source,
        "document_id": document_id,
        "date": date,
        "headline": f"[{source}] {firm}",
        "firm": firm,
        "type_or_class": "483" if "FDA" in source else "gmp-inspection",
        "site_country": "KR" if source == "MFDS" else "US",
        "modality": "Drug",
        "source_url": f"https://example.com/{document_id}",
        "official_url": f"https://example.com/official/{document_id}",
    }
    raw = {"firm": firm, "detail": "sample raw payload"}
    raw_signal = gf.raw_signal_from_row(row, raw, collected_at="2026-07-01T00:00:00+00:00")
    finding = gf.finding_from_raw_signal(
        raw_signal,
        finding_text=finding_text,
        finding_text_ko=finding_text_ko,
        translation_method=translation_method,
    )
    return raw_signal, finding


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


def _findings_rows(db_path: str) -> dict[str, dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT finding_id, finding_text, finding_text_ko, translation_method FROM findings"
        ).fetchall()
        return {row["finding_id"]: dict(row) for row in rows}
    finally:
        conn.close()


class BuildTranslationPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        self.pairs = [
            _pair(document_id="fda-1", firm="Acme Pharma", finding_text="Batch record review was skipped.", date="2026-07-05"),
            _pair(document_id="fda-2", firm="Beta Bio", finding_text="Cleaning validation residue exceeded limit.", date="2026-06-01"),
            _pair(document_id="mfds-1", firm="Korea BioPharma", finding_text="세척 밸리데이션 잔류 기준 미달.", date="2026-07-05", source="MFDS"),
            _pair(
                document_id="fda-3",
                firm="Gamma Labs",
                finding_text="Already translated finding.",
                date="2026-07-10",
                finding_text_ko="이미 번역된 지적사항.",
                translation_method="manual",
            ),
        ]
        _seed_db(self.db_path, self.pairs)

    def test_envelope_shape(self) -> None:
        plan = translate.build_translation_plan(self.db_path)

        self.assertEqual(plan["schema_version"], translate.TRANSLATION_PLAN_SCHEMA_VERSION)
        self.assertEqual(set(plan.keys()), {"schema_version", "source_db", "items"})
        self.assertEqual(
            set(plan["source_db"].keys()), {"file_name", "findings_total", "untranslated"}
        )
        self.assertEqual(plan["source_db"]["file_name"], "grm-findings.sqlite3")
        self.assertEqual(plan["source_db"]["findings_total"], 4)
        self.assertEqual(plan["source_db"]["untranslated"], 3)

    def test_excludes_already_translated_rows(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        doc_ids = {item["finding_text"] for item in plan["items"]}
        self.assertNotIn("Already translated finding.", doc_ids)
        self.assertEqual(len(plan["items"]), 3)

    def test_item_shape_and_empty_translation_fields(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        for item in plan["items"]:
            self.assertEqual(
                set(item.keys()),
                {
                    "finding_id",
                    "source",
                    "agency",
                    "category_code",
                    "category_label_ko",
                    "published_date",
                    "firm_name",
                    "finding_text",
                    "finding_text_ko",
                    "translation_method",
                },
            )
            self.assertEqual(item["finding_text_ko"], "")
            self.assertEqual(item["translation_method"], "")

    def test_sort_order_published_date_desc_finding_id_asc(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        dates = [item["published_date"] for item in plan["items"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

        # fda-1 and mfds-1 share the same published_date (2026-07-05); tie-break
        # must be ascending finding_id.
        same_date_ids = [
            item["finding_id"] for item in plan["items"] if item["published_date"] == "2026-07-05"
        ]
        self.assertEqual(same_date_ids, sorted(same_date_ids))

    def test_read_only_export_does_not_modify_db(self) -> None:
        before = open(self.db_path, "rb").read()
        translate.build_translation_plan(self.db_path)
        after = open(self.db_path, "rb").read()
        self.assertEqual(before, after)


def _base_plan_with_valid_items(db_path: str) -> dict:
    plan = translate.build_translation_plan(db_path)
    translations = {
        "Batch record review was skipped.": ("배치 기록 검토가 누락되었다.", "llm_assisted"),
        "Cleaning validation residue exceeded limit.": ("세척 밸리데이션 잔류물이 기준을 초과했다.", "manual"),
        "세척 밸리데이션 잔류 기준 미달.": ("세척 밸리데이션 기준 미달 번역.", "llm_assisted"),
    }
    for item in plan["items"]:
        ko, method = translations[item["finding_text"]]
        item["finding_text_ko"] = ko
        item["translation_method"] = method
    return plan


class ApplyValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        self.pairs = [
            _pair(document_id="fda-1", firm="Acme Pharma", finding_text="Batch record review was skipped.", date="2026-07-05"),
            _pair(document_id="fda-2", firm="Beta Bio", finding_text="Cleaning validation residue exceeded limit.", date="2026-06-01"),
            _pair(document_id="mfds-1", firm="Korea BioPharma", finding_text="세척 밸리데이션 잔류 기준 미달.", date="2026-07-05", source="MFDS"),
        ]
        _seed_db(self.db_path, self.pairs)
        self.before_bytes = open(self.db_path, "rb").read()

    def _plan(self) -> dict:
        return _base_plan_with_valid_items(self.db_path)

    def _assert_blocked(self, plan: dict, expected_substring: str) -> None:
        result = translate.apply_translations(plan, self.db_path, write_file=False)
        self.assertFalse(result["ready"])
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["updated"], 0)
        self.assertTrue(any(expected_substring in e for e in result["errors"]), result["errors"])
        # nothing was ever written
        self.assertEqual(open(self.db_path, "rb").read(), self.before_bytes)

    def test_valid_plan_passes_with_no_errors(self) -> None:
        result = translate.apply_translations(self._plan(), self.db_path, write_file=False)
        self.assertTrue(result["ready"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["validated"], 3)
        self.assertEqual(result["updated"], 3)

    def test_schema_version_mismatch_blocks(self) -> None:
        plan = self._plan()
        plan["schema_version"] = "grm-findings-translation-plan/v0"
        self._assert_blocked(plan, "schema_version mismatch")

    def test_unknown_finding_id_blocks(self) -> None:
        plan = self._plan()
        plan["items"][0]["finding_id"] = "finding-does-not-exist"
        self._assert_blocked(plan, "not found in database")

    def test_finding_text_mismatch_blocks(self) -> None:
        plan = self._plan()
        plan["items"][0]["finding_text"] = "Someone edited the source text."
        self._assert_blocked(plan, "does not byte-match")

    def test_empty_finding_text_ko_blocks(self) -> None:
        plan = self._plan()
        plan["items"][0]["finding_text_ko"] = ""
        self._assert_blocked(plan, "finding_text_ko is empty")

    def test_finding_text_ko_without_hangul_blocks(self) -> None:
        plan = self._plan()
        plan["items"][0]["finding_text_ko"] = "This is plain English, not Korean."
        self._assert_blocked(plan, "no Hangul characters")

    def test_invalid_translation_method_blocks(self) -> None:
        plan = self._plan()
        plan["items"][0]["translation_method"] = "auto"
        self._assert_blocked(plan, "translation_method must be one of")

    def test_finding_text_ko_identical_to_finding_text_blocks(self) -> None:
        plan = self._plan()
        for item in plan["items"]:
            if item["finding_text"] == "세척 밸리데이션 잔류 기준 미달.":
                item["finding_text_ko"] = item["finding_text"]
        self._assert_blocked(plan, "identical to finding_text")

    def test_one_bad_item_blocks_the_entire_plan(self) -> None:
        """All-or-nothing: a single bad item must block every other, otherwise-valid item."""
        plan = self._plan()
        plan["items"][1]["translation_method"] = "auto"
        result = translate.apply_translations(plan, self.db_path, write_file=False)
        self.assertFalse(result["ready"])
        # even the other two valid items must not be reported as updated
        self.assertEqual(result["updated"], 0)

    def test_items_not_a_list_raises(self) -> None:
        plan = self._plan()
        plan["items"] = "not-a-list"
        with self.assertRaises(ValueError):
            translate.apply_translations(plan, self.db_path, write_file=False)

    def test_missing_db_path_raises(self) -> None:
        missing = os.path.join(os.path.dirname(self.db_path), "missing.sqlite3")
        with self.assertRaises(ValueError):
            translate.apply_translations(self._plan(), missing, write_file=False)


class DryRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        self.pairs = [
            _pair(document_id="fda-1", firm="Acme Pharma", finding_text="Batch record review was skipped.", date="2026-07-05"),
            _pair(document_id="fda-2", firm="Beta Bio", finding_text="Cleaning validation residue exceeded limit.", date="2026-06-01"),
            _pair(document_id="mfds-1", firm="Korea BioPharma", finding_text="세척 밸리데이션 잔류 기준 미달.", date="2026-07-05", source="MFDS"),
        ]
        _seed_db(self.db_path, self.pairs)

    def test_dry_run_reports_would_update_but_writes_nothing(self) -> None:
        before = open(self.db_path, "rb").read()
        plan = _base_plan_with_valid_items(self.db_path)

        result = translate.apply_translations(plan, self.db_path, write_file=False)

        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["validated"], 3)
        self.assertEqual(result["updated"], 3)
        self.assertEqual(result["skipped_already_translated"], 0)
        self.assertTrue(result["ready"])
        self.assertEqual(open(self.db_path, "rb").read(), before)

        rows = _findings_rows(self.db_path)
        self.assertTrue(all(r["finding_text_ko"] == "" for r in rows.values()))


class WriteFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        self.pairs = [
            _pair(document_id="fda-1", firm="Acme Pharma", finding_text="Batch record review was skipped.", date="2026-07-05"),
            _pair(document_id="fda-2", firm="Beta Bio", finding_text="Cleaning validation residue exceeded limit.", date="2026-06-01"),
            _pair(document_id="mfds-1", firm="Korea BioPharma", finding_text="세척 밸리데이션 잔류 기준 미달.", date="2026-07-05", source="MFDS"),
        ]
        _seed_db(self.db_path, self.pairs)

    def test_write_file_applies_updates(self) -> None:
        plan = _base_plan_with_valid_items(self.db_path)

        result = translate.apply_translations(plan, self.db_path, write_file=True)

        self.assertEqual(result["mode"], "file_write")
        self.assertEqual(result["updated"], 3)
        self.assertEqual(result["skipped_already_translated"], 0)
        self.assertTrue(result["ready"])

        rows = _findings_rows(self.db_path)
        by_text = {r["finding_text"]: r for r in rows.values()}
        self.assertEqual(
            by_text["Batch record review was skipped."]["finding_text_ko"],
            "배치 기록 검토가 누락되었다.",
        )
        self.assertEqual(
            by_text["Batch record review was skipped."]["translation_method"], "llm_assisted"
        )

    def test_rerun_without_overwrite_skips_already_translated(self) -> None:
        first_plan = _base_plan_with_valid_items(self.db_path)
        translate.apply_translations(first_plan, self.db_path, write_file=True)

        # Build a second plan with *different* translations for the same rows.
        second_plan = translate.build_translation_plan(self.db_path)
        # After the first apply, all three rows are translated so untranslated == 0.
        self.assertEqual(second_plan["source_db"]["untranslated"], 0)

        # Re-submit the same finding_ids/finding_text with new (different) ko text.
        replay_items = []
        for _, finding in self.pairs:
            replay_items.append(
                {
                    "finding_id": finding["finding_id"],
                    "finding_text": finding["finding_text"],
                    "finding_text_ko": "다른 번역문으로 덮어쓰기 시도.",
                    "translation_method": "manual",
                }
            )
        replay_plan = {
            "schema_version": translate.TRANSLATION_PLAN_SCHEMA_VERSION,
            "items": replay_items,
        }

        result = translate.apply_translations(replay_plan, self.db_path, write_file=True)

        self.assertEqual(result["mode"], "file_write")
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped_already_translated"], 3)
        self.assertTrue(result["ready"])

        rows = _findings_rows(self.db_path)
        self.assertTrue(all(r["finding_text_ko"] != "다른 번역문으로 덮어쓰기 시도." for r in rows.values()))

    def test_overwrite_flag_allows_re_translation(self) -> None:
        first_plan = _base_plan_with_valid_items(self.db_path)
        translate.apply_translations(first_plan, self.db_path, write_file=True)

        replay_items = []
        for _, finding in self.pairs:
            replay_items.append(
                {
                    "finding_id": finding["finding_id"],
                    "finding_text": finding["finding_text"],
                    "finding_text_ko": "개정된 번역문.",
                    "translation_method": "manual",
                }
            )
        replay_plan = {
            "schema_version": translate.TRANSLATION_PLAN_SCHEMA_VERSION,
            "items": replay_items,
        }

        result = translate.apply_translations(
            replay_plan, self.db_path, write_file=True, overwrite=True
        )

        self.assertEqual(result["updated"], 3)
        self.assertEqual(result["skipped_already_translated"], 0)

        rows = _findings_rows(self.db_path)
        self.assertTrue(all(r["finding_text_ko"] == "개정된 번역문." for r in rows.values()))
        self.assertTrue(all(r["translation_method"] == "manual" for r in rows.values()))


class SqlOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        self.pairs = [
            _pair(
                document_id="fda-1",
                firm="Acme Pharma",
                finding_text="Firm's batch record review was skipped.",
                date="2026-07-05",
            ),
        ]
        _seed_db(self.db_path, self.pairs)

    def test_sql_output_contains_escaped_text_and_where_clause_guard(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        plan["items"][0]["finding_text_ko"] = "회사의 배치 기록 검토가 누락되었다."
        plan["items"][0]["translation_method"] = "llm_assisted"

        sql_path = os.path.join(self._tmp.name, "grm-findings-translations-test.sql")
        result = translate.apply_translations(
            plan, self.db_path, write_file=False, sql_output=sql_path
        )

        self.assertEqual(result["sql_output_path"], sql_path)
        self.assertTrue(os.path.exists(sql_path))
        sql_text = open(sql_path, encoding="utf-8").read()

        self.assertIn("update public.findings set", sql_text)
        escaped_text = fs.pg_quote_text("Firm's batch record review was skipped.")
        self.assertIn(escaped_text, sql_text)
        self.assertIn("Firm''s batch record review was skipped.", sql_text)
        self.assertIn(f"and finding_text = {escaped_text}", sql_text)
        self.assertIn("select count(*) as translated_count from public.findings", sql_text)

    def test_sql_output_written_even_without_write_file(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        plan["items"][0]["finding_text_ko"] = "회사의 배치 기록 검토가 누락되었다."
        plan["items"][0]["translation_method"] = "manual"
        sql_path = os.path.join(self._tmp.name, "grm-findings-translations-dryrun.sql")

        translate.apply_translations(plan, self.db_path, write_file=False, sql_output=sql_path)

        self.assertTrue(os.path.exists(sql_path))
        # sidecar itself was never written to
        rows = _findings_rows(self.db_path)
        self.assertTrue(all(r["finding_text_ko"] == "" for r in rows.values()))

    def test_sql_output_not_written_when_validation_fails(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        plan["items"][0]["finding_text_ko"] = "no hangul here"
        plan["items"][0]["translation_method"] = "manual"
        sql_path = os.path.join(self._tmp.name, "grm-findings-translations-blocked.sql")

        result = translate.apply_translations(
            plan, self.db_path, write_file=False, sql_output=sql_path
        )

        self.assertFalse(result["ready"])
        self.assertEqual(result["sql_output_path"], "")
        self.assertFalse(os.path.exists(sql_path))


class OutboxOutputTest(unittest.TestCase):
    """[FIND-1 M9b] --outbox-output: queued batch JSON for the unattended CI
    apply service. Mirrors SqlOutputTest's guarantees (all-or-nothing,
    additive/optional, never touches the sidecar) but for the new outbox
    schema instead of the SQL file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        self.pairs = [
            _pair(
                document_id="fda-1",
                firm="Acme Pharma",
                finding_text="Firm's batch record review was skipped.",
                date="2026-07-05",
            ),
        ]
        _seed_db(self.db_path, self.pairs)

    def test_outbox_output_contains_expected_item_schema(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        plan["items"][0]["finding_text_ko"] = "회사의 배치 기록 검토가 누락되었다."
        plan["items"][0]["translation_method"] = "llm_assisted"

        outbox_path = os.path.join(self._tmp.name, "outbox-batch.json")
        result = translate.apply_translations(
            plan, self.db_path, write_file=False, outbox_output=outbox_path
        )

        self.assertEqual(result["outbox_output_path"], outbox_path)
        self.assertTrue(os.path.exists(outbox_path))
        with open(outbox_path, encoding="utf-8") as f:
            items = json.load(f)

        self.assertEqual(len(items), 1)
        self.assertEqual(
            set(items[0].keys()),
            {"finding_id", "finding_text", "finding_text_ko", "translation_method"},
        )
        self.assertEqual(items[0]["finding_text"], "Firm's batch record review was skipped.")
        self.assertEqual(items[0]["finding_text_ko"], "회사의 배치 기록 검토가 누락되었다.")
        self.assertEqual(items[0]["translation_method"], "llm_assisted")
        self.assertEqual(items[0]["finding_id"], plan["items"][0]["finding_id"])

    def test_outbox_output_written_even_without_write_file(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        plan["items"][0]["finding_text_ko"] = "회사의 배치 기록 검토가 누락되었다."
        plan["items"][0]["translation_method"] = "manual"
        outbox_path = os.path.join(self._tmp.name, "outbox-dryrun.json")

        translate.apply_translations(
            plan, self.db_path, write_file=False, outbox_output=outbox_path
        )

        self.assertTrue(os.path.exists(outbox_path))
        rows = _findings_rows(self.db_path)
        self.assertTrue(all(r["finding_text_ko"] == "" for r in rows.values()))

    def test_outbox_output_not_written_when_validation_fails(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        plan["items"][0]["finding_text_ko"] = "no hangul here"
        plan["items"][0]["translation_method"] = "manual"
        outbox_path = os.path.join(self._tmp.name, "outbox-blocked.json")

        result = translate.apply_translations(
            plan, self.db_path, write_file=False, outbox_output=outbox_path
        )

        self.assertFalse(result["ready"])
        self.assertEqual(result["outbox_output_path"], "")
        self.assertFalse(os.path.exists(outbox_path))

    def test_outbox_output_coexists_with_sql_output(self) -> None:
        plan = translate.build_translation_plan(self.db_path)
        plan["items"][0]["finding_text_ko"] = "회사의 배치 기록 검토가 누락되었다."
        plan["items"][0]["translation_method"] = "llm_assisted"
        outbox_path = os.path.join(self._tmp.name, "outbox-both.json")
        sql_path = os.path.join(self._tmp.name, "sql-both.sql")

        result = translate.apply_translations(
            plan,
            self.db_path,
            write_file=False,
            outbox_output=outbox_path,
            sql_output=sql_path,
        )

        self.assertTrue(result["ready"])
        self.assertTrue(os.path.exists(outbox_path))
        self.assertTrue(os.path.exists(sql_path))

    def test_outbox_output_excludes_skipped_already_translated_rows(self) -> None:
        first_plan = translate.build_translation_plan(self.db_path)
        first_plan["items"][0]["finding_text_ko"] = "첫 번째 번역."
        first_plan["items"][0]["translation_method"] = "manual"
        translate.apply_translations(first_plan, self.db_path, write_file=True)

        replay_plan = {
            "schema_version": translate.TRANSLATION_PLAN_SCHEMA_VERSION,
            "items": [
                {
                    "finding_id": first_plan["items"][0]["finding_id"],
                    "finding_text": first_plan["items"][0]["finding_text"],
                    "finding_text_ko": "다른 번역문으로 덮어쓰기 시도.",
                    "translation_method": "manual",
                }
            ],
        }
        outbox_path = os.path.join(self._tmp.name, "outbox-skip.json")

        result = translate.apply_translations(
            replay_plan, self.db_path, write_file=False, outbox_output=outbox_path
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["skipped_already_translated"], 1)
        with open(outbox_path, encoding="utf-8") as f:
            items = json.load(f)
        self.assertEqual(items, [])

    def test_outbox_output_supabase_source(self) -> None:
        item = {
            "finding_id": "f-001",
            "source": "FDA 483",
            "agency": "FDA",
            "category_code": "documentation",
            "category_label_ko": "문서관리",
            "published_date": "2026-07-05",
            "firm_name": "Firm 1",
            "finding_text": "Observation number 1 was not documented.",
            "finding_text_ko": "관찰사항 1 국문 번역.",
            "translation_method": "llm_assisted",
        }
        plan = {
            "schema_version": translate.TRANSLATION_PLAN_SCHEMA_VERSION,
            "items": [item],
        }
        live_row = {
            "finding_id": "f-001",
            "finding_text": "Observation number 1 was not documented.",
            "finding_text_ko": "",
        }
        outbox_path = os.path.join(self._tmp.name, "outbox-supabase.json")
        sql_path = os.path.join(self._tmp.name, "sql-supabase.sql")

        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, [live_row]),
        ):
            result = translate.apply_translations_supabase(
                plan,
                _SB_URL,
                _SB_KEY,
                sql_output=sql_path,
                outbox_output=outbox_path,
            )

        self.assertTrue(result["ready"])
        self.assertEqual(result["outbox_output_path"], outbox_path)
        with open(outbox_path, encoding="utf-8") as f:
            items = json.load(f)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["finding_id"], "f-001")
        self.assertEqual(items[0]["finding_text_ko"], "관찰사항 1 국문 번역.")


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        self.pairs = [
            _pair(document_id="fda-1", firm="Acme Pharma", finding_text="Batch record review was skipped.", date="2026-07-05"),
            _pair(document_id="fda-2", firm="Beta Bio", finding_text="Cleaning validation residue exceeded limit.", date="2026-06-01"),
        ]
        _seed_db(self.db_path, self.pairs)

    def test_cli_export_writes_plan_and_exits_zero(self) -> None:
        out = os.path.join(self._tmp.name, "plan.json")

        rc = translate.main(["--db-path", self.db_path, "--export", "--output", out, "--pretty"])

        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            plan = json.load(f)
        self.assertEqual(plan["schema_version"], translate.TRANSLATION_PLAN_SCHEMA_VERSION)
        self.assertEqual(len(plan["items"]), 2)

    def test_cli_requires_exactly_one_mode(self) -> None:
        rc_neither = translate.main(["--db-path", self.db_path])
        self.assertEqual(rc_neither, 2)

        plan_path = os.path.join(self._tmp.name, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": translate.TRANSLATION_PLAN_SCHEMA_VERSION, "items": []}, f)
        rc_both = translate.main(
            ["--db-path", self.db_path, "--export", "--apply", plan_path]
        )
        self.assertEqual(rc_both, 2)

    def test_cli_export_missing_db_exits_2(self) -> None:
        missing = os.path.join(self._tmp.name, "missing.sqlite3")
        rc = translate.main(["--db-path", missing, "--export"])
        self.assertEqual(rc, 2)

    def test_cli_apply_missing_translations_file_exits_2(self) -> None:
        missing = os.path.join(self._tmp.name, "missing-plan.json")
        rc = translate.main(["--db-path", self.db_path, "--apply", missing])
        self.assertEqual(rc, 2)

    def test_cli_apply_with_invalid_item_exits_3(self) -> None:
        plan = _base_plan_with_valid_items_two_row(self.db_path)
        plan["items"][0]["translation_method"] = "auto"
        plan_path = os.path.join(self._tmp.name, "bad-plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)

        rc = translate.main(["--db-path", self.db_path, "--apply", plan_path])

        self.assertEqual(rc, 3)

    def test_cli_apply_dry_run_exits_0(self) -> None:
        plan = _base_plan_with_valid_items_two_row(self.db_path)
        plan_path = os.path.join(self._tmp.name, "good-plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        out = os.path.join(self._tmp.name, "apply-report.json")

        rc = translate.main(["--db-path", self.db_path, "--apply", plan_path, "--output", out])

        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["updated"], 2)

    def test_cli_apply_write_file_exits_0_and_commits(self) -> None:
        plan = _base_plan_with_valid_items_two_row(self.db_path)
        plan_path = os.path.join(self._tmp.name, "good-plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)

        rc = translate.main(
            ["--db-path", self.db_path, "--apply", plan_path, "--write-file"]
        )

        self.assertEqual(rc, 0)
        rows = _findings_rows(self.db_path)
        self.assertTrue(all(r["finding_text_ko"] != "" for r in rows.values()))


def _base_plan_with_valid_items_two_row(db_path: str) -> dict:
    plan = translate.build_translation_plan(db_path)
    translations = {
        "Batch record review was skipped.": ("배치 기록 검토가 누락되었다.", "llm_assisted"),
        "Cleaning validation residue exceeded limit.": ("세척 밸리데이션 잔류물이 기준을 초과했다.", "manual"),
    }
    for item in plan["items"]:
        ko, method = translations[item["finding_text"]]
        item["finding_text_ko"] = ko
        item["translation_method"] = method
    return plan


# ---------------------------------------------------------------------------
# FIND-1 M8a: --source supabase (live PostgREST, anon read-only). All HTTP is
# mocked via findings_translate.requests.get/.post -- no real network access.
#
# [RLS bridge] Since 009_findings_translation_bridge.sql, both the --export queue
# and the --apply live-row validation call an RPC (requests.post to
# rest/v1/rpc/<function>) first, falling back to the legacy gate-limited REST GET
# path only on a 404 (RPC not deployed to that environment yet). _FakeResponse is
# used to mock both requests.get and requests.post -- same (status_code, json())
# shape either way.
# ---------------------------------------------------------------------------

from unittest import mock  # noqa: E402  (M8a additions only; header untouched)


_SB_URL = "https://example.supabase.co"
_SB_KEY = "anon-public-key"


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = dict(headers or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


# Pre-009 tests referred to this name for GET-only mocking; kept as an alias so any
# stray reference (or a reader's muscle memory) still resolves correctly.
_FakeGetResponse = _FakeResponse


def _sb_item(i: int, *, date: str = "2026-07-05") -> dict:
    return {
        "finding_id": f"f-{i:03d}",
        "source": "FDA 483",
        "agency": "FDA",
        "category_code": "documentation",
        "category_label_ko": "문서관리",
        "published_date": date,
        "firm_name": f"Firm {i}",
        "finding_text": f"Observation number {i} was not documented.",
        "finding_text_ko": "",
        "translation_method": "",
    }


def _sb_live_row(item: dict, *, finding_text_ko: str = "") -> dict:
    return {
        "finding_id": item["finding_id"],
        "finding_text": item["finding_text"],
        "finding_text_ko": finding_text_ko,
    }


def _sb_filled_plan(items: list[dict]) -> dict:
    filled = []
    for item in items:
        entry = dict(item)
        entry["finding_text_ko"] = f"관찰사항 {entry['finding_id']} 국문 번역."
        entry["translation_method"] = "llm_assisted"
        filled.append(entry)
    return {
        "schema_version": translate.TRANSLATION_PLAN_SCHEMA_VERSION,
        "items": filled,
    }


class SupabaseExportTest(unittest.TestCase):
    """[RLS bridge] --export now calls rpc/findings_translation_queue (POST) first,
    falling back to the legacy gate-limited REST GET only on a 404 (RPC not deployed).
    The findings_total count probe is unchanged -- it always uses the plain GET
    Content-Range path regardless of which queue path served the items."""

    def test_export_uses_rpc_queue_and_plan_uses_rpc_total_not_page_length(self) -> None:
        rows = [_sb_item(1), _sb_item(2, date="2026-06-01")]
        # untranslated_total (57) intentionally differs from len(items) (2) -- this is
        # the exact defect the 009 bridge fixes: the RPC's live count must be trusted
        # over the (possibly capped) page of items.
        envelope = {"untranslated_total": 57, "items": rows}
        count_resp = _FakeResponse(200, [], headers={"Content-Range": "0-0/9999"})
        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, envelope),
        ) as post:
            with mock.patch(
                "findings_translate.requests.get",
                return_value=count_resp,
            ) as get:
                plan = translate.build_translation_plan_supabase(_SB_URL, _SB_KEY)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 1)

        post_args, post_kwargs = post.call_args
        self.assertEqual(
            post_args[0], f"{_SB_URL}/rest/v1/rpc/findings_translation_queue"
        )
        self.assertEqual(post_kwargs["json"], {"p_limit": 1000})
        self.assertEqual(post_kwargs["headers"]["apikey"], _SB_KEY)
        self.assertEqual(post_kwargs["headers"]["Authorization"], f"Bearer {_SB_KEY}")
        self.assertEqual(post_kwargs["timeout"], 15)

        count_args, count_kwargs = get.call_args
        self.assertEqual(count_args[0], f"{_SB_URL}/rest/v1/findings")
        self.assertEqual(count_kwargs["params"], {"select": "finding_id", "limit": "1"})
        self.assertEqual(count_kwargs["headers"]["Prefer"], "count=exact")

        self.assertEqual(plan["schema_version"], translate.TRANSLATION_PLAN_SCHEMA_VERSION)
        self.assertEqual(plan["queue_source"], "rpc")
        self.assertEqual(
            plan["source_db"],
            {
                "file_name": "supabase:example.supabase.co",
                "findings_total": 9999,
                "untranslated": 57,
            },
        )
        self.assertTrue(plan["truncated_possible"])
        self.assertNotIn("count_unavailable", plan)
        # item shape matches the sqlite export contract exactly
        for item in plan["items"]:
            self.assertEqual(
                set(item.keys()),
                {
                    "finding_id",
                    "source",
                    "agency",
                    "category_code",
                    "category_label_ko",
                    "published_date",
                    "firm_name",
                    "finding_text",
                    "finding_text_ko",
                    "translation_method",
                },
            )
            self.assertEqual(item["finding_text_ko"], "")
            self.assertEqual(item["translation_method"], "")

    def test_export_not_truncated_when_rpc_total_equals_page_length(self) -> None:
        rows = [_sb_item(1)]
        envelope = {"untranslated_total": 1, "items": rows}
        count_resp = _FakeResponse(200, [], headers={"Content-Range": "0-0/5"})
        with mock.patch(
            "findings_translate.requests.post", return_value=_FakeResponse(200, envelope)
        ):
            with mock.patch(
                "findings_translate.requests.get", return_value=count_resp
            ):
                plan = translate.build_translation_plan_supabase(_SB_URL, _SB_KEY)

        self.assertNotIn("truncated_possible", plan)
        self.assertEqual(plan["source_db"]["untranslated"], 1)

    def test_export_falls_back_to_legacy_rest_when_rpc_missing(self) -> None:
        rows = [_sb_item(1), _sb_item(2, date="2026-06-01")]
        count_resp = _FakeResponse(200, [], headers={"Content-Range": "0-0/42"})
        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(404),
        ) as post:
            with mock.patch(
                "findings_translate.requests.get",
                side_effect=[_FakeResponse(200, rows), count_resp],
            ) as get:
                plan = translate.build_translation_plan_supabase(_SB_URL, _SB_KEY)

        self.assertEqual(post.call_count, 1)
        # legacy export GET + count GET
        self.assertEqual(get.call_count, 2)

        legacy_args, legacy_kwargs = get.call_args_list[0]
        self.assertEqual(legacy_args[0], f"{_SB_URL}/rest/v1/findings")
        legacy_params = legacy_kwargs["params"]
        self.assertEqual(legacy_params["finding_text_ko"], "eq.")
        self.assertEqual(legacy_params["order"], "published_date.desc,finding_id.asc")
        self.assertEqual(legacy_params["limit"], "1000")
        self.assertEqual(
            legacy_params["select"],
            "finding_id,source,agency,category_code,category_label_ko,"
            "published_date,firm_name,finding_text,finding_text_ko,translation_method",
        )

        self.assertEqual(plan["queue_source"], "legacy_rest_gate_limited")
        # legacy path has no exact total -- untranslated falls back to len(items),
        # which is the pre-009 (gate-limited, known-undercounted) behavior.
        self.assertEqual(plan["source_db"]["untranslated"], 2)
        self.assertEqual(plan["source_db"]["findings_total"], 42)

    def test_export_rpc_invalid_envelope_shape_raises(self) -> None:
        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, {"items": "not-a-list", "untranslated_total": 1}),
        ):
            with self.assertRaises(ValueError) as ctx:
                translate.build_translation_plan_supabase(_SB_URL, _SB_KEY)
        self.assertIn("invalid_response_shape", str(ctx.exception))

    def test_export_count_failure_falls_back_to_minus_one(self) -> None:
        envelope = {"untranslated_total": 1, "items": [_sb_item(1)]}
        # count probe answers 200 but without a parseable Content-Range header
        count_resp = _FakeResponse(200, [], headers={})
        with mock.patch(
            "findings_translate.requests.post", return_value=_FakeResponse(200, envelope)
        ):
            with mock.patch(
                "findings_translate.requests.get", return_value=count_resp
            ):
                plan = translate.build_translation_plan_supabase(_SB_URL, _SB_KEY)

        self.assertEqual(plan["source_db"]["findings_total"], -1)
        self.assertIs(plan["count_unavailable"], True)
        self.assertEqual(plan["source_db"]["untranslated"], 1)

    def test_export_count_http_error_also_falls_back(self) -> None:
        envelope = {"untranslated_total": 1, "items": [_sb_item(1)]}
        with mock.patch(
            "findings_translate.requests.post", return_value=_FakeResponse(200, envelope)
        ):
            with mock.patch(
                "findings_translate.requests.get", return_value=_FakeResponse(403)
            ):
                plan = translate.build_translation_plan_supabase(_SB_URL, _SB_KEY)

        self.assertEqual(plan["source_db"]["findings_total"], -1)
        self.assertIs(plan["count_unavailable"], True)

    def test_export_rpc_5xx_retries_once_then_succeeds(self) -> None:
        envelope = {"untranslated_total": 1, "items": [_sb_item(1)]}
        count_resp = _FakeResponse(200, [], headers={"Content-Range": "0-0/7"})
        with mock.patch(
            "findings_translate.requests.post",
            side_effect=[_FakeResponse(503), _FakeResponse(200, envelope)],
        ) as post:
            with mock.patch(
                "findings_translate.requests.get", return_value=count_resp
            ) as get:
                plan = translate.build_translation_plan_supabase(_SB_URL, _SB_KEY)

        self.assertEqual(post.call_count, 2)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(plan["source_db"]["findings_total"], 7)
        self.assertEqual(plan["source_db"]["untranslated"], 1)

    def test_export_rpc_5xx_exhausted_raises_http_summary(self) -> None:
        with mock.patch(
            "findings_translate.requests.post",
            side_effect=[_FakeResponse(503), _FakeResponse(503)],
        ) as post:
            with self.assertRaises(ValueError) as ctx:
                translate.build_translation_plan_supabase(_SB_URL, _SB_KEY)

        self.assertEqual(post.call_count, 2)
        self.assertIn("http_503", str(ctx.exception))
        self.assertNotIn(_SB_KEY, str(ctx.exception))

    def test_export_non_https_url_rejected_without_network(self) -> None:
        with mock.patch("findings_translate.requests.post") as post:
            with mock.patch("findings_translate.requests.get") as get:
                with self.assertRaises(ValueError):
                    translate.build_translation_plan_supabase(
                        "http://example.supabase.co", _SB_KEY
                    )
        post.assert_not_called()
        get.assert_not_called()


class SupabaseApplyTest(unittest.TestCase):
    """[RLS bridge] --apply's live-row validation fetch now calls
    rpc/findings_translation_rows (POST, one call per _SUPABASE_VALIDATE_BATCH_SIZE-id
    batch) first, falling back to the legacy per-batch REST GET only on a 404 (RPC not
    deployed). Batching size/order is unchanged from the pre-009 GET path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _sql_path(self, name: str = "live-updates.sql") -> str:
        return os.path.join(self._tmp.name, name)

    def test_apply_valid_plan_writes_sql_only_report_and_sql_file(self) -> None:
        items = [_sb_item(1), _sb_item(2)]
        plan = _sb_filled_plan(items)
        live_rows = [_sb_live_row(item) for item in items]
        sql_path = self._sql_path()

        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, live_rows),
        ) as post:
            result = translate.apply_translations_supabase(
                plan, _SB_URL, _SB_KEY, sql_output=sql_path
            )

        self.assertEqual(post.call_count, 1)
        post_args, post_kwargs = post.call_args
        self.assertEqual(
            post_args[0], f"{_SB_URL}/rest/v1/rpc/findings_translation_rows"
        )
        self.assertEqual(post_kwargs["json"], {"p_finding_ids": ["f-001", "f-002"]})
        self.assertEqual(post_kwargs["headers"]["apikey"], _SB_KEY)
        self.assertEqual(post_kwargs["headers"]["Authorization"], f"Bearer {_SB_KEY}")

        self.assertEqual(result["mode"], "sql_only")
        self.assertTrue(result["ready"])
        self.assertEqual(result["validated"], 2)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["sql_output_path"], sql_path)

        sql_text = open(sql_path, encoding="utf-8").read()
        self.assertIn("update public.findings set", sql_text)
        self.assertIn(fs.pg_quote_text("관찰사항 f-001 국문 번역."), sql_text)
        self.assertIn(
            f"and finding_text = {fs.pg_quote_text(items[0]['finding_text'])}", sql_text
        )

    def test_apply_finding_text_mismatch_blocks_and_writes_nothing(self) -> None:
        items = [_sb_item(1)]
        plan = _sb_filled_plan(items)
        live_rows = [
            {
                "finding_id": "f-001",
                "finding_text": "Live text differs from the plan.",
                "finding_text_ko": "",
            }
        ]
        sql_path = self._sql_path("blocked.sql")

        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, live_rows),
        ):
            result = translate.apply_translations_supabase(
                plan, _SB_URL, _SB_KEY, sql_output=sql_path
            )

        self.assertFalse(result["ready"])
        self.assertEqual(result["mode"], "sql_only")
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["sql_output_path"], "")
        self.assertTrue(any("does not byte-match" in e for e in result["errors"]))
        self.assertFalse(os.path.exists(sql_path))

    def test_apply_skips_already_translated_without_overwrite(self) -> None:
        items = [_sb_item(1), _sb_item(2)]
        plan = _sb_filled_plan(items)
        live_rows = [
            _sb_live_row(items[0], finding_text_ko="이미 번역됨."),
            _sb_live_row(items[1]),
        ]
        sql_path = self._sql_path("skip.sql")

        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, live_rows),
        ):
            result = translate.apply_translations_supabase(
                plan, _SB_URL, _SB_KEY, sql_output=sql_path
            )

        self.assertTrue(result["ready"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped_already_translated"], 1)
        sql_text = open(sql_path, encoding="utf-8").read()
        update_lines = [l for l in sql_text.splitlines() if l.startswith("update ")]
        self.assertEqual(len(update_lines), 1)
        self.assertIn("'f-002'", update_lines[0])

    def test_apply_batches_in_filter_at_20_ids(self) -> None:
        items = [_sb_item(i) for i in range(21)]  # f-000 .. f-020
        plan = _sb_filled_plan(items)
        first_batch_rows = [_sb_live_row(item) for item in items[:20]]
        second_batch_rows = [_sb_live_row(items[20])]
        sql_path = self._sql_path("batched.sql")

        with mock.patch(
            "findings_translate.requests.post",
            side_effect=[
                _FakeResponse(200, first_batch_rows),
                _FakeResponse(200, second_batch_rows),
            ],
        ) as post:
            result = translate.apply_translations_supabase(
                plan, _SB_URL, _SB_KEY, sql_output=sql_path
            )

        self.assertEqual(post.call_count, 2)
        first_ids = post.call_args_list[0][1]["json"]["p_finding_ids"]
        second_ids = post.call_args_list[1][1]["json"]["p_finding_ids"]
        self.assertEqual(len(first_ids), 20)
        self.assertEqual(first_ids[0], "f-000")
        self.assertEqual(second_ids, ["f-020"])

        self.assertTrue(result["ready"])
        self.assertEqual(result["updated"], 21)

    def test_apply_rows_falls_back_to_legacy_rest_when_rpc_missing(self) -> None:
        items = [_sb_item(1), _sb_item(2)]
        plan = _sb_filled_plan(items)
        live_rows = [_sb_live_row(item) for item in items]
        sql_path = self._sql_path("legacy-fallback.sql")

        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(404),
        ) as post:
            with mock.patch(
                "findings_translate.requests.get",
                return_value=_FakeResponse(200, live_rows),
            ) as get:
                result = translate.apply_translations_supabase(
                    plan, _SB_URL, _SB_KEY, sql_output=sql_path
                )

        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 1)
        get_args, get_kwargs = get.call_args
        self.assertEqual(get_args[0], f"{_SB_URL}/rest/v1/findings")
        self.assertEqual(
            get_kwargs["params"]["select"], "finding_id,finding_text,finding_text_ko"
        )
        self.assertEqual(get_kwargs["params"]["finding_id"], "in.(f-001,f-002)")

        self.assertTrue(result["ready"])
        self.assertEqual(result["updated"], 2)

    def test_apply_rows_rpc_invalid_shape_raises(self) -> None:
        plan = _sb_filled_plan([_sb_item(1)])
        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, {"not": "a list"}),
        ):
            with self.assertRaises(ValueError) as ctx:
                translate.apply_translations_supabase(
                    plan, _SB_URL, _SB_KEY, sql_output=self._sql_path("invalid.sql")
                )
        self.assertIn("invalid_response_shape", str(ctx.exception))

    def test_apply_5xx_exhausted_raises_http_summary(self) -> None:
        plan = _sb_filled_plan([_sb_item(1)])
        with mock.patch(
            "findings_translate.requests.post",
            side_effect=[_FakeResponse(503), _FakeResponse(503)],
        ):
            with self.assertRaises(ValueError) as ctx:
                translate.apply_translations_supabase(
                    plan, _SB_URL, _SB_KEY, sql_output=self._sql_path()
                )
        self.assertIn("http_503", str(ctx.exception))


class SupabaseCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write_plan(self, plan: dict, name: str = "plan.json") -> str:
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        return path

    def test_cli_write_file_rejected_for_supabase_source(self) -> None:
        plan_path = self._write_plan(_sb_filled_plan([_sb_item(1)]))
        with mock.patch("findings_translate.requests.get") as get:
            with mock.patch("findings_translate.requests.post") as post:
                rc = translate.main(
                    [
                        "--source", "supabase",
                        "--supabase-url", _SB_URL,
                        "--supabase-anon-key", _SB_KEY,
                        "--apply", plan_path,
                        "--write-file",
                        "--sql-output", os.path.join(self._tmp.name, "out.sql"),
                    ]
                )
        self.assertEqual(rc, 2)
        get.assert_not_called()
        post.assert_not_called()

    def test_cli_missing_url_and_key_exits_2(self) -> None:
        with mock.patch("findings_translate.requests.get") as get:
            with mock.patch("findings_translate.requests.post") as post:
                with mock.patch.dict(os.environ):
                    os.environ.pop("SUPABASE_URL", None)
                    os.environ.pop("SUPABASE_ANON_KEY", None)
                    rc = translate.main(["--source", "supabase", "--export"])
        self.assertEqual(rc, 2)
        get.assert_not_called()
        post.assert_not_called()

    def test_cli_apply_without_sql_output_exits_2(self) -> None:
        plan_path = self._write_plan(_sb_filled_plan([_sb_item(1)]))
        with mock.patch("findings_translate.requests.get") as get:
            with mock.patch("findings_translate.requests.post") as post:
                rc = translate.main(
                    [
                        "--source", "supabase",
                        "--supabase-url", _SB_URL,
                        "--supabase-anon-key", _SB_KEY,
                        "--apply", plan_path,
                    ]
                )
        self.assertEqual(rc, 2)
        get.assert_not_called()
        post.assert_not_called()

    def test_cli_export_uses_env_fallback_credentials(self) -> None:
        envelope = {"untranslated_total": 1, "items": [_sb_item(1)]}
        count_resp = _FakeResponse(200, [], headers={"Content-Range": "0-0/9"})
        out = os.path.join(self._tmp.name, "plan-out.json")
        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, envelope),
        ) as post:
            with mock.patch(
                "findings_translate.requests.get", return_value=count_resp
            ):
                with mock.patch.dict(
                    os.environ, {"SUPABASE_URL": _SB_URL, "SUPABASE_ANON_KEY": _SB_KEY}
                ):
                    rc = translate.main(
                        ["--source", "supabase", "--export", "--output", out]
                    )

        self.assertEqual(rc, 0)
        self.assertEqual(post.call_args_list[0][1]["headers"]["apikey"], _SB_KEY)
        with open(out, encoding="utf-8") as f:
            plan = json.load(f)
        self.assertEqual(plan["source_db"]["file_name"], "supabase:example.supabase.co")
        self.assertEqual(plan["source_db"]["findings_total"], 9)
        self.assertEqual(plan["queue_source"], "rpc")

    def test_cli_apply_validation_failure_exits_3(self) -> None:
        plan = _sb_filled_plan([_sb_item(1)])
        plan["items"][0]["translation_method"] = "auto"
        plan_path = self._write_plan(plan)
        live_rows = [_sb_live_row(_sb_item(1))]
        with mock.patch(
            "findings_translate.requests.post",
            return_value=_FakeResponse(200, live_rows),
        ):
            rc = translate.main(
                [
                    "--source", "supabase",
                    "--supabase-url", _SB_URL,
                    "--supabase-anon-key", _SB_KEY,
                    "--apply", plan_path,
                    "--sql-output", os.path.join(self._tmp.name, "out.sql"),
                ]
            )
        self.assertEqual(rc, 3)


class SqliteSourceRegressionTest(unittest.TestCase):
    """--source defaults to sqlite and the sqlite path never touches the network."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        _seed_db(
            self.db_path,
            [
                _pair(
                    document_id="fda-1",
                    firm="Acme Pharma",
                    finding_text="Batch record review was skipped.",
                    date="2026-07-05",
                ),
            ],
        )

    def test_default_source_matches_explicit_sqlite_and_never_uses_http(self) -> None:
        out_default = os.path.join(self._tmp.name, "plan-default.json")
        out_explicit = os.path.join(self._tmp.name, "plan-explicit.json")

        with mock.patch("findings_translate.requests.get") as get:
            with mock.patch("findings_translate.requests.post") as post:
                rc_default = translate.main(
                    ["--db-path", self.db_path, "--export", "--output", out_default]
                )
                rc_explicit = translate.main(
                    [
                        "--source", "sqlite",
                        "--db-path", self.db_path,
                        "--export",
                        "--output", out_explicit,
                    ]
                )

        self.assertEqual(rc_default, 0)
        self.assertEqual(rc_explicit, 0)
        get.assert_not_called()
        post.assert_not_called()
        with open(out_default, encoding="utf-8") as f:
            plan_default = json.load(f)
        with open(out_explicit, encoding="utf-8") as f:
            plan_explicit = json.load(f)
        self.assertEqual(plan_default, plan_explicit)
        self.assertEqual(plan_default["source_db"]["file_name"], "grm-findings.sqlite3")

    def test_sqlite_source_without_db_path_exits_2(self) -> None:
        rc = translate.main(["--export"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
