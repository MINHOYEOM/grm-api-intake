"""무음 감시 — "오류는 안 났는데 계속 0건"을 잡는지 못박는다.

2026-09-14 실측 회귀: PIC/S 46일 · MHRA 25일 · EU GMP NCR 20일 · WHO 16일 ·
Health Canada 13일 · ICH 60일+ 이 `*_error=False` 라 경보 0건으로 멈춰 있었다.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import grm_common
from source_silence import SilenceFinding, evaluate_silence, watched_sources

RUN = date(2026, 9, 14)
ALL_ON: dict[str, bool] = {}   # 비어 있으면 get(p, True) 로 전부 켜진 것으로 본다


def _codes(findings: list[SilenceFinding]) -> list[str]:
    return sorted(f.notion_source for f in findings)


class WatchedSourceDerivationTest(unittest.TestCase):
    def test_derived_from_registry_not_a_hand_list(self) -> None:
        watched = watched_sources()
        names = [w.notion_source for w in watched]
        self.assertEqual(len(names), len(set(names)), "Source 가 중복되면 같은 소스를 두 번 경고한다")
        # 2026-09-14 에 무음이던 6종은 반드시 감시 대상이어야 한다.
        for must in ("PIC/S", "MHRA Inspectorate", "EU GMP NCR (EudraGMDP)",
                     "WHO", "Health Canada", "ICH"):
            self.assertIn(must, names, f"{must} 가 무음 감시에서 빠졌다")

    def test_shared_source_takes_the_strictest_threshold(self) -> None:
        """mhra 와 mhra_alert 는 같은 Source 다 — 둘 중 엄격한 쪽을 쓴다."""
        mhra = next(w for w in watched_sources() if w.notion_source == "MHRA Inspectorate")
        self.assertEqual(set(mhra.prefixes), {"mhra", "mhra_alert"})
        contributing = [s.silence_days for s in grm_common.INTAKE_SOURCE_SPECS
                        if s.notion_source == "MHRA Inspectorate"]
        self.assertEqual(mhra.silence_days, min(contributing))

    def test_sources_without_a_threshold_are_not_watched(self) -> None:
        names = [w.notion_source for w in watched_sources()]
        # Brave Search 는 선택 소스(ENABLE_SEARCH 기본 false) — 감시 대상이 아니다.
        self.assertNotIn("Brave Search", names)


class EvaluateSilenceTest(unittest.TestCase):
    def test_quiet_within_threshold_is_not_reported(self) -> None:
        seen = {"PIC/S": date(2026, 9, 1)}   # 13일 < 임계 35일
        self.assertEqual(evaluate_silence(last_seen=seen, run_date=RUN,
                                          source_enabled=ALL_ON), [])

    def test_silence_past_threshold_is_reported(self) -> None:
        seen = {"PIC/S": date(2026, 7, 30)}   # 46일 > 35일
        findings = evaluate_silence(last_seen=seen, run_date=RUN, source_enabled=ALL_ON)
        self.assertEqual(_codes(findings), ["PIC/S"])
        self.assertEqual(findings[0].days, 46)
        self.assertEqual(findings[0].threshold, 35)
        self.assertFalse(findings[0].capped)
        self.assertEqual(findings[0].days_text, "46일")

    def test_threshold_boundary_is_exclusive(self) -> None:
        """정확히 임계일이면 아직 경고하지 않는다(초과일 때만)."""
        exact = {"Health Canada": date(2026, 9, 4)}       # 10일 == 임계
        over = {"Health Canada": date(2026, 9, 3)}        # 11일 > 임계
        self.assertEqual(evaluate_silence(last_seen=exact, run_date=RUN,
                                          source_enabled=ALL_ON), [])
        self.assertEqual(_codes(evaluate_silence(last_seen=over, run_date=RUN,
                                                 source_enabled=ALL_ON)), ["Health Canada"])

    def test_no_row_in_lookback_reports_at_the_cap(self) -> None:
        """ICH 처럼 룩백 내내 0건이면 '≥cap일'로 보고한다."""
        findings = evaluate_silence(last_seen={"ICH": None}, run_date=RUN,
                                    source_enabled=ALL_ON, lookback_cap_days=120)
        self.assertEqual(_codes(findings), ["ICH"])
        self.assertTrue(findings[0].capped)
        self.assertEqual(findings[0].days_text, "≥120일")

    def test_unqueried_source_is_not_judged(self) -> None:
        """조회 실패를 무음으로 오보하면 감시 자체를 못 믿게 된다."""
        self.assertEqual(evaluate_silence(last_seen={}, run_date=RUN,
                                          source_enabled=ALL_ON), [])

    def test_disabled_source_is_not_judged(self) -> None:
        """이번 실행에 안 켠 소스가 조용한 건 고장이 아니다."""
        seen = {"ICH": date(2026, 1, 1)}
        self.assertEqual(evaluate_silence(last_seen=seen, run_date=RUN,
                                          source_enabled={"ich": False}), [])

    def test_shared_source_stays_watched_while_any_collector_is_on(self) -> None:
        seen = {"MHRA Inspectorate": date(2026, 8, 1)}    # 44일 > 35일
        self.assertEqual(
            _codes(evaluate_silence(last_seen=seen, run_date=RUN,
                                    source_enabled={"mhra": False, "mhra_alert": True})),
            ["MHRA Inspectorate"])
        self.assertEqual(
            evaluate_silence(last_seen=seen, run_date=RUN,
                             source_enabled={"mhra": False, "mhra_alert": False}), [])

    def test_the_2026_09_14_outage_would_have_been_caught(self) -> None:
        """실측 재현 — 그날 무음이던 소스가 잡히는지.

        임계는 "확실히 이상하다" 선이라 경계에 걸친 두 소스(EU GMP NCR 20일/임계 21,
        MHRA GMP NCR 35일/임계 35)는 **하루이틀 뒤** 울린다. 그건 설계대로다 — 그날도
        경보가 0건이었던 것과는 완전히 다른 상태다.
        """
        seen = {
            "PIC/S": date(2026, 7, 30),                  # 46일 > 35 → 경고
            "MHRA GMP NCR": date(2026, 8, 10),           # 35일 == 35 → 이튿날
            "EU GMP NCR (EudraGMDP)": date(2026, 8, 25), # 20일 < 21 → 이틀 뒤
            "WHO": date(2026, 8, 29),                    # 16일 > 10 → 경고
            "Health Canada": date(2026, 9, 1),           # 13일 > 10 → 경고
            "ICH": None,                                 # 룩백 내내 0건 → 경고
            "Federal Register": date(2026, 9, 13),       # 정상
            "FDA 483": date(2026, 9, 12),                # 정상
        }
        got = _codes(evaluate_silence(last_seen=seen, run_date=RUN, source_enabled=ALL_ON))
        self.assertEqual(got, ["Health Canada", "ICH", "PIC/S", "WHO"])

        # 이틀만 지나도 경계 2종이 따라 울린다 — 임계가 느슨한 것이지 안 잡는 게 아니다.
        later = _codes(evaluate_silence(last_seen=seen, run_date=date(2026, 9, 16),
                                        source_enabled=ALL_ON))
        self.assertEqual(later, ["EU GMP NCR (EudraGMDP)", "Health Canada", "ICH",
                                 "MHRA GMP NCR", "PIC/S", "WHO"])


class HealthWiringTest(unittest.TestCase):
    """`_evaluate_health` 가 무음 경고를 실제로 싣는지 — 배선까지 못박는다."""

    def _health(self, **kw):
        import grm_health
        from collect_intake import CollectionStats
        base = dict(
            stats=CollectionStats(), active={"fr"}, enable_search=False,
            enable_mfds=False, enable_mfds_law=False, enable_mfds_recall=False,
            enable_mfds_admin=False, enable_mfds_gmp_cert=False,
            enable_mfds_safety_letter=False, enable_mfds_gmp_inspection=False,
            enable_ich=False, enable_who=False, enable_hc=False, enable_fda483=False,
            enable_moleg_api=False, enable_scrape=False, event_name="schedule",
            emit_routine_handoff=False, handoff_emitted=False, handoff_failed=False,
            handoff_error_msg="",
        )
        base.update(kw)
        return grm_health._evaluate_health(**base)

    def test_silent_source_becomes_a_warning_not_a_failure(self) -> None:
        health = self._health(
            active={"fr", "pics"},
            source_last_seen={"PIC/S": date(2026, 7, 30)},
            run_date=RUN,
        ).finalize()
        codes = [w.code for w in health.warnings]
        self.assertIn("source-silent:PIC/S", codes)
        # 무음 감시가 그 주 발행을 막으면 안 된다.
        self.assertEqual(health.exit_code, 0)
        self.assertEqual([f.code for f in health.failures], [])

    def test_watchdog_off_emits_nothing(self) -> None:
        health = self._health(active={"fr", "pics"}, source_last_seen=None,
                              run_date=RUN).finalize()
        self.assertEqual([w.code for w in health.warnings if
                          w.code.startswith("source-silent:")], [])

    def test_query_failure_is_surfaced_separately(self) -> None:
        health = self._health(
            active={"fr"}, source_last_seen={}, run_date=RUN,
            source_silence_errors=("PIC/S: timeout",),
        ).finalize()
        self.assertIn("source-silence-query-failed", [w.code for w in health.warnings])


if __name__ == "__main__":
    unittest.main()
