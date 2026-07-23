# -*- coding: utf-8 -*-
"""
MHRA GMDP Statement of Non-Compliance (SoNC) client.

Pure-requests (urllib) client for the UK MHRA public GMP register (Drupal
Facets). Reverse-engineered + validated 2026-07-23.

Contract (STATELESS — no session, no cookiejar, unlike EudraGMDP):
  1. GET  /mhra/gmp?f[0]=gmp_compliance:Non Compliant  -> result table
  2. parse rows -> (report_no, slug, country, inspection_date) per record
     (defensive pager: follow &page=N until a page yields no rows)
  3. GET  /mhra/gmp/<slug>                              -> full Statement of
     Non-Compliance (server-rendered, SESSION-INDEPENDENT — the detail URL is
     itself the durable official source; no PDF archival needed)

Why so much simpler than eudragmdp_client:
  - The EudraGMDP drilldown/PDF endpoints are session-stateful, so the report
    URL is not durable and the PDF had to be archived to Supabase Storage. The
    MHRA detail page is a plain GET that renders the statement verbatim, so we
    keep the detail URL as the official_url and skip all archival.

Design rules honored (see project memory):
  - Failure raises; never returns a silently-empty list.
  - No robots bypass, no TLS spoofing; plain UA.
  - Deterministic parsing off stable semantic anchors (Drupal field classes /
    <b> labels / cert markup).
  - The Part-3 statement fields (Withdrawal / Marketing authorisation action /
    Recall of batches / Prohibition of supply) VARY per record (validated live
    2026-07-23: Genovior has 4, Geno Pharma has 2). So the parser does NOT
    hard-code the action labels: it treats "Nature of non-compliance" as the
    mandatory gate and folds every OTHER <b>label:</b> pair into the action
    narrative. Robust to MHRA adding/removing an action field.
"""
from __future__ import annotations
import re
import html
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Optional

BASE = "https://cms.mhra.gov.uk"
GMP_PATH = "/mhra/gmp"
# f[0]=gmp_compliance:Non Compliant — the Drupal Facets filter (session-free).
SEARCH_URL = (
    BASE + GMP_PATH + "?f%5B0%5D=gmp_compliance%3ANon%20Compliant"
)
DETAIL_URL = BASE + GMP_PATH + "/{slug}"

DEFAULT_UA = "Mozilla/5.0 (compatible; GRM-Intake/1.1; +regulatory-monitoring)"
_MAX_PAGES = 20          # defensive pager cap (6 total entries as of 2026-07)
_NATURE_LABEL_RE = re.compile(r"nature of non[- ]?compliance", re.I)


class MHRAGmpError(RuntimeError):
    """Raised on any unrecoverable fetch/parse failure (never swallow to empty)."""


@dataclass
class MHRARecord:
    # from result list
    report_no: str
    slug: str
    detail_url: str
    country: str = ""              # WDA country column (site country, best-effort)
    inspection_date: str = ""      # ISO (from <time datetime>) — inspection last date
    # from detail (filled by fetch_detail)
    manufacturer: Optional[str] = None
    site_address: Optional[str] = None
    site_country: Optional[str] = None     # parsed from address tail
    authority: Optional[str] = None        # always MHRA / United Kingdom
    regulatory_basis: Optional[str] = None
    product_type: Optional[str] = None
    operations: Optional[str] = None
    restriction: Optional[str] = None
    nature: Optional[str] = None
    action: Optional[str] = None
    issue_date: Optional[str] = None       # ISO — statement/signature date
    detail_ok: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _clean(s: Optional[str]) -> str:
    if not s:
        return ""
    s = s.replace("&nbsp;", " ").replace("\xa0", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _ddmmyyyy_to_iso(s: str) -> str:
    """'27/03/2023' -> '2023-03-27'. Empty/unparseable -> ''."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    if not m:
        return ""
    d, mo, y = m.groups()
    return f"{y}-{mo}-{d}"


class MHRAGmpNCRClient:
    def __init__(self, user_agent: str = DEFAULT_UA, timeout: int = 60,
                 delay: float = 1.0, retries: int = 3):
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self.ua = user_agent

    # --- low-level ---
    def _get_text(self, url: str, referer: Optional[str] = None) -> str:
        headers = {"User-Agent": self.ua}
        if referer:
            headers["Referer"] = referer
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", "replace")
            except Exception as e:  # network/HTTP -> retry then surface
                last = e
                if attempt < self.retries:
                    time.sleep(self.delay * attempt)
        raise MHRAGmpError(f"request failed after {self.retries} tries: {url} :: {last!r}")

    # --- list parsing ---
    @staticmethod
    def _parse_rows(body: str) -> list[MHRARecord]:
        out: list[MHRARecord] = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
            m = re.search(
                r'views-field-title[^>]*>\s*<a[^>]*href="(/mhra/gmp/[^"]+)"[^>]*>(.*?)</a>',
                row, re.S)
            if not m:
                continue
            slug = m.group(1).rsplit("/", 1)[-1]
            report_no = _clean(m.group(2))
            # defensive: honour the compliance cell if present (URL already filters)
            comp = re.search(r'views-field-field-gmp-compliance[^>]*>(.*?)</td>', row, re.S)
            if comp and "non compliant" not in _clean(comp.group(1)).lower():
                continue
            country = ""
            cm = re.search(r'country-code[^>]*>(.*?)</td>', row, re.S)
            if cm:
                country = _clean(cm.group(1))
            insp = ""
            dm = re.search(r'inspection-last-date[^>]*>.*?datetime="([^"]+)"', row, re.S)
            if dm:
                insp = dm.group(1)[:10]        # ISO date part of the <time datetime>
            out.append(MHRARecord(
                report_no=report_no, slug=slug,
                detail_url=DETAIL_URL.format(slug=slug),
                country=country, inspection_date=insp))
        return out

    def list_noncompliant(self) -> list[MHRARecord]:
        """Return every Non-Compliant register entry (walks the pager).

        List-only. Detail fields are filled by fetch_detail(rec) afterwards —
        each detail page is a standalone GET, so (unlike EudraGMDP) records can
        be drilled in any order.
        """
        records: dict[str, MHRARecord] = {}
        for page in range(_MAX_PAGES):
            url = SEARCH_URL + (f"&page={page}" if page else "")
            body = self._get_text(url, referer=SEARCH_URL if page else None)
            rows = self._parse_rows(body)
            if not rows:
                break
            new = 0
            for r in rows:
                if r.slug not in records:
                    records[r.slug] = r
                    new += 1
            if not new:                        # pager looped / no fresh rows
                break
            if page + 1 < _MAX_PAGES:
                time.sleep(self.delay)
        return list(records.values())

    # --- detail parsing ---
    def fetch_detail(self, rec: MHRARecord) -> MHRARecord:
        body = self._get_text(rec.detail_url, referer=SEARCH_URL)
        if "STATEMENT OF NON-COMPLIANCE" not in body.upper():
            raise MHRAGmpError(
                f"detail for {rec.report_no} (slug={rec.slug}) is not a Statement "
                f"of Non-Compliance page — layout changed or wrong entry")

        m = re.search(r'manufacturer-address">(.*?)</span>', body, re.S)
        rec.manufacturer = _clean(m.group(1)) if m else None

        m = re.search(r'class="address"[^>]*>(.*?)</p>', body, re.S)
        if m:
            addr = _clean(m.group(1))
            rec.site_address = addr
            segs = [s.strip() for s in addr.split(",") if s.strip()]
            rec.site_country = segs[-1] if segs else (rec.country or None)
        else:
            rec.site_country = rec.country or None

        rec.authority = "Medicines and Healthcare products Regulatory Agency (United Kingdom)"

        m = re.search(r"Issued following an inspection in accordance with\s*:(.*?)</span>",
                      body, re.S)
        rec.regulatory_basis = _clean(m.group(1)) if m else None

        m = re.search(r"Product Type</caption>\s*<span>(.*?)</span>", body, re.S)
        rec.product_type = _clean(m.group(1)) if m else None

        rec.operations = self._parse_operations(body)

        m = re.search(r'space-gmp-restriction-field[^>]*>(.*?)</div>', body, re.S)
        if m:
            rest = _clean(m.group(1))
            rest = re.sub(r"^Restrictions or remarks:\s*", "", rest, flags=re.I)
            rec.restriction = rest or None

        # issue/signature date lives in the contact-details table (first cell).
        m = re.search(r'contact-details.*?<td[^>]*>\s*([0-9]{2}/[0-9]{4}|[0-9/]{8,10})',
                      body, re.S)
        rec.issue_date = _ddmmyyyy_to_iso(m.group(1)) if m else ""

        # inspection date fallback from the statement body if the list lacked it.
        if not rec.inspection_date:
            m = re.search(r"conducted on\s*<b><i>\s*([0-9/]{8,10})", body, re.S)
            if m:
                rec.inspection_date = _ddmmyyyy_to_iso(m.group(1))

        nature, action = self._parse_part3(body)
        rec.nature = nature
        rec.action = action
        rec.detail_ok = bool(nature)
        if not rec.detail_ok:
            raise MHRAGmpError(
                f"detail for {rec.report_no} (slug={rec.slug}) missing mandatory "
                f"'Nature of non-compliance' — layout changed")
        return rec

    @staticmethod
    def _parse_operations(body: str) -> Optional[str]:
        m = re.search(r"MANUFACTURING OPERATIONS(.*?)</table>", body, re.S)
        if not m:
            return None
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)]
        cells = [c for c in cells if c]
        return "; ".join(cells) if cells else None

    @staticmethod
    def _parse_part3(body: str) -> tuple[Optional[str], Optional[str]]:
        """Return (nature, action). Nature = the gated field; action = every
        other <b>label:</b> value folded into a labelled narrative.

        Splitting purely on <b> markers (not <span>) is deliberate: the value
        sometimes sits inside the label's own <span>, sometimes in the NEXT
        <span>, and sometimes in a following nested <div> (validated live
        2026-07-23) — <b> boundaries survive all three. The container itself is
        captured up to the stable "Teleconference Details" caption that always
        closes Part 3, NOT to the first </div> (Part 3 nests <div>s)."""
        m = re.search(
            r'class="info-container">(.*?)'
            r'(?:<caption[^>]*>\s*Teleconference|class="contact-details"'
            r'|class="add-ons"|</body>)',
            body, re.S)
        if not m:
            m = re.search(r'class="info-container">(.*)', body, re.S)
            if not m:
                return None, None
        inner = m.group(1)
        nature: Optional[str] = None
        action_parts: list[str] = []
        for chunk in re.split(r"<b>", inner)[1:]:
            lm = re.match(r"(.*?)</b>(.*)", chunk, re.S)
            if not lm:
                continue
            label = _clean(lm.group(1)).rstrip(":").strip()
            value = _clean(lm.group(2))
            if not label:
                continue
            if _NATURE_LABEL_RE.search(label):
                nature = value or nature
            elif value:
                action_parts.append(f"{label}: {value}")
        action = "\n".join(action_parts) if action_parts else None
        return nature, action


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    c = MHRAGmpNCRClient(delay=0.7)
    recs = c.list_noncompliant()
    print(f"non-compliant entries: {len(recs)}")
    n_ok = 0
    for rec in recs:
        try:
            c.fetch_detail(rec)
            n_ok += rec.detail_ok
        except MHRAGmpError as e:
            print(f"  FAIL {rec.report_no}: {e}")
            continue
        print(f"  {rec.report_no:34} {(rec.manufacturer or '')[:26]:26} "
              f"{(rec.site_country or ''):10} insp={rec.inspection_date} "
              f"issue={rec.issue_date} nature={len(rec.nature or '')}c "
              f"action={len(rec.action or '')}c")
    print(f"detail_ok {n_ok}/{len(recs)}")
