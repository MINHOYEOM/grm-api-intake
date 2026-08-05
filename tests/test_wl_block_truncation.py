#!/usr/bin/env python3
"""WL 블록 절단 수리(2026-08-04) 계약 테스트 -- findings_extractors._WL_BLOCK_CHAR_CAP.

배경: FDA 경고서한을 위반 단위로 쪼갠 조각 2,441건 중 1,913건(78%)이
`findings_extractors.py` 의 `_WL_BLOCK_CHAR_CAP = 480` 하드캡 때문에 문장 중간에서 잘려
있었다("...Full release testing, which…" 류). 480 의 근거는 도입 커밋(6a224484, M11)의
"벽텍스트 방지" 주석 한 줄뿐이었고 -- 그 목적은 이미 web/templates/findings.html 의 3줄
클램프+"자세히 보기" 토글이 화면에서 담당한다. 캡을 걷어내고 재현한 실측 조각 길이 분포는
중앙값 2,198·p90 6,119·p99 11,415·최대 21,372자.

이 파일은 **findings_extractors.py 를 고치지 않는다.** 그 수리(다른 담당 작업, 이제
착지 확인됨 -- `_WL_BLOCK_CHAR_CAP == 6000`, 문장 경계 절단, 분류는 전체 본문을 봄)가
지켜야 할 계약을 테스트로 고정해 재발을 막는다:

  1. 상한을 넘는 블록은 문장 경계에서 자른다(단어/문장 중간 절단 금지).
  2. 결과 길이는 상한을 지킨다.
  3. 상한 이내이고 이미 문장부호로 끝나는 블록은 한 글자도 안 바뀐다.
  4. 상한 이내인데 미완결(문장부호 없음)인 블록에는 "…" 가 붙는다(기존 §A-2 동작 보존).
  5. 상한 이내에 문장 종결이 하나도 없는 극단 블록은 단어 경계 안전망으로 되돌아가고
     예외가 나지 않는다.
  6. ★회귀 가드 -- 상한 상수 자체가 480 으로 되돌아가면 실패한다.
  7. ★분류(category_code)는 표시용(캡 적용 후) 텍스트가 아니라 캡 이전 전체 본문을 본다.
  8. 위 표 기반 단언들이 빈 케이스에서 조용히 통과하지 않도록 0건 가드를 둔다.
  9. 커밋된 실 경고서한 2건(공개 FDA 문서, tests/fixtures/wl_truncation_real_bodies.json)
     으로 합성 픽스처가 놓칠 수 있는 실제 문형(곱은따옴표·불릿·"(b)(4)" 편집구)에서도
     절단이 깨끗한지 확인한다. DB 호출 없음 -- 커밋된 파일만 읽는다.

tests/test_findings_wl_scope.py(마이그레이션 SQL 계약)·tests/test_findings_extractors.py
(WL 분해 스펙 전반)와 동형의 오프라인 전용 테스트 -- 실 네트워크·실 DB 접근 없음.
"""

from __future__ import annotations

import json
import os
import unittest

import findings_extractors as extractors
import grm_findings as gf


GOLDEN = os.path.join(os.path.dirname(__file__), "golden")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

# 합성 픽스처를 만들 때 상한(현재 6000)에 여유 있게 초과하도록 동적으로 크기를 잡는다 --
# 상한 상수가 바뀌어도(테스트 6 이 그 변경을 사람이 검토하게 강제한다) 이 파일의 나머지
# 테스트가 "우연히 상한 밑에 들어와 아무것도 검증 못 하는" 상태로 조용히 무너지지 않는다.
_CAP = extractors._WL_BLOCK_CHAR_CAP
_SENTENCE_TERMINALS = ".。!?\"'”’)]"


def _load_input(name: str) -> dict:
    with open(os.path.join(GOLDEN, f"{name}.input.json"), encoding="utf-8") as f:
        return json.load(f)


def _wl_findings(body_full: str, row_overrides: dict | None = None) -> list[dict]:
    """warning_letter_excerpt 골든 fixture 를 템플릿으로 wl_body_full 만 갈아 끼운다.

    tests/test_findings_extractors.py 의 `_wl_findings` 헬퍼와 동형(독립 파일이라 재정의).
    """
    fx = _load_input("warning_letter_excerpt")
    raw = dict(fx["raw"])
    raw["wl_body_full"] = body_full
    raw["wl_body_excerpt"] = ""
    row = dict(fx["row"])
    if row_overrides:
        row.update(row_overrides)
    raw_signal = gf.raw_signal_from_row(row, raw)
    return extractors.findings_from_raw_signal(raw_signal)


class WlBlockCapContractTest(unittest.TestCase):
    """항목 1~5 -- 절단 로직 자체의 계약(합성 픽스처, 공개 함수 경로로만 검증)."""

    def assertValidFindings(self, findings: list[dict]) -> None:
        self.assertTrue(findings, "0건 가드: 이 테스트가 검증할 finding 이 하나도 없다")
        for finding in findings:
            self.assertEqual(gf.validate_finding(finding), [])

    # ------------------------------------------------------------------
    # 1) 문장 경계 절단
    # ------------------------------------------------------------------
    def test_over_cap_block_cuts_at_sentence_boundary(self) -> None:
        sentence = (
            "Investigators found that the batch record did not include a signed "
            "verification for the cleaning step performed prior to filling."
        )
        repeat = (_CAP // len(sentence)) + 20  # 상한을 넉넉히 넘도록
        block1 = " ".join([sentence] * repeat)
        self.assertGreater(len(block1), _CAP, "테스트 전제 붕괴: 합성 블록이 상한보다 짧다")

        body = (
            "During our inspection, our investigators observed specific violations "
            "including, but not limited to, the following: "
            f"1. {block1} "
            "2. Your firm failed to correct the deficiency within the response "
            "period (21 CFR 211.22)."
        )

        findings = _wl_findings(body)
        self.assertValidFindings(findings)
        self.assertEqual(len(findings), 2, "번호 리스트 2건이 2 findings 로 분해돼야 한다")

        text = findings[0]["finding_text"]
        core = text[:-1] if text.endswith("…") else text
        # 절단이 실제로 일어났는지(전제 확인) + 원문의 정확한 접두어인지(내용 왜곡 없음).
        self.assertLess(len(core), len(block1), "블록이 상한을 넘었는데 절단이 일어나지 않았다")
        self.assertTrue(block1.startswith(core), "절단 결과가 원문 접두어가 아니다 — 내용이 훼손됐다")
        # 핵심 계약: 문장 중간이 아니라 문장 종결부호에서 끊겨야 한다.
        self.assertTrue(
            core and core[-1] in _SENTENCE_TERMINALS,
            f"문장 중간에서 잘렸다(회귀!) — 결과 끝: {core[-60:]!r}",
        )
        # 실측 결함 패턴("...which…" 처럼 종결부호 없이 단어에서 바로 끊김)이 재발하지
        # 않았는지 구체적으로 확인 -- 문장 템플릿의 마지막 단어가 온전히 보존돼야 한다.
        self.assertTrue(core.endswith("filling."), f"문장이 온전히 안 남았다: {core[-30:]!r}")

    # ------------------------------------------------------------------
    # 2) 상한 준수
    # ------------------------------------------------------------------
    def test_capped_result_stays_within_the_limit(self) -> None:
        sentence = "The equipment cleaning log lacked a supervisor signature for this batch."
        repeat = (_CAP // len(sentence)) + 20
        block1 = " ".join([sentence] * repeat)
        body = (
            "During inspection FDA identified the following: "
            f"1. {block1} "
            "2. Your firm failed to correct the deficiency (21 CFR 211.22)."
        )

        findings = _wl_findings(body)
        self.assertValidFindings(findings)
        for finding in findings:
            # +1 은 절단 시 붙는 "…" 한 글자 여유(단어 경계 되돌림·미완결 표시 공통).
            self.assertLessEqual(
                len(finding["finding_text"]),
                _CAP + 1,
                f"finding_text 가 상한을 넘었다: {len(finding['finding_text'])} > {_CAP}",
            )

    # ------------------------------------------------------------------
    # 3) 짧은 블록 무변형
    # ------------------------------------------------------------------
    def test_short_terminated_blocks_are_byte_for_byte_unchanged(self) -> None:
        body = (
            "During inspection FDA identified the following. "
            "1. Your firm failed to validate the sterilization cycle for critical "
            "components (21 CFR 211.113). "
            "2. Your firm failed to maintain complete batch production records for "
            "lot review before release (21 CFR 211.188)."
        )

        findings = _wl_findings(body)
        self.assertValidFindings(findings)
        self.assertEqual(len(findings), 2)
        self.assertEqual(
            findings[0]["finding_text"],
            "Your firm failed to validate the sterilization cycle for critical "
            "components (21 CFR 211.113).",
        )
        self.assertEqual(
            findings[1]["finding_text"],
            "Your firm failed to maintain complete batch production records for "
            "lot review before release (21 CFR 211.188).",
        )

    # ------------------------------------------------------------------
    # 4) 미완결 원문 표시 유지(§A-2 기존 동작)
    # ------------------------------------------------------------------
    def test_incomplete_block_within_cap_keeps_ellipsis_marker(self) -> None:
        body = (
            "During inspection FDA identified the following. "
            "1. Your firm failed to validate the sterilization cycle for critical "
            "components (21 CFR 211.113). "
            "2. Your firm failed to maintain complete batch production records for "
            "lot review before release"  # 의도적으로 문장부호 없이 끝남
        )

        findings = _wl_findings(body)
        self.assertValidFindings(findings)
        self.assertEqual(len(findings), 2)
        second = findings[1]["finding_text"]
        self.assertTrue(second.endswith("…"), f"미완결 블록에 절단 표시가 없다: {second!r}")
        self.assertEqual(
            second[:-1],
            "Your firm failed to maintain complete batch production records for "
            "lot review before release",
            "미완결 블록 본문이 '…' 부착 외에 변형됐다",
        )

    # ------------------------------------------------------------------
    # 5) 문장 종결이 전혀 없는 극단 블록 -- 단어 경계 안전망
    # ------------------------------------------------------------------
    def test_extreme_block_with_no_sentence_terminal_falls_back_to_word_boundary(self) -> None:
        token_count = (_CAP // 10) + 200  # "TOKENnnnn " ~10자 * 개수 > 상한
        tokens = " ".join(f"TOKEN{i:04d}" for i in range(token_count))
        self.assertGreater(len(tokens), _CAP)

        body = (
            "During our inspection, our investigators observed the following: "
            "1. Your firm failed to maintain adequate cleaning records for "
            f"equipment including {tokens}: "  # 문장부호(.?!) 전무, 콜론만 -- 안전망 유도
            "2. Your firm failed to investigate a customer complaint related to "
            "product contamination (21 CFR 211.198). The complaint file did not "
            "include a documented root cause."
        )

        try:
            findings = _wl_findings(body)
        except Exception as exc:  # noqa: BLE001 -- 스펙 요구사항: 예외가 나면 안 된다
            self.fail(f"문장 종결 없는 극단 블록에서 예외 발생: {exc!r}")

        self.assertValidFindings(findings)
        self.assertEqual(len(findings), 2)
        text = findings[0]["finding_text"]
        self.assertLessEqual(len(text), _CAP + 1)
        self.assertTrue(text.endswith("…"), f"미완결 표시가 없다: {text[-20:]!r}")
        core = text[:-1]
        # 단어 경계에서 되돌렸는지 -- 마지막 토큰이 "TOKEN0123" 처럼 온전해야 한다
        # (예: "TOKEN012" 로 반 토막 나면 단어 중간 절단 = 회귀).
        last_token = core.rsplit(" ", 1)[-1]
        self.assertRegex(
            last_token,
            r"^TOKEN\d{4}$",
            f"단어 중간에서 잘렸다(안전망 실패) — 마지막 토큰: {last_token!r}",
        )
        # core 전체가 원문의 정확한 접두어인지(토큰이 하나도 훼손되지 않았는지) 전수 확인.
        prefix_source = (
            "Your firm failed to maintain adequate cleaning records for "
            f"equipment including {tokens}:"
        )
        self.assertTrue(
            prefix_source.startswith(core),
            "절단 결과가 원문 접두어가 아니다 — 토큰이 훼손됐다",
        )


class WlBlockCapRegressionAndClassificationTest(unittest.TestCase):
    """항목 6·7·8 -- 상한 상수 회귀 가드 + 분류가 전체 본문을 보는지."""

    # ------------------------------------------------------------------
    # 6) ★회귀 가드 -- 480 으로 되돌아가면 실패
    # ------------------------------------------------------------------
    def test_cap_constant_has_not_regressed_to_480(self) -> None:
        """캡을 걷어내고 재현한 실측 조각 길이 분포(_split_wl_violation_blocks +
        _wl_block_parts, raw_signals.wl_body_full 표본)의 **p90 이 6,119자**였다
        (중앙값 2,198 · p99 11,415 · 최대 21,372). 즉 위반 문단의 90%가 6,000자 밑에서
        자연히 끝난다 -- 그 위는 번호/헤딩 앵커가 성긴 편지에서 여러 위반이 한 블록으로
        미분해된 경우일 가능성이 크므로(캡을 완전히 없애면 그런 미분해 덩어리가 "조각인
        척" 그대로 들어온다) 6000 으로 잡는다. 이 값이 480 으로 되돌아가거나 근거 없이
        재조정되면 이 테스트가 실패해 사람이 다시 판단해야 한다.
        """
        self.assertEqual(
            extractors._WL_BLOCK_CHAR_CAP,
            6000,
            "WL 블록 표시 상한이 6000 이 아니다 — 480 회귀거나 무근거 재조정이다. "
            "바꾸려면 새 실측 분포(p90/p99)를 근거로 사람이 재검토해야 한다.",
        )

    # ------------------------------------------------------------------
    # 7) ★분류는 전체 본문을 본다(캡 적용 후 표시 텍스트가 아니라)
    # ------------------------------------------------------------------
    def test_classification_reads_the_full_body_not_the_capped_display_text(self) -> None:
        # 캡의 몇 배(여유 2x + 2000자)에 달하는 무의미 채움 문장 -- taxonomy 어휘를
        # 하나도 안 갖도록 손으로 검증한 문장(교육/설비/오염/무균/기록/컴퓨터화/공정 등
        # 20개 카테고리 키워드·패턴과 전부 무관 -- "other_quality_system" 캐치올로만 떨어짐).
        filler_sentence = (
            "Your firm failed to respond to the agency within the required "
            "timeframe following the close of the on-site assessment conducted "
            "by the review team."
        )
        # ★채움 길이는 **분류 상한(480)과 표시 상한(6000) 사이**에 키워드가 놓이도록 잡는다.
        # 그래야 "표시엔 보이는데 분류엔 안 잡힌다"는 이번 계약을 시험할 수 있다.
        # (옛 픽스처는 표시 상한도 480 이던 시절 기준이라 키워드가 6000자 너머로 밀려나
        # 표시문에서도 잘렸다 — 전제가 무너져 아무것도 증명 못 하는 상태였다.)
        classify_cap = extractors._WL_CLASSIFY_CHAR_CAP
        target_len = classify_cap * 3
        filler = filler_sentence
        while len(filler) < target_len:
            filler += " " + filler_sentence
        self.assertGreater(len(filler), classify_cap,
                           "채움 텍스트가 분류 상한보다 짧다(전제 붕괴)")
        self.assertLess(len(filler) + 400, _CAP,
                        "채움+키워드가 표시 상한을 넘는다 — 표시문에서도 잘려 전제가 무너진다")

        # 분류 신호 문장 -- environmental_monitoring 전용 어휘("environmental
        # monitoring"/"cleanroom")만 담고, 앞선 카테고리(data_integrity 등)의 어휘와는
        # 안 겹친다. 채움 뒤에 붙여 상한 **너머**에만 존재하게 만든다.
        keyword_sentence = (
            "Environmental monitoring data for the cleanroom showed atypical "
            "trends that went unaddressed."
        )

        body_with_keyword = (
            "During our inspection, our investigators observed the following: "
            f"1. {filler} {keyword_sentence} "
            "2. Your firm failed to correct the deficiency within the response "
            "period (21 CFR 211.22)."
        )
        body_without_keyword = (
            "During our inspection, our investigators observed the following: "
            f"1. {filler} "
            "2. Your firm failed to correct the deficiency within the response "
            "period (21 CFR 211.22)."
        )

        with_kw = _wl_findings(body_with_keyword)
        without_kw = _wl_findings(body_without_keyword)
        self.assertTrue(with_kw, "0건 가드: 분류 대상 finding 이 없다(with keyword)")
        self.assertTrue(without_kw, "0건 가드: 분류 대상 finding 이 없다(without keyword)")

        kw_text = with_kw[0]["finding_text"]
        kw_category = with_kw[0]["category_code"]
        control_category = without_kw[0]["category_code"]

        # 통제군: 순수 채움 문장만으로는 신호가 없어 캐치올로 떨어진다는 것부터 확인
        # (아래 kw_category 차이가 우연이 아니라 진짜 키워드 때문임을 보장).
        self.assertEqual(
            control_category,
            "other_quality_system",
            "채움 문장 자체가 의도치 않은 taxonomy 신호를 갖고 있다 — 픽스처를 다시 골라야 한다",
        )

        # ★계약이 뒤집혔다(2026-08-05). 처음엔 "분류가 캡 이전 전체 본문을 봐야 한다"고
        # 단언했는데, 전 조각 실측이 그 설계를 반증했다:
        #   · 분류 입력을 표시문(상한 6000)으로 넓히면 2,631건 중 **1,282건(48.7%)** 재분류
        #   · 전체 본문으로 넓히면 1,288건 재분류
        #   · 표본 27건을 읽어보니 새 근거가 대부분 **FDA 표준 시정권고 정형문**이었다
        #     ("customer notifications and product recalls... CAPA" 8건 등) — 위반문이 아니다.
        # 즉 옛 480자 절단은 우연히 **정형문 차단기** 노릇을 하고 있었다.
        # 그래서 이 수리는 **표시만 늘리고 분류는 옛 480자 절단본에 묶어 둔다**
        # (findings_extractors._legacy_cap_for_classify / _WL_CLASSIFY_CHAR_CAP).
        #
        # 아래 두 단언이 그 계약이다: 표시문은 키워드를 담을 만큼 길어졌는데도(전제),
        # 분류는 그 키워드를 **반영하지 않아야** 한다.
        self.assertIn(
            "cleanroom",
            kw_text.lower(),
            "표시 텍스트가 캡 너머 키워드를 담지 못했다 — 상한 인상이 반영되지 않았다(전제 붕괴)",
        )
        self.assertGreater(
            len(kw_text),
            extractors._WL_CLASSIFY_CHAR_CAP,
            "표시 텍스트가 분류 상한보다 짧다 — 이 테스트가 아무것도 증명하지 못한다(전제 붕괴)",
        )
        self.assertEqual(
            kw_category,
            control_category,
            "분류가 분류 상한 너머 키워드를 집었다 — 표시 상한 인상이 분류까지 바꾸고 있다"
            "(정형문 오염 위험: 실측 1,282건/48.7% 재분류). 분류는 옛 480자 절단본만 봐야 한다.",
        )

    # ------------------------------------------------------------------
    # 8) 0건 가드 자체가 살아 있는지(가드가 무력한 no-op 이 아님을 직접 증명)
    # ------------------------------------------------------------------
    def test_zero_finding_guard_actually_fails_on_empty_input(self) -> None:
        with self.assertRaises(AssertionError):
            WlBlockCapContractTest().assertValidFindings([])


class WlBlockCapRealLetterFixtureTest(unittest.TestCase):
    """항목 9 -- 커밋된 실 경고서한 2건으로 절단이 실제 문형에서도 깨끗한지 확인.

    DB 호출 없음 -- tests/fixtures/wl_truncation_real_bodies.json 만 읽는다(공개 FDA
    Warning Letter 본문, 출처·document_id 는 fixture 파일 자체의 "_provenance"/"note"
    필드에 남겨뒀다).
    """

    @classmethod
    def setUpClass(cls) -> None:
        path = os.path.join(FIXTURES, "wl_truncation_real_bodies.json")
        with open(path, encoding="utf-8") as f:
            cls.data = json.load(f)

    def _assert_clean_cut(self, full_block: str, capped_text: str, label: str) -> str:
        """단어 중간 절단이 아닌지 확인하고 상태를 반환한다("unchanged"/"sentence"/"word").

        "word"(단어 경계까지만)도 실패로 치지 않는다 -- 이 편지 중 하나(Janssen)는 원문
        자체가 푸터 절단 직전에 마침표 없이 끝나는 정당한 §4 미완결 케이스를 담고 있어,
        모든 조각에 문장 경계를 강제하면 그 정당한 사례에서 오탐한다. 대신 호출부가 이
        반환값을 모아 **적어도 한 건은 진짜 문장 경계 절단**이었는지 별도로 단언한다
        (word 만 계속 나오면 상한 절단 자체가 real fixture 에서 전혀 발동 안 했다는
        뜻이라 이 테스트가 아무것도 증명 못 한다).
        """
        core = capped_text[:-1] if capped_text.endswith("…") else capped_text
        self.assertTrue(
            full_block.startswith(core), f"{label}: 캡 결과가 원문 접두어가 아니다"
        )
        if core == full_block:
            return "unchanged"  # 절단 없음(§4 무변형 계약)
        sentence_ok = bool(core) and core[-1] in _SENTENCE_TERMINALS
        next_char = full_block[len(core)] if len(full_block) > len(core) else ""
        word_boundary_ok = next_char == "" or next_char.isspace()
        self.assertTrue(
            sentence_ok or word_boundary_ok,
            f"{label}: 단어 중간 절단(회귀) — 끝: {core[-50:]!r} / 원문 다음 글자: {next_char!r}",
        )
        return "sentence" if sentence_ok else "word"

    def test_real_warning_letters_decompose_without_mid_word_truncation(self) -> None:
        letters = self.data.get("letters", [])
        # 0건 가드: fixture 자체가 비어 있으면(예: 파일 손상) 아래 for 루프의 단언들이
        # 조용히 통과하는 사고를 막는다.
        self.assertGreater(len(letters), 0, "실 경고서한 fixture 가 비어 있다")

        cut_status_counts = {"unchanged": 0, "sentence": 0, "word": 0}

        for letter in letters:
            body = letter["wl_body_full"]
            document_id = letter["document_id"]

            findings = _wl_findings(body)
            self.assertGreater(
                len(findings), 0,
                f"실 경고서한 {document_id} 에서 finding 이 0건 — 0건 가드",
            )
            for finding in findings:
                self.assertEqual(gf.validate_finding(finding), [])

            # 추출기 내부 경로를 독립적으로 재현해 (full_block, capped_text) 쌍을 얻는다
            # -- findings_extractors 의 공개 헬퍼만 호출한다(수정 없음, 읽기 전용 사용).
            cut = extractors._cut_wl_footer(body) or body
            raw_blocks = extractors._split_wl_violation_blocks(cut)
            parts = [p for p in (extractors._wl_block_parts(b) for b in raw_blocks) if p]
            payload = [p for p in parts if extractors._wl_block_is_regulatory(p[0])]
            self.assertGreater(
                len(payload), 0,
                f"실 경고서한 {document_id}: 재현한 (full_block, capped) 쌍이 0건 — 0건 가드",
            )

            for i, (full_block, capped_text, _classify_src) in enumerate(payload):
                status = self._assert_clean_cut(
                    full_block, capped_text, f"{document_id} block {i}"
                )
                cut_status_counts[status] += 1
                # findings_from_raw_signal 의 실제 출력과 일치하는지도 함께 확인(경로 일치).
                self.assertEqual(
                    findings[i]["finding_text"], capped_text,
                    f"{document_id} block {i}: 파이프라인 출력이 재현값과 다르다",
                )

        # 이 fixture(Sterling Pharmaceutical Services 편지의 항목 1·3)는 상한을 실제로
        # 넘는 블록을 담고 있어야 한다 -- 전부 "unchanged" 라면 상한 절단 경로 자체가
        # 발동하지 않은 것이라 이 real-data 테스트가 아무것도 증명하지 못한다.
        self.assertGreater(
            cut_status_counts["sentence"], 0,
            f"실 데이터에서 문장 경계 절단이 한 번도 발동하지 않았다(상한 절단 미검증): "
            f"{cut_status_counts}",
        )


if __name__ == "__main__":
    unittest.main()
