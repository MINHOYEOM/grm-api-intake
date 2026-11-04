"""유사 사례 표시 층 정제(refineSimilarItems) 가드 — GRM_유사사례_정확도점검_2026-11-05.

findings.js 의 순수 함수(similarBigrams·similarDice·refineSimilarItems)를 잘라 node 로 실제 실행한다
(문자열 포함 검사가 아니라 동작 검사). node 가 없으면 건너뛴다.
임계값(0.35/0.50/10/0.8)은 표본 65건 기반 — 더 큰 표본으로 재확정되면 이 테스트의 경계값도 함께 바꾼다.
"""
import json
import pathlib
import shutil
import subprocess
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
JS = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _extract() -> str:
    a = JS.index("  var SIMILAR_GATED_SOURCES")
    b = JS.index("  function similarPill(")
    return JS[a:b]


def run_refine(items, base=None):
    prog = _extract() + "\nvar out = refineSimilarItems(%s, %s);\nconsole.log(JSON.stringify(out));" % (
        json.dumps(items, ensure_ascii=False), json.dumps(base or {}, ensure_ascii=False))
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def it(score, source="FDA 483", text="x" * 10, firm="A", dup=1, fid="f"):
    return {"finding_id": fid, "score": score, "source": source, "text": text, "firm_name": firm,
            "dup_documents": dup, "dup_findings": dup}


T1 = "귀 시설은 작업자 및 자재 흐름이 불량해질 수 있는 방식으로 설계 및/또는 운영되었다."
T1V = "귀 시설은 작업자 및 자재 흐름이 불량해질 수 있는 방식으로 설계 및 또는 운영되었다."  # 표현만 다른 변형
T2 = "무균으로 표방되는 의약품의 미생물 오염을 방지하도록 설계된 절차가 확립되지 않았다."


@unittest.skipUnless(NODE, "node 없음")
class RefineSimilarItemsTest(unittest.TestCase):
    def test_floor_applies_to_483_and_hc_only(self):
        out = run_refine([
            it(0.34, "FDA 483", T1, fid="a"), it(0.35, "FDA 483", T2, fid="b"),
            it(0.20, "Health Canada Inspection", "기록 추적성이 미흡했다 가나다라마바", fid="c"),
            it(0.10, "FDA Warning Letter", "경고서한 본문 가나다라마바사아자차", fid="d"),
            it(0.16, "MFDS", "식약처 본문 아자차카타파하가나다라", fid="e"),
        ])
        self.assertEqual([x["finding_id"] for x in out], ["b", "d", "e"])
        # 눈금이 검증되지 않은 출처는 확실도 문구를 붙이지 않는다
        self.assertEqual([x["_level"] for x in out], ["similar", "", ""])

    def test_level_boundary_050(self):
        out = run_refine([it(0.50, text=T1, fid="a"), it(0.4999, text=T2, fid="b")])
        self.assertEqual([x["_level"] for x in out], ["strong", "similar"])

    def test_translation_variants_fold_into_first(self):
        out = run_refine([it(0.6, text=T1, fid="a", firm="A", dup=1), it(0.58, text=T1V, fid="b", firm="C", dup=1),
                          it(0.5, text=T2, fid="c", firm="D")])
        self.assertEqual([x["finding_id"] for x in out], ["a", "c"])
        self.assertEqual(out[0]["_variants"], 1)
        self.assertEqual(out[1]["_variants"], 0)

    def test_different_sentences_not_folded(self):
        out = run_refine([it(0.6, text=T1, fid="a"), it(0.55, text=T2, fid="b")])
        self.assertEqual(len(out), 2)

    def test_common_phrase_has_no_case_label(self):
        out = run_refine([it(0.62, text=T1, dup=10, fid="a"), it(0.6, text=T2, dup=9, fid="b")])
        self.assertTrue(out[0]["_common"])
        self.assertEqual(out[0]["_level"], "")
        self.assertFalse(out[1]["_common"])
        self.assertEqual(out[1]["_level"], "strong")

    def test_same_firm_is_labelled_moved_last_not_dropped(self):
        out = run_refine([it(0.62, text=T1, firm="Acme  Pharma", fid="a"), it(0.5, text=T2, firm="Other", fid="b")],
                         {"firm_name": "acme pharma"})
        self.assertEqual([x["finding_id"] for x in out], ["b", "a"])
        self.assertEqual([x["_repeat"] for x in out], [False, True])

    def test_same_firm_not_folded_into_other_firm(self):
        out = run_refine([it(0.6, text=T1, firm="Other", fid="a"), it(0.58, text=T1V, firm="Me", fid="b")],
                         {"firm_name": "Me"})
        self.assertEqual(len(out), 2)

    def test_input_not_mutated_and_empty_ok(self):
        src = [it(0.6, text="abcdefghij klmnop")]
        before = json.dumps(src, separators=(",", ":"))
        prog = _extract() + "\nvar s = %s; refineSimilarItems(s, {}); console.log(JSON.stringify(s)); console.log(JSON.stringify(refineSimilarItems(null, null)));" % before
        r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(json.loads(lines[0]), src)
        self.assertEqual(lines[1], "[]")


class WiringTest(unittest.TestCase):
    def test_load_refines_before_render_and_state_fn_stays_dumb(self):
        self.assertIn("renderSimilarToState(block, refineSimilarItems(items, row));", JS)
        fn = JS[JS.index("function renderSimilarToState(block, items) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertNotIn(".sort(", fn)
        self.assertNotIn(".filter(", fn)

    def test_new_phrases_have_en_keys(self):
        en = json.loads((WEB_DIR / "data" / "i18n" / "en.json").read_text(encoding="utf-8"))
        for k in ("같은 요건 문구", "비슷한 요건", "같은 업체 반복", "자주 나오는 지적 · {n}개 문서",
                  "표현만 다른 {n}건", "유사한 사례를 찾지 못했습니다"):
            self.assertIn(k, en)
            self.assertIn('"%s"' % k, JS)

    def test_threshold_basis_is_documented_in_source(self):
        self.assertIn("표본 65건", JS)


if __name__ == "__main__":
    unittest.main()
