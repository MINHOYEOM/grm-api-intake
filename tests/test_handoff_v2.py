"""handoff v2(단계 D) 단위 테스트 — additive·플래그·raw 미포함·children 분할·v1 보존."""
import json
import os
import unittest
from datetime import date, datetime
from unittest import mock

import collect_intake as ci


RUN_DATE = date(2026, 6, 5)
GEN_AT = datetime(2026, 6, 5, 3, 17)


def _enriched_rows() -> list[dict]:
    return [
        {  # MFDS recall (recall_group_key 대상)
            "source": "MFDS", "document_id": "recall-2026003474", "date": "2026-06-02",
            "type_or_class": "recall-quality", "firm": "한국제약", "headline": "정제 회수",
            "page_id": "page-aaa", "signal_tier": "Tier 2", "modality": "Chemical",
            "language": "KO", "raw_fetch_ok": True, "raw_source": "memory",
            "raw": {"ENTRPS": "한국제약(주)", "PRDUCT": "아세트아미노펜정",
                    "RTRVL_RESN": "함량부적합 자진 회수"},
        },
        {  # FR guidance
            "source": "Federal Register", "document_id": "FR-2026-04578", "date": "2026-05-22",
            "type_or_class": "guidance-industry", "firm": "", "headline": "Guidance X",
            "page_id": "page-bbb", "signal_tier": "Tier 2", "modality": "",
            "language": "", "raw_fetch_ok": True, "raw_source": "fetch",
            "raw": {"title": "Guidance X", "abstract": "This draft guidance describes ..."},
        },
    ]


class FlagTest(unittest.TestCase):
    def test_flag_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ci._enable_handoff_v2())

    def test_flag_on(self) -> None:
        with mock.patch.dict(os.environ, {"ENABLE_HANDOFF_V2": "true"}):
            self.assertTrue(ci._enable_handoff_v2())
        with mock.patch.dict(os.environ, {"ENABLE_HANDOFF_V2": "false"}):
            self.assertFalse(ci._enable_handoff_v2())


class BuildV2PayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = ci.build_routine_handoff_payload_v2(
            _enriched_rows(), RUN_DATE, 7, GEN_AT)

    def test_schema_version_v2(self) -> None:
        self.assertEqual(self.payload["schema_version"], "grm-routine-handoff/v2")
        self.assertEqual(self.payload["row_count"], 2)

    def test_rows_have_additive_v2_fields(self) -> None:
        for r in self.payload["rows"]:
            self.assertIn("card_id", r)
            self.assertIn("section", r)
            self.assertIn("card_scaffold", r)
            self.assertIn("prose_input", r)
            self.assertIn("needs_llm_slots", r)
            # v1 필드 보존(additive·하위호환)
            self.assertIn("source", r)
            self.assertIn("document_id", r)
            self.assertIn("page_id", r)

    def test_raw_full_payload_excluded(self) -> None:
        # 단계 D: raw 전체 JSON 은 절대 미포함(크기 폭증 방지)
        for r in self.payload["rows"]:
            self.assertNotIn("raw", r)
        # 직렬화 전체에도 raw 원본 키가 새어나오지 않음
        blob = json.dumps(self.payload, ensure_ascii=False)
        self.assertNotIn("RTRVL_RESN", blob)
        self.assertNotIn("\"abstract\"", blob)

    def test_recall_group_key_present_only_for_recall(self) -> None:
        recall = next(r for r in self.payload["rows"] if r["card_id"].startswith("MFDS::recall"))
        guidance = next(r for r in self.payload["rows"] if r["source"] == "Federal Register")
        self.assertTrue(recall["recall_group_key"])
        self.assertNotIn("recall_group_key", guidance)
        # card_id 는 source::document_id 유지(§12E)
        self.assertEqual(recall["card_id"], "MFDS::recall-2026003474")

    def test_card_scaffold_is_markdown_with_slots(self) -> None:
        r = self.payload["rows"][0]
        self.assertIn("{{W1}}", r["card_scaffold"])
        self.assertIn("<callout", r["card_scaffold"])

    def test_deterministic_compact_serialization(self) -> None:
        a = ci.build_routine_handoff_payload_v2(_enriched_rows(), RUN_DATE, 7, GEN_AT)
        b = ci.build_routine_handoff_payload_v2(_enriched_rows(), RUN_DATE, 7, GEN_AT)
        sa = json.dumps(a, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sb = json.dumps(b, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(sa, sb)


class EmitBranchTest(unittest.TestCase):
    def _run_emit(self, flag: str | None):
        captured = {}

        def fake_upsert(token, db_id, payload, generated_at, compact=False):
            captured["payload"] = payload
            captured["compact"] = compact
            return "pid", "url"

        env = {} if flag is None else {"ENABLE_HANDOFF_V2": flag}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ci, "notion_query_new_intake_rows", return_value=_enriched_rows()), \
             mock.patch.object(ci, "enrich_rows_with_raw",
                               side_effect=lambda t, rows, inmemory_raw=None: (rows, {})), \
             mock.patch.object(ci, "notion_upsert_routine_handoff", side_effect=fake_upsert):
            ci.emit_routine_handoff("tok", "db", RUN_DATE, 7, GEN_AT)
        return captured

    def test_flag_off_uses_v1(self) -> None:
        cap = self._run_emit(None)
        self.assertEqual(cap["payload"]["schema_version"], "grm-routine-handoff/v1")
        self.assertFalse(cap["compact"])
        self.assertNotIn("card_scaffold", cap["payload"]["rows"][0])

    def test_flag_on_uses_v2(self) -> None:
        cap = self._run_emit("true")
        self.assertEqual(cap["payload"]["schema_version"], "grm-routine-handoff/v2")
        self.assertTrue(cap["compact"])
        self.assertIn("card_scaffold", cap["payload"]["rows"][0])


class ChildrenChunkTest(unittest.TestCase):
    def test_create_splits_blocks_over_limit(self) -> None:
        fake_blocks = [{"object": "block", "n": i} for i in range(200)]
        create_children = {}
        append_calls = {}

        def fake_api(method, url, token, body=None, **kw):
            if method == "POST":
                create_children["count"] = len(body.get("children", []))
                return {"id": "newpage", "url": "u"}
            return {}

        def fake_append(token, page_id, blocks):
            append_calls["count"] = len(blocks)

        with mock.patch.object(ci, "_handoff_blocks", return_value=fake_blocks), \
             mock.patch.object(ci, "_handoff_page_properties", return_value={}), \
             mock.patch.object(ci, "notion_find_handoff_page", return_value=None), \
             mock.patch.object(ci, "notion_api_request", side_effect=fake_api), \
             mock.patch.object(ci, "notion_append_page_children", side_effect=fake_append):
            ci.notion_upsert_routine_handoff("tok", "db",
                                             {"handoff_id": "h"}, GEN_AT, compact=True)
        self.assertEqual(create_children["count"], 90)   # 생성은 ≤90
        self.assertEqual(append_calls["count"], 110)      # 나머지 append

    def test_create_no_split_when_small(self) -> None:
        fake_blocks = [{"object": "block", "n": i} for i in range(10)]
        create_children = {}

        def fake_api(method, url, token, body=None, **kw):
            if method == "POST":
                create_children["count"] = len(body.get("children", []))
                return {"id": "p", "url": "u"}
            return {}

        with mock.patch.object(ci, "_handoff_blocks", return_value=fake_blocks), \
             mock.patch.object(ci, "_handoff_page_properties", return_value={}), \
             mock.patch.object(ci, "notion_find_handoff_page", return_value=None), \
             mock.patch.object(ci, "notion_api_request", side_effect=fake_api), \
             mock.patch.object(ci, "notion_append_page_children") as append:
            ci.notion_upsert_routine_handoff("tok", "db", {"handoff_id": "h"}, GEN_AT)
        self.assertEqual(create_children["count"], 10)
        append.assert_not_called()  # ≤90 이면 분할 없음(v1 기존 동작 유지)


if __name__ == "__main__":
    unittest.main()
