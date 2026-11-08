"""collect_deficiency_examples + 134_deficiency_examples.sql 표적 시험.

픽스처는 2026-11-08 에 받은 실제 원천 파일의 조각이다(tests/fixtures/deficiency_examples/).
  - xlsx: 원본 zip 에서 시트 행 일부만 남겼다(sharedStrings·후리가나는 원본 그대로).
  - MHRA Trend PDF: 원본 PDF 는 글꼴·이미지로 수백 KB 라, 실제 쪽 글자(page.get_text())를 txt 로 두고
    PDF 경로 시험은 그 글자로 작은 PDF 를 만들어 돌린다.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import collect_deficiency_examples as de  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "deficiency_examples"
SQL = (ROOT / "web" / "migrations" / "134_deficiency_examples.sql").read_text(encoding="utf-8")


def _b(name: str) -> bytes:
    return (FIX / name).read_bytes()


def _pages(name: str) -> list[tuple[int, str]]:
    txt = (FIX / name).read_text(encoding="utf-8")
    out = []
    for chunk in txt.split("\f@@page ")[1:]:
        head, _, body = chunk.partition("\n")
        out.append((int(head), body))
    return out


class XlsxReaderTest(unittest.TestCase):
    def test_furigana_is_dropped(self):
        rows = de.read_xlsx(_b("pmda_fy2025_ja_excerpt.xlsx"))[0][1]
        header = dict(rows)[2]
        # 원본 셀 'D2' 에는 후리가나 'シテキ' 가 rPh 로 숨어 있다.
        self.assertEqual(de.clean(header["D"]), "指摘事項（本文）")
        flat = "".join(v for _, c in rows for v in c.values())
        self.assertNotIn("セイゾウキロク", flat)
        self.assertNotIn("サキシメ", flat)

    def test_clean_keeps_inner_text(self):
        self.assertEqual(de.clean("\n  a ●\n\tb　 \n"), "a ●\nb")


class PmdaParserTest(unittest.TestCase):
    def test_ja_fy2025(self):
        year, rows = de.parse_pmda_attachment(_b("pmda_fy2025_ja_excerpt.xlsx"), lang="ja")
        self.assertEqual(year, 2025)
        self.assertEqual([r.item_no for r in rows], ["1", "2", "3", "4", "5", "10", "41", "42"])
        r3 = rows[2]
        self.assertEqual(r3.agency_system, "品質システム")
        self.assertEqual(r3.agency_category, "文書及び記録の管理（DIを含む）")
        self.assertEqual(r3.product_scope, "一般")
        self.assertTrue(r3.statement.startswith("調査対象品目の製造指図記録書"))
        self.assertTrue(r3.context.startswith("記録の保管期間"))
        self.assertEqual(r3.locator, "2025年度中程度不備事項リスト!R5")
        self.assertEqual(r3.clauses[0]["key"], "JP GMP Ordinance Article 20")
        self.assertEqual(r3.clauses[0]["para"], "para 1 item 3")
        self.assertEqual(r3.clauses[0]["raw"], "GMP省令第20条第1項第3号")
        # 부기 '(DIを含む)' 만으로 data_integrity 가 되지 않는다.
        self.assertEqual((r3.grm_category, r3.grm_category_basis), ("documentation_records", "agency_category_map"))

    def test_ja_site_groups_from_footnote(self):
        _, rows = de.parse_pmda_attachment(_b("pmda_fy2025_ja_excerpt.xlsx"), lang="ja")
        by_no = {r.item_no: r.site_group for r in rows}
        # 각주: No.7～9、No.10～12 … 同一の製造所. 10 은 묶음 첫 번호, 1 은 묶음 없음.
        self.assertEqual(by_no["10"], "no10")
        self.assertEqual(by_no["1"], "")

    def test_en_fy2025_matches_ja_categories(self):
        _, ja = de.parse_pmda_attachment(_b("pmda_fy2025_ja_excerpt.xlsx"), lang="ja")
        year, en = de.parse_pmda_attachment(_b("pmda_fy2025_en_excerpt.xlsx"), lang="en")
        self.assertEqual(year, 2025)
        self.assertEqual([r.item_no for r in en], ["1", "2", "3", "4", "5", "10", "41", "42"])
        ja_cat = {r.item_no: r.grm_category for r in ja}
        for r in en:
            if r.item_no in ja_cat:
                self.assertEqual(r.grm_category, ja_cat[r.item_no], r.item_no)
        self.assertEqual(en[0].agency_system, "Quality system")  # 앞 줄바꿈 정리
        self.assertEqual(en[2].clauses[0]["key"], "JP GMP Ordinance Article 20")
        self.assertEqual(en[2].clauses[0]["para"], "para 1 item 3")
        self.assertEqual(en[2].clauses[0]["confidence"], 0.6)  # 영문은 낮은 신뢰도
        self.assertEqual({r.site_group for r in en if r.item_no == "10"}, {"no10"})

    def test_en_fy2023_other_header_words(self):
        year, rows = de.parse_pmda_attachment(_b("pmda_fy2023_en_excerpt.xlsx"), lang="en")
        self.assertEqual(year, 2023)
        self.assertEqual(rows[0].agency_category, "Organizational management, quality management")
        self.assertEqual(rows[0].product_scope, "Drug, General")
        self.assertEqual(rows[-1].item_no, "26")
        self.assertEqual(rows[-1].clauses[0]["key"], "JP GMP Ordinance Article 11")
        self.assertEqual(rows[-1].clauses[0]["para"], "para 1 item 6")
        # 각주 "No. 2 to 4 and No. 15 and 16"
        self.assertEqual(de.site_groups(["*No. 2 to 4 and No. 15 and 16 were issued to the same "
                                         "manufacturing site, respectively."]),
                         {2: 2, 3: 2, 4: 2, 15: 15, 16: 15})

    def test_ja_fy2023_kanji_numerals(self):
        _, rows = de.parse_pmda_attachment(_b("pmda_fy2023_ja_excerpt.xlsx"), lang="ja")
        keys = {(c["key"], c["para"]) for r in rows for c in r.clauses}
        self.assertIn(("JP GMP Ordinance Article 5", "para 1 item 1"), keys)       # 省令第五条第一項第一号
        self.assertIn(("JP GMP Ordinance Article 11-5", "para 2"), keys)          # 省令第十一条の五第二項
        self.assertEqual({r.site_group for r in rows if r.item_no in ("2", "3")}, {"no2"})

    def test_header_missing_is_structure_error(self):
        with self.assertRaises(de.StructureError):
            de.parse_pmda_attachment(_b("mhra_2019_excerpt.xlsx"), lang="en")


class ClauseNormalizeTest(unittest.TestCase):
    def test_kanji(self):
        self.assertEqual([de.jnum(s) for s in ("二十一", "十", "三", "２１", "百二", "x")],
                         [21, 10, 3, 21, 102, None])

    def test_ja_forms(self):
        f = lambda s: [(c["key"], c["para"]) for c in de.jp_clauses(s, "ja")]  # noqa: E731
        self.assertEqual(f("（GMP省令第11条の2第2項に抵触）"), [("JP GMP Ordinance Article 11-2", "para 2")])
        self.assertEqual(f("GMP省令（以下「省令」という。）第30条第2号により"), [("JP GMP Ordinance Article 30", "item 2")])
        self.assertEqual(f("GCTP省令第12条"), [("JP GCTP Ordinance Article 12", "")])
        self.assertEqual(f("第20条のとおり"), [])  # 省令 없는 맨 조번호는 만들지 않는다
        self.assertEqual(f("薬機法第14条"), [])

    def test_en_forms_and_source_typos(self):
        f = lambda s: [(c["key"], c["para"]) for c in de.jp_clauses(s, "en")]  # noqa: E731
        self.assertEqual(f("(violation of item 4, paragraph 1, Article 11-2 of the Ordinance)"),
                         [("JP GMP Ordinance Article 11-2", "para 1 item 4")])
        self.assertEqual(f("(Violation of Artucle 20 paragraph 1-3 of GMP ministerial ordinance)"),
                         [("JP GMP Ordinance Article 20", "para 1 item 3")])
        self.assertEqual(f("(violation of paragraph 2, Article 3 of GMP Ministreal Audinance)"),
                         [("JP GMP Ordinance Article 3", "para 2")])
        self.assertEqual(f("as described in Article 5 of this report"), [])  # 법령 문맥 없음
        self.assertEqual(f("violation of Article 14 of the Act"), [])         # 법(薬機法) 조항
        self.assertEqual(f("see 21 CFR 211.192"), [("21 CFR 211.192", "")])

    def test_eu_reference(self):
        k = lambda r, ch="": (lambda c: (c["key"], c["para"]) if c else None)(de.eu_clause(r, ch))  # noqa: E731
        self.assertEqual(k("C1.8(iii)"), ("EU GMP Chapter 1", "1.8(iii)"))
        self.assertEqual(k("A15.3.14(ii)"), ("EU GMP Annex 15", "3.14(ii)"))
        self.assertEqual(k("c5.10"), ("EU GMP Chapter 5", "5.10"))
        self.assertEqual(k("Chapter 2 Principle"), ("EU GMP Chapter 2", "Principle"))
        self.assertEqual(k("Annex 15 General"), ("EU GMP Annex 15", "General"))
        self.assertEqual(k("", "Chapter 4"), ("EU GMP Chapter 4", ""))
        self.assertIsNone(k("", "Unknown"))


class MhraXlsxTest(unittest.TestCase):
    def test_rows(self):
        rows = de.parse_mhra_xlsx(_b("mhra_2019_excerpt.xlsx"))
        first = rows[0]
        self.assertEqual((first.row_no, first.item_no, first.site_group), (2, "2019-001", "2019-001"))
        self.assertEqual((first.grade, first.deficiency_label), ("major", "Major-2"))
        self.assertEqual(first.attrs["deficiency_no"], 2)
        self.assertTrue(first.attrs["flags"]["sterile_products"])
        self.assertFalse(first.attrs["flags"]["distributor"])
        self.assertEqual(first.statement, "")  # 원자료에 지적 문장이 없다
        self.assertEqual(first.clauses[0]["key"], "EU GMP Chapter 1")
        self.assertEqual(first.grm_category_basis, "eu_gmp_part_map")
        self.assertEqual(rows[-1].row_no, 5293)
        self.assertEqual(rows[-1].country, "UNITED KINGDOM")

    def test_pmda_file_is_not_mhra(self):
        with self.assertRaises(de.StructureError):
            de.parse_mhra_xlsx(_b("pmda_fy2025_ja_excerpt.xlsx"))


class MhraTrendTest(unittest.TestCase):
    def test_2015_lead_and_bullets(self):
        pages = dict(_pages("mhra_trend_2015_excerpt.txt"))
        self.assertIsNone(de.parse_trend_page(pages[6]))  # 그래프 쪽은 건너뛴다
        key, lead, items = de.parse_trend_page(pages[7])
        self.assertEqual(key, "EU GMP Chapter 1")
        self.assertTrue(lead.startswith("A state of control was not established"))
        self.assertEqual(len(items), 4)
        self.assertTrue(items[0].startswith("The training of aseptic area operators"))
        key, lead, items = de.parse_trend_page(pages[8])   # 머리 문장 없이 글머리만
        self.assertEqual((lead, len(items)), ("", 2))
        self.assertEqual(de.parse_trend_page(pages[47])[0], "EU GMP Annex 1")

    def test_2016_titles_and_no_bullet_page(self):
        pages = dict(_pages("mhra_trend_2016_excerpt.txt"))
        key, lead, items = de.parse_trend_page(pages[9])
        self.assertEqual((key, len(items)), ("EU GMP Chapter 1", 6))
        key, lead, items = de.parse_trend_page(pages[18])
        self.assertEqual(lead, "Deficiency related to the return of products:")
        self.assertEqual(len(items), 1)
        self.assertIsNone(de.parse_trend_page(pages[99]))
        self.assertEqual(de.parse_trend_page(pages[100])[0], "EU GMP Annex 19")

    def test_pdf_path(self):
        import pymupdf

        doc = pymupdf.open()
        for _, text in _pages("mhra_trend_2016_excerpt.txt"):
            page = doc.new_page()
            # 내장 'japan' 글꼴만 '•' 를 그대로 되돌려 준다(기본 helv 는 '·' 로 바꾼다 — 2026-11-08 실측).
            page.insert_text((40, 40), text, fontsize=7, fontname="japan")
        rows = de.parse_mhra_trend_pdf(doc.tobytes())
        self.assertGreaterEqual(len(rows), 10)
        self.assertEqual(rows[0].locator, "p.1 #1")
        self.assertEqual([r.row_no for r in rows], list(range(1, len(rows) + 1)))
        self.assertTrue(all(r.clauses and r.clauses[0]["basis"] == "mhra_page_heading" for r in rows))
        self.assertEqual(rows[-1].agency_category, "Annex 19")


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p


class FakeDb:
    """deficiency_examples_upsert/finalize 를 메모리로 흉내 — 134 의 규칙(content_hash 같으면 unchanged,
    finalize 는 keep 밖 row_no 삭제)과 같게."""

    def __init__(self):
        self.rows: dict[tuple[str, int], str] = {}
        self.sources: dict[str, dict] = {}
        self.calls: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        return _Resp(200, [{"source_key": k, "sha256": v["sha256"], "rows_loaded": v["rows_loaded"]}
                           for k, v in self.sources.items()])

    def post(self, url, json=None, headers=None, timeout=None):
        name = url.rsplit("/", 1)[1]
        self.calls.append(name)
        if name == "deficiency_examples_upsert":
            ins = upd = same = 0
            for r in json["p_rows"]:
                k = (json["p_source_key"], r["row_no"])
                if k not in self.rows:
                    ins += 1
                elif self.rows[k] != r["content_hash"]:
                    upd += 1
                else:
                    same += 1
                self.rows[k] = r["content_hash"]
            return _Resp(200, {"inserted": ins, "updated": upd, "unchanged": same})
        if name == "deficiency_examples_finalize":
            key = json["p_source"]["source_key"]
            keep = set(json["p_keep"])
            gone = [k for k in self.rows if k[0] == key and k[1] not in keep]
            for k in gone:
                del self.rows[k]
            self.sources[key] = dict(json["p_source"], rows_loaded=sum(1 for k in self.rows if k[0] == key))
            return _Resp(200, {"deleted": len(gone)})
        return _Resp(404, {})


class IdempotentLoadTest(unittest.TestCase):
    def setUp(self):
        self.src = de.Source("pmda:testfy2025ja", "PMDA", 2025, "ja", "pmda_attachment",
                             "https://www.pmda.go.jp/files/x.xlsx", de.PMDA_INDEX["ja"], 1)
        _, self.rows = de.parse_pmda_attachment(_b("pmda_fy2025_ja_excerpt.xlsx"), lang="ja")

    def _load(self, fake, rows, sha="a" * 64):
        db = de.Db("https://x.supabase.co", "k", http=fake)
        meta = {"source": de.source_payload(self.src, sha, 10, {}, len(rows))}
        return de.write_source(db, self.src, rows, meta)

    def test_twice_is_no_change(self):
        fake = FakeDb()
        first = self._load(fake, self.rows)
        self.assertEqual(first["inserted"], len(self.rows))
        second = self._load(fake, self.rows)
        self.assertEqual((second["inserted"], second["updated"], second["deleted"]), (0, 0, 0))
        self.assertEqual(second["unchanged"], len(self.rows))

    def test_removed_row_is_deleted_and_edit_is_update(self):
        fake = FakeDb()
        self._load(fake, self.rows)
        changed = self.rows[:-1]
        changed[0].statement += " (改訂)"
        res = self._load(fake, changed)
        self.assertEqual((res["updated"], res["deleted"]), (1, 1))

    def test_payload_shape(self):
        p = self.rows[2].to_payload()
        self.assertEqual(p["clause_keys"], ["JP GMP Ordinance Article 20"])
        self.assertRegex(p["content_hash"], r"^[0-9a-f]{64}$")
        cols = set(re.search(r"jsonb_to_recordset\(p_rows\) as r\((.*?)\)\s*order", SQL, re.S)[1]
                   .replace("\n", " ").split(","))
        cols = {c.strip().split()[0] for c in cols}
        self.assertLessEqual(cols, set(p), "SQL 이 읽는 열을 수집기가 안 보낸다")

    def test_run_gate_and_sha_skip(self):
        files = {"000281968.xlsx": _b("pmda_fy2025_ja_excerpt.xlsx")}

        def fetch(url):
            return files[url.rsplit("/", 1)[1]], {"last-modified": "x"}

        fake = FakeDb()
        old_sources = de.SOURCES
        de.SOURCES = (de.Source("pmda:000281968", "PMDA", 2025, "ja", "pmda_attachment",
                                de.PMDA_FILES + "000281968.xlsx", de.PMDA_INDEX["ja"], 5),)
        try:
            dry = de.run(apply=False, only=None, fetch=fetch, db=None, do_discover=False)
            self.assertEqual((dry["mode"], dry["failures"]), ("dry-run", []))
            self.assertEqual(dry["sources"][0]["summary"]["rows"], 8)
            db = de.Db("https://x.supabase.co", "k", http=fake)
            r1 = de.run(apply=True, only=None, fetch=fetch, db=db, do_discover=False)
            self.assertEqual(r1["sources"][0]["write"]["inserted"], 8)
            r2 = de.run(apply=True, only=None, fetch=fetch, db=db, do_discover=False)
            self.assertTrue(r2["sources"][0]["write"].startswith("skipped"))
        finally:
            de.SOURCES = old_sources

    def test_structure_floor_fails_without_write(self):
        fake = FakeDb()

        def fetch(url):
            return _b("pmda_fy2025_ja_excerpt.xlsx"), {}

        old_sources = de.SOURCES
        de.SOURCES = (de.Source("pmda:000281968", "PMDA", 2025, "ja", "pmda_attachment",
                                de.PMDA_FILES + "000281968.xlsx", de.PMDA_INDEX["ja"], 33),)
        try:
            db = de.Db("https://x.supabase.co", "k", http=fake)
            rep = de.run(apply=True, only=None, fetch=fetch, db=db, do_discover=False)
            self.assertEqual(rep["failure_kind"], "structure")
            self.assertEqual(fake.calls, [])
        finally:
            de.SOURCES = old_sources

    def test_wrong_year_title_is_structure_error(self):
        src = de.Source("pmda:x", "PMDA", 2024, "ja", "pmda_attachment", "https://x/x.xlsx", "", 1)
        with self.assertRaises(de.StructureError):
            de.parse_source(src, _b("pmda_fy2025_ja_excerpt.xlsx"))


class DiscoverTest(unittest.TestCase):
    def test_known_alias_and_new(self):
        ja = ('<a href="/files/000281968.xlsx">Annual Report 2025 別紙</a>'
              '<a href="/files/000283013.xlsx">Attachment</a>'
              '<a href="/files/000299999.xlsx">Annual Report 2026 別紙[40KB]</a>')
        en = '<a href="/files/000283020.xlsx">Attachment</a>'
        mh = ('<a href="https://assets.publishing.service.gov.uk/media/5f/2019_Deficiency_Data.xlsx">x</a>')
        pages = {de.PMDA_INDEX["ja"]: ja, de.PMDA_INDEX["en"]: en, de.MHRA_INDEX: mh}
        d = de.discover(lambda u: (pages[u].encode("utf-8"), {}))
        self.assertEqual(d["failures"], [])
        self.assertEqual([f["url"] for f in d["new_files"]], ["https://www.pmda.go.jp/files/000299999.xlsx"])

    def test_page_without_links_is_failure(self):
        d = de.discover(lambda u: (b"<html></html>", {}))
        self.assertEqual(len(d["failures"]), 3)


class SourcesTableTest(unittest.TestCase):
    def test_keys_match_sql_check(self):
        pat = re.compile(r"^(pmda|mhra):[a-z0-9_]{3,80}$")
        for s in de.SOURCES:
            self.assertRegex(s.key, pat)
            self.assertIn(s.kind, ("pmda_attachment", "mhra_xlsx", "mhra_trend_pdf"))
        self.assertEqual(len({s.key for s in de.SOURCES}), len(de.SOURCES))
        self.assertEqual(len({(s.agency, s.year, s.lang) for s in de.SOURCES}), len(de.SOURCES))

    def test_categories_are_real_taxonomy_codes(self):
        import grm_findings as gf

        codes = {c.code for c in gf.FINDING_TAXONOMY}
        used = {c for _, c in de.PMDA_CATEGORY_MAP} | set(de.EU_PART_MAP.values())
        self.assertLessEqual(used, codes)
        basis = set(re.search(r"grm_category_basis in\s*\((.*?)\)", SQL, re.S)[1].replace("'", "")
                    .replace("\n", "").replace(" ", "").split(","))
        self.assertEqual(basis, {"", "agency_category_map", "eu_gmp_part_map", "text_classifier"})


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


class MigrationContractTest(unittest.TestCase):
    def test_number_is_unique(self):
        names = [p.name for p in (ROOT / "web" / "migrations").glob("*.sql")]
        self.assertEqual([n for n in names if n.startswith("134_")], ["134_deficiency_examples.sql"])

    def test_rls_and_revokes(self):
        n = _norm(SQL)
        for t in ("deficiency_example_sources", "deficiency_examples"):
            self.assertIn(f"alter table public.{t} enable row level security", re.sub(r" +", " ", n))
            self.assertRegex(n, rf"revoke all on public\.{t} +from public, anon, authenticated;")
            self.assertRegex(n, rf"grant all on public\.{t} +to service_role;")
        self.assertNotIn("create policy", n)
        self.assertNotIn("security definer", n)
        for fn in ("deficiency_examples_upsert(text, text, integer, text, jsonb)",
                   "deficiency_examples_finalize(jsonb, integer[])"):
            self.assertIn(f"revoke all on function public.{fn}", n.replace("\n", " "))
            self.assertIn(f"grant execute on function public.{fn} to service_role", n)
        self.assertNotRegex(n, r"grant [^;]* to (anon|authenticated|public)")

    def test_functions_pin_search_path(self):
        self.assertEqual(SQL.count("create or replace function"), 2)
        self.assertEqual(SQL.count("set search_path = public"), 2)

    def test_idempotent_key_and_only_additive(self):
        n = _norm(SQL)
        self.assertIn("unique (source_key, row_no)", n)
        self.assertIn("on conflict (source_key, row_no) do update", n)
        self.assertIn("where t.content_hash is distinct from excluded.content_hash", n)
        for bad in ("drop table", "alter table public.findings", "truncate"):
            self.assertNotIn(bad, re.sub(r"--[^\n]*", "", n))


class CliGuardTest(unittest.TestCase):
    def test_stdout_guard_first(self):
        src = (ROOT / "collect_deficiency_examples.py").read_text(encoding="utf-8")
        body = src.split("def main(", 1)[1]
        self.assertLess(body.index("reconfigure"), body.index("argparse.ArgumentParser"))

    def test_flag_documented(self):
        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertRegex(env, r"(?m)^ENABLE_DEFICIENCY_EXAMPLES=false$")


if __name__ == "__main__":
    unittest.main()
