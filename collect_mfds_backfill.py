#!/usr/bin/env python3
"""MFDS(data.go.kr) 과거분 딥 백필 — Notion 우회, Supabase 직행.

배경: 매일 크론(`grm-intake.yml`)은 창이 짧아 국내 소스의 과거분이 영영 안 들어온다.
2026-08-05 실측으로 `admin-action` 은 2026-04-28 이전이 raw_signals 에 하나도 없다.
data.go.kr 행정처분/회수 API 는 과거분을 계속 노출한다는 보장이 없는 소멸성 데이터라
한 번에 넓은 창으로 긁어 raw_signals + findings 를 직접 적재한다
(`collect_eu_ncr_backfill.py` 와 같은 패턴 — Notion 무접촉이라 주간 브리프 무간섭).

★단일 소스 전용이 아니다. `--source {admin-action,recall,gmp-inspection}` 로 국내 수집기
셋을 모두 태운다. 나머지 배관(append·리포트·exit code·dry-run)은 완전히 공유한다.
※ collector 시그니처가 계열마다 다르다 — data.go.kr 계열은 `(start, end, service_key)`
3개, nedrug 게시판 계열(gmp-inspection)은 `(start, end)` 2개다(`SourceSpec.arity`).

★상한이 창보다 먼저 걸린다(이 저장소에서 4회 반복된 함정). 세 수집기 모두 페이지 상한
(`MAX_PAGES` — admin 20 / recall 25 / gmp 10)이 있고 **날짜 필터가 서버측이 아니라
클라이언트측**이라, 창만 넓히면 아무것도 더 안 들어온다. 그래서 `--max-pages` 로 수집기
모듈의 상한을 런타임에 덮어쓰고, 상한 도달(truncated)은 **exit 3 으로 시끄럽게** 끝낸다.

★★**상한 도달을 알리는 통로가 수집기마다 다르다.** data.go.kr 계열은 `(items, err)` 의
err 문자열로 알리지만, `collect_mfds_gmp_inspections` 는 **반환값에 아무것도 안 남기고**
모듈 전역 `LAST_HEALTH["max_pages_reached"]` 와 WARN 로그로만 알린다. 부분 페이지 실패
(`page_warnings`)도 마찬가지로 `err=None` 으로 돌아온다 — 그 전역을 안 읽으면 잘린 수집이
**완전히 침묵한 채 성공으로 끝난다.** 그래서 `read_health()` 로 매번 읽어 리포트에 싣는다.

★배치 분할은 **날짜 창으로만** 가능하다. gmp-inspection 은 창 안의 행마다 첨부를 1초
간격으로 내려받으므로(`ATTACHMENT_REQUEST_DELAY_SECONDS`) 비용이 창 크기에 비례한다.
수집이 끝난 뒤 자르는 `--limit` 류는 이미 지불한 비용을 못 돌려주고 누락만 감춘다.

멱등성: raw_signal_id / finding_id 는 (source, document_id) 해시라 이미 보유한 최근분을
다시 적재해도 중복 0. 실패 시 잔여만 재실행하면 된다.

★"번역 불요"는 **공개 RLS 한정** 사실이다(2026-08-05 실측 정정). 공개 게이트
(`finding_text_ko <> '' OR finding_language='KO'`)는 KO 로 통과하고 UI 도
`ko || finding_text` 폴백이라 표시는 정상이다 — 적재를 막을 이유는 없다. 그러나
`findings_translation_queue` RPC 는 `coalesce(finding_text_ko,'')=''` 만 보고
**`finding_language` 를 안 본다** → 한국어 원문 findings 가 전부 큐에 쌓인다.
그 큐 잔량은 "번역 대기"가 아니라 **가독성 정리 대기**이므로, 백로그 경보에서
영문 미번역(공개 게이트를 실제로 막는 건)과 **합산하면 진단이 반드시 틀린다.**

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
import collect_mfds_gmp_inspection
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
    """`--source` 값 → 수집기 모듈/함수/상한 이름. 상한은 모듈 전역이라 이름으로 덮어쓴다.

    `arity` — data.go.kr 계열은 `(start, end, service_key)` 3개, nedrug 게시판 계열은
    `(start, end)` 2개다. `health_attr` — 수집기가 반환값 대신 모듈 전역에만 남기는 관측
    딕셔너리 이름(★그 안의 `max_pages_reached` 를 안 읽으면 상한 도달이 **완전히 침묵**한다).
    `required_flags` — 켜져 있지 않으면 수율이 조용히 무너지는 플래그.
    """

    key: str
    label: str
    module: Any
    func_name: str
    arity: int = 3
    health_attr: str = ""
    required_flags: tuple[str, ...] = ()


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
    "gmp-inspection": SourceSpec(
        key="gmp-inspection",
        label="MFDS GMP 실태조사",
        module=collect_mfds_gmp_inspection,
        func_name="collect_mfds_gmp_inspections",
        arity=2,
        health_attr="LAST_HEALTH",
        # ★off 면 지적사항 표 구조화가 꺼져 문서당 findings 가 ~6건에서 최대 1건으로
        # 떨어진다 — 건수는 '정상'으로 보이므로 사후 발견이 어렵다. 라이브 일일 라인
        # (grm-intake.yml)은 이 플래그를 켜고 돌기 때문에, 끄고 백필하면 같은 소스 안에
        # 수율이 다른 2계층 코퍼스가 생긴다.
        required_flags=("ENABLE_GMP_DEFICIENCY_TABLE",),
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
    findings_inserted: int = 0
    findings_duplicate: int = 0
    findings_invalid: int = 0
    date_min: str = ""
    date_max: str = ""
    by_month: dict[str, int] = field(default_factory=dict)
    # 수집기가 모듈 전역에만 남기는 관측치(파싱 성공률·판정 분포·상한 도달). 건수만
    # 보고서에 실으면 "본문이 깨졌는데 건수는 정상"인 상태를 못 잡는다.
    source_health: dict[str, Any] = field(default_factory=dict)
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


_HEALTH_KEYS = (
    "item_count", "parsed_rows", "pages_seen", "max_pages_reached",
    "parse_status_counts", "deficiency_counts", "manual_review_count",
    "page_warnings", "deficiency_table",
)


def read_health(spec: SourceSpec) -> dict[str, Any]:
    """수집기 모듈 전역 관측치를 복사해 온다(없으면 빈 dict)."""
    if not spec.health_attr:
        return {}
    raw = getattr(spec.module, spec.health_attr, None)
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in _HEALTH_KEYS if k in raw}


def required_flag_guard(spec: SourceSpec) -> str | None:
    """수율을 조용히 떨어뜨리는 플래그가 꺼져 있는지 검사한다.

    ★건수는 정상으로 보이는데 내용이 빈약해지는 종류의 결함이라, 사후 발견이 어렵다.
    백필은 1회성이고 재실행 비용이 크므로(첨부 622건 재다운로드) 경고가 아니라 정지로 막는다.
    """
    missing = [f for f in spec.required_flags if not env_flag(f)]
    if not missing:
        return None
    return (f"{spec.label} 백필에 필요한 플래그가 꺼져 있다: {', '.join(missing)}. "
            "라이브 일일 라인은 켜고 돌기 때문에 이대로 적재하면 같은 소스 안에 수율이 "
            "다른 2계층 코퍼스가 생긴다. 플래그를 켜거나, 의도한 것이면 "
            "--allow-degraded-yield 를 명시하라.")


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
    allow_degraded_yield: bool = False,
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

    flag_guard = None if allow_degraded_yield else required_flag_guard(spec)
    if flag_guard:
        report.errors.append(f"guard_violation:{flag_guard}")
        log("ERROR", flag_guard)
        return report, 2

    # 지연 바인딩: 테스트가 collect_mfds_admin_action.collect_mfds_admin_actions 를
    # 패치하면 그대로 반영된다(EU NCR 백필과 동일한 패치 지점 규약).
    fn: Collector = collector or getattr(spec.module, spec.func_name)
    appender = appender or append_intake_item_with_findings_to_supabase

    with page_cap(spec.module, max_pages) as applied_cap:
        report.max_pages = applied_cap
        log("INFO", f"{spec.label} 백필 수집 시작 (창 {start}~{end}, max_pages={applied_cap})")
        args = (start, end) if spec.arity == 2 else (start, end, datago_key)
        items, err = fn(*args)

    # ★수집기마다 상한 도달을 알리는 통로가 다르다. data.go.kr 계열은 err 문자열로,
    # nedrug 게시판 계열은 **반환값에 아무것도 안 남기고** 모듈 전역 health 의
    # `max_pages_reached` 와 WARN 로그로만 알린다 — 안 읽으면 완전히 침묵한다.
    # 같은 이유로 부분 페이지 실패(page_warnings)도 err=None 으로 돌아온다.
    report.source_health = read_health(spec)
    if report.source_health.get("max_pages_reached"):
        report.truncated = True
        report.errors.append(
            f"collect_truncated:{spec.label} max_pages={report.max_pages} 도달(health)")
        log("WARN", f"{spec.label} 백필 상한 도달(truncated·health) — max_pages={report.max_pages}")
    for warn in report.source_health.get("page_warnings") or ():
        report.errors.append(f"collect_page_warning:{warn}")
        log("WARN", f"{spec.label} 페이지 경고: {warn}")

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
        # findings 카운터는 status 와 무관하게 누적한다 — partial 도 일부는 들어간다.
        report.findings_inserted += int(getattr(res, "findings_inserted", 0) or 0)
        report.findings_duplicate += int(getattr(res, "findings_duplicate", 0) or 0)
        report.findings_invalid += int(getattr(res, "findings_invalid", 0) or 0)
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
                    f"부분 {report.partial}건 · 실패 {report.failed}건 · "
                    f"findings 신규 {report.findings_inserted}건"
                    f"(중복 {report.findings_duplicate}·무효 {report.findings_invalid})")

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
    p.add_argument("--allow-degraded-yield", action="store_true", default=False,
                   help="수율 플래그(ENABLE_GMP_DEFICIENCY_TABLE 등)가 꺼진 채로도 진행. "
                        "기본은 정지 — 건수는 정상으로 보이면서 내용만 빈약해지기 때문.")
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
        allow_degraded_yield=args.allow_degraded_yield,
    )

    from dataclasses import asdict
    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return exit_code


__all__ = ["BackfillReport", "SourceSpec", "SOURCE_SPECS", "page_cap", "read_health",
           "required_flag_guard", "resolve_source",
           "run", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
