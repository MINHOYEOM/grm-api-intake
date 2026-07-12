#!/usr/bin/env python3
"""GRM 워치리스트 주간 이메일 통지(T-WL1) — LLM 0·결정론 발송 서비스.

015_firm_watchlist.sql(관심 업체 저장 계층, PR#185 라이브)에 이어지는 통지 절반. 매주
월요일, 관심 업체를 등록한 로그인 사용자에게 "지난주 등록 업체에 새 규제 지적 발생"
다이제스트 이메일을 보낸다.

★안전 설계(불가침):
  1. **원문 미포함** — 이메일에는 업체명(등록 시점 스냅샷 firm_display)·건수·최신 발행일·
     프로파일 링크만 담는다. finding_text/finding_text_ko/evidence_url 등 원문·인용·원문
     URL 은 이메일 어디에도 들어가지 않는다(공개 게이트·번역 여부와 무관하게 안전 —
     006/010 의 공개 게이트를 우회하지 않는다. 프로파일 페이지 자체가 이미 그 게이트를
     지킨다).
  2. **이중 시간조건(노이즈 방어)** — 대상 finding 은 (a) ingested_at 최근 7일 이내 AND
     (b) published_date 최근 21일 이내 를 모두 만족해야 한다. (a) 만으로는 백필/재수집이
     몇 달 전에 발행된 문서를 오늘 새로 적재했을 때도 "새 지적"으로 오통지한다(FIND-1
     백필 배치가 과거 문서를 계속 채워 넣는 것이 정상 운영이므로 이 오탐은 실제로 발생
     한다). (b) 만으로는 "최근 발행"이지만 이미 몇 주 전에 수집·통지된 문서가 각기 다른
     이유로 재적재(예: 재분류·raw_signal_id 갱신)될 때 중복 통지 위험이 있다. 두 조건을
     AND 로 겹치면 "최근에 실제로 새로 들어왔고 & 실제로 최근에 발행된" 좁은 교집합만
     남아, 백필이 옛 문서를 오늘 적재해도 조용히 제외된다.
  3. **멱등 로그** — `firm_watch_notification_log`(user_id, finding_id) PK. 이미 보낸
     쌍은 절대 재발송하지 않는다(재실행/중복 cron 안전). 발송에 성공한 쌍만 기록한다 —
     실패한 항목은 로그에 남지 않아 다음 실행이 자연히 재시도한다.
  4. **사람 승인 게이트** — 실제 발송은 `.github/workflows/grm-watchlist-notify.yml` 의
     `environment: newsletter-send`(기존 뉴스레터 발송과 동일 게이트) 를 통과해야만
     실행된다. 이 스크립트 자체는 게이트를 모른다 — 워크플로가 그 경계다.
  5. **발송 상한** — 1회 실행 최대 500통(MAX_SENDS_PER_RUN). 초과분은 이번 실행에서
     건너뛰고(로그에 기록하지 않으므로 다음 실행이 자연히 재시도) 리포트에 상한 도달
     사실을 명시한다.
  6. **키·개인정보 비노출** — SUPABASE_SERVICE_ROLE_KEY/Brevo API 키는 로그·리포트 어디에도
     등장하지 않는다(findings_translate_apply_service.py 관례 — 예외 타입명·HTTP 상태
     코드만 노출). 이메일 주소·user_id 는 리포트에 마스킹(`ab***@d***.com`)해서만 남긴다.

아키텍처(findings_translate_apply_service.py 관례 재사용):
  · service-role 키로 PostgREST 직접 호출(anon 키 없음 — 이 표들은 애초에 anon 조회 대상이
    아니다). requests 는 최상단에서만 import(이 모듈 전체가 이미 네트워크 스크립트이므로
    newsletter.py 식 지연 import 는 불필요 — 순수 코어/부트스트랩 분리가 없는 단일 CLI).
  · 5xx/timeout 1회 재시도(최대 2회 시도), 4xx 는 즉시 실패. 예외는 타입명만 노출.
  · 재시도·멱등 판단은 순수 함수로 분리해 모킹 없이(HTTP 호출 없이) 단위 테스트 가능.

Brevo 엔드포인트에 대한 의도적 이탈(스펙 대비): 기존 뉴스레터(web/newsletter.py)는 Brevo
Campaigns API(리스트 대상 단일 캠페인 1건)를 쓴다. 이 통지는 사용자마다 다른 개인화된
업체 목록을 담아야 하므로(리스트 캠페인은 전 구독자에게 동일 본문) Brevo 트랜잭션 이메일
API(`v3/smtp/email`)를 대신 쓴다 — 인증 헤더(api-key)·발신자 env 이름은 기존 뉴스레터와
동일 재사용, 엔드포인트만 1:1 개인화 발송에 맞는 것으로 바꿨다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html import escape as _esc
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

# ── 시간 조건(노이즈 방어) ────────────────────────────────────────────────────
INGESTED_WINDOW_DAYS = 7          # (a) 최근 수집분만
PUBLISHED_WINDOW_DAYS = 21        # (b) 최근 발행분만 -- (a)(b) AND 가 노이즈 방어의 핵심
MAX_SENDS_PER_RUN = 500           # (f) 1실행 발송 상한
DEFAULT_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2                 # 초회 시도 + 재시도 1회(5xx/timeout 만)
_PG_PAGE_SIZE = 1000
_PG_MAX_PAGES = 50                # 안전망(최대 50,000행/쿼리) -- findings_translate_apply_service 관례
_IN_CHUNK_SIZE = 100               # PostgREST `in.()` 절 청크 크기
_ADMIN_PAGE_SIZE = 1000
_ADMIN_MAX_PAGES = 200             # 안전망(최대 200,000 사용자)

SITE_BASE_URL_DEFAULT = "https://grm-solutions.com"

# 면책 캐논(web/newsletter.py DISCLOSURE_KO/EN 과 동일 문안 — 드리프트 가드는
# tests/test_watchlist_notify_service.py 가 오프라인 텍스트 대조로 고정한다).
DISCLOSURE_KO = ("요약·번역·시사점·점검·심층분석은 생성형 AI가 작성하였으며, "
                 "수치·원문 인용·링크·기계 추출 표는 원문을 그대로 제공합니다. "
                 "본 내용은 참고자료이며, 의사결정 전 공식 원문을 확인하십시오.")
DISCLOSURE_EN = ("This digest is AI-generated from primary sources; interpretations are not "
                 "official or legal advice — verify against the original before acting.")

_WRAP = ("font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
         "Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif")


# ── 마스킹(리포트 전용 -- 이메일 발송 자체는 원본 주소 사용) ──────────────────
def mask_email(addr: str) -> str:
    """`ab***@d***.com` 형태로 마스킹. `@`가 없거나 빈 값이면 `***`."""
    text = str(addr or "").strip()
    if "@" not in text:
        return "***"
    local, _, domain = text.partition("@")
    local_mask = (local[:2] if len(local) >= 2 else local) + "***"
    if "." in domain:
        head, _, rest = domain.partition(".")
        tld = rest.rsplit(".", 1)[-1] if rest else ""
        domain_mask = (head[:1] if head else "") + "***" + (f".{tld}" if tld else "")
    else:
        domain_mask = (domain[:1] if domain else "") + "***"
    return f"{local_mask}@{domain_mask}"


def mask_user_id(user_id: str) -> str:
    """uuid 앞 조각만 남기고 마스킹(`3fa85f64-***`). uuid 형태가 아니어도 안전 폴백."""
    text = str(user_id or "")
    head = text.split("-", 1)[0]
    return f"{head}-***" if head else "***"


# ── 시간 창(순수) ─────────────────────────────────────────────────────────────
def time_window_params(now: datetime) -> dict[str, str]:
    """`now` 기준 (a)(b) 창의 하한을 PostgREST 필터 값으로 반환.
    ingested_since = timestamptz(초 단위, UTC, `Z` 접미) / published_since = `YYYY-MM-DD`."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ingested_since = (now - timedelta(days=INGESTED_WINDOW_DAYS))
    published_since = (now - timedelta(days=PUBLISHED_WINDOW_DAYS)).date()
    return {
        "ingested_since": ingested_since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "published_since": published_since.isoformat(),
    }


def _parse_ts(value: Any) -> "datetime | None":
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def filter_candidate_findings(findings: list[dict[str, Any]], *, ingested_since: str,
                              published_since: str) -> list[dict[str, Any]]:
    """이중 시간조건(방어적 재검증) — findings 쿼리가 이미 서버측에서 같은 조건으로
    필터링했더라도, 여기서 다시 순수하게 검증한다(네트워크 없이 테스트 가능한 계약).
    scope_status='ok' 도 함께 재확인한다(010 공개 순도 관례 계승)."""
    since_dt = _parse_ts(ingested_since)
    out: list[dict[str, Any]] = []
    for f in findings:
        if str(f.get("scope_status") or "ok") != "ok":
            continue
        ingested_dt = _parse_ts(f.get("ingested_at"))
        if since_dt is not None and (ingested_dt is None or ingested_dt < since_dt):
            continue
        published = str(f.get("published_date") or "")
        if published < published_since:
            continue
        out.append(f)
    return out


# ── 사용자별 매칭(순수) ───────────────────────────────────────────────────────
@dataclass
class FirmMatch:
    firm_key: str
    firm_display: str
    finding_ids: list[str] = field(default_factory=list)
    latest_published_date: str = ""


def build_user_firm_matches(watchlist: list[dict[str, Any]],
                            findings: list[dict[str, Any]]) -> dict[str, dict[str, FirmMatch]]:
    """워치리스트 × 후보 findings → user_id -> firm_key -> FirmMatch. 이미 시간조건/
    scope_status 필터링된 findings 를 입력으로 받는다(`filter_candidate_findings` 이후)."""
    firm_to_findings: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        firm_to_findings.setdefault(str(f.get("firm_key") or ""), []).append(f)

    out: dict[str, dict[str, FirmMatch]] = {}
    for entry in watchlist:
        user_id = str(entry.get("user_id") or "")
        firm_key = str(entry.get("firm_key") or "")
        if not user_id or not firm_key:
            continue
        matches = firm_to_findings.get(firm_key)
        if not matches:
            continue
        firm_display = str(entry.get("firm_display") or "") or (
            matches[0].get("firm_name") or firm_key)
        finding_ids = sorted({str(m.get("finding_id") or "") for m in matches})
        latest = max((str(m.get("published_date") or "") for m in matches), default="")
        out.setdefault(user_id, {})[firm_key] = FirmMatch(
            firm_key=firm_key, firm_display=firm_display,
            finding_ids=finding_ids, latest_published_date=latest,
        )
    return out


def exclude_already_notified(
    user_matches: dict[str, dict[str, FirmMatch]],
    already_notified: set[tuple[str, str]],
) -> dict[str, dict[str, FirmMatch]]:
    """멱등 — (user_id, finding_id) 가 이미 로그에 있으면 그 finding_id 를 제외한다.
    한 firm 의 finding_id 가 전부 이미 통지된 상태면 그 firm 항목 자체를 드롭하고,
    사용자에게 남은 firm 이 하나도 없으면 그 사용자 자체를 드롭한다(통지할 게 있는
    사용자만 남긴다)."""
    out: dict[str, dict[str, FirmMatch]] = {}
    for user_id, firms in user_matches.items():
        kept_firms: dict[str, FirmMatch] = {}
        for firm_key, match in firms.items():
            remaining = [fid for fid in match.finding_ids
                        if (user_id, fid) not in already_notified]
            if remaining:
                kept_firms[firm_key] = FirmMatch(
                    firm_key=match.firm_key, firm_display=match.firm_display,
                    finding_ids=remaining, latest_published_date=match.latest_published_date,
                )
        if kept_firms:
            out[user_id] = kept_firms
    return out


def apply_send_cap(user_matches: dict[str, dict[str, FirmMatch]],
                   cap: int = MAX_SENDS_PER_RUN) -> tuple[dict[str, dict[str, FirmMatch]], list[str]]:
    """발송 상한(e) — 사용자(=이메일 1통) 단위로 상한을 적용한다. 결정론(user_id 사전순)
    으로 앞에서부터 cap 개만 남기고, 잘린 user_id 목록을 반환한다(로그에 남기지 않으므로
    다음 실행이 자연히 재시도)."""
    ordered = sorted(user_matches.keys())
    kept_ids = ordered[:cap]
    skipped_ids = ordered[cap:]
    kept = {uid: user_matches[uid] for uid in kept_ids}
    return kept, skipped_ids


# ── 이메일 조립(순수) ─────────────────────────────────────────────────────────
def firm_profile_url(site_base_url: str, firm_key: str) -> str:
    base = site_base_url.rstrip("/")
    return f"{base}/findings/firm/index.html?key={quote(firm_key, safe='')}"


def _sorted_firm_entries(firms: dict[str, FirmMatch]) -> list[FirmMatch]:
    return sorted(firms.values(),
                 key=lambda m: (m.latest_published_date, m.firm_display), reverse=True)


def build_subject(firms: dict[str, FirmMatch]) -> str:
    entries = _sorted_firm_entries(firms)
    if not entries:
        return "관심 업체 규제 동향"
    if len(entries) == 1:
        return f"관심 업체 규제 동향 — {entries[0].firm_display}"
    return f"관심 업체 규제 동향 — {entries[0].firm_display} 외 {len(entries) - 1}개 업체"


def build_email(firms: dict[str, FirmMatch], *, site_base_url: str) -> dict[str, str]:
    """firm_key -> FirmMatch 매핑 1인분 → {subject, html, text}. 원문 미포함(업체명·건수·
    발행일·프로파일 링크만). 순수·결정론(now()/난수 0)."""
    entries = _sorted_firm_entries(firms)
    subject = build_subject(firms)
    e = _esc

    html_items: list[str] = []
    text_items: list[str] = []
    for m in entries:
        url = firm_profile_url(site_base_url, m.firm_key)
        n = len(m.finding_ids)
        html_items.append(
            '<li style="margin:10px 0">'
            f'<b style="color:#141413">{e(m.firm_display)}</b>: 새 지적 {n}건 '
            f'({e(m.latest_published_date)}) → '
            f'<a href="{e(url)}" style="color:#A14B30;font-weight:600">프로파일 보기 →</a>'
            '</li>'
        )
        text_items.append(f"- {m.firm_display}: 새 지적 {n}건 ({m.latest_published_date}) → {url}")

    parts = [
        "<!DOCTYPE html>",
        '<html lang="ko"><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width,initial-scale=1" />',
        f"<title>{e(subject)}</title></head>",
        f'<body style="margin:0;padding:0;background:#FAF9F5;{_WRAP}">',
        '<div style="max-width:600px;margin:0 auto;padding:32px 24px;color:#3D3D3A;'
        'font-size:16px;line-height:1.6">',
        '<div style="font-size:12px;font-weight:600;letter-spacing:.08em;'
        'text-transform:uppercase;color:#A14B30">Global Regulatory Monitor</div>',
        '<h1 style="font-size:20px;line-height:1.3;color:#141413;margin:10px 0 18px">'
        '관심 업체 규제 동향</h1>',
        '<ul style="margin:0 0 24px;padding-left:20px;color:#141413">',
        *html_items,
        "</ul>",
        '<div style="border-top:1px solid #E6DFD8;margin-top:8px;padding-top:18px;'
        'font-size:12px;line-height:1.7;color:#6C6A64">',
        '알림 해제는 마이페이지에서 관심 업체를 삭제하세요.',
        f'<div style="margin-top:10px"><b style="color:#3D3D3A">AI 자동 생성 안내</b> · '
        f'{e(DISCLOSURE_KO)}</div>',
        f'<div style="margin-top:6px;color:#8E8B82">{e(DISCLOSURE_EN)}</div>',
        "</div>",
        "</div></body></html>",
    ]
    text = "\n".join([
        "관심 업체 규제 동향", "",
        *text_items, "",
        "알림 해제는 마이페이지에서 관심 업체를 삭제하세요.", "",
        DISCLOSURE_KO, DISCLOSURE_EN,
    ])
    return {"subject": subject, "html": "".join(parts), "text": text}


# ── PostgREST/Admin API I/O(service-role 키. requests 는 여기서만 실사용) ─────
def _headers(service_key: str) -> dict[str, str]:
    return {"apikey": service_key, "Authorization": f"Bearer {service_key}",
           "Content-Type": "application/json"}


def _request_with_retry(method: str, url: str, *, headers: dict[str, str],
                        params: dict[str, Any] | None = None,
                        json_body: Any = None,
                        timeout: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[int, Any, str]:
    """공용 재시도 래퍼(findings_translate_apply_service._patch_finding 관례) —
    5xx/timeout 1회 재시도, 4xx 즉시 실패. 반환=(status, parsed_json_or_None, error).
    error 는 ""(성공)·"timeout"·예외 타입명·"http_{status}" 뿐 — 키/원문 절대 미노출."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.request(method, url, headers=headers, params=params,
                                    json=json_body, timeout=timeout)
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, None, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, None, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            return resp.status_code, None, f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, f"http_{resp.status_code}"
        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = None
        return resp.status_code, data, ""
    return 0, None, "retry_exhausted"  # 도달 불가(안전망)


def fetch_all_watchlist(base_url: str, service_key: str) -> tuple[list[dict[str, Any]], str]:
    """firm_watchlist 전량(service-role — RLS 우회). 반환=(rows, error)."""
    url = f"{base_url}/rest/v1/firm_watchlist"
    headers = _headers(service_key)
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_PG_MAX_PAGES):
        params = {"select": "user_id,firm_key,firm_display", "order": "user_id.asc",
                  "limit": _PG_PAGE_SIZE, "offset": offset}
        status, data, err = _request_with_retry("GET", url, headers=headers, params=params)
        if err:
            return rows, err
        batch = data if isinstance(data, list) else []
        rows.extend(batch)
        if len(batch) < _PG_PAGE_SIZE:
            break
        offset += _PG_PAGE_SIZE
    return rows, ""


def fetch_candidate_findings(base_url: str, service_key: str, *, ingested_since: str,
                             published_since: str) -> tuple[list[dict[str, Any]], str]:
    """이중 시간조건 + scope_status='ok' 를 서버측에서 먼저 걸러 받아온다(방어적으로
    `filter_candidate_findings` 가 다시 재검증). 반환=(rows, error)."""
    url = f"{base_url}/rest/v1/findings"
    headers = _headers(service_key)
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_PG_MAX_PAGES):
        params = {
            "select": "finding_id,firm_key,firm_name,published_date,ingested_at,scope_status",
            "scope_status": "eq.ok",
            "ingested_at": f"gte.{ingested_since}",
            "published_date": f"gte.{published_since}",
            "order": "published_date.desc",
            "limit": _PG_PAGE_SIZE, "offset": offset,
        }
        status, data, err = _request_with_retry("GET", url, headers=headers, params=params)
        if err:
            return rows, err
        batch = data if isinstance(data, list) else []
        rows.extend(batch)
        if len(batch) < _PG_PAGE_SIZE:
            break
        offset += _PG_PAGE_SIZE
    return rows, ""


def fetch_already_notified(base_url: str, service_key: str,
                           finding_ids: list[str]) -> tuple[set[tuple[str, str]], str]:
    """finding_id 목록으로 firm_watch_notification_log 를 조회 -> {(user_id, finding_id)}.
    청크 처리(`in.()` 절 길이 방어)."""
    url = f"{base_url}/rest/v1/firm_watch_notification_log"
    headers = _headers(service_key)
    out: set[tuple[str, str]] = set()
    uniq = sorted({fid for fid in finding_ids if fid})
    for i in range(0, len(uniq), _IN_CHUNK_SIZE):
        chunk = uniq[i:i + _IN_CHUNK_SIZE]
        if not chunk:
            continue
        params = {"select": "user_id,finding_id",
                 "finding_id": "in.(" + ",".join(chunk) + ")"}
        status, data, err = _request_with_retry("GET", url, headers=headers, params=params)
        if err:
            return out, err
        for row in (data if isinstance(data, list) else []):
            out.add((str(row.get("user_id") or ""), str(row.get("finding_id") or "")))
    return out, ""


def fetch_user_emails(base_url: str, service_key: str,
                      user_ids: set[str]) -> tuple[dict[str, str], str]:
    """Supabase Auth Admin API(`GET /auth/v1/admin/users`, service-role)로 user_id ->
    email 매핑을 조회한다. GoTrue 관리자 API 는 임의 id 목록 필터를 지원하지 않으므로
    필요한 만큼 페이지네이션 순회하며, 필요한 id 를 모두 찾으면 조기 종료한다."""
    url = f"{base_url}/auth/v1/admin/users"
    headers = _headers(service_key)
    needed = set(user_ids)
    out: dict[str, str] = {}
    if not needed:
        return out, ""
    for page in range(1, _ADMIN_MAX_PAGES + 1):
        params = {"page": page, "per_page": _ADMIN_PAGE_SIZE}
        status, data, err = _request_with_retry("GET", url, headers=headers, params=params)
        if err:
            return out, err
        users = (data or {}).get("users") if isinstance(data, dict) else None
        users = users or []
        for u in users:
            uid = str(u.get("id") or "")
            if uid in needed:
                out[uid] = str(u.get("email") or "")
        if len(out) >= len(needed):
            break
        if len(users) < _ADMIN_PAGE_SIZE:
            break
    return out, ""


def insert_notification_log(base_url: str, service_key: str,
                            pairs: list[tuple[str, str]]) -> str:
    """성공 발송된 (user_id, finding_id) 쌍을 로그에 삽입(ON CONFLICT DO NOTHING —
    `Prefer: resolution=ignore-duplicates`). 청크 처리. 반환=error("" 이면 성공)."""
    if not pairs:
        return ""
    url = f"{base_url}/rest/v1/firm_watch_notification_log"
    headers = dict(_headers(service_key))
    headers["Prefer"] = "resolution=ignore-duplicates,return=minimal"
    for i in range(0, len(pairs), _IN_CHUNK_SIZE):
        chunk = pairs[i:i + _IN_CHUNK_SIZE]
        body = [{"user_id": uid, "finding_id": fid} for uid, fid in chunk]
        status, _data, err = _request_with_retry("POST", url, headers=headers, json_body=body)
        if err:
            return err
    return ""


# ── Brevo 트랜잭션 이메일 발송 ─────────────────────────────────────────────────
class WatchlistNotifySender:
    """Brevo 트랜잭션 이메일 API(`v3/smtp/email`) 어댑터. 뉴스레터의 Campaigns API 와
    달리 수신자 1인당 개인화된 본문을 그때그때 보낸다(리스트 불필요)."""

    def __init__(self, api_key: str, *, base_url: str = "https://api.brevo.com/v3",
                sender_name: str, sender_email: str, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base = base_url.rstrip("/")
        self.sender_name = sender_name
        self.sender_email = sender_email
        self.api_key = api_key
        self.timeout = timeout

    def send(self, to_email: str, subject: str, html: str, text: str) -> tuple[bool, str]:
        headers = {"api-key": self.api_key, "accept": "application/json",
                  "content-type": "application/json"}
        body = {
            "sender": {"name": self.sender_name, "email": self.sender_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html,
            "textContent": text,
        }
        status, _data, err = _request_with_retry(
            "POST", f"{self.base}/smtp/email", headers=headers, json_body=body,
            timeout=self.timeout,
        )
        return (err == ""), err


# ── 오케스트레이션 ────────────────────────────────────────────────────────────
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def run(*, supabase_url: str, service_role_key: str, brevo_api_key: str,
       sender_name: str, sender_email: str, site_base_url: str,
       dry_run: bool, now: "datetime | None" = None,
       cap: int = MAX_SENDS_PER_RUN) -> dict[str, Any]:
    """전체 절차(a~h, 모듈 docstring 참조). 반환=리포트(마스킹된 사람 읽기용 dict)."""
    now = now or datetime.now(timezone.utc)
    window = time_window_params(now)
    report: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "send",
        "window": window,
        "users_candidate": 0,
        "users_notified": 0,
        "users_capped_skipped": 0,
        "findings_notified_total": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "recipients": [],       # 마스킹된 사람별 요약
        "errors": [],
    }

    watchlist, err = fetch_all_watchlist(supabase_url, service_role_key)
    if err:
        report["errors"].append(f"firm_watchlist 조회 실패: {err}")
        return report
    if not watchlist:
        return report  # 관심 업체 등록이 하나도 없음 -- 조용히 종료(정상 상태)

    findings, err = fetch_candidate_findings(
        supabase_url, service_role_key,
        ingested_since=window["ingested_since"], published_since=window["published_since"],
    )
    if err:
        report["errors"].append(f"findings 조회 실패: {err}")
        return report

    # 방어적 재검증(순수) -- 서버 필터를 그대로 신뢰하지 않는다.
    findings = filter_candidate_findings(
        findings, ingested_since=window["ingested_since"],
        published_since=window["published_since"],
    )
    if not findings:
        return report  # 이번 창에 새 지적 없음 -- 정상 종료

    user_matches = build_user_firm_matches(watchlist, findings)
    if not user_matches:
        return report  # 후보 findings 는 있으나 워치리스트와 매칭되는 업체가 없음

    all_finding_ids = sorted({fid for firms in user_matches.values()
                             for m in firms.values() for fid in m.finding_ids})
    already_notified, err = fetch_already_notified(supabase_url, service_role_key, all_finding_ids)
    if err:
        report["errors"].append(f"notification_log 조회 실패: {err}")
        return report

    user_matches = exclude_already_notified(user_matches, already_notified)
    report["users_candidate"] = len(user_matches)
    if not user_matches:
        return report  # 전부 이미 통지됨(멱등) -- 정상 종료

    user_matches, skipped_ids = apply_send_cap(user_matches, cap=cap)
    report["users_capped_skipped"] = len(skipped_ids)
    if skipped_ids:
        report["errors"].append(
            f"발송 상한({cap}통) 도달 -- {len(skipped_ids)}명 이번 실행 제외(다음 실행 자동 재시도)"
        )

    findings_total = sum(len(m.finding_ids) for firms in user_matches.values()
                        for m in firms.values())
    report["findings_notified_total"] = findings_total

    if dry_run:
        report["users_notified"] = len(user_matches)
        for user_id, firms in sorted(user_matches.items()):
            report["recipients"].append({
                "user_id": mask_user_id(user_id),
                "firms": len(firms),
                "findings": sum(len(m.finding_ids) for m in firms.values()),
            })
        return report

    # 실발송 경로 -- 자격 확인.
    if not brevo_api_key or not sender_email:
        report["errors"].append(
            "Brevo 자격(NEWSLETTER_API_KEY/GRM_NEWSLETTER_SENDER_EMAIL) 미설정 -- 발송 불가"
        )
        return report

    needed_ids = set(user_matches.keys())
    emails, err = fetch_user_emails(supabase_url, service_role_key, needed_ids)
    if err:
        report["errors"].append(f"Auth Admin 사용자 조회 실패: {err}")
        return report

    sender = WatchlistNotifySender(brevo_api_key, sender_name=sender_name,
                                   sender_email=sender_email)
    sent_pairs: list[tuple[str, str]] = []
    for user_id, firms in sorted(user_matches.items()):
        email_addr = emails.get(user_id, "")
        recipient_summary = {
            "user_id": mask_user_id(user_id),
            "email": mask_email(email_addr) if email_addr else "(no-email)",
            "firms": len(firms),
            "findings": sum(len(m.finding_ids) for m in firms.values()),
            "sent": False,
        }
        if not email_addr:
            report["emails_failed"] += 1
            report["errors"].append(f"user_id={mask_user_id(user_id)}: 이메일 미발견(Auth 조회)")
            report["recipients"].append(recipient_summary)
            continue

        mail = build_email(firms, site_base_url=site_base_url)
        ok, send_err = sender.send(email_addr, mail["subject"], mail["html"], mail["text"])
        if not ok:
            report["emails_failed"] += 1
            report["errors"].append(f"user_id={mask_user_id(user_id)}: 발송 실패({send_err})")
            report["recipients"].append(recipient_summary)
            continue

        recipient_summary["sent"] = True
        report["recipients"].append(recipient_summary)
        report["emails_sent"] += 1
        for m in firms.values():
            for fid in m.finding_ids:
                sent_pairs.append((user_id, fid))

    report["users_notified"] = report["emails_sent"]

    log_err = insert_notification_log(supabase_url, service_role_key, sent_pairs)
    if log_err:
        report["errors"].append(
            f"notification_log 기록 실패({log_err}) -- 발송은 성공했으나 로그 누락 위험, "
            "다음 실행에서 (user, finding) 재통지 가능성 있음"
        )

    return report


# ── CLI ───────────────────────────────────────────────────────────────────────
def _write_report(path: "str | None", report: dict[str, Any]) -> None:
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    print(text)


def main(argv: "list[str] | None" = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        description="GRM 워치리스트 주간 이메일 통지 -- 결정론 발송 서비스(LLM 0)")
    ap.add_argument("--dry-run", action="store_true",
                    help="발송·로그 기록 없이 대상 매트릭스만 리포트")
    ap.add_argument("--output", help="리포트 JSON 저장 경로(기본: stdout만)")
    args = ap.parse_args(argv)

    supabase_url = _env("SUPABASE_URL")
    service_role_key = _env("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        print("watchlist_notify_service: SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 필요",
              file=sys.stderr)
        return 2

    brevo_api_key = _env("NEWSLETTER_API_KEY")
    sender_name = _env("GRM_NEWSLETTER_SENDER_NAME", "Global Regulatory Monitor")
    sender_email = _env("GRM_NEWSLETTER_SENDER_EMAIL")
    site_base_url = _env("GRM_SITE_BASE_URL", SITE_BASE_URL_DEFAULT)

    report = run(
        supabase_url=supabase_url, service_role_key=service_role_key,
        brevo_api_key=brevo_api_key, sender_name=sender_name, sender_email=sender_email,
        site_base_url=site_base_url, dry_run=args.dry_run,
    )
    _write_report(args.output, report)
    return 1 if report["errors"] else 0


__all__ = [
    "mask_email", "mask_user_id", "time_window_params", "filter_candidate_findings",
    "build_user_firm_matches", "exclude_already_notified", "apply_send_cap",
    "build_subject", "build_email", "firm_profile_url",
    "fetch_all_watchlist", "fetch_candidate_findings", "fetch_already_notified",
    "fetch_user_emails", "insert_notification_log", "WatchlistNotifySender", "run", "main",
    "FirmMatch", "DISCLOSURE_KO", "DISCLOSURE_EN",
    "INGESTED_WINDOW_DAYS", "PUBLISHED_WINDOW_DAYS", "MAX_SENDS_PER_RUN",
]


if __name__ == "__main__":
    raise SystemExit(main())
