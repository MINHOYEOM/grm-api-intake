#!/usr/bin/env python3
"""GRM FDA 483 / EIR Collector — WHY-1 #3 (가장 깊은 결함 원본).

ENABLE_FDA_483=true 또는 --sources fda483 일 때 collect_intake.main() 에서 호출된다.

데이터 소스 (Step 0 probe 2026-06-11 라이브 — 완전성 HOLD 보정):
  FDA OII FOIA Electronic Reading Room. 전수성을 보장하는 단일 소스가 없어 **JSON 전수 +
  HTML 폴백/보강 하이브리드**로 간다.
  - **전수 backbone = DataTables JSON** `https://www.fda.gov/datatables-json/ora-foia-reading.json`
    (≈3000 레코드 — 표를 렌더하는 FDA 자체 endpoint). Record Type·Publish Date·FEI·State·
    media 링크(field_foia_record_type_1 의 /media/<id>/download) 보존. **Country 컬럼은 없음.**
  - **HTML 표**(reading room 페이지)는 Record Date(실사일) 정렬 + 최신 ~10행만 노출 →
    "실사일은 오래됐지만 최근 공개된" 483(예: Intas·Dabur·Excel Vision)이 표 아래로 밀려
    Publish 윈도우 안인데도 누락(=완전성 미보장). 그래서 HTML 은 **전수 backbone 이 아니라**
    ① JSON 사망 시 폴백(부분·degrade 경고) ② Country 컬럼 보강(media_id→country)으로만 쓴다.
  - XLSX export(503/WAF)·전용 483 DB(404 dead)는 CI 비안정 → 탈락. media id 패턴
    https://www.fda.gov/media/<id>/download 는 안정(직접 합성).

수집 흐름:
  HTML fetch(best-effort: country 보강 map + 폴백 행) · JSON fetch(전수 backbone) →
  Record Type ∈ {483, EIR} 필터 → Publish Date 윈도우(전수 평가·정렬 비의존) → Country
  보강(HTML map) → 노이즈/관련성 게이트 → media id dedup → 건별 483 PDF 결함 excerpt
  (P6 _extract_pdf_text 재사용·최신 N건 cap·graceful) → IntakeItem.

설계 역할:
  - 전수성: JSON 이 윈도우 내 모든 483/EIR 를 준다(정렬·페이지 cap 비의존). JSON 사망 시
    HTML 폴백(최신 ~10행, 부분) + fda483-source-degraded warning(완전성 미보장 표면화·침묵 금지).
  - 483 = Tier 3, EIR = Tier 2(무균 시설/신호는 Tier 3 floor). distributor-only 는 하향(§4).
  - Site Country(HOLD②): Country(해외, HTML 보강) 우선 · 공란+State(미국) → "United States" ·
    둘 다 공란 → ""(미상). State(주)는 절대 Site Country 아님(raw site_state 분리).
  - Evidence = B(excerpt 는 prose_input 만 보강, W3 인용 승격 아님 — #1+#2 와 동일 정책).
  - excerpt/소스 실패는 graceful(키 미기록·메타 카드 유지·LAST_HEALTH 경고). 수집 전체 실패 금지.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

from grm_common import http_get_bytes, http_get_html, http_get_json, log
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


# 데이터 소스 (Step 0 probe 2026-06-11 — JSON 전수 + HTML 폴백/보강)
FDA_483_JSON_URL = "https://www.fda.gov/datatables-json/ora-foia-reading.json"
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
FDA_483_JSON_TIMEOUT = 60
FDA_483_HTML_TIMEOUT = 30

# HTML 표 컬럼 인덱스(probe 채록 — 9컬럼 고정 순서).
_COL_RECORD_DATE = 0
_COL_COMPANY = 1
_COL_FEI = 2
_COL_RECORD_TYPE = 3        # 셀에 /media/<id>/download href
_COL_STATE = 4
_COL_COUNTRY = 5
_COL_ESTABLISHMENT = 6
_COL_PUBLISH_DATE = 7
_MIN_COLS = 8

# WHY-1 #3: 483 PDF 결함 excerpt. P6(MFDS GMP)의 검증된 PDF 텍스트 엔진(_extract_pdf_text)을
# 재사용하고, 483 특유 관찰사항 앵커만 새로 둔다. 비용·예의: per-item timeout/delay + 최신 N건 cap.
FDA483_EXCERPT_MAX_CHARS = 1500
FDA483_EXCERPT_FETCH_TIMEOUT = 20
FDA483_EXCERPT_DELAY_SECONDS = 0.5
FDA483_EXCERPT_MAX_ITEMS = 40          # fetch 비용 상한(윈도우 내 newest-first → 최신 N건 우선)
# 표지/머리말을 건너뛰고 관찰사항(findings) 구간부터 잘라내기 위한 영문 앵커(우선순위 순).
_FDA483_EXCERPT_PATTERNS = (
    r"observation\s+1\b",
    r"during\s+an\s+inspection\s+of\s+your\s+(?:firm|facility|establishment)",
    r"this\s+document\s+lists\s+observations",
    r"\bobservations?\b",
    r"specifically,",
)

# excerpt·소스 관측용(dry-run 검증·운영 health). collect_who.LAST_HEALTH 패턴.
LAST_HEALTH: dict[str, Any] = {}

_TAG_RE = re.compile(r"<[^>]+>")
_MEDIA_RE = re.compile(r"/media/(\d+)/download")
_MDY_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _strip(value: Any) -> str:
    """HTML 태그 제거 + 공백 정규화(셀/필드 값 정리, 순수 함수)."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", str(value or ""))).strip()


def _parse_mdy(raw: str) -> str:
    """MM/DD/YYYY → ISO(YYYY-MM-DD). 실패 시 ''."""
    m = _MDY_RE.search(raw or "")
    if not m:
        return ""
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return ""


def _norm_record_type(text: str) -> str:
    """텍스트 → 정규화 Record Type({483, EIR}) 또는 ''(비대상).

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


def _media_id_from(cell_html: str) -> str:
    """레코드의 483 PDF media id = /media/<id>/download href 의 <id>(안정 dedup·PDF 키).

    주의: JSON 의 node 'mid' 필드는 media id 와 다른 번호(신규 레코드에서 불일치) — href 만 신뢰.
    """
    m = _MEDIA_RE.search(cell_html or "")
    return m.group(1) if m else ""


# ── 정규화 행 dict 스키마(JSON·HTML 공통) ───────────────────────────────────────
#   {record_date, company, fei, record_type, media_id, state, country, publish_date}
def _json_norm_rows(data: list[Any]) -> list[dict[str, str]]:
    """DataTables JSON(list[dict]) → 정규화 행(483/EIR 만). Country 는 JSON 에 없어 ''."""
    rows: list[dict[str, str]] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        rt_cell = str(r.get("field_foia_record_type_1", ""))
        record_type = _norm_record_type(_strip(r.get("field_foia_record_type")) or _strip(rt_cell))
        if not record_type:
            continue
        media_id = _media_id_from(rt_cell)
        if not media_id:
            continue
        rows.append({
            "record_date": _strip(r.get("field_record_date")),
            "company": _strip(r.get("field_company_name_1")),
            "fei": _strip(r.get("field_fein")),
            "record_type": record_type,
            "media_id": media_id,
            "state": _strip(r.get("field_state_1")),
            "country": "",                 # JSON 무 — HTML map 으로 보강
            "establishment_type": _strip(r.get("field_establishment_type_1")),
            "publish_date": _strip(r.get("field_publish_date")),
        })
    return rows


def _cell(cols: list[str], i: int) -> tuple[str, str]:
    """_FDAWLTableParser 셀('text|HREF:href') → (text, href). 범위 밖이면 ('', '')."""
    if i >= len(cols):
        return "", ""
    raw = cols[i]
    if "|HREF:" in raw:
        text, href = raw.split("|HREF:", 1)
        return text.strip(), href.strip()
    return raw.strip(), ""


def _html_norm_rows(html_text: str) -> tuple[list[dict[str, str]], int]:
    """HTML 표 → 정규화 행(483/EIR 만) + 데이터행 총수(sentinel 용). Country 컬럼 보존."""
    parser = _FDAWLTableParser()
    parser.feed(html_text)
    rows: list[dict[str, str]] = []
    data_row_count = 0
    for raw_row in parser.rows:
        cols = raw_row.get("_cols", [])
        if len(cols) < _MIN_COLS:
            continue
        date_text, _ = _cell(cols, _COL_RECORD_DATE)
        if not _MDY_RE.search(date_text):     # 헤더/비데이터 행 배제
            continue
        data_row_count += 1
        rtype_text, href = _cell(cols, _COL_RECORD_TYPE)
        record_type = _norm_record_type(rtype_text)
        if not record_type:
            continue
        media_id = _media_id_from(href)
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


def _fetch_html_rows() -> tuple[list[dict[str, str]], int]:
    """HTML 표 best-effort fetch → (정규화 행, 데이터행 수). 실패는 ([], 0)(비치명 — 보강·폴백용)."""
    try:
        html_text = http_get_html(OII_READING_ROOM_URL, timeout=FDA_483_HTML_TIMEOUT,
                                  retries=HTTP_RETRIES, label="FDA 483 HTML")
    except Exception as e:  # noqa: BLE001
        log("WARN", f"FDA 483 HTML fetch 실패(보강/폴백 불가): {str(e)[:120]}")
        return [], 0
    return _html_norm_rows(html_text)


def _extract_fda483_excerpt(text: str) -> str:
    """483 PDF 평탄화 텍스트 → 영문 관찰사항(findings) 구간 excerpt(없으면 '').

    표지/보일러플레이트가 아니라 결함(관찰사항)을 카드 컨텍스트("왜")로 올리기 위한 추출.
    앵커가 하나도 없으면 ''(빈 PDF/스캔본/구조 이질 → 호출부가 키 미기록·메타 카드 유지).
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

    status: 'ok' | 'no-excerpt' | 'fetch-fail:…' | PDF 엔진 status. 실패 시 excerpt='' →
    호출부가 raw_payload 에 키를 쓰지 않고 항목은 메타 카드로 유지(graceful degrade).
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
    if "distributor" in et and "manufactur" not in et:
        base = {"Tier 3": "Tier 2", "Tier 2": "Tier 1"}.get(base, base)
    return base


def _site_country(country: str, state: str) -> str:
    """Site Country(소재국) 매핑(HOLD②). Country 우선(해외), 공란+State(미국) → 'United States'.

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
    country = nrow.get("country", "")
    establishment_type = nrow.get("establishment_type", "")
    record_date = nrow["record_date"]                    # 실사일(원문 MM/DD/YYYY 유지)
    publish_iso = _parse_mdy(nrow["publish_date"])

    # 노이즈/관련성 게이트 — gate_blob 으로 도메인 판정.
    gate_blob = " ".join([company, establishment_type, country, record_type, excerpt])
    if _kw_any(gate_blob.lower(), QA_HARD_EXCLUDE_TERMS):   # 수의/동물용 하드 드롭
        return None
    relevance = compute_relevance(company, establishment_type, record_type, excerpt)
    if relevance == "Unrelated":           # 기기/식품/화장품 도메인 → 드롭(QA 범위 밖)
        return None
    if relevance == "Pending":
        relevance = "Possible"             # 483/EIR 은 제조 실사 맥락 → 보수적 보존

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

    # Modality 는 insert 시 notion_create_page 가 raw_payload(product_type)+headline/body 로
    # compute_modality 한다(IntakeItem 에 저장 필드 없음 — 타 수집기와 동일).
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
        site_country=site_country,                   # Country(해외) 또는 'United States'/''
        language=LANGUAGE_EN,
        region_jurisdiction=REGION_FDA,
    )


def collect_fda_483(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA 483/EIR 수집 진입점. (items, error_msg).

    전수 backbone = JSON. JSON 사망 시 HTML 폴백(부분) + source-degraded warning.
    - JSON·HTML 모두 실패 → error.
    - JSON 전수 0(483/EIR 타입 전무) + HTML 폴백도 0행 → 구조 변경 의심 error(침묵 금지).
    - 윈도우 내 0건 → 정상(빈 리스트·error 없음).
    - PDF excerpt 실패는 graceful(키 미기록·메타 카드 유지·LAST_HEALTH 경고).
    """
    global LAST_HEALTH
    excerpt_health: dict[str, Any] = {
        "attempted": 0, "ok": 0, "failed": 0, "capped": False, "warnings": [],
    }
    LAST_HEALTH = {"fda483_excerpt": excerpt_health, "source_degraded": False}

    log("INFO", f"FDA 483/EIR 수집(전수=JSON, 보강/폴백=HTML): {FDA_483_JSON_URL}")
    # HTML: country 보강 map + (JSON 사망 시) 폴백 행 — best-effort(실패 비치명).
    html_rows, html_data_count = _fetch_html_rows()
    country_map = {r["media_id"]: r["country"] for r in html_rows if r.get("country")}

    # JSON 전수 backbone.
    json_rows: list[dict[str, str]] | None = None
    json_err = ""
    try:
        data = http_get_json(FDA_483_JSON_URL, timeout=FDA_483_JSON_TIMEOUT,
                             retries=HTTP_RETRIES)
        if isinstance(data, list) and data:
            json_rows = _json_norm_rows(data)
        else:
            json_err = "JSON 비배열/0레코드"
    except Exception as e:  # noqa: BLE001
        json_err = str(e)[:160]

    source_degraded = False
    if json_rows:                       # JSON 성공 + 483/EIR 있음 → 전수 경로
        keep_rows = json_rows
    else:                               # JSON 실패/0 → HTML 폴백(부분·완전성 미보장)
        if not html_rows:
            return [], (f"FDA 483 수집 실패: JSON({json_err or '0 keep'}) · HTML 0행 "
                        "— 두 경로 모두 실패/구조 변경 의심(수동 확인 필요)")
        keep_rows = html_rows
        source_degraded = True
        log("WARN", f"FDA 483 JSON 전수 경로 실패({json_err or '483/EIR 0'}) — "
                    f"HTML 폴백(최신 {html_data_count}행, 부분) · 완전성 미보장")

    # Country 보강(HTML map) — JSON 행은 country='' 라 map 으로 채운다(HTML 폴백 행은 이미 보유).
    for r in keep_rows:
        if not r.get("country"):
            r["country"] = country_map.get(r["media_id"], "")

    # Publish Date 윈도우 필터(전수 평가·정렬 비의존). 최신 N건 excerpt cap 위해 publish desc 정렬.
    in_window = [r for r in keep_rows
                 if _within_window(_parse_mdy(r["publish_date"]), start, end)]
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

    LAST_HEALTH = {"fda483_excerpt": excerpt_health, "source_degraded": source_degraded}
    if excerpt_health["capped"]:
        log("WARN", f"FDA 483 excerpt cap({FDA483_EXCERPT_MAX_ITEMS}) 도달 — "
                    "나머지 항목은 excerpt 없이 메타 카드로 유지")
    log("INFO", f"FDA 483/EIR 완료: {len(items)}건 (윈도우내 후보 {len(in_window)}, "
                f"483/EIR 전수 {len(keep_rows)}, source={'HTML폴백' if source_degraded else 'JSON전수'}) "
                f"· excerpt attempted={excerpt_health['attempted']} ok={excerpt_health['ok']} "
                f"failed={excerpt_health['failed']}")
    return items, None
