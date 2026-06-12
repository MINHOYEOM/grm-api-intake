"""PL-10b/B1 멱등성 근본해결(v2) — `Handoff Ref` 상태기계 단위 테스트.

커버리지(지시문 §7):
  - PL-10b: CONSUMED handoff 의 Status 지연 row → reconcile 이 Processed 마감,
    소비 쿼리는 ref 기반이라 재유입 0 (중복 0)
  - B1: STALE 전환 시 미발행 row ref 비움 → 재투입 (누락 0, 날짜 윈도우 무관)
  - K4-1 상호작용: revert_refs 기본 off 시 개별 row 불가침 유지·OPEN 1개 불변식
  - flag off: v1 소비 쿼리 필터 바이트 동일(잠금)·emit 경로에서 v2 함수 미호출
  - preflight: 속성 부재/타입 불일치 → v1 폴백 + health warning
"""
import json
import os
import unittest
from datetime import date, datetime
from unittest import mock

import collect_intake as ci

RUN_DATE = date(2026, 6, 12)
GEN_AT = datetime(2026, 6, 12, 3, 17)
HID = "routine-handoff::2026-06-12"
PRIOR_HID = "routine-handoff::2026-06-11"


def _row_page(pid: str, doc_id: str, ref: str = "", status: str = "New",
              run_date: str = "2026-06-10", source: str = "MFDS") -> dict:
    """Intake row page 의 Notion query 결과 형태(snapshot + Handoff Ref 가 읽는 최소 props)."""
    return {
        "id": pid, "url": f"https://app.notion.com/p/{pid}",
        "properties": {
            "Name": {"title": [{"plain_text": f"{source} {doc_id}"}]},
            "Source": {"select": {"name": source}},
            "Document ID": {"rich_text": [{"plain_text": doc_id}]},
            "Status": {"select": {"name": status}},
            "Run Date (KST)": {"date": {"start": run_date}},
            "Handoff Ref": {"rich_text": [{"plain_text": ref}] if ref else []},
        },
    }


def _handoff_page(handoff_id: str, run_date: str, status: str, pid: str) -> dict:
    return {
        "id": pid, "url": f"https://app.notion.com/p/{pid}",
        "properties": {
            "Name": {"title": [{"plain_text": f"OPEN GRM Routine Handoff {run_date}"}]},
            "Source": {"select": {"name": "GRM Handoff"}},
            "Document ID": {"rich_text": [{"plain_text": handoff_id}]},
            "Type or Class": {"select": {"name": "routine-handoff"}},
            "Status": {"select": {"name": status}},
            "Run Date (KST)": {"date": {"start": run_date}},
        },
    }


def _filter_blob(body: dict) -> str:
    return json.dumps(body.get("filter", {}), ensure_ascii=False)


class FlagTest(unittest.TestCase):
    def test_flag_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ci._enable_handoff_idempotency_v2())

    def test_flag_on_off(self) -> None:
        with mock.patch.dict(os.environ, {"ENABLE_HANDOFF_IDEMPOTENCY_V2": "true"}):
            self.assertTrue(ci._enable_handoff_idempotency_v2())
        with mock.patch.dict(os.environ, {"ENABLE_HANDOFF_IDEMPOTENCY_V2": "false"}):
            self.assertFalse(ci._enable_handoff_idempotency_v2())


class PreflightTest(unittest.TestCase):
    """§5 — 'Handoff Ref' 속성 사전 점검: 부재/타입 불일치/조회 실패 → False(v1 폴백)."""

    def _verify(self, properties: dict | Exception) -> bool:
        def fake_api(method, url, token, body=None, **kw):
            if isinstance(properties, Exception):
                raise properties
            return {"properties": properties}

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api):
            return ci.notion_verify_handoff_ref_property("tok", "db")

    def test_rich_text_property_ok(self) -> None:
        self.assertTrue(self._verify({"Handoff Ref": {"type": "rich_text"}}))

    def test_missing_property_fails(self) -> None:
        self.assertFalse(self._verify({"Status": {"type": "select"}}))

    def test_wrong_type_fails(self) -> None:
        self.assertFalse(self._verify({"Handoff Ref": {"type": "select"}}))

    def test_db_query_failure_fails_closed(self) -> None:
        self.assertFalse(self._verify(ci.NotionHandoffError("HTTP 500")))


class ConsumeQueryTest(unittest.TestCase):
    """소비 쿼리 — v1 필터 잠금(flag off 바이트 동일) + v2 ref 기반(날짜 하한 제거)."""

    def _capture_query_body(self, current_handoff_id: str | None) -> dict:
        captured = {}

        def fake_api(method, url, token, body=None, **kw):
            captured["body"] = json.loads(json.dumps(body))  # deep copy
            return {"results": [], "has_more": False}

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api):
            ci.notion_query_new_intake_rows(
                "tok", "db", RUN_DATE, 30, current_handoff_id=current_handoff_id)
        return captured["body"]

    def test_v1_filter_byte_identical_lock(self) -> None:
        # flag off 회귀 잠금 — 기존(날짜 윈도우 + Status=New) 필터와 구조 동일.
        body = self._capture_query_body(None)
        self.assertEqual(body, {
            "filter": {"and": [
                {"property": "Run Date (KST)", "date": {"on_or_after": "2026-05-13"}},
                {"property": "Run Date (KST)", "date": {"on_or_before": "2026-06-12"}},
                {"property": "Status", "select": {"equals": "New"}},
            ]},
            "page_size": 100,
        })

    def test_v2_filter_ref_based_no_date_floor(self) -> None:
        # B1 근본: 날짜 하한(on_or_after) 제거 — 윈도우 30일 밖 row 도 자격 유지.
        body = self._capture_query_body(HID)
        blob = _filter_blob(body)
        self.assertNotIn("on_or_after", blob)
        self.assertEqual(body["filter"]["and"][0],
                         {"property": "Run Date (KST)",
                          "date": {"on_or_before": "2026-06-12"}})
        self.assertEqual(body["filter"]["and"][1],
                         {"property": "Status", "select": {"equals": "New"}})

    def test_v2_filter_or_clause_empty_or_current(self) -> None:
        # 자격 = ref 비어있음 ∨ ref=오늘(같은 날 재-emit 시 이미 표시된 row 포함).
        body = self._capture_query_body(HID)
        or_clause = body["filter"]["and"][2]["or"]
        self.assertIn({"property": "Handoff Ref", "rich_text": {"is_empty": True}},
                      or_clause)
        self.assertIn({"property": "Handoff Ref", "rich_text": {"equals": HID}},
                      or_clause)


class MarkRefTest(unittest.TestCase):
    """emit 표시 — 포함 row 에 ref 기록(Status 불변)·per-row 실패 graceful."""

    def _rows(self) -> list[dict]:
        return [{"page_id": "p1", "source": "MFDS", "document_id": "d1"},
                {"page_id": "p2", "source": "MFDS", "document_id": "d2"},
                {"page_id": "p3", "source": "MFDS", "document_id": "d3"}]

    def test_marks_all_rows_ref_only(self) -> None:
        patches = []

        def fake_api(method, url, token, body=None, **kw):
            patches.append((url, body))
            return {}

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api), \
             mock.patch("collect_intake.time.sleep"):
            ok, failed = ci.notion_mark_rows_handoff_ref("tok", self._rows(), HID)

        self.assertEqual((ok, failed), (3, 0))
        self.assertEqual(len(patches), 3)
        for url, body in patches:
            props = body["properties"]
            self.assertEqual(set(props.keys()), {"Handoff Ref"})  # Status 는 New 유지
            self.assertEqual(props["Handoff Ref"]["rich_text"][0]["text"]["content"],
                             HID)

    def test_mark_failure_graceful_continues(self) -> None:
        # 기록 실패 row 는 v1 동작 폴백(ref 없음 → 다음 emit 재포함) — 전체 중단 금지.
        def fake_api(method, url, token, body=None, **kw):
            if "p2" in url:
                raise ci.NotionHandoffError("HTTP 502")
            return {}

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api), \
             mock.patch("collect_intake.time.sleep"):
            ok, failed = ci.notion_mark_rows_handoff_ref("tok", self._rows(), HID)
        self.assertEqual((ok, failed), (2, 1))


class StaleRevertTest(unittest.TestCase):
    """B1 revert — STALE 전환 시 그 handoff 의 미발행(Status=New) row 만 ref 비움.
    K4-1 상호작용: revert_refs 기본 off 면 기존 동작(개별 row 일절 불가침) 그대로."""

    def _run(self, revert_refs: bool):
        calls = {"handoff_patches": [], "row_patches": [], "row_query_filters": []}

        def fake_api(method, url, token, body=None, **kw):
            if method == "POST" and "/query" in url:
                blob = _filter_blob(body)
                if "Type or Class" in blob:  # K4-1 handoff 조회
                    return {"results": [_handoff_page(
                        PRIOR_HID, "2026-06-11", "New", "prior-page")],
                        "has_more": False}
                if "Handoff Ref" in blob:    # revert 대상 row 조회
                    calls["row_query_filters"].append(body["filter"])
                    return {"results": [
                        _row_page("row-a", "doc-a", ref=PRIOR_HID),
                        _row_page("row-b", "doc-b", ref=PRIOR_HID,
                                  run_date="2026-04-01"),  # 윈도우 30일 밖 — 재투입 성립
                    ], "has_more": False}
                raise AssertionError(f"예상 밖 query: {blob}")
            if method == "PATCH":
                if "prior-page" in url:
                    calls["handoff_patches"].append(body)
                else:
                    calls["row_patches"].append((url, body))
                return {}
            return {}

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api), \
             mock.patch("collect_intake.time.sleep"):
            staled = ci.notion_stale_prior_open_handoffs(
                "tok", "db", keep_handoff_id=HID, superseded_by="2026-06-12",
                revert_refs=revert_refs)
        return staled, calls

    def test_revert_clears_refs_of_unpublished_rows_only(self) -> None:
        staled, calls = self._run(revert_refs=True)
        self.assertEqual(staled, 1)
        # handoff page 자신은 Name·Status 만(기존 K4-1 불변식 유지).
        self.assertEqual(set(calls["handoff_patches"][0]["properties"].keys()),
                         {"Name", "Status"})
        # row 는 Handoff Ref 만 비움 — Status 는 불변(발행분 Processed 보호는 쿼리 필터로).
        self.assertEqual(len(calls["row_patches"]), 2)
        for _url, body in calls["row_patches"]:
            self.assertEqual(set(body["properties"].keys()), {"Handoff Ref"})
            self.assertEqual(body["properties"]["Handoff Ref"]["rich_text"], [])
        # revert 대상 조회가 Status=New ∧ ref=STALE id 로 한정 — Processed row 불가침.
        blob = json.dumps(calls["row_query_filters"][0], ensure_ascii=False)
        self.assertIn('"equals": "New"', blob)
        self.assertIn(PRIOR_HID, blob)

    def test_default_off_keeps_k41_row_inviolability(self) -> None:
        # revert_refs 미지정(v1 K4-1) — handoff page 만 봉인, row 조회/PATCH 0건.
        staled, calls = self._run(revert_refs=False)
        self.assertEqual(staled, 1)
        self.assertEqual(len(calls["handoff_patches"]), 1)
        self.assertEqual(calls["row_patches"], [])
        self.assertEqual(calls["row_query_filters"], [])


class ReconcileTest(unittest.TestCase):
    """reconcile sweep — CONSUMED cleanup(PL-10b)·STALE/고아 revert(B1)·current 유지."""

    def _run(self, rows: list[tuple[str, str]], handoff_status: dict):
        patches = []

        def fake_find(token, db_id, handoff_id):
            status = handoff_status.get(handoff_id)
            if status is None:
                return None
            return _handoff_page(handoff_id, handoff_id.split("::")[-1], status,
                                 f"hp-{handoff_id[-2:]}")

        def fake_api(method, url, token, body=None, **kw):
            if method == "PATCH":
                patches.append((url, body))
            return {}

        with mock.patch.object(ci, "_query_new_rows_with_ref", return_value=rows), \
             mock.patch.object(ci, "notion_find_handoff_page", side_effect=fake_find), \
             mock.patch.object(ci, "notion_api_request", side_effect=fake_api), \
             mock.patch("collect_intake.time.sleep"):
            stats = ci.notion_reconcile_handoff_refs("tok", "db",
                                                     current_handoff_id=HID)
        return stats, patches

    def test_consumed_handoff_rows_closed_as_processed(self) -> None:
        # PL-10b: 발행(CONSUMED=Processed)됐는데 Status 갱신이 누락된 row → Processed 마감.
        stats, patches = self._run(
            rows=[("row-a", "routine-handoff::2026-06-08")],
            handoff_status={"routine-handoff::2026-06-08": "Processed"})
        self.assertEqual(stats["cleaned"], 1)
        self.assertEqual(len(patches), 1)
        url, body = patches[0]
        self.assertIn("row-a", url)
        props = body["properties"]
        self.assertEqual(set(props.keys()), {"Status"})
        self.assertEqual(props["Status"]["select"]["name"], "Processed")
        # ref 는 추적성 위해 유지(비우지 않음) — 마감됐으므로 소비 쿼리와 무관.

    def test_stale_leftover_refs_reverted(self) -> None:
        # B1: STALE(Skipped) handoff 에 묶인 채 남은 row(직전 revert 실패분) → ref 비움.
        stats, patches = self._run(
            rows=[("row-b", "routine-handoff::2026-06-09")],
            handoff_status={"routine-handoff::2026-06-09": "Skipped"})
        self.assertEqual(stats["reverted"], 1)
        _url, body = patches[0]
        self.assertEqual(set(body["properties"].keys()), {"Handoff Ref"})
        self.assertEqual(body["properties"]["Handoff Ref"]["rich_text"], [])

    def test_orphan_ref_reverted_with_warning(self) -> None:
        # handoff page 미발견(고아 ref) — 재투입이 침묵 누락보다 안전(중복은 v16 가드가 방어).
        stats, patches = self._run(
            rows=[("row-c", "routine-handoff::2026-01-01")], handoff_status={})
        self.assertEqual(stats["orphaned"], 1)
        self.assertEqual(body := patches[0][1],
                         {"properties": {"Handoff Ref": {"rich_text": []}}})

    def test_current_handoff_rows_kept(self) -> None:
        # 오늘 ref 는 불변(같은 날 재-emit — 소비 쿼리 OR 절이 포함) — PATCH 0건.
        stats, patches = self._run(rows=[("row-d", HID)], handoff_status={})
        self.assertEqual(stats["kept"], 1)
        self.assertEqual(patches, [])

    def test_unexpected_open_ref_kept_with_warning(self) -> None:
        # STALE 가드 선행 후에도 OPEN(≠오늘) ref 가 보이면 비정상 — 건드리지 않고 보류.
        stats, patches = self._run(
            rows=[("row-e", PRIOR_HID)], handoff_status={PRIOR_HID: "New"})
        self.assertEqual(stats["kept"], 1)
        self.assertEqual(patches, [])

    def test_mixed_rows_single_sweep(self) -> None:
        stats, patches = self._run(
            rows=[("row-a", "routine-handoff::2026-06-08"),
                  ("row-b", "routine-handoff::2026-06-09"),
                  ("row-d", HID)],
            handoff_status={"routine-handoff::2026-06-08": "Processed",
                            "routine-handoff::2026-06-09": "Skipped"})
        self.assertEqual((stats["cleaned"], stats["reverted"], stats["kept"]),
                         (1, 1, 1))
        self.assertEqual(len(patches), 2)


class RefRowQueryTest(unittest.TestCase):
    """_query_new_rows_with_ref — Status=New 한정·handoff page 제외·ref 추출."""

    def test_filter_and_handoff_page_exclusion(self) -> None:
        captured = {}

        def fake_api(method, url, token, body=None, **kw):
            captured["body"] = json.loads(json.dumps(body))
            return {"results": [
                _row_page("row-a", "doc-a", ref=PRIOR_HID),
                _handoff_page(PRIOR_HID, "2026-06-11", "New", "hp-11"),  # 제외 대상
            ], "has_more": False}

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api):
            rows = ci._query_new_rows_with_ref(
                "tok", "db",
                {"property": "Handoff Ref", "rich_text": {"is_not_empty": True}})

        self.assertEqual(rows, [("row-a", PRIOR_HID)])
        blob = _filter_blob(captured["body"])
        self.assertIn('"equals": "New"', blob)        # Status=New 한정
        self.assertIn("is_not_empty", blob)


class EmitIntegrationTest(unittest.TestCase):
    """emit 경로 와이어링 — flag on 시 상태기계 순서, flag off 시 v2 함수 미호출."""

    _ROWS = [{"page_id": "p1", "source": "MFDS", "document_id": "d1",
              "signal_tier": "Tier 2", "headline": "x", "run_date": "2026-06-10"}]

    def _run_emit(self, env: dict, reconcile_side_effect=None):
        order = []

        def track(name, ret=None):
            def _inner(*a, **kw):
                order.append((name, kw))
                if name == "reconcile" and reconcile_side_effect:
                    raise reconcile_side_effect
                return ret
            return _inner

        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ci, "notion_stale_prior_open_handoffs",
                               side_effect=track("stale", 0)) as stale, \
             mock.patch.object(ci, "notion_reconcile_handoff_refs",
                               side_effect=track("reconcile", {})) as reconcile, \
             mock.patch.object(ci, "notion_query_new_intake_rows",
                               side_effect=track("query", list(self._ROWS))), \
             mock.patch.object(ci, "notion_upsert_routine_handoff",
                               side_effect=track("upsert", ("pid", "url"))), \
             mock.patch.object(ci, "notion_mark_rows_handoff_ref",
                               side_effect=track("mark", (1, 0))) as mark:
            row_count, page_url = ci.emit_routine_handoff(
                "tok", "db", RUN_DATE, 30, GEN_AT, display_window_days=7)
        return order, stale, reconcile, mark, row_count

    def test_flag_on_state_machine_order(self) -> None:
        env = {"ENABLE_HANDOFF_IDEMPOTENCY_V2": "true"}
        order, stale, reconcile, mark, row_count = self._run_emit(env)
        # 순서: STALE+revert → reconcile → 소비 쿼리 → upsert → emit 표시.
        self.assertEqual([n for n, _ in order],
                         ["stale", "reconcile", "query", "upsert", "mark"])
        self.assertTrue(stale.call_args.kwargs["revert_refs"])
        self.assertEqual(stale.call_args.kwargs["keep_handoff_id"], HID)
        self.assertEqual(reconcile.call_args.kwargs["current_handoff_id"], HID)
        query_kwargs = next(kw for n, kw in order if n == "query")
        self.assertEqual(query_kwargs["current_handoff_id"], HID)
        mark_args = mark.call_args
        self.assertEqual(mark_args.args[1], self._ROWS)   # dedupe 전 전체 rows
        self.assertEqual(mark_args.args[2], HID)
        self.assertEqual(row_count, 1)

    def test_flag_off_v1_path_untouched(self) -> None:
        order, stale, reconcile, mark, _ = self._run_emit({})
        # v2 전용 함수(조기 STALE·reconcile·mark) 미호출 — K4-1 가드는 upsert 내부 경로
        # 그대로(여기선 upsert 를 mock 했으므로 호출 0). 소비 쿼리는 ref 미지정(v1 필터).
        self.assertEqual([n for n, _ in order], ["query", "upsert"])
        stale.assert_not_called()
        reconcile.assert_not_called()
        mark.assert_not_called()
        query_kwargs = next(kw for n, kw in order if n == "query")
        self.assertIsNone(query_kwargs["current_handoff_id"])

    def test_reconcile_failure_does_not_block_emit(self) -> None:
        # reconcile 은 위생 단계 — 실패해도 emit 계속(중복/누락 미발생, 다음 emit 재시도).
        env = {"ENABLE_HANDOFF_IDEMPOTENCY_V2": "true"}
        order, *_ = self._run_emit(
            env, reconcile_side_effect=ci.NotionHandoffError("HTTP 500"))
        self.assertIn("upsert", [n for n, _ in order])
        self.assertIn("mark", [n for n, _ in order])


class HealthWarningTest(unittest.TestCase):
    """preflight degrade — v1 폴백은 침묵이 아니라 warning 으로 표면화(exit 0)."""

    def _health_kwargs(self, **over):
        base = dict(
            stats=ci.CollectionStats(), active=set(),
            enable_search=False, enable_mfds=False, enable_mfds_recall=False,
            enable_mfds_admin=False, enable_mfds_gmp_inspection=False,
            enable_ich=False, enable_who=False, enable_hc=False,
            enable_fda483=False, enable_moleg_api=False, enable_scrape=False,
            event_name="schedule",
            emit_routine_handoff=False, handoff_emitted=False,
            handoff_failed=False, handoff_error_msg="",
        )
        base.update(over)
        return base

    def test_preflight_degraded_is_warning_exit0(self) -> None:
        health = ci._evaluate_health(**self._health_kwargs(
            handoff_idem_preflight_disabled=True))
        self.assertIn("handoff-idem-preflight-degraded",
                      [w.code for w in health.warnings])
        self.assertEqual(health.exit_code, 0)

    def test_no_warning_when_not_degraded(self) -> None:
        health = ci._evaluate_health(**self._health_kwargs())
        self.assertNotIn("handoff-idem-preflight-degraded",
                         [w.code for w in health.warnings])


if __name__ == "__main__":
    unittest.main()
