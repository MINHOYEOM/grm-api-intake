#!/usr/bin/env python3
"""
GRM Brave Search Collector — v1.2 Phase 2a

Brave Search API로 9개 쿼리 슬롯을 실행해 GRM 관련 규제 신호를 수집하고
IntakeItem 리스트로 반환한다.

설계 원칙:
1. 쿼리당 MAX_RESULTS_PER_QUERY = 5개 결과만 수집 (Phase 2a 기준값)
2. Evidence Candidate: 공식 규제기관 도메인 → B, 그 외 → C
   A는 Search 결과에서 절대 부여하지 않음 (API raw data만 A 자격)
   D는 Routine 최종 판정 위임
3. freshness=pw (최근 7일) 필터로 윈도우 일치
4. _stable_doc_id(source, url, url, date_iso) URL 기반 dedupe
5. 본문 fetch / 링크 추적 없음 — snippet + URL만 저장 (Phase 2a 범위 외)

collect_intake.py 의존:
- IntakeItem, compute_relevance, log, truncate
- SRC_TYPE_SEARCH_RESULT, SOURCE_BRAVE (collect_intake.py Task #7 패치 후 추가)

환경 변수:
- BRAVE_API_KEY: Brave Search API subscription token
"""

from __future__ import annotations

import hashlib
import os
import time
from urllib.parse import urlparse

import requests

# collect_intake.py에서 공유 유틸 import
# Task #7 패치 완료 후 SRC_TYPE_SEARCH_RESULT, SOURCE_BRAVE 도 여기서 import
from collect_intake import (
    IntakeItem,
    compute_relevance,
    log,
    truncate,
)

# ─────────────────────────────────────────────────────────────────────────────
# 예외
# ─────────────────────────────────────────────────────────────────────────────


class BraveSearchError(RuntimeError):
    """Brave Search API 오류 전용 예외.

    다음 경우에 발생:
    - 4xx 응답 (401 인증 실패, 403 권한 없음, 400 쿼리 오류 등)
    - 429 rate-limit 재시도 소진
    - 네트워크 오류 최종 실패

    정상 0건 (200 OK + results=[])과 오류를 명확히 구분한다.
    collect_brave_search()의 try/except가 이 예외를 slot_errors에 기록한다.
    """


# ─────────────────────────────────────────────────────────────────────────────
# 상수 (Task #7 collect_intake.py 패치 후 해당 파일로 이관)
# ─────────────────────────────────────────────────────────────────────────────

SOURCE_BRAVE = "Brave Search"
SRC_TYPE_SEARCH_RESULT = "Search Result"

BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"

# 쿼리당 최대 결과 수: 9 slots × 5 = 최대 45 rows/run
MAX_RESULTS_PER_QUERY = 5

# Brave Search freshness 파라미터: pw = past week (7일)
BRAVE_FRESHNESS = "pw"

# Evidence Candidate: 공식 규제기관 도메인 → B, 그 외 → C
# 서브도메인 포함 (endswith 매칭)
OFFICIAL_DOMAINS: frozenset[str] = frozenset({
    "fda.gov",
    "federalregister.gov",
    "ema.europa.eu",
    "picscheme.org",
    "ich.org",
    "tga.gov.au",
    "who.int",
    "canada.ca",
    "hc-sc.gc.ca",          # Health Canada 공식 도메인
    "pmda.go.jp",
    "hsa.gov.sg",
    "mhra.gov.uk",
    "gov.uk",               # MHRA blog 포함 (mhrainspectorate.blog.gov.uk)
    "edqm.eu",
    "swissmedic.ch",
    "usp.org",
    "eudra.org",
    # ispe.org 제외: 전문단체(industry association)이며 규제기관 아님 → Evidence C
    "pic-s.net",            # PIC/S 일부 도메인
})

# ─────────────────────────────────────────────────────────────────────────────
# 9개 Search Slot 정의 (v2.1 — site: 중심, 연도 하드코딩 없음, FR/Recall 중복 제거)
# ─────────────────────────────────────────────────────────────────────────────
# 형식: (slot_name, query_string)
# slot_name은 로그·디버그용, query_string이 Brave에 전달됨
# - freshness=pw (7일) 필터가 날짜 범위를 제어하므로 연도를 쿼리에 넣지 않음
# - site: 연산자로 공식 도메인 우선 탐색 → Evidence B 비율 향상
# - FR(Federal Register)과 OpenFDA Recall은 API 전수 수집 중 → Search 중복 강조 생략

SEARCH_SLOTS: list[tuple[str, str]] = [
    (
        # FDA Warning Letters: 공식 페이지에서 직접 탐색
        "FDA_WARNING_LETTERS",
        'site:fda.gov "Warning Letter" CGMP pharmaceutical',
    ),
    (
        # FDA Guidance: Draft/Final Guidance 공식 문서
        "FDA_GUIDANCE",
        'site:fda.gov ("Draft Guidance" OR "Final Guidance") "pharmaceutical quality"',
    ),
    (
        # FDA GMP 전반: 제조 품질·컴플라이언스 업데이트
        "FDA_GMP_UPDATES",
        "site:fda.gov GMP pharmaceutical quality manufacturing compliance",
    ),
    (
        # EMA GMP/Scientific Guidelines
        "EMA_GMP_GUIDELINES",
        "site:ema.europa.eu GMP guideline consultation pharmaceutical manufacturing",
    ),
    (
        # PIC/S: Annex, PI 문서, 새 가이드
        "PICS_GMP",
        "site:picscheme.org GMP Annex publication guidance",
    ),
    (
        # ICH Quality Guidelines: Q 시리즈 Step 2/4
        "ICH_QUALITY",
        "site:ich.org quality guideline Step 2 Step 4 Q1 Q2 Q9 Q10 Q12 Q14",
    ),
    (
        # TGA (Australia): GMP, 제조 인가, 실사
        "TGA_GMP",
        "site:tga.gov.au GMP manufacturing inspection pharmaceutical",
    ),
    (
        # WHO + Health Canada: 글로벌 GMP 가이던스
        "WHO_HEALTH_CANADA",
        "(site:who.int OR site:canada.ca) GMP pharmaceutical manufacturing quality",
    ),
    (
        # Deep Dive: PMDA/HSA/EDQM/Swissmedic — 아시아·유럽 규제기관 로테이션
        "DEEP_DIVE_GLOBAL",
        "(site:pmda.go.jp OR site:hsa.gov.sg OR site:edqm.eu OR site:swissmedic.ch) pharmaceutical GMP guidance",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────────────


def _search_doc_id(url: str) -> str:
    """URL 기반 stable document_id (SHA-1 12자).
    URL이 동일하면 항상 동일 ID → dedupe 보장.
    """
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def _normalize_url(url: str) -> str:
    """트래킹 파라미터 제거 후 URL 정규화."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        # scheme + netloc + path만 보존 (query/fragment 제거)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    except Exception:
        return url


def _infer_evidence_candidate(url: str) -> str:
    """URL 도메인 기반 Evidence Candidate 판정.
    공식 규제기관 도메인 → B
    그 외 (미디어, 전문지, 블로그) → C
    A는 절대 부여하지 않음 (Official API raw data 전용)
    """
    if not url:
        return "C"
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        for domain in OFFICIAL_DOMAINS:
            if netloc == domain or netloc.endswith(f".{domain}"):
                return "B"
    except Exception:
        pass
    return "C"


def _extract_date_iso(result: dict) -> str:
    """Brave Search 결과에서 날짜 추출. 없으면 빈 문자열 반환.

    오늘 날짜로 폴백하지 않는다.
    날짜를 알 수 없는 결과를 '오늘 발행'처럼 오판하면 Routine이 잘못 처리할 수 있다.
    날짜 없는 결과는 호출부에서 skip 처리한다.

    Brave Web Search API 실제 응답 필드 우선순위:
      1. page_age  — ISO 8601 datetime ("2026-05-22T10:30:00"), 가장 신뢰도 높음
      2. age       — human-readable ("May 27, 2026" / "2 days ago" 등)
                     절대 날짜 형식만 파싱; 상대 표현("N days ago")은 skip
    """
    # 1. page_age: ISO datetime (Brave Web Search API 공식 필드)
    page_age = result.get("page_age", "")
    if page_age and len(page_age) >= 10:
        return page_age[:10]  # "2026-05-22T..." → "2026-05-22"

    # 2. age: human-readable 절대 날짜만 파싱
    age = result.get("age", "").strip()
    if age:
        # "Month DD, YYYY" 형식 (예: "May 27, 2026")
        try:
            from datetime import datetime as _dt
            return _dt.strptime(age, "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
        # "YYYY-MM-DD" 형식
        if len(age) >= 10 and age[:4].isdigit() and age[4] == "-":
            return age[:10]

    return ""  # 날짜 없음 — 호출부에서 skip


def _extract_snippet(result: dict) -> str:
    """검색 결과에서 최적 snippet 추출 (200자 이하).
    extra_snippets가 있으면 가장 긴 것 선택, 없으면 description 사용.
    """
    extra = result.get("extra_snippets", [])
    if extra:
        # 가장 정보량이 많은 snippet 선택 (길이 기준)
        best = max(extra, key=len, default="")
        if best:
            return truncate(best.strip(), 200)
    desc = result.get("description", "")
    return truncate(desc.strip(), 200)


# ─────────────────────────────────────────────────────────────────────────────
# Brave Search API 클라이언트
# ─────────────────────────────────────────────────────────────────────────────


def brave_search(
    query: str,
    api_key: str,
    count: int = MAX_RESULTS_PER_QUERY,
    freshness: str = BRAVE_FRESHNESS,
    timeout: int = 15,
    retries: int = 2,
) -> list[dict]:
    """Brave Web Search API 호출. 결과 web.results 리스트 반환.

    Args:
        query: 검색 쿼리 문자열
        api_key: Brave Search API subscription token
        count: 반환할 결과 수 (기본 MAX_RESULTS_PER_QUERY = 5)
        freshness: 신선도 필터 (pw=past week, pd=24h, pm=past month)
        timeout: HTTP 타임아웃 (초)
        retries: 5xx/네트워크 오류 재시도 횟수

    Returns:
        Brave Web Search result 딕셔너리 리스트 (빈 리스트면 결과 없음 또는 오류)
    """
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params: dict[str, str | int] = {
        "q": query,
        "count": count,
        "freshness": freshness,
        "extra_snippets": "true",  # 최대 5개 추가 snippet
        "text_decorations": "false",  # HTML 태그 제거
    }

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                BRAVE_API_URL,
                headers=headers,
                params=params,
                timeout=timeout,
            )

            if resp.status_code == 429:
                # Rate limit: Retry-After 파싱 후 재시도, 소진 시 BraveSearchError
                retry_after_raw = resp.headers.get("Retry-After", "")
                try:
                    retry_after = min(int(float(retry_after_raw)), 60)
                except (ValueError, TypeError):
                    retry_after = min(2 ** attempt, 30)
                if attempt < retries:
                    log("WARN", f"Brave API 429 rate-limit q='{query[:40]}' — {retry_after}s 후 재시도")
                    time.sleep(retry_after)
                    continue
                # 재시도 소진 → 오류로 표면화 (정상 0건과 구분)
                raise BraveSearchError(
                    f"Brave API 429 rate-limit 재시도 소진 q='{query[:40]}'"
                )

            if 400 <= resp.status_code < 500:
                # 4xx: 재시도해도 동일 결과 → 즉시 오류로 표면화
                # (401=인증 실패, 403=권한 없음, 400=쿼리 오류 등)
                raise BraveSearchError(
                    f"Brave API {resp.status_code} q='{query[:40]}' body={resp.text[:200]}"
                )

            resp.raise_for_status()
            data = resp.json()
            # 200 OK + results=[] → 정상 0건. BraveSearchError 없이 빈 리스트 반환.
            return data.get("web", {}).get("results", [])

        except BraveSearchError:
            raise  # 재시도 없이 상위로 전파
        except requests.Timeout as e:
            last_err = e
            log("WARN", f"Brave API timeout q='{query[:40]}' attempt={attempt + 1}/{retries + 1}")
            if attempt < retries:
                time.sleep(2 ** attempt)
        except requests.RequestException as e:
            last_err = e
            log("WARN", f"Brave API 네트워크 오류 q='{query[:40]}' attempt={attempt + 1}/{retries + 1} err={e}")
            if attempt < retries:
                time.sleep(2 ** attempt)

    # 재시도 소진 후 네트워크/타임아웃 최종 실패 → 오류로 표면화
    raise BraveSearchError(
        f"Brave API 네트워크 최종 실패 q='{query[:40]}' last_err={last_err}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 결과 → IntakeItem 변환
# ─────────────────────────────────────────────────────────────────────────────


def _result_to_intake_item(
    result: dict,
    query: str,
    slot_name: str,
    rank: int = 0,
) -> IntakeItem | None:
    """Brave Search 결과 단건 → IntakeItem 변환.

    Args:
        result: Brave Web Search result 딕셔너리
        query: 실행한 쿼리 문자열
        slot_name: 쿼리 슬롯 이름 (로그용)
        rank: 해당 슬롯 내 결과 순위 (1-based, raw_payload에 기록)

    Returns:
        IntakeItem 또는 None (URL 없음 / 날짜 없음 / 변환 실패)
    """
    url = result.get("url", "")
    if not url:
        log("WARN", f"Brave 결과 URL 없음 slot={slot_name} — skip")
        return None

    # P0-3: 날짜 없는 결과는 skip (오늘 날짜 폴백 금지)
    date_iso = _extract_date_iso(result)
    if not date_iso:
        log("DEBUG", f"Brave 결과 날짜 없음 — skip slot={slot_name} url={url[:80]}")
        return None

    title = result.get("title", "").strip()
    if not title:
        title = url  # 제목 없으면 URL 대체

    normalized_url = _normalize_url(url)
    doc_id = _search_doc_id(normalized_url)
    snippet = _extract_snippet(result)
    evidence_candidate = _infer_evidence_candidate(url)

    # P0-2: 공식 도메인(Evidence B) 결과는 URL 자체가 L1 후보
    #   official_url = URL (공식 원본 링크로 처리)
    #   source_url   = URL (수집 출처 — 동일값)
    # 보조 도메인(Evidence C) 결과는 공식 원본을 알 수 없음
    #   official_url = "" (Routine이 snippet에서 L1 추출 또는 수동 판정)
    #   source_url   = URL (기사/페이지 URL)
    if evidence_candidate == "B":
        official_url = normalized_url
    else:
        official_url = ""

    # QA Relevance: 제목 + snippet으로 판정
    qa_relevance = compute_relevance(title, snippet)

    return IntakeItem(
        source=SOURCE_BRAVE,
        document_id=doc_id,
        date_iso=date_iso,
        headline=title,
        official_url=official_url,     # B: 공식 L1 URL / C: 빈 문자열
        source_url=normalized_url,     # 실제 발견 URL (신규 필드)
        raw_excerpt=snippet,           # ≤200자 snippet (신규 필드)
        search_query=query,            # 실행 쿼리 (신규 필드)
        evidence_candidate=evidence_candidate,  # B 또는 C (신규 필드)
        source_type=SRC_TYPE_SEARCH_RESULT,
        qa_relevance=qa_relevance,
        osd_relevance="N/A",          # Search 결과는 OpenFDA route 데이터 없음
        type_or_class="",
        firm="",
        body="",                      # 본문 fetch는 Phase 2b 이후
        distribution="",
        comments_close_iso="",
        api_query="",
        raw_payload={
            "brave_slot": slot_name,
            "brave_query": query,
            "brave_rank": rank,        # P2-1: 슬롯 내 결과 순위 (분석용)
            "brave_url": url,
            "brave_title": title,
            "brave_description": result.get("description", ""),
            "brave_date_source": (
                "published" if result.get("published") else
                "page_fetched" if result.get("page_fetched") else
                "unknown"
            ),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 메인 수집 함수
# ─────────────────────────────────────────────────────────────────────────────


def collect_brave_search(
    api_key: str,
    *,
    max_results: int = MAX_RESULTS_PER_QUERY,
    slots: list[tuple[str, str]] | None = None,
    request_delay: float = 1.0,
) -> tuple[list[IntakeItem], str | None]:
    """Brave Search API로 9개 슬롯을 실행하고 IntakeItem 리스트로 반환.

    collect_intake.py 패턴과 동일: tuple[list[IntakeItem], str | None] 반환.
    두 번째 원소는 오류 메시지 (성공 시 None).

    Args:
        api_key: Brave Search API key
        max_results: 슬롯당 최대 결과 수 (기본 5)
        slots: 커스텀 슬롯 리스트. None이면 SEARCH_SLOTS 사용
        request_delay: 슬롯 간 대기 시간 (초, rate limit 방어)

    Returns:
        (items, error_msg) — error_msg는 전체 실패 시에만 문자열
    """
    if not api_key:
        # P0-4: ENABLE_SEARCH=true 상태에서 API key가 없으면 misconfiguration
        # WARNING이 아닌 error_msg를 반환해 workflow issue 대상이 되도록 함
        msg = "BRAVE_API_KEY 누락 (ENABLE_SEARCH=true인데 API key 미설정 — misconfiguration)"
        log("ERROR", msg)
        return [], msg

    target_slots = slots or SEARCH_SLOTS
    all_items: list[IntakeItem] = []
    seen_urls: set[str] = set()  # 슬롯 간 URL 중복 방지 (doc_id와 별개)

    slot_errors: list[str] = []
    total_raw = 0

    for slot_name, query in target_slots:
        log("INFO", f"Brave Search slot={slot_name} q='{query[:60]}'")
        try:
            results = brave_search(query, api_key=api_key, count=max_results)
        except Exception as e:
            msg = f"slot={slot_name} 예외: {e}"
            log("WARN", f"Brave Search 슬롯 실패 — {msg}")
            slot_errors.append(msg)
            # graceful degradation: 한 슬롯 실패해도 다음 슬롯 계속
            if request_delay > 0:
                time.sleep(request_delay)
            continue

        total_raw += len(results)
        slot_items: list[IntakeItem] = []

        for rank, result in enumerate(results, start=1):
            url = result.get("url", "")
            normalized = _normalize_url(url)
            if normalized in seen_urls:
                log("DEBUG", f"슬롯 간 중복 URL skip — {url[:80]}")
                continue
            seen_urls.add(normalized)

            item = _result_to_intake_item(result, query, slot_name, rank=rank)
            if item is None:
                continue
            slot_items.append(item)

        log(
            "INFO",
            f"Brave slot={slot_name} raw={len(results)} "
            f"deduped={len(slot_items)} "
            f"B={sum(1 for i in slot_items if i.evidence_candidate == 'B')} "
            f"C={sum(1 for i in slot_items if i.evidence_candidate == 'C')}",
        )
        all_items.extend(slot_items)

        # 슬롯 간 rate limit 방어
        if request_delay > 0:
            time.sleep(request_delay)

    log(
        "INFO",
        f"Brave Search 완료: slots={len(target_slots)} total_raw={total_raw} "
        f"items={len(all_items)} slot_errors={len(slot_errors)}",
    )

    # 전체 실패(아이템 없음 + 모든 슬롯 오류)일 때만 error_msg 반환
    if not all_items and len(slot_errors) == len(target_slots):
        error_msg = f"Brave Search 전체 슬롯 실패: {'; '.join(slot_errors[:3])}"
        return [], error_msg

    return all_items, None


# ─────────────────────────────────────────────────────────────────────────────
# CLI (단독 실행 / 수동 테스트)
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GRM Brave Search Collector — 단독 실행 테스트")
    parser.add_argument(
        "--slot",
        choices=[s[0] for s in SEARCH_SLOTS] + ["all"],
        default="all",
        help="실행할 슬롯 (기본: all)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=MAX_RESULTS_PER_QUERY,
        help=f"슬롯당 결과 수 (기본: {MAX_RESULTS_PER_QUERY})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="슬롯 간 대기 시간 초 (기본: 1.0)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        print("ERROR: BRAVE_API_KEY 환경변수가 설정되지 않았습니다.", flush=True)
        raise SystemExit(1)

    if args.slot == "all":
        target = SEARCH_SLOTS
    else:
        target = [(s, q) for s, q in SEARCH_SLOTS if s == args.slot]

    items, err = collect_brave_search(
        api_key,
        max_results=args.max_results,
        slots=target,
        request_delay=args.delay,
    )

    print(f"\n{'='*60}")
    print(f"수집 결과: {len(items)} items  오류: {err or '없음'}")
    print(f"{'='*60}")
    for item in items:
        ev = getattr(item, "evidence_candidate", "?")
        src_url = getattr(item, "source_url", item.official_url)
        excerpt = getattr(item, "raw_excerpt", "")
        print(f"\n[{ev}] {item.headline[:80]}")
        print(f"  QA: {item.qa_relevance}  Date: {item.date_iso}")
        print(f"  URL: {src_url[:100]}")
        if excerpt:
            print(f"  Snippet: {excerpt[:120]}...")
        query = getattr(item, "search_query", "")
        print(f"  Query: {query[:60]}")
