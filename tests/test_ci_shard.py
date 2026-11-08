"""ci_shard.py 회귀 가드 - 4개 조의 합이 전체 discover 와 정확히 같아야 한다(누락·중복 0)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ci_shard  # noqa: E402


class PartitionTest(unittest.TestCase):
    WEIGHTS = {f"m{i}.C": float((i * 7) % 13 + 1) for i in range(40)}

    def test_union_equals_all_without_overlap(self):
        for total in (1, 2, 4, 7):
            bins = ci_shard.partition(self.WEIGHTS, total)
            flat = [k for b in bins for k in b]
            self.assertEqual(len(flat), len(set(flat)))
            self.assertEqual(set(flat), set(self.WEIGHTS))

    def test_deterministic(self):
        a = ci_shard.partition(self.WEIGHTS, 4)
        b = ci_shard.partition(dict(reversed(list(self.WEIGHTS.items()))), 4)
        self.assertEqual(a, b)

    def test_balanced(self):
        bins = ci_shard.partition(self.WEIGHTS, 4)
        loads = [sum(self.WEIGHTS[k] for k in b) for b in bins]
        self.assertLessEqual(max(loads) - min(loads), max(self.WEIGHTS.values()))

    def test_one_heavy_unit_gets_own_shard(self):
        bins = ci_shard.partition({"big": 100.0, "a": 1.0, "b": 1.0, "c": 1.0}, 2)
        self.assertIn(["big"], bins)

    def test_unknown_unit_uses_default(self):
        self.assertEqual(ci_shard.weight_of("new", 10, {"old": 3.0}),
                         10 * ci_shard.DEFAULT_SECONDS_PER_TEST)
        self.assertEqual(ci_shard.weight_of("old", 10, {"old": 3.0}), 3.0)

    def test_bad_index_rejected(self):
        with self.assertRaises(ValueError):
            ci_shard.select_shard({}, 4, 4, {})


class RealDiscoveryTest(unittest.TestCase):
    """실제 tests/ discover 결과를 4조로 나눠 합이 전체와 같은지 본다."""

    @classmethod
    def setUpClass(cls):
        cls.units = ci_shard.group_units(ci_shard.discover())
        cls.timings = ci_shard.load_timings()

    def test_four_shards_cover_everything_exactly_once(self):
        all_ids = sorted(t.id() for v in self.units.values() for t in v)
        got = []
        for i in range(4):
            got += [t.id() for t in ci_shard.select_shard(self.units, i, 4, self.timings)]
        self.assertEqual(sorted(got), all_ids)
        # id 중복은 discover 자체에 있다(shim 재-export) - 다중집합 동등으로 누락·중복을 모두 잡는다.

    def test_modules_with_module_fixtures_stay_whole(self):
        for key, tests in self.units.items():
            mod = ci_shard._module_name(tests[0])
            module = sys.modules.get(mod)
            if module is not None and hasattr(module, "setUpModule"):
                self.assertEqual(key, mod)


if __name__ == "__main__":
    unittest.main()
