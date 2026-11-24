# 종합상황실 · 월요일 발행 준비 확인 — 모델 Haiku · 월 10:45 KST

PC 예약작업 `grm-monday-brief-publish` 의 **확인 부분**을 옮긴 것이다. 클라우드는 GitHub Actions 를 직접 부를 수 없고(`company/CLOUD.md`)
심층분석을 새로 쓰는 일은 Haiku 의 몫이 아니므로, **확인·판정·기록과 아래 "복구"의 워크플로 재실행 요청만** 한다. 심층분석 백필은 PC 백업(월 11:30)이 한다 —
이 파일의 결과가 `상태: 완료` 가 아니면 PC 백업이 전체 절차를 돈다.

## 읽을 것
`company/CHARTER.md` · `company/CLOUD.md` · 이 파일.

## 값
`D` = 이번 주 발행일 = 오늘(KST) 날짜. `UNDER` = `D` 의 `-` 를 `_` 로. 결과 파일 = 브랜치 `claude/company-$D` 의 `company/run/$D/brief-prep.md`.
Notion 은 보지 않는다 — 발행 길에서 빠졌다(편집팀 `company/editorial/RUN.md` 0항). v16 백업은 꺼졌고, 백업 v17(`company/editorial/backup.md`, 월 09:30)이
편집팀이 못 넘긴 호를 같은 브랜치의 `company/run/$D/backup/` 에 따로 반납한다(편집팀과 같은 파일·형식).

## 확인 (순서대로, 고치지 않는다)
1. **델타 반납** — `git fetch origin claude/company-$D` 후 `company/run/$D/delta.json` 이 있고 `copyedit.json` 의 `status` 가 `완료`면 정상(`git 반납 있음`).
   편집팀 것이 아니면 `company/run/$D/backup/` 의 같은 두 파일을 본다 — `copyedit.json` 이 `완료`면 정상(`git 반납 있음(백업)`).
   백업 폴더에 `claims/backup.run.claim` 은 있는데 `copyedit.json` 이 없으면 `대기: 백업 진행 중`(백업은 13:15 KST 까지 반납한다 — 정상 진행).
   이미 main 에 `web/data/deltas/delta_$UNDER.json` 이 있어도 정상이다(브릿지가 가져갔다). 셋 다 없으면 `★델타 없음`.
2. **main 반영** — `git fetch origin main` 후 `web/data/deltas/delta_$UNDER.json`·`deep_$UNDER.json` 이 있는지.
   git 반납이 있는데 파일이 없으면 브릿지가 아직 안 돈 것이다(크론이 월 14:37 KST 까지 30분마다 돈다) → `대기: 브릿지`.
   **스캐폴드 동결**도 본다 — main 의 `delta_$UNDER.json` 에 `handoff_ref` 키가 있으면(편집팀이 git handoff 로 만든 호) `web/data/scaffolds/brief_web_$UNDER.json` 도 main 에 있어야 한다.
   없으면 `★스캐폴드 동결 없음`(발행 조립이 다른 스캐폴드를 쓰게 된다 — `상태: 보류`). 백업 v17 이 반납한 호도 `handoff_ref` 가 있으므로 같은 규칙이다.
3. **발행 PR** — 열린 PR 중 head 가 `publish/brief-$D` 인 것. head sha 의 check-runs 가 전부 `success` 인지.
4. **심층분석** — PR 브랜치의 `web/data/briefs/brief_web_$UNDER.json` 을 `git show` 로 읽어, `"deep_analysis" in card` 인 카드마다
   ① `deep_analysis` 가 비어 있지 않은가 ② `fda-483` 카드는 `observations_ko` 의 `number` 가 `deterministic_detail.observations[].number` 와 1:1 이고
   `detail` 이 있는 관찰마다 `detail_ko` 가 있는가. 파이썬 몇 줄로 센다(눈으로 판정하지 않는다).
4-1. **영문 슬롯** — 같은 브리프 JSON 에서 `cards` 마다 `en` 의 다섯(`title_issue`·`summary`·`implication`·`key_facts`·`checks`)이 전부 비어 있지 않은지, `brief.en.tldr` 가 있는지 센다(파이썬 몇 줄). 한 장이라도 빠지면 이 호는 영어로 나가지 않고 CI test 3건(영문 홈·페이지군 대칭·링크드인 월간)이 실패한다. 빠진 카드 수·예 id 를 적는다. (코드 게이트: 저장소 `check_en_slots.py` — 발행 워크플로가 PR 전에 같은 판정을 한다.)
5. **편집팀 작동 여부** — 판정만 하고 `상태:` 판정(1~4)에는 넣지 않는다. 읽기만 한다.
   - 브랜치 `claude/company-$D` 의 `company/run/$D/` 에서 `desk.json` 의 `status`, `reporter.md` 첫 줄 `상태:`, `copyedit.json` 의 `status` 와 `published` 를 본다. 파일이 없으면 "없음".
   - 세 파일 중 `완료` 가 아닌 첫 단계(데스크 → 기자 → 교열)와 그 `상태:` 줄의 보류 사유를 적는다.

## 발행 PR 미리보기 점검

`grm-web-publish` 실행 요약의 **브리프 미리보기 5유형 점검**에서 결과를 한 번에 본다. 저장소에 발행본이 있으면 `python brief_preview_check.py YYYY-MM-DD` 로도 표를 볼 수 있다. 표의 경고는 검토에 쓰며 기존 발행 게이트는 별도로 적용된다.

| 확인 유형 | 기존 자동 확인 | 미리보기 요약에서 추가로 확인하는 범위 |
| --- | --- | --- |
| 개인 실명 | 기자·교열의 `precheck` / `guess_check`가 원문 수신인과 개인 사업자 이름을 대조한다. | 최종 발행본의 산문·업체 표기를 같은 함수로 대조한다. 스캐폴드가 있으면 원문 대조에 사용한다. |
| 영문 한글 | `check_en_slots`가 해외 기관 카드의 공용 제목·사실 값에 남는 한글을 검사한다. | 카드 영문 서사·훅과 영문 TLDR도 검사한다. |
| 빈 칸 | `check_en_slots`가 필수 영문 슬롯을, 교열의 `check_render_gate`와 사이트 렌더가 국문 병기 등 렌더 조건을 검사한다. | 두 검사와 함께 국문 제목·요약 및 빈 심층분석을 확인한다. |
| 옛 실사 표시 | 렌더가 `firm_groups.is_old_inspection`으로 실사일과 게시일을 비교해 표시를 만든다. | 표시 대상과 렌더 뷰의 날짜·표시를 대조한다. |
| 묶음 카드 | 조립·`precheck`가 그룹 등록 후보를 경고한다. 렌더는 같은 그룹·FEI 카드의 상자를 만들고 FDA 본문 결손 점검은 결손 문서를 집계한다. | 렌더 뷰의 그룹 상자, 발행본의 병합 카드와 그룹 등록 후보를 집계한다. |

실명 대조에는 저장된 원문 필드를 사용한다. 음역과 원문에 없는 이름은 사람이 확인한다. 옛 실사와 그룹 상자는 렌더 뷰 기준으로 집계하며 실제 화면 배치는 PR 미리보기에서 확인한다.

## 판정
- 1~4(+4-1) 가 전부 정상 → `상태: 완료` · `대표가 볼 것: 관리자 사이트에서 발행 승인`.
- 하나라도 아니면 → `상태: 보류` · 무엇이 빠졌는지(★영문 슬롯 미완결(n/N장) · ★델타 없음/오예치 · 대기: 브릿지 · 발행 PR 없음·CI 실패 · deep 누락 카드 id·층) 한 줄씩.
  `★델타 없음` 이면 `대표가 볼 것: 편집팀·백업 모두 이번 호를 반납하지 못함 — company/run/$D/backup/backup.md 첫 줄 참고, 이번 호 처리 결정` 을 쓴다.
  `대기: 백업 진행 중` 은 `상태: 보류` 이되 `대표가 볼 것: 없음(백업 진행 중 — 13:40 이후 다시 확인)` 이다.
- 편집팀 판정 (5번) — 셋 중 하나만 쓴다. 미작동이 아닌 경우도 원인 한 줄을 붙인다.
  - `작동`: desk·reporter·copyedit 전부 `완료` 이고 `copyedit.json` 의 `published: true`.
  - `부분 작동(멈춘 단계: 데스크|기자|교열|마감)`: 파일 중 하나 이상은 있으나 위 조건이 아니다. 교열이 `완료` 인데 `published` 가 `false` 면 멈춘 단계는 `마감`.
  - `미작동`: 세 파일이 전부 없거나 데스크가 `완료` 가 아니다. 이번 호를 이어받았는지는 아래 백업 줄로 본다.
  - 형식: `편집팀: <판정> — <원인 한 줄>` (원인은 멈춘 단계의 보류 사유, 파일 없음이면 `브랜치·파일 없음`).
- 백업 판정(5-1) — `company/run/$D/backup/` 을 읽기만 한다. 하나만 쓴다: `백업: 반납`(`copyedit.json` `완료`) · `백업: 진행 중`(claim 만 있음) ·
  `백업: 못 함 — <backup.md 첫 줄>` · `백업: 안 함`(폴더 없음 — 편집팀이 넘겼거나 백업이 `SKIP`). `상태:` 판정에는 넣지 않는다(1번이 이미 본다).

## 복구 — 워크플로 재실행 요청 (확인 2·3번에서 막혔을 때만, 한 번씩)
`git fetch origin claude/ops-status` 후 `git show FETCH_HEAD:status.json` 으로 최근 실행을 먼저 본다(이미 돌고 있으면 요청하지 않는다).
- git 반납이 있는데 `delta_$UNDER.json` 이 main 에 없고 `grm-delta-bridge.yml` 최근 실행이 `in_progress` 가 아니다 → `delta-bridge` 요청(`publish_date=$D`).
- 델타 파일은 있는데 발행 PR 이 없고 `grm-web-publish.yml` 최근 실행이 `in_progress` 가 아니다 → `web-publish` 요청(`publish_date=$D`, `intake_run_id` 는 비운다 — 날짜로 찾는다).
- 요청 방법·형식은 `company/CLOUD.md` "Actions 다리". 브랜치는 `claude/request-$D-<delta-bridge|web-publish>`. 요청 id 는 `YYYYMMDD-HHMM-bridge` 처럼 만든다.
- 요청 뒤 결과 파일(`claude/ops-status` 의 `requests/<id>.json`)을 5분쯤 뒤 한 번 확인한다. `rejected`·`dispatch-failed` 면 이유를 적고 `상태: 보류`. `dispatched` 여도 이번 실행의 판정은 바꾸지 않는다(`대기: 재실행 요청함` 으로 적는다).
- 마커 파일이 안 생기거나 push 가 거절되면 `막힘: Actions — <하려던 일>` 로 남긴다(PC 백업이 한다).

## 기록
`if git fetch origin claude/company-$D; then git checkout -B claude/company-$D FETCH_HEAD; else git fetch origin main && git checkout -b claude/company-$D FETCH_HEAD; fi` — 있으면 그 위에, 없으면 방금 받은 main(`FETCH_HEAD`)에서 브랜치를 만든다. `origin/main` 이라는 이름은 쓰지 않는다(실행 환경의 것은 며칠 전 것일 수 있다 — 2026-11-13 리허설: 11-06 커밋). `company/run/$D/brief-prep.md` 하나만 쓰고 커밋·push.
첫 3줄은 `CHARTER.md` 형식(`상태:` 로 시작) — PC 백업이 이 첫 줄을 읽고 건너뛸지 정한다. 3줄 바로 아래 4번째 줄에 `편집팀: …` 한 줄(5번 판정), 5번째 줄에 `백업: …` 한 줄(5-1)을 둔다. 다른 직무의 파일은 건드리지 않는다.
- 월 10:45 판정 때 `python company/editorial/stage_timing.py "$D"` 출력의 마지막 `단계 실측: …` 한 줄을 결과 파일 `brief-prep.md` 의 6번째 줄에 붙인다. 실행할 수 없으면 `단계 실측: 확인 불가(<사유>)`를 적는다.

## 금지
발행 PR 머지 · Notion 읽기·쓰기 · 심층분석 생성 · Supabase 쓰기 · 허용 동작 밖의 워크플로 요청.
