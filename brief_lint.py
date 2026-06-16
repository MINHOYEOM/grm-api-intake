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

W1(발행 게이트): `run_publish_gate(rows, markdown)` + `format_report` + CLI(`python -m brief_lint
--handoff h.json --published brief.md`)로 "검사기 존재" 를 "매 발행마다 실행·FAIL 시 차단(exit 1)"
으로 승격한다 — 프롬프트 "지시" 가 아니라 결정론 실행·차단.
W2(전 기관 일반화): `policy=ALL_DOMAINS` 면 MFDS 뿐 아니라 모든 미근거 외부 링크를, 세션 fetch
화이트리스트(`allowed_fetched`)나 live verify(`verifier`) 로 정당화되지 않는 한 FAIL 처리한다.
기본 `lint_link_provenance(...)` 시그니처 동작은 종전과 동일(MFDS 한정 FAIL·그 외 WARN)이라
기존 호출처·테스트는 무회귀.

Lint 번호: 발행 직전 Publish Lint **17**(출처 링크 근거) / 독립 Brief Lint **L11**.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

SEV_FAIL = "FAIL"
SEV_WARN = "WARN"

# provenance 정책(W2 — 전 기관 일반화). 기본은 종전 동작(MFDS 한정 FAIL·그 외 WARN)을
# 그대로 보존해 기존 호출처·테스트가 무회귀하도록 한다. 발행 게이트(run_publish_gate)는
# ALL_DOMAINS 로 옵트인해 "지어낸 타 기관 URL" 도 차단한다.
POLICY_MFDS_ONLY = "mfds_only"      # 기본: MFDS/nedrug 미근거=FAIL · 그 외 미근거=WARN
POLICY_ALL_DOMAINS = "all_domains"  # 게이트: 미근거 외부 링크도 (fetch 화이트리스트∨live verify) 아니면 FAIL

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
                         published_markdown: str,
                         *,
                         policy: str = POLICY_MFDS_ONLY,
                         allowed_fetched: Iterable[str] = (),
                         verifier: "Any | None" = None) -> list[LintFinding]:
    """발행 markdown 의 모든 링크가 handoff 근거를 갖는지 검사(Publish Lint 17).

    근거 집합 = handoff rows 가 정당화하는 URL(`collect_allowed_urls`).
    판정(중복 URL 은 1회만 보고):
    - MFDS/nedrug 링크인데 근거 없음 → **FAIL**(MFDS 는 Core 검색 슬롯이 없어 Intake 전용 →
      날조/누출). 특히 `mfds.go.kr/brd/*/view.do?seq=` 는 W24 사고 시그니처라 메시지에 명시.
      MFDS 특례는 `allowed_fetched`·verify 로도 구제되지 않는다(검색 카드 자체가 금지).
    - 그 외 도메인 링크인데 근거 없음:
        · `allowed_fetched`(이번 세션에 실제 fetch·확인한 URL)에 있으면 통과(W2).
        · `policy=ALL_DOMAINS` 면 verifier(있으면 live verify) 통과 시 통과, 아니면 **FAIL**.
        · `policy=MFDS_ONLY`(기본) 면 **WARN**(종전 동작 보존 — 무회귀).

    `policy`/`allowed_fetched`/`verifier` 의 기본값은 종전 시그니처 동작과 동일하다
    (MFDS 한정 FAIL·그 외 WARN). 발행 게이트는 `run_publish_gate` 로 ALL_DOMAINS 옵트인.
    """
    allowed = collect_allowed_urls(rows)
    urls = extract_markdown_links(published_markdown or "")
    return lint_urls(urls, allowed, policy=policy,
                     allowed_fetched=allowed_fetched, verifier=verifier)


def lint_urls(urls: Iterable[str],
              allowed: "set[str]",
              *,
              policy: str = POLICY_MFDS_ONLY,
              allowed_fetched: Iterable[str] = (),
              verifier: "Any | None" = None) -> list[LintFinding]:
    """근거 대조 코어 — 링크 추출 방식과 무관(markdown 경로/Notion 블록 URL 경로 공용).

    `allowed` = `collect_allowed_urls(rows)` 가 만든 근거 키 집합(정규화됨).
    `urls` = 발행물에서 추출한 링크(원형). 판정 규칙은 `lint_link_provenance` 와 동일.
    """
    allowed_fetched_keys = {normalize_url(u) for u in allowed_fetched if u}
    findings: list[LintFinding] = []
    reported: set[str] = set()
    for url in urls:
        key = normalize_url(url)
        if not key or key in allowed or key in reported:
            continue
        reported.add(key)
        if _is_mfds_host(url):
            # MFDS 특례: 검색 슬롯이 없어 fetch 화이트리스트·verify 로도 구제 불가.
            if _MFDS_BRD_VIEW_RE.search(url):
                msg = ("MFDS 본사이트 게시판 직링크(보도자료/자료실)인데 handoff 근거 없음 — "
                       "W24 사고 시그니처. MFDS 는 검색 슬롯이 없어 Intake 근거 필수(날조 의심).")
            else:
                msg = ("MFDS/nedrug 링크인데 handoff 근거 없음 — MFDS 는 Intake 전용 경로라 "
                       "수집기 산출 URL 만 허용(LLM 생성·치환 의심).")
            findings.append(LintFinding(SEV_FAIL, "L17-MFDS-PROVENANCE", url, msg))
            continue
        if key in allowed_fetched_keys:
            # 비-MFDS · 이번 세션에 실제 fetch·확인한 URL → 근거 있음(검색 카드 정당 경로).
            continue
        if policy == POLICY_ALL_DOMAINS:
            if verifier is not None:
                try:
                    if verifier(url):
                        continue  # live verify 통과(200·오류셸 아님·기대어구 포함) → 통과
                except Exception:  # noqa: BLE001 — verify 실패는 미검증으로 취급(차단 측 안전)
                    pass
            findings.append(LintFinding(
                SEV_FAIL, "L17-UNGROUNDED", url,
                "handoff 근거도 세션 fetch 화이트리스트도 없는 외부 링크 — 패턴 유추(환각) "
                "의심. 검색 카드면 이번 run 에 실제 fetch 해 확인한 URL 만 허용."))
        else:
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


def live_verifier(expect_terms: Iterable[str] = (), **kwargs: Any):
    """ALL_DOMAINS 정책에서 미근거 비-MFDS 링크를 실시간 검증하는 verifier 콜백 팩토리.

    `lint_urls(..., verifier=live_verifier())` 처럼 주입한다. 네트워크가 필요하므로
    순수 lint(예방 게이트)에는 쓰지 않고, 탐지(detective)·전수검증 경로에서만 쓴다.
    """
    terms = tuple(expect_terms)

    def _verify(url: str) -> bool:
        return bool(verify_url_live(url, expect_terms=terms, **kwargs).get("ok"))

    return _verify


# ─────────────────────────────────────────────────────────────────────────────
# 발행 직전 blocking 게이트(W1) — "검사기 존재" 를 "매 발행마다 실행·FAIL 시 차단" 으로.
# 순수 함수(네트워크 없음, verifier 주입 시에만 네트워크). Routine·CI·독립 세션 공용.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GateResult:
    ok: bool                         # FAIL 0 이면 True(발행 허용), FAIL≥1 이면 False(차단)
    findings: list[LintFinding]
    report: str                      # 사람이 읽는 한 줄 요약 + 항목

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEV_FAIL)

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEV_WARN)


def format_report(findings: list[LintFinding]) -> str:
    """게이트 결과를 사람이 읽는 텍스트로(FAIL 우선). 채팅/CI 로그/Issue 본문 공용."""
    fails = [f for f in findings if f.severity == SEV_FAIL]
    warns = [f for f in findings if f.severity == SEV_WARN]
    if not findings:
        return "[PASS] 출처 링크 근거(provenance) 게이트 — 위반 0 (발행 허용)"
    head = (f"[{'FAIL' if fails else 'PASS(경고)'}] provenance 게이트 — "
            f"FAIL {len(fails)} · WARN {len(warns)}")
    lines = [head]
    for f in fails:
        lines.append(f"  ✖ {f}")
    for f in warns:
        lines.append(f"  ⚠ {f}")
    if fails:
        lines.append("→ 발행 중단: 위 FAIL 링크의 근거를 확보(Intake 카드는 scaffold 링크 복원 / "
                     "검색 날조면 출처 줄 제거·카드 보류)한 뒤 다시 게이트를 통과시켜야 발행한다.")
    return "\n".join(lines)


def run_publish_gate(rows: list[dict[str, Any]],
                     published_markdown: str,
                     *,
                     policy: str = POLICY_ALL_DOMAINS,
                     allowed_fetched: Iterable[str] = (),
                     verifier: "Any | None" = None) -> GateResult:
    """발행 직전(또는 발행 후 탐지) provenance 게이트 1회 실행.

    기본 정책 = ALL_DOMAINS(전 기관 일반화, W2). `allowed_fetched` = 이번 세션에 실제
    fetch·확인한 검색 카드 URL(있으면 그 비-MFDS 링크는 근거로 인정). `verifier` 주입 시
    미근거 비-MFDS 링크를 live verify(탐지 경로). 반환 `GateResult.ok=False` 면 **발행 차단**.
    """
    findings = lint_link_provenance(rows, published_markdown, policy=policy,
                                    allowed_fetched=allowed_fetched, verifier=verifier)
    return GateResult(ok=not has_failures(findings), findings=findings,
                      report=format_report(findings))


# ─────────────────────────────────────────────────────────────────────────────
# CLI — 결정론 발행 게이트를 셸/CI/Routine(코드 실행 가능 환경)에서 강제 실행.
#   python -m brief_lint --handoff handoff.json --published brief.md [--policy ...] [--verify]
# handoff.json = handoff v2 payload({"rows":[...]}) 또는 Notion handoff 페이지 export.
# published    = 발행 markdown/텍스트(예: Notion 페이지 export). FAIL 시 exit 1(발행 차단).
# ─────────────────────────────────────────────────────────────────────────────
def extract_handoff_rows(obj: Any) -> list[dict[str, Any]]:
    """다양한 형태의 handoff JSON 에서 `rows[]` 를 끌어낸다(payload·page·list 공용)."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        rows = obj.get("rows")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        # Notion 페이지 형태로 감싸졌으면 본문 code block JSON 을 재귀 탐색.
        for key in ("payload", "handoff", "body", "content"):
            inner = obj.get(key)
            if isinstance(inner, (dict, list)):
                got = extract_handoff_rows(inner)
                if got:
                    return got
    return []


def _load_handoff_rows(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # 본문에 ```json ... ``` code fence 로 감싼 경우 첫 JSON 객체만 추출.
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        obj = json.loads(m.group(0))
    return extract_handoff_rows(obj)


def _read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def main(argv: "list[str] | None" = None) -> int:
    import argparse
    # Windows 콘솔(cp949) 에서 한글·em-dash 출력 크래시 방지(probe_*.py 동형).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(
        prog="brief_lint",
        description="GRM 발행 직전 출처 링크 근거(provenance) 게이트 — FAIL 시 exit 1(발행 차단).")
    p.add_argument("--handoff", required=True,
                   help="handoff v2 JSON 경로(rows[] 포함 — payload·page·list 허용).")
    p.add_argument("--published", required=True,
                   help="발행 markdown/텍스트 경로(Notion 페이지 export 등).")
    p.add_argument("--allowed-fetched", default=None,
                   help="(선택) 이번 세션에 실제 fetch·확인한 URL 목록 파일(줄당 1 URL).")
    p.add_argument("--policy", choices=[POLICY_MFDS_ONLY, POLICY_ALL_DOMAINS],
                   default=POLICY_ALL_DOMAINS,
                   help="기본 all_domains(전 기관 일반화). mfds_only=종전 동작.")
    p.add_argument("--verify", action="store_true",
                   help="미근거 비-MFDS 링크를 live verify(네트워크). 탐지 경로용.")
    args = p.parse_args(argv)

    try:
        rows = _load_handoff_rows(args.handoff)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] handoff 로드 실패: {exc}", file=sys.stderr)
        return 2
    try:
        with open(args.published, "r", encoding="utf-8") as fh:
            published = fh.read()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 발행물 로드 실패: {exc}", file=sys.stderr)
        return 2
    allowed_fetched = _read_lines(args.allowed_fetched) if args.allowed_fetched else ()
    verifier = live_verifier() if args.verify else None

    result = run_publish_gate(rows, published, policy=args.policy,
                              allowed_fetched=allowed_fetched, verifier=verifier)
    print(result.report)
    print(f"(handoff rows={len(rows)} · 근거 URL={len(collect_allowed_urls(rows))})",
          file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
