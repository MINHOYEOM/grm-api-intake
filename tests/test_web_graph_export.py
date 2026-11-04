"""CI 디스커버리 shim — 실제 테스트는 `web/tests/test_graph_export.py` (TestCase 전수 자동 re-export)."""
import inspect
import pathlib
import sys
import unittest

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

import test_graph_export as _web_graph  # noqa: E402

_EXPORTED = []
for _name, _obj in inspect.getmembers(_web_graph, inspect.isclass):
    if issubclass(_obj, unittest.TestCase) and _obj is not unittest.TestCase:
        globals()[_name] = _obj
        _EXPORTED.append(_name)


class WebGraphShimCoverageTest(unittest.TestCase):
    def test_every_web_testcase_is_reexported(self):
        src = (_WEB_TESTS / "test_graph_export.py").read_text(encoding="utf-8")
        defined = {ln.split("class ", 1)[1].split("(", 1)[0]
                   for ln in src.splitlines()
                   if ln.startswith("class ") and "unittest.TestCase" in ln}
        self.assertTrue(defined)
        self.assertEqual(defined - set(_EXPORTED), set(), "CI 에서 실행되지 않는 웹 TestCase 가 있다")
