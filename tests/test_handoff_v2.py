"""handoff v2(단계 D) 단위 테스트 — additive·플래그·raw 미포함·children 분할·v1 보존."""
import io
import json
import os
import unittest
from datetime import date, datetime
from unittest import mock

import collect_intake as ci

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")
_UPDATE = bool(os.environ.get("GRM_GOLDEN_UPDATE"))
RUN_DATE = date(2026, 6, 5)
GEN_AT = datetime(2026, 6, 5, 3, 17)


def _enriched_rows() -> list[dict]:
    return [
        {  # MFDS recall (recall_group_key 대상)
            "source": "MFDS", "document_id": "recall-2026003474", "date": "2026-06-02",
            "type_or_class": "recall-quality", "firm": "한국제약", "headline": "정제 회수",
            "page_id": "page-aaa", "signal_tier": "Tier 2", "modality": "Chemical",
            "language": "KO", "raw_fetch_ok": True, "raw_source": "memory",
            "raw": {"ENTRPS": "한국제약(주)", "PRDUCT": "아세트아미노펜정",
                    "RTRVL_RESN": "함량부적합 자진 회수"},
        },
        {  # FR guidance
            "source": "Federal Register", "document_id": "FR-2026-04578", "date": "2026-05-22",
            "type_or_class": "guidance-industry", "firm": "", "headline": "Guidance X",
            "page_id": "page-bbb", "signal_tier": "Tier 2", "modality": "",
            "language": "", "raw_fetch_ok": True, "raw_source": "fetch",
            "raw": {"title": "Guidance X", "abstract": "This draft guidance describes ..."},
        },
    ]


class FlagTest(unittest.TestCase):
    def test_flag_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ci._enable_handoff_v2())

    def test_flag_on(self) -> None:
        with mock.patch.dict(os.environ, {"ENABLE_HANDOFF_V2": "true"}):
            self.assertTrue(ci._enable_handoff_v2())
        with mock.patch.dict(os.environ, {"ENABLE_HANDOFF_V2": "false"}):
            self.assertFalse(ci._enable_handoff_v2())


class BuildV2PayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = ci.build_routine_handoff_payload_v2(
            _enriched_rows(), RUN_DATE, 7, GEN_AT)

    def test_schema_version_v2(self) -> None:
        self.assertEqual(self.payload["schema_version"], "grm-routine-handoff/v2")
        self.assertEqual(self.payload["row_count"], 2)

    def test_weekday_kst_deterministic(self) -> None:
        # D-1: 발행 요일은 handoff 가 결정론 산출 — RUN_DATE 2026-06-05 = 금요일.
        self.assertEqual(self.payload["weekday_kst"], "금요일")
        self.assertEqual(self.payload["run_date_kst"], "2026-06-05")

    def test_rows_have_additive_v2_fields(self) -> None:
        for r in self.payload["rows"]:
            self.assertIn("card_id", r)
            self.assertIn("section", r)
            self.assertIn("card_scaffold", r)
            self.assertIn("prose_input", r)
            self.assertIn("needs_llm_slots", r)
            # v1 필드 보존(additive·하위호환)
            self.assertIn("source", r)
            self.assertIn("document_id", r)
            self.assertIn("page_id", r)

    def test_raw_full_payload_excluded(self) -> None:
        # 단계 D: raw 전체 JSON 은 절대 미포함(크기 폭증 방지)
        for r in self.payload["rows"]:
            self.assertNotIn("raw", r)
        # 직렬화 전체에도 raw 원본 키가 새어나오지 않음
        blob = json.dumps(self.payload, ensure_ascii=False)
        self.assertNotIn("RTRVL_RESN", blob)
        self.assertNotIn("\"abstract\"", blob)

    def test_recall_group_key_present_only_for_recall(self) -> None:
        recall = next(r for r in self.payload["rows"] if r["card_id"].startswith("MFDS::recall"))
        guidance = next(r for r in self.payload["rows"] if r["source"] == "Federal Register")
        self.assertTrue(recall["recall_group_key"])
        self.assertNotIn("recall_group_key", guidance)
        # card_id 는 source::document_id 유지(§12E)
        self.assertEqual(recall["card_id"], "MFDS::recall-2026003474")

    def test_render_order_assigned_to_visible_rows(self) -> None:
        # R1-d: 대표/단독 row 에 render_order 부여(0..N-1). <4 글로벌이라 group_label 없음.
        orders = sorted(r["render_order"] for r in self.payload["rows"])
        self.assertEqual(orders, [0, 1])
        self.assertTrue(all("group_label" not in r for r in self.payload["rows"]))

    def test_card_scaffold_is_markdown_with_slots(self) -> None:
        r = self.payload["rows"][0]
        self.assertIn("{{W1}}", r["card_scaffold"])
        self.assertIn("<callout", r["card_scaffold"])

    def test_deterministic_compact_serialization(self) -> None:
        a = ci.build_routine_handoff_payload_v2(_enriched_rows(), RUN_DATE, 7, GEN_AT)
        b = ci.build_routine_handoff_payload_v2(_enriched_rows(), RUN_DATE, 7, GEN_AT)
        sa = json.dumps(a, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sb = json.dumps(b, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(sa, sb)


def _recall_group_rows() -> list[dict]:
    """동일 ENTRPS/사유/발행일 3품목(병합 대상) — card_id 오름차순 대표 = 3474."""
    base = {
        "source": "MFDS", "date": "2026-06-02", "type_or_class": "recall-quality",
        "firm": "한국제약", "headline": "정제 회수", "signal_tier": "Tier 2",
        "modality": "Chemical", "language": "KO", "raw_fetch_ok": True,
    }
    reason = "함량부적합 자진 회수"
    out = []
    for did, page, prd in (("recall-2026003474", "page-r1", "정제 500mg"),
                           ("recall-2026003475", "page-r2", "정제 325mg"),
                           ("recall-2026003476", "page-r3", "정제 200mg")):
        out.append({**base, "document_id": did, "page_id": page,
                    "raw": {"ENTRPS": "한국제약(주)", "PRDUCT": prd, "RTRVL_RESN": reason}})
    return out


class MergeRecallV2SerializationTest(unittest.TestCase):
    """§14(F) — handoff v2 직렬화: 대표 1카드 + 멤버 merged_into(렌더 제외·Status 유지)."""

    def setUp(self) -> None:
        self.payload = ci.build_routine_handoff_payload_v2(
            _recall_group_rows(), RUN_DATE, 7, GEN_AT)

    def test_all_three_rows_retained(self) -> None:
        # row_count 는 멤버 포함 3건 유지(Status 갱신 목록 보존).
        self.assertEqual(self.payload["row_count"], 3)
        self.assertEqual({r["page_id"] for r in self.payload["rows"]},
                         {"page-r1", "page-r2", "page-r3"})

    def test_representative_has_merged_scaffold(self) -> None:
        rep = next(r for r in self.payload["rows"]
                   if r["card_id"] == "MFDS::recall-2026003474")
        self.assertNotIn("merged_into", rep)
        self.assertIn("card_scaffold", rep)
        self.assertIn("외 2품목", rep["card_scaffold"])
        self.assertIn("<details>", rep["card_scaffold"])
        self.assertEqual(rep["prose_input"]["merged_count"], 3)

    def test_members_marked_and_stripped(self) -> None:
        members = [r for r in self.payload["rows"] if "merged_into" in r]
        self.assertEqual(len(members), 2)
        for m in members:
            self.assertEqual(m["merged_into"], "MFDS::recall-2026003474")
            self.assertNotIn("card_id", m)              # R1-a: 멤버 자체 card_id 제거
            self.assertNotIn("card_scaffold", m)        # 렌더 제외
            self.assertNotIn("prose_input", m)
            self.assertNotIn("needs_llm_slots", m)
            self.assertNotIn("render_order", m)         # R1-d: 멤버 미부여
            self.assertIn("page_id", m)                  # Status 갱신용 보존

    def test_no_raw_leak(self) -> None:
        blob = json.dumps(self.payload, ensure_ascii=False)
        self.assertNotIn("RTRVL_RESN", blob)


class EmitBranchTest(unittest.TestCase):
    def _run_emit(self, flag: str | None):
        captured = {}

        def fake_upsert(token, db_id, payload, generated_at, compact=False):
            captured["payload"] = payload
            captured["compact"] = compact
            return "pid", "url"

        env = {} if flag is None else {"ENABLE_HANDOFF_V2": flag}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ci, "notion_query_new_intake_rows", return_value=_enriched_rows()), \
             mock.patch.object(ci, "enrich_rows_with_raw",
                               side_effect=lambda t, rows, inmemory_raw=None: (rows, {})), \
             mock.patch.object(ci, "notion_upsert_routine_handoff", side_effect=fake_upsert):
            ci.emit_routine_handoff("tok", "db", RUN_DATE, 7, GEN_AT)
        return captured

    def test_flag_off_uses_v1(self) -> None:
        cap = self._run_emit(None)
        self.assertEqual(cap["payload"]["schema_version"], "grm-routine-handoff/v1")
        self.assertFalse(cap["compact"])
        self.assertNotIn("card_scaffold", cap["payload"]["rows"][0])

    def test_flag_on_uses_v2(self) -> None:
        cap = self._run_emit("true")
        self.assertEqual(cap["payload"]["schema_version"], "grm-routine-handoff/v2")
        self.assertTrue(cap["compact"])
        self.assertIn("card_scaffold", cap["payload"]["rows"][0])


class ChildrenChunkTest(unittest.TestCase):
    def test_create_splits_blocks_over_limit(self) -> None:
        fake_blocks = [{"object": "block", "n": i} for i in range(200)]
        create_children = {}
        append_calls = {}

        def fake_api(method, url, token, body=None, **kw):
            if method == "POST":
                create_children["count"] = len(body.get("children", []))
                return {"id": "newpage", "url": "u"}
            return {}

        def fake_append(token, page_id, blocks):
            append_calls["count"] = len(blocks)

        with mock.patch.object(ci, "_handoff_blocks", return_value=fake_blocks), \
             mock.patch.object(ci, "_handoff_page_properties", return_value={}), \
             mock.patch.object(ci, "notion_find_handoff_page", return_value=None), \
             mock.patch.object(ci, "notion_api_request", side_effect=fake_api), \
             mock.patch.object(ci, "notion_append_page_children", side_effect=fake_append):
            ci.notion_upsert_routine_handoff("tok", "db",
                                             {"handoff_id": "h"}, GEN_AT, compact=True)
        self.assertEqual(create_children["count"], 90)   # 생성은 ≤90
        self.assertEqual(append_calls["count"], 110)      # 나머지 append

    def test_create_no_split_when_small(self) -> None:
        fake_blocks = [{"object": "block", "n": i} for i in range(10)]
        create_children = {}

        def fake_api(method, url, token, body=None, **kw):
            if method == "POST":
                create_children["count"] = len(body.get("children", []))
                return {"id": "p", "url": "u"}
            return {}

        with mock.patch.object(ci, "_handoff_blocks", return_value=fake_blocks), \
             mock.patch.object(ci, "_handoff_page_properties", return_value={}), \
             mock.patch.object(ci, "notion_find_handoff_page", return_value=None), \
             mock.patch.object(ci, "notion_api_request", side_effect=fake_api), \
             mock.patch.object(ci, "notion_append_page_children") as append:
            ci.notion_upsert_routine_handoff("tok", "db", {"handoff_id": "h"}, GEN_AT)
        self.assertEqual(create_children["count"], 10)
        append.assert_not_called()  # ≤90 이면 분할 없음(v1 기존 동작 유지)


_V1_ROWS = [
    {"source": "MFDS", "document_id": "admin-1", "date": "2026-05-30",
     "run_date": "2026-06-04", "collected_at": "2026-06-04T03:17:00", "page_id": "p1",
     "signal_tier": "Tier 2", "headline": "행정처분 x", "type_or_class": "admin-action"},
    {"source": "FDA Warning Letter", "document_id": "WL-1", "date": "2026-05-20",
     "run_date": "2026-06-03", "collected_at": "2026-06-03T03:17:00", "page_id": "p2",
     "signal_tier": "Tier 3", "headline": "CGMP y", "type_or_class": "CDER"},
]


class V1FrozenSnapshotTest(unittest.TestCase):
    """플래그 off(v1) 경로 회귀 잠금 — 고정 입력 → payload·블록 구조 바이트 스냅샷."""

    def _v1_payload(self) -> dict:
        return ci.build_routine_handoff_payload(
            [dict(r) for r in _V1_ROWS], RUN_DATE, 7, GEN_AT)

    def test_v1_payload_byte_snapshot(self) -> None:
        serialized = json.dumps(self._v1_payload(), ensure_ascii=False, indent=2)
        path = os.path.join(GOLDEN, "handoff_v1_snapshot.json")
        if _UPDATE:
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(serialized)
            return
        with io.open(path, encoding="utf-8") as f:
            expected = f.read()
        self.assertEqual(serialized, expected)

    def test_v1_blocks_structure_unchanged(self) -> None:
        # 업서트 직렬화 경로 회귀: 헤더 4블록 + code 블록, v1 은 indent=2(compact 아님)
        blocks = ci._handoff_blocks(self._v1_payload())
        types = [b["type"] for b in blocks]
        self.assertEqual(types[:4], ["heading_2", "paragraph", "paragraph", "heading_3"])
        self.assertTrue(all(t == "code" for t in types[4:]))
        self.assertEqual(blocks[0]["heading_2"]["rich_text"][0]["text"]["content"],
                         "GRM Routine Handoff")
        code_text = blocks[4]["code"]["rich_text"][0]["text"]["content"]
        self.assertIn('\n  "', code_text)  # indent 2칸 존재(v1 직렬화)

    def test_v1_has_no_v2_fields(self) -> None:
        payload = self._v1_payload()
        self.assertEqual(payload["schema_version"], "grm-routine-handoff/v1")
        for r in payload["rows"]:
            self.assertNotIn("card_scaffold", r)
            self.assertNotIn("prose_input", r)


def _handoff_page(handoff_id: str, run_date: str, status: str, pid: str) -> dict:
    """OPEN/STALE handoff page 의 Notion query 결과 형태(snapshot 가 읽는 최소 props)."""
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


class StalePriorOpenHandoffTest(unittest.TestCase):
    """B1 (K4-1): 새 OPEN emit 전 직전 OPEN → STALE+Skipped · 개별 row 불변 · OPEN 1개만."""

    def test_prior_open_staled_rows_untouched(self) -> None:
        calls = []

        def fake_api(method, url, token, body=None, **kw):
            calls.append((method, url, body))
            if method == "POST" and "/query" in url:
                return {"results": [_handoff_page(
                    "routine-handoff::2026-06-07", "2026-06-07", "New", "prior-page")],
                    "has_more": False}
            return {}

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api):
            staled = ci.notion_stale_prior_open_handoffs(
                "tok", "db", keep_handoff_id="routine-handoff::2026-06-08",
                superseded_by="2026-06-08")

        self.assertEqual(staled, 1)
        patches = [c for c in calls if c[0] == "PATCH"]
        self.assertEqual(len(patches), 1)                      # 직전 OPEN 1건만 PATCH
        _, purl, pbody = patches[0]
        self.assertIn("prior-page", purl)                      # handoff page 자신
        props = pbody["properties"]
        self.assertEqual(set(props.keys()), {"Name", "Status"})  # 불가침: 두 속성만
        self.assertEqual(props["Status"]["select"]["name"], "Skipped")
        title = props["Name"]["title"][0]["text"]["content"]
        self.assertIn("STALE GRM Routine Handoff 2026-06-07", title)
        self.assertIn("superseded by 2026-06-08", title)

    def test_current_open_not_self_staled(self) -> None:
        # 오늘(keep) handoff 만 OPEN 이면 STALE 0건 — 자기 자신 봉인 금지.
        def fake_api(method, url, token, body=None, **kw):
            if method == "POST" and "/query" in url:
                return {"results": [_handoff_page(
                    "routine-handoff::2026-06-08", "2026-06-08", "New", "today-page")],
                    "has_more": False}
            raise AssertionError("PATCH 호출되면 안 됨(자기 봉인 금지)")

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api):
            staled = ci.notion_stale_prior_open_handoffs(
                "tok", "db", keep_handoff_id="routine-handoff::2026-06-08",
                superseded_by="2026-06-08")
        self.assertEqual(staled, 0)

    def test_multiple_prior_opens_all_staled_single_remains(self) -> None:
        # OPEN 다수(06-06·06-07) + 오늘(06-08) → 직전 2건 STALE, 오늘만 OPEN 유지.
        patched_ids = []

        def fake_api(method, url, token, body=None, **kw):
            if method == "POST" and "/query" in url:
                return {"results": [
                    _handoff_page("routine-handoff::2026-06-06", "2026-06-06", "New", "p6"),
                    _handoff_page("routine-handoff::2026-06-07", "2026-06-07", "New", "p7"),
                    _handoff_page("routine-handoff::2026-06-08", "2026-06-08", "New", "p8"),
                ], "has_more": False}
            if method == "PATCH":
                patched_ids.append(url)
            return {}

        with mock.patch.object(ci, "notion_api_request", side_effect=fake_api):
            staled = ci.notion_stale_prior_open_handoffs(
                "tok", "db", keep_handoff_id="routine-handoff::2026-06-08",
                superseded_by="2026-06-08")
        self.assertEqual(staled, 2)
        self.assertTrue(any("p6" in u for u in patched_ids))
        self.assertTrue(any("p7" in u for u in patched_ids))
        self.assertFalse(any("p8" in u for u in patched_ids))  # 오늘(keep)은 불변

    def test_upsert_invokes_stale_guard_before_write(self) -> None:
        # 와이어링: notion_upsert_routine_handoff 가 emit 전 STALE 가드를 호출한다.
        with mock.patch.object(ci, "notion_stale_prior_open_handoffs", return_value=0) as guard, \
             mock.patch.object(ci, "_handoff_blocks", return_value=[]), \
             mock.patch.object(ci, "_handoff_page_properties", return_value={}), \
             mock.patch.object(ci, "notion_find_handoff_page", return_value=None), \
             mock.patch.object(ci, "notion_api_request",
                               side_effect=lambda *a, **k: {"id": "p", "url": "u"}):
            ci.notion_upsert_routine_handoff(
                "tok", "db",
                {"handoff_id": "routine-handoff::2026-06-08", "run_date_kst": "2026-06-08"},
                GEN_AT)
        guard.assert_called_once()
        self.assertEqual(guard.call_args.kwargs["keep_handoff_id"],
                         "routine-handoff::2026-06-08")
        self.assertEqual(guard.call_args.kwargs["superseded_by"], "2026-06-08")


class CoverageCollectedTest(unittest.TestCase):
    """수집 현황 '수집' 컬럼 결정론 산출(W1) — LLM 재집계 제거용."""

    def test_known_sources_fixed_order_include_zero(self) -> None:
        cov = ci.build_coverage_collected(
            {"MFDS": 30, "Federal Register": 2, "OpenFDA Recall": 1, "FDA Warning Letter": 3})
        labels = [it["label"] for it in cov["items"]]
        # 11종 known 라벨이 프롬프트 callout 순서대로 전부(0건 포함) 나온다.
        self.assertEqual(labels, ["FR", "Recall", "EMA", "MHRA", "PIC/S", "ECA",
                                  "FDA WL", "MFDS", "ICH", "WHO", "HC"])
        self.assertEqual(cov["total"], 36)
        self.assertTrue(cov["md"].startswith("Intake row 36건 (FR 2 · Recall 1 · EMA 0 · "))
        self.assertIn("MFDS 30", cov["md"])

    def test_unknown_source_appended_only_when_nonzero(self) -> None:
        cov = ci.build_coverage_collected({"FDA 483": 4, "MFDS": 1})
        labels = [it["label"] for it in cov["items"]]
        self.assertEqual(labels[-1], "FDA 483")        # 미정의 소스는 끝에 덧붙임(조용한 유실 금지)
        self.assertEqual(cov["total"], 5)
        # count 0 인 미정의 소스는 생략(클러터 방지)
        cov0 = ci.build_coverage_collected({"FDA 483": 0, "MFDS": 1})
        self.assertNotIn("FDA 483", [it["label"] for it in cov0["items"]])

    def test_empty_counts(self) -> None:
        cov = ci.build_coverage_collected({})
        self.assertEqual(cov["total"], 0)
        self.assertEqual(cov["md"], "Intake row 0건 (FR 0 · Recall 0 · EMA 0 · MHRA 0 · "
                         "PIC/S 0 · ECA 0 · FDA WL 0 · MFDS 0 · ICH 0 · WHO 0 · HC 0)")

    def test_source_counts_from_rows_matches_payload(self) -> None:
        # 발행 후 탐지가 rows 로 재집계한 값 == build_payload 의 source_counts(동일 산식).
        payload = ci.build_routine_handoff_payload_v2(_recall_group_rows(), RUN_DATE, 7, GEN_AT)
        recomputed = ci.coverage_source_counts(payload["rows"])
        self.assertEqual(recomputed, payload["source_counts"])
        self.assertEqual(recomputed["MFDS"], 3)        # 병합 멤버 포함 전수

    def test_coverage_collected_md_in_v2_payload(self) -> None:
        payload = ci.build_routine_handoff_payload_v2(_enriched_rows(), RUN_DATE, 7, GEN_AT)
        self.assertIn("coverage_collected_md", payload)
        # 정본(rows 재집계) 과 동일 문자열을 싣는다.
        expect = ci.build_coverage_collected(ci.coverage_source_counts(payload["rows"]))["md"]
        self.assertEqual(payload["coverage_collected_md"], expect)
        self.assertTrue(payload["coverage_collected_md"].startswith("Intake row 2건 ("))

    def test_v1_payload_has_no_coverage_field(self) -> None:
        # v1 은 바이트 golden 동결 — 신규 필드 미추가(회귀 금지).
        payload = ci.build_routine_handoff_payload(
            [dict(r) for r in _V1_ROWS], RUN_DATE, 7, GEN_AT)
        self.assertNotIn("coverage_collected_md", payload)


class WeekdayKstTest(unittest.TestCase):
    """발행 요일 결정론 산출(D-1 근본수정) — LLM 산술 제거용."""

    def test_known_anchors(self) -> None:
        # 06-17 dry-run 앵커: 15=월, 16=화, 17=수(LLM 이 17 을 '화'로 오산했던 날).
        self.assertEqual(ci.weekday_kst(date(2026, 6, 15)), "월요일")
        self.assertEqual(ci.weekday_kst(date(2026, 6, 16)), "화요일")
        self.assertEqual(ci.weekday_kst(date(2026, 6, 17)), "수요일")

    def test_sunday_boundary(self) -> None:
        # 2026-06-21 = 일요일(주 경계).
        self.assertEqual(ci.weekday_kst(date(2026, 6, 21)), "일요일")


if __name__ == "__main__":
    unittest.main()
