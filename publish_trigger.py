"""발행 머지 판정 — "이 main 배포가 주간 브리프 발행인가"를 정하는 단일 함수.

`grm-newsletter-send.yml` 은 `GRM Web Deploy` 가 성공하면 workflow_run 으로 깨어나는데,
모든 main 배포가 발행은 아니다. 발행 배포일 때만 발송 잡(validate-build·send·
newsletter-en)이 생겨야 한다. 그 판정을 워크플로 `if` 식에 두면 시험할 길이 배포뿐이라
여기 한 곳으로 옮겼다. 워크플로(trigger-gate 잡)는 이 모듈의 출력만 쓴다.

배경(2026-11-23) — 11-16 수리(#1792)는 `startsWith(head_commit.message, 'publish(web)')`
였다. squash 머지만 가정한 식이라, 11-23 발행 PR #1873 이 **일반 머지**로 들어가자 배포
커밋 제목이 `Merge pull request #1873 from MINHOYEOM/publish/brief-2026-11-23` 이 되어
발송 런이 skipped 됐다(실행 45094478874). 예비 예약도 16:06Z 에야 떠서 대표가 손으로 보냈다.

발행으로 보는 커밋 제목(첫 줄) — 머지 방식과 무관하게 발행 PR 이면 참:
  ① `publish(web)…` 로 시작 — squash 머지(`publish(web): 2026-11-16 주간 브리프 (#1764)`),
     rebase 머지(마지막 커밋 제목), 직접 커밋(조립 커밋 제목) 모두 이 꼴이다.
  ② `Merge pull request #N from <owner>/publish/brief-YYYY-MM-DD…` — 일반 머지.
본문 아무 데나 `publish/brief-` 가 들어 있다고 발행으로 보지 않는다(브랜치 이름을 언급한
수리 커밋이 발송을 깨우면 안 된다). 제목만 본다.

CLI: `python publish_trigger.py` — 환경변수 EVENT_NAME·WR_CONCLUSION·HEAD_MESSAGE 를 읽어
stdout 에 `is_publish=…`·`publish_date=…`·`reason=…` 라인을 낸다(GITHUB_OUTPUT 에 그대로
append). 종료코드는 늘 0 — 결과는 값으로만 전달한다. 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import os
import re
import sys

PUBLISH_PREFIX = "publish(web)"

# 일반 머지 제목 — GitHub 기본 형식. owner 는 아무 값이어도 되지만 브랜치는 publish/brief-날짜.
_MERGE_RE = re.compile(
    r"^Merge pull request #\d+ from [^/\s]+/publish/brief-(\d{4}-\d{2}-\d{2})\b")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _title(message: str | None) -> str:
    return (message or "").strip().splitlines()[0].strip() if (message or "").strip() else ""


def classify_commit(message: str | None) -> tuple[bool, str, str | None]:
    """커밋 메시지 → (발행 여부, 사유, 발행일 또는 None). 제목(첫 줄)만 본다."""
    title = _title(message)
    if not title:
        return False, "커밋 제목 없음", None
    if title.startswith(PUBLISH_PREFIX):
        m = _DATE_RE.search(title)
        return True, "제목이 publish(web) 로 시작(squash·rebase·직접 커밋)", (m.group(1) if m else None)
    m = _MERGE_RE.match(title)
    if m:
        return True, "일반 머지 — 발행 브랜치 publish/brief-날짜", m.group(1)
    return False, f"발행 커밋 아님: {title[:80]}", None


def decide(event_name: str, wr_conclusion: str, head_message: str | None) -> tuple[bool, str, str | None]:
    """발송 잡을 만들 것인가. workflow_run 이 아니면(예약·수동) 늘 참 — 그쪽은 기존 경로."""
    if event_name != "workflow_run":
        return True, f"{event_name or '(없음)'} — 발행 판정 대상 아님(기존 경로)", None
    if wr_conclusion != "success":
        return False, f"배포 결과 {wr_conclusion or '(없음)'} — 성공한 배포만", None
    return classify_commit(head_message)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ok, reason, pub = decide(os.environ.get("EVENT_NAME", ""),
                             os.environ.get("WR_CONCLUSION", ""),
                             os.environ.get("HEAD_MESSAGE", ""))
    # 사유는 한 줄로(GITHUB_OUTPUT 줄 형식 보호).
    reason = reason.replace("\n", " ").replace("\r", " ")
    sys.stdout.write(f"is_publish={'true' if ok else 'false'}\n")
    sys.stdout.write(f"publish_date={pub or ''}\n")
    sys.stdout.write(f"reason={reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
