#!/usr/bin/env python3
"""사이트 오류 감시(site_error_watch.py + grm-site-error-watch.yml) — 네트워크 없이 본다.

판정 기준(5xx·사이트 경로 404 급증·경로 수·배포 실패), 스캐너 경로 걸러내기, 이슈 열기·닫기 계획,
LABEL_TEAM 매핑(site-errors -> 개발팀), 합성 점검 경보와 제목·라벨이 겹치지 않는지, 워크플로 안전 장치.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
import site_error_watch as w  # noqa: E402
import ops_alarm_sync as al  # noqa: E402

WF = ROOT / ".github" / "workflows" / "grm-site-error-watch.yml"
PROBE_WF = ROOT / ".github" / "workflows" / "grm-site-probe.yml"
DAY = dt.date(2026, 10, 25)


def _zone(today404: dict[str, int], base_per_day: int = 20, fivexx: int = 0, total: int = 20000,
          scanner: int = 0) -> dict:
    """판정 대상 하루 + 직전 14일 표본을 흉내 낸 GraphQL zones[0]."""
    nf, totals = [], []
    for i in range(1, w.BASELINE_DAYS + 1):
        d = (DAY - dt.timedelta(days=i)).isoformat()
        if base_per_day:
            nf.append({"count": base_per_day, "dimensions": {"datetimeHour": f"{d}T03:00:00Z",
                                                             "clientRequestPath": "/glossary/old-term/"}})
        totals.append({"count": total, "dimensions": {"date": d, "edgeResponseStatus": 200}})
    d = DAY.isoformat()
    for j, (path, n) in enumerate(today404.items()):
        nf.append({"count": n, "dimensions": {"datetimeHour": f"{d}T{j % 24:02d}:00:00Z", "clientRequestPath": path}})
    if scanner:
        nf.append({"count": scanner, "dimensions": {"datetimeHour": f"{d}T05:00:00Z", "clientRequestPath": "/.env"}})
    totals.append({"count": total, "dimensions": {"date": d, "edgeResponseStatus": 200}})
    totals.append({"count": sum(today404.values()) + scanner, "dimensions": {"date": d, "edgeResponseStatus": 404}})
    errs = [{"count": fivexx, "dimensions": {"datetimeHour": f"{d}T10:00:00Z", "clientRequestPath": "/findings/",
                                             "edgeResponseStatus": 503}}] if fivexx else []
    return {"totals": totals, "notFound": nf, "serverErrors": errs}


class PathFilterTest(unittest.TestCase):
    def test_scanner_paths_are_not_site_paths(self):
        for p in ("/.env", "/erp/.env", "/.git/config", "/en/.env", "/php-info.php", "/wp-content/x.png",
                  "/assets/popular.js.map", "/findings/.git/HEAD", "/cdn-cgi/l/email-protection",
                  "/apple-touch-icon.png", "/admin/utils.js", "/terraform.tfstate"):
            self.assertFalse(w.is_site_path(p), p)

    def test_site_paths(self):
        for p in ("/en/findings/doc/hc-insp-87633/null", "/glossary/rabs/", "/briefs/2026-11-05",
                  "/library/who/", "/assets/app.js", "/en/glossary/quarantine/null", "/findings/clause/211-22/null"):
            self.assertTrue(w.is_site_path(p), p)


class JudgeTest(unittest.TestCase):
    def test_normal_day_is_quiet(self):
        # 실측 평소: 사이트 404 0~30건·경로 20개 이하, 스캐너 404 는 수천이어도 무관.
        v = w.judge(_zone({f"/glossary/t{i}/": 1 for i in range(20)}, scanner=2000), DAY, None)
        self.assertFalse(v["alarm"], v["reasons"])
        self.assertEqual(v["metrics"]["site404"], 20)
        self.assertEqual(v["metrics"]["all404"], 2020)

    def test_1025_style_null_link_spike(self):
        paths = {"/en/findings/doc/hc-insp-87633/null": 100}
        paths.update({f"/en/findings/doc/hc-insp-{80000 + i}/null": 10 for i in range(82)})
        v = w.judge(_zone(paths, base_per_day=0), DAY, None)
        self.assertTrue(v["alarm"])
        self.assertEqual(v["top404"][0]["path"], "/en/findings/doc/hc-insp-87633/null")
        self.assertLessEqual(len(v["top404"]), w.TOP_PATHS)
        self.assertEqual(len(v["reasons"]), 2)   # 급증 + 경로 수

    def test_spike_needs_both_floor_and_ratio(self):
        # 150건이어도 평소가 40건이면 5배(200) 미만 -> 조용
        self.assertFalse(w.judge(_zone({"/glossary/a/": 150}, base_per_day=40), DAY, None)["alarm"])
        self.assertTrue(w.judge(_zone({"/glossary/a/": 200}, base_per_day=40), DAY, None)["alarm"])
        # 평소 0 이어도 하한 150 미만이면 조용
        self.assertFalse(w.judge(_zone({"/glossary/a/": 140}, base_per_day=0), DAY, None)["alarm"])

    def test_many_paths_alone(self):
        v = w.judge(_zone({f"/findings/doc/x{i}/": 1 for i in range(w.DISTINCT_MIN)}), DAY, None)
        self.assertTrue(v["alarm"])
        self.assertFalse(w.judge(_zone({f"/findings/doc/x{i}/": 1 for i in range(w.DISTINCT_MIN - 1)}),
                                 DAY, None)["alarm"])

    def test_1106_noise_is_quiet(self):
        # 11-06 실측: 27건·20경로 — 경로 기준 15였다면 오경보였다.
        paths = {f"/assets/x{i}.js": 1 for i in range(13)} | {f"/en/findings/q{i}/": 2 for i in range(7)}
        self.assertFalse(w.judge(_zone(paths), DAY, None)["alarm"])

    def test_5xx(self):
        self.assertFalse(w.judge(_zone({}, fivexx=10), DAY, None)["alarm"])   # 실측 30일 합계 수준
        self.assertTrue(w.judge(_zone({}, fivexx=w.FIVEXX_MIN), DAY, None)["alarm"])
        # 요청이 적은 날은 비율로도 잡는다(1% 이상, 최소 20건)
        self.assertTrue(w.judge(_zone({}, fivexx=30, total=2000), DAY, None)["alarm"])
        self.assertFalse(w.judge(_zone({}, fivexx=10, total=500), DAY, None)["alarm"])

    def test_deploy_failure(self):
        bad = {"conclusion": "failure", "created_at": "2026-10-25T07:00:00Z", "url": "https://x/run/1"}
        v = w.judge(_zone({}), DAY, bad)
        self.assertTrue(v["alarm"])
        self.assertIn("https://x/run/1", w.render_body(v))
        self.assertFalse(w.judge(_zone({}), DAY, {"conclusion": "success"})["alarm"])

    def test_other_days_do_not_leak_into_today(self):
        z = _zone({}, base_per_day=500)
        self.assertEqual(w.judge(z, DAY, None)["metrics"]["site404"], 0)

    def test_body_has_paths_and_kst_time(self):
        v = w.judge(_zone({"/en/findings/doc/a/null": 300}, base_per_day=0), DAY, None)
        body = w.render_body(v, "https://run")
        self.assertIn("`/en/findings/doc/a/null`", body)
        self.assertIn("10-25 09시", body)   # 00:00Z -> 09시 KST
        self.assertIn("https://run", body)


class IssuePlanTest(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(w.plan_issue(True, None), "create")
        self.assertEqual(w.plan_issue(True, 12), "comment")   # 같은 이슈 재사용
        self.assertEqual(w.plan_issue(False, 12), "close")    # 해소 댓글 + 닫기
        self.assertEqual(w.plan_issue(False, None), "noop")

    def test_apply_close_comments_then_closes(self):
        calls = []
        with mock.patch.object(w, "gh", side_effect=lambda a, check=True: calls.append(a) or ""):
            w.apply_issue("o/r", "close", 7, w.ISSUE_TITLE, w.LABEL, "해소")
        self.assertEqual([c[:2] for c in calls], [["issue", "comment"], ["issue", "close"]])

    def test_dry_run_writes_nothing(self):
        """--apply 없이(또는 ENABLE 꺼짐) 경보여도 gh 쓰기 호출 0."""
        zone = _zone({"/glossary/a/": 999}, base_per_day=0)
        for argv, env in ((["--day", "2026-10-25"], {"ENABLE_SITE_ERROR_WATCH": "true"}),
                          (["--day", "2026-10-25", "--apply"], {"ENABLE_SITE_ERROR_WATCH": "false"})):
            writes = []

            def fake_gh(a, check=True):
                if a[:2] in (["issue", "create"], ["issue", "comment"], ["issue", "close"], ["label", "create"]):
                    writes.append(a)
                return "[]"
            env = {**env, "CLOUDFLARE_ANALYTICS_TOKEN": "x", "GH_TOKEN": ""}
            with mock.patch.dict("os.environ", env, clear=False), \
                    mock.patch.object(w, "fetch_zone", return_value=zone), \
                    mock.patch.object(w, "gh", side_effect=fake_gh), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(w.main(argv), 0)
            self.assertEqual(writes, [], argv)
            self.assertIn("경보", out.getvalue())

    def test_apply_creates_issue_with_label(self):
        zone = _zone({"/glossary/a/": 999}, base_per_day=0)
        created = []

        def fake_gh(a, check=True):
            if a[:2] == ["issue", "create"]:
                created.append(a)
            return "[]"
        env = {"ENABLE_SITE_ERROR_WATCH": "true", "CLOUDFLARE_ANALYTICS_TOKEN": "x"}
        with mock.patch.dict("os.environ", env, clear=False), mock.patch.object(w, "fetch_zone", return_value=zone), \
                mock.patch.object(w, "gh", side_effect=fake_gh), redirect_stdout(io.StringIO()):
            self.assertEqual(w.main(["--day", "2026-10-25", "--apply"]), 0)
        self.assertEqual(len(created), 1)
        a = created[0]
        self.assertEqual(a[a.index("--title") + 1], w.ISSUE_TITLE)
        self.assertEqual(a[a.index("--label") + 1], w.LABEL)

    def test_fetch_failure_is_separate_issue_and_exit_2(self):
        created = []

        def fake_gh(a, check=True):
            if a[:2] == ["issue", "create"]:
                created.append(a)
            return "[]"
        env = {"ENABLE_SITE_ERROR_WATCH": "true", "CLOUDFLARE_ANALYTICS_TOKEN": "x"}
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch.object(w, "fetch_zone", side_effect=w.FetchError("authz")), \
                mock.patch.object(w, "gh", side_effect=fake_gh), redirect_stdout(io.StringIO()):
            self.assertEqual(w.main(["--day", "2026-10-25", "--apply"]), 2)
        a = created[0]
        self.assertEqual(a[a.index("--title") + 1], w.FAIL_TITLE)
        self.assertEqual(a[a.index("--label") + 1], w.FAIL_LABEL)


class MappingTest(unittest.TestCase):
    def test_label_goes_to_dev_team(self):
        self.assertTrue(al.is_alarm([w.LABEL]))
        self.assertEqual(al.team_for([w.LABEL]), "dev")
        self.assertEqual(al.team_for([w.FAIL_LABEL]), "infra")
        self.assertIn((w.LABEL, "dev"), al.LABEL_TEAM)

    def test_no_overlap_with_synthetic_probe(self):
        probe = PROBE_WF.read_text(encoding="utf-8")
        self.assertIn("'사이트 합성 점검 실패'", probe)
        self.assertNotIn(w.ISSUE_TITLE, probe)
        self.assertNotEqual(w.ISSUE_TITLE, "사이트 합성 점검 실패")
        self.assertNotIn(w.LABEL, probe)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WF.read_text(encoding="utf-8")

    def test_flag_default_off_vars_only(self):
        self.assertRegex(self.text, r"ENABLE_SITE_ERROR_WATCH: \$\{\{ vars\.ENABLE_SITE_ERROR_WATCH \|\| 'false' \}\}")

    def test_no_untrusted_input_in_run(self):
        run = self.text.split("run: |", 1)[1]
        self.assertNotIn("${{", run)

    def test_pinned_actions_and_permissions(self):
        for line in re.findall(r"uses: (\S+)", self.text):
            self.assertRegex(line, r"@[0-9a-f]{40}$", line)
        self.assertIn("issues: write", self.text)
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)

    def test_stdout_guard_in_main(self):
        src = (ROOT / "site_error_watch.py").read_text(encoding="utf-8")
        body = src.split("def main(", 1)[1]
        self.assertLess(body.index("reconfigure"), body.index("argparse"))

    def test_no_secret_printing(self):
        src = (ROOT / "site_error_watch.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"print\([^)]*token")


if __name__ == "__main__":
    unittest.main()
