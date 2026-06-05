# GRM Keystone — Codex 검토 요청 프롬프트

> 사용법: Codex(repo + 코드 실행 + Notion 스키마 접근)에 그대로 붙여넣는다.
> 목적: Keystone 설계·카드 스펙이 "실제 코드/필드로 결정론적 구현이 가능한지" 검증.

---

```
[역할] GRM 시스템의 구현 타당성 검토자(Codex). repo와 collector 코드를 직접 확인해
"설계대로 결정론적 구현이 되는가"를 코드 근거로 판정한다. 추측 금지 — 파일/필드를 직접 연다.

[먼저 읽을 것]
- docs/GRM_architecture_redesign.md  (목표 아키텍처, M0~M5, handoff v2, §7 노이즈필터)
- docs/GRM_Keystone_charter.md        (프로젝트 범위·로드맵 K1~K4)
- docs/GRM_card_spec_v16.md           (카드 고정 템플릿 틀 — 이번 검토의 핵심)
- 대조용: collect_intake.py(_intake_page_snapshot·PROP_* 상수·build_routine_handoff_payload·
  FDA WL 파서·compute_modality), docs/notion_intake_db_schema.md, docs/prompts/GRM_Prompt_v15.8.md

[핵심 검토 질문 — 코드 근거로 답할 것]

1. 필드 매핑 정합성 (card_spec §0/§3/§5)
   - 스펙이 "Python이 채운다"고 한 모든 칸이 실제 Intake 필드로 도출 가능한가?
   - 스펙에 적힌 필드명(예: ADM_DISPS_NAME·RTRVL_RESN·ENTRPS·Site Country·official_url·
     api_query·attachment_text·before_after·inspection_start/end·Modality)이
     _intake_page_snapshot 키 / PROP_* 상수 / raw payload 키와 정확히 일치하는가?
     불일치·부재 필드를 목록으로.
   - source별(FR·Recall·EMA·MHRA·PIC/S·ECA·FDA WL·MFDS admin/recall/gmp·ICH·WHO·HC)로
     W2 메타표 유형별 행이 빠짐없이 채워지는지, 결측이 잦은 필드는 무엇인지.

2. 숨은 LLM 의존성
   - "Python 칸"으로 분류됐지만 실제로는 판단이 필요한 칸이 있는가?
     (예: 카드 제목의 {유형 라벨} 선택, 제품군 배지 폴백 판정, Evidence A/B/C 자동 판정,
     duplicate 통합 키) — 결정론 규칙만으로 안 되는 지점을 지적.

3. handoff v2 데이터 계약 (redesign §4)
   - rows[]에 이미 page_id·official_url·raw가 있는지 확인(코드 근거).
   - card_scaffold·prose_input·section·card_id를 collector가 산출하는 게 현실적인가,
     스키마 버전 v1→v2 하위호환 전략에 구멍은 없는가.

4. 노이즈 필터 부서 게이트 (redesign §7, 마이그레이션 M0)
   - FDA WL 파서에 발행 부서(center/office) 필드가 실제로 존재/추출 가능한가?
   - 제외 목록(Human Foods Program·Office of Inspections·CVM·CTP·CDRH·CFSAN)과
     유지(CDER)·2차판단(CBER)이 실제 데이터의 부서 표기와 매칭되는가? 표기 변형 리스크는?
   - 부서 필드 결측 시 키워드 폴백이 안전한가. unittest 케이스 제안.

5. 결정론·테스트 가능성
   - build_card_scaffold()를 golden-file 테스트로 고정하는 게 가능한가(같은 입력→같은 출력).
     비결정 요소(딕셔너리 순서·시간·URL 인코딩 등) 위험 지점.

6. 마이그레이션 충돌·순서
   - M0~M5 중 feature/multi-modality 머지 이후 코드와 충돌 가능 지점.
   - K2(scaffold) 도입이 기존 매일 수집 + handoff 생성 흐름을 깨지 않는지.

[산출물]
1. 필드 매핑 불일치/부재 목록(스펙 위치 ↔ 실제 코드 필드).
2. 숨은 LLM 의존성·결정론 불가 지점 목록.
3. M0 노이즈필터 부서 게이트 구현 가능성 판정 + unittest 케이스 제안.
4. handoff v2·golden 테스트 타당성 판정.
5. 종합: "스펙대로 구현 가능 / 조건부(무엇을 고쳐야) / 재설계 필요" 중 하나 + 근거.
※ 코드 변경은 제안만. 실제 구현·push는 별도 단계. 프로덕션 Notion/DB 파괴적 변경 금지.
```

## 📝 변경 이력
| 날짜 | 변경 내용 |
|---|---|
| 2026-06-04 | 최초 작성. Keystone 설계·카드 스펙 v16 구현 타당성 검토용(필드 매핑·숨은 LLM 의존성·handoff v2·M0 노이즈필터·golden 테스트) |
