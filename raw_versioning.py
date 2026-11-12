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
                  post: Callable[..., Any] = requests.post) -> dict[str, Any]:
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
