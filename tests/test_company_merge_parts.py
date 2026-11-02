# -*- coding: utf-8 -*-
"""기자 샤드 합치기 — 계획과 한 장이라도 어긋나면 멈추는지."""
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "merge_parts", os.path.join(ROOT, "company", "editorial", "merge_parts.py"))
mp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mp)

PLAN = {"delta": {"A": ["c1", "c2"], "B": ["c3"]}, "deep": {"A": ["fda483-1"], "B": []}}


EN = {"en": {"title_issue": "t", "summary": "s", "implication": "i", "key_facts": ["k"], "checks": ["c"]}}


def _write(folder, name, obj):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


class MergePartsTest(unittest.TestCase):
    def _folder(self, **over):
        d = tempfile.mkdtemp()
        files = {
            "desk.json": {"status": "완료", "shards": PLAN},
            "delta.A.json": {"status": "완료", "cards": {"c1": EN, "c2": EN}},
            "delta.B.json": {"status": "완료", "cards": {"c3": EN}, "tldr": ["a"], "tldr_en": ["a"]},
            "deep.A.json": {"status": "완료", "items": {"fda483-1": {"deep_analysis": {}, "observations_ko": [{"number": "1"}]}}},
        }
        files.update(over)
        for k, v in files.items():
            if v is not None:
                _write(d, k, v)
        return d

    def test_ok(self):
        delta, deep, problems = mp.merge(self._folder())
        self.assertEqual(problems, [])
        self.assertEqual(sorted(delta["cards"]), ["c1", "c2", "c3"])
        self.assertEqual(list(deep), ["fda483-1"])

    def test_missing_card_blocks(self):
        d = self._folder(**{"delta.A.json": {"status": "완료", "cards": {"c1": EN}}})
        self.assertTrue(mp.merge(d)[2])

    def test_unfinished_shard_blocks(self):
        d = self._folder(**{"delta.A.json": {"status": "보류", "cards": {}}})
        self.assertTrue(mp.merge(d)[2])

    def test_missing_shard_file_blocks(self):
        self.assertTrue(mp.merge(self._folder(**{"deep.A.json": None}))[2])

    def test_empty_deep_shard_file_optional(self):
        self.assertEqual(mp.merge(self._folder())[2], [])  # deep.B 계획 0건 → 파일 없어도 통과

    def test_483_without_observations_ko_blocks(self):
        d = self._folder(**{"deep.A.json": {"status": "완료", "items": {"fda483-1": {"deep_analysis": {}}}}})
        self.assertTrue(any("observations_ko" in p for p in mp.merge(d)[2]))

    def test_card_without_en_blocks(self):
        d = self._folder(**{"delta.A.json": {"status": "완료", "cards": {"c1": EN, "c2": {}}}})
        self.assertTrue(any("영문 슬롯" in p and "c2" in p for p in mp.merge(d)[2]))

    def test_partial_en_blocks(self):
        part = {"en": dict(EN["en"], checks=[])}
        d = self._folder(**{"delta.A.json": {"status": "완료", "cards": {"c1": EN, "c2": part}}})
        self.assertTrue(any("checks" in p for p in mp.merge(d)[2]))

    def test_en_fields_match_renderer(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "web"))
        import render
        self.assertEqual(tuple(mp.EN_FIELDS), tuple(render.CARD_NARRATIVE_FIELDS))

    def test_tldr_required(self):
        d = self._folder(**{"delta.B.json": {"status": "완료", "cards": {"c3": EN}}})
        self.assertTrue(mp.merge(d)[2])


if __name__ == "__main__":
    unittest.main()
