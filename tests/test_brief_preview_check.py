import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import brief_preview_check as preview


ISSUE = "brief_web_2026_11_23.json"
LABELS = ("개인 실명", "영문 한글", "빈 칸", "옛 실사 표시", "묶음 카드")


class TestBriefPreviewCheck(unittest.TestCase):
    def test_published_issue_cli(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(preview.main(["2026-11-23"]), 0)
        lines = output.getvalue().splitlines()
        self.assertIn("5유형 점검", lines[0])
        rows = {line.split("|")[1].strip(): line for line in lines[4:]}
        self.assertEqual(tuple(rows), LABELS)
        self.assertIn("누락·불일치 0건", rows["옛 실사 표시"])
        self.assertNotIn("ko 0장", rows["옛 실사 표시"])
        self.assertNotIn("ko 0상자", rows["묶음 카드"])

    def test_preview_treats_missing_and_null_brief_as_empty(self):
        doc = preview.read_brief(preview.DATA / "briefs" / ISSUE)
        doc.pop("brief", None)
        expected = preview.check_preview({**doc, "brief": {}})
        self.assertEqual(tuple(label for label, _, _ in expected), LABELS)

        for metadata in ({}, {"brief": None}):
            with self.subTest(metadata=metadata):
                self.assertEqual(preview.check_preview({**doc, **metadata}), expected)

    def test_preview_warns_on_mutations(self):
        doc = preview.read_brief(preview.DATA / "briefs" / ISSUE)
        scaffold = preview.read_brief(preview.DATA / "scaffolds" / ISSUE)
        card = next(card for card in doc["cards"] if card["agency"] == "FDA")
        source = next(source for source in scaffold["cards"] if source["id"] == card["id"])
        source["quotes"] = [{"original": "Dear Mr. Testerson,"}]
        card["en"]["summary"] = "Testerson 한글"
        card["summary"] = ""

        annotate = preview.render._annotate_firm_groups

        def omit_display(views, cards, tr):
            annotate(views, cards, tr)
            for view in views:
                view.pop("old_insp", None)
                view.pop("fgroup_key", None)

        with patch.object(preview.render, "_annotate_firm_groups", omit_display):
            rows = {
                label: (warned, details)
                for label, warned, details in preview.check_preview(doc, scaffold)
            }
        for label in LABELS:
            with self.subTest(label=label):
                self.assertTrue(rows[label][0])
        self.assertIn(f"{card['id']}.en.summary", rows["개인 실명"][1])
        self.assertNotIn("Testerson", rows["개인 실명"][1])
        self.assertIn("en.summary", rows["영문 한글"][1])
        self.assertIn(f"{card['id']}.summary", rows["빈 칸"][1])
        self.assertIn("예상 그룹 상자와 불일치", rows["묶음 카드"][1])
