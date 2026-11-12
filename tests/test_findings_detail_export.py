"""findings_detail_export — 표제 짝 비교 키(2026-11-12 표제 앞 OCR 잡음) · 조각 표제 detail 첫머리 짝(2026-11-13)."""
from __future__ import annotations

import os
import sys
import unittest
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import findings_detail_export as ex  # noqa: E402


class HeadingKeyTest(unittest.TestCase):
    def test_leading_ocr_noise_is_ignored(self):
        clean = "The responsibilities and procedures applicable to the quality control unit are not in writing."
        for noisy in ("r " + clean, "| " + clean, "| | | " + clean, "· " + clean, "\x01 " + clean,
                      "j " + clean, "ʖ " + clean, "  " + clean):
            self.assertEqual(ex._heading_key(noisy), ex._heading_key(clean), repr(noisy[:6]))

    def test_other_differences_still_fail(self):
        a = "The responsibilities applicable to the quality unit are not fully followed."
        self.assertNotEqual(ex._heading_key(a), ex._heading_key(a.replace("fully ", "")))     # 문구 차이
        self.assertNotEqual(ex._heading_key(a), ex._heading_key("STREET ADDRESS " + a))     # 머리글 접두
        self.assertNotEqual(ex._heading_key(a), ex._heading_key(a + " Specifically,"))      # 꼬리 차이
        self.assertNotEqual(ex._heading_key(a), ex._heading_key("ab " + a))                  # 두 글자 조각은 안 뗀다
        self.assertNotEqual(ex._heading_key(a), ex._heading_key("x the responsibilities applicable"))
        # 여는 괄호·따옴표로 시작하는 표제는 그대로 둔다
        self.assertTrue(ex._heading_key('"Quality unit" procedures').startswith('"Quality'))
        self.assertTrue(ex._heading_key("(b)(4) records were not reviewed").startswith("(b)(4)"))

    def test_queue_links_noisy_raw_heading(self):
        findings = [{"finding_id": "f1", "raw_signal_id": "r1", "published_date": "2024-01-17",
                     "finding_text": "Equipment used in the manufacture is not of appropriate design.",
                     "detail_text": None}]
        raws = [{"raw_signal_id": "r1", "raw_json": {"fda_483_observations": [
            {"number": "7", "deficiency": "| Equipment used in the manufacture is not of appropriate design.",
             "detail": "Specifically, the hopper was cracked."}]}}]
        queue, stats = ex.build_queue(findings, raws)
        self.assertEqual(stats["queued"], 1)
        self.assertEqual(queue[0]["detail_text"], "Specifically, the hopper was cracked.")
        raws[0]["raw_json"]["fda_483_observations"][0]["deficiency"] = "STREET ADDRESS Equipment used in the manufacture"
        _, stats = ex.build_queue(findings, raws)
        self.assertEqual(stats["queued"], 0)


# 2026-11-13 소급 15행 — 관찰 분할이 깨진 문서. raw 관찰 표제는 조각("."·"A."), 진짜 표제는 detail 첫머리.
# (raw_signal_id, finding_id, 관찰 표제, detail 앞 잡음, finding_text, 표제 뒤 detail 일부) — DB SELECT 로 확보(최소 조각).
_R130570, _R144803, _R151431 = "rawsig-9f0ae52dcc954d507ef07ef2", "rawsig-5fa8182a92bbd6d2f2b71d9c", "rawsig-86a6cd36b96135125d923aa8"
_R163754, _R70711, _R91684 = "rawsig-3bd93605f319b7bddcce3b61", "rawsig-587c0b64524ac5b15c076864", "rawsig-7cec5c0d7df3e01627308995"
RETRO_15 = [
    (_R130570, "finding-929e559ed448b7ef6d53d9a1", ".", "",
     "Laboratory controls do not include the establishment of scientifically sound and appropriate specifications, "
     "standards, sampling plans, and test procedures designed to assure that drug product containers, in-process "
     "materials, and drug products confonn to appropriate standards of identity, strength, quality and purity.", ""),
    (_R130570, "finding-b0ed683c160fb25005ee181d", ".", "",
     "Non-microbial contamination was observed in your production area.",
     " Specifically, during a walkthrough of your in-use production areas"),
    (_R144803, "finding-8e88ce9261c1c08b0a1e26a5", ".", ". ",
     "Procedures for receiving, reviewing, and evaluating complaints by a formally designated unit have not been "
     "adequately established.", " ' -."),
    (_R151431, "finding-3289ce92508f53334399735d", ".", "... ",
     "Records are not maintained so that data therein can be reviewed [redacted] to evaluate tbe quality standards of "
     "each drug product to determine tine need for changes in specifications or manufacturing or control procedures.",
     " Specifically. The firm has not generated [redacted] product reviews"),
    (_R151431, "finding-d2b712dd5660f4c4bfab3e2d", ".", ". ",
     "Your outsourcing facility compounds drug product that have been withdrawn or removed from the market because "
     "such drugs or components of such drugs have been found to be unsafe or not effective.",
     " Specifically, Your firm compounded the· following drug products"),
    (_R151431, "finding-df76698b4016403541fe1c14", ".", "(REPEAT OBSERVATION) ",
     "Procedures designed to prevent microbiological contamination of drug products purporting to 'be sterile are not "
     "established: Specifically, A) The media fill performed does not represent the number and complexity of routine "
     "and non routine interventions that occur during production of commercial sterile injectable products.",
     " B) A procedure is not established to determine the number of open vials"),
    (_R163754, "finding-40e4d18d14e750bae8c260d2", ".", "",
     "Both batches were not manufactured under Month/Stability Condition Initial Release % % 1 M Accelerated % Not "
     "detected 2 M Accelerated % Not detected 3 M Accelerated % % 3M Long Term % % 6 M Accelerated % (OOS U09-001587) "
     "% 6 M Long Term % (OOS U09-001588) % 9 M Long Term Discontinued Stability % 12 M Long Term % Approximately "
     "batches (Crude) Stage Intermediate batches were manufactured using reprocessed input material and shipped to "
     "Unit 1 for purification step of APL C.", " OOS-U09-001568 was initiated on 12 July 2021"),
    (_R163754, "finding-51a93156663602f86e30c0ff", ".", "",
     "CAPA states that all batches were verified and found within specification however the statement is incorrect "
     "since 6 month, 9 month and 12 month stability sample failed to meet the specification as detailed above.",
     " Preventive Action -states that SOP IQA040 will be revised"),
    (_R70711, "finding-25bb0bd957f0a9b67b11e1df", ".", "",
     "An NDA-field alert report was not submitted within three working days of receipt of information concerning a "
     "failure of one or more distributed batches of a drug to meet the specifications established for it in the "
     "application.", " Field Alerts were not routinely filed"),
    (_R70711, "finding-87b934ad825a259584ee7542", ".", "",
     "The responsibilities and procedures to the quality control unit are not in writing and fully followed.",
     " A. The Laboratory Records SOP#QC-022-04 effective 6/25/04"),
    (_R70711, "finding-9e9fb8378b711e8daf7d31cd", ".", "A. ",
     "Annual Reports for ANDA’s that were submitted to FDA did not include out of specification (OOS) result.",
     " Only passing data points were submitted."),
    (_R91684, "finding-65f362673d355000deb873e6", "A.", "",
     "The active viable environmental monitoring (EM) program at your facility is inadequate...",
     " B. The non-viable particulate (NVP) EM program"),
    (_R91684, "finding-8f95a90dfe3ddfc84b954794", "A.", "",
     "Not all lots of sterile product aseptically processed are tested for sterility.",
     " B. Your in-house sterility test....is not scientifically valid."),
    (_R91684, "finding-95eb7bcc0c89485218c90c9c", "A.", "",
     "Adequate aseptic process simulations (media fills) have not been performed under representative worst case "
     "aseptic processing conditions to assure the sterility of drug products.",
     " B. Aseptic practices and techniques observed at your facility"),
    (_R91684, "finding-96f9120ec9cc1d89583a91c7", "A.", "",
     "The gowning ofpersonnel performing aseptic operation is inadequate...",
     " B. Gowning worn in the ISO 7 buffer room"),
]
HEADING_ONLY = {"finding-929e559ed448b7ef6d53d9a1", "finding-8e88ce9261c1c08b0a1e26a5"}   # 표제 뒤에 글자 없음


def _retro_fixture():
    findings, obs = [], defaultdict(list)
    for rid, fid, deficiency, lead, text, after in RETRO_15:
        findings.append({"finding_id": fid, "raw_signal_id": rid, "published_date": "2024-01-17",
                         "finding_text": text, "detail_text": None})
        obs[rid].append({"number": "1", "deficiency": deficiency, "detail": lead + text + after})
    raws = [{"raw_signal_id": rid, "raw_json": {"fda_483_observations": o}} for rid, o in obs.items()]
    return findings, raws


class DetailHeadPairTest(unittest.TestCase):
    """조각 표제 관찰 — detail 이 finding 표제 전체로 시작하면 짝(2026-11-13, 가산 규칙)."""

    def test_retro_15_rows_pair_and_drop_only_heading(self):
        findings, raws = _retro_fixture()
        queue, stats = ex.build_queue(findings, raws)
        got = {x["finding_id"]: x["detail_text"] for x in queue}
        self.assertEqual(stats["queued"], 13)
        self.assertEqual(stats["detail_head"], 13)
        self.assertEqual(stats["heading_only"], 2)
        self.assertEqual(stats["ambiguous"], 0)
        for rid, fid, _d, _l, text, after in RETRO_15:
            if fid in HEADING_ONLY:
                self.assertNotIn(fid, got)
                continue
            self.assertEqual(got[fid], after.lstrip(), fid)          # 표제만 떼고 나머지는 원문 그대로
            self.assertFalse(got[fid].startswith(text[:20]), fid)
        md5 = {x["finding_id"]: x["finding_text_md5"] for x in queue}
        self.assertEqual(md5["finding-b0ed683c160fb25005ee181d"],
                         ex._md5("Non-microbial contamination was observed in your production area."))

    def test_remainder_keeps_original_whitespace_and_text(self):
        text = "Non-microbial contamination was observed in your production area."
        detail = "Non-microbial contamination\nwas observed in your  production area.\n Specifically,\n  the floor\twas wet."
        self.assertEqual(ex._detail_after_heading(detail, text), "Specifically,\n  the floor\twas wet.")

    def test_heading_must_start_the_detail(self):
        text = "Non-microbial contamination was observed in your production area."
        for lead in ("Specifically, ", "OBSERVATION 2. ", "Observation 4. ", "See "):
            self.assertIsNone(ex._detail_after_heading(lead + text + " More.", text), lead)
        for lead in ("", ". ", "... ", "| ", "A. ", "j ", "(REPEAT OBSERVATION) "):
            self.assertEqual(ex._detail_after_heading(lead + text + " More.", text), "More.", repr(lead))

    def test_40_char_prefix_is_only_a_candidate(self):
        text = ("The responsibilities and procedures to the quality control unit are not in writing and fully "
                "followed.")
        self.assertGreater(len(text), 60)
        diverged = text[:40] + "were not reviewed by the firm. Specifically, the SOP was not approved."
        self.assertIsNone(ex._detail_after_heading(diverged, text))
        # 표제가 낱말 중간에서 끝나면(접두 일치) 불인정
        self.assertIsNone(ex._detail_after_heading("The quality control units are not adequate. More.",
                                                   "The quality control unit"))
        findings = [{"finding_id": "f1", "raw_signal_id": "r1", "published_date": "2024-01-17",
                     "finding_text": text, "detail_text": None}]
        raws = [{"raw_signal_id": "r1", "raw_json": {"fda_483_observations": [
            {"number": "10", "deficiency": ".", "detail": diverged}]}}]
        _, stats = ex.build_queue(findings, raws)
        self.assertEqual((stats["queued"], stats["no_detail"]), (0, 1))

    def test_only_fragment_headings_are_considered(self):
        text = "Non-microbial contamination was observed in your production area."
        findings = [{"finding_id": "f1", "raw_signal_id": "r1", "published_date": "2024-01-17",
                     "finding_text": text, "detail_text": None}]
        for deficiency, expect in ((".", 1), ("", 1), ("A.", 1), ("l , .", 1), ("i I _.", 1),
                                   ("response: A.", 0), ("for additional details.", 0),
                                   ("Equipment is not maintained.", 0)):
            raws = [{"raw_signal_id": "r1", "raw_json": {"fda_483_observations": [
                {"number": "2", "deficiency": deficiency, "detail": text + " Specifically, dust."}]}}]
            _, stats = ex.build_queue(findings, raws)
            self.assertEqual(stats["queued"], expect, repr(deficiency))
        # 다른 raw 의 관찰과는 짝이 되지 않는다
        raws = [{"raw_signal_id": "r2", "raw_json": {"fda_483_observations": [
            {"number": "2", "deficiency": ".", "detail": text + " Specifically, dust."}]}}]
        self.assertEqual(ex.build_queue(findings, raws)[1]["queued"], 0)

    def test_two_different_fragment_matches_are_ambiguous(self):
        text = "Non-microbial contamination was observed in your production area."
        findings = [{"finding_id": "f1", "raw_signal_id": "r1", "published_date": "2024-01-17",
                     "finding_text": text, "detail_text": None}]
        raws = [{"raw_signal_id": "r1", "raw_json": {"fda_483_observations": [
            {"number": "2", "deficiency": ".", "detail": text + " Specifically, dust."},
            {"number": "3", "deficiency": ".", "detail": text + " Specifically, rust."}]}}]
        _, stats = ex.build_queue(findings, raws)
        self.assertEqual((stats["queued"], stats["ambiguous"]), (0, 1))

    def test_short_heading_is_not_matched(self):
        self.assertIsNone(ex._detail_after_heading("No records. Specifically, none.", "No records."))


class ExistingPairRuleUnchangedTest(unittest.TestCase):
    """기존 완전 일치 짝은 그대로 — detail 을 자르지도 바꾸지도 않는다."""

    def test_exact_heading_pair_keeps_full_detail(self):
        text = "Employees are not given training in current good manufacturing practices."
        detail = text + " QC analysts were not trained on SOP QC-011."   # 표제가 detail 에 되풀이돼도 그대로
        findings = [{"finding_id": "f1", "raw_signal_id": "r1", "published_date": "2024-01-17",
                     "finding_text": text, "detail_text": None}]
        raws = [{"raw_signal_id": "r1", "raw_json": {"fda_483_observations": [
            {"number": "8", "deficiency": text, "detail": detail},
            {"number": "9", "deficiency": ".", "detail": text + " Other text."}]}}]
        queue, stats = ex.build_queue(findings, raws)
        self.assertEqual(queue[0]["detail_text"], detail)
        self.assertEqual((stats["queued"], stats["detail_head"], stats["ambiguous"]), (1, 0, 0))

    def test_exact_rule_ambiguity_and_stale_unchanged(self):
        text = "Employees are not given training in current good manufacturing practices."
        findings = [{"finding_id": "f1", "raw_signal_id": "r1", "published_date": "2024-01-17",
                     "finding_text": text, "detail_text": "Same."}]
        raws = [{"raw_signal_id": "r1", "raw_json": {"fda_483_observations": [
            {"number": "8", "deficiency": text, "detail": "One."},
            {"number": "9", "deficiency": text, "detail": "Two."}]}}]
        self.assertEqual(ex.build_queue(findings, raws)[1]["ambiguous"], 1)
        raws[0]["raw_json"]["fda_483_observations"] = [{"number": "8", "deficiency": text, "detail": "Same."}]
        self.assertEqual(ex.build_queue(findings, raws, stale_only=True)[1]["unchanged"], 1)

    def test_stale_mode_compares_trimmed_remainder(self):
        findings, raws = _retro_fixture()
        for f in findings:
            f["detail_text"] = dict((r[1], r[5].lstrip()) for r in RETRO_15)[f["finding_id"]]
        _, stats = ex.build_queue(findings, raws, stale_only=True)
        self.assertEqual((stats["queued"], stats["unchanged"]), (0, 13))


if __name__ == "__main__":
    unittest.main()
