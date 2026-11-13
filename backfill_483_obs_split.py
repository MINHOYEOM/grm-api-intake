#!/usr/bin/env python3
"""[483 관찰 분할 소급 2026-11-13] 앞 관찰 상세가 다음 관찰(들)을 삼킨 저장 raw 를 PDF 없이 나눈다.

근거: _ops/reports/GRM_483관찰분할결함_dry_2026-11-13.md.
옛 수집기(#1618 이전)는 `OBSERVATION 7.⏎Out-of-specification…` 처럼 표제 번호 뒤가 마침표 조각이면
앵커를 상호참조로 보고 기각해, 그 관찰 전문을 **앞 관찰 상세 끝에 합쳤다**. 수집기는 이미 고쳐졌지만
이미 저장된 raw 는 그대로라, 앞 관찰 카드 상세에 다음 관찰이 통째로 들어 있고(번역도 합쳐진 채)
삼켜진 관찰은 행이 없거나(정보 손실) 조각 표제 행으로만 남아 있다.

이 도구는 저장된 `fda_483_observations[].detail` 안에서 **현행 수집기와 같은 앵커 규칙**
(`collect_fda_483._select_observation_anchors` ①~③ + 조각 표제 구제 · `_observations_from_anchors` 의
표제 게이트)을 통과하는 `OBSERVATION N` 표지만 찾아 그 자리에서 나눈다. 원문 전문이 없어도 된다.

덧붙인 조건(저장 배열 단위 — 상세 한 칸만 보는 앵커 규칙이 모르는 것):
  · 번호가 그 상세를 가진 관찰 번호보다 커야 한다(앞 관찰이 뒤 관찰을 삼킨다).
  · 그 번호에 **정상 표제**(30자 이상 또는 영단어 3개 이상) 관찰이 배열에 없어야 한다(고아 번호).
    상호참조는 참조 대상이 정상 표제로 따로 있으므로 여기서도 한 번 더 걸러진다.
  · 상세에 남는 줄이 같은 줄 산문 낱말로 끝나면(`…endotoxins OBSERVATION 7.`) 앵커 규칙이 문장 중간
    참조로 보고 버린다 — 저장 상세는 줄바꿈이 접혀 있어 원문 줄 머리 여부를 알 수 없으므로 **나누지 않는다**
    (덜 나눈다). 그런 문서는 원문 재추출 대상으로 남긴다.

바꾸는 것: 앞 관찰 상세는 표지 앞까지로 **옮겨 낸다**(지우는 글자 없음 — 표지 뒤 전문은 새 관찰 칸으로 간다).
  같은 번호의 조각 표제 관찰('.'·'A.')이 이미 그 내용을 상세 첫머리에 갖고 있으면 새 관찰을 더하지 않는다
  (중복만 걷는다 — 표제는 PR #1614 추출기 규칙이 상세 첫 문장으로 되살린다).
바꾸지 않는 것: 표제·번호·다른 관찰·원문 전문·실사관. findings 행은 이 도구가 쓰지 않는다 —
  새 관찰 행은 추출기 재실행(finding_id = raw_signal_id + 관찰 번호 + 표제 해시)으로, 앞 관찰 행의
  detail_text·국문은 `findings_detail_export.py --mode stale` 경로로 다시 맞춘다.
기본은 dry-run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

_OBS_KEY = "fda_483_observations"
_TITLED_MIN_LEN = 30          # findings_extractors._FDA483_FRAGMENT_MAX_LEN 과 같은 문턱
_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_DUP_PROBE = 40               # 조각 관찰 상세 첫머리와 새 표제를 견줄 앞 글자 수(공백 접음)


def _collector():
    import collect_fda_483 as c   # 지연 import — 테스트가 모듈 함수를 바꿔 끼울 수 있게
    return c


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _is_titled(deficiency: Any) -> bool:
    """정상 표제인가 — 추출기의 조각 표제 판정(30자 미만·영단어 3개 미만)의 반대."""
    d = _norm(deficiency)
    return len(d) >= _TITLED_MIN_LEN or len(_WORD_RE.findall(d)) >= 3


def _num(value: Any) -> int | None:
    s = str(value or "").strip()
    return int(s) if s.isdigit() else None


@dataclass
class SplitPlan:
    host_index: int
    host_number: str
    cut_at: int                      # 원 상세에서 첫 표지가 시작하는 글자 위치
    moved_chars: int                 # 앞 관찰 상세에서 옮겨 나간 글자 수
    added: list[dict[str, str]] = field(default_factory=list)       # 새로 더한 관찰
    deduped: list[dict[str, str]] = field(default_factory=list)     # 조각 관찰이 이미 가진 것(더하지 않음)


def _fragment_holder(observations: list[dict[str, Any]], number: str, heading: str) -> int | None:
    """같은 번호의 조각 표제 관찰 중 상세 첫머리(조각 머리를 걷은 뒤)가 heading 으로 시작하는 것의 위치."""
    c = _collector()
    probe = _norm(heading)[:_DUP_PROBE]
    if not probe:
        return None
    for j, o in enumerate(observations):
        if str(o.get("number") or "").strip() != number or _is_titled(o.get("deficiency")):
            continue
        head, rest, _ = c._split_past_fragment_head(str(o.get("detail") or ""))
        if _norm(f"{head} {rest}").startswith(probe):
            return j
    return None


def plan_host_split(observations: list[dict[str, Any]], index: int,
                    hints: dict[str, str] | None = None) -> tuple[SplitPlan | None, list[dict[str, str]]]:
    """관찰 하나(index)의 상세에서 삼켜진 관찰을 찾는다(순수 함수). (계획, 새 관찰 행) 또는 (None, [])."""
    c = _collector()
    host = observations[index]
    host_num = _num(host.get("number"))
    detail = str(host.get("detail") or "")
    if host_num is None or not c._OBS_RE.search(detail):
        return None, []
    titled = {str(o.get("number") or "").strip() for o in observations if _is_titled(o.get("deficiency"))}
    header = f"OBSERVATION {host_num}\n{_norm(host.get('deficiency'))}\n"
    synth = header + detail
    selected = c._select_observation_anchors(synth, list(c._OBS_RE.finditer(synth)))
    cands = [m for m in selected
             if m.start() >= len(header) and int(m.group(1)) > host_num and m.group(1) not in titled]
    if not cands:
        return None, []
    rows = c._observations_from_anchors(synth, lambda s: cands, c._header_hint_kwargs(hints),
                                        gate=c._is_publishable_deficiency, fragment_heads=True)
    # 표지마다 행이 정확히 하나씩 나와야 나눈다 — 게이트에 걸린 표지가 섞이면 그 구간 글자가
    # 어디로도 가지 않게 되므로 통째로 두고 원문 재추출 대상으로 남긴다.
    if [r["number"] for r in rows] != [m.group(1) for m in cands]:
        return None, []
    cut_at = cands[0].start() - len(header)
    plan = SplitPlan(host_index=index, host_number=str(host.get("number")), cut_at=cut_at,
                     moved_chars=len(detail) - len(detail[:cut_at].rstrip()))
    return plan, rows


def split_swallowed(observations: list[dict[str, Any]], hints: dict[str, str] | None = None
                    ) -> tuple[list[dict[str, Any]], list[SplitPlan]]:
    """저장된 관찰 배열 → (나눈 새 배열, 계획 목록). 바뀔 것이 없으면 원 배열 그대로(순수 함수)."""
    obs = [dict(o) for o in observations if isinstance(o, dict)]
    plans: list[SplitPlan] = []
    inserts: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()   # 같은 관찰을 두 상세가 삼킨 문서(쪽 중복 실림) — 한 번만 더한다
    for i in range(len(obs)):
        plan, rows = plan_host_split(obs, i, hints)
        if plan is None:
            continue
        for r in rows:
            item = {"number": r["number"], "deficiency": r["deficiency"], "detail": r["detail"]}
            key = (r["number"], _norm(r["deficiency"])[:_DUP_PROBE])
            if key in seen or _fragment_holder(obs, r["number"], r["deficiency"]) is not None:
                plan.deduped.append({"number": r["number"], "deficiency": r["deficiency"]})
            else:
                plan.added.append(item)
                seen.add(key)
        plans.append(plan)
    if not plans:
        return list(observations), []
    for p in plans:
        host = obs[p.host_index]
        host["detail"] = str(host.get("detail") or "")[:p.cut_at].rstrip()
        inserts[p.host_index] = p.added
    out: list[dict[str, Any]] = []
    for i, o in enumerate(obs):
        out.append(o)
        out.extend(dict(x) for x in inserts.get(i, []))
    return out, plans


# ── 실행 층(기본 dry-run) ─────────────────────────────────────────────────────

@dataclass
class ObsSplitReport:
    dry_run: bool = True
    candidates: int = 0
    docs_changed: int = 0
    hosts_cut: int = 0
    observations_added: int = 0
    observations_deduped: int = 0
    moved_chars: int = 0
    patched: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    docs: list[dict[str, Any]] = field(default_factory=list)


def _hints(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "establishment_type": str(raw.get("establishment_type") or ""),
        "fei_number": str(raw.get("fei_number") or ""),
        "firm_name": str(raw.get("firm") or ""),
    }


def run(rows: list[dict[str, Any]], *, dry_run: bool = True, doc_ids: set[str] | None = None,
        patch_fn: Callable[[dict[str, Any], dict[str, Any]], None] | None = None) -> ObsSplitReport:
    report = ObsSplitReport(dry_run=dry_run)
    for r in rows:
        rid = str(r.get("raw_signal_id") or "")
        doc = str(r.get("document_id") or "")
        if doc_ids and doc not in doc_ids and rid not in doc_ids:
            continue
        raw = r.get("raw_json")
        raw = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        old = raw.get(_OBS_KEY)
        if not isinstance(old, list) or not old:
            continue
        report.candidates += 1
        try:
            new, plans = split_swallowed(old, _hints(raw))
        except Exception as exc:  # noqa: BLE001 — 건별 실패는 넘긴다(멱등 재실행)
            report.failed += 1
            report.errors.append(f"{doc}: split_raised:{type(exc).__name__}")
            continue
        if not plans:
            continue
        report.docs_changed += 1
        report.hosts_cut += len(plans)
        report.observations_added += sum(len(p.added) for p in plans)
        report.observations_deduped += sum(len(p.deduped) for p in plans)
        report.moved_chars += sum(p.moved_chars for p in plans)
        report.docs.append({"document_id": doc, "raw_signal_id": rid,
                            "observations_before": len(old), "observations_after": len(new),
                            "plans": [asdict(p) for p in plans]})
        if dry_run or patch_fn is None:
            continue
        try:
            patch_fn(r, {**raw, _OBS_KEY: new})
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f"{doc}: patch_failed:{type(exc).__name__}")
            continue
        report.patched += 1
    return report


def _fetch_rows(base_url: str, service_key: str) -> list[dict[str, Any]]:
    import findings_supabase_backfill as fsb
    from grm_common import SOURCE_FDA_483
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="raw_signal_id,document_id,raw_sha256,raw_json,row_json", page_size=500,   # row_json 유지(R3)
        order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "raw_json": "like.*OBSERVATION*"},
    )


def _patch(base_url: str, service_key: str, row: dict[str, Any], raw: dict[str, Any]) -> None:
    """[원문 갱신 규칙 R3] raw_json PATCH 대신 raw_signal_apply_version(148) 경유 — 지문·옛 판 보관."""
    import raw_versioning as rv
    rv.replace_raw(base_url, service_key, row, raw, change_kind="reextract",
                   changed_by=rv.run_label("backfill_483_obs_split"),
                   diff_summary={"reason": "483_obs_split"})


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="앞 관찰 상세가 삼킨 483 관찰을 저장 raw 에서 나눈다(기본 dry-run).")
    p.add_argument("--apply", action="store_true", default=False, help="실제 반영(raw 판 교체). 기본 dry-run.")
    p.add_argument("--doc-ids", default="", help="선택: document_id/raw_signal_id 목록(공백·쉼표).")
    p.add_argument("--report-path", help="JSON 리포트를 이 경로에도 기록.")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)
    from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
    creds = resolve_supabase_service_credentials(args)
    if creds is None:
        print("backfill_483_obs_split: Supabase 자격 증명 필요", file=sys.stderr)
        return 2
    base_url, service_key = creds
    base_url = normalize_supabase_url(base_url)
    rows = _fetch_rows(base_url, service_key)
    ids = {x for x in re.split(r"[\s,]+", args.doc_ids or "") if x}
    report = run(rows, dry_run=not args.apply, doc_ids=ids or None,
                 patch_fn=lambda row, raw: _patch(base_url, service_key, row, raw))
    payload = json.dumps(asdict(report), ensure_ascii=False, indent=2)
    print(payload)
    if args.report_path:
        Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
