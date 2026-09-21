#!/usr/bin/env python3
"""MFDS 허가정보 API 가 국내 카드의 제품군을 **몇 % 나 확신 있게 가르는지** 실측한다.

배경 (2026-09-21)
-----------------
제품군 분류기를 증거 기반으로 고친 뒤, 국내 카드 상당수가 배지를 잃었다. 되살릴
경로를 찾다가 MFDS 허가정보 API 활용신청이 승인돼 응답을 받게 됐다. 그런데 기대했던
`품목구분`(생물의약품/첨단바이오)은 **응답에 없었다** — `INDUTY`/`INDUTY_TYPE` 은
업종(예: "의약품 및 의약외품 수입업")이라 항체의약품도 저분자도 똑같이 '의약품' 이다.
(1차 프로브가 이걸 "일치"로 셌다. 표본당이 아니라 오퍼레이션당 센 집계 결함이었다.)

대신 응답 44개 필드에 **`ATC_CODE`** 와 **주성분명**이 있다. 허셉틴 = `L01FD01`
(L01F = 단클론항체·항체약물접합체), 주성분 `[M090706]트라스투주맙`. ATC 는 국제 표준이고
한국어 주성분명의 `-맙`·`-셉트` 접미사와 교차검증도 된다.

★그러나 "쓸 수 있다"와 "쓸 만하다"는 다르다. **만들기 전에 잰다.**
  483 지적사항 본문 되살리기는 실효 12%·거짓 친구가 진짜 신호의 2.3배로 나와 접었다.
  같은 규율로, 실제 발행 카드의 품목코드를 전수로 돌려 도달률을 먼저 본다.

  python measure_mfds_modality_signal.py            # 발행 카드 실 품목코드 120건
  python measure_mfds_modality_signal.py --limit 20 # 빠른 확인

읽기 전용이다 — 저장소·DB 에 아무것도 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
import time

from grm_common import http_get_json, mask_service_key

PERMIT_API = ("https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService08"
              "/getDrugPrdtPrmsnDtlInq08")

# ★발행 브리프(14주)의 국내 회수·행정처분 카드 114장에서 뽑은 **실제** 품목기준코드.
#   raw_signals 의 ITEM_SEQ(회수 100%·행정처분 66% 보유, 쉼표 다중값 전개) 기준.
#   합성 표본이 아니라 우리가 실제로 발행한 모집단이어야 도달률이 의미를 갖는다.
PUBLISHED_ITEM_SEQS = (
    "197500023,198501421,198601284,198601285,198601399,198701689,198901887,198902058,"
    "198902280,199000353,199302514,199302515,199800895,199900221,199902738,199903060,"
    "200003464,200102527,200102769,200103035,200202118,200210770,200300760,200301993,"
    "200302817,200308019,200402546,200403250,200404044,200404345,200407515,200502107,"
    "200502450,200502455,200502687,200504474,200504564,200504568,200504683,200605623,"
    "200704133,200704144,200707219,200709256,200712587,200803561,200803925,200805388,"
    "200901891,200905098,200907781,200908060,201000241,201102762,201104266,201105333,"
    "201109893,201303263,201306043,201307006,201309541,201400174,201400370,201402890,"
    "201403417,201403819,201500053,201501945,201502273,201502760,201502765,201502928,"
    "201503443,201506003,201506800,201601240,201602732,201605514,201606023,201606346,"
    "201708554,201800874,201803263,201803896,201900438,201902030,201904854,201905304,"
    "201907913,201907965,202000204,202001275,202002928,202007078,202101223,202101337,"
    "202102323,202102839,202103498,202103684,202104294,202105344,202106244,202106915,"
    "202202798,202203924,202204621,202300393,202301320,202302012,202302822,202302823,"
    "202400383,202400388,202401063,202401612,202402280,202500644,202502551,202600833"
).split(",")


# ── ATC → 제품군 ─────────────────────────────────────────────────────────────
# ★접두 매칭. ATC 는 WHO 가 관리하는 국제 표준이고 이 묶음들은 **제형이 아니라 물질
#   성격**으로 묶인다 — 이번 수리가 경계한 '축 혼동'이 없다.
ATC_BIOLOGIC_PREFIXES = (
    "L01F",    # 단클론항체·항체약물접합체
    "L03A",    # 면역자극제(인터페론·필그라스팀·인터루킨)
    "L04AC", "L04AB", "L04AG",   # 면역억제 mAb·TNF 억제제
    "J07",     # 백신
    "B02BD",   # 혈액응고인자
    "B06AC",   # C1 억제제
    "A10A",    # 인슐린
    "H01A", "H01B", "H01C",      # 뇌하수체 호르몬(성장호르몬·고나도트로핀)
    "V09",     # 방사성 진단 — 제외 대상 판정용(아래 OTHER 에서 다시 본다)
)
ATC_OTHER_PREFIXES = ("V08", "V09")          # 조영제·방사성의약품 — 제품군 축 밖

# 한국어 주성분명의 생물 어간 — ATC 가 없을 때의 교차 신호.
# ★'알파'·'베타'·'페그' 같은 수식어는 뺐다: '알파칼시돌'(비타민D 유도체)처럼 저분자에도
#   붙어, 축이 다른 말을 제품군 신호로 쓰게 된다(이번 수리가 경계한 바로 그 실수).
KO_BIOLOGIC_STRONG = re.compile(
    r"(맙|셉트|인슐린|인터페론|백신|톡소이드|면역글로불린|"
    r"에포에틴|필그라스팀|소마트로핀|보툴리눔|혈장분획|응고인자)")


def _fetch(item_seq: str, service_key: str) -> dict | None:
    params = {"serviceKey": service_key, "type": "json", "numOfRows": 1,
              "pageNo": 1, "item_seq": item_seq}
    try:
        data = http_get_json(PERMIT_API, params=params, timeout=30, retries=1)
    except Exception as e:                                    # noqa: BLE001
        print(f"  {item_seq}: 조회 실패 — {mask_service_key(str(e))}", file=sys.stderr)
        return None
    body = (data.get("body") or {}) if isinstance(data, dict) else {}
    items = body.get("items") or []
    if isinstance(items, dict):
        items = [items]
    return items[0] if items else None


def _verdict(row: dict) -> tuple[str, str]:
    """(판정, 근거) — 판정은 Biologic / Chemical / Other / '' (근거 없음)."""
    atc = (row.get("ATC_CODE") or "").strip().upper()
    ingr = (row.get("MAIN_ITEM_INGR") or row.get("INGR_NAME") or "")
    if atc:
        if any(atc.startswith(p) for p in ATC_OTHER_PREFIXES):
            return "Other", f"ATC {atc} (조영제·방사성)"
        if any(atc.startswith(p) for p in ATC_BIOLOGIC_PREFIXES):
            return "Biologic", f"ATC {atc}"
    if ingr and KO_BIOLOGIC_STRONG.search(ingr):
        return "Biologic", f"주성분 {ingr[:30]}"
    if atc:
        # ATC 가 있는데 생물 묶음이 아니다 = WHO 가 분류한 저분자 계열.
        return "Chemical", f"ATC {atc}"
    return "", "근거 없음(ATC·주성분 모두 미해당)"


def main(argv: list[str] | None = None) -> int:
    import os
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N 건만(빠른 확인).")
    ap.add_argument("--delay", type=float, default=0.15, help="요청 간 대기(초).")
    args = ap.parse_args(argv)

    service_key = (os.environ.get("DATA_GO_KR_SERVICE_KEY")
                   or os.environ.get("DATA_GO_KR_KEY") or "").strip()
    if not service_key:
        print("DATA_GO_KR_SERVICE_KEY 환경변수 필요", file=sys.stderr)
        return 2

    seqs = PUBLISHED_ITEM_SEQS[:args.limit] if args.limit else PUBLISHED_ITEM_SEQS
    print(f"발행 카드 실 품목코드 {len(seqs)}건 조회\n")

    verdicts = collections.Counter()
    no_row = 0
    has_atc = 0
    examples: dict[str, list[str]] = collections.defaultdict(list)

    for i, seq in enumerate(seqs, 1):
        row = _fetch(seq, service_key)
        if args.delay:
            time.sleep(args.delay)
        if row is None:
            no_row += 1
            verdicts["(조회실패·미존재)"] += 1
            continue
        if (row.get("ATC_CODE") or "").strip():
            has_atc += 1
        v, why = _verdict(row)
        verdicts[v or "(근거 없음)"] += 1
        name = (row.get("ITEM_NAME") or "")[:34]
        if len(examples[v or ""]) < 4:
            examples[v or ""].append(f"{name} — {why}")
        if i % 25 == 0:
            print(f"  … {i}/{len(seqs)}")

    total = len(seqs)
    decided = sum(v for k, v in verdicts.items()
                  if k in ("Biologic", "Chemical", "Other"))
    print(f"\n{'='*58}\n판정 도달률")
    for k, v in verdicts.most_common():
        print(f"  {v:>4}  {k:<22} {v*100//max(total,1)}%")
    print(f"\n  ATC_CODE 보유 {has_atc}/{total} ({has_atc*100//max(total,1)}%)")
    print(f"  ★판정 도달 {decided}/{total} ({decided*100//max(total,1)}%)")
    print(f"  조회 실패·미존재 {no_row}건")
    print("\n예시")
    for k in ("Biologic", "Chemical", "Other", ""):
        for e in examples.get(k, []):
            print(f"  [{k or '근거없음':<8}] {e}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
