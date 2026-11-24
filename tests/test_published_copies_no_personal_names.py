# -*- coding: utf-8 -*-
"""Apply the editorial name checks to the checked-in weekly publication copies."""

import json
import unittest
from collections import defaultdict
from pathlib import Path

from company.editorial import precheck

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "data"
guess_check = precheck.guess_check


def _copies():
    # The other JSON files in deltas/ are raw GMP and recall backfill inputs.
    patterns = ("deltas/delta_*.json", "deltas/deep_*.json",
                "scaffolds/*.json", "briefs/*.json")
    for pattern in patterns:
        paths = sorted(DATA.glob(pattern))
        if not paths:
            raise AssertionError(f"No publication copies match {pattern}")
        for path in paths:
            yield path.relative_to(ROOT).as_posix(), json.loads(path.read_text(encoding="utf-8"))


def _cards(payload):
    cards = payload.get("cards", payload.get("items", payload))
    if isinstance(cards, list):
        return [(card["id"], card) for card in cards]
    return list(cards.items())


def _text_slots(value, path=""):
    if isinstance(value, str) and value:
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _text_slots(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _text_slots(child, f"{path}[{index}]")


def _findings(copies):
    cards = []
    contexts = defaultdict(list)
    for path, payload in copies:
        for card_id, card in _cards(payload):
            slots = list(_text_slots(card))
            if card_id.startswith("fda483-"):
                # Official FDA signatories have a dedicated attribution field.
                slots = [(slot, value) for slot, value in slots
                         if not slot.startswith("deterministic_detail.inspectors[")]
            cards.append((path, card_id, card, slots))
            if card.get("headline_target") or card.get("facts"):
                contexts[card_id].append(card)

    firm_text_by_id = {
        card_id: " ".join(f"{precheck._firm_text(None, card)} {card.get('headline_target') or ''}"
                          for card in versions)
        for card_id, versions in contexts.items()
    }
    source_names = defaultdict(set)
    for _, card_id, _, slots in cards:
        firm_text = firm_text_by_id.get(card_id, "")
        for _, value in slots:
            source_names[card_id].update(precheck.source_person_names(value, firm_text))

    findings = defaultdict(set)
    for path, card_id, card, slots in cards:
        # Use the same card's copies as context. Older deltas often have neither
        # an original body nor the firm fact, and deep files may have both.
        for context in contexts.get(card_id) or [card]:
            blocked, _ = guess_check.person_firm_lines(slots, card, context)
            if blocked:
                findings[path, card_id].update(line.split(":", 1)[0] for line in blocked)
        for slot, value in slots:
            if any(guess_check._name_hit(name, value) for name in source_names[card_id]):
                findings[path, card_id].add(slot)
    return findings


class PublishedCopiesNoPersonalNamesTest(unittest.TestCase):
    def test_published_copies_have_no_personal_names(self):
        findings = _findings(_copies())
        locations = [
            f"{path} | {card_id} | {', '.join(sorted(slots))}"
            for (path, card_id), slots in sorted(findings.items())
        ]
        if locations:
            self.fail("개인 실명 판정 (파일 | 카드 ID | 필드)\n" + "\n".join(locations))
