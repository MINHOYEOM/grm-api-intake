"""ci_shard.py 회귀 가드 - 4개 조의 합이 전체 discover 와 정확히 같아야 한다(누락·중복 0)."""
import sys
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ci_shard  # noqa: E402


def sample_test(module, name):
    return type(name, (unittest.TestCase,), {"__module__": module})()


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

    def test_unknown_file_uses_mean_file_total(self):
        tests = [sample_test("test_first", "A"), sample_test("test_first", "A"),
                 sample_test("test_first", "B"), sample_test("test_second", "C"),
                 sample_test("test_new", "D")]
        timings = {"test_first.A": 2.0, "test_first.B": 6.0, "test_second.C": 4.0,
                   "test_removed.E": 100.0}
        weights = ci_shard.shard_weights(ci_shard.group_units(tests), timings)
        self.assertEqual(weights, {"test_first": 8.0, "test_second": 4.0, "test_new": 6.0})

    def test_unknown_render_class_uses_mean_render_class_seconds(self):
        tests = [sample_test("test_render", name) for name in ("A", "B", "C")]
        tests.append(sample_test("test_other", "D"))
        timings = {"test_render.A": 10.0, "test_render.B": 30.0, "test_other.D": 1.0}
        weights = ci_shard.shard_weights(ci_shard.group_units(tests), timings)
        self.assertEqual(weights["test_render.C"], 20.0)

    def test_no_timings_uses_default_per_test(self):
        tests = [sample_test("test_new", "A"), sample_test("test_new", "B")]
        weights = ci_shard.shard_weights(ci_shard.group_units(tests), {})
        self.assertEqual(weights["test_new"], 2 * ci_shard.DEFAULT_SECONDS_PER_TEST)

    def test_zero_second_units_are_spread_across_shards(self):
        bins = ci_shard.partition(dict.fromkeys("abcd", 0.0), 4)
        self.assertEqual(bins, [["a"], ["b"], ["c"], ["d"]])

    def test_bad_index_rejected(self):
        with self.assertRaises(ValueError):
            ci_shard.select_shard({}, 4, 4, {})


class RealDiscoveryTest(unittest.TestCase):
    """실제 tests/ discover 결과를 4조로 나눠 합이 전체와 같은지 본다."""

    @classmethod
    def setUpClass(cls):
        cls.discovered = ci_shard.discover()
        cls.units = ci_shard.group_units(cls.discovered)
        cls.timings = ci_shard.load_timings()
        cls.shards = [ci_shard.select_shard(cls.units, i, 4, cls.timings) for i in range(4)]

    def test_four_shards_cover_everything_exactly_once(self):
        # shim 재-export로 같은 테스트 ID가 나올 수 있어 인스턴스별로 비교한다.
        self.assertCountEqual(
            (id(t) for shard in self.shards for t in shard),
            (id(t) for t in self.discovered),
        )

    def test_each_file_stays_in_one_shard_except_web_render(self):
        file_shards = defaultdict(set)
        for i, shard in enumerate(self.shards):
            for test in shard:
                file_shards[ci_shard._module_name(test)].add(i)
        self.assertEqual(len(file_shards.pop("test_render")), 4)
        for module, shards in file_shards.items():
            self.assertEqual(len(shards), 1, module)

    def test_estimated_shard_spread_is_at_most_30_seconds(self):
        self.assertTrue(self.timings)
        weights = ci_shard.shard_weights(self.units, self.timings)
        loads = [sum(weights[key] for key in ci_shard.group_units(shard))
                 for shard in self.shards]
        self.assertLessEqual(max(loads) - min(loads), 30, loads)

    def test_modules_with_module_fixtures_stay_whole(self):
        for key, tests in self.units.items():
            mod = ci_shard._module_name(tests[0])
            module = sys.modules.get(mod)
            if module is not None and (hasattr(module, "setUpModule")
                                       or hasattr(module, "tearDownModule")):
                self.assertEqual(key, mod)


if __name__ == "__main__":
    unittest.main()
