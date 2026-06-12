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
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

from grm_common import (
    HTTPClientError,
    http_get_json,
    http_get_xml,
    log,
    retry_after_seconds,
)
# K2: 결정론 카드 골격 조립기 (같은 폴더 평면 모듈)
from card_scaffold import build_card_scaffold, compute_render_plan, merge_recall_cards


# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────

KST = ZoneInfo("Asia/Seoul")

FR_API_BASE = "https://www.federalregister.gov/api/v1/documents.json"
OPENFDA_API_BASE = "https://api.fda.gov/drug/enforcement.json"
NOTION_API_VERSION = "2022-06-28"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"
NOTION_PAGE_URL_TPL = "https://api.notion.com/v1/pages/{page_id}"
NOTION_DB_QUERY_URL_TPL = "https://api.notion.com/v1/databases/{db_id}/query"
NOTION_BLOCK_CHILDREN_URL_TPL = "https://api.notion.com/v1/blocks/{block_id}/children"

# FDA Recalls/Enforcement L2 (OpenFDA 는 항목별 사용자 친화 URL 이 없음)
FDA_RECALLS_L2 = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"

# Notion 속성 이름 (스키마 가이드와 1:1 대응 — 변경 시 양쪽 모두 수정)
PROP_NAME = "Name"
PROP_SOURCE = "Source"
PROP_DOC_ID = "Document ID"
PROP_DATE = "Date"
PROP_HEADLINE = "Headline"
PROP_OFFICIAL_URL = "Official URL"
PROP_TYPE_CLASS = "Type or Class"
PROP_FIRM = "Firm"
PROP_BODY = "Body"
PROP_DISTRIBUTION = "Distribution"
PROP_COMMENTS_CLOSE = "Comments Close"
PROP_RUN_DATE = "Run Date (KST)"
PROP_COLLECTED_AT = "Collected At"
PROP_API_QUERY = "API Query"
PROP_QA_RELEVANCE = "QA Relevance"
PROP_STATUS = "Status"
PROP_SIGNAL_TIER = "Signal Tier"
PROP_LANGUAGE = "Language"
PROP_REGION_JURISDICTION = "Region/Jurisdiction"
PROP_SITE_COUNTRY = "Site Country"
PROP_SELF_CHECK = "Self-Check Required"  # retained in Notion, no longer written by collectors

# Phase 2a 신규 Notion 필드
PROP_SOURCE_URL         = "Source URL"
PROP_RAW_EXCERPT        = "Raw Excerpt"
PROP_SEARCH_QUERY       = "Search Query"
PROP_EVIDENCE_CANDIDATE = "Evidence Candidate"

SOURCE_FR = "Federal Register"
SOURCE_RECALL = "OpenFDA Recall"
# v15.1 Phase 2 — RSS / HTML 소스
SOURCE_EMA = "EMA"
SOURCE_MHRA = "MHRA Inspectorate"
SOURCE_PICS = "PIC/S"
SOURCE_ECA = "ECA Academy"
SOURCE_FDA_WL = "FDA Warning Letter"
SOURCE_MFDS = "MFDS"
SOURCE_ICH = "ICH"
SOURCE_WHO = "WHO"
SOURCE_HC = "Health Canada"
SOURCE_FDA_483 = "FDA 483"   # WHY-1 #3 — OII FOIA Reading Room 483/EIR (가장 깊은 결함 원본)
SOURCE_HANDOFF = "GRM Handoff"
TYPE_ROUTINE_HANDOFF = "routine-handoff"
HANDOFF_SCHEMA_VERSION = "grm-routine-handoff/v1"
HANDOFF_SCHEMA_VERSION_V2 = "grm-routine-handoff/v2"  # K2 단계 D (additive)
# PL-10b/B1 근본해결: row 가 어느 handoff 에 포함됐는지 결정론적 표시(rich_text).
# 값 = handoff_id("routine-handoff::YYYY-MM-DD") — page id 가 아니라 handoff_id 를 쓰는
# 이유: ① 같은 날 재-emit 시 page 탐색 없이 자격 판정 가능 ② 사람이 읽을 수 있음
# ③ handoff page 재생성(삭제 후 upsert)에도 참조가 살아남음. Notion 속성은 사람이
# 사전 생성(ENABLE_HANDOFF_IDEMPOTENCY_V2 preflight 가 부재 시 v1 폴백).
PROP_HANDOFF_REF = "Handoff Ref"

# ── Phase 2a: Search / Scrape 소스 ──────────────────────────────────────────
SOURCE_BRAVE = "Brave Search"
SOURCE_RAPS  = "RAPS"
SOURCE_EPR   = "European Pharma Review"   # European Pharmaceutical Review

# Source Type 분류 값 (Notion Select 옵션과 1:1 대응)
PROP_SOURCE_TYPE = "Source Type"
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
PICS_RSS_URL = "https://picscheme.org/rss/general_en.rss"
ECA_RSS_URL  = "https://app.gxp-services.net/eca_newsfeed.xml"
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
# 표지/머리말을 건너뛰고 위반 서술 단락부터 잘라내기 위한 영문 앵커(가장 이른 위치 우선).
# 대소문자 무시(re.I) — 본문은 "During our inspection" 처럼 문장 첫 글자가 대문자.
_WL_BODY_ANCHORS = (
    r"during\s+(?:our|an|the)\s+inspection",
    r"we\s+(?:found|observed)\s+that",
    r"this\s+warning\s+letter",
    r"\bviolations?\b",
    r"current\s+good\s+manufacturing\s+practice",
    r"\bcgmp\b",
    r"\badulterated\b",
    r"specifically,",
)

# 13 개 카테고리 휴리스틱 키워드 (lowercase 비교, 단어 경계 매칭)
# 주의: 단독 약어("csv", "oos" 등)는 \b 경계 매칭으로 오탐 방지됨
QA_CATEGORY_KEYWORDS = [
    "gmp", "cgmp", "manufacturing practice",
    "pharmaceutical quality system", "pqs", "ich q10",
    "quality risk management", "qrm", "ich q9",
    "data integrity", "alcoa", "part 11", "annex 11",
    "computer system validation", "artificial intelligence",
    # "csv" 단독 제거 → "computer system validation" 으로 대체 (CSV 파일 형식 오탐 방지)
    "process validation", "cleaning validation",
    "analytical procedure", "ich q2", "ich q14",
    "post-approval", "cmc change", "ich q12",
    "continuous manufacturing",
    "stability", "ich q1", "oos", "oot",
    "deviation", "capa", "change control",
    "sterile", "annex 1",
    "supplier qualification",
    # OpenFDA Recall 특화 — 경구 고형제 failure mode (v15.1 추가)
    "dissolution", "assay failure", "out of specification",
    "particulate matter", "particulate contamination",
    "subpotent", "superpotent", "mislabeling", "mislabelled",
    "endotoxin",
    # 제품군 확장 — 무균·주사 품질사유 및 생물의약품(클래스 단위, 특정 제품 아님)
    "sterility", "sterility failure", "aseptic", "aseptic processing",
    "media fill", "container closure integrity", "ccit", "container closure",
    "lyophilization", "lyophilized", "visible particulate", "glass delamination",
    "cold chain", "temperature excursion", "bioburden", "pyrogen",
    "biosimilar", "monoclonal antibody", "comparability", "ich q5",
    "immunogenicity", "viral safety", "viral clearance", "cell bank",
    "parenteral",
    # Nitrosamine 계열 (FDA hot topic)
    "nitrosamine", "ndma", "ndea", "n-nitroso",
    # 주요 generic 제조사 (경쟁사 학습 가치)
    "alkem", "aurobindo", "lupin", "zydus",
    "dr. reddy", "dr reddy",
]

# Likely 가산 키워드 (경구 고형제 · 정제 직접 연관)
QA_LIKELY_BOOST = [
    "tablet", "capsule", "oral solid", "solid dosage",
    "warning letter", "dissolution", "uniformity of dosage",
    "data integrity", "annex 1", "cgmp",
    # Recall 고신호 failure mode (v15.1 추가)
    "dissolution failure", "failed dissolution",
    "nitrosamine impurity", "ndma impurity",
    # 무균·주사·바이오 직접 연관 (제품군 확장)
    "injectable", "injection", "sterile", "aseptic",
    "biosimilar", "monoclonal antibody", "container closure integrity",
    "media fill", "non-sterility", "lack of sterility assurance",
]

# 의료기기 분류 Rule 단서 (단수·복수). FR 의 "Medical Devices; Orthopedic Devices;"
# 분류고시(Rule)가 Intake 에 카드로 유입되던 갭(C-2) 차단용. 단수 단서는 복수형
# FR 제목("Medical Devices")에 단어경계로 안 걸려 누수했다(LV-C2). 단어경계 매칭이라
# 'device(s)' 가 약물전달기기·combination product 같은 정당 항목을 오배제하지 않도록,
# compute_relevance 에서 QA_DEVICE_DRUG_GUARD 가 함께 있으면 제외를 보류한다.
QA_DEVICE_EXCLUDE_TERMS = [
    "medical device", "medical devices",
    "orthopedic devices", "device only",
]

# 명시 제외 (medical device · 화장품 · 식품 · 백신 단독 등)
# 주의: "food safety" 는 단어 경계 매칭이므로 "food safety" + "drug GMP" 동시 포함 문서는
# 아래 강력 키워드 로직으로 Possible 로 살아남음
QA_EXCLUDE_KEYWORDS = [
    *QA_DEVICE_EXCLUDE_TERMS,
    "cosmetic", "cosmetics",
    "food safety", "dietary supplement label",
    "dietary supplement", "haccp", "fsvp",
    "foreign supplier verification", "seafood haccp", "juice haccp",
    "human foods program", "preventive controls for food",
    "risk-based preventive controls for food", "hazard analysis/risk-based",
    "hazard analysis/risk based",
    "veterinary only", "animal drug only", "animal drug",
    "veterinary drug", "veterinary medicine", "animal health product",
    "medicated feed",
]

# 의료기기 단서가 있어도 약물/복합제 단서가 함께면 약물전달기기·combination product
# 정당 항목으로 보고 Unrelated 로 배제하지 않는다(오배제 가드, C-2 G4).
# ⚠️ bare "drug" 금지 — FR 초록 상용구 "Food and Drug Administration" 에 항상 걸려
#    순수 기기 Rule 을 오통과시킨다(실증: bone filler). 약물전달기기·복합제를 가리키는
#    '복합 구(phrase)'만 둔다.
QA_DEVICE_DRUG_GUARD = [
    "drug product", "drug substance", "drug constituent",
    "drug delivery", "drug-eluting", "drug-coated", "drug-device",
    "biologic", "biologics", "combination product",
]

# 강한 제외(hard exclude) — boost 키워드 구제 없이 무조건 Unrelated.
# 수의/동물용은 인체 의약품과 GMP 가 겹치는 정당한 dual 사례가 없으므로 hard 로 둔다.
# (식품/의료기기-복합제/화장품-OTC 는 dual 가능성이 있어 기존 soft 구제 유지)
QA_HARD_EXCLUDE_TERMS = [
    "veterinary only", "animal drug only", "animal drug",
    "veterinary drug", "veterinary medicine", "veterinary product",
    "animal health product", "medicated feed",
]

# FDA Warning Letter 페이지는 식품 HACCP/FSVP/건기식까지 함께 노출한다.
# GRM의 1차 사용자는 경구 고형제 중심 제약 QA이므로, 명시적 식품/보충제 도메인은
# Intake 단계에서 제외한다. 단, CDER/OPQ/finished pharmaceutical 등 human drug 단서가
# 있더라도, 식품/건기식 단서가 명시되면 제외한다.
FDA_WL_LOW_VALUE_KEYWORDS = [
    "center for food safety", "cfsan",
    "human foods program",
    "office of human and animal food", "human and animal food",
    "center for veterinary medicine",
    "foreign supplier verification", "fsvp",
    "seafood haccp", "juice haccp", "haccp",
    "hazard analysis/risk-based", "hazard analysis/risk based",
    "hazard analysis and risk-based preventive controls",
    "risk-based preventive controls for food",
    "preventive controls for food",
    "preventive controls for human food",
    "food facility", "food allergen", "produce safety",
    "low-acid canned food", "acidified food", "acidified foods",
    "infant formula", "dietary supplement", "conventional food",
    "seafood processor", "juice processor", "animal food", "medicated feed",
]

# ── M0: FDA WL 발행 부서(issuing_office) 1차 게이트 (redesign §7) ──────────────
# v1.7 필터는 본문 키워드만 봐서 식품 WL 이 샜다(LV-15.7b). 발행 부서를 1차 신호로
# 추가한다. 부서는 인체 의약품(CDER/CBER)만 유지, 식품·수의·담배·기기 부서는 무조건
# 제외. OII(구 ORA)는 식품·의약품 양쪽 실사를 담당 → 본문 맥락으로 분기.
# 매칭은 _kw_any(단어경계) 기준이라 약어("cvm","oii" 등)도 substring 오탐 없음.
#
# 무조건 제외 부서 — 인체 의약품 WL 을 발행하지 않는 센터.
FDA_WL_OFFICE_EXCLUDE = {
    "cfsan": ["center for food safety and applied nutrition", "cfsan"],
    "hfp": ["human foods program", "office of human and animal food",
            "human and animal food"],
    "cvm": ["center for veterinary medicine", "cvm"],
    "ctp": ["center for tobacco products", "ctp"],
    "cdrh": ["center for devices and radiological health", "cdrh"],
}
# 유지 부서 — 인체 의약품/바이오 (CBER 유지, 제형 2차 판단은 v15.8 범위).
FDA_WL_OFFICE_KEEP = {
    "cder": ["center for drug evaluation and research", "cder"],
    "cber": ["center for biologics evaluation and research", "cber"],
}
# 맥락 의존 부서 — OII(Office of Inspections and Investigations, 구 ORA).
# 식품·수산·HACCP 맥락이면 제외, 약품 전용 단서가 있으면 유지.
FDA_WL_OFFICE_CONTEXTUAL = {
    "oii": ["office of inspections and investigations", "oii"],
}
# OII 맥락 분기용 약품 '전용' 단서(유지). 식품 단서는 FDA_WL_LOW_VALUE_KEYWORDS 재사용.
# ⚠️ 단독 `cgmp`/`current good manufacturing practice` 는 식품 WL 제목("CGMP for Foods")
# 에도 등장해 식품 WL 을 관통시킨다(Codex 실증: Stavis Seafoods). 약품에만 쓰이는
# 단서만 둔다.
FDA_WL_DRUG_ONLY_KEYWORDS = [
    "finished pharmaceutical", "finished pharmaceuticals",
    "drug product", "drug substance",
    "active pharmaceutical ingredient",
    "sterile drug", "aseptic",
]

# 13 개 카테고리 통과를 위한 최소 매칭 키워드 수
QA_MIN_MATCH = 1

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
PROP_OSD_RELEVANCE = "OSD Relevance"   # Notion select: Direct / Indirect / N/A
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
PROP_MODALITY = "Modality"
MODALITY_CHEMICAL = "Chemical"   # 화학합성(케미컬)의약품 — 제형 무관
MODALITY_BIOLOGIC = "Biologic"   # 생물의약품(생물학적제제) — 제형 무관
MODALITY_OTHER = "Other"         # 기타·판별 곤란(제품군 단서 없음: 일반 가이드라인·정책 등)
MODALITY_OPTIONS = (MODALITY_CHEMICAL, MODALITY_BIOLOGIC, MODALITY_OTHER)

# 수의/동물용 텍스트 단서 — 인체 의약품 범위 밖 → 분류 전에 하드 제외(Other).
# 구조화 product_type 가 없는 소스(FR/RSS/Search/MFDS 등) 대비. 'animal-derived' 같은
# 인체 바이오 표현을 오제외하지 않도록 '명시적 구(phrase)'만 둔다(bare 'animal' 금지).
MODALITY_VET_EXCLUDE_TERMS = [
    "veterinary drug", "veterinary medicine", "veterinary product",
    "animal drug", "animal health product", "animal-only",
    "medicated feed", "동물용의약품", "동물용 의약품", "동물약품",
]

# 생물의약품(생물학적제제) 판별 지표 — 특정 제품이 아닌 '클래스' 단위 신호
# 영문 + MFDS 한국어 단서(MFDS row 는 Language=KO 한글 원문)
MODALITY_BIOLOGIC_TERMS = [
    "biologic", "biological product", "biotechnological", "biosimilar", "biotherapeutic",
    "monoclonal", "antibody", "recombinant", "fusion protein",
    "vaccine", "cell therapy", "gene therapy", "advanced therapy", "atmp",
    "blood product", "plasma-derived", "plasma derived",
    "immunoglobulin", "immune globulin", "immune serum globulin",
    "ich q5",
    # MFDS 한국어 단서 (클래스 + 대표 생물 원료 — 라이브 실데이터로 보강)
    "생물학적제제", "생물의약품", "바이오의약품", "바이오시밀러", "동등생물의약품",
    "세포치료제", "유전자치료제", "백신", "혈장분획제제", "항체", "재조합",
    "자하거", "태반추출물", "인슐린", "인터페론", "에리트로포이에틴", "에포에틴",
    "필그라스팀", "면역글로불린", "면역혈청", "톡소이드", "항독소", "보툴리눔",
    "줄기세포", "단클론",
]
# 브랜드명만 있고 원료/클래스 텍스트가 없는 생물의약품(GAP-2) 큐레이티드 사전.
# 키 = 브랜드 핵심 토큰(소문자, 한국어/영문). 제형 접미사(정/주/캡슐 등)는 제외하고
# 브랜드 어간만 등록한다(예: '자닥신주'·'자닥신액' 모두 잡도록 '자닥신').
# 유지 정책: 라이브에서 새로 발견된 brand-only 오분류만 추가(과수집 금지). 근거 주석 1줄 필수.
MODALITY_BIOLOGIC_BRANDS = [
    "자닥신",      # thymosin alpha-1 (면역조절 펩타이드/생물학적제제); MFDS 실데이터 '자닥신주'
    "hizentra",    # 사람면역글로불린(IgG) 피하주사 — HC P7 상세 fetch 누락 시 백업
    # ↓ 라이브 재검증에서 추가로 발견되는 brand-only 생물주사제를 여기에 근거와 함께 등록
]
# 의약품(제품) 일반 단서 — 제형/투여경로 등으로 '약'임을 식별(화학·생물 공통 1차 신호)
MODALITY_DRUG_PRODUCT_TERMS = [
    "tablet", "capsule", "oral solid", "solid dosage",
    "oral solution", "oral suspension", "syrup", "oral liquid",
    "injection", "injectable", "for injection", "parenteral", "infusion",
    "vial", "ampoule", "prefilled syringe", "inhalation", "topical",
    "ophthalmic", "cream", "ointment", "suppository",
    "drug product", "finished pharmaceutical", "dosage form",
    # MFDS 한국어 단서
    "정제", "캡슐", "주사제", "주사", "시럽", "내용액제", "현탁액",
    "점안액", "연고", "크림", "흡입제", "완제의약품", "원료의약품",
]

# MFDS 제품명 제형 단서 — 한국 의약품 명명규칙(XX정/XX주/XX캡슐 등).
# ⚠️ 제품명 필드(PRDUCT/ITEM_NAME 등)에만 적용한다. haystack 전체에 적용하면
#    '개정·규정·지정·결정·공정·행정처분' 같은 일반어가 정제로 오탐된다.
MODALITY_PRODUCT_NAME_KEYS = ("PRDUCT", "ITEM_NAME", "product_description")
MODALITY_KOREAN_FORM_TERMS = [
    "캡슐", "시럽", "과립", "산제", "액제", "내용액", "점안", "점이", "점비",
    "연고", "크림", "겔", "좌제", "수액", "식염수", "주사제", "주사액",
    "흡입제", "분무", "에어로졸", "패치", "트로키", "환제", "현탁",
]
# 제품명 끝의 '정'(정제)/'주'(주사제) 접미사. 뒤에 한글이 오면(안정성·행정 등) 제외.
_KOREAN_FORM_SUFFIX_RE = re.compile(r"[가-힣A-Za-z0-9][정주](?![가-힣])")

FR_PER_PAGE = 100  # API 최대치
OPENFDA_LIMIT = 100  # no-key 한도, key 있어도 안전치
OPENFDA_MAX_TOTAL = 200  # 안전 상한 (의약품 리콜 주간 통상 < 50)

NOTION_RICH_TEXT_CHUNK = 1900  # 2000 한도, 여유 100
NOTION_CODE_BLOCK_CHUNK = 1900


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
    # ── WHY-1 #3: FDA 483/EIR ──────────────────────────────────────────────
    fda483_fetched: int = 0
    fda483_inserted: int = 0
    fda483_skipped_dup: int = 0
    fda483_insert_failed: int = 0
    fda483_error: bool = False
    fda483_error_msg: str = ""
    # P1: 483 excerpt 관측 — collect_fda_483.LAST_HEALTH 집계분. 실패/cap 은 graceful
    # (메타 카드 유지)이라 warning 으로만 표면화(flag off 면 0 → 무발생). source_degraded
    # 는 JSON 전수 경로 사망 → HTML 폴백(부분·완전성 미보장) 신호.
    fda483_excerpt_attempted: int = 0
    fda483_excerpt_failed: int = 0
    fda483_excerpt_capped: int = 0    # cap 도달 여부(0/1)
    fda483_source_degraded: int = 0   # JSON 전수 실패→HTML 폴백 여부(0/1)

    def total_insert_failures(self) -> int:
        return (
            self.fr_insert_failed + self.recall_insert_failed
            + self.ema_insert_failed + self.mhra_insert_failed
            + self.pics_insert_failed + self.eca_insert_failed
            + self.wl_insert_failed + self.search_insert_failed
            + self.mfds_insert_failed + self.mfds_recall_insert_failed
            + self.mfds_admin_insert_failed
            + self.mfds_gmp_inspection_insert_failed
            + self.ich_insert_failed
            + self.who_insert_failed
            + self.hc_insert_failed
            + self.fda483_insert_failed
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
            or self.mhra_error or self.pics_error or self.eca_error
            or self.wl_error
            or self.search_error   # Phase 2a 신규 — misconfiguration 포함
            or self.mfds_error
            or self.mfds_recall_error
            or self.mfds_admin_error
            or self.mfds_gmp_inspection_error
            or self.ich_error
            or self.who_error
            or self.hc_error
            or self.fda483_error
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
            f"MFR  fetched={self.mfds_recall_fetched}  inserted={self.mfds_recall_inserted}  "
            f"skip_dup={self.mfds_recall_skipped_dup}  failed={self.mfds_recall_insert_failed}  "
            f"error={self.mfds_recall_error}",
            f"MFA  fetched={self.mfds_admin_fetched}  inserted={self.mfds_admin_inserted}  "
            f"skip_dup={self.mfds_admin_skipped_dup}  failed={self.mfds_admin_insert_failed}  "
            f"error={self.mfds_admin_error}",
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
        ]
        return "\n".join(lines)


@dataclass
class HealthFinding:
    level: str
    code: str
    source: str
    message: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "code": self.code,
            "source": self.source,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class HealthCheckResult:
    status: str = "ok"
    exit_code: int = 0
    failures: list[HealthFinding] = field(default_factory=list)
    warnings: list[HealthFinding] = field(default_factory=list)
    infos: list[HealthFinding] = field(default_factory=list)

    def add_failure(self, code: str, source: str, message: str, detail: str = "") -> None:
        self.failures.append(HealthFinding("failure", code, source, message, detail))

    def add_warning(self, code: str, source: str, message: str, detail: str = "") -> None:
        self.warnings.append(HealthFinding("warning", code, source, message, detail))

    def add_info(self, code: str, source: str, message: str, detail: str = "") -> None:
        self.infos.append(HealthFinding("info", code, source, message, detail))

    def finalize(self) -> "HealthCheckResult":
        if self.failures:
            self.status = "failure"
            self.exit_code = 1
        elif self.warnings:
            self.status = "warning"
            self.exit_code = 0
        else:
            self.status = "ok"
            self.exit_code = 0
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "failure_count": len(self.failures),
            "warning_count": len(self.warnings),
            "info_count": len(self.infos),
            "failures": [finding.to_dict() for finding in self.failures],
            "warnings": [finding.to_dict() for finding in self.warnings],
            "infos": [finding.to_dict() for finding in self.infos],
        }


_TRANSIENT_ERROR_MARKERS = [
    "timeout", "timed out", "connecttimeouterror", "readtimeouterror",
    "connectionreseterror", "connection reset", "connection aborted",
    "remotedisconnected", "max retries exceeded", "temporary failure",
    "name resolution", "nameresolutionerror", "http 429", "rate-limit",
    "429 client error", "too many requests",
    "http 502", "http 503", "http 504", "bad gateway",
    "service unavailable", "gateway timeout",
]
_MFDS_PUBLIC_ENDPOINT_SOURCE_CODES = {"mfds-rss", "mfds-gmp-inspection"}
_MFDS_FEATURE_SOURCE_CODES = _MFDS_PUBLIC_ENDPOINT_SOURCE_CODES | {"mfds-recall", "mfds-admin"}
# ICH/WHO/HC 도 외부 공개 endpoint(admin.ich.org · extranet.who.int · recalls-rappels.canada.ca
# 정적 JSON)라 GitHub-hosted IP 간헐 차단·timeout·5xx 가 발생한다. 네트워크성 일시 오류는 MFDS
# 공개 endpoint 와 동일하게 warning(exit 0)으로 강등 — 설정·구조 오류는 마커 미포함이라 여전히
# failure. 2026-06-05 활성화 때 누락된 스코프 확장(T1).
_GLOBAL_PUBLIC_SOURCE_CODES = {"ich", "who", "health-canada", "fda483"}
_TRANSIENT_ELIGIBLE_SOURCE_CODES = _MFDS_FEATURE_SOURCE_CODES | _GLOBAL_PUBLIC_SOURCE_CODES
# 403 transient 적격: 키 없는 공개 endpoint 만(WAF/IP 차단성). data.go.kr API 403 은
# 키/서비스 권한 문제 가능성이 높아 failure 유지.
_PUBLIC_ENDPOINT_403_SOURCE_CODES = _MFDS_PUBLIC_ENDPOINT_SOURCE_CODES | _GLOBAL_PUBLIC_SOURCE_CODES


def _is_transient_source_error(code: str, detail: str) -> bool:
    """Return True for temporary network/WAF-like source failures."""
    text = (detail or "").lower()
    if not text or "환경변수 필요" in text or "api key" in text or "service_key" in text:
        return False
    if code not in _TRANSIENT_ELIGIBLE_SOURCE_CODES:
        return False
    if any(marker in text for marker in _TRANSIENT_ERROR_MARKERS):
        return True
    # MFDS/nedrug·ICH/WHO/HC public HTML/RSS endpoints can intermittently block
    # GitHub-hosted IPs. Keep data.go.kr API 403s as failures because they usually
    # mean key/service permission.
    if code in _PUBLIC_ENDPOINT_403_SOURCE_CODES and (
        "http 403" in text or "403 forbidden" in text or "403 client error" in text
    ):
        return True
    return False


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


def truncate(text: str, limit: int = NOTION_RICH_TEXT_CHUNK) -> str:
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def chunk_text(text: str, size: int = NOTION_RICH_TEXT_CHUNK) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


def _env_int(name: str, default: int) -> int:
    """환경변수를 정수로 안전 파싱. 비정상 값이면 WARN 후 default 사용 (graceful degradation)."""
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        log("WARN", f"{name}={raw!r} 정수 파싱 실패 — default {default} 사용")
        return default


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


def _kw_match(blob: str, keywords: list[str]) -> int:
    """단어 경계(\b) 기반 키워드 매칭 카운트.
    복합어("manufacturing practice")는 전체 구문을 단어 경계로 감쌈.
    단독 약어("oos", "oot", "pqs") 오탐 방지.
    """
    count = 0
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, blob):
            count += 1
    return count


def _kw_any(blob: str, keywords: list[str]) -> bool:
    return _kw_match(blob, keywords) > 0


def _phrase_any(blob: str, keywords: list[str]) -> bool:
    return any(kw in blob for kw in keywords)


def _is_low_value_fda_warning_letter(*text_parts: str) -> bool:
    blob = " ".join(t for t in text_parts if t).lower()
    if not blob.strip():
        return False
    return _phrase_any(blob, FDA_WL_LOW_VALUE_KEYWORDS)


def _fda_wl_office_gate(issuing_office: str, *context_parts: str) -> str:
    """FDA WL 발행 부서(issuing_office) 기반 1차 게이트 (M0, redesign §7).

    반환:
      - "exclude": 무조건 제외 부서(식품/수의/담배/기기) 또는 OII+식품맥락(약품 전용
                   단서 없음) → 드롭.
      - "keep":    인체 의약품 부서(CDER/CBER) 또는 OII+약품 전용 단서(식품맥락 없음) → 유지.
      - "review":  OII 인데 식품·약품 단서가 둘 다 있거나 둘 다 없음 → 보수적 유지(비-드롭,
                   약품 WL 오삭제 방지). 전용 Status 마킹은 K4 이월.
      - "unknown": 부서 결측/미매핑 → 호출부에서 본문 키워드 폴백(회귀 방지).
    """
    office = (issuing_office or "").lower().strip()
    if not office:
        return "unknown"
    # 1) 무조건 제외 부서 — 인체 의약품 WL 을 발행하지 않는 센터.
    for tokens in FDA_WL_OFFICE_EXCLUDE.values():
        if _kw_any(office, tokens):
            return "exclude"
    # 2) 유지 부서 — 인체 의약품/바이오.
    for tokens in FDA_WL_OFFICE_KEEP.values():
        if _kw_any(office, tokens):
            return "keep"
    # 3) 맥락 의존 부서(OII) — 식품 맥락을 약품 단서보다 '먼저' 평가한다.
    #    식품만→제외 · 약품만→유지 · 둘 다 또는 둘 다 없음→review(보수적 유지, 약품 WL
    #    오삭제 방지). 단독 cgmp 가 식품 WL("CGMP for Foods")을 관통하던 갭 차단(P1).
    for tokens in FDA_WL_OFFICE_CONTEXTUAL.values():
        if _kw_any(office, tokens):
            ctx = " ".join(p for p in context_parts if p).lower()
            food = _phrase_any(ctx, FDA_WL_LOW_VALUE_KEYWORDS) or "for food" in ctx
            drug = _phrase_any(ctx, FDA_WL_DRUG_ONLY_KEYWORDS)
            if food and not drug:
                return "exclude"       # 식품/수산/HACCP/FSVP/"for foods" → 제외
            if drug and not food:
                return "keep"          # 약품 전용 단서만 → 유지
            return "review"            # 둘 다 / 둘 다 없음 → 유지(오삭제 방지)
    # 4) 미매핑 부서 → 본문 키워드 폴백.
    return "unknown"


def compute_relevance(*text_parts: str) -> str:
    blob = " ".join(t for t in text_parts if t).lower()
    if not blob.strip():
        return "Pending"
    # 수의/동물용 등 hard exclude 는 boost 구제 없이 무조건 Unrelated
    if _kw_any(blob, QA_HARD_EXCLUDE_TERMS):
        return "Unrelated"
    if _kw_any(blob, QA_EXCLUDE_KEYWORDS):
        # 가드: 의료기기 단서로 인한 제외라도 약물/복합제 단서가 함께면 약물전달기기·
        # combination product 정당 항목으로 보고 일반 분류로 진행(오배제 방지, C-2 G4).
        device_guarded = (_kw_any(blob, QA_DEVICE_EXCLUDE_TERMS)
                          and _kw_any(blob, QA_DEVICE_DRUG_GUARD))
        if not device_guarded:
            # 명시 제외 키워드가 있어도 Likely 가산 키워드 2개 이상이면 Possible 로 구제
            strong = _kw_match(blob, QA_LIKELY_BOOST)
            if strong >= 2:
                return "Possible"
            return "Unrelated"
    matches = _kw_match(blob, QA_CATEGORY_KEYWORDS)
    if matches < QA_MIN_MATCH:
        return "Pending"
    boosts = _kw_match(blob, QA_LIKELY_BOOST)
    if boosts >= 1:
        return "Likely"
    return "Possible"


# ─────────────────────────────────────────────────────────────────────────────
# Federal Register 수집
# ─────────────────────────────────────────────────────────────────────────────
# OSD Relevance 분류 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


def _as_lower_set(value: Any) -> set[str]:
    """openfda.route / dosage_form 필드를 안전하게 소문자 set으로 변환.

    OpenFDA API 는 list[str] 를 반환하는 것이 정상이지만,
    string / None / 기타 타입이 오더라도 예외 없이 처리한다.
    """
    if value is None:
        return set()
    if isinstance(value, str):
        return {value.lower()}
    if isinstance(value, list):
        return {str(v).lower() for v in value if v}
    return {str(value).lower()}


# 경구 고형제 판정에 사용하는 부분문자열 토큰 (exact set 매칭 대신)
OSD_SOLID_TERMS = [
    "tablet", "capsule", "oral solid", "solid dosage",
    "extended-release", "delayed-release",
    "orally disintegrating", "chewable",
]


def compute_osd_relevance(raw_payload: dict[str, Any]) -> str:
    """OpenFDA raw payload 에서 경구 고형제(OSD) 직접 관련성 판정.

    분류 기준 (v15.1 개선):
        "Direct"   — dosage_form 에 tablet/capsule/oral solid 계열 단어 포함
                     (exact match 가 아닌 부분문자열 매칭으로 복합 형태 처리)
        "Indirect" — tablet/capsule 확인 안 됐지만 route=oral 이거나
                     product_description 에 경구 단서 있음
        "N/A"      — 경구/고형제 근거 없음

    설계 의도:
        시스템 목표가 "경구 고형제(정제) 중심"이므로
        oral solution/suspension 은 route=oral 이더라도 Direct 가 아닌 Indirect 로 분류.
        Recall Tier 분류에서 Direct → Tier 2/3 후보, Indirect → 경계 항목으로 재확인.
    """
    openfda = raw_payload.get("openfda") or {}
    routes = _as_lower_set(openfda.get("route"))
    forms = _as_lower_set(openfda.get("dosage_form"))

    # 1순위: dosage_form 에 고형제 토큰 포함 여부 (부분문자열)
    if any(term in f for f in forms for term in OSD_SOLID_TERMS):
        return "Direct"

    # 2순위: route=oral 이면 경구 투여 확인 → Indirect (oral solution/suspension 포함)
    if "oral" in routes:
        return "Indirect"

    # 3순위: openfda 필드 없거나 미제공 시 product_description 에서 단서 탐색
    product = (raw_payload.get("product_description") or "").lower()
    if re.search(r"\b(tablets?|capsules?|oral)\b", product):
        return "Indirect"

    return "N/A"


# ─────────────────────────────────────────────────────────────────────────────
# 제품군(Modality) 분류 (제품군 확장)
# ─────────────────────────────────────────────────────────────────────────────


def compute_modality(raw_payload: dict[str, Any], *text_parts: str) -> str:
    """수집 항목의 제품군(Modality)을 '큰 틀'(원료 성격)로 1차 자동 분류한다.

    특정 제품(예: 성장호르몬·항암주사)이 아니라 클래스 단위로만 본다.
    OpenFDA 의 구조화 필드(product_type/dosage_form/route)가 있으면 우선 사용하고,
    없으면 제목·본문·분류 텍스트의 키워드로 판정한다.

    반환값:
        "Biologic" — 생물의약품(생물학적제제): 재조합 단백질·항체·백신·세포/유전자
                     치료제·바이오시밀러·혈장분획제제 등 (제형 무관)
        "Chemical" — 화학합성(케미컬)의약품: 생물 단서 없이 의약품(제형/투여경로)
                     단서가 있는 합성 저분자 의약품 (제형 무관)
        "Other"    — 제품군 단서 없음(일반 가이드라인·정책·실태조사 일반 등)

    설계 의도:
        제형을 잘게 나누면 오분류가 늘어나므로 원료 성격 3분류로만 단순화한다.
        생물 단서가 우선(생물의약품은 그 자체로 하나의 군), 그 외 의약품 단서는
        화학합성으로 본다. 세부 제형(정제/주사/액상)은 카드 본문 route/form 으로 표기.
    """
    openfda = raw_payload.get("openfda") or {}
    # product_type 은 openfda.product_type 우선, 없으면 top-level product_type 폴백
    # (HC 등 openfda 구조가 없는 소스 대응)
    product_type = _as_lower_set(openfda.get("product_type") or raw_payload.get("product_type"))
    forms = _as_lower_set(openfda.get("dosage_form") or raw_payload.get("dosage_form"))
    routes = _as_lower_set(openfda.get("route") or raw_payload.get("route"))
    product = (raw_payload.get("product_description") or "").lower()
    blob = " ".join(t for t in text_parts if t).lower()
    haystack = " ".join(
        [blob, " ".join(forms), " ".join(routes), " ".join(product_type), product]
    )

    # 수의/동물용은 인체 의약품 범위 밖 → 모든 분류 이전에 하드 제외(Other).
    #  (a) 구조화 product_type 기준  (b) 명시적 텍스트 구(phrase) 기준 — 둘 다 early-return.
    if any(("veterin" in pt or "animal" in pt) for pt in product_type):
        return MODALITY_OTHER
    if _phrase_any(haystack, MODALITY_VET_EXCLUDE_TERMS):
        return MODALITY_OTHER

    # 1순위: 생물의약품(생물학적제제)
    if any("biolog" in pt for pt in product_type):
        return MODALITY_BIOLOGIC
    if _phrase_any(haystack, MODALITY_BIOLOGIC_TERMS):
        return MODALITY_BIOLOGIC
    # GAP-2: 브랜드명만 있는 생물의약품 — 제형 접미사(2순위 d)·product_type 'drug'에
    #        가려지기 전에 가로챈다. 제품명 필드 + haystack 양쪽에서 브랜드 어간을 찾는다
    #        (haystack 은 PRDUCT/ITEM_NAME 을 포함하지 않으므로 제품명 필드를 별도로 합친다).
    _brand_blob = haystack
    for _k in MODALITY_PRODUCT_NAME_KEYS:
        _v = raw_payload.get(_k)
        if _v:
            _brand_blob = _brand_blob + " " + str(_v).lower()
            break
    if any(b.lower() in _brand_blob for b in MODALITY_BIOLOGIC_BRANDS):
        return MODALITY_BIOLOGIC
    # 단클론항체 INN 접미사 '-mab'(adalimumab·rituximab 등)만 단어 끝에서 매칭.
    # (bare "mab" 부분문자열은 'Mabel' 류 오탐을 내므로 접미사 정규식으로 한정)
    if re.search(r"\b[a-z]{3,}mab\b", haystack):
        return MODALITY_BIOLOGIC

    # 2순위: 화학합성의약품
    #  (a) product_type 이 'drug' 계열(예: Drugs / Human prescription drug)
    if any("drug" in pt for pt in product_type):
        return MODALITY_CHEMICAL
    #  (b) 생물 단서는 없고 의약품(제형/투여경로) 단서가 있으면
    if forms or routes:
        return MODALITY_CHEMICAL
    #  (c) 텍스트 제형 단서 — 단, '정제수'(purified water) 는 '정제'(tablet) 오탐이므로 제거
    haystack_dp = haystack.replace("정제수", "")
    if _phrase_any(haystack_dp, MODALITY_DRUG_PRODUCT_TERMS):
        return MODALITY_CHEMICAL
    #  (d) MFDS 한국어 제품명 제형 단서 — 제품명 필드에만 적용(개정/규정 등 일반어 오탐 방지).
    #      한국 의약품은 XX정(정제)/XX주(주사제)/XX캡슐 처럼 본문에 '정제'라는 단어 없이
    #      제품명 접미사로만 제형이 드러나는 경우가 많다(라이브 검증에서 ~40% 누락 확인).
    product_name = ""
    for k in MODALITY_PRODUCT_NAME_KEYS:
        v = raw_payload.get(k)
        if v:
            product_name = str(v)
            break
    if product_name:
        pn = product_name.replace("정제수", "")
        if (_phrase_any(pn, MODALITY_KOREAN_FORM_TERMS)
                or _KOREAN_FORM_SUFFIX_RE.search(pn)):
            return MODALITY_CHEMICAL

    # 3순위: 기타·판별 곤란(제품군 단서 없음)
    return MODALITY_OTHER


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
# EMA RSS 수집 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


def collect_ema_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """EMA 공식 RSS 피드 4개 (scientific-guidelines · inspections · news ·
    regulatory-guidelines) 를 수집해 날짜 필터링 후 반환.

    Source Type: Official API (EMA 공식 RSS 피드).
    Evidence Level: A 불가 — RSS 요약이므로 B (Official direct identified) 이상.
    """
    items: list[IntakeItem] = []
    errors: list[str] = []

    for feed_name, feed_url in EMA_RSS_FEEDS.items():
        log("INFO", f"EMA RSS 수집: {feed_name} ({feed_url})")
        try:
            root = http_get_xml(feed_url)
        except Exception as e:
            msg = f"EMA RSS '{feed_name}' 실패: {e}"
            log("WARN", msg)
            errors.append(msg)
            continue

        # RSS 2.0 형식 확인
        rss_items = _rss2_items_from_root(root)
        for el in rss_items:
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

            if not _within_window(date_iso, start, end):
                continue

            doc_id = _stable_doc_id(SOURCE_EMA, title, link, date_iso)
            relevance = compute_relevance(title, description, category)
            tier = compute_signal_tier(SOURCE_EMA, category or feed_name, relevance,
                                       "N/A", title, description, category)

            items.append(IntakeItem(
                source=SOURCE_EMA,
                document_id=doc_id,
                date_iso=date_iso,
                headline=title,
                official_url=link,
                type_or_class=category or feed_name,
                body=description,
                api_query=feed_url,
                qa_relevance=relevance,
                osd_relevance="N/A",
                source_type=SRC_TYPE_OFFICIAL_API,
                signal_tier=tier,
                raw_payload={
                    "feed": feed_name,
                    "title": title,
                    "link": link,
                    "pubDate": pub_raw,
                    "description": description,
                    "category": category,
                    "guid": guid,
                },
            ))

    err_msg = "; ".join(errors) if errors else None
    # 오류가 있어도 다른 피드에서 수집한 항목은 반환 (graceful degradation)
    log("INFO", f"EMA RSS 수집 완료: {len(items)}건 (errors={len(errors)})")
    return items, err_msg if errors else None


# ─────────────────────────────────────────────────────────────────────────────
# MHRA Inspectorate RSS 수집 (v15.1) — Atom 형식
# ─────────────────────────────────────────────────────────────────────────────


def collect_mhra_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """MHRA Inspectorate Blog RSS (Atom 형식) 수집.

    Source Type: Official Regulator Blog.
    URL: https://mhrainspectorate.blog.gov.uk/feed/
    """
    log("INFO", f"MHRA RSS 수집: {MHRA_RSS_URL}")
    try:
        root = http_get_xml(MHRA_RSS_URL)
    except Exception as e:
        log("WARN", f"MHRA RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    entries = _atom_entries_from_root(root)

    for entry in entries:
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

        # category
        cat_el = _rss_find(entry, f"{{{_NS_ATOM}}}category", "category")
        category = (cat_el.get("term", "") if cat_el is not None else "").strip()

        # Atom id 를 document_id 로 사용
        id_el = _rss_find(entry, f"{{{_NS_ATOM}}}id", "id")
        guid  = _rss_text(id_el) or link

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_MHRA, title, link, date_iso)
        relevance = compute_relevance(title, summary, category)
        tier      = compute_signal_tier(SOURCE_MHRA, category or "Blog", relevance,
                                        "N/A", title, summary, category)

        items.append(IntakeItem(
            source=SOURCE_MHRA,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class=category or "Blog",
            body=summary,
            api_query=MHRA_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_BLOG,
            signal_tier=tier,
            raw_payload={
                "title": title, "link": link,
                "published": pub_raw, "summary": summary,
                "category": category, "id": guid,
            },
        ))

    log("INFO", f"MHRA RSS 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# PIC/S RSS 수집 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


def collect_pics_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """PIC/S 공식 RSS 수집.

    Source Type: Official Regulatory Page.
    URL: https://picscheme.org/rss/general_en.rss
    """
    log("INFO", f"PIC/S RSS 수집: {PICS_RSS_URL}")
    try:
        root = http_get_xml(PICS_RSS_URL)
    except Exception as e:
        log("WARN", f"PIC/S RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    rss_items = _rss2_items_from_root(root)

    for el in rss_items:
        title   = _rss_text(el.find("title"))
        link    = _rss_text(el.find("link"))
        pub_raw = _rss_text(el.find("pubDate")) or _rss_text(el.find("pubdate"))
        date_iso = _parse_rss2_date(pub_raw) if pub_raw else ""
        description = _rss_text(el.find("description"))
        guid_el = el.find("guid")
        guid    = _rss_text(guid_el) or link

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_PICS, title, link, date_iso)
        relevance = compute_relevance(title, description)
        tier      = compute_signal_tier(SOURCE_PICS, "PIC/S", relevance,
                                        "N/A", title, description)

        items.append(IntakeItem(
            source=SOURCE_PICS,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class="PIC/S",
            body=description,
            api_query=PICS_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            signal_tier=tier,
            raw_payload={
                "title": title, "link": link,
                "pubDate": pub_raw, "description": description, "guid": guid,
            },
        ))

    log("INFO", f"PIC/S RSS 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# ECA Academy RSS 수집 (v15.1) — Expert Secondary
# ─────────────────────────────────────────────────────────────────────────────


def collect_eca_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """ECA Academy (gmp-compliance.org) RSS 수집.

    Source Type: Expert Secondary — FDA·EMA·MHRA·TGA·PIC/S·ICH 전문 GMP 뉴스 큐레이션.
    URL: https://app.gxp-services.net/eca_newsfeed.xml
    403 발생 시 운영 경고 없이 진행 (Expert Secondary 허용 정책).
    """
    log("INFO", f"ECA RSS 수집: {ECA_RSS_URL}")
    try:
        root = http_get_xml(ECA_RSS_URL)
    except HTTPClientError as e:
        # Expert Secondary: 403/404 는 경고 없이 넘어감
        log("INFO", f"ECA RSS HTTP {e.status_code} — 건너뜀 (Expert Secondary 정책)")
        return [], None
    except Exception as e:
        log("WARN", f"ECA RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    # ECA 피드는 RSS 2.0 또는 Atom 모두 가능 — 두 방향 시도
    rss_items = _rss2_items_from_root(root)
    if not rss_items:
        rss_items = _atom_entries_from_root(root)  # type: ignore[assignment]

    for el in rss_items:
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

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_ECA, title, link, date_iso)
        relevance = compute_relevance(title, description)
        tier      = compute_signal_tier(SOURCE_ECA, "GMP News", relevance,
                                        "N/A", title, description)

        items.append(IntakeItem(
            source=SOURCE_ECA,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class="GMP News",
            body=description,
            api_query=ECA_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_EXPERT_SECONDARY,
            signal_tier=tier,
            raw_payload={
                "title": title, "link": link,
                "pubDate": pub_raw, "description": description, "guid": guid,
            },
        ))

    log("INFO", f"ECA RSS 수집 완료: {len(items)}건")
    return items, None


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


def _extract_wl_body_excerpt(html_text: str) -> str:
    """WL 본문에서 위반 서술 구간 excerpt(가장 이른 앵커부터). 앵커 없으면 ""(키 미기록).

    표지/머리말 보일러플레이트가 아니라 위반 서술을 카드 컨텍스트("왜")로 올리기 위한 추출.
    FDA 페이지는 nav/푸터가 많아 앵커 미발견 시 앞부분 폴백을 하지 않고 메타 카드를 유지한다.
    """
    text = _wl_html_to_text(html_text)
    if not text:
        return ""
    best: int | None = None
    for pat in _WL_BODY_ANCHORS:
        m = re.search(pat, text, re.I)
        if m and (best is None or m.start() < best):
            best = m.start()
    if best is None:
        return ""
    return text[best:best + WL_BODY_MAX_CHARS].strip()


# WHY-1 #2 P1: WL 본문 excerpt 관측용 — collect_who.LAST_HEALTH 동형 패턴.
# collect_fda_warning_letters 가 매 호출 갱신하고 오케스트레이터가 stats 로 옮긴다.
LAST_WL_HEALTH: dict[str, Any] = {}


def _fetch_wl_body_excerpt(url: str) -> str:
    """WL 편지 페이지 fetch → 위반 excerpt. 실패(403/timeout/네트워크)는 graceful("")."""
    try:
        resp = requests.get(url, timeout=WL_BODY_FETCH_TIMEOUT, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", f"FDA WL 본문 403 — excerpt 건너뜀(메타 카드 유지): {truncate(url, 80)}")
            return ""
        resp.raise_for_status()
    except requests.RequestException as e:
        log("WARN", f"FDA WL 본문 fetch 실패(메타 카드 유지): {truncate(str(e), 120)}")
        return ""
    excerpt = _extract_wl_body_excerpt(resp.text)
    if not excerpt:
        log("INFO", f"FDA WL 본문 위반 앵커 미발견 — 메타 카드 유지: {truncate(url, 80)}")
    return excerpt


def collect_fda_warning_letters(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA Warning Letters 페이지 HTML 테이블 파싱 수집.

    Source Type: Official Regulatory Page.
    페이지: https://www.fda.gov/.../warning-letters

    FDA WL 페이지는 정적 HTML 테이블을 포함하므로 WebFetch 가능.
    JS-heavy 인 경우 content 부재 → 빈 결과 반환 (fail-silent).
    403/timeout 시 WARN 로그 후 빈 결과 반환.
    """
    log("INFO", f"FDA WL 수집: {FDA_WL_URL}")
    wl_body_enabled = os.environ.get("ENABLE_WL_BODY", "false").lower() == "true"
    # P1: excerpt 시도/실패 집계 — 시작 시점에 전역을 교체해(이른 return 포함) 항상
    # 이번 호출 분만 남긴다. dict 는 in-place 갱신이라 이후 증가분이 그대로 반영.
    global LAST_WL_HEALTH
    wl_body_health: dict[str, Any] = {
        "enabled": wl_body_enabled, "attempted": 0, "failed": 0,
    }
    LAST_WL_HEALTH = {"wl_body": wl_body_health}
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
        if wl_body_enabled and wl_href:
            wl_body_health["attempted"] += 1
            body_excerpt = _fetch_wl_body_excerpt(wl_href)
            if body_excerpt:
                wl_raw["wl_body_excerpt"] = body_excerpt
            else:
                wl_body_health["failed"] += 1

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


def notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


class NotionDedupeQueryError(RuntimeError):
    """Notion 중복 조회 실패 전용 예외 — insert 중단 판단에 사용."""
    pass


class NotionHandoffError(RuntimeError):
    """Routine handoff 생성/갱신 실패 전용 예외."""
    pass


def notion_api_request(method: str, url: str, token: str, *,
                       body: dict[str, Any] | None = None,
                       retries: int = 2) -> dict[str, Any]:
    """Notion JSON API 호출 공통 래퍼. 429/5xx 는 짧게 재시도한다."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, json=body,
                                    headers=notion_headers(token), timeout=30)
            if resp.status_code == 429 and attempt < retries:
                sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                log("WARN", f"Notion API 429 rate-limit — {sleep_s}s 후 재시도 "
                            f"({attempt + 1}/{retries + 1})")
                time.sleep(sleep_s)
                continue
            if resp.status_code >= 500 and attempt < retries:
                log("WARN", f"Notion API {resp.status_code} — 재시도 "
                            f"({attempt + 1}/{retries + 1}) body={resp.text[:200]}")
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise NotionHandoffError(
                    f"Notion API {method} {url} 실패 ({resp.status_code}): "
                    f"{resp.text[:300]}"
                )
            if not resp.text:
                return {}
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                log("WARN", f"Notion API 네트워크 오류 — 재시도 "
                            f"({attempt + 1}/{retries + 1}) err={e}")
                time.sleep(2 ** attempt)
                continue
            break
        except ValueError as e:
            raise NotionHandoffError(f"Notion API JSON 파싱 실패: {e}") from e
    raise NotionHandoffError(f"Notion API {method} {url} 실패: {last_err}")


def notion_verify_modality_property(token: str, db_id: str) -> bool:
    """ENABLE_MODALITY_TAG=true 활성화 시 Notion 'Modality' 속성 사전 점검(preflight).

    DB 에 'Modality' 가 Select 타입으로 존재하는지 확인한다. 없거나 타입이 다르면
    첫 insert 부터 전부 실패하므로, 그 전에 깨끗하게 False 를 반환해 호출부가
    'N건 insert 실패' 대신 '스키마 불일치'로 한 번에 알리고 graceful degrade 하도록 한다.

    반환: True = 기록 진행 OK / False = 스키마 불일치(이번 실행 Modality 기록 건너뜀).
    (Select 옵션 Chemical/Biologic/Other 누락은 insert 시 자동 생성되므로 경고만.)
    """
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        data = notion_api_request("GET", url, token)
    except NotionHandoffError as e:
        log("WARN", f"Modality preflight: DB 조회 실패 — {e}")
        return False
    prop = (data.get("properties", {}) or {}).get(PROP_MODALITY)
    if not prop:
        log("ERROR", f"Modality preflight 실패: Notion DB 에 '{PROP_MODALITY}' 속성이 없습니다. "
                     f"Select 속성(옵션 {', '.join(MODALITY_OPTIONS)})을 먼저 생성하세요.")
        return False
    ptype = prop.get("type")
    if ptype != "select":
        log("ERROR", f"Modality preflight 실패: '{PROP_MODALITY}' 속성 타입이 '{ptype}' — "
                     f"'select' 여야 합니다.")
        return False
    options = {o.get("name") for o in (prop.get("select", {}).get("options") or [])}
    missing = set(MODALITY_OPTIONS) - options
    if missing:
        log("WARN", f"Modality preflight: select 옵션 {sorted(missing)} 미존재 "
                    f"— insert 시 자동 생성됨(스키마 의도 확인 권장).")
    else:
        log("INFO", f"Modality preflight OK — '{PROP_MODALITY}' select 옵션 {sorted(options)}")
    return True


def notion_verify_handoff_ref_property(token: str, db_id: str) -> bool:
    """ENABLE_HANDOFF_IDEMPOTENCY_V2=true 활성화 시 'Handoff Ref' 속성 사전 점검(preflight).

    DB 에 'Handoff Ref' 가 rich_text 타입으로 존재하는지 확인한다. 없거나 타입이 다르면
    emit 의 ref 기록·reconcile 이 전부 실패하므로, 그 전에 False 를 반환해 호출부가
    이번 실행만 v2 를 끄고 v1(날짜 윈도우+K4-1)으로 graceful degrade 하도록 한다
    (`notion_verify_modality_property` 선례와 동일 패턴).
    """
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        data = notion_api_request("GET", url, token)
    except NotionHandoffError as e:
        log("WARN", f"Handoff Ref preflight: DB 조회 실패 — {e}")
        return False
    prop = (data.get("properties", {}) or {}).get(PROP_HANDOFF_REF)
    if not prop:
        log("ERROR", f"Handoff Ref preflight 실패: Notion DB 에 '{PROP_HANDOFF_REF}' 속성이 "
                     f"없습니다. Rich text 속성을 먼저 생성하세요.")
        return False
    ptype = prop.get("type")
    if ptype != "rich_text":
        log("ERROR", f"Handoff Ref preflight 실패: '{PROP_HANDOFF_REF}' 속성 타입이 "
                     f"'{ptype}' — 'rich_text' 여야 합니다.")
        return False
    log("INFO", f"Handoff Ref preflight OK — '{PROP_HANDOFF_REF}' rich_text 확인")
    return True


def notion_query_existing_doc_ids(token: str, db_id: str, run_date: date,
                                  window_days: int = 7,
                                  source_names: set[str] | None = None) -> set[str]:
    """최근 window_days 일(KST Run Date 기준) row 의 'source::document_id' key set 반환.

    daily 수집 전환(Phase 1)으로 dedupe 윈도우를 '당일' → '최근 window_days 일'로 확장.
    동일 항목이 윈도우 내 여러 daily run 에서 재삽입되는 것을 방지한다.

    dedupe key 형식: "{source}::{doc_id}"
    예) "Federal Register::{doc_id}", "OpenFDA Recall::{doc_id}",
        "Brave Search::{sha1(url)[:12]}" (Phase 2a 신규)
    Source 를 포함해 소스 간 ID 충돌을 방지한다.

    Raises:
        NotionDedupeQueryError: 조회 실패 시 — caller 가 insert 중단 여부를 결정.
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    existing: set[str] = set()
    window_start = (run_date - timedelta(days=window_days)).isoformat()
    and_filters: list[dict[str, Any]] = [
        {"property": PROP_RUN_DATE, "date": {"on_or_after": window_start}},
        {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
    ]
    # source_names 지정 시 Source 한정(snapshot 소스 long-horizon dedup용).
    if source_names:
        and_filters.append({
            "or": [{"property": PROP_SOURCE, "select": {"equals": s}}
                   for s in sorted(source_names)]
        })
    body: dict[str, Any] = {
        "filter": {"and": and_filters},
        "page_size": 100,
    }
    start_cursor: str | None = None
    page_count = 0
    # P2 개선: dedup 윈도우가 enforcement(최대 30일)×전 소스로 넓어졌으므로 상한을 상향한다.
    # 100p × 100 = 10,000 row 헤드룸. 그래도 초과하면 partial 반환 대신 예외(아래 for-else).
    _DEDUP_MAX_PAGES = 100
    try:
        for _ in range(_DEDUP_MAX_PAGES):  # 안전 페이지 상한
            page_count += 1
            if start_cursor:
                body["start_cursor"] = start_cursor
            elif "start_cursor" in body:
                del body["start_cursor"]
            data: dict[str, Any] | None = None
            for attempt in range(3):
                resp = requests.post(url, json=body, headers=notion_headers(token), timeout=30)
                if resp.status_code == 429 and attempt < 2:
                    sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                    log("WARN", f"Notion dedupe 429 rate-limit — {sleep_s}s 후 재시도 "
                                f"({attempt + 1}/3)")
                    time.sleep(sleep_s)
                    continue
                if resp.status_code >= 500 and attempt < 2:
                    log("WARN", f"Notion dedupe 조회 실패 ({resp.status_code}) "
                                f"attempt={attempt + 1}/3 body={resp.text[:200]}")
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            if data is None:
                raise NotionDedupeQueryError(
                    f"Notion 중복 조회 실패 (RunDate={run_date}): empty response"
                )
            for pg in data.get("results", []):
                props = pg.get("properties", {})
                # Source
                src = (props.get(PROP_SOURCE, {}).get("select") or {}).get("name", "")
                # Document ID
                doc_id_arr = props.get(PROP_DOC_ID, {}).get("rich_text", [])
                doc_id = "".join(rt.get("plain_text", "") for rt in doc_id_arr).strip()
                if src and doc_id:
                    existing.add(f"{src}::{doc_id}")
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
        else:
            # for-else: 상한을 모두 소진했는데 break 되지 않음 = has_more 잔존 = 상한 도달.
            # P2 개선: 일부 기존 row를 놓친 채 진행하면 중복 삽입 방어가 깨지므로,
            # WARN 후 partial 반환 대신 예외를 던져 caller(main)가 insert를 중단하게 한다.
            raise NotionDedupeQueryError(
                f"Notion 중복 조회 {_DEDUP_MAX_PAGES}페이지 상한 도달 — "
                f"dedup set 불완전(existing={len(existing)}건), 중복 삽입 방지 위해 중단"
            )
    except (requests.RequestException, ValueError) as e:
        # 중복 조회 실패 시 빈 set을 반환하면 모든 item을 신규로 판단해 대량 중복 insert 위험.
        # 안전하게 예외를 던져 caller 가 insert 중단 여부를 결정하도록 한다.
        raise NotionDedupeQueryError(
            f"Notion 중복 조회 실패 (RunDate={run_date}): {e}"
        ) from e
    log("INFO", f"Notion 기존 row {len(existing)} 건 (최근 {window_days}일, ~{run_date})")
    return existing


def _rich_text(text: str) -> list[dict[str, Any]]:
    """Notion rich_text 배열로 분할 (각 element ≤ 2000자)."""
    if not text:
        return []
    return [{"type": "text", "text": {"content": chunk}}
            for chunk in chunk_text(text, NOTION_RICH_TEXT_CHUNK)]


def _select(name: str) -> dict[str, Any]:
    return {"select": {"name": name}}


def _date_iso(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    return {"date": {"start": value}}


def _datetime_iso(value: datetime) -> dict[str, Any]:
    # Notion 은 ISO-8601 with offset 허용
    return {"date": {"start": value.isoformat()}}


def _url(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    return {"url": value}


def _plain_text(parts: list[dict[str, Any]] | None) -> str:
    if not parts:
        return ""
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _prop_title(props: dict[str, Any], name: str) -> str:
    return _plain_text(props.get(name, {}).get("title", []))


def _prop_rich_text(props: dict[str, Any], name: str) -> str:
    return _plain_text(props.get(name, {}).get("rich_text", []))


def _prop_select(props: dict[str, Any], name: str) -> str:
    return (props.get(name, {}).get("select") or {}).get("name", "") or ""


def _prop_date(props: dict[str, Any], name: str) -> str:
    return (props.get(name, {}).get("date") or {}).get("start", "") or ""


def _prop_url(props: dict[str, Any], name: str) -> str:
    return props.get(name, {}).get("url") or ""


def _intake_page_snapshot(page: dict[str, Any]) -> dict[str, Any]:
    props = page.get("properties", {})
    return {
        "page_id": page.get("id", ""),
        "page_url": page.get("url", ""),
        "title": _prop_title(props, PROP_NAME),
        "source": _prop_select(props, PROP_SOURCE),
        "document_id": _prop_rich_text(props, PROP_DOC_ID),
        "date": _prop_date(props, PROP_DATE),
        "headline": _prop_rich_text(props, PROP_HEADLINE),
        "official_url": _prop_url(props, PROP_OFFICIAL_URL),
        "source_url": _prop_url(props, PROP_SOURCE_URL),
        "type_or_class": _prop_select(props, PROP_TYPE_CLASS),
        "firm": _prop_rich_text(props, PROP_FIRM),
        "body": _prop_rich_text(props, PROP_BODY),
        "distribution": _prop_rich_text(props, PROP_DISTRIBUTION),
        "comments_close": _prop_date(props, PROP_COMMENTS_CLOSE),
        "run_date": _prop_date(props, PROP_RUN_DATE),
        "collected_at": _prop_date(props, PROP_COLLECTED_AT),
        "api_query": _prop_url(props, PROP_API_QUERY),
        "search_query": _prop_rich_text(props, PROP_SEARCH_QUERY),
        "raw_excerpt": _prop_rich_text(props, PROP_RAW_EXCERPT),
        "qa_relevance": _prop_select(props, PROP_QA_RELEVANCE),
        "osd_relevance": _prop_select(props, PROP_OSD_RELEVANCE),
        "modality": _prop_select(props, PROP_MODALITY),
        "source_type": _prop_select(props, PROP_SOURCE_TYPE),
        "signal_tier": _prop_select(props, PROP_SIGNAL_TIER),
        "evidence_candidate": _prop_select(props, PROP_EVIDENCE_CANDIDATE),
        "language": _prop_select(props, PROP_LANGUAGE),
        "region_jurisdiction": _prop_select(props, PROP_REGION_JURISDICTION),
        "site_country": _prop_rich_text(props, PROP_SITE_COUNTRY),
        "status": _prop_select(props, PROP_STATUS),
    }


# B1 임시 방어: handoff 조회 윈도우는 발행 cadence(주간 7일)보다 커야 주간 Routine 이
# 1회 지연돼도 미소비 New row 가 Run Date 하한 밖으로 빠져 영구 누락되지 않는다.
# 기본 30일은 dedup(MFDS enforcement 30일)과 정합. enforcement 의미를 handoff 에
# 섞지 않도록 전용 환경변수를 둔다. 근본 해결(날짜 하한 제거)은 PL-10b 와 별도 트랙.
_DEFAULT_HANDOFF_WINDOW_DAYS = 30


def resolve_handoff_window_days(cli_value: int | None) -> int:
    """handoff 조회 윈도우 결정 — CLI(--handoff-window-days) > GRM_HANDOFF_WINDOW_DAYS > 30."""
    if cli_value:
        return cli_value
    return _env_int("GRM_HANDOFF_WINDOW_DAYS", _DEFAULT_HANDOFF_WINDOW_DAYS)


def notion_query_new_intake_rows(token: str, db_id: str, run_date: date,
                                 window_days: int = 7,
                                 source_names: set[str] | None = None,
                                 doc_ids: set[str] | None = None,
                                 current_handoff_id: str | None = None,
                                 current_handoff_open: bool = True
                                 ) -> list[dict[str, Any]]:
    """Routine 에 넘길 Status=New row 를 Notion API 속성 필터로 조회한다.

    `current_handoff_id` 지정(멱등성 v2) 시 소비 자격을 날짜 윈도우가 아니라
    Handoff Ref 로 판정한다: `Status=New ∧ (Ref 비어있음 ∨ Ref=오늘 handoff)` —
    Run Date 하한 제거(PL-10b/B1 근본해결). `Ref=오늘` OR 절은 같은 날 재-emit 때
    이미 표시된 row 가 누락되지 않게 한다. 미지정(v1) 시 기존 날짜 윈도우 동작 그대로.

    `current_handoff_open=False`(Codex P1): 오늘 handoff 가 이미 CONSUMED/STALE 로
    종결된 경우 — `Ref=오늘` OR 절을 빼고 `Ref 비어있음` 만 자격으로 인정한다.
    이미 발행된 handoff 의 잔존 New row(Status 갱신 실패분)가 같은 날 재실행에서
    재유입되는 것을 차단한다(그 잔존분 마감은 reconcile 의 CONSUMED-cleanup 몫).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    window_start = (run_date - timedelta(days=window_days)).isoformat()
    if current_handoff_id:
        # v2: 날짜 하한 없음 — 상한(미래 Run Date 방어)과 ref 자격만.
        if current_handoff_open:
            ref_clause: dict[str, Any] = {"or": [
                {"property": PROP_HANDOFF_REF, "rich_text": {"is_empty": True}},
                {"property": PROP_HANDOFF_REF, "rich_text": {"equals": current_handoff_id}},
            ]}
        else:
            ref_clause = {"property": PROP_HANDOFF_REF, "rich_text": {"is_empty": True}}
        filters: list[dict[str, Any]] = [
            {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
            ref_clause,
        ]
    else:
        filters = [
            {"property": PROP_RUN_DATE, "date": {"on_or_after": window_start}},
            {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
        ]
    body: dict[str, Any] = {
        "filter": {"and": filters},
        "page_size": 100,
    }
    snapshots: list[dict[str, Any]] = []
    start_cursor: str | None = None
    for page_no in range(50):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            if source_names and snap["source"] not in source_names:
                continue
            if doc_ids and snap["document_id"] not in doc_ids:
                continue
            if not snap["source"] or not snap["document_id"]:
                log("WARN", f"handoff 후보 row 필수 키 누락 — skip page={snap['page_id']}")
                continue
            snapshots.append(snap)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        log("WARN", "Routine handoff New row 조회 50페이지 상한 도달 — 일부 row 누락 가능")

    if current_handoff_id:
        log("INFO", f"Routine handoff 후보 New row {len(snapshots)}건 "
                    f"(멱등성 v2 ref 기반 — Run Date ≤{run_date.isoformat()}, 날짜 하한 없음)")
    else:
        log("INFO", f"Routine handoff 후보 New row {len(snapshots)}건 "
                    f"(Run Date {window_start}~{run_date.isoformat()})")
    return snapshots


# B1 임시 방어 ②: 윈도우(30일)로도 못 막는 케이스(Routine 3주+ 누락)를 침묵 대신
# 경고로 띄운다. 카운트 목적이라 전수 페이지네이션 불요 — 상한 도달 시 하한값으로
# 충분하다(경고 트리거는 N>0 여부, 정확 수는 사람이 Notion 에서 확인).
_AGED_NEW_MAX_PAGES = 5
_AGED_NEW_PAGE_SIZE = 50


def notion_count_aged_unconsumed_new(token: str, db_id: str, run_date: date,
                                     handoff_window_days: int) -> int:
    """handoff 조회 윈도우 밖에 남은 미소비 Status=New row 수(읽기전용, 하한값).

    필터: Status=New AND Run Date on_or_before (run_date − handoff_window_days − 1)
    — notion_query_new_intake_rows 하한(on_or_after run_date−window) 바로 바깥.
    handoff 페이지 자체(SOURCE_HANDOFF/TYPE_ROUTINE_HANDOFF)는 큐 row 가 아니므로
    동일 규칙으로 제외. 조회 실패는 예외를 그대로 올린다 — 호출부(main)가
    try/except 후 경고로 표면화한다(조용한 0 반환 금지).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    cutoff = (run_date - timedelta(days=handoff_window_days + 1)).isoformat()
    body: dict[str, Any] = {
        "filter": {"and": [
            {"property": PROP_RUN_DATE, "date": {"on_or_before": cutoff}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
        ]},
        "page_size": _AGED_NEW_PAGE_SIZE,
    }
    count = 0
    start_cursor: str | None = None
    for _ in range(_AGED_NEW_MAX_PAGES):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            count += 1
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return count


# ── K2-prep: page_id 로 원 row raw API JSON 복원 → rows 에 부착 (card_spec §12(A)) ──
# v1 handoff snapshot 에는 raw 가 없다(원본 API 응답 JSON 전체는 Intake row 본문
# code block 에만 있고, `raw_excerpt` 속성은 잘린 발췌라 불충분). raw 의존 칸(W3 인용·
# MFDS W2·Modality 폴백)은 이 보강 후에만 결정론적이다(redesign §4, card_spec §12(A)).
# ⚠️ 이 단계는 네트워크 호출이므로 build_card_scaffold() 안에 두지 않는다(§12(G) 순수성).
_INTAKE_RAW_MAX_PAGES = 25  # children 100/page · raw 는 ≤2KB 청크라 1페이지로 충분(안전 상한)


def fetch_intake_raw_payload(token: str, page_id: str) -> dict[str, Any] | None:
    """Intake row(page_id) 본문의 JSON code block 들을 순서대로 이어붙여 raw dict 복원.

    `build_notion_children()` 이 저장한 'Raw API payload' code block(language=json,
    NOTION_CODE_BLOCK_CHUNK 청크)을 역으로 재조립한다. fetch/파싱 실패 시 None 반환
    (호출부 graceful degrade — 예외를 던지지 않아 전체 handoff 를 중단시키지 않는다).
    """
    if not page_id:
        return None
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    code_chunks: list[str] = []
    start_cursor: str | None = None
    try:
        for _ in range(_INTAKE_RAW_MAX_PAGES):
            req_url = url
            if start_cursor:
                req_url = f"{url}?start_cursor={urllib.parse.quote(start_cursor)}"
            data = notion_api_request("GET", req_url, token)
            for block in data.get("results", []):
                if block.get("type") != "code":
                    continue
                code = block.get("code", {})
                if (code.get("language") or "") not in ("json", "plain text", ""):
                    continue
                for rt in code.get("rich_text", []):
                    code_chunks.append(
                        rt.get("plain_text")
                        or rt.get("text", {}).get("content", "")
                        or ""
                    )
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
    except (NotionHandoffError, requests.RequestException) as e:
        log("WARN", f"K2-prep children fetch 실패 page={page_id}: {truncate(str(e), 120)}")
        return None
    if not code_chunks:
        return None
    raw_text = "".join(code_chunks)
    try:
        parsed = json.loads(raw_text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def attach_raw_to_rows(token: str, rows: list[dict[str, Any]],
                       inmemory_raw: dict[str, dict[str, Any]] | None = None,
                       sleep_s: float = 0.34) -> dict[str, int]:
    """각 row 에 raw API JSON 을 부착한다(하이브리드 — 지시문 단계 B).

    `inmemory_raw`(당일 수집분 raw_payload, key = `source::document_id`)에 있으면
    네트워크 없이 그대로 사용하고, 없으면(과거 누적 New row) page children 을 fetch 한다.
    실패 row 는 graceful degrade(card_spec §8, Codex 정정): raw=None ·
    raw_fetch_ok=False · evidence_hint='B'(A 불가) · status_hint='Error'(기존 DB
    옵션; 전용 'Needs Review' 옵션 신설은 K4 이월). 전체 중단 금지.

    ⚠️ raw 는 메모리 상 enriched row 에만 부착한다 — 최종 handoff v2 JSON 에는 넣지
    않는다(scaffold·prose_input 만; 크기 폭증·Notion children 한도 방지, 단계 B 보정).
    반환: {'ok','failed','from_memory','total'} 통계.
    """
    inmemory_raw = inmemory_raw or {}
    ok = failed = from_memory = 0
    for row in rows:
        card_id = f"{row.get('source', '')}::{row.get('document_id', '')}"
        cached = inmemory_raw.get(card_id)
        if cached is not None:
            row["raw"] = cached
            row["raw_fetch_ok"] = True
            row["raw_source"] = "memory"
            ok += 1
            from_memory += 1
            continue
        page_id = row.get("page_id", "")
        raw = fetch_intake_raw_payload(token, page_id)
        if raw is None:
            row["raw"] = None
            row["raw_fetch_ok"] = False
            row["raw_source"] = "fetch"
            row["evidence_hint"] = "B"
            row["status_hint"] = "Error"
            failed += 1
            log("WARN", "K2-prep raw 부착 실패 → graceful degrade(Evidence B·Status Error): "
                        f"{card_id} page={page_id}")
        else:
            row["raw"] = raw
            row["raw_fetch_ok"] = True
            row["raw_source"] = "fetch"
            ok += 1
        if sleep_s:
            time.sleep(sleep_s)  # 실제 fetch 한 경우만 rate-limit 대기
    log("INFO", f"K2-prep raw 부착 완료: 성공 {ok}건(메모리 {from_memory}·fetch {ok - from_memory}) "
                f"/ 실패 {failed}건 (총 {len(rows)})")
    return {"ok": ok, "failed": failed, "from_memory": from_memory, "total": len(rows)}


def enrich_rows_with_raw(token: str, rows: list[dict[str, Any]],
                         inmemory_raw: dict[str, dict[str, Any]] | None = None,
                         sleep_s: float = 0.34
                         ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """K2-prep 진입점: `_dedupe_latest_rows()` 선적용(중복 fetch 제거) → 하이브리드 raw 부착.

    handoff v2 생성 직전 단계. 반환 (deduped_rows, stats). v2 payload 빌더가 이 결과를
    소비하되 raw 는 JSON 직렬화에서 제외한다(단계 B·D 보정).
    """
    deduped = _dedupe_latest_rows(rows)
    stats = attach_raw_to_rows(token, deduped, inmemory_raw=inmemory_raw, sleep_s=sleep_s)
    return deduped, stats


def build_inmemory_raw(*item_lists: list["IntakeItem"]) -> dict[str, dict[str, Any]]:
    """당일 수집 IntakeItem 들을 `{card_id: raw_payload}` 로 모은다(K3 G2 와이어링).

    key = `source::document_id`(handoff row·`attach_raw_to_rows` 와 동일 규약). 이 dict 를
    `emit_routine_handoff(inmemory_raw=...)` 로 넘기면 당일 수집분은 children fetch 없이
    메모리에서 raw 를 부착하고, 과거 누적 New row 만 fetch 폴백한다(혼합 케이스).
    raw_payload 가 비어 있으면 제외 — `attach_raw_to_rows` 는 `get(card_id) is not None`
    으로 적중 판정하므로 빈 dict 가 들어가면 fetch 폴백을 가로채 graceful degrade 를 막는다.
    중복 card_id 는 첫 항목 우선(수집 순서 결정론).
    """
    out: dict[str, dict[str, Any]] = {}
    for items in item_lists:
        for it in items:
            if not it.raw_payload:
                continue
            out.setdefault(f"{it.source}::{it.document_id}", it.raw_payload)
    return out


def _dedupe_latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row.get('source', '')}::{row.get('document_id', '')}"
        current = latest.get(key)
        freshness = (row.get("run_date", ""), row.get("collected_at", ""), row.get("page_id", ""))
        if current is None:
            latest[key] = row
            continue
        current_freshness = (
            current.get("run_date", ""),
            current.get("collected_at", ""),
            current.get("page_id", ""),
        )
        if freshness > current_freshness:
            latest[key] = row

    tier_order = {"Tier 3": 0, "Tier 2": 1, "Tier 1": 2}
    return sorted(
        latest.values(),
        key=lambda r: (
            tier_order.get(r.get("signal_tier", ""), 9),
            r.get("source", ""),
            r.get("document_id", ""),
        ),
    )


def build_routine_handoff_payload(rows: list[dict[str, Any]], run_date: date,
                                  window_days: int,
                                  generated_at: datetime) -> dict[str, Any]:
    start = run_date - timedelta(days=window_days)
    deduped = _dedupe_latest_rows(rows)
    source_counts: dict[str, int] = {}
    for row in deduped:
        source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "handoff_id": f"routine-handoff::{run_date.isoformat()}",
        "run_date_kst": run_date.isoformat(),
        "window_start": start.isoformat(),
        "window_end": run_date.isoformat(),
        "generated_at_kst": generated_at.isoformat(),
        "row_count": len(deduped),
        "source_counts": source_counts,
        "rows": deduped,
    }


def _enable_handoff_v2() -> bool:
    """handoff v2 feature flag (기본 off, vars fallback 패턴). 운영 전환은 K3 와 함께."""
    return os.environ.get("ENABLE_HANDOFF_V2", "false").lower() == "true"


def _enable_handoff_idempotency_v2() -> bool:
    """PL-10b/B1 근본해결 flag (기본 off) — Handoff Ref 상태기계로 소비 자격 판정.

    off = 현행(날짜 윈도우 + K4-1 STALE) 100% 동일. on 전환은 K3 4주 관찰 종료 후
    사람 승인으로(Notion 'Handoff Ref' rich_text 속성 사전 생성 필요 — preflight 가
    부재를 감지하면 그 실행만 v1 으로 폴백). ENABLE_HANDOFF_V2(payload 스키마)와 직교.
    """
    return os.environ.get("ENABLE_HANDOFF_IDEMPOTENCY_V2", "false").lower() == "true"


# v2 row 에 보존할 v1 호환 필드(whitelist) — _intake_page_snapshot() 스키마.
# blacklist 대신 whitelist 로(Codex P2): raw·Stage B 부착 bookkeeping(raw_fetch_ok·
# raw_source·status_hint·evidence_hint) 같은 내부/대형 필드가 새지 않도록 보장.
_HANDOFF_V2_ROW_KEEP = (
    "page_id", "page_url", "title", "source", "document_id", "date", "headline",
    "official_url", "source_url", "type_or_class", "firm", "body", "distribution",
    "comments_close", "run_date", "collected_at", "api_query", "search_query",
    "raw_excerpt", "qa_relevance", "osd_relevance", "modality", "source_type",
    "signal_tier", "evidence_candidate", "language", "region_jurisdiction",
    "site_country", "status",
)


def build_routine_handoff_payload_v2(rows: list[dict[str, Any]], run_date: date,
                                     window_days: int,
                                     generated_at: datetime) -> dict[str, Any]:
    """handoff v2(additive) payload. 순수 함수 — 네트워크 없음(scaffold 조립만).

    `rows` 는 K2-prep(`enrich_rows_with_raw`)로 **dedupe·raw 부착**된 상태여야 한다.
    각 row 는 v1 호환 필드 whitelist 복사 + `card_scaffold`·`prose_input`·`section`·
    `card_id`·`evidence`·`recall_group_key`(해당 시)·`status_hint`(degrade 시) additive.
    **raw 전체·Stage B bookkeeping 은 제외**(크기 폭증·내부필드 누출 방지).
    recall 다품목은 `merge_recall_cards()`(§14)로 대표 1카드 + 멤버 `merged_into` 직렬화:
    멤버 row 는 v1 호환 필드 + `merged_into` 만 유지(자체 card_id 포함 v2 additive 필드 전부
    생략 → Routine 렌더 제외, page_id 보존으로 Status 갱신 목록에는 잔존, Codex R1-a).
    대표/단독 row 는 `render_order`·`group_label`(A안, R1-d)로 §7 정렬·그룹핑 결과를 받아
    Routine 이 정렬을 재현하지 않게 한다(`compute_render_plan` = assemble_brief_skeleton 공유).
    """
    start = run_date - timedelta(days=window_days)
    cards = merge_recall_cards([build_card_scaffold(row, row.get("raw")) for row in rows])
    render_plan = compute_render_plan(cards)
    out_rows: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for row, card in zip(rows, cards):
        v2row = {k: row[k] for k in _HANDOFF_V2_ROW_KEEP if k in row}
        if card.merged_into:
            # §14(F)·R1-a 멤버: v1 호환 필드 + merged_into 만(자체 card_id·card_scaffold·
            # prose_input·needs_llm_slots·section·evidence·recall_group_key·render_order 생략).
            # 렌더 제외, page_id 보존으로 Status 갱신 목록에만 잔존. 그룹 식별은 merged_into 로.
            v2row["merged_into"] = card.merged_into
        else:
            v2row["card_id"] = card.card_id
            v2row["section"] = card.section
            v2row["evidence"] = card.evidence
            v2row["card_scaffold"] = card.markdown
            v2row["prose_input"] = card.prose_input
            v2row["needs_llm_slots"] = list(card.needs_llm_slots)
            plan = render_plan.get(card.card_id)
            if plan is not None:
                v2row["render_order"] = plan["render_order"]
                if plan["group_label"]:
                    v2row["group_label"] = plan["group_label"]
            if card.recall_group_key:
                v2row["recall_group_key"] = card.recall_group_key
            if card.status_hint:
                v2row["status_hint"] = card.status_hint
        out_rows.append(v2row)
        source_counts[row.get("source", "")] = source_counts.get(row.get("source", ""), 0) + 1
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION_V2,
        "handoff_id": f"routine-handoff::{run_date.isoformat()}",
        "run_date_kst": run_date.isoformat(),
        "window_start": start.isoformat(),
        "window_end": run_date.isoformat(),
        "generated_at_kst": generated_at.isoformat(),
        "row_count": len(out_rows),
        "source_counts": source_counts,
        "rows": out_rows,
    }


def _handoff_page_properties(payload: dict[str, Any],
                             generated_at: datetime) -> dict[str, Any]:
    run_date = payload["run_date_kst"]
    row_count = payload["row_count"]
    title = f"OPEN GRM Routine Handoff {run_date}"
    body = (
        f"New-only Routine handoff. rows={row_count}; "
        f"window={payload['window_start']}~{payload['window_end']}; "
        f"generated_at={payload['generated_at_kst']}"
    )
    return {
        PROP_NAME: {"title": _rich_text(title)},
        PROP_SOURCE: _select(SOURCE_HANDOFF),
        PROP_DOC_ID: {"rich_text": _rich_text(payload["handoff_id"])},
        PROP_DATE: {"date": {"start": run_date}},
        PROP_HEADLINE: {"rich_text": _rich_text(f"Routine New-only handoff ({row_count} rows)")},
        PROP_TYPE_CLASS: _select(TYPE_ROUTINE_HANDOFF),
        PROP_BODY: {"rich_text": _rich_text(body)},
        PROP_RUN_DATE: {"date": {"start": run_date}},
        PROP_COLLECTED_AT: _datetime_iso(generated_at),
        PROP_STATUS: _select("New"),
    }


def _handoff_blocks(payload: dict[str, Any], compact: bool = False) -> list[dict[str, Any]]:
    # v2(compact=True): sort_keys 결정론 + 공백 제거(크기 절감, §12G). v1: 기존 indent=2 유지(바이트 동일).
    if compact:
        json_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"))
    else:
        json_payload = json.dumps(payload, ensure_ascii=False, indent=2)
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rich_text("GRM Routine Handoff")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(
                f"HANDOFF_ID: {payload['handoff_id']} | "
                f"SCHEMA: {payload['schema_version']} | "
                f"ROW_COUNT: {payload['row_count']}"
            )},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(
                f"WINDOW: {payload['window_start']}~{payload['window_end']} | "
                f"GENERATED_AT_KST: {payload['generated_at_kst']}"
            )},
        },
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": _rich_text("Payload JSON")},
        },
    ]
    for chunk in chunk_text(json_payload, NOTION_CODE_BLOCK_CHUNK):
        blocks.append({
            "object": "block",
            "type": "code",
            "code": {
                "language": "json",
                "rich_text": [{"type": "text", "text": {"content": chunk}}],
            },
        })
    return blocks


def notion_find_handoff_page(token: str, db_id: str,
                             handoff_id: str) -> dict[str, Any] | None:
    body = {
        "filter": {
            "property": PROP_DOC_ID,
            "rich_text": {"equals": handoff_id},
        },
        "page_size": 5,
    }
    data = notion_api_request("POST", NOTION_DB_QUERY_URL_TPL.format(db_id=db_id),
                              token, body=body)
    results = data.get("results", [])
    if not results:
        return None
    results.sort(key=lambda p: p.get("last_edited_time", ""), reverse=True)
    return results[0]


def notion_stale_prior_open_handoffs(token: str, db_id: str,
                                     keep_handoff_id: str,
                                     superseded_by: str,
                                     revert_refs: bool = False) -> int:
    """새 OPEN handoff emit 전, 직전 미소비 OPEN handoff 를 STALE 로 봉인한다(K4-1).

    Type or Class=`routine-handoff` 이고 Status=`New`(=OPEN) 인 handoff page 중
    handoff_id 가 `keep_handoff_id` 와 다른 것을 전부 찾아 Title→`STALE GRM Routine
    Handoff {날짜} (superseded by {superseded_by})`, Status→`Skipped` 로 바꾼다.
    → '항상 OPEN 1개' 불변식: 일일 emit 누적·주간 소비 오선택(6/8 근본원인) 차단.

    ⚠️ 불가침(v1, revert_refs=False 기본): handoff page **자신의** Name·Status 두
    속성만 PATCH 한다. 그 page 의 rows[] 가 가리키는 **개별 Intake row page 의
    Status 는 절대 건드리지 않는다**(handoff 의 children 은 JSON code block 일 뿐 —
    row page 가 아니다). 반환=봉인 건수.

    `revert_refs=True`(멱등성 v2, PL-10b/B1 근본해결 — K4-1 불가침의 의도적 변경):
    STALE 봉인한 handoff 의 **미발행(Status=New) row 만** `Handoff Ref` 를 비워
    다음 emit 에 재투입한다(B1 revert — 누락 0). row 의 Status 는 여기서도 불변 —
    Processed/Skipped row 는 ref 포함 일절 건드리지 않는다.
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {
        "filter": {
            "and": [
                {"property": PROP_TYPE_CLASS, "select": {"equals": TYPE_ROUTINE_HANDOFF}},
                {"property": PROP_STATUS, "select": {"equals": "New"}},
            ]
        },
        "page_size": 100,
    }
    staled = 0
    staled_ids: list[str] = []
    start_cursor: str | None = None
    for _ in range(25):  # handoff page 는 소수 — 안전 상한
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            prior_id = snap["document_id"]
            if not prior_id or prior_id == keep_handoff_id:
                continue  # 오늘 emit 본인(keep)·식별 불가 page 는 봉인 금지
            prior_date = prior_id.split("::", 1)[-1] or snap["run_date"] or "?"
            new_title = (f"STALE GRM Routine Handoff {prior_date} "
                         f"(superseded by {superseded_by})")
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=snap["page_id"]), token,
                body={"properties": {
                    PROP_NAME: {"title": _rich_text(new_title)},
                    PROP_STATUS: _select("Skipped"),
                }},
            )
            staled += 1
            staled_ids.append(prior_id)
            time.sleep(0.34)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    if staled:
        log("INFO", f"직전 미소비 OPEN handoff {staled}건 STALE 봉인 "
                    f"(keep={keep_handoff_id})")
    if revert_refs:
        for prior_id in staled_ids:
            notion_revert_refs_for_handoff(token, db_id, prior_id)
    return staled


_HANDOFF_REF_ROWS_MAX_PAGES = 10  # ref 잔존 New row 는 소수(미마감 handoff 분량) — 안전 상한


def _query_new_rows_with_ref(token: str, db_id: str,
                             ref_filter: dict[str, Any]) -> list[tuple[str, str]]:
    """Status=New ∧ ref_filter 인 Intake row 의 (page_id, handoff_ref) 목록.

    handoff page 자체(SOURCE_HANDOFF/TYPE_ROUTINE_HANDOFF)는 큐 row 가 아니므로 제외.
    handoff_ref 는 snapshot 스키마에 넣지 않고 여기서만 읽는다 — snapshot 은 v1 handoff
    payload rows 로 그대로 직렬화되므로 키 추가 = flag off 경로 바이트 변경(금지).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {
        "filter": {"and": [
            {"property": PROP_STATUS, "select": {"equals": "New"}},
            ref_filter,
        ]},
        "page_size": 100,
    }
    out: list[tuple[str, str]] = []
    start_cursor: str | None = None
    for _ in range(_HANDOFF_REF_ROWS_MAX_PAGES):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            ref = _prop_rich_text(page.get("properties", {}), PROP_HANDOFF_REF)
            out.append((snap["page_id"], ref))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        log("WARN", "Handoff Ref row 조회 페이지 상한 도달 — 일부 row 는 다음 emit 에서 처리")
    return out


def notion_revert_refs_for_handoff(token: str, db_id: str, handoff_id: str) -> int:
    """STALE handoff 의 미발행 row 재투입(B1 revert) — Ref=handoff_id ∧ Status=New
    row 의 `Handoff Ref` 를 비운다. Status 는 불변. per-row 실패는 경고 후 계속 —
    남은 ref 는 다음 emit 의 reconcile sweep 이 다시 처리한다(자기치유). 반환=비운 건수.
    """
    rows = _query_new_rows_with_ref(
        token, db_id,
        {"property": PROP_HANDOFF_REF, "rich_text": {"equals": handoff_id}})
    reverted = 0
    for page_id, _ref in rows:
        try:
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                body={"properties": {PROP_HANDOFF_REF: {"rich_text": []}}})
            reverted += 1
        except NotionHandoffError as e:
            log("WARN", f"Handoff Ref revert 실패(다음 emit 재시도) page={page_id}: "
                        f"{truncate(str(e), 120)}")
        time.sleep(0.34)
    if rows:
        log("INFO", f"STALE handoff {handoff_id} 의 미발행 row {reverted}/{len(rows)}건 "
                    f"재투입(Handoff Ref 비움)")
    return reverted


def notion_reconcile_handoff_refs(token: str, db_id: str,
                                  current_handoff_id: str) -> dict[str, int]:
    """emit 시 reconcile sweep(멱등성 v2) — `Status=New ∧ Handoff Ref 비어있지 않음`
    row 전수를 ref 가 가리키는 handoff page 상태로 마감한다. 신뢰 신호 = handoff
    page Status(발행 종료 시 Routine 의 단일 쓰기 — per-row Status 갱신보다 견고).

      - CONSUMED(Processed) → row Status→Processed (PL-10b cleanup: 발행됐으나
        per-row Status 갱신 실패/지연분 마감 — 재유입 0). ref 는 추적성 위해 유지.
        **ref=오늘(current)이라도 동일**(Codex P1: 오늘 handoff 가 이미 CONSUMED 인
        같은 날 재실행 — 잔존 New 는 발행분이므로 마감).
      - STALE(Skipped) → ref 비움 (B1 revert — 직전 revert 의 per-row 실패/크래시
        잔존분 보정; 다음 소비 쿼리에 재투입).
      - OPEN(New) ∧ ref=오늘(current) → 불변(같은 날 재-emit; 소비 쿼리 OR 절이 포함).
      - OPEN(New) ∧ ref≠오늘 → 경고만(STALE 가드가 선행 실행되므로 비정상 상태).
      - handoff page 미발견·기타 상태 → ref 비움 + 경고 (고아 ref — 재투입이 침묵
        누락보다 안전. 중복은 v16 프롬프트 PL-10b 가드가 2차 방어).

    idempotent — 같은 입력에 재적용해도 결과 동일. per-row 실패는 경고 후 계속.
    반환: {"cleaned","reverted","orphaned","kept"} 건수.
    """
    rows = _query_new_rows_with_ref(
        token, db_id,
        {"property": PROP_HANDOFF_REF, "rich_text": {"is_not_empty": True}})
    stats = {"cleaned": 0, "reverted": 0, "orphaned": 0, "kept": 0}
    handoff_status_cache: dict[str, str | None] = {}
    for page_id, ref in rows:
        if ref not in handoff_status_cache:
            handoff_page = notion_find_handoff_page(token, db_id, ref)
            handoff_status_cache[ref] = (
                _intake_page_snapshot(handoff_page)["status"] if handoff_page else None)
        handoff_status = handoff_status_cache[ref]
        try:
            if handoff_status == "Processed":
                # CONSUMED — 발행 완료 신호. row 마감(Status 지연분 cleanup).
                notion_api_request(
                    "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                    body={"properties": {PROP_STATUS: _select("Processed")}})
                stats["cleaned"] += 1
            elif handoff_status == "New":
                if ref != current_handoff_id:
                    log("WARN", f"reconcile: row {page_id} 의 ref={ref} 가 여전히 OPEN — "
                                f"STALE 가드 선행 후 비정상 상태, 이번 emit 은 보류")
                stats["kept"] += 1  # ref=오늘 ∧ OPEN = 같은 날 재-emit(정상) → 불변
            else:
                # STALE(Skipped)·미발견·기타 — 재투입(ref 비움).
                if handoff_status is None:
                    log("WARN", f"reconcile: row {page_id} 의 ref={ref} handoff page "
                                f"미발견(고아) — ref 비우고 재투입")
                    stats["orphaned"] += 1
                else:
                    stats["reverted"] += 1
                notion_api_request(
                    "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                    body={"properties": {PROP_HANDOFF_REF: {"rich_text": []}}})
        except NotionHandoffError as e:
            log("WARN", f"reconcile PATCH 실패(다음 emit 재시도) page={page_id}: "
                        f"{truncate(str(e), 120)}")
        time.sleep(0.34)
    if any(stats.values()):
        log("INFO", "Handoff Ref reconcile: "
                    f"CONSUMED 마감 {stats['cleaned']}건 · STALE 재투입 {stats['reverted']}건 · "
                    f"고아 재투입 {stats['orphaned']}건 · 유지 {stats['kept']}건")
    return stats


def notion_mark_rows_handoff_ref(token: str, rows: list[dict[str, Any]],
                                 handoff_ref: str) -> tuple[int, int]:
    """emit 표시(멱등성 v2) — handoff 에 포함된 row 에 `Handoff Ref` 를 기록한다.

    Status 는 New 유지(상태기계 §3.2). dedupe 전 전체 row 대상 — dedup 으로 payload
    에서 빠진 중복 row 도 이 handoff 가 '가져간' 것이므로 함께 표시한다(CONSUMED 시
    reconcile 이 함께 마감 — 중복 row 의 영구 New 잔존 방지). per-row 실패는 경고 후
    계속(그 row 는 ref 없음 = v1 동작 폴백 — 다음 emit 재포함, 누락 없음).
    반환=(성공, 실패) 건수.
    """
    ok = failed = 0
    for row in rows:
        page_id = row.get("page_id", "")
        if not page_id:
            failed += 1
            continue
        try:
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                body={"properties": {
                    PROP_HANDOFF_REF: {"rich_text": _rich_text(handoff_ref)},
                }})
            ok += 1
        except NotionHandoffError as e:
            failed += 1
            log("WARN", f"Handoff Ref 기록 실패(해당 row 는 v1 동작 폴백) "
                        f"page={page_id}: {truncate(str(e), 120)}")
        time.sleep(0.34)
    if failed:
        log("WARN", f"Handoff Ref 기록: 성공 {ok}건 / 실패 {failed}건 (ref={handoff_ref})")
    elif ok:
        log("INFO", f"Handoff Ref 기록 완료: {ok}건 (ref={handoff_ref})")
    return ok, failed


def notion_archive_page_children(token: str, page_id: str) -> None:
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    start_cursor: str | None = None
    archived = 0
    while True:
        req_url = url
        if start_cursor:
            req_url = f"{url}?start_cursor={urllib.parse.quote(start_cursor)}"
        data = notion_api_request("GET", req_url, token)
        for block in data.get("results", []):
            block_id = block.get("id")
            if not block_id:
                continue
            notion_api_request("PATCH", f"https://api.notion.com/v1/blocks/{block_id}",
                               token, body={"archived": True})
            archived += 1
            time.sleep(0.34)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    log("INFO", f"Routine handoff 기존 blocks archive 완료: {archived}개")


def notion_append_page_children(token: str, page_id: str,
                                blocks: list[dict[str, Any]]) -> None:
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    for i in range(0, len(blocks), 90):
        notion_api_request("PATCH", url, token, body={"children": blocks[i:i + 90]})
        time.sleep(0.34)


_NOTION_CHILDREN_CREATE_LIMIT = 90  # 요청당 100 한도 방어(단계 D, Codex P2)


def notion_upsert_routine_handoff(token: str, db_id: str,
                                  payload: dict[str, Any],
                                  generated_at: datetime,
                                  compact: bool = False) -> tuple[str, str]:
    """New-only handoff page 를 생성/갱신하고 (page_id, page_url) 반환.

    compact=True(v2) 면 payload JSON 을 compact 직렬화한다. children 이 한도(90)를
    넘으면 페이지 생성 후 append chunk 경로로 분할 전송(create 한 번에 100 초과 방지).
    """
    props = _handoff_page_properties(payload, generated_at)
    blocks = _handoff_blocks(payload, compact=compact)
    # K4-1: 새 OPEN 생성/갱신 전, 직전 미소비 OPEN handoff 를 STALE 봉인('항상 OPEN 1개').
    # 개별 Intake row Status 는 불변 — handoff page 자신의 Name·Status 만 바꾼다.
    notion_stale_prior_open_handoffs(
        token, db_id,
        keep_handoff_id=payload["handoff_id"],
        superseded_by=payload.get("run_date_kst") or payload["handoff_id"].split("::", 1)[-1],
    )
    existing = notion_find_handoff_page(token, db_id, payload["handoff_id"])
    if existing:
        # Codex P1 revive 가드(멱등성 v2 한정): 이미 CONSUMED(Processed)/STALE(Skipped)
        # 로 종결된 handoff page 를 재패치하면 Status 가 New 로 부활해 재소비(중복
        # 발행) 경로가 열린다. emit 진입 시 종결 확인 후 도달했으므로 여기 걸리면
        # 그 사이 Routine 이 소비한 경합 — 조용히 덮지 않고 실패로 표면화한다
        # (row ref 미기록 상태로 중단 → 다음 emit 이 자동 정상화). v1(flag off)은
        # 기존 재패치 동작 그대로(현행 운영 불변).
        if _enable_handoff_idempotency_v2():
            existing_status = _intake_page_snapshot(existing)["status"]
            if existing_status in ("Processed", "Skipped"):
                raise NotionHandoffError(
                    f"P1 revive 가드: handoff {payload['handoff_id']} 가 이미 "
                    f"'{existing_status}' 종결 — 재기록(부활) 금지. emit 중 Routine "
                    f"소비 경합 의심, 다음 emit 에서 자동 정상화됩니다.")
        page_id = existing["id"]
        notion_archive_page_children(token, page_id)
        notion_append_page_children(token, page_id, blocks)  # 이미 90 단위 분할
        notion_api_request("PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id),
                           token, body={"properties": props})
        page_url = existing.get("url", "")
        log("INFO", f"Routine handoff 갱신 완료: {page_url or page_id}")
        return page_id, page_url

    # 생성: children ≤90 이면 한 번에(v1 기존 동작 유지), >90 이면 첫 90 + 나머지 append.
    head = blocks[:_NOTION_CHILDREN_CREATE_LIMIT]
    tail = blocks[_NOTION_CHILDREN_CREATE_LIMIT:]
    body = {"parent": {"database_id": db_id}, "properties": props, "children": head}
    created = notion_api_request("POST", NOTION_PAGES_URL, token, body=body)
    page_id = created.get("id", "")
    page_url = created.get("url", "")
    if tail:
        notion_append_page_children(token, page_id, tail)
    log("INFO", f"Routine handoff 생성 완료: {page_url or page_id} (blocks={len(blocks)})")
    return page_id, page_url


def emit_routine_handoff(token: str, db_id: str, run_date: date,
                         window_days: int,
                         generated_at: datetime,
                         source_names: set[str] | None = None,
                         doc_ids: set[str] | None = None,
                         inmemory_raw: dict[str, dict[str, Any]] | None = None,
                         display_window_days: int | None = None
                         ) -> tuple[int, str]:
    # B1 조회/표시 분리: window_days(조회 lookback, 기본 30 — 미소비 New 누락 방지
    # 안전망)와 payload 의 window_start~window_end 는 역할이 다르다. 후자는 v16
    # 프롬프트가 발행 브리프의 "검색 기간" 속성으로 그대로 렌더하므로 발행 cadence
    # (수집 윈도우, 주간 7일)를 유지해야 한다 — 프롬프트의 "지난 7일" 문구와 정합.
    # display_window_days 미지정 시 window_days 사용(기존 호출 호환).
    payload_window_days = (display_window_days if display_window_days is not None
                           else window_days)
    # 멱등성 v2(PL-10b/B1 근본해결): 소비 쿼리 **전에** ① 직전 OPEN handoff 를 STALE
    # 봉인하며 그 미발행 row 의 ref 를 비우고(B1 revert — 이번 emit 에 즉시 재투입)
    # ② reconcile sweep 으로 CONSUMED 마감/잔존 ref 를 정리한다. 순서가 뒤면 STALE
    # 된 handoff 의 row 가 하루 늦게 재투입된다. upsert 내부의 K4-1 가드는 그대로
    # 두되(여기서 이미 봉인됐으므로 no-op), v1(flag off)은 이 블록 전체를 건너뛴다.
    idem_v2 = _enable_handoff_idempotency_v2()
    handoff_id = f"routine-handoff::{run_date.isoformat()}"
    current_handoff_open = True
    if idem_v2:
        notion_stale_prior_open_handoffs(
            token, db_id, keep_handoff_id=handoff_id,
            superseded_by=run_date.isoformat(), revert_refs=True)
        # Codex P1: 오늘 handoff 의 종결 여부를 소비 쿼리 전에 확인 — 이미
        # CONSUMED(Processed)/STALE(Skipped)면 잔존 New(ref=오늘)는 reconcile 이
        # 마감/재투입하고, page 재기록·재유입·ref 기록은 전부 생략한다(아래).
        current_page = notion_find_handoff_page(token, db_id, handoff_id)
        current_status = (_intake_page_snapshot(current_page)["status"]
                          if current_page else None)
        current_handoff_open = current_status in (None, "New")
        try:
            notion_reconcile_handoff_refs(token, db_id,
                                          current_handoff_id=handoff_id)
        except NotionHandoffError as e:
            # reconcile 은 위생 단계 — 실패해도 중복/누락이 생기지 않는다(ref 가 남은
            # row 는 소비 쿼리에서 제외된 채 다음 emit 의 sweep 이 재처리). emit 계속.
            log("WARN", f"Handoff Ref reconcile 실패(다음 emit 재시도): "
                        f"{truncate(str(e), 160)}")
        if not current_handoff_open:
            # P1: 같은 날 재실행인데 오늘 handoff 가 이미 종결 — 발행 기록(payload)
            # 보존을 위해 page 를 다시 쓰지 않는다(부활 금지). 이번 실행의 신규 row 는
            # ref 미기록(=비어있음)으로 남아 다음 emit 의 handoff 에 정상 합류한다.
            # (여기서 ref 를 기록하면 다음 reconcile 이 미발행분을 CONSUMED 마감해
            # 침묵 누락이 되므로 기록하지 않는 것이 정확하다.)
            log("INFO", f"오늘 handoff({handoff_id})가 이미 '{current_status}' 종결 — "
                        f"재기록/재유입 없이 종료(신규 row 는 다음 emit 대기)")
            return 0, (current_page or {}).get("url", "")
    rows = notion_query_new_intake_rows(token, db_id, run_date, window_days,
                                        source_names=source_names,
                                        doc_ids=doc_ids,
                                        current_handoff_id=(handoff_id if idem_v2
                                                            else None),
                                        current_handoff_open=current_handoff_open)
    if _enable_handoff_v2():
        # K2-prep: dedupe → 하이브리드 raw 부착(메모리 우선) → scaffold v2 payload.
        # inmemory_raw 는 main() 가 당일 수집 IntakeItem.raw_payload 로 전달(K3 G2 와이어링).
        # 당일분은 메모리 적중(fetch 0), 과거 누적 New row 만 page children fetch 폴백.
        enriched, _stats = enrich_rows_with_raw(token, rows, inmemory_raw=inmemory_raw)
        payload = build_routine_handoff_payload_v2(enriched, run_date,
                                                   payload_window_days, generated_at)
        _pid, page_url = notion_upsert_routine_handoff(token, db_id, payload,
                                                       generated_at, compact=True)
        log("INFO", f"Routine handoff v2 생성(ENABLE_HANDOFF_V2): rows={payload['row_count']}")
    else:
        # 기존 v1 경로 — scheduled 운영 기본. 바이트 동일 보장(변경 없음).
        payload = build_routine_handoff_payload(rows, run_date,
                                                payload_window_days, generated_at)
        _pid, page_url = notion_upsert_routine_handoff(token, db_id, payload, generated_at)
    if idem_v2:
        # emit 표시는 handoff page 확정(upsert 성공) **후** — page 없는 ref 가 생기지
        # 않게 한다. 대상은 dedupe 전 전체 rows(중복 row 도 이 handoff 가 가져감).
        notion_mark_rows_handoff_ref(token, rows, handoff_id)
    return payload["row_count"], page_url


def build_notion_properties(item: IntakeItem, run_date: date,
                            collected_at: datetime) -> dict[str, Any]:
    # Name 타이틀 — 소스별 프리픽스
    _prefix_map = {
        SOURCE_FR:      "FR",
        SOURCE_RECALL:  "Recall",
        SOURCE_EMA:     "EMA",
        SOURCE_MHRA:    "MHRA",
        SOURCE_PICS:    "PICS",
        SOURCE_ECA:     "ECA",
        SOURCE_FDA_WL:  "WL",
        SOURCE_MFDS:    "MFDS",
        # Phase 2a 신규 ("SRC" 아님 — 모호함 방지)
        SOURCE_BRAVE:   "BRV",
        SOURCE_RAPS:    "RAPS",
        SOURCE_EPR:     "EPR",
    }
    prefix = _prefix_map.get(item.source, item.source)
    if item.source in (SOURCE_RECALL, SOURCE_FDA_WL):
        name = f"{prefix} {item.document_id} — {truncate(item.firm or item.headline, 100)}"
    else:
        name = f"{prefix} {item.document_id} — {truncate(item.headline, 100)}"

    props: dict[str, Any] = {
        PROP_NAME: {"title": _rich_text(name)},
        PROP_SOURCE: _select(item.source),
        PROP_DOC_ID: {"rich_text": _rich_text(item.document_id)},
        PROP_HEADLINE: {"rich_text": _rich_text(truncate(item.headline, NOTION_RICH_TEXT_CHUNK))},
        PROP_COLLECTED_AT: _datetime_iso(collected_at),
        PROP_RUN_DATE: {"date": {"start": run_date.isoformat()}},
        PROP_QA_RELEVANCE: _select(item.qa_relevance),
        PROP_OSD_RELEVANCE: _select(item.osd_relevance),
        PROP_SOURCE_TYPE: _select(item.source_type),
        PROP_SIGNAL_TIER: _select(item.signal_tier),
        PROP_STATUS: _select("New"),
    }

    # ── 제품군(Modality) 태그 (제품군 확장) ─────────────────────────────────────
    # ENABLE_MODALITY_TAG=true 이고 Notion 에 'Modality' select 속성이 있을 때만 기록.
    # (기본 false — 속성 미생성 상태로 운영에 머지돼도 insert 가 깨지지 않도록 안전 게이트)
    if os.environ.get("ENABLE_MODALITY_TAG", "false").lower() == "true":
        modality = compute_modality(
            item.raw_payload, item.headline, item.body,
            item.type_or_class, item.firm,
        )
        props[PROP_MODALITY] = _select(modality)

    if item.date_iso:
        d = _date_iso(item.date_iso)
        if d:
            props[PROP_DATE] = d
    if item.official_url:
        u = _url(item.official_url)
        if u:
            props[PROP_OFFICIAL_URL] = u
    if item.type_or_class:
        # Select 옵션은 자동 생성됨
        props[PROP_TYPE_CLASS] = _select(item.type_or_class[:100])
    if item.firm:
        props[PROP_FIRM] = {"rich_text": _rich_text(truncate(item.firm, NOTION_RICH_TEXT_CHUNK))}
    if item.body:
        props[PROP_BODY] = {"rich_text": _rich_text(truncate(item.body, NOTION_RICH_TEXT_CHUNK))}
    if item.distribution:
        props[PROP_DISTRIBUTION] = {"rich_text": _rich_text(truncate(item.distribution, NOTION_RICH_TEXT_CHUNK))}
    if item.comments_close_iso:
        d = _date_iso(item.comments_close_iso)
        if d:
            props[PROP_COMMENTS_CLOSE] = d
    if item.api_query:
        u = _url(item.api_query)
        if u:
            props[PROP_API_QUERY] = u

    # ── Phase 2a 신규 필드 매핑 ─────────────────────────────────────────────
    if item.source_url:
        u = _url(item.source_url)
        if u:
            props[PROP_SOURCE_URL] = u
    if item.raw_excerpt:
        props[PROP_RAW_EXCERPT] = {
            "rich_text": _rich_text(truncate(item.raw_excerpt, 200))
        }
    if item.search_query:
        props[PROP_SEARCH_QUERY] = {
            "rich_text": _rich_text(truncate(item.search_query, NOTION_RICH_TEXT_CHUNK))
        }
    if item.evidence_candidate:
        props[PROP_EVIDENCE_CANDIDATE] = _select(item.evidence_candidate)
    if item.language:
        props[PROP_LANGUAGE] = _select(item.language)
    if item.region_jurisdiction:
        props[PROP_REGION_JURISDICTION] = _select(item.region_jurisdiction)
    if item.site_country:
        props[PROP_SITE_COUNTRY] = {"rich_text": _rich_text(truncate(item.site_country, NOTION_RICH_TEXT_CHUNK))}

    return props


def build_notion_children(item: IntakeItem) -> list[dict[str, Any]]:
    """페이지 본문에 raw API JSON 을 code block 으로 저장."""
    raw_json = json.dumps(item.raw_payload, ensure_ascii=False, indent=2)
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text",
                               "text": {"content": "Raw API payload"}}],
            },
        }
    ]
    for chunk in chunk_text(raw_json, NOTION_CODE_BLOCK_CHUNK):
        blocks.append({
            "object": "block",
            "type": "code",
            "code": {
                "language": "json",
                "rich_text": [{"type": "text", "text": {"content": chunk}}],
            },
        })
    return blocks


def notion_create_page(token: str, db_id: str, item: IntakeItem,
                       run_date: date, collected_at: datetime,
                       retries: int = 2) -> bool:
    """Notion 페이지 생성. 429/5xx 는 재시도, 4xx(429 제외)는 즉시 실패."""
    body = {
        "parent": {"database_id": db_id},
        "properties": build_notion_properties(item, run_date, collected_at),
        "children": build_notion_children(item),
    }
    # 재시도 불필요 상태 코드 (클라이언트 에러 — 재시도해도 동일 결과)
    _NO_RETRY_CODES = {400, 401, 403, 404, 409}

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(NOTION_PAGES_URL, json=body,
                                 headers=notion_headers(token), timeout=30)
            if resp.status_code < 400:
                return True
            if resp.status_code in _NO_RETRY_CODES:
                log("ERROR", f"Notion 페이지 생성 실패 ({resp.status_code}, 재시도 없음) "
                            f"doc={item.document_id} body={resp.text[:300]}")
                return False
            if resp.status_code == 429:
                retry_after = retry_after_seconds(resp, attempt, max_sleep=30)
                # 마지막 attempt 직전에는 sleep 후 재시도해도 의미 없으므로 생략
                if attempt < retries:
                    log("WARN", f"Notion 429 rate-limit doc={item.document_id} "
                                f"— {retry_after}s 후 재시도 ({attempt + 1}/{retries + 1})")
                    time.sleep(retry_after)
                continue
            # 500/502/503/504 등 서버 에러 — 지수 백오프 재시도
            log("WARN", f"Notion 페이지 생성 실패 ({resp.status_code}) "
                        f"doc={item.document_id} attempt={attempt + 1}/{retries + 1} "
                        f"body={resp.text[:200]}")
            if attempt < retries:
                time.sleep(2 ** attempt)
        except requests.Timeout as e:
            # Timeout: Notion이 서버 측에서 이미 row를 생성했을 수 있으므로 retry 금지.
            # retry 시 duplicate row 위험. 즉시 실패 처리 후 상위에서 insert_failed 집계.
            log("ERROR", f"Notion 페이지 생성 timeout — retry 금지 (duplicate 방지) "
                         f"doc={item.document_id} err={e}")
            return False
        except requests.RequestException as e:
            # 그 외 네트워크 오류 (ConnectionError 등): 서버 미수신 가능성 높으므로 재시도
            last_err = e
            log("WARN", f"Notion 페이지 생성 네트워크 오류 doc={item.document_id} "
                        f"attempt={attempt + 1}/{retries + 1} err={e}")
            if attempt < retries:
                time.sleep(2 ** attempt)

    log("ERROR", f"Notion 페이지 생성 최종 실패 doc={item.document_id} last_err={last_err}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────


def insert_items(token: str, db_id: str, items: Iterable[IntakeItem],
                 run_date: date, collected_at: datetime,
                 existing_ids: set[str], dry_run: bool) -> tuple[int, int, int]:
    """삽입 실행. 반환: (inserted, skipped, failed)"""
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
        ok = notion_create_page(token, db_id, item, run_date, collected_at)
        if ok:
            inserted += 1
            existing_ids.add(dedup_key)
        else:
            failed += 1
            log("WARN", f"insert 최종 실패 — 다음 항목으로 진행 doc={item.document_id}")
    return inserted, skipped, failed


_ALL_SOURCES = ["fr", "recall", "ema", "mhra", "pics", "eca", "wl"]
# ich/mfds 는 opt-in feature flag 소스라 _ALL_SOURCES(기본 all)엔 넣지 않되,
# --sources 선택지와 handoff source 매핑에는 포함한다.
_SOURCE_CHOICES = _ALL_SOURCES + ["mfds", "ich", "who", "hc", "fda483", "none"]
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
}


def _source_health_rows(stats: CollectionStats) -> list[dict[str, Any]]:
    return [
        {
            "key": "fr",
            "label": "Federal Register",
            "fetched": stats.fr_fetched,
            "inserted": stats.fr_inserted,
            "skip_dup": stats.fr_skipped_dup,
            "failed": stats.fr_insert_failed,
            "error": stats.fr_error,
            "error_msg": stats.fr_error_msg,
            "truncated": stats.fr_truncated,
        },
        {
            "key": "recall",
            "label": "OpenFDA Recall",
            "fetched": stats.recall_fetched,
            "inserted": stats.recall_inserted,
            "skip_dup": stats.recall_skipped_dup,
            "failed": stats.recall_insert_failed,
            "error": stats.recall_error,
            "error_msg": stats.recall_error_msg,
            "truncated": stats.recall_truncated,
        },
        {
            "key": "ema",
            "label": "EMA RSS",
            "fetched": stats.ema_fetched,
            "inserted": stats.ema_inserted,
            "skip_dup": stats.ema_skipped_dup,
            "failed": stats.ema_insert_failed,
            "error": stats.ema_error,
            "error_msg": stats.ema_error_msg,
        },
        {
            "key": "mhra",
            "label": "MHRA RSS",
            "fetched": stats.mhra_fetched,
            "inserted": stats.mhra_inserted,
            "skip_dup": stats.mhra_skipped_dup,
            "failed": stats.mhra_insert_failed,
            "error": stats.mhra_error,
            "error_msg": stats.mhra_error_msg,
        },
        {
            "key": "pics",
            "label": "PIC/S RSS",
            "fetched": stats.pics_fetched,
            "inserted": stats.pics_inserted,
            "skip_dup": stats.pics_skipped_dup,
            "failed": stats.pics_insert_failed,
            "error": stats.pics_error,
            "error_msg": stats.pics_error_msg,
        },
        {
            "key": "eca",
            "label": "ECA Academy RSS",
            "fetched": stats.eca_fetched,
            "inserted": stats.eca_inserted,
            "skip_dup": stats.eca_skipped_dup,
            "failed": stats.eca_insert_failed,
            "error": stats.eca_error,
            "error_msg": stats.eca_error_msg,
        },
        {
            "key": "wl",
            "label": "FDA Warning Letters",
            "fetched": stats.wl_fetched,
            "inserted": stats.wl_inserted,
            "skip_dup": stats.wl_skipped_dup,
            "failed": stats.wl_insert_failed,
            "error": stats.wl_error,
            "error_msg": stats.wl_error_msg,
        },
        {
            "key": "mfds",
            "label": "MFDS RSS",
            "fetched": stats.mfds_fetched,
            "inserted": stats.mfds_inserted,
            "skip_dup": stats.mfds_skipped_dup,
            "failed": stats.mfds_insert_failed,
            "error": stats.mfds_error,
            "error_msg": stats.mfds_error_msg,
        },
        {
            "key": "mfds_recall",
            "label": "MFDS Recall",
            "fetched": stats.mfds_recall_fetched,
            "inserted": stats.mfds_recall_inserted,
            "skip_dup": stats.mfds_recall_skipped_dup,
            "failed": stats.mfds_recall_insert_failed,
            "error": stats.mfds_recall_error,
            "error_msg": stats.mfds_recall_error_msg,
        },
        {
            "key": "mfds_admin",
            "label": "MFDS Admin",
            "fetched": stats.mfds_admin_fetched,
            "inserted": stats.mfds_admin_inserted,
            "skip_dup": stats.mfds_admin_skipped_dup,
            "failed": stats.mfds_admin_insert_failed,
            "error": stats.mfds_admin_error,
            "error_msg": stats.mfds_admin_error_msg,
        },
        {
            "key": "mfds_gmp_inspection",
            "label": "MFDS GMP Inspection",
            "fetched": stats.mfds_gmp_inspection_fetched,
            "inserted": stats.mfds_gmp_inspection_inserted,
            "skip_dup": stats.mfds_gmp_inspection_skipped_dup,
            "failed": stats.mfds_gmp_inspection_insert_failed,
            "error": stats.mfds_gmp_inspection_error,
            "error_msg": stats.mfds_gmp_inspection_error_msg,
            "parse_status": dict(stats.mfds_gmp_inspection_parse_status),
            "deficiency": dict(stats.mfds_gmp_inspection_deficiency),
            "manual_review": stats.mfds_gmp_inspection_manual_review,
            "page_warnings": list(stats.mfds_gmp_inspection_page_warnings),
        },
        {
            "key": "ich",
            "label": "ICH",
            "fetched": stats.ich_fetched,
            "inserted": stats.ich_inserted,
            "skip_dup": stats.ich_skipped_dup,
            "failed": stats.ich_insert_failed,
            "error": stats.ich_error,
            "error_msg": stats.ich_error_msg,
        },
        {
            "key": "who",
            "label": "WHO",
            "fetched": stats.who_fetched,
            "inserted": stats.who_inserted,
            "skip_dup": stats.who_skipped_dup,
            "failed": stats.who_insert_failed,
            "error": stats.who_error,
            "error_msg": stats.who_error_msg,
        },
        {
            "key": "hc",
            "label": "Health Canada",
            "fetched": stats.hc_fetched,
            "inserted": stats.hc_inserted,
            "skip_dup": stats.hc_skipped_dup,
            "failed": stats.hc_insert_failed,
            "error": stats.hc_error,
            "error_msg": stats.hc_error_msg,
        },
        {
            "key": "fda483",
            "label": "FDA 483/EIR",
            "fetched": stats.fda483_fetched,
            "inserted": stats.fda483_inserted,
            "skip_dup": stats.fda483_skipped_dup,
            "failed": stats.fda483_insert_failed,
            "error": stats.fda483_error,
            "error_msg": stats.fda483_error_msg,
        },
        {
            "key": "search",
            "label": "Brave Search",
            "fetched": stats.search_fetched,
            "inserted": stats.search_inserted,
            "skip_dup": stats.search_skipped_dup,
            "failed": stats.search_insert_failed,
            "error": stats.search_error,
            "error_msg": stats.search_error_msg,
        },
    ]


def _evaluate_health(
    *,
    stats: CollectionStats,
    active: set[str],
    enable_search: bool,
    enable_mfds: bool,
    enable_mfds_recall: bool,
    enable_mfds_admin: bool,
    enable_mfds_gmp_inspection: bool,
    enable_ich: bool,
    enable_who: bool,
    enable_hc: bool,
    enable_fda483: bool,
    enable_moleg_api: bool,
    enable_scrape: bool,
    event_name: str,
    emit_routine_handoff: bool,
    handoff_emitted: bool,
    handoff_failed: bool,
    handoff_error_msg: str,
    modality_preflight_disabled: bool = False,
    handoff_idem_preflight_disabled: bool = False,
    handoff_idem_effective: bool = False,
    aged_unconsumed_new: int = 0,
    aged_new_query_error: str = "",
    handoff_window_days: int = 0,
) -> HealthCheckResult:
    health = HealthCheckResult()

    if modality_preflight_disabled:
        health.add_warning(
            "modality-preflight-degraded",
            "Notion",
            "ENABLE_MODALITY_TAG=true 이나 'Modality' 스키마 불일치로 태그 기록 자동 비활성화",
            "Notion Intake DB 에 'Modality'(Select: Chemical/Biologic/Other) 속성을 생성하세요. "
            "수집은 정상 진행됨.",
        )

    if handoff_idem_preflight_disabled:
        health.add_warning(
            "handoff-idem-preflight-degraded",
            "GRM Handoff",
            "ENABLE_HANDOFF_IDEMPOTENCY_V2=true 이나 'Handoff Ref' 스키마 불일치로 "
            "v1(날짜 윈도우) 경로 폴백",
            "Notion Intake DB 에 'Handoff Ref'(Rich text) 속성을 생성하세요. "
            "이번 실행 handoff 는 기존 v1 멱등성으로 진행됨.",
        )

    if stats.has_insert_failures():
        health.add_failure(
            "notion-insert-failed",
            "Notion",
            f"Notion insert 최종 실패 {stats.total_insert_failures()}건",
            "해당 항목은 이번 주 다이제스트에서 누락될 수 있습니다.",
        )
    if handoff_failed:
        health.add_failure(
            "handoff-failed",
            "GRM Handoff",
            "Routine handoff 생성 실패",
            handoff_error_msg[:240],
        )

    # B1 임시 방어 ②: 윈도우 밖 미소비 New 잔존 = Routine 누락/지연 의심(침묵 누락 방지).
    # warning 이므로 exit 0 유지(§3.5) — scheduled run 은 기존 운영 경고 Issue 경로로 누적.
    # Codex P2(A안): 멱등성 v2 effective 면 노후 New 는 ref 기반 소비 쿼리(날짜 하한
    # 없음)가 자동 재투입하므로 경고 미발생(정보성 로그는 main 이 출력). reconcile
    # 고아/실패는 emit 경로의 WARN 로그로 별도 표면화된다.
    if aged_unconsumed_new > 0 and not handoff_idem_effective:
        health.add_warning(
            "aged-unconsumed-new",
            "GRM Handoff",
            f"handoff 윈도우({handoff_window_days}일) 밖 미소비 New row {aged_unconsumed_new}건",
            "주간 Routine 누락/지연 의심 — 수동 확인 후 처리(또는 윈도우 조정) 필요.",
        )
    if aged_new_query_error:
        health.add_warning(
            "aged-unconsumed-new-query-failed",
            "GRM Handoff",
            "노후 미소비 New row 카운트 조회 실패 — 이번 실행은 누락 감시 불가",
            aged_new_query_error[:240],
        )

    handoff_only_success = (
        emit_routine_handoff and handoff_emitted and
        (not active or active == {"mfds"}) and not any([
            enable_mfds,
            enable_mfds_recall,
            enable_mfds_admin,
            enable_mfds_gmp_inspection,
            enable_ich,
            enable_who,
            enable_hc,
            enable_fda483,
            enable_search,
        ])
    )
    if handoff_only_success:
        health.add_info(
            "handoff-only",
            "GRM Handoff",
            "handoff-only 실행 완료",
            "source fetch 비활성 상태를 성공으로 처리",
        )
    else:
        phase1_fr_active = "fr" in active
        phase1_recall_active = "recall" in active
        if phase1_fr_active and phase1_recall_active and stats.fr_error and stats.recall_error:
            health.add_failure(
                "phase1-all-failed",
                "Phase 1",
                "Federal Register와 OpenFDA Recall이 모두 실패",
                "핵심 공식 API 2개가 모두 실패해 workflow fail로 처리합니다.",
            )

        enabled_source_failures = [
            (enable_search and stats.search_error, "brave-search", "Brave Search", stats.search_error_msg),
            (enable_mfds and stats.mfds_error, "mfds-rss", "MFDS RSS", stats.mfds_error_msg),
            (enable_mfds_recall and stats.mfds_recall_error, "mfds-recall", "MFDS Recall", stats.mfds_recall_error_msg),
            (enable_mfds_admin and stats.mfds_admin_error, "mfds-admin", "MFDS Admin", stats.mfds_admin_error_msg),
            (
                enable_mfds_gmp_inspection and stats.mfds_gmp_inspection_error,
                "mfds-gmp-inspection",
                "MFDS GMP Inspection",
                stats.mfds_gmp_inspection_error_msg,
            ),
            (enable_ich and stats.ich_error, "ich", "ICH", stats.ich_error_msg),
            (enable_who and stats.who_error, "who", "WHO", stats.who_error_msg),
            (enable_hc and stats.hc_error, "health-canada", "Health Canada", stats.hc_error_msg),
            (enable_fda483 and stats.fda483_error, "fda483", "FDA 483/EIR", stats.fda483_error_msg),
        ]

        if not phase1_fr_active and not phase1_recall_active:
            phase2_source_states = [
                ("ema" in active, "ema", "EMA RSS", stats.ema_error, stats.ema_error_msg),
                ("mhra" in active, "mhra", "MHRA RSS", stats.mhra_error, stats.mhra_error_msg),
                ("pics" in active, "pics", "PIC/S RSS", stats.pics_error, stats.pics_error_msg),
                ("eca" in active, "eca", "ECA Academy RSS", stats.eca_error, stats.eca_error_msg),
                ("wl" in active, "wl", "FDA Warning Letters", stats.wl_error, stats.wl_error_msg),
                (enable_mfds, "mfds-rss", "MFDS RSS", stats.mfds_error, stats.mfds_error_msg),
                (enable_mfds_recall, "mfds-recall", "MFDS Recall", stats.mfds_recall_error, stats.mfds_recall_error_msg),
                (enable_mfds_admin, "mfds-admin", "MFDS Admin", stats.mfds_admin_error, stats.mfds_admin_error_msg),
                (
                    enable_mfds_gmp_inspection,
                    "mfds-gmp-inspection",
                    "MFDS GMP Inspection",
                    stats.mfds_gmp_inspection_error,
                    stats.mfds_gmp_inspection_error_msg,
                ),
                (enable_ich, "ich", "ICH", stats.ich_error, stats.ich_error_msg),
                (enable_who, "who", "WHO", stats.who_error, stats.who_error_msg),
                (enable_hc, "health-canada", "Health Canada", stats.hc_error, stats.hc_error_msg),
                (enable_fda483, "fda483", "FDA 483/EIR", stats.fda483_error, stats.fda483_error_msg),
            ]
            active_phase2_sources = [row for row in phase2_source_states if row[0]]
            if active_phase2_sources and all(row[3] for row in active_phase2_sources):
                non_transient = [
                    row for row in active_phase2_sources
                    if not _is_transient_source_error(row[1], row[4])
                ]
                if non_transient:
                    health.add_failure(
                        "all-active-sources-failed",
                        "Collector",
                        "모든 활성 소스가 실패",
                        "Phase 1 소스가 비활성인 실행에서 활성 소스가 모두 error 상태입니다.",
                    )
                else:
                    health.add_warning(
                        "all-active-sources-transient",
                        "Collector",
                        "모든 활성 소스가 일시 네트워크 오류로 실패",
                        "Phase 1 소스가 비활성인 단독 실행이므로 workflow는 warning으로 처리합니다.",
                    )

        for failed, code, source, detail in enabled_source_failures:
            if failed:
                if _is_transient_source_error(code, detail):
                    health.add_warning(
                        f"transient-source-error:{code}",
                        source,
                        f"{source} 일시 수집 오류",
                        detail[:240],
                    )
                else:
                    health.add_failure(
                        f"enabled-source-error:{code}",
                        source,
                        f"{source} 활성 상태에서 수집 오류",
                        detail[:240],
                    )

    if event_name == "schedule" and enable_moleg_api:
        health.add_warning(
            "moleg-enabled-on-schedule",
            "MFDS ogLmPp",
            "scheduled run에서 ENABLE_MOLEG_API=true 감지",
            "운영 원칙은 ENABLE_MOLEG_API=false 유지입니다. workflow_dispatch opt-in은 허용됩니다.",
        )
    if enable_scrape:
        health.add_warning(
            "scrape-enabled-unimplemented",
            "Web Scrape",
            "ENABLE_SCRAPE=true 이지만 Web Scrape 수집기는 아직 미구현",
            "현재 실행에서는 건너뜁니다.",
        )
    if stats.fr_truncated:
        health.add_warning(
            "fr-truncated",
            "Federal Register",
            "Federal Register pagination 안전 상한 도달",
            "일부 항목 누락 가능성이 있어 수동 확인이 필요합니다.",
        )
    if stats.recall_truncated:
        health.add_warning(
            "recall-truncated",
            "OpenFDA Recall",
            "OpenFDA Recall pagination 안전 상한 도달",
            "일부 항목 누락 가능성이 있어 수동 확인이 필요합니다.",
        )
    if enable_mfds_gmp_inspection and stats.mfds_gmp_inspection_manual_review:
        health.add_warning(
            "gmp-attachment-manual-review",
            "MFDS GMP Inspection",
            f"GMP 실태조사 첨부 {stats.mfds_gmp_inspection_manual_review}건 수동 확인 필요",
            f"parse_status={stats.mfds_gmp_inspection_parse_status}",
        )
    for warning in stats.mfds_gmp_inspection_page_warnings:
        health.add_warning(
            "gmp-pagination-warning",
            "MFDS GMP Inspection",
            "GMP 실태조사 페이지네이션 경고",
            warning[:240],
        )

    # WHY-1 P1: excerpt 실패/cap 표면화. 카드 자체는 graceful degrade(링크/메타 카드
    # 유지)이므로 warning-only — failure 승격 금지(§3.5, exit 0 유지). flag off 면
    # 카운터가 전부 0 이라 finding 미발생(무변경).
    if stats.whopir_excerpt_failed > 0 or stats.whopir_excerpt_capped > 0:
        degraded = []
        if stats.whopir_excerpt_failed:
            degraded.append(f"추출 실패 {stats.whopir_excerpt_failed}건")
        if stats.whopir_excerpt_capped:
            degraded.append("fetch cap 도달(이후 항목 excerpt 생략)")
        health.add_warning(
            "whopir-excerpt-degraded",
            "WHO WHOPIR",
            f"WHOPIR 결함 excerpt {' · '.join(degraded)} — 링크 카드 유지",
            f"attempted={stats.whopir_excerpt_attempted} "
            f"failed={stats.whopir_excerpt_failed} "
            f"capped={bool(stats.whopir_excerpt_capped)}",
        )
    if stats.wl_body_failed > 0:
        health.add_warning(
            "wl-body-degraded",
            "FDA WL",
            f"WL 본문 excerpt {stats.wl_body_failed}건 추출 실패 — 메타 카드 유지",
            f"attempted={stats.wl_body_attempted} failed={stats.wl_body_failed}",
        )
    # WHY-1 #3 P1: 483 excerpt 실패/cap 표면화(WHOPIR/WL 와 동형 warning-only·flag off 면
    # 카운터 0 → 미발생). 카드 자체는 graceful degrade(메타 카드 유지)라 failure 승격 금지.
    if stats.fda483_excerpt_failed > 0 or stats.fda483_excerpt_capped > 0:
        degraded = []
        if stats.fda483_excerpt_failed:
            degraded.append(f"추출 실패 {stats.fda483_excerpt_failed}건")
        if stats.fda483_excerpt_capped:
            degraded.append("fetch cap 도달(이후 항목 excerpt 생략)")
        health.add_warning(
            "fda483-excerpt-degraded",
            "FDA 483/EIR",
            f"483 결함 excerpt {' · '.join(degraded)} — 메타 카드 유지",
            f"attempted={stats.fda483_excerpt_attempted} "
            f"failed={stats.fda483_excerpt_failed} "
            f"capped={bool(stats.fda483_excerpt_capped)}",
        )
    # P1-① 완전성 표면화: JSON 전수 경로 사망 → HTML 폴백(최신 ~10행, 부분)으로 수집했음을
    # 알린다. warning-only — 수집 자체는 정상이나 이번 실행은 윈도우 전수성 미보장.
    if stats.fda483_source_degraded:
        health.add_warning(
            "fda483-source-degraded",
            "FDA 483/EIR",
            "483 전수(JSON) 경로 실패 — HTML 폴백(최신 ~10행)으로 부분 수집(완전성 미보장)",
            "OII DataTables JSON 이 응답하지 않아 HTML 표(최신 ~10행)로 폴백. 더 오래 공개된 "
            "in-window 483/EIR 가 누락됐을 수 있음 — 다음 실행 복구 확인/수동 점검 권장.",
        )

    return health.finalize()


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


def _write_health_json(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    except OSError as e:
        log("WARN", f"health JSON 쓰기 실패: {e}")


def _write_health_summary(f: Any, health: HealthCheckResult, health_path: str) -> None:
    f.write("\n## GRM Intake Health Check\n\n")
    f.write(f"- Status: `{health.status}`\n")
    f.write(f"- Exit code: `{health.exit_code}`\n")
    if health_path:
        f.write(f"- Health JSON: `{health_path}`\n")
    for title, findings in [
        ("Failures", health.failures),
        ("Warnings", health.warnings),
        ("Info", health.infos),
    ]:
        if not findings:
            continue
        f.write(f"\n### {title}\n")
        for finding in findings:
            detail = f" — {finding.detail}" if finding.detail else ""
            f.write(f"- `{finding.code}` · {finding.source}: {finding.message}{detail}\n")


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
    requested_sources = args.sources or _ALL_SOURCES
    explicit_sources = args.sources is not None
    active = set() if "none" in requested_sources else set(requested_sources)

    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    notion_db = os.environ.get("NOTION_DATABASE_ID", "").strip()
    openfda_key = os.environ.get("OPENFDA_API_KEY", "").strip() or None
    data_go_kr_key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    data_go_kr_service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    enable_search = os.environ.get("ENABLE_SEARCH", "false").lower() == "true"
    enable_mfds = os.environ.get("ENABLE_MFDS", "false").lower() == "true"
    enable_mfds_recall = os.environ.get("ENABLE_MFDS_RECALL", "false").lower() == "true"
    enable_mfds_admin = os.environ.get("ENABLE_MFDS_ADMIN", "false").lower() == "true"
    enable_mfds_gmp_inspection = os.environ.get("ENABLE_MFDS_GMP_INSPECTION", "false").lower() == "true"
    enable_ich = (os.environ.get("ENABLE_ICH", "false").lower() == "true"
                  or "ich" in active)
    enable_who = (os.environ.get("ENABLE_WHO", "false").lower() == "true"
                  or "who" in active)
    enable_hc = (os.environ.get("ENABLE_HC", "false").lower() == "true"
                 or "hc" in active)
    enable_fda483 = (os.environ.get("ENABLE_FDA_483", "false").lower() == "true"
                     or "fda483" in active)
    enable_moleg_api = os.environ.get("ENABLE_MOLEG_API", "false").lower() == "true"
    enable_scrape = os.environ.get("ENABLE_SCRAPE", "false").lower() == "true"
    event_name = os.environ.get("GRM_EVENT_NAME", "").strip()
    health_json_path = os.environ.get("GRM_HEALTH_JSON", "grm-health.json").strip()
    if enable_scrape:
        log("WARN", "ENABLE_SCRAPE=true 이지만 Web Scrape 수집기는 아직 미구현 — 건너뜀")

    if not args.dry_run:
        if not notion_token or not notion_db:
            log("ERROR", "NOTION_TOKEN / NOTION_DATABASE_ID 환경변수 필요")
            return 2

    # Modality 기록 활성 시 스키마 preflight — 속성 미생성/타입 불일치면 이번 실행은
    # Modality 기록만 끄고 수집은 계속(graceful degrade). preflight 는 read-only(GET)이므로
    # dry-run 에서도 토큰/DB 가 있으면 수행해, 활성화 전 검증 루프로 쓸 수 있게 한다.
    modality_requested = os.environ.get("ENABLE_MODALITY_TAG", "false").lower() == "true"
    modality_preflight_disabled = False
    modality_preflight_skipped = False
    if modality_requested and notion_token and notion_db:
        if not notion_verify_modality_property(notion_token, notion_db):
            modality_preflight_disabled = True
            os.environ["ENABLE_MODALITY_TAG"] = "false"
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
    handoff_idem_requested = (os.environ.get("ENABLE_HANDOFF_IDEMPOTENCY_V2", "false")
                              .lower() == "true")
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
    mfds_enforcement_window_days = max(
        args.window_days, _env_int("MFDS_ENFORCEMENT_WINDOW_DAYS", 30))
    enf_start = run_date - timedelta(days=mfds_enforcement_window_days)
    log("INFO", f"MFDS enforcement window={enf_start}~{end} "
                f"({mfds_enforcement_window_days}일, 회수·행정처분 지연공개 대응)")

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
    if "mhra" in active:
        mhra_items, mhra_err = collect_mhra_rss(start, end)
        stats.mhra_fetched = len(mhra_items)
        if mhra_err:
            stats.mhra_error = True
            stats.mhra_error_msg = mhra_err

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

    # ── WHY-1 #3: FDA 483/EIR (ENABLE_FDA_483=true 또는 --sources fda483) ─────
    fda483_items: list[IntakeItem] = []
    if enable_fda483:
        log("INFO", "=== FDA 483/EIR 수집 시작 ===")
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
        if fda483_err:
            stats.fda483_error = True
            stats.fda483_error_msg = fda483_err
            log("WARN", f"FDA 483/EIR 오류: {fda483_err}")
    else:
        log("INFO", "ENABLE_FDA_483=false — FDA 483/EIR 수집 건너뜀")

    total_fetched = (stats.fr_fetched + stats.recall_fetched + stats.ema_fetched
                     + stats.mhra_fetched + stats.pics_fetched
                     + stats.eca_fetched + stats.wl_fetched
                     + stats.mfds_fetched + stats.mfds_recall_fetched
                     + stats.mfds_admin_fetched
                     + stats.mfds_gmp_inspection_fetched
                     + stats.ich_fetched
                     + stats.who_fetched
                     + stats.hc_fetched
                     + stats.fda483_fetched)
    log("INFO", (
        f"수집 완료: FR={stats.fr_fetched} · Recall={stats.recall_fetched} · "
        f"EMA={stats.ema_fetched} · MHRA={stats.mhra_fetched} · "
        f"PICS={stats.pics_fetched} · ECA={stats.eca_fetched} · "
        f"WL={stats.wl_fetched} · MFDS={stats.mfds_fetched} · "
        f"MFDS-Recall={stats.mfds_recall_fetched} · "
        f"MFDS-Admin={stats.mfds_admin_fetched} · "
        f"MFDS-GMPInspection={stats.mfds_gmp_inspection_fetched} · "
        f"ICH={stats.ich_fetched} · WHO={stats.who_fetched} · "
        f"HC={stats.hc_fetched} · FDA483={stats.fda483_fetched} · 합계={total_fetched}건"
    ))

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

    # 4) 삽입 (반환: inserted, skipped, failed)
    fr_in, fr_sk, fr_fail = insert_items(notion_token, notion_db, fr_items,
                                         run_date, collected_at, existing, args.dry_run)
    stats.fr_inserted = fr_in
    stats.fr_skipped_dup = fr_sk
    stats.fr_insert_failed = fr_fail

    rec_in, rec_sk, rec_fail = insert_items(notion_token, notion_db, recall_items,
                                            run_date, collected_at, existing, args.dry_run)
    stats.recall_inserted = rec_in
    stats.recall_skipped_dup = rec_sk
    stats.recall_insert_failed = rec_fail

    # Phase 2 삽입
    ema_in, ema_sk, ema_fail = insert_items(notion_token, notion_db, ema_items,
                                             run_date, collected_at, existing, args.dry_run)
    stats.ema_inserted = ema_in
    stats.ema_skipped_dup = ema_sk
    stats.ema_insert_failed = ema_fail

    mhra_in, mhra_sk, mhra_fail = insert_items(notion_token, notion_db, mhra_items,
                                                run_date, collected_at, existing, args.dry_run)
    stats.mhra_inserted = mhra_in
    stats.mhra_skipped_dup = mhra_sk
    stats.mhra_insert_failed = mhra_fail

    pics_in, pics_sk, pics_fail = insert_items(notion_token, notion_db, pics_items,
                                                run_date, collected_at, existing, args.dry_run)
    stats.pics_inserted = pics_in
    stats.pics_skipped_dup = pics_sk
    stats.pics_insert_failed = pics_fail

    eca_in, eca_sk, eca_fail = insert_items(notion_token, notion_db, eca_items,
                                             run_date, collected_at, existing, args.dry_run)
    stats.eca_inserted = eca_in
    stats.eca_skipped_dup = eca_sk
    stats.eca_insert_failed = eca_fail

    wl_in, wl_sk, wl_fail = insert_items(notion_token, notion_db, wl_items,
                                          run_date, collected_at, existing, args.dry_run)
    stats.wl_inserted = wl_in
    stats.wl_skipped_dup = wl_sk
    stats.wl_insert_failed = wl_fail

    mfds_in, mfds_sk, mfds_fail = insert_items(notion_token, notion_db, mfds_items,
                                               run_date, collected_at, existing, args.dry_run)
    stats.mfds_inserted = mfds_in
    stats.mfds_skipped_dup = mfds_sk
    stats.mfds_insert_failed = mfds_fail

    mfds_rec_in, mfds_rec_sk, mfds_rec_fail = insert_items(
        notion_token, notion_db, mfds_recall_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_recall_inserted = mfds_rec_in
    stats.mfds_recall_skipped_dup = mfds_rec_sk
    stats.mfds_recall_insert_failed = mfds_rec_fail

    mfds_admin_in, mfds_admin_sk, mfds_admin_fail = insert_items(
        notion_token, notion_db, mfds_admin_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_admin_inserted = mfds_admin_in
    stats.mfds_admin_skipped_dup = mfds_admin_sk
    stats.mfds_admin_insert_failed = mfds_admin_fail

    mfds_gmp_insp_in, mfds_gmp_insp_sk, mfds_gmp_insp_fail = insert_items(
        notion_token, notion_db, mfds_gmp_inspection_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_gmp_inspection_inserted = mfds_gmp_insp_in
    stats.mfds_gmp_inspection_skipped_dup = mfds_gmp_insp_sk
    stats.mfds_gmp_inspection_insert_failed = mfds_gmp_insp_fail

    ich_in, ich_sk, ich_fail = insert_items(
        notion_token, notion_db, ich_items,
        run_date, collected_at, existing, args.dry_run)
    stats.ich_inserted = ich_in
    stats.ich_skipped_dup = ich_sk
    stats.ich_insert_failed = ich_fail

    who_in, who_sk, who_fail = insert_items(
        notion_token, notion_db, who_items,
        run_date, collected_at, existing, args.dry_run)
    stats.who_inserted = who_in
    stats.who_skipped_dup = who_sk
    stats.who_insert_failed = who_fail

    hc_in, hc_sk, hc_fail = insert_items(
        notion_token, notion_db, hc_items,
        run_date, collected_at, existing, args.dry_run)
    stats.hc_inserted = hc_in
    stats.hc_skipped_dup = hc_sk
    stats.hc_insert_failed = hc_fail

    fda483_in, fda483_sk, fda483_fail = insert_items(
        notion_token, notion_db, fda483_items,
        run_date, collected_at, existing, args.dry_run)
    stats.fda483_inserted = fda483_in
    stats.fda483_skipped_dup = fda483_sk
    stats.fda483_insert_failed = fda483_fail

    # ── Phase 2a: Brave Search (ENABLE_SEARCH=true 시 실행) ──────────────────
    # enable_search는 위 dedupe 윈도우 계산 시 이미 정의됨 (재정의 불필요)
    search_items: list[IntakeItem] = []  # G2: inmemory_raw 집계에서 항상 참조 가능하게 선초기화
    if enable_search:
        brave_api_key = os.environ.get("BRAVE_API_KEY", "")
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
    handoff_window_days = resolve_handoff_window_days(args.handoff_window_days)
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
                    wl_items, mfds_items, mfds_recall_items, mfds_admin_items,
                    mfds_gmp_inspection_items, ich_items, who_items, hc_items,
                    fda483_items, search_items)
                handoff_row_count, handoff_url = emit_routine_handoff(
                    notion_token, notion_db, run_date, handoff_window_days, collected_at,
                    source_names=handoff_sources, doc_ids=handoff_doc_ids,
                    inmemory_raw=inmemory_raw,
                    # B1 조회/표시 분리: 브리프 "검색 기간"은 수집 윈도우(주간) 유지.
                    display_window_days=args.window_days)
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
        "ENABLE_MFDS_RECALL": enable_mfds_recall,
        "ENABLE_MFDS_ADMIN": enable_mfds_admin,
        "ENABLE_MFDS_GMP_INSPECTION": enable_mfds_gmp_inspection,
        "ENABLE_ICH": enable_ich,
        "ENABLE_WHO": enable_who,
        "ENABLE_HC": enable_hc,
        "ENABLE_FDA_483": enable_fda483,
        "ENABLE_MOLEG_API": enable_moleg_api,
        "ENABLE_SCRAPE": enable_scrape,
        "ENABLE_MODALITY_TAG_REQUESTED": modality_requested,
        "ENABLE_MODALITY_TAG_EFFECTIVE": modality_effective,
        "ENABLE_MODALITY_TAG_PREFLIGHT_SKIPPED": modality_preflight_skipped,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_REQUESTED": handoff_idem_requested,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_EFFECTIVE": handoff_idem_effective,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_PREFLIGHT_SKIPPED": handoff_idem_preflight_skipped,
    }
    health = _evaluate_health(
        modality_preflight_disabled=modality_preflight_disabled,
        handoff_idem_preflight_disabled=handoff_idem_preflight_disabled,
        handoff_idem_effective=handoff_idem_effective,
        stats=stats,
        active=active,
        enable_search=enable_search,
        enable_mfds=enable_mfds,
        enable_mfds_recall=enable_mfds_recall,
        enable_mfds_admin=enable_mfds_admin,
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

    # GitHub Actions 가 읽을 수 있는 GITHUB_STEP_SUMMARY 출력
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
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
                    f.write(_src_line("FDA 483/EIR", stats.fda483_fetched, stats.fda483_inserted,
                                      stats.fda483_skipped_dup, stats.fda483_insert_failed,
                                      stats.fda483_error, stats.fda483_error_msg))
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
