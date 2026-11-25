#!/usr/bin/env python3
"""EMA 원문(기사 HTML·문서 PDF) → 전문 텍스트. 일일 수집기와 빈칸 백필이 함께 부르는 단일 경로.

[11-25 #8] EMA 발췌 빈칸의 대부분(최근 60일 72건 중 46건, 60일 밖 60건)은 원문이 PDF 문서
(`/en/documents/...`·`/system/files/...` — 가이드라인·컨셉 페이퍼·Q&A)였다. 수집기와 백필이 둘 다
HTML `<p>` 만 읽어서 PDF 는 빈 값이 됐다. 이 모듈이 그 판독을 한곳에서 한다.

대표 결정(11-25): **전문은 전부 저장한다. 저장 단계에서는 자르지 않고, 줄이는 건 화면에서만 한다.**
  · 저장 키는 기존 `article_excerpt` 그대로다. 카드 스캐폴드(`card_scaffold`)·원문 확보 신호
    (`_has_source_body`)·판 비교(`raw_update`)가 이미 이 키를 읽는다. 화면·브리프 입력은 스캐폴드가
    300자로 줄인다(표시 단계).
  · 한 칸이 `PART_CHARS` 를 넘으면 잘라 버리지 않고 `article_excerpt_parts`(이어지는 조각 목록)에
    나눠 담는다. 전문이 필요한 곳은 `fulltext(raw)` 로 다시 잇는다. `apply_to_raw` 가 이어 붙인
    결과가 판독 전문과 같은지 확인하고, 다르면 저장하지 않는다(예외).
  · 판독 메타(형식·쪽수·글자 수·상태)는 `ema_document` 에 남긴다. 실패도 사유를 남긴다 —
    "못 받았다"와 "원문에 없다"를 구분한다.

PDF 판독은 저장소의 기존 엔진을 그대로 쓴다: 텍스트층은 `collect_mfds_gmp_inspection._extract_pdf_text`
(PyMuPDF, 상한 없이 호출), 텍스트층이 빈 스캔본만 `collect_fda_483._ocr_483_pdf_text`(PyMuPDF 내장
tesseract OCR). 새 의존성·유료 API 없음.

요청 간격·429 재시도는 #1938 값(`grm_http_policy`: 2초, Retry-After 없으면 30·60초)이다.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from html import unescape
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from grm_http_policy import BACKFILL_429_RETRY_WAITS_SECONDS, BACKFILL_ARTICLE_DELAY_SECONDS

TEXT_KEY = "article_excerpt"
PARTS_KEY = "article_excerpt_parts"
META_KEY = "ema_document"

# 한 문자열 칸에 담는 최대 글자 수. jsonb 문자열 자체의 한도(≈256MB)보다 한참 작게 잡아, 판 기록
# RPC 한 요청 안의 한 값이 지나치게 커지지 않게 한다. 넘으면 **나눠 담는다**(자르지 않는다).
PART_CHARS = 1_000_000
FETCH_TIMEOUT = 60
USER_AGENT = "GRM-Intake/1.1 (+github-actions)"
_EMA_HOSTS = ("ema.europa.eu", "www.ema.europa.eu")
_DOC_PATH_PREFIXES = ("/en/documents/", "/system/files/", "/documents/", "/docs/")

STATUS_HTML_OK = "html-ok"
STATUS_PDF_OK = "pdf-ok"
STATUS_PDF_OCR = "pdf-ok-ocr"


@dataclass(frozen=True)
class EmaDocument:
    """판독 결과. text 가 비면 status 가 사유다."""
    url: str
    text: str
    status: str
    format: str = ""          # "pdf" | "html" | ""(못 받음)
    pages: int = 0
    extracted_chars: int = 0  # 엔진이 원문에서 뽑은 글자 수(저장 전문과 같아야 한다)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


def is_document_url(url: str) -> bool:
    """EMA 문서(PDF) 링크인가 — 경로 접두나 .pdf 확장자. 판독 경로 선택은 응답 본문으로 한다."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return False
    if (p.hostname or "").lower() not in _EMA_HOSTS:
        return False
    path = p.path.lower()
    return path.endswith(".pdf") or path.startswith(_DOC_PATH_PREFIXES)


def extract_html_text(html_text: str) -> str:
    """수집기의 `<p>` 선택 규칙 그대로, 길이 상한 없이."""
    text = html_text or ""
    text = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", text)
    parts: list[str] = []
    for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", text):
        t = re.sub(r"(?s)<[^>]+>", " ", p)
        t = re.sub(r"\s+", " ", unescape(t)).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def pdf_page_count(data: bytes) -> int:
    try:
        import fitz  # type: ignore[import-not-found]
        with fitz.open(stream=data, filetype="pdf") as doc:
            return int(doc.page_count)
    except Exception:  # noqa: BLE001 — 쪽수는 보고용
        return 0


def extract_pdf_text(data: bytes) -> tuple[str, str, int]:
    """PDF bytes → (전문, status, 쪽수). 상한 없음. 텍스트층이 비면 그때만 OCR."""
    from collect_mfds_gmp_inspection import _extract_pdf_text
    pages = pdf_page_count(data)
    text, status = _extract_pdf_text(data, max_chars=None)
    if text.strip():
        return text, STATUS_PDF_OK, pages
    if status != "scan-no-text":
        return "", status, pages
    try:
        from collect_fda_483 import _ocr_483_pdf_text, _ocr_enabled
    except Exception as e:  # noqa: BLE001
        return "", f"scan-no-text:ocr-missing:{type(e).__name__}", pages
    if not _ocr_enabled():
        return "", "scan-no-text:ocr-off", pages
    ocr_text, ocr_status = _ocr_483_pdf_text(data, max_chars=10 ** 9)
    if ocr_text.strip():
        return ocr_text, STATUS_PDF_OCR, pages
    return "", ocr_status or "scan-no-text", pages


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def extract_docx_text(data: bytes) -> str:
    """Word(.docx) → 문단별 전문(표준 라이브러리만). 본문·머리말 순서 그대로."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    paras = []
    for p in root.iter(f"{_W}p"):
        t = "".join(x.text or "" for x in p.iter(f"{_W}t")).strip()
        if t:
            paras.append(t)
    return "\n".join(paras)


def extract_xlsx_text(data: bytes) -> str:
    """Excel(.xlsx) → 시트별 행(칸은 탭)으로 전문. 모든 시트·모든 행."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
        sheets = sorted((n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        lines: list[str] = []
        for name in sheets:
            for row in ET.fromstring(zf.read(name)).iter(f"{_S}row"):
                cells = []
                for c in row.iter(f"{_S}c"):
                    v = c.find(f"{_S}v")
                    if c.get("t") == "s" and v is not None and (v.text or "").isdigit():
                        idx = int(v.text)
                        cells.append(shared[idx] if idx < len(shared) else "")
                    elif c.get("t") == "inlineStr":
                        cells.append("".join(t.text or "" for t in c.iter(f"{_S}t")))
                    else:
                        cells.append(v.text if v is not None and v.text else "")
                line = "\t".join(cells).strip()
                if line:
                    lines.append(line)
    return "\n".join(lines)


def _office_kind(data: bytes) -> str:
    if data[:4] != b"PK\x03\x04":
        return ""
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return ""
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return ""


def _retry_wait(resp: Any, attempt: int) -> float:
    value = str((getattr(resp, "headers", None) or {}).get("Retry-After") or "").strip()
    if value.isdigit():
        return min(float(value), 300.0)
    return BACKFILL_429_RETRY_WAITS_SECONDS[attempt]


def get_with_retry(url: str, *, accept: str = "text/html",
                   sleeper: Callable[[float], None] | None = None,
                   timeout: float = FETCH_TIMEOUT) -> Any:
    """429 는 Retry-After(없으면 30·60초)만큼 쉬고 두 번까지 다시 묻는다(#1938)."""
    resp = None
    for attempt in range(len(BACKFILL_429_RETRY_WAITS_SECONDS) + 1):
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": accept})
        if resp.status_code != 429 or attempt == len(BACKFILL_429_RETRY_WAITS_SECONDS):
            return resp
        (sleeper or time.sleep)(_retry_wait(resp, attempt))
    return resp


def _is_pdf(resp: Any, url: str) -> bool:
    content = getattr(resp, "content", b"") or b""
    if isinstance(content, (bytes, bytearray)) and content[:5] == b"%PDF-":
        return True
    ctype = str((getattr(resp, "headers", None) or {}).get("Content-Type") or "").lower()
    return "application/pdf" in ctype


def read_ema_fulltext(url: str, *, delay: bool = True,
                      sleeper: Callable[[float], None] | None = None) -> EmaDocument:
    """EMA 원문 URL → 전문. 응답 본문으로 고른다: PDF·Word(.docx)·Excel(.xlsx)·HTML `<p>` 결합. 실패는 graceful.

    delay — 요청 전 예의 간격(2초). 수집기·백필 모두 켠다(시험만 끈다).
    """
    url = (url or "").strip()
    if delay:
        (sleeper or time.sleep)(BACKFILL_ARTICLE_DELAY_SECONDS)
    accept = "application/pdf,*/*;q=0.8" if is_document_url(url) else "text/html"
    try:
        resp = get_with_retry(url, accept=accept, sleeper=sleeper)
    except requests.RequestException as e:
        return EmaDocument(url, "", f"fetch-fail:{type(e).__name__}")
    code = int(getattr(resp, "status_code", 0) or 0)
    if code >= 400:
        return EmaDocument(url, "", f"http-{code}")
    if _is_pdf(resp, url):
        data = bytes(resp.content or b"")
        text, status, pages = extract_pdf_text(data)
        return EmaDocument(url, text, status, "pdf", pages, len(text))
    content = getattr(resp, "content", b"") or b""
    kind = _office_kind(bytes(content)) if isinstance(content, (bytes, bytearray)) else ""
    if kind:
        try:
            text = (extract_docx_text if kind == "docx" else extract_xlsx_text)(bytes(content))
        except Exception as e:  # noqa: BLE001 — 깨진 파일도 수집을 멈추지 않는다
            return EmaDocument(url, "", f"{kind}-parse-fail:{type(e).__name__}", kind)
        if not text.strip():
            return EmaDocument(url, "", f"{kind}-no-text", kind)
        return EmaDocument(url, text, f"{kind}-ok", kind, 0, len(text))
    text = extract_html_text(getattr(resp, "text", "") or "")
    if not text:
        status = "html-no-paragraph"
        if is_document_url(url):
            status = "document-not-pdf"
        return EmaDocument(url, "", status, "html")
    return EmaDocument(url, text, STATUS_HTML_OK, "html", 0, len(text))


def split_parts(text: str, part_chars: int = PART_CHARS) -> list[str]:
    if part_chars <= 0:
        raise ValueError("part_chars must be positive")
    return [text[i:i + part_chars] for i in range(0, len(text), part_chars)] or [""]


def fulltext(raw: dict[str, Any]) -> str:
    """저장된 전문을 다시 잇는다(첫 칸 + 이어지는 조각)."""
    head = raw.get(TEXT_KEY) or ""
    rest = raw.get(PARTS_KEY) or []
    if not isinstance(head, str):
        return ""
    if not isinstance(rest, list):
        return head
    return head + "".join(p for p in rest if isinstance(p, str))


def meta(doc: EmaDocument, *, parts: int = 1) -> dict[str, Any]:
    out: dict[str, Any] = {"status": doc.status, "format": doc.format, "url": doc.url}
    if doc.ok:
        out.update({
            "chars": len(doc.text), "pages": doc.pages, "parts": parts,
            "sha256": hashlib.sha256(doc.text.encode("utf-8")).hexdigest(),
        })
    return out


def raw_patch(doc: EmaDocument, part_chars: int = PART_CHARS) -> dict[str, Any]:
    """raw_json 에 더할 키. 판독 실패면 사유 메타만(본문 키는 달지 않는다)."""
    if not doc.ok:
        return {META_KEY: meta(doc)}
    parts = split_parts(doc.text, part_chars)
    patch: dict[str, Any] = {TEXT_KEY: parts[0], META_KEY: meta(doc, parts=len(parts))}
    if len(parts) > 1:
        patch[PARTS_KEY] = parts[1:]
    # 저장 게이트: 이어 붙인 값이 판독 전문과 다르거나 엔진 추출보다 짧으면 저장하지 않는다.
    stored = fulltext(patch)
    if stored != doc.text or len(stored) < doc.extracted_chars:
        raise ValueError(f"EMA 전문 저장 불일치: stored={len(stored)} extracted={doc.extracted_chars}")
    return patch


def apply_to_raw(raw: dict[str, Any], doc: EmaDocument, part_chars: int = PART_CHARS) -> dict[str, Any]:
    """raw(dict)를 제자리에서 갱신하고 더한 키를 돌려준다. 이미 본문이 있으면 메타도 손대지 않는다."""
    if fulltext(raw).strip():
        return {}
    patch = raw_patch(doc, part_chars)
    raw.update(patch)
    return patch


def blank_document_gap(source_url: str, raw: dict[str, Any]) -> str:
    """EMA 문서 링크 행의 본문이 비었으면 사유(없으면 ''). 감시·시험이 같은 판정을 쓴다."""
    if not is_document_url(source_url) or fulltext(raw).strip():
        return ""
    m = raw.get(META_KEY)
    if isinstance(m, dict) and m.get("status"):
        return str(m["status"])
    return "unmarked"
