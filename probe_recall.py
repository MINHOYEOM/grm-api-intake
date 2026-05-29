#!/usr/bin/env python3
"""data.go.kr 의약품 회수·판매중지(서비스 15059114) 응답 스키마 1회 진단 — 표준 라이브러리만 사용.

목적: 실제 응답의 (a) 항목 필드명, (b) 페이징 구조, (c) 날짜 형식,
      (d) Encoding/Decoding 인증키 중 어느 쪽이 동작하는지를 확인해
      Phase2c recall-quality collector 의 매핑을 확정한다.

End Point (사용자 확인 2026-05-29): https://apis.data.go.kr/1471000/MdcinRtrvlSleStpgeInfoService04
목록조회 오퍼레이션명은 Swagger 미확인이라 후보 자동 탐색(CANDIDATE_OPS).

사용법 (repo 폴더, 로컬/Codex 환경에서):
    # 1) serviceKey 를 환경변수로 (Decoding 키 권장)
    set DATA_GO_KR_SERVICE_KEY=발급받은_키        (Windows)
    export DATA_GO_KR_SERVICE_KEY=...             (bash)

    # 2) 그냥 실행 — 엔드포인트는 내장됨
    py -3 probe_recall.py

    # (옵션) 정확한 전체 요청주소를 알면 argv[1]로 덮어쓰기:
    py -3 probe_recall.py "https://apis.data.go.kr/1471000/MdcinRtrvlSleStpgeInfoService04/<오퍼레이션명>"

주의:
- 후보 오퍼레이션이 모두 실패하면, data.go.kr 15059114 "활용신청 상세 / 참고문서(Swagger)"의
  '요청주소'에서 목록조회 오퍼레이션명을 확인해 argv[1]로 전체 URL을 넘길 것.
- serviceKey 는 출력에서 마스킹된다. 출력 전체를 그대로 붙여넣어 공유.
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


# data.go.kr 15059114 서비스 기본 End Point (사용자 확인, 2026-05-29)
DEFAULT_BASE = "https://apis.data.go.kr/1471000/MdcinRtrvlSleStpgeInfoService04"
# 목록조회 오퍼레이션명 후보 (Swagger 미확인 → 자동 탐색). 정상 응답 나오는 것을 채택.
CANDIDATE_OPS = [
    # Official Swagger path on data.go.kr (15059114, 2026-05-29)
    "getMdcinRtrvlSleStpgelList03",
    "getMdcinRtrvlSleStpgeItem03",
    "getMdcinRtrvlSleStpgelEtcList02",
    "getMdcinRtrvlSleStpgeEtcItem03",
    "getMdcinRtrvlSleStpgeInfoList04",
    "getMdcinRtrvlSleStpgeInfoInq04",
    "getMdcinRtrvlSleStpgeInfoList",
    "getMdcinRtrvlSleStpgeInfoInq04List",
]


def fetch(url: str) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "GRM-recall-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", e.read()


def build(endpoint: str, key: str, *, quote_key: bool, resp_type: str) -> str:
    # data.go.kr 표준 파라미터 + serviceKey
    base_params = {"pageNo": "1", "numOfRows": "5", "type": resp_type}
    qs = urllib.parse.urlencode(base_params)
    key_val = urllib.parse.quote(key, safe="") if quote_key else key
    sep = "&" if "?" in endpoint else "?"
    return f"{endpoint}{sep}serviceKey={key_val}&{qs}"


def summarize(status: int, ctype: str, raw: bytes) -> bool:
    """응답 요약 출력. JSON 항목을 찾으면 True(성공) 반환."""
    print(f"  HTTP {status}  Content-Type: {ctype}  bytes={len(raw)}")
    head = raw[:700]
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        text = head.decode("euc-kr", errors="replace")
    print("  --- head ---")
    print("  " + text.replace("\n", "\n  "))
    print("  ------------")
    # JSON 이면 item 필드 추출 시도
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
                print(f"  >> 첫 항목 샘플: {json.dumps(items[0], ensure_ascii=False)[:500]}")
                return True
            print("  >> item 배열을 못 찾음 — head 로 구조 확인 필요")
        except Exception as e:  # noqa: BLE001
            print(f"  >> JSON 파싱 실패: {e}")
    return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    # base 엔드포인트: argv[1]로 덮어쓰기 가능, 없으면 DEFAULT_BASE
    base = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].startswith("http") else DEFAULT_BASE
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip() or (sys.argv[2].strip() if len(sys.argv) > 2 else "")
    if not key:
        print("DATA_GO_KR_SERVICE_KEY 환경변수(또는 argv[2])에 serviceKey 필요")
        return 2

    # base가 이미 오퍼레이션까지 포함하면 그대로, 아니면 후보 오퍼레이션 자동 탐색
    if "/get" in base:
        endpoints = [base]
    else:
        endpoints = [f"{base.rstrip('/')}/{op}" for op in CANDIDATE_OPS]

    # 4조합: {Decoding(quote) / Encoding(raw)} x {json / xml}
    combos = [
        ("Decoding(quote)+json", True, "json"),
        ("Encoding(raw)+json", False, "json"),
        ("Decoding(quote)+xml", True, "xml"),
        ("Encoding(raw)+xml", False, "xml"),
    ]
    print(f"base={base}\n후보 endpoints={len(endpoints)}, combos={len(combos)}\n")
    for endpoint in endpoints:
        for label, quote_key, resp_type in combos:
            url = build(endpoint, key, quote_key=quote_key, resp_type=resp_type)
            print(f"=== {endpoint.rsplit('/', 1)[-1]} | {label} ===")
            print(f"  URL: {_mask(url)}")
            try:
                status, ctype, raw = fetch(url)
                ok = summarize(status, ctype, raw)
            except Exception as e:  # noqa: BLE001
                print(f"  [ERR] {e}")
                ok = False
            print()
            if ok:
                print(f"✅ 채택 후보: endpoint='{endpoint}', 인증키/포맷='{label}'")
                print("   이 조합의 첫 항목 키 + 샘플을 그대로 공유해 주세요 → collector 매핑 확정.")
                return 0
    print("※ 정상 항목을 못 찾음. 위 head 출력(특히 resultCode/resultMsg)으로 키 종류/오퍼레이션명 확인 필요.")
    print("  data.go.kr 15059114 '활용신청 상세 > 참고문서(Swagger)'에서 목록조회 오퍼레이션명을 확인해 argv[1]로 전체 URL을 넘겨주세요.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
