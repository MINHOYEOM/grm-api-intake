"""★[2026-08-12] web/tests ↔ CI shim 존재·커버리지 상위 가드.

## 왜 이 파일이 있나
`grm-ci.yml` 은 `python -m unittest discover -s tests` 로 **`tests/` 만** 순회한다.
`web/tests/test_*.py` 는 `tests/test_web_*.py` shim 이 TestCase 를 re-export 해 줄 때만
CI 에 합류한다. 그런데 **"shim 이 존재하는가"를 검사하는 가드가 없었다** — 즉
`web/tests/test_foo.py` 를 새로 만들고 shim 을 깜빡하면 그 파일 전체가 **초록인 채
조용히 미실행**된다.

이 저장소는 정확히 이 계열로 두 번 당했다:
  · PR#351 — shim 이 수동 `__all__` 이라 웹 TestCase 3클래스가 장기 미실행(초록).
  · PR#366 — 루트 모듈 손열거가 낡아 36/58 이 게이트를 우회.
두 번 다 **전수 자동 열거 + 0건 가드**로 근원 수리했는데, 그 수리가 각 shim **안쪽**에만
적용됐고 "shim 자체가 있는가"라는 한 층 위는 비어 있었다. 여기서 그 층을 채운다.

## 무엇을 강제하나
1. `web/tests/test_X.py` 마다 `tests/test_web_X.py` 가 존재한다(명명 규약).
2. 각 shim 이 대상 모듈의 TestCase 를 **하나도 빠짐없이** 재export 한다.
3. 스캔이 살아 있다(0건은 성공이 아니라 글롭/규약이 깨졌다는 뜻).
"""
from __future__ import annotations

import importlib
import inspect
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = REPO / "tests"
WEB_TESTS_DIR = REPO / "web" / "tests"

#: shim 이 없어도 되는 web/tests 모듈 — 사유를 반드시 남긴다(현재 없음).
INTENTIONALLY_UNSHIMMED: dict[str, str] = {}


def _web_test_modules() -> list[str]:
    """web/tests/test_*.py 전수(손열거 금지 — 글롭)."""
    return sorted(p.stem for p in WEB_TESTS_DIR.glob("test_*.py"))


def _shim_path(stem: str) -> pathlib.Path:
    """web/tests/test_X.py → tests/test_web_X.py (저장소 명명 규약)."""
    return TESTS_DIR / f"test_web_{stem[len('test_'):]}.py"


class WebTestShimExistenceTest(unittest.TestCase):
    def test_scan_is_alive(self):
        mods = _web_test_modules()
        self.assertGreaterEqual(
            len(mods), 3,
            f"web/tests 에서 test_*.py 를 {len(mods)}개만 발견 — 글롭 경로가 깨졌다. "
            "빈 결과를 통과로 읽으면 이 가드가 침묵한다.")

    def test_every_web_test_module_has_a_shim(self):
        missing = [m for m in _web_test_modules()
                   if m not in INTENTIONALLY_UNSHIMMED and not _shim_path(m).is_file()]
        self.assertEqual(
            missing, [],
            "CI shim 이 없어 **조용히 미실행**되는 웹 테스트 모듈: "
            + ", ".join(f"{m} → {_shim_path(m).name} 필요" for m in missing))

    def test_unshimmed_allowlist_is_still_needed(self):
        """예외 목록도 낡는다 — 사라진 모듈·이미 shim 이 생긴 모듈은 지워야 한다."""
        mods = set(_web_test_modules())
        for stem, reason in INTENTIONALLY_UNSHIMMED.items():
            self.assertTrue(reason.strip(), f"{stem}: 제외 사유가 비었다")
            self.assertIn(stem, mods, f"{stem}: 더는 존재하지 않는다 — 예외에서 제거할 것")
            self.assertFalse(_shim_path(stem).is_file(),
                             f"{stem}: shim 이 생겼다 — 예외에서 제거할 것")


class WebTestShimCoverageTest(unittest.TestCase):
    """각 shim 이 대상 모듈의 TestCase 를 전부 넘겼는가 — shim 안쪽 커버리지.

    개별 shim 이 각자 자기 가드를 두던 방식(WebRenderShimCoverageTest 등)은 새 shim 이
    생길 때마다 사람이 가드를 또 적어야 한다. 여기서 전 shim 을 한 번에 검사한다."""

    def test_all_shims_reexport_every_testcase(self):
        # discover -s tests 로 돌 때는 tests/ 가 자동으로 sys.path 에 들어가지만,
        # `-m unittest tests.test_web_shims` 로 직접 부르면 안 들어간다 — 둘 다 되게 한다.
        for d in (WEB_TESTS_DIR, TESTS_DIR):
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))
        problems = []
        for stem in _web_test_modules():
            shim = _shim_path(stem)
            if not shim.is_file():
                continue                      # 존재 여부는 위 클래스가 판정
            target = importlib.import_module(stem)
            defined = {
                n for n, o in inspect.getmembers(target, inspect.isclass)
                if issubclass(o, unittest.TestCase) and o is not unittest.TestCase
                and o.__module__ == target.__name__
            }
            shim_mod = importlib.import_module(shim.stem)
            exported = {
                n for n, o in inspect.getmembers(shim_mod, inspect.isclass)
                if issubclass(o, unittest.TestCase) and o is not unittest.TestCase
            }
            gap = defined - exported
            if gap:
                problems.append(f"{shim.name}: {sorted(gap)} 미전달")
        self.assertEqual(problems, [], "CI 에서 실행되지 않는 웹 TestCase: " + "; ".join(problems))


if __name__ == "__main__":
    unittest.main()
