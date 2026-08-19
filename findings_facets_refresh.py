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

SCHEMA_VERSION = "grm-findings-facets/v2"
RPC_NAME = "findings_search"
DEFAULT_OUT = Path(__file__).resolve().parent / "web" / "data" / "findings_facets.json"

# 페이지를 만들 최소 지적 건수. 낮추면 저품질 페이지가 늘어 사이트 전체 평가에 손해다.
DEFAULT_MIN_FINDINGS = 20
# 페이지에 싣는 최근 지적 사례 수. 이 문장들이 곧 그 페이지의 색인 대상 본문이다.
DEFAULT_SAMPLES = 6
# 항목 조회 실패 허용 비율 — 넘으면 아무것도 쓰지 않는다.
MAX_FAILURE_RATIO = 0.20
# 분류 × 기관 조합이 그 분류에서 차지하는 비율의 상한. 한 기관이 분류를 사실상 독점하면
# 조합 페이지는 부모 분류 페이지의 **복제본**이 된다 — 건수도 거의 같고, 색인 대상 본문인
# "최근 사례" 6건이 통째로 겹친다(2026-08-19 실측: FDA × 공정밸리데이션 = 570/578 = 98.6%,
# 사례 6/6 동일). 그런 페이지를 따로 내면 같은 질의에 두 페이지가 경쟁해 검색엔진이 하나를
# 버리고, 중복으로 판정되면 둘 다 손해다. 그 분류는 부모 페이지가 이미 답이다.
MAX_COMBO_SHARE = 0.95

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


# ── "지적이 없었다"는 선언은 지적 사례가 아니다 ──────────────────────────────────
# 캐나다 보건부 실사보고서 중 관찰이 하나도 없는 건은, 그 사실 자체가 한 건의 finding
# 행으로 적재된다(실측 138건·138문서, 문서당 정확히 1건). 코퍼스에 남는 것은 옳다 —
# "관찰 없음"도 실사 결과다. 다만 **"최근 지적 사례" 칸에 그것이 뜨면 제목과 정면으로
# 모순**된다(실제로 캐나다·HC 축 대표 사례가 "지적사항이 기록되지 않았다."였다).
#
# ★문구 전체가 그 선언일 때만 걸러낸다(부분일치 금지) — "…일탈이 기록되지 않았다" 처럼
#   진짜 지적 안에 같은 표현이 들어 있는 문장을 함께 떨어뜨리면 안 된다.
# ★건수(totals)에서는 빼지 않는다. 표시에서만 제외하고, 몇 건을 걸렀는지 보고한다 —
#   조용히 지우면 다음 사람이 "왜 6건이 아니라 5건이지"를 데이터에서 알 수 없다.
_ABSENCE_DECLARATION = re.compile(
    r"^\s*(기록된|기재된)?\s*지적사항이\s*(기록되지\s*않았|없었|없)다\.?\s*$")


def is_absence_declaration(text: str) -> bool:
    """이 문장이 '지적이 없었다'는 선언 자체인가(= 지적 사례가 아님)."""
    return bool(_ABSENCE_DECLARATION.match(text or ""))


def collect_samples(resp: dict[str, Any], limit: int,
                    skipped: "list[str] | None" = None) -> list[dict[str, Any]]:
    """응답 문서들에서 지적을 평탄화해 최근 `limit` 건.

    국문 본문이 없는 건과 **'지적 없음' 선언**은 싣지 않는다. RPC 기본 정렬이 `date_desc`
    라 "최근 사례"가 되고, 같은 입력이면 같은 결과다(난수 0).
    """
    out: list[dict[str, Any]] = []
    for doc in resp.get("documents", []):
        for finding in doc.get("findings", []):
            text = (finding.get("finding_text_ko") or "").strip()
            if not text:
                continue
            if is_absence_declaration(text):
                if skipped is not None:
                    skipped.append(finding["finding_id"])
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
    skipped_absence: list[str] = []
    failures = 0

    for entry in values:
        key = (entry.get("v") or "").strip()
        n = int(entry.get("findings") or entry.get("c") or 0)

        if not key:
            excluded.append({"key": "", "findings": n, "reason": "국가 미상(원문에 표기 없음)"})
            continue

        # ★라벨 게이트를 **표본 미달보다 먼저** 본다. 순서가 반대면 새 규제기관이 편입돼도
        # 초기 건수가 적은 동안에는 "표본 미달"로 조용히 제외되고, 건수가 임계값을 넘는
        # 순간에야 처음 터진다 — 그때는 이미 그 기관이 여러 주 동안 축에서 빠져 있었다.
        # 게이트의 목적은 "모르는 기관이 있다"를 즉시 알리는 것이지 노출을 막는 게 아니다.
        if axis == "agency" and key not in AGENCY_LABELS_KO:
            raise SystemExit(
                f"모르는 기관 코드: {key!r} — AGENCY_LABELS_KO 에 한국어 표기를 추가하세요."
                " 코드로 폴백하지 않습니다(새 기관이 조용히 영문 코드로 노출되는 것을 막습니다).")

        if n < min_findings:
            excluded.append({"key": key, "findings": n,
                             "reason": f"표본 미달(<{min_findings})"})
            continue

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
            # ★실패도 excluded 에 남긴다. log 로만 흘리면 "왜 이 축이 사라졌나"가
            # 화면에도 PR 본문에도 안 남아, 표본 미달로 빠진 것과 구분되지 않는다.
            excluded.append({"key": key, "findings": n, "reason": "조회 실패"})
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
            "samples": collect_samples(resp, samples, skipped_absence),
        })

    attempted = len(items) + failures
    if attempted and failures / attempted > MAX_FAILURE_RATIO:
        raise SystemExit(
            f"{axis} 축 조회 실패 {failures}/{attempted} — 허용치 초과. 아무것도 쓰지 않습니다.")
    if not items:
        raise SystemExit(f"{axis} 축 항목 0개 — 0건 가드. 아무것도 쓰지 않습니다.")

    items.sort(key=lambda it: (-it["findings"], it["key"]))
    excluded.sort(key=lambda ex: (-ex["findings"], ex["key"]))
    # 표시에서 뺀 "지적 없음" 선언 수 — 조용히 지우지 않는다(건수 자체는 그대로다).
    return {"axis": axis, "items": items, "excluded": excluded,
            "samples_skipped_absence": len(set(skipped_absence))}


# ── [검색 유입 2차] 분류 × 기관 조합 ─────────────────────────────────────────────
# 단일 축 페이지가 검색에서 실제로 이겼다(2026-08-19 실측: 네이버 "무균공정 밸리데이션
# 지적" → /findings/c/process-validation/ 웹문서 1위). 그런데 사람들이 치는 말은 대개
# 주제 하나가 아니라 **기관 + 주제**다("FDA 무균 지적사항", "식약처 회수 사례").
# 단일 축 60장으로는 그 조합을 받을 표면이 없다.
#
# ★조합 후보는 새로 조회하지 않는다 — 분류 축이 이미 갖고 있는 `by_agency` 건수가 곧
#   조합 건수다. 여기서 한 번 더 세면 같은 수를 두 곳에서 재게 되고, 그 둘은 반드시
#   갈라진다. 후보 선별은 그 값으로 하고, RPC 는 **페이지를 실제로 만들 조합에만** 쏜다.
# ★임계값은 단일 축과 같은 `min_findings` 를 쓴다. 조합만 낮추면 얇은 페이지가 수십 장
#   생겨 사이트 전체 평가를 깎는다(단일 축에서 이미 내린 판단을 뒤집지 않는다).
def build_category_agency_combos(base_url: str, anon_key: str, *,
                                 category_axis: dict[str, Any],
                                 min_findings: int, samples: int,
                                 log) -> dict[str, Any]:
    """분류 × 기관 조합 페이지 데이터. 후보는 분류 축의 by_agency 에서 파생한다."""
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    skipped_absence: list[str] = []
    failures = 0

    for cat in category_axis.get("items") or []:
        for entry in cat.get("by_agency") or []:
            agency = (entry.get("v") or "").strip()
            n = int(entry.get("c") or 0)
            label_key = f"{cat['slug']}/{agency or '(빈 값)'}"

            if not agency:
                excluded.append({"key": label_key, "findings": n,
                                 "reason": "기관 미상(원문에 표기 없음)"})
                continue

            # 라벨 게이트를 표본 미달보다 먼저 — build_axis 와 같은 순서다(새 기관이
            # 건수가 적은 동안 "표본 미달"로 조용히 숨는 것을 막는다).
            if agency not in AGENCY_LABELS_KO:
                raise SystemExit(
                    f"모르는 기관 코드: {agency!r} — AGENCY_LABELS_KO 에 한국어 표기를"
                    " 추가하세요(조합 축).")

            if n < min_findings:
                excluded.append({"key": label_key, "findings": n,
                                 "reason": f"표본 미달(<{min_findings})"})
                continue

            try:
                resp = post_search(base_url, anon_key,
                                   {"p_q": "", "p_category": cat["key"],
                                    "p_agency": agency, "p_page": 1,
                                    "p_docs_per_page": max(samples, 10)})
            except Exception as exc:                   # noqa: BLE001 — 항목별 격리
                failures += 1
                log(f"  ! combo/{label_key} 조회 실패: {exc}")
                excluded.append({"key": label_key, "findings": n, "reason": "조회 실패"})
                continue

            totals = resp.get("totals") or {}
            dash = resp.get("dash") or {}
            got = int(totals.get("findings") or 0)
            # 실측이 임계값 아래로 내려오면 만들지 않는다. by_agency 는 분류 축을 뜬
            # 시점의 수라, 그 사이 재분류로 줄어든 조합이 있을 수 있다.
            if got < min_findings:
                excluded.append({"key": label_key, "findings": got,
                                 "reason": f"실측 표본 미달(<{min_findings})"})
                continue

            # 부모 분류를 사실상 독점하는 조합은 만들지 않는다(MAX_COMBO_SHARE 주석 참조).
            # 판정은 실측값끼리 비교한다 — by_agency 는 분류 축을 뜬 시점의 수라 분모와
            # 분자의 시점이 어긋난다.
            parent = int(cat.get("findings") or 0)
            if parent and got / parent >= MAX_COMBO_SHARE:
                excluded.append({
                    "key": label_key, "findings": got,
                    "reason": (f"분류 독점({got}/{parent}="
                               f"{got / parent * 100:.1f}%) — 부모 페이지의 복제본")})
                continue

            items.append({
                "key": f"{cat['key']}|{agency}",
                "category_key": cat["key"],
                "category_slug": cat["slug"],
                "category_label_ko": cat["label_ko"],
                "agency_key": agency,
                "agency_label_ko": AGENCY_LABELS_KO[agency],
                "slug": agency.lower(),
                "findings": got,
                "documents": int(totals.get("documents") or 0),
                "top_firms": [{"firm_name": f.get("firm_name") or f.get("firm_key") or "",
                               "c": int(f.get("c") or 0)}
                              for f in (dash.get("top_firms") or [])[:5]],
                "samples": collect_samples(resp, samples, skipped_absence),
            })

    attempted = len(items) + failures
    if attempted and failures / attempted > MAX_FAILURE_RATIO:
        raise SystemExit(
            f"조합 축 조회 실패 {failures}/{attempted} — 허용치 초과. 아무것도 쓰지 않습니다.")
    if not items:
        raise SystemExit("조합 축 항목 0개 — 0건 가드. 아무것도 쓰지 않습니다.")

    items.sort(key=lambda it: (-it["findings"], it["key"]))
    excluded.sort(key=lambda ex: (-ex["findings"], ex["key"]))
    return {"axis": "category_agency", "items": items, "excluded": excluded,
            "samples_skipped_absence": len(set(skipped_absence))}


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

    # 조합은 분류 축의 **확정된 라벨**을 물려받아야 한다 — 위 루프가 label_ko 를 DB
    # 정본으로 덮어쓴 뒤에 만들어야 조합 제목이 코드가 아닌 한국어로 나온다.
    category_axis = next(a for a in axes if a["axis"] == "category")
    combos = build_category_agency_combos(
        base_url, anon_key, category_axis=category_axis,
        min_findings=min_findings, samples=samples, log=log)

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
        # 조합은 `axes` 와 나란히 두지 않고 별도 키로 낸다 — 렌더의 축 루프는
        # `FACET_AXES[axis_key]` 로 메타를 찾고 모르는 축이면 KeyError 로 죽는다(조용한
        # 누락 금지). 조합은 URL 구조가 2단(분류 밑 기관)이라 그 메타 표에 들어갈 수
        # 없으므로, 같은 목록에 섞으면 그 가드를 억지로 느슨하게 만들어야 한다.
        "combos": combos,
    }


def main(argv: "list[str] | None" = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다. 아래 요약 로그에는
    # em-dash 가 있는데 cp949 는 그 글자를 못 찍는다 — 한글·`·`·`→`·`★` 는 되고
    # `—`·`•`·`✓` 는 안 된다. ubuntu CI 는 UTF-8 이라 초록이어서 아무도 몰랐다.
    # brief_lint.py·deep_analysis_fanout.py·probe_*.py 와 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

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

    # 산출물을 요약 로그보다 **먼저** 쓴다. payload 는 RPC 90여 회(수 분)의 결과인데
    # 종전에는 요약 출력이 먼저였다 — 출력 한 줄이 실패하면 그 수 분이 통째로 버려졌다
    # (2026-08-19 실측: cp949 stdout 에서 EXIT=1, findings_facets.json 미갱신). 위 인코딩
    # 가드와 이중 방어다 — 인코딩 아닌 이유로 요약이 죽어도 데이터는 남는다.
    wrote: "Path | None" = None
    if not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        wrote = args.out

    # 조합 축도 함께 요약한다 — 단일 축만 찍으면 조합 51장의 제외 사유가 어디에도 안 남는다.
    for axis in [*payload["axes"], payload["combos"]]:
        log(f"{axis['axis']}: 페이지 {len(axis['items'])}개"
            f" · 제외 {len(axis['excluded'])}개")
        for ex in axis["excluded"]:
            log(f"    - {ex['key'] or '(빈 값)'} {ex['findings']}건 — {ex['reason']}")

    if wrote is None:
        log("dry-run — 파일을 쓰지 않았습니다.")
        return 0
    log(f"기록: {wrote}")
    return 0


if __name__ == "__main__":                                       # pragma: no cover
    sys.exit(main())
