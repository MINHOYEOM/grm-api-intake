# Phase 2c — recall-quality Collector Spec (data.go.kr 15059114)

작성: 2026-05-29 / 상태: **probe 완료 — 스키마 확정.** 다음은 Codex 구현 단계.

## Goal
data.go.kr 서비스 `15059114`(식품의약품안전처_의약품 회수·판매중지 정보)에서 국내 의약품 회수·판매중지 품질 신호를 수집해 기존 GRM Notion Intake DB에 `recall-quality` 항목으로 insert한다. 목적은 단순 모니터링이 아니라 **자가점검**(이 회수 사유가 우리 공정/제품에도 해당하는가).

## Endpoint (probe 확정 2026-05-29 — run 26626859933)
- **목록조회 End Point**: `https://apis.data.go.kr/1471000/MdcinRtrvlSleStpgeInfoService04/getMdcinRtrvlSleStpgelList03`
  - ⚠️ 오퍼레이션명이 서비스명과 버전 불일치: 서비스 `...Service04`인데 오퍼레이션 `getMdcinRtrvlSleStpgel**List03**`. (예상한 `...StpgeInfoList04` 아님 — 공식 Swagger 값.)
- **인증키/포맷**: `Decoding(quote) + json` 채택. (serviceKey를 URL-encode해서 전달)
- **응답**: `HTTP 200`, `resultCode=00`, `NORMAL SERVICE.`
- **페이징**: `pageNo`, `numOfRows`, `totalCount`(=951, 2026-05-29 기준). 전량 수집 시 페이지 루프 필요(단 collector는 날짜 윈도우로 최근만).
- **wrapper 주의**: `... "item": ...` 구조. **data.go.kr 관례상 결과 1건이면 `item`이 dict, 여러 건이면 list** → collector는 dict/list 둘 다 정규화 처리할 것.

## Secret (분리 필수)
- 신규 환경변수 **`DATA_GO_KR_SERVICE_KEY`** 사용.
- 기존 `grmintake`(법령 OC) / 기타 OC 시크릿 **재사용 금지** — 서비스별 승인/장애 분리.
- feature flag: `ENABLE_MFDS_RECALL`(기본 false). 켜질 때만 호출. (ENABLE_MFDS와 별개로 독립 토글 가능하게)

## 분류 고정값
- `Source = MFDS`
- `Type or Class = recall-quality`  (Notion 옵션 추가 완료)
- `Signal Tier = Tier 3` (회수는 고신호)
- `Language = KO`, `Region/Jurisdiction = Korea (MFDS)`
- `Source Type = Official API`

## 필드 매핑 (probe 확정 실제 키 → Notion/IntakeItem)
실제 item keys: `PRDUCT, ENTRPS, RTRVL_RESN, ENFRC_YN, RTRVL_CMMND_DT, RECALL_COMMAND_DATE, ITEM_SEQ, BIZRNO, STD_CD`

| API 필드 | 의미 | → Notion/IntakeItem |
|----------|------|---------------------|
| `PRDUCT` | 품목(제품)명 | `Headline` (제목 구성) |
| `ENTRPS` | 업체명 | `Firm` |
| `RTRVL_RESN` | 회수사유내용 | `Body` 본문 + **Self-Check 신호 원천** |
| `ENFRC_YN` | 강제여부(Y/N) | `Body` 메타에 포함(`강제여부: Y/N`) |
| `RECALL_COMMAND_DATE` | 회수명령일자 `YYYYMMDD` | `Date`(윈도우 필터, **1순위**) |
| `RTRVL_CMMND_DT` | 회수명령일시 `YYYYMMDDHHMMSS` | `Date` fallback(앞 8자리) |
| `ITEM_SEQ` | 품목 일련번호 | raw_payload 보조(※ 회수건 고유키 아님 — dedupe에 단독 사용 금지) |
| `BIZRNO` | 사업자번호 | raw_payload 메타 |
| `STD_CD` | 표준코드(복수, 콤마) | raw_payload 메타 |
| 호출 URL | — | `API Query`(serviceKey 마스킹) |

- 고정값: `source=MFDS`, `type_or_class=recall-quality`, `signal_tier=Tier 3`, `language=KO`, `region_jurisdiction=Korea (MFDS)`, `source_type=Official API`.
- 날짜 파싱: `RECALL_COMMAND_DATE`(YYYYMMDD)→ISO 우선, 없으면 `RTRVL_CMMND_DT[:8]`.
- 주의: `ITEM_SEQ`는 **품목** seq라 동일 품목 재회수 시 충돌 → dedupe 단독 키로 쓰지 말 것(아래 복합키).

## ~~Self-Check Required 매핑~~ — 제거됨 (2026-05-29 사용자 결정)
> 자가점검 기능은 미래 "사용자 설정형 규제 매칭 제품"으로 이관. collector에서 Self-Check 산정/매핑 제거. 회수 수집은 유지. 아래 내용은 히스토리 참고용(미적용).

### (히스토리) Self-Check Required 매핑 v2
1차 dry-run(30일) Yes=46/59(78%)로 과다 → **표시/행정성 항목을 Yes에서 제외**해 변별력 회복.
원칙: `Self-Check=Yes`는 "우리 **제조·품질 공정**에 해당 failure mode가 있는지 점검" 신호. 기본 `Review`, 아래 **제조/품질 결함 키워드**에만 `Yes` 승격.

- **`Yes` 키워드(제조·품질 무결성 — `RTRVL_RESN` substring)**:
  품질부적합, 함량, 함량부족, 함량초과, 용출, 붕해, 안정성, 미생물, 무균, 멸균, 이물, 오염, 교차오염, 불순물, 니트로사민, 기준일탈, OOS, 시험성적, 역가, 성상, 변색, 침전, 무균조작, 데이터 완전성
- **`Review`(기본 — 위 Yes 키워드 없으면 전부 Review)**: 표시기재, 표준코드, 제조번호(표기), 유효기간(표기), 포장/첨부문서 오기재, 허가사항과 상이, 영업자 자진회수 등 **라벨링/행정성**.
- **`No`**: QA 자가점검과 명백히 무관할 때만(보수적으로 거의 사용 안 함).
- 우선순위: Yes 키워드가 하나라도 있으면 Yes(라벨링 용어가 같이 있어도 Yes 유지). 없으면 Review.
- probe 샘플 `"...표시기재(표준코드) 오기재..."` → Yes 키워드 없음 → **Review**(v1의 Yes에서 하향).
- (선택) `ENFRC_YN == "Y"`(강제회수)는 심각도 높음 → 기본 Review 유지(이미 충족). 강제+Yes키워드면 Yes.

### Codex 액션 (enable 전)
- 위 v2 Yes 키워드로 좁힌 뒤 **dry-run 30일 재실행 → 새 Yes/Review 비율 보고**. (목표: Yes가 "진짜 제조/품질 결함 회수"에 수렴)
- 비율 확인 후 `ENABLE_MFDS_RECALL=true` 전환 판단.

## Dedupe (확정)
- 회수건 고유 ID 없음 확인 → **복합키 해시** 사용:
  `document_id = "recall-" + sha1(f"{PRDUCT}|{ENTRPS}|{RECALL_COMMAND_DATE}|{RTRVL_RESN}")[:12]`
- dedupe 키 = `MFDS::{document_id}` (기존 `insert_items` 경로 그대로).
- `ITEM_SEQ`는 보조 메타로만 보관(고유키 아님).

## 런타임 요구
- dry-run / live insert 모드 지원(기존 패턴).
- 로그: fetched / inserted / skip_dup / failed 카운트.
- HTTP는 `grm_common`의 helper 재사용(429 Retry-After 포함). serviceKey는 로그 마스킹.
- 전체 실패 시에만 error 반환(부분 실패 graceful) — 기존 MFDS 패턴과 동일.
- 기존 collector/속성 절대 깨지지 않게.
- 작은 최근 윈도우로 smoke 검증.

## 검증 (실삽입 전)
1. ✅ probe 완료(run 26626859933) — 위 Endpoint/필드/dedupe 확정.
2. dry-run으로 건수·Self-Check 분류(Yes/Review 비율) 확인 → Yes 과다하면 키워드 조정.
3. 실삽입 1회 → Notion에서 recall-quality 행 필드 확인 → 2회차 dedupe skip 확인 (MFDS RSS 검증과 동일 절차).

## 문서 갱신
- `notion_intake_db_schema.md`(recall-quality Type, Self-Check Required 필드 반영 — Notion엔 이미 추가됨)
- `.env.example`(`DATA_GO_KR_SERVICE_KEY`, `ENABLE_MFDS_RECALL`)
- `GRM_session_decisions.md`(구현 완료 시 상태 갱신)

## 범위 밖 (이번 아님)
- GMP 실태조사 결과(nedrug SPA) — 별도 브라우저 XHR spike 후 결정.
- 자가점검 Weekly Brief 액션 생성 = Claude Routine 프롬프트 변경(collector 아님). 회수 수집 후 별도 작업.
