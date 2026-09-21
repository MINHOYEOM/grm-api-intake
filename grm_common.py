#!/usr/bin/env python3
"""Shared runtime helpers for GRM collectors."""

from __future__ import annotations

import os
import re
import socket
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import urlencode, urlparse
from typing import Any

import requests


DEFAULT_USER_AGENT = "GRM-Intake/1.1 (+github-actions)"
DEFAULT_XML_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}
DEFAULT_JSON_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json",
}
# ★`apis.data.go.kr`(공공데이터포털 오픈API — 1471000 식약처·1170000 법제처)도 KR egress 로
#   보낸다. 이 호스트는 2026-08-24 까지 러너에서 직접 열렸으나, 해외 IP 를 며칠 단위로
#   조용히 떨어뜨린다(연결 자체가 timeout — 08-02~08-05 4연속, 08-24~08-26 3연속 실측).
#   www.mfds.go.kr 이 2026-08-10 에 보인 것과 같은 양상이다.
MFDS_EGRESS_HOSTS = {
    "www.mfds.go.kr",
    "nedrug.mfds.go.kr",
    "www.law.go.kr",
    "apis.data.go.kr",
}


class HTTPClientError(RuntimeError):
    """HTTP 4xx error with status code attached."""

    def __init__(self, status_code: int, url: str, msg: str = "") -> None:
        super().__init__(msg or f"HTTP {status_code} for {url}")
        self.status_code = status_code
        self.url = url


def log(level: str, msg: str) -> None:
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {level} {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, flush=True)


def env_flag(name: str, default: bool = False) -> bool:
    """ENABLE_* 플래그 단일 파서 — truthy = {"1","true","yes","on"} (case/공백 무시)."""
    val = (os.environ.get(name) or "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def retry_after_seconds(resp: requests.Response, attempt: int, *, max_sleep: int = 60) -> int:
    raw = resp.headers.get("Retry-After", "")
    try:
        return min(int(float(raw)), max_sleep)
    except (TypeError, ValueError):
        return min(2 ** attempt, max_sleep)


def proxies_for(url: str) -> dict[str, str] | None:
    """Return an opt-in KR egress proxy only for MFDS/law.go.kr hosts.

    공개 API 다 — `requests` 를 직접 쓰는 모듈(예: library_linkcheck)도 이 함수를 거쳐야
    한다. MFDS 는 해외 러너 IP 를 런 단위로 거부하므로, 프록시를 안 태운 경로는 같은
    호스트를 같은 시각에 한쪽은 2초에 받고 한쪽은 9분 내내 못 받는다(2026-08-10 사고).
    """
    proxy = os.environ.get("MFDS_HTTP_PROXY", "").strip()
    if not proxy:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host in MFDS_EGRESS_HOSTS:
        return {"http": proxy, "https": proxy}
    return None


# 옛 이름 — 기존 호출부·테스트 호환.
_proxies_for = proxies_for


# ── [2026-09-14] KR egress 프록시 홉 실패 시 직결 1회 폴백 ────────────────────
# 사고: `MFDS_HTTP_PROXY` 한 대(52.79.207.141:8888)가 connection timeout 으로 죽자
# `MFDS_EGRESS_HOSTS` 4종이 **동시에** 0건이 됐다 — MFDS RSS·자료실·회수·행정처분·
# GMP실사·법령이 한 프록시에 전부 매달려 있고 폴백 경로가 없었기 때문이다
# (이슈 #983 "자료실 수집기 실패 — 소스 격리", #956 "Intake 운영 경고" 의 공통 원인).
#
# 프록시를 태우는 이유는 MFDS 가 해외 러너 IP 를 거부하기 때문이지만, 그 거부는
# **런 단위로 오락가락한다** — apis.data.go.kr 은 2026-08-24 까지 러너에서 직접 열렸고,
# 지금도 날에 따라 직접 열린다. 즉 "프록시가 죽었다"가 "직결도 죽었다"를 뜻하지 않는데
# 종전 코드는 직결을 **시도조차 하지 않았다**. 그래서 프록시 홉 자체가 실패한 경우에
# 한해 같은 요청을 1회 직결로 재시도한다. 목적은 전면 정지를 부분 성공으로 낮추는 것이고,
# 프록시가 살아 있는 정상 경로의 동작은 바뀌지 않는다.
#
# ★원 서버가 준 4xx/5xx 는 폴백 대상이 **아니다** — 그건 프록시가 정상 동작했다는 뜻이다.
#   폴백은 "프록시에 못 붙었다"에만 건다.
def _is_proxy_hop_failure(err: Exception, proxy: str) -> bool:
    """예외가 원 서버가 아니라 **프록시 홉**의 실패인지 판정."""
    if isinstance(err, requests.exceptions.ProxyError):
        return True
    if not isinstance(err, (requests.exceptions.ConnectTimeout,
                            requests.exceptions.ConnectionError)):
        return False
    # ConnectionError 는 원 서버 실패와 프록시 실패를 같은 타입으로 낸다 — urllib3 메시지에
    # 프록시 **호스트명**이 찍혀 있을 때만 프록시 홉으로 본다. host:port 로 맞추면 안 된다:
    # urllib3 는 `HTTPSConnectionPool(host='h', port=3128)` 처럼 둘을 떼어 찍는다.
    host = urlparse(proxy if "//" in proxy else f"//{proxy}").hostname or ""
    return bool(host) and host in str(err)


# ── [2026-09-14] KR egress 프록시 회로차단기 ──────────────────────────────────
# 홉 실패 뒤에도 요청마다 프록시를 먼저 두드리면 요청당 connect timeout(20~30초)을 그대로
# 먹는다. 실측(dry-run 34803960136): 직결 폴백으로 MFDS 전 소스가 회복됐지만 18분이 걸렸고,
# 같은 날 자료실 갱신 잡은 20분 상한에 걸려 **취소**됐다 — 폴백이 있어도 느리면 없는 것과
# 같다. 그래서 홉 실패를 한 번 보면 쿨다운 동안 프록시를 건너뛰고 곧장 직결로 나간다.
# 쿨다운이 지나면 다시 프록시를 시도하고, 프록시가 응답하면 회로를 닫는다(자연 복귀).
# 프로세스 수명 단위 상태라 워크플로 런마다 초기화된다.
KR_PROXY_CIRCUIT_COOLDOWN_SECONDS = 600
_kr_proxy_tripped_at: float | None = None


def kr_proxy_circuit_trip(reason: str = "") -> None:
    """프록시 홉 실패를 기록 — 쿨다운 동안 `kr_egress_get` 은 프록시를 건너뛴다."""
    global _kr_proxy_tripped_at
    if _kr_proxy_tripped_at is None:
        log("WARN", f"KR egress 프록시 회로 차단 — {KR_PROXY_CIRCUIT_COOLDOWN_SECONDS}초 동안 "
                    f"프록시를 건너뛰고 직결로 나간다{(' (' + reason + ')') if reason else ''}")
    _kr_proxy_tripped_at = time.monotonic()


def kr_proxy_circuit_reset() -> None:
    """회로를 닫는다(프록시 정상 응답 시·테스트 격리용)."""
    global _kr_proxy_tripped_at
    _kr_proxy_tripped_at = None


def kr_proxy_circuit_open(now: float | None = None) -> bool:
    """True = 쿨다운 안이라 프록시를 건너뛴다."""
    if _kr_proxy_tripped_at is None:
        return False
    now = time.monotonic() if now is None else now
    return (now - _kr_proxy_tripped_at) < KR_PROXY_CIRCUIT_COOLDOWN_SECONDS


def kr_egress_get(url: str, **kwargs: Any) -> requests.Response:
    """`requests.get` + KR egress 프록시 홉 실패 시 직결 1회 폴백(+회로차단기).

    KR 호스트가 아니거나 `MFDS_HTTP_PROXY` 가 비어 있으면 `requests.get` 과 동일하다
    (`proxies=None` 을 그대로 넘기므로 종전 호출부의 동작이 보존된다).
    회로가 열려 있으면(최근 홉 실패) 프록시 시도 없이 곧장 직결로 나간다.
    """
    proxies = proxies_for(url)
    if proxies and kr_proxy_circuit_open():
        return requests.get(url, proxies=None, **kwargs)
    try:
        resp = requests.get(url, proxies=proxies, **kwargs)
    except requests.RequestException as e:
        proxy = (proxies or {}).get("https") or (proxies or {}).get("http") or ""
        if not proxy or not _is_proxy_hop_failure(e, proxy):
            raise
        log("WARN", f"KR egress 프록시 홉 실패 — 직결로 1회 폴백 "
                    f"url={mask_service_key(url)} err={mask_service_key(str(e))}")
        kr_proxy_circuit_trip("홉 실패")
        return requests.get(url, proxies=None, **kwargs)
    if proxies:
        kr_proxy_circuit_reset()     # 프록시가 응답했다 = 살아 있다
    return resp


# ── [2026-09-14] KR egress 프록시 **도달** preflight ───────────────────────────
# `MFDS_HTTP_PROXY_CONFIGURED` 는 값이 있느냐만 말한다. 프록시 1대가 죽어 있던 2026-09-08~14
# 동안 이슈 #956 은 "MFDS 5종 실패"로만 보였고, 원인이 프록시인지 원 서버인지 아무도 구분하지
# 못했다. 여기서는 TCP 연결 1회로 **도달 여부**만 잰다 — HTTP 를 태우지 않는 이유는 MFDS 가
# 러너 IP 를 거부하는 날과 프록시가 죽은 날을 섞어 읽지 않기 위해서다(프록시 문제만 가른다).
# 자격증명(userinfo)은 결과 문자열에 절대 싣지 않는다.
KR_EGRESS_PROXY_UNCONFIGURED = "unconfigured"
KR_EGRESS_PROXY_REACHABLE = "reachable"
KR_EGRESS_PROXY_UNREACHABLE = "unreachable"
KR_EGRESS_PROXY_PROBE_TIMEOUT_SECONDS = 10


def kr_egress_proxy_endpoint(proxy: str | None = None) -> tuple[str, int] | None:
    """`MFDS_HTTP_PROXY`(또는 인자)의 (host, port). 미설정·파싱 불가면 None. userinfo 는 버린다."""
    raw = (proxy if proxy is not None else os.environ.get("MFDS_HTTP_PROXY", "")).strip()
    if not raw:
        return None
    parsed = urlparse(raw if "//" in raw else f"//{raw}")
    host = parsed.hostname or ""
    if not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return host, port


def probe_kr_egress_proxy(
    timeout: float = KR_EGRESS_PROXY_PROBE_TIMEOUT_SECONDS,
    *,
    connect: Any = socket.create_connection,
) -> tuple[str, str]:
    """KR egress 프록시 도달 여부 판정 → (status, detail).

    status 는 `KR_EGRESS_PROXY_*` 셋 중 하나. detail 은 host:port 와 소요/오류만 담는다.
    `connect` 는 테스트 주입용(`socket.create_connection` 시그니처).
    """
    endpoint = kr_egress_proxy_endpoint()
    if endpoint is None:
        return KR_EGRESS_PROXY_UNCONFIGURED, "MFDS_HTTP_PROXY 미설정"
    host, port = endpoint
    started = time.monotonic()
    try:
        conn = connect((host, port), timeout)
    except OSError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return (KR_EGRESS_PROXY_UNREACHABLE,
                f"{host}:{port} TCP 연결 실패 ({type(e).__name__}: {e}) {elapsed_ms}ms")
    try:
        conn.close()
    except OSError:
        pass
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return KR_EGRESS_PROXY_REACHABLE, f"{host}:{port} TCP 연결 {elapsed_ms}ms"


def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
    retries: int = 2,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """GET JSON with Retry-After support for 429 and exponential retry for 5xx/network."""

    last_err: Exception | None = None
    req_headers = {**DEFAULT_JSON_HEADERS, **(headers or {})}
    # ★ 보안 — url 자체(및 params 병합 후 urllib3 예외 메시지)에 serviceKey 가 실려 올 수 있다.
    #   로그·예외 문구에 쓰는 사본은 항상 마스킹한 것만 쓴다(원본 url 은 requests.get 에만 전달).
    masked_url = mask_service_key(url)
    for attempt in range(retries + 1):
        try:
            resp = kr_egress_get(
                url,
                params=params,
                timeout=timeout,
                headers=req_headers,
            )
            if resp.status_code == 429:
                if attempt < retries:
                    sleep_s = retry_after_seconds(resp, attempt)
                    log("WARN", f"GET 429 rate-limit url={masked_url} sleep={sleep_s}s attempt={attempt + 1}/{retries + 1}")
                    time.sleep(sleep_s)
                    continue
                raise HTTPClientError(resp.status_code, masked_url, f"HTTP 429 for {masked_url}")
            if 400 <= resp.status_code < 500:
                raise HTTPClientError(resp.status_code, masked_url, f"HTTP {resp.status_code} for {masked_url}")
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as e:
                raise RuntimeError(f"JSON parse failed: {masked_url} - {mask_service_key(str(e))}") from e
        except HTTPClientError:
            raise
        except requests.RequestException as e:
            last_err = e
            log("WARN", f"GET failed ({attempt + 1}/{retries + 1}) url={masked_url} err={mask_service_key(str(e))}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"HTTP GET final failure: {masked_url} ({mask_service_key(str(last_err))})")


def http_get_xml(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 2,
    headers: dict[str, str] | None = None,
) -> ET.Element:
    """GET XML with Retry-After support for 429 and exponential retry for 5xx/network."""

    last_err: Exception | None = None
    req_headers = {**DEFAULT_XML_HEADERS, **(headers or {})}
    # ★ 보안 — url 에 serviceKey 쿼리스트링이 그대로 실려 오는 호출부가 있다(예: MFDS law.go.kr).
    #   로그·예외 문구는 항상 마스킹 사본을 쓴다(원본 url 은 requests.get 에만 전달).
    masked_url = mask_service_key(url)
    for attempt in range(retries + 1):
        try:
            resp = kr_egress_get(
                url,
                timeout=timeout,
                headers=req_headers,
            )
            if resp.status_code == 429:
                if attempt < retries:
                    sleep_s = retry_after_seconds(resp, attempt)
                    log("WARN", f"XML GET 429 rate-limit url={masked_url} sleep={sleep_s}s attempt={attempt + 1}/{retries + 1}")
                    time.sleep(sleep_s)
                    continue
                raise HTTPClientError(resp.status_code, masked_url, f"HTTP 429 for {masked_url}")
            if 400 <= resp.status_code < 500:
                raise HTTPClientError(resp.status_code, masked_url, f"HTTP {resp.status_code} for {masked_url}")
            resp.raise_for_status()
            # 일부 피드(예: WHO Drupal RSS)는 XML 선언 앞에 theme debug 주석/BOM 등 잡음이 붙어
            # "XML or text declaration not at start of entity" 로 파싱 실패한다.
            # XML 시작 토큰(<?xml / <rss / <feed) 이전 바이트를 잘라낸 뒤 파싱한다.
            #
            # ★ 2026-07-27 수리 — 종전 구현은 마커를 **순서대로** 훑으며 `idx > 0` 인 첫 마커에서
            #   잘랐다. 그런데 정상 문서는 `<?xml` 이 **0번 위치**라 `idx > 0` 이 거짓이 되어
            #   그냥 통과하고, 다음 마커 `<rss`(항상 0보다 큼)에서 잘라 **XML 선언을 통째로
            #   버렸다.** 선언이 사라지면 ElementTree 가 UTF-8 을 가정하므로, 선언이
            #   `encoding="windows-1252"` 처럼 비-UTF8 이고 본문에 비-ASCII 바이트가 있으면
            #   그 바이트에서 파싱이 죽는다(2026-07-27 ECA 실장애: `0x96`=en-dash 에서
            #   `not well-formed line 29 col 79` → 그 주 ECA 수집 0건, 7건 유실 직전).
            #   나머지 피드가 전부 UTF-8 이라 우연히 안 걸렸을 뿐 **소스 무관 공통 결함**이었다.
            #
            #   수리 = "가장 먼저 나오는 마커 위치"로 자른다. 그 위치가 0이면 잡음이 없다는
            #   뜻이므로 **아무것도 자르지 않는다**(선언 보존). 잡음이 있을 때만 그 앞을 버린다.
            content = resp.content
            starts = [i for i in (content.find(m) for m in (b"<?xml", b"<rss", b"<feed")) if i >= 0]
            if starts:
                idx = min(starts)
                if idx > 0:
                    content = content[idx:]
            else:
                content = content.lstrip()
            try:
                return ET.fromstring(content)
            except ET.ParseError as e:
                raise RuntimeError(f"XML parse failed: {masked_url} - {mask_service_key(str(e))}") from e
        except HTTPClientError:
            raise
        except requests.RequestException as e:
            last_err = e
            log("WARN", f"XML GET failed ({attempt + 1}/{retries + 1}) url={masked_url} err={mask_service_key(str(e))}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"HTTP XML GET final failure: {masked_url} ({mask_service_key(str(last_err))})")


# ── data.go.kr 공통 유틸리티 ──────────────────────────────────────────────────


def parse_int_safe(value: Any, default: int = 0) -> int:
    """Safely parse an integer value, returning *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def text_field(raw: dict[str, Any], key: str) -> str:
    """Extract a stripped string field from a dict, defaulting to ``""``."""
    return str(raw.get(key) or "").strip()


def parse_datago_date(raw: str) -> str:
    """Parse ``YYYYMMDD`` date strings used by data.go.kr APIs → ISO format."""
    raw = (raw or "").strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        y, m, d = raw[:4], raw[4:6], raw[6:8]
        try:
            return date(int(y), int(m), int(d)).isoformat()
        except ValueError:
            return ""
    return ""


def datago_normalize_items(raw_items: Any) -> list[dict[str, Any]]:
    """Normalize data.go.kr's ``item`` wrapper across list/dict shapes."""
    if raw_items is None:
        return []
    if isinstance(raw_items, list):
        out: list[dict[str, Any]] = []
        for item in raw_items:
            out.extend(datago_normalize_items(item))
        return out
    if isinstance(raw_items, dict):
        if "item" in raw_items:
            return datago_normalize_items(raw_items.get("item"))
        return [raw_items]
    return []


def datago_extract_items(
    data: dict[str, Any], default_page_size: int = 100,
) -> tuple[list[dict[str, Any]], int, int, int, str]:
    """Extract items and pagination from a data.go.kr JSON response."""
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    result_code = str(header.get("resultCode") or "").strip()
    result_msg = str(header.get("resultMsg") or "").strip()
    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    page_no = parse_int_safe(body.get("pageNo"), 1)
    num_rows = parse_int_safe(body.get("numOfRows"), default_page_size)
    total_count = parse_int_safe(body.get("totalCount"), 0)
    items = datago_normalize_items(body.get("items"))
    return items, page_no, num_rows, total_count, f"{result_code}:{result_msg}"


def mask_service_key(url: str) -> str:
    """data.go.kr/law.go.kr URL 의 serviceKey 값을 REDACTED 로 마스킹.

    5개 수집기의 동일 구현을 단일화 — provenance(item.api_query)에 실 키가 새지 않게 한다.
    """
    return re.sub(r"([?&]serviceKey=)[^&]+", r"\1***REDACTED***", url)


class DatagoPageError(RuntimeError):
    """data.go.kr 페이지 요청 실패 — page_no·원인 첨부(수집기가 부분/치명 판정)."""

    def __init__(self, page_no: int, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.page_no = page_no
        self.cause = cause


class _DatagoPaginator:
    """data.go.kr serviceKey JSON 엔드포인트 페이지네이션 이터레이터(4개 수집기 공용 골격).

    각 페이지 ``(raw_items, masked_url)`` 를 yield. 현행 수집기 루프 의미 보존:
      · params = ``{serviceKey, pageNo, numOfRows, type:json, **extra_params}`` (원 순서 동일)
      · masked_url = ``mask_service_key(endpoint?urlencode(params))`` — item provenance 바이트 동일
      · ``http_get(endpoint, params=, timeout=, retries=)`` → ``extract(data)`` 5-튜플
        ``(raw_items, response_page, num_rows, total_count, status)``
      · status ``'00:'`` 로 시작하지 않으면 페이지 실패
      · 빈 페이지 또는 ``response_page*num_rows >= total_count`` 시 종료
      · ``pageNo > max_pages`` 소진 시 ``.truncated = True``
    페이지 실패는 ``DatagoPageError(page_no, cause)`` raise — 수집기가 items 유무로 부분(WARN)/
    치명(error) 판정한다(소스별 실패·truncated 문구·health 의미는 수집기 소유 → 로깅은 수집기가
    담당; 제너릭 on_warn 미도입). ``extract``·``http_get`` 은 수집기 네임스페이스의 것을 주입받아
    기존 단위테스트의 monkeypatch 호환을 유지한다.
    """

    def __init__(self, endpoint: str, *, service_key: str, extract, http_get,
                 max_pages: int, page_size: int = 100,
                 extra_params: dict[str, Any] | None = None,
                 timeout: int = 30, retries: int = 2) -> None:
        self.endpoint = endpoint
        self.service_key = service_key
        self.extract = extract
        self.http_get = http_get
        self.max_pages = max_pages
        self.page_size = page_size
        self.extra_params = extra_params or {}
        self.timeout = timeout
        self.retries = retries
        self.truncated = False
        self.total_count = 0

    def __iter__(self):
        page_no = 1
        while page_no <= self.max_pages:
            params = {
                "serviceKey": self.service_key,
                "pageNo": page_no,
                "numOfRows": self.page_size,
                "type": "json",
            }
            params.update(self.extra_params)
            masked_url = mask_service_key(self.endpoint + "?" + urlencode(params))
            try:
                data = self.http_get(self.endpoint, params=params,
                                     timeout=self.timeout, retries=self.retries)
                raw_items, response_page, num_rows, total_count, status = self.extract(data)
                if not status.startswith("00:"):
                    raise RuntimeError(f"API status {status}")
            except Exception as e:  # noqa: BLE001
                raise DatagoPageError(page_no, e) from e
            self.total_count = total_count
            if not raw_items:
                return
            yield raw_items, masked_url
            if total_count and response_page * num_rows >= total_count:
                return
            page_no += 1
        self.truncated = True


def datago_paginate(endpoint: str, *, service_key: str, extract, http_get,
                    max_pages: int, page_size: int = 100,
                    extra_params: dict[str, Any] | None = None,
                    timeout: int = 30, retries: int = 2) -> _DatagoPaginator:
    """``_DatagoPaginator`` 팩토리 — 반복 후 ``.truncated``·``.total_count`` 조회. 상세는 클래스 docstring."""
    return _DatagoPaginator(
        endpoint, service_key=service_key, extract=extract, http_get=http_get,
        max_pages=max_pages, page_size=page_size, extra_params=extra_params,
        timeout=timeout, retries=retries)


# ── HTML/bytes GET with retry ─────────────────────────────────────────────────


def http_get_html(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 3,
    headers: dict[str, str] | None = None,
    label: str = "",
) -> str:
    """GET HTML with 429 Retry-After and exponential retry."""
    tag = label or "HTML"
    req_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        **(headers or {}),
    }
    last_err: Exception | None = None
    # ★ 보안 — 일부 호출부(라이브러리 소스 등)는 url 에 API 키가 실릴 수 있다. 로그·예외
    #   문구는 항상 마스킹 사본을 쓴다(원본 url 은 requests.get 에만 전달).
    masked_url = mask_service_key(url)
    for attempt in range(retries + 1):
        try:
            resp = kr_egress_get(
                url,
                timeout=timeout,
                headers=req_headers,
            )
            if resp.status_code == 429 and attempt < retries:
                sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                log("WARN", f"{tag} 429 url={masked_url} sleep={sleep_s}s")
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return resp.text or ""
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                log("WARN", f"{tag} GET retry {attempt + 1}/{retries + 1} url={masked_url} err={mask_service_key(str(e))}")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP GET final failure: {masked_url} ({mask_service_key(str(last_err))})") from e
    raise RuntimeError(f"HTTP GET final failure: {masked_url} ({mask_service_key(str(last_err))})")


def http_get_bytes(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 3,
    headers: dict[str, str] | None = None,
    label: str = "",
) -> bytes:
    """GET raw bytes with 429 Retry-After and exponential retry."""
    tag = label or "BYTES"
    req_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
        **(headers or {}),
    }
    last_err: Exception | None = None
    # ★ 보안 — 일부 호출부(라이브러리 소스 등)는 url 에 API 키가 실릴 수 있다. 로그·예외
    #   문구는 항상 마스킹 사본을 쓴다(원본 url 은 requests.get 에만 전달).
    masked_url = mask_service_key(url)
    for attempt in range(retries + 1):
        try:
            resp = kr_egress_get(
                url,
                timeout=timeout,
                headers=req_headers,
            )
            if resp.status_code == 429 and attempt < retries:
                sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                log("WARN", f"{tag} 429 url={masked_url} sleep={sleep_s}s")
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return resp.content or b""
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                log("WARN", f"{tag} GET retry {attempt + 1}/{retries + 1} url={masked_url} err={mask_service_key(str(e))}")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP GET final failure: {masked_url} ({mask_service_key(str(last_err))})") from e
    raise RuntimeError(f"HTTP GET final failure: {masked_url} ({mask_service_key(str(last_err))})")


# ── [배치5 Phase0] collect_intake 에서 relocate: 소스 식별 상수 + 공용 텍스트/환경 헬퍼 ──
SOURCE_FR = "Federal Register"
SOURCE_RECALL = "OpenFDA Recall"
SOURCE_EMA = "EMA"
SOURCE_MHRA = "MHRA Inspectorate"
SOURCE_PICS = "PIC/S"
SOURCE_ECA = "ECA Academy"
SOURCE_FDA_WL = "FDA Warning Letter"
SOURCE_MFDS = "MFDS"
SOURCE_ICH = "ICH"
SOURCE_WHO = "WHO"
SOURCE_HC = "Health Canada"
SOURCE_FDA_483 = "FDA 483"   # WHY-1 #3 — OII FOIA Reading Room 483 Observation (가장 깊은 결함 원본)
SOURCE_HANDOFF = "GRM Handoff"
SOURCE_BRAVE = "Brave Search"
SOURCE_RAPS  = "RAPS"
SOURCE_EPR   = "European Pharma Review"   # European Pharmaceutical Review
SOURCE_ISPE  = "ISPE"   # [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak 블로그 RSS
SOURCE_EU_GMP_NCR = "EU GMP NCR (EudraGMDP)"   # EU/EEA 업체별 GMP 비준수 보고서(EudraGMDP)
SOURCE_MHRA_GMP_NCR = "MHRA GMP NCR"   # 영국 MHRA 업체별 GMP 비준수 성명서(GMDP 등록부)
NOTION_RICH_TEXT_CHUNK = 1900  # 2000 한도, 여유 100


# ── [배치6 Phase2] 수집 소스 레지스트리 — 소스당 1 레코드 ────────────────────────
# card_scaffold.py 의 SourceSpec(_REGISTRY, 발행측)과 대칭인 "수집측" 레지스트리.
# CollectionStats 스칼라 필드는 유지(사용자 결정)하고, 이 레지스트리가 getattr/setattr
# 로 ② main insert 루프(collect_intake)와 ③ health rows(grm_health)를 구동한다.
# ``prefix`` = CollectionStats 필드 프리픽스 = health row "key" (전 소스 동일).
# 순서는 insert 순서(=existing dedup 누적 순서)·health rows 리스트 순서와 byte 일치해야 함.
#
# 새 수집 소스 추가 절차(4A card_scaffold._REGISTRY 절차와 대칭):
#   1) IntakeSourceSpec 1건을 아래 INTAKE_SOURCE_SPECS 에 (원하는 insert/health 순서로) 추가
#   2) collect_intake.CollectionStats 에 {prefix}_fetched/_inserted/_skipped_dup/
#      _insert_failed/_error/_error_msg 6필드 추가(스칼라 유지 결정의 잔여 — item④)
#   3) collect_intake main 에 수집 블록 1개(collect 호출 → stats.{prefix}_fetched·error) +
#      _insert_items_map 에 {prefix}: items 1항 추가
#   → ② insert / ③ health row / (해당 시) 골든 3종만 갱신. coverage 라벨은 별개
#     (grm_handoff.COVERAGE_SOURCE_LABELS, SOURCE_* 키), transient 적격은 grm_health.
@dataclass(frozen=True)
class IntakeSourceSpec:
    prefix: str          # CollectionStats 필드 프리픽스 & health row key
    health_label: str    # _source_health_rows 의 "label"
    has_truncated: bool = False   # fr/recall 만 health row 에 "truncated" 노출
    # ★[소스 오류 보고 레지스트리화 2026-08-12] 아래 두 필드는 grm_health 의 소스별 오류
    # 보고(`enabled_source_failures`)가 쓴다. 종전엔 그 리스트가 **손으로 적은 17줄**이었고,
    # 레지스트리가 23종으로 늘어도 아무도 갱신하지 않아 **발행 중인 EU/영국 GMP NCR·ISPE·
    # MHRA Alert 가 오류 보고 경로에 아예 없었다**(수집기가 죽어도 무음 — 2026-07-27 ECA
    # 7일 침묵과 같은 계열, 그때도 같은 리스트가 원인이었다). 이제 이 레지스트리 하나만
    # 고치면 오류 보고까지 따라온다.
    health_code_override: str = ""   # 비우면 prefix 의 `_`→`-`. 관례를 벗어난 3종만 지정
    warn_only: bool = False          # True = 오류를 경고로만(run 을 적색으로 만들지 않음)
    # ★[무음 감시 2026-09-14] 아래 두 필드는 `source_silence` 가 쓴다. 종전 health 는
    # **오류를 낸 소스만** 보고했다 — 피드가 200 을 주면서 빈 응답을 돌려주거나, 스키마가
    # 바뀌어 파서가 0건을 뽑거나, 소스가 그냥 갱신을 멈추면 `*_error` 가 False 라 아무
    # 경보도 안 났다. 그래서 PIC/S(46일)·MHRA(25/35일)·EU GMP NCR(20일)·WHO(12/16일)·
    # Health Canada(13일)·ICH(60일+)가 **경보 0건으로** 멈춰 있었고, 주간 브리프는 그걸
    # "한산한 주"처럼 0 으로 찍어 발행했다(2026-09-14 발견).
    #
    # `silence_days` 는 "이 정도면 확실히 이상하다" 선이지 "가장 빨리 잡는" 선이 아니다.
    # 소스가 실제로 얼마나 자주 내는지에 맞춰 4단으로 둔다 — 주 1회 이상 내는 소스 10일 ·
    # 월 1회꼴 21일 · 산발 35일 · 분기 1회꼴 60~90일. 스냅샷 diff 소스(ICH)는 0 = 제외.
    # 더 촘촘히 깎으면 한산한 주에 가짜 경고가 나고, 그러면 아무도 안 읽는다.
    #
    # ★[임계 교정 2026-09-21] 임계는 **러너 프로브로 "원천이 건강한데 조용하다"가 확인된
    #   무음 일수보다 반드시 길어야 한다.** 초판(2026-09-14)의 3단은 그 확인 **전에** 정한
    #   값이었고, 같은 날 러너 프로브(`grm-source-probe.yml targets=silent`, run
    #   34797482678)가 PIC/S·MHRA GMP NCR·EU GMP NCR **3종 전부 OPEN** 으로 판정했다:
    #   PIC/S 는 200·항목 109건·최신 pubDate 07-30 으로, Notion 마지막 행(07-30)과 정확히
    #   일치 = **있는 건 다 긁어온 상태**였다. 그런데 임계가 각각 35·35·21일이라 건강한
    #   소스가 09-15~09-17 부터 매일 경고를 냈고, 09-21 에는 이슈 #956 이 🚨 7일 연속
    #   에스컬레이션까지 갔다 — 고칠 게 없는 경고가, 같은 이슈에 올라오는 **진짜 고장**
    #   (KR egress 프록시 사망)을 묻는 상태다. 그래서 확인된 건강 무음(PIC/S 46일 ·
    #   MHRA GMP NCR 35일 · EU GMP NCR 20일)보다 길게 다시 잡는다.
    #   ⚠️ 대가를 숨기지 않는다: 임계를 늘리면 이 3종이 **진짜로** 죽었을 때 표면화가
    #   그만큼 늦는다. 그 구간은 ① `*_error` 기반 보고(하드 실패는 즉시 잡는다)와
    #   ② 러너 프로브 수동 1회전이 메운다 — 무음 감시는 원래 마지막 그물이지 첫 그물이
    #   아니다. 임계를 다시 줄이려면 먼저 프로브로 원천 주기를 재라.
    notion_source: str = ""   # Notion Intake DB 의 `Source` select 값. 비우면 무음 감시 제외
    silence_days: int = 0     # 이 일수를 **초과**해 신규 0건이면 경고. 0 = 감시 안 함

    @property
    def health_code(self) -> str:
        """health finding 코드(`source-error:{code}` 등). 기본은 prefix 의 `_`→`-`."""
        return self.health_code_override or self.prefix.replace("_", "-")


INTAKE_SOURCE_SPECS: tuple[IntakeSourceSpec, ...] = (
    # warn_only 판정 기준(2026-07-27 확립, 2026-08-12 확장): `add_failure` 는 exit 1 →
    # intake run 적색인데, `grm-web-publish.yml` 이 스캐폴드를 `--status success` 로 고른다.
    # 즉 **부차 피드 하나가 죽으면 월요일 발행이 통째로 막힌다**. 목적은 차단이 아니라
    # 표면화이므로, "이 소스가 죽어도 그 주 발행은 나가야 한다"면 warn_only=True 다.
    # fr/recall 은 둘 다 죽을 때만 `phase1-all-failed`(failure)로 남겨 두고, 단독 실패는
    # 여기서 경고로 표면화한다(종전엔 단독 실패가 완전 무음이었다).
    IntakeSourceSpec("fr", "Federal Register", has_truncated=True, warn_only=True,
                     notion_source=SOURCE_FR, silence_days=10),
    IntakeSourceSpec("recall", "OpenFDA Recall", has_truncated=True, warn_only=True,
                     notion_source=SOURCE_RECALL, silence_days=10),
    IntakeSourceSpec("ema", "EMA RSS", warn_only=True,
                     notion_source=SOURCE_EMA, silence_days=10),
    IntakeSourceSpec("mhra", "MHRA RSS", warn_only=True,
                     notion_source=SOURCE_MHRA, silence_days=35),
    IntakeSourceSpec("mhra_alert", "MHRA Drug/Device Alerts", warn_only=True,
                     notion_source=SOURCE_MHRA, silence_days=35),
    # PIC/S 90일: 러너 프로브 2026-09-14 기준 46일 무음이 **정상**(피드 109건·최신 07-30).
    IntakeSourceSpec("pics", "PIC/S RSS", warn_only=True,
                     notion_source=SOURCE_PICS, silence_days=90),
    IntakeSourceSpec("eca", "ECA Academy RSS", warn_only=True,
                     notion_source=SOURCE_ECA, silence_days=10),
    IntakeSourceSpec("wl", "FDA Warning Letters", warn_only=True,
                     notion_source=SOURCE_FDA_WL, silence_days=10),
    IntakeSourceSpec("mfds", "MFDS RSS", health_code_override="mfds-rss",
                     notion_source=SOURCE_MFDS, silence_days=10),
    IntakeSourceSpec("mfds_law", "MFDS Law/Admrul"),
    IntakeSourceSpec("mfds_recall", "MFDS Recall"),
    IntakeSourceSpec("mfds_admin", "MFDS Admin"),
    IntakeSourceSpec("mfds_gmp_cert", "MFDS GMP Certificate"),
    IntakeSourceSpec("mfds_safety_letter", "MFDS Safety Letter"),
    IntakeSourceSpec("mfds_gmp_inspection", "MFDS GMP Inspection"),
    # ★ICH 는 무음 감시 **제외**(silence_days=0). ICH 수집기는 페이지 섹션 제목의 스냅샷 diff 라
    #   (dedup 창 1095일) 페이지가 안 바뀌면 몇 달이고 신규 0건이 **설계상 정상**이다 — 여기에
    #   임계를 두면 상시 경고가 되어 진짜 무음을 묻는다(2026-09-14 실측: 60일+ 0건이 곧 그 상태).
    #   페이지 자체가 죽거나 파서가 깨지면 `ich_error`(핵심 페이지 섹션 0건 = error)가 잡는다.
    IntakeSourceSpec("ich", "ICH", notion_source=SOURCE_ICH, silence_days=0),
    IntakeSourceSpec("who", "WHO", notion_source=SOURCE_WHO, silence_days=10),
    IntakeSourceSpec("hc", "Health Canada", health_code_override="health-canada",
                     notion_source=SOURCE_HC, silence_days=10),
    IntakeSourceSpec("fda483", "FDA 483", notion_source=SOURCE_FDA_483, silence_days=10),
    # 전문지·NCR 3종은 주당 카드가 한 자릿수라 죽어도 발행을 막을 이유가 없다 → 경고.
    IntakeSourceSpec("ispe", "ISPE iSpeak RSS", warn_only=True,
                     notion_source=SOURCE_ISPE, silence_days=21),
    # ★[2026-08-12] Brave Search 는 23종 중 **유일하게** warn_only 도 transient 강등 자격도
    # 없어, 오류 한 번에 exit 1 = 그 주 발행 스캐폴드 배제였다. 요율 제한이 일상인 3rd-party
    # 보조 검색이고 카드 생성의 필수 경로도 아니다 — 죽어도 그 주 발행은 나가야 한다.
    # (`ENABLE_SEARCH` 기본 false 라 피해는 0 이었지만, 코드 리뷰를 안 거치는 repo 변수
    #  하나가 뒤집히면 터지는 지뢰였다.) transient 강등이 아니라 warn_only 로 푸는 이유:
    # 그쪽은 `_is_transient_source_error` 의 화이트리스트 의미를 바꾸는데, 그 경계는 이미
    # 테스트가 못박아 둔 계약이다(ECA·WL 선례와 같은 기구를 쓴다).
    IntakeSourceSpec("search", "Brave Search", health_code_override="brave-search",
                     warn_only=True),
    # EU GMP NCR 60일: 러너 프로브 2026-09-14 기준 20일 무음이 **정상**(EudraGMDP 폼 표식 확인).
    IntakeSourceSpec("eu_gmp_ncr", "EU GMP NCR (EudraGMDP)", warn_only=True,
                     notion_source=SOURCE_EU_GMP_NCR, silence_days=60),
    # MHRA GMP NCR 90일: 러너 프로브 2026-09-14 기준 35일 무음이 **정상**(Drupal 목록 표식 확인).
    IntakeSourceSpec("mhra_gmp_ncr", "MHRA GMP NCR", warn_only=True,
                     notion_source=SOURCE_MHRA_GMP_NCR, silence_days=90),
)


def truncate(text: str, limit: int = NOTION_RICH_TEXT_CHUNK) -> str:
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def chunk_text(text: str, size: int = NOTION_RICH_TEXT_CHUNK) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


def _env_int(name: str, default: int) -> int:
    """환경변수를 정수로 안전 파싱. 비정상 값이면 WARN 후 default 사용 (graceful degradation)."""
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        log("WARN", f"{name}={raw!r} 정수 파싱 실패 — default {default} 사용")
        return default
