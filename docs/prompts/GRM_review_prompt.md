# GRM 전방위 점검 요청 (Claude + Codex 공용 프롬프트)

> 사용법: 새 채팅에서 이 문서를 그대로 붙여넣어 점검을 요청한다. Claude(Notion MCP+repo)와 Codex(repo+Git+로컬 실행)에 각각, 또는 순차로 준다.
> **먼저 `GRM_session_decisions.md`를 정독**하고 시작할 것(현재 결정·라이브 상태·보류 항목이 정리돼 있음).

## 0. 목적
GRM(Global Regulatory Monitor)에서 **국내 제조/품질 관련 식약처(MFDS) 정보가 (a) 빠짐없이 수집되는지, (b) 명확히 분석·태깅되는지** 전방위 점검하고 개선·보완점을 도출한다. 추가로 **Weekly Brief Routine이 현재 구조를 반영하는지** 점검한다. 이 단계는 **점검·제안**이며 코드 변경은 제안만(구현은 별도 단계).

## 1. 시스템 컨텍스트 (ground truth — 중복 구축 방지)
- 흐름: GitHub Actions가 매일 03:17 KST 수집 → Notion "GRM API Intake" DB 적재 → 매주 Claude Weekly Brief가 읽어 QA팀 요약.
- repo: `MINHOYEOM/grm-api-intake`(main). 핵심 파일: `collect_intake.py`, `collect_search.py`, `collect_mfds.py`(RSS 7보드), `collect_mfds_recall.py`, `collect_mfds_admin_action.py`, `collect_mfds_gmp_inspection.py`, `grm_common.py`, `.github/workflows/grm-intake.yml`, `GRM_session_decisions.md`, `Phase2*_*spec.md`.
- Notion: DB `7784c71fb7b343749b2bee5d04db7926`, data source `collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288`.
- **현재 라이브(MFDS) — 중복 구축 금지**: ① GMP 지침·안내서·민원인안내서·공무원지침(RSS data0013/0011/0010) ② 입법예고(data0009)·개정법령(data0008)·고시(data0005) ③ 안전성서한(seohan001) ④ 회수·판매중지(data.go.kr 15059114) ⑤ 행정처분(15058457) ⑥ GMP 실사결과 목록+지적사항 본문(nedrug `/pbp/CCBBD03` 스크래핑+PDF/HWPX 추출).
- 글로벌 라이브: FR·OpenFDA·EMA·MHRA·PIC/S·ECA·FDA WL·Brave.
- 보류: 적합판정서(15097207, API 403 키전파 대기), 입법예고 ogLmPp API(`ENABLE_MOLEG_API=false`, IP/인가 제약 — RSS로 커버 중). 제거됨: **Self-Check 기능**(미래 사용자 설정형 제품으로 이관).
- 플래그: `ENABLE_MFDS/ENABLE_MFDS_RECALL/ENABLE_MFDS_ADMIN/ENABLE_MFDS_GMP_INSPECTION=true`, `ENABLE_MOLEG_API=false`.

## 2. 점검 축

### A. 커버리지 완전성 — "제조/품질 정보가 빠짐없이?"
현재 안 들어오는 **MFDS 제조/품질 관련 정보원**이 있는지 전수 검토하고, 각각 공개채널(API/RSS/HTML) 존재·제조품질 관련성·우선순위를 표로. 최소 아래 후보를 확인:
- 품목허가/변경허가/품목취하·취소, 원료의약품 등록(DMF/원료 공고), 위해(불법)의약품 회수·공표, 표준제조기준·기준규격 개정, 제조방법 변경 지시, 위·수탁 제조, 수입의약품·해외제조소 등록·실사, 안전성정보(DUR·실마리정보·부작용), 청문/사전통지, 약사감시 결과, 의약품 품질 부적합 공시.
- 산출물: **"누락 소스 우선순위 표"**(소스 / 채널 유무 / 제조품질 가치 / 난이도 / 권고).

### B. 수집 정확성·완전성 — "최신분을 누락 없이?"
- 각 collector가 수집 윈도우 내 신규 건을 빠짐없이 긁는지: 페이지네이션, 날짜 윈도우 기준, dedupe, 관련성 필터의 **과락(진짜 GMP/품질 건이 필터로 누락)** 및 **오탐**.
- 실측 대조: 각 소스 30/90일 수집 건수 vs 실제 공개 건수(원 사이트/총건수)와 비교.
- 특히: RSS GMP 키워드 필터(`collect_mfds`)가 진짜 지침을 누락 안 하나, 회수 collector의 품질 키워드, admin 저가치 도메인 필터 과락, gmp-inspection의 `sort` 함정/등록일 윈도우.

### C. 분석·태깅 품질 — "명확히 분석되나?"
- Notion 태깅 일관성·의미: `Source`/`Type or Class`/`Signal Tier`(1~3)/`QA Relevance`/`Language`/`Region/Jurisdiction`. 소스별 Tier 기준이 합리적인가(예: admin Tier3, gmp-cert Tier1, gmp-inspection 지적유무로 2/3).
- gmp-inspection 본문(`attachment_text`) 추출 정합: PDF 텍스트레이어/HWPX/스캔/실패 비율, 깨짐·누락, `attachment_parse_status` 신뢰성.
- 중복·유령행, 한국어 관련성 휴리스틱 정확도(샘플 라벨 검수).

### D. 운영 안정성
- 실패 표면화: insert 실패→exit/issue, 부분 실패 graceful, 조용한 성공 방지.
- 스크래핑 견고성(nedrug 끊김/구조 변경 시 경고), data.go.kr rate limit/429, 시크릿 마스킹·노출, 키 의존(403) 대응.

### E. Weekly Brief Routine 정합 (중요)
현재 Routine 정의 파일(`GRM_Prompt_v15.x.md` 계열)이 **이번 세션 변경을 반영하는지** 점검하고 변경안 제시:
- Intake 읽기 쿼리/필터에 **`Source=MFDS` 포함**? 신규 `Type or Class`(gmp-inspection·recall-quality·admin-action·legislative-notice·regulation-final·notice-final·guidance-industry·guidance-internal·safety-letter) 전부 인지?
- **국내(Language=KO) 항목 한글 유지** + 핵심 영문 병기 정책 반영?
- **gmp-inspection 지적사항 본문(`attachment_text`)을 읽어 분야/failure mode(세척밸리데이션·CSV·문서·시설 등)로 요약**하나? (= 2d-2b 구조화를 LLM이 대체)
- **Self-Check 관련 서술 제거**됐나(기능 폐지 반영)?
- `Signal Tier`/`Region`/`Language`로 우선순위·구획 활용? 국내 vs 글로벌 섹션 분리?
- 산출물: **Routine 변경 구체안**(추가/삭제 문구 수준).

## 3. 산출물 형식
1. 누락 소스 우선순위 표(A).
2. 버그/리스크 목록 + 심각도(B·C·D).
3. Routine 변경 구체안(E).
4. 개선 로드맵 P1/P2/P3(필수/권장/선택).
- **코드 변경은 제안만**. 실제 구현·push는 별도 단계(작성→교차검토→push). 프로덕션 Notion/DB 파괴적 변경 금지.

## 4. 역할 분담
- **Claude**(Notion MCP+repo): C(태깅/분석 품질, Notion 실측 쿼리), A(커버리지 갭), E(Routine 정합). 라이브 row 샘플 검수.
- **Codex**(repo+Git+로컬 실행): B(수집 정확성·카운트 대조·필터 과락), D(런타임/스크래핑/워크플로/시크릿). collector 로컬 dry-run으로 검증.
- 공통: `GRM_session_decisions.md` 먼저 정독, 결론은 위 산출물 형식으로.
