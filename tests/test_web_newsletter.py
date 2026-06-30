"""CI 디스커버리 shim — 실제 뉴스레터 테스트는 `web/tests/test_newsletter.py`.

`python -m unittest discover -s tests` 가 tests/ 만 순회하므로, 웹 서브트리의 뉴스레터
TestCase 들을 이 모듈 네임스페이스로 re-export 한다(test_web_render.py 와 동형).
"""
import pathlib
import sys

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

from test_newsletter import *  # noqa: E402,F401,F403  (__all__ = TestCase 클래스들)
