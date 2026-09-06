#!/usr/bin/env python3
"""자가점검 체크리스트 사례 **전수 가드** — 조항마다 실린 문장이 그 조항의 지적인가.

## 무엇을 지키나

`/findings/checklist/` 는 "조항마다 실제 지적 문장"을 약속하고 **인쇄해서 쓰는** 점검표다.
약속과 다른 것이 실리는 것은 표시 결함이 아니라 정직성 결함이라, 화면이 아니라 데이터에서
전수로 막아야 한다. 이 스크립트가 042 가 아는 **모든 조항**에 대해 043→079 가 실제로
내려주는 사례를 받아 아래를 검사한다(하나라도 깨지면 exit 1).

  ① **앵커** — 인용된 조항 번호가 그 사례 발췌 문장에 **실제로 등장**한다. 국문·영문
     양쪽을 따로 본다(화면이 언어를 골라 쓰므로 한쪽만 맞으면 다른 언어에서 거짓이 된다).
     경계까지 본다: `211.22` 질의에 본문 `211.226` 만 있으면 앵커가 아니다.
  ② **비지적 문장 금지** — 편지 서두·수신문처럼 지적이 아닌 문장이 사례로 실리면 안 된다.
     (2026-09-06 실측 결함: 211.22·211.42 의 사례가 "귀사의 의약품 제조시설인 PReye,
     LLC(FEI 3031057987…)에 대하여 …실사를 실시하였다" — 편지 서두였다.)
  ③ **하한** — 화면이 실제로 만들 수 있는 조항(042 순위 상위 20 · 두 정렬 기준 모두)은
     사례가 **1건 이상**이어야 한다. 과잉 필터로 사례가 0이 되는 것도 결함이다.
     그 밖의 꼬리 조항이 0인 것은 사실이므로 실패시키지 않고 **목록으로 남긴다**(침묵 금지).
  ④ **업체 중복 없음** — 한 조항 안에 같은 업체가 두 번 나오면 "여러 곳에서 반복되는
     지적"이라는 점검표의 전제가 깨진다.

## 검사기가 스스로 눈멀지 않게 — 뮤테이션 자가시험

새 가드의 위험은 "아무것도 안 걸리는데 초록"이다(이 저장소가 여러 번 겪었다). 그래서 본
검사 전에 **알려진 양성 표본**으로 검사기 자신을 시험한다:

  · ① 검사기에 **수리 전 실측 발췌**(Jabil 211.22(d) 앞머리)를 211.100 사례로 먹여 보고,
    반드시 **불합격 판정**이 나오는지 확인한다.
  · ② 검사기에 **수리 전 실측 서두**(PReye 경고서한 첫 문장)를 먹여 보고, 반드시
    **비지적 판정**이 나오는지 확인한다.
  · 조항 경계는 라이브 `findings_clause_excerpt` 로 직접 물어본다(`211.226` 본문이
    `211.22` 앵커로 잡히면 실패).

자가시험이 실패하면 본 검사를 돌리지 않고 즉시 exit 1 한다 — 눈먼 초록보다 낫다.

## 권한 — anon 키를 쓴다(service-role 금지)

`findings_checklist`(079)는 **security invoker** 라 RLS(010)가 공개 집합을 정의한다.
service-role 로 부르면 RLS 를 우회해 **화면에 없는 행까지** 검사 대상이 되어, 가드가
사용자가 보는 것과 다른 세계를 검사하게 된다. `glossary_cases_refresh.py`·
`findings_facets_refresh.py` 와 같은 판단이다.

사용:
    python verify_checklist_examples.py                       # 042 전 조항
    python verify_checklist_examples.py --examples 5          # 조항당 최대치로
    python verify_checklist_examples.py --sections 211.22,211.42
    python verify_checklist_examples.py --json out.json --summary $GITHUB_STEP_SUMMARY
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

import requests

from grm_cli import normalize_supabase_url as _normalize_supabase_url

REPORT_SCHEMA_VERSION = "grm-checklist-examples-verify/v1"

_HTTP_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 2

# 화면(web/templates/checklist.html)이 고를 수 있는 최대 설정. 하한(③)은 이 범위에만
# 건다 — 그 밖의 조항은 `?section=` 으로만 닿고, 사례가 없으면 화면이 없다고 말한다.
UI_MAX_SECTIONS = 20
UI_MAX_EXAMPLES = 5


# ---------------------------------------------------------------------------
# 판정기
# ---------------------------------------------------------------------------

def clause_anchor_re(section: str) -> "re.Pattern[str]":
    """`211.22` 가 `211.226` 에 걸리지 않는 조항 경계 정규식(079 SQL 과 같은 규칙)."""
    return re.compile(re.escape(section) + r"(?![0-9])")


def anchored_in(text: str, section: str) -> bool:
    """① 발췌 문장에 그 조항 번호가 실제로 등장하는가."""
    return bool(clause_anchor_re(section).search(text or ""))


# ② 비지적 문장 판정 — **편지가 열리는 자리의 정형문**만 잡는다.
#
# ★이 목록은 "고치는 장치"가 아니라 "고쳐졌는지 묻는 장치"다. 진짜 방어는 079 가
#   발췌를 조항 위치에서 뜨는 것이고(서두는 특정 조항 번호를 인용하지 않으므로 구조적으로
#   고를 수 없다), 이 정규식은 그 구조가 무너졌을 때 **소리를 내는 계기**다. 그래서 목록이
#   낡는 위험은 아래 뮤테이션 자가시험이 받는다 — 실측 서두가 더 이상 안 걸리면 즉시 실패.
_NON_FINDING_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("wl_opening_en", re.compile(
        r"\b(?:we|fda)\s+conducted\s+an?\s+inspection\s+of\s+your\b"
        r"|\bthis\s+(?:warning\s+)?letter\s+(?:summarizes|notifies|is\s+to\s+advise)\b"
        r"|\bthe\s+u\.?s\.?\s+food\s+and\s+drug\s+administration\s+\(fda\)\s+(?:inspected|conducted)\b",
        re.I)),
    ("wl_opening_ko", re.compile(
        r"실사를\s*실시하였(?:다|습니다)"
        r"|본\s*경고서한은"
        r"|이\s*실사는\s*안전하지"
        r"|공중을\s*보호하기\s*위한\s*FDA의\s*법적\s*권한", re.I)),
    ("addressee", re.compile(
        r"^\s*(?:dear\s+(?:mr|ms|mrs|dr)\b|귀하께|수신\s*[:：])", re.I)),
    ("fei_header", re.compile(
        r"\bFDA\s+Establishment\s+Identifier\b|\bFEI\s*(?:number)?\s*[:#]?\s*\d{7,}", re.I)),
)


def non_finding_hits(text: str) -> list[str]:
    """② 이 문장이 지적이 아니라 서두·수신문으로 보이는 근거 목록(비었으면 통과)."""
    return [name for name, pat in _NON_FINDING_PATTERNS if pat.search(text or "")]


# ---------------------------------------------------------------------------
# PostgREST (anon)
# ---------------------------------------------------------------------------

def _post_rpc(base_url: str, anon_key: str, name: str, body: dict[str, Any],
              *, timeout: int = _HTTP_TIMEOUT_SECONDS) -> tuple[Any, str]:
    """POST rpc/<name> — anon 키. 반환 (parsed_json_or_None, error_summary).

    키는 공개값이지만 에러 문자열에는 절대 넣지 않는다(저장소 관례).
    """
    url = f"{base_url}/rest/v1/rpc/{name}"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return None, f"{name}: timeout"
        except requests.exceptions.RequestException as exc:
            return None, f"{name}: {type(exc).__name__}"
        if resp.status_code >= 500 and attempt < _MAX_ATTEMPTS:
            continue
        if resp.status_code >= 400:
            return None, f"{name}: http_{resp.status_code}"
        try:
            return resp.json(), ""
        except ValueError:
            return None, f"{name}: invalid_response_shape"
    return None, f"{name}: retry_exhausted"


def fetch_ranking(base_url: str, anon_key: str) -> tuple[list[dict[str, Any]], str]:
    """042 — 조항 순위 정본. 조항 목록·상위 N 판정을 여기서만 얻는다(손목록 금지)."""
    data, err = _post_rpc(base_url, anon_key, "findings_cfr_ranking", {"p_months": 12})
    if err:
        return [], err
    items = (data or {}).get("items")
    if not isinstance(items, list) or not items:
        return [], "findings_cfr_ranking: 빈 순위(042 장애 의심)"
    return items, ""


def fetch_checklist(base_url: str, anon_key: str, sections: list[str],
                    examples: int) -> tuple[list[dict[str, Any]], str]:
    """079 — 조항별 사례. 화면과 **같은 함수·같은 게이트**를 쓴다."""
    data, err = _post_rpc(base_url, anon_key, "findings_checklist",
                          {"p_sections": sections, "p_examples": examples})
    if err:
        return [], err
    got = (data or {}).get("sections")
    if not isinstance(got, list):
        return [], "findings_checklist: 응답에 sections 배열이 없다"
    return got, ""


# ---------------------------------------------------------------------------
# 뮤테이션 자가시험 — 검사기가 실제로 무언가를 잡는지 먼저 증명한다
# ---------------------------------------------------------------------------

# 수리 전(043) 실측값. 이 문자열들이 더 이상 "불합격"으로 판정되지 않으면 검사기가 눈이
# 먼 것이다 — 본 검사를 돌리기 전에 멈춘다.
_FIXTURE_WRONG_CLAUSE = (
    "귀사는 품질관리부서에 적용되는 적절한 문서화된 책임과 절차를 수립하지 못하였다"
    "(21 CFR 211.22(d)). 귀사의 품질부서(QU)는 의약품 제조에 대한 적절한 감독을 제공하지 "
    "않았다. 예를 들어 QU는 다음을 보증하지 못하였다."
)
_FIXTURE_PREAMBLE_KO = (
    "귀사의 의약품 제조시설인 PReye, LLC(FDA Establishment Identifier(FEI) 3031057987, "
    "4855 Ward Road, Wheat Ridge 소재)에 대하여 2026년 3월 17일부터 19일까지 실사를 "
    "실시하였다. 이 실사는 안전하지 않거나 유효하지 않거나 품질이 낮은 의약품으로부터 "
    "공중을 보호하기 위한 FDA의 법적 권한과 공중보건상의 책임에 따라 수행되었다."
)
# ★영문은 **화면에 실제로 실렸던 240자 그대로**다(짓지 않는다). 이 편지는 "during an
#   inspection of…"로 열려 `wl_opening_en` 에는 안 걸리고 `fei_header` 에 걸린다 — 서두를
#   잡는 길이 하나뿐이 아니어야 한다는 것을 이 표본이 증명한다.
_FIXTURE_PREAMBLE_EN = (
    "during an inspection of your drug manufacturing facility, PReye, LLC, FDA "
    "Establishment Identifier (FEI) 3031057987, at 4855 Ward Road, Wheat Ridge, from "
    "March 17 to 19, 2026. This inspection was conducted under FDA's statutory authority an"
)
# 정형 WL 서두("We conducted an inspection of your …") — 이쪽은 `wl_opening_en` 이 잡는다.
_FIXTURE_PREAMBLE_EN_CANONICAL = (
    "We conducted an inspection of your drug manufacturing facility from March 17 to 19, "
    "2026. This warning letter summarizes significant violations of CGMP regulations."
)
_FIXTURE_GOOD = (
    "귀사의 품질관리부서는 제조되는 의약품이 CGMP에 적합하고 확인, 함량, 품질 및 순도에 "
    "관한 설정된 규격을 충족하도록 보증할 책임을 이행하지 못하였다(21 CFR 211.22)."
)


def self_test(base_url: str, anon_key: str) -> list[str]:
    """검사기 자신을 시험한다. 반환: 실패 사유(비었으면 통과)."""
    problems: list[str] = []

    # ① 다른 조항의 문장을 그 조항 사례로 주면 반드시 불합격이어야 한다.
    if anchored_in(_FIXTURE_WRONG_CLAUSE, "211.100"):
        problems.append("자가시험 ①: 211.22(d) 문장이 211.100 앵커로 통과했다(검사기 무력)")
    if not anchored_in(_FIXTURE_WRONG_CLAUSE, "211.22"):
        problems.append("자가시험 ①: 211.22 문장이 211.22 앵커에 걸리지 않는다(검사기 과잉)")
    # 경계: 211.22 질의가 211.226 본문을 삼키면 안 된다.
    if anchored_in("… 위반이다(21 CFR 211.226).", "211.22"):
        problems.append("자가시험 ①: 211.226 본문이 211.22 앵커로 통과했다(경계 소실)")

    # ② 실측 서두는 반드시 비지적으로 잡혀야 하고, 실측 지적은 잡히면 안 된다.
    if not non_finding_hits(_FIXTURE_PREAMBLE_KO):
        problems.append("자가시험 ②: 실측 국문 서두가 비지적으로 잡히지 않는다(검사기 무력)")
    if not non_finding_hits(_FIXTURE_PREAMBLE_EN):
        problems.append("자가시험 ②: 실측 영문 서두가 비지적으로 잡히지 않는다(검사기 무력)")
    if not non_finding_hits(_FIXTURE_PREAMBLE_EN_CANONICAL):
        problems.append("자가시험 ②: 정형 영문 WL 서두가 비지적으로 잡히지 않는다(검사기 무력)")
    if non_finding_hits(_FIXTURE_GOOD):
        problems.append("자가시험 ②: 정상 지적 문장이 비지적으로 잡힌다(검사기 과잉)")

    # 조항 경계는 라이브 함수에도 물어본다 — 화면이 실제로 쓰는 구현이 정본이다.
    data, err = _post_rpc(base_url, anon_key, "findings_clause_excerpt",
                          {"p_text": "앞 문장. 뒤 문장(21 CFR 211.226).", "p_section": "211.22"})
    if err:
        problems.append(f"자가시험: findings_clause_excerpt 호출 실패 — {err}")
    elif (data or {}).get("anchored"):
        problems.append("자가시험: 라이브 findings_clause_excerpt 가 211.226 을 211.22 로 앵커했다")

    data, err = _post_rpc(base_url, anon_key, "findings_clause_excerpt",
                          {"p_text": _FIXTURE_GOOD, "p_section": "211.22"})
    if err:
        problems.append(f"자가시험: findings_clause_excerpt 호출 실패 — {err}")
    elif not (data or {}).get("anchored") or not anchored_in(
            str((data or {}).get("text") or ""), "211.22"):
        problems.append("자가시험: 라이브 findings_clause_excerpt 가 정상 인용을 앵커하지 못했다")

    return problems


# ---------------------------------------------------------------------------
# 본 검사
# ---------------------------------------------------------------------------

def check_section(section: str, examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """한 조항의 사례 전부를 검사한다. 반환: 위반 목록."""
    problems: list[dict[str, Any]] = []
    seen_firms: dict[str, str] = {}
    for idx, ex in enumerate(examples, start=1):
        ko = str(ex.get("excerpt_ko") or "")
        en = str(ex.get("excerpt") or "")
        fid = str(ex.get("finding_id") or "")
        firm = str(ex.get("firm_name") or "")

        def add(rule: str, detail: str, text: str) -> None:
            problems.append({
                "section": section, "rank": idx, "finding_id": fid, "firm_name": firm,
                "rule": rule, "detail": detail, "excerpt": text[:300],
            })

        if not ko and not en:
            add("empty", "국문·영문 발췌가 모두 비었다", "")
            continue

        # ① 앵커 — 있는 언어는 전부 그 조항을 담고 있어야 한다.
        if ko and not anchored_in(ko, section):
            add("anchor_ko", f"국문 발췌에 21 CFR {section} 이 없다", ko)
        if en and not anchored_in(en, section):
            add("anchor_en", f"영문 발췌에 21 CFR {section} 이 없다", en)

        # ② 비지적 문장 금지.
        for lang, text in (("ko", ko), ("en", en)):
            hits = non_finding_hits(text)
            if hits:
                add(f"non_finding_{lang}", "서두·수신문 정형문: " + ", ".join(hits), text)

        # ④ 업체 중복 금지.
        key = firm.strip().lower()
        if key and key in seen_firms:
            add("duplicate_firm",
                f"같은 업체가 이 조항에서 두 번 나온다(먼저: {seen_firms[key]})", ko or en)
        elif key:
            seen_firms[key] = fid

    return problems


def run(base_url: str, anon_key: str, *, examples: int,
        only_sections: list[str] | None) -> dict[str, Any]:
    ranking, err = fetch_ranking(base_url, anon_key)
    if err:
        return {"fatal": err}

    # 화면이 실제로 만들 수 있는 조항(③ 하한 대상) = 두 정렬 기준 각각의 상위 20.
    def top(key: str) -> list[str]:
        rows = [i for i in ranking if int(i.get(key) or 0) > 0]
        rows.sort(key=lambda i: (-int(i.get(key) or 0), str(i.get("section"))))
        return [str(i["section"]) for i in rows[:UI_MAX_SECTIONS]]

    reachable = sorted(set(top("docs")) | set(top("recent_docs")))
    all_sections = sorted({str(i.get("section")) for i in ranking if i.get("section")})
    if only_sections:
        all_sections = [s for s in all_sections if s in set(only_sections)]
        reachable = [s for s in reachable if s in set(only_sections)]

    # 043 은 한 번에 50개까지 받는다(009 슬라이스) — 그 상한 안에서 나눠 부른다.
    problems: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for start in range(0, len(all_sections), 50):
        chunk = all_sections[start:start + 50]
        got, err = fetch_checklist(base_url, anon_key, chunk, examples)
        if err:
            return {"fatal": err}
        got_sections = {str(s.get("section")): (s.get("examples") or []) for s in got}
        missing = [s for s in chunk if s not in got_sections]
        if missing:
            return {"fatal": f"findings_checklist 응답에 조항이 빠졌다: {', '.join(missing)}"}
        for section, exs in got_sections.items():
            counts[section] = len(exs)
            problems.extend(check_section(section, exs))

    # ③ 하한 — 화면이 만들 수 있는 조항은 사례가 0이면 안 된다.
    empty_reachable = [s for s in reachable if counts.get(s, 0) == 0]
    empty_tail = [s for s in all_sections if s not in reachable and counts.get(s, 0) == 0]
    for section in empty_reachable:
        problems.append({
            "section": section, "rank": 0, "finding_id": "", "firm_name": "",
            "rule": "no_examples",
            "detail": "화면이 만들 수 있는 조항인데 사례가 0건이다(과잉 필터 의심)",
            "excerpt": "",
        })

    total_examples = sum(counts.values())
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "examples_per_section": examples,
        "sections_checked": len(all_sections),
        "sections_reachable": len(reachable),
        "examples_checked": total_examples,
        "counts": counts,
        "empty_tail_sections": empty_tail,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    """anon 키 자격증명 해석 — glossary_cases_refresh._resolve_credentials 와 동형.

    service-role 을 쓰지 않는 이유는 모듈 docstring 참조(RLS 우회 = 화면과 다른 세계).
    """
    url = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.supabase_anon_key or os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        return None
    return url, key


def _write_summary(path: str, report: dict[str, Any]) -> None:
    problems = report.get("problems") or []
    lines = ["## 체크리스트 사례 전수 가드", ""]
    lines.append(f"- 조항 {report.get('sections_checked')}개 · 사례 "
                 f"{report.get('examples_checked')}건 검사 "
                 f"(조항당 최대 {report.get('examples_per_section')}건)")
    lines.append(f"- 화면이 만들 수 있는 조항 {report.get('sections_reachable')}개 — 전부 사례 1건 이상"
                 if not any(p["rule"] == "no_examples" for p in problems)
                 else "- **하한 위반** — 아래 목록 참조")
    tail = report.get("empty_tail_sections") or []
    if tail:
        lines.append(f"- 사례 0건인 꼬리 조항 {len(tail)}개(사실 — `?section=` 로만 닿음): "
                     + ", ".join(tail))
    lines.append("")
    if problems:
        lines.append(f"### 위반 {len(problems)}건")
        lines.append("")
        lines.append("| 조항 | 순번 | 규칙 | 업체 | 내용 |")
        lines.append("|---|---|---|---|---|")
        for p in problems[:40]:
            excerpt = (p.get("excerpt") or "").replace("|", "\\|")[:120]
            lines.append(f"| {p['section']} | {p['rank']} | {p['rule']} | "
                         f"{(p.get('firm_name') or '').replace('|', '')} | {p['detail']} — {excerpt} |")
        if len(problems) > 40:
            lines.append(f"| … | | | | 나머지 {len(problems) - 40}건은 JSON 산출물 참조 |")
    else:
        lines.append("위반 0건.")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949)에서 로그 한 줄 때문에 죽지 않게 — 이 저장소는
    # 한국어 산출물과 규제 원문(em-dash·불릿)을 그대로 찍는다. brief_lint.py·
    # findings_facets_refresh.py 와 동형(tests/test_cli_stdout_encoding.py 가 전수로 본다).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--supabase-url", help="Supabase project URL ($SUPABASE_URL 폴백)")
    parser.add_argument("--supabase-anon-key",
                        help="Supabase anon key ($SUPABASE_ANON_KEY 폴백). "
                             "findings_checklist 는 anon 실행 권한이 있고 RLS 로 게이트된다 — "
                             "service-role 을 쓰면 화면에 없는 행까지 검사하게 된다.")
    parser.add_argument("--examples", type=int, default=UI_MAX_EXAMPLES,
                        help=f"조항당 검사할 사례 수(기본 {UI_MAX_EXAMPLES} = 화면 최대치)")
    parser.add_argument("--sections", default="",
                        help="쉼표로 구분한 조항 목록(기본: 042 가 아는 전 조항)")
    parser.add_argument("--json", dest="json_path", help="보고서 JSON 경로")
    parser.add_argument("--summary", dest="summary_path", help="GitHub Step Summary 경로")
    args = parser.parse_args(argv)

    creds = _resolve_credentials(args)
    if not creds:
        print("verify_checklist_examples: --supabase-url/--supabase-anon-key 또는 "
              "$SUPABASE_URL/$SUPABASE_ANON_KEY 가 필요하다", file=sys.stderr)
        return 2
    base_url = _normalize_supabase_url(creds[0])
    anon_key = creds[1]

    # ★본 검사 전에 검사기 자신을 시험한다 — 눈먼 초록 금지.
    blind = self_test(base_url, anon_key)
    if blind:
        print("검사기 자가시험 실패 — 본 검사를 돌리지 않는다:", file=sys.stderr)
        for line in blind:
            print(f"  · {line}", file=sys.stderr)
        return 1
    print("검사기 자가시험 통과(수리 전 실측 표본이 전부 불합격으로 잡힘).")

    only = [s.strip() for s in args.sections.split(",") if s.strip()] or None
    report = run(base_url, anon_key, examples=max(1, min(args.examples, UI_MAX_EXAMPLES)),
                 only_sections=only)
    if report.get("fatal"):
        print(f"중단: {report['fatal']}", file=sys.stderr)
        return 1

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    if args.summary_path:
        _write_summary(args.summary_path, report)

    problems = report["problems"]
    print(f"조항 {report['sections_checked']}개 · 사례 {report['examples_checked']}건 검사 "
          f"(조항당 최대 {report['examples_per_section']}건)")
    if report["empty_tail_sections"]:
        print("사례 0건인 꼬리 조항(사실 — 화면이 '없음'을 그대로 말한다): "
              + ", ".join(report["empty_tail_sections"]))
    if not problems:
        print("위반 0건.")
        return 0
    print(f"위반 {len(problems)}건:", file=sys.stderr)
    for p in problems[:40]:
        print(f"  · [{p['rule']}] 21 CFR {p['section']} #{p['rank']} "
              f"{p['firm_name']} — {p['detail']}", file=sys.stderr)
        if p["excerpt"]:
            print(f"      {p['excerpt'][:160]}", file=sys.stderr)
    if len(problems) > 40:
        print(f"  … 나머지 {len(problems) - 40}건", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
