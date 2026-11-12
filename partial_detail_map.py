#!/usr/bin/env python3
"""483 '구체 내용' 의 "원문 일부" 표시 대상 — 관찰 번호를 지적 id 로 옮긴 표를 만든다.

판정은 `content_completeness.partial_detail_notice` 하나가 한다(이 파일은 판정하지 않는다).
옮기는 이유: 그 함수는 (문서 id, **관찰 번호**) 로 묻는데, 공개 화면이 읽는 `findings` 행에는
관찰 번호가 없다. 번호는 원본 저장소(raw_signals.raw_json.fda_483_observations[].number)에만
있고 그 표는 공개 키로 읽을 수 없다. 그래서 관리자 권한으로 한 번 뽑은 행
(source·document_id·number·finding_id·detail)을 받아 함수를 그대로 태우고, 참인 지적 id 만
`web/data/partial_detail_findings.json` 에 적는다. 문서 페이지(doc-detail.js)는 이 목록에 있고
상세가 아직 문장 중간에서 끝날 때만 "원문 일부 — 전체는 원문 PDF 에서 확인" 을 붙인다.

행 뽑기(관리자 SQL — `KNOWN_PARTIAL_DETAILS` 의 문서·번호, 상세가 같은 행으로 잇는다):

    select json_agg(json_build_object('source', f.source, 'document_id', f.document_id,
             'number', o->>'number', 'finding_id', f.finding_id, 'detail', f.detail_text))
    from raw_signals r
    cross join lateral jsonb_array_elements(r.raw_json::jsonb->'fda_483_observations') o
    join findings f on f.document_id = r.document_id and f.source = r.source
                   and btrim(f.detail_text) = btrim(o->>'detail')
    where r.source = 'FDA 483' and r.document_id in (<KNOWN_PARTIAL_DETAILS 의 문서 id>);

실행:
    python partial_detail_map.py --rows rows.json [--out web/data/partial_detail_findings.json]

`KNOWN_PARTIAL_DETAILS` 를 고치거나 다시 읽기로 상세가 바뀌면 다시 돌린다. 상세가 온전해진
관찰은 함수가 거짓을 내므로 목록에서 저절로 빠진다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import content_completeness as cc

DEFAULT_OUT = Path(__file__).resolve().parent / "web" / "data" / "partial_detail_findings.json"


def build(rows: list[dict[str, Any]], measured_on: str) -> dict[str, Any]:
    ids = sorted({str(r["finding_id"]) for r in rows
                  if r.get("finding_id")
                  and cc.partial_detail_notice(str(r.get("source") or ""),
                                               str(r.get("document_id") or ""),
                                               r.get("number"),
                                               str(r.get("detail") or ""))})
    return {"schema": "grm-partial-detail/v1", "measured_on": measured_on, "finding_ids": ids}


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap =argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--measured-on", required=True)
    a = ap.parse_args(argv)
    rows = json.loads(a.rows.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("rows 는 배열이어야 한다", file=sys.stderr)
        return 2
    out = build(rows, a.measured_on)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"원문 일부 표시 대상 {len(out['finding_ids'])}건 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
