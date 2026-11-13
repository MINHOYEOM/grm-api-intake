"""backfill_483_obs_split — 앞 관찰 상세가 삼킨 483 관찰을 저장 raw 에서 나눈다(2026-11-13).

근거: _ops/reports/GRM_483관찰분할결함_dry_2026-11-13.md. 상세 조각은 실측 문서(105113·151431·
159977·85637·108507·147184·172204)의 표지 꼴만 남긴 합성 최소 조각이다(저장 상세는 줄바꿈이 접혀 있다).
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

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


def _loss(old, new):
    old_raw = b._loss_raw({"fda_483_observations": old})
    new_raw = b._loss_raw({"fda_483_observations": new})
    return b.text_loss(old_raw, new_raw)["lost_chars"]


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


class FragmentHolderExtensionTest(unittest.TestCase):
    def test_88106_truncated_holder_is_extended_without_new_row(self):
        host = _obs(
            "4", "Written process validation protocols for the remediation of ATNAA units were not followed.",
            "The referenced memo does not address particulate matter being found in the grooves of the rear "
            "plunger but rather dried material found on a unit. OBSERVATION 6. Employees performing the ATNAA "
            "remediation process were not trained according to written procedures. Specifically, SOP 29173 states "
            "employees are to complete the training items in their job specific curricula prior to performing "
            "job-related tasks. b. Contract employ [redacted] had not completed the OJT training for this task, "
            "Management stated that employees were frequently rotated through different tasks throughout their "
            "shifts [redacted] was enrolled in the ATNAA Operator Remediation curricula which requires the upper "
            "chamber low fill inspection OJT training. c. Employee [redacted] did not complete this training item "
            "until 7/31/13.",
        )
        holder = _obs(
            "6", ".", "Employees performing the ATNAA remediation process were not trained according to written "
            "procedures. Specifically, SOP 29173 states employees are to complete the training items in their job "
            "specific curricula prior to performing job-related tasks. b. Contract employ [redacted] had not "
            "completed the OJT training for this task, Management stated that employees were frequen",
        )
        old = [host, holder, _obs("7", "The ATNAA batch records are deficient in that they do not include "
                                  "identification of the equipment used.", "The equipment field was blank.")]

        new, plans = b.split_swallowed(old)

        self.assertEqual([o["number"] for o in new], ["4", "6", "7"])
        self.assertTrue(new[0]["detail"].endswith("found on a unit."))
        self.assertIn("employees were frequently rotated through different tasks", new[1]["detail"])
        self.assertTrue(new[1]["detail"].endswith("until 7/31/13."))
        self.assertEqual((len(plans[0].extended), len(plans[0].added), len(plans[0].deduped)), (1, 0, 0))
        self.assertEqual(plans[0].extended[0]["holder_index"], 1)
        self.assertGreater(plans[0].extended[0]["appended_chars"], 0)
        self.assertEqual(_loss(old, new), 0)

    def test_94989_midword_holder_is_extended(self):
        old = [
            _obs("5", "Specifically, A.",
                 "B. Thermometers used for monitoring temperatures of freezers and incubators were not calibrated. "
                 "OBSERVATION 6 A. A. The clean room including ISO 5 LAFH was certified by an outside contractor "
                 "[redacted] on a [redacted] basis. There was no record showing who performed the results reading "
                 "and no record indicating that the results were reviewed. B. No written procedure has been "
                 "established to provide a schedule for HEPA filter changes in the ISO classified rooms."),
            _obs("6", "A.",
                 "A. The clean room including ISO 5 LAFH was certified by an outside contractor [redacted] on a "
                 "[redacted] basis. There was no record showing who performed the results reading and no record "
                 "indicating that the results were reviewed. B. No written procedure h"),
            _obs("7", "Written records of cleaning operations are not maintained.", "The cleaning log was blank."),
        ]

        new, plans = b.split_swallowed(old)

        self.assertEqual([o["number"] for o in new], ["5", "6", "7"])
        self.assertTrue(new[1]["detail"].endswith(
            "B. No written procedure has been established to provide a schedule for HEPA filter changes in the "
            "ISO classified rooms."))
        self.assertEqual(len(plans[0].extended), 1)
        self.assertEqual(_loss(old, new), 0)

    def test_91878_noisy_holder_keeps_tail_and_appends_missing_body(self):
        old = [
            _obs("5", "Written production and process control procedures are not followed in the execution of "
                      "production and process control functions.",
                 "On Saturday March 28, 2015 the Cleaning Log for the Cleanroom was incomplete in the section "
                 "identified was Initials of person Verifying. OBSERVATION 6 A. The labels of your outsourcing "
                 "facility's drug products do not include information required by section 503B(a)(10). Specifically, "
                 "your firm's labels affixed to the drug products do not contain the date on which the drug was "
                 "compounded. J OATEISSUE.O B. The containers from which the individual units of the drug are "
                 "removed for dispensing or for administration do not include the following information to "
                 "facilitate adverse event reporting: www.fda.gov/medwatch and 1-800-FDA-1088. For example "
                 "Vancomycin 150mg/ml and Hydroxyprogesterone caporate 250mg/ml."),
            _obs("6", "A.",
                 "The labels of your outsourcing facility's drug products do not include information required by "
                 "section 503B(a)(10). Specifically, your firm's labels affixed to the drug products do not contain "
                 "the date on which the drug was compounded. J OATEISSUE.O EMPlOYEI;($) SIGNATIJRE [investigator]"),
        ]

        new, plans = b.split_swallowed(old)

        self.assertIn("SIGNATIJRE [investigator] B. The containers from which", new[1]["detail"])
        self.assertIn("Vancomycin 150mg/ml", " ".join(o["detail"] for o in new))
        self.assertEqual(len(plans[0].extended), 1)
        self.assertEqual(_loss(old, new), 0)

    def test_90268_truncated_fragment_head_holder_is_extended(self):
        old = [
            _obs("3", "Procedures designed to prevent microbiological contamination of drug products purporting "
                      "to be sterile are not established.",
                 "The microbial sampling of personnel gowning is only assessed through personnel gowning "
                 "qualification and not routinely during active processing of sterile products. I OBSERVATION 4 I "
                 "' Laboratory controls do not include the establishment of scientifically sound and appropriate "
                 "standards and test procedures. Specifically, 1 There is no training procedure for employees to "
                 "perform 100% visual inspection checks of sterile drug products. Operators are only trained on the "
                 "established procedure which does describe the defect categories that may appear in the finished "
                 "product.) 2. There has been no established procedures or methods employed to evaluate the "
                 "effectiveness of preservatives used in finished sterile products, such as hydroxyprogesterone "
                 "caproate injection."),
            _obs("4", "!", "I ' Laboratory controls do not include the establishment of scientifically sound and "
                 "appropriate standards and test procedures. Specifically, 1 There is no training procedure for "
                 "employees to perform 100% visual inspection checks of sterile drug products. Operators are only "
                 "trained on the established procedure which does describe the defect categories that may appear "
                 "in the finished product.)"),
        ]

        new, plans = b.split_swallowed(old)

        self.assertIn("2. There has been no established procedures or methods employed to evaluate the "
                      "effectiveness of preservatives", new[1]["detail"])
        self.assertEqual(len(plans[0].extended), 1)
        self.assertEqual(_loss(old, new), 0)

    def test_82333_fragment_chain_extends_second_holder_only(self):
        holder3 = _obs(
            "3", ".", "All required records do not have the signature or initials of the person performing the "
            "operation or creating the record. Specifically, the document that you identified as your Fly Control "
            "log fails to include the signature or initials of the person performing the operation.",
        )
        holder4 = _obs(
            "4", ".", "e You lack adequate review of records created to document your SE Prevention measures. "
            "Specifically, 1. Your Salmonella Prevention Program lacks adequate review as evidenced by the "
            "following: A. Page 24 states Threshold per barn is [redacted] fly counts or higher. This statement as "
            "written does not indicate a threshold. 1 A",
        )
        old = [
            _obs("2", "All required records do not include your name and the location of your farm.",
                 "Specifically, the document that you identified as your Fly Control log fails to identify your "
                 "firm by name and location. OBSERVATION 3. All required records do not have the signature or "
                 "initials of the person performing the operation or creating the record. Specifically, the document "
                 "that you identified as your Fly Control log fails to include the signature or initials of the "
                 "person performing the operation. DISTRICT e You lack adequate review of records created to "
                 "document your SE Prevention measures. Specifically, 1. Your Salmonella Prevention Program lacks "
                 "adequate review as evidenced by the following: A. Page 24 states Threshold per barn is [redacted] "
                 "fly counts or higher. This statement as written does not indicate a threshold. 1 A W 2. Your "
                 "documents titled Fly Counts and the document that you identified as your Fly Control log have not "
                 "been reviewed. Additionally your document titled Fly Counts as prepared do not include space for "
                 "review."),
            holder3,
            holder4,
        ]

        new, plans = b.split_swallowed(old)

        self.assertEqual([o["number"] for o in new], ["2", "3", "4"])
        self.assertEqual(new[1], holder3)
        self.assertTrue(new[2]["detail"].endswith(
            "threshold. 1 A W 2. Your documents titled Fly Counts and the document that you identified as your Fly "
            "Control log have not been reviewed. Additionally your document titled Fly Counts as prepared do not "
            "include space for review."))
        self.assertNotIn("DISTRICT", " ".join(o["detail"] for o in new))
        self.assertEqual(plans[0].extended[0]["number"], "4")
        self.assertEqual(_loss(old, new), 0)

    def test_151431_spaced_marker_adds_observation_four_and_extends_three(self):
        old = [
            _obs("2", "(REPEAT OBSERVATION) There are no written procedures for production and process controls "
                      "designed to assure that the drug products have the identity and strength they purport to have.",
                 "Specifically, approximately [redacted] more commercial batches were released for commercial "
                 "distribution after the process validation batches. OBSERVATION 3. (REPEAT OBSERVATION) Procedures "
                 "designed to prevent microbiological contamination of drug products purporting to be sterile are "
                 "not established. Specifically, there is no assurance that the media fill studies performed "
                 "accurately represent production conditions. In addition., the firm does not reject open units "
                 "inside the filling line that could be potentially exposed to microbial contamination during "
                 "routine interventions, non-routine interventions, or equipment stoppage.... OBSERVATI ON 4 Your "
                 "outsourcing facility has not submitted an adverse event report to FDA in accordance with the "
                 "content and format requirements established through guidance or regulation under 21 CFR 310.305. "
                 "Specifically, The following Adverse Event Reports were not reported to FDA in 15 calendar days "
                 "after first receiving information about the adverse event. ADE Number Product Lot Number ADE "
                 "Received ADE Reported to FDA 2020029 Prednisolone Suspension 7/15/2019 7/9/2020 reported after "
                 "retrospective investigation following the last FDA inspection."),
            _obs("3", ".", "(REPEAT OBSERVATION) Procedures designed to prevent microbiological contamination of "
                 "drug products purporting to be sterile are not established. Specifically, there is no assurance "
                 "that the media fill studies performed accurately represent production conditions."),
            _obs("5", "(REPEAT OBSERVATION) The quality control unit lacks the responsibility and authority to "
                      "approve or reject all drug products.",
                 "Specifically, unreleased drug products were not dispositioned."),
        ]

        new, plans = b.split_swallowed(old)

        self.assertEqual([o["number"] for o in new], ["2", "3", "4", "5"])
        self.assertIn("In addition., the firm does not reject open units", new[1]["detail"])
        self.assertTrue(new[2]["deficiency"].startswith(
            "Your outsourcing facility has not submitted an adverse event report"))
        self.assertIn("reported after retrospective investigation", new[2]["detail"])
        self.assertEqual(([a["number"] for a in plans[0].added], len(plans[0].extended)), (["4"], 1))
        self.assertEqual(_loss(old, new), 0)

    def test_longer_holder_with_signature_tail_remains_byte_identical(self):
        heading = "Laboratory controls do not include scientifically sound test procedures."
        detail = "Specifically, the written method did not define the required acceptance criteria."
        holder = _obs("6", ".", f"{heading} {detail} EMPI.OVEE{{S) SIGN'.TURE DATEISSUEO")
        old = [_obs("5", H6, f"{PRE6} OBSERVATION 6. {heading} {detail}"), holder]

        new, plans = b.split_swallowed(old)

        self.assertEqual(new[1], holder)
        self.assertEqual((len(plans[0].deduped), len(plans[0].extended)), (1, 0))
        self.assertEqual(_loss(old, new), 0)

    def test_83807_covered_repeated_prefix_remains_byte_identical(self):
        heading = "The quality control unit does not review all required production records."
        detail = ("Specifically, the quality unit did not review the batch production record before release. "
                  "The quality control unit does not review all required production records.")
        holder = _obs("4", ".", "The quality control unit does not review all required production records. "
                      "Specifically, the quality unit did not review the batch production record before release.")
        old = [_obs("3", H6, f"{PRE6} OBSERVATION 4. {heading} {detail}"), holder]

        new, plans = b.split_swallowed(old)

        self.assertEqual(new[1], holder)
        self.assertEqual((len(plans[0].deduped), len(plans[0].extended)), (1, 0))
        self.assertEqual(_loss(old, new), 0)


class SpacedObservationMarkerTest(unittest.TestCase):
    def test_spaced_marker_variants_are_split(self):
        plain = "Before OBSERVATION 4 after"
        collector_match = b._collector()._OBS_RE.search(plain)
        tool_match = b._OBS_MARK_RE.search(plain)
        self.assertEqual((tool_match.span(), tool_match.group(1)),
                         (collector_match.span(), collector_match.group(1)))
        for marker in ("OBSERVATI ON 4", "OBSERV ATION 4", "O BSERVATION 4"):
            with self.subTest(marker=marker):
                old = [_obs("3", H6, f"{PRE6} {marker}. {H7} {REST7}")]
                new, plans = b.split_swallowed(old)
                self.assertEqual([o["number"] for o in new], ["3", "4"])
                self.assertEqual(len(plans), 1)
                self.assertEqual(_loss(old, new), 0)

    def test_spaced_marker_cross_reference_is_not_split(self):
        old = [_obs("3", H6, "Please refer to OBSERVATI ON 4 for additional Quality Unit concerns. "
                                  "The review was not documented.")]
        new, plans = b.split_swallowed(old)
        self.assertEqual((new, plans), (old, []))


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
        self.assertEqual(rep.docs[0]["text_loss"], 0)

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


class RunTextLossGuardTest(unittest.TestCase):
    def _row(self, observations, document_id="fda483-guard"):
        return {
            "raw_signal_id": "rawsig-guard", "document_id": document_id, "raw_sha256": "s",
            "raw_json": json.dumps({"firm": "X", "fda_483_observations": observations},
                                   ensure_ascii=False),
        }

    def test_holder_cut_loss_is_blocked_in_apply_and_dry_run(self):
        kept = "The retained introductory sentence remains available."
        lost = ("Missing substantive passage alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
                "mu nu xi omicron.")
        old = [_obs("1", H6, f"{kept} OBSERVATION 2. {lost}"),
               _obs("2", ".", "Missing substantive passage alpha beta gamma")]
        new = [_obs("1", H6, kept), old[1]]
        plan = b.SplitPlan(0, "1", len(kept), len(old[0]["detail"]) - len(kept),
                           deduped=[{"number": "2", "deficiency": lost}])
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                calls = []
                with mock.patch.object(b, "split_swallowed", return_value=(new, [plan])):
                    rep = b.run([self._row(old)], dry_run=dry_run,
                                patch_fn=lambda row, raw: calls.append(raw))
                self.assertEqual(calls, [])
                self.assertEqual((rep.docs_blocked, rep.failed, rep.patched), (1, 0, 0))
                self.assertEqual((rep.docs_changed, rep.hosts_cut, rep.observations_deduped), (0, 0, 0))
                self.assertEqual(rep.blocked_text_loss[0]["reason"], "holder_cut")
                self.assertGreater(rep.blocked_text_loss[0]["lost_chars"], 0)
                self.assertTrue(rep.docs[0]["blocked_text_loss"])

    def test_200_or_more_lost_chars_are_blocked_without_holder(self):
        lost = " ".join(f"substantiveword{chr(97 + i % 26)}{i}" for i in range(60))
        kept = "The retained introductory sentence remains available."
        old = [_obs("1", H6, f"{kept} OBSERVATION 2. {lost}")]
        new = [_obs("1", H6, kept)]
        plan = b.SplitPlan(0, "1", len(kept), len(old[0]["detail"]) - len(kept),
                           added=[_obs("2", "A newly parsed observation heading is available.", "Short detail.")])
        calls = []
        with mock.patch.object(b, "split_swallowed", return_value=(new, [plan])):
            rep = b.run([self._row(old, "fda483-threshold")], dry_run=False,
                        patch_fn=lambda row, raw: calls.append(raw))

        self.assertEqual(calls, [])
        self.assertEqual((rep.docs_blocked, rep.docs_changed, rep.patched, rep.failed), (1, 0, 0, 0))
        self.assertEqual(rep.blocked_text_loss[0]["reason"], "threshold")
        self.assertGreaterEqual(rep.blocked_text_loss[0]["lost_chars"], b.NAMED_TEXT_LOSS_MIN_CHARS)

    def test_105113_normal_split_is_not_blocked_and_reports_zero_loss(self):
        calls = []
        rep = b.run([self._row(_doc_105113(), "fda483-105113")], dry_run=False,
                    patch_fn=lambda row, raw: calls.append(raw))

        self.assertEqual((rep.docs_blocked, rep.docs_changed, rep.patched), (0, 1, 1))
        self.assertEqual(rep.blocked_text_loss, [])
        self.assertEqual(rep.docs[0]["text_loss"], 0)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
