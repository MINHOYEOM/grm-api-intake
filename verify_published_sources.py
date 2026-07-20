"""발행된 카드와 **원문**을 다시 대조한다 — 저장소 밖(네트워크)에서만 가능한 검증층.

왜 별도 층인가. 조립 게이트(`assemble_publish_brief.lint_false_absence_claims`)와 CI 스윕
(`tests/test_published_briefs_integrity.py`)은 **저장소 안의 값끼리만** 본다. 그래서 "우리가
원문을 확보했는데 없다고 말한" 거짓은 잡지만, **애초에 수집이 실패해 원문을 못 받은** 누락은
구조적으로 못 잡는다 — 저장소 어디에도 "원문에는 관찰이 2건 있었다"는 사실이 없기 때문이다.

2026-07-20 전수 점검이 정확히 그 사각을 드러냈다: 483 8건이 관찰 원문을 가진 채로 "관찰 원문
없음" 취급돼 발행됐다(그중 2건은 그 주 발행분). 수집 시점 추출 실패는 health 경고로만 남고
발행물에는 흔적이 없어, 사람이 원문을 직접 열어보기 전에는 알 수 없었다.

이 스크립트는 발행된 483/WL 카드의 원문을 **다시 받아** 지금 파서로 뽑고, 카드가 실제로
보여준 건수와 대조한다. 불일치가 있으면 exit 1 — 워크플로가 이슈로 올린다.

사용:
    python verify_published_sources.py            # 최근 2주 발행분
    python verify_published_sources.py --weeks 0  # 전체(발행 이력 전수)
    python verify_published_sources.py --json out.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from typing import Any

BRIEF_GLOB = os.path.join("web", "data", "briefs", "brief_web_*.json")
DELTA_GLOB = os.path.join("web", "data", "deltas", "delta_*.json")

# 요청 간 간격(초). 소스 서버에 부담을 주지 않기 위한 최소 예의 — 주 1회·수십 건 규모다.
REQUEST_DELAY = 0.5


def _published_state(weeks: int) -> "tuple[dict[str, dict[str, Any]], list[str]]":
    """발행 이력에서 (카드 상태, 대상 날짜) 수집.

    반환 `state[card_id] = {"date","kind","shown","url"}`. `shown` 은 그 카드가 실제로 보여준
    상세 건수(디제스트로 접힌 483 은 브리프에 없으므로 0). 접힌 카드는 델타에서 id 를
    복원한다 — 접힌 것들이야말로 "관찰 없음" 판정을 받은 카드라 검증 대상의 핵심이다.
    """
    briefs = sorted(glob.glob(BRIEF_GLOB))
    if weeks > 0:
        briefs = briefs[-weeks:]
    dates = [os.path.basename(p)[10:-5] for p in briefs]
    state: dict[str, dict[str, Any]] = {}

    for path, date in zip(briefs, dates):
        brief = json.loads(open(path, encoding="utf-8").read())
        for c in brief.get("cards") or []:
            cid = str(c.get("id", ""))
            kind = _card_kind(c)
            if not kind:
                continue
            dd = c.get("deterministic_detail")
            state[cid] = {
                "date": date, "kind": kind,
                "shown": dd.get("count", 0) if isinstance(dd, dict) else 0,
                "url": (c.get("sources") or {}).get("official_url", ""),
            }

    for path in sorted(glob.glob(DELTA_GLOB)):
        date = os.path.basename(path)[6:-5]
        if date not in dates:
            continue
        for cid in (json.loads(open(path, encoding="utf-8").read()).get("cards") or {}):
            if cid.startswith("fda483-") and cid not in state:
                state[cid] = {"date": date + "(접힘)", "kind": "483", "shown": 0, "url": ""}
    return state, dates


def _card_kind(card: dict[str, Any]) -> str:
    if str(card.get("id", "")).startswith("fda483-"):
        return "483"
    if card.get("card_type") == "Warning Letter":
        return "wl"
    return ""


def _found_483(card_id: str) -> "tuple[int, str]":
    import collect_fda_483 as f
    media_id = card_id.split("-", 1)[1]
    text, status = f._fetch_fda483_pdf_text(f._pdf_url(media_id))
    if not text:
        return 0, status
    return len(f._extract_483_observations_from_text(text)), status


def _found_wl(url: str) -> "tuple[int, str]":
    import collect_intake as ci
    import requests
    if not url:
        return 0, "no-url"
    try:
        resp = requests.get(url, timeout=25, headers={
            "User-Agent": "GRM-SourceVerify/1.0 (+github-actions)", "Accept": "text/html"})
        resp.raise_for_status()
    except Exception as e:                                    # noqa: BLE001 — 네트워크는 graceful
        return 0, f"fetch-fail:{type(e).__name__}"
    full = ci._extract_wl_body_full(resp.text)
    if not full:
        return 0, "no-body"
    return len(ci.extract_wl_violations_from_text(full)), "ok"


def verify(weeks: int) -> "tuple[list[dict[str, Any]], list[dict[str, Any]]]":
    """(전체 결과, 불일치) 반환. 불일치 = 카드가 보여준 건수보다 원문이 더 많은 경우."""
    state, dates = _published_state(weeks)
    print(f"대상 브리프: {', '.join(dates) or '(없음)'} · 카드 {len(state)}건", flush=True)
    rows: list[dict[str, Any]] = []
    for i, (cid, info) in enumerate(sorted(state.items()), 1):
        if info["kind"] == "483":
            found, status = _found_483(cid)
        else:
            found, status = _found_wl(info["url"])
        row = {**info, "id": cid, "found": found, "status": status}
        rows.append(row)
        flag = "MISMATCH" if found > info["shown"] else "ok"
        print(f"[{i:>3}/{len(state)}] {cid:<18} {info['date']:<18} "
              f"카드{info['shown']:>2} 원문{found:>2} {status:<16} {flag}", flush=True)
        time.sleep(REQUEST_DELAY)
    # 원문이 카드보다 **많을 때만** 불일치다. 반대(카드가 더 많음)는 파서가 뒤에 더 보수적으로
    # 바뀐 경우라 발행물이 틀렸다는 뜻이 아니다 — 별도 판단이 필요해 여기서 알림을 올리지 않는다.
    mismatches = [r for r in rows if r["found"] > r["shown"]]
    return rows, mismatches


def format_report(mismatches: list[dict[str, Any]]) -> str:
    if not mismatches:
        return "발행 카드와 원문이 전부 일치합니다(불일치 0)."
    lines = [f"발행 카드가 원문보다 적게 보여주는 항목 **{len(mismatches)}건**:", ""]
    lines.append("| 발행일 | 카드 | 카드 표시 | 원문 | 상태 |")
    lines.append("|---|---|---|---|---|")
    for r in mismatches:
        lines.append(f"| {r['date']} | `{r['id']}` | {r['shown']} | {r['found']} | {r['status']} |")
    lines += [
        "",
        "원문에 상세가 있는데 카드가 보여주지 않는다는 뜻입니다. 수집 시점 추출 실패이거나 "
        "파서가 그 사이 개선된 경우입니다.",
        "조치: deep 델타에 해당 카드의 `source_text`(+483 이면 `observations_ko`)를 넣고 "
        "발행을 재조립하면 조립 시점 재추출이 상세를 복원합니다.",
    ]
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="발행 카드 ↔ 원문 대조(네트워크 검증층)")
    ap.add_argument("--weeks", type=int, default=2,
                    help="최근 N개 발행본만 검증(0=전체). 기본 2")
    ap.add_argument("--json", default=None, help="전체 결과 JSON 저장 경로")
    ap.add_argument("--report", default=None, help="불일치 마크다운 보고서 저장 경로")
    args = ap.parse_args(argv)

    rows, mismatches = verify(args.weeks)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
    report = format_report(mismatches)
    print()
    print(report)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
