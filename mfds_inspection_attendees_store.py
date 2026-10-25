"""실사자 파생 표 적재. 원문·기존 findings에는 쓰지 않는다."""
from __future__ import annotations

import json
from typing import Any

import requests

from grm_common import log
from mfds_inspection_attendees import AttendeeTable, parse_attendee_table


def raw_object(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}


def parse_record(record: dict[str, Any]) -> AttendeeTable:
    try:
        result = parse_attendee_table(raw_object(record).get("attachment_text"))
    except Exception as exc:
        # 예외 문자열에 원문·자격증명이 들어올 수 있으므로 타입만 기록한다.
        result = AttendeeTable("failed", reason=f"parser_exception:{type(exc).__name__}")
    if result.table_status == "failed":
        log("WARN", f"MFDS 실사자 표 {record.get('document_id', '')}: {result.reason}")
    return result


def replace_attendees(base_url: str, service_key: str, record: dict[str, Any],
                      result: AttendeeTable) -> str:
    """원자적 문서 교체 RPC. 성공은 빈 문자열, 실패는 안전한 코드다."""
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
               "Content-Type": "application/json"}
    body = {"p_document_id": record["document_id"],
            "p_raw_sha256": record["raw_sha256"], "p_result": result.payload()}
    for attempt in range(2):
        try:
            response = requests.post(
                f"{base_url.rstrip('/')}/rest/v1/rpc/mfds_inspection_attendees_replace",
                headers=headers, json=body, timeout=30,
            )
        except requests.exceptions.Timeout:
            if attempt == 0:
                continue
            return "timeout"
        except requests.exceptions.RequestException as exc:
            return type(exc).__name__
        if response.status_code >= 500 and attempt == 0:
            continue
        if not response.ok:
            return f"http_{response.status_code}"
        try:
            count = response.json()
        except ValueError:
            return "invalid_rpc_response"
        return "" if type(count) is int and count >= 1 else "invalid_rpc_response"
    return "retry_exhausted"


def sync_attendees(base_url: str, service_key: str, record: dict[str, Any]) -> None:
    """일일 적재의 독립 부수 경로. 표 파싱 실패도 failed 상태로 저장한다."""
    if (record.get("source"), record.get("source_kind")) != ("MFDS", "gmp-inspection"):
        return
    try:
        error = replace_attendees(base_url, service_key, record, parse_record(record))
        if error:
            log("WARN", f"MFDS 실사자 적재 {record.get('document_id', '')}: {error}")
    except Exception as exc:
        log("WARN", f"MFDS 실사자 적재 {record.get('document_id', '')}: {type(exc).__name__}")
