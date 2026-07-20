"""발행 워치독 판정 로직 — 자가 복구 기동/경보 여부를 결정하는 순수 함수.

워크플로(.github/workflows/grm-publish-watchdog.yml) 안의 bash 로 두면 단위 테스트가
불가능해, 판정만 여기로 분리한다. 워크플로는 사실 수집(델타 존재·발행 PR 존재·오늘
기동 횟수·KST 시각)만 하고 결정은 이 모듈에 위임한다.

배경(2026-07-20) — 월요일 발행 크론은 **드롭이 아니라 수 시간 지각**한다:
  07-13  브릿지 00:30 미발화 · 03:30 → 04:07Z(37분 지각)
  07-20  브릿지 00:30 미발화 · 03:30 → 04:12Z(42분 지각) · 워치독 01:00 → 04:28Z(3h28m 지각)
`docs/GRM_자동화지도_2026-07.md` §도 "수 시간 지연이 정상 범위 — 발화 자체는 정상"으로
기록하고 있다. 그래서 워치독은 "감지 후 사람 호출"이 아니라 **직접 브릿지를 기동**한다.

CLI: `python publish_watchdog.py dispatch|escalate --<flags>` → stdout 에 `key=value` 라인
(GITHUB_OUTPUT 에 그대로 append 가능). 종료코드는 항상 0 — 판정 결과는 값으로만 전달한다
(워크플로가 조건 분기로 쓰므로 실패로 죽이지 않는다).
"""
from __future__ import annotations

import argparse
import sys

# 워치독은 월요일 3회 돈다. 그 중 최대 몇 번까지 브릿지를 기동할지의 상한 —
# 브릿지가 구조적으로 실패하는 주에 워치독이 계속 때리는 것을 막는다(유한성 계약).
DEFAULT_DISPATCH_CAP = 2

# 이 시각(KST) 전에는 경보하지 않는다 — 조용히 복구만 시도한다.
# 첫 회차(09:13)는 Routine 예치(07:30)가 늦은 경우와 구분이 안 되므로, 이른 시각의
# 부재를 곧바로 "실패"로 부르면 오탐이 된다. 복구 시도는 멱등이라 일찍 해도 무해하지만
# 경보는 늦게 한다 — "일찍 고치고, 늦게 소리친다".
DEFAULT_QUIET_BEFORE_KST_HOUR = 11


def decide_dispatch(delta_exists: bool, publish_pr_exists: bool,
                    dispatches_today: int, cap: int = DEFAULT_DISPATCH_CAP) -> tuple[bool, str]:
    """브릿지를 기동할 것인가. 반환 = (기동 여부, 사유).

    멱등 계약 — 이미 결과가 있으면 절대 기동하지 않는다:
      · 델타가 main 에 있다 = 이관 완료(브릿지가 할 일 없음)
      · 발행 PR 이 있다 = 이관은 물론 조립까지 끝났다(델타가 지워졌거나 경로가 달라도 안전)
    유한 계약 — 오늘 기동 횟수가 상한이면 더 때리지 않는다. 사람이 손으로 dispatch 한
    횟수도 함께 세므로(브릿지의 workflow_dispatch run 전수), 사람과 워치독이 겹쳐 때리는
    경우도 상한에 걸린다.
    """
    if delta_exists:
        return False, "델타가 이미 main 에 있음 — 이관 완료(기동 불필요)"
    if publish_pr_exists:
        return False, "발행 PR 이 이미 존재 — 조립까지 완료(기동 불필요)"
    if dispatches_today >= cap:
        return False, f"오늘 브릿지 기동 {dispatches_today}회로 상한({cap}) 도달 — 추가 기동 중단"
    return True, f"델타·발행PR 모두 부재 — 브릿지 기동({dispatches_today + 1}/{cap})"


def decide_escalate(delta_exists: bool, recovered: bool, kst_hour: int,
                    quiet_before_hour: int = DEFAULT_QUIET_BEFORE_KST_HOUR,
                    publish_pr_exists: bool = False) -> tuple[bool, str]:
    """운영 경고 이슈로 표면화할 것인가. 반환 = (경보 여부, 사유).

    복구됐거나 애초에 정상이면 경보하지 않는다(과알림 0). 이른 회차의 부재는 Routine
    예치 지연과 구분되지 않으므로 조용히 넘긴다 — 다음 회차가 판단한다.

    `publish_pr_exists` 는 기동 판정과 **같은 이유로** 경보도 막는다(2026-07-20 시뮬레이션에서
    발견): 발행 PR 이 이미 있으면 그 주 발행은 정상 진행 중이므로, 델타 파일이 안 보인다는
    이유만으로 경보하면 오탐이다(경로 변경·수동 정리 등으로 델타만 사라질 수 있다).
    """
    if delta_exists:
        return False, "델타 정상 — 경보 없음"
    if publish_pr_exists:
        return False, "발행 PR 존재 — 발행 진행 중이므로 경보 없음"
    if recovered:
        return False, "자가 복구 성공 — 경보 없음(기록만 남김)"
    if kst_hour < quiet_before_hour:
        return False, (f"이른 회차({kst_hour}시 < {quiet_before_hour}시) — 복구만 시도하고 "
                       f"경보는 다음 회차로 미룸(Routine 예치 지연과 구분 불가)")
    return True, f"{kst_hour}시 기준 델타 부재 + 자가 복구 실패 — 경보"


def _emit(pairs: dict[str, object]) -> None:
    # 사유 문자열에 `—`(em dash) 등 비ASCII 가 들어간다. Windows 콘솔 기본 인코딩(cp949)에서는
    # print 가 UnicodeEncodeError 로 죽고, 워크플로의 `set -e` 가 그 실패를 스텝 실패로 올려
    # **복구가 시작조차 못 한다**(2026-07-20 로컬 시뮬레이션에서 발견 — Actions 의 UTF-8
    # 리눅스에서는 재현되지 않는 잠복 결함이다). 출력 직전에 UTF-8 로 고정한다.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    for k, v in pairs.items():
        if isinstance(v, bool):
            v = "true" if v else "false"
        print(f"{k}={v}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="발행 워치독 판정")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dispatch", help="브릿지 기동 여부 판정")
    d.add_argument("--delta-exists", action="store_true")
    d.add_argument("--publish-pr-exists", action="store_true")
    d.add_argument("--dispatches-today", type=int, default=0)
    d.add_argument("--cap", type=int, default=DEFAULT_DISPATCH_CAP)

    e = sub.add_parser("escalate", help="경보 여부 판정")
    e.add_argument("--delta-exists", action="store_true")
    e.add_argument("--recovered", action="store_true")
    e.add_argument("--kst-hour", type=int, required=True)
    e.add_argument("--quiet-before-hour", type=int, default=DEFAULT_QUIET_BEFORE_KST_HOUR)
    e.add_argument("--publish-pr-exists", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "dispatch":
        ok, reason = decide_dispatch(args.delta_exists, args.publish_pr_exists,
                                     args.dispatches_today, args.cap)
        _emit({"dispatch": ok, "dispatch_reason": reason})
    else:
        ok, reason = decide_escalate(args.delta_exists, args.recovered,
                                     args.kst_hour, args.quiet_before_hour,
                                     args.publish_pr_exists)
        _emit({"escalate": ok, "escalate_reason": reason})
    return 0


if __name__ == "__main__":
    sys.exit(main())
