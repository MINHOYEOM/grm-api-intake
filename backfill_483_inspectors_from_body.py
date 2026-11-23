#!/usr/bin/env python3
"""FDA 483 실사관(`fda483_inspectors`) 재추출 — **저장된 전문**(`fda483_body_full`)만 읽는다(외부 요청 0).

배경(GRM 수집완전성 점검 2026-11-22, 9-3 '483 실사관 176'):
  실사관 빈칸 176행 중 서명란이 있는데 못 뽑은 130행의 원인은 셋이었다.
    R3 OCR 복구(`backfill_483_ocr_recovery._rebuild_item`)가 실사관을 넘기지 않음 · R5 전문 백필
    (`backfill_483_body_full`)이 전문만 쓰고 실사관을 다시 뽑지 않음 · R4 파서가 서명란 형식을 못 읽음.
  R3·R5 는 그 경로가 공통 함수 `collect_fda_483._extract_483_inspectors` 를 부르게 고쳤고, R4 는 그 함수를
  고쳤다. 이 도구는 이미 굳은 행을 같은 함수로 다시 채운다.

대상: source='FDA 483' ∧ 전문 있음 ∧ `fda483_inspectors` 없음/빈 배열 ∧ 범위 밖 표식(`out-of-scope`) 아님.
  `--since`(collected_at 하한)·`--doc-ids` 로 좁힐 수 있다.

안전장치(대표 결정 11-22 — 이름 지어내기·추측·다른 문서에서 빌려오기 금지):
  · 이름은 그 행의 전문 안에 **글자 그대로** 있어야 한다. 공통 함수가 이미 보장하지만 쓰기 직전에
    `_inspector_name_in_text` 로 한 번 더 확인하고, 하나라도 어긋나면 그 행은 쓰지 않는다(`not_verbatim`).
  · 다른 문서·실사관 프로파일·같은 FEI 의 다른 483 을 보는 경로가 없다. `(b)(6)` 가림은 이름이 되지 않는다.
  · 못 뽑은 행은 사유만 남긴다(`no-signature-block`·`redacted`·`ocr-spelling-conflict`·`parser-limit`).
    `absent-in-source` 표식은 사람이 원문 PDF 를 본 행에만 단다 — 이 도구는 달지 않는다.

쓰기(--apply 일 때만):
  ① raw_signals: 새 판 = 지금 raw 그대로 + `fda483_inspectors` 키 하나. `raw_versioning.apply_version`
     (change_kind='repair', expected = 읽을 때의 raw_sha256 — 그사이 바뀌었으면 conflict 로 건너뜀, 옛 판 보관).
  ② findings: 그 raw_signal_id 의 findings 행 중 `inspector_names` 가 빈 배열인 행만 같은 이름으로 PATCH
     (적재 때 raw 에서 복사되는 칸이라 raw 만 고치면 검색·문서 페이지에 안 나온다). 이미 이름이 있는 행은 건드리지 않는다.
  무료 Nano DB — 한 번에 하나, 회차 상한 `--limit`(기본 2,000).

감사 모드(`--audit-filled`, 2026-11-23 비서실 요청):
  이미 실사관이 **채워진** 행도 본다. 저장된 이름 중 하나라도 ① 그 행 전문에 글자 그대로 없거나 ② 이름 문법
  (`_valid_inspector_name` — 직함 낱말 `Team Lead`·`Compliance Officer …` 거부)에 어긋나면 그 행을 **덮어쓰기**
  대상으로 표시하고, 공통 함수 결과로 바꾼다. 결과가 비면 실사관 키를 지우고(빈칸) 사유를 남긴다.
  findings 는 그 문서 행 중 **옛 저장 이름과 같은 값**을 가진 행만 새 값으로 바꾼다. 문제없는 행은 건드리지 않는다.

기본은 dry-run: 행별 전·후 표(문서 id·뽑은 이름·전문 속 위치 발췌)와 남는 행 사유를 JSON·Markdown 으로 쓴다.
`--rows-json` 은 DB 대신 미리 받아 둔 행(raw_signal_id·document_id·body·…)으로 dry-run 한다(자격증명 불필요).
환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (값은 로그에 남기지 않는다).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import collect_fda_483 as fda483

SOURCE_FDA_483 = "FDA 483"
BODY_KEY = "fda483_body_full"
INSPECTORS_KEY = "fda483_inspectors"
OUT_OF_SCOPE = "out-of-scope"
CHANGED_BY = "backfill_483_inspectors_from_body"
SELECT_COLUMNS = "raw_signal_id,document_id,collected_at,raw_json,row_json,raw_sha256"
DEFAULT_LIMIT = 2000
_EXCERPT_PAD = 60
_SIGNATURE_RE = re.compile(r"EMPLOYEE\(S\) SIGNATURE|Investigator")   # 보고서 9-3 의 서명란 정의


@dataclass
class Report:
    mode: str = "dry-run"
    scanned: int = 0
    targets: int = 0
    with_signature_block: int = 0
    extracted: int = 0
    extracted_with_signature_block: int = 0
    not_verbatim: int = 0           # 안전장치에 걸린 행(0 이어야 한다)
    reasons: dict[str, int] = field(default_factory=dict)
    reasons_with_signature_block: dict[str, int] = field(default_factory=dict)
    applied: int = 0
    unchanged: int = 0
    conflict: int = 0
    findings_rows_patched: int = 0
    # 감사 모드 — 이미 채워진 행
    audited: int = 0                # 점검한 채워진 행
    audit_ok: int = 0               # 저장 이름이 전부 전문에 글자 그대로 있고 문법도 맞음
    overwrite: int = 0              # 덮어쓰기 대상(틀린 저장 이름 있음)
    overwrite_to_empty: int = 0     # 그중 공통 함수도 못 뽑아 빈칸이 되는 행
    errors: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    audit_rows: list[dict[str, Any]] = field(default_factory=list)   # 덮어쓰기 대상만


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def row_body(row: dict[str, Any]) -> str:
    """행의 저장 전문 — DB 행이면 raw_json 안, 미리 받은 행이면 body 칸."""
    if "body" in row:
        return str(row.get("body") or "")
    return str(_loads(row.get("raw_json")).get(BODY_KEY) or "")


def stored_names(row: dict[str, Any]) -> list[str]:
    """지금 저장된 실사관 이름 — DB 행이면 raw_json 안, 미리 받은 행이면 insp 칸."""
    value = row.get("insp") if "body" in row else _loads(row.get("raw_json")).get(INSPECTORS_KEY)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = []
    return [str(n).strip() for n in value if str(n).strip()] if isinstance(value, list) else []


def _status(row: dict[str, Any]) -> str:
    return str(row.get("st") or "") if "body" in row \
        else str(_loads(row.get("raw_json")).get("fda483_text_status") or "")


def is_target(row: dict[str, Any]) -> bool:
    """채우기 대상 — 전문 있음 ∧ 실사관 없음 ∧ 범위 밖 아님."""
    return bool(row_body(row)) and not stored_names(row) and _status(row) != OUT_OF_SCOPE


def is_audit_target(row: dict[str, Any]) -> bool:
    """감사 대상 — 전문 있음 ∧ 실사관 **있음** ∧ 범위 밖 아님."""
    return bool(row_body(row)) and bool(stored_names(row)) and _status(row) != OUT_OF_SCOPE


def bad_stored_names(names: list[str], body: str) -> list[dict[str, str]]:
    """저장 이름 중 틀린 것 — 전문에 글자 그대로 없음(`not-in-body`) 또는 이름 문법 위반(`not-a-name`)."""
    bad = []
    for n in names:
        if not fda483._inspector_name_in_text(n, body):
            bad.append({"name": n, "why": "not-in-body"})
        elif not fda483._valid_inspector_name(n, allow_caps=True, max_tokens=5):
            bad.append({"name": n, "why": "not-a-name"})
    return bad


def name_excerpt(body: str, name: str) -> str:
    """이름이 전문 어디에 있는지 — 첫 등장 위치 앞뒤 발췌(개행은 ⏎). 사람이 대조할 근거."""
    toks = name.split(" ")
    m = re.search(r"(?<![A-Za-z])" + r"\s+".join(re.escape(t) for t in toks) + r"(?![A-Za-z])", body)
    if not m:
        return ""
    s, e = max(0, m.start() - _EXCERPT_PAD), min(len(body), m.end() + _EXCERPT_PAD)
    return f"@{m.start()}: " + body[s:e].replace("\r", "").replace("\n", "⏎")


def signature_excerpt(body: str) -> str:
    """이름을 못 뽑은 행 — 첫 서명란 뒤 발췌(사람 확인용)."""
    m = re.search(r"EMPLOYEE\(S\) SIGNATURE|SEE REVERSE|Investigator", body)
    if not m:
        return ""
    return f"@{m.start()}: " + body[m.start():m.start() + 2 * _EXCERPT_PAD + 40].replace("\n", "⏎")


def evaluate(row: dict[str, Any]) -> dict[str, Any]:
    """한 행 → 표 한 줄. 이름은 공통 함수로만 뽑고, 쓰기 전 글자 그대로 검사를 다시 한다."""
    body = row_body(row)
    names, reason = fda483._extract_483_inspectors_detail(body)
    verbatim = all(fda483._inspector_name_in_text(n, body) for n in names)
    out = {
        "raw_signal_id": str(row.get("raw_signal_id") or ""),
        "document_id": str(row.get("document_id") or ""),
        "signature_block": bool(_SIGNATURE_RE.search(body)),
        "before": [],
        "after": names if verbatim else [],
        "reason": reason if verbatim else "not-verbatim",
        "evidence": [name_excerpt(body, n) for n in names] if verbatim else [],
    }
    if not out["after"]:
        out["evidence"] = [signature_excerpt(body)]
    return out


def audit(row: dict[str, Any]) -> dict[str, Any] | None:
    """채워진 행 감사 → 덮어쓰기 대상이면 표 한 줄, 문제없으면 None."""
    body = row_body(row)
    before = stored_names(row)
    bad = bad_stored_names(before, body)
    if not bad:
        return None
    line = evaluate(row)
    line.update(before=before, bad=bad, action="overwrite")
    return line


def _apply_one(row: dict[str, Any], line: dict[str, Any], rep: Report, *, overwrite: bool,
               applier: Callable[..., dict[str, Any]],
               findings_patcher: Callable[..., tuple[int, str]] | None) -> None:
    raw = _loads(row.get("raw_json"))
    now = raw.get(INSPECTORS_KEY) or []
    if overwrite:
        if list(now) != list(line["before"]):          # 그사이 바뀌었으면 손대지 않는다
            rep.unchanged += 1
            return
        if line["after"]:
            raw[INSPECTORS_KEY] = list(line["after"])
        else:
            raw.pop(INSPECTORS_KEY, None)               # 빈칸 — 키를 달지 않는 수집기 관례(_to_item)
        diff = {"replaced": INSPECTORS_KEY, "removed": [b["name"] for b in line["bad"]],
                "why": sorted({b["why"] for b in line["bad"]}), "names": len(line["after"]),
                "source": BODY_KEY, "audit": True}
    else:
        if now:                                         # 그사이 채워졌으면 덮지 않는다
            rep.unchanged += 1
            return
        raw[INSPECTORS_KEY] = list(line["after"])
        diff = {"added": [INSPECTORS_KEY], "names": len(line["after"]), "source": BODY_KEY}
    import raw_versioning as rv
    raw_text, sha = rv.canonical_raw(raw)
    record = {"raw_json": raw_text, "raw_sha256": sha, "row_json": str(row.get("row_json") or "")}
    result = applier(raw_signal_id=line["raw_signal_id"],
                     expected_raw_sha256=str(row.get("raw_sha256") or ""),
                     record=record, change_kind="repair", changed_by=CHANGED_BY, diff_summary=diff)
    status = str(result.get("status") or "error")
    if status == "applied":
        rep.applied += 1
        if findings_patcher is not None:
            if overwrite:
                n, err = findings_patcher(line["raw_signal_id"], list(line["after"]),
                                          only_value=list(line["before"]))
            else:
                n, err = findings_patcher(line["raw_signal_id"], list(line["after"]))
            rep.findings_rows_patched += n
            if err:
                rep.errors.append(f"findings({line['document_id']}):{err}")
    elif status == "unchanged":
        rep.unchanged += 1
    elif status == "conflict":
        rep.conflict += 1
    else:
        rep.errors.append(f"apply({line['document_id']}):{result.get('error') or status}")


def run(rows: list[dict[str, Any]], *, apply: bool, limit: int = DEFAULT_LIMIT,
        applier: Callable[..., dict[str, Any]] | None = None,
        findings_patcher: Callable[..., tuple[int, str]] | None = None,
        audit_filled: bool = False) -> Report:
    rep = Report(mode="apply" if apply else "dry-run")
    targets, filled = [], []
    for row in rows:
        rep.scanned += 1
        if is_target(row):
            targets.append(row)
        elif audit_filled and is_audit_target(row):
            filled.append(row)
    targets = targets[:limit] if limit else targets
    rep.targets = len(targets)
    for row in filled:
        rep.audited += 1
        line = audit(row)
        if line is None:
            rep.audit_ok += 1
            continue
        rep.overwrite += 1
        rep.overwrite_to_empty += not line["after"]
        if line["reason"] == "not-verbatim":
            rep.not_verbatim += 1
        rep.audit_rows.append(line)
        if apply and line["reason"] != "not-verbatim":
            _apply_one(row, line, rep, overwrite=True, applier=applier,
                       findings_patcher=findings_patcher)
    for row in targets:
        line = evaluate(row)
        rep.rows.append(line)
        sig = line["signature_block"]
        rep.with_signature_block += sig
        if line["reason"] == "not-verbatim":
            rep.not_verbatim += 1
        if line["after"]:
            rep.extracted += 1
            rep.extracted_with_signature_block += sig
        else:
            rep.reasons[line["reason"]] = rep.reasons.get(line["reason"], 0) + 1
            if sig:
                rep.reasons_with_signature_block[line["reason"]] = \
                    rep.reasons_with_signature_block.get(line["reason"], 0) + 1
        if not apply or not line["after"]:
            continue
        _apply_one(row, line, rep, overwrite=False, applier=applier,
                   findings_patcher=findings_patcher)
    return rep


# ── Supabase ─────────────────────────────────────────────────────────────────

def fetch_rows(base_url: str, service_key: str, *, since: str = "",
               doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
    import findings_supabase_backfill as fsb
    extra: dict[str, str] = {"source": f"eq.{SOURCE_FDA_483}", "raw_json": f"like.*{BODY_KEY}*"}
    if since:
        extra["collected_at"] = f"gte.{since}"
    if doc_ids:
        extra["document_id"] = "in.(" + ",".join(doc_ids) + ")"
    return fsb._fetch_all_pages(base_url, service_key, "raw_signals", select=SELECT_COLUMNS,
                                page_size=200, order="raw_signal_id.asc", extra_params=extra)


def patch_empty_findings(base_url: str, service_key: str, raw_signal_id: str,
                         names: list[str], *, only_value: list[str] | None = None) -> tuple[int, str]:
    """그 문서의 findings 행 중 inspector_names 가 빈 배열인 행만 채운다. (갱신 행 수, 오류 요약).

    `only_value`(감사 덮어쓰기) — 빈 배열 대신 **옛 저장 이름과 같은 값**인 행만 바꾼다."""
    import requests
    import findings_supabase_backfill as fsb
    match = json.dumps(only_value if only_value is not None else [], ensure_ascii=False,
                       separators=(",", ":"))
    try:
        resp = requests.patch(
            f"{base_url}/rest/v1/findings",
            params={"raw_signal_id": f"eq.{raw_signal_id}", "inspector_names": f"eq.{match}"},
            json={"inspector_names": names},
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                     "Content-Type": "application/json", "Prefer": "return=representation"},
            timeout=fsb.DEFAULT_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        return 0, type(exc).__name__
    if resp.status_code >= 400:
        return 0, f"http_{resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return 0, ""
    return (len(data) if isinstance(data, list) else 0), ""


# ── 보고 ─────────────────────────────────────────────────────────────────────

def markdown(rep: Report) -> str:
    lines = [f"# 483 실사관 재추출 {rep.mode} — 저장 전문 {rep.targets}행", "",
             f"- 서명란 있음 {rep.with_signature_block}행 → 채움 {rep.extracted_with_signature_block} · "
             f"남음 {rep.with_signature_block - rep.extracted_with_signature_block} "
             f"{rep.reasons_with_signature_block}",
             f"- 전체 채움 {rep.extracted}/{rep.targets} · 남음 사유 {rep.reasons}",
             f"- 안전장치(전문에 글자 그대로 없는 이름) 걸린 행: {rep.not_verbatim}", "",
             "| 문서 | 서명란 | 전 | 후(뽑은 이름) | 사유 | 전문 속 위치 발췌 |",
             "|---|---|---|---|---|---|"]
    for r in rep.rows:
        ev = " / ".join(e for e in r["evidence"] if e).replace("|", "¦")[:400]
        lines.append(f"| {r['document_id']} | {'있음' if r['signature_block'] else '없음'} | (빈칸) | "
                     f"{', '.join(r['after']) or '—'} | {r['reason']} | {ev} |")
    if rep.audited:
        lines += ["", f"## ⚠ 덮어쓰기 대상 — 이미 채워진 행 감사 {rep.audited}행 중 {rep.overwrite}행",
                  f"(문제없음 {rep.audit_ok} · 덮어쓰면 빈칸이 되는 행 {rep.overwrite_to_empty})", "",
                  "| 문서 | 지금 저장된 이름 | 틀린 이름(왜) | 바꿀 값 | 사유 | 전문 속 위치 발췌 |",
                  "|---|---|---|---|---|---|"]
        for r in rep.audit_rows:
            ev = " / ".join(e for e in r["evidence"] if e).replace("|", "¦")[:400]
            bad = ", ".join(f"{b['name']}({b['why']})" for b in r["bad"])
            lines.append(f"| **{r['document_id']}** | {', '.join(r['before'])} | {bad} | "
                         f"{', '.join(r['after']) or '(빈칸)'} | {r['reason']} | {ev} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows cp949 콘솔에서 한글·기호 출력 보호
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(기본 dry-run)")
    ap.add_argument("--rows-json", default="", help="DB 대신 미리 받은 행 JSON 으로 dry-run")
    ap.add_argument("--since", default="", help="collected_at 하한(YYYY-MM-DD)")
    ap.add_argument("--doc-ids", default="", help="document_id 목록(쉼표·공백 구분)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="회차 상한(무료 Nano — 2,000 이하)")
    ap.add_argument("--report", default="fda483_inspectors_from_body_report.json")
    ap.add_argument("--markdown", default="", help="행별 전·후 표 Markdown 경로")
    ap.add_argument("--audit-filled", action="store_true",
                    help="이미 채워진 행도 감사 — 전문에 없거나 이름이 아닌 저장 이름이 있으면 공통 함수 결과로 덮어씀")
    ap.add_argument("--supabase-url", default="")
    ap.add_argument("--service-role-key", default="")
    args = ap.parse_args(argv)
    doc_ids = [d for d in re.split(r"[,\s]+", args.doc_ids.strip()) if d]

    if args.rows_json:
        if args.apply:
            print("ERROR: --rows-json 은 dry-run 전용이다", file=sys.stderr)
            return 2
        rows = json.loads(Path(args.rows_json).read_text(encoding="utf-8"))
        if doc_ids:
            rows = [r for r in rows if r.get("document_id") in set(doc_ids)]
        rep = run(rows, apply=False, limit=args.limit, audit_filled=args.audit_filled)
    else:
        from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
        creds = resolve_supabase_service_credentials(args)
        base = normalize_supabase_url(creds[0]) if creds else None
        if not creds or base is None:
            print("ERROR: SUPABASE_URL(https)·SUPABASE_SERVICE_ROLE_KEY 가 필요하다", file=sys.stderr)
            return 2
        key = creds[1]
        rows = fetch_rows(base, key, since=args.since, doc_ids=doc_ids)
        import raw_versioning as rv
        rep = run(rows, apply=args.apply, limit=args.limit,
                  applier=(lambda **kw: rv.apply_version(base, key, **kw)) if args.apply else None,
                  findings_patcher=(lambda rid, names, **kw: patch_empty_findings(base, key, rid, names, **kw))
                  if args.apply else None, audit_filled=args.audit_filled)
    Path(args.report).write_text(json.dumps(asdict(rep), ensure_ascii=False, indent=1), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(markdown(rep), encoding="utf-8")
    print(f"483 실사관 재추출 {rep.mode}: 대상 {rep.targets} · 서명란 있음 {rep.with_signature_block} "
          f"(채움 {rep.extracted_with_signature_block}) · 전체 채움 {rep.extracted} · 남음 {rep.reasons} "
          f"· 안전장치 {rep.not_verbatim} · 적용 {rep.applied} · 같음 {rep.unchanged} · 충돌 {rep.conflict} "
          f"· findings {rep.findings_rows_patched}행 · 오류 {len(rep.errors)}"
          + (f" · 감사 {rep.audited}(문제없음 {rep.audit_ok} · 덮어쓰기 {rep.overwrite}"
             f" · 그중 빈칸 {rep.overwrite_to_empty})" if args.audit_filled else ""))
    return 1 if rep.errors or rep.not_verbatim else 0


if __name__ == "__main__":
    sys.exit(main())
