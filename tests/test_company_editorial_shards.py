# -*- coding: utf-8 -*-
"""편집팀 가변 샤드 — 계획(plan_shards)·자가 완수 잠금(shard_claim)·N 샤드 합치기(merge_parts)."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "company", "editorial", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = _load("plan_shards")
sc = _load("shard_claim")
mp = _load("merge_parts")


class PlanTest(unittest.TestCase):
    def test_139_cards_never_dropped(self):
        ids = [f"c{i}" for i in range(139)]
        deep = [{"id": f"d{i}", "obs": 2 if i % 3 else 3} for i in range(72)]
        shards, warn = ps.plan({"delta": ids, "deep": deep})
        got = [c for v in shards["delta"].values() for c in v]
        self.assertEqual(sorted(got), sorted(ids))
        sizes = [len(v) for v in shards["delta"].values()]
        self.assertLessEqual(max(sizes) - min(sizes), 1)
        self.assertLessEqual(len(shards["delta"]), 8)
        dgot = [c for v in shards["deep"].values() for c in v]
        self.assertEqual(sorted(dgot), sorted(x["id"] for x in deep))
        self.assertEqual(shards["tldr"], "T")

    def test_delta_keeps_handoff_order_in_contiguous_blocks(self):
        shards, _ = ps.plan({"delta": [f"c{i}" for i in range(10)], "deep": []})
        flat = [c for k in sorted(shards["delta"]) for c in shards["delta"][k]]
        self.assertEqual(flat, [f"c{i}" for i in range(10)])

    def test_small_week_keeps_two_shards(self):
        shards, _ = ps.plan({"delta": ["a", "b", "c"], "deep": []})
        self.assertEqual(sorted(shards["delta"]), ["A", "B"])
        self.assertEqual(sorted(shards["deep"]), ["A", "B"])

    def test_cap_exceeded_warns_but_keeps_all(self):
        ids = [f"c{i}" for i in range(400)]
        shards, warn = ps.plan({"delta": ids, "deep": []}, max_n=8)
        self.assertEqual(sum(len(v) for v in shards["delta"].values()), 400)
        self.assertTrue(warn)

    def test_deep_obs_balanced_and_capped(self):
        items = [{"id": f"d{i}", "obs": 10} for i in range(9)]
        shards, _ = ps.plan({"delta": [], "deep": items}, deep_obs=30)
        obs = [10 * len(v) for v in shards["deep"].values()]
        self.assertLessEqual(max(obs), 30)

    def test_duplicate_card_rejected(self):
        with self.assertRaises(ValueError):
            ps.plan({"delta": ["a", "a"], "deep": []})


def _w(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


EN = {"en": {"title_issue": "t", "summary": "s", "implication": "i", "key_facts": ["k"], "checks": ["c"]}}
PLAN = {"delta": {"A": ["c1"], "B": ["c2"], "C": ["c3"]}, "deep": {"A": ["d1"], "B": []}, "tldr": "T"}


class NextShardTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        _w(os.path.join(self.d, "desk.json"), {"status": "완료", "shards": PLAN})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_lowest_free_first_then_skip_done_and_held(self):
        self.assertEqual(sc.next_shard(self.d, "delta", now=1000), "A")
        _w(os.path.join(self.d, "delta.A.json"), {"status": "완료"})
        _w(sc._claim_path(self.d, "delta", "B"), {"worker": "w1", "at": 1000})
        self.assertEqual(sc.next_shard(self.d, "delta", now=1000 + 60), "C")

    def test_stale_claim_can_be_taken_over(self):
        _w(sc._claim_path(self.d, "delta", "A"), {"worker": "dead", "at": 0})
        self.assertEqual(sc.next_shard(self.d, "delta", now=sc.STALE_MIN * 60 + 1), "A")

    def test_tldr_only_after_all_delta_done(self):
        for s in "ABC":
            _w(os.path.join(self.d, f"delta.{s}.json"), {"status": "완료"})
        self.assertEqual(sc.next_shard(self.d, "delta", now=1), "T")
        _w(os.path.join(self.d, "delta.T.json"), {"status": "완료"})
        self.assertIsNone(sc.next_shard(self.d, "delta", now=1))

    def test_deep_empty_shard_counts_as_done(self):
        _w(os.path.join(self.d, "deep.A.json"), {"status": "완료"})
        self.assertIsNone(sc.next_shard(self.d, "deep", now=1))

    def test_partial_status_does_not_count_as_done(self):
        _w(os.path.join(self.d, "delta.A.json"), {"status": "보류"})
        self.assertEqual(sc.next_shard(self.d, "delta", now=1), "A")


def _git(cwd, *a):
    subprocess.run(["git", "-C", cwd, *a], check=True, capture_output=True, text=True, encoding="utf-8")


class ClaimRaceTest(unittest.TestCase):
    def test_only_one_of_two_workers_wins(self):
        base = tempfile.mkdtemp()
        try:
            bare = os.path.join(base, "r.git")
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
            seed = os.path.join(base, "seed")
            subprocess.run(["git", "clone", "-q", bare, seed], check=True, capture_output=True)
            _git(seed, "checkout", "-q", "-b", "main")
            _git(seed, "config", "user.email", "t@t")
            _git(seed, "config", "user.name", "t")
            _w(os.path.join(seed, "company", "run", "D", "desk.json"), {"status": "완료", "shards": PLAN})
            _git(seed, "add", "-A")
            _git(seed, "commit", "-q", "-m", "seed")
            _git(seed, "push", "-q", "origin", "HEAD:main")
            wk = []
            for n in ("w1", "w2"):
                p = os.path.join(base, n)
                subprocess.run(["git", "clone", "-q", "-b", "main", bare, p], check=True, capture_output=True)
                _git(p, "config", "user.email", "t@t")
                _git(p, "config", "user.name", n)
                wk.append(p)
            f1 = os.path.join(wk[0], "company", "run", "D")
            f2 = os.path.join(wk[1], "company", "run", "D")
            self.assertTrue(sc.claim(f1, "delta", "A", "w1", now=1000))
            self.assertFalse(sc.claim(f2, "delta", "A", "w2", now=1000 + 30))
            self.assertTrue(sc.claim(f2, "delta", "B", "w2", now=1000 + 30))
            self.assertTrue(sc.claim(f2, "delta", "A", "w2", now=1000 + sc.STALE_MIN * 60 + 5))
        finally:
            shutil.rmtree(base, ignore_errors=True)


class MergeNTest(unittest.TestCase):
    def _folder(self, plan, files):
        d = tempfile.mkdtemp()
        _w(os.path.join(d, "desk.json"), {"status": "완료", "shards": plan})
        for k, v in files.items():
            _w(os.path.join(d, k), v)
        return d

    def test_five_delta_shards_and_T(self):
        plan = {"delta": {s: [f"c{s}"] for s in "ABCDE"}, "deep": {"A": [], "B": []}, "tldr": "T"}
        files = {f"delta.{s}.json": {"status": "완료", "cards": {f"c{s}": EN}} for s in "ABCDE"}
        files["delta.T.json"] = {"status": "완료", "tldr": ["a", "b", "c"], "tldr_en": ["a", "b", "c"]}
        delta, deep, problems = mp.merge(self._folder(plan, files))
        self.assertEqual(problems, [])
        self.assertEqual(sorted(delta["cards"]), [f"c{s}" for s in "ABCDE"])

    def test_missing_T_blocks(self):
        plan = {"delta": {"A": ["c1"], "B": ["c2"]}, "deep": {"A": [], "B": []}, "tldr": "T"}
        files = {"delta.A.json": {"status": "완료", "cards": {"c1": EN}},
                 "delta.B.json": {"status": "완료", "cards": {"c2": EN}}}
        self.assertTrue(any("tldr" in p for p in mp.merge(self._folder(plan, files))[2]))

    def test_unfinished_extra_shard_blocks(self):
        plan = {"delta": {"A": ["c1"], "B": ["c2"], "C": ["c3"]}, "deep": {"A": [], "B": []}, "tldr": "T"}
        files = {"delta.A.json": {"status": "완료", "cards": {"c1": EN}},
                 "delta.B.json": {"status": "완료", "cards": {"c2": EN}},
                 "delta.T.json": {"status": "완료", "tldr": ["x"], "tldr_en": ["x"]}}
        self.assertTrue(any("delta.C.json" in p for p in mp.merge(self._folder(plan, files))[2]))

    def test_en_gap_still_blocks_with_n_shards(self):
        plan = {"delta": {"A": ["c1"], "B": ["c2"], "C": ["c3"]}, "deep": {"A": [], "B": []}, "tldr": "T"}
        files = {"delta.A.json": {"status": "완료", "cards": {"c1": EN}},
                 "delta.B.json": {"status": "완료", "cards": {"c2": EN}},
                 "delta.C.json": {"status": "완료", "cards": {"c3": {}}},
                 "delta.T.json": {"status": "완료", "tldr": ["x"], "tldr_en": ["x"]}}
        self.assertTrue(any("영문 슬롯" in p for p in mp.merge(self._folder(plan, files))[2]))


if __name__ == "__main__":
    unittest.main()
