#!/usr/bin/env python3
"""[원문 갱신 규칙 R1 2026-11-12] 정본 원문을 바꾸는 유일한 Python 길 — DB 함수 raw_signal_apply_version(148).

원문(raw_signals.raw_json)을 바꾸는 코드는 이 모듈만 쓴다(`tests/test_raw_signals_write_guard.py`).
- 지문은 여기서 한 번만 계산한다: sha256(grm_findings.canonical_json(raw)). DB 함수는 검증만 한다.
- expected_raw_sha256 은 **읽었을 때의** 현재 지문이다. 그 사이 다른 도구가 바꾸면 'conflict' 로 돌아온다.
- 옛 판 보관·판 번호는 DB 함수가 한 트랜잭션에서 처리한다.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

import requests

import grm_findings as gf

CHANGE_KINDS = ("source_update", "reextract", "repair", "baseline")
_TIMEOUT_S = 30


def canonical_raw(raw: dict[str, Any]) -> tuple[str, str]:
    """(정규화 raw_json 텍스트, 그 sha256)."""
    text = gf.canonical_json(raw)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply_version(base_url: str, service_key: str, *, raw_signal_id: str,
                  expected_raw_sha256: str, record: dict[str, Any], change_kind: str,
                  changed_by: str, content_sha256: str = "",
                  diff_summary: dict[str, Any] | None = None,
                  post: Callable[..., Any] | None = None) -> dict[str, Any]:
    """RPC 호출. 반환: {'status': applied|unchanged|conflict|not_found|error, ...}.

    record 는 새 판의 raw_signals 열 값이다. raw_json·row_json·raw_sha256 은 필수이고,
    raw_json·raw_sha256 은 canonical_raw() 로 만든 값이어야 한다(DB 가 검증한다).
    예외 문자열·응답 본문은 싣지 않는다(서비스 키·원문이 섞일 수 있다).
    """
    if change_kind not in CHANGE_KINDS:
        raise ValueError(f"change_kind {change_kind!r}")
    for key in ("raw_json", "row_json", "raw_sha256"):
        if not isinstance(record.get(key), str) or not record.get(key):
            raise ValueError(f"record.{key} 필수")
    body = {
        "p_raw_signal_id": raw_signal_id,
        "p_expected_raw_sha256": expected_raw_sha256,
        "p_record": record,
        "p_change_kind": change_kind,
        "p_changed_by": changed_by,
        "p_content_sha256": content_sha256,
        "p_diff_summary": diff_summary or {},
    }
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
               "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/rest/v1/rpc/raw_signal_apply_version"
    post = post or requests.post   # 호출 시점에 찾는다(시험의 mock.patch 가 닿게)
    for attempt in range(2):
        try:
            resp = post(url, headers=headers, json=body, timeout=_TIMEOUT_S)
        except requests.exceptions.Timeout:
            # 시간 초과는 적용 여부를 모른다 → 재시도하면 expected 지문이 이미 바뀌어 'conflict' 로
            # 안전하게 드러난다(두 번 적용되지 않는다).
            if attempt == 0:
                continue
            return {"status": "error", "error": "timeout"}
        except requests.exceptions.RequestException as exc:
            return {"status": "error", "error": type(exc).__name__}
        if resp.status_code >= 500 and attempt == 0:
            continue
        if resp.status_code >= 400:
            return {"status": "error", "error": f"http_{resp.status_code}"}
        try:
            data = resp.json()
        except ValueError:
            return {"status": "error", "error": "invalid_rpc_response"}
        return data if isinstance(data, dict) and data.get("status") else {
            "status": "error", "error": "invalid_rpc_response"}
    return {"status": "error", "error": "retry_exhausted"}


def run_label(tool: str) -> str:
    """changed_by 값: 도구 이름 + Actions run id(로컬이면 'local')."""
    import os
    return f"{tool}:{os.environ.get('GITHUB_RUN_ID', 'local')}"


class ApplyVersionError(RuntimeError):
    """apply_version 이 applied/unchanged 가 아닌 상태를 돌려줬다(conflict·not_found·error)."""

    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__(f"apply_version {status}{':' + detail if detail else ''}")
        self.status = status


def replace_raw(base_url: str, service_key: str, row: dict[str, Any], new_raw: dict[str, Any], *,
                change_kind: str, changed_by: str, diff_summary: dict[str, Any] | None = None,
                record_extra: dict[str, Any] | None = None, content_sha256: str = "",
                post: Callable[..., Any] | None = None) -> dict[str, Any]:
    """[R3] 수리 도구 공용: 읽어 둔 행(row)의 원문을 new_raw 로 바꾼다 — 함수 경유·옛 판 보관.

    row 에는 raw_signal_id·raw_sha256 이 있어야 한다(읽은 시점 지문 = expected).
    row_json 은 record_extra 에 새 값이 없으면 row 의 것을 그대로 쓴다(PATCH 시절과 같은 동작).
    applied/unchanged 가 아니면 ApplyVersionError — 호출자는 건별 실패로 세고 넘긴다(멱등 재실행).
    """
    rid = str(row.get("raw_signal_id") or "")
    expected = str(row.get("raw_sha256") or "")
    if not rid or not expected:
        raise ApplyVersionError("error", "row 에 raw_signal_id·raw_sha256 필요")
    text, sha = canonical_raw(new_raw)
    record: dict[str, Any] = dict(record_extra or {})
    record["raw_json"] = text
    record["raw_sha256"] = sha
    if not isinstance(record.get("row_json"), str) or not record.get("row_json"):
        record["row_json"] = str(row.get("row_json") or "") or "{}"
    res = apply_version(base_url, service_key, raw_signal_id=rid, expected_raw_sha256=expected,
                        record=record, change_kind=change_kind, changed_by=changed_by,
                        content_sha256=content_sha256, diff_summary=diff_summary, post=post)
    status = str(res.get("status") or "error")
    if status not in ("applied", "unchanged"):
        raise ApplyVersionError(status, str(res.get("error") or ""))
    return {**res, "new_raw_sha256": sha, "new_raw_json": text}


def apply_record(base_url: str, service_key: str, record: dict[str, Any], row: dict[str, Any] | None,
                 *, change_kind: str, changed_by: str, reason: str,
                 post: Callable[..., Any] | None = None,
                 ) -> tuple[int, list[dict[str, Any]] | None, str]:
    """[R3] 재구성한 raw_signals 레코드 전체(수집기와 같은 함수로 만든 것)를 함수 경유로 교체.

    예전 merge-duplicates upsert 자리에 그대로 들어가도록 반환 모양을 (status, returned, err) 로 맞춘다.
    row 는 읽어 둔 현재 행(raw_sha256 = expected). 레코드의 raw_json·raw_sha256 은 수집기 경로
    (grm_findings.raw_signal_from_row)가 정규화해 만든 값이라 DB 검증을 그대로 통과한다.
    """
    rid = str(record.get("raw_signal_id") or "")
    expected = str((row or {}).get("raw_sha256") or "")
    if not rid or not expected:
        return 0, None, "missing_expected_raw_sha256"
    try:
        res = apply_version(base_url, service_key, raw_signal_id=rid, expected_raw_sha256=expected,
                            record=record, change_kind=change_kind, changed_by=changed_by,
                            diff_summary={"reason": reason}, post=post)
    except ValueError:
        return 0, None, "invalid_record"
    status = str(res.get("status") or "error")
    if status not in ("applied", "unchanged"):
        return 0, None, f"apply_version_{status}"
    return 200, [{"raw_signal_id": rid}], ""
