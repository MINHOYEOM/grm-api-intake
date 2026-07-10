# Q&A 파일럿 시나리오 (GRM Findings Copilot 커넥터)

F4c 부서 파일럿(생산·품질·RA) 전에 커넥터·에이전트 지침이 실제로 의도대로 동작하는지 확인하기 위한 시드 질문 12개다. 각 항목은 질문 예문, 기대 액션(호출돼야 할 operationId), 기대 응답 형태를 명시한다. 실제 답변 문구는 매번 조금씩 달라질 수 있으므로 "형태"만 고정 기준으로 삼는다.

## 집계형 (6개) — DB 전체/부분 요약

### 1. 최다 카테고리
- 질문 예문: "작년 FDA 무균 관련 최다 지적이 뭐야?"
- 기대 액션: `findingsCategoryMatrix` (또는 `findingsStats`의 `by_agency_category`로 연도 필터 없이 근사) — 연도 축이 필요하므로 `findingsCategoryMatrix` 우선.
- 기대 응답 형태: 카테고리 한국어 라벨 1~3개 + 해당 연도 건수 숫자. "작년"을 정확한 연도(YYYY)로 해석했는지, 숫자가 액션 결과에서 나온 값인지가 핵심 검증 포인트.

### 2. 연도 추이
- 질문 예문: "최근 3년간 데이터 완전성 지적이 늘었어 줄었어?"
- 기대 액션: `findingsCategoryMatrix`
- 기대 응답 형태: `category_code=data_integrity` 행의 연도별 cnt를 뽑아 증감 방향(늘었다/줄었다/비슷하다)과 연도별 숫자를 함께 제시.

### 3. 업체 랭킹
- 질문 예문: "지적 건수가 가장 많은 업체 상위 5곳 알려줘."
- 기대 액션: `findingsStats`
- 기대 응답 형태: `top_firms`에서 상위 5개 firm_name + cnt를 순위 목록으로 제시(30개 중 상위 5개만 자르는지 확인).

### 4. 특정 업체 이력
- 질문 예문: "삼성바이오로직스 지적 이력 요약해줘."
- 기대 액션: `findingsFirmStats` (body `{"p_firm": "삼성바이오로직스"}`)
- 기대 응답 형태: 총 건수, 최초/최근 지적일(first_seen/last_seen), 카테고리 분포 상위 몇 개. 업체명이 DB 표기와 다르면(예: 영문 표기) 0건으로 응답 — 이는 시나리오 11로 별도 검증.

### 5. 증거등급 구성
- 질문 예문: "지적사항 중에 증거등급 A는 몇 퍼센트야?"
- 기대 액션: `findingsStats`
- 기대 응답 형태: `by_evidence`의 A/B/C cnt를 가져와 A건수/전체건수 비율(%)을 계산해 제시. 비율 계산은 답변 작성 시 수행(액션이 직접 %를 반환하지 않음을 인지하고 있는지가 포인트).

### 6. 카테고리 x 연도 매트릭스
- 질문 예문: "카테고리별로 연도별 지적 건수를 표로 보여줘."
- 기대 액션: `findingsCategoryMatrix`
- 기대 응답 형태: category_code(한국어 라벨로 치환) x year 표 또는 목록. `years` 배열 순서(오름차순)를 그대로 따르는지 확인.

## row형 (4개) — 개별 지적사항 원문 인용

### 7. 특정 카테고리 최근 지적 국문 인용
- 질문 예문: "최근 환경모니터링 관련 지적 사례 하나 원문 인용해줘."
- 기대 액션: `findingsList` (`select`에 finding_text_ko·evidence_url 포함, `category_code=eq.environmental_monitoring`, `order=published_date.desc`, `limit=1~3`)
- 기대 응답 형태: finding_text_ko 원문 인용 + evidence_url 링크 병기. finding_text(영문)를 인용하지 않는지가 핵심 검증 포인트.

### 8. 특정 업체 최근 지적
- 질문 예문: "OOO제약의 가장 최근 지적 내용 알려줘."
- 기대 액션: `findingsList` (`firm_name=eq.OOO제약`, `order=published_date.desc`, `limit=1`)
- 기대 응답 형태: 지적 1건의 국문 요약 + 발행일 + evidence_url. 결과가 없으면 시나리오 12(존재하지 않는 업체)와 동일하게 정직히 0건 안내.

### 9. 무균 관련 최근 사례
- 질문 예문: "무균공정 관련 최근 FDA 지적 사례 2~3개만 보여줘."
- 기대 액션: `findingsList` (`category_code=eq.aseptic_sterility_assurance`, `agency=eq.FDA`, `order=published_date.desc`, `limit=3`)
- 기대 응답 형태: 최대 3건, 각각 업체명+발행일+국문 요약+evidence_url. "무균공정"이라는 자연어를 `aseptic_sterility_assurance` code로 정확히 매핑했는지가 핵심.

### 10. 근거 링크 요청
- 질문 예문: "방금 말한 지적사항 근거 원문 링크 줘."
- 기대 액션: 직전 턴에서 이미 받은 `findingsList` 결과의 `evidence_url`을 재사용(추가 호출 불필요) — 단, 이전 결과를 캐시하지 않는 구현이면 동일 조건으로 `findingsList` 재호출.
- 기대 응답 형태: URL 1개(또는 여러 건이면 각각). 링크를 지어내지 않고 액션 결과에 있던 값 그대로인지 확인.

## 한계 확인형 (2개) — 정직한 실패/대체 응답

### 11. 미번역 과거 원문 요청 → 집계로 대체 안내
- 질문 예문: "2019년 FDA 지적사항 원문을 전부 보여줘." (백필분 다수가 미번역 상태인 시기)
- 기대 액션: `findingsList`로 먼저 시도(`published_date=eq.2019...` 또는 연도 범위 필터) → 결과가 빈 배열이거나 매우 적음. 이어서 `findingsCategoryMatrix` 또는 `findingsStats`로 해당 연도의 전체 건수(집계)는 제시.
- 기대 응답 형태: "2019년 원문 인용 가능 사례는 X건뿐입니다(국문 번역이 끝난 것만 조회 가능). 다만 전체 지적 건수는 Y건으로 집계에서 확인됩니다" 식의 정직한 이원 답변. 없는 원문을 지어내지 않는지가 핵심 검증 포인트.

### 12. 존재하지 않는 업체 → 0건 정직 응답
- 질문 예문: "가상제약주식회사(실존하지 않는 업체명)의 지적 이력 알려줘."
- 기대 액션: `findingsFirmStats` (`p_firm`에 그대로 전달)
- 기대 응답 형태: totals 0의 유효 JSON을 받아 "해당 업체명으로 조회된 지적사항이 없습니다"라고 답함. 에러로 처리하거나 다른 업체 결과를 대신 지어내지 않는지가 핵심 검증 포인트.
