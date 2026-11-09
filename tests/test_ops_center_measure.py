#!/usr/bin/env python3
"""운영 센터 PR6 — 자동 측정. 128_ops_measure.sql 텍스트 계약 + GitHub 갈래(web/ops_measure_github.py)·워크플로.

행동(분모 0 → 측정 보류 · 실패/시간 초과 → 이전 값 유지 · measure_ref 검사 · 시드 멱등)은 격리 PostgreSQL 에서
tests/sql/ops_center_measure.sql 이 보고, 뮤테이션은 tests/run_ops_center_sql_mutations.py 가 CI 에서 돌린다.
여기서는 DB·네트워크 없이 글자·순수 함수로 본다.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MIG = _ROOT / "web" / "migrations"
_P128 = _MIG / "128_ops_measure.sql"
_WF = _ROOT / ".github" / "workflows" / "grm-ops-measure.yml"
_SQLWF = _ROOT / ".github" / "workflows" / "grm-ops-center-sql-check.yml"
_SCRIPT = _ROOT / "web" / "ops_measure_github.py"
sys.path.insert(0, str(_ROOT / "web"))
import ops_measure_github as gm  # noqa: E402

UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))
BANNED_WORDS = ("놀고 있음", "노는 중", "일 없음", "놀고있음")
MANUAL = ("sr-report", "ver-gap", "dq-precision", "tr-same-day", "brief-ontime", "brief-errors", "edu-quiz",
          "strat-adopted", "pr-linkedin", "pr-subs", "growth-channel", "growth-brief-click",
          "plan-review-gap", "plan-risks-open", "support-reply")
MEASURES = ("ops_m_clause_pct_wl", "ops_m_clause_pct_mfds", "ops_m_term_min", "ops_m_ingest_age",
            "ops_m_tr_backlog", "ops_m_db_size_mb", "ops_m_funnel_top_share", "ops_m_subs_daily_delta")


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


class Migration128Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _P128.read_text(encoding="utf-8")
        cls.sql = _code(cls.raw)

    def test_file_lf_unique_number_and_header(self):
        self.assertNotIn(b"\r\n", _P128.read_bytes())
        self.assertEqual([p.name for p in _MIG.glob("128_*.sql")], [_P128.name])
        self.assertIn("information_schema.routine_privileges", self.raw)
        self.assertIn("drop function if exists public.ops_measure_all();", self.raw)
        self.assertIn("drop table if exists private.ops_measure_log;", self.raw)
        self.assertIn("select cron.unschedule('grm-ops-measure');", self.raw)

    def test_cron_only_in_header_comment(self):
        self.assertNotIn("cron.", self.sql, "pg_cron 등록은 파일에 넣지 않는다(비서실이 적용)")
        self.assertIn("select cron.schedule('grm-ops-measure', '30 18 * * *',", self.raw)
        self.assertIn("$$set statement_timeout = '5s'; select public.ops_measure_all()$$", self.raw)
        self.assertIn("매일 03:30 KST", self.raw)

    def test_every_function_revoked_from_three_roles(self):
        made = re.findall(r"create or replace function ([a-z_]+\.[a-z_]+)\(", self.sql)
        self.assertEqual(len(made), len(set(made)))
        for fn in made:
            self.assertRegex(self.sql, re.escape(f"revoke all on function {fn}(") + r"[^;]*\) from public, anon, authenticated;", fn)
            body = self.sql.split(f"create or replace function {fn}(", 1)[1].split("$$", 1)[0]
            self.assertIn("set search_path = public", body, fn)
        grants = re.findall(r"grant execute on function ([a-z_.]+)\(\) to ([a-z_, ]+);", self.sql)
        self.assertEqual(grants, [("public.ops_measure_all", "service_role")])
        self.assertNotIn("to anon", self.sql)
        self.assertNotIn("to authenticated", self.sql)
        for m in MEASURES:
            self.assertIn(f"private.{m}", made)

    def test_entry_is_definer_measures_are_invoker(self):
        entry = self.sql.split("create or replace function public.ops_measure_all()", 1)[1].split("$$", 1)[0]
        self.assertIn("security definer", entry)
        rest = self.sql.replace(entry, "")
        self.assertEqual(rest.count("security definer"), 0, "측정 함수·본체는 invoker")

    def test_ref_guard_before_dynamic_call(self):
        body = self.sql.split("create or replace function private.ops_measure_all_at(", 1)[1]
        guard = "if g.measure_ref !~ '^private\\.ops_m_[a-z0-9_]{1,40}$'"
        self.assertIn(guard, body)
        self.assertIn("to_regprocedure(g.measure_ref || '(timestamptz)') is null", body)
        self.assertLess(body.index(guard), body.index("execute format('select %s($1)', g.measure_ref)"))
        self.assertIn("gl.measure_kind = 'sql'", body)

    def test_failure_and_hold_keep_value(self):
        body = self.sql.split("create or replace function private.ops_measure_all_at(", 1)[1]
        self.assertIn("when query_canceled then", body, "plpgsql 의 others 는 57014 를 못 잡는다")
        self.assertIn("when others then", body)
        self.assertIn("perform public.ops_goal_set(g.slug, v_val, left(v_note, 120), p_now);", body,
                      "저장은 125 ops_goal_set 과 같은 경로(퍼센트 범위 검사 포함)")
        for ln in re.findall(r"update public\.ops_goal set [^;]*;", body):
            self.assertNotIn("current_value", ln, ln)
            self.assertNotIn("measured_at", ln, ln)
        self.assertIn("'측정 보류 '", body)
        self.assertIn("'측정 실패 '", body)

    def test_definitions(self):
        s = " ".join(self.sql.split())
        self.assertIn("where f.source = 'FDA Warning Letter') c", s)
        self.assertIn("where f.agency = 'MFDS') c", s)
        self.assertIn("where t.den >= 20", s)
        self.assertIn("max(r.ingested_at) as m from public.raw_signals r", s)
        self.assertIn("f.source in ('FDA Warning Letter', 'FDA 483')", s)
        self.assertIn("f.finding_text_ko = '' and f.scope_status = 'ok' and f.review_status <> 'rejected'", s)
        self.assertIn("interval '3 days'", s)
        self.assertIn("pg_database_size(current_database())", s)
        self.assertIn("public.funnel_touch_report()", s)
        self.assertIn("'total_submits'", s)
        self.assertNotIn("captured_at", s, "구독 증감은 snap_date 로만(09-22 교훈)")
        # 부분 인덱스 조건과 측정 조건이 글자까지 같다(플래너가 인덱스를 고르게)
        self.assertIn("where finding_text_ko = '' and scope_status = 'ok' and review_status <> 'rejected';", s)

    def test_new_goals_seed(self):
        block = self.raw.split("-- seed:goals begin", 1)[1].split("-- seed:goals end", 1)[0]
        self.assertIn("('infra',    'infra-db-size',  '무료 DB 사용량',          'MB', 'lte', 400::numeric, 400::numeric, 450::numeric, 24,",
                      block)
        for slug in ("growth-sub-top", "pr-subs-day"):
            self.assertIn(f"'{slug}'", block)
        self.assertIn("on conflict (slug) do update set", block)
        self.assertIn("note = case when ops_goal.measured_at is null then excluded.note else ops_goal.note end;", block)
        self.assertNotIn("current_value = excluded", block, "값은 시드가 덮지 않는다")

    def test_manual_goals_untouched(self):
        for slug in MANUAL:
            self.assertNotRegex(self.sql, r"'" + re.escape(slug) + r"'", slug)

    def test_refs_cover_auto_goals(self):
        refs = self.raw.split("-- seed:refs begin", 1)[1].split("-- seed:refs end", 1)[0]
        for slug in ("dq-clause-wl", "dq-clause-mfds", "dq-term-min", "infra-ingest-age", "tr-backlog"):
            self.assertRegex(refs, re.escape(f"('{slug}',") + r"\s+'sql',\s+'private\.ops_m_")
        for slug in ("dev-ci", "dev-pr-old", "dev-link-streak", "infra-deploy-fail", "sr-ontime", "edu-terms-week"):
            self.assertRegex(refs, re.escape(f"('{slug}',") + r"\s+'github',\s+'web/ops_measure_github\.py'")
        for ref in re.findall(r"'(private\.ops_m_[a-z_]+)'", self.sql):
            self.assertIn(f"create or replace function {ref}(p_now timestamptz)", self.sql, ref)
            self.assertLessEqual(len(ref), 80)

    def test_notes_fit_and_no_banned_words(self):
        self.assertIn("left('측정 보류 ' || v_day || ': ' || v_note, 120)", self.sql)
        self.assertIn("left('측정 실패 ' || v_day || ': ' || v_err, 120)", self.sql)
        for text in (self.raw, _SCRIPT.read_text(encoding="utf-8"), _WF.read_text(encoding="utf-8")):
            for w in BANNED_WORDS:
                self.assertNotIn(w, text)

    def test_sql_workflow_runs_measure_checks(self):
        wf = _SQLWF.read_text(encoding="utf-8")
        head = wf.split("jobs:", 1)[0]
        self.assertIn("-f tests/sql/ops_center_measure.sql", wf)
        self.assertIn("tests/sql/ops_center_measure.sql", head)
        self.assertIn("web/migrations/*ops_measure*", head)
        sql = (_ROOT / "tests" / "sql" / "ops_center_measure.sql").read_text(encoding="utf-8")
        self.assertEqual(sql.count("\\ir ../../web/migrations/128_ops_measure.sql"), 3, "적용 + 재실행 + 124 뒤 재실행")
        self.assertIn("rollback;", sql)
        self.assertIn("set local statement_timeout = '700ms';", sql, "실제 타임아웃 시험")

    def test_ci_mutations_target_existing_text(self):
        spec = importlib.util.spec_from_file_location("ops_mut", _ROOT / "tests" / "run_ops_center_sql_mutations.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertGreaterEqual(len(mod.MUTATIONS_MEASURE), 15)
        names = [m[0] for m in mod.MUTATIONS + mod.MUTATIONS_DECIDE + mod.MUTATIONS_TASK
                 + mod.MUTATIONS_PLAN + mod.MUTATIONS_MEASURE]
        self.assertEqual(len(names), len(set(names)))
        for name, path, old, new in mod.MUTATIONS_MEASURE:
            self.assertEqual(path.read_text(encoding="utf-8").count(old), 1, name)
            self.assertNotEqual(old, new, name)


class GithubMeasureTest(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 7, 22, 30, tzinfo=UTC)   # 11-08 07:30 KST

    def test_ci_median_and_hold(self):
        jobs = [{"started_at": "2026-11-07T00:00:00Z", "completed_at": f"2026-11-07T00:{m:02d}:00Z"} for m in (20, 30, 25)]
        v, note = gm.ci_median(jobs)
        self.assertEqual(v, 25.0)
        self.assertIn("최근 3회", note)
        with self.assertRaises(gm.Hold):
            gm.ci_median([])

    def test_old_prs(self):
        prs = [{"created_at": "2026-11-01T00:00:00Z"}, {"created_at": "2026-11-07T00:00:00Z"},
               {"created_at": "2026-11-04T22:29:00Z"}]
        self.assertEqual(gm.old_pr_count(prs, self.NOW), (2, "열린 PR 3건 중 3일 초과 2건(초안 포함)"))
        self.assertEqual(gm.old_pr_count([], self.NOW)[0], 0)

    def _run(self, day, hour, concl, status="completed"):
        return {"created_at": f"2026-11-{day:02d}T{hour:02d}:00:00Z", "conclusion": concl, "status": status}

    def test_link_streak_counts_days_directly(self):
        runs = [self._run(7, 21, "success"), self._run(6, 21, "success"), self._run(5, 21, "failure"),
                self._run(5, 23, "success"), self._run(4, 21, "failure")]
        # 11-05 의 마지막 실행(23시 UTC = 11-06 KST)… KST 날짜로 묶는다
        v, note = gm.link_streak(runs, self.NOW)
        self.assertEqual(v, 3)
        runs = [self._run(7, 21, "success"), self._run(5, 21, "success")]   # 11-07 KST 실행 없음 → 끊김
        v, note = gm.link_streak(runs, self.NOW)
        self.assertEqual(v, 1)
        self.assertIn("실행 없음", note)
        v, _ = gm.link_streak([self._run(7, 21, "failure")], self.NOW)
        self.assertEqual(v, 0)
        v, note = gm.link_streak([self._run(7, 21, "success"), self._run(6, 21, "success")], self.NOW)
        self.assertEqual(v, 2)
        self.assertIn("이상", note)
        with self.assertRaises(gm.Hold):
            gm.link_streak([self._run(7, 21, None, status="in_progress")], self.NOW)

    def test_link_streak_today_not_run_yet_starts_yesterday(self):
        now = dt.datetime(2026, 11, 7, 16, 0, tzinfo=UTC)   # 11-08 01:00 KST, 오늘 실행 전
        v, _ = gm.link_streak([self._run(7, 0, "success"), self._run(6, 0, "success")], now)
        self.assertEqual(v, 2)

    def test_deploy_failures(self):
        by = {"a": [self._run(7, 10, "failure"), self._run(7, 11, "cancelled"), self._run(7, 12, "success"),
                    self._run(5, 10, "failure")],
              "b": [self._run(7, 13, "timed_out")]}
        v, note = gm.deploy_failures(by, self.NOW)
        self.assertEqual(v, 2)
        self.assertIn("배포 4회 중 실패 2회", note)

    def test_cron_parse(self):
        s, e = dt.datetime(2026, 11, 2, 0, 0, tzinfo=UTC), dt.datetime(2026, 11, 8, 23, 59, tzinfo=UTC)
        self.assertEqual(len(gm.cron_fires("17 18 * * *", s, e)), 7)
        self.assertEqual([f.isoformat() for f in gm.cron_fires("0 2 * * 1", s, e)], ["2026-11-02T02:00:00+00:00"])
        self.assertEqual(len(gm.cron_fires("7,37 22-23 * * 0", s, e)), 4)
        self.assertEqual(len(gm.cron_fires("41 */6 * * *", s, e)), 28)
        self.assertEqual(len(gm.cron_fires("0 6 * * 1,3,5", s, e)), 3)
        self.assertEqual(len(gm.cron_fires("47 15 8,15,22 * *", s, e)), 1)
        self.assertEqual(gm.fires_per_day("7,37 * * * *"), 48)
        self.assertEqual(gm.fires_per_day("17 0,6,12 * * *"), 3)

    def test_crons_in_skips_comments(self):
        text = "on:\n  schedule:\n    - cron: '17 18 * * *'   # 03:17 KST\n#    - cron: '0 0 * * *'\n    - cron: \"5 1 * * 2\"\n"
        self.assertEqual(gm.crons_in(text), ["17 18 * * *", "5 1 * * 2"])

    def test_ontime_counts_missing_and_late(self):
        now = dt.datetime(2026, 11, 8, 6, 0, tzinfo=UTC)
        start = now - dt.timedelta(days=7)
        runs = {"a.yml": [
            {"event": "schedule", "created_at": "2026-11-07T18:30:00Z"},   # 13분 지각 → 정시
            {"event": "schedule", "created_at": "2026-11-06T20:00:00Z"},   # 103분 지각 → 일반 6시간 창 안이라 정시
            {"event": "schedule", "created_at": "2026-11-05T01:30:00Z"},   # 433분 지각 → 지각
            {"event": "workflow_dispatch", "created_at": "2026-11-04T18:17:00Z"},  # 수동 실행은 안 셈
        ]}
        v, note = gm.ontime({"a.yml": ["17 18 * * *"]}, runs, {"a.yml": start}, now)
        self.assertEqual(v, round(200 / 7, 1))
        self.assertIn("7일 예약 7회 중 2회 제때(+6시간)", note)
        self.assertIn("지각 1·누락 4", note)
        self.assertIn("가장 늦은 작업: a", note)
        with self.assertRaises(gm.Hold):
            gm.ontime({}, {}, {}, now)

    def _one_fire(self, delay_min, wf="a.yml"):
        now = dt.datetime(2026, 11, 8, 6, 0, tzinfo=UTC)
        start = dt.datetime(2026, 11, 7, 0, 0, tzinfo=UTC)   # 예정 1회: 11-07 18:17
        runs = []
        if delay_min is not None:
            t = dt.datetime(2026, 11, 7, 18, 17, tzinfo=UTC) + dt.timedelta(minutes=delay_min)
            runs = [{"event": "schedule", "created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ")}]
        return gm.ontime({wf: ["17 18 * * *"]}, {wf: runs}, {wf: start}, now)

    def test_ontime_single_window_six_hours(self):
        self.assertEqual(gm.ONTIME_GRACE_MIN, 360)
        self.assertFalse(hasattr(gm, "ONTIME_GRACE_STRICT_MIN"))   # 엄격/일반 이원화는 폐기
        self.assertFalse(hasattr(gm, "ONTIME_STRICT_WORKFLOWS"))
        v, note = self._one_fire(359)
        self.assertEqual(v, 100.0)
        self.assertIn("1회 중 1회 제때(+6시간)", note)
        self.assertNotIn("가장 늦은 작업", note)
        v, note = self._one_fire(361)
        self.assertEqual(v, 0.0)
        self.assertIn("지각 1·누락 0", note)

    def test_ontime_missing_run(self):
        v, note = self._one_fire(None)
        self.assertEqual(v, 0.0)
        self.assertIn("지각 0·누락 1", note)

    def test_ontime_names_late_workflows(self):
        # 6시간을 넘겨 늦었거나 안 돈 작업의 이름(grm- 접두·.yml 제거)이 노트에 보인다
        v, note = self._one_fire(361, wf="grm-db-backup.yml")
        self.assertIn("가장 늦은 작업: db-backup", note)
        v, note = self._one_fire(None, wf="grm-intake.yml")
        self.assertIn("가장 늦은 작업: intake", note)
        # 최대 3개 + 나머지 개수, 건수 많은 순
        now = dt.datetime(2026, 11, 8, 6, 0, tzinfo=UTC)
        start = dt.datetime(2026, 11, 7, 0, 0, tzinfo=UTC)
        names = [f"grm-w{i}.yml" for i in range(5)]
        v, note = gm.ontime({n: ["17 18 * * *"] for n in names}, {n: [] for n in names},
                            {n: start for n in names}, now)
        self.assertIn("누락 5", note)
        self.assertIn("가장 늦은 작업: w0, w1, w2 외 2", note)
        self.assertLessEqual(len(note), gm.NOTE_MAX)
        # 건수가 많은 작업이 먼저(w9 는 하루 2회 예약 → 2건 누락)
        sched = {n: ["17 18 * * *"] for n in names}
        sched["grm-w9.yml"] = ["17 18,19 * * *"]
        v, note = gm.ontime(sched, {n: [] for n in sched}, {n: start for n in sched}, now)
        self.assertIn("가장 늦은 작업: w9, w0, w1 외 3", note)


    def test_ontime_skips_fires_inside_grace(self):
        now = dt.datetime(2026, 11, 8, 20, 40, tzinfo=UTC)   # 18:17 예정이 143분 전 — 일반 창(6시간) 안이라 아직 판정하지 않는다
        start = dt.datetime(2026, 11, 8, 0, 0, tzinfo=UTC)
        with self.assertRaises(gm.Hold):
            gm.ontime({"a.yml": ["17 18 * * *"]}, {"a.yml": []}, {"a.yml": start}, now)

    def test_glossary_week(self):
        self.assertEqual(gm.glossary_week(247, 244, "11-02")[0], 3)
        with self.assertRaises(gm.Hold):
            gm.glossary_week(247, None, "11-02")
        self.assertEqual(gm.kst_monday(dt.datetime(2026, 11, 8, 14, 0, tzinfo=UTC)).isoformat(), "2026-11-02T00:00:00+09:00")

    def test_touch_only_if_newer(self):
        t = dt.datetime(2026, 11, 7, 1, 0, tzinfo=UTC)
        self.assertTrue(gm.touch_needed(None, t))
        self.assertTrue(gm.touch_needed("2026-11-06T00:00:00+00:00", t))
        self.assertFalse(gm.touch_needed("2026-11-07T02:00:00+00:00", t))
        self.assertEqual(set(gm.MEMBERS), {"dq-kg-link", "infra-intake"}, "사람·세션 팀원은 건드리지 않는다")

    def test_measure_all_one_failure_does_not_stop_rest(self):
        def api(path, raw=False):
            if "grm-ci.yml" in path:
                raise RuntimeError("secret-ish message")
            if "/pulls" in path:
                return [{"created_at": "2026-11-01T00:00:00Z"}]
            return {"workflow_runs": [], "workflows": []}
        r = gm.measure_all("o/r", self.NOW, api=api)
        self.assertEqual(r["dev-ci"], {"error": "RuntimeError"}, "예외 메시지는 남기지 않는다")
        self.assertEqual(r["dev-pr-old"]["value"], 1)
        self.assertIn("hold", r["dev-link-streak"])
        self.assertEqual(r["infra-deploy-fail"]["value"], 0)

    def test_notes_clipped(self):
        self.assertEqual(len(gm.clip("가" * 300)), gm.NOTE_MAX)

    def test_summary_marks_failures(self):
        lines = gm.summary_lines({"dev-ci": {"error": "X"}, "dev-pr-old": {"hold": "y"}, "sr-ontime": {"value": 1, "note": "n"}},
                                 [], True)
        text = "\n".join(lines)
        self.assertIn("dry-run", text)
        self.assertIn("| dev-ci | 측정 실패 | X (이전 값 유지) |", text)
        self.assertIn("| dev-pr-old | 측정 보류 | y |", text)

    def test_script_writes_only_via_service_rpcs_and_hides_errors(self):
        src = _SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(sorted(set(re.findall(r'rpc\("([a-z_]+)"', src))), ["ops_goal_set", "ops_member_touch"])
        self.assertNotIn("str(exc)", src)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY\"]", src)
        self.assertIn('stream.reconfigure(encoding="utf-8", errors="replace")', src)
        self.assertNotIn("anthropic", src.lower())
        self.assertNotIn("openai", src.lower())


class MeasureWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = _WF.read_text(encoding="utf-8")

    def test_dry_run_by_default_and_enabled_variable(self):
        self.assertIn('DRY="--dry-run"', self.wf)
        self.assertIn("ENABLED: ${{ vars.GRM_OPS_MEASURE_ENABLED }}", self.wf)
        self.assertRegex(self.wf, r"dry_run:\n(?:.*\n)*?\s+default: true")
        run = self.wf[self.wf.index('DRY="--dry-run"'):]
        self.assertLess(run.index('if [ "$ENABLED" = "true" ]; then'), run.index('DRY=""'))
        self.assertIn("python web/ops_measure_github.py $DRY", self.wf)

    def test_existing_secret_only_and_masked(self):
        self.assertEqual(sorted(set(re.findall(r"secrets\.([A-Z_]+)", self.wf))), ["SUPABASE_SERVICE_ROLE_KEY"])
        self.assertIn('echo "::add-mask::$SUPABASE_SERVICE_ROLE_KEY"', self.wf)
        self.assertLess(self.wf.index("::add-mask::"), self.wf.index("python web/ops_measure_github.py"))
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.wf)

    def test_read_only_permissions_no_issues(self):
        perms = self.wf.split("permissions:", 1)[1].split("concurrency:", 1)[0]
        self.assertNotIn("write", perms)
        for p in ("contents: read", "actions: read", "pull-requests: read"):
            self.assertIn(p, perms)
        self.assertNotIn("issues", self.wf)
        self.assertNotIn("gh issue", self.wf)
        self.assertNotIn("gh issue", _SCRIPT.read_text(encoding="utf-8"))

    def test_daily_schedule_not_on_the_hour(self):
        crons = re.findall(r"cron: '([^']+)'", self.wf)
        self.assertEqual(crons, ["23 22 * * *"])
        self.assertIn("group: grm-ops-measure", self.wf)
        self.assertIn("07:23 KST", self.wf)

    def test_actions_pinned(self):
        for use in re.findall(r"uses: (\S+)", self.wf):
            self.assertRegex(use, r"@[0-9a-f]{40}$", use)


if __name__ == "__main__":
    unittest.main()
