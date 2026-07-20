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

**(E) recall 다품목 1카드 통합 키.**
현재 `_dedupe_latest_rows()` 는 `source::document_id` 1건 dedupe 뿐. 하나의 회수 사건이 SKU·유통사·함량별
개별 레코드로 쪼개진 것을 1카드로 통합하려면 `recall_group_key`(scaffold 산출)로 동일군을 묶는다.
**키 규칙은 §14(A)** 참조(2026-07-06 확장: MFDS `MFDS|ENTRPS|RTRVL_RESN` + OpenFDA `event_id`/`firm|reason`,
**발행일 제외**·소스 네임스페이스). `card_id`=`source::document_id` 유지(dedupe 와 비충돌).

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

**(A) 적용 범위.** `kind in {"recall-quality"(MFDS 회수), "openfda-recall"(OpenFDA 회수)}`.
빈 `recall_group_key` 는 병합 금지. **키 규칙(2026-07-06 확장)**: 소스로 네임스페이스하고 **발행일은 키에서 제외**한다
(하나의 실제 회수 사건이 SKU·lot·유통사·함량별 개별 레코드로 다른 날 재등록·재수집돼도 한 군으로 묶기 위함) —
- MFDS: `MFDS|{ENTRPS}|{RTRVL_RESN}` (두 요소 모두 존재 시)
- OpenFDA: 정본 `event_id` 우선 `RECALL|event|{event_id}`, 부재 시 `RECALL|{recalling_firm}|{reason_for_recall}`

hc-recall 은 여전히 제외(키 미정의). 종전 키 `ENTRPS|RTRVL_RESN|발행일` 은 (a)openfda-recall 을 아예 배제했고
(b)날짜만 다르면 같은 사건도 병합이 갈라지는 결함(대일제약 06-26/06-29·OpenFDA distributor/SKU fan-out)이 있어 대체됨.
소스 접두사로 서로 다른 소스가 우연히 같은 firm|reason 를 가져도 교차 병합되지 않는다.

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
기존 16종 golden 바이트 불변이 합격 조건. **2026-07-06 확장 회귀**: OpenFDA fan-out(event_id/firm|reason)
병합·MFDS 발행일 무관 병합·소스 교차 비병합(`OpenFdaRecallMergeTest`·`MfdsRecallCrossDateMergeTest`).
`recall_group_key` 문자열은 `recall_merged`/`recall_quality_chemical` 두 골든에만 직렬화되어 키 형식 갱신분만 반영,
markdown·web-card 골든은 병합 결과 동일로 불변.

**(H) 동일 뉴스 기사 중복 제거(rss-news).** 회수와 별개로, RSS 피드가 같은 기사를 여러 날 재노출하면
`_stable_doc_id` 가 date_iso 를 포함해 doc_id 가 갈려 `source::document_id` 수집 dedup 을 우회한다
(예: ECA "Should TGA publish GMP Certificates?" 07-01/06-29 2건). 순수함수 `dedupe_news_cards()` 가
`merge_recall_cards()` 직후(`grm_handoff` 병합 지점) 같은 (source·정규화 제목) rss-news 카드를 대표 1장만 남기고
나머지를 `merged_into` 마킹한다(회수 병합과 동일 규약: 렌더 제외·Status 유지). 회수 유형은 대상 아님.
additive — 기존 골든 불변(픽스처에 중복 뉴스 부재).

## 15. deep_analysis — 7번째·선택 슬롯 (WL 심층분석 fan-out, 2026-07-01)

> 배경: 사용자 피드백 — 얇은 6슬롯(§0 표)만으로는 Warning Letter 카드의 시사점이 "조잡"하다.
> §0~§14(6슬롯·동결)는 **그대로 불변**이다. 이 §15 는 그 위에 얹는 **완전 additive·선택적**
> 7번째 슬롯이며, §0의 "LLM 이 채우는 칸은 단 6종" 원칙을 깨지 않는다 — deep_analysis 는
> 별도 파이프라인(fan-out)이 채우는 별도 필드로, 6슬롯 Routine(GRM_Prompt_v16.md)은
> 이 필드를 전혀 참조하지 않는다. 변경은 이 §15 + golden/테스트 갱신으로만.

**(A) 적용 범위.** `kind == "warning-letter"` + 수집기가 `raw.wl_body_full`(전문, `ENABLE_WL_BODY_FULL`
게이트)을 확보한 카드만. 그 외 전 유형·전문 미확보 WL 카드는 항상 `deep_analysis_ready=False`이며
`to_web_card()` 출력에 `deep_analysis` 키 자체가 없다(기존 20+ golden 바이트 불변).

**(B) 왜 별도 파이프라인인가(fan-out).** 기존 6슬롯 Routine은 "그 주 카드 전체를 한 세션이
처리"하는 모델이라, 카드당 본문 전문+4섹션 산문까지 얹으면 세션 부하·환각 위험이 카드 수에
비례해 커진다. 대신 **카드 1건 = 독립 호출 1건**(fan-out)으로 분리한다 — 호출당 컨텍스트가
"편지 1건"으로 고정되어 카드 수와 무관하게 부하가 일정하고, 카드 간 내용 혼동이 구조적으로
없다. 이 fan-out 은 6슬롯 Routine 예산(WebSearch 9/WebFetch 5)과 완전히 분리된 별도 트랙이다.

**(C) 스키마(4섹션 — §2.5 확정: Overview 제거·required_remediation 객체화).**
```json
{
  "key_violations": [
    {"citation": "21 CFR 211.194(a)", "description": "...", "risk": "..."}
  ],
  "fda_evaluation": "평문 — FDA의 이전 대응 평가",
  "required_remediation": {"deadline": "15영업일 이내 서면 회신", "items": ["...", "..."]},
  "administrative_risks": "평문 — 행정처분·수입금지 등 리스크"
}
```
> §2.5 변경: (1) `overview` 삭제 — 표·핵심사실과 중복이라 6슬롯 summary(W1)가 규제 프레이밍을
> 흡수(fan-out 무관). (2) `required_remediation` 문자열 → `{deadline, items[]}` 객체(마감기한 한
> 줄 + 체크리스트). D2/D3 인용·숫자 근거대조는 `key_violations[].citation` 대상 그대로 유지.

**(D) 입력 컨텍스트.** fan-out 호출의 유일한 입력은 handoff v2 의 `deep_analysis_input.body_full`
(= `raw.wl_body_full`, `CardScaffold.to_dict()` 가 대상 카드에만 조건부로 싣는다). 카드 메타데이터
(facts·prose_input)는 참고만 하고, 4섹션 값은 반드시 `body_full` 원문에서 확인 가능한 사실만
쓴다 — 6슬롯 규칙(§0 "사실 생성 금지")과 동일 원칙.

**(E) 검증 게이트(발행 전 필수).** `verify_deep_analysis.run_deep_analysis_gate(deep_analysis,
body_full)` — D1 구조 완전성(4섹션 전부 채움 + `required_remediation` 은 `{deadline, items[]}`
객체이고 `items` 가 비어있지 않아야 함) + D2 조항 인용 근거대조(원문에 없는 조항번호 인용
= FAIL) + D3 원문에 없는 신규 장문 숫자(WARN, 비차단). D2 인용은 **원문과 같은 어순/표현**으로
써야 통과한다(예: "21 CFR 211.192" 처럼 원문 그대로 — "FD&C Act 502(a)"를 원문이 "section
502(a) of the FD&C Act" 로 쓴 경우처럼 어순을 바꾸면 대조 실패로 FAIL 처리될 수 있다. 원문
표현을 그대로 따르라). **FAIL 이 하나라도 있으면 그 카드는 deep_analysis 없이(6슬롯만으로)
발행한다** — 이 실패가 전체 브리프 발행을 막지 않는다(graceful degrade, 카드 단위 격리).

**(F) 병합.** `inject_slots.inject_deep_analysis(brief, deltas)` — `deltas` =
`{document_id: {"deep_analysis": {...}, "source_text": "..."}}`. 게이트 통과 카드만
`card["deep_analysis"]` 에 병합. 6슬롯 `inject_llm_slots()` 와 완전히 별개 함수(서로 호출 안 함).

**(G) 렌더(단계적 노출 + §2.5 시각위계).** `web/partials/card.html` — 기존 카드 본문(요약·핵심사실·
시사점·점검사항) 아래, 출처 링크(`.src`) 위에 `<details class="block deep">` 로 접힌 상태 기본
노출("상세 분석 보기" 클릭 시 4섹션 펼침). 4섹션 헤더는 **원형 번호 배지(①②③④ — 폰트 비의존
CSS 원+숫자, tofu 회피)+세리프 소제목**으로 위계를 준다. ① Key Violations 는 위반 항목별 카드
블록(`.viol` — 조항 코드배지 `.viol-cite`·설명·점선 아래 경고색 리스크 `.viol-risk`), ③ Required
Remediation 은 마감기한 한 줄+체크(✓) 리스트(`.rem-list`, 단일 마커 — 점검사항 `.chk` 와 구분).
deep 카드는 W2 facts 를 2×2 그리드(`.facts-grid`)로 렌더(값 자체 불변 — 마크업/CSS 만, deep
아님 카드는 기존 세로 표라 golden 불변). `card.deep_analysis` 가 없거나 `null` 이면 이 블록·그리드
모두 렌더되지 않는다(기존 카드 무변형). 클릭 → 심층분석 → 출처 링크(공식원문) 3단계 노출.

**(H) 구현 위치.** `collect_intake.py`(`_extract_wl_body_full`/`_fetch_wl_body_full`,
`ENABLE_WL_BODY_FULL`) · `card_scaffold.py`(`deep_analysis_ready`, `to_dict`/`to_web_card` 조건부
필드) · `verify_deep_analysis.py`(신규 모듈) · `inject_slots.py`(`inject_deep_analysis`) ·
`web/partials/card.html` + `web/assets/grm.css`(`.deep`/`.viol`/`.rem-list`/`.facts-grid`) ·
`web/render.py`(`_card_view` passthrough) · `deep_analysis_fanout.py`(신규 — `build_jobs`/
`assemble_deltas` 오케스트레이션 헬퍼, 순수) · `docs/prompts/GRM_Prompt_DeepWL_v1.md`(fan-out
프롬프트) · `docs/prompts/GRM_DeepWL_fanout_실행프롬프트.md`(실행 절차).

**fan-out 실행모델 — 확정(2026-07-01).** 저장소에 Anthropic API 키 기반 LLM 호출이 전무함을
전수 확인(6슬롯 Routine = MINO 의 Claude Code 세션, 구독 사용량·무과금). deep_analysis 도 동일하게
**카드당 Claude Code 서브에이전트 1개**(그 카드 `body_full` 만 컨텍스트)로 처리한다 — **신규 GitHub
Actions + Anthropic API 키(호출당 과금) 조합은 배제.** 오케스트레이션 = 순수 스크립트
(`deep_analysis_fanout` build-jobs/assemble) + 월요일 Routine 2단계 세션 절차(실행프롬프트) +
`inject_slots.py --deep-analysis-deltas` 병합. 표본 육안 검증 후 운영 투입(§3.2).

**(I) golden·회귀.** 기존 20+ web-card golden·8xx 회귀 스위트 바이트 불변(전제조건). 신규
회귀: `tests/test_wl_body.py`(WlExtractBodyFullTest 등) · `tests/test_card_scaffold.py`
(DeepAnalysisReadyTest) · `tests/test_verify_deep_analysis.py`(전체) ·
`tests/test_inject_slots.py`(InjectDeepAnalysisTest·DeepAnalysisRenderSmokeTest — 실제
`render.render_site()` 로 HTML 렌더까지 확인).

## 16. deterministic_detail — 결정론 상세보기 슬롯 (MFDS GMP실사 · FDA 483, 2026-07-02)

> 배경: PDF/전문 수집 트랙 실측(`GRM_PDF전문수집_MFDS_GMP실사_실측_2026-07-02.md`) — MFDS GMP
> **정기실태조사** PDF 는 지적(보완)사항을 **분야·구분(중대도)·근거법령·지적내용·비고 5컬럼 표**로
> 공개한다(트랙에서 상세보기가 값 있는 소스 2종 중 1종, 접근·의존성 리스크 0). 이 §16 은 §15
> deep_analysis(LLM 분석층)와 **병렬**되는 **결정론 층** 상세보기다. §0~§14(6슬롯 동결)·§15 는 불변.

**(A) 왜 별개 층인가(§12 "층 혼용").** §12 확정 모델의 **결정론 층(항상)** — DB/수집기 필드에서
구조화된 사실, 환각 0·fetch 0. deep_analysis(LLM 분석층·조건부·`verify_deep_analysis` 게이트)와
달리 **생성이 없어** 근거대조 게이트가 불필요하다(수집기가 공개 PDF 표를 그대로 구조화). 그래서
`deep_analysis` 와 **완전히 다른 필드**(`deterministic_detail`)로 낸다 — 서로 오염 안 함. GMP실사는
결정론 층만 갖는다(분석층 없음). **[FDA 483 상세보기 2026-07-02]** 같은 슬롯에
`type:"fda_483_observations"` 를 병렬 추가했다 — 483 PDF Observation 번호 목록을 LLM 없이
구조화한다.
**[FR 상세보기 철회 2026-07-02]** 한때 Federal Register 상세보기가 이 슬롯의 `type`(`fr_summary`)으로
합류했으나 **철회**했다(FR=요약보강 — 마감일/시행일은 API 메타, 전문은 abstract=요약이라 증분 가치
없음). §17(B).
**[WL 위반항목 상세 2026-07-20]** 같은 슬롯에 `type:"wl_violations"` 를 병렬 추가했다. 그 전까지
Warning Letter 는 **결정론 층이 없는 유일한 주요 소스**였고, 위반 상세를 카드에 싣는 경로가 §15
deep_analysis(LLM·조건부) 하나뿐이었다. 그래서 fan-out 이 돌지 않은 주에는 상세가 통째로 비었고,
2026-07-20 발행분에서는 그 빈 입력이 "세부 위반내용은 원문에 명시되지 않았다"는 **거짓 서술**까지
낳았다(원문 2만자·조항별 위반 3~5건을 이미 확보한 상태였다). 이 타입이 그 **바닥**이다 — fan-out
성패와 무관하게 조항별 위반 표제가 항상 남는다. 따라서 이 슬롯의 `type` 은 `gmp_deficiencies`·
`fda_483_observations`·`wl_violations` 셋이다.

**(B) 적용 범위·유형 분기.**
- `kind == "gmp-inspection"` + 수집기가 `raw.gmp_deficiencies`(구조 배열,
`ENABLE_GMP_DEFICIENCY_TABLE` 게이트)를 확보한 카드만. nedrug 실사결과 PDF 는 유형이 둘로 갈린다:
- **정기실태조사**(국내 제조소, `_detect_inspection_type`→`periodic`) — 지적 표 공개 → **상세보기 대상.**
- **사전 GMP 평가**(해외 수입, `pre_market`) — 판정("실사 결과: 적합")만 → **요약보강**(표 없음 → `deterministic_detail` 부재). periodic 만 표 추출 시도(사전평가에 강제하면 오탐/빈블록).
- `kind == "fda-483"` + 수집기가 `raw.fda_483_observations`(구조 배열,
`ENABLE_FDA_483_OBSERVATIONS` 게이트)를 확보한 카드만. `Record Type=="483"` 만 대상이며 EIR 은
별개 문서라 이번 범위 밖. 추출 실패·OCR 오인식·구조 이질이면 키 부재 → 요약카드 유지.
- **[2026-07-20]** `kind == "warning-letter"` + 수집기가 `raw.wl_violations` 를 확보한 카드만.
전용 플래그는 없다 — 이미 확보한 `wl_body_full`(`ENABLE_WL_BODY_FULL`)에서 파생하는 순수 함수라
네트워크·비용이 0 이다(`collect_intake.extract_wl_violations_from_text`). 표제 판별은 **3신호 동시
충족**일 때만 인정한다: ① 번호 뒤 도입부가 `Your firm failed`/`You failed`/`did not` 계열, ② 그
문장에 `21 CFR …` 인용 존재, ③ 인용 괄호가 닫힌 뒤 마침표로 종결. 번호가 역행/중복하면 각주
오인식으로 보고 버린다. 하나라도 어긋나면 건너뛴다(**과소추출 우선** — 잘못된 표제를 카드에
싣는 것보다 안전). 번호 목록이 없는 편지(미승인의약품 WL 등)는 빈 결과 → 키 부재 → 요약카드 유지.

**(C) 스키마.** `to_web_card()` 출력의 `deterministic_detail`(대상 raw 존재 시만; 없으면 키 자체 부재
→ 요약카드 유지·기존 golden 바이트 불변).

GMP:
```json
{
  "type": "gmp_deficiencies",
  "count": 3,
  "severity_summary": {"중대": 0, "중요": 1, "기타": 2},
  "rows": [
    {"area": "시설장비", "severity": "기타", "legal_basis": "[별표1] 2.1호",
     "summary": "제품 교차오염 방지 제조시설 운영할 것", "followup": "이행계획 타당성 인정"}
  ]
}
```

FDA 483:
```json
{
  "type": "fda_483_observations",
  "count": 2,
  "observations": [
    {"number": "1", "deficiency": "There is a failure to thoroughly review any unexplained discrepancy.",
     "detail": "The investigation did not extend to other potentially affected batches."}
  ]
}
```

Warning Letter **[2026-07-20]**:
```json
{
  "type": "wl_violations",
  "count": 3,
  "violations": [
    {"number": "1",
     "statement": "Your firm failed to thoroughly investigate any unexplained discrepancy or failure of a batch to meet any of its specifications (21 CFR 211.192).",
     "citation": "21 CFR 211.192"}
  ]
}
```
> `statement` 는 편지 원문 영어 **verbatim**(요약·의역 0), `citation` 은 그 표제문 안의 `21 CFR`
> 조항을 등장 순서대로 ` · ` 로 이은 문자열. 483 의 `deficiency_ko`/`detail_ko` 와 동형으로
> `statement_ko`(선택)가 있으면 원문↔국문 병기로 렌더하고, 없으면 영문만 렌더한다(키 부재 시
> 기존 골든 바이트 불변).

> `severity_summary` 는 실제 등장한 중대도만 집계(0 미표기 — 위 예시는 형태 설명용). 중대도 의미
> (PIC/S 정의): **중대·중요 = 행정처분 부과**(→의약품안전나라 행정처분정보 공개), **기타 = 시정·보완.**
> 이 연계로 GMP실사 상세가 MFDS 행정처분(§15 분석층 소스)과 자연 연결된다.

**(D) 수집·저장(결정론).** `collect_mfds_gmp_inspection.py` — `_extract_deficiency_table`(PyMuPDF
`find_tables()`, 새 의존성·OCR·LLM 0) → `_normalize_deficiency_table`(헤더 토큰 매핑·주석/빈행 제외·
`\n`→공백). **품질 게이트**: 각 행은 `legal_basis` 또는 `summary` 비어있지 않아야 유효. periodic·지적
present 인데 유효행 0 = `gate-degraded`(WARN + 표 미기록, 요약카드 유지). '지적사항 없음' = `empty`
(정상·적합 배지). 앵커 콜론형("평가 결과: 지적…") 정규식 보정 동반. 관측: `LAST_HEALTH["deficiency_table"]`
{enabled, attempted, extracted, failed, warnings[]} → 오케스트레이터 `stats.gmp_deficiency_table_*`
(WHOPIR excerpt health 동형·비차단).
`collect_fda_483.py` — 현행 OII 리딩룸 HTML/DataTables AJAX(`/datatables/views/ajax`)를 주 경로로
페이지네이션(`foia_record_type_name=483`, Publish Date desc), 정적 HTML 10행은 fallback(degrade warning).
PDF 는 기존 `_extract_pdf_text` 엔진 재사용 → `WE OBSERVED` 이후 `OBSERVATION\s+(\d+)` 앵커 분할 →
보일러플레이트 절단 → 첫 문장=`deficiency`, 나머지=`detail`(상한). **품질 게이트**: 각 행은
`deficiency` 비어있지 않아야 유효, 텍스트층 깨짐률 초과/유효행 0 = WARN + 표 미기록. 관측:
`LAST_HEALTH["fda_483_observations"]` {enabled, attempted, extracted, failed, warnings[]} →
오케스트레이터 `stats.fda483_observations_*`(비차단).
`collect_intake.py` **[WL 2026-07-20]** — 별도 fetch 없음. `_fetch_wl_body_full` 이 이미 가져온
`wl_body_full` 을 `extract_wl_violations_from_text`(순수 함수)에 넘겨 `raw.wl_violations` 로 저장한다.
빈 결과면 키를 달지 않는다. 같은 커밋에서 두 가지를 함께 고쳤다: ① `_skip_wl_leadin` — 앵커가 잡는
첫 문장이 내용 없는 도입구("…violations including, but not limited to, the following.")면 그 **뒤**로
시작점을 옮긴다(하류 `prose_input` 300자 문장경계 절단이 도입구 하나만 남기고 실제 위반을 버리던
2026-07-20 사고의 직접 원인). 뒤에 실질 본문이 남을 때만 이동하는 보수적 게이트. ② `WL_BODY_FULL_MAX_CHARS`
20000→30000(실측 21.4k·24.1k — 종전 상한이 회신 기한·시정요구 문단을 잘라내고 있었다).

**(E) 렌더(단계적 노출).** `web/partials/card.html` — WL `<details class="block deep">` **옆**에
`<details class="block detail">`(요약/핵심사실 뒤·출처 링크 앞, 기본 접힘 → "지적사항 상세" 클릭 시
표 펼침). 분야·근거법령=열, 중대도=배지(`.dt-badge`), 비고=후속줄. `type == "gmp_deficiencies"` 분기.
FDA 483 은 같은 블록에서 `type == "fda_483_observations"` 분기 — Observation 번호 배지 +
deficiency + 선택 detail 목록, 신뢰 라벨 "원문 기반".
**[WL 2026-07-20]** `type == "wl_violations"` 분기 — "위반항목 상세 · N건 · 원문 기반", 행마다
`위반 N` 배지(`.obs-num`) + 조항(`.dt-law`) + 원문 표제문(`.obs-en`). **CSS 추가 0**(483/GMP 블록의
클래스를 그대로 재사용).
`web/render.py` `_card_view` 는 `deterministic_detail` 을 무가공 통과(deep_analysis 와 동일). `grm.css`
는 `.deep`/표 클래스 최대 재사용 + 최소 additive(`.detail`/`.dt-*`) — **한글안전(§4): 셀은 전부
한글일 수 있어 mono/자간 절대 미사용**(근거법령도 일반 sans).

**(F) 점진 활성.** `ENABLE_GMP_DEFICIENCY_TABLE`(기본 off, `ENABLE_WL_BODY_FULL` 운영 패턴) — off 면
기존 excerpt/assessment 플로우 완전 무변경. off→dry-run 관측→실적재 확인 후 on.
`ENABLE_FDA_483` 는 현행 HTML/DataTables 소스 검증 후 워크플로 기본 on(변수로 false override 가능).
`ENABLE_FDA_483_OBSERVATIONS` 는 opt-in·기본 off — off 면 483 메타/excerpt 카드만 유지, on + 추출 성공 시
상세보기 부착.

**(G) 구현 위치.** `collect_mfds_gmp_inspection.py`(`_deficiency_table_enabled`·`_detect_inspection_type`·
`_normalize_deficiency_table`·`_extract_deficiency_table`·`_parse_deficiency_table`·health) ·
`card_scaffold.py`(`_deterministic_detail`, `to_web_card` 조건부 필드) · `collect_intake.py`
(`gmp_deficiency_table_*` stats) · `web/partials/card.html`(`<details class="block detail">`) ·
`web/render.py`(`_card_view` passthrough) · `web/assets/grm.css`(`.detail`/`.dt-*`).
FDA 483: `collect_fda_483.py`(`_fetch_html_rows`/DataTables pagination·`_extract_483_observations`·
health) · `card_scaffold.py`(`_deterministic_detail` type=`fda_483_observations`) ·
`collect_intake.py`(`fda483_observations_*` stats) · `web/partials/card.html` · `web/assets/grm.css`.

**(H) golden·회귀.** 기존 20+ web-card golden·900+ 회귀 바이트 불변(deterministic_detail 은 표 확보
카드에만 나타나 기존 fixture 무영향). 신규: web-card golden 2종(`gmp_inspection_periodic`=표 有·
`gmp_inspection_pre_market`=표 無) · `tests/test_gmp_inspection.py`(유형분기·정규화·find_tables 추출/
결정론·게이트/degrade·앵커 콜론) · `tests/test_card_scaffold.py`(DeterministicDetailTest) ·
`web/tests/test_render.py`(WebDeterministicDetailTest — 실 `render.render_site()` 렌더 확인).
FDA 483 신규: web-card golden 2종(`fda_483`=관찰 無/상세 부재·`fda_483_observations`=관찰 有) ·
`tests/test_fda_483.py`(HTML/DataTables 파싱·Observation 추출·게이트/degrade·health) ·
`tests/test_card_scaffold.py`(Fda483DeterministicDetailTest) ·
`web/tests/test_render.py`(WebFda483DeterministicDetailTest).
## 17. 소스별 상세보기 확장 + 요약카드 보강 (2026-07-02)

> §15(WL deep_analysis)의 자산을 나머지 소스로 확장한다. **모델(설계문서 §12): 모든 카드 상세영역
> = 2개 층** — ① 결정론 층(DB 필드 조립, 환각·fetch 0, 항상) + ② LLM 분석 층(Body 두껍고
> `verify_deep_analysis` 통과 시만 additive). 실 DB 깊이 기준 펼침 상세보기가 값 있는 소스는 **3개뿐**
> (FDA WL·MFDS 행정처분=분석층까지, Federal Register=결정론 상세). 나머지 13개는 데이터가 얕아
> 요약카드로 충분(펼침 상세 없음). §0~§15·§16(deterministic_detail) 불변, 이 §17 + golden/테스트 갱신으로만.

**(A) MFDS 행정처분 — 상세보기 + 분석층(§15 패턴 확장).** WL 4섹션 뼈대 재사용, ②섹션만 교체:
`fda_evaluation`(응답 왕복 평가) → **`disposition_basis`**(확정처분 내용·수위·판단근거 — 행정처분엔
"응답 왕복"이 없음). 스키마: `{key_violations[], disposition_basis, required_remediation{deadline,items[]},
administrative_risks}`. `verify_deep_analysis`: `REQUIRED_SECTIONS_ADMIN` + `resolve_required_sections`
(card_type 없으면 `disposition_basis` 키로 admin 자동판별, 있으면 WL 기본 → 후방호환)·3검사 `sections`
파라미터(기본=WL)·**한국법령 D2 정규식**(약사법 제N조·「의약품 등의 안전에 관한 규칙」·[별표N]·bare
제N조; 조사 경계는 후행 `\b` 미사용=D3 교훈 동형). 입력 body = `raw.admin_body_full`(다단락 Body —
위반상세 EXPOSE_CONT+근거법령 BEF_APPLY_LAW+처분명, `collect_mfds_admin_action` 의 `ENABLE_MFDS_ADMIN_BODY_FULL`
opt-in·기본 off 시 주입, 외부 fetch 0). `card_scaffold.deep_analysis_ready` 를 admin-action 로 확장,
`deep_analysis_input.body_full` 일반화(wl or admin). `card.html`: 섹션2 키 분기(fda_evaluation|disposition_basis)
+ 한글 섹션명(① 위반 항목 및 리스크 / ② 처분 내용 및 근거 / ③ 이행·후속 조치 / ④ 행정 리스크). WL 은
`disposition_basis` 부재 → 영문 그대로 = **바이트 불변**. 생성 프롬프트 `docs/prompts/GRM_Prompt_DeepAdmin_v1.md`
(신규), fanout 절차는 유형무관 일반화(build-jobs 가 유형 상관없이 deep_analysis_ready 카드 수집).

**(B) Federal Register — 요약보강(상세보기 철회, 2026-07-02).** 한때 이 Phase B 가 FR abstract 를 §16
`deterministic_detail`(type=`fr_summary`)로 승격했으나 **철회**했다 — PDF/전문 수집 트랙 실측
(`GRM_PDF전문수집_FR프로토타입_스키마확정_2026-07-02.md` §0)에서 FR 상세보기는 증분 가치가 없음이
확인됐다: 값 있는 마감일/시행일은 `comments_close_on`/`effective_on` API 메타로 이미 확보, 본문은
abstract 가 이미 요약이다(MINO 결정). 따라서 FR 은 기존 6슬롯 + 듀얼링크 요약카드로 복귀한다(신규
블록 없음). `_deterministic_detail` 의 guidance 분기·`_FR_KIND_LABEL`/`_fr_detail_kind`·`card.html` 의
`type=='fr_summary'` 분기·`render._detail_preview` FR부·FR 골든의 `fr_summary` 블록을 모두 제거했다
(GMP `gmp_deficiencies`·483 `fda_483_observations` 는 같은 슬롯의 다른 `type` 이라 불변).

**(C) 요약카드 보강 — 결과/원문 PDF 링크 승격(신규 블록 없음, MINO 확정).** 나머지 13개 소스는 이미
facts(대상·일자)·quote(사유, Ev A)·sources(링크)·LLM 슬롯으로 커버 → 신규 블록은 중복. 유일한 실질
보강 = 검사·실사 결과 문서 링크. `_dual_links`: who-inspection→`raw.pdf_url`(WHOPIR 결과 PDF, 종전
official_url=HTML 페이지만 노출)·gmp-inspection `download_url` 폴백. fda-483 은 이미 `raw.pdf_url` 노출.

**(D) 상세보기 UI 보강(3개 공통).** 접힘 미리보기 태그(`<details>` summary 에 결정론 내용 힌트 —
`_deep_preview`/`_detail_preview`)·신뢰 라벨(분석층="원문 근거·검증"/결정론="원문 기반")·해석 뱃지
(`.tag-interp`, 분석층 summary, coral 기존 토큰 재사용·한글 안전=자간/mono 미적용). CSS 신규 색 토큰 0.

**(E) golden·회귀.** 기본 OFF 플래그(admin)·샘플브리프 부재(deep/detail)라 커밋 골든 additive —
`guidance_fr.expected.webcard.json`·`brief_web.expected.json`(FR detail은 v1.62 에서 철회 — 재동결본에
`fr_summary` 없음)·`who_inspection[_excerpt]` 계열(WHO PDF)만 의도적 재동결, 웹 full-page 골든 불변.
**1075 green**(v1.60 시점). 비용모델(신규 API 과금 0)·외부 PDF/전문 fetch 0(승격은 별도 트랙 —
설계문서 §16). 신규 회귀: `test_verify_deep_analysis`(admin 스키마·한국법령 D2·조사경계·자동판별)·
`test_card_scaffold`(admin ready·FrDetail=철회검증·who-inspection 승격)·`test_inject_slots`(admin 병합·
한글섹션 렌더·FR 상세 철회검증·UI 보강).

## 📝 변경 이력
| 날짜 | 변경 내용 |
|---|---|
| 2026-07-20 | **483 전수 점검(70건) + 원문 무결성 3층 방어** — WL 사고의 같은 결함 클래스를 483 까지 전수 확인. 결과: **483 8건이 원문에 관찰이 있는데 "관찰 원문 없음"으로 발행돼 있었다**(그중 2건은 당주 발행분·디제스트에 "스캔·비공개"로 접힘). 원인이 셋이었다 — ①`merge_fda483_disclosures`(접기)가 deep 주입 **앞**에 있어, 원문 재추출로 관찰이 되살아나는 카드를 이미 접은 뒤였다(**순서 교정**) ②PDF 서브셋 폰트 합자가 정상 유니코드 문자로 나와(`iniƟal`=initial·`wriƩen`=written·`ﬁ`=fi) `_text_corruption_ratio` 를 통과했다(**`normalize_pdf_ligatures` 신설** — 수집 경로와 파서 양쪽에 적용해 이미 커밋된 `source_text` 도 복원) ③문장 중간에 낀 관찰 참조("…, OBSERVATION 1 and the Discussion Items, had already been discussed…")가 `_select_observation_anchors` 의 두 신호를 다 통과해 가짜 관찰을 만들었다(**신호 ③ 추가 — 표제 뒤 첫 문장이 소문자로 시작하면 기각**. 실측 9문서·29관찰 중 소문자 시작 0건). 부수 수리: `_refresh_483_observations` 가 **없던 블록도 신설**, `inject_deep_analysis` 의 `observations_ko` 병합을 심층분석 게이트에서 **분리**(deep-ready 아닌 카드의 번역이 버려져 배포 fail-closed 게이트에 걸리던 문제), 조립 게이트 4(`_lint_483_observation_ko`, 이번 조립에서 손댄 카드만), 거짓 부재 규칙 주어 확장(`조항·근거·사유·처분·상세` — "근거: 21 U.S.C.(세부 조항 원문 미기재)" 형태를 놓치고 있었다), 디제스트 문안에서 "스캔·비공개" 단정 제거. **예방 3층**: (1) 조립 게이트(그 주 발행분) (2) CI 스윕 `tests/test_published_briefs_integrity.py`(**전 발행본** 상시 재검사 — 한 번 새어 나간 거짓이 영영 남는 것을 차단) (3) 주간 워크플로 `grm-source-verification.yml`+`verify_published_sources.py`(**원문 재수집 대조** — 저장소 안에서는 알 수 없는 "처음부터 못 받은 누락"을 잡는 유일한 층). 프롬프트 규칙 2-1 신설(부재 주장 금지) + `prose_input.source_body_captured` 신호 추가(골든 21종 재동결). 과거 발행분 7장 정정(06-26 1·07-06 5·07-12 1). 신규 회귀 `tests/test_source_integrity.py`·`test_published_briefs_integrity.py`. |
| 2026-07-20 | **WL 결정론 위반항목 슬롯(§16 `wl_violations`) + 거짓 부재 서술 차단 게이트** — 2026-07-20 발행분 사고 대응. 사고: 수집기가 WL 원문 전문(2만자·조항별 위반 3~5건)을 정상 확보했는데 카드는 "세부 위반내용은 원문에 명시되지 않았다"고 발행됐다. 세 겹의 결함이 겹쳤다 — ①WL 만 결정론 상세층이 없어 상세 경로가 §15 deep_analysis(LLM·조건부) 하나뿐 ②그 fan-out 이 안 돈 주, 폴백인 6슬롯 LLM 에 전달된 입력은 `prose_input` 300자 문장경계 절단을 거쳐 도입구 **118자뿐**("…including, but not limited to, the following. 1.") ③그 거짓 서술을 막는 게이트 부재. 수리: **(1)** `collect_intake._skip_wl_leadin` — 내용 없는 도입구 뒤에서 자른다(뒤에 실질 본문이 남을 때만 이동하는 보수적 게이트). `WL_BODY_FULL_MAX_CHARS` 20000→30000(실측 21.4k·24.1k — 회신 기한 문단이 잘리고 있었다). **(2)** `extract_wl_violations_from_text`(순수·3신호 판별) → `raw.wl_violations` → `card_scaffold._detail_wl_violations` → §16 `type:"wl_violations"` → `card.html` 분기(CSS 추가 0). fan-out 성패와 무관한 **바닥**이 생겼다. **(3)** `assemble_publish_brief._refresh_wl_violations` — 조립 시점에 deep 델타의 `source_text` 에서 재추출해 **블록이 없던 스캐폴드에도 신설**한다(#373 483 재추출과 같은 이유 + 슬롯 신설분 대응). **(4)** `lint_false_absence_claims` — 원문 확보 증거(결정론 블록 또는 deep_analysis)가 있는 카드가 위반/관찰/지적의 부재를 주장하면 **발행 차단**(`report.errors`). 이 게이트가 도입 즉시 같은 결함의 3번째 카드(MFDS GMP실사 — 지적표 3행을 싣고도 "원문 미기재")를 잡아냈다. 신규 회귀 `tests/test_wl_violations.py` 27건, 기존 골든 바이트 불변(전 소스 flag/키 부재 시 additive). |
| 2026-07-13 | **전문지 브리핑 소스확장 — ISPE iSpeak**: ECA 에 이은 두 번째 '전문지 브리핑' 소스로 ISPE iSpeak 블로그(RSS·Drupal) 추가(`SOURCE_ISPE`·`ENABLE_ISPE` 기본 off). `RESOURCE_AGENCIES`=("ECA","ISPE")로 확장(§ `brief.resources[]` 판정 조건은 무변경 — agency 게이트만 확장). `keep_item`(`_is_ispe_gmp_relevant`)이 `grm_taxonomy.compute_relevance` 어휘를 그대로 재사용해 협회 홍보성 항목(Board of Directors 후보·Member Spotlight 등)을 배제. 기사 본문 흡수는 ECA 와 동형이나 `raw_payload` 키는 제네릭 `article_excerpt`(`eca_article_excerpt` 는 기존 적재행 호환을 위해 유지) — `card_scaffold` excerpt 소비 3곳(`source_excerpt_present`·`issue_or_reason`·`body_excerpt`)에 폴백 추가. `ENABLE_ISPE_ARTICLE_EXCERPT`(기본 off) 게이트. 전부 flag off 시 골든 바이트 불변. |
| 2026-07-13 | **`brief.resources[]` additive(v1 호환·선택 키)**: 해설·교육 소스(ECA류) 전용 브리핑 노트 섹션, 이벤트 카드에서 분리. `assemble_publish_brief.extract_resource_notes()`(agency ∈ `RESOURCE_AGENCIES`=("ECA",) ∧ type_tag=='GMP News' 또는 card_type=='규제 소식') 가 `merge_fda483_disclosures` 직후·render_order 재부여 직전 실행 — 남은 이벤트 카드에만 render_order/빈슬롯 게이트/coverage.evidence 적용. `brief.agencies` 는 이벤트+리소스 agency 합집합(이벤트 순서 우선). `web/render.py`(`_resource_view`)+신규 partial `web/partials/resource_notes.html`(`{% if brief.resources %}` 전체 게이트, `<style>` 포함 — resources 없는 브리프는 바이트 불변)+`brief.html` TOC 단일 엔트리·include 배선. 실데이터(2026-07-12 스캐폴드+델타 재조립) 검증: 이벤트 30장(37−7)+resources 7건, coverage.resources=7, agencies 에 ECA 유지. 신규 golden `brief_resources.expected.html` + 기존 golden 전부 바이트 불변(재동결 diff 0). |
| 2026-07-06 | **회수 병합 범위 확장 + 뉴스 중복 제거(§12E·§14A·§14G·§14H)**: 7/6 발행본 중복 진단(글로벌 TGA 2·Recall Dabur 14·Keystone 7·PAI 4·동화약품/대일제약)에서 3결함 확정 — ①OpenFDA 회수는 `kind="openfda-recall"` 이라 `merge_recall_cards`(recall-quality 전용)·`recall_group_key`(MFDS 필드 전용) 양쪽서 배제돼 fan-out 이 카드 N장 ②MFDS 키가 발행일 포함이라 같은 사건 다른 날 재등록 시 병합 갈라짐 ③동일 rss-news 기사가 `_stable_doc_id`(date_iso 포함)로 doc_id 갈려 수집 dedup 우회. 수정: `recall_group_key` 소스별 분기(MFDS `MFDS|ENTRPS|RTRVL_RESN` + OpenFDA `event_id`/`firm|reason`)·**발행일 제외**·소스 네임스페이스, group_key 산출·`merge_recall_cards` 를 openfda-recall 까지 확장(prose_input·W2 "제품"·병합 렌더 기계는 기존 그대로 재사용). 신규 순수함수 `dedupe_news_cards()`(grm_handoff 병합 지점 배선, rss-news 동일 제목 대표 1장·`merged_into`). 골든 영향=`recall_group_key` 직렬화 2종(키 문자열만)·markdown/web-card/handoff 골든 불변, 뉴스 dedup=additive. 신규 회귀 `OpenFdaRecallMergeTest`·`MfdsRecallCrossDateMergeTest`·`DedupeNewsCardsTest`. |
| 2026-07-02 | **FR 상세보기 철회(FR=요약보강)**: v1.60 Phase B 가 도입한 Federal Register 결정론 상세보기(`deterministic_detail` type=`fr_summary`)를 외과 제거. 근거=`GRM_PDF전문수집_FR프로토타입_스키마확정_2026-07-02.md` §0(마감일/시행일=API 메타·본문=abstract 요약 → 증분 가치 0). `_deterministic_detail` guidance 분기·`_FR_KIND_LABEL`/`_fr_detail_kind`·`card.html` `type=='fr_summary'` 분기·`render._detail_preview` FR부·FR 골든 2종(guidance_fr.webcard·brief_web)의 `fr_summary` 제거. GMP `gmp_deficiencies`·483 `fda_483_observations`(같은 슬롯 다른 `type`)·admin deep_analysis·UI 보강은 완전 불변. FrDetail/inject_slots FR 테스트는 철회 검증으로 전환. |
| 2026-07-02 | **FDA 483 Observation 결정론 상세보기 추가(additive)**: 죽은 `datatables-json/ora-foia-reading.json` 경로를 버리고 현행 OII 리딩룸 HTML/DataTables(`/datatables/views/ajax`)를 주 경로로 전환(`Record Type=483`, media id 링크, Publish Date 페이지네이션). `ENABLE_FDA_483` 워크플로 기본 on(변수 false override 가능)·`ENABLE_FDA_483_OBSERVATIONS` opt-in 기본 off. 483 PDF 텍스트층에서 `WE OBSERVED` 이후 `OBSERVATION N` 분할→첫 문장 deficiency·나머지 detail, 품질게이트 실패 시 raw 키 미기록+요약카드 유지. `deterministic_detail.type="fda_483_observations"` 를 §16 슬롯에 병렬 추가, `card.html` Observation 목록 분기와 최소 CSS(`.obs-num`) 추가. 신규 web-card golden 1종(관찰 有) + 기존 fda_483(관찰 無)·수집기/card_scaffold/render/health 회귀. |
| 2026-07-02 | **§16 신설(additive) — deterministic_detail 결정론 상세보기 슬롯(MFDS GMP실사)**: PDF/전문 수집 트랙 실측 근거로 MFDS **정기실태조사** 지적사항 5컬럼 표(분야·구분·근거법령·지적내용·비고)를 PyMuPDF `find_tables()` 결정론 추출→저장→상세보기 렌더로 승격(새 의존성·OCR·LLM 0, 환각 0). §15 deep_analysis(LLM 분석층)와 병렬되는 **결정론 층**으로 별도 필드(`deterministic_detail`). 유형 2분기(periodic=상세보기·pre_market=요약보강)·품질 게이트(유효행 0∧present→degrade)·`ENABLE_GMP_DEFICIENCY_TABLE`(기본 off) 점진 활성. 렌더는 WL `.deep` 옆 `<details class="block detail">`(단계적 노출)·`.detail`/`.dt-*` CSS(한글안전 §4 — mono/자간 미사용). 기존 20+ golden·900+ 회귀 바이트 불변(additive), 신규 web-card golden 2종 + 수집기/card_scaffold/렌더 회귀. 범위밖=483(이 렌더 재사용·소스 URL 갱신 선결). |
| 2026-07-02 | **§17 신설 — 소스별 상세보기 확장 + 요약카드 보강**: §15 WL 자산을 확장. Phase A(MFDS 행정처분 상세보기+분석층 — `disposition_basis` ②섹션·한국법령 D2·`resolve_required_sections`·`ENABLE_MFDS_ADMIN_BODY_FULL`·한글 섹션명)·Phase B(Federal Register 결정론 상세보기 — **v1.62 철회: FR=요약보강**, `deterministic_detail` type=`fr_summary` 제거)·Phase C(검사·실사 결과 PDF 링크 승격 — `_dual_links` who-inspection→pdf_url·gmp download_url, 신규 블록 없음)·Phase D(UI 보강 — 미리보기 태그·신뢰 라벨·해석 뱃지). 기본 OFF/샘플브리프 부재 → 커밋 골든 additive(guidance_fr.webcard·brief_web·who_inspection 계열만). 1075 green. 6슬롯 §0~§14·§15 WL 경로 불변. |
| 2026-07-01 | **§2.5 확정 반영 — 콘텐츠 경계·시각위계**: MINO 피드백(상세분석 부실·헤더 위계 부족·표↔핵심사실 중복) 대응. ① `overview` 삭제(표·핵심사실과 중복 — 규제 프레이밍은 6슬롯 summary(W1) 흡수) ② `required_remediation` 문자열→`{deadline, items[]}` 객체(마감기한 한 줄+체크리스트, 단일 ✓ 마커) ③ 4섹션 헤더 원형 번호 배지(①②③④, CSS 원+숫자)+세리프 소제목(위계) ④ Key Violations 위반 항목별 카드 블록(조항 코드배지·설명·경고색 리스크) ⑤ deep 카드 W2 facts 2×2 그리드(값 불변, 마크업/CSS만). `verify_deep_analysis` D1 이 `required_remediation` 객체 구조·`items` 비어있지 않음까지 검사. 기존 golden 20+ 불변(deep 렌더는 populated deep_analysis 카드에만 나타나 기존 fixture 무영향)·신규 회귀 +3(remediation 구조). |
| 2026-07-01 | **§15 신설(additive) — deep_analysis 7번째·선택 슬롯**: 사용자 피드백("카드가 조잡") 대응. Warning Letter 카드 한정, 전문 확보(`ENABLE_WL_BODY_FULL`) 카드만 카드별 fan-out(독립 호출 1건=카드 1건)으로 5섹션(Overview·Key Violations & Risk Analysis·FDA's Evaluation of Response·Required Remediation·Administrative Risks) 심층분석 생성. `verify_deep_analysis.py`(신규, 조항 인용 근거대조 게이트) 통과 카드만 병합, FAIL 카드는 6슬롯만으로 발행(카드 단위 graceful degrade). `card.html` 에 단계적 노출(`<details class="block deep">`, 기본 접힘) 추가. §0~§14(6슬롯 동결)는 완전 불변 — golden 20+·8xx 회귀 무변화, 신규 회귀 4개 테스트 파일 추가. 실제 FDA Huons Co., Ltd. 경고서한 원문으로 end-to-end 검증(수집→검증→병합→렌더) |
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
