#!/usr/bin/env python3
"""옛 480자 상한으로 잘린 FDA Warning Letter findings 의 복원 계획(restore plan) 생성 — 읽기 전용.

## 왜 필요한가 (2026-10-31)

PR #653(2026-08-04) 전 WL 위반 블록은 `_WL_BLOCK_CHAR_CAP=480`·단어 경계에서 잘려 "…" 로
끝났다. 상한은 6000+문장 경계로 고쳤지만 **이미 적재된 행은 그대로**다 — 2021-01 ~ 2026-08-04
발행분 1,800행(문서 598건)이 위반 서술 앞 480자만 갖고 있어, 편지의 핵심인
"Your response is inadequate because…"·"In response to this letter, provide:" 가 영문·국문
모두 빠져 있다. 편지 전문은 raw_signals 에 남아 있으므로 지금 추출기로 다시 만들 수 있다.

## 왜 finding_id 를 새로 만들지 않나

finding_id 는 finding_text 해시라(grm_findings.finding_from_raw_signal) 재추출하면 id 가
바뀐다. finding_embeddings·firm_watch_notification_log 가 ON DELETE CASCADE 로 참조하고
딥링크도 id 를 쓴다 — 그래서 **기존 id 에 새 본문을 얹는다**(DELETE·INSERT 없음).

## 대응 규칙(결정론, 모호하면 건너뛴다)

같은 raw_signal 에서 지금 추출기(findings_extractors.findings_from_raw_signal)가 낸 블록 중
`옛 본문 - 끝 "…"` 로 **시작하는 블록이 정확히 하나**이고, 그 블록이 옛 본문보다 길 때만
대응시킨다. 두 후보가 같은 새 블록을 가리키면 둘 다 모호로 센다. 분류(category_code 등)는
건드리지 않는다 — 새 본문만 계획에 싣고, 쓰기 쪽(apply)도 finding_text·finding_text_ko·
translation_method 세 열만 PATCH 한다(분류는 여전히 옛 480 분류 상한 기준 그대로).
`legacy_match` 는 진단용이다: 새 블록에 옛 480자 절단 알고리즘
(`findings_extractors._legacy_cap_for_classify`)을 다시 적용하면 옛 본문과 글자 단위로
같은가 — 같으면 "그 블록이 그때 잘린 바로 그 블록"이라는 강한 증거다.

## 산출물과 흐름

이 스크립트는 **아무것도 쓰지 않는다**(DB·git 모두). service-role 키로 findings·raw_signals 를
읽어 계획 JSON(`grm-wl-truncation-restore-plan/v1`)을 만들 뿐이다. 워크플로
(`grm-wl-truncation-restore-plan.yml`)가 이 파일을 Actions 아티팩트로 올리고, 일일 번역 세션이
`gh run download` 로 받아 `findings_translate.py --export-restore` 로 아직 남은 항목만 고른 뒤
국문을 채워 일반 번역과 **같은 outbox 배치**에 restore 항목으로 싣는다. 실제 쓰기는
`findings_translate_apply_service.py` 가 옛 본문 md5 를 대조한 뒤에만 영문·국문을 한 PATCH 로
교체한다(국문이 비는 순간이 없다 — 공개 RLS 게이트가 빈 국문 행을 즉시 숨긴다).

exit code: 자격증명/조회 실패 → 2 · 그 외 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import findings_extractors as fe
import findings_supabase_backfill as fsb
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_WL

PLAN_SCHEMA_VERSION = "grm-wl-truncation-restore-plan/v1"
ELLIPSIS = "…"
# 옛 상한 480 + 말줄임표 — 그 위로 "…" 로 끝나는 행(2026-08-18+ 의 6000자 절단 36행)은 범위 밖.
DEFAULT_MAX_OLD_CHARS = 500
_PAGE_SIZE = 1000
_RAW_BATCH = 40          # raw_signal_id=in.(...) 한 번에 — URL 길이 방어
_MAX_SAMPLES = 20


def md5_text(text: str) -> str:
    """Postgres md5(finding_text) 와 같은 값(UTF-8 바이트의 md5 hex)."""
    return hashlib.md5(str(text).encode("utf-8")).hexdigest()


def is_truncated_candidate(finding_text: str, *, max_old_chars: int = DEFAULT_MAX_OLD_CHARS) -> bool:
    text = str(finding_text or "")
    return text.endswith(ELLIPSIS) and len(text) <= max_old_chars and len(text) > 1


def legacy_display_text(new_text: str) -> str:
    """새 블록에 옛 480자 표시 절단을 다시 적용한 결과(진단용 — 대응 판정에는 쓰지 않는다)."""
    core = new_text[:-1] if new_text.endswith(ELLIPSIS) else new_text
    return fe._legacy_cap_for_classify(core, bool(core) and core[-1] in ".?!\"")


@dataclass
class RestorePlanReport:
    schema_version: str = PLAN_SCHEMA_VERSION
    generated_at: str = ""
    max_old_chars: int = DEFAULT_MAX_OLD_CHARS
    candidates: int = 0             # "…" 로 끝나고 max_old_chars 이하인 WL 행
    documents: int = 0              # 그 행들의 raw_signal 수
    mapped: int = 0                 # 계획에 실린 행
    legacy_match: int = 0           # 그중 옛 절단 알고리즘 재현이 글자 단위로 일치
    unchanged: int = 0              # 새 블록이 옛 본문과 같음(짧은 미완결 블록 — 복원할 것 없음)
    ambiguous: int = 0              # 접두 일치 블록 2개 이상, 또는 두 행이 한 블록을 가리킴
    unmatched: int = 0              # 접두 일치 블록 없음(본문이 바뀌었거나 분해가 달라짐)
    missing_raw_signal: int = 0     # raw_signals 에 행이 없음
    new_chars_total: int = 0        # 계획에 실린 새 영문 글자 수 합(번역 일수 추정용)
    old_chars_total: int = 0
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def plan_document(
    candidates: list[dict[str, Any]],
    new_findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """한 문서 안에서 잘린 행 → 새 블록 대응(순수 함수). (계획 항목, 사유별 건수)."""
    counts = {"unchanged": 0, "ambiguous": 0, "unmatched": 0}
    new_texts = [str(f.get("finding_text") or "") for f in new_findings]
    proposals: list[tuple[dict[str, Any], int]] = []
    for row in candidates:
        old = str(row.get("finding_text") or "")
        prefix = old[:-1]
        if old in new_texts:
            counts["unchanged"] += 1
            continue
        hits = [i for i, t in enumerate(new_texts)
                if prefix and t.startswith(prefix) and len(t) > len(old)]
        if not hits:
            counts["unmatched"] += 1
        elif len(hits) > 1:
            counts["ambiguous"] += 1
        else:
            proposals.append((row, hits[0]))

    used: dict[int, int] = {}
    for _row, idx in proposals:
        used[idx] = used.get(idx, 0) + 1
    items: list[dict[str, Any]] = []
    for row, idx in proposals:
        if used[idx] > 1:
            counts["ambiguous"] += 1
            continue
        old = str(row.get("finding_text") or "")
        new = new_texts[idx]
        items.append({
            "finding_id": str(row.get("finding_id") or ""),
            "raw_signal_id": str(row.get("raw_signal_id") or ""),
            "published_date": str(row.get("published_date") or ""),
            "finding_text_old_md5": md5_text(old),
            "finding_text": new,
            "old_chars": len(old),
            "new_chars": len(new),
            "legacy_match": legacy_display_text(new) == old,
        })
    return items, counts


def build_plan(
    candidate_rows: list[dict[str, Any]],
    raw_signals: dict[str, dict[str, Any]],
    *,
    max_old_chars: int = DEFAULT_MAX_OLD_CHARS,
    extract: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], RestorePlanReport]:
    """후보 행 + raw_signal → 계획(순수 조립 — 네트워크 없음)."""
    extract_fn = extract or fe.findings_from_raw_signal
    report = RestorePlanReport(max_old_chars=max_old_chars,
                               generated_at=(now or datetime.now(timezone.utc)).isoformat())
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for row in candidate_rows:
        if not is_truncated_candidate(row.get("finding_text"), max_old_chars=max_old_chars):
            continue
        by_doc.setdefault(str(row.get("raw_signal_id") or ""), []).append(row)
    report.candidates = sum(len(v) for v in by_doc.values())
    report.documents = len(by_doc)

    items: list[dict[str, Any]] = []
    for raw_signal_id in sorted(by_doc):
        rows = by_doc[raw_signal_id]
        rs = raw_signals.get(raw_signal_id)
        if rs is None:
            report.missing_raw_signal += len(rows)
            continue
        try:
            new_findings = extract_fn(rs)
        except Exception as exc:  # noqa: BLE001 — 건별 실패는 계속
            report.unmatched += len(rows)
            report.errors.append(f"{raw_signal_id}: extract_raised:{type(exc).__name__}")
            continue
        doc_items, counts = plan_document(rows, new_findings)
        report.unchanged += counts["unchanged"]
        report.ambiguous += counts["ambiguous"]
        report.unmatched += counts["unmatched"]
        items.extend(doc_items)

    # 최신 발행분 먼저 — 번역 세션이 위에서부터 남은 용량만큼 가져간다.
    items.sort(key=lambda it: (it["published_date"], it["finding_id"]), reverse=True)
    report.mapped = len(items)
    report.legacy_match = sum(1 for it in items if it["legacy_match"])
    report.new_chars_total = sum(it["new_chars"] for it in items)
    report.old_chars_total = sum(it["old_chars"] for it in items)
    report.samples = [
        {"finding_id": it["finding_id"], "old_chars": it["old_chars"],
         "new_chars": it["new_chars"], "legacy_match": it["legacy_match"]}
        for it in items[:_MAX_SAMPLES]
    ]
    plan = {"schema_version": PLAN_SCHEMA_VERSION, "report": asdict(report), "items": items}
    return plan, report


# ---------------------------------------------------------------------------
# Supabase (service-role, GET 만)
# ---------------------------------------------------------------------------


def fetch_candidate_rows(base: str, service_key: str) -> list[dict[str, Any]]:
    """WL findings 중 finding_text 가 "…" 로 끝나는 행(길이 필터는 build_plan 이 건다)."""
    return fsb._fetch_all_pages(
        base, service_key, "findings",
        select="finding_id,raw_signal_id,published_date,finding_text",
        page_size=_PAGE_SIZE, order="finding_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_WL}", "finding_text": f"like.*{ELLIPSIS}"},
    )


def fetch_raw_signals_by_id(base: str, service_key: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), _RAW_BATCH):
        batch = ids[start:start + _RAW_BATCH]
        rows = fsb._fetch_all_pages(
            base, service_key, "raw_signals", select="*", page_size=_PAGE_SIZE,
            order="raw_signal_id.asc",
            extra_params={"raw_signal_id": "in.(" + ",".join(batch) + ")"},
        )
        for r in rows:
            out[str(r.get("raw_signal_id") or "")] = r
    return out


def run(base_url: str, service_key: str, *, max_old_chars: int = DEFAULT_MAX_OLD_CHARS,
        ) -> tuple[dict[str, Any] | None, RestorePlanReport, int]:
    base = _normalize_base_url(base_url)
    if base is None:
        report = RestorePlanReport(errors=["SUPABASE_URL must start with https://"])
        return None, report, 2
    try:
        rows = fetch_candidate_rows(base, service_key)
        rows = [r for r in rows
                if is_truncated_candidate(r.get("finding_text"), max_old_chars=max_old_chars)]
        ids = sorted({str(r.get("raw_signal_id") or "") for r in rows if r.get("raw_signal_id")})
        raw_signals = fetch_raw_signals_by_id(base, service_key, ids)
    except (RuntimeError, ValueError) as exc:
        return None, RestorePlanReport(errors=[str(exc)]), 2
    plan, report = build_plan(rows, raw_signals, max_old_chars=max_old_chars)
    return plan, report, 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        description="옛 480자 상한으로 잘린 FDA WL findings 의 복원 계획 JSON 생성(읽기 전용).")
    p.add_argument("--output", required=True, help="계획 JSON 경로(Actions 아티팩트로 올린다).")
    p.add_argument("--report-path", help="요약 리포트 JSON 경로(항목 본문 제외).")
    p.add_argument("--max-old-chars", type=int, default=DEFAULT_MAX_OLD_CHARS,
                   help=f"후보 상한 — 이 길이 이하이고 '…' 로 끝나는 행(기본 {DEFAULT_MAX_OLD_CHARS}).")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    args = p.parse_args(argv)
    creds = _resolve_credentials(args)
    if creds is None:
        print("backfill_wl_truncation_restore: --supabase-url/--service-role-key 또는 "
              "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요.", file=sys.stderr)
        return 2
    plan, report, code = run(*creds, max_old_chars=args.max_old_chars)
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
