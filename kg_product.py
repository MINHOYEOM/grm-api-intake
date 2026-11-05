#!/usr/bin/env python3
"""지적↔제품군 엣지(finding_product) — 지적 단위로, **명시된 근거가 있을 때만** 연결한다.

원칙(대표 지시): 틀리게 잇느니 안 잇는다 · 모호하면 연결 안 함 · 모든 연결에 확실도·근거.
  * 합성 의약품 캐치올 금지 — 제품군 정보가 없으면 행을 만들지 않는다(문서/업체 수준 값을 지적에 물려주지 않는다).
  * 축 2개: modality(Chemical|Biologic — grm_taxonomy 와 같은 어휘) · dosage_form(tablet|capsule|syrup|ointment|injection).
  * 근거 두 종류만 본다.
      (A) 제품명 필드 — MFDS 회수(raw_json.PRDUCT)·행정처분(raw_json.ITEM_NAME). 제품명에 제형이 적힌 경우.
          '정'(정제)·'주'(주사) 접미사는 한약재(황정 등)와 겹치므로 용량·성분 괄호 같은 **보강 단서가 있을 때만** 인정한다.
      (B) 지적 본문 — **FDA 483 에 한정**. 483 관찰사항은 실사받은 업체의 제품을 서술한다. 경고서한(WL)은
          광고 문구·타 기관 안내·CBER 주소 같은 상투구가 많아 본문 어휘로는 오탐이 크므로 쓰지 않는다. HC·EU·MHRA 는
          본문/필드에 제품 정보가 없다(EU product_description 은 제조 활동 코드 목록).
  * Chemical 은 경구 고형/액체 제형이 명시된 경우만(grm_taxonomy 의 '경구=합성' 근거와 동일). 식물 추출물·효소·균주
    성분이면 제형은 연결해도 modality 는 연결하지 않는다.
  * Biologic 은 생물 어휘(백신·단클론항체·인슐린 등)가 제품명/483 본문에 명시된 경우만.

사용:
  python kg_product.py --from-db --dry-run [--sample 100]   # 연결 수·유형별 비율·무작위 표본(표). DB 읽기만
  python kg_product.py --from-db --emit-sql out.sql         # finding_product INSERT 배치(DB 에 쓰지 않는다)
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from typing import Any, Iterable

import grm_taxonomy as tx

MIN_CONFIDENCE = 0.8

# ── (A) 제품명 ─────────────────────────────────────────────────────────────────
_STRENGTH = r"(?:\d[\d.,/]*\s*(?:mg|㎎|g|㎍|mcg|밀리그램|마이크로그램|그램|단위|IU|mL|ml|%))"
# 괄호 성분(…) 를 떼고 본 이름에서 제형 접미사를 찾는다.
_RE_PAREN = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")

_RE_CAPSULE = re.compile(r"캡슐(?![가-힣])|캅셀")
_RE_SYRUP = re.compile(r"시럽(?![가-힣])")
_RE_OINTMENT = re.compile(r"연고(?![가-힣])")
_RE_INJ_WORD = re.compile(r"주사액|주사제|주사(?![가-힣])")
# 정/주 접미사 — 뒤가 한글이면 아님(안정·주사위 등). 보강 단서(숫자 직전 or 용량/성분 괄호 직후)가 있어야 한다.
_RE_TAB_SUFFIX = re.compile(r"(?:\d|[가-힣A-Za-z])정(?![가-힣])")
_RE_INJ_SUFFIX = re.compile(r"(?:\d|[가-힣A-Za-z])주(?![가-힣])")

# modality 를 막는 성분 — 식물 추출·효소·균주·한약. (제형 연결은 유지)
_NOT_CHEMICAL_HINTS = ("추출", "생약", "한약", "효소", "키나제", "키나아제", "도르나제", "프로테아제",
                       "균", "유산균", "락토바실", "프로바이오", "펩신", "판크레아틴", "리파제", "아밀라제",
                       "단백", "호르몬", "항체", "인슐린", "혈장", "태반")


_BIO_NAME_TERMS = tuple(t for t in tx.MODALITY_BIOLOGIC_TERMS if t not in ("자하거", "태반추출물", "atmp", "ich q5"))


def _split_names(raw: str) -> list[str]:
    """깊이 0 의 쉼표로만 나눈다(괄호 안 쉼표는 성분 나열)."""
    out, depth, cur = [], 0, []
    for ch in raw:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch in ",，" and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return [s for s in out if s]


def _form_of_segment(seg: str) -> tuple[str, float, str] | None:
    """(dosage_form, confidence, 근거 조각) 또는 None."""
    bare = _RE_PAREN.sub("", seg).strip()
    if not bare:
        return None
    has_paren = bare != seg.strip()
    if _RE_CAPSULE.search(bare):
        return "capsule", 0.95, "캡슐"
    if _RE_SYRUP.search(bare):
        return "syrup", 0.95, "시럽"
    if _RE_OINTMENT.search(bare):
        return "ointment", 0.95, "연고"
    if _RE_INJ_WORD.search(bare):
        return "injection", 0.95, "주사"
    # '…정'/'…주' 접미사: 이름 끝(용량 표기·괄호 성분 앞)에서 보강 단서가 있을 때만.
    m = re.search(r"([가-힣A-Za-z0-9])(정|주)(?=\s*(?:" + _STRENGTH + r"|\d|$))", bare)
    if m:
        strengthened = bool(re.search(_STRENGTH, bare)) or bool(re.search(r"\d(정|주)", bare)) or has_paren
        # 이름이 '정/주' 로 끝나는 경우는 괄호 성분(has_paren)이 있어야 한다. 용량·숫자 접속은 그 자체가 단서.
        if m.group(2) == "정" and (strengthened or re.search(r"\d", bare)):
            return "tablet", 0.9, "정+용량/성분"
        if m.group(2) == "주" and (strengthened or re.search(r"\d", bare)):
            return "injection", 0.9, "주+용량/성분"
    return None


def classify_name(name: str) -> list[dict[str, Any]]:
    """제품명 문자열 → 엣지 후보. 쉼표로 나열된 여러 품목은 각각 본다."""
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(axis: str, value: str, conf: float, basis: str, ev: str) -> None:
        if (axis, value) not in seen and conf >= MIN_CONFIDENCE:
            seen.add((axis, value))
            edges.append({"axis": axis, "value": value, "confidence": conf, "basis": basis, "evidence": ev[:120]})

    for seg in _split_names(name or ""):
        # 생물 어휘가 명시된 제품 — grm_taxonomy 판정이 Biologic 일 때만 채택(같은 어휘 한 벌).
        # 브랜드 어간(자닥신 등)·태반/자하거 추출물 같은 경계 사례는 제외 — 제품명에 생물 어휘가 **글자 그대로** 있을 때만.
        if (tx.compute_modality({"PRDUCT": seg}, seg) == tx.MODALITY_BIOLOGIC
                and any(t in seg.lower() for t in _BIO_NAME_TERMS)):
            add("modality", "Biologic", 0.9, "name_biologic_term", seg)
        form = _form_of_segment(seg)
        if form is None:
            continue
        value, conf, why = form
        add("dosage_form", value, conf, f"name_form:{why}", seg)
        parens = " ".join(_RE_PAREN.findall(seg))
        parens = " ".join(_RE_PAREN.findall(seg))
        # 경구 제형이라도 용량 표기나 성분 괄호 같은 보강 단서가 없으면(생약 시럽·건강기능 캡슐 가능성) modality 는 잇지 않는다.
        corroborated = bool(re.search(_STRENGTH, seg)) or bool(_RE_PAREN.search(seg))
        if (value in ("tablet", "capsule", "syrup") and corroborated and "엑스" not in parens
                and not any(h in seg for h in _NOT_CHEMICAL_HINTS)):
            if tx.compute_modality({"PRDUCT": seg}, seg) != tx.MODALITY_BIOLOGIC:
                add("modality", "Chemical", 0.85, "name_oral_form", seg)
    return edges


# ── (B) FDA 483 본문 ──────────────────────────────────────────────────────────
_RE_483_BIO = re.compile(
    r"\b(vaccines?|monoclonal antibod(?:y|ies)|biosimilars?|immune globulin|immunoglobulin|plasma[- ]derived|"
    r"insulin (?:aspart|lispro|glargine|detemir|degludec))\b", re.I)
_RE_483_TABLET = re.compile(r"\btablets?\b", re.I)
_RE_483_CAPSULE = re.compile(r"\bcapsules?\b", re.I)
_RE_483_NOT_BIO_CONTEXT = re.compile(r"(?i)syringe")  # 'insulin syringes' 등 기구 언급 방어


def classify_483_text(text: str) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    t = text or ""
    m = _RE_483_BIO.search(t)
    if m:
        edges.append({"axis": "modality", "value": "Biologic", "confidence": 0.9, "basis": "483_text_biologic",
                      "evidence": t[max(0, m.start() - 30): m.end() + 30].replace("\n", " ")[:120]})
    bio_hit = m is not None
    for rx, form in ((_RE_483_TABLET, "tablet"), (_RE_483_CAPSULE, "capsule")):
        mm = rx.search(t)
        if mm:
            edges.append({"axis": "dosage_form", "value": form, "confidence": 0.9, "basis": "483_text_form",
                          "evidence": t[max(0, mm.start() - 30): mm.end() + 30].replace("\n", " ")[:120]})
            if not bio_hit and not any(e["value"] == "Chemical" for e in edges):
                edges.append({"axis": "modality", "value": "Chemical", "confidence": 0.8, "basis": "483_text_oral_form",
                              "evidence": t[max(0, mm.start() - 30): mm.end() + 30].replace("\n", " ")[:120]})
    return edges


def classify_finding(row: dict[str, Any]) -> list[dict[str, Any]]:
    """row: {finding_id, agency, document_type, finding_text, raw: dict|None}. 근거 없으면 []."""
    agency = row.get("agency") or ""
    raw = row.get("raw") or {}
    edges: list[dict[str, Any]] = []
    if agency == "MFDS":
        name = raw.get("PRDUCT") or raw.get("ITEM_NAME") or ""
        if name:
            edges = classify_name(str(name))
    elif agency == "FDA" and row.get("document_type") == "483":
        edges = classify_483_text(row.get("finding_text") or "")
    return [{"finding_id": row["finding_id"], **e} for e in edges]


def build_product_edges(rows: Iterable[dict[str, Any]]) -> tuple[list[dict], dict]:
    edges: list[dict] = []
    per_type: dict[str, list[int]] = {}
    for r in rows:
        key = (r.get("agency") or "") + ":" + (r.get("document_type") if r.get("agency") != "FDA" or r.get("document_type") == "483" else "WL/etc")
        c = per_type.setdefault(key, [0, 0])
        c[0] += 1
        e = classify_finding(r)
        if e:
            c[1] += 1
            edges.extend(e)
    stats = {"edges": len(edges), "findings_with_edge": len({e["finding_id"] for e in edges}),
             "by_type": {k: {"findings": v[0], "linked": v[1], "ratio": round(v[1] / v[0], 4) if v[0] else 0}
                         for k, v in sorted(per_type.items())}}
    return edges, stats


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def emit_sql(edges: list[dict], batch: int = 500) -> str:
    out = ["-- finding_product 백필(dry-run 산출물 — 적용 전 검토). 멱등: on conflict do nothing.", "begin;"]
    rows = sorted(edges, key=lambda e: (e["finding_id"], e["axis"], e["value"]))
    for i in range(0, len(rows), batch):
        vals = ",\n".join(
            f"  ({_sql_str(e['finding_id'])}, {_sql_str(e['axis'])}, {_sql_str(e['value'])}, "
            f"{e['confidence']}, {_sql_str(e['basis'])}, {_sql_str(e['evidence'])})" for e in rows[i:i + batch])
        out.append("insert into public.finding_product (finding_id, axis, value, confidence, basis, evidence)\n"
                   "select v.finding_id, v.axis, v.value, v.confidence, v.basis, v.evidence from (values\n"
                   + vals + "\n) as v(finding_id, axis, value, confidence, basis, evidence)\n"
                   "join public.findings f on f.finding_id = v.finding_id\n"
                   "on conflict do nothing;")
    out.append("commit;")
    return "\n".join(out) + "\n"


# ── DB 읽기(백필·일일 증분 공용) ───────────────────────────────────────────────
PAGE = 1000


def fetch_rows(base: str, key: str, since_iso: str | None = None, timeout: float = 120.0) -> list[dict[str, Any]]:
    """MFDS 는 raw_signals.raw_json 의 제품명, 483 은 본문만 가져온다. (읽기 전용)"""
    import requests

    def hdr(**extra: str) -> dict[str, str]:
        return {"apikey": key, "Authorization": f"Bearer {key}", **extra}

    def page(path: str, params: dict[str, str]) -> list[dict]:
        out: list[dict] = []
        off = 0
        while True:
            try:
                r = requests.get(f"{base}/rest/v1/{path}", params=params, timeout=timeout,
                                 headers=hdr(Range=f"{off}-{off + PAGE - 1}"))
            except requests.RequestException as exc:
                raise RuntimeError(f"요청 실패: {type(exc).__name__}") from None
            if r.status_code not in (200, 206):
                raise RuntimeError(f"{path} HTTP {r.status_code}")
            data = r.json()
            out.extend(data)
            if len(data) < PAGE:
                return out
            off += PAGE

    base_params = {"select": "finding_id,raw_signal_id,agency,document_type,finding_text", "order": "finding_id.asc"}
    if since_iso:
        base_params["ingested_at"] = f"gte.{since_iso}"
    rows = page("findings", {**base_params, "or": "(agency.eq.MFDS,document_type.eq.483)"})
    mfds_ids = sorted({r["raw_signal_id"] for r in rows if r["agency"] == "MFDS"})
    raws: dict[str, dict] = {}
    for i in range(0, len(mfds_ids), 100):
        chunk = mfds_ids[i:i + 100]
        for r in page("raw_signals", {"select": "raw_signal_id,raw_json",
                                      "raw_signal_id": "in.(" + ",".join(f'"{c}"' for c in chunk) + ")"}):
            try:
                raws[r["raw_signal_id"]] = json.loads(r["raw_json"]) if str(r["raw_json"]).startswith("{") else {}
            except ValueError:
                raws[r["raw_signal_id"]] = {}
    for r in rows:
        r["raw"] = raws.get(r["raw_signal_id"]) if r["agency"] == "MFDS" else None
    return rows


def write_edges(base: str, key: str, edges: list[dict[str, Any]], timeout: float = 120.0) -> int | None:
    """finding_product 에 멱등 삽입(ignore-duplicates). 표 미적용(404)이면 None."""
    import requests

    sent = 0
    for i in range(0, len(edges), 500):
        chunk = [{k: e[k] for k in ("finding_id", "axis", "value", "confidence", "basis", "evidence")}
                 for e in edges[i:i + 500]]
        try:
            r = requests.post(
                f"{base}/rest/v1/finding_product",
                headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                         "Prefer": "resolution=ignore-duplicates,return=minimal"},
                params={"on_conflict": "finding_id,axis,value"}, json=chunk, timeout=timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"요청 실패: {type(exc).__name__}") from None
        if r.status_code == 404:
            return None
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"finding_product HTTP {r.status_code}")
        sent += len(chunk)
    return sent


def link_recent(base: str, key: str, since_iso: str) -> tuple[int, int | None]:
    """일일 증분: (연결 후보 수, 전송 수 | None=111 미적용). kg_link_new.py 가 부른다."""
    edges, _stats = build_product_edges(fetch_rows(base, key, since_iso))
    return len(edges), write_edges(base, key, edges)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--rows-json", help="[{finding_id,agency,document_type,finding_text,raw}] 파일(오프라인 측정)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--emit-sql")
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    a = ap.parse_args(argv)
    if a.rows_json:
        with open(a.rows_json, encoding="utf-8") as fh:
            rows = json.load(fh)
    elif a.from_db:
        from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
        creds = resolve_supabase_service_credentials(a)
        base = normalize_supabase_url(creds[0]) if creds else None
        if not creds or base is None:
            print("[kg-product] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
            return 2
        rows = fetch_rows(base, creds[1])
    else:
        ap.error("--from-db 또는 --rows-json 필요")
    edges, stats = build_product_edges(rows)
    print(json.dumps(stats, ensure_ascii=False))
    if a.dry_run and edges:
        rnd = random.Random(1105)
        for e in rnd.sample(edges, min(a.sample, len(edges))):
            print(f"| {e['finding_id']} | {e['axis']}={e['value']} | {e['confidence']} | {e['basis']} | {e['evidence']} |")
    if a.emit_sql:
        with open(a.emit_sql, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(emit_sql(edges))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
