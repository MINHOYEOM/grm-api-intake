"""프롬프트 ↔ 조립기 계약 검사 + `hook_line` 선택 슬롯 동작.

[2026-11-09 배경] 11-09 주간 브리프 편집팀 교열이 코드 게이트("en 에 모르는 필드 hook_line")에
걸려 보류됐다. 편집팀 문서는 카드 슬롯 `en.hook_line` 을 쓰라고 안내하는데 조립기
(`inject_slots._EN_FIELDS`)는 그 이름을 몰랐다 — 문서와 코드가 어긋난 채 아무도 못 봤다.
(한국어 `hook_line` 은 더 조용했다: 거부도 주입도 없이 델타에서 그냥 버려졌다.)

이 파일은 문서가 카드 슬롯으로 **안내하는 필드 이름**을 뽑아, 조립기가 실제로 받아 카드에
싣는지 **행동으로** 확인한다(이름 목록 대조가 아니라 주입해 보고 카드에서 찾는다).

문서에서 이름을 뽑는 곳은 두 군데뿐이다(넓게 긁으면 오탐이 난다).
  1) 카드 슬롯 JSON 예시 — `"<…card_id>": {` 또는 `"…-xxxx": {` 로 시작하는 카드 객체의 키
     (v16 [출력] envelope 가 대표. 최상위 키 = 한국어 슬롯, `"en": {…}` 안의 키 = 영문 슬롯).
  2) 편집팀 문서 산문의 `cards.{id}.필드` · `cards.{id}.en.필드` 표기.
"""
from __future__ import annotations

import copy
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inject_slots as inj  # noqa: E402
from test_inject_slots import _build_scaffold, _ids  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITORIAL = os.path.join(ROOT, "company", "editorial")
V16 = os.path.join(ROOT, "docs", "prompts", "GRM_Prompt_v16.md")

# 슬롯 값이 아닌 키 — 이유를 적어 둔다.
_NOT_A_CARD_VALUE = {
    "quotes_translation": "카드 필드가 아니라 quotes[].translation 에 positional 주입 — "
                          "test_inject_slots 가 따로 검증한다",
    "en": "영문 갈래 컨테이너 — 안쪽 키를 별도로 검사한다",
    # 아래 셋은 v16 의 '심층 델타' 예시(카드 id 키 아래)에 있는 키 — 카드 슬롯 델타가 아니라
    # inject_deep_analysis · _merge_ncr_translations · _merge_wl_violation_translations 경로로 들어간다.
    "deep_analysis": "심층 델타 항목 — inject_deep_analysis(근거 게이트 D1~D5) 경로",
    "ncr_ko": "심층 델타 항목 — inject_slots._merge_ncr_translations 경로",
    "violations_ko": "심층 델타 항목 — inject_slots._merge_wl_violation_translations 경로",
}

# 새 필드가 문서에 생기면 여기 값을 더해 '조립기가 받는지' 를 확인하게 한다.
_KO_SAMPLE = {
    "title_issue": "짧은 제목",
    "summary": "요약 한 문장.",
    "implication": "시사점 한 문장.",
    "key_facts": ["라벨: 사실"],
    "checks": ["확인 하나?", "확인 둘?"],
    "hook_line": "근본원인 없이 재시험을 허용함",
}
_EN_SAMPLE = {
    "title_issue": "Short title",
    "summary": "One sentence summary.",
    "implication": "One sentence implication.",
    "key_facts": ["Label: fact"],
    "checks": ["Check one?", "Check two?"],
    "hook_line": "Retesting allowed without root-cause investigation",
}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


_CARD_OBJ_START = re.compile(r'"(<[^">\n]*card_id>|[\w-]+-xxxx)"\s*:\s*\{')


def example_card_fields(text: str) -> tuple[set[str], set[str]]:
    """JSON 예시의 카드 객체에서 (한국어 슬롯 키, en 슬롯 키) 를 뽑는다.

    문자열 리터럴을 건너뛰며 중괄호·대괄호 깊이를 세는 작은 스캐너 — 값에 `...`·한글·콜론이
    섞여 있어도 키 위치만 정확히 본다.
    """
    ko: set[str] = set()
    en: set[str] = set()
    for m in _CARD_OBJ_START.finditer(text):
        stack = [m.group(1)]                      # 카드 객체 안: stack[0] = 카드 키
        i, n, last_key = m.end(), len(text), None
        while i < n and stack:
            ch = text[i]
            if ch == '"':
                j = i + 1
                while j < n and text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
                s = text[i + 1:j]
                k = j + 1
                while k < n and text[k] in " \t\r\n":
                    k += 1
                if k < n and text[k] == ":":      # 문자열 뒤에 ':' = 키
                    last_key = s
                    if len(stack) == 1:
                        ko.add(s)
                    elif len(stack) == 2 and stack[1] == "en":
                        en.add(s)
                    i = k + 1
                    continue
                i = j + 1
                continue
            if ch in "{[":
                stack.append(last_key if ch == "{" and last_key else "[]")
                last_key = None
            elif ch in "}]":
                stack.pop()
            i += 1
    return ko, en


_PROSE_FIELD = re.compile(r"cards\.\{[^}\n]*\}\.(en\.)?([a-z_]+)")


def prose_card_fields(text: str) -> tuple[set[str], set[str]]:
    """`cards.{id}.필드` · `cards.{id}.en.필드` 표기에서 (한국어, en) 필드명."""
    ko: set[str] = set()
    en: set[str] = set()
    for m in _PROSE_FIELD.finditer(text):
        (en if m.group(1) else ko).add(m.group(2))
    return ko, en


def documented_fields() -> dict[str, tuple[set[str], set[str]]]:
    """문서별 (한국어, en) 안내 필드 — 키는 상대 경로."""
    out: dict[str, tuple[set[str], set[str]]] = {}
    paths = [V16] + sorted(
        os.path.join(EDITORIAL, f) for f in os.listdir(EDITORIAL) if f.endswith(".md"))
    for p in paths:
        text = _read(p)
        a_ko, a_en = example_card_fields(text)
        b_ko, b_en = prose_card_fields(text)
        ko, en = a_ko | b_ko, a_en | b_en
        if ko or en:
            out[os.path.relpath(p, ROOT).replace(os.sep, "/")] = (ko, en)
    return out


class ExtractorSelfTest(unittest.TestCase):
    """추출기가 썩어 '아무것도 못 찾고 통과' 하는 일을 막는다."""

    def test_example_scanner_reads_nested_keys(self) -> None:
        text = ('{"cards": {"<web_card_id>": {"title_issue": "a: {b}", "key_facts": ["x", "y"],'
                ' "quotes_translation": ["...", null], "en": {"summary": "s", "hook_line": "h"}}}}')
        ko, en = example_card_fields(text)
        self.assertEqual(ko, {"title_issue", "key_facts", "quotes_translation", "en"})
        self.assertEqual(en, {"summary", "hook_line"})

    def test_prose_pattern(self) -> None:
        ko, en = prose_card_fields("한국어는 `cards.{id}.hook_line`, 영문은 `cards.{id}.en.hook_line`.")
        self.assertEqual((ko, en), ({"hook_line"}, {"hook_line"}))

    def test_v16_example_is_found(self) -> None:
        ko, en = example_card_fields(_read(V16))
        self.assertTrue({"title_issue", "summary", "key_facts", "implication", "checks"} <= ko, ko)
        self.assertTrue({"title_issue", "summary", "key_facts", "implication", "checks"} <= en, en)

    def test_editorial_prose_is_found(self) -> None:
        ko, en = prose_card_fields(_read(os.path.join(EDITORIAL, "reporter.md")))
        self.assertIn("hook_line", ko)
        self.assertIn("hook_line", en)


class PromptAssemblerContractTest(unittest.TestCase):
    """문서가 안내하는 슬롯 필드를 조립기가 받아 카드에 싣는다."""

    def setUp(self) -> None:
        self.brief = _build_scaffold()
        self.cid = _ids(self.brief)["ko_a"]

    def _inject(self, fields_ko: dict, fields_en: dict | None) -> dict:
        d = dict(fields_ko)
        if fields_en is not None:
            d["en"] = dict(fields_en)
        out = inj.inject_llm_slots(self.brief, {"cards": {self.cid: d}}, strict=True)
        return next(c for c in out["cards"] if c["id"] == self.cid)

    def test_every_documented_ko_field_is_accepted_and_injected(self) -> None:
        docs = documented_fields()
        self.assertTrue(docs, "안내 필드를 하나도 못 뽑았다 — 추출기 점검")
        for path, (ko, _en) in docs.items():
            for f in sorted(ko - set(_NOT_A_CARD_VALUE)):
                with self.subTest(doc=path, field=f):
                    self.assertIn(
                        f, _KO_SAMPLE,
                        f"{path} 가 카드 슬롯 `{f}` 를 안내한다. 조립기가 받는지 확인하고 "
                        f"이 테스트 _KO_SAMPLE 에 값을 더하라(허용 목록은 이유와 함께)")
                    card = self._inject({f: _KO_SAMPLE[f]}, None)
                    self.assertEqual(card.get(f), _KO_SAMPLE[f],
                                     f"{path} 가 안내하는 `{f}` 가 조립기에서 버려진다")

    def test_every_documented_en_field_is_accepted_and_injected(self) -> None:
        docs = documented_fields()
        for path, (_ko, en) in docs.items():
            for f in sorted(en):
                with self.subTest(doc=path, field=f):
                    self.assertIn(
                        f, _EN_SAMPLE,
                        f"{path} 가 영문 슬롯 `en.{f}` 를 안내한다. 조립기가 받는지 확인하고 "
                        f"이 테스트 _EN_SAMPLE 에 값을 더하라")
                    # 필수 다섯 + 해당 필드. 조립기가 "모르는 필드" 로 거부하면 여기서 터진다.
                    full = dict(_EN_SAMPLE)
                    card = self._inject({}, {k: full[k] for k in inj._EN_REQUIRED} | {f: full[f]})
                    self.assertEqual(card.get("en", {}).get(f), full[f],
                                     f"{path} 가 안내하는 `en.{f}` 가 조립기에서 거부·버려진다")

    def test_en_fields_set_is_required_plus_optional(self) -> None:
        self.assertEqual(set(inj._EN_FIELDS), set(inj._EN_REQUIRED) | {"hook_line"})


class HookLineSlotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = _build_scaffold()
        self.cid = _ids(self.brief)["ko_a"]

    def _delta(self, **card) -> dict:
        return {"cards": {self.cid: card}}

    def _en_full(self, **extra) -> dict:
        return {**{k: _EN_SAMPLE[k] for k in inj._EN_REQUIRED}, **extra}

    def _card(self, out: dict) -> dict:
        return next(c for c in out["cards"] if c["id"] == self.cid)

    def test_optional_absent_passes(self) -> None:
        rep = inj.validate_injection(self.brief, self._delta(en=self._en_full()))
        self.assertEqual(rep.errors, [])
        card = self._card(inj.inject_llm_slots(self.brief, self._delta(en=self._en_full())))
        self.assertNotIn("hook_line", card["en"])
        self.assertNotIn("hook_line", card)

    def test_ko_and_en_injected(self) -> None:
        d = self._delta(hook_line="재시험을  허용함", en=self._en_full(hook_line="Retest allowed"))
        card = self._card(inj.inject_llm_slots(self.brief, d))
        self.assertEqual(card["hook_line"], "재시험을 허용함")      # 공백 접기
        self.assertEqual(card["en"]["hook_line"], "Retest allowed")

    def test_en_hook_line_does_not_count_as_one_of_the_five(self) -> None:
        # hook_line 만 있고 다섯이 비면 '빈 en' — 부분 제공 오류가 아니라 영어 미발행 경고.
        rep = inj.validate_injection(self.brief, self._delta(en={"hook_line": "Retest allowed"}))
        self.assertEqual(rep.errors, [])
        card = self._card(inj.inject_llm_slots(
            self.brief, self._delta(en={"hook_line": "Retest allowed"})))
        self.assertNotIn("en", card)

    def test_partial_five_still_rejected_with_hook_line(self) -> None:
        en = self._en_full(hook_line="Retest allowed")
        del en["checks"]
        rep = inj.validate_injection(self.brief, self._delta(en=en))
        self.assertTrue(any("부분 제공" in e for e in rep.errors), rep.errors)

    def test_empty_string_means_absent(self) -> None:
        d = self._delta(hook_line="  ", en=self._en_full(hook_line=""))
        self.assertEqual(inj.validate_injection(self.brief, d).errors, [])
        card = self._card(inj.inject_llm_slots(self.brief, d))
        self.assertNotIn("hook_line", card)
        self.assertNotIn("hook_line", card["en"])

    def test_length_markup_type_rejected(self) -> None:
        bad = [
            self._delta(hook_line="가" * 41),
            self._delta(hook_line="**강조** 문장"),
            self._delta(hook_line=["리스트"]),
            self._delta(en=self._en_full(hook_line="x" * 91)),
            self._delta(en=self._en_full(hook_line="<td>cell")),
            self._delta(en=self._en_full(hook_line=3)),
        ]
        for d in bad:
            with self.subTest(d=d):
                self.assertTrue(inj.validate_injection(self.brief, d).errors)

    def test_limits_match_render(self) -> None:
        sys.path.insert(0, os.path.join(ROOT, "web"))
        import render
        self.assertEqual(inj._HOOK_LINE_MAX, dict(render.HOOK_LINE_MAX_CHARS))

    def test_unknown_en_field_still_rejected(self) -> None:
        rep = inj.validate_injection(self.brief, self._delta(en=self._en_full(banner="x")))
        self.assertTrue(any("모르는 필드" in e for e in rep.errors), rep.errors)

    def test_input_not_mutated(self) -> None:
        snap = copy.deepcopy(self.brief)
        inj.inject_llm_slots(self.brief, self._delta(hook_line="재시험을 허용함"))
        self.assertEqual(self.brief, snap)


if __name__ == "__main__":
    unittest.main()
