#!/usr/bin/env python3
"""링크드인 사건 해부 GRM 판 — 영문판(2026-11-16, 대표 지시 "영문도 해부판, 11-23 호부터 매주").

국문판(`linkedin_grm.py`)과 같은 서한·같은 7장 구성·같은 DB 숫자를 영어로 낸다. 다른 점은 근거의 원천이다.
  · 근거(evidence·quote)는 **FDA 영문 원문 verbatim** — 카드의 위반 `original` + 서한 전문
    `web/data/linkedin_anatomy/<발행일>.letter_en.txt`(handoff 의 `deep_analysis_input.body_full`, FDA 공문=공공 저작물).
    카드에는 회신 평가·요구 사항의 영어 원문이 없어 전문이 필요하다.
  · 그림 속 고정 문구(코너·범례·고지)는 영어, **한글 0자**(코드가 렌더 결과로 검사한다).
  · 본문 배치는 국문과 같다(첫 단락 → ■ 소제목, 한 줄 한 문장, 주소는 자기 줄), 주소는 `/en/` 경로.
  · 업체명은 익명(Firm A·B…), 유사 사례 문장은 DB 의 영어 원문(`text_orig`)에서 자른다.
슬롯: `<발행일>.v3.json` 의 `en` 블록. 유료 API 호출 코드는 없다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import linkedin_anatomy as la
import linkedin_cards as lc
import linkedin_grm as lg
import utm

_HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
# 영어 AI 티 — 사람이 쓴 실무 글에서 잘 안 나오는 상투어(STYLE §8 의 영어판). 슬롯 문장에만 적용(근거 인용 제외).
AI_TELLS_EN = [r"\bunderscor", r"\bhighlights? the (?:importance|need)", r"\bcrucial\b", r"\bpivotal\b", r"\bdelve",
               r"\bmoreover\b", r"\bfurthermore\b", r"\bin today's\b", r"\bit is (?:important|essential|worth noting)",
               r"\bserves? as a (?:reminder|wake-up)", r"\bnotably\b", r"\bkey takeaway", r"\bgame[- ]changer",
               r"\bnavigat(?:e|ing) the", r"\blandscape\b", r"\bpaves? the way", r"\bshed(?:s)? light"]
_AI_EN = re.compile("|".join(AI_TELLS_EN), re.I)
_WEAK_START_EN = re.compile(r"^(?:FDA\b|The FDA\b|U\.S\. FDA\b|A |An |One |This week\b|Last week\b|Recently\b)")
_LABEL_END_EN = re.compile(r"(?:deficienc(?:y|ies)|failures?|violations?|issues?|lapses?|concerns?|problems?)[.!]?$", re.I)
LEAD_FIRST_EN = 40.0  # text_width — 영어는 한 자가 약 0.52 이라 약 75자(모바일 두 줄)

SITE_LEVELS_EN = {"fact": "Fact match", "situation": "Situation match", "topic": "Topic overlap"}
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def letter_path(pub: str, slots_dir: Path | None = None) -> Path:
    return (slots_dir or lg.SLOTS_DIR) / f"{pub}.letter_en.txt"


def load_letter(pub: str, slots_dir: Path | None = None) -> str:
    try:
        return letter_path(pub, slots_dir).read_text(encoding="utf-8")
    except OSError:
        return ""


def letter_from_handoff(handoff: dict, card_id: str) -> str:
    """handoff 의 `deep_analysis_input.body_full`(수집기가 받아 둔 서한 전문)에서 서명 앞까지 — 서명·참조인(개인 이름)과
    FDA 페이지 바닥글은 공개 저장소에 두지 않는다. 사람이 옮겨 적지 않는다(전사 금지)."""
    rows = handoff.get("rows") if isinstance(handoff, dict) else handoff
    row = next((r for r in rows or [] if str(r.get("web_card_id") or "") == card_id), None)
    body = str(((row or {}).get("deep_analysis_input") or {}).get("body_full") or "")
    i = body.find(" Sincerely,")
    return (body[:i] if i > 0 else body).strip()


def en_source(card: dict, letter: str) -> str:
    da = card.get("deep_analysis") or {}
    parts = [str(v.get("original") or "") for v in da.get("key_violations") or []] + [letter or ""]
    return la._norm(" ".join(parts))


def issue_date_en(card: dict) -> str:
    for f in card.get("facts") or []:
        if isinstance(f, dict) and "일자" in str(f.get("label") or ""):
            m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(f.get("value") or ""))
            if m:
                return f"{_MONTHS[int(m.group(1)) - 1]} {int(m.group(2))}, {m.group(3)}"
    return ""


def deadline_en(card: dict) -> str:
    dl = str(((card.get("deep_analysis") or {}).get("required_remediation") or {}).get("deadline") or "")
    m = re.search(r"(\d+)\s*영업일", dl)
    return f"written reply within {m.group(1)} business days of receipt" if m else ""


def first_sentence_en(text: Any) -> str:
    return lc.first_sentence(la._norm(text), "en")


def hook_problems_en(en: dict, card: dict) -> list[str]:
    out: list[str] = []
    lead1 = first_sentence_en((en.get("caption") or {}).get("lead"))
    if lc.text_width(lead1) > LEAD_FIRST_EN:
        out.append(f"en.caption.lead first sentence is past the mobile fold ({lc.text_width(lead1):.0f} > {LEAD_FIRST_EN:g}): {lead1[:40]}…")
    if _WEAK_START_EN.search(lead1):
        out.append("en.caption.lead: start with the fact, not 'FDA …' / 'A firm …' — who it is goes in the second or third sentence")
    if not (re.search(r"\d", lead1) or re.search(r"[“‘\"'][^”’\"']{2,}[”’\"']", lead1)):
        out.append("en.caption.lead first sentence needs a number or a quoted fragment of the letter")
    h = [la._norm(x) for x in (en.get("cover") or {}).get("headline") or []]
    if len(h) != 2:
        out.append("en.cover.headline must be two lines")
    elif _LABEL_END_EN.search(h[-1]):
        out.append(f"en.cover.headline ends with a label ('{h[-1]}') — say what happened")
    if h and re.sub(r"\W", "", " ".join(h)).lower() == re.sub(r"\W", "", str((card.get("en") or {}).get("title_issue") or "")).lower():
        out.append("en.cover.headline repeats the card title")
    return out


def _texts(en: dict) -> list[tuple[str, str]]:
    cv, s2, s3, s4, s5, s6 = (en.get(k) or {} for k in ("cover", "s02", "s03", "s04", "s05", "s06"))
    t: list[tuple[str, str]] = [(f"en.cover.headline[{i}]", x) for i, x in enumerate(cv.get("headline") or [])]
    t += [("en.cover.sub", cv.get("sub") or ""), ("en.cover.line", cv.get("line") or ""), ("en.cover.chip", cv.get("chip") or ""),
          ("en.cover.dots.caption", (cv.get("dots") or {}).get("caption") or "")]
    t += [(f"en.s02.rows[{i}]", r.get("text") or "") for i, r in enumerate(s2.get("rows") or [])]
    t += [(f"en.s02.rows[{i}].short", r.get("short") or "") for i, r in enumerate(s2.get("rows") or [])]
    t += [("en.s02.heading", s2.get("heading") or ""), ("en.s03.heading", s3.get("heading") or ""), ("en.s03.sub", s3.get("sub") or "")]
    for i, p in enumerate(s3.get("pairs") or []):
        t += [(f"en.s03.pairs[{i}].response", p.get("response") or ""), (f"en.s03.pairs[{i}].why", p.get("why") or "")]
    if s3.get("repeat"):
        t += [("en.s03.repeat", s3["repeat"].get("text") or ""), ("en.s03.repeat.label", s3["repeat"].get("label") or "")]
    t += [("en.s04.heading", s4.get("heading") or "")]
    for i, it in enumerate(s4.get("items") or []):
        t += [(f"en.s04.items[{i}].title", it.get("title") or ""), (f"en.s04.items[{i}].text", it.get("text") or "")]
    t += [("en.s05.heading", s5.get("heading") or ""), ("en.s05.comment", s5.get("comment") or ""),
          ("en.s05.topic_label", s5.get("topic_label") or "")]
    t += [(f"en.s05.cfr_labels[{i}]", x) for i, x in enumerate(s5.get("cfr_labels") or [])]
    t += [("en.s06.heading", s6.get("heading") or ""), ("en.s06.question", s6.get("question") or "")]
    t += [(f"en.s06.not_stated[{i}]", x.get("text") or "") for i, x in enumerate(s6.get("not_stated") or [])]
    cap = en.get("caption") or {}
    t += [(f"en.caption.{k}", cap.get(k) or "") for k in ("lead", "claims", "contrast", "requests", "similar", "not_stated")]
    return t


def validate_en(card: dict, slots: dict, findings: dict, letter: str) -> list[str]:
    en = slots.get("en") or {}
    if not en:
        return ["en 블록 없음"]
    probs: list[str] = []
    if not (letter or "").strip():
        probs.append(f"en: 서한 영문 전문(<발행일>.letter_en.txt)이 없다 — 근거를 대조할 수 없다")
    src = en_source(card, letter)
    db = lg.db_for(findings, card, slots)
    allowed = la._nums(src) | lg.db_numbers(db)
    blob = json.dumps(en, ensure_ascii=False)
    if _HANGUL.search(blob):
        bad = sorted({p for p, x in _texts(en) if _HANGUL.search(x)})
        probs.append(f"en: 한글이 있다 {bad[:6] or '(근거·기타 칸)'}")
    for path, t in _texts(en):
        m = _AI_EN.search(t or "")
        if m:
            probs.append(f"{path}: AI-sounding phrase '{m.group(0)}'")
        extra = la._nums(t) - allowed
        if extra:
            probs.append(f"{path}: numbers not in the letter or DB {sorted(extra)}")

    def _ev_ok(path: str, ev: Any, text: str = "") -> None:
        evs = la._ev_list(ev)
        if not evs:
            probs.append(f"{path}: no evidence")
            return
        for e in evs:
            if e not in src:
                probs.append(f"{path}: evidence not verbatim in the FDA letter — {e[:50]}…")
        if text and la._nums(text) - la._nums(" ".join(evs)):
            probs.append(f"{path}: numbers not in its evidence {sorted(la._nums(text) - la._nums(' '.join(evs)))}")

    cv, s2, s3, s4, s5, s6 = (en.get(k) or {} for k in ("cover", "s02", "s03", "s04", "s05", "s06"))
    dots, quote = cv.get("dots"), cv.get("quote")
    if bool(dots) == bool(quote):
        probs.append("en.cover: exactly one of dots or quote")
    if dots:
        _ev_ok("en.cover.dots", dots.get("evidence"))
        if not {str(dots.get("total")), str(dots.get("marked"))} <= la._nums(" ".join(la._ev_list(dots.get("evidence")))):
            probs.append("en.cover.dots: total/marked must be numbers in the evidence")
    if quote:
        q = la._norm(quote.get("text"))
        if not q or q not in src:
            probs.append(f"en.cover.quote.text not verbatim in the FDA letter — {q[:50]}…")
        if len(q) > lg.LIMITS["quote_en"]:
            probs.append(f"en.cover.quote.text too long (> {lg.LIMITS['quote_en']})")
    for e in la._ev_list(cv.get("line_evidence")):
        if e not in src:
            probs.append(f"en.cover.line_evidence not verbatim — {e[:50]}…")

    ko_rows = ((slots.get("s02") or {}).get("rows") or [])
    rows = s2.get("rows") or []
    if len(rows) != len(ko_rows):
        probs.append(f"en.s02.rows must match the Korean rows ({len(ko_rows)})")
    for i, r in enumerate(rows):
        _ev_ok(f"en.s02.rows[{i}]", r.get("evidence"), r.get("text") or "")
        if not la._norm(r.get("cite")) or not la._norm(r.get("short")):
            probs.append(f"en.s02.rows[{i}]: cite and short are required")
    pairs = s3.get("pairs") or []
    if not 3 <= len(pairs) <= 5:
        probs.append("en.s03.pairs: 3 to 5")
    for i, p in enumerate(pairs):
        _ev_ok(f"en.s03.pairs[{i}]", p.get("evidence"), (p.get("response") or "") + " " + (p.get("why") or ""))
    if s3.get("repeat"):
        _ev_ok("en.s03.repeat", s3["repeat"].get("evidence"), s3["repeat"].get("text") or "")
    items = ((card.get("deep_analysis") or {}).get("required_remediation") or {}).get("items") or []
    refs = sorted(it.get("ref") for it in s4.get("items") or [] if isinstance(it.get("ref"), int))
    if refs != list(range(min(len(items), 4))):
        probs.append(f"en.s04.items must cover all {min(len(items), 4)} FDA requests (ref {refs})")
    for i, it in enumerate(s4.get("items") or []):
        if it.get("evidence"):
            _ev_ok(f"en.s04.items[{i}]", it.get("evidence"))
    if not la._norm(s5.get("topic_label")):
        probs.append("en.s05.topic_label required (English name of the DB tag)")
    if len(s5.get("cfr_labels") or []) != len(lg.s05_ctx(slots)["cfr_rows"]):
        probs.append("en.s05.cfr_labels must match s05.cfr_rows")
    ns = s6.get("not_stated") or []
    if len(ns) not in (2, 3) or any(not x.get("basis") for x in ns):
        probs.append("en.s06.not_stated: 2 or 3, each with a basis")
    if not la._norm(s6.get("question")).endswith("?"):
        probs.append("en.s06.question must be a question")
    for t in lg._firm_tokens(card.get("headline_target")):
        if t.lower() in blob.lower():
            probs.append(f"en: firm name leaked: {t}")
    probs += hook_problems_en(en, card)

    def w(label: str, text: str, limit: float) -> None:
        if lc.text_width(text) > limit:
            probs.append(f"{label}: too long ({lc.text_width(text):.0f} > {limit:g})")
    L = lg.LIMITS
    for i, x in enumerate(cv.get("headline") or []):
        w(f"en.cover.headline[{i}]", x, L["headline_line"])
    for i, r in enumerate(rows):
        w(f"en.s02.rows[{i}]", r.get("text") or "", L["row_text"])
    for i, p in enumerate(pairs):
        w(f"en.s03.pairs[{i}].response", p.get("response") or "", L["pair_resp"])
        w(f"en.s03.pairs[{i}].why", p.get("why") or "", L["pair_why"])
    for i, it in enumerate(s4.get("items") or []):
        w(f"en.s04.items[{i}].title", it.get("title") or "", L["item_title"])
        w(f"en.s04.items[{i}].text", it.get("text") or "", L["item_text"])
    w("en.s05.comment", s5.get("comment") or "", L["db_comment"])
    for i, x in enumerate(ns):
        w(f"en.s06.not_stated[{i}]", x.get("text") or "", L["ns"])
    w("en.s06.question", s6.get("question") or "", L["question"])
    return probs


# ── 덱 ───────────────────────────────────────────────────────────────────────────────────────

def _pick_fact_en(text: str) -> str:
    """DB 의 영어 원문 지적에서 숫자·기간이 든 문장을 먼저, 없으면 첫 문장. 길면 낱말 경계에서 자른다."""
    sents = [x for x in re.split(r"(?<=[a-z0-9)\]][.!?])\s+(?=[A-Z])", la._norm(text)) if x]
    sents = [re.sub(r"\s*\((?:21 CFR|section)[^)]*\)", "", x) for x in sents]
    pick = next((x for x in sents if re.search(r"\b\d+\s*(?:%|batches|lots|times|months|years|of)\b", x)), sents[0] if sents else "")
    if len(pick) <= 170:
        return pick
    cut = pick[:165].rsplit(" ", 1)[0]
    return re.sub(r"\s*\([^)]*$", "", cut).rstrip(" ,;") + "…"


def site_rows_en(similar: dict | None, findings: dict, en: dict) -> list[dict]:
    rows = lg.site_cases(similar, findings)
    docs = findings.get("documents") or []
    by_fid = {f.get("finding_id"): (d, f) for d in docs for f in d.get("findings") or []}
    shorts = [la._norm(r.get("short")) for r in (en.get("s02") or {}).get("rows") or []]
    out = []
    for r in rows:
        d, f = by_fid.get(r["finding_id"], (None, None))
        orig = la._norm((f or {}).get("text_orig"))
        if not d or not orig or _HANGUL.search(orig):
            continue  # 영어 원문이 없는 지적(식약처 등)은 영문판에 싣지 않는다
        mine = [x for x in docs if x.get("firm_key") == d.get("firm_key")]
        n483 = sum(1 for x in mine if x.get("source") == "FDA 483")
        wl = sorted(str(x["published_date"]) for x in mine if x.get("source") == "FDA Warning Letter")
        b4 = sorted(str(x["published_date"]) for x in mine if x.get("source") == "FDA 483")
        hist = f"This firm in our DB: {n483} Form 483{'s' if n483 != 1 else ''} · {len(wl)} warning letter{'s' if len(wl) != 1 else ''}"
        if wl and b4 and b4[0] < wl[0]:
            hist += " (483 posted before the letter)"
        out.append(dict(r, alias=f"Firm {'ABCDEFG'[len(out)]}", label=SITE_LEVELS_EN[r["level"]],
                        agency="warning letter" if r["agency"] == "경고서한" else "483",
                        target=shorts[r["q"] - 1] if r["q"] <= len(shorts) else "", fact=_pick_fact_en(orig), history=hist))
    return out


def db_lines_en(db: dict, topic: str, polite: bool = False) -> list[str]:
    yrs = sorted(db["em_years"])
    span = f"{yrs[0]}–{yrs[-1]}" if yrs else ""
    if polite:
        return [f"In our database, {db['em_n']} warning letters ({span}) to {db['em_firms']} firms carry the ‘{topic}’ tag.",
                f"Of those firms, {db['em_483_before']} had a 483 posted before the letter and {db['em_repeat']} received two or more letters (matched by firm name, so these are minimums)."]
    return [f"Tag count: {db['em_n']} ‘{topic}’ warning letters ({span}), {db['em_firms']} firms.",
            f"{db['em_483_before']} had a 483 posted first; {db['em_repeat']} got two or more letters. Firm-name match, so minimums."]


def site_lines_en(rows: list[dict]) -> list[str]:
    return [f"{r['alias']} · {r['year']} FDA {r['agency']} · {r['label']}\n‘{r['target']}’ — {r['fact']}\ngrm-solutions.com/en/findings/doc/{r['slug']}/"
            for r in rows if r["slug"]]


def build_en_deck(brief_doc: dict, slots: dict, findings: dict, letter: str, glossary: list[dict] | None = None,
                  *, base_url: str = lc.SITE_BASE_URL, similar: dict | None = None) -> dict[str, Any]:
    card = la.pick_anatomy_card(brief_doc, str(slots.get("card_id") or ""))
    if card is None:
        raise la.AnatomyError(["card_id 에 맞는 해부 가능한 경고서한이 브리프에 없다"])
    probs = validate_en(card, slots, findings, letter)
    if probs:
        raise la.AnatomyError(probs)
    en = slots["en"]
    ctx = lg.s05_ctx(slots)
    db = lg.db_for(findings, card, slots)
    db["cfr_rows"] = [dict(r, label=lbl) for r, lbl in zip(db["cfr_rows"], en["s05"]["cfr_labels"])]
    pub = str((brief_doc.get("brief") or {}).get("publish_date") or slots.get("pub") or "")
    if similar is None:
        try:
            similar = json.loads((lg.SLOTS_DIR / f"{pub}.similar.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            similar = None
    site = site_rows_en(similar, findings, en)
    db["site"] = site
    topic = la._norm(en["s05"]["topic_label"])
    issued = issue_date_en(card)
    src_note = f"Source: FDA warning letter{f' ({issued})' if issued else ''} · firm not named"
    ai = lc.STR["en"]["ai_note"]
    by = {g.get("id"): g.get("term_en") for g in (glossary if glossary is not None else lg._load_glossary())}
    terms = [(by[i], i) for i in ctx["term_ids"] if by.get(i)]
    cv, s2, s3, s4, s5, s6 = (en[k] for k in ("cover", "s02", "s03", "s04", "s05", "s06"))
    items = ((card["deep_analysis"].get("required_remediation") or {}).get("items")) or []
    rep = s3.get("repeat") or {}
    slides = [
        dict(kind="g_cover", chip=cv["chip"], h1=list(cv["headline"]), sub=cv["sub"], dots=dict(cv.get("dots") or {}),
             quote=dict(en=(cv.get("quote") or {}).get("text") or ""), line=cv.get("line") or "", ft_right=src_note, ai=ai),
        dict(kind="g_find", corner="What FDA cited", heading=s2["heading"], rows=s2["rows"], ft_right=src_note, ai=ai),
        dict(kind="g_reply", corner="Response review", heading=s3["heading"], sub=s3["sub"], pairs=s3["pairs"],
             repeat=rep.get("text") or "", repeat_label=rep.get("label") or "Repeat violation", ft_right=src_note, ai=ai),
        dict(kind="g_req", corner="Required actions", heading=s4["heading"], sub=f"{len(items)} requests in the letter",
             items=[(it["title"], it["text"]) for it in s4["items"]], deadline=deadline_en(card), ft_right=src_note, ai=ai),
        *([dict(kind="g_site", corner="Similar cases", heading=s5["heading"], rows=site,
                ft_right="GRM similar-case search · match labels by code rules", ai=ai)] if site else []),
        dict(kind="g_sim", corner="Database view" if site else "Similar cases",
             heading="Repeat and prior findings in our database", db=dict(db, cases=[]), lines=db_lines_en(db, topic),
             comment=s5["comment"], terms=terms, keywords=ctx["keywords"], cfr_rows=db["cfr_rows"],
             map_header=en["s05"].get("map_header") or "Sections this letter cites",
             ft_right=f"GRM findings DB as of {db['measured_on']} · counted directly", ai=ai),
        dict(kind="g_end", corner="Not in the letter", heading=s6["heading"], not_stated=s6["not_stated"],
             question=s6["question"], ft_right="Original letter linked from the brief page", ai=ai),
    ]
    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)
    url = f"{base_url}/en/briefs/{pub}/"
    caption_url = lc.with_utm(url, *lc.LINKEDIN_WEEKLY, lc.linkedin_weekly_campaign(pub))
    cap = en["caption"]
    cases = site_lines_en(site)
    similar_block = [la._norm(cap["similar"]), "", *("\n\n".join(cases).split("\n") if cases else []),
                     "", *db_lines_en(db, topic, polite=True)]
    while similar_block and similar_block[-1] == "":
        similar_block.pop()
    text = lg.caption_layout(cap["lead"], cap["claims"], cap["contrast"], cap["requests"], similar_block,
                             [n for n, _ in terms], cap["not_stated"], caption_url, lang="en")
    text = utm.tag_bare_site_urls(text, "linkedin_weekly", date=pub)
    deck = {"pub": pub, "lang": "en", "slides": slides, "caption": text, "doc_title": f"GRM warning letter analysis · {pub}",
            "url": url, "caption_url": caption_url, "card_id": str(card.get("id")), "mode": "grm"}
    html_ = lg.render_html(deck, font_links=False)
    bad = sorted(set(_HANGUL.findall(text + html_)))
    if bad:
        raise la.AnatomyError([f"영문판에 한글 {len(bad)}종이 남았다: {''.join(bad[:20])}"])
    return deck
