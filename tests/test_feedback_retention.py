"""문의 1년 보존 삭제 — migration 100 + 주간 워크플로 정적 계약."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "web/migrations/100_feedback_retention.sql").read_text(encoding="utf-8")
WF = (ROOT / ".github/workflows/grm-feedback-retention.yml").read_text(encoding="utf-8")


class MigrationContract(unittest.TestCase):
    def test_additive_only(self):
        body = re.sub(r"--.*", "", SQL)
        self.assertNotRegex(body, r"(?i)\bdrop\s+(table|column|function)")
        self.assertNotRegex(body, r"(?i)\btruncate\b")
        # 유일한 DELETE 는 퍼지 함수 안, 처리 완료 + 기간 조건과 함께.
        self.assertEqual(len(re.findall(r"(?i)\bdelete\s+from\b", body)), 1)
        self.assertIn("status in ('done', 'dismissed')", body)
        self.assertIn("resolved_at < now() - make_interval", body)

    def test_only_service_role_can_purge(self):
        self.assertIn("revoke execute on function public.feedback_purge_resolved(integer) from anon, authenticated;", SQL)
        self.assertIn("grant execute on function public.feedback_purge_resolved(integer) to service_role;", SQL)
        self.assertIn("p_days < 30", SQL)

    def test_unresolved_rows_never_purged(self):
        self.assertIn("resolved_at is not null", SQL)
        self.assertIn("new.resolved_at := null", SQL)


class WorkflowContract(unittest.TestCase):
    def test_schedule_and_rpc(self):
        self.assertIn("cron: '0 19 * * 0'", WF)
        self.assertIn("rpc/feedback_purge_resolved", WF)
        self.assertIn('"p_days":365', WF)
        self.assertIn("permissions: {}", WF)

    def test_no_pii_echo(self):
        self.assertIn('echo "deleted rows: ${deleted}"', WF)


if __name__ == "__main__":
    unittest.main()
