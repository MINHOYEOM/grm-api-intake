#!/usr/bin/env python3
"""[검색 유입] 문서 단위 페이지(`/findings/doc/{id}/`)의 정본 데이터를 다시 만든다.

## 왜

`/findings/` 는 런타임 RPC 검색 앱이라 HTML 에 지적 본문이 없다. 분류·국가·기관 모음
페이지(`findings_facets_refresh.py`)가 축 단위 표면을 만들었지만, 그건 축마다 최근 6건만
싣는다 — 나머지 본문은 여전히 어디에도 정적으로 존재하지 않는다. 실사 보고서 한 건을
통째로 담는 문서 페이지가 그 구멍을 메운다.

## 무엇을 페이지로 만드나 — 지적 3건 이상(단, 소스를 통째로 지우는 임계는 면제)

지적 1~2건짜리 문서는 본문이 얇아 저품질 대량 페이지로 읽힌다(사이트 전체 평가에 손해).
3건 이상이면 실사 보고서로서 최소한의 서술이 성립한다.

**[2026-08-27] 다만 그 임계가 어떤 소스의 문서를 하나도 남기지 못한다면, 그 임계는 그
소스에 대해 '얇음'이 아니라 '존재'를 재고 있는 것이다.** 실측 문서당 지적 1건 비율:
EMA(EU GMP NCR) 100% · MHRA 100% · MFDS 89% · FDA 28% · HC 16%. EU 비준수 보고서는
지적 1건이 곧 보고서 전체라 얇은 게 아니라 그 문서의 전부이고, 실제로 두 기관이
'문서로 찾기'에서 통째로 사라져 있었다(사용자 피드백: "다른 정보도 있는데 왜 뺐는지").
그래서 **그 소스의 최대 지적 수가 임계 미만이면 면제**한다 — 판정은 손목록이 아니라
수집한 데이터에서 파생하므로 새 소스가 들어와도 자동이다. 임계가 실제로 얇은 문서를
거르는 소스(FDA·HC·MFDS)에서는 종전과 똑같이 동작한다.

뺀 것은 침묵시키지 않고 건수를 표준출력과 화면(축 색인)에 남긴다.

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
import hashlib
import re
import sys
import time
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

# 페이지 한 장을 몇 번까지 쳐 보는가. 실패 원인은 대개 statement timeout 이고 일시적이다
# — 재시도가 없으면 한 번의 실패가 그 페이지의 문서 100건을 영구히 지운다.
PAGE_ATTEMPTS = 4
PAGE_RETRY_BACKOFF_SEC = 2.0
# 호출 사이 간격. RPC 한 장이 약 0.76초인데 anon 역할의 statement_timeout 은 3초라
# 여유가 4배뿐이다 — 62장을 쉼 없이 몰아치는 것이 타임아웃의 직접 원인이었다.
PAGE_PACING_SEC = 0.3

# document_id 가 그대로 URL 경로가 된다 — 안전하지 않은 값은 페이지를 만들지 않는다.
_SLUG_OK = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_slug(doc_id: str) -> "str | None":
    """URL 안전 슬러그. 안전한 id 는 그대로(기존 URL 불변), 아니면 결정론 변환.

    [2026-08-27] 종전에는 안전하지 않은 id 를 통째로 기각했는데, 그 규칙이 MHRA 를
    **전량** 침묵 소실시켰다 — MHRA 문서 id 는 기관 원문 형식이 "Insp GMP/GDP/IMP
    322/14798-0032[I]" 라 공백·슬래시·대괄호가 항상 들어 있다. id 형식은 기관이 정하는
    것이라 우리 기각 규칙이 곧 기관 차별이 된다. 변환 규칙:
      · 안전하지 않은 문자 연쇄 → '-' 하나 (읽을 수 있는 몸통 유지)
      · 원본 id 의 sha1 앞 8자를 접미 (몸통 축약·문자 치환으로 인한 충돌 차단)
    같은 id 는 언제나 같은 슬러그다(재실행 안정). 빈 id 만 None."""
    if not doc_id:
        return None
    if _SLUG_OK.match(doc_id):
        return doc_id
    body = _SLUG_UNSAFE.sub("-", doc_id).strip("-")[:80].rstrip("-")
    tail = hashlib.sha1(doc_id.encode("utf-8")).hexdigest()[:8]
    slug = f"{body}-{tail}" if body else f"doc-{tail}"
    return slug if _SLUG_OK.match(slug) else None


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
    slug = _safe_slug(doc_id)
    if slug is None:
        reject["URL 로 쓸 수 없는 문서 id"] += 1
        return None

    published = (doc.get("published_date") or "").strip()
    if not _DATE_OK.match(published):
        # 언제인지 모르는 지적은 현재 상태로 오독된다.
        reject["발행일 없음"] += 1
        return None

    firm = (doc.get("firm_name") or doc.get("firm_key") or "").strip()
    if not firm or firm.isdigit():
        # 업체명이 비었거나 숫자뿐이면(실측 FDA 2건 — 수집 원천의 결손) 제목이
        # "1021343 — FDA 483 지적사항"이 된다. 누구에 대한 기록인지 말할 수 없는
        # 페이지는 만들지 않는다.
        reject["업체명 없음(숫자뿐이거나 빈 값)"] += 1
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
    if not findings:
        # 국문 본문이 하나도 없으면 페이지를 만들 수 없다(임계와 무관한 절대 조건).
        reject["국문 지적 0건"] += 1
        return None

    seen: list[str] = []
    for f in findings:
        label = f["category_label_ko"]
        if label and label not in seen:
            seen.append(label)

    return {
        "document_id": doc_id,
        "slug": slug,
        "agency": doc.get("agency") or "",
        "source": doc.get("source") or "",
        "firm_name": firm,
        # 같은 업체의 다른 기록을 잇는 데 쓴다. **표시명이 아니라 정규화 키로 묶는다** —
        # "Intas Pharmaceuticals Limited"와 "Intas Pharmaceuticals Ltd."는 표시명이 다르지만
        # 같은 업체이고, 그 정규화는 이미 FIND-FIRM-ALIAS 가 `firm_key` 로 해 두었다.
        "firm_key": doc.get("firm_key") or "",
        "published_date": published,
        # 규제기관이 실사한 날. `published_date`(우리가 문서를 확보한 날)를 **대체하지
        # 않고** 나란히 싣는다 — 둘은 다른 축이라 화면이 어느 쪽인지 밝힐 수 있어야 한다.
        # 없는 소스가 있다: 캐나다 실사는 원천이 날짜를 안 주고, 경고서한은 실사 문서가
        # 아니라 대상에서 뺐다(web/migrations/066 의 범위 주석 참조).
        "inspection_date": (doc.get("inspection_date") or "").strip(),
        "evidence_url": evidence,
        "categories": seen,
        "findings": findings,
    }


def apply_thickness_gate(docs: list[dict[str, Any]], *, min_findings: int,
                         reject: Counter, log=None) -> list[dict[str, Any]]:
    """얇은 문서를 거르되, **임계가 소스를 통째로 지우는 경우는 면제**한다.

    판정은 수집한 데이터에서 파생한다 — 그 소스의 최대 지적 수가 임계 미만이면 임계는
    그 소스에 대해 얇음이 아니라 존재를 재고 있는 것이다(파일 상단 docstring 참조).
    손열거가 아니라 규칙이라 새 소스가 들어와도 자동으로 판정된다.
    """
    peak: dict[str, int] = {}
    for d in docs:
        a = d.get("agency") or ""
        peak[a] = max(peak.get(a, 0), len(d["findings"]))
    exempt = {a for a, mx in peak.items() if mx < min_findings}
    # ★안전장치 — 면제가 **과반**이면 그건 소스의 성질이 아니라 데이터 모양이 바뀐 것이다.
    #   상류가 findings 배열을 잘라 보내면 모든 소스가 얇아져 전량 면제되고, 두께 게이트가
    #   통째로 사라진 채 문서가 배로 불어난 스냅샷이 조용히 커밋된다(축소 게이트는 증가를
    #   못 잡는다). 그 상태를 기준선으로 남기지 않는다.
    if peak and len(exempt) * 2 > len(peak):
        raise SystemExit(
            f"임계 면제가 과반입니다({len(exempt)}/{len(peak)} 소스) — 소스 성질이 아니라 "
            f"상류 데이터 모양이 바뀐 것으로 봅니다. 아무것도 쓰지 않습니다. "
            f"소스별 최대 지적 수: {dict(sorted(peak.items()))}")
    if exempt and log:
        for a in sorted(exempt):
            log(f"  · 임계 면제: {a} — 최대 지적 {peak[a]}건 < {min_findings}"
                f"(임계가 이 소스를 통째로 지운다)")
    kept = []
    for d in docs:
        if len(d["findings"]) >= min_findings or (d.get("agency") or "") in exempt:
            kept.append(d)
        else:
            reject[f"국문 지적 {min_findings}건 미만"] += 1
    return kept


def fetch_page(base_url: str, anon_key: str, page: int, page_size: int, log,
               *, total: "int | None" = None) -> dict[str, Any]:
    """페이지 한 장. 실패하면 물러섰다 다시 친다. 끝내 안 되면 마지막 예외를 올린다.

    ★첫 장도 여기를 지난다. 처음에는 2페이지부터만 재시도를 걸었는데, 바로 그 다음
    실행에서 **첫 장이 500 으로 죽어 런 전체가 중단**됐다 — 실패는 페이지 번호를 가리지
    않는다(부하성이다). 한 곳으로 모아 모든 호출이 같은 보호를 받게 한다.

    실측 근거: `findings_search` 한 장의 소요는 약 0.76초인데 anon 역할의
    statement_timeout 은 **3초**다. 여유가 4배뿐이라, 62장을 쉼 없이 몰아치면 몇 장이
    넘어간다. 그래서 재시도와 함께 호출 사이에 짧게 쉰다.
    """
    where = f"{page}/{total}" if total else str(page)
    payload = {"p_q": "", "p_page": page, "p_docs_per_page": page_size}
    last_exc: "Exception | None" = None
    for attempt in range(1, PAGE_ATTEMPTS + 1):
        try:
            return post_search(base_url, anon_key, payload)
        except Exception as exc:                          # noqa: BLE001 — 재시도 대상
            last_exc = exc
            if attempt < PAGE_ATTEMPTS:
                log(f"  · page {where} 재시도 {attempt}/{PAGE_ATTEMPTS - 1}: {exc}")
                time.sleep(PAGE_RETRY_BACKOFF_SEC * attempt)
    assert last_exc is not None
    raise last_exc


def collect_documents(base_url: str, anon_key: str, *, min_findings: int,
                      page_size: int, log) -> tuple[list[dict[str, Any]], Counter]:
    # 첫 장이 죽으면 pages 를 모르니 런 자체가 불가능하다 — 그래서 여기도 재시도를 거치고,
    # 그래도 안 되면 예외를 그대로 올려 **아무것도 쓰지 않는다**(빈 정본은 페이지 수천
    # 장을 지운다).
    first = fetch_page(base_url, anon_key, 1, page_size, log)
    pages = int(first.get("pages") or 1)
    reject: Counter = Counter()
    out: list[dict[str, Any]] = []
    failures = 0

    def absorb(resp: dict[str, Any]) -> None:
        for doc in resp.get("documents") or []:
            # ★임계는 여기서 걸지 않는다 — 소스 전체의 분포를 봐야 판정할 수 있다.
            view = document_view(doc, min_findings=1, reject=reject)
            if view:
                out.append(view)

    absorb(first)
    for page in range(2, pages + 1):
        # 호출 사이에 짧게 쉰다 — 62장을 쉼 없이 몰아치는 것이 타임아웃의 직접 원인이다
        # (한 장 0.76초 / anon 한계 3초). 전체로는 20초 남짓이라 값싼 보험이다.
        time.sleep(PAGE_PACING_SEC)
        try:
            absorb(fetch_page(base_url, anon_key, page, page_size, log, total=pages))
        except Exception as exc:                          # noqa: BLE001 — 페이지별 격리
            failures += 1
            log(f"  ! page {page}/{pages} 실패({PAGE_ATTEMPTS}회 시도): {exc}")
            # ★실패도 사유로 센다. log 로만 흘리면 그 페이지에 있던 문서 100건이 조용히
            # 사라지고, 축소 게이트(10%)를 넘지 않으면 그대로 자동 머지된다.
            reject[f"페이지 조회 실패(문서 최대 {page_size}건 누락)"] += 1
        if page % 10 == 0:
            log(f"  · {page}/{pages} 페이지 · 채택 {len(out)}건")

    out = apply_thickness_gate(out, min_findings=min_findings, reject=reject, log=log)

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
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — 아래 요약의 em-dash
    # 한 글자가 cp949 에서 UnicodeEncodeError 를 낸다. findings_facets_refresh.py 와 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

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

    # 산출물을 요약 로그보다 **먼저** 쓴다(findings_facets_refresh.py 와 같은 이유).
    # 여기도 수천 건을 페이지네이션으로 긁은 뒤라, 출력 한 줄의 실패가 그 작업을 통째로
    # 버리게 두면 안 된다.
    wrote: "Path | None" = None
    if not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")
        wrote = args.out

    log(f"문서 {len(docs):,}건 · 지적 {payload['totals']['findings']:,}건")
    for a, c in sorted(by_agency.items()):
        log(f"  {a}: {c:,}건")
    for r, c in sorted(reject.items()):
        log(f"  제외 — {r}: {c:,}건")

    if wrote is None:
        log("dry-run — 파일을 쓰지 않았습니다.")
        return 0
    log(f"기록: {wrote}")
    return 0


if __name__ == "__main__":                                       # pragma: no cover
    sys.exit(main())
