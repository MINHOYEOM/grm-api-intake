"""GRM Keystone K2 — 결정론적 카드 골격 조립기 (card_spec v16 구현).

`build_card_scaffold(row, raw, cfg)` 는 **순수 함수**다(외부 fetch·현재시각·LLM·Notion
API 호출 없음, card_spec §12(G)). Python 이 카드 뼈대(제목·W1 배지·W2 표·W3 인용·W8
듀얼링크·출력 매트릭스)를 완성하고, LLM 이 채울 산문 6슬롯만 토큰으로 비워둔다:
  {{TITLE_ISSUE}} · {{W1}} · {{W4}}(비KO만) · {{W5}} · {{W6}} · {{W7}}

페이지 수준 조립(목차·섹션 H2·그룹핑/정렬·면책 푸터)은 `assemble_brief_skeleton()` 으로
분리한다(단계 D/K3 재사용 단위가 다름).

── 소스(kind) 레지스트리 (§1b·§10b) ──────────────────────────────────────────
유형별 산출 규칙은 `_REGISTRY: dict[kind, SourceSpec]`(§10b) 한 곳에 선언한다. producer
(`_kind_meta`·`_quote_source`·`_deterministic_detail`·`_dual_links`·`resolve_section`·
`_w2_rows`·`_category`·evidence/normative/deep 판정)는 모두 `_spec(kind)` 조회로 디스패치한다.

**신규 소스 추가 절차**(대개 이게 전부):
  1. `resolve_kind()`(§5)에 source/type_or_class → 새 내부 kind 분기 1줄 추가.
  2. `_REGISTRY`(§10b)에 `"<kind>": SourceSpec(prefix, label, core_tag, ...)` 1레코드 추가.
     - 값으로 접히는 것(category·a_eligible·normative·section·deep_body_key)은 kwargs 로.
     - 데이터로 안 접히는 분기는 per-kind callable 정의 후 참조(quote/extra_rows/official/
       detail). 공통 기본(quote=""·official=official_url·extra_rows=발행기관행·detail=None)이
       맞으면 생략.
  3. 골든 3종(`<name>.input.json` + `.expected.md` + `.expected.webcard.json`) 생성.
잔여 수정처(레지스트리 밖): (a) 새 SOURCE 상수는 §0 + `_REGULATOR_LABEL`(source-keyed,
kind 레지스트리와 직교)에도 추가, (b) 새 deep-analysis body 키는 수집기 게이트가 채워야 함.

이것은 **발행측** 레지스트리다. 대칭인 **수집측** 레지스트리는 `grm_common.INTAKE_SOURCE_SPECS`
(배치6 Phase2)로, 소스당 1 레코드가 collect_intake insert 루프 + grm_health health rows 를
구동한다 — 새 수집 소스 추가 절차는 그 docstring 참조.

마크다운 문법은 **Notion MCP enhanced markdown**(v15.8 카드 표준)만 사용한다:
  <callout icon=".." color="..">, > (원문 인용 전용), <details>(toggle), <table>,
  <table_of_contents/>, ### H3, ---. LV-15.7a 폴백 금지 문법([!WARNING]·[!NOTE]·[TOC]·
  +++·<toggle>)은 절대 쓰지 않는다(golden 에서 부재 assert).

우선순위(지시문): card_spec §12 > §13.1 > §0~§9 > redesign.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# 0. 소스/유형 상수 (collect_intake 와 동일 문자열 — import 의존 없이 평면 복제)
# ─────────────────────────────────────────────────────────────────────────────
SOURCE_FR = "Federal Register"
SOURCE_RECALL = "OpenFDA Recall"
SOURCE_EMA = "EMA"
SOURCE_MHRA = "MHRA Inspectorate"
SOURCE_PICS = "PIC/S"
SOURCE_ECA = "ECA Academy"
SOURCE_FDA_WL = "FDA Warning Letter"
SOURCE_MFDS = "MFDS"
SOURCE_ICH = "ICH"
SOURCE_WHO = "WHO"
SOURCE_HC = "Health Canada"
SOURCE_FDA_483 = "FDA 483"   # WHY-1 #3 — FDA 483/EIR 실사 관찰사항
SOURCE_ISPE = "ISPE"   # [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak 블로그
SOURCE_EU_GMP_NCR = "EU GMP NCR (EudraGMDP)"   # EU/EEA 업체별 GMP 비준수 보고서(EudraGMDP)
SOURCE_MHRA_GMP_NCR = "MHRA GMP NCR"   # 영국 MHRA 업체별 GMP 비준수 성명서(GMDP 등록부)

# MFDS 하위 유형(type_or_class)
TYPE_ADMIN_ACTION = "admin-action"
TYPE_RECALL_QUALITY = "recall-quality"
TYPE_GMP_INSPECTION = "gmp-inspection"
TYPE_GMP_CERTIFICATE = "gmp-certificate"

# LLM 산문 슬롯 토큰 (이 토큰만 비운다)
SLOT_TITLE_ISSUE = "{{TITLE_ISSUE}}"
SLOT_W1 = "{{W1}}"
SLOT_W4 = "{{W4}}"
SLOT_W5 = "{{W5}}"
SLOT_W6 = "{{W6}}"
SLOT_W7 = "{{W7}}"

# LV-15.7a 폴백 금지 문법 — golden 에서 부재 assert (사용자 제약 1)
FORBIDDEN_MARKDOWN = (
    "[!NOTE]", "[!WARNING]", "[!IMPORTANT]", "[!TIP]", "[!CAUTION]",
    "[TOC]", "+++", "<toggle>", "<toggle ", "</toggle>",
)


def assert_no_forbidden_markdown(markdown: str) -> list[str]:
    """scaffold 마크다운에 LV-15.7a 폴백 금지 문법이 있으면 발견 목록 반환(없으면 [])."""
    return [tok for tok in FORBIDDEN_MARKDOWN if tok in markdown]


# 금지 토큰 → Notion-safe 치환 맵 (원문 의미 가독 유지, 렌더 무해)
_FORBIDDEN_REPLACEMENTS = (
    ("[!NOTE]",      "[ NOTE ]"),
    ("[!WARNING]",   "[ WARNING ]"),
    ("[!IMPORTANT]", "[ IMPORTANT ]"),
    ("[!TIP]",       "[ TIP ]"),
    ("[!CAUTION]",   "[ CAUTION ]"),
    ("[TOC]",        "[ TOC ]"),
    ("+++",          "＋＋＋"),
    ("</toggle>",    "〈/toggle〉"),   # </toggle> 먼저(prefix 매칭 방지)
    ("<toggle ",     "〈toggle "),
    ("<toggle>",     "〈toggle〉"),
)


def _neutralize_forbidden(text: str) -> str:
    """금지 마크다운 토큰을 Notion-safe 형태로 결정론적 치환. 금지 토큰 없으면 no-op."""
    for old, new in _FORBIDDEN_REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# 1. FixedConfig — 결정론 상수 (현재시각·env 없음, frozen)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FixedConfig:
    # 제품군 배지 한글 (§13.1 D5)
    modality_badge: dict[str, str] = field(default_factory=lambda: {
        "Chemical": "💊 합성의약품",
        "Biologic": "🧬 바이오의약품",
        "Other": "▫️ 기타",
    })
    # callout 색 (§13.1-7): W1 파랑·W6 노랑·W7 초록·W8 회색. 사실/원문은 무채색(default).
    color_w1: str = "blue_bg"
    color_w6: str = "yellow_bg"
    color_w7: str = "green_bg"
    color_footer: str = "gray_bg"
    # 면책 D2 확정문구 (§13.1-11, 페이지 끝)
    disclaimer_ko: tuple[str, ...] = (
        "본 자료는 1차 자료(규제기관 공식 발표) 기반 AI 자동 작성 규제 정보 요약 자료입니다. "
        "사실 항목은 출처·원본을 병기해 추적 가능합니다.",
        "시사점·점검 사항은 AI 해석으로 공식 견해나 법적 자문이 아니며, 의사결정 전 반드시 원문을 확인하십시오.",
    )
    disclaimer_en: str = (
        "AI-generated regulatory summary based on primary sources. "
        "Implications and checklists are AI interpretation, not official or legal advice — verify originals."
    )
    # 섹션 헤더 (§7)
    section_titles: dict[str, str] = field(default_factory=lambda: {
        "global": "🌐 글로벌",
        "domestic": "🇰🇷 국내 (식약처)",
        "watch": "🔮 Watch",
        "recall_table": "📋 Recall 모니터링",
    })
    # 글로벌 제품군 그룹핑 임계 (§7)
    grouping_threshold: int = 4


DEFAULT_CONFIG = FixedConfig()


# ─────────────────────────────────────────────────────────────────────────────
# 1b. SourceSpec 레지스트리 — kind 당 1 선언 레코드 (소스 분기 산재 단일화)
#     레지스트리 리터럴은 §10b(모든 per-kind callable 정의 뒤)에서 조립한다.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SourceSpec:
    """한 내부 kind 의 카드 산출 규칙 1레코드(선언형). `_REGISTRY[kind]` 로 조회.

    데이터로 접히는 것은 값 필드로, 데이터로 안 접히는 분기(인용 추출·유형별 사실 행·
    듀얼링크 규칙·상세 슬롯)는 **per-kind callable 참조**로 담는다. callable 미지정(None)은
    dispatcher 의 공통 기본으로 폴백(quote=""·detail=None·official=official_url·
    extra_rows=발행기관 기본행) — 대다수 kind 는 값 필드만으로 충분하다.

    값 필드
      prefix/label/core_tag — W1 배지·제목 라벨·유형 핵심태그(구 `_kind_meta` 3-튜플).
      category              — Notion 발행 카테고리(구 `_CATEGORY_MAP`, 기본 "Other").
      a_eligible            — Evidence A 자격(구 `_A_ELIGIBLE_KINDS`). quote 존재와 커플링.
      normative             — 규범 문서 → 제품군 배지 억제(구 `_NORMATIVE_KINDS`).
      section               — 섹션 override. "" 면 source 기본(MFDS→domestic·else global).
                              str 또는 callable(row)->str(ich=consultation 판정용).
      deep_body_key         — deep_analysis fan-out 활성 raw 키("" 면 비대상).
      date_label            — W2 첫 행(`row["date"]`)의 라벨. 기본 "발행일". 원천이 게시일이
                              아닌 소스만 바꾼다(who-inspection=실사일 — WHO 목록은 게시일을
                              싣지 않고 실사일만 준다. 같은 값을 "발행일"로 부르면 오보다).
    callable 필드(모두 순수·결정론)
      quote(raw)->str                 — W3 인용 소스(§12C). None → "".
      extra_rows(row,raw)->list[(l,v)] — W2 유형별 사실 행(발행일·문서번호 이후). None → 기본행.
      official(row,raw)->(url,is_fb)   — W8 공식원본과 fallback 여부. None → (official_url, False).
      detail(row,raw)->dict|None       — 결정론 상세 슬롯(§16). None → None.
    """
    prefix: str
    label: str
    core_tag: str
    category: str = "Other"
    a_eligible: bool = False
    normative: bool = False
    section: str | Callable[[dict[str, Any]], str] = ""
    deep_body_key: str = ""
    date_label: str = "발행일"
    quote: Callable[[dict[str, Any]], str] | None = None
    extra_rows: Callable[[dict[str, Any], dict[str, Any]], list[tuple[str, str]]] | None = None
    official: Callable[[dict[str, Any], dict[str, Any]], tuple[str, bool]] | None = None
    detail: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None] | None = None


# 미등록(발현 불가) kind 안전망 — 무채색·비A·비규범·source 기본 섹션·콜러블 없음.
_DEFAULT_SPEC = SourceSpec(prefix="⬜", label="기타", core_tag="")


def _spec(kind: str) -> SourceSpec:
    """kind 의 SourceSpec 조회(`resolve_kind` 산출 kind 는 항상 등록). 미등록 → 기본 스펙."""
    return _REGISTRY.get(kind, _DEFAULT_SPEC)


# 유형 → (prefix, 한글 라벨, W1 유형 핵심 태그) — 구 §2 고정표(→ SourceSpec 로 흡수).
# 미등록 kind 만 동적 기본(라벨=kind 문자열)을 쓰므로 `_spec`(고정 라벨)과 분리 유지.
def _kind_meta(kind: str) -> tuple[str, str, str]:
    spec = _REGISTRY.get(kind)
    if spec is not None:
        return (spec.prefix, spec.label, spec.core_tag)
    return ("⬜", kind or "기타", "")


# ─────────────────────────────────────────────────────────────────────────────
# 2. CardScaffold — 산출물 (markdown 문자열 + 구조 필드)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CardScaffold:
    card_id: str                 # = source::document_id
    section: str                 # global | domestic | watch | recall_table
    kind: str                    # 카드 유형(내부 분류 키)
    evidence: str                # A | B | C
    modality: str                # Chemical | Biologic | Other | ""
    signal_tier: str             # Tier 1|2|3
    date: str                    # 원본 발행일 (정렬 키)
    markdown: str                # Python 완성 골격 + 산문 토큰
    prose_input: dict[str, Any]  # §9 LLM 최소 컨텍스트 (raw 전체 아님)
    recall_group_key: str = ""   # §12(E) — recall 다품목 통합 키(해당 시)
    status_hint: str = ""        # graceful degrade 시 'Error'
    needs_llm_slots: tuple[str, ...] = ()  # 이 카드가 비운 슬롯 토큰
    # [WL 심층분석 fan-out 2026-07-01] 이 카드가 카드별 fan-out 심층분석(5섹션, 6종 동결 슬롯과
    # 완전 분리된 7번째·선택적 슬롯) 대상인지. warning-letter 유형 + raw.wl_body_full 확보 시만
    # True. additive — 다른 모든 유형·기존 카드는 항상 False(golden 불변).
    deep_analysis_ready: bool = False
    merged_into: str = ""        # §14(F) — 병합 멤버는 대표 card_id 로 마킹(렌더 제외, Status 유지)
    # ── web-card(§3) 직렬화 보조 필드 — to_dict()/handoff v2 에는 미직렬화 ──
    merged_count: int = 1        # §14 병합 멤버수(1=단독). 대표만 >1
    merged_items: tuple[str, ...] = ()  # §14 병합 전체 품목명(대표). 비병합=()
    merged_target: str = ""      # §14 병합 headline_target 치환값(제목과 동일 헬퍼)
    merged_product: str = ""     # §14 병합 facts 제품행 치환값(W2 와 동일 헬퍼)
    row: dict[str, Any] = field(default_factory=dict, repr=False)  # to_web_card producer 재사용
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # (직렬화 제외 — handoff 무영향)

    def to_dict(self) -> dict[str, Any]:
        """handoff v2 직렬화용(결정론 — prose_input 은 sort_keys 로). raw 미포함."""
        d = {
            "card_id": self.card_id,
            "section": self.section,
            "kind": self.kind,
            "evidence": self.evidence,
            "modality": self.modality,
            "signal_tier": self.signal_tier,
            "date": self.date,
            "card_scaffold": self.markdown,
            "prose_input": self.prose_input,
            "needs_llm_slots": list(self.needs_llm_slots),
        }
        if self.recall_group_key:
            d["recall_group_key"] = self.recall_group_key
        if self.status_hint:
            d["status_hint"] = self.status_hint
        if self.merged_into:
            d["merged_into"] = self.merged_into
        d.update(self.deep_fields())
        d.update(self.translation_fields())
        return d

    def deep_fields(self) -> dict[str, Any]:
        """심층분석 fan-out 입력 3종 — 대상 카드가 아니면 빈 dict(기존 카드 완전 무영향).

        [WL 심층분석 fan-out] 대상 카드만 전문(全文)을 별도 명시적 키로 노출한다 — raw 전체는
        여전히 미포함(기존 원칙 불변). 6종 동결 슬롯 Routine 은 이 키를 쓰지 않는다(무관심
        필드 — 프롬프트가 참조하지 않으면 컨텍스트에 영향 없음). fan-out 소비자만 읽는다.

        내보내는 키(`deep_analysis_fanout.build_jobs` 가 읽는 전부):
          · `deep_analysis_ready` — 대상 표시
          · `deep_analysis_input.body_full` — 유형별 raw 키(`SourceSpec.deep_body_key`:
            WL=`wl_body_full` · admin-action=`admin_body_full` · fda-483=`fda483_body_full`).
            `deep_analysis_ready` 가 True 인 시점이라 그 키는 항상 채워져 있다. 소비자는 이
            body_full 하나만 서브에이전트에 준다(카드 격리 불변).
          · `kind` — 유형별 프롬프트 선택용(WL/admin-action/fda-483). `build_jobs` 가
            `card_type` 으로 싣는다.

        ★ 2026-07-27 신설 — 종전엔 이 방출이 `to_dict()` 안에 인라인돼 있었다. 그런데
        **실제 Notion handoff 를 만드는 `grm_handoff.build_routine_handoff_payload_v2` 는
        `to_dict()` 를 쓰지 않고** 필드를 손으로 골라 담는다(`_HANDOFF_V2_ROW_KEEP` + 카드
        속성 일부). 그 목록에 이 세 키가 없어서, 클라우드 Routine 이 읽는 handoff 에는
        deep 입력이 **한 번도 실린 적이 없었다** — Routine 은 매주 "deep 대상 0건 → 단계
        생략(정상)"으로 판단했고 그게 규정상 옳은 행동이었다(2026-07-27 실측: 실제 19건).
        직렬화기가 둘로 갈라진 것이 원인이므로 **방출 지점을 이 함수 하나로 묶어** 재발을
        막는다. 두 직렬화기가 같은 함수를 부르므로 한쪽만 갱신되는 표류가 구조적으로 불가능하다.
        """
        if not self.deep_analysis_ready:
            return {}
        return {
            "deep_analysis_ready": True,
            "deep_analysis_input": {
                "body_full": self.raw.get(_spec(self.kind).deep_body_key, "")},
            "kind": self.kind,
        }

    def translation_fields(self) -> dict[str, Any]:
        """[상세 국문 병기 2026-07-27] 결정론 상세 전문의 **번역 입력** 방출.

        심층분석(`deep_fields`)과 왜 나누는가 — NCR 은 심층분석 대상이 아니다(4섹션 스키마·
        D2 근거규칙이 WL/행정처분/483 용). 필요한 건 분석이 아니라 **이미 확보한 결정론 원문의
        국문 병기** 하나뿐이다. NCR 을 deep 대상으로 만들면 게이트가 요구하는 4섹션을 억지로
        생성하게 되므로, 번역만 요구하는 별도 신호를 둔다.

        내보내는 키(`deep_analysis_fanout.build_translation_jobs` 가 읽는 전부):
          · `ncr_translation_ready` — 대상 표시
          · `ncr_translation_input` — 번역할 원문 필드(있는 것만: nature/action/operations/
            additional). 이 값은 결정론 상세 슬롯과 **같은 producer**(`_deterministic_detail`)
            에서 나오므로 발행 카드에 실릴 원문과 글자 단위로 같다(짝 안 맞는 번역 불가능).
          · `kind` — 유형 표시(eu-gmp-ncr / mhra-gmp-ncr / who-inspection).

        ★ WHOPIR(WHO 공개 실사보고서)도 같은 채널을 탄다 — 필요한 것이 "분석"이 아니라
        "이미 확보한 결정론 원문의 국문 병기"라는 점이 NCR 과 완전히 같기 때문이다. 와이어
        키(`ncr_translation_*`)는 Routine 프롬프트가 이미 참조하고 있어 유지하되, 채널의
        의미는 **NCR 전용이 아니라 결정론 상세 일반**이다(필드명은 상세 타입이 정한다).

        ★ [WL 위반항목 국문 2026-08-24] Warning Letter 의 결정론 위반 표제(`wl_violations`)도
        같은 원리의 번역 입력을 방출한다 — 단, 산출 형태가 번호 목록이라 와이어 키를 나눈다:
          · `wl_violation_translation_ready` — 대상 표시
          · `wl_violation_translation_input` — `[{number, statement}]` (발행 카드의
            `deterministic_detail.violations` 와 같은 producer → 글자 단위 동일).
        Routine 은 이걸 번역해 deep 델타 항목에 `violations_ko: [{number, statement_ko}]` 로
        싣고, `inject_slots._merge_wl_violation_translations` 가 번호로 병합한다. 이 채널이
        없던 것이 "슬롯·병합층은 있는데 라우틴이 안 채우는"(#670 이후 3주 연속 영문 단독)
        결손의 원인이었다 — 병합층(소비자)은 2026-08-10 에 생겼는데 생산 지시·입력이 없었다.

        `deep_fields()` 와 같은 이유로 **방출 지점을 이 함수 하나로** 묶는다(두 직렬화기가
        같은 함수를 부른다 — 한쪽만 갱신되는 표류 구조적 차단).
        """
        detail = _deterministic_detail(self.kind, self.row, self.raw)
        if not isinstance(detail, dict):
            return {}
        if detail.get("type") in _NCR_TRANSLATION_DETAIL_TYPES:
            payload = {k: detail[k] for k in _NCR_TRANSLATION_FIELDS
                       if str(detail.get(k) or "").strip()}
        elif detail.get("type") == "whopir_report":
            payload = whopir_translation_input(detail)
        elif detail.get("type") == "wl_violations":
            rows = [{"number": str(v.get("number") or ""), "statement": v["statement"]}
                    for v in detail.get("violations") or []
                    if isinstance(v, dict) and str(v.get("statement") or "").strip()]
            if not rows:
                return {}
            return {
                "wl_violation_translation_ready": True,
                "wl_violation_translation_input": rows,
                "kind": self.kind,
            }
        else:
            return {}
        if not payload:
            return {}
        return {
            "ncr_translation_ready": True,
            "ncr_translation_input": payload,
            "kind": self.kind,
        }

    def to_web_card(self, render_entry: dict[str, Any] | None = None,
                    cfg: "FixedConfig" = DEFAULT_CONFIG) -> dict[str, Any]:
        """이 카드를 `grm-web-card/v1` 카드 dict 로 직렬화(§3 매핑). 순수·결정론.

        사실 셀은 build 단계와 **동일한 결정론 producer**(`_w2_rows`·`_quote_source`·
        `_dual_links`·`_headline_target`·`_kind_meta`)를 재사용한다(재계산 금지, 불변식 #1).
        LLM 슬롯(title_issue·summary·key_facts·implication·checks·비KO quotes[].translation)만
        빈 placeholder("" / [] / "")로 둔다 — null 이 아닌 빈값 = "LLM 채울 자리" 신호.
        `render_entry` = `compute_render_plan()[card_id]`(없으면 render_order/group_label 미산출).
        JSON 값에는 표현 틀 마크업을 넣지 않는다(문서번호 백틱은 `_plain` 으로 제거, 불변식 #6).
        """
        render_entry = render_entry or {}
        row, raw, kind = self.row, self.raw, self.kind
        language = _language(row, kind)
        merged = self.merged_count > 1

        facts = [{"label": l, "value": _plain(v)} for l, v in _w2_rows(kind, row, raw)]
        if merged and self.merged_product:
            facts = _apply_merged_product(facts, self.merged_product)

        quotes: list[dict[str, Any]] = []
        if self.evidence == "A":
            quote = _quote_source(kind, raw)
            if quote:
                quotes = [{"original": seg,
                           "translation": (None if language == "KO" else "")}
                          for seg in _split_sentences(quote)]

        info, official, _fallback = _dual_links(kind, row, raw)
        modality = (cfg.modality_badge.get(self.modality, self.modality)
                    if (self.modality and not _spec(kind).normative) else None)
        headline_target = (self.merged_target if (merged and self.merged_target)
                           else _headline_target(row))
        # [회수 병합 범위 표기 2026-08-25] 병합 대표 카드의 결정론 상세는 **대표 레코드 하나의**
        # 사실이다(로트·수량·타임라인이 전부 그렇다). `merge_recall_cards` 는 멤버의 raw 를
        # 보존하지 않으므로 나머지 품목의 로트를 만들어낼 수 없고, 만들어서도 안 된다 —
        # 범위 표기는 렌더가 카드 최상위 `merged_count` 를 읽어 붙인다(상세 안에 복제하면
        # 소급 병합 CLI 가 두 값을 맞춰 줘야 하는 두 번째 원천이 생긴다).
        detail = _deterministic_detail(kind, row, raw)

        return {
            "id": row.get("document_id", ""),
            "render_order": render_entry.get("render_order"),
            "group": _WEB_GROUP.get(self.section, self.section),
            "group_label": render_entry.get("group_label") or None,
            "agency": _regulator(row.get("source", "")),
            "card_type": _kind_meta(kind)[1],
            "category": _category(kind),
            "modality": modality,
            "evidence_level": self.evidence,
            "signal_tier": _signal_tier_num(self.signal_tier),
            "signal_label": _signal_level(self.signal_tier),
            "type_tag": (_kind_meta(kind)[2] or None),
            "headline_target": headline_target,
            "title_issue": "",            # LLM
            "summary": "",                # LLM
            "facts": facts,               # 코드-verbatim
            "quotes": quotes,             # original 코드-verbatim / translation LLM(비KO)
            "evidence_basis": ("Intake raw" if self.evidence == "A"
                               else "공식 인덱스 + 보조 출처"),
            "key_facts": [],              # LLM
            "implication": "",            # LLM
            "checks": [],                 # LLM
            **({"deterministic_detail": detail} if detail else {}),
            # ^ [상세보기 결정론 승격 2026-07-02] 결정론 상세 슬롯 — WL deep_analysis(LLM 분석층)와
            # 별개의 결정론 층(환각 0). `type` 분기: gmp-inspection 지적 표(gmp_deficiencies)·
            # FDA 483 Observation(fda_483_observations). 없으면 키 자체
            # 부재(요약카드 유지) → 기존 20+ golden web-card 바이트 불변(additive).
            **({"deep_analysis": None} if self.deep_analysis_ready else {}),
            # ^ [WL 심층분석 fan-out] 7번째·선택적 슬롯(6종 동결 슬롯과 별개) — placeholder
            # None 은 "fan-out 검증 통과 전" 신호. deep_analysis_ready=False 인 카드(대다수)는
            # 이 키 자체가 없다 — 기존 20+ golden web-card 픽스처 바이트 불변(additive).
            **({"source_body_captured": True} if _has_source_body(raw) else {}),
            # ^ [정직성 신호 일반화 2026-07-20] 구 `source_excerpt_present` 는 ECA/전문지 소스
            # 한 곳(eca_article_excerpt·article_excerpt)에만 붙인 반창고였다 — 같은 결함(원문
            # 확보 여부를 하류가 알 방법이 없음)이 WL·행정처분·GMP실사·483 등 다른 소스에서도
            # 재발했다(Health Canada 회수 6건 실측). `_has_source_body`(§13b, `_prose_input` 의
            # `source_body_captured` 와 동일 판정)로 전 소스 공통 신호로 일반화한다. 값이 True 일
            # 때만 키를 싣는 방식은 그대로 유지(부재 시 키 미추가 — 기존 golden 바이트 불변).
            "merged_count": self.merged_count,
            "merged_items": list(self.merged_items),
            "sources": {
                "info_url": info,
                "official_url": official,
                "official_is_pdf": _official_is_pdf(official),
                # P1 = 고정 placeholder. P3 D7 가 실제 200 체크로 덮어씀(골든 결정론 유지).
                "link_check": {"info": "pending", "official": "pending"},
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Notion-renderable 마크다운 헬퍼 (v15.8 카드 표준 문법만)
# ─────────────────────────────────────────────────────────────────────────────
def _callout(lines: list[str], icon: str, color: str | None = None) -> str:
    """<callout> 블록. 내용은 탭 1개 들여쓰기(v15.8 §1)."""
    head = f'<callout icon="{icon}"'
    head += f' color="{color}">' if color else ">"
    body = "\n".join("\t" + ln for ln in lines)
    return f"{head}\n{body}\n</callout>"


def _table(rows: list[tuple[str, str]]) -> str:
    """2열(라벨·내용) 표. 라벨 셀은 bold. header-row 없음(메타 사실표, §13.1-3)."""
    out = ["<table>"]
    for label, value in rows:
        out.append(f"<tr><td>**{label}**</td><td>{value}</td></tr>")
    out.append("</table>")
    return "\n".join(out)


def _quote_lines(text: str, numbered: bool) -> list[str]:
    """원문 인용을 `>` 마크다운 줄로(Evidence A 전용). numbered 시 ①② 부여."""
    segs = _split_sentences(text)
    marks = "①②③④⑤"
    out = []
    for i, seg in enumerate(segs):
        prefix = f"{marks[i]} " if (numbered and len(segs) > 1) else ""
        out.append(f"> {prefix}{seg}")
    return out


def _h3(text: str) -> str:
    return f"### {text}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. 텍스트 유틸 (결정론)
# ─────────────────────────────────────────────────────────────────────────────
def _split_sentences(text: str, max_segs: int = 2) -> list[str]:
    """문장 경계로 ≤max_segs 분할(한국어/영문). 빈 입력 → []."""
    t = (text or "").strip()
    if not t:
        return []
    parts = re.split(r"(?<=[.。!?])\s+", t)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= max_segs:
        return parts or [t]
    return parts[:max_segs]


def _truncate_at_sentence(text: str, limit: int) -> str:
    """limit 자 이내로 자르되 문장 경계 우선(§12C admin EXPOSE_CONT 규칙)."""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    head = t[:limit]
    # 마지막 문장부호에서 자름
    m = list(re.finditer(r"[.。!?]", head))
    if m:
        return head[: m[-1].end()].strip()
    return head.rstrip() + "…"


def _code(value: str) -> str:
    """inline code 배지 (식별자·배지 전용, v15.8 §강조)."""
    return f"`{value}`"


def _first(*vals: Any) -> str:
    for v in vals:
        if v:
            return str(v)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 5. 카드 유형 분류 (row → kind)
# ─────────────────────────────────────────────────────────────────────────────
def resolve_kind(row: dict[str, Any]) -> str:
    source = row.get("source", "")
    toc = (row.get("type_or_class", "") or "").lower()
    if source == SOURCE_FDA_WL:
        return "warning-letter"
    if source == SOURCE_RECALL:
        return "openfda-recall"
    if source == SOURCE_HC:
        return "hc-recall"
    if source == SOURCE_FDA_483:
        return "fda-483"
    if source == SOURCE_EU_GMP_NCR:
        return "eu-gmp-ncr"
    if source == SOURCE_MHRA_GMP_NCR:
        return "mhra-gmp-ncr"
    if source == SOURCE_ICH:
        return "ich"
    if source == SOURCE_WHO:
        if "noc" in toc:
            return "who-noc"
        if "inspection" in toc or "whopir" in toc:
            return "who-inspection"
        return "who-news"
    if source == SOURCE_MFDS:
        if toc == TYPE_ADMIN_ACTION:
            return "admin-action"
        if toc == TYPE_RECALL_QUALITY:
            return "recall-quality"
        if toc == TYPE_GMP_INSPECTION:
            return "gmp-inspection"
        if toc == TYPE_GMP_CERTIFICATE:
            return "gmp-certificate"
        if "legislative" in toc:
            return "legislative"
        if "safety" in toc:
            return "safety-letter"
        if "regulation" in toc or "notice-final" in toc:
            return "regulation"
        return "mfds-notice"          # MFDS guidance-industry/internal RSS → Evidence B
    if source == SOURCE_FR:
        return "guidance"             # FR(abstract) → Evidence A 가능
    if source == SOURCE_MHRA:
        # [MHRA 회수 포지션 정렬 2026-07-22] gov.uk drug-device-alerts 로 인입된 의약품
        # 회수/결함(type_or_class="Class N Medicines Recall/Defect …")은 FDA·HC·MFDS 회수와
        # 동일 포지션(Recall 섹션·Evidence A)으로 라우팅한다. 인스펙터 블로그(type_or_class=
        # "Blog"/기타)는 기존대로 rss-news(글로벌·GMP News) 유지. 수집기가 _extract_mhra_alert
        # 에서 남긴 회수 신호(_is_mhra_medicines_alert 와 동일 판정)를 여기서 소비한다.
        if re.search(r"medicines?\s+(recall|defect)", toc):
            return "mhra-recall"
        return "rss-news"             # 인스펙터 블로그 등 비회수 → Evidence B
    if source in (SOURCE_EMA, SOURCE_PICS, SOURCE_ECA, SOURCE_ISPE):
        return "rss-news"             # RSS 요약만 → Evidence B
    return "rss-news"


# 규범 문서(§4·제품군 배지 생략) 집합 `_NORMATIVE_KINDS` 와 Evidence A 가능 유형 집합
# `_A_ELIGIBLE_KINDS`(§6·"A ⟺ 인용 가능한 raw 필드" 불변식)는 이제 SourceSpec.normative/
# a_eligible 필드로 흡수 — §10b 레지스트리에서 파생 재수출한다(단일원천).


# ─────────────────────────────────────────────────────────────────────────────
# 6. Evidence 판정 (§6 + §12(D)/(H))
# ─────────────────────────────────────────────────────────────────────────────
def determine_evidence(kind: str, row: dict[str, Any], raw: dict[str, Any] | None) -> str:
    """Evidence 판정(§6·§12D/H). 불변식: A ⟺ 인용 가능한 raw 필드 존재(_quote_source)."""
    # graceful degrade(단계 B): raw fetch 실패 → B 강등
    if row.get("raw_fetch_ok") is False or row.get("evidence_hint") == "B":
        return "B"
    quote = _quote_source(kind, raw)
    # search 단계가 기록한 힌트 우선(있으면), 없으면 유형 A-eligible + quote 존재 시 A.
    hint = (row.get("evidence_candidate") or row.get("evidence_hint") or "").upper()
    if hint in ("A", "B", "C"):
        ev = hint
    elif _spec(kind).a_eligible and raw and quote:
        ev = "A"
    else:
        ev = "B"
    # 정합 가드: Evidence A 는 반드시 W3 인용이 가능해야 한다(§6 — A→W3). 아니면 B.
    if ev == "A" and not quote:
        ev = "B"
    return ev


def _language(row: dict[str, Any], kind: str) -> str:
    lang = (row.get("language") or "").upper()
    if lang:
        return lang
    # §12(B): MFDS/ICH/WHO/HC 만 채워짐. 그 외(FR/Recall/EMA/MHRA/PIC/S/ECA/WL) 기본 EN
    if row.get("source") == SOURCE_MFDS:
        return "KO"
    return "EN"


# ─────────────────────────────────────────────────────────────────────────────
# 7. W3 원문 인용 소스 필드 (§12(C))
# ─────────────────────────────────────────────────────────────────────────────
# per-kind 인용 추출기(A-eligible 만) — SourceSpec.quote 로 배선. 각기 실제 수집기 raw
# 키 기준, 형제와 동형으로 250자 문장경계 절단. 미지정 kind(RSS·WHO·ICH §12H·WL 본문
# 미수집)는 quote=None → dispatcher 가 "" 반환(Evidence B).
def _quote_admin(raw: dict[str, Any]) -> str:
    return _truncate_at_sentence(raw.get("EXPOSE_CONT", ""), 250)


def _quote_recall_quality(raw: dict[str, Any]) -> str:
    # RTRVL_RESN(회수사유)은 한국어 장문에 종결부호가 없는 경우가 많아 무절단 시 '>' 인용
    # 라인이 Notion rich-text 한도(2000자)를 초과할 수 있다(300자 prose_input 가드는 렌더
    # 라인 미보호) → 형제 분기와 동형 250자 절단(A3).
    return _truncate_at_sentence(_first(raw.get("RTRVL_RESN")), 250)


def _quote_gmp_inspection(raw: dict[str, Any]) -> str:
    # 표지 너머 결론(지적/보완사항) 우선 — 없으면 전체 본문 폴백(P6).
    return _truncate_at_sentence(
        _first(raw.get("attachment_deficiency_excerpt"), raw.get("attachment_text")), 250)


def _quote_openfda_recall(raw: dict[str, Any]) -> str:
    return _truncate_at_sentence(raw.get("reason_for_recall", ""), 250)


def _quote_hc_recall(raw: dict[str, Any]) -> str:
    return _truncate_at_sentence(_first(raw.get("Issue"), raw.get("What you should do")), 250)


def _quote_mhra_recall(raw: dict[str, Any]) -> str:
    # gov.uk drug-device-alerts Atom 의 summary 는 회수 사유 문장(항상 채워짐 — 실측 58/58).
    # 부재 시 title 폴백 없이 "" → determine_evidence 가 Evidence B 로 강등(graceful).
    return _truncate_at_sentence(_first(raw.get("summary"), raw.get("description")), 250)


def _quote_guidance(raw: dict[str, Any]) -> str:  # FR 전용 — abstract(없으면 title)
    return _truncate_at_sentence(_first(raw.get("abstract"), raw.get("title")), 250)


def _quote_eu_gmp_ncr(raw: dict[str, Any]) -> str:
    # EU GMP NCR 의 W3 인용 = 위반내용(Nature of non-compliance) 원문(항상 채워짐 — 수집기
    # 게이트). 형제 분기와 동형 250자 문장경계 절단(전문은 _detail_eu_gmp_ncr 상세슬롯).
    return _truncate_at_sentence(_first(raw.get("ncr_nature")), 250)


def _quote_mhra_gmp_ncr(raw: dict[str, Any]) -> str:
    # MHRA GMP NCR 의 W3 인용 = 위반내용(Nature of non-compliance) 원문(항상 채워짐 — 수집기
    # 게이트). EU NCR 형제와 동일 필드·동형 250자 절단(전문은 _detail_mhra_gmp_ncr 상세슬롯).
    return _truncate_at_sentence(_first(raw.get("ncr_nature")), 250)


def _quote_source(kind: str, raw: dict[str, Any] | None) -> str:
    """유형별 W3 인용 소스 필드(§12C). `SourceSpec.quote` 디스패치. 없으면 "" → Evidence B.

    A-eligible 만 인용 가능: admin(EXPOSE_CONT)·recall(RTRVL_RESN)·gmp(attachment_text)·
    openfda-recall(reason_for_recall)·hc-recall(Issue)·guidance/FR(abstract). 그 외(RSS·
    WHO·ICH §12H·WL 본문 미수집)는 "".
    """
    if not raw:
        return ""
    fn = _spec(kind).quote
    return fn(raw) if fn else ""


# ─────────────────────────────────────────────────────────────────────────────
# 7b. 결정론 상세보기 슬롯 (spec §16, 2026-07-02) — WL deep_analysis(LLM)와 별개 결정론 층
# ─────────────────────────────────────────────────────────────────────────────
_DEFICIENCY_ROW_KEYS = ("area", "severity", "legal_basis", "summary", "followup")
_FDA483_OBSERVATION_ROW_KEYS = ("number", "deficiency", "detail")
_WL_VIOLATION_ROW_KEYS = ("number", "statement", "citation")


# per-kind 결정론 상세 슬롯 생성기 — SourceSpec.detail 로 배선. WL `deep_analysis`(LLM 분석층·
# fan-out·게이트)와 **완전 별개**의 결정론 층(생성 0 → 환각 0, 근거대조 게이트 불필요). 해당
# raw 필드 부재면 None(graceful·요약카드 유지). row 인자는 시그니처 통일용(현행 미사용).
def _detail_gmp_deficiencies(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """gmp-inspection 지적사항 표(`raw.gmp_deficiencies`)."""
    rows = raw.get("gmp_deficiencies")
    if not (isinstance(rows, list) and rows):
        return None
    # 방어적 재정규화: 5개 키만·문자열 강제. 근거법령/지적내용 둘 다 빈 행은 제외
    # (수집기 게이트와 동일 불변 — 손수 작성 raw·손상 입력도 안전).
    norm = [{k: str(r.get(k, "") or "") for k in _DEFICIENCY_ROW_KEYS}
            for r in rows if isinstance(r, dict)]
    norm = [r for r in norm if r["legal_basis"] or r["summary"]]
    if not norm:
        return None
    severity_summary: dict[str, int] = {}
    for r in norm:
        sev = r["severity"]
        if sev:
            severity_summary[sev] = severity_summary.get(sev, 0) + 1
    return {
        "type": "gmp_deficiencies",
        "count": len(norm),
        "severity_summary": severity_summary,
        "rows": norm,
    }


def _detail_fda_483_observations(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """FDA 483 Observation 번호 목록(`raw.fda_483_observations`).

    [원문·국문 병기 2026-07-09] 국문 번역(deficiency_ko/detail_ko)은 존재할 때만 보존한다
    (fan-out 이 원문 statement 를 번역해 채우는 선택 필드 — 통상은 inject_slots 가 웹 카드에
    직접 병합하나, raw 에 이미 있으면 여기서도 통과). 부재 시 키를 아예 추가하지 않아
    기존 골든(ko 없는 fixture) 바이트 불변."""
    obs = raw.get("fda_483_observations")
    if not (isinstance(obs, list) and obs):
        return None
    norm: list[dict[str, str]] = []
    for o in obs:
        if not isinstance(o, dict):
            continue
        r = {k: str(o.get(k, "") or "") for k in _FDA483_OBSERVATION_ROW_KEYS}
        for kk in ("deficiency_ko", "detail_ko"):     # 있을 때만 — 없으면 키 미추가(골든 불변)
            if o.get(kk):
                r[kk] = str(o[kk])
        norm.append(r)
    # [OCR 판독 잡음 차단 2026-07-27] 알파벳 실질이 없는 표제("/T" 등 스캔 여백 파편)는
    # 관찰이 아니다 — 수집기 파서와 **같은 기준**을 여기서도 적용해, 낡은 raw 를 들고 있는
    # 스캐폴드가 잡음을 관찰로 발행하지 않게 한다(파서만 고치면 이미 굳은 스캐폴드는 못 고친다).
    from collect_fda_483 import _is_legible_deficiency   # 지연 import — 기준의 단일 출처
    norm = [o for o in norm if _is_legible_deficiency(o["deficiency"])]
    if not norm:
        return None
    detail: dict[str, Any] = {
        "type": "fda_483_observations",
        "count": len(norm),
        "observations": norm,
    }
    # [OCR 출처 표기 2026-07-27] 이 영문이 **원문 텍스트층**인지 **우리가 OCR 로 판독한 것**
    # 인지 구분한다. 스캔 483 OCR 폴백 도입 이후 두 경로가 섞이는데, 렌더는 이 블록을
    # "원문 · FDA 483" 이라고 표시한다 — 우리 판독 결과를 원문이라고 부르면 그건 거짓이다
    # (OCR 은 오인식이 있고 실제로 관찰됐다). 판독물임을 라벨에서 밝힌다.
    # `pdf-ok-ocr` 이 아닌 기존 카드는 키 미추가 → 골든 바이트 불변(additive).
    if str(raw.get("fda483_text_status") or "").split(":", 1)[0] == "pdf-ok-ocr":
        detail["text_source"] = "ocr"
    # [실사관 표기 2026-07-30] 서명블록에서 뽑은 실사관 이름(`raw.fda483_inspectors` —
    # collect_fda_483._extract_483_inspectors 산출, ENABLE_FDA_483_OBSERVATIONS/DEEP 과
    # 독립인 순수 결정론 층). 이 함수는 raw 값을 있는 그대로 옮기기만 한다 — 방어적 정제
    # (비문자열/공백 제거·strip·6개 절단)는 렌더 계층(web/render.py._card_view)의 몫이다
    # (수집기가 이미 정제한 값을 여기서 다시 검증하는 이중화를 피한다). 리스트가 아니거나
    # 비어있으면 키 자체를 달지 않는다(다른 조건부 raw 필드와 동일 관례 → 골든 바이트 불변).
    inspectors = raw.get("fda483_inspectors")
    if isinstance(inspectors, list) and inspectors:
        detail["inspectors"] = inspectors
    return detail


def _detail_wl_violations(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """[2026-07-20] Warning Letter 위반 표제 목록(`raw.wl_violations`) — 483 관찰 슬롯 동형.

    왜 필요한가 — WL 은 유일하게 결정론 상세층이 없어, 위반 상세를 카드에 싣는 경로가
    `deep_analysis` fan-out(LLM) **하나뿐**이었다. fan-out 이 안 돈 주에는 6슬롯 LLM 이 300자로
    잘린 도입구만 보고 "세부 위반내용은 원문에 명시되지 않았다"고 쓰는 일까지 벌어졌다
    (2026-07-20 발행분 2건). 이 슬롯이 그 **바닥**이다 — fan-out 성패와 무관하게, 수집된 편지
    원문에서 뽑은 조항별 위반 표제가 항상 카드에 남는다(생성 0 → 환각 0).

    `번역(statement_ko)`은 있을 때만 보존한다(483 의 deficiency_ko/detail_ko 동형 — 부재 시
    키 미추가로 기존 골든 바이트 불변).
    """
    rows = raw.get("wl_violations")
    if not (isinstance(rows, list) and rows):
        return None
    norm: list[dict[str, str]] = []
    for v in rows:
        if not isinstance(v, dict):
            continue
        r = {k: str(v.get(k, "") or "") for k in _WL_VIOLATION_ROW_KEYS}
        if v.get("statement_ko"):                    # 있을 때만 — 없으면 키 미추가(골든 불변)
            r["statement_ko"] = str(v["statement_ko"])
        norm.append(r)
    norm = [v for v in norm if v["statement"]]
    if not norm:
        return None
    return {
        "type": "wl_violations",
        "count": len(norm),
        "violations": norm,
    }


# [NCR 국문 병기 2026-07-27] 번역 대상 상세 타입/필드 — `CardScaffold.translation_fields()` 와
# `inject_slots._merge_ncr_translations` 가 같은 목록을 본다(한쪽만 늘어나는 표류 차단).
_NCR_TRANSLATION_DETAIL_TYPES = ("eu_gmp_ncr_statement", "mhra_gmp_ncr_statement")
_NCR_TRANSLATION_FIELDS = ("nature", "action", "operations", "additional")


def whopir_translation_input(detail: dict[str, Any]) -> dict[str, str]:
    """WHOPIR 상세 → 번역 입력 필드맵. **필드명 계약의 단일 정의처**.

    `card_scaffold.translation_fields()`(방출)와 `inject_slots._merge_whopir_translations`
    (병합)가 같은 함수를 부른다 — 한쪽만 규칙이 바뀌는 표류를 구조적으로 막는다. 키는
    결론 `outcome` + 항목별 `s<번호>_title`/`s<번호>`. 번호는 원문 섹션 번호 그대로라
    항목이 늘거나 빠져도 짝이 어긋나지 않는다(위치 인덱스였다면 어긋난다).
    """
    out: dict[str, str] = {}
    outcome = str(detail.get("outcome") or "").strip()
    if outcome:
        out["outcome"] = outcome
    for sec in detail.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        no = sec.get("no")
        text = str(sec.get("text") or "").strip()
        if not (isinstance(no, int) and no > 0 and text):
            continue
        title = str(sec.get("title") or "").strip()
        if title:
            out[f"s{no}_title"] = title
        out[f"s{no}"] = text
    return out


def _detail_eu_gmp_ncr(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """EU GMP NCR Statement 전문(`raw.ncr_nature`/`ncr_action`) — 결정론 상세슬롯.

    W3 인용은 위반내용(Nature)을 250자로만 보여주므로, 완결된 위반내용·당국조치 전문을
    이 슬롯에 verbatim 으로 싣는다(생성 0 → 환각 0). 발행 NCA 국가·제품범위·비준수 운영항목·
    추가코멘트(있을 때만)도 함께. 공식 원문(PDF)은 official_url 로 별도 노출된다."""
    nature = str(raw.get("ncr_nature") or "").strip()
    action = str(raw.get("ncr_action") or "").strip()
    if not (nature or action):
        return None
    detail: dict[str, Any] = {
        "type": "eu_gmp_ncr_statement",
        "authority_country": str(raw.get("authority_country") or ""),
        "product_scope": str(raw.get("product_scope") or ""),
        "operations": str(raw.get("ncr_operations") or ""),
        "nature": nature,
        "action": action,
    }
    additional = str(raw.get("ncr_additional") or "").strip()
    if additional:                                   # 있을 때만 — 없으면 키 미추가(골든 불변)
        detail["additional"] = additional
    return detail


def _detail_mhra_gmp_ncr(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """MHRA GMP NCR Statement 전문(`raw.ncr_nature`/`ncr_action`) — 결정론 상세슬롯.

    EU NCR 형제(`_detail_eu_gmp_ncr`)와 동형이나 **별도 type**(`mhra_gmp_ncr_statement`)로
    분리한다 — card.html 이 출처 라벨("EudraGMDP")을 상세 블록에 하드코딩하고 있어, EU
    골든 바이트를 건드리지 않고 MHRA 라벨을 붙이려면 형제 분기가 필요하기 때문. 위반내용
    (Nature)·당국조치 전문을 verbatim 으로 싣고(생성 0 → 환각 0), 발행기관·제품유형·비준수
    운영항목·제한사항(있을 때만)도 함께. 공식 원문(상세 페이지)은 official_url 로 별도 노출."""
    nature = str(raw.get("ncr_nature") or "").strip()
    action = str(raw.get("ncr_action") or "").strip()
    if not (nature or action):
        return None
    detail: dict[str, Any] = {
        "type": "mhra_gmp_ncr_statement",
        "authority_country": str(raw.get("authority_country") or ""),
        "product_scope": str(raw.get("product_scope") or ""),
        "operations": str(raw.get("ncr_operations") or ""),
        "nature": nature,
        "action": action,
    }
    additional = str(raw.get("ncr_additional") or "").strip()
    if additional:                                   # 제한사항 — 있을 때만(골든 불변)
        detail["additional"] = additional
    return detail


def _detail_whopir_report(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """WHOPIR 공개 실사보고서 구조(`raw.whopir_report`) — 결정론 상세슬롯.

    WHOPIR PDF 는 [결론(Inspection outcome) → Part 2 활동범위 → **Part 3 항목별 요약
    (1~22개 섹션)**] 로 잘 정돈돼 있는데, 그동안 카드에는 링크와 1,500자 excerpt 만 실려
    이 구조가 통째로 유실됐다. 수집기(`collect_who.extract_whopir_report`)가 뽑은 결론 +
    항목별 요약을 verbatim 으로 싣는다(생성 0 → 환각 0).

    SRA/NRA 실사증거에 의존한 보고서(`report_kind == "reliance"`)는 항목 요약 자체가
    없으므로 결론 + 인용 실사기관만 싣는다 — 없는 항목을 만들어내지 않는다."""
    report = raw.get("whopir_report")
    if not isinstance(report, dict):
        return None
    outcome = str(report.get("outcome") or "").strip()
    sections = [
        {"no": int(s.get("no") or 0),
         "title": str(s.get("title") or "").strip(),
         "text": str(s.get("text") or "").strip()}
        for s in (report.get("sections") or [])
        if isinstance(s, dict) and str(s.get("text") or "").strip()
    ]
    reliance = [
        {"authority": str(r.get("authority") or "").strip(),
         "dates": str(r.get("dates") or "").strip()}
        for r in (report.get("reliance") or [])
        if isinstance(r, dict) and str(r.get("authority") or "").strip()
    ]
    if not (outcome or sections or reliance):
        return None
    detail: dict[str, Any] = {
        "type": "whopir_report",
        "report_kind": "reliance" if str(report.get("report_kind")) == "reliance" else "findings",
        "outcome": outcome,
        "sections": sections,
    }
    if reliance:                                     # 있을 때만 — 없으면 키 미추가(골든 불변)
        detail["reliance"] = reliance
    return detail


# ── 회수 계열 결정론 상세(2026-08-25) ────────────────────────────────────────
# 회수 4종(openfda-recall·recall-quality·hc-recall·mhra-recall)은 발행 카드 114장(10주
# 누적 26%)을 차지하면서 부가층이 W3 인용 한 줄뿐이었다 — `detail`·`deep_body_key` 둘 다
# 미배선. 그런데 수집기는 이미 **원천 레코드를 통째로** `raw_payload` 에 넣어 두고 있었다
# (openfda `raw_payload=r` · MFDS `**raw` · HC `**rec`). 즉 원천이 천장이 아니라 **발행이
# 천장**이었고, 수집기 변경·재수집·마이그레이션 0으로 층 하나를 되살릴 수 있다.
#
# 여기서 LLM 심층분석(deep_analysis)이 아니라 결정론 상세를 쓰는 이유: 회수 레코드의 값진
# 부분은 전부 이미 구조화된 사실(진행상태·자진/명령·로트·수량·유통범위·처리 타임라인)이라
# 생성할 것이 없다. 생성 0 → 환각 0 → 근거대조 게이트 불필요.
#
# `mhra-recall` 은 배선하지 않는다 — gov.uk Atom 피드가 주는 5개 키(title·summary·
# category·id·published)를 카드가 이미 전부 쓰고 있어 **미사용 원천이 실제로 없다**.
# 늘리려면 알림 상세 페이지를 수집해야 하므로 수집기 변경 과제로 남긴다(10주 1장).

_OPENFDA_ABSENT = {"", "N/A"}        # 원천이 "값 없음"을 적는 방식 — 필드 자체를 안 싣는다

# 통제어휘 → 국문. 값 집합은 전체 코퍼스 count 집계(약 17,900건)로 확정했다 — 표본 몇 건으로
# 만든 매핑은 "Two or more of the following: …" 같은 다수 어법을 통째로 놓친다.
# 미등재 값은 **fail-open**(원문 그대로 노출) — 손목록이 낡아도 값이 사라지지 않는다.
_OPENFDA_STATUS_KO = {
    "Ongoing": "진행 중", "Completed": "완료", "Terminated": "종결",
}
_OPENFDA_INITIATION_KO = {
    "Voluntary: Firm initiated": "자진회수 (업체 착수)", "FDA Mandated": "FDA 회수명령",
}
_OPENFDA_NOTIFICATION_KO = {
    "Letter": "서한", "Telephone": "전화", "Press Release": "보도자료",
    "E-Mail": "이메일", "FAX": "팩스", "Visit": "방문", "Other": "기타",
    "Two or more of the following: Email, Fax, Letter, Press Release, Telephone, Visit":
        "2가지 이상 병행",
}
# 처리 타임라인 — OpenFDA 가 주는 3개 날짜의 의미와 순서(회수 착수 → FDA 등급 확정 → 공표).
_OPENFDA_TIMELINE = (
    ("recall_initiation_date", "회수 착수"),
    ("center_classification_date", "FDA 등급 확정"),
    ("report_date", "FDA 공표"),
)
# openfda 하위 제품식별 — 브랜드명만으론 무엇이 회수됐는지 알 수 없다(성분·투여경로 필요).
_OPENFDA_PRODUCT_FIELDS = (
    ("brand_name", "브랜드명"),
    ("generic_name", "성분명"),
    ("substance_name", "주성분"),
    ("route", "투여경로"),
    ("application_number", "허가번호"),
)


def _yyyymmdd_to_iso(value: Any) -> str:
    """OpenFDA 날짜(`YYYYMMDD`) → ISO. 형식·범위를 벗어나면 "" — 추측해 만들지 않는다."""
    s = str(value or "").strip()
    if len(s) != 8 or not s.isdigit():
        return ""
    if not ("01" <= s[4:6] <= "12" and "01" <= s[6:8] <= "31"):
        return ""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _openfda_text(raw: dict[str, Any], key: str) -> str:
    """원천이 "값 없음"으로 쓰는 표기(""·"N/A")를 부재로 접는다(빈 라벨 방지)."""
    value = str(raw.get(key) or "").strip()
    return "" if value in _OPENFDA_ABSENT else value


def _openfda_list(sub: dict[str, Any], key: str) -> str:
    """`openfda` 하위 값은 항상 리스트다 — 문자열/스칼라로 와도 안전하게 접는다."""
    value = sub.get(key)
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if str(v or "").strip()]
    else:
        parts = [str(value).strip()] if str(value or "").strip() else []
    seen: list[str] = []
    for p in parts:                                  # 중복 제거(순서 보존)
        if p not in seen:
            seen.append(p)
    return ", ".join(seen)


def _detail_openfda_recall(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """OpenFDA enforcement 레코드의 미사용 사실층 — 결정론 상세슬롯.

    카드는 24개 top-level 필드 중 5개(등급·제품·사유·업체·발행일)만 써 왔다. 나머지는
    수집기가 원본 레코드를 그대로 저장해 두고도 발행에서 버려졌다 — 회수 진행상태,
    자진/명령 구분, 최초 통지수단, 로트번호·유효기한, 회수 수량, 유통범위, 업체 소재지,
    FDA 처리 타임라인, 성분·투여경로·허가번호가 전부 그 안에 있다.

    통제어휘(status·voluntary_mandated·initial_firm_notification)만 국문을 병기한다 —
    값 집합이 닫혀 있어 결정론 매핑이 가능하기 때문. 자유서술(로트·유통범위·수량)은
    원문 그대로 싣고 번역하지 않는다(옮겨 적으면 흘린다)."""
    detail: dict[str, Any] = {"type": "openfda_recall_detail"}

    for key, out_key, ko_map in (
        ("status", "status", _OPENFDA_STATUS_KO),
        ("voluntary_mandated", "initiation", _OPENFDA_INITIATION_KO),
        ("initial_firm_notification", "notification", _OPENFDA_NOTIFICATION_KO),
    ):
        value = _openfda_text(raw, key)
        if not value:
            continue
        detail[out_key] = value
        ko = ko_map.get(value)
        if ko:                                       # 미등재는 fail-open(원문만) — 값 유실 0
            detail[f"{out_key}_ko"] = ko

    for key, out_key in (("product_quantity", "quantity"),
                         ("distribution_pattern", "distribution")):
        value = _openfda_text(raw, key)
        if value:
            detail[out_key] = value

    # 로트/유효기한 — 실무자가 자사 재고와 대조하는 유일한 칸. more_code_info 는 이어붙인다.
    code_info = " ".join(p for p in (_openfda_text(raw, "code_info"),
                                     _openfda_text(raw, "more_code_info")) if p)
    if code_info:
        detail["code_info"] = code_info

    location = ", ".join(p for p in (_openfda_text(raw, "city"), _openfda_text(raw, "state"),
                                     _openfda_text(raw, "country")) if p)
    if location:
        detail["firm_location"] = location

    timeline = [{"label": label, "date": iso}
                for key, label in _OPENFDA_TIMELINE
                if (iso := _yyyymmdd_to_iso(raw.get(key)))]
    if timeline:
        detail["timeline"] = timeline

    sub = raw.get("openfda")
    if isinstance(sub, dict):
        product: list[dict[str, str]] = []
        generic = _openfda_list(sub, "generic_name")
        for key, label in _OPENFDA_PRODUCT_FIELDS:
            value = _openfda_list(sub, key)
            # 주성분(substance_name)은 성분명과 사실상 같은 값일 때가 많다 — 같으면 생략.
            if key == "substance_name" and value.lower() == generic.lower():
                continue
            if value:
                product.append({"label": label, "value": value})
        if product:
            detail["product"] = product

    return detail if len(detail) > 1 else None       # type 만 남으면 부재(블록 미렌더)


def _detail_recall_quality(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """MFDS 회수·판매중지 레코드의 미사용 사실층 — 결정론 상세슬롯.

    data.go.kr 15059114 가 주는 키는 9개다(`PRDUCT·ENTRPS·RTRVL_RESN·ENFRC_YN·
    RECALL_COMMAND_DATE·RTRVL_CMMND_DT·ITEM_SEQ·BIZRNO·STD_CD` — 수집기 spec probe 확정).
    카드는 앞의 3개만 썼고, 그중 **`ENFRC_YN`(강제여부)** 은 자진회수와 회수명령을 가르는
    규제 신호인데도 10주 43장 내내 한 번도 발행되지 않았다.

    `RECALL_COMMAND_DATE` 는 이미 카드 발행일이라 중복이므로 싣지 않는다. `RTRVL_CMMND_DT`
    는 수집기 spec 이 "회수명령일시"로, `collect_mfds_recall._body` 가 "승인일자"로 서로 다르게
    부르고 있어 **의미가 확정되기 전까지 싣지 않는다** — 라벨을 못 붙이는 날짜는 없는 날짜보다
    나쁘다. `nedrug_item_candidate_url` 도 수집기가 스스로 "미검증 후보"라 적어 둔 링크라 제외."""
    detail: dict[str, Any] = {"type": "mfds_recall_detail"}

    enforced = str(raw.get("ENFRC_YN") or "").strip().upper()
    if enforced == "Y":
        detail["enforcement"] = "회수명령 (강제)"
    elif enforced == "N":
        detail["enforcement"] = "자진회수"
    elif enforced:                                   # Y/N 밖의 값은 원문 그대로(fail-open)
        detail["enforcement"] = str(raw.get("ENFRC_YN")).strip()

    for key, out_key in (("ITEM_SEQ", "item_seq"), ("STD_CD", "std_cd"), ("BIZRNO", "bizrno")):
        value = str(raw.get(key) or "").strip()
        if value:
            detail[out_key] = value

    return detail if len(detail) > 1 else None


def _detail_hc_recall(row: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """Health Canada 회수 레코드의 미사용 사실층 — 결정론 상세슬롯.

    HC 오픈데이터 피드는 필드가 11개뿐이고 카드가 이미 10개를 쓴다(천장이 낮다). 다만
    **`What you should do`(권고 조치)** 는 `_quote_hc_recall` 의 *폴백* 이라 `Issue` 가 있는
    한 화면에 안 나온다 — 실측 19장 전부가 그 경우였다. 상세 페이지 보강분(유효성분·함량,
    제형)도 `raw` 에는 있으나 W2 4행 상한에 밀려 미표시였다.

    영문 자유서술은 원문으로 싣고 `action_ko` 슬롯만 열어 둔다(NCR·WHOPIR 형제와 동형 —
    번역층이 붙으면 병기로 렌더된다). 여기서 옮겨 적지 않는다."""
    detail: dict[str, Any] = {"type": "hc_recall_detail"}

    for key, out_key in (("medicinal_ingredient", "ingredient"),
                         ("dosage_form_detail", "dosage_form"),
                         ("What you should do", "action")):
        value = str(raw.get(key) or "").strip()
        if value:
            detail[out_key] = value

    action_ko = str(raw.get("what_you_should_do_ko") or "").strip()
    if detail.get("action") and action_ko:           # 있을 때만 — 없으면 키 미추가(골든 불변)
        detail["action_ko"] = action_ko

    return detail if len(detail) > 1 else None


def _deterministic_detail(kind: str, row: dict[str, Any],
                          raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """결정론 상세 슬롯(펼침 상세보기용). `SourceSpec.detail` 디스패치. 없으면 None(요약카드 유지)."""
    fn = _spec(kind).detail
    return fn(row, raw or {}) if fn else None


# ─────────────────────────────────────────────────────────────────────────────
# 8. W8 듀얼링크 (§5 + §12(B)) — L1 실존할 때만, 패턴 유추 금지
# ─────────────────────────────────────────────────────────────────────────────
# per-kind W8 공식원본(📎) 생성기 — SourceSpec.official 로 배선. 반환 (official_url, is_fallback).
# L1(공식 원본)은 필드에 실제 존재할 때만, 없으면 L2 인덱스(⚠️·fallback=True). 패턴 유추 금지.
# 미지정 kind(FR/EMA/MHRA/PIC/S/ECA/WHO noc·news/HC/ICH/gmp-certificate/safety-letter 등)는
# official=None → dispatcher 가 (official_url, False)로 폴백.
def _official_wl(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    return _first(raw.get("url"), row.get("official_url")), False


def _official_admin(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    seq = _first(raw.get("ADM_DISPS_SEQ"))
    # E2(resolve & verify): 수집기가 ENABLE_MFDS_URL_VERIFY=on 일 때만 남기는
    # `admin_l1_verify`("pass"/"fail")를 존중한다. 키가 없으면(flag off=기본) verify
    # 는 None → 현행 동작(seq→L1 단언) 그대로라 golden 바이트 불변(additive).
    verify = raw.get("admin_l1_verify")
    if verify == "fail":
        # 후보 L1 이 live verify 에서 죽음/오류셸 → 정직하게 L2 인덱스 + ⚠️ 강등.
        return "https://nedrug.mfds.go.kr/pbp/CCBAO01", True
    if seq:
        # verify=="pass" → 검증된 L1. None(E2 off) → 현행(미검증 L1 단언, 행위 불변).
        # 라이브 검증 2026-06-16(URL전수검사): 실제 seq(예 2026004188)→ 행정처분정보
        # 레코드 정상 렌더. nedrug getItem 은 무효 seq 도 HTTP 200(error-shell)이라
        # 상태코드로 검증 불가 → E2(본문 길이·오류마커)로만 확정. 잔여 R-1: data.go.kr
        # 15058457 이 ADM_DISPS_SEQ 를 반환하는지 키 보유 CI 확인(증빙 §5.2 URL-1).
        return ("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?"
                f"dispsApplySeq={seq}"), False
    return "https://nedrug.mfds.go.kr/pbp/CCBAO01", True  # L2 인덱스


def _official_recall_quality(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    # L2 인덱스(§12B). 라이브 검증 2026-06-16(URL전수검사): 종전 CCBAH01 은 '재평가공고
    # 및 결과공시' 보드(회수와 무관)였음 → 회수·폐기 보드 CCBAI01 로 정정. 건별 L1 은
    # data.go.kr 15059114 payload 에 nedrug 회수레코드 seq(targetItemSeq)가 없어 불가 →
    # 정직하게 L2 인덱스 유지(📰 는 data.go.kr 회수 데이터셋 — 회수 특정).
    return "https://nedrug.mfds.go.kr/pbp/CCBAI01", True


def _official_openfda_recall(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    # 항목별 L1 없음 → FDA Recalls 인덱스 L2(§5). 패턴 유추 금지.
    official = _first(row.get("official_url"),
                      "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts")
    return official, not row.get("official_url")


def _official_gmp_inspection(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    # [소스확장 2026-07-02] 실사 결과 PDF 를 공식원본으로 노출(설계문서 §11·§15). 라이브
    # 수집기는 source_url=download_url 이나, download_url raw 키도 폴백에 넣어(belt-and-
    # suspenders) 결과문서 누락을 막는다(픽스처엔 둘 다 부재 → official="" 유지, golden 불변).
    return _first(row.get("source_url"), raw.get("download_url"), row.get("official_url")), False


def _official_who_inspection(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    # [소스확장 2026-07-02] WHOPIR 결과 PDF(raw.pdf_url)를 공식원본으로 승격 — 종전엔
    # HTML 실사 페이지(official_url)만 노출돼 실제 결과문서가 클릭 불가였다. pdf_url 부재
    # 시 official_url 로 graceful 폴백(who-noc/who-news 는 default 유지 — 변경 없음).
    return _first(raw.get("pdf_url"), row.get("official_url")), False


def _official_fda_483(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    # L1 = 건별 483 PDF(/media/<id>/download). info = OII Reading Room(api_query/source_url).
    return _first(raw.get("pdf_url"), row.get("official_url")), False


def _official_eu_gmp_ncr(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    # L1 = 수집 시점에 아카이브한 공식 Statement PDF(Supabase Storage 공개 URL). drilldown/
    # PDF endpoint 는 세션상태 의존이라 원본 URL 을 저장하면 죽은 링크가 되므로, 우리가 받아둔
    # 공개 PDF 를 공식원본으로 노출한다. 아카이브 실패(자격증명 부재·업로드 오류) 시에만
    # EudraGMDP GMP 비준수 검색 페이지로 정직하게 L2 폴백(⚠️·목록).
    pdf = _first(raw.get("pdf_archived_url"), row.get("official_url"))
    if pdf:
        return pdf, False
    return _first(raw.get("eudragmdp_search_url"),
                  "https://eudragmdp.ema.europa.eu/inspections/gmpc/"
                  "searchGMPNonCompliance.do"), True


def _official_mhra_gmp_ncr(row: dict[str, Any], raw: dict[str, Any]) -> tuple[str, bool]:
    # L1 = MHRA GMDP 등록부 상세 페이지(성명서 원문). EU NCR 과 달리 상세 페이지가 세션
    # 독립 서버렌더라 그 URL 자체가 영속 → PDF 아카이브 없이 detail URL 을 공식원본으로
    # 직접 노출한다(폴백 불요). 상세 URL 부재(비정상) 시에만 비준수 검색 필터로 L2 폴백(⚠️).
    detail = _first(raw.get("mhra_detail_url"), row.get("official_url"))
    if detail:
        return detail, False
    return _first(raw.get("mhra_search_url"),
                  "https://cms.mhra.gov.uk/mhra/gmp"), True


def _dual_links(kind: str, row: dict[str, Any], raw: dict[str, Any] | None) -> tuple[str, str, bool]:
    """반환 (info_url=📰, official_url=📎, official_is_fallback). `SourceSpec.official` 디스패치.

    info(📰)는 공통 산출. official(📎)은 spec.official 콜러블, 없으면 official_url 폴백(§5/§8/§12B).
    """
    raw = raw or {}
    info = _first(row.get("api_query"), row.get("source_url"), row.get("official_url"))
    fn = _spec(kind).official
    if fn:
        official, fallback = fn(row, raw)
    else:  # FR/EMA/MHRA/PIC/S/ECA/WHO(noc·news)/HC/ICH 등 — official_url 실존 시만
        official, fallback = _first(row.get("official_url")), False
    return info, official, fallback


def _official_label(official_url: str, fallback: bool) -> str:
    """footer 공식원본 라벨. L2 fallback 은 사용자가 목록/데이터셋임을 즉시 알 수 있게 표기."""
    if not fallback:
        return "공식원본"
    if "data.go.kr" in official_url or "api.fda.gov" in official_url:
        return "공식원본(데이터셋)"
    return "공식원본(목록)"


# ─────────────────────────────────────────────────────────────────────────────
# 9. 섹션 분류 (§7)
# ─────────────────────────────────────────────────────────────────────────────
def _section_ich(row: dict[str, Any]) -> str:
    """ICH 섹션(§7): 의견수렴(consultation) → watch, 그 외 → global."""
    return "watch" if "consultation" in (row.get("type_or_class", "") or "").lower() else "global"


def resolve_section(kind: str, row: dict[str, Any]) -> str:
    """카드 섹션(§7). SourceSpec.section override → 없으면 source 기본(MFDS→domestic·else global)."""
    sec = _spec(kind).section
    if callable(sec):
        return sec(row)
    if sec:
        return sec
    if row.get("source") == SOURCE_MFDS:
        return "domestic"
    return "global"


# ─────────────────────────────────────────────────────────────────────────────
# 10. W2 메타표 (§3 + §12(B) + §13.1-3: 발행일·문서번호·유형별 2행 = 4행)
# ─────────────────────────────────────────────────────────────────────────────
# [어휘 분리 2026-07-20] 값이 비었을 때 카드에 찍는 표기.
# 우리가 확보하지 못한 것과 원문에 실제로 없는 것은 **다른 사실**이다. 코드는 원문을 필드
# 단위로 확인하지 않으므로 후자를 주장할 자격이 없다 — 그래서 표기는 항상 우리 상태만 말한다.
# (종전 값 "원문 미기재" 는 원문에 대한 단정이라 실제로 거짓을 발행했다: Health Canada 회수
#  6건이 "업체: 원문 미기재" 로 나갔는데 원문에는 Apotex Inc. 등 업체명이 명시돼 있었다.)
VALUE_UNKNOWN = "미확인"


def _doc_number(kind: str, row: dict[str, Any]) -> str:
    """문서번호 행 값(§13.1-3): MARCS·admin-seq·FR docket 등 식별자."""
    return _code(row.get("document_id", "")) if row.get("document_id") else VALUE_UNKNOWN


# per-kind W2 유형별 사실 행(발행일·문서번호 이후) 생성기 — SourceSpec.extra_rows 로 배선.
# 각기 (row, raw) → 추가 행 리스트. 미지정 kind(guidance/FR·rss-news·mfds-notice·
# safety-letter·legislative·regulation)는 extra_rows=None → dispatcher 가 _w2_extra_default.
def _w2_extra_wl(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    # §12(B): Site Country·issue_date·CFR 조항 없음 → letter_date, 조항행 생략
    rows = [("업체/제조소", _first(raw.get("firm"), row.get("firm")) or VALUE_UNKNOWN)]
    ld = _first(raw.get("letter_date"), raw.get("posted_date"))
    if ld:
        rows.append(("발행 부서/일자", _first(raw.get("issuing_office")) + (f" · {ld}" if ld else "")))
    else:
        rows.append(("발행 부서", _first(raw.get("issuing_office")) or VALUE_UNKNOWN))
    return rows


def _w2_extra_admin(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    firm = _first(raw.get("firm"), row.get("firm"))
    sc = row.get("site_country", "")
    rows = [("업체", firm + (f" ({sc})" if sc else "") or VALUE_UNKNOWN)]
    if raw.get("ADM_DISPS_NAME"):
        rows.append(("처분", raw["ADM_DISPS_NAME"]))
    elif raw.get("ITEM_NAME"):
        rows.append(("품목/공정", raw["ITEM_NAME"]))
    return rows


def _w2_extra_recall_quality(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    rows = [("업체", _first(raw.get("ENTRPS"), row.get("firm")) or VALUE_UNKNOWN)]
    if raw.get("PRDUCT"):  # §12(B): product→PRDUCT, class 없음
        rows.append(("제품", raw["PRDUCT"]))
    return rows


def _w2_extra_gmp_inspection(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    rows = [("제조소", _first(raw.get("manufacturer"), row.get("firm")) or VALUE_UNKNOWN)]
    period = ""
    if raw.get("inspection_start") or raw.get("inspection_end"):
        period = f"{raw.get('inspection_start', '')}~{raw.get('inspection_end', '')}".strip("~")
    if period:
        rows.append(("실사기간", period))
    elif raw.get("product_type"):
        rows.append(("대상 제형", raw["product_type"]))
    return rows


def _w2_extra_gmp_certificate(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    rows = [("업체", _first(raw.get("BSSH_NM"), row.get("firm")) or VALUE_UNKNOWN)]
    if raw.get("KGMP_BGMP_NAME"):
        rows.append(("구분", str(raw["KGMP_BGMP_NAME"])))
    if raw.get("VLD_PRD_YMD"):
        rows.append(("유효기한", str(raw["VLD_PRD_YMD"])))
    return rows


def _w2_extra_openfda_recall(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    rows = [("업체", _first(raw.get("recalling_firm"), row.get("firm")) or VALUE_UNKNOWN)]
    if raw.get("product_description"):
        rows.append(("제품", _truncate_at_sentence(str(raw["product_description"]), 80)))
    if raw.get("classification"):
        rows.append(("Class", str(raw["classification"])))
    return rows


def _w2_extra_hc_recall(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    # Organization 은 HC 부서명("Drugs and health products")이라 회사가 아님 → 사용 금지.
    # 실제 회사는 collect_hc 가 상세 페이지에서 끌어와 firm/company 에 채운다(P8).
    rows = [("업체", _first(raw.get("company"), row.get("firm")) or VALUE_UNKNOWN)]
    product = _first(raw.get("Product"), raw.get("product_description"))
    if product:
        rows.append(("제품", _truncate_at_sentence(str(product), 80)))
    if raw.get("Recall class"):
        rows.append(("Class", str(raw["Recall class"])))
    return rows


def _w2_extra_mhra_recall(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    # MHRA alert 는 구조화 필드가 없어(firm/product 컬럼 부재) category·title 문자열에서
    # 결정론 파싱(§13.1 최소정보 원칙 — 얕은 소스라도 최소 사실은 담는다). 파싱 실패는
    # graceful(발행기관 행으로 폴백). W2 표는 발행일·문서번호 뒤에 최대 3행 더 붙는다.
    rows: list[tuple[str, str]] = []
    cat = str(raw.get("category") or row.get("type_or_class") or "")
    m = re.search(r"Class\s+\d+", cat)
    if m:
        rows.append(("Class", m.group(0)))
    # gov.uk 제목 형식은 "Class N Medicines Recall/Defect …: <업체>, <제품>, <참조>" 로 안정.
    title = str(raw.get("title") or row.get("headline") or "")
    after = title.split(":", 1)[1].strip() if ":" in title else ""
    firm = after.split(",", 1)[0].strip() if after else ""
    if firm:
        rows.append(("업체", _truncate_at_sentence(firm, 80)))
    return rows or [("발행기관", "MHRA")]


def _w2_extra_fda_483(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    # §6: 회사·FEI·Establishment Type·Record Type·실사일(발행일=Publish 은 발행일 행).
    firm = _first(raw.get("firm"), row.get("firm")) or VALUE_UNKNOWN
    fei = raw.get("fei_number", "")
    rows = [("제조소/업체", firm + (f" · FEI {fei}" if fei else ""))]
    meta = " · ".join(p for p in (raw.get("establishment_type", ""),
                                  raw.get("record_type", "")) if p)
    if meta:
        rows.append(("시설 · 유형", meta))
    if raw.get("record_date"):
        rows.append(("실사일", raw["record_date"]))
    return rows


def _w2_extra_eu_gmp_ncr(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    # 발행일·문서번호(=doc_ref) 뒤 최대 3행: 제조소(+소재국)·발행기관(NCA)·제품범위(또는 실사일).
    firm = _first(raw.get("site_name"), row.get("firm")) or VALUE_UNKNOWN
    sc = raw.get("country", "")
    rows = [("제조소/업체", firm + (f" ({sc})" if sc else ""))]
    auth = raw.get("authority_country", "")
    if auth:
        rows.append(("발행기관(NCA)", auth))
    scope = raw.get("product_scope", "")
    if scope:
        rows.append(("제품범위", _truncate_at_sentence(str(scope), 80)))
    elif raw.get("inspection_end_date"):
        rows.append(("실사일", str(raw["inspection_end_date"])))
    return rows


def _w2_extra_mhra_gmp_ncr(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    # 발행일·문서번호(=report_no) 뒤 최대 3행: 제조소(+소재국)·발행기관(MHRA)·제품유형(또는 실사일).
    firm = _first(raw.get("site_name"), row.get("firm")) or VALUE_UNKNOWN
    sc = raw.get("country", "")
    rows = [("제조소/업체", firm + (f" ({sc})" if sc else ""))]
    rows.append(("발행기관", "MHRA (영국)"))
    scope = raw.get("product_scope", "")
    if scope:
        rows.append(("제품유형", _truncate_at_sentence(str(scope), 80)))
    elif raw.get("inspection_end_date"):
        rows.append(("실사일", str(raw["inspection_end_date"])))
    return rows


def _w2_extra_who(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    topic = _first(raw.get("anchor_text"), row.get("headline"))
    if topic:
        rows.append(("주제", _truncate_at_sentence(topic, 80)))
    rows.append(("발행기관", "WHO"))
    return rows


def _w2_extra_ich(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if raw.get("section_title"):
        rows.append(("주제", _truncate_at_sentence(raw["section_title"], 80)))
    rows.append(("발행기관", "ICH"))
    return rows


def _w2_extra_default(row: dict[str, Any], raw: dict[str, Any]) -> list[tuple[str, str]]:
    # guidance(FR)·rss-news·mfds-notice·safety-letter·legislative·regulation
    rows = [("발행기관", _regulator(row.get("source", "")) or VALUE_UNKNOWN)]
    if row.get("comments_close"):
        rows.append(("의견기한", row["comments_close"]))
    else:
        topic = _first(raw.get("title"), row.get("headline"))
        if topic:
            rows.append(("주제", _truncate_at_sentence(topic, 80)))
    return rows


def _w2_rows(kind: str, row: dict[str, Any], raw: dict[str, Any] | None) -> list[tuple[str, str]]:
    """W2 사실표(§3·§12B·§13.1-3): 날짜·문서번호 + 유형별 행(≤5). `SourceSpec.extra_rows` 디스패치.

    첫 행 라벨은 `SourceSpec.date_label`(기본 "발행일"). 수집기가 게시일을 못 얻고 실사일만
    싣는 소스(who-inspection)는 그 이름 그대로 부른다 — 값은 같아도 라벨이 틀리면 오보다.
    """
    raw = raw or {}
    spec = _spec(kind)
    rows: list[tuple[str, str]] = [
        (spec.date_label, row.get("date", "") or VALUE_UNKNOWN),
        ("문서번호", _doc_number(kind, row)),
    ]
    rows += (spec.extra_rows or _w2_extra_default)(row, raw)
    return rows[:5]


# ─────────────────────────────────────────────────────────────────────────────
# 10b. _REGISTRY — kind → SourceSpec 선언 테이블(단일원천). 위(§7~§10)의 per-kind
#      callable 이 모두 정의된 지점에서 조립한다. 신규 소스 추가 절차는 모듈 docstring 참조.
# ─────────────────────────────────────────────────────────────────────────────
_REGISTRY: dict[str, SourceSpec] = {
    # 위치인자 = prefix, label, core_tag (구 `_kind_meta`). 나머지는 kwargs(미지정=기본).
    "warning-letter": SourceSpec(
        "🟧", "Warning Letter", "CGMP",
        category="Warning Letter", deep_body_key="wl_body_full",
        extra_rows=_w2_extra_wl, official=_official_wl,
        detail=_detail_wl_violations),
    "admin-action": SourceSpec(
        "🟦", "행정처분", "행정처분",
        a_eligible=True, deep_body_key="admin_body_full",
        quote=_quote_admin, extra_rows=_w2_extra_admin, official=_official_admin),
    "recall-quality": SourceSpec(
        "🟦", "회수·판매중지", "회수",
        a_eligible=True, section="recall_table",
        quote=_quote_recall_quality, extra_rows=_w2_extra_recall_quality,
        official=_official_recall_quality, detail=_detail_recall_quality),
    "openfda-recall": SourceSpec(
        "🟧", "Recall", "Recall",
        a_eligible=True, section="recall_table",
        quote=_quote_openfda_recall, extra_rows=_w2_extra_openfda_recall,
        official=_official_openfda_recall, detail=_detail_openfda_recall),
    "hc-recall": SourceSpec(
        "🟧", "Recall(HC)", "Recall",
        a_eligible=True, section="recall_table",
        quote=_quote_hc_recall, extra_rows=_w2_extra_hc_recall,
        detail=_detail_hc_recall),
    "mhra-recall": SourceSpec(
        "🟧", "Recall(UK)", "Recall",
        a_eligible=True, section="recall_table",
        quote=_quote_mhra_recall, extra_rows=_w2_extra_mhra_recall),
    "gmp-inspection": SourceSpec(
        "🟦", "GMP실사", "GMP실사",
        a_eligible=True,
        quote=_quote_gmp_inspection, extra_rows=_w2_extra_gmp_inspection,
        official=_official_gmp_inspection, detail=_detail_gmp_deficiencies),
    "gmp-certificate": SourceSpec(
        "🟦", "GMP적합판정", "GMP적합",
        extra_rows=_w2_extra_gmp_certificate),
    "fda-483": SourceSpec(
        "🟧", "FDA 483 실사 관찰", "483",
        deep_body_key="fda483_body_full",
        extra_rows=_w2_extra_fda_483, official=_official_fda_483,
        detail=_detail_fda_483_observations),
    "eu-gmp-ncr": SourceSpec(
        "🟧", "EU GMP 비준수", "GMP 비준수",
        a_eligible=True,
        quote=_quote_eu_gmp_ncr, extra_rows=_w2_extra_eu_gmp_ncr,
        official=_official_eu_gmp_ncr, detail=_detail_eu_gmp_ncr),
    "mhra-gmp-ncr": SourceSpec(
        "🟧", "UK GMP 비준수", "GMP 비준수",
        a_eligible=True,
        quote=_quote_mhra_gmp_ncr, extra_rows=_w2_extra_mhra_gmp_ncr,
        official=_official_mhra_gmp_ncr, detail=_detail_mhra_gmp_ncr),
    "who-noc": SourceSpec(
        "🟧", "WHO", "WHO", normative=True, extra_rows=_w2_extra_who),
    "who-inspection": SourceSpec(
        "🟧", "WHO", "WHO", normative=True,
        # WHOPIR 목록이 주는 날짜는 실사일뿐이다(게시일 미공개) → 라벨도 실사일.
        date_label="실사일",
        extra_rows=_w2_extra_who, official=_official_who_inspection,
        detail=_detail_whopir_report),
    "who-news": SourceSpec(
        "🟫", "WHO", "WHO", normative=True, extra_rows=_w2_extra_who),
    "ich": SourceSpec(
        "🟫", "ICH", "ICH",
        category="Guideline", normative=True, section=_section_ich,
        extra_rows=_w2_extra_ich),
    "guidance": SourceSpec(
        "🟫", "지침·안내서", "Guidance",
        category="Guidance", a_eligible=True, normative=True,
        quote=_quote_guidance),                 # extra_rows=None → _w2_extra_default
    "mfds-notice": SourceSpec(
        "🟫", "지침·안내서", "Guidance",
        category="Guidance", normative=True),
    "regulation": SourceSpec(
        "🟫", "고시·개정법령", "규정",
        category="Guidance", normative=True),
    "legislative": SourceSpec(
        "🟫", "입법예고", "입법예고",
        category="Guidance", normative=True, section="watch"),
    "safety-letter": SourceSpec("🟦", "안전성서한", "안전성"),
    "rss-news": SourceSpec("🟫", "규제 소식", "GMP News", normative=True),
}

# 구 선언 테이블의 파생 재수출(단일원천 = 레지스트리). 외부/테스트 후방호환 유지.
#  _NORMATIVE_KINDS: 규범 문서(제품군 배지 억제) — test_modality_null_for_normative_kinds.
#  _A_ELIGIBLE_KINDS: "A ⟺ 인용 가능" 불변식 문서화(내부 dispatch 는 spec.a_eligible 사용).
#  _CATEGORY_MAP: Notion 카테고리(비 Other 만) — test_no_dead_gmp_guideline_key 등.
_NORMATIVE_KINDS = frozenset(k for k, s in _REGISTRY.items() if s.normative)
_A_ELIGIBLE_KINDS = frozenset(k for k, s in _REGISTRY.items() if s.a_eligible)
_CATEGORY_MAP = {k: s.category for k, s in _REGISTRY.items() if s.category != "Other"}


# ─────────────────────────────────────────────────────────────────────────────
# 11. W1 배지 (§0 + §13.1-2): Evidence · 기관 · Signal · 제품군 · 유형태그 (≤5)
# ─────────────────────────────────────────────────────────────────────────────
def _signal_badge(signal_tier: str) -> str:
    m = {"Tier 3": "Signal High (T3)", "Tier 2": "Signal Med (T2)", "Tier 1": "Signal Low (T1)"}
    return m.get(signal_tier, "Signal Low (T1)")


def _w1_badges(kind: str, evidence: str, row: dict[str, Any], cfg: FixedConfig) -> list[str]:
    badges = [_code(f"Evidence {evidence}"), _code(row.get("source", "") or "?"),
              _code(_signal_badge(row.get("signal_tier", "Tier 1")))]
    modality = row.get("modality", "")
    if modality and not _spec(kind).normative:
        # 배지에는 한글명만(이모지+한글) — §13.1 D5
        badges.append(_code(cfg.modality_badge.get(modality, modality)))
    _, _, core_tag = _kind_meta(kind)
    if core_tag:
        badges.append(_code(core_tag))
    return badges[:5]


# ─────────────────────────────────────────────────────────────────────────────
# 12. recall_group_key (§12(E)) — 산출까지만. card_id 는 그대로 유지.
# ─────────────────────────────────────────────────────────────────────────────
def recall_group_key(row: dict[str, Any], raw: dict[str, Any] | None) -> str:
    """recall 다품목 통합 키(§12(E)). 하나의 실제 회수 사건이 SKU·lot·유통사·함량별
    개별 레코드(MFDS document_id / OpenFDA recall_number)로 쪼개져 들어와도 한 군으로
    묶기 위한 결정론 키. 값이 같으면 `merge_recall_cards()`가 대표 1카드로 접는다.

    - MFDS(recall-quality)       : `MFDS|{ENTRPS}|{RTRVL_RESN}`
    - OpenFDA(openfda-recall)    : 정본 `event_id` 우선 → 부재 시 `RECALL|{recalling_firm}|{reason_for_recall}`

    발행일(`row.date`)은 키에서 **제외**한다 — 같은 사건이 다른 날 재등록·재수집돼도
    (MFDS 대일제약 06-26/06-29, OpenFDA distributor/SKU fan-out 등) 같은 군으로 묶여야
    하기 때문. 종전 키는 `pub` 를 포함해 날짜만 다르면 병합이 갈라졌다.
    소스 접두사로 네임스페이스해 서로 다른 소스가 우연히 같은 firm|reason 를 가져도
    교차 병합되지 않게 한다.
    """
    raw = raw or {}
    source = row.get("source", "")
    if source == SOURCE_MFDS:
        entrps = _first(raw.get("ENTRPS"), row.get("firm"))
        reason = _first(raw.get("RTRVL_RESN"))
        if entrps and reason:
            return f"MFDS|{entrps}|{reason}"
        return ""
    if source == SOURCE_RECALL:
        event_id = _first(raw.get("event_id"))
        if event_id:
            return f"RECALL|event|{event_id}"
        firm = _first(raw.get("recalling_firm"), row.get("firm"))
        reason = _first(raw.get("reason_for_recall"))
        if firm and reason:
            return f"RECALL|{firm}|{reason}"
        return ""
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 13. 제목 (§13.1-1·8): [유형 · 기관] 핵심대상 — **{{TITLE_ISSUE}}** (DocID·소재국·prefix 제거 → W2/배지)
# ─────────────────────────────────────────────────────────────────────────────
# 기관 라벨 — 규제기관 short (제목 §13.1-1). source 기준(MFDS 카드도 소재국 아닌 Source).
_REGULATOR_LABEL = {
    SOURCE_FR: "FDA", SOURCE_RECALL: "FDA", SOURCE_FDA_WL: "FDA",
    SOURCE_EMA: "EMA", SOURCE_MHRA: "MHRA", SOURCE_PICS: "PIC/S",
    SOURCE_ECA: "ECA", SOURCE_MFDS: "MFDS", SOURCE_ICH: "ICH",
    SOURCE_WHO: "WHO", SOURCE_HC: "Health Canada", SOURCE_FDA_483: "FDA",
    SOURCE_ISPE: "ISPE",   # [전문지 브리핑 소스확장 2026-07-13]
    SOURCE_EU_GMP_NCR: "EMA",   # EudraGMDP(EMA 운영 DB) — 발행 NCA 는 W2 발행기관 행에
    SOURCE_MHRA_GMP_NCR: "MHRA",   # MHRA GMDP 등록부 — 발행기관은 항상 MHRA(영국)
}


def _regulator(source: str) -> str:
    return _REGULATOR_LABEL.get(source, source or "")


def _headline_target(row: dict[str, Any]) -> str:
    """제목 핵심대상(업체/제품/문서명, 60자 문장경계 절단) — §13.1-1.

    `_title()`(markdown)과 `CardScaffold.to_web_card()`(JSON)가 **이 단일 헬퍼를 공유**해
    제목과 web-card `headline_target` 이 항상 같은 verbatim 값을 갖게 한다(드리프트 차단, §3.5).
    """
    return _truncate_at_sentence(_first(row.get("firm"), row.get("headline")), 60)


def _title(kind: str, row: dict[str, Any]) -> str:
    """제목(§13.1-1·8 동결): ### [유형 · 기관] 핵심대상 — **{{TITLE_ISSUE}}**.

    제목에서 제거: prefix 색사각형 이모지·소재국·DocID(→ W2 문서번호 행·W1 배지로).
    기관은 Source 기준 규제기관(MFDS 도 소재국 아님). 핵심대상=업체/제품/문서명.
    """
    _, label, _ = _kind_meta(kind)
    org = _regulator(row.get("source", ""))
    target = _headline_target(row)
    return _h3(f"[{label} · {org}] {target} — **{SLOT_TITLE_ISSUE}**")


# ─────────────────────────────────────────────────────────────────────────────
# 14. build_card_scaffold — 메인 (순수 함수)
# ─────────────────────────────────────────────────────────────────────────────
def build_card_scaffold(row: dict[str, Any], raw: dict[str, Any] | None,
                        cfg: FixedConfig = DEFAULT_CONFIG) -> CardScaffold:
    """카드 1장의 결정론 골격을 조립한다(순수 함수 — §12(G)).

    같은 (row, raw, cfg) → 바이트 동일 markdown. 페이지 수준 조립은
    assemble_brief_skeleton() 참조.
    """
    kind = resolve_kind(row)
    evidence = determine_evidence(kind, row, raw)
    language = _language(row, kind)
    section = resolve_section(kind, row)
    modality = row.get("modality", "")
    card_id = f"{row.get('source', '')}::{row.get('document_id', '')}"
    used_slots: list[str] = [SLOT_TITLE_ISSUE, SLOT_W1, SLOT_W5, SLOT_W6, SLOT_W7]

    blocks: list[str] = []
    # 제목
    blocks.append(_title(kind, row))
    # W1 — 한 줄 요약(파랑) + 배지
    badges = " · ".join(_w1_badges(kind, evidence, row, cfg))
    blocks.append(_callout([SLOT_W1, badges], icon="📌", color=cfg.color_w1))
    # W2 — 사실표(무채색, 라벨 이모지 없음)
    blocks.append(_table(_w2_rows(kind, row, raw)))

    # W3/W4 — Evidence A 만. 원문/번역 인터리브(§13.1-4). KO 는 번역 없음.
    if evidence == "A":
        quote = _quote_source(kind, raw)
        if quote:
            qlines = _quote_lines(quote, numbered=True)
            w3: list[str] = ["**원문 및 번역**" if language != "KO" else "**원문**"]
            if language != "KO":
                # 인터리브(§13.1-4): 원문 다음 줄 번역 토큰. 문장 2개면 ①② 1:1(D1).
                multi = len(qlines) > 1
                for i, ln in enumerate(qlines, 1):
                    tok = f"{{{{W4_{i}}}}}" if multi else SLOT_W4
                    w3.append(ln)
                    w3.append(tok)
                    used_slots.append(tok)
            else:
                w3.extend(qlines)  # KO: 한글 원문 quote 그대로(번역 없음)
            blocks.append("\n".join(w3))

    # W5 — 핵심 사실(무채색). 근거 라벨(§13.1-5)
    basis = "근거: Intake raw" if evidence == "A" else "근거: 공식 인덱스 + 보조 출처"
    blocks.append(_callout([f"**핵심 사실**  `{basis}`", SLOT_W5], icon="🔍"))
    # W6 — 시사점(노랑)
    blocks.append(_callout([f"**시사점**", SLOT_W6], icon="💡", color=cfg.color_w6))
    # W7 — 점검 사항(초록)
    blocks.append(_callout([f"**점검 사항**", SLOT_W7], icon="✅", color=cfg.color_w7))
    # W8 — 출처 푸터(회색, 듀얼링크)
    blocks.append(_footer_block(kind, row, raw, cfg))

    markdown = _neutralize_forbidden("\n\n".join(blocks))
    prose_input = _prose_input(kind, row, raw, evidence, modality, language)
    # deep_analysis fan-out 대상 여부 = SourceSpec.deep_body_key 가 있고 그 raw 키가 채워짐.
    # warning-letter=wl_body_full(ENABLE_WL_BODY_FULL) · admin-action=admin_body_full
    # (ENABLE_MFDS_ADMIN_BODY_FULL) · fda-483=fda483_body_full(ENABLE_FDA_483_DEEP). 483 은 결정론
    # 상세(Observation)와 분석층을 함께 갖는 층 혼용 완성형(결정론 경로 불변). 그 외 전 유형·body
    # 미확보 카드는 False(플래그 off 기본 → 픽스처/샘플브리프 키 부재 → golden 불변).
    _raw = raw or {}
    _deep_key = _spec(kind).deep_body_key
    deep_analysis_ready = bool(_deep_key and _raw.get(_deep_key))
    return CardScaffold(
        card_id=card_id, section=section, kind=kind, evidence=evidence,
        modality=modality, signal_tier=row.get("signal_tier", "Tier 1"),
        date=row.get("date", ""), markdown=markdown, prose_input=prose_input,
        recall_group_key=(recall_group_key(row, raw)
                          if kind in ("recall-quality", "openfda-recall") else ""),
        status_hint=row.get("status_hint", ""),
        needs_llm_slots=tuple(used_slots),
        deep_analysis_ready=deep_analysis_ready,
        row=row, raw=(raw or {}),  # to_web_card 가 producer 재사용(직렬화 제외)
    )


def _footer_block(kind: str, row: dict[str, Any], raw: dict[str, Any] | None,
                  cfg: FixedConfig) -> str:
    info, official, fallback = _dual_links(kind, row, raw)
    parts = []
    if info and official and info == official:
        parts.append(f"정보출처/공식원본 [링크]({info})")
    else:
        if info:
            parts.append(f"📰 정보출처 [링크]({info})")
        if official:
            warn = " ⚠️" if fallback else ""
            parts.append(f"📎 {_official_label(official, fallback)} [링크]({official}){warn}")
    if not parts:
        # [어휘 분리 2026-07-20] 문맥에 맞는 우리 상태 표현(§ VALUE_UNKNOWN 과 같은 취지,
        # 문장 구조상 label:value 가 아니라 하드코딩) — 원문에 없다는 단정 금지.
        parts.append("출처 링크 미확인")
    return _callout(["**출처**  " + "   ·   ".join(parts)], icon="🔖", color=cfg.color_footer)


def _prose_input(kind: str, row: dict[str, Any], raw: dict[str, Any] | None,
                 evidence: str, modality: str, language: str) -> dict[str, Any]:
    """§9 — 카드 1장치 최소 컨텍스트(raw 전체 아님). LLM 산문 슬롯 입력.

    공통(w2_facts·quote_lines·issue_or_reason·product·action·deadline·body_excerpt) +
    유형별 텍스트를 실제 raw 키 기준으로 채운다. 300자 truncation 가드 유지.
    """
    raw = raw or {}
    quote = _quote_source(kind, raw)
    # 사유/핵심 텍스트 — 모든 유형의 실제 raw 키 폴백(gmp=attachment_text 누락 버그 수정).
    issue_or_reason = _first(
        raw.get("RTRVL_RESN"), raw.get("reason_for_recall"), raw.get("Issue"),
        raw.get("EXPOSE_CONT"),
        raw.get("attachment_deficiency_excerpt"), raw.get("attachment_text"),
        raw.get("ADM_DISPS_NAME"),
        # WHY-1 #1/#2/#3: WHOPIR PDF·FDA WL 본문·FDA 483 PDF 에서 추출한 결함/위반 excerpt
        # (있으면 우선). 구조화 사유(위) 뒤 · 링크텍스트/표지(subject·anchor_text 등) 앞 —
        # "왜"를 살린다. 세 키는 WHO-inspection/WL/FDA-483 외엔 부재 → 기존 golden _first 불변.
        raw.get("whopir_excerpt"), raw.get("wl_body_excerpt"), raw.get("fda483_excerpt"),
        # [전문지 브리핑 v2 2026-07-13] ECA 기사 본문 excerpt(ENABLE_ECA_ARTICLE_EXCERPT
        # on 시만 부재 → 기존 golden 불변). rss-news 카드의 RSS 요약(description)보다 우선해
        # Routine summary 가 실기사 본문을 근거로 쓰도록 한다.
        raw.get("eca_article_excerpt"),
        # [전문지 브리핑 소스확장 2026-07-13] article_excerpt=비ECA 전문지(ISPE 등) 제네릭 키
        raw.get("article_excerpt"),
        raw.get("abstract"), raw.get("subject"), raw.get("section_title"),
        raw.get("anchor_text"), raw.get("description"),
    )
    return {
        "kind": kind,
        "modality": modality,
        "regulator": row.get("source", ""),
        "evidence": evidence,
        "signal": row.get("signal_tier", ""),
        "language": language,
        "headline": row.get("headline", ""),
        # 공통 확장(P1-2)
        "firm_or_product": _first(raw.get("ENTRPS"), raw.get("recalling_firm"),
                                  raw.get("company"), raw.get("firm"),
                                  raw.get("manufacturer"), row.get("firm")),
        "product": _first(raw.get("PRDUCT"), raw.get("product_description"),
                          raw.get("Product"), raw.get("product_type")),
        "issue_or_reason": _truncate_at_sentence(issue_or_reason, 300),
        "action": _truncate_at_sentence(
            _first(raw.get("ADM_DISPS_NAME"), raw.get("What you should do")), 200),
        "deadline": _first(row.get("comments_close"), raw.get("comments_close_on"),
                           raw.get("edYd")),
        "quote_lines": _split_sentences(quote) if quote else [],
        "w2_facts": {label: value for label, value in _w2_rows(kind, row, raw)},
        "body_excerpt": _truncate_at_sentence(
            _first(raw.get("whopir_excerpt"), raw.get("wl_body_excerpt"),
                   raw.get("fda483_excerpt"), raw.get("eca_article_excerpt"),
                   # [전문지 브리핑 소스확장 2026-07-13] article_excerpt=비ECA 전문지(ISPE 등) 제네릭 키
                   raw.get("article_excerpt"),
                   raw.get("description"), raw.get("summary"), row.get("body")), 300),
        # [정직성 신호 2026-07-20] 이 카드에 대해 **원문 본문을 실제로 확보했는지**를 LLM 에게
        # 명시적으로 알린다. 종전엔 LLM 이 자기가 받은 300자 입력만 보고 원문의 존재 여부를
        # 추측해야 했고, 그 추측이 틀려 "세부 위반내용은 원문에 명시되지 않았다"는 거짓 요약이
        # 나갔다(WL 2건·행정처분 1건·GMP실사 1건 실측). 확보한 원문 전문은 300자 입력에
        # 담기지 않으므로 **입력 길이로는 판별이 불가능**하다 — 그래서 별도 신호가 필요하다.
        # 프롬프트 규약: 이 값과 무관하게 "원문에 없다"는 서술 자체를 금지하고, 입력이 얇으면
        # "원문 확인이 필요하다"로 쓴다. 조립 게이트(`lint_false_absence_claims`)가 강제한다.
        "source_body_captured": _has_source_body(raw),
        "source_body_absent_reason": _absent_reason(raw) if not _has_source_body(raw) else "",
    }


# 원문 본문 확보를 뜻하는 raw 키 — 소스별 이름이 다르므로 한곳에 모은다. 새 소스에서 본문을
# 확보하는 키를 추가하면 여기에도 넣어야 한다(안 넣으면 그 소스만 신호가 꺼져 있다).
#
# ★[2026-08-12] 그 경고가 그대로 현실이 됐다 — 나중에 붙은 소스 3종(EU/영국 GMP NCR·
# WHOPIR 구조화 보고서)의 본문 키가 목록에 없어 **그 소스들만 신호가 꺼져 있었다**.
# 증상이 고약한 이유: 이 카드들은 `deterministic_detail` 에 위반내용·당국조치 **전문을
# verbatim 으로 싣고 있는데**, LLM 에게 가는 `source_body_captured` 는 False 라
# "원문을 못 받았다"로 읽힌다 — 이 신호를 만든 2026-07-20 사고(LLM 이 원문 존재를
# 추측해 "원문에 명시되지 않았다"는 거짓 요약을 냄)와 정확히 같은 조건이다.
# 라이브 실측(발행분 8호): whopir_report 11/11 · eu_gmp_ncr_statement 10/10 ·
# mhra_gmp_ncr_statement 1/1 이 전부 신호 부재.
#
# 재발 방지는 골든 쌍(`tests/golden/*.input.json` ↔ `*.expected.webcard.json`)으로
# 잠갔다 — **결정론 상세를 실은 카드는 반드시 source_body_captured 가 True** 라는
# 불변식을 실데이터로 검사한다(손목록을 손목록으로 검사하지 않는다).
_SOURCE_BODY_KEYS = (
    "wl_body_excerpt", "wl_body_full", "wl_violations",
    "fda483_excerpt", "fda483_body_full", "fda_483_observations",
    "whopir_excerpt", "gmp_deficiencies", "attachment_deficiency_excerpt",
    "admin_body_full", "eca_article_excerpt", "article_excerpt",
    # WHO WHOPIR 구조화 보고서(결론·항목별 요약) — 구 `whopir_excerpt` 의 후속 경로.
    "whopir_report",
    # EU(EudraGMDP)·영국(MHRA) GMP 비준수 성명서 전문. nature=위반내용, action=당국조치가
    # 본체이고 operations/additional 은 함께 실리는 원문 구간이다.
    "ncr_nature", "ncr_action", "ncr_operations", "ncr_additional",
    # ★[회수 계열 2026-08-25] 회수 4종은 **Evidence A(= 원문 인용 가능)** 인데 이 신호는
    # 줄곧 False 였다(발행분 114장 전건). 즉 카드는 원문을 인용해 싣고 있는데 LLM 입력은
    # "원문을 못 받았다"고 말하는, 2026-07-20 사고와 같은 조건이 회수 계열에 그대로 남아
    # 있었다. 이 유형들은 원천 레코드의 사유 필드가 곧 본문이다(별도 장문 본문이 없다).
    #   openfda-recall  : reason_for_recall  (FDA enforcement 사유 원문)
    #   recall-quality  : RTRVL_RESN         (식약처 회수사유내용)
    #   hc-recall       : Issue / What you should do (HC 사유·권고조치 원문)
    "reason_for_recall", "RTRVL_RESN", "Issue", "What you should do",
)


def _has_source_body(raw: dict[str, Any]) -> bool:
    """이 카드가 원문 본문(발췌·전문·구조화 상세 중 하나)을 확보했는가."""
    return any(raw.get(k) for k in _SOURCE_BODY_KEYS)


# [결손 사유 2026-07-20] 수집기가 남긴 상태 코드 → 사람이 읽는 사유. 코드에 `:` 뒤 상세가
# 붙으면 앞부분만 본다.
_ABSENT_REASON_LABELS = {
    "scan-no-text": "원문 PDF 에 텍스트층이 없음(스캔본)",
    # [스캔 483 OCR 2026-07-27] OCR 폴백이 도입되면서 "못 읽었다"의 사유가 갈린다 —
    # 엔진이 없어서인지, 엔진이 돌았는데 글자를 못 읽었는지. 둘 다 **우리 쪽 결손**이고
    # 원문(스캔 PDF)은 공개돼 있다는 사실이 문구에서 지워지지 않게 표현한다.
    "scan-ocr-unavailable": "원문이 스캔 이미지인데 OCR 엔진을 쓸 수 없었음",
    "scan-ocr-empty": "원문이 스캔 이미지이고 OCR 이 글자를 읽지 못함",
    "scan-ocr-budget": "원문이 스캔 이미지인데 이번 실행의 OCR 예산이 소진됨",
    "pdf-encrypted": "원문 PDF 가 암호화돼 열 수 없음",
    "no-anchor": "원문에서 본문 시작점을 찾지 못함",
    "no-excerpt": "원문에서 해당 구간을 찾지 못함",
    "fetch-403": "원문 페이지 접근이 차단됨(403)",
    "fetch-fail": "원문을 받아오지 못함",
    "engine-missing": "PDF 텍스트 추출 엔진 없음",
    "not-attempted": "본문 수집을 시도하지 않음",
}
_ABSENT_STATUS_KEYS = ("fda483_text_status", "wl_body_status")


def _absent_reason(raw: dict[str, Any]) -> str:
    """원문 본문이 비었을 때, **왜** 비었는지 사람이 읽는 사유(§9 `source_body_absent_reason`).

    수집기는 본문이 왜 비었는지 안다(스캔본이라 텍스트가 없다·403 차단·엔진 없음 등) —
    하지만 그 사유가 하류(카드·LLM)로 전달되지 않았고, 그 결과 코드와 LLM 이 이유를 지어냈다
    (디제스트가 근거 없이 '스캔·비공개'라고 단정한 사례). `_ABSENT_STATUS_KEYS` 를 순서대로
    보고 첫 비어있지 않은 값을 찾아 `:` 앞 토큰으로 라벨을 조회한다. 매핑에 없는 코드거나
    상태 키가 아예 없으면 "" — `pdf-ok` 처럼 성공을 뜻하는 코드는 매핑에 없으므로 자연히
    "" 가 된다(이 함수는 `_has_source_body(raw)` 가 False 일 때만 호출되지만, 방어적으로
    성공 코드가 와도 안전하다).
    """
    for key in _ABSENT_STATUS_KEYS:
        status = raw.get(key)
        if status:
            token = str(status).split(":", 1)[0]
            return _ABSENT_REASON_LABELS.get(token, "")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 14b. merge_recall_cards — recall 다품목 1카드 병합 렌더 (card_spec §14, K3 G1)
# ─────────────────────────────────────────────────────────────────────────────
def _merged_target_value(entrps: str, rep_product: str, n: int) -> str:
    """병합 카드 핵심대상값 `{ENTRPS} {대표 PRDUCT} 외 N품목`(60자 문장경계 절단, §14 D).

    제목(markdown `_merge_title_target`)과 web-card `headline_target`(`to_web_card`)이
    이 단일 헬퍼를 공유 → 두 표현이 항상 같은 값(드리프트 차단).
    """
    base = " ".join(p for p in (entrps, rep_product) if p)
    return _truncate_at_sentence(f"{base} 외 {n}품목".strip(), 60)


def _merged_product_value(rep_product: str, n: int) -> str:
    """병합 카드 제품행값 `{대표 PRDUCT} 외 N품목`(§14 D) — W2 markdown·facts JSON 공유."""
    return f"{rep_product} 외 {n}품목" if rep_product else f"외 {n}품목"


def _merge_title_target(title_line: str, entrps: str, rep_product: str, n: int) -> str:
    """제목 §14(D): 핵심대상 → `{ENTRPS} {대표 PRDUCT} 외 N품목`(60자 문장경계 절단).

    제목 라인 형식(§13.1-1): `### [유형 · 기관] {핵심대상} — **{{TITLE_ISSUE}}**`.
    `[...] ` 머리와 ` — **...**` 꼬리는 보존하고 가운데 핵심대상만 교체(결정론).
    """
    head, sep, rest = title_line.partition("] ")
    _old_target, dash, tail = rest.partition(" — ")
    new_target = _merged_target_value(entrps, rep_product, n)
    return f"{head}{sep}{new_target}{dash}{tail}"


def _merge_w2_product(table_block: str, rep_product: str, n: int) -> str:
    """W2 §14(D): `제품` 행 값을 `{대표 PRDUCT} 외 N품목` 으로 교체(없으면 행 추가)."""
    val = _merged_product_value(rep_product, n)
    new_row = f"<tr><td>**제품**</td><td>{val}</td></tr>"
    lines = table_block.split("\n")
    out: list[str] = []
    replaced = False
    for ln in lines:
        if ln.startswith("<tr><td>**제품**</td>"):
            out.append(new_row)
            replaced = True
        elif ln == "</table>" and not replaced:
            out.append(new_row)         # 제품 행 부재 시 표 끝에 추가
            out.append(ln)
            replaced = True
        else:
            out.append(ln)
    return "\n".join(out)


def _merged_product_field(items: list[str], rep_product: str, n: int) -> str:
    """§14(E) 병합 prose_input.product — **최종 문자열**에 300자 가드 재적용(Codex R1-b).

    품목 전체 나열이 300자 이하면 그대로, 초과하면 `{대표 PRDUCT} 외 N품목` 축약.
    축약 결과(대표 품목명 자체가 길 때)도 300자를 넘으면 299자+'…'(=300)로 강제 절단해
    어떤 경우에도 ≤300자를 보장한다.
    """
    joined = ", ".join(it for it in items if it)
    candidate = joined if len(joined) <= 300 else f"{rep_product} 외 {n}품목"
    if len(candidate) > 300:
        candidate = candidate[:299].rstrip() + "…"
    return candidate


def _merge_items_toggle(items: list[str], total: int) -> str:
    """§14(D): W2 직후 toggle `전체 품목 (N+1)` 에 품목명 bullet 나열(v15.8 <details> 양식)."""
    bullets = "\n".join(f"- {it}" for it in items if it)
    return f"<details>\n<summary>전체 품목 ({total})</summary>\n{bullets}\n</details>"


def _render_merged_recall(rep_markdown: str, entrps: str, rep_product: str,
                          items: list[str], n: int, total: int) -> str:
    """대표 카드 markdown 을 병합 렌더로 변형(§14 D). W3/W5/W6/W7/W8 은 대표 그대로.

    C2: toggle 표기 수(total)는 호출부가 비공란 품목 수로 산출해 넘긴다 —
    종전 n+1(=멤버수)은 빈 PRDUCT 멤버 시 불릿 수(빈 항목 제외)와 불일치.
    """
    blocks = rep_markdown.split("\n\n")
    blocks[0] = _merge_title_target(blocks[0], entrps, rep_product, n)
    for i, blk in enumerate(blocks):
        if blk.startswith("<table>"):
            blocks[i] = _merge_w2_product(blk, rep_product, n)
            blocks.insert(i + 1, _merge_items_toggle(items, total))
            break
    return _neutralize_forbidden("\n\n".join(blocks))


def merge_recall_cards(cards: list[CardScaffold]) -> list[CardScaffold]:
    """recall 다품목을 1카드로 접는다(card_spec §14, 순수 함수 — 입력 순서·길이 보존).

    적용 범위(§14A): `kind in {recall-quality, openfda-recall}` & 비어있지 않은
    `recall_group_key` 동일군, 멤버 2건 이상. 대표(§14C) = 그룹 내 `card_id` 사전식
    오름차순 첫 카드. (그룹키는 소스로 네임스페이스돼 MFDS/OpenFDA 교차 병합 없음.)
    대표 = 병합 markdown + 통합 prose_input(§14E). 멤버 = `merged_into`=대표 card_id 마킹
    (렌더 제외·Status 유지). 빈 키·단독 멤버·이종 사유(다른 키)는 무변화.
    `build_card_scaffold()` 결과를 받아 `assemble_brief_skeleton()`/직렬화 직전에 적용.
    """
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(cards):
        if c.kind in ("recall-quality", "openfda-recall") and c.recall_group_key:
            groups.setdefault(c.recall_group_key, []).append(i)

    out = list(cards)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue  # 단독 멤버는 병합 금지(§14A)
        members = sorted(idxs, key=lambda i: cards[i].card_id)  # 대표 = card_id 오름차순 첫
        rep_idx = members[0]
        rep = cards[rep_idx]
        items = [cards[i].prose_input.get("product", "") for i in members]
        # C2: 표시 수는 전부 비공란 품목 수에서 일원 파생 — 종전 멤버수 기반은
        # 빈 PRDUCT 멤버 시 "전체 품목 (3)" vs 불릿 2개 식의 불일치를 만들었다.
        named = [it for it in items if it]
        rep_product = rep.prose_input.get("product", "")
        n = len(named) - 1 if rep_product else len(named)   # 제목/W2 의 "외 N품목"
        entrps = rep.prose_input.get("firm_or_product", "")
        merged_md = _render_merged_recall(rep.markdown, entrps, rep_product,
                                          named, n, len(named))
        new_prose = dict(rep.prose_input)
        new_prose["product"] = _merged_product_field(named, rep_product, n)
        new_prose["merged_count"] = len(named)
        # web-card(§3.7) 도 같은 결정론 값을 쓰도록 대표 scaffold 에 병합 메타를 싣는다
        # (markdown 의 제목/제품행과 동일 헬퍼 산출 → 드리프트 0). to_dict() 미직렬화.
        out[rep_idx] = replace(
            rep, markdown=merged_md, prose_input=new_prose,
            merged_count=len(named), merged_items=tuple(named),
            merged_target=_merged_target_value(entrps, rep_product, n),
            merged_product=_merged_product_value(rep_product, n))
        for i in members[1:]:
            out[i] = replace(cards[i], merged_into=rep.card_id)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 14c. dedupe_news_cards — 동일 뉴스 기사 중복 제거 (§14A 확장)
# ─────────────────────────────────────────────────────────────────────────────
def _news_dedup_key(headline: str) -> str:
    """뉴스 중복 판정용 정규화 제목 — 공백 축약 + 소문자. 문장부호는 보존."""
    return " ".join((headline or "").split()).lower()


def dedupe_news_cards(cards: list[CardScaffold]) -> list[CardScaffold]:
    """동일 기사가 문서ID·발행일만 다르게 2회 수집된 rss-news 중복을 접는다(순수 함수).

    RSS 피드는 같은 기사를 여러 날 재노출할 수 있고, `_stable_doc_id` 가 date_iso 를
    포함해 doc_id 가 갈리면 `source::document_id` 수집 dedup 을 우회한다(예: ECA
    "Should TGA publish GMP Certificates?" 07-01/06-29 2건). 여기서 같은 (source·정규화
    제목) 카드는 대표 1장만 남기고 나머지를 `merged_into` 로 마킹한다 — 렌더 제외·Status
    유지(회수 병합과 동일 규약). 대표 = `card_id` 사전식 첫 카드. 회수 유형은 대상 아님
    (`merge_recall_cards` 가 별도 처리). 이미 병합된 멤버(`merged_into`)도 건너뛴다.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cards):
        if c.merged_into or c.kind != "rss-news":
            continue
        headline = c.prose_input.get("headline", "")
        key = _news_dedup_key(headline)
        if not key:
            continue
        groups.setdefault((c.prose_input.get("regulator", ""), key), []).append(i)

    out = list(cards)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        members = sorted(idxs, key=lambda i: cards[i].card_id)
        rep = cards[members[0]]
        for i in members[1:]:
            out[i] = replace(cards[i], merged_into=rep.card_id)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 15. assemble_brief_skeleton — 페이지 수준(목차·섹션·그룹핑·면책). 별도 순수 함수.
# ─────────────────────────────────────────────────────────────────────────────
_TIER_ORDER = {"Tier 3": 0, "Tier 2": 1, "Tier 1": 2}
_SECTION_ORDER = ["global", "domestic", "watch", "recall_table"]


def _sort_key(c: CardScaffold) -> tuple[int, tuple[int, ...]]:
    # Signal Tier 3→2→1, 동급 발행일 desc (§7)
    return (_TIER_ORDER.get(c.signal_tier, 9), _neg_date(c.date))


def _neg_date(d: str) -> tuple[int, ...]:
    # desc 정렬용 — 큰 날짜가 먼저(ascending 정렬에 끼우는 역순 키).
    # 종전 chr(255-ord) 문자열 키는 비ASCII date(한글 등, ord>255)에서 chr(음수)
    # ValueError 로 _sort_key→assemble_brief_skeleton 전체를 중단시켰다(C1).
    # -ord 정수 튜플은 ASCII 에서 종전과 비교 순서 동치(둘 다 ord 의 강감소 사상,
    # prefix 단축 비교도 동일)이고 전 유니코드에서 안전. 빈 date 의 (0,) 은
    # 모든 실제 키(-ord<0 시작)보다 뒤 — 종전 "\xff" 와 동일하게 최후순.
    return tuple(-ord(ch) for ch in d) if d else (0,)


def _ordered_cards_with_groups(
        cards: list[CardScaffold],
        cfg: FixedConfig = DEFAULT_CONFIG) -> list[tuple[CardScaffold, str]]:
    """§7 정렬·그룹핑을 페이지 전역 순서의 `(card, group_label)` 시퀀스로 산출.

    **단일 진실원**: `assemble_brief_skeleton()`(렌더)과 `compute_render_plan()`(A안
    render_order/group_label, Codex R1-d)이 이 함수를 공유한다(정렬 로직 중복 금지).
    순서: 섹션 global→domestic→watch→recall_table · Tier 3→2→1 · 동급 발행일 desc.
    `group_label` = 글로벌 ≥임계 시 제품군 소제목, 그 외 "". 병합 멤버(merged_into) 제외.
    """
    visible = [c for c in cards if not c.merged_into]
    seq: list[tuple[CardScaffold, str]] = []
    for sec in _SECTION_ORDER:
        sec_cards = sorted([c for c in visible if c.section == sec], key=_sort_key)
        if not sec_cards:
            continue
        if sec == "global" and len(sec_cards) >= cfg.grouping_threshold:
            for mod in ("Chemical", "Biologic", "Other"):
                label = cfg.modality_badge.get(mod, mod)
                seq.extend((c, label) for c in sec_cards if (c.modality or "Other") == mod)
        else:
            seq.extend((c, "") for c in sec_cards)
    return seq


def compute_render_plan(cards: list[CardScaffold],
                        cfg: FixedConfig = DEFAULT_CONFIG) -> dict[str, dict[str, Any]]:
    """A안(Codex R1-d): `{card_id: {render_order:int, group_label:str}}`.

    `assemble_brief_skeleton()` 과 동일 순서(`_ordered_cards_with_groups` 공유)이므로
    Routine 은 §7 정렬·그룹핑을 재현하지 않고 render_order 순 나열 + section 전환 H2 +
    group_label 전환 H3 만 한다. 병합 멤버는 시퀀스에서 빠지므로 부여되지 않는다.
    """
    return {c.card_id: {"render_order": i, "group_label": label}
            for i, (c, label) in enumerate(_ordered_cards_with_groups(cards, cfg))}


def assemble_brief_skeleton(cards: list[CardScaffold],
                            cfg: FixedConfig = DEFAULT_CONFIG) -> str:
    """카드들을 페이지 골격(목차·섹션 H2·§7 그룹핑/정렬·면책 푸터)으로 조립.

    순수 함수. build_card_scaffold() 결과 리스트를 받아 페이지 마크다운 1개를 만든다.
    카드 1장 조립과 분리(단계 D/K3 재사용 단위가 다름). 정렬·그룹핑은
    `_ordered_cards_with_groups()` 를 `compute_render_plan()` 과 공유(R1-d 순서 일치).
    """
    out: list[str] = ["<table_of_contents/>"]
    cur_section: str | None = None
    cur_label: str | None = None
    for card, label in _ordered_cards_with_groups(cards, cfg):
        if card.section != cur_section:
            out.append(f"## {cfg.section_titles.get(card.section, card.section)}")
            cur_section = card.section
            cur_label = None  # 섹션 전환 시 그룹 소제목 리셋
        if label and label != cur_label:
            out.append(f"### {label}")
            cur_label = label
        out.append(card.markdown)
    # 면책 푸터(§13.1-11) — 페이지 끝
    out.append("---")
    disc = list(cfg.disclaimer_ko) + [cfg.disclaimer_en]
    out.append(_callout(disc, icon="ℹ️", color=cfg.color_footer))
    return _neutralize_forbidden("\n\n".join(out))


# ─────────────────────────────────────────────────────────────────────────────
# 16. web-card 직렬화 (grm-web-card/v1, P1) — markdown 표현 틀과 분리된 JSON 계약.
#     사실 셀은 §1~§15 의 결정론 producer 를 재사용(재계산 0). 산문만 LLM 슬롯.
# ─────────────────────────────────────────────────────────────────────────────
WEB_SCHEMA_VERSION = "grm-web-card/v1"

# section → web group 라벨(§3.1). group enum = {글로벌, 국내, Recall} 뿐.
# watch 는 v1 카드 아님(§3.3) → 매핑 없음. assemble_web_brief 가 watch 를 직렬화 전에
# 제외하므로 to_web_card 는 watch 카드로 호출되지 않는다(호출 측 전제). 따라서 enum 밖
# 값을 낼 경로 없음 — watch 카드의 per-card web 골든도 동결하지 않는다(WEBCARD_FIXTURES 제외).
_WEB_GROUP = {"global": "글로벌", "domestic": "국내", "recall_table": "Recall"}

# Notion 발행 카테고리 멀티셀렉트(§3.4)는 이제 SourceSpec.category 로 단일화 —
# `_CATEGORY_MAP`(비 Other 파생 재수출)는 §10b 레지스트리 직후에 생성한다. 키 = `resolve_kind`
# 가 내는 **내부 kind**(raw Type 명 아님), 미매핑(recall·admin·gmp·safety·who·hc·rss·483 등)=Other.
#
# §3.4 의 `gmp-guideline → Guideline` 은 raw Type 명을 혼용 표기한 것이며 레지스트리에 키를
# 두지 않는다(죽은 매핑 금지): `TYPE_GMP_GUIDELINE="gmp-guideline"` 은 collect_mfds.py 에
# 정의만 있고 어느 수집기도 row 에 할당하지 않는 휴면 상수 → 내부 kind `"gmp-guideline"` 은
# 발현 불가. MFDS gmp-guideline Type 이 인입되면 MFDS else 분기 → kind `mfds-notice`
# → 카테고리 **"Guidance"**(Other 로 새지 않음; 가드 테스트로 고정).

# web-card JSON 값에 들어가면 안 되는 표현 틀 토큰(불변식 #6) — 렌더러가 그림.
# modality/group_label 의 이모지(💊/🧬/▫️)는 스키마 데이터값이라 허용(여기 목록 밖).
_CARD_MARKUP_TOKENS = ("<callout", "<table", "<tr", "<td", "<details", "<summary",
                       "### ", "`", "{{")


def _category(kind: str) -> str:
    """Notion 발행 카테고리(§3.4): Warning Letter / Guidance / Guideline / Other."""
    return _spec(kind).category


def _signal_level(signal_tier: str) -> str:
    """signal_label(§3.1): `_signal_badge` 에서 레벨 단어(High/Med/Low) 추출 — 단일원천.

    `_signal_badge("Tier 3")` = "Signal High (T3)" → split()[1] = "High". 미상 tier 는
    `_signal_badge` 폴백("Signal Low (T1)") → "Low". 별도 매핑표 없이 배지와 항상 일치.
    """
    return _signal_badge(signal_tier).split()[1]


def _signal_tier_num(signal_tier: str) -> int:
    """`"Tier 3"` → 3(§3.1). 미상/결측은 1."""
    parts = (signal_tier or "").split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 1


def _official_is_pdf(url: str) -> bool:
    """공식원본 URL 이 PDF/다운로드 직링크인지(§3.1). 예: `.pdf`·`/media/<id>/download`.

    쿼리/프래그먼트 꼬리(`.pdf?download=1`·`#page=2`)는 제거 후 path 만 검사 — collect_who 의
    WHOPIR PDF 판정과 동일 규칙(§3.1 "기존 PDF 판정" 재사용).
    """
    u = (url or "").lower().split("?", 1)[0].split("#", 1)[0]
    return u.endswith(".pdf") or u.endswith("/download")


def _plain(value: str) -> str:
    """facts 값에서 인라인 코드 백틱 한 겹 제거 → verbatim 값(불변식 #6 JSON 무마크업).

    `_w2_rows` 의 문서번호 행만 `_code()` 로 백틱을 감싸므로 그 한 겹만 벗긴다(나머지 값 불변).
    """
    v = value or ""
    if len(v) >= 2 and v.startswith("`") and v.endswith("`"):
        v = v[1:-1]
    return v


def _apply_merged_product(facts: list[dict[str, Any]], value: str) -> list[dict[str, Any]]:
    """병합 카드 facts 의 `제품` 행 값을 치환(없으면 추가) — `_merge_w2_product` 와 동형(§3.7)."""
    out: list[dict[str, Any]] = []
    replaced = False
    for f in facts:
        if f["label"] == "제품":
            out.append({"label": "제품", "value": value})
            replaced = True
        else:
            out.append(f)
    if not replaced:
        out.append({"label": "제품", "value": value})
    return out


def assert_no_card_markup(card: dict[str, Any]) -> list[str]:
    """web card dict 의 문자열 값에 표현 틀 마크업이 있으면 토큰 목록 반환(없으면 []), 불변식 #6."""
    found: set[str] = set()

    def scan(v: Any) -> None:
        if isinstance(v, str):
            for tok in _CARD_MARKUP_TOKENS:
                if tok in v:
                    found.add(tok)
            if v.startswith("> ") or "\n> " in v:
                found.add("> ")
        elif isinstance(v, dict):
            for x in v.values():
                scan(x)
        elif isinstance(v, list):
            for x in v:
                scan(x)

    scan(card)
    return sorted(found)


def assemble_web_brief(cards: list[CardScaffold], brief_meta: dict[str, Any],
                       cfg: FixedConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    """카드들을 `grm-web-card/v1` 브리프 dict 로 조립(§3.2). 순수·결정론.

    `cards` = `build_card_scaffold()` → `merge_recall_cards()` 결과.
    `brief_meta`(코드 메타) = `run_date_kst`·`window`·`publish_date`·`intake_total`·(선택)`tldr`.
    `compute_render_plan()` 단일원천으로 render_order 순 나열 — 병합 멤버(`merged_into`)와
    watch 섹션(§3.3)은 제외. brief 의 `agencies`/`categories` 는 렌더 카드 등장순 distinct,
    `tldr` 는 LLM placeholder([])이며 면책 정식 문안은 JSON 에 넣지 않는다(렌더러가 보유).
    """
    plan = compute_render_plan(cards, cfg)
    web_cards: list[dict[str, Any]] = []
    for c in cards:
        if c.merged_into or c.section == "watch":
            continue
        entry = plan.get(c.card_id)
        if entry is None:          # 안전망(병합 멤버는 plan 에 없음 — 위에서 이미 제외)
            continue
        web_cards.append(c.to_web_card(entry, cfg))
    web_cards.sort(key=lambda d: d["render_order"])

    agencies: list[str] = []
    categories: list[str] = []
    evidence = {"A": 0, "B": 0, "C": 0}
    for wc in web_cards:
        if wc["agency"] and wc["agency"] not in agencies:
            agencies.append(wc["agency"])
        if wc["category"] and wc["category"] not in categories:
            categories.append(wc["category"])
        evidence[wc["evidence_level"]] = evidence.get(wc["evidence_level"], 0) + 1

    return {
        "schema_version": WEB_SCHEMA_VERSION,
        "brief": {
            "run_date_kst": brief_meta.get("run_date_kst", ""),
            "window": brief_meta.get("window", ""),
            "publish_date": brief_meta.get("publish_date", ""),
            "agencies": agencies,
            "categories": categories,
            "tldr": list(brief_meta.get("tldr", [])),   # LLM placeholder
            "coverage": {
                "intake_total": brief_meta.get("intake_total", len(web_cards)),
                "rendered": len(web_cards),
                "evidence": evidence,
            },
            "ai_disclosure": True,
        },
        "cards": web_cards,
    }
