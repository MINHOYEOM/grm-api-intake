# FIND-1 M9 — 완전 자동화(공개 게이트 + outbox 파이프라인 + 주간 스케줄)

> 날짜: 2026-07-09
> 상태: 코드 완료(M9a+M9b, 테스트 green)·스케줄 등록 완료(M9c)·outbox 파이프라인 첫 실행은 다음 월요일(2026-07-13 09:00 KST) 관찰 대기
> 코드 정본: `web/migrations/006_findings_publish_gate.sql`(공개 게이트 RLS) · `findings_translate.py`(`--outbox-output`) · `findings_translate_apply_service.py`(신규, outbox 적용 서비스) · `.github/workflows/grm-findings-translate-apply.yml`(신규, 적용 워크플로)
> 테스트 정본: `tests/test_findings_publish_gate.py`(신규)·`web/tests/test_render.py`(게이트 확장)·`tests/test_findings_translate.py`(`--outbox-output` 확장)·`tests/test_findings_translate_apply_service.py`(신규 20개, 전량 mock)
> 커밋: `40331a3`(M9a, 웹 97·루트 1409+842 green)·`72e455f`(M9b, 신규 테스트 +20·전체 1435+842 green). M9c는 코드 변경이 아니라 `schedule` 스킬로 등록한 cron 태스크(이 저장소 밖 `C:\Users\user\.claude\scheduled-tasks\grm-findings-weekly-translate\SKILL.md`)다.

---

## 0. M9 게이트 — 하는 것 / 안 하는 것 (LLM은 쓰지 않는다 / CI는 판단하지 않는다)

M6~M8까지 번역 파이프라인은 "라이브 소스를 읽고 쓸 수는 있지만, 실제 실행은 사람이 세션을 열어야 하는" 반자동 상태였다(M8 §3 runbook). 사용자가 두 가지를 동시에 요구했다 — ① 미번역 finding이 공개 웹에 영문 원문 그대로 노출되면 안 된다(최종본만 공개) ② API 과금 없이 기존 Claude 구독으로 번역을 자동화하고, 매번 사람이 추적하지 않아도 되게 하고 싶다. M9는 이 둘을 순서대로 해소한다 — 먼저 안전장치(M9a)를 최우선 배포하고, 그 위에 완전 자동화 파이프라인(M9b+M9c)을 쌓는다.

**핵심 설계 원칙(고정): "번역(LLM 판단이 필요한 작업)"과 "실제 DB 쓰기(결정론적 작업)"를 물리적으로 분리한다.**

- **하는 것(M9a):** `findings_public_read` RLS 정책을 좁혀, 국문 번역이 없는 finding은 anon 조회 자체가 DB 레벨에서 안 되게 막는다(§1). 클라이언트 필터가 아니라 정책 자체이므로 우회 경로가 없다.
- **하는 것(M9b):** 순수 결정론 Python 스크립트 하나(`findings_translate_apply_service.py`)가 outbox JSON을 읽어 Supabase에 PATCH한다. 이 스크립트도, 이 스크립트를 실행하는 GitHub Actions 워크플로도 **번역 텍스트를 생성하지 않고, 검증 로직을 다시 판단하지도 않는다** — outbox에 이미 들어있는 값을 그대로 반영할 뿐이다. 그리고 이 워크플로는 **git 쓰기를 전혀 하지 않는다**(checkout 이후 커밋/푸시/PR 생성 스텝이 0개).
- **하는 것(M9c, 스케줄):** 매주 월요일 스케줄된 Claude Code 세션(사용자의 기존 Claude 구독 사용량 — 신규 API 비용 0)이 "번역(LLM 판단)" 쪽을 전담한다 — 미번역 항목 export, 번역 채움, 검증, outbox JSON 생성, PR 커밋까지. 이 세션은 프로덕션 Supabase에 **절대 직접 쓰지 않는다**(스케줄 태스크 SKILL.md의 안전 규칙에 명시) — 결과물은 outbox JSON 하나를 담은 PR이며, 그 PR을 자동 머지하는 것까지가 이 세션의 역할이다(자동 머지는 사용자가 이 자동화 자체를 승인한 정책이지, 이 세션이 임의로 판단한 것이 아니다).
- **하지 않는 것:** ① 이 CC 컨트롤 타워 세션이 프로덕션 Supabase에 직접 쓰기(세션 정책상 여전히 회피 — M3/M6c와 동일 원칙) ② `grm-findings-translate-apply.yml`이 번역 판단이나 검증 재실행(outbox에 이미 검증 통과분만 들어있다고 신뢰) ③ outbox 파일 이동/삭제/git 조작(§3 멱등 설계상 불필요) ④ 신규 secret 발급(기존 `SUPABASE_SERVICE_ROLE_KEY` 재사용) ⑤ M1~M8 스키마·코드·골든 수정(전부 불가침).

---

## 1. 공개 게이트 계약 — `006_findings_publish_gate.sql`

라이브 `/findings/` 사용자 피드백: 국문 번역이 아직 없는 finding이 영문 원문 그대로 공개 페이지에 노출되는 것은 "최종본만 공개"라는 기대에 어긋난다. M9a 이전에는 `findings_public_read` 정책이 `using (true)`라 `finding_text_ko`가 비어 있어도 anon이 그대로 조회할 수 있었고, 웹(M6d)은 이를 원문 폴백으로 "정상 렌더"했다 — 즉 미번역 발행은 버그가 아니라 설계상 허용된 상태였다. 이 기대를 뒤집는다.

- **정책 변경**: `findings_public_read` 의 `using` 절을 `using (true)` → `using (finding_text_ko <> '' or finding_language = 'KO')`로 교체한다. `finding_text_ko`가 채워진 행만 anon/authenticated가 조회할 수 있다.
- **예외 규칙**: `finding_language = 'KO'`(MFDS 등 원문 자체가 한국어인 소스)는 번역이 필요 없으므로 게이트에서 제외된다 — 원문이 이미 한국어인데 `finding_text_ko`를 요구하면 영구히 비공개가 되는 모순을 막는다.
- **DB 레벨 차단, 클라이언트 필터 아님**: 이전까지 findings.js는 "번역 대기" chip을 클라이언트에서 계산해 보여줬을 뿐 조회 자체는 막지 않았다. 006 이후는 RLS 정책이 anon PostgREST 쿼리 단계에서 행 자체를 반환하지 않는다 — 클라이언트 코드를 신뢰할 필요가 없다.
- **`raw_signals` 무관**: 이 정책은 `findings` 테이블 전용이다. `raw_signals`는 M3부터 이미 전면 비공개(RLS no-policy)이므로 006과 상호작용하지 않는다.
- **service_role 영향 없음**: `SUPABASE_SERVICE_ROLE_KEY`를 쓰는 모든 쓰기 경로(M4 야간 적재, M9b outbox 적용)는 RLS를 우회하는 role이라 006의 영향을 받지 않는다 — 미번역 상태로 findings 행을 insert/update하는 기존 흐름은 전혀 막히지 않으며, 오직 **공개 anon 조회**만 좁아진다.
- **대시보드 정합**: `findings.js`의 "번역 대기 N건" chip은 게이트가 있는 한 항상 0으로 수렴한다(비공개 행은 애초에 조회되지 않으므로) — 그대로 두면 "번역 대기 0건"이라는 오해를 준다. M9a는 이 chip을 제거했다. 카드 레벨의 원문 폴백 렌더(finding_text_ko 없을 때 영문만 표시)와 `LEGACY_FIELDS` 폴백(M6d, 005 배포 순서 무관 방어)은 그대로 남긴다 — 006 이후에는 이 경로 자체가 공개 조회에서 도달 불가능하지만, 코드로서는 여전히 방어적이어야 한다(로컬 sidecar 조회·오프라인 뷰어 등 anon RLS가 적용되지 않는 경로가 남아있기 때문).

---

## 2. 자동화 아키텍처 — 생산자(주간 세션) / 소비자(적용 워크플로)

"번역"과 "쓰기"를 분리한 두 축이 `translations/outbox/` 디렉터리를 매개로 연결된다. 도식:

```
[생산자] 매주 월 09:00 KST                      [소비자] main push 트리거
 scheduled-tasks/grm-findings-weekly-translate    .github/workflows/
 (Claude Code 세션, 구독 사용량·API 비용 0)         grm-findings-translate-apply.yml
 ─────────────────────────────────                ─────────────────────────
 ① export --source supabase                       outbox/*.json 이 main 에
    (미번역 finding_text_ko='' 추출, read-only)      merge 되면 트리거
 ② LLM 이 finding_text_ko 채움                          │
    (번역 지침 준수 — 원어 병기·법조항 유지·          ▼
     사실 왜곡 금지)                             findings_translate_apply_service.py
 ③ apply --outbox-output out.json                 (순수 결정론 Python, LLM 무관여)
    (전건 검증 all-or-nothing —                     - outbox/*.json 을 읽는다
     원문 byte 대조·한글 필수·                       - 항목별 PATCH .../findings
     method 화이트리스트·번역=원문 금지)               (finding_id+finding_text 필터)
 ④ ready=true 면 outbox JSON 을                     - git 쓰기 0(checkout 만)
    translations/outbox/{날짜}-batch.json 로        - 서비스키는 기존
    커밋 → PR → CI green 확인 후 자동 머지             SUPABASE_SERVICE_ROLE_KEY 재사용
    (사용자가 이 자동화 자체를 승인 — 매번           - 오류 있어도 exit 0
     사람이 리뷰하지 않음)                            (outbox 파일은 그대로 두고
                                                      다음 실행이 재시도)
```

- **생산자가 절대 하지 않는 것**: Supabase에 직접 INSERT/UPDATE, `grm_findings.py`/`findings_extractors.py`/`findings_translate.py` 등 기존 코드 수정, 검증 실패(`ready=false`) 시 우회 커밋.
- **소비자가 절대 하지 않는 것**: git add/commit/push/PR 생성, outbox 파일 이동·삭제·재작성, 번역 텍스트 재검증(생산자 쪽 `_validate_item`을 다시 신뢰).
- **outbox 스키마**(`findings_translate.py --outbox-output`가 만드는 JSON 배열, 검증 통과 항목만 포함):

  ```json
  [
    {
      "finding_id": "…",
      "finding_text": "원문(영문) — PATCH 필터 겸 TOCTOU 가드",
      "finding_text_ko": "번역문",
      "translation_method": "llm_assisted"
    }
  ]
  ```

  `--sql-output`(M8a부터 존재하는 사람용 UPDATE SQL 산출)과 공존 가능 — 같은 `--apply` 실행에서 둘 다 지정하면 두 파일이 동시에 나온다. sqlite/supabase 두 `--source` 모두에서 동작한다(outbox 산출 자체는 read/검증 결과물이라 소스 무관).

---

## 3. outbox 적용 서비스 계약 — `findings_translate_apply_service.py`

- **엔드포인트**: `PATCH {SUPABASE_URL}/rest/v1/findings?finding_id=eq.{finding_id}&finding_text=eq.{finding_text}`, 헤더는 `apikey`/`Authorization: Bearer {service_role_key}`+`Prefer: return=representation`, 바디는 `{finding_text_ko, translation_method}`.
- **멱등성의 근거**: 필터가 `finding_id` 단독이 아니라 `finding_id`+`finding_text`(원문) 조합이다. 이미 반영된 항목을 재실행하면 원문이 그대로이므로 다시 매칭돼 동일 값으로 재기록되는 무해한 no-op이고, 원문이 그 사이 바뀌었다면(TOCTOU) 매칭 0건으로 자연스럽게 skip된다 — 어느 쪽도 outbox 파일을 지우거나 옮길 필요를 만들지 않는다.
- **응답 건수별 판정**:
  - **0건 매칭**: 오류 아님(`items_matched_zero`). 이미 반영됐거나 원문이 변경/삭제된 경우 — 둘 다 TOCTOU-safe no-op.
  - **1건 매칭**: 성공(`items_succeeded`).
  - **2건 이상 매칭**: `finding_id`는 유니크해야 하므로 데이터 무결성 이상 신호 — `items_errored`로 집계하고 파일은 그대로 둔다.
- **재시도**: 5xx 응답 또는 timeout에 한해 1회 재시도(총 시도 2회, 기존 `findings_supabase_append.py`/`_supabase_get` 재시도 계약과 동형). 그 외 예외·4xx는 즉시 실패 처리.
- **비노출**: 서비스 role 키는 로그·예외 메시지·리포트 어디에도 나타나지 않는다 — 실패 사유는 `timeout`, 예외 타입명, 또는 `http_{status}` 문자열뿐이다.
- **exit code**: `--supabase-url`/`--service-role-key`(또는 env) 자체가 없으면 exit 2(조기 종료, 리포트 파일 미생성). 그 외에는 **항목 오류가 있어도 항상 exit 0** — PATCH가 멱등이고 outbox 파일이 보존되므로 다음 스케줄 실행이 자연스럽게 재시도한다. CI는 리포트(`apply_report.json`, step summary에 첨부)를 기록할 뿐 이 오류로 실패 처리하지 않는다.
- **git 무접촉**: 파일을 읽기만 하고, 이동·삭제·재작성하지 않는다 — outbox는 누적된다(§6).

---

## 4. 주간 스케줄 설정 기록 (M9c)

이번 세션이 `schedule` 스킬로 직접 등록했다 — 코드 변경이 아니라 운영 이벤트다.

- **태스크**: `grm-findings-weekly-translate`(cron `0 9 * * 1` = 매주 월요일 09:00 KST).
- **정의 위치**: `C:\Users\user\.claude\scheduled-tasks\grm-findings-weekly-translate\SKILL.md`(이 저장소 밖 — 사용자 로컬 Claude 설정. 이 문서에서는 참조만 하고 내용을 복제하지 않는다).
- **수행 절차 요지**: 안전 확인(`git status`)→`chore/findings-translate-{날짜}` 브랜치→`--export --source supabase`→번역 채움(M6c 지침과 동일: QA 보고체, 전문용어 원어 병기, 법조항 원문 유지, 사실 왜곡 금지)→`--apply --outbox-output translations/outbox/{날짜}-batch.json`→`ready=false`면 커밋 없이 중단·보고→`ready=true`면 커밋+PR+CI green 확인 후 `gh pr merge --merge`로 **자동 머지**(사람 승인 없음 — 사용자가 명시적으로 승인한 정책).
- **핵심 제약(사람에게 다시 안내할 사항)**: 이 스케줄 메커니즘은 **사용자 데스크톱 앱이 열려 있어야 정시 실행된다** — 서버 상시 cron이 아니다. 앱이 꺼져 있으면 그 회차는 건너뛰고 다음 앱 실행 시 대신 돈다(주간 단위이므로 하루 이틀 지연은 무해 — M8 §3에서도 "번역 지연은 장애가 아니다"라고 이미 명시된 원칙과 일관).
- **첫 실행 안내**: 처음 실행될 때 도구 사용 승인 프롬프트(git push, `gh pr merge` 등)가 뜰 수 있다 — "지금 실행"으로 사전 승인해 두면 다음 주부터 무인으로 흐른다.
- **이 세션이 확인하지 않은 것**: 실제 cron 최초 발화(2026-07-13 월요일 09:00 KST) — §6에 이월.

---

## 5. 검증 규칙 요약 (M6b/M8a와 완전 동일 — 재확인)

`--apply`(어느 `--source`든)는 다음을 전건 all-or-nothing으로 통과해야 outbox/SQL을 산출한다:

- 원문(`finding_text`) byte 단위 대조 — plan의 원문이 라이브(또는 sqlite) 스냅샷과 한 글자라도 다르면 그 배치 전체 거부.
- `finding_id` 실존 확인.
- `finding_text_ko`에 한글 1자 이상 포함.
- `translation_method`는 `llm_assisted`/`manual` 화이트리스트만 허용.
- `finding_text_ko` != `finding_text`(번역=원문 동일 문자열 금지).
- plan 내부 `finding_id` 중복 거부.

하나라도 실패하면 `ready=false`, exit 3, outbox/SQL 파일 미생성 — M9의 자동 머지 정책도 이 게이트를 우회하지 않는다(스케줄 절차 §4가 `ready=false`를 커밋 중단 조건으로 명시).

---

## 6. 이월

- **outbox 누적 정리** — `translations/outbox/`는 의도적으로 파일을 지우거나 이동하지 않으므로 시간이 지나면 배치 JSON이 계속 쌓인다. 현재 물량(주당 소량)에서는 무해하지만, 누적이 부담될 시점에 아카이브/삭제 정책을 별도로 설계해야 한다(§0에서 이미 명시한 대로 이번 스프린트의 의도적 범위 밖).
- **export 1000행 페이지네이션**(M8 §5에서 이월 계승) — 미번역 건수가 `_SUPABASE_EXPORT_LIMIT=1000`을 넘으면 여전히 `truncated_possible` 경고만 뜨고 나머지는 잘린다.
- **스케줄 최초 실행 관찰**(신규) — `grm-findings-weekly-translate`가 실제로 2026-07-13(월) 09:00 KST에 발화해 PR을 만들고 자동 머지까지 성공하는지, 이후 `grm-findings-translate-apply.yml`이 그 머지에 반응해 PATCH를 수행하는지는 이 문서 작성 시점 기준 관찰 전이다. 데스크톱 앱 상태(꺼짐/켜짐)에 따른 첫 실행 지연 여부도 함께 확인해야 한다.
- **WL 장문 분할 추출 개선**(M7/M8에서 이미 이월) — M9와 무관하게 `findings_extractors.py` 층의 별도 개선으로 계속 이월.
