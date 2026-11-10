#!/usr/bin/env python3
"""[483 상세 다시 읽기 2026-11-10] 이미 저장된 FDA 483 관찰 **상세만** 제자리에서 고친다.

지식 그래프 방향 전환 0단계. 수집기(`collect_fda_483`)의 상세 수리(쪽 넘김 이어붙이기·서명란
이름 제거·가림 표시 `[redacted]` 통일·상한 8000)는 새로 들어오는 문서에만 적용된다. 이 스크립트가
지정 기간(기본: 게시일 2026-08-10 이후) 문서를 다시 읽어 `fda_483_observations[].detail` 만 바꾼다.

★`backfill_483_ocr_recovery.py` 를 쓰지 않는 이유: 그쪽은 행 전체를 재구성하며
  `fda483_body_full`(심층 분석 원문)·`fda483_inspectors` 를 비운다(그 스크립트의 대상은 본문이
  없던 행이라 문제가 없었다). 여기서는 **detail 칸 외에는 아무것도 바꾸지 않는다.**
★표제(deficiency)·번호·관찰 수는 바꾸지 않는다. 다시 읽은 관찰을 (번호, 표제)가 **같은** 기존 관찰에만
  대응시키고, 짝이 없는 관찰은 기존 상세를 그대로 두되 서명란 이름·가림 표시만 정리한다.
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
    patched: int = 0
    failed: int = 0
    before: QualityStats = field(default_factory=QualityStats)
    after: QualityStats = field(default_factory=QualityStats)
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


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
    d = re.sub(r"\s+", " ", detail or "").strip()
    cut = c._signer_cut(d)
    if cut is not None:
        d = d[:cut].rstrip(" :-\t")
    return c.normalize_483_redactions(d) if d else (detail or "")


def refreshed_observations(old: list[dict[str, Any]], new: list[dict[str, Any]]
                           ) -> tuple[list[dict[str, Any]], int, int]:
    """기존 관찰 목록의 detail 만 바꾼 새 목록 + (짝 맞은 수, 짝 없는 수). 순수 함수.

    짝 = (number, deficiency) 가 같음. 다시 읽은 상세가 비면 기존 상세(정리본)를 둔다."""
    by_key = {(str(o.get("number")), str(o.get("deficiency") or "")): o for o in new}
    out: list[dict[str, Any]] = []
    matched = unmatched = 0
    for o in old:
        key = (str(o.get("number")), str(o.get("deficiency") or ""))
        hit = by_key.get(key)
        old_detail = str(o.get("detail") or "")
        if hit is not None and str(hit.get("detail") or "").strip():
            matched += 1
            detail = str(hit["detail"])
        else:
            unmatched += 1
            detail = tidy_old_detail(old_detail)
        out.append({**o, "detail": detail})
    return out, matched, unmatched


def _hints(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "establishment_type": str(raw.get("establishment_type") or ""),
        "fei_number": str(raw.get("fei_number") or ""),
        "firm_name": str(raw.get("firm") or ""),
    }


def _fetch_text(pdf_url: str) -> tuple[str, str]:
    return _collector()._fetch_fda483_pdf_text(pdf_url)


def _fetch_rows(base_url: str, service_key: str, since: str) -> list[dict[str, Any]]:
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="raw_signal_id,document_id,raw_json", page_size=500,
        order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "published_date": f"gte.{since}",
                      "raw_json": f"like.*{_OBS_KEY}*"},
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
    delay_seconds: float = 0.5,
    text_fn: Callable[[str], tuple[str, str]] = _fetch_text,
    patch_fn: Callable[[str, dict[str, Any]], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> DetailRefreshReport:
    c = _collector()
    report = DetailRefreshReport(dry_run=dry_run, since=since)
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
        for o_old, o_new in zip(old, refreshed):
            _add(report.before, str(o_old.get("detail") or ""))
            _add(report.after, str(o_new.get("detail") or ""))
            if str(o_old.get("detail") or "") != o_new["detail"]:
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
    p.add_argument("--since", default=DEFAULT_SINCE, help="게시일 하한(YYYY-MM-DD).")
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
    rows = _fetch_rows(base_url, service_key, args.since)
    ids = {x for x in re.split(r"[\s,]+", args.doc_ids or "") if x}
    report = run(
        rows, dry_run=not args.apply, since=args.since, limit=args.limit, doc_ids=ids or None,
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
