"""backfill_483_obs_split — 앞 관찰 상세가 삼킨 483 관찰을 저장 raw 에서 나눈다(2026-11-13).

근거: _ops/reports/GRM_483관찰분할결함_dry_2026-11-13.md. 상세 조각은 실측 문서(105113·151431·
159977·85637·108507·147184·172204)의 표지 꼴만 남긴 합성 최소 조각이다(저장 상세는 줄바꿈이 접혀 있다).
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_483_obs_split as b  # noqa: E402

H6 = "Complete records documenting the suitability of compendial test methods are not maintained."
H7 = "Out-of-specification results are not thoroughly investigated."
H8 = "Water used in the manufacture of APIs has not been demonstrated to be suitable for its intended use."
H9 = "Adequate, clean washing and toilet facilities are not provided."
PRE6 = ("Specifically, compendial method verification studies are not available for residual solvents "
        "tested on the drug substance by an off-site, contract testing laboratory.")
REST7 = "Specifically, OOS investigation QC-17-004 did not extend to other batches."
REST8 = "Specifically, the purified water system was not sampled at the point of use."


def _obs(number, deficiency, detail=""):
    return {"number": number, "deficiency": deficiency, "detail": detail}


def _doc_105113():
    host = f"{PRE6} OBSERVATION 7. {H7} {REST7} FACILITIES AND EQUIPMENT SYSTEM OBSERVATION 8.: {H8} {REST8}"
    return [_obs("5", "Test procedures are not fully documented at the time of performance."),
            _obs("6", H6, host),
            _obs("9", H9, "Specifically, soap was not provided at the hand washing sinks.")]


class SplitSwallowedTest(unittest.TestCase):
    def test_two_swallowed_observations_become_rows_in_order(self):
        old = _doc_105113()
        new, plans = b.split_swallowed(old)
        self.assertEqual([o["number"] for o in new], ["5", "6", "7", "8", "9"])
        self.assertEqual(new[1]["detail"], PRE6)                      # 앞 관찰은 표지 앞까지
        self.assertEqual(new[2]["deficiency"], H7)
        self.assertTrue(new[2]["detail"].startswith(REST7))
        self.assertEqual(new[3]["deficiency"], H8)
        self.assertEqual(new[3]["detail"], REST8)
        self.assertEqual(len(plans), 1)
        self.assertEqual([a["number"] for a in plans[0].added], ["7", "8"])
        self.assertEqual(plans[0].cut_at, len(PRE6) + 1)
        self.assertEqual(new[1]["deficiency"], H6)                    # 표제 불변
        self.assertEqual(old[1]["detail"].startswith(PRE6 + " OBSERVATION 7."), True)   # 입력 불변

    def test_no_text_lost(self):
        old = _doc_105113()
        new, _ = b.split_swallowed(old)
        moved = " ".join(f"{o['deficiency']} {o['detail']}" for o in new[2:4])
        for part in (H7, REST7, H8, REST8):
            self.assertIn(part, moved)

    def test_repeat_observation_dedupes_against_existing_fragment_row(self):
        h3 = ("Procedures designed to prevent microbiological contamination of drug products purporting "
              "to be sterile are not established and followed.")
        rest3 = "Specifically, smoke studies were not performed under dynamic conditions."
        host = ("Specifically, PV batches were released. In addition, more commercial batches were released "
                f"after the process validation batches. OBSERVATION 3. (REPEAT OBSERVATION) {h3} {rest3}")
        old = [_obs("2", "There is no written testing program designed to assess the stability characteristics "
                         "of drug products.", host),
               _obs("3", ".", f"(REPEAT OBSERVATION) {h3} {rest3}")]
        new, plans = b.split_swallowed(old)
        self.assertEqual([o["number"] for o in new], ["2", "3"])     # 새 관찰을 더하지 않는다
        self.assertTrue(new[0]["detail"].endswith("after the process validation batches."))
        self.assertEqual(new[1], old[1])
        self.assertEqual(plans[0].added, [])
        self.assertEqual([d["number"] for d in plans[0].deduped], ["3"])

    def test_glyph_fragment_holder_dedupes(self):
        h2 = "Non-microbial contamination was observed in your production area."
        host = ("Specifically, a large dead insect was found in your ISO-8 Anteroom where sterile gowning is "
                f"conducted. 1 i OBSERVATION 2 {h2} Specifically, A. From 9/27 the shelf was dusty.")
        old = [_obs("1", "Vermin was observed in your production area.", host),
               _obs("2", "!", f"{h2} Specifically, A. From 9/27 the shelf was dusty.")]
        new, plans = b.split_swallowed(old)
        self.assertEqual(len(new), 2)
        self.assertTrue(new[0]["detail"].endswith("conducted. 1 i"))
        self.assertEqual([d["number"] for d in plans[0].deduped], ["2"])

    def test_same_swallowed_observation_in_two_hosts_is_added_once(self):
        tail = f" OBSERVATION 7. {H7} {REST7}"
        old = [_obs("5", "Test procedures are not fully documented at the time of performance.",
                    "Specifically, the injection sequence was not recorded." + tail),
               _obs("6", H6, PRE6 + tail)]
        new, plans = b.split_swallowed(old)
        self.assertEqual([o["number"] for o in new], ["5", "7", "6"])
        self.assertEqual([len(p.added) for p in plans], [1, 0])
        self.assertEqual([len(p.deduped) for p in plans], [0, 1])


class NoSplitTest(unittest.TestCase):
    def _unchanged(self, old):
        new, plans = b.split_swallowed(old)
        self.assertEqual(plans, [])
        self.assertEqual(new, old)

    def test_cross_reference_is_not_split(self):
        host = ("Specifically, pellets on EQ001 are within expiry. Please refer to OBSERVATION 4 for "
                "additional Quality Unit concerns. The review was not documented.")
        self._unchanged([_obs("3", "The responsibilities and procedures applicable to the quality "
                                   "control unit are not fully followed.", host)])

    def test_lowercase_continuation_reference_is_not_split(self):
        host = ("Specifically, the door was not interrupted during movement. This is a repeat of "
                "Observation 9 from FDA 2019 Inspection (8/06/2019 -08/29/2019).")
        self._unchanged([_obs("2", "Aseptic processing areas are deficient regarding the system for "
                                   "cleaning and disinfecting the room.", host)])

    def test_same_line_word_before_marker_is_left_for_source_reextraction(self):
        host = ("Specifically, Ketorolac 30mg/mL Injectable was not tested for sterility or endotoxins "
                "OBSERVATION 7. Testing and release of drug product for distribution do not include "
                "appropriate laboratory determination.")
        self._unchanged([_obs("5", "There is no written testing program designed to assess the "
                                   "stability characteristics of drug products.", host)])

    def test_lower_or_equal_number_is_not_split(self):
        host = (f"{PRE6} OBSERVATION 2. Laboratory records do not include complete data derived from all "
                "tests necessary to assure compliance.")
        self._unchanged([_obs("6", H6, host)])

    def test_titled_number_elsewhere_is_not_split(self):
        host = f"{PRE6} OBSERVATION 7. {H7} {REST7}"
        self._unchanged([_obs("6", H6, host), _obs("7", H7, REST7)])

    def test_ocr_question_mark_heading_is_left(self):
        host = ("Specifically, the passing results reported were from the gel clot tests. OBSERVATION 15 "
                "The? microbiological identification system is used to identify isolates without verification.")
        self._unchanged([_obs("14", "Laboratory controls do not include the establishment of scientifically "
                                    "sound test procedures.", host)])

    def test_any_gated_marker_keeps_whole_detail(self):
        # 표지 하나라도 표제 게이트에 걸리면 그 구간 글자가 갈 곳이 없다 — 통째로 둔다(덜 나눈다).
        host = f"{PRE6} OBSERVATION 7. {H7} {REST7} OBSERVATION 8. Qzxv Wbnm Rtpk Lkjh Mnbv"
        self._unchanged([_obs("6", H6, host)])

    def test_no_marker(self):
        self._unchanged([_obs("1", H6, PRE6), _obs("2", H7, REST7)])


class RunTest(unittest.TestCase):
    def _row(self, obs):
        return {"raw_signal_id": "rawsig-x", "document_id": "fda483-105113", "raw_sha256": "s",
                "raw_json": json.dumps({"firm": "X", "fda_483_observations": obs}, ensure_ascii=False)}

    def test_dry_run_reports_and_does_not_patch(self):
        calls = []
        rep = b.run([self._row(_doc_105113())], dry_run=True, patch_fn=lambda r, raw: calls.append(raw))
        self.assertEqual(calls, [])
        self.assertEqual((rep.docs_changed, rep.hosts_cut, rep.observations_added), (1, 1, 2))
        self.assertEqual(rep.docs[0]["observations_before"], 3)
        self.assertEqual(rep.docs[0]["observations_after"], 5)

    def test_apply_patches_only_observations(self):
        calls = []
        rep = b.run([self._row(_doc_105113())], dry_run=False, patch_fn=lambda r, raw: calls.append(raw))
        self.assertEqual(rep.patched, 1)
        self.assertEqual(calls[0]["firm"], "X")
        self.assertEqual([o["number"] for o in calls[0]["fda_483_observations"]], ["5", "6", "7", "8", "9"])

    def test_patch_goes_through_apply_version_function(self):
        from unittest import mock
        calls = []

        def post(url, headers=None, json=None, timeout=None):  # noqa: A002 — requests 와 같은 이름
            calls.append((url, json))
            return mock.MagicMock(status_code=200, json=lambda: {"status": "applied"})
        row = {"raw_signal_id": "rawsig-1", "raw_sha256": "a" * 64, "row_json": '{"k":1}'}
        with mock.patch("raw_versioning.requests.post", post):
            b._patch("https://x.supabase.co", "k", row, {"fda_483_observations": []})
        self.assertTrue(calls[0][0].endswith("/rpc/raw_signal_apply_version"))
        self.assertEqual(calls[0][1]["p_change_kind"], "reextract")
        self.assertEqual(calls[0][1]["p_record"]["row_json"], '{"k":1}')

    def test_unchanged_doc_is_not_patched(self):
        calls = []
        rep = b.run([self._row([_obs("1", H6, PRE6)])], dry_run=False, patch_fn=lambda r, raw: calls.append(raw))
        self.assertEqual((rep.docs_changed, rep.patched, calls), (0, 0, []))


if __name__ == "__main__":
    unittest.main()
