# Phase 2c — 행정처분 + GMP 적합판정서 Collector Spec (data.go.kr)

작성: 2026-05-29 / 상태: 스펙 작성 (probe → 구현은 serviceKey 활용신청 후 Codex). 자가점검(Self-Check) 미사용.

## Goal
제조/품질 정보 누락 최소화를 위해 data.go.kr 2개 서비스를 GRM Notion Intake에 추가 수집:
1. **의약품 행정처분** (`15058457`) → Type `admin-action` — GMP 위반·품질부적합 등 **제재 결과**(실태조사의 enforcement proxy).
2. **GMP 적합판정서 발급현황** (`15097207`) → Type `gmp-certificate` — 제조소 GMP 적합 **상태**.

회수(`recall-quality`)와 동일 구조 재사용. **Self-Check Required는 쓰지 않음(기능 제거됨).**

## Secret / Flag
- 기존 **`DATA_GO_KR_SERVICE_KEY` 재사용** (사용자: 각 서비스 data.go.kr "활용신청"만 추가 — 보통 즉시 승인).
- feature flag (기본 false, 독립 토글): `ENABLE_MFDS_ADMIN`, `ENABLE_MFDS_GMPCERT`.

## Endpoint
- **행정처분 ✅ probe 확정**: `https://apis.data.go.kr/1471000/MdcinExaathrService04/getMdcinExaathrList04`, **Decoding(quote)+json**, totalCount 624. `order=Y` 지원. `item` dict/list 정규화 필수.
- **GMP 적합판정서 ⏸ 보류(403)**: 공식 endpoint `.../DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq`. 현재 `DATA_GO_KR_SERVICE_KEY`로 https/http 모두 **403 Forbidden**(후보 List/Inq04는 404 → op명 문제 아님). 진단: 키·계정 정상(회수·행정처분 동작), 적합판정서 서비스만 키 연결 미전파 추정. → **몇 시간 후 재-probe**, 지속 시 마이페이지에서 동일 계정 활용신청인지 확인.

## 공통 고정값
- `Source=MFDS`, `Source Type=Official API`, `Language=KO`, `Region/Jurisdiction=Korea (MFDS)`.

## 매핑
**admin-action (행정처분) ✅ probe 확정 필드**
실제 item keys: `ADM_DISPS_SEQ, ENTP_NAME, ADDR, ENTP_NO, ITEM_NAME, BEF_APPLY_LAW, EXPOSE_CONT, ADM_DISPS_NAME, LAST_SETTLE_DATE, ITEM_SEQ, RLS_END_DATE, BIZRNO`

| API 필드 | 의미 | → |
|----------|------|---|
| `ENTP_NAME` | 업체명 | `Firm` |
| `ITEM_NAME` (없으면 `ADM_DISPS_NAME`) | 품목명/처분명 | `Headline` 핵심 (예: `[행정처분] {ITEM_NAME or ADM_DISPS_NAME} — {ENTP_NAME}`) |
| `EXPOSE_CONT` | 처분 공개내용(사유 본문) | `Body` 핵심 + Tier 판정 입력 |
| `ADM_DISPS_NAME` | 처분명(유형) | `Body` 메타 |
| `BEF_APPLY_LAW` | 위반 적용법령 | `Body` 메타 |
| `ADDR` / `BIZRNO` / `ENTP_NO` / `RLS_END_DATE` | 주소/사업자/처분종료일 | `Body` 메타·raw |
| `LAST_SETTLE_DATE` | 최종 처분(확정)일 | `Date`(윈도우 필터) |
| `ADM_DISPS_SEQ` | 처분 일련번호 | **dedupe 고유키** |

- `Type or Class=admin-action`. `order=Y`로 최신순 호출 권장.
- Signal Tier: `EXPOSE_CONT`+`ADM_DISPS_NAME`+`BEF_APPLY_LAW`에 GMP/품질 키워드(GMP·우수의약품제조관리기준·제조관리·품질·제조업무정지·제조정지·품질부적합·시험·함량·무균·데이터/자료·실태조사·회수·거짓/부정) → **Tier 3**, 그 외(표시·광고·판매질서·행정) → **Tier 2**.
- QA Relevance: 위 키워드 있으면 Likely, 아니면 Possible.

**gmp-certificate (적합판정서) ⏸ 403 보류 — 재-probe 후 확정**
공식 문서 필드(미검증): `BSSH_NM`(업체), `FCTR_ADDR`(공장주소), `KGMP_BGMP_NAME`(판정구분), `GMP_INGR_MM_GROUP_NAME`(제형/제조방법군), `VLD_PRD_YMD`(유효기한).
- `Type or Class=gmp-certificate`, Signal Tier **Tier 1**(상태/참고), QA Relevance Possible.
- ⚠️ "발급일"이 아니라 `VLD_PRD_YMD`(유효기한) 중심 = **상태 테이블**. 이벤트 feed 아님 → 신규/변경만 들어오게 dedupe 설계.

## Dedupe
- **admin-action ✅**: `document_id = f"admin-{ADM_DISPS_SEQ}"` (고유키 확정).
- gmp-certificate(보류): 적합판정서번호 있으면 사용, 없으면 `gmpcert-{sha1(BSSH_NM+FCTR_ADDR+GMP_INGR_MM_GROUP_NAME+VLD_PRD_YMD)[:12]}`. (유효기한 포함 → 동일 상태 재삽입 방지)
- dedupe 키 = `MFDS::{document_id}` (기존 insert 경로).

## 구조 / 런타임
- 모듈: `collect_mfds_admin_action.py`, `collect_mfds_gmp_cert.py` (각각 `collect_mfds_recall.py` 복제·수정).
- `grm_common` HTTP/429 helper 재사용, serviceKey 마스킹, 페이지네이션, dict/list 정규화.
- `collect_intake.main`에 각 flag·stats·exit 연결(recall과 동일 패턴). 부분 실패 graceful, 전체 실패만 err.
- 윈도우: 날짜 필드로 최근분만(전량 951+ 방지). gmp-certificate는 현황형이라 "최근 발급/변경" 위주 필터 권장.

## 검증 → enable
1. 각 endpoint probe(필드/op/키 확정) → 매핑 반영.
2. py_compile.
3. dry-run(건수·Tier 분포 확인).
4. 실삽입 1회 + 2회차 dedupe skip 확인(작은 윈도우).
5. green + Claude 교차검토 후 `ENABLE_MFDS_ADMIN`/`ENABLE_MFDS_GMPCERT`=true 별도 커밋.

## 문서
- `.env.example`(2 flag + serviceKey 재사용 명기), `notion_intake_db_schema.md`(admin-action/gmp-certificate Type), `GRM_session_decisions.md` 상태 갱신.

## 범위 밖
- GMP 실태조사 지적사항 본문(nedrug SPA) — 별도 Chrome XHR spike 트랙(Phase 2d).
- Self-Check — 제거됨.
