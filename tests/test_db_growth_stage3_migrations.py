#!/usr/bin/env python3
"""DB 용량 3단계(2026-11-20) migration 162·163·164 정적 계약. 165 는 tests/test_db_size_health.py 가 본다.

지키려는 것:
  · 162 findings_similar_cache_refresh — 시그니처·security invoker·search_path·권한 그대로, 158 의 같은 답 건너뛰기·
    14일 prune 유지, targets() 는 한 번만, 대상 밖 prune 은 1일 유예 + targets 가 비면 건너뜀, 반환은 종전 키 + 가산 키
  · 163 cron.job_run_details 7일 보존 — 이름 있는 cron 작업 하나(재등록 멱등), end_time null 행도 판정
  · 164 findings 무변경 UPDATE 건너뛰기 — BEFORE UPDATE 행 트리거, 생성 열 제외 후 jsonb ::text 비교(의미 비교 금지),
    같으면 return null, 표 열·RLS·다른 트리거 무변경, anon 실행 권한 없음
  · 세 파일 모두 LF·번호 유일·머리 주석에 되돌리기·검증법

뮤테이션 예: 162 에서 `if n_target > 0` 를 지우거나 '1 day' 를 빼면, 164 에서 `- v_gen` 을 지우거나 `::text` 비교를
jsonb `=` 로 바꾸면 해당 시험이 깨진다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIG = _ROOT / "web" / "migrations"
_P162 = _MIG / "162_findings_similar_cache_prune_off_target.sql"
_P163 = _MIG / "163_cron_job_run_details_retention.sql"
_P164 = _MIG / "164_findings_skip_unchanged_update.sql"
_P158 = _MIG / "158_findings_similar_cache_skip_unchanged.sql"


def _strip(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def _header(sql: str) -> str:
    out = []
    for line in sql.splitlines():
        if not line.strip().startswith("--"):
            break
        out.append(line)
    return "\n".join(out)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _fn(code: str, sig: str) -> str:
    start = code.index(sig)
    return code[start:code.index("$$;", start) + 3]


class CommonTest(unittest.TestCase):
    def test_files_unique_lf_and_documented(self) -> None:
        names = sorted(p.name for p in _MIG.glob("*.sql"))
        for p in (_P162, _P163, _P164):
            self.assertTrue(p.is_file(), p)
            prefix = p.name[:4]
            self.assertEqual([n for n in names if n.startswith(prefix)], [p.name])
            raw = p.read_bytes()
            self.assertNotIn(b"\r\n", raw)
            head = _header(raw.decode("utf-8"))
            self.assertIn("되돌리기", head)
            self.assertIn("검증법", head)
            code = _norm(_strip(raw.decode("utf-8")))
            self.assertNotIn("to anon", code)
            self.assertNotIn("grant ", code)
            self.assertNotIn("vacuum", code)


class SimilarCachePruneTest(unittest.TestCase):
    SIG = "create or replace function public.findings_similar_cache_refresh("

    def setUp(self) -> None:
        self.code = _strip(_P162.read_text(encoding="utf-8"))
        self.fn = _fn(self.code, self.SIG)
        self.prev = _fn(_strip(_P158.read_text(encoding="utf-8")), self.SIG)

    def test_only_this_function(self) -> None:
        self.assertEqual(len(re.findall(r"create\s+or\s+replace\s+function", self.code, re.I)), 1)
        self.assertNotRegex(self.code.lower(), r"\b(alter|drop|create)\s+table\b")

    def test_signature_and_security_unchanged(self) -> None:
        head = ("create or replace function public.findings_similar_cache_refresh(\n"
                "  p_budget_ms integer default 20000,\n"
                "  p_max_rows  integer default 200\n"
                ") returns jsonb\n"
                "language plpgsql security invoker set search_path to 'public', 'extensions' as $$")
        self.assertTrue(self.fn.startswith(head))
        self.assertTrue(self.prev.startswith(head))
        self.assertIn("revoke execute on function public.findings_similar_cache_refresh(integer, integer) "
                      "from public, anon, authenticated;", self.code)

    def test_targets_called_once(self) -> None:
        self.assertEqual(self.fn.count("findings_similar_cache_targets()"), 1)
        self.assertEqual(self.prev.count("findings_similar_cache_targets()"), 2)
        self.assertIn("from unnest(v_targets) as t(finding_id)", self.fn)

    def test_158_skip_unchanged_and_14d_prune_kept(self) -> None:
        n = _norm(self.fn)
        self.assertIn("and payload::text is not distinct from v::text;", n)
        self.assertIn("on conflict (finding_id) do update set payload = excluded.payload", n)
        self.assertIn("where s.refreshed_at < now() - interval '14 days'", n)
        self.assertIn("s.refreshed_at < now() - interval '3 days'", n)

    def test_off_target_prune_guarded(self) -> None:
        n = _norm(self.fn)
        self.assertIn("if n_target > 0 then", n)
        self.assertIn("where s.refreshed_at < now() - interval '1 day' and not exists "
                      "(select 1 from unnest(v_targets) as u(finding_id) where u.finding_id = s.finding_id)", n)

    def test_return_keys_additive(self) -> None:
        n = _norm(self.fn)
        for key in ("'target'", "'computed'", "'error'", "'pruned'", "'elapsed_ms'", "'pruned_off_target'"):
            self.assertIn(key, n)
        for key in ("'target'", "'computed'", "'error'", "'pruned'", "'elapsed_ms'"):
            self.assertIn(key, _norm(self.prev))


class CronRetentionTest(unittest.TestCase):
    def test_single_named_job(self) -> None:
        code = _norm(_strip(_P163.read_text(encoding="utf-8")))
        self.assertEqual(code.count("cron.schedule("), 1)
        self.assertIn("select cron.schedule('grm-cron-history-retention', '17 0 * * *',", code)
        self.assertIn("delete from cron.job_run_details where coalesce(end_time, start_time) < now() - interval '7 days'",
                      code)
        self.assertNotIn("create or replace function", code)
        self.assertNotRegex(code, r"\b(alter|drop|create)\s+table\b")

    def test_name_not_taken(self) -> None:
        hits = [p.name for p in _MIG.glob("*.sql")
                if "grm-cron-history-retention" in _strip(p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [_P163.name])


class SkipUnchangedTriggerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.code = _strip(_P164.read_text(encoding="utf-8"))
        self.n = _norm(self.code)

    def test_trigger_shape(self) -> None:
        self.assertIn("drop trigger if exists zz_findings_skip_unchanged_bu on public.findings;", self.n)
        self.assertIn("create trigger zz_findings_skip_unchanged_bu before update on public.findings "
                      "for each row execute function public.grm_skip_unchanged_update();", self.n)
        self.assertEqual(self.n.count("create trigger"), 1)

    def test_compares_text_without_generated_columns(self) -> None:
        self.assertIn("a.attgenerated <> ''", self.n)
        self.assertIn("if (to_jsonb(new) - v_gen)::text = (to_jsonb(old) - v_gen)::text then return null; end if;",
                      self.n)
        self.assertIn("return new;", self.n)
        self.assertNotIn("to_jsonb(new) = to_jsonb(old)", self.n)
        self.assertNotIn("is not distinct from", self.n)

    def test_scope_is_findings_only(self) -> None:
        self.assertNotRegex(self.n, r"\balter\s+table\b")
        self.assertNotRegex(self.n, r"\bupdate\s+public\.")
        self.assertEqual(re.findall(r"before update on public\.(\w+)", self.n), ["findings"])
        self.assertIn("revoke execute on function public.grm_skip_unchanged_update() from public, anon, authenticated;",
                      self.n)
        self.assertIn("set search_path to 'pg_catalog'", self.n)
        self.assertNotIn("security definer", self.n)


if __name__ == "__main__":
    unittest.main()
