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


MODALITY_OTHER = "Other"         # 기타·판별 곤란(제품군 단서 없음: 일반 가이드라인·정책 등)


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
