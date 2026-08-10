# -*- coding: utf-8 -*-
"""collect_hc_inspection_backfill 회귀.

네트워크를 타지 않는다 — 목록/리포트카드 응답은 실측 JSON 형태 그대로의 픽스처로 넣고,
append(PostgREST)는 대역으로 치환한다. 이 스크립트의 책임은 '원천을 계약대로 정규화해
멱등 적재하고 정확히 집계/종료코드화'라, 배관 자체는 각자 모듈 테스트가 이미 커버한다.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_hc_inspection_backfill as mod
import findings_extractors
import findings_store

START, END = date(2021, 1, 1), date(2026, 8, 11)
COLLECTED_AT = "2026-08-11T00:00:00+00:00"


# ── 실측 픽스처 ───────────────────────────────────────────────────────────────
# 목록 행: searchResult.ashx 의 실제 필드 구성(무관 필드는 잘라냈고, `insepctionNumber`
# 오타·`/Date(...)/` 형식·전건 null 인 country 는 원천 그대로다).
def _list_row(**over):
    row = {
        "city": "Toronto",
        "country": None,
        "establishmentName": "Sample Pharma Inc.",
        "insepctionNumber": 88818,
        "inspectionStartDate": "/Date(1785427200000-0400)/",   # 2026-07-30 (현지 12:00)
        "licenseNumber": "100123",
        "province": "Ontario",
        "rating": "NC",
        "ratingDesc": "Non-compliant",
        "referenceNumber": "513465",
        "reportCard": True,
    }
    row.update(over)
    return row


# 리포트카드: fullReportCard.ashx 의 실제 구조(insNumber=88332 관찰 2건을 축약).
def _card(**over):
    card = {
        "establishmentName": "Sample Pharma Inc.",
        "insStartDate": "/Date(1785427200000-0400)/",
        "insSubType": "Initial Inspection",
        "insType": "GMP Domestic",
        "insepctionNumber": 88818,
        "insOutcomeList": [
            "Inspection resulted in a Non Compliant rating. The site has been determined "
            "to not be in Compliance with Part C, Division 2 of the Food and Drug Regulations."
        ],
        "measureTakenList": ["Non Compliant rating (NC) issued."],
        "rating": "NC",
        "ratingDesc": "Non-compliant",
        "referenceNumber": "513465",
        "data": [
            {
                "no": "1",
                "regulation": "C.02.005 - Equipment",
                "summaryList": [
                    "The calibration, inspection, and/or qualification of the equipment, "
                    "including computerized systems, was inadequate.",
                    "The usage logs for the equipment used for significant processing "
                    "and/or testing operations were inadequate.",
                ],
            },
            {
                "no": "2",
                "regulation": "C.02.015 - Quality control department",
                "summaryList": [
                    "The quality control department did not undertake all of the "
                    "required activities."
                ],
            },
        ],
    }
    card.update(over)
    return card


class _FakeHttp:
    """url 로 목록/리포트카드를 갈라 응답하는 http_get_json 대역."""

    def __init__(self, rows, cards=None, card_error=None):
        self.rows = rows
        self.cards = cards or {}
        self.card_error = card_error or {}
        self.card_calls = []

    def __call__(self, url, *, params=None, timeout=None, retries=None):
        params = params or {}
        if url == mod.HC_LIST_URL:
            return {"data": list(self.rows)}
        if url == mod.HC_CARD_URL:
            ins = str(params.get("insNumber"))
            self.card_calls.append(ins)
            if ins in self.card_error:
                raise RuntimeError(self.card_error[ins])
            return self.cards.get(ins, _card(insepctionNumber=int(ins)))
        raise AssertionError(f"예상치 못한 URL: {url}")


class _Res:
    def __init__(self, status, errors=()):
        self.status = status
        self.errors = tuple(errors)


def _run(http, **over):
    kwargs = dict(
        start=START, end=END, dry_run=True, base_url="", service_key="",
        collected_at=COLLECTED_AT, http_get=http, sleep=lambda _s: None,
        request_delay=0.0,
    )
    kwargs.update(over)
    return mod.run(**kwargs)


class TestDotNetDate(unittest.TestCase):
    """`/Date(ms±HHMM)/` 파싱 — 하루가 밀리면 연도 경계에서 창 밖으로 나간다."""

    def test_summer_offset_is_applied(self):
        # 1785427200000 = 2026-07-30 16:00 UTC = 현지 12:00 (-0400)
        self.assertEqual(mod.parse_dotnet_date("/Date(1785427200000-0400)/"), "2026-07-30")

    def test_winter_offset_is_applied(self):
        # 1771088400000 = 2026-02-14 17:00 UTC = 현지 12:00 (-0500)
        self.assertEqual(mod.parse_dotnet_date("/Date(1771088400000-0500)/"), "2026-02-14")

    def test_offset_wins_over_utc_at_day_boundary(self):
        # 2026-01-01 01:00 UTC = 2025-12-31 20:00 현지(-0500). UTC 로 읽으면 하루 앞으로
        # 밀려 연도가 바뀐다 — 오프셋을 적용해야 원천이 표시하는 날짜가 나온다.
        self.assertEqual(mod.parse_dotnet_date("/Date(1767229200000-0500)/"), "2025-12-31")

    def test_positive_offset(self):
        self.assertEqual(mod.parse_dotnet_date("/Date(1767229200000+0900)/"), "2026-01-01")

    def test_malformed_returns_empty(self):
        for bad in ("", "2026-07-30", "/Date(abc-0400)/", "/Date(1785427200000)/", None, 17854):
            self.assertEqual(mod.parse_dotnet_date(bad), "", repr(bad))


class TestTargetRowFilter(unittest.TestCase):
    def test_report_card_required(self):
        self.assertFalse(mod.is_target_row(_list_row(reportCard=False)))

    def test_in_progress_excluded_even_with_report_card(self):
        # ★실측에 reportCard=true 이면서 판정이 진행중인 행이 있다 — 한 조건만 보면 샌다.
        self.assertFalse(mod.is_target_row(
            _list_row(reportCard=True, ratingDesc="Inspection in progress")))

    def test_compliant_with_report_card_is_target(self):
        self.assertTrue(mod.is_target_row(_list_row(ratingDesc="Compliant")))


class TestNonHumanFilter(unittest.TestCase):
    def test_veterinary_tokens(self):
        for name in ("Bimeda-MTC Animal Health Inc.",
                     "Dominion Veterinary Laboratories Ltd.",
                     "PharmGate Animal Health Canada Inc."):
            self.assertEqual(mod.non_human_category(name)[0], "veterinary", name)

    def test_veterinary_brand_without_any_token(self):
        # 이름에 업종 단어가 하나도 없는 상호 — 토큰층만으로는 절대 안 걸린다.
        self.assertEqual(mod.non_human_category("Vetoquinol N A Inc.")[0], "veterinary")
        self.assertEqual(mod.non_human_category("Zoetis Canada Inc")[0], "veterinary")

    def test_dental_device_nhp(self):
        self.assertEqual(mod.non_human_category("Patterson Dental Canada Inc.")[0], "dental")
        self.assertEqual(mod.non_human_category("Safecross First Aid Ltd.")[0], "device")
        self.assertEqual(mod.non_human_category("Herbalife Of Canada Limited")[0], "nhp")

    def test_human_drug_firms_are_not_excluded(self):
        """★넓은 토큰을 쓸 뻔했던 실측 반례 — 전부 사람용 의약품 제조소다.

        잘못 거른 문서는 오류 하나 없이 사라지므로, 이 반례들이 통과하는지가 필터의 안전선이다.
        """
        for name in ("B. Braun Medical Inc.",              # "medical"
                     "ICU Medical Canada Inc.",
                     "Fresenius Medical Care Canada Inc.",
                     "Bracco Imaging Canada",              # "imaging" = 조영제
                     "Lantheus Medical Imaging Inc.",      # 방사성의약품
                     "Altea Farmaceutica SA",              # "farm" 오탐
                     "Agrisan Specialty Chemical & Pharmaceutical a division of Agrisan Inc.",
                     "MagGas Medical Inc.",                # 의료용 가스 = 캐나다에선 의약품
                     "Ontario Medical Oxygen Services Inc."):
            self.assertEqual(mod.non_human_category(name), ("", ""), name)

    def test_returns_matched_token_for_audit(self):
        category, token = mod.non_human_category("Ceva Animal Health Inc")
        self.assertEqual(category, "veterinary")
        self.assertEqual(token, "animal health")


class TestObservations(unittest.TestCase):
    def test_summary_list_is_joined_with_single_space(self):
        """★계약: summaryList 배열 → 공백 1칸 join 문자열. 배열을 그대로 넘기면 추출기가
        `"['a', 'b']"` 라는 쓰레기 문자열을 만들고 게이트도 건수도 정상이라 아무도 모른다."""
        obs = mod.observations_from_card(_card())
        self.assertEqual(len(obs), 2)
        self.assertIsInstance(obs[0]["summary"], str)
        self.assertEqual(
            obs[0]["summary"],
            "The calibration, inspection, and/or qualification of the equipment, including "
            "computerized systems, was inadequate. The usage logs for the equipment used for "
            "significant processing and/or testing operations were inadequate.",
        )
        self.assertEqual(obs[0]["no"], "1")
        self.assertEqual(obs[0]["regulation"], "C.02.005 - Equipment")

    def test_newlines_and_runs_of_space_are_collapsed(self):
        card = _card(data=[{"no": "1", "regulation": "C.02.020 - Records",
                            "summaryList": ["The  maintenance\nof records\twas inadequate."]}])
        self.assertEqual(mod.observations_from_card(card)[0]["summary"],
                         "The maintenance of records was inadequate.")

    def test_entry_without_summary_is_dropped(self):
        card = _card(data=[
            {"no": "1", "regulation": "C.02.005 - Equipment", "summaryList": []},
            {"no": "2", "regulation": "C.02.020 - Records", "summaryList": ["   "]},
            {"no": "3", "regulation": "C.02.015 - Quality control department",
             "summaryList": ["The quality control department was inadequate."]},
        ])
        obs = mod.observations_from_card(card)
        self.assertEqual([o["no"] for o in obs], ["3"])

    def test_missing_or_scalar_data_is_absorbed(self):
        self.assertEqual(mod.observations_from_card({}), [])
        self.assertEqual(mod.observations_from_card({"data": None}), [])
        card = _card(data=[{"no": "1", "regulation": "C.02.005 - Equipment",
                            "summaryList": "A single string, not a list."}])
        self.assertEqual(mod.observations_from_card(card)[0]["summary"],
                         "A single string, not a list.")


class TestBuildItem(unittest.TestCase):
    def test_identity_fields(self):
        item = mod.build_item(_list_row(), _card(), "2026-07-30")
        self.assertEqual(item.source, "Health Canada Inspection")
        self.assertEqual(item.type_or_class, "hc-inspection")
        self.assertEqual(item.document_id, "hc-insp-88818")
        self.assertEqual(item.date_iso, "2026-07-30")
        self.assertEqual(item.firm, "Sample Pharma Inc.")
        self.assertEqual(item.language, "EN")
        self.assertEqual(
            item.official_url,
            "https://www.drug-inspections.canada.ca/gmp/fullReportCard-en.html"
            "?lang=en&insNumber=88818")
        self.assertIn("Sample Pharma Inc.", item.headline)
        self.assertIn("Non-compliant", item.headline)

    def test_raw_payload_carries_the_extractor_gate_key_and_context(self):
        raw = mod.build_item(_list_row(), _card(), "2026-07-30").raw_payload
        self.assertEqual(len(raw["hc_inspection_observations"]), 2)
        self.assertEqual(raw["observation_count"], 2)
        for key, expected in (("rating", "NC"), ("ratingDesc", "Non-compliant"),
                              ("insType", "GMP Domestic"), ("insSubType", "Initial Inspection"),
                              ("city", "Toronto"), ("province", "Ontario"),
                              ("license_number", "100123"), ("reference_number", "513465")):
            self.assertEqual(raw[key], expected, key)
        self.assertTrue(raw["report_card_url"].endswith("insNumber=88818"))

    def test_site_country_only_for_domestic(self):
        """★"Canada 고정"은 틀린다 — 실측 1,900건 중 172건이 GMP Foreign 이고 목록 API 의
        country 는 전건 null 이라 해외 제조소의 국가는 원천 어디에도 없다."""
        domestic = mod.build_item(_list_row(), _card(insType="GMP Domestic"), "2026-07-30")
        self.assertEqual(domestic.site_country, "Canada")
        foreign = mod.build_item(
            _list_row(province="Telangana", city="Sangareddy District"),
            _card(insType="GMP Foreign"), "2026-07-30")
        self.assertEqual(foreign.site_country, "")
        self.assertEqual(foreign.raw_payload["insType"], "GMP Foreign")

    def test_signal_tier_by_rating(self):
        self.assertEqual(mod.build_item(_list_row(), _card(), "2026-07-30").signal_tier, "Tier 3")
        self.assertEqual(
            mod.build_item(_list_row(rating="C", ratingDesc="Compliant"),
                           _card(rating="C", ratingDesc="Compliant"), "2026-07-30").signal_tier,
            "Tier 2")

    def test_document_id_is_stable_across_runs(self):
        # 멱등의 근거 — raw_signal_id 는 (source, document_id) 해시다.
        a = mod.build_item(_list_row(), _card(), "2026-07-30")
        b = mod.build_item(_list_row(), _card(), "2026-07-30")
        self.assertEqual(a.document_id, b.document_id)


class TestExtractorContract(unittest.TestCase):
    """이 수집기가 만든 item 이 실제로 findings 를 낳는지 — 게이트 키가 어긋나면 조용히 0건이 된다."""

    def test_item_yields_one_finding_per_observation(self):
        item = mod.build_item(_list_row(), _card(), "2026-07-30")
        record = findings_store.raw_signal_from_intake_item(item, collected_at=COLLECTED_AT)
        findings = findings_extractors.findings_from_raw_signal(record)
        self.assertEqual(len(findings), 2)
        for finding in findings:
            self.assertEqual(finding["source"], "Health Canada Inspection")
            self.assertEqual(finding["agency"], "HC")
            self.assertEqual(finding["document_type"], "hc-inspection")
            self.assertEqual(finding["document_id"], "hc-insp-88818")
            self.assertEqual(finding["published_date"], "2026-07-30")
            self.assertTrue(finding["evidence_url"].endswith("insNumber=88818"))
        summaries = [o["summary"] for o in item.raw_payload["hc_inspection_observations"]]
        self.assertEqual([f["finding_text"] for f in findings], summaries)


class TestRunDryRun(unittest.TestCase):
    def test_dry_run_never_appends(self):
        http = _FakeHttp([_list_row(), _list_row(insepctionNumber=88819)])
        appender = mock.Mock()
        report, code = _run(http, appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.candidates, 2)
        self.assertEqual(report.fetched, 2)
        self.assertEqual(report.observations_total, 4)
        self.assertEqual(report.appended, 0)
        self.assertEqual(report.would_append, ["hc-insp-88818", "hc-insp-88819"])
        appender.assert_not_called()

    def test_sleep_between_detail_calls_only(self):
        http = _FakeHttp([_list_row(), _list_row(insepctionNumber=88819),
                          _list_row(insepctionNumber=88820)])
        sleeper = mock.Mock()
        _run(http, sleep=sleeper, request_delay=0.4)
        # 첫 콜 앞에는 안 잔다 → 문서 3건이면 2회.
        self.assertEqual(sleeper.call_count, 2)
        sleeper.assert_called_with(0.4)


class TestRunSelection(unittest.TestCase):
    def test_non_target_rows_never_cost_a_detail_call(self):
        http = _FakeHttp([
            _list_row(),
            _list_row(insepctionNumber=1, reportCard=False),
            _list_row(insepctionNumber=2, ratingDesc="Inspection in progress"),
        ])
        report, code = _run(http)
        self.assertEqual(code, 0)
        self.assertEqual(report.listed, 3)
        self.assertEqual(report.candidates, 1)
        self.assertEqual(http.card_calls, ["88818"])

    def test_excluded_firms_are_named_not_just_counted(self):
        http = _FakeHttp([
            _list_row(),
            _list_row(insepctionNumber=2, establishmentName="Vetoquinol N A Inc."),
            _list_row(insepctionNumber=3, establishmentName="Vetoquinol N A Inc."),
            _list_row(insepctionNumber=4, establishmentName="Patterson Dental Canada Inc."),
        ])
        report, code = _run(http)
        self.assertEqual(code, 0)
        self.assertEqual(report.excluded_non_human, 3)
        self.assertEqual(
            [(e["firm"], e["category"], e["documents"]) for e in report.excluded_firms],
            [("Patterson Dental Canada Inc.", "dental", 1),
             ("Vetoquinol N A Inc.", "veterinary", 2)])
        self.assertEqual(http.card_calls, ["88818"])   # 제외분에 상세 콜을 쓰지 않는다

    def test_include_veterinary_disables_the_name_filter(self):
        http = _FakeHttp([_list_row(insepctionNumber=2,
                                    establishmentName="Vetoquinol N A Inc.")])
        report, code = _run(http, include_veterinary=True)
        self.assertEqual(code, 0)
        self.assertEqual(report.excluded_non_human, 0)
        self.assertEqual(report.excluded_firms, [])
        self.assertEqual(report.fetched, 1)

    def test_unparsable_date_is_loud_and_skips_before_detail_call(self):
        http = _FakeHttp([_list_row(insepctionNumber=7, inspectionStartDate="2026-07-30")])
        report, code = _run(http)
        self.assertEqual(code, 1)              # 조용한 0건 금지
        self.assertEqual(report.date_unparsed, 1)
        self.assertEqual(report.failed_ins_numbers, ["7"])
        self.assertEqual(http.card_calls, [])

    def test_missing_document_key_is_loud_not_collapsed(self):
        """★문서키가 비면 document_id 가 전부 "hc-insp-" 로 같아져 여러 문서가 하나로
        합쳐진다 — 실패가 아니라 '중복'이라는 성공으로 보이는 자리라 앞에서 막는다."""
        http = _FakeHttp([_list_row(insepctionNumber=None),
                          _list_row(insepctionNumber="")])
        report, code = _run(http)
        self.assertEqual(code, 1)
        self.assertEqual(report.key_missing, 2)
        self.assertEqual(http.card_calls, [])

    def test_out_of_window_row_is_counted_not_silently_dropped(self):
        http = _FakeHttp([_list_row(insepctionNumber=8,
                                    inspectionStartDate="/Date(1262347200000-0500)/")])  # 2010
        report, code = _run(http)
        self.assertEqual(code, 0)
        self.assertEqual(report.out_of_window, 1)
        self.assertEqual(report.fetched, 0)

    def test_limit_caps_detail_calls_and_reports_the_remainder(self):
        http = _FakeHttp([_list_row(insepctionNumber=n) for n in (1, 2, 3, 4)])
        report, code = _run(http, limit=2)
        self.assertEqual(code, 0)
        self.assertEqual(report.candidates, 4)
        self.assertEqual(report.limited_out, 2)
        self.assertEqual(http.card_calls, ["1", "2"])


class TestRunAppendClassification(unittest.TestCase):
    def _http(self, count=2):
        return _FakeHttp([_list_row(insepctionNumber=n) for n in range(1, count + 1)])

    def test_all_inserted(self):
        appender = mock.Mock(side_effect=[_Res("inserted"), _Res("raw_signal_inserted")])
        report, code = _run(self._http(), dry_run=False, base_url="https://x",
                            service_key="k", appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.appended, 2)
        self.assertEqual(report.failed, 0)
        _, kwargs = appender.call_args
        self.assertEqual(kwargs["collected_at"], COLLECTED_AT)

    def test_duplicate_is_idempotent_success(self):
        appender = mock.Mock(side_effect=[_Res("duplicate"), _Res("duplicate")])
        report, code = _run(self._http(), dry_run=False, base_url="https://x",
                            service_key="k", appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.duplicate, 2)
        self.assertEqual(report.appended, 0)

    def test_partial_counts_as_appended_with_warning(self):
        appender = mock.Mock(return_value=_Res("partial", errors=("finding row POST failed",)))
        report, code = _run(self._http(1), dry_run=False, base_url="https://x",
                            service_key="k", appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.partial, 1)
        self.assertEqual(report.appended, 1)
        self.assertTrue(any("append_partial" in e for e in report.errors))

    def test_error_status_fails_run_and_keeps_ins_number(self):
        appender = mock.Mock(side_effect=[
            _Res("inserted"), _Res("error", errors=("raw_signals POST failed: http_500",))])
        report, code = _run(self._http(), dry_run=False, base_url="https://x",
                            service_key="k", appender=appender)
        self.assertEqual(code, 1)
        self.assertEqual(report.appended, 1)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.failed_ins_numbers, ["2"])
        self.assertTrue(any("append_failed(hc-insp-2)" in e for e in report.errors))

    def test_append_exception_is_caught_and_counted(self):
        appender = mock.Mock(side_effect=RuntimeError("boom"))
        report, code = _run(self._http(1), dry_run=False, base_url="https://x",
                            service_key="k", appender=appender)
        self.assertEqual(code, 1)
        self.assertEqual(report.failed, 1)
        self.assertTrue(any("append_raised(hc-insp-1)" in e for e in report.errors))


class TestRunFailures(unittest.TestCase):
    def test_list_failure_exits_2_and_never_appends(self):
        def boom(url, *, params=None, timeout=None, retries=None):
            raise RuntimeError("HTTP GET final failure")
        appender = mock.Mock()
        report, code = _run(boom, appender=appender)
        self.assertEqual(code, 2)
        self.assertEqual(report.listed, 0)
        self.assertTrue(any("collect_failed" in e for e in report.errors))
        appender.assert_not_called()

    def test_list_shape_change_exits_2(self):
        report, code = _run(lambda url, **kw: {"rows": []})
        self.assertEqual(code, 2)
        self.assertTrue(any("data 배열" in e for e in report.errors))

    def test_card_failure_is_per_document_and_recorded_for_rerun(self):
        http = _FakeHttp([_list_row(insepctionNumber=n) for n in (1, 2, 3)],
                         card_error={"2": "timeout"})
        appender = mock.Mock(return_value=_Res("inserted"))
        report, code = _run(http, dry_run=False, base_url="https://x",
                            service_key="k", appender=appender)
        self.assertEqual(code, 1)                       # 침묵 금지
        self.assertEqual(report.fetched, 2)
        self.assertEqual(report.card_failed, 1)
        self.assertEqual(report.failed_ins_numbers, ["2"])
        self.assertEqual(report.appended, 2)            # 나머지는 계속 적재된다

    def test_document_without_observations_is_still_appended_but_counted(self):
        http = _FakeHttp([_list_row()], cards={"88818": _card(data=[])})
        appender = mock.Mock(return_value=_Res("raw_signal_inserted"))
        report, code = _run(http, dry_run=False, base_url="https://x",
                            service_key="k", appender=appender)
        self.assertEqual(code, 0)
        self.assertEqual(report.no_observation_docs, 1)
        self.assertEqual(report.observations_total, 0)
        self.assertEqual(report.appended, 1)


class TestMainCli(unittest.TestCase):
    def _clean_env(self):
        return {k: v for k, v in os.environ.items()
                if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}

    def test_apply_requires_creds(self):
        with mock.patch.dict(os.environ, self._clean_env(), clear=True):
            self.assertEqual(mod.main(["--apply"]), 2)

    def test_dry_run_is_the_default_and_needs_no_creds(self):
        http = _FakeHttp([_list_row()])
        with mock.patch.dict(os.environ, self._clean_env(), clear=True), \
                mock.patch.object(mod, "http_get_json", http), \
                mock.patch.object(mod, "append_intake_item_with_findings_to_supabase",
                                  mock.Mock(side_effect=AssertionError("dry-run 인데 적재했다"))):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = mod.main(["--from-date", "2021-01-01", "--request-delay", "0"])
        self.assertEqual(code, 0)
        self.assertIn("would_append", buf.getvalue())
        self.assertIn("hc-insp-88818", buf.getvalue())

    def test_reversed_window_is_rejected(self):
        with mock.patch.dict(os.environ, self._clean_env(), clear=True):
            self.assertEqual(mod.main(["--from-date", "2026-01-01",
                                       "--to-date", "2025-01-01"]), 2)

    def test_dry_run_and_apply_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            mod.build_arg_parser().parse_args(["--dry-run", "--apply"])


if __name__ == "__main__":
    unittest.main()
