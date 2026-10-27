"""발행 전 FDA 본문 게이트 배선 회귀 — 워크플로 사이의 이름·입력·안전 조건이 어긋나지 않게.

워크플로끼리는 문자열로만 연결된다(workflow_run 의 워크플로 이름, dispatch 입력값, 이슈 제목).
한쪽만 바뀌면 게이트가 **조용히 안 돈다** — 그래서 이름 대조를 테스트로 고정한다.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backfill_483_ocr_recovery as rec

WF = os.path.join(ROOT, ".github", "workflows")


def _load(name):
    with open(os.path.join(WF, name), encoding="utf-8") as fh:
        return fh.read()


def _step(text, step_id):
    """`id: <step_id>` 로 시작하는 스텝 블록(다음 `- name:` 전까지). CI 에 PyYAML 이 없다."""
    m = re.search(rf"\n\s+id: {step_id}\n(.*?)(?=\n      - name:|\Z)", text, re.S)
    assert m, step_id
    return m.group(1)


class GateWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate_text = _load("grm-fda-coverage-gate.yml")
        cls.rec_text = _load("grm-fda483-ocr-recovery.yml")
        cls.intake_text = _load("grm-intake.yml")

    def test_triggered_by_intake_workflow_name(self):
        intake_name = re.search(r"^name: (.+)$", self.intake_text, re.M).group(1).strip()
        names = re.search(r"workflows: \[(.+)\]", self.gate_text).group(1)
        self.assertEqual(names, f'"{intake_name}"')

    def test_only_scheduled_main_runs_auto_trigger(self):
        self.assertIn("github.event.workflow_run.event == 'schedule'", self.gate_text)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", self.gate_text)

    def test_recovery_dispatch_uses_existing_mode(self):
        m = re.search(r"-f mode=(\S+)", self.gate_text)
        self.assertIsNotNone(m)
        mode = m.group(1)
        self.assertIn(mode, rec.RECOVERY_MODES)
        self.assertIn(f"          - {mode}\n", self.rec_text)        # dispatch choice 옵션
        for key in re.findall(r"-f (\w+)=", _step(self.gate_text, "recover")):
            self.assertRegex(self.rec_text, rf"\n      {key}:\n        description:", key)

    def test_regeneration_requires_open_handoff_and_time(self):
        regen = _step(self.gate_text, "regen")
        self.assertIn("if: steps.handoff.outputs.handoff_status == 'New'", regen)
        self.assertIn("0700", regen)
        self.assertIn("gh workflow run grm-intake.yml", regen)

    def test_schedule_recovery_uses_recoverable(self):
        self.assertIn("github.event_name == 'schedule' && 'recoverable'", self.rec_text)
        self.assertNotIn("github.event_name == 'schedule' && 'collection-capped'", self.rec_text)

    def test_issue_title_shared_with_watchdog(self):
        title = re.search(r"const title = '(GRM FDA 본문 결손[^']+)'", self.gate_text).group(1)
        wd = _load("grm-publish-watchdog.yml")
        self.assertIn(f"const title = '{title}'", wd)
        self.assertIn("fda_body_coverage.py check", wd)

    def test_publish_workflow_surfaces_coverage(self):
        pub = _load("grm-web-publish.yml")
        self.assertIn("fda_body_coverage.py check", pub)
        self.assertIn('-b "$BODY"', pub)

    def test_admin_center_label(self):
        """Admin 운영 상태 센터는 automation-warning 라벨 이슈만 읽는다."""
        self.assertIn("'automation-warning', 'grm-fda-coverage'", self.gate_text)
        self.assertIn("'automation-warning', 'grm-fda-coverage'", self.rec_text)


if __name__ == "__main__":
    unittest.main()


class NonBlockingTest(unittest.TestCase):
    """가시화·백스톱 스텝의 실패가 발행 PR 생성이나 워치독 본 경보를 막지 않는다."""

    def test_publish_and_watchdog_steps_continue_on_error(self):
        for name in ("grm-web-publish.yml", "grm-publish-watchdog.yml"):
            block = _step(_load(name), "fdacov")
            self.assertIn("continue-on-error: true", block, name)
