#!/usr/bin/env python3
"""내용 완전성 감시 — "모은 내용이 잘리지 않았고, 모은 칸이 어딘가에 쓰이는가"를 센다.

왜 있나
  원천 대조(source_reconcile.py)는 **건수**만 본다. 문서가 들어오기만 하면 내용이 반쪽이어도 정상이다.
  실제로 두 번 있었다.
  ① FDA 483 관찰 상세(`fda_483_observations[].detail`)를 수집기가 1,200자에서 잘라 저장했고, 사이트는 표제
     한 줄만 보여 줬다(상세가 원자료에만 있었다 — 2026-11-10 지식 그래프 판단).
  ② FDA 경고서한 위반 블록을 옛 표시 상한 480자에서 "…" 로 잘라 findings 에 저장한 행이 남았다.
  이 감시는 **모든 소스의 모든 칸**을 자동으로 훑는다. 칸 목록을 손으로 적지 않으므로(원자료 JSON 을 재귀로
  편다) 새 소스·새 칸이 들어와도 저절로 검사된다.

검사 두 가지
  (T) 잘림 — 같은 소스·같은 칸에서 **서로 다른** 긴 값(≥200자)이 한 길이대(상한 L 의 3% 폭)에 몰리고, 그 값들이
      문장 끝 부호 없이(또는 "…"로) 끝나며, 바로 아래 같은 폭보다 5배 이상 많으면 "상한 L 에서 잘림"으로 본다.
      서로 다른 값만 세므로 정형 문구(같은 문장 반복)는 몰림으로 안 잡힌다. 수집 코드의 `*_MAX_CHARS`/`*_CAP`
      상수와 L 이 맞으면 그 상수를 함께 적는다(어디서 잘렸는지).
  (U) 미사용 — 산문 칸(80자 이상 값이 10개 이상, 중앙값 150자 이상)이 어디에 쓰이는지 본다.
      * 지적사항(findings)을 만드는 소스는 **데이터로** 잰다: 그 칸 값의 한 조각이 같은 원자료에서 나온 findings
        본문(원문·국문)에 들어 있는 비율(도달률). 코드가 칸 이름을 읽어도 화면에 안 닿으면 안 쓰인 것이다(①이
        정확히 이 경우였다 — 코드는 `detail` 을 읽었지만 사이트엔 표제만 나갔다).
      * 그 밖의 소스는 **코드로** 본다: 사이트 계층(web/·supabase/·findings_*·kg_*) 또는 브리프 조립 파일이
        칸 이름을 문자열로 읽는지.
      경보: 아무도 안 읽는 칸 / findings 소스인데 도달률 5% 미만인 긴 칸(중앙값 300자 이상).

의도된 것은 경보하지 않는다 — `INTENTIONAL_CAPS`(설계상 발췌)·`INTENTIONAL_UNSHOWN`(추출 원료·정형 문구)에
  이유와 함께 적는다. 표에 없는 새 칸은 기본이 경보다(사람이 판단해 표에 올리거나 고친다).
  원문 자체가 상한 언저리에서 끝나는 개별 값은 `INTENTIONAL_SOURCE_ENDS`(문서·관찰 번호)로 잘림 집계에서만 뺀다.

읽기 전용: Supabase REST GET 만 한다. 데이터는 고치지 않는다(찾은 것은 목록·이슈로 알린다).
비차단: 이상이 있어도 exit 0. 이슈 계획은 source_reconcile 와 같은 모양(`issues`)으로 JSON 에 싣는다.
서비스키는 로그·예외 문구에 싣지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parent

# ─────────────────────────────────────────────────────────────────────────────
# 기준값 — 2026-11-10 실데이터(raw_signals 8,796행·findings 28,495행)로 맞췄다.
#   잘림: 483 상세 1,200자(몰림 2,100 vs 아래 32)·WL findings 480자(1,747 vs 6) 가 뚜렷이 잡히고,
#         정형 문구가 많은 HC·483 표제(같은 길이에 수백 행)는 서로 다른 값만 세서 0건.
#   미사용: 483 상세 도달률 0.5%(60/11,620) vs 정상 칸(483 표제 99.9%·HC 요약 100%·WL 위반 93%).
# ─────────────────────────────────────────────────────────────────────────────
MIN_LEN = 200            # 잘림 검사 대상 최소 길이(짧은 메타 칸 제외)
BAND_FRAC = 0.03         # 상한 아래 몰림 폭(단어 경계에서 자르면 몇 자 앞에서 끝난다)
MIN_HITS = 10            # 몰린 서로 다른 값의 최소 개수
MIN_RATIO = 5.0          # 바로 아래 같은 폭 대비 배수
MAX_CAPS_PER_FIELD = 3   # 한 칸에 상한이 여러 개일 수 있다(옛 상한·새 상한)
RECENT_DAYS = 30         # 이 기간 안에 수집된 행이 잘렸으면 "지금도 잘리는 중"
TAIL_LEN = 20            # 끝 꼬리 길이
TEMPLATE_TAIL_MIN = 20   # 서로 다른 값 이만큼이 같은 꼬리로 끝나면 정형 끝(꼬리말)이지 잘림이 아니다
                         # (WL 전문 상당수가 "…1-888-INFO-FDA (1-888-463-6332) Back to Top" 으로 끝난다)

PROSE_MIN_LEN = 80
PROSE_MIN_VALUES = 10
PROSE_MEDIAN = 150
SITE_REACH_MIN = 0.05    # findings 도달률이 이보다 낮으면 사이트에 안 닿는다
UNSHOWN_ALARM_MEDIAN = 300
REACH_MIN_SAMPLE = 10    # 도달률을 데이터로 판정하려면 findings 가 있는 원자료가 이만큼 필요
SNIPPET_FROM, SNIPPET_LEN = 10, 40

PAGE = 1000
MAX_PAGES = 200
HTTP_TIMEOUT = 60
MAX_LISTED = 40

ISSUE_TITLE = "[content-completeness] 잘린 내용·안 쓰이는 칸이 있습니다"
CRASH_ISSUE_TITLE = "[content-completeness] 내용 완전성 감시 자체가 실패했습니다"
ISSUE_LABELS = ["content-completeness"]

# 문장이 끝났다고 볼 끝 글자(영문 부호·괄호·따옴표·국문 종결어미)
_END_OK = re.compile(r"[.!?)\]\"”’'。:;」』）다음함됨임요]$")
_ELLIPSIS = re.compile(r"(…|\.\.\.)$")
_URL = re.compile(r"^https?://", re.I)
_WS = re.compile(r"\s+")

# 설계상 상한이 있는 칸(발췌). 잘림 경보에서 빼고 보고서의 "의도된 상한" 칸에만 적는다.
INTENTIONAL_CAPS: dict[tuple[str, str], str] = {
    ("FDA 483", "fda483_excerpt"): "PDF 앞부분 발췌(1,500자). 전문은 fda483_body_full·관찰 목록에 따로 있다",
    ("FDA Warning Letter", "wl_body_excerpt"): "본문 발췌(1,500자). 전문은 wl_body_full",
    ("ECA Academy", "eca_article_excerpt"): "기사 발췌(1,200자) — 남의 기사 전문은 저장하지 않는다",
    ("ISPE", "article_excerpt"): "기사 발췌(1,200자) — 남의 기사 전문은 저장하지 않는다",
    ("ISPE", "description"): "RSS 요약(800자) — 기사 발췌가 따로 있다",
    ("WHO", "whopir_excerpt"): "WHOPIR 발췌(1,500자). 보고서 구조화 값은 whopir_report",
    ("Federal Register", "excerpts"): "FR API 가 주는 검색 발췌(원천 쪽 상한). 요약은 abstract 로 쓴다",
}

# 사이트에 안 나가는 게 맞는 산문 칸(추출 원료·정형 문구). 미사용 경보에서 뺀다.
INTENTIONAL_UNSHOWN: dict[tuple[str, str], str] = {
    ("FDA 483", "fda483_excerpt"): "PDF 앞부분 발췌 — 관찰 추출 원료·브리프 근거",
    ("FDA 483", "fda483_body_full"): "PDF 전문 — 관찰 추출 원료",
    ("FDA Warning Letter", "wl_body_full"): "서한 전문 — 위반 블록 추출 원료",
    ("FDA Warning Letter", "wl_body_excerpt"): "서한 발췌 — 브리프 근거",
    ("MFDS", "attachment_text"): "GMP 실태조사 첨부 전문 — 지적 표 추출 원료",
    ("Health Canada Inspection", "ins_outcome_list[]"): "등급 판정 정형 문구(Compliant… 반복)",
    ("EU GMP NCR (EudraGMDP)", "ncr_operations"): "부적합 제조 범위 목록(정형 번호 목록)",
    ("EU GMP NCR (EudraGMDP)", "product_description"): "ncr_operations 와 같은 제조 범위 목록",
    ("Federal Register", "excerpts"): "FR 검색 발췌 — 같은 내용을 abstract 로 쓴다",
}

# 문서 단위 예외 — 값이 상한 언저리 길이에서 문장 중간에 끝나지만 **원문 자체가 거기서 끝나는** 관찰.
# 2026-11-11 진단(483 상세 다시 읽기 뒤 잘림 띠 잔존 63건을 원문과 대조): 아래 19건은 상세 끝 바로 뒤가
# 다음 관찰·양식 서명란·문서 끝이었다(표가 가림 처리로 빠진 자리·스캔 판독이 끊긴 자리 포함). 수집기 결함이
# 아니므로 잘림 몰림 집계에서만 뺀다(값은 그대로 두고, 미사용 검사에는 그대로 센다).
# 키: 소스 → {document_id: (관찰 번호, …)}. 다시 수집해 상세가 바뀌면 값 비교가 아니라 번호로 빠지므로,
# 원문을 다시 대조해 더는 해당 없으면 여기서 지운다.
INTENTIONAL_SOURCE_ENDS: dict[str, dict[str, tuple[str, ...]]] = {
    "FDA 483": {
        "fda483-85828": ("9",), "fda483-101055": ("10",), "fda483-95350": ("1",), "fda483-89608": ("7",),
        "fda483-80191": ("6",), "fda483-82026": ("4",), "fda483-163365": ("3",), "fda483-130374": ("1",),
        "fda483-93327": ("7",), "fda483-88289": ("7",), "fda483-110129": ("7",), "fda483-77353": ("17",),
        "fda483-158445": ("9",), "fda483-159977": ("6",), "fda483-89973": ("3",), "fda483-135881": ("4",),
        "fda483-195522": ("2",), "fda483-135874": ("15",), "fda483-82813": ("1",),
        # 11-12 진단(run 43067755299): 상세 바로 뒤가 서명란 → "PAGE n of n"·양식 뒷면 고지·다음 OBSERVATION.
        "fda483-195408": ("1",), "fda483-195550": ("5",), "fda483-195630": ("5",),
        "fda483-189925": ("5",), "fda483-193645": ("2",), "fda483-148650": ("3",),
    },
}
_SOURCE_ENDS_LIST_KEY = {"FDA 483": "fda_483_observations"}


# [2026-11-12] 알려진 잔여 잘림 — 옛 수집 때 1,200자에서 잘려 저장됐고, 다시 읽어도 짝을 못 찾은 관찰
# (진단 run 43067755299 · 반영 43077152298 뒤). 원문 PDF 의 OCR 판본이 달라 조각 끝을 찾을 수 없거나(16),
# 조각 끝이 원문에 반복되는데 뒤 문장이 자리마다 달라 이을 자리를 정할 수 없다(6). 대표 위임 결정(비서실):
# 저장값은 그대로 두고 **구체 내용 화면에서 "원문 일부 — 전체는 원문 PDF 에서 확인"** 을 붙인다.
# `partial_detail_notice` 가 그 판정이다 — 상세가 문장으로 끝나면(다음 재수집에서 온전해지면) 표시하지 않는다.
# 쪽 머리말·서명란 꼬리(19)는 잘림이 아니라 꼬리 정리 대상이므로 넣지 않는다.
KNOWN_PARTIAL_DETAILS: dict[str, dict[str, tuple[str, ...]]] = {
    "FDA 483": {
        # OCR 판본 차이(원문에서 조각 끝을 못 찾음)
        "fda483-110395": ("2",), "fda483-111108": ("2",), "fda483-135875": ("2",), "fda483-145539": ("1",),
        "fda483-153336": ("2",), "fda483-161809": ("1", "5"), "fda483-163788": ("4", "5", "9", "14"),
        "fda483-175123": ("3",), "fda483-190645": ("6",), "fda483-77292": ("1",), "fda483-93492": ("1",),
        "fda483-96222": ("7",), "fda483-96641": ("8",),
        # 조각 끝 반복·뒤 문장 불일치
        "fda483-107200": ("7",), "fda483-84935": ("5",), "fda483-85303": ("5",), "fda483-91684": ("2", "7"),
    },
}
_SENTENCE_END_RE = re.compile(r"[.!?][)\"'”’]?\s*$")


def partial_detail_notice(source: str, document_id: str, number: Any, detail: str) -> bool:
    """구체 내용 화면에 "원문 일부" 표시를 붙일 관찰인가(순수 함수).

    목록에 있고 **상세가 아직 문장 중간에서 끝날 때만** 참 — 재수집으로 온전해지면 저절로 빠진다."""
    numbers = KNOWN_PARTIAL_DETAILS.get(source, {}).get(document_id)
    if not numbers or str(number) not in numbers:
        return False
    d = (detail or "").strip()
    return bool(d) and not _SENTENCE_END_RE.search(d)


def source_end_values(source: str, document_id: str, raw: Any) -> set[str]:
    """INTENTIONAL_SOURCE_ENDS 에 오른 관찰의 상세 값(앞뒤 공백 제거). 순수 함수."""
    numbers = INTENTIONAL_SOURCE_ENDS.get(source, {}).get(document_id)
    list_key = _SOURCE_ENDS_LIST_KEY.get(source)
    if not numbers or not list_key or not isinstance(raw, dict):
        return set()
    return {str(o.get("detail") or "").strip() for o in raw.get(list_key) or []
            if isinstance(o, dict) and str(o.get("number")) in numbers and str(o.get("detail") or "").strip()}


# 사이트 계층으로 보는 파일(지적사항·업체·그래프 화면을 만드는 쪽)
SITE_DIRS = ("web", "supabase")
SITE_PREFIXES = ("findings_", "kg_")
# 브리프 조립 파일(주간 카드·Routine 입력)
BRIEF_FILES = ("card_scaffold.py", "inject_slots.py", "assemble_publish_brief.py", "delta_bridge.py",
               "deep_analysis_fanout.py", "inspection_card.py", "grm_handoff.py")
_CODE_EXT = {".py", ".sql", ".html", ".js", ".j2"}


# ─────────────────────────────────────────────────────────────────────────────
# 값 펴기
# ─────────────────────────────────────────────────────────────────────────────
def iter_leaves(obj: Any, path: str = "", depth: int = 0) -> Iterator[tuple[str, str]]:
    """원자료 JSON 의 문자열 잎을 (경로, 값)으로 편다. 배열은 `키[]`, 배열 안 객체는 `키[].하위키`."""
    if depth > 5:
        return
    if isinstance(obj, str):
        if path:
            yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_leaves(v, f"{path}.{k}" if path else str(k), depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_leaves(v, f"{path}[]", depth + 1)


def is_cut(text: str) -> bool:
    """문장 끝 부호 없이 끝났거나 말줄임표로 끝났다."""
    t = text.rstrip()
    return bool(_ELLIPSIS.search(t)) or not _END_OK.search(t)


def norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


@dataclass
class FieldStats:
    """한 (소스, 칸)의 값 모음 — 잘림·미사용 두 검사가 같이 쓴다."""
    source: str
    path: str
    total: int = 0
    long_cut: dict[int, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))   # 길이 → {값 해시: 끝 꼬리}
    long_rows: dict[int, list[tuple[str, str, str]]] = field(default_factory=lambda: defaultdict(list))  # 길이 → (ref, 수집일, 끝 꼬리)
    prose_lens: list[int] = field(default_factory=list)
    url_like: int = 0
    reach_pairs: list[tuple[str, str]] = field(default_factory=list)   # (raw_signal_id, 조각)

    def add(self, value: str, *, ref: str, collected: str, raw_signal_id: str = "",
            source_ends: bool = False) -> None:
        v = value.strip()
        if not v:
            return
        self.total += 1
        n = len(v)
        if n >= MIN_LEN and is_cut(v) and not source_ends:
            tail = v[-TAIL_LEN:]
            self.long_cut[n][hashlib.md5(v.encode("utf-8")).hexdigest()] = tail
            self.long_rows[n].append((ref, collected, tail))
        if n >= PROSE_MIN_LEN:
            self.prose_lens.append(n)
            if _URL.match(v):
                self.url_like += 1
            if raw_signal_id:
                nv = norm(v)
                self.reach_pairs.append((raw_signal_id, nv[SNIPPET_FROM:SNIPPET_FROM + SNIPPET_LEN]))

    def template_tails(self) -> set[str]:
        """서로 다른 값 여럿이 똑같이 끝나는 꼬리 — 페이지 꼬리말("… Back to Top") 같은 정형 끝이지 잘림이 아니다."""
        cnt: dict[str, int] = defaultdict(int)
        for by_hash in self.long_cut.values():
            for tail in by_hash.values():
                cnt[tail] += 1
        return {t for t, c in cnt.items() if c >= TEMPLATE_TAIL_MIN}

    def cut_counts(self) -> dict[int, int]:
        tmpl = self.template_tails()
        return {l: sum(1 for t in by_hash.values() if t not in tmpl) for l, by_hash in self.long_cut.items()}


# ─────────────────────────────────────────────────────────────────────────────
# (T) 잘림
# ─────────────────────────────────────────────────────────────────────────────
def band_width(cap: int) -> int:
    return max(3, math.ceil(cap * BAND_FRAC))


def find_caps(cut_by_len: dict[int, int]) -> list[dict[str, int]]:
    """길이별 '잘린 서로 다른 값' 개수 → 몰림 상한 목록. 순수 함수(시험 대상)."""
    counts = {int(k): int(v) for k, v in cut_by_len.items() if v}
    found: list[dict[str, int]] = []
    used: set[int] = set()

    def total(lo: int, hi: int) -> int:
        return sum(c for l, c in counts.items() if lo <= l <= hi and l not in used)

    for _ in range(MAX_CAPS_PER_FIELD):
        best: tuple[int, int, int] | None = None   # (hits, cap, below)
        for cap in counts:
            if cap in used:
                continue
            w = band_width(cap)
            hits = total(cap - w + 1, cap)
            below = total(cap - 2 * w + 1, cap - w)
            if best is None or (hits, cap) > (best[0], best[1]):
                best = (hits, cap, below)
        if best is None:
            break
        hits, cap, below = best
        if hits < MIN_HITS or hits < MIN_RATIO * max(below, 1):
            break
        found.append({"cap": cap, "hits": hits, "below": below, "band_from": cap - band_width(cap) + 1})
        used.update(l for l in counts if cap - band_width(cap) + 1 <= l <= cap)
    return found


def scan_code_caps(root: Path = ROOT) -> list[dict[str, Any]]:
    """루트 .py 의 길이 상한 상수(`*_MAX_CHARS`·`*_CHAR_CAP`·`*_MAX_LEN` = 정수 ≥200)."""
    pat = re.compile(r"^(_?[A-Z][A-Z0-9_]*(?:MAX_CHARS|CHAR_CAP|MAX_LEN))\s*(?::\s*int)?\s*=\s*([0-9_]+)\b", re.M)
    out = []
    for p in sorted(root.glob("*.py")):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in pat.finditer(text):
            val = int(m.group(2).replace("_", ""))
            if val >= MIN_LEN:
                line = text.count("\n", 0, m.start()) + 1
                out.append({"name": m.group(1), "value": val, "where": f"{p.name}:{line}"})
    return out


def match_code_cap(cap: int, code_caps: Iterable[dict[str, Any]]) -> list[str]:
    """몰림 상한과 맞는 코드 상수(값이 몰림 폭 안이거나 말줄임표 3자 차이)."""
    lo = cap - band_width(cap) + 1
    return [f"{c['name']}={c['value']} ({c['where']})" for c in code_caps if lo - 3 <= c["value"] <= cap + 3]


def detect_truncation(stats: Iterable[FieldStats], code_caps: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    out = []
    recent_from = (today - timedelta(days=RECENT_DAYS)).isoformat()
    for fs in stats:
        caps = find_caps(fs.cut_counts())
        tmpl = fs.template_tails() if caps else set()
        for c in caps:
            rows = [(r, d) for l in range(c["band_from"], c["cap"] + 1) for r, d, t in fs.long_rows.get(l, [])
                    if t not in tmpl]
            dates = sorted(d for _, d in rows if d)
            key = (fs.source, fs.path)
            out.append({
                "source": fs.source, "path": fs.path, "cap": c["cap"], "distinct": c["hits"],
                "below": c["below"], "rows": len(rows),
                "recent_rows": sum(1 for _, d in rows if d and d >= recent_from),
                "first_collected": dates[0][:10] if dates else "", "last_collected": dates[-1][:10] if dates else "",
                "samples": sorted({r for r, _ in rows})[:5],
                "code_caps": match_code_cap(c["cap"], code_caps),
                "intentional": INTENTIONAL_CAPS.get(key, ""),
            })
    out.sort(key=lambda x: (bool(x["intentional"]), -x["rows"]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# (U) 미사용
# ─────────────────────────────────────────────────────────────────────────────
def code_files(root: Path = ROOT) -> dict[str, list[tuple[str, str]]]:
    """{'site': [(이름, 본문)], 'brief': [...]}"""
    out: dict[str, list[tuple[str, str]]] = {"site": [], "brief": []}
    paths: list[Path] = []
    for d in SITE_DIRS:
        base = root / d
        if base.is_dir():
            paths += [p for p in base.rglob("*") if p.suffix in _CODE_EXT and "tests" not in p.parts
                      and "node_modules" not in p.parts]
    paths += [p for p in root.glob("*.py") if p.name.startswith(SITE_PREFIXES)]
    for p in paths:
        try:
            out["site"].append((str(p.relative_to(root)).replace("\\", "/"), p.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    for name in BRIEF_FILES:
        p = root / name
        if p.is_file():
            out["brief"].append((name, p.read_text(encoding="utf-8")))
    return out


def _literal_in(text: str, key: str) -> bool:
    return f'"{key}"' in text or f"'{key}'" in text


def path_keys(path: str) -> list[str]:
    """`a[].b` → ['a', 'b'] (배열 표지 제거). 최상위만 있으면 한 개."""
    return [k for k in re.split(r"\[\]\.?|\.", path) if k]


def code_readers(path: str, files: list[tuple[str, str]]) -> list[str]:
    keys = path_keys(path)
    if not keys:
        return []
    return [name for name, text in files if all(_literal_in(text, k) for k in keys)]


def classify_usage(*, source: str, path: str, prose_lens: list[int], url_like: int,
                   reach: tuple[int, int] | None, site_readers: list[str], brief_readers: list[str]) -> dict[str, Any] | None:
    """산문 칸 하나의 쓰임 판정. 산문이 아니면 None. 순수 함수(시험 대상)."""
    if len(prose_lens) < PROSE_MIN_VALUES:
        return None
    med = statistics.median(prose_lens)
    if med < PROSE_MEDIAN or url_like * 2 > len(prose_lens):
        return None
    measured = reach is not None and reach[1] >= REACH_MIN_SAMPLE
    ratio = (reach[0] / reach[1]) if measured and reach else None
    if measured:
        site = ratio is not None and ratio >= SITE_REACH_MIN
    else:
        site = bool(site_readers)
    brief = bool(brief_readers)
    status = "site" if site else ("brief_only" if brief else "unused")
    allow = INTENTIONAL_UNSHOWN.get((source, path), "")
    alarm = (not allow) and (status == "unused" or (measured and not site and med >= UNSHOWN_ALARM_MEDIAN))
    return {"source": source, "path": path, "values": len(prose_lens), "median_len": int(med),
            "status": status, "basis": "data" if measured else "code",
            "reach": None if not measured else {"hit": reach[0], "of": reach[1], "ratio": round(ratio or 0, 3)},
            "site_readers": site_readers[:5], "brief_readers": brief_readers[:5],
            "intentional": allow, "alarm": alarm}


def compute_reach(pairs: list[tuple[str, str]], findings_text: dict[str, str]) -> tuple[int, int]:
    hit = of = 0
    for rid, snip in pairs:
        ft = findings_text.get(rid)
        if ft is None or len(snip) < 15:
            continue
        of += 1
        if snip in ft:
            hit += 1
    return hit, of


# ─────────────────────────────────────────────────────────────────────────────
# 읽기 (Supabase REST GET 전용)
# ─────────────────────────────────────────────────────────────────────────────
class StoreReadError(RuntimeError):
    pass


def fetch_table(base_url: str, key: str, table: str, select: str, order: str, session: Any = None) -> Iterator[dict[str, Any]]:
    http = session or requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    url = f"{base_url.rstrip('/')}/rest/v1/{table}"
    offset = 0
    for _ in range(MAX_PAGES):
        try:
            resp = http.get(url, params={"select": select, "order": order, "limit": str(PAGE), "offset": str(offset)},
                            headers=headers, timeout=HTTP_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            raise StoreReadError(f"{table} 조회 실패: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise StoreReadError(f"{table} 조회 HTTP {resp.status_code}")
        page = resp.json()
        if not isinstance(page, list):
            raise StoreReadError(f"{table} 응답 형식 아님")
        yield from (r for r in page if isinstance(r, dict))
        if len(page) < PAGE:
            return
        offset += PAGE
    raise StoreReadError(f"{table} 이 상한({MAX_PAGES * PAGE}행)을 넘었다")


# 원자료 밖의 본문 표 — 새 표를 붙이면 여기 한 줄. (표, id 열, 소스 열 또는 '=고정값', 본문 열들)
EXTRA_TABLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("fda_inspection_citations", "citation_id", "=FDA Data Dashboard 인용", ("long_description", "short_description")),
    ("deficiency_examples", "source_key", "agency", ("statement", "context")),
)


def collect_stats(raw_rows: Iterable[dict[str, Any]], finding_rows: Iterable[dict[str, Any]],
                  extra: dict[str, list[dict[str, Any]]] | None = None) -> tuple[list[FieldStats], dict[str, str]]:
    stats: dict[tuple[str, str], FieldStats] = {}

    def fs(source: str, path: str) -> FieldStats:
        k = (source, path)
        if k not in stats:
            stats[k] = FieldStats(source, path)
        return stats[k]

    collected_at: dict[str, str] = {}
    for r in raw_rows:
        rid = str(r.get("raw_signal_id") or "")
        src = str(r.get("source") or "?")
        coll = str(r.get("collected_at") or "")
        collected_at[rid] = coll
        raw = r.get("raw_json")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip().startswith(("{", "[")) else None
            except ValueError:
                raw = None
        if raw is None:
            continue
        ends = source_end_values(src, str(r.get("document_id") or ""), raw)
        for path, val in iter_leaves(raw):
            fs(src, path).add(val, ref=rid, collected=coll, raw_signal_id=rid,
                              source_ends=bool(ends) and val.strip() in ends)

    findings_text: dict[str, list[str]] = defaultdict(list)
    for f in finding_rows:
        rid = str(f.get("raw_signal_id") or "")
        src = str(f.get("source") or "?")
        coll = collected_at.get(rid, "")
        for col in ("finding_text", "finding_text_ko", "detail_text", "detail_text_ko"):
            v = f.get(col)
            if isinstance(v, str) and v.strip():
                fs(src, f"findings.{col}").add(v, ref=str(f.get("finding_id") or rid), collected=coll)
                findings_text[rid].append(v)
    for table, id_col, src_col, cols in EXTRA_TABLES:
        for r in (extra or {}).get(table, []):
            src = src_col[1:] if src_col.startswith("=") else str(r.get(src_col) or "?")
            for col in cols:
                v = r.get(col)
                if isinstance(v, str):
                    fs(src, f"{table}.{col}").add(v, ref=str(r.get(id_col) or ""), collected=str(r.get("collected_at") or ""))
    joined = {rid: norm(" ".join(parts)) for rid, parts in findings_text.items()}
    return list(stats.values()), joined


def build_report(stats: list[FieldStats], findings_text: dict[str, str], *, today: date,
                 root: Path = ROOT, run_url: str = "") -> dict[str, Any]:
    code_caps = scan_code_caps(root)
    trunc = detect_truncation(stats, code_caps, today)
    files = code_files(root)
    usage = []
    for f in stats:
        if f.path.startswith("findings.") or f.path.split(".")[0] in {t[0] for t in EXTRA_TABLES}:
            continue      # 화면용 표 자체는 미사용 검사 대상이 아니다(잘림만 본다)
        reach = compute_reach(f.reach_pairs, findings_text) if f.reach_pairs else None
        u = classify_usage(source=f.source, path=f.path, prose_lens=f.prose_lens, url_like=f.url_like,
                           reach=reach, site_readers=code_readers(f.path, files["site"]),
                           brief_readers=code_readers(f.path, files["brief"]))
        if u:
            usage.append(u)
    usage.sort(key=lambda u: (not u["alarm"], u["status"] != "unused", u["source"], u["path"]))
    alarms_t = [t for t in trunc if not t["intentional"]]
    alarms_u = [u for u in usage if u["alarm"]]
    report = {
        "run_date": today.isoformat(), "run_url": run_url,
        "thresholds": {"min_len": MIN_LEN, "band_frac": BAND_FRAC, "min_hits": MIN_HITS, "min_ratio": MIN_RATIO,
                       "site_reach_min": SITE_REACH_MIN, "unshown_alarm_median": UNSHOWN_ALARM_MEDIAN},
        "fields_scanned": len(stats), "truncation": trunc, "usage": usage,
        "counts": {"truncated": len(alarms_t), "unused": len(alarms_u),
                   "intentional_caps": len(trunc) - len(alarms_t)},
    }
    report["issues"] = plan_issues(report)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# 보고서 · 이슈
# ─────────────────────────────────────────────────────────────────────────────
_STATUS_KO = {"site": "사이트에 나감", "brief_only": "브리프 조립만 읽음", "unused": "아무도 안 읽음"}


def _trunc_line(t: dict[str, Any]) -> str:
    now = f"최근 {RECENT_DAYS}일 {t['recent_rows']}행" if t["recent_rows"] else "최근엔 없음(옛 데이터)"
    code = f" · 코드 상한 {', '.join(t['code_caps'])}" if t["code_caps"] else ""
    return (f"| {t['source']} | `{t['path']}` | {t['cap']}자 | {t['rows']} ({t['distinct']}) | {now} "
            f"| {t['first_collected']}~{t['last_collected']} |{code} |")


def _usage_line(u: dict[str, Any]) -> str:
    r = u["reach"]
    reach = f"{r['hit']}/{r['of']} ({r['ratio']:.1%})" if r else "코드 기준"
    readers = ", ".join(u["site_readers"] + u["brief_readers"]) or "없음"
    return f"| {u['source']} | `{u['path']}` | {u['values']} | {u['median_len']} | {_STATUS_KO[u['status']]} | {reach} | {readers[:80]} |"


def render_markdown(report: dict[str, Any]) -> str:
    c = report["counts"]
    lines = [f"# 내용 완전성 감시 ({report['run_date']})", "",
             f"훑은 칸 {report['fields_scanned']}개 · **잘림 의심 {c['truncated']}칸 · 안 쓰이는 칸 {c['unused']}개** "
             f"· 의도된 상한 {c['intentional_caps']}칸", ""]
    t_alarm = [t for t in report["truncation"] if not t["intentional"]]
    if t_alarm:
        lines += ["## 잘림 의심", "", "같은 길이대에서 서로 다른 값들이 문장 중간에 끝난다(행 수, 괄호=서로 다른 값).", "",
                  "| 소스 | 칸 | 상한 | 행 | 지금도? | 수집 기간 | 비고 |", "|---|---|---:|---:|---|---|---|"]
        lines += [_trunc_line(t) for t in t_alarm[:MAX_LISTED]]
        lines.append("")
    u_alarm = [u for u in report["usage"] if u["alarm"]]
    if u_alarm:
        lines += ["## 모아 놓고 화면에 안 쓰는 칸", "",
                  "도달률 = 그 칸 내용이 같은 원자료의 지적사항 본문에 들어간 비율.", "",
                  "| 소스 | 칸 | 값 수 | 중앙 길이 | 쓰임 | 도달률 | 읽는 파일 |", "|---|---|---:|---:|---|---|---|"]
        lines += [_usage_line(u) for u in u_alarm[:MAX_LISTED]]
        lines.append("")
    t_ok = [t for t in report["truncation"] if t["intentional"]]
    if t_ok:
        lines += ["<details><summary>의도된 상한(경보 아님)</summary>", ""]
        lines += [f"- {t['source']} `{t['path']}` {t['cap']}자 {t['rows']}행 — {t['intentional']}" for t in t_ok]
        lines += ["", "</details>", ""]
    rest = [u for u in report["usage"] if not u["alarm"]]
    if rest:
        lines += ["<details><summary>산문 칸 쓰임 전체(경보 아님)</summary>", "",
                  "| 소스 | 칸 | 값 수 | 중앙 길이 | 쓰임 | 도달률 | 읽는 파일 |", "|---|---|---:|---:|---|---|---|"]
        lines += [_usage_line(u) + (f" 의도: {u['intentional']}" if u["intentional"] else "") for u in rest]
        lines += ["", "</details>", ""]
    if not t_alarm and not u_alarm:
        lines.append("이상 없음.")
    return "\n".join(lines) + "\n"


def fingerprint(report: dict[str, Any]) -> str:
    keys = sorted([f"T|{t['source']}|{t['path']}|{t['cap']}" for t in report["truncation"] if not t["intentional"]]
                  + [f"U|{u['source']}|{u['path']}" for u in report["usage"] if u["alarm"]])
    return "content-fp:" + hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:16]


def plan_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    """source_reconcile.plan_issues 와 같은 모양 — 워크플로의 이슈 단계가 그대로 처리한다.
    칸마다 이슈를 열지 않고 하나로 모은다(감시기 이슈가 쏟아지면 무시당한다 — 2026-10 교훈)."""
    c = report["counts"]
    base = {"key": "content", "source": "내용 완전성", "title": ISSUE_TITLE, "labels": list(ISSUE_LABELS)}
    if c["truncated"] or c["unused"]:
        fp = fingerprint(report)
        body = render_markdown(report) + "\n" + (f"실행: {report['run_url']}\n\n" if report.get("run_url") else "") + \
            "데이터는 고치지 않았다(감시는 읽기 전용). 의도된 칸이면 `content_completeness.py` 의 " \
            "`INTENTIONAL_CAPS`/`INTENTIONAL_UNSHOWN` 에 이유와 함께 올리고, 아니면 수집·표시를 고친다.\n\n" + f"<!-- {fp} -->\n"
        return [{**base, "action": "open", "body": body, "fingerprint": fp, "verdict": "found",
                 "comment": f"목록이 바뀌었습니다 — 잘림 {c['truncated']}칸 · 안 쓰이는 칸 {c['unused']}개"}]
    return [{**base, "action": "close", "verdict": "ok",
             "comment": "잘림·안 쓰이는 칸이 더 보이지 않아 자동으로 닫습니다."}]


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────
def run(env: dict[str, str], *, today: date, run_url: str = "", session: Any = None) -> dict[str, Any]:
    base = str(env.get("SUPABASE_URL") or "").strip()
    key = str(env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base.startswith("https://") or not key:
        raise StoreReadError("SUPABASE_URL(https)/SUPABASE_SERVICE_ROLE_KEY 미설정")
    raw = list(fetch_table(base, key, "raw_signals", "raw_signal_id,document_id,source,collected_at,raw_json",
                           "raw_signal_id.asc", session))
    finds = list(fetch_table(base, key, "findings", "finding_id,raw_signal_id,source,finding_text,finding_text_ko,detail_text,detail_text_ko",
                             "finding_id.asc", session))
    extra = {}
    for table, id_col, src_col, cols in EXTRA_TABLES:
        sel = ",".join([id_col] + ([] if src_col.startswith("=") else [src_col]) + list(cols))
        extra[table] = list(fetch_table(base, key, table, sel, f"{id_col}.asc", session))
    stats, ftext = collect_stats(raw, finds, extra)
    return build_report(stats, ftext, today=today, run_url=run_url)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="GRM 내용 완전성 감시(잘림·안 쓰이는 칸)")
    ap.add_argument("--json", default="content-completeness.json")
    ap.add_argument("--report", default="content-completeness.md")
    ap.add_argument("--run-url", default="")
    args = ap.parse_args(argv)
    report = run(dict(os.environ), today=datetime.now(KST).date(), run_url=args.run_url)
    md = render_markdown(report)
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.report).write_text(md, encoding="utf-8")
    print(md, flush=True)
    c = report["counts"]
    if c["truncated"] or c["unused"]:
        print(f"::warning title=content-completeness::잘림 의심 {c['truncated']}칸 · 안 쓰이는 칸 {c['unused']}개", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
