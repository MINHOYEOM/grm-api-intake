"""backfill_483_detail_refresh — 저장된 483 상세만 제자리 교체(2026-11-10)."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_483_detail_refresh as b  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _text(doc: str) -> str:
    with open(os.path.join(FIX, f"fda483_{doc}_pagebreak.txt"), encoding="utf-8") as fh:
        return fh.read()


def _row(raw: dict) -> dict:
    return {"raw_signal_id": "rawsig-x", "document_id": "fda483-195434",
            "raw_json": json.dumps(raw, ensure_ascii=False)}


OLD_OBS = [{
    "number": "2",
    "deficiency": ("The firm is not testing each donation for evidence of infection due to relevant "
                   "transfusion transmitted infections (RTTI)."),
    "detail": "Specifically, when a donor tests reactive. Carmelita F. Bibby, Consumer Safety Officer",
}]


class RefreshTest(unittest.TestCase):
    def test_only_detail_changes_and_other_keys_survive(self):
        raw = {"fda483_body_full": _text("195434"), "fda483_inspectors": ["X Y"],
               "establishment_type": "Plasmapheresis Center", b._OBS_KEY: OLD_OBS}
        patched: dict = {}
        rep = b.run([_row(raw)], dry_run=False,
                    patch_fn=lambda rid, r: patched.update(r),
                    text_fn=lambda u: self.fail("PDF 를 받으면 안 된다"))
        self.assertEqual(rep.patched, 1)
        self.assertEqual(rep.from_body_full, 1)
        self.assertEqual(patched["fda483_inspectors"], ["X Y"])
        self.assertEqual(patched["fda483_body_full"], raw["fda483_body_full"])
        obs = patched[b._OBS_KEY]
        self.assertEqual(obs[0]["deficiency"], OLD_OBS[0]["deficiency"])
        self.assertTrue(obs[0]["detail"].endswith("testing laboratory."))
        self.assertNotIn("Carmelita", obs[0]["detail"])
        self.assertEqual(rep.after.signer_or_footer_trace, 0)
        self.assertEqual(rep.before.signer_or_footer_trace, 1)

    def test_dry_run_does_not_patch(self):
        raw = {"fda483_body_full": _text("195434"), b._OBS_KEY: OLD_OBS}
        rep = b.run([_row(raw)], dry_run=True, patch_fn=lambda rid, r: self.fail("쓰면 안 된다"))
        self.assertEqual(rep.observations_changed, 1)
        self.assertEqual(rep.patched, 0)

    def test_unmatched_observation_keeps_old_detail_tidied(self):
        old = [{"number": "9", "deficiency": "Not in the new text.",
                "detail": "Specifically, O© Tablets were shipped. Jane Q. Doe, DDC Investigator"}]
        out, matched, unmatched = b.refreshed_observations(old, [])
        self.assertEqual((matched, unmatched), (0, 1))
        self.assertEqual(out[0]["detail"], "Specifically, [redacted] Tablets were shipped.")

    def test_pdf_fallback_when_no_body_full(self):
        raw = {"pdf_url": "https://x/y.pdf", b._OBS_KEY: OLD_OBS}
        rep = b.run([_row(raw)], dry_run=True, sleep_fn=lambda s: None,
                    text_fn=lambda u: (_text("195434"), "ok"))
        self.assertEqual(rep.from_pdf, 1)
        self.assertEqual(rep.observations_changed, 1)


_LONG_DEF = ("There is a failure to thoroughly review any unexplained discrepancy and the failure of a batch "
             "or any of its components to meet any of its specifications whether or not the batch has been "
             "already distributed including the investigation records for the lots listed in the table that "
             "follows which were never closed by the quality unit")


class OldStorageCapTest(unittest.TestCase):
    """옛 저장 상한(표제 280자+"..."·상세 1,200자)에 잘린 관찰을 잘리지 않은 판으로 되살린다."""

    def test_truncated_deficiency_is_restored_with_number_and_prefix(self):
        old = [{"number": "3", "deficiency": _LONG_DEF[:280].rstrip() + "...", "detail": _LONG_DEF[280:]}]
        new = [{"number": "3", "deficiency": _LONG_DEF, "detail": ""}]
        out, matched, unmatched = b.refreshed_observations(old, new)
        self.assertEqual((matched, unmatched), (1, 0))
        self.assertEqual(out[0]["deficiency"], _LONG_DEF)
        self.assertEqual(out[0]["detail"], "")

    def test_truncated_deficiency_needs_same_number(self):
        old = [{"number": "3", "deficiency": _LONG_DEF[:280].rstrip() + "...", "detail": "x"}]
        new = [{"number": "4", "deficiency": _LONG_DEF, "detail": ""}]
        out, matched, _ = b.refreshed_observations(old, new)
        self.assertEqual(matched, 0)
        self.assertTrue(out[0]["deficiency"].endswith("..."))

    def test_1200_detail_is_extended_even_if_deficiency_differs(self):
        full = "Specifically, " + "the batch record was not reviewed for lot A. " * 60
        old = [{"number": "1", "deficiency": "Old heading.", "detail": full[:1200]}]
        new = [{"number": "1", "deficiency": "Old heading (rescrubbed).", "detail": full.strip()}]
        out, matched, _ = b.refreshed_observations(old, new)
        self.assertEqual(matched, 1)
        self.assertEqual(out[0]["detail"], full.strip())
        self.assertEqual(out[0]["deficiency"], "Old heading.")

    def test_1200_detail_with_different_head_matches_on_middle_and_tail(self):
        body = " ".join(f"Lot {i} record was not reviewed by the quality unit before release." for i in range(60))
        old_detail = ("SEE REVERSE page junk " + body)[:1200]
        new_detail = "Specifically, " + body
        old = [{"number": "4", "deficiency": "A.", "detail": old_detail}]
        new = [{"number": "4", "deficiency": "A (rescrubbed).", "detail": new_detail}]
        out, matched, _ = b.refreshed_observations(old, new)
        self.assertEqual(matched, 1)
        self.assertEqual(out[0]["detail"], new_detail)

    def test_different_text_with_same_number_is_not_matched(self):
        old = [{"number": "1", "deficiency": "A.", "detail": "Specifically, " + "x " * 600}]
        new = [{"number": "1", "deficiency": "B.", "detail": "Something else entirely. " * 60}]
        _, matched, unmatched = b.refreshed_observations(old, new)
        self.assertEqual((matched, unmatched), (0, 1))

    def test_truncated_only_selects_capped_docs(self):
        capped = {b._OBS_KEY: [{"number": "1", "deficiency": "A.", "detail": "y" * 1200}], "pdf_url": ""}
        fine = {b._OBS_KEY: [{"number": "1", "deficiency": "A.", "detail": "short."}], "pdf_url": ""}
        rep = b.run([_row(capped), _row(fine)], dry_run=True, truncated_only=True)
        self.assertEqual(rep.candidates, 1)
        self.assertTrue(rep.truncated_only)

    def test_residual_diagnostics_show_what_follows_in_source(self):
        cut = ("Specifically, " + " ".join(f"record {i} was not reviewed." for i in range(80)))[:1200]
        text = cut + " and continues here on the next page.\nOBSERVATION 2\nNext."
        rows = [{"number": "1", "deficiency": "A.", "detail": cut}]
        diag = b.diagnose_residual("fda483-1", rows, [{"number": "1", "deficiency": "A.", "detail": cut}], text)
        self.assertEqual(len(diag), 1)
        self.assertTrue(diag[0]["found_in_text"])
        self.assertIn("and continues here", diag[0]["text_after"][:30])

    # [11-11] 옛 수집기가 하위 항목을 따로 떼어 저장한 조각(번호·표제가 새 관찰과 어긋남) — 실측 fda483-175708
    # 의 "…There is no assurance that all incidents/deviations a" 는 다시 읽은 관찰 **1번** 상세 가운데에 있다.
    _FRAG_HEAD = "B. Your procedure, SOP-CAPS-4000101 Notification of Quality Event, states the operator shall " \
                 "complete the Notification of Quality Event Form and submit to Quality systems. "
    _FRAG_TAIL = "There is no assurance that all incidents/deviations a"

    def _fragment(self) -> str:
        filler = " ".join(f"Event {i} was recorded on the batch record but not escalated." for i in range(40))
        body = (self._FRAG_HEAD + filler)[:1195 - len(self._FRAG_TAIL) - 1].rstrip() + " " + self._FRAG_TAIL
        self.assertTrue(b.is_truncated_detail(body), len(body))
        return body

    def test_fragment_cut_sentence_is_completed_from_source(self):
        frag = self._fragment()
        rest = ("re documented for Quality Unit review throughout the subsequent production stages, including "
                "aseptic processing in ISO 5. During the inspection, we observed three different methods.")
        new = [{"number": "1", "deficiency": "There is a failure to thoroughly review.",
                "detail": "Specifically, A. Earlier text. " + frag + rest + " C. Later item."},
               {"number": "2", "deficiency": "Other.", "detail": "Unrelated detail text."}]
        old = [{"number": "2", "deficiency": ".", "detail": frag}]
        out, matched, unmatched = b.refreshed_observations(old, new)
        self.assertEqual((matched, unmatched), (1, 0))
        self.assertEqual(out[0]["number"], "2")                  # 번호·표제는 그대로
        self.assertEqual(out[0]["deficiency"], ".")
        self.assertTrue(out[0]["detail"].startswith(self._FRAG_HEAD.strip()[:60]))
        self.assertTrue(out[0]["detail"].endswith(
            "incidents/deviations are documented for Quality Unit review throughout the subsequent "
            "production stages, including aseptic processing in ISO 5."), out[0]["detail"][-200:])
        self.assertNotIn("three different methods", out[0]["detail"])   # 문장 하나만 붙인다

    def test_fragment_completion_needs_a_unique_hit_and_a_sentence_end(self):
        frag = self._fragment()
        twice = [{"number": "1", "deficiency": "A.", "detail": frag + "re documented."},
                 {"number": "3", "deficiency": "C.", "detail": frag + "re documented."}]
        self.assertIsNone(b.completed_sentence(frag, twice))      # 두 곳 — 어느 쪽인지 모른다
        no_end = [{"number": "1", "deficiency": "A.", "detail": frag + "re documented " + "and more " * 100}]
        self.assertIsNone(b.completed_sentence(frag, no_end))     # 600자 안에 문장 끝 없음
        ended = frag[:-len(self._FRAG_TAIL)].rstrip() + " The deviations were documented."
        self.assertIsNone(b.completed_sentence(ended, [{"number": "1", "detail": ended + " More."}]))  # 이미 끝난 문장

    def test_fragment_completed_from_whole_source_when_new_parse_lost_it(self):
        """[11-12] 새 파서가 그 관찰을 빠뜨려도(new 에 없음) 원문 전체에서 딱 한 곳이면 문장 하나를 잇는다."""
        frag = self._fragment()
        rest = "re documented for Quality Unit review. Next sentence stays out."
        source = "OBSERVATION 1 Heading. Specifically, " + frag + rest + " OBSERVATION 2 Other."
        old = [{"number": "5", "deficiency": ".", "detail": frag}]
        out, matched, unmatched = b.refreshed_observations(old, [], source)
        self.assertEqual((matched, unmatched), (1, 0))
        self.assertTrue(out[0]["detail"].endswith("re documented for Quality Unit review."))
        # 원문 없이는 종전대로(짝 없음)
        self.assertEqual(b.refreshed_observations(old, [])[1:], (0, 1))
        # 원문에 두 곳이면 잇지 않는다
        self.assertIsNone(b.completed_sentence(frag, [], source + " " + frag + rest))
        # 붙일 문장에 양식 머리말 낱말이 섞이면 잇지 않는다(쪽 경계 블록)
        hdr = frag + "TRY TYPE ESTABLISHMENT INSPECTED Livonia MI Producer of Sterile Drugs ii. The ISO 5 area."
        self.assertIsNone(b.completed_sentence(frag, [], hdr))

    def test_truncated_deficiency_completed_from_source(self):
        head = ("Your firm has not ensured that your facility is suitably designed with respect to adequate "
                "separation of operations and that it is properly maintained, including areas protected from "
                "particulate and microbial contamination by appropriate engineering controls that include "
                "protect from heating ventilation and air conditioning (HVAC), air")
        old_def = (head + " " + "z" * 300)[:280] + "..."
        cut = old_def[:-3]
        source = ("OBSERVATION #1 - " + cut + " pressurization, and unidirectional airflow, to prevent "
                  "contamination. OBSERVATION #2 - Next.")
        got = b.completed_deficiency(old_def, source)
        self.assertIsNotNone(got)
        self.assertTrue(got.endswith("unidirectional airflow, to prevent contamination."), got[-80:])
        old = [{"number": "2", "deficiency": old_def, "detail": "Short detail."}]
        out, matched, _ = b.refreshed_observations(old, [{"number": "2", "deficiency": "", "detail": ""}], source)
        self.assertEqual(matched, 1)
        self.assertEqual(out[0]["deficiency"], got)
        self.assertEqual(out[0]["detail"], "Short detail.")
        self.assertIsNone(b.completed_deficiency("Short heading...", source))

    def test_band_helpers(self):
        self.assertTrue(b.is_truncated_deficiency("x" * 280 + "..."))
        self.assertFalse(b.is_truncated_deficiency("Short heading..."))
        self.assertTrue(b.is_truncated_detail("y" * 1200))
        self.assertFalse(b.is_truncated_detail("y" * 3000))


class FirstSentenceNoCapTest(unittest.TestCase):
    def test_long_heading_without_boundary_is_kept_whole(self):
        import collect_fda_483 as c
        head, rest = c._first_sentence(_LONG_DEF)
        self.assertEqual((head, rest), (_LONG_DEF, ""))
        self.assertFalse(head.endswith("..."))


if __name__ == "__main__":
    unittest.main()
