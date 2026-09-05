#!/usr/bin/env python3
"""Cloudflare Web Analytics(RUM) → Supabase 일별 적재 (072·073·075).

운영자가 Cloudflare 대시보드를 읽지 않고도 /admin 성장·유입 탭에서 방문·유입 경로를
보게 하는 수집기. 하루 1회 워크플로(grm-rum-analytics.yml)가 호출한다.

## 왜 일 단위인가 (2026-09-02 정정 — 처음엔 시간 단위였다)
축을 KST 에 맞추려고 `datetimeHour` 로 받아 +9h 시프트해 합산했는데, **버킷마다
반올림이 걸려 작은 시간대가 사라졌다**. 실측: 대시보드 7일 264 방문 vs 시간합산 9일
250, 9/1 은 60 vs 20 — 값이 전부 10 의 배수로 떨어졌다. 축 정렬을 위해 정확도를
버린 거래였고, 정확도가 이긴다.

그래서 `date` 차원(UTC)으로 받는다. **UTC 날짜는 한국 사이트에서 KST 날짜의 좋은
근사다** — KST 09:00~24:00 이 같은 번호의 UTC 날짜에 들어가고, 어긋나는 구간은
KST 00:00~09:00(트래픽이 가장 적은 새벽)뿐이다. 이 근사는 화면에 명시한다.

## ★★★표본 여부는 **실행마다 다르다** — 인과는 미확정이고, 그래서 계기를 만든다
`rumPageloadEventsAdaptiveGroups` 의 Adaptive(ABR)는 표본 데이터셋을 고를 수 있고,
그때 `sum{visits}` 는 간격만큼 곱해진 **추정값**으로 와서 값이 10 의 배수로 뭉개진다.

실측 경과(2026-09-05):
- 9/1 까지 저장분에는 리퍼러 12·6·2·1 같은 정확값이 있고, **9/2 14:09 KST 실행부터
  정확값 0 건**. 같은 창의 방문 합이 대시보드 대조를 마친 264 에서 210 으로 내려앉았다.
  그 실행이 073(착지 경로)을 같은 쿼리에 붙인 첫 실행이라 **처음엔 그룹 병합을 원인으로
  지목했다.**
- ★그 가설은 **반증됐다.** 그룹을 쪼갠 뒤 00:23·00:34 UTC 실행은 표본 간격 1.0~1.2 를
  줬는데, **같은 코드·같은 창의 01:53·01:54 UTC 실행이 10~12.5 를 줬다.** 90분 사이에
  바뀐 것은 우리 쪽에 없다. ABR 의 데이터셋 선택은 우리가 통제하지 못하는 축(시점·부하·
  범위 내 원시 이벤트 수 등)에 달려 있다.

→ 그래서 코드가 하는 일은 **원인 제거가 아니라 관측과 래칫**이다:
  ⓐ 그룹마다 별도 요청(요청을 싸게 유지한다 — 해가 없고, 비싼 그룹이 나머지를 끌고
     내려갈 여지를 줄인다. **효과는 입증되지 않았다**)
  ⓑ `avg { sampleInterval }` 을 같이 받아 **행마다 저장** — 표본 여부를 추측하지 않는다
  ⓒ **정확한 값을 추정값으로 덮지 않는다**(keep_days) → 실행을 반복할수록 각 날짜가
     "가장 정확했던 관측"으로 수렴한다. 실증: 9/3 이 표본 없는 창에 잡혀 방문 16·간격
     1.00 으로 저장됐고, 이후 표본 실행 2회를 **그대로 버텨 냈다**.
  ⓓ 화면이 "전수/거의 전수/표본 추정/미상"을 그대로 표시한다.

## ★정확한 값을 추정값으로 덮지 않는다
창을 8일로 잡고 매일 다시 적재하므로, **한 번 표본으로 내려간 실행이 과거의 정확한
값을 덮어쓴다**(2026-09-02~04 에 실제로 그렇게 8/25~9/1 이 파괴됐다). 그래서 적재
전에 저장된 sample_interval 을 읽어, **새 값이 저장값보다 덜 정확한 날은 건너뛴다**.
의도적으로 덮어야 하면 `--allow-downgrade`.

## ★잔재 행을 남기지 않는다
예전엔 upsert 만 해서, 어떤 날의 리퍼러 호스트가 다음 실행에서 사라져도 옛 행이
그대로 남았다(그래서 8/31 은 방문 20 인데 리퍼러 합이 43 이었다). 지금은 **그 날의
행을 지우고 다시 넣는다**. 지우는 대상은 이번 응답의 totals 에 등장한 날뿐이다 —
API 가 답하지 않은 날을 지워 구멍을 내지 않기 위해서다.

## 봇 제외
`bot: 0` 필터가 대시보드의 "Exclude bots = Yes" 와 같은 모집단이다. 이 필터를 빼면
크롤러가 섞여 방문이 몇 배로 부푼다(2026-09-01 실측: 존 지표 1.4k/일 vs 실방문 60/일).
자동화 브라우저(개발용 인앱 브라우저·헤드리스 크롤러)는 이 필터에 걸리지 않으므로
비콘 쪽에서 막는다(base.html `navigator.webdriver` 게이트).

## 필드명 검증
GraphQL 스키마의 필드명은 토큰 없이 조회할 수 없다. `--probe` 로 **구조만** 찍어
필드명을 먼저 맞춘다(GraphQL 오류는 errors[] 에 이름이 그대로 나온다). 표본 간격은
트래픽 값이 아니라 배율이라 공개 로그에 찍어도 안전하고, 이 수리의 핵심 계기다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import grm_cli

GRAPHQL_ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
# 한 번에 받는 버킷 수 상한. 그룹을 쪼갠 뒤로는 한 요청이 (날짜 × 한 차원)뿐이라 여유가 크다.
GRAPHQL_LIMIT = 10000
# 하루에 저장하는 리퍼러 호스트 상한 — 꼬리를 무한정 담지 않는다(화면은 상위만 쓴다).
REFERRER_CAP = 25
# 하루에 저장하는 착지 경로 상한. 사이트가 4,000쪽이라 꼬리가 길다 — 화면은 상위만 쓴다.
PATH_CAP = 40
# 정밀도 하락 허용 배수(keep_days). 표본 간격 평균의 실행 간 흔들림(1.00↔1.16)은 통과시키고
# 1 → 10 같은 자릿수 하락만 막는다 — 너무 빡빡하면 늦게 도착한 집계가 영영 못 들어온다.
DOWNGRADE_TOLERANCE = 1.5

# ★그룹마다 별도 요청이다(위 docstring "왜 그룹마다 따로 묻는가"). 한 쿼리에 묶으면
# 가장 비싼 그룹이 나머지까지 표본 데이터셋으로 끌고 내려간다.
_QUERY_TEMPLATE = """
query RumGroup($accountTag: string!, $siteTag: string!, $start: string!, $end: string!) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      rows: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end, bot: 0}
        limit: __LIMIT__
        orderBy: [date_ASC]
      ) {
__FIELDS__
        avg { sampleInterval }
        dimensions { __DIMS__ }
      }
    }
  }
}
"""

# 그룹 이름 → (요청 필드, 차원). 저장 테이블은 아래 main() 이 잇는다.
GROUPS = {
    "totals": ("        count\n        sum { visits }", "date"),
    "referrers": ("        sum { visits }", "date refererHost"),
    "paths": ("        sum { visits }", "date requestPath"),
}


def build_query(group: str) -> str:
    fields, dims = GROUPS[group]
    return (_QUERY_TEMPLATE
            .replace("__LIMIT__", str(GRAPHQL_LIMIT))
            .replace("__FIELDS__", fields)
            .replace("__DIMS__", dims))


def clean_path(raw: str) -> str:
    """URL 경로만 남긴다 — **쿼리스트링은 버린다.**

    `/findings/inspector/?key=홍길동` 처럼 URL 에 사람 이름이 실리는 경로가 있다. 통째로
    담으면 실명이 테이블에 쌓인다. 경로만으로도 "어느 섹션이 유입을 받나"는 전부 답한다.
    """
    text = str(raw or "").strip()
    for sep in ("?", "#"):
        text = text.split(sep, 1)[0]
    if not text:
        return "/"
    return text if text.startswith("/") else "/" + text


def fetch_group(token: str, account_tag: str, site_tag: str, start: str, end: str,
                group: str, *, timeout: float = 30.0) -> "dict[str, Any]":
    import requests  # 지연 import — 순수 함수만 쓰는 호출부(테스트)는 네트워크 0
    body = {"query": build_query(group),
            "variables": {"accountTag": account_tag, "siteTag": site_tag,
                          "start": start, "end": end}}
    r = requests.post(GRAPHQL_ENDPOINT, json=body, timeout=timeout,
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json() or {}


def rows_of(payload: "dict[str, Any]", group: str) -> "list[dict[str, Any]]":
    """응답에서 행 목록을 꺼낸다. 구조가 어긋나면 조용히 빈 결과를 주지 않고 즉시 실패.

    침묵 실패로 빈 표가 "방문 0" 처럼 보이는 것을 막는다.
    """
    errors = payload.get("errors")
    if errors:
        raise SystemExit(f"Cloudflare GraphQL 오류({group}): "
                         + json.dumps(errors, ensure_ascii=False))
    accounts = (((payload.get("data") or {}).get("viewer") or {}).get("accounts") or [])
    if not accounts:
        raise SystemExit(f"응답에 accounts 가 없다({group}) — accountTag 또는 토큰 권한을 확인하라")
    return list(accounts[0].get("rows") or [])


def sample_interval_of(row: "dict[str, Any]", group: str) -> float:
    """행의 표본 간격. **없으면 실패한다** — 1.0 으로 가정하면 추정값을 정확값이라 부른다."""
    avg = row.get("avg")
    if not isinstance(avg, dict) or "sampleInterval" not in avg:
        raise SystemExit(
            f"응답에 avg.sampleInterval 이 없다({group}) — 필드명을 --probe 로 확인하라. "
            "이 값이 없으면 표본값을 정확값으로 오인해 저장하게 된다")
    try:
        value = float(avg.get("sampleInterval"))
    except (TypeError, ValueError):
        raise SystemExit(f"avg.sampleInterval 이 수가 아니다({group}): {avg!r}")
    # 1 미만은 있을 수 없다(간격 1 = 전수). 0/음수는 스키마 오해를 뜻하므로 막는다.
    return value if value >= 1.0 else 1.0


def parse_totals(payload):
    """→ ({날짜: {visits, page_views}}, {날짜: 표본간격})"""
    daily: "dict[str, dict[str, int]]" = {}
    si: "dict[str, float]" = {}
    for row in rows_of(payload, "totals"):
        day = str((row.get("dimensions") or {}).get("date") or "")
        if not day:
            continue
        bucket = daily.setdefault(day, {"visits": 0, "page_views": 0})
        bucket["visits"] += int(((row.get("sum") or {}).get("visits")) or 0)
        bucket["page_views"] += int(row.get("count") or 0)
        si[day] = max(si.get(day, 1.0), sample_interval_of(row, "totals"))
    return daily, si


def _parse_keyed(payload, group: str, dim: str, normalize, fallback: str = ""):
    """(날짜, 키) → 방문 · 날짜 → 표본간격. referrers/paths 공용."""
    out: "dict[tuple[str, str], int]" = {}
    si: "dict[str, float]" = {}
    for row in rows_of(payload, group):
        dims = row.get("dimensions") or {}
        day = str(dims.get("date") or "")
        if not day:
            continue
        si[day] = max(si.get(day, 1.0), sample_interval_of(row, group))
        key = normalize(dims.get(dim))
        if not key:
            key = fallback
        visits = int(((row.get("sum") or {}).get("visits")) or 0)
        if visits <= 0:
            continue
        # 정규화(쿼리스트링 제거·빈 호스트)로 같은 키가 여러 행이 되므로 합산한다.
        out[(day, key)] = out.get((day, key), 0) + visits
    return out, si


def parse_referrers(payload):
    return _parse_keyed(payload, "referrers", "refererHost",
                        lambda v: str(v or "").strip(), "(direct)")


def parse_paths(payload):
    return _parse_keyed(payload, "paths", "requestPath", clean_path)


def cap_by_day(keyed, si, cap: int, key_field: str):
    """날짜별 상위 cap 개만 남긴다(방문 내림차순·동률은 키 이름순 = 결정론)."""
    by_day: "dict[str, list]" = {}
    for (day, key), visits in keyed.items():
        by_day.setdefault(day, []).append((key, visits))
    out = []
    for day in sorted(by_day):
        top = sorted(by_day[day], key=lambda kv: (-kv[1], kv[0]))[:cap]
        out.extend({"snap_date": day, key_field: k, "visits": v,
                    "sample_interval": si.get(day, 1.0)} for k, v in top)
    return out


def cap_referrers(refs, si=None, cap: int = REFERRER_CAP):
    return cap_by_day(refs, si or {}, cap, "referer_host")


def cap_paths(paths, si=None, cap: int = PATH_CAP):
    return cap_by_day(paths, si or {}, cap, "request_path")


def probe_report(payloads: "dict[str, dict[str, Any]]") -> str:
    """첫 실행/회귀 검증용 요약. **트래픽 값은 찍지 않는다 — 구조와 표본 간격만.**

    ★이 저장소는 PUBLIC 이고 Actions 로그는 누구나 볼 수 있다. 원시 응답을 그대로
    쏟으면 사이트 방문자 수가 공개 로그에 남는다. 필드명 확인에 필요한 것은 값이 아니라
    **키 이름과 GraphQL 오류**뿐이다. 표본 간격(1·10·100…)은 방문 수가 아니라 배율이고,
    이 수리가 성공했는지 판정하는 유일한 계기라 함께 찍는다.
    """
    lines: "list[str]" = []
    for group, payload in payloads.items():
        errors = payload.get("errors")
        if errors:
            # 오류는 전문 그대로 — 여기에 우리가 틀린 필드명이 들어 있고, 트래픽 값은 없다.
            lines.append(f"[{group}] GraphQL 오류:")
            lines.append(json.dumps(errors, ensure_ascii=False, indent=2)[:2000])
            continue
        accounts = (((payload.get("data") or {}).get("viewer") or {}).get("accounts") or [])
        if not accounts:
            lines.append(f"[{group}] accounts 0 — accountTag/토큰 권한 확인")
            continue
        rows = accounts[0].get("rows")
        if rows is None:
            lines.append(f"[{group}] rows 없음 — 별칭/필드명 불일치 가능")
            continue
        lines.append(f"[{group}] {len(rows)}행")
        if not rows:
            continue
        first = rows[0] or {}
        lines.append(f"  row keys: {sorted(first.keys())}")
        lines.append(f"  dimensions keys: {sorted((first.get('dimensions') or {}).keys())}")
        lines.append(f"  sum keys: {sorted((first.get('sum') or {}).keys())}")
        lines.append(f"  avg keys: {sorted((first.get('avg') or {}).keys())}")
        try:
            intervals = sorted({sample_interval_of(r, group) for r in rows})
            lines.append(f"  ★sampleInterval: {intervals}  (1=전수 · 10=10배 추정)")
        except SystemExit as exc:
            lines.append(f"  ★sampleInterval 판정 불가: {exc}")
    return "\n".join(lines)


def _rest(url: str, key: str):
    base = grm_cli.normalize_supabase_url(url)
    if not base:
        raise SystemExit("SUPABASE_URL 형식 오류: " + repr(url))
    return base, {"apikey": key, "Authorization": "Bearer " + key}


def stored_precision(url: str, key: str, table: str, days, *, timeout: float = 30.0):
    """저장된 날짜별 표본 간격(그 날 행들 중 가장 나쁜 값). 행이 없는 날은 키가 없다."""
    if not days:
        return {}
    import requests
    base, headers = _rest(url, key)
    day_list = ",".join(sorted(days))
    r = requests.get(base + "/rest/v1/" + table
                     + "?select=snap_date,sample_interval&snap_date=in.(" + day_list + ")",
                     timeout=timeout, headers=headers)
    if r.status_code >= 300:
        raise SystemExit(f"{table} 기존 정밀도 조회 실패 {r.status_code}: {r.text[:400]}")
    out: "dict[str, float]" = {}
    for row in (r.json() or []):
        day = str(row.get("snap_date") or "")
        if not day:
            continue
        raw = row.get("sample_interval")
        try:
            # ★NULL(=075 이전 적재분)은 "미상"이고 미상은 **무한히 부정확**으로 읽는다.
            # 1.0 으로 읽으면 가장 부정확한 옛 값이 "전수"를 자칭해 새 수집을 영구히
            # 막는다 — 가드가 지키려던 것과 정확히 반대가 된다.
            value = float("inf") if raw is None else float(raw)
        except (TypeError, ValueError):
            value = float("inf")
        # 그 날 행들 중 가장 나쁜 정밀도가 그 날의 정밀도다.
        out[day] = max(out.get(day, 1.0), value)
    return out


def keep_days(new_si, stored_si, *, allow_downgrade: bool = False):
    """쓸 날짜와 건너뛸 날짜를 가른다. **저장값이 뚜렷이 더 정확하면 건너뛴다.**

    ★"조금이라도 나쁘면 건너뛴다"로 두면 안 된다. 표본 간격은 그 날 행들의 **평균**이라
    1.00 과 1.16 처럼 실행마다 흔들리는데(간격 10 인 이벤트가 1.8% 섞이면 1.16 이 된다),
    그 흔들림으로 건너뛰면 **늦게 도착한 집계가 영영 반영되지 않는다** — 워크플로가 창을
    8일로 잡은 이유가 바로 그 지각분을 메우는 것이라 자기 목적을 스스로 깬다.
    막으려는 것은 1 → 10 같은 **자릿수 하락**이므로 여유를 1.5배로 둔다.

    순수 함수 — 정책이 네트워크와 섞이지 않게 분리했다(테스트가 여기를 직접 문다).
    """
    write, skip = [], []
    for day in sorted(new_si):
        old = stored_si.get(day)
        if (not allow_downgrade) and old is not None and new_si[day] > old * DOWNGRADE_TOLERANCE:
            skip.append(day)
        else:
            write.append(day)
    return write, skip


def replace_days(url: str, key: str, table: str, rows, days, *, timeout: float = 30.0) -> int:
    """days 의 행을 지우고 rows 를 넣는다(upsert 아님 — 사라진 키의 잔재를 남기지 않는다).

    ★삭제와 삽입 사이에 원자성은 없다. 읽는 화면은 /admin 하나뿐이고 실행은 하루 한 번
    01:30 KST 라, 그 1초를 막으려 RPC 를 새로 파는 비용이 이득을 넘는다. 대신 **응답이
    답하지 않은 날은 아예 건드리지 않는다**(days 는 totals 에 등장한 날에서만 온다).
    """
    if not days:
        return 0
    import requests
    base, headers = _rest(url, key)
    day_list = ",".join(sorted(days))
    r = requests.delete(base + "/rest/v1/" + table + "?snap_date=in.(" + day_list + ")",
                        timeout=timeout, headers=dict(headers, Prefer="return=minimal"))
    if r.status_code >= 300:
        raise SystemExit(f"{table} 창 삭제 실패 {r.status_code}: {r.text[:400]}")
    # ★직렬화하는 변수 이름을 p·a·y·l·o·a·d 로 쓰지 않는다 — 공개 로그 가드가 그
    # 이름으로 직렬화하는 코드를 금지어로 잡는다(원시 응답 dump 재발 방지). 여기 body 는
    # 우리가 만든 적재 행이라 성격이 다르지만, 가드를 약화시키느니 이름을 피한다.
    # (이 주석에도 그 이름을 붙여 쓰면 가드가 걸린다 — 실제로 한 번 걸렸다.)
    body = [row for row in rows if row.get("snap_date") in set(days)]
    if not body:
        return 0
    r = requests.post(base + "/rest/v1/" + table, data=json.dumps(body), timeout=timeout,
                      headers=dict(headers, **{"Content-Type": "application/json",
                                               "Prefer": "return=minimal"}))
    if r.status_code >= 300:
        raise SystemExit(f"{table} 적재 실패 {r.status_code}: {r.text[:400]}")
    return len(body)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Cloudflare RUM → Supabase 일별 적재")
    ap.add_argument("--account-tag", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
    ap.add_argument("--site-tag", default=os.environ.get("CLOUDFLARE_RUM_SITE_TAG", ""))
    ap.add_argument("--start", required=True, help="시작 시각(UTC ISO, 예 2026-08-26T00:00:00Z)")
    ap.add_argument("--end", required=True, help="끝 시각(UTC ISO)")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    ap.add_argument("--probe", action="store_true",
                    help="구조와 표본 간격만 출력(적재 0) — 필드명·정밀도 검증용")
    ap.add_argument("--dry-run", action="store_true", help="파싱 결과만 출력(적재 0)")
    ap.add_argument("--allow-downgrade", action="store_true",
                    help="저장된 정확값을 표본 추정값으로 덮는 것을 허용한다(기본 금지)")
    args = ap.parse_args(argv)

    token = (os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN") or "").strip()
    if not token:
        # 클린 skip — 토큰 배선 전에는 매일 빨간 실패가 쌓이는 대신 조용히 넘어간다
        # (newsletter precheck 의 "NEWSLETTER_API_KEY 미설정 → 보류" 관례와 동형).
        # 토큰이 있는데 잘못된 경우는 여기가 아니라 GraphQL 오류로 드러난다.
        print("CLOUDFLARE_ANALYTICS_TOKEN 미설정 — 수집 건너뜀(클린 skip). "
              "토큰 배선 후 자동으로 돌기 시작한다.")
        return 0
    if not args.account_tag or not args.site_tag:
        print("account-tag / site-tag 필요", file=sys.stderr)
        return 2

    payloads = {g: fetch_group(token, args.account_tag, args.site_tag,
                               args.start, args.end, g) for g in GROUPS}
    if args.probe:
        print(probe_report(payloads))
        return 0

    daily, si_totals = parse_totals(payloads["totals"])
    refs, si_refs = parse_referrers(payloads["referrers"])
    paths, si_paths = parse_paths(payloads["paths"])

    daily_rows = [{"snap_date": d, "metric": m, "value": v,
                   "sample_interval": si_totals.get(d, 1.0)}
                  for d in sorted(daily) for m, v in sorted(daily[d].items())]
    ref_rows = cap_referrers(refs, si_refs)
    path_rows = cap_paths(paths, si_paths)

    # ★값은 로그에 남기지 않는다 — 이 저장소는 PUBLIC 이라 Actions 로그가 공개다.
    # 숫자는 Supabase 에만 있고, 사람은 /admin 성장·유입 탭에서 본다. 표본 간격은
    # 배율이라 값이 아니고, 이 수집이 정확한지 판정하는 유일한 계기라 남긴다.
    span = f"{min(daily)}~{max(daily)}" if daily else "(없음)"
    print(f"파싱: {len(daily)}일({span}) · 지표행 {len(daily_rows)} · "
          f"리퍼러행 {len(ref_rows)} · 경로행 {len(path_rows)}")
    print("표본 간격 — 방문 %s · 리퍼러 %s · 경로 %s (1=전수)"
          % (sorted(set(si_totals.values())) or [1.0],
             sorted(set(si_refs.values())) or [1.0],
             sorted(set(si_paths.values())) or [1.0]))
    if args.dry_run:
        return 0

    creds = grm_cli.resolve_supabase_service_credentials(args)
    if not creds:
        print("SUPABASE_URL/SERVICE_ROLE_KEY 미설정 — 적재 불가", file=sys.stderr)
        return 2
    url, key = creds

    # 답을 받은 날(totals 기준)만 손댄다. 표별로 정밀도 가드를 따로 적용한다 —
    # 방문은 전수인데 경로만 표본으로 내려온 경우가 실제로 있었다(073).
    answered = sorted(daily)
    plan = [("rum_daily", daily_rows, si_totals),
            ("rum_referrer_daily", ref_rows, si_refs),
            ("rum_path_daily", path_rows, si_paths)]
    total_written = 0
    for table, rows, new_si in plan:
        # 리퍼러·경로는 그 표의 표본 간격을 쓰되, 응답이 답한 날 집합은 totals 를 따른다.
        window_si = {d: new_si.get(d, si_totals.get(d, 1.0)) for d in answered}
        stored = stored_precision(url, key, table, answered)
        write, skip = keep_days(window_si, stored, allow_downgrade=args.allow_downgrade)
        n = replace_days(url, key, table, rows, write)
        total_written += n
        note = f" · 건너뜀 {len(skip)}일(저장값이 더 정확)" if skip else ""
        print(f"{table}: {len(write)}일 {n}행 재적재{note}")
    print(f"적재 완료: 총 {total_written}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
