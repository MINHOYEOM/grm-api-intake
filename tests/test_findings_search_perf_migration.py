#!/usr/bin/env python3
"""067_findings_search_perf.sql — findings_search 성능 수리가 **동작을 안 바꿨는지**.

이 마이그레이션은 순수 리팩터다. 실 Postgres 없이 소스텍스트만 보는 검사라 "정말 같은
값을 내는가"는 여기서 못 잰다 — 그건 적용 전후 md5 대조로 증명했다(20개 파라미터 조합
전부 일치). 여기서 고정하는 것은 **다시 깨지기 쉬운 성질들**이다:

  · 시그니처가 그대로인가 — PostgREST 는 인자가 하나만 달라도 404 를 준다(#681).
  · 검색 술어를 **복제하지 않았는가** — 성능을 위해 page_rows 를 다시 쓸 때 가장 쉬운
    유혹이 필터를 직접 베껴 넣는 것이다. 베끼면 두 곳이 갈리는 순간 검색 결과가 조용히
    달라진다. 그래서 범위 판정은 filtered 와의 조인으로만 한다.
  · 남의 객체를 건드리지 않는가 — 이 파일은 findings_search 하나만 바꾼다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "web" / "migrations" / "067_findings_search_perf.sql"


class FindingsSearchPerfMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SQL_PATH.exists():
            raise unittest.SkipTest(f"{SQL_PATH.name} 없음")
        cls.sql = SQL_PATH.read_text(encoding="utf-8")
        # 주석을 걷어낸 코드만 본다 — 주석이 검사를 통과시키거나 오발하면 안 된다.
        cls.code = "\n".join(
            ln for ln in cls.sql.splitlines() if not ln.lstrip().startswith("--"))

    def test_signature_is_unchanged(self) -> None:
        """인자 11개의 **이름·순서·기본값**이 그대로여야 한다."""
        expected = [
            ("p_q", "text"), ("p_source", "text"), ("p_category", "text"),
            ("p_month", "text"), ("p_evidence", "text"), ("p_review_status", "text"),
            ("p_agency", "text"), ("p_sort", "text"), ("p_page", "integer"),
            ("p_docs_per_page", "integer"), ("p_country", "text"),
        ]
        head = self.code.split("returns jsonb", 1)[0]
        found = re.findall(r"(p_[a-z_]+)\s+(text|integer)\s+default", head)
        self.assertEqual(found, expected)

    def test_only_findings_search_is_replaced(self) -> None:
        """이 파일은 함수 하나만 바꾼다 — 표·인덱스·다른 함수를 건드리지 않는다."""
        created = re.findall(r"create\s+or\s+replace\s+function\s+public\.(\w+)",
                             self.code, re.IGNORECASE)
        self.assertEqual(created, ["findings_search"])
        for forbidden in ("alter table", "drop function", "drop table",
                          "create index", "drop index", "truncate", "delete from"):
            self.assertNotIn(forbidden, self.code.lower(), forbidden)

    def test_page_rows_does_not_duplicate_the_search_predicate(self) -> None:
        """★범위 판정은 `filtered` 와의 조인이 한다 — 술어를 베끼지 않는다.

        page_rows 를 빠르게 만드는 가장 쉬운 방법은 필터를 그 자리에 직접 베껴 넣는
        것인데, 그러면 `filtered` 와 두 벌이 되어 한쪽만 고치는 날 검색 결과가 조용히
        달라진다. 그래서 넓은 행은 인덱스로 집되 **어떤 행이 범위 안인지는 한 곳에서만**
        정한다. 이 검사는 그 계약을 고정한다."""
        page_rows = self.code.split("page_rows as (", 1)[1].split("),\npage_docs_full", 1)[0]
        self.assertIn("join filtered fl on fl.finding_id = f.finding_id", page_rows)
        # filtered 가 쓰는 파라미터 비교가 page_rows 안에 복제되면 안 된다.
        for leaked in ("p.f_source", "p.f_cat", "p.f_month", "p.f_ev",
                       "p.f_rs", "p.f_agency", "p.f_country", "q_esc", "ilike"):
            self.assertNotIn(leaked, page_rows,
                             f"page_rows 가 검색 술어를 복제했다: {leaked}")

    def test_page_rows_drives_from_the_page_not_the_whole_corpus(self) -> None:
        """넓은 행은 page_docs(한 페이지분)를 축으로 raw_signal_id 인덱스로 집는다.

        종전에는 filtered 전체를 훑어 나온 행을 **한 건씩 PK 로 다시 조회**했다
        (394 loops · 165ms). 되돌리면 이 검사가 잡는다."""
        page_rows = self.code.split("page_rows as (", 1)[1].split("),\npage_docs_full", 1)[0]
        self.assertIn("from page_docs pd", page_rows)
        self.assertIn("join public.findings f on f.raw_signal_id = pd.raw_signal_id",
                      page_rows)

    def test_top_firms_is_not_computed_with_a_per_firm_rescan(self) -> None:
        """대표 표시명을 firm_key 마다 lateral 로 다시 훑지 않는다(10 × 26,594행 · 81ms).

        (firm_key, firm_name) 을 한 번 집계하고 그 위에서 고른다. 선택 규칙 자체는
        그대로라 산출은 동일하다(적용 전 구현과 jsonb `=` 대조로 확인)."""
        self.assertNotIn("join lateral", self.code.lower())
        self.assertIn("distinct on (fc.firm_key)", self.code)
        # 선택 규칙(건수 desc -> 이름 길이 desc -> 이름 asc)이 살아 있어야 한다.
        self.assertIn("order by fc.firm_key, fc.nc desc, length(fc.firm_name) desc,"
                      " fc.firm_name asc", self.code)

    def test_response_keys_are_untouched(self) -> None:
        """응답의 최상위 키와 documents[] 키가 그대로여야 한다 — 이번엔 신설 키조차 없다."""
        for key in ("'documents'", "'totals'", "'facets'", "'dash'", "'page'",
                    "'docs_per_page'", "'pages'", "'sort'"):
            self.assertIn(key, self.code, key)
        for key in ("'raw_signal_id'", "'firm_name'", "'firm_key'", "'source'",
                    "'agency'", "'published_date'", "'inspection_date'",
                    "'document_id'", "'evidence_url'", "'matched_findings'",
                    "'findings'"):
            self.assertIn(key, self.code, key)

    def test_facets_still_each_drop_their_own_filter(self) -> None:
        """패싯 6종은 **자기 필터만 뺀** 집합을 센다 — 한 번에 묶지 않았음을 고정한다.

        묶으려면 필터별 분기가 필요하고 그건 동작 변경 위험이라 하지 않았다. 누군가
        성능을 이유로 묶으면 이 검사가 먼저 묻는다."""
        pairs = [("fac_source as (", "p.f_source"), ("fac_cat as (", "p.f_cat"),
                 ("fac_month as (", "p.f_month"), ("fac_ev as (", "p.f_ev"),
                 ("fac_rs as (", "p.f_rs"), ("fac_agency as (", "p.f_agency")]
        for marker, own in pairs:
            body = self.code.split(marker, 1)[1].split("),", 1)[0]
            self.assertNotIn(own, body,
                             f"{marker.strip(' as (')} 가 자기 필터를 걸고 있다")


if __name__ == "__main__":
    unittest.main()
