#!/usr/bin/env python3
"""식약처 GMP 실사 `attachment_deficiency_assessment` 소급 재판정 — 저장 텍스트 전용.

## 왜 필요한가

판정 앵커를 넓혀도(2026-08-12 PR#731) **이미 적재된 행은 바뀌지 않는다.** 수집기는
등록일 창 기반이라 과거분을 다시 훑지 않고, 딥 백필(`collect_mfds_backfill.py`)조차
`resolution=ignore-duplicates` 라 재실행해도 기존 행을 건드리지 않는다. 그래서 원문이
"적합"이라고 명시했는데 우리가 `unknown`(판정 불능)으로 적어 둔 행 96건이 그대로 남는다.

## 왜 네트워크가 필요 없는가

판정 입력인 `attachment_text` 가 **이미 `raw_signals.raw_json` 에 저장돼 있다.** 그러니
PDF 를 다시 받을 이유가 없다 — nedrug 는 해외 IP 차단이라 러너에서 받을 수도 없다
([[grm-mfds-ip-block]]). 저장된 텍스트에 현행 판정 함수를 다시 돌리는 것이 전부다.

★그래서 이 잡이 고칠 수 있는 것과 없는 것이 갈린다:
  - 고침: `attachment_deficiency_assessment`·`attachment_deficiency_excerpt`
          (입력이 저장 텍스트뿐)
  - 못 고침: `gmp_deficiencies`(지적 표) — PDF 바이트가 있어야 한다. 유형 게이트 반전
          (PR#730)은 **신규 수집분부터** 효과가 난다.

## 불가침

- **판정 로직을 복제하지 않는다.** `collect_mfds_gmp_inspection` 의 `_assess_deficiency`·
  `_extract_deficiency_excerpt` 를 그대로 import 한다. 정규식을 SQL 이나 이 파일로 옮겨
  적으면 원본이 바뀔 때 조용히 어긋난다(사본 금지).
- **강등 금지.** `present` → 다른 값, 또는 `none` → `unknown` 같은 후퇴는 적용하지 않고
  보고만 한다. 넓힌 패턴은 상위집합이라 이론상 발생하지 않지만, 발생하면 그건 코드가
  바뀐 것이므로 **조용히 덮어쓰지 말고 사람이 봐야 한다.**
- **dry-run 과 apply 가 같은 판정 함수를 쓴다**(#606 에서 dry-run 전용 분기가 항상 0 을
  보고해 진단을 망친 전례 — 하필 dry-run 이 진단 모드다).

사용:
    python backfill_mfds_gmp_assessment.py                 # dry-run(기본)
    python backfill_mfds_gmp_assessment.py --apply
    python backfill_mfds_gmp_assessment.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import requests

from collect_mfds_gmp_inspection import (
    _assess_deficiency,
    _extract_deficiency_excerpt,
)

SOURCE = "MFDS"
SOURCE_KIND = "gmp-inspection"
_PAGE = 500
_TIMEOUT = 30


@dataclass
class Report:
    mode: str = "dry_run"
    scanned: int = 0
    no_text: int = 0
    unchanged: int = 0
    upgraded: int = 0                      # unknown → none/present (의도한 회수)
    downgrades_blocked: int = 0            # 후퇴 판정 — 적용하지 않고 보고만
    excerpt_filled: int = 0                # 판정은 그대로인데 excerpt 만 새로 생김
    applied: int = 0
    failed: int = 0
    transitions: dict[str, int] = field(default_factory=dict)
    samples: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "scanned": self.scanned, "no_text": self.no_text,
            "unchanged": self.unchanged, "upgraded": self.upgraded,
            "downgrades_blocked": self.downgrades_blocked,
            "excerpt_filled": self.excerpt_filled,
            "applied": self.applied, "failed": self.failed,
            "transitions": dict(sorted(self.transitions.items())),
            "samples": self.samples[:10], "errors": self.errors[:20],
        }


# 판정의 정보량 순위. 낮은 값 → 높은 값만 적용한다(강등 차단).
_RANK = {"unknown": 0, "none": 1, "present": 2}


def is_upgrade(old: str, new: str) -> bool:
    """`unknown` 에서 벗어나는 방향만 승격으로 본다.

    ★`none` ↔ `present` 상호 변환은 승격으로 치지 않는다 — 그건 판정 규칙이 실제로
    바뀌었다는 뜻이라 소급 덮어쓰기가 아니라 사람 검토 대상이다.
    """
    return old == "unknown" and new in ("none", "present")


def _creds() -> tuple[str, str]:
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정")
    return base, key


def fetch_rows(base: str, key: str) -> list[dict[str, Any]]:
    """대상 raw_signals 전량(페이지네이션). 서비스키는 로그·예외에 절대 싣지 않는다."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    url = f"{base}/rest/v1/raw_signals"
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "select": "raw_signal_id,raw_json",
            "source": f"eq.{SOURCE}",
            "source_kind": f"eq.{SOURCE_KIND}",
            "order": "raw_signal_id.asc",
            "limit": str(_PAGE), "offset": str(offset),
        }
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"raw_signals 조회 실패: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise RuntimeError(f"raw_signals 조회 HTTP {resp.status_code}")
        batch = resp.json() or []
        out.extend(batch)
        if len(batch) < _PAGE:
            return out
        offset += _PAGE


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def plan_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """이 행에 적용할 변경(없으면 None). 순수 함수 — dry-run/apply 공용."""
    payload = _payload(row.get("raw_json"))
    text = str(payload.get("attachment_text") or "")
    if not text.strip():
        return {"kind": "no_text"}
    old = str(payload.get("attachment_deficiency_assessment") or "unknown")
    new = _assess_deficiency(text)
    old_excerpt = str(payload.get("attachment_deficiency_excerpt") or "")
    new_excerpt = _extract_deficiency_excerpt(text)

    if new != old and not is_upgrade(old, new):
        return {"kind": "downgrade_blocked", "old": old, "new": new}
    changed_assess = new != old
    # excerpt 는 **채우기만** 한다. 기존 값을 다른 값으로 바꾸지 않는다(발행된 카드의
    # 인용문이 소급으로 흔들리면 안 된다 — 앵커를 맨 뒤에 둔 이유와 같은 규율).
    fill_excerpt = bool(new_excerpt) and not old_excerpt
    if not (changed_assess or fill_excerpt):
        return None

    updated = dict(payload)
    if changed_assess:
        updated["attachment_deficiency_assessment"] = new
    if fill_excerpt:
        updated["attachment_deficiency_excerpt"] = new_excerpt
    return {
        "kind": "update", "old": old, "new": new,
        "changed_assess": changed_assess, "fill_excerpt": fill_excerpt,
        "raw_json": updated,
    }


def _patch(base: str, key: str, raw_signal_id: str, payload: dict[str, Any]) -> None:
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json", "Prefer": "return=minimal",
    }
    url = f"{base}/rest/v1/raw_signals"
    params = {"raw_signal_id": f"eq.{raw_signal_id}"}
    body = {"raw_json": json.dumps(payload, ensure_ascii=False)}
    resp = requests.patch(url, params=params, headers=headers, json=body, timeout=_TIMEOUT)
    if resp.status_code >= 400:
        raise RuntimeError(f"PATCH HTTP {resp.status_code}")


def run(apply: bool) -> Report:
    base, key = _creds()
    report = Report(mode="apply" if apply else "dry_run")
    rows = fetch_rows(base, key)
    report.scanned = len(rows)

    for row in rows:
        plan = plan_row(row)
        if plan is None:
            report.unchanged += 1
            continue
        if plan["kind"] == "no_text":
            report.no_text += 1
            continue
        if plan["kind"] == "downgrade_blocked":
            report.downgrades_blocked += 1
            report.errors.append(
                f"강등 판정 차단 {row.get('raw_signal_id')}: {plan['old']} → {plan['new']}")
            continue

        key_t = f"{plan['old']}→{plan['new']}" if plan["changed_assess"] else "excerpt-only"
        report.transitions[key_t] = report.transitions.get(key_t, 0) + 1
        if plan["changed_assess"]:
            report.upgraded += 1
        if plan["fill_excerpt"]:
            report.excerpt_filled += 1
        if len(report.samples) < 10:
            report.samples.append({
                "raw_signal_id": str(row.get("raw_signal_id") or ""),
                "transition": key_t,
                "excerpt": str(plan["raw_json"].get(
                    "attachment_deficiency_excerpt") or "")[:80],
            })
        if not apply:
            continue
        try:
            _patch(base, key, str(row.get("raw_signal_id")), plan["raw_json"])
            report.applied += 1
        except RuntimeError as exc:
            report.failed += 1
            report.errors.append(f"{row.get('raw_signal_id')}: {exc}")
    return report


def main(argv: list[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="식약처 GMP 실사 판정 소급 재계산(저장 텍스트 전용)")
    ap.add_argument("--apply", action="store_true", help="실제 반영(기본은 dry-run)")
    ap.add_argument("--json", default=None, help="리포트 JSON 저장 경로")
    args = ap.parse_args(argv)

    report = run(apply=args.apply)
    data = report.as_dict()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    # 강등 판정이 있으면 적색 — 조용히 지나가면 안 되는 신호다.
    if report.downgrades_blocked or report.failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
