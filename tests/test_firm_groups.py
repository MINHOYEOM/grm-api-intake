"""업체 그룹 묶음 (2026-11-23 대표 결정) — 등록 원천·카드 판정·화면 묶음·옛 실사·등록 후보.

11-23 호에 Qilu 그룹 483 5장(시설 4곳)이 흩어져 '같은 회사가 세 번'으로 읽혔다. 결정:
카드는 시설·문서별 그대로, 화면에서 그룹 머리말로 묶어 나란히. 판정은 등록 근거(FEI)로만.
매주 자동 — 렌더가 등록 원천(web/data/firm_groups.json)을 읽고, 조립·사전 점검이 같은 후보 함수로 경고.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "company" / "editorial"))

import firm_groups as fg  # noqa: E402
import render  # noqa: E402
from grm_i18n import Translator  # noqa: E402

BRIEFS = ROOT / "web" / "data" / "briefs"
QILU_IDS = ["fda483-196372", "fda483-196373", "fda483-196374", "fda483-196408", "fda483-196409"]
QILU_FEI = {"3009831352", "3021324493", "3003802432", "3008250362"}


def _brief(date: str) -> dict:
    return json.loads((BRIEFS / f"brief_web_{date}.json").read_text(encoding="utf-8"))


def _card(cid: str, firm: str, fei: str = "", insp: str = "", pub: str = "2026-11-17",
          group: str = "글로벌", weight: str = "standard") -> dict:
    facts = [{"label": "발행일", "value": pub}, {"label": "문서번호", "value": cid},
             {"label": "제조소/업체", "value": f"{firm} · FEI {fei}" if fei else firm},
             {"label": "시설 · 유형", "value": "Manufacturer · 483"}]
    if insp:
        facts.append({"label": "실사일", "value": insp})
    return {"id": cid, "group": group, "group_label": "▫️ 기타", "agency": "FDA",
            "card_type": "FDA 483 실사 관찰", "type_tag": "483", "headline_target": firm,
            "title_issue": "t", "summary": "s", "facts": facts, "card_weight": weight,
            "sources": {"official_url": f"https://www.fda.gov/media/{cid[-6:]}/download"}}


def _sections(cards: list[dict], lang: str = "ko") -> list[dict]:
    tr = Translator(lang)
    cards = sorted(cards, key=lambda c: c.get("render_order", 0))
    views = [render._card_view(c, tr, lang) for c in cards]
    render._annotate_firm_groups(views, cards, tr)
    return render._build_sections(views, tr)


def _flat(sections: list[dict]) -> list[dict]:
    return [cv for s in sections for cv in s["cards"]]


# ─────────────────────────────────────────────────────────────────────────────
class RegistryTest(unittest.TestCase):
    def test_registry_valid_and_qilu_first_entry(self):
        reg = fg.load_registry()
        self.assertEqual(fg.validate_registry(reg), [])
        qilu = reg["groups"][0]
        self.assertEqual(qilu["key"], "qilu-pharmaceutical")
        self.assertEqual({m["fei"] for m in qilu["members"]}, QILU_FEI)
        for m in qilu["members"]:
            self.assertTrue(m["legal_name"] and m["address"] and m["evidence"])

    def test_validator_rejects_unbacked_entries(self):
        base = fg.load_registry()
        bad = copy.deepcopy(base)
        bad["groups"][0]["members"][0]["evidence"] = []
        self.assertTrue(any("evidence" in e for e in fg.validate_registry(bad)))
        bad = copy.deepcopy(base)
        bad["groups"][0]["members"][1]["address"] = ""
        self.assertTrue(any("address" in e for e in fg.validate_registry(bad)))
        bad = copy.deepcopy(base)
        bad["groups"][0]["members"] = bad["groups"][0]["members"][:1]
        self.assertTrue(any("2곳 미만" in e for e in fg.validate_registry(bad)))
        bad = copy.deepcopy(base)
        dup = copy.deepcopy(bad["groups"][0])
        dup["key"] = "other"
        bad["groups"].append(dup)
        self.assertTrue(any("에도 있다" in e for e in fg.validate_registry(bad)))
        bad = copy.deepcopy(base)
        bad["groups"][0]["basis"] = ""
        self.assertTrue(any("basis" in e for e in fg.validate_registry(bad)))


class CardJudgeTest(unittest.TestCase):
    def test_fei_from_facts(self):
        self.assertEqual(fg.card_fei(_card("fda483-1", "X Co", "3008250362")), "3008250362")
        self.assertEqual(fg.card_fei(_card("fda483-2", "X Co")), "")

    def test_cluster_key_registered_vs_fei_vs_none(self):
        self.assertEqual(fg.cluster_key(_card("a", "Qilu X", "3003802432")), "group:qilu-pharmaceutical")
        self.assertEqual(fg.cluster_key(_card("b", "Other", "1234567")), "fei:1234567")
        self.assertEqual(fg.cluster_key(_card("c", "Other")), "")

    def test_similar_name_never_groups(self):
        # 이름이 같은 계열이라도 FEI 가 다르고 미등록이면 다른 묶음이다.
        a = _card("a", "Jiangsu Hengrui Pharmaceuticals", "3006689263")
        b = _card("b", "Jiangsu Hengrui Pharmaceuticals Co., Ltd", "3011603969")
        self.assertNotEqual(fg.cluster_key(a), fg.cluster_key(b))

    def test_old_inspection_threshold(self):
        self.assertEqual(fg.OLD_INSPECTION_DAYS, 730)
        self.assertTrue(fg.is_old_inspection(_card("a", "X", insp="04/28/2022")))
        self.assertFalse(fg.is_old_inspection(_card("a", "X", insp="05/01/2025")))
        self.assertFalse(fg.is_old_inspection(_card("a", "X", insp="04/16/0226")))   # 원문 오기
        self.assertFalse(fg.is_old_inspection(_card("a", "X", insp="12/01/2026")))   # 게시 뒤
        self.assertFalse(fg.is_old_inspection(_card("a", "X")))


class RenderClusterTest(unittest.TestCase):
    def _scattered(self) -> list[dict]:
        cs = [_card("fda483-1", "Qilu Antibiotics", "3009831352", "05/30/2023"),
              _card("fda483-2", "Alpha", "1111111", "05/01/2026"),
              _card("fda483-3", "Qilu Pharmaceutical Co.", "3003802432", "07/29/2024"),
              _card("fda483-4", "Beta", "2222222", "05/01/2026"),
              _card("fda483-5", "Qilu (Hainan)", "3008250362", "05/19/2023", weight="deep")]
        for i, c in enumerate(cs):
            c["render_order"] = i
        return cs

    def test_members_adjacent_with_ko_header(self):
        flat = _flat(_sections(self._scattered()))
        ids = [cv["anchor"] for cv in flat]
        # 묶음 무게 = 가장 무거운 구성원(deep) → 묶음이 맨 앞, 구성원끼리는 원래 순서.
        self.assertEqual(ids, ["fda483-1", "fda483-3", "fda483-5", "fda483-2", "fda483-4"])
        self.assertTrue(flat[0].get("fgrp_open"))
        self.assertTrue(flat[2].get("fgrp_close"))
        self.assertEqual(flat[0]["fgrp_head"], "Qilu Pharmaceutical 그룹 — 시설 3곳 · 483 3건")
        self.assertFalse(any(cv.get("fgrp_open") for cv in flat[1:]))

    def test_en_header_from_view(self):
        flat = _flat(_sections(self._scattered(), "en"))
        self.assertEqual(flat[0]["fgrp_head"], "Qilu Pharmaceutical group — 3 sites · 3 Form 483s")

    def test_same_fei_cluster_header(self):
        cs = [_card("fda483-1", "Rechon Life Science AB", "3002806978"), _card("fda483-2", "Other", "9999999"),
              _card("fda483-3", "Rechon Life Science AB", "3002806978")]
        for i, c in enumerate(cs):
            c["render_order"] = i
        flat = _flat(_sections(cs))
        self.assertEqual([cv["anchor"] for cv in flat], ["fda483-1", "fda483-3", "fda483-2"])
        self.assertEqual(flat[0]["fgrp_head"], "Rechon Life Science AB — 483 2건")

    def test_single_member_and_other_section_not_grouped(self):
        cs = [_card("fda483-1", "Qilu A", "3009831352"), _card("fda483-2", "Alpha", "1111111"),
              _card("fda483-3", "Qilu B", "3003802432", group="국내")]
        for i, c in enumerate(cs):
            c["render_order"] = i
        flat = _flat(_sections(cs))
        self.assertEqual([cv["anchor"] for cv in flat], ["fda483-1", "fda483-2", "fda483-3"])
        self.assertFalse(any(cv.get("fgrp_open") or cv.get("fgroup_key") for cv in flat))

    def test_no_groups_no_weights_is_identity(self):
        cs = [_card(f"fda483-{i}", f"F{i}", str(1000000 + i), weight="") for i in range(4)]
        for i, c in enumerate(reversed(cs)):
            c["render_order"] = i
        views = [render._card_view(c) for c in sorted(cs, key=lambda c: c["render_order"])]
        self.assertIs(render._order_by_weight(views), views)

    def test_old_inspection_batch_vs_single(self):
        cs = [_card("fda483-1", "A", "1111111", "04/28/2022"), _card("fda483-2", "B", "2222222", "05/01/2022"),
              _card("fda483-3", "C", "3333333", "05/01/2022", pub="2026-11-10"),
              _card("fda483-4", "D", "4444444", "05/01/2026")]
        for i, c in enumerate(cs):
            c["render_order"] = i
        flat = {cv["anchor"]: cv for cv in _flat(_sections(cs))}
        self.assertEqual(flat["fda483-1"]["old_insp"]["label"], "옛 실사 일괄 공개")
        self.assertEqual(flat["fda483-1"]["old_insp"]["date"], "2022-04-28")
        self.assertEqual(flat["fda483-1"]["old_insp"]["lag"], "게시 4년 전 실사")
        self.assertEqual(flat["fda483-3"]["old_insp"]["label"], "옛 실사 공개")
        self.assertNotIn("old_insp", flat["fda483-4"])
        en = {cv["anchor"]: cv for cv in _flat(_sections(cs, "en"))}
        self.assertEqual(en["fda483-1"]["old_insp"]["label"], "Older inspection, batch release")
        self.assertEqual(en["fda483-1"]["old_insp"]["date_label"], "Inspected 2022-04-28")


class Brief1123Test(unittest.TestCase):
    """실제 11-23 호 — Qilu 5장이 한 묶음, 국·영 머리말."""

    def test_qilu_five_one_box(self):
        b = _brief("2026_11_23")
        for lang, head in (("ko", "Qilu Pharmaceutical 그룹 — 시설 4곳 · 483 5건"),
                           ("en", "Qilu Pharmaceutical group — 4 sites · 5 Form 483s")):
            flat = _flat(_sections(b["cards"], lang))
            idx = [i for i, cv in enumerate(flat) if cv.get("fgroup_key") == "group:qilu-pharmaceutical"]
            self.assertEqual(len(idx), 5, lang)
            self.assertEqual(idx, list(range(idx[0], idx[0] + 5)), lang)   # 연속
            self.assertEqual(sorted(flat[i]["anchor"] for i in idx), QILU_IDS)
            self.assertEqual(flat[idx[0]]["fgrp_head"], head)
            self.assertTrue(flat[idx[-1]].get("fgrp_close"))
            self.assertTrue(all(flat[i].get("old_insp") for i in idx))

    def test_card_data_unchanged(self):
        b = _brief("2026_11_23")
        before = copy.deepcopy(b["cards"])
        _sections(b["cards"])
        self.assertEqual(b["cards"], before)


class NoFalseGroupingTest(unittest.TestCase):
    """지난 호들 — 묶음은 등록 그룹 아니면 FEI 하나뿐(거짓 묶음 0)."""

    def test_recent_briefs(self):
        reg_idx = fg.fei_index(fg.default_registry())
        for d in ("2026_10_26", "2026_11_02", "2026_11_09", "2026_11_16", "2026_11_23"):
            b = _brief(d)
            raw = {c["id"]: c for c in b["cards"]}
            boxes: dict[str, set[str]] = {}
            for cv in _flat(_sections(b["cards"])):
                k = cv.get("fgroup_key")
                if k:
                    boxes.setdefault(k, set()).add(fg.card_fei(raw[cv["anchor"]]))
            for k, feis in boxes.items():
                if k.startswith("fei:"):
                    self.assertEqual(feis, {k[4:]}, (d, k))
                else:
                    self.assertTrue(all(reg_idx[f]["key"] == k[6:] for f in feis), (d, k))


class CandidateWarnTest(unittest.TestCase):
    def test_unregistered_family_warns_registered_silent(self):
        cards = [_card("a", "Jiangsu Hengrui Pharmaceuticals", "3006689263"),
                 _card("b", "Jiangsu Hengrui Pharmaceuticals Co., Ltd", "3011603969"),
                 _card("c", "Qilu Antibiotics", "3009831352"), _card("d", "Qilu Pharmaceutical", "3003802432"),
                 _card("e", "Rechon AB", "3002806978"), _card("f", "Rechon AB", "3002806978")]
        lines = fg.candidate_lines(cards)
        self.assertEqual(len(lines), 1)
        self.assertIn("그룹 등록 후보: 'hengrui' 계열 2장", lines[0])
        self.assertIn("firm_groups.json", lines[0])

    def test_family_token(self):
        self.assertEqual(fg.family_token("Jiangsu Hengrui Pharmaceuticals"), "hengrui")
        self.assertEqual(fg.family_token("The Wellness Pharmacy"), "wellness")
        self.assertEqual(fg.family_token("(주)현진제약"), "")

    def test_1123_candidates(self):
        fams = {c["family"] for c in fg.candidate_groups(_brief("2026_11_23")["cards"])}
        self.assertNotIn("qilu", fams)
        self.assertIn("hengrui", fams)

    def test_precheck_and_assembler_call_same_function(self):
        import precheck
        b = _brief("2026_11_23")
        delta = {"cards": {c["id"]: {} for c in b["cards"]}}
        self.assertEqual(precheck.group_candidate_lines(delta, b), fg.candidate_lines(b["cards"]))
        self.assertEqual(precheck.group_candidate_lines(delta, None), [])
        src = (ROOT / "assemble_publish_brief.py").read_text(encoding="utf-8")
        self.assertIn("firm_groups.candidate_lines(adopted_cards", src)


class RowRunBoundaryTest(unittest.TestCase):
    def test_row_table_breaks_at_group_box(self):
        cs = [_card("fda483-1", "Solo", "1111111", weight="row"),
              _card("fda483-2", "Rechon AB", "3002806978", weight="row"),
              _card("fda483-3", "Rechon AB", "3002806978", weight="row")]
        for i, c in enumerate(cs):
            c["render_order"] = i
        flat = _flat(_sections(cs))
        self.assertTrue(flat[0].get("row_open") and flat[0].get("row_close"))
        self.assertTrue(flat[1].get("row_open") and flat[1].get("fgrp_open"))
        self.assertTrue(flat[2].get("row_close") and flat[2].get("fgrp_close"))


class WiringTest(unittest.TestCase):
    """매주 자동 — 발행 경로(렌더 국·영 호출부·템플릿·사전 점검 CLI)에 실제로 꽂혀 있는가."""

    def test_render_call_sites_both_languages(self):
        src = (ROOT / "web" / "render.py").read_text(encoding="utf-8")
        self.assertIn("_annotate_firm_groups(card_views, cards_sorted, tr)", src)
        self.assertIn("_annotate_firm_groups(en_card_views, cards_sorted, en_tr)", src)

    def test_templates_carry_box_and_old_inspection(self):
        brief = (ROOT / "web" / "templates" / "brief.html").read_text(encoding="utf-8")
        card = (ROOT / "web" / "partials" / "card.html").read_text(encoding="utf-8")
        self.assertIn("card.fgrp_open", brief)
        self.assertIn("card.fgrp_close", brief)
        self.assertIn("{{ card.fgrp_head }}", brief)
        self.assertIn("data-old-insp", brief)          # 요약보기 줄에도
        self.assertIn("card.old_insp.date_label", card)
        self.assertIn('data-old-insp="', card)

    def test_precheck_terms_prints_candidate(self):
        import contextlib
        import io
        import tempfile
        import precheck
        b = _brief("2026_11_23")
        with tempfile.TemporaryDirectory() as td:
            dp, sp = pathlib.Path(td, "delta.json"), pathlib.Path(td, "scaffold.json")
            dp.write_text(json.dumps({"cards": {"fda483-196351": {}, "fda483-196407": {}}}), encoding="utf-8")
            sp.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                precheck.main(["terms", str(dp), "--scaffold", str(sp)])
        self.assertIn("경고: 그룹 등록 후보: 'hengrui' 계열 2장", buf.getvalue())


class I18nTest(unittest.TestCase):
    def test_en_keys_present(self):
        cat = json.loads((ROOT / "web" / "data" / "i18n" / "en.json").read_text(encoding="utf-8"))
        for k in ("{name} 그룹", "시설 {n}곳", "483 {n}건", "경고서한 {n}건", "옛 실사 일괄 공개",
                  "옛 실사 공개", "실사일 {date}", "게시 {n}년 전 실사"):
            self.assertIn(k, cat)


if __name__ == "__main__":
    unittest.main()
