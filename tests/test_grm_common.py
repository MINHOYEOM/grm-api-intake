#!/usr/bin/env python3
"""Unit tests for grm_common shared utilities (data.go.kr helpers + HTTP wrappers)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from grm_common import (
    DatagoPageError,
    datago_paginate,
    env_flag,
    parse_int_safe,
    text_field,
    parse_datago_date,
    datago_normalize_items,
    datago_extract_items,
)


class TestParseIntSafe(unittest.TestCase):
    def test_valid_int(self):
        self.assertEqual(parse_int_safe(42), 42)

    def test_string_int(self):
        self.assertEqual(parse_int_safe("7"), 7)

    def test_none_returns_default(self):
        self.assertEqual(parse_int_safe(None, 99), 99)

    def test_garbage_returns_default(self):
        self.assertEqual(parse_int_safe("abc", 5), 5)

    def test_empty_string_returns_default(self):
        self.assertEqual(parse_int_safe("", 0), 0)


class TestTextField(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(text_field({"a": "hello "}, "a"), "hello")

    def test_missing_key(self):
        self.assertEqual(text_field({}, "a"), "")

    def test_none_value(self):
        self.assertEqual(text_field({"a": None}, "a"), "")

    def test_numeric_value(self):
        self.assertEqual(text_field({"a": 123}, "a"), "123")


class TestParseDategoDate(unittest.TestCase):
    def test_yyyymmdd(self):
        self.assertEqual(parse_datago_date("20260601"), "2026-06-01")

    def test_yyyymmdd_with_trailing(self):
        self.assertEqual(parse_datago_date("20260315extra"), "2026-03-15")

    def test_empty(self):
        self.assertEqual(parse_datago_date(""), "")

    def test_none(self):
        self.assertEqual(parse_datago_date(None), "")

    def test_short(self):
        self.assertEqual(parse_datago_date("2026"), "")

    def test_invalid_date(self):
        self.assertEqual(parse_datago_date("20261301"), "")

    def test_whitespace_stripped(self):
        self.assertEqual(parse_datago_date("  20260101  "), "2026-01-01")


class TestDatagoNormalizeItems(unittest.TestCase):
    def test_none(self):
        self.assertEqual(datago_normalize_items(None), [])

    def test_flat_list(self):
        items = [{"a": 1}, {"b": 2}]
        self.assertEqual(datago_normalize_items(items), [{"a": 1}, {"b": 2}])

    def test_wrapped_single_dict(self):
        self.assertEqual(
            datago_normalize_items({"item": {"x": 1}}),
            [{"x": 1}],
        )

    def test_wrapped_list(self):
        result = datago_normalize_items({"item": [{"x": 1}, {"y": 2}]})
        self.assertEqual(result, [{"x": 1}, {"y": 2}])

    def test_list_of_wrappers(self):
        result = datago_normalize_items([{"item": {"a": 1}}, {"item": {"b": 2}}])
        self.assertEqual(result, [{"a": 1}, {"b": 2}])

    def test_non_dict_non_list(self):
        self.assertEqual(datago_normalize_items("string"), [])


class TestDatagoExtractItems(unittest.TestCase):
    def test_full_response(self):
        data = {
            "header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {
                "pageNo": 1,
                "numOfRows": 100,
                "totalCount": 3,
                "items": [{"item": {"a": 1}}, {"item": {"b": 2}}, {"item": {"c": 3}}],
            },
        }
        items, page_no, num_rows, total_count, status = datago_extract_items(data)
        self.assertEqual(len(items), 3)
        self.assertEqual(page_no, 1)
        self.assertEqual(num_rows, 100)
        self.assertEqual(total_count, 3)
        self.assertEqual(status, "00:OK")

    def test_missing_header(self):
        data = {"body": {"items": [{"a": 1}], "totalCount": 1, "pageNo": 1, "numOfRows": 50}}
        items, _, _, _, status = datago_extract_items(data)
        self.assertEqual(len(items), 1)
        self.assertEqual(status, ":")

    def test_empty_body(self):
        data = {"header": {"resultCode": "99", "resultMsg": "ERR"}, "body": {}}
        items, page_no, num_rows, total_count, status = datago_extract_items(data, default_page_size=25)
        self.assertEqual(items, [])
        self.assertEqual(page_no, 1)
        self.assertEqual(num_rows, 25)
        self.assertEqual(total_count, 0)
        self.assertEqual(status, "99:ERR")

    def test_default_page_size(self):
        data = {"body": {"items": []}}
        _, _, num_rows, _, _ = datago_extract_items(data, default_page_size=50)
        self.assertEqual(num_rows, 50)


class TestEnvFlag(unittest.TestCase):
    """ENABLE_* 단일 파서 — truthy = {"1","true","yes","on"} (case/공백 무시)."""

    VAR = "GRM_TEST_ENV_FLAG"

    def _set(self, value: str):
        return patch.dict(os.environ, {self.VAR: value})

    def test_truthy_values(self):
        for v in ("1", "true", "TRUE", "yes", "YES ", "on", " On "):
            with self._set(v):
                self.assertTrue(env_flag(self.VAR), f"expected truthy: {v!r}")

    def test_falsy_values(self):
        for v in ("0", "false", "FALSE", "no", "off", "banana"):
            with self._set(v):
                self.assertFalse(env_flag(self.VAR), f"expected falsy: {v!r}")

    def test_empty_returns_default(self):
        with self._set(""):
            self.assertFalse(env_flag(self.VAR))
            self.assertTrue(env_flag(self.VAR, default=True))

    def test_unset_returns_default(self):
        env = {k: v for k, v in os.environ.items() if k != self.VAR}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(env_flag(self.VAR))
            self.assertTrue(env_flag(self.VAR, default=True))
            self.assertFalse(env_flag("GRM_DEFINITELY_MISSING_VAR_XYZ"))


class TestDatagoPaginate(unittest.TestCase):
    """datago_paginate 골격 — 정상 종료(빈 페이지/totalCount)·page cap(truncated)·페이지 실패."""

    def _extract(self, pages, total, *, status="00:OK", rows=100):
        def extract(data):
            page = data["_p"]
            return pages.get(page, []), page, rows, total, status
        return extract

    def _http(self, calls):
        def http_get(endpoint, params=None, timeout=None, retries=None):
            calls.append(params["pageNo"])
            return {"_p": params["pageNo"]}
        return http_get

    def test_terminates_at_total_count(self):
        calls = []
        pages = {1: [{"a": 1}], 2: [{"b": 2}]}
        pg = datago_paginate("http://x", service_key="k", page_size=1, max_pages=10,
                             extract=self._extract(pages, total=2, rows=1),
                             http_get=self._http(calls))
        got = [items for items, _url in pg]
        self.assertEqual(calls, [1, 2])            # totalCount=2, rows=1 → 2 페이지 후 종료
        self.assertEqual(got, [[{"a": 1}], [{"b": 2}]])
        self.assertFalse(pg.truncated)
        self.assertEqual(pg.total_count, 2)

    def test_stops_on_empty_page(self):
        calls = []
        pages = {1: [{"a": 1}]}                     # page 2 = 빈 페이지
        pg = datago_paginate("http://x", service_key="k", max_pages=10,
                             extract=self._extract(pages, total=999),
                             http_get=self._http(calls))
        got = [items for items, _url in pg]
        self.assertEqual(got, [[{"a": 1}]])
        self.assertEqual(calls, [1, 2])
        self.assertFalse(pg.truncated)

    def test_page_cap_sets_truncated(self):
        calls = []
        pages = {i: [{"i": i}] for i in range(1, 20)}   # 종료 안 되게 매 페이지 비-빈
        pg = datago_paginate("http://x", service_key="k", max_pages=3,
                             extract=self._extract(pages, total=10_000),
                             http_get=self._http(calls))
        got = [items for items, _url in pg]
        self.assertEqual(calls, [1, 2, 3])
        self.assertTrue(pg.truncated)
        self.assertEqual(len(got), 3)

    def test_masked_url_redacts_service_key(self):
        pages = {1: [{"a": 1}]}
        pg = datago_paginate("http://api", service_key="SECRET", max_pages=5,
                             extract=self._extract(pages, total=1),
                             http_get=self._http([]))
        _items, url = next(iter(pg))
        self.assertIn("serviceKey=***REDACTED***", url)
        self.assertNotIn("SECRET", url)

    def test_bad_status_raises_page_error(self):
        pages = {1: [{"a": 1}]}
        pg = datago_paginate("http://x", service_key="k", max_pages=5,
                             extract=self._extract(pages, total=1, status="99:ERR"),
                             http_get=self._http([]))
        with self.assertRaises(DatagoPageError) as ctx:
            list(pg)
        self.assertEqual(ctx.exception.page_no, 1)

    def test_http_error_raises_after_partial_yield(self):
        def http_get(endpoint, params=None, timeout=None, retries=None):
            if params["pageNo"] == 2:
                raise RuntimeError("boom")
            return {"_p": params["pageNo"]}
        pages = {1: [{"a": 1}], 2: [{"b": 2}]}
        pg = datago_paginate("http://x", service_key="k", max_pages=5,
                             extract=self._extract(pages, total=999), http_get=http_get)
        collected = []
        with self.assertRaises(DatagoPageError) as ctx:
            for items, _url in pg:
                collected.append(items)
        self.assertEqual(collected, [[{"a": 1}]])   # page1 은 실패 전에 yield 됨
        self.assertEqual(ctx.exception.page_no, 2)
        self.assertIsInstance(ctx.exception.cause, RuntimeError)


if __name__ == "__main__":
    unittest.main()
