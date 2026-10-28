"""483 관련성 게이트가 **양식 상용구**에 속지 않는다 (2026-10-28 실측).

`collect_fda_483._to_item` 은 `compute_relevance(...)` 가 Unrelated 면 문서를 버린다. 그런데
OCR 로 읽은 483 의 excerpt 에는 양식 뒷면·머리의 상용구가 들어간다:

- "Section 704(b) of the Federal Food, Drug, and Cosmetic Act" ·
  "any food, drug, device, or cosmetic in such establishment"  (뒷면 — 모든 483 공통)
- "Use this check box to generate the required 483 statement on page 1 for medical device
  observations."  (머리 — 체크박스 안내문, 체크 여부와 무관하게 인쇄된다)

이 문구가 제외어 `cosmetic`·`medical device` 에 걸려, boost 키워드가 2개 미만인 **의약품 483 이
Unrelated 로 버려졌고**, 회수(backfill_483_ocr_recovery)는 그걸 범위 밖으로 보고 Supabase 표식을
`out-of-scope` 로 바꿨다. 고정 픽스처는 실제로 그렇게 찍힌 두 문서의 OCR 본문이다
(web/data/deltas/deep_2026_10_26.json 의 source_text):

- fda483-195646 Corixa — Drug Manufacturer, 무균 공정 환경모니터링 지적
- fda483-195279 Linde  — Medical Gas Manufacturer and Distributor, 안정성 프로그램 부재 지적

관찰 파서는 건드리지 않는다 — 고치는 것은 **게이트 입력**뿐이다(저장 excerpt 는 그대로).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f  # noqa: E402
from grm_taxonomy import compute_relevance, is_out_of_scope_483  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _text(name: str) -> str:
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


_CORIXA = _text("fda483_195646_text.txt")
_LINDE = _text("fda483_195279_text.txt")


def _nrow(company: str, establishment_type: str, media_id: str) -> dict[str, str]:
    return f._datatable_norm_rows([[
        "03/12/2026", company, "3007726770",
        f'<a href="/media/{media_id}/download">483</a>', "PA", "",
        establishment_type, "10/20/2026", "",
    ]])[0]


def _item(text: str, company: str, establishment_type: str, media_id: str):
    """`backfill_483_ocr_recovery._rebuild_item` 과 같은 순서로 _to_item 을 부른다."""
    nrow = _nrow(company, establishment_type, media_id)
    excerpt = f._extract_fda483_excerpt(text)
    observations = f._extract_483_observations_from_text(text, {
        "establishment_type": establishment_type, "fei_number": nrow["fei"],
        "firm_name": company,
    })
    return f._to_item(nrow, excerpt, observations, "", "pdf-ok-ocr"), excerpt


class RealDrug483KeptTest(unittest.TestCase):
    """실측 오판 두 건 — 게이트를 통과해야 한다."""

    def test_premise_raw_excerpt_is_misjudged(self):
        """전제: 상용구가 섞인 excerpt 를 그대로 넣으면 Unrelated 다(이 결함의 재현)."""
        for text, est in ((_CORIXA, "Drug Manufacturer"),
                          (_LINDE, "Medical Gas Manufacturer and Distributor")):
            self.assertEqual(
                compute_relevance("x", est, "483", f._extract_fda483_excerpt(text)), "Unrelated")

    def test_corixa_drug_manufacturer_is_kept(self):
        item, _ = _item(_CORIXA, "Corixa Corporation", "Drug Manufacturer", "195646")
        self.assertIsNotNone(item)

    def test_linde_medical_gas_is_kept(self):
        item, _ = _item(_LINDE, "Linde Inc", "Medical Gas Manufacturer and Distributor", "195279")
        self.assertIsNotNone(item)

    def test_stored_excerpt_is_unchanged(self):
        """게이트 입력만 고친다 — raw 에 저장되는 excerpt 는 파서 산출 그대로."""
        item, excerpt = _item(_CORIXA, "Corixa Corporation", "Drug Manufacturer", "195646")
        self.assertEqual(item.raw_payload["fda483_excerpt"], excerpt)


class BoilerplateStripTest(unittest.TestCase):

    def test_back_page_and_checkbox_removed(self):
        for text in (_CORIXA, _LINDE):
            stripped = f.strip_483_form_boilerplate(text).lower()
            self.assertNotIn("cosmetic", stripped)
            self.assertNotIn("medical device", stripped)

    def test_truncated_back_page_removed(self):
        """excerpt 는 1500자에서 잘리므로 뒷면 인용문이 중간에서 끊긴다 — 그래도 지운다."""
        cut = ("OBSERVATION 1 Stability program absent. The observations of objectionable "
               "conditions and practices listed on the front of this form are reported: "
               "1. Pursuant to Section 704(b) of the Federal Food, Drug and Cosm")
        stripped = f.strip_483_form_boilerplate(cut)
        self.assertIn("Stability program absent.", stripped)
        self.assertNotIn("704(b)", stripped)

    def test_observation_body_is_kept(self):
        stripped = f.strip_483_form_boilerplate(_CORIXA)
        self.assertIn("Aseptic processing areas are deficient", stripped)
        self.assertIn("contact plates", stripped)

    def test_empty(self):
        self.assertEqual(f.strip_483_form_boilerplate(""), "")


class GenuineOutOfScopeStillDroppedTest(unittest.TestCase):
    """상용구만 지운다 — 관찰 본문에 실제로 있는 기기·화장품 단서는 그대로 걸린다."""

    _BACK = _LINDE[_LINDE.index("The observations of objectionable"):]

    def test_real_device_observation_still_dropped(self):
        body = ("OBSERVATION 1 Procedures for corrective and preventive action have not been "
                "adequately established, as required by 21 CFR 820.100. Specifically, complaints "
                "for the medical device were not investigated. ") + self._BACK
        nrow = _nrow("Acme Sauna Co", "Manufacturer", "999001")
        self.assertTrue(is_out_of_scope_483("Manufacturer", f.strip_483_form_boilerplate(body)))
        self.assertIsNone(f._to_item(nrow, body, [], "", "pdf-ok"))

    def test_real_cosmetic_observation_still_dropped(self):
        body = ("OBSERVATION 1 Your firm manufactures cosmetic lotions without adequate "
                "cleaning of mixing tanks. ") + self._BACK
        nrow = _nrow("Acme Beauty", "Manufacturer", "999002")
        self.assertIsNone(f._to_item(nrow, body, [], "", "pdf-ok"))

    def test_checkbox_alone_does_not_make_device(self):
        """유형이 그냥 'Manufacturer' 인 의약품 483 — 체크박스 안내문만으로 기기 판정하지 않는다."""
        head = _LINDE[:_LINDE.index("DISTRICT OFFICE")]
        body = head + " OBSERVATION 1 Batch records were not reviewed before release."
        self.assertFalse(is_out_of_scope_483("Manufacturer", f.strip_483_form_boilerplate(body)))
        self.assertIsNotNone(f._to_item(_nrow("Acme Pharma", "Manufacturer", "999003"),
                                        body, [], "", "pdf-ok"))


if __name__ == "__main__":
    unittest.main()
