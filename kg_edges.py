#!/usr/bin/env python3
"""지식그래프 엣지 — 조항(finding_clause) 파서 파이썬 기준 구현 + 지적↔용어(finding_term) 빌더.

* `parse_citation` 은 web/migrations/106_finding_clause_edges.sql 의 grm_parse_citation 과 같은
  결과를 내야 한다(운영 백필은 SQL 이 한다 — 이 파일은 테스트·문서용 기준 구현. 대응표·신뢰도 숫자가
  갈라지면 tests/test_kg_edges.py 가 잡는다).
* `build_term_edges` 는 카드/문서 용어 링크와 **같은 엔진**(web/render.py `_doc_term_find`,
  `build_doc_term_link_index`: 정확 일치 + 한글 낱말 시작 규칙 + 동사형 배제)을 쓴다. 부분일치 금지.

사용:
  python kg_edges.py terms --dry-run            # 연결 수·신뢰도 분포·샘플 30건(표)
  python kg_edges.py terms --emit-sql out.sql   # finding_term INSERT 배치(DB 에 쓰지 않는다)
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent

# FD&C Act section -> 21 U.S.C. (대응표에 있는 것만 연결한다)
FDCA_TO_USC: dict[str, str] = {
    "201": "321", "301": "331", "303": "333", "402": "342", "403": "343", "409": "348",
    "501": "351", "502": "352", "503": "353", "503b": "353b", "505": "355", "505-1": "355-1",
    "506": "356", "510": "360", "512": "360b", "515": "360e", "519": "360i", "520": "360j",
    "704": "374", "801": "381",
}

_PARA = r"((?:\([A-Za-z0-9]+\))*)"
_CFR = re.compile(r"^\s*21\s*CFR\s*(?:Part\s*)?(\d{1,4})\.(\d{1,4})" + _PARA + r"\s*$", re.I)
_CFR_PART = re.compile(r"^\s*21\s*CFR\s*(?:Part\s*)?(\d{1,4})\s*$", re.I)
_USC = re.compile(r"^\s*21\s*U\.?\s*S\.?\s*C\.?\s*(?:§+|section)?\s*(\d{3}[A-Za-z]?(?:-\d+)?)" + _PARA + r"\s*$", re.I)
_SECTION = re.compile(r"^\s*section\s+(\d{3}[A-Za-z]?(?:-\d+)?)" + _PARA + r"\s*$", re.I)
_BYEOLPYO = re.compile(r"^\s*(.*?)\s*\[별표\s*(\d+(?:의\d+)?)\]\s*(.*)$", re.S)
_ITEM_HO = re.compile(r"제\s*(\d+(?:[.의]\d+)*)\s*호")
_ITEM_BARE = re.compile(r"^(\d+(?:\.\d+)*)\s*$")
_MASK = re.compile(r"[*0]{4,}")
_MOK = re.compile(r"([가-하])목")

Citation = tuple[str, str, float, str]  # key, paragraph, confidence, basis


def parse_citation(raw: str) -> list[Citation]:
    out: list[Citation] = []
    s = raw or ""
    if m := _CFR.match(s):
        out.append((f"21 CFR {m[1]}.{m[2]}", m[3].lower(), 1.0, "cfr"))
    if m := _CFR_PART.match(s):
        out.append((f"21 CFR {m[1]}", "", 0.9, "cfr_part"))
    if m := _USC.match(s):
        out.append((f"21 USC {m[1].lower()}", m[2].lower(), 1.0, "usc"))
    if m := _SECTION.match(s):
        usc = FDCA_TO_USC.get(m[1].lower())
        if usc:
            out.append((f"21 USC {usc}", m[2].lower(), 0.9, "fdca_section"))
    if (m := _BYEOLPYO.match(s)) and not _MASK.search(m[1]):
        law, tbl, rest = m[1].strip(), m[2], m[3]
        label = law or "[규칙 미상]"
        items = _ITEM_HO.findall(rest)
        if not items and (b := _ITEM_BARE.match(rest)):
            items = [b[1]]
        mok = (mm[1] + "목") if (mm := _MOK.search(rest)) else ""
        conf = 0.7 if not law else 0.95
        for item in items:
            out.append((f"{label} 별표{tbl} 제{item}호", mok, conf, "mfds_byeolpyo"))
        if not items and not re.search(r"제\s*\d", rest) and not _ITEM_BARE.match(rest):
            out.append((f"{label} 별표{tbl}", "", 0.5, "mfds_table"))
    return out


# ── 식약처 규칙명 없는 별표 — 출처 문서 근거 보강(109 SQL 의 파이썬 기준 구현) ─────────────────
# 문서(attachment_text)의 "｢규칙명｣ … [별표 N]" 문장에서 (규칙, 별표) 쌍을 읽고, 한 문서에서 그 별표에 규칙이
# 정확히 하나만 묶일 때만 규칙명을 붙인다. ref 가 strict 형태(한 별표 + 호/목 나열)일 때만. conf 0.9.
_DOC_RULE = re.compile(r"[｢「]([^｣」]{2,40})[｣」][^｢｣「」\[]{0,60}?\[별표\s*(\d+(?:의\d+)?)\]")
_STRICT_REF = re.compile(
    r"^\s*\[별표\s*\d+(?:의\d+)?\]\s*(?:제\s*)?\d+(?:[.의]\d+)*\s*호?(?:\s*[가-하]목)?"
    r"(?:\s*(?:,|및)\s*제\s*\d+(?:[.의]\d+)*\s*호(?:\s*[가-하]목)?)*\s*$")
_REF_TBL = re.compile(r"^\s*\[별표\s*(\d+(?:의\d+)?)\]")
DOC_RULE_CONFIDENCE = 0.9
DOC_RULE_BASIS = "mfds_byeolpyo_doc"


def doc_rule(doc_text: str | None, tbl: str) -> str | None:
    laws = set()
    for law, t in _DOC_RULE.findall(doc_text or ""):
        law = re.sub(r"\s+", " ", law.strip())
        if re.sub(r"\s+", "", t) == tbl and re.search(r"(규칙|규정)$", law):
            laws.add(law)
    return next(iter(laws)) if len(laws) == 1 else None


def parse_citation_with_doc(raw: str, doc_text: str | None) -> list[Citation]:
    """규칙명 없는 strict 별표 ref 를 문서 근거로 보강한 조항 키(conf 0.9). 근거가 없으면 빈 목록."""
    m = _REF_TBL.match(raw or "")
    if not m or not _STRICT_REF.match(raw):
        return []
    law = doc_rule(doc_text, m[1])
    if not law:
        return []
    return [(k, p, DOC_RULE_CONFIDENCE, DOC_RULE_BASIS)
            for k, p, _c, b in parse_citation(f"{law} {raw}") if b == "mfds_byeolpyo"]


# ── 지적 ↔ 용어 ────────────────────────────────────────────────────────────────

def _load_render():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "web"))
    import render  # noqa: WPS433
    return render


# 신뢰도 규칙(정확 일치만 연결, 모호하면 연결하지 않는다):
#   1.0  : 영문 약어/전체 표제어가 낱말 경계로 일치(라틴), 또는 한글 표면형 4음절 이상
#   0.85 : 한글 표면형 3음절 이하이나 문맥(동사형 배제·낱말 시작 규칙)을 통과
#   <0.8 : 연결하지 않는다 — 한글 2음절 이하(품질·제조·기록 같은 일반어), 또는 라틴 2자 이하
MIN_CONFIDENCE = 0.8


def _surface_confidence(surface: str, hangul_re: "re.Pattern[str]") -> float:
    if hangul_re.search(surface):
        n = len(re.findall(r"[가-힣]", surface))
        if n >= 4:
            return 1.0
        if n == 3:
            return 0.85
        return 0.5
    return 1.0 if len(surface) >= 3 else 0.5


def build_term_edges(docs: Iterable[dict], terms: list[dict], render=None) -> tuple[list[dict], dict]:
    """[{finding_id, term_id, surface, confidence}] 와 통계. 입력은 findings_docs.json 의 documents."""
    render = render or _load_render()
    index = render.build_doc_term_link_index(terms)
    hangul = render._HANGUL_RE
    edges: list[dict] = []
    dropped_low = 0
    n_findings = 0
    for doc in docs:
        for f in doc.get("findings") or []:
            text = f.get("text_ko") or ""
            if not text or not f.get("finding_id"):
                continue
            n_findings += 1
            seen: set[str] = set()
            for surface, tid in index:  # 긴 표면형 우선이라 한 용어의 최장 표면형이 먼저 잡힌다
                if tid in seen:
                    continue
                if render._doc_term_find(text, surface) >= 0:
                    seen.add(tid)
                    conf = _surface_confidence(surface, hangul)
                    if conf < MIN_CONFIDENCE:
                        dropped_low += 1
                        continue
                    edges.append({"finding_id": f["finding_id"], "term_id": tid,
                                  "surface": surface, "confidence": conf})
    stats = {"findings_scanned": n_findings, "edges": len(edges), "dropped_low_confidence": dropped_low,
             "findings_with_edge": len({e["finding_id"] for e in edges}),
             "terms_used": len({e["term_id"] for e in edges})}
    return edges, stats


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def emit_sql(edges: list[dict], batch: int = 500) -> str:
    out = ["-- finding_term 백필(dry-run 산출물 — 적용 전 검토). 멱등: on conflict do nothing.", "begin;"]
    rows = sorted(edges, key=lambda e: (e["finding_id"], e["term_id"]))
    for i in range(0, len(rows), batch):
        vals = ",\n".join(f"  ({_sql_str(e['finding_id'])}, {_sql_str(e['term_id'])}, "
                          f"{_sql_str(e['surface'])}, {e['confidence']})" for e in rows[i:i + batch])
        out.append("insert into public.finding_term (finding_id, term_id, surface, confidence)\n"
                   "select v.finding_id, v.term_id, v.surface, v.confidence from (values\n"
                   + vals + "\n) as v(finding_id, term_id, surface, confidence)\n"
                   "join public.findings f on f.finding_id = v.finding_id  -- 스냅샷에만 있는 id 는 건너뛴다\n"
                   "on conflict do nothing;")
    out.append("commit;")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("terms")
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--emit-sql")
    t.add_argument("--sample", type=int, default=30)
    a = ap.parse_args(argv)
    docs = json.loads((ROOT / "web/data/findings_docs.json").read_text(encoding="utf-8"))["documents"]
    terms = json.loads((ROOT / "web/data/glossary.json").read_text(encoding="utf-8"))
    edges, stats = build_term_edges(docs, terms)
    print(json.dumps(stats, ensure_ascii=False))
    by_term: dict[str, int] = {}
    for e in edges:
        by_term[e["term_id"]] = by_term.get(e["term_id"], 0) + 1
    print("top terms:", sorted(by_term.items(), key=lambda kv: -kv[1])[:10])
    if a.dry_run:
        text_by = {f["finding_id"]: f.get("text_ko") or "" for d in docs for f in d.get("findings") or []}
        rnd = random.Random(1103)
        for e in rnd.sample(edges, min(a.sample, len(edges))):
            tx = text_by[e["finding_id"]]
            pos = tx.find(e["surface"])
            ctx = tx[max(0, pos - 25):pos + len(e["surface"]) + 25].replace("\n", " ")
            print(f"| {e['finding_id']} | {e['term_id']} | {e['surface']} | {e['confidence']} | …{ctx}… |")
    if a.emit_sql:
        Path(a.emit_sql).write_text(emit_sql(edges), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
