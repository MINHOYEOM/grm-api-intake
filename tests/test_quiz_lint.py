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


class QuizLintFixture:
    """임시 뱅크·용어집·브리프 픽스처 — 테스트 메서드는 없다(두 테스트 클래스가 공유)."""

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


class QuizLintTest(QuizLintFixture, unittest.TestCase):
    def test_valid_bank_passes_and_reports_counts(self):
        # 주차를 가진 문항은 세트로만 유효하다(출제 품질 게이트) — 개념 1 + 브리프 2,
        # easy 2 · normal 1 의 최소 성립 세트로 구성한다.
        report = self._lint(
            [
                self._item(),
                self._item(id="q-202653-01", week="202653"),
                self._item(
                    id="q-202653-02",
                    question_ko="카드에 명시된 조치는 무엇인가요?",
                    source_type="brief",
                    source_ref="https://grm-solutions.com/briefs/2026-07-12/#card-1",
                    difficulty="easy",
                    week="202653",
                ),
                self._item(
                    id="q-202653-03",
                    question_ko="카드에 명시된 다른 조치는 무엇인가요?",
                    source_type="brief",
                    source_ref="https://grm-solutions.com/briefs/2026-07-12/#카드-2",
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
        self.assertEqual(report.item_count, 6)
        self.assertEqual(report.source_counts["glossary"], 2)
        self.assertEqual(report.week_counts["202653"], 3)
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
        # 형식 규칙만 본다 — 세트 구성(문항 수·출처·난이도)은 QuizQualityGateTest 소관이라
        # 여기서는 WEEK 코드의 유무로만 판정한다(단건 주차는 세트 게이트에 따로 걸린다).
        self.assertTrue(self._lint([self._item()]).ok)
        self.assertNotIn("WEEK", self._codes(self._lint([self._item(week="202653")])))
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


class QuizQualityGateTest(QuizLintFixture, unittest.TestCase):
    """출제 품질 게이트(2026-08-04) — 형식은 맞지만 지문을 안 읽어도 풀리는 문항 차단.

    적용 대상은 `week` 를 가진 비면제 주차뿐이다(legacy pool·GRANDFATHERED_WEEKS 면제).
    면제가 미래로 새지 않는지, 그리고 규율 신설의 계기가 된 실제 결함을 이 게이트가
    실제로 잡는지(역적용)를 함께 고정한다.
    """

    NEW_WEEK = "202640"          # 면제 목록 밖 — 신규 파이프라인 산출물과 같은 취급

    def _week_set(self, *items):
        return [dict(item, week=self.NEW_WEEK) for item in items]

    def _concept(self, **updates):
        """세트를 성립시키는 개념 문항 1건(source_type=glossary)."""
        base = self._item(id="q-concept", difficulty="easy")
        base.update(updates)
        return base

    def _brief(self, n, **updates):
        base = self._item(
            id=f"q-brief-{n}",
            source_type="brief",
            source_ref="https://grm-solutions.com/briefs/2026-07-12/#card-1",
            difficulty="easy",
        )
        base.update(updates)
        return base

    def _valid_week(self):
        """게이트를 모두 통과하는 최소 세트 — 개념 1 + 브리프 3, easy 3 / normal 1."""
        return self._week_set(
            self._concept(),
            self._brief(1),
            self._brief(2),
            self._brief(3, difficulty="normal"),
        )

    def test_balanced_week_set_passes(self):
        report = self._lint(self._valid_week())
        self.assertTrue(report.ok, report.format())

    def test_week_without_concept_question_is_rejected(self):
        # 그 주 브리프를 읽지 않은 사람에게 전 문항이 찍기가 되는 구성.
        items = self._week_set(
            self._brief(1), self._brief(2), self._brief(3),
            self._brief(4, difficulty="normal"),
        )
        self.assertIn("WEEK_SOURCE_MIX", self._codes(self._lint(items)))

    def test_answer_that_is_much_longer_than_distractors_is_rejected(self):
        lead = ql.ANSWER_LENGTH_LEAD_MAX
        long_answer = self._concept(choices=["가" * (10 + lead), "가" * 10, "나" * 9, "다" * 8])
        self.assertIn("ANSWER_LENGTH_LEAD", self._codes(self._lint(self._week_set(long_answer))))
        # 경계 바로 아래(허용 상한)는 통과해야 한다 — off-by-one 고정.
        ok_answer = self._concept(choices=["가" * (9 + lead), "가" * 10, "나" * 9, "다" * 8])
        self.assertNotIn(
            "ANSWER_LENGTH_LEAD",
            self._codes(self._lint(self._week_set(ok_answer, self._brief(1), self._brief(2),
                                                 self._brief(3, difficulty="normal")))),
        )

    def test_week_where_longest_choice_is_usually_the_answer_is_rejected(self):
        # 문항 하나하나는 길이 게이트를 통과해도(리드 < 상한) 세트로 보면
        # "가장 긴 것 찍기"가 과반을 맞히는 구성 — 세트 단위로만 보이는 결함.
        def longest_answer(item):
            return dict(item, choices=["가" * 20, "나" * 12, "다" * 11, "라" * 10], answer_index=0)

        items = self._week_set(
            longest_answer(self._concept()),
            longest_answer(self._brief(1)),
            longest_answer(self._brief(2)),
            self._brief(3, difficulty="normal"),
        )
        self.assertIn("WEEK_LONGEST_ANSWER", self._codes(self._lint(items)))

    def test_week_size_and_difficulty_mix_are_enforced(self):
        too_many = self._valid_week() + self._week_set(self._brief(9, id="q-brief-9"))
        self.assertIn("WEEK_SIZE", self._codes(self._lint(too_many)))

        too_few = self._week_set(self._concept(), self._brief(1, difficulty="normal"))
        self.assertIn("WEEK_SIZE", self._codes(self._lint(too_few)))

        all_easy = self._week_set(
            self._concept(), self._brief(1), self._brief(2), self._brief(3),
        )
        self.assertIn("WEEK_DIFFICULTY_MIX", self._codes(self._lint(all_easy)))

        mostly_normal = self._week_set(
            self._concept(difficulty="normal"),
            self._brief(1, difficulty="normal"),
            self._brief(2, difficulty="normal"),
            self._brief(3),
        )
        self.assertIn("WEEK_DIFFICULTY_MIX", self._codes(self._lint(mostly_normal)))

    def test_legacy_pool_and_grandfathered_weeks_are_exempt(self):
        # week 없는 legacy pool 은 세트 개념 자체가 없어 게이트 대상이 아니다.
        legacy = [self._item(id=f"q2-{n}", source_type="brief",
                             source_ref="https://grm-solutions.com/briefs/2026-07-12/#card-1",
                             choices=["가" * 40, "나", "다", "라"])
                  for n in range(4)]
        self.assertTrue(self._lint(legacy).ok, "legacy pool 은 면제되어야 합니다")

        # 면제 주차도 같은 이유로 통과한다(공개 후 불변 — 소급 수정 금지).
        exempt_week = sorted(ql.GRANDFATHERED_WEEKS)[0]
        items = [dict(item, week=exempt_week) for item in legacy]
        self.assertTrue(self._lint(items).ok, "면제 주차는 통과해야 합니다")

    def test_grandfathered_weeks_are_closed_to_the_past(self):
        """면제 목록이 미래로 새면 게이트 전체가 무력해진다 — 상한을 고정한다."""
        newest_exempt = max(ql.GRANDFATHERED_WEEKS)
        self.assertLessEqual(newest_exempt, "202632", "신규 주차를 면제 목록에 넣지 않는다")

    def test_committed_bank_weeks_would_be_caught_if_they_were_new(self):
        """역적용 증명 — 규율의 계기가 된 실제 결함을 게이트가 실제로 잡는가.

        커밋된 뱅크의 면제 주차 세트를 그대로 신규 주차로 옮기면 반드시 걸려야 한다.
        (0건이면 게이트가 아무것도 막지 못한다는 뜻이므로 실패로 본다.)
        """
        bank = json.loads(ql.DEFAULT_QUIZ_BANK.read_text(encoding="utf-8"))
        for week in sorted(ql.GRANDFATHERED_WEEKS):
            entries = [q for q in bank if str(q.get("week", "")) == week]
            self.assertTrue(entries, f"{week} 세트가 뱅크에 없습니다")
            moved = [dict(q, week=self.NEW_WEEK) for q in entries]
            report = ql.lint_quiz_bank(
                self._materialise(moved), ql.DEFAULT_GLOSSARY, ql.DEFAULT_BRIEFS_DIR
            )
            self.assertIn(
                "WEEK_SOURCE_MIX", self._codes(report),
                f"{week}: 브리프 전용 세트를 게이트가 놓쳤습니다\n{report.format()}",
            )

    def _materialise(self, items) -> Path:
        path = self.root / "moved_bank.json"
        self._write(path, items)
        return path


if __name__ == "__main__":
    unittest.main()
