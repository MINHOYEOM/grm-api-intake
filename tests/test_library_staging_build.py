from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import library_staging_build as builder


class LibraryStagingBuildTest(unittest.TestCase):
    def test_derives_only_mfds_guidance_and_notice_with_public_fields(self):
        item = {
            "document_id": "data0011-1", "headline": "품질 가이드",
            "official_url": "https://mfds.example/1", "type_or_class": "guidance-industry",
            "date_iso": "2026-07-20", "raw_payload": {"private": "no"},
        }
        row = builder.derive_item(item, "mfds")
        self.assertEqual(row["title_ko"], "품질 가이드")
        self.assertEqual(set(row) - set(builder.CATALOG_FIELDS), set())
        excluded = dict(item, document_id="x", type_or_class="safety-letter")
        self.assertIsNone(builder.derive_item(excluded, "mfds"))

    def test_merge_preserves_curated_english_and_optional_links(self):
        baseline = [{
            "id": "data0011-1", "title_en": "Quality Guide", "title_ko": "옛 제목",
            "doc_type": "guidance-industry", "official_url": "https://old",
            "pdf_url": "https://pdf",
        }]
        incoming = [{
            "id": "data0011-1", "title_en": "새 제목", "title_ko": "새 제목",
            "doc_type": "guidance-industry", "official_url": "https://new",
        }]
        candidate = builder.merge_candidate(baseline, incoming, source="mfds")
        self.assertEqual(candidate[0]["title_en"], "Quality Guide")
        self.assertEqual(candidate[0]["title_ko"], "새 제목")
        self.assertEqual(candidate[0]["pdf_url"], "https://pdf")

    def test_build_writes_staging_and_diff_without_live_swap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = root / "library"
            live.mkdir()
            for source in ("mfds", "ich"):
                (live / f"{source}.json").write_text(
                    json.dumps({"items": [{"id": f"{source}-old", "title_en": "Old",
                        "official_url": "https://old"}]}), encoding="utf-8")
            report = builder.build(
                baseline_dir=live, staging_dir=root / "staging", report_path=root / "diff.json",
                mfds_items=[], ich_items=[{"document_id": "ich-new", "headline": "Q New",
                    "official_url": "https://ich.example/new", "type_or_class": "guideline-topic"}],
                run_date=date(2026, 7, 20),
            )
            live_after = json.loads((live / "ich.json").read_text(encoding="utf-8"))
            staged = json.loads((root / "staging" / "ich.json").read_text(encoding="utf-8"))
        self.assertEqual(live_after["items"][0]["id"], "ich-old")
        self.assertEqual(report["sources"]["ich"]["new_count"], 1)
        self.assertEqual(len(staged["items"]), 2)
        self.assertFalse(report["live_catalog_swapped"])

