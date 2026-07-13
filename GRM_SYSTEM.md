# GRM 시스템 명세서 (System Spec)

> **GRM = Global Regulatory Monitor.** 전 세계·국내(식약처) 제약 GMP·품질 규제 소식을 매일 자동으로 모아, 한국 제약사 QA 담당자가 읽기 쉬운 형태로 매주 웹사이트에 발행하는 자동화 시스템입니다.
>
> 이 문서는 저장소의 단일 시스템 명세서입니다(README 대체). **개발자가 아니어도 시스템의 큰 그림을 이해할 수 있도록** 앞부분(§1~§4)은 쉬운 말로, 뒷부분(§5~§6)은 개발 레퍼런스로 씁니다.

| 문서 메타 | 값 |
|---|---|
| 문서 버전 | `v1.123` |
| 최종 수정일 | 2026-07-13 |
| 현재 상태 | 매일 자동 수집·주간 자동 발행 가동 중 — **2026-07-13 자동화 전수 정비 완료: 매주 사람 개입 = Admin 승인 1클릭 유일**(심층분석 클라우드 생성 실전 검증 완료·발송 2종 무승인 자동·크론 이중화, 상세 = `docs/GRM_자동화지도_2026-07.md`). 웹사이트(`grm-solutions.com`)가 주 발행 채널. **Findings 인텔리전스(FIND-1) M1~M14 완료·라이브**에 이어 전략 로드맵 F2(볼륨)~F4a(에이전트 자산)까지 진행: 외부 백필 자동 파이프라인 가동 중(findings 2,775건+·업체 428곳·매일 증가), 트렌드 대시보드(`/findings/trends/`) 라이브, Copilot Studio 커넥터 자산 완료(파일럿 대기). |
| 코드 저장소 | https://github.com/MINHOYEOM/grm-api-intake |
| 웹사이트 | https://grm-solutions.com (브리프 `/`·`/archive/`, 지적사항 검색 `/findings/`) |
| 변경 이력 | 상세 이력은 **git 로그**로 확인합니다. 이 문서는 "현재 상태"만 유지하고, 오래된 단계별 기록은 남기지 않습니다. |

---

## 0. 이 문서를 쓰는 법 (유지 규칙)

이 문서는 **"살아있는 명세서"** 입니다. 시스템이 바뀌면 함께 갱신하되, **현재 상태를 간결하게 유지**하는 것이 최우선입니다.

- **큰 변경만 반영한다.** 새 소스·새 단계·데이터 흐름 변경·파일 구조 변경 같은 "구조적 변화"만 씁니다. 자잘한 버그·문구 수정은 git 커밋으로 충분합니다.
- **누적하지 말고 갱신한다.** 과거 단계별 기록을 계속 쌓지 않습니다. 낡은 내용은 **과감히 삭제**하고 현재 사실로 대체합니다. 상세 이력이 필요하면 git 로그를 봅니다.
- **파일·폴더가 바뀌면 §5.1 폴더 구조를 함께 갱신합니다.**
- 상단 "문서 메타"의 버전·수정일을 같이 갱신합니다.

---

## 1. 한눈에 보기

### 1.1 무엇을 · 왜 · 누구를 위해
GRM은 FDA·EMA·MHRA·PIC/S·ICH·WHO·Health Canada·식약처(MFDS) 등 **흩어져 있는 규제 소식을 한 곳에 자동으로 모읍니다.** 매주 사람이 일일이 확인하기엔 양이 많고 영문 원문도 부담이라, GRM이 이 모니터링을 자동화하고 핵심을 한국어로 요약하되 **원문 링크를 항상 함께** 제공합니다.

**대상 사용자:** 한국 제약사의 QA(품질보증) 담당자. 특정 제형이 아니라 **화학합성의약품·생물의약품·기타** 3분류로 의약품 전반을 봅니다(의료기기 제외).

### 1.2 두 개의 트랙 (핵심 개념)
같은 원재료(매일 수집한 규제 문서)에서 **완전히 다른 두 가지 제품**이 나옵니다. 이 둘은 서로 데이터를 주고받지 않는 독립 트랙입니다.

```mermaid
flowchart TD
    S[매일 자동 수집<br/>규제 소스 13종]
    S --> A[트랙 A · 주간 브리프]
    S --> B[트랙 B · Findings 검색]
    A --> A1["이번 주 규제 동향을<br/>카드형 다이제스트로 재구성<br/>(사람이 읽는 뉴스레터)"]
    A1 --> A2["웹사이트 / · /archive/<br/>+ 이메일 뉴스레터"]
    B --> B1["규제 문서 안의 개별 위반사항을<br/>기계적으로 추출·분류<br/>(검색하는 자료실)"]
    B1 --> B2["웹사이트 /findings/<br/>기관·카테고리·기간 검색"]
```

| | **트랙 A — 주간 브리프** | **트랙 B — Findings 검색 (FIND-1)** |
|---|---|---|
| 무엇 | "이번 주 규제 뉴스 요약 신문" | "모든 위반사항을 검색하는 자료실" |
| 단위 | 사건 1건 = 카드 1장 | 문서 안의 개별 위반사항 1건씩 |
| 발행 | **매주** 한 번에 묶어 발행 | 매일 계속 쌓이는 **실시간 DB** |
| 읽는 법 | 처음부터 읽는 다이제스트 | 조건으로 검색·필터 |
| 저장 | Notion → 웹 정적 사이트 | Supabase(Postgres) → 웹 |
| 웹 위치 | `/`, `/archive/` | `/findings/`, `/findings/trends/`(전량 집계 대시보드) |

### 1.3 핵심 설계 원칙
- **원문 우선·추적 가능:** 모든 카드/항목에 정보 출처와 공식 원문 링크를 붙입니다. 요약·번역이 있어도 법적 판단은 항상 원문 기준.
- **사실과 해석의 분리:** 객관적 사실과 AI 해석을 시각적으로 구분합니다.
- **신뢰도 등급화:** Evidence Level(A/B/C)과 Signal Tier(1/2/3)를 표기합니다.
- **장애에 강하게:** 수집기 하나가 실패해도 나머지는 계속 동작합니다.
- **완성본만 공개:** 미완성(예: 번역 안 된 항목)은 DB 레벨에서 웹 노출을 차단합니다.

---

## 2. 시스템 구성

무거운 서버 없이 **GitHub Actions(연산) + Notion·Supabase(저장) + Claude(분석·생성) + Cloudflare(웹 호스팅)** 를 조합한 구조입니다.

| 계층 | 역할 | 기술 / 위치 |
|---|---|---|
| ① 수집 | 규제 소스 13종에서 원시 데이터 수집 | Python 3.12 (`requests`, `PyMuPDF`) |
| ② 실행·스케줄 | 수집기를 정시 자동 실행 | GitHub Actions (cron) |
| ③ 저장 | raw 데이터 + 분류 태그 적재 | Notion `GRM API Intake` / Supabase |
| ④ 분석·생성 | 신호를 읽어 카드형 다이제스트로 가공 | 클라우드 Claude Routine |
| ⑤ 발행 | 완성본을 사람에게 노출 | **웹 정적 사이트**(주 채널) + 이메일 |

### 2.1 계층별 요약

**① 수집 — Python 수집기.** 가벼운 순수 Python. 외부 의존성은 HTTP 클라이언트(`requests`)와 PDF 파서(`PyMuPDF`, 식약처 실태조사 PDF용)뿐. 공통 HTTP 로직(재시도·429 백오프·파싱)은 `grm_common.py`가 공유합니다.

**② 실행·스케줄 — GitHub Actions.** `grm-intake.yml`(이름 `GRM API Intake (Daily)`)이 **매일 18:17 UTC(= 03:17 KST)** 자동 실행. 실행 말미에 health check를 판정해 실패는 GitHub Issue로, 경고는 누적 comment로 남깁니다. 비밀값은 GitHub Secrets에만 보관합니다.

**③ 저장 — Notion `GRM API Intake` (staging).** 수집한 모든 항목이 1차로 쌓이는 임시 DB. 각 행에 분류 태그가 붙고, 본문에 원본 API 응답 JSON 전체를 보존합니다(재검증용). 트랙 B(Findings)는 여기에 더해 **Supabase**에도 적재됩니다(§4).

**④ 분석·생성 — 클라우드 Claude Routine.** 매주 월요일, 클라우드 "Routines" 제품("Global Regulatory Monitoring [GRM]", 커넥터=Notion)이 프롬프트(`docs/prompts/GRM_Prompt_v16.md`)에 따라 자동 실행됩니다. 수집기가 만든 카드 골격(handoff)을 받아 카드별 산문 슬롯을 채우고, deep-ready 카드(WL·행정처분·483)가 있으면 **심층분석 4섹션도 직접 생성**합니다(검증은 델타 브릿지의 `verify_deep_analysis` 게이트가 담당 — 생성/검증 역할 분리). WebSearch(이벤트 탐지)·WebFetch(보조 출처 흡수)를 씁니다.

**⑤ 발행 — 웹 정적 사이트 + 이메일 (주 채널).**
- **웹:** `web/render.py`(순수·결정론)가 정적 멀티페이지 사이트를 생성 → `grm-web-deploy.yml`이 Cloudflare Pages로 배포. production 머지만 라이브(사람 승인 게이트).
- **웹 발행 조립:** Routine이 만든 델타(슬롯만)를 빈슬롯 스캐폴드(전 수집 카드)와 `assemble_publish_brief.py`가 합쳐 **채택분만** 남기고 메타(기관·카테고리·커버리지)를 재계산합니다. "수집 89 · 카드 61" 같은 표기의 근거.
- **뉴스레터:** 회원 없이 구독(Brevo SaaS)하고, 그 호 요약 + 웹 링크를 담은 티저 메일을 `grm-newsletter-send.yml`이 **매주 월 14:00 KST 자동 발송**합니다(Admin 콘솔 수동 실행 병행). 발송 게이트 3겹(발행검증·링크체크=발송 보류 게이트·멱등 캠페인명) — environment 사람 승인은 관심업체 통지(`grm-watchlist-notify.yml`)에만 적용됩니다.
- **Admin 콘솔:** `/admin` 단일 페이지 운영 UI(Supabase Edge Function 호출). 주간 운영자는 여기서 미리보기 확인 후 **승인 버튼 하나**만 누릅니다.
- **참고 — Notion `🌐 GRM Weekly Brief` DB는 레거시:** 예전 발행 채널이었으나 2026-06-22 이후 웹 파이프라인으로 대체됨(신규 쓰기 중단, 과거 페이지만 보존).

### 2.2 저장소별 데이터베이스

| DB | 트랙 | 역할 |
|---|---|---|
| Notion `GRM API Intake` | 공용 | 수집 staging(기계 적재) |
| Notion `🌐 GRM Weekly Brief` | A(레거시) | 과거 발행물(신규 쓰기 중단) |
| Supabase `grm-reactions` | B + 웹반응 | `raw_signals`·`findings` 테이블(§4) + 웹 하트/스크랩 반응 |

---

## 3. 작동 방식 (트랙 A · 주간 브리프)

### 3.1 데이터 흐름

```mermaid
flowchart TD
    A[GitHub Actions grm-intake<br/>매일 03:17 KST] --> B[Python 수집기 13종]
    B --> C[(Notion GRM API Intake<br/>raw 적재 + handoff v2 + 빈슬롯 스캐폴드)]
    C --> D[클라우드 Claude Routine<br/>매주 월 07:30 KST]
    D --> E[6슬롯 산문 + 심층분석 생성<br/>→ Notion web-delta 행 예치·자기검증]
    E --> F[grm-delta-bridge 월 09:30·12:30<br/>델타 git 커밋 — deep은 근거 게이트 통과분만]
    F --> G[grm-web-publish 자동 조립<br/>발행 PR publish/brief-날짜]
    G --> H[grm-web-deploy<br/>Cloudflare 프리뷰 URL]
    H --> I{{사람: Admin 승인 1클릭<br/>= 유일한 사람 개입}}
    I --> J[main 머지 → 라이브<br/>grm-solutions.com]
    J --> K[뉴스레터 월 14:00 · 관심업체 알림 월 10:30<br/>무승인 자동 발송 + 발행 후 감사]
```

### 3.2 주간 발행 생애주기 — 단계별 실행 장치

"매주 월요일 카드가 웹사이트에 올라가기까지" 각 단계가 실제로 무엇으로 실행되는지의 단일 기준입니다. **①~⑤·⑦은 전부 클라우드에서 실행**되어 사람 컴퓨터가 꺼져 있어도 진행됩니다. **매주 사람의 역할은 ⑥(승인 버튼 1클릭) 하나로 수렴합니다**(관심업체 통지도 2026-07-13부터 무승인 자동). 전 자동화의 상세 인벤토리(트리거·시크릿·실패모드·월요일 타임라인)는 `docs/GRM_자동화지도_2026-07.md` 참조.

| # | 단계 | 실행 장치 | 트리거 | 사람 |
|---|---|---|---|---|
| ① | 매일 수집 + handoff v2 + 빈슬롯 스캐폴드 artifact | `grm-intake.yml` | cron 매일 03:17 KST | 없음 |
| ② | 월요일 카드 분석 → 슬롯 델타 + **심층분석 생성** → Notion 예치(자기검증) | 클라우드 Routine | 매주 월 07:30 KST | 없음 |
| ③ | 델타를 git 이관(deep는 `verify_deep_analysis` 게이트 통과분만) | `grm-delta-bridge.yml` | cron 월 09:30 KST(PAT push) | 없음 |
| ④ | 스캐폴드+델타 → 발행본 조립 → 캐노니컬 브랜치 `publish/brief-{date}` PR | `grm-web-publish.yml` | ③ 델타 커밋(PAT라 자동 트리거) | 없음 |
| ⑤ | 발행본 렌더 → 미리보기 URL | `grm-web-deploy.yml` | ④ PR 생성 | 없음 |
| ⑥ | **미리보기 확인 후 승인 = PR 머지** | Admin 승인 버튼 → `admin-github`(check-runs green 게이트) | 사람 클릭 | **있음(유일)** |
| ⑦ | production 반영 → 라이브 | `grm-web-deploy.yml` | ⑥ 머지 | 없음 |
| 보조 | 델타 부재 감지 → 경보 | `grm-publish-watchdog.yml` | cron 월 10:00 KST | 없음 |
| 보조 | 발행 준비 검증+가속(클라우드 산출 확인·부재 시만 백필) | 로컬 태스크 `grm-monday-brief-publish` | 월 09:05 KST(데스크톱 ON 전제) | 없음 |
| 보조 | 뉴스레터 자동 실발송(멱등·새 호만) | `grm-newsletter-send.yml` | cron 월 14:00 KST | 없음 |
| 보조 | 관심업체 통지 자동 발송(멱등 로그·상한) | `grm-watchlist-notify.yml` | cron 월 10:30 KST | 없음 |
| 보조 | 발행 후 provenance 감사 | `grm-brief-audit.yml` | cron 월 11:00 KST + 발행 머지 직후 | 없음 |

**사람 개입 지점은 정확히 3개다**: ⓐ claude.ai Routine 주간 스케줄(월 07:30 KST)이 활성인지 주기 확인(세션·코드에서 접근 불가 — 사람만 볼 수 있음) ⓑ 월요일 낮 **Admin 승인 1클릭**(프리뷰 확인 후 머지 = 라이브) ⓒ 백업 레이어를 쓰는 주라면 월요일 아침 데스크톱 ON(로컬 예약 태스크 전제). 이 밖의 모든 단계는 무인이며, 각 단계의 방어선·멱등성은 `docs/GRM_자동화지도_2026-07.md` §7~§9 참조.

### 3.3 핵심 개념
- **Signal Tier(신호 강도):** Tier 3(우선 카드화·고위험) / Tier 2(참고) / Tier 1(로그만).
- **Evidence Level(근거 등급):** A(1차 공식문서 직접 확인 — 원문 인용 허용) / B(공식 인덱스+보조) / C(보조 단독) / D(예정·Watch).
- **듀얼 링크:** 모든 카드에 정보 출처(📰) + 공식 원본(📎). 모든 링크는 수집 근거(provenance)가 있어야 하며, 근거 없는 링크는 **발행 차단**(발행 전 게이트 + 발행 후 감사 이중 방어).
- **Graceful degradation:** 수집기·Notion 장애로 handoff가 없어도 Routine은 WebSearch 단독 모드로 계속 동작.

### 3.4 수집 대상 소스 (기본 8 + 글로벌 확장 3 + FDA 483 + ISPE)

| # | 소스 | 채널 | 상태 |
|---|---|---|---|
| 1 | Federal Register (FDA 규칙·고시) | 공식 API | 운영 |
| 2 | OpenFDA Drug Enforcement (회수) | 공식 API | 운영 |
| 3~6 | EMA · MHRA · PIC/S · ECA Academy | RSS | 운영 |
| 7 | FDA Warning Letters | 웹 스크래핑 | 운영 (부서 노이즈 필터 적용) |
| 8 | MFDS 식약처 (지침·고시·법령·안전성서한·행정처분·회수·GMP 실태조사·적합판정) | RSS + data.go.kr API + nedrug | 운영 (일부 opt-in) |
| 9 | ICH (가이드라인·공개협의) | 스냅샷 + Routine 검색 | 활성 |
| 10 | WHO Prequalification (WHOPIR 실사보고서 등) | RSS + Drupal | 활성 |
| 11 | Health Canada (약품 recall·safety) | 오픈데이터 JSON | 활성 |
| 12 | FDA 483 (실사 Observation = 가장 깊은 결함 원본) | OII FOIA Reading Room + PDF | 활성 |
| 13 | ISPE iSpeak (전문지 브리핑 — GMP/품질 관련 항목만 keep_item 필터) | RSS(Drupal) | Expert Secondary · `ENABLE_ISPE`(기본 off) |

> **검토 후 제외:** TGA(WAF 차단·PIC/S로 커버), PMDA(공개 per-event 결함 피드 없음·일본어 전용).

### 3.5 운영 모니터링 (health check)
수집기 실행 말미에 `_evaluate_health()`가 단일 기준으로 판정합니다.
- **Failure(exit 1 + 실패 Issue):** Notion insert 실패, handoff 실패, Federal Register+OpenFDA 동시 실패, 활성 소스 전체 실패 등.
- **Warning(exit 0 + 경고 Issue 갱신):** 공개 endpoint 일시 오류(timeout·429·5xx·403), GMP 첨부 수동검토 필요, 미소비 New 행 잔존 등. 경고 구성이 바뀔 때만 알림(노이즈 억제).
- **0건 판정:** 저빈도 소스의 일일 0건은 정상으로 봅니다.

---

## 4. Findings 인텔리전스 (트랙 B · FIND-1)

### 4.1 무엇인가
트랙 A(브리프)가 "이번 주 뉴스"를 사람이 읽게 재구성한다면, **트랙 B는 규제 문서 안의 개별 위반사항(finding)을 하나씩 기계적으로 추출해 검색 가능한 데이터베이스로 쌓습니다.** 예: FDA 483 실사보고서 한 건 안의 "Observation 1: 작업자가 청정구역에서 빠르게 이동함" 같은 개별 지적을 각각 한 행으로 저장하고, 기관·카테고리·증거등급·기간으로 검색하게 합니다. 웹사이트 `/findings/`가 이 검색 화면입니다.

### 4.2 자동 파이프라인
매일 수집분에서 findings를 뽑아 Supabase에 쌓고, 미번역 항목은 웹에 안 보이게 막았다가, 매주 자동으로 번역해 공개하는 전 과정이 자동입니다. **"번역(AI 판단)"과 "DB 쓰기(기계적 실행)"를 분리**해, AI가 프로덕션 DB에 직접 쓰지 않으면서도 무인 자동화를 달성합니다.

```mermaid
flowchart TD
    A[매일 수집 → findings 추출<br/>자동 분류·카테고리] --> B[(Supabase findings)]
    Z[외부 백필 F2<br/>collect_fda_backfill.py<br/>매일 07:17 UTC --auto] --> B
    B --> C{공개 게이트 RLS<br/>국문 번역 있음?}
    C -->|없음| D[웹 비공개<br/>미완성 노출 차단]
    C -->|있음| E[웹사이트 /findings/ 공개]
    D -. 매주 월요일 예약 .-> F[예약 Claude 세션<br/>번역·검증·PR 자동 머지]
    F --> G[GitHub Actions<br/>LLM 없는 순수 스크립트가<br/>Supabase에 번역 반영]
    G --> B
    B --> H[(집계 RPC 007/008<br/>findings_stats 등)]
    H --> I[웹사이트 /findings/trends/<br/>전량 집계 대시보드]
```

- **매일 적재(M4):** `collect_intake.py`가 Notion 적재 성공분을 Supabase `findings`에 직행 append(기본 off 플래그, 현재 활성).
- **외부 백필(F2):** `collect_fda_backfill.py`(+ `grm-findings-backfill-fetch.yml`)가 FDA 483(총 2,002건)·Warning Letter(3,608건, 2021년~)의 과거분을 Notion을 우회해 Supabase에 직행 적재. robots.txt `Crawl-Delay: 30`초를 완전 준수하며, 매일 07:17 UTC cron이 `--auto` 모드로 1청크씩 소진합니다(483→WL 순, 완료되면 자가 종료 후 신규 문서 2차 안전망으로 전환). MFDS는 nedrug robots `Disallow: /` 정책 판단에 따라 백필 대상에서 제외(일일 수집만 유지). 스케줄 배치 번역은 주당 최대 40건·최신 우선이라 백필 미번역분은 당분간 웹 row로는 안 보이고 집계로만 소비됩니다.
- **공개 게이트(M9):** `findings_public_read` RLS 정책이 `국문 번역 있음 또는 원문이 한국어`인 행만 anon(공개)에 노출. 미번역은 DB가 원천 차단.
- **주간 번역(M8·M9):** 매주 월요일 예약된 Claude Code 세션(구독 사용량·API 비용 0)이 미번역분을 추출→번역→검증→PR 자동 머지. 머지되면 `grm-findings-translate-apply.yml`(LLM 미관여 순수 스크립트)이 Supabase에 반영.
- **웹 표시(M6·M7·M10):** `/findings/`가 국문 우선 + 원문 접기 + 대시보드(기관·카테고리·기간·업체 통계)를 제공. M10 검증 브랜치에서는 카드 기본 접힘(3줄 요약→자세히 보기), 칩 필터(건수 병기), 정렬, 매칭어 하이라이트, 검토 필요 시각 경계, URL 쿼리 공유, 화면표시값 검색을 보강했다.

### 4.3 데이터 계약
- **`raw_signals`** (원본 보존층): 재추출 가능한 원본. `raw_json`은 원문 byte 그대로 보존해 해시 재검증 가능. **비공개**(service_role 전용).
- **`findings`** (지적사항 분석층): FDA 483 Observation·Warning Letter·MFDS GMP 지적 등에서 정규화한 개별 위반. `finding_text`(영문 원문·불변) + `finding_text_ko`(국문 해석) + 카테고리·증거등급·검토상태. **공개 게이트 통과분만 노출.**
- **taxonomy v4:** 20개 카테고리(코드·한국어·영문 라벨 고정, v1~v4 전부 불변). 분류기는 단어경계 키워드 매칭 + 카테고리별 선택적 명시 정규식(`patterns`, 부정어/활용형/유연 인접 표현)을 병행한다. v3(2026-07-12 층화 감사, 실질 정확도 71%→89%)가 범용 "written procedure" 가로채기·"computer system" 경직 구문·"non-sterile" 부정어 오탐 등 구조적 오분류를 고쳤고, v4(동일 감사의 사후 재감사)는 잔여 wrong 9건을 근거로 ①확인된 2개 OCR 오탈자 혼동쌍 한정 내성(quality/quaJity, sterile/sterih) ②캐치올로 새는 CFR 조항 어휘 보강(연차제품검토·보류샘플·스모크스터디) ③원자재 샘플링/CPV 어순변형 보강을 추가했다(`findings_reclassify_service.py`가 라이브 재분류 담당, workflow_dispatch 전용, LLM 0). `finding_id`는 내용 해시 기반 안정 ID(번역·재분류로 안 바뀜).
- **번역 도구(`findings_translate.py`):** `--source {sqlite,supabase}` export/apply. 적용 시 원문 byte 대조 all-or-nothing 검증(원문 변조·미번역·번역=원문 동일 등 거부).
- **집계 RPC(007/008, 카운트 전용 안전 계약):** `findings_stats`/`findings_firm_stats`(007)·`findings_category_matrix`(008)는 공개 게이트(006)를 우회해 **전량** 집계(건수·연도·카테고리·업체 통계)를 서빙하되, 원문 텍스트(`finding_text`/`finding_text_ko`)는 어떤 경로로도 반환하지 않습니다. `/findings/trends/`가 이 두 RPC만 소비.

### 4.4 구현 마일스톤 요약 (M1~M14, 전부 완료·라이브)

> **번호 체계 주의:** 아래 M1~M14는 **구현 작업 단위**(스키마→적재→UX→품질개선 순으로 세션마다 이어 붙인 번호)이며, §6.4의 **전략 로드맵 M1~M5**(공모전 5개월 계획)와는 다른 체계다. 이 표 전체가 전략 로드맵의 "M1(스키마+내부 백필)" 한 칸에 해당한다.

| 단계 | 내용 |
|---|---|
| M1 | `raw_signals`·`findings` 스키마 동결 + 7월 내부 백필(raw 112 / findings 24) |
| M2 | 로컬 read-only 조회 계층 + 정적 검색 export/뷰어 |
| M3 | Supabase 적재(Postgres) + `/findings/` 웹 검색 페이지 라이브 |
| M4 | 매일 수집분 Supabase 직행 자동 적재(1·2단계 모두 활성) |
| M5 | taxonomy v2(단어경계 분류기·v1/v2 이중 수용 마이그레이션) |
| M6 | 국문 번역 + 웹 국문 우선 표시·카테고리 영문 병기 |
| M7 | `/findings/` 대시보드 밴드(필터 연동 분포·추이·업체 통계) |
| M8 | 번역 도구 라이브 소스 모드(`--source supabase`) |
| M9 | 공개 게이트(RLS) + 주간 번역 완전 자동화(예약 세션 + outbox 워크플로, git 쓰기 없는 CI 반영) |
| M10 | `/findings/` 탐색 UX 오버홀(483 페이지헤더 잡음 스크럽·카드 접힘·매칭어 하이라이트·검토 필요 시각 경계·칩 필터·정렬·모바일 접기·URL 동기화) |
| M11 | Warning Letter 벽텍스트를 개별 위반으로 분해 + 법조항(CFR/U.S.C./FD&C) 추출 강화 |
| M12 | findings 백필 도구(수집은 됐지만 findings 미변환된 raw_signals 소급 적재, 재사용 가능한 CI 경로) |
| M13 | 신뢰도 UX 분리(Evidence 배지 vs 검토 필요 배지 시각 구분) + 번역 반영 오류 표면화 |
| M14 | `/findings/` 디자인 전면 정돈(전문 SaaS 수준 — 신호색 절제·위계 정리·모바일 오버플로 제거) |

### 4.5 트렌드 대시보드 (`/findings/trends/`, F3b·H1)
`web/templates/trends.html` + `web/assets/trends.js`가 007/008 집계 RPC를 직접 fetch해 그리는 전량 통계 페이지입니다. 스탯 스트립·카테고리 상위 10 바·카테고리×연도 히트맵(008)·연도별 추이·업체 Top 30(클릭 시 `?firm=` 상세 패널·URL 공유)·증거등급/소스 구성을 보여줍니다. 공개 게이트(006)와 무충돌 — 카운트·서지 메타만 반환하고 원문은 어떤 경로로도 내려주지 않습니다(§4.3). 렌더러는 빈 셸만 결정론적으로 출력하고 실데이터는 클라이언트 JS가 채웁니다(env 미설정 시 "준비 중" 안내로 조용히 종료).

> **운영 지위:** 신규 유입분의 정본은 Supabase. 로컬 SQLite sidecar(`grm-findings.sqlite3`)는 7월 백필 스냅샷 + 로컬 개발용. **findings 2,775건+·업체 428곳**(F2 외부 백필 진행 중, 매일 증가) — §6.4의 F2(볼륨) 단계가 가동 중이며, row 단위 공개는 주당 번역 상한(최대 40건)에 따라 점진적으로 늘어나고 집계(트렌드 대시보드)는 이미 전량을 반영합니다.

---

## 5. 개발자 레퍼런스

### 5.1 저장소 폴더 구조 (요약)
코드(`.py`)는 루트에 평면 배치합니다(`collect_intake.py`가 같은 폴더 모듈을 이름으로 import하므로 하위 폴더 이동 금지).

```
grm-api-intake/
├─ GRM_SYSTEM.md, CLAUDE.md, requirements.txt
├─ collect_intake.py               # 수집 오케스트레이터(단일 진입점)
├─ collect_mfds*.py                # 식약처 수집기(recall/admin/gmp_inspection/law/gmp_cert/safety_letter)
├─ collect_ich.py, collect_who.py, collect_hc.py, collect_fda_483.py, collect_search.py
├─ collect_fda_backfill.py         # [FIND-1 F2] FDA 483·WL 외부 백필(Notion 우회, Supabase 직행)
├─ grm_common.py                   # 공통 HTTP·유틸
├─ grm_notion.py, grm_handoff.py   # Notion 적재 · handoff 멱등성
├─ card_scaffold.py, inject_slots.py, assemble_publish_brief.py, delta_bridge.py
├─ brief_lint.py, verify_published_brief.py, verify_deep_analysis.py, deep_analysis_fanout.py
├─ grm_findings.py                 # [FIND-1] 스키마 계약·taxonomy·validator·SQLite DDL
├─ findings_extractors.py          # raw_signal → findings 변환
├─ findings_store.py, findings_views.py
├─ findings_supabase.py, findings_supabase_append.py   # Postgres DDL/로드 · 직행 append
├─ findings_translate.py, findings_translate_apply_service.py  # 번역 export/apply · CI 반영
├─ findings_search_export.py, findings_search_page.py  # 검색 export · 오프라인 뷰어
├─ findings_backfill*.py, findings_notion_export.py    # 백필 도구(M12, 내부 소급 적재)
├─ findings_taxonomy_migrate_sqlite.py, findings_translation_migrate_sqlite.py  # sidecar 마이그레이터
├─ findings_reclassify_service.py  # taxonomy 라이브 재분류(현재 v4, workflow_dispatch 전용, LLM 0)
├─ web/
│  ├─ render.py, linkcheck.py, newsletter.py
│  ├─ templates/  (landing·archive·brief·findings·trends·me·admin·base)
│  ├─ assets/  (grm.css·archive.js·findings.js·trends.js·reactions.js·admin.js)
│  ├─ migrations/  (001_reaction ~ 010_findings_scope_purity, 011_findings_taxonomy_v3.sql, 012_findings_taxonomy_v4.sql, 013_findings_firm_key.sql)
│  ├─ data/  (briefs·deltas)  ·  partials/  ·  tests/  (render 골든, trends.expected.html 포함)
├─ translations/outbox/            # [FIND-1 M9] 주간 번역 배치 큐(CI가 읽어 Supabase 반영)
├─ tests/                          # unittest + pytest (golden·fixtures 포함)
├─ docs/  (prompts/·specs/ 포함)
│  ├─ GRM_자동화지도_2026-07.md          # 전 자동화 인벤토리·데이터계약·실패모드·월요일 타임라인
│  └─ copilot/  (grm_findings_connector.swagger.json, COPILOT_SETUP_GUIDE.md, QA_SCENARIOS.md)  # [FIND-1 F4a]
└─ .github/workflows/
   ├─ grm-intake.yml, grm-ci.yml
   ├─ grm-web-deploy.yml, grm-web-publish.yml, grm-delta-bridge.yml, grm-publish-watchdog.yml
   ├─ grm-newsletter-send.yml, grm-watchlist-notify.yml, grm-admin-backend-deploy.yml
   ├─ grm-brief-audit.yml, grm-reconciliation.yml, grm-supabase-keepalive.yml
   ├─ grm-findings-translate-apply.yml   # [FIND-1 M9] 번역 outbox → Supabase 반영
   ├─ grm-findings-backfill-fetch.yml    # [FIND-1 F2] 외부 백필 매일 07:17 UTC cron(--auto)
   ├─ grm-findings-backfill.yml          # [FIND-1 M12] 내부 소급 적재(workflow_dispatch 전용)
   └─ grm-findings-reclassify.yml        # taxonomy 재분류(workflow_dispatch 전용, dry_run 기본 true, 버전 무관 재사용)
```

### 5.2 주요 실행 파일
- **수집 진입점:** `collect_intake.py` (워크플로가 호출하는 유일 파일).
- **Routine 프롬프트:** `docs/prompts/GRM_Prompt_v16.md` (내부 버전 v16).
- **웹 렌더러:** `web/render.py` (순수·결정론, 골든 테스트로 고정).
- **Findings 번역 루프:** `findings_translate.py`(export/apply) + `findings_translate_apply_service.py`(CI 반영).

### 5.3 비밀값(Secrets) · 기능 플래그(Variables)
**Secrets:** `NOTION_TOKEN` · `NOTION_DATABASE_ID` · `OPENFDA_API_KEY`(선택) · `BRAVE_API_KEY` · `DATA_GO_KR_SERVICE_KEY` · `MFDS_HTTP_PROXY`(선택) · `LAW_GO_KR_OC`(선택) · `CLOUDFLARE_*`(웹 배포) · `NEWSLETTER_API_KEY`(Brevo) · `SUPABASE_URL`(vars) · `SUPABASE_SERVICE_ROLE_KEY`(findings 적재·번역 반영, admin 배포와 공용).

**주요 기능 플래그 (`vars.ENABLE_*`, 운영 기본):**

| 플래그 | 상태 |
|---|---|
| `ENABLE_MFDS` / `_RECALL` / `_ADMIN` / `_GMP_INSPECTION` | `true` |
| `ENABLE_ICH` / `_WHO` / `_HC` (글로벌 확장) | `true` |
| `ENABLE_FDA_483` | `true` |
| `ENABLE_MODALITY_TAG` | `true` |
| `ENABLE_MFDS_LAW` / `_GMP_CERT` / `_SAFETY_LETTER` (공식 API opt-in) | `false` |
| `ENABLE_SEARCH`(Brave) / `_SCRAPE` / `_MOLEG_API` | `false` |
| `ENABLE_FINDINGS_SUPABASE_APPEND` (M4 raw 적재) | `true` |
| `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND` (M4 findings 적재) | `true` (2026-07-08 활성) |
| `ENABLE_FINDINGS_SQLITE_APPEND` / `_FINDINGS_APPEND` (로컬 개발용) | `false` |

> 운영 기본값은 `grm-intake.yml`의 `vars.* || 'true/false'` fallback으로 정해집니다.

### 5.4 이 저장소 작업 환경 메모
- Python 전체경로: `C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe` (`python` 별칭은 스텁이라 미동작). pytest 9.1.1.
- 실 repo worktree = `v15.0-implementation/`. FIND-1 작업 worktree = `_wt-findings-m1/`. 작업 전 `git fetch origin main` 먼저.
- 라이브 Supabase 쓰기(마이그레이션·데이터)는 세션 권한 게이트상 사람이 SQL Editor에서 실행하거나 CI가 수행합니다(대화 세션 직접 쓰기 회피).

---

## 6. 로드맵 & 잔여 작업

### 6.1 단계 이력 (Phase)
| Phase | 목표 | 상태 |
|---|---|---|
| Phase 1 | 기반 구축(FR + OpenFDA → Notion, GitHub Actions 자동화) | ✅ 완료 |
| Phase 2a | 글로벌 다소스(EMA·MHRA·PIC/S·ECA + FDA WL) | ✅ 완료 |
| Phase 2b | 국내 MFDS(RSS + 행정처분·회수·GMP 실태조사) | ✅ 완료·운영 |
| Phase 3 | 글로벌 심화(ICH·WHO·Health Canada) | ✅ 완료·활성 |
| Phase 4 | 제품군 확장(경구 고형제 → 의약품 전반 3분류) | ✅ 완료·운영 |
| 웹/뉴스레터 | 웹 발행·뉴스레터·Admin 콘솔·클라우드 자동화 | ✅ 완료·라이브 |
| **FIND-1** | Findings 인텔리전스 — 스키마·자동적재·검색·번역자동화·UX(M1~M14) | ✅ 완료·라이브 |

### 6.2 잔여 작업 (OPEN)
| ID | 내용 | 상태 |
|---|---|---|
| FIND-483-SIGNER | FDA 483 실사관(서명자) 추출기 미구현 — `inspector_names` 전량 빈값. F3 실사관 프로파일의 선결 조건 | 🔲 이월 |
| FIND-FIRM-ALIAS | 업체명 표기·별칭 정규화(트렌드 업체 랭킹·상세 패널 정확도 개선) — 백엔드(`firm_key` generated 컬럼 + `grm_normalize_firm_name`/`findings_firm_profile` RPC, `013_findings_firm_key.sql`, 실측 982→855 업체 수렴) PR 제출·라이브 DB 미적용. 업체 프로파일 웹 페이지는 후속 PR | 🟡 백엔드 구현 완료(검수 대기) |
| MIGRATION-008 | `008_findings_category_matrix.sql` 라이브 DB 적용 대기(사람이 Supabase SQL Editor에서 실행) — 미적용 상태에서도 웹은 폴백으로 무장애 | 🟡 적용 대기 |
| FIND-WL-BACKFILL | WL 백필(3,608건, 2021년~) 완주 관찰 — 매일 07:17 UTC `--auto` 로 진행 중, 완료 시 자가 종료 확인 | 🟡 관찰 대기 |
| ROUTINE-AUTO | 클라우드 Routine 실행 자체의 완전 자동화(현재 델타 브릿지까지 자동, 실행은 클라우드 Routines 의존) | 🟡 부분 |
| EVAL-1 | 발행물 내용 품질 Eval 하니스(구조 lint가 못 보는 사실정합성) | 🔲 후보 |
| GAP-2 | 브랜드-only 생물주사제 모달리티 오분류 해소 | 🔲 후보 |
| WHY-1 | 결함 내용 표출 감사(FDA 483/WHOPIR/WL/MFDS GMP로 사실상 확보, 지속 관찰) | 🟡 진행 |
| 운영 | Bus factor 1 대비 `docs/ops_runbook.md` 인수인계·아카이브 정책 실행 | 🟡 문서 완료·실행 대기 |

### 6.3 정기 운영 (사람 개입 지점)
- **매주 월요일:** Admin 콘솔에서 웹 브리프 미리보기 확인 후 **승인 버튼 1클릭**(트랙 A). Findings 번역(트랙 B)은 예약 세션이 자동 처리 — 데스크톱 앱이 열려 있어야 정시 실행(꺼져 있으면 다음 실행 시 처리).
- **매일 07:17 UTC:** 외부 백필(F2) cron이 `--auto` 로 483→WL 순 1청크씩 자동 소진(사람 개입 없음, 완료 시 자가 종료 후 신규 문서 안전망으로 전환).
- **가끔:** health 경고 Issue 확인, Secrets 로테이션.

### 6.4 FIND-1 전략 로드맵 대비 현황 (공모전 목표)

FIND-1의 원래 목표는 검색 DB 하나가 아니라 **"규제 지적사항 트렌드 인텔리전스 + Copilot 에이전트"** 로, AI 공모전(2026-07-07 기준 D-5개월+) 출품과 이후 상용화를 겨냥한 5개월 전략 계획(`docs/GRM_Findings인텔리전스_전략로드맵_2026-07-07.md`, 확정 결정 사항·평가기준 매핑 포함)이 별도로 존재한다. §4.4의 M1~M14는 이 전략 로드맵의 **1단계(M1: 스키마+내부 백필)** 에 해당한다. 2026-07-09 구현 M-번호와의 충돌을 피해 잔여 전략 단계를 **F2~F5**로 개칭했고, **2026-07-10 기준 F2(볼륨)가 가동 중, F3(인텔리전스)의 핵심(트렌드 대시보드)이 라이브, F4(에이전트)는 연동 자산이 완료**됐다 — 원계획 대비 앞서 있다.

| 단계 | 목표 | 시기 | 현황 |
|---|---|---|---|
| (원 M1) 스키마+내부 백필 | grm-finding/v1 동결, 이중 적재, 검색·번역 자동화 | 7월 | ✅ 완료(§4.4 구현 M1~M14) |
| **F2** 볼륨 — 외부 백필 | FDA 483 전수(~2,000건)·WL 수년치 → findings ≥2,000건·3년+ 커버리지. **공개·번역 정책 게이트**(집계=전량/row 노출=주당 최대 40건·최신 우선 번역) 포함 | 7월 중순~8월 말 | ✅ 가동(`collect_fda_backfill.py`+매일 07:17 UTC cron, findings 2,775건+·업체 428곳, 매일 증가. MFDS는 robots 정책상 제외) |
| **F3** 인텔리전스 — 분석 대시보드 | 사전계산 집계 서빙 + 히트맵·업체 지적 이력·실사관 프로파일 | 8월 중순~9월 말 | 🟡 핵심 라이브(`/findings/trends/` 스탯·카테고리·히트맵·연도추이·업체 Top30 라이브; 실사관 프로파일=FIND-483-SIGNER 미구현으로 보류) |
| **F4** 에이전트·검증 | Copilot Studio 커넥터+Q&A + 생산·품질·RA 3부서 파일럿(효과 정량화 4종) | 9월 중순~10월 말 | 🟡 자산 완료·파일럿 대기(F4a: `docs/copilot/` Swagger 커넥터 스펙+셋업 가이드+Q&A 시나리오 12건 완료; Studio 등록·부서 파일럿=사람 수행 대기) |
| **F5** 패키징 | 효과 정리·데모·발표자료 | 11월~공모전 | 🔲 D-day 확정 필요 |

> 이 문서는 그동안 git에 커밋되지 않고 로컬 파일로만 존재해 유실 위험이 있었다(2026-07-09 `docs/`로 이관). 단계 진행 시 이 표와 로드맵 문서 부록을 함께 갱신한다.
