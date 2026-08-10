#!/usr/bin/env python3
"""probe_source_reachability.py — 러너에서 후보 소스에 닿는지 1회 판정한다.

배경: "GitHub 러너가 이 호스트에 닿는가"는 GRM 에서 반복해 나온 미결 질문이다
  (MFDS 해외 IP 차단 · FDA483 Akamai 봇차단 · 자료실 트랙 canada.ca 미판정).
  로컬에서 되는 것과 러너에서 되는 것이 다르고, 그 차이를 모른 채 수집기를 다 만들면
  **완성한 뒤에야 막힌 걸 안다**. 그래서 착수 전에 이 프로브를 1회 돌린다.

★측정에 쓴 호출 형태가 결론을 정한다(#619/#655 의 교훈).
  그래서 이 프로브는 **수집기가 실제로 쓸 호출 형태 그대로** 쏜다. 그리고 같은 URL 을
  두 번 — 기본 UA 와 브라우저 UA — 쏴서 실패의 **종류**를 가른다:

    | 기본 UA | 브라우저 UA | 판정            | 처방                          |
    |--------|-----------|----------------|-------------------------------|
    | ok     | ok        | OPEN           | 그냥 수집기 만들면 된다        |
    | fail   | ok        | UA_GATED       | 헤더만 붙이면 된다(코드 1줄)   |
    | fail   | fail      | BLOCKED        | egress 프록시 배선 필요(별건)  |

  이 구분이 곧 처방이라, 둘 중 하나만 쏘면 "막혔다"까지만 알고 **무엇을 해야 하는지는
  모르는** 상태가 된다.

★상태코드만 보지 않는다. 200 을 주면서 오류 셸을 돌려준 소스가 이미 있었다
  (nedrug getItem 은 무효 seq 에도 HTTP 200 + ~2.6KB 오류 셸). 그래서 타깃마다
  `expect` 로 본문을 검사하고 그 결과를 판정에 반영한다 — 본문 검사에 실패하면
  HTTP 200 이어도 ok 가 아니다.

★대조군(control)을 함께 쏜다. 이미 매주 브리프에 카드가 나오는 소스(HC 회수 오픈데이터)를
  같이 검사해서, 전부 실패하면 "호스트가 막힌 것"이 아니라 "프로브/네트워크가 고장난 것"임을
  구분한다. 대조군이 죽으면 나머지 판정은 신뢰할 수 없다.

사용:
  python probe_source_reachability.py [--output report.json] [--timeout 30]

종료코드: 항상 0(진단 도구 — 타깃이 막힌 것은 이 스크립트의 실패가 아니다).
  단 대조군이 죽으면 2 로 끝낸다(측정 자체가 무효라는 뜻).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable

import requests

# 브라우저 UA — Akamai/WAF 계열이 기본 python-requests UA 를 막을 때만 통과시키는 값.
# (FDA483 Akamai 차단 조사에서 확인된 계열의 게이트. TLS 위장까지는 하지 않는다 —
#  그건 만들지 않기로 이미 판정된 우회다.)
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Health Canada 실사 DB — 목록 API. 수집기가 쓸 바로 그 쿼리 형태(좁은 창).
_HC_LIST = (
    "https://www.drug-inspections.canada.ca/gmp//controller/searchResult.ashx"
    "?estName=&ref=&site=&rate=&term=&lic=&startDate=2026-01-01&endDate=2026-08-01"
    "&eType=&prov=&licNum=&act=&actCat=&cat=&pType=GMP&lang=en"
)
# Health Canada 실사 DB — 리포트카드(관찰 본문) API. findings 의 실제 재료는 여기 있다.
_HC_CARD = (
    "https://www.drug-inspections.canada.ca/gmp//controller/fullReportCard.ashx"
    "?insNumber=88818&lang=en"
)
# WHOPIR PDF — 인덱스는 이미 매주 브리프에 나오므로 닿는 게 증명됐지만, PDF 본문은
# **한 번도 내려받은 적이 없다**. 인덱스 도달 ≠ 첨부 도달이라 따로 검사한다.
_WHO_PDF = (
    "https://extranet.who.int/prequal/sites/default/files/whopir_files/"
    "I-05101-WHOPIR-ZMC%20Xinchang%20Pharma_0.pdf"
)
# 대조군 — 이미 매주 브리프에 HC 회수 카드가 나오는 소스. 이게 죽으면 측정 자체가 무효다.
_CONTROL_HC_RECALL = (
    "https://recalls-rappels.canada.ca/sites/default/files/"
    "opendata-donneesouvertes/HCRSAMOpenData.json"
)


def _expect_json_rows(body: bytes) -> tuple[bool, str]:
    """HC 목록/리포트카드 — JSON 이고 data 배열에 행이 있어야 ok."""
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 — 진단이라 원인 문자열이 곧 결과다
        return False, f"JSON 파싱 실패: {exc}"
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return False, f"data 배열 없음(keys={sorted(doc)[:6] if isinstance(doc, dict) else type(doc)})"
    if not rows:
        return False, "data 배열이 비었다(쿼리는 통과했으나 행 0 — 차단이 아닌 빈 응답)"
    return True, f"data {len(rows)}행"


def _expect_hc_card(body: bytes) -> tuple[bool, str]:
    """리포트카드는 관찰(data[].regulation/summaryList)이 실려야 의미가 있다.

    ★행 수만 세면 안 된다 — 껍데기 JSON 도 data 배열은 가질 수 있다. 실제로 조항 문자열이
      들어있는지까지 확인해야 "findings 재료가 온다"고 말할 수 있다.
    """
    ok, note = _expect_json_rows(body)
    if not ok:
        return ok, note
    doc = json.loads(body.decode("utf-8", "replace"))
    rows = doc.get("data") or []
    with_reg = [r for r in rows if isinstance(r, dict) and str(r.get("regulation") or "").strip()]
    if not with_reg:
        return False, f"{note} — 그러나 regulation 이 채워진 행 0(관찰 본문 미도달)"
    sample = str(with_reg[0].get("regulation"))[:60]
    return True, f"관찰 {len(with_reg)}행 · 예: {sample}"


def _expect_pdf(body: bytes) -> tuple[bool, str]:
    """PDF 는 매직바이트로 판정한다 — HTML 오류 페이지를 200 으로 주는 경우를 가른다."""
    if body[:4] != b"%PDF":
        head = body[:60].decode("utf-8", "replace").replace("\n", " ")
        return False, f"PDF 아님(head={head!r})"
    if len(body) < 20_000:
        return False, f"PDF 이지만 {len(body)}B — 실사보고서치고 너무 작다(오류/스텁 의심)"
    return True, f"PDF {len(body):,}B"


def _expect_json_any(body: bytes) -> tuple[bool, str]:
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return False, f"JSON 파싱 실패: {exc}"
    n = len(doc) if isinstance(doc, (list, dict)) else 0
    return (n > 0), f"JSON {type(doc).__name__} len={n}"


TARGETS: list[dict[str, Any]] = [
    {
        "name": "control:hc-recall-opendata",
        "url": _CONTROL_HC_RECALL,
        "expect": _expect_json_any,
        "control": True,
        "note": "대조군 — 매주 브리프에 카드가 나오는 소스. 죽으면 측정 무효.",
    },
    {
        "name": "hc-inspections:list",
        "url": _HC_LIST,
        "expect": _expect_json_rows,
        "control": False,
        "note": "HC 실사 목록 API(문서 인벤토리).",
    },
    {
        "name": "hc-inspections:report-card",
        "url": _HC_CARD,
        "expect": _expect_hc_card,
        "control": False,
        "note": "HC 리포트카드 API(관찰 본문 = findings 재료).",
    },
    {
        "name": "whopir:pdf",
        "url": _WHO_PDF,
        "expect": _expect_pdf,
        "control": False,
        "note": "WHOPIR PDF 본문. 인덱스 도달은 이미 증명됐고 첨부는 미검증.",
    },
]


def _one_shot(url: str, ua: str | None, timeout: int,
              expect: Callable[[bytes], tuple[bool, str]]) -> dict[str, Any]:
    headers = {"User-Agent": ua} if ua else {}
    started = time.time()
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — 예외 문자열이 곧 진단이다
        return {
            "ok": False, "status": None, "bytes": 0,
            "elapsed_ms": int((time.time() - started) * 1000),
            "detail": f"요청 실패: {type(exc).__name__}: {exc}",
        }
    body = resp.content or b""
    body_ok, detail = expect(body)
    return {
        "ok": bool(resp.status_code == 200 and body_ok),
        "status": resp.status_code,
        "bytes": len(body),
        "elapsed_ms": int((time.time() - started) * 1000),
        "detail": detail,
    }


def _verdict(default_ok: bool, browser_ok: bool) -> str:
    if default_ok and browser_ok:
        return "OPEN"
    if browser_ok:
        return "UA_GATED"
    if default_ok:
        # 기본은 되는데 브라우저 UA 만 막히는 건 드물다 — 일시 오류일 가능성이 커서 따로 표시.
        return "OPEN_UA_ANOMALY"
    return "BLOCKED"


_PRESCRIPTION = {
    "OPEN": "그대로 수집기 착수 가능.",
    "UA_GATED": "요청에 브라우저 UA 헤더만 붙이면 된다(코드 1줄).",
    "OPEN_UA_ANOMALY": "기본 UA 는 통과 — 브라우저 UA 실패는 일시 오류 의심. 재실행 권장.",
    "BLOCKED": "러너에서 도달 불가 → egress 프록시 배선이 선행되어야 한다(별건 작업).",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="", help="JSON 리포트 저장 경로(비면 미저장)")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    results: list[dict[str, Any]] = []
    for tgt in TARGETS:
        default = _one_shot(tgt["url"], None, args.timeout, tgt["expect"])
        browser = _one_shot(tgt["url"], _BROWSER_UA, args.timeout, tgt["expect"])
        verdict = _verdict(default["ok"], browser["ok"])
        results.append({
            "name": tgt["name"],
            "url": tgt["url"],
            "note": tgt["note"],
            "control": tgt["control"],
            "verdict": verdict,
            "prescription": _PRESCRIPTION[verdict],
            "default_ua": default,
            "browser_ua": browser,
        })

    controls = [r for r in results if r["control"]]
    control_ok = all(r["verdict"] in ("OPEN", "UA_GATED", "OPEN_UA_ANOMALY") for r in controls)
    report = {
        "schema_version": "probe/v1",
        "control_ok": control_ok,
        "results": results,
    }

    print("=" * 78)
    for r in results:
        tag = "[대조군] " if r["control"] else ""
        print(f"{tag}{r['name']}  ->  {r['verdict']}")
        print(f"   기본 UA   : status={r['default_ua']['status']} "
              f"bytes={r['default_ua']['bytes']} :: {r['default_ua']['detail']}")
        print(f"   브라우저UA: status={r['browser_ua']['status']} "
              f"bytes={r['browser_ua']['bytes']} :: {r['browser_ua']['detail']}")
        print(f"   처방      : {r['prescription']}")
        print("-" * 78)
    if not control_ok:
        print("★대조군이 실패했다 — 네트워크/프로브 자체 문제다. 나머지 판정은 신뢰하지 말 것.")
    print("=" * 78)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"리포트 저장: {args.output}")

    return 0 if control_ok else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
