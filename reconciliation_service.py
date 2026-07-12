#!/usr/bin/env python3
"""수집 커버리지 reconciliation — 소스별 주간 수집량이 과거 대비 비정상 낙차인지 판정.

[신뢰성 게이트 2026-07-12] 기존 grm_health 는 *수집된 항목 내부* 추출품질만 본다
(excerpt degraded 등). 원천 대비 "이번 주 아무것도 못 받았다"(IP 차단·피드 파손·API
침묵)는 아무도 안 봤다 — 감사에서 MHRA 회수 전건 누락이 이렇게 조용히 방치됐다.

이 모듈은 순수 판정 로직만 담는다(네트워크·DB 접근 없음). 러너
(reconciliation_audit.py)가 Supabase raw_signals 에서 소스별 주간 카운트를 뽑아
넘겨주고, 이 함수가 이상치를 반환한다. 발행 파이프라인을 **차단하지 않는다** — WARN
리포트 전용(과잉경보로 신뢰가 깎이면 게이트 자체가 무시되므로 정밀도 우선).

과잉경보 방지 설계(과거 이 감사가 DEFERRED 됐던 바로 그 이유):
  - baseline(과거 주들의 중앙값)이 의미 있게 클 때만 발화 — 원래 조용한 소스
    (PIC/S·MHRA 블로그 등)는 monitored 집합에서 제외하거나 baseline 이 작아 무발화.
  - 일간 변동은 주간 합산으로 평활(openFDA 는 dataset lag 로 특정 일자 0건이 정상 —
    주간 합으로 보면 항상 >0).
  - 이력이 얇을 때(초기 몇 주)는 보수적 floor 로 폴백, floor 도 없으면 skip(무발화).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# 감시 대상 = 주(week) 단위로 사실상 항상 데이터가 있는 고volume 소스.
# 값 = 이력이 얇을 때 쓰는 보수적 주간 floor(감사 실측 기반, 실제 평균의 한참 아래).
#   OpenFDA Recall: 60건/30일 ≈ 14/주 → floor 3
#   MFDS(회수+행정처분+GMP 합산): totalCount 회수 958·행정 607 → 주 수십건, floor 5
#   FDA 483: 윈도우당 수십건 → floor 2
#   FDA Warning Letter: 주 수건 → floor 1
# 조용한 게 정상인 소스(EMA·MHRA 블로그·PIC/S·ECA·FR)는 여기 넣지 않는다(무발화).
MONITORED_FLOORS: dict[str, int] = {
    "OpenFDA Recall": 3,
    "MFDS": 5,
    "FDA 483": 2,
    "FDA Warning Letter": 1,
}

# baseline 이 이 값 이상일 때만 "완전 침묵(0건)"을 SILENT_DROP 으로 승격.
_SILENT_MIN_BASELINE = 2
# 부분 낙차(soft) 발화: 이번주 < baseline*RATIO 이고 baseline >= SOFT_MIN_BASELINE.
_SOFT_RATIO = 0.3
_SOFT_MIN_BASELINE = 8
# 이력을 baseline 으로 신뢰하기 위한 최소 관측 주 수. 미만이면 floor 로 폴백.
_MIN_HISTORY_WEEKS = 3


@dataclass(frozen=True)
class Anomaly:
    source: str
    severity: str          # "silent_drop" | "volume_drop"
    current: int
    baseline: float
    message: str


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _baseline_for(source: str, history: list[int]) -> float | None:
    """이 소스의 기대치. 이력이 충분하면 중앙값, 얇으면 floor, 둘 다 없으면 None(skip)."""
    if len(history) >= _MIN_HISTORY_WEEKS:
        med = _median(history)
        if med > 0:
            return med
        # 이력 중앙값이 0 이면(원래 조용한 소스) floor 폴백도 하지 않는다 → skip.
        return None
    floor = MONITORED_FLOORS.get(source)
    return float(floor) if floor else None


def detect_coverage_anomalies(
    current: dict[str, int],
    history: dict[str, list[int]] | None = None,
    monitored: set[str] | None = None,
) -> list[Anomaly]:
    """소스별 이번주 카운트(current)와 과거 주들(history)로 이상 낙차를 판정.

    current:  {source: 이번 주 수집 건수}
    history:  {source: [직전주, 그 전주, ...]} — 없으면 floor 기반 판정.
    monitored: 감시 대상 소스 집합(기본 MONITORED_FLOORS 키).
    반환: severity 내림차순(silent_drop 먼저) Anomaly 리스트.
    """
    history = history or {}
    monitored = monitored if monitored is not None else set(MONITORED_FLOORS)
    out: list[Anomaly] = []

    for source in sorted(monitored):
        cur = int(current.get(source, 0))
        baseline = _baseline_for(source, history.get(source, []))
        if baseline is None:
            continue  # 기대치 산정 불가(이력 얇고 floor 없음, 또는 원래 조용) → 무발화

        if cur == 0 and baseline >= _SILENT_MIN_BASELINE:
            out.append(Anomaly(
                source=source, severity="silent_drop", current=cur, baseline=baseline,
                message=(f"{source}: 이번 주 수집 0건 (기대 기준선 ≈{baseline:g}건). "
                         f"피드 파손·API 침묵·IP 차단 등 무음 실패 의심 — 원천 점검 필요."),
            ))
        elif (baseline >= _SOFT_MIN_BASELINE and 0 < cur < baseline * _SOFT_RATIO):
            out.append(Anomaly(
                source=source, severity="volume_drop", current=cur, baseline=baseline,
                message=(f"{source}: 이번 주 {cur}건 — 기준선 ≈{baseline:g}건의 "
                         f"{_SOFT_RATIO:.0%} 미만으로 급감. 부분 수집 실패 가능."),
            ))

    # silent_drop 을 먼저 노출
    out.sort(key=lambda a: (a.severity != "silent_drop", a.source))
    return out


# ---------------------------------------------------------------------------
# evidence_url 오염 스캔 (Fix B — 2026-07-12)
#
# 배경: /findings/ 의 evidence_url 이 목록/데이터셋/API 엔드포인트로 오염되는 사고가
# 2건 발생(MFDS 행정처분·회수 21건, GMP실사 2건). 생성 시점 가드(Fix A,
# grm_findings.EVIDENCE_URL_BLOCKLIST, 별도 병렬 PR)는 "새로 만드는" finding 을 막지만,
# 이미 DB 에 들어간 오염과 앞으로 나타날 미지 패턴은 주기 감시가 필요하다.
#
# 아래 패턴은 grm_findings/findings_extractors 의 정의를 import 하지 않고 이 모듈이
# 독립적으로 보유한다(의도적 설계) — 방어 계층을 분리해 생성층 가드가 뚫리거나
# 회귀해도 이 감시층이 별도로 잡는다. 감시층은 비차단 WARN 전용이라 생성층 가드보다
# 광범위해도(오탐이 있어도) 비용이 낮다 — 두 계층의 패턴이 완전히 같을 필요는 없다.
# ---------------------------------------------------------------------------

EVIDENCE_URL_BAD_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[?&]serviceKey=", "api-key-url"),
    (r"^https?://apis\.data\.go\.kr/", "api-endpoint"),
    (r"^https?://(www\.)?data\.go\.kr/", "dataset-page"),
    (r"^(?!https?://)", "non-http"),
)

# 리포트 출력용 serviceKey 마스킹. grm_common.mask_service_key 와 같은 정규식이지만
# 이 모듈은 순수 판정 로직 전용(네트워크 의존 없음)이라 grm_common(requests 의존)을
# import 하지 않고 여기서 독립 정의한다 — 위 EVIDENCE_URL_BAD_PATTERNS 와 같은 이유.
_SERVICE_KEY_RE = re.compile(r"([?&]serviceKey=)[^&]+")


def classify_evidence_url(url: str) -> str:
    """오염이면 사유 문자열, 정상이면 ''. 빈 값은 'empty'."""
    u = (url or "").strip()
    if not u:
        return "empty"
    for pattern, reason in EVIDENCE_URL_BAD_PATTERNS:
        if re.search(pattern, u):
            return reason
    return ""


def _mask_service_key(url: str) -> str:
    """sample_url 에 serviceKey 원문이 절대 남지 않도록 마스킹."""
    return _SERVICE_KEY_RE.sub(r"\1***", url)


def summarize_url_contamination(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """오염된 evidence_url 을 (source, document_type, reason) 별로 집계.

    반환: [{source, document_type, reason, n, sample_url}, ...] — 정상(reason == '')
    행은 제외. sample_url 은 그룹의 대표 URL 이며 serviceKey 는 마스킹된 채로 담긴다
    (리포트/::warning:: 문자열에 실 키가 노출되지 않도록 이 함수 출력 시점에 마스킹).
    """
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        url = str(row.get("evidence_url") or "").strip()
        reason = classify_evidence_url(url)
        if not reason:
            continue
        source = str(row.get("source") or "").strip() or "(unknown)"
        document_type = str(row.get("document_type") or "").strip() or "(unknown)"
        key = (source, document_type, reason)
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "source": source,
                "document_type": document_type,
                "reason": reason,
                "n": 1,
                "sample_url": _mask_service_key(url),
            }
        else:
            group["n"] += 1
    return sorted(
        groups.values(),
        key=lambda g: (g["reason"], g["source"], g["document_type"]),
    )
