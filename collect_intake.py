#!/usr/bin/env python3
"""
GRM API Intake Collector — v15.1 Phase 2

Federal Register API + OpenFDA Drug Enforcement API + RSS 피드 (EMA · MHRA · PIC/S · ECA)
+ FDA Warning Letters 를 호출해 지난 7일 (KST 기준) 항목을 수집하고,
Notion "GRM API Intake" 데이터베이스에 raw 필드를 저장한다.

설계 원칙:
1. KST 기준 7일 윈도우 — 모든 날짜는 Asia/Seoul 로 계산
2. raw API 필드 보존 — Evidence A 조건 충족을 위해 원문 JSON 도 페이지 본문에 보관
3. graceful degradation — 한 소스 실패해도 다른 소스 계속 진행
4. 중복 제거 — source::document_id 키로 Run Date 내 중복 skip
5. QA relevance 1 차 휴리스틱만 부여, 최종 판정은 Routine 위임
6. Source Type 분류 — Official API / Official Regulatory Page / Official Regulator Blog
                      / Expert Secondary

환경 변수 (GitHub Secrets):
- NOTION_TOKEN       : Notion Integration token (secret_…)
- NOTION_DATABASE_ID : "GRM API Intake" DB ID
- OPENFDA_API_KEY    : OpenFDA 무료 API key (선택, 없으면 no-key 사용)

CLI 옵션:
- --dry-run : Notion 호출 없이 stdout 에 요약만 출력
- --window-days N : 기본 7. 백필 테스트 시 변경
- --sources : 수집 소스 선택 (기본: all). 예: --sources fr recall ema mhra pics eca wl
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import unescape as _html_unescape
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import requests

from grm_common import (
    HTTPClientError,
    env_flag,
    http_get_json,
    http_get_xml,
    log,
    retry_after_seconds,
)
from findings_store import (
    DEFAULT_FINDINGS_SQLITE_PATH,
    append_intake_item_to_sqlite,
    append_intake_item_with_findings_to_sqlite,
)
from findings_supabase_append import (
    append_intake_item_to_supabase,
    append_intake_item_with_findings_to_supabase,
)
# K2: 결정론 카드 골격 조립기 (같은 폴더 평면 모듈)
from card_scaffold import (
    assemble_web_brief,
    build_card_scaffold,
    compute_render_plan,
    merge_recall_cards,
)

# ── [리팩토링 배치3 Phase1] health 판정 층을 grm_health 로 분리(verbatim 이동). 아래 재수출로
#    기존 참조 경로(collect_intake._evaluate_health 등)를 전부 보존한다(하위호환·테스트 무수정). ──
from grm_health import (
    HealthCheckResult,
    HealthFinding,
    _GLOBAL_PUBLIC_SOURCE_CODES,
    _MFDS_FEATURE_SOURCE_CODES,
    _MFDS_PUBLIC_ENDPOINT_SOURCE_CODES,
    _PUBLIC_ENDPOINT_403_SOURCE_CODES,
    _TRANSIENT_ELIGIBLE_SOURCE_CODES,
    _TRANSIENT_ERROR_MARKERS,
    _evaluate_health,
    _is_transient_source_error,
    _source_health_rows,
    _write_health_json,
    _write_health_summary,
)

# ── [리팩토링 배치3 Phase2] 분류 판정 순수함수 층을 grm_taxonomy 로 분리(verbatim 이동).
#    재수출로 기존 참조 경로(collect_intake.compute_modality 등) 전부 보존. compute_signal_tier
#    는 SOURCE_* 의존으로 잔류하며 여기 재수출된 _kw_match/_kw_any 를 사용한다. ──
from grm_taxonomy import (
    FDA_WL_DRUG_ONLY_KEYWORDS,
    FDA_WL_LOW_VALUE_KEYWORDS,
    FDA_WL_OFFICE_CONTEXTUAL,
    FDA_WL_OFFICE_EXCLUDE,
    FDA_WL_OFFICE_KEEP,
    MODALITY_BIOLOGIC,
    MODALITY_BIOLOGIC_BRANDS,
    MODALITY_BIOLOGIC_TERMS,
    MODALITY_CHEMICAL,
    MODALITY_DRUG_PRODUCT_TERMS,
    MODALITY_KOREAN_FORM_TERMS,
    MODALITY_OTHER,
    MODALITY_PRODUCT_NAME_KEYS,
    MODALITY_VET_EXCLUDE_TERMS,
    OSD_SOLID_TERMS,
    QA_CATEGORY_KEYWORDS,
    QA_DEVICE_DRUG_GUARD,
    QA_DEVICE_EXCLUDE_TERMS,
    QA_EXCLUDE_KEYWORDS,
    QA_HARD_EXCLUDE_TERMS,
    QA_LIKELY_BOOST,
    QA_MIN_MATCH,
    _KOREAN_FORM_SUFFIX_RE,
    _as_lower_set,
    _fda_wl_office_gate,
    _is_low_value_fda_warning_letter,
    _kw_any,
    _kw_match,
    _phrase_any,
    compute_modality,
    compute_osd_relevance,
    compute_relevance,
)

# ── [배치5] Phase0 공용 relocate(SOURCE_*·truncate·chunk_text·_env_int·chunk상수) — grm_common 재수출(하위호환·테스트·위성 무수정) ──
from grm_common import (
    INTAKE_SOURCE_SPECS,
    NOTION_RICH_TEXT_CHUNK,
    SOURCE_BRAVE,
    SOURCE_ECA,
    SOURCE_EMA,
    SOURCE_EPR,
    SOURCE_EU_GMP_NCR,
    SOURCE_FDA_483,
    SOURCE_FDA_WL,
    SOURCE_FR,
    SOURCE_HANDOFF,
    SOURCE_HC,
    SOURCE_ICH,
    SOURCE_ISPE,
    SOURCE_MFDS,
    SOURCE_MHRA,
    SOURCE_PICS,
    SOURCE_RAPS,
    SOURCE_RECALL,
    SOURCE_WHO,
    _env_int,
    chunk_text,
    truncate,
)

# ── [배치5] Phase1 Notion 클라이언트 층 분리 — grm_notion 재수출(하위호환·테스트·위성 무수정) ──
from grm_notion import (
    MODALITY_OPTIONS,
    NOTION_API_VERSION,
    NOTION_BLOCK_CHILDREN_URL_TPL,
    NOTION_CODE_BLOCK_CHUNK,
    NOTION_DB_QUERY_URL_TPL,
    NOTION_PAGES_URL,
    NOTION_PAGE_URL_TPL,
    NotionDedupeQueryError,
    NotionHandoffError,
    PROP_API_QUERY,
    PROP_BODY,
    PROP_COLLECTED_AT,
    PROP_COMMENTS_CLOSE,
    PROP_DATE,
    PROP_DISTRIBUTION,
    PROP_DOC_ID,
    PROP_EVIDENCE_CANDIDATE,
    PROP_FIRM,
    PROP_HANDOFF_REF,
    PROP_HEADLINE,
    PROP_LANGUAGE,
    PROP_MODALITY,
    PROP_NAME,
    PROP_OFFICIAL_URL,
    PROP_OSD_RELEVANCE,
    PROP_QA_RELEVANCE,
    PROP_RAW_EXCERPT,
    PROP_REGION_JURISDICTION,
    PROP_RUN_DATE,
    PROP_SEARCH_QUERY,
    PROP_SIGNAL_TIER,
    PROP_SITE_COUNTRY,
    PROP_SOURCE,
    PROP_SOURCE_TYPE,
    PROP_SOURCE_URL,
    PROP_STATUS,
    PROP_TYPE_CLASS,
    _date_iso,
    _datetime_iso,
    _rich_text,
    _select,
    _url,
    build_notion_children,
    build_notion_properties,
    notion_api_request,
    notion_create_page,
    notion_headers,
    notion_query_existing_doc_ids,
    notion_verify_handoff_ref_property,
    notion_verify_modality_property,
)

# ── [배치5] Phase2 handoff/web emit 층 분리 — grm_handoff 재수출(하위호환·테스트·위성 무수정) ──
from grm_handoff import (
    COVERAGE_SOURCE_LABELS,
    HANDOFF_SCHEMA_VERSION,
    HANDOFF_SCHEMA_VERSION_V2,
    TYPE_ROUTINE_HANDOFF,
    _AGED_NEW_MAX_PAGES,
    _AGED_NEW_PAGE_SIZE,
    _COVERAGE_KNOWN_SOURCES,
    _DEFAULT_HANDOFF_WINDOW_DAYS,
    _HANDOFF_REF_ROWS_MAX_PAGES,
    _HANDOFF_V2_ROW_KEEP,
    _INTAKE_RAW_MAX_PAGES,
    _KO_WEEKDAYS_FULL,
    _NOTION_CHILDREN_CREATE_LIMIT,
    _dedupe_latest_rows,
    _enable_handoff_idempotency_v2,
    _enable_handoff_v2,
    _enable_web_brief_emit,
    _handoff_blocks,
    _handoff_page_properties,
    _intake_page_snapshot,
    _plain_text,
    _prop_date,
    _prop_rich_text,
    _prop_select,
    _prop_title,
    _prop_url,
    _query_new_rows_with_ref,
    attach_raw_to_rows,
    build_coverage_collected,
    build_inmemory_raw,
    build_routine_handoff_payload,
    build_routine_handoff_payload_v2,
    build_web_brief_payload_v2,
    coverage_source_counts,
    emit_routine_handoff,
    emit_web_brief_file,
    enrich_rows_with_raw,
    fetch_intake_raw_payload,
    notion_append_page_children,
    notion_archive_page_children,
    notion_count_aged_unconsumed_new,
    notion_find_handoff_page,
    notion_mark_rows_handoff_ref,
    notion_query_new_intake_rows,
    notion_reconcile_handoff_refs,
    notion_revert_refs_for_handoff,
    notion_stale_prior_open_handoffs,
    notion_upsert_routine_handoff,
    resolve_handoff_window_days,
    resolve_web_brief_dir,
    web_brief_filename,
    weekday_kst,
)


# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────

KST = ZoneInfo("Asia/Seoul")

FR_API_BASE = "https://www.federalregister.gov/api/v1/documents.json"
OPENFDA_API_BASE = "https://api.fda.gov/drug/enforcement.json"

# FDA Recalls/Enforcement L2 (OpenFDA 는 항목별 사용자 친화 URL 이 없음)
FDA_RECALLS_L2 = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"

# Notion 속성 이름 (스키마 가이드와 1:1 대응 — 변경 시 양쪽 모두 수정)
PROP_SELF_CHECK = "Self-Check Required"  # retained in Notion, no longer written by collectors

# Phase 2a 신규 Notion 필드

# v15.1 Phase 2 — RSS / HTML 소스
# PL-10b/B1 근본해결: row 가 어느 handoff 에 포함됐는지 결정론적 표시(rich_text).
# 값 = handoff_id("routine-handoff::YYYY-MM-DD") — page id 가 아니라 handoff_id 를 쓰는
# 이유: ① 같은 날 재-emit 시 page 탐색 없이 자격 판정 가능 ② 사람이 읽을 수 있음
# ③ handoff page 재생성(삭제 후 upsert)에도 참조가 살아남음. Notion 속성은 사람이
# 사전 생성(ENABLE_HANDOFF_IDEMPOTENCY_V2 preflight 가 부재 시 v1 폴백).

# ── Phase 2a: Search / Scrape 소스 ──────────────────────────────────────────

# Source Type 분류 값 (Notion Select 옵션과 1:1 대응)
SRC_TYPE_OFFICIAL_API     = "Official API"              # FR, OpenFDA Recall, EMA RSS
SRC_TYPE_OFFICIAL_PAGE    = "Official Regulatory Page"  # PIC/S, FDA WL
SRC_TYPE_OFFICIAL_BLOG    = "Official Regulator Blog"   # MHRA Inspectorate
SRC_TYPE_EXPERT_SECONDARY = "Expert Secondary"          # ECA Academy
SRC_TYPE_SEARCH_RESULT    = "Search Result"             # Phase 2a: Brave Search
SRC_TYPE_OFFICIAL_SCRAPE  = "Official Page Scrape"      # Phase 2a: scrape 수집

# 소스별 Source Type 매핑
SOURCE_TYPE_MAP: dict[str, str] = {
    # 기존 7개
    SOURCE_FR:      SRC_TYPE_OFFICIAL_API,
    SOURCE_RECALL:  SRC_TYPE_OFFICIAL_API,
    SOURCE_EMA:     SRC_TYPE_OFFICIAL_API,
    SOURCE_MHRA:    SRC_TYPE_OFFICIAL_BLOG,
    SOURCE_PICS:    SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_ECA:     SRC_TYPE_EXPERT_SECONDARY,
    SOURCE_FDA_WL:  SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_MFDS:    SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_ICH:     SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_WHO:     SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_HC:      SRC_TYPE_OFFICIAL_API,
    SOURCE_FDA_483: SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_HANDOFF: SRC_TYPE_OFFICIAL_PAGE,
    # Phase 2a 신규
    SOURCE_BRAVE:   SRC_TYPE_SEARCH_RESULT,
    SOURCE_RAPS:    SRC_TYPE_EXPERT_SECONDARY,
    SOURCE_EPR:     SRC_TYPE_EXPERT_SECONDARY,
    # [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak 블로그 — ECA 와 동일 분류
    SOURCE_ISPE:    SRC_TYPE_EXPERT_SECONDARY,
    # [EU GMP NCR 2026-07-23] EudraGMDP 공식 규제 DB — FDA WL/PIC/S 와 동일 분류
    SOURCE_EU_GMP_NCR: SRC_TYPE_OFFICIAL_PAGE,
}

# RSS / HTML 엔드포인트 (v15.1 추가)
EMA_RSS_FEEDS: dict[str, str] = {
    # 올바른 EMA RSS URL 형식: https://www.ema.europa.eu/en/{feed}.xml
    # (https://www.ema.europa.eu/en/news-events/rss-feeds 에서 확인)
    "scientific-guidelines":  "https://www.ema.europa.eu/en/scientific-guidelines.xml",
    "inspections":            "https://www.ema.europa.eu/en/inspections.xml",
    "news":                   "https://www.ema.europa.eu/en/news.xml",
    "regulatory-guidelines":  "https://www.ema.europa.eu/en/regulatory-and-procedural-guideline.xml",
}
MHRA_RSS_URL = "https://mhrainspectorate.blog.gov.uk/feed/"   # Atom 형식
# [MHRA 회수 커버리지 수리 2026-07-12] gov.uk 의약품/의료기기 경보 finder 의 Atom 피드.
# Class 2/3/4 Medicines Recall·Medicines Defect 와 Device 경보(FSN)가 섞여 있어 category
# term 으로 의약품 회수/결함만 채택한다(_is_mhra_medicines_alert).
MHRA_ALERT_RSS_URL = "https://www.gov.uk/drug-device-alerts.atom"
PICS_RSS_URL = "https://picscheme.org/rss/general_en.rss"
ECA_RSS_URL  = "https://app.gxp-services.net/eca_newsfeed.xml"
# [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak 블로그(Drupal RSS 2.0). excerpt cap/delay/
# max-chars 는 별도 상수를 만들지 않고 ECA_ARTICLE_EXCERPT_* 를 그대로 재사용(값 동일 정책).
ISPE_RSS_URL = "https://ispe.org/pharmaceutical-engineering/ispeak-blogs/rss.xml"
FDA_WL_URL   = (
    "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations"
    "/compliance-actions-and-activities/warning-letters"
)
FDA_WL_SEARCH_API = (
    "https://api.fda.gov/other/warning_letters.json"   # 미공개 엔드포인트 — 폴백용
)

# WHY-1 #2: FDA Warning Letter 본문 위반 excerpt (flag 게이트 ENABLE_WL_BODY, 기본 off).
# 목록 메타(subject)만 잡던 것을 편지 본문의 위반 서술 구간까지 추출해 카드 "왜"를 살린다.
# in-window WL 은 부서 게이트 후 소수(주 한 줌) → fetch 부담 낮음. 실패는 graceful(키 미기록).
WL_BODY_MAX_CHARS = 1500
WL_BODY_FETCH_TIMEOUT = 20
# [WL 심층분석 fan-out 2026-07-01] 본문 전문(全文) 확보 (flag 게이트 ENABLE_WL_BODY_FULL,
# 기본 off). 기존 WL_BODY_MAX_CHARS(1500자) excerpt 는 카드 "왜"용 짧은 발췌를 그대로 유지하고,
# 이 상수는 카드 1건에 대한 별도 fan-out 심층분석(조항별 위반·구제조치·행정리스크 5섹션,
# docs/prompts/GRM_Prompt_DeepWL_v1.md)의 입력 컨텍스트용 전문을 담는다. 수집(Python·GitHub
# Actions) 단계 비용은 HTTP GET 1회뿐이라 상한을 넉넉히 잡아도 부담이 없다(LLM 비용은 fan-out
# 단계에서 카드별로 격리 — collect_intake 는 저장만 한다). 실패는 graceful(키 미기록).
# [상한 상향 2026-07-20] 20000 → 30000. 실측(2026-07-20 WL 2건) 앵커~편지끝 21.4k·24.1k 로
# 종전 상한이 **회신 기한·시정요구 문단(편지 뒷부분)을 잘라내고 있었다** — fan-out 의
# `required_remediation.deadline` 이 근거를 잃는 자리다. 비용은 여전히 HTTP GET 1회.
WL_BODY_FULL_MAX_CHARS = 30000

# [전문지 브리핑 v2 2026-07-13] ECA 기사(gmp-compliance.org) 본문 흡수 — Routine summary 가
# 실기사 본문을 근거로 작성되도록 prose_input 을 풍부화(flag 게이트 ENABLE_ECA_ARTICLE_EXCERPT,
# 기본 off). off 면 collect_eca_rss 는 기존과 완전 동일(호출 자체가 없음 → 골든 바이트 불변).
# on 이어도 403/timeout 은 조용히 skip(WARN 로그 1줄, 카드는 기존 메타 그대로) — Routine 환경
# 403 이력이 있어 runner 에서도 막힐 가능성을 대비한 graceful 필수 요구.
ECA_ARTICLE_EXCERPT_MAX_CHARS = 1200
ECA_ARTICLE_EXCERPT_FETCH_TIMEOUT = 15
ECA_ARTICLE_EXCERPT_DELAY_SECONDS = 1.0
ECA_ARTICLE_EXCERPT_CAP = 10          # 실행당 fetch 상한(ECA 는 주 소수 항목 — 비용 낮음)

# 표지/머리말 보일러플레이트를 건너뛰고 위반 서술부터 자르기 위한 영문 앵커. 대소문자 무시(re.I).
# 2-tier 선별(2026-06-18): 위반 서술을 직접 가리키는 1차 앵커가 본문에 있으면 그 가장 이른
# 위치를 쓰고, 없을 때만 일반 머리말 폴백 앵커로 내려간다. (종전엔 전 앵커 통합 최이른 위치라
# "this warning letter/violations/cgmp/adulterated" 같은 일반어가 요약 머리말에서 먼저 걸려
# excerpt 앞부분이 보일러플레이트로 시작하던 문제 교정 — CGMP/기록검토/Telehealth 본문 모두.)
_WL_BODY_ANCHORS_PRIMARY = (
    r"during\s+(?:our|an|the)\s+inspection",
    r"(?:significant|specific)\s+violations\s+were\s+observed\s+including",
    r"observed\s+(?:specific\s+)?violations\s+including",
    r"we\s+(?:found|observed)\s+that",
    r"fda\s+observed\s+that",
    r"fda\s+review\s+violations",
    r"specifically,",
)
_WL_BODY_ANCHORS_FALLBACK = (
    r"this\s+warning\s+letter",
    r"\bviolations?\b",
    r"current\s+good\s+manufacturing\s+practice",
    r"\bcgmp\b",
    r"\badulterated\b",
)
# 하위호환: 통합 앵커 집합도 노출(외부 참조 안전망). 추출 선별은 PRIMARY→FALLBACK 순.
_WL_BODY_ANCHORS = _WL_BODY_ANCHORS_PRIMARY + _WL_BODY_ANCHORS_FALLBACK

# [리드인 건너뛰기 2026-07-20] 1차 앵커가 잡는 첫 문장이 정작 **내용이 없는 도입구**인 경우가
# 있다: "During our inspection, our investigators observed specific violations including, but not
# limited to, the following." — "아래와 같다"까지만 말하고 실제 위반은 그 뒤 번호 항목부터다.
# 이 한 문장이 excerpt 앞을 차지하면 하류(card_scaffold.prose_input)의 300자 문장경계 절단이
# **그 도입구 하나만 남기고 실제 위반을 통째로 버린다**(2026-07-20 실측 118자 — 이 때문에 LLM 이
# "세부 위반내용은 원문에 명시되지 않았다"는 거짓 요약을 썼다). 그래서 도입구가 확인되면 그
# **뒤**에서 자른다. 안전규약: 뒤에 실질 본문이 남을 때만 이동한다(아래 _WL_LEADIN_MIN_TAIL).
_WL_LEADIN_RE = re.compile(
    r"violations?\s+(?:were\s+observed\s+)?including,?\s+but\s+not\s+limited\s+to,?"
    r"\s+the\s+following\s*[.:]\s*", re.I)
_WL_LEADIN_MAX_OFFSET = 400   # 도입구는 앵커 직후에 온다 — 그보다 멀면 다른 문장이므로 무시
_WL_LEADIN_MIN_TAIL = 200     # 건너뛴 뒤 남는 본문이 이보다 짧으면 이동하지 않는다(정보 손실 금지)

# Signal Tier 자동 분류 키워드 (lowercase, 단어 경계 매칭 — _kw_match 사용)
# Tier 3 = 최고 신호 (CGMP 강제조치 · nitrosamine · 핵심 ICH), Tier 2 = 일반 GMP/품질 신호
SIGNAL_TIER3_KEYWORDS = [
    "cgmp", "current good manufacturing practice",
    "warning letter", "consent decree", "import alert",
    "annex 1", "ich q12", "ich q13",
    "nitrosamine", "ndma", "ndea", "n-nitroso",
    # 무균·바이오 고신호 (제품군 확장)
    "sterility failure", "non-sterility", "lack of sterility assurance",
    "viral contamination",
]
SIGNAL_TIER2_KEYWORDS = [
    "gmp", "manufacturing practice", "data integrity", "alcoa",
    "process validation", "cleaning validation", "dissolution",
    "out of specification", "oos", "stability", "capa", "deviation",
    "sterile", "aseptic", "supplier qualification", "recall", "class ii",
    # 무균·바이오 GMP/품질 신호 (제품군 확장)
    "media fill", "container closure integrity", "ccit", "biosimilar",
    "comparability", "immunogenicity", "lyophilized", "bioburden",
    "cold chain", "visible particulate",
]

# 무균·바이오 '치명적' 단일 신호 — 1개만 출현해도 Tier 3 floor 적용 (제품군 확장)
# (SIGNAL_TIER3_KEYWORDS 는 2개 매칭을 요구하므로, 단독 출현 시 누락되는 문제 보완)
STERILE_BIO_TIER3_FLOOR = [
    "sterility failure", "non-sterility", "lack of sterility assurance",
    "viral contamination", "media fill failure",
]

# OSD (경구 고형제) Relevance 분류 기준 (v15.1 추가)
OSD_ROUTES = {"oral"}
OSD_FORMS = {
    "tablet", "capsule", "extended-release tablet", "er tablet",
    "delayed-release tablet", "chewable tablet", "orally disintegrating tablet",
    "powder for oral solution", "oral solution", "oral suspension",
}

# ── 제품군(Modality) 분류 (제품군 확장) ───────────────────────────────────────
# 원료(active) 성격을 기준으로 한 '큰 틀' 제품군 태그. 특정 제품이 아닌 클래스 단위.
#   Chemical  = 화학합성(케미컬)의약품   Biologic = 생물의약품   Other = 기타·판별 곤란
# OSD Relevance(경구 고형제 전용)를 제품군 단위로 일반화한 것. 발행 섹션 그룹핑에 사용.
# Notion 의 'Modality' select 속성에 기록(ENABLE_MODALITY_TAG=true 일 때).

FR_PER_PAGE = 100  # API 최대치
OPENFDA_LIMIT = 100  # no-key 한도, key 있어도 안전치
OPENFDA_MAX_TOTAL = 200  # 안전 상한 (의약품 리콜 주간 통상 < 50)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class IntakeItem:
    source: str  # SOURCE_FR | SOURCE_RECALL | SOURCE_EMA | SOURCE_MHRA | SOURCE_PICS | SOURCE_ECA | SOURCE_FDA_WL
    document_id: str
    date_iso: str  # YYYY-MM-DD
    headline: str
    official_url: str
    type_or_class: str = ""
    firm: str = ""
    body: str = ""
    distribution: str = ""
    comments_close_iso: str = ""
    api_query: str = ""
    qa_relevance: str = "Pending"
    osd_relevance: str = "N/A"   # "Direct" | "Indirect" | "N/A" — Recall 전용, 기타 N/A
    source_type: str = SRC_TYPE_OFFICIAL_API  # Source Type 분류 (v15.1)
    signal_tier: str = "Tier 1"  # "Tier 1" | "Tier 2" | "Tier 3" — compute_signal_tier 자동 분류
    raw_payload: dict[str, Any] = field(default_factory=dict)
    # ── Phase 2a 신규 필드 ────────────────────────────────────────────────────
    # 모두 default="" — 기존 FR/Recall 등 코드는 건드리지 않아도 됨
    source_url: str = ""           # 실제 수집/발견 페이지 URL (Search/Scrape 전용)
    raw_excerpt: str = ""          # ≤200자 snippet/excerpt
    search_query: str = ""         # Brave Search 실행 쿼리 (Search 전용)
    evidence_candidate: str = ""   # "A"/"B"/"C"/"D" — 수집기 후보, Routine 최종 판정
    language: str = ""             # KO / EN 등. MFDS Phase 2b부터 사용
    region_jurisdiction: str = ""  # 예: Korea (MFDS)
    site_country: str = ""          # 제조소 소재국. Region/Jurisdiction은 관할기관으로 유지.


@dataclass
class CollectionStats:
    # ── Phase 1 (Official API) ───────────────────────────────────────────────
    fr_fetched: int = 0
    fr_inserted: int = 0
    fr_skipped_dup: int = 0
    fr_insert_failed: int = 0       # Notion 삽입 최종 실패 건수
    fr_truncated: bool = False      # FR pagination 안전 상한 초과 여부
    fr_error: bool = False
    fr_error_msg: str = ""
    recall_fetched: int = 0
    recall_inserted: int = 0
    recall_skipped_dup: int = 0
    recall_insert_failed: int = 0   # Notion 삽입 최종 실패 건수
    recall_truncated: bool = False  # OPENFDA_MAX_TOTAL 상한 초과 여부 (v15.1)
    recall_error: bool = False
    recall_error_msg: str = ""
    # ── Phase 2 (RSS / HTML) ────────────────────────────────────────────────
    ema_fetched: int = 0
    ema_inserted: int = 0
    ema_skipped_dup: int = 0
    ema_insert_failed: int = 0
    ema_error: bool = False
    ema_error_msg: str = ""
    mhra_fetched: int = 0
    mhra_inserted: int = 0
    mhra_skipped_dup: int = 0
    mhra_insert_failed: int = 0
    mhra_error: bool = False
    mhra_error_msg: str = ""
    # [MHRA 회수 커버리지 수리 2026-07-12] 의약품 회수(Class 2/3/4)는 인스펙터 블로그가
    # 아니라 gov.uk/drug-device-alerts 채널(별도 Atom 피드)에 게시된다 — 감사 결과 이 채널을
    # 전혀 안 봐서 회수를 통째로 놓치고 있었다. item.source 는 기존 "MHRA Inspectorate" 를
    # 재사용(신규 Notion Source 옵션·coverage 라벨 추가 불요)하되, 수집 통계는 별도 prefix.
    mhra_alert_fetched: int = 0
    mhra_alert_inserted: int = 0
    mhra_alert_skipped_dup: int = 0
    mhra_alert_insert_failed: int = 0
    mhra_alert_error: bool = False
    mhra_alert_error_msg: str = ""
    pics_fetched: int = 0
    pics_inserted: int = 0
    pics_skipped_dup: int = 0
    pics_insert_failed: int = 0
    pics_error: bool = False
    pics_error_msg: str = ""
    eca_fetched: int = 0
    eca_inserted: int = 0
    eca_skipped_dup: int = 0
    eca_insert_failed: int = 0
    eca_error: bool = False
    eca_error_msg: str = ""
    wl_fetched: int = 0
    wl_inserted: int = 0
    wl_skipped_dup: int = 0
    wl_insert_failed: int = 0
    wl_error: bool = False
    wl_error_msg: str = ""
    # WHY-1 #2 P1: WL 본문 excerpt 관측 — 실패는 graceful(메타 카드 유지)이라 error 가
    # 아니며, _evaluate_health 가 warning 으로만 표면화한다(flag off 면 0 → 무발생).
    wl_body_attempted: int = 0
    wl_body_failed: int = 0
    # ── Phase 2a: Search ────────────────────────────────────────────────────
    search_fetched: int = 0
    search_inserted: int = 0
    search_skipped_dup: int = 0
    search_insert_failed: int = 0
    search_error: bool = False
    search_error_msg: str = ""
    # ── Phase 2b: MFDS ─────────────────────────────────────────────────────
    mfds_fetched: int = 0
    mfds_inserted: int = 0
    mfds_skipped_dup: int = 0
    mfds_insert_failed: int = 0
    mfds_error: bool = False
    mfds_error_msg: str = ""
    mfds_law_fetched: int = 0
    mfds_law_inserted: int = 0
    mfds_law_skipped_dup: int = 0
    mfds_law_insert_failed: int = 0
    mfds_law_error: bool = False
    mfds_law_error_msg: str = ""
    # ── Phase 2c: MFDS Recall / Self-Check ────────────────────────────────
    mfds_recall_fetched: int = 0
    mfds_recall_inserted: int = 0
    mfds_recall_skipped_dup: int = 0
    mfds_recall_insert_failed: int = 0
    mfds_recall_error: bool = False
    mfds_recall_error_msg: str = ""
    mfds_admin_fetched: int = 0
    mfds_admin_inserted: int = 0
    mfds_admin_skipped_dup: int = 0
    mfds_admin_insert_failed: int = 0
    mfds_admin_error: bool = False
    mfds_admin_error_msg: str = ""
    mfds_gmp_cert_fetched: int = 0
    mfds_gmp_cert_inserted: int = 0
    mfds_gmp_cert_skipped_dup: int = 0
    mfds_gmp_cert_insert_failed: int = 0
    mfds_gmp_cert_error: bool = False
    mfds_gmp_cert_error_msg: str = ""
    mfds_safety_letter_fetched: int = 0
    mfds_safety_letter_inserted: int = 0
    mfds_safety_letter_skipped_dup: int = 0
    mfds_safety_letter_insert_failed: int = 0
    mfds_safety_letter_error: bool = False
    mfds_safety_letter_error_msg: str = ""
    mfds_gmp_inspection_fetched: int = 0
    mfds_gmp_inspection_inserted: int = 0
    mfds_gmp_inspection_skipped_dup: int = 0
    mfds_gmp_inspection_insert_failed: int = 0
    mfds_gmp_inspection_error: bool = False
    mfds_gmp_inspection_error_msg: str = ""
    mfds_gmp_inspection_parse_status: dict[str, int] = field(default_factory=dict)
    mfds_gmp_inspection_deficiency: dict[str, int] = field(default_factory=dict)
    mfds_gmp_inspection_manual_review: int = 0
    mfds_gmp_inspection_page_warnings: list[str] = field(default_factory=list)
    # [상세보기 결정론 승격 2026-07-02] 지적 표 추출 관측 — collect_mfds_gmp_inspection.LAST_HEALTH
    # ["deficiency_table"] 집계분(WHOPIR excerpt health 동형). 실패/degrade 는 warning 표면화용.
    gmp_deficiency_table_enabled: bool = False
    gmp_deficiency_table_attempted: int = 0
    gmp_deficiency_table_extracted: int = 0
    gmp_deficiency_table_failed: int = 0
    gmp_deficiency_table_warnings: list[str] = field(default_factory=list)
    # ── P1: ICH (직접 모니터링) ────────────────────────────────────────────
    ich_fetched: int = 0
    ich_inserted: int = 0
    ich_skipped_dup: int = 0
    ich_insert_failed: int = 0
    ich_error: bool = False
    ich_error_msg: str = ""
    # ── P1: WHO Prequalification ───────────────────────────────────────────
    who_fetched: int = 0
    who_inserted: int = 0
    who_skipped_dup: int = 0
    who_insert_failed: int = 0
    who_error: bool = False
    who_error_msg: str = ""
    # WHY-1 #1 P1: WHOPIR excerpt 관측 — collect_who.LAST_HEALTH 집계분. 실패/cap 은
    # graceful(링크 카드 유지)이라 warning 으로만 표면화(flag off 면 0 → 무발생).
    whopir_excerpt_attempted: int = 0
    whopir_excerpt_failed: int = 0
    whopir_excerpt_capped: int = 0   # cap 도달 여부(0/1) — 이후 항목 excerpt 생략 신호
    # ── P1: Health Canada ──────────────────────────────────────────────────
    hc_fetched: int = 0
    hc_inserted: int = 0
    hc_skipped_dup: int = 0
    hc_insert_failed: int = 0
    hc_error: bool = False
    hc_error_msg: str = ""
    # ── WHY-1 #3: FDA 483 ──────────────────────────────────────────────────
    fda483_fetched: int = 0
    fda483_inserted: int = 0
    fda483_skipped_dup: int = 0
    fda483_insert_failed: int = 0
    fda483_error: bool = False
    fda483_error_msg: str = ""
    # P1: 483 excerpt 관측 — collect_fda_483.LAST_HEALTH 집계분. 실패/cap 은 graceful
    # (메타 카드 유지)이라 warning 으로만 표면화(flag off 면 0 → 무발생). source_degraded
    # 는 DataTables AJAX 실패 → 정적 HTML 폴백(부분·완전성 미보장) 신호.
    fda483_excerpt_attempted: int = 0
    fda483_excerpt_failed: int = 0
    fda483_excerpt_capped: int = 0    # cap 도달 여부(0/1)
    fda483_source_degraded: int = 0   # 부분/동결의심 수집 여부(0/1 — 정적 HTML 폴백·stale JSON)
    fda483_backbone: str = "datatables"   # 실사용 백본(datatables|legacy-json|static-html)
    # [FDA 483 상세보기 2026-07-02] Observation 구조 추출 관측 — 실패는 상세만 생략하고
    # 요약카드 유지. ENABLE_FDA_483_OBSERVATIONS=false 면 enabled=false·카운터 0.
    fda483_observations_enabled: bool = False
    fda483_observations_attempted: int = 0
    fda483_observations_extracted: int = 0
    fda483_observations_failed: int = 0
    fda483_observations_warnings: list[str] = field(default_factory=list)
    # ── [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak RSS (ENABLE_ISPE, 기본 off) ──────
    ispe_fetched: int = 0
    ispe_inserted: int = 0
    ispe_skipped_dup: int = 0
    ispe_insert_failed: int = 0
    ispe_error: bool = False
    ispe_error_msg: str = ""
    # ── [EU GMP NCR 2026-07-23] EudraGMDP GMP 비준수 (ENABLE_EU_GMP_NCR, 기본 off) ──────
    eu_gmp_ncr_fetched: int = 0
    eu_gmp_ncr_inserted: int = 0
    eu_gmp_ncr_skipped_dup: int = 0
    eu_gmp_ncr_insert_failed: int = 0
    eu_gmp_ncr_error: bool = False
    eu_gmp_ncr_error_msg: str = ""

    def total_insert_failures(self) -> int:
        return (
            self.fr_insert_failed + self.recall_insert_failed
            + self.ema_insert_failed + self.mhra_insert_failed
            + self.pics_insert_failed + self.eca_insert_failed
            + self.wl_insert_failed + self.search_insert_failed
            + self.mfds_insert_failed + self.mfds_law_insert_failed
            + self.mfds_recall_insert_failed
            + self.mfds_admin_insert_failed + self.mfds_gmp_cert_insert_failed
            + self.mfds_safety_letter_insert_failed
            + self.mfds_gmp_inspection_insert_failed
            + self.ich_insert_failed
            + self.who_insert_failed
            + self.hc_insert_failed
            + self.fda483_insert_failed
            + self.ispe_insert_failed
            + self.eu_gmp_ncr_insert_failed
        )

    def has_insert_failures(self) -> bool:
        return self.total_insert_failures() > 0

    def has_source_errors(self) -> bool:
        """수집기 레벨 오류 여부 (insert 실패와 별개).
        ENABLE_SEARCH=true + BRAVE_API_KEY 누락 같은 misconfiguration도 포함.
        workflow issue 생성 / exit code 판정에 이 메서드를 사용할 것.
        """
        return (
            self.fr_error or self.recall_error or self.ema_error
            or self.mhra_error or self.mhra_alert_error or self.pics_error or self.eca_error
            or self.wl_error
            or self.search_error   # Phase 2a 신규 — misconfiguration 포함
            or self.mfds_error
            or self.mfds_law_error
            or self.mfds_recall_error
            or self.mfds_admin_error
            or self.mfds_gmp_cert_error
            or self.mfds_safety_letter_error
            or self.mfds_gmp_inspection_error
            or self.ich_error
            or self.who_error
            or self.hc_error
            or self.fda483_error
            or self.ispe_error
            or self.eu_gmp_ncr_error
        )

    def summary(self) -> str:
        fr_warn = " ⚠️ TRUNCATED" if self.fr_truncated else ""
        rec_warn = " ⚠️ TRUNCATED" if self.recall_truncated else ""
        lines = [
            f"FR   fetched={self.fr_fetched}  inserted={self.fr_inserted}  "
            f"skip_dup={self.fr_skipped_dup}  failed={self.fr_insert_failed}  "
            f"error={self.fr_error}{fr_warn}",
            f"REC  fetched={self.recall_fetched}  inserted={self.recall_inserted}  "
            f"skip_dup={self.recall_skipped_dup}  failed={self.recall_insert_failed}  "
            f"error={self.recall_error}{rec_warn}",
            f"EMA  fetched={self.ema_fetched}  inserted={self.ema_inserted}  "
            f"skip_dup={self.ema_skipped_dup}  failed={self.ema_insert_failed}  "
            f"error={self.ema_error}",
            f"MHRA fetched={self.mhra_fetched}  inserted={self.mhra_inserted}  "
            f"skip_dup={self.mhra_skipped_dup}  failed={self.mhra_insert_failed}  "
            f"error={self.mhra_error}",
            f"MHRA-ALERT fetched={self.mhra_alert_fetched}  inserted={self.mhra_alert_inserted}  "
            f"skip_dup={self.mhra_alert_skipped_dup}  failed={self.mhra_alert_insert_failed}  "
            f"error={self.mhra_alert_error}",
            f"PICS fetched={self.pics_fetched}  inserted={self.pics_inserted}  "
            f"skip_dup={self.pics_skipped_dup}  failed={self.pics_insert_failed}  "
            f"error={self.pics_error}",
            f"ECA  fetched={self.eca_fetched}  inserted={self.eca_inserted}  "
            f"skip_dup={self.eca_skipped_dup}  failed={self.eca_insert_failed}  "
            f"error={self.eca_error}",
            f"WL   fetched={self.wl_fetched}  inserted={self.wl_inserted}  "
            f"skip_dup={self.wl_skipped_dup}  failed={self.wl_insert_failed}  "
            f"error={self.wl_error}",
            f"SRC  fetched={self.search_fetched}  inserted={self.search_inserted}  "
            f"skip_dup={self.search_skipped_dup}  failed={self.search_insert_failed}  "
            f"error={self.search_error}",
            f"MFDS fetched={self.mfds_fetched}  inserted={self.mfds_inserted}  "
            f"skip_dup={self.mfds_skipped_dup}  failed={self.mfds_insert_failed}  "
            f"error={self.mfds_error}",
            f"MFL  fetched={self.mfds_law_fetched}  inserted={self.mfds_law_inserted}  "
            f"skip_dup={self.mfds_law_skipped_dup}  failed={self.mfds_law_insert_failed}  "
            f"error={self.mfds_law_error}",
            f"MFR  fetched={self.mfds_recall_fetched}  inserted={self.mfds_recall_inserted}  "
            f"skip_dup={self.mfds_recall_skipped_dup}  failed={self.mfds_recall_insert_failed}  "
            f"error={self.mfds_recall_error}",
            f"MFA  fetched={self.mfds_admin_fetched}  inserted={self.mfds_admin_inserted}  "
            f"skip_dup={self.mfds_admin_skipped_dup}  failed={self.mfds_admin_insert_failed}  "
            f"error={self.mfds_admin_error}",
            f"MFC  fetched={self.mfds_gmp_cert_fetched}  inserted={self.mfds_gmp_cert_inserted}  "
            f"skip_dup={self.mfds_gmp_cert_skipped_dup}  failed={self.mfds_gmp_cert_insert_failed}  "
            f"error={self.mfds_gmp_cert_error}",
            f"MFS  fetched={self.mfds_safety_letter_fetched}  "
            f"inserted={self.mfds_safety_letter_inserted}  "
            f"skip_dup={self.mfds_safety_letter_skipped_dup}  "
            f"failed={self.mfds_safety_letter_insert_failed}  "
            f"error={self.mfds_safety_letter_error}",
            f"MFG  fetched={self.mfds_gmp_inspection_fetched}  "
            f"inserted={self.mfds_gmp_inspection_inserted}  "
            f"skip_dup={self.mfds_gmp_inspection_skipped_dup}  "
            f"failed={self.mfds_gmp_inspection_insert_failed}  "
            f"error={self.mfds_gmp_inspection_error}  "
            f"parse={self.mfds_gmp_inspection_parse_status}  "
            f"manual_review={self.mfds_gmp_inspection_manual_review}",
            f"ICH  fetched={self.ich_fetched}  inserted={self.ich_inserted}  "
            f"skip_dup={self.ich_skipped_dup}  failed={self.ich_insert_failed}  "
            f"error={self.ich_error}",
            f"WHO  fetched={self.who_fetched}  inserted={self.who_inserted}  "
            f"skip_dup={self.who_skipped_dup}  failed={self.who_insert_failed}  "
            f"error={self.who_error}",
            f"HC   fetched={self.hc_fetched}  inserted={self.hc_inserted}  "
            f"skip_dup={self.hc_skipped_dup}  failed={self.hc_insert_failed}  "
            f"error={self.hc_error}",
            f"483  fetched={self.fda483_fetched}  inserted={self.fda483_inserted}  "
            f"skip_dup={self.fda483_skipped_dup}  failed={self.fda483_insert_failed}  "
            f"error={self.fda483_error}",
            f"ISPE fetched={self.ispe_fetched}  inserted={self.ispe_inserted}  "
            f"skip_dup={self.ispe_skipped_dup}  failed={self.ispe_insert_failed}  "
            f"error={self.ispe_error}",
            f"EU-NCR fetched={self.eu_gmp_ncr_fetched}  inserted={self.eu_gmp_ncr_inserted}  "
            f"skip_dup={self.eu_gmp_ncr_skipped_dup}  failed={self.eu_gmp_ncr_insert_failed}  "
            f"error={self.eu_gmp_ncr_error}",
        ]
        return "\n".join(lines)


@dataclass(frozen=True)
class RunConfig:
    """main() 실행 1회분의 환경변수·CLI 인자 파싱 결과(불변).

    [리팩토링 배치6 Phase1] 모든 ``ENABLE_*`` 플래그와 값형 env(윈도우·키·경로·dry-run)를
    실행 시작 시 ``from_env(args)`` 로 **1회** 파싱해 담는다. 이후 수집/삽입/handoff/health
    단계는 env 를 재독해하지 않고 이 객체를 참조한다. ``env_flag``/``_env_int`` 를 재사용하며,
    preflight 로 결정되는 *effective* 값(modality/handoff_idem)은 네트워크 의존이라 여기 담지
    않고 main 에서 ``*_requested`` 를 근거로 계산한다.
    """
    # ── CLI 인자 파생 ────────────────────────────────────────────────────────
    dry_run: bool
    window_days: int
    requested_sources: tuple[str, ...]
    explicit_sources: bool
    active: frozenset[str]
    # ── 자격증명 / 키 ────────────────────────────────────────────────────────
    notion_token: str
    notion_db: str
    openfda_key: str | None
    data_go_kr_key: str
    data_go_kr_service_key: str
    law_go_kr_oc: str
    brave_api_key: str
    # ── ENABLE_* 플래그 ──────────────────────────────────────────────────────
    enable_search: bool
    enable_mfds: bool
    enable_mfds_law: bool
    enable_mfds_recall: bool
    enable_mfds_admin: bool
    enable_mfds_gmp_cert: bool
    enable_mfds_safety_letter: bool
    enable_mfds_gmp_inspection: bool
    enable_ich: bool
    enable_who: bool
    enable_hc: bool
    enable_fda483: bool
    enable_fda483_observations: bool
    enable_ispe: bool
    enable_eu_gmp_ncr: bool
    enable_moleg_api: bool
    enable_scrape: bool
    modality_requested: bool
    handoff_idem_requested: bool
    findings_sqlite_append_requested: bool
    findings_sqlite_findings_append_requested: bool
    findings_supabase_append_requested: bool
    findings_supabase_findings_append_requested: bool
    # ── 값형 env / 파생 ──────────────────────────────────────────────────────
    event_name: str
    health_json_path: str
    step_summary_path: str | None
    findings_sqlite_path: str
    findings_supabase_url: str
    findings_supabase_service_key_configured: bool
    mfds_http_proxy_configured: bool
    mfds_enforcement_window_days: int
    handoff_window_days: int

    @classmethod
    def from_env(cls, args: argparse.Namespace) -> "RunConfig":
        requested_sources = tuple(args.sources or _ALL_SOURCES)
        active = (frozenset() if "none" in requested_sources
                  else frozenset(requested_sources))
        return cls(
            dry_run=args.dry_run,
            window_days=args.window_days,
            requested_sources=requested_sources,
            explicit_sources=args.sources is not None,
            active=active,
            notion_token=os.environ.get("NOTION_TOKEN", "").strip(),
            notion_db=os.environ.get("NOTION_DATABASE_ID", "").strip(),
            openfda_key=os.environ.get("OPENFDA_API_KEY", "").strip() or None,
            data_go_kr_key=os.environ.get("DATA_GO_KR_KEY", "").strip(),
            data_go_kr_service_key=os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip(),
            law_go_kr_oc=os.environ.get("LAW_GO_KR_OC", "").strip(),
            brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
            enable_search=env_flag("ENABLE_SEARCH"),
            enable_mfds=env_flag("ENABLE_MFDS"),
            enable_mfds_law=env_flag("ENABLE_MFDS_LAW"),
            enable_mfds_recall=env_flag("ENABLE_MFDS_RECALL"),
            enable_mfds_admin=env_flag("ENABLE_MFDS_ADMIN"),
            enable_mfds_gmp_cert=env_flag("ENABLE_MFDS_GMP_CERT"),
            enable_mfds_safety_letter=env_flag("ENABLE_MFDS_SAFETY_LETTER"),
            enable_mfds_gmp_inspection=env_flag("ENABLE_MFDS_GMP_INSPECTION"),
            enable_ich=env_flag("ENABLE_ICH") or "ich" in active,
            enable_who=env_flag("ENABLE_WHO") or "who" in active,
            enable_hc=env_flag("ENABLE_HC") or "hc" in active,
            enable_fda483=env_flag("ENABLE_FDA_483") or "fda483" in active,
            enable_fda483_observations=env_flag("ENABLE_FDA_483_OBSERVATIONS"),
            enable_ispe=env_flag("ENABLE_ISPE") or "ispe" in active,
            enable_eu_gmp_ncr=env_flag("ENABLE_EU_GMP_NCR") or "eu_gmp_ncr" in active,
            enable_moleg_api=env_flag("ENABLE_MOLEG_API"),
            enable_scrape=env_flag("ENABLE_SCRAPE"),
            modality_requested=env_flag("ENABLE_MODALITY_TAG"),
            handoff_idem_requested=env_flag("ENABLE_HANDOFF_IDEMPOTENCY_V2"),
            findings_sqlite_append_requested=env_flag("ENABLE_FINDINGS_SQLITE_APPEND"),
            findings_sqlite_findings_append_requested=env_flag("ENABLE_FINDINGS_SQLITE_FINDINGS_APPEND"),
            findings_supabase_append_requested=env_flag("ENABLE_FINDINGS_SUPABASE_APPEND"),
            findings_supabase_findings_append_requested=env_flag("ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND"),
            event_name=os.environ.get("GRM_EVENT_NAME", "").strip(),
            health_json_path=os.environ.get("GRM_HEALTH_JSON", "grm-health.json").strip(),
            step_summary_path=os.environ.get("GITHUB_STEP_SUMMARY"),
            findings_sqlite_path=(
                os.environ.get("GRM_FINDINGS_SQLITE_PATH", DEFAULT_FINDINGS_SQLITE_PATH).strip()
                or DEFAULT_FINDINGS_SQLITE_PATH
            ),
            findings_supabase_url=os.environ.get("SUPABASE_URL", "").strip(),
            findings_supabase_service_key_configured=bool(
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            ),
            mfds_http_proxy_configured=bool(os.environ.get("MFDS_HTTP_PROXY", "").strip()),
            mfds_enforcement_window_days=max(
                args.window_days, _env_int("MFDS_ENFORCEMENT_WINDOW_DAYS", 30)),
            handoff_window_days=resolve_handoff_window_days(args.handoff_window_days),
        )


# ─────────────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────────────


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def kst_run_date(now: datetime | None = None) -> date:
    """KST 기준 '오늘' 의 자정 날짜."""
    return (now or now_kst()).astimezone(KST).date()


def date_window(run_date: date, window_days: int = 7) -> tuple[date, date]:
    start = run_date - timedelta(days=window_days)
    return start, run_date


# ─────────────────────────────────────────────────────────────────────────────
# RSS / Atom / HTML 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

# XML 네임스페이스
_NS_ATOM  = "http://www.w3.org/2005/Atom"
_NS_DC    = "http://purl.org/dc/elements/1.1/"
_NS_DCTERMS = "http://purl.org/dc/terms/"
_NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"


def _rss_text(el: ET.Element | None) -> str:
    """ElementTree 텍스트 안전 추출."""
    if el is None:
        return ""
    return (el.text or "").strip()


def _rss_find(parent: ET.Element, *tags: str) -> ET.Element | None:
    """네임스페이스 없는 태그 및 네임스페이스 조합 순차 탐색."""
    for tag in tags:
        el = parent.find(tag)
        if el is not None:
            return el
    return None


def _parse_rss2_date(raw: str) -> str:
    """RFC 2822 (RSS 2.0) 날짜 → YYYY-MM-DD. 실패 시 "" 반환."""
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.date().isoformat()
    except Exception:
        pass
    # 일부 피드가 ISO 8601 을 쓰는 경우 폴백
    return _parse_atom_date(raw)


def _parse_atom_date(raw: str) -> str:
    """Atom/ISO 8601 날짜 → YYYY-MM-DD. 실패 시 "" 반환."""
    if not raw:
        return ""
    # 공통 패턴: 2024-03-15T12:00:00Z / 2024-03-15T12:00:00+00:00 / 2024-03-15
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:25], fmt).date().isoformat()
        except ValueError:
            continue
    # fromisoformat (Python 3.11+) 폴백
    try:
        return datetime.fromisoformat(raw[:25].rstrip("Z")).date().isoformat()
    except ValueError:
        pass
    log("WARN", f"날짜 파싱 실패 (atom): {raw!r}")
    return ""


def _stable_doc_id(source: str, title: str, url: str, date_iso: str) -> str:
    """RSS 항목에 고유 document_id 가 없을 경우 콘텐츠 기반 안정 ID 생성.

    SHA-1 상위 12자리 사용 — URL+제목+날짜 조합이므로 동일 항목은 동일 ID 를 가진다.
    dedupe 목적에 충분한 고유성을 제공한다.
    """
    key = f"{source}|{url}|{title}|{date_iso}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _within_window(date_iso: str, start: date, end: date) -> bool:
    """date_iso (YYYY-MM-DD) 가 [start, end] 구간에 포함 여부."""
    if not date_iso:
        return False
    try:
        d = date.fromisoformat(date_iso)
        return start <= d <= end
    except ValueError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# RSS 수집 공통 파서
# ─────────────────────────────────────────────────────────────────────────────


def _rss2_items_from_root(root: ET.Element) -> list[ET.Element]:
    """RSS 2.0 channel → item 리스트. RDF(rss/1.0) 도 처리."""
    channel = root.find("channel")
    if channel is not None:
        return channel.findall("item")
    return root.findall("item")


def _atom_entries_from_root(root: ET.Element) -> list[ET.Element]:
    """Atom feed → entry 리스트. 네임스페이스 있는 경우 처리."""
    entries = root.findall(f"{{{_NS_ATOM}}}entry")
    if entries:
        return entries
    return root.findall("entry")


def _atom_text(entry: ET.Element, tag: str) -> str:
    """Atom 네임스페이스 포함 텍스트 추출 (네임스페이스 있는 버전, 없는 버전 모두)."""
    el = _rss_find(entry, f"{{{_NS_ATOM}}}{tag}", tag)
    if el is None:
        return ""
    # <content> / <summary> 등의 type="html" 처리
    text = (el.text or "").strip()
    # HTML 태그 간단 제거
    return re.sub(r"<[^>]+>", " ", text).strip()


def _atom_link(entry: ET.Element) -> str:
    """Atom <link href="..."> 추출."""
    # href 속성을 가진 link 먼저
    for link in entry.findall(f"{{{_NS_ATOM}}}link"):
        href = link.get("href", "")
        if href and link.get("rel", "alternate") == "alternate":
            return href
    for link in entry.findall(f"{{{_NS_ATOM}}}link"):
        href = link.get("href", "")
        if href:
            return href
    # 네임스페이스 없는 버전
    el = entry.find("link")
    return _rss_text(el)


# ─────────────────────────────────────────────────────────────────────────────
# QA Relevance 휴리스틱
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Federal Register 수집
# ─────────────────────────────────────────────────────────────────────────────
# OSD Relevance 분류 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# 제품군(Modality) 분류 (제품군 확장)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Signal Tier 자동 분류 (v15.x Phase 1)
# ─────────────────────────────────────────────────────────────────────────────


def compute_signal_tier(source: str, type_or_class: str, qa_relevance: str,
                        osd_relevance: str, *text_parts: str) -> str:
    """수집 항목의 Signal Tier (Tier 1/2/3) 1차 자동 분류.

    최종 판정은 Routine 에 위임하되, 고신호 항목을 우선 노출하기 위한 휴리스틱.
        Tier 3 — 즉시 검토 가치가 높은 강제조치/핵심 GMP 신호
        Tier 2 — GMP/품질 관련 신호
        Tier 1 — 기본 (기타)

    인자:
        source         : SOURCE_* 상수
        type_or_class  : Recall classification("Class I"…) / FR type("Rule"…) / 카테고리
        qa_relevance   : compute_relevance 결과 ("Likely" 등)
        osd_relevance  : compute_osd_relevance 결과 ("Direct" 등)
        text_parts     : 키워드 매칭 대상 텍스트 (제목·본문·카테고리 등)
    """
    blob = " ".join(t for t in text_parts if t).lower()
    type_lc = (type_or_class or "").lower()

    # Recall classification — 단어 경계로 Class I / II / III 정확히 구분
    is_class_i = source == SOURCE_RECALL and re.search(r"\bclass i\b", type_lc) is not None
    is_class_ii = source == SOURCE_RECALL and re.search(r"\bclass ii\b", type_lc) is not None
    is_fr_rule = source == SOURCE_FR and "rule" in type_lc

    t3_matches = _kw_match(blob, SIGNAL_TIER3_KEYWORDS)
    t2_matches = _kw_match(blob, SIGNAL_TIER2_KEYWORDS)

    # ── Tier 3 ─────────────────────────────────────────────────────────────
    if source == SOURCE_FDA_WL and _kw_any(
            blob, ["cgmp", "current good manufacturing practice"]):
        return "Tier 3"
    if is_class_i:
        return "Tier 3"
    # 제외 도메인(QA Unrelated: 의료기기·식품·화장품·수의 등)은 위 강제 예외(Class I·FDA WL cGMP)
    # 외에는 키워드로 Tier 2/3 승격하지 않고 Tier 1 로 고정(handoff·통계 노이즈 방지).
    if qa_relevance == "Unrelated":
        return "Tier 1"
    if osd_relevance == "Direct" and _kw_any(
            blob, ["dissolution", "nitrosamine", "subpotent"]):
        return "Tier 3"
    # 무균·바이오 치명적 단일 신호는 1개만 있어도 Tier 3 (floor)
    # 단, QA 관련성이 Unrelated(의료기기·식품 등 제외 도메인)인 항목은 승격하지 않는다.
    if qa_relevance != "Unrelated" and _kw_any(blob, STERILE_BIO_TIER3_FLOOR):
        return "Tier 3"
    if t3_matches >= 2:
        return "Tier 3"
    if is_fr_rule and t3_matches >= 1:
        return "Tier 3"

    # ── Tier 2 ─────────────────────────────────────────────────────────────
    if qa_relevance == "Likely":
        return "Tier 2"
    if is_class_ii:
        return "Tier 2"
    if t2_matches >= 1:
        return "Tier 2"
    if osd_relevance == "Direct":
        return "Tier 2"

    return "Tier 1"


# ─────────────────────────────────────────────────────────────────────────────
# 날짜 유틸
# ─────────────────────────────────────────────────────────────────────────────


def _safe_date_iso(value: str, context: str = "") -> str:
    """날짜 문자열 검증 후 YYYY-MM-DD 반환. 실패 시 "" 반환 + WARN 로그.

    지원 포맷:
        - YYYY-MM-DD (ISO 8601)
        - YYYYMMDD   (OpenFDA report_date 형식) → 자동 변환
    """
    if not value:
        return ""
    # YYYYMMDD → YYYY-MM-DD
    if len(value) == 8 and value.isdigit():
        value = f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    try:
        datetime.fromisoformat(value)
        return value
    except ValueError:
        log("WARN", f"날짜 파싱 실패 (context={context}): {value!r} → 빈 문자열 처리")
        return ""


# ─────────────────────────────────────────────────────────────────────────────


def collect_federal_register(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA Federal Register 문서 지난 7 일 전수 수집 (pagination 처리)."""
    params: dict[str, Any] = {
        "conditions[agencies][]": "food-and-drug-administration",
        "conditions[publication_date][gte]": start.isoformat(),
        "conditions[publication_date][lte]": end.isoformat(),
        "per_page": FR_PER_PAGE,
        "order": "newest",
    }
    api_query_url = FR_API_BASE + "?" + urllib.parse.urlencode(params, doseq=True)
    log("INFO", f"FR API 호출: {api_query_url}")

    items: list[IntakeItem] = []
    next_url: str | None = api_query_url
    page_count = 0
    try:
        while next_url:
            page_count += 1
            if page_count > 10:
                msg = (f"FR pagination 10페이지 상한 초과 — truncated "
                       f"(수집 {len(items)}건, 이후 항목 누락 가능)")
                log("WARN", msg)
                return items, msg   # fr_error=True 로 집계됨
            data = http_get_json(next_url)
            results = data.get("results", []) or []
            log("INFO", f"FR page {page_count}: {len(results)} 건")
            for r in results:
                items.append(_fr_to_item(r, api_query_url))
            next_url = data.get("next_page_url")
        return items, None
    except Exception as e:
        log("ERROR", f"FR 수집 실패: {e}")
        return items, str(e)


def _fr_to_item(r: dict[str, Any], api_query_url: str) -> IntakeItem:
    doc_id = str(r.get("document_number") or "").strip()
    title = (r.get("title") or "").strip()
    pub = _safe_date_iso((r.get("publication_date") or "").strip(),
                         context=f"FR/{doc_id}")
    html_url = (r.get("html_url") or "").strip()
    doc_type = (r.get("type") or "").strip()
    abstract = (r.get("abstract") or "").strip()
    comments_close = _safe_date_iso((r.get("comments_close_on") or "").strip(),
                                    context=f"FR/{doc_id}/comments_close")

    relevance = compute_relevance(title, abstract, doc_type)
    tier = compute_signal_tier(SOURCE_FR, doc_type, relevance, "N/A",
                               title, abstract, doc_type)

    return IntakeItem(
        source=SOURCE_FR,
        document_id=doc_id,
        date_iso=pub,
        headline=title,
        official_url=html_url,
        type_or_class=doc_type,
        body=abstract,
        comments_close_iso=comments_close,
        api_query=api_query_url,
        qa_relevance=relevance,
        # FR 항목은 OSD Relevance 분류 대상 아님
        osd_relevance="N/A",
        signal_tier=tier,
        raw_payload=r,
    )


# ─────────────────────────────────────────────────────────────────────────────
# OpenFDA Recall 수집
# ─────────────────────────────────────────────────────────────────────────────


def collect_openfda_recalls(start: date, end: date,
                            api_key: str | None) -> tuple[list[IntakeItem], str | None]:
    """OpenFDA Drug Enforcement 항목 지난 7 일 전수 수집."""
    search = f"report_date:[{start.strftime('%Y%m%d')}+TO+{end.strftime('%Y%m%d')}]"
    items: list[IntakeItem] = []
    skip = 0
    api_query_url_for_log: str | None = None
    try:
        while True:
            params = [
                ("search", search),
                ("limit", str(OPENFDA_LIMIT)),
                ("skip", str(skip)),
            ]
            if api_key:
                params.append(("api_key", api_key))
            url = OPENFDA_API_BASE + "?" + urllib.parse.urlencode(params, safe=":[]+")
            if api_query_url_for_log is None:
                # api_key 는 로그·Notion 저장에서 마스킹
                api_query_url_for_log = _mask_api_key(url)
                log("INFO", f"OpenFDA 호출: {api_query_url_for_log}")
            try:
                data = http_get_json(url)
            except HTTPClientError as e:
                # OpenFDA 는 해당 기간 결과 0건일 때 404 를 반환하는 것이 관행
                if e.status_code == 404:
                    log("INFO", "OpenFDA 404 (해당 기간 결과 0건) — 정상 종료")
                    return items, None
                # 404 외 4xx (401, 403 등) 는 실제 에러로 처리
                raise RuntimeError(str(e)) from e
            results = data.get("results", []) or []
            meta_total = (data.get("meta", {}).get("results", {}) or {}).get("total", 0)
            log("INFO", f"OpenFDA skip={skip}: {len(results)} 건 (total {meta_total})")
            for r in results:
                items.append(_recall_to_item(r, api_query_url_for_log or url))
            skip += len(results)
            if not results or skip >= meta_total:
                break
            if skip >= OPENFDA_MAX_TOTAL:
                msg = (f"OpenFDA OPENFDA_MAX_TOTAL({OPENFDA_MAX_TOTAL}) 상한 초과 — truncated "
                       f"(수집 {len(items)}건, meta.total={meta_total}, 이후 항목 누락 가능)")
                log("WARN", msg)
                return items, msg   # recall_truncated=True 로 집계됨
        return items, None
    except Exception as e:
        log("ERROR", f"OpenFDA 수집 실패: {e}")
        return items, str(e)


def _mask_api_key(url: str) -> str:
    return re.sub(r"(api_key=)[^&]+", r"\1***REDACTED***", url)


def _recall_to_item(r: dict[str, Any], api_query_url: str) -> IntakeItem:
    recall_number = str(r.get("recall_number") or "").strip()
    classification = (r.get("classification") or "").strip()
    product = (r.get("product_description") or "").strip()
    reason = (r.get("reason_for_recall") or "").strip()
    firm = (r.get("recalling_firm") or "").strip()
    distribution = (r.get("distribution_pattern") or "").strip()
    report_date_raw = (r.get("report_date") or "").strip()  # YYYYMMDD
    product_type = (r.get("product_type") or "").strip()

    # report_date YYYYMMDD → YYYY-MM-DD (_safe_date_iso 가 변환 + 검증 처리)
    date_iso = _safe_date_iso(report_date_raw, context=f"Recall/{recall_number}")

    headline = product or firm or recall_number
    relevance = compute_relevance(product, reason, firm, distribution, product_type)
    osd_rel = compute_osd_relevance(r)
    tier = compute_signal_tier(SOURCE_RECALL, classification, relevance, osd_rel,
                               product, reason, firm, distribution, product_type)

    return IntakeItem(
        source=SOURCE_RECALL,
        document_id=recall_number,
        date_iso=date_iso,
        headline=headline,
        official_url=FDA_RECALLS_L2,  # OpenFDA 는 항목별 URL 부재 — L2 고정
        type_or_class=classification,
        firm=firm,
        body=reason,
        distribution=distribution,
        api_query=api_query_url,
        qa_relevance=relevance,
        osd_relevance=osd_rel,
        signal_tier=tier,
        raw_payload=r,
    )


# ─────────────────────────────────────────────────────────────────────────────
# RSS 수집기 제네릭 (배치6 Phase3) — EMA · MHRA · PIC/S · ECA
# ─────────────────────────────────────────────────────────────────────────────
# 4종 RSS/Atom 수집기의 공통 골격(fetch 루프·윈도우 필터·doc_id·relevance·tier·IntakeItem
# 조립)을 collect_rss_feed(spec) 하나로 통합한다. 소스별로 상이한 부분(피드 목록·item 반복자·
# 필드 추출·raw_payload·type_or_class·source_type)만 RssFeedSpec + per-source extractor 로
# 분리했다. **extractor 는 기존 각 수집기의 추출 로직을 그대로 옮긴 것**이라 doc_id/dedup 키를
# 포함한 산출 IntakeItem 이 입력과 무관하게 byte 동일하다(전면 재적재 방지 절대선).
#
# 새 RSS 소스 추가: extractor 1개 + RssFeedSpec 1개 + 얇은 공개 함수 1개.


@dataclass(frozen=True)
class _RssItemFields:
    """추출기가 반환하는 정규화 필드(수집기 공통 tail 이 IntakeItem 으로 조립)."""
    title: str
    link: str
    date_iso: str
    description: str
    category: str          # raw category (uses_category=True 소스만 relevance/tier 에 사용)
    type_or_class: str     # == compute_signal_tier 의 context 인자 (4종 모두 동일값)
    guid: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class RssFeedSpec:
    source: str            # SOURCE_* (doc_id/dedup 키의 일부)
    source_type: str       # SRC_TYPE_*
    label: str             # 로그 라벨 ("EMA RSS" 등)
    feeds: tuple[tuple[str, str], ...]       # (feed_name, url) — 단일피드는 1항
    iter_items: Callable[[ET.Element], list[ET.Element]]
    extract: Callable[[ET.Element, str, str], "_RssItemFields"]
    uses_category: bool = False       # relevance/tier 에 category 전달(EMA·MHRA)
    accumulate_errors: bool = False   # 멀티피드 누적(EMA); False=첫 실패 즉시 반환
    http_silent: bool = False         # HTTPClientError 를 경고없이 skip(ECA Expert Secondary)
    # 윈도우 통과 후 category 등으로 항목을 걸러내는 선택 훅(예: MHRA alerts 에서 의료기기
    # FSN 을 버리고 의약품 회수/결함만 채택). None(기본)이면 전건 채택 — 기존 4종 스펙은
    # 이 필드 미지정이라 산출 IntakeItem 이 byte 동일하게 유지된다.
    keep_item: Callable[["_RssItemFields"], bool] | None = None


def collect_rss_feed(spec: RssFeedSpec, start: date,
                     end: date) -> tuple[list[IntakeItem], str | None]:
    """RssFeedSpec 하나로 RSS/Atom 피드를 수집해 IntakeItem 리스트를 반환한다(배치6 Phase3)."""
    items: list[IntakeItem] = []
    errors: list[str] = []
    for feed_name, feed_url in spec.feeds:
        if spec.accumulate_errors:
            log("INFO", f"{spec.label} 수집: {feed_name} ({feed_url})")
        else:
            log("INFO", f"{spec.label} 수집: {feed_url}")
        try:
            root = http_get_xml(feed_url)
        except Exception as e:
            # http_silent(ECA Expert Secondary) 은 HTTPClientError 만 경고 없이 skip.
            # 그 외에는 HTTPClientError 를 일반 예외와 동일 처리해야 한다 — 원 EMA 수집기가
            # HTTPClientError 를 bare ``except Exception`` 로 잡아 accumulate+continue 했기
            # 때문(멀티피드 소스에서 한 피드의 4xx/429 가 나머지 피드를 죽이면 안 됨).
            if spec.http_silent and isinstance(e, HTTPClientError):
                # Expert Secondary: 403/404 는 경고 없이 넘어감
                log("INFO", f"{spec.label} HTTP {e.status_code} — 건너뜀 (Expert Secondary 정책)")
                return [], None
            if spec.accumulate_errors:
                msg = f"{spec.label} '{feed_name}' 실패: {e}"
                log("WARN", msg)
                errors.append(msg)
                continue
            log("WARN", f"{spec.label} 실패: {e}")
            return [], str(e)

        for el in spec.iter_items(root):
            f = spec.extract(el, feed_name, feed_url)
            if not _within_window(f.date_iso, start, end):
                continue
            if spec.keep_item is not None and not spec.keep_item(f):
                continue
            doc_id = _stable_doc_id(spec.source, f.title, f.link, f.date_iso)
            if spec.uses_category:
                relevance = compute_relevance(f.title, f.description, f.category)
                tier = compute_signal_tier(spec.source, f.type_or_class, relevance,
                                           "N/A", f.title, f.description, f.category)
            else:
                relevance = compute_relevance(f.title, f.description)
                tier = compute_signal_tier(spec.source, f.type_or_class, relevance,
                                           "N/A", f.title, f.description)
            items.append(IntakeItem(
                source=spec.source,
                document_id=doc_id,
                date_iso=f.date_iso,
                headline=f.title,
                official_url=f.link,
                type_or_class=f.type_or_class,
                body=f.description,
                api_query=feed_url,
                qa_relevance=relevance,
                osd_relevance="N/A",
                source_type=spec.source_type,
                signal_tier=tier,
                raw_payload=f.raw_payload,
            ))

    if spec.accumulate_errors:
        # 오류가 있어도 다른 피드에서 수집한 항목은 반환 (graceful degradation)
        log("INFO", f"{spec.label} 수집 완료: {len(items)}건 (errors={len(errors)})")
        return items, ("; ".join(errors) if errors else None)
    log("INFO", f"{spec.label} 수집 완료: {len(items)}건")
    return items, None


def _rss2_or_atom_items(root: ET.Element) -> list[ET.Element]:
    """ECA: RSS 2.0 우선, 비면 Atom 폴백."""
    rss_items = _rss2_items_from_root(root)
    if not rss_items:
        rss_items = _atom_entries_from_root(root)
    return rss_items


def _extract_ema(el: ET.Element, feed_name: str, feed_url: str) -> _RssItemFields:
    title = _rss_text(el.find("title"))
    link  = _rss_text(el.find("link"))
    # <link> 가 CDATA 로 감싸진 경우 텍스트에 없고 tail 에 있을 수 있음
    if not link:
        link_el = el.find("link")
        if link_el is not None:
            link = (link_el.tail or "").strip()
    pub_raw = _rss_text(el.find("pubDate")) or _rss_text(el.find("pubdate"))
    # EMA 피드에 따라 dc:date 를 fallback 으로 사용
    if not pub_raw:
        dc_date = _rss_find(el, f"{{{_NS_DC}}}date", f"{{{_NS_DCTERMS}}}modified")
        pub_raw = _rss_text(dc_date)
    date_iso = _parse_rss2_date(pub_raw) if pub_raw else ""
    # dc:date 가 Atom 형식인 경우 재시도
    if not date_iso and pub_raw:
        date_iso = _parse_atom_date(pub_raw)
    description = _rss_text(el.find("description"))
    category_el = el.find("category")
    category = _rss_text(category_el)
    guid_el = el.find("guid")
    guid = _rss_text(guid_el) or link
    return _RssItemFields(
        title=title, link=link, date_iso=date_iso, description=description,
        category=category, type_or_class=category or feed_name, guid=guid,
        raw_payload={
            "feed": feed_name,
            "title": title,
            "link": link,
            "pubDate": pub_raw,
            "description": description,
            "category": category,
            "guid": guid,
        },
    )


def _extract_mhra(entry: ET.Element, feed_name: str, feed_url: str) -> _RssItemFields:
    title   = _atom_text(entry, "title")
    link    = _atom_link(entry)
    # Atom: <updated> 또는 <published>
    pub_raw = (
        _rss_text(entry.find(f"{{{_NS_ATOM}}}published"))
        or _rss_text(entry.find(f"{{{_NS_ATOM}}}updated"))
        or _rss_text(entry.find("published"))
        or _rss_text(entry.find("updated"))
    )
    date_iso = _parse_atom_date(pub_raw)
    summary  = _atom_text(entry, "summary") or _atom_text(entry, "content")
    cat_el = _rss_find(entry, f"{{{_NS_ATOM}}}category", "category")
    category = (cat_el.get("term", "") if cat_el is not None else "").strip()
    id_el = _rss_find(entry, f"{{{_NS_ATOM}}}id", "id")
    guid  = _rss_text(id_el) or link
    return _RssItemFields(
        title=title, link=link, date_iso=date_iso, description=summary,
        category=category, type_or_class=category or "Blog", guid=guid,
        raw_payload={
            "title": title, "link": link,
            "published": pub_raw, "summary": summary,
            "category": category, "id": guid,
        },
    )


# gov.uk drug-device-alerts 는 의약품 회수/결함과 의료기기 경보(FSN)가 한 피드에 섞여 있다.
# category term 이 의약품(medicine) 회수/결함인 항목만 채택하고, 순수 의료기기 경보(Field
# Safety Notice·Device)와 월간 Safety Roundup 요약은 버린다(감사 목표=의약품 회수 누락 차단).
_MHRA_ALERT_KEEP_RE = re.compile(r"medicines?\s+(recall|defect)", re.I)
_MHRA_ALERT_DROP_RE = re.compile(r"field\s+safety|device|safety\s+roundup", re.I)


def _is_mhra_medicines_alert(f: "_RssItemFields") -> bool:
    """의약품 회수/결함만 True. category term 우선, 없으면 제목으로 판별."""
    haystack = f"{f.category} {f.title}"
    if _MHRA_ALERT_KEEP_RE.search(haystack):
        return True
    # category 가 명시적 device/FSN/roundup 이면 확실히 제외
    if _MHRA_ALERT_DROP_RE.search(haystack):
        return False
    # 애매하면 제외(과수집보다 정밀도 우선 — 회수 category 는 항상 "... Medicines Recall" 형식)
    return False


def _extract_mhra_alert(entry: ET.Element, feed_name: str, feed_url: str) -> _RssItemFields:
    # gov.uk finder Atom 은 MHRA Inspectorate 블로그와 동일한 Atom 구조라 _extract_mhra 를
    # 재사용하되, type_or_class 를 category(예 "Class 2 Medicines Recall")로 남겨 Recall 성격을
    # 신호한다(블로그의 "Blog" fallback 과 구분).
    f = _extract_mhra(entry, feed_name, feed_url)
    return _RssItemFields(
        title=f.title, link=f.link, date_iso=f.date_iso, description=f.description,
        category=f.category, type_or_class=f.category or "Medicines Recall",
        guid=f.guid, raw_payload=f.raw_payload,
    )


def _extract_pics(el: ET.Element, feed_name: str, feed_url: str) -> _RssItemFields:
    title   = _rss_text(el.find("title"))
    link    = _rss_text(el.find("link"))
    pub_raw = _rss_text(el.find("pubDate")) or _rss_text(el.find("pubdate"))
    date_iso = _parse_rss2_date(pub_raw) if pub_raw else ""
    description = _rss_text(el.find("description"))
    guid_el = el.find("guid")
    guid    = _rss_text(guid_el) or link
    return _RssItemFields(
        title=title, link=link, date_iso=date_iso, description=description,
        category="", type_or_class="PIC/S", guid=guid,
        raw_payload={
            "title": title, "link": link,
            "pubDate": pub_raw, "description": description, "guid": guid,
        },
    )


def _extract_eca(el: ET.Element, feed_name: str, feed_url: str) -> _RssItemFields:
    # RSS 2.0 태그 우선, Atom 폴백
    title = (
        _rss_text(el.find("title"))
        or _atom_text(el, "title")
    )
    link = (
        _rss_text(el.find("link"))
        or _atom_link(el)
    )
    pub_raw = (
        _rss_text(el.find("pubDate"))
        or _rss_text(el.find("pubdate"))
        or _rss_text(el.find(f"{{{_NS_ATOM}}}published"))
        or _rss_text(el.find("published"))
    )
    date_iso = (
        _parse_rss2_date(pub_raw) if pub_raw
        else _parse_atom_date(pub_raw)
    )
    description = (
        _rss_text(el.find("description"))
        or _atom_text(el, "summary")
    )
    guid_el = el.find("guid")
    guid    = _rss_text(guid_el) or link
    return _RssItemFields(
        title=title, link=link, date_iso=date_iso, description=description,
        category="", type_or_class="GMP News", guid=guid,
        raw_payload={
            "title": title, "link": link,
            "pubDate": pub_raw, "description": description, "guid": guid,
        },
    )


# [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak(Drupal) RSS teaser <description> 정제.
# 실측 구조: 저자 span·<time>·story-type div("iSpeak Blog")·<h1> 제목 중복·배너 <img> 가
# 섞인 노드 teaser HTML 전체이며, 실제 요약문은 field--name-field-description div 안에 있다.
_ISPE_FIELD_DESCRIPTION_RE = re.compile(
    r'(?is)<div[^>]*class="[^"]*field--name-field-description[^"]*"[^>]*>(.*?)</div>'
)


def _ispe_teaser_text(html: str) -> str:
    """ISPE Drupal teaser HTML → 정제 요약 텍스트(≤800자, 순수 함수·무의존).

    1) field--name-field-description div 내부 텍스트를 regex 로 우선 추출(dotall·태그
       제거·`_html_unescape`·공백 collapse) — 실제 요약문 위치.
    2) 미발견 시 전체에서 태그 제거 후 정제하는 폴백. 저자명/날짜/story-type("iSpeak
       Blog")/제목 중복이 앞쪽에 섞일 수 있으나, 이 폴백 경로는 완벽 제거를 시도하지
       않는다(보수적·결정론 우선) — 800자 절단으로 노이즈 비중을 낮춘다.
    """
    text = html or ""
    m = _ISPE_FIELD_DESCRIPTION_RE.search(text)
    if m:
        inner = re.sub(r"(?s)<[^>]+>", " ", m.group(1))
        inner = _html_unescape(inner)
        inner = re.sub(r"\s+", " ", inner).strip()
        if inner:
            return inner[:800].strip()
    fallback = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    fallback = re.sub(r"(?s)<[^>]+>", " ", fallback)
    fallback = _html_unescape(fallback)
    fallback = re.sub(r"\s+", " ", fallback).strip()
    return fallback[:800].strip()


def _extract_ispe(el: ET.Element, feed_name: str, feed_url: str) -> _RssItemFields:
    # RSS 2.0 태그 우선, Atom 폴백(_extract_eca 와 동형 — ISPE 는 실측상 RSS2 전용이나
    # 방어적으로 동일 패턴 유지).
    title = (
        _rss_text(el.find("title"))
        or _atom_text(el, "title")
    )
    link = (
        _rss_text(el.find("link"))
        or _atom_link(el)
    )
    pub_raw = (
        _rss_text(el.find("pubDate"))
        or _rss_text(el.find("pubdate"))
        or _rss_text(el.find(f"{{{_NS_ATOM}}}published"))
        or _rss_text(el.find("published"))
    )
    date_iso = (
        _parse_rss2_date(pub_raw) if pub_raw
        else _parse_atom_date(pub_raw)
    )
    description_raw = (
        _rss_text(el.find("description"))
        or _atom_text(el, "summary")
    )
    description = _ispe_teaser_text(description_raw)
    guid_el = el.find("guid")
    guid    = _rss_text(guid_el) or link
    return _RssItemFields(
        title=title, link=link, date_iso=date_iso, description=description,
        category="", type_or_class="GMP News", guid=guid,
        raw_payload={
            "title": title, "link": link,
            "pubDate": pub_raw, "description": description, "guid": guid,
        },
    )


def _is_ispe_gmp_relevant(f: _RssItemFields) -> bool:
    """ISPE iSpeak keep_item — 협회 홍보성 항목(Board of Directors 후보 소개·Member
    Spotlight·Affiliate 소식·컨퍼런스 홍보 등, 실측 약 55%)을 걸러내고 GMP/품질 관련
    항목(water system GMP·batch disposition·GxP validation·Part 11 audit trail·QRM 등,
    약 45%)만 채택한다.

    새 키워드 리스트를 발명하지 않고 grm_taxonomy.compute_relevance 어휘를 그대로
    재사용한다(어휘 단일원천). MHRA alert keep_item(_is_mhra_medicines_alert) 선례와
    동일 철학으로 정밀도를 우선 — Board 후보/Member Spotlight 류는 카테고리 키워드
    미매칭 → "Pending" 판정 → 탈락.
    """
    return compute_relevance(f.title, f.description) in ("Likely", "Possible")


_EMA_FEED_SPEC = RssFeedSpec(
    source=SOURCE_EMA, source_type=SRC_TYPE_OFFICIAL_API, label="EMA RSS",
    feeds=tuple(EMA_RSS_FEEDS.items()),
    iter_items=_rss2_items_from_root, extract=_extract_ema,
    uses_category=True, accumulate_errors=True,
)
_MHRA_ALERT_FEED_SPEC = RssFeedSpec(
    source=SOURCE_MHRA, source_type=SRC_TYPE_OFFICIAL_PAGE, label="MHRA Drug/Device Alerts",
    feeds=(("mhra_alert", MHRA_ALERT_RSS_URL),),
    iter_items=_atom_entries_from_root, extract=_extract_mhra_alert,
    uses_category=True, keep_item=_is_mhra_medicines_alert,
)
_MHRA_FEED_SPEC = RssFeedSpec(
    source=SOURCE_MHRA, source_type=SRC_TYPE_OFFICIAL_BLOG, label="MHRA RSS",
    feeds=(("mhra", MHRA_RSS_URL),),
    iter_items=_atom_entries_from_root, extract=_extract_mhra,
    uses_category=True,
)
_PICS_FEED_SPEC = RssFeedSpec(
    source=SOURCE_PICS, source_type=SRC_TYPE_OFFICIAL_PAGE, label="PIC/S RSS",
    feeds=(("pics", PICS_RSS_URL),),
    iter_items=_rss2_items_from_root, extract=_extract_pics,
)
_ECA_FEED_SPEC = RssFeedSpec(
    source=SOURCE_ECA, source_type=SRC_TYPE_EXPERT_SECONDARY, label="ECA RSS",
    feeds=(("eca", ECA_RSS_URL),),
    iter_items=_rss2_or_atom_items, extract=_extract_eca,
    http_silent=True,
)
# [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak 블로그 — ECA 와 동일 분류(Expert
# Secondary)이되 keep_item 으로 협회 홍보성 항목을 걸러낸다(설계 결정 §2 정밀도 우선).
_ISPE_FEED_SPEC = RssFeedSpec(
    source=SOURCE_ISPE, source_type=SRC_TYPE_EXPERT_SECONDARY, label="ISPE RSS",
    feeds=(("ispe", ISPE_RSS_URL),),
    iter_items=_rss2_or_atom_items, extract=_extract_ispe,
    http_silent=True, keep_item=_is_ispe_gmp_relevant,
)


def collect_ema_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """EMA 공식 RSS 피드 4개 수집. Source Type: Official API. Evidence: B 이상(RSS 요약)."""
    return collect_rss_feed(_EMA_FEED_SPEC, start, end)


def collect_mhra_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """MHRA Inspectorate Blog RSS(Atom) 수집. Source Type: Official Regulator Blog."""
    return collect_rss_feed(_MHRA_FEED_SPEC, start, end)


def collect_mhra_alerts(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """MHRA gov.uk 의약품 회수/결함(drug-device-alerts) 수집. 의료기기 FSN 은 제외.
    item.source 는 "MHRA Inspectorate" 재사용(신규 Notion Source 옵션 불요)."""
    return collect_rss_feed(_MHRA_ALERT_FEED_SPEC, start, end)


def collect_pics_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """PIC/S 공식 RSS 수집. Source Type: Official Regulatory Page."""
    return collect_rss_feed(_PICS_FEED_SPEC, start, end)


def _extract_eca_article_excerpt(html_text: str) -> str:
    """gmp-compliance.org 기사 HTML → 본문 excerpt(≤1200자).

    간단·보수적 추출: script/style/nav/header/footer 제거 후 `<p>` 태그 텍스트만 이어붙인다
    (구조 파싱 불요 — 사이트 마크업 변경에 강건). 앵커 탐색 없이 문서 처음부터 순서대로 결합.
    """
    text = html_text or ""
    text = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", text)
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", text)
    parts: list[str] = []
    for p in paras:
        t = re.sub(r"(?s)<[^>]+>", " ", p)
        t = _html_unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            parts.append(t)
    joined = " ".join(parts).strip()
    return joined[:ECA_ARTICLE_EXCERPT_MAX_CHARS].strip()


def _fetch_article_excerpt(url: str, log_label: str) -> str:
    """전문지 기사 페이지 fetch → 본문 excerpt(_extract_eca_article_excerpt 재사용 — 내용
    무관 제네릭 <p> 결합 방식이라 ECA 외 소스에도 그대로 쓸 수 있다). 실패(403/timeout/
    네트워크)는 graceful("").

    [전문지 브리핑 소스확장 2026-07-13] `_fetch_eca_article_excerpt` 본문을 여기로
    옮기고 log_label 로 소스별 로그 문구를 조립한다 — ECA 는 f"{log_label} 기사 본문 403
    — ..." = "ECA 기사 본문 403 — ..." 로 기존과 byte 동일(회귀 tests/test_eca_article_excerpt.py
    보존).
    """
    try:
        resp = requests.get(url, timeout=ECA_ARTICLE_EXCERPT_FETCH_TIMEOUT, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", f"{log_label} 기사 본문 403 — excerpt 건너뜀(카드 그대로): {truncate(url, 80)}")
            return ""
        resp.raise_for_status()
    except requests.RequestException as e:
        log("WARN", f"{log_label} 기사 본문 fetch 실패(카드 그대로): {truncate(str(e), 120)}")
        return ""
    excerpt = _extract_eca_article_excerpt(resp.text)
    if not excerpt:
        log("INFO", f"{log_label} 기사 본문 <p> 텍스트 미발견 — 카드 그대로: {truncate(url, 80)}")
    return excerpt


def _fetch_eca_article_excerpt(url: str) -> str:
    """ECA 기사 페이지 fetch → 본문 excerpt. 얇은 래퍼 — tests/test_eca_article_excerpt.py
    가 이 심볼명을 직접 호출/패치하므로 시그니처 불변 유지."""
    return _fetch_article_excerpt(url, "ECA")


def collect_eca_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """ECA Academy(gmp-compliance.org) RSS 수집. Source Type: Expert Secondary.
    403 발생 시 운영 경고 없이 진행(Expert Secondary 허용 정책).

    [전문지 브리핑 v2 2026-07-13] ENABLE_ECA_ARTICLE_EXCERPT=true 시 항목별 기사 URL
    (official_url)을 추가 fetch 해 본문 excerpt(≤1200자)를 raw_payload["eca_article_excerpt"]
    에 싣는다(cap 10건·per-item delay 1s·403/timeout 은 조용히 skip). 기본 off — off 면 이
    블록 자체가 실행되지 않아 산출물이 기존과 byte 동일(§4 골든 불변 하드 요구).
    """
    items, err = collect_rss_feed(_ECA_FEED_SPEC, start, end)
    if items and env_flag("ENABLE_ECA_ARTICLE_EXCERPT"):
        capped = False
        for i, item in enumerate(items):
            if i >= ECA_ARTICLE_EXCERPT_CAP:
                if not capped:
                    log("WARN", f"ECA 기사 excerpt cap({ECA_ARTICLE_EXCERPT_CAP}) 도달 — "
                                "나머지 항목은 excerpt 없이 유지")
                    capped = True
                break
            if not item.official_url:
                continue
            time.sleep(ECA_ARTICLE_EXCERPT_DELAY_SECONDS)
            excerpt = _fetch_eca_article_excerpt(item.official_url)
            if excerpt:
                item.raw_payload["eca_article_excerpt"] = excerpt
    return items, err


def collect_ispe_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """ISPE iSpeak(ispe.org) 블로그 RSS 수집. Source Type: Expert Secondary. keep_item
    관련성 필터(_is_ispe_gmp_relevant)로 협회 홍보성 항목을 배제한다(설계 결정 §2).

    [전문지 브리핑 소스확장 2026-07-13] ENABLE_ISPE_ARTICLE_EXCERPT=true 시 항목별 기사
    URL(official_url)을 추가 fetch 해 본문 excerpt(≤1200자)를
    raw_payload["article_excerpt"](제네릭 키 — 설계 결정 §3, ECA 의 "eca_article_excerpt"
    와 별개)에 싣는다. cap/delay/max-chars 는 ECA_ARTICLE_EXCERPT_* 상수를 그대로
    재사용한다(별도 상수 미신설 — 값 동일 정책: cap 10건·delay 1s·1200자). 기본 off —
    off 면 이 블록 자체가 실행되지 않아 산출물이 기존과 byte 동일(§4 골든 불변 하드 요구).
    """
    items, err = collect_rss_feed(_ISPE_FEED_SPEC, start, end)
    if items and env_flag("ENABLE_ISPE_ARTICLE_EXCERPT"):
        capped = False
        for i, item in enumerate(items):
            if i >= ECA_ARTICLE_EXCERPT_CAP:
                if not capped:
                    log("WARN", f"ISPE 기사 excerpt cap({ECA_ARTICLE_EXCERPT_CAP}) 도달 — "
                                "나머지 항목은 excerpt 없이 유지")
                    capped = True
                break
            if not item.official_url:
                continue
            time.sleep(ECA_ARTICLE_EXCERPT_DELAY_SECONDS)
            excerpt = _fetch_article_excerpt(item.official_url, "ISPE")
            if excerpt:
                item.raw_payload["article_excerpt"] = excerpt
    return items, err


# ─────────────────────────────────────────────────────────────────────────────
# FDA Warning Letters 수집 (v15.1) — HTML 파싱
# ─────────────────────────────────────────────────────────────────────────────


class _FDAWLTableParser(HTMLParser):
    """FDA Warning Letters 페이지 HTML 에서 테이블 행을 파싱.

    대상 테이블은 class="table" 이며 열 순서:
      Posted Date | Recipient | Letter Issue Date | Issuing Office | Subject (or close match)
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_table: bool = False
        self._in_row: bool = False
        self._in_cell: bool = False
        self._cell_depth: int = 0
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._current_href: str = ""
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table" and "table" in (attr_dict.get("class") or ""):
            self._in_table = True
        if not self._in_table:
            return
        if tag == "tr":
            self._in_row = True
            self._current_row = []
        if tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_depth = 1
            self._current_cell = []
            self._current_href = ""
        elif tag == "a" and self._in_cell:
            href = attr_dict.get("href") or ""
            if href and not self._current_href:
                self._current_href = href
        elif tag in ("td", "th") and self._in_cell:
            self._cell_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag in ("td", "th") and self._in_cell:
            self._cell_depth -= 1
            if self._cell_depth <= 0:
                cell_text = " ".join(self._current_cell).strip()
                # href 와 함께 저장
                self._current_row.append(
                    f"{cell_text}|HREF:{self._current_href}" if self._current_href else cell_text
                )
                self._in_cell = False
        if tag == "tr" and self._in_row:
            self._in_row = False
            if len(self._current_row) >= 4:
                self.rows.append({"_cols": self._current_row})
        if tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell.append(stripped)


def _parse_wl_date(raw: str) -> str:
    """FDA WL 날짜 형식 (MM/DD/YYYY 또는 YYYY-MM-DD) → YYYY-MM-DD."""
    raw = raw.strip()
    if re.match(r"^\d{2}/\d{2}/\d{4}$", raw):
        try:
            return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
        except ValueError:
            pass
    return _safe_date_iso(raw, context="FDA_WL")


def _wl_html_to_text(html_text: str) -> str:
    """WL 편지 HTML → 평탄화 텍스트. script/style 제거 + 태그 제거 + 엔티티 복원 + 공백 정규화.

    PyMuPDF 불요(편지 본문은 HTML). 결정론·무의존(re + stdlib unescape).
    """
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html_unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _earliest_anchor(text: str, patterns: tuple[str, ...]) -> int | None:
    """patterns 중 text 에서 가장 이른 매치 시작 위치. 없으면 None (re.I)."""
    best: int | None = None
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def _extract_wl_body_span(html_text: str, max_chars: int) -> str:
    """WL 본문 위반 서술 구간 추출 공통 코어(앵커 탐색 + max_chars 절단).

    1차 앵커(위반 서술 직접 지시)가 있으면 그 가장 이른 위치, 없으면 폴백 앵커(일반 머리말),
    둘 다 없으면 ""(키 미기록·목록 메타 카드 유지). `_extract_wl_body_excerpt`(1500자, 기존
    W1 카드 "왜"용)와 `_extract_wl_body_full`(전문, 심층분석 fan-out 입력용)이 이 코어를
    공유한다 — 앵커 탐색 로직 중복 방지, `max_chars` 만 다르다(기존 호출부 동작 불변).

    표지/머리말 보일러플레이트가 아니라 위반 서술을 카드 컨텍스트("왜")로 올리기 위한 추출.
    FDA 페이지는 nav/푸터가 많아 앵커 미발견 시 앞부분 폴백을 하지 않고 메타 카드를 유지한다.
    """
    text = _wl_html_to_text(html_text)
    if not text:
        return ""
    start = _earliest_anchor(text, _WL_BODY_ANCHORS_PRIMARY)
    if start is None:
        start = _earliest_anchor(text, _WL_BODY_ANCHORS_FALLBACK)
    if start is None:
        return ""
    start = _skip_wl_leadin(text, start)
    return text[start:start + max_chars].strip()


def _skip_wl_leadin(text: str, start: int) -> int:
    """앵커가 잡은 위치가 내용 없는 도입구면 그 **뒤**로 시작점을 옮긴다(못 옮기면 그대로).

    "…observed specific violations including, but not limited to, the following." 는 실제 위반을
    한 글자도 담지 않은 도입구다. 이게 excerpt 맨 앞을 차지하면 하류 300자 절단이 그 한 문장만
    남기고 실제 위반을 다 버린다(상수 주석의 2026-07-20 사고). 도입구를 앵커 직후
    (`_WL_LEADIN_MAX_OFFSET`) 에서만 찾고, 건너뛴 뒤 실질 본문이 `_WL_LEADIN_MIN_TAIL` 이상
    남을 때만 이동한다 — 형식이 다른 편지(번호 목록이 없는 미승인의약품 WL 등)에서 본문을
    잃지 않기 위한 보수적 게이트다.
    """
    m = _WL_LEADIN_RE.search(text, start, start + _WL_LEADIN_MAX_OFFSET)
    if not m:
        return start
    if len(text) - m.end() < _WL_LEADIN_MIN_TAIL:
        return start
    return m.end()


def _extract_wl_body_excerpt(html_text: str) -> str:
    """WL 본문 위반 서술 구간 excerpt(1500자) — `_extract_wl_body_span` 위임(동작 불변)."""
    return _extract_wl_body_span(html_text, WL_BODY_MAX_CHARS)


def _extract_wl_body_full(html_text: str) -> str:
    """[WL 심층분석 fan-out] WL 본문 전문(全文, 최대 WL_BODY_FULL_MAX_CHARS) — 신규·additive.

    excerpt 와 동일 앵커(위반 서술 시작점)에서 시작해 훨씬 더 긴 구간을 담는다 — 위반 서술
    이후 이어지는 구제조치 기한·행정 리스크 문단까지 포함하도록(편지 뒷부분). 카드별 fan-out
    심층분석(docs/prompts/GRM_Prompt_DeepWL_v1.md)의 유일한 입력 컨텍스트가 된다.
    """
    return _extract_wl_body_span(html_text, WL_BODY_FULL_MAX_CHARS)


# ── [WL 위반항목 결정론 추출 2026-07-20] ────────────────────────────────────────
# FDA cGMP Warning Letter 는 위반을 "번호 + 조항 표제문" 형태로 나열한다:
#     1. Your firm failed to thoroughly investigate any unexplained discrepancy … (21 CFR 211.192).
# 이 표제문을 원문 그대로 뽑아 카드의 **결정론 상세 슬롯**(환각 0)에 싣는다 — FDA 483 의
# `fda_483_observations` 와 완전 동형이다. WL 이 483 과 달리 상세를 못 보여주던 근본 원인이
# "결정론 층 부재 → LLM(deep_analysis fan-out) 단일 경로 의존"이었고, fan-out 이 안 돈 주에는
# 카드가 통째로 비었다(2026-07-20 사고). 이 파서가 그 **바닥**을 만든다.
#
# 표제 판별 신호(실측 2건으로 확정 — 번호만으로는 각주 번호와 구별되지 않는다):
#   ① 번호 뒤 도입부가 "Your firm failed / You failed / … did not …" 계열이고
#   ② 그 문장 안에 근거 조항 `21 CFR …` 인용이 있으며
#   ③ 인용 괄호가 닫힌 뒤 마침표로 표제문이 끝난다
# 셋을 모두 만족할 때만 위반으로 인정한다. 하나라도 없으면 건너뛴다(과소추출 우선 —
# 잘못된 표제를 카드에 싣는 것보다 안전). 번호가 역행/중복하면 버린다(각주 오인식 차단).
_WL_VIOLATION_HEAD_RE = re.compile(
    r"(?<![\w.)])(\d{1,2})\.\s+(?=(?:Your firm|Your|You)\b[^.]{0,80}?\b(?:failed|did not)\b)")
_WL_VIOLATION_CFR_RE = re.compile(r"21 CFR\b")
_WL_VIOLATION_END_RE = re.compile(r"\)\s*\.")
_WL_VIOLATION_CITE_RE = re.compile(r"21 CFR \d+\.\d+(?:\([^()\s]{1,6}\))*")
_WL_VIOLATION_LOOKAHEAD = 1200   # 표제문 1개의 최대 길이(그 안에 조항 인용이 없으면 표제 아님)


def extract_wl_violations_from_text(text: str) -> list[dict[str, str]]:
    """WL 본문 텍스트 → 위반 표제 목록 `[{number, statement, citation}]`(순수·결정론).

    `statement` 는 원문 영어 **그대로**(요약·의역 0), `citation` 은 표제문 안의 `21 CFR` 조항을
    등장 순서대로 ` · ` 로 이은 문자열(없으면 ""). 형식이 다른 편지(번호 목록 없는 미승인의약품
    WL 등)에서는 빈 리스트를 돌려준다 — 호출부는 그때 상세 슬롯을 아예 달지 않는다.
    """
    if not text:
        return []
    out: list[dict[str, str]] = []
    last_num = 0
    for m in _WL_VIOLATION_HEAD_RE.finditer(text):
        seg = text[m.end():m.end() + _WL_VIOLATION_LOOKAHEAD]
        cfr = _WL_VIOLATION_CFR_RE.search(seg)
        if not cfr:
            continue                       # ② 조항 인용 없음 → 표제 아님
        end = _WL_VIOLATION_END_RE.search(seg, cfr.end())
        if not end:
            continue                       # ③ 표제문 종결 미확인 → 버린다
        num = int(m.group(1))
        if num <= last_num:
            continue                       # 번호 역행/중복 → 각주 등 오인식
        last_num = num
        statement = " ".join(seg[:end.end()].split())
        cites = list(dict.fromkeys(_WL_VIOLATION_CITE_RE.findall(statement)))
        out.append({"number": str(num), "statement": statement,
                    "citation": " · ".join(cites)})
    return out


# WHY-1 #2 P1: WL 본문 excerpt 관측용 — collect_who.LAST_HEALTH 동형 패턴.
# collect_fda_warning_letters 가 매 호출 갱신하고 오케스트레이터가 stats 로 옮긴다.
LAST_WL_HEALTH: dict[str, Any] = {}


# [사유 전파 2026-07-20] 수집기는 본문을 왜 확보 못했는지 정확히 안다(403·timeout·앵커
# 미발견)인데 그 사유가 하류로 전달되지 않아 코드/LLM 이 이유를 지어내는 사고가 있었다
# (예: "스캔·비공개로 상세가 제공되지 않아" — 우리가 못 받았다는 사실에서 원문 상태까지
# 단정). 아래 고정 어휘만 status 로 쓴다 — 새 값을 함부로 추가하지 말 것(하류 매핑 계약).
WL_BODY_STATUS_OK = "ok"
WL_BODY_STATUS_403 = "fetch-403"
WL_BODY_STATUS_NO_ANCHOR = "no-anchor"


def _fetch_wl_body_excerpt(url: str) -> tuple[str, str]:
    """WL 편지 페이지 fetch → (위반 excerpt, status). 실패(403/timeout/네트워크)는 graceful(("", status)).

    status 고정 어휘: "ok"(성공) | "fetch-403" | "fetch-fail:<예외 요약 120자>" |
    "no-anchor"(fetch 는 성공했으나 위반 앵커 미발견). 기존 로그(WARN/INFO)와 graceful
    동작(예외를 올리지 않음)은 그대로 유지 — 반환 형태만 str → tuple[str, str] 로 바뀐다.
    """
    try:
        resp = requests.get(url, timeout=WL_BODY_FETCH_TIMEOUT, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", f"FDA WL 본문 403 — excerpt 건너뜀(메타 카드 유지): {truncate(url, 80)}")
            return "", WL_BODY_STATUS_403
        resp.raise_for_status()
    except requests.RequestException as e:
        log("WARN", f"FDA WL 본문 fetch 실패(메타 카드 유지): {truncate(str(e), 120)}")
        return "", f"fetch-fail:{truncate(str(e), 120)}"
    excerpt = _extract_wl_body_excerpt(resp.text)
    if not excerpt:
        log("INFO", f"FDA WL 본문 위반 앵커 미발견 — 메타 카드 유지: {truncate(url, 80)}")
        return "", WL_BODY_STATUS_NO_ANCHOR
    return excerpt, WL_BODY_STATUS_OK


def _fetch_wl_body_full(url: str) -> tuple[str, str]:
    """[WL 심층분석 fan-out] WL 편지 페이지 fetch → (본문 전문, status). 실패는 graceful(("", status)).

    `_fetch_wl_body_excerpt` 와 별도 GET(단순성·격리 우선 — in-window WL 은 주 소수라
    중복 fetch 비용 무시 가능). 두 플래그가 모두 켜져도 서로 독립적으로 동작한다.
    status 어휘는 `_fetch_wl_body_excerpt` 와 동일(고정 어휘 — 하류 매핑 계약).
    """
    try:
        resp = requests.get(url, timeout=WL_BODY_FETCH_TIMEOUT, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", f"FDA WL 본문(전문) 403 — 건너뜀(메타 카드 유지): {truncate(url, 80)}")
            return "", WL_BODY_STATUS_403
        resp.raise_for_status()
    except requests.RequestException as e:
        log("WARN", f"FDA WL 본문(전문) fetch 실패(메타 카드 유지): {truncate(str(e), 120)}")
        return "", f"fetch-fail:{truncate(str(e), 120)}"
    full = _extract_wl_body_full(resp.text)
    if not full:
        log("INFO", f"FDA WL 본문(전문) 위반 앵커 미발견 — 메타 카드 유지: {truncate(url, 80)}")
        return "", WL_BODY_STATUS_NO_ANCHOR
    return full, WL_BODY_STATUS_OK


def collect_fda_warning_letters(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA Warning Letters 페이지 HTML 테이블 파싱 수집.

    Source Type: Official Regulatory Page.
    페이지: https://www.fda.gov/.../warning-letters

    FDA WL 페이지는 정적 HTML 테이블을 포함하므로 WebFetch 가능.
    JS-heavy 인 경우 content 부재 → 빈 결과 반환 (fail-silent).
    403/timeout 시 WARN 로그 후 빈 결과 반환.
    """
    log("INFO", f"FDA WL 수집: {FDA_WL_URL}")
    wl_body_enabled = env_flag("ENABLE_WL_BODY")
    # [WL 심층분석 fan-out] 전문 확보 게이트 — ENABLE_WL_BODY 와 독립(둘 다 off 가 기본).
    wl_body_full_enabled = env_flag("ENABLE_WL_BODY_FULL")
    # P1: excerpt 시도/실패 집계 — 시작 시점에 전역을 교체해(이른 return 포함) 항상
    # 이번 호출 분만 남긴다. dict 는 in-place 갱신이라 이후 증가분이 그대로 반영.
    global LAST_WL_HEALTH
    wl_body_health: dict[str, Any] = {
        "enabled": wl_body_enabled, "attempted": 0, "failed": 0,
    }
    wl_body_full_health: dict[str, Any] = {
        "enabled": wl_body_full_enabled, "attempted": 0, "failed": 0,
    }
    LAST_WL_HEALTH = {"wl_body": wl_body_health, "wl_body_full": wl_body_full_health}
    try:
        resp = requests.get(FDA_WL_URL, timeout=30, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", "FDA WL 403 — HTML 수집 불가, 이번 주 WL 슬롯 건너뜀")
            return [], "HTTP 403"
        resp.raise_for_status()
        html_text = resp.text
    except requests.RequestException as e:
        log("WARN", f"FDA WL HTTP 실패: {e}")
        return [], str(e)

    parser = _FDAWLTableParser()
    parser.feed(html_text)

    if not parser.rows:
        msg = "FDA WL HTML 테이블 미발견 — 구조 변경 또는 JS-rendered 가능성"
        log("WARN", msg)
        return [], msg

    items: list[IntakeItem] = []
    for row in parser.rows:
        cols = row.get("_cols", [])
        if len(cols) < 4:
            continue
        # 실제 FDA WL 테이블 열 순서 (2026년 5월 확인):
        # [0] Posted Date  [1] Letter Issue Date  [2] Company Name(+href)
        # [3] Issuing Office  [4] Subject  [5] Response Letter  [6+] 기타
        posted_raw = cols[0].split("|HREF:")[0].strip()

        # 헤더 행 건너뜀 (col[0]이 날짜 패턴이 아닌 텍스트)
        if not re.match(r"^\d", posted_raw):
            continue

        letter_date_raw = cols[1].split("|HREF:")[0].strip() if len(cols) > 1 else ""

        # Company Name — href 포함 가능
        recipient_raw = cols[2] if len(cols) > 2 else ""
        wl_href = ""
        if "|HREF:" in recipient_raw:
            parts = recipient_raw.split("|HREF:", 1)
            recipient_raw = parts[0].strip()
            wl_href = parts[1].strip()

        issuing_office = cols[3].split("|HREF:")[0].strip() if len(cols) > 3 else ""
        subject = cols[4].split("|HREF:")[0].strip() if len(cols) > 4 else ""

        # Posted date 가 주 우선 날짜
        date_iso = _parse_wl_date(posted_raw) or _parse_wl_date(letter_date_raw)

        if not _within_window(date_iso, start, end):
            continue

        # URL 정규화
        if wl_href and wl_href.startswith("/"):
            wl_href = "https://www.fda.gov" + wl_href

        firm = recipient_raw
        headline = subject or firm or "FDA Warning Letter"

        # M0: 발행 부서 1차 게이트 (redesign §7). 부서 결측/미매핑은 본문 키워드 폴백.
        office_verdict = _fda_wl_office_gate(issuing_office, headline, subject, firm)
        if office_verdict == "exclude":
            log("INFO", f"FDA WL 부서 게이트 제외({truncate(issuing_office, 40)}): "
                        f"{truncate(firm or headline, 60)}")
            continue
        if office_verdict == "unknown" and _is_low_value_fda_warning_letter(
            headline, subject, issuing_office, firm
        ):
            log("INFO", f"FDA WL 저가치 식품/보충제 항목 제외: {truncate(firm or headline, 80)}")
            continue
        # keep / review / unknown(본문 폴백 통과) → 유지. review(OII 맥락 모호)는
        # 보수적 비-드롭(약품 WL 오삭제 방지). 전용 Status 마킹은 인프라 부재로 K4 이월.

        doc_id = _stable_doc_id(SOURCE_FDA_WL, firm, wl_href or FDA_WL_URL, date_iso)
        relevance = compute_relevance(headline, subject, issuing_office)
        tier = compute_signal_tier(SOURCE_FDA_WL, issuing_office or "Warning Letter",
                                   relevance, "N/A", headline, subject, issuing_office)

        wl_raw: dict[str, Any] = {
            "firm": firm, "posted_date": posted_raw,
            "letter_date": letter_date_raw,
            "issuing_office": issuing_office,
            "subject": subject, "url": wl_href,
        }
        # P2: 확정 제외/유지가 아닌 verdict(review·unknown)는 관측 가능하게 raw 에 남긴다
        # (머지 후 부서 게이트 오판 모니터링용). keep/exclude 는 자명하므로 기록 생략.
        if office_verdict in ("review", "unknown"):
            wl_raw["office_gate_verdict"] = office_verdict
            log("INFO", f"FDA WL 부서 게이트 {office_verdict}(유지·관측): "
                        f"{truncate(issuing_office or '부서결측', 40)} | "
                        f"{truncate(firm or headline, 50)}")

        # WHY-1 #2: 게이트 통과(유지) WL 의 편지 본문에서 위반 excerpt 추출(flag on 시).
        # 실패는 graceful(키 미기록 → 목록 메타 카드 유지). in-window 유지 WL 은 소수라 부담 낮음.
        # [사유 전파 2026-07-20] 본문을 확보하지 못한 경우에만 wl_body_status 를 남긴다 —
        # 성공은 사유가 없다(골든 additive, 기존 카드는 키가 안 생겨 불변).
        if wl_body_enabled and wl_href:
            wl_body_health["attempted"] += 1
            body_excerpt, wl_body_status = _fetch_wl_body_excerpt(wl_href)
            if body_excerpt:
                wl_raw["wl_body_excerpt"] = body_excerpt
            else:
                wl_body_health["failed"] += 1
                wl_raw.setdefault("wl_body_status", wl_body_status)   # 왜 비었는지 — 하류가 이유를 지어내지 않게

        # [WL 심층분석 fan-out 2026-07-01] 전문 확보(flag on 시) — 카드별 fan-out 심층분석
        # (docs/prompts/GRM_Prompt_DeepWL_v1.md)의 유일한 입력. wl_body_enabled 와 완전
        # 독립 — 기존 excerpt 플로우는 이 블록의 영향을 받지 않는다(additive).
        if wl_body_full_enabled and wl_href:
            wl_body_full_health["attempted"] += 1
            body_full, wl_body_full_status = _fetch_wl_body_full(wl_href)
            if body_full:
                wl_raw["wl_body_full"] = body_full
                # [WL 위반항목 결정론 추출 2026-07-20] 전문에서 위반 표제를 결정론 추출해
                # 함께 싣는다(FDA 483 의 `fda_483_observations` 동형 — 네트워크 0·순수 함수).
                # 카드 상세 슬롯의 입력. 형식이 달라 못 뽑으면 키 자체를 달지 않는다.
                violations = extract_wl_violations_from_text(body_full)
                if violations:
                    wl_raw["wl_violations"] = violations
            else:
                wl_body_full_health["failed"] += 1
                # excerpt 가 이미 사유를 남겼으면 덮어쓰지 않는다(setdefault) — 같은 편지
                # 이니 사유도 같을 확률이 높지만, excerpt 쪽 사유가 우선(먼저 시도됨).
                wl_raw.setdefault("wl_body_status", wl_body_full_status)

        items.append(IntakeItem(
            source=SOURCE_FDA_WL,
            document_id=doc_id,
            date_iso=date_iso,
            headline=headline,
            official_url=wl_href or FDA_WL_URL,
            type_or_class=issuing_office or "Warning Letter",
            firm=firm,
            body=subject,
            api_query=FDA_WL_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            signal_tier=tier,
            raw_payload=wl_raw,
        ))

    log("INFO", f"FDA WL 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# Notion 헬퍼
# ─────────────────────────────────────────────────────────────────────────────


# B1 임시 방어: handoff 조회 윈도우는 발행 cadence(주간 7일)보다 커야 주간 Routine 이
# 1회 지연돼도 미소비 New row 가 Run Date 하한 밖으로 빠져 영구 누락되지 않는다.
# 기본 30일은 dedup(MFDS enforcement 30일)과 정합. enforcement 의미를 handoff 에
# 섞지 않도록 전용 환경변수를 둔다. 근본 해결(날짜 하한 제거)은 PL-10b 와 별도 트랙.


# B1 임시 방어 ②: 윈도우(30일)로도 못 막는 케이스(Routine 3주+ 누락)를 침묵 대신
# 경고로 띄운다. 카운트 목적이라 전수 페이지네이션 불요 — 상한 도달 시 하한값으로
# 충분하다(경고 트리거는 N>0 여부, 정확 수는 사람이 Notion 에서 확인).


# ── K2-prep: page_id 로 원 row raw API JSON 복원 → rows 에 부착 (card_spec §12(A)) ──
# v1 handoff snapshot 에는 raw 가 없다(원본 API 응답 JSON 전체는 Intake row 본문
# code block 에만 있고, `raw_excerpt` 속성은 잘린 발췌라 불충분). raw 의존 칸(W3 인용·
# MFDS W2·Modality 폴백)은 이 보강 후에만 결정론적이다(redesign §4, card_spec §12(A)).
# ⚠️ 이 단계는 네트워크 호출이므로 build_card_scaffold() 안에 두지 않는다(§12(G) 순수성).


# 한국어 요일(date.weekday(): 월=0..일=6). 발행 요일을 LLM 이 산술하지 않게 handoff 에
# 결정론 산출해 싣는다(06-17 dry-run D-1: LLM 이 수요일을 화요일로 오산). run_date 는 이미
# KST 달력일이라 weekday() 가 KST 요일과 일치(타임존 off-by-one 없음).


# ─────────────────────────────────────────────────────────────────────────────
# 수집 현황(커버리지) '수집' 컬럼 결정론화 (W1) — handoff source_counts 를 발행 커버리지
# callout 어휘로 미리 포맷한다. LLM 이 재집계·추정하지 않고 그대로 전사한다(요일 weekday_kst
# 와 동형). 라벨·순서는 v16 프롬프트 [블록 3 — 커버리지] callout 과 일치 — 코드가 단일 기준.
# 발행 후 탐지(verify_published_brief + brief_lint.lint_coverage_counts)도 같은 정본으로
# 발행물 숫자를 대조한다(W2). 06-17 검증: 발행=실제 카드수는 확인됐으나 수집/스킵은 LLM
# 집계라 무보증 클래스(요일 오산과 동형) → 수집은 코드가 산출·감사 가능하게 한다.
# ─────────────────────────────────────────────────────────────────────────────
# (source 문자열, 발행 callout 라벨) — 고정 순서. 상시 수집기 11종(프롬프트 callout 동일).


# v2 row 에 보존할 v1 호환 필드(whitelist) — _intake_page_snapshot() 스키마.
# blacklist 대신 whitelist 로(Codex P2): raw·Stage B 부착 bookkeeping(raw_fetch_ok·
# raw_source·status_hint·evidence_hint) 같은 내부/대형 필드가 새지 않도록 보장.


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────


def insert_items(token: str, db_id: str, items: Iterable[IntakeItem],
                 run_date: date, collected_at: datetime,
                 existing_ids: set[str], dry_run: bool, *,
                 modality_enabled: bool | None = None,
                 findings_sqlite_path: str | None = None,
                 findings_sqlite_include_findings: bool = False,
                 findings_supabase: tuple[str, str] | None = None,
                 findings_supabase_include_findings: bool = False) -> tuple[int, int, int]:
    """삽입 실행. 반환: (inserted, skipped, failed)

    [배치6 Phase2] modality_enabled: main 이 preflight 로 결정한 effective 값을 전달하면
    row 당 env 재독해 없이 그대로 build_notion_properties 로 흐른다(미지정 시 env 폴백).
    """
    inserted = 0
    skipped = 0
    failed = 0
    for item in items:
        if not item.document_id:
            log("WARN", f"document_id 없음 — skip (source={item.source})")
            continue
        # dedupe key = "source::document_id" (source 포함으로 FR/Recall ID 충돌 방지)
        dedup_key = f"{item.source}::{item.document_id}"
        if dedup_key in existing_ids:
            skipped += 1
            continue
        if dry_run:
            log("INFO", f"[DRY] insert source={item.source} id={item.document_id} "
                       f"date={item.date_iso} rel={item.qa_relevance} head={truncate(item.headline, 60)}")
            inserted += 1
            existing_ids.add(dedup_key)
            continue
        # Notion rate limit 방어: 삽입 간 최소 0.34s 지연 (≤ 3 req/s)
        time.sleep(0.34)
        ok = notion_create_page(token, db_id, item, run_date, collected_at,
                                modality_enabled=modality_enabled)
        if ok:
            inserted += 1
            existing_ids.add(dedup_key)
            if findings_sqlite_path:
                try:
                    if findings_sqlite_include_findings:
                        result = append_intake_item_with_findings_to_sqlite(
                            findings_sqlite_path, item, collected_at=collected_at)
                        if result.status in {"inserted", "raw_signal_inserted"}:
                            log("INFO", f"FIND-1 raw_signals+findings SQLite append 완료 "
                                        f"doc={item.document_id} raw={result.raw_signal_status} "
                                        f"findings_inserted={result.findings_inserted} "
                                        f"duplicates={result.findings_duplicate} "
                                        f"invalid={result.findings_invalid}")
                        elif result.status == "duplicate":
                            log("INFO", f"FIND-1 raw_signals+findings SQLite append 중복 skip "
                                        f"doc={item.document_id} raw={result.raw_signal_status} "
                                        f"findings_duplicate={result.findings_duplicate}")
                        else:
                            log("WARN", f"FIND-1 raw_signals+findings SQLite append skip "
                                        f"doc={item.document_id} status={result.status} "
                                        f"raw={result.raw_signal_status} errors={list(result.errors)}")
                    else:
                        result = append_intake_item_to_sqlite(
                            findings_sqlite_path, item, collected_at=collected_at)
                        if result.status == "inserted":
                            log("INFO", f"FIND-1 raw_signals SQLite append 완료 doc={item.document_id}")
                        elif result.status == "duplicate":
                            log("INFO", f"FIND-1 raw_signals SQLite append 중복 skip doc={item.document_id}")
                        else:
                            log("WARN", f"FIND-1 raw_signals SQLite append skip doc={item.document_id} "
                                        f"status={result.status} errors={list(result.errors)}")
                except Exception as e:  # noqa: BLE001 — additive sidecar must not break Notion flow.
                    mode = "raw_signals+findings" if findings_sqlite_include_findings else "raw_signals"
                    log("WARN", f"FIND-1 {mode} SQLite append 실패 doc={item.document_id}: {e}")
            if findings_supabase:
                supabase_url, supabase_service_key = findings_supabase
                try:
                    if findings_supabase_include_findings:
                        result = append_intake_item_with_findings_to_supabase(
                            supabase_url, supabase_service_key, item, collected_at=collected_at)
                        if result.status in {"inserted", "raw_signal_inserted"}:
                            log("INFO", f"FIND-1 raw_signals+findings Supabase append 완료 "
                                        f"doc={item.document_id} raw={result.raw_signal_status} "
                                        f"findings_inserted={result.findings_inserted} "
                                        f"duplicates={result.findings_duplicate} "
                                        f"invalid={result.findings_invalid}")
                        elif result.status == "duplicate":
                            log("INFO", f"FIND-1 raw_signals+findings Supabase append 중복 skip "
                                        f"doc={item.document_id} raw={result.raw_signal_status} "
                                        f"findings_duplicate={result.findings_duplicate}")
                        else:
                            log("WARN", f"FIND-1 raw_signals+findings Supabase append skip "
                                        f"doc={item.document_id} status={result.status} "
                                        f"raw={result.raw_signal_status} errors={list(result.errors)}")
                    else:
                        result = append_intake_item_to_supabase(
                            supabase_url, supabase_service_key, item, collected_at=collected_at)
                        if result.status == "inserted":
                            log("INFO", f"FIND-1 raw_signals Supabase append 완료 doc={item.document_id}")
                        elif result.status == "duplicate":
                            log("INFO", f"FIND-1 raw_signals Supabase append 중복 skip doc={item.document_id}")
                        else:
                            log("WARN", f"FIND-1 raw_signals Supabase append skip doc={item.document_id} "
                                        f"status={result.status} errors={list(result.errors)}")
                except Exception as e:  # noqa: BLE001 — additive sidecar must not break Notion flow.
                    mode = "raw_signals+findings" if findings_supabase_include_findings else "raw_signals"
                    log("WARN", f"FIND-1 {mode} Supabase append 실패 doc={item.document_id}: {e}")
        else:
            failed += 1
            log("WARN", f"insert 최종 실패 — 다음 항목으로 진행 doc={item.document_id}")
    return inserted, skipped, failed


_ALL_SOURCES = ["fr", "recall", "ema", "mhra", "pics", "eca", "wl"]
# ich/mfds 는 opt-in feature flag 소스라 _ALL_SOURCES(기본 all)엔 넣지 않되,
# --sources 선택지와 handoff source 매핑에는 포함한다.
_SOURCE_CHOICES = _ALL_SOURCES + ["mfds", "ich", "who", "hc", "fda483", "ispe",
                                  "eu_gmp_ncr", "none"]
_SOURCE_TOKEN_TO_NOTION = {
    "fr": SOURCE_FR,
    "recall": SOURCE_RECALL,
    "ema": SOURCE_EMA,
    "mhra": SOURCE_MHRA,
    "pics": SOURCE_PICS,
    "eca": SOURCE_ECA,
    "wl": SOURCE_FDA_WL,
    "mfds": SOURCE_MFDS,
    "ich": SOURCE_ICH,
    "who": SOURCE_WHO,
    "hc": SOURCE_HC,
    "fda483": SOURCE_FDA_483,
    "ispe": SOURCE_ISPE,
    "eu_gmp_ncr": SOURCE_EU_GMP_NCR,
}


def _health_payload(
    *,
    health: HealthCheckResult,
    stats: CollectionStats,
    run_date: date,
    start: date,
    end: date,
    event_name: str,
    dry_run: bool,
    requested_sources: list[str],
    active: set[str],
    flags: dict[str, bool],
    handoff_emitted: bool,
    handoff_failed: bool,
    handoff_row_count: int,
    handoff_url: str,
    handoff_error_msg: str,
    handoff_window_days: int = 0,
    aged_unconsumed_new: int = 0,
) -> dict[str, Any]:
    return {
        "schema_version": "grm-health/v1",
        "generated_at_kst": now_kst().isoformat(),
        "run_date_kst": run_date.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "event_name": event_name,
        "dry_run": dry_run,
        "requested_sources": requested_sources,
        "active_sources": sorted(active),
        "flags": flags,
        "health": health.to_dict(),
        "sources": _source_health_rows(stats),
        "handoff": {
            "emitted": handoff_emitted,
            "failed": handoff_failed,
            "row_count": handoff_row_count,
            "url": handoff_url,
            "error_msg": handoff_error_msg,
            "window_days": handoff_window_days,
            "aged_unconsumed_new": aged_unconsumed_new,   # B1: 윈도우 밖 미소비 New(하한)
        },
    }


def _run_collection(cfg: RunConfig, active: set[str], run_date: date,
                    start: date, end: date, enf_start: date) -> tuple[CollectionStats, tuple[list[IntakeItem], ...]]:
    """[배치6 Phase4] 소스별 수집 블록(flag→수집→stats)을 실행해 stats 와 18개 소스
    item 리스트를 반환한다. 소스별 env alias 는 cfg 에서 재바인딩(바디 무수정). 수집 블록
    자체는 소스별 window/키/health 추출이 상이해 bespoke 유지 — 새 소스는 여기 블록 1개 +
    grm_common.INTAKE_SOURCE_SPECS 1건으로 insert/health 가 자동 처리된다."""
    openfda_key = cfg.openfda_key
    enable_mfds = cfg.enable_mfds
    data_go_kr_key = cfg.data_go_kr_key
    enable_moleg_api = cfg.enable_moleg_api
    enable_mfds_law = cfg.enable_mfds_law
    data_go_kr_service_key = cfg.data_go_kr_service_key
    law_go_kr_oc = cfg.law_go_kr_oc
    enable_mfds_recall = cfg.enable_mfds_recall
    enable_mfds_admin = cfg.enable_mfds_admin
    enable_mfds_gmp_cert = cfg.enable_mfds_gmp_cert
    enable_mfds_safety_letter = cfg.enable_mfds_safety_letter
    enable_mfds_gmp_inspection = cfg.enable_mfds_gmp_inspection
    enable_ich = cfg.enable_ich
    enable_who = cfg.enable_who
    enable_hc = cfg.enable_hc
    enable_fda483 = cfg.enable_fda483
    enable_ispe = cfg.enable_ispe
    enable_eu_gmp_ncr = cfg.enable_eu_gmp_ncr
    stats = CollectionStats()

    # ── Phase 1: Official API ──────────────────────────────────────────────
    fr_items: list[IntakeItem] = []
    if "fr" in active:
        fr_items, fr_err = collect_federal_register(start, end)
        stats.fr_fetched = len(fr_items)
        if fr_err:
            stats.fr_error = True
            stats.fr_error_msg = fr_err
            if "truncated" in fr_err:
                stats.fr_truncated = True

    recall_items: list[IntakeItem] = []
    if "recall" in active:
        recall_items, rec_err = collect_openfda_recalls(start, end, openfda_key)
        stats.recall_fetched = len(recall_items)
        if rec_err:
            stats.recall_error = True
            stats.recall_error_msg = rec_err
            if "truncated" in rec_err:
                stats.recall_truncated = True

    # ── Phase 2: RSS / HTML (v15.1) ───────────────────────────────────────
    ema_items: list[IntakeItem] = []
    if "ema" in active:
        ema_items, ema_err = collect_ema_rss(start, end)
        stats.ema_fetched = len(ema_items)
        if ema_err:
            stats.ema_error = True
            stats.ema_error_msg = ema_err

    mhra_items: list[IntakeItem] = []
    mhra_alert_items: list[IntakeItem] = []
    if "mhra" in active:
        mhra_items, mhra_err = collect_mhra_rss(start, end)
        stats.mhra_fetched = len(mhra_items)
        if mhra_err:
            stats.mhra_error = True
            stats.mhra_error_msg = mhra_err
        # [MHRA 회수 커버리지 수리] 같은 "mhra" 토큰으로 의약품 회수 채널도 함께 수집.
        mhra_alert_items, mhra_alert_err = collect_mhra_alerts(start, end)
        stats.mhra_alert_fetched = len(mhra_alert_items)
        if mhra_alert_err:
            stats.mhra_alert_error = True
            stats.mhra_alert_error_msg = mhra_alert_err

    pics_items: list[IntakeItem] = []
    if "pics" in active:
        pics_items, pics_err = collect_pics_rss(start, end)
        stats.pics_fetched = len(pics_items)
        if pics_err:
            stats.pics_error = True
            stats.pics_error_msg = pics_err

    eca_items: list[IntakeItem] = []
    if "eca" in active:
        eca_items, eca_err = collect_eca_rss(start, end)
        stats.eca_fetched = len(eca_items)
        if eca_err:
            stats.eca_error = True
            stats.eca_error_msg = eca_err

    wl_items: list[IntakeItem] = []
    if "wl" in active:
        wl_items, wl_err = collect_fda_warning_letters(start, end)
        stats.wl_fetched = len(wl_items)
        # P1: 본문 excerpt 실패는 graceful(메타 카드 유지) — warning 표면화용 집계.
        wl_body_health = LAST_WL_HEALTH.get("wl_body") or {}
        stats.wl_body_attempted = int(wl_body_health.get("attempted") or 0)
        stats.wl_body_failed = int(wl_body_health.get("failed") or 0)
        if wl_err:
            stats.wl_error = True
            stats.wl_error_msg = wl_err

    # ── Phase 2b: MFDS (ENABLE_MFDS=true 시 실행, 기본 false) ───────────────
    mfds_items: list[IntakeItem] = []
    if enable_mfds:
        log("INFO", "=== MFDS 수집 시작 ===")
        if data_go_kr_key and not enable_moleg_api:
            log("INFO", "DATA_GO_KR_KEY 존재하지만 ENABLE_MOLEG_API=false - ogLmPp 호출 없이 RSS primary 사용")
        try:
            from collect_mfds import collect_mfds
            mfds_key = data_go_kr_key if enable_moleg_api else None
            mfds_items, mfds_err = collect_mfds(start, end, mfds_key)
        except Exception as e:
            mfds_items, mfds_err = [], str(e)
        stats.mfds_fetched = len(mfds_items)
        if mfds_err:
            stats.mfds_error = True
            stats.mfds_error_msg = mfds_err
            log("WARN", f"MFDS 오류: {mfds_err}")
    else:
        log("INFO", "ENABLE_MFDS=false — MFDS 수집 건너뜀")

    # ── MFDS law/admrul official API replacements (ENABLE_MFDS_LAW=true) ─────
    mfds_law_items: list[IntakeItem] = []
    if enable_mfds_law:
        log("INFO", "=== MFDS 법제처 행정규칙·법령 API 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_law_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_law_items = []
        else:
            try:
                from collect_mfds_law import collect_mfds_law
                mfds_law_items, mfds_law_err = collect_mfds_law(
                    start, end, data_go_kr_service_key, law_go_kr_oc=law_go_kr_oc)
            except Exception as e:
                mfds_law_items, mfds_law_err = [], str(e)
        stats.mfds_law_fetched = len(mfds_law_items)
        if mfds_law_err:
            stats.mfds_law_error = True
            stats.mfds_law_error_msg = mfds_law_err
            log("WARN", f"MFDS Law 오류: {mfds_law_err}")
    else:
        log("INFO", "ENABLE_MFDS_LAW=false — MFDS 법제처 행정규칙·법령 API 수집 건너뜀")

    # ── Phase 2c: MFDS Recall / Self-Check (ENABLE_MFDS_RECALL=true 시 실행) ─
    mfds_recall_items: list[IntakeItem] = []
    if enable_mfds_recall:
        log("INFO", "=== MFDS 회수·판매중지 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_recall_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_recall_items = []
        else:
            try:
                from collect_mfds_recall import collect_mfds_recall
                # P0: 지연공개 대응 — 넓은 enforcement 윈도우 사용
                mfds_recall_items, mfds_recall_err = collect_mfds_recall(
                    enf_start, end, data_go_kr_service_key)
            except Exception as e:
                mfds_recall_items, mfds_recall_err = [], str(e)
        stats.mfds_recall_fetched = len(mfds_recall_items)
        if mfds_recall_err:
            stats.mfds_recall_error = True
            stats.mfds_recall_error_msg = mfds_recall_err
            log("WARN", f"MFDS Recall 오류: {mfds_recall_err}")
    else:
        log("INFO", "ENABLE_MFDS_RECALL=false — MFDS 회수·판매중지 수집 건너뜀")

    # ── Phase 2c: MFDS Administrative Actions (ENABLE_MFDS_ADMIN=true 시 실행) ─
    mfds_admin_items: list[IntakeItem] = []
    if enable_mfds_admin:
        log("INFO", "=== MFDS 행정처분 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_admin_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_admin_items = []
        else:
            try:
                from collect_mfds_admin_action import collect_mfds_admin_actions
                # P0: 지연공개 대응 — 넓은 enforcement 윈도우 사용
                mfds_admin_items, mfds_admin_err = collect_mfds_admin_actions(
                    enf_start, end, data_go_kr_service_key)
            except Exception as e:
                mfds_admin_items, mfds_admin_err = [], str(e)
        stats.mfds_admin_fetched = len(mfds_admin_items)
        if mfds_admin_err:
            stats.mfds_admin_error = True
            stats.mfds_admin_error_msg = mfds_admin_err
            log("WARN", f"MFDS Admin 오류: {mfds_admin_err}")
    else:
        log("INFO", "ENABLE_MFDS_ADMIN=false — MFDS 행정처분 수집 건너뜀")

    # ── MFDS GMP Certificate Status (ENABLE_MFDS_GMP_CERT=true 시 실행) ─────
    mfds_gmp_cert_items: list[IntakeItem] = []
    if enable_mfds_gmp_cert:
        log("INFO", "=== MFDS GMP 적합판정서 발급현황 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_gmp_cert_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_gmp_cert_items = []
        else:
            try:
                from collect_mfds_gmp_cert import collect_mfds_gmp_certs
                mfds_gmp_cert_items, mfds_gmp_cert_err = collect_mfds_gmp_certs(
                    start, end, data_go_kr_service_key)
            except Exception as e:
                mfds_gmp_cert_items, mfds_gmp_cert_err = [], str(e)
        stats.mfds_gmp_cert_fetched = len(mfds_gmp_cert_items)
        if mfds_gmp_cert_err:
            stats.mfds_gmp_cert_error = True
            stats.mfds_gmp_cert_error_msg = mfds_gmp_cert_err
            log("WARN", f"MFDS GMP Certificate 오류: {mfds_gmp_cert_err}")
    else:
        log("INFO", "ENABLE_MFDS_GMP_CERT=false — MFDS GMP 적합판정서 수집 건너뜀")

    # ── MFDS Safety Letter API (ENABLE_MFDS_SAFETY_LETTER=true 시 실행) ─────
    mfds_safety_letter_items: list[IntakeItem] = []
    if enable_mfds_safety_letter:
        log("INFO", "=== MFDS 안전성서한 API 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_safety_letter_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_safety_letter_items = []
        else:
            try:
                from collect_mfds_safety_letter import collect_mfds_safety_letters
                mfds_safety_letter_items, mfds_safety_letter_err = collect_mfds_safety_letters(
                    start, end, data_go_kr_service_key)
            except Exception as e:
                mfds_safety_letter_items, mfds_safety_letter_err = [], str(e)
        stats.mfds_safety_letter_fetched = len(mfds_safety_letter_items)
        if mfds_safety_letter_err:
            stats.mfds_safety_letter_error = True
            stats.mfds_safety_letter_error_msg = mfds_safety_letter_err
            log("WARN", f"MFDS Safety Letter 오류: {mfds_safety_letter_err}")
    else:
        log("INFO", "ENABLE_MFDS_SAFETY_LETTER=false — MFDS 안전성서한 API 수집 건너뜀")

    # ── Phase 2d: MFDS GMP Inspection Results (ENABLE_MFDS_GMP_INSPECTION=true 시 실행) ─
    mfds_gmp_inspection_items: list[IntakeItem] = []
    if enable_mfds_gmp_inspection:
        log("INFO", "=== MFDS GMP 실태조사 결과 수집 시작 ===")
        try:
            import collect_mfds_gmp_inspection as mfds_gmp_module
            mfds_gmp_inspection_items, mfds_gmp_inspection_err = mfds_gmp_module.collect_mfds_gmp_inspections(
                start, end)
            gmp_health = getattr(mfds_gmp_module, "LAST_HEALTH", {}) or {}
        except Exception as e:
            mfds_gmp_inspection_items, mfds_gmp_inspection_err = [], str(e)
            gmp_health = {}
        stats.mfds_gmp_inspection_fetched = len(mfds_gmp_inspection_items)
        stats.mfds_gmp_inspection_parse_status = dict(gmp_health.get("parse_status_counts") or {})
        stats.mfds_gmp_inspection_deficiency = dict(gmp_health.get("deficiency_counts") or {})
        stats.mfds_gmp_inspection_manual_review = int(gmp_health.get("manual_review_count") or 0)
        stats.mfds_gmp_inspection_page_warnings = list(gmp_health.get("page_warnings") or [])
        # [상세보기 결정론 승격 2026-07-02] 지적 표 추출 관측(degrade 는 요약카드 유지 — 비차단).
        deficiency_table_health = gmp_health.get("deficiency_table") or {}
        stats.gmp_deficiency_table_enabled = bool(deficiency_table_health.get("enabled"))
        stats.gmp_deficiency_table_attempted = int(deficiency_table_health.get("attempted") or 0)
        stats.gmp_deficiency_table_extracted = int(deficiency_table_health.get("extracted") or 0)
        stats.gmp_deficiency_table_failed = int(deficiency_table_health.get("failed") or 0)
        stats.gmp_deficiency_table_warnings = list(deficiency_table_health.get("warnings") or [])
        if stats.gmp_deficiency_table_failed:
            log("WARN", "MFDS GMP 지적 표 추출 degrade "
                        f"{stats.gmp_deficiency_table_failed}건(요약카드 유지): "
                        f"{stats.gmp_deficiency_table_warnings}")
        if mfds_gmp_inspection_err:
            stats.mfds_gmp_inspection_error = True
            stats.mfds_gmp_inspection_error_msg = mfds_gmp_inspection_err
            log("WARN", f"MFDS GMP Inspection 오류: {mfds_gmp_inspection_err}")
    else:
        log("INFO", "ENABLE_MFDS_GMP_INSPECTION=false — MFDS GMP 실태조사 결과 수집 건너뜀")

    # ── P1: ICH 직접 모니터링 (ENABLE_ICH=true 시 실행) ──────────────────────
    ich_items: list[IntakeItem] = []
    if enable_ich:
        log("INFO", "=== ICH 수집 시작 ===")
        try:
            from collect_ich import collect_ich
            ich_items, ich_err = collect_ich(run_date)
        except Exception as e:  # noqa: BLE001
            ich_items, ich_err = [], str(e)
        stats.ich_fetched = len(ich_items)
        if ich_err:
            stats.ich_error = True
            stats.ich_error_msg = ich_err
            log("WARN", f"ICH 오류: {ich_err}")
    else:
        log("INFO", "ENABLE_ICH=false — ICH 수집 건너뜀")

    # ── P1: WHO Prequalification (ENABLE_WHO=true 또는 --sources who) ─────────
    who_items: list[IntakeItem] = []
    if enable_who:
        log("INFO", "=== WHO 수집 시작 ===")
        try:
            import collect_who as who_module
            who_items, who_err = who_module.collect_who(start, end)
            who_health = getattr(who_module, "LAST_HEALTH", {}) or {}
        except Exception as e:  # noqa: BLE001
            who_items, who_err = [], str(e)
            who_health = {}
        stats.who_fetched = len(who_items)
        # P1: WHOPIR excerpt 실패/cap 은 graceful(링크 카드 유지) — warning 표면화용 집계.
        whopir_excerpt_health = who_health.get("whopir_excerpt") or {}
        stats.whopir_excerpt_attempted = int(whopir_excerpt_health.get("attempted") or 0)
        stats.whopir_excerpt_failed = int(whopir_excerpt_health.get("failed") or 0)
        stats.whopir_excerpt_capped = int(bool(whopir_excerpt_health.get("capped")))
        if who_err:
            stats.who_error = True
            stats.who_error_msg = who_err
            log("WARN", f"WHO 오류: {who_err}")
    else:
        log("INFO", "ENABLE_WHO=false — WHO 수집 건너뜀")

    # ── P1: Health Canada (ENABLE_HC=true 또는 --sources hc) ─────────────────
    hc_items: list[IntakeItem] = []
    if enable_hc:
        log("INFO", "=== Health Canada 수집 시작 ===")
        try:
            from collect_hc import collect_hc
            # recall/advisory 이므로 지연공개 대비 enforcement 윈도우 사용
            hc_items, hc_err = collect_hc(enf_start, end)
        except Exception as e:  # noqa: BLE001
            hc_items, hc_err = [], str(e)
        stats.hc_fetched = len(hc_items)
        if hc_err:
            stats.hc_error = True
            stats.hc_error_msg = hc_err
            log("WARN", f"HC 오류: {hc_err}")
    else:
        log("INFO", "ENABLE_HC=false — Health Canada 수집 건너뜀")

    # ── WHY-1 #3: FDA 483 (ENABLE_FDA_483=true 또는 --sources fda483) ─────────
    fda483_items: list[IntakeItem] = []
    if enable_fda483:
        log("INFO", "=== FDA 483 수집 시작 ===")
        try:
            import collect_fda_483 as fda483_module
            # 483 은 publish date 지연공개형 → enforcement 윈도우(MFDS_ENFORCEMENT_WINDOW_DAYS) 사용
            fda483_items, fda483_err = fda483_module.collect_fda_483(enf_start, end)
            fda483_health = getattr(fda483_module, "LAST_HEALTH", {}) or {}
        except Exception as e:  # noqa: BLE001
            fda483_items, fda483_err = [], str(e)
            fda483_health = {}
        stats.fda483_fetched = len(fda483_items)
        # P1: 483 excerpt 실패/cap·전수 경로 degrade 는 graceful — warning 표면화용 집계.
        fda483_excerpt_health = fda483_health.get("fda483_excerpt") or {}
        stats.fda483_excerpt_attempted = int(fda483_excerpt_health.get("attempted") or 0)
        stats.fda483_excerpt_failed = int(fda483_excerpt_health.get("failed") or 0)
        stats.fda483_excerpt_capped = int(bool(fda483_excerpt_health.get("capped")))
        stats.fda483_source_degraded = int(bool(fda483_health.get("source_degraded")))
        stats.fda483_backbone = str(fda483_health.get("backbone") or "datatables")
        fda483_obs_health = fda483_health.get("fda_483_observations") or {}
        stats.fda483_observations_enabled = bool(fda483_obs_health.get("enabled"))
        stats.fda483_observations_attempted = int(fda483_obs_health.get("attempted") or 0)
        stats.fda483_observations_extracted = int(fda483_obs_health.get("extracted") or 0)
        stats.fda483_observations_failed = int(fda483_obs_health.get("failed") or 0)
        stats.fda483_observations_warnings = list(fda483_obs_health.get("warnings") or [])
        if fda483_err:
            stats.fda483_error = True
            stats.fda483_error_msg = fda483_err
            log("WARN", f"FDA 483 오류: {fda483_err}")
    else:
        log("INFO", "ENABLE_FDA_483=false — FDA 483 수집 건너뜀")

    # ── [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak RSS (ENABLE_ISPE=true 또는
    # --sources ispe) — opt-in Expert Secondary. keep_item 관련성 필터로 협회 홍보성
    # 항목(Board 후보·Member Spotlight 등)을 차단한다(정밀도 우선). ────────────────
    ispe_items: list[IntakeItem] = []
    if enable_ispe:
        log("INFO", "=== ISPE 수집 시작 ===")
        # ISPE iSpeak 는 GMP 관련 글이 월 몇 편뿐인 성긴 블로그 → 기본 7일 창에서는
        # keep_item 통과분이 대부분 창 밖으로 빠져 만성 0 (실측 재현: 7일→0·14일→2·30일→5).
        # HC·FDA483 과 동일하게 enforcement 윈도우(기본 30일)를 사용한다. 재수집은 dedup 처리.
        ispe_items, ispe_err = collect_ispe_rss(enf_start, end)
        stats.ispe_fetched = len(ispe_items)
        if ispe_err:
            stats.ispe_error = True
            stats.ispe_error_msg = ispe_err
            log("WARN", f"ISPE 오류: {ispe_err}")
    else:
        log("INFO", "ENABLE_ISPE=false — ISPE 수집 건너뜀")

    # ── [EU GMP NCR 2026-07-23] EudraGMDP GMP 비준수 보고서 (ENABLE_EU_GMP_NCR=true 또는
    # --sources eu_gmp_ncr) — opt-in Official Page. EU/EEA 업체별 GMP 부적합을 FDA WL/483 과
    # 동일하게 News 카드 + Findings 로 편입. 발행일 지연공개형 + 성긴 소스(4년 61건)라 HC·
    # FDA483·ISPE 와 동일하게 enforcement 윈도우(기본 30일). 재수집은 dedup(doc_ref) 처리. ──
    eu_gmp_ncr_items: list[IntakeItem] = []
    if enable_eu_gmp_ncr:
        log("INFO", "=== EU GMP NCR 수집 시작 ===")
        from collect_eu_gmp_ncr import collect_eu_gmp_ncr
        eu_gmp_ncr_items, eu_gmp_ncr_err = collect_eu_gmp_ncr(enf_start, end)
        stats.eu_gmp_ncr_fetched = len(eu_gmp_ncr_items)
        if eu_gmp_ncr_err:
            stats.eu_gmp_ncr_error = True
            stats.eu_gmp_ncr_error_msg = eu_gmp_ncr_err
            log("WARN", f"EU GMP NCR 오류: {eu_gmp_ncr_err}")
    else:
        log("INFO", "ENABLE_EU_GMP_NCR=false — EU GMP NCR 수집 건너뜀")

    total_fetched = (stats.fr_fetched + stats.recall_fetched + stats.ema_fetched
                     + stats.mhra_fetched + stats.mhra_alert_fetched + stats.pics_fetched
                     + stats.eca_fetched + stats.wl_fetched
                     + stats.mfds_fetched + stats.mfds_law_fetched
                     + stats.mfds_recall_fetched
                     + stats.mfds_admin_fetched + stats.mfds_gmp_cert_fetched
                     + stats.mfds_safety_letter_fetched
                     + stats.mfds_gmp_inspection_fetched
                     + stats.ich_fetched
                     + stats.who_fetched
                     + stats.hc_fetched
                     + stats.fda483_fetched
                     + stats.ispe_fetched
                     + stats.eu_gmp_ncr_fetched)
    log("INFO", (
        f"수집 완료: FR={stats.fr_fetched} · Recall={stats.recall_fetched} · "
        f"EMA={stats.ema_fetched} · MHRA={stats.mhra_fetched} · "
        f"MHRA-Alert={stats.mhra_alert_fetched} · "
        f"PICS={stats.pics_fetched} · ECA={stats.eca_fetched} · "
        f"WL={stats.wl_fetched} · MFDS={stats.mfds_fetched} · "
        f"MFDS-Law={stats.mfds_law_fetched} · "
        f"MFDS-Recall={stats.mfds_recall_fetched} · "
        f"MFDS-Admin={stats.mfds_admin_fetched} · "
        f"MFDS-GMPCert={stats.mfds_gmp_cert_fetched} · "
        f"MFDS-SafetyLetter={stats.mfds_safety_letter_fetched} · "
        f"MFDS-GMPInspection={stats.mfds_gmp_inspection_fetched} · "
        f"ICH={stats.ich_fetched} · WHO={stats.who_fetched} · "
        f"HC={stats.hc_fetched} · FDA483={stats.fda483_fetched} · "
        f"ISPE={stats.ispe_fetched} · EU-NCR={stats.eu_gmp_ncr_fetched} · "
        f"합계={total_fetched}건"
    ))
    # ★배선 주의(PR#201/#233 교훈) — 반환 tuple 끝에 추가한 신규 소스 항목은
    # main() 언패킹(~하단)에도 동일 위치로 반드시 반영할 것(누락 시 매 실행 NameError).
    return (stats, fr_items, recall_items, ema_items, mhra_items, mhra_alert_items, pics_items, eca_items, wl_items, mfds_items, mfds_law_items, mfds_recall_items, mfds_admin_items, mfds_gmp_cert_items, mfds_safety_letter_items, mfds_gmp_inspection_items, ich_items, who_items, hc_items, fda483_items, ispe_items, eu_gmp_ncr_items)


def _write_step_summary(cfg: RunConfig, args: argparse.Namespace,
                        stats: CollectionStats, health: HealthCheckResult,
                        run_date: date, start: date, end: date,
                        handoff_emitted: bool, handoff_failed: bool,
                        handoff_row_count: int, handoff_url: str,
                        handoff_error_msg: str) -> None:
    """[배치6 Phase4] GITHUB_STEP_SUMMARY 출력(문구·행순서 불변). enable_* alias 는 cfg
    에서 재바인딩(바디 무수정)."""
    enable_mfds = cfg.enable_mfds
    enable_mfds_recall = cfg.enable_mfds_recall
    enable_mfds_admin = cfg.enable_mfds_admin
    enable_mfds_gmp_inspection = cfg.enable_mfds_gmp_inspection
    enable_ich = cfg.enable_ich
    enable_who = cfg.enable_who
    enable_hc = cfg.enable_hc
    enable_fda483 = cfg.enable_fda483
    enable_ispe = cfg.enable_ispe
    enable_eu_gmp_ncr = cfg.enable_eu_gmp_ncr
    enable_search = cfg.enable_search
    health_json_path = cfg.health_json_path
    # GitHub Actions 가 읽을 수 있는 GITHUB_STEP_SUMMARY 출력
    summary_path = cfg.step_summary_path
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## GRM Intake Collection Summary\n\n")
                f.write(f"- Run date (KST): `{run_date.isoformat()}`\n")
                f.write(f"- Window: `{start.isoformat()}` ~ `{end.isoformat()}`\n")
                def _src_line(label: str, fetched: int, inserted: int,
                              skipped: int, failed: int, error: bool,
                              err_msg: str, truncated: bool = False) -> str:
                    prefix = "⚠️ " if (error or failed > 0 or truncated) else ""
                    trunc  = " · ⚠️ TRUNCATED" if truncated else ""
                    return (f"- {prefix}{label}: fetched {fetched} · "
                            f"inserted {inserted} · skip-dup {skipped} · "
                            f"failed {failed} · error `{err_msg or 'none'}`{trunc}\n")

                f.write(_src_line("Federal Register", stats.fr_fetched, stats.fr_inserted,
                                  stats.fr_skipped_dup, stats.fr_insert_failed,
                                  stats.fr_error, stats.fr_error_msg, stats.fr_truncated))
                f.write(_src_line("OpenFDA Recall", stats.recall_fetched, stats.recall_inserted,
                                  stats.recall_skipped_dup, stats.recall_insert_failed,
                                  stats.recall_error, stats.recall_error_msg, stats.recall_truncated))
                f.write(_src_line("EMA RSS", stats.ema_fetched, stats.ema_inserted,
                                  stats.ema_skipped_dup, stats.ema_insert_failed,
                                  stats.ema_error, stats.ema_error_msg))
                f.write(_src_line("MHRA RSS", stats.mhra_fetched, stats.mhra_inserted,
                                  stats.mhra_skipped_dup, stats.mhra_insert_failed,
                                  stats.mhra_error, stats.mhra_error_msg))
                f.write(_src_line("MHRA Drug/Device Alerts", stats.mhra_alert_fetched,
                                  stats.mhra_alert_inserted, stats.mhra_alert_skipped_dup,
                                  stats.mhra_alert_insert_failed,
                                  stats.mhra_alert_error, stats.mhra_alert_error_msg))
                f.write(_src_line("PIC/S RSS", stats.pics_fetched, stats.pics_inserted,
                                  stats.pics_skipped_dup, stats.pics_insert_failed,
                                  stats.pics_error, stats.pics_error_msg))
                f.write(_src_line("ECA Academy RSS", stats.eca_fetched, stats.eca_inserted,
                                  stats.eca_skipped_dup, stats.eca_insert_failed,
                                  stats.eca_error, stats.eca_error_msg))
                f.write(_src_line("FDA Warning Letters", stats.wl_fetched, stats.wl_inserted,
                                  stats.wl_skipped_dup, stats.wl_insert_failed,
                                  stats.wl_error, stats.wl_error_msg))
                if enable_mfds:
                    f.write(_src_line("MFDS", stats.mfds_fetched, stats.mfds_inserted,
                                      stats.mfds_skipped_dup, stats.mfds_insert_failed,
                                      stats.mfds_error, stats.mfds_error_msg))
                if enable_mfds_recall:
                    f.write(_src_line("MFDS Recall", stats.mfds_recall_fetched,
                                      stats.mfds_recall_inserted,
                                      stats.mfds_recall_skipped_dup,
                                      stats.mfds_recall_insert_failed,
                                      stats.mfds_recall_error,
                                      stats.mfds_recall_error_msg))
                if enable_mfds_admin:
                    f.write(_src_line("MFDS Admin", stats.mfds_admin_fetched,
                                      stats.mfds_admin_inserted,
                                      stats.mfds_admin_skipped_dup,
                                      stats.mfds_admin_insert_failed,
                                      stats.mfds_admin_error,
                                      stats.mfds_admin_error_msg))
                if enable_mfds_gmp_inspection:
                    f.write(_src_line("MFDS GMP Inspection",
                                      stats.mfds_gmp_inspection_fetched,
                                      stats.mfds_gmp_inspection_inserted,
                                      stats.mfds_gmp_inspection_skipped_dup,
                                      stats.mfds_gmp_inspection_insert_failed,
                                      stats.mfds_gmp_inspection_error,
                                      stats.mfds_gmp_inspection_error_msg))
                if enable_ich:
                    f.write(_src_line("ICH", stats.ich_fetched, stats.ich_inserted,
                                      stats.ich_skipped_dup, stats.ich_insert_failed,
                                      stats.ich_error, stats.ich_error_msg))
                if enable_who:
                    f.write(_src_line("WHO", stats.who_fetched, stats.who_inserted,
                                      stats.who_skipped_dup, stats.who_insert_failed,
                                      stats.who_error, stats.who_error_msg))
                if enable_hc:
                    f.write(_src_line("Health Canada", stats.hc_fetched, stats.hc_inserted,
                                      stats.hc_skipped_dup, stats.hc_insert_failed,
                                      stats.hc_error, stats.hc_error_msg))
                if enable_fda483:
                    f.write(_src_line("FDA 483", stats.fda483_fetched, stats.fda483_inserted,
                                      stats.fda483_skipped_dup, stats.fda483_insert_failed,
                                      stats.fda483_error, stats.fda483_error_msg))
                if enable_ispe:
                    f.write(_src_line("ISPE iSpeak RSS", stats.ispe_fetched, stats.ispe_inserted,
                                      stats.ispe_skipped_dup, stats.ispe_insert_failed,
                                      stats.ispe_error, stats.ispe_error_msg))
                if enable_eu_gmp_ncr:
                    f.write(_src_line("EU GMP NCR (EudraGMDP)", stats.eu_gmp_ncr_fetched,
                                      stats.eu_gmp_ncr_inserted, stats.eu_gmp_ncr_skipped_dup,
                                      stats.eu_gmp_ncr_insert_failed,
                                      stats.eu_gmp_ncr_error, stats.eu_gmp_ncr_error_msg))
                if enable_search:
                    f.write(_src_line("Brave Search", stats.search_fetched, stats.search_inserted,
                                      stats.search_skipped_dup, stats.search_insert_failed,
                                      stats.search_error, stats.search_error_msg))
                if args.emit_routine_handoff:
                    if handoff_emitted:
                        f.write(f"- Routine handoff: `{handoff_row_count}` New rows · "
                                f"{handoff_url or 'url unavailable'}\n")
                    elif handoff_failed:
                        f.write(f"- ⚠️ Routine handoff: failed/skipped · "
                                f"`{handoff_error_msg[:160]}`\n")
                    else:
                        f.write("- Routine handoff: not emitted (dry-run)\n")
                f.write(f"- Dry run: `{args.dry_run}`\n")
                if stats.has_insert_failures():
                    total_fail = stats.total_insert_failures()
                    f.write(f"\n> ⚠️ **Notion 삽입 실패 {total_fail}건** — "
                            f"해당 항목은 이번 주 다이제스트에서 누락될 수 있습니다. "
                            f"Actions 로그에서 doc ID 확인 후 필요 시 수동 재실행.\n")
                if stats.has_source_errors():
                    f.write("\n> ❌ **수집기 오류 발생** — "
                            "Actions 로그에서 source별 error 메시지를 확인하세요.\n")
                    if stats.search_error:
                        f.write(f"> Brave Search error: `{stats.search_error_msg[:120] or 'none'}` "
                                f"— BRAVE_API_KEY 및 ENABLE_SEARCH 설정 확인.\n")
                    if stats.mfds_error:
                        f.write(f"> MFDS error: `{stats.mfds_error_msg[:120] or 'none'}` "
                                f"— ENABLE_MFDS 및 DATA_GO_KR_KEY 설정 확인.\n")
                    if stats.mfds_recall_error:
                        f.write(f"> MFDS Recall error: `{stats.mfds_recall_error_msg[:120] or 'none'}` "
                                f"— ENABLE_MFDS_RECALL 및 DATA_GO_KR_SERVICE_KEY 설정 확인.\n")
                    if stats.mfds_admin_error:
                        f.write(f"> MFDS Admin error: `{stats.mfds_admin_error_msg[:120] or 'none'}` "
                                f"— ENABLE_MFDS_ADMIN 및 DATA_GO_KR_SERVICE_KEY 설정 확인.\n")
                    if stats.mfds_gmp_inspection_error:
                        f.write(f"> MFDS GMP Inspection error: "
                                f"`{stats.mfds_gmp_inspection_error_msg[:120] or 'none'}` "
                                f"— ENABLE_MFDS_GMP_INSPECTION 및 nedrug HTML 구조 확인.\n")
                _write_health_summary(f, health, health_json_path)
        except OSError as e:
            log("WARN", f"STEP_SUMMARY 쓰기 실패: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="GRM API Intake Collector v15.1")
    parser.add_argument("--dry-run", action="store_true",
                        help="Notion 호출 없이 stdout 만 출력")
    parser.add_argument("--window-days", type=int, default=7,
                        choices=range(1, 91), metavar="N(1-90)",
                        help="수집 윈도우 일수 1~90 (default 7)")
    parser.add_argument("--sources", nargs="+", choices=_SOURCE_CHOICES,
                        default=None,
                        help="수집할 소스 선택 (기본: all). 예: --sources fr recall ema. "
                             "none은 feature-flag collector만 단독 실행")
    parser.add_argument("--emit-routine-handoff", action="store_true",
                        help="수집 후 Status=New row만 담은 Routine handoff 페이지를 Notion에 생성/갱신")
    parser.add_argument("--handoff-window-days", type=int, default=None,
                        choices=range(1, 91), metavar="N(1-90)",
                        help="Routine handoff 조회 윈도우. 기본 GRM_HANDOFF_WINDOW_DAYS"
                             "(미설정 시 30일 — 발행 cadence 초과로 미소비 New 누락 방지, B1)")
    parser.add_argument("--handoff-doc-ids", nargs="+", default=None,
                        help="검증/재처리용: 지정한 Document ID만 Routine handoff에 포함")
    args = parser.parse_args()

    # ── [배치6 Phase1] env·CLI 를 1회 파싱해 RunConfig 로 고정. 아래 로컬은 config 참조
    #    별칭(하위 main 본문 무수정) — Phase4 에서 하위 함수로 config 를 전달하며 정리한다. ──
    cfg = RunConfig.from_env(args)
    requested_sources = cfg.requested_sources
    explicit_sources = cfg.explicit_sources
    active = set(cfg.active)

    notion_token = cfg.notion_token
    notion_db = cfg.notion_db
    openfda_key = cfg.openfda_key
    data_go_kr_key = cfg.data_go_kr_key
    data_go_kr_service_key = cfg.data_go_kr_service_key
    law_go_kr_oc = cfg.law_go_kr_oc
    enable_search = cfg.enable_search
    enable_mfds = cfg.enable_mfds
    enable_mfds_law = cfg.enable_mfds_law
    enable_mfds_recall = cfg.enable_mfds_recall
    enable_mfds_admin = cfg.enable_mfds_admin
    enable_mfds_gmp_cert = cfg.enable_mfds_gmp_cert
    enable_mfds_safety_letter = cfg.enable_mfds_safety_letter
    enable_mfds_gmp_inspection = cfg.enable_mfds_gmp_inspection
    enable_ich = cfg.enable_ich
    enable_who = cfg.enable_who
    enable_hc = cfg.enable_hc
    enable_fda483 = cfg.enable_fda483
    enable_fda483_observations = cfg.enable_fda483_observations
    enable_ispe = cfg.enable_ispe
    enable_eu_gmp_ncr = cfg.enable_eu_gmp_ncr
    enable_moleg_api = cfg.enable_moleg_api
    enable_scrape = cfg.enable_scrape
    event_name = cfg.event_name
    health_json_path = cfg.health_json_path
    if enable_scrape:
        log("WARN", "ENABLE_SCRAPE=true 이지만 Web Scrape 수집기는 아직 미구현 — 건너뜀")

    if not args.dry_run:
        if not notion_token or not notion_db:
            log("ERROR", "NOTION_TOKEN / NOTION_DATABASE_ID 환경변수 필요")
            return 2

    # Modality 기록 활성 시 스키마 preflight — 속성 미생성/타입 불일치면 이번 실행은
    # Modality 기록만 끄고 수집은 계속(graceful degrade). preflight 는 read-only(GET)이므로
    # dry-run 에서도 토큰/DB 가 있으면 수행해, 활성화 전 검증 루프로 쓸 수 있게 한다.
    modality_requested = cfg.modality_requested
    modality_preflight_disabled = False
    modality_preflight_skipped = False
    if modality_requested and notion_token and notion_db:
        if not notion_verify_modality_property(notion_token, notion_db):
            modality_preflight_disabled = True
            # [배치6 Phase2] os.environ 변조 제거 — 아래 insert 루프가 modality_effective 를
            # build_notion_properties 로 직접 전달하므로 env 를 제어채널로 쓸 필요가 없다.
            log("WARN", "ENABLE_MODALITY_TAG=true 이나 'Modality' 스키마 불일치 — "
                        "이번 실행은 Modality 태그를 건너뜁니다(수집은 계속).")
    elif modality_requested:
        # 토큰/DB 없이 요청만 된 경우(예: 자격증명 없는 로컬 dry-run) — preflight 미수행.
        # 실제 기록도 자격증명 없이는 불가하므로 EFFECTIVE 를 false 로 두어 오해를 막는다.
        modality_preflight_skipped = True
        log("WARN", "ENABLE_MODALITY_TAG=true 이나 NOTION 자격증명이 없어 preflight 생략 — "
                    "Modality 태그 기록은 자격증명+속성이 있을 때만 동작(EFFECTIVE=false).")
    modality_effective = (modality_requested and not modality_preflight_disabled
                          and not modality_preflight_skipped)

    # 멱등성 v2 활성 시 'Handoff Ref' 스키마 preflight — 부재/타입 불일치면 이번 실행은
    # v2 만 끄고 v1(날짜 윈도우+K4-1)로 graceful degrade(Modality preflight 선례 패턴).
    handoff_idem_requested = cfg.handoff_idem_requested
    handoff_idem_preflight_disabled = False
    handoff_idem_preflight_skipped = False
    if handoff_idem_requested and notion_token and notion_db:
        if not notion_verify_handoff_ref_property(notion_token, notion_db):
            handoff_idem_preflight_disabled = True
            os.environ["ENABLE_HANDOFF_IDEMPOTENCY_V2"] = "false"
            log("WARN", "ENABLE_HANDOFF_IDEMPOTENCY_V2=true 이나 'Handoff Ref' 스키마 "
                        "불일치 — 이번 실행은 v1(날짜 윈도우) 경로로 폴백합니다(수집은 계속).")
    elif handoff_idem_requested:
        handoff_idem_preflight_skipped = True
        log("WARN", "ENABLE_HANDOFF_IDEMPOTENCY_V2=true 이나 NOTION 자격증명이 없어 "
                    "preflight 생략 — 멱등성 v2 는 자격증명+속성이 있을 때만 동작.")
    handoff_idem_effective = (handoff_idem_requested
                              and not handoff_idem_preflight_disabled
                              and not handoff_idem_preflight_skipped)

    now_k = now_kst()
    run_date = kst_run_date(now_k)
    start, end = date_window(run_date, args.window_days)
    log("INFO", f"실행일(KST)={run_date}  window={start}~{end}  dry_run={args.dry_run}")

    # ── P0 개선: data.go.kr 지연공개 대응 윈도우 ───────────────────────────────
    # 회수·행정처분은 사건일(회수명령일·최종처분일) 기준으로 윈도우를 거른다. 그런데
    # data.go.kr은 과거 일자 항목을 뒤늦게 일괄 공개하는 경우가 많아, 기본 7일 윈도우
    # 밖으로 빠지면 매일 돌려도 (날짜가 과거라) 영구 누락된다.
    # → 이 두 소스만 수집 윈도우를 넓혀 backfill하고, 중복은 dedup_window_days가 막는다.
    #    handoff는 Run Date(수집일) 기준 필터이므로, 넓은 윈도우로 오늘 새로 잡힌 과거
    #    일자 항목도 Run Date=오늘이 되어 Routine까지 정상 전달된다.
    mfds_enforcement_window_days = cfg.mfds_enforcement_window_days
    enf_start = run_date - timedelta(days=mfds_enforcement_window_days)
    log("INFO", f"MFDS enforcement window={enf_start}~{end} "
                f"({mfds_enforcement_window_days}일, 회수·행정처분 지연공개 대응)")

    (stats, fr_items, recall_items, ema_items, mhra_items, mhra_alert_items,
     pics_items, eca_items,
     wl_items, mfds_items, mfds_law_items, mfds_recall_items, mfds_admin_items,
     mfds_gmp_cert_items, mfds_safety_letter_items, mfds_gmp_inspection_items,
     ich_items, who_items, hc_items, fda483_items, ispe_items,
     eu_gmp_ncr_items) = _run_collection(
        cfg, active, run_date, start, end, enf_start)

    # 3) Notion 기존 row (중복 제거)
    # RAPS_NEWS 등 freshness=pm(31일) 소스가 있으면 dedupe 윈도우를 35일로 확장
    # (7일 윈도우 시 8일 전 RAPS URL이 누락되어 재삽입될 수 있음 — Task #13)
    _SEARCH_DEDUP_WINDOW_DAYS = 35  # pm(31일) + 여유 4일
    # P0: dedup 윈도우는 가장 넓은 수집 윈도우(enforcement 30일)를 반드시 덮어야 한다.
    # 그래야 enforcement 윈도우로 backfill된 과거 일자 항목이 다음 실행에서 중복 재삽입되지 않는다.
    dedup_window_days = max(
        args.window_days,
        mfds_enforcement_window_days,
        _SEARCH_DEDUP_WINDOW_DAYS if enable_search else 0,
    )
    log("INFO", f"dedupe window={dedup_window_days}일 (window_days={args.window_days}, enable_search={enable_search})")

    # P1: snapshot 소스(ICH·WHO)는 날짜 미단언/URL 기반 안정 스냅샷이라 매 실행 동일 항목을 재수집한다.
    # 기본 dedup 윈도우(≤30일)를 넘으면 같은 항목이 다시 New로 들어오므로(월간 중복 삽입),
    # 이들 소스만 별도로 장기(3년) Source-한정 dedup을 조회해 합친다.
    # (전체 윈도우를 3년으로 늘리면 Notion 페이지 cap에 걸릴 수 있어 Source 필터로 한정.)
    _SNAPSHOT_DEDUP_WINDOW_DAYS = 1095
    _SNAPSHOT_SOURCES = {SOURCE_ICH, SOURCE_WHO}

    if args.dry_run:
        existing: set[str] = set()
    else:
        try:
            existing = notion_query_existing_doc_ids(notion_token, notion_db, run_date,
                                                     window_days=dedup_window_days)
        except NotionDedupeQueryError as e:
            # 중복 조회 실패 시 빈 set으로 진행하면 대량 중복 insert 위험 → 중단
            log("ERROR", f"중복 조회 실패 — duplicate insert 방지를 위해 insert 단계 중단: {e}")
            return 1
        snapshot_active = ({SOURCE_ICH} if enable_ich else set()) | ({SOURCE_WHO} if enable_who else set())
        if snapshot_active:
            try:
                snap_existing = notion_query_existing_doc_ids(
                    notion_token, notion_db, run_date,
                    window_days=_SNAPSHOT_DEDUP_WINDOW_DAYS,
                    source_names=snapshot_active)
                existing |= snap_existing
                log("INFO", f"snapshot dedup({sorted(snapshot_active)}) +{len(snap_existing)}건 "
                            f"(최근 {_SNAPSHOT_DEDUP_WINDOW_DAYS}일)")
            except NotionDedupeQueryError as e:
                log("ERROR", f"snapshot dedup 조회 실패 — 중복 삽입 방지를 위해 중단: {e}")
                return 1

    collected_at = now_k
    findings_sqlite_path = None
    findings_sqlite_include_findings = False
    if cfg.findings_sqlite_append_requested:
        if args.dry_run:
            log("INFO", "ENABLE_FINDINGS_SQLITE_APPEND=true 이지만 dry-run 이므로 SQLite append 생략")
        else:
            findings_sqlite_path = cfg.findings_sqlite_path
            findings_sqlite_include_findings = cfg.findings_sqlite_findings_append_requested
            mode = "raw_signals+findings" if findings_sqlite_include_findings else "raw_signals"
            log("INFO", f"FIND-1 {mode} SQLite append 활성화 path={findings_sqlite_path}")
    elif cfg.findings_sqlite_findings_append_requested:
        log("WARN", "ENABLE_FINDINGS_SQLITE_FINDINGS_APPEND=true 이지만 "
                    "ENABLE_FINDINGS_SQLITE_APPEND=false 이므로 SQLite append 비활성")

    findings_supabase = None
    findings_supabase_include_findings = False
    if cfg.findings_supabase_append_requested:
        if args.dry_run:
            log("INFO", "ENABLE_FINDINGS_SUPABASE_APPEND=true 이지만 dry-run 이므로 Supabase append 생략")
        else:
            supabase_service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            if not cfg.findings_supabase_url or not supabase_service_key:
                log("WARN", "ENABLE_FINDINGS_SUPABASE_APPEND=true 이지만 SUPABASE_URL 또는 "
                            "SUPABASE_SERVICE_ROLE_KEY 미설정 — Supabase append 비활성")
            else:
                findings_supabase = (cfg.findings_supabase_url, supabase_service_key)
                findings_supabase_include_findings = cfg.findings_supabase_findings_append_requested
                mode = "raw_signals+findings" if findings_supabase_include_findings else "raw_signals"
                log("INFO", f"FIND-1 {mode} Supabase append 활성화 url={cfg.findings_supabase_url}")
    elif cfg.findings_supabase_findings_append_requested:
        log("WARN", "ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true 이지만 "
                    "ENABLE_FINDINGS_SUPABASE_APPEND=false 이므로 Supabase append 비활성")

    # 4) 삽입 (반환: inserted, skipped, failed)
    # [배치6 Phase2] 소스별 insert_items + stats 대입 19블록을 INTAKE_SOURCE_SPECS 레지스트리로
    # 구동한다(setattr). search 는 수집+삽입이 결합된 별도 블록(아래)이라 여기서 건너뛴다.
    # modality_enabled=modality_effective 를 전달해 build_notion_properties 의 row 당 env
    # 재독해를 제거(→ 아래 os.environ 변조도 불필요). 순서(=existing dedup 누적 순서)는 레지스트리
    # 순서로 기존과 byte 동일하다.
    _insert_items_map = {
        "fr": fr_items, "recall": recall_items, "ema": ema_items,
        "mhra": mhra_items, "mhra_alert": mhra_alert_items,
        "pics": pics_items, "eca": eca_items, "wl": wl_items,
        "mfds": mfds_items, "mfds_law": mfds_law_items,
        "mfds_recall": mfds_recall_items, "mfds_admin": mfds_admin_items,
        "mfds_gmp_cert": mfds_gmp_cert_items,
        "mfds_safety_letter": mfds_safety_letter_items,
        "mfds_gmp_inspection": mfds_gmp_inspection_items,
        "ich": ich_items, "who": who_items, "hc": hc_items,
        "fda483": fda483_items,
        "ispe": ispe_items,
        "eu_gmp_ncr": eu_gmp_ncr_items,
    }
    for spec in INTAKE_SOURCE_SPECS:
        if spec.prefix == "search":
            continue  # search 는 아래 결합 블록에서 처리
        ins, sk, fail = insert_items(
            notion_token, notion_db, _insert_items_map[spec.prefix],
            run_date, collected_at, existing, args.dry_run,
            modality_enabled=modality_effective,
            findings_sqlite_path=findings_sqlite_path,
            findings_sqlite_include_findings=findings_sqlite_include_findings,
            findings_supabase=findings_supabase,
            findings_supabase_include_findings=findings_supabase_include_findings)
        setattr(stats, f"{spec.prefix}_inserted", ins)
        setattr(stats, f"{spec.prefix}_skipped_dup", sk)
        setattr(stats, f"{spec.prefix}_insert_failed", fail)

    # ── Phase 2a: Brave Search (ENABLE_SEARCH=true 시 실행) ──────────────────
    # enable_search는 위 dedupe 윈도우 계산 시 이미 정의됨 (재정의 불필요)
    search_items: list[IntakeItem] = []  # G2: inmemory_raw 집계에서 항상 참조 가능하게 선초기화
    if enable_search:
        brave_api_key = cfg.brave_api_key
        log("INFO", "=== Brave Search 수집 시작 ===")
        try:
            from collect_search import collect_brave_search
            search_items, search_err = collect_brave_search(brave_api_key)
        except Exception as e:
            search_items, search_err = [], str(e)

        stats.search_fetched = len(search_items)
        if search_err:
            stats.search_error = True
            stats.search_error_msg = search_err
            log("WARN", f"Brave Search 오류: {search_err}")

        src_in, src_sk, src_fail = insert_items(
            notion_token, notion_db, search_items,
            run_date, collected_at, existing, args.dry_run,
            modality_enabled=modality_effective,
            findings_sqlite_path=findings_sqlite_path,
            findings_sqlite_include_findings=findings_sqlite_include_findings,
            findings_supabase=findings_supabase,
            findings_supabase_include_findings=findings_supabase_include_findings,
        )
        stats.search_inserted = src_in
        stats.search_skipped_dup = src_sk
        stats.search_insert_failed = src_fail
    else:
        log("INFO", "ENABLE_SEARCH=false — Brave Search 건너뜀")

    handoff_emitted = False
    handoff_failed = False
    handoff_row_count = 0
    handoff_url = ""
    handoff_error_msg = ""
    # B1: 윈도우는 emit 여부와 무관하게 결정(노후 미소비 New 경고 기준으로도 사용).
    handoff_window_days = cfg.handoff_window_days
    if args.emit_routine_handoff:
        if args.dry_run:
            log("INFO", "--emit-routine-handoff 지정됐지만 dry-run 이므로 Notion handoff 생성 생략")
        elif stats.has_insert_failures():
            handoff_failed = True
            handoff_error_msg = "insert failure가 있어 partial handoff 생성 생략"
            log("ERROR", f"Routine handoff 생성 생략: {handoff_error_msg}")
        else:
            try:
                handoff_sources = None
                if explicit_sources and "none" not in requested_sources:
                    handoff_sources = {
                        _SOURCE_TOKEN_TO_NOTION[src]
                        for src in requested_sources
                        if src in _SOURCE_TOKEN_TO_NOTION
                    }
                handoff_doc_ids = set(args.handoff_doc_ids or []) or None
                # G2 와이어링: 당일 수집분 raw 를 메모리로 전달(과거 New row 만 fetch 폴백).
                # v1 경로(ENABLE_HANDOFF_V2 off)는 inmemory_raw 미사용 → 무영향.
                inmemory_raw = build_inmemory_raw(
                    fr_items, recall_items, ema_items, mhra_items, pics_items, eca_items,
                    wl_items, mfds_items, mfds_law_items, mfds_recall_items,
                    mfds_admin_items, mfds_gmp_cert_items, mfds_safety_letter_items,
                    mfds_gmp_inspection_items, ich_items, who_items, hc_items,
                    fda483_items, search_items, ispe_items, eu_gmp_ncr_items)
                # §1-B: web brief emit 활성 시 산출 디렉터리(없으면 None=비활성). raw 가
                # 살아있는 handoff v2 경로 내부에서만 산출(빈슬롯 grm-web-card/v1).
                web_brief_dir = resolve_web_brief_dir() if _enable_web_brief_emit() else None
                handoff_row_count, handoff_url = emit_routine_handoff(
                    notion_token, notion_db, run_date, handoff_window_days, collected_at,
                    source_names=handoff_sources, doc_ids=handoff_doc_ids,
                    inmemory_raw=inmemory_raw,
                    # B1 조회/표시 분리: 브리프 "검색 기간"은 수집 윈도우(주간) 유지.
                    display_window_days=args.window_days,
                    web_brief_dir=web_brief_dir)
                handoff_emitted = True
            except NotionHandoffError as e:
                handoff_failed = True
                handoff_error_msg = str(e)
                log("ERROR", f"Routine handoff 생성 실패: {handoff_error_msg}")

    # B1 임시 방어 ②: 윈도우 밖 미소비 New 카운트(읽기전용 — dry-run 도 자격증명이
    # 있으면 수행해 검증 루프로 쓸 수 있다). 실패는 조용한 0 이 아니라 경고로 표면화.
    aged_unconsumed_new = 0
    aged_new_query_error = ""
    if notion_token and notion_db:
        try:
            aged_unconsumed_new = notion_count_aged_unconsumed_new(
                notion_token, notion_db, run_date, handoff_window_days)
            if aged_unconsumed_new and handoff_idem_effective:
                # Codex P2: v2 effective — 노후 New 는 ref 기반 쿼리가 자동 재투입(정보성).
                log("INFO", f"handoff 윈도우({handoff_window_days}일) 밖 미소비 New row "
                            f"{aged_unconsumed_new}건 — 멱등성 v2 자동 재투입 대상(다음 emit 포함)")
            elif aged_unconsumed_new:
                log("WARN", f"handoff 윈도우({handoff_window_days}일) 밖 미소비 New row "
                            f"{aged_unconsumed_new}건 — Routine 누락/지연 의심")
        except Exception as e:  # noqa: BLE001 — 감시 실패가 수집 자체를 죽이면 안 됨
            aged_new_query_error = str(e)
            log("WARN", f"노후 미소비 New row 카운트 조회 실패: {aged_new_query_error}")

    flags = {
        "ENABLE_SEARCH": enable_search,
        "ENABLE_MFDS": enable_mfds,
        "ENABLE_MFDS_LAW": enable_mfds_law,
        "ENABLE_MFDS_RECALL": enable_mfds_recall,
        "ENABLE_MFDS_ADMIN": enable_mfds_admin,
        "ENABLE_MFDS_GMP_CERT": enable_mfds_gmp_cert,
        "ENABLE_MFDS_SAFETY_LETTER": enable_mfds_safety_letter,
        "ENABLE_MFDS_GMP_INSPECTION": enable_mfds_gmp_inspection,
        "ENABLE_ICH": enable_ich,
        "ENABLE_WHO": enable_who,
        "ENABLE_HC": enable_hc,
        "ENABLE_FDA_483": enable_fda483,
        "ENABLE_FDA_483_OBSERVATIONS": enable_fda483_observations,
        "ENABLE_ISPE": enable_ispe,
        "ENABLE_EU_GMP_NCR": enable_eu_gmp_ncr,
        "ENABLE_MOLEG_API": enable_moleg_api,
        "ENABLE_SCRAPE": enable_scrape,
        "ENABLE_MODALITY_TAG_REQUESTED": modality_requested,
        "ENABLE_MODALITY_TAG_EFFECTIVE": modality_effective,
        "ENABLE_MODALITY_TAG_PREFLIGHT_SKIPPED": modality_preflight_skipped,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_REQUESTED": handoff_idem_requested,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_EFFECTIVE": handoff_idem_effective,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_PREFLIGHT_SKIPPED": handoff_idem_preflight_skipped,
        "ENABLE_FINDINGS_SQLITE_APPEND": cfg.findings_sqlite_append_requested,
        "ENABLE_FINDINGS_SQLITE_FINDINGS_APPEND": cfg.findings_sqlite_findings_append_requested,
        "ENABLE_FINDINGS_SUPABASE_APPEND": cfg.findings_supabase_append_requested,
        "ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND": cfg.findings_supabase_findings_append_requested,
        "MFDS_HTTP_PROXY_CONFIGURED": cfg.mfds_http_proxy_configured,
        "LAW_GO_KR_OC_CONFIGURED": bool(law_go_kr_oc),
    }
    health = _evaluate_health(
        modality_preflight_disabled=modality_preflight_disabled,
        handoff_idem_preflight_disabled=handoff_idem_preflight_disabled,
        handoff_idem_effective=handoff_idem_effective,
        stats=stats,
        active=active,
        enable_search=enable_search,
        enable_mfds=enable_mfds,
        enable_mfds_law=enable_mfds_law,
        enable_mfds_recall=enable_mfds_recall,
        enable_mfds_admin=enable_mfds_admin,
        enable_mfds_gmp_cert=enable_mfds_gmp_cert,
        enable_mfds_safety_letter=enable_mfds_safety_letter,
        enable_mfds_gmp_inspection=enable_mfds_gmp_inspection,
        enable_ich=enable_ich,
        enable_who=enable_who,
        enable_hc=enable_hc,
        enable_fda483=enable_fda483,
        enable_moleg_api=enable_moleg_api,
        enable_scrape=enable_scrape,
        event_name=event_name,
        emit_routine_handoff=args.emit_routine_handoff,
        handoff_emitted=handoff_emitted,
        handoff_failed=handoff_failed,
        handoff_error_msg=handoff_error_msg,
        aged_unconsumed_new=aged_unconsumed_new,
        aged_new_query_error=aged_new_query_error,
        handoff_window_days=handoff_window_days,
    )
    health_payload = _health_payload(
        health=health,
        stats=stats,
        run_date=run_date,
        start=start,
        end=end,
        event_name=event_name,
        dry_run=args.dry_run,
        requested_sources=list(requested_sources),
        active=active,
        flags=flags,
        handoff_emitted=handoff_emitted,
        handoff_failed=handoff_failed,
        handoff_row_count=handoff_row_count,
        handoff_url=handoff_url,
        handoff_error_msg=handoff_error_msg,
        handoff_window_days=handoff_window_days,
        aged_unconsumed_new=aged_unconsumed_new,
    )
    _write_health_json(health_json_path, health_payload)

    log("INFO", "── Collection summary ──\n" + stats.summary())

    _write_step_summary(cfg, args, stats, health, run_date, start, end,
                        handoff_emitted, handoff_failed, handoff_row_count,
                        handoff_url, handoff_error_msg)

    # 종료 코드는 _evaluate_health() 하나만 기준으로 삼는다.
    # - failure → exit 1 (workflow failure Issue)
    # - warning → exit 0 (warning Issue/summary)
    # - ok      → exit 0
    for finding in health.failures:
        log("ERROR", f"health failure [{finding.code}] {finding.source}: {finding.message}")
    for finding in health.warnings:
        log("WARN", f"health warning [{finding.code}] {finding.source}: {finding.message}")
    return health.exit_code


if __name__ == "__main__":
    sys.exit(main())
