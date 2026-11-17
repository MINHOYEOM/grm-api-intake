"""brief_metrics_ledger 표적 테스트 — 지표 정의(재측정 1116)·멱등 누적·CSV."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

import brief_metrics_ledger as bml


def card(**kw):
    base = {"card_type": "규제 소식", "title_issue": "t", "summary": "s", "key_facts": ["a"],
            "implication": "", "checks": [], "hook_line": ""}
    base.update(kw)
    return base


def brief(cards, run="2026-11-23"):
    return {"brief": {"run_date_kst": run}, "cards": cards}


class MeasureTest(unittest.TestCase):
    def test_phrase_counts_cards_and_occurrences(self):
        m = bml.measure(brief([
            card(summary="필요가 있다. 필요가 있다.", implication="확인할 차례다."),
            card(summary="무관"),
        ]))
        self.assertEqual((m["pilyo_ga_itda_cards"], m["pilyo_ga_itda_occ"]), (1, 2))
        self.assertEqual(m["hwakin_charye_cards"], 1)
        self.assertEqual(m["cards"], 2)

    def test_date8_only_standalone_eight_digits(self):
        m = bml.measure(brief([card(summary="20240601 에 실사"), card(summary="123456789012"), card(summary="2024-06-01")]))
        self.assertEqual(m["date8_cards"], 1)

    def test_checks_question_ratio(self):
        m = bml.measure(brief([card(checks=["이것은 맞는가?", "점검하라"])]))
        self.assertEqual((m["checks_question"], m["checks_total"]), (1, 2))
        self.assertEqual(bml.derived(m)["checks_question_ratio"], 0.5)

    def test_deep_excludes_original_and_citation(self):
        da = {"x": "가능성이 있다 체계적", "original": "체계적 가능성이 있다", "citation": "전반"}
        m = bml.measure(brief([card(card_type="FDA 483", deep_analysis=da)]))
        self.assertEqual(m["deep_ganeungseong_cards"], 1)
        self.assertEqual(m["deep_ganeungseong_occ"], 1)
        self.assertEqual(m["deep_jeonban_cards"], 0)

    def test_483_deadline_and_items(self):
        da = {"required_remediation": {"deadline": "15영업일 이내 서면 회신", "items": ["a"]}}
        m = bml.measure(brief([card(card_type="FDA 483 실사", deep_analysis=da),
                               card(card_type="행정처분", deep_analysis=da)]))
        self.assertEqual((m["deep_483_cards"], m["deep_483_deadline_15days"], m["deep_483_items_nonempty"]), (1, 1, 1))

    def test_jijeokhaetda_ratio(self):
        m = bml.measure(brief([card(implication="앞 문장이다. FDA가 지적했다."),
                               card(implication="좋다."), card(implication="")]))
        self.assertEqual(m["implication_end_jijeokhaetda_cards"], 1)
        self.assertEqual(m["implication_nonempty_cards"], 2)
        self.assertEqual(bml.derived(m)["implication_end_jijeokhaetda_ratio"], 0.5)

    def test_empty_brief_no_division_error(self):
        m = bml.measure(brief([]))
        self.assertIsNone(bml.derived(m)["hook_line_ratio"])

    def test_info_and_admin(self):
        m = bml.measure(brief([
            card(key_facts=["적용 대상: 제조업체"]), card(key_facts=[]),
            card(card_type="행정처분", summary="식약처가 제약사에 처분", implication="행정처분의 기준 별표"),
        ]))
        self.assertEqual((m["info_cards"], m["info_keyfacts_applies_to"], m["info_keyfacts_empty"]), (2, 1, 1))
        self.assertEqual((m["admin_summary_generic_subject"], m["admin_implication_clause_only"]), (1, 1))


class LedgerTest(unittest.TestCase):
    def test_upsert_idempotent_and_sorted(self):
        a = {"run_date_kst": "2026-11-23", "cards": 1}
        b = {"run_date_kst": "2026-11-16", "cards": 2}
        rows = bml.upsert(bml.upsert([], a), b)
        rows = bml.upsert(rows, {"run_date_kst": "2026-11-23", "cards": 9})
        self.assertEqual([r["run_date_kst"] for r in rows], ["2026-11-16", "2026-11-23"])
        self.assertEqual(rows[1]["cards"], 9)

    def test_cli_writes_jsonl_and_csv_and_reads_only(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "brief_web_2026_11_23.json"
            payload = json.dumps(brief([card(summary="필요가 있다")]), ensure_ascii=False)
            src.write_text(payload, encoding="utf-8")
            led, cs = d / "l.jsonl", d / "l.csv"
            for _ in range(2):  # 두 번 돌려도 한 줄
                self.assertEqual(bml.main([str(src), "--ledger", str(led), "--csv", str(cs)]), 0)
            lines = led.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["pilyo_ga_itda_cards"], 1)
            with cs.open(encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 1)
            self.assertEqual(src.read_text(encoding="utf-8"), payload)

    def test_missing_run_date_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text(json.dumps({"brief": {}, "cards": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                bml.record_for(p)


if __name__ == "__main__":
    unittest.main()
