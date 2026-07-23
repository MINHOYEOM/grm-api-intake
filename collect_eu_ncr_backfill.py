#!/usr/bin/env python3
"""EU GMP NCR (EudraGMDP) 과거 findings 딥 백필 — Notion 우회, Supabase 직행.

배경: 매일 크론(`grm-intake.yml`)은 `window_days` 상한이 [1,90] 이라 최근 90일만 본다.
EudraGMDP EU GMP 비준수 보고서(NCR)는 2019년부터 누적된 성긴 소스라, 과거분(약 65건)은
그 경로로 절대 들어오지 않는다. 이 스크립트는 FDA 483/WL 백필(collect_fda_backfill.py)과
같은 패턴으로 **넓은 발행일 창을 한 번에 수집**해 raw_signals + findings 를 Supabase 에
직접 적재한다(Notion 무접촉 → 주간 브리프 파이프라인 무간섭 — 과거분은 7일 창 밖이라
News 카드로도 안 나간다).

재사용 배관(신규 코드 없음):
  - `collect_eu_gmp_ncr.collect_eu_gmp_ncr(start, end)` — 수집 + PDF Storage 아카이브 +
    IntakeItem 생성을 전부 수행(라이브 라인과 동일 함수).
  - `findings_supabase_append.append_intake_item_with_findings_to_supabase` — raw_signal +
    파생 findings 를 PostgREST 로 직접 적재(on_conflict 멱등).

멱등성: raw_signal_id / finding_id 는 (source, document_id=doc_ref) 해시라, 이미 공개된
최근 8건을 다시 적재해도 중복 0. 광범위 창으로 여러 번 재실행해도 안전(성긴 소스 = 1회성
백필이지만 멱등이라 실패 시 잔여만 재적재하면 됨).

실패는 침묵 금지: 수집 자체 실패(collect_eu_gmp_ncr error)는 exit 2. 건별 append 실패는
계속하되 카운트/에러를 리포트에 남기고, 하나라도 실패면 exit 1.

★적재 후 반드시 번역 필요(별도 단계): findings 공개 RLS 게이트가 finding_text_ko 또는
finding_language='KO' 를 요구하므로, 영어 원문만으로는 anon 검색에 안 잡힌다. 이 스크립트는
적재까지만 책임진다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from collect_eu_gmp_ncr import collect_eu_gmp_ncr
from findings_supabase_append import append_intake_item_with_findings_to_supabase
from grm_common import log

SCHEMA_VERSION = "grm-eu-ncr-backfill/v1"

# append 결과 status 분류. error/invalid = 적재 실패. 그 외(inserted/duplicate/
# raw_signal_inserted/partial)는 멱등 성공으로 간주(partial 은 일부 findings 이슈라 경고만).
_APPEND_OK = {"inserted", "duplicate", "raw_signal_inserted", "partial"}
_APPEND_FAIL = {"error", "invalid"}

# 이 스크립트가 매개하는 collect_eu_gmp_ncr / append 는 테스트에서 모듈 이름으로 패치된다.
Appender = Callable[..., Any]


@dataclass
class BackfillReport:
    schema_version: str = SCHEMA_VERSION
    from_date: str = ""
    to_date: str = ""
    dry_run: bool = False
    collected: int = 0
    appended: int = 0
    duplicate: int = 0
    partial: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    would_append: list[str] = field(default_factory=list)  # dry-run: 앞 20건 doc_ref


def run(
    *,
    start: dt.date,
    end: dt.date,
    dry_run: bool,
    base_url: str,
    service_key: str,
    collected_at: str,
    collector: Callable[[dt.date, dt.date], tuple[list[Any], str | None]] | None = None,
    appender: Appender | None = None,
) -> tuple[BackfillReport, int]:
    # 지연 바인딩: 기본값을 def 시점이 아닌 호출 시점의 모듈 전역으로 해석해, 테스트가
    # collect_eu_ncr_backfill.collect_eu_gmp_ncr / ...append... 를 패치하면 그대로 반영된다.
    collector = collector or collect_eu_gmp_ncr
    appender = appender or append_intake_item_with_findings_to_supabase
    report = BackfillReport(
        from_date=start.isoformat(), to_date=end.isoformat(), dry_run=dry_run,
    )

    items, err = collector(start, end)
    if err:
        # 수집 자체 실패 = 침묵 0 금지(빈 리스트로 눙치지 않는다).
        report.errors.append(f"collect_failed:{err}")
        log("ERROR", f"EU NCR 백필 수집 실패: {err}")
        return report, 2
    report.collected = len(items)
    log("INFO", f"EU NCR 백필 수집 {len(items)}건 (창 {start}~{end})")

    for it in items:
        doc_ref = str(getattr(it, "document_id", "") or "")
        if dry_run:
            if len(report.would_append) < 20:
                report.would_append.append(doc_ref)
            continue
        try:
            res = appender(base_url, service_key, it, collected_at=collected_at)
        except Exception as e:  # noqa: BLE001 — 건별 실패는 계속(멱등 재실행 가능)
            report.failed += 1
            report.errors.append(f"append_raised({doc_ref}):{type(e).__name__}")
            log("WARN", f"append 예외 {doc_ref}: {type(e).__name__}")
            continue

        status = getattr(res, "status", "")
        res_errors = tuple(getattr(res, "errors", ()) or ())
        if status in _APPEND_FAIL:
            report.failed += 1
            report.errors.append(f"append_failed({doc_ref}):{status}:{'; '.join(res_errors)}")
            log("WARN", f"append 실패 {doc_ref}: {status}")
            continue
        # 성공 계열.
        if status == "duplicate":
            report.duplicate += 1
        elif status == "partial":
            report.partial += 1
            report.appended += 1
            if res_errors:
                report.errors.append(f"append_partial({doc_ref}):{'; '.join(res_errors)}")
        else:  # inserted / raw_signal_inserted
            report.appended += 1

    if dry_run:
        log("INFO", f"[DRY] 적재 대상 {report.collected}건 (실적재 없음)")
    else:
        log("INFO", f"적재 {report.appended}건 · 중복 {report.duplicate}건 · "
                    f"부분 {report.partial}건 · 실패 {report.failed}건")

    exit_code = 1 if report.failed else 0
    return report, exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="EU GMP NCR (EudraGMDP) 과거 findings 딥 백필 — Supabase 직행"
        "(Notion 미접촉·collect_fda_backfill 패턴). 넓은 발행일 창을 1회 수집·멱등 적재.",
    )
    p.add_argument("--from-date", default="2015-01-01",
                   help="발행일 창 시작(ISO). 기본 2015-01-01(전량 커버).")
    p.add_argument("--to-date", default="",
                   help="발행일 창 끝(ISO). 빈값이면 오늘.")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="수집·PDF 아카이브만 수행하고 Supabase 적재는 생략.")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    p.add_argument("--output", help="JSON 리포트를 이 경로에도 기록.")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)

    base = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not args.dry_run and not (base and key):
        print("collect_eu_ncr_backfill: --supabase-url/--service-role-key 또는 "
              "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요(실적재 모드)", file=sys.stderr)
        return 2

    try:
        start = dt.date.fromisoformat(args.from_date)
        end = dt.date.fromisoformat(args.to_date) if args.to_date else dt.date.today()
    except ValueError as e:
        print(f"collect_eu_ncr_backfill: 날짜 파싱 실패: {e}", file=sys.stderr)
        return 2

    # Date.now 는 스크립트 실행 시각 1회만 캡처(리포트/collected_at 결정론 앵커).
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat()

    report, exit_code = run(
        start=start, end=end, dry_run=args.dry_run,
        base_url=base, service_key=key, collected_at=collected_at,
    )

    from dataclasses import asdict
    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return exit_code


__all__ = ["BackfillReport", "run", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
