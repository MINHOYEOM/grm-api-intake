#!/usr/bin/env python3
"""findings_search_cache_sync.py + 088 마이그레이션 정적 계약 — 실 네트워크·실 DB 없음.

지키려는 것:
  · 스크립트는 표에 직접 쓰지 않고 RPC 하나만 부른다 · dry-run 은 네트워크 0
  · 빈 목록·상한 초과는 네트워크 전에 막는다(서버 거부와 같은 규칙)
  · 키는 어떤 출력에도 나오지 않는다 · 5xx 는 1회 재시도, 그래도 실패면 exit 1
  · 088: definer + service_role 만 · hot 행 불가침 · 삭제 상한 · 빈 목록 거부 · 086 정규화 함수 재사용
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import findings_search_cache_sync as svc

_ROOT = Path(__file__).resolve().parents[1]
_MIG = _ROOT / "web" / "migrations" / "088_findings_search_cache_sync.sql"
_SERVICE_KEY = "service-role-secret-token"
_BASE = "https://example.supabase.co"


def _cases_file(qs: list, tmp: str) -> str:
    p = Path(tmp) / "glossary_cases.json"
    p.write_text(json.dumps({"schema": "x", "items": [{"id": f"t{i}", "q": q, "findings": 1}
                                                       for i, q in enumerate(qs)]},
                            ensure_ascii=False), encoding="utf-8")
    return str(p)


class _Resp:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class LoadTermsTest(unittest.TestCase):
    def test_dedup_strip_sort_and_format_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _cases_file(["CAPA", " capa ", "CAPA", "", "x" * 65, "bad\x01ctl", "품질관리"], tmp)
            self.assertEqual(svc.load_terms(path), ["CAPA", "capa", "품질관리"])

    def test_missing_items_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "g.json"
            p.write_text('{"schema": "x"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                svc.load_terms(p)

    def test_real_glossary_cases_file_loads_and_is_within_cap(self):
        terms = svc.load_terms(_ROOT / "web" / "data" / "glossary_cases.json")
        self.assertGreaterEqual(len(terms), 100)
        self.assertLessEqual(len(terms), svc.MAX_TERMS)


class MainTest(unittest.TestCase):
    def _run(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", env or {}, clear=False), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = svc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_makes_no_network_call_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _cases_file(["CAPA", "Recall"], tmp)
            report = Path(tmp) / "r.json"
            with mock.patch("findings_search_cache_sync.requests.post") as post:
                code, out, _ = self._run(["--glossary-cases", path, "--dry-run", "--output", str(report)])
            self.assertEqual(code, 0)
            post.assert_not_called()
            self.assertIn("dry-run", out)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["terms"], 2)
            self.assertTrue(data["dry_run"])

    def test_empty_list_refused_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _cases_file([], tmp)
            with mock.patch("findings_search_cache_sync.requests.post") as post:
                code, _, err = self._run(["--glossary-cases", path,
                                          "--supabase-url", _BASE, "--service-role-key", _SERVICE_KEY])
            self.assertEqual(code, 2)
            post.assert_not_called()
            self.assertIn("비어", err)

    def test_missing_credentials_is_exit_2_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _cases_file(["CAPA"], tmp)
            with mock.patch("findings_search_cache_sync.requests.post") as post:
                code, _, err = self._run(["--glossary-cases", path],
                                         env={"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""})
            self.assertEqual(code, 2)
            post.assert_not_called()
            self.assertNotIn(_SERVICE_KEY, err)

    def test_apply_calls_rpc_once_with_sorted_terms_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _cases_file(["Recall", "CAPA"], tmp)
            report = Path(tmp) / "r.json"
            result = {"received": 2, "valid": 2, "dropped": 0, "added": 1, "removed": 0,
                      "total_daily": 193, "hot_rows": 2}
            with mock.patch("findings_search_cache_sync.requests.post",
                            return_value=_Resp(200, result)) as post:
                code, out, err = self._run(["--glossary-cases", path, "--output", str(report),
                                            "--supabase-url", _BASE, "--service-role-key", _SERVICE_KEY])
            self.assertEqual(code, 0)
            self.assertEqual(post.call_count, 1)
            call = post.call_args
            self.assertEqual(call.args[0], f"{_BASE}/rest/v1/rpc/findings_search_cache_sync")
            self.assertEqual(call.kwargs["json"], {"p_qs": ["CAPA", "Recall"]})
            self.assertEqual(call.kwargs["headers"]["Authorization"], f"Bearer {_SERVICE_KEY}")
            self.assertIn("added=1", out)
            self.assertNotIn(_SERVICE_KEY, out + err)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["result"], result)

    def test_5xx_retries_once_then_exit_1_without_leaking_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _cases_file(["CAPA"], tmp)
            report = Path(tmp) / "r.json"
            with mock.patch("findings_search_cache_sync.requests.post",
                            side_effect=[_Resp(503), _Resp(503)]) as post:
                code, out, err = self._run(["--glossary-cases", path, "--output", str(report),
                                            "--supabase-url", _BASE, "--service-role-key", _SERVICE_KEY])
            self.assertEqual(code, 1)
            self.assertEqual(post.call_count, 2)
            self.assertIn("HTTP 503", err)
            self.assertNotIn(_SERVICE_KEY, out + err + report.read_text(encoding="utf-8"))

    def test_4xx_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _cases_file(["CAPA"], tmp)
            with mock.patch("findings_search_cache_sync.requests.post",
                            return_value=_Resp(401)) as post:
                code, _, err = self._run(["--glossary-cases", path,
                                          "--supabase-url", _BASE, "--service-role-key", _SERVICE_KEY])
            self.assertEqual(code, 1)
            self.assertEqual(post.call_count, 1)
            self.assertIn("HTTP 401", err)


def _strip(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


class SyncMigrationContractTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(_MIG.is_file(), f"missing {_MIG}")
        self.sql = _MIG.read_text(encoding="utf-8")
        self.code = _strip(self.sql)

    def test_no_crlf_and_has_comments(self):
        self.assertNotIn(b"\r\n", _MIG.read_bytes())
        self.assertGreaterEqual(self.sql.count("--"), 15)

    def test_definer_and_service_role_only(self):
        self.assertIn("security definer", self.code)
        self.assertIn("set search_path to 'public'", self.code)
        self.assertIn("revoke execute on function public.findings_search_cache_sync(text[]) "
                      "from public, anon, authenticated;", self.code)
        self.assertIn("grant execute on function public.findings_search_cache_sync(text[]) to service_role;",
                      self.code)

    def test_reuses_086_normalizer_with_text_only(self):
        self.assertIn("public.findings_search_cache_args(p_q := btrim(q), p_text_only := true)", self.code)

    def test_hot_rows_untouched(self):
        # insert 는 'daily' 로만, delete 는 tier = 'daily' 조건이 붙어 있어야 한다.
        self.assertIn("select w.args, 'daily' from _sync_want w", self.code)
        self.assertEqual(self.code.count("delete from public.findings_search_cache"), 1)
        self.assertRegex(self.code, re.compile(r"delete from public\.findings_search_cache c\s+where c\.tier = 'daily'"))

    def test_guards_present(self):
        self.assertIn("empty list refused", self.code)
        self.assertIn("> 400", self.code)
        self.assertIn("greatest(20, (n_daily * 25) / 100)", self.code)
        self.assertIn("would remove % daily rows", self.code)
        self.assertIn("[[:cntrl:]]", self.code)
        self.assertIn("length(btrim(q)) <= 64", self.code)

    def test_no_touch_on_086_objects(self):
        for forbidden in ("drop table", "alter table", "cron.schedule", "cron.unschedule",
                          "create function public.findings_search(", "findings_search_compute"):
            self.assertNotIn(forbidden, self.code, forbidden)

    def test_reloads_postgrest(self):
        self.assertIn("notify pgrst, 'reload schema';", self.code)

    def test_script_caps_match_server(self):
        self.assertEqual(svc.MAX_TERMS, 400)
        self.assertEqual(svc.MAX_TERM_LEN, 64)


if __name__ == "__main__":
    unittest.main()
