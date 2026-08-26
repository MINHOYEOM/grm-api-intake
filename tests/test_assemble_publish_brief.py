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

    def test_ghost_error_names_key_namespace_regression(self):
        """[2026-07-25] 델타 키가 `Source::document_id` 로 회귀하면 오류가 그렇게 말해야 한다.

        2026-07-20 실장애: 라우틴이 81건 전량을 handoff card_id 형식으로 예치해 거부됐는데,
        오류 문구가 "스캐폴드가 다른 intake run?" 뿐이라 진단이 스캐폴드 쪽으로 샜다.
        판정 로직은 그대로 두고(여전히 하드 거부) 원인만 지목한다.
        """
        s = _blank_scaffold_from(self.truth)
        d = copy.deepcopy(self.delta)
        d["cards"] = {f"FDA::{cid}": slots for cid, slots in d["cards"].items()}
        _, report = apb.assemble_publish_brief(s, d, strict=False)
        joined = "\n".join(report.errors)
        self.assertIn("키 네임스페이스 회귀", joined)
        self.assertIn("bare document_id", joined)
        # 여전히 발행을 막는다 — 진단이 좋아졌다고 통과시키지 않는다.
        with self.assertRaises(apb.AssembleError):
            apb.assemble_publish_brief(s, d, strict=True)

    def test_ghost_error_keeps_generic_message_for_real_run_mismatch(self):
        """접두사 문제가 아닌 진짜 run 불일치는 기존 문구를 유지한다(오지목 방지)."""
        s = _blank_scaffold_from(self.truth)
        s["cards"] = s["cards"][:-1]
        _, report = apb.assemble_publish_brief(s, self.delta, strict=False)
        joined = "\n".join(report.errors)
        self.assertIn("다른 intake run", joined)
        self.assertNotIn("키 네임스페이스 회귀", joined)

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


class Refresh483ObservationsTest(unittest.TestCase):
    """조립 시점 483 관찰 재추출(_refresh_483_observations) — 낡은 스캐폴드 자가 교정.

    2026-07-20: 스캐폴드는 수집 시점 파서로 굳는데, 그 뒤 파서를 고쳐도 그 주 스캐폴드는
    낡은 채 남고 재수집으로도 못 고친다(Notion New 행 소진). 사람이 아티팩트를 손으로 고쳐
    로컬 조립하는 우회가 곧 사고였으므로, 조립이 원문에서 다시 뽑도록 했다.
    """
    SRC = ("I/WE OBSERVED:\n\n"
           "OBSERVATION 1: The quality unit did not follow its written procedures. "
           "Specifically, the firm failed to document the deviation. Please refer to\n\n"
           "OBSERVATION 3: .\nb. Conduct adequate root cause analysis for the "
           "recurring environmental monitoring excursions observed.\n\n"
           "OBSERVATION 2: Procedures to prevent microbiological contamination are "
           "not followed. Specifically, monitoring data document repeated excursions.\n\n")

    def _card(self, observations):
        return {"id": "fda483-1", "deterministic_detail": {
            "type": "fda_483_observations", "count": len(observations),
            "observations": observations}}

    def _report(self):
        return apb.AssembleReport()

    def test_stale_scaffold_numbers_are_corrected(self):
        # 낡은 파서가 낸 중복 번호(상호참조 오분할)가 원문 재추출로 교정된다.
        card = self._card([{"number": "1", "deficiency": "old", "detail": ""},
                           {"number": "3", "deficiency": ".", "detail": "b. Conduct"},
                           {"number": "2", "deficiency": "old2", "detail": ""}])
        out = {"cards": [card]}
        rep = self._report()
        apb._refresh_483_observations(out, {"fda483-1": {"source_text": self.SRC}}, rep)
        nums = [o["number"] for o in card["deterministic_detail"]["observations"]]
        self.assertEqual(nums, ["1", "2"])
        self.assertEqual(card["deterministic_detail"]["count"], 2)
        self.assertTrue(any("[483]" in w and "재추출" in w for w in rep.warnings),
                        "조용한 교체 금지 — report 에 남아야 한다")

    def test_no_source_text_leaves_scaffold_untouched(self):
        obs = [{"number": "7", "deficiency": "keep me", "detail": ""}]
        card = self._card(obs)
        rep = self._report()
        apb._refresh_483_observations({"cards": [card]}, {"fda483-1": {"source_text": ""}}, rep)
        self.assertEqual(card["deterministic_detail"]["observations"], obs)
        self.assertEqual(rep.warnings, [])

    def test_unparseable_source_does_not_wipe_observations(self):
        # 재추출이 빈 결과면 기존 관찰을 보존한다(데이터를 지우지 않는 방향).
        obs = [{"number": "1", "deficiency": "keep me", "detail": ""}]
        card = self._card(obs)
        rep = self._report()
        apb._refresh_483_observations({"cards": [card]},
                                      {"fda483-1": {"source_text": "no anchors here"}}, rep)
        self.assertEqual(card["deterministic_detail"]["observations"], obs)

    def test_non_483_card_is_ignored(self):
        card = {"id": "x-1", "deterministic_detail": {"type": "gmp_table", "rows": []}}
        rep = self._report()
        apb._refresh_483_observations({"cards": [card]},
                                      {"x-1": {"source_text": self.SRC}}, rep)
        self.assertEqual(card["deterministic_detail"], {"type": "gmp_table", "rows": []})

    def test_runs_before_translation_merge(self):
        """번호 교정이 observations_ko 병합보다 **먼저** 일어나야 번역이 제 관찰에 붙는다.

        병합은 number 를 키로 쓴다(inject_slots._merge_observation_translations). 낡은 번호
        `3,2` 상태로 병합하면 '2번' 번역이 **원문의 관찰 2 가 아닌 다른 관찰**에 붙는다.
        """
        stale = [{"number": "3", "deficiency": ".", "detail": "b. Conduct"},
                 {"number": "2", "deficiency": "old2", "detail": ""}]
        obs_ko = [{"number": "2", "deficiency_ko": "2번 국문", "detail_ko": "2번 상세"}]

        # 교정 후 병합(정상 순서) — 원문 관찰 2 에 정확히 붙는다.
        card = self._card(copy.deepcopy(stale))
        apb._refresh_483_observations({"cards": [card]},
                                      {"fda483-1": {"source_text": self.SRC}}, self._report())
        inject_slots._merge_observation_translations(
            card, obs_ko, inject_slots.InjectionReport(), "fda483-1")
        by_num = {o["number"]: o for o in card["deterministic_detail"]["observations"]}
        self.assertEqual(by_num["2"]["deficiency_ko"], "2번 국문")
        self.assertIn("Procedures to prevent", by_num["2"]["deficiency"])
        self.assertNotIn("deficiency_ko", by_num["1"])

        # 교정 없이 병합(역순서) — 같은 번역이 엉뚱한 본문에 붙는다(이 순서를 금지하는 근거).
        bad = self._card(copy.deepcopy(stale))
        inject_slots._merge_observation_translations(
            bad, obs_ko, inject_slots.InjectionReport(), "fda483-1")
        bad_by_num = {o["number"]: o for o in bad["deterministic_detail"]["observations"]}
        self.assertEqual(bad_by_num["2"]["deficiency"], "old2")     # 원문과 무관한 조각
        self.assertEqual(bad_by_num["2"]["deficiency_ko"], "2번 국문")


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

    def test_digest_never_blames_the_source_for_the_absence(self):
        """[부재 어휘 2026-07-27] 디제스트는 **소스가 안 줬다**고 말하지 않는다.

        실제로 두 번 틀렸다 — 2026-07-13 "스캔·비공개로", 2026-07-20 정정본 "원문이 제공되지
        않아". 두 문구 다 FDA 가 안 줬다는 단정인데, 그 카드들의 원문 PDF 에는 관찰이 스캔
        이미지로 멀쩡히 들어 있었다(2026-07-26 실측 21건). 우리가 못 읽은 것을 소스 탓으로
        돌리는 이 어휘가 반복 재발했으므로 문구를 테스트로 고정한다.
        """
        cards = [self._card("fda483-1", "Alpha", "01/01/2024"),
                 self._card("fda483-2", "Beta", "02/02/2024")]
        rep = next(c for c in apb.merge_fda483_disclosures(cards) if c["id"] == "fda483-1")
        prose = " ".join([rep["summary"], rep["implication"], *rep["key_facts"]])
        for banned in ("제공되지 않", "비공개", "미공개", "미수록", "공개되지 않"):
            self.assertNotIn(banned, prose,
                             f"디제스트가 소스의 부재를 단정한다: {banned!r}")
        # 우리 쪽 결손 + 원문은 공개돼 있다는 사실이 둘 다 남아야 한다.
        self.assertIn("확보하지 못", rep["summary"])
        self.assertIn("공개돼 있", rep["summary"])


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
        # source_excerpt_present=True(§3 정직성 게이트 통과) → summary 포함.
        cards = [
            self._card("fda-1", "FDA", card_type="Warning Letter"),
            self._card("eca-1", "ECA", type_tag="GMP News", card_type="규제 소식",
                       source_excerpt_present=True),
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

    def test_summary_omitted_without_source_excerpt_present(self):
        # [전문지 브리핑 v2 §3 정직성 게이트] source_excerpt_present 가 없으면(=수집 RSS가
        # 제목만 준 얇은 입력) summary 키 자체가 note 에서 제거된다 — LLM 의 "원문에 규정
        # 변경이 없다" 식 오서술을 렌더에 실어보내지 않기 위함(§3 배경).
        cards = [self._card("eca-4", "ECA", type_tag="GMP News", card_type="규제 소식")]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual(events, [])
        self.assertEqual(len(resources), 1)
        self.assertNotIn("summary", resources[0])

    def test_summary_omitted_when_source_excerpt_present_false(self):
        # source_excerpt_present=False(명시적 False, 흡수 시도했으나 실패) 도 동일하게 제거.
        cards = [self._card("eca-5", "ECA", type_tag="GMP News", card_type="규제 소식",
                            source_excerpt_present=False)]
        events, resources = apb.extract_resource_notes(cards)
        self.assertNotIn("summary", resources[0])

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

    def test_ispe_news_separated_with_summary_present(self):
        # [전문지 브리핑 소스확장 2026-07-13] ISPE 는 ECA 와 동일 조건(agency 게이트만 확장 —
        # RESOURCE_AGENCIES=("ECA","ISPE"))으로 resource 분리된다. source_excerpt_present=True
        # → summary 포함(§3 정직성 게이트 통과).
        cards = [
            self._card("fda-1", "FDA", card_type="Warning Letter"),
            self._card("ispe-1", "ISPE", type_tag="GMP News", card_type="규제 소식",
                       source_excerpt_present=True),
        ]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual([c["id"] for c in events], ["fda-1"])
        self.assertEqual(len(resources), 1)
        r = resources[0]
        self.assertEqual(r["id"], "ispe-1")
        self.assertEqual(r["agency"], "ISPE")
        self.assertEqual(r["type_tag"], "GMP News")
        self.assertEqual(r["summary"], "ispe-1-summary")

    def test_ispe_summary_omitted_when_source_excerpt_present_false(self):
        cards = [self._card("ispe-2", "ISPE", type_tag="GMP News", card_type="규제 소식",
                            source_excerpt_present=False)]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual(events, [])
        self.assertNotIn("summary", resources[0])

    def test_ispe_summary_omitted_without_source_excerpt_present_key(self):
        # 키 자체가 없는 경우(수집 RSS 가 제목만 준 얇은 입력)도 동일하게 summary 제거.
        cards = [self._card("ispe-3", "ISPE", type_tag="GMP News", card_type="규제 소식")]
        events, resources = apb.extract_resource_notes(cards)
        self.assertEqual(events, [])
        self.assertEqual(len(resources), 1)
        self.assertNotIn("summary", resources[0])


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




class FalseAbsenceOurSideVocabularyTest(unittest.TestCase):
    """[2026-07-27] 어휘를 정직하게 고친 뒤에도 **그 서술이 참인지**는 별개 문제다.

    2026-07-20 에 "원문에 없다"(소스 탓) → "우리가 확보하지 못했다"(우리 상태)로 어휘를
    고쳤다. 그런데 스캔 483 을 OCR 로 복구하자 그 서술이 거짓이 됐고, 게이트가 소스 탓
    표현만 잡고 있어 **카드 25장이 그대로 통과했다** — 관찰 상세가 바로 아래 붙어 있는데
    "관찰사항 본문을 확보하지 못했다"고 적힌 채 발행됐다(07-27 24건·07-20 1건 실측).
    """

    @staticmethod
    def _card(**kw):
        c = {"id": "fda483-1",
             "deterministic_detail": {"type": "fda_483_observations", "count": 3,
                                      "observations": []}}
        c.update(kw)
        return c

    def test_our_side_absence_is_blocked_when_source_captured(self):
        for text in ("상세: 원문 PDF 에 텍스트층이 없어(스캔본) 관찰사항 본문을 확보하지 못했다",
                     "상세: 본문 수집을 시도하지 않아 관찰사항을 확인하지 못했다",
                     "구체적 관찰 사유: 미확인"):
            with self.subTest(text=text):
                errs = apb.lint_false_absence_claims([self._card(key_facts=[text])])
                self.assertTrue(errs, f"우리 쪽 부재 서술이 차단되지 않았다: {text!r}")

    def test_source_blaming_absence_still_blocked(self):
        errs = apb.lint_false_absence_claims(
            [self._card(summary="세부 위반내용은 원문에 명시되지 않았다")])
        self.assertTrue(errs)

    def test_real_content_passes(self):
        errs = apb.lint_false_absence_claims([self._card(
            key_facts=["관찰 3건 — 이상사례 불만 조사 미흡·FAR 미제출",
                       "시설 유형: Sterile Drug Manufacturer"],
            summary="FDA 가 무균제조소를 실사하고 관찰사항 3건을 발부했다.")])
        self.assertEqual(errs, [])

    def test_digest_card_without_source_is_exempt(self):
        """원문을 못 얻은 디제스트는 '확보 실패'라고 **말해야 한다** — 막으면 안 된다."""
        digest = {"id": "fda483-2", "merged_count": 7,
                  "key_facts": ["공개 건수: 7건 (관찰 본문 자동 확보 실패 — 원문 PDF 는 공개돼 있음)"],
                  "summary": "원문 PDF 는 공개돼 있으나 개별 관찰 본문을 자동으로 확보하지 못해 "
                             "시설·실사일 목록만 정리했다."}
        self.assertEqual(apb.lint_false_absence_claims([digest]), [])

    def test_unrelated_absence_statement_not_flagged(self):
        """원문이 실제로 그렇게 적혀 있다는 **사실 서술**은 통과해야 한다."""
        errs = apb.lint_false_absence_claims(
            [self._card(key_facts=["관찰 1: 부적격 사유 미기재"])])
        self.assertEqual(errs, [])


class FalseAbsenceGateBlindSpotTest(unittest.TestCase):
    """[2026-08-25] 게이트 3 이 라이브 결함을 못 잡은 이유 두 가지 — **둘 다** 있어야 발화한다.

    07-27 WHOPIR 11장이 결론(+항목별 요약)을 카드에 싣고서도 "실사 결과 세부 내용은
    확보하지 못해 원문 확인이 필요하다"고 적힌 채 발행됐다. 이 게이트는 정확히 그런 문장을
    막으려고 만든 것인데 두 곳에서 각각 빠져나갔다:
      ① `_card_has_source_body` 가 `count` 만 봐서 whopir/NCR 상세를 **검사조차 안 했다**
      ② `_FALSE_ABSENCE_RE` 어미 목록에 `못해` 가 없어 문구가 **매치되지 않았다**
    하나만 고치면 여전히 통과한다 — 그래서 둘을 각각 고정한다.
    """

    _WHOPIR = {"type": "whopir_report", "report_kind": "findings",
               "outcome": "Based on the areas inspected…",
               "sections": [{"no": "1", "title": "Quality", "text": "…"}]}
    _NCR = {"type": "eu_gmp_ncr_statement",
            "nature": "Critical deficiency in sterility assurance.",
            "action": "Suspension of the GMP certificate."}

    def test_count_less_detail_still_counts_as_source_body(self):
        """count 를 쓰는 상세는 표 형태 셋뿐 — 본문을 싣고도 count 가 없는 유형이 있다."""
        for name, dd in (("whopir_report", self._WHOPIR),
                         ("eu_gmp_ncr_statement", self._NCR)):
            with self.subTest(detail=name):
                self.assertTrue(
                    apb._card_has_source_body({"id": "x", "deterministic_detail": dd}),
                    "%s 는 본문을 실었는데 미확보로 판정됐다" % name)

    def test_facts_table_detail_is_not_source_body(self):
        """음성 검사 — **사실표**는 서사 본문이 아니다(과잉 차단 방지).

        첫 수리는 "메타 키 말고 값이 있으면 본문"으로 넓혔다가 회수 상세(#796)까지 본문으로
        판정했다. 회수 카드는 로트·수량·처리경과를 싣고도 "당국이 세부 일탈 사유를 공개하지
        않았다"가 여전히 참이라, 그 서술을 막으면 **참인 문장을 못 쓰게 된다**. CI 가 이
        과잉을 잡았다(실측 3장).
        """
        recall = {"type": "openfda_recall_detail", "status": "Ongoing",
                  "code_info": "Lot 1234", "quantity": "1,200 bottles"}
        self.assertFalse(apb._card_has_source_body(
            {"id": "D-1", "deterministic_detail": recall}))
        self.assertFalse(apb._card_has_source_body({"id": "x"}))

    def test_unknown_detail_type_fails_open(self):
        """미분류 유형은 발행을 막지 않는다 — 새 상세가 생겼다고 갑자기 차단되면 안 된다.

        표류는 런타임이 아니라 CI 완전성 검사
        (`test_published_briefs_integrity.DetailTypeClassificationIsComplete`)가 알린다.
        """
        self.assertFalse(apb._card_has_source_body(
            {"id": "x", "deterministic_detail": {"type": "brand_new_detail_v9",
                                                 "body": "…"}}))

    def test_live_defect_wording_is_caught(self):
        """라이브 문구 그대로 — 어미 `못해` 하나가 게이트를 통째로 빠져나갔다."""
        card = {"id": "who-whopir-1", "deterministic_detail": self._WHOPIR,
                "key_facts": ["대상 제조소: X",
                              "상세: 실사 결과 세부 내용은 확보하지 못해 원문 확인이 필요하다"]}
        errs = apb.lint_false_absence_claims([card])
        self.assertTrue(errs, "발행 11장을 낸 그 문구가 여전히 통과한다")
        self.assertIn("key_facts[1]", errs[0])

    def test_absence_conjugations_are_covered(self):
        """활용형은 추측이 아니라 발행 코퍼스 474장 실측 분포로 넓혔다."""
        for tail in ("못했다", "못한 채", "못해 원문 확인이 필요하다", "못하고 있다"):
            with self.subTest(tail=tail):
                card = {"id": "c", "deterministic_detail": self._WHOPIR,
                        "key_facts": ["상세: 세부 내용은 확보하지 " + tail]}
                self.assertTrue(apb.lint_false_absence_claims([card]))

    def test_substantive_finding_using_the_same_word_is_not_flagged(self):
        """음성 검사 — `미확보` 가 **업체의 지적사항**을 뜻할 때는 건드리면 안 된다.

        실측 오탐: "수탁 미생물시험소 적격성 미확보"(FDA 483 카드의 진짜 지적 요약)를
        소급 대상으로 올릴 뻔했다. 앵커어 없이 `미확보` 만 잡으면 멀쩡한 카드가 걸린다.
        """
        card = {"id": "fda483-193886",
                "deterministic_detail": {"type": "fda_483_observations", "count": 5,
                                         "observations": [{"number": "1"}]},
                "title_issue": "수탁 미생물시험소 적격성 미확보",
                "summary": "FDA 가 원료의약품 제조소 실사에서 미생물시험 수탁기관의 적격성 "
                           "미확보와 품질부서 감독 미흡을 지적했다."}
        self.assertEqual(apb.lint_false_absence_claims([card]), [])
