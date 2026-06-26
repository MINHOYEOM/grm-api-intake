# GRM Card Spec v16 — 고정 카드 템플릿 명세 (틀)

> 작성: 2026-06-04 · 상태: **동결본**(디자인 §13.1 동결 2026-06-05 · K2 구현 반영). 읽는 법:
> **§12(필드보정)·§13.1(디자인)이 최종**이며, §0~§9 의 구 문구와 충돌하면 §12·§13.1 을 따른다.
> 목적: 카드를 "지시문(LLM이 매번 다시 그림)"이 아니라 "코드 템플릿(같은 입력→같은 출력)"으로
> 고정하기 위한 단일 기준. Python `build_card_scaffold()`(K2)가 이 문서를 그대로 구현한다.
> 근거: v15.8 프롬프트 W1~W8 카드 표준 + 제품군(Modality) 배지/그룹핑.
> ⚠️ 이 문서가 동결되면 카드 양식의 단일 진실원이며, 변경은 이 문서 수정 + golden 테스트 갱신으로만.

---

## 0. 핵심 원칙 — 칸별 책임 (Python = 결정론 / LLM = 판단)

각 카드 칸을 **누가 채우는지** 못 박는다. Python 칸은 필드 복사·규칙 분기뿐(판단 0).
LLM 칸은 판단 산문뿐(데이터 생성·링크 금지). 이 경계가 Keystone 의 전부다.

| 카드 칸 | 채움 주체 | 소스 / 규칙 |
|---|---|---|
| ~~H3 prefix 이모지(🟧/🟦/🟫/⬜)~~ **제목에서 제거됨(§13.1-8)** — §2 표는 유형 라벨·태그용으로만 사용 | **Python** | section + 유형 규칙(§2) |
| 카드 제목 뼈대 — **최종 형식 §13.1-1**: `[유형 · 기관] 핵심대상`(DocID·소재국·배지 제외) | **Python** | Notion 필드 조합 |
| 카드 제목 `{핵심 이슈}` 구절 | **LLM** | 짧은 판단 구절(≤12자 권장) |
| 제품군 배지 💊/🧬/▫️ | **Python** | Modality 필드/폴백(§4) |
| W1 한 줄 요약 — 문장 | **LLM** | prose_input |
| W1 배지(Evidence·기관·Signal·태그) | **Python** | 필드(유형 핵심 태그는 유형 고정값) |
| W2 메타 표 (전체) | **Python** | 100% 필드 매핑(§3) |
| W3 원문 인용(라벨+`>`인용) | **Python** | raw 필드 **그대로 복사**(생성 금지) |
| W4 한국어 번역 | **LLM** | 비KO Evidence A 만 |
| W5 핵심 사실 bullet | **LLM** | prose_input(유형별 강조 라벨은 §3 고정) |
| W6 시사점 | **LLM** | prose_input |
| W7 점검 사항 | **LLM** | prose_input |
| W8 출처 푸터(듀얼링크) | **Python** | official_url/api_query 필드(§5) |
| Raw support toggle | **Python** | raw payload 보존(선택) |
| 출력 매트릭스(어느 W를 낼지) | **Python** | Evidence/언어 분기(§6) |
| 섹션 배치·제품군 그룹핑 | **Python** | section + 건수 규칙(§7) |

LLM 이 채우는 칸은 단 6종: **제목 핵심이슈 · W1문장 · W4번역 · W5 · W6 · W7.** 나머지 전부 Python.

## 1. 카드 제목 (Python 뼈대 + LLM 1구절)

> ⚠️ **이 섹션의 형식은 §13.1-1·8 로 대체됨(동결)**: `### [유형 · 기관] 핵심대상 — **핵심이슈**(bold)`.
> prefix 색사각형·소재국·DocID·제품군배지는 **제목에서 제거**(DocID 는 W2 문서번호 행, 제품군은 W1 배지).
> 아래 구 형식은 필드 매핑 참고용으로만 남긴다.

구 형식(v15.8, §13.1 로 대체):
`### {prefix} [{유형 · 규제기관 또는 소재국}{ · 제품군배지}] {업체/제품/문서명} — {핵심 이슈} \`{DocID}\``

- `{prefix}`: §2 표. `{유형}`: 유형 한글 라벨(§2). `{규제기관/소재국}`: 글로벌=규제기관, MFDS=소재국(`Site Country`).
- `{제품군배지}`: §4(규범문서는 생략/▫️).
- `{업체/제품/문서명}`·`{DocID}`: 필드 직매핑.
- `{핵심 이슈}`: **LLM** 짧은 구절(예: "B. cereus 검출·청소절차 이탈"). 국가코드 단독 금지.

## 2. 유형 → prefix · 라벨 · 핵심태그 (Python 고정표)

| Type or Class | prefix | 유형 라벨 | W1 유형 핵심 태그(고정 후보) |
|---|---|---|---|
| warning-letter | 🟧 | Warning Letter | CGMP · 지적분야 |
| recall-quality(MFDS) | 🟦 | 회수·판매중지 | 회수 · 품질사유 |
| OpenFDA Recall(글로벌) | 🟧 | Recall · Class {I/II/III} · {route} | Recall · failure mode |
| admin-action | 🟦 | 행정처분 | 행정처분 · 위반분야 |
| gmp-inspection | 🟦 | GMP실사 | GMP실사 · 결론 |
| guidance-industry/internal · gmp-guideline | 🟫 | 지침·안내서 | Guidance · 단계 |
| regulation-final/notice-final | 🟫 | 고시·개정법령 | 규정 · 시행 |
| legislative-notice | 🟫 | 입법예고 | 입법예고(→🔮 후보) |
| safety-letter | 🟦 | 안전성서한 | 안전성 |
| ich-guideline/consultation | 🟫 | ICH | ICH · Step |
| who-noc/inspection/news | 🟧/🟫 | WHO | WHO |
| hc-recall | 🟧 | Recall(HC) | Recall |
| (미매핑 Type) | ⬜ | {Type 원문} | — |

## 3. W2 메타 표 — 유형별 필드 매핑 (Python 100%)

> ⚠️ **§13.1-3 이 최종**: W2 = 발행일 · 문서번호 · 유형별 핵심 행 = **4~5행**, 라벨 이모지 없음,
> Evidence/Signal/원문언어/제품군 행 없음(W1 배지와 중복). 아래 "공통 5행"은 구 기준(이력 보존용).

~~공통 5행(모든 카드): 📅 원본 발행일=`Date` · 🔍 Evidence Level=판정(§6) · 🏷 Signal=`Signal Tier`→방향표기 ·
🏛 규제기관=`Source`/center · 🌐 원문 언어=`Language`.~~
유형별 추가 행(빈 필드는 §8 규칙):

| 유형 | 추가 행 (라벨 = 필드) |
|---|---|
| admin-action | 업체(+소재국)=`firm`/`Site Country` · 처분=`ADM_DISPS_NAME` · 근거조항=raw 법령 · 품목/공정=`ITEM_NAME`(있을 때) |
| recall-quality | 업체=`ENTRPS`/`firm` · 제품=product · Class=class(있으면) · route/dosage=Modality 폴백 · 회수사유=`RTRVL_RESN` · 범위/유통(있을 때) |
| gmp-inspection | 제조소/업체=`manufacturer` · 소재국=`Site Country` · 실사구분=before_after · 실사기간=inspection_start~end · 대상 제형/제품=product_type · 결론=judgment |
| warning-letter | 업체/제조소=`firm` · 소재국=`Site Country` · 실사일/발행일=`issue_date` · 주요 CFR/CGMP 조항=raw · 지적 분야=center/keyword |
| guidance/regulatory-change | 발행기관=`Source` · 문서 단계=draft/final/consultation · 주제/범위=title · 시행일/의견기한=`Comments Close` · 영향 대상 |
| (공통, 제품군) | 제품군=Modality(§4) — 표에 1행 추가 권장 |

권장 5~7행. 긴 조항·긴 사유는 표에 넣지 말고 W5 로 내린다.

## 4. 제품군(Modality) 배지 (Python)

판정 순서(v15.8): `Modality` 속성 → `OSD Relevance` → Raw payload(product_type/route/dosage_form) →
(MFDS) 한국어 단서. 배지: 💊 화학합성 · 🧬 생물 · ▫️ 기타. 규범 문서(특정 제품군에 매이지 않음)는 배지 생략 또는 ▫️.
⚠️ **Modality 는 분류일 뿐 포함 결정이 아니다** — Biologic 이라고 자동 카드화 금지(포함 판단은 GMP/품질 내용 기준).
배지 위치·디자인(제목 라벨 안 vs 별도): **디자인 리뷰에서 동결**(현재 v15.8 = 제목 라벨 안).

## 5. W8 듀얼링크 (Python)

📰 정보출처 = `API Query`(Intake) / WebSearch URL. 📎 공식원본 = 항목별 L1 우선:
- FR=`html_url`(L1) · EMA/MHRA/PIC/S/ECA RSS=`link`(L1) · FDA WL=`wl_url`(L1) ·
  OpenFDA Recall=FDA Recalls 인덱스(L2) · WHO=`official_url`(L1) · HC=`official_url`(L1) ·
  MFDS admin=`CCBAO01/getItem?dispsApplySeq={seq}`(L1) · MFDS gmp=`Source URL` PDF(L1) ·
  MFDS recall=`CCBAH01` 인덱스(L2) · 기타 MFDS=`Official URL`(L1) · ICH=ich.org/page(L2).
⚠️ L1 은 필드에 실제 존재할 때만. 패턴 유추 생성 금지(L2 인덱스로 폴백, ⚠️ 마커).

## 6. 출력 매트릭스 — 어느 W를 내는가 (Python 분기)

| 조건 | 출력 블록 |
|---|---|
| Evidence A + 비KO | 제목·W1·W2·W3·W4·W5·W6·W7·W8 |
| Evidence A + KO(MFDS) | 제목·W1·W2·W3·W5·W6·W7·W8 (W4 생략) |
| Evidence B/C | 제목·W1·W2·W5·W6·W7·W8 (W3/W4 생략, quote 금지) |
| guidance/regulatory-change | 위 분기 + W5 를 변경내용·시행/의견기한·영향대상 중심 |

Evidence 판정(Python): Intake raw payload 보존 + 유형별 필수필드 충족 → A. 인덱스+보조 → B. 보조 단독 → C.
예정·진행중 D 의 처리(정정 2026-06-05, Codex G3 P3·F-2): **검색/Fetch 신규 Watch item = 🔮 표(카드 아님)**,
단 **Intake watch scaffold row(입법예고 등 §12 C-확장 legislative, section=watch)는 카드로 렌더 가능** —
golden(legislative 카드)·v16 프롬프트와 정합. 🔮 표는 비카드 Watch 전용(카드로 렌더된 항목 중복 등재 금지).

## 7. 섹션 배치 · 그룹핑 (Python)

- 섹션: 글로벌(🌐) / 국내 MFDS(🇰🇷) / 🔮 Watch / Recall 모니터링 표 — `section` 필드로 사전 분류.
- ICH: `ich-guideline` 은 정적 guideline 토픽 스냅샷이므로 Tier 1 모니터링/Skipped 기본(단독 카드화 금지).
  실제 Step 4 채택·Step 2b 공개협의·총회 보도자료는 Routine WebSearch/WebFetch 이벤트로 카드/🔮 후보화한다.
  `ich-consultation` 이 Intake 에 실제 항목으로 들어오면 `section=watch`.
- 글로벌 카드 ≥4건이면 제품군별(💊/🧬/▫️) 소제목 그룹핑, ≤3건이면 평면 나열(과분할 방지).
  (명문화 2026-06-05, Codex P2-2: 제품군 소제목은 **페이지 구조 요소**로, §13.1-2 "제품군은 W1 한 곳"
  원칙(=**카드 내부** 기준)과 충돌하지 않음.)
- 정렬: Signal Tier 3→2→1, 동급 발행일 desc.

## 8. 빈 필드 · 결측 표기 (Python 규칙)

- 필드 없음 → 해당 행/구절 생략(빈 callout·빈 표행 금지) 또는 "원문 미기재".
- L1 URL 없음 → L2 인덱스 + ⚠️. 둘 다 없음 → L3 기관 홈 + ⚠️.
- raw payload 파싱 실패 → Evidence A 불가 → B 강등(`evidence_hint='B'`) + v2 row 에 **`status_hint='Error'` 기록**(기존 옵션 어휘) + health warning. **실제 Notion `Status` 전이는 K2 범위 아님** — Status 관리의 Python 마감은 K4 에서.
  (~~Needs Review~~ → **정정 2026-06-05, Codex 사전검증**: `Needs Review` 는 Intake DB Status 옵션(New/Processed/Skipped/Error)에 없음 — 미등록 Select 옵션 400 전례(Source 옵션 건). 옵션 신설 검토는 K4 이월. `Status=Error` 표기 → `status_hint` 로 용어 정정 2026-06-05, Codex B~D 검토 P2-4.)

## 9. LLM 입력 계약 (prose_input) — 카드별 최소 컨텍스트

LLM 산문 슬롯을 채울 때 **카드 1장치 최소 정보만** 전달(raw 전체 금지). 필드:
`type · modality · firm/product · 핵심사유(reason/EXPOSE_CONT/지적요약) · 조항/결론 · route/dosage(있으면) ·
규제기관 · evidence · signal`. LLM 출력 = `{핵심이슈, W1문장, W5_bullets, W6, W7, (비KO)W4}` JSON.
LLM 은 이 입력에 없는 사실을 만들지 않는다("원문 미기재"/"확인 불가" 사용).
**W4 토큰 인덱싱(2026-06-05, K2 구현 확정)**: 인용 1개 → `{{W4}}`, 2개 이상 → `{{W4_1}}`·`{{W4_2}}`… —
①② 원문과 1:1 positional 매핑의 모호성 제거. K3 프롬프트는 인덱스별로 채운다.
**(웹 이관, 2026-06-26) JSON 슬롯 매핑 — 마크업 토큰 폐지**: LLM 출력은 이제 `grm-web-card/v1` 슬롯
값으로 산출한다(렌더는 코드·웹 렌더러) — 핵심이슈→`title_issue` · W1문장→`summary` ·
W5_bullets→`key_facts[]`(평문 문자열 리스트) · W6→`implication` · W7→`checks[]`(평문 문자열 리스트) ·
(비KO)W4→`quotes[j].translation`(KO 원문=`null`·인덱스 1:1) · 브리프 핵심 3건→`brief.tldr[]`. 코드
verbatim 필드(`facts`·`quotes[].original`·`sources`·`headline_target`·배지·`render_order`/`group`)는
LLM 출력 밖이다. 마크업 토큰(`{{...}}`)·Notion 콜아웃/표 산출은 폐지. 상세 =
`docs/prompts/GRM_Prompt_v16.md` [출력 — grm-web-card/v1 JSON 슬롯] · 계약 = §4(`grm-web-card/v1` 동결).

## 10. golden 견본 + 디자인 리뷰 방법론 (K1 동결 절차)

1. 유형별 대표 1장씩(행정처분·회수·WL·가이던스·gmp-inspection, +제품군 💊/🧬 각 1) 실데이터로 선정.
2. 본 스펙대로 Notion 에 견본 카드 렌더(수동 또는 임시 스크립트).
3. **디자인 리뷰**(사용자와): 스캔 속도·정보 위계·색 의미·제품군 배지 가독성·모바일 폭 확인.
4. 합의 시 각 견본을 `tests/golden/{type}.md` 로 동결 → K2 `build_card_scaffold()` 의 기대 출력.
5. 이후 양식 변경은 이 문서 + golden 갱신으로만(우연한 변형 불가).

## 11. ~~미확정~~ → ✅ 전부 §13.1 에서 결정 완료(2026-06-05, 표는 이력 보존용)

| # | 항목 | Codex 권장 | 상태 |
|---|---|---|---|
| ① | 제품군 배지 위치 | 섹션 **또는** W2 중 하나만(제목·W2·섹션 3중 반복은 피로). 제목엔 유형·기관·핵심대상만 | 사용자 결정 |
| ② | 색 사용 강도 | W1만 파랑, W5/W6 무채색·회색, 점검(W7)만 초록 유지 — 규제문서답게 차분하게 | 사용자 결정 |
| ③ | W2 표 행수 | 기본 5행(발행일·Evidence·Signal·기관·제품군) + 유형별 1행. `원문 언어`는 메타 하단으로 | 사용자 결정 |
| ④ | 글로벌 그룹핑 임계(≥4) | 유지 가능 | 사용자 확인 |
| ⑤ | Raw support toggle 기본 노출 | 기본 숨김 | 사용자 확인 |

## 12. Codex 검토 반영 — 필드 보정 · 결정론 규칙 (2026-06-04, K1 보정)

⚠️ 아래는 §2~§9의 **정정·우선 적용 규칙**이다(Codex 코드 검증 근거). 충돌 시 §12 우선.

**(A) handoff 에 raw 가 없다 → K2 전 필수 보강.**
v1 handoff `rows[]` 는 page_id·official_url·tier·modality(조건부)만 보유하고 **raw 는 없다**(raw 는 원 row
본문 code block 에만 존재). 따라서 `build_card_scaffold()` 전에 **page_id 로 원 row children 을 fetch 해
raw JSON 을 rows 에 붙이는 단계**가 선행돼야 한다(redesign §4 v2 = additive). raw 의존 칸(W3 인용·MFDS
W2·Modality 폴백)은 이 보강 후에만 결정론적이다.

**(B) 실제 raw 키 기준 필드명 정정.**
- FDA WL: `Source=FDA Warning Letter`, `Type or Class=issuing_office`. raw 키 = `posted_date·letter_date·
  issuing_office·subject·url`. **`Site Country` 없음**, **`issue_date` 없음**(→ `letter_date` 사용),
  **CFR/CGMP 조항 없음**(letter 본문 미수집 → W2 조항행 생략 또는 "상세 본문 미수집", LLM 도 단언 금지).
- MFDS admin: raw `ADM_DISPS_NAME·ITEM_NAME·EXPOSE_CONT·ADM_DISPS_SEQ`. `official_url` 은 data.go.kr
  dataset(L2) → 📰. 📎 는 `CCBAO01/getItem?dispsApplySeq={ADM_DISPS_SEQ}`(L1, seq 로 결정론적 생성).
- MFDS recall: raw `RTRVL_RESN·ENTRPS·PRDUCT`. 스펙 `product`→**`PRDUCT`**. **`class` 없음**(행 생략).
  `official_url`=data.go.kr(L2)→📰. 📎=`CCBAH01` 인덱스(L2).
- MFDS gmp-inspection: raw `manufacturer·Site Country·before_after·inspection_start/end·product_type·
  attachment_text·attachment_deficiency_assessment`. 스펙 `judgment` **없음** → 결론은 Python 이 아니라
  **LLM 이 attachment_text 에서 도출**(W5). Python 은 `attachment_deficiency_assessment`(none/present/unknown)
  플래그만 제공.
- `Language` 기본값: MFDS·ICH·WHO·HC 만 채워짐. FR·Recall·EMA·MHRA·PIC/S·ECA·FDA WL 은 비면 **기본 `EN`**.

**(C) W3 원문 인용 — 유형별 인용 필드·길이 고정(결정론).**
| 유형 | quote 소스 필드 | 규칙 |
|---|---|---|
| admin-action | `EXPOSE_CONT` | 핵심 1~3줄(≤250자), 앞에서 자르되 문장 경계 |
| recall-quality | `RTRVL_RESN` | 사유 1줄 |
| gmp-inspection | `attachment_text` | 주요 지적/결론 핵심 1~3줄 |
| FR(guidance) | `abstract`(없으면 `title`) | 1~3줄 |
| EMA/MHRA/PIC/S/ECA RSS | (Evidence B → quote 없음) | — |
| FDA WL | (Evidence B → quote 없음) | — |
※ Evidence A 만 W3 생성. 길이 초과분은 Raw support toggle 로.

**(C-확장) 전 유형 매핑 동결 (2026-06-05, K2.5 — 이 표가 §12(C)·§6 의 최종 기준).**
핵심 불변식: **Evidence A ⟺ 인용 가능한 raw 필드 존재**("A 인데 quote 없음" 조합 금지 — 코드 정합 가드 + `test_evidence_quote_consistency` 로 강제).

| kind | source · type_or_class | W2 유형행 | quote 소스(raw 키) | Evidence |
|---|---|---|---|---|
| warning-letter | FDA WL | 업체/제조소 · 발행부서·`letter_date` | — (본문 미수집) | **B** |
| admin-action | MFDS · admin-action | 업체(+소재국) · 처분 `ADM_DISPS_NAME`/`ITEM_NAME` | `EXPOSE_CONT`(≤250) | **A** |
| recall-quality | MFDS · recall-quality | 업체 `ENTRPS` · 제품 `PRDUCT` | `RTRVL_RESN` | **A** |
| gmp-inspection | MFDS · gmp-inspection | 제조소 · 실사기간/`product_type` | `attachment_text`(≤250) | **A**(파싱 실패 시 quote 없음 → B 강등) |
| openfda-recall | OpenFDA Recall | 업체 `recalling_firm` · 제품 `product_description` · Class `classification` | `reason_for_recall` | **A** |
| hc-recall | Health Canada · hc-recall | 업체 `Organization` · 제품 `Product` · Class `Recall class` | `Issue`(→`What you should do`) | **A** |
| guidance(FR) | Federal Register | 발행기관 FDA · 의견기한/주제 | `abstract`(→`title`) | **A** |
| who-noc/-inspection/-news | WHO · who-* | 주제 `anchor_text` · 기관 WHO | — | **B** |
| ich | ICH · ich-guideline/consultation | 주제 `section_title` · 기관 ICH | — (§12H) | **B** |
| rss-news | EMA/MHRA/PIC/S/ECA | 발행기관 · 주제 `title` | — (RSS 요약) | **B** |
| mfds-notice | MFDS · guidance-industry/internal | 발행기관 MFDS · 주제 | — (RSS) | **B** |
| safety-letter | MFDS · safety-letter | 발행기관 MFDS · 주제 | — (RSS) | **B** |
| legislative | MFDS · legislative-notice | 발행기관 · 의견기한 | — | **B**(section=watch) |
| regulation | MFDS · regulation-final/notice-final | 발행기관 · 주제 | — | **B** |

듀얼링크 보강: openfda-recall = 📰 API query + 📎 FDA Recalls 인덱스 L2(⚠️, 패턴 유추 금지).
prose_input 공통 필드(P1-2 확장): `w2_facts · quote_lines · issue_or_reason · product · action · deadline · body_excerpt` + 기존(kind·modality·regulator·evidence·signal·language·firm_or_product·headline). 유형별 raw 폴백(gmp `attachment_text`·openfda `reason_for_recall`·HC `Issue`/`What you should do`·ICH/WHO `section_title`/`anchor_text`), 300자 가드.

**(D) Evidence 판정 — 결정론 한계.**
- A(Python 가능): raw 보존 + 유형별 필수필드 충족.
- B/C(입력 플래그 필요): "공식 인덱스+보조" vs "보조 단독" 은 WebSearch/검증 성공 여부가 **구조화된 입력**으로
  들어와야 결정론. 그 플래그가 없으면 LLM 판단이 끼므로, search 단계가 `evidence_hint(B/C)` 를 row 에 기록한다.

**(E) MFDS recall 다품목 1카드 통합 키.**
현재 `_dedupe_latest_rows()` 는 `source::document_id` 1건 dedupe 뿐. 6품목→1카드 통합은
**`ENTRPS + 사유(RTRVL_RESN) + 발행일` 동일군 묶음 키**를 collector(또는 scaffold)가 산출해야 함(신규).
**범위 확정(2026-06-05, Codex 사전검증)**: K2 는 `recall_group_key` **산출까지만**(`card_id`=`source::document_id` 유지,
dedupe 와 비충돌). 실제 다품목 1카드 병합 렌더는 별도 단계(K3 연계).

**(F) WHO prefix 고정.** `who-noc`·`who-inspection` = 🟧, `who-news` = 🟫.

**(G) golden 테스트 순수성.** `build_card_scaffold(row, raw, fixed_config)` 는 외부 fetch·현재시각·LLM·
Notion API 호출 없는 순수 함수. raw 출력 시 `json.dumps(sort_keys=True)`. generated_at·source_counts·
URL 인코딩·block chunk 재조립 순서를 고정.

**(H) ICH 한계.** 날짜·Step·Revision 을 수집기가 단언하지 않음 → ICH 는 Evidence B 기본, W2 Step 자동화 제한.

## 13. 디자인 방향 (✅ 동결됨 2026-06-05 — 사용자+Codex 합의)

> 2026-06-04 사용자 디자인 의견 + Claude 개선안. Codex 디자인 검토(`GRM_Keystone_Codex_design_review_prompt.md`)
> 후 동결 예정. 목표 컨셉: **전문성 + 스캔성 + 정보 위계**(화려함 아님).

| # | 방향 | 출처 | 상태 |
|---|---|---|---|
| D1 | 글로벌(영문) 항목: **영문 핵심문장 인용(W3) + 한글 번역(W4) 각각**. 문장 2개면 ①② 짝 번호로 1:1 매핑 | 사용자 | 채택 |
| D2 | **AI 면책 문구**를 페이지 끝에 반드시. 전문 버전 확정: "1차 자료 기반 AI 자동 생성 · 사실 항목 출처/원본 병기로 추적 가능 · 시사점/점검은 AI 해석으로 공식 견해·법적 자문 아님 · 의사결정 전 원문 확인" (국·영문 병기) | 사용자 | **확정**(견본 하단) |
| D9 | **클릭형 목차** 페이지 상단에 배치(`<table_of_contents/>`). 섹션(H2) 아래 카드 제목(H3)이 들여쓰기 나열, 클릭 시 해당 카드로 점프. 따라서 카드 제목은 짧고 핵심이 분명해야 함(D10 연동) | 사용자 | **채택**(견본 상단) |
| D10 | **제목 핵심 강조**: 제목 = `유형 · 기관 · 소재국 — **핵심 대상·이슈**(bold) \`ID\``. 유형·기관은 일반, 실제 무슨 일인지(업체+이슈)를 bold 로 묶어 스캔 시 즉시 인지. 제품군 배지는 제목에서 빼고 W2 로 | 사용자 | **채택**(견본 반영) |
| D11 | **메타표(W2) 경량화 확정**: 이모지 라벨 제거 · Evidence/Signal/원문언어 행 제거(W1 배지와 중복) · 제품군 행 추가. 유형별 사실 행 위주 4~5행 | 사용자+Codex | **채택**(견본 반영) |
| D3 | **이모지 절제**: 박스 헤더 1개만 허용. **메타표(W2) 라벨 이모지 제거**. 제목 색사각형(🟧/🟦) 제거 검토(섹션 헤더와 중복) | 사용자+Claude | 채택(색사각형은 검토) |
| D4 | **W2 표에서 Evidence·Signal·원문언어 행 제거** — Evidence/Signal 은 W1 배지와 중복. W2 는 유형별 사실 행 위주로 경량화 | 사용자 | 채택 |
| D5 | **제품군 명칭(한글 확정)**: 💊 `합성의약품` · 🧬 `바이오의약품` · ▫️ `기타` | 사용자 | **확정** |
| D6 | **제목 간소화**: 정보 과다 → 유형·기관·핵심대상·DocID 만. 색사각형·제품군 배지는 제목에서 제거 | 사용자+Claude | 채택 |
| D7 | **색은 행동 박스에 집중**: 점검사항(초록)은 유일한 실행 박스라 강조 유지. 핵심사실·출처는 무채색/회색 | Claude | 후보 |
| D8 | 긴 카드 축소: 긴 표·Raw 는 toggle 로, 카드 1장이 한 화면 목표 | Claude | 후보 |

## 13.1 디자인 동결안 (✅ 동결 2026-06-05 — 사용자+Codex 합의)

> 아래 12개 원칙은 **동결됨**. 변경은 이 문서 수정 + golden 테스트 갱신으로만. (견본 Notion 페이지는 동결 후 삭제됨)

1. **제목 = 인덱스**: `### [유형 · 기관] 핵심대상 — **핵심이슈**(bold)`. 소재국·DocID·제품군은 제목에서 빼고 W2/배지로. 핵심이슈만 bold 로 스캔성 확보.
2. **W1 = 3초 스캔 영역**: 사건 1~2문장 + 배지(Evidence · 기관 · Signal · **제품군**(`합성의약품`) · 유형태그) 최대 5개. 제품군은 여기 한 곳에만.
3. **W2 = 사실 표(경량)**: 발행일 · 문서번호(`MARCS`·`admin-…`·`FR …`) · 유형별 핵심 행 = 4행. 라벨 이모지 없음. Evidence/Signal/원문언어/제품군 행 없음(중복).
4. **원문·번역 인터리브**: 헤더 "원문 및 번역", `> ① 원문` 바로 다음 줄 `① 번역`, 이어서 `> ② …` `② …`. 별도 회색 번역 박스 폐지(색 박스 1개 절감). KO 항목은 번역 없이 한글 원문 quote.
5. **W5 라벨 통일**: Evidence A/B 모두 "**핵심 사실**", 같은 줄에 작게 `근거: Intake raw` 또는 `근거: 공식 인덱스 + 보조 출처`.
6. **블록 순서(현행 유지)**: 제목 → W1 → W2 → W3/W4 → W5 → **W6 시사점 → W7 점검** → W8. (점검이 카드 마지막 실행 박스로 닫힘)
7. **색(현행 유지)**: W1 파랑 · **W6 시사점 노랑(AI 해석 분리 원칙 유지)** · W7 점검 초록 · W8 출처 회색. 핵심사실(W5)·원문(W3)은 무채색.
8. **이모지 절제**: 허용 = 콜아웃(박스) 헤더 아이콘(W1/W5/W6/W7/W8)과 W8 듀얼링크 📰/📎. 금지 = 제목 색사각형·W2 라벨 이모지. (정밀화 2026-06-05, Codex P2-1 — 동결 견본의 실사용 기준으로 "카드당 2~3개" 문구 대체)
9. **W8 = 푸터**: `정보출처 … · 공식원본 [링크]`. 정보출처=공식원본이면 `정보출처/공식원본 [링크]` 한 줄.
10. **목차(D9)**: 페이지 상단 `<table_of_contents/>`. 섹션(H2) 아래 카드 제목(H3) 들여쓰기 나열, 클릭 시 점프.
11. **면책(D2 확정)**: 페이지 끝, 이모지 없이 국문 2줄 + 영문 1줄. "1차 자료 기반 AI 자동 작성 · 시사점/점검은 AI 해석으로 공식 견해·법적 자문 아님 · 의사결정 전 원문 확인". ("다이제스트" 표현 미사용 → "규제 정보 요약 자료").
12. **길이 한도**: W2 4~5행 · W5 3(최대4) · W7 2~3 · W6 2문장. 긴 조항·raw·첨부 전문은 toggle. Evidence B/C 는 원문/번역 생략. 카드 1장 = 데스크톱 1~1.5화면.

견본 반영: 위 1~12를 Notion 견본 3카드에 적용 후 사용자 검토 → **2026-06-05 동결**. 견본 페이지는 동결 후 삭제.
**다음 단계: K2(Python 카드 조립기 코드화)** — 본 §13.1 + §12(필드 보정) + §0~§9(책임 매핑)가 `build_card_scaffold()` 의 구현 기준.

## 14. recall 다품목 병합 렌더 (K3, 결정 #6 — ✅ 동결 2026-06-05)

> §12(E)의 K3 연계분. `recall_group_key` 산출(K2 완료) 위에 **다품목 1카드 병합 렌더**를 정의한다.
> 상태: **동결** — Codex G1 조건부 GO → R1 보정(R1-a/b/c) → 재확인 GO + 사람 승인·main 머지(2026-06-05).
> 구현: `merge_recall_cards()`(card_scaffold.py) + golden `recall_merged`. 변경은 본 §14 + golden 갱신으로만.

**(A) 적용 범위.** `kind == "recall-quality"`(MFDS 회수)만. openfda-recall·hc-recall 은 키 부재로 제외
(필요 시 별도 키 정의 후 확장 — K3 범위 아님). 빈 `recall_group_key` 는 병합 금지(현행 키 규칙:
`ENTRPS|RTRVL_RESN|발행일` 3요소 모두 존재 시에만 키 산출).

**(B) 병합 시점·함수.** `build_card_scaffold()` 는 그대로 row 1:1 유지(순수성·golden 불변).
신규 순수함수 `merge_recall_cards(cards: list[CardScaffold]) -> list[CardScaffold]` 가
`assemble_brief_skeleton()` 직전에 동일 키 그룹을 1카드로 접는다. 같은 입력 → 바이트 동일 출력(§12G).

**(C) 대표 선정(결정론).** 그룹 내 `card_id` 사전식 오름차순 첫 카드 = 대표. 키 정의상
업체·사유·발행일이 동일하므로 Tier 동률 — 추가 tie-break 불요.

**(D) 병합 카드 렌더 규칙.**
- 제목 핵심대상: `{ENTRPS} {대표 PRDUCT} 외 N품목`(N = 멤버수−1). 기존 60자 문장경계 절단 적용.
- W2 `제품` 행: `대표 PRDUCT 외 N품목`. 직후 **toggle `전체 품목 (N+1)`** 에 품목명 bullet 나열(§13.1-12 — 긴 목록은 toggle).
- W3: `RTRVL_RESN` 1회 인용(키 정의상 그룹 내 동일).
- W5/W6/W7: 카드 1장 분량 유지 — 품목 나열 금지, 공통 사유·조치 중심.
- W8: 대표 듀얼링크만(MFDS recall 은 📎 CCBAH01 인덱스 L2 로 그룹 내 동일).

**(E) prose_input 통합(대표 카드).** `product` = 품목 전체 나열(300자 가드, 초과 시 `외 N품목` 축약 —
**최종 문자열 기준 재적용**: 대표 품목명 자체가 길어도 축약 결과가 300자를 넘지 않게, Codex R1 P2) ·
`merged_count` = N+1 신규 필드. 나머지 공통 필드는 대표 row 기준.

**(F) handoff v2 직렬화(additive 유지).** 대표 row = 병합 scaffold·prose_input·needs_llm_slots.
멤버 row = **v1 호환 필드(page_id·source·document_id·status 등) + `merged_into: <대표 card_id>` 만** —
자체 `card_id` 포함 v2 additive 필드 전부 생략(Codex R1 P-1 확정) → Routine 은 렌더에서 제외하되
**Status 갱신 목록(page_id)에는 멤버 전원 포함**(실제 Status 전이의 Python 마감은 K4 —
K3 프롬프트는 v15.8 방식대로 멤버 전원 갱신을 명시). v1 경로(`ENABLE_HANDOFF_V2` off)는 바이트 동일 무영향.

**(G) golden·회귀.** 신규 golden `recall-merged`(3품목 1카드) + 비병합 회귀(빈 키·단독 멤버·이종 사유 그룹 분리).
기존 16종 golden 바이트 불변이 합격 조건.

## 📝 변경 이력
| 날짜 | 변경 내용 |
|---|---|
| 2026-06-04 | 최초 작성(틀). 칸별 Python/LLM 책임표·유형별 필드매핑·제품군 배지·출력매트릭스·prose_input 계약·golden 방법론. v15.8 카드 표준 기준. 디자인 리뷰로 §11 동결 예정 |
| 2026-06-04 | Codex 구현 검토 반영(§12): raw fetch 선행·실제 필드명 정정·Evidence/quote/grouping 결정론화·MFDS recall 통합 키·golden 순수성. |
| 2026-06-05 | 디자인 리뷰 완료(§13.1 동결안 12개 원칙): 제목 인덱스화·제품군 W1 배지·W2 경량 4행·원문번역 인터리브·W5 라벨 통일·시사점 노랑/순서 현행 유지·면책 간소화(다이제스트 제외)·목차. 사용자+Codex 합의, 견본 3카드 반영. 사용자 최종 확인 시 동결 |
| 2026-06-05 | K2 Codex 사전검증(조건부 GO) 반영: §8 `Needs Review`→기존 옵션 `Error` 정정(DB Status 옵션 부재·미등록 Select 400 전례, 옵션 신설은 K4 이월) · §12(E) K2 범위 = `recall_group_key` 산출까지(병합 렌더는 K3 연계) |
| 2026-06-05 | K2 Stage C 구현 확정 반영(§9): W4 토큰 인덱싱(`{{W4_n}}`, 다중 인용 1:1 매핑 모호성 제거). 구현 = `card_scaffold.py`(build_card_scaffold + assemble_brief_skeleton 분리) + golden 5종, 금지 문법 부재·문서번호 행·결정론을 테스트로 강제 |
| 2026-06-05 | Codex B~D 일괄검토(HOLD) 반영: §1 에 "§13.1-1·8 이 최종(제목에서 prefix·소재국·DocID 제거)" 대체 명시(P1-1 혼동 뿌리 제거) · §8 `Status=Error`→`status_hint='Error'` 용어 정정(실제 Status 전이는 K4, P2-4) |
| 2026-06-05 | **K1+K2 종합점검(조건부 GO) 반영 — 동결본 정리(P1-3)**: 문서 상태 초안→동결본(§12·§13.1 우선 명시), §0 prefix/제목 행 §13.1 기준 정정, §3 공통 5행 구기준 표시, §11 결정완료 처리. P2-1 이모지 문구 정밀화(콜아웃 헤더+📰📎 허용·제목/W2 라벨 금지), P2-2 그룹핑 소제목=페이지 구조(카드 내부 원칙과 비충돌) 명문화. **K2.5 보강 트랙 신설**: 활성 전 유형 W2/quote/evidence 분기 + prose_input whitelist 확장 + golden 전 유형 확장(P1-1·P1-2) |
| 2026-06-05 | **K2.5 매핑 동결(§12 C-확장)**: 전 16 유형 × (W2 유형행·quote 소스·Evidence) 표 + A⟺quote 불변식 + prose_input 공통/유형별 필드 기록. golden 16종·134 테스트가 이 표의 기대 출력. Codex 재확인 대기 |
| 2026-06-05 | **K3 착수 — §14 recall 다품목 병합 렌더 초안 신설**(결정 #6 "규칙+구현" 채택): MFDS recall 한정·`merge_recall_cards()` 순수함수(스캐폴드 1:1 불변)·대표 card_id 오름차순·W2 toggle 품목목록·멤버 row `merged_into` 마킹(Status 갱신 목록 유지). Codex 게이트 통과 시 동결 |
| 2026-06-05 | **§6 D-처리 정정(Codex G3 P3·F-2)**: 검색/Fetch 신규 Watch = 🔮 표 전용, Intake watch scaffold(legislative)는 카드 렌더 — golden·v16 프롬프트 정합, 표/카드 중복 등재 금지 |
| 2026-06-05 | **§14 동결** — Codex R1 반영: (E) 병합 `product` 300자 가드 최종 문자열 기준, (F) 멤버 row = v1 호환 필드 + `merged_into` 만(자체 `card_id` 포함 v2 additive 전부 생략). 구현 G1+R1 4커밋 Codex GO·사람 승인·main 머지. fork A안(`render_order`+`group_label`, `_ordered_cards_with_groups()` 단일 진실원) 동반 확정 |
