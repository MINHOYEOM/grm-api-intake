#!/usr/bin/env python3
"""GRM Intake — 소스 헬스 판정 층 (배치3 Phase1, collect_intake 에서 verbatim 분리).

collect_intake.py 의 §3.5 단일 헬스 판정 지점을 순수 모듈로 분리한다. 네트워크·Notion
접근 0 — CollectionStats(수집 본체가 채운다)를 읽어 HealthCheckResult(failure/warning/
info)를 산출하고, 소스별 헬스 행·health JSON·요약 마크다운을 만든다. 일시성 네트워크
오류(_is_transient_source_error)는 warning(exit 0)으로 강등하는 단일 분류기다.

collect_intake 로의 역참조는 없다(단방향: collect_intake → grm_health). 시그니처의
`CollectionStats` 주석은 `from __future__ import annotations` 로 지연 평가되는 문자열이라
런타임 import 가 불필요하다. 기존 참조 경로(collect_intake._evaluate_health 등)는
collect_intake 가 이 모듈을 재수출해 보존한다(하위호환·테스트 무수정).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from grm_common import INTAKE_SOURCE_SPECS, log


@dataclass
class HealthFinding:
    level: str
    code: str
    source: str
    message: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "code": self.code,
            "source": self.source,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class HealthCheckResult:
    status: str = "ok"
    exit_code: int = 0
    failures: list[HealthFinding] = field(default_factory=list)
    warnings: list[HealthFinding] = field(default_factory=list)
    infos: list[HealthFinding] = field(default_factory=list)

    def add_failure(self, code: str, source: str, message: str, detail: str = "") -> None:
        self.failures.append(HealthFinding("failure", code, source, message, detail))

    def add_warning(self, code: str, source: str, message: str, detail: str = "") -> None:
        self.warnings.append(HealthFinding("warning", code, source, message, detail))

    def add_info(self, code: str, source: str, message: str, detail: str = "") -> None:
        self.infos.append(HealthFinding("info", code, source, message, detail))

    def finalize(self) -> "HealthCheckResult":
        if self.failures:
            self.status = "failure"
            self.exit_code = 1
        elif self.warnings:
            self.status = "warning"
            self.exit_code = 0
        else:
            self.status = "ok"
            self.exit_code = 0
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "failure_count": len(self.failures),
            "warning_count": len(self.warnings),
            "info_count": len(self.infos),
            "failures": [finding.to_dict() for finding in self.failures],
            "warnings": [finding.to_dict() for finding in self.warnings],
            "infos": [finding.to_dict() for finding in self.infos],
        }


_TRANSIENT_ERROR_MARKERS = [
    "timeout", "timed out", "connecttimeouterror", "readtimeouterror",
    "connectionreseterror", "connection reset", "connection aborted",
    "remotedisconnected", "max retries exceeded", "temporary failure",
    "name resolution", "nameresolutionerror", "http 429", "rate-limit",
    "429 client error", "too many requests",
    "http 502", "http 503", "http 504", "bad gateway",
    "service unavailable", "gateway timeout",
]
_MFDS_PUBLIC_ENDPOINT_SOURCE_CODES = {"mfds-rss", "mfds-gmp-inspection"}
_MFDS_FEATURE_SOURCE_CODES = _MFDS_PUBLIC_ENDPOINT_SOURCE_CODES | {
    "mfds-recall",
    "mfds-admin",
    "mfds-law",
    "mfds-gmp-cert",
    "mfds-safety-letter",
}
# ICH/WHO/HC 도 외부 공개 endpoint(admin.ich.org · extranet.who.int · recalls-rappels.canada.ca
# 정적 JSON)라 GitHub-hosted IP 간헐 차단·timeout·5xx 가 발생한다. 네트워크성 일시 오류는 MFDS
# 공개 endpoint 와 동일하게 warning(exit 0)으로 강등 — 설정·구조 오류는 마커 미포함이라 여전히
# failure. 2026-06-05 활성화 때 누락된 스코프 확장(T1).
_GLOBAL_PUBLIC_SOURCE_CODES = {"ich", "who", "health-canada", "fda483"}
# ── [소스 오류 보고 누락 수리 2026-07-27] ───────────────────────────────────────
# 아래 5종(EMA·MHRA·PIC/S·ECA·FDA WL)은 **오류가 나도 경고가 나갈 경로가 아예 없었다.**
# `enabled_source_failures`(유일하게 소비되는 소스별 보고 리스트)에 빠져 있었고, 이들이
# 등장하는 `phase2_source_states` 는 ① `if not phase1_fr_active and not phase1_recall_active`
# 안에 갇혀 FR 이 도는 정상 운영에선 실행조차 안 되고 ② 실행돼도 "전 소스가 다 죽었나"
# 집계 판정에만 쓰인다. 실제 피해: ECA RSS 가 2026-07-20~07-26 **7일 연속** 실패했는데
# intake run 은 매번 success, 경고 이슈에도 한 줄도 안 올라왔다(발견은 사람이 브리프에서
# ECA 카드가 사라진 걸 눈치챈 뒤였다).
#
# **경고 등급으로 배선한다(failure 아님)** — 의도적 선택이다:
#   · `add_failure` 는 `exit_code=1` → intake run 적색인데, `grm-web-publish.yml` 은 스캐폴드를
#     `gh run list --status success` 로 고른다. 즉 부차 피드 하나가 죽으면 그 날 run 이 배제돼
#     **월요일 발행이 스캐폴드 날짜 불일치로 통째로 막힌다**(`test -f` 하드 실패).
#     가시성을 얻으려다 발행을 멈추는 건 잘못된 교환이다.
#   · 목적은 **차단이 아니라 표면화**다. 경고는 `GRM Intake 운영 경고` 이슈에 하루 안에
#     올라오므로 ECA 도 07-20 당일 잡혔을 것이다.
#   · 선례와 일관: `aged-unconsumed-new` 도 같은 이유로 warning(exit 0)이다.
# 네트워크성 일시 오류와 구조적 오류를 문구로 구분하지 않는다 — 둘 다 경고로 올리되
# detail 에 원문 오류를 실어 사람이 판단한다(오분류로 침묵하는 것보다 낫다).
_WARN_ONLY_SOURCE_CODES = {"ema", "mhra", "pics", "eca", "wl"}
_TRANSIENT_ELIGIBLE_SOURCE_CODES = _MFDS_FEATURE_SOURCE_CODES | _GLOBAL_PUBLIC_SOURCE_CODES
# 403 transient 적격: 키 없는 공개 endpoint 만(WAF/IP 차단성). data.go.kr API 403 은
# 키/서비스 권한 문제 가능성이 높아 failure 유지.
_PUBLIC_ENDPOINT_403_SOURCE_CODES = _MFDS_PUBLIC_ENDPOINT_SOURCE_CODES | _GLOBAL_PUBLIC_SOURCE_CODES


def _is_transient_source_error(code: str, detail: str) -> bool:
    """Return True for temporary network/WAF-like source failures."""
    text = (detail or "").lower()
    if not text or "환경변수 필요" in text or "api key" in text or "service_key" in text:
        return False
    if code not in _TRANSIENT_ELIGIBLE_SOURCE_CODES:
        return False
    if any(marker in text for marker in _TRANSIENT_ERROR_MARKERS):
        return True
    # MFDS/nedrug·ICH/WHO/HC public HTML/RSS endpoints can intermittently block
    # GitHub-hosted IPs. Keep data.go.kr API 403s as failures because they usually
    # mean key/service permission.
    if code in _PUBLIC_ENDPOINT_403_SOURCE_CODES and (
        "http 403" in text or "403 forbidden" in text or "403 client error" in text
    ):
        return True
    return False


def _source_health_rows(stats: CollectionStats) -> list[dict[str, Any]]:
    """[배치6 Phase2] grm_common.INTAKE_SOURCE_SPECS(수집 소스 레지스트리)로부터 소스별
    health row 를 결정론 생성한다. 스칼라 CollectionStats 필드를 getattr 로 읽으며, 순서·키·
    값은 기존 수제 dict 리스트와 byte 동일(_write_health_json 은 sort_keys=True 라 dict 내부
    키 순서는 무관하나 리스트 순서는 레지스트리 순서로 보존). fr/recall 은 truncated 를,
    mfds_gmp_inspection 은 parse_status/deficiency/manual_review/page_warnings 를 추가한다.
    """
    rows: list[dict[str, Any]] = []
    for spec in INTAKE_SOURCE_SPECS:
        p = spec.prefix
        row: dict[str, Any] = {
            "key": p,
            "label": spec.health_label,
            "fetched": getattr(stats, f"{p}_fetched"),
            "inserted": getattr(stats, f"{p}_inserted"),
            "skip_dup": getattr(stats, f"{p}_skipped_dup"),
            "failed": getattr(stats, f"{p}_insert_failed"),
            "error": getattr(stats, f"{p}_error"),
            "error_msg": getattr(stats, f"{p}_error_msg"),
        }
        if spec.has_truncated:
            row["truncated"] = getattr(stats, f"{p}_truncated")
        if p == "mfds_gmp_inspection":
            row["parse_status"] = dict(stats.mfds_gmp_inspection_parse_status)
            row["deficiency"] = dict(stats.mfds_gmp_inspection_deficiency)
            row["manual_review"] = stats.mfds_gmp_inspection_manual_review
            row["page_warnings"] = list(stats.mfds_gmp_inspection_page_warnings)
        rows.append(row)
    return rows


def _evaluate_health(
    *,
    stats: CollectionStats,
    active: set[str],
    enable_search: bool,
    enable_mfds: bool,
    enable_mfds_law: bool,
    enable_mfds_recall: bool,
    enable_mfds_admin: bool,
    enable_mfds_gmp_cert: bool,
    enable_mfds_safety_letter: bool,
    enable_mfds_gmp_inspection: bool,
    enable_ich: bool,
    enable_who: bool,
    enable_hc: bool,
    enable_fda483: bool,
    enable_moleg_api: bool,
    enable_scrape: bool,
    event_name: str,
    emit_routine_handoff: bool,
    handoff_emitted: bool,
    handoff_failed: bool,
    handoff_error_msg: str,
    modality_preflight_disabled: bool = False,
    handoff_idem_preflight_disabled: bool = False,
    handoff_idem_effective: bool = False,
    aged_unconsumed_new: int = 0,
    aged_new_query_error: str = "",
    handoff_window_days: int = 0,
) -> HealthCheckResult:
    health = HealthCheckResult()

    if modality_preflight_disabled:
        health.add_warning(
            "modality-preflight-degraded",
            "Notion",
            "ENABLE_MODALITY_TAG=true 이나 'Modality' 스키마 불일치로 태그 기록 자동 비활성화",
            "Notion Intake DB 에 'Modality'(Select: Chemical/Biologic/Other) 속성을 생성하세요. "
            "수집은 정상 진행됨.",
        )

    if handoff_idem_preflight_disabled:
        health.add_warning(
            "handoff-idem-preflight-degraded",
            "GRM Handoff",
            "ENABLE_HANDOFF_IDEMPOTENCY_V2=true 이나 'Handoff Ref' 스키마 불일치로 "
            "v1(날짜 윈도우) 경로 폴백",
            "Notion Intake DB 에 'Handoff Ref'(Rich text) 속성을 생성하세요. "
            "이번 실행 handoff 는 기존 v1 멱등성으로 진행됨.",
        )

    if stats.has_insert_failures():
        health.add_failure(
            "notion-insert-failed",
            "Notion",
            f"Notion insert 최종 실패 {stats.total_insert_failures()}건",
            "해당 항목은 이번 주 다이제스트에서 누락될 수 있습니다.",
        )
    if handoff_failed:
        health.add_failure(
            "handoff-failed",
            "GRM Handoff",
            "Routine handoff 생성 실패",
            handoff_error_msg[:240],
        )

    # B1 임시 방어 ②: 윈도우 밖 미소비 New 잔존 = Routine 누락/지연 의심(침묵 누락 방지).
    # warning 이므로 exit 0 유지(§3.5) — scheduled run 은 기존 운영 경고 Issue 경로로 누적.
    # Codex P2(A안): 멱등성 v2 effective 면 노후 New 는 ref 기반 소비 쿼리(날짜 하한
    # 없음)가 자동 재투입하므로 경고 미발생(정보성 로그는 main 이 출력). reconcile
    # 고아/실패는 emit 경로의 WARN 로그로 별도 표면화된다.
    if aged_unconsumed_new > 0 and not handoff_idem_effective:
        health.add_warning(
            "aged-unconsumed-new",
            "GRM Handoff",
            f"handoff 윈도우({handoff_window_days}일) 밖 미소비 New row {aged_unconsumed_new}건",
            "주간 Routine 누락/지연 의심 — 수동 확인 후 처리(또는 윈도우 조정) 필요.",
        )
    if aged_new_query_error:
        health.add_warning(
            "aged-unconsumed-new-query-failed",
            "GRM Handoff",
            "노후 미소비 New row 카운트 조회 실패 — 이번 실행은 누락 감시 불가",
            aged_new_query_error[:240],
        )

    handoff_only_success = (
        emit_routine_handoff and handoff_emitted and
        (not active or active == {"mfds"}) and not any([
            enable_mfds,
            enable_mfds_law,
            enable_mfds_recall,
            enable_mfds_admin,
            enable_mfds_gmp_cert,
            enable_mfds_safety_letter,
            enable_mfds_gmp_inspection,
            enable_ich,
            enable_who,
            enable_hc,
            enable_fda483,
            enable_search,
        ])
    )
    if handoff_only_success:
        health.add_info(
            "handoff-only",
            "GRM Handoff",
            "handoff-only 실행 완료",
            "source fetch 비활성 상태를 성공으로 처리",
        )
    else:
        phase1_fr_active = "fr" in active
        phase1_recall_active = "recall" in active
        if phase1_fr_active and phase1_recall_active and stats.fr_error and stats.recall_error:
            health.add_failure(
                "phase1-all-failed",
                "Phase 1",
                "Federal Register와 OpenFDA Recall이 모두 실패",
                "핵심 공식 API 2개가 모두 실패해 workflow fail로 처리합니다.",
            )

        enabled_source_failures = [
            (enable_search and stats.search_error, "brave-search", "Brave Search", stats.search_error_msg),
            (enable_mfds and stats.mfds_error, "mfds-rss", "MFDS RSS", stats.mfds_error_msg),
            (enable_mfds_law and stats.mfds_law_error, "mfds-law", "MFDS Law/Admrul", stats.mfds_law_error_msg),
            (enable_mfds_recall and stats.mfds_recall_error, "mfds-recall", "MFDS Recall", stats.mfds_recall_error_msg),
            (enable_mfds_admin and stats.mfds_admin_error, "mfds-admin", "MFDS Admin", stats.mfds_admin_error_msg),
            (
                enable_mfds_gmp_cert and stats.mfds_gmp_cert_error,
                "mfds-gmp-cert",
                "MFDS GMP Certificate",
                stats.mfds_gmp_cert_error_msg,
            ),
            (
                enable_mfds_safety_letter and stats.mfds_safety_letter_error,
                "mfds-safety-letter",
                "MFDS Safety Letter",
                stats.mfds_safety_letter_error_msg,
            ),
            (
                enable_mfds_gmp_inspection and stats.mfds_gmp_inspection_error,
                "mfds-gmp-inspection",
                "MFDS GMP Inspection",
                stats.mfds_gmp_inspection_error_msg,
            ),
            (enable_ich and stats.ich_error, "ich", "ICH", stats.ich_error_msg),
            (enable_who and stats.who_error, "who", "WHO", stats.who_error_msg),
            (enable_hc and stats.hc_error, "health-canada", "Health Canada", stats.hc_error_msg),
            (enable_fda483 and stats.fda483_error, "fda483", "FDA 483", stats.fda483_error_msg),
            # [2026-07-27] 공개 RSS/HTML 5종 — 종전엔 이 리스트에 없어 오류가 나도 보고 경로가
            # 없었다(ECA 7일 침묵). 게이팅은 phase2 와 동일하게 `active` 기준.
            # 등급은 _WARN_ONLY_SOURCE_CODES 로 항상 warning — 이유는 그 상수 주석 참조.
            ("ema" in active and stats.ema_error, "ema", "EMA RSS", stats.ema_error_msg),
            ("mhra" in active and stats.mhra_error, "mhra", "MHRA RSS", stats.mhra_error_msg),
            ("pics" in active and stats.pics_error, "pics", "PIC/S RSS", stats.pics_error_msg),
            ("eca" in active and stats.eca_error, "eca", "ECA Academy RSS", stats.eca_error_msg),
            ("wl" in active and stats.wl_error, "wl", "FDA Warning Letters", stats.wl_error_msg),
        ]

        if not phase1_fr_active and not phase1_recall_active:
            phase2_source_states = [
                ("ema" in active, "ema", "EMA RSS", stats.ema_error, stats.ema_error_msg),
                ("mhra" in active, "mhra", "MHRA RSS", stats.mhra_error, stats.mhra_error_msg),
                ("pics" in active, "pics", "PIC/S RSS", stats.pics_error, stats.pics_error_msg),
                ("eca" in active, "eca", "ECA Academy RSS", stats.eca_error, stats.eca_error_msg),
                ("wl" in active, "wl", "FDA Warning Letters", stats.wl_error, stats.wl_error_msg),
                (enable_mfds, "mfds-rss", "MFDS RSS", stats.mfds_error, stats.mfds_error_msg),
                (enable_mfds_law, "mfds-law", "MFDS Law/Admrul", stats.mfds_law_error, stats.mfds_law_error_msg),
                (enable_mfds_recall, "mfds-recall", "MFDS Recall", stats.mfds_recall_error, stats.mfds_recall_error_msg),
                (enable_mfds_admin, "mfds-admin", "MFDS Admin", stats.mfds_admin_error, stats.mfds_admin_error_msg),
                (
                    enable_mfds_gmp_cert,
                    "mfds-gmp-cert",
                    "MFDS GMP Certificate",
                    stats.mfds_gmp_cert_error,
                    stats.mfds_gmp_cert_error_msg,
                ),
                (
                    enable_mfds_safety_letter,
                    "mfds-safety-letter",
                    "MFDS Safety Letter",
                    stats.mfds_safety_letter_error,
                    stats.mfds_safety_letter_error_msg,
                ),
                (
                    enable_mfds_gmp_inspection,
                    "mfds-gmp-inspection",
                    "MFDS GMP Inspection",
                    stats.mfds_gmp_inspection_error,
                    stats.mfds_gmp_inspection_error_msg,
                ),
                (enable_ich, "ich", "ICH", stats.ich_error, stats.ich_error_msg),
                (enable_who, "who", "WHO", stats.who_error, stats.who_error_msg),
                (enable_hc, "health-canada", "Health Canada", stats.hc_error, stats.hc_error_msg),
                (enable_fda483, "fda483", "FDA 483", stats.fda483_error, stats.fda483_error_msg),
            ]
            active_phase2_sources = [row for row in phase2_source_states if row[0]]
            if active_phase2_sources and all(row[3] for row in active_phase2_sources):
                non_transient = [
                    row for row in active_phase2_sources
                    if not _is_transient_source_error(row[1], row[4])
                ]
                if non_transient:
                    health.add_failure(
                        "all-active-sources-failed",
                        "Collector",
                        "모든 활성 소스가 실패",
                        "Phase 1 소스가 비활성인 실행에서 활성 소스가 모두 error 상태입니다.",
                    )
                else:
                    health.add_warning(
                        "all-active-sources-transient",
                        "Collector",
                        "모든 활성 소스가 일시 네트워크 오류로 실패",
                        "Phase 1 소스가 비활성인 단독 실행이므로 workflow는 warning으로 처리합니다.",
                    )

        for failed, code, source, detail in enabled_source_failures:
            if failed:
                if code in _WARN_ONLY_SOURCE_CODES:
                    # 표면화는 하되 run 을 적색으로 만들지 않는다(발행 스캐폴드 선택이
                    # `--status success` 라 적색 = 그 주 발행 차단 — 상수 주석 참조).
                    health.add_warning(
                        f"source-error:{code}",
                        source,
                        f"{source} 수집 오류 — 이 실행에서 해당 소스 0건",
                        detail[:240],
                    )
                elif _is_transient_source_error(code, detail):
                    health.add_warning(
                        f"transient-source-error:{code}",
                        source,
                        f"{source} 일시 수집 오류",
                        detail[:240],
                    )
                else:
                    health.add_failure(
                        f"enabled-source-error:{code}",
                        source,
                        f"{source} 활성 상태에서 수집 오류",
                        detail[:240],
                    )

    if event_name == "schedule" and enable_moleg_api:
        health.add_warning(
            "moleg-enabled-on-schedule",
            "MFDS ogLmPp",
            "scheduled run에서 ENABLE_MOLEG_API=true 감지",
            "운영 원칙은 ENABLE_MOLEG_API=false 유지입니다. workflow_dispatch opt-in은 허용됩니다.",
        )
    if enable_scrape:
        health.add_warning(
            "scrape-enabled-unimplemented",
            "Web Scrape",
            "ENABLE_SCRAPE=true 이지만 Web Scrape 수집기는 아직 미구현",
            "현재 실행에서는 건너뜁니다.",
        )
    if stats.fr_truncated:
        health.add_warning(
            "fr-truncated",
            "Federal Register",
            "Federal Register pagination 안전 상한 도달",
            "일부 항목 누락 가능성이 있어 수동 확인이 필요합니다.",
        )
    if stats.recall_truncated:
        health.add_warning(
            "recall-truncated",
            "OpenFDA Recall",
            "OpenFDA Recall pagination 안전 상한 도달",
            "일부 항목 누락 가능성이 있어 수동 확인이 필요합니다.",
        )
    if enable_mfds_gmp_inspection and stats.mfds_gmp_inspection_manual_review:
        health.add_warning(
            "gmp-attachment-manual-review",
            "MFDS GMP Inspection",
            f"GMP 실태조사 첨부 {stats.mfds_gmp_inspection_manual_review}건 수동 확인 필요",
            f"parse_status={stats.mfds_gmp_inspection_parse_status}",
        )
    for warning in stats.mfds_gmp_inspection_page_warnings:
        health.add_warning(
            "gmp-pagination-warning",
            "MFDS GMP Inspection",
            "GMP 실태조사 페이지네이션 경고",
            warning[:240],
        )

    # WHY-1 P1: excerpt 실패/cap 표면화. 카드 자체는 graceful degrade(링크/메타 카드
    # 유지)이므로 warning-only — failure 승격 금지(§3.5, exit 0 유지). flag off 면
    # 카운터가 전부 0 이라 finding 미발생(무변경).
    if stats.whopir_excerpt_failed > 0 or stats.whopir_excerpt_capped > 0:
        degraded = []
        if stats.whopir_excerpt_failed:
            degraded.append(f"추출 실패 {stats.whopir_excerpt_failed}건")
        if stats.whopir_excerpt_capped:
            degraded.append("fetch cap 도달(이후 항목 excerpt 생략)")
        health.add_warning(
            "whopir-excerpt-degraded",
            "WHO WHOPIR",
            f"WHOPIR 결함 excerpt {' · '.join(degraded)} — 링크 카드 유지",
            f"attempted={stats.whopir_excerpt_attempted} "
            f"failed={stats.whopir_excerpt_failed} "
            f"capped={bool(stats.whopir_excerpt_capped)}",
        )
    # [실사일 결손 2026-08-10] WHOPIR 실사일 미추출은 excerpt 결손과 성격이 다르다 —
    # excerpt 는 카드가 얇아질 뿐이지만 실사일이 비면 published_date 필수 검증에 걸려
    # **그 문서의 findings raw_signals 가 아예 안 생긴다**(조용한 소실). 다만 항목은 링크
    # 카드로 남고 전건 미추출은 수집기가 error 로 올리므로 여기서는 warning-only.
    if stats.whopir_dateless > 0:
        health.add_warning(
            "whopir-inspection-date-missing",
            "WHO WHOPIR",
            f"WHOPIR 실사일 {stats.whopir_dateless}건 미추출 — 카드 날짜 미확인 + "
            "해당 건 findings raw_signals 미생성",
            f"dateless={stats.whopir_dateless}",
        )
    if stats.wl_body_failed > 0:
        health.add_warning(
            "wl-body-degraded",
            "FDA WL",
            f"WL 본문 excerpt {stats.wl_body_failed}건 추출 실패 — 메타 카드 유지",
            f"attempted={stats.wl_body_attempted} failed={stats.wl_body_failed}",
        )
    # WHY-1 #3 P1: 483 excerpt 실패/cap 표면화(WHOPIR/WL 와 동형 warning-only·flag off 면
    # 카운터 0 → 미발생). 카드 자체는 graceful degrade(메타 카드 유지)라 failure 승격 금지.
    if stats.fda483_excerpt_failed > 0 or stats.fda483_excerpt_capped > 0:
        degraded = []
        if stats.fda483_excerpt_failed:
            degraded.append(f"추출 실패 {stats.fda483_excerpt_failed}건")
        if stats.fda483_excerpt_capped:
            degraded.append("fetch cap 도달(이후 항목 excerpt 생략)")
        health.add_warning(
            "fda483-excerpt-degraded",
            "FDA 483",
            f"483 결함 excerpt {' · '.join(degraded)} — 메타 카드 유지",
            f"attempted={stats.fda483_excerpt_attempted} "
            f"failed={stats.fda483_excerpt_failed} "
            f"capped={bool(stats.fda483_excerpt_capped)}",
        )
    # [OCR 침묵 제거 2026-07-30] 두 신호를 warning 으로 승격한다. 왜 warning 인가:
    # 수집 자체는 graceful degrade 하므로(메타 카드 유지) failure 로 올리면 12개 소스의
    # 정상 수집분까지 실패로 물든다 — 다른 483 degrade 신호들과 동형.
    #
    # ① engine-missing: 러너에 tesseract 가 없다. 문서 한 건의 사정이 아니라 환경 사정이고,
    #    **환경을 고치면 되찾을 수 있는** 결손이다. 2026-07-30 실측: grm-findings-backfill-fetch
    #    가 이 스텝을 못 받아 하루 3회 무인으로 31건을 빈 본문으로 적재했는데 신호가 없었다.
    # ② budget-exhausted: 실행당 OCR 페이지 예산 소진. 소진 뒤 문서는 OCR 없이 지나가고
    #    dedup 때문에 재시도 기회가 없다 — 상한이 조용히 커버리지를 깎는 것을 드러낸다.
    if stats.fda483_ocr_engine_unavailable > 0:
        health.add_warning(
            "fda483-ocr-engine-missing",
            "FDA 483",
            f"OCR 엔진 사용 불가 — 스캔 483 {stats.fda483_ocr_engine_unavailable}건이 "
            "본문 없이 처리됨(환경 수리로 회수 가능)",
            f"reason={stats.fda483_ocr_engine_reason} — 러너에 tesseract-ocr + "
            "tesseract-ocr-eng 설치 여부 확인(.github/actions/setup-ocr)",
        )
    if stats.fda483_ocr_exhausted:
        health.add_warning(
            "fda483-ocr-budget-exhausted",
            "FDA 483",
            f"OCR 페이지 예산 소진 — 이후 스캔 483 {stats.fda483_ocr_budget_skipped}건이 "
            "본문 없이 처리됨(dedup 때문에 재시도 기회 없음)",
            f"pages_used={stats.fda483_ocr_pages_used} "
            f"budget_skipped={stats.fda483_ocr_budget_skipped} — "
            # ★처방 순서가 중요하다(2026-08-02 실측). 종전 문구는 "예산 상향"만 말했는데,
            #   당시 실제 원인은 **이미 가진 문서를 매일 다시 OCR** 하는 것이었다(신규 1건에
            #   OCR 200쪽 전량 소진). 예산을 올리면 낭비만 커진다. 먼저 기보유 건너뛰기가
            #   살아 있는지 보고(로그의 `기보유 건너뜀 N` / LAST_HEALTH.fda483_skipped_known),
            #   그게 0 이면 dedup 사전조회가 실패해 전량 재수집 폴백으로 돌고 있다는 뜻이다.
            "① 로그의 `기보유 건너뜀` 이 0 이면 dedup 사전조회 실패(전량 재수집 폴백) — "
            "그것부터 확인. ② 신규 유입이 실제로 많을 때만 FDA483_OCR_PAGE_BUDGET 상향",
        )
    if stats.fda483_observations_failed > 0:
        health.add_warning(
            "fda483-observations-degraded",
            "FDA 483",
            f"483 Observation 상세 추출 {stats.fda483_observations_failed}건 실패 — 요약카드 유지",
            f"enabled={stats.fda483_observations_enabled} "
            f"attempted={stats.fda483_observations_attempted} "
            f"extracted={stats.fda483_observations_extracted} "
            f"warnings={stats.fda483_observations_warnings[:3]}",
        )
    # 완전성 표면화(백본 3단 — 2026-07-17 Akamai 봇차단 대응): warning-only.
    # degraded = 3차 정적 HTML(부분) 또는 2차 JSON 동결(stale) 의심 — 전수성 미보장.
    # degraded 아님 + legacy-json = 2차 백본이 전수 수집을 대신함(수집 자체는 완전) —
    # 1차 차단 고착 여부를 지켜보라는 관측 신호.
    if stats.fda483_source_degraded:
        if stats.fda483_backbone == "legacy-json":
            health.add_warning(
                "fda483-source-degraded",
                "FDA 483",
                "483 전수 JSON 백본 동결(stale) 의심 — 최신 publish 가 윈도우 시작 이전(완전성 미보장)",
                "1차 DataTables 불가 상태에서 2차 전수 JSON 의 최신 발행일이 수집 윈도우보다 "
                "오래됨 — 엔드포인트 갱신 정지 가능성. in-window 483 누락 위험, 수동 점검 권장.",
            )
        else:
            health.add_warning(
                "fda483-source-degraded",
                "FDA 483",
                "483 DataTables·전수 JSON 모두 실패 — 정적 HTML 10행 fallback 으로 부분 수집(완전성 미보장)",
                "OII 리딩룸 DataTables AJAX 와 전수 JSON 백본이 모두 응답하지 않아 HTML 본문 "
                "10행으로 폴백. in-window 483 이 누락됐을 수 있음 — 다음 실행 복구 확인/수동 점검 권장.",
            )
    elif stats.fda483_backbone == "legacy-json":
        health.add_warning(
            "fda483-backbone-json-fallback",
            "FDA 483",
            "483 1차 DataTables 불가 — 2차 전수 JSON 백본으로 수집(전수·신선도 정상)",
            "리딩룸 페이지/AJAX 접근 실패(Akamai 봇차단 계열)로 2차 백본이 가동됨. 수집 완전성은 "
            "유지 — 지속되면 차단 고착 여부 점검(1차 복구 시 자동 원복).",
        )

    return health.finalize()


def _write_health_json(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    except OSError as e:
        log("WARN", f"health JSON 쓰기 실패: {e}")


def _write_health_summary(f: Any, health: HealthCheckResult, health_path: str) -> None:
    f.write("\n## GRM Intake Health Check\n\n")
    f.write(f"- Status: `{health.status}`\n")
    f.write(f"- Exit code: `{health.exit_code}`\n")
    if health_path:
        f.write(f"- Health JSON: `{health_path}`\n")
    for title, findings in [
        ("Failures", health.failures),
        ("Warnings", health.warnings),
        ("Info", health.infos),
    ]:
        if not findings:
            continue
        f.write(f"\n### {title}\n")
        for finding in findings:
            detail = f" — {finding.detail}" if finding.detail else ""
            f.write(f"- `{finding.code}` · {finding.source}: {finding.message}{detail}\n")
