#!/usr/bin/env python3
"""web/wl_anatomy.py 테스트 — 경고서한 국문 지적을 회신·판단·요구로 나누는 순수 함수.

CI(`unittest discover -s tests`)는 `tests/test_web_wl_anatomy.py` shim 으로 순회. 직접:
  python web/tests/test_wl_anatomy.py
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import wl_anatomy  # noqa: E402

# 실제 서한(Jabil 2026-09-01) 국문 번역의 문형을 줄여 옮긴 표본 — 소제목 3개, 소제목마다
# 회신·판단 한 주기, 꼬리에 요구 블록. "결함."(명사형 '함' 종결) 소제목과 판단 뒤에 이어지는
# "또한 귀사의 답변은 …않았다." 문장이 함정이다.
SAMPLE = (
    "귀사는 무균 의약품의 미생물 오염을 방지하는 절차를 수립하지 못하였다. "
    "또한 귀사는 건물을 양호한 상태로 유지관리하지 못하였다(21 CFR 211.113(b) 및 21 CFR 211.58). "
    "기류 가시화(AFV) 스모크 스터디의 결함. "
    "당국의 실사에서 스모크 스터디가 미흡한 것이 확인되었다. "
    "답변서에서 귀사는 AFV 시험을 재수행하겠다고 밝혔다. "
    "그러나 귀사의 답변은 향후 스모크 스터디를 어떻게 보증할 것인지 설명하지 않았으므로 미흡하다. "
    "철저한 스모크 스터디는 필수적이다. "
    "시설 유지관리의 결함. "
    "조사관은 다음을 관찰하였다. "
    "벗겨진 벽면 페인트. "
    "바닥의 플라스틱 부스러기. "
    "답변서에서 귀사는 시설을 보수하겠다고 약속하였다. "
    "그러나 귀사의 답변은 근거를 설명하지 않았으므로 미흡하다. "
    "또한 귀사의 답변은 페인트가 벗겨진 원인을 적절히 평가하지 않았다. "
    "본 서한에 대한 답변으로 다음을 제출하라. "
    "독립적 오염 위험평가. "
    "일정이 포함된 시정 계획."
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


class SplitWlFindingTest(unittest.TestCase):
    def setUp(self):
        self.parts = wl_anatomy.split_wl_finding(SAMPLE)
        self.assertIsNotNone(self.parts)

    def test_rejoin_is_lossless(self):
        # FDA 문장만 쓴다 — 나눈 조각을 이으면 원문이어야 한다(한 글자도 고치지 않는다).
        self.assertEqual(_norm(wl_anatomy.rejoin(self.parts)), _norm(SAMPLE))

    def test_lead_takes_the_citation_sentence(self):
        self.assertIn("21 CFR 211.58", self.parts["lead"])
        self.assertTrue(self.parts["lead"].startswith("귀사는 무균"))

    def test_headings_including_noun_ending(self):
        heads = [it["heading"] for it in self.parts["issues"]]
        self.assertEqual(heads, ["기류 가시화(AFV) 스모크 스터디의 결함.", "시설 유지관리의 결함."])

    def test_response_and_judgment_per_issue(self):
        a, b = self.parts["issues"]
        self.assertTrue(a["response"].startswith("답변서에서 귀사는 AFV"))
        self.assertTrue(a["judgment"].startswith("그러나 귀사의 답변은"))
        self.assertIn("철저한 스모크 스터디", a["judgment"])
        # 판단 뒤 "또한 귀사의 답변은 …않았다."는 새 회신이 아니라 판단의 연속이다.
        self.assertIn("원인을 적절히 평가하지 않았다", b["judgment"])
        self.assertNotIn("원인을", b["response"])

    def test_list_items_after_intro_are_not_headings(self):
        b = self.parts["issues"][1]
        self.assertIn("벗겨진 벽면 페인트.", b["observed"])
        self.assertIn("바닥의 플라스틱 부스러기.", b["observed"])

    def test_requests(self):
        self.assertEqual(self.parts["request_head"], "본 서한에 대한 답변으로 다음을 제출하라.")
        self.assertEqual(self.parts["requests"], ["독립적 오염 위험평가.", "일정이 포함된 시정 계획."])


class SplitWlFindingDeclinesTest(unittest.TestCase):
    """확신이 없으면 나누지 않는다 — 화면은 종전 한 문단을 낸다."""

    def test_no_response_cycle(self):
        text = ("귀사는 시험을 수행하지 않았다(21 CFR 211.84). 조사관은 기록이 없음을 확인하였다. "
                "이는 중대한 위반이다.")
        self.assertIsNone(wl_anatomy.split_wl_finding(text))

    def test_response_without_inadequacy(self):
        text = ("귀사는 시험을 수행하지 않았다(21 CFR 211.84). 조사관은 기록이 없음을 확인하였다. "
                "답변서에서 귀사는 시험을 수행하겠다고 밝혔다.")
        self.assertIsNone(wl_anatomy.split_wl_finding(text))

    def test_empty_and_short(self):
        self.assertIsNone(wl_anatomy.split_wl_finding(""))
        self.assertIsNone(wl_anatomy.split_wl_finding("귀사는 위반하였다."))

    def test_truncated_text_keeps_ellipsis(self):
        # 480자 절단 잔여분처럼 "…"로 끝나도 나뉠 수 있으면 나누고, 표시는 그대로 남는다.
        parts = wl_anatomy.split_wl_finding(SAMPLE + "…")
        self.assertIsNotNone(parts)
        self.assertTrue(wl_anatomy.rejoin(parts).endswith("…"))


if __name__ == "__main__":
    unittest.main()
