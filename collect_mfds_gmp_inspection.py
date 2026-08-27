#!/usr/bin/env python3
"""GRM MFDS GMP Inspection Result Collector - Phase 2d.

Collects metadata from nedrug's public "의약품등 GMP 실사 결과공개"
HTML board, then best-effort extracts public attachment text.
"""

from __future__ import annotations

import io
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any

from grm_common import env_flag, http_get_bytes, log
from collect_intake import (
    IntakeItem,
    SOURCE_MFDS,
    SRC_TYPE_OFFICIAL_SCRAPE,
    _within_window,
)


BOARD_URL = "https://nedrug.mfds.go.kr/pbp/CCBBD03"
LIST_URL = "https://nedrug.mfds.go.kr/pbp/CCBBD03/getList"
DOWNLOAD_URL_BASE = "https://nedrug.mfds.go.kr/cmn/edms/down/"

TYPE_GMP_INSPECTION = "gmp-inspection"
LANGUAGE_KO = "KO"
REGION_MFDS = "Korea (MFDS)"

PAGE_SIZE = 100
MAX_PAGES = 10
ATTACHMENT_REQUEST_DELAY_SECONDS = 1.0
MAX_ATTACHMENT_TEXT_CHARS = 12000
MAX_ATTACHMENT_BODY_CHARS = 6000
HTTP_RETRIES = 3

LAST_HEALTH: dict[str, Any] = {}

_NO_DEFICIENCY_RE = re.compile(
    r"(지적\s*\(?보완\)?\s*사항\s*(?:\(Deficiencies\))?\s*없음|"
    r"지적\s*사항\s*없음|보완\s*사항\s*없음)"
)
# [2026-08-02 실측] 수입 **사전 GMP 평가** 보고서는 "지적(보완)사항" 섹션 자체가 없고
# 결론을 `❍ 실사 결과: 적합` 한 줄로만 쓴다. 위 _NO_DEFICIENCY_RE 가 그 어법을 몰라
# 7건이 `unknown`(판정 불능)으로 적재됐다 — 원문이 "적합"이라고 명시했는데 우리가
# "모르겠다"고 기록한 것이다([[부재 어휘]] 함정: 우리의 실패와 문서의 사실은 다른 값이다).
# 결과적으로 카드 본문에서 "지적사항 판정" 줄이 통째로 빠져, 적합 판정을 받은 실사인데
# 그 사실을 말하지 않는 카드가 나갔다.
#
# ★단순 문자열 `적합` 은 쓰지 않는다 — 실측상 "적합"을 포함한 문서 24건 중 **17건이
#   실제 지적사항을 갖고 있다**(부적합·적합성·적합하지 등 다른 용례). 반드시 `실사 결과`
#   앵커에 붙은 형태만 본다. 앵커 형태는 정확히 7건만 잡고 지적사항 보유 문서와 겹침 0.
# ★`(?<![부불])` — "실사 결과: 부적합" 을 통과시키지 않는다(이중 안전장치).
#
# ★[앵커 확장 2026-08-12] `실사 결과` 형태는 실제로는 **소수 어법**이었다. 2026-08-02 에
# 이 앵커를 만들 때의 모집단이 7건뿐이라 그게 전부인 줄 알았는데, 전수 실측(08-12)에서
# findings 0건 + assessment≠none 인 사전평가 87건 중 `실사 결과: 적합` 은 **7건뿐**이고
# **`평가 결과: 적합` 이 42건**이었다. 나머지는 여전히 `unknown` — 원문이 "적합"이라고
# 명시했는데 우리가 "모르겠다"로 적어 둔 상태가 3개월째 남아 있었다.
#
# ★`평가 결과` 는 periodic 의 결론 어법(`평가 결과: 지적(보완)사항 있음`)과 **접두어가
#   같다**. 그래서 `적합` 이 **바로 뒤에 붙는 형태만** 본다 — 사이에 '지적(보완)사항' 이
#   끼면 매칭되지 않는다. 전수 실측으로 검증했다: 이 앵커는 **findings 를 가진 문서
#   251건(present 146 + unknown 105) 중 0건**을 잡고, `평가 결과: 부적합` 형태도 0건이다.
#   즉 지적사항 보유 문서와 겹침이 없다(2026-08-02 에 `적합` 단순 문자열을 금지한 이유가
#   바로 이 겹침이었는데, 앵커 형태는 그 함정을 피한다).
# ★앵커 사이의 잡문자 허용 — 실측 43건이 `실사 涫 결과적합` 처럼 **글머리표(❍)가 깨진
#   글리프**로 남아 앵커를 갈랐다. 비한글·비영숫자 3자까지만 건너뛴다(`결과 적합` 만으로
#   느슨하게 잡으면 2026-08-02 에 금지한 '단순 적합' 함정으로 되돌아간다).
#   전수 실측: 이 관대형도 findings 보유 251건 중 **0건**·지적 표기 동반 **0건**이고,
#   회수는 58→96(/128)로 는다. `(?<![부불완])` 의 `완` 은 `보완적합`(=조건부 적합, 지적
#   있음) 차단용이다.
_INSPECTION_PASS_RE = re.compile(
    r"(?:실사|평가)\s*[^가-힣a-zA-Z0-9]{0,3}\s*결과\s*[:：]?\s*(?<![부불완])적합")
# present 는 헤더 근접 '분류 명사'만으로 판정하지 않는다(B3): 표지/목차 보일러플레이트
# '제조소 (일반)현황' 의 '제조' 가 .{0,80} 창에 걸려 정상 보고서가 Tier 3 로 오승격됐다.
# ① 명사 '제조' 는 '제조소' 를 제외한 형태만(제조 공정·제조위생 등 finding 본문),
# ② 명사 매칭 뒤 60자 내 판정 어휘(있음·미흡·부적합·불(적)합·일탈·N건) 동반을 요구.
# 판정 불충분이면 unknown → manual_review_required 경고 경로(과승격보다 안전).
# ★[표기 변형 2026-08-12] `보완` 을 **선택**으로 바꿨다. 종전 패턴은 `\(?보완\)?` 라
# 괄호만 선택이고 `보완` 자체는 필수여서 **"지적사항 분류 : 기타 1건"** 표기를 통째로
# 놓쳤다(실측: 사전 GMP 평가 중 `평가결과 : 보완적합` 계열). 원문이 "지적사항 N건"이라고
# 명시했는데 우리는 `unknown`(판정 불능)으로 적고 있었다 — [[부재 어휘]] 함정 그대로다.
# 안전: `none` 판정이 이 분기보다 **먼저** 실행되므로(무지적 명시 문서는 이미 반환됨)
# 넓혀도 `none` 문서는 구조적으로 영향을 못 받는다. 전수 실측으로 확인했다 —
# 현재 `none` 239건 중 넓힌 패턴에 걸리는 문서 **0건**, 바뀌는 건 `unknown` 뿐이다.
_DEFICIENCY_PRESENT_RE = re.compile(
    r"지적\s*(?:\(?\s*보완\s*\)?)?\s*사항\s*(?:\(Deficiencies\))?"
    r"(?:\s*있음"
    r"|.{0,30}?\d+\s*건"
    r"|.{0,80}(?:품질경영|시설장비|제조(?!소)|시험실|원자재|포장표시|허가관리|위탁|밸리데이션)"
    r".{0,60}?(?:있음|미흡|부적합|불\s*적?\s*합|일탈|\d+\s*건))",
    re.S,
)
# 표지·개요(제조소 현황·실사 개요)를 건너뛰고 '평가 결과 지적(보완)사항' 결론
# 섹션부터 잘라내기 위한 앵커(우선순위 순). PDF 본문은
# [표지 → 제조소 현황 → 실태조사 개요 → 실태조사 결과 → 평가 결과 지적(보완)사항(Deficiencies)]
# 순서라, 카드 인용이 표지 보일러플레이트가 아니라 실제 지적/결론을 가리키게 한다.
_DEFICIENCY_EXCERPT_PATTERNS = (
    # 1번 앵커: 실문은 "평가 결과: 지적(보완)사항" 처럼 콜론이 껴서 종전 정규식이 MISS 했다
    # (전문수집 트랙 실측 2026-07-02). `:?` 로 콜론 허용 — 무해·기존 무콜론 형태도 그대로 매칭.
    r"평가\s*결과\s*:?\s*지적\s*\(?\s*보완\s*\)?\s*사항",
    r"지적\s*\(?\s*보완\s*\)?\s*사항\s*\(\s*Deficiencies\s*\)",
    r"지적\s*\(?\s*보완\s*\)?\s*사항",
    # 마지막 앵커: 사전 GMP 평가 보고서는 "지적(보완)사항" 이라는 말 자체가 없고 결론이
    # `실사 결과: 적합` 뿐이다. **맨 뒤**에 둬야 기존 문서의 excerpt 가 바뀌지 않는다
    # (앞 앵커가 먼저 매칭되므로) — 오늘 excerpt 가 "" 인 문서만 값을 얻는다.
    # [앵커 확장 2026-08-12] `평가 결과: 적합` 도 같은 결론 어법이다(실측 42건). 위
    # 1번 앵커(`평가 결과: 지적(보완)사항`)가 먼저 매칭되므로 periodic 문서의 excerpt 는
    # 그대로다 — 순서가 회귀를 구조적으로 막는다.
    r"(?:실사|평가)\s*[^가-힣a-zA-Z0-9]{0,3}\s*결과\s*[:：]?\s*(?<![부불완])적합",
)

# ── [상세보기 결정론 승격 2026-07-02] 지적사항 표 구조 추출 ────────────────────
# nedrug 정기실태조사 PDF 는 지적(보완)사항을 5컬럼 표(분야·구분·근거법령·지적내용·비고)로
# 공개한다(전문수집 트랙 실측). PyMuPDF `find_tables()` 만으로 결정론 추출 — 새 의존성·OCR·LLM
# 전무, 환각 0. 사전 GMP 평가(수입) B형은 판정만 있어 표가 없다 → 유형 분기 후 periodic 만 시도.
_INSPECTION_TYPE_PERIODIC_RE = re.compile(
    r"정기\s*실태\s*조사|정기\s*실사"
    # [2026-08-05 전량 실측] 해외 제조소 현지실사 결과서도 **국내 정기실사와 같은 지적 표**를
    # 싣는다. 표제만 다르다("의약품 해외 제조소 현지실사 결과" / "해외제조소 실태조사(실사)
    # 결과" / "…현지실사(비대면 실사) 결과"). 그런데 이 표제가 두 정규식 어디에도 안 걸려
    # unknown 으로 떨어지고, 표 추출이 **시도조차 안 된 채**(skipped-type) 넘어갔다.
    # 실측: 게시판 626문서 중 398건(64%)이 skipped-type, 그중 지적이 있어야 할 102문서가
    # findings 66건뿐(36문서는 0건). 표본 5문서를 직접 열어 보니 지적 표가 전부 있었고
    # 기존 정규화기로 그대로 1~5행씩 추출됐다 — 파서가 아니라 **유형 게이트**가 원인이다.
    r"|해외\s*제조소\s*현지\s*실사|해외\s*제조소\s*실태\s*조사")
_INSPECTION_TYPE_PRE_MARKET_RE = re.compile(r"사전\s*GMP\s*평가|사전\s*평가\s*실태조사")
# 표 헤더 판별 토큰(모두 포함해야 지적 표로 채택) + 컬럼→필드 매핑 토큰.
_DEFICIENCY_HEADER_TOKENS = ("분야", "근거", "지적")
_DEFICIENCY_COLUMN_TOKENS = {
    "area": ("분야",),
    "severity": ("구분", "중대도"),
    "legal_basis": ("근거",),
    "summary": ("지적", "보완"),
    "followup": ("비고", "후속", "조치"),
}
_DEFICIENCY_FIELDS = ("area", "severity", "legal_basis", "summary", "followup")
_DEFICIENCY_TABLE_MAX_ROWS = 200  # 폭주 방어(정상 최대 수십 행)
# 지적 표로 채택하려면 서로 다른 열이 최소 이만큼 매핑돼야 한다(붕괴 colmap 가드).
_DEFICIENCY_MIN_COLUMNS = 3
# 지적 표 추출을 시도하는 첨부 포맷. hwp-ole(구형 바이너리)은 여전히 제외한다.
_DEFICIENCY_TABLE_FORMATS = ("pdf", "hwpx")

# ── [가림막 가드 2026-08-27 · docs/specs/GMP_지적표_추출불가_실측_2026-08-27.md] ──────
# 식약처는 일부 GMP 실사 결과 PDF 에서 **지적(보완)사항 요약·근거법령 칸의 일부를 검은
# 막대로 가려** 배포한다. 그런데 그 막대는 글자를 지운 게 아니라 **살아 있는 텍스트 위에
# 덧그린 벡터 사각형**이라 `page.get_text()` 는 막대 **아래 글자를 그대로 돌려준다**.
# 즉 파서가 원천이 의도적으로 감춘 문장을 읽어 낸다 — 추출이 안 되는 문제가 아니라
# **추출해도 되는 것인가**의 문제다.
#
# 실측(2026-08-27 · CONTROL 194문서/934행): 17문서에 가려진 단어가 있고, 그중 13문서가
# 가려진 단어를 포함한 35행을 낸다. ★그 13문서 중 **발행 브리프 카드에 도달한 것은 0건**
# 이므로 현재 실사고는 아니다. 다만 그 문서가 발행되거나 소급되는 순간 그대로 실현되는
# 잠복 결함이라 지금 막는다.
#
# ★막대 판별(조사 단계에서 검증된 기준 그대로): `fill` 이 어두운(채널 평균 < 0.25)
#   drawing 의 `re`(사각형) 항목 중 **가로 20pt·세로 5pt 이상**. 이 하한이 표 괘선·밑줄을
#   가른다(괘선은 세로 두께가 사실상 0, 밑줄은 낮다). 스펙 §3 이 실측했듯 이 문서들의
#   벡터 도형 수는 페이지당 1,000개대라, 하한 없이 모으면 표 전체가 가려진 것이 된다.
# ★단어가 "가려졌다"의 정의: 단어 rect 면적의 **절반 넘게** 한 막대와 겹칠 때.
#
# ★★★행을 통째로 버린다 — 가려진 칸만 비우지 않는다.
#   지적 한 행은 「분야·근거법령·지적사항」이 묶여 하나의 규제 진술이 된다. 가려진 칸만
#   비우면 **남은 칸이 온전한 진술처럼 읽히는** 행이 카드에 실린다: 근거법령이 가려진
#   행은 "근거 없이 지적받았다"로, 지적내용이 가려진 행은 "근거법령만 있고 지적은 없다"로
#   읽힌다. 규정에 대한 거짓 진술이 되므로 **없는 것보다 나쁘다**(위 스펙 §5 가 줄 파싱
#   산출물을 기각한 것과 같은 기준). 행을 버리면 기존 강등 경로(요약카드 유지)로 조용히
#   떨어지고, 이 트랙의 전제인 "생성 0 → 환각 0" 이 유지된다.
_REDACTION_BAR_MIN_WIDTH_PT = 20.0
_REDACTION_BAR_MIN_HEIGHT_PT = 5.0
_REDACTION_BAR_MAX_FILL_LEVEL = 0.25
_REDACTION_WORD_COVER_RATIO = 0.5

# 같은 가림을 **텍스트층에서** 하는 문서도 있다 — 지운 자리에 `0000…` 런이 들어간다
# (스펙 §4: TARGET 71건 중 10건이 이 형태). 그런 행은 `0000 0000 0000` 같은 쓰레기로
# 나오므로 같은 가드에서 함께 버린다. 이쪽은 좌표가 필요 없어 **PDF·HWPX 두 경로 공통**
# 으로 `_normalize_deficiency_table` 에 둔다.
# 판정: 공백을 걷어낸 칸이 통째로 0 넷 이상이거나(`0000`, `0000 0000 0000`), 0 이 여덟 개
# 이상 연달아 박혀 있을 때. 후자는 문장 안에 박힌 마스크를 잡되 `제0000호` 같은 정상
# 표기(0 넷)는 살린다 — 실제 법령 인용에 0 이 여덟 개 연달아 오는 표기는 없다.
_ZERO_MASK_WHOLE_RE = re.compile(r"^0{4,}$")
_ZERO_MASK_RUN_RE = re.compile(r"0{8,}")

# 의료용 고압가스 제조소는 GMP 공개 대상이지만, 경구 고형제 QA 다이제스트에서는
# 반복 노이즈가 컸다. 명시적 가스 업체/제품 단서만 Intake에서 제외한다.
# 한국어 단서는 substring, 영문 브랜드는 단어 경계(\b) 매칭 — "linde"가
# "Lindenberg Pharma" 같은 무관 업체명에 오탐하는 것을 방지.
# 단독 "수소"/"밀성산업"/"대성산업"은 제거: 전체 상호("한국수소" 등)·"가스" 토큰경계로 충분하며
# 부분 일치 시 무관 제약사 오탐 위험이 더 크다.
_MEDICAL_GAS_COMPANY_TERMS = [
    "에어퍼스트",
    "한국수소",
    "린데코리아",
    "에어프로덕츠",
    "프렉스에어",
]
_MEDICAL_GAS_COMPANY_WORD_RE = re.compile(
    r"\b(?:linde|praxair|air\s+first|air\s+products|air\s+liquide)\b"
)
# 한글 "가스"는 영문 브랜드(\b)와 동일하게 토큰경계로 매칭한다. 바 "가스" 부분문자열은
# "메가스터디제약"(메[가스]터디)·"한국가스공사 자회사 제약"(가스[공사]) 같은 무관 제약사를
# 과배제했다. "가스" 뒤에 한글이 이어지지 않을 때만(="○○산업가스" 류 접미사·단독 토큰)
# 가스 제조사로 본다 — "밀성산업가스"·"대성산업가스"는 잡고, 어중 "가스"는 흘려보낸다.
_MEDICAL_GAS_KO_COMPANY_RE = re.compile(r"가스(?![가-힣])")
_MEDICAL_GAS_CONTEXT_TERMS = [
    "의료용 고압가스", "의료용가스", "의료용 가스",
    "고압가스", "액화산소", "액화질소",
    "산소가스", "질소가스", "아산화질소", "혼합가스",
]


@dataclass
class _Cell:
    text: str = ""
    doc_id: str = ""


@dataclass
class _AttachmentParse:
    status: str
    file_format: str = ""
    text: str = ""
    deficiency: str = "unknown"
    deficiency_excerpt: str = ""   # 표지 너머 '지적(보완)사항' 결론 섹션(카드 인용용)
    bytes_downloaded: int = 0
    # [얇은 텍스트층 관측 2026-08-12] PDF 페이지 수. `pdf-ok` 가 "본문을 다 읽었다"를
    # 뜻하지 않으므로(표지만 읽힌 스캔본도 pdf-ok) 문서당 텍스트 밀도를 사후에 잴 수
    # 있도록 남긴다. 판정에는 아직 쓰지 않는다 — 실측 분포가 쌓이면 임계를 세운다.
    pages: int = 0
    error: str = ""
    # [상세보기 결정론 승격 2026-07-02] periodic PDF 지적 표 구조 추출 결과 + 관측 상태.
    deficiencies: list[dict[str, str]] = field(default_factory=list)
    deficiency_table_status: str = ""  # extracted|empty|gate-degraded|parse-fail|skipped-type


class _InspectionTableParser(HTMLParser):
    """Parse the GMP inspection result board table.

    Expected columns:
      No | 사전/사후 | 완제/원료 | 국가 | 제조소명 | 소재지 |
      실사시작일 | 실사종료일 | 실사결과(download) | 등록일
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_depth = 0
        self._cell_parts: list[str] = []
        self._cell_doc_id = ""
        self._row: list[_Cell] = []
        self.rows: list[list[_Cell]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table":
            self._in_table = True
        if not self._in_table:
            return
        if tag == "tr":
            self._in_row = True
            self._row = []
        if tag in ("td", "th") and self._in_row:
            if self._in_cell:
                self._cell_depth += 1
            else:
                self._in_cell = True
                self._cell_depth = 1
                self._cell_parts = []
                self._cell_doc_id = ""
        if self._in_cell:
            for value in attr_dict.values():
                if not value:
                    continue
                match = re.search(r"downFile\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", value)
                if match:
                    self._cell_doc_id = match.group(1).strip()

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag in ("td", "th") and self._in_cell:
            self._cell_depth -= 1
            if self._cell_depth <= 0:
                text = " ".join(part.strip() for part in self._cell_parts if part.strip()).strip()
                self._row.append(_Cell(text=text, doc_id=self._cell_doc_id))
                self._in_cell = False
        if tag == "tr" and self._in_row:
            self._in_row = False
            if self._row:
                self.rows.append(self._row)
        if tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._cell_parts.append(stripped)


def _parse_date(raw: str) -> str:
    raw = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return ""
    return ""


def _download_url(doc_id: str) -> str:
    return DOWNLOAD_URL_BASE + urllib.parse.quote(doc_id, safe="")


def _request_url(page_no: int) -> str:
    params = {
        "page": page_no,
        "limit": PAGE_SIZE,
    }
    return LIST_URL + "?" + urllib.parse.urlencode(params)


def _clean_cell_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw or "").strip()


def _is_medical_gas_gmp_noise(raw: dict[str, str]) -> bool:
    manufacturer = _clean_cell_text(raw.get("manufacturer", "")).lower()
    if manufacturer and (
        any(term in manufacturer for term in _MEDICAL_GAS_COMPANY_TERMS)
        or _MEDICAL_GAS_COMPANY_WORD_RE.search(manufacturer)
        or _MEDICAL_GAS_KO_COMPANY_RE.search(manufacturer)
    ):
        return True

    context = " ".join(
        _clean_cell_text(raw.get(key, ""))
        for key in ("manufacturer", "address", "product_type")
    ).lower()
    return any(term in context for term in _MEDICAL_GAS_CONTEXT_TERMS)


def _normalize_extracted_text(raw: str) -> str:
    text = (raw or "").replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _detect_attachment_format(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return "hwp-ole"
    return "unknown"


def _get_bytes(url: str, *, timeout: int = 30, accept: str = "*/*") -> bytes:
    return http_get_bytes(
        url,
        timeout=timeout,
        retries=HTTP_RETRIES,
        headers={"Accept": accept, "Referer": BOARD_URL},
        label="MFDS GMP inspection",
    )


def _extract_deficiency_excerpt(text: str) -> str:
    """표지·개요를 건너뛰고 '평가 결과 지적(보완)사항' 결론 섹션부터 반환(없으면 "").

    카드 W3 인용/요약이 표지(제조소명·실사목적 보일러플레이트)가 아니라 실제
    지적/결론을 가리키게 하기 위한 추출. 마커가 전혀 없으면 "" → 호출부가 전체
    본문으로 폴백한다.
    """
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return ""
    for pat in _DEFICIENCY_EXCERPT_PATTERNS:
        m = re.search(pat, compact)
        if m:
            return compact[m.start():][:MAX_ATTACHMENT_BODY_CHARS].strip()
    return ""


def _assess_deficiency(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return "unknown"
    # none 우선: _NO_DEFICIENCY_RE 는 '지적/보완 사항 없음' 앵커 형태만 매칭하므로
    # (단독 '이상 없음'은 A1 에서 제거됨) 부수적 '없음'이 실제 지적을 가리지 않는다.
    # present 우선이면 결론 '없음' 뒤 '제조소 (일반)현황' 헤더의 '제조' 가
    # _DEFICIENCY_PRESENT_RE 의 .{0,80} 창에 걸려 정상 보고서가 오승격된다(B3).
    if _NO_DEFICIENCY_RE.search(compact):
        return "none"
    if _DEFICIENCY_PRESENT_RE.search(compact):
        return "present"
    # 사전 GMP 평가(수입) 보고서의 결론 어법 — `실사 결과: 적합`. **present 판정 뒤에**
    # 둔다: 지적사항이 실재하는 문서는 위에서 이미 present 로 확정되므로, 이 분기는
    # 오늘 `unknown` 이 나오는 문서만 `none` 으로 바꾼다(회귀가 구조로 불가능하다).
    if _INSPECTION_PASS_RE.search(compact):
        return "none"
    # 종전 fallback("Deficiencies" 존재 + 어디에도 '없음' 없음 → present)은 B3 와
    # 동일한 오승격 경로(헤더만 있는 정상/영문 보고서를 Tier 3 로) — 제거.
    # 판정 근거 불충분은 unknown → manual_review_required 로 사람이 본다.
    return "unknown"


def _deficiency_table_enabled() -> bool:
    """`ENABLE_GMP_DEFICIENCY_TABLE`(기본 off, opt-in) — WL `ENABLE_WL_BODY_FULL` 동형.

    off 면 기존 플로우 완전 무변경(현행 excerpt/assessment 그대로). on 이고 periodic 이고
    표 추출 성공 시만 raw_payload["gmp_deficiencies"] = rows 기록(점진 활성).
    """
    return env_flag("ENABLE_GMP_DEFICIENCY_TABLE")


def _detect_inspection_type(text: str) -> str:
    """제목 문자열로 문서 유형 분기: periodic(국내 정기실태조사)·pre_market(수입 사전평가)·unknown.

    ★반환값의 **역할이 바뀌었다**(2026-08-12): 종전엔 "periodic 만 표 추출"이라 이 함수가
    사실상 허용목록이었고, 표제가 낯설면 `unknown` → 추출 미시도였다. 지금은 `pre_market`
    만 차단하고 나머지는 전부 시도한다(호출부 주석 참조). 즉 `periodic` 과 `unknown` 은
    동작이 같고, 구분은 **관측용**으로만 남는다 — 새 표제가 늘고 있는지 볼 수 있게.
    결정론·LLM 없음.
    """
    compact = re.sub(r"\s+", " ", text or "")
    if not compact:
        return "unknown"
    # pre_market 을 먼저 본다: 사전평가 문서에도 "정기실태조사" 문구가 참조로 섞일 수 있어
    # 사전평가 표지가 우선 판별되도록(오분류 시 표 미추출=안전 쪽).
    if _INSPECTION_TYPE_PRE_MARKET_RE.search(compact):
        return "pre_market"
    if _INSPECTION_TYPE_PERIODIC_RE.search(compact):
        return "periodic"
    return "unknown"


def _clean_deficiency_cell(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\n", " ")).strip()


def _is_zero_masked(value: str) -> bool:
    """텍스트층 가림(`0000…`)이 낀 칸이면 True. 순수 함수 — PDF·HWPX 공통 판정."""
    compact = re.sub(r"\s+", "", value or "")
    if not compact:
        return False
    return bool(_ZERO_MASK_WHOLE_RE.match(compact) or _ZERO_MASK_RUN_RE.search(compact))


def _rect_mostly_inside(outer: Any, inner: Any) -> bool:
    """`inner` 면적의 절반 넘게 `outer` 와 겹치면 True(빈/역전 rect 는 False)."""
    try:
        area = abs(inner)
        if area <= 0:
            return False
        overlap = outer & inner
        return (not overlap.is_empty) and abs(overlap) > _REDACTION_WORD_COVER_RATIO * area
    except Exception:      # noqa: BLE001 — rect 연산 붕괴는 "판정 불능"이지 "안 가려짐"이 아니다
        return True


def _pdf_redaction_bars(page: Any) -> list[Any]:
    """페이지 위에 덧그려진 가림막(어두운 벡터 사각형) rect 목록. 없으면 [].

    drawing 을 못 읽으면 [] — 그 경우 이 페이지에는 가드가 걸리지 않는다. 가림막이 있는
    문서에서 `get_drawings()` 가 통째로 실패하는 일은 실측되지 않았고, 여기서 fail-closed
    로 가면 **가림막이 없는 194문서 전부**가 영향을 받아 산출이 흔들린다(가드의 대전제가
    "안 가려진 문서는 바이트 불변"이다). 판정 불능의 fail-closed 는 좌표를 실제로 다루는
    `_pdf_redacted_row_indices` 쪽에서 진다.
    """
    try:
        drawings = page.get_drawings()
    except Exception:      # noqa: BLE001
        return []
    bars: list[Any] = []
    for drawing in drawings or ():
        if not isinstance(drawing, dict):      # 예상 밖 모양이면 조용히 건너뛴다 —
            continue                           # 여기서 터지면 멀쩡한 표까지 parse-fail 된다
        fill = drawing.get("fill")
        # ★`if not fill` 로 쓰면 안 된다 — 회색조 단일 채널 검정 `0.0` 은 falsy 라
        #   **진짜 검은 막대를 놓친다**(가드가 조용히 무력화되는 형태).
        if fill is None:                       # 테두리만 있는 도형 = 가림막이 아니다
            continue
        if isinstance(fill, (int, float)):
            channels = [float(fill)]
        else:
            try:
                channels = [float(c) for c in fill]
            except (TypeError, ValueError):
                continue
        if not channels:
            continue
        if sum(channels) / len(channels) >= _REDACTION_BAR_MAX_FILL_LEVEL:
            continue
        for item in drawing.get("items") or ():
            if not item or item[0] != "re":
                continue
            rect = item[1]
            try:
                wide_enough = rect.width >= _REDACTION_BAR_MIN_WIDTH_PT
                tall_enough = rect.height >= _REDACTION_BAR_MIN_HEIGHT_PT
            except AttributeError:
                continue
            if wide_enough and tall_enough:
                bars.append(rect)
    return bars


def _pdf_covered_word_rects(page: Any, bars: list[Any]) -> list[Any]:
    """가림막에 절반 넘게 덮인 단어들의 rect 목록. 막대가 없으면 [](추출 비용 0)."""
    if not bars:
        return []
    try:
        import fitz  # type: ignore[import-not-found]
        words = page.get_text("words")
    except Exception:      # noqa: BLE001
        return []
    covered: list[Any] = []
    for word in words or ():
        if len(word) < 4:
            continue
        rect = fitz.Rect(word[0], word[1], word[2], word[3])
        # 막대 **하나** 기준으로 잰다 — 여러 막대의 겹침을 더하면 인접 막대가 같은 단어를
        # 스칠 때 실제보다 크게 덮인 것으로 계산된다(경계 단어 오탐).
        if any(_rect_mostly_inside(bar, rect) for bar in bars):
            covered.append(rect)
    return covered


def _pdf_redacted_row_indices(
    table: Any, row_count: int, covered: list[Any],
) -> frozenset[int]:
    """가려진 단어가 걸린 표 행 인덱스 집합. 행을 특정 못 하면 **표 전체**를 반환.

    ★fail-closed 다. 가림막이 있는 페이지에서 좌표를 행에 대응시키지 못하면 그건
    "안 가려졌다"가 아니라 "모른다"이고, 모르는 채로 내보내면 가드가 없는 것과 같다.
    가림막이 **없는** 페이지는 `covered` 가 비어 있어 여기 오기 전에 빠진다.
    """
    if not covered:
        return frozenset()
    everything = frozenset(range(row_count))
    try:
        import fitz  # type: ignore[import-not-found]
        table_rows = list(table.rows)
        table_rect = fitz.Rect(table.bbox)
    except Exception:      # noqa: BLE001 — 기하 정보 없음 = 판정 불능
        return everything
    if len(table_rows) != row_count:           # extract() 와 행 수가 어긋나면 대응 불가
        return everything
    hit: set[int] = set()
    attributed: set[int] = set()
    for row_index, table_row in enumerate(table_rows):
        for cell in getattr(table_row, "cells", None) or ():
            if not cell:
                continue
            cell_rect = fitz.Rect(cell)
            for word_index, word_rect in enumerate(covered):
                if _rect_mostly_inside(cell_rect, word_rect):
                    hit.add(row_index)
                    attributed.add(word_index)
    # 완결성 검사 — 표 안에 있는데 **어느 칸에도** 안 붙은 가려진 단어가 남으면(병합 셀·
    # 좌표 누락) 어느 행이 오염됐는지 말할 수 없다 → 표 전체를 버린다.
    for word_index, word_rect in enumerate(covered):
        if word_index not in attributed and _rect_mostly_inside(table_rect, word_rect):
            return everything
    return frozenset(hit)


def _match_deficiency_header(rows: list[list[str | None]]) -> tuple[int | None, dict[str, int | None]]:
    """지적 표 헤더행 인덱스 + 컬럼→필드 인덱스 매핑 반환(없으면 (None, {})).

    헤더에 분야·근거·지적 을 모두 포함하는 행만 지적 표로 채택(다른 표=제조소 현황 등 배제).
    컬럼 매핑은 위치가 아니라 헤더 토큰으로 — '근거 법령' vs '근거법령' 같은 표기차에 견고.
    """
    for i, row in enumerate(rows):
        cells = [_clean_deficiency_cell(c) for c in row]
        joined = " ".join(cells)
        if all(tok in joined for tok in _DEFICIENCY_HEADER_TOKENS):
            colmap: dict[str, int | None] = {}
            for field_name, tokens in _DEFICIENCY_COLUMN_TOKENS.items():
                idx = None
                for ci, cell in enumerate(cells):
                    compact = cell.replace(" ", "")
                    if any(tok in compact for tok in tokens):
                        idx = ci
                        break
                colmap[field_name] = idx
            # ★[붕괴 colmap 가드 2026-08-27] 헤더 토큰 3개가 **한 셀 안에** 다 들어 있으면
            # (병합 셀 하나에 페이지 전체 텍스트가 담긴 경우) 모든 필드가 같은 열을 가리킨다.
            # 그 표를 채택하면 데이터행 한 칸이 다섯 필드에 그대로 복제돼 `분야=근거법령=
            # 지적내용='한약정책과'` 같은 **가짜 행**이 나온다(HWPX 실측: 문서당 1행씩 6건).
            # 진짜 지적 표는 열이 갈린다(area=0·severity=1·legal_basis=2·summary=3).
            # ★PDF 경로에도 함께 적용된다 — 채택 전 회귀 코퍼스 194문서/934행 전건에 대해
            # 산출 지문이 **바이트 불변**임을 실측했다(불일치 0건).
            if len({v for v in colmap.values() if v is not None}) < _DEFICIENCY_MIN_COLUMNS:
                continue
            return i, colmap
    return None, {}


def _normalize_deficiency_table(
    rows: list[list[str | None]],
    *,
    redacted_rows: frozenset[int] = frozenset(),
) -> list[dict[str, str]]:
    """`Table.extract()` 표(행=셀 리스트)를 지적사항 dict 목록으로 정규화(순수·결정론).

    헤더행·주석행(구조 컬럼 전무)·빈행·반복 헤더 제외. 각 행은 근거법령 또는 지적내용이
    비어있지 않아야 유효(품질 게이트). LLM·fetch 없음.

    `redacted_rows` = 가림막에 덮인 **원본 표 행 인덱스**(PDF 경로만 채운다 — 좌표가 있는
    쪽이 계산한다). 인덱스는 `rows` 기준이므로 이 함수는 헤더 뒤를 슬라이스하지 않고
    전체를 훑으며 건너뛴다 — 슬라이스하면 인덱스가 헤더 위치만큼 밀린다.
    HWPX 경로는 좌표가 없어 항상 비어 있지만, 텍스트층 `0000…` 가림은 아래에서 두 경로가
    함께 걸러 낸다. 두 가드 모두 **행을 통째로 버린다**(근거는 상단 상수 블록 주석).
    """
    if not rows:
        return []
    header_idx, colmap = _match_deficiency_header(rows)
    if header_idx is None:
        return []
    out: list[dict[str, str]] = []
    for row_index, row in enumerate(rows):
        if row_index <= header_idx or row_index in redacted_rows:
            continue
        rec: dict[str, str] = {}
        for field_name in _DEFICIENCY_FIELDS:
            ci = colmap.get(field_name)
            rec[field_name] = (_clean_deficiency_cell(row[ci])
                               if ci is not None and ci < len(row) else "")
        # 품질 게이트: 근거법령 또는 지적내용 둘 다 비면 주석/빈/구분줄 → 제외.
        if not (rec["legal_basis"] or rec["summary"]):
            continue
        # 페이지 걸친 반복 헤더행 방어.
        if rec["area"] == "분야" or rec["legal_basis"].replace(" ", "") == "근거법령":
            continue
        # [가림막 가드] 텍스트층 `0000…` 마스크가 **어느 칸에든** 끼면 버린다. 한 칸만
        # 마스크여도 그 행은 원천이 일부를 감춘 행이고, 남은 칸만 실으면 온전한 진술로
        # 읽힌다. 정상 칸이 통째로 0 넷 이상인 경우는 없으므로 오탐 여지가 없다.
        if any(_is_zero_masked(rec[field_name]) for field_name in _DEFICIENCY_FIELDS):
            continue
        out.append(rec)
        if len(out) >= _DEFICIENCY_TABLE_MAX_ROWS:
            break
    return out


def _extract_hwpx_deficiency_table(data: bytes) -> list[dict[str, str]]:
    """HWPX 첨부에서 지적 표를 결정론 추출. 없으면 [].

    ★PDF 와 상황이 정반대다 — HWPX 는 표가 **명시 마크업**(hp:tbl / hp:tr / hp:tc)이라
    좌표 추정도 어휘 휴리스틱도 필요 없다. 셀을 그대로 읽어 PDF 경로와 **같은 정규화기**
    (`_normalize_deficiency_table`)에 넘긴다 — 산출 모양이 갈릴 자리가 없다.

    여태 배선이 없어 hwpx 문서 16건은 `gmp_deficiency_table_status` 가 통째로 비어 있었다
    (본문 텍스트는 `_extract_hwpx_text` 로 이미 뽑고 있었으므로 첨부 자체는 읽히고 있었다).
    실측(2026-08-27 전건 16): 지적 present 7건에서 23행 회수 · 지적 none 6건에서 0행
    (오탐 0) · unknown 3건은 표는 있으나 데이터행이 전부 비어 있어 0행이 정답이다.

    ★[가림막 가드 2026-08-27] 두 가드 중 **텍스트층 `0000…` 쪽만** 이 경로에 걸린다 —
    같은 정규화기를 쓰므로 자동이다. 벡터 막대 가드는 걸리지 않고, 걸릴 필요도 없다:
    hwpx 는 표가 명시 마크업이고 우리는 셀 텍스트만 읽으므로 "글자 위에 도형을 덧그려
    가린다"는 상황 자체가 이 리더에 존재하지 않는다(PDF 는 그리기 명령이 텍스트와 같은
    페이지에 섞여 있어 발생한다). ★단, 그건 **우리가 도형을 안 읽기 때문**이지 hwpx 원문에
    도형이 없다는 뜻은 아니다 — hwpx 셀 위에 도형을 덧그린 문서가 나오면 이 경로에도
    같은 계열의 가드가 필요해진다(현재 16문서 전건에서는 미관측).
    """
    tables: list[list[list[str]]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = sorted(
                name
                for name in zf.namelist()
                if name.startswith("Contents/section") and name.endswith(".xml")
            )
            for name in names:
                try:
                    root = ET.fromstring(zf.read(name))
                except ET.ParseError:
                    continue                       # 섹션 하나가 깨져도 나머지는 살린다
                for tbl in root.iter():
                    if tbl.tag.rsplit("}", 1)[-1] != "tbl":
                        continue
                    rows: list[list[str]] = []
                    for tr in tbl:
                        if tr.tag.rsplit("}", 1)[-1] != "tr":
                            continue
                        rows.append([
                            " ".join(e.text for e in tc.iter()
                                     if e.tag.rsplit("}", 1)[-1] == "t" and e.text).strip()
                            for tc in tr if tc.tag.rsplit("}", 1)[-1] == "tc"
                        ])
                    if rows:
                        tables.append(rows)
    except (zipfile.BadZipFile, Exception):        # noqa: B014 — 첨부 붕괴는 degrade
        return []
    out: list[dict[str, str]] = []
    for rows in tables:
        out.extend(_normalize_deficiency_table(rows))
    return out


def _extract_deficiency_table(data: bytes, doc_id: str = "") -> list[dict[str, str]]:
    """PDF 바이트에서 지적사항 표를 결정론 추출(PyMuPDF find_tables). 없으면 [].

    페이지 걸친 다중 표를 누적. 개별 표/페이지 파싱 예외는 건너뛰되(부분 성공 우선),
    문서 열기 실패는 상위로 전파(호출부가 parse-fail 로 강등). OCR·LLM 없음.

    ★[가림막 가드 2026-08-27] 검은 막대에 덮인 단어가 든 행은 산출에서 제외한다. 막대는
    글자를 지운 게 아니라 위에 덧그린 벡터라 `get_text()` 가 아래를 읽어 버리기 때문이다
    (근거·기준·행 단위로 버리는 이유는 상단 상수 블록 주석).
    ★탐지는 **표가 잡힌 페이지에서만** 돈다 — 표 없는 페이지는 `get_drawings()` 호출조차
    없다. 가림막이 없는 페이지는 `covered` 가 비어 정규화기에 빈 집합이 가므로 기존 문서의
    산출은 바이트 불변이다(회귀 코퍼스 실측으로 확인: `verify_gmp_redaction_guard.py`).
    """
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return []
    out: list[dict[str, str]] = []
    guarded_rows = 0
    with fitz.open(stream=data, filetype="pdf") as doc:
        if doc.needs_pass or doc.is_encrypted:
            return []
        for page in doc:
            try:
                finder = page.find_tables()
                tables = list(finder.tables)
            except Exception:
                continue
            if not tables:
                continue
            covered = _pdf_covered_word_rects(page, _pdf_redaction_bars(page))
            for table in tables:
                try:
                    extracted = table.extract()
                except Exception:
                    continue
                redacted = _pdf_redacted_row_indices(table, len(extracted), covered)
                guarded_rows += len(redacted)
                out.extend(_normalize_deficiency_table(extracted, redacted_rows=redacted))
    # 가드가 조용히 먹으면 "표가 없는 문서"와 구분이 안 된다 — 원천이 감춘 것과 우리가
    # 못 읽은 것은 다른 값이다. status/raw_payload 는 바이트 불변으로 두고(발행 계약 유지)
    # 로그로만 남긴다. status 어휘 신설은 별건이다(GRM_SYSTEM.md §6.2).
    if guarded_rows:
        log("WARN", f"MFDS GMP 지적 표 가림막 가드 — 가려진 표 행 {guarded_rows}개 제외"
                    f"{f': {doc_id}' if doc_id else ''}")
    return out


def _pdf_page_count(data: bytes) -> int:
    """PDF 페이지 수(못 세면 0). 순수 관측용 — 실패해도 수집을 막지 않는다.

    ★`_extract_pdf_text` 에 얹지 않고 별도 함수로 둔 이유: 그 함수는 **공유 엔진**이다.
    `collect_fda_483`(2곳)·`collect_who`(3곳)가 `text, status = ...` 로 2-튜플 언팩하고
    있어서 반환값을 3-튜플로 바꾸면 그 호출부들이 런타임에 깨진다. 그런데 그 테스트들은
    이 함수를 **스텁으로 갈아끼우기 때문에** CI 는 초록인 채 프로덕션만 죽는다
    (#619/#655 와 같은 계열 — 테스트가 함수를 직접 호출하면 호출부 스코프는 미검사).
    """
    try:
        import fitz  # type: ignore[import-not-found]
        with fitz.open(stream=data, filetype="pdf") as doc:
            return int(doc.page_count)
    except Exception:      # noqa: BLE001 — 관측 실패는 수집 실패가 아니다
        return 0


def _extract_pdf_text(data: bytes, max_chars: int = MAX_ATTACHMENT_TEXT_CHARS) -> tuple[str, str]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return "", "pdf-parser-missing"
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or doc.is_encrypted:
                # C4: 잠긴 PDF 는 scan-no-text/parse-fail 로 오라벨하지 않는다 —
                # 라우팅은 동일(unknown→manual_review)이나 수동 확인 메시지의
                # 진단이 '스캔본'이 아니라 '암호화'를 가리키게 정정.
                # (owner-pw 만 걸린 열람 가능 PDF 는 fitz 가 자동 해제해
                # 둘 다 False — 본문 추출 경로 유지.)
                return "", "pdf-encrypted"
            text = "\n".join(page.get_text("text") for page in doc)
    except Exception as e:
        return "", f"pdf-parse-fail:{type(e).__name__}"
    text = _normalize_extracted_text(text)
    if not text:
        return "", "scan-no-text"
    # ★[얇은 텍스트층 관측 2026-08-12] `scan-no-text` 는 **완전히 빈** 경우만 잡는다. 그래서
    # 본문이 스캔 이미지이고 텍스트층엔 표지 몇 줄만 있는 PDF 가 `pdf-ok` 로 통과한다 —
    # 483 에서 이미 겪은 "글자가 있다 ≠ 본문이 있다"([[grm-ocr-engine-wiring-drift]] 의
    # found > shown)와 같은 구조다. 실측(08-12): findings 0건인 gmp-inspection 128건이
    # 전부 `pdf-ok` 인데 평균 517자(정상군 1,051~1,248자)였다.
    #
    # ★그런데 여기서 임계를 세워 `pdf-ok-thin` 같은 새 status 를 내보내지는 **않는다**.
    # 두 가지 이유다:
    #   ① status 문자열은 하류 계약이다 — `manual_review_required` 가
    #      `status not in ("pdf-ok","hwpx-ok")` 로 판정하므로 새 값을 내는 순간 128건이
    #      한꺼번에 수동확인 대기로 뒤집힌다(동작 변경).
    #   ② "얇다"의 임계를 실측으로 방어할 수 없다. 페이지 수를 여태 기록하지 않아
    #      문서당 밀도 분포를 모르고, 임계를 잘못 잡으면 483 에서 경고해 둔 **가장 위험한
    #      방향**(멀쩡한 글자를 OCR 로 덮어쓰기)으로 틀린다.
    # 그래서 이번에는 **판정의 재료만 남긴다** — 페이지 수를 payload 에 싣고(아래
    # `attachment_pages`), 다음 수집분의 실측 분포로 임계를 정한다. 근거 없는 임계보다
    # 근거를 모으는 게 먼저다.
    return text[:max_chars], "pdf-ok"


def _extract_hwpx_text(data: bytes) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = sorted(
                name
                for name in zf.namelist()
                if name.startswith("Contents/section") and name.endswith(".xml")
            )
            if not names:
                return "", "zip-not-hwpx"

            parts: list[str] = []
            for name in names:
                try:
                    root = ET.fromstring(zf.read(name))
                except ET.ParseError:
                    continue
                for elem in root.iter():
                    local = elem.tag.rsplit("}", 1)[-1]
                    if local == "t" and elem.text:
                        parts.append(elem.text)
            text = _normalize_extracted_text(" ".join(parts))
            if not text:
                return "", "hwpx-no-text"
            return text[:MAX_ATTACHMENT_TEXT_CHARS], "hwpx-ok"
    except zipfile.BadZipFile:
        return "", "zip-bad"
    except Exception as e:
        return "", f"hwpx-parse-fail:{type(e).__name__}"


def _parse_attachment(doc_id: str) -> _AttachmentParse:
    if not doc_id:
        return _AttachmentParse(status="missing-doc-id")

    url = _download_url(doc_id)
    try:
        time.sleep(ATTACHMENT_REQUEST_DELAY_SECONDS)
        data = _get_bytes(url, timeout=45, accept="*/*")
    except RuntimeError as e:
        return _AttachmentParse(status="download-fail", error=str(e)[:200])

    file_format = _detect_attachment_format(data)
    pages = 0
    if file_format == "pdf":
        text, status = _extract_pdf_text(data)
        pages = _pdf_page_count(data)
    elif file_format == "zip":
        text, status = _extract_hwpx_text(data)
        if status == "hwpx-ok":
            file_format = "hwpx"
    elif file_format == "hwp-ole":
        text, status = "", "hwp-skip"
    else:
        text, status = "", "unknown-format"

    deficiency = _assess_deficiency(text)
    deficiencies, table_status = _parse_deficiency_table(
        data, file_format, text, deficiency, doc_id)
    return _AttachmentParse(
        status=status,
        file_format=file_format,
        text=text,
        deficiency=deficiency,
        deficiency_excerpt=_extract_deficiency_excerpt(text),
        bytes_downloaded=len(data),
        pages=pages,
        deficiencies=deficiencies,
        deficiency_table_status=table_status,
    )


def _parse_deficiency_table(
    data: bytes, file_format: str, text: str, deficiency: str, doc_id: str,
) -> tuple[list[dict[str, str]], str]:
    """지적 표 추출 오케스트레이션(플래그·유형분기·품질게이트). 반환 (rows, status).

    플래그 off·비PDF·본문없음 → ("", "") 로 완전 무영향(현행 플로우 불변). periodic PDF 만
    시도하고, 추출 실패·유형 unknown·플래그 off = 조용히 요약카드 유지(degrade 우선).
    """
    if not (_deficiency_table_enabled() and text
            and file_format in _DEFICIENCY_TABLE_FORMATS):
        return [], ""
    itype = _detect_inspection_type(text)
    # ★[유형 게이트 기본값 반전 2026-08-12] 종전엔 `itype != "periodic"` 이라 **표제를 아는
    # 문서만** 표 추출을 시도했다. 그 손목록은 두 번 낡았다 — 08-05 에 해외 현지실사 3종을
    # 덧붙였는데, 실측(08-12) 결과 이번엔 국내 **"의약품 제조소 실태조사 결과"**(제목에
    # '정기'가 없는 형태) 8건이 또 `unknown` 으로 떨어져 시도조차 안 됐다.
    # **손목록으로 고친 손목록은 반드시 재발한다** → 목록을 늘리지 않고 기본을 뒤집는다.
    #
    # 안전한 이유: 표가 없으면 `_normalize_deficiency_table` 이 [] 를 돌려주고 아래에서
    # 조용히 요약카드로 degrade 한다(동작 무변경). 오탐도 구조적으로 막혀 있다 — 헤더행에
    # **분야·근거·지적 세 토큰이 전부** 있어야 지적 표로 채택하고, 각 데이터행도 근거법령
    # 또는 지적내용이 있어야 살아남는다(제조소 현황 표 등은 통과 못 한다).
    # 비용은 문서당 find_tables 호출 한 번뿐이다.
    if itype == "pre_market":
        return [], "skipped-type"  # 사전평가 B형은 판정만 있고 지적 표가 없다(설계)
    # [HWPX 배선 2026-08-27] hwpx 는 표가 **명시 마크업**이라 PDF 의 좌표 추정 문제가
    # 아예 없다 — 셀을 그대로 읽어 **같은 정규화기**에 넘기므로 산출 모양이 갈리지 않는다.
    # 여태 이 게이트가 `file_format == "pdf"` 였던 탓에 hwpx 16건은
    # `gmp_deficiency_table_status` 가 통째로 비어 있었다(본문 텍스트는 이미 뽑고 있었다).
    try:
        # PDF 경로에만 doc_id 를 넘긴다 — 가림막 가드가 발동하면 어느 문서인지 로그로
        # 남아야 한다(hwpx 경로는 좌표 가드가 없어 넘길 것이 없다).
        rows = (_extract_hwpx_deficiency_table(data) if file_format == "hwpx"
                else _extract_deficiency_table(data, doc_id))
    except Exception as e:  # noqa: BLE001 — 파싱 붕괴는 degrade(요약카드 유지)
        log("WARN", f"MFDS GMP 지적 표 추출 실패({type(e).__name__}) — 요약카드 유지: {doc_id}")
        return [], "parse-fail"
    if rows:
        return rows, "extracted"
    # 유효행 0. '지적사항 present' 인데 표가 안 잡히면(레이아웃 변이 등) 조용히 강등 + 경고.
    if deficiency == "present":
        log("WARN", "MFDS GMP 지적 표 0행(지적사항 present) — 요약카드 유지: " f"{doc_id}")
        return [], "gate-degraded"
    # ★[침묵 사각지대 가드 2026-08-25] assess=`unknown` + 표 0행은 "표가 없는 게 정상"이
    # 아니라 **판정 불능과 추출 실패가 겹친** 상태다 — 원문에 표가 실재하는데 파서가 0행을
    # 낸 문서(실측: 서울대병원)가 여태 소리 없이 empty 로 흘렀다(WARN 은 present 조합만).
    # status 는 "empty" 그대로 둔다: raw_payload 값·health 카운터(attempted/failed) 등
    # 기존 산출물 byte 불변이 목표고, 이 가드는 WARN 로그와 health 관측 항목만 더한다
    # (관측은 _tally_deficiency_table_health 가 raw_payload 의 같은 조합으로 기록).
    if deficiency == "unknown":
        log("WARN", "MFDS GMP 지적 표 0행(지적사항 unknown) — 판정 불능이라 표 실재 시 "
                    f"유실일 수 있음: {doc_id}")
    return [], "empty"  # '지적사항 없음' 정상(적합 배지) — 표 없음이 맞음


def _row_to_raw(row: list[_Cell]) -> dict[str, str] | None:
    if len(row) < 10:
        return None
    seq = _clean_cell_text(row[0].text)
    if not seq.isdigit():
        return None
    doc_id = row[8].doc_id
    if not doc_id:
        return None
    return {
        "seq": seq,
        "before_after": _clean_cell_text(row[1].text),
        "product_type": _clean_cell_text(row[2].text),
        "country": _clean_cell_text(row[3].text),
        "manufacturer": _clean_cell_text(row[4].text),
        "address": _clean_cell_text(row[5].text),
        "inspection_start": _clean_cell_text(row[6].text),
        "inspection_end": _clean_cell_text(row[7].text),
        "doc_id": doc_id,
        "registered_date": _clean_cell_text(row[9].text),
    }


def _parse_rows(html_text: str) -> list[dict[str, str]]:
    parser = _InspectionTableParser()
    parser.feed(html_text)
    rows: list[dict[str, str]] = []
    for row in parser.rows:
        raw = _row_to_raw(row)
        if raw:
            rows.append(raw)
    return rows


def _set_last_health(
    *,
    item_count: int,
    parsed_rows: int,
    parse_status_counts: dict[str, int],
    deficiency_counts: dict[str, int],
    manual_review_count: int,
    page_warnings: list[str],
    pages_seen: int,
    max_pages_reached: bool = False,
    deficiency_table: dict[str, Any] | None = None,
) -> None:
    global LAST_HEALTH
    LAST_HEALTH = {
        "item_count": item_count,
        "parsed_rows": parsed_rows,
        "parse_status_counts": dict(parse_status_counts),
        "deficiency_counts": dict(deficiency_counts),
        "manual_review_count": manual_review_count,
        "page_warnings": list(page_warnings),
        "pages_seen": pages_seen,
        "max_pages_reached": max_pages_reached,
        # [상세보기 결정론 승격 2026-07-02] 지적 표 추출 관측(collect_who WHOPIR health 동형).
        "deficiency_table": dict(deficiency_table or {}),
    }


def _body(raw: dict[str, str], attachment: _AttachmentParse,
          manual_review: bool = False) -> str:
    parts = []
    if manual_review:
        parts.append("⚠️ 첨부 자동판독 불가 — 지적사항 유무 수동 확인 필요 "
                     f"(상태: {attachment.status}). 아래 다운로드 링크에서 직접 확인할 것.")
    parts += [
        f"제조소명: {raw.get('manufacturer', '')}",
        f"소재지: {raw.get('address', '')}",
        f"국가: {raw.get('country', '')}",
        f"구분: {raw.get('before_after', '')} / {raw.get('product_type', '')}",
        f"실사일자: {raw.get('inspection_start', '')} ~ {raw.get('inspection_end', '')}",
        f"등록일: {raw.get('registered_date', '')}",
        f"실사결과 다운로드: {_download_url(raw.get('doc_id', ''))}",
        f"첨부 본문 추출 상태: {attachment.status}",
    ]
    if attachment.file_format:
        parts.append(f"첨부 포맷: {attachment.file_format}")
    if attachment.deficiency != "unknown":
        parts.append(f"지적사항 판정: {attachment.deficiency}")
    if attachment.deficiency_excerpt:
        # 표지 너머 핵심(지적/결론)을 먼저 노출 — 사람·Routine 이 보일러플레이트를
        # 건너뛰지 않아도 되게 한다(전체 원문은 아래에 그대로 보존).
        parts.extend([
            "",
            "주요 지적/결론:",
            attachment.deficiency_excerpt[:600],
        ])
    if attachment.text:
        parts.extend([
            "",
            "실사 결과/지적(보완)사항 원문:",
            attachment.text[:MAX_ATTACHMENT_BODY_CHARS],
        ])
    elif attachment.error:
        parts.append(f"첨부 본문 추출 오류: {attachment.error}")
    return "\n".join(part for part in parts if not part.endswith(": "))


def _to_item(raw: dict[str, str], api_query_url: str) -> IntakeItem | None:
    doc_id = raw.get("doc_id", "").strip()
    manufacturer = raw.get("manufacturer", "").strip()
    registered_date = _parse_date(raw.get("registered_date", ""))
    if not doc_id or not manufacturer or not registered_date:
        return None
    if _is_medical_gas_gmp_noise(raw):
        log("INFO", f"MFDS GMP 실태조사 의료용 가스 항목 제외: {manufacturer}")
        return None

    country = raw.get("country", "").strip()
    before_after = raw.get("before_after", "").strip()
    product_type = raw.get("product_type", "").strip()
    headline = f"[GMP실사] {manufacturer}"
    if country:
        headline += f" ({country})"
    detail = "·".join(part for part in [before_after, product_type] if part)
    if detail:
        headline += f" - {detail}"

    download_url = _download_url(doc_id)
    attachment = _parse_attachment(doc_id)
    qa_relevance = "Likely" if attachment.deficiency == "present" else "Possible"
    signal_tier = "Tier 3" if attachment.deficiency == "present" else "Tier 2"

    # P0 개선: 첨부를 자동판독하지 못해 지적사항 유무를 확정 못한 경우(주로 구형 .hwp/OLE,
    # 다운로드 실패, 스캔본 등)에는 침묵 강등되지 않도록 '수동확인 필요' 플래그를 남긴다.
    # 무차별 Tier 3 승격은 노이즈가 크므로 Tier 2는 유지하되, Routine이 사람 확인을 큐잉하도록 표시.
    manual_review = attachment.deficiency == "unknown" and attachment.status not in (
        "pdf-ok", "hwpx-ok",
    )

    raw_payload: dict[str, Any] = {
        "source": "nedrug CCBBD03",
        **raw,
        "download_url": download_url,
        "attachment_parse_status": attachment.status,
        "attachment_file_format": attachment.file_format,
        "attachment_bytes": attachment.bytes_downloaded,
        "attachment_pages": attachment.pages,
        "attachment_deficiency_assessment": attachment.deficiency,
        "manual_review_required": manual_review,
    }
    if attachment.error:
        raw_payload["attachment_parse_error"] = attachment.error
    if attachment.text:
        raw_payload["attachment_text"] = attachment.text
    if attachment.deficiency_excerpt:
        raw_payload["attachment_deficiency_excerpt"] = attachment.deficiency_excerpt
    # [상세보기 결정론 승격 2026-07-02] periodic 지적 표 성공 시만 구조 배열 기록(card_scaffold
    # deterministic_detail 소비). status 는 관측용(플래그 on 시도분만) — off 면 키 자체 부재.
    if attachment.deficiencies:
        raw_payload["gmp_deficiencies"] = attachment.deficiencies
    if attachment.deficiency_table_status:
        raw_payload["gmp_deficiency_table_status"] = attachment.deficiency_table_status

    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=f"gmpinspect-{doc_id}",
        date_iso=registered_date,
        headline=headline,
        official_url=BOARD_URL,
        type_or_class=TYPE_GMP_INSPECTION,
        firm=manufacturer,
        body=_body(raw, attachment, manual_review),
        api_query=api_query_url,
        qa_relevance=qa_relevance,
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_SCRAPE,
        signal_tier=signal_tier,
        raw_payload=raw_payload,
        source_url=download_url,
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
        site_country=country,
    )


def _tally_deficiency_table_health(health: dict[str, Any], item: IntakeItem) -> None:
    """수집 항목 1건의 지적 표 관측 상태를 health 누적기에 반영(결정론·부작용 없음)."""
    status = str(item.raw_payload.get("gmp_deficiency_table_status") or "")
    if status not in ("extracted", "empty", "gate-degraded", "parse-fail"):
        return  # 플래그 off / 비PDF / skipped-type 은 attempted 로 세지 않음
    health["attempted"] += 1
    if status == "extracted":
        health["extracted"] += 1
    elif status in ("gate-degraded", "parse-fail"):
        health["failed"] += 1
        health["warnings"].append(f"{status}: {item.firm}")
    elif status == "empty" and str(
            item.raw_payload.get("attachment_deficiency_assessment") or "") == "unknown":
        # ★[침묵 사각지대 가드 2026-08-25] empty + assess=unknown 조합의 health 관측.
        # 카운터(attempted/extracted/failed)는 종전 그대로 — 실패 단정이 아니라 "표가
        # 실재하면 유실일 수 있는" 후보의 가시화라, warnings 항목만 더한다.
        health["warnings"].append(f"empty-unknown: {item.firm}")


def collect_mfds_gmp_inspections(
    start: date,
    end: date,
) -> tuple[list[IntakeItem], str | None]:
    """Collect GMP inspection result metadata from nedrug's public board."""
    items: list[IntakeItem] = []
    seen_ids: set[str] = set()
    page_no = 1
    pages_fetched = 0
    total_seen_rows = 0
    parse_status_counts: dict[str, int] = {}
    deficiency_counts: dict[str, int] = {}
    manual_review_count = 0
    page_warnings: list[str] = []
    deficiency_table_health: dict[str, Any] = {
        "enabled": _deficiency_table_enabled(),
        "attempted": 0, "extracted": 0, "failed": 0, "warnings": [],
    }
    _set_last_health(
        item_count=0,
        parsed_rows=0,
        parse_status_counts=parse_status_counts,
        deficiency_counts=deficiency_counts,
        manual_review_count=0,
        page_warnings=page_warnings,
        pages_seen=0,
        deficiency_table=deficiency_table_health,
    )

    while page_no <= MAX_PAGES:
        url = _request_url(page_no)
        try:
            html_bytes = _get_bytes(
                url,
                timeout=30,
                accept="text/html,application/xhtml+xml",
            )
            html_text = html_bytes.decode("utf-8", errors="replace")
        except RuntimeError as e:
            msg = f"MFDS GMP inspection HTML page={page_no} 실패: {e}"
            if items:
                log("WARN", msg)
                page_warnings.append(msg)
                _set_last_health(
                    item_count=len(items),
                    parsed_rows=total_seen_rows,
                    parse_status_counts=parse_status_counts,
                    deficiency_counts=deficiency_counts,
                    manual_review_count=manual_review_count,
                    page_warnings=page_warnings,
                    pages_seen=pages_fetched,
                    deficiency_table=deficiency_table_health,
                )
                return items, None
            _set_last_health(
                item_count=0,
                parsed_rows=total_seen_rows,
                parse_status_counts=parse_status_counts,
                deficiency_counts=deficiency_counts,
                manual_review_count=manual_review_count,
                page_warnings=[msg],
                pages_seen=pages_fetched,
                deficiency_table=deficiency_table_health,
            )
            return [], msg

        rows = _parse_rows(html_text)
        total_seen_rows += len(rows)
        if not rows:
            msg = "MFDS GMP inspection HTML 테이블 행 미발견 — 구조 변경 가능성"
            if items or page_no > 1:
                log("WARN", msg)
                page_warnings.append(f"page={page_no}: {msg}")
                _set_last_health(
                    item_count=len(items),
                    parsed_rows=total_seen_rows,
                    parse_status_counts=parse_status_counts,
                    deficiency_counts=deficiency_counts,
                    manual_review_count=manual_review_count,
                    page_warnings=page_warnings,
                    pages_seen=pages_fetched,
                    deficiency_table=deficiency_table_health,
                )
                return items, None
            _set_last_health(
                item_count=0,
                parsed_rows=0,
                parse_status_counts=parse_status_counts,
                deficiency_counts=deficiency_counts,
                manual_review_count=manual_review_count,
                page_warnings=[msg],
                pages_seen=pages_fetched,
                deficiency_table=deficiency_table_health,
            )
            return [], msg

        pages_fetched += 1
        page_dates: list[date] = []
        for raw in rows:
            date_iso = _parse_date(raw.get("registered_date", ""))
            if date_iso:
                try:
                    page_dates.append(date.fromisoformat(date_iso))
                except ValueError:
                    pass
            if not _within_window(date_iso, start, end):
                continue
            item = _to_item(raw, url)
            if item is None or item.document_id in seen_ids:
                continue
            seen_ids.add(item.document_id)
            items.append(item)
            parse_status = str(item.raw_payload.get("attachment_parse_status") or "unknown")
            deficiency = str(item.raw_payload.get("attachment_deficiency_assessment") or "unknown")
            parse_status_counts[parse_status] = parse_status_counts.get(parse_status, 0) + 1
            deficiency_counts[deficiency] = deficiency_counts.get(deficiency, 0) + 1
            if item.raw_payload.get("manual_review_required"):
                manual_review_count += 1
            _tally_deficiency_table_health(deficiency_table_health, item)

        if page_dates and max(page_dates) < start:
            break
        page_no += 1

    if page_no > MAX_PAGES:
        msg = f"MFDS GMP inspection max_pages={MAX_PAGES} 도달 — 이후 항목 누락 가능"
        log("WARN", msg)
        page_warnings.append(msg)

    log(
        "INFO",
        "MFDS GMP inspection 수집 완료: "
        f"{len(items)}건 (parsed_rows={total_seen_rows})",
    )
    if items:
        log(
            "INFO",
            "MFDS GMP inspection attachment parse: "
            f"status={parse_status_counts} deficiency={deficiency_counts}",
        )
    if items and deficiency_table_health["enabled"]:
        log(
            "INFO",
            "MFDS GMP 지적 표: "
            f"attempted={deficiency_table_health['attempted']} "
            f"extracted={deficiency_table_health['extracted']} "
            f"failed={deficiency_table_health['failed']}",
        )
    _set_last_health(
        item_count=len(items),
        parsed_rows=total_seen_rows,
        parse_status_counts=parse_status_counts,
        deficiency_counts=deficiency_counts,
        manual_review_count=manual_review_count,
        page_warnings=page_warnings,
        pages_seen=pages_fetched,
        max_pages_reached=page_no > MAX_PAGES,
        deficiency_table=deficiency_table_health,
    )
    return items, None
