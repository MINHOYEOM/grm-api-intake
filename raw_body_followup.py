#!/usr/bin/env python3
"""[본문 교체 후속 2026-11-24] 원문 본문이 바뀐 행의 분류(row_json)·지적사항(findings)을 다시 맞춘다.

## 왜 (2026-11-24)

WL 본문 재수집(`backfill_wl_body_recovery --rebuild-body-start`)이 247행의 본문을 더 긴 정확한 본문으로
바꿨다. 그 도구는 행 전체를 `build_wl_item` 으로 다시 만들어 row_json(관련도·tier)은 함께 바뀌었지만
(Tier 1→Tier 3 34행 저장 확인), findings 는 M12 가 "findings 없는 행"만 보므로 **옛 본문 기준 그대로**
남았다. 매일 원문 갱신(R4 `raw_update`)은 반대로 row_json 을 옛 값 그대로 두었다(본문은 바뀌는데 tier 는
옛 본문 판정). 본문을 바꾸는 길마다 후속 처리가 제각각이었다.

## 규칙 — 본문을 바꾸는 모든 길은 이 모듈을 부른다

1. **분류** `reclassified_row_json` — 일일 수집과 **같은 함수**(`collect_intake.build_wl_item` →
   `findings_store.raw_signal_from_intake_item`)로 새 원문의 분류를 다시 낸다. 바꾸는 칸은 분류 칸
   (`CLASSIFICATION_KEYS`)뿐이고 row_json 의 다른 칸은 그대로 둔다. 재구성 함수가 없는 소스는 None(그대로).
   row_json 은 원문과 **같은 호출**(raw_signal_apply_version)로 바뀌어야 한다 — DB 함수는 원문이 같으면
   'unchanged' 로 돌아와 row_json 만 따로 고칠 길이 없다. 그래서 쓰는 쪽이 쓰기 전에 이 함수를 부른다.
2. **지적사항** `plan_findings` — 옛 판·새 판을 같은 추출기(`findings_extractors.findings_from_raw_signal`,
   일일 적재·M12 와 같은 함수)로 뽑는다.
     · 본문만 바뀐 짝(같은 순번, 새 글이 옛 글로 시작하거나 첫 줄이 같음) → restore 계획
       (`raw_update.pair_findings`, grm-wl-truncation-restore-plan/v1). finding_id·국문·검수 결정은 번역 경로가
       옛 md5 를 대조한 뒤 영문·국문을 한 PATCH 로 바꾼다 — 여기서는 쓰지 않는다.
     · 새 본문에만 있는 글 → 일일 적재와 같은 삽입 함수(`findings_supabase_append._append_findings_batch`)로
       넣는다. needs_review·미번역으로 들어가 공개 게이트가 숨기고, 자동 승격·번역 루프가 이어받는다.
     · 옛 본문에만 있는 행(stale) → **건드리지 않는다**(검수·번역은 사람·LLM 결정일 수 있다). 목록만 남긴다.
   기존 findings 행은 어떤 칸도 PATCH 하지 않는다 — 사람이 정한 review_status·finding_text_ko·scope_status 보존.
3. **옛 덩어리 숨김** `plan_supersede` (166, `--supersede`) — 옛 본문에만 남은 행(stale)은 지우지 않고, 같은
   문서의 새 글이 **모두 들어가 정리(번역·반려·범위 밖 판정)됐고 하나 이상 공개된** 경우에만 되돌릴 수 있는
   '대체됨'(scope_status='superseded')으로 숨긴다. 지금 공개 중(scope ok)인 행만 대상이고, 숨기기 전 값은
   findings_supersessions 에 남는다. DB 함수(grm_findings_supersede)가 같은 조건을 다시 확인한다 — 둘이 어긋나면
   (DB 가 건너뜀) `--check` 가 막는다. migration 166 적용 전에는 'supersede_unavailable' 경고만 낸다.
4. **따라잡기·검사** `main --since-days N` — 모든 원문 교체는 raw_signal_versions 에 옛 판을 남긴다(R3 ·
   migration 150 이 다른 길을 막는다). 그 기록으로 본문이 바뀐 행을 찾아 2를 다시 돌린다(멱등: 넣은 행은
   다음에 '있음'). `--check` 는 할 일이 남으면(분류가 새 본문 판정과 다름 · 넣어야 할 findings 가 남음) exit 1.

저장 단계에서 자르지 않는다: 본문·지적 글은 추출기가 낸 그대로 넣는다. 보고에는 길이·개수·id 만 싣는다.
exit: 자격증명·조회 실패 2 · `--check` 미해소 또는 쓰기 실패 1 · 그 외 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import findings_extractors as fe
import findings_store
import grm_findings as gf
from grm_common import SOURCE_FDA_WL, log

SCHEMA_VERSION = "grm-raw-body-followup/v1"
# 분류 칸 — 본문이 바뀌면 다시 계산한다. 그 밖의 row_json 칸(제목·URL·국가 보강 등)은 그대로 둔다.
CLASSIFICATION_KEYS = ("qa_relevance", "osd_relevance", "signal_tier", "modality")
# 소스별 본문 칸 — 이 칸이 바뀐 판만 후속 대상이다.
BODY_FIELDS: dict[str, tuple[str, ...]] = {
    SOURCE_FDA_WL: ("wl_body_full", "wl_body_excerpt"),
}
_FETCH_CHUNK = 40
_TIMEOUT_S = 30
_PAGE_SIZE = 200          # WL 원문이 커서(행당 ~15KB) 작게 받는다


# ---------------------------------------------------------------------------
# 분류 — 일일 수집과 같은 함수
# ---------------------------------------------------------------------------

def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def rebuild_wl_item(raw: dict[str, Any], *, excerpt: tuple[str, str] | None = None,
                    full: tuple[str, str] | None = None, record_tier: bool = True) -> Any:
    """저장 raw (+ 새 본문) → 라이브 수집과 **같은 함수**(`build_wl_item`)로 IntakeItem.

    excerpt/full 을 주지 않으면 저장 raw 의 본문·사유를 그대로 넣는다(분류 재계산용).
    """
    from collect_intake import build_wl_item

    def _s(key: str) -> str:
        return str(raw.get(key) or "")

    if excerpt is None:
        excerpt = (_s("wl_body_excerpt"), _s("wl_body_status"))
    if full is None:
        full = (_s("wl_body_full"), _s("wl_body_status"))
    return build_wl_item(
        firm=_s("firm"), posted_raw=_s("posted_date"), letter_date_raw=_s("letter_date"),
        issuing_office=_s("issuing_office"), subject=_s("subject"), wl_href=_s("url"),
        office_verdict=_s("office_gate_verdict"), excerpt=excerpt, full=full,
        record_tier=record_tier,
    )


def _wl_classification(raw: dict[str, Any]) -> dict[str, Any]:
    item = rebuild_wl_item(raw, record_tier=False)
    row = _json_object(findings_store.raw_signal_from_intake_item(item).get("row_json"))
    return {k: row.get(k, "") for k in CLASSIFICATION_KEYS}


# 소스 → 새 원문의 분류 칸(일일 수집 함수 경유). 여기 없는 소스는 분류를 다시 내지 않는다.
CLASSIFIERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    SOURCE_FDA_WL: _wl_classification,
}


def classification_of(row_json: Any) -> dict[str, Any]:
    row = _json_object(row_json)
    return {k: row.get(k, "") for k in CLASSIFICATION_KEYS}


def reclassified_row_json(source: str, raw: dict[str, Any], row_json: Any) -> str | None:
    """새 원문 기준 row_json(분류 칸만 교체, 나머지 그대로). 재구성 함수가 없으면 None."""
    fn = CLASSIFIERS.get(source)
    if fn is None:
        return None
    row = dict(_json_object(row_json))
    row.update(fn(raw))
    return gf.canonical_json(row)


def body_changed(source: str, old_raw: dict[str, Any], new_raw: dict[str, Any]) -> bool:
    """본문 칸이 바뀌었나. BODY_FIELDS 에 없는 소스는 R4 의 소스별 본문 칸(`raw_update.CONTENT_FIELDS`)으로 본다."""
    fields = BODY_FIELDS.get(source)
    if fields:
        return any(str(old_raw.get(k) or "") != str(new_raw.get(k) or "") for k in fields)
    import raw_update
    if source not in raw_update.CONTENT_FIELDS:
        return False
    return raw_update.content_sha256(source, old_raw) != raw_update.content_sha256(source, new_raw)


# ---------------------------------------------------------------------------
# 지적사항 — 같은 추출기, 기존 행은 쓰지 않는다
# ---------------------------------------------------------------------------

def _md5(text: str) -> str:
    return hashlib.md5(str(text or "").encode("utf-8")).hexdigest()


def plan_findings(old_signal: dict[str, Any], new_signal: dict[str, Any],
                  live_rows: list[dict[str, Any]], *,
                  extract: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
                  ) -> dict[str, Any]:
    """한 raw_signal 의 옛 판·새 판 → {restore, insert, stale, counts}. 순수(네트워크 없음).

    live_rows = 그 raw_signal 의 현재 findings 행(finding_id·finding_text·published_date·raw_signal_id).
    """
    import raw_update   # 순환 import 방지(raw_update 가 이 모듈을 부른다)

    extract = extract or fe.findings_from_raw_signal
    old_ex = extract(old_signal) if old_signal else []
    new_ex = extract(new_signal)
    live_by_id = {str(r.get("finding_id") or ""): r for r in live_rows}
    pairs = raw_update.pair_findings(old_ex, new_ex, live_by_id)
    restore = pairs.pop("restore")
    restore_src = {it["finding_id"] for it in restore}
    restore_dst = {_md5(it["finding_text"]) for it in restore}
    live_md5 = {_md5(str(r.get("finding_text") or "")) for r in live_rows}
    insert, seen = [], set()
    for f in new_ex:
        digest = _md5(str(f.get("finding_text") or ""))
        if digest in live_md5 or digest in restore_dst or digest in seen:
            continue
        seen.add(digest)
        insert.append(f)
    new_md5 = {_md5(str(f.get("finding_text") or "")) for f in new_ex}
    stale = sorted(str(r.get("finding_id") or "") for r in live_rows
                   if _md5(str(r.get("finding_text") or "")) not in new_md5
                   and str(r.get("finding_id") or "") not in restore_src)
    return {"restore": restore, "insert": insert, "stale": stale, "new_extracted": new_ex,
            "counts": {"live": len(live_rows), "extracted_new": len(new_ex),
                       "same_text": sum(1 for r in live_rows
                                        if _md5(str(r.get("finding_text") or "")) in new_md5),
                       "restore": len(restore), "insert": len(insert), "stale": len(stale),
                       "ambiguous": int(pairs.get("ambiguous", 0))}}


# ---------------------------------------------------------------------------
# 옛 덩어리 숨김 판정(166) — 순수
# ---------------------------------------------------------------------------

SUPERSEDE_RPC = "grm_findings_supersede"
LIVE_SELECT = "finding_id,raw_signal_id,finding_text,published_date,review_status,scope_status,finding_text_ko,finding_language"


def is_public(row: dict[str, Any]) -> bool:
    """034 공개 게이트와 같은 조건 — scope ok · 반려 아님 · 국문 있음(또는 원문 국문)."""
    return (str(row.get("scope_status") or "") == "ok"
            and str(row.get("review_status") or "") != "rejected"
            and (str(row.get("finding_text_ko") or "") != ""
                 or str(row.get("finding_language") or "") == "KO"))


def _settled(row: dict[str, Any]) -> bool:
    """더 기다릴 일이 없는 새 글 — 공개됐거나, 반려됐거나, 범위 밖으로 판정됐다(번역 대기 아님)."""
    return (is_public(row) or str(row.get("review_status") or "") == "rejected"
            or str(row.get("scope_status") or "") != "ok")


def plan_supersede(raw_signal_id: str, plan: dict[str, Any], live_rows: list[dict[str, Any]],
                   ) -> tuple[list[dict[str, Any]], str]:
    """(숨길 항목, 대기 사유). 항목 = grm_findings_supersede 입력. 사유가 비면 판정 완료.

    숨기는 조건: 새 본문의 글이 **모두** DB 에 있고 · 본문만 바뀐 짝(restore)이 남아 있지 않고 ·
    새 글이 모두 정리됐고(번역 대기 없음) · 하나 이상 공개 중. 대상 = stale 중 scope ok·반려 아닌 행.
    """
    live_by_id = {str(r.get("finding_id") or ""): r for r in live_rows}
    # 대상 = 숨지 않은 옛 행(scope ok · 반려 아님). 반려 행은 이미 비공개라 표식을 더하지 않는다.
    targets = [fid for fid in plan["stale"]
               if str(live_by_id.get(fid, {}).get("scope_status") or "") == "ok"
               and str(live_by_id.get(fid, {}).get("review_status") or "") != "rejected"]
    if not targets:
        return [], ""
    if plan["restore"]:
        return [], "restore_pending"
    live_by_md5 = {_md5(str(r.get("finding_text") or "")): r for r in live_rows}
    new_rows = [live_by_md5.get(_md5(str(f.get("finding_text") or ""))) for f in plan["new_extracted"]]
    if not new_rows or any(r is None for r in new_rows):
        return [], "new_not_inserted"
    if not all(_settled(r) for r in new_rows):
        return [], "new_untranslated"
    if not any(is_public(r) for r in new_rows):
        return [], "no_public_replacement"
    by = sorted({str(r.get("finding_id") or "") for r in new_rows} - set(targets))
    if not by:
        return [], "no_public_replacement"
    return [{"finding_id": fid, "raw_signal_id": raw_signal_id, "superseded_by": by,
             "reason": "body_recollected"} for fid in targets], ""


def _default_supersede(base_url: str, service_key: str, items: list[dict[str, Any]], changed_by: str
                       ) -> dict[str, Any]:
    """RPC 호출. 반환: {'superseded', 'skipped'} 또는 {'unavailable': True} (166 미적용) 또는 {'error'}."""
    import requests
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
               "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/rest/v1/rpc/{SUPERSEDE_RPC}"
    try:
        resp = requests.post(url, headers=headers, json={"p_items": items, "p_changed_by": changed_by},
                             timeout=_TIMEOUT_S)
    except requests.exceptions.RequestException as exc:
        return {"error": type(exc).__name__}
    if resp.status_code == 404:
        return {"unavailable": True}
    if resp.status_code >= 400:
        return {"error": f"http_{resp.status_code}"}
    try:
        data = resp.json()
    except ValueError:
        return {"error": "invalid_rpc_response"}
    return data if isinstance(data, dict) else {"error": "invalid_rpc_response"}


# ---------------------------------------------------------------------------
# 한 묶음 후속 — 쓰는 쪽(재수집 도구·R4·따라잡기)이 모두 이 함수를 부른다
# ---------------------------------------------------------------------------

def _default_get_rows(base_url: str, service_key: str, table: str, select: str, key: str,
                      ids: list[str]) -> list[dict[str, Any]] | None:
    import raw_update
    import requests
    return raw_update._get_rows(base_url, service_key, table, select, key, ids, get=requests.get)


def _default_append(base_url: str, service_key: str, findings: list[dict[str, Any]]
                    ) -> tuple[int, int, bool, list[str]]:
    import findings_supabase_append as fsa
    return fsa._append_findings_batch(base_url, service_key, findings)


def followup(base_url: str, service_key: str,
             changes: Iterable[tuple[dict[str, Any], dict[str, Any]]], *,
             dry_run: bool,
             get_rows: Callable[..., list[dict[str, Any]] | None] | None = None,
             append_fn: Callable[..., tuple[int, int, bool, list[str]]] | None = None,
             extract: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
             supersede: bool = False,
             supersede_fn: Callable[..., dict[str, Any]] | None = None,
             changed_by: str = "",
             ) -> tuple[dict[str, Any], dict[str, Any]]:
    """(옛 행, 새 행) 쌍들 → 지적사항 맞추기 + 분류 일치 점검. 반환: (요약, restore 계획).

    행 = raw_signals 행 모양(raw_signal_id·source·raw_json·row_json …). 새 행은 **지금 정본**이다.
    findings 는 새 본문에만 있는 글만 넣는다(dry_run 이면 세기만). 기존 findings 는 쓰지 않는다.
    """
    import raw_update

    get_rows = get_rows or _default_get_rows
    append_fn = append_fn or _default_append
    pairs = [(o, n) for o, n in changes
             if body_changed(str(n.get("source") or ""), _json_object(o.get("raw_json")),
                             _json_object(n.get("raw_json")))]
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "dry_run": dry_run, "rows": len(pairs),
        "lookup_failed": 0, "row_json_stale": 0, "row_json_unsupported": 0,
        "tier_transitions": {}, "relevance_transitions": {},
        "findings": Counter(), "inserted": 0, "duplicate": 0, "write_errors": 0,
        "pending_insert": 0, "per_row": [], "errors": [],
        "supersede_due": 0, "superseded": 0, "supersede_skipped": [], "supersede_unavailable": False,
        "supersede_waiting": Counter(),
    }
    sup_items: list[dict[str, Any]] = []
    plan_items: list[dict[str, Any]] = []
    plan = {"schema_version": raw_update.RESTORE_PLAN_SCHEMA_VERSION, "report": {}, "items": plan_items}
    if not pairs:
        return _finish(summary, plan)

    ids = [str(n.get("raw_signal_id") or "") for _o, n in pairs]
    live_rows = get_rows(base_url, service_key, "findings", LIVE_SELECT, "raw_signal_id", ids)
    if live_rows is None:
        summary["lookup_failed"] = 1
        summary["errors"].append("findings 조회 실패")
        return _finish(summary, plan)
    live_by_raw: dict[str, list[dict[str, Any]]] = {}
    for r in live_rows:
        live_by_raw.setdefault(str(r.get("raw_signal_id") or ""), []).append(r)

    tiers: Counter[str] = Counter()
    rels: Counter[str] = Counter()
    for old, new in pairs:
        rid = str(new.get("raw_signal_id") or "")
        source = str(new.get("source") or "")
        new_raw = _json_object(new.get("raw_json"))
        before = classification_of(old.get("row_json"))
        stored = classification_of(new.get("row_json"))
        rec: dict[str, Any] = {"raw_signal_id": rid, "document_id": str(new.get("document_id") or "")}
        expected_text = None
        try:
            expected_text = reclassified_row_json(source, new_raw, new.get("row_json"))
        except Exception as exc:  # noqa: BLE001 — 건별 실패는 세고 넘긴다
            summary["errors"].append(f"{rid}: reclassify_raised:{type(exc).__name__}")
        if expected_text is None:
            summary["row_json_unsupported"] += 1
        else:
            expected = classification_of(expected_text)
            if expected != stored:
                summary["row_json_stale"] += 1
                rec["row_json_stale"] = {k: [stored.get(k), expected.get(k)]
                                         for k in CLASSIFICATION_KEYS if stored.get(k) != expected.get(k)}
        tiers[f"{before.get('signal_tier') or '-'}→{stored.get('signal_tier') or '-'}"] += 1
        rels[f"{before.get('qa_relevance') or '-'}→{stored.get('qa_relevance') or '-'}"] += 1
        try:
            p = plan_findings(old, new, live_by_raw.get(rid, []), extract=extract)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"{rid}: extract_raised:{type(exc).__name__}")
            continue
        summary["findings"].update({k: v for k, v in p["counts"].items() if v})
        plan_items.extend(p["restore"])
        rec.update({"tier": [before.get("signal_tier"), stored.get("signal_tier")],
                    "relevance": [before.get("qa_relevance"), stored.get("qa_relevance")],
                    **{k: p["counts"][k] for k in ("live", "extracted_new", "same_text", "restore",
                                                   "insert", "stale")},
                    "stale_ids": p["stale"]})
        if supersede:
            items, waiting = plan_supersede(rid, p, live_by_raw.get(rid, []))
            if waiting:
                summary["supersede_waiting"][waiting] += 1
                rec["supersede_waiting"] = waiting
            sup_items.extend(items)
            rec["supersede"] = len(items)
        if p["insert"]:
            if dry_run:
                summary["pending_insert"] += len(p["insert"])
            else:
                try:
                    ins, dup, had_err, errs = append_fn(base_url, service_key, p["insert"])
                except Exception as exc:  # noqa: BLE001
                    ins, dup, had_err, errs = 0, 0, True, [f"append_raised:{type(exc).__name__}"]
                summary["inserted"] += ins
                summary["duplicate"] += dup
                if had_err:
                    summary["write_errors"] += 1
                    summary["pending_insert"] += max(0, len(p["insert"]) - ins - dup)
                    summary["errors"].extend(f"{rid}: {e}" for e in errs[:3])
        summary["per_row"].append(rec)
    if sup_items:
        summary["supersede_due"] = len(sup_items)
        if not dry_run:
            res = (supersede_fn or _default_supersede)(base_url, service_key, sup_items,
                                                       changed_by or "raw_body_followup")
            if res.get("unavailable"):
                summary["supersede_unavailable"] = True
                log("WARN", f"옛 덩어리 숨김 대상 {len(sup_items)}건 — migration 166 미적용이라 이번엔 숨기지 않음")
            elif res.get("error"):
                summary["write_errors"] += 1
                summary["errors"].append(f"supersede: {res['error']}")
            else:
                summary["superseded"] = int(res.get("superseded") or 0)
                summary["supersede_skipped"] = list(res.get("skipped") or [])
    summary["tier_transitions"] = dict(sorted(tiers.items()))
    summary["relevance_transitions"] = dict(sorted(rels.items()))
    log("INFO", f"본문 교체 후속{' [DRY]' if dry_run else ''}: 행 {len(pairs)} · 분류 어긋남 "
                f"{summary['row_json_stale']} · findings 넣기 {summary['inserted'] or summary['pending_insert']} "
                f"· restore {len(plan_items)} · 옛 본문에만 남은 행 {summary['findings'].get('stale', 0)}")
    return _finish(summary, plan)


def _finish(summary: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    summary["findings"] = dict(summary["findings"])
    summary["supersede_waiting"] = dict(summary["supersede_waiting"])
    items = plan["items"]
    items.sort(key=lambda it: (it.get("published_date") or "", it.get("finding_id") or ""), reverse=True)
    plan["report"] = {
        "schema_version": plan["schema_version"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "origin": "raw_body_followup",
        "mapped": len(items),
        "new_chars_total": sum(int(it.get("new_chars") or 0) for it in items),
        "old_chars_total": sum(int(it.get("old_chars") or 0) for it in items),
    }
    return summary, plan


def unresolved(summary: dict[str, Any]) -> list[str]:
    """`--check` 가 막는 사유 — 자동으로 해야 했는데 남은 것만(사람 검토 몫인 stale·모호는 제외)."""
    reasons = []
    if summary.get("lookup_failed"):
        reasons.append("lookup_failed")
    if summary.get("row_json_stale"):
        reasons.append(f"row_json_stale:{summary['row_json_stale']}")
    if summary.get("pending_insert"):
        reasons.append(f"pending_insert:{summary['pending_insert']}")
    if summary.get("write_errors"):
        reasons.append(f"write_errors:{summary['write_errors']}")
    if summary.get("supersede_skipped"):
        # 코드 판정과 DB 함수 재확인이 어긋났다(166) — 조건 중 하나가 표류했다는 뜻이라 막는다.
        reasons.append(f"supersede_skipped:{len(summary['supersede_skipped'])}")
    return reasons


# ---------------------------------------------------------------------------
# 따라잡기 — raw_signal_versions 로 본문이 바뀐 행을 찾는다
# ---------------------------------------------------------------------------

def fetch_changed_pairs(base_url: str, service_key: str, *, since: str = "",
                        changed_by_like: str = "", source: str = SOURCE_FDA_WL,
                        doc_ids: list[str] | None = None,
                        ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """기간·도구로 고른 옛 판 중 raw_signal 마다 **가장 이른 판**(= 바뀌기 전) ↔ 지금 정본."""
    import findings_supabase_backfill as fsb

    params: dict[str, str] = {}
    if since:
        params["valid_to"] = f"gte.{since}"
    if changed_by_like:
        params["changed_by"] = f"like.{changed_by_like}"
    versions = fsb._fetch_all_pages(base_url, service_key, "raw_signal_versions",
                                    select="raw_signal_id,version_no,raw_json,row_json,changed_by",
                                    page_size=_PAGE_SIZE, order="raw_signal_id.asc,version_no.asc",
                                    extra_params=params)
    oldest: dict[str, dict[str, Any]] = {}
    for v in versions:
        rid = str(v.get("raw_signal_id") or "")
        if rid and v.get("raw_json") and rid not in oldest:
            oldest[rid] = v
    ids = sorted(oldest)
    rows: list[dict[str, Any]] = []
    for start in range(0, len(ids), _FETCH_CHUNK):
        chunk = ids[start:start + _FETCH_CHUNK]
        rows.extend(fsb._fetch_all_pages(base_url, service_key, "raw_signals", select="*",
                                         page_size=_PAGE_SIZE, order="raw_signal_id.asc",
                                         extra_params={"raw_signal_id": "in.(" + ",".join(chunk) + ")",
                                                       "source": f"eq.{source}"}))
    wanted = set(doc_ids or [])
    out = []
    for row in rows:
        rid = str(row.get("raw_signal_id") or "")
        if wanted and rid not in wanted and str(row.get("document_id") or "") not in wanted:
            continue
        v = oldest[rid]
        out.append((dict(row, raw_json=v.get("raw_json"), row_json=v.get("row_json") or row.get("row_json")),
                    row))
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="본문이 바뀐 raw_signals 의 분류·findings 후속(따라잡기·검사).")
    p.add_argument("--apply", action="store_true", help="새 본문에만 있는 findings 를 넣는다. 기본은 dry-run.")
    p.add_argument("--since-days", type=int, default=0, help="이 기간(일) 안에 바뀐 판만. 0 = 기간 제한 없음.")
    p.add_argument("--changed-by-like", default="", help="raw_signal_versions.changed_by LIKE 패턴(예: 'backfill_wl_body_recovery:%%').")
    p.add_argument("--doc-ids", default="", help="raw_signal_id·document_id 목록(공백/쉼표).")
    p.add_argument("--supersede", action="store_true",
                   help="옛 본문에만 남은 공개 행을 새 글이 공개된 문서에서만 '대체됨'으로 숨긴다(migration 166).")
    p.add_argument("--check", action="store_true", help="할 일이 남으면 exit 1(분류 어긋남·넣을 findings).")
    p.add_argument("--report-path", default="raw_body_followup_report.json")
    p.add_argument("--plan-path", default="", help="restore 계획 JSON 경로(항목이 있을 때만 쓴다).")
    p.add_argument("--supabase-url", default="")
    p.add_argument("--service-role-key", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
    creds = resolve_supabase_service_credentials(args)
    base, key = (normalize_supabase_url(creds[0]), creds[1]) if creds else (None, "")
    if not base or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 필요", file=sys.stderr)
        return 2
    since = ""
    if args.since_days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=args.since_days)).isoformat()
    doc_ids = [x for x in args.doc_ids.replace(",", " ").split() if x]
    try:
        pairs = fetch_changed_pairs(base, key, since=since, changed_by_like=args.changed_by_like,
                                    doc_ids=doc_ids)
    except (RuntimeError, ValueError) as exc:
        print(f"조회 실패: {type(exc).__name__}", file=sys.stderr)
        return 2
    import raw_versioning as rv
    summary, plan = followup(base, key, pairs, dry_run=not args.apply, supersede=args.supersede,
                             changed_by=rv.run_label("raw_body_followup"))
    summary["selected"] = len(pairs)
    reasons = unresolved(summary)
    summary["unresolved"] = reasons
    Path(args.report_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.plan_path and plan["items"]:
        Path(args.plan_path).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {k: summary[k] for k in ("dry_run", "selected", "rows", "row_json_stale", "row_json_unsupported",
                                     "tier_transitions", "relevance_transitions", "findings",
                                     "inserted", "pending_insert", "supersede_due", "superseded",
                                     "supersede_waiting", "supersede_unavailable", "write_errors", "unresolved")}
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(brief, ensure_ascii=False))
    if summary["write_errors"] or (args.check and reasons):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
