# -*- coding: utf-8 -*-
"""가림·깨진 값 추정 채움 검사 — 기자 사전 점검(`precheck.py terms`·`deep`) 전용(발행 조립·v16 백업과 무관).

★리허설 1121b: 전수 교열이 153장 중 21장을 반려했고 그 2/3 가 **가려진(`[redacted]`·`(b)(4)`)·OCR 깨진 값을
  기자가 구체 명사·숫자로 채운 것**이었다 — 원문 '[redacted] meter' → '산도계', 'Line [redacted] Tester' → '누설시험',
  '[redacted] vials per minute' → '분당 수십 개', 'unlocked worksheets' → '엑셀', '[redacted] system' → '정제수 시스템'.

판정(순수, 결정론):
- 자주 채워 넣는 낱말 사전(`LEXICON`)의 낱말이 카드 산문에 있는데, **그 카드 원문 전체**(handoff 본문·body_full·
  prose_input + 스캐폴드 facts·quotes·결정론 상세)에 대응하는 말이 **영어로도 한국어로도 없으면** 원문에 없는 말이다.
  사실 칸(제목·hook_line·요약·key_facts·관찰 국문·위반 서술·FDA 평가 등)이면 **차단**, 권고 칸(implication·checks·
  risk·administrative_risks·required_remediation)이면 경고.
- 원문 쪽 대응 표현은 넓게 잡는다(OCR 이 낱말을 붙여 쓴 'ofmedia'·'pHi' 도 잡도록 낱말 경계를 두지 않는다) —
  거짓 차단을 줄이는 쪽으로 기운다. 사전에 없는 추정(‘일부’·‘훨씬 뒤’ 같은 말)은 코드가 못 센다 — reporter.md 규칙이 막는다.
- 경고: 483 관찰 국문(`observations_ko[i]`)·`(관찰 N)` 이 하나인 위반 서술에서, 낱말이 카드 어딘가엔 있지만
  **그 관찰 원문에는 없고 그 관찰에 가림 표지가 있는** 경우(다른 관찰의 'water' 를 가려진 관찰에 옮겨 채운 꼴, 1121b 196372).
  '원료'처럼 원문 대응이 넓은 낱말(`SOFT`)은 카드 전체에 없어도 경고만.
"""
from __future__ import annotations

import json
import re

# (이름, 카드 산문에서 찾는 꼴, 원문 근거로 인정하는 꼴)
_LEX = (
    ("물", r"정제수|주사용수|용수|원수|(?<![가-힣])물(?:을|이|은|의|로|과|에)?(?![가-힣])(?!\s?(?:때|뒤|후|다음|것|수|적|\d))|\bwater\b",
     r"water|WFI|\bPW\b|purified|aqueous|정제수|주사용수|용수"),
    ("pH", r"산도|\bpH\b|수소이온", r"\bpH|acidity|산도"),
    ("누설", r"누설|누출|\bleak", r"leak|integrity|\bCCI|누설|누출"),
    ("불순물", r"불순물|유연물질|분해산물|impurit",
     r"impurit|degrad|related (substance|compound)|nitros|불순물|유연물질"),
    ("엑셀", r"엑셀|\bExcel\b", r"excel|엑셀"),
    ("배지", r"배지|\bmedia\b|\bmedium\b", r"media|medium|broth|growth promotion|\bTSB|\bSCD|agar|배지"),
    ("소독", r"소독|살균제|disinfect|sanitiz",
     r"disinfect|sanit|sporicid|steril|biocid|bleach|alcohol|\bIPA\b|peroxide|소독|살균"),
    ("내시경", r"내시경|endoscop", r"endoscop|내시경"),
    ("수십", r"수[십백천](?=\s?[개건명회번병바로배종곳일시분%])|\bdozens\b|\bhundreds of\b",
     r"dozens|hundreds|thousands|tens of|수십|수백|수천"),
    ("박층", r"박층|\bTLC\b|thin[- ]layer", r"thin[- ]?layer|\bTLC\b|박층"),
)
_SOFT = (
    ("원료", r"원료|raw material",
     r"raw material|\bAPIs?\b|ingredient|starting material|component|원료|원약|원재료"),
)
LEXICON = tuple((n, re.compile(o, re.I), re.compile(s, re.I)) for n, o, s in _LEX)
SOFT = tuple((n, re.compile(o, re.I), re.compile(s, re.I)) for n, o, s in _SOFT)

# 가림 표지 — OCR 변형 '()(4)'·'(b) 4)' 포함
REDACT_RE = re.compile(r"\[redacted\]|\[가림\]|\(b\)\s?\(?4\)|\(\s?\)\s?\(4\)|\[판독불가\]|\[비공개\]", re.I)

_DELTA_FACT = ("title_issue", "hook_line", "summary")
_DELTA_LISTS_FACT = ("key_facts",)
_DELTA_ADVICE = ("implication",)
_DELTA_LISTS_ADVICE = ("checks",)
_DEEP_FACT_TEXT = ("inspectional_significance", "fda_evaluation", "disposition_basis")
_DEEP_ADVICE_TEXT = ("administrative_risks",)
_DEEP_FACT_VIOL = ("observation", "description", "summary")
_DEEP_ADVICE_VIOL = ("risk",)
_SKIP_KEYS = {"original", "citation", "number", "url", "source_url"}


def _dump(x):
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)


def card_source(row=None, scaffold_card=None, body_full=None):
    """카드 원문 전체(순수) — handoff 행의 원문 필드 + 스캐폴드 결정론 필드 + body_full. 기자 산문은 넣지 않는다."""
    parts = []
    if isinstance(row, dict):
        for k in ("body", "raw_excerpt", "headline", "title", "firm", "prose_input", "deep_analysis_input"):
            if row.get(k):
                parts.append(_dump(row[k]))
    if isinstance(scaffold_card, dict):
        for k in ("facts", "quotes", "deterministic_detail", "merged_items"):
            if scaffold_card.get(k):
                parts.append(_dump(scaffold_card[k]))
    if body_full:
        parts.append(str(body_full))
    return "\n".join(parts)


def observation_sources(scaffold_card):
    """스캐폴드 483 관찰 → {번호: 원문}(순수). 번호가 겹치면 이어 붙인다."""
    dd = scaffold_card.get("deterministic_detail") if isinstance(scaffold_card, dict) else None
    out = {}
    for o in (dd.get("observations") if isinstance(dd, dict) else None) or []:
        if isinstance(o, dict):
            n = str(o.get("number") or "").strip()
            out[n] = (out.get(n, "") + " " + " ".join(str(o.get(k) or "") for k in ("deficiency", "detail"))).strip()
    return out


def delta_slots(card):
    """델타 카드 → [(슬롯, 산문, 사실 칸 여부)](순수). 한국어·영문 둘 다."""
    out = []
    for prefix, d in (("", card), ("en.", card.get("en") if isinstance(card, dict) else None)):
        if not isinstance(d, dict):
            continue
        for keys, lists, fact in ((_DELTA_FACT, _DELTA_LISTS_FACT, True), (_DELTA_ADVICE, _DELTA_LISTS_ADVICE, False)):
            out += [(prefix + k, d.get(k), fact) for k in keys]
            for k in lists:
                v = d.get(k)
                out += [(f"{prefix}{k}[{i}]", x, fact) for i, x in enumerate(v if isinstance(v, list) else [])]
    return [(s, t, f) for s, t, f in out if isinstance(t, str) and t.strip()]


def _walk(prefix, x):
    if isinstance(x, str):
        if x.strip():
            yield prefix, x
    elif isinstance(x, dict):
        for k, v in x.items():
            if k not in _SKIP_KEYS:
                yield from _walk(f"{prefix}.{k}", v)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _walk(f"{prefix}[{i}]", v)


def deep_slots(entry):
    """심층 항목 → [(슬롯, 산문, 사실 칸 여부, 관찰 번호|None)](순수). 원문 인용 `original`·조항 `citation` 제외."""
    if not isinstance(entry, dict):
        return []
    out = []
    for i, o in enumerate(entry.get("observations_ko") or []):
        if isinstance(o, dict):
            n = str(o.get("number") or "").strip() or None
            out += [(f"observations_ko[{i}].{k}", o.get(k), True, n) for k in ("deficiency_ko", "detail_ko")]
    for key in ("violations_ko", "ncr_ko"):
        out += [(s, t, True, None) for s, t in _walk(key, entry.get(key))]
    da = entry.get("deep_analysis") if isinstance(entry.get("deep_analysis"), dict) else {}
    for i, v in enumerate(da.get("key_violations") or []):
        if not isinstance(v, dict):
            continue
        for keys, fact in ((_DEEP_FACT_VIOL, True), (_DEEP_ADVICE_VIOL, False)):
            for k in keys:
                t = v.get(k)
                if isinstance(t, str):
                    nums = set(re.findall(r"관찰\s?(\d+)", t))
                    out.append((f"key_violations[{i}].{k}", t, fact, nums.pop() if len(nums) == 1 else None))
    for keys, fact in ((_DEEP_FACT_TEXT, True), (_DEEP_ADVICE_TEXT, False)):
        out += [(k, da.get(k), fact, None) for k in keys]
    rem = da.get("required_remediation")
    out += [(s, t, False, None) for s, t in _walk("required_remediation", rem)]
    return [(s, t, f, n) for s, t, f, n in out if isinstance(t, str) and t.strip()]


def check_slots(slots, src, obs_src=None):
    """(차단 줄, 경고 줄)(순수). `slots` = [(슬롯, 산문, 사실 칸 여부, 관찰 번호|None)], `src` = 카드 원문 전체."""
    block, warn = [], []
    obs_src = obs_src or {}
    for slot, text, fact, n in slots:
        for name, out_re, src_re in LEXICON:
            m = out_re.search(text)
            if not m:
                continue
            if not src_re.search(src):
                msg = (f"{slot}: '{m.group(0)}' — 원문 어디에도 대응하는 말({name})이 없다. "
                       f"가려진·깨진 값을 채우지 않는다 — [가림] 으로 두거나 그 구절을 뺀다")
                (block if fact else warn).append(msg)
            elif n and n in obs_src:
                o = obs_src[n]
                if not src_re.search(o) and REDACT_RE.search(o):
                    warn.append(f"{slot}: '{m.group(0)}' — 관찰 {n} 원문에는 없고(가림 표지 있음) 다른 관찰에만 있다. "
                                f"가려진 값을 다른 관찰 낱말로 채우지 않았는지")
        for name, out_re, src_re in SOFT:
            m = out_re.search(text)
            if m and not src_re.search(src):
                warn.append(f"{slot}: '{m.group(0)}' — 원문에 대응하는 말({name})이 안 보인다. 가려진 대상을 분류로 채우지 않았는지")
    return block, warn


def delta_guess_lines(delta, rows, scaffold_cards):
    """델타 → (차단 줄, 경고 줄)(순수). `rows` = {id: handoff 행}, `scaffold_cards` = {id: 스캐폴드 카드}.
    handoff 행이 없는 카드는 원문 전체를 모르므로 차단하지 않고 경고로만 낸다(스캐폴드만으로는 원문이 잘려 있을 수 있다)."""
    block, warn = [], []
    cards = delta.get("cards") if isinstance(delta, dict) and isinstance(delta.get("cards"), dict) else {}
    for cid, card in cards.items():
        row, sc = rows.get(cid), scaffold_cards.get(cid)
        if not isinstance(card, dict) or not (row or sc):
            continue
        src = card_source(row, sc)
        b, w = check_slots([(s, t, f, None) for s, t, f in delta_slots(card)], src)
        prefix = "가림 값 채움" if row else "가림 값 채움 의심"
        (block if row else warn).extend(f"{prefix} {cid}.{x}" for x in b)
        warn.extend(f"가림 값 채움 의심 {cid}.{x}" for x in w)
    return block, warn


def deep_guess_lines(shard, rows, scaffold_cards=None):
    """심층 샤드(`{items:{…}}` 또는 `{id: 항목}`) → (차단 줄, 경고 줄)(순수). 원문 = handoff 행(body_full 포함)."""
    scaffold_cards = scaffold_cards or {}
    items = shard.get("items") if isinstance(shard, dict) and isinstance(shard.get("items"), dict) else shard
    block, warn = [], []
    for cid, entry in (items or {}).items():
        row = rows.get(cid)
        if not row:
            continue
        sc = scaffold_cards.get(cid)
        b, w = check_slots(deep_slots(entry), card_source(row, sc), observation_sources(sc))
        block.extend(f"가림 값 채움 {cid}.deep.{x}" for x in b)
        warn.extend(f"가림 값 채움 의심 {cid}.deep.{x}" for x in w)
    return block, warn


def handoff_rows(payload):
    """handoff payload → {web_card_id: 행}(순수). 같은 id 는 첫 행."""
    out = {}
    rows = payload.get("rows") if isinstance(payload, dict) else None
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get("web_card_id"), str) and row["web_card_id"] not in out:
            out[row["web_card_id"]] = row
    return out


def scaffold_index(scaffold):
    """스캐폴드 → {id: 카드}(순수)."""
    if not (isinstance(scaffold, dict) and isinstance(scaffold.get("cards"), list)):
        return {}
    return {c.get("id"): c for c in scaffold["cards"] if isinstance(c, dict)}


# ─────────────────────────────────────────────────────────────────────────────
# [2026-11-23] 교열 판정이 회차마다 갈린 4가지 — 원문 대조를 코드로 고정한다.
#   리허설 1122b 2차 반려(개인 실명 2·가려진 값 추측 1·직원 진술 사실화 1)가 같은 재료로 다음 판정에선 통과했고,
#   11-23 실발행은 개인 실명 2장(fda483-196329·196343)을 '개인 사업체 업체명' 이라며 통과시켜 발행 PR 에서 손으로 고쳤다.
#   ① 개인 실명(업체명이 사람 이름) ② 가려진 값 추측(가림 표지 앞 낱말 + 원문에 없는 명사) ③ 직원 진술 단정 ④ 원문에 없는 날짜.
#   확실한 것은 막고(차단), 애매한 것은 교열이 근거를 붙여 판정하도록 경고로 낸다.
#   기자 사전 점검(`precheck.py terms`·`deep`)과 교열 코드 검사(`precheck.py source`)가 **이 함수들을 그대로** 부른다.
# ─────────────────────────────────────────────────────────────────────────────
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
import grm_findings as _gf  # noqa: E402  표기·판정 단일 출처(card_scaffold·web/render 와 같은 함수)

REDACTED_PERSON_BY_LANG = _gf.REDACTED_PERSON_BY_LANG
_FIRM_LABELS = ("제조소/업체", "업체/제조소", "업체", "제조소")


def _unescape(src):
    return re.sub(r"\\[nrt]", " ", src or "")   # JSON 덤프의 '\n' 글자 — 낱말 경계를 되살린다


def _firm_values(row, sc):
    """카드의 업체명 후보(순수) — handoff 행 `firm`·스캐폴드 업체 사실값(` · FEI …`·` (KR)` 앞)·`headline_target`."""
    vals = []
    if isinstance(row, dict):
        vals.append(str(row.get("firm") or ""))
        pi = row.get("prose_input")
        if isinstance(pi, dict):
            vals.append(str(pi.get("firm") or pi.get("firm_or_product") or ""))
    if isinstance(sc, dict):
        vals.append(str(sc.get("headline_target") or ""))
        for f in sc.get("facts") or []:
            if isinstance(f, dict) and f.get("label") in _FIRM_LABELS:
                v = str(f.get("value") or "")
                for sep in (" · ", " ("):
                    v = v.split(sep, 1)[0]
                vals.append(v)
    return [v.strip() for v in vals if v and v.strip()]


def _establishment(row, sc):
    parts = []
    if isinstance(sc, dict):
        parts += [str(f.get("value") or "") for f in sc.get("facts") or []
                  if isinstance(f, dict) and f.get("label") == "시설 · 유형"]
    if isinstance(row, dict):
        parts.append(str(row.get("type_or_class") or ""))
    return " ".join(parts)


def person_firm_names(row, sc):
    """(확실한 사람 이름 업체명 집합, 의심 업체명 집합)(순수) — `grm_findings.person_firm_name` 판정."""
    est = _establishment(row, sc)
    sure, suspect = set(), set()
    for v in _firm_values(row, sc):
        if _gf.person_firm_name(v):
            sure.add(v)
        elif _gf.person_firm_name_suspect(v, est):
            suspect.add(v)
    return sure, suspect


def _name_tokens(name):
    return [t for t in re.findall(r"[A-Za-z][A-Za-z'\-]+", name) if len(t) >= 3]


def _name_hit(name, text):
    """이름 전체 또는 이름·성 낱말(3자 이상, 이니셜 제외)이 산문에 있으면 그 낱말(순수)."""
    for t in [name] + _name_tokens(name):
        if re.search(r"(?<![A-Za-z])" + re.escape(t) + r"(?![A-Za-z])", text):
            return t
    return ""


def person_firm_lines(slots, row, sc):
    """① (차단, 경고)(순수). `slots` = [(슬롯, 산문)]. 업체명이 사람 이름이면 산문에 그 이름이 나오면 안 된다."""
    sure, suspect = person_firm_names(row, sc)
    block, warn = [], []
    ko, en = REDACTED_PERSON_BY_LANG["ko"], REDACTED_PERSON_BY_LANG["en"].lower()
    for slot, text in slots:
        for name in sorted(sure):
            hit = _name_hit(name, text)
            if hit:
                block.append(f"{slot}: '{hit}' — 업체명 '{name}' 은 사람 이름(개인 사업자)이다. 이름을 빼고 "
                             f"'{ko}'(영문 '{en}')로 쓴다(11-21 대표 결정)")
        for name in sorted(suspect):
            hit = _name_hit(name, text)
            if hit:
                warn.append(f"{slot}: '{hit}' — 업체명 '{name}' 이 사람 이름일 수 있다(시설 유형: 개인 농장·목장 등). "
                            f"사람이면 '{ko}' 로 쓴다")
    return block, warn


# ② 가려진 값 추측 — 원문 'filling [redacted]' 를 카드가 'filling parts·filling equipment'(충전 부품)로 채운 꼴(1122b 196444).
#   가림 표지 바로 앞 낱말(W)을 원문에서 모으고, 카드 영문에 'W N' 이 있는데 원문 어디에도 'W N' 이 없으면 N 이 가려진 값 자리다.
#   N 이 흔히 채워 넣는 일반 명사(부품·설비·장치 등)면 사실 칸에서 막고, 그 밖의 명사는 경고.
_REDACT_BEFORE_RE = re.compile(r"\b([A-Za-z][a-z\-]{3,})\s*[,:]?\s*(?:" + REDACT_RE.pattern + r")", re.I)
_FILL_NOUNS = re.compile(r"^(?:equipment|parts?|components?|devices?|systems?|units?|machines?|machinery|instruments?|"
                         r"apparatus|tools?|assembl(?:y|ies)|hardware|accessor(?:y|ies)|items?|materials?)$", re.I)
_EN_STOP = frozenset((
    "about after against along also among and are around because been before being below between both but by can "
    "could did does during each either for from had has have having here however into its itself may might more most "
    "must near not now only onto other over per same should since some such than that the their them then there these "
    "they this those through thus under until upon used using very was were what when where which while who whose will "
    "with within without would your again further once").split())


def redaction_fill_lines(pairs, src):
    """② (차단, 경고)(순수). `pairs` = [(영문 슬롯, 영문 산문, 사실 칸 여부, 국문 짝 슬롯|None)], `src` = 카드 원문 전체."""
    src = _unescape(src)
    words = {m.group(1).lower() for m in _REDACT_BEFORE_RE.finditer(src)} - _EN_STOP
    block, warn = [], []
    if not words:
        return block, warn
    low = src.lower()
    for slot, text, fact, ko_slot in pairs:
        for m in re.finditer(r"(?=\b([A-Za-z][a-z\-]{3,})\s+([A-Za-z][a-z\-]{2,})\b)", text):   # 겹쳐 본다('Corroded filling parts')
            w, n = m.group(1).lower(), m.group(2).lower()
            if w not in words or not _FILL_NOUNS.match(n):
                continue   # 일반 채움 명사만 본다 — 그 밖의 'W N'(lots released·room cleaning)은 5회차 실측 537줄이 전부 소음이었다
            if re.search(r"\b" + re.escape(w) + r"\s+" + re.escape(n[:max(4, len(n) - 2)]), low):
                continue   # 원문에 'W N' 이 있다 — 채운 것이 아니다
            pair = f" (국문 짝 {ko_slot} 도 같은 말인지)" if ko_slot else ""
            msg = (f"{slot}: '{m.group(1)} {m.group(2)}' — 원문은 '{w} [가림]' 이고 '{w} {n}' 은 원문 어디에도 없다. "
                   f"가려진 값을 채우지 않는다 — [가림] 으로 두거나 그 구절을 뺀다{pair}")
            (block if fact else warn).append(msg)
    return block, warn


# ③ 직원 진술 단정 — 원문이 '관리자가 ~라고 인정/진술했다'(acknowledged·stated·told·confirmed …)로만 적은 내용을
#   카드가 진술 표기 없이 사실로 쓴 꼴(1122b·11-23 196410 '조사 문서 소급 작성'). 진술 문장에만 나오는 낱말(원문의 다른 문장엔 없음)이
#   카드 영문 사실 칸에 진술 표기 없이 나오면 그 칸과 국문 짝을 낸다.
_STMT_VERB_RE = re.compile(r"\b(?:stated|told|acknowledged|admitted|confirmed|explained|indicated|claimed|said|informed|"
                           r"asserted|mentioned|conceded|according\s+to)\b", re.I)
_STMT_SUBJ_RE = re.compile(r"\b(?:managers?|directors?|head|supervisors?|employees?|staff|personnel|operators?|analysts?|"
                           r"technicians?|representatives?|officers?|officials?|chemists?|microbiologists?|management|"
                           r"president|owner|ceo|he|she|they|individuals?|workers?|engineers?|specialists?|"
                           r"coordinators?|associates?)\b", re.I)
_STMT_MARK_EN = re.compile(r"stat|said|told|acknowledg|admit|confirm|according|claim|alleg|explain|indicat|"
                           r"report(?:ed|s)?\s+that|conced|assert|mention|inform", re.I)
_STMT_MARK_KO = re.compile(r"진술|인정|시인|밝혔|말했|따르면|주장|설명했|확인했다고|라고|다고 ")
_STEM = 6


def _sentences(src):
    return [s for s in re.split(r"(?<=[.;!?])\s+|\n+", src) if s.strip()]


def _stems(text):
    return {w.lower()[:_STEM] for w in re.findall(r"[A-Za-z]{6,}", text)}


def statement_only_stems(src):
    """원문에서 직원 진술 문장에만 나오는 낱말 어간(6자) → 그 진술 문장(순수)."""
    stmt, other = {}, set()
    for s in _sentences(_unescape(src)):
        if _STMT_VERB_RE.search(s) and _STMT_SUBJ_RE.search(s):
            for st in _stems(s):
                stmt.setdefault(st, s.strip())
        else:
            other |= _stems(s)
    return {k: v for k, v in stmt.items() if k not in other}


def statement_lines(pairs, src):
    """③ 줄(순수). `pairs` = [(영문 슬롯, 영문 산문, 사실 칸 여부, 국문 짝 슬롯|None, 국문 산문|None)]."""
    only = statement_only_stems(src)
    out = []
    if not only:
        return out
    for slot, text, fact, ko_slot, ko_text in pairs:
        if not fact or _STMT_MARK_EN.search(text):
            continue
        hits = sorted(st for st in _stems(text) if st in only)
        if not hits:
            continue
        sent = only[hits[0]]
        i = sent.lower().find(hits[0])
        sent = sent[max(0, i - 90):i + 70].strip()
        ko = ""
        if ko_slot and ko_text and not _STMT_MARK_KO.search(ko_text):
            ko = f" · 국문 짝 {ko_slot} 도 진술 표기 없음"
        out.append(f"{slot}: '{', '.join(hits)}…' — 원문은 직원 진술로만 적었다(\"{sent[:160]}\"). "
                   f"사실로 단정하지 말고 '진술'·'인정' 을 붙인다(STYLE R3){ko}")
    return out


# ④ 원문에 없는 날짜 — 카드 문장 속 날짜(YYYY-MM-DD·YYYY년 M월 D일·Month D, YYYY)가 그 카드 원문(handoff 행·
#   스캐폴드 facts 의 실사일·발행일·심층 전문)에 어떤 꼴로도 없으면 막는다. 원문 쪽은 넓게 본다(같은 연도 앞뒤 40자 안에
#   그 월·일이 있으면 있다고 본다 — '2nd to 7th March 2026'·'12SEP2024'·'09/20/2024'·'2026. 11. 23.' 모두 잡는다).
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_MON3 = tuple(m[:3] for m in _MONTHS)
_MON_RE = "|".join(_MON3)
_ISO_RE = re.compile(r"(?<!\d)((?:19|20)\d\d)-(\d{1,2})-(\d{1,2})(?!\d)")
_KO_DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d\d)년\s?(\d{1,2})월\s?(\d{1,2})일")
_EN_MDY_RE = re.compile(r"\b(" + _MON_RE + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d\d)\b", re.I)
_EN_DMY_RE = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MON_RE + r")[a-z]*\.?,?\s+((?:19|20)\d\d)\b", re.I)
_DATE_WINDOW = 40


def card_dates(text):
    """산문 → [(표기, 연, 월, 일)](순수)."""
    out = []
    for m in _ISO_RE.finditer(text):
        out.append((m.group(0), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _KO_DATE_RE.finditer(text):
        out.append((m.group(0), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _EN_MDY_RE.finditer(text):
        out.append((m.group(0), int(m.group(3)), _MON3.index(m.group(1).lower()[:3]) + 1, int(m.group(2))))
    for m in _EN_DMY_RE.finditer(text):
        out.append((m.group(0), int(m.group(3)), _MON3.index(m.group(2).lower()[:3]) + 1, int(m.group(1))))
    return [d for d in out if 1 <= d[2] <= 12 and 1 <= d[3] <= 31]


def date_in_source(y, mo, d, src):
    """날짜가 원문에 어떤 꼴로든 있는지(순수)."""
    if any(c in src for c in (f"{y}{mo:02d}{d:02d}", f"{d:02d}{mo:02d}{y}")):
        return True
    yy = f"{y % 100:02d}"   # 두 자리 연도 '8/16/25'(483 관찰 본문)
    if re.search(rf"(?<!\d)(?:0?{mo}[/.\-]0?{d}|0?{d}[/.\-]0?{mo})[/.\-]{yy}(?!\d)", src):
        return True
    if re.search(rf"(?i)(?<!\d)0?{d}[\s\-/.]?{_MON3[mo - 1]}[a-z]*[\s\-/.,]*(?:{y}|{yy})(?!\d)", src):
        return True   # '14FEB23'·'04Jun24'·'12-Sep-2023'
    mon_re = re.compile(r"(?i)" + _MON3[mo - 1] + r"|(?<!\d)0?" + str(mo) + r"(?!\d)")
    day_re = re.compile(r"(?<!\d)0?" + str(d) + r"(?:st|nd|rd|th)?(?!\d)")
    for m in re.finditer(r"(?<!\d)" + str(y) + r"(?!\d)", src):
        win = src[max(0, m.start() - _DATE_WINDOW):m.start()] + " " + src[m.end():m.end() + _DATE_WINDOW]
        if mon_re.search(win) and day_re.search(win):
            return True
    return False


def day_in_source(mo, d, src):
    """연도 없이 월·일만 원문에 있는지(순수) — OCR 표에서 연도가 떨어진 줄('3382891 August 16, |')."""
    mon = _MON3[mo - 1]
    return bool(re.search(rf"(?i)\b{mon}[a-z]*\.?\s*0?{d}(?:st|nd|rd|th)?(?!\d)|(?<!\d)0?{d}(?:st|nd|rd|th)?[\s\-]?{mon}", src))


def _ocr_dates(src):
    """OCR 이 날짜 숫자 사이에 넣은 공백·'!'(=1)을 걷어 낸 사본('2023-1 1-09'·'07/1 1-20/2017'·'May !3, 2026')."""
    s = re.sub(r"(?<=[\s(])[!|](?=\d)", "1", src)
    return re.sub(r"(?<=\d) (?=\d[\d/\-])|(?<=[\d/\-]\d) (?=\d)", "", s)


# 카드 문장이 날짜에 붙인 이름표 — 이 이름표가 붙은 날짜는 코드 facts 의 같은 칸과 같아야 한다(1122b WHO 서류 평가 카드:
# '실사 기간: … 미국 FDA(2026-03-02~07)' — 코드 실사일은 2026-09-22, 다른 기관 실사 날짜를 이 보고서의 실사 기간으로 적었다).
_DATE_LABELS = (("실사일", re.compile(r"^\s*(?:실사일|실사\s?기간|실사\s?일자|Inspection\s+(?:date|period|dates))s?\s*[:：]", re.I)),
                ("발행일", re.compile(r"^\s*(?:발행일|발행\s?일자|Issue\s+date|Publication\s+date)\s*[:：]", re.I)))


def fact_date(sc, label):
    """스캐폴드 facts 의 날짜 칸 → (연, 월, 일) | None(순수). 'YYYY-MM-DD'·'MM/DD/YYYY'(483 실사일)."""
    for f in (sc.get("facts") if isinstance(sc, dict) else None) or []:
        if isinstance(f, dict) and f.get("label") == label:
            v = str(f.get("value") or "")
            m = _ISO_RE.search(v)
            if m:
                return int(m.group(1)), int(m.group(2)), int(m.group(3))
            m = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})/((?:19|20)\d\d)(?!\d)", v)
            if m:
                return int(m.group(3)), int(m.group(1)), int(m.group(2))
    return None


def date_lines(slots, src, sc=None):
    """④ (차단 줄, 경고 줄)(순수). `slots` = [(슬롯, 산문)]. 원문 = 카드 원문 전체(스캐폴드 facts 의 실사일·발행일 포함).
    날짜가 어떤 꼴로도 없으면 차단, 월·일은 있는데 그 연도가 안 보이면(OCR 이 연도를 떨군 표) 경고.
    `실사일:`·`실사 기간:`·`발행일:` 이름표로 시작하는 칸은 그 날짜가 코드 facts 의 같은 칸과 다르면 세 번째 목록(차단)에 낸다."""
    src = _unescape(src)
    norm = _ocr_dates(src)
    block, warn, mismatch = [], [], []
    for slot, text in slots:
        dates = card_dates(text)
        for shown, y, mo, d in dates:
            if date_in_source(y, mo, d, src) or date_in_source(y, mo, d, norm):
                continue
            if day_in_source(mo, d, src) or day_in_source(mo, d, norm):
                warn.append(f"{slot}: '{shown}' — 원문에 같은 월·일은 있으나 {y}년이 안 보인다. 연도를 원문과 맞춰 본다")
            else:
                block.append(f"{slot}: '{shown}' — 원문·코드 facts(실사일·발행일) 어디에도 이 날짜가 없다. "
                             f"원문 날짜를 그대로 쓰거나 날짜를 뺀다")
        for label, label_re in _DATE_LABELS:
            fd = fact_date(sc, label)
            if not (fd and dates and label_re.search(text)):
                continue
            if all((y, mo, d) != fd for _, y, mo, d in dates):
                want = f"{fd[0]}-{fd[1]:02d}-{fd[2]:02d}"
                mismatch.append(f"{slot}: '{label}' 이름표의 날짜({', '.join(s for s, *_ in dates)})가 코드 facts {label} {want} 와 다르다. "
                             f"다른 기관·다른 행사의 날짜면 이름표를 바꾼다(예: '근거로 쓴 실사: 미국 FDA …')")
    return block, warn, mismatch


def _en_pairs(card):
    """델타 카드 → [(영문 슬롯, 영문 산문, 사실 칸 여부, 국문 짝 슬롯|None, 국문 산문|None)](순수)."""
    slots = delta_slots(card)
    ko = {s: t for s, t, _ in slots if not s.startswith("en.")}
    return [(s, t, fact, s[3:] if s[3:] in ko else None, ko.get(s[3:]))
            for s, t, fact in slots if s.startswith("en.")]


def delta_source_lines(delta, rows, scaffold_cards):
    """델타 → (차단 줄, 경고 줄)(순수) — 4가지. 줄 머리: `개인 실명`·`가림 값 추측`·`원문에 없는 날짜`·`날짜 불일치`(차단),
    `개인 실명 의심`·`가림 값 추측 의심`·`진술 단정 의심`·`날짜 연도 확인`(경고).
    handoff 행이 없는 카드는 원문 전체를 모르므로 ②④ 는 경고로 낮춘다(스캐폴드만으로는 원문이 잘려 있을 수 있다)."""
    block, warn = [], []
    cards = delta.get("cards") if isinstance(delta, dict) and isinstance(delta.get("cards"), dict) else {}
    for cid, card in cards.items():
        row, sc = rows.get(cid), scaffold_cards.get(cid)
        if not isinstance(card, dict) or not (row or sc):
            continue
        src = card_source(row, sc)
        slots = [(s, t) for s, t, _ in delta_slots(card)]
        pairs = _en_pairs(card)
        b1, w1 = person_firm_lines(slots, row, sc)
        b2, w2 = redaction_fill_lines([(s, t, f, k) for s, t, f, k, _ in pairs], src)
        w3 = statement_lines(pairs, src)
        b4, w4, m4 = date_lines(slots, src, sc)
        block.extend(f"개인 실명 {cid}.{x}" for x in b1)
        block.extend(f"날짜 불일치 {cid}.{x}" for x in m4)
        for head, b in (("가림 값 추측", b2), ("원문에 없는 날짜", b4)):
            (block if row else warn).extend(f"{head}{'' if row else ' 의심'} {cid}.{x}" for x in b)
        warn.extend(f"개인 실명 의심 {cid}.{x}" for x in w1)
        warn.extend(f"가림 값 추측 의심 {cid}.{x}" for x in w2)
        warn.extend(f"진술 단정 의심 {cid}.{x}" for x in w3)
        warn.extend(f"날짜 연도 확인 {cid}.{x}" for x in w4)
    return list(dict.fromkeys(block)), list(dict.fromkeys(warn))


def deep_source_lines(shard, rows, scaffold_cards=None):
    """심층 샤드 → (차단 줄, 경고 줄)(순수) — ① 개인 실명(차단)·④ 원문에 없는 날짜(사실 칸 — **경고**).
    심층 원문은 483 전문 OCR 이라 날짜 글자가 깨진다('088AUG2022' — 리허설 5회차 실측 거짓 차단 원인) — 날짜는 교열이 근거로 판정한다.
    ②③ 은 영문 짝이 없는 국문 칸이라 낱말로 못 센다 — 델타 칸과 교열이 본다."""
    scaffold_cards = scaffold_cards or {}
    items = shard.get("items") if isinstance(shard, dict) and isinstance(shard.get("items"), dict) else shard
    block, warn = [], []
    for cid, entry in (items or {}).items() if isinstance(items, dict) else ():
        row = rows.get(cid)
        if not row:
            continue
        sc = scaffold_cards.get(cid)
        ds = deep_slots(entry)
        b1, w1 = person_firm_lines([(f"deep.{s}", t) for s, t, _, _ in ds], row, sc)
        b4, w4, m4 = date_lines([(f"deep.{s}", t) for s, t, f, _ in ds if f], card_source(row, sc), sc)
        block.extend(f"개인 실명 {cid}.{x}" for x in b1)
        warn.extend(f"원문에 없는 날짜 의심 {cid}.{x}" for x in b4)
        warn.extend(f"날짜 불일치 의심 {cid}.{x}" for x in m4)
        warn.extend(f"개인 실명 의심 {cid}.{x}" for x in w1)
        warn.extend(f"날짜 연도 확인 {cid}.{x}" for x in w4)
    return list(dict.fromkeys(block)), list(dict.fromkeys(warn))
