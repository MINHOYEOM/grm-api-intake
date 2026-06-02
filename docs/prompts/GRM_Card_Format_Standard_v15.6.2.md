# GRM Card Format Standard v15.6.2

> 확정일: 2026-06-01  
> 목적: GRM Weekly Brief 사례 카드(규제 항목 1건 = 카드 1장)의 자동 생성 포맷을 고정한다.

## 1. 핵심 결정

- 배지는 국가코드가 아니라 규제기관을 쓴다: `FDA`, `MFDS`, `EMA`, `MHRA`, `PIC/S`, `ECA`.
- Signal 은 방향을 함께 쓴다: `Signal High (T3)`, `Signal Medium (T2)`, `Signal Low (T1)`.
- 원문 인용은 Evidence A(1차 공식문서 직접 확인)일 때만 사용한다. Evidence B/C 는 요약만 작성한다.
- 학습 포인트 강제 슬롯은 만들지 않는다. 시사점은 규제 동향·변화·신설과 우리 대응 관점으로 작성한다.
- 핵심 사실은 무채색 블록으로 둔다. 카드 내 유일한 파랑은 한 줄 요약이다.

## 2. 색 규칙

- 파랑: 한 줄 요약만.
- 무채색: 원문 인용 라벨, 핵심 사실, 한눈에 표.
- 회색: 한국어 번역, 출처, 커버리지, AI 면책.
- 노랑: 시사점.
- 초록: 점검 사항.

## 3. 카드 제목

모든 카드는 8블록 밖에 제목을 둔다.

```md
### {카테고리 이모지} [{유형 · 규제기관 또는 소재국}] {업체/제품/문서명} — {핵심 이슈} `{Document ID 또는 문서번호}`
```

국가코드 단독 표기 금지. MFDS 해외제조소 실사는 관할(MFDS)과 소재국(Site Country)을 혼동하지 않는다.

## 4. W1-W8

W1 한 줄 요약: blue callout. 제품/문서, 사유/변경점, 규모/범위, 맥락을 1~2문장으로 쓴다. 배지는 Evidence + 규제기관 + Signal + 유형 핵심 태그 3~5개.

W2 메타 표: 발행일, Evidence, Signal, 규제기관, 원문 언어를 공통으로 두고 유형별 필드를 추가한다. 긴 조항과 긴 처분 사유는 W5로 내린다.

W3 원문 인용: Evidence A일 때만 생성한다. 핵심 원문 1~3줄 또는 250자 이내. `>`는 여기서만 쓴다.

W4 한국어 번역: 비KO 원문일 때만 회색 callout으로 생성한다. 헤더는 `한국어 번역(비공식, 원문 언어: EN)` 형식.

W5 핵심 사실: 무채색 callout. bullet 3~5개, `분야: 내용` 구조. 마지막은 `⚠ 리스크/적용조건`으로 두되 근거가 약하면 `추가 파급은 원문상 확인 불가`라고 쓴다.

W6 시사점: 노란 callout. 규제 동향·변화·신설 또는 집행 방향과 우리 QA/RA 대응 관점. 3문장 이하. 새 사실 금지.

W7 점검 사항: 초록 callout. 3~5개. 각 항목은 대상 문서/기록, 범위, 구체 동사를 포함한다.

W8 출처 푸터: 회색 callout. 정보출처와 공식원본 직링크를 모두 둔다. Evidence/Signal 범례는 카드마다 반복하지 않고 페이지 하단에 1회만 둔다.

## 5. 유형별 메타 추가 행

- admin-action: 업체(+소재국), 처분, 근거조항, 품목/공정.
- gmp-inspection: 제조소/업체, 소재국, 실사 구분, 실사 기간, 대상 제형/제품, 결론.
- recall-quality: 업체, 제품, Class, route/dosage form, 회수 사유, 범위/유통.
- warning-letter: 업체/제조소, 소재국, 실사일, 주요 CFR/CGMP 조항, 지적 분야.
- guidance/regulatory-change: 발행기관, 문서 단계, 주제/범위, 시행일/의견기한, 영향 대상.

## 6. Raw Support

Raw payload 는 W7이 아니다. 필요한 경우 W8 뒤에 선택 보조 toggle 로만 둔다.

~~~md
<details>
<summary>📦 Raw support (Evidence A 검증용)</summary>
	```json
	{필요한 raw payload 또는 긴 원문 필드}
	```
</details>
~~~

## 7. 출력 매트릭스

| 조건 | 출력 블록 |
|---|---|
| Evidence A + 비KO 원문 | W1~W8 모두 출력 |
| Evidence A + KO 원문 | W1, W2, W3, W5, W6, W7, W8 |
| Evidence B/C | W1, W2, W5, W6, W7, W8 |
| guidance/regulatory-change | Evidence/언어 조건을 따르되 W5는 변경 내용·일정·영향 대상 중심 |

## 8. 구현 위치

`GRM_Prompt_v15.6.md`의 `[Notion 페이지 출력]` 카드 표준 구간이 정본 구현 위치다.

## 9. 검증

아래 잔여 문자열이 카드 표준 구간에 남으면 실패로 본다.

```powershell
rg -n "블록 W7 \(Raw|핵심 사실.*blue_bg|번역 quote" GRM_Prompt_v15.6.md
```

아래 문자열은 있어야 한다.

```powershell
rg -n "Signal High \(T3\)|블록 W7 — 점검 사항|Raw support|출력 매트릭스|한국어 번역\(비공식" GRM_Prompt_v15.6.md
```
