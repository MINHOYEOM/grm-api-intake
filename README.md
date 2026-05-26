# GRS API Intake — v15.0 Phase 1

Federal Register + OpenFDA Drug Enforcement API 를 매주 1회 호출해 Notion 데이터베이스에 raw 데이터를 저장하는 GitHub Actions 수집기입니다. Claude Code Routine (v15.0) 이 이 데이터를 0단계에서 읽어 다이제스트에 통합합니다.

## 구성 요소

| 파일 | 역할 |
|---|---|
| `collect_intake.py` | FR + OpenFDA 호출 → Notion 적재 (Python 3.12) |
| `.github/workflows/grs-intake.yml` | 매주 일요일 22:07 UTC (월요일 07:07 KST) 자동 실행 |
| `requirements.txt` | Python 의존성 (`requests` 만) |
| `notion_intake_db_schema.md` | Notion DB 스키마 정의 |
| `GRS_Prompt_v15.0.md` | v14.5 → v15.0 수정된 Routine 프롬프트 |
| `.env.example` | 로컬 dry-run 용 환경변수 예시 |

## ✓ 셋업 진행 상황

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. Notion Integration 토큰 발급 | ⚠️ 사용자 작업 | 채팅에 노출된 `ntn_…` 토큰은 반드시 폐기·재발급 |
| 2. Notion "GRS API Intake" DB 생성 | ✓ 완료 | DB ID `7784c71fb7b343749b2bee5d04db7926` |
| 3. Integration 을 부모 페이지에 연결 | ⚠️ 사용자 작업 | `Global Regulatory Sweep` 부모 페이지에 추가 |
| 4. OpenFDA API key 발급 | ⚠️ 사용자 작업 | 선택 (무료, 권장) |
| 5. GitHub 저장소 생성 + 파일 push | ⚠️ 사용자 작업 | 공개 권장 |
| 6. GitHub Secrets 등록 | ⚠️ 사용자 작업 | 3개 (`NOTION_TOKEN`, `NOTION_DATABASE_ID`, `OPENFDA_API_KEY`) |
| 7. 수동 dry-run | ⚠️ 사용자 작업 | Actions → workflow_dispatch → `dry_run: true` |
| 8. 실제 적재 첫 실행 | ⚠️ 사용자 작업 | dry_run: false |
| 9. Routine 프롬프트 v15.0 으로 교체 | ⚠️ 사용자 작업 | `GRS_Prompt_v15.0.md` 의 B 섹션 코드블록 |

## 셋업 절차

### 1. Notion Integration 토큰 발급 (★ 토큰 노출 대응)

**먼저 이 채팅에 붙여넣었던 토큰을 폐기하세요.** (실제 토큰 문자열은 의도적으로 이 파일에 기록하지 않습니다. 본인 Notion Integration 페이지에서 확인 가능합니다.)

1. <https://www.notion.so/profile/integrations> 접속
2. 해당 Integration 클릭 → `Secrets` 탭 → `Rotate` 버튼 → 기존 토큰 무효화 + 새 토큰 발급
3. 또는 새 Integration 생성: `+ New integration` → 이름 `GRS Intake Collector`, 타입 `Internal`
4. 권한은 `Read content` + `Insert content` + `Update content` 만 허용
5. 토큰 (`ntn_…` 또는 `secret_…`) 복사 — **다시는 평문으로 어디에도 붙여넣지 말 것. GitHub Secrets 만 사용.**

### 2. ✓ Notion "GRS API Intake" 데이터베이스 — 이미 생성됨

| 항목 | 값 |
|---|---|
| URL | <https://www.notion.so/7784c71fb7b343749b2bee5d04db7926> |
| Database ID | `7784c71fb7b343749b2bee5d04db7926` |
| 부모 페이지 | `Global Regulatory Sweep` |

`notion_intake_db_schema.md` 참조. 16개 속성 + 모든 select 옵션 사전 등록 완료.

### 3. Integration 을 부모 페이지에 연결

1. Notion 에서 `Global Regulatory Sweep` 부모 페이지 열기
2. 우상단 `…` → `Connections` → 1단계에서 발급한 Integration 추가
3. 자식 `GRS API Intake` 와 기존 `🌐 Global Regulatory Sweep` 다이제스트 DB 모두 자동으로 접근 허용됨

### 4. OpenFDA API key 발급 (무료, 권장)

<https://open.fda.gov/apis/authentication/> 에서 이메일로 발급. 토큰 없이도 호출은 되지만 시간당 240 requests 제한이 240,000 으로 늘어남.

### 5. GitHub 저장소 생성 + 파일 업로드

```bash
# 새 디렉토리에서
git init
cp -r <이 폴더의 내용>/* .
git add .
git commit -m "Initial v15.0 Phase 1 — intake collector"
git remote add origin https://github.com/<USER>/<REPO>.git
git push -u origin main
```

저장소는 **Public** 권장 (GitHub-hosted runner 무제한 무료). 코드에 비밀이 없고 Secrets 는 별도 저장되므로 안전합니다.

### 6. GitHub Secrets 등록

저장소 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

| Name | Value |
|---|---|
| `NOTION_TOKEN` | 1단계에서 재발급한 새 토큰 |
| `NOTION_DATABASE_ID` | `7784c71fb7b343749b2bee5d04db7926` |
| `OPENFDA_API_KEY` | 4단계에서 발급한 키 (없으면 비워둠) |

### 7. 첫 실행 (수동 테스트)

저장소 → `Actions` 탭 → `GRS API Intake (Weekly)` → `Run workflow` → `dry_run: true` 로 한 번 실행.

성공하면 Job Summary 에 fetched / inserted 건수가 보입니다. 그 다음 `dry_run: false` 로 실제 적재.

## 로컬 dry-run

```bash
cp .env.example .env
# .env 편집 후
python -m pip install -r requirements.txt
export $(cat .env | grep -v '^#' | xargs)
python collect_intake.py --dry-run
```

`--dry-run` 은 Notion API 호출 없이 stdout 에 fetch 결과만 출력합니다.

## 운영 시나리오

| 상황 | 결과 |
|---|---|
| 정상 (FR + OpenFDA 모두 성공) | Notion DB 에 row 추가, workflow ✓, Routine 이 0단계에서 읽어 사용 |
| 한쪽 API 실패 | 성공한 쪽만 적재, workflow ✓ (WARN 로그), Routine 은 partial intake + WebSearch 보완 |
| 두 API 모두 실패 | workflow ✗, 자동 issue 생성, Routine 은 v14.5 WebSearch-only 모드 graceful degradation |
| Notion 자체 장애 | workflow ✗, 자동 issue 생성, Routine 은 v14.5 WebSearch-only 모드 |
| 결과 0건 (조용한 주) | workflow ✓, Notion row 0건, Routine 은 v14.5 동작 |

## KPI 추적

| KPI | 목표 | 측정 방법 |
|---|---|---|
| FR Intake 저장률 | API 결과 대비 100% | Job Summary `fetched == inserted + skip-dup` |
| Recall Intake 저장률 | API 결과 대비 100% | 위 동일 |
| QA 관련 항목 다이제스트 반영률 | ≥90% | Routine 출력 후 수동 비교 (월별) |
| Workflow 성공률 | ≥95% | GitHub Actions runs 통계 |

## 트러블슈팅

### Notion 401 / unauthorized
- Integration 이 `Global Regulatory Sweep` 부모 페이지에 연결돼 있는지 확인 (`Connections` 메뉴)
- 토큰이 다른 Integration 의 것이 아닌지 확인

### Notion 400 / validation_error
- DB 속성 이름이 `notion_intake_db_schema.md` 와 정확히 일치하는지
- 특히 `Run Date (KST)` 의 괄호 · 공백 일치 여부

### Federal Register HTTP 4xx
- 일시적이면 자동 재시도 (2회). 5분 후 재실행 권장
- 파라미터 형식이 바뀐 경우 <https://www.federalregister.gov/developers/api/v1> 확인

### OpenFDA 429 (rate limit)
- `OPENFDA_API_KEY` 가 설정돼 있는지 확인
- 시간 두고 재실행

## v15.0 Routine 연동

`GRS_Prompt_v15.0.md` 의 `[0단계 — Notion Intake 읽기]` 섹션이 이 수집기 출력을 어떻게 소비하는지 정의합니다. 핵심:

1. Routine 시작 시 Notion MCP 로 `GRS API Intake` (`7784c71fb7b343749b2bee5d04db7926`) 를 `Run Date (KST) = 오늘` 필터로 조회
2. row 가 있으면 FR · Recall 전수 목록 확보 → Evidence A 부여
3. row 가 0건이면 v14.5 WebSearch-only 모드로 fallback

## 라이선스 · 책임

- Federal Register API: 미국 정부 저작물, 공용
- OpenFDA API: 정부 데이터, terms of use 준수 (<https://open.fda.gov/terms/>)
- 본 다이제스트는 학습·모니터링 목적이며 공식 견해 아님. 원문 검증은 사용자 책임
