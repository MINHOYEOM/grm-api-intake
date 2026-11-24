"""Search snippets retain the source text in full."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_search  # noqa: E402


class SearchSnippetTests(unittest.TestCase):
    def test_snippet_not_cut(self):
        long = "s" * 900
        self.assertEqual(collect_search._extract_snippet({"description": long}), long)
        self.assertEqual(
            collect_search._extract_snippet({"extra_snippets": ["x", long]}), long)


if __name__ == "__main__":
    unittest.main()
