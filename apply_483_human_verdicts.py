#!/usr/bin/env python3
"""FDA 483 **사람 판독** 결과를 원천 칸(raw_json)에 반영한다 — 10차 B(실사관)·10차 D(서명 가림 표식).

배경(데이터품질팀 2026-11-25·26): 사람이 원본 PDF 쪽 이미지를 확대해 실사관 이름을 판독했고, 그 결과는
`findings.inspector_names` 에 이미 반영됐다(대표 허락 SQL). 그런데 원천 칸 `raw_json.fda483_inspectors` 는
비어 있다 — 기존 도구(`backfill_483_inspectors_from_body`)는 **저장 전문에 글자 그대로 있는 이름만** 쓰는데,
사람이 이미지로 읽은 이름은 글자층(OCR)과 철자가 다르기 때문이다(실측: 글자층이 'm' 을 'nn' 으로 읽은 이름).

세 갈래:
  ① `--from-findings DOC,...` — 그 문서 findings 행들의 이름(사람 판독으로 채운 값)을 원천 칸으로 옮긴다.
     문서 안 **비어 있지 않은 이름 집합이 정확히 하나**일 때만 쓴다(둘 이상이면 어느 쪽이 맞는지 모른다 → 건너뜀).
     같은 문서의 findings 행 중 아직 빈 행(그 뒤 새로 붙은 행)도 같은 이름으로 채운다.
  ② `--names-json` — findings 행이 없는 문서용 {"문서 id": {"names": [...], "evidence": "근거"}}.
     근거 문장이 없으면 거부한다(사람이 어디서 읽었는지 남지 않은 이름은 쓰지 않는다).
  ③ `--absent-json` — 서명란 자체가 가려져 이름이 원문에 없는 문서 {"문서 id": "redacted-b6", ...}.
     `fda483_inspectors_status = "absent-in-source"` 와 사유를 단다. 이미 이름이 있는 행에는 달지 않는다.

원천 칸에는 사람 확인 기록 `fda483_inspectors_check = {"method": "human-pdf-image", "at": 날짜}` 를 함께 단다.
  → `backfill_483_inspectors_from_body` 는 이 기록·표식이 있는 행을 **채우기·감사 어느 쪽 대상으로도 보지 않는다**
    (감사 모드가 '전문에 글자 그대로 없음' 으로 사람 판독 이름을 OCR 결과로 덮어쓰지 않게).

안전장치: 이름은 지어내지 않는다(①은 DB 에 이미 있는 값, ②는 근거 문장 필수) · 다른 문서에서 빌리지 않는다 ·
`(b)(6)` 등 가림·숫자가 든 값은 이름으로 받지 않는다 · 원천 쓰기는 `raw_versioning.replace_raw`
(change_kind='repair', 읽은 지문 기대 — 그사이 바뀌면 conflict 로 건너뜀, 옛 판 보관).
기본은 dry-run. 보고서에는 문서 id·이름 수·사유만 싣는다(이름 자체는 싣지 않는다 — 실행 기록이 공개 저장소에 남는다).
환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import backfill_483_inspectors_from_body as fb

INSPECTORS_KEY = fb.INSPECTORS_KEY
STATUS_KEY = "fda483_inspectors_status"
ABSENT = "absent-in-source"
ABSENT_REASON_KEY = "fda483_inspectors_absent_reason"
CHECK_KEY = "fda483_inspectors_check"
CHECK_METHOD = "human-pdf-image"
CHANGED_BY = "apply_483_human_verdicts"
_REASON_RE = re.compile(r"^redacted-[a-z0-9]+(?:-[a-z0-9]+)*$")
_DOC_RE = re.compile(r"^fda483-\d+$")
_BAD_NAME_RE = re.compile(r"\d|\(\s*b\s*\)|redact", re.I)


@dataclass
class Report:
    mode: str = "dry-run"
    planned: int = 0
    applied: int = 0
    unchanged: int = 0
    conflict: int = 0
    skipped: int = 0
    findings_rows_patched: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def valid_name(name: Any) -> bool:
    s = str(name or "").strip()
    return bool(s) and len(s) <= 80 and not _BAD_NAME_RE.search(s)


def names_from_findings(name_sets: list[list[str]]) -> tuple[list[str] | None, str]:
    """그 문서 findings 행들의 inspector_names → (쓸 이름 또는 None, 사유)."""
    distinct = {tuple(s) for s in name_sets if s}
    if not distinct:
        return None, "findings-empty"
    if len(distinct) > 1:
        return None, "findings-disagree"
    names = list(next(iter(distinct)))
    if not all(valid_name(n) for n in names):
        return None, "invalid-name"
    return names, ""


def plan_row(row: dict[str, Any], *, names: list[str] | None = None, absent_reason: str = "",
             at: str) -> tuple[dict[str, Any] | None, str]:
    """(새 raw 또는 None, 사유). names 와 absent_reason 중 하나만 준다."""
    raw = fb._loads(row.get("raw_json"))
    now = fb.stored_names(row)
    if raw.get("fda483_text_status") == fb.OUT_OF_SCOPE:
        return None, "out-of-scope"
    new = dict(raw)
    new[CHECK_KEY] = {"method": CHECK_METHOD, "at": at}
    if names is not None:
        if raw.get(STATUS_KEY) == ABSENT:
            return None, "marked-absent"            # 가림 표식과 이름은 함께 있을 수 없다 — 사람이 다시 본다
        new[INSPECTORS_KEY] = list(names)
    else:
        if now:
            return None, "has-names"                # 이름이 있는 행에 '원문에 없음' 을 달지 않는다
        new[STATUS_KEY] = ABSENT
        new[ABSENT_REASON_KEY] = absent_reason
    if new == raw:
        return None, "unchanged"
    return new, ""


def run(rows: list[dict[str, Any]], *, from_findings: dict[str, list[list[str]]],
        names_input: dict[str, Any], absent_input: dict[str, str], at: str,
        apply: bool = False, replacer: Callable[..., Any] | None = None,
        findings_patcher: Callable[..., tuple[int, str]] | None = None) -> Report:
    rep = Report(mode="apply" if apply else "dry-run")
    by_doc = {str(r.get("document_id") or ""): r for r in rows}
    asked = list(dict.fromkeys([*from_findings, *names_input, *absent_input]))
    for doc in asked:
        sources = [k for k, src in (("findings", from_findings), ("names", names_input),
                                    ("absent", absent_input)) if doc in src]
        line: dict[str, Any] = {"doc": doc, "action": "", "names": 0, "reason": ""}
        rep.rows.append(line)
        if not _DOC_RE.match(doc):
            line["reason"] = "bad-doc-id"
        elif len(sources) > 1:
            line["reason"] = "conflicting-inputs"   # 한 문서에 이름과 가림 표식을 동시에 줄 수 없다
        elif doc not in by_doc:
            line["reason"] = "not-found"
        if line["reason"]:
            rep.skipped += 1
            continue
        row = by_doc[doc]
        names: list[str] | None = None
        absent = ""
        if sources[0] == "findings":
            names, why = names_from_findings(from_findings[doc])
        elif sources[0] == "names":
            ent = names_input[doc] if isinstance(names_input[doc], dict) else {}
            got = [str(n).strip() for n in ent.get("names") or []]
            why = ("no-evidence" if not str(ent.get("evidence") or "").strip()
                   else "invalid-name" if not got or not all(valid_name(n) for n in got) else "")
            names = None if why else got
        else:
            absent = str(absent_input[doc] or "")
            why = "" if _REASON_RE.match(absent) else "bad-reason"
        if why:
            line["reason"] = why
            rep.skipped += 1
            continue
        new, why = plan_row(row, names=names, absent_reason=absent, at=at)
        line["action"] = "fill" if names is not None else "absent"
        line["names"] = len(names or [])
        if new is None:
            line["reason"] = why
            if why == "unchanged":
                rep.unchanged += 1
            else:
                rep.skipped += 1
            continue
        rep.planned += 1
        if not apply:
            continue
        import raw_versioning as rv
        diff = {"source": CHECK_METHOD, "action": line["action"], "names": line["names"]}
        try:
            res = (replacer or rv.replace_raw)(row, new, change_kind="repair",
                                               changed_by=rv.run_label(CHANGED_BY), diff_summary=diff)
        except rv.ApplyVersionError as exc:
            line["reason"] = exc.status
            rep.conflict += exc.status == "conflict"
            rep.errors.append(f"{doc}: apply_version {exc.status}")
            continue
        if str(res.get("status")) == "unchanged":
            rep.unchanged += 1
        else:
            rep.applied += 1
        line["result"] = str(res.get("status"))
        if names and findings_patcher:
            n, err = findings_patcher(str(row.get("raw_signal_id") or ""), names)
            rep.findings_rows_patched += n
            if err:
                rep.errors.append(f"{doc}: findings PATCH {err}")
    return rep


def markdown(rep: Report) -> str:
    out = [f"# 483 사람 판독 반영 {rep.mode}", "",
           f"- 쓸 행 {rep.planned} · 적용 {rep.applied} · 같음 {rep.unchanged} · 충돌 {rep.conflict} · "
           f"건너뜀 {rep.skipped} · findings 빈 행 채움 {rep.findings_rows_patched}", "",
           "| 문서 | 갈래 | 이름 수 | 사유/결과 |", "|---|---|---|---|"]
    for r in rep.rows:
        out.append(f"| {r['doc']} | {r['action'] or '-'} | {r['names']} | {r.get('result') or r['reason'] or '-'} |")
    if rep.errors:
        out += ["", "## 오류", *[f"- {e}" for e in rep.errors]]
    return "\n".join(out) + "\n"


def _fetch_finding_name_sets(base: str, key: str, raw_ids: list[str]) -> dict[str, list[list[str]]]:
    import findings_supabase_backfill as fsb
    rows = fsb._fetch_all_pages(base, key, "findings", select="raw_signal_id,inspector_names",
                                page_size=500, order="finding_id.asc",
                                extra_params={"raw_signal_id": "in.(" + ",".join(raw_ids) + ")"})
    out: dict[str, list[list[str]]] = {}
    for r in rows:
        names = [str(n).strip() for n in (r.get("inspector_names") or []) if str(n).strip()]
        out.setdefault(str(r.get("raw_signal_id") or ""), []).append(names)
    return out


def _split_ids(text: str) -> list[str]:
    return [x for x in re.split(r"[,\s]+", text or "") if x]


def _load_json_arg(value: str) -> dict[str, Any]:
    """파일 경로 또는 인라인 JSON(워크플로 입력)."""
    if not value:
        return {}
    text = value
    if not value.lstrip().startswith("{"):
        with open(value, encoding="utf-8") as fh:
            text = fh.read()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 객체여야 한다")
    return data


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from-findings", default="", help="findings 이름을 원천 칸으로 옮길 문서 id(쉼표)")
    ap.add_argument("--names-json", default="", help='{"문서": {"names": [...], "evidence": "..."}} 파일 또는 인라인')
    ap.add_argument("--absent-json", default="", help='{"문서": "redacted-b6", ...} 파일 또는 인라인')
    ap.add_argument("--at", required=True, help="사람 판독 날짜 YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--markdown", default="")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.at):
        print("--at 는 YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        names_input = _load_json_arg(args.names_json)
        absent_input = {k: str(v) for k, v in _load_json_arg(args.absent_json).items()}
    except (OSError, ValueError) as exc:
        print(f"입력 JSON 오류: {type(exc).__name__}", file=sys.stderr)
        return 2
    ff_docs = _split_ids(args.from_findings)
    docs = list(dict.fromkeys([*ff_docs, *names_input, *absent_input]))
    if not docs:
        print("대상 문서가 없다", file=sys.stderr)
        return 2
    import os
    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not base.startswith("https://") or not key:
        print("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 필요", file=sys.stderr)
        return 2
    rows = fb.fetch_rows(base, key, doc_ids=docs, include_bodyless=True)
    rid_by_doc = {str(r.get("document_id")): str(r.get("raw_signal_id")) for r in rows}
    sets = _fetch_finding_name_sets(base, key, [rid_by_doc[d] for d in ff_docs if d in rid_by_doc]) \
        if ff_docs else {}
    from_findings = {d: sets.get(rid_by_doc.get(d, ""), []) for d in ff_docs}
    patcher = (lambda rid, names: fb.patch_empty_findings(base, key, rid, names)) if args.apply else None
    rep = run(rows, from_findings=from_findings, names_input=names_input, absent_input=absent_input,
              at=args.at, apply=args.apply, findings_patcher=patcher)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(asdict(rep), fh, ensure_ascii=False, indent=2)
    md = markdown(rep)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(md)
    print(md)
    return 1 if (rep.errors or rep.conflict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
