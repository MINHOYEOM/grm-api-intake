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
from dataclasses import dataclass, field
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

# MFDS 하위 유형(type_or_class)
TYPE_ADMIN_ACTION = "admin-action"
TYPE_RECALL_QUALITY = "recall-quality"
TYPE_GMP_INSPECTION = "gmp-inspection"

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
        "guidance":         ("🟫", "지침·안내서", "Guidance"),
        "regulation":       ("🟫", "고시·개정법령", "규정"),
        "legislative":      ("🟫", "입법예고", "입법예고"),
        "safety-letter":    ("🟦", "안전성서한", "안전성"),
        "ich":              ("🟫", "ICH", "ICH"),
        "who-noc":          ("🟧", "WHO", "WHO"),
        "who-inspection":   ("🟧", "WHO", "WHO"),
        "who-news":         ("🟫", "WHO", "WHO"),
        "hc-recall":        ("🟧", "Recall(HC)", "Recall"),
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
        return d


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
        if "legislative" in toc:
            return "legislative"
        if "safety" in toc:
            return "safety-letter"
        if "regulation" in toc or "notice-final" in toc:
            return "regulation"
        return "guidance"
    if source == SOURCE_FR:
        return "guidance"
    if source in (SOURCE_EMA, SOURCE_MHRA, SOURCE_PICS, SOURCE_ECA):
        return "guidance"
    return "guidance"


# 규범 문서(특정 제품군에 매이지 않음) — 제품군 배지 생략 (§4)
_NORMATIVE_KINDS = {"guidance", "regulation", "legislative", "ich", "who-news"}


# ─────────────────────────────────────────────────────────────────────────────
# 6. Evidence 판정 (§6 + §12(D)/(H))
# ─────────────────────────────────────────────────────────────────────────────
def determine_evidence(kind: str, row: dict[str, Any], raw: dict[str, Any] | None) -> str:
    # graceful degrade(단계 B): raw fetch 실패 → B 강등
    if row.get("raw_fetch_ok") is False or row.get("evidence_hint") == "B":
        return "B"
    # search 단계가 기록한 힌트 우선
    hint = (row.get("evidence_candidate") or row.get("evidence_hint") or "").upper()
    if hint in ("A", "B", "C"):
        return hint
    # 유형별 기본값 (§12)
    if kind in ("warning-letter",):           # WL 본문 미수집 → B (§12B/C)
        return "B"
    if kind in ("ich", "who-news"):           # §12(H), RSS 뉴스
        return "B"
    if kind in ("guidance", "regulation", "legislative"):
        # FR/MFDS 지침: raw 에 본문/abstract 있으면 A, 아니면 B
        if raw and (raw.get("abstract") or raw.get("body") or raw.get("EXPOSE_CONT")):
            return "A"
        return "B"
    # admin/recall/gmp/openfda-recall: raw 보존 시 A
    return "A" if raw else "B"


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
    if not raw:
        return ""
    if kind == "admin-action":
        return _truncate_at_sentence(raw.get("EXPOSE_CONT", ""), 250)
    if kind == "recall-quality":
        return _first(raw.get("RTRVL_RESN"))
    if kind == "gmp-inspection":
        return _truncate_at_sentence(raw.get("attachment_text", ""), 250)
    if kind in ("guidance", "regulation", "legislative"):
        return _truncate_at_sentence(_first(raw.get("abstract"), raw.get("title")), 250)
    return ""  # WL·EMA/MHRA/PIC/S/ECA(Evidence B) → quote 없음


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
        if seq:
            official = ("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?"
                        f"dispsApplySeq={seq}")  # L1 (seq 로 결정론 생성)
        else:
            official = "https://nedrug.mfds.go.kr/pbp/CCBAO01"  # L2 인덱스
            fallback = True
    elif kind == "recall-quality":
        official = "https://nedrug.mfds.go.kr/pbp/CCBAH01"  # L2 인덱스(§12B)
        fallback = True
    elif kind == "gmp-inspection":
        official = _first(row.get("source_url"), row.get("official_url"))
    else:  # FR/EMA/MHRA/PIC/S/ECA/WHO/HC/ICH 등 RSS·페이지 L1
        official = _first(row.get("official_url"))
    return info, official, fallback


# ─────────────────────────────────────────────────────────────────────────────
# 9. 섹션 분류 (§7)
# ─────────────────────────────────────────────────────────────────────────────
def resolve_section(kind: str, row: dict[str, Any]) -> str:
    if kind in ("recall-quality", "openfda-recall", "hc-recall"):
        return "recall_table"
    if kind == "legislative":
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
    else:  # guidance/regulation/etc
        rows.append(("발행기관", row.get("source", "") or "원문 미기재"))
        if row.get("comments_close"):
            rows.append(("의견기한", row["comments_close"]))
        elif raw.get("title"):
            rows.append(("주제", _truncate_at_sentence(raw["title"], 80)))
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
# 13. 제목 (§1 + §13.1-1): [유형 · 기관] 핵심대상 — **{{TITLE_ISSUE}}** `DocID`
# ─────────────────────────────────────────────────────────────────────────────
# 기관 라벨 — 규제기관 short (제목 §13.1-1). source 기준(MFDS 카드도 소재국 아닌 Source).
_REGULATOR_LABEL = {
    SOURCE_FR: "FDA", SOURCE_RECALL: "FDA", SOURCE_FDA_WL: "FDA",
    SOURCE_EMA: "EMA", SOURCE_MHRA: "MHRA", SOURCE_PICS: "PIC/S",
    SOURCE_ECA: "ECA", SOURCE_MFDS: "MFDS", SOURCE_ICH: "ICH",
    SOURCE_WHO: "WHO", SOURCE_HC: "Health Canada",
}


def _regulator(source: str) -> str:
    return _REGULATOR_LABEL.get(source, source or "")


def _title(kind: str, row: dict[str, Any]) -> str:
    """제목(§13.1-1·8 동결): ### [유형 · 기관] 핵심대상 — **{{TITLE_ISSUE}}**.

    제목에서 제거: prefix 색사각형 이모지·소재국·DocID(→ W2 문서번호 행·W1 배지로).
    기관은 Source 기준 규제기관(MFDS 도 소재국 아님). 핵심대상=업체/제품/문서명.
    """
    _, label, _ = _kind_meta(kind)
    org = _regulator(row.get("source", ""))
    target = _truncate_at_sentence(_first(row.get("firm"), row.get("headline")), 60)
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

    markdown = "\n\n".join(blocks)
    prose_input = _prose_input(kind, row, raw, evidence, modality, language)
    return CardScaffold(
        card_id=card_id, section=section, kind=kind, evidence=evidence,
        modality=modality, signal_tier=row.get("signal_tier", "Tier 1"),
        date=row.get("date", ""), markdown=markdown, prose_input=prose_input,
        recall_group_key=recall_group_key(row, raw) if kind == "recall-quality" else "",
        status_hint=row.get("status_hint", ""),
        needs_llm_slots=tuple(used_slots),
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
            parts.append(f"📎 공식원본 [링크]({official}){warn}")
    if not parts:
        parts.append("출처 링크 원문 미기재")
    return _callout(["**출처**  " + "   ·   ".join(parts)], icon="🔖", color=cfg.color_footer)


def _prose_input(kind: str, row: dict[str, Any], raw: dict[str, Any] | None,
                 evidence: str, modality: str, language: str) -> dict[str, Any]:
    """§9 — 카드 1장치 최소 컨텍스트(raw 전체 아님). LLM 산문 슬롯 입력."""
    raw = raw or {}
    reason = _first(raw.get("RTRVL_RESN"), raw.get("EXPOSE_CONT"),
                    raw.get("ADM_DISPS_NAME"), raw.get("subject"), raw.get("abstract"))
    return {
        "kind": kind,
        "modality": modality,
        "firm_or_product": _first(raw.get("ENTRPS"), raw.get("firm"),
                                  raw.get("manufacturer"), row.get("firm")),
        "reason_summary": _truncate_at_sentence(reason, 300),
        "regulator": row.get("source", ""),
        "evidence": evidence,
        "signal": row.get("signal_tier", ""),
        "language": language,
        "headline": row.get("headline", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 15. assemble_brief_skeleton — 페이지 수준(목차·섹션·그룹핑·면책). 별도 순수 함수.
# ─────────────────────────────────────────────────────────────────────────────
_TIER_ORDER = {"Tier 3": 0, "Tier 2": 1, "Tier 1": 2}
_SECTION_ORDER = ["global", "domestic", "watch", "recall_table"]


def _sort_key(c: CardScaffold) -> tuple[int, str]:
    # Signal Tier 3→2→1, 동급 발행일 desc (§7)
    return (_TIER_ORDER.get(c.signal_tier, 9), _neg_date(c.date))


def _neg_date(d: str) -> str:
    # desc 정렬용 — 큰 날짜가 먼저. 문자열 역순 키.
    return "".join(chr(255 - ord(ch)) for ch in d) if d else "\xff"


def assemble_brief_skeleton(cards: list[CardScaffold],
                            cfg: FixedConfig = DEFAULT_CONFIG) -> str:
    """카드들을 페이지 골격(목차·섹션 H2·§7 그룹핑/정렬·면책 푸터)으로 조립.

    순수 함수. build_card_scaffold() 결과 리스트를 받아 페이지 마크다운 1개를 만든다.
    카드 1장 조립과 분리(단계 D/K3 재사용 단위가 다름).
    """
    out: list[str] = ["<table_of_contents/>"]
    for sec in _SECTION_ORDER:
        sec_cards = sorted([c for c in cards if c.section == sec], key=_sort_key)
        if not sec_cards:
            continue
        out.append(f"## {cfg.section_titles.get(sec, sec)}")
        # 글로벌 ≥임계면 제품군 그룹핑(§7), 아니면 평면
        if sec == "global" and len(sec_cards) >= cfg.grouping_threshold:
            for mod in ("Chemical", "Biologic", "Other"):
                grp = [c for c in sec_cards if (c.modality or "Other") == mod]
                if not grp:
                    continue
                out.append(f"### {cfg.modality_badge.get(mod, mod)}")
                out.extend(c.markdown for c in grp)
        else:
            out.extend(c.markdown for c in sec_cards)
    # 면책 푸터(§13.1-11) — 페이지 끝
    out.append("---")
    disc = list(cfg.disclaimer_ko) + [cfg.disclaimer_en]
    out.append(_callout(disc, icon="ℹ️", color=cfg.color_footer))
    return "\n\n".join(out)
