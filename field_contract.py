#!/usr/bin/env python3
"""수집 완전성 N2 — 소스별 "완전한 행" 계약(칸 대조).

원천 대조(source_reconcile.py)는 문서가 **있는지**만 본다. 이 모듈은 있는 문서가 **제대로** 있는지 센다.

행 판정(저장된 값만 본다 — 원천은 다시 부르지 않는다)
  · complete   — 필수 칸이 다 차 있다.
  · reasoned   — 필수 칸이 비었지만 비어 있는 이유가 근거 칸에 적혀 있다(범위 밖·원문 접근 불가 등).
  · incomplete — 필수 칸이 비었고 근거도 없다 = **결손**. N3 재수집 대상이고 N6 일보에 오른다.
  계약 밖(예: 범위 밖 483, 경보가 아닌 MHRA 블로그 글, 무관 판정 EMA)은 세지 않는다.

계약표 = 설계서(_ops/reports/GRM_수집완전성_상시장치_설계_2026-11-26.md §2 N2). 판정 함수는 수집기·감시와
공유한다(경고서한 위반항목 = collect_intake.wl_violations_gap_kind, EMA 전문 = ema_document.fulltext).
회귀 기준 = 데이터품질팀 11-26 전 기간 결산(GRM_전기간_누락결산_2026-11-26.md 부록의 select)과 같은 수.

출력: JSON(소스별 행 수·결손 수·칸별 결손·결손 번호 앞 ID_LIMIT 개). `--apply` 면 그날(KST) coverage_daily 행의
`incomplete` 칸에 쓴다(migration 172). 이 모듈은 경보를 내지 않는다 — 알리는 일은 N6 일보가 한다.
읽기는 REST GET 만, 쓰기는 coverage_daily 한 칸뿐. 서비스키는 로그에 싣지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from grm_common import (SOURCE_ECA, SOURCE_EMA, SOURCE_EU_GMP_NCR, SOURCE_FDA_483, SOURCE_FDA_WL,
                        SOURCE_MFDS, SOURCE_MHRA, SOURCE_MHRA_GMP_NCR, SOURCE_WHO)

KST = timezone(timedelta(hours=9))
ID_LIMIT = 200          # 소스별로 싣는 결손 번호 상한(N3 재수집 입력). 수는 따로 전부 센다.
HTTP_TIMEOUT = 30

COMPLETE, REASONED, INCOMPLETE = "complete", "reasoned", "incomplete"

# 계약 키 → (raw_signals.source, 사람이 읽는 이름). N6 일보가 이 순서로 줄을 낸다.
CONTRACTS: dict[str, tuple[str, str]] = {
    "fda483": (SOURCE_FDA_483, "FDA 483"),
    "fda_wl": (SOURCE_FDA_WL, "FDA 경고서한"),
    "mfds_gmp": (SOURCE_MFDS, "식약처 GMP 실사"),
    "mfds_admin": (SOURCE_MFDS, "식약처 행정처분"),
    "mfds_recall": (SOURCE_MFDS, "식약처 회수"),
    "ema": (SOURCE_EMA, "EMA"),
    "eca": (SOURCE_ECA, "ECA"),
    "mhra_alert": (SOURCE_MHRA, "MHRA 경보"),
    "who_pir": (SOURCE_WHO, "WHO 실사 보고서"),
    "eu_ncr": (SOURCE_EU_GMP_NCR, "EU GMP NCR"),
    "mhra_ncr": (SOURCE_MHRA_GMP_NCR, "MHRA GMP NCR"),
}
READ_SOURCES = tuple(sorted({src for src, _ in CONTRACTS.values()}))

MHRA_ALERT_PREFIX = "https://www.gov.uk/drug-device-alerts/"
MHRA_ALERT_FEED = "https://www.gov.uk/drug-device-alerts.atom"

# 483 본문이 빈 이유로 인정하는 표식(`fda483_text_status`). 스캔 판독 실패(`scan-no-text`)는
# 이유가 아니라 결함이다 — 재수집·판독 대상이므로 결손으로 센다.
_483_ABSENT_IN_SOURCE = "absent-in-source"
_483_OUT_OF_SCOPE = "out-of-scope"
_MFDS_ASSESSED = ("none", "present")      # none = 결함 없음 판독(근거), present = 결함 있음


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple)):
        return not value
    return False


def _obj(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _483_reason(raw: Mapping[str, Any]) -> str:
    status = str(raw.get("fda483_text_status") or "")
    if status == _483_ABSENT_IN_SOURCE:
        return "absent-in-source"
    if status.startswith("fetch-fail") and "404" in status:
        return "원문 접근 불가(404)"
    return ""


def judge(contract: str, row: Mapping[str, Any], finding: Mapping[str, Any] | None = None
          ) -> tuple[str, list[str], str] | None:
    """한 행의 판정 = (상태, 빈 필수 칸, 근거). 계약 밖이면 None. 순수 함수.

    finding: 483 만 쓴다 — 그 문서의 findings 요약 {"in_scope": bool|None, "count": int, "inspectors": bool}.
    """
    raw = _obj(row.get("raw_json"))
    kind = str(row.get("source_kind") or "")
    missing: list[str] = []
    reason = ""

    if contract == "fda483":
        if kind != "483":
            return None
        f = finding or {}
        in_scope = f.get("in_scope")
        if in_scope is False or str(raw.get("fda483_text_status") or "") == _483_OUT_OF_SCOPE:
            return None                       # 범위 밖(시설 유형) — 설계상 제외
        if in_scope is None:
            # 지적 행이 하나도 없는 문서는 findings 로 범위를 알 수 없다 → 수집기와 같은 판정 함수로 본다
            # (11-26 실측 36건: 계란·사료·혈액원 등). 범위 안인데 관찰이 없으면 아래에서 결손으로 남는다.
            from grm_taxonomy import is_out_of_scope_483
            obs = " ".join(f"{o.get('deficiency', '')} {o.get('detail', '')}"
                           for o in raw.get("fda_483_observations") or [] if isinstance(o, dict))
            if is_out_of_scope_483(str(raw.get("establishment_type") or ""), obs):
                return None
        reason = _483_reason(raw)
        if int(f.get("count") or 0) < 1 and _blank(raw.get("fda_483_observations")):
            missing.append("observations")
        if _blank(raw.get("fda483_body_full")):
            missing.append("fda483_body_full")
        if _blank(raw.get("fda483_inspectors")) and not f.get("inspectors"):
            missing.append("inspectors")
    elif contract == "fda_wl":
        from collect_intake import wl_violations_gap_kind
        if _blank(raw.get("wl_body_full")):
            missing.append("wl_body_full")
            reason = str(raw.get("wl_body_status") or "")
        elif wl_violations_gap_kind(raw) == "unmarked":
            missing.append("wl_violations")   # 위반 형식 없음 등 사유 표식이 있으면 완전
    elif contract == "mfds_gmp":
        if kind != "gmp-inspection":
            return None
        if _blank(raw.get("attachment_text")):
            missing.append("attachment_text")
        if str(raw.get("attachment_deficiency_assessment") or "") not in _MFDS_ASSESSED:
            missing.append("attachment_deficiency_assessment")
        if _blank(raw.get("gmp_deficiency_table_status")):
            missing.append("gmp_deficiency_table_status")
    elif contract == "mfds_admin":
        if kind != "admin-action":
            return None
        for key in ("ADM_DISPS_NAME", "EXPOSE_CONT"):
            if _blank(raw.get(key)):
                missing.append(key)
    elif contract == "mfds_recall":
        if kind != "recall-quality":
            return None
        if _blank(raw.get("RTRVL_RESN")):
            missing.append("RTRVL_RESN")
    elif contract == "ema":
        import ema_document
        meta = _obj(row.get("row_json"))
        if meta.get("qa_relevance") == "Unrelated":
            return None                       # 무관 판정 — 발췌 대상 아님
        if _blank(raw.get("article_excerpt")) and not ema_document.fulltext(raw).strip():
            missing.append("article_excerpt")
            doc_meta = raw.get(ema_document.META_KEY)
            if isinstance(doc_meta, dict) and doc_meta.get("status"):
                reason = str(doc_meta["status"])   # 404 등 판독 실패 사유
    elif contract == "eca":
        if _blank(raw.get("eca_article_excerpt")):
            missing.append("eca_article_excerpt")
    elif contract == "mhra_alert":
        meta = _obj(row.get("row_json"))
        url = str(row.get("official_url") or "")
        is_alert = (url.startswith(MHRA_ALERT_PREFIX) and url != MHRA_ALERT_PREFIX) \
            or meta.get("api_query") == MHRA_ALERT_FEED
        if not is_alert:
            return None                       # 블로그 글 — 경보 본문 계약 밖
        if _blank(raw.get("mhra_alert_body")):
            missing.append("mhra_alert_body")
    elif contract == "who_pir":
        doc_id = str(row.get("document_id") or "")
        if raw.get("channel") != "whopir" and not doc_id.startswith("who-whopir-"):
            return None
        if _blank(raw.get("whopir_report")):
            missing.append("whopir_report")
    elif contract in ("eu_ncr", "mhra_ncr"):
        if _blank(raw.get("ncr_nature")):
            missing.append("ncr_nature")
    else:
        raise KeyError(contract)

    if not missing:
        return COMPLETE, [], ""
    if reason:
        return REASONED, missing, reason
    return INCOMPLETE, missing, ""


def summarize_findings(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """findings(483) → raw_signal_id 별 {in_scope, count, inspectors}. 범위 = 한 건이라도 scope_status ok."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        rid = str(r.get("raw_signal_id") or "")
        if not rid:
            continue
        s = out.setdefault(rid, {"in_scope": False, "count": 0, "inspectors": False})
        s["count"] += 1
        if r.get("scope_status") == "ok":
            s["in_scope"] = True
        names = r.get("inspector_names")
        if isinstance(names, str):
            try:
                names = json.loads(names)
            except ValueError:
                names = [names] if names.strip() else []
        if isinstance(names, list) and any(isinstance(n, str) and n.strip() for n in names):
            s["inspectors"] = True
    return out


def measure(raw_rows: Iterable[Mapping[str, Any]], findings_483: Mapping[str, Mapping[str, Any]]
            ) -> dict[str, dict[str, Any]]:
    """원자료 행 → 계약별 집계. 순수 함수(시험 대상)."""
    by_source: dict[str, list[str]] = {}
    for key, (src, _label) in CONTRACTS.items():
        by_source.setdefault(src, []).append(key)
    result = {key: {"label": label, "rows": 0, COMPLETE: 0, REASONED: 0, INCOMPLETE: 0,
                    "by_field": Counter(), "reasons": Counter(), "incomplete_ids": []}
              for key, (_src, label) in CONTRACTS.items()}
    for row in raw_rows:
        for key in by_source.get(str(row.get("source") or ""), ()):
            finding = findings_483.get(str(row.get("raw_signal_id") or "")) if key == "fda483" else None
            verdict = judge(key, row, finding)
            if verdict is None:
                continue
            status, missing, reason = verdict
            agg = result[key]
            agg["rows"] += 1
            agg[status] += 1
            if status == COMPLETE:
                continue
            agg["by_field"].update(missing)
            if status == REASONED:
                agg["reasons"][reason] += 1
            elif len(agg["incomplete_ids"]) < ID_LIMIT:
                agg["incomplete_ids"].append(str(row.get("document_id") or ""))
    for agg in result.values():
        agg["by_field"] = dict(sorted(agg["by_field"].items()))
        agg["reasons"] = dict(sorted(agg["reasons"].items()))
        agg["incomplete_ids"].sort()
    return result


def ema_pending(raw_rows: Iterable[Mapping[str, Any]]) -> int:
    """EMA 관련도 미판정(Pending) 행 수 — 사람 승인 규칙이라 결손에 넣지 않고 따로 싣는다."""
    return sum(1 for r in raw_rows if str(r.get("source") or "") == SOURCE_EMA
               and _obj(r.get("row_json")).get("qa_relevance") == "Pending")


# ── 읽기·쓰기 ──────────────────────────────────────────────────────────────────

def _read_rows(base: str, key: str, session: Any = None) -> tuple[list[dict], list[dict]]:
    from content_completeness import fetch_table
    raw = list(fetch_table(base, key, "raw_signals",
                           "raw_signal_id,document_id,source,source_kind,official_url,raw_json,row_json",
                           "raw_signal_id.asc", session,
                           filters={"source": "in.(" + ",".join(f'"{s}"' for s in READ_SOURCES) + ")"}))
    finds = list(fetch_table(base, key, "findings", "finding_id,raw_signal_id,scope_status,inspector_names",
                             "finding_id.asc", session, filters={"source": f"eq.{SOURCE_FDA_483}"}))
    return raw, finds


def save(base: str, key: str, day: str, payload: dict[str, Any], session: Any = None) -> str:
    """그날 coverage_daily 행의 incomplete 칸만 바꾼다. 행이 없으면(장부가 아직 안 돌았거나 꺼져 있으면)
    장부 칸을 'not_measured' 로 표시한 행을 새로 만든다 — 나중에 장부가 같은 날을 덮어쓴다."""
    http = session or requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = f"{base.rstrip('/')}/rest/v1/coverage_daily"
    resp = http.patch(url, params={"day": f"eq.{day}"}, json={"incomplete": payload},
                      headers={**headers, "Prefer": "return=representation"}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    if resp.json():
        return "updated"
    placeholder = {"measurement_status": "not_measured"}
    resp = http.post(url, params={"on_conflict": "day"},
                     json={"day": day, "mfds_admin": placeholder, "mfds_recall": placeholder,
                           "incomplete": payload},
                     headers={**headers, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                     timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return "inserted"


def build_payload(raw: list[dict], finds: list[dict], *, now: datetime) -> dict[str, Any]:
    sources = measure(raw, summarize_findings(finds))
    return {
        "measured_at": now.isoformat(timespec="seconds"),
        "total_incomplete": sum(s[INCOMPLETE] for s in sources.values()),
        "sources": sources,
        "ema_pending_review": ema_pending(raw),
    }


def render_markdown(payload: Mapping[str, Any]) -> str:
    lines = ["## 칸 대조(N2) — 소스별 결손", "",
             "| 소스 | 행 | 완전 | 근거 있음 | **결손** | 빈 칸 |", "|---|---:|---:|---:|---:|---|"]
    for s in payload["sources"].values():
        fields = ", ".join(f"{k} {v}" for k, v in s["by_field"].items()) or "—"
        lines.append(f"| {s['label']} | {s['rows']} | {s[COMPLETE]} | {s[REASONED]} | {s[INCOMPLETE]} | {fields} |")
    lines += ["", f"결손 합계 {payload['total_incomplete']} · EMA 관련도 미판정 {payload['ema_pending_review']}"
              "(사람 승인 규칙 — 결손에 넣지 않음)"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="수집 완전성 N2 — 소스별 완전한 행 계약")
    ap.add_argument("--json", default="field-contract.json")
    ap.add_argument("--report", default="field-contract.md")
    ap.add_argument("--apply", action="store_true", help="그날 coverage_daily.incomplete 에 저장")
    args = ap.parse_args(argv)
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base.startswith("https://") or not key:
        print("SUPABASE_URL(https)/SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
        return 2
    now = datetime.now(KST)
    try:
        raw, finds = _read_rows(base, key)
    except Exception as exc:   # StoreReadError 포함 — 측정 불가는 실패로 남긴다(조용히 0 으로 닫지 않는다)
        print(f"원자료 읽기 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    payload = build_payload(raw, finds, now=now)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    report = render_markdown(payload)
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(report)
    if args.apply:
        try:
            how = save(base, key, now.date().isoformat(), payload)
        except requests.RequestException as exc:
            print(f"coverage_daily 저장 실패 ({type(exc).__name__}).", file=sys.stderr)
            return 1
        print(f"coverage_daily {now.date().isoformat()} incomplete 칸 저장({how}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
