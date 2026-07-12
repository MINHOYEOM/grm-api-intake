#!/usr/bin/env python3
"""015_firm_watchlist.sql -- offline text-contract tests.

Mirrors the style of tests/test_findings_firm_counts_rpc.py (014) and
tests/test_findings_firm_key.py (013): the SQL migration is checked as a text
contract (table shape / RLS own-row policies x3 / no update policy / per-user
cap trigger / anon zero-grant convention from 001), not executed against a
live Postgres connection (no network, no DB -- this CC environment has no
Postgres access; a live Postgres dry-run is the control tower's job).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_MIGRATION_PATH = _MIGRATIONS_DIR / "015_firm_watchlist.sql"


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

    def test_documents_001_provenance_convention(self) -> None:
        # 001 관례 계승 명시(불투명 키만 저장·원문 미저장) — 주석 계약.
        self.assertIn("001_reaction.sql", self.sql)
        self.assertIn("불투명", self.sql)

    def test_documents_004_pitfall_not_applicable_note(self) -> None:
        # ★004 함정(plpgsql 루프변수-별칭 충돌) — 이 파일엔 FOR 루프가 없으므로
        # "해당 없음" 사실이 주석으로 명시돼 있어야 한다(작업 지시 계약).
        self.assertIn("004", self.sql)
        self.assertIn("해당 없음", self.sql)

    def test_notification_job_deferred_note(self) -> None:
        # 이 마이그레이션은 워치리스트 1/2(저장 계층)만 — 통지는 후속 PR 명시.
        self.assertIn("후속", self.sql)


class TableShapeTest(unittest.TestCase):
    """public.firm_watchlist -- 컬럼/PK/FK 계약."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        match = re.search(
            r"create table if not exists public\.firm_watchlist \((.*?)\);",
            self.code,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate firm_watchlist table body")
        self.body = match.group(1)

    def test_user_id_references_auth_users_cascade(self) -> None:
        self.assertRegex(
            self.body,
            r"user_id\s+uuid not null references auth\.users\(id\) on delete cascade",
        )

    def test_firm_key_opaque_text_not_null(self) -> None:
        self.assertRegex(self.body, r"firm_key\s+text not null")

    def test_firm_display_snapshot_default_empty(self) -> None:
        self.assertRegex(self.body, r"firm_display\s+text not null default ''")

    def test_created_at_timestamptz_default_now(self) -> None:
        self.assertRegex(self.body, r"created_at\s+timestamptz not null default now\(\)")

    def test_primary_key_user_firm(self) -> None:
        self.assertIn("primary key (user_id, firm_key)", self.body)

    def test_no_fact_or_original_text_columns(self) -> None:
        # provenance(001): 업체 사실·지적사항 원문·원문 URL 컬럼이 없어야 한다.
        for field in ("finding_text", "finding_text_ko", "evidence_url", "raw_json",
                      "firm_name", "url"):
            self.assertNotRegex(
                self.body, rf"(?m)^\s*{field}\s",
                f"{field!r} column leaked into firm_watchlist",
            )


class RlsPolicyTest(unittest.TestCase):
    """RLS enable + 본인 행만 select/insert/delete 3종(001 동형). update 정책 없음."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_rls_enabled(self) -> None:
        self.assertIn(
            "alter table public.firm_watchlist enable row level security;", self.code
        )

    def test_three_own_policies(self) -> None:
        self.assertIn(
            "create policy firm_watchlist_select_own on public.firm_watchlist "
            "for select using (auth.uid() = user_id);",
            self.code,
        )
        self.assertIn(
            "create policy firm_watchlist_insert_own on public.firm_watchlist "
            "for insert with check (auth.uid() = user_id);",
            self.code,
        )
        self.assertIn(
            "create policy firm_watchlist_delete_own on public.firm_watchlist "
            "for delete using (auth.uid() = user_id);",
            self.code,
        )

    def test_policies_dropped_before_created_idempotent(self) -> None:
        for name in ("select_own", "insert_own", "delete_own"):
            drop = f"drop policy if exists firm_watchlist_{name} on public.firm_watchlist;"
            self.assertIn(drop, self.code)
            self.assertLess(self.code.index(drop),
                            self.code.index(f"create policy firm_watchlist_{name}"))

    def test_no_update_policy_or_grant(self) -> None:
        # 등록/해제만 있는 모델 — update 는 정책도 grant 도 없어야 한다(이중 봉쇄).
        self.assertNotIn("for update", self.code.lower())
        self.assertNotRegex(self.code.lower(), r"grant[^;]*\bupdate\b")


class CapTriggerTest(unittest.TestCase):
    """사용자당 상한 50 -- before insert 트리거(초과 시 raise exception, 메시지에 상한)."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"create or replace function private\.enforce_firm_watchlist_cap\(\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate enforce_firm_watchlist_cap body")
        self.body = match.group(1)

    def test_signature_security_definer_search_path_pinned(self) -> None:
        self.assertIn(
            "create or replace function private.enforce_firm_watchlist_cap()"
            "\nreturns trigger\nlanguage plpgsql\nsecurity definer"
            "\nset search_path = public",
            self.sql,
        )

    def test_counts_own_rows_and_caps_at_50(self) -> None:
        self.assertIn(
            "select count(*) from public.firm_watchlist where user_id = new.user_id",
            self.body,
        )
        self.assertIn(">= 50", self.body)

    def test_raise_exception_message_states_cap(self) -> None:
        self.assertIn("raise exception", self.body)
        m = re.search(r"raise exception '([^']*)'", self.body)
        self.assertIsNotNone(m)
        self.assertIn("50", m.group(1))

    def test_no_for_loop_004_pitfall_path_absent(self) -> None:
        # ★004 함정 경로(FOR 루프변수-별칭 충돌) 자체가 없어야 한다 -- 단일 if-count 검사.
        self.assertNotRegex(self.body.lower(), r"\bfor\s+\w+\s+in\b")
        self.assertNotIn("declare", self.body.lower())

    def test_before_insert_trigger_wired(self) -> None:
        code = _strip_sql_comments(self.sql)
        self.assertIn(
            "drop trigger if exists firm_watchlist_cap_before_insert on public.firm_watchlist;",
            code,
        )
        self.assertIn(
            "create trigger firm_watchlist_cap_before_insert"
            "\nbefore insert on public.firm_watchlist"
            "\nfor each row execute function private.enforce_firm_watchlist_cap();",
            self.sql,
        )

    def test_trigger_function_execute_revoked(self) -> None:
        for role in ("public", "anon", "authenticated"):
            self.assertIn(
                f"revoke all on function private.enforce_firm_watchlist_cap() from {role};",
                self.sql,
            )


class GrantsTest(unittest.TestCase):
    """anon 무권한(001 revoke 관례) + authenticated 는 select/insert/delete 만."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_revoke_then_grant_order(self) -> None:
        for role in ("public", "anon", "authenticated"):
            self.assertIn(f"revoke all on public.firm_watchlist from {role};", self.code)
        grant = "grant select, insert, delete on public.firm_watchlist to authenticated;"
        self.assertIn(grant, self.code)
        self.assertLess(
            self.code.index("revoke all on public.firm_watchlist from public;"),
            self.code.index(grant),
        )

    def test_no_grant_to_anon(self) -> None:
        self.assertNotRegex(self.code.lower(), r"grant[^;]*\bto anon\b")

    def test_no_existing_objects_redefined(self) -> None:
        # 015 는 001~014 의 기존 테이블/함수/뷰를 전혀 건드리지 않는다.
        for obj in (
            "public.reaction", "public.reaction_count", "public.heart_counts",
            "public.findings", "private.sync_reaction_count",
            "findings_firm_profile", "grm_normalize_firm_name",
        ):
            self.assertNotRegex(
                self.code,
                rf"(create|alter|drop)[^;]*\b{re.escape(obj)}\b",
                f"015 must not touch {obj}",
            )


class SourceOfTruthExistsTest(unittest.TestCase):
    def test_prerequisite_migrations_exist(self) -> None:
        for name in ("001_reaction.sql", "013_findings_firm_key.sql"):
            path = _MIGRATIONS_DIR / name
            self.assertTrue(path.is_file(), f"missing {path}")


if __name__ == "__main__":
    unittest.main()
