import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import quiz_lint as ql


class CommittedQuizBankGateTest(unittest.TestCase):
    """Make unittest discovery enforce the committed bank in GitHub CI."""

    def test_committed_quiz_bank_is_lint_clean(self):
        report = ql.lint_quiz_bank()
        self.assertTrue(report.ok, report.format())


class QuizLintTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="quiz_lint_")
        self.root = Path(self._tmp.name)
        self.quiz_path = self.root / "quiz_bank.json"
        self.glossary_path = self.root / "glossary.json"
        self.briefs_dir = self.root / "briefs"
        self.briefs_dir.mkdir()
        self._write(self.glossary_path, [{"id": "gmp", "term_ko": "GMP"}])
        self._write(
            self.briefs_dir / "brief_web_2026_07_12.json",
            {
                "brief": {"publish_date": "2026-07-12"},
                "cards": [{"id": "card-1"}, {"id": "카드-2"}],
            },
        )

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _write(path: Path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def _item(self, **updates):
        item = {
            "id": "q-001",
            "question_ko": "GMP의 정의로 옳은 것은 무엇인가요?",
            "choices": ["정답", "오답 A", "오답 B", "오답 C"],
            "answer_index": 0,
            "explanation_ko": "공개 용어집 정의에 명시된 내용입니다.",
            "source_type": "glossary",
            "source_ref": "gmp",
            "difficulty": "easy",
        }
        item.update(updates)
        return item

    def _lint(self, items):
        self._write(self.quiz_path, items)
        return ql.lint_quiz_bank(self.quiz_path, self.glossary_path, self.briefs_dir)

    @staticmethod
    def _codes(report):
        return [issue.code for issue in report.issues]

    def test_valid_bank_passes_and_reports_counts(self):
        report = self._lint(
            [
                self._item(),
                self._item(
                    id="q-202653-01",
                    question_ko="카드에 명시된 조치는 무엇인가요?",
                    source_type="brief",
                    source_ref="https://grm-solutions.com/briefs/2026-07-12/#card-1",
                    difficulty="normal",
                    week="202653",
                ),
                self._item(
                    id="q-ext",
                    question_ko="공개 문서에서 확인되는 내용은 무엇인가요?",
                    source_type="external",
                    source_ref="https://example.org/source?id=1#part",
                ),
                self._item(
                    id="q-finding",
                    question_ko="공개 지적사항의 내용은 무엇인가요?",
                    source_type="finding",
                    source_ref="https://grm-solutions.com/findings/?finding_id=finding-abc",
                ),
            ]
        )
        self.assertTrue(report.ok, report.format())
        self.assertEqual(report.item_count, 4)
        self.assertEqual(report.source_counts["glossary"], 1)
        self.assertEqual(report.week_counts["202653"], 1)
        self.assertIn("quiz_lint: PASS", report.format())

    def test_invalid_json_and_top_level_contract(self):
        self.quiz_path.write_text("[{", encoding="utf-8")
        report = ql.lint_quiz_bank(self.quiz_path, self.glossary_path, self.briefs_dir)
        self.assertEqual(self._codes(report), ["QUIZ_JSON"])

        self._write(self.quiz_path, {"items": []})
        report = ql.lint_quiz_bank(self.quiz_path, self.glossary_path, self.briefs_dir)
        self.assertEqual(self._codes(report), ["BANK_TYPE"])

        report = self._lint([])
        self.assertEqual(self._codes(report), ["BANK_EMPTY"])

    def test_required_unknown_and_field_types(self):
        item = self._item(answer_index=True, week=202629, extra="x")
        del item["question_ko"]
        report = self._lint([item])
        codes = self._codes(report)
        self.assertIn("REQUIRED_FIELD", codes)
        self.assertIn("UNKNOWN_FIELD", codes)
        self.assertEqual(codes.count("FIELD_TYPE"), 2)

    def test_duplicate_ids_are_rejected(self):
        report = self._lint([self._item(), self._item()])
        self.assertIn("DUPLICATE_ID", self._codes(report))

    def test_choices_must_be_four_nonempty_unique_strings(self):
        cases = [
            (["a", "b", "c"], "CHOICES_COUNT"),
            (["a", "b", "c", 4], "CHOICE_TYPE"),
            (["a", "b", "c", "  "], "CHOICE_EMPTY"),
            (["Ａ", "a", "b", "c"], "CHOICES_DUPLICATE"),
        ]
        for choices, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, self._codes(self._lint([self._item(choices=choices)])))

    def test_answer_index_range_and_difficulty_enum(self):
        report = self._lint([self._item(answer_index=4, difficulty="hard")])
        self.assertIn("ANSWER_INDEX", self._codes(report))
        self.assertIn("DIFFICULTY", self._codes(report))

    def test_week_is_optional_but_must_be_a_real_iso_week(self):
        self.assertTrue(self._lint([self._item(week="202653")]).ok)
        for week in ("202600", "202654", "202553", "2026-W29", "000001"):
            with self.subTest(week=week):
                self.assertIn("WEEK", self._codes(self._lint([self._item(week=week)])))

    def test_glossary_reference_must_exist(self):
        report = self._lint([self._item(source_ref="missing")])
        self.assertEqual(self._codes(report), ["GLOSSARY_REF"])

    def test_glossary_duplicate_ids_fail_dependency_gate(self):
        self._write(self.glossary_path, [{"id": "gmp"}, {"id": "gmp"}])
        report = self._lint([self._item()])
        self.assertIn("GLOSSARY_DUPLICATE_ID", self._codes(report))

    def test_brief_deeplink_accepts_percent_decoded_existing_anchor(self):
        item = self._item(
            source_type="brief",
            source_ref="https://www.grm-solutions.com/briefs/2026-07-12/#%EC%B9%B4%EB%93%9C-2",
        )
        self.assertTrue(self._lint([item]).ok)

    def test_brief_deeplink_date_anchor_host_and_path_are_checked(self):
        cases = [
            ("https://example.org/briefs/2026-07-12/#card-1", "BRIEF_HOST"),
            ("https://grm-solutions.com/archive/2026-07-12/#card-1", "BRIEF_PATH"),
            ("https://grm-solutions.com/briefs/2026-07-13/#card-1", "BRIEF_DATE"),
            ("https://grm-solutions.com/briefs/2026-07-12/", "BRIEF_ANCHOR"),
            ("https://grm-solutions.com/briefs/2026-07-12/#missing", "BRIEF_ANCHOR"),
        ]
        for source_ref, expected in cases:
            with self.subTest(source_ref=source_ref):
                report = self._lint([self._item(source_type="brief", source_ref=source_ref)])
                self.assertIn(expected, self._codes(report))

    def test_external_and_finding_urls_are_format_only(self):
        for source_type in ("external", "finding"):
            with self.subTest(source_type=source_type):
                valid = self._item(source_type=source_type, source_ref="https://unreachable.invalid/a")
                self.assertTrue(self._lint([valid]).ok)
                invalid = self._item(source_type=source_type, source_ref="ftp://example.org/a")
                self.assertIn("SOURCE_URL", self._codes(self._lint([invalid])))

    def test_unknown_source_type_is_rejected(self):
        report = self._lint([self._item(source_type="database")])
        self.assertIn("SOURCE_TYPE", self._codes(report))

    def test_internal_concepts_are_rejected_in_all_public_copy(self):
        cases = [
            {"question_ko": "GRM의 Signal Tier는 무엇인가요?"},
            {"choices": ["정답", "Evidence Level", "오답 B", "오답 C"]},
            {"explanation_ko": "Notion handoff에서 확인합니다."},
            {"question_ko": "source_ref 필드의 역할은 무엇인가요?"},
        ]
        for updates in cases:
            with self.subTest(updates=updates):
                report = self._lint([self._item(**updates)])
                self.assertIn("INTERNAL_CONCEPT", self._codes(report))

    def test_missing_only_required_dependency_is_reported(self):
        self.glossary_path.unlink()
        report = self._lint([self._item()])
        self.assertIn("GLOSSARY_READ", self._codes(report))

        external = self._item(source_type="external", source_ref="https://example.org")
        report = self._lint([external])
        self.assertTrue(report.ok, report.format())

    def test_main_prints_stdout_report_and_returns_only_zero_or_one(self):
        self._write(self.quiz_path, [self._item()])
        args = [
            "--quiz-bank", str(self.quiz_path),
            "--glossary", str(self.glossary_path),
            "--briefs-dir", str(self.briefs_dir),
        ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = ql.main(args)
        self.assertEqual(exit_code, 0)
        self.assertIn("quiz_lint: PASS", stdout.getvalue())

        self._write(self.quiz_path, [self._item(answer_index=9)])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = ql.main(args)
        self.assertEqual(exit_code, 1)
        self.assertIn("ERROR [ANSWER_INDEX]", stdout.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = ql.main(["--unknown"])
        self.assertEqual(exit_code, 1)
        self.assertIn("ERROR [ARGUMENTS]", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
