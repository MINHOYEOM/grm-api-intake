#!/usr/bin/env python3
"""[구글 품질 2단계 · 2026-11-20] 국문 짧은 문서 'GRM 요지' 키트 — 대상 선정·내보내기·검사·현황(DB·네트워크 없음).

대상: 구글에 남긴 국문 문서(규칙 A 얇음·B 중복이 아닌 것) 가운데 지적 국문 본문 합이 800자 미만인 것.
      2026-11-20 측정 1,319장(FDA 483 743·Health Canada 485·식약처 91). 정본: web/data/findings_docs.json.
요지 정본: translations/doc_gist_outbox/*.json. 지침: docs/specs/doc_gist_brief.md. 검사: web/doc_gist.py `check`.

사용:
    py -3.12 -X utf8 doc_gist_kit.py status
    py -3.12 -X utf8 doc_gist_kit.py export --out <작업폴더> [--batch-size 20] [--source "FDA 483"] [--limit 100]
        → <작업폴더>/GS-0001.json … (항목: document_id·src_sha·메타·지적 전부(무절단))
          채우는 쪽은 항목마다 gist_ko·basis 를 더하고, 내보낸 메타·지적은 지워도 된다.
          그 파일을 translations/doc_gist_outbox/<같은 이름>.json 으로 낸다.
    py -3.12 -X utf8 doc_gist_kit.py qa translations/doc_gist_outbox/GS-0001.json [...]
순서: 최근 공개 문서부터.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "web"))

import doc_gist as dg  # noqa: E402

DOCS_FILE = ROOT / "web" / "data" / "findings_docs.json"
SHORT_BODY_CHARS = 800


def load_documents(path: Path = DOCS_FILE) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["documents"]


def targets(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """요지 대상 — 구글 색인에 남긴 국문 문서 중 지적 국문 본문 합 800자 미만. 판정은 렌더와 같은 함수."""
    import render   # jinja2 가 필요하다 — 이 함수에서만 부른다
    ratios = render.doc_duplicate_ratios(documents, render.DEFAULT_LANG)
    out = []
    for d in documents:
        if render.google_noindex_doc_page(d, render.DEFAULT_LANG, ratios):
            continue
        if sum(len(render.finding_body(f, render.DEFAULT_LANG)) for f in d.get("findings") or []) < SHORT_BODY_CHARS:
            out.append(d)
    return out


def state(documents: list[dict[str, Any]], outbox_dir: "Path | None" = None) -> dict[str, str]:
    over = dg.load_outbox(outbox_dir)
    out = {}
    for d in targets(documents):
        it = over.get(d["document_id"])
        if not it:
            out[d["document_id"]] = "pending"
        elif it.get("src_sha") != dg.src_sha(d):
            out[d["document_id"]] = "stale"
        elif dg.check(d, it):
            out[d["document_id"]] = "failed"
        else:
            out[d["document_id"]] = "done"
    return out


def export_row(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": d["document_id"], "src_sha": dg.src_sha(d), "source": d.get("source") or "",
        "agency": d.get("agency") or "", "firm_name": d.get("firm_name") or "",
        "published_date": d.get("published_date") or "", "inspection_date": d.get("inspection_date") or "",
        "n_findings": len(d.get("findings") or []),
        "findings": [{"n": i, "category": f.get("category_label_ko") or "", "text_ko": f.get("text_ko") or "",
                      "cfr_refs": f.get("cfr_refs") or []}
                     for i, f in enumerate(d.get("findings") or [], 1)],
    }


def export(out_dir: Path, batch_size: int, source: str = "", limit: int = 0, prefix: str = "GS") -> list[Path]:
    docs = load_documents()
    st = state(docs)
    todo = [d for d in targets(docs) if st[d["document_id"]] != "done" and (not source or d.get("source") == source)]
    todo.sort(key=lambda d: (d.get("published_date") or "", d["document_id"]), reverse=True)
    if limit:
        todo = todo[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(0, len(todo), batch_size):
        p = out_dir / f"{prefix}-{i // batch_size + 1:04d}.json"
        p.write_text(json.dumps([export_row(d) for d in todo[i:i + batch_size]], ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8", newline="\n")
        paths.append(p)
    return paths


def check_items(items: list[dict[str, Any]], docs_by_id: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    bad = {}
    for it in items:
        did = it.get("document_id") or "?"
        d = docs_by_id.get(did)
        if d is None:
            bad[did] = ["unknown_document"]
        elif it.get("src_sha") != dg.src_sha(d):
            bad[did] = [f"stale_sha:{it.get('src_sha')}!={dg.src_sha(d)}"]
        else:
            p = dg.check(d, it)
            if p:
                bad[did] = p
    return bad


def qa(files: list[Path]) -> int:
    docs_by_id = {d["document_id"]: d for d in load_documents()}
    n_all = n_bad = 0
    for f in files:
        items = json.loads(f.read_text(encoding="utf-8"))
        bad = check_items(items, docs_by_id)
        n_all += len(items)
        n_bad += len(bad)
        for did, probs in bad.items():
            print(f"FAIL {f.name} {did}: {'; '.join(probs)}")
    print(f"검사 {n_all}건 · 실격 {n_bad}건")
    return 1 if n_bad else 0


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--batch-size", type=int, default=20)
    e.add_argument("--source", default="")
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--prefix", default="GS")
    q = sub.add_parser("qa")
    q.add_argument("files", nargs="+", type=Path)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        paths = export(a.out, a.batch_size, a.source, a.limit, a.prefix)
        print(f"묶음 {len(paths)}개 → {a.out}")
        return 0
    if a.cmd == "qa":
        return qa(a.files)
    docs = load_documents()
    st = state(docs)
    by_src: dict[str, dict[str, int]] = {}
    src_of = {d["document_id"]: d.get("source") or "" for d in docs}
    counts: dict[str, int] = {}
    for did, v in st.items():
        counts[v] = counts.get(v, 0) + 1
        by_src.setdefault(src_of[did], {}).setdefault(v, 0)
        by_src[src_of[did]][v] += 1
    print(json.dumps({"total": len(st), **counts, "by_source": by_src}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
