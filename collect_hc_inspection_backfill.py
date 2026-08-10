#!/usr/bin/env python3
"""Health Canada GMP 실사 리포트카드 딥 백필 — Notion 우회, Supabase 직행.

배경: 일일 intake 경로로 새 소스를 붙이려면 수동 레지스트리 여섯 곳(INTAKE_SOURCE_SPECS /
CollectionStats / _insert_items_map / card_scaffold._REGISTRY / grm_health.enabled_source_failures /
transient 코드)을 전부 고쳐야 하고, 하나라도 빠뜨리면 "조용히 죽거나 월요일 발행을 멈추거나"
둘 중 하나가 된다. 반면 이 백필은 `collect_eu_ncr_backfill.py` 와 같은 패턴으로 PostgREST 에
직행 적재하므로 그 여섯 곳을 **하나도 건드리지 않는다**. HC 실사는 월 ~37건이라 일일 유입이
급하지 않다 → 백필 먼저, 일일 편입은 별건.

원천 (전부 실측 확인 2026-08-11, 인증 불필요):
  목록  GET /gmp//controller/searchResult.ashx?...&pType=GMP&lang=en   (한 번에 전량·페이지네이션 없음)
  상세  GET /gmp//controller/fullReportCard.ashx?insNumber={n}&lang=en (문서당 1콜)
  ※ 경로의 이중 슬래시 `/gmp//controller/` 는 원천 그대로다. 정규화하지 마라.

재사용 배관(신규 배관 없음):
  - `grm_common.http_get_json` — 429 Retry-After·5xx/네트워크 지수 재시도.
  - `findings_supabase_append.append_intake_item_with_findings_to_supabase` — raw_signal +
    파생 findings 를 PostgREST 로 직접 적재(on_conflict 멱등). source 화이트리스트가 없어
    이 소스도 그대로 통과한다.
  - findings 추출은 `findings_extractors._from_hc_inspection` 이 이미 담당한다. 이 스크립트는
    그 게이트 키(`raw.hc_inspection_observations`)를 계약대로 채우는 것까지가 책임이다.

멱등성: raw_signal_id / finding_id 는 (source, document_id) 해시라 같은 문서를 두 번 돌려도
중복 0. 실패분만 재실행해 메우면 된다.

★적재 후 번역 필요(별도 단계): findings 공개 RLS 게이트가 finding_text_ko 또는
finding_language='KO' 를 요구하므로, 영어 원문만으로는 anon 검색에 안 잡힌다.

exit code 규약:
  0 = 정상
  1 = 건별 실패 있음(상세 fetch 실패 / append 실패 / 날짜 파싱 실패) — 리포트의
      `failed_ins_numbers` 로 재실행하면 멱등하게 메워진다.
  2 = 목록 수집 자체 실패 / 인자·자격증명 위반(0건 침묵 금지)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from collect_intake import IntakeItem, SRC_TYPE_OFFICIAL_API
from findings_supabase_append import append_intake_item_with_findings_to_supabase
from grm_common import http_get_json, log

SCHEMA_VERSION = "grm-hc-inspection-backfill/v1"

HC_HOST = "https://www.drug-inspections.canada.ca"
# ★이중 슬래시는 오타가 아니라 원천 그대로다(실측). 지우면 404 위험 — 손대지 마라.
HC_LIST_URL = f"{HC_HOST}/gmp//controller/searchResult.ashx"
HC_CARD_URL = f"{HC_HOST}/gmp//controller/fullReportCard.ashx"
HC_REPORT_CARD_PAGE = f"{HC_HOST}/gmp/fullReportCard-en.html"

# ★source 는 불가침 — raw_signal_id/finding_id 해시 입력이라 한 글자만 바뀌어도 기존
#   적재분과 영영 다른 문서가 된다(멱등 붕괴).
SOURCE_HC_INSPECTION = "Health Canada Inspection"
TYPE_HC_INSPECTION = "hc-inspection"
LANGUAGE_EN = "EN"
REGION_HC = "Canada (Health Canada)"
SITE_COUNTRY_CANADA = "Canada"

# 관찰이 확정되기 전 상태 — 지적으로 실을 수 없어 제외한다(reportCard=true 인데도 이 값이
# 오는 행이 실측으로 존재한다. rating 만 보고 걸러도 안 되고 reportCard 만 봐도 안 된다).
RATING_IN_PROGRESS = "Inspection in progress"
INS_TYPE_DOMESTIC = "GMP Domestic"

DEFAULT_FROM_DATE = "2021-01-01"
LIST_TIMEOUT_SECONDS = 180      # 5년 창 = 7,500행 · 실측 약 4MB
CARD_TIMEOUT_SECONDS = 60
CARD_RETRIES = 2
# 문서당 1콜 × 1,900문서다. 원천은 정부 단일 서버라 무지연 연타를 하지 않는다.
REQUEST_DELAY_SECONDS = 0.4

# 이 스크립트가 매개하는 http_get_json / append 는 테스트에서 인자로 치환된다.
Appender = Callable[..., Any]
HttpGetJson = Callable[..., Any]


# ─────────────────────────────────────────────────────────────────────────────
# .NET JSON 날짜
# ─────────────────────────────────────────────────────────────────────────────

# "/Date(1785427200000-0400)/" — .NET MicrosoftDateFormat 직렬화.
_DOTNET_DATE_RE = re.compile(r"^/Date\((-?\d+)([+-])(\d{2})(\d{2})\)/$")
_EPOCH_UTC = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def parse_dotnet_date(value: Any) -> str:
    """`/Date(1785427200000-0400)/` → "YYYY-MM-DD". 못 읽으면 ""(호출부가 표면화한다).

    ★오프셋을 **적용한다**(UTC 로 읽지 않는다). 밀리초는 UTC epoch 이고 뒤의 ±HHMM 은
    원본 DateTime 의 로컬 오프셋이라, 현지 벽시계 = UTC + 오프셋 이다. 실측(2021-01-01~
    2026-08-11 전량)은 여름 -0400 / 겨울 -0500 로 갈리되 현지 시각이 **전부 12:00** 이라
    두 해석이 같은 날짜를 준다 — 즉 지금은 어느 쪽을 골라도 티가 안 난다. 그래서 더더욱
    안전한 쪽으로 고정한다: 원천이 저녁 시각을 싣기 시작하면 UTC 해석은 날짜를 하루
    앞으로 밀고, 그 하루가 연도 경계에서 문서를 수집 창 **밖으로** 내보낸다(그러면 그
    문서는 오류 하나 없이 영영 안 들어온다).
    """
    if not isinstance(value, str):
        return ""
    m = _DOTNET_DATE_RE.match(value.strip())
    if not m:
        return ""
    millis, sign, hours, minutes = m.group(1), m.group(2), m.group(3), m.group(4)
    offset = dt.timedelta(hours=int(hours), minutes=int(minutes))
    if sign == "-":
        offset = -offset
    try:
        # fromtimestamp 대신 epoch + timedelta — Windows 에서 음수 epoch 이 터진다.
        local = _EPOCH_UTC + dt.timedelta(milliseconds=int(millis)) + offset
    except (OverflowError, ValueError):
        return ""
    return local.date().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# 비(非)사람용 제조소 이름 필터
# ─────────────────────────────────────────────────────────────────────────────
# 사람용 의약품 검색 DB 이므로 동물약·치과·기기·건강기능식품 제조소의 지적이 섞이면 품질이
# 떨어진다. ★그런데 HC 실사 목록에는 제품군을 말해주는 필드가 하나도 없다 — `country`·
# `activity`·`programType` 이 실측 전건 null 이라 남는 신호가 **업체명뿐**이다.
#
# ★이 필터가 부정확하다는 것을 알고 쓴다. 그래서 (a) `--include-veterinary` 로 통째로 끌 수
#   있고 (b) 리포트에 **무엇을 걸렀는지**(업체명 + 매칭 토큰 + 문서 수) 전량을 남긴다.
#   "몇 건 걸렀다"는 오분류를 절대 못 잡는다 — 걸린 이름을 사람이 읽어야 잡힌다.
#
# ★토큰은 좁게 잡는다. **덜 거른 문서는 눈에 보이지만 잘못 거른 문서는 조용히 사라진다.**
#   넓힐 뻔했다가 실측 반례로 막힌 토큰들(전부 사람용 의약품 제조소다):
#     "medical" → B. Braun Medical · ICU Medical · Fresenius Medical Care (주사제·수액)
#     "imaging" → Bracco Imaging · Lantheus Medical Imaging (조영제·방사성의약품)
#     "farm"    → Altea Farmaceutica SA (farmaceutica 오탐)
#     "agri"    → Agrisan Specialty Chemical & Pharmaceutical
#     "oxygen"/"gas" → 캐나다에서 의료용 가스는 '의약품'이다(MagGas Medical 등 제외 대상 아님)
#     "herbal"  → 생약 제제 제조소를 통째로 날릴 수 있어 상표(herbalife)로만 좁혔다
_NON_HUMAN_NAME_TOKENS: dict[str, tuple[str, ...]] = {
    "veterinary": (
        "veterinar", "veterinaire", "vétérinaire",
        "animal health", "animal hospital", "animal clinic", "animal nutrition",
    ),
    "dental": ("dental", "dentaire", "orthodontic"),
    # 앞의 셋만 실측에 걸렸고(Les Instruments Medicaux / MED-E-OX Medical Equipment /
    # Safecross First Aid) 나머지는 선제 방어다 — 기기 유통사가 새로 잡혀도 조용히
    # 사람용 지적에 섞이지 않게.
    "device": (
        "instruments medicaux", "instruments médicaux", "medical equipment", "first aid",
        "medical device", "surgical", "orthopaedic", "orthopedic", "prosthe",
    ),
    "nhp": ("natural health product", "nutraceutic", "homeopath", "naturopath"),
}

# 이름에 업종 단어가 아예 없는 상호들. 토큰만으로는 절대 안 걸린다(예: Vetoquinol 은
# "veterinar" 도 "animal health" 도 아니다) — 그래서 상호 조각을 따로 둔다.
_NON_HUMAN_FIRM_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "veterinary": (
        "vetoquinol", "zoetis", "elanco", "virbac", "merial",
        "bio agri mix", "huvepharma", "producers agri-supply", "trouw nutrition",
    ),
    "nhp": ("herbalife",),
}


def non_human_category(establishment_name: str) -> tuple[str, str]:
    """(카테고리, 매칭 토큰). 사람용으로 판정되면 ("", "").

    토큰층을 먼저 전부 본 뒤 상호층을 본다 — 순서는 결정론이면 충분하고(리포트에 근거를
    남기므로), 한 이름이 두 카테고리에 걸리는 경우는 먼저 잡히는 쪽으로 확정한다.
    """
    name = (establishment_name or "").strip().lower()
    if not name:
        return "", ""
    for category, tokens in _NON_HUMAN_NAME_TOKENS.items():
        for token in tokens:
            if token in name:
                return category, token
    for category, fragments in _NON_HUMAN_FIRM_FRAGMENTS.items():
        for fragment in fragments:
            if fragment in name:
                return category, fragment
    return "", ""


# ─────────────────────────────────────────────────────────────────────────────
# 원천 파싱
# ─────────────────────────────────────────────────────────────────────────────


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def list_params(start: dt.date, end: dt.date) -> dict[str, str]:
    """목록 API 쿼리. 빈 값 파라미터도 원천 URL 그대로 전부 실어 보낸다(서버가 폼 전량을
    기대하므로 '어차피 빈 값'이라고 빼지 않는다)."""
    return {
        "estName": "", "ref": "", "site": "", "rate": "", "term": "", "lic": "",
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "eType": "", "prov": "", "licNum": "", "act": "", "actCat": "", "cat": "",
        "pType": "GMP", "lang": "en",
    }


def is_target_row(row: dict[str, Any]) -> bool:
    """적재 대상 행인가. reportCard 가 있고 판정이 확정된 것만.

    ★두 조건이 서로를 대체하지 않는다 — 실측에 `reportCard=true` 이면서
    `ratingDesc="Inspection in progress"` 인 행이 있다(관찰이 아직 확정 전).
    """
    if not row.get("reportCard"):
        return False
    return _text(row.get("ratingDesc")) != RATING_IN_PROGRESS


def report_card_url(ins_number: Any) -> str:
    return f"{HC_REPORT_CARD_PAGE}?lang=en&insNumber={ins_number}"


def observations_from_card(card: dict[str, Any]) -> list[dict[str, str]]:
    """fullReportCard 의 `data[]` → 추출기 계약 `[{"no","regulation","summary"}]`.

    ★`summaryList`(배열)를 **공백 1칸으로 join 한 문자열**로 넣는 것이 계약이다
    (`findings_extractors._from_hc_inspection` 주석). 배열을 그대로 넘기면 추출기의
    `_compact` 가 `"['a', 'b']"` 라는 쓰레기 문자열을 만들어 내고 — 게이트도 통과하고
    건수도 정상이라 **아무도 모르는 조용한 오염**이 된다.

    ★관찰 1개(= data[] 원소 1개) = finding 1건이다. summaryList 의 항목마다 쪼개지
    않는다(실측 101문서: 관찰 4.67/문서 · 불릿 5.85/문서 — 창 전량 1,900문서 기준
    관찰 약 8,900건이 예상 findings 수와 맞는다).
    """
    out: list[dict[str, str]] = []
    for entry in card.get("data") or []:
        if not isinstance(entry, dict):
            continue
        raw_summary = entry.get("summaryList")
        if isinstance(raw_summary, list):
            summary = " ".join(_text(part) for part in raw_summary if _text(part))
        else:
            summary = _text(raw_summary)
        summary = " ".join(summary.split())          # 원천 요약에 개행·연속공백이 섞인다
        if not summary:
            continue                                  # 조항만 있고 내용 없는 행은 지적이 아니다
        out.append({
            "no": _text(entry.get("no")),
            "regulation": _text(entry.get("regulation")),
            "summary": summary,
        })
    return out


def _site_country(ins_type: str) -> str:
    """제조소 소재국. 원천이 말한 것만 옮긴다.

    ★"Canada 고정"은 틀린다 — 실측 1,900건 중 172건이 `insType="GMP Foreign"`(인도
    Telangana · 미국 TX 등)이고, 목록 API 의 `country` 는 **전건 null** 이라 해외 제조소의
    국가는 이 원천 어디에도 없다. 없는 값을 "Canada" 로 채우면 검색 DB 가 "캐나다 소재
    Hetero Labs" 라는 거짓 사실을 갖게 된다. Domestic 만 Canada 로 단정하고 Foreign 은
    빈 값(=미확인)으로 둔다. 판정 근거(`ins_type`)는 raw 에 그대로 남는다.
    """
    return SITE_COUNTRY_CANADA if ins_type == INS_TYPE_DOMESTIC else ""


def _signal_tier(rating: str) -> str:
    """비준수(NC)만 고신호. 백필은 브리프에 안 나가지만 findings row_json 에 남는다."""
    return "Tier 3" if rating.upper() == "NC" else "Tier 2"


def build_item(row: dict[str, Any], card: dict[str, Any], published_date: str) -> IntakeItem:
    """목록 행 + 리포트카드 → IntakeItem.

    상세(card)의 값이 목록(row)과 겹치는 항목은 상세를 우선한다(리포트카드가 그 문서의
    정본이다). 다만 city/province/licenseNumber 는 목록에만 있다.
    """
    # ★`insepctionNumber` 오타는 원천 그대로다. 고쳐 읽으면 KeyError 도 없이 0 이 된다.
    ins_number = _text(row.get("insepctionNumber")) or _text(card.get("insepctionNumber"))
    establishment = _text(card.get("establishmentName")) or _text(row.get("establishmentName"))
    rating = _text(card.get("rating")) or _text(row.get("rating"))
    rating_desc = _text(card.get("ratingDesc")) or _text(row.get("ratingDesc"))
    ins_type = _text(card.get("insType"))
    ins_sub_type = _text(card.get("insSubType"))
    reference_number = _text(card.get("referenceNumber")) or _text(row.get("referenceNumber"))
    city = _text(row.get("city"))
    province = _text(row.get("province"))
    license_number = _text(row.get("licenseNumber"))

    observations = observations_from_card(card)
    outcome_list = [_text(v) for v in (card.get("insOutcomeList") or []) if _text(v)]
    measure_list = [_text(v) for v in (card.get("measureTakenList") or []) if _text(v)]

    url = report_card_url(ins_number)

    body_parts = [
        f"실사 유형: {ins_type}" if ins_type else "",
        f"실사 세부유형: {ins_sub_type}" if ins_sub_type else "",
        f"판정: {rating_desc}" if rating_desc else "",
        f"소재: {', '.join(p for p in (city, province) if p)}" if (city or province) else "",
        ("실사 결과(Outcome): " + " / ".join(outcome_list)) if outcome_list else "",
        ("조치(Measures taken): " + " / ".join(measure_list)) if measure_list else "",
    ]

    raw_payload: dict[str, Any] = {
        "api": "Health Canada Drug Inspections (fullReportCard)",
        "ins_number": ins_number,
        "reference_number": reference_number,
        "establishment_name": establishment,
        "city": city,
        "province": province,
        "license_number": license_number,
        "rating": rating,
        "ratingDesc": rating_desc,
        "insType": ins_type,
        "insSubType": ins_sub_type,
        "ins_outcome_list": outcome_list,
        "measure_taken_list": measure_list,
        # ★추출기 게이트 키 — 이름·형태 불가침(findings_extractors._from_hc_inspection).
        "hc_inspection_observations": observations,
        "observation_count": len(observations),
        # 추출기가 evidence_url 로 최우선 채택하는 문서급 링크(_evidence_url 의 raw_keys).
        "report_card_url": url,
        "list_query_url": HC_LIST_URL,
        # site_country 를 왜 그렇게 정했는지 데이터에 남긴다(_site_country 주석 참조).
        "site_country_basis": "insType",
    }

    return IntakeItem(
        source=SOURCE_HC_INSPECTION,
        document_id=f"hc-insp-{ins_number}",
        date_iso=published_date,
        headline=f"{establishment} — Health Canada GMP inspection ({rating_desc})"[:240],
        official_url=url,
        type_or_class=TYPE_HC_INSPECTION,
        firm=establishment,
        body="\n".join(p for p in body_parts if p),
        api_query=HC_CARD_URL,
        source_url=url,
        qa_relevance="Likely",                 # GMP 실사 지적은 전부 관련
        source_type=SRC_TYPE_OFFICIAL_API,
        signal_tier=_signal_tier(rating),
        raw_payload=raw_payload,
        language=LANGUAGE_EN,
        region_jurisdiction=REGION_HC,
        site_country=_site_country(ins_type),
        evidence_candidate="A",                # 관찰 원문 인용 가능 → Evidence A
    )


# ─────────────────────────────────────────────────────────────────────────────
# 리포트 · 실행
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class BackfillReport:
    schema_version: str = SCHEMA_VERSION
    from_date: str = ""
    to_date: str = ""
    dry_run: bool = True
    include_veterinary: bool = False
    limit: int = 0
    listed: int = 0                 # 목록 API 가 돌려준 전체 행
    candidates: int = 0             # reportCard=true & 판정 확정
    excluded_non_human: int = 0
    # ★"몇 건"이 아니라 "무엇을" — 이름 기반 필터의 오분류는 이 목록을 읽어야만 잡힌다.
    excluded_firms: list[dict[str, Any]] = field(default_factory=list)
    key_missing: int = 0            # insepctionNumber 부재 = 문서키 없음
    date_unparsed: int = 0
    out_of_window: int = 0
    limited_out: int = 0            # --limit 로 이번 실행에서 건너뛴 문서
    fetched: int = 0                # 상세 fetch 성공
    card_failed: int = 0
    no_observation_docs: int = 0
    observations_total: int = 0
    appended: int = 0
    duplicate: int = 0
    partial: int = 0
    failed: int = 0
    # 재실행으로 메울 수 있게 실패한 insNumber 를 **전량** 남긴다(샘플 아님).
    failed_ins_numbers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    would_append: list[str] = field(default_factory=list)  # dry-run: 앞 20건 document_id


def _fetch_list(
    start: dt.date, end: dt.date, *, http_get: HttpGetJson, retries: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """목록 1콜. 실패는 침묵 0 금지 — 빈 리스트로 눙치지 않고 error 를 돌려준다."""
    try:
        data = http_get(HC_LIST_URL, params=list_params(start, end),
                        timeout=LIST_TIMEOUT_SECONDS, retries=retries)
    except Exception as e:  # noqa: BLE001 — 예외 타입/문자열이 곧 진단이다
        return [], f"목록 수집 실패: {type(e).__name__}: {e}"
    if not isinstance(data, dict):
        return [], f"목록 응답이 객체가 아님(구조 변경 의심): {type(data).__name__}"
    rows = data.get("data")
    if not isinstance(rows, list):
        return [], "목록 응답에 data 배열이 없음(구조 변경 의심 — 수동 확인 필요)"
    return [r for r in rows if isinstance(r, dict)], None


def _fetch_card(
    ins_number: str, *, http_get: HttpGetJson, retries: int,
) -> tuple[dict[str, Any], str | None]:
    try:
        card = http_get(HC_CARD_URL, params={"insNumber": ins_number, "lang": "en"},
                        timeout=CARD_TIMEOUT_SECONDS, retries=retries)
    except Exception as e:  # noqa: BLE001 — 건별 실패는 계속(재실행으로 메운다)
        return {}, f"{type(e).__name__}: {e}"
    if not isinstance(card, dict):
        return {}, f"리포트카드 응답이 객체가 아님: {type(card).__name__}"
    return card, None


def run(
    *,
    start: dt.date,
    end: dt.date,
    dry_run: bool,
    base_url: str,
    service_key: str,
    collected_at: str,
    limit: int = 0,
    include_veterinary: bool = False,
    request_delay: float = REQUEST_DELAY_SECONDS,
    retries: int = CARD_RETRIES,
    http_get: HttpGetJson | None = None,
    appender: Appender | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[BackfillReport, int]:
    # 지연 바인딩: 기본값을 def 시점이 아니라 호출 시점에 해석해 테스트가 인자로 갈아끼운다.
    http_get = http_get or http_get_json
    appender = appender or append_intake_item_with_findings_to_supabase
    sleep = sleep or time.sleep

    report = BackfillReport(
        from_date=start.isoformat(), to_date=end.isoformat(), dry_run=dry_run,
        include_veterinary=include_veterinary, limit=max(0, int(limit or 0)),
    )

    rows, err = _fetch_list(start, end, http_get=http_get, retries=retries)
    if err:
        report.errors.append(f"collect_failed:{err}")
        log("ERROR", f"HC 실사 백필 목록 수집 실패: {err}")
        return report, 2
    report.listed = len(rows)
    log("INFO", f"HC 실사 백필 목록 {len(rows)}행 (창 {start}~{end})")

    # ── 1단계: 네트워크 이전에 끝낼 수 있는 선별을 전부 끝낸다 ────────────────────
    # 상세는 문서당 1콜이라 여기서 거른 만큼 그대로 비용이다. 순서도 의미가 있다 —
    # 제외/날짜 판정을 상세 fetch 뒤로 미루면 버릴 문서에 콜을 다 쓴 뒤에 버리게 된다.
    excluded: dict[str, dict[str, Any]] = {}
    selected: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        if not is_target_row(row):
            continue
        report.candidates += 1
        firm = _text(row.get("establishmentName"))
        if not include_veterinary:
            category, token = non_human_category(firm)
            if category:
                report.excluded_non_human += 1
                bucket = excluded.setdefault(
                    firm, {"firm": firm, "category": category, "token": token, "documents": 0})
                bucket["documents"] += 1
                continue
        ins_number = _text(row.get("insepctionNumber"))
        if not ins_number:
            # ★빈 문서키를 그냥 통과시키면 document_id 가 전부 "hc-insp-" 로 같아져
            #   raw_signal_id 해시가 겹치고, 여러 문서가 **중복으로 집계되며 사라진다**
            #   (실패가 아니라 성공으로 보인다). 실측 7,543행은 전건 유일하지만, 그
            #   전제가 깨지는 순간 소리 없이 데이터가 줄어드는 자리라 막아둔다.
            report.key_missing += 1
            report.errors.append(
                f"ins_number_missing:{_text(row.get('referenceNumber')) or '?'}")
            continue
        published_date = parse_dotnet_date(row.get("inspectionStartDate"))
        if not published_date:
            # 날짜가 없으면 raw_signals.published_date required 로 어차피 막힌다.
            # 상세 콜을 쓰기 전에 여기서 시끄럽게 세운다(조용한 0건 금지).
            report.date_unparsed += 1
            report.failed_ins_numbers.append(ins_number)
            report.errors.append(
                f"date_unparsed({ins_number}):{_text(row.get('inspectionStartDate'))}")
            continue
        if not (start <= dt.date.fromisoformat(published_date) <= end):
            # 서버측 필터가 이미 창을 적용하지만, 우리 파싱과 어긋나면 여기서 드러나야 한다.
            report.out_of_window += 1
            continue
        selected.append((row, published_date))

    report.excluded_firms = sorted(excluded.values(), key=lambda e: e["firm"])
    if report.excluded_non_human:
        log("INFO", f"비사람용 제외 {report.excluded_non_human}건 / 업체 "
                    f"{len(report.excluded_firms)}곳 — 업체명 전량은 리포트 excluded_firms 참조")
    if report.key_missing:
        log("WARN", f"insepctionNumber 부재 {report.key_missing}건 — 문서키가 없어 건너뜀"
                    "(원천 구조 변경 의심·수동 확인 필요)")
    if report.date_unparsed:
        log("WARN", f"실사시작일 파싱 실패 {report.date_unparsed}건 — 형식 변경 의심(수동 확인 필요)")
    if report.out_of_window:
        log("WARN", f"창 밖 판정 {report.out_of_window}건 — 서버 필터와 우리 날짜 해석이 어긋난다")

    if report.limit and len(selected) > report.limit:
        report.limited_out = len(selected) - report.limit
        selected = selected[: report.limit]
        log("WARN", f"--limit {report.limit} 적용 — {report.limited_out}건은 이번 실행에서 제외"
                    "(전량 적재하려면 --limit 없이 재실행하라·멱등)")

    log("INFO", f"상세 대상 {len(selected)}건 (후보 {report.candidates}건 중)")

    # ── 2단계: 문서당 1콜 + 적재 ────────────────────────────────────────────────
    for index, (row, published_date) in enumerate(selected):
        ins_number = _text(row.get("insepctionNumber"))
        if index and request_delay > 0:
            sleep(request_delay)
        card, card_err = _fetch_card(ins_number, http_get=http_get, retries=retries)
        if card_err:
            report.card_failed += 1
            report.failed_ins_numbers.append(ins_number)
            report.errors.append(f"card_failed({ins_number}):{card_err}")
            log("WARN", f"리포트카드 실패 insNumber={ins_number}: {card_err}")
            continue
        report.fetched += 1

        item = build_item(row, card, published_date)
        observation_count = len(item.raw_payload.get("hc_inspection_observations") or [])
        report.observations_total += observation_count
        if not observation_count:
            # 관찰 0건 문서도 적재한다 — 안 넣으면 "아직 안 받았다"와 "받았는데 비어 있다"를
            # 구분할 방법이 사라진다. 대신 건수를 리포트로 표면화한다.
            report.no_observation_docs += 1

        if dry_run:
            if len(report.would_append) < 20:
                report.would_append.append(item.document_id)
            continue

        try:
            res = appender(base_url, service_key, item, collected_at=collected_at)
        except Exception as e:  # noqa: BLE001 — 건별 실패는 계속(멱등 재실행 가능)
            report.failed += 1
            report.failed_ins_numbers.append(ins_number)
            report.errors.append(f"append_raised({item.document_id}):{type(e).__name__}")
            log("WARN", f"append 예외 {item.document_id}: {type(e).__name__}")
            continue

        status = _text(getattr(res, "status", ""))
        res_errors = tuple(getattr(res, "errors", ()) or ())
        if status in ("error", "invalid"):
            report.failed += 1
            report.failed_ins_numbers.append(ins_number)
            report.errors.append(
                f"append_failed({item.document_id}):{status}:{'; '.join(res_errors)}")
            log("WARN", f"append 실패 {item.document_id}: {status}")
            continue
        if status == "duplicate":
            report.duplicate += 1
        elif status == "partial":
            report.partial += 1
            report.appended += 1
            if res_errors:
                report.errors.append(
                    f"append_partial({item.document_id}):{'; '.join(res_errors)}")
        else:  # inserted / raw_signal_inserted
            report.appended += 1

    if dry_run:
        log("INFO", f"[DRY] 상세 {report.fetched}건 · 관찰 {report.observations_total}건 "
                    f"(실적재 없음)")
    else:
        log("INFO", f"적재 {report.appended}건 · 중복 {report.duplicate}건 · "
                    f"부분 {report.partial}건 · 실패 {report.failed}건 · "
                    f"관찰 {report.observations_total}건")
    if report.no_observation_docs:
        log("WARN", f"관찰 0건 문서 {report.no_observation_docs}건 — 리포트카드에 data[] 가 "
                    "비어 있다(적재는 했다·findings 는 안 생긴다)")
    if report.card_failed:
        log("WARN", f"리포트카드 실패 {report.card_failed}건 — failed_ins_numbers 로 재실행하라(멱등)")

    exit_code = 1 if (report.failed or report.card_failed
                      or report.date_unparsed or report.key_missing) else 0
    return report, exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Health Canada GMP 실사 리포트카드 딥 백필 — Supabase 직행"
        "(Notion 미접촉·collect_eu_ncr_backfill 패턴). 넓은 실사시작일 창을 1회 수집·멱등 적재.",
    )
    p.add_argument("--from-date", default=DEFAULT_FROM_DATE,
                   help=f"실사시작일 창 시작(ISO). 기본 {DEFAULT_FROM_DATE}.")
    p.add_argument("--to-date", default="",
                   help="실사시작일 창 끝(ISO). 빈값이면 오늘.")
    # 적재 전 데이터 확인 원칙 — 기본이 dry-run 이고, 실적재는 --apply 로 명시해야 한다.
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                      help="수집만 하고 Supabase 적재는 생략한다(기본값).")
    mode.add_argument("--apply", dest="dry_run", action="store_false",
                      help="실제로 Supabase 에 적재한다(기본은 dry-run).")
    p.add_argument("--limit", type=int, default=0,
                   help="상세 fetch·적재 대상 문서 수 상한(0=무제한). 표본 확인용 — "
                        "상한을 걸면 나머지는 이번 실행에서 안 들어온다(리포트 limited_out).")
    p.add_argument("--include-veterinary", action="store_true", default=False,
                   help="비(非)사람용 이름 필터를 전부 끈다(수의·치과·기기·건강기능식품). "
                        "이름 기반이라 오분류가 있을 수 있어 켤 수 있게 둔다.")
    p.add_argument("--request-delay", type=float, default=REQUEST_DELAY_SECONDS,
                   help=f"상세 요청 간 sleep(초). 기본 {REQUEST_DELAY_SECONDS}.")
    p.add_argument("--retries", type=int, default=CARD_RETRIES,
                   help=f"HTTP 재시도 횟수. 기본 {CARD_RETRIES}.")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    p.add_argument("--output", help="JSON 리포트를 이 경로에도 기록.")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)

    base = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not args.dry_run and not (base and key):
        print("collect_hc_inspection_backfill: --supabase-url/--service-role-key 또는 "
              "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요(--apply 모드)", file=sys.stderr)
        return 2

    try:
        start = dt.date.fromisoformat(args.from_date)
        end = dt.date.fromisoformat(args.to_date) if args.to_date else dt.date.today()
    except ValueError as e:
        print(f"collect_hc_inspection_backfill: 날짜 파싱 실패: {e}", file=sys.stderr)
        return 2
    if start > end:
        print(f"collect_hc_inspection_backfill: --from-date({start}) 가 "
              f"--to-date({end}) 보다 늦다", file=sys.stderr)
        return 2

    # 실행 시각 1회만 캡처(리포트/collected_at 결정론 앵커).
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat()

    report, exit_code = run(
        start=start, end=end, dry_run=args.dry_run,
        base_url=base, service_key=key, collected_at=collected_at,
        limit=args.limit, include_veterinary=args.include_veterinary,
        request_delay=args.request_delay, retries=args.retries,
    )

    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return exit_code


__all__ = [
    "BackfillReport",
    "build_item",
    "is_target_row",
    "list_params",
    "non_human_category",
    "observations_from_card",
    "parse_dotnet_date",
    "report_card_url",
    "run",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
