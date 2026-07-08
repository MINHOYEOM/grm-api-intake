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


if __name__ == "__main__":
    unittest.main()
