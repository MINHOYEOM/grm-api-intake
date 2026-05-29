#!/usr/bin/env python3
"""범용 data.go.kr(식약처 1471000) 오픈API 응답 스키마 진단 — 표준 라이브러리만 사용.

행정처분(15058457)·GMP 적합판정서(15097207) 등 data.go.kr 서비스의
오퍼레이션명/필드/페이징/날짜형식/인증키종류를 1회 확인해 collector 매핑을 확정한다.
(probe_recall.py 의 범용 버전)

사용법 (로컬/Codex, serviceKey 보유 환경):
    set DATA_GO_KR_SERVICE_KEY=<Decoding 키>          (Windows)
    export DATA_GO_KR_SERVICE_KEY=...                 (bash)

    # base + 오퍼레이션 후보(콤마구분)  → 후보 자동 탐색
    py -3 probe_datago.py "<base_url>" "op1,op2,op3"

    # 또는 전체 요청주소(오퍼레이션 포함) 1개
    py -3 probe_datago.py "<full_endpoint_url>"

확정된 (오퍼레이션, 인증키종류, json/xml) + 첫 항목 키/샘플 출력을 그대로 공유.
serviceKey 는 출력에서 마스킹됨.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def _mask(url: str) -> str:
    return re.sub(r"(serviceKey=)[^&]+", r"\1***REDACTED***", url)


def fetch(url: str) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "GRM-datago-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get("Content-Type", "") if e.headers else ""), e.read()


def build(endpoint: str, key: str, *, quote_key: bool, resp_type: str) -> str:
    params = {"pageNo": "1", "numOfRows": "5", "type": resp_type}
    key_val = urllib.parse.quote(key, safe="") if quote_key else key
    sep = "&" if "?" in endpoint else "?"
    return f"{endpoint}{sep}serviceKey={key_val}&{urllib.parse.urlencode(params)}"


def summarize(status: int, ctype: str, raw: bytes) -> bool:
    print(f"  HTTP {status}  Content-Type: {ctype}  bytes={len(raw)}")
    head = raw[:700]
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        text = head.decode("euc-kr", errors="replace")
    print("  --- head ---\n  " + text.replace("\n", "\n  ") + "\n  ------------")
    if "json" in ctype.lower() or raw.lstrip()[:1] in (b"{", b"["):
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))

            def find_items(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if k.lower() in ("items", "item", "row") and v:
                            return v
                        r = find_items(v)
                        if r:
                            return r
                elif isinstance(o, list) and o:
                    return o
                return None

            items = find_items(data)
            if isinstance(items, dict):
                items = [items]
            if items:
                print(f"  >> 항목 {len(items)}건, 첫 항목 키: {list(items[0].keys())}")
                print(f"  >> 첫 항목 샘플: {json.dumps(items[0], ensure_ascii=False)[:600]}")
                return True
            print("  >> item 배열 못 찾음 — head 로 resultCode/resultMsg 확인")
        except Exception as e:  # noqa: BLE001
            print(f"  >> JSON 파싱 실패: {e}")
    return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if len(sys.argv) < 2 or not sys.argv[1].startswith("http"):
        print('usage: py -3 probe_datago.py "<base_or_full_url>" ["op1,op2,..."]  (serviceKey=env DATA_GO_KR_SERVICE_KEY)')
        return 2
    base = sys.argv[1].strip().rstrip("/")
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key:
        print("DATA_GO_KR_SERVICE_KEY 환경변수 필요")
        return 2

    if "/get" in base:                       # 이미 오퍼레이션 포함
        endpoints = [base]
    elif len(sys.argv) > 2 and sys.argv[2].strip():
        endpoints = [f"{base}/{op.strip()}" for op in sys.argv[2].split(",") if op.strip()]
    else:
        print("오퍼레이션 후보(argv[2], 콤마구분)가 필요하거나, 전체 요청주소를 argv[1]로 넘기세요.")
        return 2

    combos = [
        ("Decoding(quote)+json", True, "json"),
        ("Encoding(raw)+json", False, "json"),
        ("Decoding(quote)+xml", True, "xml"),
        ("Encoding(raw)+xml", False, "xml"),
    ]
    print(f"base={base}  endpoints={len(endpoints)}  combos={len(combos)}\n")
    for endpoint in endpoints:
        for label, quote_key, resp_type in combos:
            url = build(endpoint, key, quote_key=quote_key, resp_type=resp_type)
            print(f"=== {endpoint.rsplit('/', 1)[-1]} | {label} ===")
            print(f"  URL: {_mask(url)}")
            try:
                ok = summarize(*fetch(url))
            except Exception as e:  # noqa: BLE001
                print(f"  [ERR] {e}")
                ok = False
            print()
            if ok:
                print(f"✅ 채택: endpoint='{endpoint}', 인증키/포맷='{label}'")
                print("   첫 항목 키 + 샘플을 공유 → collector 매핑 확정.")
                return 0
    print("※ 정상 항목 못 찾음. head의 resultCode/resultMsg 확인, 또는 활용신청 상세>참고문서(Swagger)에서")
    print("  목록조회 오퍼레이션명을 확인해 argv[1]에 전체 URL로 넘길 것.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
