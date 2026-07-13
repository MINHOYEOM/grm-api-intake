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


if __name__ == "__main__":
    unittest.main(verbosity=2)
