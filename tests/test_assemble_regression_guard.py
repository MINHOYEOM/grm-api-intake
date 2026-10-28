"""발행 회귀 가드 판정 계약 고정 — 재조립이 발행분을 되돌리면 막고, 정상 변동은 통과.

이 가드는 2026-07-29 실측 사고에서 나왔다: 발행 후 수기 보정(#475 WHO 카드 11장 소급
복구)이 델타에 없으니, 델타 정정 커밋이 재조립을 당길 때마다 그 보정분을 지운 산출물이
나온다(실측 −117,225자·카드당 −70~90%). 종전엔 커밋 스텝이 zero-diff 로 죽어 회귀 PR 이
못 열렸을 뿐이고, 그 실패를 고치면 회귀가 그대로 승인 카드까지 올라온다.

임계를 두는 이유도 같은 무게로 고정한다 — 임계 없이 '조금이라도 줄면 차단'하면 파서
개선 같은 정상 재조립마다 붉게 뜨고, 그건 방금 고친 오탐과 같은 종류의 결함이 된다.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assemble_regression_guard as g  # noqa: E402


def _doc(cards):
    return {"schema_version": 1, "brief": {}, "cards": cards}


def _card(cid, body, order=0):
    return {"id": cid, "render_order": order, "body": body}


class IdenticalTest(unittest.TestCase):
    def test_identical_passes(self):
        d = _doc([_card("a", "x" * 100), _card("b", "y" * 100, 1)])
        r = g.compare(d, json.loads(json.dumps(d)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["total_delta"], 0)
        self.assertEqual(r["missing_cards"], [])
        self.assertEqual(r["shrunk_cards"], [])

    def test_growth_passes(self):
        pub = _doc([_card("a", "x" * 100)])
        asm = _doc([_card("a", "x" * 400)])
        r = g.compare(pub, asm)
        self.assertTrue(r["ok"])
        self.assertGreater(r["total_delta"], 0)


class RegressionTest(unittest.TestCase):
    def test_card_content_wiped_is_blocked(self):
        """WHO 11장 사고의 축소판 — 카드는 남아 있는데 내용만 사라진다."""
        pub = _doc([_card("who-1", "결론+항목별 요약 국문 병기 " * 60), _card("ok-1", "z" * 200, 1)])
        asm = _doc([_card("who-1", "링크만"), _card("ok-1", "z" * 200, 1)])
        r = g.compare(pub, asm)
        self.assertFalse(r["ok"])
        self.assertEqual([c["id"] for c in r["shrunk_cards"]], ["who-1"])
        self.assertGreater(r["shrunk_cards"][0]["shrink_pct"], 90)
        # 카드 수는 그대로였다는 점이 이 사고의 핵심 — 건수만 보면 통과했다.
        self.assertEqual(r["published_cards"], r["assembled_cards"])

    def test_missing_card_is_blocked(self):
        pub = _doc([_card("a", "x" * 100), _card("b", "y" * 100, 1)])
        asm = _doc([_card("a", "x" * 100)])
        r = g.compare(pub, asm)
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing_cards"], ["b"])

    def test_new_card_alone_is_not_regression(self):
        pub = _doc([_card("a", "x" * 100)])
        asm = _doc([_card("a", "x" * 100), _card("new", "n" * 100, 1)])
        r = g.compare(pub, asm)
        self.assertTrue(r["ok"])


class ThresholdTest(unittest.TestCase):
    def test_minor_shrink_passes(self):
        """파서 개선 등으로 몇 % 줄어드는 것은 회귀가 아니다(오탐 방지)."""
        pub = _doc([_card("a", "x" * 1000)])
        asm = _doc([_card("a", "x" * 950)])
        r = g.compare(pub, asm)
        self.assertTrue(r["ok"], r["shrunk_cards"])

    def test_threshold_is_configurable(self):
        pub = _doc([_card("a", "x" * 1000)])
        asm = _doc([_card("a", "x" * 900)])
        self.assertTrue(g.compare(pub, asm, max_shrink_pct=30.0)["ok"])
        self.assertFalse(g.compare(pub, asm, max_shrink_pct=5.0)["ok"])


class BundleShrinkTest(unittest.TestCase):
    """[2026-10-27] 묶음 카드(`merged_items`)는 소급 복구로 항목이 빠지는 것이 정상이다.

    10-26 호: 25건 묶음에서 20건을 정식 카드로 풀자 묶음이 −32.5% 가 돼 자동 재조립 PR 이
    막혔다. 빠진 항목만큼은 축소로 세지 않되, 그 외 내용이 줄면 종전대로 막는다."""

    ITEMS = [f"Firm {i} · FEI {1000 + i} · 실사 01/0{i % 9 + 1}/2026" for i in range(25)]

    def _bundle(self, cid, items, note="n" * 200):
        return {"id": cid, "render_order": 0, "merged_items": items,
                "merged_count": len(items), "summary": note}

    def test_items_removed_is_not_regression(self):
        pub = _doc([self._bundle("b", self.ITEMS)])
        asm = _doc([self._bundle("b", self.ITEMS[:5]),
                    *[_card(f"full-{i}", "z" * 300, i + 1) for i in range(20)]])
        r = g.compare(pub, asm)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["unbundled_cards"][0]["after_items"], 5)
        self.assertIn("묶음 축소", g.render_markdown(r, "x.json"))

    def test_other_content_loss_still_blocked(self):
        pub = _doc([self._bundle("b", self.ITEMS, note="n" * 4000)])
        asm = _doc([self._bundle("b", self.ITEMS[:5], note="n")])
        self.assertFalse(g.compare(pub, asm)["ok"])

    def test_new_item_disables_exemption(self):
        pub = _doc([self._bundle("b", self.ITEMS)])
        asm = _doc([self._bundle("b", ["Unrelated Firm · FEI 1"])])
        self.assertFalse(g.compare(pub, asm)["ok"])

    def test_representative_promoted_to_full_card(self):
        """대표가 정식 카드로 풀려 같은 id 가 묶음 아닌 카드로 남는 경우(10-26 2차 복구 실측).
        스캐폴드 스키마상 모든 카드가 `merged_items: []` 를 들고 있다 — 빈 목록은 묶음이 아니다."""
        pub = _doc([self._bundle("b-1", self.ITEMS[:12])])
        full = {"id": "b-1", "render_order": 0, "merged_items": [], "merged_count": 1,
                "summary": "f" * 2000}
        asm = _doc([full, self._bundle("b-2", self.ITEMS[1:8])])
        r = g.compare(pub, asm)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["unbundled_cards"][0]["now"], r["unbundled_cards"][0]["after_items"]),
                         ("b-2", 7))

    def test_representative_change_is_not_missing(self):
        """대표(가장 작은 id)가 복구·제외돼 묶음 id 가 바뀌어도 같은 묶음으로 본다."""
        pub = _doc([self._bundle("b-1", self.ITEMS)])
        asm = _doc([self._bundle("b-2", self.ITEMS[1:6])])
        r = g.compare(pub, asm)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["unbundled_cards"][0]["now"], "b-2")

    def test_bundle_fully_resolved_is_not_missing(self):
        """[2026-10-28] 항목이 전부 빠져 묶음 자체가 없어진 경우(10-26 호 기기·식품 7건 전부
        범위 밖 제외). 묶음 아닌 카드의 소실은 종전대로 막는다."""
        other = self._bundle("r-1", ["Recall item A", "Recall item B"])   # 무관한 다른 묶음
        pub = _doc([self._bundle("b-1", self.ITEMS[:7]), _card("keep", "k" * 300, 1), other])
        asm = _doc([_card("keep", "k" * 300, 0), other])
        r = g.compare(pub, asm)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["unbundled_cards"][0]["now"], r["unbundled_cards"][0]["after_items"]),
                         (None, 0))
        self.assertIn("묶음 해소", g.render_markdown(r, "x.json"))
        self.assertEqual(g.compare(pub, _doc([]))["missing_cards"], ["keep"])


class NoIdCardTest(unittest.TestCase):
    def test_cards_without_id_use_render_order(self):
        pub = _doc([{"render_order": 3, "body": "x" * 400}])
        asm = _doc([{"render_order": 3, "body": "x"}])
        r = g.compare(pub, asm)
        self.assertFalse(r["ok"])
        self.assertTrue(r["shrunk_cards"][0]["id"].endswith("3"))


class CliTest(unittest.TestCase):
    def _write(self, doc):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(doc, fh, ensure_ascii=False)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_missing_published_file_skips(self):
        """그 날짜 최초 발행 — 대조 대상이 없으니 통과(차단 아님)."""
        asm = self._write(_doc([_card("a", "x" * 100)]))
        self.assertEqual(g.main(["--published", asm + ".nope", "--assembled", asm]), 0)

    def test_regression_returns_1(self):
        pub = self._write(_doc([_card("a", "x" * 1000)]))
        asm = self._write(_doc([_card("a", "x")]))
        self.assertEqual(g.main(["--published", pub, "--assembled", asm]), 1)

    def test_clean_returns_0(self):
        pub = self._write(_doc([_card("a", "x" * 1000)]))
        asm = self._write(_doc([_card("a", "x" * 1000)]))
        self.assertEqual(g.main(["--published", pub, "--assembled", asm]), 0)

    def test_summary_file_is_appended(self):
        pub = self._write(_doc([_card("a", "x" * 1000)]))
        asm = self._write(_doc([_card("a", "x")]))
        summary = self._write({})
        g.main(["--published", pub, "--assembled", asm, "--summary", summary])
        text = open(summary, encoding="utf-8").read()
        self.assertIn("발행 회귀 가드 — 차단", text)
        self.assertIn("`a`", text)


if __name__ == "__main__":
    unittest.main()
