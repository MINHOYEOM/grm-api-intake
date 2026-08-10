"""브리프 ↔ 자료실 대조 게이트 테스트.

2026-08-10 사고 재현이 이 파일의 본체다 — 브리프에는 실렸는데 자료실에는 없는 상태를
탐지하는가. 실제 사고 카드(`data0010-15270`)를 픽스처로 박아 둔다(라이브 데이터에 의존하면
수리된 뒤 테스트가 무의미해진다).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mfds
import library_brief_reconcile as reconcile
import library_staging_build


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class EligibleBoardsTest(unittest.TestCase):
    def test_boards_are_derived_not_hand_listed(self) -> None:
        """보드 목록은 수집기 × 자료실 타입에서 유도된다 — 손열거면 언젠가 어긋난다."""
        boards = reconcile.eligible_board_ids()
        # 자료실이 수록하는 타입의 보드
        self.assertIn("data0010", boards)   # guidance-internal — 이번 사고 보드
        self.assertIn("data0013", boards)   # guidance-industry
        self.assertIn("data0011", boards)   # guidance-industry
        self.assertIn("data0005", boards)   # notice-final
        # 자료실 대상이 아닌 타입은 빠진다
        self.assertNotIn("data0009", boards)   # legislative-notice
        self.assertNotIn("data0008", boards)   # regulation-final
        self.assertNotIn("seohan001", boards)  # safety-letter

    def test_derivation_tracks_its_two_sources(self) -> None:
        """유도의 두 입력이 살아 있는지 — 하나라도 비면 게이트가 조용히 무력해진다."""
        self.assertTrue(collect_mfds.MFDS_RSS_BOARDS)
        self.assertTrue(library_staging_build.MFDS_TYPES)
        self.assertTrue(reconcile.eligible_board_ids())


class FindGapsTest(unittest.TestCase):
    BOARDS = {"data0010", "data0013", "data0011", "data0005"}

    def test_detects_the_2026_08_10_incident(self) -> None:
        cards = [{"id": "data0010-15270", "headline_target": "바이오의약품 사전 GMP 평가 지침",
                  "card_type": "지침·안내서"}]
        gaps = reconcile.find_gaps(cards, library_ids={"data0010-15259"}, boards=self.BOARDS)
        self.assertEqual([g["id"] for g in gaps], ["data0010-15270"])
        self.assertEqual(gaps[0]["board"], "data0010")
        self.assertEqual(gaps[0]["title"], "바이오의약품 사전 GMP 평가 지침")

    def test_card_present_in_library_is_not_a_gap(self) -> None:
        cards = [{"id": "data0010-15270"}]
        self.assertEqual(
            reconcile.find_gaps(cards, {"data0010-15270"}, self.BOARDS), [])

    def test_ineligible_board_is_ignored(self) -> None:
        """입법예고·안전성서한은 자료실 수록 대상이 아니다 — 오탐이면 게이트가 죽는다."""
        cards = [{"id": "data0009-1"}, {"id": "seohan001-2"}, {"id": "data0008-3"}]
        self.assertEqual(reconcile.find_gaps(cards, set(), self.BOARDS), [])

    def test_non_mfds_card_is_ignored(self) -> None:
        cards = [{"id": "fda-wl-12345"}, {"id": ""}, {}]
        self.assertEqual(reconcile.find_gaps(cards, set(), self.BOARDS), [])

    def test_duplicate_card_ids_report_once(self) -> None:
        cards = [{"id": "data0013-1"}, {"id": "data0013-1"}]
        self.assertEqual(len(reconcile.find_gaps(cards, set(), self.BOARDS)), 1)


class BuildReportTest(unittest.TestCase):
    def _fixture(self, tmp: Path, *, library_ids: list[str]) -> Path:
        _write(tmp / "briefs" / "brief_web_2026_08_10.json", {
            "schema_version": "grm-brief-web/v1",
            "brief": {"publish_date": "2026-08-10"},
            "cards": [
                {"id": "data0010-15270", "agency": "MFDS",
                 "headline_target": "바이오의약품 사전 GMP 평가 지침"},
                {"id": "fda-wl-999", "agency": "FDA"},
            ],
        })
        _write(tmp / "library" / "mfds.json",
               {"items": [{"id": i} for i in library_ids]})
        return tmp / "briefs" / "brief_web_2026_08_10.json"

    def test_report_flags_the_gap(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            brief = self._fixture(tmp, library_ids=["data0010-15259"])
            report = reconcile.build_report(brief, tmp / "library")
        self.assertEqual(report["missing_count"], 1)
        self.assertEqual(report["missing"][0]["id"], "data0010-15270")
        self.assertEqual(report["publish_date"], "2026-08-10")
        self.assertEqual(report["checked_card_count"], 1)   # FDA 카드는 세지 않는다
        self.assertEqual(report["library_item_count"], 1)

    def test_report_is_clean_once_the_catalogue_catches_up(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            brief = self._fixture(tmp, library_ids=["data0010-15270"])
            report = reconcile.build_report(brief, tmp / "library")
        self.assertEqual(report["missing_count"], 0)
        self.assertEqual(report["missing"], [])

    def test_missing_library_file_is_not_read_as_zero_gap(self) -> None:
        """카탈로그 부재는 '격차 없음'이 아니다 — 전 카드가 격차로 잡혀야 한다."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            brief = self._fixture(tmp, library_ids=[])
            (tmp / "library" / "mfds.json").unlink()
            report = reconcile.build_report(brief, tmp / "library")
        self.assertEqual(report["missing_count"], 1)


class MainTest(unittest.TestCase):
    def test_absent_brief_is_undecidable_not_success(self) -> None:
        """브리프를 못 찾으면 종료코드 2 — 0(정상)으로 읽히면 침묵 실패가 된다."""
        with tempfile.TemporaryDirectory() as raw:
            code = reconcile.main([
                "--briefs-dir", os.path.join(raw, "none"),
                "--library-dir", os.path.join(raw, "none"),
            ])
        self.assertEqual(code, 2)

    def test_fail_on_gap_flag_controls_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _write(tmp / "briefs" / "brief_web_2026_08_10.json",
                   {"brief": {"publish_date": "2026-08-10"},
                    "cards": [{"id": "data0010-15270"}]})
            _write(tmp / "library" / "mfds.json", {"items": []})
            report_path = tmp / "gap.json"
            args = ["--briefs-dir", str(tmp / "briefs"),
                    "--library-dir", str(tmp / "library"),
                    "--report", str(report_path)]
            self.assertEqual(reconcile.main(args), 0)
            self.assertEqual(reconcile.main(args + ["--fail-on-gap"]), 1)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["missing_count"], 1)


if __name__ == "__main__":
    unittest.main()
