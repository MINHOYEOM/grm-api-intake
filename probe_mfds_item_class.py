#!/usr/bin/env python3
"""MFDS 의약품 제품 허가정보 API 가 **품목구분**(생물의약품/첨단바이오/…)을 내주는지 확인한다.

왜 필요한가
-----------
2026-09-21 제품군 분류기 수리 이후, 근거가 없는 항목은 배지를 달지 않는다. 국내 카드
(회수·행정처분)는 `ITEM_SEQ`(품목기준코드)를 이미 보유하고 있고 — 회수 987/987(100%),
행정처분 105/160(66%) — 의약품안전나라 화면에는 그 코드로 조회하면 **품목구분** 이
`의약품 / 의약외품 / 생물의약품 / 마약류 / 첨단바이오 / 한약(생약)제제등` 으로 찍힌다.
이건 약효분류번호(치료 축, 항체를 못 가른다)와 달리 **원료 성격 축 그 자체**다.

★남은 미확인 1건: 그 값이 **data.go.kr API 응답에도 있는가**. 있으면 API 로 가고,
없으면 화면 조회 경로를 설계해야 한다. 스펙 문서를 추측하지 말고 실제로 불러서 본다.

이 스크립트는 **읽기 전용**이다. 저장소·DB 에 아무것도 쓰지 않는다.

  python probe_mfds_item_class.py                       # 기본 표본(알려진 정답 포함)
  python probe_mfds_item_class.py --item-seq 200511046  # 특정 품목

기본 표본은 **정답을 아는 대조군**이다(의약품안전나라 화면 실측):
  200511046 허셉틴주150밀리그램(트라스투주맙) → 생물의약품
  196700015 하트만덱스액(수액)               → 의약품
정답을 모르는 품목만 찍으면 "필드가 있다"는 알 수 있어도 "값이 맞다"는 모른다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from grm_common import http_get_json, mask_service_key

# 식품의약품안전처_의약품 제품 허가정보 (data.go.kr 15095677, 제공기관 1471000).
# 기존 MFDS 수집기들과 같은 제공기관·같은 서비스키를 쓴다(신규 키 발급 불필요).
PERMIT_API_BASE = "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService06"
OPERATIONS = ("getDrugPrdtPrmsnDtlInq05", "getDrugPrdtPrmsnInq05")

# (ITEM_SEQ, 제품명, 화면 실측 품목구분) — 대조군.
CONTROL_SAMPLES = (
    ("200511046", "허셉틴주150밀리그램(트라스투주맙)", "생물의약품"),
    ("196700015", "하트만덱스액", "의약품"),
)

# 응답에서 품목구분으로 보이는 필드 후보. 이름을 모르므로 **값으로 찾는다** —
# 어느 필드든 값이 아래 어휘면 그게 품목구분이다(필드명 추측 금지).
CLASS_VOCAB = ("생물의약품", "첨단바이오", "의약외품", "한약(생약)제제",
               "마약류", "의약품")


def _probe_one(op: str, item_seq: str, service_key: str) -> dict:
    url = f"{PERMIT_API_BASE}/{op}"
    params = {
        "serviceKey": service_key,
        "type": "json",
        "numOfRows": 3,
        "pageNo": 1,
        "item_seq": item_seq,
    }
    try:
        data = http_get_json(url, params=params, timeout=30, retries=1)
    except Exception as e:                                    # noqa: BLE001
        return {"op": op, "error": f"{type(e).__name__}: {mask_service_key(str(e))}"}
    body = ((data.get("body") or {}) if isinstance(data, dict) else {})
    items = body.get("items") or []
    if isinstance(items, dict):
        items = [items]
    if not items:
        # 표준 data.go.kr 래퍼(response.body.items)도 시도
        resp = (data.get("response") or {}) if isinstance(data, dict) else {}
        items = ((resp.get("body") or {}).get("items")) or []
        if isinstance(items, dict):
            items = items.get("item") or []
        if isinstance(items, dict):
            items = [items]
    return {"op": op, "count": len(items), "row": items[0] if items else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item-seq", action="append", default=[],
                    help="조회할 품목기준코드(반복 가능). 생략 시 대조군 표본.")
    args = ap.parse_args(argv)

    service_key = (os.environ.get("DATA_GO_KR_SERVICE_KEY")
                   or os.environ.get("DATA_GO_KR_KEY") or "").strip()
    if not service_key:
        print("DATA_GO_KR_SERVICE_KEY 환경변수 필요", file=sys.stderr)
        return 2

    samples = ([(s, "", "") for s in args.item_seq] if args.item_seq
               else list(CONTROL_SAMPLES))

    verdict_field: str | None = None
    ok = 0
    for item_seq, name, expected in samples:
        print(f"\n=== ITEM_SEQ {item_seq} {name or ''}".rstrip())
        if expected:
            print(f"    화면 실측 품목구분: {expected}")
        for op in OPERATIONS:
            r = _probe_one(op, item_seq, service_key)
            if r.get("error"):
                print(f"  [{op}] 실패 — {r['error']}")
                continue
            row = r.get("row")
            print(f"  [{op}] rows={r.get('count')}")
            if not row:
                continue
            print(f"    필드 {len(row)}개: {sorted(row.keys())}")
            # ★값으로 품목구분 필드를 찾는다.
            hits = {k: v for k, v in row.items()
                    if isinstance(v, str) and v.strip() in CLASS_VOCAB}
            if hits:
                print(f"    ★품목구분으로 보이는 필드: {hits}")
                verdict_field = verdict_field or sorted(hits)[0]
                if expected and expected in hits.values():
                    ok += 1
                    print("    → 대조군 정답과 일치")
                elif expected:
                    print(f"    → ⚠️ 대조군 정답({expected})과 불일치")
            else:
                print("    품목구분 어휘를 가진 필드 없음")
                # 진단용 — 값이 짧은 필드만 보여 준다(본문 필드는 수십 KB).
                short = {k: v for k, v in row.items()
                         if isinstance(v, str) and 0 < len(v) <= 30}
                print(f"    (짧은 값 필드: {json.dumps(short, ensure_ascii=False)[:600]})")

    print()
    if verdict_field and (not any(e for _, _, e in samples) or ok == len(
            [s for s in samples if s[2]])):
        print(f"★판정: API 가 품목구분을 내준다 — 필드 '{verdict_field}'. "
              f"대조군 {ok}건 일치. API 경로로 진행 가능.")
        return 0
    if verdict_field:
        print(f"⚠️ 필드 '{verdict_field}' 는 찾았으나 대조군이 어긋난다 — 값 의미 재확인 필요.")
        return 1
    print("★판정: API 응답에 품목구분이 없다 — 의약품안전나라 화면 조회 경로를 설계해야 한다.")
    return 1


if __name__ == "__main__":
    # 좁은 콘솔 인코딩(cp949) 가드 — 리포트가 한글·★ 를 찍는다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
