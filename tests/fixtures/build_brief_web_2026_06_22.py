#!/usr/bin/env python3
"""6/22 실데이터 web-card(`grm-web-card/v1`) 회귀 픽스처 생성기 (P1).

provenance: `handoff_rows_2026_06_22.json`(Notion handoff page 36행, document_id +
`card_scaffold` markdown). 이 36개 **동결 scaffold markdown 을 파싱**해 `grm-web-card/v1`
브리프 JSON(`brief_web_2026_06_22.json`)을 만든다.

왜 파서인가(P1 결정 2026-06-24, 사용자 "둘 다"): handoff_rows 에는 row/raw 가 없어
`to_web_card()`(결정론 producer 재사용 경로)를 그대로 돌릴 수 없다. 그래서 **실-6/22
verbatim 골든**은 발행 당시의 단일 산물(scaffold markdown)에서 사실 셀을 글자 단위로 전사한다
(§4-1 "handoff_rows 의 verbatim 셀·문서번호가 신 골든에서도 글자 단위 동일" — PL18 의미 보존).
이 파서는 **fixture 전용·1회성 마이그레이션**이며 운영 경로가 아니다(운영 = `to_web_card`,
`tests/golden/*.expected.webcard.json`/`brief_web.expected.json` 가 검증). 정렬·그룹은
재구현하지 않고 `compute_render_plan()`(단일원천)에 stub 카드를 넣어 받는다.

실행: `python tests/fixtures/build_brief_web_2026_06_22.py`
      → tests/fixtures/brief_web_2026_06_22.json
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # repo root (card_scaffold.py)
sys.path.insert(0, ROOT)

import card_scaffold as cs  # noqa: E402

RUN_DATE = "2026-06-22"
WINDOW = "2026-06-15 ~ 2026-06-22"

# scaffold 제품군 배지 → modality 키(정렬용). 없으면 "" (규범문서 → 카드 modality=null).
_MOD_REV = {"💊 합성의약품": "Chemical", "🧬 바이오의약품": "Biologic", "▫️ 기타": "Other"}


def _kind_for(card_type: str, agency: str) -> str:
    """유형 라벨(+기관) → kind(섹션·카테고리 산출용). 6/22 실유형 한정 역매핑."""
    if card_type.startswith("FDA 483"):
        return "fda-483"
    if card_type == "Warning Letter":
        return "warning-letter"
    if card_type == "행정처분":
        return "admin-action"
    if card_type == "규제 소식":
        return "rss-news"
    if card_type.startswith("Recall(HC)"):
        return "hc-recall"
    if card_type == "지침·안내서":
        return "mfds-notice" if agency == "MFDS" else "guidance"
    if card_type == "회수·판매중지":
        return "recall-quality"
    # 폴백: 라벨로 못 가르면 global·Other 로 떨어지는 안전한 kind
    return "rss-news"


def _parse_scaffold(doc_id: str, md: str) -> dict:
    """동결 scaffold markdown 1장 → 파싱 구성요소(verbatim 전사)."""
    lines = md.split("\n")

    # ── 제목: ### [card_type · agency] headline_target — **{{TITLE_ISSUE}}**
    inner = lines[0][len("### ["):]
    bracket, _, rest = inner.partition("] ")
    card_type, _, agency = bracket.partition(" · ")
    headline_target = rest.split(" — **")[0]

    # ── W1 배지 라인(백틱 토큰): Evidence·Source·Signal·(modality)·(type_tag)
    badge_line = next(l for l in lines if l.strip().startswith("`Evidence"))
    badges = re.findall(r"`([^`]+)`", badge_line)
    evidence = badges[0].split()[-1]                 # "Evidence A" → "A"
    source = badges[1]
    sig = next(b for b in badges if b.startswith("Signal"))
    sm = re.match(r"Signal (\w+) \(T(\d)\)", sig)
    signal_label, signal_num = sm.group(1), int(sm.group(2))
    mod_badge = next((b for b in badges if b in _MOD_REV), None)
    type_tag = badges[-1] if badges and badges[-1] not in _MOD_REV else None

    # ── W2 사실표 → facts(백틱 제거 verbatim)
    facts = []
    for l in lines:
        m = re.match(r"<tr><td>\*\*(.+?)\*\*</td><td>(.*)</td></tr>$", l)
        if m:
            facts.append({"label": m.group(1), "value": cs._plain(m.group(2))})

    # ── W3 원문 인용(Evidence A 만). KO=**원문**(translation null) / 비KO=**원문 및 번역**("")
    quotes = []
    hdr_idx = next((i for i, l in enumerate(lines)
                    if l.strip() in ("**원문**", "**원문 및 번역**")), None)
    if hdr_idx is not None:
        is_ko = lines[hdr_idx].strip() == "**원문**"
        seg = None
        for l in lines[hdr_idx + 1:]:
            if l.startswith('<callout icon="🔍"'):
                break
            if l.startswith("> "):
                if seg is not None:
                    quotes.append(seg)
                seg = re.sub(r"^[①②③④⑤]\s*", "", l[2:])   # 마커 제거 → 원문 복원
            elif re.match(r"\s*\{\{W4", l) or l.strip() == "":
                continue                                    # 번역 슬롯/공백 건너뜀
            elif seg is not None:
                seg = seg + "\n" + l                         # 원문 내부 줄바꿈 이어붙임
        if seg is not None:
            quotes.append(seg)
        quotes = [{"original": q, "translation": (None if is_ko else "")} for q in quotes]

    # ── W8 푸터 듀얼링크
    # 링크 캡처는 ` )`(공백) 또는 줄끝 앞의 닫는 괄호까지 — URL 내부 ')' 에서 조기 절단 방지.
    foot = next(l for l in lines if "**출처**" in l)
    comb = re.search(r"정보출처/공식원본 \[링크\]\((.+?)\)(?=\s|$)", foot)
    if comb:
        info = official = comb.group(1)
    else:
        im = re.search(r"📰 정보출처 \[링크\]\((.+?)\)(?=\s|$)", foot)
        om = re.search(r"📎 [^\[]*\[링크\]\((.+?)\)(?=\s|$)", foot)
        info = im.group(1) if im else ""
        official = om.group(1) if om else ""

    kind = _kind_for(card_type, agency)
    section = cs.resolve_section(kind, {"source": source})
    return {
        "doc_id": doc_id, "card_type": card_type, "agency": agency,
        "headline_target": headline_target, "evidence": evidence, "source": source,
        "signal_label": signal_label, "signal_num": signal_num,
        "mod_badge": mod_badge, "mod_key": _MOD_REV.get(mod_badge, ""),
        "type_tag": type_tag, "facts": facts, "quotes": quotes,
        "info": info, "official": official, "kind": kind, "section": section,
        "date": next((f["value"] for f in facts if f["label"] in ("발행일", "처분일")), ""),
    }


def _web_card(p: dict, render_entry: dict) -> dict:
    """파싱 구성요소 + render_entry → grm-web-card/v1 카드 dict(스키마 동일)."""
    return {
        "id": p["doc_id"],
        "render_order": render_entry.get("render_order"),
        "group": cs._WEB_GROUP.get(p["section"], p["section"]),
        "group_label": render_entry.get("group_label") or None,
        "agency": p["agency"],
        "card_type": p["card_type"],
        "category": cs._category(p["kind"]),
        "modality": p["mod_badge"],            # 배지 문자열 or None(규범문서)
        "evidence_level": p["evidence"],
        "signal_tier": p["signal_num"],
        "signal_label": p["signal_label"],
        "type_tag": p["type_tag"],
        "headline_target": p["headline_target"],
        "title_issue": "",
        "summary": "",
        "facts": p["facts"],
        "quotes": p["quotes"],
        "evidence_basis": ("Intake raw" if p["evidence"] == "A"
                           else "공식 인덱스 + 보조 출처"),
        "key_facts": [],
        "implication": "",
        "checks": [],
        "merged_count": 1,
        "merged_items": [],
        "sources": {
            "info_url": p["info"],
            "official_url": p["official"],
            "official_is_pdf": cs._official_is_pdf(p["official"]),
            "link_check": {"info": "pending", "official": "pending"},
        },
    }


def build() -> dict:
    with open(os.path.join(HERE, "handoff_rows_2026_06_22.json"),
              encoding="utf-8") as fh:
        rows = json.load(fh)
    parsed = [_parse_scaffold(r["document_id"], r["card_scaffold"]) for r in rows]

    # 정렬·그룹은 단일원천(compute_render_plan)에 stub 카드를 넣어 받는다(재구현 금지).
    stubs = [cs.CardScaffold(
        card_id=f"{p['source']}::{p['doc_id']}", section=p["section"], kind=p["kind"],
        evidence=p["evidence"], modality=p["mod_key"], signal_tier=f"Tier {p['signal_num']}",
        date=p["date"], markdown="", prose_input={}) for p in parsed]
    plan = cs.compute_render_plan(stubs)

    cards = []
    for p, stub in zip(parsed, stubs):
        entry = plan.get(stub.card_id, {})
        wc = _web_card(p, entry)
        # 무결성: 표현 틀 마크업 0(불변식 #6)
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
            "handoff_page": "3863142f-dc11-81ff-8d9f-fcea41becbab",
            "run_date_kst": RUN_DATE,
            "note": "36 동결 scaffold markdown 을 파싱한 실-6/22 web-card 골든(P1, fixture "
                    "전용·1회성). 사실 셀=scaffold verbatim 전사(PL18 의미 보존). 운영 경로는 "
                    "to_web_card — golden/*.expected.webcard.json·brief_web.expected.json 가 검증.",
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
    out = os.path.join(HERE, "brief_web_2026_06_22.json")
    data = json.dumps(fixture, ensure_ascii=False, indent=1)
    with open(out, "w", encoding="utf-8", newline="\n") as f:   # repo eol=lf 정책
        f.write(data + "\n")
    print("wrote", out, "cards=", len(fixture["cards"]),
          "evidence=", fixture["brief"]["coverage"]["evidence"])


if __name__ == "__main__":
    main()
