# Phase 2 개선 작업 핸드오프

> 이 문서는 v15.0 Phase 1 완료 직후 작성됐다. Phase 2 개선 작업 5건을 새 Claude 세션에서 진행하기 위한 자체완결 컨텍스트.
>
> **새 채팅 사용법**: Cowork 모드 새 세션에서 이 파일을 첨부하거나 첫 메시지에서 경로를 참조한 뒤, "Phase 2 핸드오프를 읽고 권장 순서를 제안해 달라" 고 요청.

---

## 1. 프로젝트 정체성

**이름**: GRM (Global Regulatory Monitor) — 글로벌 GMP/QA 규제 변동 주간 다이제스트

**사용자**: 한국 제약회사 QA 담당자 (단일 사용자). 경구 고형제 (oral solid dosage — 정제) 중심 회사. 한국 규제는 사내 RA 가 별도 담당하므로 이 도구는 **글로벌만** 다룬다 (FDA · EMA · PIC/S · ICH · TGA · MHRA · Health Canada · PMDA · HSA).

**현재 컨텍스트**: 호주 TGA 실사 임박으로 TGA 검색 가중치 임시 적용 중. 실사 종료 후 Deep Dive 로 이동 예정.

**범위 외**: 임상시험·임상약리·API 단독·백신/세포/유전자치료제·의료기기·화장품·식품.

---

## 2. Phase 1 완료 상태 (Baseline)

### 인프라

| 자산 | 식별자 / 경로 |
|---|---|
| GitHub repo (public) | https://github.com/MINHOYEOM/grm-api-intake |
| Notion 다이제스트 DB (기존) | `3653142f-dc11-8049-806d-e0a779cafd90` (🌐 GRM Weekly Brief) |
| Notion Intake DB (Phase 1 신규) | `7784c71fb7b343749b2bee5d04db7926` (GRM API Intake) |
| Notion data source | `collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288` |
| GitHub Actions 자동 실행 | 매주 일요일 22:07 UTC = 월요일 07:07 KST (cron `7 22 * * 0`) |
| Claude Routine 자동 실행 | 매주 월요일 07:30 KST (수동 설정) |
| 다음 첫 v15.0 자동 실행 | 2026-06-01 (월) 07:07 KST (수집) + 07:30 KST (다이제스트) |

### 로컬 작업 폴더

`C:\Users\user\Desktop\Global Regulatory Monitor\v15.0-implementation\`

주요 파일:

| 파일 | 역할 |
|---|---|
| `collect_intake.py` | Python 수집기 (~700 줄) — FR + OpenFDA API → Notion |
| `.github/workflows/grm-intake.yml` | GitHub Actions workflow |
| `GRM_Prompt_v15.0.md` | Claude Routine 프롬프트 (~36KB) |
| `notion_intake_db_schema.md` | Intake DB 16-property 스키마 가이드 |
| `setup.ps1` · `setup.sh` | 1회용 셋업 자동화 (이미 실행됨) |
| `README.md` · `setup_guide.md` | 사용자 가이드 |
| `CODEX_REVIEW_RESPONSE.md` | Pass 1·2·3 audit trail (19 finding) |
| `PROMPT_REVIEW_PASS_1.md` | self-review 기록 (F1-F6) |

### Phase 1 가 해결한 것 (실증 기준)

| 영역 | Before (v14.5) | After (v15.0) | 첫 실행 (2026-05-26) |
|---|---|---|---|
| FDA Federal Register 캡처율 | ~50% | 100% (API 전수) | 12 / 12 ✓ |
| FDA 의약품 회수 캡처율 | **0%** | 100% (API 전수) | 3 / 3 ✓ |
| Evidence A | 사실상 불가 | Intake-direct 부활 | 15 row 모두 후보 |
| 보안 | 미적용 (호출 실패) | API key masking · fail-closed dedup · ProcessStartInfo · workflow input validation | Codex 3 round 통과 |
| graceful degradation | API 403 → WebSearch 단독 | 3 단계 (Intake 0건 / notion-fetch 부분 실패 / 전체 실패) 모두 fallback | 검증됨 |

**실증된 가치**: 첫 실행에서 `Recall D-0547-2026 Ascend Laboratories — Metoprolol Succinate ER Tablets — Failed Dissolution Specifications` 캡처. v14.5 가 100% 놓쳤던 경구 고형제 QA-critical 사례.

### Codex 검토 이력 (요약)

- **Pass 1** (baseline `95d212e`): must-fix x3 + should-fix x7 + Q1-Q5 모두 반영
- **Pass 2** (baseline `8e3d7c7`): `gh secret set --body-file` 호환성 문제 → ProcessStartInfo + RedirectStandardInput 로 교정
- **Pass 3** (baseline `5531bac`): 프롬프트 self-review 6 갭 + Notion MCP 한계 처리 + 8 should-fix + 1 nice-to-have 모두 반영

남은 backlog (Phase 2+ 후보로 분리됨):
- `Source` 를 dedupe key 에 포함 (scale 100+ rows/run 시)
- pytest 테스트 suite 구축
- Phase 2 architecture 트리거 (50 rows/run 2주 연속 또는 단일 run 100+ 시)

---

## 3. Phase 2 개선 항목 5가지

직전 세션에서 사용자가 제시한 항목들. 각 항목 별 상세 spec + 권장 시기 + 작업량 + 의존성.

### #4 Recall 카드 우선순위 개선 ⭐ (즉시 시행 권장)

**의도**: 다이제스트에서 의약품 회수 (recall) 카드를 안전 중요도 + OSD 직접 관련성으로 정렬해 사용자 학습 효율 향상.

**현재 상태**:
- 카드 순서가 D-Day 임박순. Recall 은 🟧 (Warning Letter 와 동색).
- 첫 실행에서 Ascend dissolution (Class II, ORAL) 이 행정 공지 FR 과 동급 표시됨.

**제안 변경** (Phase 1 마무리 차원에서 즉시):

1. **Class I recall 무조건 최상단** (생명 위협 — 안전 critical)
2. **Recall 전용 색 prefix `🔴`** (Warning Letter 🟧 와 시각 구분)
3. **Class II/III + ORAL (정제·캡슐) → TL;DR 헤드라인 우선 포함**

**변경 파일**:
- `GRM_Prompt_v15.0.md`:
  - 새 섹션: `[정렬 규칙 — v15.1]` 추가 (또는 기존 [H3 카테고리 prefix] 섹션 확장)
  - 정렬 규칙 1줄: "Class I recall > Class II/III recall (ORAL 우선) > Warning Letter > Guidance > Other"
  - prefix 표에 🔴 추가
  - [TL;DR 헤드라인] 섹션에 "Class II/III recall + route=ORAL 우선 포함" 규칙

**작업량**: 15분 작성 + 1 commit + Routine 프롬프트 재교체 (사용자 작업 3분)

**의존성**: 없음. 단독 적용 가능.

**검증 방법**: 다음 월요일 자동 실행 다이제스트에서 Ascend recall 이 최상단 또는 TL;DR 에 등장하는지 확인.

**왜 즉시인가?**: Class I 처리는 안전 critical — 실증 데이터 없어도 정답. 미루면 한 달 다이제스트가 정렬 잘못된 상태로 누적됨.

---

### #5 OSD-specific signal 강화 (4주 후 권장)

**의도**: 경구 고형제 (정제·캡슐) 직접 관련 row 를 자동 분류해 사용자 학습 우선순위 가시화.

**기술 가능성**: OpenFDA raw payload 에 이미 다음 데이터 존재:
- `openfda.route: ["ORAL"]`
- `openfda.dosage_form: ["TABLET", "CAPSULE", "EXTENDED-RELEASE TABLET", ...]`
- `openfda.application_number: ["ANDA211143"]` (generic 여부)

**제안 변경**:

1. Notion Intake DB 에 `OSD Relevance` property 추가 (select: Direct / Indirect / N/A)
2. `collect_intake.py` 의 `_recall_to_item()` 에 OSD 분류 로직 추가 (~50줄)
3. `GRM_Prompt_v15.0.md` 에 OSD 배지 (🔷) 카드 마킹 규칙 1줄 추가

**변경 파일**:
- `collect_intake.py`: `_recall_to_item()` 확장, `PROP_OSD_RELEVANCE` 상수 추가
- `notion_intake_db_schema.md`: 속성 표에 `OSD Relevance` 추가
- `GRM_Prompt_v15.0.md`: 카드 마킹 규칙 추가

**작업량**: 약 2시간 + Codex 1 round 검토

**의존성**: #4 이후 (정렬은 안전 우선으로 이미 처리)

**왜 4주 후인가?**:
1. 첫 4주 데이터로 OSD 직접 관련 vs 무관 비율 baseline 확보. 80%+ 면 배지 무의미 (모든 카드 표시). 20% 정도면 매우 유용.
2. FR 의 OSD 직접성 판단 휴리스틱은 추측보단 Routine LLM 분류 결과와 비교 후 검증

**검증 방법**: 신규 4주간 적재된 row 의 `OSD Relevance` 분포 vs Routine 의 카드화 결과 일치도.

---

### #2 QA relevance scoring 정교화 (4주 후 권장)

**의도**: `compute_relevance()` 휴리스틱이 첫 실행 15/15 = Pending 이었음. ICH/CGMP 학술어만 매칭하고 실제 OpenFDA 어휘 (`dissolution`, `particulate matter`) 못 잡음.

**개선 후보 키워드** (확장 가능):
- recall 어휘: `dissolution`, `particulate`, `sterility`, `assay failure`, `impurity`, `subpotent`, `mislabel`, `endotoxin`
- impurity 영역: `ndma`, `nitrosamine`, `n-nitroso` (FDA hot topic)
- 외국 제조사: `india`, `alkem`, `aurobindo`, `lupin`, `dr reddy` (generic 경쟁사 학습 가치)

**변경 파일**:
- `collect_intake.py`: `QA_CATEGORY_KEYWORDS` 확장 (현재 상수 정의 부분 약 L80-L100)

**작업량**: 30분 키워드 추가 + 1주 A/B 비교

**의존성**: 운영 데이터 (사용자 사례 메모)

**왜 4주 후인가?**:
1. **사용자 의견이 가장 강력한 신호**. "이 row 는 Pending 인데 사실 Likely 였어야 함" 사례 4주 누적 → 데이터 기반 키워드 추가가 추측보다 정확
2. 키워드 추가는 한 번 합치면 되돌리기 어려움 (이미 처리된 row 라벨은 그대로)
3. 휴리스틱은 hint 일 뿐 Routine LLM 이 최종 분류 → **운영상 무해**. 급할 이유 없음.

**검증 방법**: 신규 키워드 적용 1주 vs 미적용 1주 row 의 QA Relevance 분포 비교. Routine 의 본문 카드화 정확도와 cross-confirm.

---

### #3 Notion review feedback loop (8주 후 권장)

**의도**: 사용자가 다이제스트 본 후 "useful / not-useful" 마킹 → 시스템 학습 자산.

**단계별 옵션**:

- **단계 A** (4주 후 가능, 저비용): 다이제스트 페이지에 row 별 ✅/❌ checkbox 컬럼. 사용자가 매주 30초 마킹. 운영자 가시화만, Routine 사용 안 함.
- **단계 B** (8주+ 후, 중비용): Intake DB 에 `User Feedback` property 추가. 다음 주 휴리스틱 자동 조정.
- **단계 C** (Phase 4+, 고비용): 자동 retraining loop. 매주 피드백 → 키워드 자동 추가 → 프롬프트 갱신.

**변경 파일** (단계 B 기준):
- `notion_intake_db_schema.md`: `User Feedback` property 추가
- `GRM_Prompt_v15.0.md`: Routine 이 다이제스트 카드 작성 시 해당 row 의 feedback property 도 반영하라는 규칙 추가
- 신규 collector module 또는 `collect_intake.py` 확장 — feedback 데이터 → 휴리스틱 가중치 변환

**작업량**: 단계 A = 1시간, 단계 B = 4-6시간

**의존성**: 4-8주 운영으로 도구 가치 자체 확신 후

**왜 8주인가?**:
1. **사용자 시간 부담 ROI 평가 필요**. 매주 30초 × 52주 = 연 26분
2. v15.0 Routine 이 raw payload 직접 보고 LLM 분류하므로 휴리스틱 정확도 ↓ 가 큰 손실 아님
3. 4주 실증 후 사용자가 "이 도구가 매주 마킹할 만큼 가치 있나?" 자문 후 결심

**검증 방법**: 단계 A 도입 후 4주간 사용자 마킹 일관성 (50%+ row 마킹) 확인 → 일관성 충족 시 단계 B 진행.

---

### #1 EMA / PIC/S / TGA 공식 수집 개선 (Phase 3+, 3개월 후)

**의도**: 현재 이 영역들은 WebSearch Core 8 (5,6,8) + Deep Dive 로만 캡처. API 가 있는 곳은 직접 수집으로 전환해 누락 위험 ↓.

**API 가용성 현실**:

| 기관 | API | 우선순위 |
|---|---|---|
| EMA | 공식 API 있음 (medicines, scientific guidelines) | high |
| Health Canada | 공식 API 있음 | high |
| TGA | consultations API 만, manufacturer info 는 scraping | medium |
| PIC/S · ICH · MHRA · PMDA · HSA | scraping 만 | low (페이지 변경 시 깨짐) |

**제안 변경** (우선 EMA + HC 만):
- 신규 `collect_ema.py` · `collect_hc.py` (각 ~100줄)
- 신규 workflow step 추가
- Notion Intake DB `Source` select 옵션 확장 (`EMA Guidelines`, `Health Canada GMP`)
- 또는 source-별 DB 분리 검토

**작업량**: EMA 만 = 4시간, EMA + HC = 8시간 + Codex 1-2 round 검토

**의존성**:
- v15.0 FDA-only Intake 안정 운영 **8주 이상**
- Routine 의 Notion query 처리량 < 50 rows/run (Codex Q1' 한계)
- 사용자가 "EMA 영역에서 명백한 누락 발생 사례" 1건 이상 실증

**왜 Phase 3+ 인가?**:
1. v15.0 의 검증된 가치 (FDA Recall 캡처) 는 이미 확보. 추가 source 의 marginal value 는 운영 데이터 없이 불명
2. TGA 실사 종료 후 우선순위 변동 가능
3. scraping 기반 collector 는 유지보수 부담 — Phase 1 의 단순함 (단일 의존성 `requests`) 깨짐
4. 새 source 추가 = Routine 프롬프트 대폭 수정

**Phase 3 진입 조건** (강하게 권장):
- 8주 안정 운영 ✓
- 50 rows/run 미만 ✓
- 실증 누락 1건 이상 ✓

---

## 4. 권장 진행 순서

| 시점 | 작업 | 사유 |
|---|---|---|
| **즉시 (Phase 1 마무리)** | #4 Recall 우선순위 (Class I 상단 + 🔴 prefix + ORAL TL;DR) | 안전 critical, 사용자 행동 변화 0, 다음 월요일 첫 운영부터 적용 |
| **운영 4주 차** (~ 2026-06-22) | #5 OSD signal + #2 휴리스틱 확장 동시 검토 | 4주 데이터로 baseline 확보 후 결정 |
| **운영 8주 차** (~ 2026-07-20) | #3 feedback loop 단계 A (간단 checkbox) | 도구 가치 확신 후 마킹 시작 |
| **운영 12주 차** (~ 2026-08-17) + 조건 충족 시 | #1 EMA + Health Canada API 만 추가 | TGA 실사 종료 + 50 rows/run 안정 + 실증 누락 사례 1건 이상 |
| **Phase 4+** | feedback loop B/C 자동 학습, ML 분류, 다중 source SaaS 검토 | 1년 누적 데이터 후 |

---

## 5. 메타 가이드 — 우선순위 결정 기준

v15.0 Phase 1 경험에서 추출한 4가지. 어떤 개선이든 결정 전 점검.

1. **실증 데이터 기반인가?** Phase 1 의 가치는 "Ascend dissolution 캡처" 같은 실증으로 검증됨. 추측 기반 우선순위는 risky.
2. **Codex Q1' 한계 (50 rows/run) 와 충돌하나?** 새 source 추가는 이 한계 고려 후. 현재 FDA only 15/run, EMA + HC 추가 시 30-40/run 도달.
3. **유지보수 부담이 비례하나?** scraping 기반 collector 는 페이지 변경마다 깨짐. API 기반 우선.
4. **사용자 시간 부담이 정당화되나?** feedback loop 매주 30초 = 연 26분. 학습 가치 vs 시간 부담 평가.

---

## 6. Phase 1 의 backlog (Codex review 에서 분리된 항목)

Phase 2+ 에서 자연스럽게 다룰 항목:

| backlog | 트리거 |
|---|---|
| `Source` 를 dedupe key 에 포함 | scale 100+ rows/run 시 |
| pytest 테스트 suite 구축 (Q5) | Phase 2 안정화 후 |
| 50+ rows/run 2주 연속 시 architecture 재설계 | KPI gate |

---

## 7. 다음 자동 실행 체크리스트 (Phase 2 시작 전 점검)

다음 월요일 (2026-06-01) 07:30 KST 첫 v15.0 다이제스트 받으면 확인:

| 체크 | 정상 기준 |
|---|---|
| Job Summary | `Federal Register: fetched N · inserted N · failed 0` |
| Notion Intake DB | 새 row 들 등장 (Run Date 2026-06-01) |
| 다이제스트 페이지 M2 메타 | `notion-search candidates · notion-fetch 성공 · accepted · discarded · Status 갱신 결과` 라인 등장 |
| Evidence A 카드 | Ascend 류 (Intake-direct) 카드 존재 |
| graceful degradation 표시 | Intake 정상이면 표시 없음 |

Phase 2 작업은 이 결과를 본 후 시작 권장.

---

## 8. 새 채팅 첫 메시지 예시

새 채팅에서 다음과 같이 시작:

```
v15.0 Phase 1 완료 직후입니다. Phase 2 개선 작업 5건을 진행하려고 합니다.

먼저 첨부한 `PHASE2_HANDOFF.md` (또는 경로:
C:\Users\user\Desktop\Global Regulatory Monitor\v15.0-implementation\PHASE2_HANDOFF.md)
를 읽고:

1. 현재 시스템 상태와 5개 항목 spec 을 이해
2. 권장 순서대로 어떤 작업부터 시작할지 제안
3. 첫 작업 (#4 Recall 카드 우선순위) 의 구체 변경 spec 검토

진행해 주세요.
```

또는 새 작업으로 바로 점프:

```
v15.0 Phase 1 완료 직후. PHASE2_HANDOFF.md 의 #4 Recall 카드 우선순위 개선을
지금 진행하고 싶습니다. 변경할 파일은 GRM_Prompt_v15.0.md 한 곳뿐.

먼저 현재 GRM_Prompt_v15.0.md 의 H3 카테고리 prefix 섹션과 TL;DR 규칙 부분을
읽고 정확한 변경 위치를 찾은 뒤 변경 plan 제시해 주세요.
```

---

## 9. 핸드오프 끝

이 문서로 새 채팅에서 충분히 컨텍스트 확보 가능. 추가 정보는 다음 파일들 직접 참조:

- 코드 세부: `collect_intake.py`
- 프롬프트 세부: `GRM_Prompt_v15.0.md`
- DB 스키마: `notion_intake_db_schema.md`
- 변경 이력: `CODEX_REVIEW_RESPONSE.md`, `PROMPT_REVIEW_PASS_1.md`

모두 같은 폴더 `C:\Users\user\Desktop\Global Regulatory Monitor\v15.0-implementation\` 에 존재.

작성일: 2026-05-26 KST (Phase 1 완료 시점)
