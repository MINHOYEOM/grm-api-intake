#!/usr/bin/env python3
"""[구글 품질 2단계 · 2026-11-20] 해외 기준 조항 국문 참고 번역 키트 — 내보내기·검사·현황(DB·네트워크 없음).

대상: 국문 트리의 해외 기준 조항(21 CFR·EU GMP·ICH) 가운데 번역이 없거나 원문 해시가 바뀐 것.
정본 원문: web/data/clauses/{cfr21,eu_gmp,ich}.json. 번역 정본: translations/clause_outbox/*.json.
지침: docs/specs/clause_translation_brief.md. 검사 규칙: web/clause_translation.py `check`.

사용:
    py -3.12 -X utf8 clause_translate_kit.py status
    py -3.12 -X utf8 clause_translate_kit.py export --out <작업폴더> [--batch-size 20] [--source cfr21] [--limit 100]
        → <작업폴더>/CL-0001.json … (항목: id·sha·code·title·text — 원문 무절단)
          채우는 쪽은 같은 파일에 title_ko·text_ko 를 더하고 text·code·title 은 지워도 된다.
          그 파일을 translations/clause_outbox/<같은 이름>.json 으로 낸다.
    py -3.12 -X utf8 clause_translate_kit.py qa translations/clause_outbox/CL-0001.json [...]
        → 항목마다 실격 사유. 하나라도 있으면 종료 코드 1.
    py -3.12 -X utf8 clause_translate_kit.py status --write-queue
        → web/data/clauses/translation_queue.json 의 status 를 done/stale/pending 으로 고쳐 쓴다.

순서: 번역 뒤 구글에 돌아올 긴 조항(원문 700자 이상) 먼저, 그 안에서 21 CFR → EU GMP → ICH, 각 원천 안에서는 데이터 순서.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "web"))

import clause_text_page  # noqa: E402
import clause_translation as ctr  # noqa: E402

QUEUE = clause_text_page.DATA_DIR / "translation_queue.json"
SOURCE_ORDER = ("cfr21", "eu_gmp", "ich")


def originals() -> dict[str, dict[str, Any]]:
    """번역 대상 원문(덧씌우기 전 정본)."""
    return ctr.foreign_clauses(clause_text_page.load_sources(clause_text_page.DATA_DIR))


def state(outbox_dir: "Path | None" = None) -> dict[str, str]:
    """조항 id → done(통과한 번역이 있음)·failed(번역은 있으나 검사 실격)·stale(원문이 바뀜)·pending."""
    orig = originals()
    over = ctr.load_outbox(outbox_dir)
    out = {}
    for cid, c in orig.items():
        it = over.get(cid)
        if not it:
            out[cid] = "pending"
        elif it.get("sha") != c.get("sha"):
            out[cid] = "stale"
        elif ctr.check(c["text"], it.get("text_ko") or "", c.get("title") or "", it.get("title_ko") or ""):
            out[cid] = "failed"
        else:
            out[cid] = "done"
    return out


#: 번역 뒤 구글로 돌아올 조항을 먼저. 돌아오는 기준은 '그 언어 본문 300자 이상'(clause_text_page.GOOGLE_MIN_CHARS)인데,
#  시범 20조항 실측에서 국문은 원문의 0.43배 길이였다 → 원문 약 700자 이상이어야 번역 뒤 300자를 넘는다(1,230 중 약 320).
RETURN_EN_CHARS = 700


def _order_key(c: dict[str, Any]) -> tuple:
    src = c.get("source") or c["id"].split("/")[0]
    return (0 if len(c.get("text") or "") >= RETURN_EN_CHARS else 1,
            SOURCE_ORDER.index(src) if src in SOURCE_ORDER else 9)


def export(out_dir: Path, batch_size: int, source: str = "", limit: int = 0, prefix: str = "CL") -> list[Path]:
    orig = originals()
    st = state()
    todo = [c for c in orig.values() if st[c["id"]] != "done" and (not source or c["id"].startswith(source + "/"))]
    todo.sort(key=_order_key)   # sort 는 안정적 — 원천 안에서는 데이터 순서가 남는다
    if limit:
        todo = todo[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(0, len(todo), batch_size):
        rows = [{"id": c["id"], "sha": c["sha"], "code": c["code"], "title": c.get("title") or "",
                 "text": c["text"]} for c in todo[i:i + batch_size]]
        p = out_dir / f"{prefix}-{i // batch_size + 1:04d}.json"
        p.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        paths.append(p)
    return paths


def qa(files: list[Path]) -> int:
    orig = originals()
    n_bad = n_all = 0
    for f in files:
        items = json.loads(f.read_text(encoding="utf-8"))
        bad = ctr.check_items(items, orig)
        n_all += len(items)
        n_bad += len(bad)
        for cid, probs in bad.items():
            print(f"FAIL {f.name} {cid}: {'; '.join(probs)}")
    print(f"검사 {n_all}건 · 실격 {n_bad}건")
    return 1 if n_bad else 0


def write_queue(st: dict[str, str]) -> None:
    q = json.loads(QUEUE.read_text(encoding="utf-8"))
    for it in q["items"]:
        it["status"] = st.get(it["id"], it.get("status", "pending"))
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


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
    e.add_argument("--source", default="", choices=("",) + SOURCE_ORDER)
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--prefix", default="CL")
    q = sub.add_parser("qa")
    q.add_argument("files", nargs="+", type=Path)
    s = sub.add_parser("status")
    s.add_argument("--write-queue", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        paths = export(a.out, a.batch_size, a.source, a.limit, a.prefix)
        print(f"묶음 {len(paths)}개 → {a.out}")
        return 0
    if a.cmd == "qa":
        return qa(a.files)
    st = state()
    counts: dict[str, int] = {}
    by_src: dict[str, dict[str, int]] = {}
    for cid, v in st.items():
        counts[v] = counts.get(v, 0) + 1
        src = cid.split("/")[0]
        by_src.setdefault(src, {}).setdefault(v, 0)
        by_src[src][v] += 1
    print(json.dumps({"total": len(st), **counts, "by_source": by_src}, ensure_ascii=False))
    if a.write_queue:
        write_queue(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
