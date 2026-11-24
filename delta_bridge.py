#!/usr/bin/env python3
"""Validate editorial Git returns and record the weekly delta and scaffold."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

from grm_common import log

try:
    import inject_slots
except ImportError:  # pragma: no cover — inject_slots 는 항상 동봉되지만 방어적으로.
    inject_slots = None  # type: ignore[assignment]

try:
    import verify_deep_analysis as _vda
except ImportError:  # pragma: no cover — 항상 동봉되지만 방어적으로.
    _vda = None  # type: ignore[assignment]


TYPE_WEB_DELTA = "web-delta"


TYPE_WEB_DEEP_DELTA = "web-deep-delta"


TITLE_PREFIX_OPEN = "OPEN GRM Web Delta "


TITLE_PREFIX_OPEN_DEEP = "OPEN GRM Web Deep Delta "


TITLE_PREFIX_CONSUMED = "CONSUMED GRM Web Delta "


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DeltaBridgeError(RuntimeError):
    """브릿지 fail-loud 전용 예외 — 구조 불량/네트워크 실패를 조용히 넘기지 않는다."""


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
# ★ 목록의 정본은 `verify_deep_analysis.TRANSLATION_ONLY_KEYS` — 로컬 fanout 경로
#   (`deep_analysis_fanout.assemble_deltas`)와 공용이다. 새 번역층은 거기에 추가할 것
#   (빠뜨리면 그 카드가 조용히 drop 된다). 게이트 모듈이 없으면 `_gate_deep_analysis` 가
#   이 목록을 보기 전에 원본을 그대로 돌려주므로 빈 튜플이어도 안전하다.
_TRANSLATION_ONLY_KEYS = _vda.TRANSLATION_ONLY_KEYS if _vda is not None else ()


def _keep_translation_residue(kept: dict[str, Any], doc: str, entry: dict[str, Any]) -> bool:
    """분석 게이트 FAIL/오류 entry 에서 번역층(+동반 `source_text`)만 추려 살린다. 살렸으면 True.

    번역은 결정론 원문의 국문 병기라 분석과 독립이고, 함께 실린 `source_text` 는 조립의
    결정론 재추출(`_refresh_*`)·병합 짝의 기준선이라 같이 남긴다. **번역층이 하나도 없으면
    종전대로 통째 drop**(False) — source_text 만 남기면 어느 계층도 소비하지 않는 빈 껍데기
    항목이 deep 파일에 쌓이고 "PASS 0건 → None" 계약도 깨진다.

    판정·추림은 `verify_deep_analysis.translation_residue` 한 곳에 있다 — 로컬 fanout 경로
    (`deep_analysis_fanout.assemble_deltas`)도 같은 함수를 부른다."""
    residue = _vda.translation_residue(entry)
    if residue is None:
        return False
    kept[doc] = residue
    return True


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
        # [중첩 예치 방어 2026-08-24] 번역층(`observations_ko`/`violations_ko`)이 4섹션 dict
        # **안에** 실려 오면 entry 층으로 끌어올린다 — 프롬프트 스키마 줄이 483 의
        # `observations_ko` 를 4섹션과 나란히 나열하고 있어 중첩 예치가 자연스러운 오독이고,
        # 중첩된 채로는 inject(`payload.get(...)` — entry 층만 본다)가 못 봐 번역이 조용히
        # 죽는다(fanout 경로의 `da.pop("observations_ko")` 리프팅과 동형 방어).
        if isinstance(da, dict):
            for t_key in ("observations_ko", "violations_ko"):
                nested = da.pop(t_key, None)
                if nested and t_key not in entry:
                    entry[t_key] = nested
        source = entry.get("source_text", "")
        try:
            gate = _vda.run_deep_analysis_gate(da, source, card_type=None)
        except Exception as e:  # noqa: BLE001 — 게이트 자체 오류는 그 카드만 drop(브리프 비차단)
            if _keep_translation_residue(kept, doc, entry):
                log("WARN", f"deep 게이트 실행 오류 — 분석 drop·번역/원문 층은 보존: {doc} ({e})")
            else:
                log("WARN", f"deep 게이트 실행 오류 — 카드 drop: {doc} ({e})")
            continue
        if getattr(gate, "ok", False):
            kept[doc] = entry
        else:
            # [번역층 독립 2026-08-24] 분석이 FAIL 해도 번역/원문 층(`_TRANSLATION_ONLY_KEYS`)은
            # 살린다 — 종전엔 entry 통째 drop 이라, 프롬프트가 약속한 "4섹션이 drop 되더라도
            # `observations_ko` 는 별도 층으로 살아 병합된다"가 이 경로에서 거짓이었다(483 은
            # 그대로 render fail-closed 게이트에 걸려 **그 주 브리프 전체 차단**으로 번졌을
            # 결함). 번역은 결정론 원문의 국문 병기일 뿐이라 분석 게이트의 검증 대상이 아니다.
            if _keep_translation_residue(kept, doc, entry):
                log("WARN", f"deep 게이트 FAIL — 분석 drop·번역/원문 층은 보존: {doc}\n"
                            f"{getattr(gate, 'report', '')}")
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


HANDOFF_MIRROR_BRANCH = "claude/queue-handoff"        # grm-queue-handoff-mirror.yml 이 쓰는 브랜치
_HANDOFF_FETCHED: dict[str, bool] = {}


def git_read_handoff(date_str: str) -> "str | None":
    """`origin/claude/queue-handoff:handoff_{date}.json` 원문. 브랜치·파일이 없으면 None(WARN 없이).

    사본은 최신 8일분만 남는다 — 오래된 호를 다시 돌리면 None 이 정상이다.
    """
    import subprocess
    ref = f"refs/remotes/origin/{HANDOFF_MIRROR_BRANCH}"
    if HANDOFF_MIRROR_BRANCH not in _HANDOFF_FETCHED:
        try:
            r = subprocess.run(
                ["git", "fetch", "--quiet", "--no-tags", "origin",
                 f"+refs/heads/{HANDOFF_MIRROR_BRANCH}:{ref}"],
                capture_output=True, text=True, timeout=120)
            _HANDOFF_FETCHED[HANDOFF_MIRROR_BRANCH] = r.returncode == 0
        except (OSError, subprocess.SubprocessError) as e:
            log("WARN", f"git fetch 실행 실패({HANDOFF_MIRROR_BRANCH}): {e}")
            _HANDOFF_FETCHED[HANDOFF_MIRROR_BRANCH] = False
    if not _HANDOFF_FETCHED[HANDOFF_MIRROR_BRANCH]:
        return None
    try:
        r = subprocess.run(["git", "show", f"{ref}:handoff_{date_str}.json"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        log("WARN", f"git show 실행 실패(handoff_{date_str}.json): {e}")
        return None
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def fetch_handoff_body_full_git(date_str: str, expected_sha: str = "",
                                read=None) -> "dict[str, str] | None":
    """DB handoff 의 브랜치 사본에서 카드별 심층분석 입력 원문을 읽는다. 쓸 수 없으면 None.

    None 인 경우: 파일 없음 · JSON 아님 · 날짜가 다름 · (expected_sha 를 줬는데) 지문이 다름.
    지문이 다르다는 것은 편집팀이 읽은 handoff 와 지금 사본이 다르다는 뜻이다 — 다른 원문으로
    대조하면 멀쩡한 인용이 거짓 FAIL 나거나 틀린 인용이 통과한다. 그럴 때는 쓰지 않는다.
    """
    text = (read or git_read_handoff)(date_str)
    if text is None:
        return None
    payload = _try_json_dict(text)
    if payload is None or payload.get("run_date_kst") != date_str:
        log("WARN", f"handoff 사본(handoff_{date_str}.json)이 그 날짜의 payload 가 아님 — 사용 안 함")
        return None
    if expected_sha:
        import grm_queue
        got = grm_queue.payload_sha256(payload)
        if got != expected_sha:
            log("WARN", f"handoff 사본 지문이 델타의 handoff_ref 와 다름({got[:12]} != {expected_sha[:12]}) "
                        "— 사용 안 함")
            return None
    bodies = extract_handoff_body_full(payload)
    log("INFO", f"handoff 심층분석 입력 원문 {len(bodies)}건 확보(git 사본 {date_str})")
    return bodies


def _authoritative_source_text(date_str: str, deep: dict[str, Any],
                               handoff_ref: "dict[str, Any] | None" = None) -> None:
    """Use the Git handoff body when available, checking the input hash when supplied."""
    ref = handoff_ref if isinstance(handoff_ref, dict) else {}
    try:
        bodies = fetch_handoff_body_full_git(date_str, str(ref.get("sha256") or ""))
    except Exception as e:  # The deep layer must not block the brief.
        log("WARN", f"Handoff Git lookup failed ({date_str}): {type(e).__name__}: {e}")
        bodies = None
    if bodies is None:
        log("WARN", f"Handoff Git copy unavailable ({date_str}); using returned source_text")
    elif bodies:
        apply_handoff_source_text(deep, bodies)


def _try_json_dict(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def finalize_delta(delta: dict[str, Any], deep: "dict[str, Any] | None",
                   fallback_date: str) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """envelope 를 찾은 뒤의 공통 검증·정규화 — **노션 예치와 git 반납이 같은 문을 지난다.**

    [2026-11-12] 결과 반납 git 길(편집팀 `claude/company-{date}` 의 `delta.json`)이 생기면서
    `extract_delta` 의 뒷부분을 떼어 냈다. 두 길이 서로 다른 검증을 지나면 "어느 쪽이 맞나"
    비교가 검증 차이까지 섞여 의미가 없어진다.
    """
    delta = _validate_envelope(delta, what="delta envelope")
    if deep is not None:
        deep = _validate_deep(deep)  # 구조 파싱만(게이트는 write 직전 main 에서 — 파싱/검증 분리)

    publish_date = delta.get("publish_date") or fallback_date
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


def _under(date_str: str) -> str:
    return date_str.replace("-", "_")


def _delta_path(date_str: str) -> str:
    return os.path.join("web", "data", "deltas", f"delta_{_under(date_str)}.json")


def _scaffold_path(date_str: str) -> str:
    """main 에 동결하는 그 호의 스캐폴드(빈 슬롯 브리프). `grm-web-publish.yml` 이 있으면 이것을 쓴다."""
    return os.path.join("web", "data", "scaffolds", f"brief_web_{_under(date_str)}.json")


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


# ── 결과 반납 git 길 (재검토안 3절 1단계, 2026-11-12) ─────────────────────────────────
#
# 편집팀(클라우드)은 결과를 브랜치 `claude/company-{date}` 의 `company/run/{date}/` 에 쓰고, 교열이
# `merge_parts.py` 로 `delta.json`·`deep_delta.json` 을 만든다. 지금까지는 교열이 그것을 다시
# 노션에 **옮겨 적어** 예치했고, 이 브릿지는 노션에서 읽었다(git → 노션 → git 왕복). 노션 코드
# 블록 경계에서 공백이 깎이는 오류(`in the`→`inthe`)가 이 왕복에서 생긴다.
#
# 어느 길이 정본인가 — 플래그 `GRM_DELTA_SOURCE`(저장소 변수 · dispatch 입력 `source`):
#   · `notion`(기본) : 지금과 같다. 노션 예치가 정본이고, git 쪽은 **비교만** 한다(쓰지 않는다).
#                     11-16 은 이 모드로 두 길을 나란히 받아 차이표를 남긴다.
#   · `git`          : git 반납을 **먼저** 본다. 채택 조건을 채우면 그것을 쓰고, 아니면 지금처럼
#                     노션 예치로 넘어간다(노션 = 예비).
# 같은 날짜에 두 길이 다른 내용을 주면 **먼저 main 에 기록된 쪽이 이긴다.** git 모드에서 git 이
# 늦게 와 이미 기록된 파일과 다르면 오류로 멈추지 않고 차이표만 남긴다(발행 흐름을 막지 않는다).
# 노션 모드에서 git 쪽은 어떤 경우에도 파일을 쓰지 않는다.

SOURCE_GIT = "git"
GIT_BRANCH_TPL = "claude/company-{date}"
GIT_RUN_DIR_TPL = "company/run/{date}"
# [백업 v17] 편집팀이 마감하지 못한 호를 백업 Routine(`company/editorial/backup.md`)이 같은 브랜치의
#   하위 폴더 `company/run/{date}/backup/` 에 **편집팀과 같은 파일·같은 형식**으로 반납한다.
#   브릿지는 편집팀 반납을 먼저 보고, 채택할 수 없을 때만 이 폴더를 본다(같은 채택 조건).
#   한 날짜에 main 에 쓰는 것은 하나뿐이다 — 먼저 기록된 쪽이 이긴다(_existing_conflicts).
BACKUP_SUBDIR = "backup"
COMPARE_DIR = "_delta_compare"  # 워크플로가 아티팩트로 올린다(커밋 대상 아님)


def _kst_today() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


_GIT_FETCHED: dict[str, bool] = {}


def git_read_run_file(date_str: str, name: str) -> "str | None":
    """`origin/claude/company-{date}:company/run/{date}/{name}` 원문. 브랜치·파일이 없으면 None.

    실패는 전부 None(WARN) — git 길은 선택 경로라 이 함수가 노션 길을 막으면 안 된다.
    """
    import subprocess
    branch = GIT_BRANCH_TPL.format(date=date_str)
    ref = f"refs/remotes/origin/{branch}"
    if date_str not in _GIT_FETCHED:
        try:
            r = subprocess.run(
                ["git", "fetch", "--quiet", "--no-tags", "origin",
                 f"+refs/heads/{branch}:{ref}"],
                capture_output=True, text=True, timeout=120)
            _GIT_FETCHED[date_str] = r.returncode == 0
            if r.returncode != 0:
                log("INFO", f"git 반납 브랜치 {branch} 없음/fetch 실패 — {r.stderr.strip()[:200]}")
        except (OSError, subprocess.SubprocessError) as e:
            log("WARN", f"git fetch 실행 실패({branch}): {e}")
            _GIT_FETCHED[date_str] = False
    if not _GIT_FETCHED[date_str]:
        return None
    path = f"{GIT_RUN_DIR_TPL.format(date=date_str)}/{name}"
    try:
        r = subprocess.run(["git", "show", f"{ref}:{path}"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        log("WARN", f"git show 실행 실패({path}): {e}")
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8")


def load_git_run(date_str: str, read=None, sub: str = "") -> dict[str, Any]:
    """git 반납 한 주를 읽어 채택 여부를 판정한다(순수 판정 + 주입 가능한 reader).

    sub: 실행 폴더 안의 하위 폴더(`backup` = 백업 v17 반납). 비면 편집팀 반납(종전과 같다).

    채택 조건(전부): ① `copyedit.json` 이 있고 `status=="완료"` ② 교열 코드 검사
    `code_gate.ran` 이고 `exit==0` ③ reject 0(`counts.reject` 와 `cards[].verdict` 둘 다)
    ④ `delta.json` 이 JSON dict ⑤ `deep_delta.json` 이 있으면 JSON dict.
    샤드 미완은 `merge_parts.py` 가 `delta.json` 을 만들지 않고, 교열은 그때 `보류`로 끝나므로
    ①이 그 판정을 이어받는다.

    채택 불가여도 `delta`·`deep` 은 읽을 수 있으면 채워 둔다 — **비교에는 쓰고, 기록에는 안 쓴다.**
    반환: {"date","branch","found","adoptable","reason","delta","deep"}.
    """
    base_read = read or git_read_run_file
    prefix = f"{sub.strip('/')}/" if sub else ""

    def read(d: str, name: str) -> "str | None":
        return base_read(d, prefix + name)

    out: dict[str, Any] = {"date": date_str, "branch": GIT_BRANCH_TPL.format(date=date_str),
                           "found": False, "adoptable": False, "reason": "",
                           "delta": None, "deep": None, "sub": sub or "",
                           "by": "backup" if sub == BACKUP_SUBDIR else "editorial"}
    reasons: list[str] = []

    delta_text = read(date_str, "delta.json")
    deep_text = read(date_str, "deep_delta.json")
    copy_text = read(date_str, "copyedit.json")
    out["found"] = any(t is not None for t in (delta_text, deep_text, copy_text))
    if not out["found"]:
        where = f"{out['branch']}" + (f" 의 {prefix}" if prefix else "")
        out["reason"] = f"브랜치 {where} 에 반납 파일 없음"
        return out

    if delta_text is None:
        reasons.append("delta.json 없음(샤드 합치기 전·실패)")
    else:
        out["delta"] = _try_json_dict(delta_text)
        if out["delta"] is None:
            reasons.append("delta.json JSON 파싱 실패")
    if deep_text is not None:
        out["deep"] = _try_json_dict(deep_text)
        if out["deep"] is None:
            reasons.append("deep_delta.json JSON 파싱 실패")

    copyedit = _try_json_dict(copy_text) if copy_text is not None else None
    if copy_text is None:
        reasons.append("copyedit.json 없음(교열 미완)")
    elif copyedit is None:
        reasons.append("copyedit.json JSON 파싱 실패")
    else:
        if copyedit.get("status") != "완료":
            reasons.append(f"교열 status={copyedit.get('status')!r}(완료 아님)")
        gate = copyedit.get("code_gate") if isinstance(copyedit.get("code_gate"), dict) else {}
        if not gate.get("ran") or gate.get("exit") != 0:
            reasons.append(f"교열 코드 검사 미통과(ran={gate.get('ran')!r}, exit={gate.get('exit')!r})")
        counts = copyedit.get("counts") if isinstance(copyedit.get("counts"), dict) else {}
        rejects = [c for c in (copyedit.get("cards") or [])
                   if isinstance(c, dict) and c.get("verdict") != "pass"]
        if (counts.get("reject") or 0) > 0 or rejects:
            reasons.append(f"교열 reject {max(counts.get('reject') or 0, len(rejects))}장")

    out["adoptable"] = not reasons
    out["reason"] = " · ".join(reasons) if reasons else "채택 조건 충족"
    return out


def _scalar_ws(v: Any) -> Any:
    """공백만 다른 차이를 가려내려고 모든 공백을 지운 비교용 사본."""
    if isinstance(v, str):
        return re.sub(r"\s+", "", v)
    if isinstance(v, list):
        return [_scalar_ws(x) for x in v]
    if isinstance(v, dict):
        return {k: _scalar_ws(x) for k, x in v.items()}
    return v


def _diff_mapping(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """카드(또는 deep 항목) 단위 차이. a=노션, b=git."""
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    changed = []
    for k in sorted(set(a) & set(b)):
        va, vb = a[k], b[k]
        if va == vb:
            continue
        if isinstance(va, dict) and isinstance(vb, dict):
            slots = sorted(s for s in set(va) | set(vb) if va.get(s) != vb.get(s))
        else:
            slots = ["(값)"]
        changed.append({"id": k, "slots": slots,
                        "whitespace_only": _scalar_ws(va) == _scalar_ws(vb)})
    return {"only_notion": only_a, "only_git": only_b, "changed": changed}


def compare_deltas(notion_delta: "dict[str, Any] | None", notion_deep: "dict[str, Any] | None",
                   git_delta: "dict[str, Any] | None", git_deep: "dict[str, Any] | None"
                   ) -> dict[str, Any]:
    """두 길의 최종본(검증·정규화·게이트까지 지난 것)을 바이트·카드 단위로 비교(순수 함수)."""
    nd, gd = notion_delta or {}, git_delta or {}
    report: dict[str, Any] = {
        "delta_bytes_equal": notion_delta is not None and git_delta is not None
                             and _dumps(notion_delta) == _dumps(git_delta),
        "deep_bytes_equal": _dumps(notion_deep or {}) == _dumps(git_deep or {}),
        "cards": _diff_mapping(nd.get("cards") or {}, gd.get("cards") or {}),
        "deep": _diff_mapping(notion_deep or {}, git_deep or {}),
        "tldr_equal": nd.get("tldr") == gd.get("tldr"),
        "other_keys_changed": sorted(
            k for k in (set(nd) | set(gd)) - {"cards", "tldr"} if nd.get(k) != gd.get(k)),
        "counts": {"notion_cards": len(nd.get("cards") or {}),
                   "git_cards": len(gd.get("cards") or {}),
                   "notion_deep": len(notion_deep or {}), "git_deep": len(git_deep or {})},
    }
    report["identical"] = report["delta_bytes_equal"] and report["deep_bytes_equal"]
    return report


def render_compare_md(report: dict[str, Any], meta: dict[str, Any]) -> str:
    """Actions 요약용 차이표(한국어)."""
    c, d = report["cards"], report["deep"]
    n = report["counts"]
    ws = sum(1 for x in c["changed"] if x["whitespace_only"])
    lines = [
        f"## 델타 두 길 비교 — {meta.get('date', '')}",
        "",
        f"- 정본 길: `{meta.get('source')}` · 이번 기록: {meta.get('written_from') or '없음'}",
        f"- git 반납: `{meta.get('branch', '')}` — {'채택 가능' if meta.get('adoptable') else '채택 불가'}"
        f" ({meta.get('reason', '')})",
        f"- 결과: **{'완전히 같음' if report['identical'] else '다름'}**",
        "",
        "| 항목 | 노션 | git |",
        "|---|---|---|",
        f"| 카드 수 | {n['notion_cards']} | {n['git_cards']} |",
        f"| 심층분석 수 | {n['notion_deep']} | {n['git_deep']} |",
        f"| 한쪽에만 있는 카드 | {len(c['only_notion'])} | {len(c['only_git'])} |",
        f"| 내용이 다른 카드 | {len(c['changed'])} (공백만 다름 {ws}) | |",
        f"| 내용이 다른 심층분석 | {len(d['changed'])} | |",
        f"| 요약(tldr) | {'같음' if report['tldr_equal'] else '다름'} | |",
    ]
    if report["other_keys_changed"]:
        lines.append(f"| 그 밖에 다른 키 | {', '.join(report['other_keys_changed'])} | |")
    rows = c["changed"][:30]
    if rows:
        lines += ["", "### 내용이 다른 카드 (최대 30)", "", "| 카드 | 다른 칸 | 공백만 다름 |",
                  "|---|---|---|"]
        lines += [f"| `{x['id']}` | {', '.join(x['slots'])} | {'예' if x['whitespace_only'] else ''} |"
                  for x in rows]
    for label, ids in (("노션에만 있는 카드", c["only_notion"]), ("git 에만 있는 카드", c["only_git"])):
        if ids:
            lines += ["", f"- {label}: " + ", ".join(f"`{i}`" for i in ids[:20])
                      + (" …" if len(ids) > 20 else "")]
    return "\n".join(lines) + "\n"


def _publish_compare(report: dict[str, Any], meta: dict[str, Any]) -> None:
    """차이표를 Actions 요약과 `_delta_compare/` 파일로 남긴다(실패해도 조용히 WARN)."""
    try:
        md = render_compare_md(report, meta)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(md)
        os.makedirs(COMPARE_DIR, exist_ok=True)
        base = os.path.join(COMPARE_DIR, f"compare_{_under(meta.get('date', 'unknown'))}")
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "report": report}, f, ensure_ascii=False, indent=1)
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(md)
        log("INFO", f"두 길 비교: {'같음' if report['identical'] else '다름'} — "
                    f"카드 차이 {len(report['cards']['changed'])} · 노션만 "
                    f"{len(report['cards']['only_notion'])} · git만 {len(report['cards']['only_git'])}")
    except Exception as e:  # noqa: BLE001 — 비교는 관측 장치, 발행 경로를 막지 않는다
        log("WARN", f"비교 결과 기록 실패(무시): {e}")


def prepare_git_run(run: dict[str, Any]
                    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """git 반납을 노션 예치와 **같은 문**(finalize → handoff 원문 정본화 → 게이트)에 통과시킨다."""
    if run.get("delta") is None:
        raise DeltaBridgeError(f"git 반납 {run.get('branch')}: delta.json 없음/파싱 실패")
    delta = json.loads(json.dumps(run["delta"], ensure_ascii=False))  # 원본 보존(비교용)
    deep = json.loads(json.dumps(run["deep"], ensure_ascii=False)) if run.get("deep") else None
    delta, deep, date_str = finalize_delta(delta, deep, run["date"])
    if date_str != run["date"]:
        raise DeltaBridgeError(
            f"git 반납 publish_date={date_str} 가 브랜치 날짜 {run['date']} 와 다릅니다")
    if deep is not None:
        _authoritative_source_text(date_str, deep,
                                   handoff_ref=delta.get("handoff_ref"))
        deep = _gate_deep_analysis(deep)
    return delta, deep, date_str


def _compare_with_git(date_str: str, source: str,
                      delta: dict[str, Any], deep: "dict[str, Any] | None",
                      written_from: str, run: "dict[str, Any] | None" = None,
                      read=None) -> None:
    """노션 쪽 최종본과 git 반납을 비교해 남긴다 — **어떤 실패도 밖으로 내보내지 않는다.**"""
    try:
        run = run or load_git_run(date_str, read=read)
        meta = {"date": date_str, "source": source, "written_from": written_from,
                "branch": run["branch"], "adoptable": run["adoptable"], "reason": run["reason"]}
        if not run["found"] or run.get("delta") is None:
            log("INFO", f"git 반납 비교 생략 — {run['reason']}")
            _publish_compare(compare_deltas(delta, deep, None, None), meta)
            return
        try:
            g_delta, g_deep, _ = prepare_git_run(run)
        except DeltaBridgeError as e:
            meta["reason"] += f" · git 쪽 검증 실패: {e}"
            g_delta, g_deep = run.get("delta"), run.get("deep")
        _publish_compare(compare_deltas(delta, deep, g_delta, g_deep), meta)
    except Exception as e:  # noqa: BLE001
        log("WARN", f"git 반납 비교 실패(무시 — 발행 경로는 계속): {e}")


def _existing_conflicts(delta: dict[str, Any], deep: "dict[str, Any] | None",
                        date_str: str) -> list[str]:
    """이미 main 에 있는 파일 중 이번 내용과 **다른** 것의 경로(없거나 같으면 빈 목록)."""
    bad = []
    for path, payload in ((_delta_path(date_str), delta), (_deep_path(date_str), deep)):
        if payload is None or not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            if f.read() != _dumps(payload):
                bad.append(path)
    return bad


def _load_existing(date_str: str) -> tuple["dict[str, Any] | None", "dict[str, Any] | None"]:
    out = []
    for path in (_delta_path(date_str), _deep_path(date_str)):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                out.append(_try_json_dict(f.read()))
        else:
            out.append(None)
    return out[0], out[1]


SCAFFOLD_RUN_NAME = "scaffold.json"   # 교열이 코드 검사에 쓴 스캐폴드를 실행 폴더에 복사해 둔 것


def load_run_scaffold(run: dict[str, Any], delta: dict[str, Any], read=None) -> "str | None":
    """git handoff 로 만든 호의 스캐폴드 원문. 해당 없으면 None, 있어야 하는데 못 쓰면 DeltaBridgeError.

    [대기열 3단계 2026-11-13] 편집팀이 DB handoff(`claude/queue-handoff`)를 읽고 만든 호는 델타에
    `handoff_ref` 가 붙는다. 그 호의 기자는 **DB 로 만든 스캐폴드**의 사실 칸을 보고 썼다. 발행 조립이
    수집 실행의 아티팩트(노션 길로 만든 스캐폴드)를 쓰면 사실 칸이 다를 수 있으므로, 교열이 검사에 쓴
    바로 그 파일을 main 에 동결해 발행이 그것을 쓰게 한다.
      - 델타에 `handoff_ref` 없음(노션 handoff 로 만든 호) → None. 실행 폴더에 파일이 있어도 쓰지 않는다.
      - 있음 → `scaffold.json` 이 있어야 하고, 그 날짜의 것이어야 하고, 델타의 카드를 전부 갖고 있어야 한다.
        하나라도 아니면 예외 — 호출부가 git 반납을 버리고 노션 예비 길로 넘긴다(틀린 재료로 발행하지 않는다).
    돌려주는 것은 **원문 그대로**다(다시 직렬화하지 않는다 — 교열이 검사한 바이트를 그대로 동결).
    """
    if not isinstance(delta.get("handoff_ref"), dict):
        return None
    read = read or git_read_run_file
    sub = str(run.get("sub") or "").strip("/")
    text = read(run["date"], f"{sub}/{SCAFFOLD_RUN_NAME}" if sub else SCAFFOLD_RUN_NAME)
    if text is None:
        raise DeltaBridgeError(
            f"git handoff 로 만든 호인데 {run.get('branch')} 에 {SCAFFOLD_RUN_NAME} 이 없다")
    obj = _try_json_dict(text)
    cards = obj.get("cards") if obj is not None else None
    if not isinstance(cards, list):
        raise DeltaBridgeError(f"{SCAFFOLD_RUN_NAME}: 스캐폴드 모양이 아니다(cards 목록 없음)")
    brief = obj.get("brief") if isinstance(obj.get("brief"), dict) else {}
    if brief.get("run_date_kst") != run["date"]:
        raise DeltaBridgeError(
            f"{SCAFFOLD_RUN_NAME}: run_date_kst={brief.get('run_date_kst')!r} 가 {run['date']} 가 아니다")
    have = {str(c.get("id")) for c in cards if isinstance(c, dict)}
    missing = sorted(k for k in (delta.get("cards") or {}) if k not in have)
    if missing:
        raise DeltaBridgeError(
            f"{SCAFFOLD_RUN_NAME}: 델타 카드 {len(missing)}장이 스캐폴드에 없다(예: {missing[:3]})")
    return text


def write_scaffold(text: str, date_str: str) -> bool:
    """스캐폴드 원문을 main 경로에 쓴다. 반환 = 새로 썼는가. 이미 있으면 **건드리지 않는다**(동결).

    이미 있는 파일과 내용이 다르면 경고만 한다 — 먼저 동결된 것이 그 호의 발행 재료다.
    """
    path = _scaffold_path(date_str)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", newline="") as f:
            if f.read() != text:
                log("WARN", f"스캐폴드 {path} 가 이미 있고 내용이 다릅니다 — 먼저 동결된 것을 유지합니다.")
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))
    log("INFO", f"스캐폴드 동결: {path}")
    return True


def _copyedit_done(date_str: str, read=None) -> bool:
    """그 날짜 브랜치의 `copyedit.json` 이 `status=="완료"`·`published: true`(교열이 발행 반납을 마쳤다)인가.
    못 읽으면 False."""
    try:
        text = (read or git_read_run_file)(date_str, "copyedit.json")
    except Exception:  # noqa: BLE001 — 판정 보조, 실패는 '모름'
        return False
    obj = _try_json_dict(text) if text is not None else None
    return bool(obj) and obj.get("status") == "완료" and obj.get("published") is True


def bridge_from_git(date_str: str, read=None,
                    held: "list[str] | None" = None) -> "int | None":
    """Write the first adoptable return. Record why completed returns were held."""
    def _hold(reason: str) -> None:
        if held is not None and _copyedit_done(date_str, read=read):
            held.append(reason)

    run = load_git_run(date_str, read=read)
    picked = None
    failure: "DeltaBridgeError | None" = None
    if run["adoptable"]:
        try:
            picked = _prepare_adoptable(run, read)
        except DeltaBridgeError as e:
            failure = e
            log("WARN", f"편집팀 git 반납 검증 실패 — 백업 반납을 본다: {e}")
    else:
        log("INFO", f"편집팀 git 반납 채택 안 함({run['branch']}): {run['reason']}")
    if picked is None:
        # [백업 v17] 편집팀 반납을 쓸 수 없을 때만 같은 브랜치의 backup/ 을 같은 조건으로 본다.
        brun = load_git_run(date_str, read=read, sub=BACKUP_SUBDIR)
        if brun["adoptable"]:
            try:
                picked = _prepare_adoptable(brun, read)
                run = brun
                log("INFO", f"백업 v17 반납을 쓴다({brun['branch']} 의 {BACKUP_SUBDIR}/)")
            except DeltaBridgeError as e:
                failure = failure or e
                log("WARN", f"백업 git 반납 검증 실패: {e}")
        elif brun["found"]:
            log("INFO", f"백업 git 반납 채택 안 함: {brun['reason']}")
    if picked is None:
        _hold(f"git 반납 검증 실패: {failure}" if failure is not None
              else f"git 반납 채택 안 함({run['branch']}): {run['reason']}")
        if failure is not None:
            raise DeltaBridgeError(f"Git return validation failed: {failure}") from failure
        return None
    delta, deep, date_str, scaffold_text = picked

    conflicts = _existing_conflicts(delta, deep, date_str)
    if conflicts:
        # 먼저 기록된 쪽이 이긴다 — 오류로 멈추면 30분마다 빨간 run 이 쌓이고 발행이 꼬인다.
        log("WARN", f"이미 main 에 다른 내용이 있음({', '.join(conflicts)}) — 먼저 기록된 것을 "
                    "유지하고 git 반납은 비교만 남깁니다.")
        e_delta, e_deep = _load_existing(date_str)
        meta = {"date": date_str, "source": SOURCE_GIT, "written_from": "이미 기록됨(유지)",
                "branch": run["branch"], "adoptable": True, "reason": run["reason"]}
        _publish_compare(compare_deltas(e_delta, e_deep, delta, deep), meta)
        wrote = False
    else:
        wrote = write_delta(delta, deep, date_str)
        if scaffold_text is not None and write_scaffold(scaffold_text, date_str):
            wrote = True

    _github_output("wrote", "true" if wrote else "false")
    _github_output("date", date_str)
    _github_output("source", SOURCE_GIT)
    _github_output("by", run.get("by") or "editorial")
    print(f"브릿지 완료(git 반납 · {run.get('by') or 'editorial'}): date={date_str} wrote={wrote}")
    return 0


def _prepare_adoptable(run: dict[str, Any], read=None
                       ) -> tuple[dict[str, Any], "dict[str, Any] | None", str, "str | None"]:
    """채택 조건을 채운 반납 하나를 발행 문(검증·원문 정본화·스캐폴드)에 통과시킨다. 실패는 DeltaBridgeError."""
    delta, deep, date_str = prepare_git_run(run)
    try:
        scaffold_text = load_run_scaffold(run, delta, read=read)
    except DeltaBridgeError as e:
        raise DeltaBridgeError(f"스캐폴드를 쓸 수 없음: {e}") from e
    return delta, deep, date_str, scaffold_text


def compare_only(date_str: str, read=None) -> int:
    """이미 main 에 기록된 그 주 델타와 git 반납을 비교만 한다(쓰기·노션 마감 없음)."""
    e_delta, e_deep = _load_existing(date_str)
    if e_delta is None:
        log("WARN", f"{_delta_path(date_str)} 없음 — 비교할 기록본이 없습니다")
    _compare_with_git(date_str, "compare-only", e_delta or {}, e_deep,
                      written_from="(비교 전용)", read=read)
    _github_output("wrote", "false")
    _github_output("date", date_str)
    return 0


def _github_output(key: str, value: str) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def _fail_unrecorded_return(git_date: str, git_held: "list[str]", tail: str) -> bool:
    """[11-23 사전 점검 B] 교열은 반납을 완료로 마쳤는데 git 으로 기록하지 못했고, 대신할 길도 없다 —
    초록 '클린 skip' 이면 그 주가 조용히 발행되지 않는다. 실패로 알리고 True 를 돌려 run 을 빨갛게 한다.
    이미 main 에 그 주 델타가 있으면 해당 없음(False). 노션 길(OPEN 델타 없음)·노션 off 길이 같이 쓴다."""
    if not git_held or os.path.exists(_delta_path(git_date)):
        return False
    reason = " · ".join(git_held)
    msg = f"{git_date} 교열 반납(copyedit.json status=완료)을 기록하지 못했다 — {reason}. {tail}"
    print(f"ERROR: {msg}", file=sys.stderr)
    print(f"::error title=교열 반납 미기록::{msg}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"### 교열 반납 미기록 ({git_date})\n\n- {msg}\n"
                        "- 고친 뒤 브릿지를 `publish_date` 로 다시 돌린다.\n\n")
        except OSError:
            pass
    return True


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
        description="Record a Git editorial return in web/data/deltas.")
    ap.add_argument("--publish-date", default=None, help="Publication date (YYYY-MM-DD)")
    ap.add_argument("--compare-only", action="store_true",
                     help="쓰지 않고, main 에 이미 기록된 그 주 델타와 git 반납을 비교만 한다 "
                          "(--publish-date 필수).")
    args = ap.parse_args(argv)
    if args.compare_only:
        if not args.publish_date:
            print("ERROR: --compare-only 는 --publish-date 가 필요합니다", file=sys.stderr)
            return 1
        return compare_only(args.publish_date)

    git_held: list[str] = []
    git_date = args.publish_date or _kst_today()
    try:
        rc = bridge_from_git(git_date, held=git_held)
    except DeltaBridgeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if rc is not None:
        return rc
    _github_output("wrote", "false")
    _github_output("date", git_date)
    if _fail_unrecorded_return(git_date, git_held, "No files were written."):
        return 1
    log("INFO", f"No adoptable Git return({git_date})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
