#!/usr/bin/env python3
"""[483 상세 다시 읽기 2026-11-10] 이미 저장된 FDA 483 관찰 **상세만** 제자리에서 고친다.

지식 그래프 방향 전환 0단계. 수집기(`collect_fda_483`)의 상세 수리(쪽 넘김 이어붙이기·서명란
이름 제거·가림 표시 `[redacted]` 통일·상한 8000)는 새로 들어오는 문서에만 적용된다. 이 스크립트가
지정 기간(기본: 게시일 2026-08-10 이후) 문서를 다시 읽어 `fda_483_observations[].detail` 만 바꾼다.

★`backfill_483_ocr_recovery.py` 를 쓰지 않는 이유: 그쪽은 행 전체를 재구성하며
  `fda483_body_full`(심층 분석 원문)·`fda483_inspectors` 를 비운다(그 스크립트의 대상은 본문이
  없던 행이라 문제가 없었다). 여기서는 **detail 칸 외에는 아무것도 바꾸지 않는다.**
★번호·관찰 수는 바꾸지 않는다. 다시 읽은 관찰을 (번호, 표제)가 같은 기존 관찰에 대응시킨다. 옛 저장 상한에
  잘린 관찰(표제 280자+"..."·상세 1,200자)은 번호가 같고 새 판이 옛 앞부분으로 시작할 때만 대응시켜 표제·상세를
  잘리지 않은 판으로 바꾼다(`--truncated-only` 는 그런 문서만 고른다). 짝이 없는 관찰은 기존 상세를 그대로 두되
  서명란 이름·가림 표시만 정리한다.
★표제가 바뀐 관찰의 findings 행(finding_text = 표제)은 여기서 바꾸지 않는다 — finding_id 가 본문 해시라
  `backfill_wl_truncation_restore.py --source fda483` 계획 → 번역 outbox restore 경로로 영문·국문을 함께 바꾼다.
★원문: 저장된 `fda483_body_full` 이 있으면 그것을 쓴다(PDF 요청·OCR 없음). 없으면 PDF 를 다시 받는다.
★기본은 dry-run — 바뀔 관찰 수와 품질 지표(문장 온전·서명란 흔적·깨진 가림 표시)의 전후를 보고만 한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import findings_supabase_backfill as fsb
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_483

DEFAULT_SINCE = "2026-08-10"
_ALL_TIME = "1900-01-01"
_OBS_KEY = "fda_483_observations"
_COMPLETE_END_RE = re.compile(r"[.!?][)\"'”’]?$|\)$")
_GARBLED_MARK_RE = re.compile(r"[@©®]")


@dataclass
class QualityStats:
    observations: int = 0
    complete_sentence: int = 0
    signer_or_footer_trace: int = 0
    garbled_redaction: int = 0
    total_chars: int = 0


@dataclass
class DetailRefreshReport:
    dry_run: bool = True
    since: str = DEFAULT_SINCE
    candidates: int = 0
    from_body_full: int = 0
    from_pdf: int = 0
    no_text: int = 0
    docs_changed: int = 0
    observations_changed: int = 0
    observations_matched: int = 0
    observations_unmatched: int = 0
    deficiency_restored: int = 0
    truncated_only: bool = False
    truncated_before: int = 0
    truncated_after: int = 0
    patched: int = 0
    failed: int = 0
    before: QualityStats = field(default_factory=QualityStats)
    after: QualityStats = field(default_factory=QualityStats)
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    incomplete_samples: list[dict[str, Any]] = field(default_factory=list)


def _collector():
    import collect_fda_483 as c   # 지연 import — 테스트가 모듈 함수를 바꿔 끼울 수 있게
    return c


def detail_quality(detail: str) -> dict[str, bool]:
    """상세 문자열 하나의 품질 신호(순수 함수)."""
    c = _collector()
    d = (detail or "").strip()
    return {
        "complete": bool(_COMPLETE_END_RE.search(d)),
        "trace": bool(c._FDA483_FOOTER_RE.search(d) or c._SIGNER_TITLE_RE.search(d)),
        "garbled": bool(_GARBLED_MARK_RE.search(c._EMAIL_RE.sub(" ", d))),
    }


def _add(stats: QualityStats, detail: str) -> None:
    if not (detail or "").strip():
        return
    q = detail_quality(detail)
    stats.observations += 1
    stats.complete_sentence += q["complete"]
    stats.signer_or_footer_trace += q["trace"]
    stats.garbled_redaction += q["garbled"]
    stats.total_chars += len(detail)


def tidy_old_detail(detail: str) -> str:
    """다시 읽은 짝이 없는 기존 상세 — 서명란 이름 꼬리와 가림 표시만 정리(순수 함수)."""
    c = _collector()
    d = c.trim_detail_tail(detail)
    return c.normalize_483_redactions(d) if d else (detail or "")


TRUNC_DEFICIENCY_SUFFIX = "..."
# 옛 저장 상한(상세 1,200자·표제 280자+"...")에 걸린 관찰을 찾는 탐지 띠(판정 전용 — 저장은 원문 그대로).
TRUNC_DETAIL_BAND = (1150, 1205)
TRUNC_DEFICIENCY_BAND = (270, 290)
_PREFIX_PROBE = 200


def _norm(text: str) -> str:
    """비교용 정규화 — 가림 표시 통일 + 공백 접기."""
    c = _collector()
    return re.sub(r"\s+", " ", c.normalize_483_redactions(text or "")).strip()


def is_truncated_deficiency(deficiency: str) -> bool:
    d = deficiency or ""
    return d.endswith(TRUNC_DEFICIENCY_SUFFIX) and TRUNC_DEFICIENCY_BAND[0] <= len(d) <= TRUNC_DEFICIENCY_BAND[1]


def is_truncated_detail(detail: str) -> bool:
    return TRUNC_DETAIL_BAND[0] <= len(detail or "") <= TRUNC_DETAIL_BAND[1]


def has_truncated_observation(observations: list[dict[str, Any]]) -> bool:
    return any(is_truncated_deficiency(str(o.get("deficiency") or ""))
               or is_truncated_detail(str(o.get("detail") or "")) for o in observations)


def _restored_deficiency(old_def: str, new_def: str) -> bool:
    """옛 표제가 280자+"..." 절단이고 새 표제가 그 앞부분으로 시작하며 더 길다."""
    if not is_truncated_deficiency(old_def):
        return False
    head = _norm(old_def[:-len(TRUNC_DEFICIENCY_SUFFIX)])
    nd = _norm(new_def)
    return bool(head) and nd.startswith(head) and len(nd) > len(head)


def _extends_detail(old_detail: str, new_detail: str) -> bool:
    """새 상세가 옛 상세의 앞부분(정규화 200자)으로 시작하고 더 길다 — 같은 관찰의 잘리지 않은 판."""
    od, nd = _norm(old_detail), _norm(new_detail)
    probe = od[:_PREFIX_PROBE]
    if probe and nd.startswith(probe) and len(nd) > len(od):
        return True
    # [11-11] 옛 1,200자 절단 상세는 앞머리(머리말 조각·옛 가림 표기)가 새 판과 조금 달라 위 조건에
    # 걸리지 않았다(다시 읽기 뒤 42건 잔존). 옛 상세가 절단 띠 안이고, 그 **가운데 구간**(정규화
    # 300~600자)과 **끝 구간**(마지막 120자)이 새 상세에 그대로 있으며 새 판이 더 길면 같은 관찰로 본다.
    if not is_truncated_detail(old_detail) or len(nd) <= len(od):
        return False
    mid, tail = od[300:600], od[-120:]
    return len(mid) >= 200 and mid in nd and tail in nd


def refreshed_observations(old: list[dict[str, Any]], new: list[dict[str, Any]]
                           ) -> tuple[list[dict[str, Any]], int, int]:
    """기존 관찰 목록의 detail(과 잘린 표제)을 바꾼 새 목록 + (짝 맞은 수, 짝 없는 수). 순수 함수.

    짝 ① (number, deficiency) 가 같음.
       ② 번호가 같고 옛 표제가 280자+"..." 절단, 새 표제가 그 앞부분으로 시작 → 표제도 새 것으로.
          (그때 옛 상세가 표제 문장의 나머지였다면 새 상세가 빈 값일 수 있다 — 새 것을 그대로 쓴다.)
       ③ 번호가 같고 새 상세가 옛 상세의 앞부분으로 시작하며 더 김(옛 1,200자 절단) → 상세만.
    다시 읽은 상세가 비면(①·③) 기존 상세(정리본)를 둔다."""
    by_key = {(str(o.get("number")), str(o.get("deficiency") or "")): o for o in new}
    by_num: dict[str, list[dict[str, Any]]] = {}
    for o in new:
        by_num.setdefault(str(o.get("number")), []).append(o)
    out: list[dict[str, Any]] = []
    matched = unmatched = 0
    for o in old:
        num = str(o.get("number"))
        old_def = str(o.get("deficiency") or "")
        old_detail = str(o.get("detail") or "")
        hit = by_key.get((num, old_def))
        if hit is not None and str(hit.get("detail") or "").strip():
            matched += 1
            out.append({**o, "detail": str(hit["detail"])})
            continue
        same_num = by_num.get(num, [])
        restored = [n for n in same_num if _restored_deficiency(old_def, str(n.get("deficiency") or ""))]
        if len(restored) == 1:
            matched += 1
            n = restored[0]
            out.append({**o, "deficiency": str(n["deficiency"]), "detail": str(n.get("detail") or "")})
            continue
        extended = [n for n in same_num if _extends_detail(old_detail, str(n.get("detail") or ""))]
        if len(extended) == 1:
            matched += 1
            out.append({**o, "detail": str(extended[0]["detail"])})
            continue
        unmatched += 1
        out.append({**o, "detail": tidy_old_detail(old_detail)})
    return out, matched, unmatched


def _hints(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "establishment_type": str(raw.get("establishment_type") or ""),
        "fei_number": str(raw.get("fei_number") or ""),
        "firm_name": str(raw.get("firm") or ""),
    }


def _fetch_text(pdf_url: str) -> tuple[str, str]:
    return _collector()._fetch_fda483_pdf_text(pdf_url)


def _fetch_rows(base_url: str, service_key: str, since: str, until: str | None = None
                ) -> list[dict[str, Any]]:
    params = {"source": f"eq.{SOURCE_FDA_483}", "raw_json": f"like.*{_OBS_KEY}*"}
    if until:   # 게시일 [since, until) — 옛 문서 전체를 기간으로 나눠 돌린다(PDF·OCR 시간)
        params["and"] = f"(published_date.gte.{since},published_date.lt.{until})"
    else:
        params["published_date"] = f"gte.{since}"
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="raw_signal_id,document_id,raw_json", page_size=500,
        order="raw_signal_id.asc", extra_params=params,
    )


def _patch(base_url: str, service_key: str, rid: str, raw: dict[str, Any]) -> None:
    import requests
    resp = requests.patch(
        f"{base_url}/rest/v1/raw_signals",
        params={"raw_signal_id": f"eq.{rid}", "source": f"eq.{SOURCE_FDA_483}"},
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"},
        json={"raw_json": json.dumps(raw, ensure_ascii=False)},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"PATCH HTTP {resp.status_code}")


def run(
    rows: list[dict[str, Any]],
    *,
    dry_run: bool = True,
    since: str = DEFAULT_SINCE,
    limit: int | None = None,
    doc_ids: set[str] | None = None,
    truncated_only: bool = False,
    delay_seconds: float = 0.5,
    text_fn: Callable[[str], tuple[str, str]] = _fetch_text,
    patch_fn: Callable[[str, dict[str, Any]], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> DetailRefreshReport:
    c = _collector()
    report = DetailRefreshReport(dry_run=dry_run, since=since, truncated_only=truncated_only)
    for r in rows:
        if limit and report.candidates >= limit:
            break
        rid = str(r.get("raw_signal_id") or "")
        doc = str(r.get("document_id") or "")
        if doc_ids and doc not in doc_ids and rid not in doc_ids:
            continue
        raw = r.get("raw_json")
        raw = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        old = [o for o in (raw.get(_OBS_KEY) or []) if isinstance(o, dict)]
        if not old:
            continue
        if truncated_only and not has_truncated_observation(old):
            continue
        report.candidates += 1
        text = str(raw.get("fda483_body_full") or "")
        if text:
            report.from_body_full += 1
        else:
            pdf_url = str(raw.get("pdf_url") or "").strip()
            if not pdf_url:
                report.no_text += 1
                continue
            sleep_fn(delay_seconds)
            try:
                text, _status = text_fn(pdf_url)
            except Exception as exc:  # noqa: BLE001 — 건별 실패는 넘긴다(멱등 재실행)
                report.failed += 1
                report.errors.append(f"{doc}: fetch_raised:{type(exc).__name__}")
                continue
            if not text:
                report.no_text += 1
                continue
            report.from_pdf += 1
        try:
            new = c._extract_483_observations_from_text(text, _hints(raw))
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f"{doc}: extract_raised:{type(exc).__name__}")
            continue
        refreshed, matched, unmatched = refreshed_observations(old, new)
        report.observations_matched += matched
        report.observations_unmatched += unmatched
        changed = 0
        report.truncated_before += sum(has_truncated_observation([o]) for o in old)
        report.truncated_after += sum(has_truncated_observation([o]) for o in refreshed)
        for o_old, o_new in zip(old, refreshed):
            if str(o_old.get("deficiency") or "") != str(o_new.get("deficiency") or ""):
                report.deficiency_restored += 1
            _add(report.before, str(o_old.get("detail") or ""))
            _add(report.after, str(o_new.get("detail") or ""))
            nd = str(o_new.get("detail") or "").strip()
            if nd and not detail_quality(nd)["complete"] and len(report.incomplete_samples) < 60:
                report.incomplete_samples.append({
                    "doc": doc, "number": o_old.get("number"), "len": len(nd),
                    "source": "body_full" if raw.get("fda483_body_full") else "pdf",
                    "tail": nd[-200:]})
            if (str(o_old.get("detail") or "") != o_new["detail"]
                    or str(o_old.get("deficiency") or "") != str(o_new.get("deficiency") or "")):
                changed += 1
                if len(report.samples) < 12:
                    report.samples.append({
                        "doc": doc, "number": o_old.get("number"),
                        "before_len": len(str(o_old.get("detail") or "")),
                        "after_len": len(o_new["detail"]),
                        "before_tail": str(o_old.get("detail") or "")[-120:],
                        "after_tail": o_new["detail"][-160:],
                    })
        if not changed:
            continue
        report.docs_changed += 1
        report.observations_changed += changed
        if dry_run or patch_fn is None:
            continue
        try:
            patch_fn(rid, {**raw, _OBS_KEY: refreshed})
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f"{doc}: patch_failed:{type(exc).__name__}")
            continue
        report.patched += 1
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="저장된 FDA 483 관찰 상세만 다시 읽어 제자리 교체(기본 dry-run).")
    p.add_argument("--apply", action="store_true", default=False, help="실제 PATCH. 기본 dry-run.")
    p.add_argument("--since", default=None,
                   help=f"게시일 하한(YYYY-MM-DD). 기본 {DEFAULT_SINCE}, --truncated-only 면 기본 전체 기간.")
    p.add_argument("--until", default=None, help="게시일 상한(미포함, YYYY-MM-DD). 기간을 나눠 돌릴 때.")
    p.add_argument("--truncated-only", action="store_true", default=False,
                   help="옛 저장 상한(상세 1,200자·표제 280자+'...')에 걸린 관찰이 있는 문서만.")
    p.add_argument("--limit", type=int, default=None, help="처리 문서 수 상한.")
    p.add_argument("--doc-ids", default="", help="선택: document_id/raw_signal_id 목록(공백·쉼표).")
    p.add_argument("--delay-seconds", type=float, default=0.5, help="PDF 요청 간 대기(초).")
    p.add_argument("--report-path", help="JSON 리포트를 이 경로에도 기록.")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)
    creds = _resolve_credentials(args)
    if creds is None:
        print("backfill_483_detail_refresh: Supabase 자격 증명 필요", file=sys.stderr)
        return 2
    base_url, service_key = creds
    base_url = _normalize_base_url(base_url)
    since = args.since or (_ALL_TIME if args.truncated_only else DEFAULT_SINCE)
    rows = _fetch_rows(base_url, service_key, since, args.until)
    ids = {x for x in re.split(r"[\s,]+", args.doc_ids or "") if x}
    report = run(
        rows, dry_run=not args.apply, since=since, limit=args.limit, doc_ids=ids or None,
        truncated_only=args.truncated_only,
        delay_seconds=args.delay_seconds,
        patch_fn=lambda rid, raw: _patch(base_url, service_key, rid, raw),
    )
    payload = json.dumps(asdict(report), ensure_ascii=False, indent=2)
    print(payload)
    if args.report_path:
        Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
