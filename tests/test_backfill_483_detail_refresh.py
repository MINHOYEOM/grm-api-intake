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


if __name__ == "__main__":
    unittest.main()
