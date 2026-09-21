#!/usr/bin/env python3
"""발행된 주간 브리프의 제품군(Modality) 배지를 새 분류기로 소급 재계산한다.

배경 (2026-09-21)
-----------------
구 `compute_modality` 는 2순위에서 `product_type` 에 'drug' 가 들어 있거나 제형/투여경로
필드가 **존재하기만 하면** Chemical 을 확정했다. 그 결과 FDA 483 의 `establishment_type`
("Sterile Drug Manufacturer" 등)과 openFDA 회수의 `product_type="Drugs"` 가 통째로
💊 합성의약품으로 나갔다. 발행본 실측:

    FDA 483   138장 중 52장(38%) — 제품군 정보가 0인 시설유형으로 합성 확정
    Recall    100장 중 93장(93%) — product_type="Drugs" 한 줄로 합성 확정
    본문이 "무균의약품"이라고 말하는 14장 중 11장이 💊 합성

새 분류기는 **양성 근거가 있을 때만** 판정하고, 없으면 MODALITY_UNKNOWN("")을 돌려
배지를 달지 않는다. 이 스크립트는 그 판정을 이미 발행된 브리프에 소급 적용한다.

재생(replay) 방식 — 왜 신뢰할 수 있나
------------------------------------
발행 카드의 `modality` 는 수집 시점에 `grm_notion.notion_create_page` 가
`compute_modality(item.raw_payload, item.headline, item.body, item.type_or_class,
item.firm)` 로 계산해 Notion 에 적은 값이다. Supabase `raw_signals` 는 그 입력을 둘 다
그대로 보관한다 — `raw_json` = raw_payload, `row_json` = IntakeItem(headline/body/
type_or_class/firm). 따라서 같은 입력으로 함수를 다시 부르면 그 시점 판정이 재현된다.

★`--verify-replay` 가 그것을 **증명**한다: 수리 직전 커밋(OLD_CLASSIFIER_REF)의 구 분류기로 재생한 값이
발행본 배지와 한 장도 빠짐없이 일치해야 한다. 일치하지 않으면 재생 경로가 발행 경로와
다르다는 뜻이므로, 새 판정도 믿을 수 없고 스크립트는 실패한다. (성질을 재는 검사 —
"몇 장 바뀌었다" 같은 숫자는 재생이 맞다는 증거가 못 된다.)

  python backfill_modality_published_briefs.py --verify-replay   # 재생 충실성 증명
  python backfill_modality_published_briefs.py                   # dry-run 리포트
  python backfill_modality_published_briefs.py --apply           # 브리프 JSON 갱신

★`raw_signals` 는 읽기 전용으로만 쓴다. 이 스크립트가 쓰는 것은 `web/data/briefs/*.json`
뿐이다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import findings_supabase_backfill as fsb
from grm_cli import resolve_supabase_service_credentials
from grm_taxonomy import (
    MODALITY_BIOLOGIC,
    MODALITY_CHEMICAL,
    MODALITY_OTHER,
    MODALITY_UNKNOWN,
    compute_modality,
)

# ★재생 기준(구 분류기)은 **고정 커밋**이어야 한다 — 움직이는 ref 를 쓰면 안 된다.
#   첫 실행(2026-09-21)이 `origin/main` 을 썼다가, 바로 직전에 수리가 main 에 머지되는
#   바람에 "구 분류기" 자리에 **새 분류기**가 들어왔다. 재생값이 전부 ""(무배지)로 나와
#   466장 중 308장이 불일치로 찍혔고, 게이트는 데이터가 아니라 기준을 의심하게 만들었다.
#   = PR #1026 머지 커밋의 부모(수리 직전 main).
OLD_CLASSIFIER_REF = "e44db92a963acc18891166ce01a11507f231d0b7"

# 구 분류기가 맞는지 확인하는 앵커 — (입력, 그 시점 기대 판정).
# 'Sterile Drug Manufacturer' 는 구 구현이 `product_type` 의 'drug' 토큰만으로 Chemical 을
# 확정하던 대표 입력이다(이 사건의 발단). 새 분류기는 "" 를 돌려준다.
# ★앵커가 맞지 않으면 비교를 시작하지 않고 실패한다 — 잘못된 기준으로 308장을 찍어
#   사람에게 "데이터가 틀렸다"고 오인시키느니, 기준이 틀렸다고 바로 말하는 편이 낫다.
_OLD_CLASSIFIER_ANCHOR = (
    {"product_type": "Sterile Drug Manufacturer"},
    ("[FDA 483] Some Firm", "시설 유형: Sterile Drug Manufacturer", "483", "Some Firm"),
    "Chemical",
)

BRIEF_DIR = Path(__file__).resolve().parent / "web" / "data" / "briefs"
CACHE_PATH = Path(__file__).resolve().parent / "tmp" / "modality_replay_cache.json"
_PAGE_SIZE = 1000

# 카드 JSON 의 배지 문자열 ↔ 분류기 값. card_scaffold.FixedConfig.modality_badge 의 역방향
# — 같은 문자열을 두 벌 두지 않도록 거기서 읽어온다.
def _badge_maps() -> tuple[dict[str, str], dict[str, str]]:
    import card_scaffold as cs
    badge = dict(cs.DEFAULT_CONFIG.modality_badge)
    return badge, {v: k for k, v in badge.items()}


def _load_briefs() -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for p in sorted(BRIEF_DIR.glob("brief_web_*.json")):
        out.append((p, json.loads(p.read_text(encoding="utf-8"))))
    return out


def _fetch_replay_inputs(base_url: str, service_key: str) -> dict[str, dict[str, Any]]:
    """document_id → {raw_json, row_json}. raw_signals 전건(읽기 전용)."""
    rows = fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="document_id,raw_json,row_json",
        page_size=_PAGE_SIZE, order="raw_signal_id.asc",
    )
    by_doc: dict[str, dict[str, Any]] = {}
    for r in rows:
        doc = (r.get("document_id") or "").strip()
        if doc:
            by_doc[doc] = r          # 같은 document_id 재수집분은 최신 행이 이긴다
    return by_doc


def _replay_args(entry: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """raw_signals 행 → compute_modality 인자(raw_payload, *text_parts).

    ★인자 순서·구성은 grm_notion.notion_create_page 의 호출부와 한 글자도 달라선 안 된다:
        compute_modality(item.raw_payload, item.headline, item.body,
                         item.type_or_class, item.firm)
    """
    raw = entry.get("raw_json")
    row = entry.get("row_json")
    raw_payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    item = json.loads(row) if isinstance(row, str) else (row or {})
    parts = (
        item.get("headline") or "",
        item.get("body") or "",
        item.get("type_or_class") or "",
        item.get("firm") or "",
    )
    return raw_payload, parts


def _recompute(card_id: str, inputs: dict[str, dict[str, Any]]) -> str | None:
    entry = inputs.get(card_id)
    if entry is None:
        return None
    raw_payload, parts = _replay_args(entry)
    return compute_modality(raw_payload, *parts)


# ── 리포트 ───────────────────────────────────────────────────────────────────
def _print_report(changes: list[dict[str, Any]], unmatched: list[str],
                  total: int, badge: dict[str, str]) -> None:
    def name(v: str) -> str:
        # ★MODALITY_UNKNOWN 은 배지를 아예 달지 않는다 — 리포트도 그렇게 말해야 한다.
        #   (빈 문자열을 badge.get 에 넘기면 None 이 나와 "None" 으로 찍힌다.)
        if not v:
            return "(배지 없음)"
        return badge.get(v) or v

    print(f"발행 카드 {total}장 · raw_signals 매칭 실패 {len(unmatched)}장 "
          f"· 판정 변경 {len(changes)}장")
    print()
    moves = Counter((c["old"], c["new"]) for c in changes)
    print("변경 내역 (이전 → 이후)")
    for (old, new), n in moves.most_common():
        print(f"  {n:>4}  {name(old):<16} → {name(new)}")
    print()
    after = Counter(c["new"] for c in changes)
    for v in (MODALITY_BIOLOGIC, MODALITY_CHEMICAL, MODALITY_OTHER, MODALITY_UNKNOWN):
        if after.get(v):
            print(f"  신규 {name(v):<16} {after[v]}장")
    if unmatched:
        print()
        print(f"★raw_signals 에서 못 찾은 카드 {len(unmatched)}장 — 배지를 건드리지 않는다:")
        for d in unmatched[:20]:
            print(f"    {d}")
        if len(unmatched) > 20:
            print(f"    … 외 {len(unmatched)-20}장")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="브리프 JSON 을 실제로 갱신한다(기본은 dry-run).")
    ap.add_argument("--verify-replay", action="store_true",
                    help="구 분류기로 재생해 발행본 배지와 전건 일치하는지 증명한다.")
    ap.add_argument("--supabase-url", default="")
    ap.add_argument("--service-role-key", default="")
    ap.add_argument("--cache", default=str(CACHE_PATH),
                    help="raw_signals 재생 입력 캐시(JSON). 있으면 네트워크 없이 쓴다.")
    ap.add_argument("--refresh-cache", action="store_true")
    ap.add_argument("--old-ref", default=OLD_CLASSIFIER_REF,
                    help="재생 기준(구 분류기) 커밋. 기본은 수리 직전 main 고정 SHA.")
    args = ap.parse_args(argv)

    badge, _ = _badge_maps()
    briefs = _load_briefs()
    if not briefs:
        print(f"브리프가 없다: {BRIEF_DIR}", file=sys.stderr)
        return 2

    cache = Path(args.cache)
    if cache.exists() and not args.refresh_cache:
        inputs = json.loads(cache.read_text(encoding="utf-8"))
        print(f"[cache] {cache} 에서 {len(inputs)}건 로드")
    else:
        creds = resolve_supabase_service_credentials(args)
        if creds is None:
            print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 필요하다 "
                  "(또는 --cache 로 이미 받아둔 입력을 준다).", file=sys.stderr)
            return 2
        base_url, service_key = creds
        inputs = _fetch_replay_inputs(base_url, service_key)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
        print(f"[fetch] raw_signals {len(inputs)}건 → {cache}")

    if args.verify_replay:
        return _verify_replay(briefs, inputs, badge, args.old_ref)

    total = 0
    changes: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for path, doc in briefs:
        for card in doc.get("cards", []):
            total += 1
            card_id = card.get("id") or ""
            new_val = _recompute(card_id, inputs)
            if new_val is None:
                unmatched.append(card_id)
                continue
            old_badge = card.get("modality")
            new_badge = badge.get(new_val) if new_val else None
            # 규범 문서는 배지가 애초에 억제돼 있다(카드 modality=null) — 건드리지 않는다.
            if old_badge is None and new_badge is None:
                continue
            if old_badge == new_badge:
                continue
            changes.append({
                "file": path.name, "id": card_id,
                "old": _value_of(old_badge, badge), "new": new_val,
                "target": card.get("headline_target"),
                "title": card.get("title_issue"),
            })
            if args.apply:
                card["modality"] = new_badge

    _print_report(changes, unmatched, total, badge)

    if args.apply:
        for path, doc in briefs:
            path.write_text(
                json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8", newline="\n")
        print()
        print(f"[apply] 브리프 {len(briefs)}개 파일 갱신")
    else:
        print()
        print("(dry-run — 실제 반영은 --apply)")
    return 0


def _value_of(badge_str: str | None, badge: dict[str, str]) -> str:
    if not badge_str:
        return MODALITY_UNKNOWN
    rev = {v: k for k, v in badge.items()}
    return rev.get(badge_str, badge_str)


def _verify_replay(briefs, inputs, badge, old_ref: str) -> int:
    """★구 분류기(고정 커밋)로 재생 → 발행본 배지와 전건 일치해야 한다."""
    try:
        old_mod = _load_old_classifier(old_ref)
    except Exception as e:                                   # noqa: BLE001
        print(f"구 분류기 로드 실패({old_ref}): {e}", file=sys.stderr)
        return 2

    # ★앵커 — 기준 커밋이 정말 '구 분류기'인지 먼저 확인한다.
    anchor_raw, anchor_parts, anchor_expect = _OLD_CLASSIFIER_ANCHOR
    got = old_mod(anchor_raw, *anchor_parts)
    if got != anchor_expect:
        print(f"★기준 커밋({old_ref})이 구 분류기가 아니다 — "
              f"앵커 기대 {anchor_expect!r}, 실제 {got!r}. 비교를 시작하지 않는다.",
              file=sys.stderr)
        return 2

    checked = mismatch = skipped = 0
    rows = []
    for path, doc in briefs:
        for card in doc.get("cards", []):
            card_id = card.get("id") or ""
            entry = inputs.get(card_id)
            if entry is None:
                skipped += 1
                continue
            published = card.get("modality")
            if published is None:
                # 규범 문서 — 배지 억제라 분류기 값과 비교할 수 없다.
                skipped += 1
                continue
            raw_payload, parts = _replay_args(entry)
            # ★빈 판정은 "배지 없음"(카드 modality=None)이다. badge.get("") 이 우연히
            #   None 을 돌려주는 데 기대지 않고 명시한다.
            replayed_value = old_mod(raw_payload, *parts)
            replayed = badge.get(replayed_value) if replayed_value else None
            checked += 1
            if replayed != published:
                mismatch += 1
                rows.append((path.name, card_id, published, replayed))

    print(f"재생 충실성 검증 — 비교 {checked}장 · 불일치 {mismatch}장 "
          f"· 비교 불가(매칭 실패·배지 억제) {skipped}장")
    for r in rows[:30]:
        print(f"  불일치 [{r[0]}] {r[1]}: 발행본={r[2]} 재생={r[3]}")
    if mismatch:
        print()
        print("★재생 경로가 발행 경로와 다르다 — 새 판정도 신뢰할 수 없다. 중단.",
              file=sys.stderr)
        return 1
    print("★전건 일치 — 재생 입력이 발행 시점 입력과 같다. 새 판정을 신뢰할 수 있다.")
    return 0


def _load_old_classifier(ref: str):
    """`ref` 시점의 grm_taxonomy.compute_modality 를 독립 모듈로 적재."""
    import importlib.util
    import subprocess
    import tempfile
    src = subprocess.run(
        ["git", "show", f"{ref}:grm_taxonomy.py"],
        capture_output=True, check=True, cwd=str(Path(__file__).resolve().parent),
    ).stdout.decode("utf-8")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "grm_taxonomy_old.py"
        p.write_text(src, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("grm_taxonomy_old", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.compute_modality


if __name__ == "__main__":
    # ★좁은 콘솔 인코딩(cp949) 가드 — 리포트가 '💊/🧬/▫️/★/—' 를 찍으므로 이게 없으면
    #   Windows 러너에서 UnicodeEncodeError 로 산출물이 통째로 날아간다
    #   (tests/test_cli_stdout_encoding.py 가 전 CLI 진입점에 이 블록을 요구한다).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
