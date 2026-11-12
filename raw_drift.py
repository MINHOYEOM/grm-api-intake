#!/usr/bin/env python3
"""[원문 갱신 규칙 R0 2026-11-12] 이미 받은 문서의 '오늘 판'을 정본과 비교해 **보고만** 한다.

왜: 대표 결정(11-12) "원문이 고쳐졌을 때 DB 를 갱신하는 규칙을 노션 전환 전에 만든다".
    규칙(설계안 `GRM_원문갱신규칙_설계안_2026-11-12.md`)은 소스별 "본문 칸"만 비교하고, 새 판이
    나빠지지 않았을 때만 갱신한다. 그런데 어떤 칸이 매일 흔들리는지(수집 시각·보강 표식 등), 실제
    정정이 얼마나 잦은지는 아무도 재 본 적이 없다. 이 모듈이 그 측정을 한다.

무엇을: 수집기가 "이미 Notion 에 있음"으로 건너뛴 항목(heal_candidates)은 그날 원천이 준 판을
    메모리에 들고 있다. 그 raw 를 Supabase raw_signals.raw_json 과 정규화(canonical_json) 비교한다.
    소스별로 같음/다름 건수, 다른 최상위 칸 이름별 건수, 크기 변화(줄어듦·늘어남), 목록 칸 원소 수
    감소를 센다.

쓰지 않는다: DB·Notion·파일 어디에도 쓰지 않는다. 결과는 health JSON `store.drift` 와 로그뿐이다.
    실패(조회 오류·예외)는 수집을 막지 않는다 — 측정이 빠질 뿐이다.

비교 기준은 정본에 저장된 `raw_json` 자체다(`raw_sha256` 이 아니다 — 483·MFDS 2,385행은 지문이
    내용과 어긋나 있다, 11-12 실측).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

import requests

import findings_store
import grm_findings as gf
from grm_common import log

_FETCH_CHUNK = 40          # raw_json 이 커서(483 평균 1만 자) 한 번에 적게 받는다
_TIMEOUT_S = 20
_MAX_ITEMS_DEFAULT = 400   # 하루 측정 상한 — 수집 시간을 늘리지 않게
_SHRINK_RATIO = 0.9        # 설계안 2-2 의 '나빠짐' 기준과 같은 값
_TOP_KEYS = 12
_EXAMPLES = 3


def _canon(value: Any) -> str:
    return gf.canonical_json(value)


def fetch_stored_raw(base_url: str, service_key: str, raw_signal_ids: list[str], *,
                     get: Callable[..., Any] = requests.get) -> dict[str, dict[str, Any]] | None:
    """raw_signal_id → 저장된 raw dict. 하나라도 실패하면 None(부분 결과로 판정하지 않는다)."""
    base = (base_url or "").rstrip("/")
    if not base.startswith("https://"):
        return None
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    out: dict[str, dict[str, Any]] = {}
    ids = sorted({i for i in raw_signal_ids if i})
    for start in range(0, len(ids), _FETCH_CHUNK):
        chunk = ids[start:start + _FETCH_CHUNK]
        params = {"select": "raw_signal_id,raw_json", "raw_signal_id": "in.(" + ",".join(chunk) + ")"}
        try:
            resp = get(f"{base}/rest/v1/raw_signals", params=params, headers=headers,
                       timeout=_TIMEOUT_S)
        except requests.exceptions.RequestException:
            return None
        if getattr(resp, "status_code", 500) >= 400:
            return None
        try:
            rows = resp.json()
        except ValueError:
            return None
        for row in rows or []:
            rid = str(row.get("raw_signal_id") or "")
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, ValueError):
                raw = None
            if rid and isinstance(raw, dict):
                out[rid] = raw
    return out


def compare_raw(stored: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """두 raw 판의 차이 요약(값은 싣지 않는다 — 칸 이름·크기만)."""
    keys = sorted(set(stored) | set(new))
    changed = [k for k in keys if _canon(stored.get(k)) != _canon(new.get(k))]
    size_old = len(_canon(stored))
    size_new = len(_canon(new))
    list_shrunk = [k for k in keys
                   if isinstance(stored.get(k), list)
                   and len(new.get(k) or []) < len(stored.get(k) or [])]
    return {
        "same": not changed,
        "changed_keys": changed,
        "added_keys": [k for k in keys if k not in stored],
        "dropped_keys": [k for k in keys if k not in new],
        "size_old": size_old,
        "size_new": size_new,
        "shrunk": size_new < size_old * _SHRINK_RATIO,
        "list_shrunk_keys": list_shrunk,
    }


def measure_drift(base_url: str, service_key: str, items: Iterable[Any], *,
                  collected_at: Any = "", max_items: int = _MAX_ITEMS_DEFAULT,
                  fetch: Callable[[str, str, list[str]], dict[str, dict[str, Any]] | None]
                  = fetch_stored_raw) -> dict[str, Any]:
    """건너뛴 항목의 오늘 판을 정본과 비교한 요약. 쓰기 없음."""
    summary: dict[str, Any] = {"measured": 0, "missing_in_db": 0, "capped": 0,
                               "lookup_failed": 0, "by_source": {}}
    by_id: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for item in items:
        raw = getattr(item, "raw_payload", None)
        if not isinstance(raw, dict) or not getattr(item, "document_id", ""):
            continue
        rid = str(findings_store.raw_signal_from_intake_item(
            item, collected_at=collected_at).get("raw_signal_id") or "")
        if rid and rid not in by_id:
            by_id[rid] = (str(item.source), str(item.document_id), raw)
    if len(by_id) > max_items:
        summary["capped"] = len(by_id) - max_items
        by_id = dict(sorted(by_id.items())[:max_items])
    if not by_id:
        return summary
    stored = fetch(base_url, service_key, list(by_id))
    if stored is None:
        summary["lookup_failed"] = 1
        log("WARN", f"원문 비교(R0): 정본 조회 실패 — 이번 실행은 측정 생략(후보 {len(by_id)}건)")
        return summary

    per: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "compared": 0, "same": 0, "differ": 0, "shrunk": 0, "grew": 0, "list_shrunk": 0,
        "keys": Counter(), "examples": []})
    for rid, (source, doc_id, raw) in sorted(by_id.items()):
        old = stored.get(rid)
        if old is None:
            summary["missing_in_db"] += 1
            continue
        d = compare_raw(old, raw)
        s = per[source]
        s["compared"] += 1
        summary["measured"] += 1
        if d["same"]:
            s["same"] += 1
            continue
        s["differ"] += 1
        s["keys"].update(d["changed_keys"])
        if d["shrunk"]:
            s["shrunk"] += 1
        elif d["size_new"] > d["size_old"]:
            s["grew"] += 1
        if d["list_shrunk_keys"]:
            s["list_shrunk"] += 1
        if len(s["examples"]) < _EXAMPLES:
            s["examples"].append({"document_id": doc_id, "changed_keys": d["changed_keys"][:8],
                                  "size_old": d["size_old"], "size_new": d["size_new"]})
    for source, s in per.items():
        s["keys"] = dict(s["keys"].most_common(_TOP_KEYS))
        summary["by_source"][source] = s
        log("INFO", f"원문 비교(R0) {source}: 비교 {s['compared']} · 같음 {s['same']} · "
                    f"다름 {s['differ']}(줄어듦 {s['shrunk']} · 늘어남 {s['grew']} · "
                    f"목록 감소 {s['list_shrunk']}) · 흔들린 칸 {list(s['keys'])[:6]}")
    return summary
