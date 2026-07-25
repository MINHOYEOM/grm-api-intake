"""CI 디스커버리 shim — 실제 뉴스레터 테스트는 `web/tests/test_newsletter.py`.

`python -m unittest discover -s tests` 가 tests/ 만 순회하므로, 웹 서브트리의 뉴스레터
TestCase 들을 이 모듈 네임스페이스로 re-export 한다(test_web_render.py 와 동형).

재-export 는 **TestCase 하위클래스 전수 자동**이다. 예전엔 test_newsletter.__all__ 수동
목록이었는데, 새 클래스를 목록에 넣는 걸 잊으면 그 테스트가 CI 에서 **조용히 실행되지
않는다**(렌더 shim 에서 실제로 3개 클래스가 그랬다 — 초록인데 미실행). 목록이라는 이중
정본을 없애 표류를 구조적으로 차단한다: 클래스 정의 자체가 유일 정본.
"""
import inspect
import pathlib
import sys
import unittest

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

import test_newsletter as _web_newsletter  # noqa: E402

_EXPORTED = []
for _name, _obj in inspect.getmembers(_web_newsletter, inspect.isclass):
    if issubclass(_obj, unittest.TestCase) and _obj is not unittest.TestCase:
        globals()[_name] = _obj
        _EXPORTED.append(_name)


class WebNewsletterShimCoverageTest(unittest.TestCase):
    """shim 자체의 회귀 가드 — web/tests/test_newsletter.py 가 정의한 TestCase 는
    하나도 빠짐없이 이 모듈로 넘어와야 한다(미실행 테스트 0)."""

    def test_every_web_testcase_is_reexported(self):
        src = (_WEB_TESTS / "test_newsletter.py").read_text(encoding="utf-8")
        defined = {ln.split("class ", 1)[1].split("(", 1)[0]
                   for ln in src.splitlines()
                   if ln.startswith("class ") and "unittest.TestCase" in ln}
        self.assertTrue(defined, "web/tests/test_newsletter.py 에서 TestCase 를 찾지 못함")
        self.assertEqual(defined - set(_EXPORTED), set(),
                         "CI 에서 실행되지 않는 웹 TestCase 가 있다")
