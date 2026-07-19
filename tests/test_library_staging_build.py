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

    def test_existing_curated_fields_win_for_every_source(self):
        for source in ("mfds", "ich"):
            with self.subTest(source=source):
                item_id = f"{source}-curated"
                baseline = [{
                    "id": item_id, "code": "CURATED-CODE",
                    "title_en": "Curated English title", "title_ko": "큐레이션 제목",
                    "doc_type": "curated-type", "official_url": "https://curated/official",
                    "pdf_url": "https://curated/pdf", "ko_url": "https://curated/ko",
                }]
                incoming = [{
                    "id": item_id, "code": "COLLECTOR-CODE",
                    "title_en": "Collector title", "title_ko": "수집기 제목",
                    "doc_type": "collector-type", "official_url": "https://collector/official",
                    "pdf_url": "https://collector/pdf", "ko_url": "https://collector/ko",
                    "published_date": "2026-07-20",
                }]
                candidate = builder.merge_candidate(baseline, incoming)
                self.assertEqual(candidate[0], {
                    **baseline[0], "published_date": "2026-07-20",
                })

    def test_new_item_keeps_collector_fields(self):
        incoming = [{
            "id": "ich-new", "code": "Q99", "title_en": "New title",
            "doc_type": "ich-guideline", "official_url": "https://new",
            "pdf_url": "https://new/pdf",
        }]
        self.assertEqual(builder.merge_candidate([], incoming), incoming)

    def test_unknown_catalog_fields_fail_instead_of_being_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "future_field"):
            builder.merge_candidate(
                [{"id": "existing", "future_field": "curated"}], [],
            )

    def test_catalog_fields_cover_all_live_library_item_fields(self):
        library_dir = Path(__file__).parents[1] / "web" / "data" / "library"
        live_fields = set()
        for path in library_dir.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for item in payload["items"]:
                live_fields.update(item)
        self.assertEqual(live_fields, set(builder.CATALOG_FIELDS))

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
