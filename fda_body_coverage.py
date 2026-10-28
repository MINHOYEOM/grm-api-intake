"""발행 전 FDA 본문 결손 점검 — 이번 주 FDA 카드 중 본문·상세를 못 받은 건수와 비율.

배경(2026-10-27 FDA 결손 감사): 10-26 호에서 FDA 483 25건이 관찰 본문 없이 수집돼
"본문 미확보" 묶음 카드로 발행됐다. 수집 상한에 걸린 문서였고(#1209), 그 사실을 아는
신호(health 경고)는 있었지만 **발행 직전에 이번 주 브리프 기준으로 세어 보는 장치**가
없었다 — 결손은 조립 단계에서 조용히 묶음 카드로 접혔다.

이 모듈은 판정만 한다(순수 함수 + 얇은 CLI). 입력은 수집이 낸 빈슬롯 스캐폴드
(`brief_web_{date}.json`) 또는 조립된 발행본이다 — 둘 다 `grm-web-card/v1` 이다.

  · 483  : 결정론 관찰 상세(`deterministic_detail`)도 심층분석도 없는 카드 = 조립이 묶음으로
           접을 카드(`assemble_publish_brief._is_content_less_483` 와 같은 판정). 범위 밖
           (혈액·조직·임상·식품·기기) 483 은 조립이 버리므로 세지 않는다. 조립본의 묶음
           카드는 `merged_count` 만큼 센다.
  · WL   : 본문 확보 신호(`source_body_captured`)도 결정론 상세도 없는 카드.
  · 회수 : 본문 확보 신호가 없는 카드(OpenFDA 는 사유 필드가 곧 본문 — 비면 결손).
  · FR   : 인용(초록)이 없는 카드 — 관측만 하고 임계 판정에는 넣지 않는다(초록이 없는
           고시가 실재한다).

워크플로(`grm-fda-coverage-gate.yml`, `grm-web-publish.yml`)는 사실 수집만 하고 판정은
여기에 위임한다(`publish_watchdog.py` 와 같은 분리). CLI 는 항상 exit 0 — 판정은 값으로만.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from assemble_publish_brief import _is_content_less_483, _is_out_of_scope_483_card

# 임계 — 483 은 대량 공개 주에 수십 건이 들어오므로 건수·비율을 **둘 다** 넘어야 한다
# (공개 1~2건짜리 주에 스캔 1장으로 경보가 울리지 않게). WL·회수는 주 한 줌이고 정상이면
# 본문이 늘 있으므로 1건부터 본다(감사 실측: 최근 12주 발행분 WL·회수 결손 0).
DEFAULT_483_MIN_COUNT = 3
DEFAULT_483_MIN_RATIO = 0.10
DEFAULT_WL_MIN_COUNT = 1
DEFAULT_RECALL_MIN_COUNT = 1

KIND_483 = "483"
KIND_WL = "wl"
KIND_RECALL = "recall"
KIND_FR = "fr"
KIND_LABELS = {KIND_483: "FDA 483", KIND_WL: "FDA Warning Letter",
               KIND_RECALL: "OpenFDA Recall", KIND_FR: "Federal Register"}
_GATED_KINDS = (KIND_483, KIND_WL, KIND_RECALL)


@dataclass
class KindCoverage:
    total: int = 0
    missing: int = 0
    missing_ids: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return (self.missing / self.total) if self.total else 0.0


@dataclass
class CoverageReport:
    kinds: dict[str, KindCoverage] = field(default_factory=dict)
    over: list[str] = field(default_factory=list)      # 임계를 넘은 kind

    def to_dict(self) -> dict[str, Any]:
        return {
            "over": list(self.over),
            "kinds": {k: {"total": v.total, "missing": v.missing,
                          "ratio": round(v.ratio, 4), "missing_ids": list(v.missing_ids)}
                      for k, v in self.kinds.items()},
        }


def classify(card: dict[str, Any]) -> str | None:
    """FDA 카드의 kind. FDA 가 아니면 None."""
    cid = str(card.get("id") or "")
    if card.get("type_tag") == "483" or cid.startswith("fda483-"):
        return KIND_483
    if card.get("agency") != "FDA":
        return None
    card_type = str(card.get("card_type") or "")
    if card_type == "Warning Letter":
        return KIND_WL
    if card_type == "Recall":
        return KIND_RECALL
    return KIND_FR


def _is_missing(kind: str, card: dict[str, Any]) -> bool:
    if kind == KIND_483:
        return _is_content_less_483(card)
    if kind == KIND_WL:
        return not (card.get("source_body_captured") or card.get("deterministic_detail"))
    if kind == KIND_RECALL:
        return not card.get("source_body_captured")
    return not card.get("quotes")


def measure(brief: dict[str, Any], *,
            min_483_count: int = DEFAULT_483_MIN_COUNT,
            min_483_ratio: float = DEFAULT_483_MIN_RATIO,
            min_wl_count: int = DEFAULT_WL_MIN_COUNT,
            min_recall_count: int = DEFAULT_RECALL_MIN_COUNT) -> CoverageReport:
    """브리프(스캐폴드·조립본) → kind 별 결손 집계와 임계 초과 kind."""
    report = CoverageReport(kinds={k: KindCoverage() for k in KIND_LABELS})
    for card in brief.get("cards") or []:
        kind = classify(card)
        if kind is None:
            continue
        if kind == KIND_483 and _is_out_of_scope_483_card(card):
            continue            # 조립이 버리는 카드 — 발행 결손이 아니다
        weight = max(int(card.get("merged_count") or 0), 1) if kind == KIND_483 else 1
        cov = report.kinds[kind]
        cov.total += weight
        if _is_missing(kind, card):
            cov.missing += weight
            cov.missing_ids.append(str(card.get("id") or ""))
    k483 = report.kinds[KIND_483]
    if k483.missing >= min_483_count and k483.ratio >= min_483_ratio:
        report.over.append(KIND_483)
    if report.kinds[KIND_WL].missing >= min_wl_count:
        report.over.append(KIND_WL)
    if report.kinds[KIND_RECALL].missing >= min_recall_count:
        report.over.append(KIND_RECALL)
    return report


def decide(report: CoverageReport, *, intake_event: str, kst_hour: int,
           kst_minute: int = 0, recovery_attempted: bool = False,
           recover_before: tuple[int, int] = (7, 0)) -> tuple[str, str]:
    """다음 행동 = `ok` | `recover` | `alert`. 반환 = (행동, 사유).

    · 임계 미만 → ok.
    · 483 결손이 임계를 넘었고, 아직 회수를 안 했고, 정기 수집(schedule)의 산출물이고,
      Routine(07:30 KST) 전 여유가 있으면 → recover(회수 → handoff 재생성).
      WL·회수 결손에는 자동 회수 경로가 없다 → 곧장 alert.
    · 그 밖(회수 후에도 남음 · 시각 초과 · 수동 실행 산출물) → alert.
    """
    if not report.over:
        return "ok", "FDA 카드 본문 결손이 임계 미만"
    if recovery_attempted:
        return "alert", "자동 회수 후에도 결손이 임계 이상"
    if KIND_483 not in report.over:
        return "alert", "자동 회수 경로가 없는 소스의 결손(" + ", ".join(report.over) + ")"
    if intake_event != "schedule":
        return "alert", f"정기 수집이 아닌 실행({intake_event})의 산출물 — 자동 재생성 대상 아님"
    if (kst_hour, kst_minute) >= recover_before:
        return "alert", (f"{kst_hour:02d}:{kst_minute:02d} KST — Routine(07:30) 전에 handoff 를 "
                         "다시 만들 시간이 없음")
    return "recover", "483 결손이 임계 이상 — 회수 후 handoff·스캐폴드 재생성"


def issue_lines(report: CoverageReport) -> list[str]:
    """운영 경고 이슈 본문의 `- [code] …` 줄(Admin 운영 상태 센터가 이 형식을 읽는다)."""
    out: list[str] = []
    for kind in report.over:
        cov = report.kinds[kind]
        ids = ", ".join(cov.missing_ids[:10]) + (" …" if len(cov.missing_ids) > 10 else "")
        out.append(f"- [fda-body-coverage] {KIND_LABELS[kind]}: 본문 미확보 **{cov.missing}/"
                   f"{cov.total}건**({cov.ratio:.0%}) — {ids}")
    return out


def summary_markdown(report: CoverageReport, *, title: str = "FDA 카드 본문 확보 현황") -> str:
    lines = [f"### {title}", "", "| 소스 | 카드(건) | 본문 미확보 | 비율 | 임계 초과 |",
             "|---|---|---|---|---|"]
    for kind, label in KIND_LABELS.items():
        cov = report.kinds[kind]
        flag = "⚠️" if kind in report.over else ("관측만" if kind not in _GATED_KINDS else "")
        lines.append(f"| {label} | {cov.total} | {cov.missing} | {cov.ratio:.0%} | {flag} |")
    return "\n".join(lines) + "\n"


def handoff_status(run_date: str, *, token: str, db_id: str,
                   find_page: Any = None) -> str:
    """그 날짜 handoff page 의 Status(`New`=OPEN · `Processed`=CONSUMED · `Skipped`=STALE)
    또는 `missing`. handoff 재생성(수집 재실행) 전에 반드시 `New` 인지 본다 — 이미 소비된
    handoff 를 다시 쓰면(멱등성 v1 경로는 재패치한다) 발행이 끝난 주가 OPEN 으로 되살아난다."""
    import grm_handoff
    finder = find_page or grm_handoff.notion_find_handoff_page
    page = finder(token, db_id, grm_handoff.handoff_id_for(run_date))
    if not page:
        return "missing"
    return str(grm_handoff._intake_page_snapshot(page).get("status") or "missing")


def _emit(pairs: dict[str, object]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # publish_watchdog._emit 와 같은 이유(cp949)
    except (AttributeError, OSError):
        pass
    for k, v in pairs.items():
        if isinstance(v, bool):
            v = "true" if v else "false"
        print(f"{k}={v}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="발행 전 FDA 본문 결손 점검")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="브리프 JSON 의 FDA 결손을 세고 다음 행동을 판정")
    c.add_argument("--brief", required=True)
    c.add_argument("--intake-event", default="schedule")
    c.add_argument("--kst-hour", type=int, default=0)
    c.add_argument("--kst-minute", type=int, default=0)
    c.add_argument("--recovery-attempted", action="store_true")
    c.add_argument("--report-json", default="")
    c.add_argument("--summary-md", default="", help="이 경로에 요약 표를 덧붙인다(step summary)")
    c.add_argument("--issue-md", default="", help="이 경로에 경고 이슈 줄을 쓴다")
    c.add_argument("--min-483-count", type=int, default=DEFAULT_483_MIN_COUNT)
    c.add_argument("--min-483-ratio", type=float, default=DEFAULT_483_MIN_RATIO)
    h = sub.add_parser("handoff-status", help="그 날짜 handoff 의 Notion Status")
    h.add_argument("--date", required=True, help="run_date_kst YYYY-MM-DD")
    args = ap.parse_args(argv)

    if args.cmd == "handoff-status":
        import os
        try:
            status = handoff_status(args.date, token=os.environ["NOTION_TOKEN"],
                                    db_id=os.environ["NOTION_DATABASE_ID"])
        except Exception as e:  # noqa: BLE001 — 모르면 재생성하지 않는다(안전 쪽)
            status = f"error:{type(e).__name__}"
        _emit({"handoff_status": status})
        return 0

    with open(args.brief, encoding="utf-8") as fh:
        brief = json.load(fh)
    report = measure(brief, min_483_count=args.min_483_count, min_483_ratio=args.min_483_ratio)
    action, reason = decide(report, intake_event=args.intake_event, kst_hour=args.kst_hour,
                            kst_minute=args.kst_minute,
                            recovery_attempted=args.recovery_attempted)
    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as fh:
            json.dump({**report.to_dict(), "action": action, "reason": reason}, fh,
                      ensure_ascii=False, indent=1)
    if args.summary_md:
        with open(args.summary_md, "a", encoding="utf-8") as fh:
            fh.write(summary_markdown(report) + f"\n판정: `{action}` — {reason}\n")
    if args.issue_md:
        with open(args.issue_md, "w", encoding="utf-8") as fh:
            fh.write("\n".join(issue_lines(report)) + "\n")
    k483 = report.kinds[KIND_483]
    _emit({
        "action": action,
        "reason": reason,
        "over": ",".join(report.over),
        "missing_483": k483.missing,
        "total_483": k483.total,
        # 회수 대상 document_id(= 483 카드 id). 공백 구분 — 회수 워크플로 doc_ids 입력 형식.
        "recover_ids": " ".join(i for i in k483.missing_ids if i.startswith("fda483-")),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
