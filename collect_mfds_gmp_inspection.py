#!/usr/bin/env python3
"""GRM MFDS GMP Inspection Result Collector - Phase 2d.

Collects metadata from nedrug's public "의약품등 GMP 실사 결과공개"
HTML board. Attachment body parsing (PDF/HWP) is intentionally deferred.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any

import requests

from grm_common import DEFAULT_USER_AGENT, log
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


@dataclass
class _Cell:
    text: str = ""
    doc_id: str = ""


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


def _body(raw: dict[str, str]) -> str:
    parts = [
        f"제조소명: {raw.get('manufacturer', '')}",
        f"소재지: {raw.get('address', '')}",
        f"국가: {raw.get('country', '')}",
        f"구분: {raw.get('before_after', '')} / {raw.get('product_type', '')}",
        f"실사일자: {raw.get('inspection_start', '')} ~ {raw.get('inspection_end', '')}",
        f"등록일: {raw.get('registered_date', '')}",
        f"실사결과 다운로드: {_download_url(raw.get('doc_id', ''))}",
    ]
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
    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=f"gmpinspect-{doc_id}",
        date_iso=registered_date,
        headline=headline,
        official_url=BOARD_URL,
        type_or_class=TYPE_GMP_INSPECTION,
        firm=manufacturer,
        body=_body(raw),
        api_query=api_query_url,
        qa_relevance="Possible",
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_SCRAPE,
        signal_tier="Tier 2",
        raw_payload={"source": "nedrug CCBBD03", **raw, "download_url": download_url},
        source_url=download_url,
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
    )


def collect_mfds_gmp_inspections(
    start: date,
    end: date,
) -> tuple[list[IntakeItem], str | None]:
    """Collect GMP inspection result metadata from nedrug's public board."""
    items: list[IntakeItem] = []
    seen_ids: set[str] = set()
    page_no = 1
    total_seen_rows = 0

    while page_no <= MAX_PAGES:
        url = _request_url(page_no)
        try:
            resp = requests.get(
                url,
                timeout=30,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            if resp.status_code == 403:
                raise RuntimeError("HTTP 403")
            resp.raise_for_status()
        except requests.RequestException as e:
            msg = f"MFDS GMP inspection HTML page={page_no} 실패: {e}"
            if items:
                log("WARN", msg)
                return items, None
            return [], msg
        except RuntimeError as e:
            msg = f"MFDS GMP inspection HTML page={page_no} 실패: {e}"
            if items:
                log("WARN", msg)
                return items, None
            return [], msg

        rows = _parse_rows(resp.text)
        total_seen_rows += len(rows)
        if not rows:
            msg = "MFDS GMP inspection HTML 테이블 행 미발견 — 구조 변경 가능성"
            if items or page_no > 1:
                log("WARN", msg)
                return items, None
            return [], msg

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

        if page_dates and max(page_dates) < start:
            break
        page_no += 1

    if page_no > MAX_PAGES:
        log("WARN", f"MFDS GMP inspection max_pages={MAX_PAGES} 도달 — 이후 항목 누락 가능")

    log(
        "INFO",
        "MFDS GMP inspection 수집 완료: "
        f"{len(items)}건 (parsed_rows={total_seen_rows})",
    )
    return items, None
