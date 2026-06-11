"""정밀검토 배치3 Tier1 — 오케스트레이션 핵심 회귀(D1 공백 해소, 현 동작 동결).

대상(전부 순수 로직·무네트워크):
- _dedupe_latest_rows: freshness 승자 선택·결정론 출력 정렬·소스 간 키 충돌 방지.
  (기존 test_k2_prep 은 enrich 경유 len==1 만 단언 — 승자·정렬 미검증이었음)
- _evaluate_health: failure/warning/info 전 분기와 finalize() 의 status·exit_code.
  green/red 신호의 근원이라 무회귀면 조용한 퇴행 위험(배치2가 aged-* 만 커버).
- _is_transient_source_error: warning↔failure 갈림의 단일 분류기.

테스트 전용 배치 — 프로덕션 로직 무변경, 현 동작을 동결한다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci


# ─────────────────────────────────────────────────────────────────────────────
# T1-a. _dedupe_latest_rows
# ─────────────────────────────────────────────────────────────────────────────
def _row(source="MFDS", doc="d1", run_date="2026-06-01", collected_at="",
         page_id="p1", tier="Tier 2"):
    return {"source": source, "document_id": doc, "run_date": run_date,
            "collected_at": collected_at, "page_id": page_id, "signal_tier": tier}


class DedupeLatestRowsTest(unittest.TestCase):
    def test_freshness_winner_is_latest_run_date(self) -> None:
        # 같은 키(source::document_id) → (run_date, collected_at, page_id) 최댓값 승자.
        # stale(과거 run_date)이 아니라 latest 가 남아야 한다 — 입력 순서 무관.
        stale = _row(run_date="2026-06-01", page_id="p-stale")
        fresh = _row(run_date="2026-06-08", page_id="p-fresh")
        for ordering in ([stale, fresh], [fresh, stale]):
            with self.subTest(first=ordering[0]["page_id"]):
                out = ci._dedupe_latest_rows(ordering)
                self.assertEqual(len(out), 1)
                self.assertEqual(out[0]["page_id"], "p-fresh")

    def test_run_date_tie_breaks_on_collected_at(self) -> None:
        # run_date 동일 → collected_at 사전순 최댓값(=최신 수집 시각)이 승자.
        early = _row(collected_at="2026-06-08T03:00:00", page_id="p-early")
        late = _row(collected_at="2026-06-08T09:00:00", page_id="p-late")
        out = ci._dedupe_latest_rows([late, early])
        self.assertEqual([r["page_id"] for r in out], ["p-late"])

    def test_output_sorted_tier_then_source_then_doc_id(self) -> None:
        # 셔플 입력 → (tier_order, source, document_id) 오름차순 결정론 정렬.
        # Tier 3(0) < Tier 2(1) < Tier 1(2) < 미상(9, 말미).
        rows = [
            _row(source="MFDS", doc="z9", tier="Tier 1", page_id="p1"),
            _row(source="EMA", doc="b2", tier="알수없음", page_id="p2"),
            _row(source="MFDS", doc="a1", tier="Tier 3", page_id="p3"),
            _row(source="EMA", doc="a1", tier="Tier 2", page_id="p4"),
            _row(source="MFDS", doc="b2", tier="Tier 2", page_id="p5"),
        ]
        out = ci._dedupe_latest_rows(rows)
        got = [(r["signal_tier"], r["source"], r["document_id"]) for r in out]
        self.assertEqual(got, [
            ("Tier 3", "MFDS", "a1"),       # tier 0
            ("Tier 2", "EMA", "a1"),        # tier 1 — source 사전순
            ("Tier 2", "MFDS", "b2"),
            ("Tier 1", "MFDS", "z9"),       # tier 2
            ("알수없음", "EMA", "b2"),       # 미상 tier → 말미(9)
        ])

    def test_same_doc_id_different_source_both_preserved(self) -> None:
        # dedup 키는 source::document_id — 소스가 다르면 같은 doc_id 도 별개 row.
        out = ci._dedupe_latest_rows([
            _row(source="MFDS", doc="shared", page_id="p-mfds"),
            _row(source="EMA", doc="shared", page_id="p-ema"),
        ])
        self.assertEqual(len(out), 2)
        self.assertEqual({r["page_id"] for r in out}, {"p-mfds", "p-ema"})

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(ci._dedupe_latest_rows([]), [])


# ─────────────────────────────────────────────────────────────────────────────
# T1-c. _is_transient_source_error — warning↔failure 갈림의 단일 분류기
# ─────────────────────────────────────────────────────────────────────────────
class TransientSourceErrorTest(unittest.TestCase):
    def test_network_transients_are_transient(self) -> None:
        # MFDS 계열 코드 + 일시 네트워크 마커 → True(→ health warning).
        cases = [
            ("mfds-rss", "ReadTimeoutError: read timed out"),
            ("mfds-recall", "HTTP 429 Too Many Requests"),
            ("mfds-admin", "Connection reset by peer"),
            ("mfds-gmp-inspection", "HTTP 503 Service Unavailable"),
            ("mfds-rss", "Max retries exceeded with url"),
        ]
        for code, detail in cases:
            with self.subTest(code=code, detail=detail[:30]):
                self.assertTrue(ci._is_transient_source_error(code, detail))

    def test_public_endpoint_403_is_transient_but_api_403_is_not(self) -> None:
        # nedrug/RSS 공개 endpoint 403 = WAF성(transient). data.go.kr API 403 =
        # 키/권한 문제일 가능성 → failure 유지(코드 주석의 의도된 비대칭).
        self.assertTrue(ci._is_transient_source_error("mfds-rss", "HTTP 403 Forbidden"))
        self.assertTrue(ci._is_transient_source_error(
            "mfds-gmp-inspection", "403 Forbidden"))
        self.assertFalse(ci._is_transient_source_error("mfds-recall", "HTTP 403 Forbidden"))
        self.assertFalse(ci._is_transient_source_error("mfds-admin", "HTTP 403 Forbidden"))
        # T1: ICH/WHO/HC 도 키 없는 공개 endpoint — 403 은 WAF/IP 차단성(transient).
        for code in ("ich", "who", "health-canada"):
            with self.subTest(code=code):
                self.assertTrue(ci._is_transient_source_error(
                    code, f"HTTP 403 for https://example/{code}"))

    def test_config_errors_are_never_transient(self) -> None:
        # 설정 오류(환경변수/키)는 마커와 무관하게 failure — 사람이 고쳐야 함.
        for detail in ("DATA_GO_KR_SERVICE_KEY 환경변수 필요",
                       "invalid api key", "service_key not registered"):
            with self.subTest(detail=detail):
                self.assertFalse(ci._is_transient_source_error("mfds-recall", detail))

    def test_structure_change_is_not_transient(self) -> None:
        # 구조 변경/스키마 오류 → failure(transient 마커 없음).
        self.assertFalse(ci._is_transient_source_error(
            "mfds-gmp-inspection", "결과 테이블 컬럼 구조 변경 감지"))

    def test_global_public_sources_transient_is_transient(self) -> None:
        # T1: 활성 글로벌 소스(ich/who/health-canada) 일시오류=warning 으로 정정 —
        # 배치3 이 동결했던 "MFDS 외 무조건 False" 는 활성화(2026-06-05) 이전 스코프 누락.
        for code in ("ich", "who", "health-canada"):
            with self.subTest(code=code):
                self.assertTrue(ci._is_transient_source_error(code, "read timed out"))
                self.assertTrue(ci._is_transient_source_error(
                    code, "HTTP GET final failure: https://x (503 Server Error: "
                          "Service Unavailable)"))

    def test_global_public_sources_structural_error_stays_failure(self) -> None:
        # T1 경계: 마커 없는 구조/형식 오류는 ich/who/hc 도 여전히 failure.
        for code, detail in (
            ("who", "WHO WHOPIR 0건 — 구조/렌더 변경 의심(수동 확인 필요)"),
            ("health-canada", "HC 오픈데이터 형식 이상(배열 아님/0레코드)"),
            ("ich", "ICH 토픽 섹션 0건 — 코드 패턴 불일치"),
        ):
            with self.subTest(code=code):
                self.assertFalse(ci._is_transient_source_error(code, detail))

    def test_whitelist_outside_codes_are_never_transient(self) -> None:
        # transient 적격 화이트리스트 밖(brave 등)은 timeout 이어도 False.
        for code in ("brave-search", "ema", "wl"):
            with self.subTest(code=code):
                self.assertFalse(ci._is_transient_source_error(code, "read timed out"))

    def test_empty_detail_is_not_transient(self) -> None:
        self.assertFalse(ci._is_transient_source_error("mfds-rss", ""))


# ─────────────────────────────────────────────────────────────────────────────
# T1-b. _evaluate_health 분기 + finalize() — green/red 신호의 근원
# ─────────────────────────────────────────────────────────────────────────────
def _health_kwargs(**over):
    """최소 호출 kwargs — 전 소스 비활성·에러 없음(기본 ok). over 로 분기 주입."""
    base = dict(
        stats=ci.CollectionStats(),
        active=set(),
        enable_search=False, enable_mfds=False, enable_mfds_recall=False,
        enable_mfds_admin=False, enable_mfds_gmp_inspection=False,
        enable_ich=False, enable_who=False, enable_hc=False,
        enable_fda483=False,
        enable_moleg_api=False, enable_scrape=False,
        event_name="schedule",
        emit_routine_handoff=False, handoff_emitted=False, handoff_failed=False,
        handoff_error_msg="",
    )
    base.update(over)
    return base


def _codes(findings):
    return [f.code for f in findings]


class EvaluateHealthFailureBranchTest(unittest.TestCase):
    def test_clean_run_is_ok_exit0(self) -> None:
        health = ci._evaluate_health(**_health_kwargs())
        self.assertEqual(health.status, "ok")
        self.assertEqual(health.exit_code, 0)
        self.assertEqual(health.failures, [])
        self.assertEqual(health.warnings, [])

    def test_insert_failures_are_failure_exit1(self) -> None:
        stats = ci.CollectionStats()
        stats.fr_insert_failed = 2
        health = ci._evaluate_health(**_health_kwargs(stats=stats))
        self.assertIn("notion-insert-failed", _codes(health.failures))
        self.assertEqual(health.status, "failure")
        self.assertEqual(health.exit_code, 1)

    def test_handoff_failed_is_failure(self) -> None:
        health = ci._evaluate_health(**_health_kwargs(
            emit_routine_handoff=True, handoff_failed=True,
            handoff_error_msg="Notion API 500"))
        self.assertIn("handoff-failed", _codes(health.failures))
        self.assertEqual(health.exit_code, 1)

    def test_phase1_both_failed_is_failure(self) -> None:
        # FR + OpenFDA 동시 실패 = 핵심 공식 API 전멸 → failure.
        stats = ci.CollectionStats()
        stats.fr_error = True
        stats.recall_error = True
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, active={"fr", "recall"}))
        self.assertIn("phase1-all-failed", _codes(health.failures))
        self.assertEqual(health.exit_code, 1)

    def test_phase1_single_failure_is_not_all_failed(self) -> None:
        # 음성: 한쪽만 실패면 phase1-all-failed 아님(부분 실패 허용).
        stats = ci.CollectionStats()
        stats.fr_error = True
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, active={"fr", "recall"}))
        self.assertNotIn("phase1-all-failed", _codes(health.failures))

    def test_enabled_source_nontransient_error_is_failure(self) -> None:
        stats = ci.CollectionStats()
        stats.mfds_error = True
        stats.mfds_error_msg = "RSS 구조 변경: item 태그 없음"
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, active={"fr", "mfds"}, enable_mfds=True))
        self.assertIn("enabled-source-error:mfds-rss", _codes(health.failures))
        self.assertEqual(health.exit_code, 1)

    def test_enabled_source_transient_error_is_warning(self) -> None:
        # 같은 소스라도 transient(429) 면 warning 으로 강등 — exit 0 유지.
        stats = ci.CollectionStats()
        stats.mfds_error = True
        stats.mfds_error_msg = "HTTP 429 Too Many Requests"
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, active={"fr", "mfds"}, enable_mfds=True))
        self.assertIn("transient-source-error:mfds-rss", _codes(health.warnings))
        self.assertNotIn("enabled-source-error:mfds-rss", _codes(health.failures))
        self.assertEqual(health.status, "warning")
        self.assertEqual(health.exit_code, 0)

    def test_all_active_sources_failed_nontransient(self) -> None:
        # Phase1 비활성 + 활성 소스(mfds)가 전부 비일시 오류 → all-active 실패.
        stats = ci.CollectionStats()
        stats.mfds_error = True
        stats.mfds_error_msg = "스키마 오류"
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, active={"mfds"}, enable_mfds=True))
        self.assertIn("all-active-sources-failed", _codes(health.failures))

    def test_all_active_sources_failed_transient_is_warning(self) -> None:
        stats = ci.CollectionStats()
        stats.mfds_error = True
        stats.mfds_error_msg = "connection reset by peer"
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, active={"mfds"}, enable_mfds=True))
        self.assertIn("all-active-sources-transient", _codes(health.warnings))
        self.assertNotIn("all-active-sources-failed", _codes(health.failures))
        self.assertEqual(health.exit_code, 0)

    def test_t1_active_global_source_transient_blip_is_warning_not_red(self) -> None:
        # T1 핵심 시나리오: FR/OpenFDA 정상 + ICH 단독 timeout — 종전엔 run 전체
        # failure(exit 1)로 red. 정정 후 warning(exit 0) — graceful degrade 와 일치.
        for src, set_err in (
            ("ich", ("ich_error", "ich_error_msg")),
            ("who", ("who_error", "who_error_msg")),
            ("health-canada", ("hc_error", "hc_error_msg")),
        ):
            with self.subTest(src=src):
                stats = ci.CollectionStats()
                setattr(stats, set_err[0], True)
                setattr(stats, set_err[1],
                        "HTTP GET final failure: https://x (read timed out)")
                health = ci._evaluate_health(**_health_kwargs(
                    stats=stats, active={"fr", "recall"},
                    enable_ich=(src == "ich"), enable_who=(src == "who"),
                    enable_hc=(src == "health-canada")))
                self.assertIn(f"transient-source-error:{src}",
                              _codes(health.warnings))
                self.assertNotIn(f"enabled-source-error:{src}",
                                 _codes(health.failures))
                self.assertEqual(health.exit_code, 0)

    def test_t1_active_global_source_structural_error_stays_failure(self) -> None:
        # T1 경계: 같은 ICH 라도 구조 오류(마커 없음)는 failure(exit 1) 유지.
        stats = ci.CollectionStats()
        stats.ich_error = True
        stats.ich_error_msg = "ICH 토픽 섹션 0건 — 코드 패턴 불일치"
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, active={"fr", "recall"}, enable_ich=True))
        self.assertIn("enabled-source-error:ich", _codes(health.failures))
        self.assertEqual(health.exit_code, 1)


class EvaluateHealthInfoAndWarningTest(unittest.TestCase):
    def test_handoff_only_run_is_info_ok(self) -> None:
        # handoff-only(소스 fetch 전무 + emit 성공) → info, failure 아님, exit 0.
        health = ci._evaluate_health(**_health_kwargs(
            emit_routine_handoff=True, handoff_emitted=True))
        self.assertIn("handoff-only", _codes(health.infos))
        self.assertEqual(health.status, "ok")
        self.assertEqual(health.exit_code, 0)

    def test_moleg_on_schedule_is_warning(self) -> None:
        health = ci._evaluate_health(**_health_kwargs(
            event_name="schedule", enable_moleg_api=True))
        self.assertIn("moleg-enabled-on-schedule", _codes(health.warnings))
        self.assertEqual(health.exit_code, 0)

    def test_moleg_on_dispatch_is_allowed_no_warning(self) -> None:
        # 음성: workflow_dispatch opt-in 은 허용 — schedule 에서만 경고.
        health = ci._evaluate_health(**_health_kwargs(
            event_name="workflow_dispatch", enable_moleg_api=True))
        self.assertNotIn("moleg-enabled-on-schedule", _codes(health.warnings))

    def test_scrape_enabled_is_warning(self) -> None:
        health = ci._evaluate_health(**_health_kwargs(enable_scrape=True))
        self.assertIn("scrape-enabled-unimplemented", _codes(health.warnings))

    def test_truncation_flags_are_warnings(self) -> None:
        stats = ci.CollectionStats()
        stats.fr_truncated = True
        stats.recall_truncated = True
        health = ci._evaluate_health(**_health_kwargs(stats=stats))
        self.assertIn("fr-truncated", _codes(health.warnings))
        self.assertIn("recall-truncated", _codes(health.warnings))
        self.assertEqual(health.exit_code, 0)

    def test_gmp_manual_review_is_warning_with_count(self) -> None:
        stats = ci.CollectionStats()
        stats.mfds_gmp_inspection_manual_review = 2
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, enable_mfds_gmp_inspection=True))
        warning = next(w for w in health.warnings
                       if w.code == "gmp-attachment-manual-review")
        self.assertIn("2건", warning.message)

    def test_modality_preflight_degraded_is_warning(self) -> None:
        health = ci._evaluate_health(**_health_kwargs(
            modality_preflight_disabled=True))
        self.assertIn("modality-preflight-degraded", _codes(health.warnings))
        self.assertEqual(health.exit_code, 0)


class EvaluateHealthExcerptDegradedTest(unittest.TestCase):
    """WHY-1 P1 — excerpt 실패/cap 은 warning-only 표면화(절대 failure 승격 금지).

    카드 자체는 graceful degrade(WHOPIR=링크 카드, WL=메타 카드 유지)이므로 수집
    성공이며, 조용한 실패만 막는다. flag off(시도 0)면 카운터 0 → finding 미발생.
    """

    def test_whopir_excerpt_failed_is_warning_not_failure(self) -> None:
        stats = ci.CollectionStats()
        stats.whopir_excerpt_attempted = 5
        stats.whopir_excerpt_failed = 2
        health = ci._evaluate_health(**_health_kwargs(stats=stats, enable_who=True))
        self.assertIn("whopir-excerpt-degraded", _codes(health.warnings))
        self.assertEqual(health.failures, [])           # failure 승격 0
        self.assertEqual(health.status, "warning")
        self.assertEqual(health.exit_code, 0)
        warning = next(w for w in health.warnings
                       if w.code == "whopir-excerpt-degraded")
        self.assertIn("2건", warning.message)
        self.assertIn("링크 카드 유지", warning.message)

    def test_whopir_excerpt_capped_alone_is_warning(self) -> None:
        # 실패 0 이어도 cap 도달이면 이후 항목 excerpt 생략 사실을 표면화.
        stats = ci.CollectionStats()
        stats.whopir_excerpt_attempted = 40
        stats.whopir_excerpt_capped = 1
        health = ci._evaluate_health(**_health_kwargs(stats=stats, enable_who=True))
        self.assertIn("whopir-excerpt-degraded", _codes(health.warnings))
        self.assertEqual(health.failures, [])
        self.assertEqual(health.exit_code, 0)
        warning = next(w for w in health.warnings
                       if w.code == "whopir-excerpt-degraded")
        self.assertIn("cap", warning.message)

    def test_wl_body_failed_is_warning_not_failure(self) -> None:
        stats = ci.CollectionStats()
        stats.wl_body_attempted = 3
        stats.wl_body_failed = 3
        health = ci._evaluate_health(**_health_kwargs(
            stats=stats, active={"fr", "recall", "wl"}))
        self.assertIn("wl-body-degraded", _codes(health.warnings))
        self.assertEqual(health.failures, [])           # failure 승격 0
        self.assertEqual(health.status, "warning")
        self.assertEqual(health.exit_code, 0)
        warning = next(w for w in health.warnings if w.code == "wl-body-degraded")
        self.assertIn("3건", warning.message)
        self.assertIn("메타 카드 유지", warning.message)

    def test_flag_off_or_clean_counters_no_excerpt_warning(self) -> None:
        # flag off(시도 0) 또는 전건 성공(failed=0·cap 미도달) → finding 미발생.
        for attempted in (0, 7):
            with self.subTest(attempted=attempted):
                stats = ci.CollectionStats()
                stats.whopir_excerpt_attempted = attempted
                stats.wl_body_attempted = attempted
                health = ci._evaluate_health(**_health_kwargs(
                    stats=stats, active={"fr", "recall", "wl"}, enable_who=True))
                self.assertNotIn("whopir-excerpt-degraded", _codes(health.warnings))
                self.assertNotIn("wl-body-degraded", _codes(health.warnings))
                self.assertEqual(health.status, "ok")
                self.assertEqual(health.exit_code, 0)


class EvaluateHealthFda483DegradedTest(unittest.TestCase):
    """WHY-1 #3 P1 — 483 excerpt 실패/cap·표 절단은 warning-only(failure 승격 금지).

    카드 자체는 graceful degrade(메타 카드 유지)이므로 수집 성공이며, 조용한 실패/
    완전성 미보장만 표면화한다. flag off(시도 0·truncated 0)면 finding 미발생.
    """

    def test_fda483_excerpt_failed_is_warning_not_failure(self) -> None:
        stats = ci.CollectionStats()
        stats.fda483_excerpt_attempted = 5
        stats.fda483_excerpt_failed = 2
        health = ci._evaluate_health(**_health_kwargs(stats=stats, enable_fda483=True))
        self.assertIn("fda483-excerpt-degraded", _codes(health.warnings))
        self.assertEqual(health.failures, [])           # failure 승격 0
        self.assertEqual(health.status, "warning")
        self.assertEqual(health.exit_code, 0)
        warning = next(w for w in health.warnings if w.code == "fda483-excerpt-degraded")
        self.assertIn("2건", warning.message)
        self.assertIn("메타 카드 유지", warning.message)

    def test_fda483_excerpt_capped_alone_is_warning(self) -> None:
        stats = ci.CollectionStats()
        stats.fda483_excerpt_attempted = 40
        stats.fda483_excerpt_capped = 1
        health = ci._evaluate_health(**_health_kwargs(stats=stats, enable_fda483=True))
        self.assertIn("fda483-excerpt-degraded", _codes(health.warnings))
        self.assertEqual(health.failures, [])
        self.assertEqual(health.exit_code, 0)

    def test_fda483_table_truncated_is_warning(self) -> None:
        stats = ci.CollectionStats()
        stats.fda483_table_truncated = 1
        health = ci._evaluate_health(**_health_kwargs(stats=stats, enable_fda483=True))
        self.assertIn("fda483-table-truncated", _codes(health.warnings))
        self.assertEqual(health.failures, [])
        self.assertEqual(health.exit_code, 0)

    def test_flag_off_or_clean_counters_no_fda483_warning(self) -> None:
        # flag off(시도 0·truncated 0) 또는 전건 성공 → finding 미발생.
        for attempted in (0, 7):
            with self.subTest(attempted=attempted):
                stats = ci.CollectionStats()
                stats.fda483_excerpt_attempted = attempted
                health = ci._evaluate_health(**_health_kwargs(
                    stats=stats, enable_fda483=True))
                self.assertNotIn("fda483-excerpt-degraded", _codes(health.warnings))
                self.assertNotIn("fda483-table-truncated", _codes(health.warnings))
                self.assertEqual(health.status, "ok")
                self.assertEqual(health.exit_code, 0)


class HealthFinalizeTest(unittest.TestCase):
    """finalize(): failure > warning > ok 우선순위와 exit_code 결정."""

    def test_failure_wins_over_warning(self) -> None:
        # failure + warning 공존 → status=failure, exit 1.
        stats = ci.CollectionStats()
        stats.fr_insert_failed = 1
        stats.fr_truncated = True
        health = ci._evaluate_health(**_health_kwargs(stats=stats))
        self.assertTrue(health.failures and health.warnings)
        self.assertEqual(health.status, "failure")
        self.assertEqual(health.exit_code, 1)

    def test_finalize_direct(self) -> None:
        r = ci.HealthCheckResult()
        self.assertEqual(r.finalize().status, "ok")
        r2 = ci.HealthCheckResult()
        r2.add_warning("w", "src", "msg")
        self.assertEqual(r2.finalize().status, "warning")
        self.assertEqual(r2.exit_code, 0)
        r3 = ci.HealthCheckResult()
        r3.add_warning("w", "src", "msg")
        r3.add_failure("f", "src", "msg")
        self.assertEqual(r3.finalize().status, "failure")
        self.assertEqual(r3.exit_code, 1)


if __name__ == "__main__":
    unittest.main()
