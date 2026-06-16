"""GRM 발행물 Lint — 출처 링크 근거(provenance) 하드 가드.

URL전수검사(2026-06-16) Phase E1. 2026-06-15(W24) 발행본의 MFDS 카드 📎 가
`mfds.go.kr/brd/m_99·m_218/view.do?seq=` (보도자료/자료실 게시판) 로 **handoff 근거 없는**
엉뚱한 페이지를 가리킨 사고(AI 환각 의심)를 구조적으로 차단한다.

설계 근거(감사 결과):
- card_scaffold 는 카드 footer 의 듀얼링크를 **결정론으로** 조립한다(Intake 카드).
  Routine(LLM)은 scaffold 문자열을 한 글자도 바꾸지 않는다(v16 프롬프트 [2단계] · Publish Lint 2).
- 그러나 LLM 이 직접 URL 을 쓰는 경로가 있다 — ① 검색 카드 미니 템플릿 📎/📰(v16 L346)
  ② 🔮 Watch 표 링크(L361) ③ graceful degradation(WebSearch 단독) 모드(L151). 이 세 경로는
  코드 레벨 방어가 없고 소프트 프롬프트 규칙(L348 "패턴 유추 금지")만 막는다 → 누출 표면.
- **MFDS 는 Core 검색 슬롯이 없다**(v16 L187: "Intake 흡수가 유일 경로"). 따라서 발행물의 모든
  MFDS/nedrug 링크는 반드시 handoff(수집기 산출)에 근거가 있어야 한다. 근거 없는 mfds/nedrug
  링크 = 날조(누출) → HARD FAIL.

이 모듈은 **순수 함수**(네트워크 없음)다. handoff rows 와 발행 markdown 을 받아,
각 카드 링크가 handoff 근거 집합에 있는지 대조한다. `verify_url_live`/`looks_like_error_page`
만 선택적 네트워크 유틸(수집기 resolve&verify·Phase C 전수검증 재사용용, lazy import).

Lint 번호: 발행 직전 Publish Lint **17**(출처 링크 근거) / 독립 Brief Lint **L11**.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

SEV_FAIL = "FAIL"
SEV_WARN = "WARN"

# MFDS 계열 호스트 — Intake 전용(검색 슬롯 없음). 발행물의 이 도메인 링크는 handoff 근거 필수.
_MFDS_HOST_SUFFIXES = ("mfds.go.kr",)  # www.mfds.go.kr · nedrug.mfds.go.kr 모두 포함
# 사고 시그니처: 식약처 본사이트 게시판 직링크(보도자료 m_99·안내서/지침 m_218 등).
_MFDS_BRD_VIEW_RE = re.compile(r"mfds\.go\.kr/brd/[^/]+/view\.do\?", re.I)

# markdown 링크 `[label](url)` 및 평문 URL 추출.
_MD_LINK_RE = re.compile(r"\]\(\s*(?P<url>https?://[^)\s]+?)\s*\)")
_BARE_URL_RE = re.compile(r"https?://[^\s)\"'<>\]]+")

# nedrug 클라이언트 렌더 오류 셸 마커(HTTP 200 이어도 본문이 이것이면 무효 레코드).
# 라이브 확인(2026-06-16): getItem?dispsApplySeq=<invalid> → 200 + 이 문구.
_NEDRUG_ERROR_MARKERS = (
    "해당 화면 혹은 기능을 찾을 수 없습니다",
    "오류가 발생하였습니다",
)


@dataclass(frozen=True)
class LintFinding:
    severity: str          # SEV_FAIL | SEV_WARN
    code: str              # 예: "L17-MFDS-PROVENANCE"
    url: str
    message: str

    def __str__(self) -> str:  # 사람이 읽는 한 줄
        return f"[{self.severity}] {self.code} {self.url} — {self.message}"


def normalize_url(url: str) -> str:
    """비교용 정규화: scheme/host 소문자, fragment 제거, 끝 '/' 제거, `?&`→`?`.

    링크 자체는 바꾸지 않는다 — 근거 대조용 키만 만든다.
    """
    if not url:
        return ""
    url = url.strip().replace("&amp;", "&")
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower().rstrip("/")
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    query = parts.query
    # nedrug 등 일부가 `getItem?&dispsApplySeq=` 처럼 선행 '&' 를 쓴다 — 정규화.
    while query.startswith("&"):
        query = query[1:]
    return urlunsplit((scheme, netloc, path, query, ""))


def extract_markdown_links(text: str) -> list[str]:
    """markdown `[..](url)` + 평문 URL 전부 추출(원형 그대로)."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _MD_LINK_RE.finditer(text):
        u = m.group("url").replace("&amp;", "&")
        if u not in seen:
            seen.add(u)
            out.append(u)
    for m in _BARE_URL_RE.finditer(text):
        u = m.group(0).replace("&amp;", "&")
        # markdown 링크로 이미 잡힌 것 제외
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _walk_url_strings(obj: Any) -> Iterable[str]:
    """dict/list/str 를 깊이우선 순회하며 http(s) URL 문자열을 모은다."""
    if obj is None:
        return
    if isinstance(obj, str):
        for m in _BARE_URL_RE.finditer(obj):
            yield m.group(0).replace("&amp;", "&")
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_url_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_url_strings(v)


# handoff row 에서 URL 근거를 담는 v1 호환 필드(build_routine_handoff_payload_v2 기준).
_ROW_URL_FIELDS = ("official_url", "source_url", "api_query", "page_url")


def collect_allowed_urls(rows: list[dict[str, Any]]) -> set[str]:
    """handoff rows 가 정당화하는 URL 근거 집합(정규화 키).

    근거 = ① 각 row 의 `card_scaffold` markdown 안 링크(=scaffold 가 만든 footer 듀얼링크)
    ② v1 호환 url 필드(official_url·source_url·api_query·page_url)
    ③ `prose_input`/`raw`(있으면) 안의 url 문자열(검색카드 보강·후보 url 포함).
    """
    allowed: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for u in extract_markdown_links(row.get("card_scaffold", "") or ""):
            allowed.add(normalize_url(u))
        for f in _ROW_URL_FIELDS:
            v = row.get(f)
            if isinstance(v, str) and v.strip():
                allowed.add(normalize_url(v))
        for key in ("prose_input", "raw", "raw_payload"):
            if row.get(key):
                for u in _walk_url_strings(row.get(key)):
                    allowed.add(normalize_url(u))
    allowed.discard("")
    return allowed


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _is_mfds_host(url: str) -> bool:
    host = _host(url)
    return any(host == s or host.endswith("." + s) or host == "www." + s
              for s in _MFDS_HOST_SUFFIXES)


def lint_link_provenance(rows: list[dict[str, Any]],
                         published_markdown: str) -> list[LintFinding]:
    """발행 markdown 의 모든 링크가 handoff 근거를 갖는지 검사(Publish Lint 17).

    - MFDS/nedrug 링크인데 근거 집합에 없음 → **FAIL**(MFDS 는 Intake 전용 → 날조/누출).
      특히 `mfds.go.kr/brd/*/view.do?seq=` 는 W24 사고 시그니처라 메시지에 명시.
    - 그 외 도메인 링크인데 근거 없음 → **WARN**(검색 카드 신규 URL 가능 — 실제 fetch 확인 필요).
    근거 있는 링크는 통과. 동일 URL 중복은 1회만 보고.
    """
    allowed = collect_allowed_urls(rows)
    findings: list[LintFinding] = []
    reported: set[str] = set()
    for url in extract_markdown_links(published_markdown or ""):
        key = normalize_url(url)
        if key in allowed or key in reported:
            continue
        if _is_mfds_host(url):
            reported.add(key)
            if _MFDS_BRD_VIEW_RE.search(url):
                msg = ("MFDS 본사이트 게시판 직링크(보도자료/자료실)인데 handoff 근거 없음 — "
                       "W24 사고 시그니처. MFDS 는 검색 슬롯이 없어 Intake 근거 필수(날조 의심).")
            else:
                msg = ("MFDS/nedrug 링크인데 handoff 근거 없음 — MFDS 는 Intake 전용 경로라 "
                       "수집기 산출 URL 만 허용(LLM 생성·치환 의심).")
            findings.append(LintFinding(SEV_FAIL, "L17-MFDS-PROVENANCE", url, msg))
        else:
            reported.add(key)
            findings.append(LintFinding(
                SEV_WARN, "L17-UNVERIFIED", url,
                "handoff 근거 없는 외부 링크 — 검색 카드 신규 URL 이면 실제 확인(fetch)했는지 "
                "검증, 아니면 패턴 유추(환각) 의심."))
    return findings


def has_failures(findings: Iterable[LintFinding]) -> bool:
    return any(f.severity == SEV_FAIL for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# 선택적 네트워크 유틸 — 수집기 resolve&verify(Phase E2)·Phase C 전수검증 재사용용.
# 순수 lint 는 이걸 호출하지 않는다(테스트는 HTTP 스텁으로 검증).
# ─────────────────────────────────────────────────────────────────────────────
def looks_like_error_page(body: str) -> bool:
    """nedrug 클라이언트 렌더 오류 셸 판정(HTTP 200 이어도 무효 레코드).

    라이브 확인(2026-06-16): `CCBAO01/getItem?dispsApplySeq=<invalid>` 는 HTTP 200 +
    길이 ~2.6KB + 이 마커. 유효 seq 는 ~80KB + 마커 부재. **상태코드로는 구분 불가**.
    """
    if not body:
        return True
    return any(m in body for m in _NEDRUG_ERROR_MARKERS)


def verify_url_live(url: str, expect_terms: Iterable[str] = (),
                    timeout: int = 20, min_len: int = 5000) -> dict[str, Any]:
    """URL 을 실제 GET 해 (1) 200, (2) 오류 셸 아님, (3) 본문에 expect_terms 포함 여부 판정.

    반환 dict: {ok, status, length, is_error_page, missing_terms, error}. 네트워크 실패는
    예외 대신 ok=False+error 로 돌려준다(수집기 graceful degrade 용). requests lazy import.
    """
    try:
        import requests  # lazy — 순수 lint 경로는 의존하지 않음
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "status": 0, "length": 0, "is_error_page": True,
                "missing_terms": list(expect_terms), "error": f"requests 미설치: {exc}"}
    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0 GRM-URL-Audit/1.0"})
    except Exception as exc:
        return {"ok": False, "status": 0, "length": 0, "is_error_page": True,
                "missing_terms": list(expect_terms), "error": str(exc)[:200]}
    body = r.text or ""
    is_err = looks_like_error_page(body)
    missing = [t for t in expect_terms if t and t not in body]
    ok = (r.status_code == 200) and (not is_err) and (len(body) >= min_len) and not missing
    return {"ok": ok, "status": r.status_code, "length": len(body),
            "is_error_page": is_err, "missing_terms": missing, "error": ""}
