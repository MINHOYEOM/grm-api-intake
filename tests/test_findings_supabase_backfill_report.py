#!/usr/bin/env python3
"""M12 `--report` 문서별 dry 차이 보고서 시험 (2026-11-13).

모든 HTTP 는 가짜다. 고정값은 실제 문서의 최소 조각이다:
  - Henogen fda483-195517 — 저장 raw 의 관찰이 서식 안내문 1개뿐이고 본문에 'OBSERVATION n' 표지가 없다
    (DB findings 1행 = "Toassist firms inspected…", scope ok·accepted → 공개 중인 잡문).
  - golden fda_483_observations (BPI Labs, 관찰 2) — 새 행 / 건너뜀 / 충돌 예측.

지키는 것:
  ① `--report` 를 안 주면 호출 순서·stdout·exit code 가 종전과 같다(보고서용 조회·RPC 0회).
  ② `--report --dry-run` 이면 쓰기 호출 0(findings POST 0·_append_findings_batch 0).
  ③ 유니크 인덱스 (raw_signal_id, md5(finding_text)) 충돌을 예측해 표시한다.
  ④ 잡문 기존 행은 '숨김 필요(별도 조치)' 로만 표시하고, 원문 자체에 관찰이 없으면 no_observation_in_source.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from unittest import mock

import findings_extractors as extractors
import findings_supabase_backfill as backfill
import grm_findings as gf


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"
GOLDEN = os.path.join(os.path.dirname(__file__), "golden")

_HENOGEN_NOTICE = ("Toassist firms inspected in complying with the Acts and regulations enforced by the "
                   "Food and Drug Administration.")


def _golden_483() -> dict:
    with open(os.path.join(GOLDEN, "fda_483_observations.input.json"), encoding="utf-8") as f:
        fx = json.load(f)
    return gf.raw_signal_from_row(fx["row"], fx["raw"])


def _henogen() -> dict:
    row = {
        "date": "2026-11-01", "document_id": "fda483-195517", "firm": "Henogen SRL",
        "headline": "[FDA 483] Henogen SRL", "language": "EN", "modality": "Biologic",
        "official_url": "https://www.fda.gov/media/195517/download", "signal_tier": "Tier 3",
        "site_country": "Belgium", "source": "FDA 483", "type_or_class": "483",
    }
    raw = {
        "establishment_type": "Viral Vector Manufacturer",
        "firm": "Henogen SRL",
        "fda483_body_full": ("DEPARTMENT OF HEALTH AND HUMAN SERVICES FOOD AND DRUG ADMINISTRATION ... "
                             "The observations of objectionable conditions and practices listed on the front "
                             "of this form are reported: 1. Pursuant to Section 704(b) ... 2. " + _HENOGEN_NOTICE),
        "fda_483_observations": [{
            "number": "2",
            "deficiency": _HENOGEN_NOTICE,
            "detail": ("Section 704(b) of the Federal Food, Drug, and Cosmetic Act (21 USC 374(b)) provides: "
                       "\"Upon completion of any such inspection of a factory, warehouse, consulting laboratory, "
                       "or other establishment ...\""),
        }],
    }
    return gf.raw_signal_from_row(row, raw)


def _db_row(finding: dict, *, scope: str = "ok", review: str = "accepted", **over) -> dict:
    row = {
        "finding_id": finding["finding_id"], "raw_signal_id": finding["raw_signal_id"],
        "source": finding["source"], "finding_text": finding["finding_text"],
        "scope_status": scope, "review_status": review,
    }
    row.update(over)
    return row


def _scope_ok(_rs, _f) -> str:
    return "ok"


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="m12report-")
        self.path = os.path.join(self.dir, "sub", "report.md")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _read(self) -> str:
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()


class DefaultUnchangedTest(_Tmp):
    """① --report 없으면 종전과 100% 같다."""

    def _run_main(self, argv: list[str], raw_signals: list[dict], post_side_effect) -> tuple[int, str, list[str], mock.Mock]:
        order: list[str] = []
        env = dict(os.environ, SUPABASE_URL=_BASE_URL, SUPABASE_SERVICE_ROLE_KEY=_SERVICE_KEY)

        def _raw(*_a, **_k):
            order.append("fetch_raw_signals")
            return raw_signals

        def _existing(*_a, **_k):
            order.append("fetch_existing_finding_raw_ids")
            return {rs["raw_signal_id"] for rs in raw_signals}

        def _post(url, *_a, **_k):
            # requests 모듈은 두 파일이 공유한다 — 보고서 RPC 가 있었다면 여기 'POST rpc/…' 로 찍힌다.
            order.append("POST " + url.split("/rest/v1/")[-1])
            return post_side_effect()

        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("findings_supabase_backfill.fetch_raw_signals", side_effect=_raw), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids", side_effect=_existing), \
             mock.patch("findings_supabase_backfill.fetch_document_findings") as fdf, \
             mock.patch("findings_supabase_append.requests.post", side_effect=_post), \
             redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = backfill.main(argv)
        return code, out.getvalue(), order, fdf

    def test_document_ids_without_report_is_unchanged(self) -> None:
        rs = _golden_483()

        class _Resp:
            status_code = 201
            headers: dict = {}

            def json(self):
                return [{"finding_id": "f1"}]

        code, out, order, fdf = self._run_main(["--document-ids", rs["document_id"]], [rs], _Resp)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["fetch_raw_signals", "fetch_existing_finding_raw_ids", "POST findings"])
        fdf.assert_not_called()
        summary = json.loads(out)
        self.assertEqual(sorted(summary), sorted(asdict(backfill.BackfillReport())))
        self.assertEqual(summary["findings_inserted"], 1)
        self.assertFalse(os.path.exists(self.path))

    def test_report_does_not_change_stdout_order_or_exit_code(self) -> None:
        rs = _golden_483()

        class _Resp:
            status_code = 201
            headers: dict = {}

            def json(self):
                return [{"finding_id": "f1"}]

        plain = self._run_main(["--document-ids", rs["document_id"]], [rs], _Resp)
        with mock.patch("findings_supabase_backfill._write_document_report") as wdr:
            with_report = self._run_main(["--document-ids", rs["document_id"], "--report", self.path,
                                          "--report-scope", "approx"], [rs], _Resp)
        wdr.assert_called_once()
        self.assertEqual(plain[0], with_report[0])
        self.assertEqual(plain[1], with_report[1])           # stdout JSON 요약 그대로
        self.assertEqual(plain[2], with_report[2])           # 반영 호출 순서 그대로(dry 로 바뀌지 않음)

    def test_run_backfill_signature_default_is_not_dry_and_no_report(self) -> None:
        import inspect
        params = inspect.signature(backfill.run_backfill).parameters
        self.assertIs(params["dry_run"].default, inspect.Parameter.empty)
        self.assertIsNone(params["report_path"].default)
        parser_defaults = {}
        with mock.patch("findings_supabase_backfill.run_backfill",
                        side_effect=lambda *a, **k: parser_defaults.update(k) or backfill.BackfillReport()), \
             mock.patch.dict(os.environ, dict(os.environ, SUPABASE_URL=_BASE_URL,
                                              SUPABASE_SERVICE_ROLE_KEY=_SERVICE_KEY), clear=True), \
             redirect_stdout(io.StringIO()):
            backfill.main(["--document-ids", "fda483-1"])
        self.assertFalse(parser_defaults["dry_run"])
        self.assertNotIn("report_path", parser_defaults)

    def test_report_requires_document_ids(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            backfill.main(["--report", self.path])
        self.assertEqual(ctx.exception.code, 2)


class DryReportNoWritesTest(_Tmp):
    """② dry + report = 쓰기 0, 보고서는 마크다운 표."""

    def test_dry_report_writes_file_and_never_posts(self) -> None:
        rs = _golden_483()
        found = extractors.findings_from_raw_signal(rs)
        with mock.patch("findings_supabase_backfill.fetch_raw_signals", return_value=[rs]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids", return_value=set()), \
             mock.patch("findings_supabase_backfill.fetch_document_findings",
                        return_value=[_db_row(found[0])]) as fdf, \
             mock.patch("findings_supabase_append.requests.post") as post, \
             mock.patch("findings_supabase_append._append_findings_batch") as append, \
             redirect_stderr(io.StringIO()):
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=True,
                                           document_ids=[rs["document_id"]], report_path=self.path,
                                           report_scope_fn=_scope_ok)
        post.assert_not_called()
        append.assert_not_called()
        fdf.assert_called_once()
        self.assertEqual(report.findings_inserted, 0)
        text = self._read()
        self.assertIn("| document_id | 상태 | 기존 행 | 새로 뽑은 행 | 새로 생김 | 건너뜀 | 충돌 예측 | 숨김 필요 |", text)
        self.assertIn("dry-run — 쓰기 없음", text)
        self.assertIn(f"| {rs['document_id']} | `new_rows` | 1 | 2 | 1 | 1 | 0 | 0 |", text)
        self.assertNotIn(_SERVICE_KEY, text)

    def test_execute_with_report_still_appends_once(self) -> None:
        rs = _golden_483()
        with mock.patch("findings_supabase_backfill.fetch_raw_signals", return_value=[rs]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids", return_value=set()), \
             mock.patch("findings_supabase_backfill.fetch_document_findings", return_value=[]), \
             mock.patch("findings_supabase_append._append_findings_batch",
                        return_value=(2, 0, False, [])) as append, \
             redirect_stderr(io.StringIO()):
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=False,
                                           document_ids=[rs["document_id"]], report_path=self.path,
                                           report_scope_fn=_scope_ok)
        append.assert_called_once()
        self.assertEqual(report.findings_inserted, 2)
        self.assertIn("반영 — 이 보고서는 append 직전 상태", self._read())

    def test_report_failure_does_not_block_or_leak(self) -> None:
        rs = _golden_483()
        err = io.StringIO()
        with mock.patch("findings_supabase_backfill.fetch_raw_signals", return_value=[rs]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids", return_value=set()), \
             mock.patch("findings_supabase_backfill.fetch_document_findings",
                        side_effect=RuntimeError(f"boom apikey={_SERVICE_KEY}")), \
             mock.patch("findings_supabase_append._append_findings_batch",
                        return_value=(2, 0, False, [])) as append, \
             redirect_stderr(err):
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=False,
                                           document_ids=[rs["document_id"]], report_path=self.path)
        append.assert_called_once()
        self.assertEqual(report.errors, ())
        self.assertIn("report failed (RuntimeError)", err.getvalue())
        self.assertNotIn(_SERVICE_KEY, err.getvalue())


class DiffStatusTest(unittest.TestCase):
    """③④ 처리 예측·상태 판정 (순수 함수)."""

    def test_conflict_on_same_text_different_finding_id(self) -> None:
        rs = _golden_483()
        found = extractors.findings_from_raw_signal(rs)
        # 옛 추출기가 같은 표제를 다른 ordinal 로 넣어 finding_id 만 다른 행 — md5 유니크 인덱스 충돌.
        old = _db_row(found[0], finding_id="finding-old000000000000000000")
        diffs = backfill.build_document_diffs([rs["document_id"]], [rs], [old], scope_fn=_scope_ok)
        d = diffs[0]
        self.assertEqual(d["status"], backfill.STATUS_CONFLICT)
        actions = [x["action"] for x in d["extracted"]]
        self.assertEqual(actions, [backfill.ACTION_CONFLICT_EXISTING, backfill.ACTION_NEW])
        self.assertEqual(d["extracted"][0]["conflicts_with"], "finding-old000000000000000000")
        text = backfill.render_document_report(diffs, dry_run=True, scope_label="x")
        self.assertIn("충돌 예측 — 기존 행과 표제 같음", text)
        self.assertIn("| `conflict` | 1 | 2 | 1 | 0 | 1 | 0 |", text)

    def test_conflict_within_batch(self) -> None:
        rs = _golden_483()
        found = extractors.findings_from_raw_signal(rs)
        twin = dict(found[1], finding_id="finding-twin", finding_text=found[0]["finding_text"])
        with mock.patch("findings_extractors.findings_from_raw_signal", return_value=[found[0], twin]):
            d = backfill.build_document_diffs([rs["document_id"]], [rs], [], scope_fn=_scope_ok)[0]
        self.assertEqual([x["action"] for x in d["extracted"]],
                         [backfill.ACTION_NEW, backfill.ACTION_CONFLICT_BATCH])
        self.assertEqual(d["status"], backfill.STATUS_CONFLICT)

    def test_all_present(self) -> None:
        rs = _golden_483()
        rows = [_db_row(f) for f in extractors.findings_from_raw_signal(rs)]
        d = backfill.build_document_diffs([rs["document_id"]], [rs], rows, scope_fn=_scope_ok)[0]
        self.assertEqual(d["status"], backfill.STATUS_ALL_PRESENT)
        self.assertTrue(all(x["action"] == backfill.ACTION_SKIP_EXISTING for x in d["extracted"]))
        self.assertEqual([x["scope_preview"] for x in d["extracted"]], ["", ""])  # 건너뛸 행은 미리보기 안 함

    def test_not_found(self) -> None:
        d = backfill.build_document_diffs(["fda483-000000"], [], [], scope_fn=_scope_ok)[0]
        self.assertEqual(d["status"], backfill.STATUS_NOT_FOUND)
        self.assertIn("## fda483-000000 — `not_found`",
                      backfill.render_document_report([d], dry_run=True, scope_label="x"))


class HenogenNoObservationTest(unittest.TestCase):
    """Henogen fda483-195517 고정값 — 원문 자체에 관찰 없음 + 공개 중인 서식 안내문 = 숨김 필요."""

    def test_henogen_shape(self) -> None:
        rs = _henogen()
        found = extractors.findings_from_raw_signal(rs)
        # 현행 추출기는 서식 안내문을 그대로 1행 뽑는다(DB 의 기존 행과 같은 finding_id) → 새 행 0.
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["finding_text"], _HENOGEN_NOTICE)
        existing = [_db_row(found[0], scope="ok", review="accepted")]
        diffs = backfill.build_document_diffs(["fda483-195517"], [rs], existing, scope_fn=_scope_ok)
        d = diffs[0]
        self.assertEqual(d["status"], backfill.STATUS_NO_OBSERVATION)
        self.assertEqual(d["body_marker"], "없음")
        self.assertEqual([x["action"] for x in d["extracted"]], [backfill.ACTION_SKIP_EXISTING])
        self.assertEqual(d["existing"][0]["noise"], "서식 안내문")
        self.assertTrue(d["existing"][0]["needs_hide"])
        text = backfill.render_document_report(diffs, dry_run=True, scope_label="x")
        self.assertIn("| fda483-195517 | `no_observation_in_source` | 1 | 1 | 0 | 1 | 0 | 1 |", text)
        self.assertIn("원문/PDF 자체에 관찰이 없다", text)
        self.assertIn("숨김 필요(별도 조치, 이 도구는 건드리지 않음)", text)
        self.assertIn("'OBSERVATION n' 표지: 없음", text)

    def test_already_hidden_noise_is_not_flagged_for_hiding(self) -> None:
        rs = _henogen()
        found = extractors.findings_from_raw_signal(rs)
        d = backfill.build_document_diffs(["fda483-195517"], [rs], [_db_row(found[0], scope="fragment")],
                                          scope_fn=_scope_ok)[0]
        self.assertEqual(d["status"], backfill.STATUS_NO_OBSERVATION)
        self.assertFalse(d["existing"][0]["needs_hide"])

    def test_real_observation_alongside_noise_is_not_no_observation(self) -> None:
        rs = _golden_483()
        found = extractors.findings_from_raw_signal(rs)
        noise = {"finding_id": "finding-noise", "raw_signal_id": rs["raw_signal_id"], "source": "FDA 483",
                 "finding_text": "l , .", "scope_status": "fragment", "review_status": "accepted"}
        d = backfill.build_document_diffs([rs["document_id"]], [rs], [noise] + [_db_row(f) for f in found],
                                          scope_fn=_scope_ok)[0]
        self.assertEqual(d["status"], backfill.STATUS_ALL_PRESENT)
        by_id = {e["finding_id"]: e for e in d["existing"]}
        self.assertEqual(by_id["finding-noise"]["noise"], "표제 조각")


class NoiseReasonTest(unittest.TestCase):
    def test_reasons(self) -> None:
        self.assertEqual(backfill.noise_reason(_HENOGEN_NOTICE, "FDA 483"), "서식 안내문")
        self.assertEqual(backfill.noise_reason("Not annotated *DATES OF INSPECTION 1/2/2020", "FDA 483"),
                         "주석란 잡문")
        self.assertEqual(backfill.noise_reason(".", "FDA 483"), "표제 조각")
        self.assertEqual(backfill.noise_reason("Cleaning is not validated now", "FDA 483"), "표제 30자 미만")
        self.assertEqual(backfill.noise_reason(
            "Procedures designed to prevent microbiological contamination are not established.", "FDA 483"), "")
        self.assertEqual(backfill.noise_reason(".", "FDA Warning Letter"), "")


class ScopePreviewTest(unittest.TestCase):
    def test_db_scope_fn_used_and_fallback_marks_approx(self) -> None:
        rs = _golden_483()
        d = backfill.build_document_diffs([rs["document_id"]], [rs], [],
                                          scope_fn=lambda _r, _f: "non_pharma")[0]
        self.assertEqual({(x["scope_preview"], x["scope_approx"]) for x in d["extracted"]}, {("non_pharma", False)})

        def _boom(_r, _f):
            raise RuntimeError("rpc grm_classify_483_scope failed (http_500)")

        d = backfill.build_document_diffs([rs["document_id"]], [rs], [], scope_fn=_boom)[0]
        self.assertTrue(all(x["scope_approx"] for x in d["extracted"]))
        self.assertIn("(근사)", backfill.render_document_report([d], dry_run=True, scope_label="x"))

    def test_approx_matches_db_rule_on_fixtures(self) -> None:
        rs = _henogen()
        f = extractors.findings_from_raw_signal(rs)[0]
        self.assertEqual(backfill.approx_scope(rs, f), "ok")                         # 바이럴 벡터 = 의약품군, 113자
        self.assertEqual(backfill.approx_scope(rs, dict(f, finding_text="l , .")), "fragment")
        food = dict(rs, raw_json=json.dumps({"establishment_type": "Shell Egg Producer"}))
        self.assertEqual(backfill.approx_scope(food, f), "non_pharma")
        self.assertEqual(backfill.approx_scope(rs, dict(f, source="EU GMP NCR")), "ok")

    def test_db_scope_fn_posts_only_to_rpc_with_trigger_args(self) -> None:
        rs = _henogen()
        f = extractors.findings_from_raw_signal(rs)[0]

        class _Resp:
            status_code = 200

            def json(self):
                return "ok"

        with mock.patch("findings_supabase_backfill.requests.post", return_value=_Resp()) as post:
            value = backfill.make_db_scope_fn(_BASE_URL, _SERVICE_KEY)(rs, f)
        self.assertEqual(value, "ok")
        url = post.call_args.args[0]
        self.assertEqual(url, f"{_BASE_URL}/rest/v1/rpc/grm_classify_483_scope")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["p_est_type"], "Viral Vector Manufacturer")
        self.assertEqual(body["p_len"], len(_HENOGEN_NOTICE))
        self.assertEqual(body["p_firm_name"], "Henogen SRL")
        self.assertTrue(body["p_doc_text"].startswith(_HENOGEN_NOTICE + " Section 704(b)"))

    def test_db_scope_fn_error_never_leaks_key(self) -> None:
        import requests as _requests
        rs = _henogen()
        f = extractors.findings_from_raw_signal(rs)[0]
        with mock.patch("findings_supabase_backfill.requests.post",
                        side_effect=_requests.exceptions.ConnectionError(f"apikey={_SERVICE_KEY}")):
            with self.assertRaises(RuntimeError) as ctx:
                backfill.make_db_scope_fn(_BASE_URL, _SERVICE_KEY)(rs, f)
        self.assertNotIn(_SERVICE_KEY, str(ctx.exception))


class FetchDocumentFindingsTest(unittest.TestCase):
    def test_select_only_with_in_filter(self) -> None:
        class _Resp:
            status_code = 200
            headers = {"Content-Range": "0-0/1"}

            def json(self):
                return [{"finding_id": "f1"}]

        with mock.patch("findings_supabase_backfill.requests.get", return_value=_Resp()) as get:
            rows = backfill.fetch_document_findings(_BASE_URL, _SERVICE_KEY, ["rawsig-b", "rawsig-a", ""])
        self.assertEqual(rows, [{"finding_id": "f1"}])
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["raw_signal_id"], "in.(rawsig-a,rawsig-b)")
        self.assertIn("scope_status", params["select"])
        self.assertIn("review_status", params["select"])


if __name__ == "__main__":
    unittest.main()
