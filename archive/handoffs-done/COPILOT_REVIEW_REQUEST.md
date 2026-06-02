# Copilot 리뷰 요청 — GRM 구현 검토 및 미흡점 발굴

> **역할**: Claude(로컬 에이전트)가 구현한 변경 사항의 diff를 검토하고,
> 엣지케이스·누락 로직·더 나은 대안을 제안한다.
> 구현 전 단계에서는 설계 방향을 검토하고 위험을 사전에 짚어준다.
>
> **로컬 저장소 경로**: `C:\Users\user\Desktop\Global Regulatory Monitor\v15.0-implementation\`

---

## 시스템 한 줄 요약

FDA·OpenFDA → GitHub Actions 수집기 → Notion Intake DB → Claude Routine → Notion 다이제스트.
한국 제약사 QA 담당자용 주간 글로벌 규제 학습 도구. 경구 고형제(정제) 중심.

---

## Copilot에게 요청하는 역할

### 역할 A — 구현 전 설계 검토

Claude와 사용자가 구현 방향을 논의할 때, 아래 질문을 통해 설계 품질을 높인다.

**물어볼 것들**:
1. 이 변경이 기존 코드와 일관성 있는 패턴을 사용하는가?
2. 엣지케이스(빈 값, 예외적 API 응답, 네트워크 중단)를 충분히 고려했는가?
3. dry-run 모드에서도 동일하게 동작하는가?
4. 이 변경이 나중에 EMA/Health Canada를 추가할 때 확장되기 쉬운가?

### 역할 B — 구현 후 diff 리뷰

Claude가 `collect_intake.py` 또는 `GRM_Prompt_v15.0.md`를 변경했을 때, 변경 diff를 보고 아래를 검토한다.

**검토 포인트**:
- 변경된 함수의 호출부 모두 업데이트됐는가?
- 새 로직의 반환값이 상위 호출자에서 올바르게 처리되는가?
- 추가된 상수나 규칙이 문서(`notion_intake_db_schema.md`, README)에도 반영됐는가?
- 프롬프트 변경 시 다른 섹션과 충돌하는 부분이 없는가?

---

## 현재 진행 중인 변경 목록 (검토 대상)

### 변경 그룹 1 — 버그 픽스 (`collect_intake.py`, `grm-intake.yml`)

| 항목 | 내용 | 검토 포인트 |
|---|---|---|
| Notion 429 재시도 | `notion_create_page`에 재시도 + Retry-After 처리 추가 | 재시도 횟수·간격이 적절한가? 무한루프 가능성은? |
| insert 실패 카운트 | `CollectionStats`에 `fr_insert_failed` 추가 | 반환값 변경이 `main()`에서 올바르게 처리되는가? |
| FR pagination 에러 | 10페이지 초과 시 `stats.fr_error = True` | 이미 수집된 items는 정상 처리되는가? |
| cron 조정 | `0 22 * * 0` → `0 20 * * 0` | 주석도 함께 업데이트됐는가? |
| shell injection | `window_days` 변수화 | `dry_run` 경로도 동일하게 방어됐는가? |

### 변경 그룹 2 — 기능 추가 (`collect_intake.py`)

| 항목 | 내용 | 검토 포인트 |
|---|---|---|
| 단어 경계 매칭 | `compute_relevance`의 `in` → `re.search(r'\b...\b')` | 복합어 키워드(`"manufacturing practice"`)에서 `\b` 경계가 올바른가? |
| 키워드 확장 | `QA_CATEGORY_KEYWORDS`에 dissolution, nitrosamine 계열 추가 | 추가 키워드가 false positive를 유발하는 케이스는? |
| OSD Relevance | `_recall_to_item()`에 `compute_osd_relevance()` 연동 | `openfda` 필드 자체가 없는 row 처리는? |
| 날짜 검증 | `_safe_date_iso()` 헬퍼 추가 | FR API의 실제 날짜 형식 스펙과 일치하는가? |

### 변경 그룹 3 — 프롬프트 설계 (`GRM_Prompt_v15.0.md`)

| 항목 | 내용 | 검토 포인트 |
|---|---|---|
| Recall 3-tier v15.1 | route/dosage_form 기반 Tier 분류 규칙 | Claude가 raw payload에서 이 필드를 실제로 찾을 수 있는가? |
| Status 갱신 지시 | 처리 완료 row를 Processed로 업데이트하는 지시 | 실패한 row를 Error로 마킹하는 경우도 처리됐는가? |
| WebFetch 정책 통일 | [핵심 원칙] vs [3순위] 모순 제거 | 통일 후 [0순위] 섹션에 영향 없는가? |
| Notion pagination | [0단계]에 100건 이상 pagination 처리 지시 추가 | Claude가 실제로 Notion MCP로 pagination을 처리할 수 있는가? |

---

## 질문 목록 (구현 논의 시 활용)

아래 질문들을 Claude와 사용자 간 논의에서 던져줘.

**아키텍처 질문**:
- Notion을 중간 저장소로 계속 쓰면서 100+ rows/run이 되면 `notion-fetch`의 응답 시간이 얼마나 늘어날까?
- `collect_intake.py`를 단일 파일로 유지하는 것과 기관별 모듈로 분리하는 것 중 Phase 3(EMA 추가) 기준으로 어느 쪽이 더 유지보수하기 쉬울까?

**프롬프트 설계 질문**:
- 현재 프롬프트 540줄은 Claude의 context window 내에서 일관되게 처리되는가, 아니면 후반부 규칙이 전반부에 영향받는가?
- Recall Tier 분류를 프롬프트에서 하는 것과 수집기에서 미리 계산해 Notion 속성으로 저장하는 것 중 어느 쪽이 더 신뢰할 수 있는가?

**운영 질문**:
- 이번 주 Job Summary를 보지 않고도 "수집이 정상이었나"를 판단할 수 있는 방법이 있는가?
- 수동 재실행 시나리오(같은 주에 `workflow_dispatch` 두 번 실행)에서 데이터 중복이 발생하지 않는가?

---

## diff 리뷰 방법

구현이 완료되면:
```
git diff main..phase2-improvements -- collect_intake.py
git diff main..phase2-improvements -- GRM_Prompt_v15.0.md
```
위 diff를 Copilot Chat에 붙여넣고 "이 변경에서 놓친 엣지케이스나 더 나은 접근법이 있는가?"를 물어봐줘.
