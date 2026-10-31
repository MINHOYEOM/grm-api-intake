# -*- coding: utf-8 -*-
"""company/ 지시문이 가리키는 저장소 파일이 실제로 있는지.

클라우드 예약작업은 company/ 지시문을 읽고, 지시문은 정본(docs/prompts/…)과 코드를 경로로 가리킨다.
정본이 이름을 바꾸거나 지워지면 예약작업이 조용히 엉뚱한 일을 하므로, 백틱 안의 저장소 경로가 끊기면 CI 가 실패한다.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPANY = os.path.join(ROOT, "company")

# 백틱 안에서 저장소 안을 가리키는 경로만 본다(company/run/… 같은 실행 산출물, `$UNDER` 같은 날짜 자리표시 경로는 제외).
_PATH = re.compile(r"`((?:docs/prompts|company|tests|web/data|\.github/workflows)/[^`\s$<{]+?\.(?:md|py|json|yml))`")
_SKIP = ("company/run/",)


def pointers():
    out = []
    for dirpath, _, files in os.walk(COMPANY):
        if os.path.relpath(dirpath, COMPANY).startswith("run"):
            continue
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                for m in _PATH.finditer(f.read()):
                    if not m.group(1).startswith(_SKIP):
                        out.append((os.path.relpath(path, ROOT), m.group(1)))
    return out


def job_files():
    """ROUTINES.md 표의 지시문 칸(`팀/파일.md`)."""
    with open(os.path.join(COMPANY, "ROUTINES.md"), encoding="utf-8") as f:
        return re.findall(r"^\|[^|]+\|[^|]+\| `([a-z-]+/[a-z-]+\.md)` \|", f.read(), re.M)


class CompanyPointerTest(unittest.TestCase):
    def test_found_pointers(self):
        self.assertGreaterEqual(len(pointers()), 5)

    def test_every_pointer_exists(self):
        missing = [(src, p) for src, p in pointers() if not os.path.exists(os.path.join(ROOT, p))]
        self.assertEqual(missing, [], "company/ 지시문이 없는 파일을 가리킨다")

    def test_every_routine_job_file_exists(self):
        jobs = job_files()
        self.assertGreaterEqual(len(jobs), 6)
        missing = [j for j in jobs if not os.path.exists(os.path.join(COMPANY, j))]
        self.assertEqual(missing, [], "ROUTINES.md 가 없는 지시문을 가리킨다")


if __name__ == "__main__":
    unittest.main()
