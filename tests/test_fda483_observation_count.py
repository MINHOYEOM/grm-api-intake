"""483 관찰 **건수**를 원문 번호 표제에서 세고, 단정 못 하면 비운다 (2026-11-23).

11-23 발행(리허설 1122b 포함) 교열 1차가 483 카드 2장을 같은 사유로 반려했다 — 요약의
"관찰사항 N건"이 원문과 달랐는데, 원인은 기자 문장이 아니라 결정론 상세(`deterministic_detail`)
의 건수였다.

  · fda483-196447: 원문 `1) 2) 3) 4)` 인데 1번이 OCR 로 `|)` 가 됐고, 양식 마커
    `(I) (WE) OBSERVED` 는 정상 마커가 못 잡아 관찰 4)a 본문 속 산문 "we observed" 가 첫 마커가
    됐다. 회수 경로가 그 꼬리 한 문장을 ⑤(번호 없는 단일 관찰)로 승격해 "관찰 1건".
  · fda483-196455: 원문 1~6 인데 1번은 마침표가 빠져(`1 There…`) 앵커가 안 잡히고, 1·5·6 표제는
    OCR 로 깨져 게이트에서 빠졌다. 건수를 `len(rows)` 로 세서 "관찰 3건".

고정하는 성질:
  ① 건수는 rows 가 아니라 rows 를 만든 경로가 본 번호 표제(`seen`)에서 센다 — 표제가 깨져
     게이트에서 빠진 관찰도 건수에는 든다.
  ② ⑤ 는 어느 마커로도 번호 목록이 안 나올 때만 쓴다.
  ③ 번호 목록 1번만 깨진 꼴(`|)`·마침표 결손)은 마커 바로 뒤에서만 되살린다.
  ④ 서식 뒷면 안내문 `1. Pursuant… 2. To assist…` 는 건수 근거가 아니다.
  ⑤ 번호가 건너뛰거나 겹치면 건수를 **비운다**(None) — 조용히 틀린 숫자를 쓰지 않는다. rows 는
     그대로 둔다(저장 단계에서 자르지 않는다).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assemble_publish_brief as apb  # noqa: E402
import card_scaffold as cs  # noqa: E402
import collect_fda_483 as f  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name: str) -> str:
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def _extract(text: str) -> tuple[list[dict[str, str]], list[str], int | None]:
    seen: list[str] = []
    rows = f._extract_483_observations_from_text(text, seen=seen)
    return rows, seen, f.fda483_observation_count(rows, seen)


def _rows(*numbers: str) -> list[dict[str, str]]:
    return [{"number": n, "deficiency": f"Deficiency {n} was not followed.", "detail": ""}
            for n in numbers]


class RealDocumentCounts(unittest.TestCase):
    """실문서 전문(11-23 handoff 의 deep 입력 그대로)으로 재현·고정."""

    def test_196447_glyph_first_paren_item_counts_four(self):
        rows, seen, count = _extract(_fixture("fda483_196447_glyph_paren_list.txt"))
        self.assertEqual(seen, ["1", "2", "3", "4"])
        self.assertEqual(count, 4)
        # 종전 오독: 관찰 4)a 꼬리("staff not regularly sanitizing…")가 "관찰 1"로 저장됐다.
        self.assertFalse(any(r["deficiency"].startswith("staff not regularly") for r in rows))
        self.assertEqual([r["number"] for r in rows][-3:], ["2", "3", "4"])

    def test_196455_dropped_dot_and_garbled_heads_count_six(self):
        rows, seen, count = _extract(_fixture("fda483_196455_dropped_dot_list.txt"))
        self.assertEqual(seen, ["1", "2", "3", "4", "5", "6"])   # 뒷면 안내문 1·2 제외
        self.assertEqual(count, 6)
        self.assertEqual([r["number"] for r in rows], ["2", "3", "4"])  # 공개 표제는 그대로


class CountRule(unittest.TestCase):
    """`fda483_observation_count` — 하위 번호·번호 건너뜀·페이지 넘김 반복 부류."""

    def test_contiguous_rows_without_seen_keep_len(self):
        self.assertEqual(f.fda483_observation_count(_rows("1", "2", "3")), 3)

    def test_seen_covers_gated_out_headings(self):
        self.assertEqual(f.fda483_observation_count(_rows("2", "3", "4"),
                                                    ["1", "2", "3", "4", "5", "6"]), 6)

    def test_skipped_numbers_are_blank_not_len(self):
        self.assertIsNone(f.fda483_observation_count(_rows("2", "3", "4")))           # 1 결손
        self.assertIsNone(f.fda483_observation_count(_rows("1", "2", "6"),
                                                     ["1", "2", "3", "6"]))           # 4·5 결손
        self.assertIsNone(f.fda483_observation_count(_rows("4")))                     # 하위 4)a

    def test_repeated_numbers_from_page_turn_or_sublist_are_blank(self):
        self.assertIsNone(f.fda483_observation_count(_rows("1", "2", "3", "5", "5", "6")))
        self.assertIsNone(f.fda483_observation_count(_rows("1", "1", "2", "1", "2")))

    def test_rows_outside_seen_run_fall_back(self):
        # 표제 근거가 1..3 인데 rows 에 5 가 있으면 근거가 rows 를 설명 못 한다 → rows 규칙.
        self.assertIsNone(f.fda483_observation_count(_rows("1", "5"), ["1", "2", "3"]))

    def test_non_digit_number_is_blank(self):
        self.assertIsNone(f.fda483_observation_count(_rows("1", "a")))
        self.assertIsNone(f.fda483_observation_count([]))


class MissingFirstItem(unittest.TestCase):
    """번호 목록의 1번만 깨진 꼴은 **마커 바로 뒤**에서만 되살린다."""

    SECOND = "\n2. The firm did not validate the cleaning procedure for the mixer.\n"

    def _anchors(self, scoped: str) -> list[str]:
        found = f._with_missing_first_item(scoped, list(f._NUMBERED_OBS_RE.finditer(scoped)))
        return [m.group(1) for m in found]

    def test_dropped_dot_right_after_marker(self):
        self.assertEqual(self._anchors("\n1 There is no written procedure." + self.SECOND),
                         ["1", "2"])

    def test_glyph_one_right_after_marker(self):
        self.assertEqual(self._anchors(":\n|) Your firm failed to follow procedures." + self.SECOND),
                         ["|", "2"])

    def test_not_after_other_text(self):
        # 마커 뒤 첫 내용이 1번 모양이 아니면 아무것도 붙이지 않는다(문서 중간의 1·I 무시).
        self.assertEqual(self._anchors("\nSee section 1 There is text." + self.SECOND), ["2"])

    def test_untouched_when_first_anchor_is_one(self):
        text = "\n1. The firm failed to investigate.\n2. The firm did not validate it.\n"
        self.assertEqual(self._anchors(text), ["1", "2"])


class SingleUnnumberedIsLastResort(unittest.TestCase):
    """산문 'we observed' 가 첫 마커여도, 양식 마커 뒤 번호 목록이 ⑤ 단일 관찰보다 앞선다."""

    TEXT = (
        "DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED:\n"
        "1) Your firm failed to follow written procedures designed to prevent mix-ups.\n"
        "2) Your firm failed to establish the reliability of the supplier analyses.\n"
        "3) Written operating procedures are not followed, specifically,\n"
        "a. On March 30, 2026, we observed staff not regularly sanitizing their gloves "
        "as required by the procedure for aseptic operations.\n"
    )

    def test_numbered_list_wins_over_prose_marker_tail(self):
        rows, seen, count = _extract(self.TEXT)
        self.assertEqual([r["number"] for r in rows], ["1", "2", "3"])
        self.assertEqual(count, 3)

    def test_unnumbered_single_still_used_when_no_list(self):
        text = ("DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED:\n"
                "The firm failed to maintain the compounding area in a sanitary condition, "
                "and surfaces were not cleaned between preparations.\n")
        rows, seen, count = _extract(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(count, 1)


class DownstreamCount(unittest.TestCase):
    """스캐폴드·조립·렌더가 같은 규칙을 쓰고, 단정 불가면 숫자를 비운다."""

    def test_scaffold_uses_collector_count(self):
        dd = cs._detail_fda_483_observations({}, {
            "fda_483_observations": _rows("2", "3", "4"), "fda_483_observation_count": 6})
        self.assertEqual(dd["count"], 6)
        self.assertEqual(len(dd["observations"]), 3)

    def test_scaffold_blank_count_when_collector_says_unknown(self):
        dd = cs._detail_fda_483_observations({}, {
            "fda_483_observations": _rows("1", "2", "6"), "fda_483_observation_count": None})
        self.assertNotIn("count", dd)
        self.assertEqual(len(dd["observations"]), 3)      # rows 는 자르지 않는다

    def test_scaffold_old_raw_with_gap_is_blank(self):
        dd = cs._detail_fda_483_observations({}, {"fda_483_observations": _rows("2", "3", "4")})
        self.assertNotIn("count", dd)

    def test_scaffold_old_raw_contiguous_unchanged(self):
        dd = cs._detail_fda_483_observations({}, {"fda_483_observations": _rows("1", "2")})
        self.assertEqual(dd["count"], 2)

    def test_assemble_refresh_counts_from_source(self):
        card = {"id": "fda483-196455", "deterministic_detail": {
            "type": "fda_483_observations", "count": 3, "observations": _rows("2", "3", "4")}}
        rep = apb.AssembleReport()
        apb._refresh_483_observations(
            {"cards": [card]},
            {"fda483-196455": {"source_text": _fixture("fda483_196455_dropped_dot_list.txt")}},
            rep)
        self.assertEqual(card["deterministic_detail"]["count"], 6)
        self.assertTrue(any("건수 3 → 6" in w for w in rep.warnings), rep.warnings)

    def test_render_preview_has_no_number_when_blank(self):
        web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
        if web_dir not in sys.path:
            sys.path.insert(0, web_dir)          # render.py 는 web/ 를 sys.path 로 가정한다
        import render
        self.assertEqual(render._detail_preview({"type": "fda_483_observations",
                                                 "observations": []}), "")
        self.assertIn("6", render._detail_preview({"type": "fda_483_observations", "count": 6}))


if __name__ == "__main__":
    unittest.main()
