"""CI 디스커버리 shim — 실제 영문 주간 메일 테스트는 `web/tests/test_newsletter_en.py`.

`tests/test_web_newsletter.py` 와 같은 방식: TestCase 하위클래스를 전수 자동 re-export 하고,
빠진 클래스가 없는지 shim 스스로 검사한다(초록인데 미실행 방지).
"""
import inspect
import pathlib
import sys
import unittest

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

import test_newsletter_en as _web_newsletter_en  # noqa: E402

_EXPORTED = []
for _name, _obj in inspect.getmembers(_web_newsletter_en, inspect.isclass):
    if issubclass(_obj, unittest.TestCase) and _obj is not unittest.TestCase:
        globals()[_name] = _obj
        _EXPORTED.append(_name)


class WebNewsletterEnShimCoverageTest(unittest.TestCase):
    def test_every_web_testcase_is_reexported(self):
        src = (_WEB_TESTS / "test_newsletter_en.py").read_text(encoding="utf-8")
        defined = {ln.split("class ", 1)[1].split("(", 1)[0]
                   for ln in src.splitlines()
                   if ln.startswith("class ") and "unittest.TestCase" in ln}
        self.assertTrue(defined, "web/tests/test_newsletter_en.py 에서 TestCase 를 찾지 못함")
        self.assertEqual(defined - set(_EXPORTED), set(),
                         "CI 에서 실행되지 않는 웹 TestCase 가 있다")
