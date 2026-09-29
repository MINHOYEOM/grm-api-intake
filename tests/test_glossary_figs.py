"""용어 그림 일일 배치 도구(`glossary_figs.py`) — queue 의 판정 규칙.

check·sheet 는 헤드리스 Chrome 과 CDN 글꼴이 필요해 CI 에서 돌리지 않는다(배치 세션이 로컬에서 쓴다).
여기서 고정하는 것: 다시 볼 그림(redo)이 새 후보보다 먼저 나온다 · 그림이 있거나 그리지 않기로 한
(skip) 용어는 새 후보가 아니다 · 새 후보는 카드 노출이 많은 순이다 · 같은 입력이면 같은 순서다.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import glossary_figs as gf  # noqa: E402


class QueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.q = gf.build_queue(10_000)
        cls.figs = set(gf.lc.figure_ids())
        cls.review = gf.load_review()

    def test_counts_add_up(self):
        c = self.q["counts"]
        self.assertEqual(c["figures"], len(self.figs))
        self.assertEqual(c["remaining"], len(self.q["new"]))
        self.assertEqual(c["terms"], c["figures"] + c["skip"] + c["remaining"])

    def test_new_candidates_have_no_figure_and_are_not_skipped(self):
        ids = [r["id"] for r in self.q["new"]]
        self.assertEqual(set(ids) & self.figs, set())
        self.assertEqual(set(ids) & set(self.review["skip"]), set())
        self.assertEqual(len(ids), len(set(ids)))

    def test_redo_lists_ledger_figures_with_reason(self):
        self.assertEqual({r["id"] for r in self.q["redo"]}, set(self.review["redo"]) & self.figs)
        for r in self.q["redo"]:
            self.assertTrue(r["reason"])

    def test_new_candidates_are_ordered_by_card_exposure(self):
        keys = [(-r["card_weeks_top5"], -r["card_weeks_scored"]) for r in self.q["new"]]
        self.assertEqual(keys, sorted(keys))

    def test_skip_entry_removes_a_term_from_the_queue(self):
        """장부에 skip 으로 적으면 다음 회차가 같은 용어를 다시 보지 않는다 — 이게 장부의 존재 이유다."""
        first = self.q["new"][0]["id"]
        fake = {"redo": self.review["redo"], "skip": dict(self.review["skip"], **{first: {"since": "2026-09-29", "reason": "x"}})}
        with mock.patch.object(gf, "load_review", return_value=fake):
            q2 = gf.build_queue(10_000)
        self.assertNotIn(first, [r["id"] for r in q2["new"]])
        self.assertEqual(q2["counts"]["skip"], self.q["counts"]["skip"] + 1)

    def test_deterministic(self):
        self.assertEqual(json.dumps(gf.build_queue(20), ensure_ascii=False),
                         json.dumps(gf.build_queue(20), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
