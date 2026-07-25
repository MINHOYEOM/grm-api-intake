"""자료실 변경 이력(library_updates.py) 테스트 — 누적·멱등·상한 계약 고정."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import library_updates as lu


def _report(sources: dict, *, generated_on: str = "2026-07-27") -> dict:
    return {"schema_version": "grm-library-staging-diff/v1",
            "generated_on": generated_on, "sources": sources}


def _detail(*, new=(), changed=(), removed=(), total=10) -> dict:
    return {"new_ids": list(new), "changed_ids": list(changed),
            "removed_ids": list(removed), "candidate_count": total}


class BuildEntryTest(unittest.TestCase):
    def test_no_change_yields_no_entry(self):
        """변경 0이면 항목을 만들지 않는다 — '변경 없음'은 항목 부재로 표현한다."""
        report = _report({"ema": _detail(), "who": _detail()})
        self.assertIsNone(lu.build_entry(report))

    def test_only_changed_sources_are_recorded(self):
        report = _report({"ema": _detail(new=["ema-2", "ema-1"]), "who": _detail()})
        entry = lu.build_entry(report)
        self.assertEqual(entry["date"], "2026-07-27")
        self.assertEqual(list(entry["sources"]), ["ema"])
        self.assertEqual(entry["sources"]["ema"]["new_ids"], ["ema-1", "ema-2"])
        self.assertEqual(entry["sources"]["ema"]["total_count"], 10)
        self.assertFalse(entry["sources"]["ema"]["truncated"])

    def test_removed_only_is_still_an_update(self):
        entry = lu.build_entry(_report({"pics": _detail(removed=["pics-9"])}))
        self.assertEqual(entry["sources"]["pics"]["removed_ids"], ["pics-9"])

    def test_oversized_bucket_is_capped_and_flagged(self):
        """상한을 넘겨 자른 사실은 truncated 로 남긴다 — 조용한 절삭 금지."""
        ids = [f"mfds-{n:04d}" for n in range(lu.MAX_IDS_PER_BUCKET + 5)]
        entry = lu.build_entry(_report({"mfds": _detail(new=ids)}))
        self.assertEqual(len(entry["sources"]["mfds"]["new_ids"]), lu.MAX_IDS_PER_BUCKET)
        self.assertTrue(entry["sources"]["mfds"]["truncated"])


class MergeEntryTest(unittest.TestCase):
    def test_new_date_is_prepended_newest_first(self):
        entries = lu.merge_entry([{"date": "2026-07-20", "sources": {}}],
                                 {"date": "2026-07-27", "sources": {}})
        self.assertEqual([e["date"] for e in entries], ["2026-07-27", "2026-07-20"])

    def test_same_date_merges_as_union_not_duplicate(self):
        """같은 날 재실행은 항목을 늘리지 않고 합집합으로 합친다."""
        first = lu.build_entry(_report({"ema": _detail(new=["ema-1"])}))
        second = lu.build_entry(_report({"ema": _detail(new=["ema-2"], total=11)}))
        entries = lu.merge_entry(lu.merge_entry([], first), second)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["sources"]["ema"]["new_ids"], ["ema-1", "ema-2"])
        self.assertEqual(entries[0]["sources"]["ema"]["total_count"], 11)

    def test_history_is_capped_to_max_entries(self):
        entries: list[dict] = []
        for day in range(lu.MAX_ENTRIES + 3):
            entries = lu.merge_entry(entries, {"date": f"2026-01-{day + 1:02d}",
                                               "sources": {}})
        self.assertEqual(len(entries), lu.MAX_ENTRIES)


class AppendUpdateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.report_path = self.root / "diff.json"
        self.updates_path = self.root / "library_updates.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_report(self, sources: dict, *, generated_on="2026-07-27") -> None:
        self.report_path.write_text(
            json.dumps(_report(sources, generated_on=generated_on)), encoding="utf-8")

    def test_appends_and_is_idempotent_on_rerun(self):
        self._write_report({"ema": _detail(new=["ema-1"])})
        self.assertIsNotNone(lu.append_update(report_path=self.report_path,
                                              updates_path=self.updates_path))
        first = self.updates_path.read_bytes()
        lu.append_update(report_path=self.report_path, updates_path=self.updates_path)
        self.assertEqual(self.updates_path.read_bytes(), first,
                         "같은 리포트 재실행은 파일을 바꾸지 않아야 한다")

    def test_no_change_leaves_the_file_untouched(self):
        self.updates_path.write_text(
            json.dumps({"schema_version": lu.SCHEMA_VERSION, "entries": []}),
            encoding="utf-8")
        before = self.updates_path.read_bytes()
        self._write_report({"ema": _detail()})
        self.assertIsNone(lu.append_update(report_path=self.report_path,
                                           updates_path=self.updates_path))
        self.assertEqual(self.updates_path.read_bytes(), before)

    def test_second_week_keeps_the_first_week(self):
        self._write_report({"ema": _detail(new=["ema-1"])}, generated_on="2026-07-20")
        lu.append_update(report_path=self.report_path, updates_path=self.updates_path)
        self._write_report({"who": _detail(new=["who-1"])}, generated_on="2026-07-27")
        lu.append_update(report_path=self.report_path, updates_path=self.updates_path)
        payload = json.loads(self.updates_path.read_text(encoding="utf-8"))
        self.assertEqual([e["date"] for e in payload["entries"]],
                         ["2026-07-27", "2026-07-20"])
        self.assertEqual(payload["schema_version"], lu.SCHEMA_VERSION)

    def test_missing_history_file_is_created(self):
        self._write_report({"ema": _detail(new=["ema-1"])})
        self.assertFalse(self.updates_path.exists())
        lu.append_update(report_path=self.report_path, updates_path=self.updates_path)
        self.assertTrue(self.updates_path.exists())


class ShippedHistoryTest(unittest.TestCase):
    """저장소에 커밋된 이력 파일이 스키마 계약을 지키는지."""

    def test_committed_file_matches_the_schema(self):
        path = Path(__file__).resolve().parents[1] / "web" / "data" / "library_updates.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], lu.SCHEMA_VERSION)
        self.assertIsInstance(payload["entries"], list)
        dates = [e["date"] for e in payload["entries"]]
        self.assertEqual(dates, sorted(dates, reverse=True), "최신 우선 정렬이어야 한다")


if __name__ == "__main__":
    unittest.main()
