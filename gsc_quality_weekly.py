#!/usr/bin/env python3
"""Record GSC URL Inspection sample metrics and the latest Search Analytics date."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import requests

from grm_cli import load_json_object, normalize_supabase_url
from gsc_index_sample import KST, week_key


STAGE_1 = dt.date(2026, 11, 19)
STAGE_2 = dt.date(2026, 11, 26)
BUCKETS = tuple((lang, thickness) for lang in ("ko", "en") for thickness in ("thin", "thick"))
VERDICTS = {"PASS", "NEUTRAL", "FAIL"}
NOINDEX = {"BLOCKED_BY_META_TAG", "BLOCKED_BY_HTTP_HEADER"}
INDEXING_STATES = NOINDEX | {"INDEXING_ALLOWED", "BLOCKED_BY_ROBOTS_TXT"}


def phase(day: dt.date) -> str:
    if day < STAGE_1:
        return "1단계 전"
    if day == STAGE_1:
        return "1단계 전환일"
    if day < STAGE_2:
        return "1단계 후 · 2단계 전"
    if day == STAGE_2:
        return "2단계 전환일"
    return "2단계 후"


def latest_gsc_date(url: str, key: str, day: dt.date) -> dt.date | None:
    """gsc_daily contains Search Analytics, without indexing or noindex counts."""
    start = day - dt.timedelta(days=7)
    response = requests.get(
        f"{url}/rest/v1/gsc_daily",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        params={"select": "snap_date", "and": f"(snap_date.gte.{start},snap_date.lt.{day})",
                "order": "snap_date.desc", "limit": 1},
        timeout=20,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}")
    rows = response.json()
    if not isinstance(rows, list) or len(rows) > 1:
        raise ValueError("gsc_daily 응답 형식 오류")
    if not rows:
        return None
    latest = dt.date.fromisoformat(rows[0]["snap_date"])
    if not start <= latest < day:
        raise ValueError("기준일 범위 오류")
    return latest


def sample_rows(path: Path, day: dt.date) -> dict[tuple[str, str], list[dict]]:
    sample = load_json_object(path)
    if sample.get("week") != week_key(day) or sample.get("observed_on") != day.isoformat():
        raise ValueError("표본의 관측일 또는 주차 불일치")
    buckets = {bucket: [] for bucket in BUCKETS}
    if not isinstance(sample.get("rows"), list):
        raise ValueError("표본 행 형식 오류")
    for row in sample["rows"]:
        if not isinstance(row, dict) or (row.get("lang"), row.get("bucket")) not in BUCKETS:
            raise ValueError("표본 버킷 형식 오류")
        buckets[row["lang"], row["bucket"]].append(row)
    return buckets


def metrics(rows: list[dict]) -> tuple[str, str, bool, bool]:
    verdicts = [r["verdict"] for r in rows if not r.get("error") and r.get("verdict") in VERDICTS]
    states = [r["indexing"] for r in rows if not r.get("error") and r.get("indexing") in INDEXING_STATES]
    indexed = verdicts.count("PASS")
    excluded = sum(state in NOINDEX for state in states)
    ratio = f"{indexed}/{len(verdicts)} ({indexed / len(verdicts):.1%})" if verdicts else "자료 없음"
    noindex = f"{excluded}건 / 확인 {len(states)}건" if states else "자료 없음"
    return noindex, ratio, bool(states and verdicts), min(len(states), len(verdicts)) < len(rows)


def main(argv: list[str] | None = None, *, today: dt.date | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=Path("gsc_index_sample.json"))
    parser.add_argument("--report-dir", type=Path, default=Path("_ops/reports"))
    day = today or dt.datetime.now(KST).date()
    args = parser.parse_args(argv)

    errors = []
    warnings = []
    latest = None
    url = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if url and key:
        try:
            latest = latest_gsc_date(url, key, day)
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append(f"gsc_daily REST 조회 실패 ({type(exc).__name__})")
        else:
            if latest is None:
                errors.append("gsc_daily 최근 7일 자료 없음")
    else:
        errors.append("SUPABASE_URL 또는 SUPABASE_SERVICE_ROLE_KEY 설정 누락")

    try:
        buckets = sample_rows(args.sample, day)
    except (OSError, ValueError):
        errors.append("이번 주 URL 검사 표본이 없거나 읽을 수 없음")
        buckets = {bucket: [] for bucket in BUCKETS}
    else:
        if not any(buckets.values()):
            errors.append("이번 주 URL 검사 표본 0건")

    week = week_key(day)
    lines = [
        f"## 구글 품질 주간 지표 {week}", "",
        f"- 관측일 {day} KST · **{phase(day)}**",
        "- 비교 기준일 1단계 #1830 (2026-11-19) · 2단계 #1836~#1841 (2026-11-26)",
        "- 기준일 당일은 전환일로 표시합니다. 과거 관측값은 소급 집계하지 않습니다.",
        f"- `gsc_daily` 검색 실적 최신 기준일 {latest or '자료 없음'} (색인 집계와 별도)", "",
        "색인 지표는 URL 검사 표본에 한합니다. 색인 비율은 유효 판정 중 PASS 비율이며, "
        "noindex는 메타 태그 또는 HTTP 헤더 차단을 합산합니다. 언어별 URL을 각각 세며, "
        "표본 URL은 주차에 따라 달라질 수 있습니다.", "",
        "| W 표본 버킷 | 검사 시도 | noindex 제외 | 색인 비율 |",
        "|---|---:|---:|---:|",
    ]
    all_rows = []
    for (lang, thickness), rows in buckets.items():
        label = f"{lang}·{thickness}"
        all_rows.extend(rows)
        noindex, ratio, usable, incomplete = metrics(rows)
        lines.append(f"| {label} | {len(rows)} | {noindex} | {ratio} |")
        if not usable and rows:
            errors.append(f"{label} 버킷의 필수 지표 자료 없음")
        elif not rows or incomplete:
            warnings.append(f"{label} 버킷에 비어 있거나 판정할 수 없는 표본이 있음")
    noindex, ratio, _, _ = metrics(all_rows)
    lines.append(f"| **전체 표본** | {len(all_rows)} | {noindex} | {ratio} |")
    if errors or warnings:
        lines.extend(["", *(f"- ❌ {error}" for error in errors), *(f"- ⚠️ {warning}" for warning in warnings)])

    report = "\n".join(lines) + "\n"
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as output:
            output.write(report)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / f"gsc_quality_{week}.md").write_text(report, encoding="utf-8")
    print(f"구글 품질 {week} 보고서 기록 완료")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
