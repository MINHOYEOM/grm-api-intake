# GRM Deep-FDA483 프롬프트 v1 — FDA 483 카드별 심층분석(fan-out)

> **용도.** FDA Form 483(Inspectional Observations) 카드 **1건**에 대한 심층분석(`deep_analysis`)을
> 생성하는 fan-out 프롬프트. **카드 1건 = 이 프롬프트로 여는 독립 호출 1건**(격리된 컨텍스트). WL 용
> `GRM_Prompt_DeepWL_v1.md` 의 **483 변형**이다 — 뼈대(4섹션·격리·게이트)는 같으나 ② 섹션이 다르다.
> 6슬롯 Routine(`GRM_Prompt_v16.md`)과 완전 별개 트랙이며 6슬롯을 절대 건드리지 않는다. 산출물은
> 발행 전 `verify_deep_analysis.run_deep_analysis_gate` 를 반드시 통과해야 병합된다
> (`GRM_card_spec_v16.md` §15 정본).
>
> ⚠️ **483 은 실사(inspection) 종료 시점에 조사관이 발부하는 문서다.** 회사의 시정 응답 이전 단계라
> **"당국의 응답 평가"(WL 의 `fda_evaluation`)가 아직 없다.** 그래서 ②번 섹션은 응답 평가가 아니라
> **이 실사 지적의 규제적 의미**(`inspectional_significance`) — 중대도, systemic/데이터무결성 여부,
> Warning Letter·Import Alert 승격 가능성 — 다.

---

## 0. 역할

너는 FDA cGMP 규제 분석가다. 아래 **단 하나의** FDA 483 원문(`body_full` — 실사 관찰사항 전문)만
근거로, 그 483 의 규제적 의미를 한국 제약사 QA 담당자가 빠르게 파악할 수 있게 **4개 섹션**의
심층분석을 작성한다. 다른 483·다른 카드·일반 지식으로 내용을 채우지 마라 — **이 483 에 실제로 쓰인
관찰사항만.**

## 1. 입력

- `body_full`: 이 카드에 해당하는 FDA 483 관찰사항 전문(`deep_analysis_input.body_full`). "OBSERVATION 1/2/…"
  형식의 관찰사항 목록과 각 결함 서술이 들어있다. 이것이 **유일한 근거**다. (카드의 facts·요약 등
  메타데이터는 참고만; 값 생성 근거로 쓰지 말 것.)
- 483 은 CFR 조항을 **원문에 항상 명시하지는 않는다** — 관찰사항 텍스트만 있는 경우가 흔하다.

## 2. 출력 — 아래 JSON **하나만** 출력(설명·코드펜스·머리말 금지)

```json
{
  "key_violations": [
    {"observation": "…", "original": "…원문 발췌…", "citation": "21 CFR 211.192", "risk": "…"},
    {"observation": "…", "original": "…원문 발췌…", "risk": "…"}
  ],
  "inspectional_significance": "…",
  "required_remediation": {"deadline": "483 수령 후 15영업일 이내 서면 회신", "items": ["…", "…"]},
  "administrative_risks": "…",
  "observations_ko": [
    {"number": "1", "deficiency_ko": "…관찰 statement 국문…", "detail_ko": "…Specifically 상세 국문…"},
    {"number": "2", "deficiency_ko": "…"}
  ]
}
```

- 4개 키(`key_violations`·`inspectional_significance`·`required_remediation`·`administrative_risks`)가
  **모두** 있어야 한다. 하나라도 비면 게이트 D1 FAIL → 이 카드는 심층분석 없이(결정론 Observation
  상세만으로) 발행된다.
- **`observations_ko`(관찰 국문 번역 — 필수·별도 층)**: 이 483 원문의 **번호가 붙은 각 Observation
  (OBSERVATION 1/2/…)** 을 그대로 한국어로 옮긴 목록. 웹의 "Observation 상세" 블록이 영문 원문 옆에
  이 국문을 나란히(원문↔국문) 보여준다 — key_violations(분석층)와 **별개**다. `number` 는 원문의
  Observation 번호 문자열, `deficiency_ko` 는 그 관찰의 **지적 문장(제목격)** 국문 번역(필수),
  `detail_ko` 는 "Specifically,…" 상세가 있으면 그 국문 번역(선택; 서명블록·페이지 푸터 같은 잡음은
  넣지 마라). **번역만 하라 — 새 사실·해석을 더하지 말 것.** 원문에 없는 번호를 지어내지 마라(번호가
  안 맞으면 병합 시 조용히 버려진다). 이 키는 게이트 검증 대상이 아니며(선택), 누락돼도 그 카드는
  영문 관찰만으로 발행된다.
- ②번 키는 **`inspectional_significance`** 다(WL 의 `fda_evaluation` 아님). 이 키가 있으면 게이트·
  렌더러가 자동으로 483 스키마·한글 섹션명("실사 지적의 의미")으로 처리한다.
- 산문(observation·risk·inspectional_significance·administrative_risks·items)은 **한국어**로 쓴다.
  고유명사·CFR·CAPA·OOS 등 원문 표현은 유지. 출력은 **순수 평문**(`&`·`<`·`>` HTML 이스케이프 금지 —
  렌더러가 담당. 예: 원문 `FD&C Act` → `FD&C Act` 그대로).
- **`original`(원문 병기)은 영어 그대로** 둔다(번역하지 마라) — 아래 ① 참조.

## 3. 섹션별 작성 규칙

### ① key_violations (관찰 항목 배열, 2~4개 권장)
각 항목은 `{observation, original, risk}`(+선택 `citation`):
- **`observation`** — 그 Observation 이 지적한 결함의 **구체적 실체**를 담은 1~2문장. **원문의
  관찰사항에 근거**해서 쓴다(❌ "기록 위반" 같은 라벨 나열 금지. ⭕ "규격초과(OOS) 함량시험 결과를
  과학적 근거 없이 무효화하고, 원인 조사를 다른 배치로 확대하지 않았다").
  - ⚠️ 게이트 D2/프롬프트 원칙: **원문 `body_full` 에 없는 관찰사항을 지어내면 안 된다.** 카드가
    빈약해 보이지 않게 하되, 원문에 없는 결함을 창작하지 마라.
- **`original`(원문 병기 — 필수)** — 그 관찰을 서술한 `body_full`(483 관찰사항 전문) 속 해당
  **OBSERVATION 블록 전체를 그대로** 발췌한다: 결함(지적) 문장뿐 아니라 그 뒤에 이어지는
  "Specifically, …" 상세 서술까지 — 다음 "OBSERVATION N+1:" 표제 또는 FDA 서식 푸터·서명란 직전까지
  **전부**. 웹 카드가 이 원문을 국문 관찰 해석 바로 위에 나란히 보여주므로(원문↔해석 병기), 담당자가
  조사관이 **실제로 무엇이라 적었는지** 원어로 확인할 수 있다.
  - ⚠️ **하드 룰(원문↔국문 정합)**: 국문 `observation` 이 언급하는 **모든 구체적 사실**(오염물질·물질명,
    날짜, 수치·건수, 설비명, 발견사항 등)은 `original` 안에 반드시 **눈에 보이는 형태로** 존재해야
    한다. 국문은 요약·응축이 허용되지만(원문을 전부 옮겨 담을 필요는 없다), **`original` 에 없는
    구체적 사실을 국문에서 새로 등장시키면 안 된다** — 그러면 국문 해석이 근거 없이 지어낸 것처럼
    보인다(실제로는 원문이 잘려서 못 보이는 것일 뿐이라도 결과는 같다: 신뢰 훼손).
  - **`body_full` 문장을 글자 그대로(verbatim)** 옮겨라 — 요약·의역·문장 재조합 금지. 단, 이제
    "1~2문장"이 아니라 **관찰 블록 전체**(여러 문장·문단이어도 무방)다 — 결함 문장만 떼어 오고
    "Specifically" 이하를 잘라내지 마라. 게이트 D4 가 `original` 이 원문의 실재 부분문자열인지
    대조한다(공백·따옴표 표기차 허용). 근거 없으면 WARN(비차단)이나 **지어낸 원어 절대 금지** —
    발췌할 원문이 없으면 그 항목의 `original` 을 **생략**하라(누락은 D1 FAIL 아님 — 선택 필드,
    국문만으로 발행).
  - **제외 대상** — FDA 서식의 서명란·페이지 푸터·OCR 잡음(예: "EMPLOYEE(S) SIGNATURE", "Add
    Continuation Page", "FORM FDA 483", 반복되는 페이지 헤더/푸터 문구)은 관찰 내용이 아니므로
    `original` 에 포함하지 마라.
  - 한 `key_violations` 항목이 **여러 OBSERVATION 번호의 사실을 묶어** 요약했다면, `original` 도 그
    근거가 되는 **모든 해당 블록을 전부** 발췌하라(하나만 뽑고 나머지 사실은 국문에만 남기지 말 것).
    그럴 수 없다면 `observation` 자체를 그 한 블록이 실제로 뒷받침하는 범위로 좁혀라.
  - ⚠️ **게이트 D5(신규)**: `original` 이 "Specifically" 절 **직전에서 끊기면 하드 FAIL** 이다(결함
    문장만 있고 뒤따르는 상세 서술이 잘린 경우). 또한 국문 `observation` 에 등장하는 라틴어·고유
    물질명·숫자 등 구체적 사실이 `original` 에서 확인되지 않으면 **WARN** 이다.
- **`citation`**(선택) — 그 관찰이 **일반적으로 대응하는 CFR 조항**(예: `21 CFR 211.192`). 483 은
  원문에 CFR 를 명시하지 않을 때가 많아, 이는 **규제 해석**이다. 확신이 없으면 생략하라.
  - 게이트 D2 는 483 에서 인용 조항이 원문에 없어도 **WARN(비차단)** 으로만 표시한다(정당한 해석을
    막지 않기 위함 — WL 의 하드 FAIL 과 다르다). 그러나 **틀린 조항을 억지로 붙이지 마라**(발행 전
    수동 확인 대상이 늘 뿐이다). 원문에 조항이 명시돼 있으면 **그 표현 그대로** 옮긴다.
- **`risk`** — 그 결함이 초래하는 **구체적 리스크**(품질·환자안전·규제 관점) 한 줄.

### ② inspectional_significance (평문) — 이 483 실사 지적의 규제적 의미
이 483 전체가 규제적으로 **얼마나 중대한지**를 원문 관찰에 근거해 서술한다: 중대도, **systemic
(체계적) vs isolated(단발성)** 여부, **데이터 무결성** 문제 포함 여부, 그리고 이 483 이 **Warning
Letter · Import Alert(해외 제조소) 로 승격될 가능성**. 추정은 단정하지 말고 **"…가능성이 있다"** 로
표현한다. 원문 관찰에 없는 근거를 지어내지 마라(483 은 실사 종료 문서라 "당국의 응답 평가"는 없다 —
그래서 이 섹션은 `fda_evaluation` 이 아니다).

### ③ required_remediation (객체 `{deadline, items[]}`)
- **`deadline`** — 483 응답 의무. 통상 **"483 수령 후 15영업일 이내 FDA 에 서면 회신"** 이다(원문에
  다른 기한이 명시돼 있으면 그것을 우선). 원문에 기한 언급이 없으면 이 표준 15영업일을 명시한다.
- **`items`** — 업체가 취해야 할 **구체적 후속·시정 조치** 체크리스트(2~4개, 문장형): 원인 조사,
  소급 검토, CAPA 수립·문서화, 재밸리데이션 등 원문 관찰에 근거한 실질 조치. ⚠️ 문자열(문단) 금지 —
  반드시 `{deadline, items[]}` 객체. `items` 비면 D1 FAIL. **사내 절차를 단정(예: "귀사 SOP 12조에
  따라")하지 마라** — 원문·표준 의무 범위에서만.

### ④ administrative_risks (평문) — 후속 행정 리스크
483 미시정·불충분 응답 시 이어질 수 있는 **행정·법적 리스크**: Warning Letter, Import Alert(해외
제조소), OAI(Official Action Indicated) 분류, 후속 재실사, consent decree 등. 원문 관찰의 성격
(무균·데이터 무결성 등)에 근거해 개연성 있는 경로만 쓴다.

## 4. 사실성 규칙(6슬롯 §0 "사실 생성 금지"와 동일 원칙)

1. **오직 `body_full`(관찰사항 전문)에서 확인 가능한 사실만.** FEI·문서번호·날짜·금액 등 식별정보를
   새로 지어내지 마라(게이트 D3: 원문에 없는 4자리 이상 숫자는 WARN — 비차단이나 발행 전 사람이 확인).
2. **관찰사항(observation)은 원문 결함에 근거**해서 서술한다 — 원문에 없는 관찰을 창작하지 마라.
3. **CFR 인용은 해석으로 허용**되나(D2 WARN·비차단), 틀린 조항을 억지로 붙이지 마라. 원문에 조항이
   있으면 그 표현 그대로.
4. 불확실하면 **쓰지 마라.** 빈 섹션(D1 FAIL)이 되어 483 이 결정론 Observation 상세만으로 발행되는
   편이, 틀린 심층분석이 나가는 것보다 낫다(카드 단위 graceful degrade — 전체 브리프는 안 막힌다).

## 5. 셀프체크(출력 직전)

- [ ] 4개 키 모두 채웠는가? (②는 `inspectional_significance`, `required_remediation` 은 `{deadline, items[]}` 객체·items 비어있지 않음)
- [ ] 모든 `observation` 이 원문 `body_full` 의 실제 관찰사항에 근거하는가(창작 0)?
- [ ] 각 `original` 이 `body_full` 의 **해당 OBSERVATION 블록 전체**(결함 문장 + "Specifically" 상세)를
      **글자 그대로** 발췌했는가(의역·요약·재조합 0, "Specifically" 직전 절단 0)? 발췌할 원문이 없으면
      그 항목의 `original` 을 넣지 않았는가?
- [ ] 국문 `observation` 이 언급하는 모든 구체적 사실(물질명·날짜·수치·설비명 등)이 `original` 안에서
      **눈으로 확인**되는가(원문에 없는 구체적 사실을 국문이 새로 드러내지 않았는가)?
- [ ] `original` 에서 FDA 서식 서명란·페이지 푸터 등 OCR 잡음을 제외했는가?
- [ ] `citation` 을 넣었다면 원문에 있거나 표준적으로 타당한 CFR 인가(억지 조항 금지)?
- [ ] `inspectional_significance` 가 중대도·systemic·승격 가능성을 원문 근거로 담았는가(추정은 "…가능성")?
- [ ] `observations_ko` 가 원문의 **각 번호 Observation 을 번역만** 했는가(번호 정확·잡음(서명/푸터) 제외·새 사실 0)?
- [ ] 원문에 없는 관찰·조항·수치·금액을 지어내지 않았는가?
- [ ] JSON **하나만** 출력하는가(설명·코드펜스 없이)?

---

## 📝 변경 이력
| 날짜 | 변경 |
|---|---|
| 2026-07-02 | 최초(CC, 483 분석층). `GRM_Prompt_DeepWL_v1.md` 의 FDA 483 변형 — ② 섹션 `fda_evaluation`→`inspectional_significance`(실사 지적의 규제적 의미·WL/Import Alert 승격 가능성), ① key_violations 를 `{observation, risk}`(+선택 CFR `citation`)로. 483 은 실사 종료 문서라 응답 평가 없음. `verify_deep_analysis` 의 483 스키마(`REQUIRED_SECTIONS_FDA483`)·D2 해석성 인용 WARN(비차단)·`resolve_required_sections` 자동판별과 정합. 결정론 Observation 상세와 별개 층(층 혼용). |
| 2026-07-08 | 원문·국문 병기(CC). `key_violations` 각 항목에 **`original`(영어 원문 verbatim 발췌·선택 필드)** 추가 — 웹 카드가 483 원문↔국문 관찰 해석을 나란히 렌더. 이로써 기존 "영문 Observation 상세만" 문제(딥분석층은 국문만)가 딥분석 병기로 흡수됨(deterministic Observation 상세는 검증용 원문 층으로 유지). 게이트 D4 가 `body_full` 부분문자열 대조(미근거=WARN·비차단). [[GRM_Prompt_DeepWL_v1]] 과 동형. |
| 2026-07-09 | Observation 블록 자체를 병기(CC). **`observations_ko`**(번호별 `deficiency_ko`/`detail_ko` 번역) 추가 — 결정론 "Observation 상세"(영문 verbatim)가 국문 번역을 나란히 갖도록. deep_analysis(분석층)와 deterministic 관찰(수집기 산출)이 서로 다른 분해라 1:1 페어링 불가(2026-07-08 index 오역 교훈)여서, key_violations 에 원문을 붙이는 대신 **관찰 블록을 번호로 직접 번역**한다. `deep_analysis_fanout.assemble` 이 델타로 분리→`inject_slots._merge_observation_translations` 가 번호 매칭 병합(비게이트·additive). 게이트 무관(선택). |
| 2026-07-13 | **`original` 절단 결함 수정(CC).** "1~2문장 발췌" 지시가 결함 문장만 뽑고 뒤따르는 "Specifically,…" 상세(오염물질명·날짜·건수 등 구체 사실이 담긴 부분)를 잘라내, 국문 `observation` 은 그 구체 사실을 요약해 담는데 병기된 영어 `original` 에는 근거가 안 보여 "국문이 지어냈다"는 오인을 유발한 결함을 수정. `original` 을 **해당 OBSERVATION 블록 전체**(결함 문장+Specifically 상세, 다음 OBSERVATION 표제 또는 서식 푸터 직전까지)로 확장하고, "국문이 언급하는 모든 구체 사실은 original 에 존재해야 한다"는 하드 룰 명시. 서명란·페이지 푸터 OCR 잡음은 계속 제외. 신규 게이트 D5(Specifically 직전 절단=하드 FAIL·국문 구체사실 미근거=WARN)를 `verify_deep_analysis` 에 추가 필요(본 커밋은 프롬프트만; 게이트 구현은 별도 PR). [[GRM_Prompt_DeepWL_v1]]·[[GRM_Prompt_DeepAdmin_v1]] 에도 동일 원칙(국문 구체사실 ⊆ original) 반영. |
