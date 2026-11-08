#!/usr/bin/env python3
"""FDA Data Dashboard API(``inspections_citations``) 실사 지적 조항 수집 —
Notion 무접촉, Supabase(``public.fda_inspection_citations``, 133) 직행.

`collect_fda_inspections.py`(실사 등급 ``inspections_classifications``, 058/059)의 형제다.
같은 자격증명(`FDA_API_USER`/`FDA_DDAPI_KEY`, 헤더 `Authorization-User`/`Authorization-Key`),
같은 POST 전송·재시도(`ddapi_post`)·같은 응답 판정(`parse_response`)·같은 회계연도 창
(`fiscal_year_window`, 기본 최근 7년)을 **그대로 import 해서** 쓴다 — 두 수집기가 같은
API 계약을 따로 구현하면 한쪽만 고쳐져 갈라진다(08-12 `start` 0/1 사고가 바로 프로브와
구현이 갈라진 사례였다).

★citations 는 findings(483 서술형 지적)와 섞지 않는다. 이 표의 행은 "21 CFR 211.192"
같은 **조항 번호 + 짧은 정형 문구**다. 483 관찰 본문과 한 표에 두면 검색·집계가 두
모집단을 합쳐 버린다(052/053/054 반복 함정). 그래서 별도 평평한 표 하나에만 싣는다.

★문서(api-definitions-citations.htm, 2026-11-08 확인)와 classifications 의 차이:
  - citations 에는 **ProductType 도 ProjectArea 도 없다.** 대신 `ProgramArea`(필터 가능,
    Partial 일치)가 있다. 범위(의약품·생물의약품)는 `ProgramArea` 로 건다.
  - `CitationID` 가 있고 `InspectionID` 가 있다 — classifications 행(058 PK = InspectionID)과
    **InspectionID 로 연결**한다. FK 는 걸지 않는다(fda_inspections 는 GMP 의약품만 싣고
    생물의약품·BIMO 실사는 거기 없다 — 연결은 조인으로 판정한다).
  - `InspectionEndDate` 는 이름 그대로는 필터 불가(From/To 범위 필터만). 우리는
    `FiscalYear` 로 건다(classifications 와 같은 창).

★ProgramArea 실측값은 아직 없다(로컬에 키가 없다). 기본값 ``Drugs``/``Biologics`` 는
classifications 의 ProductType 값 목록(문서: Biologics, Devices, Drugs, Food/Cosmetics,
Tobacco, Veterinary)과 같은 표기라는 **가정**이다. 그래서:
  - 요청 필터는 Partial 일치라 넓게 들어올 수 있다 → 클라이언트에서 **정확히 일치**하는
    값만 남기고, 나머지는 "어느 값이 몇 건" 으로 리포트에 이름을 남긴다(조용히 버리지 않는다).
  - 요청한 ProgramArea 중 하나라도 0건이면 exit 2(라벨이 다르다는 신호 — 리포트의
    `excluded_by_program_area` 에 실제 라벨이 찍힌다). 첫 dry-run 이 이 가정을 검증한다.

★정렬: `sort=""`/`sortorder=""` 로 보내 **서버 기본 정렬 = 표의 고유 기본키**를 쓴다
(문서: "Default: the table's unique primary key", 예시 (a) 가 빈 문자열을 보낸다).
CitationID 가 행마다 유일한지 아직 실측이 없어, 동점이 있을 수 있는 열로 정렬하면
페이지 경계에서 행이 겹치거나 빠질 수 있다. 대신 `returntotalcount: true` 로 서버 총수를
받아 **받은 행 수와 대조**한다 — 어긋나면 exit 2(페이징이 행을 잃었다는 뜻).

★페이지네이션(08-12 교훈 계승): `start` 는 **1부터**(0 이면 HTTP 400), rows 상한 5000,
받은 행 수 < rows 면 마지막 페이지, `--max-pages` 를 꽉 찬 페이지로 소진하면 exit 2.

★`country_key` 는 133 의 GENERATED 컬럼이다 — payload 에 넣지 않는다.
★한국 국가명 실측값은 ``Korea (the Republic of)`` 다(classifications 실측과 같다고 가정 —
리포트가 'korea' 를 포함하는 모든 국가명을 따로 세어 이 가정도 함께 검증한다).

멱등성: upsert ``on_conflict=inspection_id,citation_id`` + ``merge-duplicates``
(classifications 와 같은 이유 — 재실행마다 최신 원천값으로 덮는다). 월간 실행은 창 전량을
매번 다시 싣는다(수집 상태를 저장하지 않는다).

exit code(collect_fda_inspections.py 와 같은 규약):
  0 = 정상
  1 = 건별 실패(정규화 실패 / upsert 청크 실패 / 키 중복)
  2 = 수집 실패 / 응답 형태 이상 / 페이지 상한 소진 / 서버 총수 불일치 /
      요청한 ProgramArea 0건 / 인자·자격증명 위반
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import requests

import findings_supabase_append as fsa
from collect_fda_inspections import (
    DEFAULT_FISCAL_YEARS,
    DEFAULT_RETRIES,
    PAGE_SIZE,
    ddapi_post,
    fiscal_year_window,
    parse_response,
)
from grm_common import log

SCHEMA_VERSION = "grm-fda-citations/v1"

DDAPI_URL = "https://api-datadashboard.fda.gov/v1/inspections_citations"

DDAPI_COLUMNS: tuple[str, ...] = (
    "InspectionID", "CitationID", "FEINumber", "LegalName", "City", "State", "StateCode",
    "ZipCode", "CountryCode", "CountryName", "InspectionEndDate", "FiscalYear",
    "ProgramArea", "ActCFRNumber", "ShortDescription", "LongDescription", "FirmProfile",
)

DEFAULT_PROGRAM_AREAS: tuple[str, ...] = ("Drugs", "Biologics")   # 133 CHECK 제약과 동일
DRUGS = "Drugs"
KOREA_COUNTRY_NAME = "Korea (the Republic of)"
TOP_CFR_N = 20

MAX_PAGES = 40           # 5000 x 40 = 20만 행. 7년 의약품+생물의약품 지적 조항의 여유분.
DEFAULT_TIMEOUT_SECONDS = 120   # LongDescription 이 붙어 페이지가 classifications 보다 무겁다.

TABLE = "fda_inspection_citations"
ON_CONFLICT = "inspection_id,citation_id"
UPSERT_RESOLUTION = "merge-duplicates"
UPSERT_CHUNK_SIZE = 1000

CLASSIFICATIONS_TABLE = "fda_inspections"
LINK_PAGE_SIZE = 1000

HttpPost = Callable[..., Any]
Poster = Callable[..., Any]
LinkLoader = Callable[[str, str], tuple[set[str] | None, str]]


# ─────────────────────────────────────────────────────────────────────────────
# 순수 함수(네트워크 0)
# ─────────────────────────────────────────────────────────────────────────────


def build_request_body(start: int, rows: int, fiscal_years: list[str],
                       program_areas: list[str], *, sort: str = "",
                       sortorder: str = "") -> dict[str, Any]:
    """citations 요청 바디. FiscalYear 는 **반드시** 건다(빠뜨리면 전체 기간이 들어와
    모집단이 오염된다 — 08-12 classifications 적대적 검증이 잡은 결손)."""
    if not fiscal_years:
        raise ValueError("fiscal_years 가 비었다 -- 회계연도 스코프 필터는 필수다")
    filters: dict[str, Any] = {"FiscalYear": list(fiscal_years)}
    if program_areas:
        filters["ProgramArea"] = list(program_areas)
    return {
        "start": start,
        "rows": rows,
        "sort": sort,
        "sortorder": sortorder,
        "returntotalcount": True,
        "filters": filters,
        "columns": list(DDAPI_COLUMNS),
    }


def split_by_program_area(
    rows: list[dict[str, Any]], program_areas: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """정확히 일치하는 ProgramArea 만 남기고, 나머지는 값별 건수로 이름을 남긴다."""
    wanted = set(program_areas)
    included: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    for row in rows:
        area = str(row.get("ProgramArea") or "")
        if area in wanted:
            included.append(row)
        else:
            excluded[area] = excluded.get(area, 0) + 1
    return included, excluded


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int_id(value: Any) -> str:
    """숫자 ID 를 문자열로. 1234.0 처럼 float 로 와도 '1234' 로 맞춘다
    (classifications 의 inspection_id 텍스트와 조인되어야 한다)."""
    s = _s(value)
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def find_duplicate_keys(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(f"{_int_id(r.get('InspectionID'))}:{_int_id(r.get('CitationID'))}"
                     for r in rows)
    return {k: c for k, c in counts.items() if c > 1}


def normalize_row(row: dict[str, Any], program_areas: list[str]) -> tuple[dict[str, Any], str]:
    inspection_id = _int_id(row.get("InspectionID"))
    if not inspection_id:
        return {}, "missing_inspection_id"
    citation_id = _int_id(row.get("CitationID"))
    if not citation_id:
        return {}, "missing_citation_id"
    fy_raw = row.get("FiscalYear")
    try:
        fiscal_year = int(_s(fy_raw))
    except ValueError:
        return {}, f"unparseable_fiscal_year:{fy_raw!r}"
    program_area = _s(row.get("ProgramArea"))
    if program_area not in program_areas:
        return {}, f"unexpected_program_area:{program_area!r}"
    end_date = _s(row.get("InspectionEndDate"))[:10]
    return {
        "inspection_id": inspection_id,
        "citation_id": citation_id,
        "fei_number": _int_id(row.get("FEINumber")),
        "legal_name": _s(row.get("LegalName")),
        "city": _s(row.get("City")),
        "state": _s(row.get("State")),
        "state_code": _s(row.get("StateCode")),
        "zip_code": _s(row.get("ZipCode")),
        "country_name": _s(row.get("CountryName")),
        "inspection_end_date": end_date or None,
        "fiscal_year": fiscal_year,
        "program_area": program_area,
        "act_cfr_number": _s(row.get("ActCFRNumber")),
        "short_description": _s(row.get("ShortDescription")),
        "long_description": _s(row.get("LongDescription")),
        "firm_profile_url": _s(row.get("FirmProfile")),
        # country_key -- 넣지 않는다(GENERATED).
    }, ""


def summarize(payloads: list[dict[str, Any]], linked_ids: set[str] | None) -> dict[str, Any]:
    """대표가 첫 dry-run 에서 볼 숫자: ProgramArea 별 전체·회계연도별·한국·상위 20 CFR.
    linked_ids(classifications 의 inspection_id 집합)가 있으면 GMP 연결분을 따로 센다."""

    def block(rows: list[dict[str, Any]]) -> dict[str, Any]:
        by_fy = Counter(r["fiscal_year"] for r in rows)
        cfr = Counter(r["act_cfr_number"] for r in rows if r["act_cfr_number"])
        korea = [r for r in rows if r["country_name"] == KOREA_COUNTRY_NAME]
        return {
            "total": len(rows),
            "inspections": len({r["inspection_id"] for r in rows}),
            "by_fiscal_year": {str(k): by_fy[k] for k in sorted(by_fy)},
            "korea": len(korea),
            "korea_inspections": len({r["inspection_id"] for r in korea}),
            "korea_top_cfr": [[k, c] for k, c in Counter(
                r["act_cfr_number"] for r in korea if r["act_cfr_number"]).most_common(10)],
            "top_cfr": [[k, c] for k, c in cfr.most_common(TOP_CFR_N)],
        }

    out: dict[str, Any] = {}
    for area in sorted({p["program_area"] for p in payloads}):
        out[area] = block([p for p in payloads if p["program_area"] == area])
    # 'Korea' 가 들어간 국가명 전수 -- KOREA_COUNTRY_NAME 가정 검증용.
    out["_korea_like_country_names"] = dict(Counter(
        p["country_name"] for p in payloads if "korea" in p["country_name"].lower()))
    if linked_ids is not None:
        linked = [p for p in payloads if p["inspection_id"] in linked_ids]
        out["_linked_to_classifications"] = block(linked)
        out["_linked_to_classifications"]["classified_inspections_total"] = len(linked_ids)
        cited = {p["inspection_id"] for p in payloads}
        out["_linked_to_classifications"]["classified_inspections_with_citations"] = len(
            linked_ids & cited)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 네트워크
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FetchResult:
    rows: list[dict[str, Any]]
    truncated: bool
    pages_fetched: int
    server_total: int | None = None
    error: str = ""


def fetch_all_pages(*, api_user: str, api_key: str, http_post: HttpPost,
                    fiscal_years: list[str], program_areas: list[str],
                    page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES,
                    timeout: int = DEFAULT_TIMEOUT_SECONDS, retries: int = DEFAULT_RETRIES,
                    sort: str = "", sortorder: str = "") -> FetchResult:
    headers = {
        "Authorization-User": api_user,
        "Authorization-Key": api_key,
        "Content-Type": "application/json",
    }
    all_rows: list[dict[str, Any]] = []
    server_total: int | None = None
    start = 1   # ★1부터 센다(0 이면 HTTP 400 -- 2026-08-12 실장애).
    pages = 0
    while pages < max_pages:
        body = build_request_body(start, page_size, fiscal_years, program_areas,
                                  sort=sort, sortorder=sortorder)
        try:
            payload = http_post(DDAPI_URL, headers=headers, body=body,
                                timeout=timeout, retries=retries)
        except Exception as e:  # noqa: BLE001
            return FetchResult(all_rows, False, pages, server_total,
                               f"page_fetch_failed(start={start}):{type(e).__name__}: {e}")
        rows, err = parse_response(payload)
        if err:
            return FetchResult(all_rows, False, pages, server_total,
                               f"page_error(start={start}):{err}")
        if isinstance(payload, dict) and server_total is None:
            raw_total = payload.get("totalrecordcount")
            try:
                server_total = int(raw_total) if raw_total is not None else None
            except (TypeError, ValueError):
                server_total = None
        pages += 1
        all_rows.extend(rows)
        log("INFO", f"DDAPI citations 페이지 {pages} start={start} rows={len(rows)} "
                    f"(누적 {len(all_rows)} / 서버 총수 {server_total})")
        if len(rows) < page_size:
            return FetchResult(all_rows, False, pages, server_total, "")
        start += page_size
    return FetchResult(all_rows, True, pages, server_total, "")


def load_classified_inspection_ids(base_url: str, service_key: str, *,
                                   timeout: int = 60) -> tuple[set[str] | None, str]:
    """`fda_inspections.inspection_id` 전량(약 6,400건)을 PostgREST 로 읽는다.
    실패해도 수집을 막지 않는다 -- (None, 사유)를 돌려 리포트에 남긴다.
    ★오류 사유에 예외 본문을 싣지 않는다(service-role 키 유출 방지, fsa._post_rows 와 같다)."""
    if not (base_url and service_key):
        return None, "no_supabase_credentials"
    url = f"{base_url.rstrip('/')}/rest/v1/{CLASSIFICATIONS_TABLE}"
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    ids: set[str] = set()
    offset = 0
    while True:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, params={
                "select": "inspection_id", "order": "inspection_id",
                "limit": LINK_PAGE_SIZE, "offset": offset})
        except requests.RequestException as e:
            return None, f"link_fetch_failed:{type(e).__name__}"
        if resp.status_code != 200:
            return None, f"link_fetch_http_{resp.status_code}"
        try:
            batch = resp.json()
        except ValueError:
            return None, "link_fetch_bad_json"
        if not isinstance(batch, list):
            return None, "link_fetch_not_list"
        ids.update(_int_id(r.get("inspection_id")) for r in batch if isinstance(r, dict))
        if len(batch) < LINK_PAGE_SIZE:
            return ids, ""
        offset += LINK_PAGE_SIZE


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CitationsReport:
    schema_version: str = SCHEMA_VERSION
    dry_run: bool = True
    fiscal_years: list[str] = field(default_factory=list)
    program_areas: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    truncated: bool = False
    server_total: int | None = None
    received_total: int = 0
    excluded_by_program_area: dict[str, int] = field(default_factory=dict)
    in_scope: int = 0
    duplicate_keys: dict[str, int] = field(default_factory=dict)
    normalize_failed: int = 0
    normalize_errors: list[str] = field(default_factory=list)
    link_check: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    would_upsert: int = 0
    upserted: int = 0
    upsert_chunk_failed: int = 0
    errors: list[str] = field(default_factory=list)


def run(*, dry_run: bool, base_url: str, service_key: str, api_user: str, api_key: str,
        fiscal_years: list[str], program_areas: list[str] | None = None,
        page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES,
        chunk_size: int = UPSERT_CHUNK_SIZE, timeout: int = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES, sort: str = "", sortorder: str = "",
        http_post: HttpPost | None = None, poster: Poster | None = None,
        link_loader: LinkLoader | None = None) -> tuple[CitationsReport, int]:
    http_post = http_post or ddapi_post
    poster = poster or fsa._post_rows
    link_loader = link_loader or load_classified_inspection_ids
    areas = list(program_areas if program_areas is not None else DEFAULT_PROGRAM_AREAS)

    report = CitationsReport(dry_run=dry_run, fiscal_years=list(fiscal_years),
                             program_areas=areas)
    fetch = fetch_all_pages(api_user=api_user, api_key=api_key, http_post=http_post,
                            fiscal_years=fiscal_years, program_areas=areas,
                            page_size=page_size, max_pages=max_pages, timeout=timeout,
                            retries=retries, sort=sort, sortorder=sortorder)
    report.pages_fetched = fetch.pages_fetched
    report.truncated = fetch.truncated
    report.server_total = fetch.server_total
    if fetch.error:
        report.errors.append(f"fetch_failed:{fetch.error}")
        log("ERROR", f"DDAPI citations 수집 실패: {fetch.error}")
        return report, 2
    if fetch.truncated:
        report.errors.append(f"truncated:max_pages={max_pages}_exhausted_with_full_last_page")
        log("ERROR", f"페이지 상한({max_pages}) 소진 -- --max-pages 를 늘려 재실행하라")
        return report, 2

    report.received_total = len(fetch.rows)
    if fetch.server_total is not None and fetch.server_total != report.received_total:
        report.errors.append(
            f"server_total_mismatch:server={fetch.server_total}_received={report.received_total}")
        log("ERROR", f"서버 총수 {fetch.server_total} != 수신 {report.received_total} -- "
                     "페이징이 행을 잃었거나 겹쳤다(조용히 싣지 않는다)")
        return report, 2

    included, excluded = split_by_program_area(fetch.rows, areas)
    report.excluded_by_program_area = excluded
    report.in_scope = len(included)
    log("INFO", f"수신 {report.received_total}건 -- 범위 {areas} {report.in_scope}건 / "
                f"제외 {excluded}")

    report.duplicate_keys = find_duplicate_keys(included)
    if report.duplicate_keys:
        log("WARN", f"(InspectionID, CitationID) 중복 {len(report.duplicate_keys)}종")

    payloads: list[dict[str, Any]] = []
    for row in included:
        payload, err = normalize_row(row, areas)
        if err:
            report.normalize_failed += 1
            report.normalize_errors.append(
                f"{_s(row.get('InspectionID')) or '?'}/{_s(row.get('CitationID')) or '?'}:{err}")
            continue
        payloads.append(payload)

    linked_ids, link_err = link_loader(base_url, service_key)
    report.link_check = link_err or f"ok:{len(linked_ids or ())}"
    report.summary = summarize(payloads, linked_ids)

    if dry_run:
        report.would_upsert = len(payloads)
        log("INFO", f"[DRY] upsert 대상 {len(payloads)}건 (실적재 없음)")
    else:
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i:i + chunk_size]
            status, _rows, err = poster(base_url, service_key, TABLE, chunk, ON_CONFLICT,
                                        resolution=UPSERT_RESOLUTION)
            if err:
                report.upsert_chunk_failed += 1
                report.errors.append(
                    f"upsert_chunk_failed(start={i},size={len(chunk)}):status={status}:{err}")
                continue
            report.upserted += len(chunk)
        log("INFO", f"적재 {report.upserted}건 / 청크실패 {report.upsert_chunk_failed}")

    # ★요청한 ProgramArea 마다 0건 하한 -- 라벨 가정이 틀렸거나 응답이 비었다는 신호.
    #   (classifications 의 zero_gmp_rows 와 같은 이유: 0건 초록은 알람을 스스로 끈다.)
    seen = Counter(p["program_area"] for p in payloads)
    empty = [a for a in areas if seen.get(a, 0) == 0]
    if empty:
        report.errors.append(
            f"zero_rows_for_program_area:{empty}:실제 라벨은 excluded_by_program_area 참고"
            f"({dict(excluded)})")
        log("ERROR", f"ProgramArea {empty} 0건 -- 라벨이 다를 수 있다: {excluded}")
        return report, 2

    exit_code = 1 if (report.normalize_failed or report.upsert_chunk_failed
                      or report.duplicate_keys) else 0
    return report, exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FDA DDAPI inspections_citations(실사 지적 조항) 수집 -- Supabase 직행. "
        "기본 dry-run(DDAPI 는 호출, 적재 생략).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                      help="DDAPI 는 호출하되 Supabase 적재는 생략한다(기본값).")
    mode.add_argument("--apply", dest="dry_run", action="store_false",
                      help="실제로 Supabase 에 적재한다.")
    p.add_argument("--fiscal-years", type=int, default=DEFAULT_FISCAL_YEARS,
                   help=f"최근 회계연도 수(기본 {DEFAULT_FISCAL_YEARS}, classifications 와 같은 창).")
    p.add_argument("--program-areas", default=",".join(DEFAULT_PROGRAM_AREAS),
                   help="쉼표로 구분한 ProgramArea(기본 Drugs,Biologics). 133 CHECK 밖 값은 적재 실패.")
    p.add_argument("--page-size", type=int, default=PAGE_SIZE)
    p.add_argument("--max-pages", type=int, default=MAX_PAGES)
    p.add_argument("--chunk-size", type=int, default=UPSERT_CHUNK_SIZE)
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--sort", default="",
                   help="정렬 열(기본 빈 값 = 서버 기본키 정렬). 진단용.")
    p.add_argument("--sortorder", default="")
    p.add_argument("--api-user", help="미지정 시 $FDA_API_USER")
    p.add_argument("--api-key", help="미지정 시 $FDA_DDAPI_KEY")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    p.add_argument("--output", help="JSON 리포트를 이 경로에도 기록.")
    return p


def main(argv: list[str] | None = None) -> int:
    # ★stdout 인코딩 가드 -- Windows cp949 콘솔에서 한글·em-dash 가 깨지거나 죽지 않게.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    args = build_arg_parser().parse_args(argv)

    api_user = (args.api_user or os.environ.get("FDA_API_USER") or "").strip()
    api_key = (args.api_key or os.environ.get("FDA_DDAPI_KEY") or "").strip()
    if not (api_user and api_key):
        print("collect_fda_citations: --api-user/--api-key 또는 $FDA_API_USER/$FDA_DDAPI_KEY "
              "필요(dry-run 도 실 API 를 호출한다)", file=sys.stderr)
        return 2
    base = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not args.dry_run and not (base and key):
        print("collect_fda_citations: --apply 에는 $SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요",
              file=sys.stderr)
        return 2
    areas = [a.strip() for a in args.program_areas.split(",") if a.strip()]
    if not areas:
        print("collect_fda_citations: --program-areas 가 비었다", file=sys.stderr)
        return 2

    report, exit_code = run(
        dry_run=args.dry_run, base_url=base, service_key=key,
        api_user=api_user, api_key=api_key,
        fiscal_years=fiscal_year_window(date.today(), args.fiscal_years),
        program_areas=areas, page_size=args.page_size, max_pages=args.max_pages,
        chunk_size=args.chunk_size, timeout=args.timeout, retries=args.retries,
        sort=args.sort, sortorder=args.sortorder,
    )
    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
