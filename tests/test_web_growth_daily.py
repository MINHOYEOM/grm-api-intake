"""CI 디스커버리 shim — 실제 성장 일보 v2 테스트는 `web/tests/test_growth_daily.py`.

`python -m unittest discover -s tests` 가 tests/ 만 순회하므로, 웹 서브트리 TestCase 를 이
모듈 네임스페이스로 re-export 한다(전수 자동 — `test_web_growth_weekly.py` 와 동형).
"""
import inspect
import pathlib
import sys
import unittest

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

import test_growth_daily as _web_growth_daily  # noqa: E402

_EXPORTED = []
for _name, _obj in inspect.getmembers(_web_growth_daily, inspect.isclass):
    if issubclass(_obj, unittest.TestCase) and _obj is not unittest.TestCase:
        globals()[_name] = _obj
        _EXPORTED.append(_name)


class WebGrowthDailyShimCoverageTest(unittest.TestCase):
    """web/tests/test_growth_daily.py 의 TestCase 는 하나도 빠짐없이 이 모듈로 넘어와야 한다."""

    def test_every_web_testcase_is_reexported(self):
        src = (_WEB_TESTS / "test_growth_daily.py").read_text(encoding="utf-8")
        defined = {ln.split("class ", 1)[1].split("(", 1)[0]
                   for ln in src.splitlines()
                   if ln.startswith("class ") and "unittest.TestCase" in ln}
        self.assertTrue(defined)
        self.assertEqual(defined - set(_EXPORTED), set(), "CI 에서 실행되지 않는 웹 TestCase 가 있다")
