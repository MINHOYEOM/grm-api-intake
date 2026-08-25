"""LLM 슬롯 주입 헬퍼 — v16 프롬프트 델타를 scaffold 빈슬롯 브리프에 결정론 주입.

운영 콘텐츠 트랙의 브리지: v16 프롬프트가 산출하는 **LLM 슬롯 델타 JSON**
(`{cards:{card.id:{슬롯}}, tldr:[]}`)을 `card_scaffold.assemble_web_brief` 가 만든
**빈 슬롯 grm-web-card/v1 브리프**에 `card.id` 로 주입해 **산문 채운 완성 브리프 JSON**을
만든다(수기 병합 제거).

설계 가드(추가만):
  - **순수·결정론**: 같은 (brief, delta) → 바이트 동일 출력. 네트워크·현재시각·LLM 호출 0.
  - **코드 verbatim 필드 불가침**: `facts`·`quotes[].original`·`sources`·`headline_target`·
    배지(agency·card_type·category·modality·evidence_*·signal_*·type_tag)·render_order/
    group/group_label·id·merged_* 는 절대 변경 안 함. 주입은 LLM 슬롯에만:
    title_issue·summary·key_facts·implication·checks·비KO quotes[].translation + brief.tldr.
  - **평문 가드**: 주입 값에 표현 틀 마크업(`{{`·`<callout`·`<table`·`###`·`> `·선행 `- `·`**`)
    이 있으면 거부(LLM 마크업 유출 방어 — 값은 평문이어야 렌더 안전).
  - **positional 번역**: `quotes_translation[j]` ↔ `card.quotes[j]`. 비KO("") 자리만 채우고
    KO(null) 자리는 그대로 둔다. 길이 어긋나면 실패.
  - **길이(§13.1-12)**: title_issue ≤25자 · key_facts ≤4 · checks 2~3 · tldr 3 — 위반 시 실패.

`card_scaffold.py`·`web/render.py`·골든·워크플로는 불변(이 모듈은 신규 추가).
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass, field
from typing import Any

# 입력 A scaffold 가 LLM 채울 자리로 둔 빈 placeholder(card_scaffold.to_web_card §3):
#   문자열 슬롯="" · 리스트 슬롯=[] · 비KO quote translation="" · KO quote translation=None.
# 주입 대상 슬롯(코드 필드 아님) — 이 키만 델타에서 카드로 옮긴다.
_STR_SLOTS = ("title_issue", "summary", "implication")
_LIST_SLOTS = ("key_facts", "checks")

# 평문 가드(§3·불변식 #6) — 슬롯 값에 들어가면 안 되는 표현 틀 마크업 부분문자열.
# `> `(인용)·`- `(불릿)은 선행/줄머리 형태만 마크업으로 본다(문장 중간의 하이픈/부등호 허용).
_MARKUP_SUBSTRINGS = ("{{", "<callout", "<table", "<tr", "<td", "###", "**")

# 길이 한도(§13.1-12). title_issue 는 문자 수(한글 1자=1), 리스트는 항목 수.
_MAX_TITLE_ISSUE = 25
_MAX_KEY_FACTS = 4
_CHECKS_RANGE = (2, 3)      # 포함 범위
_TLDR_LEN = 3


class SlotInjectionError(ValueError):
    """주입 검증 실패(코드 가드 위반) — 메시지에 사유 목록."""


@dataclass
class InjectionReport:
    """주입 검증 결과. errors 가 비어야 주입 가능(strict)."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _markup_violation(value: str) -> str | None:
    """평문 슬롯 값의 마크업 토큰(없으면 None). 선행/줄머리 `> `·`- ` 포함."""
    for tok in _MARKUP_SUBSTRINGS:
        if tok in value:
            return tok
    if value.startswith("> ") or "\n> " in value:
        return "> "
    if value.startswith("- ") or "\n- " in value:
        return "- "
    return None


def _check_str_slot(report: InjectionReport, cid: str, key: str, value: Any) -> None:
    if not isinstance(value, str):
        report.errors.append(f"{cid}.{key}: 문자열이어야 함 (got {type(value).__name__})")
        return
    tok = _markup_violation(value)
    if tok is not None:
        report.errors.append(f"{cid}.{key}: 마크업 토큰 {tok!r} — 평문만 허용")


def _check_list_slot(report: InjectionReport, cid: str, key: str, value: Any) -> None:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        report.errors.append(f"{cid}.{key}: 문자열 리스트여야 함")
        return
    for i, item in enumerate(value):
        tok = _markup_violation(item)
        if tok is not None:
            report.errors.append(f"{cid}.{key}[{i}]: 마크업 토큰 {tok!r} — 평문만 허용")


def validate_injection(brief: dict[str, Any], delta: dict[str, Any]) -> InjectionReport:
    """주입 전 검증(순수). errors=코드 가드 위반(주입 차단), warnings=비차단 보고.

    검사: card.id 정합(누락/유령) · positional 번역 길이·KO null 보존 · 평문 가드 ·
    길이(§13.1-12) · 슬롯 타입. 네트워크·현재시각 0.
    """
    report = InjectionReport()
    cards = brief.get("cards") or []
    delta_cards = delta.get("cards") or {}
    if not isinstance(delta_cards, dict):
        report.errors.append("delta.cards 는 {card.id: {슬롯}} 객체여야 함")
        delta_cards = {}

    brief_ids = {c.get("id") for c in cards}

    # 유령 키: 델타에 있으나 브리프에 없는 card.id (산문이 어느 카드에도 안 붙음) — 경고.
    for did in delta_cards:
        if did not in brief_ids:
            report.warnings.append(f"delta.cards[{did!r}]: 브리프에 없는 카드 id (무시됨)")

    for card in cards:
        cid = card.get("id", "")
        d = delta_cards.get(cid)
        if d is None:
            report.warnings.append(f"카드 {cid!r}: 델타에 산문 없음 — 슬롯 빈 채로 유지")
            continue
        if not isinstance(d, dict):
            report.errors.append(f"delta.cards[{cid!r}]: 객체여야 함")
            continue

        for key in _STR_SLOTS:
            if key in d:
                _check_str_slot(report, cid, key, d[key])
        if "title_issue" in d and isinstance(d["title_issue"], str):
            if len(d["title_issue"]) > _MAX_TITLE_ISSUE:
                report.errors.append(
                    f"{cid}.title_issue: {len(d['title_issue'])}자 — ≤{_MAX_TITLE_ISSUE} 초과")

        for key in _LIST_SLOTS:
            if key in d:
                _check_list_slot(report, cid, key, d[key])
        if "key_facts" in d and isinstance(d["key_facts"], list):
            if len(d["key_facts"]) > _MAX_KEY_FACTS:
                report.errors.append(
                    f"{cid}.key_facts: {len(d['key_facts'])}개 — ≤{_MAX_KEY_FACTS} 초과")
        if "checks" in d and isinstance(d["checks"], list) and d["checks"]:
            lo, hi = _CHECKS_RANGE
            if not (lo <= len(d["checks"]) <= hi):
                report.errors.append(
                    f"{cid}.checks: {len(d['checks'])}개 — {lo}~{hi} 범위 벗어남")

        _validate_quotes(report, cid, card.get("quotes") or [], d.get("quotes_translation"))

    # brief tldr — 제공·비어있지 않으면 정확히 3개(§13.1) + 평문.
    if "tldr" in delta:
        tldr = delta["tldr"]
        if not isinstance(tldr, list) or not all(isinstance(x, str) for x in tldr):
            report.errors.append("delta.tldr: 문자열 리스트여야 함")
        else:
            if tldr and len(tldr) != _TLDR_LEN:
                report.errors.append(f"delta.tldr: {len(tldr)}개 — 정확히 {_TLDR_LEN}개여야 함")
            elif not tldr:
                report.warnings.append("delta.tldr 비어있음 — 브리프 제목이 날짜 파생됨")
            for i, item in enumerate(tldr):
                tok = _markup_violation(item)
                if tok is not None:
                    report.errors.append(f"delta.tldr[{i}]: 마크업 토큰 {tok!r} — 평문만 허용")

    return report


def _validate_quotes(report: InjectionReport, cid: str, quotes: list[dict[str, Any]],
                     qt: Any) -> None:
    """positional 번역(§2·§3) 검증. qt 없으면 번역 미제공(비차단)."""
    if qt is None:
        if any(q.get("translation") == "" for q in quotes):
            report.warnings.append(
                f"카드 {cid!r}: 비KO 인용 번역 자리({sum(q.get('translation') == '' for q in quotes)}개) "
                "있으나 quotes_translation 미제공 — 번역 빈 채로 유지")
        return
    if not isinstance(qt, list):
        report.errors.append(f"{cid}.quotes_translation: 리스트여야 함")
        return
    if len(qt) != len(quotes):
        report.errors.append(
            f"{cid}.quotes_translation: 길이 {len(qt)} ≠ quotes {len(quotes)} (positional 어긋남)")
        return
    for j, (q, t) in enumerate(zip(quotes, qt)):
        slot = q.get("translation")
        if slot is None:                       # KO 세그먼트 — 번역 주입 금지
            if isinstance(t, str) and t:
                report.errors.append(
                    f"{cid}.quotes_translation[{j}]: KO 세그먼트(null) 자리에 번역 주입 시도")
        elif slot == "":                       # 비KO 빈 자리 — 채울 수 있음
            if isinstance(t, str) and t:
                tok = _markup_violation(t)
                if tok is not None:
                    report.errors.append(
                        f"{cid}.quotes_translation[{j}]: 마크업 토큰 {tok!r} — 평문만 허용")
        else:                                  # 이미 채워진 자리(scaffold 엔 없음) — 방어
            report.warnings.append(
                f"카드 {cid!r}: quotes[{j}] 이미 번역 있음 — quotes_translation[{j}] 무시")


def inject_llm_slots(brief: dict[str, Any], delta: dict[str, Any], *,
                     strict: bool = True) -> dict[str, Any]:
    """델타의 LLM 슬롯을 scaffold 브리프에 주입한 **새 브리프**를 반환(입력 불변, 순수).

    strict=True(기본): 코드 가드 위반(errors) 시 SlotInjectionError. False 면 검증 보고만 하고
    가능한 슬롯을 best-effort 주입(운영 기본은 strict — 잘못된 산문 발행 차단).
    `delta` 에 없는 키는 건드리지 않음(§2). 코드 verbatim 필드는 절대 변경 안 함.
    """
    report = validate_injection(brief, delta)
    if strict and report.errors:
        raise SlotInjectionError("LLM 슬롯 주입 검증 실패:\n  - " + "\n  - ".join(report.errors))

    out = copy.deepcopy(brief)
    delta_cards = delta.get("cards") or {}
    if not isinstance(delta_cards, dict):
        delta_cards = {}

    for card in out.get("cards") or []:
        d = delta_cards.get(card.get("id"))
        if not isinstance(d, dict):
            continue
        for key in _STR_SLOTS:
            if key in d and isinstance(d[key], str):
                card[key] = d[key]
        for key in _LIST_SLOTS:
            if key in d and isinstance(d[key], list):
                card[key] = list(d[key])
        _inject_quotes(card, d.get("quotes_translation"))

    if "tldr" in delta and isinstance(delta["tldr"], list):
        out.setdefault("brief", {})["tldr"] = list(delta["tldr"])

    return out


def _inject_quotes(card: dict[str, Any], qt: Any) -> None:
    """positional 번역 주입 — 비KO("") 자리에만 채우고 KO(null)는 그대로(§2)."""
    quotes = card.get("quotes") or []
    if not isinstance(qt, list) or len(qt) != len(quotes):
        return                                 # 미제공/어긋남 — validate 가 이미 처리
    for q, t in zip(quotes, qt):
        if q.get("translation") == "" and isinstance(t, str) and t:
            q["translation"] = t


# ─────────────────────────────────────────────────────────────────────────────
# [WL 심층분석 fan-out 2026-07-01] deep_analysis(7번째·선택 슬롯) 주입 — 6종 동결 슬롯
# inject_llm_slots 와 완전 별개 함수(서로 호출 안 함, additive). 카드별 fan-out(카드 1건 =
# 호출 1건, 독립 컨텍스트) 결과를 verify_deep_analysis 게이트로 검증한 뒤에만 주입한다.
# ─────────────────────────────────────────────────────────────────────────────
def inject_deep_analysis(brief: dict[str, Any],
                         deltas: dict[str, dict[str, Any]]) -> InjectionReport:
    """카드별 deep_analysis 델타를 검증 후 주입(in-place — 호출 전 필요시 별도 deepcopy).

    `deltas` = {document_id(=card.id): {"deep_analysis": {5섹션 dict}, "source_text": str}}.
    `source_text` 는 그 카드의 fan-out 입력 원문(handoff `deep_analysis_input.body_full`)이며
    verify_deep_analysis 가 인용 근거 대조에 쓴다.

    카드마다 `verify_deep_analysis.run_deep_analysis_gate`를 통과해야 주입된다. FAIL 카드는
    `card["deep_analysis"]`를 placeholder(None) 그대로 두고 report.errors 에 사유만 남긴다 —
    이 실패가 전체 브리프 발행을 막지 않는다(그 카드는 기존 6슬롯만으로 발행 — graceful
    degrade. 6종 동결 슬롯 트랙과 이 트랙은 서로 독립이라 한쪽 실패가 다른 쪽에 번지지 않는다).
    """
    import verify_deep_analysis as vda
    report = InjectionReport()
    cards_by_id = {c.get("id"): c for c in (brief.get("cards") or []) if isinstance(c, dict)}
    for doc_id, payload in (deltas or {}).items():
        card = cards_by_id.get(doc_id)
        if card is None:
            report.warnings.append(f"deep_analysis[{doc_id!r}]: 브리프에 없는 카드 id (무시됨)")
            continue
        if not isinstance(payload, dict):
            report.errors.append(f"deep_analysis[{doc_id!r}]: 델타는 객체여야 함")
            continue
        # [순서 분리 2026-07-20] 관찰 국문 번역은 **심층분석과 독립**이다 — deterministic_detail
        # 에 붙는 값이라 deep-ready 여부와 무관하다. 종전엔 deep-ready 게이트 뒤에 있어, 결정론
        # 관찰만 되살린 카드(fan-out 없이 `source_text`+`observations_ko` 만 실은 항목)의 번역이
        # 통째로 버려졌고 그대로 두면 `render.validate_483_observations` 가 발행을 막았다.
        _merge_observation_translations(card, payload.get("observations_ko"), report, doc_id)
        _merge_ncr_translations(card, payload.get("ncr_ko"), report, doc_id)
        _merge_wl_violation_translations(card, payload.get("violations_ko"), report, doc_id)
        if "deep_analysis" not in payload:
            continue          # 결정론 재추출·번역 전용 항목(정상) — 심층분석 없음
        if "deep_analysis" not in card:
            report.warnings.append(
                f"deep_analysis[{doc_id!r}]: 이 카드는 대상이 아님"
                "(deep_analysis_ready=False, 무시됨)")
            continue
        da = payload.get("deep_analysis")
        source_text = payload.get("source_text", "")
        if not isinstance(da, dict):
            report.errors.append(f"deep_analysis[{doc_id!r}]: 'deep_analysis' 키가 dict 아님")
            continue
        # [D5c 배선 2026-08-24] WL 카드의 결정론 위반 표제문을 게이트에 넘긴다 — deep 의
        # original 이 표제문 안에 머무는 발췌(=본문 근거 0)를 FAIL 로 차단(병기쌍 파손 사고
        # 재발 방지). wl_violations 블록이 없는 카드는 None → 게이트 동작 기존과 동일.
        dd = card.get("deterministic_detail")
        wl_statements = None
        if isinstance(dd, dict) and dd.get("type") == "wl_violations":
            wl_statements = [str(r.get("statement") or "")
                             for r in (dd.get("violations") or []) if isinstance(r, dict)]
        # [D5b 결정론 수리 2026-08-25] 잘린 483 원문 병기는 **버리지 말고 이어붙인다.**
        # D5b(FAIL) 판정 자체는 옳지만 그 결과가 entry 통째 보류라, 절단 하나 때문에 카드가
        # 심층분석 4섹션을 통째로 잃는다(실측: 07-12 발행분 3장이 이 경로로 죽었고 델타에는
        # 멀쩡한 분석이 그대로 있었다). 잘려나간 부분은 이미 `source_text` 안에 verbatim 으로
        # 있으므로 LLM 재호출 없이 원문 slice 를 붙이면 된다(생성 0 → 환각 0). 경계를 확정
        # 못 하는 항목은 수리하지 않고 남겨 게이트가 그대로 FAIL 시킨다 — 조용한 통과 없음.
        obs_rows = None
        if isinstance(dd, dict) and dd.get("type") == "fda_483_observations":
            obs_rows = [r for r in (dd.get("observations") or []) if isinstance(r, dict)]
        da_repaired, n_repaired, _unrepaired = vda.repair_fda483_original_truncation(
            da, source_text, obs_rows)
        if n_repaired:
            da = da_repaired
            report.warnings.append(
                f"deep_analysis[{doc_id!r}]: 원문 병기 절단 {n_repaired}건을 원문에서 "
                "결정론 복원(생성 0 — source_text verbatim)")
        gate = vda.run_deep_analysis_gate(da, source_text, wl_statements=wl_statements)
        if not gate.ok:
            report.errors.append(
                f"deep_analysis[{doc_id!r}]: 게이트 FAIL {gate.fail_count}건(병합 보류) — "
                f"{gate.report}")
            continue
        card["deep_analysis"] = da
        if gate.warn_count:
            report.warnings.append(
                f"deep_analysis[{doc_id!r}]: 게이트 WARN {gate.warn_count}건(병합은 진행)")
        _merge_observation_translations(card, payload.get("observations_ko"), report, doc_id)
    return report


def _merge_observation_translations(card: dict[str, Any], obs_ko: Any,
                                    report: InjectionReport, doc_id: str) -> None:
    """[원문·국문 병기 2026-07-09] fan-out 이 낸 관찰 statement 국문 번역을 이 카드의
    deterministic_detail.observations 에 **번호(number)로 매칭** 병합(deficiency_ko/detail_ko).

    결정론 English 관찰(수집기 산출)은 그대로 두고 국문만 additive 로 얹는다 → 웹 렌더가
    원문(영문)+국문 병기. 번호가 안 맞거나 관찰 블록이 없으면 조용히 건너뛴다(비차단). 값은
    문자열 강제·평문(렌더러가 이스케이프). deep_analysis 와 독립이라 실패해도 카드는 발행된다."""
    if not isinstance(obs_ko, list) or not obs_ko:
        return
    dd = card.get("deterministic_detail")
    if not (isinstance(dd, dict) and dd.get("type") == "fda_483_observations"):
        return
    by_num = {str(o.get("number")): o for o in dd.get("observations", [])
              if isinstance(o, dict)}
    merged = 0
    for t in obs_ko:
        if not isinstance(t, dict):
            continue
        obs = by_num.get(str(t.get("number")))
        if obs is None:
            continue
        if t.get("deficiency_ko"):
            obs["deficiency_ko"] = str(t["deficiency_ko"])
            merged += 1
        if t.get("detail_ko"):
            obs["detail_ko"] = str(t["detail_ko"])
    if merged:
        report.warnings.append(
            f"observations_ko[{doc_id!r}]: 관찰 국문 번역 {merged}건 병합(원문+국문 병기)")


def _merge_wl_violation_translations(card: dict[str, Any], viol_ko: Any,
                                     report: InjectionReport, doc_id: str) -> None:
    """[WL 위반항목 국문 병기 2026-08-10] Warning Letter 조항별 위반 표제의 국문 번역을
    deterministic_detail.violations 에 **번호(number)로 매칭** 병합(statement_ko).

    왜 이제야 생겼나 — `statement_ko` 는 2026-07-20 블록 신설 때부터 스캐폴드
    (`card_scaffold._detail_wl_violations`: "있을 때만 보존")와 렌더 템플릿
    (`web/partials/card.html`: 있으면 원문→국문 병기)에 **양쪽 다 준비돼 있었는데, 그 값을
    채워 넣는 층이 어디에도 없었다.** 그래서 WL 위반항목 상세는 도입 이래 줄곧 영문 단독으로
    나갔다(07-20·07-27·08-10 발행본 전부 키 `{number, statement, citation}` 뿐 — 2026-08-10
    사용자가 프리뷰에서 지적). 483 은 `render.validate_483_observations` 가 fail-closed 라
    누락이 곧 발행 실패로 시끄럽게 드러나지만, WL 은 템플릿이 영문으로 **조용히 degrade** 해서
    아무도 못 봤다 — 같은 결손인데 한쪽만 소리가 났다([[grm-absence-vocabulary-root-cause]] 계열).

    `_merge_observation_translations` 와 **동형**이다: 결정론 English 표제(수집기 산출)는 그대로
    두고 국문만 additive 로 얹는다. 번호가 안 맞거나 위반 블록이 없으면 조용히 건너뛴다(비차단).
    조립 순서상 `_refresh_wl_violations`(원문 재추출로 블록을 통째 재생성) **뒤**에 이 병합이
    돌아야 한다 — 앞이면 재생성이 국문을 지운다. 현재 assemble 호출 순서가 그 조건을 만족한다.
    """
    if not isinstance(viol_ko, list) or not viol_ko:
        return
    dd = card.get("deterministic_detail")
    if not (isinstance(dd, dict) and dd.get("type") == "wl_violations"):
        return
    by_num = {str(v.get("number")): v for v in dd.get("violations", [])
              if isinstance(v, dict)}
    merged = 0
    for t in viol_ko:
        if not isinstance(t, dict):
            continue
        viol = by_num.get(str(t.get("number")))
        if viol is None:
            continue
        if t.get("statement_ko"):
            viol["statement_ko"] = str(t["statement_ko"])
            merged += 1
    if merged:
        report.warnings.append(
            f"violations_ko[{doc_id!r}]: WL 위반항목 국문 번역 {merged}건 병합(원문+국문 병기)")


# EU/MHRA GMP NCR 상세 블록에서 국문 병기 대상이 되는 필드(원문 키 → 국문 키).
_NCR_KO_FIELDS = ("nature", "action", "operations", "additional")
_NCR_DETAIL_TYPES = ("eu_gmp_ncr_statement", "mhra_gmp_ncr_statement")


def _merge_ncr_translations(card: dict[str, Any], ncr_ko: Any,
                            report: InjectionReport, doc_id: str) -> None:
    """[NCR 국문 병기 2026-07-27] EU/MHRA GMP 비준수(NCR) 상세 전문의 국문 번역 병합.

    왜 필요한가 — 이 블록은 도입 이래 EudraGMDP/MHRA **원문 영문만** 렌더했다. 483 에는
    `deficiency_ko` 병기가 있는데 NCR 만 빠져 있어서, 한국어 이용자가 정작 비준수의
    **내용**(Nature)과 **당국 조치**(Action)를 읽을 수 없었다(2026-07-27 사용자 지적).

    ⚠️ 종전 이 주석은 "483·WL 에는 병기가 있는데"라고 적었으나 **WL 은 사실이 아니었다** —
    `statement_ko` 는 슬롯만 있고 채우는 층이 없었다(2026-08-10 확인). 준비된 슬롯을 보고
    "배선돼 있다"고 읽은 착각이라 기록해 둔다. 지금은 `_merge_wl_violation_translations` 가 있다.

    `ncr_ko` = {"nature_ko": str, "action_ko": str, "operations_ko": str, "additional_ko": str}
    (전부 선택 — 있는 키만 병합). 결정론 영문 원문은 그대로 두고 국문만 additive 로 얹는다.
    원문 필드가 없는데 번역만 온 경우는 붙이지 않는다(짝 없는 국문 = 근거 없는 문장).
    관찰 번역과 마찬가지로 비차단 — 실패해도 카드는 영문 단독으로 발행된다.
    """
    if not isinstance(ncr_ko, dict) or not ncr_ko:
        return
    dd = card.get("deterministic_detail")
    if not isinstance(dd, dict):
        return
    if dd.get("type") == "whopir_report":            # 같은 채널, 다른 상세 타입
        _merge_whopir_translations(dd, ncr_ko, report, doc_id)
        return
    if dd.get("type") not in _NCR_DETAIL_TYPES:
        return
    merged = 0
    for field in _NCR_KO_FIELDS:
        value = ncr_ko.get(f"{field}_ko")
        if not (isinstance(value, str) and value.strip()):
            continue
        if not str(dd.get(field) or "").strip():
            report.warnings.append(
                f"ncr_ko[{doc_id!r}]: 원문 {field!r} 이 비어 있어 국문만 붙이지 않음")
            continue
        dd[f"{field}_ko"] = value.strip()
        merged += 1
    if merged:
        report.warnings.append(
            f"ncr_ko[{doc_id!r}]: NCR 상세 국문 번역 {merged}필드 병합(원문+국문 병기)")


def _merge_whopir_translations(dd: dict[str, Any], ko: dict[str, Any],
                               report: InjectionReport, doc_id: str) -> None:
    """[WHOPIR 국문 병기 2026-07-27] WHO 공개 실사보고서 결론·항목요약의 국문 병합.

    필드명 계약은 `card_scaffold.whopir_translation_input` 이 단독으로 정한다(방출·병합이
    같은 함수를 부른다). 짝 맞춤 규칙은 NCR 형제와 동일 — **원문이 있는 자리에만** 국문을
    얹는다. 섹션은 위치가 아니라 **원문 섹션 번호**로 맞춘다(항목 증감에 안전).
    """
    from card_scaffold import whopir_translation_input

    expected = whopir_translation_input(dd)
    if not expected:
        return
    merged = 0
    outcome_ko = ko.get("outcome_ko")
    if isinstance(outcome_ko, str) and outcome_ko.strip() and expected.get("outcome"):
        dd["outcome_ko"] = outcome_ko.strip()
        merged += 1
    for sec in dd.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        no = sec.get("no")
        if not isinstance(no, int):
            continue
        for src, dst in ((f"s{no}", "text_ko"), (f"s{no}_title", "title_ko")):
            value = ko.get(f"{src}_ko")
            if not (isinstance(value, str) and value.strip() and expected.get(src)):
                continue
            sec[dst] = value.strip()
            merged += 1
    if merged:
        report.warnings.append(
            f"ncr_ko[{doc_id!r}]: WHOPIR 상세 국문 번역 {merged}필드 병합(원문+국문 병기)")


# ─────────────────────────────────────────────────────────────────────────────
# CLI — python inject_slots.py --brief <scaffold.json> --delta <delta.json> --out <out.json>
# ─────────────────────────────────────────────────────────────────────────────
def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="v16 LLM 슬롯 델타를 scaffold 브리프에 주입(grm-web-card/v1, 순수·결정론).")
    ap.add_argument("--brief", required=True, help="scaffold 브리프 JSON(빈 슬롯, assemble_web_brief 산출)")
    ap.add_argument("--delta", required=True, help="v16 LLM 델타 JSON({cards:{id:{슬롯}}, tldr:[]})")
    ap.add_argument("--out", required=True, help="완성 브리프 출력 경로(web/data/briefs/brief_web_{date}.json)")
    ap.add_argument("--deep-analysis-deltas", default=None,
                   help="[WL 심층분석 fan-out, 선택] {document_id:{deep_analysis,source_text}} "
                        "JSON 경로. 미지정 시 기존 동작과 완전 동일(additive).")
    args = ap.parse_args(argv)

    brief = _load_json(args.brief)
    delta = _load_json(args.delta)

    report = validate_injection(brief, delta)
    for w in report.warnings:
        print(f"WARN {w}", file=sys.stderr)
    if not report.ok:
        for e in report.errors:
            print(f"ERROR {e}", file=sys.stderr)
        print(f"주입 거부 — 코드 가드 위반 {len(report.errors)}건", file=sys.stderr)
        return 2

    out = inject_llm_slots(brief, delta, strict=True)

    if args.deep_analysis_deltas:
        deltas = _load_json(args.deep_analysis_deltas)
        da_report = inject_deep_analysis(out, deltas)
        for w in da_report.warnings:
            print(f"WARN {w}", file=sys.stderr)
        for e in da_report.errors:
            print(f"WARN(심층분석 병합 보류) {e}", file=sys.stderr)  # 비차단 — 6슬롯 발행은 계속

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"주입 완료: {len(out.get('cards') or [])}개 카드 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
