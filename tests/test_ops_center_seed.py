#!/usr/bin/env python3
"""124_ops_center_seed.sql — 운영 센터 시드 오프라인 시험(PR2).

DB 없이 시드 파일의 VALUES 행을 글자로 읽어 검사한다:
  ① 칸 14(실 2 · 부문 3 · 팀 9)·팀원·목표 수, slug 유일·형식, 부모 규칙(팀은 부문 아래, 실·부문은 최상위)
  ② 122 의 제약(길이·열거값·선 순서)을 시드가 하나도 어기지 않음
  ③ 거짓 초록 방지: 값 없는 목표 목록 고정, 값이 있으면 측정 시각도 있음, 측정 시각은 미래가 아님
  ④ 123 의 상태 규칙을 파이썬으로 옮겨 부록 C 의 '예상 팀 상태'와 대조
  ⑤ 멱등: 넣는 문장마다 on conflict (slug) do update, DDL·삭제 없음, 되돌리기 slug 목록이 시드와 같음
행동(실제 두 번 적용·ops_home 결과)은 격리 PostgreSQL 에서 tests/sql/ops_center_seed.sql 이 본다
(CI: grm-ops-center-sql-check.yml).
"""
from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MIG = _ROOT / "web" / "migrations"
_SEED = _MIG / "124_ops_center_seed.sql"
_KST = timezone(timedelta(hours=9))
_NOW = datetime(2026, 11, 6, 18, 0, tzinfo=_KST)   # 시드 작성일 저녁(부록 C 의 '오늘')

_TOKEN = re.compile(r"'(?:[^']|'')*'|null|true|false|-?\d+(?:\.\d+)?", re.I)


def _lit(tok: str):
    low = tok.lower()
    if low == "null":
        return None
    if low in ("true", "false"):
        return low == "true"
    if tok.startswith("'"):
        return tok[1:-1].replace("''", "'")
    return float(tok) if "." in tok else int(tok)


def _rows(sql: str, block: str) -> list[tuple]:
    m = re.search(rf"-- seed:{block} begin\n(.*?)-- seed:{block} end", sql, re.S)
    if not m:
        raise AssertionError(f"seed block {block} not found")
    body = m.group(1)
    vals = body.split("from (values", 1)[1].split(") v(", 1)[0]
    rows = []
    for line in vals.splitlines():
        line = line.strip()
        if not line.startswith("("):
            continue
        inner = line.rstrip(",")
        assert inner.endswith(")"), line
        rows.append(tuple(_lit(t) for t in _TOKEN.findall(inner[1:-1])))
    return rows


def _ts(s):
    return None if s is None else datetime.fromisoformat(s.replace(" ", "T"))


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def goal_state(d, target, warn, bad, val, measured, period, now):
    """123 private.ops_goal_state 를 그대로 옮긴 것(주의선이 비면 목표선이 주의선)."""
    if target is None or val is None or measured is None:
        return "unknown"
    if now - measured > timedelta(hours=period * 3 // 2):
        return "unknown"
    if bad is not None and ((d == "gte" and val < bad) or (d == "lte" and val > bad)):
        return "bad"
    w = target if warn is None else warn
    if (d == "gte" and val < w) or (d == "lte" and val > w):
        return "warn"
    return "ok"


_RANK = {"bad": 4, "warn": 3, "unknown": 2, "ok": 1}


class SeedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _SEED.read_bytes()
        cls.sql = cls.raw.decode("utf-8")
        cls.top = _rows(cls.sql, "top")          # slug, name, kind, ord, blurb
        cls.teams = _rows(cls.sql, "teams")      # slug, name, parent, ord, pilot, blurb
        cls.members = _rows(cls.sql, "members")  # team, slug, name, kind, ref, model, exempt, last, note
        cls.goals = _rows(cls.sql, "goals")      # team, slug, label, unit, dir, t, w, b, val, measured, period, mkind, ord, note
        cls.kind = {r[0]: r[2] for r in cls.top} | {r[0]: "team" for r in cls.teams}

    # ① 모양 ------------------------------------------------------------------
    def test_file_number_unique_and_lf(self):
        self.assertEqual(sorted(p.name for p in _MIG.glob("124_*.sql")), [_SEED.name])
        self.assertNotIn(b"\r\n", self.raw)
        self.assertTrue(self.sql.startswith("-- ====="))

    def test_fourteen_cells(self):
        kinds = [r[2] for r in self.top]
        self.assertEqual(kinds.count("office"), 2)
        self.assertEqual(kinds.count("division"), 3)
        self.assertEqual(len(self.teams), 9)
        self.assertEqual(len(self.kind), 14)
        self.assertEqual([r[0] for r in self.top],
                         ["situation-room", "verification", "research", "platform", "customer"])

    def test_counts(self):
        self.assertEqual(len(self.members), 15)
        self.assertEqual(len(self.goals), 23)

    def test_slugs_unique_and_valid(self):
        for name, slugs, pat in (
            ("team", [r[0] for r in self.top] + [r[0] for r in self.teams], r"^[a-z0-9-]{2,30}$"),
            ("member", [r[1] for r in self.members], r"^[a-z0-9-]{2,40}$"),
            ("goal", [r[1] for r in self.goals], r"^[a-z0-9-]{2,40}$"),
        ):
            self.assertEqual(len(slugs), len(set(slugs)), f"{name} slug 중복")
            for s in slugs:
                self.assertRegex(s, pat)

    def test_parent_rule(self):
        # 실·부문은 상위 없음(insert 가 parent_id 를 null 로 고정), 팀은 부문 아래
        self.assertIn("select v.slug, v.name, v.kind, null, v.ord", self.sql)
        for slug, _n, parent, *_ in self.teams:
            self.assertEqual(self.kind.get(parent), "division", slug)
        self.assertIn("join public.ops_team p on p.slug = v.parent and p.kind = 'division'", self.sql)
        for d in ("research", "platform", "customer"):
            self.assertTrue(any(r[2] == d for r in self.teams), f"{d} 아래 팀 없음")

    def test_members_and_goals_hang_on_stateful_cells(self):
        # 부문 칸은 상태를 직접 갖지 않는다 — 팀원·목표는 실 또는 팀에만
        for r in self.members:
            self.assertIn(self.kind.get(r[0]), ("office", "team"), r[1])
        for r in self.goals:
            self.assertIn(self.kind.get(r[0]), ("office", "team"), r[1])
        stateful = {s for s, k in self.kind.items() if k in ("office", "team")}
        self.assertEqual(stateful, {r[0] for r in self.goals}, "목표 없는 칸이 있다")

    # ② 122 제약 ---------------------------------------------------------------
    def test_team_columns(self):
        for r in self.top:
            self.assertTrue(1 <= len(r[1]) <= 30)
            self.assertTrue(r[4] and len(r[4]) <= 60, r[0])
        for slug, name, _p, _o, pilot, blurb in self.teams:
            self.assertTrue(1 <= len(name) <= 30)
            self.assertTrue(blurb and len(blurb) <= 60, slug)
            self.assertEqual(pilot, slug == "strategy")

    def test_member_columns(self):
        for team, slug, name, kind, ref, model, exempt, last, note in self.members:
            self.assertTrue(1 <= len(name) <= 40, slug)
            self.assertIn(kind, ("routine", "session", "code"))
            self.assertTrue(ref is None or len(ref) <= 120)
            self.assertTrue(model is None or len(model) <= 20)
            self.assertIsInstance(exempt, bool)
            self.assertTrue(note is None or len(note) <= 80)
            self.assertEqual(last is None, note is None, f"{slug}: 활동 시각과 메모는 같이")
            if last is not None:
                self.assertLessEqual(_ts(last), _NOW, f"{slug}: 미래 활동")

    def test_idle_exempt_only_on_call_members(self):
        """D6: 호출형(개발 세션·시범 세션)만 놀고 있음 판정에서 뺀다. 정기 작업·코드는 빼지 않는다."""
        exempt = {r[1] for r in self.members if r[6]}
        self.assertEqual(exempt, {"dev-session", "strat-card-history"})
        for r in self.members:
            if r[0] == "dev":
                self.assertTrue(r[6], r[1])

    def test_member_names_are_roles(self):
        for r in self.members:
            self.assertRegex(r[2], r"(담당|팀장|기자)$", f"{r[1]}: 직무 이름이 아니다")

    def test_goal_columns_and_line_order(self):
        for (team, slug, label, unit, d, t, w, b, val, measured, period, mkind, ord_, note) in self.goals:
            self.assertTrue(1 <= len(label) <= 60, slug)
            self.assertLessEqual(len(unit), 8)
            self.assertIn(d, ("gte", "lte"))
            self.assertIn(period, (24, 168, 744))
            self.assertIn(mkind, ("sql", "github", "manual"))
            self.assertTrue(note and len(note) <= 120, slug)
            pairs = [(b, w), (w, t), (b, t)] if d == "gte" else [(t, w), (w, b), (t, b)]
            for lo, hi in pairs:
                if lo is not None and hi is not None:
                    self.assertLessEqual(lo, hi, f"{slug}: 선 순서 위반")

    # ③ 거짓 초록 방지 ---------------------------------------------------------
    def test_values_have_real_dates(self):
        for r in self.goals:
            slug, val, measured = r[1], r[8], r[9]
            self.assertEqual(val is None, measured is None, f"{slug}: 값과 측정 시각은 같이")
            if measured is not None:
                self.assertLessEqual(_ts(measured), _NOW, f"{slug}: 미래 측정")
        self.assertNotIn("now()", _code(self.sql), "측정 시각은 now() 가 아니라 실제 측정일")

    def test_unmeasured_goals_stay_null(self):
        """부록 C-2 의 '—'(값 없음)은 값을 지어 넣지 않는다. 이 목록이 바뀌면 출처가 있어야 한다."""
        nulls = sorted(r[1] for r in self.goals if r[8] is None)
        self.assertEqual(nulls, sorted([
            "sr-ontime", "sr-report", "tr-backlog", "edu-terms-week", "infra-ingest-age",
            "infra-deploy-fail", "pr-linkedin", "pr-subs", "growth-channel", "growth-brief-click",
        ]))
        undecided = sorted(r[1] for r in self.goals if r[5] is None)
        self.assertEqual(undecided, ["growth-channel", "pr-subs"])

    def test_no_bad_line_on_streak(self):
        g = {r[1]: r for r in self.goals}
        self.assertIsNone(g["dev-link-streak"][7], "연속 성공은 한 번 실패로 빨강이 되면 안 된다(D3)")

    # ④ 부록 C 예상 상태 --------------------------------------------------------
    def _team_states(self, now):
        states = {}
        for slug, k in self.kind.items():
            if k == "division":
                continue
            gs = [goal_state(r[4], r[5], r[6], r[7], r[8], _ts(r[9]), r[10], now)
                  for r in self.goals if r[0] == slug]
            states[slug] = max(gs, key=_RANK.get) if gs else "unknown"
        for slug, k in self.kind.items():
            if k == "division":
                kids = [states[r[0]] for r in self.teams if r[2] == slug]
                states[slug] = max(kids, key=_RANK.get) if kids else "unknown"
        return states

    def test_expected_states_match_appendix_c(self):
        s = self._team_states(_NOW)
        self.assertEqual(s, {
            "situation-room": "unknown", "verification": "ok",
            "brief": "ok", "translation": "warn", "education": "unknown",
            "strategy": "ok", "dev": "warn", "quality": "warn", "infra": "unknown",
            "outreach": "unknown", "growth": "unknown",
            "research": "warn", "platform": "warn", "customer": "unknown",
        })

    def test_seed_values_age_into_unknown(self):
        """오래 안 고치면 전부 '측정 필요'로 내려간다 — 시드 값이 초록으로 남지 않는다."""
        s = self._team_states(datetime(2027, 1, 1, tzinfo=_KST))
        self.assertEqual(set(s.values()), {"unknown"})

    # ⑤ 멱등·범위 --------------------------------------------------------------
    def test_every_insert_is_idempotent(self):
        code = _code(self.sql).lower()
        inserts = re.findall(r"insert into public\.(ops_\w+)", code)
        self.assertEqual(inserts, ["ops_team", "ops_team", "ops_member", "ops_goal"])
        self.assertEqual(code.count("on conflict (slug) do update set"), len(inserts))

    def test_no_ddl_or_deletes(self):
        code = _code(self.sql).lower()
        for bad in ("create ", "drop ", "alter ", "truncate", "delete from", "grant ", "revoke ",
                    "ops_task", "ops_decision", "ops_reports"):
            self.assertNotIn(bad, code, bad)

    def test_measured_values_not_rolled_back(self):
        """적용 뒤 새로 잰 값을 시드 재실행이 옛 값으로 되돌리지 않는다."""
        code = " ".join(_code(self.sql).split())
        self.assertIn("ops_goal.measured_at <= excluded.measured_at", code)
        self.assertIn("ops_member.last_active_at <= excluded.last_active_at", code)
        tail = code.split("insert into public.ops_goal", 1)[1].split("on conflict", 1)[1]
        self.assertNotIn("current_value = excluded.current_value", tail)

    def test_rollback_comment_lists_every_seed_slug(self):
        head = self.sql.split("-- seed:top begin", 1)[0]
        rb = head.split("되돌리기", 1)[1]
        listed = set(re.findall(r"'([a-z0-9-]+)'", rb))
        seeded = ({r[0] for r in self.top} | {r[0] for r in self.teams}
                  | {r[1] for r in self.members} | {r[1] for r in self.goals})
        self.assertEqual(listed - {"team"}, seeded)

    def test_workflow_runs_seed_sql(self):
        wf = (_ROOT / ".github" / "workflows" / "grm-ops-center-sql-check.yml").read_text(encoding="utf-8")
        self.assertIn("-f tests/sql/ops_center_seed.sql", wf)
        self.assertIn("tests/sql/ops_center_seed.sql", wf.split("jobs:", 1)[0])
        sql = (_ROOT / "tests" / "sql" / "ops_center_seed.sql").read_text(encoding="utf-8")
        self.assertEqual(sql.count("\\ir ../../web/migrations/124_ops_center_seed.sql"), 3)
        self.assertIn("rollback;", sql)


if __name__ == "__main__":
    unittest.main()
