#!/usr/bin/env python3
"""6/26 실데이터 web-card(`grm-web-card/v1`) 빈슬롯 브리프 생성기 (1회용·발행 경로).

provenance: `handoff_rows_2026_06_26.json`(routine-handoff::2026-06-26, Notion page
38a3142f-dc11-816f-8f9a-f568f9a49d0c). 윈도우 2026-06-19~2026-06-26 핸드오프 62행 중
**렌더 카드 27개**(Tier 2/3 with scaffold; Tier1 33행·merged 2행 제외)의 동결 scaffold
markdown 을 파싱해 **빈 슬롯** `grm-web-card/v1` 브리프 JSON 을 만든다. 산문 슬롯
(title_issue·summary·key_facts·implication·checks·번역·tldr)은 **빈 채로** 두고,
`inject_slots.py` 가 v16 LLM 델타(`delta_2026-06-26.json`)를 주입해 완성한다.

왜 파서인가(P1 결정 2026-06-24, 06-22 빌더와 동일 근거): handoff 에 row/raw 는 있으나
실 producer(`to_web_card`)는 발행 당시 단일 산물(scaffold markdown)에서 사실 셀을 글자
단위로 전사하는 1회성 마이그레이션 경로가 verbatim 골든에 가장 안전하다(PL18 의미 보존).
정렬·그룹·render_order 는 재구현하지 않고 `compute_render_plan()`(단일원천)에 stub 카드를
넣어 받는다(06-22 빌더와 동일 stub 경로).

06-22 빌더 대비 확장점:
  - `_kind_for` 신규 매핑 2종: `GMP실사`→`gmp-inspection`(MFDS), `Recall`(agency=FDA)
    →`openfda-recall`. (card_scaffold.py 가 이미 인지하는 kind — 신규 kind 도입 아님.)
  - 그 외 회수·판매중지/행정처분/규제 소식/FDA 483/Warning Letter 는 06-22 와 동일 재사용.
  - 인용(quotes): EA FDA recall 9장은 `**원문 및 번역**` + `> 원문` + `{{W4}}`(번역 슬롯).
    EA MFDS(GMP/행정/회수) 카드는 `**원문**` + `> 원문`(KO, translation=null). 06-22 파서의
    원문/번역 블록 로직이 그대로 동작(원문은 scaffold 인라인, {{W4}} 는 건너뜀).

실행: python tests/fixtures/build_brief_web_2026_06_26.py [out.json]
      (out 미지정 시 web/data/briefs/brief_web_2026_06_26.json — 빈슬롯)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # repo root (card_scaffold.py)
sys.path.insert(0, ROOT)

# 06-22 빌더의 파서·web-card 직렬화 로직을 재사용(중복 구현 금지). 파서는 1회용 fixture
# 빌더라 import 가 안전(운영 경로 아님). _kind_for 만 06-26 유형으로 확장해 덮어쓴다.
import card_scaffold as cs  # noqa: E402
import build_brief_web_2026_06_22 as b22  # noqa: E402

RUN_DATE = "2026-06-26"
WINDOW = "2026-06-19 ~ 2026-06-26"
HANDOFF_PAGE = "38a3142f-dc11-816f-8f9a-f568f9a49d0c"
ROWS_FILE = "handoff_rows_2026_06_26.json"


def _kind_for(card_type: str, agency: str) -> str:
    """유형 라벨(+기관) → kind. 06-22 매핑 + 06-26 신규 유형(GMP실사·OpenFDA Recall)."""
    if card_type == "GMP실사":
        return "gmp-inspection"
    if card_type == "Recall":                       # OpenFDA Recall(agency=FDA)
        return "openfda-recall"
    return b22._kind_for(card_type, agency)         # 나머지는 06-22 매핑 재사용


def build() -> dict:
    with open(os.path.join(HERE, ROWS_FILE), encoding="utf-8") as fh:
        rows = json.load(fh)

    # 06-22 파서 재사용하되 kind 매핑만 06-26 확장본으로 교체(파싱 후 section 재산출).
    parsed = []
    for r in rows:
        p = b22._parse_scaffold(r["document_id"], r["card_scaffold"])
        p["kind"] = _kind_for(p["card_type"], p["agency"])
        p["section"] = cs.resolve_section(p["kind"], {"source": p["source"]})
        parsed.append(p)

    # 정렬·그룹은 단일원천(compute_render_plan)에 stub 카드를 넣어 받는다(재구현 금지).
    stubs = [cs.CardScaffold(
        card_id=f"{p['source']}::{p['doc_id']}", section=p["section"], kind=p["kind"],
        evidence=p["evidence"], modality=p["mod_key"], signal_tier=f"Tier {p['signal_num']}",
        date=p["date"], markdown="", prose_input={}) for p in parsed]
    plan = cs.compute_render_plan(stubs)

    cards = []
    for p, stub in zip(parsed, stubs):
        entry = plan.get(stub.card_id, {})
        wc = b22._web_card(p, entry)
        bad = cs.assert_no_card_markup(wc)
        assert not bad, f"{p['doc_id']}: card markup leak {bad}"
        cards.append(wc)
    cards.sort(key=lambda d: d["render_order"])

    agencies, categories = [], []
    evidence = {"A": 0, "B": 0, "C": 0}
    for wc in cards:
        if wc["agency"] and wc["agency"] not in agencies:
            agencies.append(wc["agency"])
        if wc["category"] and wc["category"] not in categories:
            categories.append(wc["category"])
        evidence[wc["evidence_level"]] = evidence.get(wc["evidence_level"], 0) + 1

    return {
        "schema_version": cs.WEB_SCHEMA_VERSION,
        "provenance": {
            "handoff_page": HANDOFF_PAGE,
            "run_date_kst": RUN_DATE,
            "note": "routine-handoff::2026-06-26(62행, 윈도우 2026-06-19~2026-06-26) 중 렌더 "
                    "카드 27개의 동결 scaffold markdown 을 파싱한 빈슬롯 web-card 브리프(1회용·"
                    "발행 경로). 산문 슬롯은 inject_slots 가 v16 델타로 채운다. 사실 셀=scaffold "
                    "verbatim 전사(PL18 의미 보존).",
        },
        "brief": {
            "run_date_kst": RUN_DATE,
            "window": WINDOW,
            "publish_date": RUN_DATE,
            "agencies": agencies,
            "categories": categories,
            "tldr": [],
            "coverage": {
                "intake_total": len(rows),
                "rendered": len(cards),
                "evidence": evidence,
            },
            "ai_disclosure": True,
        },
        "cards": cards,
    }


def main() -> None:
    fixture = build()
    out = (sys.argv[1] if len(sys.argv) > 1
           else os.path.join(ROOT, "web", "data", "briefs", "brief_web_2026_06_26.json"))
    data = json.dumps(fixture, ensure_ascii=False, indent=1)
    with open(out, "w", encoding="utf-8", newline="\n") as f:   # repo eol=lf 정책
        f.write(data + "\n")
    print("wrote", out, "cards=", len(fixture["cards"]),
          "evidence=", fixture["brief"]["coverage"]["evidence"])


if __name__ == "__main__":
    main()
