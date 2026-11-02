"""CI 디스커버리 shim — 실제 가드는 `web/tests/test_member_newsletter_optin.py`.

`python -m unittest discover -s tests` 가 tests/ 만 순회하므로, 웹 서브트리 TestCase 를 이
모듈 네임스페이스로 re-export 한다(TestCase 하위클래스 전수 자동 — `test_web_i18n_plural.py` 동형).
"""
import inspect
import pathlib
import sys
import unittest

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

import test_member_newsletter_optin as _web_mod  # noqa: E402

_EXPORTED = []
for _name, _obj in inspect.getmembers(_web_mod, inspect.isclass):
    if issubclass(_obj, unittest.TestCase) and _obj is not unittest.TestCase:
        globals()[_name] = _obj
        _EXPORTED.append(_name)
