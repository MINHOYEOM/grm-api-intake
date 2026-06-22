#!/usr/bin/env python3
"""6/22 주간 브리프 실데이터 회귀 픽스처 생성기(PL18/PL19 보정 정본).

provenance: Notion handoff page `3863142f-dc11-81ff-8d9f-fcea41becbab`(CONSUMED, 36행) 의
card_scaffold 정본 + 발행 page `3863142f-dc11-8130-b593-d64c00df745a` 의 실제 W2 셀값.
합성 테스트는 "내부 정합 scaffold" 만 써서 enrich-날짜/재구성 셀 클래스를 못 잡았으므로
실데이터로 FP 0/TP 8 을 영구 고정한다(scaffold→발행 전사 무결성, PL18).

handoff_rows 는 Notion handoff page 본문 JSON 에서 추출한 `handoff_rows_2026_06_22.json`
(36행 document_id + card_scaffold)를 그대로 싣는다. published_text 는 발행본의 실제 W2 셀값을
카드 영역으로 재구성한 것(LLM 산문은 lint 무관이라 최소화, 단 Evidence 배지·Coverage 헤더·
M3 메타는 PL19/카드영역 가림 재현을 위해 포함)이다.

실행: `python tests/fixtures/build_brief_2026_06_22.py` → tests/fixtures/brief_2026_06_22.json
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 발행본(2026-06-22) 렌더 14카드의 실제 W2 셀값 — 발행 page 에서 채록(render 순서).
# (라벨, 값) 리스트. enrich/재구성된 값은 그대로 반영(scaffold 와 다른 값 = TP/enrich 검증 대상).
EVIDENCE = {
    "fda483-192438": "B", "fda483-192689": "B", "fda483-192690": "B",
    "d1e41608f7ba": "A", "fda483-192439": "B", "fda483-192443": "B",
    "4399b537ba30": "B", "2026-12237": "B", "2026-12238": "B",
    "bfa2307d43e9": "B", "fda483-192871": "B", "fda483-192916": "B",
    "admin-2026004434": "A", "hc-82222": "A",
}
SOURCE_BADGE = {
    "fda483-192438": "FDA 483", "fda483-192689": "FDA 483", "fda483-192690": "FDA 483",
    "d1e41608f7ba": "Warning Letter", "fda483-192439": "FDA 483", "fda483-192443": "FDA 483",
    "4399b537ba30": "규제 소식", "2026-12237": "지침·안내서", "2026-12238": "지침·안내서",
    "bfa2307d43e9": "규제 소식", "fda483-192871": "FDA 483", "fda483-192916": "FDA 483",
    "admin-2026004434": "행정처분", "hc-82222": "Recall",
}
PUBLISHED_CELLS: list[tuple[str, list[tuple[str, str]]]] = [
    ("fda483-192438", [
        ("발행일", "2026-05-27"), ("문서번호", "`fda483-192438`"),
        ("제조소/업체", "Central Alabama Veterans Health Care System · FEI 1073935"),
        ("시설 · 유형", "Producer of Sterile Drug Products · 483"), ("실사일", "04/16/2026")]),
    ("fda483-192689", [
        ("발행일", "2026-05-27"), ("문서번호", "`fda483-192689`"),
        ("제조소/업체", "Intas Pharmaceutical Limited · FEI 3004831697"),
        ("시설 · 유형", "Finished Pharmaceuticals · 483"), ("실사일", "미파싱")]),
    ("fda483-192690", [
        ("발행일", "2026-05-27"), ("문서번호", "`fda483-192690`"),
        ("제조소/업체", "Dabur India Limited · FEI 3002809831"),
        ("시설 · 유형", "Finished Pharmaceuticals · 483"), ("실사일", "미파싱")]),
    ("d1e41608f7ba", [   # FDA WL — 발행일 enrich(수집일 06-16→실제 03-19), 발행부서 셀 재구성/생략
        ("발행일", "2026-03-19"), ("문서번호", "`d1e41608f7ba`"),
        ("제조소/업체", "Pharmathen International S.A."),
        ("시설 · 유형", "API 제조소 (그리스)"),
        ("위반 유형", "CGMP 위반 · 불량화(adulteration)")]),
    ("fda483-192439", [
        ("발행일", "2026-05-27"), ("문서번호", "`fda483-192439`"),
        ("제조소/업체", "BPI Labs, LLC · FEI 3016534068"),
        ("시설 · 유형", "503B Outsourcing Facility · 483"), ("실사일", "미파싱")]),
    ("fda483-192443", [
        ("발행일", "2026-05-27"), ("문서번호", "`fda483-192443`"),
        ("제조소/업체", "Wells Pharma of Houston LLC · FEI 3016440965"),
        ("시설 · 유형", "503B Outsourcing Facility · 483"), ("실사일", "미파싱")]),
    ("4399b537ba30", [   # ECA — 발행일 enrich(수집일 06-18→실제 2025-10-08), 주제 한국어 재작성
        ("발행일", "2025-10-08"), ("문서번호", "`4399b537ba30`"), ("출처", "ECA Academy"),
        ("주제", "GDP 비준수 보고서 — CAPA 기한 미준수로 인증 거부")]),
    ("2026-12237", [    # FR — 발행일 enrich(수집일 06-18→공시일 06-19), 주제 재작성
        ("발행일", "2026-06-19"), ("문서번호", "`2026-12237`"), ("출처", "Federal Register (FDA)"),
        ("주제", "Type A 동물용 의약품 MFFs GMP 정보수집 OMB 제출"), ("의견 기한", "원문 미기재")]),
    ("2026-12238", [
        ("발행일", "2026-06-19"), ("문서번호", "`2026-12238`"), ("출처", "Federal Register (FDA)"),
        ("주제", "건강기능식품 GMP 정보수집 OMB 제출"), ("의견 기한", "원문 미기재")]),
    ("bfa2307d43e9", [   # ECA — 발행일 enrich(수집일 06-17→실제 2025-11-04)
        ("발행일", "2025-11-04"), ("문서번호", "`bfa2307d43e9`"), ("출처", "ECA Academy"),
        ("주제", "원자재 시험·안정성·데이터 무결성 WL 결함 분석")]),
    ("fda483-192871", [
        ("발행일", "2026-05-27"), ("문서번호", "`fda483-192871`"),
        ("제조소/업체", "BlendHouse Portland LLC · FEI 3016827524"),
        ("시설 · 유형", "503B Outsourcing Facility · 483"), ("실사일", "미파싱")]),
    ("fda483-192916", [
        ("발행일", "2026-05-27"), ("문서번호", "`fda483-192916`"),
        ("제조소/업체", "BlendHouse Allerton LLC · FEI 3018218218"),
        ("시설 · 유형", "503B Outsourcing Facility · 483"), ("실사일", "미파싱")]),
    ("admin-2026004434", [   # MFDS 행정처분 — 처분일 발행일(06-17 정본)→수집일(06-19) 오사용·재구성
        ("처분일", "2026-06-19"), ("문서번호", "`admin-2026004434`"), ("업체", "경방신약(주)"),
        ("처분 유형", "제조업무정지"), ("정지 기간", "1개월"), ("위반 사항", "제조관리기준서 미준수")]),
    ("hc-82222", [   # HC recall — Class 삭제(과억제), 제품명 단순화
        ("발행일", "2026-06-19"), ("문서번호", "`hc-82222`"), ("제품명", "Lancora Tablets"),
        ("회수 사유", "블리스터 포장 내 파손·부분 정제"), ("업체", "원문 미기재"),
        ("회수 등급", "원문 내 등급 미기재 — 생성 금지")]),
]

# 발행본 Coverage 헤더(실측) — Evidence A 4/B 10/C 0 선언(실제 배지는 A3/B11 = PL19 불일치 재현).
COVERAGE_HEADER = (
    "**Coverage** · Intake 36건 (FR 13 · Recall 0 · EMA 2 · MHRA 0 · PIC/S 0 · ECA 7 · "
    "FDA WL 5 · MFDS 1 · ICH 0 · WHO 0 · HC 1 · FDA 483 7) · 병합 0건 · WebSearch 7/9 · "
    "WebFetch 2/5 · 유효항목 14건 · Evidence A 4/B 10/C 0 · 미확인: 슬롯 7(ICH Fetch 403)"
)
# M3 메타(실측 일부) — 'CONSUMED 2026-06-17 handoff' 가 admin 처분일 오류(06-17 정본)를
# 전역 substring 에서 가린다 → 카드 영역 한정 검사로만 admin 이 TP 로 잡힌다(보정 핵심).
M3_META = (
    "## 메타데이터\n"
    "M2 — handoff_id: routine-handoff::2026-06-22 · row_count: 36 · render_candidates: 14\n"
    "M3 — WebSearch 7/9 · 슬롯 7 (ICH Q-series): ICH Assembly Fetch 403 × 2 — 수동 확인 권장\n"
    "PL-10b 주간 재유입 가드: CONSUMED 2026-06-17 handoff rows=0 → 재카드화 위험 없음\n"
)


def _render_card(doc_id: str, cells: list[tuple[str, str]]) -> str:
    badges = (f"`Evidence {EVIDENCE[doc_id]}` · `{SOURCE_BADGE[doc_id]}` · "
              f"`Signal High (T3)` · `▫️ 기타`")
    rows = "\n".join(f"<tr><td>**{label}**</td><td>{value}</td></tr>" for label, value in cells)
    return (f"### [{SOURCE_BADGE[doc_id]} · 기관] 카드 제목 — **핵심 이슈**\n"
            f"<callout icon=\"📌\" color=\"blue_bg\">\n\t발행 산문(슬롯 채움).\n\t{badges}\n</callout>\n"
            f"<table>\n{rows}\n</table>")


def build_published_text() -> str:
    parts = [COVERAGE_HEADER, "## 🌐 글로벌"]
    parts += [_render_card(doc_id, cells) for doc_id, cells in PUBLISHED_CELLS]
    parts.append(M3_META)
    return "\n\n".join(parts)


def main() -> None:
    rows = json.load(open(os.path.join(HERE, "handoff_rows_2026_06_22.json"), encoding="utf-8"))
    fixture = {
        "provenance": {
            "handoff_page": "3863142f-dc11-81ff-8d9f-fcea41becbab",
            "brief_page": "3863142f-dc11-8130-b593-d64c00df745a",
            "run_date_kst": "2026-06-22",
            "note": "PL18/PL19 보정 정본 — FP 0/TP 8. handoff_rows=실데이터 36행, "
                    "published_text=발행 W2 셀 실측 재구성(+Coverage 헤더·M3 메타).",
        },
        "handoff_rows": rows,
        "published_text": build_published_text(),
        "expected_pl18_fail_doc_ids": [
            "fda483-192439", "fda483-192443", "fda483-192689", "fda483-192690",
            "fda483-192871", "fda483-192916", "hc-82222", "admin-2026004434",
        ],
        "expected_pl18_clean_doc_ids": [
            "fda483-192438", "d1e41608f7ba", "2026-12237", "2026-12238",
            "4399b537ba30", "bfa2307d43e9",
        ],
    }
    out = os.path.join(HERE, "brief_2026_06_22.json")
    json.dump(fixture, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", out, "rows=", len(rows), "pub_len=", len(fixture["published_text"]))


if __name__ == "__main__":
    main()
