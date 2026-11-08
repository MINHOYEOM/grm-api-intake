#!/usr/bin/env python3
"""collect_fda_import_alert.py · 132_fda_import_alert.sql — 표적 시험(네트워크·DB 0).

픽스처 tests/fixtures/fda_import_alert/ia_66-40_excerpt.html 은 2026-11-08 에 `requests` 로 받은
실제 66-40 원문의 조각이다(머리말 + ARMENIA 2곳 + KOREA 앞 4곳). 원문 그대로라 cp1252 바이트(0x93)와
같은 업체의 표기 중복("BABIKIAN HEALTHCARE PRODUCTS" / "Babikian Healthcare Products, CJSC")이 들어 있다.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import collect_fda_import_alert as ia

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "fda_import_alert" / "ia_66-40_excerpt.html"
_MIG = _ROOT / "web" / "migrations" / "132_fda_import_alert.sql"
_RUN = "2026-11-08"


def _page() -> str:
    return ia.decode_page(_FIX.read_bytes())


def _parsed() -> ia.ParsedAlert:
    return ia.parse_alert_page(_page())


def _big_page(n: int) -> str:
    """하한 검사를 통과할 만큼 업체 블록을 복제한 합성 페이지(이름만 바꿔 서로 다른 업체)."""
    page = _page()
    m = re.search(r'(<div class="div-info">.*?)(?=<div class="div-info">)', page, re.S)
    block = m.group(1)
    copies = "".join(block.replace("BABIKIAN HEALTHCARE PRODUCTS", f"SYNTH FIRM {i}") for i in range(n))
    return page.replace(block, block + copies, 1)


class DecodeTest(unittest.TestCase):
    def test_cp1252_bytes_in_utf8_page_do_not_crash(self):
        data = _FIX.read_bytes()
        self.assertIn(b"\x93", data)            # 원문 그대로의 스마트 따옴표
        with self.assertRaises(UnicodeDecodeError):
            data.decode("utf-8")
        self.assertIn("“", ia.decode_page(data))


class ParserTest(unittest.TestCase):
    def test_header_fields(self):
        p = _parsed()
        self.assertEqual(p.alert_no, "66-40")
        self.assertEqual(p.page_published, "2026-11-06")
        self.assertFalse(p.other_list_present)

    def test_firm_blocks(self):
        p = _parsed()
        self.assertEqual(p.raw_block_count, 6)
        self.assertEqual(len(p.firms), 6)
        names = [f.name for f in p.firms]
        self.assertEqual(names[0], "BABIKIAN HEALTHCARE PRODUCTS")
        self.assertIn("Amaros Co., Ltd", names)
        amaros = next(f for f in p.firms if f.name.startswith("Amaros"))
        self.assertEqual(amaros.country, "KOREA (THE REPUBLIC OF)")
        self.assertEqual(amaros.city, "Seongnam")
        self.assertEqual(amaros.published, "2017-08-23")
        self.assertTrue(amaros.address.endswith("KOREA (THE REPUBLIC OF)"))

    def test_product_codes_and_their_own_dates(self):
        p = _parsed()
        babikian = p.firms[0]
        self.assertEqual(babikian.products[0].code, "54-D---")
        self.assertEqual(babikian.products[0].published, "2026-06-22")
        barox = next(f for f in p.firms if f.name.startswith("Barox"))
        # 제품 레벨 날짜는 업체 헤더 날짜와 다를 수 있다(실제 추가 시점).
        self.assertEqual(barox.published, "2018-11-08")
        self.assertIn("2022-05-26", {x.published for x in barox.products})
        self.assertTrue(all(f.products for f in p.firms))

    def test_no_red_anchor_means_no_firms(self):
        page = _page().replace('<a name="RedFirm">', "")
        p = ia.parse_alert_page(page)
        self.assertEqual(p.firms, [])

    def test_green_list_is_cut_off(self):
        page = _page()
        i = page.index("<h4>KOREA (THE REPUBLIC OF)</h4>")
        cut = page[:i] + '<a name="GreenFirm"></a>' + page[i:]
        p = ia.parse_alert_page(cut)
        self.assertTrue(p.other_list_present)
        self.assertEqual({f.country for f in p.firms}, {"ARMENIA"})


class NormalizeTest(unittest.TestCase):
    def test_legal_suffix_and_punctuation(self):
        for raw in ("COSMAX, Inc.", "Cosmax Inc", "COSMAX INC.", "cosmax co., ltd."):
            self.assertEqual(ia.normalize_firm_name(raw), "COSMAX", raw)
        self.assertEqual(ia.normalize_firm_name("Tianjin Boai Pharmaceutical Co. Ltd"),
                         ia.normalize_firm_name("Tianjin Boai Pharmaceutical Ltd. co."))
        self.assertEqual(ia.normalize_firm_name("PT. Kalbe Farma Tbk"), "KALBE FARMA")
        self.assertEqual(ia.normalize_firm_name("C&T Dream Co., Ltd."), "C AND T DREAM")

    def test_suffix_only_in_tail_and_all_suffix_name_kept(self):
        self.assertEqual(ia.normalize_firm_name("Co Ltd"), "CO LTD")
        self.assertEqual(ia.normalize_firm_name("Leo Pharmaceutical Products"), "LEO PHARMACEUTICAL PRODUCTS")

    def test_key_ignores_spacing_and_city(self):
        self.assertEqual(ia.firm_key("66-41", "KOREA (THE REPUBLIC OF)", "BIO PORT KOREA"),
                         ia.firm_key("66-41", "KOREA (THE REPUBLIC OF)", "Bioport Korea Inc."))
        self.assertNotEqual(ia.firm_key("66-40", "KOREA (THE REPUBLIC OF)", "Cosmax"),
                            ia.firm_key("66-41", "KOREA (THE REPUBLIC OF)", "Cosmax"))
        self.assertNotEqual(ia.firm_key("66-40", "CHINA", "Cosmax"),
                            ia.firm_key("66-40", "KOREA (THE REPUBLIC OF)", "Cosmax"))

    def test_korea(self):
        self.assertTrue(ia.is_korea("KOREA (THE REPUBLIC OF)"))
        self.assertFalse(ia.is_korea("KOREA (THE DEMOCRATIC PEOPLE'S REPUBLIC OF)"))
        self.assertFalse(ia.is_korea("JAPAN"))

    def test_in_document_duplicates_merge(self):
        cur, merged = ia.consolidate("66-40", _parsed().firms)
        self.assertEqual(merged, 1)              # Babikian 두 표기 → 한 업체
        self.assertEqual(len(cur), 5)
        bab = next(r for r in cur.values() if r["name_norm"] == "BABIKIAN HEALTHCARE PRODUCTS")
        self.assertEqual(len(bab["addresses"]), 2)
        self.assertEqual(sum(r["is_korea"] for r in cur.values()), 4)
        chem = next(r for r in cur.values() if r["name_norm"] == "CHEMLAND")
        self.assertEqual(len(chem["product_codes"]), len(set(chem["product_codes"])))


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.cur, _ = ia.consolidate("66-40", _parsed().firms)

    def _prev_from(self, cur, *, active=True):
        return {k: {"firm_key": k, "firm_name": r["firm_name"], "country": r["country"],
                    "firm_published": r["firm_published"], "product_codes": list(r["product_codes"]),
                    "active": active} for k, r in cur.items()}

    def test_first_run_is_seed_only(self):
        ev = ia.compute_diff("66-40", self.cur, {}, seeded=False, run_date=_RUN)
        self.assertEqual({e["event"] for e in ev}, {"seed"})
        self.assertEqual(len(ev), len(self.cur))

    def test_same_snapshot_twice_gives_nothing(self):
        prev = self._prev_from(self.cur)
        self.assertEqual(ia.compute_diff("66-40", self.cur, prev, seeded=True, run_date=_RUN), [])

    def test_notation_drift_is_not_fake_new(self):
        """다음 날 원천이 대소문자·구두점·법인격·도시 표기만 바꿔도 가짜 신규/해제 0."""
        prev = self._prev_from(self.cur)
        page = (_page().replace("Amaros Co., Ltd", "AMAROS CO LTD.")
                .replace("Barox Co., Ltd.", "Barox Company Limited")
                .replace("Seongnam,", "Seongnam-si,"))
        cur2, _ = ia.consolidate("66-40", ia.parse_alert_page(page).firms)
        ev = ia.compute_diff("66-40", cur2, prev, seeded=True, run_date=_RUN)
        self.assertEqual([e for e in ev if e["event"] in ("listed", "delisted")], [])

    def test_new_and_removed(self):
        prev = self._prev_from(self.cur)
        gone_key = next(k for k, r in self.cur.items() if r["name_norm"] == "CHEMLAND")
        cur2 = {k: v for k, v in self.cur.items() if k != gone_key}
        newk = ia.firm_key("66-40", "KOREA (THE REPUBLIC OF)", "New Pharma Co., Ltd.")
        cur2[newk] = dict(next(iter(self.cur.values())), firm_key=newk, firm_name="New Pharma Co., Ltd.")
        ev = ia.compute_diff("66-40", cur2, prev, seeded=True, run_date=_RUN)
        kinds = {(e["event"], e["firm_key"]) for e in ev}
        self.assertEqual(kinds, {("listed", newk), ("delisted", gone_key)})
        self.assertTrue(all(e["event_date"] == _RUN for e in ev))

    def test_relisted_and_products_changed(self):
        prev = self._prev_from(self.cur)
        k_inactive = next(iter(prev))
        prev[k_inactive]["active"] = False
        k_prod = list(prev)[1]
        prev[k_prod]["product_codes"] = prev[k_prod]["product_codes"][1:] + ["99-X---"]
        ev = ia.compute_diff("66-40", self.cur, prev, seeded=True, run_date=_RUN)
        by = {e["firm_key"]: e for e in ev}
        self.assertEqual(by[k_inactive]["event"], "relisted")
        self.assertEqual(by[k_prod]["event"], "products_changed")
        self.assertEqual(by[k_prod]["detail"]["removed"], ["99-X---"])
        self.assertEqual(len(by[k_prod]["detail"]["added"]), 1)

    def test_inactive_missing_is_not_delisted_again(self):
        prev = self._prev_from(self.cur, active=False)
        ev = ia.compute_diff("66-40", {}, prev, seeded=True, run_date=_RUN)
        self.assertEqual(ev, [])

    def test_recent_counts(self):
        rc = ia.recent_counts(self.cur, "2026-07-01")
        self.assertEqual(rc["firms_listed"], 1)          # Babikian 06-22
        self.assertEqual(rc["firms_listed_korea"], 0)
        self.assertEqual(ia.recent_counts(self.cur, _RUN)["firms_listed"], 0)


class StructureValidationTest(unittest.TestCase):
    def test_excerpt_fails_floor(self):
        fails = ia.validate_structure("66-40", _parsed())
        self.assertTrue(any(f.startswith("too_few_firms") for f in fails), fails)

    def test_big_page_passes(self):
        p = ia.parse_alert_page(_big_page(300))
        self.assertEqual(ia.validate_structure("66-40", p), [])

    def test_wrong_alert_number(self):
        p = ia.parse_alert_page(_big_page(300))
        self.assertIn("alert_no_mismatch:66-40", ia.validate_structure("66-41", p)[0])

    def test_class_rename_is_caught(self):
        """FDA CMS 리디자인으로 클래스 이름이 바뀌면 0건이 아니라 실패다."""
        page = _big_page(300).replace('class="div-name floatright"', 'class="firm-date"')
        fails = ia.validate_structure("66-40", ia.parse_alert_page(page))
        self.assertTrue(any(f.startswith("field_rate") for f in fails), fails)
        page2 = _big_page(300).replace('class="div-info"', 'class="firm-card"')
        fails2 = ia.validate_structure("66-40", ia.parse_alert_page(page2))
        self.assertIn("no_firm_blocks", fails2)

    def test_product_markup_change_is_caught(self):
        page = _big_page(300).replace('class="floatright marginsmall"', 'class="pdate"')
        fails = ia.validate_structure("66-40", ia.parse_alert_page(page))
        self.assertTrue(any(f.startswith("product_rate") for f in fails), fails)

    def test_mass_delist_guard(self):
        p = ia.parse_alert_page(_big_page(300))
        self.assertEqual(ia.validate_structure("66-40", p, prev_active=320, curr_unique=300), [])
        fails = ia.validate_structure("66-40", p, prev_active=400, curr_unique=300)
        self.assertTrue(any(f.startswith("mass_delist_guard") for f in fails), fails)


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return self._p


class _FakeHttp:
    def __init__(self, *, snapshot_rows, listing_rows):
        self.snapshot_rows = snapshot_rows
        self.listing_rows = listing_rows
        self.posts = []

    def get(self, url, params=None, headers=None, timeout=None):
        if url.endswith("import_alert_snapshot"):
            return _FakeResp(200, self.snapshot_rows)
        return _FakeResp(200, self.listing_rows)

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json))
        return _FakeResp(200, {"upserted": len(json["p_payload"]["listings"])})


class RunAlertTest(unittest.TestCase):
    def _fetch_big(self, alert_no):
        return _big_page(300).encode("utf-8"), {"last_modified": "Fri, 06 Nov 2026 20:20:37 GMT", "url": "x"}

    def test_structure_failure_writes_nothing(self):
        http = _FakeHttp(snapshot_rows=[], listing_rows=[])
        store = ia.Store("https://example.invalid", "k", http=http)
        rep = ia.run_alert("66-40", run_date=_RUN, fetch=lambda a: (_FIX.read_bytes(), {}),
                           store=store, write=True, raw_dir=None)
        self.assertTrue(any(f.startswith("structure:too_few_firms") for f in rep["failures"]))
        self.assertEqual(http.posts, [])

    def test_seed_write_payload(self):
        http = _FakeHttp(snapshot_rows=[], listing_rows=[])
        store = ia.Store("https://example.invalid", "k", http=http)
        with tempfile.TemporaryDirectory() as d:
            rep = ia.run_alert("66-40", run_date=_RUN, fetch=self._fetch_big, store=store,
                               write=True, raw_dir=Path(d))
            self.assertEqual(len(list(Path(d).glob("*.html.gz"))), 1)
        self.assertEqual(rep["failures"], [])
        self.assertTrue(rep["written"])
        self.assertEqual(set(rep["events"]), {"seed"})
        url, body = http.posts[0]
        self.assertTrue(url.endswith("/rest/v1/rpc/import_alert_apply"))
        payload = body["p_payload"]
        self.assertEqual(payload["alert_no"], "66-40")
        self.assertEqual(len(payload["listings"]), rep["firms_unique"])
        self.assertRegex(payload["snapshot"]["sha256"], r"^[0-9a-f]{64}$")

    def test_dry_run_does_not_post(self):
        http = _FakeHttp(snapshot_rows=[], listing_rows=[])
        store = ia.Store("https://example.invalid", "k", http=http)
        rep = ia.run_alert("66-40", run_date=_RUN, fetch=self._fetch_big, store=store,
                           write=False, raw_dir=None)
        self.assertEqual(rep["failures"], [])
        self.assertEqual(http.posts, [])

    def test_mass_disappearance_blocks_write(self):
        prev_rows = [{"firm_key": f"66-40|X|F{i}", "firm_name": f"F{i}", "country": "X",
                      "firm_published": None, "product_codes": [], "active": True} for i in range(600)]
        http = _FakeHttp(snapshot_rows=[{"id": 1}], listing_rows=prev_rows)
        store = ia.Store("https://example.invalid", "k", http=http)
        rep = ia.run_alert("66-40", run_date=_RUN, fetch=self._fetch_big, store=store,
                           write=True, raw_dir=None)
        self.assertTrue(any("mass_delist_guard" in f for f in rep["failures"]), rep["failures"])
        self.assertEqual(http.posts, [])

    def test_fetch_failure_is_reported(self):
        def boom(a):
            raise RuntimeError("HTTP 403")
        rep = ia.run_alert("66-40", run_date=_RUN, fetch=boom, store=None, write=False, raw_dir=None)
        self.assertTrue(rep["failures"][0].startswith("fetch:"))


class MainGateTest(unittest.TestCase):
    def _main(self, argv, env):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", env, clear=False), \
                mock.patch.object(ia, "fetch_page", side_effect=lambda a: (_FIX.read_bytes(), {})):
            out = Path(d) / "r.json"
            code = ia.main(argv + ["--output", str(out), "--alerts", "66-40", "--run-date", _RUN])
            return code, json.loads(out.read_text(encoding="utf-8"))

    def test_apply_without_flag_is_dry_run(self):
        code, rep = self._main(["--apply", "--no-db"], {"ENABLE_FDA_IMPORT_ALERT": "false"})
        self.assertEqual(rep["mode"], "dry-run")
        self.assertEqual(code, 2)                     # 픽스처는 하한 미달 → 구조 실패 = exit 2
        self.assertEqual(rep["failure_kind"], "structure")

    def test_apply_with_flag_but_no_creds_refuses(self):
        with mock.patch.dict("os.environ", {"ENABLE_FDA_IMPORT_ALERT": "true", "SUPABASE_URL": "",
                                            "SUPABASE_SERVICE_ROLE_KEY": ""}):
            self.assertEqual(ia.main(["--apply", "--alerts", "66-40"]), 2)


class MigrationContractTest(unittest.TestCase):
    TABLES = ("import_alert_snapshot", "import_alert_listing", "import_alert_event")

    @classmethod
    def setUpClass(cls):
        sql = _MIG.read_text(encoding="utf-8")
        cls.code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
        cls.flat = " ".join(cls.code.split())

    def test_rls_and_revokes_for_every_table(self):
        for t in self.TABLES:
            self.assertIn(f"alter table public.{t} enable row level security", self.flat, t)
            self.assertRegex(self.flat, rf"revoke all on public\.{t}\s+from public, anon, authenticated;", t)
            self.assertRegex(self.flat, rf"grant all on public\.{t}\s+to service_role;", t)

    def test_no_grant_to_client_roles_and_no_policies(self):
        self.assertNotRegex(self.flat, r"grant [^;]* to [^;]*(anon|authenticated|public)\b")
        self.assertNotIn("create policy", self.flat)

    def test_function_privileges(self):
        self.assertIn("revoke all on function public.import_alert_apply(jsonb) from public, anon, authenticated;",
                      self.flat)
        self.assertIn("grant execute on function public.import_alert_apply(jsonb) to service_role;", self.flat)
        self.assertNotIn("security definer", self.flat)
        self.assertIn("set search_path = public", self.flat)

    def test_additive_only(self):
        for bad in ("drop table", "drop function", "alter table public.findings", "truncate"):
            self.assertNotIn(bad, self.flat)
        for t in self.TABLES:
            self.assertIn(f"create table if not exists public.{t}", self.flat)

    def test_event_vocabulary_matches_collector(self):
        m = re.search(r"event in \(([^)]*)\)", self.flat)
        sql_events = set(re.findall(r"'(\w+)'", m.group(1)))
        self.assertEqual(sql_events, {"seed", "listed", "relisted", "delisted", "products_changed"})

    def test_payload_fields_exist_in_function(self):
        cur, _ = ia.consolidate("66-40", _parsed().firms)
        payload = ia.build_payload("66-40", _RUN, {}, cur, [])
        body = self.flat[self.flat.index("create or replace function public.import_alert_apply"):]
        for k in payload["listings"][0]:
            self.assertRegex(body, rf"\b{k} ", f"함수가 listings.{k} 를 읽지 않는다")

    def test_migration_number_is_unique(self):
        same = [p.name for p in _MIG.parent.glob("132_*.sql")]
        self.assertEqual(same, [_MIG.name])


if __name__ == "__main__":
    unittest.main(verbosity=2)
