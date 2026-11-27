"""Search and term-matching contracts for individual clause pages."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))
import render  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "Node.js is required to exercise the browser's search logic")
class ClauseFindLogicTest(unittest.TestCase):
    def run_search_logic(self, expression):
        script = f"""
const {{ findSpans, normalizeQuery, stepResult }} = require(process.argv[1]);
const find = (text, query) => findSpans(text.replace(/[A-Z]/g, c => c.toLowerCase()), normalizeQuery(query));
console.log(JSON.stringify({expression}));
"""
        out = subprocess.check_output(
            ["node", "-e", script, str(WEB / "assets" / "clause_find.js")], encoding="utf-8")
        return json.loads(out)

    def test_search_accepts_korean_particles_and_latin_case(self):
        self.assertEqual(self.run_search_logic(
            r'[normalizeQuery(" \tGMP의\u00a0"), find("수율이 수율을", "수율"), find("😀GMP gMp", "gmp")]'),
            ["gmp의", [[0, 2], [4, 6]], [[2, 5], [6, 9]]])

    def test_search_counts_overlapping_hits(self):
        self.assertEqual(self.run_search_logic('find("Banana", "ANA")'), [[1, 4], [3, 6]])

    def test_empty_query_has_no_results(self):
        self.assertEqual(self.run_search_logic(
            r'["", " \t\n", "\u00a0"].map(query => find("원문", query))'), [[], [], []])

    def test_search_treats_special_characters_as_literal(self):
        self.assertEqual(self.run_search_logic(r'find("[A+B](C)?.\\* [AAB](C)! ", "[A+B](C)?.\\*")'), [[0, 12]])

    def test_search_rejects_inner_korean_matches(self):
        self.assertEqual(self.run_search_logic('find("회수율 수율이", "수율")'), [[4, 6]])

    def test_result_navigation_wraps_in_both_directions(self):
        self.assertEqual(self.run_search_logic(
            '[[2,3,1],[0,3,-1],[-1,3,1],[-1,3,-1],[0,1,-1],[-1,0,1]].map(args => stepResult(...args))'),
            [0, 2, 0, 2, 0, -1])


class ClauseTermLogicTest(unittest.TestCase):
    def test_terms_treat_special_characters_as_literal(self):
        special = r"[A+B](C)?.\*"
        self.assertEqual(render.term_link_spans(
            f"{special} [AAB](C)! ", [(special, "special")], set(), "en"), [(0, len(special), "special")])

    def test_terms_accept_particles_and_reject_inner_korean_matches(self):
        selected = [("수율", "yield")]
        self.assertEqual(render.term_link_spans("회수율과 수율이", selected, set()), [(5, 7, "yield")])
        self.assertEqual(render.term_link_spans("회수율", selected, set()), [])

    def test_terms_use_actual_span_and_skip_verb_use(self):
        self.assertEqual(render.term_link_spans(
            "기록하고 무균\u00a0공정의 기록은", [("무균공정", "aseptic"), ("기록", "record")], set()),
            [(5, 10, "aseptic"), (12, 14, "record")])
        self.assertEqual(render.term_link_spans("무균  공정", [("무균공정", "aseptic")], set()), [])

    def test_overlapping_terms_keep_priority_and_try_the_next_location(self):
        self.assertEqual(render.term_link_spans(
            "audit trail trail", [("audit trail", "audit"), ("trail", "trail")], set(), "en"),
            [(0, 11, "audit"), (12, 17, "trail")])

    def test_term_selection_obeys_latin_boundaries_and_shared_used_set(self):
        used = set()
        selected = [("API", "api")]
        self.assertEqual(render.term_link_spans("RAPID api API", selected, used, "en"), [(6, 9, "api")])
        self.assertEqual(render.term_link_spans("API", selected, used, "en"), [])
        self.assertEqual(render.term_link_spans("api API는", selected, set()), [(4, 7, "api")])

    def test_link_and_tooltip_preserve_escaped_text_and_use_the_same_spans(self):
        text = "<기록> & 기록은"
        selected = [("기록", "record")]
        self.assertEqual(str(render.link_terms_in_text(text, selected, "../", set())),
                         '&lt;<a class="fd-term" href="../glossary/record/">기록</a>&gt; &amp; 기록은')
        [paragraph] = render.clause_term_paragraphs(
            [text], selected, {"record": '"<&>'}, "../", set(), "ko")
        self.assertEqual(str(paragraph),
                         '&lt;<a class="ct-term" href="../glossary/record/" '
                         'data-clause-definition="&#34;&lt;&amp;&gt;">기록</a>&gt; &amp; 기록은')
