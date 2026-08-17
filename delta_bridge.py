#!/usr/bin/env python3
"""GRM 웹 발행 클라우드 자동화 — Fix A: 클라우드 델타 브릿지 (설계 §2, A-3).

클라우드 Routine 은 Notion 커넥터만 있어 git 에 못 쓴다. Routine 이 완성 슬롯 델타 JSON 을
Notion Intake DB 페이지(`OPEN GRM Web Delta {date}`)에 예치하면(A-1), 이 스크립트가 그 반대편
에서 읽어 `web/data/deltas/delta_{date}.json` 으로 커밋 가능한 파일을 만든다 — 수집기가
handoff 를 Notion 에 남기는 것과 정확히 대칭(`grm_handoff.py` 의 읽기/소비 패턴을 미러링).

`grm_notion.py`(`notion_headers`·`notion_api_request`) 를 재사용한다 — 새 HTTP 클라이언트를
만들지 않는다. 검증은 `inject_slots` 의 슬롯 계약(최소: 최상위 dict + cards dict + tldr list)을
재사용한다.

순수 판정(select_open_delta·extract_delta·write_delta)과 얇은 I/O(consume_delta·main)를
분리한다. 실행 순서(A-2 워크플로가 기대):
  1) main() 이 select → extract → write 를 수행하고 wrote/date 를 $GITHUB_OUTPUT 에 낸다.
  2) 워크플로가 wrote=true 일 때만 git add/commit/push.
  3) push 성공 후에만 `--consume` 재실행으로 Notion 페이지를 CONSUMED 처리한다
     (커밋 전 CONSUMED 금지 — handoff PL-10 순서와 동형, 소비=영구 처리).

OPEN 델타 페이지가 없으면 클린 skip(exit 0). 구조 불량 델타는 fail-loud(exit 1) — 조용히
빈 델타를 커밋하지 않는다. 같은 publish_date 파일이 이미 있고 내용이 다르면 exit 1(재발행은
사람 판단). 내용이 같으면 wrote=false(멱등 no-op).

[2026-08-17] deep 델타의 `source_text`(근거 대조 기준선)는 **Routine 이 옮겨 적은 것을 믿지
않고** 그 주 handoff 페이지의 `deep_analysis_input.body_full` 에서 직접 읽어 맞춘다
(`fetch_handoff_body_full`→`apply_handoff_source_text`). 전사 경로가 사라져 무인 실행에서
사람 대조가 필요 없어진다. handoff 를 못 읽으면 예치본으로 종전과 동일하게 진행(비차단).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

from grm_common import log
from grm_notion import (
    NOTION_BLOCK_CHILDREN_URL_TPL,
    NOTION_DB_QUERY_URL_TPL,
    NOTION_PAGE_URL_TPL,
    NotionHandoffError,
    PROP_NAME,
    PROP_STATUS,
    PROP_TYPE_CLASS,
    _rich_text,
    _select,
    notion_api_request,
)

try:
    import inject_slots
except ImportError:  # pragma: no cover — inject_slots 는 항상 동봉되지만 방어적으로.
    inject_slots = None  # type: ignore[assignment]

try:
    import verify_deep_analysis as _vda
except ImportError:  # pragma: no cover — 항상 동봉되지만 방어적으로.
    _vda = None  # type: ignore[assignment]

try:
    from grm_handoff import handoff_id_for, notion_find_handoff_page
except ImportError:  # pragma: no cover — 항상 동봉되지만 방어적으로.
    handoff_id_for = None  # type: ignore[assignment]
    notion_find_handoff_page = None  # type: ignore[assignment]


TYPE_WEB_DELTA = "web-delta"


TYPE_WEB_DEEP_DELTA = "web-deep-delta"


TITLE_PREFIX_OPEN = "OPEN GRM Web Delta "


TITLE_PREFIX_OPEN_DEEP = "OPEN GRM Web Deep Delta "


TITLE_PREFIX_CONSUMED = "CONSUMED GRM Web Delta "


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DeltaBridgeError(RuntimeError):
    """브릿지 fail-loud 전용 예외 — 구조 불량/네트워크 실패를 조용히 넘기지 않는다."""


def _plain_text(parts: list[dict[str, Any]] | None) -> str:
    if not parts:
        return ""
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _prop_title(props: dict[str, Any], name: str) -> str:
    return _plain_text(props.get(name, {}).get("title", []))


def _prop_select(props: dict[str, Any], name: str) -> str:
    return (props.get(name, {}).get("select") or {}).get("name", "") or ""


def _date_from_title(title: str) -> str:
    """`OPEN GRM Web Delta {date}` / `OPEN GRM Web Deep Delta {date}` 제목에서 날짜만 추출."""
    for prefix in (TITLE_PREFIX_OPEN_DEEP, TITLE_PREFIX_OPEN):
        if title.startswith(prefix):
            return title[len(prefix):].strip()
    return ""


def _query_open_pages(token: str, db_id: str,
                       type_class: str) -> list[tuple[str, dict[str, Any]]]:
    """Intake DB 에서 `Type or Class=<type_class> ∧ Status=New` 페이지를 (run_date, page)로.

    제목에서 날짜를 못 뽑는 페이지는 skip(WARN). 페이지네이션 상한 25(handoff 동형).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {
        "filter": {"and": [
            {"property": PROP_TYPE_CLASS, "select": {"equals": type_class}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
        ]},
        "page_size": 100,
    }
    candidates: list[tuple[str, dict[str, Any]]] = []
    start_cursor: str | None = None
    for _ in range(25):  # OPEN 페이지는 소수 — 안전 상한(handoff 패턴과 동형)
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            title = _prop_title(page.get("properties", {}), PROP_NAME)
            run_date = _date_from_title(title)
            if not run_date:
                log("WARN", f"{type_class} 페이지 제목에서 날짜 파생 실패 — skip: {title!r}")
                continue
            candidates.append((run_date, page))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        log("WARN", f"{type_class} OPEN 조회 25페이지 상한 도달 — 일부 페이지 누락 가능")
    return candidates


def select_open_delta(token: str, db_id: str,
                       publish_date: str | None = None) -> dict[str, Any] | None:
    """Intake DB 에서 `Type or Class=web-delta ∧ Status=New` OPEN 페이지 1건을 고른다.

    `publish_date` 지정 시 그 날짜의 OPEN 페이지. 미지정 시 제목에서 파생한 날짜가
    **가장 큰**(최신) 페이지 1건(K4-1 "최신 OPEN 이 입력" 동형 — '오늘'을 계산하지 않는다).
    없으면 None(클린 skip 판단은 호출부 몫).
    """
    candidates = _query_open_pages(token, db_id, TYPE_WEB_DELTA)
    if not candidates:
        return None

    if publish_date:
        matches = [pg for d, pg in candidates if d == publish_date]
        if not matches:
            return None
        # 동일 날짜 복수(비정상) — 가장 최근 편집분 우선(handoff notion_find_handoff_page 동형).
        matches.sort(key=lambda p: p.get("last_edited_time", ""), reverse=True)
        return matches[0]

    candidates.sort(key=lambda item: item[0])  # run_date(YYYY-MM-DD) 문자열 정렬 = 날짜순
    return candidates[-1][1]


def select_open_deep_delta(token: str, db_id: str,
                            publish_date: str) -> dict[str, Any] | None:
    """`Type or Class=web-deep-delta ∧ Status=New` 중 **날짜가 정확히 일치**하는 1건.

    **왜 별도 페이지를 조회하나(2026-08-03 사고).** 예치 규약(`GRM_Prompt_v16.md`,
    `GRM_Routine_델타예치_스니펫.md`)은 deep 델타를 web-delta 페이지의 두 번째 코드블록
    **또는** 별도 페이지 `OPEN GRM Web Deep Delta {date}`(`web-deep-delta`)로 예치하는 걸
    허용한다. 그런데 브릿지는 `web-delta` 만 조회해, Routine 이 문서대로 별도 페이지를 쓴 주에
    deep 이 **조용히 유실**됐다(브릿지는 exit 0 성공 + "deep 없음(정상)" 로그 — 진짜 정상과
    유실이 같은 문장으로 나와 3주간 아무도 못 봤다). 상수 `TYPE_WEB_DEEP_DELTA` 는 정의만
    돼 있고 선택 경로에 배선돼 있지 않았다.

    ⚠️ `publish_date` 는 **필수**다(최신 1건 폴백 없음). deep 은 그 주 delta 의 카드 id 공간에
    종속되므로, 지난 주 잔류 OPEN deep 페이지가 이번 주 delta 에 짝지어지면 카드 id 가 전부
    빗나가 조용히 유실된다 — '최신'이 아니라 '같은 날짜'만 짝이다.
    """
    if not publish_date:
        raise DeltaBridgeError("select_open_deep_delta: publish_date 필수(날짜 정확일치만 허용)")
    matches = [pg for d, pg in _query_open_pages(token, db_id, TYPE_WEB_DEEP_DELTA)
               if d == publish_date]
    if not matches:
        return None
    if len(matches) > 1:
        log("WARN", f"web-deep-delta OPEN 페이지가 {publish_date} 에 {len(matches)}건 — "
                    "가장 최근 편집분 사용(비정상: 중복 예치 확인 필요)")
    matches.sort(key=lambda p: p.get("last_edited_time", ""), reverse=True)
    return matches[0]


def _fetch_code_blocks(token: str, page_id: str) -> list[str]:
    """페이지 본문의 code 블록 원문 텍스트들을 순서대로 반환. 코드블록이 아니면 skip.

    ★파싱하지 않고 원문을 그대로 보존한다 — Notion 은 긴 본문을 여러 code 블록으로
    쪼개 저장할 수 있어(작성 도구 의존), 블록 단위 선-파싱은 쪼개진 델타를 전부 버리는
    침묵 실패가 된다. 파싱·결합 전략은 extract_delta 가 담당(A/B/C 폴백)."""
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    texts: list[str] = []
    start_cursor: str | None = None
    for _ in range(25):
        req_url = url
        if start_cursor:
            req_url = f"{url}?start_cursor={start_cursor}"
        data = notion_api_request("GET", req_url, token)
        for block in data.get("results", []):
            if block.get("type") != "code":
                continue
            code = block.get("code", {})
            text = "".join(
                rt.get("plain_text") or rt.get("text", {}).get("content", "") or ""
                for rt in code.get("rich_text", [])
            )
            if text.strip():
                texts.append(text)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        # [2026-08-17] 상한 도달을 **말은 하게** 한다. 델타 페이지는 블록이 몇 개뿐이라 이 상한이
        # 걸릴 일이 없었지만, handoff 페이지는 payload 전문이 1,900자씩 쪼개져 실려 실측 175~230
        # 블록(2~3 API 페이지)이다. 조용히 잘리면 뒤이은 json 파싱이 실패해 "handoff 없음"으로
        # degrade 하는데, 그 로그만 봐서는 페이지가 없는 건지 잘린 건지 구별할 수 없다.
        log("WARN", f"코드블록 조회 25페이지 상한 도달(page={page_id}) — 본문 일부 누락 가능")
    return texts


def _validate_envelope(obj: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise DeltaBridgeError(f"{what}: 최상위가 dict 아님(got {type(obj).__name__})")
    cards = obj.get("cards")
    tldr = obj.get("tldr")
    if not isinstance(cards, dict):
        raise DeltaBridgeError(f"{what}: 'cards' 는 dict 여야 함")
    if not isinstance(tldr, list):
        raise DeltaBridgeError(f"{what}: 'tldr' 는 list 여야 함")
    return obj


def _is_envelope(obj: Any) -> bool:
    """슬롯 델타 envelope 모양(cards dict + tldr list)인지 — deep 델타와 구별용."""
    return (isinstance(obj, dict)
            and isinstance(obj.get("cards"), dict)
            and isinstance(obj.get("tldr"), list))


def _validate_deep(obj: Any) -> dict[str, Any]:
    """deep 델타 = 맨몸 `{document_id: {"deep_analysis": {...}, ...}}` dict.

    소비자 계약 = `assemble_publish_brief(deep_deltas=...)` → `inject_slots.
    inject_deep_analysis` — cards/tldr 봉투 없음(예치 스니펫 규약과 동일). 봉투로
    감싸 예치되면 card id 매칭이 전부 빗나가 deep 이 조용히 유실되므로 fail-loud."""
    if not isinstance(obj, dict) or not obj:
        raise DeltaBridgeError("deep delta: 비어있지 않은 dict 여야 함")
    if _is_envelope(obj):
        raise DeltaBridgeError(
            "deep delta: cards/tldr 봉투 금지 — `{document_id: {...}}` 맨몸 dict 만 허용"
            "(assemble --deep 계약, 예치 스니펫 규약)")
    bad = [k for k, v in obj.items() if not isinstance(v, dict)]
    if bad:
        raise DeltaBridgeError(f"deep delta: 값이 dict 가 아닌 키 존재: {bad[:3]}")
    return obj


def normalize_card_key_namespace(delta: dict[str, Any],
                                 deep: "dict[str, Any] | None" = None) -> int:
    """델타 카드 키가 `Source::document_id` 로 예치됐으면 bare `document_id` 로 되돌린다.

    **왜 자동 교정하나(조립 게이트와 다른 판단).** 2026-07-13·07-27 두 번, Routine 이 카드
    키를 handoff 의 `card_id`(=`source::document_id`) 형식으로 예치해 발행이 전건 거부됐고
    (07-27 은 103장) 그때마다 사람이 순수 rename 데이터 패치를 손으로 만들어 머지했다.
    두 번 다 결과는 **완전히 동일한 결정론적 변환**이었다.

    안전 근거 — `::` 는 정상 카드 id 에 **존재하지 않는다**: 전 발행본(06-22~07-27)+최신
    스캐폴드 371개 id 중 `::` 포함 0개. 그래서 접두사 유무만으로 회귀를 확정할 수 있고,
    변환은 무손실이다(충돌 시 아무것도 안 고치고 그대로 둔다 — 아래).

    `assemble_publish_brief` 의 ghost 게이트는 계속 **거부**한다(자동 교정 안 함) — 그쪽은
    발행 직전 최후 관문이라 조용히 고치면 회귀가 영영 안 보인다. 반면 브릿지는 외부 입력을
    받아들이는 **수집 어댑터**라 정규화가 제 역할이고, 여기서 고치면 발행이 안 막힌다.
    **조용히 고치지는 않는다** — WARN 로그로 건수·표본을 남기고, 호출측이 커밋 메시지·
    step summary 에 실어 회귀 자체는 계속 보이게 한다.

    Returns: 정규화한 카드 수(0 = 정상 예치).
    """
    cards = delta.get("cards")
    if not isinstance(cards, dict) or not cards:
        return 0
    prefixed = [k for k in cards if isinstance(k, str) and "::" in k]
    if not prefixed:
        return 0

    renamed = {k: k.split("::", 1)[1] for k in prefixed}
    # 접두사를 뗀 뒤 서로 충돌하거나 기존 bare 키와 부딪히면 **아무것도 손대지 않는다**
    # (무손실이 보장되지 않는 변환은 하지 않는다 — 조립 게이트가 거부하게 둔다).
    survivors = [k for k in cards if k not in renamed]
    new_ids = list(renamed.values())
    if len(set(new_ids)) != len(new_ids) or set(new_ids) & set(survivors):
        log("WARN", "카드 키 네임스페이스 회귀 감지 — 그러나 접두사 제거 시 id 충돌이 "
                    "발생해 자동 정규화를 포기한다(조립 게이트가 거부할 것).")
        return 0

    delta["cards"] = {renamed.get(k, k): v for k, v in cards.items()}
    # deep 델타도 같은 키 공간이라 함께 맞춘다(안 맞추면 심층분석이 조용히 유실된다).
    normalize_deep_key_namespace(deep)

    sample = ", ".join(f"{k} → {renamed[k]}" for k in prefixed[:3])
    log("WARN", f"카드 키 네임스페이스 회귀 자동 정규화 {len(prefixed)}건 "
                f"(Source::document_id → document_id): {sample}"
                + (" …" if len(prefixed) > 3 else ""))
    log("WARN", "원인 = Routine 이 handoff 의 `card_id`(source::document_id)를 델타 키로 "
                "썼다. 정본은 `web_card_id`(=bare document_id) — 프롬프트 §B [출력] 참조.")
    return len(prefixed)


def normalize_deep_key_namespace(deep: "dict[str, Any] | None") -> int:
    """deep 델타 키가 `Source::document_id` 면 bare `document_id` 로 되돌린다. 반환=교정 건수.

    delta 와 **분리된 함수인 이유**: deep 이 web-delta 블록2가 아니라 별도 `web-deep-delta`
    페이지로 오면 `normalize_card_key_namespace(delta, deep)` 의 delta 경로를 타지 않는다
    (delta 는 이미 정규화됐거나 애초에 접두사가 없어 조기 return 한다). 그러면 접두사 붙은
    deep 키가 그대로 남아 조립 때 카드 id 가 전부 빗나가고 심층분석이 **조용히** 사라진다.
    """
    if not isinstance(deep, dict) or not deep:
        return 0
    renamed = {k: (k.split("::", 1)[1] if isinstance(k, str) and "::" in k else k) for k in deep}
    changed = [k for k, v in renamed.items() if k != v]
    if not changed:
        return 0
    # 무손실이 보장되지 않으면 손대지 않는다(delta 쪽 판단과 동형).
    if len(set(renamed.values())) != len(deep):
        log("WARN", "deep 키 네임스페이스 회귀 감지 — 접두사 제거 시 id 충돌이라 정규화 포기.")
        return 0
    for old in changed:
        deep[renamed[old]] = deep.pop(old)
    log("WARN", f"deep 키 네임스페이스 회귀 자동 정규화 {len(changed)}건 "
                f"(Source::document_id → document_id)")
    return len(changed)


# deep 델타 항목이 `deep_analysis` 없이 실려도 **정상**인 키들(번역/재추출 입력 전용).
# 새 번역층을 만들면 여기에 반드시 추가할 것 — 빠뜨리면 그 카드가 조용히 drop 된다.
# 대응 병합층: observations_ko/violations_ko → inject_slots._merge_*_translations,
# ncr_ko → _merge_ncr_translations, source_text → assemble._refresh_* 결정론 재추출.
_TRANSLATION_ONLY_KEYS = ("observations_ko", "ncr_ko", "violations_ko", "source_text")


def _gate_deep_analysis(deep: dict[str, Any]) -> "dict[str, Any] | None":
    """deep 델타 각 카드를 `verify_deep_analysis` 게이트에 통과시켜 **PASS 만** 남긴다.

    [클라우드화 2026-07-13] 심층분석 생성은 클라우드 Routine 의 LLM 이(python·서브에이전트
    없이) 하고, **근거 검증(D1 구조·D2 조항 근거대조·D3 숫자)은 python 이 도는 이 브릿지가**
    담당한다 — 근거 없는(날조·미근거) 분석의 발행을 차단한다(생성=클라우드·검증=여기). 각 카드
    entry 는 `{"deep_analysis": {...4섹션...}, "source_text": "<그 카드 원문>", ...}`;
    `source_text` 로 D2/D4 근거대조를 한다. 게이트 FAIL 카드는 조용히 drop(그 카드만 6슬롯으로
    graceful degrade). 게이트 모듈 부재 시(방어) 구조검증만 하고 원본 통과. PASS 0건이면 None.

    ★ 이 함수가 보는 `source_text` 는 호출 직전 `_authoritative_source_text` 가 그 주 handoff 의
    `deep_analysis_input.body_full` 로 맞춰 놓은 값이다(2026-08-17). 즉 **분석기가 실제로 받은
    입력**과 대조한다 — 예치된 전사본과 대조하던 종전 방식은 옮겨 적다 흘린 만큼이 그대로
    '원문에 없음'이 됐다."""
    if _vda is None:  # pragma: no cover — 항상 동봉
        log("WARN", "verify_deep_analysis 미탑재 — deep 게이트 skip(구조검증만 적용됨)")
        return deep
    kept: dict[str, Any] = {}
    for doc, entry in deep.items():
        if not isinstance(entry, dict):
            continue
        # [번역 전용 항목 통과 2026-07-27] `deep_analysis` 가 없고 번역/재추출 입력만 실린
        # 항목(`observations_ko`·`ncr_ko`·`violations_ko`·`source_text`)은 **생성된 분석이 없어**
        # 근거 게이트의 검증 대상이 아니다. 종전 코드는 이런 entry 를 통째로 `da` 로 보고 4섹션
        # 게이트에 태워 전건 FAIL·drop 시켰다 — 관찰 국문 번역이 클라우드 경로에서 조용히
        # 사라지는 구멍이었고, NCR 번역(`ncr_ko`)도 같은 자리에서 죽었을 것이다.
        #
        # ★ [2026-08-10] 이 목록은 **손으로 열거한 허용목록이라 새 번역층이 생길 때마다 낡는다.**
        #   `violations_ko`(WL 위반항목 국문)를 추가하며 그 함정을 실제로 밟았다 — 번역층을
        #   새로 만들면 여기도 같이 늘려야 하고, 안 늘리면 그 카드는 "검증 대상 키 없음"으로
        #   조용히 drop 된다(CI 허용목록 표류 계열: [[grm-ci-shim-silent-gap]]).
        if "deep_analysis" not in entry:
            if any(k in entry for k in _TRANSLATION_ONLY_KEYS):
                kept[doc] = entry
                continue
            log("WARN", f"deep 델타 항목에 검증 대상 키 없음 — 카드 drop: {doc}")
            continue
        da = entry.get("deep_analysis")
        source = entry.get("source_text", "")
        try:
            gate = _vda.run_deep_analysis_gate(da, source, card_type=None)
        except Exception as e:  # noqa: BLE001 — 게이트 자체 오류는 그 카드만 drop(브리프 비차단)
            log("WARN", f"deep 게이트 실행 오류 — 카드 drop: {doc} ({e})")
            continue
        if getattr(gate, "ok", False):
            kept[doc] = entry
        else:
            log("WARN", f"deep 게이트 FAIL — 카드 drop: {doc}\n{getattr(gate, 'report', '')}")
    if not kept:
        log("INFO", "deep 게이트 통과 카드 0건 — deep 델타 없음 처리(6슬롯만 발행)")
        return None
    log("INFO", f"deep 게이트 통과 {len(kept)}/{len(deep)} 카드 병합")
    return kept


def extract_handoff_body_full(payload: dict[str, Any]) -> dict[str, str]:
    """handoff v2 payload → `{web_card_id: deep_analysis_input.body_full}`(순수 함수).

    v1 payload·심층분석 비대상 주에는 그런 키가 아예 없으므로 자연히 빈 dict 다 —
    `schema_version` 문자열로 분기하지 않는다(**데이터에서 파생**한다. 스키마 이름이 바뀌어도
    키가 있으면 읽고, 키가 없으면 안 읽는 게 정확한 판정이다).
    """
    bodies: dict[str, str] = {}
    dupes = 0
    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        card_id = row.get("web_card_id")
        body = (row.get("deep_analysis_input") or {}).get("body_full") \
            if isinstance(row.get("deep_analysis_input"), dict) else None
        if not (isinstance(card_id, str) and card_id and isinstance(body, str) and body.strip()):
            continue
        if card_id in bodies:
            if bodies[card_id] != body:
                dupes += 1
            continue  # 먼저 만난 것이 정본(rows 는 상류에서 이미 dedupe 된다)
        bodies[card_id] = body
    if dupes:
        log("WARN", f"handoff 에 같은 web_card_id 의 서로 다른 body_full 이 {dupes}건 — "
                    "첫 행을 사용(비정상: 상류 dedupe 확인 필요)")
    return bodies


def fetch_handoff_body_full(token: str, db_id: str, publish_date: str) -> dict[str, str]:
    """그 주 Routine handoff 페이지에서 카드별 심층분석 입력 원문을 되찾아 온다.

    **왜 되찾아 오나(2026-08-17).** 종전 계약은 deep 델타의 `source_text` 에 그 카드의
    `body_full`(483 은 12~14k자)을 **클라우드 Routine 이 그대로 옮겨 적어** 예치하라고 요구했다.
    그런데 `source_text` 는 장식이 아니라 **근거 대조의 기준선**이다 — `verify_deep_analysis` 의
    D2(조항 인용)·D4(원문 verbatim)·D5b(483 절단)가 전부 이 문자열 안에서 근거를 찾고,
    조립 단계의 `_refresh_483_observations`·`_refresh_wl_violations` 는 이 문자열을 **다시
    파싱**해 발행 카드의 결정론 블록을 만든다. 즉 LLM 이 옮겨 적다 흘린 만큼이 그대로
    "원문에 없는 것"이 된다.
    2026-08-17 실측: Routine 이 예치한 6건이 **6건 전부** body_full 과 달랐다(각 51~241자,
    말미 업체주소·업체번호·사업자등록번호 블록이 통째로 누락). 무인 실행에는 대조할 사람이 없다.

    `body_full` 은 애초에 **같은 Notion DB 의 그 주 handoff 페이지 안에**
    `rows[].deep_analysis_input.body_full` 로 이미 있다(Routine 이 그걸 읽고 분석했다).
    그러니 옮겨 적게 하지 말고 브릿지가 직접 읽는다 — 전사 경로 자체가 없어진다.

    ★ 여기서 가져오는 값이 **정확히 분석기가 본 입력**이라는 점이 핵심이다. 게이트는 "LLM 이
    받은 원문"과 대조해야 맞다. 더 풍부한 텍스트로 대조하면 게이트가 과하게 관대해지고,
    더 빈약한 텍스트로 대조하면 멀쩡한 인용이 거짓 FAIL 난다.

    짝은 **날짜 정확일치**만이다(`routine-handoff::{publish_date}`) — 예치 규약이
    `publish_date` = 그 handoff 의 `run_date_kst` 로 못박고 있다. '최신 handoff' 폴백을 두면
    지난 주 원문으로 이번 주 분석을 대조하게 되고, 그건 조용한 오염이다
    (`select_open_deep_delta` 의 판단과 동형).

    반환: `{web_card_id: body_full}`. 페이지 없음·구조 불량은 빈 dict(호출부가 폴백).
    """
    if notion_find_handoff_page is None or handoff_id_for is None:  # pragma: no cover
        log("WARN", "grm_handoff 미탑재 — handoff 원문 정본화 skip(예치된 source_text 사용)")
        return {}
    handoff_id = handoff_id_for(publish_date)
    # `request=` 로 **이 모듈의** notion_api_request 를 넘긴다 — 그래야 브릿지의 Notion I/O 가
    # 전부 한 이름을 지나가고, 호출부 테스트가 그 하나만 막으면 실제 네트워크로 새지 않는다.
    page = notion_find_handoff_page(token, db_id, handoff_id, request=notion_api_request)
    if page is None:
        log("WARN", f"handoff 페이지 미발견({handoff_id}) — 예치된 source_text 를 그대로 사용")
        return {}
    texts = [t for t in _fetch_code_blocks(token, page.get("id", ""))
             if isinstance(t, str) and t.strip()]
    # payload 는 JSON 하나가 1,900자 단위 code 블록으로 쪼개져 실린다(`_handoff_blocks`) —
    # 결합해야 파싱된다. 블록 단위 선-파싱은 전량 실패한다(_fetch_code_blocks 주석 동형).
    payload = _try_json_dict("".join(texts))
    if payload is None:
        log("WARN", f"handoff payload JSON 파싱 실패({handoff_id}, 블록 {len(texts)}개) — "
                    "예치된 source_text 를 그대로 사용")
        return {}
    bodies = extract_handoff_body_full(payload)
    log("INFO", f"handoff 심층분석 입력 원문 {len(bodies)}건 확보({handoff_id})")
    return bodies


def apply_handoff_source_text(deep: dict[str, Any],
                              bodies: dict[str, str]) -> dict[str, int]:
    """deep 델타 각 카드의 `source_text` 를 handoff `body_full` 로 **정본화**한다(순수 함수).

    규약:
      · handoff 에 그 카드의 비어있지 않은 `body_full` 이 있을 때만 손댄다.
      · 없으면 예치된 값을 **그대로 둔다** — 폴백이 곧 안전망이다. 소급 복구 경로가 실제로
        존재한다(`fda483_ocr_backfill` 의 OCR 판독본은 `fda483_body_full` 이 애초에 없어서
        만든 것이라 handoff 에 짝이 없다). 그런 값을 지우면 그 카드는 원문을 잃는다.
      · 값이 다르면 **조용히 고치지 않는다** — 건수·표본을 WARN 으로 남긴다(전사 드리프트
        자체가 계속 보여야 프롬프트를 언제 손봐야 할지 알 수 있다).
      · `source_text_status`(OCR 출처 표기) 등 다른 키는 건드리지 않는다.

    ⚠️ 카드를 **새로 만들지는 않는다.** handoff 에 body_full 이 있어도 deep 델타에 그 카드
    항목이 없으면 분석 자체가 없다는 뜻이라, 원문만 실은 유령 항목을 만들면 조립이 "원문은
    있는데 분석이 없는 카드"를 새로 보게 된다.

    Returns: {"filled","replaced","identical","absent"} 건수.
    """
    stats = {"filled": 0, "replaced": 0, "identical": 0, "absent": 0}
    drifted: list[str] = []
    for doc, entry in deep.items():
        if not isinstance(entry, dict):
            continue
        body = bodies.get(doc)
        if not body:
            stats["absent"] += 1
            continue
        current = entry.get("source_text")
        if not (isinstance(current, str) and current.strip()):
            entry["source_text"] = body
            stats["filled"] += 1
        elif current == body:
            stats["identical"] += 1
        else:
            entry["source_text"] = body
            stats["replaced"] += 1
            drifted.append(f"{doc}({len(current) - len(body):+d}자)")
    if stats["replaced"]:
        log("WARN", f"예치된 source_text 가 handoff body_full 과 다릅니다 {stats['replaced']}건 "
                    f"— handoff 원문으로 교체(전사 드리프트): {', '.join(drifted[:5])}"
                    + (" …" if len(drifted) > 5 else ""))
        log("WARN", "원인 = Routine 이 body_full 을 델타에 **옮겨 적는** 계약(프롬프트 §B ③). "
                    "브릿지가 handoff 에서 직접 읽으므로 예치는 더 이상 필요하지 않다.")
    if any(stats.values()):
        log("INFO", f"handoff 원문 정본화: 채움 {stats['filled']} · 교체 {stats['replaced']} · "
                    f"동일 {stats['identical']} · handoff 미보유 {stats['absent']}")
    return stats


def _authoritative_source_text(token: str, db_id: str, date_str: str,
                               deep: dict[str, Any]) -> None:
    """deep 델타의 `source_text` 를 그 주 handoff 원문으로 맞춘다(얇은 I/O·**비차단**).

    `_merge_deep_from_separate_page` 와 같은 이유로 실패가 delta 브릿지를 죽이면 안 된다 —
    이건 선택 계층(deep)의 품질 개선이고, delta 기록은 주간 발행의 필수 경로다. 실패하면
    예치된 `source_text` 로 종전과 100% 동일하게 진행한다(WARN 만 남긴다).
    """
    try:
        bodies = fetch_handoff_body_full(token, db_id, date_str)
    except (DeltaBridgeError, NotionHandoffError) as e:
        log("WARN", f"handoff 원문 조회 실패({date_str}) — 예치된 source_text 로 계속: {e}")
        return
    if bodies:
        apply_handoff_source_text(deep, bodies)


def _try_json_dict(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def extract_delta(page: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """페이지에서 (delta, deep|None, publish_date) 추출·검증.

    본문 code 블록 결합 전략(작성 도구가 긴 본문을 여러 블록으로 쪼개도 견딘다):
      A) 블록1 = delta envelope (+블록2 = deep 맨몸 dict) — 예치 규약 기본형.
      B) 전체 블록 결합 = 단일 delta envelope (Notion 분할 저장 대응).
      C) 마지막 블록 제외 결합 = delta envelope + 마지막 블록 = deep.
    deep 델타는 맨몸 `{document_id: {...}}` — cards/tldr 봉투 금지(_validate_deep,
    assemble --deep 소비 계약). envelope 검증(최상위 dict + cards dict + tldr list)은
    fail-loud — 구조 불량이면 DeltaBridgeError.
    가능하면 `inject_slots.validate_injection` 류 슬롯 계약도 참고하되(카드 id 정합은
    scaffold 가 없어 이 시점엔 알 수 없으므로), 여기서는 envelope 형태만 강제한다
    (슬롯 세부 가드는 조립 단계 assemble_publish_brief/inject_slots 가 다시 강제).
    `publish_date` 는 본문 delta 안 값 우선, 없으면 페이지 제목에서 파생.
    형식은 `^\\d{4}-\\d{2}-\\d{2}$` 강제.
    """
    props = page.get("properties", {})
    title = _prop_title(props, PROP_NAME)
    title_date = _date_from_title(title)

    texts = page.get("_code_blocks")
    if texts is None:
        raise DeltaBridgeError(
            "extract_delta 는 page['_code_blocks'] (사전 fetch 된 코드블록 원문 리스트)를 "
            "요구합니다 — select_open_delta 호출 후 _attach_code_blocks 로 채우세요.")
    texts = [t for t in texts if isinstance(t, str) and t.strip()]
    if not texts:
        raise DeltaBridgeError(f"페이지 {title!r}: 코드 블록(델타 envelope JSON) 없음")

    parsed = [_try_json_dict(t) for t in texts]
    delta: dict[str, Any] | None = None
    deep: dict[str, Any] | None = None
    # A) 기본형(예치 규약): 블록1 = delta envelope, (있으면) 블록2 = deep 맨몸 dict.
    if parsed[0] is not None and _is_envelope(parsed[0]):
        delta = _validate_envelope(parsed[0], what="delta envelope")
        if len(texts) > 1:
            if parsed[1] is None:
                raise DeltaBridgeError("deep delta 블록 JSON 파싱 실패(블록 2)")
            deep = parsed[1]
    # B) Notion 이 긴 본문을 여러 code 블록으로 쪼갠 경우 — 전체 결합이 단일 delta.
    if delta is None:
        joined = _try_json_dict("".join(texts))
        if joined is not None and _is_envelope(joined):
            delta = _validate_envelope(joined, what="delta envelope(다중 블록 결합)")
    # C) 쪼개진 delta + 마지막 블록이 deep 인 경우.
    if delta is None and len(texts) >= 2:
        head = _try_json_dict("".join(texts[:-1]))
        if head is not None and _is_envelope(head) and parsed[-1] is not None:
            delta = _validate_envelope(head, what="delta envelope(다중 블록 결합)")
            deep = parsed[-1]
    if delta is None:
        diag = " · ".join(
            f"블록{i + 1}={'dict' if p is not None else '파싱실패/비dict'}"
            for i, p in enumerate(parsed))
        raise DeltaBridgeError(
            f"페이지 {title!r}: 델타 envelope(cards+tldr)를 어떤 결합으로도 못 찾음 — {diag}")
    if deep is not None:
        deep = _validate_deep(deep)  # 구조 파싱만(게이트는 write 직전 main 에서 — 파싱/검증 분리)

    publish_date = delta.get("publish_date") or title_date
    if not isinstance(publish_date, str) or not _DATE_RE.match(publish_date):
        raise DeltaBridgeError(
            f"publish_date 형식 오류: {publish_date!r} — ^\\d{{4}}-\\d{{2}}-\\d{{2}}$ 필요")

    normalize_card_key_namespace(delta, deep)
    # ★[2026-08-17] deep 정규화를 **무조건** 한 번 더 부른다(멱등 — 정상 키면 0 반환).
    # 위 호출은 delta 카드 키에 접두사가 없으면 조기 return 하므로, "delta 는 깨끗한데 deep 만
    # 접두사"인 조합에서 deep 이 전혀 정규화되지 않았다. 그러면 카드 id 가 전부 빗나가 심층분석이
    # 조용히 사라지고, 아래 handoff 원문 정본화도 `web_card_id`(bare) 로 조회하므로 전건 미스가
    # 된다 — 잃는 것이 겹치는 자리라 delta 쪽 상태에 의존시키지 않는다.
    normalize_deep_key_namespace(deep)

    if inject_slots is not None:
        try:
            report = inject_slots.validate_injection({"cards": []}, delta)
            # 카드 id 정합은 scaffold 부재로 판단 불가(전부 '유령' 경고) — errors 만 본다.
            # errors 는 cards 타입·평문·길이 위반 등 envelope 자체 문제만 표면화한다.
            hard_errors = [e for e in report.errors if "브리프에 없는" not in e]
            if hard_errors:
                raise DeltaBridgeError(
                    "델타 슬롯 계약 위반(inject_slots.validate_injection):\n  - "
                    + "\n  - ".join(hard_errors))
        except DeltaBridgeError:
            raise
        except Exception as e:  # noqa: BLE001 — 슬롯 검증 자체 실패는 fail-loud 대상 아님(비차단 참고)
            log("WARN", f"inject_slots 슬롯 계약 참고 검증 실패(무시): {e}")

    return delta, deep, publish_date


def extract_deep_page(page: dict[str, Any]) -> dict[str, Any]:
    """별도 `web-deep-delta` 페이지 → deep 델타 맨몸 dict. fail-loud.

    web-delta 페이지와 달리 envelope(cards+tldr)가 **없다** — 본문 전체가 deep dict 하나다.
    `_fetch_code_blocks` 주석과 같은 이유로 Notion 이 긴 본문을 여러 code 블록으로 쪼갤 수
    있으므로, 블록1 단독 → 전체 결합 순으로 시도한다(쪼개진 deep 을 통째로 버리지 않기 위해).

    ★[2026-08-17] 키 정규화(`normalize_deep_key_namespace`)를 **여기서** 건다.
    `normalize_deep_key_namespace` 의 docstring 은 "별도 페이지로 온 deep 은 delta 경로를 타지
    않는다"고 그 위험을 정확히 적어두고도, 정작 이 경로에서 그 함수를 부르는 곳이 없었다
    (정의만 있고 배선이 없는 슬롯 — 이 저장소의 상습 결함 계열).
    """
    props = page.get("properties", {})
    title = _prop_title(props, PROP_NAME)
    texts = page.get("_code_blocks")
    if texts is None:
        raise DeltaBridgeError(
            "extract_deep_page 는 page['_code_blocks'] 를 요구합니다 — _attach_code_blocks 선행.")
    texts = [t for t in texts if isinstance(t, str) and t.strip()]
    if not texts:
        raise DeltaBridgeError(f"deep 페이지 {title!r}: 코드 블록(deep 델타 JSON) 없음")

    first = _try_json_dict(texts[0])
    if first is not None and not _is_envelope(first) and len(texts) == 1:
        return _normalized_deep(first)
    joined = _try_json_dict("".join(texts))
    if joined is not None:
        return _normalized_deep(joined)
    if first is not None:
        return _normalized_deep(first)
    raise DeltaBridgeError(
        f"deep 페이지 {title!r}: deep 델타 JSON 파싱 실패(블록 {len(texts)}개, 단독·결합 모두)")


def _normalized_deep(obj: Any) -> dict[str, Any]:
    """구조 검증 + 키 네임스페이스 정규화를 한 묶음으로(별도 deep 페이지 전용 진입점)."""
    deep = _validate_deep(obj)
    normalize_deep_key_namespace(deep)
    return deep


def _attach_code_blocks(token: str, page: dict[str, Any]) -> dict[str, Any]:
    """select_open_delta 결과 page 에 본문 code 블록을 fetch 해 붙인다(extract_delta 입력)."""
    page = dict(page)
    page["_code_blocks"] = _fetch_code_blocks(token, page.get("id", ""))
    return page


def _under(date_str: str) -> str:
    return date_str.replace("-", "_")


def _delta_path(date_str: str) -> str:
    return os.path.join("web", "data", "deltas", f"delta_{_under(date_str)}.json")


def _deep_path(date_str: str) -> str:
    return os.path.join("web", "data", "deltas", f"deep_{_under(date_str)}.json")


def _dumps(payload: dict[str, Any]) -> str:
    """기존 델타 fixture 포맷과 일치(indent=1·ensure_ascii=False·후행개행) —
    `emit_web_brief_file`/`assemble_publish_brief._write_json` 과 동형 data 관례."""
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def write_delta(delta: dict[str, Any], deep: dict[str, Any] | None,
                 date_str: str) -> bool:
    """`web/data/deltas/delta_{date}.json`(+deep) 결정론 기록. 반환=wrote(새로 썼는가).

    파일이 이미 있고 내용이 다르면 DeltaBridgeError(중복 publish_date 가드 — 재발행은
    사람 판단). 내용이 같으면(멱등) False 를 반환하고 아무것도 쓰지 않는다.
    """
    path = _delta_path(date_str)
    new_text = _dumps(delta)
    wrote = False
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing_text = f.read()
        if existing_text == new_text:
            log("INFO", f"델타 {path} 이미 동일 내용 — no-op(멱등)")
        else:
            raise DeltaBridgeError(
                f"델타 {path} 이미 존재하고 내용이 다릅니다 — 중복 publish_date 가드. "
                f"재발행이 의도라면 사람이 직접 덮어쓰세요.")
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(new_text.encode("utf-8"))
        wrote = True
        log("INFO", f"델타 기록: {path}")

    if deep is not None and _write_deep_file(deep, date_str):
        wrote = True

    return wrote


def _write_deep_file(deep: dict[str, Any], date_str: str) -> bool:
    """`web/data/deltas/deep_{date}.json` 결정론 기록. 반환=wrote. 멱등(동일 내용=no-op)."""
    deep_path = _deep_path(date_str)
    new_deep_text = _dumps(deep)
    if os.path.exists(deep_path):
        with open(deep_path, "r", encoding="utf-8") as f:
            existing_deep_text = f.read()
        if existing_deep_text != new_deep_text:
            raise DeltaBridgeError(
                f"deep 델타 {deep_path} 이미 존재하고 내용이 다릅니다 — 중복 가드.")
        log("INFO", f"deep 델타 {deep_path} 이미 동일 내용 — no-op(멱등)")
        return False
    os.makedirs(os.path.dirname(deep_path), exist_ok=True)
    with open(deep_path, "wb") as f:
        f.write(new_deep_text.encode("utf-8"))
    log("INFO", f"deep 델타 기록: {deep_path}")
    return True


def write_deep_only(deep: dict[str, Any], date_str: str) -> bool:
    """delta 는 이미 커밋됐고 deep 만 뒤늦게 확보한 주의 **소급 기록**(2026-08-03 복구 경로).

    ⚠️ 그 날짜의 `delta_{date}.json` 이 **이미 있을 때만** 쓴다 — delta 없는 deep 은 조립
    입력이 될 수 없어(카드 id 짝이 없다) 고아 파일이 되고, 다음 주 브릿지의 중복 가드만
    건드린다. '짝이 있는가'를 먼저 묻는 게 이 함수의 존재 이유다.
    """
    delta_path = _delta_path(date_str)
    if not os.path.exists(delta_path):
        raise DeltaBridgeError(
            f"deep 단독 기록 거부: {delta_path} 가 없습니다 — deep 은 그 주 delta 의 카드 id "
            f"공간에 종속됩니다(고아 deep 방지). delta 부터 브릿지하세요.")
    return _write_deep_file(deep, date_str)


def consume_delta(token: str, page: dict[str, Any]) -> None:
    """페이지 Status→Processed, Title→CONSUMED. 커밋·push 성공 **후**에만 호출할 것
    (handoff PL-10 순서 동형 — 소비=영구 처리이므로 커밋 전에 하지 않는다)."""
    props = page.get("properties", {})
    title = _prop_title(props, PROP_NAME)
    date_str = _date_from_title(title)
    is_deep = title.startswith(TITLE_PREFIX_OPEN_DEEP)
    prefix_open = TITLE_PREFIX_OPEN_DEEP if is_deep else TITLE_PREFIX_OPEN
    new_title = title.replace(prefix_open, TITLE_PREFIX_CONSUMED, 1) if title.startswith(
        prefix_open) else f"{TITLE_PREFIX_CONSUMED}{date_str}"
    page_id = page.get("id", "")
    notion_api_request(
        "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
        body={"properties": {
            PROP_NAME: {"title": _rich_text(new_title)},
            PROP_STATUS: _select("Processed"),
        }})
    log("INFO", f"web-delta 페이지 CONSUMED 처리 완료: {new_title} ({page_id})")


def _merge_deep_from_separate_page(token: str, db_id: str, date_str: str,
                                    deep: "dict[str, Any] | None") -> "dict[str, Any] | None":
    """web-delta 본문에 deep 블록이 없으면 별도 `web-deep-delta` 페이지에서 가져온다.

    두 경로 모두 예치 규약상 정당하므로 **블록2가 있으면 그쪽이 정본**이고, 이 함수는
    그때 조회조차 하지 않는다(네트워크 절약이 아니라 우선순위를 코드로 못박는 것).
    단, 둘 다 존재하면 조용히 덮지 않고 WARN 으로 표면화한다 — 내용이 다를 수 있는데
    어느 쪽을 버렸는지 로그에 안 남으면 2026-08-03 과 같은 침묵 유실이 반복된다.

    ⚠️ **이 경로의 실패는 delta 브릿지를 죽이지 않는다(WARN 후 deep=None).** deep 은 없어도
    그 주가 6슬롯으로 발행되는 **선택 계층**이고, delta 기록은 주간 발행의 필수 경로다.
    선택 기능의 파싱·네트워크 실패가 필수 경로를 인질로 잡으면, 고치려던 것(deep 유실)보다
    더 큰 것(그 주 발행 전체)을 잃는다. 대신 침묵하지 않는다 — WARN 으로 사유를 남기고,
    그 페이지는 CONSUMED 하지 않아 OPEN 인 채로 남는다(사람이 볼 수 있는 물증).
    """
    try:
        deep_page = select_open_deep_delta(token, db_id, date_str)
        if deep is not None:
            if deep_page is not None:
                log("WARN", f"deep 델타가 web-delta 블록2와 별도 페이지에 **둘 다** 있습니다"
                            f"({date_str}) — 블록2를 정본으로 사용하고 별도 페이지는 OPEN 으로 "
                            f"남깁니다(사람이 중복 예치를 확인할 것).")
            return deep
        if deep_page is None:
            return None
        log("INFO", f"deep 델타를 별도 web-deep-delta 페이지에서 확보: {date_str}")
        deep_page = _attach_code_blocks(token, deep_page)
        return extract_deep_page(deep_page)
    except (DeltaBridgeError, NotionHandoffError) as e:
        log("WARN", f"별도 web-deep-delta 페이지 처리 실패({date_str}) — 이번 주는 deep 없이 "
                    f"6슬롯만 발행합니다(delta 는 정상 기록). 페이지는 OPEN 유지: {e}")
        return None


def _consume_deep_page_if_persisted(token: str, db_id: str,
                                     delta_page: dict[str, Any]) -> None:
    """별도 deep 페이지는 **그 deep 이 실제로 파일로 남았을 때만** CONSUMED 처리한다.

    파일 존재를 조건으로 두는 이유 — 소비는 영구 처리라, 쓰지도 못한 deep 페이지를 닫으면
    다음 실행에서 다시 주울 기회까지 사라진다(침묵 유실의 재생산). 조건 불충족이면 OPEN 으로
    남겨 사람이 볼 수 있게 둔다.
    """
    date_str = _date_from_title(_prop_title(delta_page.get("properties", {}), PROP_NAME))
    if not date_str:
        return
    # delta 페이지 소비는 이미 끝났다 — 여기서 예외가 나가면 워크플로가 실패로 뒤집혀
    # "커밋·push·delta consume 은 됐는데 스텝은 red" 라는 오해를 만든다. 비차단.
    try:
        deep_page = select_open_deep_delta(token, db_id, date_str)
        if deep_page is None:
            return
        if not os.path.exists(_deep_path(date_str)):
            log("WARN", f"web-deep-delta 페이지({date_str})가 OPEN 이지만 "
                        f"{_deep_path(date_str)} 가 없어 CONSUMED 처리하지 않습니다 — "
                        f"deep 이 기록되지 않았습니다(원인 확인 필요).")
            return
        consume_delta(token, deep_page)
    except (DeltaBridgeError, NotionHandoffError) as e:
        log("WARN", f"web-deep-delta 페이지 CONSUMED 처리 실패({date_str}) — OPEN 유지: {e}")


def _bridge_deep_only(token: str, db_id: str, publish_date: str,
                       consume: bool) -> "int | None":
    """OPEN web-delta 가 없을 때의 deep 단독 처리. 처리했으면 종료코드, 아니면 None."""
    deep_page = select_open_deep_delta(token, db_id, publish_date)
    if deep_page is None:
        return None
    if consume:
        if not os.path.exists(_deep_path(publish_date)):
            log("WARN", f"deep 단독 consume 거부: {_deep_path(publish_date)} 없음")
            return None
        consume_delta(token, deep_page)
        return 0
    log("INFO", f"OPEN web-delta 없음 · web-deep-delta 단독 확보 — 소급 기록: {publish_date}")
    deep_page = _attach_code_blocks(token, deep_page)
    deep = extract_deep_page(deep_page)
    _authoritative_source_text(token, db_id, publish_date, deep)  # 게이트 **전에** 정본화
    deep = _gate_deep_analysis(deep)
    wrote = bool(deep) and write_deep_only(deep, publish_date)
    _github_output("wrote", "true" if wrote else "false")
    _github_output("date", publish_date)
    print(f"브릿지 완료(deep 단독): date={publish_date} wrote={wrote}")
    return 0


def _github_output(key: str, value: str) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Notion web-delta 페이지 → web/data/deltas/delta_{date}.json 브릿지.")
    ap.add_argument("--db", required=True, help="Notion Intake DB id")
    ap.add_argument("--publish-date", default=None,
                     help="특정 발행일(YYYY-MM-DD) 강제 선택. 미지정 시 최신 OPEN web-delta.")
    ap.add_argument("--consume", action="store_true",
                     help="write 없이, 지정된(또는 최신) OPEN 페이지를 CONSUMED 처리만 한다. "
                          "커밋·push 성공 뒤 워크플로의 별도 단계에서 호출할 것.")
    args = ap.parse_args(argv)

    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        print("ERROR: NOTION_TOKEN 환경변수 없음", file=sys.stderr)
        return 1

    try:
        page = select_open_delta(token, args.db, publish_date=args.publish_date)
    except NotionHandoffError as e:
        print(f"ERROR: Notion 조회 실패: {e}", file=sys.stderr)
        return 1

    if page is None:
        # [deep 소급 복구 2026-08-03] delta 는 이미 소비/커밋됐는데 deep 만 남은 주 —
        # `--publish-date` 로 명시했을 때만 deep 단독 경로를 연다(스케줄 크론에서는 절대
        # 발화하지 않는다). 이 경로가 없으면 08-03 같은 주는 브릿지로 **복구가 불가능**하다.
        if args.publish_date:
            try:
                rc = _bridge_deep_only(token, args.db, args.publish_date, consume=args.consume)
            except DeltaBridgeError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            except NotionHandoffError as e:
                print(f"ERROR: Notion 조회 실패: {e}", file=sys.stderr)
                return 1
            if rc is not None:
                return rc
        log("INFO", "no OPEN web-delta — nothing to bridge")
        _github_output("wrote", "false")
        return 0

    if args.consume:
        try:
            consume_delta(token, page)
            _consume_deep_page_if_persisted(token, args.db, page)
        except NotionHandoffError as e:
            print(f"ERROR: consume 실패: {e}", file=sys.stderr)
            return 1
        return 0

    try:
        page = _attach_code_blocks(token, page)
        delta, deep, date_str = extract_delta(page)
        deep = _merge_deep_from_separate_page(token, args.db, date_str, deep)
        if deep is not None:
            # [2026-08-17] 근거 대조의 기준선(`source_text`)을 그 주 handoff 원문으로 맞춘 **뒤**
            # 게이트를 돌린다 — 순서 불가침. 게이트가 보는 원문이 곧 판정이다.
            _authoritative_source_text(token, args.db, date_str, deep)
            deep = _gate_deep_analysis(deep)  # 클라우드 생성 deep 근거 검증(write 직전)
        wrote = write_delta(delta, deep, date_str)
    except DeltaBridgeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except NotionHandoffError as e:
        print(f"ERROR: Notion 조회 실패: {e}", file=sys.stderr)
        return 1

    _github_output("wrote", "true" if wrote else "false")
    _github_output("date", date_str)
    print(f"브릿지 완료: date={date_str} wrote={wrote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
