#!/usr/bin/env python3
"""GRM EDQM CEP Actions (Suspensions/Withdrawals/Restorations) Collector.

╔══════════════════════════════════════════════════════════════════════════════╗
║ ★★★ 이 소스는 **수집 불가 판정**을 받았다 (2026-08-11). 이 브랜치는 머지되지     ║
║ 않았고, 배선(collect_intake·card_scaffold·findings_extractors·워크플로)도 하지   ║
║ 않았다. 아래 `collect_edqm_cep()` 의 네트워크 경로는 **동작하지 않는다.**        ║
║                                                                              ║
║ 근거(같은 머신·같은 순간·같은 UA 실측):                                        ║
║   · curl            → HTTP 200 (225,976 bytes)                               ║
║   · python requests → HTTP 403 `cf-mitigated: challenge` (Cloudflare JS 챌린지) ║
║   → UA 문자열 게이트가 아니라 **HTTP 클라이언트(TLS 지문) 단위 봇 방어**다.      ║
║     UA 를 무엇으로 바꿔도 requests/urllib 로는 못 뚫는다.                       ║
║   · EDQM 이 기계 소비용으로 발행하는 **공식 RSS 피드도 동일하게 403** 이다        ║
║     (/en/newsroom-cep/-/asset_publisher/cUV3HrUHZ3lm/rss).                    ║
║   · robots.txt 가 ClaudeBot·GPTBot·CCBot·Google-Extended·Bytespider 등        ║
║     AI 크롤러를 **이름으로 전면 차단**하고 `Content-Signal: ai-train=no` 다.     ║
║                                                                              ║
║ 뚫는 방법은 TLS 지문 위장·헤드리스 브라우저뿐인데 **만들지 않는다** —            ║
║ 이 저장소는 FDA483 Akamai 차단 때 같은 판단을 이미 내렸다                       ║
║ ([[grm-fda483-akamai-block-json-backbone]] "TLS위장 우회는 안 만듦").          ║
║                                                                              ║
║ ★그래도 이 파일을 남겨 두는 이유: **파서(`parse_cep_actions`)는 실측 스냅샷으로  ║
║ 검증이 끝났다**(tests/test_edqm_cep.py 32건 통과, 네트워크 0). EDQM 에서 데이터  ║
║ 접근 허락을 받거나 공식 배포 채널이 열리면 **입력 경로만 갈아끼우면 된다.**       ║
║ 판정 근거 전문 = docs/신규소스_수입경보_FDA대시보드_EDQM_타당성_2026-08-11.md §4 ║
╚══════════════════════════════════════════════════════════════════════════════╝

EDQM(유럽의약품 및 보건의료품질위원회, Council of Europe)이 공개하는 **CEP(Certificate
of Suitability) 조치 현황** 단일 페이지를 수집한다. EudraGMDP(EU GMP NCR)·MHRA GMDP
NCR 과 같은 "업체/원료 단위 GMP 비준수 신호" 계열이지만, 이 소스는 검사보고서가 아니라
**약전 인증서(CEP) 자체의 상태 변경**(정지/철회/복원)을 알린다.

채널 (실측 2026-08-11):
    https://www.edqm.eu/en/actions-on-ceps
  단일 정적(서버렌더) 페이지. 세션 불요·페이지네이션 없음. **UA_GATED**: 기본
  `requests`/urllib UA 는 403(5/5 실측), 브라우저 UA 는 200(5/5 실측) — Akamai/WAF 류
  게이트로 `probe_source_reachability.py` 의 FDA483 조사에서 확인된 것과 같은 계열.
  그래서 `grm_common.http_get_html` 을 그대로 재사용하되 `headers={"User-Agent": ...}`
  로 브라우저 UA 를 덮어쓴다(새 HTTP 클라이언트를 만들지 않는다 — http_get_html 은
  `headers` kwarg 를 기본 헤더 위에 병합해주므로 이 재사용만으로 충분하다).

  ★연타하면 레이트리밋(단일 페이지·1일 1회 수집이라 실무상 무관 — probe 로 확인된
  범위를 벗어나 반복 조회하지 말 것).

구조 (실측 2026-08-11, `tests/fixtures/edqm_actions_on_ceps.html` 에 스냅샷 보존):
  섹션 3개, `data-analytics-asset-title="... - Actions on CEPs - <NAME>"` 로 경계
  마커. NAME ∈ {"CEP Suspensions", "CEP Withdrawals", "Restoration of suspended CEP"}.
  같은 마커 패턴에 "Looking for a CEP?" 안내 섹션도 잡히므로 **화이트리스트 3종만
  인정**한다(그 외는 무시 — 새 섹션이 추가돼도 화이트리스트에 없으면 조용히 건너뛴다는
  뜻이라, 그 경우 sections_found 로는 안 잡히고 무해하게 무시됨을 인지할 것).

  섹션 안에는 `<h4><strong>사유</strong></h4>` 표제가 오고 그 뒤에 `<table>`(헤더
  Date|Substance name|CEP Number)이 따른다. Restoration 섹션만 예외 — 사유 소구분
  없이 표 하나뿐이다(h4 자체가 없음. 빈 `<h4> </h4>` 가 등장하는 섹션도 있어 공백
  전용 h4 는 사유로 인정하지 않는다). 실측 표는 총 7개(Suspensions 3 + Withdrawals 3
  + Restoration 1).

  빈 표는 두 형태로 나타난다 — 데이터 행이 `-|-|-` 하나뿐이거나(플레이스홀더),
  `<tbody></tbody>` 가 완전히 비어 있거나(Restoration 실측). 둘 다 데이터 0건으로
  처리한다.

  날짜 = DD/MM/YYYY. 2026-06-11 선행 조사가 `22/09//2023` 같은 **슬래시 중복 오타**를
  실측했다(이 스냅샷엔 없음) → `_parse_ddmmyyyy` 가 연속 슬래시를 관대하게 허용해
  방어한다. 그래도 파싱이 실패하면 그 행은 **조용히 버리지 않고** `ParseHealth
  .date_parse_failures` 로 카운트해 로그/에러 문자열에 싣는다.

  물질명에 HTML 엔티티가 섞인다(실측 `Diosmin&nbsp;`) → unescape + `\xa0`→공백 + trim.

  ★사유 문언 철자가 섹션마다 다르다 — "fulfill"(Suspensions) vs "fulfil"(Withdrawals).
  정확일치 매칭 금지, 부분문자열 키워드로 분류한다(`_classify_reason`).

★★소멸성 데이터 — 롤링 창(실측 날짜폭 약 5.5개월, 2026-02-16~2026-07-27):
  이 페이지는 "최근 ~6개월" 창만 보여주는 롤링 목록이다. 지나간 항목은 페이지에서
  **사라진다** — 과거분을 뒤늦게 백필할 방법이 원천적으로 없다(원문 자체가 소멸).
  매일 수집 + document_id dedup 이 유일한 이력 축적 경로다. 하루라도 수집을 거르면
  그 사이 사라진 항목은 영구 유실된다 — 이 소스는 다른 GRM 소스처럼 "놓쳐도 나중에
  다시 받으면 된다"가 성립하지 않는다.

설계:
  - **document_id = "<CEP번호 정규화>|<action>|<reason_code>|<YYYY-MM-DD>"**. CEP 번호만
    쓰지 않는 이유: 같은 CEP 가 정지 후 복원되면 서로 다른 사실이라 다른 키가 되어야
    한다(그게 이 설계의 목적).
  - firm/site_country 는 **비운다** — 원문 표에 업체(CEP 보유자)·제조소 정보가 아예
    없다. 물질명을 firm 에 넣으면 firm_key·업체 프로파일·Top업체 집계가 오염되므로
    물질명은 headline/raw_payload 에만 싣는다.
  - official_url/source_url/api_query 는 **전 행이 동일한 페이지 URL 을 공유**한다 —
    개별 항목 딥링크가 원문에 없다(MFDS 회수의 단일 evidence_url 과 같은 상황). 없는
    링크를 만들어내지 않는다.
  - signal_tier: reason_code == "gmp-non-compliance" → "Tier 3", 그 외 "Tier 2".
  - qa_relevance = "Likely" — EU GMP NCR/MHRA GMP NCR 쌍둥이와 동일 근거(CEP 조치는
    원료의약품 GMP/품질 사안이라 전부 관련). 설계 문서의 "■ 필드" 목록엔 명시가 없어
    쌍둥이 관례를 그대로 따른 추론값이다(핸드오프에 명시).
  - 0건은 정상(저빈도 + 롤링 창) — health 판정에서 실패로 치지 않는다. EU/MHRA GMP
    NCR 과 동일하게 성긴 소스로 취급.

★findings(결정론 이중언어)는 이 모듈의 범위가 아니다 — `findings_extractors.py` 에
  `_from_edqm_cep` 를 신설하는 후속 작업이 raw_payload(reason_code/reason_text/action/
  substance/cep_number/action_date)를 읽어 finding_text(EN)+finding_text_ko(KO)를
  만든다. 이 모듈은 그 추출기가 바로 쓸 수 있도록 `REASON_KO_MAP`/`ACTION_KO_MAP`
  (닫힌 5종 집합 한국어 정본)을 여기서 export 해 둔다 — 같은 매핑을 두 파일에서
  따로 유지하지 않기 위함.

★ENABLE_EDQM_CEP 플래그는 아직 `collect_intake.py` 에 배선되지 않았다(이 모듈은
  독립적으로 테스트 가능한 순수 함수 계약만 제공). vars 전용·기본 off 로
  `ENABLE_EU_GMP_NCR` 바로 옆에 같은 모양으로 추가하는 것이 다음 단계 — workflow_dispatch
  입력 25개 상한 때문에 입력은 만들지 않는다(ISPE/EU GMP NCR 관례와 동일 이유).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from grm_common import http_get_html, log
from collect_intake import IntakeItem, SRC_TYPE_OFFICIAL_PAGE, _within_window

# ── 소스 식별 상수 ──────────────────────────────────────────────────────────
# ★불가침: SOURCE_EDQM_CEP 문자열은 finding_id 해시 입력이다. 한 번 정한 값을
# 나중에 바꾸면 고아 행이 생긴다 — 절대 바꾸지 말 것.
SOURCE_EDQM_CEP = "EDQM CEP Actions"
TYPE_EDQM_CEP = "cep-action"
LANGUAGE_EN = "EN"
REGION_EU_EDQM = "EU/EDQM"

EDQM_ACTIONS_URL = "https://www.edqm.eu/en/actions-on-ceps"

# UA_GATED 실측(2026-08-11): 기본 UA 403 x5/5, 브라우저 UA 200 x5/5. FDA483 Akamai
# 조사(probe_source_reachability.py `_BROWSER_UA`)에서 검증된 것과 동일 계열 문자열을
# 그대로 재사용한다(TLS 위장은 하지 않는다 — 이미 만들지 않기로 판정된 우회).
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_HTTP_TIMEOUT = 30
_HTTP_RETRIES = 3

# 섹션명(화이트리스트) → action. "Looking for a CEP?" 등 다른 마커는 여기 없으면
# 자동으로 무시된다.
_SECTION_ACTION: dict[str, str] = {
    "CEP Suspensions": "suspension",
    "CEP Withdrawals": "withdrawal",
    "Restoration of suspended CEP": "restoration",
}

# reason_code 한국어 정본(닫힌 5종 집합 — findings_extractors._from_edqm_cep 후속
# 작업이 그대로 import 해 쓴다).
REASON_KO_MAP: dict[str, str] = {
    "holder-request": "인증 보유자 요청(승인된 조건에서 일시적으로 생산 불가)",
    "gmp-non-compliance": "GMP 비준수",
    "certification-procedure": "적합성인증 절차 요건 미충족",
    "monograph-deleted": "유럽약전 모노그래프 삭제",
    "restoration": "정지 해제(복원)",
}
ACTION_KO_MAP: dict[str, str] = {
    "suspension": "정지",
    "withdrawal": "철회",
    "restoration": "복원",
}

# 부분문자열 매칭 순서 — "fulfil"/"fulfill" 철자 차이를 흡수하려 "certification
# procedure" 만 검사(fulfil/fulfill 단어는 보지 않는다).
_REASON_PATTERNS: tuple[tuple[str, str], ...] = (
    ("holder-request", "upon request from the holder"),
    ("gmp-non-compliance", "gmp non-compliance"),
    ("certification-procedure", "certification procedure"),
    ("monograph-deleted", "deletion of the monograph"),
)

# ── 파싱 정규식(저장소 관례 — bs4 미의존, mhra_gmdp_client.py 와 동형 정규식 파싱) ──
_MARKER_RE = re.compile(r'data-analytics-asset-title="([^"]*)"')
_EVENT_RE = re.compile(r"<h4[^>]*>(.*?)</h4>|<table[^>]*>(.*?)</table>", re.S | re.I)
_TBODY_RE = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.S | re.I)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)

# DD/MM/YYYY, 슬래시 1개 이상 관대 허용(2026-06-11 선행 조사의 `22/09//2023` 방어).
_DATE_RE = re.compile(r"^(\d{1,2})/+(\d{1,2})/+(\d{4})$")

# 날짜 파싱 실패율이 이 비율을 넘으면 표 구조 변경 의심 → error 승격(EU/MHRA GMP NCR
# 의 _MAX_RECORD_FAILURE_RATIO 와 동형 가드).
_MAX_DATE_FAILURE_RATIO = 0.5


def _clean_text(raw: str | None) -> str:
    """HTML 태그 제거 + 엔티티 unescape + `\xa0`→공백 + 공백 정규화."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_cep_number(raw: str) -> str:
    return re.sub(r"\s+", " ", raw or "").strip().upper()


def _parse_ddmmyyyy(raw: str) -> str:
    """'27/07/2026' -> '2026-07-27'. 방어 파싱 실패 시 ''(행 버림은 호출부 책임)."""
    match = _DATE_RE.match((raw or "").strip())
    if not match:
        return ""
    day, month, year = match.groups()
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return ""


def _classify_reason(reason_text: str, section_name: str) -> str:
    if section_name == "Restoration of suspended CEP":
        return "restoration"
    haystack = reason_text.lower()
    for code, needle in _REASON_PATTERNS:
        if needle in haystack:
            return code
    return "other"


@dataclass
class CepAction:
    """섹션 표 한 데이터 행 = CEP 조치 1건."""

    section: str            # "CEP Suspensions" | "CEP Withdrawals" | "Restoration of suspended CEP"
    action: str              # suspension | withdrawal | restoration
    reason_text: str          # 사유 h4 원문(verbatim, EN). Restoration 은 "".
    reason_code: str           # holder-request | gmp-non-compliance | certification-procedure
                                # | monograph-deleted | restoration | other
    substance: str              # 정리된 물질명
    cep_number: str               # 정규화된 CEP 번호
    action_date_raw: str            # 원문 DD/MM/YYYY 문자열(verbatim)
    date_iso: str                     # YYYY-MM-DD


@dataclass
class ParseHealth:
    """`parse_cep_actions` 진단 — 침묵 실패 금지: 실패/이상은 전부 여기 카운트된다."""

    sections_found: dict[str, bool] = field(default_factory=dict)
    tables_by_section: dict[str, int] = field(default_factory=dict)
    data_rows: int = 0
    empty_tables: int = 0
    date_parse_failures: int = 0
    reason_other_count: int = 0
    reason_other_samples: list[str] = field(default_factory=list)

    @property
    def tables_found(self) -> int:
        return sum(self.tables_by_section.values())


def parse_cep_actions(html_text: str) -> tuple[list[CepAction], ParseHealth]:
    """EDQM "Actions on CEPs" 페이지 HTML -> (행 목록, 진단). 네트워크 0(순수 함수)."""
    health = ParseHealth(sections_found={name: False for name in _SECTION_ACTION})
    actions: list[CepAction] = []

    markers = [(m.start(), m.group(1)) for m in _MARKER_RE.finditer(html_text)]
    for idx, (pos, title) in enumerate(markers):
        section_name = title.rsplit(" - ", 1)[-1].strip()
        if section_name not in _SECTION_ACTION:
            continue  # 화이트리스트 밖(예: "Looking for a CEP?") — 무시
        health.sections_found[section_name] = True
        end = markers[idx + 1][0] if idx + 1 < len(markers) else len(html_text)
        section_html = html_text[pos:end]
        action = _SECTION_ACTION[section_name]
        is_restoration = section_name == "Restoration of suspended CEP"

        current_reason = ""
        for ev in _EVENT_RE.finditer(section_html):
            heading, table_html = ev.group(1), ev.group(2)
            if table_html is None:
                text = _clean_text(heading)
                if text:
                    current_reason = text
                continue

            health.tables_by_section[section_name] = (
                health.tables_by_section.get(section_name, 0) + 1
            )
            reason_text = "" if is_restoration else current_reason
            reason_code = _classify_reason(reason_text, section_name)

            tbody_match = _TBODY_RE.search(table_html)
            tbody_html = tbody_match.group(1) if tbody_match else ""
            table_data_rows = 0
            for row_html in _ROW_RE.findall(tbody_html):
                cells = [_clean_text(c) for c in _CELL_RE.findall(row_html)]
                if not cells or all(c == "-" for c in cells):
                    continue  # "-|-|-" 플레이스홀더(또는 완전 빈 tbody 는 애초에 findall 이 0행)
                if len(cells) < 3:
                    # 셀 수 이상 -- 구조가 깨져 날짜/물질/번호를 신뢰할 수 없다. 조용히
                    # 버리지 않고 date_parse_failures 로 표면화(가장 가까운 실패 범주).
                    health.date_parse_failures += 1
                    continue
                date_raw, substance_raw, cep_raw = cells[0], cells[1], cells[2]
                date_iso = _parse_ddmmyyyy(date_raw)
                if not date_iso:
                    health.date_parse_failures += 1
                    continue
                if reason_code == "other":
                    health.reason_other_count += 1
                    if reason_text and reason_text not in health.reason_other_samples:
                        health.reason_other_samples.append(reason_text)
                actions.append(CepAction(
                    section=section_name,
                    action=action,
                    reason_text=reason_text,
                    reason_code=reason_code,
                    substance=substance_raw,
                    cep_number=_normalize_cep_number(cep_raw),
                    action_date_raw=date_raw,
                    date_iso=date_iso,
                ))
                table_data_rows += 1
                health.data_rows += 1
            if table_data_rows == 0:
                health.empty_tables += 1

    return actions, health


def _to_item(act: CepAction, document_id: str) -> IntakeItem:
    action_ko = ACTION_KO_MAP.get(act.action, act.action)
    if act.reason_code == "other":
        # ★한국어를 지어내지 않는다 -- 매칭 안 된 사유는 원문(EN) verbatim 을 그대로 노출.
        reason_display = act.reason_text or act.reason_code
    else:
        reason_display = REASON_KO_MAP.get(act.reason_code, act.reason_code)
    headline = f"{act.substance} — CEP {action_ko} ({reason_display})"[:240]

    signal_tier = "Tier 3" if act.reason_code == "gmp-non-compliance" else "Tier 2"

    raw_payload: dict[str, Any] = {
        "api": "EDQM Actions on CEPs",
        "section": act.section,
        "action": act.action,
        "reason_code": act.reason_code,
        "reason_text": act.reason_text,
        "substance": act.substance,
        "cep_number": act.cep_number,
        "action_date": act.action_date_raw,
        "edqm_actions_url": EDQM_ACTIONS_URL,
    }

    return IntakeItem(
        source=SOURCE_EDQM_CEP,
        document_id=document_id,
        date_iso=act.date_iso,
        headline=headline,
        official_url=EDQM_ACTIONS_URL,        # 전 행 공유(개별 딥링크가 원문에 없음)
        type_or_class=TYPE_EDQM_CEP,
        firm="",                              # 원문에 업체 정보 없음 -- 비운다
        api_query=EDQM_ACTIONS_URL,
        qa_relevance="Likely",                # EU/MHRA GMP NCR 쌍둥이 관례(추론값)
        source_type=SRC_TYPE_OFFICIAL_PAGE,
        signal_tier=signal_tier,
        raw_payload=raw_payload,
        language=LANGUAGE_EN,
        region_jurisdiction=REGION_EU_EDQM,
        site_country="",                      # 원문에 제조소 국가 없음 -- 비운다
        source_url=EDQM_ACTIONS_URL,
        evidence_candidate="A",
    )


def collect_edqm_cep(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """EDQM CEP Actions 수집. (items, error_msg).

    - 페이지 fetch 자체 실패 → error(0건 침묵 금지).
    - 화이트리스트 3섹션 중 하나라도 안 잡히면 구조 변경 의심 → error.
    - 날짜 파싱 실패율 > 50% → 표 구조 변경 의심 → error.
    - reason_code='other' 발생 은 error 아님(새 사유 카테고리 정상 편입) -- 단
      WARN 로그로 반드시 표면화한다(조용히 흘리면 아무도 모른다).
    - 윈도우 내 0건 → 정상(빈 리스트, error 없음). 롤링 창 + 저빈도 소스라 일일
      0건이 정상이다(EU/MHRA GMP NCR 과 동일 처리).
    """
    log("INFO", f"EDQM CEP Actions 수집: {EDQM_ACTIONS_URL} [{start.isoformat()}~{end.isoformat()}]")

    try:
        html_text = http_get_html(
            EDQM_ACTIONS_URL,
            timeout=_HTTP_TIMEOUT,
            retries=_HTTP_RETRIES,
            headers={"User-Agent": _BROWSER_UA},
            label="EDQM CEP Actions",
        )
    except Exception as e:  # noqa: BLE001
        return [], f"EDQM CEP Actions 수집 실패(네트워크): {e!r}"

    actions, health = parse_cep_actions(html_text)

    missing_sections = [name for name, found in health.sections_found.items() if not found]
    if missing_sections:
        return [], (
            "EDQM CEP Actions 파싱 실패 — 섹션 누락(구조 변경 의심): "
            + ", ".join(missing_sections)
        )

    total_date_attempts = health.data_rows + health.date_parse_failures
    if total_date_attempts and (
        health.date_parse_failures / total_date_attempts > _MAX_DATE_FAILURE_RATIO
    ):
        return [], (
            f"EDQM CEP Actions 날짜 파싱 실패율 과다"
            f"({health.date_parse_failures}/{total_date_attempts}) — 표 구조 변경 의심"
        )

    if health.date_parse_failures:
        log("WARN", f"EDQM CEP Actions 날짜 파싱 실패 {health.date_parse_failures}건 — 해당 행 건너뜀")
    if health.reason_other_count:
        log("WARN", (
            f"EDQM CEP Actions 사유 미매칭(reason_code=other) {health.reason_other_count}건 "
            f"— 신규 사유 카테고리 의심(한국어 미생성, 영문 verbatim 유지). "
            f"예: {health.reason_other_samples[:3]}"
        ))

    items: list[IntakeItem] = []
    seen: set[str] = set()
    for act in actions:
        if not _within_window(act.date_iso, start, end):
            continue
        document_id = f"{act.cep_number}|{act.action}|{act.reason_code}|{act.date_iso}"
        if document_id in seen:
            continue
        seen.add(document_id)
        items.append(_to_item(act, document_id))

    log("INFO", (
        f"EDQM CEP Actions 수집 완료: {len(items)}건 "
        f"(파싱 {health.data_rows}행, 표 {health.tables_found}개, 빈표 {health.empty_tables}개)"
    ))
    return items, None
