#!/usr/bin/env python3
"""Unit tests for grm_common shared utilities (data.go.kr helpers + HTTP wrappers)."""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest.mock import MagicMock, patch

import requests

from grm_common import (
    DatagoPageError,
    datago_paginate,
    env_flag,
    parse_int_safe,
    text_field,
    parse_datago_date,
    datago_normalize_items,
    datago_extract_items,
    http_get_bytes,
    http_get_json,
    http_get_xml,
    mask_service_key,
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


class HttpGetXmlDeclarationTest(unittest.TestCase):
    """[2026-07-27] `http_get_xml` 잡음 제거가 XML 선언을 삼키지 않아야 한다.

    실장애: ECA 피드(`encoding="windows-1252"`)가 그 주 0건 수집됐다. 원인은 피드가 아니라
    우리 잡음 제거 로직이었다 — 마커를 순서대로 훑으며 `idx > 0` 인 첫 마커에서 잘랐는데,
    정상 문서는 `<?xml` 이 0번 위치라 통과해 버리고 다음 마커 `<rss`(항상 >0)에서 잘라
    **선언을 통째로 버렸다.** 선언이 없으면 ElementTree 가 UTF-8 을 가정하므로 비-ASCII
    바이트(`0x96` en-dash)에서 죽는다. 나머지 피드가 UTF-8 이라 우연히 안 걸렸을 뿐이다.
    """

    @staticmethod
    def _resp(content: bytes):
        r = MagicMock()
        r.status_code = 200
        r.content = content
        r.raise_for_status = MagicMock()
        return r

    def _fetch(self, content: bytes):
        with patch("grm_common.requests.get", return_value=self._resp(content)):
            return http_get_xml("https://example.org/feed.xml")

    # 회귀 본체 — 이 케이스가 수리 전 구현에서 실패한다.
    def test_non_utf8_declaration_survives_and_parses(self):
        body = (
            b'<?xml version="1.0" encoding="windows-1252" ?>\r\n'
            b"<rss version=\"2.0\"><channel><item>"
            b"<title>Non-Sterile Medicinal Products \x96 EMA Q&amp;A</title>"
            b"</item></channel></rss>"
        )
        root = self._fetch(body)
        title = root.find("./channel/item/title")
        self.assertIsNotNone(title, "windows-1252 선언 피드의 item 을 파싱하지 못했다")
        # 0x96(windows-1252 en-dash)이 U+2013 으로 정확히 디코딩돼야 한다.
        self.assertIn("–", title.text)

    def test_leading_noise_before_declaration_is_stripped(self):
        """원래 의도(WHO Drupal theme debug 주석/BOM 제거)는 그대로 살아 있어야 한다."""
        body = (b"\xef\xbb\xbf<!-- theme debug -->\n"
                b'<?xml version="1.0" encoding="utf-8" ?><rss version="2.0">'
                b"<channel><item><title>ok</title></item></channel></rss>")
        root = self._fetch(body)
        self.assertEqual(root.find("./channel/item/title").text, "ok")

    def test_noise_before_rss_without_declaration_is_stripped(self):
        """선언이 아예 없고 잡음만 있는 피드도 종전처럼 복구된다."""
        body = (b"garbage\n<rss version=\"2.0\">"
                b"<channel><item><title>ok</title></item></channel></rss>")
        root = self._fetch(body)
        self.assertEqual(root.find("./channel/item/title").text, "ok")

    def test_clean_utf8_feed_unchanged(self):
        body = (b'<?xml version="1.0" encoding="utf-8" ?><rss version="2.0">'
                b"<channel><item><title>\xed\x95\x9c\xea\xb8\x80</title></item></channel></rss>")
        root = self._fetch(body)
        self.assertEqual(root.find("./channel/item/title").text, "한글")


class HttpHelperServiceKeyExceptionMaskingTest(unittest.TestCase):
    """[2026-09-10 보안] data.go.kr serviceKey 가 예외 로그로 새는 경로를 막는다.

    ``mask_service_key`` 는 provenance(item.api_query)용으로만 쓰이고 있었다 — 정작
    ``requests``/``urllib3`` 가 연결 실패 시 만드는 ``MaxRetryError`` 문구는 **요청
    URL 전체(쿼리스트링 포함)** 를 담는데, ``http_get_json``/``http_get_xml`` 등의
    WARN 로그(``err={e}``)와 최종 ``RuntimeError`` 는 그 문구를 그대로 이어붙였다.
    이 로그는 매일 도는 공개 GitHub Actions 로그(collect_mfds_recall 등)에 찍힌다.
    """

    # urllib3.exceptions.MaxRetryError 문구를 흉내: 요청 URL 전체(서비스키 포함)를 담는다.
    LEAK_URL = ("https://apis.data.go.kr/1471000/MdcinPrdlstInfoService"
                "?serviceKey=SECRET123&pageNo=1&numOfRows=10&type=json")

    @staticmethod
    def _conn_error() -> requests.exceptions.ConnectionError:
        msg = (
            "HTTPSConnectionPool(host='apis.data.go.kr', port=443): "
            "Max retries exceeded with url: /1471000/MdcinPrdlstInfoService"
            "?serviceKey=SECRET123&pageNo=1&numOfRows=10&type=json "
            "(Caused by NewConnectionError('<urllib3.connection.HTTPSConnection "
            "object>: Failed to establish a new connection'))"
        )
        return requests.exceptions.ConnectionError(msg)

    def _run_capture(self, fn, *args, **kwargs):
        """fn(*args, **kwargs) 호출 — stdout(log 출력)과 최종 예외 메시지를 함께 반환."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(RuntimeError) as ctx:
                fn(*args, **kwargs)
        return buf.getvalue(), str(ctx.exception)

    def test_http_get_json_masks_service_key(self):
        with patch("grm_common.requests.get", side_effect=self._conn_error()):
            log_out, exc_msg = self._run_capture(http_get_json, self.LEAK_URL, retries=0)
        self.assertNotIn("SECRET123", log_out)
        self.assertNotIn("SECRET123", exc_msg)
        self.assertIn("***REDACTED***", log_out)
        self.assertIn("***REDACTED***", exc_msg)

    def test_http_get_xml_masks_service_key(self):
        with patch("grm_common.requests.get", side_effect=self._conn_error()):
            log_out, exc_msg = self._run_capture(http_get_xml, self.LEAK_URL, retries=0)
        self.assertNotIn("SECRET123", log_out)
        self.assertNotIn("SECRET123", exc_msg)
        self.assertIn("***REDACTED***", log_out)
        self.assertIn("***REDACTED***", exc_msg)

    def test_http_get_bytes_masks_service_key(self):
        # http_get_bytes 는 마지막 시도에서만 예외를 던지고, WARN 로그는 "재시도 전"에만
        # 찍는다(구현 차이) — retries=1 로 최소 1번의 WARN 로그 + 최종 예외를 모두 관측한다.
        with patch("grm_common.requests.get", side_effect=self._conn_error()), \
             patch("grm_common.time.sleep"):
            log_out, exc_msg = self._run_capture(http_get_bytes, self.LEAK_URL, retries=1)
        self.assertNotIn("SECRET123", log_out)
        self.assertNotIn("SECRET123", exc_msg)
        self.assertIn("***REDACTED***", log_out)
        self.assertIn("***REDACTED***", exc_msg)

    def test_paginator_retry_path_masks_service_key(self):
        """_DatagoPaginator 가 주입받는 http_get(실전 배선=http_get_json) 이 연결 실패해도
        DatagoPageError.cause 문구에 원본 키가 남지 않아야 한다."""

        def failing_http_get(endpoint, params=None, timeout=None, retries=None):
            return http_get_json(endpoint, params=params, timeout=timeout, retries=retries)

        buf = io.StringIO()
        with patch("grm_common.requests.get", side_effect=self._conn_error()):
            with contextlib.redirect_stdout(buf):
                pg = datago_paginate(
                    "https://apis.data.go.kr/1471000/MdcinPrdlstInfoService",
                    service_key="SECRET123", max_pages=2, retries=0,
                    extract=lambda data: ([], 1, 10, 0, "00:OK"),
                    http_get=failing_http_get,
                )
                with self.assertRaises(DatagoPageError) as ctx:
                    list(pg)
        self.assertNotIn("SECRET123", buf.getvalue())
        self.assertNotIn("SECRET123", str(ctx.exception.cause))
        self.assertIn("***REDACTED***", buf.getvalue())

    def test_mask_service_key_is_idempotent_on_already_masked_text(self):
        """방어적 재마스킹(collect_mfds_law.py 등)이 이중 마스킹으로 문구를 깨지 않아야 한다."""
        once = mask_service_key(self.LEAK_URL)
        twice = mask_service_key(once)
        self.assertEqual(once, twice)
        self.assertNotIn("SECRET123", twice)


if __name__ == "__main__":
    unittest.main()
