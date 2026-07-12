#!/usr/bin/env python3
"""016_watchlist_notify_log.sql -- offline text-contract tests.

Mirrors tests/test_firm_watchlist.py(015)'s style: the SQL migration is
checked as a text contract (table shape / RLS enabled + zero policies /
anon-authenticated zero-grant / 004-009 pitfall notes), not executed against
a live Postgres connection (no network, no DB in this CC environment -- a
live Postgres dry-run is the control tower's job).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_MIGRATION_PATH = _MIGRATIONS_DIR / "016_watchlist_notify_log.sql"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


class MigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}")
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_004_pitfall_not_applicable_note(self) -> None:
        self.assertIn("004", self.sql)
        self.assertIn("해당 없음", self.sql)

    def test_documents_009_pitfall_not_applicable_note(self) -> None:
        self.assertIn("009", self.sql)
        # 009 절 역시 "해당 없음"으로 명시(004 와 같은 문구 반복 허용).
        self.assertIn("해당 없음", self.sql)

    def test_references_015_prior_layer(self) -> None:
        self.assertIn("015_firm_watchlist.sql", self.sql)


class TableShapeTest(unittest.TestCase):
    """public.firm_watch_notification_log -- 컬럼/PK/FK 계약."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        match = re.search(
            r"create table if not exists public\.firm_watch_notification_log \((.*?)\);",
            self.code,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate firm_watch_notification_log table body")
        self.body = match.group(1)

    def test_user_id_uuid_not_null(self) -> None:
        self.assertRegex(self.body, r"user_id\s+uuid not null")

    def test_finding_id_text_not_null(self) -> None:
        self.assertRegex(self.body, r"finding_id\s+text not null")

    def test_sent_at_timestamptz_default_now(self) -> None:
        self.assertRegex(self.body, r"sent_at\s+timestamptz not null default now\(\)")

    def test_primary_key_user_finding(self) -> None:
        self.assertIn("primary key (user_id, finding_id)", self.body)

    def test_user_id_references_auth_users_cascade(self) -> None:
        self.assertRegex(
            self.body,
            r"user_id\s+uuid not null references auth\.users\(id\) on delete cascade",
        )

    def test_finding_id_references_findings_cascade(self) -> None:
        self.assertRegex(
            self.body,
            r"finding_id\s+text not null references public\.findings\(finding_id\) on delete cascade",
        )

    def test_no_fact_or_original_text_columns(self) -> None:
        # provenance: 원문/사실 컬럼이 로그 테이블에 있어선 안 된다(멱등 키+타임스탬프뿐).
        for field in ("finding_text", "finding_text_ko", "evidence_url", "firm_name",
                     "firm_display", "url"):
            self.assertNotRegex(
                self.body, rf"(?m)^\s*{field}\s",
                f"{field!r} column leaked into firm_watch_notification_log",
            )


class RlsZeroPolicyTest(unittest.TestCase):
    """RLS enable 되지만 정책은 0개(service_role 전용 테이블)."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_rls_enabled(self) -> None:
        self.assertIn(
            "alter table public.firm_watch_notification_log enable row level security;",
            self.code,
        )

    def test_no_create_policy_statements(self) -> None:
        self.assertNotIn("create policy", self.code.lower())


class GrantsTest(unittest.TestCase):
    """anon/authenticated/public 무권한(001/015 revoke 관례)."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_revoke_all_from_all_client_roles(self) -> None:
        for role in ("public", "anon", "authenticated"):
            self.assertIn(
                f"revoke all on public.firm_watch_notification_log from {role};", self.code
            )

    def test_no_grant_statements_at_all(self) -> None:
        # service_role 전용 -- 어떤 client 역할에도 재부여(grant)하지 않는다.
        self.assertNotIn("grant ", self.code.lower())

    def test_no_existing_objects_redefined(self) -> None:
        # public.findings 는 finding_id FK 참조로만 등장(정당한 참조 -- CREATE TABLE
        # 대상은 아니다)하므로 이 목록에서 제외한다. 나머지 기존 객체는 전혀 건드리지 않아야
        # 한다(create/alter/drop 대상이 아님).
        for obj in (
            "public.firm_watchlist", "public.reaction", "public.reaction_count",
            "public.raw_signals", "private.sync_reaction_count",
            "private.enforce_firm_watchlist_cap",
        ):
            self.assertNotRegex(
                self.code,
                rf"(create|alter|drop)[^;]*\b{re.escape(obj)}\b",
                f"016 must not touch {obj}",
            )

    def test_findings_only_referenced_via_fk_not_redefined(self) -> None:
        # public.findings 는 이 파일에서 오직 "references public.findings(...)" FK 대상
        # 으로만 등장해야 한다 -- "table public.findings" 형태(create/alter/drop table 의
        # 직접 대상)로는 절대 나타나면 안 된다("references public.findings" 는 이 패턴에
        # 걸리지 않는다 -- "table" 바로 뒤가 아니라 "references" 바로 뒤이므로).
        self.assertNotIn("table public.findings", self.code.lower())
        self.assertIn("references public.findings(finding_id)", self.code.lower())


class IndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.code = _strip_sql_comments(_MIGRATION_PATH.read_text(encoding="utf-8"))

    def test_finding_id_index_present(self) -> None:
        self.assertIn(
            "create index if not exists firm_watch_notification_log_finding_idx\n"
            "  on public.firm_watch_notification_log (finding_id);",
            self.code,
        )


class SourceOfTruthExistsTest(unittest.TestCase):
    def test_prerequisite_migrations_exist(self) -> None:
        for name in ("001_reaction.sql", "013_findings_firm_key.sql", "015_firm_watchlist.sql"):
            path = _MIGRATIONS_DIR / name
            self.assertTrue(path.is_file(), f"missing {path}")


if __name__ == "__main__":
    unittest.main()
