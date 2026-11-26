"""네이버 블로그 '왜 중요한가' 슬롯 가드 (2026-11-26).

편집팀 Routine 이 매주 web/data/naver_blog/<발행일>.json 에 블로그 전용 해설 2~3문장을 쓴다. 지키는 것
  1. 게이트는 render.naver_blog_slot_problems 한 곳 — 빌드·CLI(web/naver_blog_slot.py)·이 시험이 같이 부른다.
  2. 고른 483 과 같은 카드, 문장 2~3개·20~160자, 새 숫자 금지, 사이트 카드 문장과 겹침 ≤25%, 금지 표현·마크다운 없음.
  3. 커밋된 슬롯은 전부 게이트를 넘는다(Routine PR 을 CI 가 막는다).
  4. 게이트를 못 넘는 슬롯은 빌드가 쓰지 않는다(그 칸만 빠지고 발행은 계속).
  5. 슬롯 PR 은 워치독이 해부 슬롯과 같은 규율(폴더 한 곳 + 30분)로 머지한다.
"""
from __future__ import annotations

import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO = pathlib.Path(__file__).resolve().parent.parent
WEB = REPO / "web"
sys.path.insert(0, str(WEB))
import render  # noqa: E402
import naver_blog_slot  # noqa: E402

BRIEF_1123 = WEB / "data" / "briefs" / "brief_web_2026_11_23.json"
GOOD_WHY = [
    "시험실 기록과 절차가 어긋나면 출하 판정이 무엇에 근거했는지 설명하기 어려워집니다.",
    "무균 제품은 공정 중 한 번의 빈틈이 제품 전체의 신뢰로 번지므로 감시 체계부터 다시 보게 됩니다.",
]


def _brief():
    return json.loads(BRIEF_1123.read_text(encoding="utf-8"))


def _slot(card, why=None, **kw):
    s = {"schema": render.NAVER_BLOG_SLOT_SCHEMA, "pub": "2026-11-23",
         "card_id": card.get("id"), "why": list(GOOD_WHY if why is None else why)}
    s.update(kw)
    return s


@unittest.skipUnless(BRIEF_1123.exists(), "11-23 호 없음")
class SlotGateTest(unittest.TestCase):
    def setUp(self):
        self.card = render.naver_blog_pick_from_brief(_brief())
        self.assertIsNotNone(self.card)

    def test_good_slot_passes(self):
        self.assertEqual(render.naver_blog_slot_problems(_slot(self.card), self.card), [])

    def test_each_rule_catches_a_planted_violation(self):
        c = self.card
        cases = {
            "schema": _slot(c, schema="x"),
            "card_id": _slot(c, card_id="other"),
            "2~3": _slot(c, why=GOOD_WHY[:1]),
            "길이": _slot(c, why=["짧다", GOOD_WHY[1]]),
            "금지 표현": _slot(c, why=[GOOD_WHY[0] + "!", GOOD_WHY[1]]),
            "숫자": _slot(c, why=[GOOD_WHY[0], "같은 지적이 지난 3년간 4719건 있었다고 볼 수 있는 근거는 없습니다."]),
            "마크다운": _slot(c, why=["**" + GOOD_WHY[0], GOOD_WHY[1]]),
            "겹침": _slot(c, why=[c["summary"][:160], (c.get("implication") or c["summary"])[:160]]),
        }
        for name, slot in cases.items():
            with self.subTest(rule=name):
                self.assertTrue(render.naver_blog_slot_problems(slot, c), name)

    def test_numbers_from_the_card_are_allowed(self):
        n = str((self.card.get("deterministic_detail") or {}).get("count"))
        why = [f"관찰사항이 {n}건에 이르면 한두 건의 실수보다 체계의 문제로 읽힙니다.", GOOD_WHY[1]]
        self.assertEqual(render.naver_blog_slot_problems(_slot(self.card, why=why), self.card), [])

    def test_doc_gets_the_block_and_still_passes_hard_checks(self):
        b = _brief()
        cards = sorted([x for x in b["cards"] if render._is_renderable(x)],
                       key=lambda x: (x.get("render_order") is None, x.get("render_order")))
        views = [render._card_view(x, render._KO, "ko") for x in cards]
        doc = render.naver_blog_483_doc(cards, views, "https://grm-solutions.com/briefs/2026-11-23/",
                                        "2026년 11월 4주차", "2026-11-23", why=GOOD_WHY)
        heads = [blk[1] for blk in doc["blocks"] if blk[0] == "hp"]
        self.assertIn("왜 중요한가", heads)
        self.assertLess(heads.index("이번 문서의 쟁점"), heads.index("왜 중요한가"))
        for chk in render.naver_blog_checks(doc):
            if chk["hard"]:
                self.assertTrue(chk["ok"], chk)

    def test_build_skips_a_failing_slot(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_nbwhy_"))
        old = render.NAVER_BLOG_SLOT_DIR
        try:
            render.NAVER_BLOG_SLOT_DIR = tmp
            self.assertIsNone(render.load_naver_blog_why("2026-11-23", self.card), "파일 없음")
            (tmp / "2026-11-23.json").write_text(json.dumps(_slot(self.card, card_id="x")), encoding="utf-8")
            self.assertIsNone(render.load_naver_blog_why("2026-11-23", self.card), "게이트 미통과")
            (tmp / "2026-11-23.json").write_text(json.dumps(_slot(self.card), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(render.load_naver_blog_why("2026-11-23", self.card), GOOD_WHY)
        finally:
            render.NAVER_BLOG_SLOT_DIR = old
            shutil.rmtree(tmp, ignore_errors=True)


class CommittedSlotsTest(unittest.TestCase):
    def test_every_committed_slot_passes_the_gate(self):
        for path in sorted(render.NAVER_BLOG_SLOT_DIR.glob("*.json")) if render.NAVER_BLOG_SLOT_DIR.exists() else []:
            with self.subTest(slot=path.name):
                slot = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(slot.get("pub"), path.stem)
                brief = WEB / "data" / "briefs" / f"brief_web_{path.stem.replace('-', '_')}.json"
                self.assertTrue(brief.exists(), "그 호 브리프가 없다")
                card = render.naver_blog_pick_from_brief(json.loads(brief.read_text(encoding="utf-8")))
                self.assertEqual(render.naver_blog_slot_problems(slot, card), [])


@unittest.skipUnless(BRIEF_1123.exists(), "11-23 호 없음")
class CliTest(unittest.TestCase):
    def test_candidate_and_check(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(naver_blog_slot.main(["--brief", str(BRIEF_1123), "--candidate"]), 0)
        cand = json.loads(buf.getvalue())
        card = render.naver_blog_pick_from_brief(_brief())
        self.assertEqual(cand["card_id"], card["id"])
        self.assertIn("site_text_do_not_repeat", cand)
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_nbcli_"))
        try:
            good, bad = tmp / "g.json", tmp / "b.json"
            good.write_text(json.dumps(_slot(card), ensure_ascii=False), encoding="utf-8")
            bad.write_text(json.dumps(_slot(card, why=GOOD_WHY[:1]), ensure_ascii=False), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(naver_blog_slot.main(["--brief", str(BRIEF_1123), "--check", str(good)]), 0)
                self.assertEqual(naver_blog_slot.main(["--brief", str(BRIEF_1123), "--check", str(bad)]), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class WiringTest(unittest.TestCase):
    def test_build_reads_the_slot_and_watchdog_merges_slot_prs(self):
        src = (WEB / "render.py").read_text(encoding="utf-8")
        self.assertIn("load_naver_blog_why(pub,", src, "빌드가 슬롯을 읽는다")
        wd = (REPO / ".github" / "workflows" / "grm-stranded-batch-pr.yml").read_text(encoding="utf-8")
        self.assertIn("'naver-blog(why):'", wd)
        self.assertIn("path: 'web/data/naver_blog/'", wd)
        prompt = (REPO / "company" / "outreach" / "naver-blog-why.md").read_text(encoding="utf-8")
        for must in ("naver_blog_slot.py --candidate", "naver_blog_slot.py --check", "naver-blog(why): $PUB",
                     "grm-naver-blog-why/v1"):
            self.assertIn(must, prompt)


if __name__ == "__main__":
    unittest.main()
