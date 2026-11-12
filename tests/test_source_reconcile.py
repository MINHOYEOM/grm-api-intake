#!/usr/bin/env python3
"""원천 대조 감시(source_reconcile.py) 표적 시험 — 네트워크는 전부 목.

① 등식 계산(수집·제외·미뤄짐·놓침)  ② pre_ledger 유예  ③ 판정 종류  ④ 미뤄짐 3일 승격
⑤ 원천 접속 실패는 놓침 0  ⑥ 이슈 계획(열기·닫기·유지)  ⑦ 수집기와 같은 문서 키(핀)
⑧ 워크플로 계약
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import source_reconcile as sr  # noqa: E402

RUN = date(2026, 12, 1)            # 유예(2026-11-22) 이후 — pre_ledger 가 꺼진 날
EARLY = date(2026, 11, 10)         # 유예 안
SPEC = sr.SPEC_BY_KEY["fda483"]
WIN = (date(2026, 11, 1), RUN)


def doc(i: str, pub: str = "2026-11-25") -> sr.SourceDoc:
    return sr.SourceDoc(document_id=i, url=f"https://x/{i}", title=f"T{i}", published=pub)


def recon(docs, *, collected=(), ledger=None, run=RUN, spec=SPEC, **kw):
    out = kw.pop("outcome", None) or sr.FetchOutcome(docs=list(docs))
    return sr.reconcile_source(
        spec, out, run_date=run, window=WIN,
        collected={f"{spec.source}::{c}" for c in collected} if kw.pop("collected_ok", True) else None,
        ledger={} if ledger is None else ledger, **kw)


def row(basis="metadata", first="2026-11-30", reason="out_of_scope_type"):
    return sr.LedgerRow(basis=basis, reason_code=reason, first_seen=first, last_seen=first)


class EquationTest(unittest.TestCase):
    def test_every_doc_lands_in_exactly_one_bucket(self):
        r = recon([doc("a"), doc("b"), doc("c"), doc("d")], collected=["a"],
                  ledger={"b": row(), "c": row("text_missing", "2026-11-30")})
        c = r.counts
        self.assertEqual((c["listed"], c["collected"], c["excluded"], c["deferred"], c["missed"]),
                         (4, 1, 1, 1, 1))
        self.assertEqual(c["collected"] + c["excluded"] + c["deferred"] + c["pre_ledger"] + c["missed"],
                         c["listed"])
        self.assertEqual([m["document_id"] for m in r.missed], ["d"])
        self.assertEqual(r.verdict, sr.V_MISSED)

    def test_missed_carries_source_url_title_date(self):
        m = recon([doc("zz", "2026-11-26")]).missed[0]
        self.assertEqual((m["document_id"], m["url"], m["title"], m["published"]),
                         ("zz", "https://x/zz", "Tzz", "2026-11-26"))

    def test_collected_key_includes_source(self):
        # 다른 소스의 같은 id 는 수집으로 치지 않는다(수집기 dedup 키 = source::document_id)
        out = sr.FetchOutcome(docs=[doc("a")])
        r = sr.reconcile_source(SPEC, out, run_date=RUN, window=WIN,
                                collected={"FDA Warning Letter::a"}, ledger={})
        self.assertEqual(r.counts["missed"], 1)

    def test_all_accounted_is_ok(self):
        r = recon([doc("a"), doc("b")], collected=["a"], ledger={"b": row()})
        self.assertEqual((r.verdict, r.counts["missed"]), (sr.V_OK, 0))


class PreLedgerTest(unittest.TestCase):
    def test_pre_ledger_published_before_activation_within_grace(self):
        r = recon([doc("old", "2026-11-05")], run=EARLY)
        self.assertEqual((r.counts["pre_ledger"], r.counts["missed"], r.verdict), (1, 0, sr.V_OK))

    def test_after_activation_is_not_pre_ledger(self):
        r = recon([doc("new", "2026-11-09")], run=date(2026, 11, 20))   # 11일 전 게시 — 유예 안 + 장부 이후
        self.assertEqual((r.counts["pre_ledger"], r.counts["missed"]), (0, 1))

    def test_grace_expires_after_two_weeks(self):
        r = recon([doc("old", "2026-11-05")], run=date(2026, 11, 22))
        self.assertEqual((r.counts["pre_ledger"], r.counts["missed"]), (0, 1))

    def test_constants(self):
        self.assertEqual(sr.LEDGER_ACTIVE_FROM, date(2026, 11, 8))
        self.assertEqual(sr.PRE_LEDGER_GRACE_DAYS, 14)

    def test_unknown_published_date_is_not_excused(self):
        r = recon([doc("nodate", "")], run=EARLY)
        self.assertEqual(r.counts["missed"], 1)


class VerdictTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(recon([doc("a")], collected=["a"]).verdict, sr.V_OK)

    def test_missed(self):
        self.assertEqual(recon([doc("a")]).verdict, sr.V_MISSED)

    def test_source_quiet_when_source_lists_nothing(self):
        r = recon([])
        self.assertEqual((r.verdict, r.counts["listed"], r.counts["missed"]), (sr.V_QUIET, 0, 0))

    def test_deferred_only(self):
        r = recon([doc("a"), doc("b")], ledger={"a": row("text_missing"), "b": row("text_missing")})
        self.assertEqual((r.verdict, r.counts["deferred"], r.counts["missed"]), (sr.V_DEFERRED_ONLY, 2, 0))

    def test_some_deferred_among_collected_is_ok(self):
        r = recon([doc("a"), doc("b")], collected=["a"], ledger={"b": row("text_missing")})
        self.assertEqual(r.verdict, sr.V_OK)

    def test_disabled_source(self):
        r = recon([], outcome=sr.FetchOutcome(enabled=False))
        self.assertEqual(r.verdict, sr.V_DISABLED)

    def test_store_unreachable_never_counts_missed(self):
        r = recon([doc("a")], collected_ok=False, store_error="Notion 조회 실패")
        self.assertEqual((r.verdict, r.counts["missed"]), (sr.V_STORE_UNREACHABLE, 0))
        self.assertIn("Notion", r.error)

    def test_ledger_unreachable_never_counts_missed(self):
        out = sr.FetchOutcome(docs=[doc("a")])
        r = sr.reconcile_source(SPEC, out, run_date=RUN, window=WIN, collected=set(),
                                ledger=None, ledger_error="제외 장부 조회 HTTP 500")
        self.assertEqual((r.verdict, r.counts["missed"]), (sr.V_STORE_UNREACHABLE, 0))


class RecentNotYetAttemptedTest(unittest.TestCase):
    def test_within_three_days_is_deferred_not_missed(self):
        for pub in ("2026-12-01", "2026-11-30", "2026-11-28"):     # 0·1·3일 전
            r = recon([doc("a", pub)])
            self.assertEqual((r.counts["deferred"], r.counts["deferred_recent"], r.counts["missed"]), (1, 1, 0), pub)
            self.assertEqual(r.deferred[0]["reason"], "recent_not_yet_attempted")
            self.assertEqual(r.verdict, sr.V_DEFERRED_ONLY)

    def test_over_three_days_is_missed(self):
        r = recon([doc("a", "2026-11-27")])      # 4일 전
        self.assertEqual((r.counts["deferred"], r.counts["missed"], r.verdict), (0, 1, sr.V_MISSED))

    def test_no_published_date_is_missed(self):
        r = recon([doc("a", "")])
        self.assertEqual((r.counts["deferred"], r.counts["missed"]), (0, 1))

    def test_collected_or_excluded_recent_doc_is_not_deferred(self):
        r = recon([doc("a", "2026-12-01"), doc("b", "2026-12-01")], collected=["a"], ledger={"b": row()})
        self.assertEqual((r.counts["deferred_recent"], r.counts["collected"], r.counts["excluded"]), (0, 1, 1))

    def test_visible_in_json_and_markdown_and_no_issue_opened(self):
        r = recon([doc("a", "2026-12-01"), doc("b", "2026-12-01")], collected=["b"])
        rep = sr.build_report(run_date=RUN, specs=[SPEC], outcomes={"fda483": sr.FetchOutcome(docs=[doc("a", "2026-12-01")])},
                              windows={"fda483": WIN}, collected=set(), store_error="",
                              ledger={"FDA 483": {}}, ledger_error="")
        self.assertEqual(rep["sources"][0]["counts"]["deferred_recent"], 1)
        self.assertIn("recent_not_yet_attempted", sr.render_markdown(rep))
        self.assertEqual(sr.plan_issues([r])[0]["action"], "close")


class DeferredPromotionTest(unittest.TestCase):
    def test_three_days_is_still_deferred(self):
        r = recon([doc("a")], ledger={"a": row("text_missing", "2026-11-28")})   # 12-01 - 11-28 = 3
        self.assertEqual((r.counts["deferred"], r.counts["missed"]), (1, 0))

    def test_over_three_days_promotes_to_missed(self):
        r = recon([doc("a")], ledger={"a": row("text_missing", "2026-11-27", "unrelated_domain")})   # 4일
        self.assertEqual((r.counts["deferred"], r.counts["missed"], r.counts["deferred_overdue"]), (0, 1, 1))
        self.assertEqual(r.verdict, sr.V_MISSED)
        self.assertEqual(r.missed[0]["reason"], "deferred_overdue")
        self.assertIn("4일", r.missed[0]["detail"])

    def test_text_basis_exclusion_never_ages_into_missed(self):
        r = recon([doc("a")], ledger={"a": row("text", "2026-10-01")})
        self.assertEqual((r.counts["excluded"], r.counts["missed"]), (1, 0))


class UnreachableTest(unittest.TestCase):
    def test_source_failure_yields_zero_missed(self):
        r = recon([], outcome=sr.FetchOutcome(error="ConnectionError: tls"))
        self.assertEqual((r.verdict, r.counts["missed"], r.error), (sr.V_UNREACHABLE, 0, "ConnectionError: tls"))

    def test_fetch_all_converts_exceptions_and_isolates_sources(self):
        def boom(s, e):
            raise RuntimeError("x api_key=SECRET123 y")

        def good(s, e):
            return sr.FetchOutcome(docs=[doc("a")])

        specs = [sr.SPEC_BY_KEY["wl"], sr.SPEC_BY_KEY["fr"]]
        wins = sr.compute_windows(specs, RUN, 7, 30)
        out = sr.fetch_all(specs, wins, {"wl": boom, "fr": good}, env={})
        self.assertIn("RuntimeError", out["wl"].error)
        self.assertNotIn("SECRET123", out["wl"].error)
        self.assertEqual(len(out["fr"].docs), 1)

    def test_fetch_error_class_message_kept(self):
        def bad(s, e):
            raise sr.SourceFetchError("TLS 실패")
        wins = sr.compute_windows([SPEC], RUN, 7, 30)
        self.assertEqual(sr.fetch_all([SPEC], wins, {"fda483": bad}, env={})["fda483"].error, "TLS 실패")

    def test_disabled_flag_skips_fetch(self):
        called = []
        wins = sr.compute_windows([SPEC], RUN, 7, 30)
        out = sr.fetch_all([SPEC], wins, {"fda483": lambda s, e: called.append(1)},
                           env={"ENABLE_FDA_483": "false"})
        self.assertFalse(out["fda483"].enabled)
        self.assertEqual(called, [])


class WindowTest(unittest.TestCase):
    def test_windows_follow_collector(self):
        w = sr.compute_windows(sr.SPECS, RUN, 7, 30)
        self.assertEqual(w["wl"], (date(2026, 11, 24), RUN))
        self.assertEqual(w["fr"], (date(2026, 11, 24), RUN))
        self.assertEqual(w["mfds_rss"], (date(2026, 11, 24), RUN))
        self.assertEqual(w["fda483"], (date(2026, 11, 1), RUN))
        self.assertEqual(w["recall"], (date(2026, 11, 1), RUN))


class IssuePlanTest(unittest.TestCase):
    def _res(self, verdict_docs, **kw):
        return recon(verdict_docs, **kw)

    def test_open_for_missed_with_labels_title_fingerprint(self):
        plan = sr.plan_issues([self._res([doc("a"), doc("b")], collected=["a"])], run_url="https://run/1")[0]
        self.assertEqual(plan["action"], "open")
        self.assertEqual(plan["title"], "[source-reconcile] FDA 483 원천 대비 수집 누락이 있습니다")
        self.assertEqual(plan["labels"], ["reconciliation", "reconcile-fda-483"])
        for needle in ("`b`", "https://x/b", "Tb", "2026-11-25", plan["fingerprint"], "https://run/1"):
            self.assertIn(needle, plan["body"])

    def test_same_title_every_day_so_issue_is_reused(self):
        a = sr.plan_issues([self._res([doc("a")])])[0]
        b = sr.plan_issues([self._res([doc("a"), doc("b")])])[0]
        self.assertEqual(a["title"], b["title"])
        self.assertNotEqual(a["fingerprint"], b["fingerprint"])   # 집합이 바뀌면 댓글을 새로 단다

    def test_close_when_back_to_normal(self):
        for docs, kw in (([doc("a")], {"collected": ["a"]}), ([], {})):
            plan = sr.plan_issues([self._res(docs, **kw)])[0]
            self.assertEqual(plan["action"], "close")
            self.assertIn("자동으로 닫", plan["comment"])

    def test_close_when_deferred_only_or_disabled(self):
        r1 = self._res([doc("a")], ledger={"a": row("text_missing")})
        r2 = recon([], outcome=sr.FetchOutcome(enabled=False))
        self.assertEqual([p["action"] for p in sr.plan_issues([r1, r2])], ["close", "close"])

    def test_keep_when_verdict_could_not_be_made(self):
        r1 = recon([], outcome=sr.FetchOutcome(error="down"))
        r2 = self._res([doc("a")], collected_ok=False, store_error="x")
        self.assertEqual([p["action"] for p in sr.plan_issues([r1, r2])], ["keep", "keep"])

    def test_labels_name_the_source_for_every_spec(self):
        slugs = {s.slug for s in sr.SPECS}
        self.assertEqual(len(slugs), len(sr.SPECS))
        for s in sr.SPECS:
            plan = sr.plan_issues([recon([doc("a")], spec=s)])[0]
            self.assertEqual(plan["labels"][-1], "reconcile-" + s.slug)

    def test_long_missed_list_is_truncated_in_body(self):
        r = self._res([doc(f"d{i}") for i in range(sr.MAX_LISTED_IN_ISSUE + 5)])
        body = sr.plan_issues([r])[0]["body"]
        self.assertIn("외 5건", body)


class ReportTest(unittest.TestCase):
    def test_build_report_roundtrips_json_and_totals(self):
        specs = [sr.SPEC_BY_KEY["wl"], sr.SPEC_BY_KEY["fr"]]
        wins = sr.compute_windows(specs, RUN, 7, 30)
        outs = {"wl": sr.FetchOutcome(docs=[doc("a"), doc("b")]), "fr": sr.FetchOutcome(error="down")}
        rep = sr.build_report(run_date=RUN, specs=specs, outcomes=outs, windows=wins,
                              collected={"FDA Warning Letter::a"}, store_error="",
                              ledger={"FDA Warning Letter": {}, "Federal Register": {}}, ledger_error="")
        rep = json.loads(json.dumps(rep, ensure_ascii=False))
        self.assertEqual(rep["totals"]["missed"], 1)
        self.assertEqual(rep["totals"]["verdicts"], {"wl": "missed", "fr": "source_unreachable"})
        md = sr.render_markdown(rep)
        self.assertIn("원천 접속 불가", md)
        self.assertIn("`b`", md)

    def test_run_without_credentials_reports_store_unreachable_not_missed(self):
        rep = sr.run(run_date=RUN, base_days=7, enf_days=30, specs=[sr.SPEC_BY_KEY["wl"]],
                     fetchers={"wl": lambda s, e: sr.FetchOutcome(docs=[doc("a")])}, env={})
        self.assertEqual(rep["sources"][0]["verdict"], sr.V_STORE_UNREACHABLE)
        self.assertEqual(rep["totals"]["missed"], 0)


class SupabaseShadowTest(unittest.TestCase):
    """'수집됨'을 정본(Supabase) 기준으로 다시 판정해 차이만 기록한다 — 판정은 Notion 기준 그대로."""

    ENV = {"NOTION_TOKEN": "t", "NOTION_DATABASE_ID": "d", "SUPABASE_URL": "https://x.supabase.co",
           "SUPABASE_SERVICE_ROLE_KEY": "KEY"}

    def _run(self, *, notion_ids, db_ids, flag="true", db_error=None):
        from unittest.mock import patch
        spec = sr.SPEC_BY_KEY["wl"]
        env = dict(self.ENV, ENABLE_RECONCILE_SUPABASE_SHADOW=flag)

        def db_keys(*_a, **_k):
            if db_error:
                raise db_error
            return {f"{spec.source}::{i}" for i in db_ids}, set()
        with patch.object(sr, "load_collected_keys",
                          return_value=({f"{spec.source}::{i}" for i in notion_ids}, set())), \
                patch.object(sr, "load_ledger", return_value={spec.source: {}}), \
                patch.object(sr, "load_collected_keys_supabase", side_effect=db_keys) as db_call:
            rep = sr.run(run_date=RUN, base_days=7, enf_days=30, specs=[spec],
                         fetchers={"wl": lambda s, e: sr.FetchOutcome(docs=[doc("a"), doc("b")])},
                         env=env)
        return rep, db_call

    def test_off_by_default_and_never_reads_the_database(self):
        rep, db_call = self._run(notion_ids=["a", "b"], db_ids=["a", "b"], flag="")
        self.assertNotIn("shadow_supabase", rep)
        db_call.assert_not_called()

    def test_same_verdict_is_reported_as_same(self):
        rep, _ = self._run(notion_ids=["a", "b"], db_ids=["a", "b", "old"])
        sh = rep["shadow_supabase"]
        self.assertEqual((sh["ok"], sh["same"], sh["differences"]), (True, True, []))
        self.assertEqual(sh["collected_keys"], 3)
        self.assertIn("차이 없음", sr.render_markdown(rep))

    def test_difference_is_recorded_and_the_verdict_stays_notion(self):
        rep, _ = self._run(notion_ids=["a"], db_ids=["a", "b"])       # b: 노션 창 밖, 정본에는 있음
        self.assertEqual(rep["sources"][0]["verdict"], sr.V_MISSED)    # 판정은 Notion 기준
        self.assertEqual(rep["totals"]["missed"], 1)
        self.assertEqual(len(rep["issues"]), 1)                        # 이슈 계획도 그대로
        sh = rep["shadow_supabase"]
        self.assertFalse(sh["same"])
        d = sh["differences"][0]
        self.assertEqual((d["verdict_notion"], d["verdict_supabase"]), (sr.V_MISSED, sr.V_OK))
        self.assertEqual((d["missed_only_notion"], d["missed_only_supabase"]), (["b"], []))
        self.assertEqual((sh["missed_total_notion"], sh["missed_total_supabase"]), (1, 0))
        md = sr.render_markdown(rep)
        self.assertIn("그림자 — 정본(Supabase) 기준", md)
        self.assertIn("| 1 / 0 |", md)

    def test_database_failure_does_not_touch_the_report(self):
        base, _ = self._run(notion_ids=["a"], db_ids=[], flag="")
        rep, _ = self._run(notion_ids=["a"], db_ids=[],
                           db_error=sr.StoreReadError("정본(raw_signals) 조회 HTTP 500"))
        sh = rep.pop("shadow_supabase")
        self.assertEqual((sh["ok"], sh["error"]), (False, "정본(raw_signals) 조회 HTTP 500"))
        for volatile in ("generated_at",):
            rep.pop(volatile), base.pop(volatile)
        self.assertEqual(rep, base)
        rep2, _ = self._run(notion_ids=["a"], db_ids=[], db_error=RuntimeError("boom KEY"))
        self.assertNotIn("KEY", json.dumps(rep2["shadow_supabase"], ensure_ascii=False))

    def test_loader_reads_all_rows_of_each_source_without_leaking_key(self):
        http = MagicMock()

        def get(url, params, headers, timeout):
            r = MagicMock()
            r.status_code = 200
            first = params["offset"] == "0"
            r.json.return_value = ([{"document_id": f"d{i}", "official_url": "https://EX.org/p/#x"}
                                    for i in range(1000)] if first
                                   else [{"document_id": "last", "official_url": ""}])
            return r
        http.get.side_effect = get
        ids, urls = sr.load_collected_keys_supabase("https://x.supabase.co", "KEY",
                                                    ["FDA Warning Letter"], session=http)
        self.assertEqual(len(ids), 1001)
        self.assertIn("FDA Warning Letter::last", ids)
        self.assertEqual(http.get.call_count, 2)
        self.assertEqual(http.get.call_args.kwargs["params"]["source"], "eq.FDA Warning Letter")
        from grm_notion import canonical_url_key
        self.assertEqual(urls, {canonical_url_key("FDA Warning Letter", "https://EX.org/p/#x")})
        bad = MagicMock()
        bad.get.return_value = MagicMock(status_code=401)
        with self.assertRaises(sr.StoreReadError) as cm:
            sr.load_collected_keys_supabase("https://x.supabase.co", "SECRETKEY", ["X"], session=bad)
        self.assertNotIn("SECRETKEY", str(cm.exception))

    def test_workflow_passes_the_switch_default_off(self):
        wf = (ROOT / ".github" / "workflows" / "grm-source-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn("ENABLE_RECONCILE_SUPABASE_SHADOW: ${{ vars.ENABLE_RECONCILE_SUPABASE_SHADOW || 'false' }}",
                      wf)


class LedgerLoadTest(unittest.TestCase):
    def _resp(self, status=200, body=None):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body if body is not None else []
        return r

    def test_reads_rows_by_source_without_leaking_key(self):
        http = MagicMock()
        http.get.return_value = self._resp(200, [
            {"document_id": "fda483-1", "basis": "text_missing", "reason_code": "r",
             "first_seen": "2026-11-08", "last_seen": "2026-11-09"}])
        out = sr.load_ledger("https://x.supabase.co", "KEY", ["FDA 483"], session=http)
        self.assertEqual(out["FDA 483"]["fda483-1"].basis, "text_missing")
        self.assertEqual(http.get.call_args.kwargs["params"]["source"], "eq.FDA 483")

    def test_http_error_is_store_error_without_secret(self):
        http = MagicMock()
        http.get.return_value = self._resp(401)
        with self.assertRaises(sr.StoreReadError) as cm:
            sr.load_ledger("https://x.supabase.co", "SECRETKEY", ["FDA 483"], session=http)
        self.assertNotIn("SECRETKEY", str(cm.exception))

    def test_network_error_is_store_error(self):
        import requests
        http = MagicMock()
        http.get.side_effect = requests.exceptions.ConnectionError("boom SECRETKEY")
        with self.assertRaises(sr.StoreReadError) as cm:
            sr.load_ledger("https://x.supabase.co", "SECRETKEY", ["FDA 483"], session=http)
        self.assertNotIn("SECRETKEY", str(cm.exception))


class KeyPinTest(unittest.TestCase):
    """감시가 쓰는 문서 키 식이 수집기 것과 같다 — 갈라지면 모든 문서가 놓침으로 보인다."""

    def test_noid_matches_insert_items(self):
        want = "noid-" + hashlib.sha1("u|h|2026-01-01".encode("utf-8")).hexdigest()[:16]
        self.assertEqual(sr.noid_document_id("u", "h", "2026-01-01"), want)
        src = (ROOT / "collect_intake.py").read_text(encoding="utf-8")
        self.assertIn('f"{item.official_url}|{item.headline}|{item.date_iso}"', src)
        self.assertIn('"noid-" + hashlib.sha1(', src)

    def test_wl_doc_id_matches_build_wl_item(self):
        import collect_intake as ci
        cols = ["11/03/2026", "10/28/2026", "Acme Pharma Inc.|HREF:/inspections/wl/acme-1",
                "Center for Drug Evaluation and Research", "CGMP/Finished Pharmaceuticals"]
        docs = sr.wl_docs_from_rows([{"_cols": cols}], date(2026, 11, 1), date(2026, 11, 8))
        self.assertEqual(len(docs), 1)
        item = ci.build_wl_item(
            firm="Acme Pharma Inc.", posted_raw="11/03/2026", letter_date_raw="10/28/2026",
            issuing_office="Center for Drug Evaluation and Research",
            subject="CGMP/Finished Pharmaceuticals", wl_href="https://www.fda.gov/inspections/wl/acme-1",
            office_verdict="keep", excerpt=None, full=None)
        self.assertEqual(docs[0].document_id, item.document_id)
        self.assertEqual(docs[0].published, "2026-11-03")

    def test_wl_rows_outside_window_and_headers_skipped(self):
        cols_old = ["01/03/2026", "", "Old Co|HREF:/x", "Office", "Subj"]
        hdr = ["Posted Date", "Letter Issue Date", "Company", "Office", "Subject"]
        self.assertEqual(sr.wl_docs_from_rows([{"_cols": cols_old}, {"_cols": hdr}],
                                              date(2026, 11, 1), date(2026, 11, 8)), [])

    def test_mfds_doc_id_function_is_collectors(self):
        import collect_mfds as m
        self.assertEqual(m._doc_id("data0008", "https://www.mfds.go.kr/brd/m_1/view.do?seq=123", "t", "2026-11-03"),
                         "data0008-123")
        src = (ROOT / "source_reconcile.py").read_text(encoding="utf-8")
        self.assertIn("m._doc_id(brd_id, link, title, date_iso)", src)

    def test_fr_and_recall_use_collector_item_builders(self):
        import collect_intake as ci
        it = ci._fr_to_item({"document_number": "2026-99999", "title": "T", "publication_date": "2026-11-03",
                             "html_url": "https://fr/x", "type": "Notice"}, "q")
        self.assertEqual(it.document_id, "2026-99999")
        rc = ci._recall_to_item({"recall_number": "D-0001-2026", "product_description": "P",
                                 "report_date": "20261103"}, "q")
        self.assertEqual((rc.document_id, rc.date_iso), ("D-0001-2026", "2026-11-03"))

    def test_spec_sources_match_collector_source_names(self):
        import grm_common as g
        want = {"fda483": g.SOURCE_FDA_483, "wl": g.SOURCE_FDA_WL, "recall": g.SOURCE_RECALL,
                "mfds_rss": g.SOURCE_MFDS, "fr": g.SOURCE_FR}
        # 첫 5개 소스(PR #1371). 확장분은 test_source_reconcile_expand 가 잠근다.
        self.assertEqual({k: sr.SPEC_BY_KEY[k].source for k in want}, want)


class WorkflowContractTest(unittest.TestCase):
    WF = (ROOT / ".github" / "workflows" / "grm-source-reconcile.yml").read_text(encoding="utf-8")

    def test_runs_after_intake_via_workflow_run_and_manually(self):
        self.assertIn('workflows: ["GRM API Intake (Daily)"]', self.WF)
        self.assertIn("types: [completed]", self.WF)
        self.assertIn("workflow_dispatch:", self.WF)
        self.assertIn("window_days:", self.WF)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.WF)
        intake = (ROOT / ".github" / "workflows" / "grm-intake.yml").read_text(encoding="utf-8")
        self.assertIn("name: GRM API Intake (Daily)", intake)

    def test_least_privilege_and_no_paid_service(self):
        self.assertIn("contents: read", self.WF)
        self.assertIn("issues: write", self.WF)
        self.assertNotIn("ANTHROPIC", self.WF)

    def test_issue_steps_follow_plan_and_keep_unverified_open(self):
        self.assertIn("plan.action === 'keep'", self.WF)
        self.assertIn("plan.action === 'close'", self.WF)
        self.assertIn("state: 'closed'", self.WF)

    def test_dispatch_inputs_not_interpolated_into_shell(self):
        run_block = self.WF.split("run: |", 2)[2]
        self.assertNotIn("${{ inputs.", run_block)


if __name__ == "__main__":
    unittest.main()
