"""Reject new Notion API usage in repository Python and YAML files."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_USAGE = (
    "NOTION_" + "TOKEN",
    "api." + "notion.com",
    "notion_" + "client",
)


class RepositoryInventoryTest(unittest.TestCase):
    def test_no_notion_usage(self) -> None:
        patterns = [arg for marker in FORBIDDEN_USAGE for arg in ("-e", marker)]
        result = subprocess.run(
            ["git", "grep", "-n", "-F", *patterns, "--", "*.py", "*.yml", "*.yaml"],
            cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)


class PublishPathInstructionsTest(unittest.TestCase):
    """클라우드 Routine 이 읽는 발행 길 지시문 — off 뒤 노션을 읽거나 쓰라고 하지 않는다."""

    FILES = [("company", "editorial", f) for f in ("RUN.md", "desk.md", "reporter.md", "copyedit.md")] + \
            [("company", "editorial", "next", f) for f in ("RUN.md", "desk.md", "reporter.md", "copyedit.md")] + \
            [("company", "situation-room", "brief-prep.md"),
             ("company", "editorial", "backup.md")]          # 백업 v17 — 노션 없이 도는 월요일 자동 백업
    FORBIDDEN = (
        "CONSUMED` 로 바꾼다",          # 교열의 노션 마감
        "OPEN(Status=New)",             # 데스크의 노션 OPEN 확인
        "백업이 발행",                   # 꺼진 v16 백업에 기대는 문장
        "백업 Routine 이 발행",
        "09:30 백업",
        "collection://",                # 노션 data source 조회
        "`CONSUMED` 를 찍",
        "`CONSUMED` 뒤에",
        "반납 길: notion` 인 호는 종전 순서",
    )

    def test_no_notion_dependence(self) -> None:
        problems = []
        for parts in self.FILES:
            text = ROOT.joinpath(*parts).read_text(encoding="utf-8")
            for phrase in self.FORBIDDEN:
                if phrase in text:
                    problems.append(f"{'/'.join(parts)}: {phrase!r}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_live_and_next_copies_say_the_same_rules(self) -> None:
        """실발행 판과 next/ 판의 노션 은퇴 핵심 문장이 같다(한쪽만 고치면 리허설과 실발행이 갈린다)."""
        for f, phrases in (("RUN.md", ["**Notion 은 발행 길에 없다**"]),
                           ("copyedit.md", ["**Notion 마감은 없다.**", "3-3. **2차 마감 전 다음 할 일"])):
            live = ROOT.joinpath("company", "editorial", f).read_text(encoding="utf-8")
            nxt = ROOT.joinpath("company", "editorial", "next", f).read_text(encoding="utf-8")
            for p in phrases:
                with self.subTest(f=f, p=p):
                    self.assertIn(p, live)
                    self.assertIn(p, nxt)


if __name__ == "__main__":
    unittest.main()
