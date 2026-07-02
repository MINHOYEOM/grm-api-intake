#!/usr/bin/env python3
"""GRM MFDS GMP Inspection Result Collector - Phase 2d.

Collects metadata from nedrug's public "의약품등 GMP 실사 결과공개"
HTML board, then best-effort extracts public attachment text.
"""

from __future__ import annotations

import io
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import os
import zipfile
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any

from grm_common import http_get_bytes, log
from collect_intake import (
    IntakeItem,
    SOURCE_MFDS,
    SRC_TYPE_OFFICIAL_SCRAPE,
    _within_window,
)


BOARD_URL = "https://nedrug.mfds.go.kr/pbp/CCBBD03"
LIST_URL = "https://nedrug.mfds.go.kr/pbp/CCBBD03/getList"
DOWNLOAD_URL_BASE = "https://nedrug.mfds.go.kr/cmn/edms/down/"

TYPE_GMP_INSPECTION = "gmp-inspection"
LANGUAGE_KO = "KO"
REGION_MFDS = "Korea (MFDS)"

PAGE_SIZE = 100
MAX_PAGES = 10
ATTACHMENT_REQUEST_DELAY_SECONDS = 1.0
MAX_ATTACHMENT_TEXT_CHARS = 12000
MAX_ATTACHMENT_BODY_CHARS = 6000
HTTP_RETRIES = 3

LAST_HEALTH: dict[str, Any] = {}

_NO_DEFICIENCY_RE = re.compile(
    r"(지적\s*\(?보완\)?\s*사항\s*(?:\(Deficiencies\))?\s*없음|"
    r"지적\s*사항\s*없음|보완\s*사항\s*없음)"
)
# present 는 헤더 근접 '분류 명사'만으로 판정하지 않는다(B3): 표지/목차 보일러플레이트
# '제조소 (일반)현황' 의 '제조' 가 .{0,80} 창에 걸려 정상 보고서가 Tier 3 로 오승격됐다.
# ① 명사 '제조' 는 '제조소' 를 제외한 형태만(제조 공정·제조위생 등 finding 본문),
# ② 명사 매칭 뒤 60자 내 판정 어휘(있음·미흡·부적합·불(적)합·일탈·N건) 동반을 요구.
# 판정 불충분이면 unknown → manual_review_required 경고 경로(과승격보다 안전).
_DEFICIENCY_PRESENT_RE = re.compile(
    r"지적\s*\(?보완\)?\s*사항\s*(?:\(Deficiencies\))?"
    r"(?:\s*있음"
    r"|.{0,30}?\d+\s*건"
    r"|.{0,80}(?:품질경영|시설장비|제조(?!소)|시험실|원자재|포장표시|허가관리|위탁|밸리데이션)"
    r".{0,60}?(?:있음|미흡|부적합|불\s*적?\s*합|일탈|\d+\s*건))",
    re.S,
)
# 표지·개요(제조소 현황·실사 개요)를 건너뛰고 '평가 결과 지적(보완)사항' 결론
# 섹션부터 잘라내기 위한 앵커(우선순위 순). PDF 본문은
# [표지 → 제조소 현황 → 실태조사 개요 → 실태조사 결과 → 평가 결과 지적(보완)사항(Deficiencies)]
# 순서라, 카드 인용이 표지 보일러플레이트가 아니라 실제 지적/결론을 가리키게 한다.
_DEFICIENCY_EXCERPT_PATTERNS = (
    # 1번 앵커: 실문은 "평가 결과: 지적(보완)사항" 처럼 콜론이 껴서 종전 정규식이 MISS 했다
    # (전문수집 트랙 실측 2026-07-02). `:?` 로 콜론 허용 — 무해·기존 무콜론 형태도 그대로 매칭.
    r"평가\s*결과\s*:?\s*지적\s*\(?\s*보완\s*\)?\s*사항",
    r"지적\s*\(?\s*보완\s*\)?\s*사항\s*\(\s*Deficiencies\s*\)",
    r"지적\s*\(?\s*보완\s*\)?\s*사항",
)

# ── [상세보기 결정론 승격 2026-07-02] 지적사항 표 구조 추출 ────────────────────
# nedrug 정기실태조사 PDF 는 지적(보완)사항을 5컬럼 표(분야·구분·근거법령·지적내용·비고)로
# 공개한다(전문수집 트랙 실측). PyMuPDF `find_tables()` 만으로 결정론 추출 — 새 의존성·OCR·LLM
# 전무, 환각 0. 사전 GMP 평가(수입) B형은 판정만 있어 표가 없다 → 유형 분기 후 periodic 만 시도.
_INSPECTION_TYPE_PERIODIC_RE = re.compile(r"정기\s*실태\s*조사|정기\s*실사")
_INSPECTION_TYPE_PRE_MARKET_RE = re.compile(r"사전\s*GMP\s*평가|사전\s*평가\s*실태조사")
# 표 헤더 판별 토큰(모두 포함해야 지적 표로 채택) + 컬럼→필드 매핑 토큰.
_DEFICIENCY_HEADER_TOKENS = ("분야", "근거", "지적")
_DEFICIENCY_COLUMN_TOKENS = {
    "area": ("분야",),
    "severity": ("구분", "중대도"),
    "legal_basis": ("근거",),
    "summary": ("지적", "보완"),
    "followup": ("비고", "후속", "조치"),
}
_DEFICIENCY_TABLE_MAX_ROWS = 200  # 폭주 방어(정상 최대 수십 행)

# 의료용 고압가스 제조소는 GMP 공개 대상이지만, 경구 고형제 QA 다이제스트에서는
# 반복 노이즈가 컸다. 명시적 가스 업체/제품 단서만 Intake에서 제외한다.
# 한국어 단서는 substring, 영문 브랜드는 단어 경계(\b) 매칭 — "linde"가
# "Lindenberg Pharma" 같은 무관 업체명에 오탐하는 것을 방지.
# 단독 "수소"/"밀성산업"/"대성산업"은 제거: 전체 상호("한국수소" 등)·"가스" 토큰경계로 충분하며
# 부분 일치 시 무관 제약사 오탐 위험이 더 크다.
_MEDICAL_GAS_COMPANY_TERMS = [
    "에어퍼스트",
    "한국수소",
    "린데코리아",
    "에어프로덕츠",
    "프렉스에어",
]
_MEDICAL_GAS_COMPANY_WORD_RE = re.compile(
    r"\b(?:linde|praxair|air\s+first|air\s+products|air\s+liquide)\b"
)
# 한글 "가스"는 영문 브랜드(\b)와 동일하게 토큰경계로 매칭한다. 바 "가스" 부분문자열은
# "메가스터디제약"(메[가스]터디)·"한국가스공사 자회사 제약"(가스[공사]) 같은 무관 제약사를
# 과배제했다. "가스" 뒤에 한글이 이어지지 않을 때만(="○○산업가스" 류 접미사·단독 토큰)
# 가스 제조사로 본다 — "밀성산업가스"·"대성산업가스"는 잡고, 어중 "가스"는 흘려보낸다.
_MEDICAL_GAS_KO_COMPANY_RE = re.compile(r"가스(?![가-힣])")
_MEDICAL_GAS_CONTEXT_TERMS = [
    "의료용 고압가스", "의료용가스", "의료용 가스",
    "고압가스", "액화산소", "액화질소",
    "산소가스", "질소가스", "아산화질소", "혼합가스",
]


@dataclass
class _Cell:
    text: str = ""
    doc_id: str = ""


@dataclass
class _AttachmentParse:
    status: str
    file_format: str = ""
    text: str = ""
    deficiency: str = "unknown"
    deficiency_excerpt: str = ""   # 표지 너머 '지적(보완)사항' 결론 섹션(카드 인용용)
    bytes_downloaded: int = 0
    error: str = ""
    # [상세보기 결정론 승격 2026-07-02] periodic PDF 지적 표 구조 추출 결과 + 관측 상태.
    deficiencies: list[dict[str, str]] = field(default_factory=list)
    deficiency_table_status: str = ""  # extracted|empty|gate-degraded|parse-fail|skipped-type


class _InspectionTableParser(HTMLParser):
    """Parse the GMP inspection result board table.

    Expected columns:
      No | 사전/사후 | 완제/원료 | 국가 | 제조소명 | 소재지 |
      실사시작일 | 실사종료일 | 실사결과(download) | 등록일
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_depth = 0
        self._cell_parts: list[str] = []
        self._cell_doc_id = ""
        self._row: list[_Cell] = []
        self.rows: list[list[_Cell]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table":
            self._in_table = True
        if not self._in_table:
            return
        if tag == "tr":
            self._in_row = True
            self._row = []
        if tag in ("td", "th") and self._in_row:
            if self._in_cell:
                self._cell_depth += 1
            else:
                self._in_cell = True
                self._cell_depth = 1
                self._cell_parts = []
                self._cell_doc_id = ""
        if self._in_cell:
            for value in attr_dict.values():
                if not value:
                    continue
                match = re.search(r"downFile\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", value)
                if match:
                    self._cell_doc_id = match.group(1).strip()

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag in ("td", "th") and self._in_cell:
            self._cell_depth -= 1
            if self._cell_depth <= 0:
                text = " ".join(part.strip() for part in self._cell_parts if part.strip()).strip()
                self._row.append(_Cell(text=text, doc_id=self._cell_doc_id))
                self._in_cell = False
        if tag == "tr" and self._in_row:
            self._in_row = False
            if self._row:
                self.rows.append(self._row)
        if tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._cell_parts.append(stripped)


def _parse_date(raw: str) -> str:
    raw = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return ""
    return ""


def _download_url(doc_id: str) -> str:
    return DOWNLOAD_URL_BASE + urllib.parse.quote(doc_id, safe="")


def _request_url(page_no: int) -> str:
    params = {
        "page": page_no,
        "limit": PAGE_SIZE,
    }
    return LIST_URL + "?" + urllib.parse.urlencode(params)


def _clean_cell_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw or "").strip()


def _is_medical_gas_gmp_noise(raw: dict[str, str]) -> bool:
    manufacturer = _clean_cell_text(raw.get("manufacturer", "")).lower()
    if manufacturer and (
        any(term in manufacturer for term in _MEDICAL_GAS_COMPANY_TERMS)
        or _MEDICAL_GAS_COMPANY_WORD_RE.search(manufacturer)
        or _MEDICAL_GAS_KO_COMPANY_RE.search(manufacturer)
    ):
        return True

    context = " ".join(
        _clean_cell_text(raw.get(key, ""))
        for key in ("manufacturer", "address", "product_type")
    ).lower()
    return any(term in context for term in _MEDICAL_GAS_CONTEXT_TERMS)


def _normalize_extracted_text(raw: str) -> str:
    text = (raw or "").replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _detect_attachment_format(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return "hwp-ole"
    return "unknown"


def _get_bytes(url: str, *, timeout: int = 30, accept: str = "*/*") -> bytes:
    return http_get_bytes(
        url,
        timeout=timeout,
        retries=HTTP_RETRIES,
        headers={"Accept": accept, "Referer": BOARD_URL},
        label="MFDS GMP inspection",
    )


def _extract_deficiency_excerpt(text: str) -> str:
    """표지·개요를 건너뛰고 '평가 결과 지적(보완)사항' 결론 섹션부터 반환(없으면 "").

    카드 W3 인용/요약이 표지(제조소명·실사목적 보일러플레이트)가 아니라 실제
    지적/결론을 가리키게 하기 위한 추출. 마커가 전혀 없으면 "" → 호출부가 전체
    본문으로 폴백한다.
    """
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return ""
    for pat in _DEFICIENCY_EXCERPT_PATTERNS:
        m = re.search(pat, compact)
        if m:
            return compact[m.start():][:MAX_ATTACHMENT_BODY_CHARS].strip()
    return ""


def _assess_deficiency(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return "unknown"
    # none 우선: _NO_DEFICIENCY_RE 는 '지적/보완 사항 없음' 앵커 형태만 매칭하므로
    # (단독 '이상 없음'은 A1 에서 제거됨) 부수적 '없음'이 실제 지적을 가리지 않는다.
    # present 우선이면 결론 '없음' 뒤 '제조소 (일반)현황' 헤더의 '제조' 가
    # _DEFICIENCY_PRESENT_RE 의 .{0,80} 창에 걸려 정상 보고서가 오승격된다(B3).
    if _NO_DEFICIENCY_RE.search(compact):
        return "none"
    if _DEFICIENCY_PRESENT_RE.search(compact):
        return "present"
    # 종전 fallback("Deficiencies" 존재 + 어디에도 '없음' 없음 → present)은 B3 와
    # 동일한 오승격 경로(헤더만 있는 정상/영문 보고서를 Tier 3 로) — 제거.
    # 판정 근거 불충분은 unknown → manual_review_required 로 사람이 본다.
    return "unknown"


def _deficiency_table_enabled() -> bool:
    """`ENABLE_GMP_DEFICIENCY_TABLE`(기본 off, opt-in) — WL `ENABLE_WL_BODY_FULL` 동형.

    off 면 기존 플로우 완전 무변경(현행 excerpt/assessment 그대로). on 이고 periodic 이고
    표 추출 성공 시만 raw_payload["gmp_deficiencies"] = rows 기록(점진 활성).
    """
    return os.environ.get("ENABLE_GMP_DEFICIENCY_TABLE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _detect_inspection_type(text: str) -> str:
    """제목 문자열로 문서 유형 분기: periodic(국내 정기실태조사)·pre_market(수입 사전평가)·unknown.

    periodic 만 지적 표를 공개한다 → periodic 일 때만 표 추출을 시도(사전평가 B형에 강제하면
    표 없음 → 오탐/빈블록). 결정론·LLM 없음.
    """
    compact = re.sub(r"\s+", " ", text or "")
    if not compact:
        return "unknown"
    # pre_market 을 먼저 본다: 사전평가 문서에도 "정기실태조사" 문구가 참조로 섞일 수 있어
    # 사전평가 표지가 우선 판별되도록(오분류 시 표 미추출=안전 쪽).
    if _INSPECTION_TYPE_PRE_MARKET_RE.search(compact):
        return "pre_market"
    if _INSPECTION_TYPE_PERIODIC_RE.search(compact):
        return "periodic"
    return "unknown"


def _clean_deficiency_cell(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\n", " ")).strip()


def _match_deficiency_header(rows: list[list[str | None]]) -> tuple[int | None, dict[str, int | None]]:
    """지적 표 헤더행 인덱스 + 컬럼→필드 인덱스 매핑 반환(없으면 (None, {})).

    헤더에 분야·근거·지적 을 모두 포함하는 행만 지적 표로 채택(다른 표=제조소 현황 등 배제).
    컬럼 매핑은 위치가 아니라 헤더 토큰으로 — '근거 법령' vs '근거법령' 같은 표기차에 견고.
    """
    for i, row in enumerate(rows):
        cells = [_clean_deficiency_cell(c) for c in row]
        joined = " ".join(cells)
        if all(tok in joined for tok in _DEFICIENCY_HEADER_TOKENS):
            colmap: dict[str, int | None] = {}
            for field_name, tokens in _DEFICIENCY_COLUMN_TOKENS.items():
                idx = None
                for ci, cell in enumerate(cells):
                    compact = cell.replace(" ", "")
                    if any(tok in compact for tok in tokens):
                        idx = ci
                        break
                colmap[field_name] = idx
            return i, colmap
    return None, {}


def _normalize_deficiency_table(rows: list[list[str | None]]) -> list[dict[str, str]]:
    """`Table.extract()` 표(행=셀 리스트)를 지적사항 dict 목록으로 정규화(순수·결정론).

    헤더행·주석행(구조 컬럼 전무)·빈행·반복 헤더 제외. 각 행은 근거법령 또는 지적내용이
    비어있지 않아야 유효(품질 게이트). LLM·fetch 없음.
    """
    if not rows:
        return []
    header_idx, colmap = _match_deficiency_header(rows)
    if header_idx is None:
        return []
    out: list[dict[str, str]] = []
    for row in rows[header_idx + 1:]:
        rec: dict[str, str] = {}
        for field_name in ("area", "severity", "legal_basis", "summary", "followup"):
            ci = colmap.get(field_name)
            rec[field_name] = (_clean_deficiency_cell(row[ci])
                               if ci is not None and ci < len(row) else "")
        # 품질 게이트: 근거법령 또는 지적내용 둘 다 비면 주석/빈/구분줄 → 제외.
        if not (rec["legal_basis"] or rec["summary"]):
            continue
        # 페이지 걸친 반복 헤더행 방어.
        if rec["area"] == "분야" or rec["legal_basis"].replace(" ", "") == "근거법령":
            continue
        out.append(rec)
        if len(out) >= _DEFICIENCY_TABLE_MAX_ROWS:
            break
    return out


def _extract_deficiency_table(data: bytes) -> list[dict[str, str]]:
    """PDF 바이트에서 지적사항 표를 결정론 추출(PyMuPDF find_tables). 없으면 [].

    페이지 걸친 다중 표를 누적. 개별 표/페이지 파싱 예외는 건너뛰되(부분 성공 우선),
    문서 열기 실패는 상위로 전파(호출부가 parse-fail 로 강등). OCR·LLM 없음.
    """
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return []
    out: list[dict[str, str]] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        if doc.needs_pass or doc.is_encrypted:
            return []
        for page in doc:
            try:
                finder = page.find_tables()
            except Exception:
                continue
            for table in finder.tables:
                try:
                    extracted = table.extract()
                except Exception:
                    continue
                out.extend(_normalize_deficiency_table(extracted))
    return out


def _extract_pdf_text(data: bytes) -> tuple[str, str]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return "", "pdf-parser-missing"
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or doc.is_encrypted:
                # C4: 잠긴 PDF 는 scan-no-text/parse-fail 로 오라벨하지 않는다 —
                # 라우팅은 동일(unknown→manual_review)이나 수동 확인 메시지의
                # 진단이 '스캔본'이 아니라 '암호화'를 가리키게 정정.
                # (owner-pw 만 걸린 열람 가능 PDF 는 fitz 가 자동 해제해
                # 둘 다 False — 본문 추출 경로 유지.)
                return "", "pdf-encrypted"
            text = "\n".join(page.get_text("text") for page in doc)
    except Exception as e:
        return "", f"pdf-parse-fail:{type(e).__name__}"
    text = _normalize_extracted_text(text)
    if not text:
        return "", "scan-no-text"
    return text[:MAX_ATTACHMENT_TEXT_CHARS], "pdf-ok"


def _extract_hwpx_text(data: bytes) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = sorted(
                name
                for name in zf.namelist()
                if name.startswith("Contents/section") and name.endswith(".xml")
            )
            if not names:
                return "", "zip-not-hwpx"

            parts: list[str] = []
            for name in names:
                try:
                    root = ET.fromstring(zf.read(name))
                except ET.ParseError:
                    continue
                for elem in root.iter():
                    local = elem.tag.rsplit("}", 1)[-1]
                    if local == "t" and elem.text:
                        parts.append(elem.text)
            text = _normalize_extracted_text(" ".join(parts))
            if not text:
                return "", "hwpx-no-text"
            return text[:MAX_ATTACHMENT_TEXT_CHARS], "hwpx-ok"
    except zipfile.BadZipFile:
        return "", "zip-bad"
    except Exception as e:
        return "", f"hwpx-parse-fail:{type(e).__name__}"


def _parse_attachment(doc_id: str) -> _AttachmentParse:
    if not doc_id:
        return _AttachmentParse(status="missing-doc-id")

    url = _download_url(doc_id)
    try:
        time.sleep(ATTACHMENT_REQUEST_DELAY_SECONDS)
        data = _get_bytes(url, timeout=45, accept="*/*")
    except RuntimeError as e:
        return _AttachmentParse(status="download-fail", error=str(e)[:200])

    file_format = _detect_attachment_format(data)
    if file_format == "pdf":
        text, status = _extract_pdf_text(data)
    elif file_format == "zip":
        text, status = _extract_hwpx_text(data)
        if status == "hwpx-ok":
            file_format = "hwpx"
    elif file_format == "hwp-ole":
        text, status = "", "hwp-skip"
    else:
        text, status = "", "unknown-format"

    deficiency = _assess_deficiency(text)
    deficiencies, table_status = _parse_deficiency_table(
        data, file_format, text, deficiency, doc_id)
    return _AttachmentParse(
        status=status,
        file_format=file_format,
        text=text,
        deficiency=deficiency,
        deficiency_excerpt=_extract_deficiency_excerpt(text),
        bytes_downloaded=len(data),
        deficiencies=deficiencies,
        deficiency_table_status=table_status,
    )


def _parse_deficiency_table(
    data: bytes, file_format: str, text: str, deficiency: str, doc_id: str,
) -> tuple[list[dict[str, str]], str]:
    """지적 표 추출 오케스트레이션(플래그·유형분기·품질게이트). 반환 (rows, status).

    플래그 off·비PDF·본문없음 → ("", "") 로 완전 무영향(현행 플로우 불변). periodic PDF 만
    시도하고, 추출 실패·유형 unknown·플래그 off = 조용히 요약카드 유지(degrade 우선).
    """
    if not (_deficiency_table_enabled() and file_format == "pdf" and text):
        return [], ""
    itype = _detect_inspection_type(text)
    if itype != "periodic":
        return [], "skipped-type"  # pre_market/unknown → 요약보강(적합/부적합 배지)
    try:
        rows = _extract_deficiency_table(data)
    except Exception as e:  # noqa: BLE001 — 파싱 붕괴는 degrade(요약카드 유지)
        log("WARN", f"MFDS GMP 지적 표 추출 실패({type(e).__name__}) — 요약카드 유지: {doc_id}")
        return [], "parse-fail"
    if rows:
        return rows, "extracted"
    # 유효행 0. '지적사항 present' 인데 표가 안 잡히면(레이아웃 변이 등) 조용히 강등 + 경고.
    if deficiency == "present":
        log("WARN", "MFDS GMP 지적 표 0행(지적사항 present) — 요약카드 유지: " f"{doc_id}")
        return [], "gate-degraded"
    return [], "empty"  # '지적사항 없음' 정상(적합 배지) — 표 없음이 맞음


def _row_to_raw(row: list[_Cell]) -> dict[str, str] | None:
    if len(row) < 10:
        return None
    seq = _clean_cell_text(row[0].text)
    if not seq.isdigit():
        return None
    doc_id = row[8].doc_id
    if not doc_id:
        return None
    return {
        "seq": seq,
        "before_after": _clean_cell_text(row[1].text),
        "product_type": _clean_cell_text(row[2].text),
        "country": _clean_cell_text(row[3].text),
        "manufacturer": _clean_cell_text(row[4].text),
        "address": _clean_cell_text(row[5].text),
        "inspection_start": _clean_cell_text(row[6].text),
        "inspection_end": _clean_cell_text(row[7].text),
        "doc_id": doc_id,
        "registered_date": _clean_cell_text(row[9].text),
    }


def _parse_rows(html_text: str) -> list[dict[str, str]]:
    parser = _InspectionTableParser()
    parser.feed(html_text)
    rows: list[dict[str, str]] = []
    for row in parser.rows:
        raw = _row_to_raw(row)
        if raw:
            rows.append(raw)
    return rows


def _set_last_health(
    *,
    item_count: int,
    parsed_rows: int,
    parse_status_counts: dict[str, int],
    deficiency_counts: dict[str, int],
    manual_review_count: int,
    page_warnings: list[str],
    pages_seen: int,
    max_pages_reached: bool = False,
    deficiency_table: dict[str, Any] | None = None,
) -> None:
    global LAST_HEALTH
    LAST_HEALTH = {
        "item_count": item_count,
        "parsed_rows": parsed_rows,
        "parse_status_counts": dict(parse_status_counts),
        "deficiency_counts": dict(deficiency_counts),
        "manual_review_count": manual_review_count,
        "page_warnings": list(page_warnings),
        "pages_seen": pages_seen,
        "max_pages_reached": max_pages_reached,
        # [상세보기 결정론 승격 2026-07-02] 지적 표 추출 관측(collect_who WHOPIR health 동형).
        "deficiency_table": dict(deficiency_table or {}),
    }


def _body(raw: dict[str, str], attachment: _AttachmentParse,
          manual_review: bool = False) -> str:
    parts = []
    if manual_review:
        parts.append("⚠️ 첨부 자동판독 불가 — 지적사항 유무 수동 확인 필요 "
                     f"(상태: {attachment.status}). 아래 다운로드 링크에서 직접 확인할 것.")
    parts += [
        f"제조소명: {raw.get('manufacturer', '')}",
        f"소재지: {raw.get('address', '')}",
        f"국가: {raw.get('country', '')}",
        f"구분: {raw.get('before_after', '')} / {raw.get('product_type', '')}",
        f"실사일자: {raw.get('inspection_start', '')} ~ {raw.get('inspection_end', '')}",
        f"등록일: {raw.get('registered_date', '')}",
        f"실사결과 다운로드: {_download_url(raw.get('doc_id', ''))}",
        f"첨부 본문 추출 상태: {attachment.status}",
    ]
    if attachment.file_format:
        parts.append(f"첨부 포맷: {attachment.file_format}")
    if attachment.deficiency != "unknown":
        parts.append(f"지적사항 판정: {attachment.deficiency}")
    if attachment.deficiency_excerpt:
        # 표지 너머 핵심(지적/결론)을 먼저 노출 — 사람·Routine 이 보일러플레이트를
        # 건너뛰지 않아도 되게 한다(전체 원문은 아래에 그대로 보존).
        parts.extend([
            "",
            "주요 지적/결론:",
            attachment.deficiency_excerpt[:600],
        ])
    if attachment.text:
        parts.extend([
            "",
            "실사 결과/지적(보완)사항 원문:",
            attachment.text[:MAX_ATTACHMENT_BODY_CHARS],
        ])
    elif attachment.error:
        parts.append(f"첨부 본문 추출 오류: {attachment.error}")
    return "\n".join(part for part in parts if not part.endswith(": "))


def _to_item(raw: dict[str, str], api_query_url: str) -> IntakeItem | None:
    doc_id = raw.get("doc_id", "").strip()
    manufacturer = raw.get("manufacturer", "").strip()
    registered_date = _parse_date(raw.get("registered_date", ""))
    if not doc_id or not manufacturer or not registered_date:
        return None
    if _is_medical_gas_gmp_noise(raw):
        log("INFO", f"MFDS GMP 실태조사 의료용 가스 항목 제외: {manufacturer}")
        return None

    country = raw.get("country", "").strip()
    before_after = raw.get("before_after", "").strip()
    product_type = raw.get("product_type", "").strip()
    headline = f"[GMP실사] {manufacturer}"
    if country:
        headline += f" ({country})"
    detail = "·".join(part for part in [before_after, product_type] if part)
    if detail:
        headline += f" - {detail}"

    download_url = _download_url(doc_id)
    attachment = _parse_attachment(doc_id)
    qa_relevance = "Likely" if attachment.deficiency == "present" else "Possible"
    signal_tier = "Tier 3" if attachment.deficiency == "present" else "Tier 2"

    # P0 개선: 첨부를 자동판독하지 못해 지적사항 유무를 확정 못한 경우(주로 구형 .hwp/OLE,
    # 다운로드 실패, 스캔본 등)에는 침묵 강등되지 않도록 '수동확인 필요' 플래그를 남긴다.
    # 무차별 Tier 3 승격은 노이즈가 크므로 Tier 2는 유지하되, Routine이 사람 확인을 큐잉하도록 표시.
    manual_review = attachment.deficiency == "unknown" and attachment.status not in (
        "pdf-ok", "hwpx-ok",
    )

    raw_payload: dict[str, Any] = {
        "source": "nedrug CCBBD03",
        **raw,
        "download_url": download_url,
        "attachment_parse_status": attachment.status,
        "attachment_file_format": attachment.file_format,
        "attachment_bytes": attachment.bytes_downloaded,
        "attachment_deficiency_assessment": attachment.deficiency,
        "manual_review_required": manual_review,
    }
    if attachment.error:
        raw_payload["attachment_parse_error"] = attachment.error
    if attachment.text:
        raw_payload["attachment_text"] = attachment.text
    if attachment.deficiency_excerpt:
        raw_payload["attachment_deficiency_excerpt"] = attachment.deficiency_excerpt
    # [상세보기 결정론 승격 2026-07-02] periodic 지적 표 성공 시만 구조 배열 기록(card_scaffold
    # deterministic_detail 소비). status 는 관측용(플래그 on 시도분만) — off 면 키 자체 부재.
    if attachment.deficiencies:
        raw_payload["gmp_deficiencies"] = attachment.deficiencies
    if attachment.deficiency_table_status:
        raw_payload["gmp_deficiency_table_status"] = attachment.deficiency_table_status

    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=f"gmpinspect-{doc_id}",
        date_iso=registered_date,
        headline=headline,
        official_url=BOARD_URL,
        type_or_class=TYPE_GMP_INSPECTION,
        firm=manufacturer,
        body=_body(raw, attachment, manual_review),
        api_query=api_query_url,
        qa_relevance=qa_relevance,
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_SCRAPE,
        signal_tier=signal_tier,
        raw_payload=raw_payload,
        source_url=download_url,
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
        site_country=country,
    )


def _tally_deficiency_table_health(health: dict[str, Any], item: IntakeItem) -> None:
    """수집 항목 1건의 지적 표 관측 상태를 health 누적기에 반영(결정론·부작용 없음)."""
    status = str(item.raw_payload.get("gmp_deficiency_table_status") or "")
    if status not in ("extracted", "empty", "gate-degraded", "parse-fail"):
        return  # 플래그 off / 비PDF / skipped-type 은 attempted 로 세지 않음
    health["attempted"] += 1
    if status == "extracted":
        health["extracted"] += 1
    elif status in ("gate-degraded", "parse-fail"):
        health["failed"] += 1
        health["warnings"].append(f"{status}: {item.firm}")


def collect_mfds_gmp_inspections(
    start: date,
    end: date,
) -> tuple[list[IntakeItem], str | None]:
    """Collect GMP inspection result metadata from nedrug's public board."""
    items: list[IntakeItem] = []
    seen_ids: set[str] = set()
    page_no = 1
    pages_fetched = 0
    total_seen_rows = 0
    parse_status_counts: dict[str, int] = {}
    deficiency_counts: dict[str, int] = {}
    manual_review_count = 0
    page_warnings: list[str] = []
    deficiency_table_health: dict[str, Any] = {
        "enabled": _deficiency_table_enabled(),
        "attempted": 0, "extracted": 0, "failed": 0, "warnings": [],
    }
    _set_last_health(
        item_count=0,
        parsed_rows=0,
        parse_status_counts=parse_status_counts,
        deficiency_counts=deficiency_counts,
        manual_review_count=0,
        page_warnings=page_warnings,
        pages_seen=0,
        deficiency_table=deficiency_table_health,
    )

    while page_no <= MAX_PAGES:
        url = _request_url(page_no)
        try:
            html_bytes = _get_bytes(
                url,
                timeout=30,
                accept="text/html,application/xhtml+xml",
            )
            html_text = html_bytes.decode("utf-8", errors="replace")
        except RuntimeError as e:
            msg = f"MFDS GMP inspection HTML page={page_no} 실패: {e}"
            if items:
                log("WARN", msg)
                page_warnings.append(msg)
                _set_last_health(
                    item_count=len(items),
                    parsed_rows=total_seen_rows,
                    parse_status_counts=parse_status_counts,
                    deficiency_counts=deficiency_counts,
                    manual_review_count=manual_review_count,
                    page_warnings=page_warnings,
                    pages_seen=pages_fetched,
                    deficiency_table=deficiency_table_health,
                )
                return items, None
            _set_last_health(
                item_count=0,
                parsed_rows=total_seen_rows,
                parse_status_counts=parse_status_counts,
                deficiency_counts=deficiency_counts,
                manual_review_count=manual_review_count,
                page_warnings=[msg],
                pages_seen=pages_fetched,
                deficiency_table=deficiency_table_health,
            )
            return [], msg

        rows = _parse_rows(html_text)
        total_seen_rows += len(rows)
        if not rows:
            msg = "MFDS GMP inspection HTML 테이블 행 미발견 — 구조 변경 가능성"
            if items or page_no > 1:
                log("WARN", msg)
                page_warnings.append(f"page={page_no}: {msg}")
                _set_last_health(
                    item_count=len(items),
                    parsed_rows=total_seen_rows,
                    parse_status_counts=parse_status_counts,
                    deficiency_counts=deficiency_counts,
                    manual_review_count=manual_review_count,
                    page_warnings=page_warnings,
                    pages_seen=pages_fetched,
                    deficiency_table=deficiency_table_health,
                )
                return items, None
            _set_last_health(
                item_count=0,
                parsed_rows=0,
                parse_status_counts=parse_status_counts,
                deficiency_counts=deficiency_counts,
                manual_review_count=manual_review_count,
                page_warnings=[msg],
                pages_seen=pages_fetched,
                deficiency_table=deficiency_table_health,
            )
            return [], msg

        pages_fetched += 1
        page_dates: list[date] = []
        for raw in rows:
            date_iso = _parse_date(raw.get("registered_date", ""))
            if date_iso:
                try:
                    page_dates.append(date.fromisoformat(date_iso))
                except ValueError:
                    pass
            if not _within_window(date_iso, start, end):
                continue
            item = _to_item(raw, url)
            if item is None or item.document_id in seen_ids:
                continue
            seen_ids.add(item.document_id)
            items.append(item)
            parse_status = str(item.raw_payload.get("attachment_parse_status") or "unknown")
            deficiency = str(item.raw_payload.get("attachment_deficiency_assessment") or "unknown")
            parse_status_counts[parse_status] = parse_status_counts.get(parse_status, 0) + 1
            deficiency_counts[deficiency] = deficiency_counts.get(deficiency, 0) + 1
            if item.raw_payload.get("manual_review_required"):
                manual_review_count += 1
            _tally_deficiency_table_health(deficiency_table_health, item)

        if page_dates and max(page_dates) < start:
            break
        page_no += 1

    if page_no > MAX_PAGES:
        msg = f"MFDS GMP inspection max_pages={MAX_PAGES} 도달 — 이후 항목 누락 가능"
        log("WARN", msg)
        page_warnings.append(msg)

    log(
        "INFO",
        "MFDS GMP inspection 수집 완료: "
        f"{len(items)}건 (parsed_rows={total_seen_rows})",
    )
    if items:
        log(
            "INFO",
            "MFDS GMP inspection attachment parse: "
            f"status={parse_status_counts} deficiency={deficiency_counts}",
        )
    if items and deficiency_table_health["enabled"]:
        log(
            "INFO",
            "MFDS GMP 지적 표: "
            f"attempted={deficiency_table_health['attempted']} "
            f"extracted={deficiency_table_health['extracted']} "
            f"failed={deficiency_table_health['failed']}",
        )
    _set_last_health(
        item_count=len(items),
        parsed_rows=total_seen_rows,
        parse_status_counts=parse_status_counts,
        deficiency_counts=deficiency_counts,
        manual_review_count=manual_review_count,
        page_warnings=page_warnings,
        pages_seen=pages_fetched,
        max_pages_reached=page_no > MAX_PAGES,
        deficiency_table=deficiency_table_health,
    )
    return items, None
