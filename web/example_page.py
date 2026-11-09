"""지적 사례 화면(/findings/examples/) — 주제별 규제기관 지적 문장 대표 사례.

데이터: web/data/deficiency_examples.json (사람이 고른 대표 사례. 출처 행 id 보존).
순수 함수만 둔다 — 네트워크·DB 0(render.py 순수성 가드).
번역(ko)은 DB 에 없어 정본 JSON 에 사람이 붙인 참고용 한 줄이다. 원문 기준은 영어(text) —
한국어 화면도 원문을 함께 싣는다(번역 단독 노출 금지). 영어 화면은 원문만.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXAMPLES_FILE = Path(__file__).resolve().parent / "data" / "deficiency_examples.json"
SCHEMA = "grm-deficiency-examples/v1"
ROUTE = "findings/examples/"

# 독자가 먼저 찾는 주제 순. 목록에 없는 주제의 사례는 화면에 싣지 않는다.
TOPIC_ORDER: tuple[str, ...] = (
    "data_integrity", "aseptic_sterility_assurance", "regulatory_reporting",
    "computer_system_validation", "deviation_capa", "documentation_records",
    "qc_lab_controls", "cleaning_validation", "contamination_control",
    "environmental_monitoring", "material_supplier_control", "complaint_recall",
    "training_personnel", "stability_storage", "equipment_facility",
    "quality_unit_oversight", "process_validation",
)
MAX_PER_TOPIC = 6
MAX_TEXT = 300
MAX_KO = 200


def load_examples(path: Path = EXAMPLES_FILE) -> "dict[str, Any] | None":
    """정본이 없으면 None → 페이지·링크가 조용히 빠진다(다른 정적 정본과 같은 관례)."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"deficiency_examples.json 스키마 불일치: {data.get('schema')!r}")
    return data


def build_topics(data: "dict[str, Any] | None",
                 labels: "dict[str, tuple[str, str]]") -> "list[dict[str, Any]]":
    """주제별 사례 뷰. labels = {code: (label_ko, label_en)}.

    - 주제는 TOPIC_ORDER 순, 사례는 정본 순서대로 주제당 MAX_PER_TOPIC 건까지.
    - 사례가 없는 주제는 낸 적 없는 것으로 친다(빈 구역 금지).
    - 출처 id 가 sources 에 없거나 본문이 한도를 넘으면 ValueError — 깨진 정본이 조용히 나가지 않게.
    """
    if not data:
        return []
    sources = data.get("sources") or {}
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for ex in data.get("examples") or []:
        src = sources.get(ex["source"])
        if src is None:
            raise ValueError(f"사례 {ex['id']}: 출처 {ex['source']} 없음")
        text = " ".join(ex["text"].split())
        if not text or len(text) > MAX_TEXT:
            raise ValueError(f"사례 {ex['id']}: 본문 길이 {len(text)} (1..{MAX_TEXT})")
        ko = " ".join((ex.get("ko") or "").split())
        if not ko or len(ko) > MAX_KO:
            raise ValueError(f"사례 {ex['id']}: 번역(ko) 길이 {len(ko)} (1..{MAX_KO})")
        if ex["category"] not in labels:
            raise ValueError(f"사례 {ex['id']}: 모르는 주제 {ex['category']}")
        by_topic.setdefault(ex["category"], []).append({
            "id": ex["id"], "text": text, "ko": ko,
            "agency": src["agency"], "year": src["year"],
            "year_basis": src.get("year_basis", "calendar"),
            "agency_category": " ".join((ex.get("agency_category") or "").split()),
            "locator": ex.get("locator") or "", "url": src["url"], "kind": src["kind"],
        })
    out = []
    for code in TOPIC_ORDER:
        items = by_topic.get(code, [])[:MAX_PER_TOPIC]
        if items:
            out.append({"code": code, "label_ko": labels[code][0],
                        "label_en": labels[code][1], "examples": items})
    return out
