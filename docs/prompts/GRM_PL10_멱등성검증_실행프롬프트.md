# PL-10 멱등성 검증 — Codex/Claude Code 실행 프롬프트

> 목적: v15.6.3 handoff-first 구조가 **중복 발행을 막는지**(멱등성) 라이브 검증.
> 이 작업은 Notion 쓰기 + GitHub Secrets가 필요해 Codex/Claude Code에서 실행. 결과는 Claude(GRM 점검 담당)에게 공유.
> 준비 완료: Intake DB에 테스트용 New row 5건 복원됨(아래 목록). 새 수집 run 없이 바로 1회차부터 데이터 잡힘.

---

## 사전 상태 (이미 세팅됨)

Intake DB(`collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288`)에 `Status=New` 5건 복원:
- `admin-2026003441` (주)대웅제약 — Tier 3
- `admin-2026003654` (주)팜젠사이언스 — Tier 3
- `admin-2026003474` (주)바이넥스 — Tier 3
- `gmpinspect-1PutNZjPQaP` (주)엘앤씨바이오 — GMP실사 국내, 지적 present, Tier 3
- `gmpinspect-1PufDyaR_-p` Bora Pharmaceutical (캐나다) — GMP실사 해외, Tier 2

나머지 MFDS row는 Processed 상태 유지(잡히면 안 됨 — 멱등성 확인용).

---

## 실행 순서

### STEP 1 — 수집기 handoff 생성 (non-dry run)

```
py -3 collect_intake.py --sources mfds --window-days 7 --emit-routine-handoff
```
(또는 GitHub Actions non-dry run 트리거)

**확인:**
- [ ] Job Summary / 로그에 `Routine handoff: N New rows` (N=5 기대) 출력
- [ ] Intake DB에 `OPEN GRM Routine Handoff 2026-06-01` page 생성됨 (Source=`GRM Handoff`, Type=`routine-handoff`)
- [ ] 그 page 본문 JSON code block(`grm-routine-handoff/v1`)에 위 5건의 page_id/document_id 포함, `row_count=5`

### STEP 2 — Routine v15.6.3 **1회차** 실행

GRM_Prompt_v15.6.md(v15.6.3) 전문을 Routine에 넣고 실행.

**확인 (1회차 통과 조건):**
- [ ] 풀 브리프 생성 — 국내(MFDS) 카드 5건(또는 Skip 제외분) 정상
- [ ] **★ 핵심: handoff page Title이 `CONSUMED GRM Routine Handoff 2026-06-01`로, Status가 `Processed`로 전환됨** ← 멱등성의 결정적 지점
- [ ] 카드의 듀얼 링크(📎 MFDS 행정처분 상세 / 실사 PDF) 정상 출력
- [ ] M2에 handoff 소비 기록

### STEP 3 — Routine v15.6.3 **2회차** 실행 (같은 날, 같은 프롬프트)

STEP 1의 수집기를 다시 돌리지 말고, Routine만 한 번 더 실행.

**확인 (2회차 통과 조건):**
- [ ] **★ 핵심: "특이사항 없음 / Intake 0건" 빈 브리프** (1회차와 같은 카드 재생성 안 됨)
- [ ] M2에 `Routine handoff already consumed — duplicate run suppressed` 로그
- [ ] **원본 Intake DB broad search(notion-search + created_date_range)를 수행하지 않음** — Processed row 재카드화 없음

### STEP 4 (선택) — 안전망 테스트

1회차가 handoff를 CONSUMED로 못 바꿨다고 가정하고 싶으면: handoff를 수동으로 `OPEN`/New로 되돌린 뒤 2회차 실행 → 그래도 broad fallback이 막혀 원본 DB Processed를 안 긁는지 확인(L206 "legacy broad fallback suppressed").

---

## Claude에게 공유할 결과

다음 4가지를 주세요:
1. **1회차 브리프** 페이지 링크 (또는 본문)
2. **2회차 브리프** 페이지 링크 (또는 본문)
3. **handoff page** 최종 상태 — Title(`OPEN` vs `CONSUMED`) + Status
4. STEP 1 Job Summary의 `Routine handoff: N New rows` 값

Claude가 아래 표로 판정:

| 검증 | 통과 조건 |
|---|---|
| 1회차 | handoff 5건 → 풀 브리프 + handoff CONSUMED/Processed 전환 |
| 2회차 | 빈 브리프 + broad fallback 안 함 + "duplicate run suppressed" 로그 |
| 안전망 | 1회차가 전환 깜빡해도 2회차가 중복 안 냄 |

3개 다 통과 → **PL-10 닫힘, 정기 발행 차단 해제.**

---

## 검증 후 정리 (Claude가 수행)

- 복원했던 New 5건 → 다시 Processed로 정리(또는 1회차가 자연 Processed 처리).
- 1·2회차 테스트 브리프 → `[TEST]` 라벨.
- punch-list PL-10 상태 갱신.
