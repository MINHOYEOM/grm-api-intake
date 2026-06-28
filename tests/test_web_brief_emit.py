"""§1-B 영구배선 — 빈슬롯 web brief 자동 산출(collector emit) 단위 테스트.

설계 보증(additive·결정론·D5 보존):
  - `build_web_brief_payload_v2` = 실 producer 경로(`assemble_web_brief`), 파싱 아님.
  - LLM 슬롯은 빈값, 코드 필드(facts·배지·sources)는 verbatim, 같은 입력 → byte 동일.
  - emit 은 handoff v2 경로 + web_brief_dir 지정 시에만 산출(무인 라이브 0 = D5).
"""
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

import card_scaffold as cs
import collect_intake as ci

RUN_DATE = date(2026, 6, 22)
GEN_AT = datetime(2026, 6, 22, 3, 17)


def _enriched_rows() -> list[dict]:
    """handoff v2 와 동형의 enriched(raw 부착) rows — recall 2건(병합 대상) + FR 1건."""
    return [
        {  # recall 멤버 A (동일 group_key)
            "source": "MFDS", "document_id": "recall-aaa", "date": "2026-06-02",
            "type_or_class": "recall-quality", "firm": "한국제약", "headline": "정제 회수",
            "page_id": "p-a", "signal_tier": "Tier 2", "modality": "Chemical",
            "language": "KO", "raw_fetch_ok": True,
            "raw": {"ENTRPS": "한국제약(주)", "PRDUCT": "아세트아미노펜정",
                    "RTRVL_RESN": "함량부적합 자진 회수"},
        },
        {  # recall 멤버 B (동일 group_key — 병합되어 멤버는 brief 에서 제외)
            "source": "MFDS", "document_id": "recall-bbb", "date": "2026-06-02",
            "type_or_class": "recall-quality", "firm": "한국제약", "headline": "캡슐 회수",
            "page_id": "p-b", "signal_tier": "Tier 2", "modality": "Chemical",
            "language": "KO", "raw_fetch_ok": True,
            "raw": {"ENTRPS": "한국제약(주)", "PRDUCT": "이부프로펜캡슐",
                    "RTRVL_RESN": "함량부적합 자진 회수"},
        },
        {  # FR guidance
            "source": "Federal Register", "document_id": "FR-0001", "date": "2026-05-22",
            "type_or_class": "guidance-industry", "firm": "", "headline": "Guidance X",
            "page_id": "p-c", "signal_tier": "Tier 2", "modality": "",
            "language": "", "raw_fetch_ok": True,
            "raw": {"title": "Guidance X", "abstract": "This draft guidance describes ..."},
        },
    ]


class FlagTest(unittest.TestCase):
    def test_emit_flag_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ci._enable_web_brief_emit())

    def test_emit_flag_on_off(self) -> None:
        with mock.patch.dict(os.environ, {"ENABLE_WEB_BRIEF_EMIT": "true"}):
            self.assertTrue(ci._enable_web_brief_emit())
        with mock.patch.dict(os.environ, {"ENABLE_WEB_BRIEF_EMIT": "false"}):
            self.assertFalse(ci._enable_web_brief_emit())

    def test_resolve_dir_default_cwd(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ci.resolve_web_brief_dir(), ".")

    def test_resolve_dir_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"GRM_WEB_BRIEF_DIR": "out/x"}):
            self.assertEqual(ci.resolve_web_brief_dir(), "out/x")


class BuildPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = ci.build_web_brief_payload_v2(_enriched_rows(), RUN_DATE, 7)

    def test_schema_and_brief_meta(self) -> None:
        self.assertEqual(self.brief["schema_version"], "grm-web-card/v1")
        b = self.brief["brief"]
        self.assertEqual(b["run_date_kst"], "2026-06-22")
        self.assertEqual(b["window"], "2026-06-15 ~ 2026-06-22")
        self.assertEqual(b["publish_date"], "2026-06-22")
        self.assertEqual(b["tldr"], [])               # LLM placeholder
        self.assertTrue(b["ai_disclosure"])

    def test_coverage_counts(self) -> None:
        cov = self.brief["brief"]["coverage"]
        # intake_total = 전체 row 수(병합 멤버 포함, handoff row_count 와 동일 산식)
        self.assertEqual(cov["intake_total"], len(_enriched_rows()))
        # rendered = 직렬화된 카드 수(병합 멤버·watch 제외)
        self.assertEqual(cov["rendered"], len(self.brief["cards"]))

    def test_merged_member_excluded(self) -> None:
        # recall 2건이 1카드로 병합 → recall 카드는 1장(대표만)
        recall_cards = [c for c in self.brief["cards"] if c["group"] == "Recall"]
        self.assertEqual(len(recall_cards), 1)

    def test_llm_slots_empty_on_every_card(self) -> None:
        for c in self.brief["cards"]:
            self.assertEqual(c["title_issue"], "")
            self.assertEqual(c["summary"], "")
            self.assertEqual(c["implication"], "")
            self.assertEqual(c["key_facts"], [])
            self.assertEqual(c["checks"], [])

    def test_code_fields_verbatim_present(self) -> None:
        for c in self.brief["cards"]:
            self.assertTrue(c["facts"], "facts(W2 코드 verbatim)는 비어선 안 됨")
            self.assertTrue(c["agency"])
            self.assertIn("info_url", c["sources"])
            # JSON 값에 표현 틀 마크업 부재(불변식 #6)
            self.assertEqual(cs.assert_no_card_markup(c), [])

    def test_determinism_byte_for_byte(self) -> None:
        a = ci.build_web_brief_payload_v2(_enriched_rows(), RUN_DATE, 7)
        b = ci.build_web_brief_payload_v2(_enriched_rows(), RUN_DATE, 7)
        dump = lambda d: json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True)
        self.assertEqual(dump(a), dump(b))

    def test_is_real_producer_path_not_parsing(self) -> None:
        # 빌더가 to_web_card/assemble_web_brief 와 동일 결과(파싱 우회 0 증명).
        rows = _enriched_rows()
        cards = cs.merge_recall_cards(
            [cs.build_card_scaffold(r, r.get("raw")) for r in rows])
        start = RUN_DATE.fromordinal(RUN_DATE.toordinal() - 7)
        meta = {
            "run_date_kst": RUN_DATE.isoformat(),
            "window": f"{start.isoformat()} ~ {RUN_DATE.isoformat()}",
            "publish_date": RUN_DATE.isoformat(),
            "intake_total": len(rows),
            "tldr": [],
        }
        expected = cs.assemble_web_brief(cards, meta)
        self.assertEqual(self.brief, expected)


class FileWriteTest(unittest.TestCase):
    def test_filename_uses_underscores(self) -> None:
        self.assertEqual(ci.web_brief_filename(RUN_DATE), "brief_web_2026_06_22.json")

    def test_write_file_lf_and_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = ci.emit_web_brief_file(_enriched_rows(), RUN_DATE, 7, d)
            self.assertEqual(os.path.basename(path), "brief_web_2026_06_22.json")
            raw = io.open(path, "rb").read()
            self.assertNotIn(b"\r", raw)                # LF only
            self.assertTrue(raw.endswith(b"\n"))        # 후행개행
            # 파싱 결과 = 빌더 산출과 동일(삽입순서 보존 = sort_keys 미사용)
            loaded = json.loads(raw.decode("utf-8"))
            self.assertEqual(loaded, ci.build_web_brief_payload_v2(_enriched_rows(), RUN_DATE, 7))

    def test_creates_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "nested", "briefs")
            path = ci.emit_web_brief_file(_enriched_rows(), RUN_DATE, 7, sub)
            self.assertTrue(os.path.isfile(path))


class EmitBranchTest(unittest.TestCase):
    """emit_routine_handoff 가 web_brief_dir 지정 + v2 경로일 때만 산출하는지(D5)."""

    def _run(self, *, flag: str | None, web_dir: str | None):
        def fake_upsert(token, db_id, payload, generated_at, compact=False):
            return "pid", "url"

        env = {} if flag is None else {"ENABLE_HANDOFF_V2": flag}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ci, "notion_query_new_intake_rows",
                               return_value=_enriched_rows()), \
             mock.patch.object(ci, "enrich_rows_with_raw",
                               side_effect=lambda t, rows, inmemory_raw=None: (rows, {})), \
             mock.patch.object(ci, "notion_upsert_routine_handoff", side_effect=fake_upsert):
            ci.emit_routine_handoff("tok", "db", RUN_DATE, 7, GEN_AT,
                                    web_brief_dir=web_dir)

    def test_v2_with_dir_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self._run(flag="true", web_dir=d)
            self.assertTrue(os.path.isfile(os.path.join(d, "brief_web_2026_06_22.json")))

    def test_v2_without_dir_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self._run(flag="true", web_dir=None)
            self.assertEqual(os.listdir(d), [])

    def test_v1_path_no_file_even_with_dir(self) -> None:
        # flag off → v1 경로(scaffold 카드 없음) → web brief 미산출
        with tempfile.TemporaryDirectory() as d:
            self._run(flag=None, web_dir=d)
            self.assertEqual(os.listdir(d), [])

    def test_web_emit_failure_does_not_break_handoff(self) -> None:
        # web brief 빌드가 던져도 handoff 는 정상 반환(비차단).
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(ci, "emit_web_brief_file",
                               side_effect=RuntimeError("boom")):
            # _run 내부에서 예외가 새지 않아야 한다(통과 = 비차단 보장).
            self._run(flag="true", web_dir=d)


if __name__ == "__main__":
    unittest.main()
