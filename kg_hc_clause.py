#!/usr/bin/env python3
"""Health Canada 지적 ↔ 근거 조항(C.02.xxx) 연결 — 원문(raw_signals.raw_json)이 관찰마다 적어 둔 조항을 옮긴다.

원천: raw_json 의 `hc_inspection_observations[] = {no, regulation: "C.02.015 - Quality control department", summary}`.
findings.finding_text 는 summary 와 같은 문장이므로(공백 정리 후 일치) 같은 문서(raw_signal_id) 안에서 문장으로 짝을 짓는다.
- 추측 없음: 조항 번호가 정규식에 맞고 문장이 정확히 짝지어질 때만 잇는다. 못 이은 것은 세어 보고한다.
- 같은 문서 안에서 같은 문장이 서로 다른 조항 아래 적혀 있으면(findings 가 문장으로 합쳐진 경우) 둘 다 잇되 신뢰도 0.9.
- 키 형식은 113 의 `Canada GUI-0080` 과 같은 계열: `Canada C.02.015`. basis=`hc_regulation_raw`(되돌림 표식).
- 기본 꺼짐: 일일 연결(kg_link_new.py)에서 GRM_KG_HC_CLAUSE=1 일 때만 돈다. DB 쓰기는 PostgREST ignore-duplicates(멱등, 기존 행 불변).
서비스 키는 로그에 넣지 않는다.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

import requests

HC_SOURCE = "Health Canada Inspection"
BASIS = "hc_regulation_raw"
KEY_PREFIX = "Canada "
CONF_SINGLE = 0.95
CONF_MULTI = 0.9
PAGE = 1000
BATCH = 500
IN_CHUNK = 100

_REG = re.compile(r"^\s*(C\.\d{2}[A-Z]?\.\d{3})\b")
_WS = re.compile(r"\s+")


def norm_text(s: str | None) -> str:
    return _WS.sub(" ", (s or "").strip()).lower()


def parse_regulation(reg: str | None) -> str | None:
    """'C.02.015 - Quality control department' → 'C.02.015'. 형식 밖이면 None(추측 금지)."""
    m = _REG.match(reg or "")
    return m.group(1) if m else None


def parse_observations(raw_json: str | None) -> list[tuple[str, str, str]]:
    """raw_json → [(정규화 summary, 조항 번호, 원 regulation 문자열)]. 형식 밖 관찰은 건너뛴다."""
    if not raw_json or not raw_json.lstrip().startswith("{"):
        return []
    try:
        doc = json.loads(raw_json)
    except ValueError:
        return []
    obs = doc.get("hc_inspection_observations") if isinstance(doc, dict) else None
    out: list[tuple[str, str, str]] = []
    if not isinstance(obs, list):
        return out
    for o in obs:
        if not isinstance(o, dict):
            continue
        k = parse_regulation(o.get("regulation"))
        s = norm_text(o.get("summary"))
        if k and s:
            out.append((s, k, str(o.get("regulation")).strip()))
    return out


def build_edges(findings: Iterable[dict[str, Any]], raw_by_doc: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """findings [{finding_id, raw_signal_id, finding_text}] × raw_json → finding_clause 행 + 통계."""
    obs_cache: dict[str, dict[str, dict[str, str]]] = {}
    edges: list[dict[str, Any]] = []
    stats = {"findings": 0, "matched": 0, "multi_key": 0, "unmatched": 0, "no_raw": 0}
    for f in findings:
        stats["findings"] += 1
        doc = f.get("raw_signal_id")
        if doc not in raw_by_doc:
            stats["no_raw"] += 1
            stats["unmatched"] += 1
            continue
        if doc not in obs_cache:
            by_text: dict[str, dict[str, str]] = {}
            for s, k, reg in parse_observations(raw_by_doc[doc]):
                by_text.setdefault(s, {}).setdefault(k, reg)
            obs_cache[doc] = by_text
        keys = obs_cache[doc].get(norm_text(f.get("finding_text")))
        if not keys:
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1
        conf = CONF_SINGLE if len(keys) == 1 else CONF_MULTI
        if len(keys) > 1:
            stats["multi_key"] += 1
        for k in sorted(keys):
            edges.append({"finding_id": f["finding_id"], "citation_key": KEY_PREFIX + k, "citation_paragraph": "",
                          "confidence": conf, "basis": BASIS, "raw_ref": keys[k]})
    return edges, stats


def _headers(key: str, **extra: str) -> dict[str, str]:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", **extra}


def _call(fn):
    try:
        return fn()
    except requests.RequestException as exc:
        raise RuntimeError(f"요청 실패: {type(exc).__name__}") from None


def fetch_findings(base: str, key: str, since_iso: str, timeout: float = 120.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        resp = _call(lambda: requests.get(
            f"{base}/rest/v1/findings", headers=_headers(key, Range=f"{offset}-{offset + PAGE - 1}"), timeout=timeout,
            params={"select": "finding_id,raw_signal_id,finding_text", "source": f"eq.{HC_SOURCE}",
                    "ingested_at": f"gte.{since_iso}", "order": "finding_id.asc"}))
        if resp.status_code not in (200, 206):
            raise RuntimeError(f"findings HTTP {resp.status_code}")
        page = resp.json()
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def fetch_raw(base: str, key: str, doc_ids: list[str], timeout: float = 120.0) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(doc_ids), IN_CHUNK):
        chunk = doc_ids[i:i + IN_CHUNK]
        resp = _call(lambda: requests.get(
            f"{base}/rest/v1/raw_signals", headers=_headers(key), timeout=timeout,
            params={"select": "raw_signal_id,raw_json", "raw_signal_id": "in.(" + ",".join(chunk) + ")"}))
        if resp.status_code != 200:
            raise RuntimeError(f"raw_signals HTTP {resp.status_code}")
        for r in resp.json():
            out[r["raw_signal_id"]] = r.get("raw_json") or ""
    return out


def write_edges(base: str, key: str, edges: list[dict[str, Any]], timeout: float = 120.0) -> int | None:
    """finding_clause 에 멱등 삽입(기존 행 불변). 표 미적용(404)이면 None."""
    sent = 0
    for i in range(0, len(edges), BATCH):
        chunk = edges[i:i + BATCH]
        resp = _call(lambda: requests.post(
            f"{base}/rest/v1/finding_clause",
            headers=_headers(key, Prefer="resolution=ignore-duplicates,return=minimal"),
            params={"on_conflict": "finding_id,citation_key,citation_paragraph"}, json=chunk, timeout=timeout))
        if resp.status_code == 404:
            return None
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(f"finding_clause HTTP {resp.status_code}")
        sent += len(chunk)
    return sent


def link_recent(base: str, key: str, since_iso: str) -> tuple[dict[str, Any], int | None]:
    findings = fetch_findings(base, key, since_iso)
    docs = sorted({f["raw_signal_id"] for f in findings if f.get("raw_signal_id")})
    raw = fetch_raw(base, key, docs)
    edges, stats = build_edges(findings, raw)
    sent = write_edges(base, key, edges) if edges else 0
    return stats, sent
