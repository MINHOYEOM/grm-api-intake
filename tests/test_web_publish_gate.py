"""CI 디스커버리 shim — 실제 483 발행 게이트 테스트는 `web/tests/test_publish_gate.py`.

grm-ci.yml 은 `python -m unittest discover -s tests` 로 이 디렉터리(tests/)만 순회한다.
483 Observation 발행 게이트(render.validate_483_observations) 테스트(web/tests/
test_publish_gate.py)를 공용 스위트·그린 카운트·머지 게이트에 포함시키기 위해 그
TestCase 들을 이 모듈 네임스페이스로 re-export 한다.

★[2026-08-12] `from test_publish_gate import *` + 대상 모듈의 수동 `__all__` 이었다 —
새 TestCase 를 목록에 넣는 걸 잊으면 **초록인데 조용히 미실행**되는, 이 저장소가 이미
당한 관용구다(PR#351). TestCase 전수 자동 수집으로 바꿔 이중 정본을 없앤다.
커버리지는 tests/test_web_shims.py 가 전 shim 에 대해 한 번에 강제한다.
"""
import inspect
import pathlib
import sys
import unittest

_WEB_TESTS = pathlib.Path(__file__).resolve().parent.parent / "web" / "tests"
sys.path.insert(0, str(_WEB_TESTS))

import test_publish_gate as _web_publish_gate  # noqa: E402

for _name, _obj in inspect.getmembers(_web_publish_gate, inspect.isclass):
    if issubclass(_obj, unittest.TestCase) and _obj is not unittest.TestCase:
        globals()[_name] = _obj
