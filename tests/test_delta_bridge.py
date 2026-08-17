"""delta_bridge — 클라우드 델타 브릿지(Fix A, 설계 §2 A-3/A-4) 단위 테스트.

Notion API 를 fake_api(method, url, token, body=None, **kw) 라우터로 mock 한다
(tests/test_handoff_idempotency.py·test_web_brief_emit.py 의 `notion_api_request`
side_effect 패턴 재사용). 커버리지(A-4):
  - 정상: OPEN web-delta 페이지(유효 델타) → 델타 파일 내용·경로·wrote=true·CONSUMED 호출.
  - deep 포함: deep_{date}.json 동반 생성.
  - 멱등: 동일 내용 재실행 → wrote=false·중복 커밋 없음(파일 미변경).
  - 중복 충돌: 같은 날짜·다른 내용 → exit 1(가드, DeltaBridgeError).
  - OPEN 0건: 클린 skip(exit 0·파일 미생성).
  - 구조 불량 델타(cards 없음 등): fail-loud(DeltaBridgeError).
  - 최신 선택: OPEN 2건(다른 날짜) → 최신 1건 선택.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import delta_bridge as db  # noqa: E402
import grm_handoff as gh  # noqa: E402


def _code_block(payload: dict) -> dict:
    text = json.dumps(payload, ensure_ascii=False)
    return {
        "type": "code",
        "code": {"language": "json", "rich_text": [{"plain_text": text}]},
    }


def _j(payload: dict) -> str:
    """extract_delta 입력용 코드블록 원문(JSON 문자열) — _fetch_code_blocks 반환형과 동형."""
    return json.dumps(payload, ensure_ascii=False)


def _delta_page(pid: str, date_str: str, *, status: str = "New",
                 title_prefix: str = db.TITLE_PREFIX_OPEN,
                 type_class: str = db.TYPE_WEB_DELTA,
                 last_edited: str = "2026-07-01T00:00:00.000Z") -> dict:
    return {
        "id": pid, "url": f"https://app.notion.com/p/{pid}",
        "last_edited_time": last_edited,
        "properties": {
            "Name": {"title": [{"plain_text": f"{title_prefix}{date_str}"}]},
            "Type or Class": {"select": {"name": type_class}},
            "Status": {"select": {"name": status}},
        },
    }


def _valid_delta(publish_date: str | None = None) -> dict:
    d = {
        "cards": {"mfds-1": {"title_issue": "제목", "summary": "요약",
                              "key_facts": ["사실1"], "implication": "시사점",
                              "checks": ["점검1", "점검2"]}},
        "tldr": ["가", "나", "다"],
    }
    if publish_date:
        d["publish_date"] = publish_date
    return d


class _FakeNotion:
    """Notion API 라우터 — DB query(POST)/block children(GET)/PATCH 를 흉내.

    `pages`: {page_id: page dict}. `blocks`: {page_id: [code_block, ...]}.
    쿼리(POST .../query)는 filter 를 그대로 신뢰하지 않고, 이 fake 가 이미 필터링된
    `query_results` 리스트를 그대로 반환한다(실제 필터 파싱은 검증 범위 밖 — 다른
    grm_notion 테스트가 이미 커버).
    """

    def __init__(self, query_results: list[dict], blocks: dict[str, list[dict]] | None = None):
        self.query_results = query_results
        self.blocks = blocks or {}
        self.patches: list[tuple[str, dict]] = []

    def __call__(self, method, url, token, body=None, **kw):
        if method == "POST" and url.endswith("/query"):
            return {"results": self.query_results, "has_more": False}
        if method == "GET" and "/children" in url:
            page_id = url.split("/blocks/")[1].split("/children")[0]
            return {"results": self.blocks.get(page_id, []), "has_more": False}
        if method == "PATCH":
            self.patches.append((url, body))
            return {"id": "patched"}
        raise AssertionError(f"unexpected call: {method} {url}")


class _TypeAwareNotion(_FakeNotion):
    """`Type or Class` 필터를 **실제로 적용**하는 라우터.

    기본 `_FakeNotion` 은 어떤 query 에도 같은 리스트를 돌려준다. 별도 deep 페이지 배선은
    "web-delta 조회"와 "web-deep-delta 조회" 두 번을 하므로, 필터를 무시하는 fake 로는
    delta 페이지가 deep 조회 결과로 되돌아와 실제와 다른 상황을 만든다.
    """

    def __call__(self, method, url, token, body=None, **kw):
        if method == "POST" and url.endswith("/query"):
            want = None
            for cond in ((body or {}).get("filter", {}).get("and") or []):
                if cond.get("property") == db.PROP_TYPE_CLASS:
                    want = (cond.get("select") or {}).get("equals")
            results = [p for p in self.query_results
                       if want is None
                       or (p["properties"]["Type or Class"]["select"]["name"] == want)]
            return {"results": results, "has_more": False}
        return super().__call__(method, url, token, body=body, **kw)


def _deep_page(pid: str, date_str: str, **kw) -> dict:
    return _delta_page(pid, date_str, title_prefix=db.TITLE_PREFIX_OPEN_DEEP,
                        type_class=db.TYPE_WEB_DEEP_DELTA, **kw)


def _handoff_payload(bodies: dict[str, str], *, run_date: str = "2026-07-13") -> dict:
    """handoff v2 payload(실 producer 스키마의 최소 형태) — deep 대상 row + 비대상 row 혼재."""
    rows: list[dict] = [{"web_card_id": "plain-1", "section": "news"}]  # deep 비대상
    for card_id, body in bodies.items():
        rows.append({
            "web_card_id": card_id,
            "card_id": f"MFDS::{card_id}",
            "deep_analysis_ready": True,
            "deep_analysis_input": {"body_full": body},
            "kind": "admin-action",
        })
    return {"schema_version": gh.HANDOFF_SCHEMA_VERSION_V2,
            "handoff_id": gh.handoff_id_for(run_date),
            "run_date_kst": run_date, "row_count": len(rows), "rows": rows}


def _chunked_code_blocks(payload: dict, size: int = 1900) -> list[dict]:
    """payload JSON 을 `_handoff_blocks` 와 동형으로 1,900자 code 블록에 쪼개 담는다.

    handoff payload 는 실측 175~230 블록으로 쪼개져 실린다 — 블록 단위로 파싱하면 전량
    실패하므로, 결합해서 읽는지를 이 픽스처가 강제한다.
    """
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return [_code_block_text(text[i:i + size]) for i in range(0, len(text), size)]


def _code_block_text(text: str) -> dict:
    return {"type": "code", "code": {"language": "json", "rich_text": [{"plain_text": text}]}}


def _handoff_page(pid: str = "h1") -> dict:
    return {"id": pid, "url": f"https://app.notion.com/p/{pid}",
            "last_edited_time": "2026-07-13T00:00:00.000Z",
            "properties": {"Name": {"title": [{"plain_text": "CONSUMED GRM Routine Handoff"}]}}}


class _HandoffNotion(_TypeAwareNotion):
    """handoff 페이지 조회(`Document ID` rich_text equals)까지 흉내내는 라우터.

    handoff 는 web-delta/web-deep-delta 와 **필터 모양이 다르다**(select 가 아니라 rich_text).
    필터를 무시하는 fake 로는 delta 페이지가 handoff 로 되돌아와 실제와 다른 상황이 된다.
    `handoff_queries` 로 **어떤 handoff_id 를 물었는지** 검사한다 — id 산식이 어긋나면 조회가
    조용히 0건이 되므로, 그 산식 자체를 테스트가 고정해야 한다.
    """

    def __init__(self, query_results: list[dict], blocks: dict[str, list[dict]] | None = None,
                 handoff_pages: dict[str, dict] | None = None):
        super().__init__(query_results, blocks)
        self.handoff_pages = handoff_pages or {}
        self.handoff_queries: list[str] = []

    def __call__(self, method, url, token, body=None, **kw):
        flt = (body or {}).get("filter") or {}
        if method == "POST" and url.endswith("/query") and flt.get("property") == gh.PROP_DOC_ID:
            handoff_id = (flt.get("rich_text") or {}).get("equals")
            self.handoff_queries.append(handoff_id)
            page = self.handoff_pages.get(handoff_id)
            return {"results": [page] if page else [], "has_more": False}
        return super().__call__(method, url, token, body=body, **kw)


# 2026-08-17 실측 드리프트의 최소 재현: Routine 이 예치한 source_text 에서 **말미 블록이 통째로
# 빠졌고**, 하필 그 안에 심층분석이 인용한 조항이 있었다(실측 6/6 카드가 51~241자씩 누락).
_BODY_FULL_ADMIN = "기준서 미준수. 적용법령: 약사법 제38조제1항. [별표8] 개별기준."
_DRIFTED_SOURCE = "기준서 미준수."
_GROUNDED_DA = {
    "key_violations": [{
        "citation": "약사법 제38조제1항",
        "original": "기준서 미준수",
        "description": "제조·품질관리기준서를 준수하지 않았다는 지적이 확인됨.",
        "risk": "제품 품질 일관성 저하 위험이 있다.",
    }],
    "disposition_basis": "기준서 미준수를 사유로 제조업무정지 1개월이 부과됐다([별표8] 근거).",
    "required_remediation": {"deadline": "제조업무정지 기간 이행",
                              "items": ["기준서와 실제 작업기록 일치 여부 점검"]},
    "administrative_risks": "재위반 시 가중처분으로 이어질 수 있는 리스크가 있다.",
}


_DEEP_PAYLOAD = {"mfds-1": {"deep_analysis": {"a": 1}, "source_text": "원문"}}


class SelectOpenDeepDeltaTest(unittest.TestCase):
    """[2026-08-03] 별도 web-deep-delta 페이지 조회 배선 — 예치 규약이 허용한 경로."""

    def test_requires_publish_date(self) -> None:
        fake = _TypeAwareNotion([])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            with self.assertRaises(db.DeltaBridgeError):
                db.select_open_deep_delta("tok", "db", "")

    def test_exact_date_match_only(self) -> None:
        # 지난 주 잔류 OPEN deep 페이지가 이번 주에 짝지어지면 카드 id 가 전부 빗나간다.
        stale = _deep_page("p-old", "2026-07-06")
        fake = _TypeAwareNotion([stale])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            self.assertIsNone(db.select_open_deep_delta("tok", "db", "2026-07-13"))
            self.assertEqual(db.select_open_deep_delta("tok", "db", "2026-07-06")["id"], "p-old")

    def test_delta_page_is_not_returned_as_deep(self) -> None:
        fake = _TypeAwareNotion([_delta_page("p1", "2026-07-13")])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            self.assertIsNone(db.select_open_deep_delta("tok", "db", "2026-07-13"))


class DeepKeyNamespaceTest(unittest.TestCase):
    def test_prefixed_deep_keys_normalized(self) -> None:
        deep = {"MFDS::mfds-1": {"deep_analysis": {}}}
        self.assertEqual(db.normalize_deep_key_namespace(deep), 1)
        self.assertIn("mfds-1", deep)

    def test_collision_aborts_normalization(self) -> None:
        deep = {"MFDS::x": {}, "x": {}}
        self.assertEqual(db.normalize_deep_key_namespace(deep), 0)
        self.assertIn("MFDS::x", deep)  # 무손실 아니면 손대지 않는다


class SelectOpenDeltaTest(unittest.TestCase):
    def test_no_open_returns_none(self) -> None:
        fake = _FakeNotion([])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            page = db.select_open_delta("tok", "db")
        self.assertIsNone(page)

    def test_latest_selected_among_two_dates(self) -> None:
        older = _delta_page("p-old", "2026-07-06")
        newer = _delta_page("p-new", "2026-07-13")
        fake = _FakeNotion([older, newer])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            page = db.select_open_delta("tok", "db")
        self.assertEqual(page["id"], "p-new")

    def test_publish_date_pins_selection(self) -> None:
        older = _delta_page("p-old", "2026-07-06")
        newer = _delta_page("p-new", "2026-07-13")
        fake = _FakeNotion([older, newer])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            page = db.select_open_delta("tok", "db", publish_date="2026-07-06")
        self.assertEqual(page["id"], "p-old")

    def test_publish_date_no_match_returns_none(self) -> None:
        older = _delta_page("p-old", "2026-07-06")
        fake = _FakeNotion([older])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            page = db.select_open_delta("tok", "db", publish_date="2026-07-20")
        self.assertIsNone(page)


class ExtractDeltaTest(unittest.TestCase):
    def test_normal_delta_extracted(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        page["_code_blocks"] = [_j(_valid_delta())]
        delta, deep, date_str = db.extract_delta(page)
        self.assertEqual(date_str, "2026-07-13")
        self.assertIsNone(deep)
        self.assertEqual(delta["tldr"], ["가", "나", "다"])

    def test_deep_delta_extracted_as_second_block(self) -> None:
        """deep 델타 = 맨몸 {document_id: {...}} — assemble --deep 소비 계약."""
        page = _delta_page("p1", "2026-07-13")
        deep_payload = {"mfds-1": {"deep_analysis": {"x": "y"}, "source_text": "원문"}}
        page["_code_blocks"] = [_j(_valid_delta()), _j(deep_payload)]
        delta, deep, date_str = db.extract_delta(page)
        self.assertIsNotNone(deep)
        self.assertEqual(deep["mfds-1"]["deep_analysis"], {"x": "y"})

    def test_deep_envelope_wrapped_rejected(self) -> None:
        """cards/tldr 봉투로 감싼 deep 는 거부 — 조용한 deep 유실(card id 매칭 실패) 차단."""
        page = _delta_page("p1", "2026-07-13")
        wrapped = {"cards": {"mfds-1": {"deep_analysis": {"x": "y"}}}, "tldr": []}
        page["_code_blocks"] = [_j(_valid_delta()), _j(wrapped)]
        with self.assertRaises(db.DeltaBridgeError):
            db.extract_delta(page)

    def test_multiblock_split_delta_joined(self) -> None:
        """Notion 이 긴 델타를 여러 code 블록으로 쪼갠 경우 — 결합 파싱(B) 으로 복원."""
        page = _delta_page("p1", "2026-07-13")
        text = _j(_valid_delta())
        third = max(1, len(text) // 3)
        page["_code_blocks"] = [text[:third], text[third:2 * third], text[2 * third:]]
        delta, deep, date_str = db.extract_delta(page)
        self.assertEqual(date_str, "2026-07-13")
        self.assertIn("mfds-1", delta["cards"])
        self.assertIsNone(deep)

    def test_multiblock_split_delta_plus_deep_tail(self) -> None:
        """쪼개진 델타 + 마지막 블록 deep(C) — deep 은 맨몸 dict."""
        page = _delta_page("p1", "2026-07-13")
        text = _j(_valid_delta())
        half = len(text) // 2
        deep_payload = {"mfds-1": {"deep_analysis": {"x": "y"}}}
        page["_code_blocks"] = [text[:half], text[half:], _j(deep_payload)]
        delta, deep, _date = db.extract_delta(page)
        self.assertIn("mfds-1", delta["cards"])
        self.assertEqual(deep["mfds-1"]["deep_analysis"], {"x": "y"})

    def test_deep_gate_keeps_grounded_drops_ungrounded(self) -> None:
        """[클라우드화] _gate_deep_analysis: 근거 있는 deep 은 통과, 미근거 인용은 drop."""
        good = {"admin-1": {
            "deep_analysis": {
                "key_violations": [{
                    "citation": "약사법 제38조제1항",
                    "original": "기준서 미준수",
                    "description": "제조·품질관리기준서를 준수하지 않았다는 지적이 확인됨.",
                    "risk": "제품 품질 일관성 저하 위험이 있다.",
                }],
                "disposition_basis": "기준서 미준수를 사유로 제조업무정지 1개월이 부과됐다([별표8] 근거).",
                "required_remediation": {"deadline": "제조업무정지 기간 이행",
                                          "items": ["기준서와 실제 작업기록 일치 여부 점검"]},
                "administrative_risks": "재위반 시 가중처분으로 이어질 수 있는 리스크가 있다.",
            },
            "source_text": "기준서 미준수. 적용법령: 약사법 제38조제1항. [별표8] 개별기준.",
        }}
        kept = db._gate_deep_analysis(good)
        self.assertIn("admin-1", kept)  # 근거 있는 카드는 유지

        bad = {"admin-2": dict(good["admin-1"])}
        bad["admin-2"]["deep_analysis"] = dict(good["admin-1"]["deep_analysis"])
        bad["admin-2"]["deep_analysis"]["key_violations"] = [{
            "citation": "「화장품법」 제999조",  # source_text 에 없음 — D2 하드 FAIL
            "original": "x", "description": "a" * 24, "risk": "b" * 24}]
        self.assertIsNone(db._gate_deep_analysis(bad))  # 전건 drop → None

    def test_garbage_blocks_fail_loud(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        page["_code_blocks"] = ["not json at all", "{broken"]
        with self.assertRaises(db.DeltaBridgeError):
            db.extract_delta(page)

    def test_publish_date_from_body_overrides_title(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        page["_code_blocks"] = [_j(_valid_delta(publish_date="2026-07-14"))]
        _delta, _deep, date_str = db.extract_delta(page)
        self.assertEqual(date_str, "2026-07-14")

    def test_missing_cards_fails_loud(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        page["_code_blocks"] = [_j({"tldr": []})]  # cards 없음
        with self.assertRaises(db.DeltaBridgeError):
            db.extract_delta(page)

    def test_cards_wrong_type_fails_loud(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        page["_code_blocks"] = [_j({"cards": ["not", "a", "dict"], "tldr": []})]
        with self.assertRaises(db.DeltaBridgeError):
            db.extract_delta(page)

    def test_no_code_blocks_fails_loud(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        page["_code_blocks"] = []
        with self.assertRaises(db.DeltaBridgeError):
            db.extract_delta(page)

    def test_bad_publish_date_format_fails_loud(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        page["_code_blocks"] = [_j(_valid_delta(publish_date="07/13/2026"))]
        with self.assertRaises(db.DeltaBridgeError):
            db.extract_delta(page)


class WriteDeltaTest(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_writes_new_delta_file(self) -> None:
        delta = _valid_delta()
        wrote = db.write_delta(delta, None, "2026-07-13")
        self.assertTrue(wrote)
        path = pathlib.Path("web/data/deltas/delta_2026_07_13.json")
        self.assertTrue(path.exists())
        raw = path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r", raw)
        self.assertEqual(json.loads(raw.decode("utf-8")), delta)

    def test_writes_deep_delta_when_present(self) -> None:
        delta = _valid_delta()
        deep = {"mfds-1": {"deep_analysis": {"a": "b"}, "source_text": "원문"}}
        wrote = db.write_delta(delta, deep, "2026-07-13")
        self.assertTrue(wrote)
        self.assertTrue(pathlib.Path("web/data/deltas/delta_2026_07_13.json").exists())
        self.assertTrue(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())

    def test_idempotent_same_content_noop(self) -> None:
        delta = _valid_delta()
        first = db.write_delta(delta, None, "2026-07-13")
        second = db.write_delta(delta, None, "2026-07-13")
        self.assertTrue(first)
        self.assertFalse(second)

    def test_duplicate_conflicting_content_raises(self) -> None:
        delta = _valid_delta()
        db.write_delta(delta, None, "2026-07-13")
        other = _valid_delta()
        other["tldr"] = ["다른", "내용", "입니다"]
        with self.assertRaises(db.DeltaBridgeError):
            db.write_delta(other, None, "2026-07-13")

    def test_serialization_matches_fixture_style(self) -> None:
        """indent=1·ensure_ascii=False·후행개행 — tests/fixtures/delta_2026_07_06.json 관례."""
        delta = _valid_delta()
        db.write_delta(delta, None, "2026-07-13")
        raw = pathlib.Path("web/data/deltas/delta_2026_07_13.json").read_text(encoding="utf-8")
        expected = json.dumps(delta, ensure_ascii=False, indent=1) + "\n"
        self.assertEqual(raw, expected)


class ConsumeDeltaTest(unittest.TestCase):
    def test_consume_sets_processed_and_renames(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        fake = _FakeNotion([])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            db.consume_delta("tok", page)
        self.assertEqual(len(fake.patches), 1)
        url, body = fake.patches[0]
        self.assertIn("p1", url)
        self.assertEqual(body["properties"]["Status"]["select"]["name"], "Processed")
        title = body["properties"]["Name"]["title"][0]["text"]["content"]
        self.assertTrue(title.startswith(db.TITLE_PREFIX_CONSUMED))
        self.assertIn("2026-07-13", title)

    def test_consume_deep_page_title(self) -> None:
        page = _delta_page("p1", "2026-07-13", title_prefix=db.TITLE_PREFIX_OPEN_DEEP,
                            type_class=db.TYPE_WEB_DEEP_DELTA)
        fake = _FakeNotion([])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            db.consume_delta("tok", page)
        _url, body = fake.patches[0]
        title = body["properties"]["Name"]["title"][0]["text"]["content"]
        self.assertTrue(title.startswith(db.TITLE_PREFIX_CONSUMED))


class MainIntegrationTest(unittest.TestCase):
    """main() end-to-end — GITHUB_OUTPUT 방출 + no-OPEN 클린 skip 포함."""

    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self._out_path = os.path.join(self._tmp.name, "gh_output.txt")
        self._env_patch = mock.patch.dict(
            os.environ, {"NOTION_TOKEN": "tok", "GITHUB_OUTPUT": self._out_path})
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _read_outputs(self) -> dict:
        if not os.path.exists(self._out_path):
            return {}
        out = {}
        with open(self._out_path, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
        return out

    def test_no_open_clean_skip(self) -> None:
        fake = _FakeNotion([])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read_outputs().get("wrote"), "false")
        self.assertFalse(os.path.exists("web/data/deltas"))

    def test_normal_run_writes_and_outputs(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        fake = _FakeNotion([page], blocks={"p1": [_code_block(_valid_delta())]})
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid"])
        self.assertEqual(rc, 0)
        outputs = self._read_outputs()
        self.assertEqual(outputs.get("wrote"), "true")
        self.assertEqual(outputs.get("date"), "2026-07-13")
        self.assertTrue(pathlib.Path("web/data/deltas/delta_2026_07_13.json").exists())

    # ── [2026-08-03] 별도 web-deep-delta 페이지 배선 ────────────────────────────
    def test_deep_from_separate_page_is_written(self) -> None:
        """예치 규약이 허용한 별도 페이지 경로 — 이게 없어서 08-03 deep 이 침묵 유실됐다."""
        fake = _TypeAwareNotion(
            [_delta_page("p1", "2026-07-13"), _deep_page("p2", "2026-07-13")],
            blocks={"p1": [_code_block(_valid_delta())],
                    "p2": [_code_block(_DEEP_PAYLOAD)]})
        with mock.patch.object(db, "notion_api_request", side_effect=fake), \
             mock.patch.object(db, "_gate_deep_analysis", side_effect=lambda d: d):
            rc = db.main(["--db", "dbid"])
        self.assertEqual(rc, 0)
        self.assertTrue(pathlib.Path("web/data/deltas/delta_2026_07_13.json").exists())
        self.assertTrue(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())

    def test_broken_deep_page_does_not_block_delta(self) -> None:
        """deep 은 **선택 계층** — 그 실패가 주간 발행(=delta)을 인질로 잡으면 안 된다."""
        fake = _TypeAwareNotion(
            [_delta_page("p1", "2026-07-13"), _deep_page("p2", "2026-07-13")],
            blocks={"p1": [_code_block(_valid_delta())],
                    "p2": [_code_block({"cards": {}, "tldr": []})]})  # deep 에 봉투 = 불량
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid"])
        self.assertEqual(rc, 0)  # delta 는 정상 기록
        self.assertTrue(pathlib.Path("web/data/deltas/delta_2026_07_13.json").exists())
        self.assertFalse(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())

    def test_stale_deep_page_not_paired_with_other_week(self) -> None:
        fake = _TypeAwareNotion(
            [_delta_page("p1", "2026-07-13"), _deep_page("p-old", "2026-07-06")],
            blocks={"p1": [_code_block(_valid_delta())],
                    "p-old": [_code_block(_DEEP_PAYLOAD)]})
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid"])
        self.assertEqual(rc, 0)
        self.assertFalse(pathlib.Path("web/data/deltas/deep_2026_07_06.json").exists())
        self.assertFalse(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())

    def test_deep_only_recovery_requires_existing_delta(self) -> None:
        """delta 없는 deep = 고아. 짝이 있는지부터 묻는다."""
        fake = _TypeAwareNotion([_deep_page("p2", "2026-07-13")],
                                 blocks={"p2": [_code_block(_DEEP_PAYLOAD)]})
        with mock.patch.object(db, "notion_api_request", side_effect=fake), \
             mock.patch.object(db, "_gate_deep_analysis", side_effect=lambda d: d), \
             redirect_stderr(io.StringIO()):
            rc = db.main(["--db", "dbid", "--publish-date", "2026-07-13"])
        self.assertEqual(rc, 1)
        self.assertFalse(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())

    def test_deep_only_recovery_backfills_when_delta_exists(self) -> None:
        """08-03 실제 상황 — delta 는 이미 커밋·CONSUMED, deep 만 OPEN 으로 남은 주."""
        os.makedirs("web/data/deltas", exist_ok=True)
        pathlib.Path("web/data/deltas/delta_2026_07_13.json").write_text("{}", encoding="utf-8")
        fake = _TypeAwareNotion([_deep_page("p2", "2026-07-13")],
                                 blocks={"p2": [_code_block(_DEEP_PAYLOAD)]})
        with mock.patch.object(db, "notion_api_request", side_effect=fake), \
             mock.patch.object(db, "_gate_deep_analysis", side_effect=lambda d: d):
            rc = db.main(["--db", "dbid", "--publish-date", "2026-07-13"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read_outputs().get("wrote"), "true")
        self.assertTrue(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())

    def test_deep_only_recovery_never_fires_without_publish_date(self) -> None:
        """스케줄 크론(날짜 미지정)에서는 이 경로가 절대 열리지 않는다."""
        os.makedirs("web/data/deltas", exist_ok=True)
        pathlib.Path("web/data/deltas/delta_2026_07_13.json").write_text("{}", encoding="utf-8")
        fake = _TypeAwareNotion([_deep_page("p2", "2026-07-13")],
                                 blocks={"p2": [_code_block(_DEEP_PAYLOAD)]})
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read_outputs().get("wrote"), "false")
        self.assertFalse(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())

    def test_consume_also_consumes_deep_page_when_persisted(self) -> None:
        os.makedirs("web/data/deltas", exist_ok=True)
        pathlib.Path("web/data/deltas/deep_2026_07_13.json").write_text("{}", encoding="utf-8")
        fake = _TypeAwareNotion([_delta_page("p1", "2026-07-13"),
                                  _deep_page("p2", "2026-07-13")])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid", "--consume"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.patches), 2)  # delta + deep 둘 다 CONSUMED

    def test_consume_leaves_deep_page_open_when_not_persisted(self) -> None:
        """쓰지도 못한 deep 페이지를 닫으면 다시 주울 기회까지 사라진다(침묵 유실 재생산)."""
        fake = _TypeAwareNotion([_delta_page("p1", "2026-07-13"),
                                  _deep_page("p2", "2026-07-13")])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid", "--consume"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.patches), 1)  # delta 만 CONSUMED

    def test_malformed_delta_fails_loud(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        fake = _FakeNotion([page], blocks={"p1": [_code_block({"tldr": []})]})
        with mock.patch.object(db, "notion_api_request", side_effect=fake), \
             redirect_stderr(io.StringIO()):
            rc = db.main(["--db", "dbid"])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists("web/data/deltas"))

    def test_consume_flag_marks_processed_only(self) -> None:
        page = _delta_page("p1", "2026-07-13")
        fake = _FakeNotion([page])
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid", "--consume"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.patches), 1)
        self.assertFalse(os.path.exists("web/data/deltas"))


class NormalizeCardKeyNamespaceTest(unittest.TestCase):
    """[2026-07-27] `Source::document_id` 키 회귀 자동 정규화.

    2026-07-13·07-27 두 번, Routine 이 handoff 의 `card_id`(=`source::document_id`)를 델타
    키로 예치해 발행이 전건 거부됐다(07-27 = 103장). 두 번 다 사람이 **완전히 동일한**
    순수 rename 패치를 손으로 만들어 수습했다. `::` 는 정상 카드 id 에 없으므로(전 발행본+
    스캐폴드 371개 중 0개) 접두사 유무만으로 회귀를 확정할 수 있고 변환은 무손실이다.
    """

    def _delta(self, cards):
        return {"publish_date": "2026-07-27", "tldr": ["a", "b", "c"], "cards": cards}

    def test_prefixed_keys_are_normalized(self):
        d = self._delta({"FDA 483::fda483-193813": {"summary": "s"},
                         "MFDS::admin-2026002715": {"summary": "t"}})
        n = db.normalize_card_key_namespace(d)
        self.assertEqual(n, 2)
        self.assertEqual(set(d["cards"]), {"fda483-193813", "admin-2026002715"})

    def test_clean_delta_untouched(self):
        d = self._delta({"fda483-193813": {"summary": "s"}})
        before = dict(d["cards"])
        self.assertEqual(db.normalize_card_key_namespace(d), 0)
        self.assertEqual(d["cards"], before)

    def test_slash_and_space_ids_are_not_touched(self):
        """MHRA NCR id 처럼 공백·슬래시가 있어도 `::` 가 없으면 정상 id 다."""
        d = self._delta({"Insp GMP 52165/19076958-0001": {"summary": "s"}})
        self.assertEqual(db.normalize_card_key_namespace(d), 0)
        self.assertIn("Insp GMP 52165/19076958-0001", d["cards"])

    def test_deep_delta_keys_follow(self):
        """deep 델타를 같이 안 고치면 심층분석이 조용히 유실된다."""
        d = self._delta({"FDA 483::fda483-193813": {"summary": "s"}})
        deep = {"FDA 483::fda483-193813": {"deep_analysis": {}, "source_text": "x"}}
        db.normalize_card_key_namespace(d, deep)
        self.assertEqual(set(deep), {"fda483-193813"})

    def test_collision_aborts_normalization(self):
        """접두사 제거 시 충돌하면 **아무것도 고치지 않는다**(무손실 보장 안 되면 포기)."""
        d = self._delta({"FDA 483::x1": {"summary": "a"}, "MFDS::x1": {"summary": "b"}})
        self.assertEqual(db.normalize_card_key_namespace(d), 0)
        self.assertEqual(set(d["cards"]), {"FDA 483::x1", "MFDS::x1"})

    def test_collision_with_existing_bare_key_aborts(self):
        d = self._delta({"FDA 483::x1": {"summary": "a"}, "x1": {"summary": "b"}})
        self.assertEqual(db.normalize_card_key_namespace(d), 0)


class HandoffWebCardIdTest(unittest.TestCase):
    """[2026-07-27] handoff row 가 모호하지 않은 `web_card_id`(=bare document_id)를 노출한다.

    `card_id` 는 `source::document_id` 라 델타 키로 쓰면 발행이 거부된다. 이름만으로
    구분되게 같은 값을 별도 필드로 한 번 더 낸다(프롬프트가 이 필드명을 그대로 지시).
    """

    def test_web_card_id_is_bare_document_id(self):
        import collect_intake as ci
        from datetime import date, datetime
        rows = [{
            "source": "FDA Warning Letter", "document_id": "WL-CMS-660124",
            "date": "2026-05-20", "type_or_class": "CDER", "firm": "Acme",
            "headline": "CGMP", "page_id": "p1", "signal_tier": "Tier 3",
            "modality": "Chemical", "language": "", "raw_fetch_ok": True,
            "raw": {"firm": "Acme", "subject": "CGMP"},
        }]
        payload = ci.build_routine_handoff_payload_v2(
            rows, date(2026, 6, 5), 7, datetime(2026, 6, 5, 3, 17))
        row = payload["rows"][0]
        self.assertEqual(row["web_card_id"], "WL-CMS-660124")
        # 두 필드가 나란히 있고 **서로 다르다** — 이름으로 구분해야 한다는 사실 자체를 고정.
        self.assertEqual(row["card_id"], "FDA Warning Letter::WL-CMS-660124")
        self.assertNotEqual(row["web_card_id"], row["card_id"])


# ── [2026-08-17] deep `source_text` 정본 = handoff `deep_analysis_input.body_full` ──────────
#
# 종전 계약은 클라우드 Routine 이 카드 원문(483 은 12~14k자)을 델타에 **옮겨 적게** 했다.
# 그 문자열은 장식이 아니라 근거 대조의 기준선이라(D2/D4/D5b + 조립 시점 결정론 재추출),
# 옮겨 적다 흘린 만큼이 그대로 "원문에 없음"이 된다. 2026-08-17 실측에서 예치된 6건이
# **6건 전부** body_full 과 달랐다. 무인 실행에는 대조할 사람이 없으므로 전사 경로를 없앤다.


class ExtractHandoffBodyFullTest(unittest.TestCase):
    """payload → {web_card_id: body_full} 순수 추출."""

    def test_deep_rows_extracted_by_web_card_id(self) -> None:
        bodies = db.extract_handoff_body_full(_handoff_payload({"admin-1": "원문A", "x-2": "원문B"}))
        self.assertEqual(bodies, {"admin-1": "원문A", "x-2": "원문B"})

    def test_non_deep_rows_ignored(self) -> None:
        """deep 비대상 row(`plain-1`)는 body_full 이 없다 — 섞여 들어오면 안 된다."""
        self.assertNotIn("plain-1", db.extract_handoff_body_full(_handoff_payload({"a": "b"})))

    def test_v1_payload_yields_empty(self) -> None:
        """v1 payload·심층분석 없는 주는 그 키가 아예 없다 — 버전 문자열이 아니라 **키**로 판정."""
        v1 = {"schema_version": gh.HANDOFF_SCHEMA_VERSION, "rows": [{"document_id": "a"}]}
        self.assertEqual(db.extract_handoff_body_full(v1), {})
        self.assertEqual(db.extract_handoff_body_full({}), {})

    def test_blank_body_is_not_harvested(self) -> None:
        """빈 body_full 을 주우면 예치된 진짜 원문을 공백으로 덮어 카드를 죽인다."""
        p = _handoff_payload({"a": "   "})
        self.assertEqual(db.extract_handoff_body_full(p), {})

    def test_card_id_prefixed_key_is_not_used(self) -> None:
        """`card_id`(source::document_id)를 주우면 델타 키 공간과 어긋나 전건 미스가 된다."""
        bodies = db.extract_handoff_body_full(_handoff_payload({"admin-1": "원문"}))
        self.assertEqual(list(bodies), ["admin-1"])


class ApplyHandoffSourceTextTest(unittest.TestCase):
    """정본화 규약 — 채움/교체/동일/미보유, 그리고 **하지 않는 것들**."""

    def test_drifted_source_text_is_replaced(self) -> None:
        deep = {"admin-1": {"deep_analysis": {}, "source_text": _DRIFTED_SOURCE}}
        stats = db.apply_handoff_source_text(deep, {"admin-1": _BODY_FULL_ADMIN})
        self.assertEqual(deep["admin-1"]["source_text"], _BODY_FULL_ADMIN)
        self.assertEqual(stats["replaced"], 1)

    def test_missing_source_text_is_filled(self) -> None:
        """프롬프트가 예치를 그만두면 이 경로가 정상 경로가 된다."""
        deep = {"admin-1": {"deep_analysis": {}}}
        stats = db.apply_handoff_source_text(deep, {"admin-1": _BODY_FULL_ADMIN})
        self.assertEqual(deep["admin-1"]["source_text"], _BODY_FULL_ADMIN)
        self.assertEqual(stats["filled"], 1)

    def test_identical_is_noop(self) -> None:
        deep = {"admin-1": {"source_text": _BODY_FULL_ADMIN}}
        stats = db.apply_handoff_source_text(deep, {"admin-1": _BODY_FULL_ADMIN})
        self.assertEqual((stats["identical"], stats["replaced"], stats["filled"]), (1, 0, 0))

    def test_card_absent_from_handoff_keeps_deposited_text(self) -> None:
        """소급 복구 경로(`fda483_ocr_backfill` OCR 판독본)는 handoff 에 짝이 없다 — 지우면 안 된다."""
        deep = {"fda483-1": {"source_text": "OCR 판독본", "source_text_status": "pdf-ok-ocr"}}
        stats = db.apply_handoff_source_text(deep, {"admin-9": _BODY_FULL_ADMIN})
        self.assertEqual(deep["fda483-1"]["source_text"], "OCR 판독본")
        self.assertEqual(stats["absent"], 1)

    def test_does_not_create_cards(self) -> None:
        """분석 없는 카드에 원문만 실으면 조립이 '원문은 있는데 분석이 없는 카드'를 새로 본다."""
        deep = {"admin-1": {"deep_analysis": {}}}
        db.apply_handoff_source_text(deep, {"admin-1": "a", "admin-2": "b", "admin-3": "c"})
        self.assertEqual(set(deep), {"admin-1"})

    def test_other_keys_untouched(self) -> None:
        deep = {"fda483-1": {"source_text": "옛것", "source_text_status": "pdf-ok",
                             "observations_ko": [{"number": "1"}], "deep_analysis": {"a": 1}}}
        db.apply_handoff_source_text(deep, {"fda483-1": _BODY_FULL_ADMIN})
        self.assertEqual(deep["fda483-1"]["source_text_status"], "pdf-ok")
        self.assertEqual(deep["fda483-1"]["observations_ko"], [{"number": "1"}])
        self.assertEqual(deep["fda483-1"]["deep_analysis"], {"a": 1})


class FetchHandoffBodyFullTest(unittest.TestCase):
    def test_chunked_payload_is_joined_and_parsed(self) -> None:
        """payload 는 1,900자 code 블록으로 쪼개져 실린다 — 결합하지 않으면 전량 유실."""
        payload = _handoff_payload({"admin-1": "가" * 4000})
        fake = _HandoffNotion([], blocks={"h1": _chunked_code_blocks(payload)},
                              handoff_pages={"routine-handoff::2026-07-13": _handoff_page()})
        self.assertGreater(len(fake.blocks["h1"]), 1)  # 픽스처가 실제로 쪼개졌는지부터 확인
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            bodies = db.fetch_handoff_body_full("tok", "dbid", "2026-07-13")
        self.assertEqual(bodies, {"admin-1": "가" * 4000})

    def test_queries_exact_handoff_id(self) -> None:
        """`routine-handoff::{publish_date}` 정확일치 — 산식이 어긋나면 조회가 조용히 0건."""
        fake = _HandoffNotion([], handoff_pages={})
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            db.fetch_handoff_body_full("tok", "dbid", "2026-07-13")
        self.assertEqual(fake.handoff_queries, ["routine-handoff::2026-07-13"])

    def test_missing_page_returns_empty(self) -> None:
        fake = _HandoffNotion([], handoff_pages={})
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            self.assertEqual(db.fetch_handoff_body_full("tok", "dbid", "2026-07-13"), {})

    def test_unparseable_payload_returns_empty(self) -> None:
        fake = _HandoffNotion([], blocks={"h1": [_code_block_text("{망가진")]},
                              handoff_pages={"routine-handoff::2026-07-13": _handoff_page()})
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            self.assertEqual(db.fetch_handoff_body_full("tok", "dbid", "2026-07-13"), {})


class HandoffSourceTextIntegrationTest(unittest.TestCase):
    """main() 경로 — 정본화가 **게이트보다 먼저** 걸리는지(순서 불가침)."""

    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"NOTION_TOKEN": "tok"})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _run(self, handoff_pages, deep_payload):
        fake = _HandoffNotion(
            [_delta_page("p1", "2026-07-13"), _deep_page("p2", "2026-07-13")],
            blocks={"p1": [_code_block(_valid_delta())],
                    "p2": [_code_block(deep_payload)],
                    "h1": _chunked_code_blocks(_handoff_payload({"admin-1": _BODY_FULL_ADMIN}))},
            handoff_pages=handoff_pages)
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid"])
        path = pathlib.Path("web/data/deltas/deep_2026_07_13.json")
        written = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return rc, written, fake

    def test_drifted_deposit_is_healed_before_the_gate(self) -> None:
        """★발화 확인용 — 인용 조항이 **전사에서 잘려나간 말미**에만 있는 카드.

        정본화가 없으면 D2 가 "원문에 없는 조항"으로 보고 그 카드를 drop 한다(= 심층분석
        침묵 유실). handoff 원문으로 맞춘 뒤 게이트를 돌려야 통과한다.
        """
        deposited = {"admin-1": {"deep_analysis": _GROUNDED_DA, "source_text": _DRIFTED_SOURCE}}
        rc, written, _ = self._run({"routine-handoff::2026-07-13": _handoff_page()}, deposited)
        self.assertEqual(rc, 0)
        self.assertIsNotNone(written, "정본화가 걸리지 않아 D2 FAIL 로 카드가 drop 됐다")
        self.assertEqual(written["admin-1"]["source_text"], _BODY_FULL_ADMIN)

    def test_without_handoff_the_same_card_is_dropped(self) -> None:
        """대조군 — handoff 가 없으면 예치본 그대로라 종전 동작(drop)과 동일하다.

        이 대조군이 있어야 위 테스트가 '정본화 덕분에' 통과한 것임이 증명된다(순환 방지).
        """
        deposited = {"admin-1": {"deep_analysis": _GROUNDED_DA, "source_text": _DRIFTED_SOURCE}}
        with redirect_stderr(io.StringIO()):
            rc, written, _ = self._run({}, deposited)
        self.assertEqual(rc, 0)                       # delta 는 정상 기록(비차단)
        self.assertIsNone(written)                    # deep 은 전건 drop
        self.assertTrue(pathlib.Path("web/data/deltas/delta_2026_07_13.json").exists())

    def test_handoff_failure_never_blocks_the_delta(self) -> None:
        """deep 은 선택 계층 — 그 조회 실패가 주간 발행(=delta)을 인질로 잡으면 안 된다."""
        class _Boom(_HandoffNotion):
            def __call__(self, method, url, token, body=None, **kw):
                flt = (body or {}).get("filter") or {}
                if method == "POST" and flt.get("property") == gh.PROP_DOC_ID:
                    raise db.NotionHandoffError("Notion 500")
                return super().__call__(method, url, token, body=body, **kw)

        fake = _Boom([_delta_page("p1", "2026-07-13"), _deep_page("p2", "2026-07-13")],
                     blocks={"p1": [_code_block(_valid_delta())],
                             "p2": [_code_block({"admin-1": {"deep_analysis": _GROUNDED_DA,
                                                             "source_text": _BODY_FULL_ADMIN}})]})
        with mock.patch.object(db, "notion_api_request", side_effect=fake):
            rc = db.main(["--db", "dbid"])
        self.assertEqual(rc, 0)
        self.assertTrue(pathlib.Path("web/data/deltas/delta_2026_07_13.json").exists())
        # 예치본이 근거로 남아 있으므로 deep 도 종전대로 기록된다(폴백이 실제로 산다).
        self.assertTrue(pathlib.Path("web/data/deltas/deep_2026_07_13.json").exists())


class DeepKeyNamespaceWiringTest(unittest.TestCase):
    """[2026-08-17] `normalize_deep_key_namespace` 가 **정의만 되고 배선이 없던** 두 경로.

    이 함수의 docstring 은 "별도 페이지로 온 deep 은 delta 경로를 타지 않는다"고 위험을
    정확히 적어두고도 정작 그 경로에서 호출되지 않았다. 접두사 키가 남으면 카드 id 가 전부
    빗나가 심층분석이 조용히 사라지고, handoff 원문 조회(`web_card_id` = bare)도 전건 미스가 된다.
    """

    def test_separate_deep_page_keys_are_normalized(self) -> None:
        page = _deep_page("p2", "2026-07-13")
        page["_code_blocks"] = [_j({"MFDS::admin-1": {"deep_analysis": {}, "source_text": "x"}})]
        self.assertEqual(set(db.extract_deep_page(page)), {"admin-1"})

    def test_clean_delta_with_prefixed_deep_keys_is_normalized(self) -> None:
        """delta 키가 깨끗하면 `normalize_card_key_namespace` 는 조기 return 한다 — 그때가 사각."""
        page = _delta_page("p1", "2026-07-13")
        page["_code_blocks"] = [_j(_valid_delta()),
                                _j({"MFDS::mfds-1": {"deep_analysis": {}, "source_text": "x"}})]
        _delta, deep, _date = db.extract_delta(page)
        self.assertEqual(set(deep), {"mfds-1"})


class HandoffIdFormulaTest(unittest.TestCase):
    """`handoff_id` 산식 단일화 — 쓰는 쪽(emit)과 읽는 쪽(브릿지)이 같은 문자열을 봐야 한다."""

    def test_write_and_read_sides_agree(self) -> None:
        from datetime import date, datetime
        payload = gh.build_routine_handoff_payload_v2(
            [], date(2026, 7, 13), 7, datetime(2026, 7, 13, 3, 17))
        self.assertEqual(payload["handoff_id"], gh.handoff_id_for("2026-07-13"))
        self.assertEqual(payload["handoff_id"], "routine-handoff::2026-07-13")

    def test_accepts_date_and_str(self) -> None:
        from datetime import date
        self.assertEqual(gh.handoff_id_for(date(2026, 7, 13)), gh.handoff_id_for("2026-07-13"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
