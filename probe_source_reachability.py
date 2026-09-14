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
import re
import sys
import time
from typing import Any, Callable

from grm_common import kr_egress_get

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


# ── [2026-09-14] 무음 엔드포인트 그룹 — "오류는 안 났는데 계속 0건" 을 러너에서 가른다 ──
# 2026-09-14 Notion Intake 실측에서 12개 엔드포인트가 10~60일 신규 0건이었다(§0 표). 한국 IP
# 에서는 전부 200 + 항목 있음이 확인됐지만, 러너에서는 다를 수 있다(UA·봇차단·IP 거부).
# 이 그룹은 그 12개를 **수집기가 쓰는 URL 그대로** 쏘고, 피드는 항목 수와 최신 날짜를,
# 페이지는 파서가 의지하는 표식을 검사한다. `--targets silent` 로 고른다.
#
# ★프록시는 수집기와 **같은 규칙**으로 붙는다(`kr_egress_get` → `proxies_for`; 홉 실패 시
#   직결 1회 폴백). grm-source-probe 워크플로는 secret 이 없으므로 러너에서는 직결이고,
#   그래서 MFDS 3종의 결과는 곧 "오늘 러너 직결이 열려 있나"의 답이다. 프록시 자체의 도달
#   여부는 같은 워크플로의 `kr_egress` 입력(probe_mfds_egress.py)이 따로 잰다.
#   (MFDS 호스트를 requests 로 직접 치는 모듈은 `test_mfds_egress_wiring` 가드가 막는다 —
#    프로브도 예외가 아니다: 로컬에서 MFDS_HTTP_PROXY 를 두고 돌리면 수집기와 같은 경로를 탄다.)
_FEED_DATE_RES = (
    re.compile(r"<pubDate>([^<]+)</pubDate>"),
    re.compile(r"<updated>([^<]+)</updated>"),
    re.compile(r"<published>([^<]+)</published>"),
    re.compile(r"<dc:date>([^<]+)</dc:date>"),
)


def _expect_feed_items(body: bytes) -> tuple[bool, str]:
    """RSS/Atom — <item>/<entry> 가 1개 이상이어야 ok. 최신 날짜를 함께 적는다.

    ★상태코드·바이트 수만 보면 안 된다: 200 + 빈 채널이 정확히 "무음" 의 한 모습이다.
    """
    text = body.decode("utf-8", "replace")
    n = len(re.findall(r"<item[\s>]", text)) + len(re.findall(r"<entry[\s>]", text))
    dates: list[str] = []
    for rx in _FEED_DATE_RES:
        dates.extend(d.strip() for d in rx.findall(text))
    newest = dates[0] if dates else "(날짜 없음)"
    if n == 0:
        return False, "피드에 항목 0건(200 이어도 무음 — 빈 채널 또는 스키마 변경)"
    return True, f"항목 {n}건 · 최신 {newest}"


def _expect_markers(*patterns: str, min_hits: int = 1,
                    note: str = "") -> Callable[[bytes], tuple[bool, str]]:
    """HTML/XML 페이지 — 파서가 의지하는 표식(정규식)이 전부 min_hits 회 이상이어야 ok."""
    compiled = [re.compile(p, re.I) for p in patterns]

    def _check(body: bytes) -> tuple[bool, str]:
        text = body.decode("utf-8", "replace")
        hits = {p.pattern: len(p.findall(text)) for p in compiled}
        missing = [p for p, c in hits.items() if c < min_hits]
        summary = " · ".join(f"{p}×{c}" for p, c in hits.items())
        if missing:
            return False, f"표식 부족({summary}){' — ' + note if note else ''}"
        return True, f"표식 확인({summary})"

    return _check


_MFDS_RSS = "https://www.mfds.go.kr/www/rss/brd.do?brdId={board}"
SILENT_TARGETS: list[dict[str, Any]] = [
    {
        "name": "mfds-rss:data0013",
        "url": _MFDS_RSS.format(board="data0013"),
        "expect": _expect_feed_items, "control": False,
        "note": "MFDS 자료실 RSS(46일 무음). 프록시 없이 = 러너 직결 판정.",
    },
    {
        "name": "mfds-rss:data0011",
        "url": _MFDS_RSS.format(board="data0011"),
        "expect": _expect_feed_items, "control": False,
        "note": "MFDS 자료실 RSS(16일 무음). 프록시 없이 = 러너 직결 판정.",
    },
    {
        "name": "mfds-rss:data0010",
        "url": _MFDS_RSS.format(board="data0010"),
        "expect": _expect_feed_items, "control": False,
        "note": "MFDS 자료실 RSS(14일 무음). 프록시 없이 = 러너 직결 판정.",
    },
    {
        "name": "pics:rss",
        "url": "https://picscheme.org/rss/general_en.rss",
        "expect": _expect_feed_items, "control": False,
        "note": "PIC/S RSS(46일 무음) — 최신 pubDate 가 곧 답이다.",
    },
    {
        "name": "mhra-alert:atom",
        "url": "https://www.gov.uk/drug-device-alerts.atom",
        "expect": _expect_feed_items, "control": False,
        "note": "MHRA drug-device alerts(25일 무음) — 의약품 회수만 채택하므로 기기 FSN 만 있으면 0건이 정상.",
    },
    {
        "name": "mhra-gmp-ncr:search",
        "url": "https://cms.mhra.gov.uk/mhra/gmp?f%5B0%5D=gmp_compliance%3ANon%20Compliant",
        "expect": _expect_markers(r"Non Compliant", r"/mhra/gmp/", r"<time[^>]*datetime=",
                                  note="Drupal 목록 구조 변경 의심"),
        "control": False,
        "note": "MHRA GMP 비준수 목록(35일 무음).",
    },
    {
        "name": "eu-gmp-ncr:search",
        "url": "https://eudragmdp.ema.europa.eu/inspections/gmpc/searchGMPNonCompliance.do",
        "expect": _expect_markers(r"searchGMPNCResultControlList", r"Non[- ]?Compliance",
                                  note="EudraGMDP 검색 폼 구조 변경 의심"),
        "control": False,
        "note": "EudraGMDP 비준수 검색(20일 무음).",
    },
    {
        "name": "who:rss",
        "url": "https://extranet.who.int/prequal/rss.xml",
        "expect": _expect_feed_items, "control": False,
        "note": "WHO PQ 뉴스 RSS(16일 무음).",
    },
    {
        "name": "who:whopir-list",
        "url": ("https://extranet.who.int/prequal/inspection-services/"
                "who-public-inspection-reports-whopirs-medicines"),
        "expect": _expect_markers(r"WHOPIR", r"href=\"[^\"]*whopir[^\"]*\"", min_hits=3,
                                  note="WHOPIR 목록 렌더 변경 의심"),
        "control": False,
        "note": "WHOPIR 목록(12일 무음).",
    },
    {
        "name": "mfds-admin:data-go-kr-gateway",
        "url": ("https://apis.data.go.kr/1471000/MdcinExaathrService04/"
                "getMdcinExaathrList04?pageNo=1&numOfRows=1"),
        "expect": _expect_markers(r"OpenAPI_ServiceResponse|returnAuthMsg|SERVICE_KEY|resultCode",
                                  note="게이트웨이 응답 형식 변경 의심"),
        "ok_statuses": (200, 401),   # 키 없이 쏘므로 401 이 정상 — 게이트웨이 도달만 본다
        "control": False,
        "note": "MFDS 행정처분 API 게이트웨이(11일 무음). serviceKey 없이 도달만 판정(401=도달).",
    },
    {
        "name": "mfds-gmp-inspection:nedrug-list",
        "url": "https://nedrug.mfds.go.kr/pbp/CCBBD03/getList?page=1&limit=10",
        "expect": _expect_markers(r"<table", r"GMP", note="nedrug 목록 렌더 변경 의심"),
        "control": False,
        "note": "nedrug GMP 실태조사 목록(10일 무음). 프록시 없이 = 러너 직결 판정.",
    },
    {
        "name": "ich:quality-guidelines",
        "url": "https://admin.ich.org/page/quality-guidelines",
        "expect": _expect_markers(r"\b[QM]\d{1,2}[A-Z]?\b", min_hits=5,
                                  note="ICH 페이지 섹션 제목 구조 변경 의심"),
        "control": False,
        "note": "ICH 품질 가이드라인 페이지(60일+ 0건 — 스냅샷 diff 라 변동 없으면 0건이 정상).",
    },
]


def _one_shot(url: str, ua: str | None, timeout: int,
              expect: Callable[[bytes], tuple[bool, str]],
              ok_statuses: tuple[int, ...] = (200,)) -> dict[str, Any]:
    headers = {"User-Agent": ua} if ua else {}
    started = time.time()
    try:
        resp = kr_egress_get(url, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — 예외 문자열이 곧 진단이다
        return {
            "ok": False, "status": None, "bytes": 0,
            "elapsed_ms": int((time.time() - started) * 1000),
            "detail": f"요청 실패: {type(exc).__name__}: {exc}",
        }
    body = resp.content or b""
    body_ok, detail = expect(body)
    return {
        "ok": bool(resp.status_code in ok_statuses and body_ok),
        "status": resp.status_code,
        "bytes": len(body),
        "elapsed_ms": int((time.time() - started) * 1000),
        "detail": detail,
    }


# 그룹마다 대조군을 반드시 포함한다 — 대조군 없는 판정은 "막혔다"와 "프로브가 고장났다"를
# 못 가른다. 무음 그룹의 대조군(HC 회수 오픈데이터)은 §0 표의 13일 무음 소스이기도 하다.
TARGET_GROUPS: dict[str, list[dict[str, Any]]] = {
    "candidates": TARGETS,
    "silent": [t for t in TARGETS if t["control"]] + SILENT_TARGETS,
    "all": TARGETS + SILENT_TARGETS,
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
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="", help="JSON 리포트 저장 경로(비면 미저장)")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--targets", choices=sorted(TARGET_GROUPS), default="candidates",
                    help="candidates=착수 전 후보(기본) · silent=2026-09-14 무음 12종 · all=둘 다")
    args = ap.parse_args(argv)

    results: list[dict[str, Any]] = []
    for tgt in TARGET_GROUPS[args.targets]:
        ok_statuses = tuple(tgt.get("ok_statuses", (200,)))
        default = _one_shot(tgt["url"], None, args.timeout, tgt["expect"], ok_statuses)
        browser = _one_shot(tgt["url"], _BROWSER_UA, args.timeout, tgt["expect"], ok_statuses)
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
        "targets": args.targets,
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
