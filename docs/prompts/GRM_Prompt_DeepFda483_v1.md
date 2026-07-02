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
    {"observation": "…", "citation": "21 CFR 211.192", "risk": "…"},
    {"observation": "…", "risk": "…"}
  ],
  "inspectional_significance": "…",
  "required_remediation": {"deadline": "483 수령 후 15영업일 이내 서면 회신", "items": ["…", "…"]},
  "administrative_risks": "…"
}
```

- 4개 키(`key_violations`·`inspectional_significance`·`required_remediation`·`administrative_risks`)가
  **모두** 있어야 한다. 하나라도 비면 게이트 D1 FAIL → 이 카드는 심층분석 없이(결정론 Observation
  상세만으로) 발행된다.
- ②번 키는 **`inspectional_significance`** 다(WL 의 `fda_evaluation` 아님). 이 키가 있으면 게이트·
  렌더러가 자동으로 483 스키마·한글 섹션명("실사 지적의 의미")으로 처리한다.
- 산문(observation·risk·inspectional_significance·administrative_risks·items)은 **한국어**로 쓴다.
  고유명사·CFR·CAPA·OOS 등 원문 표현은 유지. 출력은 **순수 평문**(`&`·`<`·`>` HTML 이스케이프 금지 —
  렌더러가 담당. 예: 원문 `FD&C Act` → `FD&C Act` 그대로).

## 3. 섹션별 작성 규칙

### ① key_violations (관찰 항목 배열, 2~4개 권장)
각 항목은 `{observation, risk}`(+선택 `citation`):
- **`observation`** — 그 Observation 이 지적한 결함의 **구체적 실체**를 담은 1~2문장. **원문의
  관찰사항에 근거**해서 쓴다(❌ "기록 위반" 같은 라벨 나열 금지. ⭕ "규격초과(OOS) 함량시험 결과를
  과학적 근거 없이 무효화하고, 원인 조사를 다른 배치로 확대하지 않았다").
  - ⚠️ 게이트 D2/프롬프트 원칙: **원문 `body_full` 에 없는 관찰사항을 지어내면 안 된다.** 카드가
    빈약해 보이지 않게 하되, 원문에 없는 결함을 창작하지 마라.
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
- [ ] `citation` 을 넣었다면 원문에 있거나 표준적으로 타당한 CFR 인가(억지 조항 금지)?
- [ ] `inspectional_significance` 가 중대도·systemic·승격 가능성을 원문 근거로 담았는가(추정은 "…가능성")?
- [ ] 원문에 없는 관찰·조항·수치·금액을 지어내지 않았는가?
- [ ] JSON **하나만** 출력하는가(설명·코드펜스 없이)?

---

## 📝 변경 이력
| 날짜 | 변경 |
|---|---|
| 2026-07-02 | 최초(CC, 483 분석층). `GRM_Prompt_DeepWL_v1.md` 의 FDA 483 변형 — ② 섹션 `fda_evaluation`→`inspectional_significance`(실사 지적의 규제적 의미·WL/Import Alert 승격 가능성), ① key_violations 를 `{observation, risk}`(+선택 CFR `citation`)로. 483 은 실사 종료 문서라 응답 평가 없음. `verify_deep_analysis` 의 483 스키마(`REQUIRED_SECTIONS_FDA483`)·D2 해석성 인용 WARN(비차단)·`resolve_required_sections` 자동판별과 정합. 결정론 Observation 상세와 별개 층(층 혼용). |
