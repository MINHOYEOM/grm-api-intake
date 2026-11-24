"""수집기 health와 같은 범위의 DB 집계로 일별 대조 행을 만든다."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date


# collect_intake의 health 키. 두 수집기의 LAST_HEALTH를 같은 실행에서 복사해 전달한다.
SUPPORTED_SOURCES = ("mfds_admin", "mfds_recall")


def _count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def build_daily_rows(
    day: date,
    source_health: Mapping[str, Mapping[str, object] | None],
    db_counts: Mapping[str, int | None],
) -> list[dict[str, str | int | bool | None]]:
    """날짜와 소스당 한 행을 반환한다.

    호출자는 해당 날짜의 수집기 LAST_HEALTH를 전달한다. 수집 상태가 없으면
    해당 소스 키를 생략하거나 None을 전달한다. total_count는 날짜·
    관련성 필터 전의 API 목록 총수다. DB 건수는 같은 목록 범위와 단위로 집계한
    값만 전달하고, 그런 집계가 없으면 키를 생략하거나 None을 전달한다.
    건수 차이는 원천 총수 - DB 건수이며 문서별 누락이나 완전성은 여기서 판정하지 않는다.
    """
    rows: list[dict[str, str | int | bool | None]] = []
    for source in SUPPORTED_SOURCES:
        health = source_health.get(source)
        if health is None:
            health = {}
        source_total = _count(health.get("total_count"))
        pages_seen = _count(health.get("pages_seen"))
        truncated = health.get("truncated")
        warnings = health.get("page_warnings")
        warning_count = len(warnings) if isinstance(warnings, (list, tuple)) else None
        db_count = _count(db_counts.get(source))

        # data.go.kr 파서는 총수 누락/파싱 실패도 0으로 기록한다.
        difference = None
        if source_total and pages_seen and db_count is not None:
            difference = source_total - db_count

        if difference is None:
            status = "unavailable"
        elif truncated is not False or warning_count != 0:
            status = "incomplete"
        else:
            status = "measured"

        rows.append({
            "day": day.isoformat(),
            "source": source,
            "source_total": source_total,
            "db_count": db_count,
            "difference": difference,
            "measurement_status": status,
            "pages_seen": pages_seen,
            "truncated": truncated if isinstance(truncated, bool) else None,
            "page_warning_count": warning_count,
        })
    return rows
