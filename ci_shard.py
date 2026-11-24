#!/usr/bin/env python3
"""CI unittest 샤딩 러너 — `python ci_shard.py --index i --total N`.

`python -m unittest discover -s tests` 한 줄은 약 29분이 걸린다. 이 러너는 같은 discover
결과를 N 개 조로 **겹침·누락 없이** 나눠 병렬 잡(grm-ci.yml matrix)에서 돌린다.

## 나누는 단위
- 기본 배정 단위는 **시험 모듈**이다. `tests/test_web_render.py` shim이 불러오는
  `web/tests/test_render.py`는 실측 약 23분이 걸려 기존처럼 **TestCase 클래스**로 나눈다.
- 모듈이 `setUpModule`/`tearDownModule` 을 정의하면 **모듈 전체가 한 단위**다(모듈 상태 보존).
- import 실패(_FailedTest)는 모듈 단위로 취급 — 실패는 어느 조에서든 반드시 빨갛게 나온다.

## 균형
`tests/ci_shard_timings.json`({단위: 초})을 읽어 **탐욕(LPT) 배정**을 한다: 무거운 단위부터
가장 가벼운 조에 넣는다. 파일별 기록은 기존 클래스별 실측을 합산한다. 기록이 없는 파일은
실측한 파일의 평균, 새 렌더 클래스는 실측한 렌더 클래스의 평균으로 어림한다.
기록 갱신: 각 샤드가 `--record out.json` 으로 기존 단위별 실측을 남기고(CI 가 아티팩트로 업로드),
`--merge a.json b.json ... --into tests/ci_shard_timings.json` 로 합쳐 커밋한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import unittest
from pathlib import Path
from statistics import mean

from ci_network_guard import ExternalNetworkBlocked, NetworkGuard

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"
TIMINGS_PATH = TESTS_DIR / "ci_shard_timings.json"
DEFAULT_SECONDS_PER_TEST = 0.05
RENDER_TEST_MODULE = "test_render"


def iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def _module_name(test) -> str:
    if type(test).__name__ == "_FailedTest":
        # id() 가 'unittest.loader._FailedTest.<모듈>' 이다 - 모듈명은 메서드명에 있다.
        return test._testMethodName
    return type(test).__module__


def unit_key(test) -> str:
    """실측 기록의 키. 모듈 fixture가 있으면 모듈, 아니면 클래스."""
    mod = _module_name(test)
    if type(test).__name__ == "_FailedTest":
        return mod
    module = sys.modules.get(mod)
    if module is not None and (hasattr(module, "setUpModule")
                               or hasattr(module, "tearDownModule")):
        return mod
    return f"{mod}.{type(test).__qualname__}"


def discover(start_dir: Path = TESTS_DIR) -> list:
    """CI 와 같은 discover(-s tests). 반환: 평탄화한 테스트 인스턴스 리스트(발견 순서)."""
    loader = unittest.TestLoader()
    return list(iter_tests(loader.discover(str(start_dir))))


def group_units(tests: list) -> dict:
    """{배정 단위: [테스트...]} — 발견 순서를 유지한다."""
    units: dict = {}
    for test in tests:
        module = _module_name(test)
        key = unit_key(test) if module == RENDER_TEST_MODULE else module
        units.setdefault(key, []).append(test)
    return units


def load_timings(path: Path = TIMINGS_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def shard_weights(units: dict, timings: dict) -> dict:
    recorded = {}
    render_prefix = f"{RENDER_TEST_MODULE}."
    file_seconds = []
    render_seconds = []
    for key, tests in units.items():
        timing_keys = dict.fromkeys(unit_key(test) for test in tests)
        seconds = [timings[name] for name in timing_keys if name in timings]
        if seconds:
            recorded[key] = sum(seconds)
            samples = render_seconds if key.startswith(render_prefix) else file_seconds
            samples.append(recorded[key])

    file_average = mean(file_seconds) if file_seconds else None
    render_average = mean(render_seconds) if render_seconds else None
    weights = {}
    for key, tests in units.items():
        default = render_average if key.startswith(render_prefix) else file_average
        if default is None:
            default = len(tests) * DEFAULT_SECONDS_PER_TEST
        weights[key] = recorded.get(key, default)
    return weights


def partition(weights: dict, total: int) -> list:
    """{단위: 무게} -> total 개의 단위 키 리스트. LPT 탐욕, 결정론적."""
    if total < 1:
        raise ValueError("total >= 1 이어야 한다")
    bins = [[] for _ in range(total)]
    loads = [0.0] * total
    for key in sorted(weights, key=lambda k: (-weights[k], k)):
        i = min(range(total), key=lambda j: (loads[j], len(bins[j]), j))
        bins[i].append(key)
        loads[i] += weights[key]
    return bins


def select_shard(units: dict, index: int, total: int, timings: dict) -> list:
    if not 0 <= index < total:
        raise ValueError(f"index {index} 는 0..{total - 1} 범위여야 한다")
    weights = shard_weights(units, timings)
    mine = set(partition(weights, total)[index])
    return [t for k, v in units.items() if k in mine for t in v]


class _TimedResult(unittest.TextTestResult):
    """단위별 소요를 '직전 테스트 종료 -> 이 테스트 종료' 간격으로 합산한다.
    (setUpClass/tearDownClass 시간이 첫/끝 테스트에 귀속된다.)"""

    def startTestRun(self):
        super().startTestRun()
        self.unit_seconds = {}
        self._last = time.perf_counter()

    def startTest(self, test):
        super().startTest(test)
        self.guard.test_id = test.id()
        self._network_start = len(self.guard.violations)

    def addSuccess(self, test):
        violations = self.guard.violations[self._network_start:]
        if violations:
            error = ExternalNetworkBlocked("\n".join(violations))
            self.addFailure(test, (type(error), error, None))
        else:
            super().addSuccess(test)

    def stopTest(self, test):
        super().stopTest(test)
        self.guard.test_id = None
        now = time.perf_counter()
        key = unit_key(test)
        self.unit_seconds[key] = self.unit_seconds.get(key, 0.0) + (now - self._last)
        self._last = now


def merge_timings(paths: list, into: Path) -> dict:
    merged: dict = {}
    for p in paths:
        for k, v in json.loads(Path(p).read_text(encoding="utf-8")).items():
            merged[k] = round(float(v), 2)
    Path(into).write_text(
        json.dumps(dict(sorted(merged.items())), ensure_ascii=False, indent=0,
                   sort_keys=True) + "\n", encoding="utf-8")
    return merged


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="CI unittest 샤딩 러너")
    ap.add_argument("--index", type=int, help="이 조의 번호(0부터)")
    ap.add_argument("--total", type=int, help="전체 조 수")
    ap.add_argument("--record", help="단위별 실측(초)을 이 JSON 파일에 쓴다")
    ap.add_argument("--list", action="store_true", help="이 조의 테스트 id 만 출력하고 끝낸다")
    ap.add_argument("--merge", nargs="+", help="--record 결과들을 합친다(--into 필수)")
    ap.add_argument("--into", help="--merge 출력 경로")
    args = ap.parse_args(argv)

    if args.merge:
        if not args.into:
            ap.error("--merge 에는 --into 가 필요하다")
        merged = merge_timings(args.merge, Path(args.into))
        print(f"merged {len(merged)} units -> {args.into}")
        return 0
    if args.index is None or args.total is None:
        ap.error("--index 와 --total 이 필요하다")

    # `unittest discover -s tests` 와 동일하게 tests/ 를 sys.path 에 둔다(+ 루트 모듈 import).
    sys.path.insert(0, str(TESTS_DIR))
    sys.path.insert(0, str(ROOT))
    guard = NetworkGuard()
    with guard.installed():
        status = _run_shard(args, guard)
    for violation in guard.violations:
        print(violation, file=sys.stderr)
    return 1 if guard.violations else status


def _run_shard(args, guard):
    units = group_units(discover())
    timings = load_timings()
    tests = select_shard(units, args.index, args.total, timings)
    n_units = len(group_units(tests))
    print(f"shard {args.index}/{args.total}: {len(tests)} tests, {n_units} units "
          f"(전체 {sum(len(v) for v in units.values())} tests, 기록 {len(timings)} units)")
    if args.list:
        for t in tests:
            print(t.id())
        return 0

    def make_result(*args):
        result = _TimedResult(*args)
        result.guard = guard
        return result

    runner = unittest.TextTestRunner(verbosity=2, resultclass=make_result)
    result = runner.run(unittest.TestSuite(tests))
    if args.record:
        Path(args.record).write_text(
            json.dumps({k: round(v, 2) for k, v in sorted(result.unit_seconds.items())},
                       ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
