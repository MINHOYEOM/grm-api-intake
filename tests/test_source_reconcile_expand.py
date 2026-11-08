#!/usr/bin/env python3
"""원천 대조 감시 2026-11 확장(유럽·국제·식약처 API·HC 회수) 표적 시험 — 네트워크는 전부 목.

① 소스 명세(이름·창·플래그·이슈 제목)  ② 수집기와 같은 문서 키(핀)  ③ 수집기가 버린 문서가 제외 장부에
같은 키로 남는다  ④ 스냅샷 소스는 3년 창으로 따로 읽는다  ⑤ URL 2차 dedup 소스  ⑥ 소스별 장부 시작일
⑦ 워크플로 계약
"""
from __future__ import annotations

import os
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import collect_intake as ci  # noqa: E402
import intake_exclusion as xl  # noqa: E402
import source_reconcile as sr  # noqa: E402

RUN = date(2026, 12, 1)
START, END = date(2026, 11, 1), date(2026, 11, 8)

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry><title>Class 2 Medicines Recall: Acme Ltd, Drug X</title>
  <link rel="alternate" href="https://www.gov.uk/drug-device-alerts/acme-drug-x"/>
  <updated>2026-11-03T10:00:00Z</updated><summary>recall</summary></entry>
 <entry><title>Field Safety Notice: Device Co infusion pump</title>
  <link rel="alternate" href="https://www.gov.uk/drug-device-alerts/fsn-pump"/>
  <updated>2026-11-04T10:00:00Z</updated><summary>device</summary></entry>
 <entry><title>Old alert</title>
  <link rel="alternate" href="https://www.gov.uk/drug-device-alerts/old"/>
  <updated>2026-01-04T10:00:00Z</updated><summary>old</summary></entry>
</feed>"""

WHO_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
 <item><title>Medicines prequalified: new FPP</title><link>https://extranet.who.int/prequal/news/a</link>
  <pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;medicines&lt;/p&gt;</description></item>
 <item><title>Vector control product list updated</title><link>https://extranet.who.int/prequal/news/b</link>
  <pubDate>Wed, 04 Nov 2026 10:00:00 GMT</pubDate><description>vector control</description></item>
</channel></rss>"""


class _LedgerOn:
    """제외 장부를 메모리로만 켠다(flush 하지 않으므로 네트워크 없음)."""

    def __enter__(self):
        xl.LEDGER.reset()
        assert xl.LEDGER.configure("https://x.supabase.co", "k", RUN)
        return xl.LEDGER

    def __exit__(self, *exc):
        xl.LEDGER.reset()


def pending(source: str) -> dict[str, dict]:
    return {d: row for (s, d), row in xl.LEDGER._pending.items() if s == source}


class SpecTest(unittest.TestCase):
    def test_sources_match_collector_source_names(self):
        import grm_common as g
        want = {
            "fda483": g.SOURCE_FDA_483, "wl": g.SOURCE_FDA_WL, "recall": g.SOURCE_RECALL,
            "mfds_rss": g.SOURCE_MFDS, "fr": g.SOURCE_FR, "ema": g.SOURCE_EMA,
            "mhra_blog": g.SOURCE_MHRA, "mhra_alert": g.SOURCE_MHRA, "mhra_ncr": g.SOURCE_MHRA_GMP_NCR,
            "eu_ncr": g.SOURCE_EU_GMP_NCR, "pics": g.SOURCE_PICS, "eca": g.SOURCE_ECA,
            "ispe": g.SOURCE_ISPE, "ich": g.SOURCE_ICH, "who_rss": g.SOURCE_WHO, "who_insp": g.SOURCE_WHO,
            "hc_recall": g.SOURCE_HC, "mfds_recall": g.SOURCE_MFDS, "mfds_admin": g.SOURCE_MFDS,
        }
        self.assertEqual({s.key: s.source for s in sr.SPECS}, want)

    def test_every_spec_has_a_fetcher_and_distinct_slug_and_issue_title(self):
        self.assertEqual(set(sr.FETCHERS), set(sr.SPEC_BY_KEY))
        self.assertEqual(len({s.slug for s in sr.SPECS}), len(sr.SPECS))
        titles = [p["title"] for p in sr.plan_issues(
            sr.reconcile_source(s, sr.FetchOutcome(docs=[]), run_date=RUN, window=(START, END),
                                collected=set(), ledger={}) for s in sr.SPECS)]
        self.assertEqual(len(set(titles)), len(titles), "같은 source 를 나눠 쓰는 소스의 이슈 제목이 겹친다")

    def test_existing_issue_titles_unchanged(self):
        # 이미 열려 있을 수 있는 5개 소스 이슈는 같은 제목으로 재사용돼야 한다.
        for key in ("fda483", "wl", "recall", "mfds_rss", "fr"):
            self.assertEqual(sr.SPEC_BY_KEY[key].issue_source, sr.SPEC_BY_KEY[key].source)

    def test_hc_inspection_not_a_target(self):
        self.assertNotIn("Health Canada Inspection", {s.source for s in sr.SPECS})
        self.assertIn("이전 중", sr.fetch_hc_recall.__doc__ or "")

    def test_windows_follow_collector(self):
        w = sr.compute_windows(sr.SPECS, RUN, 7, 30)
        for k in ("ema", "mhra_blog", "mhra_alert", "pics", "eca", "who_rss"):
            self.assertEqual(w[k], (date(2026, 11, 24), RUN), k)          # collect_intake start
        for k in ("ispe", "mhra_ncr", "eu_ncr", "hc_recall", "mfds_recall", "mfds_admin"):
            self.assertEqual(w[k], (date(2026, 11, 1), RUN), k)           # enf_start
        for k in ("ich", "who_insp"):
            self.assertEqual(w[k], (RUN - __import__("datetime").timedelta(days=1095), RUN), k)

    def test_snapshot_window_matches_collector_dedup(self):
        src = (ROOT / "collect_intake.py").read_text(encoding="utf-8")
        self.assertIn(f"_SNAPSHOT_DEDUP_WINDOW_DAYS = {sr.SNAPSHOT_DEDUP_DAYS}", src)

    def test_flags_match_intake_workflow_defaults(self):
        intake = (ROOT / ".github" / "workflows" / "grm-intake.yml").read_text(encoding="utf-8")
        for s in sr.SPECS:
            if not s.flag:
                continue
            default = "'true'" if s.flag_default else "'false'"
            line = next(ln for ln in intake.splitlines() if ln.strip().startswith(f"{s.flag}:"))
            self.assertTrue(line.rstrip().endswith(f"|| {default} }}}}"), (s.flag, line))

    def test_data_go_kr_key_missing_is_unreachable_not_missed(self):
        with patch.dict(os.environ, {"DATA_GO_KR_SERVICE_KEY": ""}):
            out = sr.fetch_all([sr.SPEC_BY_KEY["mfds_admin"]],
                               sr.compute_windows([sr.SPEC_BY_KEY["mfds_admin"]], RUN, 7, 30), env={})
        res = sr.reconcile_source(sr.SPEC_BY_KEY["mfds_admin"], out["mfds_admin"], run_date=RUN,
                                  window=(START, END), collected=set(), ledger={})
        self.assertEqual(res.verdict, sr.V_UNREACHABLE)
        self.assertEqual(res.counts["missed"], 0)


class RssKeyAndLedgerTest(unittest.TestCase):
    """RSS 계열: 감시의 문서 키 = collect_rss_feed 의 키, keep_item 이 버린 항목 = 같은 키의 장부 행."""

    def test_mhra_alert_keys_and_device_fsn_goes_to_ledger(self):
        spec = ci._MHRA_ALERT_FEED_SPEC
        root = ET.fromstring(ATOM)
        docs, undated = sr.rss_docs_from_root(spec, root, "mhra_alert", ci.MHRA_ALERT_RSS_URL, START, END)
        self.assertEqual(undated, 0)
        self.assertEqual(len(docs), 2)                 # 창 안 2건, keep_item 은 적용하지 않는다
        with _LedgerOn(), patch.object(ci, "http_get_xml", return_value=root):
            items, err = ci.collect_rss_feed(spec, START, END)
            led = pending(ci.SOURCE_MHRA)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        ids = {d.document_id for d in docs}
        self.assertEqual({items[0].document_id} | set(led), ids)   # 수집 + 장부 = 원천
        self.assertEqual(next(iter(led.values()))["reason_code"], "not_medicines_alert")

    def test_feed_without_keep_item_records_nothing(self):
        spec = ci._MHRA_FEED_SPEC
        with _LedgerOn(), patch.object(ci, "http_get_xml", return_value=ET.fromstring(ATOM)):
            ci.collect_rss_feed(spec, START, END)
            self.assertEqual(pending(ci.SOURCE_MHRA), {})

    def test_rule_version_tracks_keep_item_vocabulary(self):
        v = ci._rss_exclusion_rule_version(ci._is_mhra_medicines_alert)
        names = {n for n, _ in xl.rule_closure(("_is_mhra_medicines_alert",), ("collect_intake", "grm_taxonomy"))}
        self.assertIn("_MHRA_ALERT_KEEP_RE", names)
        self.assertRegex(v, r"^[0-9a-f]{12}$")
        self.assertNotEqual(v, ci._rss_exclusion_rule_version(ci._is_ispe_gmp_relevant))

    def test_partial_and_total_feed_failure(self):
        spec = ci._EMA_FEED_SPEC
        calls = {"n": 0}

        def flaky(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("HTTP 503")
            return ET.fromstring("<rss><channel></channel></rss>")
        with patch.object(ci, "http_get_xml", side_effect=flaky):
            out = sr.fetch_rss_spec(spec, START, END)
        self.assertTrue(out.partial)
        with patch.object(ci, "http_get_xml", side_effect=RuntimeError("down")):
            with self.assertRaises(sr.SourceFetchError):
                sr.fetch_rss_spec(spec, START, END)

    def test_eca_ispe_dedup_by_url_like_collector(self):
        self.assertEqual({s.source for s in sr.SPECS if s.url_dedup}, set(ci._CANONICAL_URL_DEDUP_SOURCES))
        spec = sr.SPEC_BY_KEY["eca"]
        d = sr.SourceDoc(document_id="new-id", url="https://www.gmp-compliance.org/a/", title="t",
                         published="2026-11-20")
        from grm_notion import canonical_url_key
        res = sr.reconcile_source(spec, sr.FetchOutcome(docs=[d]), run_date=RUN, window=(START, RUN),
                                  collected=set(), ledger={},
                                  collected_urls={canonical_url_key(spec.source, "https://www.gmp-compliance.org/a")})
        self.assertEqual((res.counts["collected"], res.counts["missed"]), (1, 0))
        # URL dedup 이 아닌 소스는 URL 이 같아도 키가 다르면 놓침
        pics = sr.SPEC_BY_KEY["pics"]
        res2 = sr.reconcile_source(pics, sr.FetchOutcome(docs=[d]), run_date=RUN, window=(START, RUN),
                                   collected=set(), ledger={},
                                   collected_urls={canonical_url_key(pics.source, d.url)})
        self.assertEqual(res2.counts["missed"], 1)


class HcTest(unittest.TestCase):
    REC_IN = {"NID": "80001", "Title": "Drug X recall", "URL": "/en/alert-recall/drug-x",
              "Organization": "Drugs and health products", "Category": "Drugs",
              "Last updated": "2026-11-03"}
    REC_FOOD = {"NID": "80002", "Title": "Cookies recall", "URL": "/en/alert-recall/cookies",
                "Organization": "Canadian Food Inspection Agency", "Category": "Food",
                "Last updated": "2026-11-04"}
    REC_VET = {"NID": "80003", "Title": "Vet drug", "URL": "/en/alert-recall/vet",
               "Organization": "Drugs and health products", "Category": "Veterinary drugs",
               "Last updated": "2026-11-05"}
    REC_OLD = {"NID": "70000", "Title": "Old", "Organization": "Canadian Food Inspection Agency",
               "Category": "Food", "Last updated": "2026-01-01"}

    def test_collected_plus_ledger_equals_source_list(self):
        import collect_hc as hc
        data = [self.REC_IN, self.REC_FOOD, self.REC_VET, self.REC_OLD, {"Title": "no nid",
                "Last updated": "2026-11-05"}]
        docs, no_nid = sr.hc_docs_from_records(data, START, END)
        self.assertEqual(no_nid, 1)
        self.assertEqual({d.document_id for d in docs}, {"hc-80001", "hc-80002", "hc-80003"})
        with _LedgerOn(), patch.object(hc, "http_get_json", return_value=data), \
                patch.object(hc, "_fetch_recall_detail", return_value={}):
            items, err = hc.collect_hc(START, END)
            led = pending(hc.SOURCE_HC)
        self.assertIsNone(err)
        self.assertEqual({i.document_id for i in items}, {"hc-80001"})
        self.assertEqual({k: v["reason_code"] for k, v in led.items()},
                         {"hc-80002": "out_of_scope_org", "hc-80003": "excluded_category"})
        self.assertNotIn("hc-70000", led)      # 창 밖은 장부에 쓰지 않는다

    def test_correction_of_earlier_notice_goes_to_ledger(self):
        import collect_hc as hc
        with _LedgerOn():
            item = hc._to_item(self.REC_IN, START, END,
                               detail_fetcher=lambda u: {hc._FIRST_PUBLISHED_KEY: "2026-09-01"})
            led = pending(hc.SOURCE_HC)
        self.assertIsNone(item)
        self.assertEqual(led["hc-80001"]["reason_code"], "correction_of_earlier_notice")
        self.assertEqual(led["hc-80001"]["basis"], xl.BASIS_TEXT)


class WhoTest(unittest.TestCase):
    def test_rss_keys_match_and_excluded_program_goes_to_ledger(self):
        import collect_who as who
        root = ET.fromstring(WHO_RSS)
        docs, undated = sr.who_rss_docs_from_root(root, START, END)
        self.assertEqual((len(docs), undated), (2, 0))
        with _LedgerOn(), patch.object(who, "http_get_xml", return_value=root):
            items, err = who._collect_rss(START, END)
            led = pending(who.SOURCE_WHO)
        self.assertIsNone(err)
        self.assertEqual({i.document_id for i in items} | set(led), {d.document_id for d in docs})
        self.assertEqual({v["reason_code"] for v in led.values()}, {"out_of_scope_program"})

    def test_inspections_use_collector_list_functions(self):
        import collect_who as who
        it = ci.IntakeItem(source="WHO", document_id="who-whopir-abc", date_iso="2025-03-01",
                           headline="h", official_url="https://x/a.pdf")
        with patch.object(who, "_collect_whopir", return_value=([it], None)), \
                patch.object(who, "_collect_noc", return_value=([], "WHO NOC 수집 실패: x")):
            out = sr.fetch_who_inspections(START, END)
        self.assertEqual([d.document_id for d in out.docs], ["who-whopir-abc"])
        self.assertTrue(out.partial)
        with patch.object(who, "_collect_whopir", return_value=([], "a")), \
                patch.object(who, "_collect_noc", return_value=([], "b")):
            with self.assertRaises(sr.SourceFetchError):
                sr.fetch_who_inspections(START, END)


class IchTest(unittest.TestCase):
    HTML = "<html><h3>Q1A(R2) Stability Testing of New Drug Substances</h3><p>Login</p>" \
           "<h3>Q9(R1) Quality Risk Management</h3></html>"

    def test_snapshot_keys_equal_collector_items(self):
        import collect_ich as ich
        with patch.object(ich, "_get_html", return_value=self.HTML), \
                patch.object(ich.time, "sleep", return_value=None):
            out = sr.fetch_ich(START, END)
            items, _err = ich.collect_ich(RUN)
        self.assertEqual({d.document_id for d in out.docs}, {i.document_id for i in items})
        self.assertTrue(out.docs)
        self.assertTrue(all(d.published == "" for d in out.docs))


class MfdsAdminTest(unittest.TestCase):
    def test_not_collectable_and_missing_field_go_to_ledger_with_collector_key(self):
        import collect_mfds_admin_action as m
        cosmetic = {"ADM_DISPS_SEQ": "991", "ENTP_NAME": "화장품사", "ITEM_NAME": "화장품 크림",
                    "ADM_DISPS_NAME": "광고업무정지", "LAST_SETTLE_DATE": "20261103"}
        no_subject = {"ADM_DISPS_SEQ": "992", "ENTP_NAME": "회사", "LAST_SETTLE_DATE": "20261103"}
        with _LedgerOn():
            self.assertIsNone(m._to_item(cosmetic, "q"))
            self.assertIsNone(m._to_item(no_subject, "q"))
            led = pending(m.SOURCE_MFDS)
        self.assertEqual(led["admin-991"]["reason_code"], "not_collectable")
        self.assertEqual(led["admin-992"]["reason_code"], "missing_required_field")
        self.assertEqual(m._document_id(cosmetic), "admin-991")

    def test_datago_docs_use_module_key_and_date(self):
        import collect_mfds_admin_action as m

        class FakePaginator:
            truncated = False
            total_count = 2

            def __iter__(self):
                yield ([{"ADM_DISPS_SEQ": "1", "ITEM_NAME": "정", "LAST_SETTLE_DATE": "20261103"},
                        {"ADM_DISPS_SEQ": "2", "ITEM_NAME": "old", "LAST_SETTLE_DATE": "20250101"}], "u")
        with patch.dict(os.environ, {"DATA_GO_KR_SERVICE_KEY": "k"}), \
                patch("grm_common.datago_paginate", return_value=FakePaginator()):
            out = sr.fetch_mfds_admin(START, END)
        self.assertEqual([(d.document_id, d.published) for d in out.docs], [("admin-1", "2026-11-03")])


class LedgerWiringTest(unittest.TestCase):
    def test_collector_loads_ledger_for_newly_connected_sources(self):
        for s in (ci.SOURCE_MHRA, ci.SOURCE_ISPE, ci.SOURCE_HC, ci.SOURCE_WHO, ci.SOURCE_MFDS):
            self.assertIn(s, ci._EXCLUSION_LEDGER_SOURCES)

    def test_new_ledger_sources_get_their_own_grace_start(self):
        self.assertGreaterEqual(sr.LEDGER_EXPANSION_FROM, sr.LEDGER_ACTIVE_FROM)
        spec = sr.SPEC_BY_KEY["hc_recall"]
        d = sr.SourceDoc(document_id="hc-1", published=(sr.LEDGER_EXPANSION_FROM.replace(day=sr.LEDGER_EXPANSION_FROM.day - 1)).isoformat())
        run = sr.LEDGER_EXPANSION_FROM.replace(day=sr.LEDGER_EXPANSION_FROM.day + 5)
        res = sr.reconcile_source(spec, sr.FetchOutcome(docs=[d]), run_date=run, window=(START, run),
                                  collected=set(), ledger={})
        self.assertEqual((res.counts["pre_ledger"], res.counts["missed"]), (1, 0))


class SnapshotStoreTest(unittest.TestCase):
    def test_snapshot_sources_read_with_three_year_window_only_for_their_source(self):
        calls = []

        def fake(run_date, window_days, sources, token, db_id):
            calls.append((window_days, frozenset(sources)))
            return ({"ICH::ich-1"} if "ICH" in sources else {"EMA::e1"}), set()
        specs = [sr.SPEC_BY_KEY["ich"], sr.SPEC_BY_KEY["ema"]]
        env = {"NOTION_TOKEN": "t", "NOTION_DATABASE_ID": "d", "ENABLE_ICH": "true",
               "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"}
        fetchers = {"ich": lambda s, e: sr.FetchOutcome(docs=[sr.SourceDoc("ich-1")]),
                    "ema": lambda s, e: sr.FetchOutcome(docs=[sr.SourceDoc("e1", published="2026-11-29")])}
        with patch.object(sr, "load_collected_keys", side_effect=fake), \
                patch.object(sr, "load_ledger", return_value={}):
            rep = sr.run(run_date=RUN, base_days=7, enf_days=30, specs=specs, fetchers=fetchers, env=env)
        self.assertIn((sr.SNAPSHOT_DEDUP_DAYS, frozenset({"ICH"})), calls)
        self.assertIn((7 + sr.NOTION_DEDUP_MARGIN_DAYS, frozenset({"EMA"})), calls)
        by = {s["key"]: s for s in rep["sources"]}
        self.assertEqual(by["ich"]["counts"]["collected"], 1)
        self.assertEqual(by["ema"]["counts"]["collected"], 1)


class WorkflowContractTest(unittest.TestCase):
    WF = (ROOT / ".github" / "workflows" / "grm-source-reconcile.yml").read_text(encoding="utf-8")

    def test_dispatch_accepts_every_source_key(self):
        line = next(ln for ln in self.WF.splitlines() if 'src_args+=("$t")' in ln)
        tokens = set(line.strip().split(")")[0].split("|"))
        self.assertEqual(tokens, set(sr.SPEC_BY_KEY))

    def test_flags_and_secret_passed(self):
        for s in sr.SPECS:
            if s.flag:
                self.assertIn(f"{s.flag}: ${{{{ vars.{s.flag}", self.WF)
        self.assertIn("DATA_GO_KR_SERVICE_KEY: ${{ secrets.DATA_GO_KR_SERVICE_KEY }}", self.WF)
        self.assertNotIn("ENABLE_WHOPIR_EXCERPT", self.WF)   # 목록만 — PDF 본문은 받지 않는다


if __name__ == "__main__":
    unittest.main()
