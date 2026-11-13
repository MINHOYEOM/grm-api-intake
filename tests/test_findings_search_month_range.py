"""[153 · 2026-11-13] findings_search 기간(월 범위) 축.

  · 153 의 원 계산 본문 = 082 본문에 "월 조건 6곳 치환 + p CTE 두 열 추가"만 한 것(글자 그대로 대조).
  · 공개 RPC·캐시 키 함수(086)는 다시 정의하지 않는다 — 키 정규화가 그대로라 캐시가 어긋나지 않는다.
  · 화면(search.js)이 보내는 범위 값의 모양과 옛 함수 방어가 있다.
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIG = ROOT / "web" / "migrations"
SQL_153 = (MIG / "153_findings_search_month_range.sql").read_text(encoding="utf-8")
SQL_082 = (MIG / "082_findings_search_text_only.sql").read_text(encoding="utf-8")
SEARCH_JS = (ROOT / "web" / "assets" / "search.js").read_text(encoding="utf-8")


def _code(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def _fn(sql: str, head: str) -> str:
    # 줄 머리에서 시작하는 실제 정의만 — 082 주석 안의 같은 글자("…findings_search(...)" 전문을)에 걸리면
    # 주석 뒤의 drop·공개 RPC 정의까지 딸려 온다(11-13 초안이 실제로 그랬다).
    start = sql.index("\n" + head + "\n") + 1
    return sql[start:sql.index("$function$;", start) + len("$function$;")]


EXTRA_COLS = (
    "    (case when position('..' in coalesce(p_month, '')) > 0\n"
    "          then coalesce(nullif(split_part(p_month, '..', 1), ''), '0000-00')\n"
    "          else coalesce(p_month, '') end)                         as f_mfrom,\n"
    "    (case when position('..' in coalesce(p_month, '')) > 0\n"
    "          then coalesce(nullif(split_part(p_month, '..', 2), ''), '9999-99')\n"
    "          else coalesce(p_month, '') end)                         as f_mto,\n"
)
F_MONTH = "    coalesce(p_month, '')                                          as f_month,\n"


class MonthRangeMigrationTest(unittest.TestCase):
    def test_no_crlf(self):
        self.assertNotIn(b"\r\n", (MIG / "153_findings_search_month_range.sql").read_bytes())

    def test_body_is_082_with_only_the_month_substitution(self):
        old = _fn(SQL_082, "create or replace function public.findings_search(")
        want = old.replace("create or replace function public.findings_search(",
                           "create or replace function public.findings_search_compute(", 1)
        want = want.replace(F_MONTH, F_MONTH + EXTRA_COLS, 1)
        want = want.replace("s.month           = p.f_month", "s.month between p.f_mfrom and p.f_mto")
        want = want.replace("s.month = p.f_month", "s.month between p.f_mfrom and p.f_mto")
        got = _fn(SQL_153, "create or replace function public.findings_search_compute(")
        self.assertEqual(got, want)
        self.assertEqual(got.count("s.month between p.f_mfrom and p.f_mto"), 6)
        self.assertNotIn("= p.f_month", got)

    def test_month_facet_still_ignores_month_filter(self):
        got = _fn(SQL_153, "create or replace function public.findings_search_compute(")
        fac = got[got.index("fac_month as ("):got.index("fac_ev as (")]
        self.assertNotIn("f_mfrom", fac)

    def test_single_value_degenerates_to_equality(self):
        # `..` 없으면 from = to = f_month 와 같은 식 → between a and a ≡ = a.
        self.assertEqual(EXTRA_COLS.count("else coalesce(p_month, '') end)"), 2)
        self.assertIn(F_MONTH, _code(SQL_153))

    def test_public_rpc_and_cache_key_untouched(self):
        code = _code(SQL_153)
        self.assertNotIn("function public.findings_search(", code)
        self.assertEqual(code.count("create or replace function"), 1)
        self.assertNotIn("findings_search_cache_args", code)
        self.assertNotIn("drop function", code.lower())
        self.assertNotIn("grant ", code.lower())
        self.assertNotIn("revoke ", code.lower())


class SearchScreenPeriodTest(unittest.TestCase):
    def test_sends_range_shape(self):
        self.assertIn('return y + "-" + (m < 10 ? "0" : "") + m + "..";', SEARCH_JS)
        self.assertIn("if (period) body.p_month = period;", SEARCH_JS)  # 안 고르면 안 보낸다(캐시 키 불변)
        self.assertRegex(SEARCH_JS, r"function periodRange\(")

    def test_guards_old_function(self):
        # 옛 함수는 범위를 한 달 값으로 보고 0건 — by_month 에 그 기간 행이 있으면 기간 축을 끈다.
        self.assertIn("periodUnsupported", SEARCH_JS)


if __name__ == "__main__":
    unittest.main()
