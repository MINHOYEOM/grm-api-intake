"""[M9c 정리 수리 2026-11-26] outbox → applied 판정. 11-14~11-26 정리가 멈춘 원인(브랜치 커밋의 체크를 봄)을 막는다."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import findings_outbox_archive as oa  # noqa: E402


class DecideTest(unittest.TestCase):
    def test_archive_when_any_later_apply_succeeded(self):
        files = {"a.json": "m1", "b.json": "m2", "c.json": "m3"}
        ok = {"m1": False, "m2": True, "m3": False}.get
        v = oa.decide(files, ["m1", "m2", "m3"], ok, set())
        self.assertEqual(v, {"a.json": "archive", "b.json": "archive", "c.json": "keep:no_success_yet"})

    def test_unreadable_check_keeps_file(self):
        v = oa.decide({"a.json": "m1"}, ["m1", "m2"], lambda c: None, set())
        self.assertEqual(v["a.json"], "keep:check_unreadable")

    def test_keep_and_unknown_commit(self):
        v = oa.decide({"a.json": "m1", "b.json": None, "c.json": "zz"}, ["m1"], lambda c: True, {"a.json"})
        self.assertEqual(v, {"a.json": "keep:requested", "b.json": "keep:no_main_commit", "c.json": "keep:no_main_commit"})


class FirstParentTest(unittest.TestCase):
    """머지로 들어온 파일의 '추가 커밋'은 머지 커밋이어야 한다 — 반영 체크는 거기에만 붙는다(11-14 정지 원인)."""

    def _git(self, d, *a):
        return subprocess.run(("git", "-C", d) + a, check=True, capture_output=True, text=True).stdout.strip()

    def test_added_commit_is_the_merge_commit(self):
        d = tempfile.mkdtemp()
        g = lambda *a: self._git(d, *a)  # noqa: E731
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        Path(d, "translations", "outbox").mkdir(parents=True)
        Path(d, "README").write_text("x")
        g("add", "-A")
        g("commit", "-q", "-m", "init")
        g("checkout", "-q", "-b", "feat")
        Path(d, "translations", "outbox", "b.json").write_text("[]")
        g("add", "-A")
        g("commit", "-q", "-m", "batch")
        branch_commit = g("rev-parse", "HEAD")
        g("checkout", "-q", "main")
        g("merge", "-q", "--no-ff", "-m", "Merge pull request", "feat")
        merge_commit = g("rev-parse", "HEAD")
        cwd = os.getcwd()
        try:
            os.chdir(d)
            # 옛 지시문 방식은 브랜치 커밋을 돌려준다 — 여기엔 반영 체크가 없다
            self.assertEqual(g("log", "-1", "--format=%H", "main", "--", "translations/outbox"), branch_commit)
            self.assertEqual(oa.added_commit("main", "translations/outbox/b.json"), merge_commit)
            self.assertEqual(oa.outbox_commits("main"), [merge_commit])
        finally:
            os.chdir(cwd)


class DailyInstructionTest(unittest.TestCase):
    def test_daily_prompt_archives_through_this_script(self):
        text = (ROOT / "company" / "translation" / "findings-daily.md").read_text(encoding="utf-8")
        self.assertIn("findings_outbox_archive.py", text)
        self.assertIsNone(re.search(r"git log -1 --format=%H FETCH_HEAD -- translations/outbox", text),
                          "브랜치 커밋을 고르는 옛 정리 규칙이 남아 있다")


if __name__ == "__main__":
    unittest.main()
