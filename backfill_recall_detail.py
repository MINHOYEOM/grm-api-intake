#!/usr/bin/env python3
"""[회수 결정론 상세 소급 2026-08-25] `#786` 이전에 발행된 회수 카드에 결정론 상세를
`raw_signals` 의 원천 레코드로부터 소급 병합하는 1회성 CLI.

배경 — `#786` 이 회수 3종(openfda-recall·recall-quality·hc-recall)에 결정론 상세를 배선했지만
**다음 조립분부터** 적용된다. 발행 브리프 JSON 에는 `raw` 가 없어 이미 나간 카드는 그대로다.

★그런데 원천은 `raw_signals` 에 전건 남아 있다(2026-08-25 실측: OpenFDA 120/120 ·
MFDS recall-quality 959/959 · Health Canada 19/19 이 상세 생성에 쓰는 필드를 보유). 즉
재수집·재-fetch 없이 **DB 조회 + 결정론 변환**만으로 소급된다 — LLM 0, 생성 0, 환각 0.

변환은 **운영 producer 를 그대로 호출**한다(`card_scaffold._detail_openfda_recall` 등) —
별도 구현을 두지 않으므로 라이브와 갈라질 자리가 없다. 병합 대상 필드도 `deterministic_detail`
한 층뿐이다(다른 슬롯 무변형).

★병합 대표 카드 주의 — `merged_count > 1` 인 카드의 상세는 **대표 레코드 1건**의 사실이다
(로트·수량·타임라인이 전부 그렇다). 렌더러가 카드 최상위 `merged_count` 를 읽어 "대표 품목
1건 기준"을 화면에 적으므로 여기서 따로 표시할 것은 없다 — 다만 대표의 `document_id` 로
원천을 조회해야 짝이 맞는다(멤버 raw 를 섞으면 안 된다).

입력은 `raw_signals` 를 미리 떠 둔 JSON 이다(이 CLI 는 DB 를 직접 치지 않는다 — 자격증명·
네트워크 없이 재현·검증되도록):
    {document_id: {<raw_payload 그대로>}}

사용:
    python backfill_recall_detail.py --brief web/data/briefs/brief_web_2026_07_20.json \
        --raw <경로>/recall_raw.json [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import card_scaffold as cs
from assemble_publish_brief import _write_json

# card_type → 상세 producer. 레지스트리에서 파생하지 않고 명시하는 이유: 이 소급의 대상은
# "#786 이전 발행분"으로 고정돼 있고, 나중에 다른 유형에 상세가 배선돼도 이 1회성 CLI 의
# 대상이 늘어나면 안 되기 때문이다(대상 확대는 의도적 결정이어야 한다).
_PRODUCERS = {
    "Recall": cs._detail_openfda_recall,
    "회수·판매중지": cs._detail_recall_quality,
    "Recall(HC)": cs._detail_hc_recall,
    # [MHRA 소급 2026-08-27] #806 이 회수 4종의 마지막 하나를 배선하면서 이 CLI 의 대상이
    # 늘었다 — 위 주석이 요구하는 "의도적 결정"이 여기다. 다른 3종과 달리 원천이 DB 에 없어
    # (본문 필드는 #806 이후 수집분부터 생긴다) gov.uk Content API 로 따로 확보해 먹인다.
    "Recall(UK)": cs._detail_mhra_recall,
}

# ★card_type 이 "규제 소식"인 MHRA 회수 2장 — 분류 결함이 아니라 **시점 산물**이다.
# `mhra-recall` kind 는 #399(2026-07-22)에 신설됐고, 2026-07-20 브리프의 이 두 장은 그보다
# 이틀 앞서 발행돼 당시 규칙대로 rss-news → "규제 소식"으로 나갔다. 원천은 동일한 gov.uk
# 의약품 회수 공고이므로 상세를 싣는 것이 맞다. 다만 "규제 소식"을 _PRODUCERS 에 넣으면
# 무관한 뉴스 카드까지 회수 producer 를 타므로, **문서번호로 못박아** 대상을 고정한다
# (1회성 소급이라 목록이 낡을 자리가 없다 — 새 발행분은 전부 Recall(UK) 로 나온다).
_PRE_399_MHRA_RECALL_IDS = {"93f98fe39f5c", "625fc2215ae3"}


def build_details(brief: dict[str, Any], raws: dict[str, Any]) -> tuple[dict, list, list]:
    """카드별 결정론 상세 산출. 반환 = (id→detail, 원천없음, 상세없음).

    이미 `deterministic_detail` 이 있는 카드는 건드리지 않는다(덮어쓰기는 이 도구의 일이 아니다).
    원천이 없거나 producer 가 None 을 돌려주면 그 카드는 그대로 둔다 — 빈 블록을 만들지 않는다."""
    details: dict[str, Any] = {}
    no_raw: list[str] = []
    no_detail: list[str] = []
    for card in (brief.get("cards") or []):
        if not isinstance(card, dict):
            continue
        doc_id = card.get("id")
        fn = _PRODUCERS.get(card.get("card_type"))
        if fn is None and doc_id in _PRE_399_MHRA_RECALL_IDS:
            fn = cs._detail_mhra_recall
        if fn is None or card.get("deterministic_detail"):
            continue
        raw = raws.get(doc_id)
        if not isinstance(raw, dict):
            no_raw.append(doc_id)
            continue
        detail = fn({}, raw)
        if not detail:
            no_detail.append(doc_id)
            continue
        details[doc_id] = detail
    return details, no_raw, no_detail


def apply_details(brief: dict[str, Any], details: dict[str, Any]) -> int:
    """상세를 카드에 얹는다. 키 위치는 `checks` 바로 뒤 — 운영 스캐폴드의 키 순서와 같고,
    발행 JSON 의 카드 키 정렬 관례(알파벳순)도 만족해 diff 가 추가 블록에만 국한된다."""
    applied = 0
    for i, card in enumerate(brief.get("cards") or []):
        if not isinstance(card, dict):
            continue
        detail = details.get(card.get("id"))
        if not detail:
            continue
        rebuilt: dict[str, Any] = {}
        for key, value in card.items():
            rebuilt[key] = value
            if key == "checks":
                rebuilt["deterministic_detail"] = detail
        if "deterministic_detail" not in rebuilt:      # checks 가 없는 카드(방어)
            rebuilt["deterministic_detail"] = detail
        brief["cards"][i] = rebuilt
        applied += 1
    return applied


def main(argv: "list[str] | None" = None) -> int:
    # [cp949 가드] 파이프 stdout 은 Windows 기본 코드페이지로 인코딩된다 — 리포트 문구에
    # em-dash 하나만 섞여도 UnicodeEncodeError 로 죽고, --apply 의 기록이 출력 실패에 끌려
    # 유실된다. 전수 가드(tests/test_cli_stdout_encoding.py)가 이 관용구 부재를 잡는다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        description="회수 카드 결정론 상세 소급 병합(raw_signals 원천 → 발행 브리프)")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--raw", required=True, help="{document_id: raw_payload} JSON")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    with open(args.brief, encoding="utf-8") as fh:
        brief = json.load(fh)
    with open(args.raw, encoding="utf-8") as fh:
        raws = json.load(fh)

    details, no_raw, no_detail = build_details(brief, raws)
    total = sum(1 for c in (brief.get("cards") or [])
                if isinstance(c, dict) and c.get("card_type") in _PRODUCERS)
    if not details:
        print(f"[FAIL] 병합 대상 0건(회수 카드 {total} · 원천없음 {len(no_raw)} · "
              f"상세없음 {len(no_detail)})")
        return 1
    applied = apply_details(brief, details)
    print(f"회수 카드 {total} · 상세 생성 {len(details)} · 병합 {applied} · "
          f"원천없음 {len(no_raw)} · 상세없음 {len(no_detail)}")
    if no_raw:
        print(f"   원천없음: {no_raw[:8]}{' ...' if len(no_raw) > 8 else ''}")
    if no_detail:
        print(f"   상세없음: {no_detail[:8]}{' ...' if len(no_detail) > 8 else ''}")
    if not args.apply:
        print("(dry-run — 기록하지 않았다. 반영하려면 --apply)")
        return 0
    _write_json(args.brief, brief)
    print(f"기록 완료: {args.brief}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
