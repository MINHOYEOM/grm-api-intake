"""발행 머지 판정(publish_trigger.py) + 발송 워크플로 배선 시험.

11-23 발행 PR #1873 이 일반 머지로 들어가 배포 커밋 제목이 `Merge pull request #1873 from
MINHOYEOM/publish/brief-2026-11-23` 이 되자, `startsWith(…, 'publish(web)')` 식이 발송을
skipped 로 만들었다(실행 45094478874). 실제 커밋 제목으로 판정을 고정한다.
"""
from __future__ import annotations

import io
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import publish_trigger as pt  # noqa: E402

# main 에 실제로 들어간 배포 커밋 메시지(git log origin/main 에서 그대로).
MSG_1123_MERGE = ("Merge pull request #1873 from MINHOYEOM/publish/brief-2026-11-23\n\n"
                  "publish(web): 2026-11-23 주간 브리프")
MSG_1116_SQUASH = "publish(web): 2026-11-16 주간 브리프 (자동 조립) (#1764)"
MSG_1109_SQUASH = "publish(web): 2026-11-09 주간 브리프 (자동 조립) (#1415)"
MSG_1123_REBASE_LAST = "publish(web): 2026-11-23 개인 실명 2건을 일반 표현으로 (11-21 대표 결정·#1860)"

# 발행이 아닌 main 배포 커밋들(실제 제목).
NON_PUBLISH = [
    "chore: refresh recent regulatory news (#1948)",
    "Merge pull request #1947 from MINHOYEOM/data/483-readable-X-0017\n\ndata(483): 상세 국문 재번역 X-0017 (134행)",
    "Merge pull request #1937 from MINHOYEOM/claude/published-names-redact\n\n"
    "fix(privacy): 공개 발행본 14장 개인 실명 정리",
    "fix(publish): 11-23 첫 실발행 사전 점검 위험 4건 (handoff 사본·브릿지 조용한 skip·반영 지연·샤드 이중 점유)",
    "fix(newsletter): 발행 배포 성공 직후에도 발송 파이프라인을 탄다 (예약 크론 미발화 대비) (#1792)",
    "bridge(web): 2026-11-16 라우틴 델타 커밋 (자동)",
    "glossary(fig): 2026-11-16 새 그림 0 · 다시 그림 0",
    # 본문에만 발행 브랜치 이름이 나오는 수리 커밋 — 제목만 보므로 발행 아님.
    "fix(watchdog): 발행 브랜치 확인\n\npublish/brief-2026-11-23 이 없을 때 경보",
    "Merge branch 'main' into claude/published-names-redact",
    "",
]


class ClassifyCommitTest(unittest.TestCase):
    def test_1123_normal_merge_is_publish(self):
        ok, reason, pub = pt.classify_commit(MSG_1123_MERGE)
        self.assertTrue(ok, reason)
        self.assertEqual(pub, "2026-11-23")
        self.assertIn("일반 머지", reason)

    def test_1116_squash_is_publish(self):
        ok, _, pub = pt.classify_commit(MSG_1116_SQUASH)
        self.assertTrue(ok)
        self.assertEqual(pub, "2026-11-16")

    def test_1109_squash_is_publish(self):
        ok, _, pub = pt.classify_commit(MSG_1109_SQUASH)
        self.assertTrue(ok)
        self.assertEqual(pub, "2026-11-09")

    def test_rebase_last_commit_is_publish(self):
        ok, _, pub = pt.classify_commit(MSG_1123_REBASE_LAST)
        self.assertTrue(ok)
        self.assertEqual(pub, "2026-11-23")

    def test_merge_title_with_other_owner(self):
        ok, _, pub = pt.classify_commit("Merge pull request #2001 from someone/publish/brief-2026-11-30")
        self.assertTrue(ok)
        self.assertEqual(pub, "2026-11-30")

    def test_non_publish_commits(self):
        for msg in NON_PUBLISH:
            with self.subTest(msg=msg[:50]):
                ok, reason, pub = pt.classify_commit(msg)
                self.assertFalse(ok, reason)
                self.assertIsNone(pub)

    def test_none_message(self):
        ok, _, _ = pt.classify_commit(None)
        self.assertFalse(ok)

    def test_old_rule_would_have_missed_1123(self):
        # 회귀 기록 — 11-16 의 식(startsWith 'publish(web)')은 11-23 을 놓쳤다.
        self.assertFalse(MSG_1123_MERGE.startswith("publish(web)"))
        self.assertTrue(pt.classify_commit(MSG_1123_MERGE)[0])


class DecideTest(unittest.TestCase):
    def test_workflow_run_success_publish(self):
        self.assertTrue(pt.decide("workflow_run", "success", MSG_1123_MERGE)[0])

    def test_workflow_run_failed_deploy_never_sends(self):
        ok, reason, _ = pt.decide("workflow_run", "failure", MSG_1123_MERGE)
        self.assertFalse(ok)
        self.assertIn("failure", reason)

    def test_workflow_run_non_publish(self):
        self.assertFalse(pt.decide("workflow_run", "success", NON_PUBLISH[0])[0])

    def test_schedule_and_dispatch_pass_through(self):
        for ev in ("schedule", "workflow_dispatch"):
            self.assertTrue(pt.decide(ev, "", "")[0])


class CliTest(unittest.TestCase):
    def _run(self, **env):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(buf):
            rc = pt.main([])
        return rc, dict(line.split("=", 1) for line in buf.getvalue().splitlines())

    def test_outputs_publish(self):
        rc, out = self._run(EVENT_NAME="workflow_run", WR_CONCLUSION="success", HEAD_MESSAGE=MSG_1123_MERGE)
        self.assertEqual(rc, 0)
        self.assertEqual(out["is_publish"], "true")
        self.assertEqual(out["publish_date"], "2026-11-23")
        self.assertNotIn("\n", out["reason"])

    def test_outputs_not_publish_exit_0(self):
        rc, out = self._run(EVENT_NAME="workflow_run", WR_CONCLUSION="success",
                            HEAD_MESSAGE="chore: x\n\nline2")
        self.assertEqual(rc, 0)
        self.assertEqual(out["is_publish"], "false")
        self.assertEqual(out["publish_date"], "")


class SendWorkflowWiringTest(unittest.TestCase):
    """grm-newsletter-send.yml 이 판정을 publish_trigger.py 한 곳에 맡기는지(텍스트 계약)."""

    WF = ROOT / ".github" / "workflows" / "grm-newsletter-send.yml"

    @classmethod
    def setUpClass(cls):
        cls.text = cls.WF.read_text(encoding="utf-8")

    def _job(self, name: str) -> str:
        m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][\w-]*:\n|\Z)", self.text, re.S | re.M)
        self.assertIsNotNone(m, f"잡 {name} 없음")
        return m.group(1)

    def test_no_inline_commit_message_rule_left(self):
        # 판정 식이 워크플로에 다시 생기면 두 군데가 갈라진다 — 판정은 publish_trigger.py 뿐.
        self.assertNotIn("startsWith(github.event.workflow_run.head_commit.message", self.text)
        self.assertNotIn("contains(github.event.workflow_run.head_commit.message", self.text)

    def test_trigger_gate_runs_publish_trigger_with_env_message(self):
        gate = self._job("trigger-gate")
        self.assertIn("github.event_name == 'workflow_run'", gate)
        self.assertIn("python3 publish_trigger.py", gate)
        self.assertIn("HEAD_MESSAGE: ${{ github.event.workflow_run.head_commit.message }}", gate)
        self.assertIn("WR_CONCLUSION: ${{ github.event.workflow_run.conclusion }}", gate)
        self.assertIn('"$GITHUB_OUTPUT"', gate)
        # run 블록에 커밋 메시지를 직접 보간하지 않는다(인젝션).
        run_block = gate.split("run: |", 1)[1]
        self.assertNotIn("${{", run_block)

    def test_send_jobs_gate_on_is_publish(self):
        for name in ("validate-build", "newsletter-en"):
            with self.subTest(job=name):
                job = self._job(name)
                self.assertIn("needs: trigger-gate", job)
                self.assertIn("!cancelled()", job)
                self.assertIn("github.event_name != 'workflow_run'", job)
                self.assertIn("needs.trigger-gate.outputs.is_publish == 'true'", job)

    def test_send_job_survives_skipped_gate(self):
        # 예약·수동 실행엔 trigger-gate 가 skipped — send 가 따라 skip 되면 안 된다.
        job = self._job("send")
        self.assertIn("!cancelled()", job)
        self.assertIn("needs.validate-build.result == 'success'", job)
        self.assertIn("needs.validate-build.outputs.should_send == 'true'", job)

    def test_send_serialized_per_job(self):
        self.assertIn("group: grm-newsletter-send-ko", self._job("send"))
        self.assertIn("group: grm-newsletter-send-en", self._job("newsletter-en"))

    def test_workflow_run_runs_not_collapsed_into_one_group(self):
        self.assertIn("github.event.workflow_run.head_sha", self.text)

    def test_still_listens_to_web_deploy(self):
        self.assertIn('workflows: ["GRM Web Deploy"]', self.text)
        self.assertIn("types: [completed]", self.text)

    def test_resolve_treats_workflow_run_as_send(self):
        self.assertIn('[ "$EVENT_NAME" = "workflow_run" ]', self.text)


if __name__ == "__main__":
    unittest.main()
