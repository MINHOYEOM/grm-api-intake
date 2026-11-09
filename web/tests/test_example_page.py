"""지적 사례 화면(example_page) 표적 테스트 — 정본 검증·주제 분류·선별 상한."""
from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB.parent))

import example_page as ep  # noqa: E402
import grm_findings as gf  # noqa: E402

LABELS = {c.code: (c.label_ko, c.label_en) for c in gf.FINDING_TAXONOMY}


def _data():
    return {
        "schema": ep.SCHEMA, "curated_on": "2026-11-09",
        "sources": {
            "s1": {"agency": "MHRA", "year": 2016, "year_basis": "calendar", "kind": "pdf", "url": "https://x/a.pdf"},
            "s2": {"agency": "PMDA", "year": 2025, "year_basis": "jp_fiscal", "kind": "xlsx", "url": "https://x/b.xlsx"},
        },
        "examples": [
            {"id": f"s1#{i}", "category": "data_integrity", "source": "s1", "locator": "", "agency_category": "Annex 11", "text": f"Text {i}.", "ko": f"번역 {i}."}
            for i in range(8)
        ] + [
            {"id": "s2#1", "category": "aseptic_sterility_assurance", "source": "s2", "locator": "R1",
             "agency_category": "Sterility  assurance", "text": "A  b\nc.", "ko": "가  나\n다."},
        ],
    }


class BuildTopicsTest(unittest.TestCase):
    def test_none_gives_empty(self):
        self.assertEqual(ep.build_topics(None, LABELS), [])

    def test_cap_per_topic_and_order(self):
        topics = ep.build_topics(_data(), LABELS)
        self.assertEqual([t["code"] for t in topics], ["data_integrity", "aseptic_sterility_assurance"])
        self.assertEqual(len(topics[0]["examples"]), ep.MAX_PER_TOPIC)
        # 정본 순서 유지(앞에서부터 자른다)
        self.assertEqual([e["text"] for e in topics[0]["examples"]],
                         [f"Text {i}." for i in range(ep.MAX_PER_TOPIC)])

    def test_whitespace_normalised_and_year_basis_carried(self):
        e = ep.build_topics(_data(), LABELS)[1]["examples"][0]
        self.assertEqual(e["text"], "A b c.")
        self.assertEqual(e["agency_category"], "Sterility assurance")
        self.assertEqual((e["agency"], e["year"], e["year_basis"]), ("PMDA", 2025, "jp_fiscal"))

    def test_unknown_topic_not_in_order_is_dropped_but_known_label_required(self):
        d = _data()
        d["examples"][0]["category"] = "other_quality_system"   # 라벨은 있으나 TOPIC_ORDER 밖 → 화면에 안 나간다
        codes = [t["code"] for t in ep.build_topics(d, LABELS)]
        self.assertNotIn("other_quality_system", codes)
        d["examples"][0]["category"] = "no_such_topic"
        with self.assertRaises(ValueError):
            ep.build_topics(d, LABELS)

    def test_missing_source_and_overlong_text_fail(self):
        d = _data()
        d["examples"][0]["source"] = "nope"
        with self.assertRaises(ValueError):
            ep.build_topics(d, LABELS)
        d = _data()
        d["examples"][0]["text"] = "x" * (ep.MAX_TEXT + 1)
        with self.assertRaises(ValueError):
            ep.build_topics(d, LABELS)

    def test_ko_carried_normalised_and_required(self):
        e = ep.build_topics(_data(), LABELS)[1]["examples"][0]
        self.assertEqual(e["ko"], "가 나 다.")
        for bad in (None, "", "   ", "가" * (ep.MAX_KO + 1)):
            d = _data()
            if bad is None:
                del d["examples"][0]["ko"]
            else:
                d["examples"][0]["ko"] = bad
            with self.subTest(ko=bad), self.assertRaises(ValueError):
                ep.build_topics(d, LABELS)

    def test_schema_mismatch_rejected(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "d.json"
            bad = copy.deepcopy(_data()); bad["schema"] = "other/v9"
            p.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                ep.load_examples(p)
            self.assertIsNone(ep.load_examples(Path(td) / "absent.json"))


class CommittedDataTest(unittest.TestCase):
    """커밋된 정본이 화면 계약을 지키는가 — 깨진 사례가 조용히 나가지 않게."""

    @classmethod
    def setUpClass(cls):
        cls.data = ep.load_examples()

    def test_every_example_resolves_and_is_short(self):
        topics = ep.build_topics(self.data, LABELS)   # 규칙 위반이면 여기서 ValueError
        self.assertGreaterEqual(len(topics), 10)
        for t in topics:
            for e in t["examples"]:
                self.assertTrue(e["url"].startswith("https://"))
                self.assertNotIn("●", e["text"], e["id"])       # 가려진 수치가 든 문장은 싣지 않는다
                self.assertNotRegex(e["text"], r"(?i)e\.g\.$", e["id"])  # 잘린 문장 금지

    def test_ids_unique_and_every_example_is_shown_or_capped(self):
        ids = [e["id"] for e in self.data["examples"]]
        self.assertEqual(len(ids), len(set(ids)))
        per = {}
        for e in self.data["examples"]:
            per[e["category"]] = per.get(e["category"], 0) + 1
        # 정본이 상한보다 많이 쌓이면 뒤쪽은 말없이 잘린다 — 일부러 그런 거면 이 값을 올려라.
        self.assertLessEqual(max(per.values()), ep.MAX_PER_TOPIC)

    def test_every_example_has_korean_translation(self):
        hangul = re.compile(r"[가-힣]")
        for e in self.data["examples"]:
            ko = (e.get("ko") or "").strip()
            self.assertTrue(ko, e["id"])
            self.assertRegex(ko, hangul, e["id"])
            self.assertNotIn("●", ko, e["id"])
            self.assertNotEqual(ko, e["text"], e["id"])

    def test_acronyms_and_numbers_survive_translation(self):
        """약어·고유명은 번역에서 바뀌지 않고, 원문의 숫자는 번역에도 그대로 있다."""
        for e in self.data["examples"]:
            for tok in re.findall(r"\b(?:HPLC|QPs?|MHRA|PMDA|OOS|CAPA|SOP|COA|IPC|KPI|PQS|EU GMP|DMRC|Annex \d+)\b", e["text"]):
                self.assertIn(tok.rstrip("s") if tok == "QPs" else tok, e["ko"], f"{e['id']}: {tok}")
            for num in re.findall(r"\d+", e["text"]):
                self.assertIn(num, e["ko"], f"{e['id']}: 숫자 {num}")

    def test_coverage_floor_per_topic_and_total(self):
        """확대 계약 — 전체 70건 이상, 화면의 모든 영역이 3건 이상. 낮추려면 이유를 적어라."""
        topics = ep.build_topics(self.data, LABELS)
        self.assertEqual([t["code"] for t in topics], list(ep.TOPIC_ORDER))
        shown = sum(len(t["examples"]) for t in topics)
        self.assertGreaterEqual(shown, 70)
        for t in topics:
            self.assertGreaterEqual(len(t["examples"]), 3, t["code"])

    def test_categories_are_on_screen_and_texts_not_fragments(self):
        texts = set()
        for e in self.data["examples"]:
            self.assertIn(e["category"], ep.TOPIC_ORDER, e["id"])   # 화면 밖 영역에 둔 사례 금지
            self.assertGreaterEqual(len(e["text"].split()), 5, e["id"])
            norm = re.sub(r"\W+", " ", e["text"].lower()).strip()
            self.assertNotIn(norm, texts, f"{e['id']}: 같은 문장 중복")
            texts.add(norm)

    def test_pmda_year_is_fiscal(self):
        for key, s in self.data["sources"].items():
            if s["agency"] == "PMDA":
                self.assertEqual(s["year_basis"], "jp_fiscal", key)


if __name__ == "__main__":
    unittest.main()
