#!/usr/bin/env python3
"""MFDS(data.go.kr) 과거분 딥 백필 — Notion 우회, Supabase 직행.

배경: 매일 크론(`grm-intake.yml`)은 창이 짧아 국내 소스의 과거분이 영영 안 들어온다.
2026-08-05 실측으로 `admin-action` 은 2026-04-28 이전이 raw_signals 에 하나도 없다.
data.go.kr 행정처분/회수 API 는 과거분을 계속 노출한다는 보장이 없는 소멸성 데이터라
한 번에 넓은 창으로 긁어 raw_signals + findings 를 직접 적재한다
(`collect_eu_ncr_backfill.py` 와 같은 패턴 — Notion 무접촉이라 주간 브리프 무간섭).

★단일 소스 전용이 아니다. `--source {admin-action,recall}` 로 data.go.kr 계열 두 수집기를
모두 태운다. 두 collector 의 시그니처가 `(start, end, service_key) -> (items, err)` 로
동일하기 때문이며, 나머지 배관(append·리포트·exit code·dry-run)은 완전히 공유한다.
※ EU NCR 템플릿의 collector 는 인자 2개, 이쪽은 **3개**(`service_key`)다.

★상한이 창보다 먼저 걸린다(이 저장소에서 4회 반복된 함정). 두 수집기 모두 페이지 상한
(`MAX_PAGES` — admin 20 / recall 25)이 있고 **날짜 필터가 서버측이 아니라 클라이언트측**
이라, 창만 넓히면 아무것도 더 안 들어온다. 그래서 `--max-pages` 로 수집기 모듈의 상한을
런타임에 덮어쓰고, 상한 도달(truncated)은 **exit 3 으로 시끄럽게** 끝낸다.

멱등성: raw_signal_id / finding_id 는 (source, document_id) 해시라 이미 보유한 최근분을
다시 적재해도 중복 0. 실패 시 잔여만 재실행하면 된다.

번역 불요: MFDS findings 는 전건 `finding_language='KO'` + `finding_text_ko` 라 공개 RLS
게이트를 그대로 통과한다(EU NCR 백필의 번역 단계가 여기엔 없다).

exit code 규약:
  0 = 정상
  1 = 건별 append 실패 있음
  2 = 수집 자체 실패 / 인자·자격증명·가드 위반
  3 = 페이지 상한 도달(truncated) — 수집·적재는 수행했으나 창을 다 못 덮었을 수 있다.
      `--max-pages` 를 올려 재실행하라(멱등).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import collect_mfds_admin_action
import collect_mfds_recall
from findings_supabase_append import append_intake_item_with_findings_to_supabase
from grm_common import env_flag, log

SCHEMA_VERSION = "grm-mfds-backfill/v1"

# append 결과 status 분류(EU NCR 백필과 동일 규약). error/invalid = 적재 실패.
_APPEND_OK = {"inserted", "duplicate", "raw_signal_inserted", "partial"}
_APPEND_FAIL = {"error", "invalid"}

# 수집기가 (items, err) 의 err 로 돌려주는 상한 도달 메시지의 마커. 두 수집기 모두
# f"... max_pages={MAX_PAGES} 도달 — truncated ..." 형식이다. ★이건 수집 실패가 아니라
# 부분 수집이므로, 템플릿처럼 err 만 보고 exit 2 로 버리면 최대 2,000행을 통째로 버린다.
_TRUNCATED_MARKER = "truncated"

Appender = Callable[..., Any]
Collector = Callable[..., tuple[list[Any], str | None]]


@dataclass(frozen=True)
class SourceSpec:
    """`--source` 값 → 수집기 모듈/함수/상한 이름. 상한은 모듈 전역이라 이름으로 덮어쓴다."""

    key: str
    label: str
    module: Any
    func_name: str


SOURCE_SPECS: dict[str, SourceSpec] = {
    "admin-action": SourceSpec(
        key="admin-action",
        label="MFDS 행정처분",
        module=collect_mfds_admin_action,
        func_name="collect_mfds_admin_actions",
    ),
    "recall": SourceSpec(
        key="recall",
        label="MFDS 회수·판매중지",
        module=collect_mfds_recall,
        func_name="collect_mfds_recall",
    ),
}


@dataclass
class BackfillReport:
    schema_version: str = SCHEMA_VERSION
    source: str = ""
    from_date: str = ""
    to_date: str = ""
    dry_run: bool = False
    max_pages: int = 0  # 실제 적용된 상한(0 = 수집기 기본값 그대로)
    collected: int = 0
    appended: int = 0
    duplicate: int = 0
    partial: int = 0
    failed: int = 0
    truncated: bool = False
    date_min: str = ""
    date_max: str = ""
    by_month: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    would_append: list[str] = field(default_factory=list)  # dry-run: 앞 20건 document_id


@contextmanager
def page_cap(module: Any, max_pages: int | None) -> Iterator[int]:
    """수집기 모듈의 `MAX_PAGES` 전역을 일시 상향하고 반드시 되돌린다.

    수집기 함수가 호출 시점에 모듈 전역을 읽으므로 이 방식이 성립한다. 수집기 자체는
    손대지 않아 매일 크론 라인의 동작·골든은 완전 불변이다(백필 프로세스 안에서만 유효).
    """
    original = int(getattr(module, "MAX_PAGES", 0) or 0)
    if not max_pages or max_pages <= 0:
        yield original
        return
    module.MAX_PAGES = int(max_pages)
    try:
        yield int(max_pages)
    finally:
        module.MAX_PAGES = original


def resolve_source(source: str) -> SourceSpec:
    spec = SOURCE_SPECS.get(source)
    if spec is None:
        raise KeyError(source)
    return spec


def _url_verify_guard() -> str | None:
    """`ENABLE_MFDS_URL_VERIFY` 는 건별로 nedrug.mfds.go.kr 을 때린다 — nedrug 은 해외 IP
    차단이 있고 GitHub Actions 러너는 해외 IP다. 백필은 건수가 크므로 켜진 채 돌면 전건이
    타임아웃으로 느리게 실패한다. 불가침 규칙을 코드로 강제한다(경고 아닌 정지)."""
    if env_flag("ENABLE_MFDS_URL_VERIFY"):
        return ("ENABLE_MFDS_URL_VERIFY 가 켜져 있다 — 백필에서 nedrug(해외 IP 차단) 을 "
                "건별로 때리게 되므로 중단한다. 이 플래그를 끄고 재실행하라.")
    return None


def run(
    *,
    source: str,
    start: dt.date,
    end: dt.date,
    dry_run: bool,
    base_url: str,
    service_key: str,
    datago_key: str,
    collected_at: str,
    max_pages: int | None = None,
    collector: Collector | None = None,
    appender: Appender | None = None,
) -> tuple[BackfillReport, int]:
    report = BackfillReport(
        source=source, from_date=start.isoformat(), to_date=end.isoformat(),
        dry_run=dry_run,
    )

    guard = _url_verify_guard()
    if guard:
        report.errors.append(f"guard_violation:{guard}")
        log("ERROR", guard)
        return report, 2

    try:
        spec = resolve_source(source)
    except KeyError:
        msg = f"알 수 없는 --source: {source} (허용: {', '.join(sorted(SOURCE_SPECS))})"
        report.errors.append(f"bad_source:{source}")
        log("ERROR", msg)
        return report, 2

    # 지연 바인딩: 테스트가 collect_mfds_admin_action.collect_mfds_admin_actions 를
    # 패치하면 그대로 반영된다(EU NCR 백필과 동일한 패치 지점 규약).
    fn: Collector = collector or getattr(spec.module, spec.func_name)
    appender = appender or append_intake_item_with_findings_to_supabase

    with page_cap(spec.module, max_pages) as applied_cap:
        report.max_pages = applied_cap
        log("INFO", f"{spec.label} 백필 수집 시작 (창 {start}~{end}, max_pages={applied_cap})")
        items, err = fn(start, end, datago_key)

    if err and _TRUNCATED_MARKER in err:
        # 상한 도달 = 부분 수집. 수집물은 살리되 exit 3 으로 시끄럽게 끝낸다.
        report.truncated = True
        report.errors.append(f"collect_truncated:{err}")
        log("WARN", f"{spec.label} 백필 상한 도달(truncated) — {err}")
    elif err:
        report.errors.append(f"collect_failed:{err}")
        log("ERROR", f"{spec.label} 백필 수집 실패: {err}")
        return report, 2

    report.collected = len(items)
    dates = sorted(str(getattr(it, "date_iso", "") or "") for it in items)
    dates = [d for d in dates if d]
    if dates:
        report.date_min, report.date_max = dates[0], dates[-1]
    for d in dates:
        month = d[:7]
        report.by_month[month] = report.by_month.get(month, 0) + 1
    log("INFO", f"{spec.label} 백필 수집 {len(items)}건 "
                f"(창 {start}~{end}, 실측 {report.date_min}~{report.date_max})")

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

    if report.failed:
        return report, 1
    if report.truncated:
        return report, 3
    return report, 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="MFDS(data.go.kr) 과거분 딥 백필 — Supabase 직행(Notion 무접촉). "
                    "넓은 창을 1회 수집해 raw_signals + findings 를 멱등 적재한다.",
    )
    p.add_argument("--source", choices=sorted(SOURCE_SPECS), default="admin-action",
                   help="백필할 data.go.kr 수집기. 기본 admin-action(행정처분).")
    p.add_argument("--from-date", required=True,
                   help="처분/공표일 창 시작(ISO). 클라이언트측 필터라 넓힐수록 페이지가 필요하다.")
    p.add_argument("--to-date", default="", help="창 끝(ISO). 빈값이면 오늘.")
    p.add_argument("--max-pages", type=int, default=0,
                   help="수집기 MAX_PAGES 상한 덮어쓰기(0 = 수집기 기본값). "
                        "미지정 시 $GRM_MFDS_BACKFILL_MAX_PAGES.")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="수집만 하고 Supabase 적재는 생략.")
    p.add_argument("--service-key", help="미지정 시 $DATA_GO_KR_SERVICE_KEY")
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
        print("collect_mfds_backfill: --supabase-url/--service-role-key 또는 "
              "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요(실적재 모드)", file=sys.stderr)
        return 2

    datago_key = (args.service_key or os.environ.get("DATA_GO_KR_SERVICE_KEY") or "").strip()

    max_pages = args.max_pages or 0
    if not max_pages:
        try:
            max_pages = int((os.environ.get("GRM_MFDS_BACKFILL_MAX_PAGES") or "0").strip() or 0)
        except ValueError:
            print("collect_mfds_backfill: GRM_MFDS_BACKFILL_MAX_PAGES 는 정수여야 한다",
                  file=sys.stderr)
            return 2

    try:
        start = dt.date.fromisoformat(args.from_date)
        end = dt.date.fromisoformat(args.to_date) if args.to_date else dt.date.today()
    except ValueError as e:
        print(f"collect_mfds_backfill: 날짜 파싱 실패: {e}", file=sys.stderr)
        return 2
    if start > end:
        print(f"collect_mfds_backfill: from-date({start}) 가 to-date({end}) 보다 늦다",
              file=sys.stderr)
        return 2

    collected_at = dt.datetime.now(dt.timezone.utc).isoformat()

    report, exit_code = run(
        source=args.source, start=start, end=end, dry_run=args.dry_run,
        base_url=base, service_key=key, datago_key=datago_key,
        collected_at=collected_at, max_pages=max_pages,
    )

    from dataclasses import asdict
    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return exit_code


__all__ = ["BackfillReport", "SourceSpec", "SOURCE_SPECS", "page_cap", "resolve_source",
           "run", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
