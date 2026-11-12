"""[154 · 2026-11-13] 이동·클릭 익명 카운터.

  · migration: 표 둘·bump 둘(anon 실행)·_clean 보기 둘(139 봇 날 제외)·판독 함수는 _clean 만 읽음.
  · 개인정보 0: 표 열에 경로·쿼리·값 자리가 없고, 형식 위반은 'other' 로 접는다.
  · base.html: env 게이트 안 · 운영자/자동화/프리뷰/admin 제외 · 고른 값·화면 글자를 싣지 않음 ·
    영문 페이지 한글 0(스크립트 본문에 한글 없음).
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL = (ROOT / "web" / "migrations" / "154_rum_nav_ui_events.sql").read_text(encoding="utf-8")
CODE = "\n".join(l for l in SQL.splitlines() if not l.strip().startswith("--"))
BASE = (ROOT / "web" / "templates" / "base.html").read_text(encoding="utf-8")


def _block() -> str:
    i = BASE.index("{% if reactions_enabled %}\n{#- [154 · 2026-11-13]")
    return BASE[i:BASE.index("{% endif %}\n", i)]


def _script() -> str:
    b = _block()
    return b[b.index("<script>"):b.index("</script>")]


class MigrationTest(unittest.TestCase):
    def test_no_crlf(self):
        self.assertNotIn(b"\r\n", (ROOT / "web" / "migrations" / "154_rum_nav_ui_events.sql").read_bytes())

    def test_columns_have_no_path_or_value_slot(self):
        for table in ("rum_nav_daily", "rum_ui_daily"):
            body = CODE[CODE.index(f"create table if not exists public.{table} ("):]
            body = body[:body.index(");")]
            for bad in ("path", "query", "value", "session", "user", "firm", "text_q"):
                self.assertNotRegex(body, rf"\n\s+{bad}\w*\s", f"{table}: {bad}")

    def test_bumps_fold_bad_shapes_and_cap_rows(self):
        for fn, cap in (("rum_nav_bump", 600), ("rum_ui_bump", 800)):
            body = CODE[CODE.index(f"function public.{fn}("):]
            body = body[:body.index("$$;")]
            self.assertIn("security definer", body)
            self.assertIn(f"cap constant integer := {cap};", body)
            self.assertIn(":= 'other'", body)
            self.assertIn("(now() at time zone 'Asia/Seoul')::date", body)
        self.assertIn("raise exception 'rum_ui_bump: unknown ui kind %'", CODE)

    def test_grants(self):
        self.assertIn("grant execute on function public.rum_nav_bump(text, text, text) to anon, authenticated;", CODE)
        self.assertIn("grant execute on function public.rum_ui_bump(text, text, text, text, text) to anon, authenticated;", CODE)
        self.assertIn("grant execute on function public.rum_ui_report(date, date) to authenticated, service_role;", CODE)
        self.assertIn("revoke all on public.rum_nav_daily, public.rum_ui_daily from public, anon, authenticated;", CODE)
        self.assertNotRegex(CODE, r"grant [^;]*on (table )?public\.rum_(nav|ui)_daily[^;]* to [^;]*anon")

    def test_clean_views_exclude_anomaly_days_and_report_reads_only_clean(self):
        for v in ("rum_nav_daily_clean", "rum_ui_daily_clean"):
            body = CODE[CODE.index(f"view public.{v}"):]
            body = body[:body.index(";")]
            self.assertIn("security_invoker = true", body)
            self.assertIn("public.rum_anomaly_days", body)
        rep = CODE[CODE.index("function public.rum_ui_report("):]
        rep = rep[:rep.index("$$;")]
        self.assertNotRegex(rep, r"from public\.rum_(nav|ui)_daily\s+where", "판독은 _clean 만")
        self.assertIn("ctx = 'fda-483'", rep)   # ⑥

    def test_additive_only(self):
        self.assertNotRegex(CODE.lower(), r"drop (table|function|view)")
        self.assertNotRegex(CODE, r"function public\.(funnel_|rum_anomaly|growth_)")


class BaseScriptTest(unittest.TestCase):
    def test_env_gated_and_same_gates_as_rum(self):
        b = _block()
        js = _script()
        self.assertTrue(b.startswith("{% if reactions_enabled %}"))
        self.assertIn("hn!=='{{ site_host }}'&&hn!=='www.{{ site_host }}'", js)
        self.assertIn("navigator.webdriver===true||/\\bHeadless|\\bClaude\\//.test(", js)
        self.assertIn("p0.indexOf('/admin/')===0", js)
        self.assertIn("localStorage.getItem('grm-op')==='1'", js)
        self.assertIn("if(sent>=60)return;", js)

    def test_sends_only_vocabulary_not_values(self):
        js = _script()
        self.assertIn("send('rum_nav_bump',{p_lang:lang,p_from:from,p_to:page});", js)
        self.assertIn("p_kind:kind,p_id:id||nameOf(el),p_ctx:ctxOf(el)", js)
        for bad in ("location.search", "location.href", ".value", "textContent", "innerText"):
            self.assertNotIn(bad, js, bad)
        self.assertIn("kindOf(r.pathname)", js)   # 리퍼러는 종류로만

    def test_no_hangul_in_emitted_script(self):
        self.assertIsNone(re.search("[가-힣]", _script()))

    def test_483_card_type_maps_to_fda_483(self):
        js = _script()
        key = "".join("\\u%04x" % ord(c) if ord(c) > 127 else c for c in "FDA 483 실사 관찰")
        self.assertIn(f"'{key}':'fda-483'", js)
        self.assertIn("d.tagName==='DETAILS'&&d.open", js)


if __name__ == "__main__":
    unittest.main()
