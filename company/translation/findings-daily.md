# 글로벌번역팀 · 지적사항 일일 번역 — 모델 Sonnet · 매일 04:00 KST

PC 예약작업 `grm-findings-weekly-translate`(매일 09:10)를 옮긴 것이다. PC 쪽은 2026-11-14 까지 백업으로 남고,
이 작업이 그날 이미 했으면 건너뛴다.

## 읽을 것
`company/CHARTER.md` · `company/CLOUD.md` · 이 파일. 도구 사용법은 `findings_translate.py --help` 만 본다(코드를 고치지 않는다).

## 무엇을 왜
`findings.finding_text`(영문 원문, 불변)에 국문 `finding_text_ko` 를 채운다. 국문이 없으면 공개 RLS(`006_findings_publish_gate.sql`)가
웹 노출을 막는다. 이 작업은 **번역 배치 JSON 을 만들어 PR 로 올리는 것까지**다 — DB 반영은 머지 후 `grm-findings-translate-apply.yml` 이 한다.

- **1회 상한 100건**, `published_date` 최신순. 나머지는 다음 날 이어서.
- **품질 우선**: 100건을 성실히 완역하기 어렵다고 판단되면 건수를 줄인다(60건도 정상).

## 절차
`D=$(TZ=Asia/Seoul date +%F)`, 표식 브랜치 `B=claude/findings-translate-$D`, 스크래치 `/tmp/tr`.

0. **이미 했나:** 원격에 `$B` 가 있거나, 제목이 `chore(findings): 일일 번역 배치 $D` 인 PR(열림·닫힘 모두, `pulls?state=all&per_page=30`)이 있으면
   `스킵: 오늘 이미 함` 으로 끝낸다. 어제 이전 배치 PR 이 열려 있으면 번호와 CI 상태만 기록한다(머지는 코드가 한다).
1. `git checkout -b $B origin/main`.
2. anon 키·URL 을 `company/CLOUD.md` 방법으로 얻어 `SUPABASE_URL`·`SUPABASE_ANON_KEY` 로 export. 못 얻으면 `보류: 네트워크` 로 끝낸다.
3. `python3 findings_translate.py --export --source supabase --output /tmp/tr/plan.json --pretty`.
4. **브릿지 가드:** `plan.json` 의 `queue_source` 가 `"rpc"` 가 아니면 번역하지 말고
   `★브릿지(009 RPC) 미적용 — 번역 파이프라인이 미번역 행을 볼 수 없음` 을 맨 위에 쓰고 끝낸다.
5. `source_db.untranslated` 가 0 이면 `git push origin origin/main:refs/heads/$B`(표식만) 후 `오늘 번역할 신규 항목 없음` 으로 끝낸다.
6. `items[]` 에서 최신순 최대 100건을 골라 `finding_text_ko` 를 채우고 `translation_method` 를 `"llm_assisted"` 로 둔 완성본을
   **별도 파일** `/tmp/tr/filled.json` 에 쓴다(고른 항목만 담는다). `plan.json` 은 절대 고치지 않는다 — 적용 도구가 원문을 바이트로 대조해,
   한 글자라도 다르면 배치 전체를 거부한다. 번역 지침(반드시 지킨다):
   - 독자: 한국 제약사 GMP/QA 실무자. 문체는 간결한 보고체("~했다/~하지 않았다").
   - 업계 고유 개념은 원어 유지 또는 병기: 무균공정(aseptic), 배지충전(media fill), 일탈(deviation), 허위표시(misbranded),
     청정구역(critical area), 일방향 기류(unidirectional airflow), 제조기록(batch record).
   - 법 조항 인용(21 U.S.C., 21 CFR, FD&C Act §, 약사법 등)은 원문 표기 그대로.
   - 회사명·URL·기관명·문서번호·날짜·수치는 원문 그대로.
   - **사실 왜곡 금지**: 없는 내용 추가 금지, 수치·날짜·조항 번호 변경 금지. 긴 원문도 요약하지 말고 완역한다.
   - `finding_text_ko` 는 원문과 같은 문자열이면 안 되고 한글을 1자 이상 담아야 한다(위반 시 배치 전체 거부).
   - 원문이 한국어(MFDS 등)인 표 형태 지적사항은 문장형 평문으로 다듬되 사실·수치·조항 번호는 그대로.
6-1. **WL 절단 복원(restore)은 클라우드에서 하지 않는다** — 계획이 Actions 아티팩트에만 있다(`/actions` 막힘).
   결과에 `막힘: Actions — restore 계획 아티팩트` 한 줄만 남긴다. restore 는 PC 백업이 남은 용량으로 채운다.
7. `python3 findings_translate.py --apply /tmp/tr/filled.json --source supabase --sql-output /tmp/tr/$D.sql --outbox-output translations/outbox/$D-batch.json --output /tmp/tr/apply_report.json --pretty`
   (`--sql-output` 은 필수다. SQL 은 저장소 밖에 쓴다).
8. `apply_report.json` 의 `ready` 가 `false` 면 아무것도 커밋하지 말고, 어떤 항목이 왜 실패했는지 요약해 `실패` 로 끝낸다(우회 금지).
9. **outbox 정리(기아 방지):** `translations/outbox/` 에 오늘 것 말고 다른 `*.json` 이 있으면, 그 파일들을 마지막으로 바꾼 main 커밋
   (`git log -1 --format=%H origin/main -- translations/outbox`)의 check-runs 중 이름이 `Apply queued translation outbox → Supabase` 인 것의
   `conclusion` 이 `success` 일 때만 `git mv` 로 `translations/applied/` 에 옮긴다. 아니면 그대로 두고 `apply 미확인으로 outbox 정리 보류` 를 남긴다.
10. `git add translations/outbox/$D-batch.json`(9 의 이동분은 이미 스테이징됨). 커밋 `chore(findings): 일일 번역 배치 $D - N건`. `git push -u origin $B`.
11. PR(`gh api`) — 제목 **`chore(findings): 일일 번역 배치 $D`**, 본문: N건 번역(상한 100·최신 우선, 잔여 M건은 다음 실행), dry-run 검증 통과(ready=true),
    머지되면 `grm-findings-translate-apply.yml` 이 Supabase 에 반영. **머지하지 않는다** — CI 초록 3시간 뒤 워치독이 머지한다.
12. **번역 표본 판정:** `translations/applied/` 에서 오늘 것을 뺀 가장 최근 배치 파일(20행 미만이면 그 앞 파일까지, 최대 20행)을 읽어
    각 행을 판정한다 — ① 의미 보존(없는 사실·주체·제품·수치·원인 추가 없음, 중요 내용 누락 없음) ② 극성 보존(미실시·부적합·없음·not·failed to 유지)
    ③ 숫자·날짜·단위·조항 인용 그대로 ④ 원문이 한국어면 원문 범위를 벗어나지 않음 ⑤ 고유명사 글자 일치. 하나라도 어긋나면 FAIL, 어색하지만 충실하면 주의.
    **FAIL 이 3행 이상이거나 20% 를 넘으면** 이슈를 연다(제목 `번역 품질 표본 판정 — {배치 날짜} {n}/{m} 실패`, 라벨 `automation-warning`,
    본문 = finding_id·기준·사유 표). 데이터는 고치지 않는다.

## 결과 (최종 메시지)
맨 위 3줄(`CHARTER.md`) 다음에 한 줄씩: 번역 N건 · 잔여 미번역 M건 · outbox 정리 여부 · PR 번호 · 표본 판정 `m행 중 FAIL n·주의 k`.

## 금지
Supabase 직접 쓰기(커넥터 포함) · 코드 수정(`grm_findings.py`·`findings_extractors.py`·`findings_translate.py` 등) · 검증 우회 · `main` push·머지.
