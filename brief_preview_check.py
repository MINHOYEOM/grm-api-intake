#!/usr/bin/env python3
"""발행 브리프 미리보기 5유형 점검. 저장소의 JSON을 읽어 Markdown 표를 출력한다."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import firm_groups
from check_en_slots import find_problems, render
from check_render_gate import find_violations
from company.editorial import precheck

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "web" / "data"
guess_check = precheck.guess_check


def card_refs(refs):
    return f"{len(refs)}건 (예: {', '.join(refs[:5])})" if refs else "0건"


def personal_name_refs(cards, scaffold):
    by_id = {str(card["id"]): card for card in cards}
    source_cards = dict(by_id)
    source_cards.update(guess_check.scaffold_index(scaffold))
    # 기존 원문 대조를 발행본의 한·영 산문 및 심층에 적용한다.
    hits = precheck.private_name_warn_lines(
        {"cards": by_id}, by_id, scaffold_cards=source_cards,
    )
    # 이름 자체는 요약에 남기지 않는다.
    refs = [line.split()[3].partition(":")[0] for line in hits]
    for cid, card in by_id.items():
        slots = [(slot, value) for slot, value, *_ in
                 guess_check.delta_slots(card) + guess_check.deep_slots(card)]
        slots.append(("headline_target", str(card.get("headline_target") or "")))
        slots.extend((f"facts[{i}]", str(fact.get("value") or ""))
                     for i, fact in enumerate(card.get("facts") or []))
        for source in (source_cards[cid], card):
            block, warn = guess_check.person_firm_lines(slots, None, source)
            refs.extend(f"{cid}.{line.split(':', 1)[0]}" for line in block + warn)
    return list(dict.fromkeys(refs))


def english_hangul_refs(doc):
    en_brief = (doc["brief"].get("en") or {})
    refs = ["brief.en.tldr"] if any(
        render._HANGUL_RE.search(str(line)) for line in en_brief.get("tldr") or []
    ) else []
    for card in doc["cards"]:
        slots = list(render.en_code_slot_hangul(card))
        en = card.get("en") or {}
        for field in (*render.CARD_NARRATIVE_FIELDS, "hook_line"):
            value = en.get(field)
            values = value if isinstance(value, list) else [value]
            if any(render._HANGUL_RE.search(str(item or "")) for item in values):
                slots.append(f"en.{field}")
        if slots:
            refs.append(f"{card['id']} ({', '.join(slots)})")
    return refs


def rendered_cards(cards, lang):
    tr = render.Translator(lang)
    views = [render._card_view(card, tr, lang) for card in cards]
    index = firm_groups.fei_index(firm_groups.default_registry())
    eligible = Counter(
        (view.get("group"), view.get("group_label"), key)
        for card, view in zip(cards, views) if (key := firm_groups.cluster_key(card, index))
    )
    expected = Counter({key: 1 for key, count in eligible.items() if count >= 2})
    render._annotate_firm_groups(views, cards, tr)
    flattened = [view for section in render._build_sections(views, tr) for view in section["cards"]]
    opens, closes = Counter(), Counter()
    for view in flattened:
        key = (view.get("group"), view.get("group_label"), view.get("fgroup_key"))
        if view.get("fgrp_open") and view.get("fgrp_head"):
            opens[key] += 1
        if view.get("fgrp_close"):
            closes[key] += 1
    return flattened, sum(opens.values()), opens != expected or closes != expected


def check_preview(doc, scaffold=None):
    cards = sorted(
        (card for card in doc["cards"] if render._is_renderable(card)),
        key=lambda card: (card.get("render_order") is None, card.get("render_order")),
    )
    doc = {**doc, "cards": cards}
    names = personal_name_refs(cards, scaffold)
    hangul = english_hangul_refs(doc)
    # 영문 슬롯 게이트의 한글 항목은 위에서 별도로 집계한다.
    missing = [line for line in find_problems(doc) if not line.startswith("영문 코드 칸에 한글")]
    missing.extend(
        f"{card['id']}.{field}" for card in cards for field in ("title_issue", "summary")
        if not render._narrative_filled(card.get(field))
    )
    missing.extend(
        f"{card['id']}.deep_analysis" for card in cards
        if "deep_analysis" in card and not card["deep_analysis"]
    )
    violations = find_violations(doc)
    blanks = f"필수 칸 {card_refs(missing)} · 렌더 게이트 {len(violations)}건"
    if violations:
        blanks += f" (검사: {', '.join(dict.fromkeys(label for label, _ in violations))})"

    old_expected = {card["id"]: firm_groups.inspection_dates(card)[0].isoformat()
                    for card in cards if firm_groups.is_old_inspection(card)}
    languages = ["ko", "en"] if render.brief_has_english(doc) else ["ko"]
    old_counts, group_counts, old_errors, group_errors = [], [], [], []
    for lang in languages:
        views, boxes, group_error = rendered_cards(cards, lang)
        displayed = {view["anchor"]: view["old_insp"] for view in views if view.get("old_insp")}
        old_counts.append(f"{lang} {len(displayed)}장")
        group_counts.append(f"{lang} {boxes}상자")
        if group_error:
            group_errors.append(lang)
        for cid in sorted(old_expected.keys() | displayed.keys()):
            mark = displayed.get(cid, {})
            if old_expected.get(cid) != mark.get("date") or not mark.get("label") or not mark.get("date_label"):
                old_errors.append(f"{lang}:{cid}")
    candidates = ["/".join(str(card["id"]) for card in candidate["cards"][:3])
                  for candidate in firm_groups.candidate_groups(cards)]
    merged = [str(card["id"]) for card in cards if (card.get("merged_count") or 0) > 1]
    groups = f"업체 그룹 {', '.join(group_counts)} · 병합 카드 {card_refs(merged)} · 등록 후보 {card_refs(candidates)}"
    if group_errors:
        groups += f" · 예상 그룹 상자와 불일치({', '.join(group_errors)})"

    return [
        ("개인 실명", bool(names) or scaffold is None,
         f"의심 {card_refs(names)}" + (" · 스캐폴드 없음(발행본 원문만 대조)" if scaffold is None else "")),
        ("영문 한글", bool(hangul), card_refs(hangul)),
        ("빈 칸", bool(missing or violations), blanks),
        ("옛 실사 표시", bool(old_errors),
         f"표시 {', '.join(old_counts)} · 누락·불일치 {card_refs(old_errors)}"),
        ("묶음 카드", bool(candidates or group_errors), groups),
    ]


def read_brief(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("cards"), list):
        raise ValueError(f"{path.name}: cards 목록이 없습니다.")
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("brief_date", type=date.fromisoformat, help="발행일 YYYY-MM-DD")
    parser.add_argument("--scaffold", type=Path, help="발행 조립에 사용한 스캐폴드 경로")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    name = f"brief_web_{args.brief_date:%Y_%m_%d}.json"
    scaffold_path = args.scaffold or DATA / "scaffolds" / name
    try:
        doc = read_brief(DATA / "briefs" / name)
        scaffold = read_brief(scaffold_path) if args.scaffold or scaffold_path.is_file() else None
    except (OSError, ValueError) as exc:
        print(f"미리보기 점검 입력 오류: {exc}", file=sys.stderr)
        return 2
    rows = check_preview(doc, scaffold)
    print(f"### 브리프 미리보기 {args.brief_date} · 5유형 점검 · 경고 {sum(row[1] for row in rows)}유형")
    print("\n| 유형 | 판정 | 결과 |\n| --- | --- | --- |")
    for label, warned, details in rows:
        details = details.replace("|", "&#124;").replace("\n", " ")
        print(f"| {label} | {'경고' if warned else '확인'} | {details} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
