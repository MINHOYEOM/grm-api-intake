#!/usr/bin/env python3
"""[브리프 자료실 스트립 2026-08-31] 브리프 창(window) 안 자료실 변경 뷰모델 테스트.

대상: render.build_library_update_window_view() / render._parse_brief_window() — 둘 다
순수 함수(파일 IO·네트워크 0, entries/catalogs 는 인자로 받는다). 실제 렌더(제목 해석·
라벨·round-robin 배분)는 기존 render._library_update_view() 가 맡고 이미
WebLibraryUpdateTest(web/tests/test_render.py)가 그 계약을 고정하므로, 여기서는 "창
필터링 + 다중 이력 병합" 이라는 이 기능 고유의 로직만 검증한다.

CI(`unittest discover -s tests`)는 `tests/test_web_library_week_strip.py` shim 으로
순회(TestCase 전수 자동 재-export, test_web_render.py 와 동형). 직접:
  python web/tests/test_library_week_strip.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent          # …/web
sys.path.insert(0, str(WEB_DIR))
import render  # noqa: E402  (web/render.py)

# 합성 카탈로그 — 실 web/data/library 를 건드리지 않는 순수 in-memory 픽스처.
# render._library_update_view() 가 실제로 읽는 키만 갖춘다(source/slug/short/title/
# items_by_id, 항목은 title/sub/official_url).
CATALOG_FDA = {
    "source": "fda_guidance", "slug": "fda-guidance", "short": "FDA", "title": "FDA 가이던스",
    "items_by_id": {
        "fda-1": {"title": "FDA 문서 1", "sub": "", "official_url": "https://fda.example/1"},
        "fda-2": {"title": "FDA 문서 2", "sub": "", "official_url": "https://fda.example/2"},
        "fda-3": {"title": "FDA 문서 3", "sub": "", "official_url": "https://fda.example/3"},
    },
}
CATALOG_MFDS = {
    "source": "mfds", "slug": "mfds", "short": "식약처", "title": "식약처 가이드라인",
    "items_by_id": {
        "mfds-1": {"title": "식약처 문서 1", "sub": "", "official_url": "https://mfds.example/1"},
        "mfds-2": {"title": "식약처 문서 2", "sub": "", "official_url": "https://mfds.example/2"},
    },
}
CATALOGS = [CATALOG_FDA, CATALOG_MFDS]


def _entry(date: str, sources: dict) -> dict:
    return {"date": date, "sources": sources}


class BuildLibraryUpdateWindowViewTest(unittest.TestCase):
    """① 창 내 1엔트리 ② 창 내 2엔트리 병합 ③ 창 밖만 ④ 빈 entries."""

    def test_single_entry_within_window_builds_a_view(self):
        entries = [_entry("2026-08-02", {
            "fda_guidance": {"new_ids": ["fda-1"], "changed_ids": [], "removed_ids": [],
                              "total_count": 3},
        })]
        view = render.build_library_update_window_view(
            entries, CATALOGS, "2026-07-27", "2026-08-03")
        self.assertIsNotNone(view)
        self.assertEqual(view["date"], "2026-08-02")
        shorts = {s["short"]: s for s in view["sources"]}
        self.assertIn("FDA", shorts)
        self.assertEqual(shorts["FDA"]["new_count"], 1)

    def test_two_entries_in_window_merge_with_dedup_and_hidden_count(self):
        """겹치는 id(fda-1)는 한 번만 세고, cap 을 넘긴 나머지는 hidden_count 로 남는다."""
        entries = [
            # entries 는 최신 우선(날짜 내림차순) — load_library_update_entries() 계약.
            _entry("2026-08-10", {
                "fda_guidance": {"new_ids": ["fda-1", "fda-2"], "changed_ids": [],
                                  "removed_ids": [], "total_count": 9},
                "mfds": {"new_ids": ["mfds-1"], "changed_ids": [], "removed_ids": [],
                         "total_count": 5},
            }),
            _entry("2026-08-05", {
                # fda-1 은 위와 중복(등장 순서 보존 union 은 중복 제거) — fda-3 만 신규 추가.
                "fda_guidance": {"new_ids": ["fda-1", "fda-3"], "changed_ids": [],
                                  "removed_ids": [], "total_count": 7},
                "mfds": {"new_ids": [], "changed_ids": ["mfds-2"], "removed_ids": [],
                         "total_count": 4},
            }),
        ]
        view = render.build_library_update_window_view(
            entries, CATALOGS, "2026-08-03", "2026-08-10", cap=3)
        self.assertIsNotNone(view)
        self.assertEqual(view["date"], "2026-08-10")   # 창 내 최신 이력의 date
        shorts = {s["short"]: s for s in view["sources"]}
        # fda-1 중복 제거 → new_count 는 fda-1·fda-2·fda-3 세 건(중복 없음).
        self.assertEqual(shorts["FDA"]["new_count"], 3)
        self.assertEqual(shorts["식약처"]["new_count"], 1)
        self.assertEqual(shorts["식약처"]["changed_count"], 1)
        # cap=3 을 전체 5건(3 신규+1 신규+1 변경)이 넘으므로 절삭분은 hidden_count 로 드러난다.
        shown = sum(len(s["items"]) for s in view["sources"])
        self.assertLessEqual(shown, 3)
        self.assertEqual(view["hidden_count"], view["change_count"] - shown)
        self.assertGreater(view["hidden_count"], 0, "이 픽스처는 절삭이 나야 한다")

    def test_entries_outside_window_yield_none(self):
        entries = [
            _entry("2026-07-20", {"fda_guidance": {"new_ids": ["fda-1"], "changed_ids": [],
                                                     "removed_ids": [], "total_count": 3}}),
            _entry("2026-09-01", {"fda_guidance": {"new_ids": ["fda-2"], "changed_ids": [],
                                                     "removed_ids": [], "total_count": 3}}),
        ]
        view = render.build_library_update_window_view(
            entries, CATALOGS, "2026-07-27", "2026-08-03")
        self.assertIsNone(view)

    def test_empty_entries_yield_none(self):
        view = render.build_library_update_window_view(
            [], CATALOGS, "2026-07-27", "2026-08-03")
        self.assertIsNone(view)

    def test_window_bounds_are_inclusive(self):
        """window_start_iso·window_end_iso 자체 날짜의 이력도 포함해야 한다(경계 포함)."""
        entries = [_entry("2026-08-03", {
            "fda_guidance": {"new_ids": ["fda-1"], "changed_ids": [], "removed_ids": [],
                              "total_count": 3},
        })]
        self.assertIsNotNone(render.build_library_update_window_view(
            entries, CATALOGS, "2026-07-27", "2026-08-03"))
        entries2 = [_entry("2026-07-27", entries[0]["sources"])]
        self.assertIsNotNone(render.build_library_update_window_view(
            entries2, CATALOGS, "2026-07-27", "2026-08-03"))

    def test_entry_ids_absent_from_catalog_do_not_leak_into_the_view(self):
        """카탈로그에 없는 id 만 든 소스는 통째로 빠진다(_library_update_view 계약 재사용 확인)."""
        entries = [_entry("2026-08-02", {
            "fda_guidance": {"new_ids": ["fda-does-not-exist"], "changed_ids": [],
                              "removed_ids": [], "total_count": 3},
        })]
        view = render.build_library_update_window_view(
            entries, CATALOGS, "2026-07-27", "2026-08-03")
        self.assertIsNone(view)   # 유일한 소스의 유일한 id 가 해소 안 되므로 통째로 None


class ParseBriefWindowTest(unittest.TestCase):
    """⑤ 창 문자열 파싱 — 정상/깨진 입력."""

    def test_parses_the_standard_display_format(self):
        self.assertEqual(render._parse_brief_window("2026-08-24 ~ 2026-08-31"),
                          ("2026-08-24", "2026-08-31"))

    def test_tolerates_incidental_whitespace_or_dash_variants(self):
        self.assertEqual(render._parse_brief_window("2026-08-24~2026-08-31"),
                          ("2026-08-24", "2026-08-31"))

    def test_empty_string_yields_none(self):
        self.assertIsNone(render._parse_brief_window(""))

    def test_missing_second_date_yields_none(self):
        self.assertIsNone(render._parse_brief_window("2026-08-24 ~ "))

    def test_garbled_input_yields_none(self):
        self.assertIsNone(render._parse_brief_window("지난 주"))

    def test_three_dates_yields_none(self):
        """정확히 두 개가 아니면(형식이 어긋난 신호) 조용히 포기한다 — 셋 중 아무 둘도 억측하지 않는다."""
        self.assertIsNone(render._parse_brief_window(
            "2026-08-24 ~ 2026-08-31 (2026-09-01 기준)"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
