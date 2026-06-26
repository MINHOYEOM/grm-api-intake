# `web/data/briefs/` — 발행 입력(공유 저장소 결합 seam, P3·C4)

이 디렉터리는 **수집(Action A)과 발행(Action B)의 유일한 결합점**이다(D8). 두 Action 은
서로를 직접 호출하지 않는다 — routine 이 만든 **grm-web-card/v1 JSON** 을 여기에 커밋하면,
그 커밋이 `web/**` 변경으로 `GRM Web Deploy`(`.github/workflows/grm-web-deploy.yml`)를
트리거한다(linkcheck → build → 미리보기 → 사람 승인 시 라이브).

## 규약
- **주차당 1파일.** 파일명 = `brief_web_{publish_date}.json` (예: `brief_web_2026_06_22.json`).
  - 렌더러는 디렉터리의 `*.json` 을 파일명 정렬로 순회하고 **내부 `brief.publish_date`** 로
    issue 번호·slug 를 부여하므로 파일명 자체는 정렬 결정론에만 쓰인다. 날짜 접두 권장.
- **스키마** = `grm-web-card/v1`(`card_scaffold.assemble_web_brief` 산출). 최상위 키:
  `schema_version` · `provenance` · `brief` · `cards`.
- **`link_check` 는 `pending` 으로 둔다.** 실제 200 체크 주입(ok/broken/degraded)은 배포단계
  `web/linkcheck.py`(C1)가 비파괴로 수행한다 — 이 디렉터리의 원본은 `pending` 유지가 정상.
- **중복 `publish_date` 금지.** 같은 publish_date 의 두 파일은 slug 충돌 → 렌더러가 즉시
  fail-loud(조용한 덮어쓰기 방지). 같은 주차 **재발행**은 같은 파일을 덮어쓰는 것(파일 1개
  유지)으로 하며, 이는 **사람 판단**이다.

## 결합 흐름(D8)
```
routine(Claude, 사람 실행)
  └─ grm-web-card/v1 JSON 산출
       └─ web/data/briefs/brief_web_{date}.json 로 커밋   ← 통합 seam(사람/소형 스크립트)
            └─ push → GRM Web Deploy(Action B) 트리거
                 ├─ linkcheck(C1·비차단) → link_check enrich
                 ├─ render(결정론) → dist
                 ├─ 미리보기(preview URL + dist 아티팩트)
                 └─ [사람 승인] production 브랜치 머지 = 라이브(D5)
```

> Notion→JSON→commit 자동 파이프는 **후속(필요 시)**. P3 는 이 디렉터리 규약과 트리거만
> 확정한다 — 현재 seam 은 사람/소형 스크립트 단계.
