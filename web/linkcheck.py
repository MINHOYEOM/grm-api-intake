#!/usr/bin/env python3
"""GRM 링크체크 (P3·C1) — 배포 전 공식 링크 200 체크 → `link_check` enrich.

렌더러(`web/render.py`)와 **분리된 배포단계** 스크립트. `web/data/briefs/*.json`(주차별
브리프)의 각 카드 `sources.info_url`·`sources.official_url` 에 HEAD(폴백 GET) 요청을 보내
도달 상태를 산정하고, 그 카드의 `sources.link_check.{info,official}` 를 enrich 한 **사본**을
`--out` 디렉터리에 쓴다(원본 비파괴). 렌더러는 enrich 된 `link_check` 값을 *읽기만* 한다
(P2 가 이미 상태 분기 마크업 보유 — ok/pending→링크, broken→"일시 접근불가", degraded→⚠️).

설계 불변식 (P3 §1·§3.2):
  1. **렌더러 순수성 보존** — 네트워크는 *이 파일*에만. render.py 에 fetch 도입 0.
  2. **비차단(D7)** — 깨진 링크는 *표시/보류*이지 배포 중단이 아니다. 본 스크립트는
     네트워크 실패가 있어도 항상 exit 0 (경고만). 모든 입력 JSON 에 대해 유효한 사본을
     산출(체크 못 한 URL 은 상태 보존).
  3. **무변형** — 사실·URL·디자인 무변경. `link_check` 상태 라벨만 주입.
  4. **KR-egress 오탐 방지** — 국내 정부/공공 도메인(`*.go.kr`·`*.or.kr`)은 클라우드
     egress 에서 방화벽/봇거부될 수 있어(수집기 KR-egress 이력) **체크 스킵 → "ok" 유지**.
     클라우드 비-200 을 실제 깨짐(false broken)으로 오인하지 않는다.

상태 산정 표 (`classify_status`) — **false-broken 방지(§3.2)를 §3.1 리터럴 표보다 우선**:
  · 봇월 인터스티셜(최종 URL)  → "inconclusive" → 상태 보존.  ★중요(실측 FDA)
        최종 URL 이 apology/challenge/captcha(Akamai·Cloudflare·Imperva 등) 표식이면,
        혹은 *다른 호스트*로 리다이렉트된 에러 응답이면 — 상태코드 무관 단정 금지.
  · 2xx/3xx(최종)            → "ok"
  · 401·403                  → "inconclusive" → **상태 변경 없음(보존)**.  ★중요
        HEAD/GET 의 401·403 은 대부분 봇방어(WAF·Akamai/Cloudflare)이지 죽은 링크가 아니다.
        실측: www.fda.gov 가 자동 HEAD/GET 에 403 — "broken"(클릭 비활성) 처리하면 멀쩡한
        공식 링크를 끊는 false-broken 이 된다. KR 화이트리스트와 같은 원리의 **글로벌판** —
        단정하지 않고 기존 상태(보통 "pending"→정상 링크)를 보존한다.
  · 404·410                  → "broken"     (확실히 사라짐 — 클릭 비활성 "일시 접근불가")
  · 5xx(500·502·503·504…)    → "degraded"   (서버측·일시 가능 — 링크 살리고 ⚠️)
  · 408·429                  → "degraded"   (타임아웃·레이트리밋 — 일시)
  · 그 외 4xx(400·451…)       → "broken"
  · 타임아웃(재시도 소진)     → "degraded"
  · 연결 실패(DNS·거부)       → "broken"
  · 빈 URL                    → 변경 없음(상태 보존: 보통 "pending")
  · KR-egress 화이트리스트     → "ok" (네트워크 스킵)

  ※ §3.1 리터럴은 "4xx/5xx→broken" 이나, §3.2(주의·중요)가 *오탐 방지*를 핵심 가드로 명시한다.
    위 표는 그 우선순위를 401/403(봇방어)·5xx(일시)에 일관 적용한 정련 — Codex 검증 대상.

결정론 주의: 본 스크립트는 네트워크라 **비결정** → 렌더러 골든 테스트에 포함 금지.
자체 검증은 모킹 단위테스트(`web/tests/test_linkcheck.py`: 200/404/503/timeout/KR-skip/
HEAD→GET 폴백)로 한다.

사용:
  python web/linkcheck.py --data web/data/briefs --out <staging-dir>   # 비파괴 enrich
  python web/linkcheck.py --in-place                                   # 원본 덮어쓰기(사람 판단)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import requests

# ── 경로(이 파일 기준 — cwd 무관) ──────────────────────────────────────────────
WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data" / "briefs"

# ── 상태 라벨(card.html 의 src.state 분기와 동일 어휘) ─────────────────────────
OK = "ok"
BROKEN = "broken"
DEGRADED = "degraded"
# 내부 전용 — link_check 에 기록되지 않는다(enrich 가 보존-스킵). card.html 은 못 본다.
INCONCLUSIVE = "inconclusive"

# ── 네트워크 정책 ──────────────────────────────────────────────────────────────
USER_AGENT = "GRM-LinkCheck/1.0 (+regulatory monitor; HEAD probe; non-intrusive)"
DEFAULT_TIMEOUT = 10.0          # 초
DEFAULT_RETRIES = 1            # 타임아웃/일시오류 재시도(총 시도 = retries+1)
DEFAULT_HOST_DELAY = 0.3       # 동일 호스트 연속 요청 간 최소 간격(초) — 예의

# HEAD 를 거부/미지원하는 서버에 대해 GET 으로 폴백할 상태코드.
_HEAD_FALLBACK_GET = frozenset({403, 405, 406, 501})
# 봇방어 추정(WAF) → 단정 금지(inconclusive, 상태 보존).
_INCONCLUSIVE_CODES = frozenset({401, 403})
# 확실히 사라짐 → broken.
_GONE_CODES = frozenset({404, 410})
# 일시·과부하 → degraded(보류). 5xx 전체도 degraded(서버측·일시 가능).
_TRANSIENT_4XX = frozenset({408, 429})

# KR-egress 화이트리스트(체크 스킵 → "ok"). 클라우드 egress 차단·봇거부로 인한
# false broken 방지. 호스트의 *접미사*로 매칭(서브도메인 포함). 튜닝 가능.
#   예: nedrug.mfds.go.kr / www.mfds.go.kr / apis.data.go.kr / *.or.kr
KR_SKIP_SUFFIXES = ("go.kr", "or.kr")

# 봇방어(WAF) 인터스티셜 페이지 표식 — 최종 URL 에 이 토큰이 있으면 봇월로 보고
# INCONCLUSIVE(보존). 실측: www.fda.gov 가 클라우드 IP 를 /apology_objects/abuse-detection…
# (HTTP 404)로 리다이렉트 → 그대로면 false-broken. 클라우드에선 FDA 모든 링크가 이 형태라
# 죽음/살아있음 구분 불가 → 끊지 말고 보존이 정답. 토큰은 사이트별 봇방어 시그니처(튜닝 가능).
_BOTWALL_MARKERS = (
    "abuse-detection", "apology_objects", "apology",   # Akamai
    "cdn-cgi/challenge", "__cf_chl", "challenges.cloudflare",  # Cloudflare
    "_incapsula_resource", "incapsula",                # Imperva/Incapsula
    "distil_r_captcha", "px-captcha", "captcha", "are-you-human",  # Distil/PerimeterX/일반
)


# ── 순수 헬퍼(네트워크 없음 — 단위테스트 용이) ────────────────────────────────
def classify_status(code: int) -> str:
    """HTTP 상태코드 → link_check 상태(순수). false-broken 방지 우선(§3.2).

    INCONCLUSIVE 는 link_check 에 기록되지 않고 enrich 가 기존 상태를 보존한다.
    """
    if 200 <= code < 400:
        return OK
    if code in _INCONCLUSIVE_CODES:      # 401·403 봇방어 추정 → 단정 안 함
        return INCONCLUSIVE
    if code in _GONE_CODES:              # 404·410 → 확실히 끊김
        return BROKEN
    if 500 <= code < 600:               # 5xx → 서버측·일시 가능 → 링크 살리고 ⚠️
        return DEGRADED
    if code in _TRANSIENT_4XX:          # 408·429 → 일시
        return DEGRADED
    return BROKEN                        # 그 외 4xx(400·451…)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_kr_skip(url: str) -> bool:
    """국내 정부/공공(`*.go.kr`·`*.or.kr`) → 체크 스킵 대상(순수)."""
    host = host_of(url)
    if not host:
        return False
    return any(host == s or host.endswith("." + s) for s in KR_SKIP_SUFFIXES)


def looks_botwall(final_url: str) -> bool:
    """최종 URL 이 봇방어 인터스티셜(apology/challenge/captcha…)인지(순수)."""
    u = (final_url or "").lower()
    return any(m in u for m in _BOTWALL_MARKERS)


# ── 단일 URL 체크(네트워크 — session 주입으로 모킹) ───────────────────────────
def _probe(session: requests.Session, url: str, timeout: float):
    """HEAD 우선, 거부 코드면 GET 폴백. 응답(또는 예외 전파)."""
    resp = session.head(url, allow_redirects=True, timeout=timeout)
    if resp.status_code in _HEAD_FALLBACK_GET:
        resp = session.get(url, allow_redirects=True, timeout=timeout, stream=True)
        # 본문은 받지 않는다(상태만). 연결 반환.
        try:
            resp.close()
        except Exception:
            pass
    return resp


def check_url(url: str, session: requests.Session, *,
              timeout: float = DEFAULT_TIMEOUT,
              retries: int = DEFAULT_RETRIES) -> str:
    """URL 도달 상태 산정. 봇월 인터스티셜·교차호스트 에러 리다이렉트→inconclusive(보존),
    타임아웃→degraded·연결실패→broken·재시도 소진 반영."""
    last_exc: Exception | None = None
    for _ in range(retries + 1):
        try:
            resp = _probe(session, url, timeout)
            final = getattr(resp, "url", "") or url
            code = resp.status_code
            # 봇방어 인터스티셜(apology/challenge/captcha) → 단정 금지(false-broken 방지).
            if looks_botwall(final):
                return INCONCLUSIVE
            # 다른 호스트로 리다이렉트된 *에러* 응답 → 봇월/차단 추정 → 보존.
            if not (200 <= code < 400) and host_of(final) != host_of(url):
                return INCONCLUSIVE
            return classify_status(code)
        except requests.exceptions.Timeout as e:
            last_exc = e          # 일시 — 재시도
            continue
        except requests.exceptions.ConnectionError as e:
            last_exc = e          # 연결 실패 — 재시도 후 broken
            continue
        except requests.exceptions.RequestException as e:
            last_exc = e          # 그 외 요청 예외 — broken
            break
    if isinstance(last_exc, requests.exceptions.Timeout):
        return DEGRADED
    return BROKEN


# ── 호스트별 예의 백오프(연속 동일 호스트 요청 간 최소 간격) ──────────────────
class _HostThrottle:
    def __init__(self, min_delay: float, sleeper: Callable[[float], None],
                 clock: Callable[[], float]):
        self._min = min_delay
        self._sleep = sleeper
        self._clock = clock
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        if self._min <= 0 or not host:
            return
        prev = self._last.get(host)
        now = self._clock()
        if prev is not None:
            gap = self._min - (now - prev)
            if gap > 0:
                self._sleep(gap)
                now = self._clock()
        self._last[host] = now


def make_checker(session: requests.Session, *,
                 timeout: float = DEFAULT_TIMEOUT,
                 retries: int = DEFAULT_RETRIES,
                 host_delay: float = DEFAULT_HOST_DELAY,
                 sleeper: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> Callable[[str], str]:
    """URL→상태 체커 클로저. 런 단위 캐시(같은 URL 1회)·KR 스킵·호스트 백오프 포함."""
    throttle = _HostThrottle(host_delay, sleeper, clock)
    cache: dict[str, str] = {}

    def checker(url: str) -> str:
        if url in cache:
            return cache[url]
        if is_kr_skip(url):
            cache[url] = OK                      # KR-egress: 스킵 → ok 유지
            return OK
        throttle.wait(host_of(url))
        state = check_url(url, session, timeout=timeout, retries=retries)
        cache[url] = state
        return state

    return checker


# ── 브리프 enrich(체커 주입 — 순수 로직) ──────────────────────────────────────
def enrich_brief(brief: dict[str, Any], checker: Callable[[str], str]) -> dict[str, int]:
    """brief.cards[*].sources.link_check 를 in-place enrich. 상태별 건수 반환.

    상태를 *기록하지 않는*(보존) 두 경우 — 빈 URL(누락)·INCONCLUSIVE(401/403 봇방어) —
    는 lc[role] 을 건드리지 않는다(보통 "pending" 유지 → 정상 링크 렌더).
    """
    tally = {OK: 0, BROKEN: 0, DEGRADED: 0, INCONCLUSIVE: 0, "skipped": 0}
    for card in brief.get("cards") or []:
        src = card.get("sources")
        if not isinstance(src, dict):
            continue
        lc = src.get("link_check")
        if not isinstance(lc, dict):
            lc = {}
            src["link_check"] = lc
        for role, key in (("info", "info_url"), ("official", "official_url")):
            url = (src.get(key) or "").strip()
            if not url:
                tally["skipped"] += 1
                continue
            state = checker(url)
            if state == INCONCLUSIVE:
                tally[INCONCLUSIVE] += 1       # 보존: lc[role] 변경 없음
                continue
            lc[role] = state
            tally[state] = tally.get(state, 0) + 1
    return tally


# ── 오케스트레이션 ────────────────────────────────────────────────────────────
def _iter_briefs(data_dir: Path) -> Iterable[Path]:
    return sorted(data_dir.glob("*.json"))


def run(data_dir: Path = DATA_DIR, out_dir: Path | None = None, *,
        in_place: bool = False,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        host_delay: float = DEFAULT_HOST_DELAY,
        checker: Callable[[str], str] | None = None) -> dict[str, Any]:
    """data_dir 의 브리프를 enrich. in_place 면 원본 덮어쓰기, 아니면 out_dir 에 사본.

    항상 유효한 사본을 산출(비차단 D7): URL 별 예외는 check_url 이 흡수, JSON 파싱
    실패 파일은 그대로 복사-통과(상태 보존)하고 경고만 남긴다.
    """
    if in_place:
        target = data_dir
    else:
        if out_dir is None:
            raise ValueError("out_dir 가 필요합니다(--out) 또는 --in-place 를 쓰세요.")
        target = out_dir
        target.mkdir(parents=True, exist_ok=True)

    own_session = False
    if checker is None:
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT})
            own_session = True
        checker = make_checker(session, timeout=timeout, retries=retries,
                               host_delay=host_delay)

    totals = {OK: 0, BROKEN: 0, DEGRADED: 0, INCONCLUSIVE: 0, "skipped": 0}
    files = 0
    warnings: list[str] = []
    try:
        for fp in _iter_briefs(data_dir):
            raw = fp.read_text(encoding="utf-8")
            try:
                brief = json.loads(raw)
            except json.JSONDecodeError as e:
                # 파싱 불가 — 비차단: 원본 그대로 통과(in_place 면 변경 없음).
                warnings.append(f"{fp.name}: JSON 파싱 실패({e}) — 변경 없이 통과")
                if not in_place:
                    (target / fp.name).write_text(raw, encoding="utf-8")
                continue
            tally = enrich_brief(brief, checker)
            for k, v in tally.items():
                totals[k] = totals.get(k, 0) + v
            out_text = json.dumps(brief, ensure_ascii=False, indent=2) + "\n"
            (target / fp.name).write_bytes(out_text.encode("utf-8"))
            files += 1
    finally:
        if own_session and session is not None:
            session.close()

    return {"data_dir": str(data_dir), "out_dir": str(target), "files": files,
            "totals": totals, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="GRM 링크체크 — 배포 전 link_check enrich(비차단·KR-egress 관대)")
    ap.add_argument("--data", type=Path, default=DATA_DIR, help="브리프 JSON 디렉터리")
    ap.add_argument("--out", type=Path, default=None,
                    help="enrich 사본 출력 디렉터리(비파괴). 미지정 시 --in-place 필요")
    ap.add_argument("--in-place", action="store_true",
                    help="원본 덮어쓰기(사람 판단). --out 무시")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    ap.add_argument("--host-delay", type=float, default=DEFAULT_HOST_DELAY,
                    help="동일 호스트 연속 요청 최소 간격(초)")
    args = ap.parse_args(argv)

    if not args.in_place and args.out is None:
        # 비차단 철학상 에러로 죽이지 않되, 인자 오용은 즉시 알린다(네트워크 전).
        print("⚠️  --out 또는 --in-place 필요. 아무 것도 하지 않고 종료(exit 0).",
              file=sys.stderr)
        return 0

    meta = run(args.data, args.out, in_place=args.in_place,
               timeout=args.timeout, retries=args.retries, host_delay=args.host_delay)
    t = meta["totals"]
    print(f"링크체크 완료: {meta['files']}개 브리프 → {meta['out_dir']}")
    print(f"  ok={t.get(OK, 0)}  degraded={t.get(DEGRADED, 0)}  "
          f"broken={t.get(BROKEN, 0)}  inconclusive(401/403 보존)={t.get(INCONCLUSIVE, 0)}  "
          f"skipped(빈 URL)={t.get('skipped', 0)}")
    for w in meta["warnings"]:
        print(f"  ⚠️  {w}", file=sys.stderr)
    if t.get(BROKEN, 0) or t.get(DEGRADED, 0):
        # 비차단(D7): 깨짐/보류는 *경고*일 뿐 — exit 0 유지(배포 계속).
        print(f"  ℹ️  broken/degraded 발견 — 표시·보류이며 배포 비차단(D7).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
