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
  D4 원문 병기 근거(original grounding) — key_violations[].original(번역 근거가 된 규제 원문
      발췌·선택 필드)이 원문(body_full)에서 따온 verbatim 인지 정규화 후 대조. 없으면 WARN
      (비차단 — 공백·따옴표 표기차 오탐 가능). original 미보유 항목은 검사 없음(후방호환).
  D5 원문·국문 병기 정합성(발췌 절단·미근거 구체어) — [FDA 483 원문절단 결함 2026-07-13]
      실제 발행본에서 483 카드의 key_violations[].original 이 결함 첫 문장까지만 발췌되고,
      국문 observation 은 그 뒤 "Specifically, ..." 상세(구체 사실)까지 요약해 병기쌍이 깨진
      사례가 발견됐다(화면엔 원문에 없어 보이는 구체 사실이 국문에만 있어 날조처럼 보임 — 실제로는
      원문에 근거하나 화면의 original 이 잘려 그 근거가 안 보이는 것). 두 하위 검사로 잡는다.
      D5a(WARN·전 카드타입) — 국문 해석(observation/description/summary)의 하드 구체어(라틴
        단어 4자+·3자리+ 숫자, 흔한 규제 약어 제외)가 병기된 original 에 없으면 WARN(비차단 —
        original 절단·정당한 재서술 양쪽 가능해 D4 와 동형으로 강한 차단은 하지 않는다).
      D5b(FAIL·FDA 483 전용) — original 이 source_text 안에서 발견되는데(=D4 통과) 그 매칭
        직후 이어지는 원문이 "Specifically" 로 시작하면 original 이 상세 앞에서 결함 문장만
        잘라낸 것 — 483 전용 하드 FAIL(WL/admin 은 이 절단 패턴이 구조적으로 없음). original 을
        아예 못 찾으면(=D4 의 영역) D5b 는 관여하지 않는다.

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

# WL(warning-letter) 4섹션 — 기본값(후방호환: 기존 직접 호출·테스트가 이 상수를 그대로 씀).
REQUIRED_SECTIONS: tuple[str, ...] = (
    "key_violations",
    "fda_evaluation",
    "required_remediation",
    "administrative_risks",
)
# [소스확장 2026-07-02] MFDS 행정처분 4섹션 — WL 의 fda_evaluation(응답 왕복 평가) 자리를
# disposition_basis(처분 내용·수위·판단근거)로 교체(확정처분엔 "응답 평가"가 없음, 설계문서 §5·§15).
# 나머지 3섹션(key_violations·required_remediation·administrative_risks)은 WL 과 동일 구조 재사용.
REQUIRED_SECTIONS_ADMIN: tuple[str, ...] = (
    "key_violations",
    "disposition_basis",
    "required_remediation",
    "administrative_risks",
)
# [FDA 483 분석층 2026-07-02] FDA 483 4섹션 — WL 의 fda_evaluation(응답 왕복 평가) 자리를
# inspectional_significance(실사 지적의 규제적 의미·중대도·WL/Import Alert 승격 가능성)로 교체.
# 483 은 실사 종료 시 발부되는 문서라 "회사 응답 → 당국 평가" 왕복이 아직 없다(설계문서 §9·§12).
# 나머지 3섹션(key_violations·required_remediation·administrative_risks)은 WL·admin 과 동일 구조 재사용.
REQUIRED_SECTIONS_FDA483: tuple[str, ...] = (
    "key_violations",
    "inspectional_significance",
    "required_remediation",
    "administrative_risks",
)
_MIN_SECTION_LEN = 20  # 구조 완전성 최소 길이(문자열 섹션). 리스트 섹션은 비었는지만 본다.


def resolve_required_sections(deep_analysis: "dict[str, Any] | None" = None,
                              card_type: "str | None" = None) -> tuple[str, ...]:
    """카드타입별 필수 섹션 집합. card_type 이 명시되면 그것으로, 없으면 산출물 키로 자동판별.

    자동판별: `disposition_basis`(admin)·`inspectional_significance`(483) 중 하나가 있고
    `fda_evaluation` 이 없으면 그 유형, 그 외엔 WL.
    → 기존 WL 호출부(card_type 미전달·fda_evaluation 보유)는 항상 REQUIRED_SECTIONS 로 귀결(불변).
    """
    if card_type in ("admin-action", "행정처분"):
        return REQUIRED_SECTIONS_ADMIN
    if card_type in ("fda-483", "FDA 483"):
        return REQUIRED_SECTIONS_FDA483
    if card_type in ("warning-letter", "Warning Letter"):
        return REQUIRED_SECTIONS
    da = deep_analysis or {}
    if isinstance(da, dict) and "fda_evaluation" not in da:
        if "disposition_basis" in da:
            return REQUIRED_SECTIONS_ADMIN
        if "inspectional_significance" in da:
            return REQUIRED_SECTIONS_FDA483
    return REQUIRED_SECTIONS


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


def check_structure(deep_analysis: dict[str, Any],
                    sections: tuple[str, ...] = REQUIRED_SECTIONS) -> list[Finding]:
    """D1: 4개 섹션 전부 존재 + 공백 아님. required_remediation 은 객체 구조(deadline·items)
    까지 검사한다(§2.5 — overview 제거·remediation 객체화). 누락/공백 섹션마다 FAIL 1건.

    `sections` 로 카드타입별 필수 섹션 집합을 받는다(기본=WL, 후방호환). admin 은 fda_evaluation
    자리에 disposition_basis(문자열 섹션 — 동일 최소길이 검사)."""
    findings: list[Finding] = []
    for key in sections:
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
    # ─ [소스확장 2026-07-02] 한국 행정처분 근거법령 — 위 CFR/FD&C 전용 패턴은 한국 법령에
    #   매칭이 0건이라 D2 근거대조가 무력화된다(설계문서 §5-1). 아래 3종을 추가한다.
    #   (1) 알려진 법령/규칙명 + 조항 = 하나의 토큰(법령명까지 근거대조 → "화장품법 제38조"를
    #       원문의 "약사법 제38조"로 잘못 근거삼는 교차오인용 차단). generic `[가-힣]+법` 은
    #       앞 단어를 greedy 하게 삼켜(예 "위반한약사법") 오탐하므로 명시 열거만 쓴다.
    #       ★코너 브래킷 관용(Codex 게이트 2차): 법령명은 원문/산출물서 「」『』로 감싸 인용된다
    #       (예 「화장품법」 제38조제1항). 법령명 뒤 `」`가 `제` 앞을 막으면 full law token 이
    #       추출 안 돼 bare `제N조`만 남고, 그러면 원문의 다른 법(「약사법」)에 근거삼아져 교차
    #       오인용이 통째로 우회된다. → 선행 여는브래킷 `[「『]?` + 법령명·`제` 사이 구분자를
    #       `[\s「」『』]*`(브래킷 관용)로 둬 `「화장품법」 제38조제1항` 전체를 한 토큰으로 뽑는다
    #       (정규화 `_normalize_citation` 이 「」 제거 → 법령명 대조 성립). 약사법↔화장품법=FAIL.
    #   (2) bare 조항(제N조[의N][제N항][제N호]) — 법령명 없이 인용된 조/항/호.
    #   (3) [별표N] — 행정처분 기준 별표.
    #   조사 경계: 후행 `\b` 미사용(패턴이 조사 직전에서 끝나 "제38조를"→"제38조" 정상 추출 —
    #   D3 `_LONG_NUMBER_RE` 의 한글 조사 경계 교훈과 동형). 한글 부재 텍스트(WL 영문)엔 무매칭.
    re.compile(
        r"[「『]?(?:약사법|화장품법|의료기기법|마약류\s*관리에\s*관한\s*법률|"
        r"의약품\s*등의\s*안전에\s*관한\s*규칙|약사법\s*시행규칙|약사법\s*시행령)"
        r"[\s「」『』]*(?:시행규칙|시행령)?[\s「」『』]*제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?"),
    # 앵커를 조/항/호로 확장(Codex 게이트 차단1): 앵커가 `제\d+조` 뿐이면 `조` 없이 단독으로
    # 온 날조 `제999호`·`제99항`이 추출조차 안 돼 D2 근거대조를 통째로 우회한다. `제38조제1항`은
    # 여전히 긴 매칭 1토큰으로 유지(extract_citations 의 겹침 dedup) → 기존 동작 불변.
    re.compile(r"제\d+(?:조|항|호)(?:의\d+)?(?:제\d+항)?(?:제\d+호)?"),   # bare 제N조/항/호
    re.compile(r"\[\s*별표\s*\d+\s*\]"),                                            # [별표N]
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
    """공백·대소문자·법령명 코너 브래킷(「」『』) 차이만 정규화(원문·산출물 양쪽 표기 차이 흡수).

    Codex 게이트 차단2: 원문이 `「약사법」 제38조제1항`인데 정상 인용 `약사법 제38조제1항`이
    브래킷 차이만으로 FAIL(과탐)나던 것을 막는다. CFR `502(a)`의 소괄호는 의미 있는 부분이라
    보존(문자클래스에 넣지 않는다)."""
    return re.sub(r"[\s「」『』]+", "", tok).lower()


def check_citation_grounding(deep_analysis: dict[str, Any], source_text: str,
                             sections: tuple[str, ...] = REQUIRED_SECTIONS,
                             severity: str = SEV_FAIL) -> list[Finding]:
    """D2: deep_analysis 안의 조항 인용이 source_text(원문 body_full)에 실제 있는지.

    없으면 `severity`(기본 FAIL, 날조 의심) — brief_lint 의 MFDS 미근거 링크 FAIL 과 동형(식별자성
    사실은 하드 검증, 근거 없이 지어낸 조항 번호로 카드가 나가는 걸 구조적으로 막는다). 한국 행정처분은
    약사법 조항·[별표N] 등 한국법령 토큰이 대상(_CITATION_PATTERNS 확장, 설계문서 §5-1).

    ★FDA 483 은 `severity=SEV_WARN`(비차단): 483 원문(관찰사항 목록)은 CFR 조항을 명시하지 않을
    때가 많아, 분석가가 붙인 정당한 규제 해석(21 CFR 211.x 등)을 하드 FAIL 로 막으면 과차단이 된다.
    → 원문 밖 인용은 WARN 으로 남겨 발행 전 수동 확인만 유도한다(WL 의 "원문 밖 숫자 WARN" 원리와
    동형). 날조된 식별자성 숫자(FEI·날짜 등)는 D3 가 여전히 잡는다.
    """
    findings: list[Finding] = []
    source_norm = _normalize_citation(source_text or "")
    reported: set[str] = set()
    for key in sections:
        text = _section_text(deep_analysis.get(key))
        for tok in extract_citations(text):
            key_norm = _normalize_citation(tok)
            if key_norm in reported:
                continue
            if key_norm not in source_norm:
                reported.add(key_norm)
                if severity == SEV_WARN:
                    detail = (f"섹션 '{key}'의 조항 인용 '{tok}'가 원문(body_full)에 없음 — "
                              "483 원문은 CFR 조항을 명시하지 않을 수 있어 해석성 인용은 차단하지 "
                              "않으나 발행 전 수동 확인 권고.")
                else:
                    detail = (f"섹션 '{key}'의 조항 인용 '{tok}'가 원문(wl_body_full)에 없음 — "
                              "날조/오인용 의심. 원문에 실재하는 조항만 인용해야 함.")
                findings.append(Finding(severity, "D2-CITATION-UNGROUNDED", detail))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# D3 — 원문에 없는 신규 고유숫자(날짜·금액 등) — WARN(표기법 차이로 오탐 가능, 비차단)
# ─────────────────────────────────────────────────────────────────────────────
# (?<!\d)...(?!\d) — 순수 자릿수 경계(앞뒤에 숫자만 없으면 됨). `\b` 는 쓰지 않는다: Python
# re 의 \w 는 유니코드 인식이라 한글도 단어문자로 보아, 숫자 바로 뒤에 조사가 붙는 한국어 관용
# ("30441955는")에서 \b 가 성립하지 않아 매칭이 누락되는 결함이 있었다(2026-07-01 발견).
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{4,}(?!\d)")


def check_novel_numbers(deep_analysis: dict[str, Any], source_text: str,
                        sections: tuple[str, ...] = REQUIRED_SECTIONS) -> list[Finding]:
    """D3: 4자리 이상 숫자(날짜·FEI·금액 등)가 원문에 없으면 WARN(비차단 — 표기법 차이 가능)."""
    findings: list[Finding] = []
    source_nums = set(_LONG_NUMBER_RE.findall(source_text or ""))
    reported: set[str] = set()
    for key in sections:
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
# D4 — 원문 병기 근거(original grounding) — WARN(비차단, 표기법 차이로 오탐 가능)
# ─────────────────────────────────────────────────────────────────────────────
# [원문·국문 병기 2026-07-08] key_violations[].original(번역 근거가 된 규제 원문 발췌)이 실제로
# source_text(body_full)에서 따온 verbatim 인지 확인한다. LLM 이 "원문"을 지어내면 사용자에게 가짜
# 원어가 노출되므로(국문 해석보다 위험), 근거 없는 original 은 잡아야 한다. 다만 D3(숫자)와 같은
# WARN·비차단: LLM 이 공백·줄바꿈·따옴표/대시 표기를 정규화해 산출하는 일이 흔해 하드 substring 은
# 오탐이 잦다 → 아래처럼 정규화 후 대조하고, 못 찾으면 WARN 으로 남겨 발행 전 수동 확인만 유도한다.
_WS_RE = re.compile(r"\s+")
_QUOTE_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", "﻿": "",
}
_MIN_ORIGINAL_LEN = 12  # 이보다 짧은 발췌는 대조 신뢰도가 낮아 근거검사 생략(빈값·라벨성 조각 방지)


def _normalize_original(text: str) -> str:
    """original/source 대조용 정규화 — 유니코드 따옴표·대시를 ASCII 로, 연속 공백을 단일 공백으로,
    앞뒤 공백 제거. 대소문자는 보존(규제 원문 표기 유지). LLM 이 원문 span 을 옮기며 공백/따옴표만
    바꾸는 흔한 표기차를 흡수해 하드 substring 오탐을 줄인다(판정은 여전히 '원문에 있나' 하나)."""
    for src, dst in _QUOTE_MAP.items():
        text = text.replace(src, dst)
    return _WS_RE.sub(" ", text).strip()


def check_original_grounding(deep_analysis: dict[str, Any],
                             source_text: str) -> list[Finding]:
    """D4: key_violations 각 항목의 `original`(원문 병기 발췌)이 source_text 에 실재하는 verbatim
    인지 정규화 후 대조. 없으면 WARN(비차단). `original` 미보유 항목은 검사 없음(선택 필드 — 백필 전
    구데이터·original 없는 카드는 무영향). key_violations 가 리스트-of-dict 가 아니면 조용히 건너뜀."""
    findings: list[Finding] = []
    kv = deep_analysis.get("key_violations")
    if not isinstance(kv, list):
        return findings
    source_norm = _normalize_original(source_text or "")
    for i, v in enumerate(kv):
        if not isinstance(v, dict):
            continue
        orig = v.get("original")
        if not isinstance(orig, str) or len(orig.strip()) < _MIN_ORIGINAL_LEN:
            continue
        if _normalize_original(orig) not in source_norm:
            snippet = orig.strip()[:60] + ("…" if len(orig.strip()) > 60 else "")
            findings.append(Finding(
                SEV_WARN, "D4-ORIGINAL-UNGROUNDED",
                f"key_violations[{i}]의 원문 병기(original) '{snippet}'가 원문(body_full)에서 "
                "확인 안 됨 — 공백·따옴표 표기차일 수 있어 차단하지 않으나, 지어낸 원어일 수 있으니 "
                "발행 전 수동 확인 권고(원문은 body_full 에서 그대로 발췌해야 함)."))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# D5 — 원문·국문 병기 정합성(발췌 절단·미근거 구체어) — [FDA 483 원문절단 결함 2026-07-13]
# ─────────────────────────────────────────────────────────────────────────────
# 배경: FDA 483 카드에서 LLM 이 key_violations[].original 을 결함 첫 문장(짧은 지적사항)까지만
# 발췌하고, 국문 observation 은 그 뒤 "Specifically, ..." 상세(예: 특정 이물질명 등 구체 사실)까지
# 요약해버린 사례가 실제 발행본에서 발견됐다. 결과: 화면에 병기된 original(원문)에는 없는 구체
# 사실이 국문에만 등장 → 사용자 눈에는 "날조"로 보인다(실제로는 원문에 근거하나 화면의 original
# 이 잘려서 그 근거가 안 보이는 것). 아래 두 검사로 이를 결정론으로 잡는다.
_KO_SPECIFIC_ALLOWLIST = {
    "ISO", "HEPA", "CAPA", "OOS", "GMP", "CGMP", "FDA", "CFR", "WHO", "LAFW", "LAFH",
    "BSC", "MVI", "OTC", "TPN", "USP", "HVAC", "SOP", "QC", "QA", "API", "MVID",
}
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{3,}")
_DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{3,}(?!\d)")
_483_TRUNCATION_FOLLOW_WORD = "specifically"


def _extract_ko_specifics(ko_text: str) -> list[str]:
    """국문 해석 텍스트에서 '하드 구체어' 후보 추출(중복 제거, 순서 보존) — 라틴 단어(4자+,
    흔한 규제 약어는 제외)와 3자리 이상 숫자열. original 대조로 미근거 구체 사실을 잡는 데 쓴다."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _LATIN_WORD_RE.finditer(ko_text or ""):
        tok = m.group(0)
        key = tok.upper()
        if key in _KO_SPECIFIC_ALLOWLIST or key in seen:
            continue
        seen.add(key)
        out.append(tok)
    for m in _DIGIT_RUN_RE.finditer(ko_text or ""):
        tok = m.group(0)
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def check_ko_specific_grounding(deep_analysis: dict[str, Any]) -> list[Finding]:
    """D5a: key_violations 각 항목의 국문 해석(observation/description/summary 중 첫 비공백
    필드)에 등장하는 하드 구체어(라틴 단어·3자리+ 숫자)가 병기된 original 에 없으면 WARN(비차단)
    — original 이 잘렸거나 국문이 원문 밖 사실을 추가했을 가능성(2026-07-13 483 원문절단 결함과
    동형 패턴, 전 카드타입에 적용 — WL/admin 도 같은 병기 UI 를 쓰므로 동일 위험이 있다).
    original 미보유/짧은 항목은 검사 없음(D4 와 동일 최소길이 _MIN_ORIGINAL_LEN)."""
    findings: list[Finding] = []
    kv = deep_analysis.get("key_violations")
    if not isinstance(kv, list):
        return findings
    for i, v in enumerate(kv):
        if not isinstance(v, dict):
            continue
        orig = v.get("original")
        if not isinstance(orig, str) or len(orig.strip()) < _MIN_ORIGINAL_LEN:
            continue
        ko_text = next(
            (v[k] for k in ("observation", "description", "summary")
             if isinstance(v.get(k), str) and v.get(k).strip()), None)
        if not ko_text:
            continue
        orig_lower = orig.lower()
        for tok in _extract_ko_specifics(ko_text):
            if tok.lower() not in orig_lower:
                findings.append(Finding(
                    SEV_WARN, "D5-KO-SPECIFIC-UNGROUNDED",
                    f"key_violations[{i}]의 국문 해석에 등장하는 구체어 '{tok}'가 병기된 "
                    "original 에서 확인 안 됨 — original 이 잘렸거나 국문이 원문 밖 사실을 "
                    "추가했을 수 있어 차단하지 않으나 발행 전 수동 확인 권고."))
    return findings


def check_fda483_original_truncation(deep_analysis: dict[str, Any],
                                     source_text: str) -> list[Finding]:
    """D5b: FDA 483 전용. key_violations[].original 이 source_text 안에서 발견되는데(=D4 가
    통과시키는 케이스), 매칭 직후 이어지는 원문이(공백·구두점 제외 후) "Specifically" 로 시작하면
    — original 이 결함 문장만 발췌하고 그 뒤 "Specifically…" 상세를 잘라낸 것. 483 카드에서
    국문(observation)이 그 상세까지 요약해 병기쌍이 깨지는, 실제 발행본에서 발견된 결함
    (2026-07-13) 패턴을 하드 FAIL 로 잡는다. original 을 source_text 에서 아예 못 찾으면
    (=D4 의 영역, 근거 자체가 불명) D5b 는 관여하지 않는다."""
    findings: list[Finding] = []
    kv = deep_analysis.get("key_violations")
    if not isinstance(kv, list):
        return findings
    source_norm = _normalize_original(source_text or "")
    source_norm_lower = source_norm.lower()
    for i, v in enumerate(kv):
        if not isinstance(v, dict):
            continue
        orig = v.get("original")
        if not isinstance(orig, str) or len(orig.strip()) < _MIN_ORIGINAL_LEN:
            continue
        orig_norm_lower = _normalize_original(orig).lower()
        idx = source_norm_lower.find(orig_norm_lower)
        if idx == -1:
            continue  # D4 의 영역(원문에서 아예 발견 안 됨) — D5b 는 관여하지 않음
        tail = source_norm[idx + len(orig_norm_lower):].lstrip(" .:;,-")
        if tail[:len(_483_TRUNCATION_FOLLOW_WORD)].lower() == _483_TRUNCATION_FOLLOW_WORD:
            citation = v.get("citation")
            cite_note = (f"(citation: {citation}) "
                        if isinstance(citation, str) and citation.strip() else "")
            snippet = orig.strip()[:60] + ("…" if len(orig.strip()) > 60 else "")
            findings.append(Finding(
                SEV_FAIL, "D5-483-ORIGINAL-TRUNCATED",
                f"key_violations[{i}] {cite_note}의 원문 병기(original) '{snippet}'가 "
                "'Specifically…' 상세 앞 결함 문장까지만 발췌됨 — original 은 결함 진술 + 그 뒤 "
                "'Specifically…' 상세 전체를 포함해야 국문 해석(observation)의 구체 사실이 화면에 "
                "근거로 보인다(그렇지 않으면 국문만 구체적이고 원문은 짧아 날조처럼 보인다)."))
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


def run_deep_analysis_gate(deep_analysis: dict[str, Any], source_text: str, *,
                           card_type: "str | None" = None) -> GateResult:
    """카드 1건의 deep_analysis 를 원문(source_text=body_full)과 대조해 병합 가부 판정.

    FAIL 이 하나라도 있으면 이 카드의 deep_analysis 는 최종 브리프에 병합하지 않는다
    (카드는 기존 6슬롯 얇은 형태로 발행 — graceful degrade). WARN 은 병합을 막지 않되
    사람이 검토할 수 있게 report 에 남긴다.

    `card_type` 미전달 시 산출물 키로 WL/admin 섹션 집합을 자동판별(resolve_required_sections)
    → 기존 WL 호출부(card_type 없음·fda_evaluation 보유)는 동작 완전 불변.
    """
    sections = resolve_required_sections(deep_analysis, card_type)
    # FDA 483 은 CFR 인용이 원문에 없어도 정당한 해석일 수 있어 D2 를 WARN(비차단)으로 강등한다
    # (WL·admin 은 하드 FAIL 유지 — 조항이 원문에 실재해야 함). 위 docstring · 설계문서 §12.
    citation_severity = SEV_WARN if sections is REQUIRED_SECTIONS_FDA483 else SEV_FAIL
    findings: list[Finding] = []
    findings.extend(check_structure(deep_analysis, sections))
    if not has_failures(findings):  # 구조가 불완전하면 인용 대조는 의미가 없어 생략
        findings.extend(check_citation_grounding(deep_analysis, source_text, sections,
                                                 severity=citation_severity))
        findings.extend(check_novel_numbers(deep_analysis, source_text, sections))
        # D4: 원문 병기(original) 근거 — 선택 필드라 미보유 카드엔 무영향(WARN·비차단).
        findings.extend(check_original_grounding(deep_analysis, source_text))
        # D5a: 국문 해석의 미근거 구체어(전 카드타입, WARN) — 2026-07-13 483 원문절단 결함 대응.
        findings.extend(check_ko_specific_grounding(deep_analysis))
        # D5b: FDA 483 전용 original 절단 하드검증(FAIL) — WL/admin 은 이 절단 패턴이 구조적으로 없음.
        if sections is REQUIRED_SECTIONS_FDA483:
            findings.extend(check_fda483_original_truncation(deep_analysis, source_text))
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
        description="GRM WL 심층분석(4섹션) 사실 근거 게이트 — FAIL 시 이 카드의 심층 섹션 병합 보류.")
    p.add_argument("--deep-analysis", required=True, help="deep_analysis JSON 경로(4섹션 키).")
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
