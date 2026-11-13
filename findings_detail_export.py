#!/usr/bin/env python3
"""[483 구체 내용 · 2026-11-11] 상세 번역 대기열 → 겹치지 않는 묶음(A·B·C) 파일. 읽기 전용(DB 쓰기 없음).

대상: findings 중 FDA 483·scope_status='ok'·detail_text_ko 가 빈 행. 원문 상세는 raw_signals 의
fda_483_observations[] 에서 (raw_signal_id, 공백 접은 표제 == finding_text) 로 짝을 찾는다. 그 짝이
없을 때만, 표제가 조각("." 등)인 관찰 중 detail 이 finding 표제 전체로 시작하는 것을 짝으로 보고 표제를
뗀 나머지를 싣는다(2026-11-13, 아래 `_detail_after_heading`). 짝이 둘 이상이거나 없으면 싣지 않는다(지어내지 않는다).

출력: <out>/<묶음>/<묶음>-<번호4>.json — 각 파일은 항목 50개 배열
  {"finding_id", "finding_text_md5", "published_date", "detail_text"}
  최신 게시일부터 50개씩 끊어 A·B·C 에 번갈아 준다(세 곳 모두 최신 문서부터 진행된다).
번역자는 같은 파일에 "detail_text_ko" 를 더해 translations/detail_outbox/<같은 파일 이름> 으로 낸다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import findings_supabase_backfill as fsb
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_483

SHARDS = ("A", "B", "C")
PER_FILE = 50


def _md5(text: str) -> str:
    return hashlib.md5(str(text).encode("utf-8")).hexdigest()


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# [2026-11-12] 표제 앞 OCR 잡음 — 원문(raw) 관찰 표제에만 남고 findings 표제는 예전 수리로 정리돼,
# 완전 일치 비교가 실패했다(국문 없는 옛 483 98건 중 95건: "j Aseptic…", "| Equipment…", "· Procedures…",
# "\x01 Samples…", "| | | The responsibilities…"). **앞쪽** 비문자(글자·숫자·여는 괄호·따옴표가 아닌 것)와
# 그 뒤 **소문자 한 글자 + 공백 + 대문자** 조각만 뗀다. 문구가 달라진 표제는 그대로 실패한다.
_LEAD_NONWORD_RE = re.compile(r"^[^A-Za-z0-9(\"“']+")
_LEAD_STRAY_LETTER_RE = re.compile(r"^[a-z] (?=[A-Z])")


def _heading_key(text: str) -> str:
    """짝 비교용 표제 키 — 공백 접기 + 앞 잡음 제거(순수 함수)."""
    t = _compact(text)
    t = _LEAD_NONWORD_RE.sub("", t)
    t = _LEAD_STRAY_LETTER_RE.sub("", t)
    return _LEAD_NONWORD_RE.sub("", t).strip()


# [2026-11-13] 관찰 분할이 깨진 문서 — raw 관찰의 표제(deficiency)가 "."·"A."·"l , ." 같은 **조각**이고
# 진짜 표제는 그 관찰 detail 첫머리에 들어 있다(국문 없는 ok 행 289개 중 15행: 문서 130570·144803·151431·
# 163754·70711·91684, 표제 위치 1~22번째 글자). 위 완전 일치 규칙으로 짝이 없을 때만 **덧붙여** 본다:
#   · 그 관찰 표제가 조각(영문 두 글자 이상 연속이 없음)이고
#   · detail 이 finding 표제 **전체**로 시작하면(앞에는 잡음·"A. "·"(REPEAT OBSERVATION)" 만 허용,
#     표제 끝은 낱말 경계) 짝으로 인정한다. 앞 몇십 자만 같은 것은 후보일 뿐 인정하지 않는다.
#   짝이 되면 detail 에서 **표제와 그 앞 잡음만** 떼고(번역 중복 방지) 나머지는 원문 그대로 싣는다.
#   떼고 남은 것에 글자가 없으면(표제뿐) 번역할 상세가 없으므로 싣지 않는다.
_HAS_WORD_RE = re.compile(r"[A-Za-z]{2}")
_DETAIL_LEAD_RE = re.compile(r"(?:[^A-Za-z0-9(\"“']|[A-Za-z][.)](?=\s)|[a-z](?=\s)|\(REPEAT(?:ED)? OBSERVATION\))*")
_MIN_HEAD_WORDS = 4


def _is_fragment_heading(deficiency: Any) -> bool:
    """raw 관찰 표제가 비었거나 '.'·'A.' 같은 조각인가(순수 함수)."""
    return not _HAS_WORD_RE.search(str(deficiency or ""))


def _detail_after_heading(detail: str, heading: str) -> str | None:
    """detail 이 표제 전체로 시작하면 표제(와 앞 잡음)를 뗀 나머지 원문, 아니면 None(순수 함수)."""
    words = _heading_key(heading).split()
    if len(words) < _MIN_HEAD_WORDS:
        return None
    m = re.search(r"\s+".join(map(re.escape, words)) + r"(?![A-Za-z0-9])", detail)
    if not m or not _DETAIL_LEAD_RE.fullmatch(detail[:m.start()]):
        return None
    return detail[m.end():].lstrip()


def build_queue(findings: list[dict[str, Any]], raws: list[dict[str, Any]], *, stale_only: bool = False
                ) -> tuple[list[dict[str, Any]], dict]:
    """(대기열 — 최신 먼저, 집계). 순수 함수.

    stale_only: 이미 국문이 있는 행 중 **지금 원문 상세가 저장된 detail_text 와 다른 행**만(다시 읽기로 원문이
    바뀐 것 — 2026-11-11 실측 73%). 아니면 국문이 아직 없는 행."""
    by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    frag_details: dict[str, list[str]] = defaultdict(list)   # 표제가 조각인 관찰의 detail(raw 별)
    for r in raws:
        raw = r.get("raw_json")
        raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
        for o in raw.get("fda_483_observations") or []:
            if isinstance(o, dict) and str(o.get("detail") or "").strip():
                by_key[(str(r.get("raw_signal_id")), _heading_key(o.get("deficiency")))].append(str(o["detail"]))
                if _is_fragment_heading(o.get("deficiency")):
                    frag_details[str(r.get("raw_signal_id"))].append(str(o["detail"]))
    queue, stats = [], {"findings": len(findings), "queued": 0, "no_detail": 0, "ambiguous": 0, "unchanged": 0,
                        "detail_head": 0, "heading_only": 0}
    for f in findings:
        hits = by_key.get((str(f.get("raw_signal_id")), _heading_key(f.get("finding_text"))), [])
        by_detail_head = False
        if not hits:   # 완전 일치 짝이 없을 때만 — 조각 표제 관찰의 detail 첫머리에서 찾는다(가산)
            rests = [x for x in (_detail_after_heading(d, str(f.get("finding_text") or ""))
                                 for d in frag_details.get(str(f.get("raw_signal_id")), [])) if x is not None]
            if len(set(rests)) == 1 and not _HAS_WORD_RE.search(rests[0]):
                stats["no_detail"] += 1
                stats["heading_only"] += 1
                continue
            hits, by_detail_head = rests, True
        if not hits:
            stats["no_detail"] += 1
            continue
        if len(set(hits)) > 1:
            stats["ambiguous"] += 1
            continue
        if stale_only and hits[0] == str(f.get("detail_text") or ""):
            stats["unchanged"] += 1
            continue
        stats["detail_head"] += int(by_detail_head)
        queue.append({"finding_id": str(f["finding_id"]), "finding_text_md5": _md5(f.get("finding_text") or ""),
                      "published_date": str(f.get("published_date") or ""), "detail_text": hits[0]})
    queue.sort(key=lambda x: (x["published_date"], x["finding_id"]), reverse=True)
    stats["queued"] = len(queue)
    stats["chars"] = sum(len(x["detail_text"]) for x in queue)
    return queue, stats


def write_shards(queue: list[dict[str, Any]], out: Path, shards: tuple[str, ...] = SHARDS
                 ) -> dict[str, dict[str, int]]:
    summary = {s: {"files": 0, "items": 0, "chars": 0} for s in shards}
    for i in range(0, len(queue), PER_FILE):
        chunk = queue[i:i + PER_FILE]
        shard = shards[(i // PER_FILE) % len(shards)]
        n = summary[shard]["files"] + 1
        d = out / shard
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{shard}-{n:04d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1) + "\n",
                                                 encoding="utf-8")
        summary[shard]["files"] = n
        summary[shard]["items"] += len(chunk)
        summary[shard]["chars"] += sum(len(x["detail_text"]) for x in chunk)
    return summary


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="483 상세 번역 대기열을 A·B·C 묶음 파일로(읽기 전용).")
    p.add_argument("--out", required=True)
    p.add_argument("--mode", choices=("pending", "stale"), default="pending",
                   help="pending = 국문 없는 행 · stale = 국문 있으나 원문이 바뀐 행(재번역).")
    p.add_argument("--prefix", default="", help="묶음 이름(쉼표). 기본 pending=A,B,C · stale=R.")
    p.add_argument("--supabase-url")
    p.add_argument("--service-role-key")
    args = p.parse_args(argv)
    creds = _resolve_credentials(args)
    if creds is None:
        print("findings_detail_export: Supabase 자격 증명 필요", file=sys.stderr)
        return 2
    base = _normalize_base_url(creds[0])
    findings = fsb._fetch_all_pages(
        base, creds[1], "findings", select="finding_id,raw_signal_id,published_date,finding_text,detail_text",
        page_size=1000, order="finding_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "scope_status": "eq.ok",
                      "detail_text_ko": "not.is.null" if args.mode == "stale" else "is.null"})
    raws = fsb._fetch_all_pages(
        base, creds[1], "raw_signals", select="raw_signal_id,raw_json", page_size=200,
        order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "raw_json": "like.*fda_483_observations*"})
    queue, stats = build_queue(findings, raws, stale_only=args.mode == "stale")
    out = Path(args.out)
    shards = tuple(x for x in args.prefix.split(",") if x) or (("R",) if args.mode == "stale" else SHARDS)
    summary = write_shards(queue, out, shards)
    report = {"mode": args.mode, "stats": stats, "shards": summary}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"summary-{'-'.join(shards)}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
