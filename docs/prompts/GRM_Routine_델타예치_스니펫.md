# GRM Routine — 델타 Notion 예치 스니펫 (붙여넣기용, 2026-07-07)

> **용도**: 클라우드 Routine("Global Regulatory Monitoring [GRM]")의 `docs/prompts/GRM_Prompt_v16.md`
> `[산출물]` 절 뒤에 아래 블록을 **그대로 붙여넣는다**. 기존 6슬롯 계약·[출력]·[2단계]·기존
> [산출물] 문단은 **한 글자도 바꾸지 않는다** — 이 스니펫은 순수 additive 후행 단계다.
> repo 쪽 반영(`docs/prompts/GRM_Prompt_v16.md`)은 이미 되어 있다 — 이 파일은 **클라우드 Routine
> 자체**(Notion 커넥터만 있는 환경)에 사람이 직접 복사해 넣기 위한 사본이다.
> 배경·설계 근거: `GRM_웹발행_클라우드자동화_설계_2026-07-07.md` §2(Fix A).

---

## 붙여넣을 블록 (그대로 복사)

```
[산출물 — 델타 Notion 예치 (클라우드 델타 브릿지, 2026-07-07 additive)]
너(클라우드 Routine)는 git 에 쓸 수 없다 — 완성한 슬롯 델타 JSON 을 **Notion Intake DB**
(`7784c71fb7b343749b2bee5d04db7926`, 이 handoff 를 읽어온 것과 같은 DB)에 **페이지로 예치**하면,
별도 자동화(`grm-delta-bridge.yml` + `delta_bridge.py`)가 그 반대편에서 읽어 git 커밋 → 발행
파이프로 넘긴다(네가 handoff 를 Notion 에서 읽어오는 것과 정확히 대칭 — 이번엔 네가 쓰는 쪽).
  - **새 페이지 1개** 생성(Intake DB): 제목 `OPEN GRM Web Delta {publish_date}`
    (예: `OPEN GRM Web Delta 2026-07-13`, `publish_date` = 이 handoff 의 `run_date_kst`).
  - 속성: `Source` = `GRM Web Delta` · `Type or Class` = `web-delta` · `Status` = `New`
    (옵션이 없으면 Notion 이 자동 생성 — handoff 페이지 생성 규약과 동형).
  - 본문에 **코드 블록 1개**로 네 [산출물] 슬롯 델타 JSON 을 그대로 붙여넣는다(형태 불변 —
    `{"cards":{"<card.id>":{...슬롯...}},"tldr":[...]}`, `publish_date` 키를 최상위에 추가해도 무방).
  - [2단계] 심층분석(deep_analysis)을 수행했다면, **같은 페이지에 두 번째 코드 블록**으로
    `{"<card.id>":{"deep_analysis":{...4섹션...},"source_text":"..."}}` 델타를 추가로 붙인다
    (별도 페이지 `OPEN GRM Web Deep Delta {date}` + `Type or Class`=`web-deep-delta` 로 대신해도 됨).
    심층분석이 없으면(대다수 주) 이 블록은 생략 — 정상.
  - **멱등**: 같은 주에 이 단계를 다시 수행하게 되면(재실행) 새 페이지를 또 만들지 말고
    **기존 `OPEN GRM Web Delta {date}` 페이지 본문을 덮어쓴다**(handoff 의 upsert 규약과 동형).
  - 이 예치는 [Status 갱신]·handoff CONSUMED 처리와 **별개**다 — 델타 페이지의 CONSUMED 전환은
    브릿지 자동화가 git 커밋 성공 후에 처리한다(너는 예치만 하면 된다. 페이지 Status 를 직접
    바꾸지 않는다).
  - 위 [산출물] 문단의 "산출 JSON → `web/data/briefs/` 커밋" 운영 흐름 자체는 불변 — 이 예치는
    "네 컴퓨터가 꺼져 있어도 그 커밋이 자동으로 일어나게" 만드는 추가 단계일 뿐이다.
```

---

## 배경 (왜 필요한가)

- 2026-07-06 근본 원인(실측): `grm-web-publish.yml` 이 `web/data/deltas/delta_{date}.json` 없음
  으로 exit 1 — 그 델타가 **한 번도 커밋된 적이 없었다**(라이브 반영은 그날 수동 서지컬 병합으로
  처리). 원인 = Routine 산출 델타를 git 으로 옮기는 다리가 코드에 없었기 때문.
- 클라우드 Routine 은 Notion 커넥터만 있고 GitHub 커넥터가 없다(2026-07-07 확인) — 즉 델타를
  직접 git 에 커밋할 방법이 없다.
- 해법 = 수집기가 handoff 를 Notion 에 남기는 것과 **대칭**으로, Routine 도 산출물을 Notion 에
  남긴다. `grm-delta-bridge.yml`(GitHub Actions, 매주 월 09:30 KST + 수동 실행)이 그 페이지를
  읽어 git 커밋 + 발행 파이프(`grm-web-publish.yml`)를 자동으로 트리거한다.
- 사람의 역할은 이 예치 이후로는 없다 — 발행 준비(스캐폴드 → 델타 브릿지 → 조립 → 프리뷰)까지
  전부 클라우드에서 진행되고, 사람은 Admin 콘솔에서 **미리보기 확인 후 승인(1클릭)** 만 한다
  (Fix B, 별도 트랙). "무인 라이브 0" 은 그대로 유지 — 브릿지는 델타 커밋까지만 하고 절대
  라이브(main 프로덕션 배포)를 직접 트리거하지 않는다.

## 확인 체크리스트 (붙여넣은 뒤 1회)

1. Routine 프롬프트에 위 블록이 [산출물] 절 뒤(기존 문단 다음)에 있는지 확인.
2. 다음 월요일 실행 후 Notion Intake DB 에 `OPEN GRM Web Delta {그 주 날짜}` 페이지가
   생성되었는지 확인(제목·속성 3종·본문 코드 블록).
3. `grm-delta-bridge.yml` 워크플로 실행 로그에서 그 페이지를 읽어 `web/data/deltas/delta_*.json`
   커밋이 발생했는지 확인 — 성공하면 페이지 제목이 `CONSUMED GRM Web Delta {date}` 로 바뀐다.
