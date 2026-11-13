"""157 공개 RPC 함수 단위 statement_timeout + 화면 재시도 — 계약 고정.

· migration 은 함수 설정(proconfig) 한 항목만 바꾼다: create/drop/rename/grant/revoke 0 → 본문·권한·반환 바이트 불변.
· 화면 3곳(findings.js·trends.js·checklist.js)은 5xx·네트워크 오류만 두 번 더 시도하고, 4xx 는 즉시 실패한다.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "web" / "migrations" / "157_public_rpc_statement_timeout.sql"
ASSETS = ROOT / "web" / "assets"


def _code(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))


class Migration157(unittest.TestCase):
    def setUp(self):
        self.code = _code(MIG.read_text(encoding="utf-8")).lower()

    def test_only_function_settings(self):
        for word in ("create ", "drop ", "rename", "grant ", "revoke ", "insert ", "update ", "delete "):
            self.assertNotIn(word, self.code, word)

    def test_four_public_rpcs_8s(self):
        alters = re.findall(r"alter function public\.(\w+)\(([^)]*)\)\s*set statement_timeout = '8s';", self.code)
        self.assertEqual(
            sorted(n for n, _ in alters),
            ["findings_checklist", "findings_inspector_profile", "findings_search", "findings_similar_to"],
        )
        self.assertIn("notify pgrst, 'reload schema';", self.code)


class ClientRetry(unittest.TestCase):
    def _src(self, name):
        return (ASSETS / name).read_text(encoding="utf-8")

    def test_findings_js_retries_5xx_only(self):
        s = self._src("findings.js")
        self.assertIn("function fetchSearch(page, attempt)", s)
        self.assertIn("function fetchSearchOnce(page)", s)
        self.assertIn("err.status < 500", s)
        self.assertIn("shapeErr.noRetry = true", s)

    def test_trends_js_search_calls_use_retry(self):
        s = self._src("trends.js")
        self.assertIn("function postSearchRetry(body, attempt)", s)
        self.assertEqual(s.count('rpcEndpoint("findings_search")'), 1)  # 재시도 함수 한 곳만 직접 호출

    def test_checklist_js_rpc_retry(self):
        s = self._src("checklist.js")
        self.assertIn("function rpc(name, body, attempt)", s)
        self.assertIn("err.status < 500", s)


if __name__ == "__main__":
    unittest.main()
