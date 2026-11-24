"""경고서한 위반항목 — '모든 경로가 같은 함수' + 빈칸 사유 표식 + 상시 점검 + 재추출 도구 (2026-11-23).

데이터품질팀 재현(tests/test_wl_violations_repro_1123.py)이 밝힌 770행의 원인은 파서가 아니라 **경로**였다:
10-28 이전 과거분 백필이 본문만 싣고 추출을 부르지 않았다. 그래서 여기서 고정한다.
  ① 일일 수집·과거분 백필·WL 복구·재추출이 모두 `collect_intake.extract_wl_violations_detail` 한 함수를 지난다.
  ② 못 뽑으면 빈 목록 + 사유(`wl_violations_status`) — '본문 있음·위반항목 없음·사유 없음'은 경로 누락의 신호다.
  ③ 일일 수집 요약(health)과 내용 완전성 감시가 그 신호를 센다(0 이 아니면 경고).
  ④ 재추출 도구는 원문에 없는 문장을 쓰지 않고, 옛 목록을 줄이지 않으며, raw 의 두 칸만 바꾼다.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import backfill_wl_body_recovery as rec  # noqa: E402
import backfill_wl_violations_reextract as rx  # noqa: E402
import collect_fda_backfill as fb  # noqa: E402
import collect_intake as ci  # noqa: E402
import content_completeness as cc  # noqa: E402
from grm_health import _evaluate_health  # noqa: E402

_FIX = os.path.join(_ROOT, "tests", "fixtures", "wl_violations_1123", "samples.json")


def _sample(doc: str) -> dict:
    with open(_FIX, encoding="utf-8") as f:
        return next(s for s in json.load(f)["samples"] if s["document_id"] == doc)


_SENTINEL = ([{"number": "1", "statement": "SENTINEL", "citation": ""}], "F1")


def _raw_from_sample(s: dict, **extra) -> dict:
    raw = {"firm": s["firm"], "posted_date": s["published_date"], "letter_date": "",
           "issuing_office": s["issuing_office"], "subject": s["subject"],
           "url": f"https://www.fda.gov/warning-letters/fixture-{s['document_id']}",
           "wl_body_full": s["wl_body_full"]}
    raw.update(extra)
    return raw


def _row(raw: dict, rid: str = "rs-1", doc: str = "d1") -> dict:
    return {"raw_signal_id": rid, "document_id": doc, "collected_at": "2026-10-01T00:00:00Z",
            "raw_sha256": "old", "raw_json": json.dumps(raw, ensure_ascii=False), "row_json": "{}"}


class AllPathsSameFunctionTest(unittest.TestCase):
    """추출 함수를 가짜로 바꾸면 모든 경로의 결과가 같이 바뀌어야 한다 — 규칙을 베낀 경로가 없다는 증거."""

    def setUp(self) -> None:
        p = mock.patch.object(ci, "extract_wl_violations_detail", return_value=_SENTINEL)
        self.spy = p.start()
        self.addCleanup(p.stop)
        self.s = _sample("0af7c14e5949")

    def test_daily_build_wl_item(self) -> None:
        item = ci.build_wl_item(firm="F", posted_raw="11/10/2026", letter_date_raw="", issuing_office="CDER",
                                subject="CGMP", wl_href="https://www.fda.gov/x", office_verdict="keep",
                                excerpt=None, full=(self.s["wl_body_full"], ci.WL_BODY_STATUS_OK))
        self.assertEqual(item.raw_payload["wl_violations"], _SENTINEL[0])
        self.spy.assert_called()

    def test_backfill_wl_item_from_row(self) -> None:
        item = fb._wl_item_from_row(
            {"firm": "F", "posted_date": "11/10/2026", "letter_date": "", "issuing_office": "CDER",
             "subject": "CGMP", "wl_href": "https://www.fda.gov/x"},
            "keep", None, (self.s["wl_body_full"], ci.WL_BODY_STATUS_OK))
        self.assertEqual(item.raw_payload["wl_violations"], _SENTINEL[0])

    def test_wl_body_recovery_rebuild(self) -> None:
        item = rec._rebuild_item(_raw_from_sample(self.s), ("", ci.WL_BODY_STATUS_OK),
                                 (self.s["wl_body_full"], ci.WL_BODY_STATUS_OK))
        self.assertEqual(item.raw_payload["wl_violations"], _SENTINEL[0])

    def test_reextract_tool(self) -> None:
        rows, _status, fmt = rx.reextract_raw(_raw_from_sample(self.s))
        self.assertEqual((rows, fmt), _SENTINEL)

    def test_public_list_function_delegates(self) -> None:
        self.assertEqual(ci.extract_wl_violations_from_text("x"), _SENTINEL[0])


class StatusMarkerTest(unittest.TestCase):
    def _build(self, s: dict, office: str | None = None):
        return ci.build_wl_item(firm=s["firm"], posted_raw="11/10/2026", letter_date_raw="",
                                issuing_office=office or s["issuing_office"], subject=s["subject"],
                                wl_href="https://www.fda.gov/x/" + s["document_id"], office_verdict="keep",
                                excerpt=None, full=(s["wl_body_full"], ci.WL_BODY_STATUS_OK))

    def test_unfillable_row_gets_reason_not_fabrication(self) -> None:
        raw = self._build(_sample("0fd447d61356")).raw_payload      # GLP — 본문이 'Specifically,' 로 시작
        self.assertNotIn("wl_violations", raw)
        self.assertEqual(raw["wl_violations_status"], ci.WL_VIOLATIONS_STATUS_BODY_HEAD_TRUNCATED)
        self.assertEqual(ci.wl_violations_gap_kind(raw), ci.WL_VIOLATIONS_STATUS_BODY_HEAD_TRUNCATED)

    def test_filled_row_has_no_status_key(self) -> None:
        raw = self._build(_sample("425d7e338b22")).raw_payload      # F4 미승인 신약
        self.assertTrue(raw["wl_violations"])
        self.assertNotIn("wl_violations_status", raw)

    def test_tobacco_center_letter_gets_no_f4_sentence(self) -> None:
        # 담배센터 발행이면 F2~F4 를 돌리지 않는다(시제품에서 담배·FSVP 서한에 판정 문장이 붙었다).
        raw = self._build(_sample("425d7e338b22"), office="Center for Tobacco Products").raw_payload
        self.assertNotIn("wl_violations", raw)
        self.assertEqual(raw["wl_violations_status"], ci.WL_VIOLATIONS_STATUS_OUT_OF_SCOPE)

    def test_no_body_no_status(self) -> None:
        item = ci.build_wl_item(firm="F", posted_raw="11/10/2026", letter_date_raw="", issuing_office="CDER",
                                subject="CGMP", wl_href="https://www.fda.gov/x", office_verdict="keep",
                                excerpt=None, full=("", "fetch-403"))
        self.assertNotIn("wl_violations_status", item.raw_payload)
        self.assertEqual(ci.wl_violations_gap_kind(item.raw_payload), "")

    def test_gap_kind_unmarked_is_the_old_backfill_shape(self) -> None:
        self.assertEqual(ci.wl_violations_gap_kind({"wl_body_full": "body"}), "unmarked")
        self.assertEqual(ci.wl_violations_gap_kind({"wl_body_full": "b", "wl_violations": []}), "unmarked")
        self.assertEqual(ci.wl_violations_gap_kind({"wl_body_full": "b", "wl_violations_status": "zzz"}),
                         "unmarked")

    def test_recovery_knows_the_new_key(self) -> None:
        # 복구 도구가 '모르는 키'로 건너뛰지 않게 — build_wl_item 이 만드는 키는 전부 알아야 한다.
        self.assertIn("wl_violations_status", rec.KNOWN_RAW_KEYS)


class DailySummaryHealthTest(unittest.TestCase):
    def _health(self, **overrides):
        stats = ci.CollectionStats()
        for k, v in overrides.items():
            setattr(stats, k, v)
        return _evaluate_health(
            stats=stats, active={"wl"}, enable_search=False,
            enable_mfds=False, enable_mfds_law=False, enable_mfds_recall=False,
            enable_mfds_admin=False, enable_mfds_gmp_cert=False,
            enable_mfds_safety_letter=False, enable_mfds_gmp_inspection=False,
            enable_ich=False, enable_who=False, enable_hc=False, enable_fda483=False,
            enable_moleg_api=False, enable_scrape=False, event_name="schedule",
            emit_routine_handoff=False, handoff_emitted=False, handoff_failed=False,
            handoff_error_msg="",
        )

    def test_run_counts(self) -> None:
        class _It:
            def __init__(self, raw: dict, doc: str = "") -> None:
                self.raw_payload, self.document_id = raw, doc
        c = ci.wl_violations_run_counts([
            _It({"wl_body_full": "b", "wl_violations": [{"number": "1"}]}),
            _It({"wl_body_full": "b", "wl_violations_status": "unsupported_format"}, "dX"),
            _It({"wl_body_full": "b"}),
            _It({"wl_body_full": "b", "wl_violations_status": "opdp_promotional",
                 "issuing_office": "Office of Prescription Drug Promotion"}),
            _It({}),
        ])
        self.assertEqual((c["filled"], c["unsupported"], c["unmarked"], c["unsupported_docs"]), (1, 1, 1, ["dX"]))
        self.assertEqual(c["out_of_scope"], {"opdp_promotional": 1})

    def test_warnings(self) -> None:
        health = self._health(wl_fetched=3, wl_violations_unmarked=1, wl_violations_unsupported=1,
                              wl_violations_out_of_scope={"opdp_promotional": 1})
        self.assertEqual({w.code for w in health.warnings if w.code.startswith("wl-violations-")},
                         {"wl-violations-unsupported"})
        self.assertIn("wl-violations-unmarked", {item.code for item in health.infos})
        scope = next(item for item in health.infos if item.code == "wl-violations-out-of-scope")
        self.assertIn("opdp_promotional=1", scope.detail)


class ContentCompletenessGapTest(unittest.TestCase):
    def test_reports_known_reasons_and_alarms_only_on_unknown_format(self) -> None:
        rows = [
            {"source": "FDA Warning Letter", "document_id": "a", "raw_json": json.dumps({"wl_body_full": "b"})},
            {"source": "FDA Warning Letter", "document_id": "b",
             "raw_json": json.dumps({"wl_body_full": "b", "wl_violations_status": "body_head_truncated"})},
            {"source": "FDA Warning Letter", "document_id": "c",
             "raw_json": json.dumps({"wl_body_full": "b", "wl_violations": [{"number": "1"}]})},
            {"source": "FDA Warning Letter", "document_id": "d", "raw_json": json.dumps({"firm": "x"})},
            {"source": "FDA 483", "document_id": "e", "raw_json": json.dumps({"wl_body_full": "b"})},
            {"source": "FDA Warning Letter", "document_id": "f",
             "raw_json": {"wl_body_full": "b", "wl_violations_status": "unsupported_format"}},
            {"source": "FDA Warning Letter", "document_id": "g",
             "raw_json": {"wl_body_full": "b", "issuing_office": "Office of Prescription Drug Promotion",
                          "wl_violations_status": "unsupported_format"}},
        ]
        g = cc.wl_violation_gaps(rows)
        self.assertEqual(g["unmarked"], 1)
        self.assertEqual(g["unmarked_docs"], ["a"])
        self.assertEqual(g["unsupported_docs"], ["f"])
        self.assertEqual(g["out_of_scope"], {"opdp_promotional": 1})
        self.assertEqual(g["by_kind"], {"body_head_truncated": 1, "filled": 1, "unmarked": 1,
                                        "opdp_promotional": 1, "unsupported_format": 1})
        rep = cc.build_report([], {}, today=date(2026, 11, 23), wl_gaps=g)
        self.assertEqual(rep["counts"]["wl_violation_gaps"], 1)
        self.assertEqual(rep["issues"][0]["action"], "open")
        self.assertIn("경고서한 위반항목 빈칸", cc.render_markdown(rep))
        self.assertIn("추출 대상 밖 1행 (opdp_promotional 1행)", cc.render_markdown(rep))

    def test_zero_gap_closes(self) -> None:
        rep = cc.build_report([], {}, today=date(2026, 11, 23), wl_gaps={
            "unmarked": 1, "unsupported": 0, "by_kind": {"unmarked": 1, "opdp_promotional": 1},
            "out_of_scope": {"opdp_promotional": 1},
        })
        self.assertEqual(rep["counts"]["wl_violation_gaps"], 0)
        self.assertEqual(rep["issues"][0]["action"], "close")


class RealDryMisfireTest(unittest.TestCase):
    """실제 DB dry(11-23, 1,403행)에서 찾은 오검출 세 유형 — 원문 발췌(공백 정규화) 그대로."""

    def test_api_lead_including_not_limited_to_is_region(self) -> None:
        # df43f47188af — 도입이 'observed specific deviations including, but not limited to, the following.'
        text = ("During our inspection, our investigators observed specific deviations including, but not limited to, "
                "the following. 1. Failure to properly maintain buildings and facilities used in the manufacture of "
                "API. Facility Conditions You failed to maintain your drug manufacturing facility in a good state of "
                "repair. This may also render your (b)(4) USP adulterated under section 501(b) of the FD&C Act.")
        rows, fmt = ci.extract_wl_violations_detail(text)
        self.assertEqual(fmt, "F2")
        self.assertEqual([r["statement"] for r in rows],
                         ["Failure to properly maintain buildings and facilities used in the manufacture of API."])

    def test_label_quote_is_its_own_sentence(self) -> None:
        # 287aa0c57364 — 라벨 인용 뒤 '[from your product’s label]' 에 마침표가 없다.
        text = ("EZRICARE Artificial Tears “Drug Facts . . . Uses . . . ■ for use as a protectant against further "
                "irritation or to relieve dryness of the eye” [from your product’s label] EZRICARE Artificial Tears "
                "is misbranded under section 502(j) of the FD&C Act.")
        rows, fmt = ci.extract_wl_violations_detail(text)
        self.assertEqual(fmt, "F4")
        self.assertEqual(rows[0]["statement"],
                         "EZRICARE Artificial Tears is misbranded under section 502(j) of the FD&C Act.")

    def test_glued_compounding_heading_is_stripped(self) -> None:
        # 5f1bb791f6cd — 'B. Violations of the FDCA Adulterated Drug Products The FDA investigator noted …'
        text = ("U.S.C. §§ 351(a)(2)(B), 352(f)(1) and 355(a)]. B. Violations of the FDCA Adulterated Drug Products "
                "The FDA investigator noted that drug products intended or expected to be sterile were prepared, "
                "packed, or held under insanitary conditions, whereby they may have become contaminated with filth or "
                "rendered injurious to health, causing your drug products to be adulterated under section "
                "501(a)(2)(A) of the FDCA. For example, the investigator observed that: 1. Your facility lacked a "
                "certified ISO 5 classified area for sterile compounding.")
        rows, fmt = ci.extract_wl_violations_detail(text)
        self.assertEqual(fmt, "F4")
        self.assertTrue(rows[0]["statement"].startswith("The FDA investigator noted that drug products"))
        self.assertIn("501(a)(2)(A)", rows[0]["citation"])

    def test_hypothetical_may_render_not_a_verdict(self) -> None:
        self.assertEqual(ci.extract_wl_violations_detail(
            "This may also render your (b)(4) USP adulterated under section 501(b) of the FD&C Act.")[0], [])


class ReextractToolTest(unittest.TestCase):
    def test_fill_and_mark_and_same(self) -> None:
        rows = [
            _row(_raw_from_sample(_sample("511a2f6e48e7")), "r1", "511a"),               # F1b 채움
            _row(_raw_from_sample(_sample("0fd447d61356")), "r2", "0fd4"),               # 못 뽑음 → 사유만
        ]
        f1 = _sample("0af7c14e5949")
        rows.append(_row(_raw_from_sample(f1, wl_violations=f1["expected"], wl_body_excerpt="e"), "r3", "0af7"))
        rep = rx.run(rows, apply=False, limit=2000)
        self.assertEqual(rep.kinds, {"fill": 1, "mark": 1, "same": 1})
        self.assertEqual(rep.fill_by_format, {"F1b": 1})
        self.assertEqual(rep.remain_empty_by_reason, {"body_head_truncated": 1})
        self.assertEqual(rep.blank_before_unmarked, 2)
        self.assertEqual(rep.not_verbatim, 0)

    def test_stale_superset_written_diverged_not(self) -> None:
        f1 = _sample("0af7c14e5949")
        stale = _row(_raw_from_sample(f1, wl_violations=f1["expected"][:1]), "r1", "stale")
        wrong = [{"number": "1", "statement": "Something not in the new list.", "citation": ""}]
        div = _row(_raw_from_sample(f1, wl_violations=wrong), "r2", "div")
        written: list[dict] = []
        rep = rx.run([stale, div], apply=True, limit=2000,
                     writer=lambda row, new_raw, res: written.append(new_raw) or "")
        self.assertEqual(rep.kinds, {"diverged": 1, "stale": 1})
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["wl_violations"], f1["expected"])
        # 바꾸는 칸은 둘뿐 — 나머지 raw 는 그대로.
        old = json.loads(stale["raw_json"])
        self.assertEqual({k: v for k, v in written[0].items() if k != "wl_violations"},
                         {k: v for k, v in old.items() if k != "wl_violations"})

    def test_not_verbatim_guard_blocks_write(self) -> None:
        s = _sample("425d7e338b22")
        fake = ([{"number": "1", "statement": "Invented sentence that is not in the letter.", "citation": ""}], "F4")
        with mock.patch.object(ci, "extract_wl_violations_detail", return_value=fake):
            written: list = []
            rep = rx.run([_row(_raw_from_sample(s))], apply=True, limit=2000,
                         writer=lambda *a: written.append(a) or "")
        self.assertEqual(rep.not_verbatim, 1)
        self.assertEqual(written, [])

    def test_unknown_key_skipped(self) -> None:
        rep = rx.run([_row(_raw_from_sample(_sample("425d7e338b22"), extra_key="x"))], apply=False, limit=2000)
        self.assertEqual(rep.kinds, {"skipped": 1})

    def test_limit_defers_and_dry_never_writes(self) -> None:
        rows = [_row(_raw_from_sample(_sample(d)), f"r{i}", d)
                for i, d in enumerate(("511a2f6e48e7", "425d7e338b22", "36f8f018c810"))]
        calls: list = []
        rep = rx.run(rows, apply=True, limit=2, writer=lambda *a: calls.append(a) or "")
        self.assertEqual((rep.written, rep.deferred_by_limit, len(calls)), (2, 1, 2))
        dry = rx.run(rows, apply=False, limit=2000, writer=lambda *a: calls.append(a) or "")
        self.assertEqual((dry.written, len(calls)), (0, 2))

    def test_markdown_has_table(self) -> None:
        rep = rx.run([_row(_raw_from_sample(_sample("511a2f6e48e7")), "r1", "511a")], apply=False, limit=2000)
        md = rx.render_markdown(rep)
        self.assertIn("| 511a | fill | F1b | 0→5 |", md)


if __name__ == "__main__":
    unittest.main()
