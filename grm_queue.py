#!/usr/bin/env python3
"""GRM 주간 대기열 — 노션 대신 Supabase(+git) 기준으로 (그림자 단계, 2026-11-12).

노션 Intake DB 가 쥐고 있던 두 가지를 여기로 옮긴다.

1. **처리 상태 규칙** — 어느 행이 이번 주 handoff 에 들어가고, 발행 뒤 어떤 상태로 끝나는가.
2. **매주 handoff 만들기** — 노션 행·페이지 본문이 아니라 `raw_signals` 로 같은 payload 를 만든다.

원칙
- **대기열에 넣는 것은 일일 수집뿐이다.** 노션 행이 새로 생긴 문서만 `intake_queue` 에 넣는다.
  과거분 백필·직행 수집기는 넣지 않으므로 handoff 에 끼어들 수 없다.
- **상태는 AI 가 찍지 않고 코드가 계산한다.** 근거는 main 의 발행 파일이다
  (`web/data/deltas/delta_{날짜}.json`, `web/data/briefs/brief_web_{날짜}.json`).
  표의 state 는 그 계산의 사본이라, 매일 다시 계산해도 결과가 같다(쓰기 실패는 다음 실행이 고친다).
- **운영 중인 규칙(v1)을 그대로 옮긴다.** 후보 = 미소비(new·deferred) ∧ 수집일이 최근 N일(기본 30) 안.
  `ENABLE_HANDOFF_IDEMPOTENCY_V2`(Handoff Ref 상태 기계)는 운영에서 켜진 적이 없어 옮기지 않는다.
- **그림자**: `ENABLE_INTAKE_QUEUE_SHADOW` 가 켜져 있을 때만 돌고, 실패해도 수집·노션 handoff 를
  멈추지 않는다. 발행은 노션 handoff 를 그대로 쓴다.

행의 최종 상태(v16 [Status 갱신] 대응)
    error      status_hint=Error (카드가 실렸어도 Error 가 우선)
    processed  발행본에 카드로 실림(병합 멤버는 대표를 따른다)
    deferred   델타의 `deferred` 목록에 있음 → 다음 handoff 에 다시 들어간다
    skipped    그 밖 — 사유: out_of_scope_483 · tier1 · watch · carded_not_published · not_carded
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

import requests

import grm_findings as gf
from grm_cli import normalize_supabase_url
from grm_common import SOURCE_FDA_483, env_flag, log, truncate
from grm_taxonomy import is_out_of_scope_483

KST = timezone(timedelta(hours=9))

STATE_NEW = "new"
STATE_PROCESSED = "processed"
STATE_SKIPPED = "skipped"
STATE_ERROR = "error"
STATE_DEFERRED = "deferred"
OPEN_STATES = (STATE_NEW, STATE_DEFERRED)          # 다음 handoff 후보가 되는 상태

HANDOFF_OPEN = "open"
HANDOFF_CONSUMED = "consumed"
HANDOFF_STALE = "stale"

# 노션 Status ↔ 표 state. 비교(그림자 b)에만 쓴다.
NOTION_STATUS_TO_STATE = {
    "New": STATE_NEW, "Processed": STATE_PROCESSED, "Skipped": STATE_SKIPPED, "Error": STATE_ERROR,
}

RECONCILE_LOOKBACK_DAYS = 35      # 이 기간 안의 handoff 는 매번 다시 계산한다(늦게 온 발행본 반영)
SHADOW_DIR_ENV = "GRM_QUEUE_SHADOW_DIR"
DEFAULT_SHADOW_DIR = "_queue_shadow"

_TIMEOUT = 30
_ATTEMPTS = 2
_ID_CHUNK = 20                    # raw_json 이 커서 한 번에 조금씩 읽는다
_KEY_CHUNK = 100


FROZEN_PUBLISH_DAY = "publish-day-first-record"


def handoff_refresh_requested() -> bool:
    """발행 요일 handoff 를 일부러 다시 만들라는 스위치(기본 꺼짐). 쓰고 나면 반드시 끈다."""
    return env_flag("GRM_QUEUE_HANDOFF_REFRESH")


def is_publish_day(handoff_date: str) -> bool:
    """그 날짜가 발행 요일(월)인가. 요일 정본은 `grm_handoff.PUBLISH_WEEKDAY`."""
    import grm_handoff as gh
    try:
        return date.fromisoformat(str(handoff_date)[:10]).weekday() == gh.PUBLISH_WEEKDAY
    except ValueError:
        return False


def shadow_enabled() -> bool:
    """그림자 스위치(기본 꺼짐). 켜도 발행 경로는 바뀌지 않는다."""
    return env_flag("ENABLE_INTAKE_QUEUE_SHADOW")


def shadow_dir() -> str:
    return os.environ.get(SHADOW_DIR_ENV, "").strip() or DEFAULT_SHADOW_DIR


# ─────────────────────────────── 순수 규칙 ───────────────────────────────

def row_key(source: str, document_id: str) -> str:
    return f"{source}::{document_id}"


def raw_signal_id_for(source: str, document_id: str) -> str:
    """`grm_findings` 의 raw_signal_id 산식과 같은 값(source+document_id 고정 해시)."""
    return "rawsig-" + gf.stable_hash({
        "schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
        "source": source,
        "document_id": document_id,
    })[:24]


def to_kst(value: Any) -> "datetime | None":
    """수집 시각(text·datetime) → KST datetime. 읽을 수 없으면 None(날짜를 지어내지 않는다)."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def snapshot_from_raw_signal(record: dict[str, Any], queue_row: dict[str, Any],
                             snapshot_keys: Iterable[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """raw_signals 1행 → (노션 스냅샷과 같은 모양의 handoff 행, 원문 raw dict).

    노션 전용 칸(page_id·page_url·title)은 빈 값이다. 수집일은 대기열의 first_seen 을 쓴다
    (raw_signals.collected_at 은 과거분 수집기가 먼저 넣었을 때 값이 달라진다).
    제품군은 raw_signals.modality 칸이 우선이고, 없으면 row_json 안의 값이다.
    """
    rj = _loads(record.get("row_json"))
    snap: dict[str, Any] = {k: "" for k in snapshot_keys}
    for k in snap:
        v = rj.get(k)
        if v is not None:
            # 노션 길은 속성 값을 읽을 때 앞뒤 공백을 떼어 낸다(`_plain_text().strip()`). 원천이 남긴
            # 줄 끝 `\r` 같은 문자가 DB 에는 그대로 있어 헤드라인이 1글자 달랐다(재생 대조 10-3).
            # 같은 규칙으로 맞춘다 — 앞뒤만 뗀다. 글 안쪽은 건드리지 않는다.
            snap[k] = v.strip() if isinstance(v, str) else v
    snap["source"] = str(record.get("source") or "")
    snap["document_id"] = str(record.get("document_id") or "")
    seen = to_kst(queue_row.get("first_seen_at")) or to_kst(record.get("collected_at"))
    snap["run_date"] = str(queue_row.get("first_seen_run_date") or
                           (seen.date().isoformat() if seen else ""))
    snap["collected_at"] = seen.isoformat() if seen else ""
    snap["modality"] = str(record.get("modality") or rj.get("modality") or "")
    snap["status"] = "New"
    return snap, _loads(record.get("raw_json"))


def pre_handoff_skip_reason(source: str, raw: dict[str, Any]) -> str:
    """handoff 에 싣기 전에 코드가 닫는 행 — FDA 483 범위 밖 두 가지.

    out_of_scope_483       수집 때 찍힌 표식(`fda483_text_status=out-of-scope`). 노션에서는
                           `fda483_notion_raw_sync` 가 같은 행을 Status=Skipped 로 닫는다.
    out_of_scope_483_rule  표식은 없지만 지금 규칙(`grm_taxonomy.is_out_of_scope_483`)으로 범위 밖.
                           범위 규칙이 넓어지기 전에 저장된 행이다. 노션 길에서는 handoff 에 실렸다가
                           발행 조립(`assemble_publish_brief._is_out_of_scope_483_card`)에서 빠진다.
                           같은 함수에 같은 재료(시설 유형 + 관찰 본문)를 넘긴다 — 판정 사본을 두지 않는다.
    """
    if source != SOURCE_FDA_483:
        return ""
    raw = raw or {}
    if raw.get("fda483_text_status") == "out-of-scope":
        return "out_of_scope_483"
    body = " ".join(f"{o.get('deficiency', '')} {o.get('detail', '')}"
                    for o in (raw.get("fda_483_observations") or []) if isinstance(o, dict))
    if is_out_of_scope_483(str(raw.get("establishment_type") or ""), body):
        return "out_of_scope_483_rule"
    return ""


def candidate_window(run_date: date, window_days: int) -> tuple[str, str]:
    """후보 수집일 범위(양끝 포함). 노션 v1 필터(Run Date ≥ D−N ∧ ≤ D)와 같다."""
    return (run_date - timedelta(days=window_days)).isoformat(), run_date.isoformat()


def build_manifest(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """handoff v2 payload → 상태 계산에 필요한 최소 행 목록(본문 없음)."""
    out: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "source": row.get("source", ""),
            "document_id": row.get("document_id", ""),
            "web_card_id": row.get("web_card_id", ""),
            "merged_into": row.get("merged_into", ""),
            "status_hint": row.get("status_hint", ""),
            "signal_tier": row.get("signal_tier", ""),
            "section": row.get("section", ""),
        })
    return out


def payload_sha256(payload: dict[str, Any]) -> str:
    """행 내용의 지문(생성 시각 제외) — 같은 재료면 같은 값."""
    body = {k: v for k, v in payload.items() if k != "generated_at_kst"}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _id_set(values: Any) -> set[str]:
    out: set[str] = set()
    for v in values or []:
        s = str(v or "").strip()
        if s:
            out.add(s)
    return out


def compute_states(manifest: list[dict[str, Any]], delta: dict[str, Any],
                   brief: "dict[str, Any] | None", publish_date: str
                   ) -> dict[str, dict[str, str]]:
    """그 주 handoff 행 목록 + main 의 델타·발행본 → 행별 최종 상태.

    반환 {source::document_id: {"state","reason","brief_publish_date"}}. 순수 함수 — 같은 입력이면
    같은 결과다. `brief` 가 아직 없으면(델타만 들어온 직후) 델타에 실린 카드를 임시로 processed 로
    두고 사유에 표시한다 — 발행본이 들어오면 다음 계산에서 확정된다.
    """
    delta_ids = _id_set((delta.get("cards") or {}).keys())
    deferred = _id_set(delta.get("deferred"))
    brief_ids = (None if brief is None
                 else _id_set(c.get("id") for c in brief.get("cards") or [] if isinstance(c, dict)))

    def verdict_for_card(key: str, doc_id: str, row: dict[str, Any]) -> tuple[str, str]:
        if key in deferred or doc_id in deferred:
            carded = doc_id in delta_ids
            if not carded:
                return STATE_DEFERRED, "deferred"
        if brief_ids is not None:
            if doc_id in brief_ids:
                return STATE_PROCESSED, "published"
            if doc_id in delta_ids:
                return STATE_SKIPPED, "carded_not_published"
        elif doc_id in delta_ids:
            return STATE_PROCESSED, "carded_pending_publish"
        if row.get("signal_tier") == "Tier 1":
            return STATE_SKIPPED, "tier1"
        if row.get("section") == "watch":
            return STATE_SKIPPED, "watch"
        return STATE_SKIPPED, "not_carded"

    by_key = {row_key(r.get("source", ""), r.get("document_id", "")): r for r in manifest}
    out: dict[str, dict[str, str]] = {}
    for key, row in by_key.items():
        if row.get("merged_into"):
            continue
        state, reason = verdict_for_card(key, str(row.get("document_id") or ""), row)
        out[key] = {"state": state, "reason": reason}
    for key, row in by_key.items():
        rep = row.get("merged_into")
        if not rep:
            continue
        rep_verdict = out.get(str(rep))
        if rep_verdict is None:      # 대표가 목록에 없다 — 근거 없이 소비하지 않는다
            out[key] = {"state": STATE_SKIPPED, "reason": "merged_representative_missing"}
        else:
            out[key] = {"state": rep_verdict["state"], "reason": "merged:" + rep_verdict["reason"]}
    for key, row in by_key.items():
        if row.get("status_hint") == "Error":
            out[key] = {"state": STATE_ERROR, "reason": "status_hint_error"}
    for verdict in out.values():
        verdict["brief_publish_date"] = publish_date if verdict["state"] == STATE_PROCESSED else ""
    return out


# 비교에서 빼는 칸 — 노션 쪽 장부 칸(값이 다른 것이 정상).
COMPARE_SKIP_FIELDS = frozenset({"page_id", "page_url", "title", "run_date", "collected_at", "status"})


def _norm(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _classify_diff(notion_value: Any, db_value: Any) -> str:
    """칸 하나의 차이를 분류한다. db_richer = 노션 값이 DB 값의 앞부분(노션 쪽 잘림)."""
    if isinstance(notion_value, str) and isinstance(db_value, str):
        n = notion_value.rstrip("….").rstrip()
        d = db_value.rstrip("….").rstrip()
        if not n and d:
            return "db_only_value"
        if n and not d:
            return "notion_only_value"
        if len(d) > len(n) and d.startswith(n):
            return "db_richer"
        if len(n) > len(d) and n.startswith(d):
            return "notion_richer"
        return "differs"
    if notion_value in (None, "", [], {}) and db_value not in (None, "", [], {}):
        return "db_only_value"
    if db_value in (None, "", [], {}) and notion_value not in (None, "", [], {}):
        return "notion_only_value"
    return "differs"


def compare_payloads(notion_payload: dict[str, Any], db_payload: dict[str, Any]) -> dict[str, Any]:
    """노션으로 만든 handoff 와 DB 로 만든 handoff 를 행·칸 단위로 비교한다(그림자 a)."""
    n_rows = {row_key(r.get("source", ""), r.get("document_id", "")): r
              for r in notion_payload.get("rows") or [] if isinstance(r, dict)}
    d_rows = {row_key(r.get("source", ""), r.get("document_id", "")): r
              for r in db_payload.get("rows") or [] if isinstance(r, dict)}
    common = sorted(set(n_rows) & set(d_rows))
    identical = 0
    field_diff: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for key in common:
        a, b = n_rows[key], d_rows[key]
        clean = True
        for f in sorted((set(a) | set(b)) - COMPARE_SKIP_FIELDS):
            if _norm(a.get(f)) == _norm(b.get(f)):
                continue
            clean = False
            label = f"{a.get('source', '')}|{f}|{_classify_diff(a.get(f), b.get(f))}"
            field_diff[label] = field_diff.get(label, 0) + 1
            examples.setdefault(label, [])
            if len(examples[label]) < 3:
                examples[label].append(str(a.get("document_id", "")))
        identical += 1 if clean else 0
    return {
        "notion_rows": len(n_rows),
        "db_rows": len(d_rows),
        "common": len(common),
        "identical": identical,
        "only_notion": sorted(set(n_rows) - set(d_rows)),
        "only_db": sorted(set(d_rows) - set(n_rows)),
        "field_diff": dict(sorted(field_diff.items(), key=lambda kv: (-kv[1], kv[0]))),
        "examples": examples,
        "coverage_same": (notion_payload.get("coverage_collected_md")
                          == db_payload.get("coverage_collected_md")),
    }


def _explain_only_notion(store: Any, cmp_: dict[str, Any]) -> None:
    """'노션에만' 행 중 코드가 범위 밖 483 으로 이미 닫은 행을 따로 뺀다.

    노션 길은 그 행을 handoff 에 싣고 발행 조립에서 뺀다. DB 길은 handoff 전에 뺀다. 발행 결과는 같다.
    남은 `only_notion` 이 진짜로 설명되지 않은 차이다. 조회 실패는 분류만 못 할 뿐 비교는 남긴다.
    """
    cmp_["only_notion_out_of_scope"] = []
    keys = [tuple(k.split("::", 1)) for k in cmp_.get("only_notion") or [] if "::" in k]
    if not keys:
        return
    try:
        rows = store.queue_rows(keys)
    except QueueStoreError:
        return
    closed = sorted(k for k, r in rows.items()
                    if r.get("state") == STATE_SKIPPED
                    and str(r.get("state_reason") or "").startswith("out_of_scope_483"))
    cmp_["only_notion_out_of_scope"] = closed
    cmp_["only_notion"] = [k for k in cmp_["only_notion"] if k not in set(closed)]


def compare_states(computed: dict[str, dict[str, str]],
                   notion_status: dict[str, str]) -> dict[str, Any]:
    """코드가 계산한 상태 vs 노션에 실제로 찍힌 Status(그림자 b)."""
    pairs: dict[str, int] = {}
    mismatched: list[dict[str, str]] = []
    missing: list[str] = []
    explained = 0
    for key in sorted(computed):
        mine = computed[key]["state"]
        status = notion_status.get(key)
        if status is None:
            missing.append(key)
            continue
        theirs = NOTION_STATUS_TO_STATE.get(status, status)
        # 노션에는 deferred 가 없다(Status 미변경 = New).
        same = mine == theirs or (mine == STATE_DEFERRED and theirs == STATE_NEW)
        label = f"notion={theirs} / code={mine}"
        pairs[label] = pairs.get(label, 0) + 1
        # Routine 은 "델타에 카드를 썼다"로 Processed 를 찍고, 코드는 "발행본에 실렸다"로 판정한다.
        # 델타에는 있었지만 발행 조립에서 빠진 카드는 둘 다 '끝난 행'이라 재유입에는 차이가 없다
        # → 불일치가 아니라 설명된 차이로 따로 센다.
        if (not same and theirs == STATE_PROCESSED and mine == STATE_SKIPPED
                and computed[key].get("reason", "").endswith("carded_not_published")):
            explained += 1
            continue
        if not same:
            mismatched.append({"key": key, "notion": theirs, "code": mine,
                               "reason": computed[key].get("reason", "")})
    return {"rows": len(computed),
            "match": len(computed) - len(mismatched) - len(missing) - explained,
            "explained": explained, "mismatch": len(mismatched), "not_in_notion": missing,
            "pairs": dict(sorted(pairs.items())), "mismatched": mismatched}


def render_report_md(report: dict[str, Any]) -> str:
    """그림자 보고서(실행 요약·아티팩트용). 사람이 읽는 표."""
    lines = [f"## 대기열 그림자 — {report.get('run_date', '')}", ""]
    enq = report.get("enqueue") or {}
    if enq:
        lines += [f"- 대기열에 넣음: 요청 {enq.get('requested', 0)} · 새로 {enq.get('inserted', 0)} · "
                  f"실패 {enq.get('failed', 0)}"]
    heal = report.get("heal") or {}
    if heal:
        lines += [f"- 대기열 보충(노션 handoff 에 있는데 대기열에 없던 행): 없던 행 {heal.get('missing', 0)} · "
                  f"다시 넣음 {heal.get('inserted', 0)} · 실패 {heal.get('failed', 0)}"]
    if report.get("handoff_frozen") == FROZEN_PUBLISH_DAY:
        lines += ["- 오늘(발행 요일) handoff 는 첫 기록이 동결돼 있어 다시 쓰지 않았다. 새 행은 내일 handoff 에 들어간다. "
                  "일부러 다시 만들려면 저장소 변수 GRM_QUEUE_HANDOFF_REFRESH=true 로 수집을 다시 돌린 뒤 변수를 끈다"]
    elif report.get("handoff_frozen"):
        lines += [f"- 오늘 handoff 기록은 이미 {report['handoff_frozen']} 상태라 다시 쓰지 않았다(동결)"]
    rec = report.get("reconcile") or {}
    if rec:
        lines += [f"- 상태 다시 계산: handoff {rec.get('handoffs', 0)}건 · 행 {rec.get('rows', 0)}건 "
                  f"({', '.join(f'{k} {v}' for k, v in sorted((rec.get('states') or {}).items())) or '없음'})"]
    cand = report.get("candidates") or {}
    if cand:
        lines += [f"- 후보 {cand.get('queue', 0)}행 → 범위 밖 483 {cand.get('pre_skipped', 0)} · "
                  f"정본 없음 {cand.get('missing_raw', 0)} · handoff {cand.get('handoff', 0)}행"]
    cmp_ = report.get("compare")
    if cmp_:
        lines += ["", "### 노션 handoff ↔ DB handoff", "",
                  "| 노션 | DB | 공통 | 칸까지 같음 | 노션에만 | DB에만 | 수집 현황 줄 |",
                  "|---|---|---|---|---|---|---|",
                  f"| {cmp_['notion_rows']} | {cmp_['db_rows']} | {cmp_['common']} | {cmp_['identical']} | "
                  f"{len(cmp_['only_notion'])} | {len(cmp_['only_db'])} | "
                  f"{'같음' if cmp_['coverage_same'] else '다름'} |"]
        if cmp_["only_notion"]:
            lines += ["", "노션에만: " + ", ".join(cmp_["only_notion"][:30])]
        if cmp_.get("only_notion_out_of_scope"):
            lines += ["", f"노션에만 있지만 설명됨(범위 밖 483 — 노션 길은 발행 조립에서 뺀다) "
                          f"{len(cmp_['only_notion_out_of_scope'])}건: "
                          + ", ".join(cmp_["only_notion_out_of_scope"][:30])]
        if cmp_["only_db"]:
            lines += ["", "DB에만: " + ", ".join(cmp_["only_db"][:30])]
        if cmp_["field_diff"]:
            lines += ["", "| 소스 | 칸 | 분류 | 건수 | 예 |", "|---|---|---|---|---|"]
            for label, count in list(cmp_["field_diff"].items())[:40]:
                src, field, kind = label.split("|", 2)
                lines.append(f"| {src} | {field} | {kind} | {count} | "
                             f"{', '.join(cmp_['examples'].get(label, []))} |")
    for st in report.get("state_compare") or []:
        lines += ["", f"### 처리 상태 — {st.get('handoff_date', '')} 호 (코드 계산 ↔ 노션 Status)", "",
                  f"- 행 {st['rows']} · 일치 {st['match']} · 설명된 차이(델타에는 있었으나 발행본에서 빠짐) "
                  f"{st.get('explained', 0)} · 불일치 {st['mismatch']} · "
                  f"노션에서 못 찾음 {len(st['not_in_notion'])}"]
        for label, count in st["pairs"].items():
            lines.append(f"  - {label}: {count}")
        for m in st["mismatched"][:20]:
            lines.append(f"  - 불일치 {m['key']} — 노션 {m['notion']} / 코드 {m['code']}({m['reason']})")
    for w in report.get("warnings") or []:
        lines += ["", f"> 경고: {w}"]
    return "\n".join(lines) + "\n"


# ─────────────────────────────── Supabase 입출력 ───────────────────────────────

class QueueStoreError(RuntimeError):
    """대기열 표 읽기·쓰기 실패. 메시지에는 표 이름과 상태 코드만 담는다(키 누출 방지)."""


class QueueStore:
    """`intake_queue`·`intake_handoffs`·`raw_signals` PostgREST 접근. `session` 주입으로 시험한다."""

    def __init__(self, base_url: str, service_key: str, session: Any = None) -> None:
        base = normalize_supabase_url(base_url)
        if base is None or not service_key:
            raise QueueStoreError("Supabase 주소·키 없음")
        self.base = base
        self._headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
        self._http = session or requests

    def _call(self, method: str, table: str, *, params: dict[str, str] | None = None,
              body: Any = None, prefer: str = "") -> Any:
        headers = dict(self._headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        url = f"{self.base}/rest/v1/{table}"
        last = ""
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                resp = self._http.request(method, url, params=params, json=body,
                                          headers=headers, timeout=_TIMEOUT)
            except requests.exceptions.Timeout:
                last = "timeout"
                continue
            except requests.exceptions.RequestException as e:
                raise QueueStoreError(f"{table} {method}: {type(e).__name__}") from None
            if resp.status_code >= 500 and attempt < _ATTEMPTS:
                last = f"http_{resp.status_code}"
                continue
            if resp.status_code >= 400:
                raise QueueStoreError(f"{table} {method}: http_{resp.status_code}")
            try:
                return resp.json()
            except ValueError:
                return []
        raise QueueStoreError(f"{table} {method}: {last or 'retry_exhausted'}")

    def _get_all(self, table: str, params: dict[str, str], page: int = 1000) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            q = dict(params, limit=str(page), offset=str(offset))
            rows = self._call("GET", table, params=q)
            rows = rows if isinstance(rows, list) else []
            out.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < page:
                return out
            offset += page

    # 대기열 ------------------------------------------------------------
    def enqueue(self, rows: list[dict[str, Any]]) -> int:
        """대기열에 넣는다(이미 있으면 그대로 둔다). 반환 = 새로 들어간 행 수."""
        inserted = 0
        for i in range(0, len(rows), _KEY_CHUNK):
            got = self._call("POST", "intake_queue", params={"on_conflict": "source,document_id"},
                             body=rows[i:i + _KEY_CHUNK],
                             prefer="resolution=ignore-duplicates,return=representation")
            inserted += len(got) if isinstance(got, list) else 0
        return inserted

    def open_candidates(self, start: str, end: str) -> list[dict[str, Any]]:
        return self._get_all("intake_queue", {
            "select": "source,document_id,first_seen_run_date,first_seen_at,state,handoff_date",
            "state": f"in.({','.join(OPEN_STATES)})",
            "first_seen_run_date": f"gte.{start}",
            "and": f"(first_seen_run_date.lte.{end})",
            "order": "source.asc,document_id.asc",
        })

    def aged_open_count(self, before: str) -> int:
        rows = self._get_all("intake_queue", {
            "select": "source,document_id", "state": f"in.({','.join(OPEN_STATES)})",
            "first_seen_run_date": f"lt.{before}"})
        return len(rows)

    def queue_rows(self, keys: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
        """(source, document_id) 목록의 현재 대기열 행. 소스별로 나눠 읽는다."""
        by_source: dict[str, list[str]] = {}
        for source, doc in keys:
            by_source.setdefault(source, []).append(doc)
        out: dict[str, dict[str, Any]] = {}
        for source, docs in by_source.items():
            for i in range(0, len(docs), _KEY_CHUNK):
                chunk = docs[i:i + _KEY_CHUNK]
                quoted = ",".join('"' + d.replace('"', '\\"') + '"' for d in chunk)
                for r in self._get_all("intake_queue", {
                        "select": "source,document_id,state,state_reason,handoff_date,brief_publish_date",
                        "source": f"eq.{source}", "document_id": f"in.({quoted})"}):
                    out[row_key(r.get("source", ""), r.get("document_id", ""))] = r
        return out

    def set_state(self, source: str, document_id: str, fields: dict[str, Any]) -> None:
        self._call("PATCH", "intake_queue",
                   params={"source": f"eq.{source}", "document_id": f"eq.{document_id}"},
                   body=fields, prefer="return=minimal")

    # 정본 --------------------------------------------------------------
    def raw_signals(self, keys: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
        ids = {raw_signal_id_for(s, d): row_key(s, d) for s, d in keys}
        out: dict[str, dict[str, Any]] = {}
        ordered = sorted(ids)
        for i in range(0, len(ordered), _ID_CHUNK):
            chunk = ordered[i:i + _ID_CHUNK]
            rows = self._call("GET", "raw_signals", params={
                "select": "raw_signal_id,source,document_id,collected_at,modality,row_json,raw_json",
                "raw_signal_id": "in.(" + ",".join(chunk) + ")"})
            for r in rows if isinstance(rows, list) else []:
                out[row_key(r.get("source", ""), r.get("document_id", ""))] = r
        return out

    def raw_signals_ingested_since(self, since_iso: str) -> list[dict[str, Any]]:
        """초기 이관(seed)용 — 본문 없이 키와 수집 시각만.

        collected_at 은 text 칸이라 서버에서 시각 비교를 하지 않는다. 적재 시각(ingested_at)으로
        넉넉히 읽고, 수집 시각 비교는 `plan_seed` 가 한다(그 뒤 수집됐다면 그 뒤에 적재됐다).
        """
        return self._get_all("raw_signals", {
            "select": "source,document_id,collected_at,published_date",
            "ingested_at": f"gte.{since_iso}", "order": "raw_signal_id.asc"})

    def exclusion_keys(self) -> set[str]:
        try:
            rows = self._get_all("intake_exclusion", {"select": "source,document_id"})
        except QueueStoreError:
            return set()
        return {row_key(r.get("source", ""), r.get("document_id", "")) for r in rows}

    # handoff 기록 --------------------------------------------------------
    def handoffs_since(self, start: str) -> list[dict[str, Any]]:
        return self._get_all("intake_handoffs", {
            "select": "handoff_date,generated_at,status,manifest,consumed_evidence,payload_sha256",
            "handoff_date": f"gte.{start}", "order": "handoff_date.asc"})

    def get_handoff(self, handoff_date: str) -> "dict[str, Any] | None":
        rows = self._call("GET", "intake_handoffs", params={
            "select": "handoff_date,status,row_count,consumed_evidence",
            "handoff_date": f"eq.{handoff_date}", "limit": "1"})
        return rows[0] if isinstance(rows, list) and rows else None

    def all_queue_keys(self) -> set[str]:
        """대기열에 있는 모든 문서 키(`source::document_id`)."""
        return {row_key(r.get("source", ""), r.get("document_id", ""))
                for r in self._get_all("intake_queue", {"select": "source,document_id",
                                                        "order": "source.asc,document_id.asc"})}

    def existing_raw_keys(self, pairs: list[tuple[str, str]]) -> "set[str] | None":
        """정본(raw_signals)에 있는 문서 키. 조회가 한 번이라도 실패하면 None(모르는 채로 쓰지 않는다)."""
        ids = {raw_signal_id_for(s, d): row_key(s, d) for s, d in pairs}
        found: set[str] = set()
        ordered = sorted(ids)
        for i in range(0, len(ordered), 150):
            chunk = ordered[i:i + 150]
            try:
                rows = self._call("GET", "raw_signals", params={
                    "select": "raw_signal_id", "raw_signal_id": "in.(" + ",".join(chunk) + ")"})
            except QueueStoreError:
                return None
            for r in rows if isinstance(rows, list) else []:
                key = ids.get(str(r.get("raw_signal_id") or ""))
                if key:
                    found.add(key)
        return found

    def get_handoff_manifest(self, handoff_date: str) -> "dict[str, Any] | None":
        rows = self._call("GET", "intake_handoffs", params={
            "select": "handoff_date,status,manifest,payload_sha256,window_days",
            "handoff_date": f"eq.{handoff_date}", "limit": "1"})
        return rows[0] if isinstance(rows, list) and rows else None

    def closed_out_of_scope(self, start: str, end: str) -> list[dict[str, Any]]:
        """코드가 범위 밖 483 으로 닫은 대기열 행(수집일 창 안)."""
        return self._get_all("intake_queue", {
            "select": "source,document_id,state_reason",
            "state": f"eq.{STATE_SKIPPED}", "state_reason": "like.out_of_scope_483*",
            "first_seen_run_date": f"gte.{start}",
            "and": f"(first_seen_run_date.lte.{end})"})

    def upsert_handoff(self, row: dict[str, Any]) -> None:
        self._call("POST", "intake_handoffs", params={"on_conflict": "handoff_date"}, body=[row],
                   prefer="resolution=merge-duplicates,return=minimal")

    def patch_handoff(self, handoff_date: str, fields: dict[str, Any]) -> None:
        self._call("PATCH", "intake_handoffs", params={"handoff_date": f"eq.{handoff_date}"},
                   body=fields, prefer="return=minimal")

    def stale_prior_open(self, keep_date: str) -> int:
        """오늘 것이 아닌 open handoff 를 stale 로 닫는다('항상 open 1개'). 행 상태는 건드리지 않는다."""
        got = self._call("PATCH", "intake_handoffs",
                         params={"status": f"eq.{HANDOFF_OPEN}", "handoff_date": f"neq.{keep_date}"},
                         body={"status": HANDOFF_STALE, "superseded_by": keep_date,
                               "updated_at": _now_iso()},
                         prefer="return=representation")
        return len(got) if isinstance(got, list) else 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────── 조립 ───────────────────────────────

def enqueue_items(store: QueueStore, items: Iterable[Any], run_date: date,
                  collected_at: datetime, *, enqueued_by: str = "intake") -> dict[str, int]:
    """오늘 노션 대기열에 새로 들어간 항목을 DB 대기열에도 넣는다.

    정본(raw_signals)에 없는 문서는 FK 로 거부된다 — 그 건수는 `failed` 로 드러난다.
    한 묶음이 거부되면 한 건씩 다시 넣어 어느 문서가 빠졌는지 남긴다.
    """
    seen = to_kst(collected_at) or datetime.now(KST)
    rows, keys = [], set()
    for it in items:
        source, doc = getattr(it, "source", ""), getattr(it, "document_id", "")
        if not source or not doc or (source, doc) in keys:
            continue
        keys.add((source, doc))
        rows.append({"source": source, "document_id": doc,
                     "first_seen_run_date": run_date.isoformat(),
                     "first_seen_at": seen.isoformat(), "enqueued_by": enqueued_by})
    summary = {"requested": len(rows), "inserted": 0, "failed": 0}
    if not rows:
        return summary
    try:
        summary["inserted"] = store.enqueue(rows)
        return summary
    except QueueStoreError:
        pass
    for row in rows:
        try:
            summary["inserted"] += store.enqueue([row])
        except QueueStoreError as e:
            summary["failed"] += 1
            log("WARN", f"대기열 넣기 실패(정본에 없거나 표 오류) "
                        f"{row['source']}::{row['document_id']}: {e}")
    return summary


def heal_from_notion(store: QueueStore, notion_payload: dict[str, Any],
                     generated_at: datetime) -> dict[str, int]:
    """노션 handoff 에는 있는데 DB 대기열에 없는 행을 다시 넣는다(매일).

    대기열 넣기는 "그 실행에 노션 행이 새로 생긴 문서"에만 한 번 일어난다. 그날 넣기가 실패하면
    (표 오류·그림자 꺼짐·정본 저장 지연) 다음 날에는 노션 중복이라 다시 시도되지 않는다. 노션이
    대기열 정본인 동안에는 노션 handoff(=지금 Status=New 인 행 전부)가 기준이므로, 거기 있는 행은
    대기열에도 있어야 한다. 이미 있는 행은 상태가 무엇이든 건드리지 않는다.
    수집일은 노션 행의 Run Date·Collected At 을 쓴다(둘 다 없으면 넣지 않고 failed 로 센다 —
    지어내지 않는다).
    """
    wanted: dict[str, dict[str, Any]] = {}
    for row in notion_payload.get("rows") or []:
        if isinstance(row, dict) and row.get("source") and row.get("document_id"):
            wanted.setdefault(row_key(row["source"], row["document_id"]), row)
    summary = {"checked": len(wanted), "missing": 0, "inserted": 0, "failed": 0}
    if not wanted:
        return summary
    have = store.queue_rows([(r["source"], r["document_id"]) for r in wanted.values()])
    for key in sorted(set(wanted) - set(have)):
        row = wanted[key]
        summary["missing"] += 1
        seen = to_kst(row.get("collected_at"))
        run_date = str(row.get("run_date") or "")[:10] or (seen.date().isoformat() if seen else "")
        if not run_date:
            summary["failed"] += 1
            continue
        if seen is None:
            seen = datetime.fromisoformat(run_date + "T00:00:00").replace(tzinfo=KST)
        try:
            summary["inserted"] += store.enqueue([{
                "source": row["source"], "document_id": row["document_id"],
                "first_seen_run_date": run_date, "first_seen_at": seen.isoformat(),
                "enqueued_by": "heal"}])
        except QueueStoreError as e:
            summary["failed"] += 1
            log("WARN", f"대기열 보충 실패(정본에 없거나 표 오류) {key}: {e}")
    return summary


def _read_json(path: str) -> "dict[str, Any] | None":
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def publish_files(repo_root: str, handoff_date: str) -> tuple["dict | None", "dict | None", str]:
    """그 날짜의 main 발행 파일(델타, 발행본, 델타 경로)."""
    tag = handoff_date.replace("-", "_")
    delta_rel = f"web/data/deltas/delta_{tag}.json"
    delta = _read_json(os.path.join(repo_root, *delta_rel.split("/")))
    brief = _read_json(os.path.join(repo_root, "web", "data", "briefs", f"brief_web_{tag}.json"))
    return delta, brief, delta_rel


def reconcile(store: QueueStore, repo_root: str, run_date: date, *, apply: bool = True,
              lookback_days: int = RECONCILE_LOOKBACK_DAYS) -> dict[str, Any]:
    """main 에 델타가 있는 handoff 마다 행 상태를 다시 계산해 표에 맞춘다(멱등).

    날짜 오름차순으로 처리한다. 어떤 행을 더 나중 handoff 가 이미 판정했으면 건드리지 않는다
    (보류됐다가 다음 주에 실린 행을 옛 handoff 의 '보류'로 되돌리지 않기 위해).
    `apply=False` 면 계산만 하고 쓰지 않는다. 반환에 날짜별 계산 결과를 담는다.
    """
    start = (run_date - timedelta(days=lookback_days)).isoformat()
    summary: dict[str, Any] = {"handoffs": 0, "rows": 0, "changed": 0, "states": {}, "by_date": {}}
    for h in store.handoffs_since(start):
        handoff_date = str(h.get("handoff_date") or "")[:10]
        delta, brief, delta_rel = publish_files(repo_root, handoff_date)
        if delta is None:
            continue
        manifest = h.get("manifest") if isinstance(h.get("manifest"), list) else []
        # 편집팀이 읽은 handoff 와 DB 가 기록한 handoff 가 같은지(델타의 handoff_ref — git handoff 길만 싣는다).
        # 다르면 행 목록이 어긋났을 수 있다: 편집팀이 못 본 행이 '미채택'으로 닫힌다. 경고로 드러낸다.
        ref = delta.get("handoff_ref") if isinstance(delta.get("handoff_ref"), dict) else {}
        if ref.get("sha256") and h.get("payload_sha256") and ref["sha256"] != h["payload_sha256"]:
            summary.setdefault("ref_mismatch", []).append(handoff_date)
        states = compute_states(manifest, delta, brief, handoff_date)
        summary["handoffs"] += 1
        summary["rows"] += len(states)
        summary["by_date"][handoff_date] = states
        for verdict in states.values():
            summary["states"][verdict["state"]] = summary["states"].get(verdict["state"], 0) + 1
        if not apply:
            continue
        keys = [(r.get("source", ""), r.get("document_id", "")) for r in manifest]
        current = store.queue_rows(keys)
        for key, verdict in states.items():
            row = current.get(key)
            if row is None:
                continue                     # 대기열에 없는 행(그림자 이전 주) — 만들지 않는다
            prior = str(row.get("handoff_date") or "")[:10]
            if prior and prior > handoff_date:
                continue
            want = {"state": verdict["state"], "state_reason": verdict["reason"],
                    "handoff_date": handoff_date,
                    "brief_publish_date": verdict["brief_publish_date"] or None}
            have = {"state": row.get("state"), "state_reason": row.get("state_reason"),
                    "handoff_date": prior or None,
                    "brief_publish_date": (str(row.get("brief_publish_date") or "")[:10] or None)}
            if want == have:
                continue
            source, doc = key.split("::", 1)
            store.set_state(source, doc, dict(want, state_computed_at=_now_iso()))
            summary["changed"] += 1
        if h.get("status") != HANDOFF_CONSUMED or h.get("consumed_evidence") != delta_rel:
            store.patch_handoff(handoff_date, {"status": HANDOFF_CONSUMED, "superseded_by": None,
                                               "consumed_evidence": delta_rel,
                                               "updated_at": _now_iso()})
    return summary


def load_candidates(store: QueueStore, run_date: date, window_days: int,
                    snapshot_keys: Iterable[str], *, apply: bool = True
                    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """대기열에서 이번 handoff 후보를 읽어 (행 스냅샷들, 원문 캐시, 집계)로 돌려준다.

    범위 밖 483 은 여기서 skipped 로 닫는다(노션의 Status=Skipped 대응). 정본이 없는 행은
    싣지 않고 집계에 남긴다(FK 가 있어 정상이라면 0).
    """
    start, end = candidate_window(run_date, window_days)
    queue = store.open_candidates(start, end)
    keys = [(q.get("source", ""), q.get("document_id", "")) for q in queue]
    records = store.raw_signals(keys)
    snapshot_keys = tuple(snapshot_keys)
    rows: list[dict[str, Any]] = []
    raws: dict[str, dict[str, Any]] = {}
    stats: dict[str, Any] = {"queue": len(queue), "pre_skipped": 0, "missing_raw": 0,
                             "missing_raw_keys": [], "no_raw_body": 0}
    for q in queue:
        key = row_key(q.get("source", ""), q.get("document_id", ""))
        rec = records.get(key)
        if rec is None:
            stats["missing_raw"] += 1
            stats["missing_raw_keys"].append(key)
            continue
        snap, raw = snapshot_from_raw_signal(rec, q, snapshot_keys)
        reason = pre_handoff_skip_reason(snap["source"], raw)
        if reason:
            stats["pre_skipped"] += 1
            if apply:
                store.set_state(snap["source"], snap["document_id"], {
                    "state": STATE_SKIPPED, "state_reason": reason,
                    "state_computed_at": _now_iso()})
            continue
        rows.append(snap)
        if raw:
            raws[key] = raw
        else:
            stats["no_raw_body"] += 1
    stats["handoff"] = len(rows)
    return rows, raws, stats


def build_db_handoff(store: QueueStore, run_date: date, window_days: int,
                     display_window_days: int, generated_at: datetime, *,
                     silent_sources: Iterable[str] = (), apply: bool = True,
                     web_brief_dir: "str | None" = None
                     ) -> tuple[dict[str, Any], dict[str, Any]]:
    """대기열 + 정본만으로 handoff v2 payload 를 만든다(노션 호출 없음).

    카드 조립은 노션 경로와 **같은 함수**를 쓴다(`enrich_rows_with_raw` →
    `build_routine_handoff_payload_v2`). 달라지는 것은 재료뿐이다.
    """
    import grm_handoff as gh   # 순환 import 방지 — grm_handoff 는 이 모듈을 모른다

    rows, raws, stats = load_candidates(store, run_date, window_days,
                                        gh._HANDOFF_V2_ROW_KEEP, apply=apply)
    # 원문이 없는 행은 빈 dict 를 주면 안 된다(노션 fetch 폴백으로 빠져 token 없이 실패 처리된다).
    # 그 폴백이 곧 '원문 미확보 → Evidence B·Error' 강등이고, 그것이 올바른 결과다.
    enriched, _ = gh.enrich_rows_with_raw("", rows, inmemory_raw=raws, sleep_s=0)
    payload = gh.build_routine_handoff_payload_v2(enriched, run_date, display_window_days,
                                                  generated_at, silent_sources=silent_sources)
    if web_brief_dir:
        gh.emit_web_brief_file(enriched, run_date, display_window_days, web_brief_dir)
    return payload, stats


def record_handoff(store: QueueStore, payload: dict[str, Any], window_days: int,
                   generated_at: datetime) -> dict[str, Any]:
    """오늘 handoff 를 표에 남기고(open), 이전 open 을 stale 로 닫는다.

    **이미 닫힌 날짜는 다시 쓰지 않는다(동결).** 월요일 델타가 main 에 들어와 consumed 가 된 뒤
    같은 날 수집을 다시 돌리면, 그때의 후보는 '남은 새 행'뿐이라 그대로 덮어쓰면 그 호의 행 목록이
    사라진다 — 발행본이 늦게 들어와도 상태를 확정할 근거가 없어진다(노션 길의 P1 부활 금지 가드 대응).
    그 실행의 새 행은 대기열에 new 로 남아 다음 날 handoff 에 들어간다.
    반환 {"staled": 닫은 건수, "frozen": 동결돼 쓰지 않았으면 그 상태(아니면 "")}.
    """
    handoff_date = payload["run_date_kst"]
    existing = store.get_handoff(handoff_date)
    if existing is not None and existing.get("status") != HANDOFF_OPEN:
        return {"staled": 0, "frozen": str(existing.get("status") or "")}
    if existing is not None and is_publish_day(handoff_date) and not handoff_refresh_requested():
        # 발행 요일(월)의 handoff 는 **첫 기록이 동결**된다. 편집팀은 아침에 이 기록의 사본을 읽고 일한다.
        # 그 뒤 같은 날 수집을 다시 돌려 행 목록을 바꾸면, 편집팀이 못 본 행이 그 호의 '미채택'으로 닫힌다.
        # 재실행의 새 행은 대기열에 new 로 남아 화요일 handoff 에 들어간다.
        # 일부러 다시 만들 때(새벽 수집이 일부 소스 실패로 끝남 — 편집팀이 읽기 전)는
        # GRM_QUEUE_HANDOFF_REFRESH=true 로 수집을 다시 돌린다. 닫힌(consumed) 기록은 그래도 못 바꾼다.
        return {"staled": 0, "frozen": FROZEN_PUBLISH_DAY}
    staled = store.stale_prior_open(handoff_date)
    store.upsert_handoff({
        "handoff_date": handoff_date,
        "generated_at": (to_kst(generated_at) or datetime.now(KST)).isoformat(),
        "status": HANDOFF_OPEN,
        "superseded_by": None,
        "consumed_evidence": "",
        "window_days": window_days,
        "row_count": payload["row_count"],
        "payload_sha256": payload_sha256(payload),
        "manifest": build_manifest(payload),
        "updated_at": _now_iso(),
    })
    return {"staled": staled, "frozen": ""}


def handoff_ref(payload: dict[str, Any]) -> dict[str, Any]:
    """handoff payload → 데스크가 `desk.json` 에 적을 참조(날짜·지문·행 수). 지문은 DB 기록과 같은 산식."""
    return {"date": str(payload.get("run_date_kst") or ""),
            "sha256": payload_sha256(payload),
            "rows": len(payload.get("rows") or [])}


def handoff_file_name(run_date: date, frozen: str = "") -> str:
    """DB handoff 파일 이름. 그 날짜가 이미 닫힌 뒤의 재실행분은 `.rerun` 을 붙인다.

    `grm-queue-handoff-mirror.yml` 은 `handoff_db_YYYY-MM-DD.json` 만 브랜치에 복사한다. 닫힌 뒤
    재실행분(남은 새 행뿐인 작은 payload)이 같은 이름으로 나가면 발행에 쓰인 날짜의 파일을 덮어쓴다.
    """
    suffix = ".rerun" if frozen else ""
    return f"handoff_db_{run_date.isoformat()}{suffix}.json"


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def run_shadow(supabase: tuple[str, str], *, run_date: date, generated_at: datetime,
               window_days: int, display_window_days: int, new_items: Iterable[Any] = (),
               notion_payload: "dict[str, Any] | None" = None,
               notion_status_lookup: "Callable[[str, list[str]], dict[str, str]] | None" = None,
               silent_sources: Iterable[str] = (), repo_root: str = ".",
               out_dir: "str | None" = None, store: "QueueStore | None" = None) -> dict[str, Any]:
    """그림자 한 바퀴: 넣기 → 상태 다시 계산 → DB handoff → 노션 것과 비교 → 파일로 남기기.

    `notion_status_lookup(handoff_date, keys)` 를 주면 최근 consumed 호의 계산 상태를 노션 Status 와
    비교한다. 어느 단계가 실패해도 예외를 올리지 않고 `warnings` 에 적는다(발행 경로 보호).
    """
    out_dir = out_dir or shadow_dir()
    report: dict[str, Any] = {"run_date": run_date.isoformat(), "warnings": []}
    try:
        store = store or QueueStore(supabase[0], supabase[1])
    except QueueStoreError as e:
        report["warnings"].append(f"대기열 표 접근 불가: {e}")
        return report

    try:
        report["enqueue"] = enqueue_items(store, new_items, run_date, generated_at)
        if report["enqueue"]["failed"]:
            report["warnings"].append(f"대기열에 못 넣은 문서 {report['enqueue']['failed']}건")
    except QueueStoreError as e:
        report["warnings"].append(f"대기열 넣기 실패: {e}")

    if notion_payload is not None:
        try:
            report["heal"] = heal_from_notion(store, notion_payload, generated_at)
            if report["heal"]["failed"]:
                report["warnings"].append(f"대기열 보충 실패 {report['heal']['failed']}건")
        except QueueStoreError as e:
            report["warnings"].append(f"대기열 보충 실패: {e}")

    reconciled: dict[str, Any] = {}
    try:
        reconciled = reconcile(store, repo_root, run_date)
        report["reconcile"] = {k: v for k, v in reconciled.items() if k != "by_date"}
        for d in reconciled.get("ref_mismatch") or []:
            report["warnings"].append(
                f"{d} 호: 편집팀이 읽은 handoff 지문이 DB 기록과 다르다 — 행 상태를 사람이 확인할 것")
    except QueueStoreError as e:
        report["warnings"].append(f"상태 다시 계산 실패: {e}")

    payload = None
    try:
        payload, stats = build_db_handoff(store, run_date, window_days, display_window_days,
                                          generated_at, silent_sources=silent_sources,
                                          web_brief_dir=out_dir)
        report["candidates"] = {k: v for k, v in stats.items() if k != "missing_raw_keys"}
        if stats["missing_raw"]:
            report["warnings"].append(
                "정본에 없는 대기열 행 " + ", ".join(stats["missing_raw_keys"][:10]))
        start = (run_date - timedelta(days=window_days)).isoformat()
        aged = store.aged_open_count(start)
        if aged:
            report["warnings"].append(f"창({window_days}일) 밖에 남은 미소비 행 {aged}건")
        recorded = record_handoff(store, payload, window_days, generated_at)
        report["staled"] = recorded["staled"]
        report["handoff_frozen"] = recorded["frozen"]
        _write(os.path.join(out_dir, handoff_file_name(run_date, recorded["frozen"])),
               json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    except Exception as e:  # noqa: BLE001 — 그림자가 수집을 죽이면 안 된다
        report["warnings"].append(f"DB handoff 생성 실패: {type(e).__name__}: {truncate(str(e), 160)}")

    if payload is not None and notion_payload is not None:
        try:
            report["compare"] = compare_payloads(notion_payload, payload)
            _explain_only_notion(store, report["compare"])
        except Exception as e:  # noqa: BLE001
            report["warnings"].append(f"handoff 비교 실패: {type(e).__name__}")

    if notion_status_lookup is not None:
        report["state_compare"] = []
        for handoff_date in sorted(reconciled.get("by_date") or {})[-2:]:
            states = reconciled["by_date"][handoff_date]
            try:
                statuses = notion_status_lookup(handoff_date, sorted(states))
                st = compare_states(states, statuses)
                st["handoff_date"] = handoff_date
                report["state_compare"].append(st)
            except Exception as e:  # noqa: BLE001
                report["warnings"].append(f"{handoff_date} 상태 비교 실패: {type(e).__name__}")

    try:
        _write(os.path.join(out_dir, f"queue_shadow_{run_date.isoformat()}.json"),
               json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        md = render_report_md(report)
        _write(os.path.join(out_dir, f"queue_shadow_{run_date.isoformat()}.md"), md)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("\n" + md)
    except OSError as e:
        report["warnings"].append(f"그림자 보고서 쓰기 실패: {type(e).__name__}")
    for w in report["warnings"]:
        log("WARN", f"대기열 그림자: {w}")
    cmp_ = report.get("compare") or {}
    log("INFO", "대기열 그림자 완료: "
                f"handoff {(report.get('candidates') or {}).get('handoff', '?')}행 · "
                f"노션과 공통 {cmp_.get('common', '?')} · 칸까지 같음 {cmp_.get('identical', '?')} · "
                f"노션에만 {len(cmp_.get('only_notion') or [])} · DB에만 {len(cmp_.get('only_db') or [])}")
    return report


def notion_status_map(token: str, db_id: str, handoff_date: str,
                      window_days: int = 30) -> dict[str, str]:
    """그 호의 수집 창에 있는 노션 Intake 행의 현재 Status(비교 전용 — 읽기만 한다).

    같은 문서가 노션에 두 번 들어간 경우(재유입) 수집일이 늦은 행의 값을 쓴다.
    """
    return {k: v["status"] for k, v in
            notion_rows_in_window(token, db_id, handoff_date, window_days).items()}


def notion_rows_in_window(token: str, db_id: str, handoff_date: str,
                          window_days: int = 30) -> dict[str, dict[str, str]]:
    """그 호의 수집 창에 있는 노션 Intake 행 — {source::document_id: {"page_id","status"}}. 읽기만."""
    import grm_handoff as gh
    from grm_notion import NOTION_DB_QUERY_URL_TPL, PROP_RUN_DATE

    end = date.fromisoformat(handoff_date)
    body: dict[str, Any] = {"filter": {"and": [
        {"property": PROP_RUN_DATE, "date": {"on_or_after": (end - timedelta(days=window_days)).isoformat()}},
        {"property": PROP_RUN_DATE, "date": {"on_or_before": end.isoformat()}},
    ]}, "page_size": 100}
    best: dict[str, tuple[str, str, str]] = {}
    cursor = None
    for _ in range(60):
        if cursor:
            body["start_cursor"] = cursor
        data = gh.notion_api_request("POST", NOTION_DB_QUERY_URL_TPL.format(db_id=db_id), token,
                                     body=body)
        for page in data.get("results", []):
            snap = gh._intake_page_snapshot(page)
            if snap["type_or_class"] == gh.TYPE_ROUTINE_HANDOFF or not snap["document_id"]:
                continue
            key = row_key(snap["source"], snap["document_id"])
            if key not in best or snap["run_date"] >= best[key][0]:
                best[key] = (snap["run_date"], snap["status"], snap["page_id"])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return {k: {"status": v[1], "page_id": v[2]} for k, v in best.items()}


# ─────────────────────────────── 과거 기록 채우기(history) ───────────────────────────────
# 대기열(intake_queue)은 그림자를 켠 11-10 수집분부터만 있다. 노션 Intake 의 지난 N일 행을 그대로 옮겨
# "언제 대기열에 들어왔고 어떻게 끝났나"의 기록을 과거로 늘린다. 쓰임: 무음 감시의 DB 기준 판정
# (소스별 마지막 수집일), 창 밖 미소비 경고, 그리고 노션을 끈 뒤에도 남는 처리 이력.
# 노션 Status 를 그대로 옮긴다(New→new · Processed→processed · Skipped→skipped · Error→error).
# 이미 대기열에 있는 행은 건드리지 않는다. 정본(raw_signals)에 없는 행은 넣지 않고 따로 센다.

HISTORY_ENQUEUED_BY = "history"
HISTORY_ROLLBACK_SQL = "delete from public.intake_queue where enqueued_by = 'history';"
_NON_QUEUE_TYPES = ("routine-handoff", "web-delta", "web-deep-delta")   # handoff·델타 예치 페이지


def notion_history_rows(token: str, db_id: str, start: date, end: date,
                        max_pages: int = 400) -> list[dict[str, str]]:
    """노션 Intake 에서 Run Date 가 [start, end] 인 행 전부(읽기만). 상한에 닿으면 예외 — 잘린 채 쓰지 않는다."""
    import grm_handoff as gh
    from grm_notion import NOTION_DB_QUERY_URL_TPL, PROP_RUN_DATE

    body: dict[str, Any] = {"filter": {"and": [
        {"property": PROP_RUN_DATE, "date": {"on_or_after": start.isoformat()}},
        {"property": PROP_RUN_DATE, "date": {"on_or_before": end.isoformat()}},
    ]}, "page_size": 100}
    out: list[dict[str, str]] = []
    cursor = None
    for _ in range(max_pages):
        if cursor:
            body["start_cursor"] = cursor
        data = gh.notion_api_request("POST", NOTION_DB_QUERY_URL_TPL.format(db_id=db_id), token,
                                     body=body)
        for page in data.get("results", []):
            snap = gh._intake_page_snapshot(page)
            out.append({k: str(snap.get(k) or "") for k in (
                "source", "document_id", "run_date", "collected_at", "status", "type_or_class")})
        if not data.get("has_more"):
            return out
        cursor = data.get("next_cursor")
    raise QueueStoreError(f"노션 조회가 상한({max_pages}쪽)에 닿았다 — 기간을 줄여 다시 돌릴 것")


def plan_history(notion_rows: list[dict[str, str]], queue_keys: set[str],
                 raw_keys: "set[str] | None") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """노션 행 → 대기열에 넣을 계획(순수 함수). 반환 (넣을 행, 집계).

    - handoff·델타 예치 페이지, 키(소스·문서 번호)나 수집일이 없는 행은 뺀다.
    - 같은 문서가 노션에 두 번 있으면 수집일이 늦은 행 하나만(대기열은 문서당 1행).
    - 이미 대기열에 있는 문서는 뺀다(`already_in_queue`).
    - `raw_keys` 를 주면 정본에 없는 문서를 뺀다(`no_raw` — FK 로 어차피 거부된다). None 이면 판정 생략.
    - 알 수 없는 Status 는 넣지 않는다(`unknown_status`).
    """
    stats: dict[str, Any] = {"notion_rows": len(notion_rows), "not_queue_rows": 0, "no_key_or_date": 0,
                             "duplicates_in_notion": 0, "already_in_queue": 0, "no_raw": 0,
                             "unknown_status": 0, "planned": 0, "by_source": {}, "by_state": {},
                             "no_raw_examples": [], "run_date_min": "", "run_date_max": ""}
    best: dict[str, dict[str, str]] = {}
    for r in notion_rows:
        if r.get("type_or_class") in _NON_QUEUE_TYPES or r.get("source") == "GRM Handoff":
            stats["not_queue_rows"] += 1
            continue
        if not r.get("source") or not r.get("document_id") or not r.get("run_date"):
            stats["no_key_or_date"] += 1
            continue
        key = row_key(r["source"], r["document_id"])
        if key in best:
            stats["duplicates_in_notion"] += 1
            if r["run_date"] < best[key]["run_date"]:
                continue
        best[key] = r
    plan: list[dict[str, Any]] = []
    for key in sorted(best):
        r = best[key]
        if key in queue_keys:
            stats["already_in_queue"] += 1
            continue
        if raw_keys is not None and key not in raw_keys:
            stats["no_raw"] += 1
            if len(stats["no_raw_examples"]) < 10:
                stats["no_raw_examples"].append(key)
            continue
        state = NOTION_STATUS_TO_STATE.get(r.get("status", ""))
        if state is None:
            stats["unknown_status"] += 1
            continue
        run_date = r["run_date"][:10]
        seen = to_kst(r.get("collected_at")) or datetime.fromisoformat(run_date + "T00:00:00").replace(tzinfo=KST)
        plan.append({"source": r["source"], "document_id": r["document_id"],
                     "first_seen_run_date": run_date, "first_seen_at": seen.isoformat(),
                     "enqueued_by": HISTORY_ENQUEUED_BY, "state": state,
                     "state_reason": "" if state == STATE_NEW else "notion_history"})
        stats["by_source"][r["source"]] = stats["by_source"].get(r["source"], 0) + 1
        stats["by_state"][state] = stats["by_state"].get(state, 0) + 1
    stats["planned"] = len(plan)
    if plan:
        stats["run_date_min"] = min(p["first_seen_run_date"] for p in plan)
        stats["run_date_max"] = max(p["first_seen_run_date"] for p in plan)
    stats["by_source"] = dict(sorted(stats["by_source"].items()))
    return plan, stats


def backfill_history(store: Any, token: str, db_id: str, *, end: date, days: int, apply: bool = False,
                     reader: "Callable[..., list[dict[str, str]]] | None" = None,
                     raw_lookup: "Callable[[list[tuple[str, str]]], set[str] | None] | None" = None
                     ) -> dict[str, Any]:
    """노션의 지난 `days` 일 행으로 대기열 과거 기록을 채운다. `apply=False` 면 계획과 집계만."""
    start = end - timedelta(days=days)
    rows = (reader or notion_history_rows)(token, db_id, start, end)
    queue_keys = store.all_queue_keys()
    pairs = sorted({(r["source"], r["document_id"]) for r in rows
                    if r.get("source") and r.get("document_id")
                    and row_key(r["source"], r["document_id"]) not in queue_keys})
    raw_keys = (raw_lookup or store.existing_raw_keys)(pairs)
    plan, stats = plan_history(rows, queue_keys, raw_keys)
    out: dict[str, Any] = {"window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
                           "apply": bool(apply), "queue_rows_before": len(queue_keys),
                           "raw_check": "ok" if raw_keys is not None else "정본 존재 확인 실패 — 적용 금지",
                           "stats": stats, "rollback_sql": HISTORY_ROLLBACK_SQL}
    if not apply:
        return out
    if raw_keys is None:
        out["applied"] = {"inserted": 0, "error": "정본 존재 확인을 못 해 쓰지 않았다"}
        return out
    out["applied"] = {"inserted": store.enqueue(plan)}
    return out


# 표 state → 노션 Status. deferred 는 없다 — 노션에서 보류는 "Status 를 바꾸지 않는다"이다.
STATE_TO_NOTION_STATUS = {
    STATE_PROCESSED: "Processed", STATE_SKIPPED: "Skipped", STATE_ERROR: "Error",
}
NOTION_SYNC_ENV = "ENABLE_QUEUE_NOTION_STATUS_SYNC"


def plan_notion_sync(states: dict[str, dict[str, str]], closed_keys: Iterable[str],
                     notion_rows: dict[str, dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, int]]:
    """코드가 계산한 상태를 노션 행 Status 에 옮길 계획(순수 함수).

    [대기열 3단계] 편집팀이 DB handoff 를 읽으면 행에 노션 page_id 가 없어 교열이 Status 를 손으로 찍을 수
    없다. 백업 길이 노션에 남아 있는 동안에는 노션 행 Status 가 맞아야 지난 행이 다음 주 노션 handoff 에
    다시 들어오지 않는다 → 코드가 찍는다.
    규칙
      - **지금 노션에서 New 인 행만** 바꾼다. 이미 Processed·Skipped·Error 인 행(사람·Routine·다른 도구가
        찍은 것)은 건드리지 않는다. 되돌리는 방향(→New)은 없다.
      - processed→Processed · skipped→Skipped · error→Error. deferred 는 바꾸지 않는다(다음 주 재유입).
      - `closed_keys`(범위 밖 483 으로 handoff 전에 닫은 행)는 Skipped.
      - 노션에서 찾지 못한 행은 건너뛰고 센다(만들지 않는다).
    반환 (할 일 목록 [{key,page_id,status}], 집계).
    """
    wanted: dict[str, str] = {}
    for key, verdict in states.items():
        target = STATE_TO_NOTION_STATUS.get(verdict.get("state", ""))
        if target:
            wanted[key] = target
    for key in closed_keys:
        wanted.setdefault(key, "Skipped")
    todo: list[dict[str, str]] = []
    stats = {"rows": len(states), "deferred": sum(1 for v in states.values()
                                                  if v.get("state") == STATE_DEFERRED),
             "not_in_notion": 0, "already_set": 0, "to_set": 0}
    for key in sorted(wanted):
        row = notion_rows.get(key)
        if row is None or not row.get("page_id"):
            stats["not_in_notion"] += 1
            continue
        if row.get("status") != "New":
            stats["already_set"] += 1
            continue
        todo.append({"key": key, "page_id": row["page_id"], "status": wanted[key]})
    stats["to_set"] = len(todo)
    for item in todo:
        stats[item["status"]] = stats.get(item["status"], 0) + 1
    return todo, stats


def notion_sync(store: Any, token: str, db_id: str, handoff_date: str, repo_root: str = ".", *,
                apply: bool = False, force: bool = False,
                rows_reader: "Callable[..., dict[str, dict[str, str]]] | None" = None,
                patch_status: "Callable[[str, str], None] | None" = None) -> dict[str, Any]:
    """그 호의 계산 상태를 노션 행 Status 에 찍는다. `apply=False` 면 계획만.

    대상은 **git handoff 로 만든 호**(델타에 `handoff_ref`)뿐이다. 노션 handoff 로 만든 호는 Routine·교열이
    직접 찍으므로 건드리지 않는다(`force=True` 로만 넘는다). 델타가 main 에 없으면 아무것도 하지 않는다.
    """
    out: dict[str, Any] = {"handoff_date": handoff_date, "apply": bool(apply), "skipped": "",
                           "patched": 0, "failed": 0}
    delta, brief, _ = publish_files(repo_root, handoff_date)
    if delta is None:
        out["skipped"] = "main 에 그 날짜 델타 없음"
        return out
    if not force and not isinstance(delta.get("handoff_ref"), dict):
        out["skipped"] = "노션 handoff 로 만든 호(handoff_ref 없음) — Routine·교열이 찍는다"
        return out
    record = store.get_handoff_manifest(handoff_date)
    if record is None or not isinstance(record.get("manifest"), list):
        out["skipped"] = "DB 에 그 날짜 handoff 기록 없음"
        return out
    states = compute_states(record["manifest"], delta, brief, handoff_date)
    window = int(record.get("window_days") or 30)
    start, end = candidate_window(date.fromisoformat(handoff_date), window)
    closed = [row_key(r.get("source", ""), r.get("document_id", ""))
              for r in store.closed_out_of_scope(start, end)]
    reader = rows_reader or notion_rows_in_window
    todo, stats = plan_notion_sync(states, closed, reader(token, db_id, handoff_date, window))
    out["stats"] = stats
    out["examples"] = [f"{t['key']} → {t['status']}" for t in todo[:10]]
    if not apply:
        return out
    if patch_status is None:
        import time

        import grm_handoff as gh
        from grm_notion import NOTION_PAGE_URL_TPL, PROP_STATUS, _select

        def patch_status(page_id: str, status: str) -> None:
            gh.notion_api_request("PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                                  body={"properties": {PROP_STATUS: _select(status)}})
            time.sleep(0.34)
    for item in todo:
        try:
            patch_status(item["page_id"], item["status"])
            out["patched"] += 1
        except Exception as e:  # noqa: BLE001 — 한 행 실패가 나머지를 막지 않는다(다시 돌리면 남은 New 만 찍는다)
            out["failed"] += 1
            log("WARN", f"노션 Status 기록 실패 {item['key']}: {type(e).__name__}")
    return out


def health_summary(report: dict[str, Any]) -> dict[str, Any]:
    """그림자 보고서 → health JSON 에 남길 짧은 요약(숫자·짧은 문자열만)."""
    enq, heal = report.get("enqueue") or {}, report.get("heal") or {}
    cmp_ = report.get("compare") or {}
    return {
        "enqueued": int(enq.get("inserted") or 0),
        "enqueue_failed": int(enq.get("failed") or 0),
        "healed": int(heal.get("inserted") or 0),
        "heal_missing": int(heal.get("missing") or 0),
        "heal_failed": int(heal.get("failed") or 0),
        "handoff_rows": int((report.get("candidates") or {}).get("handoff") or 0),
        "handoff_frozen": str(report.get("handoff_frozen") or ""),
        "only_notion": len(cmp_.get("only_notion") or []),
        "only_db": len(cmp_.get("only_db") or []),
        "warnings": len(report.get("warnings") or []),
    }


# ─────────────────────────────── 초기 이관(seed) ───────────────────────────────

def plan_seed(records: list[dict[str, Any]], exclusion: set[str], since: Any, *,
              skip_sources: Iterable[str] = ("Health Canada Inspection",),
              backfill_gap_days: int = 30) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """그림자 시작 전에 수집된 행을 대기열에 한 번 옮길 계획(재생 대조의 X1·X2·X4 규칙).

    새로 수집되는 행은 일일 수집이 직접 넣으므로 이 규칙이 필요 없다. 여기서만 쓴다.
      X1 노션을 거치지 않는 소스 · X2 수집 제외 장부 · X4 483 과거분(게시일이 수집일보다 30일 넘게 앞섬)
    범위 밖 483(X3)은 넣되, handoff 를 만들 때 skipped 로 닫힌다.
    """
    skip = set(skip_sources)
    plan: list[dict[str, Any]] = []
    stats = {"before_since": 0, "X1": 0, "X2": 0, "X4": 0, "undated": 0, "planned": 0}
    lower = to_kst(since)
    if lower is None:
        raise ValueError("since 를 시각으로 읽을 수 없음")
    for r in records:
        source, doc = str(r.get("source") or ""), str(r.get("document_id") or "")
        seen = to_kst(r.get("collected_at"))
        if not source or not doc or seen is None:
            stats["undated"] += 1
            continue
        if seen <= lower:
            stats["before_since"] += 1
            continue
        if source in skip:
            stats["X1"] += 1
            continue
        if row_key(source, doc) in exclusion:
            stats["X2"] += 1
            continue
        if source == SOURCE_FDA_483:
            try:
                gap = (seen.date() - date.fromisoformat(str(r.get("published_date") or "")[:10])).days
            except ValueError:
                gap = 0
            if gap > backfill_gap_days:
                stats["X4"] += 1
                continue
        plan.append({"source": source, "document_id": doc,
                     "first_seen_run_date": seen.date().isoformat(),
                     "first_seen_at": seen.isoformat(), "enqueued_by": "seed"})
    stats["planned"] = len(plan)
    return plan, stats


def _cli_store() -> QueueStore:
    return QueueStore(os.environ.get("SUPABASE_URL", ""),
                      os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))


def main(argv: "list[str] | None" = None) -> int:
    # 좁은 콘솔 인코딩(cp949 등)에서 한글·기호 출력이 죽지 않게 한다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="GRM 주간 대기열(DB) 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)
    seed = sub.add_parser("seed", help="그림자 시작 전 수집분을 대기열에 한 번 옮긴다")
    seed.add_argument("--since", required=True,
                      help="이 시각 뒤에 수집된 행만(직전 발행 호 handoff 생성 시각, ISO)")
    seed.add_argument("--apply", action="store_true", help="실제로 쓴다(기본은 계획만 출력)")
    ref = sub.add_parser("handoff-ref", help="handoff 파일의 날짜·지문·행 수(desk.json 의 handoff_ref)")
    ref.add_argument("path")
    syn = sub.add_parser("notion-sync", help="그 호의 계산 상태를 노션 행 Status 에 찍는다(git handoff 호만)")
    syn.add_argument("--date", required=True, help="발행일 YYYY-MM-DD")
    syn.add_argument("--repo-root", default=".")
    syn.add_argument("--db", default="", help="노션 Intake DB id(없으면 NOTION_DATABASE_ID·GRM_INTAKE_DB_ID)")
    syn.add_argument("--apply", action="store_true", help="실제로 쓴다(기본은 계획만 출력)")
    syn.add_argument("--force", action="store_true", help="handoff_ref 없는 호에도 적용(사람만)")
    his = sub.add_parser("backfill-history",
                         help="노션의 지난 N일 행으로 대기열 과거 기록을 채운다(상태 포함)")
    his.add_argument("--days", type=int, default=120)
    his.add_argument("--end", default="", help="끝 날짜 YYYY-MM-DD(기본 오늘 KST)")
    his.add_argument("--db", default="", help="노션 Intake DB id(없으면 NOTION_DATABASE_ID·GRM_INTAKE_DB_ID)")
    his.add_argument("--apply", action="store_true", help="실제로 쓴다(기본은 계획만 출력)")
    rec = sub.add_parser("reconcile", help="main 발행 파일로 행 상태를 다시 계산한다")
    rec.add_argument("--run-date", default="")
    rec.add_argument("--repo-root", default=".")
    rec.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "handoff-ref":          # DB 접근 없음 — 편집팀(클라우드)이 자격증명 없이 돌린다
        payload = _read_json(args.path)
        if payload is None or not isinstance(payload.get("rows"), list) or not payload.get("run_date_kst"):
            print(f"handoff 파일을 읽을 수 없다: {args.path}", file=sys.stderr)
            return 1
        print(json.dumps(handoff_ref(payload), ensure_ascii=False))
        return 0
    try:
        store = _cli_store()
        if args.cmd == "seed":
            plan, stats = plan_seed(store.raw_signals_ingested_since(args.since),
                                    store.exclusion_keys(), args.since)
            by_source: dict[str, int] = {}
            for row in plan:
                by_source[row["source"]] = by_source.get(row["source"], 0) + 1
            print(json.dumps({"since": args.since, "stats": stats, "by_source": by_source,
                              "apply": bool(args.apply)}, ensure_ascii=False, indent=1))
            if args.apply:
                print(json.dumps({"inserted": store.enqueue(plan)}))
            return 0
        if args.cmd == "backfill-history":
            token = os.environ.get("NOTION_TOKEN", "").strip()
            db_id = (args.db or os.environ.get("NOTION_DATABASE_ID", "")
                     or os.environ.get("GRM_INTAKE_DB_ID", "")).strip()
            if not token or not db_id:
                print("NOTION_TOKEN·노션 DB id 가 필요하다", file=sys.stderr)
                return 1
            if not 1 <= args.days <= 400:
                print("--days 는 1~400", file=sys.stderr)
                return 1
            end = date.fromisoformat(args.end) if args.end else datetime.now(KST).date()
            out = backfill_history(store, token, db_id, end=end, days=args.days, apply=bool(args.apply))
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return 1 if (out.get("applied") or {}).get("error") else 0
        if args.cmd == "notion-sync":
            token = os.environ.get("NOTION_TOKEN", "").strip()
            db_id = (args.db or os.environ.get("NOTION_DATABASE_ID", "")
                     or os.environ.get("GRM_INTAKE_DB_ID", "")).strip()
            if not token or not db_id:
                print("NOTION_TOKEN·노션 DB id 가 필요하다", file=sys.stderr)
                return 1
            apply = bool(args.apply)
            if apply and not (args.force or env_flag("ENABLE_QUEUE_NOTION_STATUS_SYNC")):
                print(f"{NOTION_SYNC_ENV} 가 꺼져 있어 쓰지 않는다(계획만)", file=sys.stderr)
                apply = False
            out = notion_sync(store, token, db_id, args.date, args.repo_root,
                              apply=apply, force=bool(args.force))
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return 1 if out.get("failed") else 0
        run_date = date.fromisoformat(args.run_date) if args.run_date else datetime.now(KST).date()
        out = reconcile(store, args.repo_root, run_date, apply=bool(args.apply))
        print(json.dumps({k: v for k, v in out.items() if k != "by_date"},
                         ensure_ascii=False, indent=1))
        return 0
    except QueueStoreError as e:
        print(f"대기열 표 오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
