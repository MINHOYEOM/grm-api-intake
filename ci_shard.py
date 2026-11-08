#!/usr/bin/env python3
"""CI unittest 샤딩 러너 — `python ci_shard.py --index i --total N`.

`python -m unittest discover -s tests` 한 줄은 약 29분이 걸린다. 이 러너는 같은 discover
결과를 N 개 조로 **겹침·누락 없이** 나눠 병렬 잡(grm-ci.yml matrix)에서 돌린다.

## 나누는 단위
- 기본 단위는 **TestCase 클래스**다. 시간의 대부분이 한 모듈(`tests/test_web_render.py` shim →
  `web/tests/test_render.py` 의 클래스 100여 개, 클래스마다 setUpClass 가 사이트를 빌드)에
  몰려 있어 모듈 단위로는 균형이 안 맞는다. setUpClass 상태는 클래스 안에 갇히므로 클래스
  단위로 갈라도 안전하다.
- 모듈이 `setUpModule`/`tearDownModule` 을 정의하면 **모듈 전체가 한 단위**다(모듈 상태 보존).
- import 실패(_FailedTest)는 모듈 단위로 취급 — 실패는 어느 조에서든 반드시 빨갛게 나온다.

## 균형
`tests/ci_shard_timings.json`({단위: 초})을 읽어 **탐욕(LPT) 배정**을 한다: 무거운 단위부터
가장 가벼운 조에 넣는다. 동점은 이름·인덱스 순이라 결정론적이다. 기록이 없는 새 단위는
테스트 수 x DEFAULT_SECONDS_PER_TEST 로 어림한다.
기록 갱신: 각 샤드가 `--record out.json` 으로 단위별 실측을 남기고(CI 가 아티팩트로 업로드),
`--merge a.json b.json ... --into tests/ci_shard_timings.json` 로 합쳐 커밋한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"
TIMINGS_PATH = TESTS_DIR / "ci_shard_timings.json"
DEFAULT_SECONDS_PER_TEST = 0.05


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
    """샤딩 단위 키. 모듈이 모듈 수준 fixture 를 쓰면 모듈, 아니면 클래스."""
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
    """{단위 키: [테스트...]} - 발견 순서를 유지한다."""
    units: dict = {}
    for t in tests:
        units.setdefault(unit_key(t), []).append(t)
    return units


def load_timings(path: Path = TIMINGS_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def weight_of(key: str, n_tests: int, timings: dict) -> float:
    if key in timings:
        return timings[key]
    return max(n_tests, 1) * DEFAULT_SECONDS_PER_TEST


def partition(weights: dict, total: int) -> list:
    """{단위: 무게} -> total 개의 단위 키 리스트. LPT 탐욕, 결정론적."""
    if total < 1:
        raise ValueError("total >= 1 이어야 한다")
    bins = [[] for _ in range(total)]
    loads = [0.0] * total
    for key in sorted(weights, key=lambda k: (-weights[k], k)):
        i = min(range(total), key=lambda j: (loads[j], j))
        bins[i].append(key)
        loads[i] += weights[key]
    return bins


def select_shard(units: dict, index: int, total: int, timings: dict) -> list:
    if not 0 <= index < total:
        raise ValueError(f"index {index} 는 0..{total - 1} 범위여야 한다")
    weights = {k: weight_of(k, len(v), timings) for k, v in units.items()}
    mine = set(partition(weights, total)[index])
    return [t for k, v in units.items() if k in mine for t in v]


class _TimedResult(unittest.TextTestResult):
    """단위별 소요를 '직전 테스트 종료 -> 이 테스트 종료' 간격으로 합산한다.
    (setUpClass/tearDownClass 시간이 첫/끝 테스트에 귀속된다.)"""

    def startTestRun(self):
        super().startTestRun()
        self.unit_seconds = {}
        self._last = time.perf_counter()

    def stopTest(self, test):
        super().stopTest(test)
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
    units = group_units(discover())
    timings = load_timings()
    tests = select_shard(units, args.index, args.total, timings)
    n_units = len({unit_key(t) for t in tests})
    print(f"shard {args.index}/{args.total}: {len(tests)} tests, {n_units} units "
          f"(전체 {sum(len(v) for v in units.values())} tests, 기록 {len(timings)} units)")
    if args.list:
        for t in tests:
            print(t.id())
        return 0

    runner = unittest.TextTestRunner(verbosity=2, resultclass=_TimedResult)
    result = runner.run(unittest.TestSuite(tests))
    if args.record:
        Path(args.record).write_text(
            json.dumps({k: round(v, 2) for k, v in sorted(result.unit_seconds.items())},
                       ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
