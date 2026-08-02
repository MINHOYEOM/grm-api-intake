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

    def test_recovery_opts_into_merge_duplicates(self) -> None:
        with mock.patch("findings_supabase_append.requests.post") as post:
            post.return_value = mock.MagicMock(status_code=200, json=lambda: [])
            rec._upsert_raw_signal("https://x.supabase.co", "k", {"raw_signal_id": "r"})
        self.assertIn("resolution=merge-duplicates", post.call_args[1]["headers"]["Prefer"])


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
