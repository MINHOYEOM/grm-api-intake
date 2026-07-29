"""발행 회귀 가드 — 자동 조립본이 이미 발행된 브리프의 내용을 되돌리는지 검사한다.

왜 필요한가 (2026-07-29)
------------------------
`grm-web-publish.yml` 은 `push · main · paths: web/data/deltas/delta_*.json` 로 트리거되어
스캐폴드+델타에서 브리프를 **다시 조립**한다. 그런데 발행 후 수기로 보정한 내용(예: #475
"07-27 WHO 카드 11장 소급 복구 — 링크 전용 → 결론+항목별 요약 국문 병기")은 발행본
`web/data/briefs/brief_web_*.json` 에만 들어가고 델타·스캐폴드에는 없다. 그래서 재조립하면
그 보정분이 통째로 사라진 산출물이 나온다 — 실측 −117,225자(WHO 11장, 카드당 −70~90%).

종전에는 커밋 스텝이 zero-diff 로 죽어서(exit 1) 이 회귀 PR 이 열리지 않았다. 그 실패를
수리하고 나면 회귀 PR 이 정상적으로 열리고, Admin '이번 주 발행 승인' 카드는 1클릭 승인을
전제로 설계돼 있으므로 **보정분이 조용히 라이브에서 사라질 수 있다.**

계약
----
- 카드 `id` 기준으로 발행본과 조립본을 대조한다.
- 이미 발행된 카드가 사라졌거나(카드 소실) 직렬화 길이가 임계 이상 줄면 회귀로 판정한다.
- 길이가 조금 늘거나 줄어드는 것(파서 개선 등)은 회귀가 아니다 — 임계 미만은 통과시킨다.
  임계를 두지 않으면 정상 재조립마다 붉게 뜨고, 그건 방금 고친 결함과 같은 종류의 오탐이다.
- 발행본이 없으면(그 날짜 최초 발행) 대조 대상이 없으므로 통과.

출력은 GitHub step summary 에 그대로 붙일 수 있는 마크다운이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 카드 하나가 이만큼 넘게 줄면 '내용 소실'로 본다. 파서 개선으로 인한 문장 재분할 정도는
# 수 %~십수 % 수준이고, 실측된 회귀(WHO 11장)는 −70~90% 였다. 30%는 그 사이의 여유 있는 선.
DEFAULT_MAX_SHRINK_PCT = 30.0


def _cards_by_id(doc: dict[str, Any]) -> dict[str, str]:
    """카드 id → 직렬화 문자열. id 없는 카드는 render_order 로 대체 키를 만든다."""
    out: dict[str, str] = {}
    for i, card in enumerate(doc.get("cards") or []):
        key = card.get("id") or f"__noid_{card.get('render_order', i)}"
        out[str(key)] = json.dumps(card, ensure_ascii=False, sort_keys=True)
    return out


def compare(published: dict[str, Any], assembled: dict[str, Any],
            max_shrink_pct: float = DEFAULT_MAX_SHRINK_PCT) -> dict[str, Any]:
    """발행본 대비 조립본의 회귀를 판정한다. 반환=결과 dict(순수 함수)."""
    old = _cards_by_id(published)
    new = _cards_by_id(assembled)

    missing = sorted(set(old) - set(new))
    shrunk: list[dict[str, Any]] = []
    for key, old_text in old.items():
        new_text = new.get(key)
        if new_text is None:
            continue
        before, after = len(old_text), len(new_text)
        if after >= before:
            continue
        pct = (before - after) / before * 100.0
        if pct >= max_shrink_pct:
            shrunk.append({"id": key, "before": before, "after": after, "shrink_pct": round(pct, 1)})
    shrunk.sort(key=lambda r: r["shrink_pct"], reverse=True)

    total_before = sum(len(v) for v in old.values())
    total_after = sum(len(v) for v in new.values())
    return {
        "ok": not missing and not shrunk,
        "published_cards": len(old),
        "assembled_cards": len(new),
        "missing_cards": missing,
        "shrunk_cards": shrunk,
        "total_before": total_before,
        "total_after": total_after,
        "total_delta": total_after - total_before,
        "max_shrink_pct": max_shrink_pct,
    }


def render_markdown(result: dict[str, Any], out_path: str) -> str:
    lines: list[str] = []
    if result["ok"]:
        lines.append("### 발행 회귀 가드 — 통과")
        lines.append("")
        lines.append(
            f"발행본 {result['published_cards']}장 → 조립본 {result['assembled_cards']}장 · "
            f"본문 총 길이 {result['total_delta']:+,}자. 되돌려진 내용 없음."
        )
        return "\n".join(lines)

    lines.append("### 발행 회귀 가드 — 차단")
    lines.append("")
    lines.append(
        f"`{out_path}` 재조립 결과가 **이미 발행된 내용을 되돌립니다**. "
        f"발행 후 수기로 보정한 내용이 델타·스캐폴드에 반영돼 있지 않을 때 생깁니다."
    )
    lines.append("")
    lines.append(
        f"- 카드 수: 발행본 {result['published_cards']} → 조립본 {result['assembled_cards']}"
    )
    lines.append(f"- 본문 총 길이: {result['total_delta']:+,}자")
    if result["missing_cards"]:
        lines.append(f"- 사라진 카드 {len(result['missing_cards'])}장: "
                     + ", ".join(f"`{c}`" for c in result["missing_cards"][:10])
                     + (" …" if len(result["missing_cards"]) > 10 else ""))
    if result["shrunk_cards"]:
        lines.append("")
        lines.append(f"내용이 {result['max_shrink_pct']:g}% 이상 줄어든 카드 "
                     f"{len(result['shrunk_cards'])}장:")
        lines.append("")
        lines.append("| 카드 id | 발행본 | 조립본 | 감소 |")
        lines.append("|---|---:|---:|---:|")
        for row in result["shrunk_cards"][:15]:
            lines.append(f"| `{row['id']}` | {row['before']:,} | {row['after']:,} | "
                         f"−{row['shrink_pct']:g}% |")
        if len(result["shrunk_cards"]) > 15:
            lines.append(f"| … 외 {len(result['shrunk_cards']) - 15}장 | | | |")
    lines.append("")
    lines.append("**조치**: 보정분을 델타에 반영해 재조립이 같은 결과를 내게 하거나, "
                 "이 날짜의 재발행을 건너뛰십시오. 이대로 PR 을 열면 승인 시 라이브에서 "
                 "보정분이 사라집니다.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="발행본 대비 조립본 회귀 검사")
    ap.add_argument("--published", required=True,
                    help="이미 발행된 브리프 JSON (없으면 최초 발행으로 보고 통과)")
    ap.add_argument("--assembled", required=True, help="방금 조립한 브리프 JSON")
    ap.add_argument("--max-shrink-pct", type=float, default=DEFAULT_MAX_SHRINK_PCT)
    ap.add_argument("--summary", help="마크다운을 덧붙일 파일(GITHUB_STEP_SUMMARY)")
    args = ap.parse_args(argv)

    pub_path = Path(args.published)
    if not pub_path.exists() or pub_path.stat().st_size == 0:
        print("발행본 없음 — 최초 발행으로 보고 회귀 검사 생략")
        return 0

    published = json.loads(pub_path.read_text(encoding="utf-8"))
    assembled = json.loads(Path(args.assembled).read_text(encoding="utf-8"))
    result = compare(published, assembled, args.max_shrink_pct)
    md = render_markdown(result, args.assembled)

    print(md)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")

    if result["ok"]:
        return 0
    print("::error::재조립이 이미 발행된 내용을 되돌립니다 — 발행 PR 을 열지 않습니다")
    return 1


if __name__ == "__main__":
    sys.exit(main())
