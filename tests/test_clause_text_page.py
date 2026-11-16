"""/clauses/ 트리 뷰·근거 조항 해석기(web/clause_text_page.py) — 커밋 정본으로 돈다(네트워크 0)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

import clause_text_page as ct  # noqa: E402


class ClauseTextView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = ct.build(ct.load_sources())

    def test_paths_cover_every_text_clause_and_no_heading_only(self):
        paths = ct.tree_paths(self.view)
        self.assertIn("clauses/", paths)
        self.assertIn("clauses/cfr21/211/211-192/", paths)
        self.assertIn("clauses/eu-gmp/annex1/8-123/", paths)
        self.assertIn("clauses/ich/q10/2-7/", paths)
        n_pages = sum(1 for _ in ct.pages(self.view))
        self.assertEqual(n_pages, self.view["total"])
        # 소제목뿐인 번호(Q7 2.1 Principles)는 페이지가 없다 — 빈 페이지 금지.
        self.assertNotIn("clauses/ich/q7/2-1/", paths)

    def test_text_is_not_truncated(self):
        for _d, c in ct.pages(self.view):
            self.assertEqual(sum(len(p) for p in c["paras"]) + 2 * (len(c["paras"]) - 1), c["chars"],
                             c["id"])

    def test_ref_href_resolves_only_when_certain(self):
        h = lambda lab: ct.ref_href(self.view, lab)  # noqa: E731
        self.assertEqual(h("21 CFR 211.192"), "clauses/cfr21/211/211-192/")
        self.assertEqual(h("21 CFR 211.42(c)(10)"), "clauses/cfr21/211/211-42/")
        self.assertEqual(h("21 CFR Part 211"), "clauses/cfr21/211/")
        self.assertEqual(h("21 CFR 211.160–211.194"), "")
        self.assertEqual(h("ICH Q10 §2.7"), "clauses/ich/q10/2-7/")
        self.assertEqual(h("ICH Q9(R1) §4.4"), "clauses/ich/q9/4-4/")
        self.assertEqual(h("ICH Q7 §§7–11"), "clauses/ich/q7/#c-7")
        self.assertEqual(h("EU GMP Part I Chapter 1 §1.12"), "clauses/eu-gmp/ch1/1-12/")
        self.assertEqual(h("EU GMP Part I Chapter 4"), "clauses/eu-gmp/ch4/")
        self.assertEqual(h("EU GMP Annex 1 §§4.1–4.32"), "clauses/eu-gmp/annex1/#c-4-1")
        self.assertEqual(h("EU GMP Annex 11 §4"), "")
        self.assertEqual(h("EU GMP Annex 15"), "")
        self.assertEqual(h("PIC/S PI 041-1"), "")

    def test_resolved_targets_exist(self):
        paths = ct.tree_paths(self.view)
        for lab in ("21 CFR 211.192", "ICH Q7 Glossary", "EU GMP Annex 1 §8.123", "ICH Q10 §§1.5–1.8"):
            href = ct.ref_href(self.view, lab)
            self.assertTrue(href, lab)
            self.assertIn(href.split("#")[0], paths, lab)


if __name__ == "__main__":
    unittest.main()
