#!/usr/bin/env python3
"""[원문 갱신 규칙 R4 2026-11-12] 매일 수집에서 원문이 고쳐졌는지 감지하고, 규칙대로 반영한다.

설계안 `GRM_원문갱신규칙_설계안_2026-11-12.md` 2절·4-1절. R0(raw_drift.py)이 "보고만" 하던 비교를
실제 갱신으로 잇는다.

대상: 수집기가 "이미 있음"으로 건너뛴 항목(heal_candidates) — 원천이 오늘 준 판을 메모리에 들고 있다.

판정(문서 하나):
  1) 오늘 판을 정본 위에 **얹는다**(merge): 오늘 판에서 값이 비지 않은 칸만 덮는다. 기보유 문서는 PDF·OCR·
     보강을 건너뛰어 덜 채운 판이 오므로, 오늘 비어 있는 칸이 좋은 값을 지우지 못하게 한다.
  2) 소스별 **본문 칸**(CONTENT_FIELDS)만 정규화해 지문(content_sha256)을 비교한다. 같으면 아무것도 안 쓴다
     — 수집 시각·보강 표식처럼 매일 흔들리는 칸으로는 판이 쌓이지 않는다.
  3) 오늘 판 본문 칸이 모두 비었으면 `no_body_today`(비교 불가 — 나빠짐이 아니다).
  4) "나빠짐"이면 갱신하지 않고 `rejected_worse` 로만 센다(설계안 2-2):
       본문 길이 합이 90% 미만 · 목록(관찰·위반·지적) 원소 수 감소 · 실패 표식 새로 생김 · 끝이 '…' 로 잘린
       관찰이 새로 생김.
  5) 그 밖이면 raw_signal_apply_version(148) 으로 바꾼다(change_kind=source_update, 옛 판 보관).
     raw_json 만 바꾼다 — row_json·제목·업체 등 열 값은 그대로 둔다(오늘 판이 덜 채운 판일 수 있다).
     MFDS 실사 문서는 실사자 표를 새 지문으로 다시 맞춘다.

지적사항(findings) — 4-1절: 지우고 다시 넣지 않는다. 옛 판·새 판을 같은 추출기로 뽑아 **같은 ordinal** 끼리
짝짓는다. 본문만 바뀐 짝(새 본문이 옛 본문으로 시작하거나 첫 줄=표제가 같음)은 WL 복원과 **같은 형식**의
restore 계획(grm-wl-truncation-restore-plan/v1)에 싣는다 → 번역 세션이 `findings_translate.py
--export-restore` 로 영문·국문을 한 PATCH 로 교체한다(옛 본문 md5 대조, finding_id 유지).
짝이 모호하거나, 새 블록에만 있거나, 옛 블록에만 있는 것은 **쓰지 않고** 사람 검토 목록에 센다.

저장 단계에서 자르지 않는다: 원문은 받은 그대로 정규화만 해서 넣는다. 보고에는 값 대신 칸 이름·길이만 싣는다.
실패는 수집을 막지 않는다(부가 장치). 끄려면 RAW_UPDATE_APPLY=false(측정 R0 은 그대로 돈다).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import requests

import findings_extractors as fe
import findings_store
import grm_findings as gf
import mfds_inspection_attendees_store as mas
import raw_versioning as rv
from grm_common import (
    SOURCE_BRAVE, SOURCE_ECA, SOURCE_EMA, SOURCE_EPR, SOURCE_EU_GMP_NCR, SOURCE_FDA_483,
    SOURCE_FDA_WL, SOURCE_FR, SOURCE_HANDOFF, SOURCE_HC, SOURCE_ICH, SOURCE_ISPE, SOURCE_MFDS,
    SOURCE_MHRA, SOURCE_MHRA_GMP_NCR, SOURCE_PICS, SOURCE_RAPS, SOURCE_RECALL, SOURCE_WHO, log,
)

SOURCE_HC_INSPECTION = "Health Canada Inspection"

# 소스별 본문 칸(설계안 2-1 초안 + 11-12 운영 DB 키 실측). 여기 없는 칸은 비교하지 않는다.
# 모든 소스를 빠짐없이 둔다 — 새 소스를 더하면 tests/test_raw_update.py 가 여기 정의를 요구한다.
_NEWS = ("title", "description", "article_excerpt", "link", "pubDate")
CONTENT_FIELDS: dict[str, tuple[str, ...]] = {
    SOURCE_FDA_483: ("fda_483_observations", "fda483_body_full", "fda483_excerpt", "firm",
                     "fei_number", "record_date", "publish_date"),
    SOURCE_FDA_WL: ("wl_body_full", "wl_body_excerpt", "wl_violations", "subject", "firm",
                    "letter_date", "posted_date", "issuing_office"),
    SOURCE_MFDS: ("attachment_text", "gmp_deficiencies", "admin_body_full", "description",
                  "EXPOSE_CONT", "RTRVL_RESN", "ADM_DISPS_NAME", "inspection_start",
                  "inspection_end", "manufacturer", "ENTP_NAME", "ITEM_NAME", "PRDUCT", "title"),
    SOURCE_HC_INSPECTION: ("hc_inspection_observations", "observation_count", "rating",
                           "ratingDesc", "measure_taken_list", "ins_outcome_list",
                           "establishment_name", "inspection_start_date"),
    SOURCE_EU_GMP_NCR: ("ncr_nature", "ncr_action", "ncr_additional", "ncr_operations",
                        "site_name", "site_address", "product_scope", "inspection_end_date",
                        "issue_date"),
    SOURCE_MHRA_GMP_NCR: ("ncr_nature", "ncr_action", "ncr_additional", "ncr_operations",
                          "site_name", "site_address", "product_scope", "inspection_end_date",
                          "issue_date"),
    SOURCE_WHO: ("whopir_report", "whopir_excerpt", "title", "inspection_dates", "pdf_url",
                 "pubDate"),
    SOURCE_FR: ("title", "abstract", "excerpts", "html_url", "publication_date", "type"),
    SOURCE_RECALL: ("reason_for_recall", "product_description", "classification", "status",
                    "recalling_firm", "code_info", "distribution_pattern", "product_quantity",
                    "recall_initiation_date", "report_date"),
    SOURCE_HC: ("Title", "Issue", "What you should do", "Product", "Category", "Recall class",
                "company", "first_published", "Last updated"),
    SOURCE_MHRA: ("title", "summary", "mhra_alert_body", "link", "published"),
    SOURCE_EMA: _NEWS,
    SOURCE_ECA: ("title", "description", "eca_article_excerpt", "link", "pubDate"),
    SOURCE_ISPE: _NEWS,
    SOURCE_PICS: _NEWS,
    SOURCE_RAPS: _NEWS,
    SOURCE_EPR: _NEWS,
    SOURCE_ICH: ("section_title", "public_page", "source_page", "page_slug", "detection"),
    SOURCE_BRAVE: ("brave_title", "brave_description", "brave_url"),
    SOURCE_HANDOFF: ("title", "description", "body"),
}

# 목록 칸: 원소 수가 줄면 나빠짐.
LIST_FIELDS = ("fda_483_observations", "wl_violations", "gmp_deficiencies",
               "hc_inspection_observations", "measure_taken_list", "excerpts")
# 실패·가림 표식 칸: 옛 판에 없던 값이 새로 생기면 나빠짐.
MARKER_FIELDS = ("fda483_text_status", "wl_body_status", "attachment_parse_error")
_SHRINK_RATIO = 0.9
ELLIPSIS_ENDINGS = ("…", "...")

RESTORE_PLAN_SCHEMA_VERSION = "grm-wl-truncation-restore-plan/v1"   # 번역 경로가 그대로 받는 형식
_FETCH_CHUNK = 40
_TIMEOUT_S = 20
MAX_APPLY_DEFAULT = 50


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _normalize(value: Any) -> Any:
    """앞뒤 공백·줄바꿈만 통일한다. 대소문자·구두점은 바꾸지 않는다(실제 정정일 수 있다)."""
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    return value


def content_fields(source: str, raw: dict[str, Any]) -> dict[str, Any]:
    fields = CONTENT_FIELDS.get(source)
    if fields is None:
        return {}
    return {k: _normalize(raw.get(k)) for k in fields if not _empty(raw.get(k))}


def content_sha256(source: str, raw: dict[str, Any]) -> str:
    return hashlib.sha256(gf.canonical_json(content_fields(source, raw)).encode("utf-8")).hexdigest()


def merge_today(stored: dict[str, Any], today: dict[str, Any]) -> dict[str, Any]:
    """정본 위에 오늘 판의 비지 않은 칸만 얹는다."""
    merged = dict(stored)
    for k, v in today.items():
        if not _empty(v):
            merged[k] = v
    return merged


def _body_len(fields: dict[str, Any]) -> int:
    return len(gf.canonical_json(fields)) if fields else 0


def _truncated_items(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    n = 0
    for o in value:
        text = o if isinstance(o, str) else (
            " ".join(str(o.get(k) or "") for k in ("deficiency", "detail", "text"))
            if isinstance(o, dict) else "")
        if text.rstrip().endswith(ELLIPSIS_ENDINGS):
            n += 1
    return n


def worse_reasons(source: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """새 판이 옛 판보다 나빠진 이유(비면 나빠지지 않음). 설계안 2-2."""
    reasons = []
    of, nf = content_fields(source, old), content_fields(source, new)
    if not nf:
        reasons.append("empty_body")
    elif _body_len(nf) < _body_len(of) * _SHRINK_RATIO:
        reasons.append("body_shrunk")
    for k in LIST_FIELDS:
        if isinstance(old.get(k), list) and len(new.get(k) or []) < len(old.get(k) or []):
            reasons.append(f"list_shrunk:{k}")
        if _truncated_items(new.get(k)) > _truncated_items(old.get(k)):
            reasons.append(f"truncated:{k}")
    for k in MARKER_FIELDS:
        if _empty(old.get(k)) and not _empty(new.get(k)):
            reasons.append(f"marker:{k}")
    return reasons


def decide(source: str, stored: dict[str, Any], today: dict[str, Any]) -> dict[str, Any]:
    """문서 하나의 판정(순수). kind: same|no_body_today|rejected_worse|update|unknown_source."""
    if source not in CONTENT_FIELDS:
        return {"kind": "unknown_source"}
    if not content_fields(source, today):
        return {"kind": "no_body_today"}
    merged = merge_today(stored, today)
    old_sha, new_sha = content_sha256(source, stored), content_sha256(source, merged)
    if old_sha == new_sha:
        return {"kind": "same"}
    reasons = worse_reasons(source, stored, merged)
    of, nf = content_fields(source, stored), content_fields(source, merged)
    changed = sorted(k for k in set(of) | set(nf) if gf.canonical_json(of.get(k)) != gf.canonical_json(nf.get(k)))
    summary = {"changed_fields": changed, "body_len_old": _body_len(of), "body_len_new": _body_len(nf),
               "old_content_sha256": old_sha}
    for k in LIST_FIELDS:
        if isinstance(stored.get(k), list) or isinstance(merged.get(k), list):
            summary[f"{k}_count"] = [len(stored.get(k) or []), len(merged.get(k) or [])]
    if reasons:
        return {"kind": "rejected_worse", "reasons": reasons, "diff_summary": summary}
    return {"kind": "update", "merged": merged, "content_sha256": new_sha, "diff_summary": summary}


# ---------------------------------------------------------------------------
# 지적사항 짝짓기(순수)
# ---------------------------------------------------------------------------

def _head(text: str) -> str:
    return text.strip().split("\n", 1)[0].strip()


def _strip_ellipsis(text: str) -> str:
    t = text.rstrip()
    for e in ELLIPSIS_ENDINGS:
        if t.endswith(e):
            return t[:-len(e)].rstrip()
    return t


def pair_findings(old_extracted: list[dict[str, Any]], new_extracted: list[dict[str, Any]],
                  live: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """같은 ordinal(추출 순서) 끼리 짝짓는다. live = DB 에 있는 finding_id → 행.

    반환: restore(본문만 바뀐 짝, 계획 항목) · same · ambiguous · new_only · old_only · not_live 건수.
    """
    out: dict[str, Any] = {"restore": [], "same": 0, "ambiguous": 0, "new_only": 0,
                           "old_only": 0, "not_live": 0}
    targets: Counter[str] = Counter()
    proposals = []
    for i, old in enumerate(old_extracted):
        fid = str(old.get("finding_id") or "")
        row = live.get(fid)
        if row is None:
            out["not_live"] += 1          # 범위 밖·숨김 등으로 DB 에 없는 블록 — 건드리지 않는다
            continue
        if i >= len(new_extracted):
            out["old_only"] += 1
            continue
        old_text = str(row.get("finding_text") or "")
        new_text = str(new_extracted[i].get("finding_text") or "")
        if old_text == new_text:
            out["same"] += 1
            continue
        prefix = _strip_ellipsis(old_text)
        if (prefix and new_text.startswith(prefix)) or (_head(old_text) and _head(old_text) == _head(new_text)):
            proposals.append((row, i, old_text, new_text))
            targets[new_text] += 1
        else:
            out["ambiguous"] += 1
    for row, _i, old_text, new_text in proposals:
        if targets[new_text] > 1:
            out["ambiguous"] += 1
            continue
        out["restore"].append({
            "finding_id": str(row.get("finding_id") or ""),
            "raw_signal_id": str(row.get("raw_signal_id") or ""),
            "published_date": str(row.get("published_date") or ""),
            "finding_text_old_md5": hashlib.md5(old_text.encode("utf-8")).hexdigest(),
            "finding_text": new_text,
            "old_chars": len(old_text),
            "new_chars": len(new_text),
            "legacy_match": False,
        })
    out["new_only"] = max(0, len(new_extracted) - len(old_extracted))
    return out


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def _get_rows(base_url: str, service_key: str, table: str, select: str, key: str, ids: list[str],
              *, get: Callable[..., Any]) -> list[dict[str, Any]] | None:
    base = (base_url or "").rstrip("/")
    if not base.startswith("https://"):
        return None
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    out: list[dict[str, Any]] = []
    uniq = sorted({i for i in ids if i})
    for start in range(0, len(uniq), _FETCH_CHUNK):
        chunk = uniq[start:start + _FETCH_CHUNK]
        params = {"select": select, key: "in.(" + ",".join(chunk) + ")"}
        try:
            resp = get(f"{base}/rest/v1/{table}", params=params, headers=headers, timeout=_TIMEOUT_S)
        except requests.exceptions.RequestException:
            return None
        if getattr(resp, "status_code", 500) >= 400:
            return None
        try:
            rows = resp.json()
        except ValueError:
            return None
        out.extend(r for r in (rows or []) if isinstance(r, dict))
    return out


def _json_obj(text: Any) -> dict[str, Any] | None:
    try:
        v = json.loads(text) if isinstance(text, str) else None
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def apply_updates(base_url: str, service_key: str, items: Iterable[Any], *,
                  collected_at: Any = "", max_apply: int = MAX_APPLY_DEFAULT, dry_run: bool = False,
                  changed_by: str = "", get: Callable[..., Any] | None = None,
                  apply_fn: Callable[..., dict[str, Any]] | None = None,
                  sync_fn: Callable[[str, str, dict[str, Any]], None] | None = None,
                  extract: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
                  now: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """건너뛴 항목의 오늘 판으로 정본을 갱신한다. 반환: (요약, restore 계획)."""
    get = get or requests.get
    apply_fn = apply_fn or rv.apply_version
    sync_fn = sync_fn or mas.sync_attendees
    extract = extract or fe.findings_from_raw_signal
    changed_by = changed_by or rv.run_label("raw_update")
    summary: dict[str, Any] = {"dry_run": dry_run, "candidates": 0, "lookup_failed": 0,
                               "missing_in_db": 0, "applied": 0, "would_apply": 0, "capped": 0,
                               "conflict": 0, "errors": 0, "attendees_resynced": 0,
                               "by_source": {}, "findings": Counter(), "rejected_examples": []}
    plan_items: list[dict[str, Any]] = []
    plan = {"schema_version": RESTORE_PLAN_SCHEMA_VERSION, "report": {}, "items": plan_items}

    today_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for item in items:
        raw = getattr(item, "raw_payload", None)
        if not isinstance(raw, dict) or not getattr(item, "document_id", ""):
            continue
        rid = str(findings_store.raw_signal_from_intake_item(
            item, collected_at=collected_at).get("raw_signal_id") or "")
        if rid and rid not in today_by_id:
            today_by_id[rid] = (str(item.source), raw)
    summary["candidates"] = len(today_by_id)
    if not today_by_id:
        return _finish(summary, plan, now)
    stored_rows = _get_rows(base_url, service_key, "raw_signals", "*", "raw_signal_id",
                            list(today_by_id), get=get)
    if stored_rows is None:
        summary["lookup_failed"] = 1
        log("WARN", f"원문 갱신(R4): 정본 조회 실패 — 이번 실행은 갱신 생략(후보 {len(today_by_id)}건)")
        return _finish(summary, plan, now)
    stored_by_id = {str(r.get("raw_signal_id")): r for r in stored_rows}

    per: dict[str, Counter[str]] = defaultdict(Counter)
    updates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for rid, (source, today) in sorted(today_by_id.items()):
        row = stored_by_id.get(rid)
        stored = _json_obj((row or {}).get("raw_json"))
        if row is None or stored is None:
            summary["missing_in_db"] += 1
            continue
        d = decide(source, stored, today)
        per[source][d["kind"]] += 1
        if d["kind"] == "rejected_worse":
            for reason in d["reasons"]:
                per[source][f"worse:{reason.split(':')[0]}"] += 1
            if len(summary["rejected_examples"]) < 5:
                summary["rejected_examples"].append({
                    "source": source, "document_id": str(row.get("document_id") or ""),
                    "reasons": d["reasons"], "changed_fields": d["diff_summary"]["changed_fields"][:8]})
        elif d["kind"] == "update":
            updates.append((rid, row, d))

    if len(updates) > max_apply:
        summary["capped"] = len(updates) - max_apply
        updates = updates[:max_apply]

    applied_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for rid, row, d in updates:
        text, sha = rv.canonical_raw(d["merged"])
        if dry_run:
            summary["would_apply"] += 1
            applied_pairs.append((row, dict(row, raw_json=text, raw_sha256=sha)))
            continue
        try:
            res = apply_fn(base_url, service_key, raw_signal_id=rid,
                           expected_raw_sha256=str(row.get("raw_sha256") or ""),
                           record={"raw_json": text, "raw_sha256": sha,
                                   "row_json": str(row.get("row_json") or "") or "{}"},
                           change_kind="source_update", changed_by=changed_by,
                           content_sha256=d["content_sha256"], diff_summary=d["diff_summary"])
        except Exception as exc:  # noqa: BLE001 — 건별 실패는 넘긴다
            res = {"status": "error", "error": type(exc).__name__}
        status = str(res.get("status") or "error")
        if status == "conflict":
            summary["conflict"] += 1
            continue
        if status not in ("applied", "unchanged"):
            summary["errors"] += 1
            continue
        summary["applied"] += 1
        new_row = dict(row, raw_json=text, raw_sha256=sha)
        if (row.get("source"), row.get("source_kind")) == (SOURCE_MFDS, "gmp-inspection"):
            try:
                sync_fn(base_url, service_key, new_row)
                summary["attendees_resynced"] += 1
            except Exception as exc:  # noqa: BLE001
                log("WARN", f"원문 갱신(R4) 실사자 재동기화 실패 {row.get('document_id')}: {type(exc).__name__}")
        applied_pairs.append((row, new_row))

    # 지적사항: 옛 판·새 판 재추출 → 같은 ordinal 짝 → restore 계획(쓰지 않음)
    if applied_pairs:
        ids = [str(r.get("raw_signal_id")) for r, _ in applied_pairs]
        live_rows = _get_rows(base_url, service_key, "findings",
                              "finding_id,raw_signal_id,finding_text,published_date",
                              "raw_signal_id", ids, get=get)
        if live_rows is None:
            summary["findings"]["lookup_failed"] += 1
        else:
            live = {str(r.get("finding_id")): r for r in live_rows}
            for old_row, new_row in applied_pairs:
                try:
                    pairs = pair_findings(extract(old_row), extract(new_row), live)
                except Exception as exc:  # noqa: BLE001
                    summary["findings"][f"extract_raised:{type(exc).__name__}"] += 1
                    continue
                plan_items.extend(pairs.pop("restore"))
                summary["findings"].update({k: v for k, v in pairs.items() if v})
    summary["findings"]["restore"] = len(plan_items)
    summary["by_source"] = {s: dict(c) for s, c in sorted(per.items())}
    for s, c in summary["by_source"].items():
        log("INFO", f"원문 갱신(R4) {s}: " + " · ".join(f"{k} {v}" for k, v in sorted(c.items())))
    log("INFO", f"원문 갱신(R4){' [DRY]' if dry_run else ''}: 반영 {summary['applied'] or summary['would_apply']} · "
                f"상한 초과 {summary['capped']} · 충돌 {summary['conflict']} · 오류 {summary['errors']} · "
                f"지적 restore {len(plan_items)} · 검토 필요 "
                f"{sum(summary['findings'].get(k, 0) for k in ('ambiguous', 'new_only', 'old_only'))}")
    return _finish(summary, plan, now)


def _finish(summary: dict[str, Any], plan: dict[str, Any], now: datetime | None
            ) -> tuple[dict[str, Any], dict[str, Any]]:
    summary["findings"] = dict(summary["findings"])
    items = plan["items"]
    items.sort(key=lambda it: (it["published_date"], it["finding_id"]), reverse=True)
    plan["report"] = {
        "schema_version": RESTORE_PLAN_SCHEMA_VERSION,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "origin": "raw_update_r4",
        "mapped": len(items),
        "new_chars_total": sum(it["new_chars"] for it in items),
        "old_chars_total": sum(it["old_chars"] for it in items),
    }
    return summary, plan
