"""OCR 엔진 부재로 빈 본문 적재된 스캔 483 소급 복구 — backfill_483_ocr_recovery.

이 스크립트의 핵심 전제는 하나다: **저장된 raw_payload 만으로 `_to_item` 의 입력(nrow)을
무손실 복원할 수 있다.** 그래야 부분 패치(raw_json 만 손으로 기워 넣어 raw_sha256/row_json/
body 가 어긋나는 길) 대신 라이브 수집과 같은 함수로 행 전체를 다시 만들 수 있다.
`RoundTripIdentityTest` 가 그 전제를 고정한다 — `_to_item` 이 읽는 nrow 키가 늘거나
raw_payload 의 키 이름이 바뀌면 즉시 깨진다.

나머지는 안전장치 검증이다. 이 스크립트는 프로덕션 `raw_signals` 를 **제자리 교체**하므로,
"쓰지 않아야 할 때 쓰지 않는다"가 "쓸 때 제대로 쓴다"보다 중요하다.
"""
from __future__ import annotations

import os
import sys
import unittest
from dataclasses import asdict
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_483_ocr_recovery as rec  # noqa: E402
import collect_fda_483 as f  # noqa: E402


_AJAX_ROW = [
    "05/27/2026",                                        # record date
    "BPI Labs, LLC",                                     # company
    "3012345678",                                        # FEI
    '<a href="/media/555001/download">483</a>',          # record type + media href
    "FL",                                                # state
    "",                                                  # country
    "Pharmaceutical Manufacturer",                       # establishment type
    "06/01/2026",                                        # publish date
    "",
]

_TEXT = (
    "OBSERVATION 1\n"
    "Aseptic processing operations were not validated for the sterile drug product.\n"
    "Specifically, media fills were not performed at the required frequency.\n"
    "OBSERVATION 2\n"
    "Laboratory records did not include complete data derived from all tests.\n"
)


def _nrow() -> dict[str, str]:
    return f._datatable_norm_rows([list(_AJAX_ROW)])[0]


def _raw_payload(text: str = _TEXT, status: str = "pdf-ok-ocr") -> dict:
    item = rec._rebuild_item(_nrow(), text, status)
    assert item is not None
    return item.raw_payload


class RoundTripIdentityTest(unittest.TestCase):
    """저장된 raw_payload → nrow 복원 → 재구성이 **원본과 동일한 raw_payload** 를 낳는다.

    이 동일성이 이 스크립트 전체의 근거다. 깨지면 복구가 원본과 다른 행을 쓰게 된다.
    """

    def test_nrow_reconstruction_is_lossless(self) -> None:
        original = _raw_payload()
        rebuilt = rec._rebuild_item(
            rec.nrow_from_raw_payload(original), _TEXT, "pdf-ok-ocr",
        ).raw_payload
        self.assertEqual(rebuilt, original)

    def test_reconstruction_covers_every_nrow_key_to_item_reads(self) -> None:
        """복원된 nrow 가 `_to_item` 이 실제로 읽는 키를 전부 덮는다.

        `_to_item` 은 대부분의 키를 `nrow["x"]`(KeyError)로 읽는다 — 키가 하나라도 빠지면
        런타임에 터진다. 복원 매핑이 낡았는지를 여기서 잡는다.
        """
        restored = rec.nrow_from_raw_payload(_raw_payload())
        for key in ("record_type", "media_id", "company", "fei", "state",
                    "country", "establishment_type", "record_date", "publish_date"):
            self.assertIn(key, restored, f"복원 nrow 에 {key} 가 없다")
        self.assertEqual(restored, {k: v for k, v in _nrow().items() if k in restored})

    def test_publish_date_stays_raw_mdy(self) -> None:
        """publish_date 는 원문 MM/DD/YYYY 그대로 — ISO 로 바꾸면 재구성이 어긋난다."""
        self.assertEqual(rec.nrow_from_raw_payload(_raw_payload())["publish_date"], "06/01/2026")

    def test_rebuilt_row_keeps_same_raw_signal_id(self) -> None:
        """raw_signal_id 는 document_id 에만 의존 → 내용을 고쳐도 같은 행을 가리킨다."""
        import findings_store
        empty = rec._rebuild_item(_nrow(), "", "scan-ocr-unavailable:no tessdata")
        full = rec._rebuild_item(_nrow(), _TEXT, "pdf-ok-ocr")
        a = findings_store.raw_signal_from_intake_item(empty, collected_at="2026-07-28T00:00:00Z")
        b = findings_store.raw_signal_from_intake_item(full, collected_at="2026-07-28T00:00:00Z")
        self.assertEqual(a["raw_signal_id"], b["raw_signal_id"])
        self.assertNotEqual(a["raw_sha256"], b["raw_sha256"])   # 내용은 실제로 달라졌다


class TargetSelectionTest(unittest.TestCase):
    """되찾을 수 있는 결손만 고른다 — 원문의 사정은 건드리지 않는다."""

    def test_prefix_matches_collector_definition(self) -> None:
        """상수가 수집기의 판정과 어긋나면 대상 선정이 조용히 빗나간다."""
        self.assertTrue(f.is_ocr_engine_unavailable(rec.OCR_UNAVAILABLE_PREFIX + ":x"))
        self.assertEqual(rec.OCR_UNAVAILABLE_PREFIX, f._OCR_ENGINE_UNAVAILABLE_PREFIX)

    def test_only_engine_absence_is_a_target(self) -> None:
        self.assertTrue(rec.is_ocr_unavailable_row(
            {"fda483_text_status": "scan-ocr-unavailable:No tessdata specified"}))
        for other in ("scan-no-text", "scan-ocr-empty", "scan-ocr-budget", "pdf-ok", ""):
            self.assertFalse(rec.is_ocr_unavailable_row({"fda483_text_status": other}),
                             f"대상이 아니어야 한다: {other!r}")
        self.assertFalse(rec.is_ocr_unavailable_row({}))


def _row(raw_signal_id: str = "rawsig-x", *, status: str = "scan-ocr-unavailable:no tessdata",
         collected_at: str = "2026-07-28T01:02:03Z") -> dict:
    import json
    payload = _raw_payload("", status)
    return {
        "raw_signal_id": raw_signal_id,
        "document_id": "fda483-555001",
        "collected_at": collected_at,
        "raw_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def _run(**over):
    """net-free run() — 조회/PDF/upsert 를 전부 스텁."""
    import findings_store
    real_id = findings_store.raw_signal_from_intake_item(
        rec._rebuild_item(_nrow(), "", "scan-ocr-unavailable:x"), collected_at="",
    )["raw_signal_id"]

    rows = over.pop("rows", [_row(real_id)])
    upsert = over.pop("upsert", mock.MagicMock(return_value=(201, [{"raw_signal_id": real_id}], "")))
    kwargs = dict(
        base_url="https://example.supabase.co", service_key="k", dry_run=True,
        fetch_raw_signals=lambda *a, **k: list(rows),
        fetch_text=lambda url: (_TEXT, "pdf-ok-ocr"),
        upsert_raw_signal=upsert,
        sleeper=lambda s: None,
    )
    kwargs.update(over)
    report, code = rec.run(**kwargs)
    return report, code, upsert, real_id


class RecoveryRunTest(unittest.TestCase):

    def test_dry_run_reports_recovery_without_writing(self) -> None:
        report, code, upsert, _rid = _run()
        self.assertEqual(code, 0)
        self.assertEqual(report.mode, "dry_run")
        self.assertEqual(report.marked, 1)
        self.assertEqual(report.candidates, 1)
        self.assertEqual(report.recovered, 1)
        self.assertEqual(report.observations_recovered, 2)
        upsert.assert_not_called()

    def test_apply_upserts_and_preserves_collected_at(self) -> None:
        report, code, upsert, rid = _run(dry_run=False)
        self.assertEqual(code, 0)
        self.assertEqual(report.recovered, 1)
        upsert.assert_called_once()
        record = upsert.call_args[0][2]
        self.assertEqual(record["raw_signal_id"], rid)
        self.assertEqual(record["collected_at"], "2026-07-28T01:02:03Z")  # ★원본 시점 보존
        self.assertNotIn("fda483_text_status", record["raw_json"])        # 결손 사유 소멸
        self.assertIn("fda_483_observations", record["raw_json"])

    def test_still_empty_document_is_left_alone(self) -> None:
        """OCR 재시도에도 본문이 없으면 **쓰지 않는다** — 빈손을 빈손으로 덮지 않는다."""
        report, code, upsert, _rid = _run(
            dry_run=False, fetch_text=lambda url: ("", "scan-ocr-unavailable:still no tessdata"))
        self.assertEqual(report.still_empty, 1)
        self.assertEqual(report.no_text, 1)                  # ★수집/OCR 문제로 귀속
        self.assertEqual(report.text_without_extraction, 0)
        self.assertEqual(report.recovered, 0)
        upsert.assert_not_called()
        self.assertEqual(code, 0)      # 실패가 아니라 '아직 안 됨'

    def test_text_without_extractable_content_is_left_alone(self) -> None:
        report, _code, upsert, _rid = _run(
            dry_run=False, fetch_text=lambda url: ("....", "pdf-ok-ocr"))
        self.assertEqual(report.still_empty, 1)
        self.assertEqual(report.text_without_extraction, 1)  # ★파서 문제로 귀속
        self.assertEqual(report.no_text, 0)
        self.assertEqual(report.recovered, 0)
        upsert.assert_not_called()

    def test_still_empty_is_the_sum_of_two_distinct_causes(self) -> None:
        """★계기판 회귀 방어. `still_empty` 하나만 보면 수집 문제와 파서 문제가 같은
        숫자로 보인다 — 실제로 이 혼동이 OCR 오진 3회(엔진 배선·DPI 300·DPI 400)를 낳았다.
        두 원인은 처방이 정반대이므로 리포트는 반드시 분리해서 답해야 한다."""
        no_text, _c, _u, _r = _run(
            dry_run=False, fetch_text=lambda url: ("", "scan-no-text"))
        parser, _c2, _u2, _r2 = _run(
            dry_run=False, fetch_text=lambda url: ("....", "pdf-ok"))
        self.assertEqual(no_text.still_empty, parser.still_empty)      # 합계는 구분 못 함
        self.assertNotEqual(no_text.no_text, parser.no_text)           # 분리 필드는 구분함
        for rep in (no_text, parser):
            self.assertEqual(rep.still_empty, rep.no_text + rep.text_without_extraction)

    def test_excerpt_without_observations_is_not_counted_as_full_recovery(self) -> None:
        """발췌만 살고 지적사항이 0건이면 findings 는 한 건도 안 생긴다 — '복구'로 뭉뚱그리면
        `recovered` 가 커 보여 파서 결함이 리포트에서 사라진다(07-31 DPI 실험에서 실제로
        `recovered 60 / observations_recovered 0` 이 나왔는데 60 만 눈에 띄었다)."""
        # 실제 사례와 같은 모양: 483 양식 보일러플레이트만 있고 지적사항 표제가 없다.
        # excerpt 앵커(`this document lists observations`)에는 걸리므로 발췌는 나온다.
        prose = ("This document lists observations made by the FDA representative(s) during "
                 "the inspection of your facility. They are inspectional observations, and do "
                 "not represent a final Agency determination regarding your compliance. ") * 4
        for dry in (True, False):
            report, _code, _upsert, _rid = _run(
                dry_run=dry, fetch_text=lambda url: (prose, "pdf-ok"))
            self.assertEqual(report.observations_recovered, 0)
            self.assertEqual(report.recovered, report.recovered_excerpt_only)
            self.assertGreaterEqual(report.recovered_excerpt_only, 1,
                                    f"발췌만 살아난 문서가 별도 카운터에 잡혀야 한다(dry={dry})")

    def test_dry_run_and_apply_count_identically(self):
        """★dry-run 과 apply 가 **같은 카운터 코드**를 쓴다. 두 경로에 복제해 두면 한쪽만
        고치게 된다 — 실제로 그렇게 했다가 `recovered_excerpt_only` 가 dry-run 에서 항상
        0 이 됐고, 하필 dry-run 이 진단에 쓰는 모드라 계기판이 처음부터 거짓말을 했다."""
        prose = ("This document lists observations made by the FDA representative(s) "
                 "during the inspection of your facility. ") * 4
        for text in (_TEXT, prose):
            dry, _c, _u, _r = _run(dry_run=True, fetch_text=lambda url: (text, "pdf-ok"))
            app, _c2, _u2, _r2 = _run(dry_run=False, fetch_text=lambda url: (text, "pdf-ok"))
            for field in ("recovered", "observations_recovered", "recovered_excerpt_only"):
                self.assertEqual(getattr(dry, field), getattr(app, field),
                                 f"{field} 가 dry-run 과 apply 에서 다르다")

    def test_id_mismatch_never_writes(self) -> None:
        """재구성이 다른 문서를 가리키면 떠돌이 행을 만들지 않는다."""
        report, _code, upsert, _rid = _run(dry_run=False, rows=[_row("rawsig-WRONG")])
        self.assertEqual(report.id_mismatch, 1)
        self.assertEqual(report.recovered, 0)
        upsert.assert_not_called()

    def test_domain_gate_drop_never_writes(self) -> None:
        report, _code, upsert, _rid = _run(
            dry_run=False, rebuild_item=lambda nrow, text, status: None)
        self.assertEqual(report.gate_dropped, 1)
        self.assertEqual(report.recovered, 0)
        upsert.assert_not_called()

    def test_non_target_rows_are_filtered_out(self) -> None:
        """서버 like 로 걸려 왔더라도 상태값이 대상이 아니면 손대지 않는다."""
        report, _code, upsert, _rid = _run(
            dry_run=False, rows=[_row(status="scan-no-text")])
        self.assertEqual(report.marked, 0)
        self.assertEqual(report.attempted, 0)
        upsert.assert_not_called()

    def test_upsert_failure_is_counted_not_raised(self) -> None:
        report, code, _upsert, _rid = _run(
            dry_run=False,
            upsert=mock.MagicMock(return_value=(500, None, "http_500")))
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.recovered, 0)
        self.assertEqual(code, 1)                       # 전건 실패 = 광범위
        self.assertIn("upsert_failed:http_500", report.failure_reasons)

    def test_limit_zero_means_all(self) -> None:
        """`limit=0` 을 '0건 처리'로 읽으면 apply 가 조용히 무동작한다(07-30 실사고)."""
        report, _code, _upsert, _rid = _run(limit=0)
        self.assertEqual(report.candidates, 1)

    def test_bad_supabase_url_is_exit_2(self) -> None:
        report, code = rec.run(base_url="http://insecure", service_key="k", dry_run=True)
        self.assertEqual(code, 2)
        self.assertEqual(report.candidates, 0)

    def test_service_key_never_appears_in_report(self) -> None:
        import json
        report, _code, _upsert, _rid = _run(
            dry_run=False, service_key="super-secret-key",
            upsert=mock.MagicMock(return_value=(0, None, "http_401")))
        from dataclasses import asdict
        self.assertNotIn("super-secret-key", json.dumps(asdict(report), ensure_ascii=False))


class PostResolutionTest(unittest.TestCase):
    """공용 POST 헬퍼의 기본 정책은 바뀌지 않는다 — 일상 수집이 기존 행을 덮으면 안 된다."""

    def test_default_resolution_is_ignore_duplicates(self) -> None:
        import findings_supabase_append as fsa
        with mock.patch("findings_supabase_append.requests.post") as post:
            post.return_value = mock.MagicMock(status_code=201, json=lambda: [])
            fsa._post_rows("https://x.supabase.co", "k", "raw_signals", [{}], "raw_signal_id")
        self.assertIn("resolution=ignore-duplicates", post.call_args[1]["headers"]["Prefer"])

    def test_recovery_goes_through_apply_version(self) -> None:
        """[원문 갱신 규칙 R3 2026-11-12] 회수는 merge-duplicates upsert 가 아니라 함수(148) 경유다 —
        읽은 시점 지문을 expected 로 넘기고, 옛 판 보관은 DB 가 한다."""
        import grm_findings as gf
        import hashlib
        raw = gf.canonical_json({"a": 1})
        record = {"raw_signal_id": "r", "raw_json": raw, "row_json": "{}",
                  "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
        with mock.patch("raw_versioning.requests.post") as post:
            post.return_value = mock.MagicMock(status_code=200, json=lambda: {"status": "applied"})
            got = rec._upsert_raw_signal("https://x.supabase.co", "k", record,
                                         row={"raw_signal_id": "r", "raw_sha256": "e" * 64})
        self.assertEqual(got, (200, [{"raw_signal_id": "r"}], ""))
        self.assertTrue(post.call_args[0][0].endswith("/rest/v1/rpc/raw_signal_apply_version"))
        body = post.call_args[1]["json"]
        self.assertEqual(body["p_expected_raw_sha256"], "e" * 64)
        self.assertEqual(body["p_change_kind"], "reextract")

    def test_recovery_without_read_fingerprint_refuses(self) -> None:
        with mock.patch("raw_versioning.requests.post") as post:
            got = rec._upsert_raw_signal("https://x.supabase.co", "k", {"raw_signal_id": "r"})
        post.assert_not_called()
        self.assertEqual(got[2], "missing_expected_raw_sha256")

    def test_conflict_is_a_failure_not_a_silent_success(self) -> None:
        import grm_findings as gf
        import hashlib
        raw = gf.canonical_json({"a": 1})
        record = {"raw_signal_id": "r", "raw_json": raw, "row_json": "{}",
                  "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
        with mock.patch("raw_versioning.requests.post") as post:
            post.return_value = mock.MagicMock(status_code=200, json=lambda: {"status": "conflict"})
            got = rec._upsert_raw_signal("https://x.supabase.co", "k", record,
                                         row={"raw_signal_id": "r", "raw_sha256": "e" * 64})
        self.assertEqual(got[2], "apply_version_conflict")


class CollectionCappedModeTest(unittest.TestCase):
    """[#1209] 수집 상한(PDF 건수·OCR 예산)으로 본문 없이 적재된 행을 예약 실행이 회수한다.

    일일 수집은 이미 저장된 document_id 를 다시 받지 않으므로, 상한 밖 문서는 이 경로가
    없으면 영구히 빈손이다(2026-10-26 FDA 대량 공개 때 51건)."""

    def test_capped_statuses_match_what_the_collector_writes(self):
        """상태 문자열이 수집기와 어긋나면 후보가 조용히 0건이 된다 — 실제 산출물로 고정."""
        with mock.patch.object(f, "_OCR_BUDGET", {"remaining": 0, "used": 0}):
            _text, budget_status = f._ocr_483_pdf_text_uncounted(b"", 1000)
        self.assertIn(budget_status, rec.CAPPED_TEXT_STATUSES)
        not_attempted = rec._rebuild_item(_nrow(), "", "not-attempted").raw_payload
        self.assertTrue(rec.is_collection_capped_row(not_attempted))

    def test_predicate_targets_only_cap_statuses(self):
        for status in rec.CAPPED_TEXT_STATUSES:
            self.assertTrue(rec.is_collection_capped_row({"fda483_text_status": status}))
        for other in ("scan-no-text", "scan-ocr-unavailable:x", "fetch-fail:404", "pdf-ok", ""):
            self.assertFalse(rec.is_collection_capped_row({"fda483_text_status": other}), other)
        self.assertFalse(rec.is_collection_capped_row({"fda483_excerpt": "OBSERVATION 1"}))

    def test_fetch_narrows_on_server(self):
        seen = {}

        def fake_pages(base, key, table, **kw):
            seen.update(kw.get("extra_params") or {})
            return []

        with mock.patch.object(rec.fsb, "_fetch_all_pages", fake_pages):
            rec._fetch_raw_signals("https://x", "k", mode=rec.MODE_COLLECTION_CAPPED)
        self.assertEqual(
            seen.get("or"),
            "(raw_json.like.*not-attempted*,raw_json.like.*scan-ocr-budget*)")

    def _capped_run(self, **over):
        import findings_store
        rid = findings_store.raw_signal_from_intake_item(
            rec._rebuild_item(_nrow(), "", "not-attempted"), collected_at="",
        )["raw_signal_id"]
        over.setdefault("rows", [_row(rid, status="not-attempted")])
        return _run(mode=rec.MODE_COLLECTION_CAPPED, **over)

    def test_capped_row_is_recovered_with_observations(self):
        report, code, upsert, _rid = self._capped_run(dry_run=False)
        self.assertEqual(code, 0)
        self.assertEqual(report.marked, 1)
        self.assertEqual(report.recovered, 1)
        self.assertEqual(report.observations_recovered, 2)
        record = upsert.call_args[0][2]
        self.assertNotIn("fda483_text_status", record["raw_json"])
        self.assertEqual(record["collected_at"], "2026-07-28T01:02:03Z")

    def test_definitive_empty_result_replaces_cap_marker(self):
        """404·텍스트층 없음이면 상한 표식을 실제 사유로 바꿔 매 실행 재시도를 끊는다."""
        report, _code, upsert, rid = self._capped_run(
            dry_run=False, fetch_text=lambda url: ("", "scan-no-text"))
        self.assertEqual(report.no_text, 1)
        self.assertEqual(report.status_resolved, 1)
        upsert.assert_called_once()
        record = upsert.call_args[0][2]
        self.assertEqual(record["raw_signal_id"], rid)
        self.assertEqual(rec._json_object(record["raw_json"])["fda483_text_status"],
                         "scan-no-text")

    def test_transient_fetch_failure_keeps_cap_marker(self):
        """5xx·타임아웃은 확정 사유가 아니다 — 표식을 바꾸면 일시 장애로 재시도가 끊긴다."""
        report, _code, upsert, _rid = self._capped_run(
            dry_run=False,
            fetch_text=lambda url: ("", "fetch-fail:HTTP GET final failure: 503 Server Error"))
        self.assertEqual(report.status_resolved, 0)
        upsert.assert_not_called()

    def test_cap_hit_again_leaves_row_for_next_run(self):
        """회수 실행도 예산에 걸리면 표식을 그대로 둔다 — 다음 실행이 다시 시도한다."""
        report, _code, upsert, _rid = self._capped_run(
            dry_run=False, fetch_text=lambda url: ("", "scan-ocr-budget"))
        self.assertEqual(report.status_resolved, 0)
        upsert.assert_not_called()

    def test_dry_run_counts_resolution_without_writing(self):
        report, _code, upsert, _rid = self._capped_run(
            fetch_text=lambda url: ("", "fetch-fail:HTTP GET final failure: (404 Client Error)"))
        self.assertEqual(report.status_resolved, 1)
        upsert.assert_not_called()

    def test_other_modes_never_rewrite_empty_rows(self):
        """종전 모드의 '빈손을 빈손으로 덮지 않는다' 계약은 그대로다."""
        report, _code, upsert, _rid = _run(
            dry_run=False, fetch_text=lambda url: ("", "scan-no-text"))
        self.assertEqual(report.status_resolved, 0)
        upsert.assert_not_called()

    def test_cli_accepts_mode(self):
        self.assertEqual(
            rec.build_arg_parser().parse_args(["--mode", "collection-capped"]).mode,
            rec.MODE_COLLECTION_CAPPED)


if __name__ == "__main__":
    unittest.main()


# ── [사각지대 모드 2026-08-01] 관찰 키가 없는 행 선택 ──────────────────────────
class MissingObservationsModeTest(unittest.TestCase):
    """`_to_item` 은 `fda483_text_status` 를 **본문이 전무할 때만** 기록한다
    (`if text_status and ... and not (excerpt or observations or body_full)`).
    그래서 "excerpt 는 있는데 관찰이 0" 인 행은 상태 키가 없고, 상태 키로 후보를 고르는
    기존 선택 조건(`is_ocr_unavailable_row`)의 **시야 밖**이었다 — 어떤 자동 복구도 영원히
    도달하지 못하는 사각지대(라이브 실측 2026-08-01: 417건).

    기본 모드는 종전 그대로 두고(회귀 0), 새 모드는 명시해야 켜진다."""

    def test_missing_observations_predicate(self):
        self.assertTrue(rec.is_missing_observations_row({"fda483_excerpt": "OBSERVATION 1 ..."}))
        self.assertFalse(rec.is_missing_observations_row({"fda_483_observations": [{"number": "1"}]}))
        # 본문이 아예 없는 행도 이 모드의 대상이다(관찰 키가 없으므로).
        self.assertTrue(rec.is_missing_observations_row({"fda483_text_status": "scan-no-text"}))

    def test_mode_dispatch_keeps_default_behaviour(self):
        ocr_row = {"fda483_text_status": f"{rec.OCR_UNAVAILABLE_PREFIX}:no tessdata"}
        excerpt_row = {"fda483_excerpt": "OBSERVATION 1 ..."}
        # 기본 모드: 종전 판정 그대로 — excerpt 행은 후보가 아니다.
        self.assertTrue(rec.is_recovery_candidate(ocr_row, rec.MODE_OCR_UNAVAILABLE))
        self.assertFalse(rec.is_recovery_candidate(excerpt_row, rec.MODE_OCR_UNAVAILABLE))
        # 새 모드: 관찰 키 부재만 본다.
        self.assertTrue(rec.is_recovery_candidate(excerpt_row, rec.MODE_MISSING_OBSERVATIONS))
        self.assertTrue(rec.is_recovery_candidate(ocr_row, rec.MODE_MISSING_OBSERVATIONS))

    def test_fetch_narrows_on_server_per_mode(self):
        seen = {}

        def fake_pages(base, key, table, **kw):
            seen.update(kw.get("extra_params") or {})
            return []

        with mock.patch.object(rec.fsb, "_fetch_all_pages", fake_pages):
            rec._fetch_raw_signals("https://x", "k", mode=rec.MODE_MISSING_OBSERVATIONS)
        self.assertEqual(seen.get("raw_json"), "not.like.*fda_483_observations*")

        seen.clear()
        with mock.patch.object(rec.fsb, "_fetch_all_pages", fake_pages):
            rec._fetch_raw_signals("https://x", "k", mode=rec.MODE_OCR_UNAVAILABLE)
        self.assertEqual(seen.get("raw_json"), f"like.*{rec.OCR_UNAVAILABLE_PREFIX}*")

    def test_report_records_which_candidate_set_was_scanned(self):
        """리포트만 보고 "무엇을 훑었는지" 알 수 있어야 한다(정직성 — 같은 스크립트가 두
        모집단을 처리하므로 모드를 안 적으면 0건이 어느 쪽의 0건인지 알 수 없다)."""
        report = rec.OcrRecoveryReport()
        self.assertEqual(report.select_mode, rec.MODE_OCR_UNAVAILABLE)
        self.assertIn("select_mode", asdict(report))

    def test_cli_exposes_mode_with_safe_default(self):
        parser = rec.build_arg_parser()
        self.assertEqual(parser.parse_args([]).mode, rec.MODE_OCR_UNAVAILABLE)
        self.assertEqual(
            parser.parse_args(["--mode", "missing-observations"]).mode,
            rec.MODE_MISSING_OBSERVATIONS,
        )
        with self.assertRaises(SystemExit):
            parser.parse_args(["--mode", "nope"])


# ── [2026-10-28] missing-observations 모드가 확정 사유를 표식으로 남긴다 ──────────────
class MissingObservationsMarkerTest(unittest.TestCase):
    """상태 키·발췌·관찰이 모두 없는 행 25건(2016~2026)이 이 모드로 읽히고도 아무것도 쓰이지
    않아 매 감사마다 원인 미상으로 남았다. 다시 읽은 결과가 확정 사유(본문은 있으나 추출 0 ·
    404 · 범위 밖)면 그 사유를 적어, 다음 감사에서 원인별로 갈리게 한다."""

    _NO_ANCHOR = "We are enclosing a copy of the Establishment Inspection Report for your firm."

    def _bare_row(self, rid, **extra):
        import json
        payload = _raw_payload("", "")
        self.assertNotIn("fda483_text_status", payload)
        payload.update(extra)
        return {"raw_signal_id": rid, "document_id": "fda483-555001",
                "collected_at": "2026-07-28T01:02:03Z",
                "raw_json": json.dumps(payload, ensure_ascii=False, sort_keys=True)}

    def _run_mode(self, fetch_text, **row_extra):
        import findings_store
        rid = findings_store.raw_signal_from_intake_item(
            rec._rebuild_item(_nrow(), "", ""), collected_at="")["raw_signal_id"]
        return _run(mode=rec.MODE_MISSING_OBSERVATIONS, dry_run=False,
                    rows=[self._bare_row(rid, **row_extra)], fetch_text=fetch_text)

    def test_text_without_extraction_records_pdf_status(self):
        report, _code, upsert, rid = self._run_mode(lambda url: (self._NO_ANCHOR, "pdf-ok"))
        self.assertEqual(report.text_without_extraction, 1)
        self.assertEqual(report.status_resolved, 1)
        record = upsert.call_args[0][2]
        self.assertEqual(record["raw_signal_id"], rid)
        self.assertEqual(rec._json_object(record["raw_json"])["fda483_text_status"], "pdf-ok")

    def test_definitive_empty_records_reason_on_bare_row(self):
        report, _code, upsert, _rid = self._run_mode(
            lambda url: ("", "fetch-fail:HTTP GET final failure: (404 Client Error)"))
        self.assertEqual(report.status_resolved, 1)
        self.assertTrue(rec._json_object(upsert.call_args[0][2]["raw_json"])
                        ["fda483_text_status"].startswith("fetch-fail"))

    def test_row_with_excerpt_is_never_overwritten_by_empty_result(self):
        """발췌가 있는 행에 빈 본문 재구성을 쓰면 발췌가 지워진다 — 쓰지 않는다."""
        report, _code, upsert, _rid = self._run_mode(
            lambda url: ("", "fetch-fail:HTTP GET final failure: (404 Client Error)"),
            fda483_excerpt="OBSERVATION 1 The cleaning procedure was not followed.")
        self.assertEqual(report.status_resolved, 0)
        upsert.assert_not_called()

    def test_gate_drop_records_out_of_scope(self):
        real_rebuild = rec._rebuild_item

        def rebuild(nrow, text, status):
            return None if text else real_rebuild(nrow, text, status)
        import findings_store
        rid = findings_store.raw_signal_from_intake_item(
            real_rebuild(_nrow(), "", ""), collected_at="")["raw_signal_id"]
        report, _code, upsert, _ = _run(
            mode=rec.MODE_MISSING_OBSERVATIONS, dry_run=False, rows=[self._bare_row(rid)],
            rebuild_item=rebuild)
        self.assertEqual(report.gate_dropped, 1)
        self.assertEqual(report.status_resolved, 1)
        self.assertEqual(rec._json_object(upsert.call_args[0][2]["raw_json"])
                         ["fda483_text_status"], rec.OUT_OF_SCOPE_STATUS)


class DumpTextTest(unittest.TestCase):
    """[11-12] 진단 전용 원문 덤프 — DB 없이 PDF URL 을 만들어 판독 전문만 남긴다."""

    def test_dump_writes_text_per_doc_without_db(self):
        import tempfile
        calls = []

        def fake_fetch(url):
            calls.append(url)
            return ("OBSERVATION 1 text", "pdf-ok-ocr") if url.endswith("195467/download") else ("", "scan-no-text")

        with tempfile.TemporaryDirectory() as d:
            rows = rec.dump_texts(["fda483-195467", "195468", "bogus"], d, fetch_text=fake_fetch)
            self.assertEqual(calls, ["https://www.fda.gov/media/195467/download",
                                     "https://www.fda.gov/media/195468/download"])
            with open(os.path.join(d, "fda483-195467.txt"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "OBSERVATION 1 text")
            self.assertFalse(os.path.exists(os.path.join(d, "fda483-195468.txt")))   # 빈 본문은 파일 없음
        self.assertEqual([r["status"] for r in rows], ["pdf-ok-ocr", "scan-no-text", "bad_doc_id"])

    def test_main_dump_needs_doc_ids_and_no_credentials(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(rec, "_fetch_text", return_value=("T", "pdf-ok")), \
                mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""}):
            self.assertEqual(rec.main(["--dump-text-dir", d]), 2)
            self.assertEqual(rec.main(["--dump-text-dir", d, "--doc-ids", "fda483-1234"]), 0)
            self.assertTrue(os.path.exists(os.path.join(d, "fda483-1234.txt")))



def _mixed_pdf_bytes() -> bytes:
    """1쪽 = 글자층(관찰 1), 2쪽 = 글자 없는 이미지(스캔 쪽) — 195468 꼴."""
    import fitz  # type: ignore[import-not-found]
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "OBSERVATION 1 Procedures designed to prevent microbiological "
                             "contamination are not established. Specifically, the firm did not.")
    p2 = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    pix.clear_with(200)
    p2.insert_image(fitz.Rect(72, 72, 300, 300), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


class MixedPdfTest(unittest.TestCase):
    """[11-12] 섞인 PDF — 문서 전체로는 글자가 충분해도 이미지 쪽은 OCR 해야 한다."""

    def test_count_unread_image_pages(self):
        self.assertEqual(f.count_unread_image_pages(_mixed_pdf_bytes()), 1)
        self.assertEqual(f.count_unread_image_pages(b"not a pdf"), 0)

    def test_read_path_ocrs_mixed_pdf_and_never_shrinks(self):
        data = _mixed_pdf_bytes()
        layer = ("OBSERVATION 1 Procedures designed to prevent microbiological contamination are not "
                 "established. Specifically, the firm did not. " * 3)

        def extract(_data, max_chars):
            return layer, "pdf-ok"

        longer = layer + " OBSERVATION 2 Second observation from the scanned page."
        with mock.patch.object(f, "_ocr_enabled", return_value=True), \
                mock.patch.object(f, "_needs_ocr", return_value=False), \
                mock.patch.object(f, "_ocr_483_pdf_text", return_value=(longer, "pdf-ok-ocr")) as ocr:
            text, status = f._read_fda483_pdf_text(data, 200000, extract)
        ocr.assert_called_once()
        self.assertEqual((text, status), (longer, "pdf-ok-ocr"))
        with mock.patch.object(f, "_ocr_enabled", return_value=True), \
                mock.patch.object(f, "_needs_ocr", return_value=False), \
                mock.patch.object(f, "_ocr_483_pdf_text", return_value=("short", "pdf-ok-ocr")):
            self.assertEqual(f._read_fda483_pdf_text(data, 200000, extract), (layer, "pdf-ok"))  # 축소 금지


class NamedModeTest(unittest.TestCase):
    """[11-12] mode=named — 지목한 문서만, 관찰이 늘 때만 교체."""

    def test_named_candidate_requires_explicit(self):
        self.assertTrue(rec.is_recovery_candidate({"fda_483_observations": [{}]}, rec.MODE_NAMED, explicit=True))
        self.assertFalse(rec.is_recovery_candidate({}, rec.MODE_NAMED, explicit=False))

    def test_named_without_doc_ids_is_refused(self):
        report, code = rec.run(base_url="https://x.supabase.co", service_key="k", dry_run=True,
                               mode=rec.MODE_NAMED, fetch_raw_signals=lambda *a, **k: [])
        self.assertEqual(code, 2)
        self.assertTrue(report.errors)

    def test_named_fetch_passes_doc_ids(self):
        seen = {}

        def fetch(base, key, **kw):
            seen.update(kw)
            return []

        rec.run(base_url="https://x.supabase.co", service_key="k", dry_run=True, mode=rec.MODE_NAMED,
                doc_ids=["fda483-195468"], fetch_raw_signals=fetch)
        self.assertEqual(seen.get("doc_ids"), ["fda483-195468"])
        self.assertEqual(seen.get("mode"), rec.MODE_NAMED)


class NamedGuardAndScanTest(unittest.TestCase):
    """[11-12] 대량 named 재추출 전 가드 · 섞인 PDF 진단 분류."""

    def test_keeps_old_deficiencies(self):
        old = [{"deficiency": "Procedures designed to prevent microbiological contamination are not established..."}]
        same = [{"deficiency": "Procedures designed to prevent microbiological contamination are not established, "
                               "written and followed."}, {"deficiency": "Employees lack training."}]
        changed = [{"deficiency": "Procedures to prevent contamination were missing."}, {"deficiency": "X."}]
        self.assertTrue(rec._keeps_old_deficiencies(old, same))
        self.assertFalse(rec._keeps_old_deficiencies(old, changed))
        self.assertTrue(rec._keeps_old_deficiencies([], changed))

    def test_scan_reports_gap_split(self):
        data = _mixed_pdf_bytes()
        rows = [{"document_id": "fda483-1", "raw_json": {"pdf_url": "u", "fda_483_observations": [{}]}}]
        out = rec.scan_mixed_pdfs(rows, delay_seconds=0, fetch_bytes=lambda u: data, sleep_fn=lambda s: None)
        self.assertEqual(out["mixed_docs"], 1)
        self.assertEqual(out["gap_docs"] + out["mixed_already_ocr_path"], 1)
        for k in ("gap_unread_pages", "gap_with_stored_obs", "gap_without_stored_obs"):
            self.assertIn(k, out)


_P85637_OBS6 = ("Sterility testing was only performed at 106 days. OBSERVATION6 Each batch of dmg product required to be free of objectionable microorganisms is not tested through appropriate laboratory testing, Specifically, your firm does not conduct routine sterility or endotoxin testing for all injectable drug products currently produced. You stated that the stock solutions are routinely tested for sterility and endotoxins but that finished product is not Toutinely tested unless(J units or more are filed or fi re. Stock solutions are used to fill finished product.. For example, [redacted] lot #02262013M8of Fentanyl (NICU Syringe) 10mcg/mL Injectable was not tested for sterility [redacted] fot #03182013S18 afDMPS (Dimercaptopropanesulfonic Acid) (Preservative Free) 5% Injectable was not tested for sterflity or endotoxins [redacted] fot 403112013823 of Sodium Phenylbutyrate 200 mg/mL Injectable was not tested for sterility or endotoxins e fot #03132013832 of Glutathione/Adenosine Triphosphate 100mg/img/ml. Injectable was not tested for sterility or endotoxins e tot #0315201388 ofHydrogen Peroxide (Preservative Free) 3.75% Injectable was not tested for sterility or endotoxins ¢ fot #03152013824 ofMethylcobalamin (Preservative Free) 12.5mg/mL, Injectable was not tested for sterility or endotoxins ¢ lot 40318201313 ofMethylcobalamin/Folic Acid (Preservative Free) 1mg/l0mg/mL Injectable wasnot tested for sterility or endotoxins")
_P85637_OBS7_DEF = "Testing and release of drug product for distribution do not include appropriate laboratory determination of satisfactory conformance to the final specifications and identity and strength of each active ingredient prior to release."
_P85637_OBS7_DETAIL = "Specifically, your firm does not conduct routine potency testing for all injectable drug products currently produced. You stated that the stock solutions are routinely tested for potency but that finished preduct is not routinely tested unless [jj units or more are filled or J Stock solutions are used to fill finished product. For example, Ela CYPE TS)"
# 3판에서 사라진 블록(머리글 + 역가 미시험 로트 6줄, 서명자 실명은 [signature] 로 가림)
_P85637_LOST = ("GKSNATURE OATE ISSUED [signature], lot 403182013518 of DMPS (Dimercaptopropanesulfonic Acid) (Preservative Free) 5% Injectable was not tested for potency [redacted] Jot 403112013823 ofSodium Phenylbutyrate 200 mg/mL Injectable was not tested for potency [redacted] lot 49313213832 of Ghitathione/Adenosine Triphosphate 100mg/I mg/mL Injectable was not tested for potency [redacted] lot #03152013S8 ofHydrogen Peroxide (Preservative Free) 3.75% Injectable was not tested for potency [redacted] lot #03152013S24 ofMethyicobalamin (Preservative Free} 12.5mg/mL Injectable was not tested for potency. [redacted] Jot #03182013S13 ofMethylcobalamit/Folic Acid (Preservative Free) img/10mg/mL Injectable was not tested for potency")
_P85637_META = {"firm": "Specialty Compounding Pharmacy", "establishment_type": "Producer of Sterile Drug Products", "site_state": "Texas", "country": "United States", "product_type": "Producer of Sterile Drug Products"}
_P85637_DEF5 = "There is no written testing program designed to assess the stability characteristics of drug products."
# 2판: 관찰 5 상세에 관찰 6·7 본문과 손실 블록이 병합돼 있었다
OLD_85637 = {**_P85637_META, "fda_483_observations": [{"number": "5", "deficiency": _P85637_DEF5, "detail": _P85637_OBS6 + " OBSERVATION 7. " + _P85637_OBS7_DEF + " " + _P85637_OBS7_DETAIL + " " + _P85637_LOST}]}
# 3판: 관찰 7 이 분리됐지만 상세가 'For example, Ela CYPE TS)' 에서 끝나 손실 블록이 어디에도 없다
NEW_85637 = {**_P85637_META, "fda_483_observations": [{"number": "5", "deficiency": _P85637_DEF5, "detail": _P85637_OBS6}, {"number": "7", "deficiency": _P85637_OBS7_DEF, "detail": _P85637_OBS7_DETAIL}]}
# 4판(현행, 수동 복원): 로트 6줄을 관찰 7 상세 뒤에 되붙였다(머리글은 뺌)
_P85637_LOT_LINES = _P85637_LOST.split("[signature], ", 1)[1]
V4_85637 = {**_P85637_META, "fda_483_observations": [{"number": "5", "deficiency": _P85637_DEF5, "detail": _P85637_OBS6}, {"number": "7", "deficiency": _P85637_OBS7_DEF, "detail": _P85637_OBS7_DETAIL + " " + _P85637_LOT_LINES}]}

_P85401_META = {"firm": "Central Admixture Pharmacy Services, Inc. (CAPS)", "establishment_type": "Sterile Drug Manufacturer", "site_state": "Alabama", "country": "United States", "product_type": "Sterile Drug Manufacturer"}
_P85401_DEF8 = "Equipment used in the manufacture, processing, packing or holding of drug products is not of appropriate design to facilitate operations for its intended use."
_P85401_NEW8 = "Specifically, tape was observed holding up clipboards on the outside sash of the ISO 5 TPN hood in the room identified by the firm as aredfil"
_P85401_DEF9 = "There is no written testing program designed to assess the stability characteristics of drug products."
_P85401_NEW9 = "Specifically, the firm has no written stability program which requires ongoing stability studies to show that the aseptically processed sterile drug products which they distribute, continue to meet all relevant chemical and sterility specifications all the way to product expiry, or beyond use dates (BUD)."
_P85401_HEADER = "Central Admixture Pharmacy Services, 211 Summit Parkway #122 Inc. City, STATE, 2 CODE, COUNTRYS TABS INSPECTED Homewood, AL 35209 Producer of Sterile Drug Products"
# 2판: 관찰 8 상세(730자)에 관찰 9 와 페이지 머리글(주소)이 붙어 있었다 → 3판은 8(141자)·9 로 나뉘고 머리글은 버려졌다 — 정상
OLD_85401 = {**_P85401_META, "fda_483_observations": [{"number": "8", "deficiency": _P85401_DEF8, "detail": _P85401_NEW8 + " OBSERVATION 9. " + _P85401_DEF9 + " " + _P85401_NEW9 + " " + _P85401_HEADER}]}
NEW_85401 = {**_P85401_META, "fda_483_observations": [{"number": "8", "deficiency": _P85401_DEF8, "detail": _P85401_NEW8}, {"number": "9", "deficiency": _P85401_DEF9, "detail": _P85401_NEW9}]}


class NamedTextLossGuardTest(unittest.TestCase):

    def test_85637_lost_block_is_detected(self):
        loss = rec.text_loss(OLD_85637, NEW_85637)
        self.assertGreaterEqual(loss["lost_chars"], rec.NAMED_TEXT_LOSS_MIN_CHARS)
        self.assertIn("potency", loss["sample"])

    def test_85401_split_and_header_drop_is_allowed(self):
        loss = rec.text_loss(OLD_85401, NEW_85401)
        self.assertLess(loss["lost_chars"], rec.NAMED_TEXT_LOSS_MIN_CHARS)

    def test_identical_is_zero(self):
        self.assertEqual(rec.text_loss(OLD_85637, OLD_85637)["lost_chars"], 0)

    def test_footer_only_reduction_is_allowed(self):
        footer = ("EMPLOYEE(S) SIGNATURE DATE ISSUED SEE REVERSE OF THIS PAGE FORM FDA 483 "
                  "(09/08) PREVIOUS EDITION OBSOLETE INSPECTIONAL OBSERVATIONS PAGE 2 OF 5 PAGES")
        observations = [
            {"number": str(i),
             "deficiency": f"Quality procedure {i} was not adequately established and followed.",
             "detail": f"The documented control process for operation {i} was reviewed by investigators."}
            for i in range(1, 4)
        ]
        old = {"fda_483_observations": [
            {**observation, "detail": observation["detail"] + " " + footer}
            for observation in observations
        ]}
        new = {"fda_483_observations": observations}
        self.assertLess(rec.text_loss(old, new)["lost_chars"], rec.NAMED_TEXT_LOSS_MIN_CHARS)

    def test_duplicate_cleanup_is_allowed(self):
        detail2 = " ".join(f"duplicatedqualityword{i}" for i in range(30))
        deficiency2 = "Laboratory controls did not include complete documented review procedures."
        duplicate = deficiency2 + " " + detail2
        old = {"fda_483_observations": [
            {"number": "1", "deficiency": "Written controls were not followed during production.",
             "detail": "Investigators reviewed the complete production process and records. " + duplicate},
            {"number": "2", "deficiency": deficiency2, "detail": detail2},
        ]}
        new = {"fda_483_observations": [
            {"number": "1", "deficiency": "Written controls were not followed during production.",
             "detail": "Investigators reviewed the complete production process and records."},
            {"number": "2", "deficiency": deficiency2, "detail": detail2},
        ]}
        self.assertLess(rec.text_loss(old, new)["lost_chars"], rec.NAMED_TEXT_LOSS_MIN_CHARS)

    def test_whole_observation_body_dropped_is_detected(self):
        body = " ".join(f"uniquecompoundword{i}" for i in range(35))
        old = {"fda_483_observations": [
            {"number": "1", "deficiency": "Production records were incomplete.",
             "detail": "The first observation remains unchanged in the replacement document."},
            {"number": "2", "deficiency": "Laboratory review procedures were deficient.",
             "detail": body},
        ]}
        new = {"fda_483_observations": [
            {"number": "1", "deficiency": "Production records were incomplete.",
             "detail": "The first observation remains unchanged in the replacement document."},
            {"number": "2", "deficiency": "Laboratory review procedures were deficient.",
             "detail": ""},
        ]}
        self.assertGreaterEqual(rec.text_loss(old, new)["lost_chars"],
                                rec.NAMED_TEXT_LOSS_MIN_CHARS)

    def _run_named(self, old_observations, new_observations, *, dry_run):
        import findings_store
        import json

        base_item = rec._rebuild_item(_nrow(), "", "")
        record = findings_store.raw_signal_from_intake_item(base_item, collected_at="")
        rid = record["raw_signal_id"]
        pdf_url = "https://example.test/fda483.pdf"
        old_raw = {**base_item.raw_payload, "pdf_url": pdf_url,
                   "fda_483_observations": old_observations}
        new_raw = {**base_item.raw_payload, "pdf_url": pdf_url,
                   "fda_483_observations": new_observations}
        rebuilt = rec._rebuild_item(_nrow(), "", "")
        rebuilt.raw_payload = new_raw
        row = {
            "raw_signal_id": rid,
            "document_id": base_item.document_id,
            "collected_at": "2026-07-28T01:02:03Z",
            "raw_json": json.dumps(old_raw, ensure_ascii=False, sort_keys=True),
        }
        return _run(
            mode=rec.MODE_NAMED,
            doc_ids=[base_item.document_id],
            dry_run=dry_run,
            rows=[row],
            fetch_text=lambda _url: ("stub text", "pdf-ok"),
            rebuild_item=lambda _nrow, _text, _status: rebuilt,
        )

    def test_run_named_blocks_text_loss_dry_and_apply(self):
        shared = "The unchanged process description remains present throughout the replacement document."
        lost = " ".join(f"uniquelostword{i}" for i in range(35))
        old = [{"number": "1", "deficiency": "Written procedures were not adequately followed.",
                "detail": shared + " " + lost}]
        new = [
            {"number": "1", "deficiency": "Written procedures were not adequately followed.",
             "detail": shared},
            {"number": "2", "deficiency": "Equipment controls were not adequately documented.",
             "detail": "A separate observation was recovered from the final scanned page."},
        ]
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                report, code, upsert, _rid = self._run_named(old, new, dry_run=dry_run)
                self.assertEqual(report.failure_reasons["named_would_drop_text"], 1)
                self.assertEqual(report.named_no_gain, 1)
                self.assertEqual(report.recovered, 0)
                self.assertEqual(len(report.named_text_loss), 1)
                self.assertEqual(report.named_text_loss[0]["document_id"], "fda483-555001")
                upsert.assert_not_called()
                self.assertEqual(code, 0)

    def test_run_named_allows_split_without_loss(self):
        detail1 = "The documented production controls remained unchanged in the replacement document."
        deficiency2 = "Equipment controls were not adequately documented."
        detail2 = "A separate observation was recovered from the final scanned page."
        old = [{"number": "1", "deficiency": "Written procedures were not adequately followed.",
                "detail": detail1 + " " + deficiency2 + " " + detail2}]
        new = [
            {"number": "1", "deficiency": "Written procedures were not adequately followed.",
             "detail": detail1},
            {"number": "2", "deficiency": deficiency2, "detail": detail2},
        ]
        report, code, upsert, _rid = self._run_named(old, new, dry_run=False)
        self.assertNotIn("named_would_drop_text", report.failure_reasons)
        self.assertEqual(report.recovered, 1)
        upsert.assert_called_once()
        self.assertEqual(code, 0)

    # [11-13] 4판(현행, 수동 복원 — 관찰 7 상세 1,043자·'tested for potency' 7회) 보호: 다시 읽은 결과가
    # 3판처럼 관찰 7 이 354자에서 끊기면 관찰 수가 늘어도 named 교체를 막아야 한다(85637 재실행 차단 보장).
    def test_85637_v4_to_v3_shape_is_blocked(self):
        loss = rec.text_loss(V4_85637, NEW_85637)
        self.assertGreaterEqual(loss["lost_chars"], rec.NAMED_TEXT_LOSS_MIN_CHARS)
        self.assertIn("potency", loss["sample"])

    def test_85637_v2_to_v4_restore_is_not_loss(self):
        # 4판은 2판의 손실 블록을 관찰 7 로 옮긴 것 — 머리글 낱말만 빠졌다.
        self.assertLess(rec.text_loss(OLD_85637, V4_85637)["lost_chars"], rec.NAMED_TEXT_LOSS_MIN_CHARS)

    def test_run_named_rerun_of_85637_v4_is_blocked(self):
        extra = {"number": "8", "deficiency": "Aseptic processing areas are deficient regarding the system for "
                 "cleaning and disinfecting the room and equipment to produce aseptic conditions.",
                 "detail": "Specifically, sporicidal agents are not used in the cleanroom."}
        old = V4_85637["fda_483_observations"]
        new = NEW_85637["fda_483_observations"] + [extra]      # 관찰 +1·표제 유지·관찰 7 은 354자 판
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                report, code, upsert, _rid = self._run_named(old, new, dry_run=dry_run)
                self.assertNotIn("named_would_drop_deficiency", report.failure_reasons)
                self.assertEqual(report.failure_reasons.get("named_would_drop_text"), 1)
                self.assertEqual(report.recovered, 0)
                upsert.assert_not_called()
                self.assertEqual(code, 0)
