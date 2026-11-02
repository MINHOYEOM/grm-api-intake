#!/usr/bin/env python3
"""링크드인 '사건 해부형' 카드 — 한 주의 대표 경고서한 1건을 6장으로 판다(시범, 플래그 뒤 새 경로).

## 왜 (2026-11-03 콘텐츠 품질 벤치마크)

종전 `linkedin_cards.py` 덱은 한 장이 사건 1건인 요약 8장이라 어느 사건도 끝까지 파지 못한다.
벤치마크(대웅바이오 HoJin Lee 의 경고서한 해부 연재)는 한 서한을 6장으로 판다:
표지(판단이 든 헤드라인 + 서한에 적힌 수치 막대) → FDA 지적 내용 표 → 근본원인 흐름 → 시정조치
요구 → 회사 회신 vs FDA 판단·예방/유효성 → 현장 체크리스트 + 질문 1개.

## 구조 — 코드가 사실을 쥐고, 해설 슬롯은 검증받는다 (비용 0)

  · **코드가 뽑는 사실**: 대상 카드 선정, 발행일·출처, 회신 기한(`required_remediation.deadline`
    의 'N영업일'), FDA 요구 항목 수, 회사 익명 별칭 사용 여부. LLM 이 쓰지 않는다.
  · **해설 슬롯**(`web/data/linkedin_anatomy/<발행일>.json`): 문장을 쓰는 일은 구독 경로의 LLM
    (클라우드 Routine·로컬 세션)이 한다. 유료 API 를 부르는 코드는 이 모듈에 없다.
  · **검증 게이트**(`validate_slots`): 슬롯의 모든 `evidence` 는 카드 원천 텍스트의 **원문 부분
    문자열**이어야 하고, 본문 숫자는 그 근거 안에 있어야 한다. AI 티 상투어(`AI_TELLS`)·실명
    노출·질문형이 아닌 체크리스트·FDA 요구 누락은 덱을 만들기 전에 막는다. 하나라도 걸리면 빈
    덱(예외)이다 — 반쯤 맞는 카드가 나가지 않는다.
  · 슬롯이 없거나 검증을 통과하지 못하면 **발행하지 않고 종전 덱을 그대로 쓴다**(이 모듈은 현행
    출력을 바꾸지 않는다 — 별도 CLI, `linkedin_cards.main` 불변).

서한에 없는 내용은 슬롯에서 `not_stated`(서한이 기재하지 않은 것) / `proposal`(우리 점검 제안)로 갈라
쓰고, 카드에는 '서한은 …를 기재하지 않는다. 점검 항목은 제안이다'로 표시한다.
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
import linkedin_cards as lc  # noqa: E402

SCHEMA = "grm-anatomy-slots/v1"
SLOTS_DIR = WEB_DIR / "data" / "linkedin_anatomy"

# STYLE.md '4. AI 티 제거 체크리스트' 의 금지 표현 — 사람이 쓴 글에서 잘 안 나오는 상투 맺음·
# 빈 수식어. 새 표현을 발견하면 여기와 STYLE.md 를 함께 고친다.
AI_TELLS = [
    r"라는\s*(?:신호|점|사실|의미)", r"신호(?:다|이다|로\s*읽)", r"할\s*필요가\s*있", r"필요가\s*있다",
    r"것으로\s*(?:보인다|판단된다|사료된다|해석된다)", r"드러난다", r"시사(?:한다|점)", r"강조된다",
    r"중요(?:하다|성)", r"핵심(?:축|이다|적)", r"다양한", r"효과적(?:인|으로)", r"철저(?:히|한)",
    r"적극(?:적)?", r"종합적(?:인|으로)", r"이를\s*통해", r"한편", r"나아가", r"아울러",
]
_AI_TELL_RE = re.compile("|".join(AI_TELLS))

_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")

# 슬롯 글자 수 상한 — 1080x1350 카드 안에 들어가는 실측 근사(한글 1자 = 1.0)
LIMITS = {"headline_line": 15.5, "bar_label": 25, "row_text": 78, "flow": 15, "item_title": 14,
          "item_text": 62, "pair_resp": 28, "pair_judg": 66, "check": 40, "question": 40}


class AnatomyError(ValueError):
    """슬롯이 검증을 통과하지 못했다 — `.problems` 에 사유 전부."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:6]) + (f" …(+{len(problems) - 6})" if len(problems) > 6 else ""))
        self.problems = problems


def _norm(s: Any) -> str:
    return _WS.sub(" ", str(s or "")).strip()


def _nums(s: str) -> set[str]:
    return set(_NUM.findall(s or ""))


def _ev_list(ev: Any) -> list[str]:
    if not ev:
        return []
    return [_norm(x) for x in (ev if isinstance(ev, list) else [ev])]


# ── 대상 카드 선정(제안 규칙 — 근거는 company/editorial/ANATOMY_CARDS.md) ──────────────────────

def anatomy_eligible(card: dict) -> bool:
    """해부할 수 있는 경고서한: 본문이 확보되고, 지적·FDA 평가·요구사항이 모두 있다."""
    if not lc.is_warning_letter(card) or not card.get("source_body_captured"):
        return False
    da = card.get("deep_analysis") or {}
    return bool(da.get("key_violations") and _norm(da.get("fda_evaluation"))
                and (da.get("required_remediation") or {}).get("items"))


def anatomy_score(card: dict) -> tuple:
    """클수록 먼저. ① 신호 등급 ② 서한에 적힌 구체 수치 수(지적 본문의 숫자 토큰, 조항·날짜 제외 근사)
    ③ 지적 건수 ④ 앞선 렌더 순서. 결정론 — 같은 입력이면 같은 카드."""
    da = card.get("deep_analysis") or {}
    body = " ".join(_norm(v.get("description")) for v in da.get("key_violations") or [])
    body = re.sub(r"\(\s*21\s*CFR[^)]*\)", " ", body)
    n_facts = len(re.findall(r"\d+\s*(?:건|개|회|년|개월|일|%|배치|명|곳)", body))
    return (int(card.get("signal_tier") or 0), n_facts, len(da.get("key_violations") or []),
            -int(card.get("render_order") or 0))


def pick_anatomy_card(brief_doc: dict, card_id: str = "") -> dict | None:
    cards = [c for c in brief_doc.get("cards") or [] if anatomy_eligible(c)]
    if card_id:
        return next((c for c in cards if str(c.get("id")) == card_id), None)
    return max(cards, key=anatomy_score) if cards else None


# ── 원천 텍스트·검증 ─────────────────────────────────────────────────────────────────────────

def source_text(card: dict) -> str:
    """증거가 될 수 있는 카드 텍스트 전부(국문 번역 `description`·영어 `original`·FDA 평가·요구)."""
    da = card.get("deep_analysis") or {}
    parts: list[str] = [str(card.get("summary") or ""), *[str(x) for x in card.get("key_facts") or []]]
    for v in da.get("key_violations") or []:
        parts += [str(v.get("description") or ""), str(v.get("original") or ""), str(v.get("risk") or "")]
    rr = da.get("required_remediation") or {}
    parts += [str(da.get("fda_evaluation") or ""), str(da.get("administrative_risks") or ""),
              str(rr.get("deadline") or ""), *[str(x) for x in rr.get("items") or []]]
    return _norm(" ".join(parts))


def _visible_fields(slots: dict) -> list[tuple[str, str, Any]]:
    """(경로, 본문, 근거) — 사람이 읽는 문장 전부. 근거가 없는 필드는 근거 칸이 None."""
    out: list[tuple[str, str, Any]] = []
    cv = slots.get("cover") or {}
    for i, ln in enumerate(cv.get("headline") or []):
        out.append((f"cover.headline[{i}]", ln, None))
    out.append(("cover.sub", cv.get("sub") or "", None))
    for i, b in enumerate(cv.get("bars") or []):
        out.append((f"cover.bars[{i}].label", b.get("label") or "", b.get("evidence")))
    s2 = slots.get("s02") or {}
    out.append(("s02.heading", s2.get("heading") or "", None))
    for i, g in enumerate(s2.get("glosses") or []):
        out.append((f"s02.glosses[{i}]", g.get("text") or "", None))
    for i, r in enumerate(s2.get("rows") or []):
        out.append((f"s02.rows[{i}]", r.get("text") or "", r.get("evidence")))
    s3 = slots.get("s03") or {}
    out += [("s03.heading", s3.get("heading") or "", None), ("s03.proposal", s3.get("proposal") or "", None),
            ("s03.not_stated", s3.get("not_stated") or "", None)]
    out += [(f"s03.flow[{i}]", x, None) for i, x in enumerate(s3.get("flow") or [])]
    s4 = slots.get("s04") or {}
    out.append(("s04.heading", s4.get("heading") or "", None))
    for i, it in enumerate(s4.get("items") or []):
        out += [(f"s04.items[{i}].title", it.get("title") or "", None), (f"s04.items[{i}].text", it.get("text") or "", None)]
    out.append(("s04.footnote", s4.get("footnote") or "", None))
    s5 = slots.get("s05") or {}
    out.append(("s05.heading", s5.get("heading") or "", None))
    for i, p in enumerate(s5.get("pairs") or []):
        out += [(f"s05.pairs[{i}].response", p.get("response") or "", p.get("evidence")),
                (f"s05.pairs[{i}].judgment", p.get("judgment") or "", p.get("evidence"))]
    rp = s5.get("repeat") or {}
    out += [("s05.repeat", rp.get("text") or "", rp.get("evidence")), ("s05.not_stated", s5.get("not_stated") or "", None),
            ("s05.proposal", s5.get("proposal") or "", None)]
    s6 = slots.get("s06") or {}
    out.append(("s06.heading", s6.get("heading") or "", None))
    out += [(f"s06.checks[{i}]", x, None) for i, x in enumerate(s6.get("checks") or [])]
    out.append(("s06.question", s6.get("question") or "", None))
    cap = slots.get("caption") or {}
    for k in ("lead", "facts", "contrast", "requests", "question"):
        out.append((f"caption.{k}", cap.get(k) or "", None))
    return out


def validate_slots(card: dict, slots: dict, glossary: list[dict] | None = None) -> list[str]:
    """문제 목록(빈 목록이면 통과). 사람이 고칠 수 있게 경로와 사유를 함께 낸다."""
    probs: list[str] = []
    src = source_text(card)
    src_nums = _nums(src)
    da = card.get("deep_analysis") or {}
    items = (da.get("required_remediation") or {}).get("items") or []
    if slots.get("schema") != SCHEMA:
        probs.append(f"schema 가 {SCHEMA} 가 아니다")
    if str(slots.get("card_id")) != str(card.get("id")):
        probs.append("card_id 가 대상 카드와 다르다")

    # 1) 근거는 원문 부분 문자열, 본문 숫자는 근거 안(근거가 없으면 원천 전체) 안에 있어야 한다
    def _check_evidence(path: str, ev: Any) -> None:
        for e in _ev_list(ev):
            if e not in src:
                probs.append(f"{path}: 근거가 원천에 없다 — {e[:40]}…")
    for path, text, ev in _visible_fields(slots):
        evs = _ev_list(ev)
        if evs:
            allowed = set().union(*(_nums(e) for e in evs))
            extra = _nums(text) - allowed
            if extra:
                probs.append(f"{path}: 근거에 없는 숫자 {sorted(extra)}")
            _check_evidence(path, ev)
        elif _nums(text) - src_nums:
            probs.append(f"{path}: 원천에 없는 숫자 {sorted(_nums(text) - src_nums)}")
        m = _AI_TELL_RE.search(text or "")
        if m:
            probs.append(f"{path}: AI 티 표현 '{m.group(0)}'")
    for i, b in enumerate((slots.get("cover") or {}).get("bars") or []):
        if not _ev_list(b.get("evidence")):
            probs.append(f"cover.bars[{i}]: 근거 없음")
        evs = _ev_list(b.get("evidence"))
        if evs and str(b.get("value")) not in _nums(" ".join(evs)):
            probs.append(f"cover.bars[{i}]: value {b.get('value')} 가 근거에 없다")
    for i, r in enumerate((slots.get("s02") or {}).get("rows") or []):
        if not _ev_list(r.get("evidence")):
            probs.append(f"s02.rows[{i}]: 근거 없음")
    q = _norm((slots.get("s03") or {}).get("fda_quote"))
    if not q or q not in src:
        probs.append("s03.fda_quote: FDA 문장이 원천에 그대로 있어야 한다(verbatim)")
    for i, p in enumerate((slots.get("s05") or {}).get("pairs") or []):
        if not _ev_list(p.get("evidence")):
            probs.append(f"s05.pairs[{i}]: 근거 없음")

    # 2) 구조 — 장마다 정해진 개수, FDA 요구 누락 금지
    cv = slots.get("cover") or {}
    if len(cv.get("headline") or []) not in (1, 2):
        probs.append("cover.headline 은 1~2줄")
    if len(cv.get("bars") or []) != 3:
        probs.append("cover.bars 는 3개")
    if len((slots.get("s02") or {}).get("rows") or []) != 4:
        probs.append("s02.rows 는 4개(무엇이·어떻게·결과·다른 신호)")
    if len((slots.get("s03") or {}).get("flow") or []) != 3:
        probs.append("s03.flow 는 3칸")
    refs = [it.get("ref") for it in (slots.get("s04") or {}).get("items") or []]
    want = list(range(min(len(items), 4)))
    if sorted(r for r in refs if isinstance(r, int)) != want:
        probs.append(f"s04.items 가 서한의 FDA 요구 {len(want)}개를 하나씩 모두 다뤄야 한다(ref {refs})")
    if len((slots.get("s05") or {}).get("pairs") or []) not in (2, 3):
        probs.append("s05.pairs 는 2~3개")
    chk = (slots.get("s06") or {}).get("checks") or []
    if len(chk) != 3:
        probs.append("s06.checks 는 3개")
    for i, c in enumerate(chk + [(slots.get("s06") or {}).get("question") or ""]):
        if not _norm(c).endswith("?"):
            probs.append(f"s06 점검·질문 {i + 1} 은 질문형(?)이어야 한다 — 명사 나열 금지")
    for k, v in (slots.get("caption") or {}).items():
        if k not in ("tags", "en") and not _norm(v):
            probs.append(f"caption.{k} 비어 있음")

    # 3) 업체 익명 — 슬롯 어디에도 실명이 없어야 한다
    firm_tokens = [t for t in re.split(r"[\s,.()]+", str(card.get("headline_target") or ""))
                   if len(t) >= 4 and t.lower() not in ("inc", "llc", "ltd", "corp", "company", "co")]
    blob = json.dumps(slots, ensure_ascii=False).lower()
    for t in firm_tokens:
        if t.lower() in blob:
            probs.append(f"업체 실명 노출: {t}")

    # 4) 글자 수 — 카드 안에 들어가는가
    def _w(label: str, text: str, limit: float) -> None:
        if lc.text_width(text) > limit:
            probs.append(f"{label}: 너무 길다({lc.text_width(text):.0f} > {limit:g}) — {text[:20]}…")
    for i, ln in enumerate(cv.get("headline") or []):
        _w(f"cover.headline[{i}]", ln, LIMITS["headline_line"])
    for i, b in enumerate(cv.get("bars") or []):
        _w(f"cover.bars[{i}].label", b.get("label") or "", LIMITS["bar_label"])
    for i, r in enumerate((slots.get("s02") or {}).get("rows") or []):
        _w(f"s02.rows[{i}]", r.get("text") or "", LIMITS["row_text"])
    for i, x in enumerate((slots.get("s03") or {}).get("flow") or []):
        _w(f"s03.flow[{i}]", x, LIMITS["flow"])
    for i, it in enumerate((slots.get("s04") or {}).get("items") or []):
        _w(f"s04.items[{i}].title", it.get("title") or "", LIMITS["item_title"])
        _w(f"s04.items[{i}].text", it.get("text") or "", LIMITS["item_text"])
    for i, p in enumerate((slots.get("s05") or {}).get("pairs") or []):
        _w(f"s05.pairs[{i}].response", p.get("response") or "", LIMITS["pair_resp"])
        _w(f"s05.pairs[{i}].judgment", p.get("judgment") or "", LIMITS["pair_judg"])
    for i, c in enumerate(chk):
        _w(f"s06.checks[{i}]", c, LIMITS["check"])
    _w("s06.question", (slots.get("s06") or {}).get("question") or "", LIMITS["question"])
    return probs


# ── 덱 ───────────────────────────────────────────────────────────────────────────────────────

def _deadline(card: dict) -> str:
    """'서한 수령 후 15영업일 이내 …' 에서 기한만 — 코드가 원문에서 뽑는다(없으면 빈 문자열)."""
    dl = str(((card.get("deep_analysis") or {}).get("required_remediation") or {}).get("deadline") or "")
    m = re.search(r"(\d+)\s*영업일", dl)
    return f"서한 수령 후 {m.group(1)}영업일 이내 서면 회신" if m else ""


def _issue_date(card: dict) -> str:
    for f in card.get("facts") or []:
        if isinstance(f, dict) and "일자" in str(f.get("label") or ""):
            m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(f.get("value") or ""))
            if m:
                return f"{m.group(3)}년 {int(m.group(1))}월 {int(m.group(2))}일"
    return ""


def build_anatomy_deck(brief_doc: dict, slots: dict, *, glossary: list[dict] | None = None,
                       base_url: str = lc.SITE_BASE_URL) -> dict[str, Any]:
    """슬롯 검증 → 6장 덱(`linkedin_cards.build_deck` 와 같은 모양: pub·slides·caption·doc_title·url).
    검증을 통과하지 못하면 AnatomyError — 호출자는 종전 덱을 쓴다."""
    card = pick_anatomy_card(brief_doc, str(slots.get("card_id") or ""))
    if card is None:
        raise AnatomyError(["card_id 에 맞는 해부 가능한 경고서한이 브리프에 없다"])
    probs = validate_slots(card, slots, glossary)
    if probs:
        raise AnatomyError(probs)
    brief = brief_doc.get("brief") or {}
    pub = str(brief.get("publish_date") or slots.get("pub") or "")
    y, m, wk = lc.title_dateform(pub)
    alias = str(slots.get("firm_alias") or "한 제조소")
    issued = _issue_date(card)
    src_note = f"출처: FDA 경고서한 본문{f' ({issued} 발행)' if issued else ''} · 업체명 비공개"
    ai = lc.AI_NOTE
    cv, s2, s3, s4, s5, s6 = (slots[k] for k in ("cover", "s02", "s03", "s04", "s05", "s06"))
    items = (card["deep_analysis"].get("required_remediation") or {}).get("items") or []
    slides: list[dict] = [
        dict(kind="a_cover", chip=cv["chip"], h1=list(cv["headline"]), sub=cv["sub"], bars_head=cv["bars_head"],
             bars=[(b["label"], int(b["value"]), b.get("unit") or "") for b in cv["bars"]],
             note=f"근거: FDA 경고서한 본문{f'({issued} 발행)' if issued else ''}. {alias}의 이름은 밝히지 않는다. 가림 처리된 수치는 쓰지 않았다.",
             ft_right=src_note, ai=ai),
        dict(kind="a_table", num="02", title="FDA 지적 내용", heading=s2["heading"], cite=s2.get("cite") or "",
             glosses=[(g["term"], g["text"]) for g in s2.get("glosses") or []],
             rows=[(r["label"], r["text"]) for r in s2["rows"]], ft_right=src_note, ai=ai),
        dict(kind="a_flow", num="03", title="근본원인 분석", heading=s3["heading"], flow=list(s3["flow"]),
             fda_quote=s3["fda_quote"], proposal=s3["proposal"], notice=s3["not_stated"], ft_right=src_note, ai=ai),
        dict(kind="a_boxes", num="04", title="시정조치(CA): FDA 요구사항", heading=s4["heading"],
             sub=f"서한이 요구한 항목 {len(items)}건", items=[(it["title"], it["text"]) for it in s4["items"]],
             deadline=_deadline(card), footnote=s4.get("footnote") or "", ft_right=src_note, ai=ai),
        dict(kind="a_pairs", num="05", title="예방조치와 유효성 확인", heading=s5["heading"],
             pairs=[(p["response"], p["judgment"]) for p in s5["pairs"]], repeat=(s5.get("repeat") or {}).get("text") or "",
             notice=s5["not_stated"], proposal=s5["proposal"], ft_right=src_note, ai=ai),
        dict(kind="a_check", num="06", title="시사점: 우리 현장", heading=s6["heading"], checks=list(s6["checks"]),
             question=s6["question"], ft_right="원문 링크는 브리프 페이지에서 확인", ai=ai),
    ]
    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)
    url = f"{base_url}/briefs/{pub}/"
    cap = slots["caption"]
    caption_url = lc.with_utm(url, *lc.LINKEDIN_WEEKLY, lc.linkedin_weekly_campaign(pub))
    ko = "\n\n".join(_norm(cap[k]) if k != "facts" else _norm(cap[k]) for k in ("lead", "facts", "contrast", "requests"))
    parts = [ko, _norm(cap["question"]), f"전체 해설: {caption_url}", cap.get("tags", "")]
    if cap.get("en"):
        parts.insert(2, cap["en"])
    return {"pub": pub, "lang": "ko", "slides": slides, "caption": "\n\n".join(p for p in parts if p) + "\n",
            "doc_title": f"GRM {m}월 {wk}주 경고서한 해부", "url": url, "caption_url": caption_url,
            "card_id": str(card.get("id")), "mode": "anatomy"}


# ── 렌더 ─────────────────────────────────────────────────────────────────────────────────────

CSS = """
.slide.a-cover{background:#1A1815 url(HEX_DARK) 0 0/69.28px 120px;color:#FAF9F5}
.a-cover .brand .w span,.a-cover .pg{color:#A8A299}
.a-cover .ft{border-color:rgba(255,255,255,.14);color:#A8A299}.a-cover .ai{color:rgba(250,249,245,.42)}
.a-chip{align-self:flex-start;padding:10px 26px;border-radius:9999px;background:#FAF9F5;color:#1A1815;font-size:25px;font-weight:700;margin-bottom:44px}
.a-cover h1{font-size:88px}
.a-sub{margin:30px 0 0;font-size:31px;color:rgba(250,249,245,.82)}
.a-bars{margin-top:70px}.a-bars h3{margin:0 0 26px;font-size:32px;font-weight:700}
.a-br{margin-bottom:30px}.a-br .l{font-size:26px;margin-bottom:10px;color:rgba(250,249,245,.92);word-break:keep-all}
.a-br .t{display:flex;align-items:center;gap:20px}.a-br .b{height:36px;border-radius:7px;background:#E3936F}
.a-br .v{font-size:42px;font-weight:800;color:#E3936F;font-variant-numeric:tabular-nums;white-space:nowrap}.a-br .v small{font-size:24px;font-weight:600;margin-left:4px}
.a-page .main{justify-content:flex-start;padding-top:46px}
.a-note{margin:6px 0 0;font-size:21px;line-height:1.5;color:#A8A299;word-break:keep-all}
.a-num{display:flex;align-items:baseline;gap:20px;padding-bottom:20px;border-bottom:4px solid #141413;margin-bottom:34px}
.a-num b{font-size:64px;color:#C2603F;letter-spacing:-.03em}.a-num span{font-size:50px;font-weight:800;letter-spacing:-.02em}
.a-hd{padding:16px 22px;border-left:8px solid #C2603F;background:#EFE9DE;font-size:31px;font-weight:700;line-height:1.35;word-break:keep-all;margin-bottom:6px}
.a-cite{font-size:23px;color:#6C6A64;margin:10px 0 26px}
.a-gl{background:#FFFDF9;border:1.5px solid #E6DFD8;border-radius:12px;padding:16px 22px;margin-bottom:28px;font-size:22px;line-height:1.5;color:#3D3D3A;word-break:keep-all}
.a-gl p{margin:4px 0}.a-gl b{color:#A14B30}
.a-rows{display:flex;flex-direction:column;gap:26px}
.a-r{display:grid;grid-template-columns:150px 1fr;gap:20px;font-size:28px;line-height:1.5;word-break:keep-all}
.a-r b{color:#C2603F;font-weight:800}
.a-flow{display:grid;grid-template-columns:1fr 36px 1fr 36px 1fr;align-items:stretch;gap:8px;margin:14px 0 40px}
.a-fb{border:2px solid #141413;border-radius:10px;background:#FFFDF9;padding:22px 16px;font-size:26px;font-weight:700;line-height:1.35;display:flex;align-items:center;word-break:keep-all}
.a-fb.last{background:#1A1815;color:#FAF9F5}.a-ar{align-self:center;color:#C2603F;font-size:34px;text-align:center}
.a-q{font-size:28px;line-height:1.55;margin-bottom:18px;word-break:keep-all}.a-q b{color:#C2603F;margin-right:14px}
.a-q em{font-style:normal;color:#3D3D3A}
.a-warn{margin-top:22px;padding:18px 22px;background:#FBF1DD;border:1.5px solid #E9C98C;border-radius:10px;font-size:23px;line-height:1.5;color:#5A4210;word-break:keep-all}
.a-sub2{font-size:27px;font-weight:700;padding:14px 20px;background:#EFE9DE;border-left:8px solid #C2603F;margin-bottom:24px}
.a-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.a-bx{background:#FFFDF9;border:1.5px solid #E6DFD8;border-radius:12px;padding:24px 24px 22px}
.a-bx h4{margin:0 0 12px;display:flex;align-items:center;gap:14px;font-size:27px}
.a-bx h4 i{font-style:normal;flex:none;width:42px;height:42px;border-radius:50%;background:#C2603F;color:#fff;font-size:24px;display:flex;align-items:center;justify-content:center}
.a-bx p{margin:0;font-size:22px;line-height:1.55;color:#3D3D3A;word-break:keep-all}
.a-dl{margin-top:26px;font-size:25px;font-weight:700;color:#A14B30}.a-fn{margin-top:14px;font-size:21px;color:#6C6A64}
.a-pr{display:flex;flex-direction:column;gap:20px}
.a-p{display:grid;grid-template-columns:1fr 44px 1.5fr;gap:12px;align-items:stretch}
.a-p div{padding:20px 20px;border-radius:10px;font-size:23px;line-height:1.5;word-break:keep-all}
.a-p .r{background:#EFE9DE}.a-p .j{background:#1A1815;color:#FAF9F5}.a-p .ar{padding:0;background:none;color:#C2603F;font-size:32px;display:flex;align-items:center;justify-content:center}
.a-p small{display:block;font-size:17px;font-weight:700;margin-bottom:6px;opacity:.75}
.a-rep{margin-top:22px;font-size:24px;line-height:1.5;color:#3D3D3A;word-break:keep-all}.a-rep b{color:#A14B30}
.a-ck{display:flex;flex-direction:column;gap:20px;margin-bottom:34px}
.a-ck div{display:flex;gap:18px;align-items:flex-start;font-size:29px;line-height:1.45;word-break:keep-all}
.a-ck div::before{content:'';flex:none;width:30px;height:30px;margin-top:6px;border:3px solid #C2603F;border-radius:7px}
.a-qq{background:#C2603F;color:#FAF9F5;border-radius:14px;padding:30px 32px;font-size:34px;font-weight:800;line-height:1.4;word-break:keep-all}
.a-qq small{display:block;font-size:20px;font-weight:700;margin-bottom:10px;opacity:.85}
"""


def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _slide_html(s: dict) -> str:
    k = s["kind"]
    cls = " a-cover" if k == "a_cover" else " a-page"
    p: list[str] = []
    if k == "a_cover":
        mx = max(v for _, v, _ in s["bars"]) or 1
        p.append(f'<div class="a-chip">{_e(s["chip"])}</div>')
        p.append(lc._h1(s["h1"], 88))
        p.append(f'<p class="a-sub">{_e(s["sub"])}</p>')
        bars = "".join(
            f'<div class="a-br"><div class="l">{_e(lab)}</div><div class="t"><span class="b" style="width:{max(2, int(560 * v / mx))}px"></span>'
            f'<span class="v">{v}<small>{_e(u)}</small></span></div></div>' for lab, v, u in s["bars"])
        p.append(f'<div class="a-bars"><h3>{_e(s["bars_head"])}</h3>{bars}<p class="a-note">{_e(s["note"])}</p></div>')
    else:
        p.append(f'<div class="a-num"><b>{_e(s["num"])}</b><span>{_e(s["title"])}</span></div>')
        if k == "a_table":
            p.append(f'<div class="a-hd">{_e(s["heading"])}</div>')
            p.append(f'<div class="a-cite">{_e(s["cite"])}</div>' if s.get("cite") else "")
            if s.get("glosses"):
                p.append('<div class="a-gl">' + "".join(f"<p><b>{_e(t)}</b> {_e(x)}</p>" for t, x in s["glosses"]) + "</div>")
            p.append('<div class="a-rows">' + "".join(f'<div class="a-r"><b>{_e(a)}</b><span>{_e(b)}</span></div>' for a, b in s["rows"]) + "</div>")
        elif k == "a_flow":
            p.append(f'<div class="a-hd">{_e(s["heading"])}</div><div style="height:28px"></div>')
            boxes = []
            for i, x in enumerate(s["flow"]):
                boxes.append(f'<div class="a-fb{" last" if i == 2 else ""}">{_e(x)}</div>')
                if i < 2:
                    boxes.append('<div class="a-ar">▶</div>')
            p.append(f'<div class="a-flow">{"".join(boxes)}</div>')
            p.append(f'<div class="a-q"><b>FDA 관점</b><em>“{_e(s["fda_quote"])}”</em></div>')
            p.append(f'<div class="a-q"><b>점검(제안)</b>{_e(s["proposal"])}</div>')
            p.append(f'<div class="a-warn">{_e(s["notice"])}</div>')
        elif k == "a_boxes":
            p.append(f'<div class="a-sub2">{_e(s["sub"])}</div>')
            p.append('<div class="a-grid">' + "".join(
                f'<div class="a-bx"><h4><i>{n}</i>{_e(t)}</h4><p>{_e(x)}</p></div>' for n, (t, x) in enumerate(s["items"], 1)) + "</div>")
            if s.get("deadline"):
                p.append(f'<div class="a-dl">회신 기한: {_e(s["deadline"])}</div>')
            if s.get("footnote"):
                p.append(f'<div class="a-fn">{_e(s["footnote"])}</div>')
        elif k == "a_pairs":
            p.append(f'<div class="a-hd">{_e(s["heading"])}</div><div style="height:24px"></div>')
            p.append('<div class="a-pr">' + "".join(
                f'<div class="a-p"><div class="r"><small>회사 회신</small>{_e(r)}</div><div class="ar">▶</div>'
                f'<div class="j"><small>FDA 판단: 부적절</small>{_e(j)}</div></div>' for r, j in s["pairs"]) + "</div>")
            if s.get("repeat"):
                p.append(f'<div class="a-rep"><b>반복 위반</b> {_e(s["repeat"])}</div>')
            p.append(f'<div class="a-warn">{_e(s["notice"])}<br><b>점검(제안)</b> {_e(s["proposal"])}</div>')
        elif k == "a_check":
            p.append(f'<div class="a-hd">{_e(s["heading"])}</div><div style="height:30px"></div>')
            p.append('<div class="a-ck">' + "".join(f"<div>{_e(c)}</div>" for c in s["checks"]) + "</div>")
            p.append(f'<div class="a-qq"><small>오늘 우리 팀에 던질 질문</small>{_e(s["question"])}</div>')
    ai = f'<div class="ai">{_e(s["ai"])}</div>' if s.get("ai") else ""
    brand = lc.OWL_CREAM_SVG if k == "a_cover" else lc.OWL_SVG
    return (f'<section class="slide{cls}"><div class="hd"><div class="brand">{brand}<span class="w"><b>GRM</b>'
            f'<span>Global Regulatory Monitor</span></span></div><div class="pg">{s["idx"]:02d} / {s["total"]:02d}</div></div>'
            f'<div class="main">{"".join(p)}</div>'
            f'<div class="ft"><span>grm-solutions.com</span><span>{_e(s.get("ft_right", ""))}</span></div>{ai}</section>')


def render_html(deck: dict, *, font_links: bool = True) -> str:
    css = (lc.CSS + CSS).replace("HEX_DARK", lc._data_uri(lc._HEX.format(op="0.07"))) \
        .replace("HEX_CREAM", lc._data_uri(lc._HEX.format(op="0.16")))
    body = "".join(_slide_html(s) for s in deck["slides"])
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{_e(deck["doc_title"])}</title>'
            f'{lc.FONT_LINKS if font_links else ""}<style>{css}</style></head><body class="lang-ko">{body}</body></html>')


def render_pngs(deck: dict, stem: str, out_dir: Path, chrome: str) -> list[str]:
    """장마다 PNG(`{stem}_01.png`…). 하나라도 실패하면 쓴 것을 지우고 빈 목록(반쪽 세트 금지)."""
    names: list[str] = []
    try:
        for i, slide in enumerate(deck["slides"], 1):
            tmp = out_dir / f"{stem}_{i:02d}.tmp.html"
            tmp.write_text(render_html(dict(deck, slides=[slide])), encoding="utf-8")
            name = f"{stem}_{i:02d}.png"
            try:
                lc.render_png(tmp, out_dir / name, chrome)
            finally:
                tmp.unlink(missing_ok=True)
            names.append(name)
    except Exception as exc:  # 비차단
        print(f"::warning::{stem} PNG 렌더 실패({type(exc).__name__})", file=sys.stderr)
        for n in names + [f"{stem}_{len(names) + 1:02d}.png"]:
            (out_dir / n).unlink(missing_ok=True)
        return []
    return names


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):      # cp949 콘솔 방어(저장소 관용구)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="링크드인 사건 해부형 6장 카드(시범) — 슬롯 검증 후 생성")
    ap.add_argument("--brief", required=True, help="브리프 JSON")
    ap.add_argument("--slots", help="해설 슬롯 JSON(기본: web/data/linkedin_anatomy/<발행일>.json)")
    ap.add_argument("--out", required=True, help="출력 디렉터리")
    ap.add_argument("--no-pdf", action="store_true", help="Chrome 렌더(PNG)를 건너뛴다")
    ap.add_argument("--candidates", action="store_true", help="대상 후보와 점수만 출력")
    args = ap.parse_args(argv)
    doc = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    if args.candidates:
        for c in sorted((c for c in doc.get("cards") or [] if anatomy_eligible(c)), key=anatomy_score, reverse=True):
            print(c["id"], anatomy_score(c), c.get("headline_target"))
        return 0
    pub = str((doc.get("brief") or {}).get("publish_date") or "")
    sp = Path(args.slots) if args.slots else SLOTS_DIR / f"{pub}.json"
    if not sp.exists():
        print(f"::warning::해설 슬롯 없음({sp.name}) — 해부형 덱을 만들지 않는다(종전 덱 유지)", file=sys.stderr)
        return 0
    slots = json.loads(sp.read_text(encoding="utf-8"))
    glossary = json.loads((WEB_DIR / "data" / "glossary.json").read_text(encoding="utf-8"))
    try:
        deck = build_anatomy_deck(doc, slots, glossary=glossary)
    except AnatomyError as exc:
        print("::warning::해부형 덱 검증 실패 — 만들지 않는다(종전 덱 유지)", file=sys.stderr)
        for pr in exc.problems:
            print(f"  - {pr}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "linkedin_anatomy.txt").write_text(deck["caption"], encoding="utf-8")
    (out / "linkedin_anatomy.html").write_text(render_html(deck), encoding="utf-8")
    chrome = None if args.no_pdf else lc.find_chrome()
    pngs: list[str] = []
    if chrome:
        pngs = render_pngs(deck, "linkedin_anatomy", out, chrome)
    print(f"linkedin_anatomy: {pub} · {len(deck['slides'])}장 · 본문 {len(deck['caption'])}자 · PNG {len(pngs)} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
