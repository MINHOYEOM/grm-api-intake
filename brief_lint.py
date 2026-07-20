"""GRM 발행물 Lint — 출처 링크 근거(provenance) 하드 가드.

URL전수검사(2026-06-16) Phase E1. 2026-06-15(W24) 발행본의 MFDS 카드 📎 가
`mfds.go.kr/brd/m_99·m_218/view.do?seq=` (보도자료/자료실 게시판) 로 **handoff 근거 없는**
엉뚱한 페이지를 가리킨 사고(AI 환각 의심)를 구조적으로 차단한다.

설계 근거(감사 결과):
- card_scaffold 는 카드 footer 의 듀얼링크를 **결정론으로** 조립한다(Intake 카드).
  Routine(LLM)은 scaffold 문자열을 한 글자도 바꾸지 않는다(v16 프롬프트 [2단계] · Publish Lint 2).
- 그러나 LLM 이 직접 URL 을 쓰는 경로가 있다 — ① 검색 카드 미니 템플릿 📎/📰(v16 L346)
  ② 🔮 Watch 표 링크(L361) ③ graceful degradation(WebSearch 단독) 모드(L151). 이 세 경로는
  코드 레벨 방어가 없고 소프트 프롬프트 규칙(L348 "패턴 유추 금지")만 막는다 → 누출 표면.
- **MFDS 는 Core 검색 슬롯이 없다**(v16 L187: "Intake 흡수가 유일 경로"). 따라서 발행물의 모든
  MFDS/nedrug 링크는 반드시 handoff(수집기 산출)에 근거가 있어야 한다. 근거 없는 mfds/nedrug
  링크 = 날조(누출) → HARD FAIL.

이 모듈은 **순수 함수**(네트워크 없음)다. handoff rows 와 발행 markdown 을 받아,
각 카드 링크가 handoff 근거 집합에 있는지 대조한다. `verify_url_live`/`looks_like_error_page`
만 선택적 네트워크 유틸(수집기 resolve&verify·Phase C 전수검증 재사용용, lazy import).

W1(발행 게이트): `run_publish_gate(rows, markdown)` + `format_report` + CLI(`python -m brief_lint
--handoff h.json --published brief.md`)로 "검사기 존재" 를 "매 발행마다 실행·FAIL 시 차단(exit 1)"
으로 승격한다 — 프롬프트 "지시" 가 아니라 결정론 실행·차단.
W2(전 기관 일반화): `policy=ALL_DOMAINS` 면 MFDS 뿐 아니라 모든 미근거 외부 링크를, 세션 fetch
화이트리스트(`allowed_fetched`)나 live verify(`verifier`) 로 정당화되지 않는 한 FAIL 처리한다.
기본 `lint_link_provenance(...)` 시그니처 동작은 종전과 동일(MFDS 한정 FAIL·그 외 WARN)이라
기존 호출처·테스트는 무회귀.

Lint 번호: 발행 직전 Publish Lint **17**(출처 링크 근거) / 독립 Brief Lint **L11**.

구조 lint(2026-06-17, v16 프롬프트 축소): `lint_publish_structure(md)` 가 v16 [Publish Lint] 의
**기계 판정** 항목(PL1 잔존토큰·PL3/16 금지문법·PL10 제목 미상·PL14 요일=날짜)을 결정론으로
판정한다 — 프롬프트 자가 서술을 코드 실행으로 강등(`run_publish_gate(..., include_structure=True)`
/ CLI `--structure`). 의미 판정 항목은 코드로 이관하지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

SEV_FAIL = "FAIL"
SEV_WARN = "WARN"

# provenance 정책(W2 — 전 기관 일반화). 기본은 종전 동작(MFDS 한정 FAIL·그 외 WARN)을
# 그대로 보존해 기존 호출처·테스트가 무회귀하도록 한다. 발행 게이트(run_publish_gate)는
# ALL_DOMAINS 로 옵트인해 "지어낸 타 기관 URL" 도 차단한다.
POLICY_MFDS_ONLY = "mfds_only"      # 기본: MFDS/nedrug 미근거=FAIL · 그 외 미근거=WARN
POLICY_ALL_DOMAINS = "all_domains"  # 게이트: 미근거 외부 링크도 (fetch 화이트리스트∨live verify) 아니면 FAIL

# MFDS 계열 호스트 — Intake 전용(검색 슬롯 없음). 발행물의 이 도메인 링크는 handoff 근거 필수.
_MFDS_HOST_SUFFIXES = ("mfds.go.kr",)  # www.mfds.go.kr · nedrug.mfds.go.kr 모두 포함
# 사고 시그니처: 식약처 본사이트 게시판 직링크(보도자료 m_99·안내서/지침 m_218 등).
_MFDS_BRD_VIEW_RE = re.compile(r"mfds\.go\.kr/brd/[^/]+/view\.do\?", re.I)

# markdown 링크 `[label](url)` 및 평문 URL 추출.
_MD_LINK_RE = re.compile(r"\]\(\s*(?P<url>https?://[^)\s]+?)\s*\)")
_BARE_URL_RE = re.compile(r"https?://[^\s)\"'<>\]]+")

# nedrug 클라이언트 렌더 오류 셸 마커(HTTP 200 이어도 본문이 이것이면 무효 레코드).
# 라이브 확인(2026-06-16): getItem?dispsApplySeq=<invalid> → 200 + 이 문구.
_NEDRUG_ERROR_MARKERS = (
    "해당 화면 혹은 기능을 찾을 수 없습니다",
    # www.mfds.go.kr/brd/*/view.do?seq=<invented> 계열은 HTTP 200 으로 오류 셸을 돌려줄 수 있다.
    "일시적으로 서비스를 이용할 수 없습니다",
    "요청하신 페이지 주소를 다시 한번 확인",
)
_SHORT_ERROR_MARKERS = (
    # 정상 nedrug 페이지의 로그인 JS 에도 이 문구가 있어, 짧은 오류 셸에서만 단독 마커로 쓴다.
    "오류가 발생하였습니다",
)


@dataclass(frozen=True)
class LintFinding:
    severity: str          # SEV_FAIL | SEV_WARN
    code: str              # 예: "L17-MFDS-PROVENANCE"
    url: str
    message: str

    def __str__(self) -> str:  # 사람이 읽는 한 줄
        return f"[{self.severity}] {self.code} {self.url} — {self.message}"


def normalize_url(url: str) -> str:
    """비교용 정규화: scheme/host 소문자, fragment 제거, 끝 '/' 제거, `?&`→`?`.

    링크 자체는 바꾸지 않는다 — 근거 대조용 키만 만든다.
    """
    if not url:
        return ""
    url = url.strip().replace("&amp;", "&")
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower().rstrip("/")
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    query = parts.query
    # nedrug 등 일부가 `getItem?&dispsApplySeq=` 처럼 선행 '&' 를 쓴다 — 정규화.
    while query.startswith("&"):
        query = query[1:]
    return urlunsplit((scheme, netloc, path, query, ""))


def extract_markdown_links(text: str) -> list[str]:
    """markdown `[..](url)` + 평문 URL 전부 추출(원형 그대로)."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _MD_LINK_RE.finditer(text):
        u = m.group("url").replace("&amp;", "&")
        if u not in seen:
            seen.add(u)
            out.append(u)
    for m in _BARE_URL_RE.finditer(text):
        u = m.group(0).replace("&amp;", "&")
        # markdown 링크로 이미 잡힌 것 제외
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _walk_url_strings(obj: Any) -> Iterable[str]:
    """dict/list/str 를 깊이우선 순회하며 http(s) URL 문자열을 모은다."""
    if obj is None:
        return
    if isinstance(obj, str):
        for m in _BARE_URL_RE.finditer(obj):
            yield m.group(0).replace("&amp;", "&")
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_url_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_url_strings(v)


# handoff row 에서 URL 근거를 담는 v1 호환 필드(build_routine_handoff_payload_v2 기준).
_ROW_URL_FIELDS = ("official_url", "source_url", "api_query", "page_url")


def collect_allowed_urls(rows: list[dict[str, Any]]) -> set[str]:
    """handoff rows 가 정당화하는 URL 근거 집합(정규화 키).

    근거 = ① 각 row 의 `card_scaffold` markdown 안 링크(=scaffold 가 만든 footer 듀얼링크)
    ② v1 호환 url 필드(official_url·source_url·api_query·page_url)
    ③ `prose_input`/`raw`(있으면) 안의 url 문자열(검색카드 보강·후보 url 포함).
    """
    allowed: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for u in extract_markdown_links(row.get("card_scaffold", "") or ""):
            allowed.add(normalize_url(u))
        for f in _ROW_URL_FIELDS:
            v = row.get(f)
            if isinstance(v, str) and v.strip():
                allowed.add(normalize_url(v))
        for key in ("prose_input", "raw", "raw_payload"):
            if row.get(key):
                for u in _walk_url_strings(row.get(key)):
                    allowed.add(normalize_url(u))
    allowed.discard("")
    return allowed


def collect_scaffold_footer_urls(rows: list[dict[str, Any]]) -> list[str]:
    """Intake 카드 scaffold 가 실제 footer 에 싣도록 만든 URL 목록(원형, 중복 제거).

    `card_scaffold` 가 없는 병합 멤버 row 는 렌더 대상이 아니므로 제외한다. 이 목록은
    발행본에 그대로 남아야 하는 결정론 산출물이다.
    """
    out: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        scaffold = row.get("card_scaffold")
        if not isinstance(scaffold, str) or not scaffold.strip():
            continue
        for url in extract_markdown_links(scaffold):
            key = normalize_url(url)
            if key and key not in seen:
                seen.add(key)
                out.append(url)
    return out


def _row_is_rendered(row: dict[str, Any], published_text: str) -> bool:
    """발행본에 이 row 의 카드가 실제로 렌더됐는지 — `document_id`(문서번호 셀, LLM 불변)
    가 발행 평문에 존재하는지로 판정한다. footer URL 이 양쪽 다 변형돼도(예: MFDS 📰·📎
    동시 재구성) 문서번호는 그대로라 렌더 판정이 견고하다. document_id 가 없으면 식별 불가 →
    과알림 0 원칙상 미검사(False)."""
    doc_id = row.get("document_id")
    if not isinstance(doc_id, str) or not doc_id.strip():
        return False
    return doc_id.strip() in (published_text or "")


def lint_scaffold_footer_integrity(rows: list[dict[str, Any]],
                                   published_urls: Iterable[str],
                                   *,
                                   published_text: "str | None" = None
                                   ) -> list[LintFinding]:
    """Intake scaffold footer URL 이 발행본에 글자 그대로 보존됐는지 검사한다.

    `published_text` 가 주어지면 **실제로 렌더된 카드**(그 row 의 `document_id` 가 발행본
    문서번호 셀에 존재)만 검사한다 — Tier 1/용량초과 보류로 의도적으로 생략된 row 의 footer
    가 "누락"으로 오탐되는 것을 막는다(과알림 0). `None` 이면 전수 검사(종전 동작·하위호환).

    live verify 나 fetched 화이트리스트로 구제하지 않는다. 렌더된 카드의 footer(📰/📎)는
    수집기+scaffold 결정론 산출물이므로 발행본에서 사라지면 LLM 이 URL 을 삭제·변형한 것이다
    (예: nedrug→m_74 재구성·blister-pack→package 자동보정).
    """
    published_keys = {normalize_url(u) for u in published_urls if u}
    findings: list[LintFinding] = []
    seen_keys: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        scaffold = row.get("card_scaffold")
        if not isinstance(scaffold, str) or not scaffold.strip():
            continue
        if published_text is not None and not _row_is_rendered(row, published_text):
            continue  # 발행 안 된 카드(Tier1 Skipped/보류) — 무결성 검사 제외
        for expected_url in extract_markdown_links(scaffold):
            key = normalize_url(expected_url)
            if not key or key in seen_keys:
                continue
            if key not in published_keys:
                seen_keys.add(key)
                findings.append(LintFinding(
                    SEV_FAIL, "L17-SCAFFOLD-FOOTER-MISSING", expected_url,
                    "렌더된 Intake 카드의 scaffold footer URL 이 발행본에 없음 — footer "
                    "링크가 삭제·변형된 것으로 보임. live verify/fetched 관용 없이 scaffold "
                    "원문 URL 로 복원해야 함."))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# scaffold 고정 셀(W2 메타표) 전사 무결성 (PL18) — Routine(LLM) 이 scaffold 의 **authoritative
# identity 셀**(수집기가 원문에서 박은 정본)을 글자그대로 렌더했는지 발행 후 결정론 대조.
# 06-22 사고 클래스(FDA 483 FEI·시설유형 전사오류 + Lancora Class 과억제 삭제 + admin 처분일
# 오사용)를 차단한다. footer URL 은 lint_scaffold_footer_integrity, 표 고정 셀 텍스트는 이 함수.
#
# 스코프(06-22 실데이터 보정 — FP 0/TP 8 정본):
#   · **identity 셀(라벨 화이트리스트)** = FEI 동반 제조소/업체·문서번호·시설유형·Class·제품 등
#     전 소스 공통 수집기 정본 → **전 소스 verbatim 강제**.
#   · **날짜 셀**(값이 날짜형) = `fda483-`/`admin-` 카드의 발행일/처분일만 수집기가 원문에서
#     추출한 authoritative 일자 → verbatim 강제. 그 외 소스(WL·FR·ECA·HC)의 발행일은 수집일
#     placeholder 이고 Routine 이 WebSearch 로 실제 문서일자로 enrich 하도록 **설계된 동작**이라
#     검사 제외(verbatim 강제 시 정상 enrich 를 오판 = false positive, 06-22 5건 확인).
#   · 그 밖의 셀(발행 부서/일자·주제·발행기관·의견기한 등 derived/placeholder)은 LLM 이 enrich·
#     재구성하도록 설계 → 검사 제외(WL 발행부서·FR/ECA 주제 재작성이 FP 였던 클래스).
#   · **카드 영역 한정**: 전역 substring 은 타 카드·M2/M3 메타의 동일 값(예 M3 'CONSUMED
#     2026-06-17 handoff')에 가려질 수 있어, 각 카드의 발행 영역(인접 document_id anchor 경계)
#     안에서만 대조한다(admin 처분일 오류가 메타 날짜에 가려지던 것 차단).
# ─────────────────────────────────────────────────────────────────────────────
# W2 메타표 셀: <tr><td>**라벨**</td><td>값</td></tr> (card_scaffold._table 산출형).
_SCAFFOLD_CELL_RE = re.compile(
    r"<tr>\s*<td>\s*\*\*(?P<label>.*?)\*\*\s*</td>\s*<td>(?P<value>.*?)</td>\s*</tr>", re.S)
# 대조 제외 값: 의도적 빈칸 placeholder(생성 금지 신호) + 무신호 기호. 이런 값은 발행본에
# 그대로 없을 수도 있고(LLM 이 동일 placeholder 를 다른 칸에 쓰기도 함) 신호가 없어 과알림만 낸다.
# [어휘 분리 2026-07-20] "미확인"=신형(card_scaffold.VALUE_UNKNOWN) 추가. "원문 미기재"는
# 과거 발행분(신형 배포 전 발행본) 호환을 위해 남겨둔다 — 신형 코드는 더 이상 이 값을 찍지 않는다.
_SCAFFOLD_CELL_SKIP_VALUES = frozenset({"", "—", "-", "원문 미기재", "미확인", "N/A"})
# authoritative identity 셀 라벨(정규화형) — 수집기가 원문에서 박은 정본, 전 소스 verbatim.
# 제조소/업체·업체/제조소 셀은 'FEI {n}' 를 동반하므로 FEI 전사오류가 여기서 잡힌다.
_IDENTITY_CELL_LABELS = frozenset({
    "제조소/업체", "업체/제조소", "제조소", "업체", "문서번호",
    "시설 · 유형", "Class", "회수 등급", "제품", "제품명",
})
# 날짜형 셀 값(단일 날짜만 — 범위·식별자는 identity 로 본다): YYYY-MM-DD · YYYY/MM/DD · MM/DD/YYYY.
_DATE_VALUE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{4}$")
# 날짜 셀을 verbatim 강제할 authoritative 소스 prefix(수집기가 원문 일자 추출). 그 외는 enrich 대상.
_DATE_AUTHORITATIVE_PREFIXES = ("fda483-", "admin-")


def _normalize_cell_text(text: str) -> str:
    """셀/발행 평문 대조용 정규화 — 인라인 코드 백틱·볼드 제거 + 공백 1칸 정규화.

    scaffold 의 문서번호 셀은 `` `값` ``(inline code)인데 Notion 발행 평문은 코드 서식이
    벗겨져 백틱이 없다. 볼드(`**`)·여러 공백/줄바꿈도 정규화해 **서식 차이로 인한 오탐**을
    막는다(FEI 숫자·날짜·고정 문구의 실질 내용만 비교 — 마크다운/평문 양쪽 입력 공용).
    """
    t = (text or "").replace("`", "").replace("**", "")
    return re.sub(r"\s+", " ", t).strip()


def _scaffold_cell_enforced(label: str, norm_value: str, doc_id: str) -> bool:
    """이 셀을 verbatim 강제할지 판정(스코프 규칙). identity 라벨이면 전 소스 강제,
    날짜형 값이면 fda483/admin 카드에서만 강제, 그 외는 비강제(enrich/derived 셀)."""
    if label in _IDENTITY_CELL_LABELS:
        return True
    if _DATE_VALUE_RE.match(norm_value):
        return doc_id.startswith(_DATE_AUTHORITATIVE_PREFIXES)
    return False


def lint_scaffold_fixed_cells(rows: list[dict[str, Any]],
                              published_text: str) -> list[LintFinding]:
    """scaffold W2 메타표의 authoritative 고정 셀이 발행본에 글자그대로 보존됐는지 검사(PL18).

    `card_scaffold` 의 표 셀 `<tr><td>**라벨**</td><td>값</td></tr>` 중 **강제 대상 셀**
    (`_scaffold_cell_enforced`: identity 라벨 전 소스 + fda483/admin 날짜)을 추출해, **렌더된
    카드**(그 row 의 `document_id` 가 발행 평문에 존재)에 한해 값(서식 정규화 후)이 **그 카드의
    발행 영역**(인접 anchor 경계)에 substring 으로 존재하는지 대조한다. 없으면 LLM 이 고정 값을
    재생성·추론·삭제·단정보강한 것(scaffold 계약 위반) → **FAIL**. 카드당 1 finding(과알림 0).

    비강제 셀(WL·FR·ECA 발행일=수집일 placeholder→enrich, 발행부서/주제 등 derived)은 검사
    제외 — 정상 enrich 를 오판하지 않는다(06-22 실데이터 FP 0/TP 8 정본). 슬롯(`{{...}}`)·의도적
    빈칸('원문 미기재')·미렌더 카드(Tier1 Skipped/보류)도 제외(과알림 0). 요일 PL14·footer
    무결성과 동형 결정론 검사로, MCP 전용 Routine 이 못 돌리는 발행 후 방어선.
    """
    pub = published_text or ""
    # 렌더된 카드와 그 발행 영역(인접 anchor 경계) — 전역 substring 의 타 카드·메타 가림 방지.
    rendered: list[tuple[int, str, str]] = []  # (anchor_pos, doc_id, scaffold)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        scaffold = row.get("card_scaffold")
        if not isinstance(scaffold, str) or not scaffold.strip():
            continue
        doc_id = (row.get("document_id") or "").strip()
        if not doc_id:
            continue
        pos = pub.find(doc_id)
        if pos < 0:
            continue  # 미렌더 카드(Tier1 Skipped/보류) — 검사 제외(과알림 0)
        rendered.append((pos, doc_id, scaffold))
    rendered.sort(key=lambda t: t[0])
    anchors = [pos for pos, _, _ in rendered]
    findings: list[LintFinding] = []
    for idx, (pos, doc_id, scaffold) in enumerate(rendered):
        # 영역 = [직전 anchor, 다음 anchor] — 카드의 W2 표(발행일은 anchor 앞, 나머지는 뒤)를
        # 온전히 포함하되 멀리 있는 메타(M2/M3)·비인접 카드는 배제한다. 인접 카드 일부는 들어올
        # 수 있으나 FEI·업체명은 카드마다 고유라 가림 위험이 없다(공유 값은 날짜뿐).
        start = anchors[idx - 1] if idx > 0 else 0
        end = anchors[idx + 1] if idx + 1 < len(anchors) else len(pub)
        region = _normalize_cell_text(pub[start:end])
        missing: list[str] = []
        for m in _SCAFFOLD_CELL_RE.finditer(scaffold):
            value = m.group("value")
            if "{{" in value:
                continue  # 슬롯 셀(LLM 채움) — 방어적 제외
            norm_value = _normalize_cell_text(value)
            if norm_value in _SCAFFOLD_CELL_SKIP_VALUES:
                continue
            label = _normalize_cell_text(m.group("label"))
            if not _scaffold_cell_enforced(label, norm_value, doc_id):
                continue  # enrich/derived 셀(비강제) — 검사 제외
            if norm_value not in region:
                missing.append(f"{label}={value.strip()}")
        if missing:
            findings.append(LintFinding(
                SEV_FAIL, "PL18-SCAFFOLD-CELL", "",
                f"카드[{doc_id}] scaffold 고정 셀이 발행본과 불일치 — {'; '.join(missing)} "
                "(값을 재생성·추론·삭제·단정보강한 것으로 보임. scaffold 의 authoritative 셀은 "
                "글자그대로 전사해야 함 — 06-22 FDA 483 FEI·시설유형/Lancora Class/admin 처분일 "
                "사고 클래스)."))
    return findings


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _is_mfds_host(url: str) -> bool:
    host = _host(url)
    return any(host == s or host.endswith("." + s) or host == "www." + s
              for s in _MFDS_HOST_SUFFIXES)


def lint_link_provenance(rows: list[dict[str, Any]],
                         published_markdown: str,
                         *,
                         policy: str = POLICY_MFDS_ONLY,
                         allowed_fetched: Iterable[str] = (),
                         verifier: "Any | None" = None) -> list[LintFinding]:
    """발행 markdown 의 모든 링크가 handoff 근거를 갖는지 검사(Publish Lint 17).

    근거 집합 = handoff rows 가 정당화하는 URL(`collect_allowed_urls`).
    판정(중복 URL 은 1회만 보고):
    - MFDS/nedrug 링크인데 근거 없음 → **FAIL**(MFDS 는 Core 검색 슬롯이 없어 Intake 전용 →
      날조/누출). 특히 `mfds.go.kr/brd/*/view.do?seq=` 는 W24 사고 시그니처라 메시지에 명시.
      MFDS 특례는 `allowed_fetched`·verify 로도 구제되지 않는다(검색 카드 자체가 금지).
    - 그 외 도메인 링크인데 근거 없음:
        · `allowed_fetched`(이번 세션에 실제 fetch·확인한 URL)에 있으면 통과(W2).
        · `policy=ALL_DOMAINS` 면 verifier(있으면 live verify) 통과 시 통과, 아니면 **FAIL**.
        · `policy=MFDS_ONLY`(기본) 면 **WARN**(종전 동작 보존 — 무회귀).

    `policy`/`allowed_fetched`/`verifier` 의 기본값은 종전 시그니처 동작과 동일하다
    (MFDS 한정 FAIL·그 외 WARN). 발행 게이트는 `run_publish_gate` 로 ALL_DOMAINS 옵트인.
    """
    allowed = collect_allowed_urls(rows)
    urls = extract_markdown_links(published_markdown or "")
    return lint_urls(urls, allowed, policy=policy,
                     allowed_fetched=allowed_fetched, verifier=verifier)


def lint_urls(urls: Iterable[str],
              allowed: "set[str]",
              *,
              policy: str = POLICY_MFDS_ONLY,
              allowed_fetched: Iterable[str] = (),
              verifier: "Any | None" = None) -> list[LintFinding]:
    """근거 대조 코어 — 링크 추출 방식과 무관(markdown 경로/Notion 블록 URL 경로 공용).

    `allowed` = `collect_allowed_urls(rows)` 가 만든 근거 키 집합(정규화됨).
    `urls` = 발행물에서 추출한 링크(원형). 판정 규칙은 `lint_link_provenance` 와 동일.
    """
    allowed_fetched_keys = {normalize_url(u) for u in allowed_fetched if u}
    findings: list[LintFinding] = []
    reported: set[str] = set()
    for url in urls:
        key = normalize_url(url)
        if not key or key in allowed or key in reported:
            continue
        reported.add(key)
        if _is_mfds_host(url):
            # MFDS 특례: 검색 슬롯이 없어 fetch 화이트리스트·verify 로도 구제 불가.
            if _MFDS_BRD_VIEW_RE.search(url):
                msg = ("MFDS 본사이트 게시판 직링크(보도자료/자료실)인데 handoff 근거 없음 — "
                       "W24 사고 시그니처. MFDS 는 검색 슬롯이 없어 Intake 근거 필수(날조 의심).")
            else:
                msg = ("MFDS/nedrug 링크인데 handoff 근거 없음 — MFDS 는 Intake 전용 경로라 "
                       "수집기 산출 URL 만 허용(LLM 생성·치환 의심).")
            findings.append(LintFinding(SEV_FAIL, "L17-MFDS-PROVENANCE", url, msg))
            continue
        if key in allowed_fetched_keys:
            # 비-MFDS · 이번 세션에 실제 fetch·확인한 URL → 근거 있음(검색 카드 정당 경로).
            continue
        if policy == POLICY_ALL_DOMAINS:
            if verifier is not None:
                try:
                    if verifier(url):
                        continue  # live verify 통과(200·오류셸 아님·기대어구 포함) → 통과
                except Exception:  # noqa: BLE001 — verify 실패는 미검증으로 취급(차단 측 안전)
                    pass
            findings.append(LintFinding(
                SEV_FAIL, "L17-UNGROUNDED", url,
                "handoff 근거도 세션 fetch 화이트리스트도 없는 외부 링크 — 패턴 유추(환각) "
                "의심. 검색 카드면 이번 run 에 실제 fetch 해 확인한 URL 만 허용."))
        else:
            findings.append(LintFinding(
                SEV_WARN, "L17-UNVERIFIED", url,
                "handoff 근거 없는 외부 링크 — 검색 카드 신규 URL 이면 실제 확인(fetch)했는지 "
                "검증, 아니면 패턴 유추(환각) 의심."))
    return findings


def has_failures(findings: Iterable[LintFinding]) -> bool:
    return any(f.severity == SEV_FAIL for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# 발행물 구조 lint(기계적 Publish Lint) — v16 프롬프트 [Publish Lint] 의 **기계 판정** 항목을
# 결정론 코드로 강등한다(자가 서술 → 실행). 순수 함수(네트워크 없음, markdown 만 입력).
#   PL1    잔존 슬롯 토큰 `{{` 0 (전 슬롯 치환됨)
#   PL3/16 금지 문법(admonition·<toggle>·[toc]·+++) 0 — 메타 toggle HARD(06-15 회귀) 포함
#   PL10   카드 제목(TITLE_ISSUE)에 "미상/미기재" 0 (D1)
#   PL14   날짜(헤더·제목·푸터)의 실제 KST 요일 == 표기 요일 (D7) — 괄호형·비괄호 푸터형 모두
# 의미 판정 항목(2 scaffold 불변·5 단일블록·7 Tier3 누락·8/9 조치배정·11~13·15·17 provenance)은
# LLM 자가 점검 또는 provenance 게이트가 담당한다 — 기계화 어려운 항목은 코드로 이관하지 않는다.
# ─────────────────────────────────────────────────────────────────────────────

# card_scaffold._FORBIDDEN_REPLACEMENTS 와 동일 어휘 + 메타 toggle 회귀 시그니처.
_FORBIDDEN_LITERALS = (
    "[!NOTE]", "[!WARNING]", "[!IMPORTANT]", "[!TIP]", "[!CAUTION]",
    "[TOC]", "[toc]", "+++", "<toggle>", "</toggle>",
)
_TOGGLE_OPEN_RE = re.compile(r"<toggle\b", re.I)   # <toggle> 또는 속성형 <toggle ...>
_RESIDUAL_TOKEN_RE = re.compile(r"\{\{")
# 카드 제목 H3 + bold TITLE_ISSUE:  ### [유형 · 기관] 대상 — **핵심이슈**
_CARD_TITLE_RE = re.compile(r"^#{3}\s+.*\*\*(?P<issue>.+?)\*\*", re.M)
_TITLE_FORBIDDEN_WORDS = ("미상", "미기재")
# 발행물 날짜+요일 표기 — 두 형태를 모두 잡는다:
#   ① 괄호형(헤더 메타라인·페이지 제목):  2026-06-15 (월)  ·  2026-06-15 (월요일)
#   ② 비괄호 푸터형(LLM 자유 작성 푸터):    발행일: 2026-06-17 화요일  (06-17 dry-run D-1)
# ②는 오탐 방지를 위해 '요일' 접미사를 필수로 한다(날짜 뒤 우연한 단일 요일 글자는 무시).
_DATE_WEEKDAY_RE = re.compile(
    r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"(?:\s*\(\s*(?P<wd_paren>[월화수목금토일])"      # ① (월) / (월요일)
    r"|\s+(?P<wd_bare>[월화수목금토일])요일)")         # ② … 화요일
_KO_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")  # date.weekday(): 월=0..일=6

# PL19 — Evidence 집계(커버리지 헤더 선언) ↔ 실제 카드 W1 배지 수 대조.
# 헤더 메타라인: `... · Evidence A {N}/B {N}/C {N} · 미확인 ...`(v16 L422). 카드 배지: W1 callout
# 의 `` `Evidence A` · `Source` · ... ``(항상 첫 배지라 뒤에 ` · ` 가 온다). 헤더는 'A' 뒤에 숫자,
# 범례(L439 "Evidence A: 1차…")는 'A' 뒤에 ':' 라 배지 패턴(`?\s*·)과 구분된다 → 오집계 없음.
# 06-22 사고: 헤더 A4/B10 인데 실제 배지 A3/B11(요일 PL14 와 동형 — LLM 자유 집계가 실제와 어긋남).
_EVIDENCE_TALLY_RE = re.compile(r"Evidence\s+A\s*(\d+)\s*/\s*B\s*(\d+)(?:\s*/\s*C\s*(\d+))?")
_EVIDENCE_BADGE_RE = re.compile(r"Evidence ([ABC])`?\s*·")


def lint_publish_structure(published_markdown: str) -> list[LintFinding]:
    """발행 markdown 의 기계적 구조 위반(PL1·PL3/16·PL10·PL14)을 결정론 판정.

    순수 함수 — 네트워크·handoff 없이 markdown 만으로 판정한다. 모든 위반은 FAIL.
    확인 불가(예: 헤더 날짜/요일 패턴 부재)는 finding 을 만들지 않는다(추측 금지).
    """
    md = published_markdown or ""
    findings: list[LintFinding] = []

    # PL1 — 잔존 슬롯 토큰
    if _RESIDUAL_TOKEN_RE.search(md):
        findings.append(LintFinding(
            SEV_FAIL, "PL1-RESIDUAL-TOKEN", "",
            "치환되지 않은 슬롯 토큰 `{{` 가 본문에 남아 있다 — 전 슬롯을 값으로 치환해야 발행."))

    # PL3/16 — 금지 문법(메타 toggle HARD 포함)
    for lit in _FORBIDDEN_LITERALS:
        if lit in md:
            findings.append(LintFinding(
                SEV_FAIL, "PL3-FORBIDDEN-MD", "",
                f"금지 문법 리터럴 `{lit}` 노출 — 메타는 <details>/<summary>, 목차는 "
                "<table_of_contents/> 로만(06-15 toggle 회귀 차단)."))
    if _TOGGLE_OPEN_RE.search(md) and "<toggle>" not in md:  # 속성형 <toggle ...>
        findings.append(LintFinding(
            SEV_FAIL, "PL3-FORBIDDEN-MD", "",
            "`<toggle ...>` 태그 노출 — <details>/<summary> 로 교정해야 발행."))

    # PL10 — 카드 제목(TITLE_ISSUE)에 미상/미기재
    for m in _CARD_TITLE_RE.finditer(md):
        issue = m.group("issue")
        for w in _TITLE_FORBIDDEN_WORDS:
            if w in issue:
                findings.append(LintFinding(
                    SEV_FAIL, "PL10-TITLE-UNKNOWN", "",
                    f"카드 제목 핵심이슈에 '{w}' — TITLE_ISSUE 는 위반유형/주제 명사구만"
                    f"(D1, 제목: …**{issue}**)."))
                break

    # PL14 — 발행물 날짜(헤더 메타라인·제목·푸터)의 실제 KST 요일 == 표기 요일.
    # 한 발행물에 날짜+요일이 여러 곳(헤더·푸터)이라 전부 검사하되 같은 (날짜,표기) 는 1회만 보고.
    seen_wd: set[tuple[str, str, str, str]] = set()
    for m in _DATE_WEEKDAY_RE.finditer(md):
        wd = m.group("wd_paren") or m.group("wd_bare")
        y, mo, dd = m.group("y"), m.group("m"), m.group("d")
        key = (y, mo, dd, wd)
        if key in seen_wd:
            continue
        seen_wd.add(key)
        try:
            d = _dt.date(int(y), int(mo), int(dd))
        except ValueError:
            continue  # 잘못된 날짜 자체는 본 항목 소관 아님
        actual = _KO_WEEKDAYS[d.weekday()]
        if actual != wd:
            findings.append(LintFinding(
                SEV_FAIL, "PL14-WEEKDAY", "",
                f"발행물 요일 불일치 — {y}-{mo}-{dd} 는 "
                f"'{actual}'요일인데 '{wd}'로 표기(D7)."))

    # PL19 — Evidence 집계 헤더(선언) ↔ 실제 카드 배지 수. 헤더 메타라인이 없으면 검사 생략
    # (추측 금지·과알림 0). C 미선언(A/B 만)이면 C=0 으로 본다.
    tally = _EVIDENCE_TALLY_RE.search(md)
    if tally:
        declared = {"A": int(tally.group(1)), "B": int(tally.group(2)),
                    "C": int(tally.group(3) or 0)}
        counted = {"A": 0, "B": 0, "C": 0}
        for bm in _EVIDENCE_BADGE_RE.finditer(md):
            counted[bm.group(1)] += 1
        mismatched = [k for k in ("A", "B", "C") if declared[k] != counted[k]]
        if mismatched:
            findings.append(LintFinding(
                SEV_FAIL, "PL19-EVIDENCE-TALLY", "",
                "Evidence 집계 헤더 ↔ 카드 배지 수 불일치 — "
                f"헤더 A{declared['A']}/B{declared['B']}/C{declared['C']} vs "
                f"배지 A{counted['A']}/B{counted['B']}/C{counted['C']} "
                f"(불일치: {', '.join(mismatched)}; LLM 집계 오류 의심)."))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# 수집 현황(커버리지) '수집' 숫자 대조 lint (W2) — 발행물의 수집 callout 숫자(총계+소스별)가
# handoff 근거(수집기 산출 source_counts)와 일치하는지 결정론 판정. 순수 함수(네트워크 없음).
# 정본 expected({total, items}) 는 collect_intake.build_coverage_collected(= W1 이 handoff 에
# 싣는 값과 동일 산식)가 만든다. 이 모듈은 source→label 매핑을 모르고 expected 만 받아 대조한다
# (collect_intake 비의존 — 순수성 유지). 파싱 불가(앵커 부재)면 finding 없음(추측 금지·과알림 0).
# 06-17 검증 동기: 발행=실제 카드수로 확인됐으나 수집/스킵은 LLM 집계라 무보증(요일 오산과 동형).
# 수집 컬럼은 W1 이 결정론 산출하고, 이 lint 가 발행 후 결정론으로 재대조한다(발행물 LLM 집계 방어).
# ─────────────────────────────────────────────────────────────────────────────
# "Intake row {N}건 ( ... )" — 발행 커버리지 callout 의 수집 세그먼트(첫 괄호까지만 — 그 뒤
# 병합·WebSearch 등은 수집 대상 아님).
_COVERAGE_ANCHOR_RE = re.compile(r"Intake\s+row\s+(?P<total>\d+)\s*건\s*\((?P<body>[^)]*)\)")
# 괄호 안 토큰: "{label} {count}[건]" — 라벨은 영문 시작·영숫자/공백/슬래시(FDA WL·PIC/S·FDA 483).
_COVERAGE_ITEM_RE = re.compile(r"(?P<label>[A-Za-z][A-Za-z0-9/ ]*?)\s+(?P<count>\d+)\s*건?\s*$")


def parse_collected_coverage(published_text: str) -> "dict[str, Any] | None":
    """발행물 평문에서 수집 세그먼트('Intake row N건 (라벨 n · ...)')를 파싱.

    반환 {"total": int, "items": {label: count}} 또는 None(앵커 부재 — 대조 불가).
    괄호 안을 ' · ' 로 분리해 각 토큰 끝의 숫자를 카운트로 본다(선택적 '건' 접미 허용).
    중복 라벨은 마지막 값. 토큰이 "label count" 형이 아니면 무시(잡음 내성).
    """
    m = _COVERAGE_ANCHOR_RE.search(published_text or "")
    if not m:
        return None
    items: dict[str, int] = {}
    for tok in m.group("body").split("·"):
        tm = _COVERAGE_ITEM_RE.match(tok.strip())
        if tm:
            items[tm.group("label").strip()] = int(tm.group("count"))
    return {"total": int(m.group("total")), "items": items}


def lint_coverage_counts(expected: "dict[str, Any] | None",
                         published_text: str) -> list[LintFinding]:
    """발행물 '수집' 숫자가 handoff 정본(expected)과 일치하는지 결정론 판정(W2).

    expected = `collect_intake.build_coverage_collected(source_counts)` 반환값
    ({"total", "items":[{"label","count"}...]}). 발행물에서 수집 세그먼트를 파싱해
    (1) 총계, (2) known 소스별 건수를 대조한다. 불일치는 **FAIL**(요일 PL14 와 동형 — MCP 전용
    Routine 이 인-루틴 게이트를 못 돌리므로 발행 후 탐지가 유일 결정론 방어선).
    파싱 불가(앵커 부재) 또는 expected 부재면 finding 없음(추측 금지·과알림 0).
    - 총계 불일치 → PL15-COVERAGE-TOTAL FAIL.
    - known 소스 건수 불일치(또는 0 아닌데 누락) → PL15-COVERAGE-SOURCE FAIL.
      (expected 0 건 소스를 발행물이 생략한 건 정상 — 보고 안 함.)
    - 발행물 수집 줄에만 있고 expected 에 없는 라벨 → PL15-COVERAGE-EXTRA WARN(저신뢰·라벨 오기
      가능성 — 알림 트리거 아님).
    """
    if not expected:
        return []
    parsed = parse_collected_coverage(published_text)
    if parsed is None:
        return []  # 수집 callout 부재 — 대조 불가, 추측 금지(과알림 0)
    findings: list[LintFinding] = []
    exp_total = int(expected.get("total", 0))
    if parsed["total"] != exp_total:
        findings.append(LintFinding(
            SEV_FAIL, "PL15-COVERAGE-TOTAL", "",
            f"수집 현황 '수집' 총계 불일치 — 발행물 Intake row {parsed['total']}건인데 "
            f"handoff 근거(수집기 산출)는 {exp_total}건(LLM 집계 오류 의심)."))
    exp_items = {it["label"]: int(it["count"]) for it in expected.get("items", [])}
    pub_items = parsed["items"]
    for label, exp_n in exp_items.items():
        pub_n = pub_items.get(label)
        if pub_n is None:
            if exp_n != 0:  # 0건 소스 생략은 정상(과알림 0)
                findings.append(LintFinding(
                    SEV_FAIL, "PL15-COVERAGE-SOURCE", "",
                    f"수집 현황 소스 '{label}' 누락 — handoff 근거 {exp_n}건인데 발행물 수집 "
                    f"줄에 없음(LLM 집계 누락 의심)."))
        elif pub_n != exp_n:
            findings.append(LintFinding(
                SEV_FAIL, "PL15-COVERAGE-SOURCE", "",
                f"수집 현황 소스 '{label}' 건수 불일치 — 발행물 {pub_n}건 / handoff 근거 "
                f"{exp_n}건(LLM 집계 오류 의심)."))
    for label in pub_items:
        if label not in exp_items:
            findings.append(LintFinding(
                SEV_WARN, "PL15-COVERAGE-EXTRA", "",
                f"수집 현황 소스 '{label}'({pub_items[label]}건)이 handoff 근거에 없음 — "
                f"수집기 미산출 라벨(라벨 오기·검색 소스 혼입 가능성)."))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# 선택적 네트워크 유틸 — 수집기 resolve&verify(Phase E2)·Phase C 전수검증 재사용용.
# 순수 lint 는 이걸 호출하지 않는다(테스트는 HTTP 스텁으로 검증).
# ─────────────────────────────────────────────────────────────────────────────
def looks_like_error_page(body: str) -> bool:
    """nedrug 클라이언트 렌더 오류 셸 판정(HTTP 200 이어도 무효 레코드).

    라이브 확인(2026-06-16): `CCBAO01/getItem?dispsApplySeq=<invalid>` 는 HTTP 200 +
    길이 ~2.6KB + 이 마커. 유효 seq 는 ~80KB + 마커 부재. **상태코드로는 구분 불가**.
    """
    if not body:
        return True
    if any(m in body for m in _NEDRUG_ERROR_MARKERS):
        return True
    return len(body) < 10000 and any(m in body for m in _SHORT_ERROR_MARKERS)


def verify_url_live(url: str, expect_terms: Iterable[str] = (),
                    timeout: int = 20, min_len: int = 5000) -> dict[str, Any]:
    """URL 을 실제 GET 해 (1) 200, (2) 오류 셸 아님, (3) 본문에 expect_terms 포함 여부 판정.

    반환 dict: {ok, status, length, is_error_page, missing_terms, error}. 네트워크 실패는
    예외 대신 ok=False+error 로 돌려준다(수집기 graceful degrade 용). requests lazy import.
    """
    try:
        import requests  # lazy — 순수 lint 경로는 의존하지 않음
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "status": 0, "length": 0, "is_error_page": True,
                "missing_terms": list(expect_terms), "error": f"requests 미설치: {exc}"}
    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0 GRM-URL-Audit/1.0"})
    except Exception as exc:
        return {"ok": False, "status": 0, "length": 0, "is_error_page": True,
                "missing_terms": list(expect_terms), "error": str(exc)[:200]}
    body = r.text or ""
    is_err = looks_like_error_page(body)
    missing = [t for t in expect_terms if t and t not in body]
    ok = (r.status_code == 200) and (not is_err) and (len(body) >= min_len) and not missing
    return {"ok": ok, "status": r.status_code, "length": len(body),
            "is_error_page": is_err, "missing_terms": missing, "error": ""}


def live_verifier(expect_terms: Iterable[str] = (), **kwargs: Any):
    """ALL_DOMAINS 정책에서 미근거 비-MFDS 링크를 실시간 검증하는 verifier 콜백 팩토리.

    `lint_urls(..., verifier=live_verifier())` 처럼 주입한다. 네트워크가 필요하므로
    순수 lint(예방 게이트)에는 쓰지 않고, 탐지(detective)·전수검증 경로에서만 쓴다.
    """
    terms = tuple(expect_terms)

    def _verify(url: str) -> bool:
        return bool(verify_url_live(url, expect_terms=terms, **kwargs).get("ok"))

    return _verify


# ─────────────────────────────────────────────────────────────────────────────
# 발행 직전 blocking 게이트(W1) — "검사기 존재" 를 "매 발행마다 실행·FAIL 시 차단" 으로.
# 순수 함수(네트워크 없음, verifier 주입 시에만 네트워크). Routine·CI·독립 세션 공용.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GateResult:
    ok: bool                         # FAIL 0 이면 True(발행 허용), FAIL≥1 이면 False(차단)
    findings: list[LintFinding]
    report: str                      # 사람이 읽는 한 줄 요약 + 항목

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEV_FAIL)

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEV_WARN)


def format_report(findings: list[LintFinding]) -> str:
    """게이트 결과를 사람이 읽는 텍스트로(FAIL 우선). 채팅/CI 로그/Issue 본문 공용."""
    fails = [f for f in findings if f.severity == SEV_FAIL]
    warns = [f for f in findings if f.severity == SEV_WARN]
    if not findings:
        return "[PASS] GRM 발행 게이트(출처 근거+구조) — 위반 0 (발행 허용)"
    head = (f"[{'FAIL' if fails else 'PASS(경고)'}] GRM 발행 게이트 — "
            f"FAIL {len(fails)} · WARN {len(warns)}")
    lines = [head]
    for f in fails:
        lines.append(f"  ✖ {f}")
    for f in warns:
        lines.append(f"  ⚠ {f}")
    if fails:
        lines.append("→ 발행 중단: 위 FAIL 링크의 근거를 확보(Intake 카드는 scaffold 링크 복원 / "
                     "검색 날조면 출처 줄 제거·카드 보류)한 뒤 다시 게이트를 통과시켜야 발행한다.")
    return "\n".join(lines)


def run_publish_gate(rows: list[dict[str, Any]],
                     published_markdown: str,
                     *,
                     policy: str = POLICY_ALL_DOMAINS,
                     allowed_fetched: Iterable[str] = (),
                     verifier: "Any | None" = None,
                     require_scaffold_footers: bool = True,
                     require_scaffold_cells: bool = True,
                     include_structure: bool = False) -> GateResult:
    """발행 직전(또는 발행 후 탐지) provenance(+선택 구조) 게이트 1회 실행.

    기본 정책 = ALL_DOMAINS(전 기관 일반화, W2). `allowed_fetched` = 이번 세션에 실제
    fetch·확인한 검색 카드 URL(있으면 그 비-MFDS 링크는 근거로 인정). `verifier` 주입 시
    미근거 비-MFDS 링크를 live verify(탐지 경로). `require_scaffold_cells=True`(기본) 면
    scaffold W2 고정 셀 전사 무결성(PL18)도 검사한다(footer URL 과 동일 결함 클래스의 셀-텍스트
    일반화 — 06-22 FDA 483/Lancora 사고). `include_structure=True` 면 기계적 Publish
    Lint(PL1·PL3/16·PL10·PL14·PL19)도 함께 검사(기본 off — 기존 호출처 무회귀).
    반환 `GateResult.ok=False`(FAIL≥1) 면 **발행 차단**.
    """
    published_urls = extract_markdown_links(published_markdown or "")
    findings: list[LintFinding] = []
    if require_scaffold_footers:
        findings.extend(lint_scaffold_footer_integrity(
            rows, published_urls, published_text=published_markdown or ""))
    if require_scaffold_cells:
        findings.extend(lint_scaffold_fixed_cells(rows, published_markdown or ""))
    findings.extend(lint_urls(published_urls, collect_allowed_urls(rows), policy=policy,
                              allowed_fetched=allowed_fetched, verifier=verifier))
    if include_structure:
        findings = findings + lint_publish_structure(published_markdown)
    return GateResult(ok=not has_failures(findings), findings=findings,
                      report=format_report(findings))


# ─────────────────────────────────────────────────────────────────────────────
# CLI — 결정론 발행 게이트를 셸/CI/Routine(코드 실행 가능 환경)에서 강제 실행.
#   python -m brief_lint --handoff handoff.json --published brief.md [--policy ...] [--verify]
# handoff.json = handoff v2 payload({"rows":[...]}) 또는 Notion handoff 페이지 export.
# published    = 발행 markdown/텍스트(예: Notion 페이지 export). FAIL 시 exit 1(발행 차단).
# ─────────────────────────────────────────────────────────────────────────────
def extract_handoff_rows(obj: Any) -> list[dict[str, Any]]:
    """다양한 형태의 handoff JSON 에서 `rows[]` 를 끌어낸다(payload·page·list 공용)."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        rows = obj.get("rows")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        # Notion 페이지 형태로 감싸졌으면 본문 code block JSON 을 재귀 탐색.
        for key in ("payload", "handoff", "body", "content"):
            inner = obj.get(key)
            if isinstance(inner, (dict, list)):
                got = extract_handoff_rows(inner)
                if got:
                    return got
    return []


def _load_handoff_rows(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # 본문에 ```json ... ``` code fence 로 감싼 경우 첫 JSON 객체만 추출.
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        obj = json.loads(m.group(0))
    return extract_handoff_rows(obj)


def _read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def main(argv: "list[str] | None" = None) -> int:
    import argparse
    # Windows 콘솔(cp949) 에서 한글·em-dash 출력 크래시 방지(probe_*.py 동형).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(
        prog="brief_lint",
        description="GRM 발행 직전 출처 링크 근거(provenance) 게이트 — FAIL 시 exit 1(발행 차단).")
    p.add_argument("--handoff", required=True,
                   help="handoff v2 JSON 경로(rows[] 포함 — payload·page·list 허용).")
    p.add_argument("--published", required=True,
                   help="발행 markdown/텍스트 경로(Notion 페이지 export 등).")
    p.add_argument("--allowed-fetched", default=None,
                   help="(선택) 이번 세션에 실제 fetch·확인한 URL 목록 파일(줄당 1 URL).")
    p.add_argument("--policy", choices=[POLICY_MFDS_ONLY, POLICY_ALL_DOMAINS],
                   default=POLICY_ALL_DOMAINS,
                   help="기본 all_domains(전 기관 일반화). mfds_only=종전 동작.")
    p.add_argument("--verify", action="store_true",
                   help="미근거 비-MFDS 링크를 live verify(네트워크). 탐지 경로용.")
    p.add_argument("--structure", action="store_true",
                   help="기계적 Publish Lint(PL1 잔존토큰·PL3/16 금지문법·PL10 제목 미상·"
                        "PL14 요일=날짜)도 함께 검사(FAIL 시 발행 차단).")
    args = p.parse_args(argv)

    try:
        rows = _load_handoff_rows(args.handoff)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] handoff 로드 실패: {exc}", file=sys.stderr)
        return 2
    try:
        with open(args.published, "r", encoding="utf-8") as fh:
            published = fh.read()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 발행물 로드 실패: {exc}", file=sys.stderr)
        return 2
    allowed_fetched = _read_lines(args.allowed_fetched) if args.allowed_fetched else ()
    verifier = live_verifier() if args.verify else None

    result = run_publish_gate(rows, published, policy=args.policy,
                              allowed_fetched=allowed_fetched, verifier=verifier,
                              include_structure=args.structure)
    print(result.report)
    print(f"(handoff rows={len(rows)} · 근거 URL={len(collect_allowed_urls(rows))})",
          file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
