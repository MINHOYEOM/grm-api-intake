#!/usr/bin/env python3
"""GRM FDA 483 Collector — WHY-1 #3 (가장 깊은 결함 원본).

ENABLE_FDA_483=true 또는 --sources fda483 일 때 collect_intake.main() 에서 호출된다.

데이터 소스 (2026-07-17 실측 보정 — 백본 3단):
  FDA OII FOIA Electronic Reading Room. 백본 우선순위:
  1차 = 리딩룸 페이지의 서버사이드 DataTables AJAX(`/datatables/views/ajax`) — Record Date·
    Company Name·FEI Number·Record Type·State·Country·Establishment Type·Publish Date +
    `/media/<id>/download` 링크(유일하게 Country 포함).
  2차 = 구 전수 JSON `https://www.fda.gov/datatables-json/ora-foia-reading.json`.
    2026-07-02 시점 404/timeout 으로 사망 판정했으나 2026-07-17 부활 실측(runner UA 200·
    전 레코드·publish 전일까지 최신). ★2026-07-16 부터 리딩룸 HTML 페이지가 Akamai Bot
    Manager 에 막혀(비브라우저 TLS 클라이언트 → `/core/install.php` 미끼 302 → apology 404,
    UA 무관·브라우저 UA 흉내도 차단) DataTables 설정 자체를 얻을 수 없게 됐다 — 이때 이
    JSON 이 전수 백본을 대신한다. Country 컬럼만 없어 site_country 는 State 기반
    ('United States'/'') 추정으로 degrade(그 외 필드 동일).
  3차 = 정적 HTML 본문 10행(부분 수집 — health warning 으로 완전성 리스크 표면화).
  - XLSX export 는 media id 가 없어 건별 PDF dedup/source 로 부적합. media id 패턴
    https://www.fda.gov/media/<id>/download 는 안정(직접 합성·Akamai 차단 무관 실측).

수집 흐름:
  백본 fetch(3단) → Record Type == 483 필터 → Publish Date 윈도우 → 노이즈/관련성
  게이트 → media id dedup → 건별 483 PDF 결함 excerpt + (옵션) Observation 구조 추출
  (P6 _extract_pdf_text 재사용·최신 N건 cap·graceful) → IntakeItem.

설계 역할:
  - 전수성: DataTables AJAX 를 Publish Date desc 로 페이지네이션, 실패 시 전수 JSON(2차),
    그것도 실패 시 정적 HTML 10행(3차) — 3차만 부분 소스이므로 fda483-source-degraded
    warning 으로 완전성 리스크를 표면화한다.
  - 483 = Tier 3(무균 시설/신호는 Tier 3 floor). distributor-only 는 하향(§4).
  - Site Country(HOLD②): Country(해외, HTML 보강) 우선 · 공란+State(미국) → "United States" ·
    둘 다 공란 → ""(미상). State(주)는 절대 Site Country 아님(raw site_state 분리).
  - Evidence = B(excerpt 는 prose_input 만 보강, W3 인용 승격 아님 — #1+#2 와 동일 정책).
  - Observation 상세보기는 ENABLE_FDA_483_OBSERVATIONS=true 일 때만 raw_payload 에
    fda_483_observations 를 쓴다. 추출 실패/OCR 오인식/구조 이질은 키 미기록 + 요약카드 유지.
  - excerpt/소스/Observation 실패는 graceful(키 미기록·메타 카드 유지·LAST_HEALTH 경고).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from html import unescape as _html_unescape
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urljoin

import grm_findings as gf
from grm_common import _env_int, env_flag, http_get_bytes, http_get_html, http_get_json, log
from collect_intake import (
    IntakeItem,
    SOURCE_FDA_483,
    SRC_TYPE_OFFICIAL_PAGE,
    STERILE_BIO_TIER3_FLOOR,
    QA_HARD_EXCLUDE_TERMS,
    compute_relevance,
    _kw_any,
    _within_window,
    _FDAWLTableParser,
)


# 데이터 소스 (2026-07-02 — 현행 OII HTML/DataTables 표)
FDA_483_JSON_URL = "https://www.fda.gov/datatables-json/ora-foia-reading.json"
OII_READING_ROOM_URL = (
    "https://www.fda.gov/about-fda/office-inspections-and-investigations/"
    "oii-foia-electronic-reading-room"
)
FDA_MEDIA_BASE = "https://www.fda.gov"
DATATABLE_AJAX_PATH = "/datatables/views/ajax"

# Record Type — 이번 트랙은 FDA Form 483 Observation 만. EIR 은 별개 문서라 수집 대상 밖.
RECORD_TYPE_483 = "483"
RECORD_TYPE_EIR = "Establishment Inspection Report (EIR)"

TYPE_FDA_483 = "483"        # type_or_class(카드 분류) — 483
TYPE_FDA_EIR = "EIR"        # type_or_class — EIR
LANGUAGE_EN = "EN"
REGION_FDA = "USA (FDA)"

HTTP_RETRIES = 3
FDA_483_JSON_TIMEOUT = 60
FDA_483_HTML_TIMEOUT = 30
FDA_483_HTML_PAGE_LENGTH = 100
FDA_483_HTML_MAX_PAGES = 50

# HTML 표 컬럼 인덱스(probe 채록 — 9컬럼 고정 순서).
_COL_RECORD_DATE = 0
_COL_COMPANY = 1
_COL_FEI = 2
_COL_RECORD_TYPE = 3        # 셀에 /media/<id>/download href
_COL_STATE = 4
_COL_COUNTRY = 5
_COL_ESTABLISHMENT = 6
_COL_PUBLISH_DATE = 7
_MIN_COLS = 8

# WHY-1 #3: 483 PDF 결함 excerpt. P6(MFDS GMP)의 검증된 PDF 텍스트 엔진(_extract_pdf_text)을
# 재사용하고, 483 특유 관찰사항 앵커만 새로 둔다. 비용·예의: per-item timeout/delay + 최신 N건 cap.
FDA483_EXCERPT_MAX_CHARS = 1500
FDA483_EXCERPT_FETCH_TIMEOUT = 20
FDA483_EXCERPT_DELAY_SECONDS = 0.5
# [수집 사각 수리 2026-07-27] 종전 상한 40 은 **윈도우 후보 수(실측 108)의 3분의 1**이었다.
# 정렬이 publish desc 라 최신 40건 밖의 483 은 **어느 날 실행에서도** PDF 를 받지 못하고, 한 번
# Notion 에 들어가면 재시도 기회도 없다(스캐폴드는 New 행에서만 생성). 그렇게 통째로 건너뛴
# 문서가 실제로 발행됐다 — 2026-07-27 소급 복구 24건 중 **10건이 "텍스트층 정상인데 한 번도
# 시도되지 않은" 문서**였다(OCR 로 살린 14건과 별개의 원인). 상한을 올리되 무한정 늘리지 않고
# OCR 페이지 예산으로 실행시간을 닫는다.
FDA483_EXCERPT_MAX_ITEMS = _env_int("FDA483_PDF_MAX_ITEMS", 60)
# 실행 1회당 OCR 페이지 예산 — 실측 ≈2.2s/쪽. 200쪽 ≈ 7분으로 intake 전체 예산 안에 든다.
# 소진 후 문서는 OCR 없이 진행(상태 `scan-ocr-budget`)해 **왜** 비었는지가 카드까지 전달된다.
FDA483_OCR_PAGE_BUDGET = _env_int("FDA483_OCR_PAGE_BUDGET", 200)
FDA483_OBSERVATION_DETAIL_MAX_CHARS = 1200
FDA483_TEXT_CORRUPTION_RATIO_MAX = 0.08
FDA483_TEXT_MAX_CHARS = 200000   # ≈74쪽 — 현실 483 절대 초과 안 함

# [스캔 483 OCR 폴백 2026-07-27] FDA FOIA 전자열람실 483 의 대다수는 **스캔 이미지**다 —
# 실측(2026-07-26 intake, 시도 40건): 텍스트층이 온전한 483 은 5건뿐이고 나머지 35건은
# 관찰이 담긴 앞장이 전부 이미지였다. 그런데 그 스캔본에도 **마지막 장**에는 FDA 정형
# 고지문("The observations of objectionable conditions and practices …" ≈1133자)이 텍스트로
# 들어 있다. 종전 엔진은 문서 단위로 "텍스트가 하나라도 있으면 pdf-ok" 라 판정해, 이 고지문
# 한 장 때문에 스캔본이 **정상 텍스트 PDF 로 오분류**됐다. 그 결과 관찰 0건 → body_full 미보존
# → deep 델타에 source_text 없음 → 조립 시점 재추출도 불가 → 디제스트가 "원문이 제공되지
# 않아"라고 발행했다(원문은 공개돼 있는데도). 페이지 단위로 보고, 텍스트 없는 페이지만 OCR
# 한다. OCR 은 PyMuPDF 내장 경로(tesseract 바이너리 필요)만 쓰고 새 파이썬 의존성은 없다.
# [렌더 DPI 실행별 조절 2026-08-02] 기본 300 은 일상 수집의 비용/품질 균형점이고 그대로
# 둔다. 다만 잔여 회수 대상 52건의 PDF 를 직접 열어 본 결과 **원본 스캔이 대부분 ~163dpi**
# 였다(2026-08-01 실측: 10건 중 7건). 저해상도 원본에서는 렌더 DPI 를 올려 글리프를 키우는
# 것이 tesseract 인식률에 도움이 되는 경우가 있어, 1회성 회수 워크플로가 실행별로 시험할
# 수 있게 env 로 뺀다. 상한 600 — 그 이상은 렌더 시간만 늘고 163dpi 원본에 없는 정보를
# 만들어내지 못한다(업스케일은 정보를 늘리지 않는다).
FDA483_OCR_DPI = min(max(_env_int("FDA483_OCR_DPI", 300), 72), 600)
FDA483_OCR_MAX_PAGES = 30        # OCR 비용 상한(문서당) — 현실 483 은 10쪽 내외
# 483 마지막 장 정형 고지문 — 이 문구만 남은 텍스트층은 "본문 없음"과 같다(스캔 판정 신호).
_FDA483_NOTICE_ANCHOR = "observations of objectionable conditions"
# 표지/머리말을 건너뛰고 관찰사항(findings) 구간부터 잘라내기 위한 영문 앵커(우선순위 순).
_FDA483_EXCERPT_PATTERNS = (
    r"observation\s+1\b",
    r"during\s+an\s+inspection\s+of\s+your\s+(?:firm|facility|establishment)",
    r"this\s+document\s+lists\s+observations",
    r"\bobservations?\b",
    r"specifically,",
)

# excerpt·소스 관측용(dry-run 검증·운영 health). collect_who.LAST_HEALTH 패턴.
LAST_HEALTH: dict[str, Any] = {}

# 실행 1회당 OCR 페이지 예산(모듈 전역 — collect_fda_483() 진입 시 리셋). 예산 밖 문서는
# OCR 없이 진행하고 사유를 남긴다 — 조용히 빈 카드가 되지 않게.
_OCR_BUDGET: dict[str, int] = {"remaining": FDA483_OCR_PAGE_BUDGET, "used": 0}

# [OCR 엔진 부재 표면화 2026-07-30] OCR 결과를 **상태코드별로 센다**.
#
# 왜 필요한가: 종전에는 엔진이 아예 없어도 `_ocr_483_pdf_text` 가 status 문자열만 돌려주고
# 끝났다. 그 문자열은 raw_payload 에 묻히고, 워크플로는 초록으로 끝나고, health 경보에도
# 안 나왔다. 실제로 grm-findings-backfill-fetch 가 하루 3회 무인으로 31건을 빈 본문으로
# 적재하는 동안 **아무 신호도 없었다**(2026-07-30 실측). 엔진 부재는 문서 한 건의 사정이
# 아니라 **런타임 전체의 사정**이므로 실행 단위로 세어 경보 경로에 올린다.
#
# `engine_reason` 은 첫 실패 사유만 보관한다 — 전건이 같은 원인이므로 목록은 잡음이다.
_OCR_HEALTH: dict[str, Any] = {"ok": 0, "engine_unavailable": 0, "engine_reason": "",
                               "budget_skipped": 0, "empty": 0}

# 엔진 부재를 뜻하는 status 접두사 — 이 판정을 문자열 비교로 흩뿌리지 않는다(백필 경로도 쓴다).
_OCR_ENGINE_UNAVAILABLE_PREFIX = "scan-ocr-unavailable"


def is_ocr_engine_unavailable(status: str) -> bool:
    """이 status 가 "우리 쪽에 OCR 엔진이 없었다"를 뜻하는가(순수 함수).

    스캔본이라 OCR 이 필요했는데 엔진/tessdata 가 없어 시도조차 못 한 경우다. `scan-no-text`
    (OCR 비활성) · `scan-ocr-empty`(OCR 했으나 글자 0) · `scan-ocr-budget`(예산 소진)과
    구별해야 한다 — 이것만이 **환경을 고치면 되찾을 수 있는** 결손이다.
    """
    return str(status or "").startswith(_OCR_ENGINE_UNAVAILABLE_PREFIX)


def reset_ocr_health() -> None:
    """OCR 관측 카운터를 실행 시작 상태로 되돌린다(collect_fda_483·백필 진입점이 호출)."""
    _OCR_HEALTH.update({"ok": 0, "engine_unavailable": 0, "engine_reason": "",
                        "budget_skipped": 0, "empty": 0})


def ocr_health() -> dict[str, Any]:
    """이번 실행의 OCR 관측치 스냅샷 — 예산 사용량 + 상태코드별 건수 + 파생 플래그."""
    return {
        "pages_used": _OCR_BUDGET["used"],
        "budget": FDA483_OCR_PAGE_BUDGET,
        "exhausted": _OCR_BUDGET["remaining"] <= 0,
        "ok": _OCR_HEALTH["ok"],
        "engine_unavailable": _OCR_HEALTH["engine_unavailable"],
        "engine_reason": _OCR_HEALTH["engine_reason"],
        "budget_skipped": _OCR_HEALTH["budget_skipped"],
        "empty": _OCR_HEALTH["empty"],
    }


def _record_ocr_outcome(status: str) -> None:
    """OCR 한 건의 결과를 카운터에 반영한다(모든 반환 경로가 여기 한 곳을 지난다)."""
    if status == "pdf-ok-ocr":
        _OCR_HEALTH["ok"] += 1
    elif is_ocr_engine_unavailable(status):
        _OCR_HEALTH["engine_unavailable"] += 1
        if not _OCR_HEALTH["engine_reason"]:
            _OCR_HEALTH["engine_reason"] = status
    elif status == "scan-ocr-budget":
        _OCR_HEALTH["budget_skipped"] += 1
    elif status == "scan-ocr-empty":
        _OCR_HEALTH["empty"] += 1

# 직전 _fetch_html_rows 가 실제 사용한 백본(관측용 — LAST_HEALTH["backbone"] 로 표면화).
# collect_fda_483() 진입 시 "datatables" 로 리셋 — 테스트가 _fetch_html_rows 를 스텁해도
# 이전 실호출 값이 새 실행에 새지 않는다.
BACKBONE_DATATABLES = "datatables"
BACKBONE_LEGACY_JSON = "legacy-json"
BACKBONE_STATIC_HTML = "static-html"
_LAST_BACKBONE = BACKBONE_DATATABLES

_TAG_RE = re.compile(r"<[^>]+>")
_MEDIA_RE = re.compile(r"/media/(\d+)/download")
_MDY_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_DRUPAL_SETTINGS_RE = re.compile(
    r'<script[^>]+data-drupal-selector="drupal-settings-json"[^>]*>(.*?)</script>',
    re.S,
)
_OBS_RE = re.compile(r"\bOBSERVATION\s+(\d+)\b", re.I)
_WE_OBSERVED_RE = re.compile(r"\b(?:I\s*/\s*)?WE\s+OBSERVED\b", re.I)

# ── [관찰 회수 경로 2026-08-01] 아래 3종은 **오늘 0건인 문서에만** 쓰인다 ──────────
# 근거(라이브 실측 49문서 표본, 본문은 있는데 관찰 0건인 483):
#   · `OBS ERVAT ION 1`  — 스캔 텍스트층이 단어 안에 공백을 넣어 _OBS_RE 가 못 잡는다.
#   · `WE OBSERVED` 뒤가 `1. 2. 3.` 번호 목록이고 "OBSERVATION" 단어 자체가 없다(옛 양식).
#   · `WE OBSERVED` 마커가 관찰 표제 **뒤**에 있어, 그 지점에서 자르면 관찰을 통째로 버린다.
# 셋 다 정상 경로를 건드리지 않는다 — `_extract_483_observations_from_text` 가 기존 경로로
# 1건이라도 얻으면 그 결과를 그대로 반환하고 회수 경로에는 진입조차 하지 않는다.
_OBS_LOOSE_RE = re.compile(
    # 번호 앞 구분자에 `#` 를 포함한다 — 스캔 483 실측(2026-08-01, 회수 실패 171건 중 42건)이
    # `OBSERVATION #1` · `Observation #1:` 형태였고, 공백만 허용하는 `_OBS_RE` 가 전부 놓쳤다.
    r"\bO\s?B\s?S\s?E\s?R\s?V\s?A\s?T\s?I\s?O\s?N\s*[#.:\-]?\s*(\d{1,2})\b", re.I)

# [OCR 마커 변형 2026-08-01] 회수 경로 전용 "관찰 목록 시작" 마커.
# ★`_WE_OBSERVED_RE`(정상 경로·`_is_notice_only` 공용)는 **건드리지 않는다** — 그걸 넓히면
#   오늘 정상 파싱되는 문서의 컷 위치가 달라져 회귀 위험이 생긴다. 회수 경로에만 쓰는 별도
#   패턴을 둔다(품질 게이트·느슨한 앵커와 같은 격리 원칙).
# 실측 근거(회수 실패 171건, excerpt 기준): `(I) (WE) OBSERVED` 93건 · `| OBSERVED` 19건
#   (OCR 이 대문자 I 를 `|` 로 읽는다) · 평문 `WE OBSERVED` 15건. 즉 112건이 마커를 못 읽어
#   번호목록 폴백이 아예 켜지지 않았다 — 이 저장소에서 가장 큰 단일 결손 원인이었다.
# 괄호 안 대명사는 `(I)`·`(i)`·`(1)`·`(|)`·`(l)` 로 다양하게 깨진다.
_PRONOUN = r"[Ii1l|]"
_OBS_MARKER_RECOVERY_RE = re.compile(
    r"\(\s*" + _PRONOUN + r"\s*\)\s*\(\s*WE\s*\)\s*OBSERVED"      # (I) (WE) OBSERVED
    # 앞의 `(I)` 없이 `(WE) OBSERVED` 만 오는 변형 — 실측 151790
    # "DURING AN INSPECTION OF YOUR FIRM (WE) OBSERVED 1. There are no…".
    # 괄호가 `WE` 와 `OBSERVED` 사이를 끊어 아래 평문 패턴으로는 잡히지 않는다.
    r"|\(\s*WE\s*\)\s*OBSERVED"                                     # (WE) OBSERVED
    r"|" + _PRONOUN + r"\s*/\s*WE\s+OBSERVED"                       # I/WE OBSERVED
    r"|\bWE\s+OBSERVED\b"                                            # WE OBSERVED
    r"|(?:^|[\s:])" + _PRONOUN + r"\s+OBSERVED\s*[:.]"              # I OBSERVED: · | OBSERVED:
    r"|\bOBSERVED\s*:\s*(?=\S)",                                     # …OBSERVED: (최후 폴백)
    re.I,
)
# 번호 목록 앵커 — 문장/줄 경계 뒤 "N. " + 대문자(따옴표 포함). WL 파서의
# `_WL_NUMBERED_ITEM_RE` 와 같은 경계 요구다(조항번호 소수점 뒷자리 오탐 방지).
_NUMBERED_OBS_RE = re.compile(r"(?:^|(?<=[.)\:])\s|\n)\s*(\d{1,2})\.\s+(?=[A-Z\"“])")

# [닫는 괄호 번호 2026-08-01] `1)` · `1.)` 형태. 잔여 39건 중 **22건**이 이 형태였다
# (`1) Procedures designed to prevent…` · `PRODUCTION SYSTEM 1.) Blending of…`).
# ★경계 조건이 위 패턴보다 느슨하다(단어 뒤에서도 시작 허용) — 실측 119171 은
#   "PRODUCTION SYSTEM 1.) Blending…" 처럼 **소제목 바로 뒤**에 번호가 붙어, 문장부호
#   뒤만 허용하는 위 경계로는 잡히지 않는다. 대신 **닫는 괄호를 필수**로 요구해 안전을
#   확보한다 — 산문 속 숫자("within 5 days")는 괄호가 없어 애초에 매치되지 않고,
#   뒤따르는 대문자 요구가 남은 오탐을 막는다.
# ★`(b) (4)` 마스킹은 걸리지 않는다 — `4)` 앞이 여는 괄호라 `(?:^|\s)` 경계를 못 만족한다.
_PAREN_NUMBERED_OBS_RE = re.compile(r"(?:^|\s)(\d{1,2})\s*\.?\)\s+(?=[A-Z\"“])")

# 483 **양식 문구**(관찰이 아니다). 회수 경로는 앵커를 느슨하게 잡으므로 양식 보일러플레이트가
# deficiency 자리에 들어올 수 있다 — 실측 2건: "OR PLAN TO IMPLEMENT CORRECTIVE ACTION IN
# RESPONSE TO AN OBSERVATION…"(양식 안내문), "Pursuant to Section 704(b) of the Federal Food,
# Drug and Cosmetic Act…"(법령 근거문). 공개 findings 에 이런 문장이 들어가면 지금의 침묵
# (0건)보다 나쁘다.
_FORM_BOILERPLATE_RE = re.compile(
    r"OR\s+PLAN\s+TO\s+IMPLEMENT\s+CORRECTIVE\s+ACTION"
    r"|PURSUANT\s+TO\s+SECTION\s+704"
    r"|SEE\s+REVERSE\s+OF\s+THIS\s+PAGE"
    r"|THIS\s+DOCUMENT\s+LISTS\s+OBSERVATIONS\s+MADE"
    r"|DURING\s+AN\s+INSPECTION\s+OF\s+YOUR"
    r"|FORM\s+FDA\s+483"
    r"|EMPLOYEE\(S\)\s+SIGNATURE"
    r"|ANNOTATIONS?\s+TO\s+OBSERVATIONS?"
    r"|ADD\s+CONTINUATION\s+PAGE"
    r"|DEPARTMENT\s+OF\s+HEALTH\s+AND\s+HUMAN\s+SERVICES"
    r"|INSPECTIONAL\s+OBSERVATIONS?\s*$"
    r"|NAME\s+AND\s+TITLE\s+OF\s+INDIVIDUAL"
    # 양식 뒷면 안내문(실측 192341) — "To assist firms inspected in complying with the Acts
    # and regulations enforced by the Food and Drug Administration…"
    r"|TO\s+ASSIST\s+FIRMS\s+INSPECTED\s+IN\s+COMPLYING",
    re.I,
)

# 단어가 조각난 OCR 을 잡는 지표. `_text_corruption_ratio` 는 replacement/control 문자만 세기
# 때문에 "Thl ttiliv director failed to ass~re" · "pr eve ntion" 같은 **문자 단위 깨짐**을
# 0.0 으로 통과시킨다(실측). 토큰 단위로 다시 센다.
# ★기호 판정은 앞뒤 문장부호를 떼어 낸 **단어 속**에서만 한다 — 토큰 원문에 그대로 걸면
# "Consolidation:" 같은 정상 어미 콜론까지 깨짐으로 세어 멀쩡한 표제를 버린다(실측).
# `!`·`:`·`;` 를 포함하는 이유: OCR 이 글자를 이들로 바꿔 단어 안에 심는다
# ("perfonned for th:1!!!!l!!!Isolators" 실측).
_GARBLE_SYMBOL_RE = re.compile(r"[A-Za-z][~^|\\{}<>_!:;*#]|[~^|\\{}<>_!:;*#][A-Za-z]")
_ALPHA_WITH_DIGIT_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+$")
# 실측 튜닝(49문서 표본): 0.10 이면 "Thl ttiliv director failed to ass~re"(1/12=0.083)가
# 통과했다. 0.08 로 내리면 그 표제는 걸리고, 회수된 정상 표제 7건은 전부 0.0 이라 무영향이다.
FDA483_DEFICIENCY_GARBLE_MAX = 0.08
# FDA 483 페이지 하단 서명/양식 푸터 블록 시작 마커. 스캔 OCR 이 이 블록의 텍스트를 자주
# 깨뜨리고(EMPLOYEE(S)→EMPI..OYEE(S)/EMPLOYEE($), SIGNATURE→SIGNAT\JRE, FORM FDA 483→
# FORM FDA 4&3), 심지어 Observation 본문 자리로 흘려보내(본문을 통째로 대체) 서명블록이
# detail 로 들어온다(2026-07 Mixlab Obs2·5 실측 결함). 옛 정규식은 ① `EMPLOYEE\(S\)\b` 의
# 후행 `\b` 가 `)` 뒤에서 성립하지 않아 이 마커를 아예 못 잡고 ② OCR 변형에 취약했다.
# [2026-07-12 Catalent Indiana 실측 추가결함] ③ OCR 이 "OYEE" 내부까지 깨뜨려(문자 대신
# 숫자/콜론 삽입: OYEE→OY1:E) 옛 `[A-Z.]{0,4}` 갭이 문자 클래스 밖(숫자·콜론·아포스트로피·
# 백슬래시)까지는 못 건너뛰었고, 그 앞에 붙는 낱자 노이즈("I EMPi.OY1:E($) SIGJ'IAl\lRE")도
# 미흡수였다 → EMP~OY~E 사이 갭 클래스를 `[A-Z0-9.:'\\]`로 넓히고 선행 고립 I/l 을 옵션으로
# 흡수한다. ④ Observation 별 "Add Continuation Page" 연속페이지 마커도 신규 추가.
# [2026-07-12 Catalent(2번째 483) obs#8 실측 추가결함] ⑤ OCR 이 "EMPLOYEE(S)" 를 여는 괄호
# `(` 까지 통째로 삼켜(EMPLOYEE(S)→EMPt..oYEECS), 여닫는 괄호 중 여는 쪽이 아예 사라짐)
# 옛 패턴의 `\s*\([S$]\)`(여는 괄호 필수)가 못 잡았다 → 첫 EMP 마커 전체를 "EMP 로 시작해
# OY 를 거쳐 마지막 ')' 로 끝나는 임의 잡음"으로 재정의(여는 괄호 유무 무관, 갭 클래스에
# 공백·괄호류까지 포함). 대문자 EMP 고정((?-i:EMP))은 그대로 유지해 소문자 산문
# ("our employees)" 등)은 여전히 오탐하지 않는다.
# 아래는 가장 이른 푸터 마커에서 잘라내며, EMPLOYEE(S)/($) 는 소문자 산문("employees were
# observed")과 달리 반드시 대문자 EMP 로 시작하므로 오탐 없이 서명블록 시작을 잡는다.
_FDA483_FOOTER_RE = re.compile(
    r"(?:\b[IlL]\s+)?(?-i:EMP)[A-Za-z0-9.:'\\ ]{0,6}?OY[A-Za-z0-9.:'\\ ]{0,5}?E"
    r"[A-Za-z0-9.:'()$\\]{0,4}?\)"
    #   EMPLOYEE(S) / EMPLOYEE($) / EMPI..OYEE(S) / "I EMPi.OY1:E($)"(2026-07 Catalent 실측)
    #   / "EMPt..oYEECS)"(2026-07-12 Catalent 2번째 483 obs#8 실측 — 여는 괄호 소실)
    r"|(?-i:EMP)\S{0,6}?OY"                      # 게이트(render._FOOTER_GARBAGE_RE)와 동일한
    #   느슨한 EMP..OY 마커. 위 ①패턴은 닫는 괄호 `)` 로 끝나야 하는데 OCR 이 그 자리를 쉼표로
    #   깨뜨리면("EMPLOYEE(S," — 2026-07-20 193490 obs#2 실측) 못 잡았다. 게이트는 잡고 수집기는
    #   못 잡는 비대칭이 곧 "발행 직전에야 터지는 차단"이므로 수집기를 게이트 수준으로 맞춘다.
    r"|(?-i:AMENDMENT)"                          # 483 양식 하단 개정 스탬프 — 서명블록 바로 위에
    #   찍히며, EMP/SIGNATURE/DATE ISSUED 가 OCR 로 완전히 파괴돼도(“Et,40LOYE£ SIS G•.,-.n,,~
    #   oi:.1e 1ssueo” / “EJ·.tP!.OYEE{S) Sa'.:;!l.\'ATI..RE OA"E SSUED” — 둘 다 실측) 이 토큰만은
    #   살아남는 관찰이 반복됐다(2026-07-20 193490 obs#1·#4 는 기존 마커 전부 실패). 대문자 고정 —
    #   산문의 소문자 "amendment"(규정 개정 언급)는 오탐하지 않는다.
    r"|(?-i:SIGNATURE|SIGJ)"                     # [2026-07-27] 게이트에는 있는데 수집기에만
    #   없던 마커. 스캔 OCR 원문에서 서명란이 "! a Mae SIGNATURE |" 형태로 detail 끝에 남았고
    #   (fda483-193759 obs#8 실측), 수집기가 못 자른 채 게이트가 잡아 **발행 직전에 브리프
    #   전체가 차단**됐다 — 바로 아래 EMP..OY 주석이 경고한 비대칭이 다른 토큰에서 재발한 것.
    #   두 정규식의 마커 집합을 맞춰 둔다(대문자 고정 — 산문 "signature" 는 오탐하지 않는다).
    r"|,\s*Investigator\b(?!\s+[A-Z][a-z])"      # 서명블록 직함(`<이름>, Investigator`).
    #   산문 "Specifically, Investigator <이름> noted…" 는 쉼표도 앞에 오므로 쉼표만으로는
    #   못 가른다 — 결정적 차이는 **뒤에 사람 이름이 오느냐**다. 게이트와 같은 조건.
    r"|\bSEE\s+REVERSE\b"
    r"|\bFORM\s+FDA\s*4"                         # FORM FDA 483 / FORM FDA 4&3
    r"|PREVIOUS[\s.]*EDITION"
    r"|\bINSPECTIONAL\s+OBSERVATIONS?\b"
    r"|\bDEPARTMENT\s+OF\s+HEAL(?:TH)?"
    r"|\bDATE\s+ISSUED\b"
    r"|\bPAGE\s+\d+\s+OF\s+\d+\b"
    r"|\bAdd\s+Continuation\s+Page\b",           # Observation 별 연속페이지 마커(2026-07-12 실측)
    re.I,
)
_BOILERPLATE_RE = _FDA483_FOOTER_RE  # 후방호환 별칭(옛 이름 참조 안전)
# 위 EMPLOYEE 마커가 선행 고립 I/l 을 흡수하지 못하는 잔여 경우를 대비한 안전망(가벼운 후처리).
# 실제 산문에서 문장이 고립된 단일 I/l 로 끝나는 경우는 사실상 없어(대명사 "I"는 문장 중간),
# 오탐 위험 없이 절단 후 남은 낱자 잔재만 제거한다.
_TRAILING_STRAY_LETTER_RE = re.compile(r"\s+[IlL]$")

# ── [표제 선행 잡음 2026-08-01] 관찰 표제 **앞**에 붙는 OCR/양식 파편 ──────────
# 위 `_TRAILING_STRAY_LETTER_RE` 의 앞쪽 짝. 스캔 483 을 OCR 하면 표 테두리·페이지
# 괘선·글머리 기호가 본문 첫 글자 앞에 남는다(라이브 실측 106건):
#   "| There is a failure…" · "_ |Procedures describing…" · "· • ·· The Quality Unit…"
#   "!· ; There is a lack of…" · "— Equipment and utensils…" · ")* * * Specifically,…"
# 내용은 멀쩡한데 앞 2~6자만 잡음이라, 버릴 게 아니라 **떼어내면** 된다.
#
# 여는따옴표·여는괄호는 남긴다 — 실제 표제가 인용/괄호로 시작할 수 있다.
# ★`<` 도 반드시 남긴다: FDA 마스킹 표기 `<Redacted B4>` 가 표제 첫 글자인 경우가 있어
#   (실측 153584 "…<Redacted B4> testing to the <Redacted B4> was not performed.")
#   떼면 멀쩡한 표기가 깨진다 — 첫 측정에서 실제로 밟은 오탐이다.
_LEADING_SYMBOL_NOISE_RE = re.compile(r'^[^A-Za-z0-9"\'(<]+')
# 하위항목 마커·낱자 잔재("i The quality control unit…", "b. Written procedures…").
# 관사 "A"/"a" 와 대명사 "I" 는 진짜 문장 시작일 수 있어 **제외**한다 — 소문자 낱자가
# 대문자로 시작하는 다음 낱말 앞에 홀로 선 경우만 잡는다.
_LEADING_STRAY_LETTER_RE = re.compile(r"^(?!a\b)[b-z][.)]?\s+(?=[A-Z])")
# 안전 상한 — 이보다 많이 깎이면 잡음 제거가 아니라 내용 절단이다(원본을 그대로 둔다).
FDA483_LEADING_NOISE_MAX_STRIP = 12
_DETAIL_MIN_ALPHA = 25  # 'Specifically,' 뒤 실질 내용이 이보다 적으면 detail 을 비운다
# `OBSERVATION N` 이 표제가 아니라 본문 속 상호참조인지 가리는 신호(→ _select_observation_anchors).
# 앵커 **앞** 문맥: "...Please refer to" / "see" / "per" 로 끝나면 참조다. 중간에 다른 참조가
# 끼는 실측 형태("Please refer to OBSERVATlON 2 and" — OCR 로 깨진 것은 앵커로 안 잡힌다)까지
# 흡수하도록 참조 대상 나열을 옵션으로 둔다.
_XREF_LOOKBEHIND = 60
_XREF_PREFIX_RE = re.compile(
    r"(?:refer(?:red|ring)?\s+to|see|per)\s*"
    r"(?:OBSERVAT\w*\s*\d*\s*(?:and|,|&)?\s*)*$",
    re.I,
)
_HEADING_MIN_ALPHA = 6   # 표제 뒤 첫 문장의 최소 알파벳 수(참조문 잔재 '.' 는 0)
_BAD_CHAR_RE = re.compile(r"[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]")

# \u2500\u2500 [PDF \ub9ac\uac00\ucc98 \ubcf5\uc6d0 2026-07-20] \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# 483 PDF \ub294 \uc11c\ube0c\uc14b \ud3f0\ud2b8\ub97c \uc4f0\ub294 \uacbd\uc6b0\uac00 \uc788\uc5b4 \ud14d\uc2a4\ud2b8\uce35\uc5d0\uc11c \ud569\uc790(ligature)\uac00 **\uc5c9\ub6b1\ud55c \uc720\ub2c8\ucf54\ub4dc
# \ubb38\uc790**\ub85c \ub098\uc628\ub2e4. \uc2e4\uce21(193570): "ini\u019fal receipt of the informa\u019fon" \u00b7 "wri\u01a9en procedures" \u00b7
# "Speci\ufb01cally" \u00b7 "iden\u019f\ufb01ed". U+019F=ti \u00b7 U+01A9=tt \u00b7 U+FB01=fi.
# \uc804\ubd80 **\uc815\uc0c1 \uc720\ub2c8\ucf54\ub4dc \ubb38\uc790**\ub77c `_text_corruption_ratio`(replacement/control \ubb38\uc790 \uae30\ubc18)\uc5d0
# \uac78\ub9ac\uc9c0 \uc54a\ub294\ub2e4 \u2014 \uae68\uc9d0 \ud310\uc815\uc744 \ud1b5\uacfc\ud574 \uadf8\ub300\ub85c \ubc1c\ud589\ub420 \uc218 \uc788\ub294 \uce68\ubb35 \uacb0\ud568\uc774\ub2e4.
# NFKC \ub97c \uc4f0\uc9c0 \uc54a\ub294 \uc774\uc720: \ud45c\uc900 \ud569\uc790 \ube14\ub85d(U+FB0x)\ub9cc \ud480\uace0 U+019F/U+01A9 \ub294 \uadf8\ub300\ub85c \ub450\uba74\uc11c
# \ub2e4\ub978 \uae30\ud638\ub294 \uc608\uc0c1 \ubc16\uc73c\ub85c \ubc14\uafbc\ub2e4. \uc2e4\uce21\ub41c \ubb38\uc790\ub9cc \uba85\uc2dc \ub9e4\ud551\ud55c\ub2e4(\uacb0\uc815\ub860\u00b7\uac80\uc99d \uac00\ub2a5).
_PDF_LIGATURES = {
    "\u019f": "ti",   # \uc11c\ube0c\uc14b \ud3f0\ud2b8\uc758 ti \ud569\uc790
    "\u01a9": "tt",   # tt \ud569\uc790
    "\u0296": "",     # \uae00\uba38\ub9ac \uae30\ud638 \uc794\uc7ac
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
}
_PDF_LIGATURE_RE = re.compile("[" + "".join(_PDF_LIGATURES) + "]")


def normalize_pdf_ligatures(text: str) -> str:
    """PDF \uc11c\ube0c\uc14b \ud3f0\ud2b8\uac00 \ub0a8\uae34 \ud569\uc790 \ubb38\uc790\ub97c \uc6d0\ub798 \uc54c\ud30c\ubcb3\uc73c\ub85c \ub418\ub3cc\ub9b0\ub2e4(\uc21c\uc218\u00b7\uacb0\uc815\ub860).

    \uc218\uc9d1 \uacbd\ub85c(`_fetch_fda483_pdf_text`)\uc640 \ud30c\uc11c(`_extract_483_observations_from_text`) \uc591\ucabd\uc5d0
    \uac74\ub2e4 \u2014 \ud30c\uc11c\uc5d0\ub3c4 \uac70\ub294 \uc774\uc720\ub294 **\uc774\ubbf8 \ucee4\ubc0b\ub41c `source_text`**(deep \ub378\ud0c0)\ub97c \uc870\ub9bd \uc2dc\uc810\uc5d0 \ub2e4\uc2dc
    \ud30c\uc2f1\ud560 \ub54c\ub3c4 \ubcf5\uc6d0\ub3fc\uc57c \ud558\uae30 \ub54c\ubb38\uc774\ub2e4(\ub0a1\uc740 \ud14d\uc2a4\ud2b8\ub97c \uc7ac\uc218\uc9d1 \uc5c6\uc774 \uace0\uce58\ub294 \uc720\uc77c\ud55c \uc9c0\uc810).
    """
    if not text:
        return text
    return _PDF_LIGATURE_RE.sub(lambda m: _PDF_LIGATURES[m.group(0)], text)


def _strip(value: Any) -> str:
    """HTML 태그 제거 + 엔티티 복원 + 공백 정규화(셀/필드 값 정리, 순수 함수).

    엔티티 복원이 필수인 이유(2026-07-16 실측): 이 함수의 입력은 전부 JSON 문자열이다
    (`_json_norm_rows` 는 `.json` 필드, `_datatable_norm_rows` 는 DataTables `data` 셀).
    FDA 원본이 이미 escape 해서 내려준다 — 라이브 3079행 중 217셀에 `&amp;`(129)·
    `&#039;`(95)·`&quot;`(2) 가 실재한다. HTML 표 경로(`_html_norm_rows`)는 HTMLParser
    (convert_charrefs=True)가 이미 복원하므로 무관 — JSON 경로만 새던 구멍이다.

    순서 고정(태그 제거 → 복원 → 공백 축약):
      - 복원을 뒤에 두어야 `&lt;b&gt;` 가 태그로 오인돼 삭제되지 않고 리터럴로 남는다.
      - 공백 축약을 맨 뒤에 두어야 `&nbsp;`(\\xa0) 가 복원된 뒤 정규 공백으로 흡수된다.
    """
    text = _TAG_RE.sub(" ", str(value or ""))
    return re.sub(r"\s+", " ", _html_unescape(text)).strip()


def _observations_enabled() -> bool:
    return env_flag("ENABLE_FDA_483_OBSERVATIONS")


def _deep_enabled() -> bool:
    """[483 분석층 2026-07-02] `ENABLE_FDA_483_DEEP`(기본 off) — WL 의 `ENABLE_WL_BODY_FULL`
    동형. on 일 때만 483 PDF 전문(全文)을 raw 에 `fda483_body_full` 로 보존해 심층분석
    (deep_analysis) fan-out 입력으로 쓴다. `ENABLE_FDA_483_OBSERVATIONS`(결정론 상세)와 **독립** —
    deep off 여도 결정론 Observation 상세는 그대로 나오고, deep on 이어도 결정론 층은 불변.
    off(기본) 면 키 부재 → scaffold deep_analysis_ready=False → 골든/동작 완전 불변(활성=사람 게이트)."""
    return env_flag("ENABLE_FDA_483_DEEP")


def _ocr_enabled() -> bool:
    """[스캔 483 OCR 2026-07-27] `ENABLE_FDA_483_OCR` — **기본 on**.

    다른 483 플래그(observations/deep)와 달리 기본을 켜 둔다. 이 경로는 새 산출물을 만드는
    기능이 아니라 **이미 있어야 할 원문을 못 읽던 결손의 수리**이고, off 면 FOIA 483 의
    87%(실측)가 계속 "원문 없음"으로 발행되기 때문이다. tesseract 가 없는 환경에서는
    자동으로 무해하게 건너뛴다(상태코드 `scan-ocr-unavailable` — 없는 이유가 남는다).
    """
    return env_flag("ENABLE_FDA_483_OCR", default=True)


def _parse_mdy(raw: str) -> str:
    """MM/DD/YYYY → ISO(YYYY-MM-DD). 실패 시 ''."""
    m = _MDY_RE.search(raw or "")
    if not m:
        return ""
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return ""


def _norm_record_type(text: str) -> str:
    """텍스트 → 정규화 Record Type({483, EIR}) 또는 ''(비대상).

    수집 경로는 `483` 만 사용한다. EIR 인식은 제외 회귀 테스트/레거시 방어용으로 유지한다.
    """
    t = (text or "").strip()
    if t == RECORD_TYPE_483:
        return RECORD_TYPE_483
    low = t.lower()
    if "establishment inspection report" in low or re.search(r"\beir\b", low):
        return RECORD_TYPE_EIR
    return ""


def _pdf_url(media_id: str) -> str:
    return f"{FDA_MEDIA_BASE}/media/{media_id}/download" if media_id else ""


def _media_id_from(cell_html: str) -> str:
    """레코드의 483 PDF media id = /media/<id>/download href 의 <id>(안정 dedup·PDF 키).

    주의: JSON 의 node 'mid' 필드는 media id 와 다른 번호(신규 레코드에서 불일치) — href 만 신뢰.
    """
    m = _MEDIA_RE.search(cell_html or "")
    return m.group(1) if m else ""


# ── 정규화 행 dict 스키마(JSON·HTML 공통) ───────────────────────────────────────
#   {record_date, company, fei, record_type, media_id, state, country, publish_date}
def _json_norm_rows(data: list[Any]) -> list[dict[str, str]]:
    """전수 JSON(list[dict]) → 정규화 행. 2차 백본(_fetch_legacy_json_rows)이 사용한다."""
    rows: list[dict[str, str]] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        rt_cell = str(r.get("field_foia_record_type_1", ""))
        record_type = _norm_record_type(_strip(r.get("field_foia_record_type")) or _strip(rt_cell))
        if not record_type:
            continue
        media_id = _media_id_from(rt_cell)
        if not media_id:
            continue
        rows.append({
            "record_date": _strip(r.get("field_record_date")),
            "company": _strip(r.get("field_company_name_1")),
            "fei": _strip(r.get("field_fein")),
            "record_type": record_type,
            "media_id": media_id,
            "state": _strip(r.get("field_state_1")),
            "country": "",                 # JSON 무 — HTML map 으로 보강
            "establishment_type": _strip(r.get("field_establishment_type_1")),
            "publish_date": _strip(r.get("field_publish_date")),
        })
    return rows


def _cell(cols: list[str], i: int) -> tuple[str, str]:
    """_FDAWLTableParser 셀('text|HREF:href') → (text, href). 범위 밖이면 ('', '')."""
    if i >= len(cols):
        return "", ""
    raw = cols[i]
    if "|HREF:" in raw:
        text, href = raw.split("|HREF:", 1)
        return text.strip(), href.strip()
    return raw.strip(), ""


def _html_norm_rows(html_text: str) -> tuple[list[dict[str, str]], int]:
    """HTML 표 → 정규화 행(483 만) + 데이터행 총수(sentinel 용). Country 컬럼 보존."""
    parser = _FDAWLTableParser()
    parser.feed(html_text)
    rows: list[dict[str, str]] = []
    data_row_count = 0
    for raw_row in parser.rows:
        cols = raw_row.get("_cols", [])
        if len(cols) < _MIN_COLS:
            continue
        date_text, _ = _cell(cols, _COL_RECORD_DATE)
        if not _MDY_RE.search(date_text):     # 헤더/비데이터 행 배제
            continue
        data_row_count += 1
        rtype_text, href = _cell(cols, _COL_RECORD_TYPE)
        record_type = _norm_record_type(rtype_text)
        if record_type != RECORD_TYPE_483:
            continue
        media_id = _media_id_from(href)
        if not media_id:
            continue
        company, _ = _cell(cols, _COL_COMPANY)
        fei, _ = _cell(cols, _COL_FEI)
        state, _ = _cell(cols, _COL_STATE)
        country, _ = _cell(cols, _COL_COUNTRY)
        establishment, _ = _cell(cols, _COL_ESTABLISHMENT)
        publish, _ = _cell(cols, _COL_PUBLISH_DATE)
        rows.append({
            "record_date": date_text,
            "company": company,
            "fei": fei,
            "record_type": record_type,
            "media_id": media_id,
            "state": state,
            "country": country,
            "establishment_type": establishment,
            "publish_date": publish,
        })
    return rows, data_row_count


def _datatable_norm_rows(data_rows: list[Any]) -> list[dict[str, str]]:
    """DataTables AJAX `data` 배열 → 정규화 행(483 만)."""
    rows: list[dict[str, str]] = []
    for raw in data_rows:
        if not isinstance(raw, list) or len(raw) < _MIN_COLS:
            continue
        record_type = _norm_record_type(_strip(raw[_COL_RECORD_TYPE]))
        if record_type != RECORD_TYPE_483:
            continue
        media_id = _media_id_from(str(raw[_COL_RECORD_TYPE]))
        if not media_id:
            continue
        rows.append({
            "record_date": _strip(raw[_COL_RECORD_DATE]),
            "company": _strip(raw[_COL_COMPANY]),
            "fei": _strip(raw[_COL_FEI]),
            "record_type": record_type,
            "media_id": media_id,
            "state": _strip(raw[_COL_STATE]),
            "country": _strip(raw[_COL_COUNTRY]),
            "establishment_type": _strip(raw[_COL_ESTABLISHMENT]),
            "publish_date": _strip(raw[_COL_PUBLISH_DATE]),
        })
    return rows


def _datatable_ajax_config(html_text: str) -> dict[str, Any] | None:
    """리딩룸 HTML 의 Drupal settings 에서 서버사이드 DataTables AJAX 설정을 추출."""
    m = _DRUPAL_SETTINGS_RE.search(html_text or "")
    if not m:
        return None
    try:
        settings = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    for dt in (settings.get("datatables") or {}).values():
        if not isinstance(dt, dict):
            continue
        ajax = dt.get("ajax") or {}
        params = ajax.get("data") or {}
        if params.get("view_name") == "ora_foia_electronic_reading_room_solr":
            return {
                "url": urljoin(FDA_MEDIA_BASE, ajax.get("url") or DATATABLE_AJAX_PATH),
                "params": dict(params),
                "total_items": int(params.get("total_items") or 0),
            }
    return None


def _datatable_query(params: dict[str, Any], *, start: int, length: int, draw: int) -> dict[str, Any]:
    """DataTables 서버사이드 프로토콜 파라미터(필터=Record Type 483, Publish Date desc)."""
    out = dict(params)
    out.update({
        "draw": str(draw),
        "start": str(start),
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": str(_COL_PUBLISH_DATE),
        "order[0][dir]": "desc",
        "foia_record_type_name": RECORD_TYPE_483,
    })
    for i in range(9):
        out[f"columns[{i}][data]"] = str(i)
        out[f"columns[{i}][name]"] = ""
        out[f"columns[{i}][searchable]"] = "true"
        out[f"columns[{i}][orderable]"] = "true"
        out[f"columns[{i}][search][value]"] = ""
        out[f"columns[{i}][search][regex]"] = "false"
    return out


def _fetch_datatable_page(config: dict[str, Any], *, start: int, length: int, draw: int) -> dict[str, Any]:
    """현재 OII HTML 테이블의 서버사이드 페이지를 fetch. http_get_html 로 404도 retry/backoff."""
    params = _datatable_query(config["params"], start=start, length=length, draw=draw)
    url = config["url"] + "?" + urlencode(params)
    text = http_get_html(
        url, timeout=FDA_483_HTML_TIMEOUT, retries=HTTP_RETRIES,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": OII_READING_ROOM_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        label="FDA 483 DataTables",
    )
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _fetch_legacy_json_rows() -> tuple[list[dict[str, str]], int]:
    """2차 백본: 전수 JSON fetch → (정규화 483 행, 전체 레코드 수). 실패 시 ([], 0).

    [2026-07-17 실측] 리딩룸 HTML 이 Akamai 봇매니저에 막힌 날에도 이 JSON 은 runner UA 로
    정상 응답하며 publish_date 전일까지의 전 레코드를 담는다(부활 확인 — 모듈 docstring 참조).
    `_json_norm_rows` 는 EIR 도 통과시키므로 여기서 483 만 남긴다(1·3차 백본과 동일 계약).
    """
    try:
        data = http_get_json(FDA_483_JSON_URL, timeout=FDA_483_JSON_TIMEOUT,
                             retries=HTTP_RETRIES)
    except Exception as e:  # noqa: BLE001
        log("WARN", f"FDA 483 전수 JSON 백본 fetch 실패: {str(e)[:120]}")
        return [], 0
    if not isinstance(data, list):
        log("WARN", f"FDA 483 전수 JSON 백본 형식 이상(list 아님: {type(data).__name__})")
        return [], 0
    rows = [r for r in _json_norm_rows(data) if r["record_type"] == RECORD_TYPE_483]
    return rows, len(data)


def _fetch_html_rows(start_date: date | None = None) -> tuple[list[dict[str, str]], int, bool]:
    """백본 3단 fetch → (정규화 행, 데이터행 수, 부분 fallback 여부).

    1차 DataTables AJAX → 2차 전수 JSON → 3차 정적 HTML 10행. 부분 fallback 여부(True)는
    3차(정적 10행)와 **2차 JSON 동결(stale) 의심** — 2차 JSON 이 신선하면 전수라 degraded
    아님(WARN 로그로만 표면화). stale 판정: 윈도우 시작 이전에서 최신 publish 가 멈춘 경우
    (한번 사망했던 레거시 엔드포인트라 "살아있되 갱신 정지"가 현실 위험 — 침묵 누락 금지).
    사용 백본은 module 전역 `_LAST_BACKBONE` 에 기록(관측용).
    """
    global _LAST_BACKBONE
    static_rows: list[dict[str, str]] = []
    static_count = 0
    config = None
    try:
        html_text = http_get_html(OII_READING_ROOM_URL, timeout=FDA_483_HTML_TIMEOUT,
                                  retries=HTTP_RETRIES, label="FDA 483 HTML")
    except Exception as e:  # noqa: BLE001
        log("WARN", f"FDA 483 HTML fetch 실패: {str(e)[:120]} — 전수 JSON 백본 시도")
    else:
        static_rows, static_count = _html_norm_rows(html_text)
        config = _datatable_ajax_config(html_text)
        if not config:
            log("WARN", "FDA 483 DataTables 설정 없음(봇차단/구조변경 의심) — 전수 JSON 백본 시도")

    if config:
        rows: list[dict[str, str]] = []
        total = 0
        try:
            for page in range(FDA_483_HTML_MAX_PAGES):
                start = page * FDA_483_HTML_PAGE_LENGTH
                data = _fetch_datatable_page(
                    config, start=start, length=FDA_483_HTML_PAGE_LENGTH, draw=page + 1)
                raw_rows = data.get("data") if isinstance(data.get("data"), list) else []
                total = int(data.get("recordsFiltered") or data.get("recordsTotal") or total or 0)
                page_rows = _datatable_norm_rows(raw_rows)
                rows.extend(page_rows)
                if not raw_rows or len(raw_rows) < FDA_483_HTML_PAGE_LENGTH:
                    break
                if start_date and page_rows:
                    dates = [_parse_mdy(r.get("publish_date", "")) for r in page_rows]
                    valid = [d for d in dates if d]
                    if valid and min(valid) < start_date.isoformat():
                        break
        except Exception as e:  # noqa: BLE001
            log("WARN", f"FDA 483 DataTables 페이지 fetch 실패: {str(e)[:160]} — "
                        "전수 JSON 백본 시도")
        else:
            if rows:
                _LAST_BACKBONE = BACKBONE_DATATABLES
                return rows, total or len(rows), False
            log("WARN", "FDA 483 DataTables 응답 483 행 0(이상) — 전수 JSON 백본 시도")

    json_rows, json_total = _fetch_legacy_json_rows()
    if json_rows:
        _LAST_BACKBONE = BACKBONE_LEGACY_JSON
        # 동결(stale) 가드: 최신 publish 가 윈도우 시작 이전 → 갱신 정지 의심. 행은 그대로
        # 쓰되 degraded=True 로 fda483-source-degraded warning 을 태워 완전성 리스크를
        # 표면화한다(진짜 발행 공백 주간이면 드문 무해 경보 — 침묵 누락보다 낫다).
        newest = max((p for p in (_parse_mdy(r["publish_date"]) for r in json_rows) if p),
                     default="")
        if start_date and newest and newest < start_date.isoformat():
            log("WARN", f"FDA 483 전수 JSON 백본 동결 의심 — 최신 publish {newest} < "
                        f"윈도우 시작 {start_date.isoformat()} → 부분 수집으로 표면화")
            return json_rows, json_total, True
        log("WARN", f"FDA 483 전수 JSON 백본으로 수집(전체 {json_total}레코드 중 483 "
                    f"{len(json_rows)}행·최신 publish {newest or '?'}) — "
                    "1차 DataTables 복구 시 자동 원복")
        return json_rows, json_total, False

    log("WARN", "FDA 483 전수 JSON 백본도 실패 — 정적 HTML 10행 fallback(부분 수집)")
    _LAST_BACKBONE = BACKBONE_STATIC_HTML
    return static_rows, static_count, True


def _extract_fda483_excerpt(text: str) -> str:
    """483 PDF 평탄화 텍스트 → 영문 관찰사항(findings) 구간 excerpt(없으면 '').

    표지/보일러플레이트가 아니라 결함(관찰사항)을 카드 컨텍스트("왜")로 올리기 위한 추출.
    앵커가 하나도 없으면 ''(빈 PDF/스캔본/구조 이질 → 호출부가 키 미기록·메타 카드 유지).
    """
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return ""
    for pat in _FDA483_EXCERPT_PATTERNS:
        m = re.search(pat, compact, re.I)
        if m:
            return compact[m.start():][:FDA483_EXCERPT_MAX_CHARS].strip()
    return ""


def _fetch_fda483_pdf_text(pdf_url: str, max_chars: int = FDA483_TEXT_MAX_CHARS) -> tuple[str, str]:
    """483 PDF fetch → 평탄화 텍스트. fetch 는 grm_common.http_get_bytes(404 포함 retry/backoff).

    ★상한은 483 전용 FDA483_TEXT_MAX_CHARS(200000·≈74쪽)를 **기본**으로 쓴다 — 공유 PDF 엔진의
    기본 상한(GMP용 12000)은 8쪽+ 483 의 뒤 Observation 을 잘라, 이 라이브 경로를 쓰는 **결정론
    Observation 추출**(ENABLE_FDA_483_OBSERVATIONS)과 **deep 전문 확보** 둘 다 앞 2~3건만 남기는
    절단 버그를 냈다. PR #57 이 public `_extract_483_observations` API 만 200000 으로 고치고 이
    라이브 경로(`_fetch_fda483_pdf_text`)는 12000 그대로 두었던 것을 보완한다. excerpt 경로도 이
    함수를 쓰지만 자체적으로 앵커 뒤 1500자만 다시 잘라 무해(현실 483 은 200000 을 넘지 않아
    excerpt/카드 산출물 바이트도 불변). GMP/WHO 는 각자 `_extract_pdf_text` 를 직접 호출해 무관.
    """
    try:
        from collect_mfds_gmp_inspection import _extract_pdf_text
    except Exception as e:  # noqa: BLE001 — 임포트 실패도 graceful(키 미기록)
        return "", f"engine-missing:{type(e).__name__}"
    try:
        data = http_get_bytes(
            pdf_url, timeout=FDA483_EXCERPT_FETCH_TIMEOUT, retries=HTTP_RETRIES,
            headers={"Accept": "application/pdf"}, label="FDA 483 PDF",
        )
    except RuntimeError as e:
        return "", f"fetch-fail:{str(e)[:120]}"
    text, status = _extract_pdf_text(data, max_chars=max_chars)
    text = normalize_pdf_ligatures(text)           # [2026-07-20] 서브셋 폰트 합자 복원
    if not _needs_ocr(text):
        return text, status
    # [스캔 483 OCR 폴백 2026-07-27] 텍스트층이 아예 없거나 뒷장 정형 고지문뿐이다 —
    # 관찰은 이미지 안에 있다. 페이지 단위 OCR 로 되살린다. 실패해도 기존 산출은 보존.
    ocr_text, ocr_status = _ocr_483_pdf_text(data, max_chars=max_chars)
    if ocr_text:
        return normalize_pdf_ligatures(ocr_text), ocr_status
    # OCR 이 못 살렸다 — 원래 텍스트가 고지문뿐이거나 **아예 비었다면** 그건 "본문 없음"
    # 이므로 사유를 OCR 실패 코드로 바꿔 하류에 정확히 알린다.
    #
    # ★`not text.strip()` 조건이 빠져 있어 사유가 통째로 버려졌다(2026-08-01 실측):
    #   텍스트층이 완전히 빈 스캔본은 `_is_notice_only("")` 가 False 라 이 줄이 원래
    #   status(`scan-no-text`)를 그대로 돌려줬고, `scan-ocr-budget`(예산 소진)·
    #   `scan-ocr-empty`(OCR 했으나 글자 0)·`scan-ocr-unavailable`(엔진 없음)이 전부
    #   `scan-no-text` 한 값으로 뭉개졌다. 그 결과 **"아직 시도 안 함"과 "시도했지만
    #   안 됨"이 구분되지 않았고**, `scan-no-text` 를 "엔진을 붙여도 결과가 같다"고 보고
    #   복구 대상에서 제외하는 `backfill_483_ocr_recovery.is_ocr_unavailable_row` 가
    #   예산 때문에 밀린 문서를 영구히 못 보게 만들었다(회수 실행 실측: 사유별 집계에
    #   scan-no-text 135 · scan-ocr-budget 42 로 갈렸는데 앞쪽에 예산 건이 섞여 있었다).
    #   [[부재 어휘]] 원칙 — "우리가 못 받았다"와 "원문에 없다"는 다른 말이어야 한다.
    if _is_notice_only(text) or not (text or "").strip():
        return "", ocr_status
    return text, status


def _is_notice_only(text: str) -> bool:
    """텍스트층이 483 마지막 장 정형 고지문뿐인가(= 본문은 스캔 이미지).

    관찰 앵커(`OBSERVATION n` / `WE OBSERVED`)가 하나도 없으면서 고지문 앵커만 있는 상태.
    이 판정이 없으면 1133자짜리 고지문 한 장이 `pdf-ok` 로 통과해 스캔본이 정상 PDF 로
    오분류된다(2026-07-27 실측 21건 — 디제스트 오발행의 직접 원인).
    """
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return False
    if _OBS_RE.search(compact) or _WE_OBSERVED_RE.search(compact):
        return False
    return _FDA483_NOTICE_ANCHOR in compact.lower()


def _needs_ocr(text: str) -> bool:
    """OCR 폴백이 필요한가 — 텍스트층 부재 또는 고지문 전용(본문 이미지)."""
    if not _ocr_enabled():
        return False
    return (not (text or "").strip()) or _is_notice_only(text)


def _ocr_483_pdf_text(data: bytes, max_chars: int = FDA483_TEXT_MAX_CHARS) -> tuple[str, str]:
    """스캔 483 PDF → 페이지 단위 OCR 텍스트. 반환 (text, status).

    설계 원칙
      · **페이지 단위**로 본다 — 스캔 483 은 관찰 페이지만 이미지이고 마지막 장 고지문은
        텍스트다. 문서 단위 판정이 이 혼합 구조를 놓쳐 오분류를 냈다.
      · 텍스트가 있는 페이지는 **그대로 쓴다**(OCR 오인식을 이미 읽을 수 있는 글자에
        덧씌우지 않는다). 빈 페이지만 OCR.
      · 새 파이썬 의존성 0 — PyMuPDF 내장 OCR(`get_textpage_ocr`)만 쓴다. tesseract
        바이너리가 없으면 예외 → `scan-ocr-unavailable` 로 graceful degrade.
      · 비용 상한: 문서당 `FDA483_OCR_MAX_PAGES` 쪽까지만.

    status: `pdf-ok-ocr`(OCR 로 본문 확보) | `scan-ocr-unavailable`(엔진 없음) |
            `scan-ocr-empty`(OCR 했으나 글자 0) | `scan-ocr-budget`(예산 소진) |
            `pdf-parse-fail:<Err>`
    """
    text, status = _ocr_483_pdf_text_uncounted(data, max_chars)
    _record_ocr_outcome(status)
    return text, status


def _ocr_483_pdf_text_uncounted(data: bytes, max_chars: int) -> tuple[str, str]:
    """실제 추출 본체. 반환 경로가 여섯 갈래라 **계수는 위 래퍼가 단독으로** 맡는다 —
    반환마다 카운터를 손으로 올리면 나중에 추가되는 경로가 반드시 하나 빠진다."""
    if _OCR_BUDGET["remaining"] <= 0:
        return "", "scan-ocr-budget"
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return "", "scan-ocr-unavailable:pymupdf"
    _ensure_tessdata_prefix()
    try:
        parts: list[str] = []
        ocr_pages = 0
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or doc.is_encrypted:
                return "", "pdf-encrypted"
            for page in doc:
                native = page.get_text("text")
                if native.strip():
                    parts.append(native)
                    continue
                if ocr_pages >= FDA483_OCR_MAX_PAGES or _OCR_BUDGET["remaining"] <= 0:
                    continue
                ocr_pages += 1
                _OCR_BUDGET["remaining"] -= 1
                _OCR_BUDGET["used"] += 1
                tp = page.get_textpage_ocr(language="eng", dpi=FDA483_OCR_DPI,
                                           full=True, tessdata=_tessdata_dir())
                parts.append(page.get_text("text", textpage=tp))
    except RuntimeError as e:
        # PyMuPDF 는 tesseract 미설치/tessdata 미탐지를 RuntimeError 로 던진다.
        return "", f"scan-ocr-unavailable:{str(e)[:80]}"
    except Exception as e:  # noqa: BLE001 — 어떤 실패도 수집을 멈추지 않는다
        return "", f"pdf-parse-fail:{type(e).__name__}"
    from collect_mfds_gmp_inspection import _normalize_extracted_text
    text = _normalize_extracted_text("\n".join(parts))
    if not text.strip():
        return "", "scan-ocr-empty"
    return text[:max_chars], "pdf-ok-ocr"


def _tessdata_dir() -> str:
    """tessdata 디렉터리 절대경로(못 찾으면 ''). TESSDATA_PREFIX 우선, 없으면 표준 위치 탐색.

    PyMuPDF 는 `TESSDATA_PREFIX` 가 없으면 OCR 을 거부한다. 러너·컨테이너마다 tesseract
    버전 디렉터리(`/usr/share/tesseract-ocr/5/tessdata` 등)가 달라 워크플로에 경로를
    하드코딩하면 조용히 깨진다 — 코드가 찾는다.
    """
    env = (os.environ.get("TESSDATA_PREFIX") or "").strip()
    if env and os.path.isdir(env):
        return env
    for cand in ("/usr/share/tesseract-ocr/5/tessdata",
                 "/usr/share/tesseract-ocr/4.00/tessdata",
                 "/usr/share/tesseract-ocr/tessdata",
                 "/usr/share/tessdata",
                 "/usr/local/share/tessdata"):
        if os.path.isdir(cand):
            return cand
    return ""


def _ensure_tessdata_prefix() -> None:
    """탐색한 tessdata 경로를 `TESSDATA_PREFIX` 로 심는다(미설정일 때만)."""
    if not (os.environ.get("TESSDATA_PREFIX") or "").strip():
        found = _tessdata_dir()
        if found:
            os.environ["TESSDATA_PREFIX"] = found


def _fetch_fda483_excerpt(pdf_url: str) -> tuple[str, str]:
    """483 PDF fetch → 영문 관찰사항 excerpt. 반환 (excerpt, status).

    status: 'ok' | 'no-excerpt' | 'fetch-fail:…' | PDF 엔진 status. 실패 시 excerpt='' →
    호출부가 raw_payload 에 키를 쓰지 않고 항목은 메타 카드로 유지(graceful degrade).
    P6 PDF 엔진(_extract_pdf_text) 재사용 — fetch 는 grm_common.http_get_bytes(공용 클라이언트).
    """
    text, status = _fetch_fda483_pdf_text(pdf_url)
    if not text:
        return "", status
    excerpt = _extract_fda483_excerpt(text)
    if not excerpt:
        return "", "no-excerpt"
    return excerpt, "ok"


# [OCR 판독 잡음 차단 2026-07-27] 관찰 표제로 인정할 최소 실질. 스캔본 OCR 이 들어오면서
# 페이지 여백의 잡음("/T" · "‘T" 같은 파편)이 `OBSERVATION n` 앵커 뒤에 걸려 **관찰 1건으로
# 발행되는** 사례가 소급 복구 표본에서 실측됐다(fda483-193759 obs 6 = "/T"). 표제는 문장이지
# 기호가 아니다 — 알파벳 실질이 이 수 미만이면 관찰로 세지 않는다. 기존 텍스트층 483 의
# 정상 표제는 전부 수십 자 이상이라 무영향(골든 불변).
FDA483_DEFICIENCY_MIN_ALPHA = 12
# 483 양식에서 관찰 목록이 끝나는 지점 — 이 뒤는 시정 약속 주석이지 관찰이 아니다.
# OCR 이 공백을 흘리는 경우가 있어 단어 사이 공백을 관대하게 둔다.
_ANNOTATIONS_RE = re.compile(r"\bAnnotations?\s+to\s+Observations?\b", re.I)


def _is_legible_deficiency(deficiency: str) -> bool:
    """이 문자열이 관찰 표제로 읽히는가 — 글자 실질 최소치 충족 여부(순수 함수).

    ASCII 가 아니라 **유니코드 글자**를 센다 — 483 표제는 영문이지만, 이 판정은 언어에
    의존하면 안 된다(한국어 표제가 들어오는 경로가 생기면 조용히 전건 탈락한다).
    현실 483 표제는 전부 수십 자 문장이고, 걸러내려는 대상은 1~3자 기호 파편이다.
    """
    return sum(1 for ch in (deficiency or "") if ch.isalpha()) >= FDA483_DEFICIENCY_MIN_ALPHA


def _strip_redaction_markers(text: str) -> str:
    """483 본문의 **정상** 마스킹 표기를 지운다(깨짐 판정 전처리, 순수 함수).

    `<Redacted B4>` · `(b) (4)` 는 FDA 가 공개본에서 정보를 가린 흔적이지 OCR 깨짐이 아니다.
    지우지 않고 기호 검사를 돌리면 멀쩡한 관찰 표제가 `<`·`(` 때문에 통째로 기각된다.
    """
    out = re.sub(r"<[^<>]{0,40}>", " ", text or "")
    out = re.sub(r"\(\s*[b6]\s*\)\s*\(\s*\d\s*\)", " ", out, flags=re.I)
    return out


def _deficiency_garble(text: str) -> tuple[int, float]:
    """관찰 표제의 깨짐 신호 → (단어 속 기호 토큰 수, 한 글자 조각 비율). 순수 함수.

    `_text_corruption_ratio` 는 replacement/control 문자만 세어, 스캔 483 에서 흔한 문자
    단위 깨짐을 0.0 으로 통과시킨다(실측): "…failed to ass~re that all experimentaf data",
    "…perfonned for th:1!!!!l!!!Isolators".

    ★두 신호를 **다른 단위**로 낸다. 단어 속 기호(~ : ! | 등)·영문 속 숫자는 정상 문장에
      사실상 0이라 **절대 건수**로 봐야 한다 — 비율로 보면 긴 문장에서 희석돼(1/40=0.025)
      기각되지 않는다(실측으로 확인한 실패). 반면 한 글자 조각("Eac h batc h")은 정상
      문장에도 드물게 나오므로 비율로 본다.
    """
    cleaned = _strip_redaction_markers(text)
    tokens = [t for t in re.split(r"\s+", cleaned.strip()) if t]
    if not tokens:
        return (0, 1.0)
    symbols = 0
    fragments = 0
    for tok in tokens:
        core = tok.strip(".,;:()[]\"'“”")
        if not core:
            continue
        if _GARBLE_SYMBOL_RE.search(core) or _ALPHA_WITH_DIGIT_RE.match(core):
            symbols += 1
        elif len(core) == 1 and core.isalpha() and core.lower() not in ("a", "i"):
            fragments += 1
    return (symbols, fragments / len(tokens))


def _is_recovered_deficiency_publishable(deficiency: str) -> bool:
    """회수 경로가 만든 관찰 표제를 공개해도 되는가(순수 함수).

    ★정상 경로에는 적용하지 않는다 — 오늘 관찰이 나오는 1,545개 문서의 출력은 byte 단위로
    그대로 둔다(회귀 0을 측정이 아니라 **구조로** 보장). 이 게이트는 앵커를 느슨하게 잡는
    회수 경로에만 걸린다. 통과 조건 셋:
      ① 기존 가독성 하한(_is_legible_deficiency)
      ② 483 **양식 문구**가 아니다(_FORM_BOILERPLATE_RE)
      ③ 토큰 깨짐률이 하한 이하(_deficiency_garble_ratio)
    """
    if not _is_legible_deficiency(deficiency):
        return False
    if _FORM_BOILERPLATE_RE.search(deficiency or ""):
        return False
    symbols, fragment_ratio = _deficiency_garble(deficiency)
    return symbols == 0 and fragment_ratio <= FDA483_DEFICIENCY_GARBLE_MAX


def _text_corruption_ratio(text: str) -> float:
    """PDF 텍스트층 깨짐률. replacement/control 문자가 과하면 상세 추출은 degrade."""
    if not text:
        return 1.0
    bad = len(_BAD_CHAR_RE.findall(text))
    return bad / max(len(text), 1)


def strip_leading_observation_noise(deficiency: str) -> str:
    """관찰 표제 앞에 붙은 OCR/양식 파편을 떼어낸다(순수 함수).

    공개 API 로 둔다 — 신규 추출뿐 아니라 **이미 저장된 표제 재청소(backfill)** 에도 같은
    규칙을 써야 코드와 데이터가 갈리지 않는다(`_clean_observation_detail` 과 동일 관례).

    규칙(순서대로, 한 번만):
      ① 앞쪽 비문자 기호 제거 — 여는따옴표·여는괄호는 남긴다(진짜 표제가 그렇게 시작할 수 있다).
      ② 남은 것이 하위항목 마커/낱자면 제거("i The …", "b. Written …"). 관사 a/A·대명사 I 제외.
      ③ ②가 걷힌 뒤 다시 드러난 기호 한 겹 제거("_ |Procedures" 같은 이중 파편).

    ★상한(`FDA483_LEADING_NOISE_MAX_STRIP`) — 이보다 많이 깎이면 잡음 제거가 아니라 내용
      절단이다. 그럴 땐 **원본을 그대로 돌려준다**(의심스러우면 손대지 않는다).
    ★결과가 비면 원본을 돌려준다 — 표제를 통째로 지우는 일은 없어야 한다.
    """
    original = deficiency or ""
    text = _LEADING_SYMBOL_NOISE_RE.sub("", original)
    text = _LEADING_STRAY_LETTER_RE.sub("", text)
    text = _LEADING_SYMBOL_NOISE_RE.sub("", text)
    text = text.lstrip()
    if not text:
        return original
    if len(original) - len(text) > FDA483_LEADING_NOISE_MAX_STRIP:
        return original
    return text


def _clean_observation_chunk(chunk: str) -> str:
    """Observation 본문 chunk 에서 페이지 하단 서명/양식 푸터 블록을 제거(가장 이른 마커에서 절단)."""
    text = re.sub(r"[\r\f]+", "\n", chunk or "")
    m = _FDA483_FOOTER_RE.search(text)
    if m:
        text = text[:m.start()]
    text = _TRAILING_STRAY_LETTER_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" :-\t\n")
    return text


def _clean_observation_detail(detail: str) -> str:
    """detail 잔여 푸터 절단 + 실질내용 검증. 'Specifically,' 만 남거나 알파벳 실질 내용이
    `_DETAIL_MIN_ALPHA` 미만이면 빈 문자열을 돌려준다 — 서명블록이 본문 자리를 차지한 관찰
    (Mixlab Obs2·5)은 deficiency 만 표시하고 detail 은 생략(garbage 미노출). 순수 함수.
    (재추출뿐 아니라 이미 저장된 detail 문자열 재청소(backfill)에도 그대로 쓴다.)"""
    text = re.sub(r"\s+", " ", detail or "").strip()
    m = _FDA483_FOOTER_RE.search(text)
    if m:
        # 주의: 여기서 `.` 를 rstrip 대상에서 제외한다(구버전은 " :-.\t" 로 마침표까지 잘라
        # 정상 문장의 종결 마침표까지 날리는 부작용이 있었다 — 2026-07-12 hardening 에서 수정).
        text = _TRAILING_STRAY_LETTER_RE.sub("", text[:m.start()]).rstrip(" :-\t")
    core = re.sub(r"^\s*specifically[,:]?\s*", "", text, flags=re.I)
    if len(re.sub(r"[^A-Za-z]", "", core)) < _DETAIL_MIN_ALPHA:
        return ""
    return text.strip()


# ── [실사관 추출 2026-07-30] 서명블록 이름 파서 ──────────────────────────────────────
# 483 양식 하단 서명블록은 **이름이 앞, 직함이 뒤**(`Jose F Velez, Investigator`) 어순이고,
# 관찰 산문은 정반대로 **직함이 앞**(`Investigator Piechocki noted…`)이다 — 이 어순 차이는
# web/render.py `_FOOTER_GARBAGE_RE`(2026-07-27 오탐 수리 주석)가 이미 실측으로 문서화한
# 판별 근거이고, 여기서도 그대로 판별 축으로 쓴다(그 파일은 발행 게이트 전용이라 건드리지
# 않는다 — 이 함수는 수집 시점에 **원시** PDF 텍스트에서 별도로 이름을 뽑아낼 뿐이다).
#
# 정밀도가 최우선이다(틀린 이름 노출 > 이름 누락) — 그래서 후보는 두 겹으로 검증한다:
#   ① 이름 문법(대문자 시작 토큰 2~4개 + 직함 허용목록)을 통과해야 "후보"가 되고,
#   ② 그 후보가 서명블록 문맥(날짜 인접 또는 SIGNATURE/EMPLOYEE 류 마커 인접)에 있다는
#      교차 확증까지 통과해야 "채택"된다. ①만으로는 산문 오탐을 못 막는다(예: 보고서 산문
#      "John A Smith, Investigator" 처럼 형태만 맞는 완전 무해한 문장도 있을 수 있어 —
#      실측 fda483-193541 류 사례에서 서명블록이 아닌 본문에 이런 형태가 낄 위험을 배제 못함).
#   [2026-07-30 교정] 프로덕션 재실측으로 3건 보정. ①`Biologist`·`FDA Center Employee` 추가
#   — 실측 "Sarah E Venti, FDA Center Employee"(EMPLOYEE(S) SIGNATURE 서명자는 전원 그 실사
#   FDA 인력이므로 문서 그대로 포함하지 않으면 서명자 누락). ②슬래시 복합 직함(실측
#   "Ivis L Negron Torres, Chemist/Biologist")은 별도 항목을 추가하지 않는다 — 아래 정규식이
#   `\b`(단어 경계)로 끝나므로 `Chemist` 단독 항목이 "Chemist/" 앞에서 그대로 매칭되고
#   ("t"→"/" 는 단어/비단어 경계), 직함 나머지("/Biologist")는 애초에 버리는 값이라 이름
#   추출 결과에 영향이 없다(직함 텍스트는 저장하지 않는다 — 반환값은 이름뿐).
_INSPECTOR_TITLES = (
    "Investigator", "Consumer Safety Officer", "Microbiologist", "Biologist",
    "Chemist", "Analyst", "FDA Center Employee",
)
# 이름 토큰 1개: 대문자 시작 + [a-zA-Z'-] 연속 + 마침표 0~1개(중간이니셜 `F.`). 길이 상한은
# 느슨히 두고(백트래킹 폭주 방지) 정밀 검사는 `_valid_inspector_name`이 담당한다.
_INSPECTOR_TOKEN_RE = r"[A-Z][a-zA-Z'\-]{0,24}\.?"
# 쉼표 앞의 "대문자 시작 토큰" 연속 구간(run) — 토큰 사이는 스페이스 1개(개행/파이프 등
# 다른 구분자가 끼면 그 자리에서 매칭이 끊긴다 — "SEE REVERSE| Jose…"의 `|`처럼 서식 잡음이
# 이름을 오염시키지 못하게 막는 부수 효과가 있다).
#
# 최대 2~4토큰이 아니라 **최대 8토큰까지** 느슨히 잡는다 — 실측(fda483 서명블록)에서
# "EMPLOYEE(S) SIGNATURE Christina K Theodorou," 처럼 양식 마커 단어가 스페이스 하나 사이로
# 이름 바로 앞에 들러붙는 사례가 있다. 이름만 2~4토큰으로 좁게 잡으면 그리디 매칭이
# "SIGNATURE Christina K"(4토큰, 진짜 이름이 아님)를 먼저 집어 전체를 무효 처리해버리고,
# `finditer`는 이미 소비한 구간을 되짚어 "Christina K Theodorou"만 다시 시도하지 않는다.
# 대신 run 전체를 잡은 뒤 `_extract_483_inspectors`가 **왼쪽부터 양식 어휘 토큰을 벗겨내고**
# 남은 것만 이름 문법으로 검증한다(아래). 8은 실측 마커 접두어(많아야 1~2단어)에 여유를 둔
# 임의 상한 — 성능/폭주 방지용일 뿐 의미론적 의미는 없다.
_INSPECTOR_RUN_RE = rf"{_INSPECTOR_TOKEN_RE}(?: {_INSPECTOR_TOKEN_RE}){{0,7}}"
_INSPECTOR_TITLE_ALT = "|".join(re.escape(t) for t in _INSPECTOR_TITLES)
# [2026-07-30 실측 보정] 쉼표 **앞** 공백을 허용한다(`LePage , Investigator`). 스캔 OCR 이
# 쉼표를 이름에서 한 칸 떼어놓는 변형이 프로덕션에 38문서 존재한다(정상 쉼표 421문서 대비
# ~9% 추가 회수). 오탐 위험은 없다 — 쉼표 앞 run 은 여전히 이름 문법(2~4토큰·양식어휘 제외)
# 검증을 통과해야 하고, 교차 확증 (a)/(b)도 그대로 요구된다. `[ \t]*` 로 좁혀 개행은 넘지
# 않는다(다른 줄의 토큰이 이름으로 붙는 것을 막는다).
_INSPECTOR_CANDIDATE_RE = re.compile(
    rf"(?P<run>{_INSPECTOR_RUN_RE})[ \t]*,[ \t\r\n]*(?P<title>{_INSPECTOR_TITLE_ALT})\b"
)
# 교차 확증 (a) — 직함 뒤 60자 이내 날짜. [2026-07-30 교정 확인] 서명블록 두 번째 이후
# 서명자는 날짜가 OCR 로 깨지는 경우(`0227-2026`·`0417-2026` — 슬래시 없음)가 실측에서
# 흔하다. 여기서 패턴을 넓히지 않는다 — 넓히면 산문 오탐이 늘어난다. 이런 경우는 (b)
# (같은 블록 안 서명 마커)가 구제한다 — 한 페이지 서명블록은 마커가 한 번만 등장하고
# 서명자 여럿이 그 뒤를 잇는 구조라, 뒤쪽 서명자도 200자 룩비하인드 안에 마커가 든다.
_INSPECTOR_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
_INSPECTOR_DATE_LOOKAHEAD = 60
# 교차 확증 (b) — 이름 앞 200자 이내 서명블록 마커. 대문자 고정(re.I 미사용) — 산문의 소문자
# "signature"/"employee" 언급을 오탐하지 않는다. EMP…OY 는 `_FDA483_FOOTER_RE`와 동형(OCR
# 변형 EMPLOYEE 흡수).
_INSPECTOR_MARKER_RE = re.compile(
    r"SIGNATURE|SIGJ|EMP\S{0,6}?OY|SEE\s+REVERSE|DATE\s+ISSUED"
)
_INSPECTOR_MARKER_LOOKBEHIND = 200
_INSPECTOR_MAX_RESULTS = 6
# 중복·조각 정리 **전** 원시 후보 상한. 상한이 정리보다 먼저 걸리면 조각이 자리를 차지해
# 진짜 이름이 밀려난다(상한을 선택보다 먼저 거는 고전적 실수) — 넉넉히 두고 정리 후 자른다.
_INSPECTOR_MAX_CANDIDATES = 24
# ── OCR 신뢰도 게이트 2종 [2026-07-30 백필 실측] ──────────────────────────────────
# 문제: 구형 스캔 483 은 **FDA 가 심어둔 텍스트층 자체가 이미 OCR 산물**이라 우리 파서가
# `pdf-ok`(정상 텍스트층)로 판정하는데도 이름 철자가 틀린다. 알파벳·대문자 시작·2~4토큰
# 문법은 전부 통과하므로 기존 검증으로는 못 잡는다. 실측 사례:
#   Ameridose : ['JUetlne M. Coraon', 'Nichole B. HUrpny', 'Aahley M. Whitehurot', …]
#   Delta     : ['Brandon C. Hcitmcier', 'Brandon C. Heitrueier', 'Brandon C. Heianeier', …]
#   Immacule  : ['Damaris Y. Hernandez', 'Damaris Y. Hemandez', …]   ← rn→m 오인식
# **틀린 실명을 노출하는 것은 이 기능의 최악 결과**라, 의심스러우면 문서 전체를 버린다.
#
# 게이트①(토큰 형태) — 토큰 중간의 대문자는 OCR 대소문자 혼동의 고전적 흔적이다
#   (JUetlne·HUrpny·BiswaS). 실존 이름의 내부 대문자는 Mc/Mac/O'/D'/Le/De 접두나
#   하이픈 뒤에만 온다(McDonald·O'Brien·LePage·Wilimczyk-Macri) — 그것만 허용한다.
#   ★[2026-07-30 감사 실측] 종전 구현은 `^(?:Mc|Mac|Le|De|…)` 를 **re.I 로** 매칭해
#   접두만 맞으면 **토큰 전체를 검사에서 면제**했다 → `DemitTia J. Argiropoulos` 가
#   그대로 통과(De 로 시작한다는 이유로 내부 대문자 T 를 아무도 안 봤다). Demitria·
#   Denise·Leslie·Lauren·Macey… De/Le/La/Mc 로 시작하는 **모든** 이름이 우회하던 구멍이다.
#   수리 = 면제를 토큰 단위가 아니라 **대문자가 나온 그 자리**에 준다: 접두가 정확히
#   거기서 끝날 때만(DeJesus·McGuckin·LaBounty) 허용하고, 대소문자를 구분한다.
_INSPECTOR_CAP_PREFIXES = ("Mc", "Mac", "Le", "La", "De", "Di", "Du", "Van", "Von", "O'", "D'")
# 게이트②(문서 내 합의) — 같은 문서에서 **거의 같은 이름이 두 가지 철자로** 나오면 그
#   문서의 텍스트를 신뢰할 수 없다는 직접 증거다(같은 서명을 두 번 다르게 읽었다는 뜻).
#   한 명이라도 그런 쌍이 있으면 **그 문서의 이름 전부를 버린다** — 어느 철자가 옳은지
#   알 방법이 없기 때문. 0.82 는 실측 6개 불량 문서를 전부 잡고 정상 문서(서로 다른 사람)는
#   건드리지 않는 값이다.
_INSPECTOR_NEAR_DUP_RATIO = 0.82
# 양식 어휘 — 서명블록 주변에 흔히 나오는 대문자 단어들이 우연히 "대문자 시작 토큰"
# 문법을 통과해 이름처럼 보이는 것을 막는다(예: "OF THIS PAGE", "DATE ISSUED").
_INSPECTOR_FORM_VOCAB = {
    "SEE", "REVERSE", "PAGE", "FORM", "DATE", "ISSUED", "EMPLOYEE", "SIGNATURE",
    "THIS", "AND", "THE", "OF", "FDA", "AMENDMENT", "REPORT", "FIRM", "NAME",
    "TITLE", "ADDRESS",
}


def _inspector_token_shape_ok(token: str) -> bool:
    """토큰 형태 검사(게이트①) — 첫 글자 뒤의 대문자는 OCR 대소문자 혼동으로 본다.

    허용 예외는 실존 이름 관례뿐: 하이픈·어퍼스트로피 **바로 뒤**의 대문자
    (Wilimczyk-Macri·O'Brien), 그리고 Mc/Mac/Le/La/De/Di/Du/Van/Von/O'/D' 접두가
    **정확히 그 자리에서 끝날 때**의 대문자(DeJesus·McGuckin·LaBounty). 그 외 위치의
    대문자는 거부한다(JUetlne·HUrpny·BiswaS 실측).

    ★면제는 **토큰 전체가 아니라 대문자 한 자리**에만 준다 — 종전처럼 접두만 보고 토큰을
    통째로 면제하면 `DemitTia`(De 로 시작) 같은 OCR 오인식이 그대로 통과한다(실측 결함).
    """
    core = token.rstrip(".")
    for i, ch in enumerate(core):
        if i == 0 or not ch.isupper():
            continue
        if core[i - 1] in "-'":          # 하이픈·어퍼스트로피 뒤 대문자는 정상
            continue
        if core[:i] in _INSPECTOR_CAP_PREFIXES:   # 접두가 정확히 여기서 끝날 때만(대소문자 구분)
            continue
        return False
    return True


def _inspector_names_are_consistent(names: list[str]) -> bool:
    """문서 내 합의 검사(게이트②) — 거의 같은 이름이 두 철자로 있으면 False.

    같은 서명을 두 번 다르게 읽었다는 직접 증거이므로, 어느 쪽이 옳은지 알 수 없다.
    이 경우 호출측은 **그 문서의 이름 전부를 버린다**(정밀도 우선 계약).
    """
    from difflib import SequenceMatcher
    keys = [_inspector_key(n) for n in names]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if keys[i] == keys[j]:
                continue                                   # 정확 중복은 이미 정리됨
            if SequenceMatcher(None, keys[i], keys[j]).ratio() >= _INSPECTOR_NEAR_DUP_RATIO:
                return False
    return True


def _inspector_key(name: str) -> str:
    """중복 판정용 정규화 키 — 소문자·마침표 제거·공백 정규화.

    [2026-07-30 백필 dry-run 실측] 전자서명 레이어가 같은 사람을 여러 표기로 남긴다:
    `Barbara A. Rusin` 과 `Barbara A Rusin` 이 한 문서에 함께 나온다. 마침표를 무시하지
    않으면 같은 사람이 두 명으로 표시된다.
    """
    return re.sub(r"\s+", " ", (name or "").replace(".", "")).strip().lower()


def _dedupe_inspector_names(names: list[str]) -> list[str]:
    """부분 표기(조각)를 흡수한 최종 목록 — 등장 순서 유지.

    [2026-07-30 백필 dry-run 실측] 한 문서에서 이런 목록이 나왔다:
      ['Barbara A. Rusin', "L'Oreal D. Fowlkes", 'Sherri J. Blessman',
       'Barbara A Rusin', 'A. Rusin', 'D. Fowlkes']
    실제로는 **3명**인데 6명으로 보인다 — 마침표 변형(`Barbara A. Rusin`/`Barbara A Rusin`)
    과 **뒤쪽 조각**(`A. Rusin` ⊂ `Barbara A. Rusin`, `D. Fowlkes` ⊂ `L'Oreal D. Fowlkes`)이
    섞인 탓이다. 조각은 전자서명 필드가 이름을 짧게 다시 적으면서 생긴다.

    규칙: 정규화 토큰열이 **다른 이름의 접미(suffix)이면서 더 짧으면** 조각으로 보고 버린다
    (긴 쪽을 남긴다). 접미로 좁힌 이유 = 서명블록의 조각은 항상 "앞이 잘린" 형태이기 때문.
    `John Smith` 가 `Mary John Smith` 의 접미라 함께 있으면 병합되는 이론적 오탐이 있으나,
    한 서명블록 안에서 그 형태는 사실상 동일인이고, 3명을 6명으로 보여주는 쪽이 훨씬 나쁘다.
    """
    # ①정확 중복(마침표 변형 포함) 제거 — 첫 등장 표기를 남긴다. 추출 루프도 같은 키로
    #   거르지만, 이 함수가 단독으로도 완결되게(테스트·재사용) 여기서 다시 수행한다.
    uniq: list[str] = []
    seen: set[str] = set()
    for n in names:
        k = _inspector_key(n)
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    # ②뒤쪽 조각 흡수.
    keys = [_inspector_key(n).split(" ") for n in uniq]
    out: list[str] = []
    for i, name in enumerate(uniq):
        toks = keys[i]
        if any(j != i and len(keys[j]) > len(toks) and keys[j][-len(toks):] == toks
               for j in range(len(uniq))):
            continue                                   # 더 긴 이름의 뒤쪽 조각 — 버린다
        out.append(name)
    return out


def _valid_inspector_name(name: str) -> bool:
    """이름 문법 검증(순수 함수) — 토큰 2~4개·토큰당 ≤20자·전체 4~60자·양식 어휘 제외.

    정규식(`_INSPECTOR_NAME_RE`)이 이미 대부분 걸러내지만, 여기서 다시 명시적으로 검사해
    거부 조건을 코드로 auditable 하게 남긴다(숫자·기호 오염은 애초에 정규식 문법이 막고,
    여기서는 길이 상한·양식 어휘까지 마저 확인)."""
    tokens = name.split(" ")
    if not (2 <= len(tokens) <= 4):
        return False
    if not (4 <= len(name) <= 60):
        return False
    # [2026-07-30 실측] 첫 토큰이 홑이니셜인 이름(`I. Gaul`·`P. Cintron`·`A. Rusin`)은
    # 이름(given name)이 잘려나간 **조각**이다 — 483 서명블록은 항상 이름을 온전히 적는다.
    # 조각은 식별에 쓸모가 없을뿐더러 같은 사람을 중복 표시하게 만든다.
    if len(tokens[0].rstrip(".")) < 2:
        return False
    for tok in tokens:
        if len(tok) > 20 or not re.fullmatch(r"[A-Z][a-zA-Z'\-]*\.?", tok):
            return False
        if tok.rstrip(".").upper() in _INSPECTOR_FORM_VOCAB:
            return False
        if not _inspector_token_shape_ok(tok):   # 게이트① OCR 대소문자 혼동
            return False
    return True


def _extract_483_inspectors(text: str) -> list[str]:
    """483 PDF **원시**(청소 전) 텍스트 → 서명블록 실사관 이름 목록(등장 순서·중복 제거).

    반드시 `_clean_observation_detail`/`_FDA483_FOOTER_RE` 로 청소하기 **전** 텍스트에서
    호출해야 한다 — 그 청소 로직은 서명블록을 지우는 것이 목적이고(발행 게이트가 그 결과에
    의존) 이 함수와는 반대 방향이다. 순수 함수·부작용 없음·네트워크 없음.

    계약: 이름 문법(`_valid_inspector_name`)을 통과하고, 서명블록 문맥 교차 확증(직함 뒤
    60자 이내 날짜 **또는** 이름 앞 200자 이내 서명블록 마커) 중 하나라도 만족해야 채택한다.
    확증이 없으면 형태만 맞아도 버린다 — 관찰 산문의 "Investigator <이름> noted…"는 애초에
    어순이 반대라 이 정규식 자체가 잡지 않지만(이름이 뒤에 오면 대상 밖), 어순이 우연히
    맞아도 확증 없이는 채택하지 않는 것이 이 함수의 정밀도 원칙이다.

    최대 `_INSPECTOR_MAX_RESULTS`명. 어떤 예외도 밖으로 던지지 않는다(입력이 비정상이어도
    빈 리스트 — 이름을 지어내거나 추측 보정하지 않는다: 확신이 없으면 빈 리스트가 정답).
    """
    try:
        body = text or ""
        if not body.strip():
            return []
        out: list[str] = []
        seen: set[str] = set()
        for m in _INSPECTOR_CANDIDATE_RE.finditer(body):
            tokens = m.group("run").split(" ")
            # 왼쪽부터 양식 어휘 토큰을 벗겨낸다("SIGNATURE Christina K Theodorou" →
            # "Christina K Theodorou") — 위 run 정규식 주석 참조. 이름 한복판/끝에 낀 양식
            # 어휘는 여기서 안 걸러지고 `_valid_inspector_name`이 마저 거부한다.
            offset = 0
            while tokens and tokens[0].rstrip(".").upper() in _INSPECTOR_FORM_VOCAB:
                offset += len(tokens[0]) + 1        # 벗겨낸 토큰 + 뒤따르는 스페이스 1개
                tokens = tokens[1:]
            name = " ".join(tokens)
            if not _valid_inspector_name(name):
                continue
            name_start = m.start("run") + offset
            before = body[max(0, name_start - _INSPECTOR_MARKER_LOOKBEHIND):name_start]
            after = body[m.end("title"):m.end("title") + _INSPECTOR_DATE_LOOKAHEAD]
            if not (_INSPECTOR_MARKER_RE.search(before) or _INSPECTOR_DATE_RE.search(after)):
                continue                                   # 확증 없음 — 산문 오탐 방지
            key = _inspector_key(name)
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
            if len(out) >= _INSPECTOR_MAX_CANDIDATES:
                break                                  # 병리적 입력 상한(중복 정리 전 원시분)
        final = _dedupe_inspector_names(out)
        # 게이트② 문서 내 합의 — 같은 이름이 두 철자로 읽힌 문서는 통째로 버린다.
        # (조각 흡수 **뒤에** 판정한다: 조각은 정상적으로 부분 일치하므로 먼저 걸러야
        #  근사 중복 판정이 오작동하지 않는다. 순서가 계약이다.)
        if not _inspector_names_are_consistent(final):
            return []
        return final[:_INSPECTOR_MAX_RESULTS]
    except Exception:  # noqa: BLE001 — 어떤 실패도 이름을 지어내는 대신 빈 리스트로 degrade
        return []


def _first_sentence(text: str) -> tuple[str, str]:
    """첫 문장(deficiency)과 나머지(detail). 문장부호가 없으면 안전 길이로 잘라낸다."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return "", ""
    m = re.search(r"(?<=[.!?])\s+", t)
    if m:
        return t[:m.start()].strip(), t[m.end():].strip()
    if len(t) <= 280:
        return t, ""
    return t[:280].rstrip() + "...", t[280:].strip()


def _header_hint_kwargs(header_hints: dict[str, str] | None) -> dict[str, str]:
    """수집 행(nrow)/raw 의 힌트 dict → strip_fda483_page_header kwargs. None 은 힌트 없음(후방호환)."""
    hints = header_hints or {}
    return {
        "establishment_type": hints.get("establishment_type", ""),
        "fei_number": hints.get("fei_number", ""),
        "firm_name": hints.get("firm_name", ""),
    }


def _select_observation_anchors(body: str, matches: list[re.Match[str]]) -> list[re.Match[str]]:
    """`OBSERVATION N` 매치 중 **진짜 항목 표제**만 남긴다(순수 함수).

    결함(2026-07-20 193490 실측): 483 본문은 다른 관찰을 상호참조한다 —
    "...Please refer to Observation 3." 옛 코드는 이 **문장 속 상호참조**까지 표제로 보고
    분할해, 관찰 1 하나가 4조각으로 찢어졌다. 찢긴 조각의 번호는 참조 대상 번호를 그대로
    물려받아 `1,1,3,4,2,3,4` 처럼 **중복**되고, 조각의 첫 문장은 참조문 끝의 마침표뿐이라
    deficiency 가 "." 가 된다. 중복 번호는 하류(inject_slots 의 번역 병합)가 number 를 키로
    쓰기 때문에 번역 오배치까지 유발한다.

    판정 근거 — 5개 문서 전수 실측(193490/193644/193675/193603/193616)에서 다음이 확인됐다.
    쓸 수 없는 신호:
      · 번호 순차성 — 193616 은 원문에 **관찰 1 과 3 만** 존재(2 없음), 193644 는 1,2,3.
        "1부터 +1" 규칙은 193616 의 관찰 3 을 통째로 **유실**시킨다.
      · 콜론 / 앞선 빈 줄 — 상호참조에도 똑같이 붙는다(`refer to\\n\\nOBSERVATION 3: .`).
    유일하게 갈리는 신호(전수 일치):
      · 진짜 표제 뒤에는 **실질 deficiency 문장**이 온다 — ': The responsibili…' ': Written standard…'
      · 상호참조 뒤에는 **참조문의 종결 마침표만** 남는다 — ': .' (다음 줄부터 하위항목 b./c./d.)

    [3번째 신호 2026-07-20 — 193583 실측] 위 두 신호를 **둘 다 통과하는** 문장 속 참조가 있다:
    "...the Form FDA 483, OBSERVATION 1 and the Discussion Items, had already been discussed
    with Dr. Yáñez..." — 앞이 "refer to/see/per" 가 아니고(①통과), 뒤에 실질 문장이 이어진다
    (②통과). 그 결과 번호 1 이 **중복**된 가짜 관찰이 하나 더 생겼다. 갈리는 신호는 하나:
      · 진짜 표제 뒤 문장은 **대문자로 시작**한다 — 실측 9개 문서·29개 관찰 전건
        ("There is a failure…" "The quality control unit…" "An investigation was not…").
      · 문장 중간에 낀 참조 뒤는 **소문자 연결어**로 이어진다("and the Discussion Items…").
    그래서 ③ 뒤따르는 첫 실질 문장이 소문자로 시작하면 기각한다. 실측 29개 정상 관찰 중
    소문자로 시작하는 것은 0건이라 정상 항목을 버리지 않는다.

    그래서 세 가지 **양성 검출**로만 기각한다(정상 항목을 버리지 않는 방향):
      ① 앵커 앞이 참조 문구("refer to" / "see" / "per", 중간에 낀 다른 참조 포함)로 끝난다
      ② 앵커 뒤 첫 문장에 실질 알파벳 내용이 없다(마침표·기호뿐)
      ③ 앵커 뒤 첫 문장이 소문자로 시작한다(문장 중간에 낀 참조)
    셋 다 결함의 직접 증거라, 번호가 건너뛰거나 중복돼도 **정상 관찰은 그대로 살아남는다**.
    """
    selected: list[re.Match[str]] = []
    for i, m in enumerate(matches):
        before = body[max(0, m.start() - _XREF_LOOKBEHIND):m.start()]
        if _XREF_PREFIX_RE.search(before):
            continue                                   # ① 문장 속 상호참조
        nxt = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        head, _ = _first_sentence(body[m.end():nxt].lstrip(" :\t\r\n"))
        if len(re.sub(r"[^A-Za-z]", "", head)) < _HEADING_MIN_ALPHA:
            continue                                   # ② 뒤따르는 실질 문장 없음
        first_alpha = next((c for c in head if c.isalpha()), "")
        if first_alpha and first_alpha.islower():
            continue                                   # ③ 문장 중간에 낀 참조
        selected.append(m)
    return selected


def _extract_483_observations_from_text(
    text: str, header_hints: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """483 PDF 텍스트층 → Observation 번호별 결정론 구조.

    `WE OBSERVED` 이후 `OBSERVATION N` 앵커로 분할하고, 각 Observation 의 첫 문장을
    deficiency 로 둔다. 유효 deficiency 가 없거나 텍스트층 깨짐률이 높으면 [] 로 degrade.

    [FIND-1 M10a] `header_hints`(establishment_type/fei_number/firm_name)가 있으면
    `grm_findings.strip_fda483_page_header` 로 페이지 넘김 헤더 라벨-값 인터리브를 chunk·
    detail 양쪽에서 제거한다 — Observation 이 페이지 경계에 걸치면 STREET ADDRESS/FEI NUMBER/
    TYPE OF ESTABLISHMENT INSPECTED 등 헤더 파편이 deficiency 앞에 접두사로 섞여 들어오는
    라이브 오염(2026-07 VA San Diego 실측)을 방지한다. header_hints=None 은 힌트 없이도
    라벨/날짜/숫자런/주소는 그대로 제거된다(후방호환 — 기존 호출부는 그대로 동작).
    """
    if not text or _text_corruption_ratio(text) > FDA483_TEXT_CORRUPTION_RATIO_MAX:
        return []
    hints = _header_hint_kwargs(header_hints)
    body = normalize_pdf_ligatures(text)   # [2026-07-20] 커밋된 낡은 source_text 도 여기서 복원
    m = _WE_OBSERVED_RE.search(body)
    scoped = body[m.end():] if m else body
    primary = _observations_from_anchors(
        _cut_at_annotations(scoped),
        lambda s: _select_observation_anchors(s, list(_OBS_RE.finditer(s))),
        hints,
        gate=_is_legible_deficiency,
    )
    if primary:
        return primary
    # ── 여기부터 회수 경로 ────────────────────────────────────────────────────
    # 오늘 관찰이 **0건인 문서에만** 도달한다(위에서 1건이라도 나오면 그대로 반환했다) —
    # 정상 문서 1,545개의 출력이 byte 단위로 불변임을 측정이 아니라 구조로 보장한다.
    return _recover_483_observations(body, m, hints)


def _cut_at_annotations(body: str) -> str:
    """[관찰목록 종료 마커 2026-07-27] 483 양식의 "Annotations to Observations" 절은 관찰이
    아니라 **어느 관찰을 시정하기로 했는지에 대한 주석**이고, 그 안에서 관찰 번호가 다시
    열거된다("8. Promised to correct."). 이 절까지 훑으면 같은 번호의 관찰이 **두 번**
    만들어지고(fda483-193541 실측: obs 8 이 중복), 국문 병기는 번호로 매칭하므로 번역이
    뒤쪽(주석) 항목에만 붙어 진짜 관찰 8 이 미번역으로 남아 발행이 막힌다.
    관찰 목록은 이 표제에서 끝난다 — 여기서 자른다."""
    cut = _ANNOTATIONS_RE.search(body)
    return body[:cut.start()] if cut else body


def _observations_from_anchors(
    body: str,
    finder: Callable[[str], list[re.Match[str]]],
    hints: dict[str, str],
    gate: Callable[[str], bool],
) -> list[dict[str, str]]:
    """앵커 목록 → Observation rows. 앵커 찾기(finder)와 표제 통과 기준(gate)만 갈아끼운다
    — 정상 경로와 회수 경로가 **같은 조립 코드**를 쓰게 해 둘이 갈라지지 않도록 한다."""
    matches = finder(body)
    if not matches:
        return []
    out: list[dict[str, str]] = []
    for i, obs in enumerate(matches):
        start = obs.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = _clean_observation_chunk(body[start:end])
        chunk = gf.strip_fda483_page_header(chunk, **hints)
        deficiency, detail = _first_sentence(chunk)
        # [표제 선행 잡음] **모든 경로**에 적용한다(회수 경로 전용 게이트와 달리) — OCR 로
        # 되살린 문서는 정상 앵커 경로로도 파싱되므로, 회수 경로에만 걸면 잡음이 그대로
        # 남는다(실측: 신규 727건 중 23건이 정상 경로로 들어오면서 "| There is a failure…"
        # 형태로 저장됐다). 순수 접두 제거라 정상 표제에는 영향이 없다(내용 절단 상한 있음).
        deficiency = strip_leading_observation_noise(deficiency)
        if not gate(deficiency):
            continue
        clean_detail = _clean_observation_detail(detail)
        clean_detail = gf.strip_fda483_page_header(clean_detail, **hints)
        row = {
            "number": obs.group(1),
            "deficiency": deficiency,
            "detail": clean_detail[:FDA483_OBSERVATION_DETAIL_MAX_CHARS].strip(),
        }
        out.append(row)
    return out


def _recover_483_observations(
    body: str, we_observed: re.Match[str] | None, hints: dict[str, str],
) -> list[dict[str, str]]:
    """정상 경로가 0건일 때만 도는 회수 경로(순서대로 시도, 처음 성공한 것을 쓴다).

    표제는 전부 `_is_recovered_deficiency_publishable` 를 통과해야 한다 — 앵커를 느슨하게
    잡는 만큼 양식 문구·OCR 조각이 섞여 들어오고, 그런 문장이 공개 findings 가 되면 지금의
    침묵(0건)보다 나쁘다.

    ① 비파괴 컷 — `WE OBSERVED` 마커가 관찰 표제 **뒤**에 있으면 자르지 않는다. 오늘은 그
       지점에서 잘라 관찰을 통째로 버린다(실측 83305: 앵커 @1151, 마커 @1345).
    ② 느슨한 앵커 — `OBS ERVAT ION 1` 처럼 단어 안에 공백이 낀 스캔 텍스트층.
    ③ 번호 목록 — `WE OBSERVED` 뒤가 `1. 2. 3.` 이고 "OBSERVATION" 단어가 없는 옛 양식.
       마커가 있을 때만 켠다(문서 어디의 번호 목록이든 잡으면 목차·별첨까지 관찰이 된다).
    """
    gate = _is_recovered_deficiency_publishable

    # ① 비파괴 컷: 자른 뒤에 앵커가 하나도 안 남으면 자르지 않는다.
    if we_observed is not None and _OBS_RE.search(body):
        after = body[we_observed.end():]
        if not _OBS_RE.search(after):
            found = _observations_from_anchors(
                _cut_at_annotations(body),
                lambda s: _select_observation_anchors(s, list(_OBS_RE.finditer(s))),
                hints, gate=gate,
            )
            if found:
                return found

    # [OCR 마커 변형] 정상 경로 마커(`_WE_OBSERVED_RE`)가 못 잡은 문서라도 회수 경로에서는
    # 더 넓은 패턴으로 다시 찾는다 — `(I) (WE) OBSERVED` · `| OBSERVED:` 등. 실측상 회수
    # 실패의 최대 원인이었고, 이게 안 잡히면 아래 ③ 번호목록 폴백이 아예 켜지지 않는다.
    marker = we_observed or _OBS_MARKER_RECOVERY_RE.search(body)
    scoped = _cut_at_annotations(body[marker.end():] if marker is not None else body)

    # ② 느슨한 앵커(OCR 공백·`OBSERVATION #1`). 정상 앵커가 0건인 문서에서만 여기 온다.
    found = _observations_from_anchors(
        scoped, lambda s: list(_OBS_LOOSE_RE.finditer(s)), hints, gate=gate)
    if found:
        return found

    # ③ 번호 목록 — 마커가 있는 문서로 한정(없으면 목차·별첨까지 관찰이 된다).
    if marker is None:
        return []
    found = _observations_from_anchors(
        scoped, lambda s: list(_NUMBERED_OBS_RE.finditer(s)), hints, gate=gate)
    if found:
        return found

    # ④ 닫는 괄호 번호(`1)` · `1.)`). ③ 이 0건일 때만 — 한 문서가 두 양식을 섞어 쓰지
    # 않으므로 순서대로 시도하면 충분하고, 서로의 오탐을 만들지 않는다.
    return _observations_from_anchors(
        scoped, lambda s: list(_PAREN_NUMBERED_OBS_RE.finditer(s)), hints, gate=gate)


def _extract_483_observations(
    pdf_bytes: bytes, header_hints: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """483 PDF bytes → Observation rows. 공개 API(테스트/후속 재사용용), LLM/OCR 없음."""
    try:
        from collect_mfds_gmp_inspection import _extract_pdf_text
    except Exception:  # noqa: BLE001
        return []
    text, _status = _extract_pdf_text(pdf_bytes, max_chars=FDA483_TEXT_MAX_CHARS)
    if len(text) >= FDA483_TEXT_MAX_CHARS:
        log("WARN", "483 텍스트 상한 도달 — Observation 일부 누락 가능(수동 확인)")
    return _extract_483_observations_from_text(text, header_hints)


def _signal_tier(record_type: str, establishment_type: str, excerpt: str) -> str:
    """483 = Tier 3. 무균 시설/신호는 Tier 3 floor, distributor-only 는 하향(§4)."""
    et = (establishment_type or "").lower()
    blob = f"{et} {excerpt}".lower()
    if "sterile" in blob or _kw_any(blob, STERILE_BIO_TIER3_FLOOR):
        return "Tier 3"            # 무균 시설/치명 신호 → Tier 3 floor
    base = "Tier 3" if record_type == RECORD_TYPE_483 else "Tier 2"
    if "distributor" in et and "manufactur" not in et:
        base = {"Tier 3": "Tier 2", "Tier 2": "Tier 1"}.get(base, base)
    return base


def _site_country(country: str, state: str) -> str:
    """Site Country(소재국) 매핑(HOLD②). Country 우선(해외), 공란+State(미국) → 'United States'.

    State(주)는 절대 Site Country 에 넣지 않는다 — GRM 의 Site Country 의미(소재국)와 어긋남.
    """
    if country:
        return country
    if state:
        return "United States"
    return ""


def _to_item(nrow: dict[str, str], excerpt: str,
             observations: list[dict[str, str]] | None = None,
             body_full: str = "", text_status: str = "",
             inspectors: list[str] | None = None) -> IntakeItem | None:
    """정규화 행(+excerpt) → IntakeItem. 수의/기기/식품 도메인은 None(드롭).

    `body_full`(비공백)이면 raw 에 `fda483_body_full` 로 실어 deep_analysis fan-out 입력으로 쓴다
    (ENABLE_FDA_483_DEEP 게이트 산출 — WL wl_body_full 동형). 결정론 Observation 상세와 별개 층.

    `text_status` 는 PDF 텍스트층 확보 결과 코드다. **아무 본문층도 못 얻은 경우에만**
    `fda483_text_status` 로 raw 에 남긴다 — 하류(card_scaffold.`_absent_reason`)가 "왜 비었는지"를
    사람 문장으로 바꿔 prose_input 에 실어, 코드와 LLM 이 사유를 **지어내지** 않게 하기 위해서다.
    본문을 얻었으면 사유가 없으므로 키 자체를 달지 않는다(골든 additive).

    `inspectors`(비어있지 않으면) raw 에 `fda483_inspectors` 로 실사관 이름 목록을 싣는다
    (`_extract_483_inspectors` 산출 — ENABLE_FDA_483_OBSERVATIONS/DEEP 과 독립인 순수 결정론
    층). 빈 리스트/None 이면 키 자체를 달지 않는다(다른 조건부 raw 필드와 동일 관례)."""
    record_type = nrow["record_type"]
    media_id = nrow["media_id"]
    company = nrow["company"]
    fei = nrow["fei"]
    state = nrow["state"]
    country = nrow.get("country", "")
    establishment_type = nrow.get("establishment_type", "")
    record_date = nrow["record_date"]                    # 실사일(원문 MM/DD/YYYY 유지)
    publish_iso = _parse_mdy(nrow["publish_date"])

    # 노이즈/관련성 게이트 — gate_blob 으로 도메인 판정.
    gate_blob = " ".join([company, establishment_type, country, record_type, excerpt])
    if _kw_any(gate_blob.lower(), QA_HARD_EXCLUDE_TERMS):   # 수의/동물용 하드 드롭
        return None
    relevance = compute_relevance(company, establishment_type, record_type, excerpt)
    if relevance == "Unrelated":           # 기기/식품/화장품 도메인 → 드롭(QA 범위 밖)
        return None
    if relevance == "Pending":
        relevance = "Possible"             # 483 은 제조 실사 맥락 → 보수적 보존

    tier = _signal_tier(record_type, establishment_type, excerpt)
    type_or_class = TYPE_FDA_483 if record_type == RECORD_TYPE_483 else TYPE_FDA_EIR
    pdf_url = _pdf_url(media_id)
    site_country = _site_country(country, state)

    raw_payload: dict[str, Any] = {
        "channel": "fda-483",
        "firm": company,
        "fei_number": fei,
        "record_type": record_type,
        "establishment_type": establishment_type,
        "record_date": record_date,
        "publish_date": nrow["publish_date"],
        "country": country,
        "site_state": state,          # 미국 주(소재국 아님 — Site Country 와 분리)
        "media_id": media_id,
        "pdf_url": pdf_url,
        # compute_modality 가 시설유형의 'drug' 단서를 보도록 product_type 으로 정규화.
        "product_type": establishment_type,
    }
    if excerpt:
        raw_payload["fda483_excerpt"] = excerpt
    if observations:
        raw_payload["fda_483_observations"] = observations
    if inspectors:
        raw_payload["fda483_inspectors"] = inspectors   # 서명블록 실사관 이름(결정론·정밀도 최우선)
    if body_full:
        raw_payload["fda483_body_full"] = body_full   # deep_analysis fan-out 입력(전문)
    if text_status and text_status not in ("ok", "") and not (excerpt or observations or body_full):
        raw_payload["fda483_text_status"] = text_status   # 결손 사유(본문 전무일 때만)

    # Modality 는 insert 시 notion_create_page 가 raw_payload(product_type)+headline/body 로
    # compute_modality 한다(IntakeItem 에 저장 필드 없음 — 타 수집기와 동일).
    label = "FDA 483" if record_type == RECORD_TYPE_483 else "FDA EIR"
    locale = country or (f"{state}, United States" if state else "")
    body = (
        f"FDA {record_type} — OII FOIA Electronic Reading Room 공개 실사 기록.\n"
        # [어휘 분리 2026-07-20] '원문 미기재'는 원문에 없다는 단정이라 거짓일 수 있다 —
        # 여기서는 우리가 확보 못 했다는 사실만 말한다(card_scaffold.VALUE_UNKNOWN 과 동일 취지).
        f"제조소/업체: {company or '미확인'}"
        + (f" (FEI {fei})" if fei else "")
        + (f"\n시설 유형: {establishment_type}" if establishment_type else "")
        + (f"\n소재: {locale}" if locale else "")
        + f"\n출처: {OII_READING_ROOM_URL}"
    )

    return IntakeItem(
        source=SOURCE_FDA_483,
        document_id=f"fda483-{media_id}",            # media id 안정 → dedup
        date_iso=publish_iso,                        # 공개일(윈도우·카드 발행일)
        headline=f"[{label}] {company or media_id}"[:240],
        official_url=pdf_url,                        # 건별 483 PDF (per-item L1)
        type_or_class=type_or_class,
        firm=company[:200],
        body=body,
        api_query=OII_READING_ROOM_URL,
        qa_relevance=relevance,
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_PAGE,
        signal_tier=tier,
        raw_payload=raw_payload,
        source_url=OII_READING_ROOM_URL,
        site_country=site_country,                   # Country(해외) 또는 'United States'/''
        language=LANGUAGE_EN,
        region_jurisdiction=REGION_FDA,
    )


def collect_fda_483(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA 483 수집 진입점. (items, error_msg).

    전수 backbone = DataTables AJAX(1차) → 전수 JSON(2차). 둘 다 사망 시에만 정적 HTML
    폴백(부분) + warning.
    - 백본 3단 모두 실패 → error.
    - 483 행 0 → 구조 변경 의심 error(침묵 금지).
    - 윈도우 내 0건 → 정상(빈 리스트·error 없음).
    - PDF excerpt/Observation 실패는 graceful(키 미기록·메타 카드 유지·LAST_HEALTH 경고).
    """
    global LAST_HEALTH, _LAST_BACKBONE
    _LAST_BACKBONE = BACKBONE_DATATABLES     # 실행별 리셋(테스트 스텁 시 이전 값 누출 방지)
    _OCR_BUDGET["remaining"] = FDA483_OCR_PAGE_BUDGET   # 실행별 리셋(위와 동일 이유)
    _OCR_BUDGET["used"] = 0
    reset_ocr_health()
    excerpt_health: dict[str, Any] = {
        "attempted": 0, "ok": 0, "failed": 0, "capped": False, "warnings": [],
        # [수집 사각 표면화 2026-07-27] cap 때문에 **한 번도 시도되지 않은** 윈도우 내 문서 수.
        # 종전엔 "cap 도달" 한 줄만 남아 몇 건이 통째로 빠졌는지 아무도 몰랐고, 그 문서들이
        # "원문 없음" 카드로 발행됐다. 숫자를 남겨야 사각이 보인다.
        "skipped_no_attempt": 0,
    }
    observations_enabled = _observations_enabled()
    observations_health: dict[str, Any] = {
        "enabled": observations_enabled, "attempted": 0, "extracted": 0,
        "failed": 0, "warnings": [],
    }
    # [483 분석층 2026-07-02] deep(전문 보존) 관측. 결정론 Observation 과 독립(위 _deep_enabled).
    deep_enabled = _deep_enabled()
    deep_health: dict[str, Any] = {
        "enabled": deep_enabled, "attempted": 0, "stored": 0, "failed": 0, "warnings": [],
    }
    # [실사관 추출 2026-07-30] 순수 결정론 파서 — ENABLE_FDA_483_OBSERVATIONS/DEEP 과 독립으로
    # 항상 수행(네트워크 추가 요청 0, 이미 받은 text 재사용). "enabled" 키가 없는 것은 의도
    # (플래그 게이트 자체가 없다는 뜻 — observations_health/deep_health 와의 차이).
    inspectors_health: dict[str, Any] = {
        "attempted": 0, "extracted": 0, "failed": 0, "warnings": [],
    }
    LAST_HEALTH = {
        "fda483_excerpt": excerpt_health,
        "fda_483_observations": observations_health,
        "fda_483_deep": deep_health,
        "fda483_inspectors": inspectors_health,
        "source_degraded": False,
    }

    log("INFO", f"FDA 483 수집(백본 3단 DataTables→전수JSON→정적HTML): {OII_READING_ROOM_URL}")
    keep_rows, html_data_count, source_degraded = _fetch_html_rows(start)
    if not keep_rows:
        LAST_HEALTH = {
            "fda483_excerpt": excerpt_health,
            "fda_483_observations": observations_health,
            "fda_483_deep": deep_health,
            "fda483_inspectors": inspectors_health,
            "source_degraded": source_degraded,
            "backbone": _LAST_BACKBONE,
        }
        return [], ("FDA 483 수집 실패: 백본 3단(DataTables/전수JSON/정적HTML) 모두 483 행 0 — "
                    "소스 구조 변경 또는 일시 장애(수동 확인 필요)")

    # Publish Date 윈도우 필터(전수 평가·정렬 비의존). 최신 N건 excerpt cap 위해 publish desc 정렬.
    in_window = [r for r in keep_rows
                 if _within_window(_parse_mdy(r["publish_date"]), start, end)]
    in_window.sort(key=lambda r: _parse_mdy(r["publish_date"]), reverse=True)

    items: list[IntakeItem] = []
    seen: set[str] = set()
    for nrow in in_window:
        media_id = nrow["media_id"]
        if not media_id or media_id in seen:
            continue
        seen.add(media_id)

        # 483 PDF 결함 excerpt + Observation 상세 + (deep on) 전문 보존(cap 내 시도).
        # 실패는 키 미기록 + warning(graceful — 결정론/deep 어느 층이 빠져도 요약카드는 유지).
        excerpt = ""
        observations: list[dict[str, str]] = []
        body_full = ""
        inspectors: list[str] = []
        # [결손 사유 전파 2026-07-20] 본문을 못 얻었을 때 **왜** 못 얻었는지. 종전엔 이 사유가
        # health 카운터에만 남고 카드로는 "없음"만 갔고, 이유를 모르는 하류가 이유를 지어냈다
        # (디제스트가 "스캔·비공개로 상세가 제공되지 않아" 라고 단정한 사례). cap 에 걸려 아예
        # 시도하지 못한 경우도 결손이므로 사유를 남긴다.
        text_status = "not-attempted"
        pdf_url = _pdf_url(media_id)
        # [FIND-1 M10a] Observation 추출의 페이지헤더 스크럽 힌트 — 이 행(nrow)의 시설유형/
        # FEI/업체명을 strip_fda483_page_header 에 그대로 넘겨 OCR 공백변형까지 흡수한다.
        header_hints = {
            "establishment_type": nrow.get("establishment_type", ""),
            "fei_number": nrow.get("fei", ""),
            "firm_name": nrow.get("company", ""),
        }
        # ★ `capped` 로 루프를 빠져나가지 않는다 — 종전 `and not capped` 는 상한 도달 후
        #   첫 문서에서만 분기를 평가해 **몇 건이 통째로 빠졌는지 셀 수 없었다**. 이제 전건을
        #   지나가며 미시도 수를 센다(작업은 여전히 상한까지만 — 비용 불변).
        if pdf_url:
            if excerpt_health["attempted"] >= FDA483_EXCERPT_MAX_ITEMS:
                excerpt_health["capped"] = True
                excerpt_health["skipped_no_attempt"] += 1
            else:
                excerpt_health["attempted"] += 1
                if FDA483_EXCERPT_DELAY_SECONDS:
                    time.sleep(FDA483_EXCERPT_DELAY_SECONDS)
                # 483 전문(200000 상한)을 읽는다 — 결정론 Observation·deep 전문 모두 8쪽+ 483 의
                # 뒤 Observation 까지 담기게(공유 엔진 12000 기본이 절단하던 것 보완). excerpt 는
                # 이 text 에서 앵커 뒤 1500자만 다시 잘라 산출물 불변.
                text, status = _fetch_fda483_pdf_text(pdf_url)
                text_status = status if not text else "ok"
                # [실사관 추출 2026-07-30] 반드시 **청소 이전** 원시 text 에서 추출한다 —
                # `_clean_observation_detail`/`_FDA483_FOOTER_RE` 는 서명블록을 지우는 것이
                # 목적이라(발행 게이트가 그 결과에 의존) 그 뒤 텍스트에는 이름이 남지 않는다.
                # 플래그와 무관(순수 파서, 추가 네트워크 요청 없음 — 이미 받은 text 재사용).
                inspectors_health["attempted"] += 1
                inspectors = _extract_483_inspectors(text) if text else []
                if inspectors:
                    inspectors_health["extracted"] += 1
                else:
                    inspectors_health["failed"] += 1
                    warn = (f"FDA 483 inspectors 미확보"
                            f"({status if not text else 'no-inspectors'}): {pdf_url}")
                    inspectors_health["warnings"].append(warn)
                    log("WARN", warn + " — 카드 발행에는 영향 없음(선택 슬롯)")
                excerpt = _extract_fda483_excerpt(text) if text else ""
                if excerpt:
                    excerpt_health["ok"] += 1
                else:
                    excerpt_health["failed"] += 1
                    warn = f"FDA 483 excerpt 실패({status if not text else 'no-excerpt'}): {pdf_url}"
                    excerpt_health["warnings"].append(warn)
                    log("WARN", warn + " — 메타 카드로 유지(manual_review)")
                if observations_enabled:
                    observations_health["attempted"] += 1
                    observations = (
                        _extract_483_observations_from_text(text, header_hints) if text else []
                    )
                    if observations:
                        observations_health["extracted"] += 1
                    else:
                        observations_health["failed"] += 1
                        warn = (f"FDA 483 observations 실패"
                                f"({status if not text else 'no-observations'}): {pdf_url}")
                        observations_health["warnings"].append(warn)
                        log("WARN", warn + " — 요약카드로 유지")
                # [483 분석층] 전문 보존 — 파싱 가능한 실제 483(Observation ≥1)일 때만 보존해
                # fan-out 이 스캔본/표지-only/깨진 텍스트를 LLM 입력으로 삼지 않게 한다(환각 통제).
                # ENABLE_FDA_483_OBSERVATIONS 와 독립(순수 파서 재사용 — 그 플래그와 무관하게 판정).
                if deep_enabled:
                    deep_health["attempted"] += 1
                    parsed = (
                        _extract_483_observations_from_text(text, header_hints) if text else []
                    )
                    if parsed:
                        body_full = text
                        deep_health["stored"] += 1
                    else:
                        deep_health["failed"] += 1
                        warn = (f"FDA 483 deep 전문 미확보"
                                f"({status if not text else 'no-observations'}): {pdf_url}")
                        deep_health["warnings"].append(warn)
                        log("WARN", warn + " — 분석층 없이 발행(결정론 상세·요약카드는 유지)")

        item = _to_item(nrow, excerpt, observations, body_full, text_status,
                        inspectors=inspectors)
        if item is not None:             # dedup 은 위 media_id seen 으로 보장(doc_id=fda483-<id>)
            items.append(item)

    LAST_HEALTH = {
        "fda483_excerpt": excerpt_health,
        "fda_483_observations": observations_health,
        "fda_483_deep": deep_health,
        "fda483_inspectors": inspectors_health,
        "source_degraded": source_degraded,
        "backbone": _LAST_BACKBONE,
        "fda_483_ocr": ocr_health(),
    }
    # [침묵 금지 2026-07-30] 엔진 부재는 문서 사정이 아니라 **환경 사정**이다 — 고치면
    # 되찾을 수 있는 결손이므로 건수와 사유를 로그에 올리고, health 경보로도 승격시킨다
    # (grm_health.fda483-ocr-engine-missing). 종전에는 status 문자열만 raw 에 묻혔다.
    if _OCR_HEALTH["engine_unavailable"]:
        log("WARN", f"FDA 483 OCR 엔진 사용 불가 — 스캔본 {_OCR_HEALTH['engine_unavailable']}건이 "
                    f"본문 없이 처리됐다({_OCR_HEALTH['engine_reason']}). "
                    "러너에 tesseract-ocr + tesseract-ocr-eng 가 설치됐는지 확인하라 "
                    "(.github/actions/setup-ocr).")
    if excerpt_health["capped"]:
        log("WARN", f"FDA 483 PDF 상한({FDA483_EXCERPT_MAX_ITEMS}) 도달 — 윈도우 내 "
                    f"{excerpt_health['skipped_no_attempt']}건이 **한 번도 시도되지 않았다**. "
                    "그 문서들은 원문을 확보하지 못한 채 발행된다(FDA483_PDF_MAX_ITEMS 로 상향 가능)")
    if _OCR_BUDGET["remaining"] <= 0:
        log("WARN", f"FDA 483 OCR 페이지 예산({FDA483_OCR_PAGE_BUDGET}쪽) 소진 — "
                    "이후 스캔본은 OCR 없이 진행(FDA483_OCR_PAGE_BUDGET 로 상향 가능)")
    log("INFO", f"FDA 483 완료: {len(items)}건 (윈도우내 후보 {len(in_window)}, "
                f"483 행 {len(keep_rows)}/{html_data_count}, "
                f"source={_LAST_BACKBONE}{'·부분/동결의심' if source_degraded else ''}) "
                f"· excerpt attempted={excerpt_health['attempted']} ok={excerpt_health['ok']} "
                f"failed={excerpt_health['failed']} "
                f"미시도={excerpt_health['skipped_no_attempt']} "
                f"· OCR {_OCR_BUDGET['used']}/{FDA483_OCR_PAGE_BUDGET}쪽 "
                f"ok={_OCR_HEALTH['ok']} 엔진불가={_OCR_HEALTH['engine_unavailable']} "
                f"예산초과={_OCR_HEALTH['budget_skipped']} "
                f"· observations enabled={observations_enabled} "
                f"attempted={observations_health['attempted']} "
                f"extracted={observations_health['extracted']} "
                f"failed={observations_health['failed']} · deep enabled={deep_enabled} "
                f"attempted={deep_health['attempted']} stored={deep_health['stored']} "
                f"failed={deep_health['failed']} "
                f"· inspectors attempted={inspectors_health['attempted']} "
                f"extracted={inspectors_health['extracted']} "
                f"failed={inspectors_health['failed']}")
    return items, None
