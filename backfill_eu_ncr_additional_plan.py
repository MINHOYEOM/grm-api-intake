#!/usr/bin/env python3
"""EU GMP NCR `ncr_additional`(후속 설명) 소급 계획 생성 — 읽기 전용.

## 왜 필요한가

`findings_extractors._from_eu_gmp_ncr` 가 finding_text 끝에 "\\n\\nAdditional information: ..." 을
덧붙이도록 바뀌었지만(분류는 덧붙이기 전 텍스트로 고정), 이미 적재된 EU NCR findings(ncr_additional
이 있는 raw_signal 약 44행)는 옛 본문 그대로다. raw_signals 에는 ncr_additional 이 남아 있으므로
지금 추출기로 새 본문을 만들 수 있다.

## 대응 규칙(결정론, 모호하면 건너뛴다)

같은 raw_signal 의 기존 finding 중 **현재 finding_text 가 새 본문의 앞부분과 정확히 같고 새 본문이
더 긴 것**이 정확히 하나일 때만 기존 finding_id 에 대응시킨다(1레코드 1finding). finding_id 는
finding_text 해시라 재추출·INSERT 하면 id 가 바뀌어 임베딩·알림 로그(ON DELETE CASCADE)·딥링크가
끊기고 같은 NCR 이 2건이 된다 -- 그래서 **기존 id 에 새 본문을 얹는다**. 분류 열은 건드리지 않는다
(추출기가 분류를 덧붙이기 전 텍스트로 고정하므로 어차피 같다).

## 산출물과 흐름

계획 스키마는 WL 복원 계획과 같다(`grm-wl-truncation-restore-plan/v1`) -- findings_translate.py 의
`--export-restore`(build_restore_queue_supabase) 와 apply 서비스(옛 md5 대조 PATCH)는 출처 무관이라
그대로 읽는다. 이 스크립트는 **아무것도 쓰지 않는다**(service-role GET 만). 이미 반영된 행은
finding_text == 새 본문이라 후보에서 빠져 재실행해도 안전하다.

exit code: 자격증명/조회 실패 → 2 · 그 외 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import backfill_wl_truncation_restore as wl_plan
import findings_extractors as fe
import findings_supabase_backfill as fsb
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_EU_GMP_NCR

PLAN_SCHEMA_VERSION = wl_plan.PLAN_SCHEMA_VERSION   # 같은 스키마 -- 읽는 쪽이 출처를 가리지 않는다
md5_text = wl_plan.md5_text
_PAGE_SIZE = 1000
_RAW_BATCH = 40
_MAX_SAMPLES = 20


@dataclass
class AdditionalPlanReport:
    schema_version: str = PLAN_SCHEMA_VERSION
    generated_at: str = ""
    raw_signals_scanned: int = 0     # EU NCR raw_signals 전체
    with_additional: int = 0         # ncr_additional 이 비어 있지 않은 raw_signal
    mapped: int = 0                  # 계획에 실린 행
    already_applied: int = 0         # 기존 finding 이 이미 새 본문(재실행)
    no_finding: int = 0              # raw_signal 에 finding 없음(적재 전 -- 다음 추출이 새 본문으로 만든다)
    ambiguous: int = 0               # 접두 일치 finding 이 2건 이상
    unmatched: int = 0               # 접두 일치 finding 없음(본문이 달라짐)
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def has_additional(raw_signal: dict[str, Any]) -> bool:
    raw = fe._json_object(raw_signal.get("raw_json"))
    return bool(fe._compact(raw.get("ncr_additional")))


def plan_signal(
    existing: list[dict[str, Any]],
    new_findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """한 raw_signal 의 기존 findings → 새 본문 대응(순수 함수)."""
    counts = {"already_applied": 0, "ambiguous": 0, "unmatched": 0}
    new_texts = [str(f.get("finding_text") or "") for f in new_findings]
    items: list[dict[str, Any]] = []
    if not new_texts:
        counts["unmatched"] += 1
        return items, counts
    new = new_texts[0]            # EU NCR = 1 레코드 1 finding
    if any(str(r.get("finding_text") or "") == new for r in existing):
        counts["already_applied"] += 1
        return items, counts
    hits = [r for r in existing
            if str(r.get("finding_text") or "") and new.startswith(str(r["finding_text"]))
            and len(new) > len(str(r["finding_text"]))]
    if not hits:
        counts["unmatched"] += 1
    elif len(hits) > 1:
        counts["ambiguous"] += 1
    else:
        row = hits[0]
        old = str(row.get("finding_text") or "")
        items.append({
            "finding_id": str(row.get("finding_id") or ""),
            "raw_signal_id": str(row.get("raw_signal_id") or ""),
            "published_date": str(row.get("published_date") or ""),
            "finding_text_old_md5": md5_text(old),
            "finding_text": new,
            "old_chars": len(old),
            "new_chars": len(new),
            "legacy_match": True,   # 옛 본문 == 새 본문의 접두(WL 의 480자 재현 진단은 해당 없음)
        })
    return items, counts


def build_plan(
    raw_signals: list[dict[str, Any]],
    findings_rows: list[dict[str, Any]],
    *,
    extract: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], AdditionalPlanReport]:
    extract_fn = extract or fe.findings_from_raw_signal
    report = AdditionalPlanReport(generated_at=(now or datetime.now(timezone.utc)).isoformat())
    report.raw_signals_scanned = len(raw_signals)
    by_raw: dict[str, list[dict[str, Any]]] = {}
    for r in findings_rows:
        by_raw.setdefault(str(r.get("raw_signal_id") or ""), []).append(r)

    items: list[dict[str, Any]] = []
    for rs in sorted(raw_signals, key=lambda x: str(x.get("raw_signal_id") or "")):
        if not has_additional(rs):
            continue
        report.with_additional += 1
        rid = str(rs.get("raw_signal_id") or "")
        existing = by_raw.get(rid, [])
        if not existing:
            report.no_finding += 1
            continue
        try:
            new_findings = extract_fn(rs)
        except Exception as exc:  # noqa: BLE001 -- 건별 실패는 계속
            report.unmatched += 1
            report.errors.append(f"{rid}: extract_raised:{type(exc).__name__}")
            continue
        doc_items, counts = plan_signal(existing, new_findings)
        report.already_applied += counts["already_applied"]
        report.ambiguous += counts["ambiguous"]
        report.unmatched += counts["unmatched"]
        items.extend(doc_items)

    items.sort(key=lambda it: (it["published_date"], it["finding_id"]), reverse=True)
    report.mapped = len(items)
    report.samples = [
        {"finding_id": it["finding_id"], "old_chars": it["old_chars"], "new_chars": it["new_chars"]}
        for it in items[:_MAX_SAMPLES]
    ]
    plan = {"schema_version": PLAN_SCHEMA_VERSION, "report": asdict(report), "items": items}
    return plan, report


# ---------------------------------------------------------------------------
# Supabase (service-role, GET 만)
# ---------------------------------------------------------------------------


def fetch_eu_raw_signals(base: str, service_key: str) -> list[dict[str, Any]]:
    return fsb._fetch_all_pages(
        base, service_key, "raw_signals", select="*", page_size=_PAGE_SIZE,
        order="raw_signal_id.asc", extra_params={"source": f"eq.{SOURCE_EU_GMP_NCR}"},
    )


def fetch_findings_for_raw_ids(base: str, service_key: str, ids: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for start in range(0, len(ids), _RAW_BATCH):
        batch = ids[start:start + _RAW_BATCH]
        out.extend(fsb._fetch_all_pages(
            base, service_key, "findings",
            select="finding_id,raw_signal_id,published_date,finding_text",
            page_size=_PAGE_SIZE, order="finding_id.asc",
            extra_params={"raw_signal_id": "in.(" + ",".join(batch) + ")"},
        ))
    return out


def run(base_url: str, service_key: str) -> tuple[dict[str, Any] | None, AdditionalPlanReport, int]:
    base = _normalize_base_url(base_url)
    if base is None:
        return None, AdditionalPlanReport(errors=["SUPABASE_URL must start with https://"]), 2
    try:
        raws = fetch_eu_raw_signals(base, service_key)
        with_add = [r for r in raws if has_additional(r)]
        ids = sorted({str(r.get("raw_signal_id") or "") for r in with_add if r.get("raw_signal_id")})
        rows = fetch_findings_for_raw_ids(base, service_key, ids)
    except (RuntimeError, ValueError) as exc:
        return None, AdditionalPlanReport(errors=[str(exc)]), 2
    plan, report = build_plan(raws, rows)
    return plan, report, 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        description="EU GMP NCR ncr_additional 소급 계획 JSON 생성(읽기 전용).")
    p.add_argument("--output", required=True, help="계획 JSON 경로(Actions 아티팩트로 올린다).")
    p.add_argument("--report-path", help="요약 리포트 JSON 경로(항목 본문 제외).")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    args = p.parse_args(argv)
    creds = _resolve_credentials(args)
    if creds is None:
        print("backfill_eu_ncr_additional_plan: --supabase-url/--service-role-key 또는 "
              "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요.", file=sys.stderr)
        return 2
    plan, report, code = run(*creds)
    summary = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(summary)
    if args.report_path:
        Path(args.report_path).write_text(summary + "\n", encoding="utf-8")
    if plan is not None:
        Path(args.output).write_text(
            json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
