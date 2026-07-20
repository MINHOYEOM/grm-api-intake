"""라우틴 델타 + 빈슬롯 스캐폴드 → 발행본(채택분) 조립. 순수·결정론.

한 주치 웹 발행 파이프의 **코드 계약**(2026-07-06 수동 조립 사고의 코드화):

    scaffold(grm-web-card/v1, §1-B 이미터가 낸 전 intake 카드·빈 슬롯)
      + delta({"cards": {card.id: {슬롯}}, "tldr": [...]})
        1) inject_slots.inject_llm_slots  — LLM 슬롯 주입 + 코드 가드(마크업·길이·정합)
        2) 채택 필터              — 델타에 없는 카드(=Routine 이 Tier1/Skipped 처리) 제거
        3) render_order 재배열     — 채택분을 0..N-1 로 연속 재부여(라이브 06-26 규약)
        4) 브리프 메타 재계산       — agencies·categories·coverage.evidence 를 채택분으로
                                    재집계(assemble_web_brief 규약 미러). coverage.intake_total
                                    (실수집 총건)은 진실값으로 **보존**, rendered=채택 수.
        → 발행본 brief_web_{publish_date}.json

**왜 필요한가**: §1-B 컬렉터는 tier 를 모르므로 스캐폴드에 전 intake 카드(예: 89)를 방출한다.
Routine 이 그중 일부(예: 61)만 채택하고 나머지(Tier1)를 Skipped 한다. 따라서 발행 직전
"채택분만 남기고 메타를 재계산"하는 결정론 단계가 반드시 필요하다 — 이 단계가 지금까지
어떤 코드에도 없어 매주 수작업이었다(그리고 2026-07-06 에 89 vs 61 로 표면화됐다).

**불변식**:
- 코드 verbatim 필드(facts·quotes[].original·sources·headline_target·배지·id·group 등)는 절대
  변경하지 않는다(inject_slots 계승 — 이 모듈은 슬롯 주입을 inject_slots 에 위임하고, 그 외에는
  카드 부분집합 선택 + render_order 재부여 + 브리프 메타 재계산만 한다).
- 순수·결정론: 같은 (scaffold, delta) → 바이트 동일 출력(네트워크·현재시각·난수 0).
- data 관례(indent=1·ensure_ascii=False·LF·후행개행) = web/render._write_json / emit_web_brief_file 동형.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import inject_slots

# 발행 카드가 반드시 채워야 하는 LLM 슬롯(빈값이면 발행 불가).
_REQUIRED_STR_SLOTS = ("title_issue", "summary", "implication")
_REQUIRED_LIST_SLOTS = ("key_facts", "checks")

# [업계 브리핑 노트 2026-07-13, v2 명칭개편 2026-07-13] 해설·교육성 2차 소스(전문 매체) —
# 이벤트 카드가 아닌 '전문지 브리핑'으로 렌더(구 '업계 브리핑 노트'). 향후 RAPS·European
# Pharmaceutical Review 등 수집 추가 시 여기에 기관명만 추가.
# [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak 추가 — card_scaffold._REGULATOR_LABEL 의
# agency="ISPE" 와 일치.
RESOURCE_AGENCIES = ("ECA", "ISPE")

# ── [거짓 부재 서술 차단 2026-07-20] ──────────────────────────────────────────
# 2026-07-20 발행분 사고: 수집기가 Warning Letter 원문 전문(21k자·조항별 위반 3~5건)을 정상
# 확보했는데도 카드가 "세부 위반내용은 원문에 명시되지 않았다"고 발행됐다. 원인은 LLM 이 아니라
# 그 LLM 에게 **도입구 118자만 전달된 입력**이었지만, 결과물은 독자에게 그냥 거짓말이다.
# 그래서 입력 결함을 고치는 것(리드인 건너뛰기·결정론 위반항목 슬롯)과 별개로, **원문을 손에
# 쥔 카드가 "원문에 없다"고 주장하면 발행을 막는다.** 정직성은 재발 방지 장치가 필요하다.
#
# 판정 = ① 이 카드에 원문 확보 증거가 있고(결정론 상세 블록 또는 심층분석) ② 산문 슬롯에
# "위반/관찰/지적/결함"의 부재 주장이 함께 있을 때만 FAIL. 원문이 실제로 없는 카드
# (스캔 483 등)의 정직한 "원문 미기재" 서술은 ①이 성립하지 않아 걸리지 않는다.
#
# [주어 확장 2026-07-20] 전수 점검에서 "근거: 21 U.S.C.(세부 **조항** 원문 미기재)" 가 처음
# 목록(위반/관찰/지적/결함)에 안 걸려 빠져나갔다 — Genzyme WL 은 21 U.S.C. § 331(a)·§ 351(a)
# 를 명시하고 있었다. 부재 주장의 주어는 "위반"만이 아니다.
_FALSE_ABSENCE_RE = re.compile(
    r"(?:위반|관찰|지적|결함|조항|근거|사유|처분|상세)[^.\n]{0,24}?"
    r"(?:원문|본문|공개)[^.\n]{0,12}?"
    r"(?:미기재|미공개|명시되(?:어\s*있)?지\s*않|기재되(?:어\s*있)?지\s*않|"
    r"공개되(?:어\s*있)?지\s*않|나와\s*있지\s*않|없)")
# 검사 대상 산문 슬롯(코드 verbatim 필드인 facts 는 제외 — 거기서의 "원문 미기재"는
# 그 칸의 값이 실제로 원문에 없다는 정직한 표기다).
_FALSE_ABSENCE_SLOTS = ("summary", "implication", "title_issue")

# ── [게이트 5 — facts 칸 근거 없는 원문 부재 단정 2026-07-20] ──────────────────
# 위 게이트 3(`lint_false_absence_claims`)은 산문 슬롯만 본다. facts 는 "코드 verbatim 필드라
# 그 칸의 '원문 미기재'는 그 값이 실제로 원문에 없다는 정직한 표기"라는 가정으로 일부러
# 검사에서 뺐었는데, 그 가정이 반증됐다 — Health Canada 회수 카드 6건이 `업체 | 원문 미기재`
# 로 발행됐지만 원문에는 업체명(Apotex Inc.·Servier Canada Inc.·Kao Canada Inc.·Becton
# Dickinson Canada Inc.·Jamp Pharma·BC Cancer)이 분명히 적혀 있었다. facts 값은 수집기가
# 원문 dict 에서 특정 키를 못 찾았을 때 채우는 자리표시자일 뿐 원문을 필드 단위로 대조한
# 결과가 아니므로, "이 칸이 비었으니 원문에도 없다"고 말할 자격이 코드에는 없다.
#
# 값 **전체**가 부재 단정 표기일 때만 잡는다(앵커 fullmatch) — 값 안에 섞여 등장하는 서술
# (예: "관찰 1: 부적격 사유 미기재")은 업체가 실제로 기재를 누락했다는 **사실**이라 대상이
# 아니다. 새로 확보를 못 한 값은 이 표기 대신 `card_scaffold.VALUE_UNKNOWN`("미확인" — 우리
# 상태만 말하는 표기)을 쓰라는 뜻이다.
_UNVERIFIED_ABSENCE_RE = re.compile(
    r"^\s*(?:원문|본문)\s*(?:에는?|은|이)?\s*(?:"
    r"미기재|미상|미표기|미공개|"
    r"명시되(?:어\s*있)?지\s*않(?:음|다)?|"
    r"기재되(?:어\s*있)?지\s*않(?:음|다)?|"
    r"공개되(?:어\s*있)?지\s*않(?:음|다)?|"
    r"나와\s*있지\s*않(?:음|다)?|"
    r"없(?:음|다)?"
    r")\s*$")


@dataclass
class AssembleReport:
    """조립 결과 보고. errors 가 비어야 발행 가능(strict)."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    adopted: int = 0
    dropped: int = 0
    dropped_ids: list[str] = field(default_factory=list)
    resources: int = 0
    # [2026-07-20] 이번 조립에서 관찰 블록을 새로 만들거나 갱신한 483 카드 id.
    # 국문 병기 게이트(`_lint_483_observation_ko`)의 **적용 범위**다 — 손대지 않고 통과시킨
    # 과거 발행분까지 소급 검사하면 병기 기능(2026-07-09) 이전 브리프가 통째로 막힌다.
    refreshed_483_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class AssembleError(ValueError):
    """조립 검증 실패(코드 가드 위반)."""


def _distinct_in_order(values: list[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return seen


def _is_content_less_483(c: dict[str, Any]) -> bool:
    """관찰 원문이 없는 FDA 483 공개 카드(스캔·비공개 PDF → 상세/심층 없음)인가.
    시설·실사일 메타만 있는 '공개 알림' 카드 — 분석된 483(deterministic_detail·deep_analysis
    보유)과 구별해 목록카드 1장으로 접는 대상(2026-07-13)."""
    is483 = (c.get("type_tag") == "483") or str(c.get("id", "")).startswith("fda483-")
    return bool(is483 and not c.get("deterministic_detail") and not c.get("deep_analysis"))


def _fda483_facility_line(c: dict[str, Any]) -> str:
    """카드 facts 에서 '제조소/업체' + 실사일을 목록 항목 1줄로(결정론·사실 재작성 0)."""
    firm = ""
    insp = ""
    for f in c.get("facts") or []:
        lab = f.get("label", "")
        if ("제조소" in lab) or ("업체" in lab):
            firm = f.get("value", "")
        elif "실사" in lab:
            insp = f.get("value", "")
    firm = firm or c.get("headline_target", "") or str(c.get("id", ""))
    return f"{firm} · 실사 {insp}" if insp else firm


def merge_fda483_disclosures(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """관찰 원문 없는 FDA 483 공개 카드 다건을 목록 카드 1장으로 접는다(순수·결정론).

    2건 미만이면 무변화. 대표 = `id` 오름차순 첫 카드 — 슬롯을 디제스트 문안으로 결정론
    재작성하고 `merged_count`/`merged_items`(시설·실사일 목록)/`merged_noun='건'` 을 실어
    렌더러의 '전체 N건' 토글이 목록을 편다. 나머지 멤버는 발행본에서 제외(그 카드들의
    Notion Status 는 Routine 이 이미 Processed 처리 — 유실 아님). 분석된 483(상세/심층 보유)
    과 회수 병합은 대상 아님(이 함수는 content-less 483 만)."""
    idxs = [i for i, c in enumerate(cards) if _is_content_less_483(c)]
    if len(idxs) < 2:
        return cards
    idxs.sort(key=lambda i: str(cards[i].get("id", "")))
    rep_src = cards[idxs[0]]
    n = len(idxs)
    facilities = [_fda483_facility_line(cards[i]) for i in idxs]
    rep_fac = _fda483_facility_line(rep_src).split(" · 실사")[0]
    rep = dict(rep_src)
    rep["title_issue"] = "483 실사기록 다건 공개"
    # [문구 정확화 2026-07-20] 종전 "스캔·비공개로" 는 단정이 지나쳤다 — 전수 점검(70건) 결과
    # 텍스트층은 정상인데 관찰 상세가 수록되지 않은 PDF 도 다수였다. 사유를 단정하지 않는다.
    rep["summary"] = (
        f"FDA OII FOIA 전자열람실에 483 실사기록 {n}건이 공개됐다. 개별 관찰 원문이 제공되지 "
        "않아(스캔본이거나 관찰 상세 미수록) 시설·실사일 목록만 확인 가능하다.")
    rep["key_facts"] = [
        f"공개 건수: {n}건 (개별 관찰 상세 미공개)",
        f"대표 시설: {rep_fac} 외 {n - 1}곳",
        "출처: FDA OII FOIA 전자열람실",
    ]
    rep["implication"] = (
        "개별 관찰 내용이 공개되지 않은 실사기록 공개는 그 자체로 조치 신호는 아니다. "
        "국내 업체는 목록에 자사 공급망 시설이 있는지 확인하고, 있으면 원문(PDF)을 직접 "
        "열람할 필요가 있다.")
    rep["checks"] = [
        "목록에 자사 공급망·수급처 시설이 있는지 확인",
        "해당 시 FDA FOIA 원문(PDF) 직접 열람",
    ]
    rep["merged_count"] = n
    rep["merged_items"] = facilities
    rep["merged_noun"] = "건"
    rep.pop("quotes_translation", None)  # 디제스트엔 부적합(대표 1건의 번역 슬롯 제거)
    drop_ids = {str(cards[i].get("id")) for i in idxs[1:]}
    rep_id = str(rep_src.get("id"))
    out: list[dict[str, Any]] = []
    for c in cards:
        cid = str(c.get("id"))
        if cid in drop_ids:
            continue
        out.append(rep if cid == rep_id else c)
    return out


def extract_resource_notes(cards: list[dict[str, Any]]
                           ) -> "tuple[list[dict[str, Any]], list[dict[str, Any]]]":
    """(event_cards, resources). resource 판정 = agency ∈ RESOURCE_AGENCIES ∧
    (type_tag=='GMP News' or card_type=='규제 소식'). 순수·순서보존.

    해설·교육성 2차 소스(현재 ECA GMP News 7장 유형)를 이벤트 카드 목록에서 분리해
    브리프 하단 '전문지 브리핑' 전용 섹션으로 렌더하기 위한 결정론 변환. 카드 dict 에서
    사실 재작성 0 으로 추출(§1 자료구조) — sources 는 그대로 통과하되, 렌더는 official_url
    (실기사)만 쓰고 info_url(RSS 피드)은 쓰지 않는다(렌더 쪽 책임).

    [전문지 브리핑 v2 2026-07-13 §3 정직성 게이트] `summary` 는 카드에 본문 흡수 흔적이 있을
    때만 포함한다. 수집 RSS 가 제목만 준 얇은 입력을 LLM 이 "원문에 없다"고 오서술하는 문제(§3
    배경)를 근본 차단 — 흡수 흔적이 없으면 summary 키 자체를 note 에서 제거한다(partial 의
    `{% if r.summary %}` 게이트가 그대로 요지 줄을 생략).

    [신호 일반화 2026-07-20] 판정은 `source_body_captured`(card_scaffold 전 소스 공통 신호)
    우선, 없으면 구 `source_excerpt_present`(ECA/전문지 소스 1곳에만 붙던 반창고)로 폴백한다.
    같은 결함(원문 확보 여부를 하류가 알 방법이 없음)이 Health Canada 회수 카드 6건에서
    재발한 것을 계기로 소스별 반창고를 걷어내고 전 소스 공통 신호로 옮겼다 — 구 키는 이미
    발행된 브리프(스캐폴드에 `source_excerpt_present` 만 있는 과거 발행분)를 재조립할 때도
    깨지지 않도록 호환 폴백으로만 남긴다.
    """
    events: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    for c in cards:
        is_resource = (c.get("agency") in RESOURCE_AGENCIES
                       and (c.get("type_tag") == "GMP News"
                            or c.get("card_type") == "규제 소식"))
        if is_resource:
            note = {
                "id": c["id"],
                "title": c["title_issue"],
                "original_title": c.get("headline_target", ""),
                "agency": c["agency"],
                "type_tag": c.get("type_tag", ""),
                "sources": c.get("sources") or {},
            }
            if c.get("source_body_captured") or c.get("source_excerpt_present"):
                note["summary"] = c.get("summary", "")
            resources.append(note)
        else:
            events.append(c)
    return events, resources


def assemble_publish_brief(scaffold: dict[str, Any], delta: dict[str, Any],
                           *, strict: bool = True,
                           deep_deltas: dict[str, dict[str, Any]] | None = None
                           ) -> tuple[dict[str, Any], AssembleReport]:
    """(scaffold, delta) → (발행본 브리프, 보고). 입력 불변(순수).

    strict=True(기본): 델타 카드가 스캐폴드에 없거나·채택 카드에 빈 슬롯이 남으면 AssembleError.

    deep_deltas(선택, additive): {document_id: {"deep_analysis": {...}, "source_text": str}}.
    지정 시 채택 필터 이후 inject_slots.inject_deep_analysis 로 카드별 게이트 검증 후 주입한다.
    게이트 FAIL 카드는 deep_analysis 없이 6슬롯만으로 발행(카드 단위 graceful degrade —
    이 실패는 report.errors 에 넣지 않고 report.warnings 에만 남겨 발행을 막지 않는다).
    """
    report = AssembleReport()
    delta_cards = delta.get("cards") or {}
    if not isinstance(delta_cards, dict):
        raise AssembleError("delta.cards 는 {card.id: {슬롯}} 객체여야 함")
    adopted_ids = set(delta_cards)

    scaffold_cards = scaffold.get("cards") or []
    scaffold_ids = {c.get("id") for c in scaffold_cards}

    # 게이트 1: 델타의 모든 카드 id 가 스캐폴드에 존재해야 한다(다른 run 스캐폴드 감지).
    ghost = sorted(cid for cid in adopted_ids if cid not in scaffold_ids)
    if ghost:
        report.errors.append(
            f"델타 카드 id {len(ghost)}건이 스캐폴드에 없음(스캐폴드가 다른 intake run?): "
            + ", ".join(ghost[:8]) + (" …" if len(ghost) > 8 else ""))

    # 슬롯 주입(코드 가드는 inject_slots 가 수행). strict 는 상위로 위임.
    injected = inject_slots.inject_llm_slots(scaffold, delta, strict=strict)

    # 채택 필터 + render_order 정렬 보존.
    all_cards = injected.get("cards") or []
    ordered = sorted(all_cards, key=lambda c: c.get("render_order", 0))
    adopted_cards: list[dict[str, Any]] = []
    for c in ordered:
        if c.get("id") in adopted_ids:
            adopted_cards.append(c)
        else:
            report.dropped += 1
            report.dropped_ids.append(c.get("id"))

    # 낡은 스캐폴드가 들고 있는 구 어휘를 **가장 먼저** 교정한다 — 아래 483 디제스트 접기가
    # facts 에서 시설 줄(`merged_items`)을 만들어내므로, 여기서 안 고치면 구 어휘가 목록으로
    # 복제된다(2026-07-20 실측: '원문 미기재 · 실사 02/13/2026').
    _normalize_legacy_absence_labels(adopted_cards, report)

    # [심층분석 fan-out 배선] deep_deltas 지정 시 채택분 카드에 한해 게이트 검증 후 주입.
    # additive·선택 — 미지정 시 기존 동작과 완전 동일. 게이트 FAIL 은 발행을 막지 않는다
    # (해당 카드는 deep_analysis=null 그대로 6슬롯만 발행 — inject_deep_analysis 자체 규약).
    #
    # ★ 디제스트 접기(`merge_fda483_disclosures`) **앞**이어야 한다(2026-07-20 순서 수정).
    #   접기 판정이 "결정론 상세도 심층분석도 없음"이므로, 원문 재추출로 관찰이 되살아나는
    #   카드를 접은 **뒤에** 주입하면 그 카드는 이미 사라진 뒤다 — 실제로 관찰이 있는 483 이
    #   "스캔·비공개" 목록으로 접혀 나갔다(전수 점검 실측 2건).
    if deep_deltas:
        # ★ 번역(observations_ko) 병합 **전에** 483 관찰을 원문에서 재추출한다 — 순서 불가침.
        #   병합이 number 를 키로 쓰므로, 스캐폴드의 관찰 번호가 틀린 채로 병합하면 번역이
        #   엉뚱한 관찰에 붙는다(2026-07-20 193490: 번호 `1,1,3,4,2,3,4`).
        staged = {"cards": adopted_cards}
        _refresh_483_observations(staged, deep_deltas, report)
        _refresh_wl_violations(staged, deep_deltas, report)
        deep_report = inject_slots.inject_deep_analysis(staged, deep_deltas)
        for w in deep_report.warnings:
            report.warnings.append(f"[deep] {w}")
        for e in deep_report.errors:
            report.warnings.append(f"[deep] {e}")

    # [FDA 483 공개 디제스트 2026-07-13] 관찰 원문 없는 483 공개 카드 다건 → 목록카드 1장.
    adopted_cards = merge_fda483_disclosures(adopted_cards)

    # [업계 브리핑 노트 2026-07-13] 해설·교육성 2차 소스(ECA GMP News 등) → 이벤트 카드에서
    # 분리해 브리프 하단 전용 섹션으로. 아래 render_order 재부여·빈슬롯 게이트·adopted 집계는
    # 남은 이벤트 카드에만 적용된다(resource 는 별도 브리프 메타로 실린다).
    adopted_cards, resource_notes = extract_resource_notes(adopted_cards)

    # render_order 0..N-1 연속 재부여(원 상대순서 보존).
    for i, c in enumerate(adopted_cards):
        c["render_order"] = i

    # 게이트 2: 채택 카드에 빈 필수 슬롯 0.
    for c in adopted_cards:
        for k in _REQUIRED_STR_SLOTS:
            if not c.get(k):
                report.errors.append(f"채택 카드 {c.get('id')!r}: 빈 슬롯 {k}")
        for k in _REQUIRED_LIST_SLOTS:
            if not c.get(k):
                report.errors.append(f"채택 카드 {c.get('id')!r}: 빈 슬롯 {k}")

    report.adopted = len(adopted_cards)
    report.resources = len(resource_notes)

    # 브리프 메타 재계산(assemble_web_brief 규약 미러) — 채택분 기준.
    out = copy.deepcopy(injected)
    out["cards"] = adopted_cards
    brief = out.setdefault("brief", {})

    # 게이트 4: 483 관찰 블록의 국문 병기 결손 — 발행 차단(render fail-closed 게이트 선행 검출).
    # `web/render.py` 의 `validate_483_observations` 가 배포 단계에서 같은 검사를 하지만, 거기서
    # 죽으면 원인이 조립에서 멀어 진단이 오래 걸린다. 같은 규약을 조립에서 먼저 확인한다.
    report.errors.extend(
        _lint_483_observation_ko(out.get("cards") or [], report.refreshed_483_ids))

    # 게이트 3: 원문을 확보한 카드의 거짓 부재 서술(2026-07-20 사고) — 발행 차단.
    # deep/결정론 주입이 **끝난 뒤** 검사해야 확보 증거를 정확히 본다(순서 불가침).
    report.errors.extend(lint_false_absence_claims(out.get("cards") or []))

    # 게이트 5: 근거 없는 원문 부재 단정(facts 칸) — 발행 차단.
    # 게이트 3 이 산문만 보던 사각을 메운다 — facts 는 코드 verbatim 이라 검사 대상에서
    # 뺐었는데, 그 칸 값 자체가 근거 없는 부재 단정일 수 있다는 게 HC 회수 카드 6건 실측으로
    # 드러났다(lint_unverified_absence_labels docstring 참고).
    report.errors.extend(lint_unverified_absence_labels(out.get("cards") or []))

    # agencies = event 카드 + resource 노트의 agency 합집합(카드 순서 우선, 중복 제거) —
    # 리소스로 빠진 소스(예: ECA)가 헤더 기관 목록에서 사라지지 않게 한다.
    agencies = _distinct_in_order(
        [c.get("agency", "") for c in adopted_cards]
        + [r.get("agency", "") for r in resource_notes])
    categories = _distinct_in_order([c.get("category", "") for c in adopted_cards])
    evidence = {"A": 0, "B": 0, "C": 0}
    for c in adopted_cards:
        lvl = c.get("evidence_level")
        if lvl in evidence:
            evidence[lvl] += 1
    brief["agencies"] = agencies
    brief["categories"] = categories
    if resource_notes:
        brief["resources"] = resource_notes

    coverage = brief.setdefault("coverage", {})
    # intake_total = 실수집 총건(진실값) 보존. rendered = 채택(이벤트) 수. evidence = 채택 재집계.
    coverage["rendered"] = len(adopted_cards)
    coverage["evidence"] = evidence
    coverage.setdefault("intake_total", scaffold.get("brief", {})
                        .get("coverage", {}).get("intake_total", len(adopted_cards)))
    if resource_notes:
        coverage["resources"] = len(resource_notes)

    if strict and report.errors:
        raise AssembleError("발행본 조립 검증 실패:\n  - " + "\n  - ".join(report.errors))
    return out, report


def _write_json(path: str, payload: dict[str, Any]) -> None:
    """emit_web_brief_file / web.render._write_json 과 동형(indent=1·LF·후행개행·UTF-8)."""
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def _refresh_483_observations(out: dict[str, Any], deep_deltas: dict[str, Any],
                              report: "AssembleReport") -> None:
    """483 카드의 결정론 관찰을 deep 델타의 `source_text` 로 **재추출**해 최신 파서 결과로 맞춘다.

    왜 필요한가 — 스캐폴드(grm-intake 아티팩트)는 수집 시점의 파서로 굳은 산출물이라, 그 뒤
    파서를 고쳐도 **이미 만들어진 그 주 스캐폴드는 영영 낡은 채로 남는다**. 재수집으로는 못
    고친다(스캐폴드는 Notion 의 New 행에서만 생성되는데 그 행들은 Routine 이 이미 소비했다 —
    2026-07-20 실측: 재수집 시 `handoff 후보 New row 0건`). 그러면 남는 길은 사람이 아티팩트를
    손으로 고쳐 로컬 조립하는 것뿐인데, 그 경로가 바로 이날 미발행 브리프를 main 에 흘려 사이트
    배포를 4시간 멈춘 사고의 원인이다.

    그래서 조립 시점에 원문에서 다시 뽑는다. `source_text` 는 deep 델타에 이미 커밋돼 있으므로
    (fan-out 이 원문 전문을 싣는다) 입력·코드 모두 저장소 안에 있고 **재현 가능**하다.

    **[신설도 한다 2026-07-20]** 종전엔 이미 관찰 블록이 있는 카드만 갱신했다. 그런데 수집
    시점에 추출이 실패하면 블록 자체가 없고, 그 카드는 "관찰 원문 없음"으로 디제스트에 접혀
    "스캔·비공개" 라고 발행된다 — 실제로는 원문에 관찰이 있는데도. 전수 점검(70건) 결과 그런
    카드가 실재했다(193570·193583). 그래서 `source_text` 만 있으면 **없던 블록도 만든다**.
    블록이 생기면 그 카드는 `merge_fda483_disclosures` 의 접힘 대상에서 자동으로 빠진다.

    안전 규약(하나라도 어긋나면 스캐폴드 값을 그대로 둔다 — 데이터를 지우지 않는 방향):
      · 483 카드만 대상(`deterministic_detail.type == "fda_483_observations"` 이거나,
        블록이 아예 없는 `fda483-` 카드)
      · deep 델타에 그 카드의 비어있지 않은 `source_text` 가 있을 때만
      · 재추출 결과가 **비어있지 않을 때만** 교체(파서가 degrade 해도 기존 관찰 보존)
      · 다른 타입의 `deterministic_detail` 을 덮어쓰지 않는다
    바뀐 카드는 report 에 남긴다 — 조용한 교체 금지(발행 로그로 육안 확인 가능).
    """
    import collect_fda_483 as _f          # 지연 import — 조립 경로가 수집기 로드에 묶이지 않게

    for card in out.get("cards", []):
        dd = card.get("deterministic_detail")
        if isinstance(dd, dict):
            if dd.get("type") != "fda_483_observations":
                continue                  # 다른 결정론 블록 보유 — 덮어쓰지 않는다
        elif not str(card.get("id", "")).startswith("fda483-"):
            continue                      # 블록 없음 + 483 아님 — 대상 아님
        payload = deep_deltas.get(card.get("id")) or {}
        source_text = payload.get("source_text") if isinstance(payload, dict) else None
        if not (isinstance(source_text, str) and source_text.strip()):
            continue
        fresh = _f._extract_483_observations_from_text(source_text)
        if not fresh:
            continue                      # degrade — 기존 관찰 유지
        before = [(o.get("number"), o.get("deficiency"), o.get("detail"))
                  for o in (dd or {}).get("observations", []) if isinstance(o, dict)]
        after = [(o["number"], o["deficiency"], o["detail"]) for o in fresh]
        if before == after:
            continue
        card["deterministic_detail"] = {
            "type": "fda_483_observations", "count": len(fresh), "observations": fresh,
        }
        report.refreshed_483_ids.append(str(card.get("id")))
        report.warnings.append(
            f"[483] {card.get('id')}: 관찰 원문 재추출 — 번호 "
            f"{[b[0] for b in before]} → {[a[0] for a in after]} "
            f"({'스캐폴드가 낡은 파서 산출' if before else '스캐폴드에 블록 없음 — 신설'})")


def _card_has_source_body(card: dict[str, Any]) -> bool:
    """이 카드가 원문 본문을 실제로 확보했는가(결정론 상세 블록 또는 심층분석 보유)."""
    dd = card.get("deterministic_detail")
    if isinstance(dd, dict) and dd.get("count"):
        return True
    return isinstance(card.get("deep_analysis"), dict) and bool(card["deep_analysis"])


def lint_false_absence_claims(cards: list[dict[str, Any]]) -> list[str]:
    """원문을 확보한 카드가 "원문에 위반내용이 없다"고 주장하면 그 사유 목록을 돌려준다.

    반환이 비어있지 않으면 호출부가 `report.errors` 로 올려 **발행을 차단**한다(strict).
    상수 `_FALSE_ABSENCE_RE` 주석의 2026-07-20 사고 재발 방지 게이트.
    """
    errs: list[str] = []
    for c in cards:
        if not _card_has_source_body(c):
            continue
        for slot in _FALSE_ABSENCE_SLOTS:
            v = c.get(slot)
            if isinstance(v, str) and _FALSE_ABSENCE_RE.search(v):
                errs.append(
                    f"카드 {c.get('id')!r}: 원문을 확보했는데 {slot} 가 부재를 주장 — "
                    f"{_FALSE_ABSENCE_RE.search(v).group(0)!r}")
        for i, v in enumerate(c.get("key_facts") or []):
            if isinstance(v, str) and _FALSE_ABSENCE_RE.search(v):
                errs.append(
                    f"카드 {c.get('id')!r}: 원문을 확보했는데 key_facts[{i}] 가 부재를 주장 — "
                    f"{_FALSE_ABSENCE_RE.search(v).group(0)!r}")
    return errs


def _normalize_legacy_absence_labels(cards: list[dict[str, Any]],
                                     report: "AssembleReport") -> None:
    """스캐폴드에 굳어 있는 구 어휘(`"원문 미기재"` 등)를 값 부재 표기로 교정한다.

    왜 필요한가 — 스캐폴드는 수집 시점에 굳는 아티팩트라, 어휘를 고쳐도 **이미 만들어진 그 주
    스캐폴드는 낡은 문자열을 그대로 들고 있다**. 그대로 발행하면 거짓이고(원문에 값이 있는데도
    "원문에 없다"고 말한다), 게이트 5 가 막으면 그 주 브리프를 **영영 재조립할 수 없다**
    (2026-07-20 실측: 07-20 스캐폴드에 HC 4장).

    이 교정은 **사실을 바꾸지 않는다** — 값이 비어 있다는 사실은 그대로이고, 그 사실에 대한
    거짓 진술("원문에 없다")을 참인 진술("우리가 확인하지 못했다")로 바꿀 뿐이다. 그래서
    코드-verbatim 불변식(사실 보존)을 깨지 않는다. 원문이 실제로 무엇을 담았는지는 수집기
    수정으로만 회복되며(예: HC `Brand(s)` 폴백), 이 함수는 그 회복을 대신하지 않는다.

    바뀐 항목은 전부 report 에 남긴다 — 조용한 교정 금지.
    """
    from card_scaffold import VALUE_UNKNOWN     # 지연 import — 표기 값의 단일 출처

    for card in cards:
        for fact in card.get("facts") or []:
            value = fact.get("value")
            if isinstance(value, str) and _UNVERIFIED_ABSENCE_RE.match(value):
                fact["value"] = VALUE_UNKNOWN
                report.warnings.append(
                    f"[표기] {card.get('id')}: facts[{fact.get('label')}] {value!r} → "
                    f"{VALUE_UNKNOWN!r} (낡은 스캐폴드의 구 어휘 교정 — 원문 부재 단정 제거)")


def lint_unverified_absence_labels(cards: list[dict[str, Any]]) -> list[str]:
    """facts 칸 값이 "원문에 이 값이 없다"를 근거 없이 단정하면 사유 목록을 돌려준다.

    [게이트 5, 2026-07-20] 게이트 3(`lint_false_absence_claims`)는 summary·implication·
    title_issue·key_facts 같은 산문 슬롯만 검사한다 — facts 칸은 "코드 verbatim 필드라 그 칸의
    '원문 미기재'는 그 값이 실제로 원문에 없다는 정직한 표기"라는 가정 아래 일부러 검사 대상에서
    뺐다. **그 가정이 반증됐다**: Health Canada 회수 카드 6건이 `업체 | 원문 미기재` 로
    발행됐는데(Apotex Inc.·Servier Canada Inc.·Kao Canada Inc.·Becton Dickinson Canada
    Inc.·Jamp Pharma·BC Cancer), 원문에는 업체명이 분명히 명시돼 있었다. facts 값은 수집기가
    원문 dict 에서 특정 키를 못 찾았을 때 채우는 자리표시자일 뿐 원문을 필드 단위로 대조한
    결과가 아니므로, 코드는 애초에 "원문에 없다"고 단정할 자격이 없다.

    그래서 facts 값이 **그 값 전체로서** 원문/본문 부재 단정 표기(원문 미기재·원문에 없음·
    원문 미상·원문 미표기 등, `_UNVERIFIED_ABSENCE_RE`)이면 발행을 막는다. 값 **안에** 그런
    문구가 섞여 있을 뿐인 경우(예: "관찰 1: 부적격 사유 미기재" — 업체가 실제로 사유를 안
    적었다는 **사실** 서술)는 정상 통과해야 하므로 부분일치가 아니라 값 전체 일치만 잡는다.

    `merged_items`(483 디제스트가 facts 에서 만들어내는 시설 목록)도 함께 본다. 정상 흐름에선
    `_normalize_legacy_absence_labels` 가 접기 **전에** facts 를 고쳐 이 목록이 오염되지 않지만,
    실제로 오염된 채 발행된 전례가 있다("원문 미기재 · 실사 02/13/2026"). 두 장치가 어긋나면
    조용히 새는 자리라 **사후 조건**으로 한 번 더 확인한다.
    """
    errs: list[str] = []
    for c in cards:
        for f in c.get("facts") or []:
            if not isinstance(f, dict):
                continue
            label = f.get("label", "")
            value = f.get("value", "")
            if isinstance(value, str) and _UNVERIFIED_ABSENCE_RE.match(value):
                errs.append(
                    f"카드 {c.get('id')!r}: facts[{label}] 가 원문 부재를 단정 — {value!r}. "
                    f"코드는 원문을 필드 단위로 확인하지 않는다(card_scaffold.VALUE_UNKNOWN 사용)")
        for i, item in enumerate(c.get("merged_items") or []):
            # 목록 항목은 "<업체> · 실사 <일자>" 형태라 앞부분만 떼어 값 전체 일치로 본다.
            head = str(item).split(" · ")[0] if isinstance(item, str) else ""
            if head and _UNVERIFIED_ABSENCE_RE.match(head):
                errs.append(
                    f"카드 {c.get('id')!r}: merged_items[{i}] 가 원문 부재를 단정 — {item!r}. "
                    f"접기 전 facts 교정이 누락됐다(_normalize_legacy_absence_labels)")
    return errs


def _lint_483_observation_ko(cards: list[dict[str, Any]],
                             only_ids: "list[str] | None" = None) -> list[str]:
    """483 관찰 블록의 국문 병기 결손을 조립 단계에서 잡는다(발행 차단 사유 목록 반환).

    정본 규약은 `web/render.validate_483_observations`(배포 fail-closed)다. 여기서는 같은
    규약을 **먼저** 확인만 한다 — 결정론 관찰을 새로 되살린 카드가 번역 없이 조립을 통과하면
    배포 단계에서 브리프 전체가 막히고, 그때는 원인이 조립에서 멀어 진단이 오래 걸린다.

    `only_ids` 를 주면 그 카드들만 본다(운영 기본 = 이번 조립에서 관찰을 새로 만든 카드).
    소급 검사를 하지 않는 이유: 원문·국문 병기는 2026-07-09 에 생긴 요구라, 그 이전 발행분을
    지금 기준으로 재검사하면 손대지도 않은 과거 브리프가 통째로 막힌다.
    """
    errs: list[str] = []
    targets = set(only_ids) if only_ids is not None else None
    for c in cards:
        if targets is not None and str(c.get("id")) not in targets:
            continue
        dd = c.get("deterministic_detail")
        if not (isinstance(dd, dict) and dd.get("type") == "fda_483_observations"):
            continue
        for obs in dd.get("observations") or []:
            num = obs.get("number", "?")
            if not str(obs.get("deficiency_ko") or "").strip():
                errs.append(f"카드 {c.get('id')!r} 관찰 #{num}: deficiency_ko 없음")
            if str(obs.get("detail") or "").strip() and not str(obs.get("detail_ko") or "").strip():
                errs.append(f"카드 {c.get('id')!r} 관찰 #{num}: detail 이 있는데 detail_ko 없음")
    return errs


def _refresh_wl_violations(out: dict[str, Any], deep_deltas: dict[str, Any],
                           report: "AssembleReport") -> None:
    """[2026-07-20] Warning Letter 카드의 **결정론 위반항목 블록을 조립 시점에 원문에서 만든다.**

    `_refresh_483_observations` 와 같은 이유(스캐폴드는 수집 시점 파서로 굳는다)에 더해, WL 은
    한 발 더 나간다 — 결정론 상세층 자체가 2026-07-20 에야 생겼으므로 그 이전 스캐폴드에는
    `deterministic_detail` **키가 아예 없다.** 그래서 이 함수는 갱신뿐 아니라 **신설**도 한다.
    입력(`source_text`)은 deep 델타로 저장소에 커밋돼 있고 파서도 저장소 안에 있으므로
    산출은 재현 가능하다(사람이 아티팩트를 손으로 고치는 경로를 만들지 않는다 — 그 경로가
    2026-07-20 미발행 브리프 유입 사고의 원인이었다).

    안전 규약(하나라도 어긋나면 카드를 그대로 둔다):
      · `card_type == "Warning Letter"` 인 카드만 대상
      · deep 델타에 그 카드의 비어있지 않은 `source_text` 가 있을 때만
      · 추출 결과가 비어있지 않을 때만(형식이 다른 편지 → 블록 없이 발행)
      · 이미 다른 타입의 `deterministic_detail` 이 있으면 건드리지 않는다(덮어쓰기 금지)
    바뀐 카드는 report 에 남긴다 — 조용한 주입 금지.
    """
    import collect_intake as _ci          # 지연 import — 조립 경로가 수집기 로드에 묶이지 않게

    for card in out.get("cards", []):
        if card.get("card_type") != "Warning Letter":
            continue
        dd = card.get("deterministic_detail")
        if isinstance(dd, dict) and dd.get("type") != "wl_violations":
            continue                      # 다른 결정론 블록 보유 — 덮어쓰지 않는다
        payload = deep_deltas.get(card.get("id")) or {}
        source_text = payload.get("source_text") if isinstance(payload, dict) else None
        if not (isinstance(source_text, str) and source_text.strip()):
            continue
        fresh = _ci.extract_wl_violations_from_text(source_text)
        if not fresh:
            continue                      # degrade — 기존 상태 유지
        before = [(v.get("number"), v.get("statement")) for v in (dd or {}).get("violations", [])
                  if isinstance(v, dict)]
        after = [(v["number"], v["statement"]) for v in fresh]
        if before == after:
            continue
        card["deterministic_detail"] = {
            "type": "wl_violations", "count": len(fresh), "violations": fresh,
        }
        report.warnings.append(
            f"[WL] {card.get('id')}: 위반항목 {len(fresh)}건을 원문에서 결정론 추출 "
            f"(조항 {[v['citation'] for v in fresh]}) — 스캐폴드에 {'낡은 블록' if before else '블록 없음'}")


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="빈슬롯 스캐폴드 + 라우틴 델타 → 발행본(채택분) 조립(순수·결정론).")
    ap.add_argument("--scaffold", required=True,
                    help="§1-B 이미터 스캐폴드 JSON(전 intake 카드·빈 슬롯, grm-web-card/v1)")
    ap.add_argument("--delta", required=True,
                    help="라우틴 델타 JSON({cards:{id:{슬롯}}, tldr:[]})")
    ap.add_argument("--out", required=True,
                    help="발행본 출력 경로(web/data/briefs/brief_web_{date}.json)")
    ap.add_argument("--deep", default=None,
                    help="[선택] 심층분석 델타 JSON 경로 "
                         "({document_id:{deep_analysis,source_text}}). 미지정 시 기존 동작과 완전 동일.")
    args = ap.parse_args(argv)

    scaffold = _load_json(args.scaffold)
    delta = _load_json(args.delta)
    deep_deltas = _load_json(args.deep) if args.deep else None
    try:
        out, report = assemble_publish_brief(scaffold, delta, strict=True, deep_deltas=deep_deltas)
    except (inject_slots.SlotInjectionError, AssembleError) as e:
        print(f"조립 거부:\n{e}", file=sys.stderr)
        return 2

    for w in report.warnings:
        print(f"WARN {w}", file=sys.stderr)
    _write_json(args.out, out)
    cov = out["brief"]["coverage"]
    deep_merged = sum(1 for c in out["cards"] if c.get("deep_analysis"))
    print(f"조립 완료: 채택 {report.adopted}카드 (스킵 {report.dropped}) → {args.out}")
    print(f"  브리핑 노트 {report.resources}건")
    if args.deep:
        print(f"  deep_analysis: 병합 {deep_merged}카드 (게이트 보류는 위 WARN 참고)")
    print(f"  coverage: 수집 {cov.get('intake_total')} · 카드 {cov['rendered']} · "
          f"Evidence A{cov['evidence']['A']}/B{cov['evidence']['B']}/C{cov['evidence']['C']}")
    print(f"  agencies: {out['brief']['agencies']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
