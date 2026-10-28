#!/usr/bin/env python3
"""본문 없이 적재된 FDA Warning Letter 행을 다시 받아 제자리 교체 — Supabase 직행.

## 왜 필요한가 (2026-10-28)

WL 편지 본문(`wl_body_full`)은 일일 수집(`collect_intake.collect_fda_warning_letters`)과
백필 페치(`collect_fda_backfill.run_wl`)가 편지 페이지 GET 으로 받는다. 그 GET 이 403·5xx·
타임아웃이면 행은 **본문 없이** 적재되고(`raw_payload.wl_body_status` 에 사유), 이후 수집은
이미 저장된 document_id 를 다시 받지 않으므로 **영구 결손**이다. #1218 이 실패를 health
경고(`wl-body-full-degraded`)로 보이게 했지만 되찾는 경로는 없었다. 수집기에 일시 장애
재시도를 넣었어도 403(같은 IP 봇 차단)과 재시도를 넘긴 장애는 남는다 — 이 스크립트가
다음 날 다른 러너에서 그 행들을 다시 읽는다.

## 설계 — `backfill_483_ocr_recovery.py` 와 같은 원칙

  · **행 전체 재구성.** raw_json 을 손으로 기우지 않고 라이브 수집과 **같은 함수**로 만든다:
        저장 raw → collect_intake.build_wl_item(...) → findings_store.raw_signal_from_intake_item
    `raw_signal_id` 는 document_id 에만 의존하므로 DELETE 없이 upsert(merge-duplicates)
    한 번으로 제자리 교체된다(findings CASCADE 위험 없음). `collected_at` 은 원본 보존.
  · **본문을 못 얻으면 쓰지 않는다**(빈손을 빈손으로 덮지 않는다). 예외: 다시 읽어 본 결과가
    **확정 사유**(404·no-anchor)면 그 사유만 표식으로 남겨 매일 같은 편지를 다시 받는 일을 끊는다.
  · 재구성한 document_id/raw_signal_id 가 기존과 다르면 건너뛴다(떠돌이 행 금지).
  · 저장 raw 에 이 재구성이 모르는 키가 있으면 건너뛴다 — 다른 경로가 덧붙인 값을 지우지 않는다.

후보: source='FDA Warning Letter' 이고 `wl_body_full` 이 비었으며 `wl_body_status` 가 확정
사유가 아닌 행(403·fetch-fail(404 제외)·사유 없음 — 사유 없음은 전문 플래그 도입 전 적재분과
백필 페치의 예외 경로다).

## 영구 403 — 언제 포기하고 누구에게 알리나 (2026-10-28)

fda.gov 의 403 은 러너 IP 봇 차단이다. GitHub 러너 IP 는 날마다 바뀌므로 하루 403 은 포기
사유가 아니다 — 그러나 같은 편지가 몇 주째 403 이면 매일 GET 은 차단만 길게 만든다.
  · 첫 403 관측일을 표식에 남긴다: 라이브 수집이 남긴 `fetch-403`(첫 관측 = 적재일)은 그대로,
    사유가 없거나 다른 사유였던 행은 처음 403 을 본 날 `fetch-403:since=YYYY-MM-DD` 로 한 번 쓴다.
  · 첫 관측일부터 `PERMANENT_403_AFTER_DAYS`(14일 = 매일 회수 14회) 동안 본문을 한 번도 못 받았고
    오늘도 403 이면 `fetch-403:permanent since=YYYY-MM-DD` 로 바꿔 **확정 사유**로 만든다 —
    이후 매일 회수 대상에서 빠진다. `:` 앞 토큰이 `fetch-403` 그대로라 하류 사유 매핑
    (`card_scaffold._ABSENT_REASON_LABELS`)은 바뀌지 않는다.
  · 사람에게는 주간 reconciliation 요약(`reconciliation_audit.py`, 월 06:00 KST)이 포기 건수와
    문서 id 를 한 줄로 알린다. 다시 시도하려면 `--doc-ids` 로 그 문서를 지정한다(지정하면 영구
    표식도 후보가 된다).

## 본문 시작점 재구성 `--rebuild-body-start` (2026-10-28, 수동 전용)

본문 시작 앵커가 문장 안 낱말에서 잘라 `wl_body_full` 이 문장 중간(소문자)에서 시작하는 행이
있었다(수리: `collect_intake._wl_sentence_start`·`_wl_letter_opening`). 이 모드는 **본문이 이미
있는** 행 중 소문자로 시작하는 행(+ `--doc-ids` 로 지정한 행)을 다시 받아 같은 `build_wl_item`
으로 재구성한다. 다시 받은 본문이 없거나(403 등) 기존과 같으면 쓰지 않는다 — 있는 본문을 표식으로
덮지 않는다. 예약 실행은 이 모드를 쓰지 않는다. findings 는 M12 가 "findings 없는 행"만 보므로
이 모드로 바뀐 행의 findings 는 재생성되지 않는다(필요하면 별도 재변환).

findings 재생성은 이 스크립트가 하지 않는다 — 본문이 채워지면 M12(`findings_supabase_backfill.py`)
가 "findings 없는 raw_signal" 로 보고 변환한다. 워크플로가 그 체인을 잇는다.

exit code: 자격증명/URL/조회 실패 → 2 · 건별 실패가 광범위(시도의 절반 이상)면 → 1 · 그 외 0.
dry-run(기본)은 fetch·재구성까지 하고 쓰기만 건너뛴다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import findings_store
import findings_supabase_append as fsa
import findings_supabase_backfill as fsb
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_WL, log

SCHEMA_VERSION = "grm-wl-body-recovery/v1"

DEFAULT_TIMEOUT_SECONDS = fsb.DEFAULT_TIMEOUT_SECONDS
_DEFAULT_PAGE_SIZE = 1000
# fda.gov robots Crawl-Delay 는 백필 페치가 30초로 지킨다. 회수 대상은 하루 한 줌이라
# 그 정도 간격도 부담이 없다 — 기본값을 같은 예의 수준으로 둔다.
_DEFAULT_DELAY_SECONDS = 5.0
_MAX_SAMPLES = 20
_BROAD_FAILURE_MIN_ATTEMPTS = 3
_BROAD_FAILURE_RATIO = 0.5

# build_wl_item 이 만드는 raw 키 전부. 저장 raw 에 이 밖의 키가 있으면 재구성이 그 값을 잃는다.
KNOWN_RAW_KEYS = frozenset({
    "firm", "posted_date", "letter_date", "issuing_office", "subject", "url",
    "office_gate_verdict", "wl_body_excerpt", "wl_body_full", "wl_violations",
    "wl_body_status",
})
# 다시 읽어도 결과가 같을 사유. 이 표식이 붙은 행은 후보가 아니다(재시도가 끊긴다).
DEFINITIVE_STATUSES = ("no-anchor",)

# 영구 403 정책(모듈 docstring). 표식은 모두 `fetch-403` 토큰으로 시작한다 — 하류 사유 매핑 계약.
STATUS_403 = "fetch-403"
PERMANENT_403_AFTER_DAYS = 14
_403_PERMANENT_PREFIX = "fetch-403:permanent"
_403_SINCE_RE = re.compile(r"since=(\d{4}-\d{2}-\d{2})$")

Sleeper = Callable[[float], None]


@dataclass
class WlBodyRecoveryReport:
    schema_version: str = SCHEMA_VERSION
    mode: str = ""                      # "dry_run" | "apply"
    limit: int | None = None
    delay_seconds: float = _DEFAULT_DELAY_SECONDS
    scanned: int = 0                    # 서버에서 좁힌 WL 행(wl_body_full 문자열 없음)
    candidates: int = 0                 # 그중 회수 대상(doc-ids/limit 반영 후)
    skipped_definitive: int = 0         # 확정 사유 표식 — 대상 아님
    attempted: int = 0                  # 편지 페이지 fetch 시도
    recovered: int = 0                  # 본문 확보(dry-run=교체 예정, apply=upsert 성공)
    violations_recovered: int = 0       # 되찾은 위반 표제 수
    still_empty: int = 0                # 이번에도 못 받음(403·5xx 등) — 본문은 손대지 않음, 다음 실행 재시도
    blocked_403_marked: int = 0         # 첫 403 관측일 표식(fetch-403:since=)을 새로 남김
    permanent_403: int = 0              # 영구 403 으로 확정 — 이후 매일 회수에서 빠진다
    permanent_403_docs: list[str] = field(default_factory=list)
    rebuild_body_start: bool = False    # --rebuild-body-start 모드
    body_start_unchanged: int = 0       # 그 모드에서 다시 받은 본문이 기존과 같음 — 쓰지 않음
    status_resolved: int = 0            # 확정 사유(404·no-anchor)로 표식만 교체
    unknown_keys: int = 0               # 저장 raw 에 모르는 키 — 손대지 않음
    id_mismatch: int = 0                # 재구성 id ≠ 기존 — 손대지 않음(0 이어야 정상)
    failed: int = 0                     # 재구성·upsert 예외
    failure_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    # --rebuild-body-start 에서 본문이 바뀐 행 전부의 길이 변화(apply 전 짧아지는 행을 가려낸다).
    body_changes: list[dict[str, Any]] = field(default_factory=list)


def _default_sleep(seconds: float) -> None:
    if seconds:
        time.sleep(seconds)


def _bump(report: WlBodyRecoveryReport, reason: str) -> None:
    report.failure_reasons[reason] = report.failure_reasons.get(reason, 0) + 1


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_doc_ids(value: str) -> list[str]:
    if not value:
        return []
    return [p for p in re.split(r"[,\s]+", value.strip()) if p]


def is_permanent_403(status: str) -> bool:
    return str(status or "").startswith(_403_PERMANENT_PREFIX)


def is_definitive_status(status: str) -> bool:
    """다시 읽어도 본문이 나오지 않을 사유인가 — 404·no-anchor(페이지는 받았는데 앵커 없음)·영구 403."""
    status = str(status or "")
    if status in DEFINITIVE_STATUSES or is_permanent_403(status):
        return True
    return status.startswith("fetch-fail") and "404" in status


def is_recovery_candidate(raw: dict[str, Any], *, include_permanent_403: bool = False) -> bool:
    """본문 전문이 없고, 다시 읽으면 나올 수 있는 행인가(순수 함수).

    `include_permanent_403` 은 사람이 `--doc-ids` 로 문서를 지정했을 때만 켠다(수동 재시도)."""
    if raw.get("wl_body_full"):
        return False
    status = str(raw.get("wl_body_status") or "")
    if include_permanent_403 and is_permanent_403(status):
        return True
    return not is_definitive_status(status)


def starts_mid_sentence(body: str) -> bool:
    """본문이 문장 중간(소문자)에서 시작하는가 — 시작 앵커 절단의 흔적."""
    return bool(re.match(r"[a-z]", str(body or "")))


def is_body_start_candidate(raw: dict[str, Any], *, explicit: bool = False) -> bool:
    """`--rebuild-body-start` 대상: 본문이 있고, 소문자로 시작하거나 사람이 지정한 행."""
    full = str(raw.get("wl_body_full") or "")
    return bool(full) and (explicit or starts_mid_sentence(full))


def first_403_date(raw: dict[str, Any], collected_at: str) -> date | None:
    """이 행에서 403 을 처음 본 날. 모르면 None(→ 오늘을 첫 관측일로 표식한다).

    `fetch-403:since=D`·`fetch-403:permanent since=D` → D · 라이브 수집이 남긴 맨 `fetch-403`
    → 적재일(그때 403 이었다) · 사유가 없거나 다른 사유면 None."""
    status = str(raw.get("wl_body_status") or "")
    text = ""
    if status.startswith(STATUS_403 + ":"):
        m = _403_SINCE_RE.search(status)
        text = m.group(1) if m else ""
    elif status == STATUS_403:
        text = str(collected_at or "")[:10]
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def next_403_status(raw: dict[str, Any], collected_at: str, today: date) -> str:
    """오늘 또 403 을 받은 행의 새 표식(순수 함수). 저장 표식과 같으면 쓸 필요가 없다."""
    since = first_403_date(raw, collected_at)
    if since is None:
        return f"{STATUS_403}:since={today.isoformat()}"
    if (today - since).days >= PERMANENT_403_AFTER_DAYS:
        return f"{_403_PERMANENT_PREFIX} since={since.isoformat()}"
    return str(raw.get("wl_body_status") or "")


# ---------------------------------------------------------------------------
# 지연 import 간접층 — collect_intake 는 무겁고 import 시 환경을 읽으므로, 테스트가 이 모듈
# 속성만 패치해 net-free 로 돌 수 있게 얇게 감싼다(backfill_483_ocr_recovery 와 동일 관례).
# ---------------------------------------------------------------------------


def _fetch_letter(url: str) -> tuple[str, str, str]:
    """편지 페이지 1회 GET(수집기의 재시도 포함) → (excerpt, full, status).

    excerpt·전문 추출기는 같은 HTML 에서 돌린다 — 라이브 수집은 플래그별로 GET 을 두 번
    하지만, 회수에서 같은 페이지를 두 번 받을 이유는 없다(추출 함수는 동일).
    """
    import collect_intake as ci
    html_text, status = ci._fetch_wl_letter_html(url, "(회수)")
    if not html_text:
        return "", "", status
    excerpt = ci._extract_wl_body_excerpt(html_text)
    full = ci._extract_wl_body_full(html_text)
    if not full:
        return excerpt, "", ci.WL_BODY_STATUS_NO_ANCHOR
    return excerpt, full, ci.WL_BODY_STATUS_OK


def _rebuild_item(raw: dict[str, Any], excerpt: tuple[str, str], full: tuple[str, str]) -> Any:
    """저장 raw + 새 본문 → 라이브 수집과 **같은 함수**(`build_wl_item`)로 IntakeItem."""
    from collect_intake import build_wl_item

    def _s(key: str) -> str:
        return str(raw.get(key) or "")

    return build_wl_item(
        firm=_s("firm"), posted_raw=_s("posted_date"), letter_date_raw=_s("letter_date"),
        issuing_office=_s("issuing_office"), subject=_s("subject"), wl_href=_s("url"),
        office_verdict=_s("office_gate_verdict"), excerpt=excerpt, full=full,
    )


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------


def _fetch_raw_signals(base_url: str, service_key: str, *, with_body: bool = False,
                       page_size: int = _DEFAULT_PAGE_SIZE) -> list[dict[str, Any]]:
    """source='FDA Warning Letter' 중 raw_json 에 `wl_body_full` 문자열이 없는 행(서버 좁히기).

    `with_body` 면 반대로 본문이 있는 행(`--rebuild-body-start`)."""
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="raw_signal_id,document_id,collected_at,raw_json",
        page_size=page_size, order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_WL}",
                      "raw_json": ("like" if with_body else "not.like") + ".*wl_body_full*"},
    )


def _upsert_raw_signal(base_url: str, service_key: str, record: dict[str, Any], *,
                       timeout: int = DEFAULT_TIMEOUT_SECONDS,
                       ) -> tuple[int, list[dict[str, Any]] | None, str]:
    """제자리 교체(merge-duplicates) — 일상 적재(ignore-duplicates)와 의도적으로 다르다."""
    return fsa._post_rows(
        base_url, service_key, "raw_signals",
        [fsa._raw_signal_payload(record)], "raw_signal_id",
        timeout=timeout, resolution="merge-duplicates",
    )


def _is_broad_failure(report: WlBodyRecoveryReport) -> bool:
    """fda.gov 전면 차단 같은 인프라성 실패를 조용히 넘기지 않는다(483 회수와 같은 규약).

    표본이 충분하면(3건+) '본문을 못 받음(still_empty)'도 실패로 센다 — 절반 이상이 403·5xx 면
    러너가 막힌 것이다. 표본이 작을 때는 재구성·쓰기 실패(failed)만 본다: 끈질긴 403 편지
    한 통이 매일 경보를 울리게 두지 않는다(그 편지는 리포트의 still_empty 에 남는다)."""
    if report.attempted == 0:
        return False
    if report.attempted < _BROAD_FAILURE_MIN_ATTEMPTS:
        return report.failed == report.attempted
    bad = report.failed + report.still_empty
    return (bad / report.attempted) >= _BROAD_FAILURE_RATIO


def _write(report: WlBodyRecoveryReport, *, label: str, row: dict[str, Any], item: Any,
           base: str, service_key: str, dry_run: bool,
           upsert_fn: Callable[..., tuple[int, list[dict[str, Any]] | None, str]]) -> bool:
    """재구성 item → 레코드 → id 검증 → (apply 면) upsert. 성공 여부."""
    record = findings_store.raw_signal_from_intake_item(
        item, collected_at=str(row.get("collected_at") or ""),   # ★원본 시점 보존
    )
    if (str(record.get("raw_signal_id") or "") != str(row.get("raw_signal_id") or "")
            or str(record.get("document_id") or "") != str(row.get("document_id") or "")):
        report.id_mismatch += 1
        _bump(report, "raw_signal_id_mismatch")
        report.errors.append(f"{label}: raw_signal_id_mismatch")
        return False
    if dry_run:
        return True
    try:
        _status, _returned, err = upsert_fn(base, service_key, record)
    except Exception as exc:  # noqa: BLE001
        err = f"raised:{type(exc).__name__}"
    if err:
        report.failed += 1
        _bump(report, f"upsert_failed:{err}")
        report.errors.append(f"{label}: upsert_failed:{err}")
        log("WARN", f"WL raw_signals upsert 실패 {label}: {err}")
        return False
    return True


def run(
    *,
    base_url: str,
    service_key: str,
    dry_run: bool,
    limit: int | None = None,
    delay_seconds: float = _DEFAULT_DELAY_SECONDS,
    doc_ids: list[str] | None = None,
    fetch_raw_signals: Callable[..., list[dict[str, Any]]] | None = None,
    fetch_letter: Callable[[str], tuple[str, str, str]] | None = None,
    rebuild_item: Callable[..., Any] | None = None,
    upsert_raw_signal: Callable[..., tuple[int, list[dict[str, Any]] | None, str]] | None = None,
    sleeper: Sleeper | None = None,
    today: date | None = None,
    rebuild_body_start: bool = False,
) -> tuple[WlBodyRecoveryReport, int]:
    fetch_raw = fetch_raw_signals or _fetch_raw_signals
    letter_fn = fetch_letter or _fetch_letter
    rebuild_fn = rebuild_item or _rebuild_item
    upsert_fn = upsert_raw_signal or _upsert_raw_signal
    sleep_fn = sleeper or _default_sleep
    today = today or datetime.now(timezone.utc).date()

    report = WlBodyRecoveryReport(mode="dry_run" if dry_run else "apply", limit=limit,
                                  delay_seconds=delay_seconds,
                                  rebuild_body_start=rebuild_body_start)
    base = _normalize_base_url(base_url)
    if base is None:
        report.errors.append("SUPABASE_URL must start with https://")
        return report, 2

    try:
        rows = (fetch_raw(base, service_key, with_body=True) if rebuild_body_start
                else fetch_raw(base, service_key))
    except (RuntimeError, ValueError) as exc:
        report.errors.append(str(exc))
        log("ERROR", f"WL 본문 회수 조회 실패: {exc}")
        return report, 2
    report.scanned = len(rows)

    wanted = set(doc_ids or [])

    def _named(r: dict[str, Any]) -> bool:
        return (str(r.get("raw_signal_id") or "") in wanted
                or str(r.get("document_id") or "") in wanted)

    targets: list[dict[str, Any]] = []
    for r in rows:
        raw = _json_object(r.get("raw_json"))
        if rebuild_body_start:
            if is_body_start_candidate(raw, explicit=bool(wanted) and _named(r)):
                targets.append(r)
        elif is_recovery_candidate(raw, include_permanent_403=bool(doc_ids)):
            targets.append(r)
        elif not raw.get("wl_body_full"):
            report.skipped_definitive += 1
    if wanted:
        targets = [r for r in targets if _named(r)]
    targets.sort(key=lambda r: str(r.get("raw_signal_id") or ""))
    if limit is not None and limit > 0:     # 0 이하 = 전건(형제 백필과 같은 규약)
        targets = targets[:limit]
    report.candidates = len(targets)

    for r in targets:
        label = str(r.get("document_id") or r.get("raw_signal_id") or "")
        raw = _json_object(r.get("raw_json"))
        unknown = sorted(set(raw) - KNOWN_RAW_KEYS)
        if unknown:
            report.unknown_keys += 1
            _bump(report, "unknown_raw_keys")
            report.errors.append(f"{label}: unknown_raw_keys:{','.join(unknown)}")
            continue
        url = str(raw.get("url") or "").strip()
        if not url:
            # 편지 URL 이 없는 행은 다시 읽을 곳이 없다 — 시도로 세지 않는다.
            _bump(report, "missing_url")
            continue

        report.attempted += 1
        sleep_fn(delay_seconds)
        try:
            excerpt, full, status = letter_fn(url)
        except Exception as exc:  # noqa: BLE001 — 건별 실패는 계속
            report.failed += 1
            _bump(report, f"fetch_raised:{type(exc).__name__}")
            report.errors.append(f"{label}: fetch_raised:{type(exc).__name__}")
            continue

        if not full and rebuild_body_start:
            # 있는 본문을 표식으로 덮지 않는다 — 이번에 못 받았으면 그대로 둔다.
            report.still_empty += 1
            _bump(report, status.split(":", 1)[0] or "empty")
            continue
        old_full = str(raw.get("wl_body_full") or "")
        if rebuild_body_start and full == old_full:
            report.body_start_unchanged += 1
            continue
        if not full:
            new_status = status
            if not is_definitive_status(status):
                # 403·5xx 가 또 났다 — 본문은 손대지 않고 다음 실행에 다시 본다.
                report.still_empty += 1
                _bump(report, status.split(":", 1)[0] or "empty")
                if status != STATUS_403:
                    continue
                # 403 만 표식을 관리한다(영구 403 정책 — 모듈 docstring).
                new_status = next_403_status(raw, str(r.get("collected_at") or ""), today)
                if new_status == str(raw.get("wl_body_status") or ""):
                    continue
            # 확정 사유·403 표식 — 기존 발췌는 살리고 사유 표식만 바꾼다.
            old_excerpt = str(raw.get("wl_body_excerpt") or "")
            excerpt_arg = (old_excerpt or excerpt, new_status)
            try:
                item = rebuild_fn(raw, excerpt_arg, ("", new_status))
            except Exception as exc:  # noqa: BLE001
                report.failed += 1
                _bump(report, f"rebuild_raised:{type(exc).__name__}")
                report.errors.append(f"{label}: rebuild_raised:{type(exc).__name__}")
                continue
            if not _write(report, label=label, row=r, item=item, base=base,
                          service_key=service_key, dry_run=dry_run, upsert_fn=upsert_fn):
                continue
            if is_permanent_403(new_status):
                report.permanent_403 += 1
                report.permanent_403_docs.append(label)
                log("WARN", f"WL 본문 영구 403 확정(매일 회수 중단) {label}: {new_status}")
            elif new_status != status:
                report.blocked_403_marked += 1
            else:
                report.status_resolved += 1
            continue

        try:
            # 발췌 앵커만 못 찾은 경우의 사유는 라이브 수집과 같은 no-anchor(전문은 확보).
            item = rebuild_fn(raw, (excerpt, "ok" if excerpt else "no-anchor"), (full, "ok"))
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            _bump(report, f"rebuild_raised:{type(exc).__name__}")
            report.errors.append(f"{label}: rebuild_raised:{type(exc).__name__}")
            continue
        if not _write(report, label=label, row=r, item=item, base=base,
                      service_key=service_key, dry_run=dry_run, upsert_fn=upsert_fn):
            continue
        violations = (getattr(item, "raw_payload", {}) or {}).get("wl_violations") or []
        report.recovered += 1
        if rebuild_body_start:
            report.body_changes.append({
                "raw_signal_id": str(r.get("raw_signal_id") or ""),
                "old_chars": len(old_full), "new_chars": len(full)})
        report.violations_recovered += len(violations)
        if len(report.samples) < _MAX_SAMPLES:
            report.samples.append({
                "raw_signal_id": str(r.get("raw_signal_id") or ""), "document_id": label,
                "previous_status": str(raw.get("wl_body_status") or ""),
                "full_chars": len(full), "violations": len(violations),
                **({"old_head": old_full[:60], "new_head": full[:60]} if rebuild_body_start else {}),
            })

    head = "[DRY] 교체 예정" if dry_run else "회수 upsert"
    log("INFO", f"WL 본문 {head} {report.recovered}건(위반 표제 {report.violations_recovered}건) "
                f"· 대상 {report.candidates}건 · 이번에도 못 받음 {report.still_empty}건 "
                f"· 확정 사유로 표식 교체 {report.status_resolved}건 · 영구 403 확정 "
                f"{report.permanent_403}건 · 실패 {report.failed}건")
    return report, (1 if _is_broad_failure(report) else 0)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="본문 없이 적재된 FDA Warning Letter raw_signals 행을 다시 받아 라이브 수집과 "
                    "같은 함수로 재구성해 제자리 upsert.")
    p.add_argument("--apply", action="store_true", default=False,
                   help="실제 upsert. 기본은 dry-run(조회·fetch·재구성까지, 쓰기 생략).")
    p.add_argument("--limit", type=int, default=None,
                   help="이번 실행의 문서 수 상한. 0 또는 미지정이면 전건.")
    p.add_argument("--delay-seconds", type=float, default=_DEFAULT_DELAY_SECONDS,
                   help=f"편지 요청 간 대기(초) — fda.gov 예의(기본 {_DEFAULT_DELAY_SECONDS:g}).")
    p.add_argument("--doc-ids", default="",
                   help="선택: 처리할 raw_signal_id 또는 document_id 목록(공백/쉼표 구분). "
                        "지정하면 영구 403 표식 행도 다시 시도한다.")
    p.add_argument("--rebuild-body-start", action="store_true", default=False,
                   help="본문이 있지만 문장 중간(소문자)에서 시작하는 행(+ --doc-ids 지정 행)을 다시 "
                        "받아 재구성한다(수동 전용 — 모듈 docstring).")
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
        print("backfill_wl_body_recovery: --supabase-url/--service-role-key 또는 "
              "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요(dry-run 도 조회에 필요).",
              file=sys.stderr)
        return 2
    base_url, service_key = creds
    report, code = run(
        base_url=base_url, service_key=service_key, dry_run=not args.apply,
        limit=args.limit, delay_seconds=args.delay_seconds,
        doc_ids=_parse_doc_ids(args.doc_ids),
        rebuild_body_start=args.rebuild_body_start,
    )
    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.report_path:
        Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
