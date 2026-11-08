#!/usr/bin/env python3
"""FDA 수입경보(Import Alert) 66-40 · 66-41 Red List 수집기 — 독립 경로(일일 intake 무접촉).

무엇: FDA 가 "무검사 자동억류(DWPE)" 대상으로 올린 해외 제조소 목록이다. GRM 의 다른 소스가
"과거에 일어난 사건"이라면 이것은 "오늘 현재 미국 수입이 막혀 있는 제조소"라는 **상태**다.
  - 66-40 = Drug GMP 미충족 제조소 (https://www.accessdata.fda.gov/cms_ia/importalert_189.html)
  - 66-41 = 미승인 신약 판매 제조소 (https://www.accessdata.fda.gov/cms_ia/importalert_190.html)
타당성 판정: docs/신규소스_수입경보_FDA대시보드_EDQM_타당성_2026-08-11.md §2(조건부 채택).

왜 독립 경로인가: 일일 intake 에 붙이려면 collect_intake·card_scaffold·findings_extractors·grm-intake.yml
을 고쳐야 하고, 그 파일들은 다른 세션이 상시 만진다. 서술(위반 사유)이 없어 findings 스키마와도 목적이
다르다(§2.5) → 전용 경량 테이블(web/migrations/132_fda_import_alert.sql)에 PostgREST 로 직행한다.

반대 근거를 코드로 막은 자리(§2.5):
  ① 파서 취약(프레젠테이션 클래스 의존) → `validate_structure()` 가 블록 수·필드 채움률·하한·급감을
     검사하고, 실패하면 **쓰지 않고 exit 2** + failure_kind 를 남긴다(워크플로가 이슈로 올린다).
     "조용히 0건" 이 성공으로 읽히는 길을 없앤다.
  ② 문서 안 표기 중복(13~65건) → `normalize_firm_name()` + `firm_key()` 로 정규화한 뒤 같은 키를
     합친다. 정규화 없이 diff 하면 같은 업체가 매번 '신규'로 오탐된다.
  ③ 해제일 필드 없음 → 직전 스냅샷(DB 의 active 행)과의 diff 로만 등재·해제를 만든다.
  ④ 첫 실행 → 이력 시작점('seed')으로만 표시한다. 'listed'(신규 등재)를 수천 건 쏟지 않는다.
  ⑤ 갱신 주기 미검증 → 매 실행 Last-Modified·문서 Published Date 를 스냅샷 행에 남긴다(주기 실측용).

접속: 한국 PC(가정·사무실 회선)에서는 `requests` + 저장소 기본 UA(grm_common.DEFAULT_USER_AGENT)로
  두 페이지 모두 HTTP 200(2026-11-08 실측). ★GitHub 러너(Azure IP)에서는 www.accessdata.fda.gov
  호스트 전체가 Akamai abuse-detection 으로 HTTP 404("FDA Apology")다(같은 날 진단 실행 —
  cms_ia·Drugs@FDA·루트 전부, UA 무관). 그래서 워크플로 schedule 은 꺼 두었다. 막히면 우회하지
  말고 실패(failure_kind=fetch)로 보고한다.

쓰기 게이트(둘 다 참일 때만 Supabase 에 쓴다):
  - CLI `--apply`
  - 환경변수 ENABLE_FDA_IMPORT_ALERT=true (repo 변수, 기본 off)
그 밖에는 dry-run: 수집·파싱·검증·diff(읽기 가능하면 DB 와, 아니면 빈 상태와)까지만 하고 리포트를 쓴다.

exit code:
  0 = 정상(쓰기 했거나 dry-run)
  2 = 수집 실패 / 구조 검증 실패 / 쓰기 실패 — report.failures 에 종류가 남는다.
"""

from __future__ import annotations

import argparse
import codecs
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "grm-fda-import-alert/v1"

ALERTS: dict[str, dict[str, Any]] = {
    # min_firms = 구조 검증 하한. 2026-08-11 실측 465·1,920 / 2026-11-08 실측 473·1,927 의 약 절반.
    # 원천이 하루 만에 반 토막 날 일은 없다 — 그 아래면 파서가 깨졌다고 본다.
    "66-40": {"page_id": 189, "min_firms": 230},
    "66-41": {"page_id": 190, "min_firms": 950},
}
URL_TMPL = "https://www.accessdata.fda.gov/cms_ia/importalert_{page_id}.html"

# 한 번의 실행에서 직전 active 대비 이 비율 넘게 사라지면 대량 해제가 아니라 파서 이상으로 본다.
MAX_DELIST_RATIO = 0.10
# 업체 블록 중 이름·국가·등재일이 모두 파싱된 비율 하한.
MIN_FIELD_RATE = 0.99
# 업체 블록 중 제품코드가 1개 이상 붙은 비율 하한.
MIN_PRODUCT_RATE = 0.95

RECENT_DAYS = 30


# ── 디코딩 ────────────────────────────────────────────────────────────────────
def _cp1252_fallback(err: UnicodeDecodeError) -> tuple[str, int]:
    """UTF-8 로 선언된 문서에 cp1252 스마트 따옴표(0x93 등)가 섞여 있다(2026-11-08 실측)."""
    bad = err.object[err.start:err.end]
    return bad.decode("cp1252", errors="replace"), err.end


codecs.register_error("grm_cp1252_fallback", _cp1252_fallback)


def decode_page(data: bytes) -> str:
    return data.decode("utf-8", errors="grm_cp1252_fallback")


# ── 파서 ──────────────────────────────────────────────────────────────────────
_RED_ANCHOR = re.compile(r'<a\s+name="RedFirm"\s*>', re.I)
# Green/Yellow List 는 2026-08-11·11-08 모두 0건. 생기면 그 앞에서 Red List 를 끊는다(면제 목록을
# 억류 목록으로 세면 안 된다).
_OTHER_LIST_ANCHOR = re.compile(r'<a\s+name="(GreenFirm|YellowFirm)"\s*>', re.I)
_ALERT_NO = re.compile(r"<B>\s*Import Alert\s*</B>\s*#\s*([0-9]{2}-[0-9]{2})", re.I)
_PAGE_PUBLISHED = re.compile(r"<B>\s*Published Date:\s*</B>\s*([0-9]{2}/[0-9]{2}/[0-9]{4})", re.I)

# ★간격은 전부 "다음 닫는 태그 전까지"로 묶는다(tempered). `.*?` 를 그냥 쓰면 마크업이 바뀐 날
#   매 토큰이 문서 끝까지 훑어 6.7MB 페이지에서 O(n²) 로 멈춘다(시험에서 실제로 멈췄다).
_TOKEN = re.compile(
    r'<div class="center">\s*<h4>(?P<country>[^<]+)</h4>\s*</div>'
    r'|<div class="div-info">(?P<firm>(?:(?!<div class="div-info">|</div>\s*</div>).)*)</div>\s*</div>'
    r'|<div class="floatleft">\s*<em>(?P<code>[^<]*)</em>(?P<pdesc>(?:(?!</div>).)*)</div>\s*'
    r'<div class="floatright marginsmall">\s*Date Published:\s*(?P<pdate>[0-9/]+)',
    re.S | re.I,
)
_FIRM_NAME = re.compile(r'<div class="div-name floatleft">(.*?)</div>', re.S | re.I)
_FIRM_DATE = re.compile(
    r'<div class="div-name floatright">\s*Date Published\s*:\s*([0-9/]+)\s*</div>', re.S | re.I)
_FIRM_ADDR = re.compile(r'<div class="clear">(.*?)$', re.S | re.I)
_DIV_INFO = re.compile(r'<div class="div-info">', re.I)
_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    s = _TAG.sub(" ", fragment or "")
    s = html.unescape(s).replace("\xa0", " ")
    return " ".join(s.split())


def _date(raw: str) -> str | None:
    raw = (raw or "").strip()
    try:
        return dt.datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _product_code(raw_em: str) -> str:
    # "54&nbsp;-&nbsp;D&nbsp;-&nbsp;-- " → "54-D---"
    return re.sub(r"\s+", "", _text(raw_em))


@dataclass
class Product:
    code: str
    description: str
    published: str | None


@dataclass
class FirmBlock:
    country: str
    name: str
    address: str
    city: str
    published: str | None
    products: list[Product] = field(default_factory=list)


@dataclass
class ParsedAlert:
    alert_no: str | None
    page_published: str | None
    raw_block_count: int
    firms: list[FirmBlock]
    other_list_present: bool


def _split_address(addr_html: str) -> tuple[str, str]:
    """주소 블록 = "거리, 도시,\n 주/지역 국가". 도시는 끝에서 두 번째 쉼표 조각이다."""
    text = _text(addr_html)
    parts = [p.strip() for p in text.split(",")]
    city = parts[-2] if len(parts) >= 2 else ""
    return text, city


def parse_alert_page(page: str) -> ParsedAlert:
    """수입경보 HTML → Red List 업체·제품. 순수 함수(네트워크 0)."""
    m_no = _ALERT_NO.search(page)
    m_pub = _PAGE_PUBLISHED.search(page)
    m_red = _RED_ANCHOR.search(page)
    if not m_red:
        return ParsedAlert(m_no.group(1) if m_no else None, _date(m_pub.group(1)) if m_pub else None,
                           0, [], bool(_OTHER_LIST_ANCHOR.search(page)))
    body = page[m_red.end():]
    m_other = _OTHER_LIST_ANCHOR.search(body)
    if m_other:
        body = body[:m_other.start()]
    raw_blocks = len(_DIV_INFO.findall(body))

    firms: list[FirmBlock] = []
    country = ""
    current: FirmBlock | None = None
    for tok in _TOKEN.finditer(body):
        if tok.group("country") is not None:
            country = _text(tok.group("country"))
            current = None
        elif tok.group("firm") is not None:
            inner = tok.group("firm")
            m_name = _FIRM_NAME.search(inner)
            m_date = _FIRM_DATE.search(inner)
            m_addr = _FIRM_ADDR.search(inner)
            address, city = _split_address(m_addr.group(1)) if m_addr else ("", "")
            current = FirmBlock(
                country=country,
                name=_text(m_name.group(1)) if m_name else "",
                address=address,
                city=city,
                published=_date(m_date.group(1)) if m_date else None,
            )
            firms.append(current)
        elif tok.group("code") is not None and current is not None:
            current.products.append(Product(
                code=_product_code(tok.group("code")),
                description=_text(tok.group("pdesc")),
                published=_date(tok.group("pdate")),
            ))
    return ParsedAlert(
        alert_no=m_no.group(1) if m_no else None,
        page_published=_date(m_pub.group(1)) if m_pub else None,
        raw_block_count=raw_blocks,
        firms=firms,
        other_list_present=bool(m_other),
    )


# ── 정규화 ────────────────────────────────────────────────────────────────────
# 법인격 접미어 — 꼬리에서만 반복 제거한다(중간 단어는 업체 이름의 일부일 수 있다).
LEGAL_SUFFIXES = frozenset({
    "CO", "COMPANY", "CORP", "CORPORATION", "INC", "INCORPORATED", "LTD", "LIMITED", "LLC", "LLP",
    "LP", "PLC", "GMBH", "AG", "KG", "SA", "SAS", "SARL", "SRL", "SPA", "BV", "NV", "AB", "AS", "OY",
    "OYJ", "PVT", "PRIVATE", "PTE", "PTY", "SDN", "BHD", "CV", "DE", "RL", "SL", "SAU", "KK",
    "KABUSHIKI", "KAISHA", "JSC", "CJSC", "OJSC", "PJSC", "OAO", "OOO", "ZAO", "TBK", "PT", "THE",
})
# 앞에 붙는 법인격(인도네시아 PT, 영어 관사).
LEGAL_PREFIXES = frozenset({"PT", "THE", "CV"})


def normalize_firm_name(name: str) -> str:
    """대문자화 → & 를 AND 로 → 영숫자 외 공백 → 앞뒤 법인격 토큰 제거 → 공백 하나로.

    "COSMAX, Inc." / "Cosmax Inc" / "COSMAX INC." → "COSMAX". 모든 토큰이 법인격이면(드묾) 원 토큰 유지.
    """
    s = unicodedata.normalize("NFKC", name or "").upper().replace("&", " AND ")
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    toks = s.split()
    orig = list(toks)
    while toks and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    while toks and toks[0] in LEGAL_PREFIXES:
        toks.pop(0)
    return " ".join(toks or orig)


def normalize_country(country: str) -> str:
    s = unicodedata.normalize("NFKC", country or "").upper()
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    return " ".join(s.split())


def firm_key(alert_no: str, country: str, name: str) -> str:
    """같은 경보·같은 국가·같은 정규화 이름 = 같은 업체.

    주소·도시는 키에 넣지 않는다 — 도시 표기("Seongnam" / "Seongnam-si")만 달라도 가짜 신규+가짜 해제가
    한 쌍 생긴다. 같은 이름의 다른 공장(같은 나라)은 한 행으로 합치고 주소를 모두 보관한다.
    이름 안 공백도 키에서는 지운다 — "BIO PORT KOREA" / "BIOPORT KOREA" 가 같은 66-41 문서에 둘 다 있다
    (2026-11-08 실측).
    """
    return f"{alert_no}|{normalize_country(country)}|{normalize_firm_name(name).replace(' ', '')}"


def is_korea(country: str) -> bool:
    return normalize_country(country).startswith("KOREA") and "DEMOCRATIC" not in normalize_country(country)


def consolidate(alert_no: str, firms: list[FirmBlock]) -> tuple[dict[str, dict[str, Any]], int]:
    """정규화 키로 문서 안 중복 블록을 합친다. (키 → 행, 합쳐진 블록 수)."""
    out: dict[str, dict[str, Any]] = {}
    merged = 0
    for f in firms:
        key = firm_key(alert_no, f.country, f.name)
        prods = {p.code: p for p in f.products if p.code}
        row = out.get(key)
        if row is None:
            out[key] = {
                "alert_no": alert_no,
                "firm_key": key,
                "firm_name": f.name,
                "name_norm": normalize_firm_name(f.name),
                "country": f.country,
                "is_korea": is_korea(f.country),
                "address": f.address,
                "addresses": [f.address] if f.address else [],
                "city": f.city,
                "firm_published": f.published,
                "products": prods,
            }
            continue
        merged += 1
        if f.address and f.address not in row["addresses"]:
            row["addresses"].append(f.address)
        if f.published and (not row["firm_published"] or f.published < row["firm_published"]):
            row["firm_published"] = f.published
        for code, p in prods.items():
            old = row["products"].get(code)
            if old is None or (p.published and (not old.published or p.published < old.published)):
                row["products"][code] = p
    for row in out.values():
        row["products"] = [
            {"code": p.code, "description": p.description, "published": p.published}
            for p in sorted(row["products"].values(), key=lambda p: p.code)
        ]
        row["product_codes"] = [p["code"] for p in row["products"]]
    return out, merged


# ── 구조 검증 ─────────────────────────────────────────────────────────────────
def validate_structure(expected_alert: str, parsed: ParsedAlert, *, prev_active: int = 0,
                       curr_unique: int | None = None) -> list[str]:
    """실패 사유 코드 목록(빈 목록 = 통과). 하나라도 있으면 그 경보는 쓰지 않는다."""
    fails: list[str] = []
    if parsed.alert_no != expected_alert:
        fails.append(f"alert_no_mismatch:{parsed.alert_no}")
    n = len(parsed.firms)
    if parsed.raw_block_count == 0 or n == 0:
        fails.append("no_firm_blocks")
        return fails
    if parsed.raw_block_count != n:
        fails.append(f"block_count_mismatch:{parsed.raw_block_count}!={n}")
    floor = ALERTS[expected_alert]["min_firms"]
    if n < floor:
        fails.append(f"too_few_firms:{n}<{floor}")
    complete = sum(1 for f in parsed.firms if f.name and f.country and f.published)
    if complete / n < MIN_FIELD_RATE:
        fails.append(f"field_rate:{complete}/{n}")
    with_prod = sum(1 for f in parsed.firms if f.products)
    if with_prod / n < MIN_PRODUCT_RATE:
        fails.append(f"product_rate:{with_prod}/{n}")
    if parsed.other_list_present:
        # 실패가 아니다 — Red List 는 앵커 앞에서 끊었다. 그래도 형식이 바뀐 신호라 경고로 남긴다.
        pass
    if prev_active and curr_unique is not None and curr_unique < prev_active:
        gone = prev_active - curr_unique
        if gone / prev_active > MAX_DELIST_RATIO:
            fails.append(f"mass_delist_guard:{gone}/{prev_active}")
    return fails


# ── diff ──────────────────────────────────────────────────────────────────────
def compute_diff(alert_no: str, current: dict[str, dict[str, Any]],
                 previous: dict[str, dict[str, Any]], *, seeded: bool,
                 run_date: str) -> list[dict[str, Any]]:
    """직전 DB 상태(previous: firm_key → {active, product_codes, ...}) 대비 이벤트.

    seeded=False(이 경보의 스냅샷이 한 번도 없음) → 현재 전원을 'seed' 로만 표시한다.
    """
    events: list[dict[str, Any]] = []

    def ev(kind: str, key: str, row: dict[str, Any], **extra: Any) -> None:
        events.append({
            "alert_no": alert_no,
            "firm_key": key,
            "event": kind,
            "event_date": run_date,
            "firm_name": row.get("firm_name", ""),
            "country": row.get("country", ""),
            "firm_published": row.get("firm_published"),
            "detail": extra or {},
        })

    if not seeded:
        for key in sorted(current):
            ev("seed", key, current[key])
        return events

    for key in sorted(current):
        row = current[key]
        prev = previous.get(key)
        if prev is None:
            ev("listed", key, row)
        elif not prev.get("active", False):
            ev("relisted", key, row)
        else:
            old_codes = set(prev.get("product_codes") or [])
            new_codes = set(row.get("product_codes") or [])
            if old_codes != new_codes:
                ev("products_changed", key, row,
                   added=sorted(new_codes - old_codes), removed=sorted(old_codes - new_codes))
    for key in sorted(previous):
        prev = previous[key]
        if prev.get("active", False) and key not in current:
            ev("delisted", key, prev)
    return events


def recent_counts(current: dict[str, dict[str, Any]], run_date: str,
                  days: int = RECENT_DAYS) -> dict[str, int]:
    """원천 날짜 기준 최근 N일 등재(업체 헤더 날짜)·제품 추가(제품 날짜) 수."""
    cutoff = (dt.date.fromisoformat(run_date) - dt.timedelta(days=days)).isoformat()
    firms = [r for r in current.values() if (r.get("firm_published") or "") >= cutoff]
    firms_with_new_product = [
        r for r in current.values()
        if any((p.get("published") or "") >= cutoff for p in r.get("products", []))
    ]
    return {
        "firms_listed": len(firms),
        "firms_listed_korea": sum(1 for r in firms if r["is_korea"]),
        "firms_with_new_product": len(firms_with_new_product),
    }


# ── Supabase(PostgREST) ──────────────────────────────────────────────────────
class SupabaseError(RuntimeError):
    pass


class Store:
    """service_role 로만 쓴다. 읽기 실패·쓰기 실패는 예외 → 호출부가 failure 로 남긴다."""

    def __init__(self, base_url: str, key: str, *, http: Any = None) -> None:
        import requests  # 지연 import — 테스트가 http 를 주입하면 불필요

        self.base = base_url.rstrip("/")
        self.key = key
        self.http = http or requests

    def _h(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
             "Content-Type": "application/json"}
        h.update(extra or {})
        return h

    def has_snapshot(self, alert_no: str) -> bool:
        r = self.http.get(f"{self.base}/rest/v1/import_alert_snapshot",
                          params={"alert_no": f"eq.{alert_no}", "select": "id", "limit": "1"},
                          headers=self._h(), timeout=60)
        if r.status_code != 200:
            raise SupabaseError(f"snapshot read HTTP {r.status_code}: {r.text[:200]}")
        return bool(r.json())

    def load_listing(self, alert_no: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        offset, page = 0, 1000
        while True:
            r = self.http.get(
                f"{self.base}/rest/v1/import_alert_listing",
                params={"alert_no": f"eq.{alert_no}",
                        "select": "firm_key,firm_name,country,firm_published,product_codes,active",
                        "order": "firm_key", "limit": str(page), "offset": str(offset)},
                headers=self._h(), timeout=60)
            if r.status_code != 200:
                raise SupabaseError(f"listing read HTTP {r.status_code}: {r.text[:200]}")
            rows = r.json()
            for row in rows:
                out[row["firm_key"]] = row
            if len(rows) < page:
                return out
            offset += page

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(f"{self.base}/rest/v1/rpc/import_alert_apply",
                           json={"p_payload": payload}, headers=self._h(), timeout=180)
        if r.status_code != 200:
            raise SupabaseError(f"apply HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}


def build_payload(alert_no: str, run_date: str, snapshot: dict[str, Any],
                  current: dict[str, dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    listings = [{
        "firm_key": r["firm_key"], "firm_name": r["firm_name"], "name_norm": r["name_norm"],
        "country": r["country"], "is_korea": r["is_korea"], "address": r["address"],
        "addresses": r["addresses"], "city": r["city"], "firm_published": r["firm_published"],
        "product_codes": r["product_codes"], "products": r["products"],
    } for r in current.values()]
    return {"alert_no": alert_no, "run_date": run_date, "snapshot": snapshot,
            "listings": listings, "events": events}


# ── 실행 ──────────────────────────────────────────────────────────────────────
def fetch_page(alert_no: str) -> tuple[bytes, dict[str, str]]:
    import requests

    from grm_common import DEFAULT_USER_AGENT

    url = URL_TMPL.format(page_id=ALERTS[alert_no]["page_id"])
    last: Exception | None = None
    for _ in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": DEFAULT_USER_AGENT,
                                           "Accept": "text/html,application/xhtml+xml"}, timeout=120)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} for {url}")
            return r.content, {"last_modified": r.headers.get("Last-Modified", ""), "url": url}
        except (requests.RequestException, RuntimeError) as exc:
            last = exc
    raise RuntimeError(f"fetch failed: {url} ({last})")


def run_alert(alert_no: str, *, run_date: str, fetch: Callable[[str], tuple[bytes, dict[str, str]]],
              store: Store | None, write: bool, raw_dir: Path | None) -> dict[str, Any]:
    rep: dict[str, Any] = {"alert_no": alert_no, "failures": [], "warnings": []}
    try:
        data, meta = fetch(alert_no)
    except Exception as exc:  # noqa: BLE001 — 수집 실패는 리포트로
        rep["failures"].append(f"fetch:{exc}")
        return rep
    sha = hashlib.sha256(data).hexdigest()
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(raw_dir / f"importalert_{alert_no}_{run_date}.html.gz", "wb") as fh:
            fh.write(data)
    parsed = parse_alert_page(decode_page(data))
    current, merged = consolidate(alert_no, parsed.firms)

    previous: dict[str, dict[str, Any]] = {}
    seeded = False
    if store is not None:
        try:
            seeded = store.has_snapshot(alert_no)
            previous = store.load_listing(alert_no) if seeded else {}
        except Exception as exc:  # noqa: BLE001
            rep["failures"].append(f"db_read:{exc}")
            return rep
    prev_active = sum(1 for r in previous.values() if r.get("active"))

    fails = validate_structure(alert_no, parsed, prev_active=prev_active, curr_unique=len(current))
    if parsed.other_list_present:
        rep["warnings"].append("other_list_present")
    events = compute_diff(alert_no, current, previous, seeded=seeded, run_date=run_date)
    kinds: dict[str, int] = {}
    for e in events:
        kinds[e["event"]] = kinds.get(e["event"], 0) + 1
    rep.update({
        "url": meta.get("url"),
        "last_modified": meta.get("last_modified"),
        "page_published": parsed.page_published,
        "sha256": sha,
        "raw_blocks": parsed.raw_block_count,
        "firms_unique": len(current),
        "merged_duplicate_blocks": merged,
        "korea_firms": sum(1 for r in current.values() if r["is_korea"]),
        "products": sum(len(r["product_codes"]) for r in current.values()),
        "recent": recent_counts(current, run_date),
        "seeded_before": seeded,
        "prev_active": prev_active,
        "events": kinds,
        "korea_events": [e for e in events if e["event"] != "seed" and is_korea(e["country"])][:50],
    })
    rep["failures"].extend(f"structure:{f}" for f in fails)
    if fails:
        return rep
    if write and store is not None:
        snapshot = {"sha256": sha, "last_modified": meta.get("last_modified") or None,
                    "page_published": parsed.page_published, "raw_blocks": parsed.raw_block_count,
                    "firms_unique": len(current), "merged_blocks": merged,
                    "schema_version": SCHEMA_VERSION}
        try:
            rep["apply_result"] = store.apply(build_payload(alert_no, run_date, snapshot, current, events))
            rep["written"] = True
        except Exception as exc:  # noqa: BLE001
            rep["failures"].append(f"db_write:{exc}")
    return rep


def env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--alerts", default=",".join(ALERTS), help="쉼표 구분 경보 번호(기본 66-40,66-41)")
    ap.add_argument("--apply", action="store_true",
                    help="Supabase 에 쓴다(ENABLE_FDA_IMPORT_ALERT=true 일 때만 실제로 쓴다)")
    ap.add_argument("--no-db", action="store_true", help="DB 읽기도 하지 않는다(빈 상태 기준 diff)")
    ap.add_argument("--run-date", default="", help="기준일 ISO(기본 오늘 UTC)")
    ap.add_argument("--raw-dir", default="", help="원문 HTML.gz 보관 폴더(워크플로 아티팩트용)")
    ap.add_argument("--output", default="import_alert_report.json")
    args = ap.parse_args(argv)

    run_date = args.run_date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    alerts = [a.strip() for a in args.alerts.split(",") if a.strip()]
    unknown = [a for a in alerts if a not in ALERTS]
    if unknown:
        print(f"알 수 없는 경보 번호: {unknown}", file=sys.stderr)
        return 2

    enabled = env_flag("ENABLE_FDA_IMPORT_ALERT")
    write = bool(args.apply and enabled)
    store: Store | None = None
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not args.no_db and url and key:
        store = Store(url, key)
    if write and store is None:
        print("--apply + ENABLE_FDA_IMPORT_ALERT=true 인데 SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 가 없다",
              file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "run_date": run_date,
        "mode": "apply" if write else "dry-run", "enabled_flag": enabled,
        "apply_requested": args.apply, "db_read": store is not None, "alerts": [],
    }
    raw_dir = Path(args.raw_dir) if args.raw_dir else None
    for a in alerts:
        report["alerts"].append(run_alert(a, run_date=run_date, fetch=fetch_page, store=store,
                                          write=write, raw_dir=raw_dir))
    failures = [f"{r['alert_no']}:{f}" for r in report["alerts"] for f in r["failures"]]
    report["failures"] = failures
    report["failure_kind"] = (
        "structure" if any(":structure:" in f for f in failures)
        else "fetch" if any(":fetch:" in f for f in failures)
        else "db" if failures else "")
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in report["alerts"]:
        print(f"[{r['alert_no']}] 업체 {r.get('firms_unique', '-')}곳(블록 {r.get('raw_blocks', '-')}, "
              f"문서 안 중복 합침 {r.get('merged_duplicate_blocks', '-')}) · 한국 {r.get('korea_firms', '-')}곳 · "
              f"최근 {RECENT_DAYS}일 등재 {r.get('recent', {}).get('firms_listed', '-')}곳 · "
              f"이벤트 {r.get('events', {})} · 실패 {r['failures'] or '없음'}")
    print(f"mode={report['mode']} failures={len(failures)}")
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
