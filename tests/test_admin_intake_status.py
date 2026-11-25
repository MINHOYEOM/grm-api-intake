"""Admin '수집 현황' — ① 26시간 지연 판정(경계) ② 소스별 건수 기록·파싱 ③ 화면이 '지연'을 그리는지."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS_JS = ROOT / "web" / "assets" / "admin-ops.js"
HARNESS = ROOT / "tests" / "js" / "intake_status_render.js"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def node(fn, *args):
    res = subprocess.run(["node", str(HARNESS), str(OPS_JS), json.dumps({"fn": fn, "args": list(args)})],
                         capture_output=True, text=True, encoding="utf-8", timeout=20)
    if res.returncode:
        raise AssertionError(res.stderr)
    return json.loads(res.stdout)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(ago_h, conclusion="success", status="completed", rid=1):
    t = NOW - timedelta(hours=ago_h)
    return {"workflow_id": "grm-intake.yml", "relevant": True, "id": rid, "status": status, "conclusion": conclusion,
            "created_at": iso(t), "updated_at": iso(t), "html_url": "https://github.com/o/r/actions/runs/%d" % rid}


def rec(ago_h, sources, rid, active=None):
    """sources: [(key, fetched)] 또는 [(key, fetched, error)]"""
    rows = [{"key": s[0], "label": s[0].upper(), "fetched": s[1], "error": bool(len(s) > 2 and s[2])} for s in sources]
    return {"run_id": rid, "created_at": iso(NOW - timedelta(hours=ago_h)), "conclusion": "success",
            "url": "https://github.com/o/r/actions/runs/%d" % rid, "total": sum(r["fetched"] for r in rows),
            "active": active or [r["key"] for r in rows], "sources": rows}


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class LateJudgementTest(unittest.TestCase):
    def kind(self, runs):
        return node("intakeVerdict", runs, NOW_MS)["kind"]

    def test_boundary_26h(self):
        self.assertEqual(self.kind([run(25.99)]), "ok")
        self.assertEqual(self.kind([run(26)]), "ok")          # 정확히 26시간은 아직 지연 아님
        self.assertEqual(self.kind([run(26.02)]), "late")     # 넘으면 지연
        self.assertEqual(self.kind([run(60)]), "late")

    def test_failed_or_running_run_is_not_a_new_collection(self):
        # 어제 성공 후 오늘 실패/실행 중 — 새 수집으로 치지 않는다(하루 누락이 정상으로 보이면 안 된다)
        v = node("intakeVerdict", [run(1, "failure", rid=3), run(30, rid=2)], NOW_MS)
        self.assertEqual(v["kind"], "late")
        self.assertEqual(v["lastFailed"]["id"], 3)
        v = node("intakeVerdict", [run(1, None, "in_progress", rid=3), run(30, rid=2)], NOW_MS)
        self.assertEqual(v["kind"], "late")
        self.assertEqual(v["running"]["id"], 3)

    def test_no_success_and_other_workflows_ignored(self):
        self.assertEqual(self.kind([]), "none")
        self.assertEqual(self.kind([run(2, "failure")]), "none")
        other = dict(run(1), workflow_id="grm-web-publish.yml")
        self.assertEqual(self.kind([other]), "none")


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class SourceCountsTest(unittest.TestCase):
    def test_zero_vs_usual_uses_prior_median(self):
        hist = [rec(1, [("pics", 0), ("eca", 10), ("wl", 4)], 9)] + \
               [rec(24 * i + 1, [("pics", 3), ("eca", 9 + i), ("wl", 0)], 9 - i) for i in range(1, 5)]
        s = node("intakeSources", hist)
        self.assertEqual([r["key"] for r in s["zeros"]], ["pics"])     # 평소 3 → 0 만 표시, wl 은 평소부터 0
        self.assertEqual({r["key"]: r["base"] for r in s["rows"]}["pics"], 3)

    def test_needs_enough_history_and_ignores_disabled(self):
        few = [rec(1, [("pics", 0)], 3), rec(25, [("pics", 5)], 2)]
        self.assertEqual(node("intakeSources", few)["zeros"], [])      # 이전 수집 1회뿐 — 평소를 말하지 않는다
        hist = [rec(1, [("pics", 0), ("eca", 1)], 9, active=["eca"])] + \
               [rec(24 * i, [("pics", 4), ("eca", 1)], 9 - i) for i in range(1, 5)]
        self.assertEqual(node("intakeSources", hist)["zeros"], [])     # 이번에 꺼진 소스는 0 이어도 표시 안 함

    def test_summary_line_parse(self):
        ic = _load("intake_counts", "company/ops/intake_counts.py")
        line = "2026-11-24 INFO 수집 완료: FR=1 · MHRA-Alert=2 · PICS=0 · ECA=10 · WL=4 · EU-NCR=3 · 합계=20건"
        p = ic.parse_summary_line("앞줄\n" + line + "\n뒷줄")
        self.assertEqual(p["sources"], {"FR": 1, "MHRA-Alert": 2, "PICS": 0, "ECA": 10, "WL": 4, "EU-NCR": 3})
        self.assertEqual(p["total"], 20)
        self.assertEqual(sum(p["sources"].values()), p["total"])
        self.assertIsNone(ic.parse_summary_line("합계 없음"))

    def test_from_health_matches_collector_registry(self):
        """health 의 소스 키가 수집기 레지스트리와 같고 합계가 소스별 합이다 — 수집기가 소스를 늘려도 어긋나면 여기서 잡힌다."""
        sys.path.insert(0, str(ROOT))
        from collect_intake import CollectionStats
        from grm_health import _source_health_rows
        from grm_common import INTAKE_SOURCE_SPECS
        stats = CollectionStats()
        stats.eca_fetched, stats.wl_fetched = 10, 4
        ic = _load("intake_counts2", "company/ops/intake_counts.py")
        r = ic.from_health({"sources": _source_health_rows(stats), "run_date_kst": "2026-11-24", "active_sources": ["eca"]},
                           "123", "https://github.com/o/r/actions/runs/123")
        self.assertEqual([x["key"] for x in r["sources"]], [s.prefix for s in INTAKE_SOURCE_SPECS])
        self.assertEqual(r["total"], 14)
        self.assertEqual(r["run_id"], 123)

    def test_ops_status_gathers_and_reuses_counts(self):
        st = _load("ops_status_x", "company/ops/ops_status.py")
        calls = []

        def fake(args):
            if args[:2] == ["run", "download"]:
                calls.append(args[2])
                d = args[args.index("-D") + 1]
                with open(os.path.join(d, "intake_counts.json"), "w", encoding="utf-8") as f:
                    json.dump({"sources": [{"key": "eca", "fetched": 10}], "total": 10}, f)
                return ""
            raise AssertionError(args)
        runs = [{"databaseId": 5, "status": "completed", "conclusion": "success", "createdAt": "2026-11-24T01:00:00Z", "url": "u"},
                {"databaseId": 4, "status": "in_progress", "createdAt": "2026-11-24T02:00:00Z"}]
        first = st.gather_counts(fake, runs, [])
        self.assertEqual([r["run_id"] for r in first], [5])
        st.gather_counts(fake, runs, first)
        self.assertEqual(calls, ["5"])                                  # 이미 받은 실행은 다시 받지 않는다


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class RenderTest(unittest.TestCase):
    def test_screen_shows_late(self):
        html = node("renderIntake", {"runs": [run(30)], "intake_counts": []}, NOW_MS)
        self.assertIn('data-intake-verdict="late"', html)
        self.assertIn("수집 지연", html)
        self.assertIn("admin-ops-brief bad", html)
        self.assertIn("GitHub 실행 보기", html)
        self.assertIn("03:17 KST", html)

    def test_screen_ok_and_flags_zero_source(self):
        hist = [rec(1, [("pics", 0), ("eca", 10)], 9)] + [rec(24 * i, [("pics", 3), ("eca", 9)], 9 - i) for i in range(1, 5)]
        html = node("renderIntake", {"runs": [run(2)], "intake_counts": hist}, NOW_MS)
        self.assertIn('data-intake-verdict="ok"', html)
        self.assertNotIn("수집 지연", html)
        self.assertIn("평소보다 0건", html)
        self.assertIn("합계 10건", html)

    def test_screen_no_history_message_and_unsafe_url(self):
        r = run(2)
        r["html_url"] = "javascript:alert(1)"
        html = node("renderIntake", {"runs": [r], "intake_counts": []}, NOW_MS)
        self.assertNotIn("javascript:", html)
        self.assertIn("다음 수집부터", html)

    def test_wiring(self):
        js = (ROOT / "web" / "assets" / "admin.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "templates" / "admin.html").read_text(encoding="utf-8")
        self.assertIn('id="grm-intake"', html)
        self.assertIn("renderIntakeStatus(errorMessage)", js)
        self.assertIn("intakeIsLate", js)                               # 상단 요약도 지연이면 '조치 필요'
        ts = (ROOT / "supabase" / "functions" / "admin-github" / "index.ts").read_text(encoding="utf-8")
        self.assertIn("intake_counts", ts)
        wf = (ROOT / ".github" / "workflows" / "grm-intake.yml").read_text(encoding="utf-8")
        self.assertIn("name: grm-intake-counts", wf)
        self.assertIn("company/ops/intake_counts.py", wf)


if __name__ == "__main__":
    unittest.main()
