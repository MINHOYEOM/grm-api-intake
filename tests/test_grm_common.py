#!/usr/bin/env python3
"""Unit tests for grm_common shared utilities (data.go.kr helpers + HTTP wrappers)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from grm_common import (
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


if __name__ == "__main__":
    unittest.main()
