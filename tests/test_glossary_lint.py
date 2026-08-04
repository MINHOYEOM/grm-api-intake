import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import glossary_lint as gl


class CommittedGlossaryGateTest(unittest.TestCase):
    """Make unittest discovery enforce the committed glossary in GitHub CI."""

    def test_committed_glossary_is_lint_clean(self):
        report = gl.lint_glossary()
        self.assertTrue(report.ok, report.format())


class GlossaryLintTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="glossary_lint_")
        self.root = Path(self._tmp.name)
        self.glossary_path = self.root / "glossary.json"
        self.cases_path = self.root / "glossary_cases.json"

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _write(path: Path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _term(**updates):
        item = {
            "id": "term-a",
            "term_ko": "용어 에이",
            "term_en": "Term A",
            "easy_ko": "충분히 긴 쉬운 설명 문장입니다",
            "definition_source": "Source A 정의",
        }
        item.update(updates)
        return item

    @staticmethod
    def _cases_for(ids):
        return {
            "items": [],
            "excluded": [{"id": i, "term_ko": i, "reason": "테스트 제외 사유"} for i in ids],
        }

    def _lint(self, items, cases=None):
        self._write(self.glossary_path, items)
        if cases is None:
            ids = [
                it["id"] for it in items
                if isinstance(it, dict) and isinstance(it.get("id"), str) and it.get("id")
            ]
            cases = self._cases_for(ids)
        self._write(self.cases_path, cases)
        return gl.lint_glossary(self.glossary_path, self.cases_path)

    @staticmethod
    def _codes(report):
        return [issue.code for issue in report.issues]

    @staticmethod
    def _warn_codes(report):
        return [issue.code for issue in report.warnings]

    # ── happy path ──────────────────────────────────────────────────────

    def test_valid_bank_passes_and_reports_counts(self):
        report = self._lint(
            [
                self._term(
                    related=["term-b"],
                    source_url="https://example.org/a",
                    detail_ko="term-a에 대한 실무 맥락 설명입니다.",
                    reg_refs=["21 CFR 211.22", {"label": "ICH Q9", "url": "https://example.org/q9"}],
                ),
                self._term(
                    id="term-b",
                    term_ko="용어 비",
                    term_en="Term B",
                    related=["term-a"],
                ),
            ]
        )
        self.assertTrue(report.ok, report.format())
        self.assertEqual(report.term_count, 2)
        self.assertEqual(report.cases_excluded_count, 2)
        self.assertIn("glossary_lint: PASS", report.format())

    # ── structure (E1) ──────────────────────────────────────────────────

    def test_invalid_json_and_top_level_contract(self):
        self.glossary_path.write_text("[{", encoding="utf-8")
        self._write(self.cases_path, self._cases_for([]))
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        self.assertIn("GLOSSARY_JSON", self._codes(report))

        self._write(self.glossary_path, {"items": []})
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        self.assertIn("GLOSSARY_TYPE", self._codes(report))

        report = self._lint([{"id": "x"}, "not-a-dict"], cases=self._cases_for(["x"]))
        self.assertIn("ITEM_TYPE", self._codes(report))

    def test_zero_guard(self):
        report = self._lint([], cases=self._cases_for([]))
        self.assertEqual(self._codes(report), ["GLOSSARY_EMPTY"])
        self.assertFalse(report.ok)

    # ── required / unknown / type (E2, E3, E4) ────────────────────────

    def test_required_unknown_and_field_types(self):
        item = self._term(related="not-a-list", extra_field="x")
        del item["term_en"]
        report = self._lint([item])
        codes = self._codes(report)
        self.assertIn("REQUIRED_FIELD", codes)
        self.assertIn("UNKNOWN_FIELD", codes)
        self.assertIn("FIELD_TYPE", codes)

    def test_empty_required_string_is_rejected(self):
        report = self._lint([self._term(term_ko="   ")])
        self.assertIn("EMPTY_STRING", self._codes(report))

    def test_related_item_type_violation(self):
        report = self._lint([self._term(related=[123])])
        self.assertIn("RELATED_ITEM_TYPE", self._codes(report))

    def test_reg_refs_shape_violations(self):
        cases = [
            ([123], "REG_REF_ITEM_TYPE"),
            ([""], "REG_REF_ITEM_TYPE"),
            ([{"url": "https://example.org"}], "REG_REF_LABEL"),
            ([{"label": "  "}], "REG_REF_LABEL"),
            ([{"label": "A", "url": 5}], "REG_REF_ITEM_TYPE"),
        ]
        for reg_refs, expected in cases:
            with self.subTest(reg_refs=reg_refs):
                report = self._lint([self._term(reg_refs=reg_refs)])
                self.assertIn(expected, self._codes(report))

    # ── id (E5, E6) ─────────────────────────────────────────────────────

    def test_duplicate_ids_are_rejected(self):
        report = self._lint([self._term(), self._term()])
        self.assertIn("DUPLICATE_ID", self._codes(report))

    def test_id_format_violations(self):
        for bad_id in ("Term-A", "term a", "term_a", "-term", "term-", "term--a"):
            with self.subTest(bad_id=bad_id):
                report = self._lint([self._term(id=bad_id)], cases=self._cases_for([bad_id]))
                self.assertIn("ID_FORMAT", self._codes(report))

    # ── related references (E7, E8, E9) ────────────────────────────────

    def test_related_orphan_self_and_duplicate(self):
        report = self._lint([self._term(related=["missing-term"])])
        self.assertIn("RELATED_ORPHAN", self._codes(report))

        report = self._lint([self._term(related=["term-a"])])
        self.assertIn("RELATED_SELF", self._codes(report))

        report = self._lint(
            [
                self._term(related=["term-b", "term-b"]),
                self._term(id="term-b", term_ko="용어 비", term_en="Term B"),
            ]
        )
        self.assertIn("RELATED_DUPLICATE", self._codes(report))

    # ── URLs (E10, E11, E12) ────────────────────────────────────────────

    def test_source_url_scheme_and_whitespace(self):
        report = self._lint([self._term(source_url="ftp://example.org/a")])
        self.assertIn("SOURCE_URL_SCHEME", self._codes(report))

        report = self._lint([self._term(source_url="https://example.org/a b")])
        self.assertIn("URL_WHITESPACE", self._codes(report))

    def test_reg_ref_url_scheme_and_whitespace(self):
        report = self._lint([self._term(reg_refs=[{"label": "A", "url": "javascript:alert(1)"}])])
        self.assertIn("REG_REF_URL_SCHEME", self._codes(report))

        report = self._lint([self._term(reg_refs=[{"label": "A", "url": "https://example.org/a\tb"}])])
        self.assertIn("URL_WHITESPACE", self._codes(report))

    # ── string hygiene (E13) ────────────────────────────────────────────

    def test_string_hygiene_patterns(self):
        cases = [
            ({"term_ko": " 용어 에이"}, "앞뒤 공백"),
            ({"term_ko": "용어  에이"}, "연속된 공백"),
            ({"easy_ko": "탭\t포함된 설명 문장입니다"}, "탭 문자"),
            ({"detail_ko": "개행\n포함된 실무 맥락 설명입니다"}, "개행 문자"),
            ({"term_en": "Term​A"}, "제로폭 문자"),
        ]
        for updates, expected_fragment in cases:
            with self.subTest(updates=updates):
                report = self._lint([self._term(**updates)])
                self.assertIn("STRING_HYGIENE", self._codes(report))
                self.assertTrue(
                    any(expected_fragment in issue.message for issue in report.issues),
                    report.format(),
                )

    # ── glossary_cases.json (E14, E15, E16) ─────────────────────────────

    def test_cases_json_and_top_level_contract(self):
        self._write(self.glossary_path, [self._term()])
        self.cases_path.write_text("{", encoding="utf-8")
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        self.assertIn("CASES_JSON", self._codes(report))

        self._write(self.cases_path, [])
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        self.assertIn("CASES_TYPE", self._codes(report))

        self._write(self.cases_path, {"items": "x", "excluded": "y"})
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        codes = self._codes(report)
        self.assertIn("CASES_ITEMS_TYPE", codes)
        self.assertIn("CASES_EXCLUDED_TYPE", codes)

    def test_cases_id_set_must_match_glossary(self):
        self._write(self.glossary_path, [self._term()])

        # missing: glossary has term-a but cases mentions nothing.
        self._write(self.cases_path, {"items": [], "excluded": []})
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        self.assertIn("CASES_ID_MISSING", self._codes(report))

        # extra: cases mentions an id glossary doesn't have.
        self._write(
            self.cases_path,
            {
                "items": [],
                "excluded": [
                    {"id": "term-a", "term_ko": "a", "reason": "r"},
                    {"id": "term-z", "term_ko": "z", "reason": "r"},
                ],
            },
        )
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        self.assertIn("CASES_ID_EXTRA", self._codes(report))

        # duplicate: same id in both items and excluded.
        self._write(
            self.cases_path,
            {
                "items": [{"id": "term-a", "q": "Q", "findings": 1, "documents": 1}],
                "excluded": [{"id": "term-a", "term_ko": "a", "reason": "r"}],
            },
        )
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        self.assertIn("CASES_ID_DUPLICATE", self._codes(report))

    def test_cases_excluded_reason_required(self):
        self._write(self.glossary_path, [self._term()])
        self._write(
            self.cases_path,
            {"items": [], "excluded": [{"id": "term-a", "term_ko": "a", "reason": "  "}]},
        )
        report = gl.lint_glossary(self.glossary_path, self.cases_path)
        self.assertIn("CASES_EXCLUDED_REASON", self._codes(report))

    def test_cases_item_q_and_counts(self):
        self._write(self.glossary_path, [self._term()])
        cases = [
            ({"id": "term-a", "q": "", "findings": 1, "documents": 1}, "CASES_ITEM_Q"),
            ({"id": "term-a", "q": "Q", "findings": 0, "documents": 1}, "CASES_ITEM_COUNT"),
            ({"id": "term-a", "q": "Q", "findings": 1, "documents": -1}, "CASES_ITEM_COUNT"),
            ({"id": "term-a", "q": "Q", "findings": "1", "documents": 1}, "CASES_ITEM_COUNT"),
        ]
        for item, expected in cases:
            with self.subTest(item=item):
                self._write(self.cases_path, {"items": [item], "excluded": []})
                report = gl.lint_glossary(self.glossary_path, self.cases_path)
                self.assertIn(expected, self._codes(report))

    # ── warnings + baselines (W1, W2, W3) ───────────────────────────────

    def test_term_ko_and_term_en_duplicates_are_warned(self):
        report = self._lint(
            [
                self._term(),
                self._term(id="term-b", term_en="Term B"),  # same term_ko, distinct term_en
            ]
        )
        self.assertTrue(report.ok, report.format())  # 1 pair == baseline, not over it
        self.assertIn("TERM_KO_DUPLICATE", self._warn_codes(report))
        self.assertEqual(report.term_ko_dup_pairs, 1)

    def test_term_ko_duplicate_over_baseline_becomes_error(self):
        # Fixed module baseline is 1 pair; 3 terms sharing term_ko = C(3,2) = 3 pairs, over it.
        report = self._lint(
            [
                self._term(id="term-a", term_en="Term A"),
                self._term(id="term-b", term_en="Term B"),
                self._term(id="term-c", term_en="Term C"),
            ]
        )
        self.assertFalse(report.ok, report.format())
        self.assertIn("TERM_KO_DUPLICATE_BASELINE", self._codes(report))

    def test_detail_ko_similarity_new_term_is_error(self):
        """기준선에 없는 용어가 다른 용어와 실무 맥락이 겹치면 ERROR.

        ★기준선은 개수가 아니라 **id 집합**이다. 그래서 총량(2)이 기준선(121개 id)보다
        훨씬 적어도 "이 id 는 원래 겹치지 않던 용어"라는 사실만으로 잡힌다 — 개수 기준선은
        기존 1건을 고치고 새 1건을 만드는 상쇄를 통과시켜 버린다."""
        report = self._lint(
            [
                self._term(
                    detail_ko="실무 맥락 설명입니다. 이 용어는 실사에서 반복적으로 확인됩니다. 예시 문장입니다.",
                ),
                self._term(
                    id="term-b",
                    term_ko="용어 비",
                    term_en="Term B",
                    detail_ko="실무 맥락 설명입니다. 이 용어는 실사에서 자주 확인됩니다. 예시 문장입니다.",
                ),
            ]
        )
        self.assertFalse(report.ok, report.format())
        self.assertIn("DETAIL_KO_SIMILAR_NEW", self._codes(report))
        self.assertEqual(report.detail_ko_similar_terms, 2)
        # 어느 용어와 겹치는지까지 메시지에 나와야 진단이 된다(숫자만으로는 못 고친다).
        new_issues = [i for i in report.issues if i.code == "DETAIL_KO_SIMILAR_NEW"]
        self.assertTrue(any("term-b" in i.message for i in new_issues), report.format())

    def test_detail_ko_similarity_within_baseline_ids_passes(self):
        """기준선에 이미 등재된 id 끼리 겹치는 건 통과 — 기존 데이터를 갑자기 FAIL 시키지 않는다."""
        known = sorted(gl.BASELINE_DETAIL_KO_SIMILAR_IDS)[:2]
        self.assertEqual(len(known), 2, "기준선 집합이 비어 있으면 이 테스트가 헛돈다")
        report = self._lint(
            [
                self._term(
                    id=known[0],
                    detail_ko="실무 맥락 설명입니다. 이 용어는 실사에서 반복적으로 확인됩니다. 예시 문장입니다.",
                ),
                self._term(
                    id=known[1],
                    term_ko="용어 비",
                    term_en="Term B",
                    detail_ko="실무 맥락 설명입니다. 이 용어는 실사에서 자주 확인됩니다. 예시 문장입니다.",
                ),
            ]
        )
        self.assertTrue(report.ok, report.format())
        self.assertNotIn("DETAIL_KO_SIMILAR_NEW", self._codes(report))

    def test_detail_ko_similar_pairs_are_folded_unless_verbose(self):
        """기준선 안쪽 쌍은 기본 출력에서 한 줄로 접힌다.

        ★매번 509줄을 찍으면 아무도 안 읽고, 그러면 진짜 신규 경고가 그 사이에 묻힌다.
        항상 울리는 경보는 경보가 아니다."""
        known = sorted(gl.BASELINE_DETAIL_KO_SIMILAR_IDS)[:2]
        items = [
            self._term(id=known[0], detail_ko="실무 맥락 설명입니다. 반복적으로 확인됩니다. 예시 문장입니다."),
            self._term(id=known[1], term_ko="용어 비", term_en="Term B",
                       detail_ko="실무 맥락 설명입니다. 자주 확인됩니다. 예시 문장입니다."),
        ]
        self._write(self.glossary_path, items)
        self._write(self.cases_path, self._cases_for([i["id"] for i in items]))

        folded = gl.lint_glossary(self.glossary_path, self.cases_path)
        verbose = gl.lint_glossary(self.glossary_path, self.cases_path, verbose=True)
        similar = [i for i in folded.warnings if i.code == "DETAIL_KO_SIMILAR"]
        self.assertEqual(len(similar), 1, folded.format())
        self.assertIn("요약", similar[0].location)
        self.assertEqual(
            len([i for i in verbose.warnings if i.code == "DETAIL_KO_SIMILAR"]),
            len(verbose.detail_ko_similar_pairs),
            verbose.format(),
        )

    def test_invisible_space_lookalikes_are_rejected(self):
        """NBSP·전각공백처럼 **보통 공백으로 보이는 다른 문자**를 잡는다.

        복붙·웹 스크랩으로 섞여 들어오고, 들어오면 검색어 일치가 조용히 어긋난다."""
        for ch, label in ((" ", "U+00A0"), ("　", "U+3000")):
            with self.subTest(char=label):
                report = self._lint([self._term(term_ko=f"용어{ch}에이")])
                self.assertFalse(report.ok, report.format())
                self.assertIn("STRING_HYGIENE", self._codes(report))
                self.assertTrue(
                    any(label in i.message for i in report.issues), report.format()
                )

    def test_short_field_over_baseline_becomes_error(self):
        # Baseline is 0, so a single occurrence already exceeds it.
        report = self._lint([self._term(easy_ko="짧은설명")])
        self.assertFalse(report.ok, report.format())
        self.assertIn("SHORT_FIELD", self._warn_codes(report))
        self.assertIn("SHORT_FIELD_BASELINE", self._codes(report))

    def test_baseline_below_measured_value_emits_notice_not_failure(self):
        # No term_ko duplicates at all -> 0 pairs < baseline 1 -> NOTICE, still PASS.
        report = self._lint([self._term(), self._term(id="term-b", term_ko="용어 비", term_en="Term B")])
        self.assertTrue(report.ok, report.format())
        self.assertTrue(any("기준선" in notice for notice in report.notices), report.format())

    # ── CLI ───────────────────────────────────────────────────────────

    def test_main_prints_stdout_report_and_returns_only_zero_or_one(self):
        self._write(self.glossary_path, [self._term()])
        self._write(self.cases_path, self._cases_for(["term-a"]))
        args = [
            "--glossary", str(self.glossary_path),
            "--glossary-cases", str(self.cases_path),
        ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = gl.main(args)
        self.assertEqual(exit_code, 0)
        self.assertIn("glossary_lint: PASS", stdout.getvalue())

        self._write(self.glossary_path, [self._term(id="Bad Id")])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = gl.main(args)
        self.assertEqual(exit_code, 1)
        self.assertIn("ERROR [ID_FORMAT]", stdout.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = gl.main(["--unknown"])
        self.assertEqual(exit_code, 1)
        self.assertIn("ERROR [ARGUMENTS]", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
