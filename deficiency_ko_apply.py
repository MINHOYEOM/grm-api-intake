#!/usr/bin/env python3
"""[지적 사례 한국어 2026-11-11] PMDA·MHRA 지적 사례(134 deficiency_examples)에 한국어 칸(140)을 채운다.

원천: translations/deficiency_examples_ko.json(저장소에 커밋된 번역 정본)
  rows    [{id: 'source_key#row_no', chash: 번역할 때의 content_hash, ko: 한국어}]  — 지적 문장 번역
  labels  {기관 분류 원문(공백 정규화): 한국어}                                  — 모든 행(xlsx 포함)의 분류
쓰는 칸: statement_ko · ko_source_hash(=chash) · agency_category_ko. 원문 칸은 건드리지 않는다.

규칙:
  · 문장 번역은 DB 의 content_hash 가 chash 와 같은 행에만 쓴다(원문이 바뀐 행은 건너뛰고 stale 로 센다).
  · 분류는 (source_key, agency_category 원문) 묶음마다 PATCH 한 번 — xlsx 11,501행을 한 줄씩 보내지 않는다.
  · 이미 같은 값이면 보내지 않는다(다시 돌려도 변화 0).
  · 끝나면 다시 읽어 남은 행(문장이 있는데 유효한 한국어가 없는 행 · 분류 한국어가 빈 행)을 센다.

실행(워크플로 grm-deficiency-cases-refresh.yml 이 스냅샷 전에 부른다, 또는 로컬):
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python deficiency_ko_apply.py [--dry-run]
exit: 0 = 남은 행 0 · 3 = 적용은 했지만 남은 행 있음 · 2 = 입력/읽기/쓰기 실패
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials

DEFAULT_IN = Path(__file__).resolve().parent / "translations" / "deficiency_examples_ko.json"
SCHEMA = "grm-deficiency-ko/v1"
TABLE = "deficiency_examples"
FIELDS = ("source_key", "row_no", "statement", "agency_category", "content_hash",
          "statement_ko", "agency_category_ko", "ko_source_hash")
PAGE_SIZE = 1000
_ID = re.compile(r"^(pmda|mhra):[a-z0-9_]{3,80}#\d{1,6}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_HANGUL = re.compile(r"[가-힣]")


def norm(s: Any) -> str:
    return " ".join(str(s or "").split())


def load(path: Path) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA:
        raise ValueError(f"schema {d.get('schema')!r}")
    rows: dict[str, tuple[str, str]] = {}
    for r in d.get("rows") or []:
        i, h, ko = str(r.get("id") or ""), str(r.get("chash") or ""), str(r.get("ko") or "").strip()
        if not _ID.match(i) or not _HASH.match(h) or not _HANGUL.search(ko) or len(ko) > 20000:
            raise ValueError(f"행 형식: {i!r}")
        if i in rows:
            raise ValueError(f"행 중복: {i}")
        rows[i] = (h, ko)
    labels: dict[str, str] = {}
    for k, v in (d.get("labels") or {}).items():
        v = norm(v)
        if not _HANGUL.search(v) or len(v) > 300:
            raise ValueError(f"분류 형식: {k!r}")
        labels[norm(k)] = v
    return rows, labels


def fetch(http: Any, base: str, key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    off = 0
    while True:
        r = http.get(f"{base}/rest/v1/{TABLE}",
                     params={"select": ",".join(FIELDS), "order": "source_key.asc,row_no.asc",
                             "limit": str(PAGE_SIZE), "offset": str(off)},
                     headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"read HTTP {r.status_code}: {r.text[:200]}")
        page = r.json()
        out.extend(page)
        if len(page) < PAGE_SIZE:
            return out
        off += PAGE_SIZE


def plan(db: list[dict[str, Any]], rows: dict[str, tuple[str, str]],
         labels: dict[str, str]) -> tuple[list[tuple[dict[str, str], dict[str, str]]], dict[str, int]]:
    """(PATCH 목록[(필터, 본문)], 통계). 순수 함수."""
    patches: list[tuple[dict[str, str], dict[str, str]]] = []
    st = {"stmt_write": 0, "stmt_same": 0, "stale": 0, "label_groups": 0, "label_rows": 0}
    groups: dict[tuple[str, str], tuple[str, int]] = {}
    for r in db:
        rid = f"{r['source_key']}#{r['row_no']}"
        t = rows.get(rid)
        if t:
            h, ko = t
            if h != r["content_hash"]:
                st["stale"] += 1
            elif r.get("statement_ko") == ko and r.get("ko_source_hash") == h:
                st["stmt_same"] += 1
            else:
                st["stmt_write"] += 1
                patches.append(({"source_key": f"eq.{r['source_key']}", "row_no": f"eq.{r['row_no']}",
                                 "content_hash": f"eq.{h}"},
                                {"statement_ko": ko, "ko_source_hash": h}))
        lab = labels.get(norm(r.get("agency_category")))
        if lab and r.get("agency_category_ko") != lab:
            g = (r["source_key"], r.get("agency_category") or "")
            groups[g] = (lab, groups.get(g, (lab, 0))[1] + 1)
    for (sk, raw), (lab, n) in sorted(groups.items()):
        st["label_groups"] += 1
        st["label_rows"] += n
        patches.append(({"source_key": f"eq.{sk}", "agency_category": f"eq.{raw}"},
                        {"agency_category_ko": lab}))
    return patches, st


def remaining(db: list[dict[str, Any]]) -> dict[str, int]:
    stmt = sum(1 for r in db if str(r.get("statement") or "").strip()
               and (not str(r.get("statement_ko") or "").strip()
                    or r.get("ko_source_hash") != r.get("content_hash")))
    lab = sum(1 for r in db if not str(r.get("agency_category_ko") or "").strip())
    return {"rows": len(db), "statement_missing": stmt, "category_missing": lab}


def main(argv: "list[str] | None" = None, *, http: Any = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="PMDA·MHRA 지적 사례 한국어 칸 채우기")
    ap.add_argument("--supabase-url", default="")
    ap.add_argument("--service-role-key", default="")
    ap.add_argument("--input", type=Path, default=DEFAULT_IN)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        rows, labels = load(args.input)
    except (OSError, ValueError) as exc:
        print(f"입력 실패: {exc}", file=sys.stderr)
        return 2
    creds = resolve_supabase_service_credentials(args)
    if creds is None:
        print("SUPABASE_URL·SUPABASE_SERVICE_ROLE_KEY 가 필요합니다.", file=sys.stderr)
        return 2
    base, key = normalize_supabase_url(creds[0]) or "", creds[1]
    if http is None:
        import requests  # 지연 import — 테스트는 http 를 주입한다
        http = requests
    try:
        db = fetch(http, base, key)
        patches, st = plan(db, rows, labels)
        print("plan", json.dumps(st, ensure_ascii=False), "patches", len(patches))
        if args.dry_run:
            print("remaining(before)", json.dumps(remaining(db), ensure_ascii=False))
            return 0
        hdr = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Prefer": "return=minimal"}
        for flt, body in patches:
            r = http.patch(f"{base}/rest/v1/{TABLE}", params=flt, headers=hdr, json=body, timeout=60)
            if r.status_code not in (200, 204):
                raise RuntimeError(f"PATCH HTTP {r.status_code} {flt.get('source_key')}: {r.text[:200]}")
        left = remaining(fetch(http, base, key))
    except (RuntimeError, KeyError, TypeError, ValueError) as exc:
        print(f"적용 실패: {exc}", file=sys.stderr)
        return 2
    print("remaining", json.dumps(left, ensure_ascii=False))
    return 0 if not left["statement_missing"] and not left["category_missing"] else 3


if __name__ == "__main__":
    sys.exit(main())
