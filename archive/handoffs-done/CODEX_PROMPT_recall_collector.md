# CODEX 작업 지시 — Phase 2c recall-quality Collector

작성: 2026-05-29. 이 문서대로 probe → 구현 → 검증까지 진행. 운영 enable은 검증 green + Claude 교차검토 후.

## 사전 상태 (이미 완료됨)
- Notion Intake DB(`collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288`)에 추가 완료:
  - `Type or Class` 옵션 `recall-quality`
  - 신규 select 필드 `Self-Check Required` = `Yes` / `Review` / `No`
- GitHub Secret `DATA_GO_KR_SERVICE_KEY` 등록됨(data.go.kr 15059114 Decoding 키).
- End Point base: `https://apis.data.go.kr/1471000/MdcinRtrvlSleStpgeInfoService04` (데이터포맷 JSON+XML).
- 참고 문서: `Phase2c_recall_collector_spec.md`(전체 스펙), `probe_recall.py`(스키마 진단).
- 제약: `ENABLE_MOLEG_API=false` 유지. GMP 실태조사(nedrug)는 범위 밖(defer).

## Step 1 — probe ✅ 완료 (run 26626859933) — 스키마 확정
재실행 불필요. 확정값(상세는 `Phase2c_recall_collector_spec.md`):
- End Point: `https://apis.data.go.kr/1471000/MdcinRtrvlSleStpgeInfoService04/getMdcinRtrvlSleStpgelList03`
- 인증키/포맷: **Decoding(quote) + json**. resultCode=00, totalCount=951.
- item keys: `PRDUCT(품목)`, `ENTRPS(업체)`, `RTRVL_RESN(회수사유)`, `ENFRC_YN(강제여부)`, `RECALL_COMMAND_DATE(YYYYMMDD)`, `RTRVL_CMMND_DT(YYYYMMDDHHMMSS)`, `ITEM_SEQ`, `BIZRNO`, `STD_CD`.
- ⚠️ wrapper `item`은 결과 1건이면 dict, 다건이면 list → 정규화 필수.
- dedupe 복합키: `recall-{sha1(PRDUCT|ENTRPS|RECALL_COMMAND_DATE|RTRVL_RESN)[:12]}` (`ITEM_SEQ` 단독 금지 — 품목 seq라 재회수 충돌).
- Self-Check v2: `RTRVL_RESN`에 직접 제조·품질 결함 키워드 시 `Yes`; 표시기재/표준코드/제조번호/유효기간 등 라벨링·행정성은 `Review`. probe 샘플 `표시기재(표준코드) 오기재`는 `Review`.
- scheduled(GitHub Actions) 첫 실행 시 serviceKey 권한/트래픽·IP 제약 없는지만 확인.

## Step 2 — collector 구현
- **새 모듈 `collect_mfds_recall.py`** 권장(데이터 흐름이 RSS와 달라 분리). 공유 자산은 import:
  - `grm_common`: `http_get_json`(있으면)/HTTP·429 helper, `log`.
  - `collect_intake`: `IntakeItem`, `SOURCE_MFDS`, `SRC_TYPE_OFFICIAL_API`, `_stable_doc_id`, `_within_window`.
- 진입점 `collect_mfds_recall(start, end, service_key) -> tuple[list[IntakeItem], str | None]` (MFDS 패턴과 동일 시그니처/그레이스풀).
- 항목 매핑(Step 1 실제 필드명으로 확정):
  - 품목명 → Headline 구성, 업체명 → `firm`, 회수사유내용 → `body`(+자가점검 신호), 회수명령일자(없으면 승인일자) → `date_iso`(윈도우 필터), 호출 URL(serviceKey 마스킹) → `api_query`.
  - 고정: `source=SOURCE_MFDS`, `type_or_class="recall-quality"`, `signal_tier="Tier 3"`, `language="KO"`, `region_jurisdiction="Korea (MFDS)"`, `source_type=Official API`.
- **Self-Check Required**(IntakeItem에 필드 추가 + `build_notion_properties`에 `PROP_SELF_CHECK` 매핑 추가 필요):
  - 기본 `Review`. 회수사유내용에 직접 제조·품질 결함 키워드 포함 시 `Yes`: 품질부적합, 함량, 함량부족, 용출, 안정성, 미생물, 무균, 이물, 오염, 제조공정, 시험성적, 기준일탈, 불순물, 니트로사민.
  - 표시기재/표준코드/제조번호/유효기간/포장·첨부문서 오기재/허가사항과 상이 등 라벨링·행정성 항목은 `Review`.
  - 명백히 무관할 때만 `No`.
- dedupe: API 고유 ID 있으면 `MFDS::{id}`, 없으면 `MFDS::recall-{sha1(품목명+업체명+회수명령일자+회수사유내용)[:12]}`.
- serviceKey는 `_mask` 후 로깅. 부분 실패 graceful, 전체 실패만 err 반환.

## Step 3 — 파이프라인 연결
- `collect_intake.main()`에 `ENABLE_MFDS_RECALL`(기본 false) 플래그 추가. true일 때만 lazy import 호출(collect_mfds와 동일 패턴).
- `CollectionStats`에 recall 카운터 + `total_insert_failures()`/summary 포함. 오류 시 exit 경로도 기존 MFDS와 동일.
- `Notion` 신규 prop 상수 `PROP_SELF_CHECK = "Self-Check Required"` 추가 + `build_notion_properties`에서 `item.self_check_required`가 있으면 `_select(...)`로 기입.
- `.github/workflows/grm-intake.yml`에 `enable_mfds_recall` dispatch input + `ENABLE_MFDS_RECALL` env, `DATA_GO_KR_SERVICE_KEY` secret 전달.

## Step 4 — 검증 (실삽입 전)
1. `py -3 -m py_compile grm_common.py collect_intake.py collect_search.py collect_mfds.py collect_mfds_recall.py` 통과.
2. dry-run: 수집 건수 + Self-Check `Yes`/`Review` 비율 확인 → `Yes` 과다하면 키워드 조정.
3. 실삽입 1회(작은 윈도우, `ENABLE_MFDS_RECALL=true`) → Notion에서 `Type or Class=recall-quality`, `Self-Check Required`, `Signal Tier=Tier 3` 확인.
4. 2회차 동일 실행 → `skip_dup`로 잡히고 `inserted=0` (dedupe 입증). (MFDS RSS 검증과 동일 절차)
5. `git diff --check` 통과.

## Step 5 — 문서
- `notion_intake_db_schema.md`(recall-quality, Self-Check Required 반영 — Notion엔 이미 추가됨), `.env.example`(`DATA_GO_KR_SERVICE_KEY`, `ENABLE_MFDS_RECALL`), `GRM_session_decisions.md`(상태 갱신), README/runbook.

## Step 6 — enable 정책
- 검증 green + Claude 교차검토 전까지 `ENABLE_MFDS_RECALL=false` 유지. 검증 후 별도 커밋으로 기본값 전환.
- 기존 collector/속성 절대 깨지지 않게. `grmintake` OC/기타 시크릿 재사용 금지.

## Step 1 결과 공유
probe의 `✅ 채택 후보` 줄 + 첫 항목 키/샘플을 Claude(이 세션)에 공유하면, 매핑·Self-Check 키워드를 함께 확정한 뒤 Step 2로 진행 가능.
