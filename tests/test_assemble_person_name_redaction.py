# -*- coding: utf-8 -*-
"""발행 경로 개인 실명 자동 가림(2026-11-25) — `assemble_publish_brief._redact_person_names`.

11-25 #1937 은 공개 발행본 14장의 483 수신인·WHO 업체 임원 이름을 손으로 역할 표기로 바꿨다. 이 시험은
① 11-23 호를 #1937 이전 원문으로 다시 조립하면 **같은 가림이 자동으로** 나오는지(회귀),
② FDA 실사관 서명(`inspectors[]`)·업체명은 가리지 않는지,
③ 판정 함수 수리(오탐 'Chief'·'September'·'Ste', 단일 낱말 일치)가 유지되는지를 고정한다.
판정·가림은 편집팀 precheck·발행 사본 게이트와 같은 함수(`precheck.source_person_roles`·`person_name_spans`)다.
"""

import copy
import json
import unittest
from pathlib import Path

import assemble_publish_brief as apb
from company.editorial import precheck

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "data"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "person_names_1123_pre1937.json"
CLEANED_1123 = ["fda483-196312", "fda483-196351", "fda483-196372", "fda483-196412", "who-whopir-492199ae88d3"]


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _detail_strings(dd):
    return " ".join(v for _, v in apb._detail_text_slots(dd or {}))


class Reassemble1123MatchesHandRedactionTest(unittest.TestCase):
    """11-23 재료(#1937 이전 원문)로 다시 조립 → #1937 이 손으로 고친 발행본과 결정론 상세가 같다."""

    @classmethod
    def setUpClass(cls):
        fx = _load(FIXTURE)
        cls.scaffold = _load(DATA / "scaffolds" / "brief_web_2026_11_23.json")
        cls.delta = _load(DATA / "deltas" / "delta_2026_11_23.json")
        deep = _load(DATA / "deltas" / "deep_2026_11_23.json")
        cls.deep = copy.deepcopy(deep.get("items", deep))
        for cid, text in fx["source_text"].items():          # 원문으로 되돌린다
            cls.deep.setdefault(cid, {})["source_text"] = text
        for card in cls.scaffold["cards"]:
            if card.get("id") in fx["scaffold_detail"]:
                card["deterministic_detail"] = fx["scaffold_detail"][card["id"]]
        cls.deep_before = copy.deepcopy(cls.deep)
        cls.out, cls.report = apb.assemble_publish_brief(cls.scaffold, cls.delta, strict=False,
                                                         deep_deltas=cls.deep)
        cls.published = {c["id"]: c for c in _load(DATA / "briefs" / "brief_web_2026_11_23.json")["cards"]}

    def test_inputs_really_hold_names(self):
        """재료가 실명을 담고 있어야 시험이 뜻이 있다(회귀 재료가 이미 가려져 있으면 공회전)."""
        fx = _load(FIXTURE)
        for cid in CLEANED_1123:
            roles = apb.card_person_roles({"deterministic_detail": fx["scaffold_detail"][cid]},
                                          fx["source_text"].get(cid, ""))
            self.assertTrue(roles, cid)

    def test_same_redaction_as_hand_fix(self):
        out = {c["id"]: c for c in self.out["cards"]}
        for cid in CLEANED_1123:
            with self.subTest(cid=cid):
                self.assertEqual(out[cid]["deterministic_detail"], self.published[cid]["deterministic_detail"])
        self.assertTrue(set(CLEANED_1123) <= set(self.report.redacted_name_ids))

    def test_no_other_card_is_touched(self):
        """가림이 걸린 카드는 #1937 이 손으로 고친 카드뿐이다(다른 카드의 원문은 그대로)."""
        self.assertEqual(sorted(self.report.redacted_name_ids), sorted(CLEANED_1123))

    def test_source_text_is_preserved(self):
        """원문(deep source_text)은 바꾸지 않는다 — 화면 사본만 바뀐다."""
        self.assertEqual(self.deep, self.deep_before)
        self.assertIn("Velmor Tasindra Quell", self.deep["fda483-196312"]["source_text"])

    def test_gate_finds_nothing_in_assembled_cards(self):
        out = {c["id"]: c for c in self.out["cards"]}
        for cid in CLEANED_1123:
            card = out[cid]
            roles = apb.card_person_roles(card, self.deep_before.get(cid, {}).get("source_text", ""))
            for path, text in apb._detail_text_slots(card["deterministic_detail"]):
                self.assertEqual(precheck.person_name_hits(roles, text), set(), f"{cid} {path}")


def _card_483(detail, inspectors=None, firm="Acme Pharma Inc"):
    return {
        "id": "fda483-900001",
        "headline_target": firm,
        "facts": [{"label": "제조소/업체", "value": f"{firm} · FEI 1234567"}],
        "deterministic_detail": {"type": "fda_483_observations", "inspectors": list(inspectors or []),
                                 "observations": [{"number": "1", "deficiency": "Procedures are not followed.",
                                                   "detail": detail}]},
    }


HEADER = ("NAME AND TITLE OF INDIVIDUAL TO WHOM REPORT ISSUED\nDarvin G. Ostrell, Vice President\n"
          "FIRM NAME\nAcme Pharma Inc\nSTREET ADDRESS\n616 Atrium Dr Ste 100\n")


class RedactRulesTest(unittest.TestCase):
    def _redact(self, card, source_text=""):
        rep = apb.AssembleReport()
        apb._redact_person_names([card], {card["id"]: {"source_text": source_text}}, rep)
        return card["deterministic_detail"], rep

    def test_recipient_replaced_firm_and_inspector_kept(self):
        detail = ("Specifically, the line was not cleaned. " + HEADER +
                  "EMPLOYEE(S) SIGNATURE Mirel L. Varsten, Investigator")
        card = _card_483(detail, inspectors=["Mirel L. Varsten"])
        dd, rep = self._redact(card, source_text=HEADER)
        text = dd["observations"][0]["detail"]
        self.assertIn("[recipient], Vice President", text)
        self.assertNotIn("Ostrell", text)
        self.assertIn("Mirel L. Varsten, Investigator", text)       # 실사관 서명은 공식 인물
        self.assertEqual(dd["inspectors"], ["Mirel L. Varsten"])
        self.assertIn("Acme Pharma Inc", text)                    # 업체명
        self.assertIn("616 Atrium Dr Ste 100", text)              # 주소 'Dr Ste' 는 이름이 아니다
        self.assertEqual(rep.redacted_name_ids, ["fda483-900001"])

    def test_inspector_named_like_recipient_is_not_redacted(self):
        """실사관이 원문 호칭 자리에 나와도(‘Dr. Mirel Varsten’) 서명 칸 이름이면 가리지 않는다."""
        detail = "As stated by Dr. Mirel Varsten during the inspection."
        dd, _ = self._redact(_card_483(detail, inspectors=["Mirel L. Varsten"]))
        self.assertIn("Dr. Mirel Varsten", dd["observations"][0]["detail"])

    def test_company_named_after_person_keeps_firm(self):
        """업체명에 든 낱말('Kelvaro')은 업체명 자리에서 그대로 둔다."""
        header = HEADER.replace("Darvin G. Ostrell", "Mark\nS.\nKelvaro").replace("Acme Pharma Inc", "Kelvaro Electronics Corp")
        card = _card_483("Firm: Kelvaro Electronics Corp.\n" + header, firm="Kelvaro Electronics Corp")
        dd, _ = self._redact(card, source_text=header)
        text = dd["observations"][0]["detail"]
        self.assertIn("Kelvaro Electronics Corp", text)
        self.assertNotIn("Mark\nS.", text)

    def test_korean_slot_uses_korean_label_and_fda_contact(self):
        card = _card_483("ATTN: Ms. Orlena Twisk\nTo: Mr. XO Brimtal, General Manager")
        card["deterministic_detail"]["observations"][0]["detail_ko"] = "Mr. XO Brimtal 총경리가 확인했다."
        src = ("NAME AND TITLE OF INDIVIDUAL TO WHOM REPORT ISSUED\nTo: Mr. XO Brimtal, General Manager\nFIRM NAME")
        dd, _ = self._redact(card, source_text=src)
        obs = dd["observations"][0]
        self.assertIn("[FDA contact]", obs["detail"])
        self.assertIn("To: [recipient], General Manager", obs["detail"])
        self.assertEqual(obs["detail_ko"], "[수신인] 총경리가 확인했다.")

    def test_whopir_company_executive(self):
        card = {"id": "who-whopir-x", "headline_target": "Norvex Lifesciences (API Corp - formerly Pelvik)",
                "deterministic_detail": {"type": "whopir_report", "sections": [
                    {"title": "Organisation", "text": "Mr. Hadrin Velloc is CEO; Director (his son, Mr. Pelvik Velloc).",
                     "text_ko": "Mr. Hadrin Velloc 이 CEO 이고 이사(그의 아들 Mr. Pelvik Velloc)."}]}}
        dd, _ = self._redact(card)
        sec = dd["sections"][0]
        self.assertEqual(sec["text"], "[company executive] is CEO; Director (his son, [company executive]).")
        self.assertEqual(sec["text_ko"], "[업체 임원] 이 CEO 이고 이사(그의 아들 [업체 임원]).")
        self.assertIn("formerly Pelvik", card["headline_target"])   # 업체명은 그대로

    def test_other_detail_types_untouched(self):
        card = {"id": "wl-1", "deterministic_detail": {"type": "wl_violations",
                                                         "violations": [{"text": "Dear Mr. Quent Arbel:"}]}}
        before = copy.deepcopy(card)
        self._redact(card)
        self.assertEqual(card, before)


class JudgeNarrowingTest(unittest.TestCase):
    """① 판정 함수 후보 좁히기 — #1937 이 남긴 오탐 3개와 단일 낱말 일치."""

    def test_chief_across_line_is_not_a_name(self):
        text = "Recipient:\nMr. Ravel Okonder\nChief Executive Officer\nFenwick India Limited"
        self.assertEqual(precheck.source_person_names(text), {"Ravel", "Okonder"})

    def test_inspection_date_inside_recipient_block(self):
        text = ("NAME AND TITLE OF INDIVIDUAL TO WHOM REPORT IS ISSUED DATE(S) OF INSPECTION September 19. 20. 21, "
                "2023 FEI NUMBER 3010227150 TO: Tor Vasky, Managing Director and Site Head FIRM NAME")
        self.assertEqual(precheck.source_person_names(text), {"Tor", "Vasky"})

    def test_street_drive_is_not_doctor(self):
        text = "STREET ADDRESS\nHybrid Pharma LLC\n1015 W Newport Center Dr Ste 106a\nGolden Hills\nDr\nSuite\n100"
        self.assertEqual(precheck.source_person_names(text), set())

    def test_titles_and_nmi_and_org_tail(self):
        self.assertEqual(precheck.source_person_names(
            "INDIVIDUAL TO WHOM REPORT IS ISSUED To: Dr. Jorn Pelkastrom Chief Executive Officer | FIRM NAME"),
            {"Jorn", "Pelkastrom"})
        self.assertEqual(precheck.source_person_names(
            "INDIVIDUAL TO WHOM REPORT IS ISSUED To: Veshan (NMI) Qiu, Chairman & Founder FIRM NAME"),
            {"Veshan", "Qiu"})
        self.assertEqual(precheck.source_person_names(
            "INDIVIDUAL TO WHOM REPORT IS ISSUED to: Mr. Morlai Zha (Vice President) FIRM NAME"), {"Morlai", "Zha"})
        self.assertEqual(precheck.source_person_names(
            "cc: U.S. Agent: Mr. Dovan Krest Atlantic Wellcare Systems Corp., Inc."), {"Dovan", "Krest"})

    def test_possessive_company_after_honorific(self):
        self.assertEqual(precheck.source_person_names("viral control at Dr. Reddy's Laboratories is inadequate"), set())

    def test_single_common_word_is_not_a_hit(self):
        names = precheck.source_person_names("NAME AND TITLE OF INDIVIDUAL TO WHOM REPORT ISSUED\n"
                                             "Mr. More Kavrin, Factory Manager\nFIRM NAME")
        self.assertEqual(names, {"More", "Kavrin"})
        self.assertEqual(precheck.person_name_hits(names, "More than 10 lots were released."), set())
        self.assertEqual(precheck.person_name_hits(names, "Mr. More Kavrin stated that"), {"More", "Kavrin"})
        self.assertEqual(precheck.person_name_hits(names, "Kavrin signed the record."), {"Kavrin"})

    def test_long_ocr_runs_stay_linear(self):
        """이니셜·짧은 낱말이 길게 이어진 OCR 글에서 시간이 폭발하지 않는다(첫 구현의 정규식은 지수 시간이었다)."""
        import time
        names = {"Ostrell", "Darvin"}
        for text in ("A.B." * 3000 + " x", "A. " * 3000 + "x", "Ab " * 3000 + "x", "Jean A. Ab " * 1000 + "x"):
            t0 = time.perf_counter()
            precheck.person_name_spans(names, text)
            self.assertLess(time.perf_counter() - t0, 2.0)

    def test_full_name_forms(self):
        names = {"Zun", "Dravesh", "Brimtal"}
        for text, expect in (("SEE Dr. Ko Zun, Vice President", "SEE [r], Vice President"),
                             ("SEE Dr. R.K.T.P.L. Dravesh, Senior GM", "SEE [r], Senior GM"),
                             ("To: Mr. XO Brimtal, General Manager", "To: [r], General Manager")):
            roles = {n: "recipient" for n in names}
            self.assertEqual(precheck.redact_person_names(text, roles, {"recipient": "[r]", "person": "[p]"})[0], expect)


if __name__ == "__main__":
    unittest.main()
