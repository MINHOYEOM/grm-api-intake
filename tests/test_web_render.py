"""CI 디스커버리 shim — 실제 웹 렌더러 테스트는 `web/tests/test_render.py`.

grm-ci.yml 은 `python -m unittest discover -s tests` 로 이 디렉터리(tests/)만 순회한다.
웹 서브시스템 테스트(web/tests/test_render.py)를 기존 그린 카운트·머지 게이트에
포함시키기 위해, 그 TestCase 들을 이 모듈 네임스페이스로 re-export 한다.
(웹 서브트리는 수집 .py 루트와 분리 — 테스트만 공용 스위트에 합류.)
"""
import pathlib
import sys

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

from test_render import *  # noqa: E402,F401,F403  (__all__ = TestCase 클래스들)
