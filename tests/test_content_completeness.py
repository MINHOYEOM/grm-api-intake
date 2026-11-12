#!/usr/bin/env python3
"""내용 완전성 감시(content_completeness.py) 표적 시험 — 네트워크 없음.

① 잘림 몰림 판정(상한·정형 반복·자연 분포)  ② 끝 글자 판정  ③ 원자료 펴기(중첩 경로)
④ 미사용 판정(데이터 도달률이 코드 참조를 이긴다 — 483 상세 사례)  ⑤ 의도 표  ⑥ 이슈 계획  ⑦ 워크플로 계약
기준값은 2026-11-10 실측으로 맞췄다(content_completeness.py 머리 주석).
"""
from __future__ import annotations

import json
import random
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import content_completeness as cc  # noqa: E402

TODAY = date(2026, 11, 10)


def words(rng: random.Random, n: int) -> str:
    vocab = ["the", "firm", "failed", "to", "validate", "batch", "records", "cleaning", "procedure", "sterile",
             "water", "system", "deviation", "investigation", "laboratory", "controls", "were", "not", "adequate"]
    out = ""
    while len(out) < n:
        out += rng.choice(vocab) + " "
    return out


def capped(rng: random.Random, cap: int, ell: str = "") -> str:
    """수집기 식 자르기: 상한에서 자르고 단어 경계로 물린다."""
    t = words(rng, cap + 200)[:cap]
    t = t.rsplit(" ", 1)[0]
    return t + ell


def natural(rng: random.Random) -> str:
    return words(rng, rng.randint(250, 2500)).strip() + "."


class CutTest(unittest.TestCase):
    def test_endings(self):
        self.assertFalse(cc.is_cut("The firm failed to validate."))
        self.assertFalse(cc.is_cut("세척 밸리데이션을 하지 않았다"))
        self.assertFalse(cc.is_cut("(see 21 CFR 211.68)"))
        self.assertTrue(cc.is_cut("the implementation is sufficient for software release after"))
        self.assertTrue(cc.is_cut("Your drug products contain…"))
        self.assertTrue(cc.is_cut("and so on..."))


class FindCapsTest(unittest.TestCase):
    def _fs(self, values: list[str]) -> cc.FieldStats:
        fs = cc.FieldStats("S", "p")
        for i, v in enumerate(values):
            fs.add(v, ref=f"r{i}", collected="2026-11-01")
        return fs

    def test_detects_hard_cap_like_483_detail(self):
        rng = random.Random(1)
        vals = [capped(rng, 1200) for _ in range(300)] + [natural(rng) for _ in range(300)]
        found = cc.detect_truncation([self._fs(vals)], [{"name": "X_MAX_CHARS", "value": 1200, "where": "x.py:1"}], TODAY)
        self.assertEqual(len(found), 1)
        self.assertGreaterEqual(found[0]["cap"], 1190)
        self.assertLessEqual(found[0]["cap"], 1200)
        self.assertEqual(found[0]["code_caps"], ["X_MAX_CHARS=1200 (x.py:1)"])
        self.assertEqual(found[0]["recent_rows"], 300)

    def test_detects_ellipsis_cap_like_wl_480(self):
        rng = random.Random(2)
        vals = [capped(rng, 479, "…") for _ in range(200)] + [natural(rng) for _ in range(500)]
        found = cc.detect_truncation([self._fs(vals)], [], TODAY)
        self.assertEqual(len(found), 1)
        self.assertTrue(466 <= found[0]["cap"] <= 480, found[0]["cap"])

    def test_two_caps_in_one_field(self):
        rng = random.Random(3)
        vals = [capped(rng, 20000) for _ in range(40)] + [capped(rng, 30000) for _ in range(30)] \
            + [natural(rng) for _ in range(200)]
        caps = sorted(f["cap"] for f in cc.detect_truncation([self._fs(vals)], [], TODAY))
        self.assertEqual(len(caps), 2)
        self.assertTrue(19400 <= caps[0] <= 20000 and 29100 <= caps[1] <= 30000, caps)

    def test_repeated_boilerplate_is_not_a_cap(self):
        """같은 문장이 수백 번 반복(HC·483 정형 표제)돼도 서로 다른 값만 세므로 몰림이 아니다."""
        rng = random.Random(4)
        same = capped(rng, 300)
        vals = [same] * 500 + [natural(rng) for _ in range(300)]
        self.assertEqual(cc.detect_truncation([self._fs(vals)], [], TODAY), [])

    def test_page_footer_tail_is_not_a_cut(self):
        """WL 전문처럼 서로 다른 본문이 같은 꼬리말로 끝나면(부호 없음) 잘림으로 세지 않는다."""
        rng = random.Random(10)
        footer = " Contact Number 1-888-INFO-FDA (1-888-463-6332) Back to Top"
        vals = [words(rng, 19500 + rng.randint(0, 500)).strip() + footer for _ in range(80)] \
            + [natural(rng) for _ in range(100)]
        # 길이를 한 대역에 몰아도(19.5k~20k) 꼬리가 같으면 몰림이 아니다
        self.assertEqual(cc.detect_truncation([self._fs(vals)], [], TODAY), [])

    def test_natural_lengths_no_alarm(self):
        rng = random.Random(5)
        vals = [words(rng, rng.randint(200, 3000)).strip() for _ in range(2000)]   # 끝 부호 없음 + 고른 길이
        self.assertEqual(cc.detect_truncation([self._fs(vals)], [], TODAY), [])

    def test_small_pile_below_min_hits(self):
        rng = random.Random(6)
        vals = [capped(rng, 800) for _ in range(cc.MIN_HITS - 1)] + [natural(rng) for _ in range(100)]
        self.assertEqual(cc.detect_truncation([self._fs(vals)], [], TODAY), [])

    def test_intentional_cap_is_marked_not_alarmed(self):
        rng = random.Random(7)
        fs = cc.FieldStats("FDA 483", "fda483_excerpt")
        for i in range(100):
            fs.add(capped(rng, 1500), ref=str(i), collected="2025-01-01")
        found = cc.detect_truncation([fs], [], TODAY)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["intentional"])
        self.assertEqual(found[0]["recent_rows"], 0)

    def test_source_ends_document_is_not_counted_as_cut(self):
        """[11-11] 원문 자체가 거기서 끝나는 관찰(문서·번호 예외)은 잘림 몰림 집계에서 빠진다 — 같은 길이
        같은 문서라도 예외 번호가 아닌 관찰은 그대로 센다."""
        doc, num = next(iter(cc.INTENTIONAL_SOURCE_ENDS["FDA 483"].items()))
        rng = random.Random(11)
        listed, other = capped(rng, 1200), capped(rng, 1200)
        raw = {"fda_483_observations": [{"number": num[0], "deficiency": "d", "detail": listed},
                                        {"number": "99", "deficiency": "d", "detail": other}]}
        self.assertEqual(cc.source_end_values("FDA 483", doc, raw), {listed.strip()})
        self.assertEqual(cc.source_end_values("FDA 483", "fda483-0", raw), set())
        rows = [{"raw_signal_id": "r1", "document_id": doc, "source": "FDA 483",
                 "collected_at": "2026-11-01", "raw_json": json.dumps(raw)}]
        stats, _ = cc.collect_stats(rows, [], {})
        det = {(s.source, s.path): s for s in stats}[("FDA 483", "fda_483_observations[].detail")]
        self.assertEqual(sum(len(v) for v in det.long_cut.values()), 1)   # 예외 아닌 관찰 하나만
        self.assertEqual(det.total, 2)                                     # 미사용 검사에는 그대로 센다

    def test_source_ends_list_has_25_documented_cases(self):
        self.assertEqual(sum(len(v) for v in cc.INTENTIONAL_SOURCE_ENDS["FDA 483"].values()), 25)

    def test_partial_detail_notice(self):
        """[11-12] 알려진 잔여 잘림 22건 — 문장 중간에서 끝날 때만 표시, 온전해지면 저절로 빠진다."""
        self.assertEqual(sum(len(v) for v in cc.KNOWN_PARTIAL_DETAILS["FDA 483"].values()), 22)
        cut = "Specifically, the operator was observed touching the"
        self.assertTrue(cc.partial_detail_notice("FDA 483", "fda483-163788", "4", cut))
        self.assertTrue(cc.partial_detail_notice("FDA 483", "fda483-163788", 4, cut))          # 번호 타입 무관
        self.assertFalse(cc.partial_detail_notice("FDA 483", "fda483-163788", "4", cut + " gloves."))  # 온전
        self.assertFalse(cc.partial_detail_notice("FDA 483", "fda483-163788", "1", cut))       # 목록 밖 번호
        self.assertFalse(cc.partial_detail_notice("FDA 483", "fda483-99999", "4", cut))
        self.assertFalse(cc.partial_detail_notice("FDA 483", "fda483-163788", "4", ""))
        # 원문 끝 예외와 겹치지 않는다(한 관찰은 한 범주)
        for doc, nums in cc.KNOWN_PARTIAL_DETAILS["FDA 483"].items():
            ends = cc.INTENTIONAL_SOURCE_ENDS["FDA 483"].get(doc, ())
            self.assertFalse(set(nums) & set(ends), doc)


class LeavesTest(unittest.TestCase):
    def test_nested_paths(self):
        raw = {"a": "x", "obs": [{"detail": "d1", "number": "1"}, {"detail": "d2"}], "lst": ["s"],
               "rep": {"outcome": "o"}, "n": 3}
        got = sorted(cc.iter_leaves(raw))
        self.assertEqual(got, [("a", "x"), ("lst[]", "s"), ("obs[].detail", "d1"), ("obs[].detail", "d2"),
                               ("obs[].number", "1"), ("rep.outcome", "o")])
        self.assertEqual(cc.path_keys("obs[].detail"), ["obs", "detail"])
        self.assertEqual(cc.path_keys("rep.outcome"), ["rep", "outcome"])

    def test_collect_stats_reach_483_detail(self):
        """483 상세 사례 재현: 표제는 findings 에 들어가고 상세는 안 들어간다 → 상세는 도달률 0."""
        rng = random.Random(8)
        raws, finds = [], []
        for i in range(30):
            deficiency = "Procedures for corrective and preventive action have not been adequately established " + str(i)
            detail = natural(rng)
            raws.append({"raw_signal_id": f"r{i}", "source": "FDA 483", "collected_at": "2026-11-01T00:00:00Z",
                         "raw_json": json.dumps({"fda_483_observations": [{"deficiency": deficiency, "detail": detail}]})})
            finds.append({"finding_id": f"f{i}", "raw_signal_id": f"r{i}", "source": "FDA 483",
                          "finding_text": deficiency, "finding_text_ko": "시정 예방 조치 절차 미흡"})
        stats, ftext = cc.collect_stats(raws, finds, {})
        by = {(s.source, s.path): s for s in stats}
        det = by[("FDA 483", "fda_483_observations[].detail")]
        self.assertEqual(cc.compute_reach(det.reach_pairs, ftext), (0, 30))
        dfc = by[("FDA 483", "fda_483_observations[].deficiency")]
        self.assertEqual(cc.compute_reach(dfc.reach_pairs, ftext), (30, 30))
        self.assertIn(("FDA 483", "findings.finding_text"), by)


    def test_collect_stats_reach_483_detail_text_column(self):
        """#1463 오경보 회귀: 483 상세는 findings.detail_text(·_ko)에 담긴다 — 도달률이 이 칸도 세야 한다.
        옛 감시기는 finding_text(·_ko)만 세어 상세 도달률이 0%로 나왔다."""
        rng = random.Random(9)
        raws, finds = [], []
        for i in range(30):
            deficiency = "Procedures for corrective and preventive action have not been adequately established " + str(i)
            detail = natural(rng)
            raws.append({"raw_signal_id": f"r{i}", "source": "FDA 483", "collected_at": "2026-11-01T00:00:00Z",
                         "raw_json": json.dumps({"fda_483_observations": [{"deficiency": deficiency, "detail": detail}]})})
            finds.append({"finding_id": f"f{i}", "raw_signal_id": f"r{i}", "source": "FDA 483",
                          "finding_text": deficiency, "finding_text_ko": "시정 예방 조치 절차 미흡",
                          "detail_text": detail, "detail_text_ko": "상세 번역"})
        stats, ftext = cc.collect_stats(raws, finds, {})
        by = {(s.source, s.path): s for s in stats}
        det = by[("FDA 483", "fda_483_observations[].detail")]
        self.assertEqual(cc.compute_reach(det.reach_pairs, ftext), (30, 30))
        self.assertIn(("FDA 483", "findings.detail_text"), by)


class UsageTest(unittest.TestCase):
    def test_data_beats_code_for_findings_sources(self):
        """코드가 칸 이름을 읽어도(사이트 파일) 화면 표본에 안 닿으면 '사이트'가 아니다."""
        u = cc.classify_usage(source="FDA 483", path="fda_483_observations[].detail", prose_lens=[621] * 100,
                              url_like=0, reach=(1, 100), site_readers=["web/render.py"], brief_readers=["card_scaffold.py"])
        self.assertEqual(u["status"], "brief_only")
        self.assertEqual(u["basis"], "data")
        self.assertTrue(u["alarm"])

    def test_reached_field_ok(self):
        u = cc.classify_usage(source="S", path="p", prose_lens=[300] * 50, url_like=0, reach=(40, 50),
                              site_readers=[], brief_readers=[])
        self.assertEqual(u["status"], "site")
        self.assertFalse(u["alarm"])

    def test_code_basis_unused_alarms_brief_only_does_not(self):
        unused = cc.classify_usage(source="S", path="p", prose_lens=[300] * 20, url_like=0, reach=None,
                                   site_readers=[], brief_readers=[])
        self.assertEqual(unused["status"], "unused")
        self.assertTrue(unused["alarm"])
        brief = cc.classify_usage(source="S", path="p", prose_lens=[300] * 20, url_like=0, reach=None,
                                  site_readers=[], brief_readers=["card_scaffold.py"])
        self.assertEqual(brief["status"], "brief_only")
        self.assertFalse(brief["alarm"])

    def test_short_or_url_fields_skipped(self):
        self.assertIsNone(cc.classify_usage(source="S", path="p", prose_lens=[100] * 50, url_like=0, reach=None,
                                            site_readers=[], brief_readers=[]))
        self.assertIsNone(cc.classify_usage(source="S", path="link", prose_lens=[160] * 50, url_like=50, reach=None,
                                            site_readers=[], brief_readers=[]))
        self.assertIsNone(cc.classify_usage(source="S", path="p", prose_lens=[500] * 5, url_like=0, reach=None,
                                            site_readers=[], brief_readers=[]))

    def test_intentional_unshown(self):
        u = cc.classify_usage(source="FDA 483", path="fda483_body_full", prose_lens=[7000] * 100, url_like=0,
                              reach=(0, 100), site_readers=[], brief_readers=["card_scaffold.py"])
        self.assertFalse(u["alarm"])
        self.assertTrue(u["intentional"])

    def test_code_readers_nested_needs_both_keys(self):
        files = [("a.py", 'x["obs"]["detail"]'), ("b.py", 'y["detail"]')]
        self.assertEqual(cc.code_readers("obs[].detail", files), ["a.py"])

    def test_repo_code_caps_include_known_constants(self):
        names = {c["name"]: c["value"] for c in cc.scan_code_caps()}
        # 값은 다른 세션이 바꿀 수 있으니 이름만 잡는다(483 상세 상한은 11-10 제거)
        self.assertIn("WL_BODY_MAX_CHARS", names)  # WL_BODY_FULL_MAX_CHARS 는 2026-11-10 제거
        self.assertTrue(all(v >= cc.MIN_LEN for v in names.values()))


class ReportTest(unittest.TestCase):
    def _report(self, alarm: bool) -> dict:
        rng = random.Random(9)
        fs = cc.FieldStats("FDA Warning Letter", "findings.finding_text")
        n = 50 if alarm else 3
        for i in range(n):
            fs.add(capped(rng, 479, "…"), ref=f"f{i}", collected="2026-11-02")
        return cc.build_report([fs], {}, today=TODAY, run_url="https://x/run/1")

    def test_open_plan_shape_matches_source_reconcile(self):
        rep = self._report(True)
        self.assertEqual(rep["counts"]["truncated"], 1)
        (plan,) = rep["issues"]
        self.assertEqual(plan["action"], "open")
        self.assertEqual(plan["title"], cc.ISSUE_TITLE)
        self.assertEqual(plan["labels"], ["content-completeness"])
        for k in ("body", "fingerprint", "comment", "source"):
            self.assertIn(k, plan)
        self.assertIn(plan["fingerprint"], plan["body"])
        self.assertIn("잘림 의심", plan["body"])
        self.assertEqual(cc.fingerprint(rep), cc.fingerprint(self._report(True)), "같은 결과는 같은 지문(멱등)")

    def test_close_plan_when_clean(self):
        (plan,) = self._report(False)["issues"]
        self.assertEqual(plan["action"], "close")

    def test_markdown_renders(self):
        md = cc.render_markdown(self._report(True))
        self.assertIn("내용 완전성 감시", md)
        self.assertIn("findings.finding_text", md)


class WorkflowContractTest(unittest.TestCase):
    def test_workflow_runs_and_issues_content_check(self):
        wf = (ROOT / ".github" / "workflows" / "grm-source-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn("python content_completeness.py", wf)
        self.assertIn("content-completeness.json", wf)
        self.assertIn("ENABLE_CONTENT_COMPLETENESS", wf)

    def test_label_has_owner_team(self):
        sys.path.insert(0, str(ROOT / "web"))
        import ops_alarm_sync as al  # noqa: E402
        self.assertEqual(al.team_for(cc.ISSUE_LABELS), "quality")


if __name__ == "__main__":
    unittest.main()
