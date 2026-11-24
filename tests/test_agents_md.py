import unittest
from pathlib import Path


AGENTS_MD = Path(__file__).resolve().parents[1] / "AGENTS.md"


class TestAgentsMd(unittest.TestCase):
    def test_agents_md_exists(self):
        self.assertTrue(AGENTS_MD.is_file())

    def test_requires_reading_only_relevant_sections(self):
        self.assertIn("통째로 읽지 말 것", AGENTS_MD.read_text(encoding="utf-8"))

    def test_prohibits_merging(self):
        self.assertIn("머지 금지", AGENTS_MD.read_text(encoding="utf-8"))

    def test_prohibits_git_add_all(self):
        self.assertIn("`git add -A` 금지", AGENTS_MD.read_text(encoding="utf-8"))
