#!/usr/bin/env python3
"""GRM WHO Prequalification Collector — P1 (글로벌 확장).

ENABLE_WHO=true 또는 --sources who 일 때 collect_intake.main() 에서 호출된다.

배경 (probe 2026-06-02):
  extranet.who.int/prequal 는 Drupal 10 **서버렌더링**. ICH 와 달리 NOC/WHOPIR 목록 항목이
  HTML 에 **inline** 으로 존재한다(링크 추출이 실제로 동작). 또한 공식 **RSS** 가 있다.

수집 채널 (제조/품질 직접 관련):
  1. RSS  : https://extranet.who.int/prequal/rss.xml — PQ 뉴스/공지/가이드라인 등 (날짜 있음)
  2. WHOPIR Medicines : 제조소(FPP/API) 공개 실사보고서 PDF 목록(제조소명·국가, 페이지네이션)
  3. NOC Medicines    : Notice of Concern (제조소 GMP 비순응) — 최고 신호

설계 역할:
  - RSS 는 날짜 기반 윈도우 수집(다른 RSS 소스와 동일).
  - WHOPIR/NOC 는 목록 스냅샷 + URL 기반 dedup → 새 보고서/공지가 새 후보로 표면화.
    날짜를 단언하기 어려우면 date_iso="" (Run Date 기준 intake).
  - official_url 은 항상 WHO 공식 PDF/페이지.
  - 핵심 목록 페이지가 0건이면 침묵하지 않고 error(구조 변경/렌더 변경 = 수동 확인).

대상 사용자(QA/QC/VAL/설비/DI 등)가 폭넓게 쓰도록, 명백한 임상/기기 전용만 배제하고
제조·품질 신호는 보수적으로 포함한다(최종 판정은 Routine).
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin

from grm_common import http_get_html, log
from collect_intake import (
    IntakeItem,
    SOURCE_WHO,
    SRC_TYPE_OFFICIAL_API,
    SRC_TYPE_OFFICIAL_PAGE,
    compute_relevance,
    compute_signal_tier,
    _within_window,
    _stable_doc_id,
    _rss2_items_from_root,
    _atom_entries_from_root,
    _rss_text,
    _atom_text,
    _atom_link,
    _parse_rss2_date,
    _parse_atom_date,
)
from grm_common import http_get_xml


TYPE_WHO_NEWS = "who-news"
TYPE_WHO_INSPECTION = "who-inspection"     # WHOPIR
TYPE_WHO_NOC = "who-noc"                    # Notice of Concern
LANGUAGE_EN = "EN"
REGION_WHO = "WHO (Global)"

WHO_RSS_URL = "https://extranet.who.int/prequal/rss.xml"
WHOPIR_MED_URL = ("https://extranet.who.int/prequal/inspection-services/"
                  "who-public-inspection-reports-whopirs-medicines")
NOC_MED_URL = ("https://extranet.who.int/prequal/inspection-services/"
               "notices-concern-nocs-medicines")

HTTP_RETRIES = 3
REQUEST_DELAY_SECONDS = 1.0
WHOPIR_MAX_PAGES = 8
MAX_TITLE_CHARS = 240

# 임상/기기 전용 등 명백히 무관한 것만 배제 (제조·품질은 보수적으로 포함)
_WHO_EXCLUDE = [
    "vector control", "pesticide", "male circumcision",
    "in vitro diagnostic", "ivd ", "snake antivenom",
]

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

_DATE_DMY_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b")
_DATE_MY_RE = re.compile(r"\b([A-Za-z]+)\s+(\d{4})\b")


class _LinkParser(HTMLParser):
    """<a href> + 앵커 텍스트 쌍 수집 (구조 비의존)."""

    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            if self._href is not None:
                self.links.append((self._href, " ".join(self._parts).strip()))
            self._href = (dict(attrs).get("href") or "").strip()
            self._parts = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._parts).strip()))
            self._href = None
            self._parts = []

    def handle_data(self, data):
        if self._href is not None:
            s = data.strip()
            if s:
                self._parts.append(s)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _excluded(blob: str) -> bool:
    b = blob.lower()
    return any(x in b for x in _WHO_EXCLUDE)


def _parse_text_date(text: str) -> str:
    """앵커 텍스트에서 'DD Month YYYY' 또는 'Month YYYY' → ISO. 실패 시 ''."""
    m = _DATE_DMY_RE.search(text or "")
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(1))).isoformat()
            except ValueError:
                pass
    m2 = _DATE_MY_RE.search(text or "")
    if m2:
        mon = _MONTHS.get(m2.group(1).lower())
        if mon:
            try:
                return date(int(m2.group(2)), mon, 1).isoformat()
            except ValueError:
                pass
    return ""


def _get_html(url: str, *, timeout: int = 30) -> str:
    return http_get_html(url, timeout=timeout, retries=HTTP_RETRIES, label="WHO")


def _links(html_text: str) -> list[tuple[str, str]]:
    p = _LinkParser()
    p.feed(html_text)
    return p.links


# ── 1) RSS ────────────────────────────────────────────────────────────────────
def _collect_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    log("INFO", f"WHO RSS 수집: {WHO_RSS_URL}")
    try:
        root = http_get_xml(WHO_RSS_URL)
    except Exception as e:  # noqa: BLE001
        return [], f"WHO RSS 실패: {e}"

    rss_items = _rss2_items_from_root(root)
    use_atom = not rss_items
    nodes = _atom_entries_from_root(root) if use_atom else rss_items

    items: list[IntakeItem] = []
    for node in nodes:
        if use_atom:
            title = _atom_text(node, "title")
            link = _atom_link(node)
            pub = _atom_text(node, "updated") or _atom_text(node, "published")
            date_iso = _parse_atom_date(pub) if pub else ""
            desc = _atom_text(node, "summary") or _atom_text(node, "content")
        else:
            title = _rss_text(node.find("title"))
            link = _rss_text(node.find("link"))
            pub = _rss_text(node.find("pubDate")) or _rss_text(node.find("pubdate"))
            date_iso = _parse_rss2_date(pub) if pub else ""
            desc = _rss_text(node.find("description"))
        title = _clean(title)
        if not title or not _within_window(date_iso, start, end):
            continue
        blob = f"{title} {desc}"
        if _excluded(blob):
            continue
        relevance = compute_relevance(title, desc)
        if relevance == "Pending":
            relevance = "Possible"   # WHO PQ 항목은 제조/품질 맥락 → 보수적으로 보존
        tier = compute_signal_tier(SOURCE_WHO, TYPE_WHO_NEWS, relevance, "N/A", title, desc)
        items.append(IntakeItem(
            source=SOURCE_WHO,
            document_id=_stable_doc_id(SOURCE_WHO, title, link, date_iso),
            date_iso=date_iso,
            headline=title[:MAX_TITLE_CHARS],
            official_url=link or WHO_RSS_URL,
            type_or_class=TYPE_WHO_NEWS,
            body=_clean(desc)[:1500],
            api_query=WHO_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_API,
            signal_tier=tier,
            raw_payload={"channel": "rss", "title": title, "link": link, "pubDate": pub},
            language=LANGUAGE_EN,
            region_jurisdiction=REGION_WHO,
        ))
    log("INFO", f"WHO RSS 완료: {len(items)}건")
    return items, None


# ── 2) WHOPIR (공개 실사보고서) ────────────────────────────────────────────────
def _collect_whopir(run_date: date) -> tuple[list[IntakeItem], str | None]:
    items: list[IntakeItem] = []
    seen: set[str] = set()
    for page in range(WHOPIR_MAX_PAGES):
        url = WHOPIR_MED_URL if page == 0 else f"{WHOPIR_MED_URL}?page={page}"
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            html_text = _get_html(url)
        except RuntimeError as e:
            if items:
                log("WARN", f"WHOPIR page={page} 실패(부분 수집 유지): {e}")
                break
            return [], f"WHO WHOPIR 수집 실패: {e}"
        page_links = [(h, t) for h, t in _links(html_text)
                      if "/whopir_files/" in h.lower() and h.lower().endswith(".pdf")]
        if not page_links:
            break  # 더 이상 보고서 없음 → 페이지네이션 종료
        new_on_page = 0
        for href, text in page_links:
            abs_url = urljoin(WHOPIR_MED_URL, href)   # 상대경로 → 절대 URL (Notion URL 속성 요건)
            if abs_url in seen:
                continue
            seen.add(abs_url)
            new_on_page += 1
            manuf = _clean(text) or abs_url.rsplit("/", 1)[-1]
            items.append(IntakeItem(
                source=SOURCE_WHO,
                document_id="who-whopir-" + hashlib.sha1(abs_url.encode()).hexdigest()[:12],
                date_iso=_parse_text_date(text),   # 본문에 날짜 있으면 사용, 없으면 ""(Run Date)
                headline=f"[WHOPIR] {manuf}"[:MAX_TITLE_CHARS],
                official_url=abs_url,              # WHO 공식 PDF (per-item, 절대 URL)
                type_or_class=TYPE_WHO_INSPECTION,
                firm=manuf[:200],
                body=("WHO 공개 실사보고서(WHOPIR) — 제조소/CRO/QCL GMP 실사. "
                      f"제조소: {manuf}\n출처: {WHOPIR_MED_URL}"),
                api_query=url,
                qa_relevance="Likely",
                osd_relevance="N/A",
                source_type=SRC_TYPE_OFFICIAL_PAGE,
                signal_tier="Tier 2",
                raw_payload={"channel": "whopir", "anchor_text": _clean(text),
                             "pdf_url": abs_url, "list_page": url},
                source_url=WHOPIR_MED_URL,
                language=LANGUAGE_EN,
                region_jurisdiction=REGION_WHO,
            ))
        if new_on_page == 0:
            break
    else:
        # for-else: break 없이 WHOPIR_MAX_PAGES 소진 = cap 도달(이후 페이지 누락 가능)
        log("WARN", f"WHO WHOPIR 페이지 cap({WHOPIR_MAX_PAGES}) 도달 — 이후 보고서 누락 가능")
    if not items:
        return [], f"WHO WHOPIR 0건({WHOPIR_MED_URL}) — 구조/렌더 변경 의심(수동 확인 필요)"
    log("INFO", f"WHO WHOPIR 완료: {len(items)}건")
    return items, None


# ── 3) NOC (Notice of Concern) ────────────────────────────────────────────────
_NODE_RE = re.compile(r"/prequal/node/\d+")
# B4: Drupal 이 /node/N 대신 path alias 를 쓰게 되는 드리프트 대비 — 'notice' 를
# 포함한 /prequal/ 경로도 후보로 수용. nav 의 'Notice of Concern' 메뉴류는 연도
# 게이트(항목 텍스트의 연도)가 걸러주므로 과수집 위험 낮음(2026-06-10 라이브 확인:
# 연도 텍스트 앵커 = NOC 엔트리뿐, nav 'notice' 링크들은 전부 연도 없음).
_NOC_ALIAS_RE = re.compile(r"/prequal/[^\s\"'<>]*notice", re.I)
_YEAR_TEXT_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _collect_noc(run_date: date) -> tuple[list[IntakeItem], str | None]:
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        html_text = _get_html(NOC_MED_URL)
    except RuntimeError as e:
        return [], f"WHO NOC 수집 실패: {e}"

    items: list[IntakeItem] = []
    seen: set[str] = set()
    seen_texts: set[str] = set()
    links = _links(html_text)
    for href, text in links:
        if not (_NODE_RE.search(href) or _NOC_ALIAS_RE.search(href)):
            continue
        t = _clean(text)
        if not t or not _YEAR_TEXT_RE.search(t):   # NOC 항목은 텍스트에 연도 포함(nav 메뉴 배제)
            continue
        abs_url = urljoin(NOC_MED_URL, href)          # 상대경로 → 절대 URL
        if abs_url in seen or t in seen_texts:        # node+alias 가 같은 NOC 가리킴 대비
            continue
        seen.add(abs_url)
        seen_texts.add(t)
        items.append(IntakeItem(
            source=SOURCE_WHO,
            document_id="who-noc-" + hashlib.sha1((abs_url + "|" + t).encode()).hexdigest()[:12],
            date_iso=_parse_text_date(t),
            headline=f"[WHO NOC] {t}"[:MAX_TITLE_CHARS],
            official_url=abs_url,
            type_or_class=TYPE_WHO_NOC,
            firm=t[:200],
            body=("WHO Notice of Concern — 제조소/CRO/QCL 의 중대 GMP 비순응 미해결 공지. "
                  f"대상: {t}\n출처: {NOC_MED_URL}"),
            api_query=NOC_MED_URL,
            qa_relevance="Likely",
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            signal_tier="Tier 3",            # GMP 비순응 = 최고 신호
            raw_payload={"channel": "noc", "anchor_text": t, "node_url": abs_url},
            source_url=NOC_MED_URL,
            language=LANGUAGE_EN,
            region_jurisdiction=REGION_WHO,
        ))
    if not items:
        # B4 구조 sentinel: '선택자 전건 탈락'과 '진짜 빈 목록'을 구분해 침묵 0건 금지.
        # NOC = Tier 3 최고신호(GMP 비순응)라 조용한 누락이 가장 위험하다.
        prequal_hrefs = [h for h, _ in links if "/prequal/" in h]
        if not prequal_hrefs:
            return [], (f"WHO NOC 페이지 렌더 이상(prequal 앵커 0, {NOC_MED_URL}) "
                        "— 구조/렌더 변경 의심(수동 확인 필요)")
        stray_year_anchors = [
            h for h, t in links
            if ("/prequal/" in h or "node/" in h.lower())
            and _YEAR_TEXT_RE.search(_clean(t))
        ]
        if stray_year_anchors:
            return [], (f"WHO NOC 선택자 0건 — 연도 텍스트 콘텐츠 앵커 "
                        f"{len(stray_year_anchors)}건이 패턴(/prequal/node/N·notice 별칭) "
                        f"밖({NOC_MED_URL}) — URL 스킴 변경 의심(수동 확인 필요)")
        # 페이지 정상 렌더 + 연도 콘텐츠 앵커 자체가 없음 = 진짜 빈 목록 → 0건 정상.
    log("INFO", f"WHO NOC 완료: {len(items)}건")
    return items, None


def collect_who(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """WHO 수집 진입점. (items, error_msg).

    - WHOPIR(핵심) 0건/실패 또는 RSS 실패는 error 로 올린다.
    - NOC 진짜 0건(빈 목록)은 정상. 단 페이지 실패·렌더 이상·선택자 전건 탈락은
      sentinel 이 error 로 올린다(B4) — NOC 도 core: Tier 3 최고신호의 침묵 누락 금지.
      네트워크성 블립은 health 단계에서 transient warning 강등(T1)이라 core 승격이
      일시 오류로 run 을 red 로 만들지 않는다.
    - 부분 실패라도 수집분은 반환(graceful), 단 핵심 실패 시 error 동반.
    """
    items: list[IntakeItem] = []
    errors: list[str] = []
    core_failed = False
    seen: set[str] = set()

    for fn, core in ((_collect_rss, True), (lambda s, e: _collect_whopir(end), True),
                     (lambda s, e: _collect_noc(end), True)):
        try:
            part, err = fn(start, end)
        except Exception as e:  # noqa: BLE001
            part, err = [], str(e)
        if err:
            log("WARN", f"WHO 부분 오류: {err}")
            errors.append(err)
            if core:
                core_failed = True
        for it in part:
            if it.document_id in seen:
                continue
            seen.add(it.document_id)
            items.append(it)

    if core_failed or (errors and not items):
        return items, "; ".join(errors) or "WHO 핵심 채널 수집 실패"
    log("INFO", f"WHO 수집 완료: {len(items)}건 (부분오류={len(errors)})")
    return items, None
