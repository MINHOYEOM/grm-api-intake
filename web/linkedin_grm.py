#!/usr/bin/env python3
"""링크드인 '사건 해부' GRM 판(시범 v2) — 구조는 `linkedin_anatomy` 와 같고, 겉모습·코너·근거 표시를 GRM 식으로 바꾼다.

v1(`linkedin_anatomy.py`)은 벤치마크의 6장 구성을 거의 그대로 옮겼다. 대표 피드백(11-03): "따라한 느낌이 너무
많다." v2 는 같은 6칸 기능(표지·지적·분석·회신/판단·요구·시사점)을 유지하되 GRM 만 가진 자산으로 채운다.

GRM 시그니처 4개 (company/editorial/ANATOMY_SIGNATURES.md)
  1. **세 겹 표시** — 서한 원문(검정) / GRM 해설(크림) / 서한에 없는 말(노랑). 모든 문장이 셋 중 하나다.
  2. **원문 대조** — 영어 원문과 우리 번역을 나란히. 코드가 두 문장이 카드 원천에 그대로 있는지 검증한다.
  3. **DB로 본 자리** — 지적사항 DB(`findings_docs.json`)에서 코드가 센 숫자만. LLM 이 쓰지 않는다.
  4. **점 그림** — 서한의 비율 숫자("약 75건 중 2건")를 점 75개로 그린다. 막대가 아니라 분모를 보이게.
  (+ 용어사전 주소 띠: 카드에 쓴 용어를 사전 페이지로 잇는다.)

비용 0 구조·검증 게이트는 v1 과 같다(유료 API 코드 없음, 슬롯이 근거를 못 대면 덱을 만들지 않는다).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB_DIR))
import linkedin_anatomy as la  # noqa: E402
import linkedin_cards as lc  # noqa: E402

SCHEMA = "grm-anatomy-slots/v2"
SLOTS_DIR = la.SLOTS_DIR
FINDINGS = WEB_DIR / "data" / "findings_docs.json"
GLOSSARY = WEB_DIR / "data" / "glossary.json"

LIMITS = {"headline_line": 15.5, "quote_note": 52, "db_line": 70, "pair_resp": 28, "pair_judg": 66,
          "item_title": 14, "item_text": 62, "ns": 74, "question": 40}

# 지적사항 DB 의 출처 라벨 → 카드에 쓰는 이름
_SRC = [("FDA Warning Letter", "FDA 경고서한"), ("FDA 483", "FDA 483"), ("MFDS", "식약처"),
        ("Health Canada Inspection", "캐나다 보건부")]
_ISO5 = re.compile(r"ISO 5|ISO class 5|Grade A|Class 100", re.I)


# ── 코드가 세는 사실: 지적사항 DB ───────────────────────────────────────────────────────────────

def db_position(findings: dict, card: dict) -> dict[str, Any]:
    """이 서한의 주제가 우리 DB 에서 차지하는 자리. 전부 센 숫자 — 문장을 만들지 않는다."""
    docs = findings.get("documents") or []
    tgt = lc.firm_name(card).lower() if hasattr(lc, "firm_name") else ""
    den: dict[str, int] = {}
    em: dict[str, int] = {}
    iso: dict[str, int] = {}
    for d in docs:
        s = d.get("source")
        den[s] = den.get(s, 0) + 1
        if "환경모니터링" in (d.get("categories") or []):
            em[s] = em.get(s, 0) + 1
        if any(_ISO5.search(f.get("text_orig") or "") for f in d.get("findings") or []):
            iso[s] = iso.get(s, 0) + 1
    rows = [dict(label=lab, n=em.get(src, 0), total=den.get(src, 0),
                 pct=round(100 * em.get(src, 0) / den[src], 1) if den.get(src) else 0.0) for src, lab in _SRC]
    pet = [d for d in docs if d.get("source") == "FDA Warning Letter"
           and any("21 CFR 212" in r for f in d.get("findings") or [] for r in f.get("cfr_refs") or [])
           and not (tgt and tgt in str(d.get("firm_name") or "").lower())]
    return dict(measured_on=findings.get("measured_on") or "", em_rows=rows,
                wl_total=den.get("FDA Warning Letter", 0), wl_iso5=iso.get("FDA Warning Letter", 0),
                b483_total=den.get("FDA 483", 0), b483_iso5=iso.get("FDA 483", 0),
                pet_other=len(pet), pet_other_dates=sorted(str(d.get("published_date")) for d in pet))


def db_numbers(db: dict) -> set[str]:
    out: set[str] = set()
    for r in db["em_rows"]:
        out |= la._nums(f"{r['n']} {r['total']} {r['pct']}")
    out |= la._nums(f"{db['wl_total']} {db['wl_iso5']} {db['b483_total']} {db['b483_iso5']} {db['pet_other']} "
                    f"{db['measured_on']} {' '.join(db['pet_other_dates'])}")
    return out


def peer_doc(findings: dict, slug: str) -> dict | None:
    return next((d for d in findings.get("documents") or [] if str(d.get("slug")) == slug), None)


# ── 검증 ─────────────────────────────────────────────────────────────────────────────────────

def validate_v2(card: dict, slots: dict, findings: dict, glossary: list[dict]) -> list[str]:
    probs: list[str] = []
    src = la.source_text(card)
    db = db_position(findings, card)
    allowed_global = la._nums(src) | db_numbers(db)
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
    texts += [(f"s02.quotes[{i}].note", q.get("note") or "") for i, q in enumerate(s2.get("quotes") or [])]
    texts += [(f"s03.line[{i}]", x) for i, x in enumerate(s3.get("lines") or [])]
    texts += [(f"s03.peer.note", (s3.get("peer") or {}).get("note") or "")]
    for i, p in enumerate(s4.get("pairs") or []):
        texts += [(f"s04.pairs[{i}].response", p.get("response") or ""), (f"s04.pairs[{i}].judgment", p.get("judgment") or "")]
    for i, it in enumerate(s5.get("items") or []):
        texts += [(f"s05.items[{i}].title", it.get("title") or ""), (f"s05.items[{i}].text", it.get("text") or "")]
    texts += [(f"s06.not_stated[{i}]", x.get("text") or "") for i, x in enumerate(s6.get("not_stated") or [])]
    texts += [("s06.question", s6.get("question") or "")]
    cap = slots.get("caption") or {}
    texts += [(f"caption.{k}", cap.get(k) or "") for k in ("lead", "db", "contrast", "not_stated")]
    for path, t in texts:
        m = la._AI_TELL_RE.search(t or "")
        if m:
            probs.append(f"{path}: AI 티 표현 '{m.group(0)}'")
        extra = la._nums(t) - allowed_global
        if extra:
            probs.append(f"{path}: 원천·DB 에 없는 숫자 {sorted(extra)}")

    # 점 그림: 분모·분자는 근거 안의 숫자
    dots = cv.get("dots") or {}
    evs = la._ev_list(dots.get("evidence"))
    for e in evs:
        if e not in src:
            probs.append(f"cover.dots: 근거가 원천에 없다 — {e[:40]}…")
    if not evs or not {str(dots.get("total")), str(dots.get("marked"))} <= la._nums(" ".join(evs)):
        probs.append("cover.dots: total·marked 가 근거 안의 숫자여야 한다")
    if not (isinstance(dots.get("total"), int) and 1 <= dots["total"] <= 100 and 0 <= dots.get("marked", -1) <= dots["total"]):
        probs.append("cover.dots: total 1~100, marked ≤ total")
    for e in la._ev_list(cv.get("line_evidence")):
        if e not in src:
            probs.append(f"cover.line_evidence: 근거가 원천에 없다 — {e[:40]}…")

    # 원문 대조: 영어·국문 모두 원천에 그대로
    qs = s2.get("quotes") or []
    if len(qs) != 3:
        probs.append("s02.quotes 는 3개")
    for i, q in enumerate(qs):
        for k in ("en", "ko"):
            if la._norm(q.get(k)) not in src or not la._norm(q.get(k)):
                probs.append(f"s02.quotes[{i}].{k}: 원천에 그대로 있어야 한다(verbatim)")

    # DB 칸: 줄 수와 동료 사례(483) 근거
    if len(s3.get("lines") or []) not in (2, 3):
        probs.append("s03.lines 는 2~3개")
    peer = s3.get("peer") or {}
    pd = peer_doc(findings, str(peer.get("slug") or ""))
    if not pd:
        probs.append("s03.peer.slug 가 DB 에 없다")
    else:
        blob = la._norm(" ".join(f.get("text_orig") or "" for f in pd.get("findings") or []))
        if la._norm(peer.get("en")) not in blob or not la._norm(peer.get("en")):
            probs.append("s03.peer.en: DB 원문에 그대로 있어야 한다")
        blob_ko = la._norm(" ".join(f.get("text_ko") or "" for f in pd.get("findings") or []))
        if la._norm(peer.get("ko")) not in blob_ko or not la._norm(peer.get("ko")):
            probs.append("s03.peer.ko: DB 번역문에 그대로 있어야 한다")

    # 회신/판단, 요구
    if len(s4.get("pairs") or []) not in (2, 3):
        probs.append("s04.pairs 는 2~3개")
    for i, p in enumerate(s4.get("pairs") or []):
        ev = la._ev_list(p.get("evidence"))
        if not ev or any(e not in src for e in ev):
            probs.append(f"s04.pairs[{i}]: 근거가 원천에 없다")
        elif la._nums(p.get("response", "") + p.get("judgment", "")) - la._nums(" ".join(ev)):
            probs.append(f"s04.pairs[{i}]: 근거에 없는 숫자")
    refs = sorted(it.get("ref") for it in s5.get("items") or [] if isinstance(it.get("ref"), int))
    if refs != list(range(min(len(items), 4))):
        probs.append(f"s05.items 가 서한의 FDA 요구 {min(len(items), 4)}개를 모두 다뤄야 한다(ref {refs})")

    # 서한에 없는 말·질문·용어
    ns = s6.get("not_stated") or []
    if len(ns) not in (2, 3) or any(not x.get("basis") for x in ns):
        probs.append("s06.not_stated 는 2~3개이고 각각 basis(무엇을 보고 없다고 했는지)가 있어야 한다")
    if not la._norm(s6.get("question")).endswith("?"):
        probs.append("s06.question 은 질문형(?)이어야 한다")
    ids = {t["id"] for t in glossary}
    for tid in s6.get("terms") or []:
        if tid not in ids:
            probs.append(f"s06.terms: 용어사전에 없는 id {tid}")

    # 업체 익명
    firm_tokens = [t for t in re.split(r"[\s,.()]+", str(card.get("headline_target") or ""))
                   if len(t) >= 4 and t.lower() not in ("inc", "llc", "ltd", "corp", "company", "co")]
    blob = json.dumps(slots, ensure_ascii=False).lower()
    probs += [f"업체 실명 노출: {t}" for t in firm_tokens if t.lower() in blob]

    # 글자 수
    def w(label: str, text: str, limit: float) -> None:
        if lc.text_width(text) > limit:
            probs.append(f"{label}: 너무 길다({lc.text_width(text):.0f} > {limit:g})")
    for i, x in enumerate(cv.get("headline") or []):
        w(f"cover.headline[{i}]", x, LIMITS["headline_line"])
    for i, q in enumerate(qs):
        w(f"s02.quotes[{i}].note", q.get("note") or "", LIMITS["quote_note"])
    for i, x in enumerate(s3.get("lines") or []):
        w(f"s03.lines[{i}]", x, LIMITS["db_line"])
    for i, p in enumerate(s4.get("pairs") or []):
        w(f"s04.pairs[{i}].response", p.get("response") or "", LIMITS["pair_resp"])
        w(f"s04.pairs[{i}].judgment", p.get("judgment") or "", LIMITS["pair_judg"])
    for i, it in enumerate(s5.get("items") or []):
        w(f"s05.items[{i}].title", it.get("title") or "", LIMITS["item_title"])
        w(f"s05.items[{i}].text", it.get("text") or "", LIMITS["item_text"])
    for i, x in enumerate(ns):
        w(f"s06.not_stated[{i}]", x.get("text") or "", LIMITS["ns"])
    w("s06.question", s6.get("question") or "", LIMITS["question"])
    return probs


# ── 덱 ───────────────────────────────────────────────────────────────────────────────────────

def build_grm_deck(brief_doc: dict, slots: dict, findings: dict, glossary: list[dict],
                   *, base_url: str = lc.SITE_BASE_URL) -> dict[str, Any]:
    card = la.pick_anatomy_card(brief_doc, str(slots.get("card_id") or ""))
    if card is None:
        raise la.AnatomyError(["card_id 에 맞는 해부 가능한 경고서한이 브리프에 없다"])
    probs = validate_v2(card, slots, findings, glossary)
    if probs:
        raise la.AnatomyError(probs)
    db = db_position(findings, card)
    brief = brief_doc.get("brief") or {}
    pub = str(brief.get("publish_date") or slots.get("pub") or "")
    y, m, wk = lc.title_dateform(pub)
    issued = la._issue_date(card)
    cv, s2, s3, s4, s5, s6 = (slots[k] for k in ("cover", "s02", "s03", "s04", "s05", "s06"))
    items = (card["deep_analysis"].get("required_remediation") or {}).get("items") or []
    terms = {t["id"]: t for t in glossary}
    chips = [(terms[i]["term_ko"], f"{base_url}/glossary/{i}/") for i in s6.get("terms") or []]
    src_note = f"원문: FDA 경고서한{f' ({issued})' if issued else ''} · 업체명 비공개"
    ai = lc.AI_NOTE
    slides = [
        dict(kind="g_cover", chip=cv["chip"], h1=list(cv["headline"]), sub=cv["sub"], dots=dict(cv["dots"]),
             line=cv.get("line") or "", ft_right=src_note, ai=ai),
        dict(kind="g_orig", corner="원문 대조", heading=s2["heading"], quotes=s2["quotes"], ft_right=src_note, ai=ai),
        dict(kind="g_db", corner="DB로 본 자리", heading=s3["heading"], db=db, lines=list(s3["lines"]), peer=s3["peer"],
             ft_right=f"지적사항 DB {db['measured_on']} 기준 · 직접 센 숫자", ai=ai),
        dict(kind="g_vs", corner="회신과 판단", heading=s4["heading"],
             pairs=[(p["response"], p["judgment"]) for p in s4["pairs"]], ft_right=src_note, ai=ai),
        dict(kind="g_req", corner="FDA가 요구한 것", heading=s5["heading"], sub=f"서한이 요구한 항목 {len(items)}건",
             items=[(it["title"], it["text"]) for it in s5["items"]], deadline=la._deadline(card), ft_right=src_note, ai=ai),
        dict(kind="g_end", corner="서한에 없는 말", heading=s6["heading"], not_stated=s6["not_stated"],
             question=s6["question"], chips=chips, ft_right="원문 링크는 브리프 페이지에서 확인", ai=ai),
    ]
    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)
    url = f"{base_url}/briefs/{pub}/"
    cap = slots["caption"]
    caption_url = lc.with_utm(url, *lc.LINKEDIN_WEEKLY, lc.linkedin_weekly_campaign(pub))
    ko = "\n\n".join(la._norm(cap[k]) for k in ("lead", "db", "contrast", "not_stated"))
    glossary_line = "용어사전: " + " · ".join(f"{n} {u}" for n, u in chips) if chips else ""
    parts = [ko, f"전체 해설: {caption_url}", glossary_line]
    return {"pub": pub, "lang": "ko", "slides": slides, "caption": "\n\n".join(p for p in parts if p) + "\n",
            "doc_title": f"GRM {m}월 {wk}주 경고서한 해부(GRM 판)", "url": url, "caption_url": caption_url,
            "card_id": str(card.get("id")), "mode": "grm"}


# ── 렌더 ─────────────────────────────────────────────────────────────────────────────────────

CSS = """
.g-page .main{justify-content:flex-start;padding-top:40px}
.g-key{display:flex;gap:12px;align-items:center;margin-bottom:26px;font-size:19px;font-weight:700}
.g-key span{padding:5px 14px;border-radius:7px}
.g-key .o{background:#1A1815;color:#FAF9F5}.g-key .h{background:#EFE9DE;color:#3D3D3A;border:1.5px solid #D9CFC0}.g-key .n{background:#FBF1DD;color:#5A4210;border:1.5px solid #E9C98C}
.g-corner{align-self:flex-start;display:inline-block;padding:8px 22px;border-radius:9999px;background:#C2603F;color:#fff;font-size:26px;font-weight:800;margin-bottom:22px}
.g-h{font-size:46px;font-weight:800;line-height:1.28;letter-spacing:-.02em;word-break:keep-all;margin:0 0 26px}
.g-cover h1{font-size:92px;margin:6px 0 0}
.g-chip{align-self:flex-start;padding:9px 24px;border-radius:9999px;border:2.5px solid #1A1815;font-size:24px;font-weight:700;margin-bottom:36px}
.g-sub{margin:28px 0 0;font-size:30px;color:#3D3D3A}
.g-dots{margin-top:52px}
.g-grid{display:grid;grid-template-columns:repeat(15,1fr);gap:14px 14px;max-width:940px}
.g-grid i{display:block;aspect-ratio:1;border-radius:50%;border:3px solid #B8AFA0;background:transparent}
.g-grid i.m{background:#C2603F;border-color:#C2603F;box-shadow:0 0 0 7px rgba(194,96,63,.22)}
.g-dcap{margin-top:30px;font-size:31px;font-weight:700;line-height:1.4;word-break:keep-all}.g-dcap b{color:#C2603F;font-size:44px}
.g-line{margin-top:16px;font-size:25px;color:#6C6A64;word-break:keep-all}
.g-q{margin-bottom:18px}
.g-q .en{background:#1A1815;color:#FAF9F5;border-radius:12px 12px 0 0;padding:14px 24px;font-size:20px;line-height:1.5;font-style:italic}
.g-q .ko{background:#FFFDF9;border:2px solid #1A1815;border-top:0;padding:12px 24px;font-size:23px;line-height:1.5;word-break:keep-all}
.g-q .hae{background:#EFE9DE;border:1.5px solid #D9CFC0;border-top:0;border-radius:0 0 12px 12px;padding:8px 24px;font-size:22px;font-weight:700;color:#A14B30;word-break:keep-all}
.g-dbbar{display:grid;grid-template-columns:170px 1fr 120px;gap:16px;align-items:center;margin-bottom:14px;font-size:24px}
.g-dbbar .t{height:30px;background:#EFE9DE;border-radius:6px;position:relative}.g-dbbar .t span{position:absolute;left:0;top:0;bottom:0;background:#C2603F;border-radius:6px}
.g-dbbar b{font-size:26px;font-variant-numeric:tabular-nums;text-align:right}.g-dbbar small{display:block;font-size:17px;color:#6C6A64;font-weight:400}
.g-dbnote{margin:6px 0 22px;font-size:20px;color:#6C6A64}
.g-ln{font-size:27px;line-height:1.5;margin-bottom:12px;word-break:keep-all;padding-left:18px;border-left:6px solid #C2603F}
.g-peer{margin-top:22px;border:2px dashed #8A8478;border-radius:12px;padding:18px 24px}
.g-peer small{font-size:19px;font-weight:800;color:#6C6A64;display:block;margin-bottom:8px}
.g-peer .en{font-size:19px;font-style:italic;line-height:1.5;color:#3D3D3A}.g-peer .ko{font-size:21px;line-height:1.5;margin-top:6px;word-break:keep-all}
.g-pr{display:flex;flex-direction:column;gap:28px}
.g-p{display:grid;grid-template-columns:1fr 1.5fr;gap:0;border-radius:12px;overflow:hidden}
.g-p div{padding:30px 28px;font-size:28px;line-height:1.5;word-break:keep-all}
.g-p .r{background:#EFE9DE}.g-p .j{background:#1A1815;color:#FAF9F5}.g-p small{display:block;font-size:17px;font-weight:800;opacity:.75;margin-bottom:6px}
.g-req{display:flex;flex-direction:column;gap:0;border-top:3px solid #141413}
.g-r{display:grid;grid-template-columns:100px 250px 1fr;gap:18px;padding:34px 4px;border-bottom:1.5px solid #D9CFC0;align-items:baseline}
.g-r em{font-style:normal;font-weight:800;color:#C2603F;font-size:23px}.g-r b{font-size:30px;word-break:keep-all}.g-r span{font-size:26px;line-height:1.5;color:#3D3D3A;word-break:keep-all}
.g-dl{margin-top:26px;font-size:26px;font-weight:800;color:#A14B30}
.g-ns{background:#FBF1DD;border:2px solid #E9C98C;border-radius:12px;padding:28px 30px;margin-bottom:22px;font-size:31px;line-height:1.5;word-break:keep-all;color:#3F2F0B}
.g-ns small{display:block;font-size:21px;font-weight:700;margin-top:8px;color:#7A6120}
.g-qq{margin-top:22px;border:3px solid #1A1815;border-radius:14px;padding:26px 30px;font-size:34px;font-weight:800;line-height:1.4;word-break:keep-all}
.g-qq small{display:block;font-size:19px;font-weight:700;margin-bottom:8px;color:#6C6A64}
.g-chips{margin-top:22px;font-size:20px;color:#6C6A64;line-height:1.7}.g-chips b{color:#1A1815}
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
        p.append(f'<p class="g-sub">{_e(s["sub"])}</p>')
        p.append(f'<div class="g-dots"><div class="g-grid">{grid}</div>'
                 f'<div class="g-dcap">{_e(d["caption"])}</div>'
                 + (f'<div class="g-line">{_e(s["line"])}</div>' if s.get("line") else "") + '</div>')
    else:
        p.append(_KEY)
        p.append(f'<span class="g-corner">{_e(s["corner"])}</span>')
        p.append(f'<h2 class="g-h">{_e(s["heading"])}</h2>')
        if k == "g_orig":
            for q in s["quotes"]:
                p.append(f'<div class="g-q"><div class="en">{_e(q["en"])}</div><div class="ko">{_e(q["ko"])}</div>'
                         f'<div class="hae">{_e(q["note"])}</div></div>')
        elif k == "g_db":
            db = s["db"]
            mx = max(r["pct"] for r in db["em_rows"]) or 1
            for r in db["em_rows"]:
                p.append(f'<div class="g-dbbar"><div>{_e(r["label"])}<small>{r["n"]} / {r["total"]:,}건</small></div>'
                         f'<div class="t"><span style="width:{int(100 * r["pct"] / mx)}%"></span></div><b>{r["pct"]}%</b></div>')
            p.append('<div class="g-dbnote">‘환경모니터링’ 분류가 붙은 문서의 비율 (지적사항 DB)</div>')
            p.extend(f'<div class="g-ln">{_e(x)}</div>' for x in s["lines"])
            pr = s["peer"]
            p.append(f'<div class="g-peer"><small>{_e(pr["label"])}</small><div class="en">{_e(pr["en"])}</div>'
                     f'<div class="ko">{_e(pr["ko"])}</div></div>')
        elif k == "g_vs":
            p.append('<div class="g-pr">' + "".join(
                f'<div class="g-p"><div class="r"><small>회사 회신</small>{_e(r)}</div>'
                f'<div class="j"><small>FDA 판단: 부적절</small>{_e(j)}</div></div>' for r, j in s["pairs"]) + "</div>")
        elif k == "g_req":
            p.append(f'<div class="g-req">' + "".join(
                f'<div class="g-r"><em>요구 {n}/{len(s["items"])}</em><b>{_e(t)}</b><span>{_e(x)}</span></div>'
                for n, (t, x) in enumerate(s["items"], 1)) + "</div>")
            if s.get("deadline"):
                p.append(f'<div class="g-dl">회신 기한: {_e(s["deadline"])}</div>')
        elif k == "g_end":
            for x in s["not_stated"]:
                p.append(f'<div class="g-ns">{_e(x["text"])}<small>근거: {_e(x["basis"])}</small></div>')
            p.append(f'<div class="g-qq"><small>우리 현장에 가져갈 질문 1개</small>{_e(s["question"])}</div>')
            if s.get("chips"):
                p.append('<div class="g-chips">용어사전 · ' + " · ".join(f"<b>{_e(n)}</b>" for n, _ in s["chips"]) +
                         " → grm-solutions.com/glossary/</div>")
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
    ap = argparse.ArgumentParser(description="링크드인 사건 해부 GRM 판(시범 v2)")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--slots", help="v2 슬롯 JSON(기본: web/data/linkedin_anatomy/<발행일>.v2.json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args(argv)
    doc = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    pub = str((doc.get("brief") or {}).get("publish_date") or "")
    sp = Path(args.slots) if args.slots else SLOTS_DIR / f"{pub}.v2.json"
    if not sp.exists():
        print(f"::warning::v2 슬롯 없음({sp.name}) — 만들지 않는다", file=sys.stderr)
        return 0
    slots = json.loads(sp.read_text(encoding="utf-8"))
    findings = json.loads(FINDINGS.read_text(encoding="utf-8"))
    glossary = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    try:
        deck = build_grm_deck(doc, slots, findings, glossary)
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
        names: list[str] = []
        for i, slide in enumerate(deck["slides"], 1):
            tmp = out / f"linkedin_grm_{i:02d}.tmp.html"
            tmp.write_text(render_html(dict(deck, slides=[slide])), encoding="utf-8")
            try:
                lc.render_png(tmp, out / f"linkedin_grm_{i:02d}.png", chrome)
                names.append(f"linkedin_grm_{i:02d}.png")
            finally:
                tmp.unlink(missing_ok=True)
        n = len(names)
    print(f"linkedin_grm: {pub} · {len(deck['slides'])}장 · 본문 {len(deck['caption'])}자 · PNG {n} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
