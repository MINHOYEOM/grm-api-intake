"""154 DB 크기 감시 — db_size_health.evaluate·워크플로·migration·알림 배선 고정.

뮤테이션: WARN_RATIO 를 1.0 으로, RUNWAY_DAYS 를 0 으로 바꾸면 경보 시험이 실패한다.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import db_size_health as h  # noqa: E402
import ops_alarm_sync as al  # noqa: E402

MB = 1024 * 1024
NOW = dt.datetime(2026, 11, 13, 1, 0, tzinfo=dt.timezone.utc)
WF = (ROOT / ".github" / "workflows" / "grm-db-size-health.yml").read_text(encoding="utf-8")
MIG = (ROOT / "web" / "migrations" / "154_db_size_daily.sql").read_text(encoding="utf-8")
MIG165 = (ROOT / "web" / "migrations" / "165_db_size_status_conservative.sql").read_text(encoding="utf-8")


def _status(db_mb: float, growth_mb: float | None = None, recorded: str = "2026-11-13T00:05:00Z") -> dict:
    s = {"limit_bytes": 500 * MB, "db_bytes": int(db_mb * MB), "used_ratio": db_mb / 500,
         "rows": 30, "recorded_at": recorded, "top_tables": []}
    if growth_mb is not None:
        s["growth_7d_per_day"] = int(growth_mb * MB)
    return s


class EvaluateTest(unittest.TestCase):
    def test_today_is_ok(self):
        # 2026-11-12 실측 366MB, 하루 1MB 증가면 134일 남음 → 정상
        self.assertEqual(h.evaluate(_status(366, 1.0), now=NOW), [])

    def test_warn_ratio(self):
        problems = h.evaluate(_status(430, 0.1), now=NOW)  # 86% ≥ 85%
        self.assertTrue(any("사용률" in p for p in problems))

    def test_warn_ratio_is_85_percent(self):
        # 165: 기준 85%. 410/500 = 82% 는 정상, 뮤테이션(0.80 로 되돌리기)이면 이 시험이 깨진다.
        self.assertEqual(h.WARN_RATIO, 0.85)
        self.assertEqual(h.evaluate(_status(410, 0.1), now=NOW), [])

    def test_runway(self):
        problems = h.evaluate(_status(366, 3.0), now=NOW)  # 134MB / 3MB ≈ 45일
        self.assertTrue(any("한도까지" in p for p in problems))

    def test_shrinking_or_flat_has_no_runway_alarm(self):
        self.assertEqual(h.evaluate(_status(366, 0.0), now=NOW), [])
        self.assertEqual(h.evaluate(_status(366, -2.0), now=NOW), [])
        self.assertEqual(h.evaluate(_status(366, None), now=NOW), [])

    def test_stale_record(self):
        problems = h.evaluate(_status(366, 1.0, recorded="2026-11-11T00:05:00Z"), now=NOW)
        self.assertTrue(any("멈춤" in p for p in problems))

    def test_no_record(self):
        self.assertTrue(h.evaluate({"limit_bytes": 500 * MB}, now=NOW))

    def test_used_bytes_preferred_over_db_bytes(self):
        # 165: used_bytes(DB 전체 합, template 포함)가 있으면 그것으로 센다 — db_bytes 만 보면 낙관 쪽으로 어긋난다.
        s = _status(400, 0.1)          # db_bytes 80%
        self.assertEqual(h.evaluate(s, now=NOW), [])
        s["used_bytes"] = int(430 * MB)  # 전체 합 86%
        self.assertTrue(any("사용률" in p for p in h.evaluate(s, now=NOW)))
        self.assertEqual(h.used_bytes({"db_bytes": 5}), 5.0)
        self.assertEqual(h.used_bytes({"db_bytes": 5, "used_bytes": 7}), 7.0)

    def test_median_growth_preferred(self):
        # 165: 일회성 적재가 낀 7일 평균(3.6MB) 대신 중앙값(1.3MB)으로 한도까지 일수를 본다.
        s = _status(390, 3.6)
        s["growth_7d_median_per_day"] = int(1.3 * MB)
        self.assertEqual(h.growth_per_day(s), int(1.3 * MB))
        self.assertEqual(h.evaluate(s, now=NOW), [])          # 110MB / 1.3MB ≈ 85일 → 정상
        del s["growth_7d_median_per_day"]
        self.assertTrue(any("한도까지" in p for p in h.evaluate(s, now=NOW)))  # 평균이면 ≈ 31일

    def test_growth_window_fallback(self):
        s = _status(366)
        s["growth_28d_per_day"] = 2 * MB
        self.assertEqual(h.growth_per_day(s), 2 * MB)


class WiringTest(unittest.TestCase):
    def test_alarm_team(self):
        self.assertEqual(al.team_for(["db-size-health"]), "infra")

    def test_workflow(self):
        self.assertIn("python db_size_health.py --output db_size_report.json", WF)
        self.assertIn("const label = 'db-size-health';", WF)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}", WF)
        self.assertNotIn("findings_search_cache", WF)

    def test_migration_permissions(self):
        self.assertIn("revoke all on table public.ops_db_size_daily from public, anon, authenticated;", MIG)
        self.assertIn("revoke execute on function public.grm_db_size_record() from public, anon, authenticated;", MIG)
        self.assertIn("revoke execute on function public.grm_db_size_status() from public, anon, authenticated;", MIG)
        self.assertIn("grant execute on function public.grm_db_size_status() to service_role;", MIG)
        self.assertNotIn("to anon", MIG)

    def test_migration_165_conservative_and_additive(self):
        code = "\n".join(l for l in MIG165.splitlines() if not l.strip().startswith("--"))
        self.assertIn("'limit_bytes',       (select b from lim)", code)
        self.assertIn("select 500000000::bigint as b", code)
        self.assertIn("sum(pg_database_size(d.datname))", code)
        self.assertNotIn("524288000", code)
        # 154 의 키는 전부 남는다(가산만)
        for key in ("'limit_bytes'", "'day'", "'db_bytes'", "'used_ratio'", "'rows'", "'growth_7d_per_day'",
                    "'growth_28d_per_day'", "'growth_all_per_day'", "'recorded_at'", "'top_tables'",
                    "'used_bytes'", "'growth_7d_median_per_day'"):
            self.assertIn(key, code)
        self.assertIn("percentile_cont(0.5)", code)
        self.assertIn("returns jsonb\nlanguage sql stable security definer set search_path to 'public' as $$", code)
        self.assertIn("revoke execute on function public.grm_db_size_status() from public, anon, authenticated;", code)
        self.assertIn("grant execute on function public.grm_db_size_status() to service_role;", code)
        self.assertIn("revoke execute on function public.grm_db_size_record() from public, anon, authenticated;", code)
        self.assertIn("add column if not exists used_bytes bigint", code)
        self.assertNotIn("to anon", code)
        self.assertNotIn(b"\r\n", (ROOT / "web" / "migrations" / "165_db_size_status_conservative.sql").read_bytes())

    def test_cli_status_json(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(_status(366, 1.0, recorded=dt.datetime.now(dt.timezone.utc).isoformat())),
                         encoding="utf-8")
            out = Path(d) / "r.json"
            self.assertEqual(h.main(["--status-json", str(p), "--output", str(out)]), 0)
            self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["ok"])


if __name__ == "__main__":
    unittest.main()
