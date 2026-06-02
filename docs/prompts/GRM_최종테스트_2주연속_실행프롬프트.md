# GRM 최종 테스트 — 2주 연속 브리프 (Codex/Claude Code 실행 프롬프트)

> 목적: 운영 전 마지막 통합 검증. 깨끗한 상태에서 **2주치 연속 브리프**를 만들어 (1)데이터 수합 정확성 (2)분석 품질 (3)포맷 (4)교차 주차 중복 방지를 한 번에 확인.
> 시나리오: **05-18~05-24 수집 → 2026-05-25(월) 브리프** / **05-25~05-31 수집 → 2026-06-01(월) 브리프**.
> 실행: Notion 쓰기 + GitHub Secrets 필요 → Codex/Claude Code. 결과는 Claude(GRM 점검 담당)에게 공유.

---

## ⚠️ 가장 중요한 설계 포인트 — Run Date 시뮬레이션

Routine/handoff의 윈도우 필터는 **게시일(publication date)이 아니라 Notion `Run Date (KST)` 속성**을 본다.
따라서 "5/18~5/24 정보를 5/25에 브리핑"을 재현하려면:

- **Week1 batch:** 게시일이 2026-05-18~05-24인 항목을 수집 → 그 row들의 `Run Date (KST)` = **2026-05-25**로 설정.
- **Week2 batch:** 게시일이 2026-05-25~05-31인 항목을 수집 → 그 row들의 `Run Date (KST)` = **2026-06-01**로 설정.

수집기에 `--run-date` 류 오버라이드가 있으면 그걸 쓰고, 없으면 수집 후 Notion API PATCH로 Run Date를 일괄 설정한다.
(handoff 윈도우는 `[run_date-7, run_date]`이므로 Run Date를 브리프 날짜로 박으면 정확히 잡힌다.)

---

## STEP 0 — 클린 슬레이트 (사용자 승인 하에 삭제)

GRM API Intake DB와 GRM Weekly Brief DB를 테스트용으로 비운다. **단 구조·스키마·운영 설정은 보존.**

- **삭제 대상:** Intake DB의 기존 규제항목 row 전부, 이전 handoff row(`OPEN/CONSUMED GRM Routine Handoff *`), `[TEST]` 라벨 브리프, 06-01·05-31 기존 브리프.
- **보존 대상:** DB 자체·스키마(Site Country 등 필드)·뷰·워크플로 설정·시크릿. `GRM_Prompt_v15.6.md`(v15.6.3) 본문.
- ⚠️ 영구 삭제는 되돌릴 수 없으니, 미심쩍으면 archive(휴지통)로.

확인:
- [ ] Intake DB에 규제항목 row 0건(handoff 포함), Weekly Brief DB에 테스트 브리프 0건
- [ ] 스키마·필드·워크플로 그대로

---

## STEP A — Week1 (2026-05-25 브리프)

**A-1. 수집 (게시일 05-18~05-24)**
```
py -3 collect_intake.py --sources mfds,fr,recall,wl,ema --run-date 2026-05-25 --window-days 7 --emit-routine-handoff
```
(또는: 정상 수집 후 게시일 05-18~05-24 항목만 남기고 Run Date=2026-05-25로 PATCH)

확인:
- [ ] 수집된 항목의 게시일이 모두 05-18~05-24 범위
- [ ] 각 row `Run Date (KST)` = 2026-05-25
- [ ] handoff page `OPEN GRM Routine Handoff 2026-05-25` 생성, `row_count=N1`
- [ ] Job Summary `Routine handoff: N1 New rows`

**A-2. Routine 1회차 (run_date=2026-05-25)**
`GRM_Prompt_v15.6.md`(v15.6.3) 전문으로 실행.

확인:
- [ ] 브리프 제목 `GRM Weekly Brief — 2026-05-25 (월)`, 검색기간 `05-18 ~ 05-25`
- [ ] handoff N1건 카드화
- [ ] **handoff → `CONSUMED GRM Routine Handoff 2026-05-25` / Status=Processed**
- [ ] 처리된 원본 row Status=Processed

---

## STEP B — Week2 (2026-06-01 브리프)

**B-1. 수집 (게시일 05-25~05-31)**
```
py -3 collect_intake.py --sources mfds,fr,recall,wl,ema --run-date 2026-06-01 --window-days 7 --emit-routine-handoff
```
(또는: 게시일 05-25~05-31 항목만, Run Date=2026-06-01로 PATCH)

확인:
- [ ] 수집된 항목 게시일 05-25~05-31
- [ ] 각 row `Run Date (KST)` = 2026-06-01
- [ ] handoff `OPEN GRM Routine Handoff 2026-06-01` 생성, `row_count=N2`
- [ ] **★ Week1에서 Processed된 항목이 이 handoff에 포함되지 않음**(Status=New 필터 작동)

**B-2. Routine 2회차 (run_date=2026-06-01)**
동일 프롬프트로 실행.

확인:
- [ ] 브리프 제목 `GRM Weekly Brief — 2026-06-01 (월)`, 검색기간 `05-25 ~ 06-01`
- [ ] handoff N2건 카드화
- [ ] handoff → CONSUMED/Processed
- [ ] **★★ 핵심: Week1 브리프에 나온 항목이 Week2 브리프에 재등장하지 않음** (교차 주차 중복 0)

---

## STEP C — 경계/엣지 확인 (선택이지만 권장)

- [ ] **05-25 경계:** 게시일 05-25 항목이 양쪽 윈도우에 다 걸릴 수 있음. Run Date를 한쪽(Week2=06-01)에만 배정해 한 번만 나오는지 확인.
- [ ] **빈 윈도우:** 만약 한 주차 수집이 0건이면 "특이사항 없음" 빈 브리프가 정상 생성되는지.
- [ ] **비제조 항목:** 임상시험 단독 행정처분(예: 맹검 해제) 같은 비제조 건은 Routine이 국내 섹션에서 Skip/강등하는지.

---

## 결과 보고 양식 (Claude에게 줄 것)

다음을 주세요:

1. **Week1 브리프** 링크 (또는 본문)
2. **Week2 브리프** 링크 (또는 본문)
3. **두 handoff** 최종 상태 (제목 OPEN/CONSUMED + Status + 각 row_count N1·N2)
4. **수집 로그 요약** — 각 주차 소스별 건수(MFDS admin/recall/gmp·FR·WL·EMA), dedupe 전후
5. **교차 중복 점검** — Week1 카드 document_id 목록 vs Week2 카드 document_id 목록 (겹치는 게 있나)
6. 발견된 이슈(윈도우 경계·Run Date 오설정·수집 누락 등)

---

## Claude가 검증할 항목 (참고 — 이 기준으로 판정함)

**데이터 정확성**
- 각 주차 항목 게시일이 윈도우 내 / 수집 누락·중복 없음 / dedupe 정상

**분석 품질**
- Type→테마 매핑 정확 (admin→행정처분, gmp-inspection→GMP실사 등)
- 소재국: `Site Country` 우선, 빈 경우 Body `국가:` fallback → 해외 제조소(프랑스·캐나다 등) 정확
- gmp-inspection 지적사항: 분야+failure mode 요약, present/none 구분, 결론(적합/보완/부적합)
- KO 한글 유지 + quote 1~3줄 + 전문은 toggle
- 듀얼 링크(Q4): 행정처분 `CCBAO01/getItem?dispsApplySeq=`, GMP실사 PDF 직링크
- Signal Tier 정렬, Self-Check 서술 없음

**포맷**
- 🇰🇷국내 / 🌐글로벌 2단 섹션, 한눈에 표 컬럼(소재국 포함)
- 카드 포맷 일관성 → **단 Q5(카드 포맷 표준)는 미결이라, 현재 포맷이 그대로 나오는지만 확인하고 개선점은 Q5 세션으로**

**중복 방지 (이번 테스트의 핵심)**
- Week1 카드 항목이 Week2에 재등장 0
- 두 handoff 각각 CONSUMED 전환
- broad fallback 미실행
- 05-25 경계 항목 한 번만 출현

**운영**
- 빈 윈도우 graceful (특이사항 없음)
- 비제조 항목 적절 처리
