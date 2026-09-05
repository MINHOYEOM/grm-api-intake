#!/usr/bin/env python3
"""Google Search Console → Supabase 일별 적재 (077).

Cloudflare RUM 은 "google.com 에서 왔다"까지만 알려준다([[grm-seo-indexable-surface]]
08-12 에 이미 지목된 공백). **무엇을 검색해서 왔는지**는 Search Console 만 안다 —
그게 이 수집기가 채우는 자리다. 검색어를 알면 "사람들이 실제로 찾는 말"로 페이지를
만들 수 있고, 노출은 많은데 클릭이 없는 페이지(제목 문제)와 아예 노출이 없는 페이지
(색인 문제)를 가를 수 있다.

## 인증 — 서비스 계정
사람 OAuth 는 브라우저 동의가 필요해 무인 실행에 맞지 않는다. Search Console 은
**서비스 계정 이메일을 속성 사용자로 추가**하는 경로를 지원하므로 그걸 쓴다.
시크릿 `GSC_SERVICE_ACCOUNT_JSON` = 서비스 계정 키 JSON 전문. 미설정이면 **클린
skip(exit 0)** — RUM 수집기의 토큰 미설정 관례와 동형이다.

## ★속성 주소를 추측하지 않는다
Search Console 속성은 도메인 속성(`sc-domain:grm-solutions.com`)일 수도 URL 접두
속성(`https://grm-solutions.com/`)일 수도 있고, 둘은 **다른 데이터**를 준다. 어느
쪽인지 코드가 단정하면 틀렸을 때 "권한 없음"으로만 보인다. 그래서 `GET /sites` 로
**접근 가능한 속성을 먼저 열거**하고 호스트가 맞는 것을 고른다. 하나도 없으면 열거
결과를 그대로 실패 메시지에 실어 준다 — 그 목록이 "서비스 계정을 속성에 추가했는가"
라는 유일한 질문의 답이다.

## ★★데이터가 늦게 온다 — "어제"가 비어 있는 게 정상이다
GSC 확정(`dataState: final`) 데이터는 보통 **2~3일 지연**된다. 어제 자료가 없다고
"검색 유입 0"으로 읽으면 안 된다. 그래서 창을 넓게(기본 16일) 잡아 매번 다시 적재하고,
보고는 **GSC 자신의 최신 날짜**를 따로 밝힌다(RUM 의 어제와 다른 날짜다).

## ★★★쿼리 합은 총합보다 작다 — 구글이 희귀 검색어를 감춘다
개인 식별을 막으려고 GSC 는 **희소 검색어를 아예 응답에서 제외**한다(익명화). 그래서
검색어 행의 클릭 합은 사이트 총 클릭보다 **항상 작거나 같다**. 이 차이를 "누락"이나
"오류"로 읽으면 안 되고, 반대로 검색어 표만 보고 "이게 전부"라고 읽어도 안 된다.
→ 총합은 `dimensions: ["date"]` 로 **따로** 받아 저장하고(그게 진짜 총합), 화면·보고가
두 수를 나란히 놓는다.

## 저장하지 않는 것
- 페이지는 **경로만**(스킴·호스트 제거, 쿼리스트링 제거) — `rum_path_daily` 와 같은
  규칙이다(`/findings/inspector/?key=실명` 계열 방어).
- 검색어 값은 **로그에 찍지 않는다**(이 저장소는 PUBLIC). 로그에는 행 수만 남긴다.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.parse
from typing import Any

import grm_cli
# 창 삭제 후 재삽입(잔재 행 방지)은 RUM 수집기가 이미 푼 문제다 — 미묘한 정확성
# 함수를 복제하지 않고 그대로 쓴다. (공통 헬퍼의 최종 거처는 grm_cli 지만, 동시에
# 진행 중인 정밀도 작업이 그 파일을 잡고 있어 이관은 그 뒤로 미룬다.)
from collect_rum_analytics import replace_days

API_BASE = "https://www.googleapis.com/webmasters/v3"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
# GSC 응답 1회 상한. 검색어 꼬리가 길어 페이징한다.
ROW_LIMIT = 25000
# 하루에 저장하는 검색어·페이지 상한. 화면·보고는 상위만 쓴다.
QUERY_CAP = 60
PAGE_CAP = 60
# 확정 데이터 지연(보통 2~3일)을 덮고도 남는 기본 창.
DEFAULT_DAYS = 16
KST = _dt.timezone(_dt.timedelta(hours=9))


def kst_today() -> _dt.date:
    return _dt.datetime.now(tz=KST).date()


def default_window(days: int = DEFAULT_DAYS, *, today: "_dt.date | None" = None):
    """(시작, 끝) — GSC 는 날짜만 받는다(시각 없음). 끝은 오늘(아직 안 온 날은 응답에 없다)."""
    end = today or kst_today()
    return (end - _dt.timedelta(days=days)).isoformat(), end.isoformat()


def parse_service_account(raw_json: str) -> "dict[str, Any]":
    """시크릿 문자열 → 검증된 서비스 계정 키(순수 함수).

    ★검증이 google-auth **import 보다 먼저**다. 순서를 뒤집으면 "키를 잘못 넣었다"가
    "라이브러리가 없다"로 보고돼 사람이 엉뚱한 데를 고친다. 잘못된 값에 대한 진단은
    의존성 유무와 무관해야 한다.
    """
    try:
        info = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"GSC_SERVICE_ACCOUNT_JSON 이 JSON 이 아니다: {exc}")
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise SystemExit("GSC_SERVICE_ACCOUNT_JSON 이 서비스 계정 키가 아니다"
                         f"(type={info.get('type') if isinstance(info, dict) else '?'}) — "
                         "Google Cloud 콘솔에서 받은 키 JSON 전문을 넣어라")
    missing = [k for k in ("client_email", "private_key", "token_uri") if not info.get(k)]
    if missing:
        raise SystemExit(f"서비스 계정 키에 필수 항목이 없다: {missing} — 키 JSON 이 잘렸는지 확인하라")
    return info


def access_token(raw_json: str) -> str:
    """서비스 계정 키 JSON → 액세스 토큰."""
    info = parse_service_account(raw_json)
    # 지연 import — 순수 함수만 쓰는 테스트는 google-auth 없이도 돈다.
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
    creds.refresh(Request())
    return creds.token


def _headers(token: str) -> "dict[str, str]":
    return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}


def list_sites(token: str, *, timeout: float = 30.0) -> "list[dict[str, Any]]":
    import requests
    r = requests.get(API_BASE + "/sites", timeout=timeout, headers=_headers(token))
    if r.status_code >= 300:
        raise SystemExit(f"속성 목록 조회 실패 {r.status_code}: {r.text[:400]}")
    return list((r.json() or {}).get("siteEntry") or [])


def pick_site(entries, host: str) -> str:
    """접근 가능한 속성 중 이 사이트의 것을 고른다(순수 함수).

    ★도메인 속성을 먼저 고른다 — URL 접두 속성보다 www·http 변형을 모두 포함해
    데이터가 더 온전하다. 후보가 없으면 **무엇이 보였는지**를 실패 메시지에 담는다.
    """
    h = str(host or "").strip().lower().removeprefix("www.")
    domain, prefix = [], []
    for entry in entries or []:
        site = str((entry or {}).get("siteUrl") or "").strip()
        if not site:
            continue
        if site.lower() == "sc-domain:" + h:
            domain.append(site)
        elif h in site.lower():
            prefix.append(site)
    for bucket in (domain, sorted(prefix)):
        if bucket:
            return bucket[0]
    seen = [str((e or {}).get("siteUrl") or "?") for e in (entries or [])]
    raise SystemExit(
        f"'{h}' 속성에 접근할 수 없다. 서비스 계정이 볼 수 있는 속성: {seen or '(없음)'} — "
        "Search Console → 설정 → 사용자 및 권한 에서 서비스 계정 이메일을 추가했는지 확인하라")


def fetch_rows(token: str, site_url: str, start: str, end: str, dimensions,
               *, row_limit: int = ROW_LIMIT, timeout: float = 60.0):
    """searchAnalytics.query 전체 페이지. dataState=final(확정분만)."""
    import requests
    path = API_BASE + "/sites/" + urllib.parse.quote(site_url, safe="") + "/searchAnalytics/query"
    out: "list[dict[str, Any]]" = []
    start_row = 0
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": list(dimensions),
                "rowLimit": row_limit, "startRow": start_row, "dataState": "final"}
        r = requests.post(path, data=json.dumps(body), timeout=timeout, headers=_headers(token))
        if r.status_code >= 300:
            raise SystemExit(f"searchAnalytics 조회 실패({dimensions}) {r.status_code}: {r.text[:400]}")
        rows = list((r.json() or {}).get("rows") or [])
        out.extend(rows)
        if len(rows) < row_limit:
            return out
        start_row += len(rows)
        if start_row >= 200000:  # 방어 — 무한 페이징 금지
            return out


def clean_page(raw: str) -> str:
    """전체 URL → 경로만. **쿼리스트링·프래그먼트는 버린다**(rum_path_daily 와 같은 규칙)."""
    text = str(raw or "").strip()
    if not text:
        return "/"
    if "://" in text:
        text = text.split("://", 1)[1]
        text = text[text.find("/"):] if "/" in text else "/"
    for sep in ("?", "#"):
        text = text.split(sep, 1)[0]
    if not text:
        return "/"
    return text if text.startswith("/") else "/" + text


def _metrics(row: "dict[str, Any]") -> "dict[str, Any]":
    def num(key, cast):
        try:
            return cast(row.get(key) or 0)
        except (TypeError, ValueError):
            return cast(0)
    return {"clicks": num("clicks", int), "impressions": num("impressions", int),
            "avg_position": round(num("position", float), 2)}


def parse_totals(rows) -> "dict[str, dict[str, Any]]":
    """dimensions=[date] → 날짜별 사이트 총합. **이것이 진짜 총합**(익명화 검색어 포함)."""
    out: "dict[str, dict[str, Any]]" = {}
    for row in rows or []:
        keys = (row or {}).get("keys") or []
        if not keys:
            continue
        day = str(keys[0])
        m = _metrics(row)
        m["snap_date"] = day
        out[day] = m
    return out


def parse_keyed(rows, normalize) -> "dict[tuple, dict[str, Any]]":
    """dimensions=[date, X] → (날짜, X) 별 지표. 정규화로 겹치는 키는 합산한다."""
    out: "dict[tuple, dict[str, Any]]" = {}
    for row in rows or []:
        keys = (row or {}).get("keys") or []
        if len(keys) < 2:
            continue
        day = str(keys[0])
        key = normalize(keys[1])
        if not day or not key:
            continue
        m = _metrics(row)
        prev = out.get((day, key))
        if prev is None:
            out[(day, key)] = m
            continue
        # 정규화로 합쳐진 행 — 순위는 노출 가중 평균이라야 뜻이 맞는다.
        total = prev["impressions"] + m["impressions"]
        if total > 0:
            prev["avg_position"] = round(
                (prev["avg_position"] * prev["impressions"] + m["avg_position"] * m["impressions"]) / total, 2)
        prev["clicks"] += m["clicks"]
        prev["impressions"] = total
    return out


def cap_by_day(keyed, cap: int, key_field: str):
    """날짜별 상위 cap 개(노출 내림차순·동률은 키 이름순 = 결정론)."""
    by_day: "dict[str, list]" = {}
    for (day, key), m in keyed.items():
        by_day.setdefault(day, []).append((key, m))
    out = []
    for day in sorted(by_day):
        top = sorted(by_day[day], key=lambda kv: (-kv[1]["impressions"], kv[0]))[:cap]
        for key, m in top:
            out.append({"snap_date": day, key_field: key, "clicks": m["clicks"],
                        "impressions": m["impressions"], "avg_position": m["avg_position"]})
    return out


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Google Search Console → Supabase 일별 적재")
    ap.add_argument("--site-host", default=os.environ.get("GRM_SITE_HOST", "grm-solutions.com"))
    ap.add_argument("--site-url", default=os.environ.get("GSC_SITE_URL", ""),
                    help="속성 주소를 못박는다(기본: /sites 열거로 자동 선택)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"가져올 일수(기본 {DEFAULT_DAYS}). GSC 확정 데이터는 2~3일 늦게 온다.")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    ap.add_argument("--probe", action="store_true",
                    help="접근 가능한 속성과 응답 구조만 출력(적재 0). 검색어 값은 찍지 않는다.")
    ap.add_argument("--dry-run", action="store_true", help="파싱 결과만 출력(적재 0)")
    args = ap.parse_args(argv)

    raw = (os.environ.get("GSC_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        print("GSC_SERVICE_ACCOUNT_JSON 미설정 — Search Console 수집 건너뜀(클린 skip). "
              "서비스 계정 배선 후 자동으로 돌기 시작한다.")
        return 0

    token = access_token(raw)
    site_url = args.site_url.strip() or pick_site(list_sites(token), args.site_host)
    start, end = default_window(args.days)

    if args.probe:
        # ★구조와 개수만 — 검색어·클릭 수는 공개 로그에 남기지 않는다.
        entries = list_sites(token)
        print("접근 가능한 속성: "
              + ", ".join(f"{e.get('siteUrl')}({e.get('permissionLevel')})" for e in entries))
        print(f"선택: {site_url} · 창 {start}~{end}")
        sample = fetch_rows(token, site_url, start, end, ["date"], row_limit=10)
        print(f"[totals] {len(sample)}행 · row keys: "
              f"{sorted((sample[0] or {}).keys()) if sample else '(응답 0행 — 지연 또는 무유입)'}")
        return 0

    totals = parse_totals(fetch_rows(token, site_url, start, end, ["date"]))
    queries = parse_keyed(fetch_rows(token, site_url, start, end, ["date", "query"]),
                          lambda v: str(v or "").strip())
    pages = parse_keyed(fetch_rows(token, site_url, start, end, ["date", "page"]), clean_page)

    total_rows = [dict(m) for _, m in sorted(totals.items())]
    query_rows = cap_by_day(queries, QUERY_CAP, "query")
    page_rows = cap_by_day(pages, PAGE_CAP, "page_path")

    # ★값은 로그에 남기지 않는다 — 이 저장소는 PUBLIC 이라 Actions 로그가 공개다.
    span = f"{min(totals)}~{max(totals)}" if totals else "(없음)"
    print(f"파싱: {len(totals)}일({span}) · 총합행 {len(total_rows)} · "
          f"검색어행 {len(query_rows)} · 페이지행 {len(page_rows)}")
    if args.dry_run:
        return 0

    creds = grm_cli.resolve_supabase_service_credentials(args)
    if not creds:
        print("SUPABASE_URL/SERVICE_ROLE_KEY 미설정 — 적재 불가", file=sys.stderr)
        return 2
    url, key = creds

    # 답을 받은 날만 손댄다(총합 기준) — GSC 가 아직 안 준 날을 지워 구멍을 내지 않는다.
    answered = sorted(totals)
    written = 0
    for table, rows in (("gsc_daily", total_rows),
                        ("gsc_query_daily", query_rows),
                        ("gsc_page_daily", page_rows)):
        written += replace_days(url, key, table, rows, answered)
    print(f"적재 완료: 총 {written}행 ({len(answered)}일)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
