"""무음 감시 — "오류는 안 났는데 계속 0건"을 잡는지 못박는다.

2026-09-14 실측 회귀: PIC/S 46일 · MHRA 25일 · EU GMP NCR 20일 · WHO 16일 ·
Health Canada 13일 · ICH 60일+ 이 `*_error=False` 라 경보 0건으로 멈춰 있었다.

★[2026-09-21 정정] 그 12종을 한 덩어리로 "무음 사고"라 부른 것이 틀렸다. 같은 날 러너
프로브(`grm-source-probe.yml targets=silent`, run 34797482678)가 PIC/S·MHRA GMP NCR·
EU GMP NCR 를 **3종 전부 OPEN** 으로 판정했다 — PIC/S 는 피드 109건·최신 pubDate 07-30 이고
Notion 마지막 행도 07-30, 즉 **있는 건 다 긁어온 상태**였다. 고장이 아니라 원천이 느린
것이었고, 초판 임계(35·35·21일)가 그 주기보다 짧아 건강한 소스가 매일 경고를 냈다
(09-21 이슈 #956 이 🚨 7일 연속까지 감). 임계를 교정했으므로 이 파일의 기대값도 함께 바꾼다.
WHO·Health Canada 는 반대로 **진짜 신호**였다(WHO 는 09-18 에 회복) — 그쪽은 그대로 둔다.
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
        # 2026-09-14 에 무음이던 소스 중 원천이 날짜 있는 항목을 내는 5종은 반드시 감시 대상이다.
        for must in ("PIC/S", "MHRA Inspectorate", "EU GMP NCR (EudraGMDP)",
                     "WHO", "Health Canada"):
            self.assertIn(must, names, f"{must} 가 무음 감시에서 빠졌다")

    def test_snapshot_diff_source_ich_is_exempt(self) -> None:
        """ICH 는 섹션 제목 스냅샷 diff(dedup 창 1095일) — 페이지가 안 바뀌면 몇 달이고 0건이
        설계상 정상이라 임계를 두면 상시 경고가 된다. 수집 고장은 `ich_error` 가 잡는다."""
        names = [w.notion_source for w in watched_sources()]
        self.assertNotIn("ICH", names)
        spec = next(s for s in grm_common.INTAKE_SOURCE_SPECS if s.prefix == "ich")
        self.assertEqual(spec.silence_days, 0)

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
        seen = {"PIC/S": date(2026, 9, 1)}   # 13일 < 임계 90일
        self.assertEqual(evaluate_silence(last_seen=seen, run_date=RUN,
                                          source_enabled=ALL_ON), [])

    def test_silence_past_threshold_is_reported(self) -> None:
        seen = {"PIC/S": date(2026, 5, 1)}   # 136일 > 90일
        findings = evaluate_silence(last_seen=seen, run_date=RUN, source_enabled=ALL_ON)
        self.assertEqual(_codes(findings), ["PIC/S"])
        self.assertEqual(findings[0].days, 136)
        self.assertEqual(findings[0].threshold, 90)
        self.assertFalse(findings[0].capped)
        self.assertEqual(findings[0].days_text, "136일")

    def test_threshold_boundary_is_exclusive(self) -> None:
        """정확히 임계일이면 아직 경고하지 않는다(초과일 때만)."""
        exact = {"Health Canada": date(2026, 9, 4)}       # 10일 == 임계
        over = {"Health Canada": date(2026, 9, 3)}        # 11일 > 임계
        self.assertEqual(evaluate_silence(last_seen=exact, run_date=RUN,
                                          source_enabled=ALL_ON), [])
        self.assertEqual(_codes(evaluate_silence(last_seen=over, run_date=RUN,
                                                 source_enabled=ALL_ON)), ["Health Canada"])

    def test_no_row_in_lookback_reports_at_the_cap(self) -> None:
        """룩백 내내 0건이면 '≥cap일'로 보고한다."""
        findings = evaluate_silence(last_seen={"PIC/S": None}, run_date=RUN,
                                    source_enabled=ALL_ON, lookback_cap_days=120)
        self.assertEqual(_codes(findings), ["PIC/S"])
        self.assertTrue(findings[0].capped)
        self.assertEqual(findings[0].days_text, "≥120일")

    def test_unqueried_source_is_not_judged(self) -> None:
        """조회 실패를 무음으로 오보하면 감시 자체를 못 믿게 된다."""
        self.assertEqual(evaluate_silence(last_seen={}, run_date=RUN,
                                          source_enabled=ALL_ON), [])

    def test_disabled_source_is_not_judged(self) -> None:
        """이번 실행에 안 켠 소스가 조용한 건 고장이 아니다."""
        seen = {"WHO": date(2026, 1, 1)}
        self.assertEqual(evaluate_silence(last_seen=seen, run_date=RUN,
                                          source_enabled={"who": False}), [])

    def test_shared_source_stays_watched_while_any_collector_is_on(self) -> None:
        seen = {"MHRA Inspectorate": date(2026, 8, 1)}    # 44일 > 35일
        self.assertEqual(
            _codes(evaluate_silence(last_seen=seen, run_date=RUN,
                                    source_enabled={"mhra": False, "mhra_alert": True})),
            ["MHRA Inspectorate"])
        self.assertEqual(
            evaluate_silence(last_seen=seen, run_date=RUN,
                             source_enabled={"mhra": False, "mhra_alert": False}), [])

    def test_the_2026_09_14_snapshot_after_probe_correction(self) -> None:
        """실측 재현 — 단, 러너 프로브로 **건강함이 확인된** 3종은 울리지 않아야 한다.

        이게 이 파일에서 가장 중요한 기대값이다. 초판은 PIC/S·MHRA GMP NCR·EU GMP NCR 를
        "잡아야 할 무음"으로 못박았는데, 같은 날 러너 프로브가 셋 다 OPEN 으로 판정했다
        (PIC/S 피드 최신 07-30 = Notion 마지막 행과 동일 = 있는 건 다 긁어온 상태).
        건강한 소스에 매일 경고를 내는 것은 감시의 실패이지 성공이 아니다 — 그 경고가
        같은 이슈(#956)에 올라오는 **진짜 고장**(KR egress 프록시 사망)을 묻는다.
        """
        seen = {
            "PIC/S": date(2026, 7, 30),                  # 46일 < 90 → 침묵(프로브 OPEN 확인)
            "MHRA GMP NCR": date(2026, 8, 10),           # 35일 < 90 → 침묵(프로브 OPEN 확인)
            "EU GMP NCR (EudraGMDP)": date(2026, 8, 25), # 20일 < 60 → 침묵(프로브 OPEN 확인)
            "WHO": date(2026, 8, 29),                    # 16일 > 10 → 경고(진짜 신호)
            "Health Canada": date(2026, 9, 1),           # 13일 > 10 → 경고(진짜 신호)
            "ICH": None,                                 # 룩백 내내 0건 — 스냅샷 diff 라 제외(설계상 정상)
            "Federal Register": date(2026, 9, 13),       # 정상
            "FDA 483": date(2026, 9, 12),                # 정상
        }
        got = _codes(evaluate_silence(last_seen=seen, run_date=RUN, source_enabled=ALL_ON))
        self.assertEqual(got, ["Health Canada", "WHO"])

        # 일주일 뒤(2026-09-21, 이슈 #956 이 🚨 7일 연속을 찍던 날)에도 셋은 조용해야 한다.
        week_later = _codes(evaluate_silence(last_seen=seen, run_date=date(2026, 9, 21),
                                             source_enabled=ALL_ON))
        self.assertNotIn("PIC/S", week_later)
        self.assertNotIn("MHRA GMP NCR", week_later)
        self.assertNotIn("EU GMP NCR (EudraGMDP)", week_later)

        # ★느슨해진 것이지 꺼진 게 아니다 — 임계를 넘기면 그대로 울린다.
        much_later = _codes(evaluate_silence(last_seen=seen, run_date=date(2026, 12, 1),
                                             source_enabled=ALL_ON))
        for must in ("PIC/S", "MHRA GMP NCR", "EU GMP NCR (EudraGMDP)"):
            self.assertIn(must, much_later)

    def test_thresholds_exceed_probe_verified_healthy_silence(self) -> None:
        """임계는 **"원천이 건강한데 조용하다"가 확인된 일수보다 길어야 한다.**

        2026-09-14 러너 프로브(run 34797482678)가 OPEN 으로 판정한 시점의 무음 일수다.
        이 선 아래로 임계를 다시 깎으면 건강한 소스가 또 매일 운다 — 그때는 먼저
        프로브로 원천 주기를 재라(이 테스트를 고치는 것이 아니라).
        """
        probe_verified_healthy_days = {
            "PIC/S": 46,
            "MHRA GMP NCR": 35,
            "EU GMP NCR (EudraGMDP)": 20,
        }
        by_source = {w.notion_source: w.silence_days for w in watched_sources()}
        for source, healthy_days in probe_verified_healthy_days.items():
            self.assertGreater(
                by_source[source], healthy_days,
                f"{source}: 임계 {by_source[source]}일 ≤ 프로브로 건강함이 확인된 "
                f"{healthy_days}일 무음 — 건강한 소스에 상시 경고가 난다")


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
            source_last_seen={"PIC/S": date(2026, 5, 1)},   # 136일 > 임계 90일
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
