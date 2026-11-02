#!/usr/bin/env python3
"""account-delete (098/099 + Edge Function) -- offline text-contract tests.

No DB / Deno in CI here; the SQL and TypeScript are checked as text contracts:
no personal data in the log table, service_role-only privileges, deploy wiring.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LOG = _ROOT / "web" / "migrations" / "098_account_deletion_log.sql"
_SCRUB = _ROOT / "web" / "migrations" / "099_account_scrub_feedback.sql"
_FN = _ROOT / "supabase" / "functions" / "account-delete" / "index.ts"
_WF = _ROOT / ".github" / "workflows" / "grm-admin-backend-deploy.yml"
_CFG = _ROOT / "supabase" / "config.toml"


def _code(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


class LogMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _code(_LOG.read_text(encoding="utf-8"))

    def test_no_personal_columns(self) -> None:
        cols = re.findall(r"^\s+([a-z_]+)\s+(?:bigint|text|boolean|jsonb|timestamptz|uuid)",
                          self.sql, re.M)
        for bad in ("email", "user_id", "ip", "ip_address", "hash"):
            self.assertNotIn(bad, cols)

    def test_rls_and_revokes(self) -> None:
        self.assertIn("enable row level security", self.sql)
        for role in ("public", "anon", "authenticated"):
            self.assertIn(f"revoke all on public.account_deletion_log from {role}", self.sql)
        self.assertIn("private.is_admin()", self.sql)
        self.assertNotRegex(self.sql, r"grant\s+(insert|update|delete)")


class ScrubMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _code(_SCRUB.read_text(encoding="utf-8"))

    def test_clears_email_and_consent_together(self) -> None:
        self.assertIn("email = null", self.sql)
        self.assertIn("contact_consent = false", self.sql)
        self.assertNotIn("delete from", self.sql)  # body is kept

    def test_service_role_only(self) -> None:
        self.assertIn("security definer", self.sql)
        self.assertIn("from anon, authenticated", self.sql)
        self.assertIn("to service_role", self.sql)
        self.assertNotRegex(self.sql, r"grant\s+execute[^;]*\bto\s+(anon|authenticated|public)")


class FunctionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ts = _FN.read_text(encoding="utf-8")

    def test_email_comes_from_jwt_not_body(self) -> None:
        self.assertNotIn("req.json", self.ts)
        self.assertIn("user.email", self.ts)

    def test_guards_present(self) -> None:
        for token in ("reauth_required", "admin_protected", "REAUTH_WINDOW_SEC",
                      "account_scrub_feedback", "deleteUser"):
            self.assertIn(token, self.ts)

    def test_order_brevo_then_feedback_then_auth(self) -> None:
        handler = self.ts[self.ts.index("Deno.serve"):]
        j = [handler.index(k) for k in ("brevoDelete(email)", "account_scrub_feedback", "deleteUser(")]
        self.assertEqual(j, sorted(j))

    def test_never_logs_email(self) -> None:
        self.assertNotRegex(self.ts, r"console\.(log|error|warn)")
        log_call = re.search(r"logResult\([^)]*\)", self.ts)
        self.assertNotIn("email", log_call.group(0))
        self.assertNotIn("user.id", log_call.group(0))

    def test_brevo_404_is_success(self) -> None:
        self.assertIn("res.status === 404", self.ts)


class WiringTest(unittest.TestCase):
    def test_config_and_workflow(self) -> None:
        self.assertIn("[functions.account-delete]", _CFG.read_text(encoding="utf-8"))
        wf = _WF.read_text(encoding="utf-8")
        self.assertIn("supabase/functions/account-delete/index.ts", wf)
        self.assertIn("supabase functions deploy account-delete", wf)

    def test_no_crlf(self) -> None:
        for p in (_LOG, _SCRUB, _FN):
            self.assertNotIn(b"\r\n", p.read_bytes())


if __name__ == "__main__":
    unittest.main()
