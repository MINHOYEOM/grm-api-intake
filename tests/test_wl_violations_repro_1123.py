"""경고서한 `wl_violations` 빈 목록 47행 재현 — 2026-11-23 데이터품질팀 (수집완전성 점검 9-2).

표본은 `tests/fixtures/wl_violations_1123/` 에 있다. 본문은 DB `raw_signals.raw_json.wl_body_full`
원문 그대로이고, 기대 위반항목의 `statement` 는 그 본문에서 **잘라 낸 원문 문장**이다(공백만 정규화,
요약·의역·추측 0). 형식 명세와 행별 원인은 공유 노트 `wl_violations_repro_1123.md`.

상태(`status`)별 계약:
  passes_now       현행 파서가 이미 뽑는다(A 묶음 7행 — DB 가 빈 까닭은 10-28 이전 백필 경로).
  must_stay_empty  저장 본문에 위반 표제가 없다 → 빈 목록이 정답(지어내기 금지).
  fails_now        새 형식(F1b 대괄호·F2 API·F3 단일 표제·F4 법 조항 판정 문장). 화요일 수리 전까지
                   `expectedFailure`. 수리 후 이 파일의 `_FIXED_FORMATS` 에 형식을 넣으면 일반 시험이 된다
                   (넣지 않고 고치면 unexpected success 로 CI 가 빨개진다 — 일부러 그렇게 했다).
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import collect_fda_backfill as fb  # noqa: E402
import collect_intake as ci  # noqa: E402

_FIX = os.path.join(_ROOT, "tests", "fixtures", "wl_violations_1123")

# 수리가 끝난 형식을 여기에 적는다(예: {"F1b", "F2"}). 비어 있으면 fails_now 표본은 전부 예상 실패.
_FIXED_FORMATS: set[str] = set()


def _load_samples() -> list[dict]:
    with open(os.path.join(_FIX, "samples.json"), encoding="utf-8") as f:
        return json.load(f)["samples"]


def _load_normal() -> list[dict]:
    with gzip.open(os.path.join(_FIX, "normal25.json.gz"), "rt", encoding="utf-8") as f:
        return json.load(f)["rows"]


def _norm(s: str) -> str:
    return " ".join(s.split())


def _check(tc: unittest.TestCase, sample: dict) -> None:
    got = ci.extract_wl_violations_from_text(sample["wl_body_full"])
    exp = sample["expected"]
    tc.assertEqual([g.get("number") for g in got], [e["number"] for e in exp], "번호 목록")
    tc.assertEqual([g.get("statement") for g in got], [e["statement"] for e in exp], "원문 문장")
    if sample["format"] in ("F1", "F1b"):
        # 기존 계약(21 CFR 표제) — citation 문자열까지 같아야 한다.
        tc.assertEqual([g.get("citation") for g in got], [e["citation"] for e in exp], "citation")
    else:
        # 새 형식 — 소제목은 원문 그대로, citation 은 기대 조항 토큰을 모두 담기만 하면 된다(표기 형식은 개발팀 재량).
        for g, e in zip(got, exp):
            tc.assertEqual(g.get("heading", ""), e["heading"], "소제목(원문 그대로)")
            for tok in [t for t in e["citation"].split(" · ") if t]:
                tc.assertIn(tok.replace("FD&C Act ", ""), g.get("citation", ""), "조항 토큰")


class FixtureIntegrityTest(unittest.TestCase):
    """기대 문장이 정말 원문에 있는지 — 표본을 손으로 고치다 지어낸 문장이 섞이는 것을 막는다."""

    def test_expected_statements_are_verbatim(self) -> None:
        for s in _load_samples():
            body = _norm(s["wl_body_full"])
            for e in s["expected"]:
                with self.subTest(doc=s["document_id"], n=e["number"]):
                    self.assertIn(e["statement"], body)
                    if e.get("heading"):
                        self.assertIn(e["heading"], body)

    def test_normal_fixture_size(self) -> None:
        self.assertEqual(len(_load_normal()), 25)


class NormalExtractionRegressionTest(unittest.TestCase):
    """정상 추출 25건(최근 61일 적재) — 현행 main 파서 출력 고정. 새 형식을 넣다 기존 표제가 바뀌면 여기서 걸린다."""

    def test_normal25_unchanged(self) -> None:
        for r in _load_normal():
            with self.subTest(doc=r["document_id"]):
                self.assertEqual(ci.extract_wl_violations_from_text(r["wl_body_full"]), r["expected"])


class BlankPassesNowTest(unittest.TestCase):
    """A 묶음 — 파서는 이미 뽑는다. 신규 행은 백필도 build_wl_item 을 쓰므로 위반항목이 붙는다."""

    def test_parser_extracts_a_rows(self) -> None:
        for s in _load_samples():
            if s["status"] == "passes_now":
                with self.subTest(doc=s["document_id"]):
                    _check(self, s)

    def test_backfill_row_now_carries_violations(self) -> None:
        # 10-28 이전 `_wl_item_from_row` 는 wl_body_full 만 싣고 추출을 안 불렀다(DB 770행이 그 흔적).
        s = next(x for x in _load_samples() if x["document_id"] == "0af7c14e5949")
        item = fb._wl_item_from_row(
            {"firm": s["firm"], "posted_date": s["published_date"], "letter_date": "",
             "issuing_office": s["issuing_office"], "subject": s["subject"],
             "wl_href": "https://www.fda.gov/warning-letters/fixture-0af7c14e5949"},
            "keep", None, (s["wl_body_full"], ci.WL_BODY_STATUS_OK))
        self.assertEqual(item.raw_payload.get("wl_violations"), s["expected"])

    def test_no_fabrication_when_heading_absent(self) -> None:
        for s in _load_samples():
            if s["status"] == "must_stay_empty":
                with self.subTest(doc=s["document_id"]):
                    self.assertEqual(ci.extract_wl_violations_from_text(s["wl_body_full"]), [])


def _make_case(sample: dict):
    def test(self: unittest.TestCase) -> None:
        _check(self, sample)
    test.__doc__ = f"{sample['document_id']} {sample['group']} {sample['format']} — {sample['note']}"
    if sample["format"] not in _FIXED_FORMATS:
        test = unittest.expectedFailure(test)
    return test


class BlankNewFormatReproTest(unittest.TestCase):
    """B·C 묶음 + A 의 대괄호 1행 — 화요일 개발팀 수리 대상(현재 예상 실패)."""


for _s in _load_samples():
    if _s["status"] == "fails_now":
        setattr(BlankNewFormatReproTest, f"test_{_s['group'].replace('-', '_')}_{_s['document_id']}", _make_case(_s))
del _s


if __name__ == "__main__":
    unittest.main()
