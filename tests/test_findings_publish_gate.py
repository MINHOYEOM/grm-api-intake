#!/usr/bin/env python3
"""FIND-1 M9a public read gate migration tests (006_findings_publish_gate.sql).

Offline source-text checks only -- no network, no real Postgres/sqlite connection.
Mirrors the style of the 003/004/005 migration tests in test_findings_supabase.py.
"""

from __future__ import annotations

import unittest
from pathlib import Path


_GATE_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "006_findings_publish_gate.sql"
)
_PUBLIC_READ_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "003_findings_public_read.sql"
)


class PublishGateMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_GATE_MIGRATION_PATH.is_file(), f"missing {_GATE_MIGRATION_PATH}")
        self.sql = _GATE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _GATE_MIGRATION_PATH.read_bytes())

    def test_drops_then_recreates_named_policy_idempotently(self) -> None:
        self.assertIn("drop policy if exists findings_public_read on public.findings;", self.sql)
        self.assertIn("create policy findings_public_read", self.sql)
        # Exactly one drop + one create -- re-running this file must not error or duplicate.
        self.assertEqual(self.sql.count("drop policy if exists findings_public_read"), 1)
        self.assertEqual(self.sql.count("create policy findings_public_read"), 1)

    def test_policy_targets_findings_select_for_anon_and_authenticated(self) -> None:
        self.assertIn("on public.findings", self.sql)
        self.assertIn("for select", self.sql)
        self.assertIn("to anon, authenticated", self.sql)

    def test_policy_condition_gates_on_translation_or_ko_language(self) -> None:
        self.assertIn(
            "using (finding_text_ko <> '' or finding_language = 'KO');",
            self.sql,
        )

    def test_does_not_touch_raw_signals(self) -> None:
        self.assertNotIn("raw_signals enable row level security", self.sql)
        self.assertNotIn("grant select on public.raw_signals", self.sql)
        self.assertNotIn("create policy", self.sql.replace("create policy findings_public_read", "", 1))

    def test_does_not_regrant_select(self) -> None:
        # 003 already granted select on public.findings to anon/authenticated -- this
        # migration only replaces the policy, it must not re-issue the grant.
        self.assertNotIn("grant select", self.sql)

    def test_does_not_touch_finding_text_or_reclassify_rows(self) -> None:
        self.assertNotIn("update public.findings", self.sql.lower())
        self.assertNotIn("alter table public.findings", self.sql.lower())

    def test_mentions_service_role_bypass_note(self) -> None:
        self.assertIn("service_role", self.sql)

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 5)


class PublicReadMigrationSupersededTest(unittest.TestCase):
    """003 remains valid history (its `using (true)` policy is the pre-M9a baseline that
    006 replaces at apply time) -- this just pins that 003 is unmodified by this sprint."""

    def test_003_still_uses_permissive_true_policy(self) -> None:
        self.assertTrue(_PUBLIC_READ_MIGRATION_PATH.is_file())
        sql = _PUBLIC_READ_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("using (true)", sql)


if __name__ == "__main__":
    unittest.main()
