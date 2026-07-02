"""GRM [WL 심층분석 fan-out] 카드별 심층분석(4섹션) 사실 근거(grounding) 결정론 게이트.

배경(2026-07-01): 사용자 요청 — 기존 6종 동결 슬롯(card_spec v16)은 "카드가 조잡하다"는
피드백에 따라 warning-letter 카드에 한해 7번째·선택적 슬롯 `deep_analysis`(Key Violations &
Risk Analysis·FDA's Evaluation of Response·Required Remediation·Administrative Risks &
Special Notes 4섹션 — §2.5 확정으로 Overview 제거)를 fan-out(카드 1건 = 호출 1건, 독립 컨텍스트)
으로 추가한다. fan-out 은 Routine 세션 1개가 전 카드를 처리하는 방식과 달리 카드마다 완전히
분리된 짧은 호출이라, 카드 수가 늘어도 호출당 부하가 커지지 않고 카드 간 내용 혼동이
구조적으로 없다(brief_lint.py §헤더 설명과 동형 원칙: Python 이 결정론으로 검증하고,
LLM 산출물은 발행 전 그 검증을 반드시 통과해야 한다).

이 모듈은 brief_lint.py 와 동일하게 **순수 함수**(네트워크 없음)다: 카드별 `deep_analysis`
JSON 과 그 카드의 `wl_body_full`(fan-out 입력 원문)만 받아, 산출물이 원문에 근거하는지
결정론으로 대조한다. 이 게이트를 통과하지 못한 deep_analysis 는 병합(merge)하지 않는다
(카드는 기존 6슬롯 얇은 형태로만 발행 — graceful degrade, brief_lint 의 "발행 차단" 철학과
동형이나 여기서는 "이 카드의 심층 섹션만 보류"로 스코프가 좁다).

검증 항목:
  D1 구조 완전성 — 4개 섹션 키가 전부 존재하고 공백이 아님. required_remediation 은
      {deadline, items[]} 객체이고 items 가 비어있지 않아야 함(아니면 FAIL).
  D2 인용 근거(citation grounding) — 산출물 안의 조항 번호류 토큰(21 CFR·FD&C Act 섹션·
      bare subsection 등)이 원문(wl_body_full)에 실제로 존재하는지 대조. 없으면 FAIL(날조
      의심) — brief_lint 의 MFDS 미근거 링크 FAIL 과 동형 취급(식별자성 사실은 하드 검증).
  D3 원문 대비 과도한 신규 고유숫자(날짜·금액 등, 4자리 이상 숫자) — 원문에 없는 숫자가
      산출물에 등장하면 WARN(과알림 방지 — 완전 차단은 D2 만, 날짜류는 표기法 차이로 오탐
      가능성이 있어 결정적 FAIL 로 승격하지 않는다. brief_lint 의 WARN 등급과 동형 원칙).

CLI: python -m verify_deep_analysis --deep-analysis da.json --source body_full.txt
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable

SEV_FAIL = "FAIL"
SEV_WARN = "WARN"

REQUIRED_SECTIONS: tuple[str, ...] = (
    "key_violations",
    "fda_evaluation",
    "required_remediation",
    "administrative_risks",
)
_MIN_SECTION_LEN = 20  # 구조 완전성 최소 길이(문자열 섹션). 리스트 섹션은 비었는지만 본다.


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code} — {self.detail}"


def has_failures(findings: Iterable[Finding]) -> bool:
    return any(f.severity == SEV_FAIL for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# D1 — 구조 완전성
# ─────────────────────────────────────────────────────────────────────────────
def _section_text(value: Any) -> str:
    """섹션 값을 대조용 평문으로 재귀 평탄화(str/list/dict, 중첩 포함).

    key_violations(리스트-of-dict)와 required_remediation({deadline, items[]} 객체 —
    items 가 리스트)를 모두 평탄화해 D2/D3 대조 텍스트를 만든다(§2.5: remediation 객체화).
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_section_text(v) for v in value if v)
    if isinstance(value, dict):
        return " ".join(_section_text(v) for v in value.values() if v)
    if value is None:
        return ""
    return str(value)


def _check_remediation_structure(value: Any) -> list[Finding]:
    """required_remediation 은 {deadline, items[]} 객체(§2.5 확정: 문단→체크리스트 구조
    변경) — deadline 은 비공백 문자열, items 는 비어있지 않은(≥1 비공백) 리스트여야 한다."""
    if not isinstance(value, dict):
        return [Finding(SEV_FAIL, "D1-SECTION-INCOMPLETE",
                        "섹션 'required_remediation' 은 {deadline, items[]} 객체여야 함 — "
                        "4섹션 전부 채워야 병합 가능.")]
    findings: list[Finding] = []
    deadline = value.get("deadline")
    if not isinstance(deadline, str) or not deadline.strip():
        findings.append(Finding(SEV_FAIL, "D1-SECTION-INCOMPLETE",
                                "섹션 'required_remediation.deadline' 누락/공백 — 마감기한 한 줄 필수."))
    items = value.get("items")
    if not isinstance(items, list) or not any(isinstance(i, str) and i.strip() for i in items):
        findings.append(Finding(SEV_FAIL, "D1-SECTION-INCOMPLETE",
                                "섹션 'required_remediation.items' 가 비어있음 — 체크리스트 항목 ≥1 필수."))
    return findings


def check_structure(deep_analysis: dict[str, Any]) -> list[Finding]:
    """D1: 4개 섹션 전부 존재 + 공백 아님. required_remediation 은 객체 구조(deadline·items)
    까지 검사한다(§2.5 — overview 제거·remediation 객체화). 누락/공백 섹션마다 FAIL 1건."""
    findings: list[Finding] = []
    for key in REQUIRED_SECTIONS:
        if key == "required_remediation":
            findings.extend(_check_remediation_structure(deep_analysis.get(key)))
            continue
        text = _section_text(deep_analysis.get(key))
        if len(text.strip()) < _MIN_SECTION_LEN:
            findings.append(Finding(
                SEV_FAIL, "D1-SECTION-INCOMPLETE",
                f"섹션 '{key}' 누락 또는 내용 부족(<{_MIN_SECTION_LEN}자) — "
                "4섹션 전부 채워야 병합 가능."))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# D2 — 조항 인용 근거(citation grounding)
# ─────────────────────────────────────────────────────────────────────────────
# 조항류 토큰 — 21 CFR 표기 · bare subsection(211.192) · FD&C Act 섹션(502(a) 류).
# 과알림 방지: 순수 연도(2026)·전화번호 등 일반 4자리 숫자는 별도(D3)로만 다룬다.
_CITATION_PATTERNS = (
    re.compile(r"21\s*CFR\s*(?:Part\s*)?\d{1,4}(?:\.\d{1,4})?(?:\([A-Za-z0-9]{1,4}\))*", re.I),
    re.compile(r"\bFD&C\s*Act[^.]{0,20}?\b\d{3}[A-Za-z]?(?:\([A-Za-z0-9]{1,4}\))*", re.I),
    re.compile(r"\b(?:섹션|section|§)\s*\d{3}[A-Za-z]?(?:\([A-Za-z0-9]{1,4}\))*", re.I),
    # ★ 경계는 \b 대신 숫자 인접만 차단(D3 `_LONG_NUMBER_RE` 와 동일 교훈). Python re 의 \b 는
    #   숫자와 한글 사이에 경계를 만들지 못해(둘 다 \w), 조사가 공백 없이 붙은 날조 조항 번호
    #   (예: "610.13는")를 추출조차 못 해 D2 근거대조를 통째로 우회하는 결함이 있었다(2026-07-01
    #   Codex 리뷰 P1 발견·재현). (?<!\d)…(?!\d) 는 한글/공백/문장부호에 인접해도 정상 추출한다.
    re.compile(r"(?<!\d)\d{3}\.\d{1,4}(?:\([A-Za-z0-9]{1,4}\))*(?!\d)"),          # 211.192(a) 류
    re.compile(r"(?<!\d)\d{3}\([A-Za-z0-9]{1,4}\)(?:\([A-Za-z0-9]{1,4}\))*(?!\d)"),  # 502(a) 류
)


def extract_citations(text: str) -> list[str]:
    """텍스트에서 조항류 토큰 추출(중복 제거, 순서 보존).

    패턴 간 겹치는 구간(예: "21 CFR 610.13" 전체 매칭과 그 안의 "610.13" bare-subsection
    매칭)은 **더 긴 매칭만** 남기고 스킵한다 — 같은 조항이 인용 근거 없음으로 두 번 보고되는
    중복(D2 findings)을 막는다(과알림 축소, 판정 자체는 불변 — 여전히 FAIL 1건).
    """
    text = text or ""
    spans: list[tuple[int, int, str]] = []
    for pat in _CITATION_PATTERNS:
        for m in pat.finditer(text):
            spans.append((m.start(), m.end(), m.group(0).strip()))
    # 긴 매칭 우선(겹치면 짧은 쪽 제외) — 시작 위치 오름차순·길이 내림차순 정렬 후 그리디 채택.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out: list[str] = []
    seen: set[str] = set()
    claimed: list[tuple[int, int]] = []
    for start, end, tok in spans:
        if any(start < c_end and end > c_start for c_start, c_end in claimed):
            continue  # 이미 채택된 더 긴 매칭과 겹침 — 스킵
        key = re.sub(r"\s+", "", tok).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(tok)
        claimed.append((start, end))
    return out


def _normalize_citation(tok: str) -> str:
    """공백·대소문자 차이만 정규화(원문·산출물 양쪽 표기 차이 흡수)."""
    return re.sub(r"\s+", "", tok).lower()


def check_citation_grounding(deep_analysis: dict[str, Any], source_text: str) -> list[Finding]:
    """D2: deep_analysis 안의 조항 인용이 source_text(원문 wl_body_full)에 실제 있는지.

    없으면 FAIL(날조 의심) — brief_lint 의 MFDS 미근거 링크 FAIL 과 동형(식별자성 사실은
    하드 검증, 근거 없이 지어낸 조항 번호로 카드가 나가는 걸 구조적으로 막는다).
    """
    findings: list[Finding] = []
    source_norm = _normalize_citation(source_text or "")
    reported: set[str] = set()
    for key in REQUIRED_SECTIONS:
        text = _section_text(deep_analysis.get(key))
        for tok in extract_citations(text):
            key_norm = _normalize_citation(tok)
            if key_norm in reported:
                continue
            if key_norm not in source_norm:
                reported.add(key_norm)
                findings.append(Finding(
                    SEV_FAIL, "D2-CITATION-UNGROUNDED",
                    f"섹션 '{key}'의 조항 인용 '{tok}'가 원문(wl_body_full)에 없음 — "
                    "날조/오인용 의심. 원문에 실재하는 조항만 인용해야 함."))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# D3 — 원문에 없는 신규 고유숫자(날짜·금액 등) — WARN(표기법 차이로 오탐 가능, 비차단)
# ─────────────────────────────────────────────────────────────────────────────
# (?<!\d)...(?!\d) — 순수 자릿수 경계(앞뒤에 숫자만 없으면 됨). `\b` 는 쓰지 않는다: Python
# re 의 \w 는 유니코드 인식이라 한글도 단어문자로 보아, 숫자 바로 뒤에 조사가 붙는 한국어 관용
# ("30441955는")에서 \b 가 성립하지 않아 매칭이 누락되는 결함이 있었다(2026-07-01 발견).
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{4,}(?!\d)")


def check_novel_numbers(deep_analysis: dict[str, Any], source_text: str) -> list[Finding]:
    """D3: 4자리 이상 숫자(날짜·FEI·금액 등)가 원문에 없으면 WARN(비차단 — 표기법 차이 가능)."""
    findings: list[Finding] = []
    source_nums = set(_LONG_NUMBER_RE.findall(source_text or ""))
    reported: set[str] = set()
    for key in REQUIRED_SECTIONS:
        text = _section_text(deep_analysis.get(key))
        for m in _LONG_NUMBER_RE.finditer(text):
            num = m.group(0)
            if num in reported or num in source_nums:
                continue
            reported.add(num)
            findings.append(Finding(
                SEV_WARN, "D3-NUMBER-UNVERIFIED",
                f"섹션 '{key}'의 숫자 '{num}'가 원문에서 확인 안 됨 — 표기법 차이일 수 있어 "
                "차단하지 않으나 발행 전 수동 확인 권고."))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# 게이트 실행
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GateResult:
    ok: bool
    findings: list[Finding]
    report: str

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEV_FAIL)

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEV_WARN)


def format_report(findings: list[Finding]) -> str:
    fails = [f for f in findings if f.severity == SEV_FAIL]
    warns = [f for f in findings if f.severity == SEV_WARN]
    if not findings:
        return "[PASS] 심층분석 게이트 — 위반 0 (병합 허용)"
    head = (f"[{'FAIL' if fails else 'PASS(경고)'}] 심층분석 게이트 — "
            f"FAIL {len(fails)} · WARN {len(warns)}")
    lines = [head]
    for f in fails:
        lines.append(f"  ✖ {f}")
    for f in warns:
        lines.append(f"  ⚠ {f}")
    if fails:
        lines.append("→ 병합 보류: 이 카드는 deep_analysis 없이(기존 6슬롯만) 발행하고, "
                     "위 FAIL 항목을 고친 뒤 재검증해야 심층 섹션이 카드에 반영된다.")
    return "\n".join(lines)


def run_deep_analysis_gate(deep_analysis: dict[str, Any], source_text: str) -> GateResult:
    """카드 1건의 deep_analysis 를 원문(source_text=wl_body_full)과 대조해 병합 가부 판정.

    FAIL 이 하나라도 있으면 이 카드의 deep_analysis 는 최종 브리프에 병합하지 않는다
    (카드는 기존 6슬롯 얇은 형태로 발행 — graceful degrade). WARN 은 병합을 막지 않되
    사람이 검토할 수 있게 report 에 남긴다.
    """
    findings: list[Finding] = []
    findings.extend(check_structure(deep_analysis))
    if not has_failures(findings):  # 구조가 불완전하면 인용 대조는 의미가 없어 생략
        findings.extend(check_citation_grounding(deep_analysis, source_text))
        findings.extend(check_novel_numbers(deep_analysis, source_text))
    return GateResult(ok=not has_failures(findings), findings=findings,
                      report=format_report(findings))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv: "list[str] | None" = None) -> int:
    import argparse
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(
        prog="verify_deep_analysis",
        description="GRM WL 심층분석(5섹션) 사실 근거 게이트 — FAIL 시 이 카드의 심층 섹션 병합 보류.")
    p.add_argument("--deep-analysis", required=True, help="deep_analysis JSON 경로(5섹션 키).")
    p.add_argument("--source", required=True, help="fan-out 입력 원문(wl_body_full) 텍스트 경로.")
    args = p.parse_args(argv)

    with open(args.deep_analysis, "r", encoding="utf-8") as fh:
        deep_analysis = json.load(fh)
    with open(args.source, "r", encoding="utf-8") as fh:
        source_text = fh.read()

    result = run_deep_analysis_gate(deep_analysis, source_text)
    print(result.report)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
