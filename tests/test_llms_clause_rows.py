"""llms.txt 가 조항 원문·실사 대비 노트를 알린다 (2026-11-26 검색 유입 점검).

빙 유입의 둘째 묶음이 조항 원문(en/clauses·en/library)인데 AI 검색 안내 파일에는 그 면이
없었다. 면이 있을 때만 적고(없는 면을 알리면 AI 가 404 를 인용한다), 국·영 두 줄이 함께 난다.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "web"))
sys.path.insert(0, str(REPO))
import render  # noqa: E402

B = render.SITE_BASE_URL


class LlmsClauseRowsTest(unittest.TestCase):
    def test_rows_present_when_pages_exist(self):
        txt = render.build_llms_txt([], en_paths={"", "clauses/", "inspection-notes/"})
        for path in ("/clauses/", "/inspection-notes/", "/en/clauses/", "/en/inspection-notes/"):
            self.assertIn(f"]({B}{path})", txt, path)

    def test_rows_absent_when_pages_do_not_exist(self):
        txt = render.build_llms_txt([], en_paths={"", "glossary/"})
        for path in ("/clauses/", "/inspection-notes/"):
            self.assertNotIn(f"]({B}{path})", txt, path)
            self.assertNotIn(f"]({B}/en{path})", txt, path)


if __name__ == "__main__":
    unittest.main()
