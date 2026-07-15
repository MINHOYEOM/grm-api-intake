#!/usr/bin/env python3
"""FIND-1 S1 유사 문구 검색 마이그레이션 tests -- 018/021/022.

Offline source-text checks only -- no network, no real Postgres connection.
Mirrors the style of test_findings_scope_allowlist.py (020 vs 010), which set the
repo's supersede-testing convention this file now follows for a second case.

★022(022_findings_similar_truth.sql) 가 018(findings_similar)과 021
(findings_similar_to) 의 함수 바디를 create or replace 로 **supersede** 한다--
Codex 감사 F-01(절단 후 붕괴로 dup 배지 과소표시)/F-02(p_limit underfill) 수리. 018/021
파일의 함수 바디는 git 히스토리·원복용 원본으로 그대로 남고(파일 상단에 그 사실을 알리는
포인터 주석만 추가했다 -- 007/008/009->010 관례와 동형), 프로덕션 현행 정의는 022 다.
018 의 pg_trgm 확장·idx_findings_search_fts 인덱스는 022 가 그대로 재사용하므로 이 파일이
여전히 현행이다.

이 파일이 018/021/022 전체에 걸쳐 고정하는 불가침 계약(022 가 명시적으로 계승):
  ①검색 대상 = coalesce(nullif(finding_text_ko,''), finding_text) -- ko 단독 금지
    (finding_language='KO' 행이 검색에서 누락되는 것을 막는다)
  ②공개 술어 = 010 현행 정책과 동일(번역 AND scope_status='ok'), findings_similar_to 는
    같은 문서(raw_signal_id) 제외를 **집계 이전**에 적용
  ③반환 계약 = 원문·URL 누설 금지(evidence_url/raw_json 등 미반환), 13키 불변
  ④인덱스(018) == RPC 본문(022) 표현식 byte 일치
  ⑤022 신규: 붕괴 후 절단(matches 전량 집계 -> reps 그룹당 대표 -> window_reps 400개
    절단) + 전 절단 결정론 타이브레이크(published_date desc, finding_id asc)
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_SIMILAR_PATH = _MIGRATIONS_DIR / "018_findings_similar_lexical.sql"
_SIMILAR_TO_PATH = _MIGRATIONS_DIR / "021_findings_similar_to.sql"
_TRUTH_PATH = _MIGRATIONS_DIR / "022_findings_similar_truth.sql"
_SCOPE_PURITY_PATH = _MIGRATIONS_DIR / "010_findings_scope_purity.sql"
_STATS_RPC_PATH = _MIGRATIONS_DIR / "007_findings_stats_rpc.sql"

# 설계가 고정한 검색 대상 표현식(불가침) -- 인덱스(018)·RPC 본문(022) 양쪽에서 byte 동일해야 한다.
_SEARCH_EXPR = "coalesce(nullif(finding_text_ko, ''), finding_text)"
_SEARCH_EXPR_QUALIFIED = "coalesce(nullif(f.finding_text_ko, ''), f.finding_text)"

# 022 는 한 파일에 함수 2개(findings_similar/findings_similar_to)를 정의한다 -- 두 시그니처는
# 서로 접두어 관계(_to)라 여는 괄호+개행까지 포함해야 부분일치 없이 유일하게 식별된다.
_FN_SIMILAR_SIG = "create or replace function public.findings_similar(\n"
_FN_SIMILAR_TO_SIG = "create or replace function public.findings_similar_to(\n"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


def _slice_function(code: str, signature: str) -> str:
    """comment 제거된 code 에서 signature 로 시작하는 함수 정의 전체(닫는 $$; 까지)를 뽑는다."""
    start = code.index(signature)
    end = code.index("$$;", start) + len("$$;")
    return code[start:end]


def _slice_between(text: str, start_marker: str, end_marker: str) -> str:
    """text 안에서 start_marker 부터(포함) 그 뒤 첫 end_marker 직전까지를 뽑는다."""
    start = text.index(start_marker)
    return text[start: text.index(end_marker, start)]


# ---------------------------------------------------------------------------
# 018 -- findings_similar() 함수 바디는 022 가 supersede. pg_trgm 확장·
# idx_findings_search_fts 인덱스는 022 가 재사용하므로 이 파일이 여전히 현행이다.
# ---------------------------------------------------------------------------

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

    def test_extension_and_index_still_current(self) -> None:
        """022 는 함수 2개만 교체한다(자체 header 명시) -- pg_trgm 확장과
        idx_findings_search_fts 인덱스는 이 파일이 여전히 프로덕션 현행 정의다."""
        self.assertIn("create extension if not exists pg_trgm", self.code)
        self.assertIn("create index if not exists idx_findings_search_fts", self.code)

    def test_no_unused_trgm_index(self) -> None:
        """★라이브 실측(2026-07-15) -- trgm GIN 인덱스는 만들지 않는다(018 원본 근거 그대로,
        022 도 이 판단을 뒤집지 않았다: 022 는 index/extension 을 아예 건드리지 않는다)."""
        self.assertNotIn("idx_findings_search_trgm", self.code)
        self.assertNotIn("gin_trgm_ops", self.code)
        self.assertNotIn("pg_trgm.similarity_threshold", self.code)
        self.assertNotIn("alter role", self.code.lower())
        self.assertNotIn("alter database", self.code.lower())

    def test_does_not_touch_existing_objects(self) -> None:
        """기존 테이블·RLS·정책·다른 RPC 를 건드리지 않는다(설계 §4.2 롤백 계약)."""
        for forbidden in (
            "drop table", "drop policy", "alter table", "drop function public.findings_stats",
            "create policy", "delete from", "update public.findings", "insert into public.findings",
        ):
            self.assertNotIn(forbidden, self.code.lower(), f"018 must not contain: {forbidden}")

    def test_supersede_header_points_to_022(self) -> None:
        """★findings_similar() 는 022 가 create or replace 로 supersede -- 007/008/009->010
        관례와 동형으로, 018 파일 상단에 그 사실을 알리는 포인터 주석이 있어야 한다."""
        self.assertIn("022_findings_similar_truth.sql", self.sql)
        self.assertIn("supersede", self.sql.lower())
        # 포인터는 파일 맨 위(함수 정의보다 앞)에 있어야 한다.
        self.assertLess(
            self.sql.index("022_findings_similar_truth.sql"),
            self.sql.index("create or replace function public.findings_similar("),
        )


# ---------------------------------------------------------------------------
# 021 -- findings_similar_to() 함수 바디도 022 가 supersede. 021 은 함수 정의 1개뿐이라
# (인덱스는 018 것을 재사용) 022 이후로는 이 파일 전체가 역사·원복용 기록이다.
# ---------------------------------------------------------------------------

class SimilarToMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_SIMILAR_TO_PATH.is_file(), f"missing {_SIMILAR_TO_PATH}")
        self.sql = _SIMILAR_TO_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _SIMILAR_TO_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_supersede_header_points_to_022(self) -> None:
        self.assertIn("022_findings_similar_truth.sql", self.sql)
        self.assertIn("supersede", self.sql.lower())
        self.assertLess(
            self.sql.index("022_findings_similar_truth.sql"),
            self.sql.index("create or replace function public.findings_similar_to("),
        )


# ---------------------------------------------------------------------------
# 022 -- findings_similar()/findings_similar_to() 의 프로덕션 현행 정의. 018/021 이
# 검사하던 RPC 본문 계약(후보/그룹/절단/반환/보안)이 전부 이리로 옮겨온다.
# ---------------------------------------------------------------------------

class TruthMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_TRUTH_PATH.is_file(), f"missing {_TRUTH_PATH}")
        self.sql = _TRUTH_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TRUTH_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_replaces_both_functions_idempotently(self) -> None:
        self.assertIn("create or replace function public.findings_similar(", self.code)
        self.assertIn("create or replace function public.findings_similar_to(", self.code)

    def test_does_not_touch_existing_objects(self) -> None:
        """022 자체 header 계약: "함수 2개 교체뿐(멱등)" -- 다른 오브젝트는 건드리지 않는다."""
        for forbidden in (
            "drop table", "drop policy", "alter table", "create policy",
            "delete from", "update public.findings", "insert into public.findings",
            "create extension", "create index",
        ):
            self.assertNotIn(forbidden, self.code.lower(), f"022 must not contain: {forbidden}")


class CollapseAfterTruncationStructureTest(unittest.TestCase):
    """(a) 구조: matches(무절단 FTS 전량) -> groups(전역 집계) -> reps(distinct on grp) ->
    window_reps(limit 400) 순서. matches 절엔 limit 문자열이 없고, limit 400 은 window 절에만."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_TRUTH_PATH.read_text(encoding="utf-8"))
        self.fn_similar = _slice_function(self.code, _FN_SIMILAR_SIG)
        self.fn_similar_to = _slice_function(self.code, _FN_SIMILAR_TO_SIG)

    def test_old_pre_limit_200_candidate_stage_is_gone(self) -> None:
        """018/021 의 '후보 200개 재랭킹' 설계는 022 가 폐기 -- candidates CTE·limit 200
        둘 다 파일 어디에도 없어야 한다(구조가 실제로 바뀌었는지의 회귀 가드)."""
        self.assertNotIn("candidates as (", self.code)
        self.assertNotIn("limit 200", self.code)

    def test_cte_order_is_matches_groups_reps_window_reps(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            i_matches = fn.index("matches as (")
            i_groups = fn.index("groups as (")
            i_reps = fn.index("reps as (")
            i_window = fn.index("window_reps as (")
            self.assertLess(i_matches, i_groups)
            self.assertLess(i_groups, i_reps)
            self.assertLess(i_reps, i_window)

    def test_matches_clause_has_no_limit(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            matches_block = _slice_between(fn, "matches as (", "groups as (")
            self.assertNotIn("limit", matches_block.lower())

    def test_window_reps_is_the_only_limit_400(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            self.assertEqual(fn.count("limit 400"), 1)
            window_block = _slice_between(fn, "window_reps as (", "scored as (")
            self.assertIn("limit 400", window_block)

    def test_reps_is_distinct_on_grp_over_matches(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            reps_block = _slice_between(fn, "reps as (", "window_reps as (")
            self.assertIn("select distinct on (grp) *", reps_block)
            self.assertIn("from matches", reps_block)

    def test_grp_is_md5_of_search_text(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            matches_block = _slice_between(fn, "matches as (", "groups as (")
            self.assertIn(
                f"md5({_SEARCH_EXPR_QUALIFIED}) as grp",
                matches_block,
            )


class GroupAggregationBeforeLimitTest(unittest.TestCase):
    """(b) 그룹 집계(count(distinct raw_signal_id)/count(*))는 절단 이전 CTE(matches) 위에서
    수행한다 -- F-01(200 창 안 부분집합만 세는 결함)의 근본 수리."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_TRUTH_PATH.read_text(encoding="utf-8"))
        self.fn_similar = _slice_function(self.code, _FN_SIMILAR_SIG)
        self.fn_similar_to = _slice_function(self.code, _FN_SIMILAR_TO_SIG)

    def test_groups_aggregates_over_matches_not_a_limited_set(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            groups_block = _slice_between(fn, "groups as (", "reps as (")
            self.assertIn("count(distinct raw_signal_id) as dup_documents", groups_block)
            self.assertIn("count(*) as dup_findings", groups_block)
            self.assertIn("from matches", groups_block)
            self.assertIn("group by grp", groups_block)
            # F-02 수리 확인: matches(무절단)를 소비하지, window_reps(절단 후)를 소비하지 않는다.
            self.assertNotIn("from window_reps", groups_block)


class DeterministicTiebreakTest(unittest.TestCase):
    """(c) 모든 절단·최종 정렬에 결정론 타이브레이크(published_date desc, finding_id asc)."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_TRUTH_PATH.read_text(encoding="utf-8"))
        self.fn_similar = _slice_function(self.code, _FN_SIMILAR_SIG)
        self.fn_similar_to = _slice_function(self.code, _FN_SIMILAR_TO_SIG)

    def test_reps_representative_pick_has_tiebreak(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            reps_block = _slice_between(fn, "reps as (", "window_reps as (")
            self.assertIn("order by grp, published_date desc, finding_id asc", reps_block)

    def test_window_truncation_has_tiebreak(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            window_block = _slice_between(fn, "window_reps as (", "scored as (")
            self.assertIn(
                "order by g.best_rank desc, r.published_date desc, r.finding_id asc",
                window_block,
            )

    def test_final_sort_has_tiebreak(self) -> None:
        # 두 함수 x (jsonb_agg order by + 바깥 top_items order by) = 4회 등장해야 한다.
        self.assertEqual(
            self.code.count("order by group_score desc, published_date desc, finding_id asc"),
            4,
        )


class PublicGateAndSameDocContractTest(unittest.TestCase):
    """(d) 두 함수 모두 공개 술어(번역 OR KO + scope_status='ok') 유지, findings_similar_to
    는 같은 문서 제외가 집계 이전(matches 절 안에 is distinct from). 검색 대상 = ko 단독 금지(①)."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_TRUTH_PATH.read_text(encoding="utf-8"))
        self.fn_similar = _slice_function(self.code, _FN_SIMILAR_SIG)
        self.fn_similar_to = _slice_function(self.code, _FN_SIMILAR_TO_SIG)

    def test_gate_matches_010_live_policy_terms(self) -> None:
        """010 의 findings_public_read 정책이 쓰는 두 조건을 022 가 복제했는지 대조하는
        기준선(010 정책이 바뀌면 이 테스트가 먼저 깨진다)."""
        policy_sql = _strip_sql_comments(_SCOPE_PURITY_PATH.read_text(encoding="utf-8"))
        policy = policy_sql[policy_sql.index("create policy findings_public_read"):]
        policy = policy[: policy.index(";") + 1]
        self.assertIn("finding_text_ko <> '' or finding_language = 'KO'", policy)
        self.assertIn("scope_status = 'ok'", policy)

    def test_gate_applied_in_matches_before_aggregation(self) -> None:
        """비공개 행이 랭킹·집계에 들어가면 dup_documents 같은 카운트로 존재가 새어나갈
        수 있다 -- 게이트는 matches(집계 이전 CTE)에서 적용해야 한다."""
        for fn in (self.fn_similar, self.fn_similar_to):
            matches_block = _slice_between(fn, "matches as (", "groups as (")
            self.assertIn("f.scope_status = 'ok'", matches_block)
            self.assertIn("f.finding_text_ko <> '' or f.finding_language = 'KO'", matches_block)

    def test_similar_to_excludes_same_document_inside_matches(self) -> None:
        """021 의 핵심 계약(같은 문서 제외 = 붕괴 전) -- 022 도 matches 절 안에서 지켜야
        그룹 소실 결함(평가셋 40건 중 21건 재현)이 되살아나지 않는다."""
        matches_block = _slice_between(self.fn_similar_to, "matches as (", "groups as (")
        self.assertIn("f.raw_signal_id is distinct from i.raw_signal_id", matches_block)
        self.assertIn("f.finding_id <> i.finding_id", matches_block)
        # findings_similar()(자유질의, 기준 문서가 없음)에는 이 제외가 없어야 한다.
        similar_matches_block = _slice_between(self.fn_similar, "matches as (", "groups as (")
        self.assertNotIn("is distinct from", similar_matches_block)

    def test_no_bare_ko_only_search_predicate(self) -> None:
        """①검색 대상은 coalesce(ko, finding_text) -- finding_text_ko 단독 사용 금지
        (finding_language='KO' 행이 검색에서 누락되는 것을 막는다)."""
        self.assertNotIn("similarity(finding_text_ko", self.code)
        self.assertNotIn("similarity(f.finding_text_ko", self.code)
        self.assertNotIn("to_tsvector('simple', finding_text_ko)", self.code)
        self.assertNotIn("to_tsvector('simple', f.finding_text_ko)", self.code)


class ReturnContractTest(unittest.TestCase):
    """(e) 반환 키 집합 불변(018 때와 동일 13키), evidence_url/raw 미반환."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_TRUTH_PATH.read_text(encoding="utf-8"))

    def test_no_forbidden_fields_returned(self) -> None:
        build = self.code[self.code.index("jsonb_build_object("):]
        for forbidden in ("evidence_url", "raw_json", "row_json", "raw_sha256", "embedding"):
            self.assertNotIn(forbidden, build, f"022 must not return {forbidden}")

    def test_returned_keys_are_the_declared_surface(self) -> None:
        """반환 키 목록이 계약의 유일한 표면(007 주석 관례) -- 예상 키 집합 고정, 018 과 동일."""
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
        # 두 함수 모두 같은 13키 반환 계약을 각자 선언한다(findings_similar + findings_similar_to).
        self.assertEqual(self.code.count("'finding_id', finding_id"), 2)

    def test_trust_badge_fields_returned(self) -> None:
        """신뢰도 배지 2종(M13)은 두 함수 모두에서 유지돼야 한다."""
        build = self.code[self.code.index("'items', coalesce(("):]
        self.assertEqual(build.count("'evidence_level', evidence_level"), 2)
        self.assertEqual(build.count("'review_status', review_status"), 2)


class SecurityDefinerConventionTest(unittest.TestCase):
    """(f) revoke->grant(anon, authenticated) 관례. security definer + 고정 search_path."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_TRUTH_PATH.read_text(encoding="utf-8"))
        self.fn_similar = _slice_function(self.code, _FN_SIMILAR_SIG)
        self.fn_similar_to = _slice_function(self.code, _FN_SIMILAR_TO_SIG)

    def test_both_functions_security_definer_with_fixed_search_path(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            self.assertIn("security definer", fn)
            self.assertIn("stable", fn)
            # pg_trgm 이 extensions 스키마라 similarity() 해석에 필요 -- 고정 목록(mutable 아님).
            self.assertIn("set search_path = public, extensions", fn)

    def test_revoke_then_grant_execute_both_functions(self) -> None:
        self.assertIn(
            "revoke all on function public.findings_similar(text, int) from public;", self.code
        )
        self.assertIn(
            "grant execute on function public.findings_similar(text, int) to anon, authenticated;",
            self.code,
        )
        self.assertIn(
            "revoke all on function public.findings_similar_to(text, int) from public;", self.code
        )
        self.assertIn(
            "grant execute on function public.findings_similar_to(text, int) to anon, authenticated;",
            self.code,
        )

    def test_matches_007_revoke_grant_convention(self) -> None:
        """007 이 같은 관례(회수 후 anon/authenticated 재부여)를 쓰는지 대조."""
        stats = _strip_sql_comments(_STATS_RPC_PATH.read_text(encoding="utf-8"))
        self.assertIn("revoke all on function public.findings_stats() from public;", stats)
        self.assertIn("grant execute on function public.findings_stats() to anon, authenticated;", stats)


class SearchExpressionIndexParityTest(unittest.TestCase):
    """(g) 검색 표현식이 idx_findings_search_fts 표현식과 byte 일치(018 인덱스 재사용).

    라이브 실측 확인(018 주석): Bitmap Index Scan on idx_findings_search_fts (42ms) vs
    전량 seq scan (244ms) -- 표현식이 어긋나면 이 이점이 022 에서도 조용히 사라진다.
    """

    def setUp(self) -> None:
        self.truth_code = _strip_sql_comments(_TRUTH_PATH.read_text(encoding="utf-8"))
        similar_code = _strip_sql_comments(_SIMILAR_PATH.read_text(encoding="utf-8"))
        fts_idx = similar_code[
            similar_code.index("create index if not exists idx_findings_search_fts"):
        ]
        self.fts_idx = fts_idx[: fts_idx.index(";") + 1]

    def test_index_still_uses_unqualified_search_expression(self) -> None:
        self.assertIn(f"to_tsvector('simple', {_SEARCH_EXPR})", self.fts_idx)

    def test_both_functions_matches_clause_reuses_index_expression_bytewise(self) -> None:
        fn_similar = _slice_function(self.truth_code, _FN_SIMILAR_SIG)
        fn_similar_to = _slice_function(self.truth_code, _FN_SIMILAR_TO_SIG)
        for fn in (fn_similar, fn_similar_to):
            matches_block = _slice_between(fn, "matches as (", "groups as (")
            self.assertIn(
                f"to_tsvector('simple', {_SEARCH_EXPR_QUALIFIED}) @@ t.tq", matches_block
            )

    def test_search_text_expression_used_not_ko_alone(self) -> None:
        self.assertIn(_SEARCH_EXPR_QUALIFIED, self.truth_code)


class SharedRpcBodyInvariantsTest(unittest.TestCase):
    """018/021 이 갖고 있던, 022 에도 문구 그대로 계승된 나머지 불변 계약(OR 시맨틱 변환,
    빈 tsquery 가드, 입력 클램프) -- 구조가 바뀐 a-g 항목과 달리 이들은 그대로다."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_TRUTH_PATH.read_text(encoding="utf-8"))
        self.fn_similar = _slice_function(self.code, _FN_SIMILAR_SIG)
        self.fn_similar_to = _slice_function(self.code, _FN_SIMILAR_TO_SIG)

    def test_fts_query_uses_or_semantics(self) -> None:
        """★라이브 실측(2026-07-15): 한국어는 조사 때문에 websearch 기본 AND 로는 표본
        질의 3종 전부 0건 -- OR 변환이 있어야 후보가 잡힌다."""
        self.assertIn(
            "replace(websearch_to_tsquery('simple', i.q)::text, ' & ', ' | ')", self.code
        )

    def test_empty_tsquery_guarded(self) -> None:
        self.assertIn("websearch_to_tsquery('simple', i.q)::text = '' then null", self.code)
        for fn in (self.fn_similar, self.fn_similar_to):
            self.assertIn("t.tq is not null", fn)

    def test_char_length_guard_present_in_both(self) -> None:
        for fn in (self.fn_similar, self.fn_similar_to):
            self.assertIn("char_length(i.q) >= 2", fn)

    def test_limit_clamp_present_with_each_functions_own_default(self) -> None:
        # findings_similar 기본 20, findings_similar_to 기본 5(각자 원본 default 그대로).
        self.assertIn("greatest(1, least(coalesce(p_limit, 20), 50))", self.fn_similar)
        self.assertIn("greatest(1, least(coalesce(p_limit, 5), 50))", self.fn_similar_to)

    def test_query_text_truncation_clamp_present(self) -> None:
        # findings_similar 는 원 질의(p_query)를, findings_similar_to 는 기준 finding 본문
        # (base.txt)을 500자로 자른다 -- 입력 출처는 다르지만 클램프 자체는 둘 다 존재.
        self.assertIn("left(btrim(coalesce(p_query, '')), 500)", self.fn_similar)
        self.assertIn("left(btrim(b.txt), 500)", self.fn_similar_to)

    def test_empty_result_is_valid_jsonb_in_both(self) -> None:
        self.assertEqual(self.code.count("'[]'::jsonb"), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
