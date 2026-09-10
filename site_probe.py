#!/usr/bin/env python3
"""GRM 사이트 합성 점검(synthetic probe) — 방문자가 실제로 받는 것을 매일 잰다.

## 왜

이 저장소의 워크플로 43개는 전부 **저장소 자신**(골든·워치독·감사)을 검사한다.
`https://grm-solutions.com`(Cloudflare Pages 정적 사이트 + 브라우저에서 anon 키로
부르는 Supabase PostgREST RPC)에서 방문자가 실제로 무엇을 받는지는 아무것도 재지
않는다. 2026-09-02 감사가 이 축을 지적했고, 2026-09-06에는 "관련 사례" RPC
(`findings_similar_to`)가 며칠째 호출의 56%에서 타임아웃 나고 있었는데 화면은 그걸
그냥 "결과 없음"으로 삼켰다 — 저장소 안 어떤 가드도 이걸 볼 수 없었다.

## 무엇을 재는가

1. 정적 페이지 5종(`/`·`/en/`·`/findings/`·`/rss.xml`·`/sitemap.xml`)이 200 을
   주고 최소한의 본문 계약(`<html lang>`·`<loc>` 브리프 항목)을 지키는가.
2. 신선도 — KST 기준 가장 최근에 발행됐어야 할 월요일 브리프(`/briefs/{date}/`)가
   실제로 떠 있는가. 영문판은 지연될 수 있으므로 별도로 warn 만 한다.
3. RPC 지연 — anon 키로 실제 브라우저가 부르는 것과 같은 호출 형태로
   `fda_inspection_stats`·`findings_stats`·`findings_similar_to` 를 불러 2초 초과는
   warn, 3초 초과(anon `statement_timeout`)는 fail 로 본다.
4. 보안 헤더 존재 여부(`strict-transport-security`·`x-frame-options`) — 별도 PR 이
   `_headers` 로 추가할 예정이라 여기서는 부재를 fail 이 아니라 warn 으로만 남긴다.

각 점검은 ok|warn|fail 과 경과 초, 한 줄 상세를 기록한다. warn 은 전체를 fail 로
만들지 않는다 — exit 1 은 fail 이 하나라도 있을 때만.

## 고정 finding_id 선택 근거

`findings_similar_to` 지연 점검에는 실재하는 공개 finding_id 가 필요하다.
`web/data/findings_docs.json` 은 `findings_docs_refresh.py` 가 **anon 키**로
`findings_search` RPC 를 불러 만든 파일이고, 그 RPC 가 기대는 공개 게이트
(006/010 마이그레이션 — RLS 정책에 `scope_status = 'ok'` 가 AND 로 걸려 있다)를 통과한
행만 준다. 즉 이 파일에 실린 모든 finding_id 는 **이미** scope_status='ok' 다(파일
스키마 자체에 별도 scope_status 필드가 없는 이유이기도 하다 — anon RLS 가 그 필터를
이미 대신했으므로). 그래서 이 파일의 `documents[0].findings[0]` 을 그대로 골랐다:
document_id=`009ffad3df01`(FDA 경고서한, Lex Inc.), finding_id 는 아래 상수.

## 환경변수

  SITE_BASE_URL       기본 https://grm-solutions.com (끝 슬래시 없이)
  SUPABASE_URL        PostgREST 베이스(`vars.SUPABASE_URL`)
  SUPABASE_ANON_KEY   공개·RLS 로 보호되는 anon 키(`vars.SUPABASE_ANON_KEY`, 사이트도
                      브라우저 번들에 그대로 심는다 — 시크릿이 아니다)
  PROBE_TODAY         선택. ISO 날짜(YYYY-MM-DD) — 테스트/재현용으로 "오늘(KST)" 을
                      덮어쓴다.

CLI: `python site_probe.py [--output probe_report.json] [--timeout 15]`
종료코드: 0 = fail 없음(warn 은 무관) · 1 = fail 1건 이상.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests

from grm_cli import header_ci, normalize_supabase_url

SCHEMA_VERSION = "grm-site-probe/v1"
DEFAULT_SITE_BASE_URL = "https://grm-solutions.com"
DEFAULT_TIMEOUT = 15.0

KST = dt.timezone(dt.timedelta(hours=9))

# anon statement_timeout 이 3초다(웹 RPC 마이그레이션 이력 — 079 findings_similar_perf 등).
# 2초를 넘으면 그 한도에 근접했다는 조기 경보, 3초를 넘으면 실제로 잘리는 지연이다.
RPC_WARN_S = 2.0
RPC_FAIL_S = 3.0

# web/data/findings_docs.json 의 documents[0].findings[0] — 위 "고정 finding_id 선택
# 근거" 참조. document_id=009ffad3df01, agency=FDA(경고서한), firm=Lex Inc.
FIXED_FINDING_ID = "finding-3234e2d59f2c100dedb30684"


@dataclass
class CheckResult:
    name: str
    status: str  # ok | warn | fail
    elapsed_s: float
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "elapsed_s": round(self.elapsed_s, 3),
            "detail": self.detail,
        }


def most_recent_published_monday(today: dt.date) -> dt.date:
    """KST 기준 오늘로부터, 이미 발행됐어야 할 가장 최근 월요일 브리프 날짜.

    월요일 브리프는 오후에 뜨므로 오늘이 월요일이면(이른 시각일 수 있어) 이번 주는
    아직 기준으로 삼지 않고 지난주 월요일을 본다. 그 외 요일은 이번 주 월요일이
    이미 최소 하루 전이므로 그대로 쓴다.
    """
    weekday = today.weekday()  # Mon=0 .. Sun=6
    if weekday == 0:
        return today - dt.timedelta(days=7)
    return today - dt.timedelta(days=weekday)


def resolve_today(env: dict[str, str] | None = None) -> dt.date:
    env = os.environ if env is None else env
    override = (env.get("PROBE_TODAY") or "").strip()
    if override:
        return dt.date.fromisoformat(override)
    return dt.datetime.now(KST).date()


def _get(url: str, timeout: float):
    try:
        return requests.get(url, timeout=timeout), None
    except requests.exceptions.RequestException as exc:  # pragma: no cover - network edge
        return None, exc


def _post(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float):
    try:
        return requests.post(url, headers=headers, json=payload, timeout=timeout), None
    except requests.exceptions.RequestException as exc:  # pragma: no cover - network edge
        return None, exc


def _resp_elapsed(resp: Any) -> float:
    elapsed = getattr(resp, "elapsed", None)
    if elapsed is None:
        return 0.0
    try:
        return elapsed.total_seconds()
    except AttributeError:  # pragma: no cover - defensive
        return 0.0


def _page_check(name: str, url: str, timeout: float, *, body_contains: str | None = None
                ) -> tuple[CheckResult, Any]:
    resp, err = _get(url, timeout)
    if err is not None:
        return CheckResult(name, "fail", 0.0, f"요청 실패: {err}"), None
    elapsed = _resp_elapsed(resp)
    if resp.status_code != 200:
        return CheckResult(name, "fail", elapsed, f"HTTP {resp.status_code}"), resp
    if body_contains is not None and body_contains not in resp.text:
        return CheckResult(name, "fail", elapsed, f"본문에 {body_contains!r} 없음"), resp
    detail = "HTTP 200" + (f", {body_contains!r} 확인" if body_contains else "")
    return CheckResult(name, "ok", elapsed, detail), resp


def _downgrade_fail_to_warn(result: CheckResult, reason: str) -> CheckResult:
    if result.status != "fail":
        return result
    return CheckResult(result.name, "warn", result.elapsed_s, f"{result.detail} ({reason})")


def check_header_present(name: str, resp: Any, header_name: str) -> CheckResult:
    if resp is None:
        return CheckResult(name, "warn", 0.0, "GET / 실패로 헤더를 확인할 수 없음")
    value = header_ci(dict(resp.headers), header_name)
    if value:
        return CheckResult(name, "ok", 0.0, f"{header_name}: {value}")
    return CheckResult(name, "warn", 0.0, f"{header_name} 헤더 없음(_headers 배선 별도 PR 예정)")


# [2026-09-10 재시도] 첫 실측에서 findings_stats 가 콜드 상태에서 한 번 500, findings_similar_to 가
# 3.58s 로 잡혔다가 30초 뒤엔 정상이었다. 하루 1회 프로브가 그런 블립으로 이슈를 열면 경보가
# 잡음이 되므로, RPC 검사가 fail 이면 RETRY_SLEEP_S 뒤 **한 번만** 다시 재고 두 번째 결과를
# 채택한다(단 상세에 1차 결과를 남겨 콜드 지연이 반복되는지 추적할 수 있게 한다).
# 두 번 연속 fail 이어야 fail 이다. 테스트는 RETRY_SLEEP_S 를 0 으로 패치한다.
RETRY_SLEEP_S = 15.0


def check_rpc(name: str, base_url_norm: str | None, anon_key: str, rpc_name: str,
              payload: dict[str, Any], timeout: float, *,
              warn_s: float = RPC_WARN_S, fail_s: float = RPC_FAIL_S) -> CheckResult:
    first = _check_rpc_once(name, base_url_norm, anon_key, rpc_name, payload, timeout,
                            warn_s=warn_s, fail_s=fail_s)
    if first.status != "fail" or base_url_norm is None or not anon_key:
        return first
    time.sleep(RETRY_SLEEP_S)
    second = _check_rpc_once(name, base_url_norm, anon_key, rpc_name, payload, timeout,
                             warn_s=warn_s, fail_s=fail_s)
    if second.status != "fail":
        return CheckResult(name, second.status, second.elapsed_s,
                           f"재시도 통과({second.detail}) · 1차 {first.detail}")
    return CheckResult(name, "fail", second.elapsed_s,
                       f"{first.detail} · 재시도도 {second.detail}")


def _check_rpc_once(name: str, base_url_norm: str | None, anon_key: str, rpc_name: str,
                    payload: dict[str, Any], timeout: float, *,
                    warn_s: float = RPC_WARN_S, fail_s: float = RPC_FAIL_S) -> CheckResult:
    if base_url_norm is None:
        return CheckResult(name, "fail", 0.0, "SUPABASE_URL 미설정 또는 https:// 형식 아님")
    if not anon_key:
        return CheckResult(name, "fail", 0.0, "SUPABASE_ANON_KEY 미설정")
    url = f"{base_url_norm}/rest/v1/rpc/{rpc_name}"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }
    resp, err = _post(url, headers, payload, timeout)
    if err is not None:
        return CheckResult(name, "fail", 0.0, f"요청 실패: {err}")
    elapsed = _resp_elapsed(resp)
    if resp.status_code != 200:
        return CheckResult(name, "fail", elapsed, f"HTTP {resp.status_code}")
    if elapsed > fail_s:
        return CheckResult(name, "fail", elapsed,
                           f"{elapsed:.2f}s > {fail_s:.1f}s(anon statement_timeout)")
    if elapsed > warn_s:
        return CheckResult(name, "warn", elapsed, f"{elapsed:.2f}s > {warn_s:.1f}s")
    return CheckResult(name, "ok", elapsed, f"HTTP 200, {elapsed:.2f}s")


def _overall_status(checks: list[CheckResult]) -> str:
    if any(c.status == "fail" for c in checks):
        return "fail"
    if any(c.status == "warn" for c in checks):
        return "warn"
    return "ok"


def run_probe(*, base_url: str, supabase_url: str, anon_key: str, today: dt.date,
              finding_id: str = FIXED_FINDING_ID,
              timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    base_url = (base_url or DEFAULT_SITE_BASE_URL).rstrip("/")
    checks: list[CheckResult] = []

    # 1) 정적 페이지 + 홈 응답을 재사용하는 보안 헤더 점검(같은 GET 을 두 번 쏘지 않는다)
    home_check, home_resp = _page_check(
        "GET / (ko)", f"{base_url}/", timeout, body_contains='<html lang="ko"')
    checks.append(home_check)
    checks.append(check_header_present(
        "헤더 strict-transport-security", home_resp, "strict-transport-security"))
    checks.append(check_header_present(
        "헤더 x-frame-options", home_resp, "x-frame-options"))

    en_check, _ = _page_check(
        "GET /en/ (en)", f"{base_url}/en/", timeout, body_contains='<html lang="en"')
    checks.append(en_check)

    findings_check, _ = _page_check("GET /findings/", f"{base_url}/findings/", timeout)
    checks.append(findings_check)

    rss_check, _ = _page_check("GET /rss.xml", f"{base_url}/rss.xml", timeout)
    checks.append(rss_check)

    sitemap_check, _ = _page_check(
        "GET /sitemap.xml", f"{base_url}/sitemap.xml", timeout,
        body_contains=f"<loc>{base_url}/briefs/")
    checks.append(sitemap_check)

    # 2) 신선도 — 가장 최근에 발행됐어야 할 월요일 브리프
    monday = most_recent_published_monday(today)
    date_str = monday.isoformat()

    brief_check, _ = _page_check(
        f"최신 브리프 GET /briefs/{date_str}/", f"{base_url}/briefs/{date_str}/", timeout)
    checks.append(brief_check)

    brief_en_check, _ = _page_check(
        f"최신 브리프(EN) GET /en/briefs/{date_str}/",
        f"{base_url}/en/briefs/{date_str}/", timeout)
    checks.append(_downgrade_fail_to_warn(brief_en_check, "영문판은 지연될 수 있음"))

    # 3) RPC 지연(anon 키, 브라우저와 같은 호출 형태)
    base_norm = normalize_supabase_url(supabase_url) if supabase_url else None
    checks.append(check_rpc(
        "RPC fda_inspection_stats", base_norm, anon_key, "fda_inspection_stats", {}, timeout))
    checks.append(check_rpc(
        "RPC findings_stats", base_norm, anon_key, "findings_stats", {}, timeout))
    checks.append(check_rpc(
        "RPC findings_similar_to", base_norm, anon_key, "findings_similar_to",
        {"p_finding_id": finding_id, "p_limit": 5}, timeout))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(KST).isoformat(),
        "site_base_url": base_url,
        "most_recent_monday_kst": date_str,
        "finding_id": finding_id,
        "overall": _overall_status(checks),
        "checks": [c.to_dict() for c in checks],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"### GRM Site Probe — {report['generated_at']} (전체: `{report['overall']}`)",
        "",
        "| 점검 | 상태 | 경과(s) | 상세 |",
        "|---|---|---|---|",
    ]
    for c in report["checks"]:
        detail = str(c["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {c['name']} | `{c['status']}` | {c['elapsed_s']:.2f} | {detail} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # 저장소 관례(tests/test_cli_stdout_encoding.py): 좁은 콘솔 인코딩(cp949 등)에서도
    # 한글·특수문자 출력이 죽지 않게 한다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--output", default="probe_report.json",
                    help="JSON 리포트 저장 경로(비우면 저장하지 않음)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="요청 1건당 타임아웃(초)")
    args = ap.parse_args(argv)

    base_url = os.environ.get("SITE_BASE_URL") or DEFAULT_SITE_BASE_URL
    supabase_url = os.environ.get("SUPABASE_URL", "")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    today = resolve_today()

    report = run_probe(
        base_url=base_url, supabase_url=supabase_url, anon_key=anon_key,
        today=today, timeout=args.timeout)

    print(render_markdown(report))

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

    return 1 if report["overall"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
