#!/usr/bin/env python3
"""FIND-1 M1d pure raw_signal -> grm-finding/v1 extractors.

This layer is intentionally offline and side-effect free.  It does not fetch
documents, write SQLite, or call Notion/Supabase; callers pass an already
captured grm-raw-signal/v1 record and receive deterministic findings.
"""

from __future__ import annotations

import json
import re
from typing import Any

import grm_findings as gf


MFDS_GMP_LIST_URL = "https://nedrug.mfds.go.kr/pbp/CCBBD03/getList?page=1&limit=100"
FDA_483_LIST_URL = (
    "https://www.fda.gov/about-fda/office-inspections-and-investigations/"
    "oii-foia-electronic-reading-room"
)
# [findings DB 구멍 수리 2026-07-12 · evidence_url 정정 2026-07-12] MFDS 행정처분/회수
# 의 "원본 확인" 링크는 nedrug 사용자 페이지여야 한다. raw_signal.official_url 은
# data.go.kr 오픈API 데이터셋 안내 페이지(개별 사건 열람 불가)이고 source_url 은
# serviceKey 포함 API 엔드포인트라 둘 다 부적합 — _evidence_url 을 쓰면 official_url 이
# 최우선 반환돼 데이터셋 페이지가 나온다(초기 배선 버그). card_scaffold._official_admin/
# _official_recall_quality 의 canonical 링크와 동일하게 직접 구성한다:
#   admin  = 행정처분 개별 레코드 L1 (CCBAO01/getItem?dispsApplySeq=<ADM_DISPS_SEQ>)
#            — 라이브 검증됨(card_scaffold 주석 §5.2). seq 없으면 목록 L2.
#   recall = 회수·폐기 공표 목록 L2 (CCBAI01) — data.go.kr payload 에 건별 nedrug seq 가
#            없어 건별 L1 불가(브리프도 동일하게 목록 인덱스 사용).
MFDS_ADMIN_ACTION_INDEX_URL = "https://nedrug.mfds.go.kr/pbp/CCBAO01"
MFDS_RECALL_INDEX_URL = "https://nedrug.mfds.go.kr/pbp/CCBAI01"

_CFR_RE = re.compile(r"\b21\s*CFR\s*(?:Part\s*)?\d+(?:\.\d+)?(?:\([a-z0-9]+\))*", re.I)

# [FIND-1 M11] 법조항 추출 강화 -- 21 CFR 만 잡던 옛 `_extract_cfr_refs`(단일 패턴, `_CFR_RE`)를
# 21 U.S.C./FD&C Act section 까지 잡는 `_extract_us_legal_refs` 로 승격한다. `_CFR_RE`(21 CFR 단일
# 패턴)는 그대로 재사용 -- 매칭 로직을 다시 쓰지 않고 이 상위 함수 안 CFR pass 에 그대로 포함시켰다.
# 483(`_from_fda_483_observations`)·WL(`_from_warning_letter`) 양쪽 호출부 모두 이 함수로 교체.
#
# "21 CFR parts 210 and 211" 처럼 한 접두사 뒤에 번호 목록이 이어지는 형태는 각 항으로 전개한다.
# 전개 시도 중 흔한 함정: 목록 계속(continuation) 패턴이 탐욕적으로 다음 절의 무관한 숫자까지
# 삼킬 수 있다(예 "21 U.S.C. § 351(a)(2)(B) and 21 CFR parts 210 and 211" 에서 " and " 뒤의
# "21"이 "21 CFR" 의 시작인데 U.S.C. 목록의 다음 항으로 오인될 수 있음) -- 그래서 U.S.C./section
# 목록의 계속 절은 바로 뒤에 "CFR" 이 오면 소비하지 않도록 부정 전방탐색을 둔다.
_LIST_SEP = r"(?:,\s*and\s+|,\s*|and\s+)"  # "and"/","/", and " -- longest-alt-first
_CFR_PARTS_LIST_RE = re.compile(
    r"21\s*CFR\s*parts\s+(\d+(?:\s*" + _LIST_SEP + r"\d+)*)", re.I
)
_USC_PREFIX_RE = re.compile(r"21\s*U\.S\.C\.\s*§?\s*", re.I)
# NOTE: `\d++` (possessive) not `\d+` -- a plain greedy `\d+` can backtrack to a
# shorter digit run (e.g. "21" -> "2") purely to *satisfy* the trailing
# `(?!\s*CFR)` negative lookahead, which defeats the guard it's there for
# (observed live on "... and 21 CFR parts 210 ..." matching a phantom "2").
# Possessive quantifiers (Python 3.11+) forbid that backtrack.
_USC_RE = re.compile(
    r"21\s*U\.S\.C\.\s*§?\s*\d++(?:\([a-zA-Z0-9]+\))*+(?!\s*CFR)"
    r"(?:\s*" + _LIST_SEP + r"§?\s*\d++(?:\([a-zA-Z0-9]+\))*+(?!\s*CFR))*",
    re.I,
)
_SECTION_RE = re.compile(
    r"sections?\s+\d++(?:\([a-zA-Z0-9]+\))*+(?!\s*CFR)"
    r"(?:\s*" + _LIST_SEP + r"\d++(?:\([a-zA-Z0-9]+\))*+(?!\s*CFR))*",
    re.I,
)


def _extract_us_legal_refs(text: str) -> list[str]:
    """Extract+normalize+dedupe 21 CFR / 21 U.S.C. / FD&C Act section references.

    List forms ("21 CFR parts 210 and 211", "sections 301(a), 301(d)") are
    expanded into one ref per item where feasible; a form that resists clean
    expansion is kept as the original matched string (better a coarse ref than
    a dropped one).
    """
    haystack = text or ""
    seen: set[str] = set()
    refs: list[str] = []
    consumed: list[tuple[int, int]] = []

    def _add(ref: str) -> None:
        key = ref.lower()
        if key not in seen:
            seen.add(key)
            refs.append(ref)

    def _overlaps(span: tuple[int, int]) -> bool:
        start, end = span
        return any(not (end <= s or start >= e) for s, e in consumed)

    for match in _CFR_PARTS_LIST_RE.finditer(haystack):
        consumed.append(match.span())
        for number in re.findall(r"\d+", match.group(1)):
            _add(f"21 CFR {number}")

    for match in _CFR_RE.finditer(haystack):
        if _overlaps(match.span()):
            continue
        ref = re.sub(r"\s+", " ", match.group(0)).strip()
        ref = re.sub(r"(?i)\bcfr\b", "CFR", ref)
        ref = re.sub(r"(?i)\bpart\b", "Part", ref)
        _add(ref)

    for match in _USC_RE.finditer(haystack):
        consumed.append(match.span())
        prefix = _USC_PREFIX_RE.match(match.group(0))
        body = match.group(0)[prefix.end():] if prefix else match.group(0)
        for number in re.findall(r"\d+(?:\([a-zA-Z0-9]+\))*", body):
            _add(f"21 U.S.C. § {number}")

    for match in _SECTION_RE.finditer(haystack):
        if _overlaps(match.span()):
            continue
        consumed.append(match.span())
        for number in re.findall(r"\d+(?:\([a-zA-Z0-9]+\))*", match.group(0)):
            _add(f"section {number}")

    return refs


def findings_from_raw_signal(raw_signal: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract deterministic v0 findings from one grm-raw-signal/v1 record."""
    findings, _report = findings_from_raw_signal_with_report(raw_signal)
    return findings


def findings_from_raw_signal_with_report(
    raw_signal: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract findings plus a diagnostic report distinguishing "nothing to
    extract" from "extracted but dropped as invalid/duplicate".

    Report keys: extracted (attempts before dedupe/validation), kept,
    dropped_invalid, dropped_duplicate_text, invalid_errors (deduped, sorted
    validate_finding error strings).
    """
    if gf.validate_raw_signal(raw_signal):
        return [], _empty_extraction_report()

    raw = _json_object(raw_signal.get("raw_json"))
    row = _json_object(raw_signal.get("row_json"))
    if not raw:
        return [], _empty_extraction_report()

    signal = _raw_signal_with_firm_fallback(raw_signal, raw, row)
    findings: list[dict[str, Any]] = []
    findings.extend(_from_fda_483_observations(signal, raw, row))
    findings.extend(_from_mfds_gmp(signal, raw, row))
    findings.extend(_from_mfds_admin_action(signal, raw, row))
    findings.extend(_from_mfds_recall(signal, raw, row))
    findings.extend(_from_warning_letter(signal, raw, row))
    findings.extend(_from_whopir(signal, raw, row))
    return _dedupe_valid_findings_with_report(findings)


def _empty_extraction_report() -> dict[str, Any]:
    return {
        "extracted": 0,
        "kept": 0,
        "dropped_invalid": 0,
        "dropped_duplicate_text": 0,
        "invalid_errors": [],
    }


def _from_fda_483_observations(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    observations = _dicts(raw.get("fda_483_observations"))
    if not observations:
        return []

    # [FIND-1 M10a] 저장된 raw 재추출 방어(백필 경로) — Observation 이 이미 수집기 층에서
    # 스크럽됐어도(또는 스크럽 이전 raw 가 그대로 저장돼 있어도) 페이지 넘김 헤더 라벨-값
    # 인터리브를 여기서도 한 번 더 제거한다. finding_text 가 바뀌면 finding_id(해시)도
    # 바뀐다 -- 오염 텍스트를 깨끗한 텍스트로 교체하는 의도된 동작이다.
    header_hints = {
        "establishment_type": _compact(raw.get("establishment_type")),
        "fei_number": _compact(raw.get("fei_number")),
        "firm_name": _compact(raw_signal.get("firm_name")) or _compact(raw.get("firm")),
    }

    evidence_url = _evidence_url(raw_signal, raw, "pdf_url", "url", fallback=FDA_483_LIST_URL)
    out: list[dict[str, Any]] = []
    for index, observation in enumerate(observations, start=1):
        deficiency = _compact(
            gf.strip_fda483_page_header(_compact(observation.get("deficiency")), **header_hints)
        )
        if not deficiency:
            continue
        detail = _compact(
            gf.strip_fda483_page_header(_compact(observation.get("detail")), **header_hints)
        )
        refs = _extract_us_legal_refs(" ".join(part for part in (deficiency, detail) if part))
        out.append(gf.finding_from_raw_signal(
            raw_signal,
            finding_text=deficiency,
            ordinal=_positive_int(observation.get("number"), default=index),
            evidence_level="A",
            evidence_url=evidence_url,
            finding_language=_language(row, "EN"),
            cfr_refs=refs,
            confidence=0.95,
            review_status="accepted",
        ))
    return out


def _from_mfds_gmp(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    table_rows = _dicts(raw.get("gmp_deficiencies"))
    if table_rows:
        return _from_mfds_gmp_table(raw_signal, raw, row, table_rows)

    excerpt = _compact(raw.get("attachment_deficiency_excerpt"))
    if not excerpt or _compact(raw.get("attachment_deficiency_assessment")).lower() == "none":
        return []

    return [gf.finding_from_raw_signal(
        raw_signal,
        finding_text=excerpt,
        ordinal=1,
        evidence_level="B",
        # [Fix A 2026-07-12] download_url = 실사 결과 PDF(raw_json, card_scaffold.
        # _official_gmp_inspection 과 동일 소스) — source_url/url 보다 먼저 시도한다.
        evidence_url=_evidence_url(raw_signal, raw, "download_url", "source_url", "url", fallback=MFDS_GMP_LIST_URL),
        finding_language=_language(row, "KO"),
        mfds_refs=_extract_mfds_refs(excerpt),
        confidence=0.72,
        review_status="needs_review",
    )]


def _from_mfds_gmp_table(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
    table_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # [Fix A 2026-07-12 — 사고2 직접 수리] raw_json.download_url = 실사 결과 PDF
    # (card_scaffold._official_gmp_inspection 과 동일 소스). 실측: raw_json 에는 이
    # download_url 만 있고 source_url/url 은 null — 그런데도 옛 _evidence_url 은
    # raw_signal.official_url(목록 CCBBD03)을 최우선 반환해 사고가 났다. 위 _evidence_url
    # 재정렬로 raw_keys 가 official_url/source_url 보다 먼저 시도되므로 download_url 이
    # 최우선 채택된다.
    evidence_url = _evidence_url(raw_signal, raw, "download_url", "source_url", "url", fallback=MFDS_GMP_LIST_URL)
    out: list[dict[str, Any]] = []
    for index, item in enumerate(table_rows, start=1):
        text = _gmp_table_text(item)
        if not text:
            continue
        legal_basis = _compact(item.get("legal_basis") or item.get("basis") or item.get("law_ref"))
        refs = [legal_basis] if legal_basis else _extract_mfds_refs(text)
        out.append(gf.finding_from_raw_signal(
            raw_signal,
            finding_text=text,
            ordinal=index,
            category_code=_classify_gmp_summary(_gmp_summary_text(item)),
            evidence_level="A",
            evidence_url=evidence_url,
            finding_language=_language(row, "KO"),
            mfds_refs=refs,
            confidence=0.90,
            review_status="accepted",
        ))
    return out


def _from_mfds_admin_action(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    # [findings DB 구멍 수리 2026-07-12] MFDS 행정처분(admin-action) raw_signal 은
    # 지금까지 findings 로 변환하는 추출기가 없어 검색 DB(`/findings/`)가 미국 FDA 483 에
    # 99.6% 편중되는 원인 중 하나였다(raw_signals 에는 적재되지만 findings 로 안 나감).
    # 트리거는 row/source_kind 문자열이 아니라 raw 필드 존재로 판별한다(collect_intake.py
    # 의 IntakeItem.type_or_class="admin-action" 이 row_json 에 "type_or_class" 로 실리지만,
    # raw 필드 자체(ADM_DISPS_SEQ + EXPOSE_CONT/admin_body_full)가 이 소스의 더 안정적인
    # 지문이다 -- collect_mfds_admin_action.py _document_id/_body 와 동일 필드).
    adm_seq = _compact(raw.get("ADM_DISPS_SEQ"))
    expose_cont = _compact(raw.get("EXPOSE_CONT"))
    admin_body_full_raw = str(raw.get("admin_body_full") or "")
    admin_body_full = _compact(admin_body_full_raw)
    if not adm_seq or not (expose_cont or admin_body_full):
        return []

    # 첫 줄만 취할 때는 개행이 살아있는 원본에서 잘라야 한다 -- _compact() 는 개행도
    # 공백 하나로 접어버려 "첫 줄" 경계 자체가 사라진다(admin_body_full 은 위반상세/
    # 처분명/적용법령이 개행으로 구분된 다단락 텍스트, collect_mfds_admin_action._body).
    finding_text = expose_cont or _compact(admin_body_full_raw.split("\n", 1)[0])
    if not finding_text:
        return []

    bef_apply_law = _compact(raw.get("BEF_APPLY_LAW"))
    refs = _extract_mfds_refs(bef_apply_law) if bef_apply_law else _extract_mfds_refs(expose_cont)

    # 원본 확인 링크 = 행정처분 개별 레코드 L1. _evidence_url 은 official_url(data.go.kr
    # 데이터셋)을 최우선 반환하므로 쓰지 않고 직접 구성한다(card_scaffold._official_admin 동형).
    evidence_url = (
        f"https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq={adm_seq}"
        if adm_seq else MFDS_ADMIN_ACTION_INDEX_URL
    )

    return [gf.finding_from_raw_signal(
        raw_signal,
        finding_text=finding_text,
        ordinal=1,
        category_code=_classify_gmp_summary(expose_cont or finding_text),
        evidence_level="A",
        evidence_url=evidence_url,
        finding_language=_language(row, "KO"),
        mfds_refs=refs,
        confidence=0.88,
        review_status="accepted",
    )]


def _from_mfds_recall(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    # [findings DB 구멍 수리 2026-07-12] MFDS 회수(recall-quality) raw_signal -- admin-action
    # 과 같은 원인(추출기 부재)으로 검색 DB에 안 들어가던 소스. collect_mfds_recall.py
    # _to_item 은 PRDUCT 없는 항목을 애초에 수집하지 않으므로(product 없으면 IntakeItem 자체가
    # None) 실 데이터에서 PRDUCT 는 항상 채워져 있다 -- ENTRPS 단독 존재는 방어적 트리거일 뿐.
    reason = _compact(raw.get("RTRVL_RESN"))
    product = _compact(raw.get("PRDUCT"))
    firm = _compact(raw.get("ENTRPS"))
    if not reason or not (product or firm):
        return []

    finding_text = f"{product}: {reason}" if product else reason

    return [gf.finding_from_raw_signal(
        raw_signal,
        finding_text=finding_text,
        ordinal=1,
        evidence_level="A",
        # 회수·폐기 공표 목록(건별 안정 URL 부재 — 브리프도 동일하게 목록 인덱스 사용).
        # official_url(data.go.kr 데이터셋)·source_url(API 엔드포인트)은 부적합이라 미사용.
        evidence_url=MFDS_RECALL_INDEX_URL,
        finding_language=_language(row, "KO"),
        mfds_refs=_extract_mfds_refs(reason),
        confidence=0.85,
        review_status="accepted",
    )]


def _from_warning_letter(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    # [FIND-1 M11] WL 본문을 483 Observation 처럼 개별 위반 finding 여러 건으로 분해한다(과거엔
    # 편지 전체가 finding 1건 -- 483 대비 정보 밀도가 완전히 달라 스캔이 불가능했다). 완전판
    # (wl_body_full)이 있으면 그것을, 없으면 excerpt(1500자, 절단 감수)를 쓴다.
    text = _compact(raw.get("wl_body_full")) or _compact(raw.get("wl_body_excerpt"))
    if not text:
        return []

    body = _cut_wl_footer(text) or text
    raw_blocks = _split_wl_violation_blocks(body)
    parts = [parts for parts in (_wl_block_parts(block) for block in raw_blocks) if parts]
    # [FIND-1 P1 A-S1] 품질 게이트 — 규제 지적 신호(조항 인용 or 위반 문형)가 있는 블록만
    # 방출한다. 화장품/OTC WL 이 인용한 제품 라벨 사용법·2인칭 명령형 지시문은 신호가 없어
    # 여기서 드랍된다(scope_status='ok' 오추출 유입 차단). 정당 위반 블록은 조항/문형을
    # 거의 항상 가지므로 통과한다.
    payload = [part for part in parts if _wl_block_is_regulatory(part[0])]
    if not payload:
        # degrade -- 앵커(번호/헤딩)가 없거나 유효 블록이 하나도 안 남으면 현행 동작 그대로
        # 통짜 1건(길이 상한도 적용하지 않는다 -- 회귀 0 이 우선). 문서 단위로 유효 위반이
        # 하나도 안 남는 경우의 안전망: 정당 위반을 통째로 잃지 않는다(needs_review 로 남아
        # 검수/승격 대상이 되고, 신호 없는 통짜는 P2 자동승격에서 accepted 되지 않는다).
        payload = [(body, body)]

    evidence_url = _evidence_url(raw_signal, raw, "url", "source_url")
    out: list[dict[str, Any]] = []
    for ordinal, (full_block, finding_text) in enumerate(payload, start=1):
        out.append(gf.finding_from_raw_signal(
            raw_signal,
            finding_text=finding_text,
            ordinal=ordinal,
            evidence_level="B",
            evidence_url=evidence_url,
            finding_language=_language(row, "EN"),
            cfr_refs=_extract_us_legal_refs(full_block),
            confidence=0.72,
            review_status="needs_review",
        ))
    return out


# 편지 결론/서명/nav boilerplate -- 위반 서술 뒤에 오는 이 마커들 중 가장 이른 위치부터 잘라
# 버린다. ★대소문자를 반드시 구분한다(re.I 금지): 이 마커들은 전부 편지의 섹션 제목/맺음말/
# nav 로 나오는 고정 대문자 문구다. re.I 를 쓰면 위반 본문 산문의 소문자 "at the conclusion of
# the inspection"(Genzyme 실측) 같은 표현을 푸터로 오인해 위반 서술 뒷부분과 조항(21 CFR 211.22)
# 을 통째로 잘라내는 회귀가 난다 -- "위반을 살린다"는 M11 목표와 정반대. Title-Case 제목형만 잡아
# LyfeUnit 실측 "… section 505(a). Conclusion As previously stated …"(대문자 C) 는 정확히 절단하되
# 소문자 산문은 보존한다.
_WL_FOOTER_MARKERS_RE = re.compile(
    r"\bConclusion\b"
    r"|Send your written response"
    r"|\bSincerely\b"
    r"|/S/"
    r"|Content current as of"
    r"|Regulated Product\(s\)"
    r"|Please note FDA posts warning letters"
)

# 번호 리스트 앵커: "1. " "2. " 처럼 문장/구절 경계(문서 시작·마침표+공백·닫는괄호+공백·콜론+공백)
# 뒤에서 시작하고 이어서 대문자(또는 인용부호)로 시작하는 항목만 잡는다. 이 경계 요구가 없으면
# "21 CFR 211.22." 같은 조항번호의 소수점 뒷자리("22.")를 리스트 항목으로 오탐한다(실측 확인됨).
# 하위항목 "a. b." 는 숫자가 아니므로 이 패턴에 안 걸려 상위 번호 블록에 자연히 포함된다(과분해
# 방지 -- 스펙 요구사항).
_WL_NUMBERED_ITEM_RE = re.compile(r"(\d{1,2})\.\s+(?=[A-Z\"“])")
_WL_NUMBERED_BOUNDARY_OK = ("\n", ". ", ") ", ": ")

# 섹션 헤딩 앵커: "Unapproved New Drug Violations" 처럼 2~5개의 Title-Case 단어 뒤에 "Violations"
# 로 끝나는 짧은 제목구. 바로 뒤에 공백+대문자가 이어질 때만 앵커로 인정한다 -- 원문 HTML 의
# <h2> 헤딩이 텍스트 추출 과정에서 다음 문단과 공백 하나로 그냥 이어붙는 특징(개행이 사라짐)을
# 이용한 판별이다. 이 조건이 없으면 문서 서두의 "FDA Review Violations were identified..."
# (뒤에 소문자 "were"가 옴 -- 진짜 헤딩이 아니라 페이지 타이틀+본문이 그냥 이어붙은 것)까지
# 헤딩으로 오탐해 서두가 첫 finding 이 돼 버린다(스펙: 서두는 반드시 버려야 함).
_WL_HEADING_RE = re.compile(r"((?:[A-Z][A-Za-z]*\s+){2,5}Violations)(?=\s+[A-Z])")

_WL_BLOCK_CHAR_CAP = 480
_WL_FRAGMENT_MIN_CHARS = 40

# [FIND-1 P1 A-S1 · 2026-07-21 RCA 원인 A] WL 블록 품질 게이트의 "규제 지적 신호". 화장품/OTC WL 이
# 본문에 인용하는 **제품 라벨 사용법·2인칭 명령형 지시문**("Use thick amount of the cream on the
# treatment area." · "Allow to absorb for 5-10 minutes." 등, SeeNext Venture 실측)은 조항 인용도,
# 아래 문형/조건 신호도 갖지 않아 finding 으로 방출되지 않는다(_wl_block_is_regulatory 에서 조항
# 인용과 OR 로 묶인다). 게이트는 "블록을 살릴지"를 permissive 하게 판정한다 — 신호를 놓쳐도(false
# negative) 문서 단위 통짜 폴백이 받으므로 정당 위반이 통째로 사라지지 않는다(회귀 0 우선).
#
# ★교정(라이브 3,144 accepted WL 대조): 두 그룹으로 나눈다.
#   (1) 위반 서술 문형("failed to"/"misbranded"/"your firm"…),
#   (2) GMP 환경/시설 조건 신호 — 무균·청정 관찰형 위반은 위 문형 없이 상태만 서술한다("aseptic
#       areas had visibly dirty surfaces" · "operator blocked first air" · "vermin were observed").
#   (2) 없이 (1)만 두면 이 조건형 위반 ~26건을 놓쳐 드랍한다. (2)를 더하면 accepted WL 의
#   99.9%가 신호를 갖고(2건만 경계형 미스), 화장품 라벨 어휘는 (1)(2) 어디에도 없어 판별력이
#   오히려 는다. re.I 로 대소문자 무관.
_WL_VIOLATION_SIGNAL_RE = re.compile(
    # (1) 위반 서술 문형
    r"fail(?:ed|s|ure)\b"
    r"|\b(?:did|does|do|was|were|is|are|has|have|had)\s+not\b"
    r"|\bnot\s+(?:be\s+)?(?:establish|validat|maintain|clean|document|adequate|"
    r"qualif|verif|approv|conduct|perform|in\s+compliance)"
    r"|adulterat|misbrand|\bviolat|unapproved|deviat|inadequate|contaminat"
    r"|\byour\s+(?:firm|response|quality|written|drug|product)"
    r"|\bC?GMP\b|current\s+good\s+manufacturing"
    r"|\b(?:we|fda|investigators?)\s+(?:observed|identified|documented|noted|found)"
    r"|inspection\s+(?:revealed|found|documented|identified)"
    r"|\b(?:is|are)\s+required\b|\brequired\s+to\b"
    r"|\black(?:ed|ing|s)?\s+of\b|\bnot\s+in\s+compliance\b|noncompliance"
    # (2) GMP 환경/시설 조건 신호 (무균/청정 관찰형 위반)
    r"|\bobserved\b|visibly\s+dirty|difficult\s+to\s+clean|particle[-\s]?generating"
    r"|\baseptic\b|\bsterile\b|\bnon-?sterile\b|\bfirst\s+air\b|\bclean[-\s]?room\b"
    r"|\bHEPA\b|\bISO[\s-]*(?:classified|\d)|\bvermin\b"
    r"|\bforeign\s+(?:matter|substance|material)|\bresidues?\b|\bdebris\b|\brust"
    r"|\bexposed\s+(?:skin|hair)",
    re.I,
)


def wl_violation_signal_present(text: str) -> bool:
    """text 에 WL 위반 서술/조건 신호(_WL_VIOLATION_SIGNAL_RE)가 있는지. A-S1 드랍 게이트와
    P2 검수 자동승격 서비스(findings_review_promote_service)가 **동일 신호 원천**을 공유하도록
    공개한다 — 승격은 저장 cfr_refs(조항 신호) AND 이 함수(위반 신호)로 판정한다(둘 다 있을 때만
    고신뢰). 조항 신호는 저장 열을 쓰므로 여기서 재추출하지 않는다."""
    return bool(_WL_VIOLATION_SIGNAL_RE.search(text or ""))


def _wl_block_is_regulatory(full_block: str) -> bool:
    """블록이 규제 지적 신호(21 CFR/U.S.C/FD&C 조항 인용 **또는** 위반 서술 문형)를 하나라도
    갖는지. 둘 다 없으면 인용된 제품 라벨 사용법·실질 없는 파편으로 보고 방출하지 않는다(A-S1).
    조항 추출은 방출 경로가 이미 계산하는 _extract_us_legal_refs 를 그대로 재사용한다."""
    return bool(_extract_us_legal_refs(full_block)) or wl_violation_signal_present(full_block)


def _cut_wl_footer(text: str) -> str:
    match = _WL_FOOTER_MARKERS_RE.search(text)
    if not match:
        return text
    return text[: match.start()].rstrip()


def _wl_numbered_anchors(text: str) -> list[tuple[int, int]]:
    anchors: list[tuple[int, int]] = []
    for match in _WL_NUMBERED_ITEM_RE.finditer(text):
        start = match.start()
        if start == 0 or text[max(0, start - 2):start] in _WL_NUMBERED_BOUNDARY_OK:
            anchors.append((start, match.end()))
    return anchors


def _wl_heading_anchors(text: str) -> list[tuple[int, int]]:
    anchors: list[tuple[int, int]] = []
    for match in _WL_HEADING_RE.finditer(text):
        start = match.start(1)
        if start == 0 or text[max(0, start - 2):start] in (". ", "! ", "? ") or text[max(0, start - 1):start] == "\n":
            anchors.append((start, match.end(1)))
    return anchors


def _split_wl_violation_blocks(text: str) -> list[str]:
    """(우선순위 degrade) 번호 리스트 -> 섹션 헤딩 -> [] (호출부가 통짜 1건으로 되돌린다).

    각 anchor 는 (label_start, content_start): label_start 는 다음 anchor 를 만나기 전까지
    "이전 블록"의 끝 경계로, content_start 는 "이 블록" 자신의 시작(번호/헤딩 잔여를 뗀 지점)으로
    쓰인다.
    """
    anchors = _wl_numbered_anchors(text)
    if len(anchors) < 2:
        anchors = _wl_heading_anchors(text)
    if len(anchors) < 2:
        return []
    blocks = []
    for index, (_label_start, content_start) in enumerate(anchors):
        end = anchors[index + 1][0] if index + 1 < len(anchors) else len(text)
        blocks.append(text[content_start:end])
    return blocks


def _wl_block_parts(raw_block: str) -> tuple[str, str] | None:
    """블록 1개 -> (조항추출용 원문 전체, 표시용 finding_text) 또는 미완결 조각이면 None.

    excerpt(1500자) 절단으로 마지막 블록이 문장 중간에서 잘리는 경우(`...data, i`)를 짧고
    미완결이면 버린다(<40자 + 문장부호 없음). 길면 -- 잘렸어도 -- 정보가 있으니 보존한다.
    """
    block = _compact(raw_block)
    if not block:
        return None
    ends_with_terminal = block[-1] in ".?!\""
    if not ends_with_terminal and len(block) < _WL_FRAGMENT_MIN_CHARS:
        return None
    return block, _cap_wl_block_text(block, ends_with_terminal)


def _cap_wl_block_text(block: str, ends_with_terminal: bool) -> str:
    """표시용 상한(약 480자) -- 483 은 첫 문장만 쓰지만 WL 위반은 한 문단이 한 위반이라 문단
    전체를 유지하는 게 자연스럽다. 다만 벽텍스트 방지를 위해 480자 근방에서 안전 절단한다.
    문장부호 없이 끝나는(미완결) 블록은 짧아도 길어도 "…" 로 표시해 절단됐음을 드러낸다.
    """
    if len(block) <= _WL_BLOCK_CHAR_CAP:
        return block if ends_with_terminal else block.rstrip(" .,;:") + "…"
    truncated = block[:_WL_BLOCK_CHAR_CAP]
    cut = truncated.rfind(" ")
    if cut > 0:
        truncated = truncated[:cut]
    return truncated.rstrip(" .,;:") + "…"


def _from_whopir(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    text = _compact(raw.get("whopir_excerpt"))
    if not text:
        return []

    return [gf.finding_from_raw_signal(
        raw_signal,
        finding_text=text,
        ordinal=1,
        evidence_level="B",
        evidence_url=_evidence_url(raw_signal, raw, "pdf_url", "url", "list_page"),
        finding_language=_language(row, "EN"),
        confidence=0.72,
        review_status="needs_review",
    )]


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _language(row: dict[str, Any], default: str) -> str:
    return _compact(row.get("language")) or default


def _raw_signal_with_firm_fallback(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    if _compact(raw_signal.get("firm_name")):
        return raw_signal
    signal = dict(raw_signal)
    fallback = (
        _compact(raw.get("firm"))
        or _compact(raw.get("company"))
        or _compact(raw.get("manufacturer"))
        or _compact(raw.get("anchor_text"))
        or _compact(row.get("headline"))
        or _compact(raw_signal.get("title"))
    )
    signal["firm_name"] = fallback
    if not _compact(signal.get("site_name")):
        signal["site_name"] = fallback
    return signal


def _positive_int(value: Any, *, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _evidence_url(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    *raw_keys: str,
    fallback: str = "",
) -> str:
    # [Fix A 2026-07-12 — findings evidence_url 품질 계층] 근본 원인 #1 수리: 추출기가
    # 명시한 문서급 raw 필드(raw_keys)를 최우선으로 시도한다. raw_signal 레벨(official_url/
    # source_url)은 소스마다 의미가 달라(483/WL=개별 문서, MFDS 계열=목록/데이터셋/API
    # 엔드포인트) 마지막 순위로 강등한다 -- 이전 순서(official_url 최우선)에서는 추출기가
    # "이 raw 필드가 문서급 링크"라고 명시해도 official_url 에 밀려 죽었다(사고2: MFDS GMP
    # 실사 raw.source_url=PDF 인데 official_url=목록 CCBBD03 이 승격). 모든 후보는 품질
    # 분류기(gf.evidence_url_quality_error)를 통과해야 채택된다 -- serviceKey/API 엔드포인트/
    # data.go.kr 데이터셋 페이지가 조용히 evidence_url 로 승격되는 경로를 차단한다.
    candidates = [raw.get(key) for key in raw_keys]
    candidates.append(raw_signal.get("official_url"))
    candidates.append(raw_signal.get("source_url"))
    for value in candidates:
        text = _compact(value)
        if text and not gf.evidence_url_quality_error(text):
            return text
    return fallback


def _gmp_table_text(item: dict[str, Any]) -> str:
    summary = _gmp_summary_text(item)
    if not summary:
        return ""
    parts = [
        _compact(item.get("area")),
        _compact(item.get("severity")),
        _compact(item.get("legal_basis") or item.get("basis") or item.get("law_ref")),
        summary,
    ]
    return _compact(" ".join(part for part in parts if part))


def _gmp_summary_text(item: dict[str, Any]) -> str:
    return _compact(item.get("summary") or item.get("deficiency") or item.get("finding") or item.get("issue"))


def _classify_gmp_summary(text: str) -> str:
    lowered = _compact(text).lower()
    if any(token in lowered for token in ("cross-contamination", "contamination", "교차오염", "오염")):
        return "contamination_control"
    return gf.classify_finding_category(text)


def _extract_mfds_refs(text: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\[별표\s*\d+(?:의\d+)?\]\s*[^,\.;\s]*(?:\s*[가-힣]목)?", text or ""):
        ref = _compact(match.group(0))
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def _dedupe_valid_findings_with_report(
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    dropped_invalid = 0
    dropped_duplicate_text = 0
    invalid_errors: set[str] = set()
    for finding in findings:
        key = _compact(finding.get("finding_text")).casefold()
        if not key or key in seen_texts:
            dropped_duplicate_text += 1
            continue
        errors = gf.validate_finding(finding)
        if errors:
            dropped_invalid += 1
            invalid_errors.update(errors)
            continue
        seen_texts.add(key)
        out.append(finding)
    report = {
        "extracted": len(findings),
        "kept": len(out),
        "dropped_invalid": dropped_invalid,
        "dropped_duplicate_text": dropped_duplicate_text,
        "invalid_errors": sorted(invalid_errors),
    }
    return out, report
