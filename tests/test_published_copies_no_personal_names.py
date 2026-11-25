# -*- coding: utf-8 -*-
"""발행 사본 실명 게이트 — 편집팀 precheck 와 **같은 판정 함수**로 저장소의 주간 발행 사본을 본다.

[2026-11-25] #1916 이 main 발행 사본 33개 파일에서 508건을 잡았다(502건이 실제 개인 실명 —
483 수신인·직원·임상시험자, WL 민간 수신인, WHO 보고서 업체 임원). 공개 중인 발행본 14장(9개 호)은
같은 PR 에서 역할 표기([recipient]·[업체 임원]·'개인 사업자')로 바꿨다. 이 게이트는 그 뒤를 막는다.

규칙
  · 막음  = `briefs/`(사이트에 나가는 발행본)에서 **기준 목록에 없는** 판정. 새 호(기준일 이후
            발행)는 기준 목록에 들 수 없으니 판정이 하나라도 있으면 실패하고, 기존 호도 이미 정리한
            칸에 실명이 다시 들어오면(재조립·재추출) 실패한다.
  · 경고  = 기준 목록에 있는 기존 판정(원문 상세 칸 대부분)과 조립 입력(`deltas/`·`scaffolds/`)의
            새 판정. 입력은 브리프 페이지로 직접 배포되지 않는다 — 다만 483 관찰은 조립 때 deep 의
            `source_text` 에서 재추출되므로, 입력 경고가 발행본 차단으로 이어질 수 있다.
  · 기준 목록(`tests/fixtures/published_names_baseline.json`)에는 기준일 이후 호의 발행본을 넣을 수 없다.

판정 함수: `company.editorial.precheck.source_person_names`(원문 → 개인 이름 낱말) +
`guess_check.person_firm_lines`(업체명이 사람 이름) + `guess_check._name_hit` — 편집팀 사전 점검과 같은 것.
FDA 483 의 전용 실사관 칸(`deterministic_detail.inspectors[]`)은 규제기관 공식 서명자라 제외한다.
"""

import hashlib
import json
import re
import unittest
from collections import defaultdict
from pathlib import Path

from company.editorial import precheck

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "data"
BASELINE = Path(__file__).resolve().parent / "fixtures" / "published_names_baseline.json"
guess_check = precheck.guess_check

# 이 날짜 이후(포함)에 발행되는 호는 기준 목록(경고)에 들 수 없다 — 판정이 있으면 실패.
CUTOFF_ISSUE = "2026_11_25"
_ISSUE_RE = re.compile(r"(\d{4}_\d{2}_\d{2})")
DISPLAYED_PREFIX = "web/data/briefs/"


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
            if precheck.person_name_hits(source_names[card_id], value):
                findings[path, card_id].add(slot)
    return findings


def _keys(findings):
    """판정 → `파일 | 카드 ID | 칸` 문자열 집합(기준 목록과 같은 꼴)."""
    return {f"{path} | {card_id} | {slot}"
            for (path, card_id), slots in findings.items() for slot in slots}


def _issue(key):
    m = _ISSUE_RE.search(key.split(" | ", 1)[0])
    return m.group(1) if m else ""


def load_baseline():
    return set(json.loads(BASELINE.read_text(encoding="utf-8"))["entries"])


def classify(found, baseline):
    """(막음, 경고) — 순수. 막음 = 발행본의 기준 목록 밖 판정. 경고 = 나머지."""
    block = sorted(k for k in found - baseline if k.startswith(DISPLAYED_PREFIX))
    warn = sorted(k for k in found if k not in block)
    return block, warn


# ── 시험 자료·시험 코드 실명 게이트(2026-11-25) ────────────────────────────────────────────────
# 저장소가 공개다. 발행본에서 지운 실명이 시험 재료(`tests/fixtures/**`)·시험 코드(`tests/*.py`)로 되살아나면 안 된다
# (#1955 가 #1937 이전 원문을 시험 재료로 실어 지운 이름을 되살릴 뻔했다). 판정은 발행 사본 게이트와 같은 함수.
#   · 막음 = 허용 목록(가짜 이름)에도, 기존 기준 목록에도 없는 이름.
#   · 경고 = 기준 목록(main 에 이미 있던 자료의 이름 — 이름 대신 해시로 적는다). 지우면 기준 목록에서도 뺀다.
FIXTURE_NAMES_BASELINE = Path(__file__).resolve().parent / "fixtures" / "fixture_names_baseline.json"
# 시험용 가짜 이름(실존 인물 아님)과 관용 자리표시 이름.
FAKE_FIXTURE_NAMES = frozenset((
    "Arbel Brimtal Danqui Darvin Dovan Dravesh Hadrin Jorn Kavrin Kelvaro Ko Ka Krest Mirel More Mark Morlai Okonder "
    "Orlena Ostrell Pelkastrom Pelvik Qiu Quell Quent Quorvala Ravel Tasindra Tor Torvel Twisk Varsten Vasky Velloc "
    "Velmor Veshan Zha Zun Doe Roe Testerson Example").split())
_TEXT_SUFFIXES = (".json", ".txt", ".md", ".html", ".csv")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _test_material():
    here = Path(__file__).resolve().parent
    files = [p for p in sorted((here / "fixtures").rglob("*")) if p.is_file() and p.suffix in _TEXT_SUFFIXES
             and p != FIXTURE_NAMES_BASELINE]
    files += sorted(here.glob("*.py"))
    for path in files:
        raw = path.read_bytes().decode("utf-8", errors="replace")
        if path.suffix == ".json":
            try:
                yield path, list(_strings(json.loads(raw)))
                continue
            except ValueError:
                pass
        yield path, [raw]


def _name_key(path, name):
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{path.relative_to(ROOT).as_posix()} | sha256:{digest}"


def fixture_name_keys():
    """시험 자료·코드의 원문 개인 이름 → `파일 | sha256:…` 집합(가짜 이름 제외)."""
    keys = {}
    for path, values in _test_material():
        for value in values:
            for name in precheck.source_person_names(value):
                if name not in FAKE_FIXTURE_NAMES:
                    keys[_name_key(path, name)] = name
    return keys


class TestMaterialNoPersonalNamesTest(unittest.TestCase):
    """시험 재료·시험 코드에 새 실명이 들어오면 실패한다 — 공개 저장소."""

    def test_no_new_personal_names_in_tests(self):
        found = fixture_name_keys()
        baseline = set(json.loads(FIXTURE_NAMES_BASELINE.read_text(encoding="utf-8"))["entries"])
        new = sorted(f"{k.split(' | ')[0]} | {found[k]}" for k in set(found) - baseline)
        stale = len(baseline - set(found))
        old = len(set(found) & baseline)
        if old or stale:
            print(f"\n[경고] 시험 자료 기존 실명 {old}건(기준 목록, 막지 않음)" + (f" · 정리돼 사라진 기준 {stale}건" if stale else ""))
        self.assertEqual(new, [], "시험 자료·시험 코드에 실명이 있다(공개 저장소). 같은 꼴의 가짜 이름으로 바꾸고, "
                                  "새 가짜 이름이면 FAKE_FIXTURE_NAMES 에 더한다.")

    def test_fake_names_are_caught_without_allowlist(self):
        """허용 목록이 판정을 대신하지 않는다 — 가짜 이름도 판정 함수에는 이름으로 잡힌다(게이트가 살아 있다)."""
        fx = json.loads((Path(__file__).resolve().parent / "fixtures" / "person_names_1123_pre1937.json")
                        .read_text(encoding="utf-8"))
        names = set()
        for value in _strings(fx):
            names |= precheck.source_person_names(value)
        self.assertTrue({"Velmor", "Quell", "Brimtal", "Dravesh", "Torvel"} <= names)


class PublishedCopiesNoPersonalNamesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = _keys(_findings(_copies()))
        cls.baseline = load_baseline()

    def test_published_briefs_have_no_new_personal_names(self):
        block, warn = classify(self.found, self.baseline)
        if warn:
            print(f"\n[경고] 발행 사본 개인 실명 판정 {len(warn)}건(기존 기준 목록·조립 입력, 막지 않음). "
                  f"발행본 {sum(k.startswith(DISPLAYED_PREFIX) for k in warn)}건.")
        if block:
            self.fail("발행본 개인 실명 판정 — 기준 목록에 없다(새 호이거나 정리한 칸에 다시 들어왔다).\n"
                      "공직자·규제기관 공식 서명자가 아니면 역할로 쓴다: 483 원문 상세 '[recipient]', "
                      "WHO 상세 '[company executive]'/'[업체 임원]', 업체명이 사람이면 '개인 사업자'(11-21 결정).\n"
                      "(파일 | 카드 ID | 칸)\n" + "\n".join(block))

    def test_baseline_has_no_issue_after_cutoff(self):
        late = sorted(k for k in self.baseline
                      if k.startswith(DISPLAYED_PREFIX) and _issue(k) >= CUTOFF_ISSUE)
        self.assertEqual(late, [], f"기준일({CUTOFF_ISSUE}) 이후 호의 발행본은 기준 목록(경고)에 넣을 수 없다")

    def test_cleaned_14_cards_stay_clean(self):
        """11-25 에 정리한 공개 발행본 14장은 국·영 어느 칸에도 판정이 없어야 한다."""
        cleaned = {"fda483-193546", "fda483-193585", "who-whopir-443308706477", "who-whopir-4fe0703b89ce",
                   "who-whopir-5ca01e2642a0", "fda483-195318", "fda483-195384", "fda483-196262",
                   "fda483-196273", "fda483-196312", "fda483-196351", "fda483-196372", "fda483-196412",
                   "who-whopir-492199ae88d3"}
        hits = sorted(k for k in self.found if k.split(" | ")[1] in cleaned)
        self.assertEqual(hits, [])


class ClassifyTest(unittest.TestCase):
    """막음/경고 규칙 자체(순수) — 사본 상태와 무관하게 동작을 고정한다."""

    def test_new_issue_brief_blocks(self):
        k = "web/data/briefs/brief_web_2026_11_30.json | fda483-1 | deterministic_detail.observations[0].detail"
        self.assertEqual(classify({k}, set()), ([k], []))

    def test_baselined_brief_warns(self):
        k = "web/data/briefs/brief_web_2026_11_02.json | fda483-1 | key_facts[2]"
        self.assertEqual(classify({k}, {k}), ([], [k]))

    def test_regression_into_old_brief_blocks(self):
        k = "web/data/briefs/brief_web_2026_07_20.json | fda483-193585 | headline_target"
        self.assertEqual(classify({k}, set()), ([k], []))

    def test_input_copies_only_warn(self):
        k = "web/data/deltas/deep_2026_11_30.json | fda483-1 | source_text"
        self.assertEqual(classify({k}, set()), ([], [k]))

    def test_same_judge_as_precheck(self):
        text = "NAME AND TITLE OF INDIVIDUAL TO WHOM REPORT ISSUED Darvin G. Ostrell, Vice President FIRM NAME"
        names = precheck.source_person_names(text, "Acme Pharma")
        self.assertIn("Ostrell", names)
        self.assertTrue(guess_check._name_hit("Ostrell", text))
        self.assertFalse(any(guess_check._name_hit(n, text.replace("Darvin G. Ostrell", "[recipient]"))
                             for n in names))


class PersonalStreetAddressTest(unittest.TestCase):
    """[2026-11-25] 개인 사업자 카드의 번지 주소 — 발행본·조립 입력 **모두** 막는다(저장소가 공개라 입력도 공개).
    11-23호 196329(개인 농장)는 이름을 '개인 사업자'로 바꾼 뒤에도 deep `source_text` 에 번지가 남았다.
    판정 = `grm_findings.street_addresses`(규제기관 사무소 주소 칸 제외). 자동 정리 = 발행 브릿지
    `delta_bridge.extract_handoff_body_full` → `grm_findings.redact_person_firm_source`(같은 함수)."""

    @staticmethod
    def _personal_hits(copies):
        from grm_findings import REDACTED_PERSON_BY_LANG, person_firm_name, street_addresses
        marks = tuple(REDACTED_PERSON_BY_LANG.values())
        personal, firm, slots_by = set(), defaultdict(list), defaultdict(list)
        for path, payload in copies:
            for card_id, card in _cards(payload):
                firm[card_id] += [str(card.get(k) or "") for k in ("firm", "headline_target")]
                firm[card_id] += [str(f.get("value") or "") for f in card.get("facts") or []
                                  if isinstance(f, dict)]
                for slot, value in _text_slots(card):
                    slots_by[card_id].append((path, slot, value))
                    if any(m in value for m in marks):
                        personal.add(card_id)
        personal |= {cid for cid, vals in firm.items() if any(person_firm_name(v.strip()) for v in vals)}
        return sorted(f"{path} | {cid} | {slot} | {addr!r}"
                      for cid in personal for path, slot, value in slots_by[cid]
                      for _, _, addr in street_addresses(value))

    def test_no_personal_street_address_in_any_copy(self):
        hits = self._personal_hits(_copies())
        self.assertEqual(hits, [], "개인 사업자 카드에 번지 주소가 있다 — 도시·주 단위까지만 둔다"
                                   "(grm_findings.redact_street_addresses, 표기 '[번지 생략]').\n" + "\n".join(hits))

    def test_judge_catches_farm_and_skips_agency_office(self):
        import grm_findings as g
        text = ("DISTRICT ADDRESS AND PHONE NUMBER\n250 Marquette Ave,\nSte.\n600\nMinneapolis,\nMN 55401\n"
                "NAME AND TITLE OF INDIVIDUAL TO WHOM REPORT ISSUED\nKenneth F Tesch, Owner\nFIRM NAME\n"
                "STREET ADDRESS\nKenneth F Tesch\n234797\nPickard Ave\nCITY, STATE, ZIP CODE, COUNTRY")
        self.assertEqual([a for _, _, a in g.street_addresses(text)], ["234797\nPickard Ave"])
        out = g.redact_person_firm_source(text, "Kenneth F Tesch")
        self.assertNotIn("Pickard", out)
        self.assertNotIn("Tesch", out)
        self.assertIn("250 Marquette Ave", out)          # 규제기관 사무소 주소는 남는다
        self.assertIn(g.STREET_REDACTED, out)

    def test_corporate_source_untouched(self):
        import grm_findings as g
        text = "FIRM NAME\nRK Pharma Inc.\n401 N Middletown Rd Building 21"
        self.assertEqual(g.redact_person_firm_source(text, "RK Pharma Inc."), text)

    def test_bridge_redacts_person_firm_body(self):
        import delta_bridge
        payload = {"rows": [
            {"web_card_id": "fda483-1", "firm": "Kenneth F Tesch",
             "deep_analysis_input": {"body_full": "FIRM NAME\nKenneth F Tesch\n234797\nPickard Ave\nOBSERVATION 1"}},
            {"web_card_id": "fda483-2", "firm": "Acme Pharma Inc.",
             "deep_analysis_input": {"body_full": "FIRM NAME\nAcme Pharma Inc.\n12 Main Street\nOBSERVATION 1"}}]}
        bodies = delta_bridge.extract_handoff_body_full(payload)
        self.assertNotIn("Pickard", bodies["fda483-1"])
        self.assertNotIn("Tesch", bodies["fda483-1"])
        self.assertIn("12 Main Street", bodies["fda483-2"])


if __name__ == "__main__":
    unittest.main()
