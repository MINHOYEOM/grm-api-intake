import datetime as dt
import unittest
from unittest.mock import patch

import card_scaffold as cs
import collect_ich as ich


class IchTrackingTest(unittest.TestCase):
    def test_static_guideline_snapshots_are_tier1(self) -> None:
        html = """
        <html><body>
          <h2>Q12 Lifecycle Management</h2>
          <h2>M7 Mutagenic Impurities</h2>
        </body></html>
        """
        with patch.object(ich, "_get_html", return_value=html), \
                patch.object(ich.time, "sleep", return_value=None):
            items, err = ich._collect_page(
                "quality-guidelines",
                ich.TYPE_ICH_GUIDELINE,
                True,
                dt.date(2026, 6, 6),
            )

        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item.signal_tier == "Tier 1" for item in items))

    def test_ich_consultation_scaffold_goes_to_watch(self) -> None:
        row = {
            "source": cs.SOURCE_ICH,
            "document_id": "ich-consult-q1",
            "headline": "[Public Consultation] Q1 Stability Testing",
            "type_or_class": "ich-consultation",
            "signal_tier": "Tier 2",
            "official_url": "https://www.ich.org/page/public-consultations",
            "date": "",
            "language": "EN",
        }
        raw = {"section_title": "Q1 Stability Testing"}

        card = cs.build_card_scaffold(row, raw)

        self.assertEqual(card.kind, "ich")
        self.assertEqual(card.section, "watch")


if __name__ == "__main__":
    unittest.main()
