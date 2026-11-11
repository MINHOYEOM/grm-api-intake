#!/usr/bin/env python3
"""식약처 의약품 GMP 적합판정서 발급 현황 — 월 1회 전체 목록 스냅샷(Notion·브리프 무접촉).

원천: 공공데이터포털 15097207 `DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq`.
현황표라 게시일·변경일이 없어 날짜로 나눌 수 없다 → 매달 **전체 목록**을 받아 표
`mfds_gmp_cert`(migration 142)에 멱등 적재한다.

★브리프에 올리지 않는다(대표 결정 11-11). 그래서 기존 `collect_mfds_gmp_cert.py`(Notion→핸드오프
경로, 30일 중복 창)는 쓰지 않고 이 스크립트가 Supabase 로 직행한다.
★저장 단계에서 자르지 않는다 — 원천이 준 칸 전부를 `raw` jsonb 에 그대로 담고, 주요 칸은 컬럼으로
복사만 한다(tests/test_no_storage_truncation.py).
★상한: 페이지 수를 totalCount 에서 계산하고, 다 못 받으면 exit 3(적재는 하되 시끄럽게).

행 정체성(cert_id): 원천 행 **전체 칸**의 정렬 JSON 해시. 같은 판정서가 갱신돼 유효기한이 바뀌면 새
행이 생기고 옛 행은 `last_seen_on` 이 멈춘다 — 그게 이력이다(지우지 않는다).

업체 연결: 적재 뒤 DB 함수 `grm_link_mfds_gmp_cert()`(142)를 부른다 — 사업자등록번호(BIZRNO) 우선,
없으면 업체명 원문 일치 → 법인격 표기만 접은 이름 일치. 모호하거나 번호가 어긋나면 연결하지 않고
`link_note` 에 사유를 남긴다(이 스크립트가 그 목록을 --unlinked-output 으로 쓴다).

모드:
  --probe    : 조회만. totalCount·원천 칸 합집합·칸별 채움 수·표본 3행을 출력(적재·연결 없음)
  --dry-run  : 전량 수집 + 기존 표와 비교(신규·사라진 행 수), 적재·연결 없음
  (기본)     : 전량 수집 → upsert → 연결 갱신 → 스냅샷 기록

exit: 0 정상 · 2 자격증명/수집 실패 · 3 전량 미수집(상한·부분 실패) · 1 적재 실패
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "grm-mfds-gmp-cert/v1"
ENDPOINT = (
    "https://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService"
    "/getDrugGmpStbltJgmtIssuStusInq"
)
DATASET_URL = "https://www.data.go.kr/data/15097207/openapi.do"
PAGE_SIZE = 100
# 원천이 아무리 커져도 이 이상은 비정상(무한 루프 방지). totalCount 로 실제 필요 페이지를 계산한다.
HARD_MAX_PAGES = 400

# ── 원천 행 → 저장 행 ─────────────────────────────────────────────────────────────

def _text(raw: dict[str, Any], key: str) -> str:
    v = raw.get(key)
    return "" if v is None else str(v).strip()


def parse_ymd(raw: str) -> str | None:
    raw = (raw or "").strip()
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", raw)
    if not m:
        return None
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


def cert_id(raw: dict[str, Any]) -> str:
    canon = json.dumps({k: raw[k] for k in sorted(raw)}, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return "gmpcert-" + hashlib.sha1(canon.encode("utf-8")).hexdigest()


def to_row(raw: dict[str, Any], snapshot_on: str) -> dict[str, Any]:
    return {
        "cert_id": cert_id(raw),
        "firm_name": _text(raw, "BSSH_NM"),
        "factory_addr": _text(raw, "FCTR_ADDR"),
        "product_kind": _text(raw, "KGMP_BGMP_NAME"),
        "dosage_group": _text(raw, "GMP_INGR_MM_GROUP_NAME"),
        "valid_until_raw": _text(raw, "VLD_PRD_YMD"),
        "valid_until": parse_ymd(_text(raw, "VLD_PRD_YMD")),
        "bizrno": _text(raw, "BIZRNO"),
        "raw": raw,
        "first_seen_on": snapshot_on,
        "last_seen_on": snapshot_on,
    }


# ── 수집 ─────────────────────────────────────────────────────────────────────────

def fetch_all(service_key: str, *, http_get=None, page_size: int = PAGE_SIZE,
              hard_max: int = HARD_MAX_PAGES) -> dict[str, Any]:
    """전 페이지를 받는다. 반환: items·total_count·pages·complete·errors."""
    if http_get is None:
        from grm_common import http_get_json as http_get  # 지연 import(테스트 무의존)
    from grm_common import datago_extract_items

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    total = None
    page = 1
    while page <= hard_max:
        params = {"serviceKey": service_key, "pageNo": page, "numOfRows": page_size,
                  "type": "json"}
        try:
            data = http_get(ENDPOINT, params=params)
            raw_items, _resp_page, _num_rows, total_count, status = datago_extract_items(data)
            if not status.startswith("00:"):
                raise RuntimeError(f"API status {status}")
        except Exception as e:  # noqa: BLE001 — 페이지 단위 실패는 기록하고 다음으로
            msg = str(e).replace(service_key, "***") if service_key else str(e)
            errors.append(f"page={page}: {msg}")
            page += 1
            if total is not None and (page - 1) * page_size >= total:
                break
            if total is None and len(errors) >= 3:
                break
            continue
        if total is None:
            total = int(total_count or 0)
        if not raw_items:
            break
        items.extend(r for r in raw_items if isinstance(r, dict))
        if page * page_size >= (total or 0):
            break
        page += 1
    complete = (total is not None and not errors and len(items) >= total)
    return {"items": items, "total_count": total, "pages": page, "complete": complete,
            "errors": errors}


def probe_report(items: list[dict[str, Any]], total: int | None) -> dict[str, Any]:
    keys: Counter[str] = Counter()
    filled: Counter[str] = Counter()
    for r in items:
        for k, v in r.items():
            keys[k] += 1
            if v not in (None, ""):
                filled[k] += 1
    return {"total_count": total, "fetched": len(items),
            "keys": {k: {"present": keys[k], "filled": filled[k]} for k in sorted(keys)},
            "samples": items[:3]}


# ── Supabase(PostgREST) ──────────────────────────────────────────────────────────

class Rest:
    def __init__(self, url: str, key: str):
        import requests
        self.r = requests
        self.base = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}",
                  "Content-Type": "application/json"}

    def get_all(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        step = 1000
        offset = 0
        while True:
            h = {**self.h, "Range-Unit": "items", "Range": f"{offset}-{offset + step - 1}"}
            resp = self.r.get(f"{self.base}/{path}", params=params, headers=h, timeout=60)
            resp.raise_for_status()
            batch = resp.json()
            out.extend(batch)
            if len(batch) < step:
                return out
            offset += step

    def rpc(self, name: str, body: dict[str, Any] | None = None) -> Any:
        resp = self.r.post(f"{self.base}/rpc/{name}", headers=self.h, json=body or {}, timeout=120)
        if resp.status_code >= 300:
            raise RuntimeError(f"rpc {name} {resp.status_code}: {resp.text}")
        return resp.json()

    def upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> None:
        h = {**self.h, "Prefer": "resolution=merge-duplicates,return=minimal"}
        for i in range(0, len(rows), 500):
            resp = self.r.post(f"{self.base}/{table}", params={"on_conflict": on_conflict},
                               headers=h, data=json.dumps(rows[i:i + 500], ensure_ascii=False)
                               .encode("utf-8"), timeout=120)
            if resp.status_code >= 300:
                raise RuntimeError(f"upsert {table} {resp.status_code}: {resp.text}")


# ── main ─────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서도 한글·기호 출력이 죽지 않게(test_cli_stdout_encoding).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output", default="mfds_gmp_cert_report.json")
    ap.add_argument("--unlinked-output", default="mfds_gmp_cert_unlinked.json")
    a = ap.parse_args(argv)

    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
    if not key:
        print("DATA_GO_KR_SERVICE_KEY 필요", file=sys.stderr)
        return 2
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    got = fetch_all(key)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "snapshot_on": today,
        "total_count": got["total_count"], "fetched": len(got["items"]),
        "complete": got["complete"], "errors": got["errors"],
    }
    if a.probe:
        report.update(probe_report(got["items"], got["total_count"]))
        Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({k: report[k] for k in ("total_count", "fetched", "complete", "errors",
                                                 "keys")}, ensure_ascii=False, indent=2))
        return 0 if got["items"] else 2
    if not got["items"]:
        Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
        return 2

    rows_by_id: dict[str, dict[str, Any]] = {}
    for raw in got["items"]:
        row = to_row(raw, today)
        rows_by_id.setdefault(row["cert_id"], row)
    rows = list(rows_by_id.values())
    report["distinct_rows"] = len(rows)
    report["exact_duplicate_source_rows"] = len(got["items"]) - len(rows)

    url, skey = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not (url and skey):
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 필요", file=sys.stderr)
        return 2
    rest = Rest(url, skey)
    have = {r["cert_id"]: r["last_seen_on"]
            for r in rest.get_all("mfds_gmp_cert", {"select": "cert_id,last_seen_on"})}
    new = [r for r in rows if r["cert_id"] not in have]
    gone = sorted(set(have) - {r["cert_id"] for r in rows})
    report.update({"existing_rows": len(have), "new_rows": len(new),
                   "not_in_this_listing": len(gone),
                   "source_firms": len({r["firm_name"] for r in rows}),
                   "source_bizrnos": len({r["bizrno"] for r in rows if r["bizrno"]})})

    rc = 0
    if not a.dry_run:
        try:
            # first_seen_on 은 기존 행이면 보존한다 → 두 갈래로 보낸다.
            old = [{k: v for k, v in r.items() if k != "first_seen_on"}
                   for r in rows if r["cert_id"] in have]
            rest.upsert("mfds_gmp_cert", new, "cert_id")
            rest.upsert("mfds_gmp_cert", old, "cert_id")
            link = rest.rpc("grm_link_mfds_gmp_cert")
            report["link"] = link
            unlinked = rest.get_all("mfds_gmp_cert", {
                "select": "cert_id,firm_name,bizrno,factory_addr,link_note,"
                          "candidate_firm_key,candidate_method,candidate_confidence,candidate_evidence",
                "firm_key": "is.null", "order": "link_note,firm_name"})
            Path(a.unlinked_output).write_text(
                json.dumps(unlinked, ensure_ascii=False, indent=2), "utf-8")
            rest.upsert("mfds_gmp_cert_snapshot", [{
                "snapshot_on": today, "total_count": got["total_count"],
                "fetched": len(got["items"]), "distinct_rows": len(rows),
                "new_rows": len(new), "complete": got["complete"],
                "linked_rows": link.get("linked_rows"), "unlinked_rows": link.get("unlinked_rows"),
                "report": report}], "snapshot_on")
        except Exception as e:  # noqa: BLE001
            report["append_error"] = str(e)
            rc = 1
    if rc == 0 and not got["complete"]:
        rc = 3
    Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
