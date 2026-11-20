# -*- coding: utf-8 -*-
"""클라우드 요청 마커 검증 — 허용목록 밖의 동작·인자·날짜가 전부 거부되는지."""
import datetime
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "cloud_request", os.path.join(ROOT, "company", "ops", "cloud_request.py"))
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

TODAY = datetime.date(2026, 11, 9)
RID = "20261109-0900-bridge"


def req(**over):
    body = {"id": RID, "action": "delta-bridge", "args": {"publish_date": "2026-11-09"}}
    body.update(over)
    return json.dumps(body)


class ValidateTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(cr.validate(req(), RID, TODAY),
                         ("grm-delta-bridge.yml", "delta-bridge", {"publish_date": "2026-11-09"}))

    def test_ping_has_no_workflow(self):
        wf, action, args = cr.validate(req(action="ping", args={}), RID, TODAY)
        self.assertIsNone(wf)

    def test_web_publish_optional_run_id(self):
        r = req(action="web-publish", args={"publish_date": "2026-11-09", "intake_run_id": "123456789"})
        self.assertEqual(cr.validate(r, RID, TODAY)[2]["intake_run_id"], "123456789")

    def test_rejections(self):
        bad = [
            req(action="newsletter-send"),                       # 허용목록 밖
            req(action="grm-delta-bridge.yml"),                  # 워크플로 파일명 직접 지정
            req(args={"publish_date": "2026-11-09", "x": "1"}),  # 모르는 인자
            req(args={}),                                        # 필수 인자 없음
            req(args={"publish_date": "2026-11-09; rm -rf /"}),  # 주입
            req(args={"publish_date": "2026-13-40"}),            # 없는 날짜
            req(args={"publish_date": "2026-01-01"}),            # 범위 밖(과거)
            req(args={"publish_date": "2027-01-01"}),            # 범위 밖(미래)
            req(id="other"),                                     # id 불일치
            req(extra=1),                                        # 모르는 최상위 키
            req(action="scaffold-mirror", args={"intake_run_id": "12ab"}),
            req(action="web-publish", args={"publish_date": "2026-11-09", "intake_run_id": ["1"]}),
            "[]", "not json", "x" * 3000,
        ]
        for raw in bad:
            with self.assertRaises(cr.Rejected, msg=raw[:60]):
                cr.validate(raw, RID, TODAY)

    def test_bad_ids(self):
        for rid in ["", "../x", "20261109-0900-A", "x" * 60, "20261109-0900-"]:
            with self.assertRaises(cr.Rejected):
                cr.validate(req(id=rid), rid, TODAY)

    def test_allowlist_has_no_send_or_merge(self):
        wfs = {v[0] for v in cr.ACTIONS.values() if v[0]}
        for banned in ("newsletter", "announce", "watchlist-notify", "stranded", "db-backup"):
            self.assertFalse([w for w in wfs if banned in w], banned)


class IntakeTest(unittest.TestCase):
    """intake 동작 — 일일 수집을 입력 없이 깨우고, 소유자 PAT 로, 오늘 handoff 가 있으면 건너뛴다."""
    RID = "20261123-0500-intake"
    # 2026-11-23 05:00 KST = 11-22 20:00 UTC
    NOW = datetime.datetime(2026, 11, 22, 20, 0, 0, tzinfo=datetime.timezone.utc)

    def test_validate_no_args(self):
        raw = json.dumps({"id": self.RID, "action": "intake", "args": {}})
        self.assertEqual(cr.validate(raw, self.RID, datetime.date(2026, 11, 23)),
                         ("grm-intake.yml", "intake", {}))
        raw = json.dumps({"id": self.RID, "action": "intake"})              # args 생략도 된다
        self.assertEqual(cr.validate(raw, self.RID, datetime.date(2026, 11, 23))[0], "grm-intake.yml")
        for args in ({"window_days": "30"}, {"sources": "fr"}, {"dry_run": "true"}):
            with self.assertRaises(cr.Rejected, msg=args):
                cr.validate(json.dumps({"id": self.RID, "action": "intake", "args": args}),
                            self.RID, datetime.date(2026, 11, 23))

    def test_pat_only_for_intake(self):
        self.assertEqual(cr.PAT_ACTIONS, {"intake"})
        self.assertEqual(cr.ACTIONS["queue-handoff-mirror"][0], "grm-queue-handoff-mirror.yml")

    def test_workflow_files_exist_and_inputs_match(self):
        """깨우는 워크플로가 실제로 있고 workflow_dispatch 를 받는지, 넘기는 인자가 그 워크플로의 입력인지."""
        import re
        for action, (wf, spec) in cr.ACTIONS.items():
            if not wf:
                continue
            with open(os.path.join(ROOT, ".github", "workflows", wf), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("workflow_dispatch:", text, wf)
            for name in spec:
                self.assertTrue(re.search(r"^\s{6}%s:\s*$" % re.escape(name), text, re.M), (wf, name))
        # 사본은 수집 완료(workflow_run)로 돈다 — 이름이 바뀌면 intake 를 깨워도 사본이 안 붙는다
        with open(os.path.join(ROOT, ".github", "workflows", "grm-intake.yml"), encoding="utf-8") as f:
            self.assertTrue(f.readline().startswith("name: GRM API Intake (Daily)"))
        with open(os.path.join(ROOT, ".github", "workflows", "grm-queue-handoff-mirror.yml"), encoding="utf-8") as f:
            self.assertIn('workflows: ["GRM API Intake (Daily)"]', f.read())

    def test_request_workflow_uses_pat_and_skip(self):
        with open(os.path.join(ROOT, ".github", "workflows", "grm-cloud-request.yml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("ADMIN_TOKEN: ${{ secrets.ADMIN_GITHUB_ACTIONS_TOKEN }}", text)
        self.assertIn('GH_TOKEN="$DISPATCH_TOKEN" gh workflow run', text)
        self.assertIn('python3 "$CR" intake-skip _runs.json _handoffs.txt', text)
        self.assertIn("claude/queue-handoff", text)

    def run_(self, rid, status, conclusion, created):
        return {"id": rid, "status": status, "conclusion": conclusion, "created_at": created}

    def test_skip_when_handoff_exists(self):
        self.assertIn("handoff_2026-11-23.json",
                      cr.intake_skip_reason([], {"handoff_2026-11-23.json", "README.txt"}, self.NOW))
        # 어제 handoff 만 있으면 깨운다
        self.assertIsNone(cr.intake_skip_reason([], {"handoff_2026-11-22.json"}, self.NOW))

    def test_skip_when_today_run_live_or_success(self):
        today_early = "2026-11-22T15:10:00Z"  # 월 00:10 KST
        for status, concl in (("in_progress", None), ("queued", None), ("waiting", None),
                              ("completed", "success")):
            with self.subTest(status=status):
                self.assertIsNotNone(cr.intake_skip_reason(
                    [self.run_(1, status, concl, today_early)], set(), self.NOW))

    def test_dispatch_when_only_failures_or_old_runs(self):
        runs = [
            self.run_(1, "completed", "failure", "2026-11-22T15:10:00Z"),   # 오늘 실패 — handoff 없음
            self.run_(2, "completed", "cancelled", "2026-11-22T15:45:00Z"),
            self.run_(3, "completed", "success", "2026-11-22T00:38:00Z"),   # 일 09:38 KST — 어제
            self.run_(4, "in_progress", None, "2026-11-22T14:50:00Z"),      # 일 23:50 KST — 어제
            {"id": 5},                                                      # 형식 깨짐은 무시
        ]
        self.assertIsNone(cr.intake_skip_reason(runs, set(), self.NOW))

    def test_cli_fails_open(self):
        d = tempfile.mkdtemp()
        bad = os.path.join(d, "runs.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("not json")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cr.main(["x", "intake-skip", bad, os.path.join(d, "missing.txt")])
        self.assertEqual((rc, buf.getvalue().strip()), (0, ""))

    def test_check_verdict_carries_pat(self):
        d = tempfile.mkdtemp()
        rid = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M") + "-intake"
        p = os.path.join(d, "req.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"id": rid, "action": "intake", "args": {}}, f)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cr.main(["x", "check", p, rid, os.path.join(d, "res")])
        v = json.loads(buf.getvalue())
        self.assertEqual((rc, v["workflow"], v["pat"]), (0, "grm-intake.yml", True))


class ReplayTest(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 9, 1, 0, 0, tzinfo=datetime.timezone.utc)

    def test_replay_and_rate(self):
        d = tempfile.mkdtemp()
        cr.check_replay(d, RID, self.NOW)
        cr.write_result(d, RID, {"action": "ping", "args": {}}, "pinged", "a" * 40, self.NOW)
        with self.assertRaises(cr.Rejected):
            cr.check_replay(d, RID, self.NOW)
        for i in range(cr.RATE_PER_HOUR):
            cr.write_result(d, "20261109-0100-r%02d" % i, {}, "pinged", "a" * 40, self.NOW)
        with self.assertRaises(cr.Rejected):
            cr.check_replay(d, "20261109-0100-new", self.NOW)

    def test_results_trimmed(self):
        d = tempfile.mkdtemp()
        for i in range(cr.KEEP_RESULTS + 5):
            cr.write_result(d, "20261109-%04d-r" % i, {}, "pinged", "a" * 40, self.NOW)
        self.assertEqual(len(os.listdir(d)), cr.KEEP_RESULTS)


class StatusTest(unittest.TestCase):
    def test_collect_survives_failures_and_flags_artifacts(self):
        spec = importlib.util.spec_from_file_location(
            "ops_status", os.path.join(ROOT, "company", "ops", "ops_status.py"))
        st = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(st)

        def fake(args):
            if args[0] == "run":
                if args[3] == "grm-web-publish.yml":
                    raise RuntimeError("x")
                return json.dumps([{"databaseId": 7, "conclusion": "success"}]) if args[3] == "grm-intake.yml" else "[]"
            return json.dumps([{"name": "grm-web-brief", "expired": False}])

        out = st.collect(run=fake, repo="o/r")
        self.assertEqual(out["workflows"]["grm-web-publish.yml"], {"error": "RuntimeError"})
        self.assertTrue(out["intake_artifacts"][0]["has_web_brief"])
        self.assertTrue(st.same_ignoring_time(out, dict(out, generated_at="x")))


if __name__ == "__main__":
    unittest.main()
