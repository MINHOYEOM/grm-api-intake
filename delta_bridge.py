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


def select_open_delta(token: str, db_id: str,
                       publish_date: str | None = None) -> dict[str, Any] | None:
    """Intake DB 에서 `Type or Class=web-delta ∧ Status=New` OPEN 페이지 1건을 고른다.

    `publish_date` 지정 시 그 날짜의 OPEN 페이지. 미지정 시 제목에서 파생한 날짜가
    **가장 큰**(최신) 페이지 1건(K4-1 "최신 OPEN 이 입력" 동형 — '오늘'을 계산하지 않는다).
    없으면 None(클린 skip 판단은 호출부 몫).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {
        "filter": {"and": [
            {"property": PROP_TYPE_CLASS, "select": {"equals": TYPE_WEB_DELTA}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
        ]},
        "page_size": 100,
    }
    candidates: list[tuple[str, dict[str, Any]]] = []
    start_cursor: str | None = None
    for _ in range(25):  # OPEN web-delta 페이지는 소수 — 안전 상한(handoff 패턴과 동형)
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            title = _prop_title(page.get("properties", {}), PROP_NAME)
            run_date = _date_from_title(title)
            if not run_date:
                log("WARN", f"web-delta 페이지 제목에서 날짜 파생 실패 — skip: {title!r}")
                continue
            candidates.append((run_date, page))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        log("WARN", "web-delta OPEN 조회 25페이지 상한 도달 — 일부 페이지 누락 가능")

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
        deep = _validate_deep(deep)

    publish_date = delta.get("publish_date") or title_date
    if not isinstance(publish_date, str) or not _DATE_RE.match(publish_date):
        raise DeltaBridgeError(
            f"publish_date 형식 오류: {publish_date!r} — ^\\d{{4}}-\\d{{2}}-\\d{{2}}$ 필요")

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

    if deep is not None:
        deep_path = _deep_path(date_str)
        new_deep_text = _dumps(deep)
        if os.path.exists(deep_path):
            with open(deep_path, "r", encoding="utf-8") as f:
                existing_deep_text = f.read()
            if existing_deep_text != new_deep_text:
                raise DeltaBridgeError(
                    f"deep 델타 {deep_path} 이미 존재하고 내용이 다릅니다 — 중복 가드.")
            log("INFO", f"deep 델타 {deep_path} 이미 동일 내용 — no-op(멱등)")
        else:
            os.makedirs(os.path.dirname(deep_path), exist_ok=True)
            with open(deep_path, "wb") as f:
                f.write(new_deep_text.encode("utf-8"))
            wrote = True
            log("INFO", f"deep 델타 기록: {deep_path}")

    return wrote


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
        log("INFO", "no OPEN web-delta — nothing to bridge")
        _github_output("wrote", "false")
        return 0

    if args.consume:
        try:
            consume_delta(token, page)
        except NotionHandoffError as e:
            print(f"ERROR: consume 실패: {e}", file=sys.stderr)
            return 1
        return 0

    try:
        page = _attach_code_blocks(token, page)
        delta, deep, date_str = extract_delta(page)
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
