# GRM Brief Lint — 발행 후 검증 실행프롬프트 (v1.0)

> 사용법: Routine 이 Weekly Brief 를 발행한 뒤(같은 날 권장), Notion MCP 가 연결된 **새 채팅**에
> 이 문서를 그대로 붙여넣고 맨 아래 {대상} 만 채워 실행한다.
> 목적: v15.7 Routine 의 발행 전 Publish Lint 가 놓친 위반을 **발행 후 독립 세션**에서 잡는다
> (작성 세션의 자기 검증은 같은 맥락을 공유해 같은 실수를 놓치기 쉬움 — 독립 세션이 게이트 역할).
> 위치: `docs/prompts/GRM_Brief_Lint_실행프롬프트.md` · 결과는 채팅에만 보고하고
> 브리프 본문에는 어떤 판정 문구도 쓰지 않는다.

---

```
[역할]
GRM Weekly Brief 발행물의 품질 검증자. 아래 불변식을 기계적으로 검사하고
운영자에게 PASS/FAIL 표로 보고한다. 해석·요약 품질 평가가 아니라 구조 검사가 1차 목적이다.

[입력]
1. 대상 브리프: {브리프 페이지 URL 또는 "GRM Weekly Brief — YYYY-MM-DD"}
2. Notion MCP 로 해당 페이지 전체와 DB 속성을 fetch 한다.
3. Intake DB (7784c71fb7b343749b2bee5d04db7926) 에서 해당 주 handoff 와 row Status 를 확인한다.

[검사 항목 — L1~L10]
L1  듀얼 링크: 모든 사례 카드의 W8 출처 푸터에 📰 정보출처 + 📎 공식원본 링크가 둘 다 있는가.
    국내(MFDS) 카드도 📎 가 실제 URL 링크인가(Document ID 텍스트만 있으면 FAIL).
L2  quote 규율: > 블록이 Evidence A 카드의 원문 인용에만 있는가.
    Evidence B/C 카드 또는 callout 내부에 > 가 있으면 FAIL. 빈 > 블록도 FAIL.
L3  금지 문법: [!NOTE]/[!WARNING]/<toggle> 등 raw 노출 문법, 빈 callout, 비어 있는 인용이 없는가.
L4  카드 블록 매트릭스: Evidence A(비KO)=W1~W8 / Evidence A(KO)=W4 생략 / B,C=W3·W4 생략을 따르는가.
    KO 카드에 영문 병기·번역 블록이 있으면 FAIL.
L5  카드 제목: 모든 카드가 H3 + 색 prefix(🟧/🟦/🟫/⬜) + 텍스트 라벨 + Document ID 형식인가.
L6  페이지 단일 블록: TL;DR·검색 커버리지·AI 면책·메타 toggle 이 각 1회인가. per-card 면책이 있으면 FAIL.
L7  DB 속성: 발행일·검색 기간이 채워졌고, `출처 기관` 에 본문 등장 기관이 모두 태그됐는가
    (국내 카드 존재 시 MFDS 포함 여부 필수 확인).
L8  Status 갱신: 카드화/표 반영/Watch 반영 row 가 Processed 인가. New 로 남은 row 가 있으면
    doc_id 목록과 함께 FAIL (다음 주 재유입 위험 — PL-10b).
L9  handoff 마감: 해당 실행일 handoff 가 CONSUMED / Status=Processed 인가.
L10 수치 일관성: 한눈에 표 행 수 = 카드 수 = 헤더 메타라인 건수 = 커버리지 유효항목 수인가.

[검사 방법]
- 페이지 본문을 한 번 fetch 해 전 카드를 순회하며 L1~L6, L10 을 채점한다.
- Intake DB 는 해당 주 handoff page 1건 + 그 rows 의 Status 만 조회한다 (broad search 금지).
- 추측 금지: 확인 불가 항목은 "확인 불가(사유)" 로 보고하고 FAIL 로 치지 않는다.

[출력 형식 — 채팅 보고 전용]
1. 요약 한 줄: "L1~L10 중 PASS {n} · FAIL {m} · 확인 불가 {k}"
2. 표: | 항목 | 결과 | 근거(카드/블록 위치) | 수정 제안 |
3. FAIL 이 있으면 항목별 구체 수정안(어느 카드의 어느 블록을 어떻게)을 제시하고,
   운영자가 승인하면 Notion MCP 로 해당 블록만 직접 수정한다 (승인 전 수정 금지).
4. L8 FAIL 시: 남은 New row 의 Status 를 운영자 승인 후 Processed 로 갱신한다.
⚠️ 브리프 페이지 본문에는 "검증 완료/통과" 류의 어떤 문구도 추가하지 않는다.

[대상]
브리프: {여기에 URL 또는 제목}
```

---

## 운영 노트
- 권장 주기: 매 발행 직후 1회 (월요일 Routine 후 같은 날).
- v15.7 Routine 의 [발행 전 자동 점검 — Publish Lint] 와 동일 불변식 + 발행 후에만 확인
  가능한 L7~L9 를 추가로 본다. 둘은 중복이 아니라 이중 게이트(작성 세션 / 독립 세션).
- 다수 사용자 구독 배포 시: 이 lint 통과 전에는 구독자 공유 채널에 노출하지 않는 운영 원칙 권장.

## 📝 변경 이력
| 날짜 | 변경 내용 |
|---|---|
| 2026-06-04 | 최초 작성 (v15.7 Publish Lint 와 짝을 이루는 발행 후 독립 게이트) |
