#!/usr/bin/env python3
"""[handoff 정본 DB 2026-11-13] GRM_HANDOFF_SOURCE=db — 기본 꺼짐.

지키는 것:
- 변수가 꺼져 있으면(기본 notion) 그림자 보고서·파일·수집기 판정이 종전과 같다(`canonical` 키도 없다).
- db 면 DB handoff 가 발행 스캐폴드를 수집기의 스캐폴드 자리에 **같은 이름·같은 바이트**로 낸다.
- 동결된 날(발행 요일 재실행·이미 닫힌 호)의 재실행분은 스캐폴드를 덮어쓰지 않는다.
- db 면 health 의 handoff 성패는 DB handoff 가 정한다. 노션 handoff 는 거울이라 실패해도 handoff 실패가 아니다.
- db 인데 DB handoff 를 못 만들면 handoff 실패(failure)다 — 조용히 넘어가지 않는다.
- db 면 노션 길은 스캐폴드를 쓰지 않는다(같은 파일을 두 길이 쓰지 않는다).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import collect_intake as ci  # noqa: E402
import grm_handoff as gh  # noqa: E402
import grm_health  # noqa: E402
import grm_notion  # noqa: E402
import grm_queue as gq  # noqa: E402
from test_grm_queue import FakeStore, _Item, _record  # noqa: E402
from test_intake_db_dedup import _FakeQueueStore, _item, _ok, _NOW, _SUPABASE  # noqa: E402

RUN = date(2026, 11, 16)          # 월요일(발행 요일)
GEN = datetime(2026, 11, 16, 3, 40, tzinfo=gq.KST)
TUE = date(2026, 11, 17)
BRIEF = "brief_web_2026_11_16.json"


class SwitchTest(unittest.TestCase):
    def test_default_is_notion_and_only_db_turns_it_on(self):
        for value, want in ((None, "notion"), ("", "notion"), ("notion", "notion"),
                            ("true", "notion"), ("supabase", "notion"), ("db", "db"), (" DB ", "db")):
            env = {} if value is None else {"GRM_HANDOFF_SOURCE": value}
            with mock.patch.dict(os.environ, env):
                if value is None:
                    os.environ.pop("GRM_HANDOFF_SOURCE", None)
                self.assertEqual(gq.handoff_source(), want, value)

    def test_workflow_and_env_example_default_off(self):
        wf = (ROOT / ".github" / "workflows" / "grm-intake.yml").read_text(encoding="utf-8")
        self.assertIn("GRM_HANDOFF_SOURCE: ${{ vars.GRM_HANDOFF_SOURCE || 'notion' }}", wf)
        self.assertIn("GRM_HANDOFF_SOURCE=notion", (ROOT / ".env.example").read_text(encoding="utf-8"))


class CanonicalShadowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.shadow_dir = os.path.join(self.root, "_queue_shadow")
        self.canon_dir = os.path.join(self.root, "canon")
        os.makedirs(os.path.join(self.root, "web", "data", "deltas"))
        os.makedirs(os.path.join(self.root, "web", "data", "briefs"))
        self.store = FakeStore()
        for doc in ("2026-1", "2026-2"):
            self.store.add_raw(_record("Federal Register", doc))
        self.store.enqueue([{"source": "Federal Register", "document_id": d,
                             "first_seen_run_date": "2026-11-13",
                             "first_seen_at": "2026-11-13T03:30:00+09:00"}
                            for d in ("2026-1", "2026-2")])

    def _shadow(self, run=RUN, **kw):
        gen = datetime(run.year, run.month, run.day, 3, 40, tzinfo=gq.KST)
        with mock.patch.object(gq, "log"), mock.patch.object(gh, "log"), \
                mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            os.environ.pop("GRM_QUEUE_HANDOFF_REFRESH", None)
            return gq.run_shadow(("u", "k"), run_date=run, generated_at=gen, window_days=30,
                                 display_window_days=7, store=self.store, out_dir=self.shadow_dir,
                                 repo_root=self.root, **kw)

    def _bytes(self, folder, name=BRIEF):
        with open(os.path.join(folder, name), "rb") as f:
            return f.read()

    def test_default_report_and_files_are_unchanged(self):
        plain = self._shadow()
        self.assertNotIn("canonical", plain)
        self.assertFalse(os.path.exists(self.canon_dir))
        # 같은 재료로 canonical 을 켠 보고서에서 `canonical` 키만 빼면 종전 보고서와 같다.
        other = CanonicalShadowTest("test_default_report_and_files_are_unchanged")
        other.setUp()
        self.addCleanup(other.tmp.cleanup)
        on = other._shadow(canonical=True, canonical_web_brief_dir=other.canon_dir)
        on.pop("canonical")
        self.assertEqual(json.dumps(plain, sort_keys=True, ensure_ascii=False),
                         json.dumps(on, sort_keys=True, ensure_ascii=False))
        self.assertEqual(self._bytes(self.shadow_dir), other._bytes(other.shadow_dir))

    def test_canonical_writes_the_same_scaffold_bytes_where_the_collector_puts_it(self):
        report = self._shadow(canonical=True, canonical_web_brief_dir=self.canon_dir)
        canon = report["canonical"]
        self.assertEqual((canon["ok"], canon["rows"], canon["frozen"], canon["error"]),
                         (True, 2, "", ""))
        self.assertEqual(canon["web_brief"], os.path.join(self.canon_dir, BRIEF))
        self.assertEqual(self._bytes(self.canon_dir), self._bytes(self.shadow_dir))
        # 노션 길이 같은 재료로 냈을 파일과도 같다(같은 producer).
        with mock.patch.object(gh, "log"):
            rows, raws, _ = gq.load_candidates(self.store, RUN, 30, gh._HANDOFF_V2_ROW_KEEP, apply=False)
            enriched, _ = gh.enrich_rows_with_raw("", rows, inmemory_raw=raws, sleep_s=0)
            notion_dir = os.path.join(self.root, "notion")
            gh.emit_web_brief_file(enriched, RUN, 7, notion_dir)
        self.assertEqual(self._bytes(self.canon_dir), self._bytes(notion_dir))

    def test_canonical_without_a_scaffold_dir_still_reports_success(self):
        report = self._shadow(canonical=True)          # ENABLE_WEB_BRIEF_EMIT off
        self.assertTrue(report["canonical"]["ok"])
        self.assertEqual(report["canonical"]["web_brief"], "")
        self.assertFalse(os.path.exists(self.canon_dir))

    def test_publish_day_rerun_does_not_overwrite_the_scaffold(self):
        self._shadow(canonical=True, canonical_web_brief_dir=self.canon_dir)
        first = self._bytes(self.canon_dir)
        self.store.add_raw(_record("Federal Register", "2026-3"))
        report = self._shadow(new_items=[_Item("Federal Register", "2026-3")],
                              canonical=True, canonical_web_brief_dir=self.canon_dir)
        canon = report["canonical"]
        self.assertEqual((canon["ok"], canon["frozen"], canon["web_brief"]),
                         (True, gq.FROZEN_PUBLISH_DAY, ""))
        self.assertEqual(self._bytes(self.canon_dir), first)
        # 재실행 실행기(새 러너)에는 정본 자리가 비어 있다 — 그 실행은 스캐폴드를 내지 않는다.
        fresh = os.path.join(self.root, "fresh")
        self._shadow(canonical=True, canonical_web_brief_dir=fresh)
        self.assertFalse(os.path.exists(os.path.join(fresh, BRIEF)))

    def test_other_weekday_rerun_refreshes_the_scaffold(self):
        self._shadow(run=TUE, canonical=True, canonical_web_brief_dir=self.canon_dir)
        name = "brief_web_2026_11_17.json"
        first = self._bytes(self.canon_dir, name)
        self.store.add_raw(_record("Federal Register", "2026-3"))
        report = self._shadow(run=TUE, new_items=[_Item("Federal Register", "2026-3")],
                              canonical=True, canonical_web_brief_dir=self.canon_dir)
        self.assertEqual(report["canonical"]["rows"], 3)
        self.assertNotEqual(self._bytes(self.canon_dir, name), first)

    def test_failure_is_reported_not_swallowed(self):
        class Broken(FakeStore):
            def open_candidates(self, start, end):
                raise gq.QueueStoreError("intake_queue GET: http_500")
        self.store = Broken()
        report = self._shadow(canonical=True, canonical_web_brief_dir=self.canon_dir)
        self.assertFalse(report["canonical"]["ok"])
        self.assertIn("DB handoff 생성 실패", report["canonical"]["error"])
        self.assertFalse(os.path.exists(self.canon_dir))

    def test_aged_open_rows_are_returned(self):
        self.store.add_raw(_record("Federal Register", "2026-old"))
        self.store.enqueue([{"source": "Federal Register", "document_id": "2026-old",
                             "first_seen_run_date": "2026-09-01",
                             "first_seen_at": "2026-09-01T03:30:00+09:00"}])
        report = self._shadow(canonical=True)
        self.assertEqual(report["canonical"]["aged_open"], 1)


class MainHandoffSourceTest(unittest.TestCase):
    """수집기 main() — 어느 handoff 가 성패·스캐폴드를 정하는가."""

    def setUp(self):
        _FakeQueueStore.made, _FakeQueueStore.fail, _FakeQueueStore.url_fail = 0, False, False

    def _run_main(self, extra_env, *, canonical=None, shadow_raises=False, notion_handoff_error=False,
                  create_ok=True, supa_status="inserted"):
        stats = ci.CollectionStats()
        groups = [[] for _ in range(22)]
        groups[7] = [_item("wl-new")]
        collected = (stats,) + tuple(groups)
        env = {"NOTION_TOKEN": "tok", "NOTION_DATABASE_ID": "db",
               "ENABLE_FINDINGS_SUPABASE_APPEND": "true", "RAW_SIGNALS_HEAL": "false",
               "RAW_UPDATE_APPLY": "false", "ENABLE_SOURCE_SILENCE_WATCHDOG": "false",
               "ENABLE_WEB_BRIEF_EMIT": "true", "GRM_WEB_BRIEF_DIR": "canon-dir",
               "SUPABASE_URL": _SUPABASE[0], "SUPABASE_SERVICE_ROLE_KEY": _SUPABASE[1]}
        env.update(extra_env)
        report = {} if canonical is None else {"canonical": canonical}
        shadow_kw = ({"side_effect": RuntimeError("boom")} if shadow_raises
                     else {"return_value": report})
        emit_kw = ({"side_effect": ci.NotionHandoffError("notion 502")} if notion_handoff_error
                   else {"return_value": (7, "https://notion.so/handoff")})
        with tempfile.TemporaryDirectory() as d:
            env["GRM_HEALTH_JSON"] = os.path.join(d, "grm-health.json")
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(sys, "argv", ["collect_intake.py", "--emit-routine-handoff"]), \
                    mock.patch.object(ci, "now_kst", return_value=_NOW), \
                    mock.patch.object(ci.time, "sleep"), \
                    mock.patch.object(ci, "notion_verify_modality_property", return_value=True), \
                    mock.patch.object(ci, "notion_verify_handoff_ref_property", return_value=True), \
                    mock.patch.object(ci, "probe_kr_egress_proxy", return_value=("unconfigured", "")), \
                    mock.patch.object(ci, "_fda483_known_document_ids", return_value=None), \
                    mock.patch.object(ci, "_configure_exclusion_ledger"), \
                    mock.patch.object(ci, "_run_collection", return_value=collected), \
                    mock.patch.object(ci, "notion_query_existing_keys",
                                      return_value=grm_notion.ExistingKeys(doc_ids=set(), url_keys=set())), \
                    mock.patch.object(ci, "notion_count_aged_unconsumed_new", return_value=0), \
                    mock.patch.object(ci, "existing_raw_signal_ids", return_value=set()), \
                    mock.patch.object(gq, "QueueStore", _FakeQueueStore), \
                    mock.patch.object(gq, "run_shadow", **shadow_kw) as shadow, \
                    mock.patch.object(gq, "health_summary", return_value={}), \
                    mock.patch.object(ci, "emit_routine_handoff", **emit_kw) as emit, \
                    mock.patch.object(ci, "append_intake_item_to_supabase",
                                      return_value=_ok(supa_status)), \
                    mock.patch.object(ci, "notion_create_page", return_value=create_ok):
                for name in ("GRM_HANDOFF_SOURCE", "GRM_INTAKE_DEDUP_SOURCE",
                             "ENABLE_INTAKE_DEDUP_SHADOW", "ENABLE_INTAKE_QUEUE_SHADOW"):
                    if name not in extra_env:
                        os.environ.pop(name, None)
                code = ci.main()
            with open(env["GRM_HEALTH_JSON"], encoding="utf-8") as f:
                payload = json.load(f)
        return code, payload, shadow, emit

    @staticmethod
    def _codes(payload, level):
        return [f["code"] for f in payload["health"][level]]

    def test_default_keeps_notion_as_the_source_and_never_asks_for_canonical(self):
        code, payload, shadow, emit = self._run_main({"ENABLE_INTAKE_QUEUE_SHADOW": "true"})
        self.assertEqual(code, 0)
        self.assertEqual(emit.call_args.kwargs["web_brief_dir"], "canon-dir")   # 스캐폴드는 노션 길
        self.assertNotIn("canonical", shadow.call_args.kwargs)
        self.assertNotIn("canonical_web_brief_dir", shadow.call_args.kwargs)
        self.assertNotIn("handoff_source", payload["store"])
        self.assertEqual(payload["handoff"]["emitted"], True)
        self.assertEqual(payload["handoff"]["row_count"], 7)
        self.assertEqual(payload["handoff"]["url"], "https://notion.so/handoff")

    def test_default_without_shadow_does_not_touch_the_queue(self):
        code, payload, shadow, _emit = self._run_main({})
        self.assertEqual(code, 0)
        shadow.assert_not_called()
        self.assertNotIn("handoff_source", payload["store"])

    def test_db_source_decides_success_and_owns_the_scaffold(self):
        canon = {"ok": True, "rows": 5, "frozen": "", "aged_open": 0,
                 "web_brief": "canon-dir/brief_web_2026_11_20.json", "error": ""}
        code, payload, shadow, emit = self._run_main({"GRM_HANDOFF_SOURCE": "db"}, canonical=canon)
        self.assertEqual(code, 0)
        self.assertIsNone(emit.call_args.kwargs["web_brief_dir"])     # 노션 거울은 스캐폴드를 안 쓴다
        self.assertIs(shadow.call_args.kwargs["canonical"], True)
        self.assertEqual(shadow.call_args.kwargs["canonical_web_brief_dir"], "canon-dir")
        self.assertEqual(payload["handoff"]["emitted"], True)
        self.assertEqual(payload["handoff"]["row_count"], 5)          # DB handoff 의 행 수
        self.assertEqual(payload["handoff"]["url"], "")
        hs = payload["store"]["handoff_source"]
        self.assertEqual((hs["source"], hs["notion_mirror"], hs["notion_mirror_rows"], hs["web_brief"]),
                         ("db", "ok", 7, True))

    def test_db_source_turns_the_queue_on_by_itself(self):
        canon = {"ok": True, "rows": 1, "frozen": "", "aged_open": 0, "web_brief": "", "error": ""}
        _code, _payload, shadow, _emit = self._run_main({"GRM_HANDOFF_SOURCE": "db"}, canonical=canon)
        shadow.assert_called_once()
        self.assertEqual([it.document_id for it in shadow.call_args.kwargs["new_items"]], ["wl-new"])

    def test_notion_mirror_failure_is_not_a_handoff_failure(self):
        canon = {"ok": True, "rows": 5, "frozen": "", "aged_open": 0, "web_brief": "x", "error": ""}
        code, payload, _shadow, _emit = self._run_main(
            {"GRM_HANDOFF_SOURCE": "db"}, canonical=canon, notion_handoff_error=True)
        self.assertEqual(code, 0)
        self.assertNotIn("handoff-failed", self._codes(payload, "failures"))
        self.assertEqual(payload["handoff"]["emitted"], True)
        hs = payload["store"]["handoff_source"]
        self.assertEqual(hs["notion_mirror"], "failed")
        self.assertIn("notion 502", hs["notion_mirror_error"])

    def test_db_handoff_failure_is_a_failure_even_if_the_mirror_worked(self):
        cases = (
            {"canonical": {"ok": False, "rows": 0, "frozen": "", "aged_open": 0, "web_brief": "",
                           "error": "DB handoff 생성 실패: QueueStoreError: http_500"}},
            {"shadow_raises": True},
            {"canonical": None},                       # 보고서에 canonical 이 없다(구버전 그림자)
        )
        for kw in cases:
            with self.subTest(kw=kw):
                code, payload, _shadow, emit = self._run_main({"GRM_HANDOFF_SOURCE": "db"}, **kw)
                emit.assert_called_once()              # 거울은 만들어졌다
                self.assertEqual(code, 1)
                self.assertIn("handoff-failed", self._codes(payload, "failures"))
                self.assertEqual(payload["handoff"]["emitted"], False)
                self.assertTrue(payload["handoff"]["error_msg"])

    def test_notion_only_failure_still_fails_in_notion_mode(self):
        code, payload, _shadow, _emit = self._run_main(
            {"ENABLE_INTAKE_QUEUE_SHADOW": "true"}, notion_handoff_error=True)
        self.assertEqual(code, 1)
        self.assertIn("handoff-failed", self._codes(payload, "failures"))

    def test_db_source_without_supabase_falls_back_to_notion(self):
        code, payload, shadow, emit = self._run_main(
            {"GRM_HANDOFF_SOURCE": "db", "ENABLE_FINDINGS_SUPABASE_APPEND": "false"})
        self.assertEqual(code, 0)
        shadow.assert_not_called()
        self.assertEqual(emit.call_args.kwargs["web_brief_dir"], "canon-dir")
        self.assertNotIn("handoff_source", payload["store"])
        self.assertEqual(payload["handoff"]["url"], "https://notion.so/handoff")

    def test_store_failure_warning_says_the_document_is_missing_from_todays_handoff(self):
        canon = {"ok": True, "rows": 0, "frozen": "", "aged_open": 0, "web_brief": "", "error": ""}
        _code, db_payload, _s, _e = self._run_main(
            {"GRM_HANDOFF_SOURCE": "db"}, canonical=canon, supa_status="invalid")
        _code, notion_payload, _s, _e = self._run_main({}, supa_status="invalid")

        def message(payload):
            return [f["message"] for f in payload["health"]["warnings"]
                    if f["code"] == "supabase-append-failed"]
        self.assertEqual(notion_payload and message(notion_payload),
                         ["Supabase 정본 저장 실패 1건"])            # 종전 문구 그대로
        self.assertEqual(len(message(db_payload)), 1)
        self.assertIn("오늘 handoff(DB 정본)에 없는 문서", message(db_payload)[0])


class HealthTextMutationGuard(unittest.TestCase):
    """health 문구 분기가 `store.handoff_source.source == 'db'` 하나에만 걸려 있는지."""

    def test_branch_reads_the_store_key(self):
        src = Path(grm_health.__file__).read_text(encoding="utf-8")
        self.assertIn('(store.get("handoff_source") or {}).get("source") == "db"', src)


if __name__ == "__main__":
    unittest.main()
