# GRM 운영 Runbook (v1.0)

> 목적: 운영자 1인 의존(Bus factor 1) 완화. 키·시크릿 관리, 정기 점검, 장애 대응,
> 데이터 누적 관리(아카이브 정책)를 한 문서로 정리해 백업 운영자가 이 문서만으로
> 시스템을 이어받을 수 있게 한다.
> 위치: `docs/ops_runbook.md` · 시스템 구조는 `GRM_SYSTEM.md` 가 기준 (이 문서는 운영 절차만).

---

## 1. 자격증명(Secrets) 인벤토리 · 로테이션

저장 위치: GitHub repo `MINHOYEOM/grm-api-intake` → Settings → Secrets and variables → Actions.
로컬 `.env` 는 개발용이며 git 비추적.

| Secret | 용도 | 발급처 | 만료/특이사항 | 로테이션 절차 |
|---|---|---|---|---|
| `NOTION_TOKEN` | Intake DB 적재 + handoff 생성 | notion.so/my-integrations (internal integration) | 명시 만료 없음. 단 integration 권한 회수·워크스페이스 이전 시 무효 | 새 토큰 발급 → 두 DB(Intake·Weekly Brief 부모 페이지)에 integration 연결 확인 → Secret 교체 → workflow_dispatch dry-run 으로 적재 확인 |
| `NOTION_DATABASE_ID` | Intake DB 식별 | 고정값 `7784c71f…` | DB 이동/재생성 시만 변경 | 변경 시 GRM_SYSTEM §2.2 도 갱신 |
| `DATA_GO_KR_SERVICE_KEY` | 식약처 회수(15059114)·행정처분(15058457)·GMP 적합판정(15097207)·안전성서한(15059182), 법제처 국가법령정보(15000115) API | data.go.kr 마이페이지 | **활용신청 기간 만료 있음** — 만료 시 401/403 | data.go.kr 로그인 → 활용 연장/재신청 → 키 동일하면 조치 불요, 재발급 시 Secret 교체 |
| `DATA_GO_KR_KEY` | 법제처 ogLmPp (현재 `ENABLE_MOLEG_API=false`) | data.go.kr | 비활성 — 만료돼도 운영 영향 없음 | 활성화 시점에 재확인 |
| `LAW_GO_KR_OC` | law.go.kr DRF 고시/행정규칙 본문 enrich | law.go.kr 국가법령정보센터 Open API | 선택. 미설정 시 법제처 목록 수집만 수행 | OC 등록/재발급 → Secret 교체 → `ENABLE_MFDS_LAW=true` dry-run |
| `MFDS_HTTP_PROXY` | MFDS/nedrug/law.go.kr KR-egress 프록시 | 운영자가 구성한 KR proxy/runner | 선택. 잘못된 프록시는 잔여 3종만 실패해야 함 | `probe_mfds_egress.py` 3종 HTTP 200 확인 후 Secret 교체 |
| `OPENFDA_API_KEY` | OpenFDA rate limit 상향(선택) | open.fda.gov | 없어도 동작(쿼터 축소) | 만료 시 무키 운영 가능 |
| `BRAVE_API_KEY` | Brave 보조검색 (현재 `ENABLE_SEARCH=false`) | brave.com/search/api | 비활성 | 활성화 시점에 재확인 |
| `NEWSLETTER_API_KEY` | Brevo 뉴스레터 발송 API (주간 Brief → 구독자 이메일) | app.brevo.com → SMTP & API → API Keys | 만료: 2027-06-30. 유출 시 즉시 재발급 | 재발급 → GitHub Secrets NEWSLETTER_API_KEY 교체 → dry-run 1회 |

만료 의심 신호: 수집 Issue 에 401/403 비일시 오류, 특정 소스만 연속 0건.
점검 우선순위: `DATA_GO_KR_SERVICE_KEY`(만료 존재) > `NOTION_TOKEN`(권한 회수형) > KR-egress 잔여 3종(`MFDS_HTTP_PROXY`/`LAW_GO_KR_OC`) > 나머지.

## 2. 정기 점검 (주간 5분)

매주 월요일 Routine 후:
1. GitHub Actions → `GRM API Intake (Daily)` 최근 7회가 모두 성공인지 확인. 실패 Issue/`GRM Intake 운영 경고` Issue 열람.
2. Weekly Brief 발행물에 대해 `GRM_Brief_Lint_실행프롬프트.md` 실행 (발행 후 독립 게이트).
3. Intake DB 를 `Run Date (KST)` 내림차순으로 열어 최신 row 가 오늘/어제인지 확인.
4. M2 메타에 "Status 갱신 실패" 또는 "주간 재유입 가드" 기록이 있으면 해당 doc_id 의 Status 를 수동 정리.

## 3. 장애 대응 빠른 분기

| 증상 | 1차 확인 | 조치 |
|---|---|---|
| 월요일 브리프 미발행 | Routine 실행 여부(수동/예약) | 수동 재실행. handoff 가 이미 CONSUMED 면 그 주는 빈 브리프가 정상(PL-10) |
| handoff 0건/없음 | Actions Job Summary 의 "Routine handoff" 라인 | 수집기 실패면 Issue 확인. Routine 은 WebSearch-only 로 자동 강등되므로 발행 자체는 가능 |
| 특정 소스 연속 0건(4주+) | 해당 collector 의 원 사이트 직접 확인 | 구조 변경이면 collector 수정 트랙 오픈. M2 기록과 대조 |
| Notion 적재 실패 | `NOTION_TOKEN` 권한, DB 연결 | §1 로테이션 절차 |
| data.go.kr 403/401 | 활용신청 만료 | §1 로테이션 절차 |
| MFDS 가이드라인/GMP 실태조사/law.go.kr 본문만 실패 | KR proxy 차단·proxy URL 오류·law.go.kr OC 문제 | `MFDS_HTTP_PROXY` 설정 상태에서 `python probe_mfds_egress.py` 실행. RSS/nedrug/law.go.kr 3종 200 확인 후 `MFDS_RSS_BOARD_MODE=residual` 유지 여부 점검 |
| 중복 카드 발견 | 전주 M2 의 Status 갱신 실패 기록 | PL-10b 가드 동작 여부 확인, 남은 New row Processed 처리 |

상세 판정 기준(failure/warning)은 GRM_SYSTEM §3.5 운영 모니터링 health check 참조.

## 4. Intake DB 아카이브 정책 (Notion-as-DB 누적 관리)

배경: Notion 을 DB 로 쓰므로 병목은 사용자 수가 아니라 row 누적(쿼리 페이지네이션·rate limit ~3rps)이다.
handoff 조회는 `Run Date 7일 + Status=New` 필터라 직접 영향은 작지만, dedup 조회(최근 30~1095일)와
수동 열람 성능은 누적에 따라 저하된다.

정책(권장 기본값):
- **보존**: `Status=New` row 는 기간 무관 보존(처리 대기 큐).
- **아카이브 대상**: `Status=Processed/Skipped` 이고 `Run Date` 가 **180일 경과**한 row.
  단 ICH·WHO 스냅샷 소스는 장기 dedup(1095일)이 row 존재를 전제로 하므로 **아카이브 제외**.
- **방법**: 분기 1회(1/4/7/10월 첫째 주) Notion 에서 해당 row 를 Archive (Notion archive 는 복구 가능).
  Raw payload 는 row 본문에 있으므로 아카이브해도 Notion 휴지통/복구 범위에서 보존된다.
- **Error/Needs Review row**: 아카이브 전에 반드시 수동 확인 후 처리.
- 자동화가 필요해지면 collector 에 maintenance 플래그(예: `ARCHIVE_PROCESSED_DAYS=180`)로 구현 — 별도 트랙.

## 5. 백업 운영자 인수인계 체크리스트

- [ ] GitHub repo `MINHOYEOM/grm-api-intake` collaborator 권한 (Actions·Secrets 접근)
- [ ] Notion `Global Regulatory Monitor` 부모 페이지 + 두 DB 편집 권한, integration 소유 확인
- [ ] data.go.kr 계정 또는 키 공유 방식 합의 (활용신청 명의 확인)
- [ ] `GRM_SYSTEM.md` §2(구성)·§3(흐름) 정독, 이 runbook §2 주간 점검 1회 동행 수행
- [ ] Routine 실행 방법 숙지: `docs/prompts/GRM_Prompt_v15.7.md` 를 새 채팅에 붙여넣어 실행
- [ ] Brief Lint 실행 방법 숙지: `docs/prompts/GRM_Brief_Lint_실행프롬프트.md`

## 📝 변경 이력
| 날짜 | 변경 내용 |
|---|---|
| 2026-06-30 | Brevo 뉴스레터 설정 완료 — NEWSLETTER_API_KEY Secret 등록, 변수 4개(GRM_NEWSLETTER_*) 등록 |
| 2026-06-18 | KR-egress 잔여 QA 3종 운영 항목 추가 — `MFDS_HTTP_PROXY`, `LAW_GO_KR_OC`, `probe_mfds_egress.py`, `MFDS_RSS_BOARD_MODE=residual` 점검 경로 |
| 2026-06-04 | 최초 작성 — Secrets 인벤토리·로테이션, 주간 점검, 장애 분기, Intake 아카이브 정책(180일), 인수인계 체크리스트 |
