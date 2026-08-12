#!/usr/bin/env python3
"""[검색 유입] 분류·국가·기관별 모음 페이지의 정본 데이터를 다시 만든다.

## 왜 이 파일이 있나

`/findings/` 는 런타임에 RPC 로 결과를 불러오는 검색 앱이라 HTML 에 지적사항 본문이
없다. 그래서 공개 findings 24,797건 전체가 **검색엔진에 색인 대상 0개**다(2026-08-12
실측 — sitemap 등록 URL 이 29개뿐이었던 것의 절반이 이 이유다). 축(분류·국가·기관)마다
정적 페이지를 내면 그 축이 각각 색인 대상이 되고, 각 페이지가 실제 지적 문장을 담는다.

정적 사이트는 커밋된 데이터만 렌더한다(`render.py` 는 네트워크를 타지 않는다 — 결정론이
발행 게이트의 전제다). 그래서 이 스크립트가 **주기적으로 집계를 떠서 커밋 데이터로
남기고**, 렌더러는 그 파일만 읽는다. 자료실(`library_staging`)·용어사전 사례연결
(`glossary_cases_refresh`)과 같은 구조다.

## 권한 — anon 키를 쓴다(service-role 금지)

`findings_search` 는 `anon` 에 execute 가 부여돼 있고 RLS 가 공개 집합을 그대로 정의한다.
service-role 로 세면 **RLS 를 우회해 비공개 행까지 집계**되어, 공개 페이지가 존재하지 않는
건수를 광고하게 된다. 실제로 service-role SQL 은 24,802건, anon 은 24,797건으로 갈렸다 —
**공개 페이지의 진실은 anon 쪽이다.** `glossary_cases_refresh.py` 와 같은 판단.

## 게이트

  1. **0건 가드** — 축 하나라도 항목이 0개면 아무것도 쓰지 않는다(RPC 장애를 "축이 비었다"
     로 커밋하면 다음 렌더가 페이지를 통째로 지운다).
  2. **표본 미달 제외** — `--min-findings`(기본 20) 미만은 페이지를 만들지 않는다. 지적 두세
     건짜리 페이지는 검색엔진에 저품질 대량 페이지로 읽혀 사이트 전체에 손해다.
  3. **제외를 침묵시키지 않는다** — 뺀 항목은 사유와 함께 `excluded` 에 남기고 표준출력에도
     찍는다. 상한을 조용히 걸면 "전부 다뤘다"로 읽힌다.
  4. **모르는 기관 코드 = 실패** — 라벨 맵에 없는 agency 를 만나면 코드로 폴백하지 않고
     멈춘다. 폴백하면 새 규제기관이 편입돼도 아무도 모른 채 영문 코드가 제목에 박힌다.
  5. **항목별 실패 격리** — 개별 축 값의 조회 실패는 그 항목만 빼고 계속하되, 실패가
     20% 를 넘으면 전면 중단한다(부분 갱신 상태를 기준선으로 남기지 않는다).

## 라벨의 출처

  · **분류** — RPC 응답의 `category_label_ko`(DB 정본). 여기서 만들지 않는다.
  · **국가** — `grm_findings._COUNTRY_CODE_MAP`(정본, 이름→코드)을 **역인덱스**해 한국어
    표기를 얻는다. 새 맵을 손으로 적지 않는다 — 그 맵은 마이그레이션 055 의 SQL CASE 와
    파리티가 테스트로 고정돼 있어 이 파일이 사본을 들면 즉시 갈라진다. 한국어 표기가 없는
    코드(IS·MY 등)는 **페이지를 만들지 않는다**(제목이 "IS" 인 페이지는 검색에 무의미하다).
    고치려면 정본 맵과 055 SQL 을 함께 늘려야 하므로 이 스크립트의 범위 밖이다.
  · **기관** — 저장소에 정본 맵이 없어 여기서 정의한다(5개·저드리프트). 대신 **모르는
    코드는 실패**시켜 드리프트를 침묵이 아니라 오류로 만든다(게이트 4).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import requests

from grm_cli import normalize_supabase_url as _normalize_supabase_url
import grm_findings

SCHEMA_VERSION = "grm-findings-facets/v1"
RPC_NAME = "findings_search"
DEFAULT_OUT = Path(__file__).resolve().parent / "web" / "data" / "findings_facets.json"

# 페이지를 만들 최소 지적 건수. 낮추면 저품질 페이지가 늘어 사이트 전체 평가에 손해다.
DEFAULT_MIN_FINDINGS = 20
# 페이지에 싣는 최근 지적 사례 수. 이 문장들이 곧 그 페이지의 색인 대상 본문이다.
DEFAULT_SAMPLES = 6
# 항목 조회 실패 허용 비율 — 넘으면 아무것도 쓰지 않는다.
MAX_FAILURE_RATIO = 0.20

# 기관 코드 → 한국어 표기. 저장소에 정본이 없어 여기가 사실상 정본이다(게이트 4가
# 드리프트를 실패로 만든다). 새 기관이 편입되면 여기에 추가해야 스크립트가 통과한다.
AGENCY_LABELS_KO: dict[str, str] = {
    "FDA": "미국 FDA",
    "HC": "캐나다 보건부",
    "MFDS": "식품의약품안전처",
    "EMA": "유럽 EMA",
    "MHRA": "영국 MHRA",
}

_HANGUL = re.compile(r"[가-힣]")


def country_labels_ko() -> dict[str, str]:
    """ISO2 → 한국어 국가명. `_COUNTRY_CODE_MAP`(이름→코드) 역인덱스에서 파생한다.

    같은 코드에 한국어 키가 여럿이면 먼저 나온 것을 쓴다(정본 dict 는 삽입 순서가 고정된
    리터럴이라 결정론). 한국어 키가 없는 코드는 결과에 없으며, 호출부가 그 코드를 건너뛴다.
    """
    out: dict[str, str] = {}
    for name, code in grm_findings._COUNTRY_CODE_MAP.items():
        if _HANGUL.search(name) and code not in out:
            out[code] = name
    return out


def slugify_code(code: str) -> str:
    """분류 코드(`aseptic_sterility_assurance`) → URL 슬러그(`aseptic-sterility-assurance`).

    입력은 DB 의 `category_code`(소문자·언더스코어)라 ASCII 만 나온다. 그 가정을 깨는 값이
    오면 조용히 뭉개지 말고 실패한다 — URL 이 깨지는 것보다 낫다.
    """
    slug = code.strip().lower().replace("_", "-")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        raise SystemExit(f"슬러그로 쓸 수 없는 코드: {code!r}")
    return slug


# ── PostgREST 호출(anon) ──────────────────────────────────────────────────────
def post_search(base_url: str, anon_key: str, payload: dict[str, Any],
                *, timeout: int = 60) -> dict[str, Any]:
    url = f"{base_url}/rest/v1/rpc/{RPC_NAME}"
    resp = requests.post(
        url,
        headers={
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _sample_view(doc: dict[str, Any], finding: dict[str, Any]) -> dict[str, Any]:
    """지적 1건의 표시용 투영 — 값 무변형(절단 금지).

    본문을 자르지 않는 것은 의도다. 이 문장이 그 페이지의 색인 대상 본문이고, 저장소는
    원문 절단으로 이미 두 번 데였다(deep_analysis·경고서한 조각). 길이 조절은 표시층(CSS)의
    일이지 데이터의 일이 아니다.
    """
    return {
        "finding_id": finding["finding_id"],
        "text_ko": finding.get("finding_text_ko") or "",
        "agency": finding.get("agency") or doc.get("agency") or "",
        "firm_name": finding.get("firm_name") or doc.get("firm_name") or "",
        "published_date": finding.get("published_date") or doc.get("published_date") or "",
        "category_label_ko": finding.get("category_label_ko") or "",
        "evidence_url": finding.get("evidence_url") or doc.get("evidence_url") or "",
        # 이 사례가 속한 문서 — 렌더가 문서 페이지(/findings/doc/{id}/)로 잇는 데 쓴다.
        # 그 문서에 페이지가 없을 수도 있으므로(지적 3건 미만) **링크 여부는 렌더가
        # findings_docs.json 과 대조해 정한다** — 없는 페이지로 보내는 링크는 무링크보다 나쁘다.
        "document_id": doc.get("document_id") or "",
    }


def collect_samples(resp: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """응답 문서들에서 지적을 평탄화해 최근 `limit` 건. 국문 본문이 없는 건은 싣지 않는다.

    RPC 기본 정렬이 `date_desc` 라 "최근 사례"가 되고, 같은 입력이면 같은 결과다(난수 0).
    """
    out: list[dict[str, Any]] = []
    for doc in resp.get("documents", []):
        for finding in doc.get("findings", []):
            if not (finding.get("finding_text_ko") or "").strip():
                continue
            out.append(_sample_view(doc, finding))
            if len(out) >= limit:
                return out
    return out


def build_axis(base_url: str, anon_key: str, *, axis: str, param: str,
               values: list[dict[str, Any]], labels: dict[str, str] | None,
               min_findings: int, samples: int,
               log) -> dict[str, Any]:
    """축 하나(분류/국가/기관)의 항목·제외 목록을 만든다."""
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    failures = 0

    for entry in values:
        key = (entry.get("v") or "").strip()
        n = int(entry.get("findings") or entry.get("c") or 0)

        if not key:
            excluded.append({"key": "", "findings": n, "reason": "국가 미상(원문에 표기 없음)"})
            continue
        if n < min_findings:
            excluded.append({"key": key, "findings": n,
                             "reason": f"표본 미달(<{min_findings})"})
            continue

        if axis == "agency" and key not in AGENCY_LABELS_KO:
            raise SystemExit(
                f"모르는 기관 코드: {key!r} — AGENCY_LABELS_KO 에 한국어 표기를 추가하세요."
                " 코드로 폴백하지 않습니다(새 기관이 조용히 영문 코드로 노출되는 것을 막습니다).")

        label = (labels or {}).get(key, "")
        if labels is not None and not label:
            excluded.append({"key": key, "findings": n,
                             "reason": "한국어 표기 없음(정본 맵 미수록)"})
            continue

        try:
            resp = post_search(base_url, anon_key,
                               {"p_q": "", param: key, "p_page": 1,
                                "p_docs_per_page": max(samples, 10)})
        except Exception as exc:                       # noqa: BLE001 — 항목별 격리
            failures += 1
            log(f"  ! {axis}/{key} 조회 실패: {exc}")
            continue

        totals = resp.get("totals") or {}
        dash = resp.get("dash") or {}
        items.append({
            "key": key,
            "slug": key.lower() if axis != "category" else slugify_code(key),
            "label_ko": label or key,
            "findings": int(totals.get("findings") or 0),
            "documents": int(totals.get("documents") or 0),
            "by_agency": [{"v": a.get("v"), "c": int(a.get("c") or 0)}
                          for a in (dash.get("by_agency") or [])],
            "top_firms": [{"firm_name": f.get("firm_name") or f.get("firm_key") or "",
                           "c": int(f.get("c") or 0)}
                          for f in (dash.get("top_firms") or [])[:5]],
            "samples": collect_samples(resp, samples),
        })

    attempted = len(items) + failures
    if attempted and failures / attempted > MAX_FAILURE_RATIO:
        raise SystemExit(
            f"{axis} 축 조회 실패 {failures}/{attempted} — 허용치 초과. 아무것도 쓰지 않습니다.")
    if not items:
        raise SystemExit(f"{axis} 축 항목 0개 — 0건 가드. 아무것도 쓰지 않습니다.")

    items.sort(key=lambda it: (-it["findings"], it["key"]))
    excluded.sort(key=lambda ex: (-ex["findings"], ex["key"]))
    return {"axis": axis, "items": items, "excluded": excluded}


def build_payload(base_url: str, anon_key: str, *, min_findings: int, samples: int,
                  measured_on: str, log) -> dict[str, Any]:
    root = post_search(base_url, anon_key, {"p_q": "", "p_page": 1, "p_docs_per_page": 1})
    dash = root.get("dash") or {}
    totals = root.get("totals") or {}
    countries = country_labels_ko()

    axes = [
        build_axis(base_url, anon_key, axis="category", param="p_category",
                   values=[{"v": c.get("v"), "c": c.get("c")}
                           for c in (dash.get("by_category") or [])],
                   labels=None, min_findings=min_findings, samples=samples, log=log),
        build_axis(base_url, anon_key, axis="country", param="p_country",
                   values=(dash.get("by_country") or []),
                   labels=countries, min_findings=min_findings, samples=samples, log=log),
        build_axis(base_url, anon_key, axis="agency", param="p_agency",
                   values=[{"v": a.get("v"), "c": a.get("c")}
                           for a in (dash.get("by_agency") or [])],
                   labels=AGENCY_LABELS_KO, min_findings=min_findings,
                   samples=samples, log=log),
    ]
    # 분류 라벨은 DB 정본(category_label_ko)에서 채운다 — 표본에서 읽으므로 사본이 없다.
    for axis in axes:
        if axis["axis"] != "category":
            continue
        for item in axis["items"]:
            for sample in item["samples"]:
                if sample.get("category_label_ko"):
                    item["label_ko"] = sample["category_label_ko"]
                    break

    return {
        "schema_version": SCHEMA_VERSION,
        "measured_on": measured_on,
        "min_findings": min_findings,
        "totals": {"findings": int(totals.get("findings") or 0),
                   "documents": int(totals.get("documents") or 0)},
        # 기관 코드 → 한국어 표기를 데이터에 함께 싣는다. 분류·국가 페이지의 기관 구성
        # 막대가 코드(FDA·HC)만 갖고 있어서인데, 이걸 안 실으면 템플릿이 같은 맵을 또
        # 적게 되고 그 사본은 반드시 갈라진다(게이트 4가 지키는 것은 이 맵 하나뿐이다).
        "agency_labels": dict(AGENCY_LABELS_KO),
        "axes": axes,
    }


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="분류·국가·기관 모음 페이지 데이터 재측정")
    ap.add_argument("--supabase-url", default=os.environ.get("SUPABASE_URL", ""))
    ap.add_argument("--supabase-anon-key", default=os.environ.get("SUPABASE_ANON_KEY", ""))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-findings", type=int, default=DEFAULT_MIN_FINDINGS)
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    ap.add_argument("--measured-on", default="",
                    help="측정일(YYYY-MM-DD). 미지정 시 오늘(UTC 아님·러너 로컬).")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 요약만 출력")
    args = ap.parse_args(argv)

    if not args.supabase_url or not args.supabase_anon_key:
        raise SystemExit("SUPABASE_URL·SUPABASE_ANON_KEY 가 필요합니다(anon 키 — service-role 금지).")

    def log(msg: str) -> None:
        print(msg, flush=True)

    base_url = _normalize_supabase_url(args.supabase_url)
    payload = build_payload(base_url, args.supabase_anon_key,
                            min_findings=args.min_findings, samples=args.samples,
                            measured_on=args.measured_on or date.today().isoformat(),
                            log=log)

    for axis in payload["axes"]:
        log(f"{axis['axis']}: 페이지 {len(axis['items'])}개"
            f" · 제외 {len(axis['excluded'])}개")
        for ex in axis["excluded"]:
            log(f"    - {ex['key'] or '(빈 값)'} {ex['findings']}건 — {ex['reason']}")

    if args.dry_run:
        log("dry-run — 파일을 쓰지 않았습니다.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    log(f"기록: {args.out}")
    return 0


if __name__ == "__main__":                                       # pragma: no cover
    sys.exit(main())
