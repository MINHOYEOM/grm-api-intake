# -*- coding: utf-8 -*-
"""기자 샤드 N개(가변)의 결과를 delta.json · deep_delta.json 으로 합친다 (교열이 첫 단계로 돌린다).

LLM 이 옮겨 적지 않는다 — 딕셔너리 합집합만 한다. 데스크의 `shards` 계획과 한 장도 어긋나면 exit 1.

    python company/editorial/merge_parts.py company/run/2026-11-02
"""
import json
import os
import re
import sys

# 샤드 이름은 데스크 계획(desk.json shards)의 키 — A, B, C … (plan_shards.py). 고정이 아니다.


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _part(folder, name, planned_ids, problems):
    """샤드 파일 하나를 읽는다. 계획이 비어 있으면 파일이 없어도 된다."""
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        if planned_ids:
            problems.append(f"{name}: 파일 없음 (계획 {len(planned_ids)}건)")
        return None
    part = _load(path)
    if part.get("status") != "완료":
        problems.append(f"{name}: status={part.get('status')!r} (완료 아님)")
        return None
    return part


def _diff(name, got, planned, problems):
    missing = sorted(set(planned) - set(got))
    extra = sorted(set(got) - set(planned))
    if missing:
        problems.append(f"{name}: 계획에 있는데 없는 카드 {len(missing)}건 (예: {missing[:3]})")
    if extra:
        problems.append(f"{name}: 계획에 없는 카드 {len(extra)}건 (예: {extra[:3]})")


# 카드 영문 갈래 필수 다섯 — web/render.py CARD_NARRATIVE_FIELDS 와 같다(tests 가 일치를 확인한다).
EN_FIELDS = ("title_issue", "summary", "implication", "key_facts", "checks")
# 한국어가 비어 있으면 영어도 비어도 되는 둘 — render.CARD_NARRATIVE_OPTIONAL 과 같다(2026-11-10:
# 쓸 원문 정보가 없는 카드는 시사점·점검을 비운다. 한국어가 채운 칸만 영어가 짝으로 채운다).
EN_OPTIONAL = ("implication", "checks")
# [2026-11-16 브리프 품질 ③] 소식·지침·고시 카드는 원문에 머리말 사실(바뀌는 것·적용 대상·시행일)이
# 없으면 `key_facts` 도 비운다(v16 — 머리말을 붙여 요약을 다시 쓰지 않는다). 이 세 유형에서만 한국어
# `key_facts` 가 비어도 되고, 그때는 영어도 비어도 된다. render.KEY_FACTS_OPTIONAL_CARD_TYPES ·
# inject_slots.KEY_FACTS_OPTIONAL_CARD_TYPES 와 같다(tests 가 일치를 확인한다).
KEY_FACTS_OPTIONAL_CARD_TYPES = ("규제 소식", "지침·안내서", "고시·개정법령")
# handoff 행의 `card_scaffold` 첫 줄 — `### [유형 · 기관] …`(card_scaffold §13.1-1 동결 형식).
_SCAFFOLD_TYPE_RE = re.compile(r"^###\s*\[(.+?) · ")


def _filled(v):
    return bool([x for x in v if str(x).strip()]) if isinstance(v, list) else bool(str(v or "").strip())


def key_facts_optional(card_type):
    """이 카드 유형은 한국어 `key_facts` 를 비워도 되는가(유형을 모르면 아니다 — 종전 규칙)."""
    return card_type in KEY_FACTS_OPTIONAL_CARD_TYPES


def ko_required(card_type=None):
    """한국어가 반드시 채워야 하는 칸. 소식·지침·고시는 `key_facts` 가 빠진다."""
    return tuple(f for f in EN_FIELDS
                 if f not in EN_OPTIONAL and not (f == "key_facts" and key_facts_optional(card_type)))


def _en_gaps(card, card_type=None):
    """카드 영문 갈래에서 비었거나 빠진 필드 목록. 완결이면 [].

    `card_type`(선택)이 소식·지침·고시면 한국어가 비운 `key_facts` 는 영어도 비어도 된다."""
    card = card if isinstance(card, dict) else {}
    en = card.get("en")
    en = en if isinstance(en, dict) else {}
    optional = EN_OPTIONAL + (("key_facts",) if key_facts_optional(card_type) else ())
    gaps = []
    for f in EN_FIELDS:
        if _filled(en.get(f)):
            continue
        if f in optional and not _filled(card.get(f)):
            continue
        gaps.append(f)
    return gaps


def handoff_card_types(payload):
    """handoff payload → {web_card_id: 카드 유형}. 유형은 행의 `card_scaffold` 첫 줄에서 읽는다(순수)."""
    out = {}
    for row in (payload or {}).get("rows") or []:
        if not isinstance(row, dict) or row.get("merged_into"):
            continue
        cid = row.get("web_card_id")
        md = row.get("card_scaffold")
        m = _SCAFFOLD_TYPE_RE.match(md) if isinstance(md, str) else None
        if isinstance(cid, str) and cid and m:
            out.setdefault(cid, m.group(1).strip())
    return out


def find_handoff(folder, run_date=None):
    """실행 폴더의 handoff 파일 경로(`_handoff/handoff_<날짜>.json`). 없으면 None.

    날짜는 `run_date` → 폴더 이름 순. 현재 작업 폴더와 저장소 루트를 차례로 본다(RUN.md 2항이 루트에 받는다)."""
    d = str(run_date or os.path.basename(os.path.normpath(folder)) or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return None
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for base in (os.getcwd(), root):
        path = os.path.join(base, "_handoff", f"handoff_{d}.json")
        if os.path.exists(path):
            return path
    return None


def run_card_types(folder, run_date=None):
    """실행 폴더 → {web_card_id: 카드 유형}. handoff 를 못 찾거나 못 읽으면 {}(= 종전처럼 전부 필수)."""
    path = find_handoff(folder, run_date)
    if not path:
        return {}
    try:
        return handoff_card_types(_load(path))
    except (OSError, ValueError):
        return {}


def merge(folder):
    problems = []
    desk = _load(os.path.join(folder, "desk.json"))
    if desk.get("status") != "완료":
        return None, None, [f"desk.json status={desk.get('status')!r}"]
    plan = desk.get("shards")
    if not plan:
        return None, None, ["desk.json 에 shards 계획이 없다"]

    cards, tldr, tldr_en, deep = {}, None, None, {}
    delta_shards = sorted(plan.get("delta", {}))
    deep_shards = sorted(plan.get("deep", {}))
    for s in delta_shards:
        planned = plan["delta"].get(s, [])
        part = _part(folder, f"delta.{s}.json", planned, problems)
        if part is None:
            continue
        got = part.get("cards", {})
        _diff(f"delta.{s}.json", got, planned, problems)
        cards.update(got)
        if "tldr" in part:
            tldr, tldr_en = part["tldr"], part.get("tldr_en")
    # tldr·tldr_en 은 가상 샤드 T(delta.T.json) 몫. 옛 형식(델타 샤드 안에 tldr)도 받는다.
    t_name = plan.get("tldr") or "T"
    t_path = os.path.join(folder, f"delta.{t_name}.json")
    if os.path.exists(t_path):
        t_part = _load(t_path)
        if t_part.get("status") == "완료":
            tldr, tldr_en = t_part.get("tldr"), t_part.get("tldr_en")
        else:
            problems.append(f"delta.{t_name}.json: status={t_part.get('status')!r} (완료 아님)")
    if not tldr or not tldr_en:
        problems.append("tldr · tldr_en 이 없다 (delta.T.json 의 몫)")

    # 카드 유형(소식·지침·고시의 빈 key_facts 허용)은 그 주 handoff 에서 읽는다. 못 찾으면 종전처럼 전부 필수.
    types = run_card_types(folder, desk.get("run_date_kst"))
    en_bad = {cid: _en_gaps(c, types.get(cid)) for cid, c in cards.items()
              if _en_gaps(c, types.get(cid))}
    if en_bad:
        ex = ", ".join(f"{cid}({'/'.join(g)})" for cid, g in list(en_bad.items())[:3])
        problems.append(f"영문 슬롯 미완결 {len(en_bad)}/{len(cards)}장 — 이 호는 영어로 나가지 않는다 (예: {ex})")

    for s in deep_shards:
        planned = plan["deep"].get(s, [])
        part = _part(folder, f"deep.{s}.json", planned, problems)
        if part is None:
            continue
        got = part.get("items", {})
        _diff(f"deep.{s}.json", got, planned, problems)
        for cid, item in got.items():
            inner = item.get("deep_analysis") or {}
            if cid.startswith("fda483-") and not (item.get("observations_ko") or inner.get("observations_ko")):
                problems.append(f"deep.{s}.json: {cid} observations_ko 없음 — 브리프 전체가 막힌다")
        deep.update(got)

    if problems:
        return None, None, problems
    delta = {"cards": cards, "tldr": tldr, "tldr_en": tldr_en}
    delta.update(_queue_keys(desk))
    return delta, deep, []


def _queue_keys(desk):
    """git handoff(`claude/queue-handoff`)로 만든 호에만 붙는 두 키 — 없으면 빈 dict(종전과 바이트 동일).

    handoff_ref  데스크가 읽은 handoff 의 날짜·지문·행 수(`python grm_queue.py handoff-ref <파일>` 출력).
                 상태 계산(grm_queue.reconcile)이 "편집팀이 읽은 것 = DB 가 기록한 것"인지 대조한다.
    deferred     데스크가 `defer`(용량 초과 보류)로 배정한 행의 `source::document_id`. 다음 주 handoff 에
                 다시 들어간다. 보류가 없어도 빈 목록을 싣는다("보류 없음"을 분명히).
    노션 handoff 로 만든 호는 DEFERRED 블록을 노션 페이지에 적으므로 여기 싣지 않는다.
    """
    ref = desk.get("handoff_ref")
    if not (isinstance(ref, dict) and ref.get("sha256") and ref.get("date")):
        return {}
    deferred = sorted({f"{r.get('source', '')}::{r.get('document_id', '')}"
                       for r in desk.get("rows") or []
                       if isinstance(r, dict) and r.get("action") == "defer"
                       and r.get("source") and r.get("document_id")})
    return {"handoff_ref": {k: ref[k] for k in ("date", "sha256", "rows") if k in ref},
            "deferred": deferred}


def main(argv):
    folder = argv[1]
    delta, deep, problems = merge(folder)
    if problems:
        for p in problems:
            print("합치기 실패:", p)
        return 1
    with open(os.path.join(folder, "delta.json"), "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False, separators=(",", ":"))
    if deep:
        with open(os.path.join(folder, "deep_delta.json"), "w", encoding="utf-8") as f:
            json.dump(deep, f, ensure_ascii=False, separators=(",", ":"))
    print(f"delta.json: 카드 {len(delta['cards'])}장 · deep_delta.json: {len(deep)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
