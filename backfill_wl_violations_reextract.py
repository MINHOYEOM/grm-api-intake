#!/usr/bin/env python3
"""경고서한 위반항목(`wl_violations`) 재추출 — **저장된 본문**(`wl_body_full`)만 읽는다(외부 요청 0).

배경(데이터품질팀 재현 2026-11-23, 공유 노트 wl_violations_repro_1123.md):
  ① 10-28 이전 과거분 백필 경로(`collect_fda_backfill._wl_item_from_row`)가 본문만 싣고 추출을 부르지 않아
     '본문 있음 · 위반항목 없음 · 사유 없음' 행이 약 770 남았다(387c038f 에서 build_wl_item 으로 고쳤지만 이미
     들어간 행은 그대로). ② 파서가 F1(번호 + 'Your firm failed … (21 CFR …).') 밖 형식을 못 읽었다
     (F1b 대괄호 · F2 API 번호 표제 · F3/F3b 번호 없는 표제 · F4 비CGMP 법 조항 판정 문장 — 11-23 수리).
  ③ 11-03 #1296 이전 파서 결과(비-'Your' 표제 누락)가 그대로인 행은 지금 파서보다 항목이 적다.
  이 도구는 그런 행을 수집과 **같은 함수**(`collect_intake.build_wl_item` → `extract_wl_violations_detail`)로
  다시 뽑는다.

대상: source='FDA Warning Letter' ∧ 본문 있음. 행마다 다음 중 하나로 분류한다.
  fill   — 위반항목이 비었는데 이번에 뽑혔다 → 쓴다.
  stale  — 위반항목이 있는데 새 결과가 옛 목록을 **모두 포함**하고 더 많다(낡은 부분 추출) → 쓴다.
  mark   — 이번에도 못 뽑았다 → 위반항목은 그대로 비우고 사유 표식(`wl_violations_status`)만 쓴다.
  same   — 바뀔 것이 없다.
  diverged — 위반항목이 있는데 새 결과가 옛 목록을 포함하지 않는다(줄거나 문장이 바뀜) → **쓰지 않는다**, 표에만.

안전장치(대표 결정 11-22 — 원문 문장 그대로·지어내기 금지):
  · 새 statement 는 그 행 본문(공백 정규화) 안에 글자 그대로 있어야 한다. 하나라도 어긋나면 그 행은 쓰지 않는다
    (`not_verbatim` — 0 이어야 한다).
  · raw 에서 바꾸는 칸은 `wl_violations`·`wl_violations_status` 둘뿐이다. row_json(관련도·tier·제품군)은 그대로 둔다.
  · 저장 raw 에 build_wl_item 이 모르는 키가 있으면 건너뛴다(다른 경로가 덧붙인 값을 재구성이 잃지 않게).

쓰기(--apply 일 때만): `raw_versioning.replace_raw`(raw_signal_apply_version · change_kind='reextract' ·
  expected = 읽을 때의 raw_sha256 — 그사이 바뀌었으면 conflict 로 건너뜀, 옛 판 보관, 지문은 수집과 같은
  canonical_raw). findings 는 wl_violations 를 읽지 않으므로 손대지 않는다.
  무료 Nano DB — 한 번에 하나, 회차 쓰기 상한 `--limit`(기본·최대 2,000). 남으면 다시 돌린다(멱등).

기본은 dry-run: 행별 전·후 표(문서 id·형식·항목 수·첫 statement 발췌)와 남는 빈 행 사유를 JSON·Markdown 으로 쓴다.
`--rows-json` 은 DB 대신 미리 받아 둔 행(raw_signal_id·document_id·raw_json…)으로 dry-run 한다(자격증명 불필요).
`--glp-census --rows-json` 은 GLP 서한의 빈 행과 재추출로 채울 수 있는 행을 읽기 전용으로 집계한다.
환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (값은 로그에 남기지 않는다).
exit: 자격증명·조회 실패 2 · 안전장치(not_verbatim) 또는 쓰기 실패가 있으면 1 · 그 외 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import collect_intake as ci
from grm_common import SOURCE_FDA_WL

SCHEMA_VERSION = "grm-wl-violations-reextract/v1"
CHANGED_BY = "backfill_wl_violations_reextract"
SELECT_COLUMNS = "raw_signal_id,document_id,collected_at,raw_sha256,raw_json,row_json"
DEFAULT_LIMIT = 2000
MAX_LIMIT = 2000
_PAGE_SIZE = 200          # 본문이 커서(행당 ~15KB) 작게 받는다
_EXCERPT = 110
WRITE_KINDS = ("fill", "stale", "mark")


@dataclass
class Report:
    schema_version: str = SCHEMA_VERSION
    mode: str = "dry-run"
    limit: int = DEFAULT_LIMIT
    scanned: int = 0                      # 본문 있는 WL 행
    blank_before: int = 0                 # 그중 위반항목 빈 행
    blank_before_unmarked: int = 0        # 그중 사유 표식도 없는 행(상시 점검이 세는 것)
    blank_before_cgmp: int = 0            # 빈 행 중 subject 에 CGMP
    kinds: dict[str, int] = field(default_factory=dict)          # fill/stale/mark/same/diverged/skipped
    fill_by_format: dict[str, int] = field(default_factory=dict)
    fill_by_format_cgmp: dict[str, int] = field(default_factory=dict)
    stale_by_format: dict[str, int] = field(default_factory=dict)
    remain_empty_by_reason: dict[str, int] = field(default_factory=dict)
    blank_by_path: dict[str, int] = field(default_factory=dict)  # 빈 행의 적재 경로 흔적(발췌 키 유무)
    items_added: int = 0                  # fill+stale 로 새로 생기는 위반항목 수
    not_verbatim: int = 0
    unknown_keys: int = 0
    written: int = 0
    write_failed: int = 0
    deferred_by_limit: int = 0            # 회차 상한으로 이번엔 못 쓴 행(다시 돌리면 이어서)
    errors: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            obj = json.loads(value)
        except ValueError:
            return {}
        return obj if isinstance(obj, dict) else {}
    return {}


def _norm(s: str) -> str:
    return " ".join(str(s or "").split())


def _is_cgmp(raw: dict[str, Any]) -> bool:
    return "cgmp" in str(raw.get("subject") or "").lower()


def reextract_raw(raw: dict[str, Any]) -> tuple[list[dict[str, str]], str, str]:
    """저장 raw → (새 위반항목, 새 사유 표식, 형식) — 수집과 같은 함수(build_wl_item)를 저장 본문으로 부른다."""
    def _s(key: str) -> str:
        return str(raw.get(key) or "")

    if raw.get("wl_body_excerpt"):
        excerpt: tuple[str, str] | None = (_s("wl_body_excerpt"), ci.WL_BODY_STATUS_OK)
    elif raw.get("wl_body_status"):
        excerpt = ("", _s("wl_body_status"))       # 저장 사유 보존(setdefault 경로)
    else:
        excerpt = None
    item = ci.build_wl_item(
        firm=_s("firm"), posted_raw=_s("posted_date"), letter_date_raw=_s("letter_date"),
        issuing_office=_s("issuing_office"), subject=_s("subject"), wl_href=_s("url"),
        office_verdict=_s("office_gate_verdict"), excerpt=excerpt,
        full=(_s("wl_body_full"), ci.WL_BODY_STATUS_OK),
    )
    new_raw = item.raw_payload
    rows = list(new_raw.get("wl_violations") or [])
    status = str(new_raw.get("wl_violations_status") or "")
    # 형식 라벨(표용) — 같은 함수·같은 범위 판정. 결과가 build_wl_item 과 다르면 경로가 갈라진 것이다.
    rows2, fmt = ci.extract_wl_violations_detail(
        _s("wl_body_full"), extended=ci.wl_violations_extended_scope(item.qa_relevance, _s("issuing_office")),
        issuing_office=_s("issuing_office"))
    if rows2 != rows:
        raise RuntimeError("build_wl_item 과 extract_wl_violations_detail 결과 불일치")
    return rows, status, (fmt if rows else "")


def _contains_old(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> bool:
    new_s = {_norm(v.get("statement", "")) for v in new}
    return all(_norm(v.get("statement", "")) in new_s for v in old if isinstance(v, dict))


def classify(row: dict[str, Any]) -> dict[str, Any]:
    """행 1개 → 분류 결과(쓰지 않는다). 키: kind, new_raw(쓸 때), 표 칸들."""
    raw = _json_object(row.get("raw_json"))
    doc = str(row.get("document_id") or "")
    body = str(raw.get("wl_body_full") or "")
    old = raw.get("wl_violations") if isinstance(raw.get("wl_violations"), list) else []
    old_status = str(raw.get("wl_violations_status") or "")
    out: dict[str, Any] = {
        "document_id": doc, "raw_signal_id": str(row.get("raw_signal_id") or ""),
        "collected_at": str(row.get("collected_at") or "")[:10],
        "subject": str(raw.get("subject") or "")[:60], "cgmp": _is_cgmp(raw),
        "path": "daily(excerpt)" if raw.get("wl_body_excerpt") else "backfill(no-excerpt)",
        "before": len(old), "before_status": old_status,
        "gap_before": ci.wl_violations_gap_kind(raw),
    }
    unknown = sorted(set(raw) - ci_known_raw_keys())
    if unknown:
        return {**out, "kind": "skipped", "reason": "unknown_raw_keys:" + ",".join(unknown)}
    new, status, fmt = reextract_raw(raw)
    nbody = _norm(body)
    bad = [v["statement"] for v in new if _norm(v.get("statement", "")) not in nbody]
    out.update({"after": len(new), "format": fmt, "status": status,
                "first": (new[0]["statement"][:_EXCERPT] if new else "")})
    if bad:
        return {**out, "kind": "not_verbatim", "reason": bad[0][:_EXCERPT]}
    if not old:
        if new:
            kind = "fill"
        elif status != old_status:
            kind = "mark"
        else:
            kind = "same"
    elif new == old:
        kind = "same"
    elif len(new) > len(old) and _contains_old(old, new):
        kind = "stale"
    else:
        kind = "diverged"
    new_raw = None
    if kind in WRITE_KINDS:
        new_raw = dict(raw)
        new_raw.pop("wl_violations", None)
        new_raw.pop("wl_violations_status", None)
        if new:
            new_raw["wl_violations"] = new
        elif status:
            new_raw["wl_violations_status"] = status
    return {**out, "kind": kind, "new_raw": new_raw}


def ci_known_raw_keys() -> frozenset[str]:
    from backfill_wl_body_recovery import KNOWN_RAW_KEYS
    return KNOWN_RAW_KEYS


def glp_census(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"glp": 0, "zero_before": 0, "fillable": 0, "zero_after": 0}
    remaining: Counter[str] = Counter()
    for row in rows:
        if row.get("source") and row["source"] != SOURCE_FDA_WL:
            continue
        raw = _json_object(row.get("raw_json"))
        if not ci.is_wl_glp(raw):
            continue
        counts["glp"] += 1
        if isinstance(raw.get("wl_violations"), list) and raw["wl_violations"]:
            continue
        counts["zero_before"] += 1
        if not _norm(raw.get("wl_body_full") or ""):
            remaining["no_body"] += 1
            continue
        result = classify(row)
        if result["kind"] == "fill":
            counts["fillable"] += 1
        else:
            remaining[result.get("reason") or result.get("status") or result["kind"]] += 1
    counts["zero_after"] = counts["zero_before"] - counts["fillable"]
    return {"mode": "dry-run", **counts, "zero_after_by_reason": dict(sorted(remaining.items()))}


def run(rows: list[dict[str, Any]], *, apply: bool, limit: int,
        writer: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], str] | None = None) -> Report:
    rep = Report(mode="apply" if apply else "dry-run", limit=limit)
    kinds: Counter[str] = Counter()
    fill_fmt: Counter[str] = Counter()
    fill_fmt_cgmp: Counter[str] = Counter()
    stale_fmt: Counter[str] = Counter()
    remain: Counter[str] = Counter()
    by_path: Counter[str] = Counter()
    for row in sorted(rows, key=lambda r: str(r.get("raw_signal_id") or "")):
        raw = _json_object(row.get("raw_json"))
        if not str(raw.get("wl_body_full") or "").strip():
            continue
        rep.scanned += 1
        try:
            res = classify(row)
        except Exception as exc:  # noqa: BLE001 — 건별 실패는 계속
            rep.errors.append(f"{row.get('document_id')}: {type(exc).__name__}: {exc}")
            kinds["error"] += 1
            continue
        if res["before"] == 0:
            rep.blank_before += 1
            by_path[res["path"]] += 1
            rep.blank_before_cgmp += int(res["cgmp"])
            if res["gap_before"] == "unmarked":
                rep.blank_before_unmarked += 1
        kind = res["kind"]
        kinds[kind] += 1
        if kind == "not_verbatim":
            rep.not_verbatim += 1
        if kind == "skipped":
            rep.unknown_keys += 1
        if kind == "fill":
            fill_fmt[res["format"]] += 1
            if res["cgmp"]:
                fill_fmt_cgmp[res["format"]] += 1
            rep.items_added += res["after"]
        elif kind == "stale":
            stale_fmt[res["format"]] += 1
            rep.items_added += res["after"] - res["before"]
        if res.get("after", 0) == 0 and kind in ("mark", "same"):
            remain[res.get("status") or "unmarked"] += 1
        new_raw = res.pop("new_raw", None)
        if kind in WRITE_KINDS and new_raw is not None:
            if rep.written + rep.write_failed >= limit:
                rep.deferred_by_limit += 1
                res["write"] = "deferred"
            elif apply and writer is not None:
                err = writer(row, new_raw, res)
                if err:
                    rep.write_failed += 1
                    res["write"] = f"failed:{err}"
                    rep.errors.append(f"{res['document_id']}: {err}")
                else:
                    rep.written += 1
                    res["write"] = "ok"
            else:
                res["write"] = "dry"
        if kind != "same":
            rep.rows.append(res)
    rep.kinds = dict(sorted(kinds.items()))
    rep.fill_by_format = dict(sorted(fill_fmt.items()))
    rep.fill_by_format_cgmp = dict(sorted(fill_fmt_cgmp.items()))
    rep.stale_by_format = dict(sorted(stale_fmt.items()))
    rep.remain_empty_by_reason = dict(sorted(remain.items()))
    rep.blank_by_path = dict(sorted(by_path.items()))
    return rep


def render_markdown(rep: Report, *, max_rows: int = 400) -> str:
    k = rep.kinds
    lines = [
        f"# 경고서한 위반항목 재추출 ({rep.mode}) — 본문 있는 WL {rep.scanned}행", "",
        f"- 위반항목 빈 행(전): **{rep.blank_before}** (사유 표식도 없음 {rep.blank_before_unmarked} · "
        f"CGMP 서한 {rep.blank_before_cgmp}) · 적재 경로 흔적 {rep.blank_by_path}",
        f"- 채움(fill) {k.get('fill', 0)} {rep.fill_by_format} — 그중 CGMP {rep.fill_by_format_cgmp}",
        f"- 낡은 부분 추출 보강(stale) {k.get('stale', 0)} {rep.stale_by_format} · 새 항목 합계 {rep.items_added}",
        f"- 사유 표식만(mark) {k.get('mark', 0)} · 그대로(same) {k.get('same', 0)} · "
        f"옛 목록과 어긋나 안 씀(diverged) {k.get('diverged', 0)}",
        f"- 남는 빈 행 사유: {rep.remain_empty_by_reason}",
        f"- 안전장치(본문에 글자 그대로 없는 문장) {rep.not_verbatim} · 모르는 키 {rep.unknown_keys} · "
        f"오류 {k.get('error', 0)}",
        f"- 쓰기: {rep.written} · 실패 {rep.write_failed} · 회차 상한으로 다음 회차 {rep.deferred_by_limit} (limit {rep.limit})",
        "",
        "| 문서 | 분류 | 형식 | 전→후 | 사유 | 경로 | CGMP | 첫 statement 발췌 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    order = {"not_verbatim": 0, "diverged": 1, "stale": 2, "fill": 3, "mark": 4, "skipped": 5}
    for r in sorted(rep.rows, key=lambda r: (order.get(r["kind"], 9), r.get("format", ""), r["document_id"]))[:max_rows]:
        first = str(r.get("first") or r.get("reason") or "").replace("|", "/")
        lines.append(f"| {r['document_id']} | {r['kind']} | {r.get('format') or '—'} | {r['before']}→{r.get('after', '?')} "
                     f"| {r.get('status') or ''} | {r['path']} | {'Y' if r['cgmp'] else ''} | {first} |")
    if len(rep.rows) > max_rows:
        lines.append(f"\n(표는 {max_rows}행까지 — 전체는 report.json)")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def fetch_rows(base_url: str, service_key: str, *, doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
    import findings_supabase_backfill as fsb
    params = {"source": f"eq.{SOURCE_FDA_WL}", "raw_json": "like.*wl_body_full*"}
    if doc_ids:
        params["document_id"] = "in.(" + ",".join(doc_ids) + ")"
    return fsb._fetch_all_pages(base_url, service_key, "raw_signals", select=SELECT_COLUMNS,
                                page_size=_PAGE_SIZE, order="raw_signal_id.asc", extra_params=params)


def make_writer(base_url: str, service_key: str) -> Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], str]:
    import raw_versioning as rv

    def _write(row: dict[str, Any], new_raw: dict[str, Any], res: dict[str, Any]) -> str:
        try:
            rv.replace_raw(base_url, service_key, row, new_raw, change_kind="reextract",
                           changed_by=rv.run_label(CHANGED_BY),
                           diff_summary={"reason": "wl_violations_reextract_1123", "kind": res["kind"],
                                         "format": res.get("format", ""), "before": res["before"],
                                         "after": res.get("after", 0)})
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}:{str(exc)[:80]}"
        return ""
    return _write


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="경고서한 위반항목 재추출(저장 본문 · 외부 요청 0)")
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(기본 dry-run)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"회차 쓰기 상한(최대 {MAX_LIMIT})")
    ap.add_argument("--doc-ids", default="", help="document_id 쉼표 목록(비우면 전부)")
    ap.add_argument("--rows-json", default="", help="DB 대신 미리 받아 둔 행 JSON(list 또는 {rows:[]}) — dry 전용")
    ap.add_argument("--glp-census", action="store_true", help="GLP 빈 행 전수 집계(--rows-json 필수)")
    ap.add_argument("--report", default="report.json")
    ap.add_argument("--markdown", default="table.md")
    args = ap.parse_args(argv)
    if args.glp_census and (not args.rows_json or args.apply):
        ap.error("--glp-census 는 --rows-json 을 지정한 dry-run 에서만 사용할 수 있습니다")
    limit = max(0, min(int(args.limit), MAX_LIMIT))
    doc_ids = [d.strip() for d in args.doc_ids.split(",") if d.strip()]
    writer = None
    if args.rows_json:
        if args.apply:
            print("--rows-json 은 dry-run 전용", file=sys.stderr)
            return 2
        data = json.loads(Path(args.rows_json).read_text(encoding="utf-8"))
        rows = data.get("rows", []) if isinstance(data, dict) else data
        if doc_ids:
            rows = [r for r in rows if str(r.get("document_id")) in doc_ids]
        if args.glp_census:
            summary = json.dumps(glp_census(rows), ensure_ascii=False, indent=2)
            Path(args.report).write_text(summary + "\n", encoding="utf-8")
            print(summary)
            return 0
    else:
        from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
        creds = resolve_supabase_service_credentials(argparse.Namespace(supabase_url="", service_role_key=""))
        if not creds:
            print("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
            return 2
        base, key = normalize_supabase_url(creds[0]), creds[1]
        try:
            rows = fetch_rows(base, key, doc_ids=doc_ids or None)
        except Exception as exc:  # noqa: BLE001
            print(f"조회 실패: {type(exc).__name__}", file=sys.stderr)
            return 2
        if args.apply:
            writer = make_writer(base, key)
    rep = run(rows, apply=args.apply, limit=limit, writer=writer)
    Path(args.report).write_text(json.dumps(asdict(rep), ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_markdown(rep)
    Path(args.markdown).write_text(md, encoding="utf-8")
    print("\n".join(md.splitlines()[:12]))
    return 1 if (rep.not_verbatim or rep.write_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
