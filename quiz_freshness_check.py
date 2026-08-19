"""주간 퀴즈 신선도 감시 — "이번 주 문항이 실제로 생성됐는가"를 클라우드에서 본다.

출제 파이프라인(`grm-monday-quiz-gen`)은 GitHub Actions 가 아니라 **운영자 데스크톱의
로컬 예약 태스크**로 돈다.  데스크톱이 꺼져 있거나 세션이 스킵하면 그 주 문항이 생기지
않는데, 지금까지 그 사실은 어디에도 남지 않았다 — 사용자 화면은 legacy 회전 세트를
띄우고, 저장소에는 커밋도 이슈도 남지 않으며, 로컬 태스크의 침묵은 로컬 밖에서 보이지
않는다.  **로컬에서 도는 자동화의 실패는 로컬 밖에서 감시해야 한다.**

검사는 순수 파일 대조다(네트워크·secret 불필요):
  실행일(KST) 기준 ISO 주차 YYYYWW 가 web/data/quiz_bank.json 의 week 집합에 있는가.

exit 0 = 이번 주 세트 있음 / 1 = 없음(알림 대상) / 2 = 입력 오류.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_QUIZ_BANK = REPO_ROOT / "web" / "data" / "quiz_bank.json"

KST = dt.timezone(dt.timedelta(hours=9))

# 생성 파이프라인이 주차를 KST 실행일로 계산하므로(운영설계 addendum §1) 감시도 KST 로 센다.
# UTC 로 세면 월요일 09:00 KST 이전이 전주로 잡혀 매주 거짓 경보가 난다.


def iso_week_key(day: dt.date) -> str:
    """YYYYWW — ISO 8601 주차(생성 파이프라인·quiz.js 와 같은 표기)."""
    year, week, _ = day.isocalendar()
    return f"{year}{week:02d}"


def _bank_weeks(bank: Any) -> tuple[list[str], list[str]]:
    """(정렬된 주차 목록, 오류 목록). 스키마 정합은 quiz_lint.py 소관이라 여기선 관대하게 읽는다."""
    if not isinstance(bank, list):
        return [], ["quiz_bank.json 최상위 값이 배열이 아닙니다"]
    weeks: set[str] = set()
    for item in bank:
        if not isinstance(item, dict):
            continue
        week = item.get("week")
        if isinstance(week, str) and week.strip():
            weeks.add(week.strip())
        elif isinstance(week, int):
            weeks.add(str(week))
    return sorted(weeks), []


def check(
    quiz_bank: Path = DEFAULT_QUIZ_BANK,
    as_of: dt.date | None = None,
) -> dict[str, Any]:
    """신선도 판정 리포트(순수 — 파일 1개만 읽는다)."""
    today = as_of or dt.datetime.now(KST).date()
    current = iso_week_key(today)
    report: dict[str, Any] = {
        "checked_date_kst": today.isoformat(),
        "current_week": current,
        "quiz_bank": str(quiz_bank),
        "fresh": False,
        "weeks_behind": None,
        "latest_week": None,
        "week_count": 0,
        "errors": [],
    }

    try:
        bank = json.loads(quiz_bank.read_text(encoding="utf-8"))
    except OSError as exc:
        report["errors"].append(f"quiz_bank.json 을 읽지 못했습니다: {exc}")
        return report
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report["errors"].append(f"quiz_bank.json 파싱 실패: {exc}")
        return report

    weeks, errors = _bank_weeks(bank)
    report["errors"].extend(errors)
    if errors:
        return report

    report["week_count"] = len(weeks)
    report["latest_week"] = weeks[-1] if weeks else None
    report["fresh"] = current in weeks

    # 며칠이 아니라 **몇 주** 밀렸는지를 센다 — 한 주 스킵과 3주 연속 스킵은 대응이 다르다.
    if weeks:
        try:
            latest_year, latest_week = int(weeks[-1][:4]), int(weeks[-1][4:])
            latest_monday = dt.date.fromisocalendar(latest_year, latest_week, 1)
            current_monday = dt.date.fromisocalendar(*today.isocalendar()[:2], 1)
            report["weeks_behind"] = max(0, (current_monday - latest_monday).days // 7)
        except ValueError as exc:
            report["errors"].append(f"최신 주차 {weeks[-1]!r} 해석 실패: {exc}")

    return report


def format_report(report: dict[str, Any]) -> str:
    status = "FRESH" if report["fresh"] and not report["errors"] else "STALE"
    lines = [
        f"quiz_freshness: {status}",
        f"checked_date_kst: {report['checked_date_kst']}",
        f"current_week: {report['current_week']}",
        f"latest_week: {report['latest_week']}",
        f"weeks_behind: {report['weeks_behind']}",
        f"week_count: {report['week_count']}",
        f"errors: {len(report['errors'])}",
    ]
    lines.extend(f"ERROR {message}" for message in report["errors"])
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="주간 퀴즈 신선도 감시(이번 주 세트 존재 확인)")
    parser.add_argument("--quiz-bank", type=Path, default=DEFAULT_QUIZ_BANK)
    parser.add_argument("--as-of", default="", help="KST 기준 검사일(YYYY-MM-DD) — 테스트용")
    parser.add_argument("--output", type=Path, default=None, help="리포트 JSON 저장 경로")
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    except (SystemExit, ValueError) as exc:
        print("quiz_freshness: STALE")
        print(f"ERROR 인자 오류: {exc}")
        return 2

    report = check(args.quiz_bank, as_of)
    if args.output:
        try:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            print(f"ERROR 리포트 저장 실패: {exc}")
    print(format_report(report))
    if report["errors"]:
        return 2
    return 0 if report["fresh"] else 1


if __name__ == "__main__":
    sys.exit(main())
