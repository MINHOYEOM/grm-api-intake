"""GRM Keystone K2 — 결정론적 카드 골격 조립기 (card_spec v16 구현).

`build_card_scaffold(row, raw, cfg)` 는 **순수 함수**다(외부 fetch·현재시각·LLM·Notion
API 호출 없음, card_spec §12(G)). Python 이 카드 뼈대(제목·W1 배지·W2 표·W3 인용·W8
듀얼링크·출력 매트릭스)를 완성하고, LLM 이 채울 산문 6슬롯만 토큰으로 비워둔다:
  {{TITLE_ISSUE}} · {{W1}} · {{W4}}(비KO만) · {{W5}} · {{W6}} · {{W7}}

페이지 수준 조립(목차·섹션 H2·그룹핑/정렬·면책 푸터)은 `assemble_brief_skeleton()` 으로
분리한다(단계 D/K3 재사용 단위가 다름).

마크다운 문법은 **Notion MCP enhanced markdown**(v15.8 카드 표준)만 사용한다:
  <callout icon=".." color="..">, > (원문 인용 전용), <details>(toggle), <table>,
  <table_of_contents/>, ### H3, ---. LV-15.7a 폴백 금지 문법([!WARNING]·[!NOTE]·[TOC]·
  +++·<toggle>)은 절대 쓰지 않는다(golden 에서 부재 assert).

우선순위(지시문): card_spec §12 > §13.1 > §0~§9 > redesign.
"""
from __future__ import annotations

import json
import re
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


# 유형 → (prefix, 한글 라벨, W1 유형 핵심 태그) — §2 고정표
def _kind_meta(kind: str) -> tuple[str, str, str]:
    table = {
        "warning-letter":   ("🟧", "Warning Letter", "CGMP"),
        "recall-quality":   ("🟦", "회수·판매중지", "회수"),
        "openfda-recall":   ("🟧", "Recall", "Recall"),
        "admin-action":     ("🟦", "행정처분", "행정처분"),
        "gmp-inspection":   ("🟦", "GMP실사", "GMP실사"),
        "gmp-certificate":  ("🟦", "GMP적합판정", "GMP적합"),
        "guidance":         ("🟫", "지침·안내서", "Guidance"),
        "mfds-notice":      ("🟫", "지침·안내서", "Guidance"),
        "rss-news":         ("🟫", "규제 소식", "GMP News"),
        "regulation":       ("🟫", "고시·개정법령", "규정"),
        "legislative":      ("🟫", "입법예고", "입법예고"),
        "safety-letter":    ("🟦", "안전성서한", "안전성"),
        "ich":              ("🟫", "ICH", "ICH"),
        "who-noc":          ("🟧", "WHO", "WHO"),
        "who-inspection":   ("🟧", "WHO", "WHO"),
        "who-news":         ("🟫", "WHO", "WHO"),
        "hc-recall":        ("🟧", "Recall(HC)", "Recall"),
        "fda-483":          ("🟧", "FDA 483 실사 관찰", "483"),
    }
    return table.get(kind, ("⬜", kind or "기타", ""))


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
        # [WL 심층분석 fan-out] 대상 카드만 전문(全文)을 별도 명시적 키로 노출 — raw 전체는
        # 여전히 미포함(기존 원칙 불변). 6종 동결 슬롯 Routine 은 이 키를 쓰지 않는다(무관심
        # 필드 — 프롬프트가 참조하지 않으면 컨텍스트에 영향 없음). fan-out 오케스트레이터만 소비.
        if self.deep_analysis_ready:
            d["deep_analysis_ready"] = True
            # WL=wl_body_full, admin-action=admin_body_full(소스확장 2026-07-02). fan-out
            # 오케스트레이터가 이 body_full 만 서브에이전트 컨텍스트로 준다(카드 격리 불변).
            d["deep_analysis_input"] = {
                "body_full": self.raw.get("wl_body_full")
                or self.raw.get("admin_body_full", "")}
        return d

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
                    if (self.modality and kind not in _NORMATIVE_KINDS) else None)
        headline_target = (self.merged_target if (merged and self.merged_target)
                           else _headline_target(row))
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
    if source in (SOURCE_EMA, SOURCE_MHRA, SOURCE_PICS, SOURCE_ECA):
        return "rss-news"             # RSS 요약만 → Evidence B
    return "rss-news"


# 규범 문서(특정 제품군에 매이지 않음) — 제품군 배지 생략 (§4)
_NORMATIVE_KINDS = {
    "guidance", "mfds-notice", "rss-news", "regulation", "legislative",
    "ich", "who-noc", "who-inspection", "who-news",
}

# Evidence A 가능 유형 — 결정론적으로 인용 가능한 공식 raw 필드를 가진 유형만.
# 그 외(RSS·WHO·ICH §12H)는 항상 B. determine_evidence 와 _quote_source 가 함께 보장:
# "A 인데 quote 없음" 조합이 한 유형도 없도록(최종 정합 가드).
_A_ELIGIBLE_KINDS = {
    "admin-action", "recall-quality", "gmp-inspection",
    "openfda-recall", "hc-recall", "guidance",
}


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
    elif kind in _A_ELIGIBLE_KINDS and raw and quote:
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
def _quote_source(kind: str, raw: dict[str, Any] | None) -> str:
    """유형별 W3 인용 소스 필드(§12C). 실제 수집기 raw 키 기준. 없으면 "" → Evidence B.

    A-eligible 만 인용 가능: admin(EXPOSE_CONT)·recall(RTRVL_RESN)·gmp(attachment_text)·
    openfda-recall(reason_for_recall)·hc-recall(Issue)·guidance/FR(abstract). 그 외(RSS·
    WHO·ICH §12H·WL 본문 미수집)는 "".
    """
    if not raw:
        return ""
    if kind == "admin-action":
        return _truncate_at_sentence(raw.get("EXPOSE_CONT", ""), 250)
    if kind == "recall-quality":
        # 형제 분기와 동형으로 250자 절단(A3). RTRVL_RESN(회수사유)은 한국어 장문에
        # 종결부호가 없는 경우가 많아 무절단 시 '>' 인용 라인이 Notion rich-text
        # 한도(2000자)를 초과할 수 있다(300자 prose_input 가드는 렌더 라인 미보호).
        return _truncate_at_sentence(_first(raw.get("RTRVL_RESN")), 250)
    if kind == "gmp-inspection":
        # 표지 너머 결론(지적/보완사항) 우선 — 없으면 전체 본문 폴백(P6).
        return _truncate_at_sentence(
            _first(raw.get("attachment_deficiency_excerpt"),
                   raw.get("attachment_text")), 250)
    if kind == "openfda-recall":
        return _truncate_at_sentence(raw.get("reason_for_recall", ""), 250)
    if kind == "hc-recall":
        return _truncate_at_sentence(_first(raw.get("Issue"), raw.get("What you should do")), 250)
    if kind == "guidance":  # FR 전용 — abstract(없으면 title)
        return _truncate_at_sentence(_first(raw.get("abstract"), raw.get("title")), 250)
    # mfds-notice·rss-news·safety-letter·legislative·regulation·ich·who-*·WL → quote 없음
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 7b. 결정론 상세보기 슬롯 (spec §16, 2026-07-02) — WL deep_analysis(LLM)와 별개 결정론 층
# ─────────────────────────────────────────────────────────────────────────────
_DEFICIENCY_ROW_KEYS = ("area", "severity", "legal_basis", "summary", "followup")
_FDA483_OBSERVATION_ROW_KEYS = ("number", "deficiency", "detail")


def _deterministic_detail(kind: str, row: dict[str, Any],
                          raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """결정론 상세 슬롯(펼침 상세보기용). 없으면 None(요약카드 유지·golden 불변).

    WL `deep_analysis`(LLM 분석층·fan-out·게이트)와 **완전 별개**의 결정론 층 — 생성이 없어
    환각 0, `verify_deep_analysis` 같은 근거대조 게이트 불필요(수집기가 공개 사실을 그대로
    구조화). `type` 분기로 소스별 결정론 detail 확장:
      - `gmp_deficiencies` — gmp-inspection 지적사항 표(`raw.gmp_deficiencies`).
      - `fda_483_observations` — FDA 483 Observation 번호 목록(`raw.fda_483_observations`).
        해당 raw 필드 부재면 None(graceful). 창작 0(DB 필드 무변형).
    """
    raw = raw or {}
    if kind == "gmp-inspection":
        rows = raw.get("gmp_deficiencies")
        if isinstance(rows, list) and rows:
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
    if kind == "fda-483":
        obs = raw.get("fda_483_observations")
        if isinstance(obs, list) and obs:
            norm = [{k: str(o.get(k, "") or "") for k in _FDA483_OBSERVATION_ROW_KEYS}
                    for o in obs if isinstance(o, dict)]
            norm = [o for o in norm if o["deficiency"]]
            if norm:
                return {
                    "type": "fda_483_observations",
                    "count": len(norm),
                    "observations": norm,
                }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 8. W8 듀얼링크 (§5 + §12(B)) — L1 실존할 때만, 패턴 유추 금지
# ─────────────────────────────────────────────────────────────────────────────
def _dual_links(kind: str, row: dict[str, Any], raw: dict[str, Any] | None) -> tuple[str, str, bool]:
    """반환 (info_url=📰, official_url=📎, official_is_fallback). 없으면 ''.

    L1(공식 원본)은 필드에 실제 존재할 때만. 없으면 L2 인덱스(⚠️). (§5/§8/§12B)
    """
    raw = raw or {}
    info = _first(row.get("api_query"), row.get("source_url"), row.get("official_url"))
    official = ""
    fallback = False
    if kind == "warning-letter":
        official = _first(raw.get("url"), row.get("official_url"))
    elif kind == "admin-action":
        seq = _first(raw.get("ADM_DISPS_SEQ"))
        # E2(resolve & verify): 수집기가 ENABLE_MFDS_URL_VERIFY=on 일 때만 남기는
        # `admin_l1_verify`("pass"/"fail")를 존중한다. 키가 없으면(flag off=기본) verify
        # 는 None → 현행 동작(seq→L1 단언) 그대로라 golden 바이트 불변(additive).
        verify = raw.get("admin_l1_verify")
        if verify == "fail":
            # 후보 L1 이 live verify 에서 죽음/오류셸 → 정직하게 L2 인덱스 + ⚠️ 강등.
            official = "https://nedrug.mfds.go.kr/pbp/CCBAO01"
            fallback = True
        elif seq:
            # verify=="pass" → 검증된 L1. None(E2 off) → 현행(미검증 L1 단언, 행위 불변).
            # 라이브 검증 2026-06-16(URL전수검사): 실제 seq(예 2026004188)→ 행정처분정보
            # 레코드 정상 렌더. nedrug getItem 은 무효 seq 도 HTTP 200(error-shell)이라
            # 상태코드로 검증 불가 → E2(본문 길이·오류마커)로만 확정. 잔여 R-1: data.go.kr
            # 15058457 이 ADM_DISPS_SEQ 를 반환하는지 키 보유 CI 확인(증빙 §5.2 URL-1).
            official = ("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?"
                        f"dispsApplySeq={seq}")
        else:
            official = "https://nedrug.mfds.go.kr/pbp/CCBAO01"  # L2 인덱스
            fallback = True
    elif kind == "recall-quality":
        # L2 인덱스(§12B). 라이브 검증 2026-06-16(URL전수검사): 종전 CCBAH01 은 '재평가공고
        # 및 결과공시' 보드(회수와 무관)였음 → 회수·폐기 보드 CCBAI01 로 정정. 건별 L1 은
        # data.go.kr 15059114 payload 에 nedrug 회수레코드 seq(targetItemSeq)가 없어 불가 →
        # 정직하게 L2 인덱스 유지(📰 는 data.go.kr 회수 데이터셋 — 회수 특정).
        official = "https://nedrug.mfds.go.kr/pbp/CCBAI01"
        fallback = True
    elif kind == "openfda-recall":
        # 항목별 L1 없음 → FDA Recalls 인덱스 L2(§5). 패턴 유추 금지.
        official = _first(row.get("official_url"),
                          "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts")
        fallback = not row.get("official_url")
    elif kind == "gmp-inspection":
        # [소스확장 2026-07-02] 실사 결과 PDF 를 공식원본으로 노출(설계문서 §11·§15). 라이브
        # 수집기는 source_url=download_url 이나, download_url raw 키도 폴백에 넣어(belt-and-
        # suspenders) 결과문서 누락을 막는다(픽스처엔 둘 다 부재 → official="" 유지, golden 불변).
        official = _first(row.get("source_url"), raw.get("download_url"), row.get("official_url"))
    elif kind == "who-inspection":
        # [소스확장 2026-07-02] WHOPIR 결과 PDF(raw.pdf_url)를 공식원본으로 승격 — 종전엔
        # HTML 실사 페이지(official_url)만 노출돼 실제 결과문서가 클릭 불가였다. pdf_url 부재
        # 시 official_url 로 graceful 폴백(who-noc/who-news 는 아래 else 유지 — 변경 없음).
        official = _first(raw.get("pdf_url"), row.get("official_url"))
    elif kind == "fda-483":
        # L1 = 건별 483 PDF(/media/<id>/download). info = OII Reading Room(api_query/source_url).
        official = _first(raw.get("pdf_url"), row.get("official_url"))
    else:  # FR/EMA/MHRA/PIC/S/ECA/WHO(noc·news)/HC/ICH 등 RSS·페이지 L1(official_url 실존 시)
        official = _first(row.get("official_url"))
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
def resolve_section(kind: str, row: dict[str, Any]) -> str:
    if kind in ("recall-quality", "openfda-recall", "hc-recall"):
        return "recall_table"
    if kind == "legislative":
        return "watch"
    if kind == "ich" and "consultation" in (row.get("type_or_class", "") or "").lower():
        return "watch"
    if row.get("source") == SOURCE_MFDS:
        return "domestic"
    return "global"


# ─────────────────────────────────────────────────────────────────────────────
# 10. W2 메타표 (§3 + §12(B) + §13.1-3: 발행일·문서번호·유형별 2행 = 4행)
# ─────────────────────────────────────────────────────────────────────────────
def _doc_number(kind: str, row: dict[str, Any]) -> str:
    """문서번호 행 값(§13.1-3): MARCS·admin-seq·FR docket 등 식별자."""
    return _code(row.get("document_id", "")) if row.get("document_id") else "원문 미기재"


def _w2_rows(kind: str, row: dict[str, Any], raw: dict[str, Any] | None) -> list[tuple[str, str]]:
    raw = raw or {}
    rows: list[tuple[str, str]] = [
        ("발행일", row.get("date", "") or "원문 미기재"),
        ("문서번호", _doc_number(kind, row)),
    ]
    if kind == "warning-letter":
        # §12(B): Site Country·issue_date·CFR 조항 없음 → letter_date, 조항행 생략
        rows.append(("업체/제조소", _first(raw.get("firm"), row.get("firm")) or "원문 미기재"))
        ld = _first(raw.get("letter_date"), raw.get("posted_date"))
        if ld:
            rows.append(("발행 부서/일자", _first(raw.get("issuing_office")) + (f" · {ld}" if ld else "")))
        else:
            rows.append(("발행 부서", _first(raw.get("issuing_office")) or "원문 미기재"))
    elif kind == "admin-action":
        firm = _first(raw.get("firm"), row.get("firm"))
        sc = row.get("site_country", "")
        rows.append(("업체", firm + (f" ({sc})" if sc else "") or "원문 미기재"))
        if raw.get("ADM_DISPS_NAME"):
            rows.append(("처분", raw["ADM_DISPS_NAME"]))
        elif raw.get("ITEM_NAME"):
            rows.append(("품목/공정", raw["ITEM_NAME"]))
    elif kind == "recall-quality":
        rows.append(("업체", _first(raw.get("ENTRPS"), row.get("firm")) or "원문 미기재"))
        if raw.get("PRDUCT"):  # §12(B): product→PRDUCT, class 없음
            rows.append(("제품", raw["PRDUCT"]))
    elif kind == "gmp-inspection":
        rows.append(("제조소", _first(raw.get("manufacturer"), row.get("firm")) or "원문 미기재"))
        period = ""
        if raw.get("inspection_start") or raw.get("inspection_end"):
            period = f"{raw.get('inspection_start', '')}~{raw.get('inspection_end', '')}".strip("~")
        if period:
            rows.append(("실사기간", period))
        elif raw.get("product_type"):
            rows.append(("대상 제형", raw["product_type"]))
    elif kind == "gmp-certificate":
        rows.append(("업체", _first(raw.get("BSSH_NM"), row.get("firm")) or "원문 미기재"))
        if raw.get("KGMP_BGMP_NAME"):
            rows.append(("구분", str(raw["KGMP_BGMP_NAME"])))
        if raw.get("VLD_PRD_YMD"):
            rows.append(("유효기한", str(raw["VLD_PRD_YMD"])))
    elif kind == "openfda-recall":
        rows.append(("업체", _first(raw.get("recalling_firm"), row.get("firm")) or "원문 미기재"))
        if raw.get("product_description"):
            rows.append(("제품", _truncate_at_sentence(str(raw["product_description"]), 80)))
        if raw.get("classification"):
            rows.append(("Class", str(raw["classification"])))
    elif kind == "hc-recall":
        # Organization 은 HC 부서명("Drugs and health products")이라 회사가 아님 → 사용 금지.
        # 실제 회사는 collect_hc 가 상세 페이지에서 끌어와 firm/company 에 채운다(P8).
        rows.append(("업체", _first(raw.get("company"), row.get("firm")) or "원문 미기재"))
        product = _first(raw.get("Product"), raw.get("product_description"))
        if product:
            rows.append(("제품", _truncate_at_sentence(str(product), 80)))
        if raw.get("Recall class"):
            rows.append(("Class", str(raw["Recall class"])))
    elif kind == "fda-483":
        # §6: 회사·FEI·Establishment Type·Record Type·실사일(발행일=Publish 은 발행일 행).
        firm = _first(raw.get("firm"), row.get("firm")) or "원문 미기재"
        fei = raw.get("fei_number", "")
        rows.append(("제조소/업체", firm + (f" · FEI {fei}" if fei else "")))
        meta = " · ".join(p for p in (raw.get("establishment_type", ""),
                                      raw.get("record_type", "")) if p)
        if meta:
            rows.append(("시설 · 유형", meta))
        if raw.get("record_date"):
            rows.append(("실사일", raw["record_date"]))
    elif kind in ("who-noc", "who-inspection", "who-news"):
        topic = _first(raw.get("anchor_text"), row.get("headline"))
        if topic:
            rows.append(("주제", _truncate_at_sentence(topic, 80)))
        rows.append(("발행기관", "WHO"))
    elif kind == "ich":
        if raw.get("section_title"):
            rows.append(("주제", _truncate_at_sentence(raw["section_title"], 80)))
        rows.append(("발행기관", "ICH"))
    else:  # guidance(FR)·rss-news·mfds-notice·safety-letter·legislative·regulation
        rows.append(("발행기관", _regulator(row.get("source", "")) or "원문 미기재"))
        if row.get("comments_close"):
            rows.append(("의견기한", row["comments_close"]))
        else:
            topic = _first(raw.get("title"), row.get("headline"))
            if topic:
                rows.append(("주제", _truncate_at_sentence(topic, 80)))
    return rows[:5]


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
    if modality and kind not in _NORMATIVE_KINDS:
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
    raw = raw or {}
    entrps = _first(raw.get("ENTRPS"), row.get("firm"))
    reason = _first(raw.get("RTRVL_RESN"))
    pub = row.get("date", "")
    if entrps and reason and pub:
        return f"{entrps}|{reason}|{pub}"
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
    # [WL 심층분석 fan-out] warning-letter + 전문 확보(raw.wl_body_full, ENABLE_WL_BODY_FULL
    # 게이트 산출) 시만 True. [소스확장 2026-07-02] admin-action + raw.admin_body_full
    # (ENABLE_MFDS_ADMIN_BODY_FULL 게이트 산출) 도 대상. 그 외 전 유형·body 미확보 카드는
    # 항상 False(플래그 off 기본 → 픽스처/샘플브리프 키 부재 → golden 불변).
    _raw = raw or {}
    deep_analysis_ready = bool(
        (kind == "warning-letter" and _raw.get("wl_body_full"))
        or (kind == "admin-action" and _raw.get("admin_body_full")))
    return CardScaffold(
        card_id=card_id, section=section, kind=kind, evidence=evidence,
        modality=modality, signal_tier=row.get("signal_tier", "Tier 1"),
        date=row.get("date", ""), markdown=markdown, prose_input=prose_input,
        recall_group_key=recall_group_key(row, raw) if kind == "recall-quality" else "",
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
        parts.append("출처 링크 원문 미기재")
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
                   raw.get("fda483_excerpt"),
                   raw.get("description"), raw.get("summary"), row.get("body")), 300),
    }


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

    적용 범위(§14A): `kind=="recall-quality"` & 비어있지 않은 `recall_group_key` 동일군,
    멤버 2건 이상. 대표(§14C) = 그룹 내 `card_id` 사전식 오름차순 첫 카드.
    대표 = 병합 markdown + 통합 prose_input(§14E). 멤버 = `merged_into`=대표 card_id 마킹
    (렌더 제외·Status 유지). 빈 키·단독 멤버·이종 사유(다른 키)는 무변화.
    `build_card_scaffold()` 결과를 받아 `assemble_brief_skeleton()`/직렬화 직전에 적용.
    """
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(cards):
        if c.kind == "recall-quality" and c.recall_group_key:
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

# Notion 발행 카테고리 멀티셀렉트(§3.4) 결정론 매핑 — 별도 산출 로직이 코드/프롬프트에
# 없어 신규 단일원천으로 둔다. 키 = `resolve_kind` 가 내는 **내부 kind**(raw Type 명 아님).
# 미매핑(recall·admin·gmp-inspection/certificate·safety·who·hc·rss·483 등) = Other.
#
# §3.4 의 `gmp-guideline → Guideline` 은 raw Type 명을 혼용 표기한 것이다. 이 매핑에
# `"gmp-guideline"` 키를 추가하지 않는다(죽은 매핑 금지): `TYPE_GMP_GUIDELINE="gmp-guideline"`
# 은 collect_mfds.py 에 **정의만 있고 어느 수집기도 row 에 할당하지 않는 휴면 상수**이며,
# `resolve_kind` 에도 `gmp-guideline` 분기가 없다 → 내부 kind `"gmp-guideline"` 은 파이프라인에서
# 발현 불가. 만약 MFDS gmp-guideline Type 이 인입되면 MFDS else 분기 → kind `mfds-notice`
# → 카테고리 **"Guidance"**(Other 로 새지 않음; 가드 테스트로 고정). `Guideline` 독립 승격은
# 신규 kind 신설이 필요 = P1 범위 밖, 후속 이월.
_CATEGORY_MAP = {
    "warning-letter": "Warning Letter",
    "guidance": "Guidance",        # FR guidance-industry
    "mfds-notice": "Guidance",     # MFDS guidance-industry/internal
    "regulation": "Guidance",      # regulation-final/notice-final
    "legislative": "Guidance",     # legislative-notice
    "ich": "Guideline",            # ich-guideline/consultation
}

# web-card JSON 값에 들어가면 안 되는 표현 틀 토큰(불변식 #6) — 렌더러가 그림.
# modality/group_label 의 이모지(💊/🧬/▫️)는 스키마 데이터값이라 허용(여기 목록 밖).
_CARD_MARKUP_TOKENS = ("<callout", "<table", "<tr", "<td", "<details", "<summary",
                       "### ", "`", "{{")


def _category(kind: str) -> str:
    """Notion 발행 카테고리(§3.4): Warning Letter / Guidance / Guideline / Other."""
    return _CATEGORY_MAP.get(kind, "Other")


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
