"""CI 디스커버리 shim — 실제 웹 렌더러 테스트는 `web/tests/test_render.py`.

grm-ci.yml 은 `python -m unittest discover -s tests` 로 이 디렉터리(tests/)만 순회한다.
웹 서브시스템 테스트(web/tests/test_render.py)를 기존 그린 카운트·머지 게이트에
포함시키기 위해, 그 TestCase 들을 이 모듈 네임스페이스로 re-export 한다.
(웹 서브트리는 수집 .py 루트와 분리 — 테스트만 공용 스위트에 합류.)

재-export 는 **TestCase 하위클래스 전수 자동**이다. 예전엔 `from test_render import *` +
test_render.__all__ 수동 목록이었는데, 새 클래스를 목록에 넣는 걸 잊으면 그 테스트가
CI 에서 **조용히 실행되지 않았다**(9~11차 3개 클래스가 실제로 그랬다 — 초록인데 미실행).
목록이라는 이중 정본을 없애 표류를 구조적으로 차단한다: 클래스 정의 자체가 유일 정본.
"""
import inspect
import pathlib
import sys
import unittest

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

import test_render as _web_render  # noqa: E402

_EXPORTED = []
for _name, _obj in inspect.getmembers(_web_render, inspect.isclass):
    if issubclass(_obj, unittest.TestCase) and _obj is not unittest.TestCase:
        globals()[_name] = _obj
        _EXPORTED.append(_name)


class WebRenderShimCoverageTest(unittest.TestCase):
    """shim 자체의 회귀 가드 — web/tests/test_render.py 가 정의한 TestCase 는
    하나도 빠짐없이 이 모듈로 넘어와야 한다(미실행 테스트 0)."""

    def test_every_web_testcase_is_reexported(self):
        src = (_WEB_TESTS / "test_render.py").read_text(encoding="utf-8")
        defined = {ln.split("class ", 1)[1].split("(", 1)[0]
                   for ln in src.splitlines()
                   if ln.startswith("class ") and "unittest.TestCase" in ln}
        self.assertTrue(defined, "web/tests/test_render.py 에서 TestCase 를 찾지 못함")
        self.assertEqual(defined - set(_EXPORTED), set(),
                         "CI 에서 실행되지 않는 웹 TestCase 가 있다")
