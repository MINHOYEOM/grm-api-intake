"""홈 '이번 주 핵심' 문장이 브리프 본문과 같은 글자인지 지키는 가드 (2026-11-26).

버그: 홈은 가운뎃점 줄바꿈을 막으려고 점 앞 **한 글자**만 `<span class="ht-nb">` 로 묶었다
("충<span>전·</span>포<span>장·</span>출하한"). 화면 모양은 같아 보여도 낱말 한가운데에 태그
경계가 생겨 화면 읽기·텍스트 추출에서 "충 전· 포 장·" 또는 "충포출하한"으로 깨졌다.

지키는 것
1. 홈 줄의 태그를 걷으면 브리프 본문(`_brief_tldr` → 그대로 이스케이프)과 글자가 같다.
   실제 발행 브리프 전부(web/data/briefs/*.json, ko·en)로 잰다.
2. 홈 골든 3장(ko·en·다중)의 '이번 주 핵심' 칸에서 태그 경계는 늘 빈칸 자리에만 있다.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
WEB = REPO / "web"
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(REPO))
import render  # noqa: E402

GOLDENS = ("landing.expected.html", "en_landing.expected.html", "landing_multi.expected.html")
_TAG = re.compile(r"<[^>]+>")


def _inner_lines(html: str) -> list[str]:
    if 'class="home-tl"' not in html:
        return []
    block = html.split('class="home-tl"', 1)[1].split("</ul>", 1)[0]
    return re.findall(r'<span class="ht-t">(.*)</span></span></li>', block)


class HomeKeypointSameTextTest(unittest.TestCase):
    def test_tag_free_text_equals_brief_body_text(self):
        briefs = sorted((WEB / "data" / "briefs").glob("brief_web_*.json"))
        self.assertTrue(briefs)
        seen = 0
        for path in briefs:
            meta = (json.loads(path.read_text(encoding="utf-8")).get("brief") or {})
            for lang in ("ko", "en"):
                for line in render._brief_tldr(meta, lang):
                    got = _TAG.sub("", str(render.home_nobreak_middot(line)))
                    self.assertEqual(got, str(render._escape(line)), f"{path.name} {lang}")
                    seen += 1
        self.assertGreater(seen, 0)

    def test_whole_word_is_wrapped_not_one_letter(self):
        self.assertEqual(str(render.home_nobreak_middot("기간 중 충전·포장·출하한 (주)")),
                         '기간 중 <span class="ht-nb">충전·포장·출하한</span> (주)')
        self.assertEqual(str(render.home_nobreak_middot("위조·DEG <b>")),
                         '<span class="ht-nb">위조·DEG</span> &lt;b&gt;')
        self.assertEqual(str(render.home_nobreak_middot("점 없음")), "점 없음")

    def test_goldens_have_no_tag_inside_a_word(self):
        checked = 0
        for name in GOLDENS:
            path = WEB / "tests" / "golden" / name
            if not path.is_file():
                continue
            for inner in _inner_lines(path.read_text(encoding="utf-8")):
                checked += 1
                self.assertIsNone(re.search(r"[^\s>]<span", inner), f"{name}: {inner}")
                self.assertIsNone(re.search(r"</span>[^\s<]", inner), f"{name}: {inner}")
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
