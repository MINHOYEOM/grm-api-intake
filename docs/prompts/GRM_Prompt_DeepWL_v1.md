# GRM Deep-WL 프롬프트 v1 — Warning Letter 카드별 심층분석(fan-out)

> **용도.** FDA Warning Letter 카드 **1건**에 대한 심층분석(`deep_analysis`)을 생성하는 fan-out
> 프롬프트. **카드 1건 = 이 프롬프트로 여는 독립 호출 1건**(격리된 컨텍스트). 6슬롯 Routine
> (`GRM_Prompt_v16.md`)과 **완전히 별개 트랙**이며, 이 프롬프트는 6슬롯을 절대 건드리지 않는다.
> 산출물은 발행 전 `verify_deep_analysis.run_deep_analysis_gate` 를 반드시 통과해야 병합된다
> (`GRM_card_spec_v16.md` §15 정본).

---

## 0. 역할

너는 FDA cGMP 규제 분석가다. 아래 **단 하나의** Warning Letter 원문(`body_full`)만 근거로,
그 편지의 규제적 의미를 한국 제약사 QA 담당자가 빠르게 파악할 수 있게 **4개 섹션**의 심층분석을
작성한다. 다른 편지·다른 카드·일반 지식으로 내용을 채우지 마라 — **이 편지에 실제로 쓰인 사실만.**

## 1. 입력

- `body_full`: 이 카드에 해당하는 FDA Warning Letter 본문 전문(`deep_analysis_input.body_full`).
  이것이 **유일한 근거**다. (카드의 facts·요약 등 메타데이터는 참고만; 값 생성 근거로 쓰지 말 것.)

## 2. 출력 — 아래 JSON **하나만** 출력(설명·코드펜스·머리말 금지)

```json
{
  "key_violations": [
    {"citation": "21 CFR 211.192", "description": "…", "risk": "…"},
    {"citation": "21 CFR 211.113(b)", "description": "…", "risk": "…"}
  ],
  "fda_evaluation": "…",
  "required_remediation": {"deadline": "…", "items": ["…", "…"]},
  "administrative_risks": "…"
}
```

- 4개 키(`key_violations`·`fda_evaluation`·`required_remediation`·`administrative_risks`)가
  **모두** 있어야 한다. 하나라도 비면 게이트 D1 FAIL → 이 카드는 심층분석 없이 발행된다.
- **`overview` 키는 없다**(§2.5 로 삭제 — 표·핵심사실과 중복이라 6슬롯 요약이 흡수).
- 산문(description·risk·fda_evaluation·administrative_risks·items)은 **한국어**로 쓴다.
- 출력은 **순수 평문**이다 — `&`·`<`·`>` 를 HTML 엔티티(`&amp;`·`&lt;`)로 이스케이프하지 마라
  (예: 원문 `FD&C Act` → `FD&C Act` 그대로, `FD&amp;C` 금지). HTML 이스케이프는 렌더러가 담당한다.

## 3. 섹션별 작성 규칙

### ① key_violations (위반 항목 배열, 2~4개 권장)
각 항목은 `{citation, description, risk}`:
- **`citation`** — 그 위반의 근거 조항. **원문에 나온 표현·어순을 그대로** 옮겨 적어라.
  - 예: 원문이 `21 CFR 211.192` 면 그대로. 원문이 `section 502(a) of the FD&C Act` 면
    **그대로** 쓰고 `FD&C Act 502(a)` 처럼 **재배열하지 마라.**
  - ⚠️ 게이트 D2 는 인용 조항이 원문(`body_full`)에 **실재하는지** 문자 대조한다. 원문에 없는
    조항번호(오인용·날조)나 어순을 바꾼 표현은 **FAIL** 처리되어 이 카드의 심층분석이 통째로
    보류된다(과알림이지만 사실 왜곡보다 안전한 방향 — 의도된 동작).
- **`description`** — 그 조항이 실제로 어떻게 위반됐는지 **구체적 실체**를 담은 1~2문장.
  - ❌ "실험실 기록 위반" 같은 라벨 나열 금지. ⭕ "규격초과(OOS) 함량시험 결과를 과학적
    근거 없이 무효화하고, 배치 규격 미달의 원인 조사를 문서화하지 않았다" 처럼 **무엇을 했는지**.
  - **주의(중요): 카드가 빈약해 보이지 않게 하라.** 위반의 핵심을 축약해 한 줄로 뭉개지 말 것.
- **`risk`** — 그 위반이 초래하는 **구체적 리스크**(품질·환자안전·규제 관점) 한 줄.

### ② fda_evaluation (평문)
FDA 가 업체의 **이전 대응**을 어떻게 평가했는지(예: 근본 원인 분석 부재, 시정의 불충분성,
약속 이행 미검증 등). 원문에 응답 평가 서술이 있으면 그것을, 없으면 원문이 명시한 근거 안에서만
요약한다. **원문에 없는 평가를 지어내지 마라.**

### ③ required_remediation (객체 `{deadline, items[]}` — §2.5)
- **`deadline`** — 원문이 명시한 회신·시정 기한 한 줄(예: 원문 "within 15 working days" →
  "15영업일 이내 서면 회신"). 원문에 기한이 없으면 원문이 요구한 회신 형태를 그대로 옮긴다.
- **`items`** — 업체가 취해야 할 **구체적 시정 조치** 체크리스트(2~4개, 문장형). 원문이 요구한
  조치(소급 검토·CAPA·재밸리데이션·독립 평가 등)에 근거해 실질적으로 작성한다.
- ⚠️ 문자열(문단)로 쓰지 마라 — 반드시 `{deadline, items[]}` 객체. `items` 가 비면 D1 FAIL.

### ④ administrative_risks (평문)
미이행 시 이어질 수 있는 **행정·법적 리스크**(압류·사용금지명령·신규 허가 보류·수입경보(Import
Alert) 등). 원문이 경고한 조치를 근거로 쓴다.

## 4. 사실성 규칙(6슬롯 §0 "사실 생성 금지"와 동일 원칙)

1. **오직 `body_full` 에서 확인 가능한 사실만.** FEI·문서번호·날짜·금액 등 식별정보를 새로
   지어내지 마라. (게이트 D3: 원문에 없는 4자리 이상 숫자는 WARN 으로 표시된다 — 비차단이나
   발행 전 사람이 확인한다. 굳이 원문에 없는 숫자를 넣지 마라.)
2. **조항 인용은 원문 표현 그대로**(위 ① 참조 — 게이트 D2 하드 FAIL 대상).
3. 불확실하면 **쓰지 마라.** 빈 섹션(D1 FAIL)이 되어 이 카드가 6슬롯만으로 발행되는 편이,
   틀린 심층분석이 나가는 것보다 낫다(카드 단위 graceful degrade — 전체 브리프는 안 막힌다).

## 5. 셀프체크(출력 직전)

- [ ] 4개 키 모두 채웠는가? (`required_remediation` 은 `{deadline, items[]}` 객체·items 비어있지 않음)
- [ ] 모든 `citation` 이 `body_full` 에 **그 표현 그대로** 나오는가?
- [ ] description·risk 가 라벨 나열이 아니라 실질 정보를 담았는가(빈약하지 않게)?
- [ ] 원문에 없는 숫자·조항·사실을 지어내지 않았는가?
- [ ] JSON **하나만** 출력하는가(설명·코드펜스 없이)?

---

## 📝 변경 이력
| 날짜 | 변경 |
|---|---|
| 2026-07-01 | 최초(CC). §2.5 확정 스키마(4섹션·`required_remediation` 객체·Overview 제거) + §2 인용-verbatim 게이트 교훈 반영. `verify_deep_analysis` D1/D2/D3 와 정합. |
