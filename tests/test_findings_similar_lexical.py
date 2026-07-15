#!/usr/bin/env python3
"""FIND-1 S1 유사 문구 검색 마이그레이션 tests (018_findings_similar_lexical.sql).

Offline source-text checks only -- no network, no real Postgres connection.
Mirrors the style of test_findings_scope_purity.py (010), test_findings_stats_rpc.py
(007), test_findings_publish_gate.py (006).

설계 근거: GRM_규제인텔리전스_업그레이드_설계 v1.1.1 §4.0/§4.2 (Codex 검토 정정 반영).
이 테스트가 고정하는 불가침 계약 4가지:
  ①검색 대상 = coalesce(nullif(finding_text_ko,''), finding_text) -- ko 단독 금지
    (finding_language='KO' 행이 검색에서 누락되는 것을 막는다)
  ②공개 술어 = 010 현행 정책과 동일(번역 AND scope_status='ok') -- 006 단독 복제 금지
  ③반환 계약 = 원문·URL 누설 금지(evidence_url/raw_json 등 미반환)
  ④인덱스 표현식 == RPC 본문 표현식 byte 일치 -- 불일치 시 인덱스 미사용
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_SIMILAR_PATH = _MIGRATIONS_DIR / "018_findings_similar_lexical.sql"
_SCOPE_PURITY_PATH = _MIGRATIONS_DIR / "010_findings_scope_purity.sql"
_STATS_RPC_PATH = _MIGRATIONS_DIR / "007_findings_stats_rpc.sql"

# 설계가 고정한 검색 대상 표현식(불가침) -- 인덱스·RPC 양쪽에서 byte 동일해야 한다.
_SEARCH_EXPR = "coalesce(nullif(finding_text_ko, ''), finding_text)"
_SEARCH_EXPR_QUALIFIED = "coalesce(nullif(f.finding_text_ko, ''), f.finding_text)"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


class SimilarMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_SIMILAR_PATH.is_file(), f"missing {_SIMILAR_PATH}")
        self.sql = _SIMILAR_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        # ★골든/마이그레이션 CRLF 함정(과거 전례) -- LF 고정.
        self.assertNotIn(b"\r\n", _SIMILAR_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_idempotent_ddl_only(self) -> None:
        """전부 멱등 -- 재실행이 안전해야 한다(007/010 관례)."""
        self.assertIn("create extension if not exists pg_trgm", self.code)
        self.assertIn("create index if not exists idx_findings_search_trgm", self.code)
        self.assertIn("create index if not exists idx_findings_search_fts", self.code)
        self.assertIn("create or replace function public.findings_similar(", self.code)

    def test_does_not_touch_existing_objects(self) -> None:
        """기존 테이블·RLS·정책·다른 RPC 를 건드리지 않는다(설계 §4.2 롤백 계약)."""
        for forbidden in (
            "drop table", "drop policy", "alter table", "drop function public.findings_stats",
            "create policy", "delete from", "update public.findings", "insert into public.findings",
        ):
            self.assertNotIn(forbidden, self.code.lower(), f"018 must not contain: {forbidden}")


class SearchTextContractTest(unittest.TestCase):
    """①검색 대상 = coalesce(nullif(ko,''), finding_text) -- ko 단독이면 KO 원문 행 누락."""

    def setUp(self) -> None:
        self.sql = _SIMILAR_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_search_expression_used_not_ko_alone(self) -> None:
        self.assertIn(_SEARCH_EXPR, self.code)
        self.assertIn(_SEARCH_EXPR_QUALIFIED, self.code)

    def test_no_bare_ko_only_search_predicate(self) -> None:
        """finding_text_ko 를 검색 대상으로 단독 사용하는 흔적이 없어야 한다.

        similarity(finding_text_ko, ...) / to_tsvector('simple', finding_text_ko) 같은
        ko-단독 형태를 금지한다(공개 게이트 술어의 finding_text_ko <> '' 비교는 예외).
        """
        self.assertNotIn("similarity(finding_text_ko", self.code)
        self.assertNotIn("similarity(f.finding_text_ko", self.code)
        self.assertNotIn("to_tsvector('simple', finding_text_ko)", self.code)
        self.assertNotIn("to_tsvector('simple', f.finding_text_ko)", self.code)

    def test_index_expressions_match_rpc_expression_bytewise(self) -> None:
        """④인덱스 표현식 == RPC 표현식 byte 일치(불일치 = 인덱스 미사용)."""
        trgm_idx = self.code[self.code.index("create index if not exists idx_findings_search_trgm"):]
        trgm_idx = trgm_idx[: trgm_idx.index(";") + 1]
        self.assertIn(f"(({_SEARCH_EXPR}) extensions.gin_trgm_ops)", trgm_idx)

        fts_idx = self.code[self.code.index("create index if not exists idx_findings_search_fts"):]
        fts_idx = fts_idx[: fts_idx.index(";") + 1]
        self.assertIn(f"to_tsvector('simple', {_SEARCH_EXPR})", fts_idx)


class PublicGateContractTest(unittest.TestCase):
    """②공개 술어 = 010 현행 정책(번역 AND scope_status='ok')과 동일해야 한다."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_SIMILAR_PATH.read_text(encoding="utf-8"))

    def test_gate_predicate_replicated_in_function(self) -> None:
        self.assertIn("f.finding_text_ko <> '' or f.finding_language = 'KO'", self.code)
        self.assertIn("f.scope_status = 'ok'", self.code)

    def test_gate_matches_010_live_policy_terms(self) -> None:
        """010 의 findings_public_read 정책이 쓰는 두 조건을 018 이 모두 복제했는지."""
        policy_sql = _strip_sql_comments(_SCOPE_PURITY_PATH.read_text(encoding="utf-8"))
        policy = policy_sql[policy_sql.index("create policy findings_public_read"):]
        policy = policy[: policy.index(";") + 1]
        # 010 정책이 여전히 이 두 축을 쓰는지 확인(정책이 바뀌면 이 테스트가 먼저 깨진다).
        self.assertIn("finding_text_ko <> '' or finding_language = 'KO'", policy)
        self.assertIn("scope_status = 'ok'", policy)

    def test_gate_applied_before_ranking(self) -> None:
        """후보(candidates) 단계에서 게이트를 적용해야 한다 -- 비공개 행이 랭킹·붕괴
        집계에 들어가면 dup_documents 같은 카운트로 존재가 새어나갈 수 있다."""
        cand = self.code[self.code.index("candidates as ("):]
        cand = cand[: cand.index("scored as (")]
        self.assertIn("f.scope_status = 'ok'", cand)
        self.assertIn("f.finding_text_ko <> '' or f.finding_language = 'KO'", cand)


class ReturnContractTest(unittest.TestCase):
    """③반환 계약 -- 원문 URL·raw 등 비공개 필드 누설 금지(007 안전 계약 확장)."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_SIMILAR_PATH.read_text(encoding="utf-8"))

    def test_no_forbidden_fields_returned(self) -> None:
        build = self.code[self.code.index("jsonb_build_object("):]
        for forbidden in ("evidence_url", "raw_json", "row_json", "raw_sha256", "embedding"):
            self.assertNotIn(forbidden, build, f"018 must not return {forbidden}")

    def test_returned_keys_are_the_declared_surface(self) -> None:
        """반환 키 목록이 계약의 유일한 표면(007 주석 관례) -- 예상 키 집합 고정."""
        build = self.code[self.code.index("'items', coalesce(("):]
        keys = set(re.findall(
            r"'([a-z_]+)',\s+(?:finding_id|raw_signal_id|source|agency|published_date"
            r"|firm_name|category_code|evidence_level|review_status|search_text|round"
            r"|dup_documents|dup_findings)",
            build,
        ))
        self.assertEqual(
            keys,
            {
                "finding_id", "raw_signal_id", "source", "agency", "published_date",
                "firm_name", "category_code", "evidence_level", "review_status",
                "text", "score", "dup_documents", "dup_findings",
            },
        )

    def test_trust_badge_fields_returned(self) -> None:
        """신뢰도 배지 2종(M13)은 유사검색 결과에서도 유지돼야 한다 -- RPC 가
        evidence_level/review_status 를 반환하지 않으면 "검토 필요" 경계가 조용히
        사라진다(검수 발견 결함). 둘 다 row 조회(FIELDS)로 이미 anon 공개되는 서지
        메타이고 007 안전 계약도 evidence_level 을 명시 허용한다."""
        build = self.code[self.code.index("'items', coalesce(("):]
        self.assertIn("'evidence_level', evidence_level", build)
        self.assertIn("'review_status', review_status", build)


class RankingAndGuardsTest(unittest.TestCase):
    """설계 §4.2 -- 후보검색→재랭킹 2단, 입력 가드 클램프, 중복 붕괴."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_SIMILAR_PATH.read_text(encoding="utf-8"))

    def test_candidate_then_rerank_two_stage(self) -> None:
        """similarity() 단일 ORDER BY 는 인덱스를 못 탄다 -- 후보를 %/@@ 로 좁힌 뒤 재랭킹."""
        cand = self.code[self.code.index("candidates as ("):]
        cand = cand[: cand.index("scored as (")]
        self.assertIn("%", cand)          # trgm 후보(인덱스 가용)
        self.assertIn("@@", cand)         # FTS 후보(인덱스 가용)
        self.assertIn("limit 200", cand)  # 재랭킹 대상 상한
        self.assertIn("0.6 * sim + 0.4 * fts_rank", self.code)  # 재랭킹 가중치

    def test_fts_query_uses_or_semantics(self) -> None:
        """★라이브 실측(2026-07-15): 한국어는 조사 때문에 websearch 기본 AND 로는 표본
        질의 3종 전부 0건 -- OR 변환이 있어야 후보가 잡힌다. 커버리지는 ts_rank 가 반영."""
        self.assertIn("replace(websearch_to_tsquery('simple', i.q)::text, ' & ', ' | ')", self.code)

    def test_empty_tsquery_guarded(self) -> None:
        """to_tsquery('') 는 에러 -- 빈 질의는 null 로 우회하고 @@/ts_rank 는 null-안전."""
        self.assertIn("websearch_to_tsquery('simple', i.q)::text = '' then null", self.code)
        self.assertIn("t.tq is not null", self.code)
        self.assertIn("coalesce(ts_rank(", self.code)

    def test_input_guards_clamp_not_error(self) -> None:
        """007 관례 -- 이상 입력은 에러가 아니라 클램프/빈 결과."""
        self.assertIn("left(btrim(coalesce(p_query, '')), 500)", self.code)   # 500자 절단
        self.assertIn("greatest(1, least(coalesce(p_limit, 20), 50))", self.code)  # 1..50 클램프
        self.assertIn("char_length(i.q) >= 2", self.code)                     # 2자 미만 = 빈 결과
        self.assertIn("'[]'::jsonb", self.code)                               # 빈 결과도 유효 jsonb

    def test_duplicate_collapse_present(self) -> None:
        """공개 483 의 59.4%가 동일 문구(최다 337회, 2026-07-15 실측) -- 서버에서 붕괴."""
        self.assertIn("md5(search_text)", self.code)
        self.assertIn("distinct on (md5(search_text))", self.code)
        self.assertIn("count(distinct raw_signal_id) as dup_documents", self.code)
        self.assertIn("count(*) as dup_findings", self.code)

    def test_deterministic_tiebreak_ordering(self) -> None:
        """대표 선정·최종 정렬 모두 결정론 타이브레이크(published_date desc, finding_id asc)."""
        self.assertIn("order by md5(search_text), published_date desc, finding_id asc", self.code)
        self.assertIn("order by group_score desc, published_date desc, finding_id asc", self.code)


class SecurityDefinerConventionTest(unittest.TestCase):
    """007/001 관례 -- security definer + 고정 search_path + revoke/grant."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_SIMILAR_PATH.read_text(encoding="utf-8"))

    def test_security_definer_with_fixed_search_path(self) -> None:
        fn = self.code[self.code.index("create or replace function public.findings_similar("):]
        fn = fn[: fn.index("$$;") + 3]
        self.assertIn("security definer", fn)
        self.assertIn("stable", fn)
        # pg_trgm 이 extensions 스키마라 %/similarity() 해석에 필요 -- 고정 목록(mutable 아님).
        self.assertIn("set search_path = public, extensions", fn)

    def test_similarity_threshold_lowered_via_function_set(self) -> None:
        """기본 0.3 은 짧은 지적문 대 문장형 질의에서 후보를 과도하게 잘라낸다."""
        self.assertIn("set pg_trgm.similarity_threshold = 0.1", self.code)

    def test_revoke_then_grant_execute(self) -> None:
        self.assertIn("revoke all on function public.findings_similar(text, int) from public;", self.code)
        self.assertIn(
            "grant execute on function public.findings_similar(text, int) to anon, authenticated;",
            self.code,
        )

    def test_matches_007_revoke_grant_convention(self) -> None:
        """007 이 같은 관례(회수 후 anon/authenticated 재부여)를 쓰는지 대조."""
        stats = _strip_sql_comments(_STATS_RPC_PATH.read_text(encoding="utf-8"))
        self.assertIn("revoke all on function public.findings_stats() from public;", stats)
        self.assertIn("grant execute on function public.findings_stats() to anon, authenticated;", stats)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
