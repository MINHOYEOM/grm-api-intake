#!/usr/bin/env python3
"""PMDA·MHRA 지적 사례 전체 화면 + 조항·용어 블록 (해외 지적 사례 전체 2026-11-10).

  · 스냅샷(deficiency_cases_refresh.build_snapshot) — 화이트리스트(배경 설명·익명 실사 ID 없음)·PMDA 일본어판
    문장 제외(건수만)·MHRA xlsx 조항 집계·게이트(0행·급감·변경 없음)·직렬화 왕복·select 열
  · 뷰(deficiency_cases_page) — 정렬·자르기·한글 행 제외·분류 목록·조항 집계·블록 매칭 규칙
  · 실제 렌더 — 실데이터 크기의 합성 스냅샷으로 ko·en 세 장, 전 행 서버 렌더, 영어판 한글 없음,
    페이지 무게 상한(500KB), sitemap·hreflang·llms, 허브 진입 블록, 조항(211.192)·용어 블록
  · 스냅샷이 없으면 페이지·링크·블록이 없고 기존 페이지 바이트가 그대로다

CI 는 `tests/test_web_deficiency_cases_page.py` shim 으로 순회한다. 직접:
  python web/tests/test_deficiency_cases_page.py
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import render  # noqa: E402
import deficiency_cases_page as dcp  # noqa: E402
import deficiency_cases_refresh as refresh  # noqa: E402

SINGLE_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "single"
HANGUL = re.compile(r"[가-힣]")
PAGE_BUDGET = 500_000  # 압축 전 한 페이지 상한(바이트)

LABELS = {"data_integrity": ("데이터 완전성", "Data integrity"),
          "deviation_capa": ("일탈·CAPA", "Deviation and CAPA"),
          "documentation_records": ("문서·기록", "Documentation and records")}

FILLER = ("the procedure for the operation was not defined and the records did not show who "
          "performed the step or when it was reviewed by the responsible person ")


def _src(key, agency, year, lang, kind):
    return {"source_key": key, "agency": agency, "year": year, "lang": lang, "kind": kind,
            "url": f"https://example.org/{key.replace(':', '_')}"}


def _text(i: int, n_chars: int, lead: str = "") -> str:
    body = (lead + " " if lead else "") + f"Item {i}: " + FILLER * (n_chars // len(FILLER) + 1)
    return body[:n_chars].rstrip() + "."


def raw_data(*, scale: bool = True):
    """실측(2026-11-10) 모양의 합성 원장 — PMDA 영문 111(평균 1,600자·최장 4,732) · 일본어 110 ·
    MHRA 예시 371(평균 158자) · MHRA xlsx 11,501행(조항 문단 ~800)."""
    sources = [
        _src("pmda:000283020", "PMDA", 2025, "en", "pmda_attachment"),
        _src("pmda:000276920", "PMDA", 2024, "en", "pmda_attachment"),
        _src("pmda:000281968", "PMDA", 2025, "ja", "pmda_attachment"),
        _src("mhra:trend_2016", "MHRA", 2016, "en", "mhra_trend_pdf"),
        _src("mhra:2019_deficiency_data", "MHRA", 2019, "en", "mhra_xlsx"),
    ]
    rows = []
    n_pm, n_mh, n_x = (111, 371, 11_501) if scale else (4, 4, 30)
    for i in range(1, n_pm + 1):
        src = "pmda:000283020" if i % 2 else "pmda:000276920"
        lead = ""
        keys, cat = [], "documentation_records"
        if i == 1:
            lead, cat = "The audit trail of the HPLC system was not reviewed before release.", "data_integrity"
        if i == 2:
            lead, keys, cat = "Investigation of the OOS result did not meet 21 CFR 211.192.", ["21 CFR 211.192"], "deviation_capa"
        n_chars = 4732 if i == 3 else (120 if i == 1 else 1600)
        rows.append({"source_key": src, "row_no": i + 5, "agency": "PMDA", "year": 2025 if i % 2 else 2024,
                     "lang": "en", "locator": f"Sheet1!R{i + 5}", "item_no": str(i),
                     "statement": _text(i, n_chars, lead), "agency_category": "Documents and records",
                     "grade": "", "site_group": "", "clause_keys": keys, "clauses": [],
                     "grm_category": cat, "context": "BACKGROUND MUST NOT LEAK"})
    rows.append({"source_key": "pmda:000283020", "row_no": 999, "agency": "PMDA", "year": 2025,
                 "lang": "en", "locator": "Sheet1!R999", "item_no": "99",
                 "statement": "데이터 한글이 섞인 행", "agency_category": "", "grade": "",
                 "site_group": "", "clause_keys": [], "clauses": [], "grm_category": ""})
    for i in range(1, (110 if scale else 3) + 1):
        rows.append({"source_key": "pmda:000281968", "row_no": i + 5, "agency": "PMDA", "year": 2025,
                     "lang": "ja", "locator": f"Sheet1!R{i + 5}", "item_no": str(i),
                     "statement": "製造記録が作成されていなかった。", "agency_category": "文書",
                     "grade": "", "site_group": "", "clause_keys": [], "clauses": [],
                     "grm_category": "documentation_records"})
    for i in range(1, n_mh + 1):
        lead = "Batch record review did not include the audit trail." if i == 1 else ""
        rows.append({"source_key": "mhra:trend_2016", "row_no": i, "agency": "MHRA", "year": 2016,
                     "lang": "en", "locator": f"p.{10 + i // 10} #{i % 10 + 1}", "item_no": "",
                     "statement": _text(i, 80 if i == 1 else 158, lead), "agency_category": "Chapter 4",
                     "grade": "", "site_group": "", "clause_keys": ["EU GMP Chapter 4"], "clauses": [],
                     "grm_category": "documentation_records"})
    parts = [("Chapter", n) for n in range(1, 10)] + [("Annex", n) for n in (1, 11, 15, 16)]
    for i in range(1, n_x + 1):
        kind, num = parts[i % len(parts)]
        para = f"{num}.{i % 61 + 1}" if kind == "Chapter" else f"{i % 61 + 1}"
        grade = ("critical", "major", "other", "other")[i % 4]
        cl = [{"key": f"EU GMP {kind} {num}", "para": para}] if i % 97 else []
        rows.append({"source_key": "mhra:2019_deficiency_data", "row_no": i + 1, "agency": "MHRA",
                     "year": 2019, "lang": "en", "locator": f"Deficiency Data!R{i + 1}", "item_no": f"B{i % 500}",
                     "statement": "", "agency_category": f"{kind} {num}", "grade": grade,
                     "site_group": f"B{i % 500}", "clause_keys": [c["key"] for c in cl], "clauses": cl,
                     "grm_category": ""})
    return sources, rows


def build(scale: bool = True) -> dict:
    s, r = raw_data(scale=scale)
    return refresh.build_snapshot(s, r, measured_on="2026-11-10")


class SnapshotBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = build()

    def test_totals_and_kinds(self):
        t = self.snap["totals"]
        self.assertEqual(t["by_kind"], {"pmda_en": 112, "pmda_ja": 110, "mhra_trend_pdf": 371,
                                        "mhra_xlsx": 11_501})
        self.assertEqual(t["rows"], 112 + 110 + 371 + 11_501)
        self.assertEqual(t["cases"], 112 + 371)

    def test_whitelist_no_context_no_blinded_id(self):
        blob = refresh.serialize(self.snap)
        self.assertNotIn("BACKGROUND MUST NOT LEAK", blob)
        self.assertNotIn('"context"', blob)
        self.assertNotIn('"site_group"', blob)
        self.assertNotIn("製造記録", blob, "PMDA 일본어판 문장은 싣지 않는다(건수만)")
        self.assertEqual(set(self.snap["cases"][0]), {"id", "src", "row", "agency", "year", "no", "loc",
                                                       "acat", "cat", "keys", "text"})
        self.assertEqual(set(self.snap["sources"]["pmda:000283020"]),
                         {"agency", "year", "year_basis", "lang", "kind", "url"})

    def test_clause_counts_cover_every_xlsx_row(self):
        cc = self.snap["clause_counts"]
        counted = sum(r["critical"] + r["major"] + r["other"] for r in cc)
        self.assertEqual(counted + self.snap["mhra_data"]["no_clause"], 11_501)
        self.assertEqual(self.snap["mhra_data"]["no_clause"], 11_501 // 97)
        self.assertEqual(self.snap["mhra_data"]["inspections"], 500)
        self.assertGreater(len(cc), 500)

    def test_serialize_roundtrip(self):
        self.assertEqual(json.loads(refresh.serialize(self.snap)), self.snap)

    def test_gates(self):
        with self.assertRaises(refresh.SnapshotError):
            refresh.build_snapshot([], [], measured_on="2026-11-10")
        self.assertEqual(refresh.guard(self.snap, dict(self.snap, measured_on="2026-12-01")), "unchanged")
        small = build(scale=False)
        self.assertIn("감소", refresh.guard(self.snap, small) or "")
        self.assertIsNone(refresh.guard(small, self.snap))
        self.assertIsNone(refresh.guard(None, self.snap))

    def test_pending_source_url_rejected(self):
        s, r = raw_data(scale=False)
        s[0]["url"] = "https://pending.invalid/"
        with self.assertRaises(refresh.SnapshotError):
            refresh.build_snapshot(s, r, measured_on="2026-11-10")

    def test_fetch_selects_whitelist_only(self):
        calls = []

        class Resp:
            status_code = 200

            def __init__(self, body):
                self._b = body

            def json(self):
                return self._b

        class Http:
            def get(self, url, params, headers, timeout):
                calls.append((url, params["select"]))
                return Resp([])

        refresh.fetch("https://x.supabase.co", "k", http=Http())
        self.assertEqual([c[0].rsplit("/", 1)[1] for c in calls],
                         ["deficiency_example_sources", "deficiency_examples"])
        for _, sel in calls:
            for banned in ("context", "attrs", "country", "product_scope", "sha256"):
                self.assertNotIn(banned, sel.split(","))

    def test_main_writes_then_unchanged(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_dc_main_"))
        try:
            out = tmp / "d.json"
            argv = ["--supabase-url", "https://x.supabase.co", "--service-role-key", "k",
                    "--out", str(out), "--measured-on", "2026-11-10"]
            self.assertEqual(refresh.main(argv, fetch_fn=lambda b, k: raw_data(scale=False)), 0)
            self.assertEqual(dcp.load_snapshot(out)["totals"]["cases"], 9)
            self.assertEqual(refresh.main(argv, fetch_fn=lambda b, k: raw_data(scale=False)),
                             refresh.EXIT_NO_CHANGE)
            self.assertEqual(refresh.main(argv, fetch_fn=lambda b, k: ([], [])), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = build()

    def test_cases_view_sorted_and_hangul_dropped(self):
        v = dcp.cases_view(self.snap, "PMDA", LABELS, "ko")
        self.assertEqual(v["total"], 111, "한글 섞인 행은 양쪽 트리에서 빠진다")
        self.assertEqual(v["years"], ["FY2025", "FY2024"])
        self.assertEqual([r["year"] for r in v["rows"]][:56], ["FY2025"] * 56)
        self.assertEqual(v["ja_rows"], 110)
        self.assertEqual([f["lang"] for f in v["files"]], ["en", "ja", "en"])
        long = next(r for r in v["rows"] if r["ref"] == "No.3")
        self.assertTrue(long["cut"] and long["text"].endswith(" …"))
        self.assertLessEqual(len(long["text"]), dcp.CAP + 2)
        self.assertEqual(v["cats"][0], {"code": "documentation_records", "label": "문서·기록", "n": 109})

    def test_mhra_view_ref_is_page(self):
        v = dcp.cases_view(self.snap, "MHRA", LABELS, "en")
        self.assertEqual(v["total"], 371)
        self.assertEqual(v["rows"][0]["ref"], "p.10")
        self.assertEqual(v["rows"][0]["cat_label"], "Documentation and records")
        self.assertEqual([f["kind"] for f in v["files"]], ["mhra_trend_pdf"])

    def test_clause_counts_view(self):
        v = dcp.clause_counts_view(self.snap)
        self.assertEqual([g["label"] for g in v["groups"]][:3], ["Chapter 1", "Chapter 2", "Chapter 3"])
        self.assertEqual(v["groups"][-1]["label"], "Annex 16")
        self.assertEqual(v["rows_total"] + v["no_clause"], 11_501)
        g = v["groups"][0]
        self.assertEqual([r["total"] for r in g["rows"]], sorted((r["total"] for r in g["rows"]), reverse=True))
        self.assertEqual(g["anchor"], "chapter-1")

    def test_clip(self):
        self.assertEqual(dcp.clip("a  b\nc", 10), ("a b c", False))
        t, cut = dcp.clip("word " * 100, 50)
        self.assertTrue(cut)
        self.assertTrue(t.endswith("word …"))

    def test_term_phrases(self):
        ph = dcp.term_phrases({"term_en": "Corrective and Preventive Action (CAPA)", "aliases": ["시정·예방조치"]})
        self.assertEqual(ph, [("Corrective and Preventive Action", False), ("CAPA", True)])
        ph = dcp.term_phrases({"term_en": "Batch (or Lot)", "aliases": []})
        self.assertEqual(ph, [("Batch", False)])
        self.assertEqual(dcp.term_phrases({"term_en": "Lot", "aliases": ["QA"]}), [("QA", True)])

    def test_term_block_matches_word_and_chapter(self):
        term = {"id": "audit-trail", "term_en": "Audit Trail", "aliases": [],
                "reg_refs": ["PIC/S PI 041-1", "EU GMP Annex 11 §§4, 9, 12"]}
        b = dcp.term_block(self.snap, term, LABELS, "ko", dcp.chapter_totals(self.snap))
        self.assertEqual(len(b["rows"]), 2)
        self.assertEqual([(a["agency"], a["n"], a["q"]) for a in b["by_agency"]],
                         [("PMDA", 1, "Audit Trail"), ("MHRA", 1, "Audit Trail")])
        self.assertEqual(b["chapter"]["label"], "Annex 11")

    def test_term_block_plural_head(self):
        term = {"id": "batch-record", "term_en": "Batch Records", "aliases": [], "reg_refs": []}
        b = dcp.term_block(self.snap, term, LABELS, "ko")
        self.assertEqual([a["agency"] for a in b["by_agency"]], ["MHRA"], "'Batch record review' 도 일치")

    def test_term_block_too_common_is_dropped(self):
        term = {"id": "procedure", "term_en": "Procedure", "aliases": [], "reg_refs": []}
        self.assertIsNone(dcp.term_block(self.snap, term, LABELS, "ko"),
                          "모든 행에 나오는 낱말은 주제를 가르지 못한다 — 블록 없음")

    def test_abbreviation_is_case_sensitive(self):
        term = {"id": "x", "term_en": "Out of Specification (OOS)", "aliases": [], "reg_refs": []}
        b = dcp.term_block(self.snap, term, LABELS, "ko")
        self.assertEqual(b["by_agency"][0]["n"], 1)
        term2 = {"id": "y", "term_en": "Zz (OOS)", "aliases": [], "reg_refs": []}
        snap = json.loads(json.dumps(self.snap))
        for c in snap["cases"]:
            c["text"] = c["text"].replace("OOS", "oos")
        self.assertIsNone(dcp.term_block(snap, term2, LABELS, "ko"))

    def test_clause_block_exact_key(self):
        b = dcp.clause_block(self.snap, "21 CFR 211.192", LABELS, "en")
        self.assertEqual([r["ref"] for r in b["rows"]], ["No.2"])
        self.assertIsNone(dcp.clause_block(self.snap, "21 CFR 211.19", LABELS, "en"))
        self.assertIsNone(dcp.clause_block(None, "21 CFR 211.192", LABELS, "en"))

    def test_credit_pages_match_collector_index(self):
        import collect_deficiency_examples as cde
        self.assertEqual({p["url"] for p in dcp.CREDIT["PMDA"]["pages"]}, set(cde.PMDA_INDEX.values()))
        self.assertEqual([p["url"] for p in dcp.CREDIT["MHRA"]["pages"]], [cde.MHRA_INDEX])

    def test_validate_rejects_broken(self):
        bad = json.loads(json.dumps(self.snap))
        bad["cases"][0]["src"] = "pmda:nope"
        with self.assertRaises(dcp.SnapshotError):
            dcp.validate(bad)
        with self.assertRaises(dcp.SnapshotError):
            dcp.validate({"schema": "x"})


def _render(out: pathlib.Path, snap_path: pathlib.Path) -> None:
    saved = render.DEFICIENCY_CASES_FILE
    render.DEFICIENCY_CASES_FILE = snap_path
    try:
        render.render_site(SINGLE_FIXTURES, out, render_doc_pages=False)
    finally:
        render.DEFICIENCY_CASES_FILE = saved


class RenderedPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_dcases_"))
        snap_path = cls._tmp / "deficiency_cases.json"
        snap_path.write_text(refresh.serialize(build()), encoding="utf-8")
        cls.out = cls._tmp / "site"
        _render(cls.out, snap_path)
        cls.none = cls._tmp / "none"
        _render(cls.none, cls._tmp / "no_such.json")
        cls.rd = staticmethod(lambda root, *p: root.joinpath(*p).read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def page(self, *p):
        return self.rd(self.out, *p)

    @staticmethod
    def _main(html):
        return html.split("<main>", 1)[1].split("</main>", 1)[0]

    def test_three_pages_both_trees_under_budget(self):
        for route in ("pmda", "mhra", "mhra-data"):
            for pre in ((), ("en",)):
                with self.subTest(route=route, tree=pre):
                    path = self.out.joinpath(*pre, "findings", "examples", route, "index.html")
                    self.assertTrue(path.is_file())
                    self.assertLess(path.stat().st_size, PAGE_BUDGET)

    def test_all_rows_server_rendered(self):
        for pre in ((), ("en",)):
            pm = self._main(self.page(*pre, "findings", "examples", "pmda", "index.html"))
            mh = self._main(self.page(*pre, "findings", "examples", "mhra", "index.html"))
            self.assertEqual(pm.count('<article class="dc-i"'), 111)
            self.assertEqual(mh.count('<article class="dc-i"'), 371)
            self.assertIn("The audit trail of the HPLC system was not reviewed before release.", pm)
            self.assertNotIn("BACKGROUND", pm)

    def test_en_pages_have_no_korean(self):
        for route in ("pmda", "mhra", "mhra-data"):
            body = self._main(self.page("en", "findings", "examples", route, "index.html"))
            m = HANGUL.search(body)
            self.assertIsNone(m, m and body[max(0, m.start() - 80):m.start() + 40])

    def test_inline_script_has_no_korean_and_bar_hidden(self):
        html = self.page("findings", "examples", "pmda", "index.html")
        scripts = [s.split("</script>", 1)[0] for s in html.split("<script>")[1:]]
        mine = [s for s in scripts if 'getElementById("dc-rows")' in s]
        self.assertEqual(len(mine), 1)
        self.assertIsNone(HANGUL.search(mine[0]))
        self.assertIn('<form class="dc-bar" id="dc-bar" role="search" hidden>', html)

    def test_ko_page_copy(self):
        body = self._main(self.page("findings", "examples", "pmda", "index.html"))
        self.assertIn("공개한 지적 111건을 원문(영문판) 그대로", body)
        self.assertIn('<option value="documentation_records">문서화/기록관리 (109)</option>', body)
        self.assertIn("원문 파일 FY2025 (일본어)", body)
        self.assertIn('class="on" aria-current="page">일본 PMDA 사례</a>', body)

    def test_mhra_data_table(self):
        body = self._main(self.page("findings", "examples", "mhra-data", "index.html"))
        self.assertIn('id="annex-11"', body)
        self.assertIn("조항이나 등급을 읽지 못한 118행은 표에 넣지 않았습니다.", body)
        en = self._main(self.page("en", "findings", "examples", "mhra-data", "index.html"))
        self.assertIn("118 rows without a readable clause or grade", en)

    def test_sitemap_hreflang_llms(self):
        sm = self.page("sitemap.xml")
        for route in ("pmda", "mhra", "mhra-data"):
            self.assertIn(f"/findings/examples/{route}/</loc><lastmod>2026-11-10</lastmod>", sm)
            self.assertIn(f"/en/findings/examples/{route}/</loc>", sm)
        html = self.page("findings", "examples", "pmda", "index.html")
        self.assertIn('hreflang="en" href="https://grm-solutions.com/en/findings/examples/pmda/"', html)
        self.assertIn("/en/findings/examples/mhra-data/", self.page("llms.txt"))

    def test_hub_entry_block(self):
        ko = self.page("findings", "examples", "index.html")
        self.assertIn('href="../../findings/examples/pmda/">일본 PMDA 사례 111건</a>', ko)
        self.assertIn("원자료 12,094행 전부", ko)
        en = self.page("en", "findings", "examples", "index.html")
        self.assertIn('findings/examples/mhra-data/">MHRA clause counts: 11,383</a>', en)

    def test_clause_page_block(self):
        ko = self.page("findings", "clause", "211-192", "index.html")
        self.assertIn('id="intl-cases"', ko)
        self.assertIn("Investigation of the OOS result did not meet 21 CFR 211.192.", ko)
        self.assertIn('href="../../../findings/examples/pmda/">일본 PMDA 사례 1건 보기', ko)
        en = self.page("en", "findings", "clause", "211-192", "index.html")
        self.assertIn("whose wording cites 21 CFR 211.192", en)
        self.assertNotIn('id="intl-cases"', self.page("findings", "clause", "211-100", "index.html"))

    def test_glossary_term_block(self):
        ko = self.page("glossary", "audit-trail", "index.html")
        self.assertIn('id="intl-cases"', ko)
        self.assertIn("pmda/?q=Audit%20Trail", ko)
        self.assertIn("EU GMP Annex 11 인용", ko)
        en = self.page("en", "glossary", "audit-trail", "index.html")
        self.assertIn("whose wording uses this term", en)

    def test_source_credit_and_edit_notice_on_every_page(self):
        """이용 조건 고정 — PMDA PDL1.0: 출처(기관명+원문 페이지 URL)·가공 명시·PMDA 제작 아님 /
        MHRA OGL v3.0: 출처 표기. ko·en 여섯 장 모두 원문 페이지 링크와 가공 고지가 있어야 한다."""
        pm = dcp.CREDIT["PMDA"]["pages"]
        mh = dcp.CREDIT["MHRA"]["pages"][0]["url"]
        want = {
            ("ko", "pmda"): ["출처: 독립행정법인 의약품의료기기종합기구(PMDA) 웹사이트",
                             "공공데이터 이용 약관(PDL1.0)에 따라 이용합니다.",
                             "GRM 이 원문을 발췌·정리했습니다", "원문과 다를 수 있으니 반드시 원문 파일로 확인하세요.",
                             "이 화면은 PMDA 가 만들거나 검토한 것이 아닙니다."],
            ("en", "pmda"): ["Source: Pharmaceuticals and Medical Devices Agency (PMDA) website",
                             "Used under the Public Data License (PDL1.0).", "GRM excerpted and arranged",
                             "always check the source file", "This page was not made or reviewed by PMDA."],
            ("ko", "mhra"): ["출처: 영국 의약품·의료제품규제청(MHRA), GOV.UK",
                             "Open Government Licence v3.0 에 따라 이용합니다.", "GRM 이 원문을 발췌·정리했습니다",
                             "이 화면은 MHRA 가 만들거나 검토한 것이 아닙니다."],
            ("en", "mhra"): ["Source: Medicines and Healthcare products Regulatory Agency (MHRA), GOV.UK",
                             "Used under the Open Government Licence v3.0.", "GRM excerpted and arranged",
                             "This page was not made or reviewed by MHRA."],
            ("ko", "mhra-data"): ["출처: 영국 의약품·의료제품규제청(MHRA), GOV.UK",
                                  "GRM 이 원자료의 행을 조항·문단별로 세어 표로 정리했습니다.",
                                  "원문과 다를 수 있으니 반드시 원문 파일로 확인하세요."],
            ("en", "mhra-data"): ["Source: Medicines and Healthcare products Regulatory Agency (MHRA), GOV.UK",
                                  "GRM counted the rows of the source data", "always check the source file"],
        }
        for (lg, route), phrases in want.items():
            with self.subTest(lang=lg, route=route):
                pre = ("en",) if lg == "en" else ()
                body = self._main(self.page(*pre, "findings", "examples", route, "index.html"))
                self.assertEqual(body.count('id="source-credit"'), 1)
                for ph in phrases:
                    self.assertIn(ph, body)
                urls = [p["url"] for p in pm] if route == "pmda" else [mh]
                for u in urls:
                    self.assertIn(f'href="{u}"', body)
                    self.assertIn(f"({u})", body, "URL 자체도 글자로 보여야 한다(출처 표기)")
                lic = dcp.CREDIT["PMDA" if route == "pmda" else "MHRA"]["license_url"]
                self.assertIn(f'href="{lic}"', body)

    def test_absent_snapshot_no_pages_no_links(self):
        self.assertFalse((self.none / "findings" / "examples" / "pmda").exists())
        self.assertNotIn("examples/pmda", self.rd(self.none, "sitemap.xml"))
        self.assertNotIn("examples/mhra", self.rd(self.none, "llms.txt"))
        for rel in (("findings", "clause", "211-192", "index.html"), ("glossary", "audit-trail", "index.html"),
                    ("findings", "examples", "index.html")):
            self.assertNotIn("intl-cases", self.rd(self.none, *rel))
            self.assertNotIn("examples/pmda", self.rd(self.none, *rel))

    def test_absent_snapshot_keeps_existing_pages_byte_identical(self):
        """스냅샷이 없으면 조항·용어·지적 사례 페이지가 이 변경 전과 같아야 한다(골든 흔들림 0) —
        템플릿 삽입 줄이 공백 한 칸도 남기지 않는지, 스냅샷이 있는 빌드에서 블록이 없는 페이지와 대조."""
        rel = ("findings", "clause", "211-100", "index.html")
        self.assertEqual(self.rd(self.none, *rel), self.page(*rel))
        rel = ("glossary", "gmp-inspection", "index.html")
        if (self.none.joinpath(*rel)).is_file() and "intl-cases" not in self.page(*rel):
            self.assertEqual(self.rd(self.none, *rel), self.page(*rel))


if __name__ == "__main__":
    unittest.main()
