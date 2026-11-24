"""K2-prep(raw fetch → rows 부착) 단위 테스트.

network 없이 collect_intake.notion_api_request 를 mock 해서
code block 재조립·페이지네이션·graceful degrade 를 검증한다.
"""
import json
import unittest
from unittest import mock

import collect_intake
import grm_handoff  # 배치5 Phase2: handoff/emit 정의 모듈(patch 대상)
from collect_intake import IntakeItem, build_inmemory_raw


def _item(source: str, document_id: str, raw: dict | None) -> IntakeItem:
    return IntakeItem(source=source, document_id=document_id, date_iso="2026-06-05",
                      headline="h", official_url="", raw_payload=raw or {})


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


class BuildInmemoryRawTest(unittest.TestCase):
    """G2 와이어링: 당일 수집 IntakeItem → {card_id: raw_payload} 집계."""

    def test_maps_card_id_to_raw_across_lists(self) -> None:
        recall = [_item("MFDS", "recall-1", {"RTRVL_RESN": "사유"})]
        fr = [_item("Federal Register", "FR-1", {"abstract": "x"})]
        cache = build_inmemory_raw(recall, fr)
        self.assertEqual(cache, {
            "MFDS::recall-1": {"RTRVL_RESN": "사유"},
            "Federal Register::FR-1": {"abstract": "x"},
        })

    def test_empty_raw_payload_excluded(self) -> None:
        # 빈 raw_payload 는 제외 — attach 의 fetch 폴백/graceful degrade 를 가로채지 않게.
        cache = build_inmemory_raw([_item("WHO", "who-1", None),
                                    _item("MFDS", "admin-1", {"EXPOSE_CONT": "내용"})])
        self.assertEqual(cache, {"MFDS::admin-1": {"EXPOSE_CONT": "내용"}})

    def test_duplicate_card_id_first_wins(self) -> None:
        cache = build_inmemory_raw([_item("MFDS", "d", {"v": 1})],
                                   [_item("MFDS", "d", {"v": 2})])
        self.assertEqual(cache["MFDS::d"], {"v": 1})

    def test_no_items(self) -> None:
        self.assertEqual(build_inmemory_raw([], []), {})


if __name__ == "__main__":
    unittest.main()
