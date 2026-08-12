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

★[원인 분해 2026-08-12] 종전엔 `원문 > 카드표시` 하나로 알림을 올렸다. 그런데 그 한 줄에
**처방이 정반대인 세 사건**이 섞여 있었다(이슈 #638 26건 실측):
  ① 뽑긴 했는데 `scope_status='non_pharma'` 라 **의도대로 감춘 것** — 결함이 아니다.
     실측 3문서 24건(혈액·제대혈·조직은행)이 전부 여기였는데 결함으로 보고되고 있었다.
  ② findings 는 있고 scope 도 'ok' 인데 카드가 덜 보여줌 — **발행 격차**(재조립으로 복원).
  ③ findings 자체가 없음 — **추출 격차**(수집 시점 파서 실패).
원문 관찰 수(`found`)는 scope 를 모르는 날것이라 ①을 걸러낼 수 없다. 그래서 DB 에서
문서별 `scope_status` 분포를 읽어 세 갈래로 가른다. 같은 실수를 다른 검사기에서 이미 한 번
했다([[미번역 909 = 허위경보]]: 원시 카운트를 scope 로 분해해야 했다).

scope 규칙은 **복제하지 않는다** — 정본은 마이그레이션 010 의 SQL 이고, 여기서는 그 결과
(`findings.scope_status`)를 읽기만 한다. 자격증명이 없으면 종전 판정으로 degrade 하되
**보고서에 그 사실을 명시**한다(조용한 강등 금지).

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


def _db_creds() -> "tuple[str, str] | None":
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return (base, key) if base and key else None


def _db_scope_counts(state: dict[str, dict[str, Any]]) -> "dict[str, dict[str, int]] | None":
    """카드별 DB findings 분포 `{card_id: {"total": n, "ok": n}}`. 자격증명 없으면 None.

    대응 키가 유형마다 다르다 — 483 은 `document_id` 가 카드 id 와 같고(`fda483-193645`),
    WL 은 `document_id` 가 불투명 해시라 **`evidence_url` 로 맞춘다**(카드의 official_url).
    """
    creds = _db_creds()
    if creds is None:
        return None
    base, key = creds
    import requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    url = f"{base}/rest/v1/findings"

    def _tally(rows: "list[dict[str, Any]]", bucket: dict[str, int]) -> None:
        for r in rows:
            bucket["total"] += 1
            if (r.get("scope_status") or "") == "ok":
                bucket["ok"] += 1

    out: dict[str, dict[str, int]] = {cid: {"total": 0, "ok": 0} for cid in state}
    ids_483 = [cid for cid, i in state.items() if i["kind"] == "483"]
    # 483: id 는 `fda483-\d+` 라 예약문자가 없어 in.() 에 그대로 넣어도 안전하다.
    for chunk in (ids_483[i:i + 100] for i in range(0, len(ids_483), 100)):
        params = {"select": "document_id,scope_status",
                  "document_id": f"in.({','.join(chunk)})"}
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        for row in resp.json() or []:
            bucket = out.get(str(row.get("document_id") or ""))
            if bucket is not None:
                _tally([row], bucket)
    # WL: URL 은 쉼표·괄호를 담을 수 있어 in.() 에 넣지 않고 카드당 eq. 로 조회한다
    # (주당 몇 건 규모라 요청 수가 문제되지 않는다).
    for cid, info in state.items():
        if info["kind"] == "wl" and info["url"]:
            params = {"select": "scope_status", "evidence_url": f"eq.{info['url']}"}
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            _tally(resp.json() or [], out[cid])
    return out


def classify(row: dict[str, Any]) -> str:
    """한 행의 판정. ok / scope-excluded / publish-gap / extraction-gap / unknown-no-db.

    ★세 사건을 한 카운터에 합치지 않는다 — 처방이 전부 다르다.
    """
    found, shown = row["found"], row["shown"]
    if found <= shown:
        return "ok"
    total, ok = row.get("db_total"), row.get("db_ok")
    if total is None or ok is None:
        return "unknown-no-db"      # DB 를 못 봤다 → 종전 판정(보수적으로 불일치 취급)
    if found > total:
        return "extraction-gap"     # 원문에 있는데 우리가 뽑지 못했다
    if ok > shown:
        return "publish-gap"        # 뽑았고 공개 대상인데 카드가 덜 보여줬다
    return "scope-excluded"         # 뽑았으나 scope 로 정상 제외 — 결함 아님


def verify(weeks: int) -> "tuple[list[dict[str, Any]], list[dict[str, Any]]]":
    """(전체 결과, 불일치) 반환. 불일치 = scope 로 설명되지 않는 격차만."""
    state, dates = _published_state(weeks)
    print(f"대상 브리프: {', '.join(dates) or '(없음)'} · 카드 {len(state)}건", flush=True)
    try:
        scope = _db_scope_counts(state)
        reason = "SUPABASE_URL/KEY 미설정"
    except Exception as exc:              # noqa: BLE001 — 관측 잡이 조회 실패로 죽으면 안 된다
        scope, reason = None, f"조회 실패({type(exc).__name__})"
    if scope is None:
        # 강등은 하되 **조용히** 하지 않는다: 이 상태의 행은 'unknown-no-db' 로 알림에 남는다.
        print(f"::warning title=source-verify::{reason} — scope 분해 없이 종전 판정으로 "
              "진행(허위경보 가능)", flush=True)
    rows: list[dict[str, Any]] = []
    for i, (cid, info) in enumerate(sorted(state.items()), 1):
        if info["kind"] == "483":
            found, status = _found_483(cid)
        else:
            found, status = _found_wl(info["url"])
        counts = (scope or {}).get(cid)
        row = {**info, "id": cid, "found": found, "status": status,
               "db_total": counts["total"] if counts else None,
               "db_ok": counts["ok"] if counts else None}
        row["verdict"] = classify(row)
        rows.append(row)
        print(f"[{i:>3}/{len(state)}] {cid:<18} {info['date']:<18} "
              f"카드{info['shown']:>2} 원문{found:>2} "
              f"DB{'' if counts is None else str(counts['ok']) + '/' + str(counts['total']):>6} "
              f"{status:<16} {row['verdict']}", flush=True)
        time.sleep(REQUEST_DELAY)
    # scope 로 설명되는 격차(scope-excluded)는 결함이 아니므로 알림에서 뺀다. 반대 방향
    # (카드가 더 많음)도 알리지 않는다 — 파서가 뒤에 더 보수적으로 바뀐 경우라 발행물이
    # 틀렸다는 뜻이 아니다.
    mismatches = [r for r in rows
                  if r["verdict"] in ("extraction-gap", "publish-gap", "unknown-no-db")]
    return rows, mismatches


_VERDICT_HELP = {
    "extraction-gap": ("추출 격차 — 원문에는 있는데 **findings 자체가 없습니다**(수집 시점 "
                       "파서 실패). 조치: 해당 소스 파서를 보고, 원문 재수집·재추출."),
    "publish-gap": ("발행 격차 — findings 가 있고 공개 대상(`scope_status='ok'`)인데 카드가 "
                    "덜 보여줬습니다. 조치: deep 델타에 `source_text`(+483 이면 "
                    "`observations_ko`)를 넣고 발행 재조립."),
    "unknown-no-db": ("판정 불가 — DB 자격증명이 없어 scope 분해를 못 했습니다. 이 행들은 "
                      "허위경보일 수 있습니다(워크플로에 `SUPABASE_*` 를 넣으면 갈립니다)."),
}


def format_report(mismatches: list[dict[str, Any]],
                  rows: "list[dict[str, Any]] | None" = None) -> str:
    # 조용한 축소 금지: scope 로 제외한 건수를 결과와 **함께** 보고한다. 안 적으면
    # "불일치 0"이 "볼 게 없다"인지 "걸러냈다"인지 구분되지 않는다.
    excluded = [r for r in (rows or []) if r.get("verdict") == "scope-excluded"]
    tail = ""
    if excluded:
        n_find = sum(r["found"] - r["shown"] for r in excluded)
        tail = (f"\n\n참고: {len(excluded)}개 카드({n_find}건)는 원문보다 적게 보이지만 "
                f"`scope_status` 가 `ok` 가 아니라 **의도대로 제외된 것**이라 불일치로 "
                f"세지 않았습니다: " + ", ".join(f"`{r['id']}`" for r in excluded[:12])
                + ("…" if len(excluded) > 12 else ""))
    if not mismatches:
        return "발행 카드와 원문이 전부 일치합니다(불일치 0)." + tail

    by_verdict: dict[str, list[dict[str, Any]]] = {}
    for r in mismatches:
        by_verdict.setdefault(r["verdict"], []).append(r)
    head = " · ".join(f"{v} {len(rs)}건" for v, rs in sorted(by_verdict.items()))
    lines = [f"발행 카드가 원문보다 적게 보여주는 항목 **{len(mismatches)}건** ({head}):", ""]
    for verdict, rs in sorted(by_verdict.items()):
        lines += [f"### {verdict} — {len(rs)}건", ""]
        lines.append("| 발행일 | 카드 | 카드 표시 | DB(공개/전체) | 원문 | 상태 |")
        lines.append("|---|---|---|---|---|---|")
        for r in rs:
            db = "?" if r.get("db_total") is None else f"{r['db_ok']}/{r['db_total']}"
            lines.append(f"| {r['date']} | `{r['id']}` | {r['shown']} | {db} | "
                         f"{r['found']} | {r['status']} |")
        lines += ["", _VERDICT_HELP.get(verdict, ""), ""]
    return "\n".join(lines).rstrip() + tail


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
    report = format_report(mismatches, rows)
    print()
    print(report)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
