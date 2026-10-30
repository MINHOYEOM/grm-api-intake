#!/usr/bin/env python3
"""[WL 절단 복원 · 2026-10-31] 옛 480자 상한으로 잘린 WL findings 복원 경로 테스트.

세 층을 net-free 로 고정한다:
  1) backfill_wl_truncation_restore — 잘린 행 → 지금 추출기의 새 블록 대응(모호하면 건너뜀).
  2) findings_translate — restore 항목 검증(옛 md5 대조)·outbox 모양·SQL·--export-restore 선별.
  3) findings_translate_apply_service — restore PATCH 는 옛 md5 가 맞을 때만, 영문·국문 동시,
     국문이 비면 쓰지 않음, 기존 번역 항목의 요청 모양은 그대로.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import backfill_wl_truncation_restore as plan_mod
import findings_extractors as fe
import findings_translate as translate
import findings_translate_apply_service as svc

_URL = "https://example.supabase.co"
_KEY = "secret-key"

_OLD = "Your firm failed to establish adequate written procedures for production…"
_NEW = ("Your firm failed to establish adequate written procedures for production and process "
        "control. Your response is inadequate because it did not include a retrospective review.")
_OLD_MD5 = plan_mod.md5_text(_OLD)


class _Resp:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.headers: dict = {}

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# 1) 계획 생성
# ---------------------------------------------------------------------------


class PlanDocumentTest(unittest.TestCase):
    def _row(self, fid="finding-a", text=_OLD):
        return {"finding_id": fid, "raw_signal_id": "rs-1", "published_date": "2025-03-01",
                "finding_text": text}

    def test_unique_prefix_maps_to_new_block_keeping_finding_id(self):
        items, counts = plan_mod.plan_document(
            [self._row()], [{"finding_text": "Other block."}, {"finding_text": _NEW}])
        self.assertEqual(counts, {"unchanged": 0, "ambiguous": 0, "unmatched": 0})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["finding_id"], "finding-a")       # id 는 기존 그대로
        self.assertEqual(items[0]["finding_text"], _NEW)
        self.assertEqual(items[0]["finding_text_old_md5"], _OLD_MD5)

    def test_two_prefix_hits_is_ambiguous(self):
        items, counts = plan_mod.plan_document(
            [self._row()], [{"finding_text": _NEW}, {"finding_text": _NEW + " Again."}])
        self.assertEqual(items, [])
        self.assertEqual(counts["ambiguous"], 1)

    def test_two_rows_pointing_at_one_block_are_both_ambiguous(self):
        rows = [self._row("finding-a"), self._row("finding-b", _OLD[:-10] + "…")]
        items, counts = plan_mod.plan_document(rows, [{"finding_text": _NEW}])
        self.assertEqual(items, [])
        self.assertEqual(counts["ambiguous"], 2)

    def test_identical_block_is_unchanged_and_no_hit_is_unmatched(self):
        _items, counts = plan_mod.plan_document([self._row()], [{"finding_text": _OLD}])
        self.assertEqual(counts["unchanged"], 1)
        _items, counts = plan_mod.plan_document([self._row()], [{"finding_text": "Nope."}])
        self.assertEqual(counts["unmatched"], 1)

    def test_build_plan_filters_length_and_sorts_newest_first(self):
        rows = [
            {"finding_id": "f-old", "raw_signal_id": "rs-1", "published_date": "2022-01-01",
             "finding_text": _OLD},
            {"finding_id": "f-new", "raw_signal_id": "rs-2", "published_date": "2026-01-01",
             "finding_text": _OLD},
            {"finding_id": "f-long", "raw_signal_id": "rs-3", "published_date": "2026-09-01",
             "finding_text": "x" * 600 + "…"},     # 6000자 절단 등 — 범위 밖
        ]
        raw = {"rs-1": {"raw_signal_id": "rs-1"}, "rs-2": {"raw_signal_id": "rs-2"}}
        plan, report = plan_mod.build_plan(rows, raw, extract=lambda rs: [{"finding_text": _NEW}])
        self.assertEqual(report.candidates, 2)
        self.assertEqual([it["finding_id"] for it in plan["items"]], ["f-new", "f-old"])
        self.assertEqual(report.new_chars_total, 2 * len(_NEW))
        self.assertEqual(plan["schema_version"], translate.RESTORE_PLAN_SCHEMA_VERSION)

    def test_legacy_match_reproduces_old_480_cut(self):
        block = ("Your firm failed to " + "validate the process adequately " * 30).strip() + "."
        old = fe._legacy_cap_for_classify(block, True)
        self.assertTrue(old.endswith("…"))
        items, _ = plan_mod.plan_document([self._row(text=old)], [{"finding_text": block}])
        self.assertTrue(items[0]["legacy_match"])

    def test_md5_matches_postgres_utf8_md5(self):
        # Postgres md5(text) 는 UTF-8 바이트 기준이다 — 비 ASCII("…") 포함 문자열로 고정.
        import hashlib
        self.assertEqual(plan_mod.md5_text("é…"), hashlib.md5("é…".encode("utf-8")).hexdigest())


# ---------------------------------------------------------------------------
# 2) findings_translate — 검증·outbox·SQL·export-restore
# ---------------------------------------------------------------------------


def _restore_item(**over):
    item = {"kind": "restore", "finding_id": "f-001", "published_date": "2025-03-01",
            "finding_text_old_md5": _OLD_MD5, "finding_text": _NEW,
            "finding_text_ko": "귀사는 생산 및 공정 관리에 관한 적절한 서면 절차를 수립하지 않았다.",
            "translation_method": "llm_assisted"}
    item.update(over)
    return item


def _plan(items):
    return {"schema_version": translate.TRANSLATION_PLAN_SCHEMA_VERSION, "items": items}


class TranslateRestoreValidationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sql = os.path.join(self._tmp.name, "x.sql")
        self.outbox = os.path.join(self._tmp.name, "outbox.json")

    def _apply(self, items, live_rows):
        with mock.patch("findings_translate.requests.post", return_value=_Resp(200, live_rows)):
            return translate.apply_translations_supabase(
                _plan(items), _URL, _KEY, sql_output=self.sql, outbox_output=self.outbox)

    def test_restore_on_translated_row_is_not_skipped_and_outbox_carries_md5(self):
        live = [{"finding_id": "f-001", "finding_text": _OLD, "finding_text_ko": "옛 국문"}]
        result = self._apply([_restore_item()], live)
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(result["updated"], 1)
        with open(self.outbox, encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(out[0]["kind"], "restore")
        self.assertEqual(out[0]["finding_text_old_md5"], _OLD_MD5)
        self.assertEqual(out[0]["finding_text"], _NEW)
        sql = open(self.sql, encoding="utf-8").read()
        self.assertIn("set finding_text = ", sql)
        self.assertIn(f"md5(finding_text) = '{_OLD_MD5}'", sql)

    def test_restore_already_applied_or_drifted_blocks(self):
        live = [{"finding_id": "f-001", "finding_text": _NEW, "finding_text_ko": "국문"}]
        result = self._apply([_restore_item()], live)
        self.assertFalse(result["ready"])
        self.assertTrue(any("old md5" in e for e in result["errors"]))

    def test_restore_empty_ko_blocks(self):
        live = [{"finding_id": "f-001", "finding_text": _OLD, "finding_text_ko": "옛 국문"}]
        result = self._apply([_restore_item(finding_text_ko="")], live)
        self.assertFalse(result["ready"])

    def test_unknown_kind_blocks(self):
        live = [{"finding_id": "f-001", "finding_text": _OLD, "finding_text_ko": ""}]
        result = self._apply([_restore_item(kind="rewrite")], live)
        self.assertFalse(result["ready"])

    def test_plain_translate_item_outbox_shape_unchanged(self):
        plain = {"finding_id": "f-002", "finding_text": "Observation 2 was not documented.",
                 "finding_text_ko": "관찰 2 미기록.", "translation_method": "llm_assisted"}
        live = [{"finding_id": "f-002", "finding_text": plain["finding_text"], "finding_text_ko": ""}]
        result = self._apply([plain], live)
        self.assertTrue(result["ready"])
        with open(self.outbox, encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(sorted(out[0]), ["finding_id", "finding_text", "finding_text_ko",
                                          "translation_method"])

    def test_sqlite_mode_rejects_restore_items(self):
        db_path = os.path.join(self._tmp.name, "db.sqlite")
        open(db_path, "wb").close()
        with mock.patch("findings_translate.views.open_findings_db_readonly") as op:
            op.return_value.execute.return_value.fetchall.return_value = []
            result = translate.apply_translations(_plan([_restore_item()]), db_path)
            result_errors = result["errors"]
        self.assertFalse(result["ready"])
        self.assertTrue(any("--source supabase" in e for e in result_errors))


class ExportRestoreTest(unittest.TestCase):
    def test_keeps_only_rows_still_on_old_text(self):
        restore_plan = {
            "schema_version": translate.RESTORE_PLAN_SCHEMA_VERSION,
            "report": {"generated_at": "2026-10-31T00:00:00+00:00"},
            "items": [
                {"finding_id": "f-1", "published_date": "2024-01-01",
                 "finding_text_old_md5": _OLD_MD5, "finding_text": _NEW},
                {"finding_id": "f-2", "published_date": "2026-01-01",
                 "finding_text_old_md5": _OLD_MD5, "finding_text": _NEW},
                {"finding_id": "f-3", "published_date": "2026-02-01",
                 "finding_text_old_md5": _OLD_MD5, "finding_text": _NEW},
                {"finding_id": "f-4", "published_date": "2026-03-01",
                 "finding_text_old_md5": _OLD_MD5, "finding_text": _NEW},
            ],
        }
        live = [
            {"finding_id": "f-1", "finding_text": _OLD, "finding_text_ko": "a"},
            {"finding_id": "f-2", "finding_text": _OLD, "finding_text_ko": "b"},
            {"finding_id": "f-3", "finding_text": _NEW, "finding_text_ko": "c"},   # 이미 복원
            {"finding_id": "f-4", "finding_text": "changed", "finding_text_ko": "d"},
        ]
        with mock.patch("findings_translate.requests.post", return_value=_Resp(200, live)):
            out = translate.build_restore_queue_supabase(restore_plan, _URL, _KEY)
        self.assertEqual([it["finding_id"] for it in out["items"]], ["f-2", "f-1"])
        self.assertEqual(out["restore_already_applied"], 1)
        self.assertEqual(out["restore_drifted"], 1)
        self.assertEqual(out["items"][0]["kind"], "restore")
        self.assertEqual(out["items"][0]["finding_text_ko"], "")
        self.assertEqual(out["schema_version"], translate.TRANSLATION_PLAN_SCHEMA_VERSION)

    def test_wrong_plan_schema_raises(self):
        with self.assertRaises(ValueError):
            translate.build_restore_queue_supabase({"schema_version": "x", "items": []}, _URL, _KEY)


# ---------------------------------------------------------------------------
# 3) apply service
# ---------------------------------------------------------------------------


def _write(directory, items):
    with open(os.path.join(directory, "2026-10-31-batch.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)


class ApplyServiceRestoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _run(self, items, live_text):
        _write(self._tmp.name, items)
        with mock.patch("findings_translate_apply_service.requests.get",
                        return_value=_Resp(200, [{"finding_text": live_text}])) as get, \
             mock.patch("findings_translate_apply_service.requests.patch",
                        return_value=_Resp(200, [{"finding_id": "f-001"}])) as patch:
            report = svc.apply_outbox(self._tmp.name, _URL, _KEY)
        return report, get, patch

    def test_normal_restore_patches_english_and_korean_together(self):
        report, get, patch = self._run([_restore_item()], _OLD)
        self.assertEqual(report["items_succeeded"], 1)
        body = patch.call_args.kwargs["json"]
        self.assertEqual(body["finding_text"], _NEW)
        self.assertTrue(body["finding_text_ko"].strip())
        self.assertEqual(body["translation_method"], "llm_assisted")
        self.assertNotIn("category_code", body)                     # 분류 불변
        self.assertEqual(patch.call_args.kwargs["params"], {"finding_id": "eq.f-001"})
        self.assertNotIn("finding_text", get.call_args.kwargs["params"])

    def test_md5_mismatch_writes_nothing(self):
        report, _get, patch = self._run([_restore_item()], "some other text")
        patch.assert_not_called()
        self.assertEqual(report["items_matched_zero"], 1)
        self.assertEqual(report["errors"], [])

    def test_rerun_after_restore_is_matched_zero(self):
        report, _get, patch = self._run([_restore_item()], _NEW)
        patch.assert_not_called()
        self.assertEqual(report["items_matched_zero"], 1)

    def test_empty_korean_never_written(self):
        for ko in ("", "   "):
            report, get, patch = self._run([_restore_item(finding_text_ko=ko)], _OLD)
            get.assert_not_called()
            patch.assert_not_called()
            self.assertEqual(report["items_errored"], 1)

    def test_bad_md5_or_unknown_kind_writes_nothing(self):
        for item in (_restore_item(finding_text_old_md5="abc"), _restore_item(kind="rewrite")):
            report, get, patch = self._run([item], _OLD)
            get.assert_not_called()
            patch.assert_not_called()
            self.assertEqual(report["items_errored"], 1)

    def test_existing_translate_item_unchanged(self):
        plain = {"finding_id": "f-001", "finding_text": "Observation 1 was not documented.",
                 "finding_text_ko": "관찰 1 미기록.", "translation_method": "llm_assisted"}
        report, _get, patch = self._run([plain], plain["finding_text"])
        self.assertEqual(report["items_succeeded"], 1)
        self.assertEqual(patch.call_args.kwargs["json"],
                         {"finding_text_ko": "관찰 1 미기록.", "translation_method": "llm_assisted"})


if __name__ == "__main__":
    unittest.main()
