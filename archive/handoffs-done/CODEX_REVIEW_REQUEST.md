# Codex 리뷰 요청 — GRM v15.0 시스템 종합 탐색

> **역할**: 코드베이스를 직접 탐색해서 개선 포인트를 발굴하고, Claude(로컬 에이전트)가
> 구현한 변경 사항을 검토해 미흡점·엣지케이스·더 나은 접근법을 제안한다.
> 실제 구현은 Claude가 로컬에서 직접 수행한다.
>
> **로컬 저장소 경로**: `C:\Users\user\Desktop\Global Regulatory Monitor\v15.0-implementation\`
> (또는 GitHub: `MINHOYEOM/grm-api-intake`)

---

## 시스템 개요

**GRM (Global Regulatory Monitor)** — 한국 제약사 QA 담당자용 주간 글로벌 규제 다이제스트 자동화 시스템.

```
[GitHub Actions — 매주 일요일 20:00 UTC]
  FDA Federal Register API → 수집
  OpenFDA Drug Enforcement API → 수집
  → Notion "GRM API Intake" DB에 raw 필드 저장

[Claude Routine — 매주 월요일 07:30 KST]
  Notion Intake DB 읽기 (0단계)
  → WebSearch 9회 + WebFetch 5회 보완
  → Notion "Global Regulatory Monitor" DB에 다이제스트 페이지 생성
```

- **v15.0 Phase 1 완료** (2026-05-26 첫 실행 성공)
- **현재 상태**: Phase 2 개선 작업 진행 중
- **주요 파일**: `collect_intake.py` (수집기, ~700줄), `GRM_Prompt_v15.0.md` (Routine 프롬프트), `.github/workflows/grm-intake.yml`, `notion_intake_db_schema.md`

---

## 탐색 요청 사항

아래 관점으로 코드베이스 전체를 탐색하고, 발견 사항을 우선순위 목록으로 정리해줘.
Claude가 이미 파악한 항목(아래 "이미 식별된 이슈" 참고)과 중복되지 않는 새로운 발견에 집중해줘.

### 관점 1 — 코드 품질 및 Python 관용구
- `collect_intake.py`에서 Python best practice를 위반하는 패턴
- 타입 힌트 누락, 에러 처리 비일관성, 하드코딩된 값
- 테스트 가능성 (현재 테스트 파일 없음 — 테스트 작성 용이성 평가)

### 관점 2 — GitHub Actions 워크플로우
- `.github/workflows/grm-intake.yml` 에서 보안·신뢰성·유지보수성 개선 가능한 부분
- Actions 권한 설정 (`permissions`)의 최소 권한 원칙 준수 여부
- 실패 시나리오 커버리지

### 관점 3 — Notion 통합 설계
- `collect_intake.py`의 Notion API 사용 패턴에서 비효율 또는 위험한 부분
- Notion DB 스키마(`notion_intake_db_schema.md`)와 코드 간 불일치
- Notion을 중간 저장소로 쓰는 설계의 엣지케이스

### 관점 4 — Claude Routine 프롬프트 (`GRM_Prompt_v15.0.md`)
- 지시 충돌, 모호성, Claude가 잘못 해석할 수 있는 부분
- v15.0 → v15.1 delta 누적으로 생긴 구조적 문제
- 프롬프트 길이·밀도가 실행 신뢰성에 미치는 영향

### 관점 5 — 운영 위험
- 무음 실패(silent failure) 시나리오 — 에러가 없어 보이지만 데이터가 손실되는 경우
- KPI "저장률 100%"를 실제로 검증할 수 없는 경우가 어디 있나
- 단기(4주) vs 중기(8주) 운영에서 처음으로 문제가 될 지점

---

## 이미 식별된 이슈 (중복 탐색 불필요)

**코드 레벨**:
- Notion API 429 재시도 미처리 (`notion_create_page`)
- FR pagination 10페이지 초과 시 `fr_error` 미설정
- insert 실패 카운트 미집계 (`CollectionStats`)
- `workflow_dispatch` `window_days` shell injection 위험
- cron 30분 버퍼 부족 (race condition)
- `QA_CATEGORY_KEYWORDS`의 `csv`, `oos` 등 단어 경계 오탐
- 중복 체크 키에 Source 미포함

**시스템 레벨**:
- Notion SPOF — 조회 실패 vs 0건 구분 불가
- Recall 3-tier 분류에 필요한 `route`/`dosage_form` 필드가 Notion 속성에 없음
- FR 날짜 윈도우의 UTC/KST 경계 불일치
- WebFetch 정책 모순 ([핵심 원칙] 6항 vs [3순위])
- 13개 카테고리 필터 vs Recall 3-tier 처리 순서 불명확
- Routine 프롬프트에 Notion pagination 처리 지시 없음
- Body 속성 1900자 truncation이 Evidence A quote에 영향 가능
- 프롬프트 블록 번호 체계 불일치 (숫자 vs 알파벳 혼용)
- Status 갱신 지시 누락 (row가 영구 New 상태)

---

## 구현 변경 사항 검토 요청

Claude가 구현을 완료하면 아래 항목을 검토해줘.

### 검토 체크리스트

1. **Recall 3-tier 규칙 (GRM_Prompt_v15.0.md)**
   - Tier 분류 기준이 실제 OpenFDA raw payload 구조와 일치하는가?
   - `route=ORAL` 단순 비교가 다중값 배열(`["ORAL", "TOPICAL"]`)을 올바르게 처리하는가?
   - 13개 카테고리 필터와 Tier 규칙의 우선순위가 명확한가?

2. **QA relevance 키워드 확장 (collect_intake.py)**
   - 추가된 키워드가 false positive를 증가시킬 가능성은?
   - 단어 경계 매칭(`\b` regex) 적용 후 성능 영향은?
   - `QA_EXCLUDE_KEYWORDS`의 `"food safety"` 조건이 정상 GMP 문서를 걸러낼 위험은?

3. **OSD Relevance 필드 추가 (collect_intake.py + Notion)**
   - `openfda.route`가 없는 recall 항목의 처리가 올바른가?
   - `compute_osd_relevance()` 함수의 fallback 로직이 충분한가?

4. **버그 픽스 묶음 (collect_intake.py + grm-intake.yml)**
   - `notion_create_page` 재시도 로직이 무한루프에 빠질 엣지케이스는?
   - `insert_items` 반환 시그니처 변경 후 모든 호출부가 업데이트됐는가?
   - 실패 카운터가 dry-run 모드에서 의도치 않게 집계되지 않는가?

---

## 출력 형식

각 새 발견 항목:
```
[카테고리] 파일명:라인범위
현상: (2-3줄)
영향: critical / should-fix / nice-to-have
제안: (1-2줄)
```

구현 검토 피드백:
```
[검토 항목] #번호
판정: OK / 수정 필요 / 주의
사유: (구체적으로)
```
