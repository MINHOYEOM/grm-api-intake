#!/usr/bin/env python3
"""GRM 자료실 수집기 — MHRA(영국) GMP/GDP 참조문서.

플러그인 계약:
  LIBRARY_SOURCE = "mhra"
  collect_library_items(run_date) -> (items, error)
  items = 카탈로그 v2 공개 필드 dict 목록.

⚠️ 영국은 HC/PMDA/EMA 와 달리 **완전한 단일 인덱스가 없다**(2026-08-11 정찰 실측).
gov.uk 검색 API 는 동작하지만(HTTP 200) GMP 전용 채널이 못 된다 — `filter_format=guidance`
상위 30건 중 GMP 관련은 3건(10%)뿐이었다. 그래서 이 수집기는 **3갈래 구조**다:

  ① GMP/GDP 허브 페이지의 'Information sheets' 절 — 완전 자동
     https://www.gov.uk/guidance/good-manufacturing-practice-and-good-distribution-practice
     PMDA 수집기(library_collect_pmda.py)의 h2 섹션 경계 파싱 패턴을 재사용한다 —
     govuk Content API 의 details.body(HTML)를 h2/h3 경계로 나눠 표제가 정규화 후
     "information sheets" 인 절 안의 .pdf 링크만 추린다(_HUB_URL 참고).
     실측: 이 절이 두 번(대소문자만 다른 "Information sheets"/"Information Sheets")
     나온다 — govuk 편집 이력상 페이지가 두 절로 나뉘어 계속 유지되는 것으로 보이며,
     본 수집기는 정규화 매칭으로 둘 다 잡는다(원인 임의 추정 안 함).
     ⚠️ 원문 details.attachments 배열엔 이 절들이 실제로 링크하지 않는 "고아" 첨부가
     2건 있다(TQP 재평가 서식·"Compliance Management – UK Wholesaler" PDF) — 본문에
     없으면 페이지 사용자도 못 보는 콘텐츠이므로 자료실도 제외한다(추측 채움 금지).
  ② GMP 결함통계 컬렉션 — 자동, 연도별 PDF
     https://www.gov.uk/government/statistics/good-manufacturing-practice-inspection-deficiencies
     details.attachments 중 content_type == application/pdf 만(연도별 .xlsx 원자료는
     별개 산출물이라 제외). ★ 2019년 이후 신규가 없다(실측 재확인, 7년 정체) — 이건
     **소스가 죽은 게 아니라 이 갈래의 정상 상태**다. MIN_EXPECTED_STATS_ITEMS 가드는
     "0건 근처로 줄었는가"(구조 변경 의심)만 본다 — "늘지 않았는가"는 절대 경보하지
     않는다(늘지 않는 게 설계다).
  ③ 단독 가이던스 페이지 — 고정 목록 7건
     org/format/taxon 어떤 일반 API로도 안 잡히고 사람이 검색으로 골라야 찾아지는
     페이지들이다. HC 수집기(library_collect_health_canada.py)의 INDEX_PATHS 고정목록
     패턴을 따르지만 **HC 와 달리 이 고정목록은 완전성을 보장하지 않는다** — gov.uk
     가 GMP 관련 신규 단독 페이지를 발행해도 이 목록에 없으면 자료실에 안 잡힌다
     (EMA 앵커표처럼 append-only 로 다뤄야 한다. 새 페이지 발견 시 STANDALONE_PAGES
     에 사람이 직접 추가). slug 는 gov.uk search API(https://www.gov.uk/api/search.json)
     로 실측 검증했다(2026-08-11) — 지시문 표기 slug 7개 중 5개가 실제와 달라 전부
     재확인 후 고정했다.

제외(확정, 근거는 지시문 정찰):
  - Orange Guide / Green Guide — pharmpress.com 유료 구매 페이지로 링크(HTTP 301 실측).
  - MHRA Inspectorate 블로그 — 형식이 참조문서 아닌 블로그 포스트.
  - `collect_mhra_gmp_ncr.py`(findings 용 비준수 성명서)와 무관 — 이건 자료실(원문
    참조 카탈로그), 그건 findings(지적사항 DB). 혼동 금지.
  - "Exceptional GMP flexibilities"(COVID 기 유연화) 페이지 — 철회는 안 됐지만
    public_updated_at=2020-07-13 로 6년 정체다. 자료실에 넣으면 '현행 가이던스'처럼
    보이는 표시 오류가 된다. **판단: 넣지 않는다.** 근거 — ①고정 7건 목록에 원래
    없던 항목이라 넣으려면 새로 발견한 걸 임의로 추가하는 셈이고 ②정체 사실을 화면에
    드러낼 장치(예: "임시조치 종료 여부" 배지)가 카탈로그 v2 스키마에 없어 지금
    추가하면 결국 '조용히 낡은 채로' 노출된다. 재검토 시 사람이 이 문단부터 볼 것.

official_url:
  ①②는 PDF 원문 URL 자체(PMDA 관례와 동일 — assets.publishing.service.gov.uk 의
  불투명 해시 경로). ③은 페이지 URL(gov.uk/<base>/<slug>).
  ⚠️ PDF URL 이 재업로드로 바뀌면 다음 수집에서 새 id 로 잡혀 옛 항목이 자료실에서
  빠질 수 있다(추적 끊김) — id 를 govuk 첨부 id(숫자)가 아니라 **파일명 stem** 기반
  으로 안정화한 이유가 이거다(파일명은 재업로드에도 보통 보존된다. 완벽한 방어는
  아니라는 점은 명시해 둔다).

id 규칙:
  "mhra-" + 파일명(확장자 제외) 슬러그(_filename_slug). 이미 "mhra"로 시작하는
  파일명(②의 일부)은 그대로 슬러그화해 "mhra-mhra-..." 형태가 되는 걸 허용한다 —
  중복 접두보다 "규칙이 항상 같다"는 결정론을 우선한다. ③은 "mhra-" + slug(페이지
  slug 자체가 이미 안정 식별자).

withdrawn 판별:
  govuk Content API 응답의 최상위 `withdrawn_notice` 가 비어있지 않은 dict 면 철회본
  이다 — ①②(허브·통계 컬렉션 페이지 자체)와 ③(단독 페이지 각각)에 모두 적용한다.

published_date:
  ③만 first_published_at 날짜부를 쓴다(페이지 단위 필드라 신뢰 가능). ①②는 PDF
  개별 발행일을 주는 필드가 API 에 없다 — 페이지 전체의 first_published_at/
  public_updated_at 을 개별 PDF에 씌우면 없는 정밀도를 지어내는 것이라 비워 둔다.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from grm_common import http_get_json, log


LIBRARY_SOURCE = "mhra"

USER_AGENT = "GRM-Library/1.0 (+https://grm-solutions.com)"
CONTENT_API_TMPL = "https://www.gov.uk/api/content/{path}"

HUB_PATH = "guidance/good-manufacturing-practice-and-good-distribution-practice"
STATS_PATH = "government/statistics/good-manufacturing-practice-inspection-deficiencies"

# ③ 고정 목록 — 완전성 보장 없음(위 docstring). (표시용 base, content-api path, doc_type 힌트 없이
# API 의 document_type 을 그대로 쓴다.)
STANDALONE_PAGES: tuple[str, ...] = (
    "guidance/guidance-on-responding-to-a-gmpgdp-post-inspection-letter",
    "guidance/decentralised-manufacture-uk-guideline-on-good-manufacturing-practice-gmp",
    "government/publications/access-consortium-good-manufacturing-practice-gmp-statement",
    "government/publications/good-manufacturing-practice-conflicts-of-interests",
    "guidance/clinical-trials-for-medicines-good-manufacturing-practice-and-radiopharmaceutical-investigational-medicinal-products",
    "government/publications/guidance-for-specials-manufacturers",
    "government/publications/out-of-specification-investigations",
)

HUB_DOC_TYPE = "information-sheet"
STATS_DOC_TYPE = "gmp-deficiency-statistics"

MIN_EXPECTED_HUB_ITEMS = 8      # 실측 11건 — 이보다 적으면 페이지 구조 변경 의심
MIN_EXPECTED_STATS_ITEMS = 5    # 실측 8건, 신규 0건이 정상 — "줄었는가"만 본다

_TAG_RE = re.compile(r"<[^>]+>")
_H2H3_SPLIT_RE = re.compile(r"<h([23])[^>]*>(.*?)</h\1>", re.S | re.I)
_PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf)"', re.I)
_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _heading_text(raw: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", raw or "")).strip()


def _normalize_heading(raw: str) -> str:
    return re.sub(r"[^a-z]+", " ", _heading_text(raw).lower()).strip()


def _filename_slug(url: str) -> str:
    """PDF URL → 파일명 stem 슬러그(네트워크 없음). id 안정화 근거는 모듈 docstring."""
    stem = url.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    slug = _SLUG_STRIP_RE.sub("-", stem.lower()).strip("-")
    return slug or "item"


def _fetch_content(path: str) -> tuple[dict[str, Any] | None, str | None]:
    """govuk Content API GET(path 는 선행 슬래시 없이). 반환 (payload, error)."""
    url = CONTENT_API_TMPL.format(path=path)
    try:
        payload = http_get_json(url, timeout=30, retries=2, headers={"User-Agent": USER_AGENT})
    except RuntimeError as exc:
        return None, f"gov.uk Content API 조회 실패({path}): {exc}"
    if not isinstance(payload, dict):
        return None, f"gov.uk Content API 응답 구조 이상({path}): {type(payload)}"
    return payload, None


def is_withdrawn(payload: dict[str, Any]) -> bool:
    """withdrawn_notice 가 비어있지 않은 dict 면 철회본(네트워크 없음)."""
    notice = payload.get("withdrawn_notice")
    return bool(notice)


def find_information_sheet_segments(body_html: str) -> list[str]:
    """govuk body HTML → 'information sheet(s)' 로 정규화되는 h2/h3 절의 본문 목록(네트워크 없음).

    같은 페이지에 대소문자만 다른 표제가 두 번 나올 수 있다 — 정규화 매칭으로 전부 잡는다."""
    body = body_html or ""
    bounds = list(_H2H3_SPLIT_RE.finditer(body))
    segments: list[str] = []
    for index, match in enumerate(bounds):
        if _normalize_heading(match.group(2)) != "information sheets":
            continue
        start = match.end()
        end = bounds[index + 1].start() if index + 1 < len(bounds) else len(body)
        segments.append(body[start:end])
    return segments


def derive_hub_items(payload: dict[str, Any]) -> list[dict[str, str]]:
    """허브 페이지 Content API 응답 → 'Information sheets' 절의 PDF 카탈로그 항목(네트워크 없음)."""
    details = payload.get("details") or {}
    body_html = str(details.get("body") or "")
    attachments = details.get("attachments") or []
    by_url: dict[str, dict[str, Any]] = {
        str(a.get("url") or ""): a for a in attachments if isinstance(a, dict) and a.get("url")
    }

    seen: set[str] = set()
    items: list[dict[str, str]] = []
    for segment in find_information_sheet_segments(body_html):
        for href in _PDF_HREF_RE.findall(segment):
            if href in seen:
                continue
            seen.add(href)
            attachment = by_url.get(href)
            if not attachment or attachment.get("content_type") != "application/pdf":
                continue
            title = str(attachment.get("title") or "").strip()
            if not title:
                continue
            items.append({
                "id": f"mhra-{_filename_slug(href)}",
                "title_en": title,
                "doc_type": HUB_DOC_TYPE,
                "official_url": href,
            })
    return items


def derive_stats_items(payload: dict[str, Any]) -> list[dict[str, str]]:
    """GMP 결함통계 컬렉션 Content API 응답 → 연도별 PDF 카탈로그 항목(네트워크 없음)."""
    details = payload.get("details") or {}
    attachments = details.get("attachments") or []
    items: list[dict[str, str]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("content_type") != "application/pdf":
            continue
        url = str(attachment.get("url") or "").strip()
        title = str(attachment.get("title") or "").strip()
        if not url or not title:
            continue
        items.append({
            "id": f"mhra-{_filename_slug(url)}",
            "title_en": title,
            "doc_type": STATS_DOC_TYPE,
            "official_url": url,
        })
    return items


def derive_standalone_item(path: str, payload: dict[str, Any]) -> dict[str, str] | None:
    """단독 가이던스 페이지 Content API 응답 → 카탈로그 항목(네트워크 없음). 철회본은 None."""
    if is_withdrawn(payload):
        return None
    title = str(payload.get("title") or "").strip()
    if not title:
        return None
    doc_type = str(payload.get("document_type") or "guidance").strip() or "guidance"
    slug = path.rsplit("/", 1)[-1]
    item: dict[str, str] = {
        "id": f"mhra-{slug}",
        "title_en": title,
        "doc_type": doc_type,
        "official_url": f"https://www.gov.uk/{path}",
    }
    published = _ISO_DATE_RE.match(str(payload.get("first_published_at") or ""))
    if published:
        item["published_date"] = published.group(1)
    return item


def _collect_hub() -> tuple[list[dict[str, str]], str | None]:
    payload, err = _fetch_content(HUB_PATH)
    if err or payload is None:
        return [], err
    if is_withdrawn(payload):
        return [], f"MHRA GMP/GDP 허브 페이지가 철회됨({HUB_PATH})"
    items = derive_hub_items(payload)
    if len(items) < MIN_EXPECTED_HUB_ITEMS:
        return items, (f"MHRA 허브 'Information sheets' {len(items)}건"
                       f"(<{MIN_EXPECTED_HUB_ITEMS}) — 페이지 구조 변경 의심")
    return items, None


def _collect_stats() -> tuple[list[dict[str, str]], str | None]:
    payload, err = _fetch_content(STATS_PATH)
    if err or payload is None:
        return [], err
    if is_withdrawn(payload):
        return [], f"MHRA GMP 결함통계 컬렉션이 철회됨({STATS_PATH})"
    items = derive_stats_items(payload)
    if len(items) < MIN_EXPECTED_STATS_ITEMS:
        return items, (f"MHRA GMP 결함통계 PDF {len(items)}건"
                       f"(<{MIN_EXPECTED_STATS_ITEMS}) — 페이지 구조 변경 의심"
                       "(2019년 이후 신규 0건 자체는 정상 — 이 가드는 급감만 본다)")
    return items, None


def _collect_standalone() -> tuple[list[dict[str, str]], str | None]:
    items: list[dict[str, str]] = []
    errors: list[str] = []
    for path in STANDALONE_PAGES:
        payload, err = _fetch_content(path)
        if err or payload is None:
            errors.append(err or f"{path}: 조회 실패")
            continue
        item = derive_standalone_item(path, payload)
        if item is None:
            if is_withdrawn(payload):
                errors.append(f"{path}: 철회본이라 제외")
            else:
                errors.append(f"{path}: title 결측이라 제외")
            continue
        items.append(item)
    return items, ("; ".join(errors) if errors else None)


def collect_library_items(run_date: date) -> tuple[list[dict], str | None]:
    """MHRA 자료실 수집 진입점(3갈래 통합). 반환 (items, error)."""
    hub_items, hub_err = _collect_hub()
    stats_items, stats_err = _collect_stats()
    standalone_items, standalone_err = _collect_standalone()

    items = hub_items + stats_items + standalone_items
    errors = [e for e in (hub_err, stats_err, standalone_err) if e]

    log("INFO", f"MHRA 자료실 수집: 허브(info sheets) {len(hub_items)}건 / "
                f"결함통계 {len(stats_items)}건 / 단독페이지 {len(standalone_items)}건 "
                f"/ 합계 {len(items)}건")

    if not items:
        return [], "; ".join(errors) or "MHRA 자료실 수집 결과 0건"
    return items, ("; ".join(errors) if errors else None)


if __name__ == "__main__":  # 수동 점검용
    import json

    collected, err = collect_library_items(date.today())
    print(f"items={len(collected)} error={err}")
    for entry in collected:
        print(" ", entry["id"], entry["doc_type"], "|", entry["title_en"][:70])
