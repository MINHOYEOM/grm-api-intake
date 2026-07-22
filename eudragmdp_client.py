# -*- coding: utf-8 -*-
"""
EudraGMDP GMP Non-Compliance Report (NCR) client.

Pure-requests (urllib) client for the EMA EudraGMDP public GMP Non-Compliance
search (Struts `.do` app). Reverse-engineered + validated 2026-07-22.

Contract (single cookiejar session):
  1. GET  searchGMPNonCompliance.do        -> jsessionid cookie
  2. POST (formid=frmGMPCSearch, fromDate, toDate, btnSearchGMPNC) -> load results into session
  3. parse 11-column result rows -> doc_ref per record
  4. GET  ...&action=Page&param=N          -> page N (0-based), 10/page
  5. GET  ...&action=Drilldown&param=<ref> -> full Statement of Non-Compliance (SESSION-STATEFUL)
  6. POST generateGMPCPDF.do (after drilldown) -> application/pdf (durable archive source)

Design rules honored (see project memory):
  - Failure raises; never returns a silently-empty list.
  - No robots bypass, no TLS spoofing; plain UA.
  - Deterministic parsing off stable semantic anchors (ids / <b> labels).
"""
from __future__ import annotations
import re
import html
import time
import urllib.request
import urllib.parse
import http.cookiejar
from dataclasses import dataclass, field, asdict
from typing import Iterator, Optional

ROOT = "https://eudragmdp.ema.europa.eu/inspections/gmpc/"
SEARCH_URL = ROOT + "searchGMPNonCompliance.do"
PDF_URL = ROOT + "generateGMPCPDF.do"
PAGE_URL = SEARCH_URL + "?ctrl=searchGMPNCResultControlList&action=Page&param={n}"
DRILL_URL = SEARCH_URL + "?ctrl=searchGMPNCResultControlList&action=Drilldown&param={ref}"

DEFAULT_UA = "Mozilla/5.0 (compatible; GRM-Intake/1.1; +regulatory-monitoring)"
PAGE_SIZE = 10


class EudraGMDPError(RuntimeError):
    """Raised on any unrecoverable fetch/parse failure (never swallow to empty)."""


@dataclass
class NCRRecord:
    # from result list
    report_no: str
    doc_ref: str
    mia_number: str
    site_name: str
    site_address: str
    oms_location: str
    city: str
    postcode: str
    country: str
    inspection_end_date: str
    issue_date: str
    # 0-based index of the result page this record appears on (needed because
    # drilldown only resolves a doc_ref that is on the CURRENTLY active page).
    page_index: int = 0
    # from drilldown detail (filled by fetch_detail)
    doc_title: Optional[str] = None
    authority_country: Optional[str] = None
    product_scope: Optional[str] = None
    operations: Optional[str] = None
    nature: Optional[str] = None
    action: Optional[str] = None
    additional: Optional[str] = None
    detail_ok: bool = False
    # pdf (filled by fetch_pdf)
    pdf_bytes: Optional[bytes] = field(default=None, repr=False)

    def as_dict(self, include_pdf: bool = False) -> dict:
        d = asdict(self)
        if not include_pdf:
            d.pop("pdf_bytes", None)
        return d


def _clean(s: str) -> str:
    if s is None:
        return ""
    s = s.replace("&#13;", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


class EudraGMDPClient:
    def __init__(self, user_agent: str = DEFAULT_UA, timeout: int = 60,
                 delay: float = 1.0, retries: int = 3):
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self.ua = user_agent
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj))
        self._opener.addheaders = [("User-Agent", user_agent)]
        self._session_started = False

    # --- low-level ---
    def _open(self, url: str, data: Optional[bytes] = None,
              referer: Optional[str] = None, expect_binary: bool = False):
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if referer:
            headers["Referer"] = referer
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers)
                resp = self._opener.open(req, timeout=self.timeout)
                raw = resp.read()
                return raw, resp
            except Exception as e:  # network/HTTP -> retry then surface
                last = e
                if attempt < self.retries:
                    time.sleep(self.delay * attempt)
        raise EudraGMDPError(f"request failed after {self.retries} tries: {url} :: {last!r}")

    def _get_text(self, url, referer=None):
        raw, _ = self._open(url, referer=referer)
        return raw.decode("utf-8", "replace")

    # --- session / search ---
    def start_session(self) -> None:
        self._get_text(SEARCH_URL)
        self._session_started = True

    def _search_window(self, from_date: str, to_date: str) -> str:
        """POST a date window; returns page-0 HTML. Loads results into session."""
        if not self._session_started:
            self.start_session()
        payload = urllib.parse.urlencode({
            "formid": "frmGMPCSearch",
            "fromDate": from_date,
            "toDate": to_date,
            "btnSearchGMPNC": "",
        }).encode()
        raw, _ = self._open(SEARCH_URL, data=payload, referer=SEARCH_URL)
        return raw.decode("utf-8", "replace")

    # --- parsing ---
    @staticmethod
    def _parse_total(body: str) -> Optional[int]:
        m = re.search(r"\d+\s+to\s+\d+\s+of\s+([\d,]+)", body)
        return int(m.group(1).replace(",", "")) if m else None

    @staticmethod
    def _parse_rows(body: str) -> list[NCRRecord]:
        out = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
            cells = [_clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) == 11 and re.search(r"\d", cells[0]) and cells[3]:
                out.append(NCRRecord(
                    report_no=cells[0], doc_ref=cells[1], mia_number=cells[2],
                    site_name=cells[3], site_address=cells[4], oms_location=cells[5],
                    city=cells[6], postcode=cells[7], country=cells[8],
                    inspection_end_date=cells[9], issue_date=cells[10]))
        return out

    @staticmethod
    def _page_count(total: Optional[int]) -> int:
        if not total:
            return 1
        return (total + PAGE_SIZE - 1) // PAGE_SIZE

    def list_window(self, from_date: str, to_date: str) -> list[NCRRecord]:
        """Return ALL records in [from_date, to_date] by walking every page.

        List-only (no drilldown). Detail/PDF CANNOT be fetched from these records
        afterwards, because drilldown only resolves a doc_ref on the currently
        active page — use collect() for detail/PDF, which interleaves per page.
        """
        first = self._search_window(from_date, to_date)
        total = self._parse_total(first)
        records: dict[str, NCRRecord] = {}
        for r in self._parse_rows(first):
            r.page_index = 0
            records.setdefault(r.doc_ref, r)
        for n in range(1, self._page_count(total)):
            time.sleep(self.delay)
            body = self._get_text(PAGE_URL.format(n=n), referer=SEARCH_URL)
            page_rows = self._parse_rows(body)
            if not page_rows:
                break
            for r in page_rows:
                r.page_index = n
                records.setdefault(r.doc_ref, r)
        return list(records.values())

    # --- detail (session-stateful; requires the record's list loaded first) ---
    def fetch_detail(self, rec: NCRRecord) -> NCRRecord:
        body = self._get_text(DRILL_URL.format(ref=rec.doc_ref), referer=SEARCH_URL)
        m = re.search(r'id="documentTitle"[^>]*>(.*?)</p>', body, re.S)
        rec.doc_title = _clean(m.group(1)) if m else None
        m = re.search(r"Competent Authority of</span>\s*</span>\s*([A-Za-z .\-]+?)</td>", body)
        rec.authority_country = _clean(m.group(1)) if m else None
        m = re.search(r'class="subsectionContent">([^<]*Medicinal Products[^<]*)</span>', body)
        rec.product_scope = _clean(m.group(1)) if m else None
        m = re.search(r"(1 NON-COMPLIANT MANUFACTURING OPERATIONS.*?)</div>", body, re.S)
        rec.operations = _clean(m.group(1)) if m else None
        rec.nature = self._label(body, "Nature of non-compliance:")
        rec.action = self._label(body, "Action taken/proposed by the NCA:")
        rec.additional = self._label(body, "Additional comments:")
        # detail is valid only if the mandatory narratives are present
        rec.detail_ok = bool(rec.nature and rec.action)
        if not rec.detail_ok:
            raise EudraGMDPError(
                f"drilldown for {rec.report_no} (ref={rec.doc_ref}) missing mandatory "
                f"nature/action — session may be stale or layout changed")
        return rec

    @staticmethod
    def _label(body: str, label: str) -> Optional[str]:
        m = re.search(r"<b>\s*" + re.escape(label) + r"\s*</b>(.*?)</td>", body, re.S)
        return _clean(m.group(1)) if m else None

    # --- pdf archive (session-stateful; call immediately after fetch_detail) ---
    def fetch_pdf(self, rec: NCRRecord) -> bytes:
        # ensure this record is the active drilldown in session
        self._get_text(DRILL_URL.format(ref=rec.doc_ref), referer=SEARCH_URL)
        payload = urllib.parse.urlencode({
            "btnPrintGMPC": "clicked", "exclTeleconInfo": "",
            "btnBackToList": "", "fromwhere": "NCR",
        }).encode()
        raw, resp = self._open(PDF_URL, data=payload, referer=SEARCH_URL, expect_binary=True)
        ct = resp.headers.get("Content-Type", "")
        if not raw[:5] == b"%PDF-":
            raise EudraGMDPError(
                f"PDF fetch for {rec.report_no} did not return a PDF (ct={ct}, "
                f"magic={raw[:8]!r})")
        rec.pdf_bytes = raw
        return raw

    # --- high-level ---
    def iter_pages(self, from_date: str, to_date: str) -> Iterator[tuple[int, list[NCRRecord]]]:
        """Yield (page_index, rows) with that page ACTIVE (drilldown-ready).

        The generator advances to page N+1 (GET param=N+1) only when the caller
        requests the next item — i.e. AFTER it has drilled every row of page N.
        This preserves the interleaving invariant (drilldown resolves a doc_ref
        only against the currently active page; validated live 2026-07-22) while
        letting the caller wrap each record's detail/PDF fetch in its own
        try/except for per-record resilience.
        """
        first = self._search_window(from_date, to_date)
        total = self._parse_total(first)
        yield 0, self._parse_rows(first)
        for n in range(1, self._page_count(total)):
            body = self._get_text(PAGE_URL.format(n=n), referer=SEARCH_URL)
            page_rows = self._parse_rows(body)
            if not page_rows:
                break
            yield n, page_rows

    def collect(self, from_date: str, to_date: str, *, with_detail: bool = True,
                with_pdf: bool = True) -> Iterator[NCRRecord]:
        """Convenience wrapper over iter_pages that raises on the first bad record.

        Prefer iter_pages() + per-record try/except in production collectors so a
        single malformed statement does not abort the whole window.
        """
        seen: set[str] = set()
        for n, page_rows in self.iter_pages(from_date, to_date):
            for rec in page_rows:
                if rec.doc_ref in seen:
                    continue
                seen.add(rec.doc_ref)
                rec.page_index = n
                if with_detail:
                    self.fetch_detail(rec)
                if with_pdf:
                    self.fetch_pdf(rec)
                time.sleep(self.delay)
                yield rec


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    c = EudraGMDPClient(delay=0.7)
    recs = c.list_window("2026-04-01", "2026-07-22")
    print(f"window records: {len(recs)}")
    n_detail = n_pdf = 0
    for rec in recs[:4]:
        c.fetch_detail(rec)
        pdf = c.fetch_pdf(rec)
        n_detail += rec.detail_ok
        n_pdf += pdf[:5] == b"%PDF-"
        print(f"  {rec.report_no:26} {rec.site_name[:26]:26} {rec.country:14} "
              f"issue={rec.issue_date} nature={len(rec.nature or '')}c "
              f"action={len(rec.action or '')}c pdf={len(pdf)//1024}KB "
              f"auth={rec.authority_country}")
    print(f"detail_ok {n_detail}/4  pdf_ok {n_pdf}/4")
