# 백업 Routine v17 초안 — git handoff 를 읽고 git 으로 반납한다 (2026-11-13, 초안)

**상태: 초안. 아직 어디에도 붙여 넣지 않는다.** 4단계(백업 길 전환)의 지시문 준비물이다.
현행은 `docs/prompts/GRM_Prompt_v16.md`(백업 Routine "Global Regulatory Monitoring [GRM]", 월 09:30 KST)이고, 이 문서는 **v16 에서 바뀌는 절만** 적는다. 카드 쓰는 법(§B [1단계]·[2단계]·[출력] 6슬롯·심층분석)은 한 글자도 바꾸지 않는다.

## 1. 왜 바꾸나

노션을 끄려면(5단계) 백업 Routine 이 노션에 기대는 네 가지가 먼저 없어져야 한다.

| v16 이 노션으로 하는 일 | v17 |
|---|---|
| 이번 주 handoff 를 노션에서 읽는다(최신 OPEN 1건) | 브랜치 `claude/queue-handoff` 의 `handoff_{날짜}.json` 을 읽는다 |
| "이미 발행됐나"를 노션 handoff 의 CONSUMED 로 안다 | git 에서 안다 — main 에 그 날짜 델타가 있거나, 편집팀 브랜치에 완료된 반납이 있으면 끝낸다 |
| 델타를 노션 페이지로 예치하고 글자 단위로 다시 대조한다 | 실행 폴더에 파일로 쓰고 push 한다(옮겨 적지 않으니 대조할 것이 없다) |
| 행 Status 를 하나씩 바꾸고 handoff 를 CONSUMED 로 닫는다 | **하지 않는다.** 행 상태는 코드가 main 의 발행 파일에서 계산한다(`grm_queue.reconcile`) |

지난주 재유입 가드(PL-10b — 지난 CONSUMED handoff 와 대조)도 필요 없어진다. DB handoff 는 아직 열려 있는 행(`new`·`deferred`)만 싣는다.

## 2. 먼저 확인할 것 (이것이 풀리기 전에는 v17 을 쓰지 않는다)

**백업 Routine 이 `claude/company-{날짜}` 브랜치에 push 할 수 있는가.**

- 비서실 확인(11-13): 백업 Routine 은 저장소 `grm-api-intake` 를 원본으로 쓰고, 결과 브랜치가 `claude/charming-mendel` 로 지정돼 있다. 편집팀 직무 Routine 들은 결과 브랜치가 비어 있어도 `claude/company-{날짜}` 에 push 해 온 기록이 있다(11-09). **백업 Routine 이 지정 브랜치가 아닌 다른 `claude/` 브랜치에 push 되는지는 본 적이 없다.**
- 확인 방법(주중 아무 날, 발행과 무관): 백업 Routine 과 같은 설정의 일회성 실행에 아래 한 가지만 시킨다.
  ```
  git checkout -b claude/company-rehearsal-push && mkdir -p company/run/rehearsal-push \
    && echo ok > company/run/rehearsal-push/probe.txt && git add company/run/rehearsal-push/probe.txt \
    && git commit -m "probe" && git push origin claude/company-rehearsal-push
  ```
  브릿지·발행 워크플로는 `claude/company-{YYYY-MM-DD}` 만 읽으므로 이 브랜치는 어디에도 닿지 않는다. 끝나면 브랜치를 지운다.
- 안 되면: (가) Routine 의 결과 브랜치 설정을 비우거나 바꿀 수 있는지 본다. (나) 그래도 안 되면 브릿지가 지정 브랜치(`claude/charming-mendel`)의 `company/run/{날짜}/` 도 읽게 하는 코드가 필요하다 — **변수로 꺼 둔 채** 따로 낸다(이 초안의 범위 밖).

## 3. 반납물 — 편집팀과 같은 폴더, 같은 파일

브릿지(`delta_bridge.load_git_run`)는 git 반납을 아래 조건으로만 받는다. 백업도 **같은 모양**으로 낸다(브릿지 코드 변경 없음).

브랜치 `claude/company-{실행일}` · 폴더 `company/run/{실행일}/`

| 파일 | 내용 | 브릿지가 보는 것 |
|---|---|---|
| `delta.json` | v16 [출력] 그대로의 슬롯 델타 + `handoff_ref`(읽은 handoff 의 날짜·지문·행 수) + `deferred`(보류한 행) | JSON dict |
| `deep_delta.json` | 심층분석 델타(있을 때만) | JSON dict |
| `scaffold.json` | 코드 검사에 쓴 스캐폴드 원문 그대로 | 그 날짜의 것 · 델타 카드를 전부 가짐 |
| `copyedit.json` | `{"status":"완료","code_gate":{"ran":true,"exit":0,...},"counts":{"reject":0,...},"cards":[...],"by":"backup"}` | `status=="완료"` · 코드 검사 통과 · reject 0 |

`by: "backup"` 은 누가 냈는지 남기는 표식이다(브릿지는 모르는 키를 통과시킨다).

## 4. v16 에서 바뀌는 절 (초안 문구)

### [0단계] handoff 소비 — 전체 교체

```
[0단계 — 이번 주 입력 읽기 (git)]
1. 이미 끝났는지 먼저 본다. 아래 둘 중 하나라도 참이면 "이미 발행됨 — 백업 불필요" 한 줄만 남기고 끝낸다.
   가. main 에 web/data/deltas/delta_{실행일 밑줄}.json 이 있다.
   나. git fetch origin claude/company-{실행일} 가 성공하고, 그 브랜치의
       company/run/{실행일}/copyedit.json 의 status 가 "완료" 다.
2. handoff 를 읽는다.
     git fetch origin claude/queue-handoff
     git show FETCH_HEAD:handoff_{실행일}.json > _handoff.json
   파일이 없으면 "handoff 없음" 으로 끝낸다. 다른 날짜의 파일로 대신하지 않는다. 검색으로 대신하지도 않는다.
3. 참조를 코드로 만든다(직접 세지 않는다).
     python company/editorial/handoff_ref.py _handoff.json
   출력(JSON 한 줄)을 그대로 델타의 handoff_ref 로 쓴다.   (handoff_ref.py 는 #1627 에 들어 있다)
4. rows[] 로 처리 목록을 만든다(v16 과 같다 — 병합 멤버·원문 미확보 행 규칙 포함).
   page_id 는 없다. 행을 가리킬 때는 source::document_id 를 쓴다.
```

삭제: PL-10(handoff CONSUMED 마감) · PL-10b(지난주 재유입 가드) · "Notion MCP 사용 불가 시" 폴백.

### [Status 갱신] — 절 전체 삭제

행 상태·handoff 닫기는 코드가 한다. 보류하는 행만 델타에 남긴다:

```
보류(다음 주로 넘김)한 행이 있으면 델타 최상위에 적는다.
  "deferred": [{"source": "...", "document_id": "...", "reason": "한 줄"}]
보류가 아닌 행은 따로 적지 않는다 — 카드가 된 행·건너뛴 행은 코드가 발행 파일을 보고 가른다.
```

### [산출물 — 델타 Notion 예치] — 전체 교체

```
[산출물 — 실행 폴더에 쓰고 push]
D=company/run/{실행일}; U=$(echo {실행일} | tr - _)
1. git checkout -b claude/company-{실행일}   (이미 있으면 그 브랜치에서 이어 쓴다)
2. $D/delta.json 에 슬롯 델타를 쓴다(handoff_ref·deferred 포함). 심층분석이 있으면 $D/deep_delta.json.
3. 스캐폴드를 받는다.
     git show FETCH_HEAD:brief_web_$U.json > $D/scaffold.json      (FETCH_HEAD = claude/queue-handoff)
4. 코드 검사.
     python assemble_publish_brief.py --scaffold $D/scaffold.json --delta $D/delta.json \
       --out $D/brief_check.json [--deep $D/deep_delta.json]
   exit 0 이 아니면 메시지를 읽고 델타를 고쳐 다시 돌린다. 세 번 해도 안 되면 copyedit.json 에
   status "실패" 와 그 메시지를 적고 push 한 뒤 끝낸다(발행되지 않는다 — 사람이 본다).
5. $D/copyedit.json 을 쓴다. status "완료" · code_gate {ran:true, exit:0} · counts.reject 0 · by "backup".
6. git add $D && git commit -m "backup run {실행일}" && git push origin claude/company-{실행일}
7. push 가 거부되면 한 번 git pull --rebase 후 다시 push 한다. 그래도 안 되면 그 사실을 한 줄로 남기고 끝낸다.
노션에는 아무것도 쓰지 않는다.
```

삭제: 예치 페이지 부모 지정·자기검증·블록 경계 공백 검증·델타 페이지 Status 관련 문단 전부.

### [K4 경계 — 임시 책임 고지] — 삭제

"발행 후 Status 갱신·handoff CONSUMED 는 Routine 책임" 문단이 사라진다(그 책임이 코드로 갔다).

## 5. 편집팀과 겹칠 때

- 백업은 0단계 1번에서 편집팀의 완료를 보면 아무것도 하지 않는다.
- 편집팀이 `보류`·`실패`로 끝났으면 같은 브랜치에 반납 파일이 일부 있을 수 있다. 백업은 그 브랜치에서 이어서 `delta.json`·`copyedit.json` 을 **덮어쓴다**(편집팀의 샤드 파일은 건드리지 않는다).
- 둘이 거의 동시에 끝나 main 에 이미 다른 델타가 있으면 브릿지는 **먼저 기록된 쪽을 유지**하고 차이표만 남긴다(지금 규칙 그대로).

## 6. 켜는 순서 (4단계)

1. 2절의 push 확인.
2. 3단계(편집팀 git handoff)가 2회 발행에 성공한 뒤 — 주 경로와 백업 경로를 같은 주에 바꾸지 않는다.
3. 주중 리허설 1회: 지난 월요일 handoff 로 v17 을 `claude/company-rehearsal-backup` 브랜치·`company/run/rehearsal-backup/` 폴더에 돌린다. 판정은 편집팀 리허설과 같다(`company/editorial/rehearsal_check.py`).
4. 대표가 Routine 프롬프트를 v17 로 바꾼다(저장소 파일을 읽는 방식인지 붙여 넣는 방식인지는 그때 확인 — 은퇴 목록 8절).
5. 그 주 월요일 편집팀이 정상 발행하면 백업은 0단계 1번에서 끝난다. 그것이 "정상 동작 1회"다. 백업이 실제로 발행해 보는 확인은 리허설로 한다.

되돌리기 = 프롬프트를 v16 으로 되돌린다(노션 handoff 는 5단계 전까지 계속 만들어진다).

## 7. 4단계가 끝나면 열리는 것 (코드는 준비돼 있고 변수로 꺼져 있다)

| 변수 | 값 | 하는 일 | PR |
|---|---|---|---|
| `GRM_HANDOFF_SOURCE` | `db` | 발행 스캐폴드·handoff 성패의 정본을 DB handoff 로 | #1675 |
| `GRM_INTAKE_DEDUP_SOURCE` | `supabase` | 중복 판정·대기열 넣기 기준을 DB 로 | #1641(main) |
| `GRM_INTAKE_NOTION` | `off` | 수집기·원천 대조·483/WL 노션 동기화·노션 Status 기록이 노션을 쓰지도 읽지도 않음 | #1676 · #1677 |

`GRM_INTAKE_NOTION=off` 하나가 위 두 변수를 함께 답하므로, 마지막에는 이 변수 하나만 바꾸면 된다. 앞의 둘은 **노션을 살려 둔 채 한 가지씩 먼저 옮겨 보는** 중간 단계용이다.

## 8. 아직 없는 것

- 델타 브릿지의 노션 예비 길 끄기(git 반납이 없을 때 노션 예치를 읽는 길). 백업이 v17 로 옮겨지면 노션에 예치가 생기지 않으므로 그 길은 빈손으로 돌아온다 — 5단계에서 코드째 지운다.
- 심층분석 원문 정본(`GRM_HANDOFF_BODY_SOURCE=git`)은 `handoff_ref` 가 있는 호에서 변수 없이도 git 을 쓴다(#1630). v17 델타에는 `handoff_ref` 가 붙으므로 따로 할 일이 없다.
