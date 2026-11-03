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
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB_DIR))
import linkedin_anatomy as la  # noqa: E402
import linkedin_cards as lc  # noqa: E402

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
    cases = []
    for d in sorted(em, key=lambda x: str(x["published_date"]), reverse=True):
        if len(cases) >= n_cases:
            break
        f = next((x for x in d.get("findings") or [] if x.get("category_label_ko") == "환경모니터링"
                  and _EM_TOPIC.search(x.get("text_orig") or "")), None)
        if f is None:
            continue
        txt = re.sub(r"\s+", " ", str((f or {}).get("text_ko") or "")).strip()
        if len(txt) < 20 or any(t.lower() in (txt + " " + " ".join(x.get("text_ko") or "" for x in d["findings"])).lower()
                                for t in _firm_tokens(d.get("firm_name"))):
            continue
        cfr = next((r for r in (f.get("cfr_refs") or []) if r.startswith("21 CFR 21")), "")
        cases.append(dict(year=str(d["published_date"])[:4], agency="FDA 경고서한", cfr=cfr,
                          text=txt[:60].rstrip() + ("…" if len(txt) > 60 else ""), slug=d.get("slug")))
    for i, c in enumerate(cases):
        c["alias"] = f"업체 {'ABC'[i]}"
    return dict(measured_on=findings.get("measured_on") or "", wl_total=len(wls), em_n=len(em), em_years=dict(sorted(years.items())),
                em_firms=len(firms), em_483_before=b483_before, em_repeat=repeat, cfr_rows=cfr_rows, pet_other=len(pet),
                cases=cases)


def db_numbers(db: dict) -> set[str]:
    blob = " ".join(str(v) for v in (db["measured_on"], db["wl_total"], db["em_n"], db["em_firms"], db["em_483_before"],
                                     db["em_repeat"], db["pet_other"], *db["em_years"].keys(), *db["em_years"].values()))
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
        return [f"환경모니터링으로 지적된 경고서한은 {db['em_n']}건({yrs[0]}~{yrs[-1]}년), {db['em_firms']}개 업체입니다.",
                f"이 중 {db['em_483_before']}곳은 서한 전에 483을 먼저 받았고, {db['em_repeat']}곳은 서한을 두 번 이상 받았습니다."]
    return [f"환경모니터링으로 지적된 경고서한은 {db['em_n']}건({yrs[0]}~{yrs[-1]}년), {db['em_firms']}개 업체.",
            f"이 중 {db['em_483_before']}곳은 서한 전에 483을 먼저 받았다. 서한을 두 번 이상 받은 곳은 {db['em_repeat']}곳."]


def build_grm_deck(brief_doc: dict, slots: dict, findings: dict, glossary: list[dict] | None = None,
                   *, base_url: str = lc.SITE_BASE_URL) -> dict[str, Any]:
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
    issued = la._issue_date(card)
    cv, s2, s3, s4, s5, s6 = (slots[k] for k in ("cover", "s02", "s03", "s04", "s05", "s06"))
    items = (card["deep_analysis"].get("required_remediation") or {}).get("items") or []
    src_note = f"원문: FDA 경고서한{f' ({issued})' if issued else ''} · 업체명 비공개"
    ai = lc.AI_NOTE
    slides = [
        dict(kind="g_cover", chip=cv["chip"], h1=list(cv["headline"]), sub=cv["sub"], dots=dict(cv["dots"]),
             line=cv.get("line") or "", ft_right=src_note, ai=ai),
        dict(kind="g_find", corner="지적 내용", heading=s2["heading"], rows=s2["rows"], ft_right=src_note, ai=ai),
        dict(kind="g_reply", corner="회신 평가", heading=s3["heading"], sub=s3["sub"], pairs=s3["pairs"],
             repeat=s3["repeat"]["text"], ft_right=src_note, ai=ai),
        dict(kind="g_req", corner="시정 요구", heading=s4["heading"], sub=f"서한이 요구한 항목 {len(items)}건",
             items=[(it["title"], it["text"]) for it in s4["items"]], deadline=la._deadline(card), ft_right=src_note, ai=ai),
        dict(kind="g_sim", corner="유사 사례", heading=s5["heading"], db=db, lines=db_lines(db), comment=s5["comment"],
             ft_right=f"지적사항 DB {db['measured_on']} 기준 · 직접 센 숫자", ai=ai),
        dict(kind="g_end", corner="서한에 없는 것", heading=s6["heading"], not_stated=s6["not_stated"],
             question=s6["question"], ft_right="원문 링크는 브리프 페이지에서 확인", ai=ai),
    ]
    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)
    url = f"{base_url}/briefs/{pub}/"
    cap = slots["caption"]
    caption_url = lc.with_utm(url, *lc.LINKEDIN_WEEKLY, lc.linkedin_weekly_campaign(pub))
    sim = " ".join([la._norm(cap["similar"]), *db_lines(db, polite=True)])
    ko = "\n\n".join([la._norm(cap["lead"]), la._norm(cap["claims"]), la._norm(cap["contrast"]),
                      la._norm(cap["requests"]), sim, la._norm(cap["not_stated"])])
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
.g-map div{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:10px;padding:8px 2px;border-bottom:1px solid #D9CFC0}
.g-map small{display:block;font-size:17px;color:#6C6A64}
.g-cs3{margin-top:12px;display:flex;flex-direction:column;gap:8px}
.g-cs3 div{display:grid;grid-template-columns:96px 1fr;gap:12px;padding:10px 14px;border:1.5px dashed #8A8478;border-radius:8px;font-size:19px;line-height:1.45;word-break:keep-all}
.g-cs3 b{font-size:19px}.g-cs3 small{display:block;font-size:16px;color:#6C6A64}
.g-cm{margin-top:10px;font-size:22px;font-weight:700;color:#A14B30;word-break:keep-all}
.g-ns{background:#FBF1DD;border:2px solid #E9C98C;border-radius:12px;padding:28px 30px;margin-bottom:22px;font-size:31px;line-height:1.5;word-break:keep-all;color:#3F2F0B}
.g-ns small{display:block;font-size:21px;font-weight:700;margin-top:8px;color:#7A6120}
.g-qq{margin-top:22px;border:3px solid #1A1815;border-radius:14px;padding:26px 30px;font-size:34px;font-weight:800;line-height:1.4;word-break:keep-all}
.g-qq small{display:block;font-size:19px;font-weight:700;margin-bottom:8px;color:#6C6A64}
"""


def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


_KEY = '<div class="g-key"><span class="o">서한 원문</span><span class="h">GRM 해설</span><span class="n">서한에 없음</span></div>'


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
        p.append(_KEY)
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
        elif k == "g_sim":
            db = s["db"]
            mx = max(db["em_years"].values()) or 1
            p.append('<div class="g-yr">' + "".join(
                f'<div><b>{n}</b><i style="height:{max(6, int(100 * n / mx))}px"></i>{y}</div>' for y, n in db["em_years"].items()) + "</div>")
            p.extend(f'<div class="g-ln">{_e(x)}</div>' for x in s["lines"])
            p.append('<div class="g-map">' + "".join(
                f'<div><span>{_e(r["label"])}<small>대응 조항 {_e(r["cfr"])}</small></span><span>서한 {r["n"]}건</span><span>2025년 이후 {r["recent"]}건</span></div>'
                for r in db["cfr_rows"]) + '</div>')
            p.append(f'<div class="g-cs3">' + "".join(
                f'<div><b>{_e(c["alias"])}<small>{_e(c["year"])} · {_e(c["cfr"] or c["agency"])}</small></b><span>{_e(c["text"])}</span></div>'
                for c in db["cases"]) + "</div>")
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
    ap.add_argument("--brief", required=True)
    ap.add_argument("--slots", help="v3 슬롯 JSON(기본: web/data/linkedin_anatomy/<발행일>.v3.json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args(argv)
    doc = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    pub = str((doc.get("brief") or {}).get("publish_date") or "")
    sp = Path(args.slots) if args.slots else SLOTS_DIR / f"{pub}.v3.json"
    if not sp.exists():
        print(f"::warning::v3 슬롯 없음({sp.name}) — 만들지 않는다", file=sys.stderr)
        return 0
    slots = json.loads(sp.read_text(encoding="utf-8"))
    findings = json.loads(FINDINGS.read_text(encoding="utf-8"))
    try:
        deck = build_grm_deck(doc, slots, findings)
    except la.AnatomyError as exc:
        print("::warning::GRM 판 덱 검증 실패 — 만들지 않는다", file=sys.stderr)
        for pr in exc.problems:
            print(f"  - {pr}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "linkedin_grm.txt").write_text(deck["caption"], encoding="utf-8")
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
