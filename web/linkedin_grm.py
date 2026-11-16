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
          "db_comment": 70, "ns": 74, "question": 40, "quote_ko": 70, "quote_en": 170,
          # 본문 첫 문장 — 링크드인 모바일 피드는 약 22자 두 줄에서 '…더 보기'로 접는다. 그 안에 핵심이 끝나야 한다.
          "lead_first": 44}

# 후킹 규칙(STYLE.md §13) — 피드에서 멈추게 하는 두 자리: 표지 헤드라인과 본문 첫 문장.
_LABEL_END = re.compile(r"(?:미흡|부족|결함|위반|실패|거부|문제|미비|부재|소홀|논란|우려)[.!]?$")
_WEAK_START = re.compile(r"^(?:FDA|MHRA|EMA|식약처|한\s|이번\s|지난\s|최근\s)")


_EM_TOPIC = re.compile(r"ISO 5|ISO class 5|Grade A|Class 100|environmental monitor|viable|microbial (?:recover|monitor)|isolates?", re.I)


def load_findings(path: Path = FINDINGS) -> dict:
    """지적사항 DB 사본을 읽는다 — .gz 면 풀어서(테스트의 동결 스냅숏용)."""
    if path.suffix == ".gz":
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text(encoding="utf-8"))


def _is_wl(d: dict) -> bool:
    return d.get("source") == "FDA Warning Letter"


# ── 코드가 세는 사실: 지적사항 DB ───────────────────────────────────────────────────────────────

def _firm_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[\s,.()&]+", str(name or ""))
            if len(t) >= 4 and t.lower() not in ("inc", "llc", "ltd", "corp", "company", "co", "pharma", "pharmaceutical")]


def db_similar(findings: dict, card: dict, *, n_cases: int = 3, topic: str = "환경모니터링",
               cfr_map: list | None = None) -> dict[str, Any]:
    """같은 주제(지적사항 DB 분류 `topic`)·대응 조항으로 지적된 과거 사례. 전부 센 값 — 문장을 만들지 않는다.
    주제·조항 표는 서한마다 다르므로 슬롯 `s05.topic`·`s05.cfr_rows` 가 정한다(기본값은 11-02 시범 서한의 것)."""
    cfr_map = CFR_MAP if cfr_map is None else [tuple(x) for x in cfr_map]
    docs = findings.get("documents") or []
    tgt = (lc.firm_name(card) or "").lower()
    by_firm: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_firm[d.get("firm_key")].append(d)
    wls = [d for d in docs if _is_wl(d)]
    em = [d for d in wls if topic in (d.get("categories") or []) and not (tgt and tgt in str(d.get("firm_name")).lower())]
    years = Counter(str(d["published_date"])[:4] for d in em)
    firms = {d["firm_key"] for d in em}
    b483_before = 0
    for k in firms:
        wl_first = min(x["published_date"] for x in by_firm[k] if _is_wl(x))
        if any(x.get("source") == "FDA 483" and x["published_date"] < wl_first for x in by_firm[k]):
            b483_before += 1
    repeat = sum(1 for k in firms if sum(1 for x in by_firm[k] if _is_wl(x)) > 1)
    cfr_rows = []
    for label, prefix in cfr_map:
        hit = [d for d in wls if any(r.startswith(prefix) for f in d.get("findings") or [] for r in f.get("cfr_refs") or [])]
        cfr_rows.append(dict(label=label, cfr=prefix, n=len(hit), recent=sum(1 for d in hit if str(d["published_date"]) >= "2025-01-01")))
    pet = [d for d in wls if any("21 CFR 212" in r for f in d.get("findings") or [] for r in f.get("cfr_refs") or [])
           and not (tgt and tgt in str(d.get("firm_name")).lower())]
    # 문장 패턴(FACETS)은 11-02 환경모니터링 서한용이다 — 다른 주제에 돌리면 엉뚱한 '닮은 사례'가 나온다.
    cases, tiers, id_n = similar_cases(wls, tgt, n_cases) if topic == "환경모니터링" else ([], dict(both=0, pattern=0, clause=0), 0)
    return dict(topic=topic, measured_on=findings.get("measured_on") or "", wl_total=len(wls), em_n=len(em), em_years=dict(sorted(years.items())),
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


def s05_ctx(slots: dict) -> dict[str, Any]:
    """유사 사례 칸의 서한별 맥락 — 주제 분류·조항 표·키워드·용어·설명 문장. 슬롯 `s05` 가 정한다.
    없는 항목은 그 요소를 그리지 않는다(11-02 시범 서한의 PET·환경모니터링 문구를 다른 서한에 붙이지 않는다)."""
    s5 = slots.get("s05") or {}
    return dict(topic=str(s5.get("topic") or ""), cfr_rows=[tuple(x) for x in s5.get("cfr_rows") or []],
                map_header=str(s5.get("map_header") or "인용 조항"), map_note=str(s5.get("map_note") or ""),
                keywords=[str(x) for x in s5.get("keywords") or []], term_ids=[str(x) for x in s5.get("term_ids") or []],
                method=str(s5.get("method") or ""), no_match_note=str(s5.get("no_match_note") or ""))


def db_for(findings: dict, card: dict, slots: dict) -> dict[str, Any]:
    ctx = s05_ctx(slots)
    return db_similar(findings, card, topic=ctx["topic"] or "환경모니터링", cfr_map=ctx["cfr_rows"])


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
    db = db_for(findings, card, slots)
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
              ("cover.line", cv.get("line") or ""), ("s03.repeat.label", (s3.get("repeat") or {}).get("label") or "")]
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

    # 표지 그림은 둘 중 하나: 점 그림(서한에 'N건 중 M건' 같은 분모가 있을 때) 또는 원문 한 줄(분모가 없을 때).
    # 분모 없는 서한에 점 그림을 억지로 만들지 않는다(2026-11-16 — 3건 중 3건 같은 그림은 아무것도 보여 주지 않는다).
    dots, quote = cv.get("dots"), cv.get("quote")
    if bool(dots) == bool(quote):
        probs.append("cover: dots(분모가 있는 숫자) 또는 quote(서한 원문 한 줄) 중 정확히 하나")
    if dots:
        _ev_ok("cover.dots", dots.get("evidence"))
        if not {str(dots.get("total")), str(dots.get("marked"))} <= la._nums(" ".join(la._ev_list(dots.get("evidence")))):
            probs.append("cover.dots: total·marked 가 근거 안의 숫자여야 한다")
        if not (isinstance(dots.get("total"), int) and 1 <= dots["total"] <= 100 and 0 <= dots.get("marked", -1) <= dots["total"]):
            probs.append("cover.dots: total 1~100, marked ≤ total")
    if quote:
        for k in ("en", "ko"):
            q = la._norm(quote.get(k))
            if not q:
                probs.append(f"cover.quote.{k} 비어 있음")
            elif q not in src:
                probs.append(f"cover.quote.{k}: 서한 원문에 그대로 없다 — {q[:40]}…")
        if lc.text_width(la._norm(quote.get("ko"))) > LIMITS["quote_ko"]:
            probs.append(f"cover.quote.ko: 너무 길다(> {LIMITS['quote_ko']:g})")
        if len(la._norm(quote.get("en"))) > LIMITS["quote_en"]:
            probs.append(f"cover.quote.en: 너무 길다(> {LIMITS['quote_en']}자)")
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
    if s3.get("repeat"):  # 반복 위반·후속 조치 한 줄 — 서한에 없으면 칸을 만들지 않는다
        _ev_ok("s03.repeat", s3["repeat"].get("evidence"), s3["repeat"].get("text") or "")

    refs = sorted(it.get("ref") for it in s4.get("items") or [] if isinstance(it.get("ref"), int))
    if refs != list(range(min(len(items), 4))):
        probs.append(f"s04.items 가 서한의 FDA 요구 {min(len(items), 4)}개를 모두 다뤄야 한다(ref {refs})")
    if str(slots.get("pub") or "") > "2026-11-02" and not _ns(s5.get("topic")):
        probs.append("s05.topic: 지적사항 DB 분류 이름이 필요하다(예: 무균보증/무균공정)")
    if not db["em_n"]:
        probs.append(f"s05: DB 에서 같은 주제('{db['topic']}') 사례를 찾지 못했다")
    for t in s05_ctx(slots)["term_ids"]:
        if t not in {g.get("id") for g in _load_glossary()}:
            probs.append(f"s05.term_ids: 용어사전에 없는 id '{t}'")

    ns = s6.get("not_stated") or []
    if len(ns) not in (2, 3) or any(not x.get("basis") for x in ns):
        probs.append("s06.not_stated 는 2~3개이고 각각 basis(무엇을 보고 없다고 했는지)가 있어야 한다")
    if not la._norm(s6.get("question")).endswith("?"):
        probs.append("s06.question 은 질문형(?)이어야 한다")

    probs += hook_problems(card, slots)

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


def first_sentence(text: Any) -> str:
    t = la._norm(text)
    m = re.search(r"(?<=[다요죠])\.(?:\s|$)|[?!](?:\s|$)", t)
    return t[: m.end()].strip() if m else t


def hook_problems(card: dict, slots: dict) -> list[str]:
    """피드에서 멈추게 하는지 — 코드가 볼 수 있는 것만 본다(STYLE.md §13).
    ① 본문 첫 문장은 모바일 접힘(약 22자×2줄) 안에 끝난다 ② 첫 문장은 사실로 시작한다 — 'FDA는 한 제조소가…' 같은
    주어·기관 머리는 접힘 위의 자리를 다 쓴다 ③ 첫 문장에 숫자나 원문 인용(‘…’) 한 조각이 있다 ④ 표지 헤드라인은 두 줄이고
    제목(라벨)을 되풀이하지 않으며 '…미흡'·'…위반' 같은 라벨로 끝나지 않는다."""
    out: list[str] = []
    lead1 = first_sentence((slots.get("caption") or {}).get("lead"))
    alias = _ns(slots.get("firm_alias"))
    if lc.text_width(lead1) > LIMITS["lead_first"]:
        out.append(f"caption.lead 첫 문장이 모바일 접힘(약 22자×2줄)을 넘는다({lc.text_width(lead1):.0f} > {LIMITS['lead_first']}): {lead1[:30]}…")
    if _WEAK_START.search(lead1) or (alias and alias in lead1):
        out.append("caption.lead 첫 문장은 사실로 시작한다 — 기관·업체 소개('FDA는 …', '한 제조소가 …')는 둘째 문장으로")
    if not (re.search(r"\d", lead1) or re.search(r"[‘“'\"][^’”'\"]{2,}[’”'\"]", lead1)):
        out.append("caption.lead 첫 문장에 숫자나 원문 인용(‘…’) 한 조각이 있어야 한다")
    h = [_ns(x) for x in (slots.get("cover") or {}).get("headline") or []]
    if len(h) != 2:
        out.append("cover.headline 은 두 줄")
    elif _LABEL_END.search(h[-1]):
        out.append(f"cover.headline 이 라벨로 끝난다('{h[-1]}') — 무엇이 어떻게 됐는지를 쓴다")
    if h and re.sub(r"[\s·,]", "", "".join(h)) == re.sub(r"[\s·,]", "", _ns(card.get("title_issue"))):
        out.append("cover.headline 이 카드 제목을 되풀이한다")
    return out


# ── 덱 ───────────────────────────────────────────────────────────────────────────────────────

def db_lines(db: dict, polite: bool = False) -> list[str]:
    """DB 값으로만 만드는 문장(템플릿) — LLM 이 쓰지 않는다. polite=True 는 본문(합쇼체), 아니면 카드(짧은 평서)."""
    yrs = sorted(db["em_years"])
    tp = db.get("topic") or "환경모니터링"
    if polite:
        return [f"참고로 ‘{tp}’ 태그로 묶인 경고서한은 {db['em_n']}건({yrs[0]}~{yrs[-1]}년), {db['em_firms']}개 업체입니다.",
                f"이 중 {db['em_483_before']}곳은 서한 전에 483이 먼저 게시됐고, {db['em_repeat']}곳은 서한을 두 번 이상 받았습니다(업체명 일치 기준이라 최소값입니다)."]
    return [f"태그 기준 집계: ‘{tp}’ 경고서한 {db['em_n']}건({yrs[0]}~{yrs[-1]}년), {db['em_firms']}개 업체.",
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


def case_lines(db: dict, polite: bool = False, no_match_note: str = _NO_ID) -> list[str]:
    """개별 유사 사례 문장(템플릿) — 코드가 센 값·판정만 쓴다. 정확히 일치가 모자라면 단계적으로 넓히고 정도 라벨을 붙인다."""
    out = []
    if polite:
        for c in db["cases"]:
            out.append(f"{c['alias']}({c['year']}년, {c['cfr']}, {c['label']}): {_case_facets(c)} — grm-solutions.com/findings/doc/{c['slug']}/")
    if db["id_n"] == 0 and db["cases"] and no_match_note:
        out.append(no_match_note.replace("찾지 못했다. 그래서", "찾지 못했습니다. 그래서").replace("표시한다.", "표시했습니다.") if polite else _NO_ID)
    return out


SITE_LEVELS = {"fact": "사실 일치", "situation": "상황 일치", "topic": "주제만 겹침"}
_SITE_RULES = {1: ("situation", re.compile(r"ISO ?5.*(?:오염|검출)|동정")),
               2: ("situation", re.compile(r"CAPA|시정 및 예방")),
               3: ("situation", re.compile(r"환경 ?모니터링|플레이트|곰팡이")),
               4: ("fact", re.compile(r"(?=.*(?:스모크|기류 시각화))(?=.*동적)"))}


def _pick_fact(text: str) -> str:
    """원문 지적에서 수치·기간이 든 문장을 먼저, 없으면 첫 문장을 그대로 쓴다(직접 쓰지 않는다). 너무 길면 자른다."""
    # 문장 끝은 마침표·물음표로만 본다 — '다' 뒤 공백으로 자르면 '…ISO-5 품질보다 낮은 공기'가 '품질보다'에서 잘렸다(2026-11-16).
    sents = [x for x in re.split(r"(?<=[.!?])\s+(?=[가-힣A-Z0-9(])", re.sub(r"\s+", " ", text or "").strip()) if x]
    sents = [re.sub(r"\s*\[[^\]]*CFR[^\]]*\]", "", x) for x in sents]
    pick = next((x for x in sents if re.search(r"\d+\s*건|\d{4}년|\d+\s*%|\d+개월", x)), sents[0] if sents else "")
    pick = re.sub(r"^구체적으로,\s*", "", pick)
    pick = re.sub(r"^귀사는\s*", "", pick)
    if len(pick) <= 104:
        return pick
    cut = pick[:100].rsplit(" ", 1)[0]  # 낱말 중간에서 자르지 않는다
    return re.sub(r"\s*\([^)]*$", "", cut).rstrip(" ,") + "…"  # 닫히지 않은 괄호(영문 병기 등)는 통째로 뺀다


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
        rows.append(dict(q=q, finding_id=it["finding_id"], target=targets[q - 1] if q <= len(targets) else "", alias=f"업체 {'ABCDEFG'[n]}", level=lv, label=SITE_LEVELS[lv],
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


def load_similar(slots: dict, pub: str, *, url: str, key: str, fetch=fetch_similar,
                 slots_dir: Path | None = None, snap: Path | None = None) -> dict | None:
    """유사 사례 묶음을 고른다 — **저장된 <발행일>.similar.json 이 있으면 그것을 쓰고 RPC 를 부르지 않는다.**

    ★2026-11-09 실측: 이 스텝은 web/** push·PR 마다(하루 수십 번) 돌며 지적마다 findings_similar 를
      limit 50·최대 3회 재시도로 불렀다. 서한 증거 문장은 길고 흔한 낱말('귀사'·'b'·'4'·'또는')이 섞여
      OR 질의가 지적 DB 의 절반(13,055/28,491행)에 맞는다 — 한 번에 평시 3~4.5초로 anon
      statement_timeout(3초)을 넘는다. 24시간 250회 중 55회가 500 이었고, 같은 시각 방문자 검색·
      체크리스트 500 과 겹쳤다. 그 주 묶음이 이미 저장소에 있으면(사람이 검수해 커밋한 정본) 다시
      부를 이유가 없다 — 결과도 배포마다 흔들리지 않는다.
    저장본이 없을 때만(새 호, 키가 있을 때) 엔진을 부른다. 키도 없으면 None(그 카드만 빠진다)."""
    snap = snap or (slots_dir or SLOTS_DIR) / f"{pub}.similar.json"
    try:
        saved = json.loads(snap.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = None
    if isinstance(saved, dict) and saved.get("items"):
        return saved
    if url and key:
        return fetch(slots, url=url, key=key) or {}
    return None


def site_lines(rows: list[dict]) -> list[str]:
    """유사 사례 한 건 = 세 줄(머리 · 원문 한 조각 · 주소). 주소는 반드시 자기 줄 — 문장 끝·괄호에 붙으면
    링크드인이 링크를 덜 걸거나 마침표·괄호까지 주소로 먹는다(2026-11-16 대표 지적)."""
    return [f"{r['alias']} · {r['year']}년 {r['agency']} · {r['label']}\n‘{r['target']}’ — {r['fact']}\ngrm-solutions.com/findings/doc/{r['slug']}/"
            for r in rows if r["slug"]]


_SENT_END = re.compile(r"(?<=[다요]\.)\s+")
_URL_IN_LINE = re.compile(r"\s*[—(]?\s*((?:https://)?grm-solutions\.com/\S*?)\)?(?=\s|$)")


# 영어 문장 끝: 소문자·숫자·닫는 괄호/따옴표 뒤 마침표·물음표 + 공백 + 대문자/따옴표('211.113(b)' 같은 조항 마침표는 걸리지 않는다)
_SENT_END_EN = re.compile(r"(?<=[a-z0-9)\]”’\"'][.?!])\s+(?=[A-Z“‘\"0-9])")


def _cap_lines(text: str, lang: str = "ko") -> list[str]:
    """한 줄에 한 문장 — 모바일에서 문장 중간에 접히지 않게(링크드인 본문 규칙)."""
    rx = _SENT_END_EN if lang == "en" else _SENT_END
    return [x.strip() for x in rx.split(la._norm(text or "")) if x.strip()]


CAP_HEADS = {
    "ko": dict(claims="지적 내용", contrast="회사 회신과 FDA 판단", requests="FDA 요구", similar="GRM 지적사항 DB의 비슷한 사례",
               terms="관련 용어", not_stated="서한에 없는 것", url="전체 해설", glossary="grm-solutions.com/glossary/",
               sub="GRM 주간 브리프", sub_line="매주 FDA·EU·식약처 실사 지적 소식을 받아 보세요.",
               ns_prefix=r"^서한에 없는 것\s*[:：]\s*"),
    "en": dict(claims="What FDA cited", contrast="The firm's response and FDA's view", requests="What FDA asked for",
               similar="Similar cases in the GRM findings database", terms="Related terms", not_stated="Not in the letter",
               url="Full analysis", glossary="grm-solutions.com/en/glossary/",
               sub="GRM Weekly Brief", sub_line="Get the week's FDA, EU and MFDS inspection findings.", ns_prefix=r"^Not in the letter\s*:\s*"),
}


def _url_own_line(text: str) -> str:
    """줄 안에 섞인 사이트 주소를 다음 줄로 뺀다(앞의 '—'·'(' 와 뒤의 ')' 는 떼어 낸다)."""
    out = []
    for line in text.split("\n"):
        urls = [m.group(1) for m in _URL_IN_LINE.finditer(line)]
        rest = _URL_IN_LINE.sub("", line).rstrip(" ·:—(")
        if urls and rest.strip():
            out.append(rest)
            out.extend(urls)
        else:
            out.append(line)
    return "\n".join(out)


def caption_layout(lead: str, claims: str, contrast: str, requests: str, similar_block: list[str],
                   term_names: list[str], not_stated: str, caption_url: str, lang: str = "ko",
                   sub_url: str = "") -> str:
    """링크드인 본문 배치(코드 고정) — 첫 단락은 소제목 없이 사건, 그다음 ■ 소제목 단락.
    문장은 한 줄에 하나, 주소는 자기 줄, 단락 사이는 빈 줄 하나."""
    def sec(title: str, lines: list[str]) -> str:
        body = list(lines)
        while body and not body[-1]:
            body.pop()
        return "\n".join([f"■ {title}", *body]) if any(body) else ""
    H = CAP_HEADS[lang]
    ns = re.sub(H["ns_prefix"], "", la._norm(not_stated or ""))
    parts = [
        "\n".join(_cap_lines(lead, lang)),
        sec(H["claims"], _cap_lines(claims, lang)),
        sec(H["contrast"], _cap_lines(contrast, lang)),
        sec(H["requests"], _cap_lines(requests, lang)),
        sec(H["similar"], similar_block),
        sec(H["terms"], [" · ".join(term_names), H["glossary"]] if term_names else []),
        sec(H["not_stated"], _cap_lines(ns, lang)),
        sec(H["url"], [caption_url]),
        # [구독 유입 2026-11-17 대책 4] 맨 끝 구독 단락 — fit_caption 이 이것까지 세어 2,900자 상한을 지킨다.
        sec(H["sub"], [H["sub_line"], sub_url]) if sub_url else "",
    ]
    return _url_own_line("\n\n".join(x for x in parts if x)) + "\n"


# 링크드인 게시물 글자 한도는 3,000자 — 넘으면 게시 버튼이 막힌다(2026-11-16 영문 3,699자). 주소는 utm 까지 센다. 여유를 두고 2,900.
CAPTION_MAX = 2900
SHORT_FACT = 80


def _short_fact(text: str, limit: int = SHORT_FACT) -> str:
    t = la._norm(text).rstrip("…")
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0]
    return re.sub(r"\s*\([^)]*$", "", cut).rstrip(" ,;·") + "…"


def fit_caption(make, site: list[dict], limit: int = CAPTION_MAX) -> str:
    """본문을 상한 안에 넣는다 — 정해진 순서로만 줄인다: ① 유사 사례를 뒤에서부터 빼되 2건까지 ② 남은 사례 인용을 80자로
    ③ 사례를 1건·0건까지. 그래도 넘으면 슬롯 문장이 긴 것이다 — 거부(AnatomyError)하고 슬롯을 고치게 한다."""
    tries: list[list[dict]] = [site[:n] for n in range(len(site), 1, -1)]
    short = [dict(r, fact=_short_fact(r.get("fact") or "")) for r in site]
    tries += [short[:n] for n in range(min(len(site), 2), -1, -1)]
    if not site:
        tries = [[]]
    last = ""
    for rows in tries:
        last = make(rows)
        if len(last) <= limit:
            return last
    raise la.AnatomyError([f"본문 {len(last)}자 — 유사 사례를 다 빼도 링크드인 상한 {limit}자를 넘는다. 슬롯 caption 문장을 줄여라"])


def _load_glossary() -> list[dict]:
    try:
        return json.loads((WEB_DIR / "data" / "glossary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def glossary_terms(glossary: list[dict] | None, ids: list[str] | None = None) -> list[tuple[str, str]]:
    by = {g.get("id"): g.get("term_ko") for g in glossary or []}
    return [(by[i], i) for i in (TERM_IDS if ids is None else ids) if by.get(i)]


def build_grm_deck(brief_doc: dict, slots: dict, findings: dict, glossary: list[dict] | None = None,
                   *, base_url: str = lc.SITE_BASE_URL, similar: dict | None = None) -> dict[str, Any]:
    card = la.pick_anatomy_card(brief_doc, str(slots.get("card_id") or ""))
    if card is None:
        raise la.AnatomyError(["card_id 에 맞는 해부 가능한 경고서한이 브리프에 없다"])
    probs = validate_v3(card, slots, findings)
    if probs:
        raise la.AnatomyError(probs)
    db = db_for(findings, card, slots)
    ctx = s05_ctx(slots)
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
    terms = glossary_terms(glossary if glossary is not None else _load_glossary(), ctx["term_ids"])
    slides = [
        dict(kind="g_cover", chip=cv["chip"], h1=list(cv["headline"]), sub=cv["sub"], dots=dict(cv.get("dots") or {}),
             quote=dict(cv.get("quote") or {}), line=cv.get("line") or "", ft_right=src_note, ai=ai),
        dict(kind="g_find", corner="지적 내용", heading=s2["heading"], rows=s2["rows"], ft_right=src_note, ai=ai),
        dict(kind="g_reply", corner="회신 평가", heading=s3["heading"], sub=s3["sub"], pairs=s3["pairs"],
             repeat=(s3.get("repeat") or {}).get("text") or "", repeat_label=(s3.get("repeat") or {}).get("label") or "반복 위반",
             ft_right=src_note, ai=ai),
        dict(kind="g_req", corner="시정 요구", heading=s4["heading"], sub=f"서한이 요구한 항목 {len(items)}건",
             items=[(it["title"], it["text"]) for it in s4["items"]], deadline=la._deadline(card), ft_right=src_note, ai=ai),
        *([dict(kind="g_site", corner="유사 사례", heading=s5["heading"], rows=site, ft_right="사이트 유사 사례 검색 결과 · 정도 라벨은 코드 규칙", ai=ai)] if site else []),
        dict(kind="g_sim", corner="DB 연관" if site else "유사 사례", heading="DB로 본 반복과 선행" if site else s5["heading"], db=db,
             lines=(db_lines(db) if site else case_lines(db, no_match_note=ctx["no_match_note"]) + db_lines(db)), comment=s5["comment"], terms=terms,
             keywords=ctx["keywords"], cfr_rows=db["cfr_rows"], map_header=ctx["map_header"],
             ft_right=f"지적사항 DB {db['measured_on']} 기준 · 직접 센 숫자", ai=ai),
        dict(kind="g_end", corner="서한에 없는 것", heading=s6["heading"], not_stated=s6["not_stated"],
             question=s6["question"], ft_right="원문 링크는 브리프 페이지에서 확인", ai=ai),
    ]
    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)
    url = f"{base_url}/briefs/{pub}/"
    cap = slots["caption"]
    caption_url = lc.with_utm(url, *lc.LINKEDIN_WEEKLY, lc.linkedin_weekly_campaign(pub))
    def make(rows: list[dict]) -> str:
        cases = site_lines(rows) if site else case_lines(db, polite=True, no_match_note=ctx["no_match_note"])
        similar_block = [la._norm(cap["similar"]), "", *("\n\n".join(cases).split("\n") if cases else []),
                         "", *[x for x in [ctx["method"], *db_lines(db, polite=True), ctx["map_note"]] if x]]
        while similar_block and similar_block[-1] == "":
            similar_block.pop()
        text = caption_layout(cap["lead"], cap["claims"], cap["contrast"], cap["requests"], similar_block,
                              [n for n, _ in terms], cap["not_stated"], caption_url,
                              sub_url=lc.subscribe_url(base_url, "ko", "linkedin_weekly", date=pub))
        # 캡션 본문 속 글자 주소(`grm-solutions.com/...`)에도 같은 표식 — 링크드인이 링크로 걸어 주지만 표식이 없어 '직접'에 섞였다.
        return utm.tag_bare_site_urls(text, "linkedin_weekly", date=pub)
    ko = fit_caption(make, site)
    return {"pub": pub, "lang": "ko", "slides": slides, "caption": ko,
            "doc_title": f"GRM {m}월 {wk}주 경고서한 분석(GRM 판)", "url": url, "caption_url": caption_url,
            "card_id": str(card.get("id")), "mode": "grm"}


MAX_PER_ISSUE = 2  # 한 호에 해부 최대 2편 — 수요일=1위(a1), 금요일=2위(a2). 1건뿐이면 수요일만(2026-11-16 대표 결정)


def slot_path_for(pub: str, rank: int, slots_dir: Path | None = None) -> Path:
    return (slots_dir or SLOTS_DIR) / f"{pub}.a{rank}.v3.json"


def slot_paths(pub: str, slots_dir: Path | None = None) -> list[tuple[int, Path]]:
    """그 호의 슬롯 — `<pub>.a1.v3.json`·`<pub>.a2.v3.json`. 옛 단일 슬롯 `<pub>.v3.json`(11-02·11-16)은 a1 로 본다."""
    d = slots_dir or SLOTS_DIR
    out: list[tuple[int, Path]] = []
    for k in range(1, MAX_PER_ISSUE + 1):
        sp = slot_path_for(pub, k, d)
        if not sp.exists() and k == 1 and (d / f"{pub}.v3.json").exists():
            sp = d / f"{pub}.v3.json"
        if sp.exists():
            out.append((k, sp))
    return out


def sidecar(slot_path: Path, kind: str) -> Path:
    """슬롯 옆 파일 — `<pub>.a1.v3.json` → `<pub>.a1.similar.json`·`<pub>.a1.letter_en.txt`(옛 단일 슬롯은 `<pub>.similar.json`)."""
    return slot_path.with_name(slot_path.name[: -len(".v3.json")] + "." + kind)


def rank_problems(doc: dict, slots: dict, rank: int) -> list[str]:
    """슬롯의 서한이 규칙 순위 `rank` 번째인지 — 사람·LLM 이 서한을 바꿔 고르지 못하게."""
    ranked = la.ranked_anatomy_cards(doc)
    if rank > len(ranked):
        return [f"a{rank}: 해부 가능한 서한이 {len(ranked)}건뿐이다 — 이 슬롯은 만들지 않는다"]
    want = str(ranked[rank - 1].get("id"))
    return [] if str(slots.get("card_id")) == want else [f"a{rank}: card_id 가 규칙 {rank}위({want})가 아니다"]


def _material(c: dict) -> dict[str, Any]:
    da = c.get("deep_analysis") or {}
    return dict(title_issue=c.get("title_issue"), summary=c.get("summary"), key_facts=c.get("key_facts"), facts=c.get("facts"),
                key_violations=[{k: v.get(k) for k in ("citation", "description", "original")} for v in da.get("key_violations") or []],
                fda_evaluation=da.get("fda_evaluation"), required_remediation=da.get("required_remediation"),
                administrative_risks=da.get("administrative_risks"), deadline=la._deadline(c), issue_date=la._issue_date(c))


def candidates(doc: dict, slot_path: Path | None = None) -> dict[str, Any]:
    """주간 슬롯 작성자(대외홍보팀 Routine)에게 줄 재료 — 대상 선정은 코드가 한다(ANATOMY_CARDS.md 규칙).
    `picks` = 규칙 상위 최대 2건(a1=수요일, a2=금요일). 고른 서한의 원천 필드만 준다 — 슬롯 문장은 이 안에서만 근거를 찾는다.
    (`pick`·`material`·`slot_path` 는 a1 — 옛 호출 호환)"""
    brief = doc.get("brief") or {}
    pub = str(brief.get("publish_date") or "")
    d = slot_path.parent if slot_path else SLOTS_DIR
    elig = la.ranked_anatomy_cards(doc)
    skipped = [dict(id=c.get("id"), title_issue=c.get("title_issue")) for c in doc.get("cards") or []
               if lc.is_warning_letter(c) and la.is_records_review(c)]
    out: dict[str, Any] = {"pub": pub,
                           "ranked": [dict(id=c.get("id"), title_issue=c.get("title_issue"), score=list(la.anatomy_score(c)))
                                      for c in elig],
                           "records_review_skipped": skipped}
    picks = []
    for k, c in enumerate(elig[:MAX_PER_ISSUE], 1):
        sp = slot_path_for(pub, k, d)
        exists = sp.exists() or (k == 1 and (d / f"{pub}.v3.json").exists())
        picks.append(dict(rank=k, day="수요일" if k == 1 else "금요일", id=c.get("id"), slot_path=str(sp), slot_exists=exists,
                          firm_name_do_not_write=c.get("headline_target"), material=_material(c)))
    out["picks"] = picks
    if not picks:
        out.update(pick=None, reason="해부 가능한 경고서한 없음(서한 0건, 본문·평가·요구 미확보, 또는 기록 요청형뿐) — 이번 주는 넘긴다")
        return out
    out.update(pick=picks[0]["id"], slot_path=picks[0]["slot_path"], slot_exists=picks[0]["slot_exists"],
               firm_name_do_not_write=picks[0]["firm_name_do_not_write"], material=picks[0]["material"])
    return out


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
.g-quote{margin-top:46px;border-left:10px solid #1A1815;padding:6px 0 6px 30px}
.g-quote small{display:block;font-size:20px;font-weight:800;color:#6C6A64;margin-bottom:10px}
.g-quote blockquote{margin:0;font-size:33px;line-height:1.42;font-style:italic;font-weight:600;color:#141413}
.g-quote .ko{margin-top:18px;font-size:30px;line-height:1.45;font-weight:700;color:#3D3D3A;word-break:keep-all}
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
# 그림 속 고정 문구 — 언어별(영문판은 한글 0자여야 한다). 국문 값은 종전 문자열 그대로.
LABELS = {
    "ko": dict(key=_KEY_SPAN, quote="서한 원문", resp="회사 회신", why="FDA: 부적절한 이유", repeat="반복 위반",
               req="요구 {n}/{m}", due="회신 기한: ", target="이번 서한의 지적: ", map_head="인용 조항",
               col_wl="경고서한", col_recent="2025년 이후", n_unit="{n}건", basis="근거: ", ask="우리 현장에 던질 질문"),
    "en": dict(key={"o": "Letter text", "h": "GRM note", "n": "Not in the letter"}, quote="From the letter",
               resp="Firm's response", why="FDA: why it falls short", repeat="Repeat violation",
               req="Request {n}/{m}", due="Response due: ", target="This letter: ", map_head="Cited sections",
               col_wl="Warning letters", col_recent="Since 2025", n_unit="{n}", basis="Basis: ",
               ask="A question for your site"),
}
_KEY_OF = {"g_find": "oh", "g_reply": "oh", "g_req": "oh", "g_site": "h", "g_sim": "h", "g_end": "nh"}  # 그 카드에 실제로 있는 종류만 표시


def _key(kind: str, lang: str = "ko") -> str:
    ks = LABELS[lang]["key"]
    return '<div class="g-key">' + "".join(f'<span class="{c}">{ks[c]}</span>' for c in _KEY_OF.get(kind, "oh")) + "</div>"


def _slide_html(s: dict, lang: str = "ko") -> str:
    k = s["kind"]
    L = LABELS[lang]
    p: list[str] = []
    if k == "g_cover":
        d, q = s.get("dots") or {}, s.get("quote") or {}
        p.append(f'<div class="g-chip">{_e(s["chip"])}</div>')
        p.append(lc._h1(s["h1"], 92))
        p.append(f'<p class="g-cs">{_e(s["sub"])}</p>')
        line = f'<div class="g-line">{_e(s["line"])}</div>' if s.get("line") else ""
        if d:
            grid = "".join(f'<i class="{"m" if n < d["marked"] else ""}"></i>' for n in range(d["total"]))
            p.append(f'<div class="g-dots"><div class="g-grid">{grid}</div><div class="g-dcap">{_e(d["caption"])}</div>{line}</div>')
        else:  # 분모가 없는 서한 — 서한 원문 한 줄(영어 그대로 + 우리 번역)이 표지 그림이다
            ko_line = f'<div class="ko">{_e(q.get("ko"))}</div>' if q.get("ko") else ""
            p.append(f'<div class="g-quote"><small>{L["quote"]}</small><blockquote lang="en">“{_e(q.get("en"))}”</blockquote>'
                     f'{ko_line}{line}</div>')
    else:
        p.append(_key(k, lang))
        p.append(f'<span class="g-corner">{_e(s["corner"])}</span>')
        p.append(f'<h2 class="g-h">{_e(s["heading"])}</h2>')
        if k == "g_find":
            p.append('<div class="g-fr">' + "".join(
                f'<div class="g-f"><span class="c">{_e(r["cite"])}</span><div class="t">{_e(r["text"])}</div>'
                f'<div class="e">{_e(la._ev_list(r["evidence"])[0])}</div></div>' for r in s["rows"]) + "</div>")
        elif k == "g_reply":
            p.append(f'<div class="g-sub">{_e(s["sub"])}</div>')
            p.append('<div class="g-rp">' + "".join(
                f'<div class="g-p"><div class="r"><small>{L["resp"]}</small>{_e(x["response"])}</div>'
                f'<div class="j"><small>{L["why"]}</small>{_e(x["why"])}</div></div>' for x in s["pairs"]) + "</div>")
            if s.get("repeat"):
                p.append(f'<div class="g-rep">{_e(s.get("repeat_label") or L["repeat"])} · {_e(s["repeat"])}</div>')
        elif k == "g_req":
            p.append('<div class="g-req">' + "".join(
                f'<div class="g-r"><em>{L["req"].format(n=n, m=len(s["items"]))}</em><b>{_e(t)}</b><span>{_e(x)}</span></div>'
                for n, (t, x) in enumerate(s["items"], 1)) + "</div>")
            if s.get("deadline"):
                p.append(f'<div class="g-dl">{L["due"]}{_e(s["deadline"])}</div>')
        elif k == "g_site":
            p.append('<div class="g-cs3 g-site">' + "".join(
                f'<div><b>{_e(r["alias"])}<small>{_e(r["year"])} · FDA {_e(r["agency"])}</small></b>'
                f'<span><small class="t">{L["target"]}{_e(r["target"])}</small><em class="lv {r["level"]}">{_e(r["label"])}</em>{_e(r["fact"])}'
                f'<small class="h">{_e(r["history"])}</small>'
                f'<small class="u">grm-solutions.com{"/en" if lang == "en" else ""}/findings/doc/{_e(r["slug"])}/</small></span></div>' for r in s["rows"]) + "</div>")
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
            if s.get("keywords") or s.get("terms"):
                p.append('<div class="g-chips">' + "".join(f'<i>{_e(k_)}</i>' for k_ in s.get("keywords") or [])
                         + "".join(f'<u>{_e(n)}</u>' for n, _ in s.get("terms") or []) + '</div>')
            if db["cfr_rows"]:
                p.append(f'<div class="g-map{" big" if big else ""}"><div class="mh"><span>{_e(s.get("map_header") or L["map_head"])}</span><span>{L["col_wl"]}</span><span>{L["col_recent"]}</span></div>' + "".join(
                    f'<div><span>{_e(r["label"])}<small>{_e(r["cfr"])}</small></span><span>{L["n_unit"].format(n=r["n"])}</span><span>{L["n_unit"].format(n=r["recent"])}</span></div>'
                    for r in db["cfr_rows"]) + '</div>')
            p.append(f'<div class="g-cm">{_e(s["comment"])}</div>')
        elif k == "g_end":
            for x in s["not_stated"]:
                p.append(f'<div class="g-ns">{_e(x["text"])}<small>{L["basis"]}{_e(x["basis"])}</small></div>')
            p.append(f'<div class="g-qq"><small>{L["ask"]}</small>{_e(s["question"])}</div>')
    ai = f'<div class="ai">{_e(s["ai"])}</div>' if s.get("ai") else ""
    cls = " g-cover g-page" if k == "g_cover" else " g-page"
    return (f'<section class="slide{cls}"><div class="hd"><div class="brand">{lc.OWL_SVG}<span class="w"><b>GRM</b>'
            f'<span>Global Regulatory Monitor</span></span></div><div class="pg">{s["idx"]:02d} / {s["total"]:02d}</div></div>'
            f'<div class="main">{"".join(p)}</div>'
            f'<div class="ft"><span>grm-solutions.com</span><span>{_e(s.get("ft_right", ""))}</span></div>{ai}</section>')


def render_html(deck: dict, *, font_links: bool = True) -> str:
    css = (lc.CSS + CSS).replace("HEX_DARK", lc._data_uri(lc._HEX.format(op="0.07"))) \
        .replace("HEX_CREAM", lc._data_uri(lc._HEX.format(op="0.16")))
    lang = deck.get("lang") or "ko"
    body = "".join(_slide_html(s, lang) for s in deck["slides"])
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{_e(deck["doc_title"])}</title>'
            f'{lc.FONT_LINKS if font_links else ""}<style>{css}</style></head><body class="lang-{lang}">{body}</body></html>')


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="링크드인 사건 해부 GRM 판(시범 v3)")
    ap.add_argument("--brief", help="브리프 JSON(미지정 시 --data 의 최신)")
    ap.add_argument("--data", default=str(WEB_DIR / "data" / "briefs"), help="--brief 가 없을 때 최신 브리프를 찾을 디렉터리")
    ap.add_argument("--slots", help="슬롯 JSON 하나만(기본: 그 호의 <발행일>.a1·a2.v3.json, 옛 <발행일>.v3.json 은 a1)")
    ap.add_argument("--rank", type=int, choices=range(1, MAX_PER_ISSUE + 1),
                    help="이 순위 슬롯만(--save-letter·--save-similar·--check·렌더). 없으면 그 호의 슬롯 전부")
    ap.add_argument("--out", help="출력 폴더")
    ap.add_argument("--dist", help="배포 루트 — briefs/<발행일>/ 아래에 낸다(linkedin.* 옆, 사이트맵 비등록)")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--findings", default=str(FINDINGS), help="지적사항 DB 사본(기본 web/data/findings_docs.json · .gz 허용)")
    ap.add_argument("--candidates", action="store_true",
                    help="주간 슬롯 작성용: 규칙으로 고른 서한과 후보 순위·원천 재료를 JSON 으로 출력(파일을 쓰지 않는다)")
    ap.add_argument("--check", action="store_true", help="슬롯 검증만(근거·숫자·실명·AI 티·후킹). 실패면 exit 1, 파일을 쓰지 않는다")
    ap.add_argument("--save-similar", action="store_true",
                    help="<발행일>.similar.json 저장본이 없으면 유사 사례 엔진을 불러 슬롯 폴더에 저장한다(SUPABASE_URL·SUPABASE_ANON_KEY)")
    ap.add_argument("--save-letter", metavar="HANDOFF_JSON",
                    help="영문판 근거 원천 저장: 그 주 handoff 에서 대상 서한 전문을 <발행일>.letter_en.txt 로(서명 앞까지)")
    args = ap.parse_args(argv)
    if not (args.out or args.dist or args.candidates or args.check or args.save_similar or args.save_letter):
        ap.error("--out 또는 --dist 가 필요하다")
    bp = Path(args.brief) if args.brief else lc.latest_brief_path(Path(args.data))
    if bp is None:
        print("::warning::브리프 JSON 없음 — 만들지 않는다", file=sys.stderr)
        return 0
    doc = json.loads(bp.read_text(encoding="utf-8"))
    pub = str((doc.get("brief") or {}).get("publish_date") or "")
    if args.candidates:
        print(json.dumps(candidates(doc, Path(args.slots) if args.slots else None), ensure_ascii=False, indent=1))
        return 0
    if args.save_letter:
        import linkedin_grm_en as lge
        picks = candidates(doc).get("picks") or []
        rc = 0
        for pk in picks:
            if args.rank and pk["rank"] != args.rank:
                continue
            text = lge.letter_from_handoff(json.loads(Path(args.save_letter).read_text(encoding="utf-8")), str(pk["id"]))
            if not text:
                print(f"::warning::handoff 에 서한 전문이 없다(a{pk['rank']} {pk['id']}) — 그 편 영문판은 만들지 않는다", file=sys.stderr)
                rc = 1
                continue
            lp = sidecar(Path(pk["slot_path"]), "letter_en.txt")
            lp.write_text(text + "\n", encoding="utf-8", newline="\n")
            print(f"저장: {lp.name} · {len(text)}자")
        return rc
    if args.slots:
        sp0 = Path(args.slots)
        if not sp0.exists():
            print(f"::warning::슬롯 없음({sp0.name}) — 만들지 않는다", file=sys.stderr)
            return 0
        targets = [(int(json.loads(sp0.read_text(encoding="utf-8")).get("rank") or args.rank or 1), sp0)]
    else:
        targets = [(k, p) for k, p in slot_paths(pub) if not args.rank or k == args.rank]
    if not targets:
        print(f"::warning::{pub} 해부 슬롯 없음 — 만들지 않는다", file=sys.stderr)
        return 0
    findings = load_findings(Path(args.findings))
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_ANON_KEY", "").strip()
    rc = 0
    for rank, sp in targets:
        slots = json.loads(sp.read_text(encoding="utf-8"))
        tag = f"a{rank}"
        if args.check:
            card = la.pick_anatomy_card(doc, str(slots.get("card_id") or ""))
            probs = validate_v3(card, slots, findings) if card else ["card_id 에 맞는 해부 가능한 경고서한이 브리프에 없다"]
            probs += rank_problems(doc, slots, rank)
            if card and slots.get("en"):
                import linkedin_grm_en as lge
                probs += lge.validate_en(card, slots, findings, lge.load_letter_at(sidecar(sp, "letter_en.txt")))
            if not probs:  # 본문 글자 상한(링크드인 3,000자)은 덱을 조립해 봐야 안다 — 줄일 수 있는 만큼 줄인 뒤의 길이를 본다
                sim = load_similar(slots, pub, url="", key="", snap=sidecar(sp, "similar.json"))
                try:
                    lens = [len(build_grm_deck(doc, slots, findings, similar=sim)["caption"])]
                    if slots.get("en"):
                        lens.append(len(lge.build_en_deck(doc, slots, findings, lge.load_letter_at(sidecar(sp, "letter_en.txt")),
                                                          similar=sim)["caption"]))
                    print(f"  [{tag}] 본문 글자 수(상한 {CAPTION_MAX}): {' · '.join(map(str, lens))}")
                except la.AnatomyError as exc:
                    probs += exc.problems
            for pr in probs:
                print(f"  - [{tag}] {pr}")
            print(f"linkedin_grm --check: {pub} {tag} · 문제 {len(probs)}건")
            rc |= 1 if probs else 0
            continue
        if args.save_similar:
            snap = sidecar(sp, "similar.json")
            if snap.exists():
                print(f"저장본 있음 — 부르지 않는다: {snap.name}")
                continue
            if not (url and key):
                print("::warning::SUPABASE_URL·SUPABASE_ANON_KEY 없음 — 저장하지 않는다", file=sys.stderr)
                rc = 1
                continue
            sim = fetch_similar(slots, url=url, key=key)
            if not sim:
                rc = 1
                continue
            snap.write_text(json.dumps(sim, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
            print(f"저장: {snap.name} · 항목 {len(sim['items'])} · 실패 지적 {sim['failed_findings']}")
            continue
        rc |= _render_slot(args, doc, pub, rank, sp, slots, findings, url, key)
    return rc


def _render_slot(args, doc: dict, pub: str, rank: int, sp: Path, slots: dict, findings: dict, url: str, key: str) -> int:
    """한 편(a1/a2)을 낸다 — `linkedin_grm_a{k}*`(국문)·`linkedin_grm_a{k}_en*`(영문). a1 은 옛 이름(`linkedin_grm*`)으로도
    복사한다(11-16 에 이미 나간 주소·옛 PC 작업 호환)."""
    import shutil
    similar = load_similar(slots, pub, url=url, key=key, snap=sidecar(sp, "similar.json"))
    probs = rank_problems(doc, slots, rank)
    try:
        if probs:
            raise la.AnatomyError(probs)
        deck = build_grm_deck(doc, slots, findings, similar=similar)
    except la.AnatomyError as exc:
        print(f"::warning::GRM 판 덱 a{rank} 검증 실패 — 이 편은 만들지 않는다", file=sys.stderr)
        for pr in exc.problems:
            print(f"  - {pr}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else Path(args.dist) / "briefs" / pub
    out.mkdir(parents=True, exist_ok=True)
    stem = f"linkedin_grm_a{rank}"
    if similar:
        (out / f"similar_a{rank}.json").write_text(json.dumps(similar, ensure_ascii=False, indent=1), encoding="utf-8")
    chrome = None if args.no_pdf else lc.find_chrome()
    n = _write_deck(deck, out, stem, chrome)
    written = [(stem, "linkedin_grm", len(deck["slides"]))]
    print(f"{stem}: {pub} · {len(deck['slides'])}장 · 본문 {len(deck['caption'])}자 · PNG {n} → {out}")
    # 영문판 — 슬롯에 en 블록이 있는 편만. 실패해도 국문판은 그대로 낸다(그 편 영문만 빠진다).
    if slots.get("en"):
        import linkedin_grm_en as lge
        try:
            en_deck = lge.build_en_deck(doc, slots, findings, lge.load_letter_at(sidecar(sp, "letter_en.txt")), similar=similar)
        except la.AnatomyError as exc:
            print(f"::warning::GRM 판 영문 덱 a{rank} 검증 실패 — 영문만 만들지 않는다", file=sys.stderr)
            for pr in exc.problems:
                print(f"  - {pr}", file=sys.stderr)
            en_deck = None
        if en_deck:
            n = _write_deck(en_deck, out, f"{stem}_en", chrome)
            written.append((f"{stem}_en", "linkedin_grm_en", len(en_deck["slides"])))
            print(f"{stem}_en: {pub} · {len(en_deck['slides'])} slides · caption {len(en_deck['caption'])} chars · PNG {n} → {out}")
    if rank == 1:
        for new, old, k in written:
            for ext in (".txt", ".html", *[f"_{i:02d}.png" for i in range(1, k + 1)]):
                if (out / f"{new}{ext}").exists():
                    shutil.copyfile(out / f"{new}{ext}", out / f"{old}{ext}")
        if similar:
            shutil.copyfile(out / f"similar_a{rank}.json", out / "similar.json")
    return 0


def _write_deck(deck: dict, out: Path, stem: str, chrome: str | None) -> int:
    (out / f"{stem}.txt").write_text(deck["caption"], encoding="utf-8")
    (out / f"{stem}.html").write_text(render_html(deck), encoding="utf-8")
    n = 0
    if chrome:
        for i, slide in enumerate(deck["slides"], 1):
            tmp = out / f"{stem}_{i:02d}.tmp.html"
            tmp.write_text(render_html(dict(deck, slides=[slide])), encoding="utf-8")
            try:
                lc.render_png(tmp, out / f"{stem}_{i:02d}.png", chrome)
                n += 1
            finally:
                tmp.unlink(missing_ok=True)
    return n


if __name__ == "__main__":
    raise SystemExit(main())
