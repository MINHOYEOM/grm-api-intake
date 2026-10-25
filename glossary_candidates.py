#!/usr/bin/env python3
"""용어사전 후보 추출 — 최근 브리프에 나왔는데 사전에 없는 용어 (마케팅 2026-09-28).

매주 용어 추가 예약작업(세션)이 첫 단계로 부른다. 이 스크립트는 **후보만** 낸다 — 무엇을
실제로 싣고 어떻게 정의할지는 세션이 공식 출처를 읽고 정한다(정의를 여기서 만들지 않는다).

후보의 근거는 브리프가 **스스로 병기한 짝**이다. 카드 본문은 낯선 용어에 원어를 괄호로 달아
둔다 — `총유핵세포(TNC)`, `동종(allogeneic)`, `Container Closure Integrity (CCI)`. 이 짝은
"이 주에 독자가 만난, 풀이가 필요한 말"이라는 뜻이고 표제어(국문)와 영문 표기가 함께 온다.

  1) 한글 + (영문)  : `([가-힣][가-힣·]*(?: [가-힣·]+){0,3})\\(([A-Za-z][^()]{0,60})\\)`
  2) 영문구 + (약어): `Title Case Words (ABC)`
  3) 이미 사전에 있는 말은 뺀다 — term_ko·term_en·동의어·괄호 속 약어와 **정규화 비교**
     (대소문자·공백·하이픈·가운뎃점 무시).
  4) 기관·지명·업체로 보이는 말은 뺀다(규제 용어가 아니다) — 사전식 목록이 아니라 형태로
     거른다: 법인 접미사(Inc/Ltd/Co./GmbH…)·기관 약어 집합·전부 숫자.

순위: 나온 카드 수 → 나온 주 수 → 첫 등장 최신순. 결정론(같은 입력 → 같은 출력).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_BRIEFS = REPO_ROOT / "web" / "data" / "briefs"
DEFAULT_GLOSSARY = REPO_ROOT / "web" / "data" / "glossary.json"

KO_EN = re.compile(r"([가-힣][가-힣·]*(?: [가-힣·]+){0,3})\(([A-Za-z][^()\n]{0,60})\)")
EN_ACR = re.compile(r"((?:[A-Z][a-z]+|[A-Z]{2,})(?:[ \-](?:[A-Z][a-z]+|[a-z]{2,3}|[A-Z]{2,})){1,6}) \(([A-Z][A-Za-z0-9\-]{1,9})\)")

# 규제 용어가 아닌 괄호 병기 — 기관·국가·법인. 목록은 짧게, 나머지는 세션이 거른다.
AGENCIES = {
    "fda", "ema", "mhra", "mfds", "pic/s", "pics", "ich", "who", "eu", "us", "usa", "uk", "hc",
    "cder", "cber", "cdrh", "ora", "pmda", "tga", "anvisa", "nmpa", "edqm", "eca", "ispe",
}
CORP = re.compile(r"\b(inc|ltd|llc|co|corp|gmbh|ag|s\.?a|pvt|plc|limited|company|pharma(ceuticals?)?)\b\.?", re.I)
# 한글 쪽이 조사·일반어로 끝나 표제어가 못 되는 경우(“…에 대한(regarding)” 같은 것)
KO_STOP_TAIL = ("에", "의", "를", "을", "은", "는", "이", "가", "로", "과", "와", "및", "등")


def norm(s: str) -> str:
    return re.sub(r"[\s\-·‧・_/.,]+", "", str(s or "")).lower()


def covered_keys(glossary: list[dict[str, Any]]) -> set[str]:
    """사전이 이미 가진 이름들(정규화). 괄호 속 약어와 괄호를 뺀 나머지도 이름이다."""
    keys: set[str] = set()
    for t in glossary:
        names = [t.get("term_ko", ""), t.get("term_en", "")] + list(t.get("aliases") or [])
        for n in names:
            n = str(n or "")
            if not n:
                continue
            keys.add(norm(n))
            for inner in re.findall(r"\(([^)]+)\)", n):
                keys.add(norm(inner))
            outer = re.sub(r"\([^)]*\)", " ", n).strip()
            if outer:
                keys.add(norm(outer))
    keys.discard("")
    return keys


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _is_noise(ko: str, en: str) -> bool:
    e = en.strip()
    if norm(e) in {norm(a) for a in AGENCIES} or CORP.search(e) or re.fullmatch(r"[\d\s.,%\-]+", e):
        return True
    if ko and ko.endswith(KO_STOP_TAIL) and len(ko) <= 3:
        return True
    return False


# 표제어 앞에 딸려 온 어절 판정. 조사 한 글자는 명사 끝 글자와 겹친다(결과·온도·평가·부서)
# — 그래서 **두 글자 이상 어미·조사**는 언제나 조각이고, **한 글자**는 떼고 남는 말이 두 글자
# 이상일 때만 조각으로 본다(`귀사의`→조각, `결과`→명사). 어절 자체가 조사 한 글자면 조각.
_STOP_LONG = ("까지", "부터", "에서", "으로", "하고", "하여", "하거나", "거나", "따라", "위한",
              "되는", "하는", "하기", "대한")
_STOP_SHORT = ("을", "를", "은", "는", "의", "에", "과", "와", "이", "가", "도", "만", "로", "고",
               "며", "된", "한")


def _is_fragment(tok: str) -> bool:
    if tok in _STOP_SHORT or tok in ("및", "등", "각종", "귀사", "해당", "관련", "모든", "기타"):
        return True
    if tok.endswith(_STOP_LONG):
        return True
    return tok.endswith(_STOP_SHORT) and len(tok) - 1 >= 2


def trim_ko(ko: str) -> str:
    """`귀사의 품질부서` → `품질부서`, `를 준수하고 확인` → `확인`. 뒤에서부터 어절을 모으다가
    문장 조각인 어절을 만나면 멈춘다(그 어절부터 앞은 표제어가 아니다). 최대 세 어절."""
    kept: list[str] = []
    for tok in reversed(ko.split()):
        if _is_fragment(tok) or len(kept) == 3:
            break
        kept.append(tok)
    return " ".join(reversed(kept))


# ★정규식은 반드시 raw 문자열로 — 비-raw 로 쓰면 단어 경계가 백스페이스(0x08)가 돼 조용히
# 아무것도 안 맞는다(2026-09-28 이 파일에서 실제로 밟았다).
_ID_TAIL = re.compile(r",\s*(?:FEI|DUNS|NDC|ANDA|NDA)\b.*$", re.I)


def clean_en(en: str) -> str:
    """`Outsourcing Facility, FEI 3011430551` → `Outsourcing Facility` — 식별번호 꼬리를 뗀다."""
    return _ID_TAIL.sub("", en.strip()).strip().rstrip(".,;:")


# 영문구 앞에 딸려 온 문장 머리(See the …·In …) — 대문자로 시작해도 용어가 아니다.
_LEAD_WORDS = {"See", "The", "A", "An", "In", "On", "For", "And", "Of", "To", "This", "That",
               "Per", "Under", "With", "Its", "Our", "Your"}


def trim_en_phrase(full: str) -> str:
    toks = full.split()
    while toks and (toks[0] in _LEAD_WORDS or toks[0].islower()):
        toks.pop(0)
    return " ".join(toks)


def extract_pairs(text: str) -> list[tuple[str, str]]:
    """본문 한 조각 → (국문, 영문) 짝 목록. 영문구+약어 짝은 (약어 풀이, 약어) 로 국문 자리가 빈다."""
    out: list[tuple[str, str]] = []
    for ko, en in KO_EN.findall(text):
        ko = trim_ko(ko)
        en = clean_en(en)
        if not ko or re.search(r"\d{5,}", en):
            continue
        if not _is_noise(ko, en):
            out.append((ko, en))
    for full, acr in EN_ACR.findall(text):
        full = trim_en_phrase(full)
        if len(full.split()) >= 2 and not _is_noise("", acr) and not _is_noise("", full):
            out.append(("", f"{full} ({acr})"))
    return out


def load_recent_briefs(briefs_dir: Path, weeks: int) -> list[dict[str, Any]]:
    files = sorted(briefs_dir.glob("brief_web_*.json"))[-weeks:]
    docs = []
    for p in files:
        try:
            docs.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"::warning::{p.name} 건너뜀({type(exc).__name__})", file=sys.stderr)
    return docs


def candidates(briefs: list[dict[str, Any]], glossary: list[dict[str, Any]], top: int = 20) -> list[dict[str, Any]]:
    have = covered_keys(glossary)
    agg: dict[str, dict[str, Any]] = {}
    for doc in briefs:
        pub = str((doc.get("brief") or {}).get("publish_date") or "")
        for card in doc.get("cards") or []:
            seen_in_card: set[str] = set()
            text = "\n".join(_strings({k: v for k, v in card.items() if k not in ("sources", "en", "id")}))
            for ko, en in extract_pairs(text):
                en_core = re.sub(r"\s*\([^)]*\)$", "", en) if not ko else en
                acr = re.findall(r"\(([^)]+)\)$", en)
                names = [n for n in (ko, en, en_core, *(acr or [])) if n]
                if any(norm(n) in have for n in names):
                    continue
                key = norm(ko) or norm(en)
                if key in seen_in_card:
                    continue
                seen_in_card.add(key)
                a = agg.setdefault(key, {"term_ko": ko, "term_en": en, "cards": 0, "weeks": set(),
                                         "latest": "", "examples": []})
                a["cards"] += 1
                a["weeks"].add(pub)
                a["latest"] = max(a["latest"], pub)
                if len(a["examples"]) < 2:
                    a["examples"].append({"week": pub, "card": str(card.get("title_issue") or "")[:90]})
    rows = [{**a, "weeks": len(a["weeks"])} for a in agg.values()]
    # 안정 정렬 세 번 — 마지막 키가 1순위: 카드 수 → 주 수 → 최신 등장 → 이름
    rows.sort(key=lambda r: r["term_ko"] or r["term_en"])
    rows.sort(key=lambda r: r["latest"], reverse=True)
    rows.sort(key=lambda r: (-r["cards"], -r["weeks"]))
    return rows[:top]


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="최근 브리프에 나왔는데 용어사전에 없는 용어 후보")
    ap.add_argument("--briefs", type=Path, default=DEFAULT_BRIEFS)
    ap.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    ap.add_argument("--weeks", type=int, default=4, help="최근 몇 호를 볼지(기본 4)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true", help="JSON 으로 출력")
    args = ap.parse_args(argv)
    glossary = json.loads(args.glossary.read_text(encoding="utf-8"))
    rows = candidates(load_recent_briefs(args.briefs, args.weeks), glossary, args.top)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"후보 {len(rows)}개 (최근 {args.weeks}호 · 사전 {len(glossary)}어 제외)")
    for i, r in enumerate(rows, 1):
        name = f"{r['term_ko']} ({r['term_en']})" if r["term_ko"] else r["term_en"]
        ex = " / ".join(f"{e['week']} {e['card']}" for e in r["examples"])
        print(f"{i:2d}. {name} — 카드 {r['cards']} · {r['weeks']}주 · 예: {ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
