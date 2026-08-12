#!/usr/bin/env python3
"""[검색 유입] 문서 단위 페이지(`/findings/doc/{id}/`)의 정본 데이터를 다시 만든다.

## 왜

`/findings/` 는 런타임 RPC 검색 앱이라 HTML 에 지적 본문이 없다. 분류·국가·기관 모음
페이지(`findings_facets_refresh.py`)가 축 단위 표면을 만들었지만, 그건 축마다 최근 6건만
싣는다 — 나머지 본문은 여전히 어디에도 정적으로 존재하지 않는다. 실사 보고서 한 건을
통째로 담는 문서 페이지가 그 구멍을 메운다.

## 무엇을 페이지로 만드나 — 지적 3건 이상

지적 1~2건짜리 문서는 본문이 얇아 저품질 대량 페이지로 읽힌다(사이트 전체 평가에 손해).
3건 이상이면 실사 보고서로서 최소한의 서술이 성립한다. 뺀 것은 침묵시키지 않고 건수를
표준출력과 화면(축 색인)에 남긴다.

## 이 페이지가 다루는 것은 실명 업체의 규제 기록이다

그래서 문구 규율을 데이터·템플릿 양쪽에 못박는다:

  1. **원문이 권위다** — 모든 페이지가 규제기관 공개 원문(`evidence_url`)으로 직결된다.
     우리가 판단을 덧붙이지 않는다(요약·논평 필드 자체를 만들지 않는다).
  2. **날짜를 앞세운다** — 지적은 시점의 기록이다. 발행일이 없는 문서는 페이지를 만들지
     않는다(언제인지 모르는 지적은 현재 상태로 오독된다).
  3. **후속 조치 가능성을 밝힌다** — 483·실사 지적은 대개 시정 절차가 뒤따른다. 그 사실을
     템플릿 고정 문구로 적는다(데이터에는 없는 사실을 지어내지 않되, 없는 것을 있는 것처럼
     읽히게 두지도 않는다).
  4. **값 무변형** — 본문을 자르지 않는다. 저장소는 원문 절단으로 두 번 데였다.

## 권한

`findings_search` 를 **anon 키**로 부른다 — RLS 가 공개 집합을 정의하므로 anon 이라야
화면에 실제로 보이는 것과 같다. service-role 은 비공개 행까지 끌어와 공개 페이지가
존재하지 않는 기록을 노출하게 된다(= 이 스크립트에서는 사고).

## 게이트

  · 0건 가드 — 문서 0건이면 아무것도 쓰지 않는다(RPC 장애를 "문서가 없다"로 커밋하면
    다음 렌더가 페이지 수천 장을 지운다).
  · 페이지 수집 실패율 20% 초과 시 전면 중단(부분 상태를 기준선으로 남기지 않는다).
  · 발행일·원문 링크·국문 본문이 없는 문서는 제외하고 사유별로 센다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import requests

from grm_cli import normalize_supabase_url as _normalize_supabase_url

SCHEMA_VERSION = "grm-findings-docs/v1"
RPC_NAME = "findings_search"
DEFAULT_OUT = Path(__file__).resolve().parent / "web" / "data" / "findings_docs.json"

DEFAULT_MIN_FINDINGS = 3
DEFAULT_PAGE_SIZE = 100
MAX_FAILURE_RATIO = 0.20

# document_id 가 그대로 URL 경로가 된다 — 안전하지 않은 값은 페이지를 만들지 않는다.
_SLUG_OK = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_DATE_OK = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def post_search(base_url: str, anon_key: str, payload: dict[str, Any],
                *, timeout: int = 120) -> dict[str, Any]:
    resp = requests.post(
        f"{base_url}/rest/v1/rpc/{RPC_NAME}",
        headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}",
                 "Content-Type": "application/json"},
        json=payload, timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def document_view(doc: dict[str, Any], *, min_findings: int,
                  reject: Counter) -> "dict[str, Any] | None":
    """문서 1건의 표시용 투영. 규율을 못 지키면 None(사유를 세어 침묵을 막는다)."""
    doc_id = (doc.get("document_id") or "").strip()
    if not _SLUG_OK.match(doc_id):
        reject["URL 로 쓸 수 없는 문서 id"] += 1
        return None

    published = (doc.get("published_date") or "").strip()
    if not _DATE_OK.match(published):
        # 언제인지 모르는 지적은 현재 상태로 오독된다.
        reject["발행일 없음"] += 1
        return None

    evidence = (doc.get("evidence_url") or "").strip()
    if not evidence.startswith(("http://", "https://")):
        # 원문으로 못 보내는 페이지는 우리 주장만 남는다.
        reject["원문 링크 없음"] += 1
        return None

    findings = []
    for f in doc.get("findings") or []:
        text = (f.get("finding_text_ko") or "").strip()
        if not text:
            continue
        # 화면에 쓰는 것만 싣는다 — 이 파일은 국문 본문 167만 자를 담아 이미 9MB 급이라
        # "혹시 쓸까 봐" 넣는 필드 하나가 수백 KB 다(category_code 는 label 과 중복).
        findings.append({
            "finding_id": f["finding_id"],
            "text_ko": text,                              # 무변형 — 자르지 않는다
            "category_label_ko": f.get("category_label_ko") or "",
        })
    if len(findings) < min_findings:
        reject[f"국문 지적 {min_findings}건 미만"] += 1
        return None

    seen: list[str] = []
    for f in findings:
        label = f["category_label_ko"]
        if label and label not in seen:
            seen.append(label)

    return {
        "document_id": doc_id,
        "slug": doc_id,
        "agency": doc.get("agency") or "",
        "source": doc.get("source") or "",
        "firm_name": doc.get("firm_name") or doc.get("firm_key") or "",
        # 같은 업체의 다른 기록을 잇는 데 쓴다. **표시명이 아니라 정규화 키로 묶는다** —
        # "Intas Pharmaceuticals Limited"와 "Intas Pharmaceuticals Ltd."는 표시명이 다르지만
        # 같은 업체이고, 그 정규화는 이미 FIND-FIRM-ALIAS 가 `firm_key` 로 해 두었다.
        "firm_key": doc.get("firm_key") or "",
        "published_date": published,
        "evidence_url": evidence,
        "categories": seen,
        "findings": findings,
    }


def collect_documents(base_url: str, anon_key: str, *, min_findings: int,
                      page_size: int, log) -> tuple[list[dict[str, Any]], Counter]:
    first = post_search(base_url, anon_key,
                        {"p_q": "", "p_page": 1, "p_docs_per_page": page_size})
    pages = int(first.get("pages") or 1)
    reject: Counter = Counter()
    out: list[dict[str, Any]] = []
    failures = 0

    def absorb(resp: dict[str, Any]) -> None:
        for doc in resp.get("documents") or []:
            view = document_view(doc, min_findings=min_findings, reject=reject)
            if view:
                out.append(view)

    absorb(first)
    for page in range(2, pages + 1):
        try:
            absorb(post_search(base_url, anon_key,
                               {"p_q": "", "p_page": page, "p_docs_per_page": page_size}))
        except Exception as exc:                          # noqa: BLE001 — 페이지별 격리
            failures += 1
            log(f"  ! page {page}/{pages} 실패: {exc}")
        if page % 10 == 0:
            log(f"  · {page}/{pages} 페이지 · 채택 {len(out)}건")

    if pages and failures / pages > MAX_FAILURE_RATIO:
        raise SystemExit(
            f"페이지 수집 실패 {failures}/{pages} — 허용치 초과. 아무것도 쓰지 않습니다.")
    if not out:
        raise SystemExit("문서 0건 — 0건 가드. 아무것도 쓰지 않습니다.")

    # 문서 id 오름차순 = 결정론이자 **작은 diff**. 실사 기록은 한 번 발행되면 바뀌지 않아
    # 주간 재생성의 실제 변경분은 신규분뿐인데, 순서가 흔들리면 전 파일이 diff 로 잡힌다.
    out.sort(key=lambda d: d["document_id"])
    return out, reject


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="문서 단위 페이지 정본 재측정")
    ap.add_argument("--supabase-url", default=os.environ.get("SUPABASE_URL", ""))
    ap.add_argument("--supabase-anon-key", default=os.environ.get("SUPABASE_ANON_KEY", ""))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-findings", type=int, default=DEFAULT_MIN_FINDINGS)
    ap.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    ap.add_argument("--measured-on", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not args.supabase_url or not args.supabase_anon_key:
        raise SystemExit("SUPABASE_URL·SUPABASE_ANON_KEY 가 필요합니다(anon 키 — service-role 금지).")

    def log(msg: str) -> None:
        print(msg, flush=True)

    base_url = _normalize_supabase_url(args.supabase_url)
    docs, reject = collect_documents(base_url, args.supabase_anon_key,
                                     min_findings=args.min_findings,
                                     page_size=args.page_size, log=log)

    by_agency = Counter(d["agency"] for d in docs)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "measured_on": args.measured_on or date.today().isoformat(),
        "min_findings": args.min_findings,
        "totals": {
            "documents": len(docs),
            "findings": sum(len(d["findings"]) for d in docs),
        },
        "by_agency": [{"v": a, "c": c} for a, c in sorted(by_agency.items())],
        # 뺀 것을 침묵시키지 않는다 — 화면(축 색인)도 이 숫자를 그대로 보여준다.
        "excluded": [{"reason": r, "documents": c} for r, c in sorted(reject.items())],
        "documents": docs,
    }

    log(f"문서 {len(docs):,}건 · 지적 {payload['totals']['findings']:,}건")
    for a, c in sorted(by_agency.items()):
        log(f"  {a}: {c:,}건")
    for r, c in sorted(reject.items()):
        log(f"  제외 — {r}: {c:,}건")

    if args.dry_run:
        log("dry-run — 파일을 쓰지 않았습니다.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    log(f"기록: {args.out}")
    return 0


if __name__ == "__main__":                                       # pragma: no cover
    sys.exit(main())
