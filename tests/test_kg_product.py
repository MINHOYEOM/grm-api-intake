#!/usr/bin/env python3
"""지적↔제품군 엣지(111 · kg_product.py) tests. 오프라인."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import kg_product as kp

_ROOT = Path(__file__).resolve().parent.parent
_S111 = (_ROOT / "web" / "migrations" / "111_finding_product_edges.sql").read_text(encoding="utf-8")


def _pairs(name):
    return {(e["axis"], e["value"]) for e in kp.classify_name(name)}


class NameRules(unittest.TestCase):
    def test_tablet_with_strength_or_ingredient(self):
        self.assertEqual(_pairs("트롬빅스정(클로피도그렐황산수소염)"), {("dosage_form", "tablet"), ("modality", "Chemical")})
        self.assertEqual(_pairs("리트라젠듀오정2.5/1000mg"), {("dosage_form", "tablet"), ("modality", "Chemical")})

    def test_herb_ending_in_jeong_is_not_a_tablet(self):
        self.assertEqual(_pairs("보위황정"), set())

    def test_bare_suffix_without_corroboration_is_skipped(self):
        for n in ("이프펜정", "탄시나정", "자닥신주", "리코리스주"):
            self.assertEqual(_pairs(n), set(), n)

    def test_oral_form_without_corroboration_gets_form_but_not_modality(self):
        self.assertEqual(_pairs("세코라시럽"), {("dosage_form", "syrup")})
        self.assertEqual(_pairs("이모나캡슐"), {("dosage_form", "capsule")})

    def test_plant_extract_and_enzyme_never_chemical(self):
        self.assertEqual(_pairs("펠라고시럽(펠라고니움시도이데스11%에탄올추출물)"), {("dosage_form", "syrup")})
        self.assertEqual(_pairs("세라타제정(스트렙토키나제,스트렙토도르나제)"), {("dosage_form", "tablet")})

    def test_xr_in_brand_is_not_extract(self):
        self.assertIn(("modality", "Chemical"), _pairs("듀오메트엑스알정500/10밀리그램"))

    def test_biologic_needs_literal_term_not_brand(self):
        self.assertIn(("modality", "Biologic"), _pairs("옵디보주240mg(니볼루맙,유전자재조합)"))
        self.assertIn(("modality", "Biologic"), _pairs("비에녹스주(클로스트리디움보툴리눔독소A형)"))
        self.assertEqual(_pairs("자닥신주"), set())

    def test_injection_is_not_modality(self):
        self.assertEqual(_pairs("타포신주(테이코플라닌)"), {("dosage_form", "injection")})

    def test_out_of_scope_products_unlinked(self):
        for n in ("쉴드맥스라이트보건용마스크(KF80)(대형)", "브이앤치약(침강탄산칼슘)", "농림자소엽", "한강멸균거즈(3호)"):
            self.assertEqual(_pairs(n), set(), n)

    def test_multi_item_list_splits_on_top_level_commas(self):
        self.assertEqual(kp._split_names("가정(나,다),라캡슐"), ["가정(나,다)", "라캡슐"])


class Text483(unittest.TestCase):
    def test_vaccine_and_tablet(self):
        e = kp.classify_483_text("The building used for the manufacture of viral vaccine drug substance is not suitable.")
        self.assertEqual({(x["axis"], x["value"]) for x in e}, {("modality", "Biologic")})
        e = kp.classify_483_text("Aspirin E/C Tablets failed in-process acid stage testing.")
        self.assertEqual({(x["axis"], x["value"]) for x in e},
                         {("dosage_form", "tablet"), ("modality", "Chemical")})

    def test_no_signal_no_edge(self):
        self.assertEqual(kp.classify_483_text("Procedures for cleaning were not followed. sterile area."), [])

    def test_only_483_is_read_for_text(self):
        wl = {"finding_id": "f", "agency": "FDA", "document_type": "Center for Biologics Evaluation and Research",
              "finding_text": "vaccine tablets monoclonal antibody", "raw": None}
        self.assertEqual(kp.classify_finding(wl), [])
        hc = {"finding_id": "f", "agency": "HC", "document_type": "hc-inspection", "finding_text": "tablets", "raw": None}
        self.assertEqual(kp.classify_finding(hc), [])

    def test_mfds_reads_product_field(self):
        r = {"finding_id": "f", "agency": "MFDS", "document_type": "recall-quality", "finding_text": "x",
             "raw": {"PRDUCT": "뉴빅스정(클로피도그렐황산염)"}}
        self.assertIn(("dosage_form", "tablet"), {(e["axis"], e["value"]) for e in kp.classify_finding(r)})
        self.assertEqual(kp.classify_finding({**r, "raw": {}}), [])
        self.assertEqual(kp.classify_finding({**r, "raw": {"manufacturer": "정"}}), [])


class Stats(unittest.TestCase):
    def test_ratio_and_confidence_floor(self):
        rows = [{"finding_id": "a", "agency": "MFDS", "document_type": "recall-quality", "finding_text": "",
                 "raw": {"PRDUCT": "뉴빅스정(클로피도그렐황산염)"}},
                {"finding_id": "b", "agency": "MFDS", "document_type": "recall-quality", "finding_text": "",
                 "raw": {"PRDUCT": "농림자소엽"}}]
        edges, st = kp.build_product_edges(rows)
        self.assertEqual(st["by_type"]["MFDS:recall-quality"], {"findings": 2, "linked": 1, "ratio": 0.5})
        self.assertTrue(all(e["confidence"] >= kp.MIN_CONFIDENCE for e in edges))

    def test_emit_sql_is_idempotent_and_escapes(self):
        sql = kp.emit_sql([{"finding_id": "f'1", "axis": "modality", "value": "Chemical", "confidence": 0.85,
                            "basis": "b", "evidence": "o'x"}])
        self.assertIn("on conflict do nothing", sql)
        self.assertIn("'f''1'", sql)
        self.assertIn("join public.findings", sql)


class Migration111(unittest.TestCase):
    def test_additive_and_locked_down(self):
        code = "\n".join(l for l in _S111.splitlines() if not l.strip().startswith("--")).lower()
        for word in ("drop ", "delete from", "truncate ", "update ", "insert "):
            self.assertNotIn(word, code)
        self.assertIn("create table if not exists public.finding_product", code)
        self.assertIn("revoke all on table public.finding_product from public, anon, authenticated", code)
        self.assertIn("enable row level security", code)
        self.assertIn("confidence >= 0.8", code)

    def test_vocabulary_matches_code(self):
        for v in ("tablet", "capsule", "syrup", "ointment", "injection", "Chemical", "Biologic"):
            self.assertIn(f"'{v}'", _S111)


class Runner(unittest.TestCase):
    def test_write_edges_idempotent_and_skips_when_unapplied(self):
        e = [{"finding_id": "f", "axis": "modality", "value": "Chemical", "confidence": 0.85, "basis": "b", "evidence": ""}]
        with mock.patch("requests.post", return_value=mock.Mock(status_code=404)):
            self.assertIsNone(kp.write_edges("https://x.supabase.co", "k", e))
        with mock.patch("requests.post", return_value=mock.Mock(status_code=201)) as post:
            self.assertEqual(kp.write_edges("https://x.supabase.co", "k", e), 1)
        kw = post.call_args.kwargs
        self.assertIn("ignore-duplicates", kw["headers"]["Prefer"])
        self.assertEqual(kw["params"], {"on_conflict": "finding_id,axis,value"})


if __name__ == "__main__":
    unittest.main()
