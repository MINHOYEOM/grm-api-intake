#!/usr/bin/env python3
"""082_findings_search_text_only.sql — #804 본문 전용 검색(`p_text_only`) 가드.

068/074 와 같은 사상: 실 Postgres 없이 소스텍스트만 보는 검사라 "정말 같은 값을 내는가"는
여기서 못 잰다(그건 적용 전후 md5 대조로 증명한다 — 마이그레이션 헤더 주석의 검증 쿼리
참조). 여기서 고정하는 것은 **다시 깨지기 쉬운 성질들**이다:

  · 옛 12인자 판을 drop 하는가 — 안 하면 PostgREST 가 두 오버로드 사이에서 모호해진다(074
    가 11→12 인자에서 이미 겪은 그 함정).
  · 신설 인자가 맨 뒤 + 기본값 false 인가 — 기존 12인자 호출(현재 사이트·
    glossary_cases_refresh.py)이 그대로 동작해야 한다.
  · p_text_only=false 분기가 074 의 전체 매치 문자열과 **글자 하나까지 같은가** — 다르면
    "기본값은 동작 무변경"이라는 계약이 소스 수준에서부터 깨진 것이다.
  · 검색 술어를 다른 CTE(searched 밖)에 복제하지 않았는가.
  · anon/authenticated 에게 execute 가 재부여됐는가.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "web" / "migrations" / "082_findings_search_text_only.sql"
PREV_SQL_PATH = ROOT / "web" / "migrations" / "074_findings_search_orig_lang.sql"


class FindingsSearchTextOnlyMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SQL_PATH.exists():
            raise unittest.SkipTest(f"{SQL_PATH.name} 없음")
        cls.sql = SQL_PATH.read_text(encoding="utf-8")
        cls.prev_sql = PREV_SQL_PATH.read_text(encoding="utf-8")
        # 주석을 걷어낸 코드만 본다 — 헤더/인라인 주석이 검사를 오발시키면 안 된다.
        cls.code = "\n".join(
            ln for ln in cls.sql.splitlines() if not ln.lstrip().startswith("--"))

    # ── 시그니처 ────────────────────────────────────────────────────────────
    def test_old_signature_is_dropped_before_create(self) -> None:
        """옛 12인자 판을 내리지 않으면 12인자 호출이 두 함수 사이에서 모호해진다."""
        self.assertIn(
            "drop function if exists public.findings_search(\n"
            "  text, text, text, text, text, text, text, text, integer, integer, text, text);",
            self.sql,
        )
        # drop 이 create 보다 먼저 나와야 한다(같은 트랜잭션 안에서 순서가 의미를 가진다).
        self.assertLess(self.sql.index("drop function if exists"),
                        self.sql.index("create or replace function"))

    def test_new_argument_is_last_and_defaulted_so_old_callers_survive(self) -> None:
        """PostgREST 는 인자가 하나만 달라도 404 다(#681) — 기존 12인자 호출을 지킨다."""
        sig = self.code.split("create or replace function public.findings_search(", 1)[1]
        sig = sig.split(")\nreturns jsonb", 1)[0]
        self.assertTrue(
            sig.rstrip().rstrip(",").endswith("p_text_only boolean default false"),
            f"신설 인자는 맨 뒤 + 기본값이어야 한다: {sig!r}")
        expected = [
            ("p_q", "text"), ("p_source", "text"), ("p_category", "text"),
            ("p_month", "text"), ("p_evidence", "text"), ("p_review_status", "text"),
            ("p_agency", "text"), ("p_sort", "text"), ("p_page", "integer"),
            ("p_docs_per_page", "integer"), ("p_country", "text"), ("p_orig_lang", "text"),
            ("p_text_only", "boolean"),
        ]
        found = re.findall(r"(p_[a-z_]+)\s+(text|integer|boolean)\s+default", sig)
        self.assertEqual(found, expected)

    def test_only_findings_search_is_touched(self) -> None:
        """이 파일은 함수 하나만 바꾼다 — 표·인덱스·다른 함수를 건드리지 않는다."""
        created = re.findall(r"create\s+or\s+replace\s+function\s+public\.(\w+)",
                             self.code, re.IGNORECASE)
        self.assertEqual(created, ["findings_search"])
        dropped = re.findall(r"drop\s+function\s+if\s+exists\s+public\.(\w+)",
                             self.code, re.IGNORECASE)
        self.assertEqual(dropped, ["findings_search"])
        for forbidden in ("alter table", "drop table", "create index", "drop index",
                          "truncate", "delete from"):
            self.assertNotIn(forbidden, self.code.lower(), forbidden)

    def test_security_invoker_and_grants_are_present(self) -> None:
        """030 계약(RLS 단일 게이트) 승계 — anon/authenticated 실행 권한을 명시로 재부여."""
        self.assertIn("security invoker", self.code)
        self.assertIn(
            "grant execute on function public.findings_search(\n"
            "  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean\n"
            ") to anon, authenticated;",
            self.sql,
        )

    def test_function_body_carries_no_comments_so_it_can_be_diffed_with_prosrc(self) -> None:
        """본문에 주석을 두지 않는다 — `md5(prosrc)` 로 프로덕션 무단 수정을 잡기 위해(074 계약)."""
        body = self.sql.split("as $function$", 1)[1].rsplit("$function$;", 1)[0]
        stray = [ln for ln in body.split("\n") if ln.strip().startswith("--")]
        self.assertEqual(stray, [], f"본문 주석 {stray[:3]}")

    # ── 동작 계약: false 는 byte-identical ────────────────────────────────
    def test_false_branch_matches_074_search_string_exactly(self) -> None:
        """p_text_only=false 일 때의 매치 문자열은 074 의 것과 **글자 하나까지 같아야** 한다.

        074 자체가 "기본 호출 21조합 응답 md5 적용 전후 전부 동일"을 증명한 문자열이다 —
        여기서 그 문자열을 그대로 복제했는지 소스 수준에서 고정한다(살아있는 회귀 가드).
        """
        prev_block = self.prev_sql.split("searched as (", 1)[1].split(") ilike", 1)[0]
        prev_expr = prev_block.split("where p.q = ''\n     or (\n", 1)[1].rstrip()
        # 082 의 else 분기를 추출한다.
        new_block = self.sql.split("searched as (", 1)[1].split(") ilike", 1)[0]
        else_expr = new_block.split("else\n", 1)[1].rsplit("\n          end", 1)[0]
        # 들여쓰기가 두 파일에서 다를 수 있어(074=10칸, else 분기=12칸) 공백을 접어 비교한다.
        norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
        self.assertEqual(norm(else_expr), norm(prev_expr),
                        "p_text_only=false(else) 매치 문자열이 074 원본과 다르다 — "
                        "기본값 동작 무변경 계약 위반")

    def test_true_branch_is_body_only(self) -> None:
        """p_text_only=true 는 finding_text/finding_text_ko **딱 둘만** 매치 대상이다."""
        new_block = self.sql.split("searched as (", 1)[1].split(") ilike", 1)[0]
        then_expr = new_block.split("then\n", 1)[1].split("\n          else", 1)[0]
        norm = re.sub(r"\s+", " ", then_expr).strip()
        self.assertEqual(
            norm,
            "coalesce(f.finding_text, '') || ' ' || coalesce(f.finding_text_ko, '')",
        )
        # 메타 필드는 본문 전용 분기 안에 있으면 안 된다.
        for leaked in ("category_code", "category_label_ko", "document_id", "f.source",
                      "cfr_refs", "mfds_refs", "inspector_names"):
            self.assertNotIn(leaked, then_expr, f"본문 전용 분기에 메타 필드가 샜다: {leaked}")

    def test_text_only_flag_does_not_duplicate_the_search_predicate_elsewhere(self) -> None:
        """판정은 `searched` 한 곳에서만 — filtered/facets/dash 는 그 파생일 뿐이다."""
        outside_searched = self.code.split("searched as (", 1)[1].split(")\n),\nfiltered", 1)[1] \
            if ")\n),\nfiltered" in self.code else self.code.split("filtered as (", 1)[1]
        self.assertNotIn("f_text_only", outside_searched,
                        "f_text_only 가 searched CTE 밖에서 다시 쓰였다(술어 복제 위험)")

    def test_response_keys_are_untouched(self) -> None:
        """응답의 최상위 키가 그대로여야 한다 — 이번엔 신설 응답 키가 없다."""
        for key in ("'documents'", "'totals'", "'facets'", "'dash'", "'page'",
                    "'docs_per_page'", "'pages'", "'sort'"):
            self.assertIn(key, self.code, key)


if __name__ == "__main__":
    unittest.main()
