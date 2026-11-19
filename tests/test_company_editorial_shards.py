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


class DeepCharsPlanTest(unittest.TestCase):
    """2026-11-19 리허설 1120g: 카드·관찰 한도 안인데 원문이 샤드당 5~13만 자 — 원문 글자 수로도 나눈다."""

    def test_chars_cap_splits_heavy_shards(self):
        items = [{"id": f"d{i}", "obs": 3, "chars": 20000} for i in range(20)]   # 40만 자 · 관찰 60 · 20장
        shards, warn = ps.plan({"delta": [], "deep": items}, deep_chars=60000)
        self.assertEqual(len(shards["deep"]), 7)                                  # ceil(400000/60000)
        per = {k: 20000 * len(v) for k, v in shards["deep"].items()}
        self.assertLessEqual(max(per.values()), 60000)
        self.assertEqual(sorted(c for v in shards["deep"].values() for c in v), sorted(x["id"] for x in items))
        self.assertEqual(warn, [])

    def test_oversize_single_card_gets_its_own_shard_and_a_warning(self):
        items = [{"id": "big", "obs": 17, "chars": 75000}] + [{"id": f"d{i}", "obs": 1, "chars": 5000} for i in range(10)]
        shards, warn = ps.plan({"delta": [], "deep": items}, deep_chars=60000)
        self.assertIn(["big"], list(shards["deep"].values()))
        self.assertTrue(any("big" in w for w in warn))

    def test_handoff_fills_chars_from_deep_analysis_input(self):
        handoff = {"rows": [{"web_card_id": "a", "deep_analysis_input": {"body_full": "x" * 50000}},
                            {"web_card_id": "b", "deep_analysis_input": {"body_full": "y" * 50000}},
                            {"web_card_id": "c", "deep_analysis_input": {"body_full": "z" * 50000}},
                            {"card_id": "no-web-id"}]}
        self.assertEqual(ps.handoff_chars(handoff), {"a": 50000, "b": 50000, "c": 50000})
        items = [{"id": k, "obs": 0} for k in "abc"]
        self.assertEqual(len(ps.plan({"delta": [], "deep": items})[0]["deep"]), 2)        # 글자 수 모르면 종전
        self.assertEqual(len(ps.plan({"delta": [], "deep": items}, handoff=handoff)[0]["deep"]), 3)

    def test_small_text_cards_do_not_pile_past_the_card_cap(self):
        items = [{"id": "big", "chars": 59000}] + [{"id": f"s{i}", "chars": 10} for i in range(23)]
        shards, _ = ps.plan({"delta": [], "deep": items}, deep_chars=60000, deep_cards=12)
        self.assertLessEqual(max(len(v) for v in shards["deep"].values()), 12)

    def test_deep_max_is_separate_from_delta_max(self):
        items = [{"id": f"d{i}", "chars": 60000} for i in range(11)]
        shards, warn = ps.plan({"delta": [f"c{i}" for i in range(90)], "deep": items})
        self.assertEqual(len(shards["deep"]), 11)
        self.assertEqual(len(shards["delta"]), 5)
        self.assertEqual(warn, [])


class ExitCheckTest(unittest.TestCase):
    """워커는 check 가 exit 0 일 때만 끝낸다(RUN.md 7항)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        plan = {"delta": {"A": ["c1"], "B": ["c2"]},
                "deep": {s: [f"d{s}"] for s in "ABCDEFG"}, "tldr": "T"}
        _w(os.path.join(self.d, "desk.json"), {"status": "완료", "shards": plan})
        self.until = sc.until_ts("08:55", 1_763_500_000)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _done(self, kind, *names):
        for s in names:
            _w(os.path.join(self.d, f"{kind}.{s}.json"), {"status": "완료"})

    def test_1120g_deep_worker_may_not_quit_with_empty_shards(self):
        self._done("deep", "A", "B")
        ok, line = sc.exit_check(self.d, "deep", "심층-1+", now=1000)
        self.assertFalse(ok)
        self.assertEqual(line, "CLAIM C")

    def test_all_done_exits_at_once(self):
        self._done("deep", *"ABCDEFG")
        self.assertEqual(sc.exit_check(self.d, "deep", "w", until=self.until, now=self.until - 3600), (True, "DONE"))

    def test_boost_waits_for_a_held_shard_to_go_stale(self):
        self._done("deep", *"ABCDEF")
        now = self.until - 15 * 60                      # 08:40
        at = now - 10 * 60                              # 08:30 점유
        _w(sc._claim_path(self.d, "deep", "G"), {"worker": "심층-2", "at": at})
        ok, line = sc.exit_check(self.d, "deep", "심층-1+", until=self.until, now=now)
        self.assertFalse(ok)
        self.assertTrue(line.startswith("WAIT G "), line)
        self.assertLessEqual(int(line.split()[2]), sc.WAIT_MAX_SEC)
        # 08:55 에 점유 25분이 지나 죽은 것으로 판정 → 이어받는다
        self.assertEqual(sc.exit_check(self.d, "deep", "심층-1+", until=None, now=at + sc.STALE_MIN * 60 + 1),
                         (False, "CLAIM G"))

    def test_no_takeover_possible_before_deadline_exits(self):
        self._done("deep", *"ABCDEF")
        now = self.until - 15 * 60
        _w(sc._claim_path(self.d, "deep", "G"), {"worker": "심층-2", "at": now - 5 * 60})    # 08:35 점유 → 09:00 에야 죽음 판정
        self.assertEqual(sc.exit_check(self.d, "deep", "심층-1+", until=self.until, now=now), (True, "NO_TAKEOVER G"))
        # 리허설(시한 없음)은 끝내지 않고 기다린다
        self.assertFalse(sc.exit_check(self.d, "deep", "심층-1+", until=None, now=now)[0])

    def test_deadline_passed_exits(self):
        self.assertEqual(sc.exit_check(self.d, "deep", "w", until=self.until, now=self.until + 1), (True, "DEADLINE"))

    def test_my_own_unfinished_claim_is_still_mine(self):
        self._done("deep", *"ABCDEF")
        _w(sc._claim_path(self.d, "deep", "G"), {"worker": "심층-1", "at": 1000})
        self.assertEqual(sc.exit_check(self.d, "deep", "심층-1", now=1060), (False, "CLAIM G"))

    def test_delta_waits_while_body_held_then_takes_T(self):
        self._done("delta", "A")
        _w(sc._claim_path(self.d, "delta", "B"), {"worker": "델타-2", "at": 1000})
        ok, line = sc.exit_check(self.d, "delta", "델타-1", now=1060)
        self.assertFalse(ok)
        self.assertTrue(line.startswith("WAIT B "), line)
        self._done("delta", "B")
        self.assertEqual(sc.exit_check(self.d, "delta", "델타-1", now=1120), (False, "CLAIM T"))

    def test_until_is_kst_wall_clock(self):
        import datetime
        t = sc.until_ts("08:55", 1_763_500_000)
        k = datetime.datetime.fromtimestamp(t, sc.KST)
        self.assertEqual((k.hour, k.minute), (8, 55))

    def test_cli_exit_code(self):
        self._done("deep", *"ABCDEFG")
        self.assertEqual(sc.main(["x", "check", self.d, "deep", "w", "--until", "08:55"]), 0)
        os.remove(os.path.join(self.d, "deep.C.json"))
        self.assertEqual(sc.main(["x", "check", self.d, "deep", "w"]), 1)


class WorkerLoopInstructionTest(unittest.TestCase):
    """실발행(company/editorial)·다음 판(next/) 지시문이 같은 종료 규칙을 쓴다."""

    def _read(self, *parts):
        with open(os.path.join(ROOT, "company", "editorial", *parts), encoding="utf-8") as f:
            return f.read()

    def test_loop_ends_only_on_check_exit_0(self):
        for folder in ((), ("next",)):
            run = self._read(*folder, "RUN.md")
            self.assertIn("shard_claim.py check company/run/{D} <delta|deep> <워커이름> --until 08:55", run, folder)
            self.assertIn("끝내는 조건은 `check` 의 exit 0 하나뿐이다", run, folder)
            self.assertNotIn("NONE 이거나 08:55 KST 를 넘었으면 종료", run, folder)
            self.assertNotIn("할 일이 없으면 바로 종료", run, folder)
            self.assertIn("--handoff _handoff/handoff_{D}.json", run, folder)
            self.assertIn("shard_claim.py check` 가 exit 0 을 내기 전에는 끝내지 않는다", self._read(*folder, "reporter.md"), folder)
            self.assertIn("plan_shards.py <입력.json> --handoff", self._read(*folder, "desk.md"), folder)
        self.assertIn("`--until 08:55` 를 빼고", self._read("REHEARSAL.md"))


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

    def test_desk_lock_07_20_and_07_45(self):
        """데스크 두 슬롯이 같은 handoff 를 동시에 보면 한 쪽만 일한다(RUN.md 5-2)."""
        base = tempfile.mkdtemp()
        try:
            bare = os.path.join(base, "r.git")
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
            seed = os.path.join(base, "seed")
            subprocess.run(["git", "clone", "-q", bare, seed], check=True, capture_output=True)
            _git(seed, "checkout", "-q", "-b", "main")
            _git(seed, "config", "user.email", "t@t")
            _git(seed, "config", "user.name", "t")
            _w(os.path.join(seed, "company", "run", "D", "README.txt"), {})
            _git(seed, "add", "-A")
            _git(seed, "commit", "-q", "-m", "seed")
            _git(seed, "push", "-q", "origin", "HEAD:main")
            wk = []
            for n in ("d1", "d2"):
                p = os.path.join(base, n)
                subprocess.run(["git", "clone", "-q", "-b", "main", bare, p], check=True, capture_output=True)
                _git(p, "config", "user.email", "t@t")
                _git(p, "config", "user.name", n)
                wk.append(p)
            f1 = os.path.join(wk[0], "company", "run", "D")
            f2 = os.path.join(wk[1], "company", "run", "D")
            self.assertTrue(sc.claim(f1, "desk", "desk", "데스크 07:20", now=1000))
            self.assertTrue(os.path.exists(os.path.join(f1, "claims", "desk.claim")))
            self.assertFalse(sc.claim(f2, "desk", "desk", "데스크 07:45", now=1000 + 60))
            # 앞 데스크가 죽었으면(25분 넘게 desk.json 없음) 뒤 데스크가 이어받는다
            self.assertTrue(sc.claim(f2, "desk", "desk", "데스크 07:45", now=1000 + sc.STALE_MIN * 60 + 5))
            # desk.json 이 완료면 아무도 다시 잡지 않는다
            _w(os.path.join(f2, "desk.json"), {"status": "완료", "shards": PLAN})
            self.assertFalse(sc.claim(f2, "desk", "desk", "데스크 07:45", now=1000 + 9999))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_desk_state_command(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(sc._claim_state(d, "desk", "desk", 1)[0], "free")
            _w(os.path.join(d, "claims", "desk.claim"), {"worker": "x", "at": 0})
            self.assertEqual(sc._claim_state(d, "desk", "desk", 60)[0], "held")
            self.assertFalse(sc._result_done(d, "desk", "desk"))
            _w(os.path.join(d, "desk.json"), {"status": "보류"})
            self.assertFalse(sc._result_done(d, "desk", "desk"))
            _w(os.path.join(d, "desk.json"), {"status": "완료"})
            self.assertTrue(sc._result_done(d, "desk", "desk"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


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
