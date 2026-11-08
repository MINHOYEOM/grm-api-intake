#!/usr/bin/env python3
"""수집기 제외 장부(intake_exclusion · migration 130) 표적 시험.

① 마이그레이션 130 텍스트 계약(표·RLS·정책 없음·public/anon/authenticated 명시 회수·service_role 만)
② 규칙 지문: 키워드가 바뀌면 달라지고 주석·공백은 안 바꾼다 — 실제 483 규칙 체인이 지문에 들어 있다
③ 장부 동작: 같은 규칙 버전이면 건너뛰고, 규칙 버전이 바뀌면 다시 판정하며, 본문 미확보 판정은 재판정
④ 저장 실패(읽기·쓰기·예외)는 수집을 멈추지 않는다
⑤ 연결 지점: 483(건너뜀·재판정·해제), WL 문서키, 빈 document_id, 비활성/ dry-run 은 no-op
"""
from __future__ import annotations

import importlib
import os
import re
import sys
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

import collect_fda_483 as f  # noqa: E402
import collect_intake as ci  # noqa: E402
import intake_exclusion as xl  # noqa: E402

SQL = (ROOT / "web" / "migrations" / "130_intake_exclusion_ledger.sql").read_text(encoding="utf-8")
START, END = date(2026, 1, 1), date(2026, 12, 31)
RUN = date(2026, 11, 8)


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _norm(sql: str) -> str:
    return " ".join(_code(sql).split()).lower()


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else []
    return r


def _ledger(known=None):
    led = xl.ExclusionLedger()
    assert led.configure("https://x.supabase.co", "SECRET-KEY-VALUE", RUN)
    for k, v in (known or {}).items():
        led._known[k] = v
    return led


class MigrationContractTest(unittest.TestCase):
    def test_is_number_130_and_follows_129(self):
        names = sorted(p.name for p in (ROOT / "web" / "migrations").glob("*.sql"))
        self.assertIn("130_intake_exclusion_ledger.sql", names)
        # 130 은 129 바로 다음이어야 한다(뒤에 131 이후가 붙는 것은 정상 — 최댓값을 못 박지 않는다).
        numbered = sorted(n[:3] for n in names if re.match(r"\d{3}_", n))
        self.assertIn("129", numbered)
        self.assertEqual(numbered[numbered.index("129") + 1], "130")

    def test_table_key_and_columns(self):
        n = _norm(SQL)
        self.assertIn("create table if not exists public.intake_exclusion", n)
        self.assertIn("primary key (source, document_id)", n)
        self.assertIn("dedup_key text generated always as (source || '::' || document_id) stored", n)
        for col in ("official_url", "title", "published_date", "reason_code", "rule_version",
                    "basis", "first_seen", "last_seen"):
            self.assertIn(col, n)
        self.assertIn("rule_version text not null", n)

    def test_rls_on_no_policies_and_explicit_revokes(self):
        n = _norm(SQL)
        self.assertIn("alter table public.intake_exclusion enable row level security", n)
        self.assertNotIn("create policy", n, "정책이 있으면 클라이언트 경로가 생긴다")
        self.assertIn("revoke all on public.intake_exclusion from public, anon, authenticated", n)
        self.assertIn("grant all on public.intake_exclusion to service_role", n)
        for role in ("anon", "authenticated", "public"):
            self.assertIsNone(re.search(r"grant [^;]* to [^;]*\b" + role + r"\b", n),
                              f"{role} 에게 grant 가 있다")

    def test_functions_revoked_from_three_and_service_role_only(self):
        n = _norm(SQL)
        for sig in ("intake_exclusion_record(jsonb)", "intake_exclusion_release(jsonb)"):
            self.assertIn(f"revoke all on function public.{sig} from public, anon, authenticated", n)
            self.assertIn(f"grant execute on function public.{sig} to service_role", n)
            self.assertNotIn(f"function public.{sig} to anon", n)
        self.assertEqual(n.count("set search_path = public"), 2)
        self.assertNotIn("security definer", n)

    def test_upsert_keeps_first_seen_and_moves_last_seen_forward(self):
        n = _norm(SQL)
        self.assertIn("on conflict (source, document_id) do update", n)
        self.assertIn("last_seen = greatest(t.last_seen, excluded.last_seen)", n)
        upd = n[n.index("do update"):n.index("returning 1")]
        self.assertNotIn("first_seen", upd, "first_seen 은 갱신하면 안 된다")
        self.assertIn("distinct on (r.source, r.document_id)", n)

    def test_additive_only(self):
        n = _norm(SQL)
        for bad in ("drop table", "alter table public.raw_signals", "alter table public.findings",
                    "drop function", "delete from public.raw_signals"):
            self.assertNotIn(bad, n)


class RuleFingerprintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        sys.path.insert(0, self.tmp.name)
        self.addCleanup(lambda: sys.path.remove(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)
        xl._module_index.cache_clear()
        self.addCleanup(xl._module_index.cache_clear)

    def _write(self, text):
        Path(self.tmp.name, "fp_rule_mod.py").write_text(textwrap.dedent(text), encoding="utf-8")
        sys.modules.pop("fp_rule_mod", None)
        xl._module_index.cache_clear()
        importlib.invalidate_caches()

    BASE = """
        TERMS = ["cosmetic", "veterinary"]
        UNRELATED = 5
        def helper(blob):
            return any(t in blob for t in TERMS)
        def gate(text):
            # 주석
            return "Unrelated" if helper(text) else "Possible"
    """

    def _fp(self):
        return xl.rule_fingerprint(("gate",), ("fp_rule_mod",))

    def test_keyword_change_changes_version(self):
        self._write(self.BASE)
        a = self._fp()
        self._write(self.BASE.replace('"cosmetic", ', ""))
        self.assertNotEqual(a, self._fp())

    def test_function_logic_change_changes_version(self):
        self._write(self.BASE)
        a = self._fp()
        self._write(self.BASE.replace('"Unrelated"', '"Pending"'))
        self.assertNotEqual(a, self._fp())

    def test_comment_whitespace_and_unrelated_globals_do_not_change_version(self):
        self._write(self.BASE)
        a = self._fp()
        self._write(self.BASE.replace("# 주석", "# 다른 주석\n\n").replace("UNRELATED = 5", "UNRELATED = 99"))
        self.assertEqual(a, self._fp(), "규칙이 읽지 않는 전역·주석은 지문을 바꾸면 안 된다")

    def test_salt_changes_version(self):
        self._write(self.BASE)
        self.assertNotEqual(xl.rule_fingerprint(("gate",), ("fp_rule_mod",), salt="1"),
                            xl.rule_fingerprint(("gate",), ("fp_rule_mod",), salt="2"))

    def test_real_483_rule_chain_is_covered(self):
        names = {n for n, _ in xl.rule_closure(
            ("is_out_of_scope_483", "is_out_of_scope_483_establishment", "compute_relevance",
             "strip_483_form_boilerplate", "_kw_any", "QA_HARD_EXCLUDE_TERMS"),
            ("collect_fda_483", "grm_taxonomy"))}
        for must in ("QA_HARD_EXCLUDE_TERMS", "QA_EXCLUDE_KEYWORDS", "QA_DEVICE_EXCLUDE_TERMS",
                     "_FDA483_OUT_OF_SCOPE_EST_RE", "_FDA483_DEVICE_TEXT_RE", "_FDA483_BOILERPLATE_RES",
                     "_VET_DOMAIN_RE", "is_veterinary_domain"):
            self.assertIn(must, names, f"{must} 가 483 규칙 지문 밖이다 — 바뀌어도 재판정이 안 된다")

    def test_real_versions_are_stable_and_distinct(self):
        v483 = f.exclusion_rule_version()
        self.assertEqual(v483, f.exclusion_rule_version())
        import collect_mfds
        self.assertEqual(len(v483), 12)
        self.assertNotEqual(v483, collect_mfds._exclusion_rule_version())
        self.assertNotEqual(v483, ci._wl_exclusion_rule_version())


class LedgerBehaviourTest(unittest.TestCase):
    def test_same_version_is_known_and_counted(self):
        led = _ledger({("FDA 483", "fda483-1"): xl.KnownEntry("v1aaaa", "unrelated_domain", "text")})
        ent = led.current_entry("FDA 483", "fda483-1", "v1aaaa")
        self.assertEqual(ent.reason_code, "unrelated_domain")
        self.assertEqual(led.summary()["by_source"]["FDA 483"]["skipped_known"], 1)

    def test_rule_version_change_means_rejudge_not_blacklist(self):
        led = _ledger({("FDA 483", "fda483-1"): xl.KnownEntry("v1aaaa", "unrelated_domain", "text")})
        self.assertIsNone(led.current_entry("FDA 483", "fda483-1", "v2bbbb"))
        self.assertEqual(led.summary()["by_source"], {})

    def test_text_missing_basis_is_never_final(self):
        led = _ledger({("FDA 483", "fda483-1"): xl.KnownEntry("v1aaaa", "unrelated_domain",
                                                              xl.BASIS_TEXT_MISSING)})
        self.assertIsNone(led.current_entry("FDA 483", "fda483-1", "v1aaaa"))

    def test_disabled_ledger_is_noop(self):
        led = xl.ExclusionLedger()
        self.assertFalse(led.record(source="s", document_id="d", reason_code="ab",
                                    rule_version="vvvv"))
        self.assertIsNone(led.current_entry("s", "d", "vvvv"))
        led.flush()   # 예외 없이
        self.assertFalse(led.configure("http://insecure", "k", RUN))
        self.assertFalse(led.configure("https://x", "", RUN))

    def test_record_validates_and_dedups_per_key(self):
        led = _ledger()
        self.assertFalse(led.record(source="s", document_id="d", reason_code="Bad Code",
                                    rule_version="vvvv"))
        self.assertFalse(led.record(source="s", document_id="d", reason_code="ok_code",
                                    rule_version="vvvv", basis="nonsense"))
        self.assertTrue(led.record(source="s", document_id="d", reason_code="ok_code",
                                   rule_version="vvvv", published_date="not-a-date"))
        led.record(source="s", document_id="d", reason_code="other_code", rule_version="vvvv")
        s = led.summary()["by_source"]["s"]
        self.assertEqual(s["excluded"], 1)
        self.assertEqual(s["by_reason"], {"other_code": 1})
        self.assertIsNone(next(iter(led._pending.values()))["published_date"] or None)

    def test_flush_posts_rows_and_keeps_secret_out_of_summary(self):
        led = _ledger()
        led.record(source="FDA 483", document_id="fda483-9", official_url="https://u",
                   title="Hankook   Cosmetics", published_date="2026-11-03",
                   reason_code="unrelated_domain", rule_version="abc123456789", basis="text")
        with patch.object(xl.requests, "post", return_value=_resp(200)) as post:
            led.flush()
        url = post.call_args.args[0]
        self.assertTrue(url.endswith("/rest/v1/rpc/intake_exclusion_record"))
        row = post.call_args.kwargs["json"]["p_rows"][0]
        self.assertEqual(row["title"], "Hankook Cosmetics")
        self.assertEqual(row["seen_on"], "2026-11-08")
        self.assertEqual(row["published_date"], "2026-11-03")
        self.assertEqual(led.summary()["flushed"], 1)
        self.assertNotIn("SECRET-KEY-VALUE", repr(led.summary()))

    def test_load_known_reads_pages_and_failure_is_graceful(self):
        led = _ledger()
        rows = [{"document_id": f"fda483-{i}", "rule_version": "v1aaaa",
                 "reason_code": "unrelated_domain", "basis": "text"} for i in range(3)]
        with patch.object(xl.requests, "get", return_value=_resp(200, rows)):
            self.assertEqual(led.load_known(["FDA 483"]), 3)
        self.assertFalse(led.load_failed)

        led2 = _ledger()
        with patch.object(xl.requests, "get", return_value=_resp(404)):
            self.assertEqual(led2.load_known(["FDA 483"]), 0)   # 표 미적용 상태
        self.assertTrue(led2.load_failed)
        self.assertEqual(led2.last_error, "http_404")
        self.assertIsNone(led2.current_entry("FDA 483", "fda483-0", "v1aaaa"))

        led3 = _ledger()
        with patch.object(xl.requests, "get", side_effect=requests.exceptions.ConnectionError("k=SECRET")):
            led3.load_known(["FDA 483"])
        self.assertTrue(led3.load_failed)
        self.assertNotIn("SECRET", led3.last_error)


class StorageFailureDoesNotStopCollectionTest(unittest.TestCase):
    def test_flush_failures_are_counted_not_raised(self):
        for exc in (requests.exceptions.ConnectionError("boom"), requests.exceptions.Timeout()):
            led = _ledger()
            led.record(source="s", document_id="d", reason_code="ok_code", rule_version="vvvv")
            with patch.object(xl.requests, "post", side_effect=exc):
                led.flush()   # 예외 없음
            self.assertEqual(led.summary()["flush_failed"], 1)
            self.assertEqual(led.summary()["flushed"], 0)

    def test_http_500_retries_once_then_counts_failure(self):
        led = _ledger()
        led.record(source="s", document_id="d", reason_code="ok_code", rule_version="vvvv")
        with patch.object(xl.requests, "post", return_value=_resp(503)) as post:
            led.flush()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(led.summary()["flush_failed"], 1)
        self.assertEqual(led.last_error, "http_503")

    def test_483_collection_continues_when_ledger_store_is_dead(self):
        """장부가 활성인데 저장소가 죽어 있어도 수집 결과는 장부 없는 실행과 같다."""
        rows = Fda483LedgerIntegrationTest._rows(
            [("Acme Pharma", "Drug Manufacturer"), ("Hankook Cosmetics", "Drug Manufacturer")])
        base_items, _e, _f = Fda483LedgerIntegrationTest()._run(rows, None)
        led = _ledger()
        with patch.object(xl, "LEDGER", led):
            items, err, _fetched = Fda483LedgerIntegrationTest()._run(rows, None)
            with patch.object(xl.requests, "post", side_effect=requests.exceptions.ConnectionError()):
                led.flush()
        self.assertIsNone(err)
        self.assertEqual([i.document_id for i in items], [i.document_id for i in base_items])
        self.assertEqual(led.summary()["flush_failed"], len(led._pending))


class Fda483LedgerIntegrationTest(unittest.TestCase):
    @staticmethod
    def _rows(firms):
        raw = [["04/17/2026", name, f"100000{i}",
                f'<a href="/media/{9200 + i}/download">483</a>', "Wisconsin", "",
                est, "05/27/2026", ""] for i, (name, est) in enumerate(firms)]
        return f._datatable_norm_rows(raw)

    def _run(self, rows, known_ids):
        fetched = []

        def _fetch(url):
            fetched.append(url)
            return ("OBSERVATION 1 Equipment is not cleaned at appropriate intervals.", "pdf-ok")

        with patch.dict(os.environ, {"ENABLE_FDA_483_OBSERVATIONS": "false",
                                     "ENABLE_FDA_483_DEEP": "false",
                                     "ENABLE_FDA_483_OCR": "false"}), \
                patch.object(f, "FDA483_EXCERPT_DELAY_SECONDS", 0), \
                patch.object(f, "_fetch_fda483_pdf_text", _fetch), \
                patch.object(f, "_fetch_html_rows",
                             lambda start_date=None: (list(rows), len(rows), False)):
            items, err = f.collect_fda_483(START, END, skip_document_ids=known_ids)
        return items, err, fetched

    FIRMS = [("Acme Pharma", "Drug Manufacturer"),
             ("Hankook Cosmetics", "Drug Manufacturer"),       # 의약품 시설 → 업체명 무시하고 수집(11-08 수리)
             ("Best Bakery Inc", "Bakery"),                    # 시설 유형만으로 범위 밖(PDF 안 받음)
             ("Vet Pharm", "Veterinary Drug Manufacturer"),
             ("Glow Cosmetics", "Manufacturer")]               # 유형이 의약품 아님 → 업체명으로 Unrelated

    def _fresh(self):
        led = _ledger()
        p = patch.object(xl, "LEDGER", led)
        p.start()
        self.addCleanup(p.stop)
        return led

    def test_dropped_documents_leave_a_trace_with_reason_and_version(self):
        led = self._fresh()
        items, err, fetched = self._run(self._rows(self.FIRMS), None)
        self.assertIsNone(err)
        got = {d: r for (_s, d), r in led._pending.items()}
        self.assertEqual({i.document_id for i in items}, {"fda483-9200", "fda483-9201"})
        self.assertEqual(got["fda483-9204"]["reason_code"], "unrelated_domain")
        self.assertEqual(got["fda483-9204"]["basis"], "text")
        self.assertEqual(got["fda483-9202"]["reason_code"], "out_of_scope_type")
        self.assertEqual(got["fda483-9202"]["basis"], "metadata")
        self.assertEqual(got["fda483-9203"]["reason_code"], "veterinary_hard_exclude")
        for r in got.values():
            self.assertEqual(r["rule_version"], f.exclusion_rule_version())
        self.assertEqual(len(fetched), 4, "시설유형 제외(9202)는 PDF 를 받지 않는다")
        self.assertNotIn("fda483-9200", got, "수집된 문서는 장부에 없다")
        self.assertNotIn("fda483-9201", got, "의약품 시설 Hankook 은 업체명으로 버리지 않는다")

    def test_next_day_same_rule_version_does_not_refetch_excluded_pdf(self):
        led = self._fresh()
        rows = self._rows(self.FIRMS)
        self._run(rows, None)
        ver = f.exclusion_rule_version()
        # 어제 장부 상태를 오늘 실행의 '읽은 장부'로.
        led2 = _ledger({("FDA 483", d): xl.KnownEntry(ver, r["reason_code"], r["basis"])
                        for (_s, d), r in led._pending.items()})
        with patch.object(xl, "LEDGER", led2):
            items, _err, fetched = self._run(rows, None)
        self.assertEqual(len(fetched), 2, "수집된 Acme·Hankook 만 PDF 를 받는다(제외 2건은 건너뜀)")
        self.assertEqual({i.document_id for i in items}, {"fda483-9200", "fda483-9201"})
        self.assertEqual(f.LAST_HEALTH["fda483_skipped_excluded"], 2)
        # 마지막 본 날 갱신을 위해 다시 기록은 된다
        self.assertEqual(led2.summary()["by_source"]["FDA 483"]["excluded"], 3)

    def test_rule_change_rejudges_and_hankook_comes_back(self):
        """③ 시나리오: 오판정 규칙을 고치면(규칙 버전이 달라지면) Hankook 이 자동으로 다시 들어온다."""
        rows = self._rows([("Hankook Cosmetics", "Drug Manufacturer")])
        old_ver = "oldversion00"
        led = _ledger({("FDA 483", "fda483-9200"): xl.KnownEntry(old_ver, "unrelated_domain", "text")})
        with patch.object(xl, "LEDGER", led):
            # 지금 규칙 버전은 old_ver 와 다르다 → 건너뛰지 않고 PDF 를 받아 다시 판정
            _i, _e, fetched = self._run(rows, None)
            self.assertEqual(len(fetched), 1)
            # 11-08 수리로 실제 규칙이 고쳐져 패치 없이 통과한다
            items, _e, fetched = self._run(rows, None)
            self.assertEqual([i.document_id for i in items], ["fda483-9200"])
            # 적재 단계가 note_included 를 부르면 예전 제외 행은 해제 대상이 된다
            led._pending.clear()
            led.note_included("FDA 483", "fda483-9200")
            self.assertIn(("FDA 483", "fda483-9200"), led._released)
            with patch.object(xl.requests, "post", return_value=_resp(200)) as post:
                led.flush()
            self.assertTrue(post.call_args.args[0].endswith("intake_exclusion_release"))
            self.assertEqual(post.call_args.kwargs["json"]["p_keys"],
                             [{"source": "FDA 483", "document_id": "fda483-9200"}])

    def test_excluded_without_text_is_recorded_as_text_missing_and_refetched(self):
        led = self._fresh()
        rows = self._rows([("Glow Cosmetics", "Manufacturer")])
        with patch.object(f, "_fetch_fda483_pdf_text", lambda url: ("", "ocr-budget")), \
                patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "false"}), \
                patch.object(f, "FDA483_EXCERPT_DELAY_SECONDS", 0), \
                patch.object(f, "_fetch_html_rows", lambda start_date=None: (list(rows), 1, False)):
            f.collect_fda_483(START, END)
        row = led._pending[("FDA 483", "fda483-9200")]
        self.assertEqual(row["basis"], "text_missing")
        led2 = _ledger({("FDA 483", "fda483-9200"):
                        xl.KnownEntry(row["rule_version"], row["reason_code"], row["basis"])})
        self.assertIsNone(led2.current_entry("FDA 483", "fda483-9200", f.exclusion_rule_version()))

    def test_disabled_ledger_changes_nothing(self):
        rows = self._rows(self.FIRMS)
        base = self._run(rows, None)
        with patch.object(xl, "LEDGER", xl.ExclusionLedger()):
            again = self._run(rows, None)
        self.assertEqual([i.document_id for i in base[0]], [i.document_id for i in again[0]])
        self.assertEqual(len(base[2]), len(again[2]))


class OtherWiringTest(unittest.TestCase):
    def test_missing_document_id_in_insert_items_is_recorded_and_skip_unchanged(self):
        led = _ledger()
        item = ci.IntakeItem(source=ci.SOURCE_RECALL, document_id="", date_iso="2026-11-05",
                             headline="Some product", official_url="https://fda.example/recalls",
                             type_or_class="Class II")
        with patch.object(xl, "LEDGER", led):
            ins, sk, fail = ci.insert_items("t", "db", [item], RUN, ci.now_kst(), set(), True)
        self.assertEqual((ins, sk, fail), (0, 0, 0))
        (key, row), = led._pending.items()
        self.assertEqual(key[0], ci.SOURCE_RECALL)
        self.assertTrue(key[1].startswith("noid-"))
        self.assertEqual(row["reason_code"], "missing_document_id")

    def test_insert_items_releases_previously_excluded_doc(self):
        led = _ledger({(ci.SOURCE_FDA_WL, "abc123def456"): xl.KnownEntry("old000", "low_value_food_supplement",
                                                                         "metadata")})
        item = ci.IntakeItem(source=ci.SOURCE_FDA_WL, document_id="abc123def456", date_iso="2026-11-05",
                             headline="x", official_url="https://u", type_or_class="Warning Letter")
        with patch.object(xl, "LEDGER", led):
            ci.insert_items("t", "db", [item], RUN, ci.now_kst(), set(), True)
        self.assertIn((ci.SOURCE_FDA_WL, "abc123def456"), led._released)

    def test_wl_exclusion_uses_same_doc_id_as_build_wl_item(self):
        led = _ledger()
        with patch.object(xl, "LEDGER", led):
            ci._record_wl_exclusion("Acme Foods", "https://www.fda.gov/wl/acme", "2026-11-05",
                                    "CGMP/Food", "office_gate_exclude")
        (_k, doc_id), = led._pending.keys()
        built = ci.build_wl_item(firm="Acme Foods", posted_raw="11/05/2026", letter_date_raw="10/30/2026",
                                 issuing_office="CDER", subject="CGMP/Food",
                                 wl_href="https://www.fda.gov/wl/acme", office_verdict="keep",
                                 excerpt=None, full=None)
        self.assertEqual(doc_id, built.document_id)

    def test_configure_is_off_by_default_and_in_dry_run(self):
        cfg = MagicMock(findings_supabase_url="https://x.supabase.co")
        env = {"SUPABASE_SERVICE_ROLE_KEY": "k"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ENABLE_INTAKE_EXCLUSION_LEDGER", None)
            self.assertFalse(ci._configure_exclusion_ledger(False, set(), cfg, RUN))
            with patch.dict(os.environ, {"ENABLE_INTAKE_EXCLUSION_LEDGER": "true"}):
                self.assertFalse(ci._configure_exclusion_ledger(True, set(), cfg, RUN))
                with patch.object(xl.requests, "get", return_value=_resp(200, [])):
                    self.assertTrue(ci._configure_exclusion_ledger(False, set(), cfg, RUN))
                self.assertTrue(xl.LEDGER.enabled)
        xl.LEDGER.reset()

    def test_configure_with_missing_table_still_runs(self):
        cfg = MagicMock(findings_supabase_url="https://x.supabase.co")
        with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "k",
                                     "ENABLE_INTAKE_EXCLUSION_LEDGER": "true"}):
            with patch.object(xl.requests, "get", return_value=_resp(404)):
                self.assertTrue(ci._configure_exclusion_ledger(False, set(), cfg, RUN))
            self.assertTrue(xl.LEDGER.load_failed)
        xl.LEDGER.reset()

    def test_summary_and_health_payload_have_per_source_excluded(self):
        stats = ci.CollectionStats()
        self.assertEqual(stats.exclusion_ledger, {})
        led = _ledger()
        led.record(source="FDA 483", document_id="a", reason_code="unrelated_domain", rule_version="vvvv")
        led.record(source="FDA 483", document_id="b", reason_code="unrelated_domain", rule_version="vvvv")
        led.record(source="MFDS", document_id="c", reason_code="relevance_pending", rule_version="vvvv")
        stats.exclusion_ledger = led.summary()
        self.assertEqual(stats.exclusion_ledger["by_source"]["FDA 483"]["excluded"], 2)
        self.assertEqual(stats.exclusion_ledger["by_source"]["MFDS"]["excluded"], 1)

    def test_existing_stats_fields_unchanged_additive_only(self):
        stats = ci.CollectionStats()
        for name in ("fr_fetched", "recall_fetched", "wl_fetched", "fda483_fetched", "mfds_fetched"):
            self.assertEqual(getattr(stats, name), 0)


if __name__ == "__main__":
    unittest.main()
