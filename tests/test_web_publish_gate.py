"""CI 디스커버리 shim — 실제 483 발행 게이트 테스트는 `web/tests/test_publish_gate.py`.

grm-ci.yml 은 `python -m unittest discover -s tests` 로 이 디렉터리(tests/)만 순회한다.
483 Observation 발행 게이트(render.validate_483_observations) 테스트(web/tests/
test_publish_gate.py)를 공용 스위트·그린 카운트·머지 게이트에 포함시키기 위해 그
TestCase 들을 이 모듈 네임스페이스로 re-export 한다(test_web_render.py 와 동일 패턴).
"""
import pathlib
import sys

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

from test_publish_gate import *  # noqa: E402,F401,F403  (__all__ = TestCase 클래스들)
