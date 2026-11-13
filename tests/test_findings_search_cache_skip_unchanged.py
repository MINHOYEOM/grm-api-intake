#!/usr/bin/env python3
"""156_findings_search_cache_skip_unchanged.sql — 검색 캐시 갱신의 "같은 답이면 payload 를 다시 쓰지 않기" 정적 계약.

지키려는 것(어느 하나가 조용히 뒤집히면 용량 절감이 사라지거나, 낡은 바이트를 내보내거나, 캐시가 통째로 낡는다):
  · 151 의 refresh_row 시그니처·반환형·security invoker·search_path·권한 그대로, 다른 함수·표는 건드리지 않는다
  · 본문은 151 과 같고 update 한 곳만 두 갈래로 바뀐다(몸통 복제 표류 방지)
  · 비교는 jsonb 의미 비교가 아니라 text 바이트 비교(`payload::text is not distinct from v::text`)
  · 같은 답 갈래는 payload 를 SET 에 넣지 않되 refreshed_at·computed_ms·last_error 는 종전대로 갱신
  · 다른 답 갈래(if not found)는 종전 update 그대로
  · 표 재작성(VACUUM FULL 등)·DML·grant 없음, 머리 주석에 되돌림 전문(=151 본문)·검증법
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _ROOT / "web" / "migrations"
_PATH = _MIGRATIONS / "156_findings_search_cache_skip_unchanged.sql"
_PREV = _MIGRATIONS / "151_findings_search_cache_batched_refresh.sql"

_SIG = "create or replace function public.findings_search_cache_refresh_row(p_args jsonb)"
_HEAD = ("create or replace function public.findings_search_cache_refresh_row(p_args jsonb)\n"
         "returns text\n"
         "language plpgsql security invoker set search_path to 'public', 'extensions' as $$")
_OLD_UPDATE = ("update public.findings_search_cache "
               "set payload = v, computed_ms = ms, refreshed_at = clock_timestamp(), last_error = null "
               "where args = p_args;")


def _strip(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def _header(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        if not line.strip().startswith("--"):
            break
        lines.append(line)
    return "\n".join(lines)


def _slice_function(code: str, signature: str) -> str:
    start = code.index(signature)
    end = code.index("$$;", start) + len("$$;")
    return code[start:end]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


class SkipUnchangedMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_PATH.is_file(), f"missing {_PATH}")
        self.raw = _PATH.read_bytes()
        self.sql = self.raw.decode("utf-8")
        self.code = _strip(self.sql)
        self.head = _header(self.sql)
        self.fn = _slice_function(self.code, _SIG)
        self.prev_fn = _slice_function(_strip(_PREV.read_text(encoding="utf-8")), _SIG)

    # ── 번호·형식 ──────────────────────────────────────────────────────────────
    def test_number_unique_and_follows_155(self) -> None:
        names = sorted(p.name for p in _MIGRATIONS.glob("*.sql"))
        self.assertEqual([n for n in names if n.startswith("156_")], [_PATH.name])
        self.assertTrue(any(n.startswith("155_") for n in names))

    def test_lf_only(self) -> None:
        self.assertNotIn(b"\r\n", self.raw)

    # ── 범위: 함수 하나만, 시그니처·보안 속성 불변 ───────────────────────────────
    def test_defines_only_refresh_row(self) -> None:
        defs = re.findall(r"create\s+(?:or\s+replace\s+)?function\s+([\w.]+)\(", self.code, flags=re.I)
        self.assertEqual(defs, ["public.findings_search_cache_refresh_row"])

    def test_signature_and_security_identical_to_151(self) -> None:
        self.assertTrue(self.fn.startswith(_HEAD), self.fn[:300])
        self.assertTrue(self.prev_fn.startswith(_HEAD), "151 머리가 바뀌었다 — 대조 기준 확인")
        self.assertNotIn("security definer", self.fn.lower())

    def test_no_ddl_dml_grant_or_table_rewrite(self) -> None:
        low = self.code.lower()
        for bad in ("vacuum", "cluster ", "pg_repack", "alter table", "create table", "drop ",
                    "insert into", "delete from", "truncate", "grant ", "cron.", "notify "):
            self.assertNotIn(bad, low, bad)

    def test_privileges_restated_not_widened(self) -> None:
        self.assertIn("revoke execute on function public.findings_search_cache_refresh_row(jsonb) "
                      "from public, anon, authenticated;", self.code)
        self.assertEqual(self.code.count("revoke "), 1)

    def test_readers_untouched(self) -> None:
        for name in ("findings_search_cache_read(", "function public.findings_search(",
                     "findings_search_cache_refresh(", "findings_search_cache_refresh_batch(",
                     "findings_search_cache_status(", "findings_search_cache_job_status("):
            self.assertNotIn(name, self.code, name)

    # ── 본문: 151 과 같고 update 한 곳만 두 갈래 ──────────────────────────────────
    def test_body_is_151_except_the_update(self) -> None:
        new, old = _norm(self.fn), _norm(self.prev_fn)
        self.assertEqual(old.count(_norm(_OLD_UPDATE)), 1)
        start = new.index("update public.findings_search_cache set computed_ms")
        end = new.index("end if;", start) + len("end if;")
        self.assertEqual(new[:start] + _norm(_OLD_UPDATE) + new[end:], old)

    def test_compares_text_bytes_not_jsonb(self) -> None:
        body = _norm(self.fn)
        self.assertIn("and payload::text is not distinct from v::text;", body)
        self.assertNotRegex(body, r"payload\s+is\s+(not\s+)?distinct\s+from\s+v\b(?!::)")
        self.assertNotRegex(body, r"payload\s*=\s*v\b(?!::)\s*(and|;|\))")  # where 절 jsonb '='
        self.assertNotIn("md5(", body)

    def test_same_answer_branch_skips_payload_but_keeps_freshness(self) -> None:
        body = _norm(self.fn)
        first = body[body.index("update public.findings_search_cache set computed_ms"):]
        first = first[:first.index(";") + 1]
        set_clause = first[:first.index(" where ")]
        self.assertNotIn("payload", set_clause)
        for col in ("computed_ms = ms", "refreshed_at = clock_timestamp()", "last_error = null"):
            self.assertIn(col, set_clause, col)
        self.assertIn("where args = p_args and payload::text is not distinct from v::text;", first)

    def test_changed_answer_branch_is_the_old_update_under_not_found(self) -> None:
        body = _norm(self.fn)
        i = body.index("if not found then")
        rest = body[i:]
        self.assertTrue(rest.startswith("if not found then " + _norm(_OLD_UPDATE) + " end if; return null;"), rest[:300])
        self.assertEqual(body.count("set payload = v"), 1)
        self.assertLess(body.index("payload::text is not distinct from"), i, "비교 갈래가 먼저")

    def test_error_path_unchanged(self) -> None:
        tail = _norm(self.fn)[_norm(self.fn).index("exception when others then"):]
        self.assertEqual(tail, _norm(self.prev_fn)[_norm(self.prev_fn).index("exception when others then"):])

    # ── 머리 주석: 되돌림 전문·검증법·재작성은 텍스트만 ──────────────────────────
    def test_header_rollback_is_full_151_definition(self) -> None:
        self.assertIn("되돌리기", self.head)
        lines = self.head.splitlines()
        start = next(i for i, l in enumerate(lines) if l.strip() == "--   " + _SIG)
        body = []
        for l in lines[start:]:
            body.append(re.sub(r"^--   ?", "", l))
            if l.strip() == "--   $$;":
                break
        self.assertEqual(_norm("\n".join(body)), _norm(self.prev_fn))
        self.assertIn("revoke execute on function public.findings_search_cache_refresh_row(jsonb) "
                      "from public, anon, authenticated;", self.head)

    def test_header_has_measurements_and_verification(self) -> None:
        for token in ("34,234,368", "18,444,878", "n_tup_upd", "n_dead_tup", "n_tup_ins",
                      "md5(payload::text)", "set local role anon", "rollback;", "routine_privileges",
                      "VACUUM FULL", "적용 순서", "1.00"):
            self.assertIn(token, self.head, token)

    def test_header_explains_why_refreshed_at_still_moves(self) -> None:
        for reader in ("findings_search_cache_read", "refresh_batch", "findings_search_cache_health.py"):
            self.assertIn(reader, self.head, reader)


if __name__ == "__main__":
    unittest.main()
