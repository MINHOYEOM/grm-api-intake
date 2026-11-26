import unittest

from proto.clause_readability.logic import find_spans, normalize_query, step_result, term_spans


class ClauseReadabilityProtoTests(unittest.TestCase):
    def test_search_accepts_korean_particles_and_latin_case(self):
        self.assertEqual(normalize_query(" \tGMP의\u00a0"), "gmp의")
        self.assertEqual(find_spans("수율이 수율을", "수율"), [(0, 2), (4, 6)])
        self.assertEqual(find_spans("😀GMP gMp", "gmp"), [(1, 4), (5, 8)])

    def test_search_counts_overlapping_hits(self):
        self.assertEqual(find_spans("Banana", "ANA"), [(1, 4), (3, 6)])

    def test_empty_query_has_no_results(self):
        for query in ("", " \t\n", "\u00a0"):
            with self.subTest(query=query):
                self.assertEqual(find_spans("원문", query), [])

    def test_search_and_terms_treat_special_characters_as_literal(self):
        special = r"[A+B](C)?.\*"
        text = f"{special} [AAB](C)! "
        self.assertEqual(find_spans(text, special), [(0, len(special))])
        self.assertEqual(term_spans(text, [(special, "special")], "en"), [(0, len(special), "special")])

    def test_terms_accept_particles_and_reject_inner_korean_matches(self):
        self.assertEqual(term_spans("회수율과 수율이", [("수율", "yield")]), [(5, 7, "yield")])
        self.assertEqual(term_spans("회수율", [("수율", "yield")]), [])

    def test_terms_use_actual_span_and_skip_verb_use(self):
        self.assertEqual(
            term_spans("기록하고 무균\u00a0공정의 기록은", [("무균공정", "aseptic"), ("기록", "record")]),
            [(5, 10, "aseptic"), (12, 14, "record")],
        )
        self.assertEqual(term_spans("무균  공정", [("무균공정", "aseptic")]), [])

    def test_overlapping_terms_keep_priority_and_try_the_next_location(self):
        self.assertEqual(
            term_spans("audit trail trail", [("audit trail", "audit"), ("trail", "trail")], "en"),
            [(0, 11, "audit"), (12, 17, "trail")],
        )

    def test_term_selection_obeys_latin_boundaries_and_shared_used_set(self):
        used = set()
        selected = [("API", "api")]
        self.assertEqual(term_spans("RAPID api API", selected, "en", used), [(6, 9, "api")])
        self.assertEqual(term_spans("API", selected, "en", used), [])
        self.assertEqual(term_spans("api API는", selected), [(4, 7, "api")])

    def test_result_navigation_wraps_in_both_directions(self):
        self.assertEqual(step_result(2, 3, 1), 0)
        self.assertEqual(step_result(0, 3, -1), 2)
        self.assertEqual(step_result(-1, 3, 1), 0)
        self.assertEqual(step_result(-1, 3, -1), 2)
        self.assertEqual(step_result(0, 1, -1), 0)
        self.assertEqual(step_result(-1, 0, 1), -1)
