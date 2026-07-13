"""assemble_publish_brief 유닛 + known-good 재현 테스트.

known-good: 2026-07-06 머지 발행본을 truth 로 두고, 그로부터 '빈슬롯 스캐폴드'(슬롯 blank +
Tier1 가짜 카드 삽입)를 역산해, assemble_publish_brief 가 truth 를 그대로 재현하는지
(채택 필터·render_order 재배열·메타 재계산) 검증한다.

fixture(tests/fixtures/): 발행본·델타를 라이브 web/data/briefs 와 분리해 동결(MULTI_GOLDENS 동형).
이렇게 하면 이 테스트가 발행 파이프(주간 briefs 교체)나 별도 발행 PR 과 결합하지 않는다.
경로는 GRM_TRUTH_BRIEF / GRM_TRUTH_DELTA 로 덮어쓸 수 있다(임의 실발행본 재검증용).
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import assemble_publish_brief as apb  # noqa: E402
import inject_slots  # noqa: E402

TRUTH_PATH = pathlib.Path(os.environ.get(
    "GRM_TRUTH_BRIEF", ROOT / "tests" / "fixtures" / "brief_web_2026_07_06.json"))
DELTA_PATH = pathlib.Path(os.environ.get(
    "GRM_TRUTH_DELTA", ROOT / "tests" / "fixtures" / "delta_2026_07_06.json"))

_STR_SLOTS = ("title_issue", "summary", "implication")
_LIST_SLOTS = ("key_facts", "checks")


def _blank_slots(card: dict) -> dict:
    """카드의 LLM 슬롯을 스캐폴드 빈 placeholder 로 되돌린다."""
    c = copy.deepcopy(card)
    for k in _STR_SLOTS:
        c[k] = ""
    for k in _LIST_SLOTS:
        c[k] = []
    for q in c.get("quotes") or []:
        if isinstance(q, dict) and q.get("translation") not in (None, ""):
            q["translation"] = ""  # 비KO 자리만 빈칸(KO=None 보존), inject 가 다시 채움
    return c


def _fake_tier1_card(cid: str, render_order: int) -> dict:
    """델타에 없는(=Skipped) 가짜 Tier1 카드. 빈 슬롯 + 이질 agency/category."""
    return {
        "id": cid, "render_order": render_order, "signal_tier": 1,
        "agency": "EMA", "category": "Guideline", "evidence_level": "C",
        "title_issue": "", "summary": "", "implication": "",
        "key_facts": [], "checks": [], "quotes": [], "sources": [],
        "headline_target": "x", "signal_label": "관찰", "facts": [],
    }


def _blank_scaffold_from(truth: dict) -> dict:
    s = copy.deepcopy(truth)
    s["cards"] = [_blank_slots(c) for c in truth["cards"]]
    s["brief"]["coverage"] = {"intake_total": 89, "rendered": len(s["cards"]),
                              "evidence": {"A": 0, "B": 0, "C": 0}}
    s["brief"]["agencies"] = []
    s["brief"]["categories"] = []
    return s


class TestReproduceKnownGood(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
        cls.delta = json.loads(DELTA_PATH.read_text(encoding="utf-8"))
        # [업계 브리핑 노트 2026-07-13] truth(2026-07-06 발행본, resource notes 기능 도입 전
        # 머지분)에는 ECA GMP News 2장이 이벤트 카드로 섞여 있다. extract_resource_notes 는
        # 정본 함수이므로 이걸로 truth 를 분리해 "이벤트만" 기대치를 만든다(기능 도입 후
        # assemble_publish_brief 는 이 2장을 resources 로 옮기므로, 옛 truth["cards"] 그대로와
        # 비교하면 실패한다 — §4 정합성 점검에 따른 최소 보정).
        cls.truth_events, cls.truth_resources = apb.extract_resource_notes(cls.truth["cards"])

    def _pseudo_scaffold(self):
        """truth 로부터 89-style 스캐폴드 역산: 채택 61(빈슬롯) + Tier1 가짜 3,
        render_order 비연속(×2 + 홀수 삽입)으로 흩뿌려 재배열 로직을 실제로 시험."""
        s = copy.deepcopy(self.truth)
        blanked = [_blank_slots(c) for c in s["cards"]]
        for i, c in enumerate(blanked):
            c["render_order"] = i * 2
        fakes = [_fake_tier1_card("ema-ghost-%d" % k, k * 2 + 1) for k in range(3)]
        s["cards"] = blanked + fakes
        s["brief"]["coverage"] = {"intake_total": 89, "rendered": len(s["cards"]),
                                  "evidence": {"A": 0, "B": 0, "C": 0}}
        s["brief"]["agencies"] = []
        s["brief"]["categories"] = []
        return s

    def test_reproduces_truth(self):
        scaffold = self._pseudo_scaffold()
        out, report = apb.assemble_publish_brief(scaffold, self.delta, strict=True)
        self.assertEqual(report.adopted, len(self.truth_events))
        self.assertEqual(report.resources, len(self.truth_resources))
        self.assertEqual(report.dropped, 3)
        self.assertEqual(sorted(report.dropped_ids),
                         ["ema-ghost-0", "ema-ghost-1", "ema-ghost-2"])
        self.assertEqual([c["id"] for c in out["cards"]],
                         [c["id"] for c in self.truth_events])
        self.assertEqual([c["render_order"] for c in out["cards"]],
                         list(range(len(self.truth_events))))
        for oc, tc in zip(out["cards"], self.truth_events):
            for k in _STR_SLOTS + _LIST_SLOTS:
                self.assertEqual(oc.get(k), tc.get(k), f"{tc['id']}.{k}")
        # agencies = event 카드 + resource 노트 agency 합집합(카드 순서 우선) — truth 원본은
        # resource 분리 이전 값이라 순서가 다를 수 있으므로, 같은 산식으로 기대치를 재계산한다.
        expected_agencies = apb._distinct_in_order(
            [c.get("agency", "") for c in self.truth_events]
            + [r.get("agency", "") for r in self.truth_resources])
        self.assertEqual(out["brief"]["agencies"], expected_agencies)
        self.assertEqual(out["brief"]["categories"], self.truth["brief"]["categories"])
        # evidence 집계는 이벤트 카드 기준(resource 는 배지 미렌더) — truth_events 로 재계산.
        expected_evidence = {"A": 0, "B": 0, "C": 0}
        for c in self.truth_events:
            lvl = c.get("evidence_level")
            if lvl in expected_evidence:
                expected_evidence[lvl] += 1
        self.assertEqual(out["brief"]["coverage"]["evidence"], expected_evidence)
        self.assertEqual(out["brief"]["coverage"]["rendered"], len(self.truth_events))
        self.assertEqual(out["brief"]["coverage"]["intake_total"], 89)
        self.assertEqual(out["brief"]["tldr"], self.delta["tldr"])
        if self.truth_resources:
            self.assertEqual(out["brief"].get("resources"), self.truth_resources)
            self.assertEqual(out["brief"]["coverage"].get("resources"),
                             len(self.truth_resources))
        else:
            self.assertNotIn("resources", out["brief"])
            self.assertNotIn("resources", out["brief"]["coverage"])

    def test_verbatim_fields_unchanged(self):
        scaffold = self._pseudo_scaffold()
        out, _ = apb.assemble_publish_brief(scaffold, self.delta, strict=True)
        byid = {c["id"]: c for c in out["cards"]}
        for tc in self.truth_events:
            oc = byid[tc["id"]]
            for k in ("facts", "sources", "headline_target", "signal_label", "agency",
                      "category", "evidence_level", "id"):
                self.assertEqual(oc.get(k), tc.get(k), f"{tc['id']}.{k} verbatim drift")


class TestGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.delta = json.loads(DELTA_PATH.read_text(encoding="utf-8"))
        cls.truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))

    def test_ghost_delta_id_errors(self):
        """델타에 스캐폴드에 없는 id → strict 거부."""
        s = _blank_scaffold_from(self.truth)
        s["cards"] = s["cards"][:-1]  # 마지막 카드 제거 → 델타엔 있으나 스캐폴드엔 없음
        with self.assertRaises(apb.AssembleError):
            apb.assemble_publish_brief(s, self.delta, strict=True)

    def test_empty_adopted_slot_errors(self):
        """채택 카드가 델타 없이 빈 슬롯이면 거부."""
        s = _blank_scaffold_from(self.truth)
        d = copy.deepcopy(self.delta)
        victim = next(iter(d["cards"]))
        d["cards"][victim] = {}  # 슬롯 없음 → 채택인데 빈 슬롯
        with self.assertRaises((apb.AssembleError, inject_slots.SlotInjectionError)):
            apb.assemble_publish_brief(s, d, strict=True)

    def test_determinism(self):
        """같은 입력 → 바이트 동일 출력."""
        s = _blank_scaffold_from(self.truth)
        o1, _ = apb.assemble_publish_brief(s, self.delta, strict=True)
        o2, _ = apb.assemble_publish_brief(s, self.delta, strict=True)
        d1 = json.dumps(o1, ensure_ascii=False, sort_keys=True)
        d2 = json.dumps(o2, ensure_ascii=False, sort_keys=True)
        self.assertEqual(d1, d2)


class TestDeepAnalysisWiring(unittest.TestCase):
    """assemble_publish_brief(deep_deltas=...) — additive 배선 검증. 실제 게이트 로직은
    verify_deep_analysis 자체 테스트가 담당하므로, 여기선 '연결이 되는지'만 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls.truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
        cls.delta = json.loads(DELTA_PATH.read_text(encoding="utf-8"))
        cls.truth_events, _truth_resources = apb.extract_resource_notes(cls.truth["cards"])

    def _scaffold(self):
        return _blank_scaffold_from(self.truth)

    def test_no_deep_deltas_unchanged(self):
        """deep_deltas 미지정 — 기존 동작과 바이트 동일(회귀 가드)."""
        s = self._scaffold()
        out_old, _ = apb.assemble_publish_brief(s, self.delta, strict=True)
        out_new, _ = apb.assemble_publish_brief(s, self.delta, strict=True, deep_deltas=None)
        self.assertEqual(
            json.dumps(out_old, ensure_ascii=False, sort_keys=True),
            json.dumps(out_new, ensure_ascii=False, sort_keys=True))

    def test_deep_gate_fail_does_not_block_publish(self):
        """게이트 FAIL(구조 불완전) deep_deltas 를 줘도 assemble 은 계속 성공해야 한다
        (카드 단위 graceful degrade — 전체 발행은 안 막힘)."""
        s = self._scaffold()
        first_id = self.truth["cards"][0]["id"]
        deep_deltas = {first_id: {"deep_analysis": {"key_violations": ""}, "source_text": "x"}}
        out, report = apb.assemble_publish_brief(
            s, self.delta, strict=True, deep_deltas=deep_deltas)
        self.assertTrue(report.ok)  # errors 는 비어야 함(FAIL 은 warnings 로만 기록)
        self.assertTrue(any("[deep]" in w for w in report.warnings))

    def test_deep_delta_for_non_target_card_is_noop(self):
        """deep_analysis_ready=False 카드(=scaffold 에 deep_analysis 키 자체 없음)에 대한
        델타는 무시되고 발행은 정상 진행되어야 한다."""
        s = self._scaffold()
        out, report = apb.assemble_publish_brief(
            s, self.delta, strict=True,
            deep_deltas={"no-such-card-id": {"deep_analysis": {}, "source_text": ""}})
        self.assertTrue(report.ok)
        self.assertEqual(report.adopted, len(self.truth_events))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class MergeFda483DisclosuresTest(unittest.TestCase):
    """[2026-07-13] 관찰 원문 없는 483 공개 카드 다건 → 목록카드 1장."""

    @staticmethod
    def _card(cid, firm, insp, detail=None, deep=None):
        c = {"id": cid, "type_tag": "483", "render_order": 0,
             "title_issue": "x", "summary": "s", "key_facts": ["k"],
             "implication": "i", "checks": ["c"],
             "headline_target": firm,
             "facts": [{"label": "제조소/업체", "value": firm},
                       {"label": "실사일", "value": insp}]}
        if detail:
            c["deterministic_detail"] = detail
        if deep:
            c["deep_analysis"] = deep
        return c

    def test_content_less_483_folded_into_one(self):
        cards = [
            self._card("fda483-2", "Beta Corp", "01/01/2024"),
            self._card("fda483-1", "Alpha Corp", "02/02/2024"),
            self._card("fda483-3", "Gamma Corp", "03/03/2024"),
            self._card("fda483-9", "Rich Corp", "04/04/2026",
                       detail={"type": "fda_483_observations", "count": 2}),  # 상세有 → 유지
            {"id": "admin-1", "type_tag": "admin", "facts": []},              # 483 아님 → 유지
        ]
        out = apb.merge_fda483_disclosures(cards)
        ids = [c["id"] for c in out]
        # content-less 3장 → 1장(id 오름차순 대표=fda483-1), 상세483·admin 유지
        self.assertEqual(len(out), 3)
        self.assertIn("fda483-1", ids)      # 대표
        self.assertIn("fda483-9", ids)      # 상세 483 유지
        self.assertIn("admin-1", ids)       # 비483 유지
        self.assertNotIn("fda483-2", ids)   # 접힘
        rep = next(c for c in out if c["id"] == "fda483-1")
        self.assertEqual(rep["merged_count"], 3)
        self.assertEqual(rep["merged_noun"], "건")
        self.assertEqual(len(rep["merged_items"]), 3)
        self.assertIn("3건", rep["summary"])

    def test_single_content_less_483_unchanged(self):
        cards = [self._card("fda483-1", "Alpha", "01/01/2024")]
        self.assertEqual(apb.merge_fda483_disclosures(cards), cards)  # 1건 무변화


class ExtractResourceNotesTest(unittest.TestCase):
    """[업계 브리핑 노트 2026-07-13] extract_resource_notes 단위 테스트(순수 함수)."""

    @staticmethod
    def _card(cid, agency, type_tag="", card_type="", **extra):
        c = {"id": cid, "agency": agency, "type_tag": type_tag, "card_type": card_type,
             "title_issue": f"{cid}-issue", "headline_target": f"{cid}-target",
             "summary": f"{cid}-summary",
             "sources": {"info_url": "https://rss.example/feed.xml",
                        "official_url": f"https://example.com/{cid}"}}
        c.update(extra)
        return c

    def test_eca_news_separated_events_remain(self):
        # ① ECA GMP News → resource 분리, 그 외 카드는 이벤트로 잔존(순서보존).
        cards = [
            self._card("fda-1", "FDA", card_type="Warning Letter"),
            self._card("eca-1", "ECA", type_tag="GMP News", card_type="규제 소식"),
            self._card("mfds-1", "MFDS", card_type="행정처분"),
        ]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual([c["id"] for c in events], ["fda-1", "mfds-1"])
        self.assertEqual(len(resources), 1)
        r = resources[0]
        self.assertEqual(r["id"], "eca-1")
        self.assertEqual(r["title"], "eca-1-issue")
        self.assertEqual(r["original_title"], "eca-1-target")
        self.assertEqual(r["summary"], "eca-1-summary")
        self.assertEqual(r["agency"], "ECA")
        self.assertEqual(r["type_tag"], "GMP News")
        self.assertEqual(r["sources"], cards[1]["sources"])

    def test_eca_card_type_variant_also_separated(self):
        # card_type=='규제 소식' 만으로도(type_tag 부재) resource 판정(OR 조건).
        cards = [self._card("eca-2", "ECA", type_tag="", card_type="규제 소식")]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual(events, [])
        self.assertEqual(len(resources), 1)

    def test_non_resource_agency_unchanged(self):
        # ② RESOURCE_AGENCIES 외 기관은 type_tag/card_type 이 같아도 무변화(agency 게이트 우선).
        cards = [self._card("x-1", "RAPS", type_tag="GMP News", card_type="규제 소식")]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual(events, cards)
        self.assertEqual(resources, [])

    def test_eca_wrong_type_unchanged(self):
        # agency=ECA 라도 type_tag/card_type 조건을 만족 못하면 이벤트로 남는다.
        cards = [self._card("eca-3", "ECA", type_tag="Recall", card_type="회수")]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual(events, cards)
        self.assertEqual(resources, [])

    def test_no_resources_returns_all_as_events(self):
        cards = [self._card("fda-1", "FDA"), self._card("mfds-1", "MFDS")]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual(events, cards)
        self.assertEqual(resources, [])


class ResourceNotesPipelineTest(unittest.TestCase):
    """[업계 브리핑 노트 2026-07-13] assemble_publish_brief 배선 — 0건/합집합 케이스.

    truth(2026-07-06 발행본)에는 ECA GMP News 2장이 실제로 섞여 있어(§1 실데이터 근거)
    이를 필터링해 '0건' 케이스를 합성하고, 원본으로 '합집합' 케이스를 검증한다.
    """

    @classmethod
    def setUpClass(cls):
        cls.truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
        cls.delta = json.loads(DELTA_PATH.read_text(encoding="utf-8"))

    def test_zero_resources_key_omitted(self):
        # ③ ECA 카드를 제거한 truth/delta → resources 0건이면 brief.resources/
        # coverage.resources 키 자체가 없어야 한다(하위호환 — 기존 소비자 무영향).
        truth = copy.deepcopy(self.truth)
        delta = copy.deepcopy(self.delta)
        eca_ids = {c["id"] for c in truth["cards"] if c.get("agency") == "ECA"}
        self.assertTrue(eca_ids)  # 전제 확인(고정 fixture 회귀 가드 — 0건이면 이 테스트 무의미)
        truth["cards"] = [c for c in truth["cards"] if c["id"] not in eca_ids]
        for cid in eca_ids:
            delta["cards"].pop(cid, None)
        s = _blank_scaffold_from(truth)
        out, report = apb.assemble_publish_brief(s, delta, strict=True)
        self.assertEqual(report.resources, 0)
        self.assertNotIn("resources", out["brief"])
        self.assertNotIn("resources", out["brief"]["coverage"])
        self.assertNotIn("ECA", out["brief"]["agencies"])

    def test_agencies_union_includes_resource_agency(self):
        # ④ agencies = event 카드 + resource 노트 agency 합집합(중복 제거, 이벤트 순서 우선) —
        # ECA 가 리소스로 빠져도 헤더 기관 목록에서 사라지지 않는다.
        s = _blank_scaffold_from(self.truth)
        out, report = apb.assemble_publish_brief(s, self.delta, strict=True)
        self.assertGreater(report.resources, 0)
        self.assertIn("ECA", out["brief"]["agencies"])
        event_agencies = {c.get("agency") for c in out["cards"]}
        self.assertNotIn("ECA", event_agencies)  # ECA 는 이벤트 카드 목록엔 없다(리소스로 이동)


