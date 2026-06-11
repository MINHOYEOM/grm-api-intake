#!/usr/bin/env python3
"""GRM FDA 483 / EIR Collector — WHY-1 #3 (가장 깊은 결함 원본).

ENABLE_FDA_483=true 또는 --sources fda483 일 때 collect_intake.main() 에서 호출된다.

데이터 소스 (probe 2026-06-11 라이브 재확인):
  FDA OII FOIA Electronic Reading Room 의 **공식 HTML 표**(서버사이드 렌더, 차단 없음).
    https://www.fda.gov/about-fda/office-inspections-and-investigations/oii-foia-electronic-reading-room
  컬럼: Record Date · Company Name · FEI Number · Record Type · State · Country ·
        Establishment Type · Publish Date · Excerpt.
  - Record Type 셀에 483 PDF 링크 <a href="…/media/<id>/download">483</a> — <id> = 안정 dedup 키.
  - State 와 Country 는 **별도 컬럼**: 해외 소재국은 Country 에(예: India), 미국 국내 행은
    State 채움·Country 공란.
  - DataTables JSON(datatables-json/ora-foia-reading.json)은 간헐 사망(빈 응답)이 관측돼
    신뢰 불가 → **HTML 단독 경로**. XLSX 내보내기는 openpyxl 신규 의존이라 사용 금지.
  - HTML 표는 최신 ~10행만 노출(DataTables 클라이언트 페이지네이션, 서버 페이지 파라미터
    없음). 저빈도 소스 + Publish Date 윈도우라 초기 rollout 에 충분. 단, 표 행이 전부
    in-window(=오래된 경계 미도달)면 절단 의심 health warning 으로 표면화(누락 침묵 방지).

수집 흐름:
  HTML fetch → 표 파싱(FDA WL _FDAWLTableParser 재사용) → Record Type ∈ {483, EIR} 필터 →
  Publish Date 윈도우 필터(지연 공개형 → MFDS_ENFORCEMENT_WINDOW_DAYS 재사용·정렬 비의존) →
  노이즈/관련성 게이트(수의 드롭·기기/식품 드롭) → 건별 483 PDF 결함 excerpt(P6
  _extract_pdf_text 재사용·최신 N건 cap·graceful) → IntakeItem.

설계 역할:
  - 483 = Tier 3(미시정 시 WL/집행으로 이어지는 선행 신호), EIR = Tier 2(무균 시설/신호는
    Tier 3 floor). distributor-only(제조 아님)는 드롭 대신 한 단계 하향(§4 과필터 금지).
  - Site Country = Country 컬럼(해외, 예: India). 공란+State 있음(미국 국내) → "United States".
    State(주)는 절대 Site Country 에 넣지 않는다(raw_payload['state'] 별도 보존).
  - Evidence = B: excerpt 는 prose_input(W5/W6/W7)만 보강하고 W3 인용(Evidence A) 승격은
    WHOPIR·WL·483 통합 별도 게이트로 보류(#1+#2 와 동일 정책).
  - excerpt 실패(fetch/암호화/스캔본/앵커 미스)는 graceful — 키 미기록·항목은 메타 카드로
    유지·LAST_HEALTH 경고. 수집 전체 실패 금지.
  - 일일/주간 0건은 정상(저빈도). 표 미발견(0행)·필터 전건 탈락만 error(구조 변경 의심).
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

from grm_common import http_get_bytes, http_get_html, log
from collect_intake import (
    IntakeItem,
    SOURCE_FDA_483,
    SRC_TYPE_OFFICIAL_PAGE,
    STERILE_BIO_TIER3_FLOOR,
    QA_HARD_EXCLUDE_TERMS,
    compute_relevance,
    _kw_any,
    _within_window,
    _FDAWLTableParser,
)


# 데이터 소스 (probe 2026-06-11 라이브 재확인 — HTML 단독, JSON 경로 폐기)
OII_READING_ROOM_URL = (
    "https://www.fda.gov/about-fda/office-inspections-and-investigations/"
    "oii-foia-electronic-reading-room"
)
FDA_MEDIA_BASE = "https://www.fda.gov"

# Record Type — 결함 본문을 담은 두 타입만(나머지 Response/Consent Decree/Recall 등 제외).
RECORD_TYPE_483 = "483"
RECORD_TYPE_EIR = "Establishment Inspection Report (EIR)"

TYPE_FDA_483 = "483"        # type_or_class(카드 분류) — 483
TYPE_FDA_EIR = "EIR"        # type_or_class — EIR
LANGUAGE_EN = "EN"
REGION_FDA = "USA (FDA)"

HTTP_RETRIES = 3
FDA_483_HTML_TIMEOUT = 30

# 표 컬럼 인덱스(probe 채록 — 9컬럼 고정 순서).
_COL_RECORD_DATE = 0
_COL_COMPANY = 1
_COL_FEI = 2
_COL_RECORD_TYPE = 3        # 셀에 /media/<id>/download href
_COL_STATE = 4
_COL_COUNTRY = 5
_COL_ESTABLISHMENT = 6
_COL_PUBLISH_DATE = 7
_MIN_COLS = 8              # Excerpt(8)는 선택 — 8컬럼이면 충분

# WHY-1 #3: 483 PDF 결함 excerpt. P6(MFDS GMP)의 검증된 PDF 텍스트 엔진(_extract_pdf_text)을
# 재사용하고, 483 특유 관찰사항 앵커만 새로 둔다. 비용·예의: per-item timeout/delay + 최신 N건 cap.
FDA483_EXCERPT_MAX_CHARS = 1500
FDA483_EXCERPT_FETCH_TIMEOUT = 20
FDA483_EXCERPT_DELAY_SECONDS = 0.5
FDA483_EXCERPT_MAX_ITEMS = 40          # fetch 비용 상한(윈도우 내 newest-first → 최신 N건 우선)
# 표지/머리말을 건너뛰고 관찰사항(findings) 구간부터 잘라내기 위한 영문 앵커(우선순위 순).
# 483 PDF 는 [표지(주소·FEI·실사일) → 보일러플레이트 → "DURING AN INSPECTION OF YOUR FIRM …
# I/WE OBSERVED:" → OBSERVATION 1 …] 구조. 인용보다 LLM 컨텍스트("왜")용으로 결함 구간 우선.
_FDA483_EXCERPT_PATTERNS = (
    r"observation\s+1\b",
    r"during\s+an\s+inspection\s+of\s+your\s+(?:firm|facility|establishment)",
    r"this\s+document\s+lists\s+observations",
    r"\bobservations?\b",
    r"specifically,",
)

# excerpt·절단 관측용(dry-run 검증·운영 health). collect_who.LAST_HEALTH 패턴.
LAST_HEALTH: dict[str, Any] = {}

_MEDIA_RE = re.compile(r"/media/(\d+)/download")
_MDY_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _parse_mdy(raw: str) -> str:
    """MM/DD/YYYY → ISO(YYYY-MM-DD). 실패 시 ''."""
    m = _MDY_RE.search(raw or "")
    if not m:
        return ""
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return ""


def _cell(cols: list[str], i: int) -> tuple[str, str]:
    """_FDAWLTableParser 셀('text|HREF:href') → (text, href). 범위 밖이면 ('', '')."""
    if i >= len(cols):
        return "", ""
    raw = cols[i]
    if "|HREF:" in raw:
        text, href = raw.split("|HREF:", 1)
        return text.strip(), href.strip()
    return raw.strip(), ""


def _norm_record_type(text: str) -> str:
    """셀 텍스트 → 정규화 Record Type({483, EIR}) 또는 ''(비대상).

    '483' 정확일치(‘Amended 483’·‘483 Response’ 제외), EIR 은 'Establishment Inspection
    Report' 또는 \\bEIR\\b 포함.
    """
    t = (text or "").strip()
    if t == RECORD_TYPE_483:
        return RECORD_TYPE_483
    low = t.lower()
    if "establishment inspection report" in low or re.search(r"\beir\b", low):
        return RECORD_TYPE_EIR
    return ""


def _pdf_url(media_id: str) -> str:
    return f"{FDA_MEDIA_BASE}/media/{media_id}/download" if media_id else ""


def _parse_rows(html_text: str) -> tuple[list[dict[str, str]], int]:
    """HTML 표 → 정규화 행 dict 리스트(483/EIR 만) + 파싱된 데이터행 총수.

    데이터행 총수는 절단/구조변경 sentinel 용(헤더 제외, len>=_MIN_COLS 인 행).
    """
    parser = _FDAWLTableParser()
    parser.feed(html_text)
    rows: list[dict[str, str]] = []
    data_row_count = 0
    for raw_row in parser.rows:
        cols = raw_row.get("_cols", [])
        if len(cols) < _MIN_COLS:
            continue
        rtype_text, href = _cell(cols, _COL_RECORD_TYPE)
        date_text, _ = _cell(cols, _COL_RECORD_DATE)
        # 헤더 행 배제: Record Date 셀이 날짜 패턴이 아니면 헤더/비데이터.
        if not _MDY_RE.search(date_text):
            continue
        data_row_count += 1
        record_type = _norm_record_type(rtype_text)
        if not record_type:
            continue
        m = _MEDIA_RE.search(href)
        media_id = m.group(1) if m else ""
        if not media_id:
            continue
        company, _ = _cell(cols, _COL_COMPANY)
        fei, _ = _cell(cols, _COL_FEI)
        state, _ = _cell(cols, _COL_STATE)
        country, _ = _cell(cols, _COL_COUNTRY)
        establishment, _ = _cell(cols, _COL_ESTABLISHMENT)
        publish, _ = _cell(cols, _COL_PUBLISH_DATE)
        rows.append({
            "record_date": date_text,
            "company": company,
            "fei": fei,
            "record_type": record_type,
            "media_id": media_id,
            "state": state,
            "country": country,
            "establishment_type": establishment,
            "publish_date": publish,
        })
    return rows, data_row_count


def _extract_fda483_excerpt(text: str) -> str:
    """483 PDF 평탄화 텍스트 → 영문 관찰사항(findings) 구간 excerpt(없으면 '').

    표지/보일러플레이트가 아니라 결함(관찰사항)을 카드 컨텍스트("왜")로 올리기 위한 추출.
    앵커가 하나도 없으면 ''(빈 PDF/스캔본/구조 이질 → 호출부가 키 미기록·메타 카드 유지).
    WHOPIR 추출기와 달리 앞부분 폴백을 하지 않는다 — 483 표지는 주소·FEI 보일러플레이트라
    findings 가 없으면 LLM 에 줄 "왜"가 없기 때문.
    """
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return ""
    for pat in _FDA483_EXCERPT_PATTERNS:
        m = re.search(pat, compact, re.I)
        if m:
            return compact[m.start():][:FDA483_EXCERPT_MAX_CHARS].strip()
    return ""


def _fetch_fda483_excerpt(pdf_url: str) -> tuple[str, str]:
    """483 PDF fetch → 영문 관찰사항 excerpt. 반환 (excerpt, status).

    status: 'ok' | 'no-excerpt' | 'fetch-fail:…' | PDF 엔진 status
    (pdf-encrypted/scan-no-text/pdf-parse-fail:…/pdf-parser-missing). 실패 시 excerpt=''
    → 호출부가 raw_payload 에 키를 쓰지 않고 항목은 메타 카드로 유지(graceful degrade).
    P6 PDF 엔진(_extract_pdf_text) 재사용 — fetch 는 grm_common.http_get_bytes(공용 클라이언트).
    """
    try:
        from collect_mfds_gmp_inspection import _extract_pdf_text
    except Exception as e:  # noqa: BLE001 — 임포트 실패도 graceful(키 미기록)
        return "", f"engine-missing:{type(e).__name__}"
    try:
        data = http_get_bytes(
            pdf_url, timeout=FDA483_EXCERPT_FETCH_TIMEOUT, retries=HTTP_RETRIES,
            headers={"Accept": "application/pdf"}, label="FDA 483 PDF",
        )
    except RuntimeError as e:
        return "", f"fetch-fail:{str(e)[:120]}"
    text, status = _extract_pdf_text(data)
    if not text:
        return "", status
    excerpt = _extract_fda483_excerpt(text)
    if not excerpt:
        return "", "no-excerpt"
    return excerpt, "ok"


def _signal_tier(record_type: str, establishment_type: str, excerpt: str) -> str:
    """483 = Tier 3, EIR = Tier 2. 무균 시설/신호는 Tier 3 floor, distributor-only 는 하향(§4)."""
    et = (establishment_type or "").lower()
    blob = f"{et} {excerpt}".lower()
    if "sterile" in blob or _kw_any(blob, STERILE_BIO_TIER3_FLOOR):
        return "Tier 3"            # 무균 시설/치명 신호 → 483/EIR 무관 Tier 3 floor
    base = "Tier 3" if record_type == RECORD_TYPE_483 else "Tier 2"
    # 관련성 낮은 distributor-only(제조 아님)는 드롭이 아니라 한 단계 하향(§4 과필터 금지).
    if "distributor" in et and "manufactur" not in et:
        base = {"Tier 3": "Tier 2", "Tier 2": "Tier 1"}.get(base, base)
    return base


def _site_country(country: str, state: str) -> str:
    """Site Country(소재국) 매핑. Country 우선(해외), 공란+State(미국 국내) → 'United States'.

    State(주)는 절대 Site Country 에 넣지 않는다 — GRM 의 Site Country 의미(소재국)와 어긋남.
    """
    if country:
        return country
    if state:
        return "United States"
    return ""


def _to_item(nrow: dict[str, str], excerpt: str) -> IntakeItem | None:
    """정규화 행(+excerpt) → IntakeItem. 수의/기기/식품 도메인은 None(드롭)."""
    record_type = nrow["record_type"]
    media_id = nrow["media_id"]
    company = nrow["company"]
    fei = nrow["fei"]
    state = nrow["state"]
    country = nrow["country"]
    establishment_type = nrow["establishment_type"]
    record_date = nrow["record_date"]                    # 실사일(원문 MM/DD/YYYY 유지)
    publish_iso = _parse_mdy(nrow["publish_date"])

    # 노이즈/관련성 게이트 — gate_blob 으로 도메인 판정.
    gate_blob = " ".join([company, establishment_type, country, record_type, excerpt])
    # 수의/동물용은 인체 의약품 밖 → 하드 드롭(타 수집기와 동일 정책).
    if _kw_any(gate_blob.lower(), QA_HARD_EXCLUDE_TERMS):
        return None
    relevance = compute_relevance(company, establishment_type, record_type, excerpt)
    # 기기/식품/화장품 도메인(compute_relevance=Unrelated)은 QA 제약 범위 밖 → 드롭.
    # (distributor·compounding 은 Unrelated 가 아니라 Pending/Possible → 보존·Tier 하향)
    if relevance == "Unrelated":
        return None
    if relevance == "Pending":
        relevance = "Possible"     # 483/EIR 은 제조 실사 맥락 → 보수적으로 보존

    tier = _signal_tier(record_type, establishment_type, excerpt)
    type_or_class = TYPE_FDA_483 if record_type == RECORD_TYPE_483 else TYPE_FDA_EIR
    pdf_url = _pdf_url(media_id)
    site_country = _site_country(country, state)

    raw_payload: dict[str, Any] = {
        "channel": "fda-483",
        "firm": company,
        "fei_number": fei,
        "record_type": record_type,
        "establishment_type": establishment_type,
        "record_date": record_date,
        "publish_date": nrow["publish_date"],
        "country": country,
        "site_state": state,          # 미국 주(소재국 아님 — Site Country 와 분리)
        "media_id": media_id,
        "pdf_url": pdf_url,
        # compute_modality 가 시설유형의 'drug' 단서를 보도록 product_type 으로 정규화.
        "product_type": establishment_type,
    }
    if excerpt:
        raw_payload["fda483_excerpt"] = excerpt

    # Modality 는 insert 시 notion_create_page 가 raw_payload(product_type=시설유형)+headline/
    # body 로 compute_modality 한다(IntakeItem 에 저장 필드 없음 — 타 수집기와 동일). 시설유형의
    # 'Drug Manufacturer' → Chemical, 'Outsourcing Facility' → Other 등으로 폴백된다.
    label = "FDA 483" if record_type == RECORD_TYPE_483 else "FDA EIR"
    locale = country or (f"{state}, United States" if state else "")
    body = (
        f"FDA {record_type} — OII FOIA Electronic Reading Room 공개 실사 기록.\n"
        f"제조소/업체: {company or '원문 미기재'}"
        + (f" (FEI {fei})" if fei else "")
        + (f"\n시설 유형: {establishment_type}" if establishment_type else "")
        + (f"\n소재: {locale}" if locale else "")
        + f"\n출처: {OII_READING_ROOM_URL}"
    )

    return IntakeItem(
        source=SOURCE_FDA_483,
        document_id=f"fda483-{media_id}",            # media id 안정 → dedup
        date_iso=publish_iso,                        # 공개일(윈도우·카드 발행일)
        headline=f"[{label}] {company or media_id}"[:240],
        official_url=pdf_url,                        # 건별 483 PDF (per-item L1)
        type_or_class=type_or_class,
        firm=company[:200],
        body=body,
        api_query=OII_READING_ROOM_URL,
        qa_relevance=relevance,
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_PAGE,
        signal_tier=tier,
        raw_payload=raw_payload,
        source_url=OII_READING_ROOM_URL,
        site_country=site_country,                   # Country(해외) 또는 'United States'
        language=LANGUAGE_EN,
        region_jurisdiction=REGION_FDA,
    )


def collect_fda_483(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA 483/EIR 수집 진입점. (items, error_msg).

    - HTML fetch 실패 → error(transient 마커는 health 단계에서 warning 강등).
    - 표 미발견(데이터행 0) → 구조/렌더 변경 의심 error(침묵 0건 금지).
    - 데이터행은 있으나 483/EIR 0건 → 정상(빈 리스트, 저빈도·newest 페이지 구성).
    - 윈도우 내 0건 → 정상(빈 리스트·error 없음).
    - 표가 전부 in-window(오래된 경계 미도달) → 절단 의심(LAST_HEALTH·warning, 누락 침묵 방지).
    - PDF excerpt 실패는 graceful(키 미기록·메타 카드 유지·LAST_HEALTH 경고).
    """
    global LAST_HEALTH
    excerpt_health: dict[str, Any] = {
        "attempted": 0, "ok": 0, "failed": 0, "capped": False, "warnings": [],
    }
    LAST_HEALTH = {"fda483_excerpt": excerpt_health, "table_truncated": False}

    log("INFO", f"FDA 483/EIR 수집: {OII_READING_ROOM_URL}")
    try:
        html_text = http_get_html(OII_READING_ROOM_URL, timeout=FDA_483_HTML_TIMEOUT,
                                  retries=HTTP_RETRIES, label="FDA 483")
    except Exception as e:  # noqa: BLE001
        return [], f"FDA 483 HTML 수집 실패: {e}"

    keep_rows, data_row_count = _parse_rows(html_text)
    if data_row_count == 0:
        # 표가 아예 파싱되지 않음 = 구조/렌더 변경(침묵 0건 금지).
        return [], ("FDA 483 표 미발견(데이터행 0) — HTML 구조/렌더 변경 의심(수동 확인 필요)")
    if not keep_rows:
        # 데이터행은 있으나 newest 페이지에 483/EIR 가 없음 = 저빈도 정상(드물지만 가능).
        log("INFO", f"FDA 483/EIR 0건 — 표 데이터행 {data_row_count}개 중 483/EIR 없음(정상)")
        return [], None

    start_iso = start.isoformat()
    in_window = [r for r in keep_rows
                 if _within_window(_parse_mdy(r["publish_date"]), start, end)]
    # 절단 의심: HTML 은 최신 ~10행만 노출 → 오래된 경계(start 이전 publish)를 한 번도 못 봤고
    # in-window 행이 있으면 더 오래된 in-window 행이 가려졌을 수 있다(누락 침묵 방지 신호).
    saw_older = any(
        _parse_mdy(r["publish_date"]) and _parse_mdy(r["publish_date"]) < start_iso
        for r in keep_rows
    )
    truncated = bool(in_window) and not saw_older
    # 최신 N건 excerpt cap 위해 publish desc 정렬(파싱 순서 비의존 — B2 교훈).
    in_window.sort(key=lambda r: _parse_mdy(r["publish_date"]), reverse=True)

    items: list[IntakeItem] = []
    seen: set[str] = set()
    for nrow in in_window:
        media_id = nrow["media_id"]
        if not media_id or media_id in seen:
            continue
        seen.add(media_id)

        # 483 PDF 결함 excerpt(cap 내 시도). 실패는 키 미기록 + warning 누적(메타 카드 유지).
        excerpt = ""
        pdf_url = _pdf_url(media_id)
        if pdf_url and not excerpt_health["capped"]:
            if excerpt_health["attempted"] >= FDA483_EXCERPT_MAX_ITEMS:
                excerpt_health["capped"] = True
            else:
                excerpt_health["attempted"] += 1
                if FDA483_EXCERPT_DELAY_SECONDS:
                    time.sleep(FDA483_EXCERPT_DELAY_SECONDS)
                excerpt, status = _fetch_fda483_excerpt(pdf_url)
                if excerpt:
                    excerpt_health["ok"] += 1
                else:
                    excerpt_health["failed"] += 1
                    warn = f"FDA 483 excerpt 실패({status}): {pdf_url}"
                    excerpt_health["warnings"].append(warn)
                    log("WARN", warn + " — 메타 카드로 유지(manual_review)")

        item = _to_item(nrow, excerpt)   # None = 수의/기기/식품 도메인 드롭
        if item is not None:             # dedup 은 위 media_id seen 으로 보장(doc_id=fda483-<id>)
            items.append(item)

    LAST_HEALTH = {"fda483_excerpt": excerpt_health, "table_truncated": truncated}
    if excerpt_health["capped"]:
        log("WARN", f"FDA 483 excerpt cap({FDA483_EXCERPT_MAX_ITEMS}) 도달 — "
                    "나머지 항목은 excerpt 없이 메타 카드로 유지")
    if truncated:
        log("WARN", "FDA 483 표 절단 의심 — 표시 행이 전부 윈도우 내(오래된 경계 미도달). "
                    "더 오래된 in-window 483/EIR 가 가려졌을 수 있음(수동 확인 권장)")
    log("INFO", f"FDA 483/EIR 완료: {len(items)}건 (윈도우내 후보 {len(in_window)}, "
                f"483/EIR 파싱 {len(keep_rows)}, 데이터행 {data_row_count}) · excerpt "
                f"attempted={excerpt_health['attempted']} ok={excerpt_health['ok']} "
                f"failed={excerpt_health['failed']} · truncated={truncated}")
    return items, None
