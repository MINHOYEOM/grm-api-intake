"""지식 그래프 내보내기 최소 버전 — 사이트 빌드 때 `graph/` 아래 공개 정적 파일을 만든다.

설계: 작업공간 루트 `GRM_지식그래프_연결설계_2026-11-04.md` 4절.

원칙
- 순수 함수: 입력(커밋된 정본에서 render.py 가 이미 만든 값)만 받고 네트워크·난수·now() 를 쓰지 않는다.
  같은 입력 → 같은 바이트(정렬 고정, `sort_keys`, 콤팩트 JSON, LF).
- **개인정보 제외**: 노드·엣지는 아래 *허용 필드* 로만 만든다(허용목록). 실사관 이름·참석자·이메일·IP 가
  든 입력 필드(`inspector_names`, `mfds_attendees` 등)는 읽지조차 않는다.
- 엣지는 전부 `confidence`(exact | name_match | embedding) 와 `src_url`(근거 페이지)을 가진다.
  끊긴 엣지(없는 노드 참조)는 만들지 않는다 — 양끝이 노드로 존재할 때만 낸다.
- 영문판 별도 파일은 없다. 노드의 `lang`(= 정본 언어 ko) 과 `url_en`(영문 페이지가 있을 때만)으로 구분한다.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = "1"

NODE_TYPES = ("doc", "firm", "clause", "term", "brief")
EDGE_RELS = ("cited_in_brief", "issued_to", "cites_clause", "uses_term")
CONFIDENCES = ("exact", "name_match", "embedding")

# 허용목록 — 이 밖의 필드는 노드·엣지에 실릴 수 없다(테스트가 파일 전체에서 대조한다).
NODE_FIELDS = frozenset({"id", "type", "label", "label_en", "url", "url_en", "lang", "date",
                         "agency", "source", "doc_count", "alias_keys"})
EDGE_FIELDS = frozenset({"src", "dst", "rel", "confidence", "src_url", "weight"})

PROVENANCE = {
    "generated_by": "machine",
    "site": "https://grm-solutions.com",
    "note": ("공개 규제 문서의 기계 정리 결과다. 근거는 엣지의 src_url 과 노드의 url 을 확인할 것. "
             "confidence=name_match 는 업체명·용어 표기 일치로 이은 추정 연결이다."),
    "personal_data": "실사관·참석자 이름, 이메일, IP 는 포함하지 않는다.",
}


def _dump(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """빈 값은 키를 뺀다(파일을 가볍게, 스키마는 선택 필드 규칙으로 고정)."""
    return {k: v for k, v in row.items() if v not in ("", None, [], ())}


def build_graph(
    *,
    documents: "list[dict[str, Any]]",
    brief_slugs: "Iterable[str]",
    en_brief_slugs: "Iterable[str]",
    card_issue_index: "dict[str, list[dict[str, str]]]",
    en_doc_slugs: "set[str]",
    glossary_terms: "list[dict[str, Any]]",
    term_ids_with_pages: "Iterable[str]",
    doc_term_ids: "Callable[[dict[str, Any]], list[str]]",
    firm_page_slug_by_key: "dict[str, str]",
    canonical_firm_key: "Callable[[str], str]",
    cfr_section_of: "Callable[[Any], str]",
    clause_slugs: "set[str]",
    clause_slug_of: "Callable[[str], str]",
    gmp_new_firms: "Iterable[dict[str, Any]]" = (),
) -> "dict[str, list[dict[str, Any]]]":
    """파일 이름(확장자 제외) → 행 목록. 키: nodes-<type>, edges-<rel>."""
    briefs = sorted({s for s in brief_slugs if s})
    brief_set = set(briefs)
    en_briefs = set(en_brief_slugs)
    nodes: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in NODE_TYPES}
    edges: dict[str, list[dict[str, Any]]] = {r: [] for r in EDGE_RELS}

    def add_node(typ: str, key: str, row: dict[str, Any]) -> None:
        nid = f"{typ}:{key}"
        if nid in nodes[typ]:
            raise ValueError(f"노드 id 중복: {nid}")
        nodes[typ][nid] = _clean({"id": nid, "type": typ, "lang": "ko", **row})

    # ── 브리프 호 ─────────────────────────────────────────────────────────────
    for d in briefs:
        add_node("brief", d, {"label": f"주간 브리프 {d}", "url": f"/briefs/{d}/", "date": d,
                              "url_en": f"/en/briefs/{d}/" if d in en_briefs else ""})

    # ── 용어 ──────────────────────────────────────────────────────────────────
    page_terms = set(term_ids_with_pages)
    for t in sorted(glossary_terms, key=lambda x: str(x.get("id"))):
        tid = str(t.get("id") or "")
        if not tid or tid not in page_terms:
            continue
        add_node("term", tid, {"label": t.get("term_ko") or t.get("term_en") or tid,
                               "label_en": t.get("term_en") or "", "url": f"/glossary/{tid}/"})

    # ── 문서 ──────────────────────────────────────────────────────────────────
    docs = sorted((d for d in documents if d.get("document_id") and d.get("slug")),
                  key=lambda d: str(d["document_id"]))
    by_firm: dict[str, list[dict[str, Any]]] = {}
    alias_keys: dict[str, set[str]] = {}
    for d in docs:
        did = str(d["document_id"])
        slug = d["slug"]
        add_node("doc", did, {
            "label": " · ".join(x for x in (d.get("firm_name") or "", d.get("source") or "",
                                            d.get("published_date") or "") if x),
            "url": f"/findings/doc/{slug}/",
            "url_en": f"/en/findings/doc/{slug}/" if slug in en_doc_slugs else "",
            "date": d.get("published_date") or "", "agency": d.get("agency") or "",
            "source": d.get("source") or ""})
        doc_url = f"/findings/doc/{slug}/"

        # cited_in_brief — #1327 의 카드 id → 호 역색인을 그대로 쓴다(id 일치 = exact).
        for e in card_issue_index.get(did, []):
            if e["date"] not in brief_set:
                continue
            edges["cited_in_brief"].append({
                "src": f"doc:{did}", "dst": f"brief:{e['date']}", "rel": "cited_in_brief",
                "confidence": "exact", "weight": 1,
                "src_url": f"/briefs/{e['date']}/" + (f"#{e['anchor']}" if e.get("anchor") else "")})

        # issued_to — 업체명 정규화 키 일치(name_match). 별칭 그룹은 정본 키로 합친다(#1320).
        raw_key = d.get("firm_key") or ""
        if raw_key:
            fk = canonical_firm_key(raw_key)
            by_firm.setdefault(fk, []).append(d)
            if fk != raw_key:
                alias_keys.setdefault(fk, set()).add(raw_key)
            edges["issued_to"].append({
                "src": f"doc:{did}", "dst": f"firm:{fk}", "rel": "issued_to",
                "confidence": "name_match", "weight": 1, "src_url": doc_url})

        # cites_clause — 21 CFR 조항(하위항은 섹션으로 접음). 인용한 지적 수가 weight.
        cnt: Counter[str] = Counter()
        for f in d.get("findings") or []:
            for sec in {cfr_section_of(r) for r in (f.get("cfr_refs") or [])} - {""}:
                cnt[sec] += 1
        for sec, n in sorted(cnt.items()):
            nid = f"clause:21cfr{sec}"
            if nid not in nodes["clause"]:
                add_node("clause", f"21cfr{sec}", {
                    "label": f"21 CFR {sec}",
                    "url": (f"/findings/clause/{clause_slug_of(sec)}/"
                            if sec in clause_slugs else "")})
            edges["cites_clause"].append({
                "src": f"doc:{did}", "dst": nid, "rel": "cites_clause", "confidence": "exact",
                "weight": n, "src_url": doc_url})

        # uses_term — 문서 페이지의 용어 링크 선정 규칙을 그대로(표기 일치 = name_match).
        for tid in sorted(set(doc_term_ids(d))):
            if f"term:{tid}" in nodes["term"]:
                edges["uses_term"].append({
                    "src": f"doc:{did}", "dst": f"term:{tid}", "rel": "uses_term",
                    "confidence": "name_match", "weight": 1, "src_url": doc_url})

    # ── 업체 ──────────────────────────────────────────────────────────────────
    for fk in sorted(by_firm):
        group = by_firm[fk]
        name = sorted(Counter(g.get("firm_name") or "" for g in group).items(),
                      key=lambda kv: (-kv[1], kv[0]))[0][0]
        slug = firm_page_slug_by_key.get(fk, "")
        add_node("firm", fk, {"label": name or fk, "doc_count": len(group),
                              "url": f"/findings/firm/{slug}/" if slug else "",
                              "alias_keys": sorted(alias_keys.get(fk, ()))})

    # [식약처 GMP 적합판정 새 업체 2026-11-11] 지적·처분이 아직 없는 판정서 업체 — 노드만,
    # 업체 페이지는 만들지 않는다(대표 결정 "지적 생기면 페이지"). 지적이 들어와 판정서가
    # 그 업체에 이어지면 원천 목록(144)에서 빠져 이 노드도 사라진다.
    for g in sorted(gmp_new_firms, key=lambda r: str(r.get("node_key") or "")):
        nk = str(g.get("node_key") or "")
        if not nk.startswith("mfds-gmp-") or f"firm:{nk}" in nodes["firm"]:
            continue
        add_node("firm", nk, {"label": g.get("firm_name") or nk, "agency": "MFDS",
                              "source": "mfds_gmp_cert", "doc_count": 0})

    # ── 정렬 고정 ─────────────────────────────────────────────────────────────
    out: dict[str, list[dict[str, Any]]] = {}
    for t in NODE_TYPES:
        if nodes[t]:
            out[f"nodes-{t}"] = [nodes[t][k] for k in sorted(nodes[t])]
    for r in EDGE_RELS:
        if edges[r]:
            out[f"edges-{r}"] = sorted(edges[r], key=lambda e: (e["src"], e["dst"], e["src_url"]))
    return out


def graph_files(rows_by_name: "dict[str, list[dict[str, Any]]]",
                *, built_at: str) -> "dict[str, str]":
    """파일명 → 본문. manifest.json 포함. built_at 은 **입력에서 파생한 날짜**(now() 금지)."""
    files: dict[str, str] = {}
    meta: dict[str, dict[str, Any]] = {}
    for name in sorted(rows_by_name):
        body = "".join(_dump(r) + "\n" for r in rows_by_name[name])
        fn = f"{name}.jsonl"
        files[fn] = body
        meta[fn] = {"rows": len(rows_by_name[name]), "bytes": len(body.encode("utf-8")),
                    "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
    manifest = {
        "schema_version": SCHEMA_VERSION, "built_at": built_at,
        "node_total": sum(m["rows"] for f, m in meta.items() if f.startswith("nodes-")),
        "edge_total": sum(m["rows"] for f, m in meta.items() if f.startswith("edges-")),
        "files": meta, **PROVENANCE,
    }
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    return files


def write_graph(out_dir: Path, files: "dict[str, str]") -> "list[str]":
    """`<out_dir>/graph/` 에 쓴다. 상대 경로 목록을 돌려준다."""
    gdir = out_dir / "graph"
    gdir.mkdir(parents=True, exist_ok=True)
    written = []
    for fn in sorted(files):
        with open(gdir / fn, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(files[fn])
        written.append(f"graph/{fn}")
    return written


# ── 가드 ─────────────────────────────────────────────────────────────────────
_FORBIDDEN_KEY_PARTS = ("inspector", "attendee", "email", "mail", "phone", "tel", "ip", "address",
                        "person", "investigator")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def validate_graph(rows_by_name: "dict[str, list[dict[str, Any]]]") -> "list[str]":
    """위반 목록(빈 목록 = 통과). 끊긴 엣지 0 · 노드 id 중복 0 · 개인정보 필드 0 · 스키마 고정."""
    errs: list[str] = []
    ids: set[str] = set()
    for name, rows in rows_by_name.items():
        if not name.startswith("nodes-"):
            continue
        for r in rows:
            nid = r.get("id")
            if nid in ids:
                errs.append(f"노드 id 중복: {nid}")
            ids.add(nid)
            if not set(r) <= NODE_FIELDS:
                errs.append(f"{nid}: 허용 밖 노드 필드 {sorted(set(r) - NODE_FIELDS)}")
            if not str(nid).startswith(f"{r.get('type')}:"):
                errs.append(f"{nid}: id 접두와 type 불일치")
    for name, rows in rows_by_name.items():
        if not name.startswith("edges-"):
            continue
        for r in rows:
            tag = f"{r.get('src')}->{r.get('dst')}"
            if not set(r) <= EDGE_FIELDS:
                errs.append(f"{tag}: 허용 밖 엣지 필드 {sorted(set(r) - EDGE_FIELDS)}")
            if r.get("src") not in ids or r.get("dst") not in ids:
                errs.append(f"끊긴 엣지: {tag}")
            if r.get("rel") not in EDGE_RELS or name != f"edges-{r.get('rel')}":
                errs.append(f"{tag}: rel 이상 {r.get('rel')}")
            if r.get("confidence") not in CONFIDENCES:
                errs.append(f"{tag}: confidence 이상 {r.get('confidence')}")
            if not r.get("src_url"):
                errs.append(f"{tag}: src_url 없음")
    for name, rows in rows_by_name.items():
        for r in rows:
            for k, v in r.items():
                if any(part in k.lower().split("_") or k.lower() == part
                       for part in _FORBIDDEN_KEY_PARTS):
                    errs.append(f"{name}: 개인정보 의심 필드명 {k}")
                vals = v if isinstance(v, list) else [v]
                for x in vals:
                    if isinstance(x, str) and (_EMAIL_RE.search(x) or _IPV4_RE.search(x)):
                        errs.append(f"{name}: 이메일/IP 형태 값 {r.get('id') or r.get('src')}")
    return errs
