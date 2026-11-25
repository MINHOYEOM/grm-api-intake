#!/usr/bin/env python3
"""업체 그룹 묶음(2026-11-23 대표 결정) — 등록 원천 읽기·카드 판정·등록 후보 경고.

배경: 11-23 호에 Qilu 그룹 483 카드 5장(시설 4곳)이 목록에 흩어져 실려 "같은 회사가
세 번 나왔다"로 읽혔다. 대표 결정 — 카드는 시설(FEI)·문서별로 그대로 두고, 화면에서
그룹 머리말로 묶어 나란히 보여 준다. 옛 실사가 한꺼번에 공개된 것은 실사일을 눈에 띄게.

판정 원칙(불가침):
  * 묶음은 **등록된 근거로만** 정한다 — ① `web/data/firm_groups.json` 에 근거와 함께 등록된
    그룹(FEI 단위), ② 같은 FEI(같은 시설 — FDA 가 부여한 식별자 자체가 근거).
    이름이 비슷하다는 것만으로는 절대 묶지 않는다.
  * 이름 계열(`family_token`)은 **후보를 알려 주는 데만** 쓴다(`candidate_lines`) —
    조립(assemble_publish_brief)과 편집팀 사전 점검(precheck terms)이 같은 함수를 부른다.
  * 카드 데이터는 바꾸지 않는다. 화면 묶음·머리말은 렌더(web/render.py)가 이 모듈로 정한다.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

REGISTRY_FILE = Path(__file__).resolve().parent / "web" / "data" / "firm_groups.json"

# [기준 근거 2026-11-23] 발행본 전 호의 FDA 483 카드 488장에서 (FDA 게시일 − 실사일) 을 쟀다:
# 중앙값 280일 · 75 백분위 725일 · 90 백분위 1,278일. 483 은 실사 뒤 몇 달 지나 올라오는 게
# 보통이라(메모: 483 기준은 게시 시점) 1년 기준은 절반 가까이(44%)가 걸려 표시가 의미를 잃는다.
# 75 백분위(≈2년)를 넘는 카드만 '옛 실사'로 표시한다 — 11-23 호 Qilu 5장은 전부 2년 넘음.
OLD_INSPECTION_DAYS = 730

_FEI_RE = re.compile(r"\bFEI\s*[:#]?\s*(\d{7,10})\b")
_FEI_ONLY_RE = re.compile(r"^\d{7,10}$")
_INSP_LABELS = ("실사일",)
_PUB_LABELS = ("발행일",)


class RegistryError(ValueError):
    """등록 원천 형식 위반."""


# ─────────────────────────────────────────────────────────────────────────────
# 등록 원천
# ─────────────────────────────────────────────────────────────────────────────
def validate_registry(data: Any) -> list[str]:
    """등록 원천 형식 검사 — 위반 목록(빈 목록 = 통과). 근거 없는 등록을 막는 관문."""
    errs: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        return ["groups 목록이 없다"]
    seen_keys: set[str] = set()
    seen_fei: dict[str, str] = {}
    for g in data["groups"]:
        key = str((g or {}).get("key") or "")
        if not key:
            errs.append("key 없는 그룹")
            continue
        if key in seen_keys:
            errs.append(f"{key}: key 중복")
        seen_keys.add(key)
        for f in ("name_ko", "name_en", "basis", "registered_at", "registered_by"):
            if not str(g.get(f) or "").strip():
                errs.append(f"{key}: {f} 비어 있음")
        members = g.get("members") or []
        if len(members) < 2:
            errs.append(f"{key}: 시설이 2곳 미만 — 그룹이 아니다")
        for m in members:
            fei = str((m or {}).get("fei") or "")
            if not _FEI_ONLY_RE.match(fei):
                errs.append(f"{key}: FEI 형식 위반 {fei!r}")
                continue
            if fei in seen_fei:
                errs.append(f"{key}: FEI {fei} 가 {seen_fei[fei]} 에도 있다")
            seen_fei[fei] = key
            for f in ("legal_name", "address"):
                if not str(m.get(f) or "").strip():
                    errs.append(f"{key}/{fei}: {f} 비어 있음(원문 근거 필수)")
            ev = m.get("evidence") or []
            if not (isinstance(ev, list) and any(str(e).strip() for e in ev)):
                errs.append(f"{key}/{fei}: evidence 비어 있음(원문 근거 필수)")
    return errs


def load_registry(path: "Path | str | None" = None) -> dict[str, Any]:
    p = Path(path) if path else REGISTRY_FILE
    if not p.is_file():
        return {"groups": []}
    data = json.loads(p.read_text(encoding="utf-8"))
    errs = validate_registry(data)
    if errs:
        raise RegistryError("업체 그룹 등록 원천 위반:\n  - " + "\n  - ".join(errs))
    return data


@lru_cache(maxsize=1)
def default_registry() -> dict[str, Any]:
    return load_registry()


def fei_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(m["fei"]): g for g in registry.get("groups") or [] for m in g.get("members") or []}


# ─────────────────────────────────────────────────────────────────────────────
# 카드 판정
# ─────────────────────────────────────────────────────────────────────────────
def _facts(card: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in (card.get("facts") or []) if isinstance(f, dict)]


def card_fei(card: dict[str, Any]) -> str:
    """카드의 FEI — `fei` 칸, 없으면 facts 값의 'FEI 1234567' 표기. 없으면 ''."""
    v = str(card.get("fei") or "").strip()
    if _FEI_ONLY_RE.match(v):
        return v
    for f in _facts(card):
        m = _FEI_RE.search(str(f.get("value") or ""))
        if m:
            return m.group(1)
    return ""


def card_firm_name(card: dict[str, Any]) -> str:
    for f in _facts(card):
        lab = str(f.get("label") or "")
        if "제조소" in lab or "업체" in lab:
            return _FEI_RE.sub("", str(f.get("value") or "")).strip(" ·")
    return str(card.get("headline_target") or "").strip()


def doc_kind(card: dict[str, Any]) -> str:
    """머리말 건수용 문서 종류 — '483' · 'WL' · 그 밖은 card_type 원문."""
    tag = str(card.get("type_tag") or "")
    if tag == "483" or str(card.get("id") or "").startswith("fda483-"):
        return "483"
    if "Warning Letter" in tag or str(card.get("card_type") or "") == "Warning Letter":
        return "WL"
    return str(card.get("card_type") or tag or "")


def cluster_key(card: dict[str, Any], idx: "dict[str, dict[str, Any]] | None" = None) -> str:
    """묶음 열쇠 — 등록 그룹이면 'group:<key>', 아니면 FEI 가 있으면 'fei:<FEI>', 없으면 ''."""
    fei = card_fei(card)
    if not fei:
        return ""
    idx = fei_index(default_registry()) if idx is None else idx
    g = idx.get(fei)
    return f"group:{g['key']}" if g else f"fei:{fei}"


def _parse_date(s: str) -> "_dt.date | None":
    s = (s or "").strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
    try:
        if m:
            return _dt.date(int(m[3]), int(m[1]), int(m[2]))
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return _dt.date.fromisoformat(s)
    except ValueError:
        return None
    return None


def _fact(card: dict[str, Any], labels: Iterable[str]) -> str:
    for f in _facts(card):
        if str(f.get("label") or "") in labels:
            return str(f.get("value") or "")
    return ""


def inspection_dates(card: dict[str, Any]) -> "tuple[_dt.date, _dt.date] | None":
    """(실사일, 게시일) — 둘 다 읽혀야 한다. 실사일이 게시일보다 뒤거나 20년 넘게 앞서면
    (원문 오기 — 예: 11-23 호 '04/16/0226') 판정하지 않는다."""
    insp = _parse_date(_fact(card, _INSP_LABELS))
    pub = _parse_date(_fact(card, _PUB_LABELS))
    if not insp or not pub:
        return None
    lag = (pub - insp).days
    if lag < 0 or lag > 365 * 20:
        return None
    return insp, pub


def is_old_inspection(card: dict[str, Any]) -> bool:
    d = inspection_dates(card)
    return bool(d and (d[1] - d[0]).days > OLD_INSPECTION_DAYS)


# ─────────────────────────────────────────────────────────────────────────────
# 등록 후보 경고(이름 계열은 후보 제시에만)
# ─────────────────────────────────────────────────────────────────────────────
# 이름 첫머리에 흔히 오는 지명·일반어 — 계열 토큰으로 쓰지 않는다(Jiangsu Hengrui → hengrui).
_FAMILY_STOP = frozenset("""
the a an new american international national general global united first royal
shanghai beijing jiangsu zhejiang shandong guangdong guangzhou shenzhen hainan sichuan hubei hunan
henan hebei anhui fujian jilin liaoning tianjin chongqing suzhou hangzhou nanjing wuhan jinan
taizhou zhuhai xiamen yunnan shanxi shaanxi jiangxi guangxi inner mongolia
china chinese india indian japan korea korean taiwan usa us europe european
pharma pharmaceutical pharmaceuticals laboratories laboratory labs lab health healthcare
medical bio biotech biologics life science sciences company co corp corporation inc ltd llc
limited private pvt group holdings industries
""".split())
_TOKEN_RE = re.compile(r"[a-z][a-z0-9]+")


def family_token(name: str) -> str:
    """이름 계열 토큰 — 지명·일반어를 건너뛴 첫 라틴 단어(3자 이상). 없으면 ''.
    ★후보 제시에만 쓴다 — 묶음 판정에 쓰면 안 된다."""
    for t in _TOKEN_RE.findall((name or "").lower()):
        if len(t) >= 3 and t not in _FAMILY_STOP:
            return t
    return ""


def _identity_card(card: dict[str, Any]) -> bool:
    """업체·시설 단위 기록 카드인가(실사·경고서한 등) — 회수·지침은 후보 대상이 아니다."""
    return bool(card_fei(card) or card.get("inspection") or doc_kind(card) in ("483", "WL"))


def candidate_groups(cards: list[dict[str, Any]],
                     registry: "dict[str, Any] | None" = None) -> list[dict[str, Any]]:
    """한 호 안에서 같은 이름 계열인데 한 묶음(등록 그룹·같은 FEI)으로 다 덮이지 않는 카드 무리."""
    idx = fei_index(registry if registry is not None else default_registry())
    fam: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        if not _identity_card(c):
            continue
        tok = family_token(card_firm_name(c))
        if tok:
            fam.setdefault(tok, []).append(c)
    out: list[dict[str, Any]] = []
    for tok, cs in sorted(fam.items()):
        if len(cs) < 2:
            continue
        keys = {cluster_key(c, idx) or f"id:{c.get('id')}" for c in cs}
        if len(keys) < 2:
            continue                                   # 이미 한 묶음(등록 그룹 또는 같은 FEI)
        out.append({"family": tok, "cards": cs})
    return out


def candidate_lines(cards: list[dict[str, Any]],
                    registry: "dict[str, Any] | None" = None) -> list[str]:
    """'그룹 등록 후보' 경고 줄(막지 않음). 조립·사전 점검이 같은 함수를 부른다."""
    lines = []
    for cand in candidate_groups(cards, registry):
        parts = []
        for c in cand["cards"]:
            fei = card_fei(c)
            parts.append(f"{c.get('id')}({card_firm_name(c)}{' · FEI ' + fei if fei else ''})")
        lines.append(
            f"그룹 등록 후보: '{cand['family']}' 계열 {len(cand['cards'])}장이 한 묶음으로 보이지 않는다 — "
            + ", ".join(parts)
            + " — 같은 그룹이면 원문 업체명·주소 근거와 함께 web/data/firm_groups.json 에 등록한다"
              "(이름만 비슷하면 등록하지 않는다)")
    return lines


def main(argv: "list[str] | None" = None) -> int:
    """후보 제시 도구: python firm_groups.py <brief_web_*.json>..."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    args = sys.argv[1:] if argv is None else argv
    reg = default_registry()
    print(f"등록 그룹 {len(reg.get('groups') or [])}개 — {REGISTRY_FILE}")
    for p in args:
        b = json.loads(Path(p).read_text(encoding="utf-8"))
        lines = candidate_lines(b.get("cards") or [], reg)
        print(f"{p}: 후보 {len(lines)}건")
        for ln in lines:
            print("  " + ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
