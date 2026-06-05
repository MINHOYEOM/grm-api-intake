"""K2-prep(raw fetch → rows 부착) 단위 테스트.

network 없이 collect_intake.notion_api_request 를 mock 해서
code block 재조립·페이지네이션·graceful degrade 를 검증한다.
"""
import json
import unittest
from unittest import mock

import collect_intake
from collect_intake import (
    attach_raw_to_rows, enrich_rows_with_raw, fetch_intake_raw_payload,
)


def _code_block(content: str, language: str = "json") -> dict:
    return {
        "type": "code",
        "code": {
            "language": language,
            "rich_text": [{"type": "text", "text": {"content": content},
                           "plain_text": content}],
        },
    }


def _heading_block(text: str) -> dict:
    return {
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": text},
                                     "plain_text": text}]},
    }


def _children_page(blocks: list[dict], next_cursor: str | None = None) -> dict:
    return {"results": blocks, "has_more": bool(next_cursor),
            "next_cursor": next_cursor}


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class FetchIntakeRawPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        # 실제 네트워크/슬립 차단
        patcher = mock.patch.object(collect_intake.time, "sleep", lambda *_: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_api(self, pages: list[dict]) -> mock.MagicMock:
        m = mock.MagicMock(side_effect=pages)
        p = mock.patch.object(collect_intake, "notion_api_request", m)
        p.start()
        self.addCleanup(p.stop)
        return m

    def test_single_code_block_roundtrip(self) -> None:
        raw = {"ENTRPS": "한국제약", "RTRVL_RESN": "함량부적합", "PRDUCT": "정제"}
        body = json.dumps(raw, ensure_ascii=False, indent=2)
        self._patch_api([_children_page(
            [_heading_block("Raw API payload"), _code_block(body)]
        )])
        got = fetch_intake_raw_payload("tok", "page-1")
        self.assertEqual(got, raw)

    def test_multi_chunk_reassembly(self) -> None:
        raw = {"issuing_office": "CDER", "subject": "x" * 5000, "letter_date": "2026-06-01"}
        body = json.dumps(raw, ensure_ascii=False, indent=2)
        # 1900자 청크로 쪼개 여러 code block 으로 저장된 상황 재현
        blocks = [_heading_block("Raw API payload")]
        blocks += [_code_block(c) for c in _chunks(body, 1900)]
        self.assertGreater(len(blocks), 2)  # 실제로 다중 청크인지 보장
        self._patch_api([_children_page(blocks)])
        got = fetch_intake_raw_payload("tok", "page-1")
        self.assertEqual(got, raw)

    def test_pagination_across_pages(self) -> None:
        raw = {"a": 1, "b": "긴내용" * 800}
        body = json.dumps(raw, ensure_ascii=False)
        c1, c2 = body[: len(body) // 2], body[len(body) // 2:]
        pages = [
            _children_page([_heading_block("Raw API payload"), _code_block(c1)],
                           next_cursor="cur-2"),
            _children_page([_code_block(c2)]),
        ]
        m = self._patch_api(pages)
        got = fetch_intake_raw_payload("tok", "page-1")
        self.assertEqual(got, raw)
        self.assertEqual(m.call_count, 2)

    def test_no_code_blocks_returns_none(self) -> None:
        self._patch_api([_children_page([_heading_block("Raw API payload")])])
        self.assertIsNone(fetch_intake_raw_payload("tok", "page-1"))

    def test_invalid_json_returns_none(self) -> None:
        self._patch_api([_children_page(
            [_code_block('{"broken": ')]
        )])
        self.assertIsNone(fetch_intake_raw_payload("tok", "page-1"))

    def test_non_dict_json_returns_none(self) -> None:
        self._patch_api([_children_page([_code_block("[1, 2, 3]")])])
        self.assertIsNone(fetch_intake_raw_payload("tok", "page-1"))

    def test_empty_page_id_returns_none_without_call(self) -> None:
        m = mock.MagicMock()
        with mock.patch.object(collect_intake, "notion_api_request", m):
            self.assertIsNone(fetch_intake_raw_payload("tok", ""))
        m.assert_not_called()

    def test_api_error_is_swallowed(self) -> None:
        m = mock.MagicMock(side_effect=collect_intake.NotionHandoffError("boom"))
        with mock.patch.object(collect_intake, "notion_api_request", m):
            self.assertIsNone(fetch_intake_raw_payload("tok", "page-1"))


class AttachRawToRowsTest(unittest.TestCase):
    def test_success_and_degrade_paths(self) -> None:
        rows = [
            {"source": "MFDS", "document_id": "ok-1", "page_id": "p-ok"},
            {"source": "FDA Warning Letter", "document_id": "fail-1", "page_id": "p-fail"},
        ]

        def fake_fetch(token: str, page_id: str):
            return {"ENTRPS": "회사", "RTRVL_RESN": "사유"} if page_id == "p-ok" else None

        with mock.patch.object(collect_intake, "fetch_intake_raw_payload",
                               side_effect=fake_fetch):
            stats = attach_raw_to_rows("tok", rows, sleep_s=0)

        self.assertEqual(stats, {"ok": 1, "failed": 1, "from_memory": 0, "total": 2})
        ok_row, fail_row = rows
        self.assertTrue(ok_row["raw_fetch_ok"])
        self.assertEqual(ok_row["raw"]["ENTRPS"], "회사")
        self.assertEqual(ok_row["raw_source"], "fetch")
        self.assertNotIn("status_hint", ok_row)

        self.assertFalse(fail_row["raw_fetch_ok"])
        self.assertIsNone(fail_row["raw"])
        self.assertEqual(fail_row["evidence_hint"], "B")
        self.assertEqual(fail_row["status_hint"], "Error")

    def test_inmemory_raw_skips_fetch(self) -> None:
        # 당일 수집분: inmemory_raw 에 있으면 page children fetch 를 호출하지 않는다.
        rows = [{"source": "MFDS", "document_id": "today-1", "page_id": "p-x"}]
        cache = {"MFDS::today-1": {"RTRVL_RESN": "메모리 raw"}}
        with mock.patch.object(collect_intake, "fetch_intake_raw_payload") as fetch:
            stats = attach_raw_to_rows("tok", rows, inmemory_raw=cache, sleep_s=0)
        fetch.assert_not_called()
        self.assertEqual(stats, {"ok": 1, "failed": 0, "from_memory": 1, "total": 1})
        self.assertEqual(rows[0]["raw_source"], "memory")
        self.assertEqual(rows[0]["raw"]["RTRVL_RESN"], "메모리 raw")

    def test_empty_rows(self) -> None:
        self.assertEqual(attach_raw_to_rows("tok", [], sleep_s=0),
                         {"ok": 0, "failed": 0, "from_memory": 0, "total": 0})


class EnrichRowsWithRawTest(unittest.TestCase):
    def test_dedupe_before_attach(self) -> None:
        # 같은 source::document_id 중복 2건 → dedupe 로 1건만 fetch 대상.
        rows = [
            {"source": "MFDS", "document_id": "dup", "page_id": "p1",
             "run_date": "2026-06-01", "signal_tier": "Tier 2"},
            {"source": "MFDS", "document_id": "dup", "page_id": "p2",
             "run_date": "2026-06-04", "signal_tier": "Tier 2"},
        ]
        with mock.patch.object(collect_intake, "fetch_intake_raw_payload",
                               return_value={"k": "v"}) as fetch:
            deduped, stats = enrich_rows_with_raw("tok", rows, sleep_s=0)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(fetch.call_count, 1)  # 중복 fetch 제거됨


if __name__ == "__main__":
    unittest.main()
