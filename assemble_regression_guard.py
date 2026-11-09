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
- [2026-11-09] 양쪽에 다 있는 카드의 `facts` 값이 바뀌면 회귀로 본다(아래 `facts_changed`).

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


def _card_key(card: dict[str, Any], i: int) -> str:
    return str(card.get("id") or f"__noid_{card.get('render_order', i)}")


def _bundle_items(card: dict[str, Any] | None) -> list[str] | None:
    """묶음 카드의 항목 목록. 묶음이 아니면 None — 스캐폴드 스키마가 **모든** 카드에
    `merged_items: []` 기본값을 싣으므로 빈 목록은 묶음이 아니다."""
    items = (card or {}).get("merged_items")
    return [str(x) for x in items] if isinstance(items, list) and items else None


def _facts_map(card: dict[str, Any] | None) -> dict[str, str]:
    """카드 facts → {label: value}. 같은 label 이 여럿이면 첫 값."""
    out: dict[str, str] = {}
    for f in (card or {}).get("facts") or []:
        if isinstance(f, dict) and "label" in f:
            out.setdefault(str(f["label"]), str(f.get("value", "")))
    return out


def facts_changes(published: dict[str, Any], assembled: dict[str, Any]) -> list[dict[str, Any]]:
    """양쪽에 다 있는 카드의 facts 값 변경 목록(순수 함수).

    ★왜(2026-11-09 실측 #1398): 재발행은 **같은 날짜의 옛 스캐폴드**로 다시 조립한다. 발행 뒤
    수집기 결함을 고치면서 발행본의 사실 값도 함께 정정한 경우(#1313 — HC 회수 업체가 브랜드명
    "Equate" 로, 발행일이 정정일로 적혀 있던 것), 재조립은 옛 스캐폴드의 틀린 값을 되살린다.
    값 하나가 바뀌는 것이라 길이는 거의 같아 길이 기준 판정에는 걸리지 않았다.
    같은 스캐폴드에서 facts 가 달라지는 경우는 사실상 발행 뒤 수기 정정뿐이므로 회귀로 본다.
    묶음 카드는 facts 가 대표 문서에서 오고 대표가 바뀔 수 있어 대상에서 뺀다."""
    old_cards = {_card_key(c, i): c for i, c in enumerate(published.get("cards") or [])}
    out: list[dict[str, Any]] = []
    for i, card in enumerate(assembled.get("cards") or []):
        key = _card_key(card, i)
        old = old_cards.get(key)
        if old is None or _bundle_items(old) is not None or _bundle_items(card) is not None:
            continue
        before, after = _facts_map(old), _facts_map(card)
        for label, value in before.items():
            if label in after and after[label] != value:
                out.append({"id": key, "label": label, "published": value, "assembled": after[label]})
    return out


def carry_published_facts(published: dict[str, Any], assembled: dict[str, Any]) -> list[dict[str, Any]]:
    """조립본의 facts 값을 발행본 값으로 되돌린다(조립본을 **제자리 수정**). 반환=되돌린 목록.

    `facts_changes` 가 찾는 바로 그 변경을 고친다 — 발행된 사실은 재조립이 되돌리지 않는다.
    바꾼 항목은 전부 반환해 요약에 남긴다(조용한 교정 금지)."""
    changes = facts_changes(published, assembled)
    wanted = {(c["id"], c["label"]): c["published"] for c in changes}
    for i, card in enumerate(assembled.get("cards") or []):
        key = _card_key(card, i)
        for f in card.get("facts") or []:
            if isinstance(f, dict) and (key, str(f.get("label"))) in wanted:
                f["value"] = wanted[(key, str(f.get("label")))]
    return changes


def compare(published: dict[str, Any], assembled: dict[str, Any],
            max_shrink_pct: float = DEFAULT_MAX_SHRINK_PCT) -> dict[str, Any]:
    """발행본 대비 조립본의 회귀를 판정한다. 반환=결과 dict(순수 함수).

    [묶음 카드 축소 2026-10-27] 묶음 카드(`merged_items` 보유 — 483 본문 미확보 디제스트 등)는
    소급 복구로 항목이 **빠지는 것이 정상**이다 — 빠진 건은 정식 카드로 풀리거나 범위 밖으로
    제외된다. 종전엔 그 축소를 그대로 '내용 소실'로 세어, 10-26 호 25건 중 20건을 되살리자
    묶음이 −32.5% 가 돼 자동 재조립 PR 이 막혔다. 그래서 묶음 카드는 "발행본에서 **빠진 항목만**
    뺀 모습"을 기준선으로 잰다 — 항목 제거 외의 축소(수기 보정 소실)는 여전히 잡힌다. 남은
    항목이 발행본 항목의 부분집합일 때만 적용한다(새 항목이 끼면 종전 판정 그대로).
    대표 카드가 바뀌어 발행본 id 가 사라진 경우(대표가 복구·제외됨)도, 그 항목의 부분집합을
    든 묶음이 조립본에 있으면 같은 묶음으로 보고 소실로 세지 않는다."""
    old = _cards_by_id(published)
    new = _cards_by_id(assembled)
    old_cards = {_card_key(c, i): c for i, c in enumerate(published.get("cards") or [])}
    new_cards = {_card_key(c, i): c for i, c in enumerate(assembled.get("cards") or [])}
    new_bundles = {k: _bundle_items(c) for k, c in new_cards.items()
                   if _bundle_items(c) is not None}

    def _successor(key: str) -> str | None:
        """발행본 묶음 카드 key 에 대응하는 조립본 묶음 카드 key(항목이 부분집합인 것)."""
        old_items = _bundle_items(old_cards.get(key))
        if old_items is None:
            return None
        if key in new_bundles and set(new_bundles[key]) <= set(old_items):
            return key
        for k, items in new_bundles.items():
            if k not in old and items and set(items) <= set(old_items):
                return k
        return None

    missing: list[str] = []
    shrunk: list[dict[str, Any]] = []
    unbundled: list[dict[str, Any]] = []
    for key, old_text in old.items():
        succ = _successor(key)
        new_text = new.get(succ or key)
        if new_text is None:
            old_items = _bundle_items(old_cards.get(key))
            if old_items is not None:
                # 묶음 항목이 전부 정식 카드로 풀렸거나 범위 밖으로 제외돼 묶음 자체가 없어졌다
                # (10-26 호 남은 7건 = 기기·식품 483 전부 제외). 묶음은 시설 목록뿐인 결정론
                # 산출이라 수기 보정분이 없다 — 항목이 빠지는 축소와 같은 이유로 소실로 세지 않는다.
                unbundled.append({"id": key, "now": None, "before_items": len(old_items),
                                  "after_items": 0})
                continue
            missing.append(key)
            continue
        if succ is not None:
            old_items = _bundle_items(old_cards[key]) or []
            new_items = new_bundles[succ]
            if len(new_items) < len(old_items) or succ != key:
                unbundled.append({"id": key, "now": succ, "before_items": len(old_items),
                                  "after_items": len(new_items)})
            baseline = dict(old_cards[key])
            baseline["merged_items"] = new_items
            baseline["merged_count"] = new_cards[succ].get("merged_count")
            old_text = json.dumps(baseline, ensure_ascii=False, sort_keys=True)
        before, after = len(old_text), len(new_text)
        if after >= before:
            continue
        pct = (before - after) / before * 100.0
        if pct >= max_shrink_pct:
            shrunk.append({"id": key, "before": before, "after": after, "shrink_pct": round(pct, 1)})
    shrunk.sort(key=lambda r: r["shrink_pct"], reverse=True)
    missing.sort()

    total_before = sum(len(v) for v in old.values())
    total_after = sum(len(v) for v in new.values())
    changed_facts = facts_changes(published, assembled)
    return {
        "ok": not missing and not shrunk and not changed_facts,
        "facts_changed": changed_facts,
        "published_cards": len(old),
        "assembled_cards": len(new),
        "missing_cards": missing,
        "shrunk_cards": shrunk,
        "unbundled_cards": unbundled,
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
        for row in result.get("carried_facts") or []:
            lines.append(f"- 발행 사실 유지 `{row['id']}` {row['label']}: 재조립 값 "
                         f"{row['assembled']!r} 대신 발행본 값 {row['published']!r} — 발행 뒤 정정분")
        for row in result.get("unbundled_cards") or []:
            if row["now"] is None:
                lines.append(f"- 묶음 해소 `{row['id']}`: {row['before_items']}건 → 0건 — "
                             "항목이 전부 정식 카드로 풀렸거나 범위 밖 제외")
                continue
            moved = f" (대표 → `{row['now']}`)" if row["now"] != row["id"] else ""
            lines.append(f"- 묶음 축소 `{row['id']}`: {row['before_items']}건 → "
                         f"{row['after_items']}건{moved} — 빠진 항목은 정식 카드로 풀렸거나 범위 밖 제외")
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
    if result.get("facts_changed"):
        lines.append("")
        lines.append(f"사실 값(facts)이 바뀐 카드 {len(result['facts_changed'])}건 — 발행 뒤 정정한 값이 "
                     "옛 스캐폴드 값으로 되돌아갔을 가능성이 큽니다(`--carry-published-facts` 로 유지):")
        lines.append("")
        lines.append("| 카드 id | 항목 | 발행본 | 조립본 |")
        lines.append("|---|---|---|---|")
        for row in result["facts_changed"][:15]:
            lines.append(f"| `{row['id']}` | {row['label']} | {row['published']} | {row['assembled']} |")
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
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="발행본 대비 조립본 회귀 검사")
    ap.add_argument("--published", required=True,
                    help="이미 발행된 브리프 JSON (없으면 최초 발행으로 보고 통과)")
    ap.add_argument("--assembled", required=True, help="방금 조립한 브리프 JSON")
    ap.add_argument("--max-shrink-pct", type=float, default=DEFAULT_MAX_SHRINK_PCT)
    ap.add_argument("--summary", help="마크다운을 덧붙일 파일(GITHUB_STEP_SUMMARY)")
    ap.add_argument("--carry-published-facts", action="store_true",
                    help="조립본의 facts 값이 발행본과 다르면 발행본 값으로 되돌려 --assembled 에 다시 쓴다")
    ap.add_argument("--carry-only", action="store_true",
                    help="--carry-published-facts 만 하고 회귀 판정은 하지 않는다(항상 0). 발행 워크플로가 "
                         "골든 재동결 **전에** 값을 맞추고, 판정은 뒤의 가드 스텝에 맡기기 위한 모드.")
    args = ap.parse_args(argv)
    if args.carry_only:
        args.carry_published_facts = True

    pub_path = Path(args.published)
    if not pub_path.exists() or pub_path.stat().st_size == 0:
        print("발행본 없음 — 최초 발행으로 보고 회귀 검사 생략")
        return 0

    published = json.loads(pub_path.read_text(encoding="utf-8"))
    assembled_path = Path(args.assembled)
    assembled = json.loads(assembled_path.read_text(encoding="utf-8"))
    carried: list[dict[str, Any]] = []
    if args.carry_published_facts:
        carried = carry_published_facts(published, assembled)
        if carried:
            # assemble_publish_brief 와 같은 직렬화(indent=1 + 개행) — 발행 PR diff 를 그 값만으로 둔다.
            assembled_path.write_text(
                json.dumps(assembled, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.carry_only:
        lines = [f"### 발행 사실 유지 — {len(carried)}건"]
        lines += [f"- `{c['id']}` {c['label']}: {c['assembled']!r} → 발행본 값 {c['published']!r}"
                  for c in carried]
        md = "\n".join(lines)
        print(md)
        if args.summary and carried:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write(md + "\n")
        return 0
    result = compare(published, assembled, args.max_shrink_pct)
    result["carried_facts"] = carried
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
