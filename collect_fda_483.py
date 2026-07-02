#!/usr/bin/env python3
"""GRM FDA 483 Collector — WHY-1 #3 (가장 깊은 결함 원본).

ENABLE_FDA_483=true 또는 --sources fda483 일 때 collect_intake.main() 에서 호출된다.

데이터 소스 (2026-07-02 실측 보정):
  FDA OII FOIA Electronic Reading Room 현행 HTML/DataTables 표.
  - 구 `https://www.fda.gov/datatables-json/ora-foia-reading.json` backbone 은 더 이상 신뢰하지
    않는다(404/timeout 계열 사망). 현재 공식 리딩룸 페이지의 서버사이드 DataTables AJAX
    (`/datatables/views/ajax`)가 Record Date·Company Name·FEI Number·Record Type·State·
    Country·Establishment Type·Publish Date + `/media/<id>/download` 링크를 준다.
  - 정적 HTML 본문은 최신 10행만 들어 있어 AJAX 페이지네이션을 우선 사용한다. AJAX 설정을
    찾지 못하거나 실패하면 정적 10행 파싱으로 degrade 하되 health warning 을 남긴다.
  - XLSX export 는 media id 가 없어 건별 PDF dedup/source 로 부적합. media id 패턴
    https://www.fda.gov/media/<id>/download 는 안정(직접 합성).

수집 흐름:
  HTML/DataTables fetch → Record Type == 483 필터 → Publish Date 윈도우 → 노이즈/관련성
  게이트 → media id dedup → 건별 483 PDF 결함 excerpt + (옵션) Observation 구조 추출
  (P6 _extract_pdf_text 재사용·최신 N건 cap·graceful) → IntakeItem.

설계 역할:
  - 전수성: HTML DataTables AJAX 를 Publish Date desc 로 페이지네이션한다. 정적 HTML fallback 은
    부분 소스이므로 fda483-source-degraded warning 으로 완전성 리스크를 표면화한다.
  - 483 = Tier 3(무균 시설/신호는 Tier 3 floor). distributor-only 는 하향(§4).
  - Site Country(HOLD②): Country(해외, HTML 보강) 우선 · 공란+State(미국) → "United States" ·
    둘 다 공란 → ""(미상). State(주)는 절대 Site Country 아님(raw site_state 분리).
  - Evidence = B(excerpt 는 prose_input 만 보강, W3 인용 승격 아님 — #1+#2 와 동일 정책).
  - Observation 상세보기는 ENABLE_FDA_483_OBSERVATIONS=true 일 때만 raw_payload 에
    fda_483_observations 를 쓴다. 추출 실패/OCR 오인식/구조 이질은 키 미기록 + 요약카드 유지.
  - excerpt/소스/Observation 실패는 graceful(키 미기록·메타 카드 유지·LAST_HEALTH 경고).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from typing import Any
from urllib.parse import urlencode, urljoin

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


# 데이터 소스 (2026-07-02 — 현행 OII HTML/DataTables 표)
FDA_483_JSON_URL = "https://www.fda.gov/datatables-json/ora-foia-reading.json"
OII_READING_ROOM_URL = (
    "https://www.fda.gov/about-fda/office-inspections-and-investigations/"
    "oii-foia-electronic-reading-room"
)
FDA_MEDIA_BASE = "https://www.fda.gov"
DATATABLE_AJAX_PATH = "/datatables/views/ajax"

# Record Type — 이번 트랙은 FDA Form 483 Observation 만. EIR 은 별개 문서라 수집 대상 밖.
RECORD_TYPE_483 = "483"
RECORD_TYPE_EIR = "Establishment Inspection Report (EIR)"

TYPE_FDA_483 = "483"        # type_or_class(카드 분류) — 483
TYPE_FDA_EIR = "EIR"        # type_or_class — EIR
LANGUAGE_EN = "EN"
REGION_FDA = "USA (FDA)"

HTTP_RETRIES = 3
FDA_483_JSON_TIMEOUT = 60
FDA_483_HTML_TIMEOUT = 30
FDA_483_HTML_PAGE_LENGTH = 100
FDA_483_HTML_MAX_PAGES = 50

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
FDA483_OBSERVATION_DETAIL_MAX_CHARS = 1200
FDA483_TEXT_CORRUPTION_RATIO_MAX = 0.08
FDA483_TEXT_MAX_CHARS = 200000   # ≈74쪽 — 현실 483 절대 초과 안 함
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
_DRUPAL_SETTINGS_RE = re.compile(
    r'<script[^>]+data-drupal-selector="drupal-settings-json"[^>]*>(.*?)</script>',
    re.S,
)
_OBS_RE = re.compile(r"\bOBSERVATION\s+(\d+)\b", re.I)
_WE_OBSERVED_RE = re.compile(r"\b(?:I\s*/\s*)?WE\s+OBSERVED\b", re.I)
_BOILERPLATE_RE = re.compile(
    r"\b(?:SEE\s+REVERSE|EMPLOYEE\(S\)|DEPARTMENT\s+OF\s+HEAL(?:TH)?|"
    r"FORM\s+FDA\s+483|INSPECTIONAL\s+OBSERVATIONS|DATE\s+ISSUED|"
    r"PREVIOUS\s+EDITION|PAGE\s+\d+\s+OF\s+\d+)\b",
    re.I,
)
_BAD_CHAR_RE = re.compile(r"[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]")


def _strip(value: Any) -> str:
    """HTML 태그 제거 + 공백 정규화(셀/필드 값 정리, 순수 함수)."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", str(value or ""))).strip()


def _env_true(name: str) -> bool:
    return (os.environ.get(name, "false") or "").strip().lower() == "true"


def _observations_enabled() -> bool:
    return _env_true("ENABLE_FDA_483_OBSERVATIONS")


def _deep_enabled() -> bool:
    """[483 분석층 2026-07-02] `ENABLE_FDA_483_DEEP`(기본 off) — WL 의 `ENABLE_WL_BODY_FULL`
    동형. on 일 때만 483 PDF 전문(全文)을 raw 에 `fda483_body_full` 로 보존해 심층분석
    (deep_analysis) fan-out 입력으로 쓴다. `ENABLE_FDA_483_OBSERVATIONS`(결정론 상세)와 **독립** —
    deep off 여도 결정론 Observation 상세는 그대로 나오고, deep on 이어도 결정론 층은 불변.
    off(기본) 면 키 부재 → scaffold deep_analysis_ready=False → 골든/동작 완전 불변(활성=사람 게이트)."""
    return _env_true("ENABLE_FDA_483_DEEP")


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

    수집 경로는 `483` 만 사용한다. EIR 인식은 제외 회귀 테스트/레거시 방어용으로 유지한다.
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
    """레거시 DataTables JSON(list[dict]) → 정규화 행. 현행 수집 경로에서는 사용하지 않는다."""
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
    """HTML 표 → 정규화 행(483 만) + 데이터행 총수(sentinel 용). Country 컬럼 보존."""
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
        if record_type != RECORD_TYPE_483:
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


def _datatable_norm_rows(data_rows: list[Any]) -> list[dict[str, str]]:
    """DataTables AJAX `data` 배열 → 정규화 행(483 만)."""
    rows: list[dict[str, str]] = []
    for raw in data_rows:
        if not isinstance(raw, list) or len(raw) < _MIN_COLS:
            continue
        record_type = _norm_record_type(_strip(raw[_COL_RECORD_TYPE]))
        if record_type != RECORD_TYPE_483:
            continue
        media_id = _media_id_from(str(raw[_COL_RECORD_TYPE]))
        if not media_id:
            continue
        rows.append({
            "record_date": _strip(raw[_COL_RECORD_DATE]),
            "company": _strip(raw[_COL_COMPANY]),
            "fei": _strip(raw[_COL_FEI]),
            "record_type": record_type,
            "media_id": media_id,
            "state": _strip(raw[_COL_STATE]),
            "country": _strip(raw[_COL_COUNTRY]),
            "establishment_type": _strip(raw[_COL_ESTABLISHMENT]),
            "publish_date": _strip(raw[_COL_PUBLISH_DATE]),
        })
    return rows


def _datatable_ajax_config(html_text: str) -> dict[str, Any] | None:
    """리딩룸 HTML 의 Drupal settings 에서 서버사이드 DataTables AJAX 설정을 추출."""
    m = _DRUPAL_SETTINGS_RE.search(html_text or "")
    if not m:
        return None
    try:
        settings = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    for dt in (settings.get("datatables") or {}).values():
        if not isinstance(dt, dict):
            continue
        ajax = dt.get("ajax") or {}
        params = ajax.get("data") or {}
        if params.get("view_name") == "ora_foia_electronic_reading_room_solr":
            return {
                "url": urljoin(FDA_MEDIA_BASE, ajax.get("url") or DATATABLE_AJAX_PATH),
                "params": dict(params),
                "total_items": int(params.get("total_items") or 0),
            }
    return None


def _datatable_query(params: dict[str, Any], *, start: int, length: int, draw: int) -> dict[str, Any]:
    """DataTables 서버사이드 프로토콜 파라미터(필터=Record Type 483, Publish Date desc)."""
    out = dict(params)
    out.update({
        "draw": str(draw),
        "start": str(start),
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": str(_COL_PUBLISH_DATE),
        "order[0][dir]": "desc",
        "foia_record_type_name": RECORD_TYPE_483,
    })
    for i in range(9):
        out[f"columns[{i}][data]"] = str(i)
        out[f"columns[{i}][name]"] = ""
        out[f"columns[{i}][searchable]"] = "true"
        out[f"columns[{i}][orderable]"] = "true"
        out[f"columns[{i}][search][value]"] = ""
        out[f"columns[{i}][search][regex]"] = "false"
    return out


def _fetch_datatable_page(config: dict[str, Any], *, start: int, length: int, draw: int) -> dict[str, Any]:
    """현재 OII HTML 테이블의 서버사이드 페이지를 fetch. http_get_html 로 404도 retry/backoff."""
    params = _datatable_query(config["params"], start=start, length=length, draw=draw)
    url = config["url"] + "?" + urlencode(params)
    text = http_get_html(
        url, timeout=FDA_483_HTML_TIMEOUT, retries=HTTP_RETRIES,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": OII_READING_ROOM_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        label="FDA 483 DataTables",
    )
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _fetch_html_rows(start_date: date | None = None) -> tuple[list[dict[str, str]], int, bool]:
    """현행 HTML/DataTables 표 fetch → (정규화 행, 데이터행 수, 부분 fallback 여부)."""
    try:
        html_text = http_get_html(OII_READING_ROOM_URL, timeout=FDA_483_HTML_TIMEOUT,
                                  retries=HTTP_RETRIES, label="FDA 483 HTML")
    except Exception as e:  # noqa: BLE001
        log("WARN", f"FDA 483 HTML fetch 실패: {str(e)[:120]}")
        return [], 0, True

    static_rows, static_count = _html_norm_rows(html_text)
    config = _datatable_ajax_config(html_text)
    if not config:
        log("WARN", "FDA 483 DataTables 설정 없음 — 정적 HTML 10행 fallback(부분 수집)")
        return static_rows, static_count, True

    rows: list[dict[str, str]] = []
    total = 0
    try:
        for page in range(FDA_483_HTML_MAX_PAGES):
            start = page * FDA_483_HTML_PAGE_LENGTH
            data = _fetch_datatable_page(
                config, start=start, length=FDA_483_HTML_PAGE_LENGTH, draw=page + 1)
            raw_rows = data.get("data") if isinstance(data.get("data"), list) else []
            total = int(data.get("recordsFiltered") or data.get("recordsTotal") or total or 0)
            page_rows = _datatable_norm_rows(raw_rows)
            rows.extend(page_rows)
            if not raw_rows or len(raw_rows) < FDA_483_HTML_PAGE_LENGTH:
                break
            if start_date and page_rows:
                dates = [_parse_mdy(r.get("publish_date", "")) for r in page_rows]
                valid = [d for d in dates if d]
                if valid and min(valid) < start_date.isoformat():
                    break
    except Exception as e:  # noqa: BLE001
        log("WARN", f"FDA 483 DataTables 페이지 fetch 실패: {str(e)[:160]} — "
                    "정적 HTML fallback(부분 수집)")
        return static_rows, static_count, True
    return rows, total or len(rows), False


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


def _fetch_fda483_pdf_text(pdf_url: str, max_chars: int = FDA483_TEXT_MAX_CHARS) -> tuple[str, str]:
    """483 PDF fetch → 평탄화 텍스트. fetch 는 grm_common.http_get_bytes(404 포함 retry/backoff).

    ★상한은 483 전용 FDA483_TEXT_MAX_CHARS(200000·≈74쪽)를 **기본**으로 쓴다 — 공유 PDF 엔진의
    기본 상한(GMP용 12000)은 8쪽+ 483 의 뒤 Observation 을 잘라, 이 라이브 경로를 쓰는 **결정론
    Observation 추출**(ENABLE_FDA_483_OBSERVATIONS)과 **deep 전문 확보** 둘 다 앞 2~3건만 남기는
    절단 버그를 냈다. PR #57 이 public `_extract_483_observations` API 만 200000 으로 고치고 이
    라이브 경로(`_fetch_fda483_pdf_text`)는 12000 그대로 두었던 것을 보완한다. excerpt 경로도 이
    함수를 쓰지만 자체적으로 앵커 뒤 1500자만 다시 잘라 무해(현실 483 은 200000 을 넘지 않아
    excerpt/카드 산출물 바이트도 불변). GMP/WHO 는 각자 `_extract_pdf_text` 를 직접 호출해 무관.
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
    return _extract_pdf_text(data, max_chars=max_chars)


def _fetch_fda483_excerpt(pdf_url: str) -> tuple[str, str]:
    """483 PDF fetch → 영문 관찰사항 excerpt. 반환 (excerpt, status).

    status: 'ok' | 'no-excerpt' | 'fetch-fail:…' | PDF 엔진 status. 실패 시 excerpt='' →
    호출부가 raw_payload 에 키를 쓰지 않고 항목은 메타 카드로 유지(graceful degrade).
    P6 PDF 엔진(_extract_pdf_text) 재사용 — fetch 는 grm_common.http_get_bytes(공용 클라이언트).
    """
    text, status = _fetch_fda483_pdf_text(pdf_url)
    if not text:
        return "", status
    excerpt = _extract_fda483_excerpt(text)
    if not excerpt:
        return "", "no-excerpt"
    return excerpt, "ok"


def _text_corruption_ratio(text: str) -> float:
    """PDF 텍스트층 깨짐률. replacement/control 문자가 과하면 상세 추출은 degrade."""
    if not text:
        return 1.0
    bad = len(_BAD_CHAR_RE.findall(text))
    return bad / max(len(text), 1)


def _clean_observation_chunk(chunk: str) -> str:
    """Observation 본문 chunk 에서 페이지 머리말/서명 보일러플레이트를 제거."""
    text = re.sub(r"[\r\f]+", "\n", chunk or "")
    m = _BOILERPLATE_RE.search(text)
    if m:
        text = text[:m.start()]
    text = re.sub(r"\s+", " ", text).strip(" :-\t\n")
    return text


def _first_sentence(text: str) -> tuple[str, str]:
    """첫 문장(deficiency)과 나머지(detail). 문장부호가 없으면 안전 길이로 잘라낸다."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return "", ""
    m = re.search(r"(?<=[.!?])\s+", t)
    if m:
        return t[:m.start()].strip(), t[m.end():].strip()
    if len(t) <= 280:
        return t, ""
    return t[:280].rstrip() + "...", t[280:].strip()


def _extract_483_observations_from_text(text: str) -> list[dict[str, str]]:
    """483 PDF 텍스트층 → Observation 번호별 결정론 구조.

    `WE OBSERVED` 이후 `OBSERVATION N` 앵커로 분할하고, 각 Observation 의 첫 문장을
    deficiency 로 둔다. 유효 deficiency 가 없거나 텍스트층 깨짐률이 높으면 [] 로 degrade.
    """
    if not text or _text_corruption_ratio(text) > FDA483_TEXT_CORRUPTION_RATIO_MAX:
        return []
    body = text
    m = _WE_OBSERVED_RE.search(body)
    if m:
        body = body[m.end():]
    matches = list(_OBS_RE.finditer(body))
    if not matches:
        return []

    out: list[dict[str, str]] = []
    for i, obs in enumerate(matches):
        start = obs.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = _clean_observation_chunk(body[start:end])
        deficiency, detail = _first_sentence(chunk)
        if not deficiency:
            continue
        row = {
            "number": obs.group(1),
            "deficiency": deficiency,
            "detail": detail[:FDA483_OBSERVATION_DETAIL_MAX_CHARS].strip(),
        }
        out.append(row)
    return out


def _extract_483_observations(pdf_bytes: bytes) -> list[dict[str, str]]:
    """483 PDF bytes → Observation rows. 공개 API(테스트/후속 재사용용), LLM/OCR 없음."""
    try:
        from collect_mfds_gmp_inspection import _extract_pdf_text
    except Exception:  # noqa: BLE001
        return []
    text, _status = _extract_pdf_text(pdf_bytes, max_chars=FDA483_TEXT_MAX_CHARS)
    if len(text) >= FDA483_TEXT_MAX_CHARS:
        log("WARN", "483 텍스트 상한 도달 — Observation 일부 누락 가능(수동 확인)")
    return _extract_483_observations_from_text(text)


def _signal_tier(record_type: str, establishment_type: str, excerpt: str) -> str:
    """483 = Tier 3. 무균 시설/신호는 Tier 3 floor, distributor-only 는 하향(§4)."""
    et = (establishment_type or "").lower()
    blob = f"{et} {excerpt}".lower()
    if "sterile" in blob or _kw_any(blob, STERILE_BIO_TIER3_FLOOR):
        return "Tier 3"            # 무균 시설/치명 신호 → Tier 3 floor
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


def _to_item(nrow: dict[str, str], excerpt: str,
             observations: list[dict[str, str]] | None = None,
             body_full: str = "") -> IntakeItem | None:
    """정규화 행(+excerpt) → IntakeItem. 수의/기기/식품 도메인은 None(드롭).

    `body_full`(비공백)이면 raw 에 `fda483_body_full` 로 실어 deep_analysis fan-out 입력으로 쓴다
    (ENABLE_FDA_483_DEEP 게이트 산출 — WL wl_body_full 동형). 결정론 Observation 상세와 별개 층."""
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
        relevance = "Possible"             # 483 은 제조 실사 맥락 → 보수적 보존

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
    if observations:
        raw_payload["fda_483_observations"] = observations
    if body_full:
        raw_payload["fda483_body_full"] = body_full   # deep_analysis fan-out 입력(전문)

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
    """FDA 483 수집 진입점. (items, error_msg).

    전수 backbone = 현행 OII HTML/DataTables. AJAX 사망 시 정적 HTML 폴백(부분) + warning.
    - HTML/DataTables 모두 실패 → error.
    - 483 행 0 → 구조 변경 의심 error(침묵 금지).
    - 윈도우 내 0건 → 정상(빈 리스트·error 없음).
    - PDF excerpt/Observation 실패는 graceful(키 미기록·메타 카드 유지·LAST_HEALTH 경고).
    """
    global LAST_HEALTH
    excerpt_health: dict[str, Any] = {
        "attempted": 0, "ok": 0, "failed": 0, "capped": False, "warnings": [],
    }
    observations_enabled = _observations_enabled()
    observations_health: dict[str, Any] = {
        "enabled": observations_enabled, "attempted": 0, "extracted": 0,
        "failed": 0, "warnings": [],
    }
    # [483 분석층 2026-07-02] deep(전문 보존) 관측. 결정론 Observation 과 독립(위 _deep_enabled).
    deep_enabled = _deep_enabled()
    deep_health: dict[str, Any] = {
        "enabled": deep_enabled, "attempted": 0, "stored": 0, "failed": 0, "warnings": [],
    }
    LAST_HEALTH = {
        "fda483_excerpt": excerpt_health,
        "fda_483_observations": observations_health,
        "fda_483_deep": deep_health,
        "source_degraded": False,
    }

    log("INFO", f"FDA 483 수집(현행 HTML/DataTables): {OII_READING_ROOM_URL}")
    keep_rows, html_data_count, source_degraded = _fetch_html_rows(start)
    if not keep_rows:
        LAST_HEALTH = {
            "fda483_excerpt": excerpt_health,
            "fda_483_observations": observations_health,
            "fda_483_deep": deep_health,
            "source_degraded": source_degraded,
        }
        return [], ("FDA 483 수집 실패: HTML/DataTables 483 행 0 — "
                    "소스 구조 변경 또는 일시 장애(수동 확인 필요)")

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

        # 483 PDF 결함 excerpt + Observation 상세 + (deep on) 전문 보존(cap 내 시도).
        # 실패는 키 미기록 + warning(graceful — 결정론/deep 어느 층이 빠져도 요약카드는 유지).
        excerpt = ""
        observations: list[dict[str, str]] = []
        body_full = ""
        pdf_url = _pdf_url(media_id)
        if pdf_url and not excerpt_health["capped"]:
            if excerpt_health["attempted"] >= FDA483_EXCERPT_MAX_ITEMS:
                excerpt_health["capped"] = True
            else:
                excerpt_health["attempted"] += 1
                if FDA483_EXCERPT_DELAY_SECONDS:
                    time.sleep(FDA483_EXCERPT_DELAY_SECONDS)
                # 483 전문(200000 상한)을 읽는다 — 결정론 Observation·deep 전문 모두 8쪽+ 483 의
                # 뒤 Observation 까지 담기게(공유 엔진 12000 기본이 절단하던 것 보완). excerpt 는
                # 이 text 에서 앵커 뒤 1500자만 다시 잘라 산출물 불변.
                text, status = _fetch_fda483_pdf_text(pdf_url)
                excerpt = _extract_fda483_excerpt(text) if text else ""
                if excerpt:
                    excerpt_health["ok"] += 1
                else:
                    excerpt_health["failed"] += 1
                    warn = f"FDA 483 excerpt 실패({status if not text else 'no-excerpt'}): {pdf_url}"
                    excerpt_health["warnings"].append(warn)
                    log("WARN", warn + " — 메타 카드로 유지(manual_review)")
                if observations_enabled:
                    observations_health["attempted"] += 1
                    observations = _extract_483_observations_from_text(text) if text else []
                    if observations:
                        observations_health["extracted"] += 1
                    else:
                        observations_health["failed"] += 1
                        warn = (f"FDA 483 observations 실패"
                                f"({status if not text else 'no-observations'}): {pdf_url}")
                        observations_health["warnings"].append(warn)
                        log("WARN", warn + " — 요약카드로 유지")
                # [483 분석층] 전문 보존 — 파싱 가능한 실제 483(Observation ≥1)일 때만 보존해
                # fan-out 이 스캔본/표지-only/깨진 텍스트를 LLM 입력으로 삼지 않게 한다(환각 통제).
                # ENABLE_FDA_483_OBSERVATIONS 와 독립(순수 파서 재사용 — 그 플래그와 무관하게 판정).
                if deep_enabled:
                    deep_health["attempted"] += 1
                    parsed = _extract_483_observations_from_text(text) if text else []
                    if parsed:
                        body_full = text
                        deep_health["stored"] += 1
                    else:
                        deep_health["failed"] += 1
                        warn = (f"FDA 483 deep 전문 미확보"
                                f"({status if not text else 'no-observations'}): {pdf_url}")
                        deep_health["warnings"].append(warn)
                        log("WARN", warn + " — 분석층 없이 발행(결정론 상세·요약카드는 유지)")

        item = _to_item(nrow, excerpt, observations, body_full)   # None = 수의/기기/식품 도메인 드롭
        if item is not None:             # dedup 은 위 media_id seen 으로 보장(doc_id=fda483-<id>)
            items.append(item)

    LAST_HEALTH = {
        "fda483_excerpt": excerpt_health,
        "fda_483_observations": observations_health,
        "fda_483_deep": deep_health,
        "source_degraded": source_degraded,
    }
    if excerpt_health["capped"]:
        log("WARN", f"FDA 483 excerpt cap({FDA483_EXCERPT_MAX_ITEMS}) 도달 — "
                    "나머지 항목은 excerpt/detail 없이 메타 카드로 유지")
    log("INFO", f"FDA 483 완료: {len(items)}건 (윈도우내 후보 {len(in_window)}, "
                f"483 행 {len(keep_rows)}/{html_data_count}, "
                f"source={'HTML정적폴백' if source_degraded else 'DataTables'}) "
                f"· excerpt attempted={excerpt_health['attempted']} ok={excerpt_health['ok']} "
                f"failed={excerpt_health['failed']} · observations enabled={observations_enabled} "
                f"attempted={observations_health['attempted']} "
                f"extracted={observations_health['extracted']} "
                f"failed={observations_health['failed']} · deep enabled={deep_enabled} "
                f"attempted={deep_health['attempted']} stored={deep_health['stored']} "
                f"failed={deep_health['failed']}")
    return items, None
