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
import zipfile
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any

import requests

from grm_common import DEFAULT_USER_AGENT, log, retry_after_seconds
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
    r"지적\s*사항\s*없음|보완\s*사항\s*없음|이상\s*없음)"
)
_DEFICIENCY_PRESENT_RE = re.compile(
    r"(지적\s*\(?보완\)?\s*사항\s*(?:\(Deficiencies\))?\s*있음|"
    r"지적\s*\(?보완\)?\s*사항\s*(?:\(Deficiencies\))?.{0,80}"
    r"(품질경영|시설장비|제조|시험실|원자재|포장표시|허가관리|위탁|밸리데이션))",
    re.S,
)


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
    bytes_downloaded: int = 0
    error: str = ""


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
    last_err: Exception | None = None
    for attempt in range(HTTP_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": accept,
                    "Referer": BOARD_URL,
                },
            )
            if resp.status_code == 429 and attempt < HTTP_RETRIES:
                sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                log("WARN", f"MFDS GMP inspection 429 url={url} sleep={sleep_s}s")
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return resp.content or b""
        except requests.RequestException as e:
            last_err = e
            if attempt < HTTP_RETRIES:
                log(
                    "WARN",
                    f"MFDS GMP inspection GET retry {attempt + 1}/{HTTP_RETRIES + 1} "
                    f"url={url} err={e}",
                )
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP GET final failure: {url} ({last_err})") from e


def _assess_deficiency(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return "unknown"
    if _NO_DEFICIENCY_RE.search(compact):
        return "none"
    if _DEFICIENCY_PRESENT_RE.search(compact):
        return "present"
    if "Deficiencies" in compact and "없음" not in compact:
        return "present"
    return "unknown"


def _extract_pdf_text(data: bytes) -> tuple[str, str]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return "", "pdf-parser-missing"
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
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
    return _AttachmentParse(
        status=status,
        file_format=file_format,
        text=text,
        deficiency=deficiency,
        bytes_downloaded=len(data),
    )


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
    _set_last_health(
        item_count=0,
        parsed_rows=0,
        parse_status_counts=parse_status_counts,
        deficiency_counts=deficiency_counts,
        manual_review_count=0,
        page_warnings=page_warnings,
        pages_seen=0,
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
    _set_last_health(
        item_count=len(items),
        parsed_rows=total_seen_rows,
        parse_status_counts=parse_status_counts,
        deficiency_counts=deficiency_counts,
        manual_review_count=manual_review_count,
        page_warnings=page_warnings,
        pages_seen=pages_fetched,
        max_pages_reached=page_no > MAX_PAGES,
    )
    return items, None
