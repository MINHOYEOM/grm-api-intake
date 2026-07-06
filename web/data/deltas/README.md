# `web/data/deltas/` — 라우틴 슬롯 델타 착지점 (발행 파이프 시작점)

라우틴(Claude)이 산출한 **슬롯 델타** JSON 을 여기에 커밋하면 `grm-web-publish.yml`(자동 조립 →
PR)이 트리거된다. 이 폴더가 "라우틴 산출물 → 발행 파이프"의 유일한 결합점이다.

## 규약
- **주차당 1파일** `delta_{YYYY_MM_DD}.json` (예: `delta_2026_07_06.json`).
- 내용 = 라우틴 산출 델타 그대로: `{"cards": {"<card.id>": {슬롯...}}, "tldr": [3개]}`.
  - 슬롯 = `title_issue`·`summary`·`key_facts[]`·`implication`·`checks[]`·(비KO)`quotes_translation[]`.
  - 코드 verbatim 필드(facts·sources·배지·render_order 등)는 **넣지 않는다**(스캐폴드가 소유).
- **중복 publish_date 금지.** 재발행 = 같은 파일 덮어쓰기(사람 판단).
- 이 델타는 `assemble_publish_brief.py` 가 빈슬롯 스캐폴드(grm-intake 아티팩트)와 합쳐 발행본을 만든다.
- **클라우드 예치 경로(2026-07-07)**: 사람 컴퓨터 없이도 이 폴더에 델타가 도착하도록, 클라우드 Routine
  이 Notion Intake DB 에 남긴 `OPEN GRM Web Delta {date}` 페이지를 `grm-delta-bridge.yml`+`delta_bridge.py`
  가 읽어 여기에 커밋한다(수동 커밋 대체 — 규약은 동일, 상세는 `docs/prompts/GRM_Routine_델타예치_스니펫.md`).

## 흐름
```
routine → delta_{date}.json 커밋
  └─ grm-web-publish.yml: 스캐폴드 다운로드 + assemble_publish_brief(채택 필터·메타 재계산)
       → web/data/briefs/brief_web_{date}.json 커밋 + PR
       → grm-web-deploy: 프리뷰 → [사람 머지 = 라이브]
```
