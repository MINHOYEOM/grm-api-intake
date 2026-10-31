# -*- coding: utf-8 -*-
"""편집팀 3분리 지시문이 v16 을 빠짐없이 가리키는지.

직무 지시문(company/editorial/*.md)은 v16 본문을 옮겨 적지 않고 `- v16: [절 제목]` 줄로
따를 절을 가리킨다. 이 테스트는 두 방향을 막는다:
  1) v16 §B 의 어떤 절도 어느 직무에도 배정되지 않은 채 남지 않는다(규칙 유실).
  2) 지시문이 가리키는 절 제목이 v16 에 실제로 있다(v16 이 고쳐져 가리킴이 끊김).
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V16 = os.path.join(ROOT, "docs", "prompts", "GRM_Prompt_v16.md")
EDITORIAL = os.path.join(ROOT, "company", "editorial")
ROLE_FILES = ("desk.md", "reporter.md", "copyedit.md")

_POINTER = re.compile(r"^- v16: (\[.+\])\s*$")
_HEADING = re.compile(r"^\[[^\]]+\]\s*$")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def v16_section_headings():
    """§B 완성 프롬프트 코드 블록 안에서 한 줄 전체가 `[제목]` 인 절 제목."""
    lines = _read(V16)
    start = next(i for i, l in enumerate(lines) if l.startswith("## B."))
    fence = next(i for i in range(start, len(lines)) if lines[i].startswith("```"))
    end = next(i for i in range(fence + 1, len(lines)) if lines[i].startswith("```"))
    return [l.strip() for l in lines[fence + 1:end] if _HEADING.match(l)]


def role_pointers():
    out = {}
    for name in ROLE_FILES:
        out[name] = [m.group(1) for l in _read(os.path.join(EDITORIAL, name))
                     if (m := _POINTER.match(l))]
    return out


class EditorialCoverageTest(unittest.TestCase):
    def test_v16_has_sections(self):
        self.assertGreaterEqual(len(v16_section_headings()), 20)

    def test_every_v16_section_is_assigned(self):
        assigned = {p for ps in role_pointers().values() for p in ps}
        missing = [h for h in v16_section_headings() if h not in assigned]
        self.assertEqual(missing, [], "어느 직무에도 배정되지 않은 v16 절")

    def test_every_pointer_exists_in_v16(self):
        headings = set(v16_section_headings())
        dangling = {name: [p for p in ps if p not in headings]
                    for name, ps in role_pointers().items()}
        self.assertEqual({k: v for k, v in dangling.items() if v}, {},
                         "v16 에 없는 절을 가리킨다")


if __name__ == "__main__":
    unittest.main()
