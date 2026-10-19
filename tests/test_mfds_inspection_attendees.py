"""원문 표 구조·공개 범위·복수 표 회귀. 모든 성명은 가명이다."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import backfill_mfds_inspection_attendees as backfill
import mfds_inspection_attendees as parser
import mfds_inspection_attendees_store as store

FIXTURES = Path(__file__).parent / "fixtures" / "mfds_attendees"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def table(rows, *, named=True, heading="실사자"):
    return f"{heading}\n기관\n부서\n" + ("성명\n" if named else "") + rows + "\n3\n실태조사 결과\n"


class ParseTableTest(unittest.TestCase):
    def test_truth_pdfs_anonymized(self):
        first = parser.parse_attendee_table(fixture("celltrion_factory_1.txt"))
        second = parser.parse_attendee_table(fixture("celltrion_factory_2.txt"))
        self.assertEqual((first.table_status, len(first.attendees)), ("named", 4))
        observers = [a for a in first.attendees if a.is_observer]
        self.assertEqual(len(observers), 1)
        self.assertEqual(observers[0].dept, "의료제품실사과")
        self.assertNotIn("참관", observers[0].inspector_name_norm)
        self.assertEqual(len(parser.document_inspector_names(first)), 3)
        self.assertEqual(len(second.attendees), 5)

    def test_three_multi_table_documents(self):
        for doc, count in (("1PJA8DLaWHT", 4), ("1PJESNIyhzw", 4), ("1PJESNIyXBt", 2)):
            with self.subTest(document=doc):
                text = fixture(f"gmpinspect-{doc}.txt")
                self.assertEqual(text.count("실사자"), 2)
                result = parser.parse_attendee_table(text)
                self.assertEqual((result.table_status, result.table_count), ("named", 2))
                self.assertEqual(len(parser.document_inspector_names(result)), count)
                self.assertEqual(len(result.attendees), count)

    def test_one_document_per_name_despite_department_changes(self):
        text = table("식품의약품안전처\n의약품품질과\n가 상 가")
        text += table("경인지방식품의약품안전청\n의료제품실사과\n가상가")
        result = parser.parse_attendee_table(text)
        self.assertEqual(len(result.attendees), 2)
        self.assertEqual(parser.document_inspector_names(result), {"가상가"})

    def test_two_columns_are_unknown_people_not_zero_or_non_observers(self):
        result = parser.parse_attendee_table(table(
            "서울지방식품의약품안전청\n의약품안전관리과\n"
            "서울지방식품의약품안전청\n의약품안전관리과", named=False))
        self.assertEqual(result.table_status, "named_none")
        self.assertTrue(result.attendees)
        self.assertTrue(all(a.inspector_name_norm is None and a.is_observer is None for a in result.attendees))

    def test_absent_and_missing_are_distinct(self):
        self.assertEqual(parser.parse_attendee_table("제조소 현황\n실태조사 결과").table_status, "absent")
        for text in (None, "", " \n"):
            self.assertEqual(parser.parse_attendee_table(text).table_status, "failed")

    def test_missing_boundary_of_either_table_discards_whole_document(self):
        valid = table("식품의약품안전처\n의약품품질과\n가상가")
        invalid = valid.replace("3\n실태조사 결과", "")
        for text in (invalid, invalid + valid, valid + invalid):
            result = parser.parse_attendee_table(text)
            self.assertEqual(result.table_status, "failed")
            self.assertEqual(result.attendees, ())

    def test_partial_rows_and_unrecognized_columns_fail(self):
        valid = table("식품의약품안전처\n의약품품질과\n가상가")
        for text in (valid.replace("가상가", ""), valid.replace("부서", "직급"),
                     valid.replace("가상가", "가상가(팀장)"), valid.replace("안전처", "미확인기관")):
            self.assertEqual(parser.parse_attendee_table(text).table_status, "failed")

    def test_tf_and_whitespace_and_heading_variant(self):
        result = parser.parse_attendee_table(table("식품의약품안전처\n마약류오남용감시단 T/F\n가 상 가 (참관)", heading="실 태 조 사 자"))
        self.assertEqual(result.table_status, "named")
        self.assertEqual(result.attendees[0], parser.Attendee("가상가", "식품의약품안전처", "마약류오남용감시단T/F", True))
        self.assertEqual(parser.document_inspector_names(result), set())

    def test_spelling_is_not_merged(self):
        result = parser.parse_attendee_table(table("식품의약품안전처\n의약품품질과\n가상가\n식품의약품안전처\n의약품품질과\n가상나"))
        self.assertEqual(parser.document_inspector_names(result), {"가상가", "가상나"})


class StoreAndBackfillTest(unittest.TestCase):
    def setUp(self):
        self.record = {"source": "MFDS", "source_kind": "gmp-inspection", "document_id": "doc-a",
                       "raw_sha256": "a" * 64, "raw_json": {"attachment_text": fixture("celltrion_factory_1.txt"), "attachment_file_format": "pdf"}}

    def test_dry_run_counts_unique_document_names_and_no_network(self):
        self.record["raw_json"]["attachment_text"] = fixture("gmpinspect-1PJA8DLaWHT.txt")
        with patch.object(store.requests, "post", side_effect=AssertionError("network")):
            report, planned = backfill.plan([self.record])
        self.assertEqual(report["by_format"]["pdf"]["document_name_links_including_observers"], 4)
        self.assertEqual(report["writes"], 0)
        self.assertEqual(len(planned), 1)
        self.assertNotIn("가상", json.dumps(report, ensure_ascii=False))

    def test_failure_is_logged_and_persisted_without_breaking_intake(self):
        self.record["raw_json"]["attachment_text"] = "실사자\n소속"
        with patch.object(store, "replace_attendees", return_value="") as write, patch.object(store, "log") as log:
            store.sync_attendees("https://example.com", "secret", self.record)
        self.assertEqual(write.call_args.args[-1].table_status, "failed")
        log.assert_called()

    def test_unexpected_parser_exception_becomes_failed_record(self):
        with patch.object(store, "parse_attendee_table", side_effect=ValueError("secret")), patch.object(store, "log") as log:
            result = store.parse_record(self.record)
        self.assertEqual((result.table_status, result.reason), ("failed", "parser_exception:ValueError"))
        self.assertNotIn("secret", str(log.call_args))

    def test_transport_retries_timeout_and_sends_hash(self):
        ok = Mock(ok=True, status_code=200); ok.json.return_value = 4
        with patch.object(store.requests, "post", side_effect=[store.requests.exceptions.Timeout(), ok]) as post:
            self.assertEqual(store.replace_attendees("https://example.com", "secret", self.record,
                parser.parse_attendee_table(self.record["raw_json"]["attachment_text"])), "")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs["json"]["p_raw_sha256"], "a" * 64)

    def test_other_sources_never_call_transport(self):
        for source, kind in (("FDA 483", "inspection"), ("MFDS", "recall")):
            with patch.object(store, "replace_attendees") as write:
                store.sync_attendees("https://example.com", "secret", dict(self.record, source=source, source_kind=kind))
            write.assert_not_called()

    def test_baseline_mismatch_is_detected(self):
        self.assertEqual(backfill.compare_baseline({"x": {"n": 4}}, {"x": {"n": 4}}), [])
        self.assertEqual(len(backfill.compare_baseline({"x": {"n": 4}}, {"x": {"n": 5}})), 1)

    def test_daily_append_hook_is_isolated_and_fda_result_unchanged(self):
        import findings_supabase_append as append
        for source, kind in (("MFDS", "gmp-inspection"), ("FDA 483", "483")):
            record = dict(self.record, source=source, source_kind=kind, raw_signal_id="raw-a")
            with patch.object(append.gf, "validate_raw_signal", return_value=[]), \
                 patch.object(append, "_post_rows", return_value=(201, [{"raw_signal_id": "raw-a"}], "")), \
                 patch.object(store, "sync_attendees", side_effect=RuntimeError("secret")) as sync, \
                 patch("grm_common.log") as log:
                result = append._append_raw_signal("https://example.com", "secret", record)
            self.assertEqual(result, append.SupabaseRawSignalAppendResult("inserted", raw_signal_id="raw-a"))
            self.assertEqual(sync.call_count, int(source == "MFDS"))
            self.assertNotIn("secret", str(log.call_args))

    def test_failed_raw_append_does_not_attempt_attendee_write(self):
        import findings_supabase_append as append
        with patch.object(append.gf, "validate_raw_signal", return_value=[]), \
             patch.object(append, "_post_rows", return_value=(500, None, "http_500")), \
             patch.object(store, "sync_attendees") as sync:
            result = append._append_raw_signal("https://example.com", "secret", self.record)
        self.assertEqual(result.status, "error")
        sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
