# -*- coding: utf-8 -*-
"""편집팀 가변 샤드 — 계획(plan_shards)·자가 완수 잠금(shard_claim)·N 샤드 합치기(merge_parts)."""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

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

    def test_T_is_exempt_from_the_deadline_once_body_shards_are_done(self):
        # 2026-11-20 감사: 본 샤드가 다 끝났는데 T 가 비어 있으면 08:56 에 DEADLINE → T 고아 → merge_parts 'tldr 없음' → 백업.
        self._done("delta", "A", "B")
        self.assertEqual(sc.exit_check(self.d, "delta", "델타-1+", until=self.until, now=self.until - 60),
                         (False, "CLAIM T"))
        self.assertEqual(sc.exit_check(self.d, "delta", "델타-1+", until=self.until, now=self.until + 60),
                         (False, "CLAIM T"))
        # 남이 T 를 잡고 살아 있으면 이어받을 수 없으니 끝내도 된다(그 워커가 쓴다).
        _w(sc._claim_path(self.d, "delta", "T"), {"worker": "델타-2", "at": self.until})
        self.assertEqual(sc.exit_check(self.d, "delta", "델타-1+", until=self.until, now=self.until + 60),
                         (True, "NO_TAKEOVER T"))
        self._done("delta", "T")
        self.assertEqual(sc.exit_check(self.d, "delta", "델타-1+", until=self.until, now=self.until + 60),
                         (True, "DONE"))

    def test_deadline_still_applies_while_body_shards_remain(self):
        self._done("delta", "A")
        self.assertEqual(sc.exit_check(self.d, "delta", "델타-1+", until=self.until, now=self.until + 60),
                         (True, "DEADLINE"))

    def test_wait_is_capped_under_the_bash_tool_timeout(self):
        self.assertLessEqual(sc.WAIT_MAX_SEC, 100)
        self._done("deep", *"ABCDEF")
        _w(sc._claim_path(self.d, "deep", "G"), {"worker": "심층-2", "at": 1000})
        ok, line = sc.exit_check(self.d, "deep", "심층-1+", now=1060)       # 죽음 판정까지 24분 남음
        self.assertEqual((ok, line), (False, f"WAIT G {sc.WAIT_MAX_SEC}"))

    def test_check_wait_sleeps_in_python_and_rechecks(self):
        from unittest import mock
        self._done("deep", *"ABCDEF")
        _w(sc._claim_path(self.d, "deep", "G"), {"worker": "심층-2", "at": 1e12})   # 먼 미래까지 살아 있는 점유
        slept = []

        def fake_sleep(sec):
            slept.append(sec)
            self._done("deep", "G")                       # 기다리는 사이 남이 끝냈다

        with mock.patch.object(sc, "_sleep", side_effect=fake_sleep):
            self.assertEqual(sc.main(["x", "check", self.d, "deep", "w", "--wait"]), 0)
        self.assertEqual(len(slept), 1)
        self.assertLessEqual(slept[0], sc.WAIT_MAX_SEC)
        # WAIT 가 아니면 기다리지 않는다
        os.remove(os.path.join(self.d, "deep.C.json"))
        with mock.patch.object(sc, "_sleep") as never:
            self.assertEqual(sc.main(["x", "check", self.d, "deep", "w", "--wait"]), 1)   # CLAIM C
            never.assert_not_called()

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

    def test_2026_11_20_audit_wording(self):
        for folder in ((), ("next",)):
            run = self._read(*folder, "RUN.md")
            self.assertIn("--until 08:55 --wait", run, folder)                   # 셸 sleep 없는 루프
            self.assertNotIn("그 초만큼(5분 이하) 기다렸다가", run, folder)
            self.assertNotIn("한 번에 5분 이하로", run, folder)
            rep = self._read(*folder, "reporter.md")
            self.assertNotIn("`shard_claim.py next` → `claim`", rep, folder)
            self.assertIn("--wait", rep, folder)
            ce = self._read(*folder, "copyedit.md")
            self.assertIn("python check_en_slots.py company/run/{run_date_kst}/brief_check.json", ce, folder)
            self.assertIn("`역할: 교열(2차)`", ce, folder)
            self.assertIn("1차 판정을 대신하지 않는다", ce, folder)
        switch = self._read("SWITCH_2026-11-23.md")
        self.assertNotIn("| 09:05 |", switch)
        self.assertIn("08:45 · 09:12", switch)

    def test_2026_11_23_precheck_wording(self):
        """사전 점검 C(교열 마감 뒤 브릿지 요청)·D(claim 실패 뒤 git 금지) — 두 판이 같은 문장."""
        import importlib.util as iu
        spec = iu.spec_from_file_location("cloud_request", os.path.join(ROOT, "company", "ops", "cloud_request.py"))
        cr = iu.module_from_spec(spec)
        spec.loader.exec_module(cr)
        self.assertEqual(cr.ACTIONS["delta-bridge"][0], "grm-delta-bridge.yml")   # 허용 목록에 있다
        req = '`{"id": "<id>", "action": "delta-bridge", "args": {"publish_date": "{run_date_kst}"}}`'
        line = ("`claim` 이 실패(exit 1)하면 그 샤드는 남의 것이다 — git 을 손대지 말고"
                "(claim 파일 수정·commit·push·rebase·reset 모두 하지 않는다) 곧바로 `check` 로 돌아간다.")
        for folder in ((), ("next",)):
            ce = self._read(*folder, "copyedit.md")
            self.assertIn("3. **브릿지 요청 — 2번까지 끝나면 곧바로 한 번.**", ce, folder)
            self.assertIn(req, ce, folder)
            self.assertIn("claude/request-{run_date_kst}-delta-bridge", ce, folder)
            self.assertIn("리허설은 이 단계를 하지 않는다(`REHEARSAL.md` 금지)", ce, folder)
            self.assertLess(ce.index("2. push 가 끝나면 **곧바로**"), ce.index("3. **브릿지 요청"))
            self.assertIn(line, self._read(*folder, "reporter.md"), folder)
        reh = self._read("REHEARSAL.md")
        self.assertIn("워크플로 실행 요청(`claude/request-*` 마커)", reh)          # 리허설 금지가 덮는다
        self.assertIn("**1번(실행 폴더 push)까지만.**", reh)
        # 요청 마커 형식이 실제 검증을 통과한다
        import datetime as dt
        today = dt.date(2026, 11, 23)
        obj = json.loads(req.strip("`").replace("<id>", "20261123-0905-bridge")
                         .replace("{run_date_kst}", "2026-11-23"))
        self.assertEqual(cr.validate(json.dumps(obj), "20261123-0905-bridge", today),
                         ("grm-delta-bridge.yml", "delta-bridge", {"publish_date": "2026-11-23"}))

    def test_switch_wording_kept(self):
        switch = self._read("SWITCH_2026-11-23.md")
        self.assertIn("08:45 · 09:12", switch)


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

    def _repo(self, base, names):
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
        out = []
        for n in names:
            p = os.path.join(base, n)
            subprocess.run(["git", "clone", "-q", "-b", "main", bare, p], check=True, capture_output=True)
            _git(p, "config", "user.email", "t@t")
            _git(p, "config", "user.name", n)
            out.append(p)
        return out

    @staticmethod
    def _overwrite_and_push(clone, worker, at):
        """다른 워커가 같은 claim 파일을 덮어써 push 한다(리허설 1120i deep.B 의 수정 커밋)."""
        _git(clone, "pull", "-q", "--rebase", "origin", "main")
        p = os.path.join(clone, "company", "run", "D", "claims", "deep.B.claim")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"worker": worker, "at": at, "kind": "deep", "shard": "B"}, f)
            f.write("\n")
        _git(clone, "add", "company/run/D/claims/deep.B.claim")
        _git(clone, "commit", "-q", "-m", f"claim deep.B by {worker}")
        _git(clone, "push", "-q", "origin", "HEAD:main")

    def test_push_success_is_rechecked_against_the_remote_file(self):
        base = tempfile.mkdtemp()
        try:
            w1, w2 = self._repo(base, ("w1", "w2"))
            real = sc._git
            fired = []

            def racing_git(root, *args, check=True):
                # w1 의 push 가 성공한 직후(원격 재확인 fetch 직전) w2 가 덮어쓴다
                if args[:1] == ("fetch",) and os.path.samefile(root, w1) and not fired:
                    fired.append(1)
                    self._overwrite_and_push(w2, "w2", 1001.5)
                return real(root, *args, check=check)

            with mock.patch.object(sc, "_git", side_effect=racing_git), \
                    redirect_stderr(io.StringIO()) as err:
                self.assertFalse(sc.claim(os.path.join(w1, "company", "run", "D"), "deep", "B", "w1", now=1000))
            self.assertTrue(fired)
            self.assertIn("claim 확인 실패", err.getvalue())
            # 덮어쓰기가 없으면 종전대로 이긴다
            self.assertTrue(sc.claim(os.path.join(w1, "company", "run", "D"), "deep", "C", "w1", now=1000))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_shared_folder_head_move_between_check_and_write_loses(self):
        """두 워커가 한 작업 폴더를 같이 쓰면 판정 뒤 남의 커밋이 HEAD 를 옮긴다 — 덮어쓰지 않고 진다."""
        base = tempfile.mkdtemp()
        try:
            (w1,) = self._repo(base, ("w1",))
            folder = os.path.join(w1, "company", "run", "D")
            real_state = sc._claim_state
            fired = []

            def state_then_other_commits(*a, **k):
                r = real_state(*a, **k)
                if not fired:
                    fired.append(1)
                    self._overwrite_and_push(w1, "심층-2", 999.0)   # 같은 폴더에서 남이 먼저 커밋·push
                return r

            with mock.patch.object(sc, "_claim_state", side_effect=state_then_other_commits):
                self.assertFalse(sc.claim(folder, "deep", "B", "심층-1+", now=1000))
            with open(os.path.join(folder, "claims", "deep.B.claim"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["worker"], "심층-2")
            shown = subprocess.run(["git", "-C", w1, "show", "origin/main:company/run/D/claims/deep.B.claim"],
                                   capture_output=True, text=True, encoding="utf-8", check=True).stdout
            self.assertEqual(json.loads(shown)["worker"], "심층-2")
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
