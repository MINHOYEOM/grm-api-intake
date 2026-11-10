#!/usr/bin/env python3
"""EU NCR ncr_additional 소급 계획 생성기 -- 순수 함수 + 읽는 쪽(build_restore_queue) 호환 테스트."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import backfill_eu_ncr_additional_plan as plan_mod
import findings_extractors as fe
import findings_translate as translate
import grm_findings as gf

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")


def _fixture(additional: str = "") -> dict:
    with open(os.path.join(GOLDEN, "eu_gmp_ncr.input.json"), encoding="utf-8") as f:
        fx = json.load(f)
    raw = dict(fx["raw"], ncr_additional=additional)
    return gf.raw_signal_from_row(fx["row"], raw)


def _old_row(rs: dict, fid: str = "finding-old") -> dict:
    """추출기가 additional 을 덧붙이기 전 본문을 가진 기존 finding 행."""
    old = fe.findings_from_raw_signal(_fixture(""))[0]["finding_text"]
    return {"finding_id": fid, "raw_signal_id": rs["raw_signal_id"],
            "published_date": "2026-07-13", "finding_text": old}


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.rs = _fixture("Activity suspended onsite as a precaution.")
        self.old = _old_row(self.rs)

    def test_maps_to_existing_finding_id_with_old_md5(self):
        plan, rep = plan_mod.build_plan([self.rs], [self.old])
        self.assertEqual(plan["schema_version"], "grm-wl-truncation-restore-plan/v1")
        self.assertEqual((rep.with_additional, rep.mapped), (1, 1))
        it = plan["items"][0]
        self.assertEqual(it["finding_id"], "finding-old")
        self.assertEqual(it["finding_text_old_md5"], plan_mod.md5_text(self.old["finding_text"]))
        self.assertTrue(it["finding_text"].startswith(self.old["finding_text"]))
        self.assertTrue(it["finding_text"].endswith(
            "\n\nAdditional information: Activity suspended onsite as a precaution."))

    def test_rerun_after_apply_is_noop(self):
        new = fe.findings_from_raw_signal(self.rs)[0]["finding_text"]
        done = dict(self.old, finding_text=new)
        plan, rep = plan_mod.build_plan([self.rs], [done])
        self.assertEqual((rep.mapped, rep.already_applied), (0, 1))
        self.assertEqual(plan["items"], [])

    def test_empty_additional_is_skipped(self):
        rs = _fixture("")
        plan, rep = plan_mod.build_plan([rs], [_old_row(rs)])
        self.assertEqual((rep.with_additional, rep.mapped), (0, 0))

    def test_no_prefix_match_is_unmatched_and_two_hits_ambiguous(self):
        other = dict(self.old, finding_text="Completely different text")
        _p, rep = plan_mod.build_plan([self.rs], [other])
        self.assertEqual((rep.unmatched, rep.mapped), (1, 0))
        dup = dict(self.old, finding_id="finding-old-2")
        _p, rep = plan_mod.build_plan([self.rs], [self.old, dup])
        self.assertEqual((rep.ambiguous, rep.mapped), (1, 0))

    def test_no_existing_finding_is_counted(self):
        _p, rep = plan_mod.build_plan([self.rs], [])
        self.assertEqual((rep.no_finding, rep.mapped), (1, 0))

    def test_plan_is_readable_by_build_restore_queue(self):
        plan, _ = plan_mod.build_plan([self.rs], [self.old])
        live = {"finding-old": {"finding_id": "finding-old",
                                "finding_text": self.old["finding_text"], "finding_text_ko": ""}}
        with mock.patch.object(translate, "_fetch_live_rows_for_ids_supabase",
                               return_value=(live, "")):
            queue = translate.build_restore_queue_supabase(plan, "https://x.supabase.co", "anon")
        self.assertEqual(queue["restore_pending"], 1)
        item = queue["items"][0]
        self.assertEqual(item["kind"], "restore")
        self.assertEqual(item["finding_text_old_md5"], plan["items"][0]["finding_text_old_md5"])
        # 옛 md5 대조를 --apply 검증 경로가 그대로 통과시킨다(WL 전용 제약 없음)
        item = dict(item, finding_text_ko="후속 설명을 포함한 번역", translation_method="claude")
        errs = translate._validate_item(item, live)
        self.assertEqual([e for e in errs if "translation_method" not in e], [])


if __name__ == "__main__":
    unittest.main()
