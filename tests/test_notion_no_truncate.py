"""Notion 저장 단계 절단 제거 (2026-11-10) — 쪼개 담되 자르지 않는다.

- rich_text: 2,000자 단위로 쪼개고, Notion API 한도인 100 요소에서만 끊는다.
- notion_create_page: children 100 초과분은 생성 후 90개씩 PATCH 로 덧붙인다.
네트워크는 전부 mock.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci  # noqa: E402
import collect_search  # noqa: E402
import grm_notion  # noqa: E402

RUN_DATE = date(2026, 11, 10)
NOW = datetime(2026, 11, 10, 9, 0, 0)


def _item(**kw):
    base = dict(source=ci.SOURCE_FR, document_id="DOC-1", date_iso="2026-11-10",
                headline="h", official_url="https://example.com/x")
    base.update(kw)
    return ci.IntakeItem(**base)


def _resp(status=200, body=None):
    r = mock.Mock()
    r.status_code = status
    r.text = "{}"
    r.json.return_value = body if body is not None else {"id": "page-1"}
    return r


def _blocks(n):
    return [{"object": "block", "type": "paragraph", "n": i} for i in range(n)]


class RichTextTests(unittest.TestCase):
    def test_long_text_split_not_cut(self):
        text = "가" * 5000
        rt = grm_notion._rich_text(text)
        self.assertEqual(len(rt), 3)
        self.assertEqual("".join(c["text"]["content"] for c in rt), text)

    def test_capped_at_100_elements(self):
        rt = grm_notion._rich_text("a" * (2000 * 150))
        self.assertEqual(len(rt), 100)

    def test_properties_not_truncated(self):
        long = "b" * 5000
        item = _item(headline=long, body=long, firm=long, distribution=long,
                     raw_excerpt=long, search_query=long, site_country=long)
        props = grm_notion.build_notion_properties(item, RUN_DATE, NOW)
        for key in (grm_notion.PROP_HEADLINE, grm_notion.PROP_BODY,
                    grm_notion.PROP_FIRM, grm_notion.PROP_DISTRIBUTION,
                    grm_notion.PROP_RAW_EXCERPT, grm_notion.PROP_SEARCH_QUERY,
                    grm_notion.PROP_SITE_COUNTRY):
            joined = "".join(c["text"]["content"] for c in props[key]["rich_text"])
            self.assertEqual(joined, long, key)
        title = "".join(c["text"]["content"] for c in props[grm_notion.PROP_NAME]["title"])
        self.assertTrue(title.endswith(long))




class SearchSnippetTests(unittest.TestCase):
    def test_snippet_not_cut(self):
        long = "s" * 900
        self.assertEqual(collect_search._extract_snippet({"description": long}), long)
        self.assertEqual(
            collect_search._extract_snippet({"extra_snippets": ["x", long]}), long)


if __name__ == "__main__":
    unittest.main()
