#!/usr/bin/env python3
"""GRM 자료실 수집기 — 21 CFR Part 210/211 (미국 cGMP 규정 원문, 조항 단위).

자료실 플러그인 계약:
  LIBRARY_SOURCE = "cfr"
  collect_library_items(run_date) -> (items, error)
  - 실패는 반드시 error 문자열로 보고한다(빈 리스트로 성공을 가장하지 않는다).

범위 (이번 초판 — 2026-08-11 확정):
  21 CFR **Part 210 + Part 211 만.** findings.cfr_refs 실측 4,469건 중 Part 211 이
  2,523건(56%), 210+211 합산 59% 로 압도적 1순위이고 두 Part 모두 [Reserved] 없이
  전 조항이 활성이다(실측 재확인: 210=3섹션·211=60섹션, reserved 0건 — 아래 참고).
    - 11/600/610/4 는 인용 1~3건뿐이라 초판 근거가 약해 제외.
    - 820(의료기기 QSR)은 2026-02-06 개정으로 활성 조항이 6개뿐이고 실질 내용이
      "ISO 13485 참조"라 자료실이 '완결된 원문'으로 오인 표시할 위험이 있어 제외.
    - Part 201(라벨링, 504건/10섹션)은 wave 2 후보 — 이번엔 넣지 않는다.

id 규칙:
  cfr-<part>-<section 번호>  (예: "21 CFR 211.192" → cfr-211-192, 결정론·영속)
  eCFR identifier(예: "211.192")를 "." 에서 쪼개 그대로 이어붙인다 — part/section
  번호가 유일 키이므로 이 규칙이 곧 안정 식별자다.

title_ko 를 비워 두는 이유(중요 — 빈 필드가 결손으로 오인되지 않도록 명시):
  21 CFR 조문 제목은 식약처 등 공식 번역본이 별도로 존재한다. 자료실은 "추측 번역
  금지" 원칙을 쓰므로 공식 번역이 없는 상태에서 임의 번역을 채우면 원문 대조 없이
  퍼지는 오역 위험이 더 크다. 그래서 title_ko 는 의도적으로 비워 둔다(결손이 아니다).

수집 경로 (실측 2026-08-11):
  구조 데이터 — https://www.ecfr.gov/api/versioner/v1/structure/{issue_date}/title-21.json
    issue_date 는 하드코딩하지 않는다. titles.json 이 주는 title 21 의
    latest_issue_date 를 그대로 쓴다(결정론 — 그날그날 최신 유효일).
    응답은 identifier/label/label_description/reserved/type/children 트리(part→
    (subpart)→section). Part 210 은 section 이 part 바로 아래(서브파트 없음),
    Part 211 은 11개 subpart 아래 section 이 있다 — 트리 깊이가 달라 재귀 순회로
    both 케이스를 다 잡는다(_iter_sections).
  섹션 원문 URL — https://www.ecfr.gov/current/title-21/part-{part}/section-{id}
    실측: 이 짧은 형태가 200 으로 정식 breadcrumb 형태(.../chapter-I/subchapter-C/
    part-211/subpart-J/section-211.192)로 리다이렉트된다. chapter/subchapter 값을
    하드코딩하지 않아도 되고, 향후 챕터 재편에도 안정적이다.
  ⚠️ ecfr.gov 는 봇으로 의심되는 UA(브라우저 UA 포함 python-requests 기본 UA 도)에
     **200 응답으로 위장한 차단 페이지**(unblock.federalregister.gov 리다이렉트)를
     돌려주기도 한다 — library_linkcheck.py 의 `_classify()` 는 이걸 잡도록 별도
     수리했다(호스트가 바뀌는 리다이렉트 = 의심). 이 수집기 자체는 구조 API(JSON)만
     쓰고 섹션 페이지 HTML 은 절대 가져오지 않으므로(원문 파싱 없음) 그 차단의
     영향을 받지 않는다 — 셀렉션·베이스라인 생성은 API 응답에만 의존한다.

published_date:
  섹션 노드의 received_on(예: "2017-01-05T00:00:00-0500")에서 날짜부만 취한다 —
  eCFR 이 이 섹션의 현재 버전이 반영된 날짜로, "최신 개정일"에 해당하는 유일한
  필드다. 얻을 수 없는 섹션은 필드를 아예 비운다(지어내지 않는다).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterator

from grm_common import http_get_json, log

LIBRARY_SOURCE = "cfr"

USER_AGENT = "GRM-Library/1.0 (+https://grm-solutions.com)"
TITLES_URL = "https://www.ecfr.gov/api/versioner/v1/titles.json"
STRUCTURE_URL_TMPL = "https://www.ecfr.gov/api/versioner/v1/structure/{issue_date}/title-21.json"
SECTION_URL_TMPL = "https://www.ecfr.gov/current/title-21/part-{part}/section-{identifier}"

TITLE_NUMBER = 21
TARGET_PARTS = ("210", "211")   # 확정 범위 — 위 docstring 근거. 임의 추가 금지.
DOC_TYPE = "regulation-section"

_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _iter_sections(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """part 노드 아래 section 타입 노드를 깊이 무관하게 재귀 수집(네트워크 없음).

    Part 210 은 section 이 part 바로 아래, Part 211 은 subpart 아래에 있다 —
    구조가 달라도 이 재귀 하나로 둘 다 잡는다."""
    for child in node.get("children") or []:
        if child.get("type") == "section":
            yield child
        else:
            yield from _iter_sections(child)


def find_target_parts(structure: dict[str, Any], parts: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """title 구조 트리에서 identifier 가 parts 에 속하는 type=='part' 노드를 찾는다(네트워크 없음)."""
    found: dict[str, dict[str, Any]] = {}
    wanted = set(parts)

    def walk(node: dict[str, Any]) -> None:
        if node.get("type") == "part" and node.get("identifier") in wanted:
            found[node["identifier"]] = node
        for child in node.get("children") or []:
            walk(child)

    walk(structure)
    return found


def section_item(part: str, section: dict[str, Any]) -> dict[str, str] | None:
    """section 노드 → 카탈로그 항목(네트워크 없음). reserved/제목 결측이면 버린다."""
    identifier = str(section.get("identifier") or "").strip()
    if not identifier or section.get("reserved"):
        return None
    title_en = str(section.get("label_description") or "").strip()
    if not title_en:
        return None
    slug = identifier.replace(".", "-")
    item: dict[str, str] = {
        "id": f"cfr-{slug}",
        "code": f"21 CFR {identifier}",
        "title_en": title_en,
        "doc_type": DOC_TYPE,
        "official_url": SECTION_URL_TMPL.format(part=part, identifier=identifier),
    }
    received = _ISO_DATE_RE.match(str(section.get("received_on") or ""))
    if received:
        item["published_date"] = received.group(1)
    return item


def derive_items(structure: dict[str, Any], parts: tuple[str, ...] = TARGET_PARTS) -> list[dict[str, str]]:
    """구조 트리 → 대상 Part 들의 section 카탈로그 항목 목록(네트워크 없음, 결정론)."""
    found = find_target_parts(structure, parts)
    items: list[dict[str, str]] = []
    for part in parts:
        node = found.get(part)
        if not node:
            continue
        for section in _iter_sections(node):
            item = section_item(part, section)
            if item:
                items.append(item)
    return items


def _latest_issue_date() -> tuple[str | None, str | None]:
    """title 21 의 최신 유효일을 API 에서 그대로 얻는다(하드코딩 금지). 반환 (date, error)."""
    try:
        payload = http_get_json(TITLES_URL, timeout=30, retries=2,
                                headers={"User-Agent": USER_AGENT})
    except RuntimeError as exc:
        return None, f"eCFR titles.json 조회 실패: {exc}"
    titles = payload.get("titles") if isinstance(payload, dict) else None
    if not isinstance(titles, list):
        return None, f"eCFR titles.json 응답 구조 이상(titles 배열 없음): {list(payload)[:5] if isinstance(payload, dict) else type(payload)}"
    for entry in titles:
        if isinstance(entry, dict) and entry.get("number") == TITLE_NUMBER:
            issue_date = str(entry.get("latest_issue_date") or "").strip()
            if issue_date:
                return issue_date, None
            return None, f"title {TITLE_NUMBER} latest_issue_date 결측"
    return None, f"eCFR titles.json 에 title {TITLE_NUMBER} 없음"


def collect_library_items(run_date: date) -> tuple[list[dict], str | None]:
    """21 CFR Part 210/211 조항 수집 진입점. 반환 (items, error)."""
    issue_date, err = _latest_issue_date()
    if err or not issue_date:
        return [], err or "issue_date 조회 실패"

    structure_url = STRUCTURE_URL_TMPL.format(issue_date=issue_date)
    try:
        structure = http_get_json(structure_url, timeout=90, retries=2,
                                  headers={"User-Agent": USER_AGENT})
    except RuntimeError as exc:
        return [], f"eCFR title-21 구조 수집 실패({structure_url}): {exc}"

    found = find_target_parts(structure, TARGET_PARTS)
    missing = [part for part in TARGET_PARTS if part not in found]
    if missing:
        return [], f"eCFR title-21 구조에서 Part {', '.join(missing)} 를 찾지 못함(구조 변경 의심)"

    items = derive_items(structure, TARGET_PARTS)
    if not items:
        return [], f"21 CFR Part {'/'.join(TARGET_PARTS)} section 0건(구조 변경 의심)"

    log("INFO", f"21 CFR 자료실 수집: issue_date={issue_date} "
                f"Part {'/'.join(TARGET_PARTS)} section {len(items)}건")
    return items, None


if __name__ == "__main__":
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    import json

    collected, err = collect_library_items(date.today())
    print(json.dumps({"count": len(collected), "error": err, "items": collected},
                     ensure_ascii=False, indent=2))
