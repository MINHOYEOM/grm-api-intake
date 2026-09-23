#!/usr/bin/env python3
"""findings_search_cache_sync — 086 검색 캐시의 daily 목록을 용어사전 사례 링크(glossary_cases.json)와 맞춘다.

무엇을: `web/data/glossary_cases.json` 의 `items[].q`(용어사전 "사례 N건 보기" 링크의 검색어)를
모아 RPC `findings_search_cache_sync(p_qs)`(087) 에 넘긴다. 표에 직접 쓰지 않는다 — 넣고 지우는
판단(정규화·hot 보호·삭제 상한·형식 게이트)은 전부 DB 함수가 한다. 이 스크립트는 목록을 읽어
넘기고 결과를 보고할 뿐이다.

왜: 086 은 목록을 시드로 굳혀 새 용어의 링크가 캐시를 못 탔다. 주간 용어 재측정이 파일을 바꾸면
(main push) 이 스크립트가 돌아 목록을 맞추고, 새 행은 다음 daily 갱신(12:10 KST)이 채운다.

보안: service-role 키로 RPC 하나만 부른다. 키는 어떤 로그·예외 메시지·report 에도 넣지 않는다
(예외 타입명·HTTP status 만 표면화 — findings_review_promote_service 관례). --dry-run 은
네트워크 0 이다(목록만 세고 report 를 쓴다).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests

from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials

DEFAULT_GLOSSARY_CASES = Path("web") / "data" / "glossary_cases.json"
RPC_NAME = "findings_search_cache_sync"
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TERMS = 400          # 087 의 상한과 같다 — 넘으면 서버가 거부하므로 먼저 막는다
MAX_TERM_LEN = 64


def load_terms(path: str | Path) -> list[str]:
    """glossary_cases.json → 정렬된 중복 없는 q 목록(btrim, 빈 값 제외). 형식 게이트는 서버와 같다."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items") if isinstance(data, dict) else None
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        raise ValueError("glossary_cases.json: 'items' 배열이 없습니다")
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        q = str(it.get("q") or "").strip()
        if not q or len(q) > MAX_TERM_LEN:
            continue
        if any(ord(ch) < 32 for ch in q):
            continue
        seen.add(q)
    return sorted(seen)


def build_payload(terms: list[str]) -> dict[str, Any]:
    return {"p_qs": list(terms)}


def call_sync(base_url: str, service_key: str, terms: list[str], *,
              timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    base = normalize_supabase_url(base_url)
    if base is None:
        raise ValueError("SUPABASE_URL 형식 오류(https:// 필요)")
    url = f"{base}/rest/v1/rpc/{RPC_NAME}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    last_status: int | None = None
    for attempt in (1, 2):
        try:
            resp = requests.post(url, headers=headers, json=build_payload(terms), timeout=timeout)
        except requests.RequestException as exc:  # 키가 담길 수 있는 메시지는 쓰지 않는다
            if attempt == 2:
                raise RuntimeError(f"RPC 요청 실패: {type(exc).__name__}") from None
            continue
        last_status = resp.status_code
        if 500 <= resp.status_code < 600 and attempt == 1:
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"RPC HTTP {resp.status_code}")
        body = resp.json()
        if not isinstance(body, dict):
            raise RuntimeError("RPC 응답이 객체가 아님")
        return body
    raise RuntimeError(f"RPC HTTP {last_status}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--glossary-cases", default=str(DEFAULT_GLOSSARY_CASES))
    p.add_argument("--supabase-url", default=None)
    p.add_argument("--service-role-key", default=None)
    p.add_argument("--dry-run", action="store_true", help="네트워크 없이 목록만 세고 report 를 쓴다")
    p.add_argument("--output", default=None, help="report JSON 경로")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return p


def _write_report(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        terms = load_terms(args.glossary_cases)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[cache-sync] 목록 읽기 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    report: dict[str, Any] = {
        "schema_version": "grm-findings-search-cache-sync/v1",
        "glossary_cases": str(args.glossary_cases),
        "terms": len(terms),
        "dry_run": bool(args.dry_run),
    }
    if not terms:
        report["error"] = "empty-term-list"
        _write_report(args.output, report)
        print("[cache-sync] 용어 목록이 비어 있어 중단(서버도 빈 목록은 거부한다)", file=sys.stderr)
        return 2
    if len(terms) > MAX_TERMS:
        report["error"] = "too-many-terms"
        _write_report(args.output, report)
        print(f"[cache-sync] 용어 {len(terms)}개 > 상한 {MAX_TERMS} — 중단", file=sys.stderr)
        return 2
    if args.dry_run:
        report["preview"] = terms[:10]
        _write_report(args.output, report)
        print(f"[cache-sync] dry-run · 용어 {len(terms)}개 · 네트워크 0")
        return 0
    creds = resolve_supabase_service_credentials(args)
    if creds is None:
        report["error"] = "missing-credentials"
        _write_report(args.output, report)
        print("[cache-sync] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
        return 2
    base_url, service_key = creds
    try:
        result = call_sync(base_url, service_key, terms, timeout=args.timeout)
    except (RuntimeError, ValueError) as exc:
        report["error"] = str(exc)
        _write_report(args.output, report)
        print(f"[cache-sync] 실패: {exc}", file=sys.stderr)
        return 1
    report["result"] = result
    _write_report(args.output, report)
    print("[cache-sync] "
          f"received={result.get('received')} valid={result.get('valid')} dropped={result.get('dropped')} "
          f"added={result.get('added')} removed={result.get('removed')} "
          f"total_daily={result.get('total_daily')} hot_rows={result.get('hot_rows')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
