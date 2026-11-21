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
