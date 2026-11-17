"""호별 브리프 품질 지표 기록 도구 (읽기 전용 · 네트워크 없음).

발행본 `web/data/briefs/brief_web_YYYY_MM_DD.json` 을 읽어 말투·구조 지표를 같은 규칙으로 세고,
호마다 한 줄(JSONL)로 누적한다. 호 간 비교용 `--csv` 도 만든다. 지표 정의의 출처는
2026-11-16 재측정 스크립트(brief_quality_remeasure_1116.py)이며 아래 정의를 그대로 옮겼다.

사용(발행 뒤 비서실이 손으로 한 번 실행, 자동 실행 없음):
  python brief_metrics_ledger.py web/data/briefs/brief_web_2026_11_16.json [...더 많은 호]
      --ledger company/metrics/brief_ledger.jsonl(기본값) [--csv brief_metrics_ledger.csv] [--print]

같은 run_date_kst 가 이미 있으면 그 줄을 덮어쓴다(멱등). 발행본·지시문은 읽기만 한다.
카드 수에 비해 모집단이 작은 지표(소식·지침 카드 등)는 분모를 같이 남긴다.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

DEFAULT_LEDGER = Path(__file__).resolve().parent / "company" / "metrics" / "brief_ledger.jsonl"
CARD_FIELDS = ["title_issue", "hook_line", "summary", "key_facts", "implication", "checks"]
INFO_TYPES = ("규제 소식", "지침·안내서", "고시·개정법령")
DATE8 = r"(?<!\d)(?:19|20)\d{6}(?!\d)"


def flat(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        out = []
        for x in v:
            out += flat(x)
        return out
    if isinstance(v, dict):
        out = []
        for x in v.values():
            out += flat(x)
        return out
    return []


def deep_prose(da):
    """deep_analysis 의 산문. 원문 그대로인 original·citation 은 뺀다."""
    out = []

    def walk(v, key=None):
        if isinstance(v, str):
            if key not in ("original", "citation"):
                out.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)

    walk(da)
    return out


def ctext(c):
    return "\n".join(flat([c.get(f) for f in CARD_FIELDS]))


def dtext(c):
    return "\n".join(deep_prose(c["deep_analysis"]))


def _rd(c):
    return c.get("deep_analysis") or {}


def measure(brief):
    """발행본 dict -> {지표키: 정수}. 키 순서·이름은 안정(호 간 비교용)."""
    cs = brief.get("cards") or []
    m = collections.OrderedDict()
    m["cards"] = len(cs)

    def cnt(key, pat, src=ctext, cond=None):
        rx = re.compile(pat)
        cards = occ = 0
        for c in cs:
            if cond and not cond(c):
                continue
            n = len(rx.findall(src(c)))
            if n:
                cards += 1
                occ += n
        m[key + "_cards"] = cards
        m[key + "_occ"] = occ

    # 카드 앞면
    cnt("sinho", "신호")
    cnt("pilyo_ga_itda", "필요가 있")
    cnt("hwakin_charye", "확인할 차례")
    cnt("ilbanjeok_yeongwan", r"일반적으로[^.]*연관될 수 있")
    cnt("wonmun_hwakin", "원문 확인이 필요")
    cnt("fda_ga_spaced", r"FDA [가는](?=[ ,.])|FDA [가는]\b")
    cnt("date8", DATE8)
    cnt("unit_caps_or_glued", r"\d\s?(?:MG|ML|MCG)\b|\d(?:mg|mcg|mL)\b")
    q = tot = 0
    for c in cs:
        for x in c.get("checks") or []:
            tot += 1
            if "?" in x:
                q += 1
    m["checks_question"] = q
    m["checks_total"] = tot
    m["hook_line_cards"] = sum(1 for c in cs if (c.get("hook_line") or "").strip())
    m["implication_empty_cards"] = sum(1 for c in cs if not (c.get("implication") or "").strip())
    ends = collections.Counter()
    for c in cs:
        imp = (c.get("implication") or "").strip()
        if imp:
            s = re.split(r"(?<=[다요])\.\s*", imp.rstrip("."))[-1].strip()
            ends[s[-4:] if len(s) >= 4 else s] += 1
    top = ends.most_common(1)
    m["implication_nonempty_cards"] = sum(ends.values())
    m["implication_end4_top"] = top[0][0] if top else ""
    m["implication_end4_top_cards"] = top[0][1] if top else 0
    m["implication_end_jijeokhaetda_cards"] = ends.get("지적했다", 0)

    # 심층 칸
    dc = [c for c in cs if c.get("deep_analysis")]
    has_deep = lambda c: c.get("deep_analysis")  # noqa: E731
    m["deep_cards"] = len(dc)
    cnt("deep_ganeungseong", r"가능성이 (?:있|크)", dtext, has_deep)
    cnt("deep_chegyejeok", "체계적", dtext, has_deep)
    cnt("deep_jeonban", "전반", dtext, has_deep)
    cnt("deep_warning_letter_en", "Warning Letter", dtext, has_deep)
    cnt("deep_gwisa", "귀사", dtext, has_deep)
    cnt("deep_483_fixed_sentence", r"483은 최종 판정이 아니", dtext, has_deep)
    cnt("deep_haeseokdoel", r"해석될|소지가|승격", dtext, has_deep)
    cnt("deep_date8", DATE8, dtext, has_deep)
    is483 = lambda c: str(c.get("card_type") or "").startswith("FDA 483")  # noqa: E731
    m["deep_483_cards"] = sum(1 for c in dc if is483(c))
    m["deep_483_deadline_15days"] = sum(
        1 for c in dc if is483(c) and "15영업일" in str((_rd(c).get("required_remediation") or {}).get("deadline", "")))
    m["deep_483_items_nonempty"] = sum(
        1 for c in dc if is483(c) and (_rd(c).get("required_remediation") or {}).get("items"))

    # 구조·업계 표기
    info = lambda c: c.get("card_type") in INFO_TYPES  # noqa: E731
    m["info_cards"] = sum(1 for c in cs if info(c))
    m["info_keyfacts_applies_to"] = sum(
        1 for c in cs if info(c) and any(str(x).startswith("적용 대상") for x in c.get("key_facts") or []))
    m["info_keyfacts_empty"] = sum(1 for c in cs if info(c) and not c.get("key_facts"))
    cnt("double_subject", r"\b[가-힣A-Za-z]+가 (?:FDA|EMA|MHRA|식약처)가",
        cond=lambda c: c.get("card_type") in ("규제 소식", "지침·안내서"))
    adm = [c for c in cs if c.get("card_type") == "행정처분"]
    m["admin_cards"] = len(adm)
    m["admin_summary_generic_subject"] = sum(
        1 for c in adm if re.match(
            r"\s*식약처가 (?:제약사|업체|의료기관|의약품[^에]*업체|[^ ]*업체|[^ ]*제약사)에", c.get("summary") or ""))
    m["admin_implication_clause_only"] = sum(
        1 for c in adm if re.search(r"행정처분의 기준|개별기준|일반기준", c.get("implication") or ""))
    m["admin_implication_empty"] = sum(1 for c in adm if not (c.get("implication") or "").strip())
    m["obs_prefix_implication"] = sum(
        1 for c in cs if re.search(r"(?:^|[.\s])관찰 \d+[:：]", c.get("implication") or ""))
    m["obs_tail_implication"] = sum(1 for c in cs if re.search(r"\(관찰 \d+\)", c.get("implication") or ""))
    for key, w in [("term_jibrak", "집락형성단위"), ("term_pyeongil", "평일"), ("term_gyeonghyang_ital", "경향이탈"),
                   ("term_sponsor", "스폰서"), ("term_trial_binder", "시험 기본 문서철"),
                   ("term_trial_master_file", "시험 마스터 파일"), ("term_jeokgyeok_nospace", "적격성평가"),
                   ("term_jeokgyeok_space", "적격성 평가"), ("term_class_i", "class I")]:
        cnt(key, re.escape(w))
    return m


def ratio(a, b):
    return round(a / b, 4) if b else None


def derived(m):
    """사람이 바로 보는 비율. 분모 0 이면 None."""
    return {
        "checks_question_ratio": ratio(m["checks_question"], m["checks_total"]),
        "hook_line_ratio": ratio(m["hook_line_cards"], m["cards"]),
        "implication_end_jijeokhaetda_ratio": ratio(m["implication_end_jijeokhaetda_cards"], m["implication_nonempty_cards"]),
        "deep_483_deadline_15days_ratio": ratio(m["deep_483_deadline_15days"], m["deep_483_cards"]),
    }


def record_for(path: Path):
    brief = json.loads(path.read_text(encoding="utf-8"))
    head = brief.get("brief") or {}
    run = head.get("run_date_kst") or head.get("publish_date")
    if not run:
        raise ValueError(f"{path}: run_date_kst 없음")
    rec = collections.OrderedDict()
    rec["run_date_kst"] = run
    rec["source"] = path.name
    rec.update(measure(brief))
    rec.update(derived(rec))
    return rec


def read_ledger(path: Path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def upsert(rows, rec):
    """같은 run_date_kst 는 덮어쓰고, 결과는 날짜순."""
    out = [r for r in rows if r.get("run_date_kst") != rec["run_date_kst"]] + [rec]
    return sorted(out, key=lambda r: r["run_date_kst"])


def write_ledger(path: Path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")


def write_csv(path: Path, rows):
    cols = []
    for r in rows:  # 열은 등장 순서의 합집합(지표가 늘어도 옛 줄은 빈칸)
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("briefs", nargs="+", type=Path, help="brief_web_*.json 발행본")
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--print", action="store_true", dest="show", help="기록 대신(또는 함께) 표로 출력")
    ap.add_argument("--no-write", action="store_true", help="파일에 쓰지 않는다")
    a = ap.parse_args(argv)
    recs = [record_for(p) for p in a.briefs]
    if not a.no_write:
        rows = read_ledger(a.ledger)
        for r in recs:
            rows = upsert(rows, r)
        a.ledger.parent.mkdir(parents=True, exist_ok=True)
        write_ledger(a.ledger, rows)
        if a.csv:
            a.csv.parent.mkdir(parents=True, exist_ok=True)
            write_csv(a.csv, rows)
    if a.show or a.no_write:
        sys.stdout.reconfigure(encoding="utf-8")
        keys = list(recs[0])
        print("| 지표 | " + " | ".join(r["run_date_kst"] for r in recs) + " |")
        print("|---|" + "---|" * len(recs))
        for k in keys[2:]:
            print(f"| {k} | " + " | ".join(str(r.get(k)) for r in recs) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
