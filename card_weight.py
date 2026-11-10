"""카드 무게(크기) 판정 — 깊이 읽는 카드 / 표준 카드 / 표 한 줄 (2026-11-10 대표 결정 A·B).

카드마다 `card_weight` 값(`deep`·`standard`·`row`)을 **코드가** 정해 넣는다. 글 쓰는 쪽(Routine)이
"이건 중요하다"고 고르지 않는다 — 기준은 유형·등급·지적 건수·원문 길이처럼 코드가 셀 수 있는
것뿐이다(`GRM_브리프구조_재정비안_2026-11-10.md` §2).

- 깊이(deep): 경고서한 · EU/UK GMP 비준수 · Class I·Type I 회수 · 지적 7건 이상 483
- 표 한 줄(row): 식약처 회수 중 사유 원문 20자 이하 · FDA Class II 회수 중 사유 원문 84자 미만 ·
  MHRA 회수 · 지적 0건 실사 · 내용을 확보하지 못한 고시·지침
- 나머지: 표준(standard)

문턱 숫자는 11-09호 한 호에 맞춘 값이다(그 호 51장 = 깊이 9·표준 23·표 19 — 테스트가 고정).
두세 호 더 돌려 보고 바꿀 때는 아래 상수만 고친다.

순수·결정론. 입력 카드를 바꾸지 않는다(`assign_card_weights` 만 `card_weight` 키를 쓴다).
지난 호에는 붙이지 않는다(`card_weight_applies` — 소급 없음).
"""
from __future__ import annotations

import re
from typing import Any

WEIGHT_DEEP = "deep"
WEIGHT_STANDARD = "standard"
WEIGHT_ROW = "row"
WEIGHTS = (WEIGHT_DEEP, WEIGHT_STANDARD, WEIGHT_ROW)

# ── 문턱(11-09호 실측에 맞춘 값) ──────────────────────────────────────────────
DEEP_483_MIN_OBSERVATIONS = 7          # 지적(Observation) 이 건수 이상인 483 = 깊이
ROW_MFDS_RECALL_REASON_MAX = 20        # 식약처 회수 사유 원문이 이 글자 수 이하 = 표 한 줄
ROW_FDA_CLASS2_REASON_LT = 84          # FDA Class II 회수 사유 원문이 이 글자 수 미만 = 표 한 줄

# 새 짜임은 이 발행일부터(11-16호). 그 앞 호는 다시 조립해도 값이 붙지 않는다.
CARD_WEIGHT_FROM = "2026-11-16"

_DEEP_CARD_TYPES = frozenset({"Warning Letter", "EU GMP 비준수", "UK GMP 비준수"})
_RECALL_CARD_TYPES = frozenset({"회수·판매중지", "Recall", "Recall(UK)", "Recall(HC)"})
_NOTICE_CARD_TYPES = frozenset({"고시·개정법령", "지침·안내서"})
_NO_FINDING_RE = re.compile(r"지적\(보완\)사항(?:\(Deficiencies\))?\s*없음")


def card_weight_applies(brief_meta: dict[str, Any] | None) -> bool:
    """이 호에 카드 무게를 붙이는가. 발행일 < CARD_WEIGHT_FROM 이면 False.

    날짜를 못 구하면 True(새 호로 본다 — 조립기가 막 만든 호는 날짜가 있다)."""
    meta = brief_meta or {}
    d = str(meta.get("publish_date") or meta.get("run_date_kst") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return True
    return d >= CARD_WEIGHT_FROM


def _fact(card: dict[str, Any], label: str) -> str:
    for f in card.get("facts") or []:
        if (f.get("label") or "").strip() == label:
            return str(f.get("value") or "").strip()
    return ""


def recall_reason(card: dict[str, Any]) -> str:
    """회수 사유 원문 — 수집기가 원문에서 옮긴 첫 인용(`quotes[0].original`). 없으면 ""."""
    qs = card.get("quotes") or []
    if qs and isinstance(qs[0], dict):
        return " ".join(str(qs[0].get("original") or "").split())
    return ""


def recall_class(card: dict[str, Any]) -> str:
    """회수 등급 사실값(FDA `Class I/II/III`·HC `Type I/II/III`·MHRA `Class 1~4`). 없으면 ""."""
    return _fact(card, "Class")


def finding_count(card: dict[str, Any]) -> "int | None":
    """실사 결과 카드의 지적 건수(결정론 상세 기준). 셀 수 없으면 None."""
    dd = card.get("deterministic_detail") or {}
    t = dd.get("type")
    if t == "fda_483_observations":
        obs = dd.get("observations")
        if isinstance(obs, list) and obs:
            return len(obs)
        n = dd.get("count")
        return n if isinstance(n, int) else None
    if t in ("gmp_deficiencies", "wl_violations"):
        n = dd.get("count")
        if isinstance(n, int):
            return n
        rows = dd.get("rows") if t == "gmp_deficiencies" else dd.get("violations")
        return len(rows) if isinstance(rows, list) else None
    return None


def is_no_finding_inspection(card: dict[str, Any]) -> bool:
    """지적이 없었던 GMP 실사 결과(식약처 결과 문구 기준 — 단건·묶음 카드 둘 다)."""
    if card.get("card_type") != "GMP실사":
        return False
    texts = [str(k) for k in card.get("key_facts") or []]
    texts += [str((q or {}).get("original") or "") for q in card.get("quotes") or []
              if isinstance(q, dict)]
    return any(_NO_FINDING_RE.search(t) for t in texts)


def classify_card_weight(card: dict[str, Any]) -> str:
    """카드 한 장의 무게 — `deep`·`standard`·`row`."""
    ctype = card.get("card_type") or ""
    agency = card.get("agency") or ""

    # 깊이 읽는 카드
    if ctype in _DEEP_CARD_TYPES:
        return WEIGHT_DEEP
    if ctype in _RECALL_CARD_TYPES and recall_class(card) in ("Class I", "Type I"):
        return WEIGHT_DEEP
    if card.get("type_tag") == "483" and (card.get("merged_count") or 1) <= 1:
        n = finding_count(card)
        if n is not None and n >= DEEP_483_MIN_OBSERVATIONS:
            return WEIGHT_DEEP

    # 표 한 줄
    if ctype in _RECALL_CARD_TYPES:
        reason = recall_reason(card)
        if agency == "MFDS" and reason and len(reason) <= ROW_MFDS_RECALL_REASON_MAX:
            return WEIGHT_ROW
        if (agency == "FDA" and recall_class(card) == "Class II"
                and reason and len(reason) < ROW_FDA_CLASS2_REASON_LT):
            return WEIGHT_ROW
        if agency == "MHRA":
            return WEIGHT_ROW
    if is_no_finding_inspection(card):
        return WEIGHT_ROW
    if ctype in _NOTICE_CARD_TYPES and not card.get("source_body_captured"):
        return WEIGHT_ROW

    return WEIGHT_STANDARD


def assign_card_weights(cards: list[dict[str, Any]]) -> dict[str, int]:
    """카드마다 `card_weight` 를 써 넣고 무게별 장수를 돌려준다(제자리 변경)."""
    counts = {w: 0 for w in WEIGHTS}
    for c in cards:
        w = classify_card_weight(c)
        c["card_weight"] = w
        counts[w] += 1
    return counts
