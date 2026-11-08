"""CI 디스커버리 shim — 실제 실사 결과 카드 앞면 테스트는 `web/tests/test_inspection_front.py`.

`python -m unittest discover -s tests` 가 tests/ 만 순회하므로, 웹 서브트리 TestCase 를 이
모듈 네임스페이스로 re-export 한다. 재-export 는 **TestCase 하위클래스 전수 자동**이다
(`test_web_wl_anatomy.py` 와 동형).
"""
import inspect
import pathlib
import sys
import unittest

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

import test_inspection_front as _web_inspection_front  # noqa: E402

_EXPORTED = []
for _name, _obj in inspect.getmembers(_web_inspection_front, inspect.isclass):
    if issubclass(_obj, unittest.TestCase) and _obj is not unittest.TestCase:
        globals()[_name] = _obj
        _EXPORTED.append(_name)


class WebInspectionFrontShimCoverageTest(unittest.TestCase):
    """shim 자체의 회귀 가드 — web/tests/test_inspection_front.py 가 정의한 TestCase 는 하나도
    빠짐없이 이 모듈로 넘어와야 한다(미실행 테스트 0)."""

    def test_every_web_testcase_is_reexported(self):
        src = (_WEB_TESTS / "test_inspection_front.py").read_text(encoding="utf-8")
        defined = {ln.split("class ", 1)[1].split("(", 1)[0]
                   for ln in src.splitlines()
                   if ln.startswith("class ") and "unittest.TestCase" in ln}
        self.assertTrue(defined, "web/tests/test_inspection_front.py 에서 TestCase 를 찾지 못함")
        self.assertEqual(defined - set(_EXPORTED), set(),
                         "CI 에서 실행되지 않는 웹 TestCase 가 있다")
