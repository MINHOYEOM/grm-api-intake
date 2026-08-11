#!/usr/bin/env python3
"""FDA Data Dashboard API(``inspections_classifications``) GMP 실사 등급 백필 —
Notion 무접촉, Supabase(``public.fda_inspections``, 058) 직행.

가장 가까운 선례는 `collect_hc_inspection_backfill.py` 다(1회성 백필·Supabase 직행·
멱등·health 표면화·exit 코드 규약을 그대로 계승). 다른 점은 이 소스가 findings 파생이
없는 **평평한 집계표**라는 것 — raw_signals/findings 2단 적재가 아니라
`findings_supabase_append._post_rows`(범용 PostgREST POST 헬퍼, `backfill_483_ocr_recovery.py`
가 이미 그 두 번째 소비처였다)를 그대로 재사용해 `fda_inspections` 테이블 하나에
upsert 한다.

★DDAPI 는 GET 이 아니라 **POST**다 — `grm_common.http_get_json` 을 못 쓴다(GET 전용).
이 파일의 `ddapi_post` 가 같은 재시도 관례(429 Retry-After·5xx 지수 백오프)를 POST 에
맞춰 다시 구현한다.

★응답 판정 3중 함정(임무서 실측 그대로):
  1) 성공 응답은 **dict**, 오류 응답은 **list** — 형이 다르다. `isinstance` 로 먼저 가른다.
  2) 성공인데도 body 의 `statuscode` 필드가 400 으로 온다 — **`statuscode` 로 성공을
     판정하지 않는다.** 판정은 오직 `message == "Success."` 와 `result` 배열 존재.
  3) `filters` 에 `ProjectArea` 를 넣으면 statuscode 413 `invalid_filters:["ProjectArea"]`
     — ProjectArea 는 반환 전용 컬럼이라 필터 불가. 그래서 이 수집기는 `ProductType=Drugs`
     로 **전량**(실측 11,104건, 4개 ProjectArea 혼재)을 받은 뒤 클라이언트에서
     `ProjectArea == "Drug Quality Assurance"`(GMP 제조소 실사, 6,417건)만 골라 싣는다.
     나머지 3개 모집단(Bioresearch Monitoring/Unapproved and Misbranded Drugs/
     Over-the-Counter Drug Evaluation)은 058 마이그레이션 헤더가 이미 설명한 대로
     "성격이 다른 모집단을 한 축에 합치면 진단이 반드시 틀린다"(052/053/054 반복 함정)
     라 싣지 않는다 — 058 의 CHECK 제약이 혹시라도 섞여 들어오면 즉시 insert 를
     실패시키지만(fail-closed), 이 수집기는 그 전에 클라이언트에서 이미 걸러 애초에
     보내지 않는다.

★페이지네이션: 상한 5000(20000 은 HTTP 400 실측) · 정렬은 `InspectionID`/`ASC` 로 안정
페이징. "마지막 페이지 판정"은 서버 total 필드에 의존하지 않는다(이 엔드포인트가 total
카운트를 body 어디에 주는지 확인된 바 없다) — **받은 행 수가 요청한 rows 보다 적으면
마지막 페이지**로 판정한다(대다수 REST 페이지네이션의 안전한 최소 가정). 안전판으로
`--max-pages`(기본 10, 현재 전량 11,104건=3페이지의 3배 이상 여유)를 두고, 이 상한을
꽉 찬 페이지로 소진하면 **더 있을 수 있다는 뜻이므로 조용히 자르지 않고** exit 2 로
loud 하게 실패시킨다(임무서 "조용한 부분 수집이 가장 위험한 실패" — 1회성 백필에서
페이지 하나가 조용히 비면 영영 빈다).

★`filters` 값 형태(예: ``{"ProductType": ["Drugs"]}`` — 리스트로 감싼다)는 라이브 API 를
직접 호출해 검증하지 못했다(이 실행 환경에 FDA_API_USER/FDA_DDAPI_KEY 자격증명이 없다 —
handoff 참고). 오프라인 테스트는 이 가정과 무관하게 응답 파싱·필터링·정규화·적재 로직만
검증한다 — **최초 실사용(--dry-run) 시 이 가정이 틀렸다면 statuscode 413 등으로 즉시
드러난다**(원래도 필터가 틀리면 침묵하지 않고 에러 응답으로 온다).

★`country_key` 는 058 의 GENERATED 컬럼이다 — payload 에 넣으면 insert 전체가 실패한다
(어제 057/058 설계가 이미 겪은 함정과 같은 계열). 이 수집기는 `country_name` 만 보내고
`country_key` 는 절대 만들지 않는다.

★State/StateCode/ZipCode 가 JSON null 로 오는 행(058 헤더 실측: 해외 실사 3,193건)은
반드시 `''` 로 coalesce 한다 — 세 컬럼이 058 에서 not null 이라 null 을 그대로 보내면
그 행의 insert 가 즉시 실패한다.

멱등성: PostgREST upsert 를 `Prefer: resolution=merge-duplicates`(있으면 갱신)로 건다 —
`ignore-duplicates`(있으면 두기, 대다수 일상 수집기의 기본)가 아니라 `merge-duplicates`를
쓰는 이유는 `backfill_483_ocr_recovery.py`와 같다: 이 백필은 "이미 있으면 두기"가 아니라
"최신 등급으로 갱신"이 옳다(같은 InspectionID 가 재실행 때마다 최신 원천값으로 덮여야
한다 — 예: PostedCitations 가 사후에 바뀌는 경우).

exit code 규약(collect_hc_inspection_backfill.py 와 같은 형태):
  0 = 정상
  1 = 건별 실패 있음(행 정규화 실패 / upsert 청크 실패 / GMP 부분집합 내 InspectionID
      중복 — 058 마이그레이션이 "GMP 만 남기면 중복 0" 을 전제로 PK 를 걸었으므로 이
      전제가 깨지면 loud 해야 한다) — merge-duplicates upsert 라 그냥 재실행하면 멱등하게
      메워진다(HC 백필처럼 "실패분만" 재실행할 필요 없음 — 전량 재수집·재적재가 항상
      안전하다).
  2 = 페이지 수집 자체 실패 / 응답 형태 이상(list·message 불일치·result 없음) / 페이지
      상한 소진(더 있을 수 있는데 자름) / 인자·자격증명 위반(0건 침묵 금지)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

import findings_supabase_append as fsa
from grm_common import log, retry_after_seconds

SCHEMA_VERSION = "grm-fda-inspections-backfill/v1"

DDAPI_URL = "https://api-datadashboard.fda.gov/v1/inspections_classifications"

# 원문 컬럼 요청 목록. CountryCode 는 058 테이블에 싣지 않지만(058 헤더 -- 한국 85건
# 전부 포함 96건이 null) 수집 단계 리포트 진단에는 남긴다(정규화에는 안 쓴다).
DDAPI_COLUMNS: tuple[str, ...] = (
    "InspectionID", "FEINumber", "LegalName", "City", "State", "StateCode", "ZipCode",
    "CountryCode", "CountryName", "InspectionEndDate", "FiscalYear", "Classification",
    "ClassificationCode", "ProjectArea", "ProductType", "PostedCitations",
)

PAGE_SIZE = 5000       # 실측 상한(20000 은 HTTP 400).
MAX_PAGES = 10          # 안전판 -- 승인 범위 11,104건(3페이지)의 3배 이상 여유.

# ★수집 범위 = **최근 N 회계연도**(기본 7). 사용자가 2026-08-12 에 세 선택지 중 이걸 골랐다:
#     · 의약품 최근 7년   11,104건 -> GMP 6,417건   ← 채택
#     · 의약품 전체 기간  39,783건                  (오래된 건 실무 가치가 낮다)
#     · 전 제품군        339,727건                 (화장품·식품·기기 -- 범위 밖)
#   ★연도를 하드코딩하지 않는다. [2020..2026] 을 상수로 박으면 내년 FY2027 유입분이
#   조용히 빠진다("사전은 반드시 낡는다" 와 같은 계열의 결함). 실행일에서 계산한다.
#   FDA 회계연도는 10/1~9/30 이므로 10월부터 이듬해 FY 다.
DEFAULT_FISCAL_YEARS = 7


def fiscal_year_of(day: date) -> int:
    """FDA 회계연도(10/1 시작). 2025-10-01 -> FY2026, 2026-09-30 -> FY2026."""
    return day.year + 1 if day.month >= 10 else day.year


def fiscal_year_window(run_date: date, years: int = DEFAULT_FISCAL_YEARS) -> list[str]:
    """최근 N 회계연도를 최신순 문자열 목록으로. DDAPI filters 는 문자열 리스트를 받는다."""
    if years < 1:
        raise ValueError("fiscal years must be >= 1")
    latest = fiscal_year_of(run_date)
    return [str(latest - offset) for offset in range(years)]

GMP_PROJECT_AREA = "Drug Quality Assurance"
TARGET_PRODUCT_TYPE = "Drugs"
_VALID_CLASSIFICATION_CODES = ("NAI", "VAI", "OAI")   # 058 CHECK 제약과 동일

TABLE = "fda_inspections"
ON_CONFLICT = "inspection_id"
UPSERT_RESOLUTION = "merge-duplicates"   # 위 모듈독스트링 참고 -- ignore-duplicates 아님.
UPSERT_CHUNK_SIZE = 1000                 # 6,417건을 한 POST 에 몰지 않는다(413/timeout 방어).

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_RETRIES = 2

# 이 스크립트가 매개하는 http_post / poster 는 테스트에서 인자로 치환된다.
HttpPost = Callable[..., Any]
Poster = Callable[..., Any]


# ─────────────────────────────────────────────────────────────────────────────
# 순수 함수(네트워크 0) — 요청 바디 / 응답 판정 / 필터 / 정규화
# ─────────────────────────────────────────────────────────────────────────────


def build_request_body(start: int, rows: int,
                       fiscal_years: list[str] | None = None) -> dict[str, Any]:
    """DDAPI 요청 바디. 5개 필드 전부 필수(임무서 실측 -- 하나라도 빠지면 statuscode 410).

    ★`FiscalYear` 는 **반드시 걸어야 한다.** 안 걸면 전체 기간 39,783건이 들어와
    사용자가 고른 범위(최근 7 회계연도 11,104건)를 3.5배 넘긴다 -- 2026-08-12 적대적
    검증이 실제로 이 결손을 잡아냈다(수집기에 필터가 없는데 모든 실측치는 7년 기준이라,
    라이브로 돌리면 6,417/1,409/4,055/953 이 재현되지 않아 "뭔가 깨졌다"고 오판했을 것).
    `ProjectArea` 와 달리 `FiscalYear` 는 필터 가능 필드다(정의 문서 Checked).
    """
    years = list(fiscal_years) if fiscal_years else fiscal_year_window(date.today())
    return {
        "start": start,
        "rows": rows,
        "sort": "InspectionID",
        "sortorder": "ASC",
        # ProjectArea 는 여기 넣지 않는다(반환 전용 컬럼 -- 넣으면 413 invalid_filters).
        "filters": {"ProductType": [TARGET_PRODUCT_TYPE], "FiscalYear": years},
        "columns": list(DDAPI_COLUMNS),
    }


def parse_response(payload: Any) -> tuple[list[dict[str, Any]], str]:
    """DDAPI 응답 판정. 성공: dict + message=='Success.' + result 배열 존재.

    ★`statuscode` 필드는 절대 성공 판정에 쓰지 않는다(성공인데도 400 으로 오는 실측
    함정). 오류 응답은 list 로 오므로 dict 가 아니면 그 자체로 오류다.
    """
    if isinstance(payload, list):
        snippet = json.dumps(payload, ensure_ascii=False)[:300]
        return [], f"error_response_is_list:{snippet}"
    if not isinstance(payload, dict):
        return [], f"unexpected_response_type:{type(payload).__name__}"
    message = str(payload.get("message") or "")
    if message != "Success.":
        return [], f"unexpected_message:{message!r}_statuscode={payload.get('statuscode')!r}"
    result = payload.get("result")
    if not isinstance(result, list):
        return [], "missing_result_array"
    return [r for r in result if isinstance(r, dict)], ""


def split_by_project_area(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """GMP(Drug Quality Assurance) 행만 남기고, 제외한 나머지는 ProjectArea 별 건수로.

    ★"몇 건 걸렀다"가 아니라 "어느 모집단에서 몇 건"이어야 한다 -- 058 헤더가 이미
    실측해 둔 3개 모집단(BIMO/Unapproved/OTC) 외에 새 값이 나타나도 여기서 그대로
    이름이 잡힌다(손으로 3개만 허용목록화하지 않는다 -- 미지의 4번째 모집단이 조용히
    GMP 로 새는 게 아니라, 조용히 '제외' 버킷에 새 이름으로 잡혀야 한다).
    """
    included: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    for row in rows:
        area = str(row.get("ProjectArea") or "")
        if area == GMP_PROJECT_AREA:
            included.append(row)
        else:
            excluded[area] = excluded.get(area, 0) + 1
    return included, excluded


def find_duplicate_inspection_ids(rows: list[dict[str, Any]]) -> dict[str, int]:
    """GMP 부분집합 내 InspectionID 중복 건수. 058 마이그레이션의 "GMP 만 남기면 중복
    0"이라는 실측 전제를 매 실행 재확인한다 -- 전제가 깨지면 여기서 즉시 드러난다."""
    counts: dict[str, int] = {}
    for row in rows:
        iid = str(row.get("InspectionID") or "").strip()
        counts[iid] = counts.get(iid, 0) + 1
    return {iid: c for iid, c in counts.items() if c > 1}


def _coalesce_str(value: Any) -> str:
    """JSON null -> ''. 058 의 not null 컬럼(state/state_code/zip_code 등)에 필수."""
    if value is None:
        return ""
    return str(value).strip()


def normalize_row(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """DDAPI 행 -> `fda_inspections` upsert payload. 실패하면 (빈 dict, 사유).

    ★`country_key` 는 GENERATED 컬럼이라 여기서 절대 만들지 않는다 -- 만들어 넣으면
    insert 전체가 실패한다(057/058 헤더가 이미 겪은 함정과 같은 계열).
    """
    inspection_id = _coalesce_str(row.get("InspectionID"))
    if not inspection_id:
        return {}, "missing_inspection_id"

    fiscal_year_raw = row.get("FiscalYear")
    try:
        fiscal_year = int(str(fiscal_year_raw).strip())
    except (TypeError, ValueError):
        return {}, f"unparseable_fiscal_year:{fiscal_year_raw!r}"

    classification_code = _coalesce_str(row.get("ClassificationCode"))
    if classification_code not in _VALID_CLASSIFICATION_CODES:
        return {}, f"unexpected_classification_code:{classification_code!r}"

    project_area = _coalesce_str(row.get("ProjectArea"))
    if project_area != GMP_PROJECT_AREA:
        # 방어적 -- 호출부(run)가 이미 split_by_project_area 로 걸러야 정상 도달 안 함.
        return {}, f"unexpected_project_area:{project_area!r}"

    product_type = _coalesce_str(row.get("ProductType"))
    if product_type != TARGET_PRODUCT_TYPE:
        return {}, f"unexpected_product_type:{product_type!r}"

    end_date = _coalesce_str(row.get("InspectionEndDate"))

    payload = {
        "inspection_id": inspection_id,
        "fei_number": _coalesce_str(row.get("FEINumber")),
        "legal_name": _coalesce_str(row.get("LegalName")),
        "city": _coalesce_str(row.get("City")),
        # ★null -> '' coalesce 필수(058 헤더 실측: 해외 실사 3,193건이 이 세 필드에서 null).
        "state": _coalesce_str(row.get("State")),
        "state_code": _coalesce_str(row.get("StateCode")),
        "zip_code": _coalesce_str(row.get("ZipCode")),
        "country_name": _coalesce_str(row.get("CountryName")),
        "inspection_end_date": end_date or None,
        "fiscal_year": fiscal_year,
        "classification": _coalesce_str(row.get("Classification")),
        "classification_code": classification_code,
        "project_area": project_area,
        "product_type": product_type,
        "posted_citations": _coalesce_str(row.get("PostedCitations")),
        # country_key -- 절대 넣지 않는다(GENERATED, 위 독스트링 참고).
    }
    return payload, ""


# ─────────────────────────────────────────────────────────────────────────────
# 네트워크 — DDAPI POST 전송
# ─────────────────────────────────────────────────────────────────────────────


def ddapi_post(
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
) -> Any:
    """POST to DDAPI with 429 Retry-After / 5xx exponential retry.

    ``grm_common.http_get_json`` 은 GET 전용이라 못 쓴다 -- 같은 재시도 관례를 POST 에
    맞춰 재구현한다. 반환값은 파싱된 JSON 그대로(성공 dict / 오류 list) -- 판정은
    호출부의 ``parse_response`` 가 한다(여기서는 형을 미리 재단하지 않는다).
    """
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_err = e
            log("WARN", f"DDAPI POST 실패({attempt + 1}/{retries + 1}): {type(e).__name__}: {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"DDAPI POST 최종 실패: {last_err}") from last_err

        if resp.status_code == 429:
            if attempt < retries:
                sleep_s = retry_after_seconds(resp, attempt)
                log("WARN", f"DDAPI 429 rate-limit sleep={sleep_s}s attempt={attempt + 1}/{retries + 1}")
                time.sleep(sleep_s)
                continue
            raise RuntimeError(f"DDAPI POST HTTP 429 최종 실패")
        if resp.status_code >= 500:
            if attempt < retries:
                log("WARN", f"DDAPI 5xx({resp.status_code}) 재시도 {attempt + 1}/{retries + 1}")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"DDAPI POST HTTP {resp.status_code} 최종 실패")

        try:
            return resp.json()
        except ValueError as e:
            raise RuntimeError(f"DDAPI JSON 파싱 실패: {e}") from e

    raise RuntimeError("DDAPI POST 재시도 소진")  # 도달 불가 안전망


@dataclass
class FetchResult:
    rows: list[dict[str, Any]]
    truncated: bool
    pages_fetched: int
    error: str = ""


def fetch_all_pages(
    *,
    api_user: str,
    api_key: str,
    http_post: HttpPost,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    fiscal_years: list[str] | None = None,
) -> FetchResult:
    """ProductType=Drugs 를 **최근 N 회계연도로 한정**해 안정 정렬로 페이지네이션 수집.

    마지막 페이지 판정: 받은 행 수가 요청 rows 보다 적으면 종료(서버 total 필드에
    의존하지 않는다 -- 위 모듈독스트링 참고). max_pages 를 꽉 찬 페이지로 소진하면
    `truncated=True` 로 표면화한다(호출부가 이를 loud 하게 실패 처리해야 한다).
    """
    headers = {
        "Authorization-User": api_user,
        "Authorization-Key": api_key,
        "Content-Type": "application/json",
    }
    all_rows: list[dict[str, Any]] = []
    start = 0
    pages = 0
    while pages < max_pages:
        body = build_request_body(start, page_size, fiscal_years)
        try:
            payload = http_post(DDAPI_URL, headers=headers, body=body,
                                timeout=timeout, retries=retries)
        except Exception as e:  # noqa: BLE001 -- 예외 타입/문자열이 곧 진단이다
            return FetchResult(all_rows, False, pages,
                               f"page_fetch_failed(start={start}):{type(e).__name__}: {e}")
        rows, err = parse_response(payload)
        if err:
            return FetchResult(all_rows, False, pages, f"page_error(start={start}):{err}")
        pages += 1
        all_rows.extend(rows)
        log("INFO", f"DDAPI 페이지 {pages} start={start} rows={len(rows)} (누적 {len(all_rows)})")
        if len(rows) < page_size:
            return FetchResult(all_rows, False, pages, "")
        start += page_size
    return FetchResult(all_rows, True, pages, "")


# ─────────────────────────────────────────────────────────────────────────────
# 리포트 · 실행
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class BackfillReport:
    schema_version: str = SCHEMA_VERSION
    dry_run: bool = True
    pages_fetched: int = 0
    truncated: bool = False
    received_total: int = 0                 # ProductType=Drugs 전량(모든 ProjectArea 포함)
    excluded_by_project_area: dict[str, int] = field(default_factory=dict)
    gmp_candidates: int = 0                 # ProjectArea == 'Drug Quality Assurance'
    duplicate_inspection_ids: dict[str, int] = field(default_factory=dict)
    normalize_failed: int = 0
    # ★"몇 건"이 아니라 "어느 InspectionID 가 왜" -- HC 백필의 excluded_firms 와 같은 원칙.
    normalize_errors: list[str] = field(default_factory=list)
    would_upsert: int = 0
    upserted: int = 0
    upsert_chunk_failed: int = 0
    errors: list[str] = field(default_factory=list)


def run(
    *,
    dry_run: bool,
    base_url: str,
    service_key: str,
    api_user: str,
    api_key: str,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    chunk_size: int = UPSERT_CHUNK_SIZE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    http_post: HttpPost | None = None,
    poster: Poster | None = None,
    fiscal_years: list[str] | None = None,
) -> tuple[BackfillReport, int]:
    # 지연 바인딩 -- 테스트가 인자로 갈아끼운다.
    http_post = http_post or ddapi_post
    poster = poster or fsa._post_rows

    report = BackfillReport(dry_run=dry_run)

    fetch = fetch_all_pages(
        api_user=api_user, api_key=api_key, http_post=http_post,
        page_size=page_size, max_pages=max_pages, timeout=timeout, retries=retries,
        fiscal_years=fiscal_years,
    )
    report.pages_fetched = fetch.pages_fetched
    report.truncated = fetch.truncated

    if fetch.error:
        report.errors.append(f"fetch_failed:{fetch.error}")
        log("ERROR", f"DDAPI 수집 실패: {fetch.error}")
        return report, 2

    if fetch.truncated:
        report.errors.append(
            f"truncated:max_pages={max_pages}_exhausted_with_full_last_page")
        log("ERROR", f"페이지 상한({max_pages}) 소진 -- 마지막 페이지가 꽉 찼다"
                     "(더 있을 수 있는데 조용히 자르지 않는다 -- --max-pages 를 늘려 재실행하라)")
        return report, 2

    report.received_total = len(fetch.rows)
    included, excluded = split_by_project_area(fetch.rows)
    report.excluded_by_project_area = excluded
    report.gmp_candidates = len(included)
    log("INFO", f"수신 {report.received_total}건 -- GMP(Drug Quality Assurance) "
                f"{report.gmp_candidates}건 / 제외 {sum(excluded.values())}건 {excluded}")

    report.duplicate_inspection_ids = find_duplicate_inspection_ids(included)
    if report.duplicate_inspection_ids:
        log("WARN", f"GMP 부분집합에 중복 InspectionID {len(report.duplicate_inspection_ids)}종 -- "
                    "058 마이그레이션의 'GMP 만 남기면 중복 0' 전제가 이번 수집에서 깨졌다"
                    f": {report.duplicate_inspection_ids}")

    payloads: list[dict[str, Any]] = []
    for row in included:
        payload, err = normalize_row(row)
        if err:
            report.normalize_failed += 1
            iid = str(row.get("InspectionID") or "?")
            report.normalize_errors.append(f"{iid}:{err}")
            continue
        payloads.append(payload)
    if report.normalize_failed:
        log("WARN", f"정규화 실패 {report.normalize_failed}건 -- normalize_errors 참조")

    if dry_run:
        report.would_upsert = len(payloads)
        log("INFO", f"[DRY] upsert 대상 {len(payloads)}건 (실적재 없음)")
    else:
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i:i + chunk_size]
            status, _rows, err = poster(
                base_url, service_key, TABLE, chunk, ON_CONFLICT,
                resolution=UPSERT_RESOLUTION,
            )
            if err:
                report.upsert_chunk_failed += 1
                report.errors.append(
                    f"upsert_chunk_failed(start={i},size={len(chunk)}):status={status}:{err}")
                log("WARN", f"upsert 청크 실패 start={i} size={len(chunk)} status={status}: {err}")
                continue
            report.upserted += len(chunk)
        log("INFO", f"적재 {report.upserted}건 / 청크실패 {report.upsert_chunk_failed}")

    exit_code = 1 if (report.normalize_failed or report.upsert_chunk_failed
                      or report.duplicate_inspection_ids) else 0
    return report, exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FDA Data Dashboard API(inspections_classifications) GMP 실사 등급 "
        "백필 -- Supabase 직행(collect_hc_inspection_backfill.py 와 같은 패턴). "
        "ProductType=Drugs 전량을 받아 클라이언트에서 Drug Quality Assurance 만 남긴다.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                      help="DDAPI 는 호출하되 Supabase 적재는 생략한다(기본값).")
    mode.add_argument("--apply", dest="dry_run", action="store_false",
                      help="실제로 Supabase 에 적재한다(기본은 dry-run).")
    p.add_argument("--fiscal-years", type=int, default=DEFAULT_FISCAL_YEARS,
                   help=("수집할 최근 회계연도 수(기본 %d). 사용자가 승인한 범위가 7년이다 — "
                         "전체 기간은 39,783건으로 3.5배다. 연도는 실행일에서 계산하므로 "
                         "해가 바뀌어도 낡지 않는다." % DEFAULT_FISCAL_YEARS))
    p.add_argument("--page-size", type=int, default=PAGE_SIZE,
                   help=f"페이지당 rows(상한 5000 -- 20000 은 HTTP 400). 기본 {PAGE_SIZE}.")
    p.add_argument("--max-pages", type=int, default=MAX_PAGES,
                   help=f"안전판 페이지 상한. 기본 {MAX_PAGES}. 꽉 찬 채로 소진하면 exit 2.")
    p.add_argument("--chunk-size", type=int, default=UPSERT_CHUNK_SIZE,
                   help=f"upsert POST 당 행 수. 기본 {UPSERT_CHUNK_SIZE}.")
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                   help=f"HTTP 재시도 횟수. 기본 {DEFAULT_RETRIES}.")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                   help=f"HTTP 타임아웃(초). 기본 {DEFAULT_TIMEOUT_SECONDS}.")
    p.add_argument("--api-user", help="미지정 시 $FDA_API_USER")
    p.add_argument("--api-key", help="미지정 시 $FDA_DDAPI_KEY")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    p.add_argument("--output", help="JSON 리포트를 이 경로에도 기록.")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)

    api_user = (args.api_user or os.environ.get("FDA_API_USER") or "").strip()
    api_key = (args.api_key or os.environ.get("FDA_DDAPI_KEY") or "").strip()
    if not (api_user and api_key):
        # ★dry-run 도 실 API 를 호출한다(수집 자체가 목적이므로) -- 조용히 0건으로
        # 떨어지지 않고 여기서 명확히 실패한다.
        print("collect_fda_inspections: --api-user/--api-key 또는 "
              "$FDA_API_USER/$FDA_DDAPI_KEY 필요(dry-run 도 실 API 를 호출한다)",
              file=sys.stderr)
        return 2

    base = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not args.dry_run and not (base and key):
        print("collect_fda_inspections: --supabase-url/--service-role-key 또는 "
              "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요(--apply 모드)", file=sys.stderr)
        return 2

    report, exit_code = run(
        dry_run=args.dry_run, base_url=base, service_key=key,
        api_user=api_user, api_key=api_key,
        fiscal_years=fiscal_year_window(date.today(), args.fiscal_years),
        page_size=args.page_size, max_pages=args.max_pages, chunk_size=args.chunk_size,
        timeout=args.timeout, retries=args.retries,
    )

    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return exit_code


__all__ = [
    "BackfillReport",
    "FetchResult",
    "build_request_body",
    "fiscal_year_of",
    "fiscal_year_window",
    "parse_response",
    "split_by_project_area",
    "find_duplicate_inspection_ids",
    "normalize_row",
    "ddapi_post",
    "fetch_all_pages",
    "run",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
