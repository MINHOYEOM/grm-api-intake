#!/usr/bin/env python3
"""GRM Intake — 분류 판정 순수함수 층 (배치3 Phase2, collect_intake 에서 verbatim 분리).

입력(텍스트·raw payload) → 판정 순수함수: QA relevance·modality·OSD relevance·FDA WL
부서 게이트 + 그 분류용 키워드 상수. 네트워크·Notion·IntakeItem 접근 0. collect_intake
로의 역참조 없음(단방향: collect_intake -> grm_taxonomy). 기존 참조 경로
(collect_intake.compute_modality 등)는 collect_intake 가 이 모듈을 재수출해 보존한다
(하위호환·테스트 무수정). compute_signal_tier 는 SOURCE_* 소스 레지스트리(배치4)에 의존해
collect_intake 에 잔류하고, 이 모듈의 _kw_match/_kw_any 를 재수출로 사용한다.
"""
from __future__ import annotations

import re
from typing import Any


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

# ★이 목록만으로는 못 잡는다(2026-09-21 실측). 두 가지가 겹쳐 있었다.
#
#  ① `_kw_match` 는 키워드를 `\b…\b` 로 감싸므로 **복수형이 통째로 빠진다**.
#     "animal drug" 은 걸리는데 "Animal Drug**s**" 는 안 걸린다(뒤 \b 가 s 앞에서
#     성립하지 않음). 표제는 거의 항상 복수형이라 목록 전체가 사실상 헛돈다.
#     실증: 2026-06-22 "Emergency Use for Two Animal Drugs" 가 그대로 발행됐다.
#  ② 목록은 **인접 낱말쌍**을 손으로 나열한 것이라 새 조합을 못 잡는다.
#     실증: 2026-09-21 "Veterinary **Monoclonal Antibody** Products"(GFI #298,
#     FDA CVM) — veterinary 도 animal 도 표제에 있는데 8개 중 아무것도 안 맞아
#     Unrelated 가 아니라 **Likely** 로 통과했다.
#
# 그래서 낱말쌍 목록이 아니라 **성질**로 판정한다: 이 문서의 *주제*가 수의인가.
# 업체명에 우연히 Veterinary 가 든 것(예: 483 대상 "Veterinary Pharmacy
# Corporation" — 무균조제·배지충진·엔도톡신 지적이라 GMP 로 유효)과 구분해야
# 하므로, bare `veterinary` 가 아니라 **수의 + 제품/제형 명사**를 본다.
# `compute_relevance` 는 업체명도 함께 받으므로(collect_fda_483) 이 구분이 필수다.
#
# 실측 근거(발행본 13주·카드 520장 전수 + 경계사례 13건): 경계사례 불일치 0건,
# 코퍼스에서 새로 차단되는 카드는 2장이고 둘 다 실제 수의 문서다
# (EMA CVMP 회의결과 · FDA 동물용의약품 긴급사용승인). 위 483 은 그대로 남는다.
_VET_DOMAIN_RE = re.compile(
    r"\bveterinary\s+(?:\w+\s+){0,3}products?\b"
    r"|\bveterinary\s+(?:medicinal\s+)?"
    r"(?:drugs?|medicines?|medicinal|vaccines?|monoclonal|biologics?|use)\b"
    r"|\bcommittee\s+for\s+veterinary\b"
    r"|\banimal\s+(?:drugs?|health)\b"
    r"|\btarget\s+animal\b"
    r"|\bmedicated\s+feeds?\b"
    r"|\bvich\b"                    # 동물용의약품 국제조화(ICH 의 수의 대응)
    r"|\bgfi\s*#?\s*\d+",           # FDA CVM 의 Guidance for Industry 번호 체계
    re.I)


def is_veterinary_domain(*text_parts: str) -> bool:
    """문서 주제가 수의/동물용인가(순수 함수). 업체명 단독 일치로는 참이 되지 않는다."""
    blob = " ".join(t for t in text_parts if t).lower()
    return bool(_VET_DOMAIN_RE.search(blob))


# ── 한국어 제외 도메인 (2026-09-21) ──────────────────────────────────────────
# 위 제외 어휘는 **전부 영어**다. MFDS 는 한국어 원천이라 그대로 통과한다:
#   "Medical Devices"                  → Unrelated (막힘)
#   "디지털의료기기 제조 및 품질관리 기준"  → Pending   (통과)
# 실증: 2026-09-21 호에 `디지털의료기기 제조 및 품질관리 기준 질의·응답집` 이 실렸고,
# 발행본 전수(13주)에도 의료기기 지침이 2건 더 있었다(07-06 변경관리·08-03 SW 밸리데이션).
#
# ★목록(`_kw_match`)에 한국어를 더하는 것으로는 못 고친다 — `_kw_match` 는 키워드를
#   `\b…\b` 로 감싸는데 한국어에는 그 경계가 없다. `\b의료기기\b` 는 "디지털의료기기"
#   안에서 성립하지 않아(앞 글자 '털'도 단어문자) 한 번도 안 걸린다.
#   그래서 별도 정규식 층으로 둔다(수의 판정과 같은 구조).
#
# 의약품 단서가 함께 있으면 구제한다 — 복합제·겸업 제조소·생약 잔류농약처럼 제약
# 문서가 이 낱말을 정당하게 포함하는 경우가 실제로 있다(실측: `(주)현진제약
# 잔류농약(뷰프로페진) 검출 회수` 는 남아야 한다).
#
# 실측(발행본 13주 574장 + 경계사례 12건): 경계사례 불일치 0건, 코퍼스에서 새로
# 차단되는 것은 MFDS 의료기기 지침 3건뿐이다(영어 원천인 FDA 483 은 이 층을 타지
# 않는다 — `compute_relevance` 가 받는 것이 영어 본문이라 한국어 정규식이 안 걸린다).
_KO_NON_PHARMA_RE = re.compile(
    r"의료기기|체외진단기기|의료용구|화장품|건강기능식품|식품위생|축산물|식품첨가물")
_KO_PHARMA_GUARD_RE = re.compile(
    r"의약품|원료의약품|완제의약품|복합제|의약외품|생물학적제제|한약|생약|신약|"
    r"제네릭|무균|제조소|주사제|경구|정제|캡슐|회수")


def is_korean_non_pharma_domain(*text_parts: str) -> bool:
    """한국어 표제가 기기·화장품·식품 도메인인가(의약품 단서가 함께면 False)."""
    blob = " ".join(t for t in text_parts if t)
    if not _KO_NON_PHARMA_RE.search(blob):
        return False
    return not _KO_PHARMA_GUARD_RE.search(blob)


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


MODALITY_CHEMICAL = "Chemical"   # 화학합성(케미컬)의약품 — 제형 무관


MODALITY_BIOLOGIC = "Biologic"   # 생물의약품(생물학적제제) — 제형 무관


MODALITY_OTHER = "Other"         # 제품군 축 자체가 무관 — 동물용·혈액원·인체조직 등 범위 밖


# ★판별 근거 없음 — '기타'가 아니다. 화면에 제품군 배지를 아예 달지 않는다.
# 2026-09-21 이전에는 이 자리가 MODALITY_CHEMICAL 이었다(= 합성이 캐치올 기본값).
# 빈 문자열인 이유: 카드/렌더/facet 이 이미 빈 modality 를 '배지 없음'으로 처리한다
# (card_scaffold `if modality and not normative`, web/render facet `if entry["modality"]`).
MODALITY_UNKNOWN = ""


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
    # [2026-09-21] 라이브 실측으로 추가 — 아래 어휘가 없어 바이오 제조소가 기타/합성으로
    #   새고 있었다(Baxalta 'Plasma Derivative Manufacturer' → 기타, Biomat 'Source Plasma'
    #   → 기타). 'plasma-derived' 만 있고 'plasma derivative'·'source plasma' 가 없었다.
    "plasma derivative", "source plasma", "plasma fractionation",
    "biologics", "biological drug", "antivenin", "antivenom", "toxoid",
    "allergenic extract", "conjugate vaccine", "antibody-drug conjugate",
    "혈장분획", "항체약물접합체", "바이오로직스", "생물학적 제제",
]


# ★MFDS `품목구분` → 제품군. 규제기관이 **같은 축에서** 부여한 확정 분류다
# (의약품 / 생물의약품 / 첨단바이오 / 한약(생약)제제등 / 의약외품 / 마약류).
# ATC 가 못 닿는 한약제제·의약외품을 여기서 가른다 — 발행 카드 실 품목코드 120건 실측:
# 한약 20 + 의약외품 19 = 39건(33%)이 허가정보 API 범위 밖이었다.
#
# '의약품'·'마약류' 를 Chemical 로 보내지 않는 이유: 그건 "생물이 아니다"까지만 말한다.
# 저분자라는 **양성 근거**는 ATC 가 준다. 여기서는 판정을 넘기고(아래 UNKNOWN 아님 —
# 매핑에서 빠지면 다음 규칙으로 흘러간다) ATC·경구 고형이 마저 본다.
MODALITY_MFDS_GUBUN_MAP = {
    "생물의약품": MODALITY_BIOLOGIC,
    "첨단바이오": MODALITY_BIOLOGIC,     # 세포·유전자치료제
    "한약(생약)제제등": MODALITY_OTHER,   # 합성도 바이오도 아니다
    "의약외품": MODALITY_OTHER,          # 의약품 범위 밖(치약·미백제 등)
}


# ★ATC(WHO 해부-치료-화학 분류) → 제품군. **국제 표준**이고 제형이 아니라 **물질 성격**
# 으로 묶이므로, 이번 수리가 경계한 축 혼동이 없다. MFDS 허가정보에서 품목기준코드로
# 받아 온다(grm_mfds_item_class). 실측(발행 카드 실 품목코드 120건): 조회되는 품목의
# ATC 보유 85건(70%) · 전량 판정 도달.
#
# ⚠️ 접두 매칭이다. 더 긴 접두가 먼저 이기도록 아래 조회는 길이 내림차순으로 돈다
#    (L04AB 가 L04A 보다 먼저 판정돼야 한다).
MODALITY_ATC_BIOLOGIC_PREFIXES = (
    "L01F",                      # 단클론항체·항체약물접합체
    "L03A",                      # 면역자극제(인터페론·필그라스팀·인터루킨)
    "L04AA", "L04AB", "L04AC", "L04AG",   # 면역억제 단클론항체·TNF 억제제
    "J07",                       # 백신
    "J06B",                      # 면역글로불린
    "B02BD",                     # 혈액응고인자
    "B06AC",                     # C1 억제제
    "A10A",                      # 인슐린
    "H01A", "H01B", "H01C",      # 뇌하수체·시상하부 호르몬(성장호르몬·고나도트로핀)
    "M05BX04",                   # 데노수맙
    "S01LA",                     # 안과용 혈관신생억제 항체
)

# 제품군 축이 무관한 ATC — 조영제·방사성의약품. 치료 물질이 아니다.
MODALITY_ATC_OTHER_PREFIXES = ("V08", "V09")


# 한국어 주성분명의 생물 어간 — ATC 가 없을 때의 보조 신호.
# ★'알파'·'베타'·'페그' 같은 수식어는 넣지 않는다: '알파칼시돌'(비타민D 유도체)처럼
#   저분자에도 붙어, 축이 다른 말을 제품군 신호로 쓰게 된다.
MODALITY_KO_INGREDIENT_BIOLOGIC = (
    "맙", "셉트", "인슐린", "인터페론", "백신", "톡소이드", "면역글로불린",
    "에포에틴", "필그라스팀", "소마트로핀", "보툴리눔", "혈장분획", "응고인자",
)


# ★'biologic' 의 거짓 친구 — 무균 제조 설비·시험 용어. 제품군과 아무 상관이 없다.
# MODALITY_BIOLOGIC_TERMS 는 부분문자열 매칭(_phrase_any)이라 'biologic' 이 'biological
# indicator'(멸균 확인용 생물학적 지표)·'biological safety cabinet'(생물안전작업대)에
# 그대로 걸린다. 둘 다 **무균 제조소 483·경고서한에 상시 등장하는 단어**라, 판정 텍스트가
# 넓어지는 순간 무균 기록이 통째로 바이오의약품이 된다(2026-09-21 실측으로 확인).
# 판정 전에 haystack 에서 제거한다 — 어휘를 빼는 게 아니라 **문맥을 빼는** 것이다.
MODALITY_BIOLOGIC_FALSE_FRIENDS = [
    "biological indicator", "biological indicators", "bi challenge",
    "biological safety cabinet", "biosafety cabinet",
    "biological monitoring", "biological evaluation",
    "biological oxygen demand",
    "생물학적 지표", "생물학적지표", "생물안전작업대", "생물학적 안전",
]


# ★제품군 축 자체가 무관한 시설/업종 — 인체 의약품(완제·원료) 범위 밖.
# 혈액원·인체조직(HCT/P)·생식세포는 '생물' 이지만 의약품이 아니므로 Biologic 이 아니라
# Other 다. 이 판정은 MODALITY_BIOLOGIC_TERMS 보다 먼저 와야 한다('blood'·'tissue' 가
# 생물 어휘에 걸려 바이오의약품으로 승격되는 것을 막는다).
MODALITY_OUT_OF_SCOPE_FACILITY_TERMS = [
    "blood bank", "blood center", "blood establishment", "blood donor",
    "tissue bank", "tissue testing", "human tissue", "hct/p",
    "reproductive human tissue", "reproductive firm", "sperm bank",
    "혈액원", "조직은행",
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
# ⚠️ 이 목록은 "의약품이다"만 말한다. "합성이다"는 말하지 않는다 — 제품군 판정 근거로
#    쓰면 안 된다(주사제·바이알·무균은 바이오의약품에도 그대로 해당). 2026-09-21 이전
#    구현은 이 목록을 Chemical 확정 근거로 썼고, 그 결과 무균 제조소 483 과 openFDA
#    회수가 통째로 '합성의약품' 으로 나갔다. 하위호환을 위해 이름은 보존한다.
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


# ★화학합성(저분자) 양성 근거 — 경구 고형제/경구 투여.
# 근거: 단백질·항체·세포/유전자 치료제는 위장관에서 분해돼 경구 고형제로 만들 수 없다.
#       경구 투여 생물의약품(경구 로타바이러스 백신 등)은 극소수이고, 그것들은 판정
#       순서상 앞선 MODALITY_BIOLOGIC_TERMS('vaccine'·'백신')에서 먼저 잡힌다.
# ⚠️ 주사·무균·바이알·수액 등 '제형이 있다'만 말하는 어휘는 여기 넣지 않는다.
MODALITY_ORAL_SOLID_TERMS = [
    "tablet", "capsule", "oral solid", "solid dosage", "caplet", "softgel",
    "oral solution", "oral suspension", "oral liquid", "syrup", "chewable",
    "정제", "캡슐", "경구", "시럽", "내용액제", "츄어블", "구강붕해",
]


# ★화학합성 명시 표기 — 텍스트가 스스로 합성이라고 말하는 경우만.
MODALITY_CHEMICAL_EXPLICIT_TERMS = [
    "small molecule", "small-molecule", "synthetic api", "chemically synthesized",
    "화학합성", "합성의약품", "저분자",
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


# ★제품명 제형 단서 중 '경구 고형/경구' 만 추린 것 — 화학합성 양성 근거로 쓸 수 있다.
#   (주사제·수액·점안·연고 등은 제품군을 가르지 못하므로 제외)
MODALITY_KOREAN_ORAL_FORM_TERMS = [
    "캡슐", "시럽", "과립", "산제", "내용액", "환제", "트로키", "츄어블",
]


# 제품명 끝의 '정'(정제)/'주'(주사제) 접미사. 뒤에 한글이 오면(안정성·행정 등) 제외.
_KOREAN_FORM_SUFFIX_RE = re.compile(r"[가-힣A-Za-z0-9][정주](?![가-힣])")


# ★제품명 끝의 '정'(정제) 접미사만 — 경구 고형이라 화학합성 양성 근거가 된다.
#   '주'(주사제)는 제품군을 가르지 못하므로 뺀다(구 _KOREAN_FORM_SUFFIX_RE 는 둘 다 봤다).
_KOREAN_ORAL_FORM_SUFFIX_RE = re.compile(r"[가-힣A-Za-z0-9]정(?![가-힣])")


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
    # 수의/동물용 등 hard exclude 는 boost 구제 없이 무조건 Unrelated.
    # 낱말쌍 목록(복수형·새 조합에 약함)과 성질 판정을 함께 본다 — 목록은 기존
    # 동작 보존용이고, 실제로 잡는 일은 `is_veterinary_domain` 이 한다.
    if _kw_any(blob, QA_HARD_EXCLUDE_TERMS) or is_veterinary_domain(blob):
        return "Unrelated"
    # 한국어 원천(MFDS)은 위 영어 목록에 안 걸린다 — 같은 성격의 제외를 한국어로도
    # 한 번 본다. 이 함수는 자체 의약품 구제를 이미 거친 결과라 device_guarded 를
    # 다시 태우지 않는다(영어 가드 어휘는 한국어 표제에 어차피 없다).
    if _kw_any(blob, QA_EXCLUDE_KEYWORDS) or is_korean_non_pharma_domain(blob):
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


def _modality_from_mfds_gubun(raw_payload: dict[str, Any]) -> str:
    """MFDS `품목구분` 으로 제품군을 판정한다(매핑에 없거나 값이 없으면 "").

    ★`mfds_gubun_lookup` 이 ok 가 아니면 값을 쓰지 않는다 — 화면 파싱이 깨졌거나
      조회를 못 한 상태를 판정으로 바꾸지 않는다.
    """
    if raw_payload.get("mfds_gubun_lookup") != "ok":
        return MODALITY_UNKNOWN
    gubun = str(raw_payload.get("mfds_item_gubun") or "").strip()
    return MODALITY_MFDS_GUBUN_MAP.get(gubun, MODALITY_UNKNOWN)


def _modality_from_mfds_atc(raw_payload: dict[str, Any]) -> str:
    """MFDS 허가정보에서 받아 온 ATC·주성분으로 제품군을 판정한다(없으면 "").

    ★수집기가 실어 준 **근거만** 본다. 조회 자체가 안 됐거나(`not_found`·`error`)
      ATC 가 없으면 판정하지 않는다 — 특히 `not_found` 를 '의약외품' 으로 읽지 않는다
      (허가정보 API 는 의약품만 담지만, 누락·지연도 같은 0건을 낸다).
    """
    atc = str(raw_payload.get("mfds_atc_code") or "").strip().upper()
    if atc:
        # 더 긴 접두가 먼저 이긴다(L04AB > L04A).
        for pref in sorted(MODALITY_ATC_OTHER_PREFIXES, key=len, reverse=True):
            if atc.startswith(pref):
                return MODALITY_OTHER
        for pref in sorted(MODALITY_ATC_BIOLOGIC_PREFIXES, key=len, reverse=True):
            if atc.startswith(pref):
                return MODALITY_BIOLOGIC
        # ATC 가 있는데 생물·범위밖 묶음이 아니다 = WHO 가 분류한 저분자 계열.
        return MODALITY_CHEMICAL
    ingr = str(raw_payload.get("mfds_main_ingredient") or "")
    if ingr and any(k in ingr for k in MODALITY_KO_INGREDIENT_BIOLOGIC):
        return MODALITY_BIOLOGIC
    return MODALITY_UNKNOWN


def _modality_application_numbers(raw_payload: dict[str, Any]) -> list[str]:
    """openFDA `openfda.application_number` 를 대문자 문자열 리스트로 꺼낸다.

    FDA 허가 트랙은 제품군을 직접 말하는 '구조화된 양성 근거'다.
        BLA#######  → Biologics License Application  = 생물의약품
        NDA/ANDA### → (Abbreviated) New Drug Application = 화학합성 트랙
    2020 년 '생물학적제제 이관'(insulin·성장호르몬 등)이 끝나 현재 BLA/NDA 경계는
    제품군 경계와 사실상 일치한다. 실측: openFDA 회수 211건 중 155건(73%)이 보유.
    """
    openfda = raw_payload.get("openfda") or {}
    value = openfda.get("application_number") or raw_payload.get("application_number")
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(v).strip().upper() for v in value if v]


def compute_modality(raw_payload: dict[str, Any], *text_parts: str) -> str:
    """수집 항목의 제품군(Modality)을 '원료 성격' 축으로 분류한다.

    ★핵심 규칙 — **양성 근거가 있을 때만 판정한다.**
      "생물이라는 증거가 없다" 는 "합성이다" 가 아니다. 근거가 없으면 판정하지 않고
      MODALITY_UNKNOWN("") 을 돌려 화면에 배지를 달지 않는다.

    ★축을 섞지 않는다.
      `무균/멸균`·`주사제`·`바이알`·`제형이 있다` 는 **멸균성·제형** 축이다. 제품군
      축과 직교하므로 제품군 판정 근거가 될 수 없다. 무균 충전 제조소에는 바이오
      fill-finish 가 다수이고, 주사제에는 항체·백신이 그대로 들어간다.

    반환값:
        "Biologic" — 생물의약품(생물학적제제): 재조합 단백질·항체·백신·세포/유전자
                     치료제·바이오시밀러·혈장분획제제 등 (제형 무관)
        "Chemical" — 화학합성(저분자)의약품. **경구 고형/경구 투여 또는 NDA·ANDA
                     허가 트랙 또는 명시적 합성 표기** 가 있을 때만.
        "Other"    — 제품군 축이 무관: 동물용의약품·혈액원·인체조직(HCT/P) 등 범위 밖
        ""         — 판별 근거 없음(MODALITY_UNKNOWN). 배지 미표시.

    [2026-09-21 재설계] 이전 구현은 2순위에서 `product_type` 에 'drug' 가 있거나
    제형/투여경로 필드가 존재하기만 하면 Chemical 을 확정했다. 그 결과 FDA 483 의
    `establishment_type`("Sterile Drug Manufacturer" 등)과 openFDA 회수의
    `product_type="Drugs"` 가 통째로 합성의약품으로 나갔다 — 발행본 실측에서 483 은
    38%(52/138), 회수는 93%(93/100)가 제품군 정보가 0인 문자열로 합성 확정이었다.
    """
    openfda = raw_payload.get("openfda") or {}
    # product_type 은 openfda.product_type 우선, 없으면 top-level product_type 폴백
    # (HC 등 openfda 구조가 없는 소스 대응). FDA 483 은 establishment_type 을 여기 싣는다.
    product_type = _as_lower_set(openfda.get("product_type") or raw_payload.get("product_type"))
    forms = _as_lower_set(openfda.get("dosage_form") or raw_payload.get("dosage_form"))
    routes = _as_lower_set(openfda.get("route") or raw_payload.get("route"))
    product = (raw_payload.get("product_description") or "").lower()
    blob = " ".join(t for t in text_parts if t).lower()
    haystack = " ".join(
        [blob, " ".join(forms), " ".join(routes), " ".join(product_type), product]
    )
    product_type_blob = " ".join(product_type)

    # ── 0. 범위 밖 하드 제외 → Other ──────────────────────────────────────────
    # 수의/동물용은 인체 의약품 범위 밖.
    #  (a) 구조화 product_type 기준  (b) 명시적 텍스트 구(phrase) 기준 — 둘 다 early-return.
    if any(("veterin" in pt or "animal" in pt) for pt in product_type):
        return MODALITY_OTHER
    if _phrase_any(haystack, MODALITY_VET_EXCLUDE_TERMS):
        return MODALITY_OTHER
    # 혈액원·인체조직(HCT/P)·생식세포 — '생물'이지만 의약품이 아니다.
    # ⚠️ 구조화 시설/제품 유형 필드에만 적용한다. haystack 전체에 걸면 의약품 483 본문의
    #    'blood donor screening' 같은 스쳐가는 언급이 제조소 전체를 범위 밖으로 만든다.
    if _phrase_any(product_type_blob, MODALITY_OUT_OF_SCOPE_FACILITY_TERMS):
        return MODALITY_OTHER

    # ── 1. MFDS 품목구분 — 규제기관이 같은 축에서 부여한 확정 분류. 가장 앞선다.
    _gubun_verdict = _modality_from_mfds_gubun(raw_payload)
    if _gubun_verdict:
        return _gubun_verdict

    # ── 2. MFDS 허가정보(ATC) — 규제기관 분류라 텍스트 추정보다 앞선다 ──────────
    #   국내 회수·행정처분은 품목기준코드로 ATC 를 받아 둔다(grm_mfds_item_class).
    #   제형·업체명 같은 약한 신호가 끼어들기 전에 여기서 결론이 난다.
    _atc_verdict = _modality_from_mfds_atc(raw_payload)
    if _atc_verdict:
        return _atc_verdict

    # ── 3. 생물의약품 양성 근거 ───────────────────────────────────────────────
    if any("biolog" in pt for pt in product_type):
        return MODALITY_BIOLOGIC
    # FDA 허가 트랙 BLA = 생물의약품(구조화 근거).
    app_numbers = _modality_application_numbers(raw_payload)
    if any(a.startswith("BLA") for a in app_numbers):
        return MODALITY_BIOLOGIC
    # ★거짓 친구를 먼저 도려낸 사본으로 본다 — 'biological indicator' 의 'biologic' 이
    #   무균 기록을 바이오의약품으로 만들지 않도록.
    haystack_bio = haystack
    for _ff in MODALITY_BIOLOGIC_FALSE_FRIENDS:
        haystack_bio = haystack_bio.replace(_ff, " ")
    if _phrase_any(haystack_bio, MODALITY_BIOLOGIC_TERMS):
        return MODALITY_BIOLOGIC
    # GAP-2: 브랜드명만 있는 생물의약품 — 제형 접미사·product_type 'drug'에 가려지기 전에
    #        가로챈다. 제품명 필드 + haystack 양쪽에서 브랜드 어간을 찾는다
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
    if re.search(r"\b[a-z]{3,}mab\b", haystack_bio):
        return MODALITY_BIOLOGIC

    # ── 4. 화학합성 양성 근거 ─────────────────────────────────────────────────
    # ★여기 들어오는 근거는 전부 "합성이다"를 적극적으로 말해야 한다.
    #   "의약품이다"만 말하는 근거(product_type 의 'drug', 제형·투여경로 필드의 존재,
    #   주사·무균·바이알)는 근거가 아니다 — 이전 구현의 결함이 정확히 그것이었다.
    #  (a) FDA 허가 트랙 NDA/ANDA = 화학합성 트랙(구조화 근거).
    if any(a.startswith("NDA") or a.startswith("ANDA") for a in app_numbers):
        return MODALITY_CHEMICAL
    #  (b) 경구 고형/경구 투여 — 단백질·항체·세포치료제는 경구로 만들 수 없다.
    if "oral" in routes:
        return MODALITY_CHEMICAL
    if any(term in f for f in forms for term in MODALITY_ORAL_SOLID_TERMS):
        return MODALITY_CHEMICAL
    #  (c) 텍스트 경구 고형 단서 — '정제수'(purified water)는 '정제'(tablet) 오탐이라 제거
    haystack_oral = haystack.replace("정제수", "")
    if _phrase_any(haystack_oral, MODALITY_ORAL_SOLID_TERMS):
        return MODALITY_CHEMICAL
    #  (d) 텍스트가 스스로 합성이라고 말하는 경우
    if _phrase_any(haystack, MODALITY_CHEMICAL_EXPLICIT_TERMS):
        return MODALITY_CHEMICAL
    #  (e) MFDS 한국어 제품명 경구 제형 단서 — 제품명 필드에만 적용(개정/규정 등 오탐 방지).
    #      한국 의약품은 XX정(정제)/XX캡슐 처럼 본문에 '정제'라는 단어 없이 제품명
    #      접미사로만 제형이 드러나는 경우가 많다(라이브 검증에서 ~40% 누락 확인).
    #      ⚠️ 'XX주'(주사제)는 제품군을 가르지 못하므로 뺐다(구현 전에는 포함돼 있었다).
    product_name = ""
    for k in MODALITY_PRODUCT_NAME_KEYS:
        v = raw_payload.get(k)
        if v:
            product_name = str(v)
            break
    if product_name:
        pn = product_name.replace("정제수", "")
        if (_phrase_any(pn, MODALITY_KOREAN_ORAL_FORM_TERMS)
                or _KOREAN_ORAL_FORM_SUFFIX_RE.search(pn)):
            return MODALITY_CHEMICAL

    # ── 5. 판별 근거 없음 → 배지 미표시 ───────────────────────────────────────
    # ★MODALITY_OTHER 로 보내지 않는다. 'Other' 는 "제품군 축이 무관"(가이드라인·동물용)
    #   이라는 적극적 의미이고, 여기는 "우리가 모른다" 이다. 둘을 같은 값에 담으면
    #   '기타' 집계가 두 모집단을 섞어 무의미해진다.
    return MODALITY_UNKNOWN
