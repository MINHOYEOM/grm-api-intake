#!/usr/bin/env python3
"""링크드인 '사건 해부' GRM 판(시범 v3) — 서한 원문을 조항별로 깊이 파고, 우리 DB 의 유사 사례를 붙인다.

v1(`linkedin_anatomy.py`)은 벤치마크 6칸을 거의 그대로 옮겼다. 대표 피드백(11-03): ① 따라한 느낌이 크다
② 우리 사이트 소개보다 서한/483 원문 자체를 깊이 ③ 매 편 같은 고정 포맷 ④ DB 유사 사례(연도·기관·반복).

## 고정 포맷 — 매 편 같은 순서 (`company/editorial/STYLE.md` §11)

카드: 01 판정 · 02 지적 내용 · 03 회신 평가 · 04 시정 요구 · 05 유사 사례 · 06 서한에 없는 것
본문: ① 판정 한 줄 ② 지적(조항별) ③ 회신이 부적절한 이유 ④ 시정 요구 ⑤ 유사 사례(DB) ⑥ 서한에 없는 것 ⑦ 링크

## 사실의 출처 — 코드·DB 가 쥐고 LLM 은 슬롯만 (비용 0)
  · 서한 문장은 카드 원천의 verbatim 부분 문자열(근거)로만 인용하고, 슬롯 숫자는 근거 안에 있어야 한다.
  · 유사 사례 칸은 전부 `db_similar` 가 `findings_docs.json` 에서 센 값이다 — 슬롯은 한 줄 해설만 쓰고
    그 숫자도 DB 값 안에 있어야 한다.
  · 세 겹 표시: 서한 원문(검정) / GRM 해설(크림) / 서한에 없음(노랑).
유료 API 호출 코드는 없다. 현행 출력(`linkedin_cards`) 불변.
"""
from __future__ import annotations

import argparse
import html
import datetime as _dt
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB_DIR))
import linkedin_anatomy as la  # noqa: E402
import linkedin_cards as lc  # noqa: E402
import utm  # noqa: E402

SCHEMA = "grm-anatomy-slots/v3"
SLOTS_DIR = la.SLOTS_DIR
FINDINGS = WEB_DIR / "data" / "findings_docs.json"

# PET 의약품(21 CFR 212) 조항 → 같은 취지의 일반 cGMP(21 CFR 211) 조항. DB 에 212 인용 서한이 거의 없어
# 대응 조항으로 센다. 매핑은 규정 해석이라 카드에 '대응 조항 기준'을 밝히고 사람이 확인한다 [검증필요].
CFR_MAP = [("212.20(d) 부적합 조사·시정", "21 CFR 211.192"),
           ("212.30(b) 설비 적합성", "21 CFR 211.67"),
           ("ISO 5·EM 무균 관리", "21 CFR 211.113(b)")]

LIMITS = {"headline_line": 15.5, "row_text": 40, "pair_resp": 24, "pair_why": 74, "item_title": 14, "item_text": 62,
          "db_comment": 70, "ns": 74, "question": 40}


_EM_TOPIC = re.compile(r"ISO 5|ISO class 5|Grade A|Class 100|environmental monitor|viable|microbial (?:recover|monitor)|isolates?", re.I)


def _is_wl(d: dict) -> bool:
    return d.get("source") == "FDA Warning Letter"


# ── 코드가 세는 사실: 지적사항 DB ───────────────────────────────────────────────────────────────

def _firm_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[\s,.()&]+", str(name or ""))
            if len(t) >= 4 and t.lower() not in ("inc", "llc", "ltd", "corp", "company", "co", "pharma", "pharmaceutical")]


def db_similar(findings: dict, card: dict, *, n_cases: int = 3) -> dict[str, Any]:
    """같은 주제(환경모니터링)·대응 조항으로 지적된 과거 사례. 전부 센 값 — 문장을 만들지 않는다."""
    docs = findings.get("documents") or []
    tgt = (lc.firm_name(card) or "").lower()
    by_firm: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_firm[d.get("firm_key")].append(d)
    wls = [d for d in docs if _is_wl(d)]
    em = [d for d in wls if "환경모니터링" in (d.get("categories") or []) and not (tgt and tgt in str(d.get("firm_name")).lower())]
    years = Counter(str(d["published_date"])[:4] for d in em)
    firms = {d["firm_key"] for d in em}
    b483_before = 0
    for k in firms:
        wl_first = min(x["published_date"] for x in by_firm[k] if _is_wl(x))
        if any(x.get("source") == "FDA 483" and x["published_date"] < wl_first for x in by_firm[k]):
            b483_before += 1
    repeat = sum(1 for k in firms if sum(1 for x in by_firm[k] if _is_wl(x)) > 1)
    cfr_rows = []
    for label, prefix in CFR_MAP:
        hit = [d for d in wls if any(r.startswith(prefix) for f in d.get("findings") or [] for r in f.get("cfr_refs") or [])]
        cfr_rows.append(dict(label=label, cfr=prefix, n=len(hit), recent=sum(1 for d in hit if str(d["published_date"]) >= "2025-01-01")))
    pet = [d for d in wls if any("21 CFR 212" in r for f in d.get("findings") or [] for r in f.get("cfr_refs") or [])
           and not (tgt and tgt in str(d.get("firm_name")).lower())]
    cases, tiers, id_n = similar_cases(wls, tgt, n_cases)
    return dict(measured_on=findings.get("measured_on") or "", wl_total=len(wls), em_n=len(em), em_years=dict(sorted(years.items())),
                em_firms=len(firms), em_483_before=b483_before, em_repeat=repeat, cfr_rows=cfr_rows, pet_other=len(pet),
                cases=cases, tiers=tiers, pool=tiers["both"] + tiers["pattern"], id_n=id_n)


# 개별 유사 사례 — 태그 집계가 아니라 서한 문장에서 사실이 겹치는 것만. 겹침 판정은 정규식(코드)이다.
_ISO5 = re.compile(r"ISO ?\(?5|Grade A|Class ?100", re.I)
_ID_NEG = re.compile(r"(?:not|fail\w*|without|did not|never|no)\b[^.]{0,80}(?:identif|speciat)|(?:identif|speciat)\w*[^.]{0,80}(?:not|fail|without|inadequate)", re.I)
FACETS = {
    "iso5_recovery": ("ISO 5에서 균이 나온 뒤 조사·평가가 부족하다는 지적",
                      lambda s: bool(_ISO5.search(s) and re.search(r"recover|isolate|excursion|mold|growth", s, re.I)
                                     and re.search(r"investigat|product evaluation|corrective action|impact|remediat|evaluat", s, re.I))),
    "smoke_dynamic": ("스모크 스터디에 동적 조건이 빠졌다는 지적",
                      lambda s: bool(re.search(r"smoke|airflow visuali", s, re.I) and re.search(r"dynamic", s, re.I)
                                     and re.search(r"fail|inadequate|did not|not (?:include|perform|conduct)|lack|ensure", s, re.I))),
    "capa_effect": ("CAPA 효과성 확인이 부족하다는 지적",
                    lambda s: bool(re.search(r"CAPA|corrective", s, re.I) and re.search(r"effectiveness", s, re.I)
                                   and re.search(r"fail|without|lack|did not|no defined", s, re.I)
                                   and not re.search(r"should include|should be|ensure", s, re.I))),
}


def _sentences(text: Any) -> list[str]:
    return re.split(r"(?<=[.;])\s+", re.sub(r"\s+", " ", str(text or "")))


_KO_KEY = re.compile(r"ISO ?\(?5|Grade A|스모크|기류|CAPA|효과성|곰팡이|집락")


def _fact(fs: list[dict]) -> str:
    """서한 한국어 번역(text_ko)에서 코드로 뽑은 서한별 고유 사실 한 줄 — 직접 쓰지 않고, 핵심어가 든 원문 문장의 그 부근을 자른다."""
    for f in fs:
        for sent in re.split(r"(?<=\.)\s+", re.sub(r"\s+", " ", str(f.get("text_ko") or ""))):
            m = _KO_KEY.search(sent)
            if not m or len(sent) < 34:
                continue
            sent = re.sub(r"^(?:또한\s*)?귀사는\s*", "", sent.strip()).rstrip(".")
            m = _KO_KEY.search(sent)
            st = sent.rfind(" ", 0, max(m.start() - 14, 0)) + 1 if m.start() > 14 else 0
            out = sent[st:st + 50]
            if st + 50 < len(sent):
                out = out[:max(out.rfind(" "), 30)] + "…"
            return ("…" if st else "") + out
    return ""


LEVELS = {"both": "조항·사실 일치", "pattern": "사실 패턴 일치", "clause": "조항만 일치"}
_CLAUSES = tuple(prefix for _, prefix in CFR_MAP)


def similar_cases(wls: list[dict], tgt: str, n_cases: int = 3) -> tuple[list[dict], dict, int]:
    """과거 경고서한을 닮은 정도 순으로 고른다 — 정확히 일치가 모자라면 단계적으로 넓힌다.
      조항·사실 일치: 겹치는 지적이 있고 서한이 대응 조항(211.192·211.67·211.113(b))을 인용한 서한
      사실 패턴 일치: 조항은 달라도 겹치는 지적(ISO 5 검출 대응 부족·스모크 동적·CAPA 효과성)이 2가지 이상
      조항만 일치:    위 둘로 3건이 안 차면, 환경모니터링 지적에 대응 조항이 인용된 최근 서한
    정렬: 단계 → 겹침 수 ↓ → 최신 ↓ → slug. 반환: (상위 n건, 단계별 후보 수, ISO 5 동정 미흡 서한 수)."""
    found: dict[str, list[dict]] = {"both": [], "pattern": [], "clause": []}
    id_n = 0
    for d in wls:
        if tgt and tgt in str(d.get("firm_name")).lower():
            continue
        hit: dict[str, dict] = {}
        idneg = False
        for f in d.get("findings") or []:
            for s in _sentences(f.get("text_orig")):
                if _ISO5.search(s) and _ID_NEG.search(s):
                    idneg = True
                for key, (_, fn) in FACETS.items():
                    if key not in hit and fn(s):
                        hit[key] = f
        id_n += idneg
        refs_of = lambda fs: [r for f in fs for r in (f.get("cfr_refs") or []) if r.startswith(_CLAUSES)]  # noqa: E731
        # 조항은 겹치는 문장이 든 지적과 같은 주제 분류의 지적 어디에서든 인용되면 인정한다(문장 지적에만 조항이 달리지 않는 서한이 있다)
        cats = {f.get("category_label_ko") for f in hit.values()}
        doc_refs = refs_of([f for f in d.get("findings") or [] if f.get("category_label_ko") in cats])
        base = dict(year=str(d["published_date"])[:4], date=str(d["published_date"]), agency="FDA 경고서한",
                    slug=str(d.get("slug") or ""), facets=[FACETS[k][0] for k in FACETS if k in hit])
        if hit and doc_refs:
            found["both"].append(dict(base, level="both", cfr=(refs_of(hit.values()) or doc_refs)[0], fact=_fact(list(hit.values())),
                                      strict=bool(refs_of(hit.values()))))
        elif len(hit) >= 2:
            cfr = next((r for f in d.get("findings") or [] for r in (f.get("cfr_refs") or []) if r.startswith("21 CFR 21")), "")
            found["pattern"].append(dict(base, level="pattern", cfr=cfr or "211·212 인용 없음", fact=_fact(list(hit.values()))))
        else:
            em = [f for f in d.get("findings") or [] if f.get("category_label_ko") == "환경모니터링" and refs_of([f])]
            if em:
                found["clause"].append(dict(base, level="clause", cfr=refs_of(em)[0], facets=[], fact=_fact(em)))
    for lv in found:
        found[lv].sort(key=lambda c: (not c.get("strict"), -len(c["facets"]), [-ord(ch) for ch in c["date"]], c["slug"]))
    # 독자에게 닮은 정도의 폭을 보이려고 단계마다 먼저 한 건씩(조항·사실 → 패턴 → 조항만), 남는 자리는 가까운 단계부터 채운다.
    cases = [found[lv][0] for lv in ("both", "pattern", "clause") if found[lv]][:n_cases]
    for lv in ("both", "pattern", "clause"):
        for c in found[lv][1:]:
            if len(cases) < n_cases:
                cases.append(c)
    cases.sort(key=lambda c: list(LEVELS).index(c["level"]))
    for i, c in enumerate(cases):
        c["alias"] = f"업체 {'ABC'[i]}"
        c["label"] = LEVELS[c["level"]]
    return cases, {k: len(v) for k, v in found.items()}, id_n


def db_numbers(db: dict) -> set[str]:
    blob = " ".join(str(v) for v in (db["measured_on"], db["wl_total"], db["em_n"], db["em_firms"], db["em_483_before"],
                                     db["em_repeat"], db["pet_other"], db["pool"], db["id_n"], *db["tiers"].values(),
                                     *db["em_years"].keys(), *db["em_years"].values(), *(c["year"] for c in db["cases"])))
    for r in db["cfr_rows"]:
        blob += f" {r['n']} {r['recent']} {r['cfr']}"
    return la._nums(blob)


# ── 검증 ─────────────────────────────────────────────────────────────────────────────────────

def validate_v3(card: dict, slots: dict, findings: dict) -> list[str]:
    probs: list[str] = []
    src = la.source_text(card)
    db = db_similar(findings, card)
    allowed = la._nums(src) | db_numbers(db)
    if slots.get("schema") != SCHEMA:
        probs.append(f"schema 가 {SCHEMA} 가 아니다")
    if str(slots.get("card_id")) != str(card.get("id")):
        probs.append("card_id 가 대상 카드와 다르다")
    cv, s2, s3, s4, s5, s6 = (slots.get(k) or {} for k in ("cover", "s02", "s03", "s04", "s05", "s06"))
    items = ((card.get("deep_analysis") or {}).get("required_remediation") or {}).get("items") or []

    texts: list[tuple[str, str]] = []
    texts += [(f"cover.headline[{i}]", x) for i, x in enumerate(cv.get("headline") or [])]
    texts += [("cover.sub", cv.get("sub") or ""), ("cover.dots.caption", (cv.get("dots") or {}).get("caption") or ""),
              ("cover.line", cv.get("line") or "")]
    texts += [(f"s02.rows[{i}]", r.get("text") or "") for i, r in enumerate(s2.get("rows") or [])]
    texts += [("s03.sub", s3.get("sub") or ""), ("s03.repeat", (s3.get("repeat") or {}).get("text") or "")]
    for i, p in enumerate(s3.get("pairs") or []):
        texts += [(f"s03.pairs[{i}].response", p.get("response") or ""), (f"s03.pairs[{i}].why", p.get("why") or "")]
    for i, it in enumerate(s4.get("items") or []):
        texts += [(f"s04.items[{i}].title", it.get("title") or ""), (f"s04.items[{i}].text", it.get("text") or "")]
    texts += [("s05.comment", s5.get("comment") or "")]
    texts += [(f"s06.not_stated[{i}]", x.get("text") or "") for i, x in enumerate(s6.get("not_stated") or [])]
    texts += [("s06.question", s6.get("question") or "")]
    cap = slots.get("caption") or {}
    texts += [(f"caption.{k}", cap.get(k) or "") for k in ("lead", "claims", "contrast", "requests", "similar", "not_stated")]
    for path, t in texts:
        m = la._AI_TELL_RE.search(t or "")
        if m:
            probs.append(f"{path}: AI 티 표현 '{m.group(0)}'")
        extra = la._nums(t) - allowed
        if extra:
            probs.append(f"{path}: 원천·DB 에 없는 숫자 {sorted(extra)}")

    def _ev_ok(path: str, ev: Any, text: str = "") -> None:
        evs = la._ev_list(ev)
        if not evs:
            probs.append(f"{path}: 근거 없음")
            return
        for e in evs:
            if e not in src:
                probs.append(f"{path}: 근거가 원천에 없다 — {e[:40]}…")
        if text and la._nums(text) - la._nums(" ".join(evs)):
            probs.append(f"{path}: 근거에 없는 숫자 {sorted(la._nums(text) - la._nums(' '.join(evs)))}")

    dots = cv.get("dots") or {}
    _ev_ok("cover.dots", dots.get("evidence"))
    if not {str(dots.get("total")), str(dots.get("marked"))} <= la._nums(" ".join(la._ev_list(dots.get("evidence")))):
        probs.append("cover.dots: total·marked 가 근거 안의 숫자여야 한다")
    if not (isinstance(dots.get("total"), int) and 1 <= dots["total"] <= 100 and 0 <= dots.get("marked", -1) <= dots["total"]):
        probs.append("cover.dots: total 1~100, marked ≤ total")
    for e in la._ev_list(cv.get("line_evidence")):
        if e not in src:
            probs.append(f"cover.line_evidence: 근거가 원천에 없다 — {e[:40]}…")

    rows = s2.get("rows") or []
    if len(rows) != 4:
        probs.append("s02.rows 는 4개(조항별 지적)")
    for i, r in enumerate(rows):
        _ev_ok(f"s02.rows[{i}]", r.get("evidence"), r.get("text") or "")
        if not _ns(r.get("cite")):
            probs.append(f"s02.rows[{i}].cite 비어 있음")

    pairs = s3.get("pairs") or []
    if not 3 <= len(pairs) <= 5:
        probs.append("s03.pairs 는 3~5개")
    for i, p in enumerate(pairs):
        _ev_ok(f"s03.pairs[{i}]", p.get("evidence"), (p.get("response") or "") + " " + (p.get("why") or ""))
    _ev_ok("s03.repeat", (s3.get("repeat") or {}).get("evidence"), (s3.get("repeat") or {}).get("text") or "")

    refs = sorted(it.get("ref") for it in s4.get("items") or [] if isinstance(it.get("ref"), int))
    if refs != list(range(min(len(items), 4))):
        probs.append(f"s04.items 가 서한의 FDA 요구 {min(len(items), 4)}개를 모두 다뤄야 한다(ref {refs})")
    if not db["em_n"]:
        probs.append("s05: DB 에서 같은 주제 사례를 찾지 못했다")

    ns = s6.get("not_stated") or []
    if len(ns) not in (2, 3) or any(not x.get("basis") for x in ns):
        probs.append("s06.not_stated 는 2~3개이고 각각 basis(무엇을 보고 없다고 했는지)가 있어야 한다")
    if not la._norm(s6.get("question")).endswith("?"):
        probs.append("s06.question 은 질문형(?)이어야 한다")

    firm_tokens = [t for t in _firm_tokens(card.get("headline_target"))]
    blob = json.dumps(slots, ensure_ascii=False).lower()
    probs += [f"업체 실명 노출: {t}" for t in firm_tokens if t.lower() in blob]

    def w(label: str, text: str, limit: float) -> None:
        if lc.text_width(text) > limit:
            probs.append(f"{label}: 너무 길다({lc.text_width(text):.0f} > {limit:g})")
    for i, x in enumerate(cv.get("headline") or []):
        w(f"cover.headline[{i}]", x, LIMITS["headline_line"])
    for i, r in enumerate(rows):
        w(f"s02.rows[{i}]", r.get("text") or "", LIMITS["row_text"])
    for i, p in enumerate(pairs):
        w(f"s03.pairs[{i}].response", p.get("response") or "", LIMITS["pair_resp"])
        w(f"s03.pairs[{i}].why", p.get("why") or "", LIMITS["pair_why"])
    for i, it in enumerate(s4.get("items") or []):
        w(f"s04.items[{i}].title", it.get("title") or "", LIMITS["item_title"])
        w(f"s04.items[{i}].text", it.get("text") or "", LIMITS["item_text"])
    w("s05.comment", s5.get("comment") or "", LIMITS["db_comment"])
    for i, x in enumerate(ns):
        w(f"s06.not_stated[{i}]", x.get("text") or "", LIMITS["ns"])
    w("s06.question", s6.get("question") or "", LIMITS["question"])
    return probs


def _ns(x: Any) -> str:
    return la._norm(x)


# ── 덱 ───────────────────────────────────────────────────────────────────────────────────────

def db_lines(db: dict, polite: bool = False) -> list[str]:
    """DB 값으로만 만드는 문장(템플릿) — LLM 이 쓰지 않는다. polite=True 는 본문(합쇼체), 아니면 카드(짧은 평서)."""
    yrs = sorted(db["em_years"])
    if polite:
        return [f"참고로 ‘환경모니터링’ 태그로 묶인 경고서한은 {db['em_n']}건({yrs[0]}~{yrs[-1]}년), {db['em_firms']}개 업체입니다.",
                f"이 중 {db['em_483_before']}곳은 서한 전에 483이 먼저 게시됐고, {db['em_repeat']}곳은 서한을 두 번 이상 받았습니다(업체명 일치 기준이라 최소값입니다)."]
    return [f"태그 기준 집계: ‘환경모니터링’ 경고서한 {db['em_n']}건({yrs[0]}~{yrs[-1]}년), {db['em_firms']}개 업체.",
            f"이 중 {db['em_483_before']}곳은 서한 전에 483이 먼저 게시됐고, {db['em_repeat']}곳은 서한 2건 이상. 업체명 일치 기준이라 최소값."]


MAP_NOTE = "대응 조항 기준: PET 의약품 규정(21 CFR 212)을 같은 취지의 일반 cGMP(21 CFR 211) 조항으로 바꿔 센 값입니다."


METHOD = "찾은 방법: 경고서한 영문 문장을 코드로 검색(ISO 5·Grade A 검출 + 조사·평가 부족 / smoke study + dynamic / CAPA + effectiveness 부재)하고, 대응 조항 인용 여부로 닮은 정도를 나눴습니다."
KEYWORDS = ["ISO 5 · Grade A", "smoke study · dynamic", "CAPA effectiveness"]
TERM_IDS = ["cleanroom-classification", "viable-environmental-monitoring", "airflow-visualization-study", "capa"]
_NO_ID = "‘검출균 동정 미흡’ 그대로 지적한 서한은 DB에서 아직 찾지 못했다. 그래서 상황·조항이 겹치는 서한까지 넓혀 닮은 정도를 표시한다."


def _case_facets(c: dict) -> str:
    if c.get("fact"):
        return c["fact"]
    return ", ".join(c["facets"]) if c["facets"] else "환경모니터링 지적에 같은 조항이 인용됨(세부 사실은 다름)"


def case_lines(db: dict, polite: bool = False) -> list[str]:
    """개별 유사 사례 문장(템플릿) — 코드가 센 값·판정만 쓴다. 정확히 일치가 모자라면 단계적으로 넓히고 정도 라벨을 붙인다."""
    out = []
    if polite:
        for c in db["cases"]:
            out.append(f"{c['alias']}({c['year']}년, {c['cfr']}, {c['label']}): {_case_facets(c)} — grm-solutions.com/findings/doc/{c['slug']}/")
    if db["id_n"] == 0:
        out.append(_NO_ID.replace("찾지 못했다. 그래서", "찾지 못했습니다. 그래서").replace("표시한다.", "표시했습니다.") if polite else _NO_ID)
    return out


SITE_LEVELS = {"fact": "사실 일치", "situation": "상황 일치", "topic": "주제만 겹침"}
_SITE_RULES = {1: ("situation", re.compile(r"ISO ?5.*(?:오염|검출)|동정")),
               2: ("situation", re.compile(r"CAPA|시정 및 예방")),
               3: ("situation", re.compile(r"환경 ?모니터링|플레이트|곰팡이")),
               4: ("fact", re.compile(r"(?=.*(?:스모크|기류 시각화))(?=.*동적)"))}


def _pick_fact(text: str) -> str:
    """원문 지적에서 수치·기간이 든 문장을 먼저, 없으면 첫 문장을 그대로 쓴다(직접 쓰지 않는다). 너무 길면 자른다."""
    sents = [x for x in re.split(r"(?<=[.!?다])\s+(?=[가-힣A-Z0-9(])", re.sub(r"\s+", " ", text or "").strip()) if x]
    sents = [re.sub(r"\s*\[[^\]]*CFR[^\]]*\]", "", x) for x in sents]
    pick = next((x for x in sents if re.search(r"\d+\s*건|\d{4}년|\d+\s*%|\d+개월", x)), sents[0] if sents else "")
    pick = re.sub(r"^구체적으로,\s*", "", pick)
    pick = re.sub(r"^귀사는\s*", "", pick)
    return pick if len(pick) <= 104 else pick[:100].rstrip() + "…"


def _firm_history(findings: dict, slug: str) -> str:
    docs = findings.get("documents") or []
    key = next((d.get("firm_key") for d in docs if d.get("slug") == slug), None)
    mine = [d for d in docs if key and d.get("firm_key") == key]
    wl = sorted(str(d["published_date"]) for d in mine if _is_wl(d))
    b483 = sorted(str(d["published_date"]) for d in mine if d.get("source") == "FDA 483")
    line = f"이 업체 DB 이력: 483 {len(b483)}건 · 경고서한 {len(wl)}건"
    if wl and b483 and b483[0] < wl[0]:
        line += " (483이 서한보다 먼저 게시)"
    return line


def _rule(sim: dict, q: int) -> tuple[str, re.Pattern]:
    """지적 q 의 라벨 규칙: 슬롯(sim['rules'])이 있으면 그것, 없으면 이 서한용 기본값. 규칙이 없는 지적은 ‘주제만’."""
    r = (sim.get("rules") or {}).get(str(q))
    if r:
        try:
            return r[0], re.compile(r[1])
        except re.error:
            pass
    return _SITE_RULES.get(q, ("topic", re.compile("$^")))


def site_cases(sim: dict | None, findings: dict) -> list[dict]:
    """사이트 ‘유사 사례’ 엔진(findings_similar RPC)이 대상 서한의 지적마다 돌려준 결과를 그대로 쓴다 — 점수는 사이트 것이다.
    우리가 더하는 것은 닮은 정도 라벨(코드 규칙)·업체 익명 처리·그 업체의 DB 이력(센 값)이다.
    지적마다 (라벨, 같은 라벨이면 경고서한 먼저, 점수) 순으로 한 건을 고르고, 같은 업체는 한 번만 쓴다."""
    if not sim or not sim.get("items"):
        return []
    docs = findings.get("documents") or []
    # 지적사항 DB 사본에 없는 지적은 링크·업체 이력을 댈 수 없어 고르지 않는다(빈 링크·거짓 ‘0건’ 방지)
    by_fid = {f.get("finding_id"): (d.get("slug"), f.get("text_ko")) for d in docs for f in d.get("findings") or []}
    rank = {"fact": 0, "situation": 1, "topic": 2}
    used: set[str] = set()
    picked: list[tuple[int, str, dict]] = []
    for q in sorted({it["q"] for it in sim["items"]}):
        best = None
        for it in (x for x in sim["items"] if x["q"] == q and x["firm"] not in used and by_fid.get(x["finding_id"], ("",))[0]):
            lv, rx = _rule(sim, q)
            lv = lv if rx.search(it["text"]) else "topic"
            key = (rank[lv], "Warning" not in it["source"], -float(it["score"]))
            if best is None or key < best[0]:
                best = (key, lv, it)
        if best:
            used.add(best[2]["firm"])
            picked.append((q, best[1], best[2]))
    targets = sim.get("targets") or []
    rows = []
    for n, (q, lv, it) in enumerate(picked):
        slug, full = by_fid.get(it["finding_id"], ("", ""))
        rows.append(dict(q=q, target=targets[q - 1] if q <= len(targets) else "", alias=f"업체 {'ABCDEFG'[n]}", level=lv, label=SITE_LEVELS[lv],
                         year=str(it["date"])[:4], agency="경고서한" if "Warning" in it["source"] else "483", score=float(it["score"]),
                         text=it["text"], fact=_pick_fact(full or it["text"]), slug=str(slug or ""),
                         history=_firm_history(findings, str(slug or ""))))
    return rows


def _rpc(url: str, key: str, query: str, limit: int, timeout: int = 20, tries: int = 3) -> list[dict]:
    """공개 anon RPC 한 번. anon 의 statement_timeout(3초)에 간헐로 걸리므로(500·57014) 몇 번 다시 시도한다."""
    import time
    import urllib.request
    last: Exception | None = None
    for n in range(tries):
        req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/rpc/findings_similar", method="POST",
                                     data=json.dumps({"p_query": query[:500], "p_limit": limit}).encode(),
                                     headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 공개 anon RPC
                return (json.loads(r.read().decode("utf-8")) or {}).get("items") or []
        except Exception as exc:  # noqa: BLE001
            last = exc
            if n + 1 < tries:
                time.sleep(1.5)
    raise last  # type: ignore[misc]


def fetch_similar(slots: dict, *, url: str, key: str, rpc=_rpc) -> dict | None:
    """사이트 ‘유사 사례’ 엔진(findings_similar)을 서한의 지적마다 한 번 호출한다(limit 50 → 상위 5건 + 그중 경고서한 상위 3건).
    지적 하나가 끝내 실패해도 나머지로 간다. 전부 실패·결과 0건이면 None — 호출한 쪽이 이 카드만 빼고 만든다(없다고 쓰거나 지어내지 않는다)."""
    rows = (slots.get("s02") or {}).get("rows") or []
    items: list[dict] = []
    rules: dict[str, list[str]] = {}
    targets: list[str] = []
    failed = 0
    for q, r in enumerate(rows, 1):
        query = str(r.get("sim_query") or " ".join(la._ev_list(r.get("evidence")))).strip()
        targets.append(str(r.get("sim_short") or r.get("text") or "")[:24])
        lv = "fact" if r.get("sim_fact") else "situation" if r.get("sim_situation") else ""
        if lv:
            rules[str(q)] = [lv, str(r.get("sim_fact") or r.get("sim_situation"))]
        try:
            got = rpc(url, key, query, 50)
        except Exception as exc:  # noqa: BLE001 — 네트워크·권한·JSON 어떤 실패든 그 지적만 건너뛴다
            failed += 1
            print(f"::warning::유사 사례 조회 실패(지적 {q}, {type(exc).__name__})", file=sys.stderr)
            continue
        seen: set[str] = set()
        for x in [*got[:5], *[x for x in got if "Warning" in str(x.get("source"))][:3]]:
            if x.get("finding_id") in seen:
                continue
            seen.add(x["finding_id"])
            items.append(dict(q=q, finding_id=x["finding_id"], score=x.get("score") or 0, source=x.get("source") or "",
                              firm=x.get("firm_name") or "", date=str(x.get("published_date") or ""), text=x.get("text") or ""))
    if not items:
        print("::warning::유사 사례 결과 없음 — 05 카드를 뺀다", file=sys.stderr)
        return None
    return dict(source="findings_similar RPC", captured_on=_dt.date.today().isoformat(), card_id=str(slots.get("card_id") or ""),
                targets=targets, rules=rules, items=items, failed_findings=failed)


def site_lines(rows: list[dict]) -> list[str]:
    return [f"{r['alias']}({r['year']}년 {r['agency']}, {r['label']}) ‘{r['target']}’ ← {r['fact']} grm-solutions.com/findings/doc/{r['slug']}/"
            for r in rows if r["slug"]]


def _load_glossary() -> list[dict]:
    try:
        return json.loads((WEB_DIR / "data" / "glossary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def glossary_terms(glossary: list[dict] | None) -> list[tuple[str, str]]:
    by = {g.get("id"): g.get("term_ko") for g in glossary or []}
    return [(by[i], i) for i in TERM_IDS if by.get(i)]


def build_grm_deck(brief_doc: dict, slots: dict, findings: dict, glossary: list[dict] | None = None,
                   *, base_url: str = lc.SITE_BASE_URL, similar: dict | None = None) -> dict[str, Any]:
    card = la.pick_anatomy_card(brief_doc, str(slots.get("card_id") or ""))
    if card is None:
        raise la.AnatomyError(["card_id 에 맞는 해부 가능한 경고서한이 브리프에 없다"])
    probs = validate_v3(card, slots, findings)
    if probs:
        raise la.AnatomyError(probs)
    db = db_similar(findings, card)
    brief = brief_doc.get("brief") or {}
    pub = str(brief.get("publish_date") or slots.get("pub") or "")
    y, m, wk = lc.title_dateform(pub)
    if similar is None:
        try:
            similar = json.loads((SLOTS_DIR / f"{pub}.similar.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            similar = None
    site = site_cases(similar, findings)
    db["site"] = site
    issued = la._issue_date(card)
    cv, s2, s3, s4, s5, s6 = (slots[k] for k in ("cover", "s02", "s03", "s04", "s05", "s06"))
    items = (card["deep_analysis"].get("required_remediation") or {}).get("items") or []
    src_note = f"원문: FDA 경고서한{f' ({issued})' if issued else ''} · 업체명 비공개"
    ai = lc.AI_NOTE
    terms = glossary_terms(glossary if glossary is not None else _load_glossary())
    slides = [
        dict(kind="g_cover", chip=cv["chip"], h1=list(cv["headline"]), sub=cv["sub"], dots=dict(cv["dots"]),
             line=cv.get("line") or "", ft_right=src_note, ai=ai),
        dict(kind="g_find", corner="지적 내용", heading=s2["heading"], rows=s2["rows"], ft_right=src_note, ai=ai),
        dict(kind="g_reply", corner="회신 평가", heading=s3["heading"], sub=s3["sub"], pairs=s3["pairs"],
             repeat=s3["repeat"]["text"], ft_right=src_note, ai=ai),
        dict(kind="g_req", corner="시정 요구", heading=s4["heading"], sub=f"서한이 요구한 항목 {len(items)}건",
             items=[(it["title"], it["text"]) for it in s4["items"]], deadline=la._deadline(card), ft_right=src_note, ai=ai),
        *([dict(kind="g_site", corner="유사 사례", heading=s5["heading"], rows=site, ft_right="사이트 유사 사례 검색 결과 · 정도 라벨은 코드 규칙", ai=ai)] if site else []),
        dict(kind="g_sim", corner="DB 연관" if site else "유사 사례", heading="DB로 본 반복과 선행" if site else s5["heading"], db=db,
             lines=(db_lines(db) if site else case_lines(db) + db_lines(db)), comment=s5["comment"], terms=terms,
             ft_right=f"지적사항 DB {db['measured_on']} 기준 · 직접 센 숫자", ai=ai),
        dict(kind="g_end", corner="서한에 없는 것", heading=s6["heading"], not_stated=s6["not_stated"],
             question=s6["question"], ft_right="원문 링크는 브리프 페이지에서 확인", ai=ai),
    ]
    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)
    url = f"{base_url}/briefs/{pub}/"
    cap = slots["caption"]
    caption_url = lc.with_utm(url, *lc.LINKEDIN_WEEKLY, lc.linkedin_weekly_campaign(pub))
    term_line = ("관련 용어: " + " · ".join(n for n, _ in terms) + " (grm-solutions.com/glossary/)") if terms else ""
    sim = "\n".join(x for x in [la._norm(cap["similar"]), *(site_lines(site) if site else case_lines(db, polite=True)), METHOD, *db_lines(db, polite=True),
                               MAP_NOTE, term_line] if x)
    ko = "\n\n".join([la._norm(cap["lead"]), la._norm(cap["claims"]), la._norm(cap["contrast"]),
                      la._norm(cap["requests"]), sim, la._norm(cap["not_stated"])])
    # 캡션 본문 속 글자 주소(`grm-solutions.com/...`)에도 같은 표식 — 링크드인이 링크로 걸어 주지만 표식이 없어 '직접'에 섞였다.
    ko = utm.tag_bare_site_urls(ko, "linkedin_weekly", date=pub)
    return {"pub": pub, "lang": "ko", "slides": slides, "caption": ko + f"\n\n전체 해설: {caption_url}\n",
            "doc_title": f"GRM {m}월 {wk}주 경고서한 분석(GRM 판)", "url": url, "caption_url": caption_url,
            "card_id": str(card.get("id")), "mode": "grm"}


# ── 렌더 ─────────────────────────────────────────────────────────────────────────────────────

CSS = """
.g-page .main{justify-content:flex-start;padding-top:40px}
.g-key{display:flex;gap:12px;align-items:center;margin-bottom:22px;font-size:19px;font-weight:700}
.g-key span{padding:5px 14px;border-radius:7px}
.g-key .o{background:#1A1815;color:#FAF9F5}.g-key .h{background:#EFE9DE;color:#3D3D3A;border:1.5px solid #D9CFC0}.g-key .n{background:#FBF1DD;color:#5A4210;border:1.5px solid #E9C98C}
.g-corner{align-self:flex-start;display:inline-block;padding:8px 22px;border-radius:9999px;background:#C2603F;color:#fff;font-size:26px;font-weight:800;margin-bottom:18px}
.g-h{font-size:42px;font-weight:800;line-height:1.28;letter-spacing:-.02em;word-break:keep-all;margin:0 0 20px}
.g-sub{margin:0 0 20px;font-size:25px;font-weight:700;color:#A14B30;word-break:keep-all}
.g-cover h1{font-size:92px;margin:6px 0 0}
.g-chip{align-self:flex-start;padding:9px 24px;border-radius:9999px;border:2.5px solid #1A1815;font-size:24px;font-weight:700;margin-bottom:36px}
.g-cs{margin:28px 0 0;font-size:30px;color:#3D3D3A}
.g-dots{margin-top:52px}
.g-grid{display:grid;grid-template-columns:repeat(15,1fr);gap:14px;max-width:940px}
.g-grid i{display:block;aspect-ratio:1;border-radius:50%;border:3px solid #B8AFA0}
.g-grid i.m{background:#C2603F;border-color:#C2603F;box-shadow:0 0 0 7px rgba(194,96,63,.22)}
.g-dcap{margin-top:30px;font-size:31px;font-weight:700;line-height:1.4;word-break:keep-all}
.g-line{margin-top:16px;font-size:25px;color:#6C6A64;word-break:keep-all}
.g-fr{border-top:3px solid #141413}
.g-f{padding:20px 4px 18px;border-bottom:1.5px solid #D9CFC0}
.g-f .c{display:inline-block;font-size:20px;font-weight:800;color:#fff;background:#1A1815;border-radius:6px;padding:3px 12px;margin-bottom:8px}
.g-f .t{font-size:29px;font-weight:800;line-height:1.35;word-break:keep-all}
.g-f .e{margin-top:8px;font-size:19px;line-height:1.5;color:#6C6A64;font-style:italic;word-break:keep-all;border-left:4px solid #1A1815;padding-left:14px}
.g-rp{display:flex;flex-direction:column;gap:12px}
.g-p{display:grid;grid-template-columns:.8fr 1.7fr;border-radius:10px;overflow:hidden}
.g-p div{padding:16px 20px;font-size:22px;line-height:1.45;word-break:keep-all}
.g-p .r{background:#EFE9DE}.g-p .j{background:#1A1815;color:#FAF9F5}.g-p small{display:block;font-size:16px;font-weight:800;opacity:.75;margin-bottom:4px}
.g-rep{margin-top:16px;padding:16px 22px;border-left:8px solid #C2603F;background:#EFE9DE;font-size:24px;font-weight:700;line-height:1.45;word-break:keep-all}
.g-req{border-top:3px solid #141413}
.g-r{display:grid;grid-template-columns:100px 250px 1fr;gap:18px;padding:34px 4px;border-bottom:1.5px solid #D9CFC0;align-items:baseline}
.g-r em{font-style:normal;font-weight:800;color:#C2603F;font-size:23px}.g-r b{font-size:30px;word-break:keep-all}.g-r span{font-size:26px;line-height:1.5;color:#3D3D3A;word-break:keep-all}
.g-dl{margin-top:26px;font-size:26px;font-weight:800;color:#A14B30}
.g-yr{display:flex;align-items:flex-end;gap:14px;height:150px;margin:2px 0 8px}
.g-yr div{flex:1;text-align:center;font-size:20px;color:#6C6A64}.g-yr i{display:block;background:#C2603F;border-radius:5px 5px 0 0;margin-bottom:6px}.g-yr b{display:block;font-size:22px;color:#141413}
.g-ln{font-size:23px;line-height:1.45;margin:6px 0;word-break:keep-all;padding-left:14px;border-left:6px solid #C2603F}
.g-map{margin:14px 0 4px;font-size:21px;border-top:2px solid #141413}
.g-map div{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:10px;padding:3px 2px;border-bottom:1px solid #D9CFC0}
.g-map.big{font-size:27px;margin-top:26px}.g-map.big div{padding:12px 2px}.g-map.big small{font-size:20px}.g-map small{display:block;font-size:17px;color:#6C6A64}
.g-cs3{margin-top:12px;display:flex;flex-direction:column;gap:8px}
.g-st{margin:2px 0 8px;font-size:22px;font-weight:800;color:#6C6A64}
.g-map .mh{font-size:17px;font-weight:800;color:#6C6A64;border-bottom:2px solid #141413}
.g-cs3 em.lv{display:inline-block;font-style:normal;font-size:16px;font-weight:800;border-radius:5px;padding:1px 9px;margin:0 8px 3px 0;color:#fff;background:#6C6A64}
.g-cs3 em.both{background:#C2603F}.g-cs3 em.pattern{background:#1A1815}.g-cs3 em.fact{background:#C2603F}.g-cs3 em.situation{background:#1A1815}.g-cs3 small.t{color:#A14B30;font-weight:700;margin-bottom:2px}.g-site div{padding:16px 18px;font-size:25px;line-height:1.5}.g-site small.h{color:#3D3D3A;font-size:18px;margin-top:4px}.g-site{gap:12px}
.g-chips{margin:8px 0 2px;display:flex;flex-wrap:wrap;gap:8px;font-size:17px}
.g-chips i{font-style:normal;border:1.5px solid #8A8478;border-radius:99px;padding:2px 12px;color:#3D3D3A}
.g-chips u{text-decoration:none;background:#EFE9DE;border-radius:99px;padding:2px 12px;font-weight:700}
.g-cs3 small.u{font-size:15px;color:#8A8478;margin-top:2px}
.g-cs3 div{display:grid;grid-template-columns:150px 1fr;gap:12px;padding:10px 14px;border:1.5px dashed #8A8478;border-radius:8px;font-size:19px;line-height:1.45;word-break:keep-all}
.g-cs3 b{font-size:19px}.g-cs3 small{display:block;font-size:16px;color:#6C6A64}
.g-cm{margin-top:10px;font-size:22px;font-weight:700;color:#A14B30;word-break:keep-all}
.g-ns{background:#FBF1DD;border:2px solid #E9C98C;border-radius:12px;padding:28px 30px;margin-bottom:22px;font-size:31px;line-height:1.5;word-break:keep-all;color:#3F2F0B}
.g-ns small{display:block;font-size:21px;font-weight:700;margin-top:8px;color:#7A6120}
.g-qq{margin-top:22px;border:3px solid #1A1815;border-radius:14px;padding:26px 30px;font-size:34px;font-weight:800;line-height:1.4;word-break:keep-all}
.g-qq small{display:block;font-size:19px;font-weight:700;margin-bottom:8px;color:#6C6A64}
"""


def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


_KEY_SPAN = {"o": "서한 원문", "h": "GRM 해설", "n": "서한에 없음"}
_KEY_OF = {"g_find": "oh", "g_reply": "oh", "g_req": "oh", "g_site": "h", "g_sim": "h", "g_end": "nh"}  # 그 카드에 실제로 있는 종류만 표시


def _key(kind: str) -> str:
    return '<div class="g-key">' + "".join(f'<span class="{c}">{_KEY_SPAN[c]}</span>' for c in _KEY_OF.get(kind, "oh")) + "</div>"


def _slide_html(s: dict) -> str:
    k = s["kind"]
    p: list[str] = []
    if k == "g_cover":
        d = s["dots"]
        grid = "".join(f'<i class="{"m" if n < d["marked"] else ""}"></i>' for n in range(d["total"]))
        p.append(f'<div class="g-chip">{_e(s["chip"])}</div>')
        p.append(lc._h1(s["h1"], 92))
        p.append(f'<p class="g-cs">{_e(s["sub"])}</p>')
        p.append(f'<div class="g-dots"><div class="g-grid">{grid}</div><div class="g-dcap">{_e(d["caption"])}</div>'
                 + (f'<div class="g-line">{_e(s["line"])}</div>' if s.get("line") else "") + '</div>')
    else:
        p.append(_key(k))
        p.append(f'<span class="g-corner">{_e(s["corner"])}</span>')
        p.append(f'<h2 class="g-h">{_e(s["heading"])}</h2>')
        if k == "g_find":
            p.append('<div class="g-fr">' + "".join(
                f'<div class="g-f"><span class="c">{_e(r["cite"])}</span><div class="t">{_e(r["text"])}</div>'
                f'<div class="e">{_e(la._ev_list(r["evidence"])[0])}</div></div>' for r in s["rows"]) + "</div>")
        elif k == "g_reply":
            p.append(f'<div class="g-sub">{_e(s["sub"])}</div>')
            p.append('<div class="g-rp">' + "".join(
                f'<div class="g-p"><div class="r"><small>회사 회신</small>{_e(x["response"])}</div>'
                f'<div class="j"><small>FDA: 부적절한 이유</small>{_e(x["why"])}</div></div>' for x in s["pairs"]) + "</div>")
            p.append(f'<div class="g-rep">반복 위반 · {_e(s["repeat"])}</div>')
        elif k == "g_req":
            p.append('<div class="g-req">' + "".join(
                f'<div class="g-r"><em>요구 {n}/{len(s["items"])}</em><b>{_e(t)}</b><span>{_e(x)}</span></div>'
                for n, (t, x) in enumerate(s["items"], 1)) + "</div>")
            if s.get("deadline"):
                p.append(f'<div class="g-dl">회신 기한: {_e(s["deadline"])}</div>')
        elif k == "g_site":
            p.append('<div class="g-cs3 g-site">' + "".join(
                f'<div><b>{_e(r["alias"])}<small>{_e(r["year"])} · FDA {_e(r["agency"])}</small></b>'
                f'<span><small class="t">이번 서한의 지적: {_e(r["target"])}</small><em class="lv {r["level"]}">{_e(r["label"])}</em>{_e(r["fact"])}'
                f'<small class="h">{_e(r["history"])}</small>'
                f'<small class="u">grm-solutions.com/findings/doc/{_e(r["slug"])}/</small></span></div>' for r in s["rows"]) + "</div>")
        elif k == "g_sim":
            db = s["db"]
            if not db.get("site"):
                p.append('<div class="g-cs3">' + "".join(
                f'<div><b>{_e(c["alias"])}<small>{_e(c["year"])} · {_e(c["agency"])}<br>{_e(c["cfr"])}</small></b>'
                f'<span><em class="lv {c["level"]}">{_e(c["label"])}</em>{_e(_case_facets(c))}'
                f'<small class="u">grm-solutions.com/findings/doc/{_e(c["slug"])}/</small></span></div>'
                for c in db["cases"]) + "</div>")
            big = bool(db.get("site"))  # DB 연관 카드는 내용이 적어 글을 키운다(억지로 채우지 않고 읽기 편하게)
            ln_style = ' style="font-size:29px;margin:14px 0"' if big else ""
            p.extend(f'<div class="g-ln"{ln_style}>{_e(x)}</div>' for x in s["lines"])
            p.append('<div class="g-chips">' + "".join(f'<i>{_e(k_)}</i>' for k_ in KEYWORDS)
                     + "".join(f'<u>{_e(n)}</u>' for n, _ in s.get("terms") or []) + '</div>')
            p.append(f'<div class="g-map{" big" if big else ""}"><div class="mh"><span>대응 조항 기준 (212 → 211)</span><span>경고서한</span><span>2025년 이후</span></div>' + "".join(
                f'<div><span>{_e(r["label"])}<small>{_e(r["cfr"])}</small></span><span>{r["n"]}건</span><span>{r["recent"]}건</span></div>'
                for r in db["cfr_rows"]) + '</div>')
            p.append(f'<div class="g-cm">{_e(s["comment"])}</div>')
        elif k == "g_end":
            for x in s["not_stated"]:
                p.append(f'<div class="g-ns">{_e(x["text"])}<small>근거: {_e(x["basis"])}</small></div>')
            p.append(f'<div class="g-qq"><small>우리 현장에 던질 질문</small>{_e(s["question"])}</div>')
    ai = f'<div class="ai">{_e(s["ai"])}</div>' if s.get("ai") else ""
    cls = " g-cover g-page" if k == "g_cover" else " g-page"
    return (f'<section class="slide{cls}"><div class="hd"><div class="brand">{lc.OWL_SVG}<span class="w"><b>GRM</b>'
            f'<span>Global Regulatory Monitor</span></span></div><div class="pg">{s["idx"]:02d} / {s["total"]:02d}</div></div>'
            f'<div class="main">{"".join(p)}</div>'
            f'<div class="ft"><span>grm-solutions.com</span><span>{_e(s.get("ft_right", ""))}</span></div>{ai}</section>')


def render_html(deck: dict, *, font_links: bool = True) -> str:
    css = (lc.CSS + CSS).replace("HEX_DARK", lc._data_uri(lc._HEX.format(op="0.07"))) \
        .replace("HEX_CREAM", lc._data_uri(lc._HEX.format(op="0.16")))
    body = "".join(_slide_html(s) for s in deck["slides"])
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{_e(deck["doc_title"])}</title>'
            f'{lc.FONT_LINKS if font_links else ""}<style>{css}</style></head><body class="lang-ko">{body}</body></html>')


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="링크드인 사건 해부 GRM 판(시범 v3)")
    ap.add_argument("--brief", help="브리프 JSON(미지정 시 --data 의 최신)")
    ap.add_argument("--data", default=str(WEB_DIR / "data" / "briefs"), help="--brief 가 없을 때 최신 브리프를 찾을 디렉터리")
    ap.add_argument("--slots", help="v3 슬롯 JSON(기본: web/data/linkedin_anatomy/<발행일>.v3.json)")
    ap.add_argument("--out", help="출력 폴더")
    ap.add_argument("--dist", help="배포 루트 — briefs/<발행일>/ 아래에 낸다(linkedin.* 옆, 사이트맵 비등록)")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args(argv)
    if not (args.out or args.dist):
        ap.error("--out 또는 --dist 가 필요하다")
    bp = Path(args.brief) if args.brief else lc.latest_brief_path(Path(args.data))
    if bp is None:
        print("::warning::브리프 JSON 없음 — 만들지 않는다", file=sys.stderr)
        return 0
    doc = json.loads(bp.read_text(encoding="utf-8"))
    pub = str((doc.get("brief") or {}).get("publish_date") or "")
    sp = Path(args.slots) if args.slots else SLOTS_DIR / f"{pub}.v3.json"
    if not sp.exists():
        print(f"::warning::v3 슬롯 없음({sp.name}) — 만들지 않는다", file=sys.stderr)
        return 0
    slots = json.loads(sp.read_text(encoding="utf-8"))
    findings = json.loads(FINDINGS.read_text(encoding="utf-8"))
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_ANON_KEY", "").strip()
    similar = None  # 키가 없으면(로컬·테스트) 저장된 <발행일>.similar.json 이 있을 때만 쓴다
    if url and key:
        similar = fetch_similar(slots, url=url, key=key) or {}
    try:
        deck = build_grm_deck(doc, slots, findings, similar=similar)
    except la.AnatomyError as exc:
        print("::warning::GRM 판 덱 검증 실패 — 만들지 않는다", file=sys.stderr)
        for pr in exc.problems:
            print(f"  - {pr}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else Path(args.dist) / "briefs" / pub
    out.mkdir(parents=True, exist_ok=True)
    (out / "linkedin_grm.txt").write_text(deck["caption"], encoding="utf-8")
    if similar:
        (out / "similar.json").write_text(json.dumps(similar, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "linkedin_grm.html").write_text(render_html(deck), encoding="utf-8")
    chrome = None if args.no_pdf else lc.find_chrome()
    n = 0
    if chrome:
        for i, slide in enumerate(deck["slides"], 1):
            tmp = out / f"linkedin_grm_{i:02d}.tmp.html"
            tmp.write_text(render_html(dict(deck, slides=[slide])), encoding="utf-8")
            try:
                lc.render_png(tmp, out / f"linkedin_grm_{i:02d}.png", chrome)
                n += 1
            finally:
                tmp.unlink(missing_ok=True)
    print(f"linkedin_grm: {pub} · {len(deck['slides'])}장 · 본문 {len(deck['caption'])}자 · PNG {n} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
