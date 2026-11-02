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
