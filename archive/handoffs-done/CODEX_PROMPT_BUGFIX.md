# Codex 의뢰 — GRM 수집기 버그 픽스 5건 (묶음 A)

> **대상 저장소**: `MINHOYEOM/grm-api-intake`
> **대상 파일**: `collect_intake.py`, `.github/workflows/grm-intake.yml`
> **우선순위**: critical 2건 + should-fix 3건. 모두 독립적으로 적용 가능.
> **요청 방식**: 아래 5개 변경을 한 PR로 묶어 주세요. 커밋은 항목별로 분리.

---

## Fix 1 — shell injection 방어 (grm-intake.yml)

**위치**: `.github/workflows/grm-intake.yml` L66

**현재 코드**:
```yaml
if [ -n "${{ inputs.window_days }}" ]; then ARGS="$ARGS --window-days ${{ inputs.window_days }}"; fi
```

**문제**: `${{ inputs.window_days }}`가 따옴표 없이 shell 문자열에 삽입됨. `workflow_dispatch` 권한 있는 사용자가 `; malicious_cmd`를 주입할 수 있음.

**수정 후**:
```yaml
if [ -n "${{ inputs.window_days }}" ]; then ARGS="$ARGS --window-days ${{ inputs.window_days }}"; fi
```
→ 아래로 교체:
```yaml
WINDOW_DAYS="${{ inputs.window_days }}"
if [ -n "$WINDOW_DAYS" ]; then ARGS="$ARGS --window-days $WINDOW_DAYS"; fi
```

같은 패턴으로 `dry_run` 라인도 점검해 비슷한 취약점 없는지 확인 후 필요 시 동일 수정.

**검증**: `window_days`에 `7; echo pwned` 입력 시 `argparse type=int`에서 거부되어야 함.

---

## Fix 2 — Notion API 429 재시도 (collect_intake.py)

**위치**: `collect_intake.py` L551–568 (`notion_create_page` 함수)

**문제**: `requests.post()`에 재시도 로직이 없어 Notion API가 429(rate limit)를 반환하면 해당 row가 무음 누락됨. `http_get_json`에는 retry가 있지만 POST 경로에는 없음.

**현재 코드**:
```python
def notion_create_page(token: str, db_id: str, item: IntakeItem,
                       run_date: date, collected_at: datetime) -> bool:
    body = { ... }
    try:
        resp = requests.post(NOTION_PAGES_URL, json=body,
                             headers=notion_headers(token), timeout=30)
        if resp.status_code >= 400:
            log("ERROR", f"Notion 페이지 생성 실패 ({resp.status_code}) "
                        f"doc={item.document_id} body={resp.text[:300]}")
            return False
        return True
    except requests.RequestException as e:
        log("ERROR", f"Notion 페이지 생성 예외 doc={item.document_id} err={e}")
        return False
```

**수정 방향**:
- `requests.post` 호출을 최대 3회 재시도 (`retries=2`)
- HTTP 429 응답 시 `Retry-After` 헤더(초 단위)를 읽어 `time.sleep()`. 헤더 없으면 지수 백오프(`2 ** attempt`초)
- 500/502/503/504는 재시도. 400/401/403/404는 즉시 실패(재시도 불필요)
- 기존 `http_get_json`의 retry 패턴(`for attempt in range(retries + 1)`)과 일관된 스타일로 작성

**추가**: `insert_items` (L576) 루프 안에 삽입 간 최소 0.33초 지연 추가 (`time.sleep(0.33)`). Notion 무료 플랜 rate limit(3 req/s)에 맞춤.

---

## Fix 3 — FR pagination 안전 중단을 error로 집계 (collect_intake.py)

**위치**: `collect_intake.py` L265–280 (`collect_federal_register` 함수 내 while 루프)

**문제**: `page_count > 10`에서 안전 중단 시 `WARN` 로그만 출력하고 `fr_error`를 세우지 않음. Job Summary에 ✓로 표시되어 100건+ 누락이 운영자에게 불투명.

**현재 코드**:
```python
if page_count > 10:
    log("WARN", "FR pagination 10 페이지 초과 — 안전 중단")
    break
```

**수정 후**: 함수 반환값을 이용해 truncation 사실을 `main()`에 전달.

```python
if page_count > 10:
    msg = f"FR pagination 10페이지 상한 초과 — truncated (수집 {len(items)}건, 이후 누락 가능)"
    log("WARN", msg)
    return items, msg   # error_msg 로 반환 → stats.fr_error = True 처리
```

`main()`에서 `fr_err` 처리 시 이미 `stats.fr_error = True`로 세우므로 추가 변경 불필요.

GITHUB_STEP_SUMMARY에서 `fr_error`가 `True`인 경우 ⚠️ 접두어를 붙여주세요:
```
⚠️ Federal Register: fetched N · inserted N · skip-dup N · error `FR pagination ...`
```

---

## Fix 4 — insert 실패 카운트 집계 (collect_intake.py)

**위치**: `collect_intake.py` L143–162 (`CollectionStats`), L576–600 (`insert_items`)

**문제**: Notion 페이지 생성 실패 시 `WARN` 로그만 찍히고 카운터에 집계 안 됨. KPI `저장률 = fetched 대비 100%` 검증 불가.

**수정 1 — CollectionStats에 필드 추가** (L143):
```python
@dataclass
class CollectionStats:
    fr_fetched: int = 0
    fr_inserted: int = 0
    fr_skipped_dup: int = 0
    fr_insert_failed: int = 0   # ← 신규
    fr_error: bool = False
    fr_error_msg: str = ""
    recall_fetched: int = 0
    recall_inserted: int = 0
    recall_skipped_dup: int = 0
    recall_insert_failed: int = 0  # ← 신규
    recall_error: bool = False
    recall_error_msg: str = ""
```

**수정 2 — insert_items 반환값 확장** (L576):
현재 `return inserted, skipped` → `return inserted, skipped, failed` (failed 카운터 추가).
실패 시 `failed += 1` 누적.

**수정 3 — main()에서 집계 후 summary 출력**:
`stats.fr_insert_failed`, `stats.recall_insert_failed` 반영.
GITHUB_STEP_SUMMARY에 `failed={N}` 컬럼 추가.
`insert_failed > 0`이면 STEP_SUMMARY에 ⚠️ 표시 (workflow exit 코드는 현행 유지 — graceful degradation 정책).

---

## Fix 5 — cron 2시간 앞당기기 (grm-intake.yml)

**위치**: `.github/workflows/grm-intake.yml` L11

**문제**: GitHub Actions cron은 부하 시 최대 60분 지연 시작이 공식 문서상 명시됨. 현재 `0 22 * * 0` = 월요일 07:00 KST. Claude Routine이 07:30 KST에 실행하므로 30분 여유는 지연 발생 시 부족.

**수정**:
```yaml
# 변경 전
- cron: '0 22 * * 0'   # 일요일 22:00 UTC = 월요일 07:00 KST

# 변경 후
- cron: '0 20 * * 0'   # 일요일 20:00 UTC = 월요일 05:00 KST (2.5시간 여유)
```

파일 상단 주석도 함께 갱신:
```yaml
# 매주 일요일 20:00 UTC = 월요일 05:00 KST
# Claude Code Routine (월 07:30 KST) 2.5시간 전에 수집 완료 목표
```

---

## PR 메시지 제안

```
fix(reliability): Notion 429 retry, insert fail count, FR truncation error, cron offset, shell quote

- [Fix 1] grm-intake.yml: window_days shell injection 방어 (따옴표 + 변수화)
- [Fix 2] notion_create_page: 429 Retry-After 재시도 + 삽입 간 0.33s 지연
- [Fix 3] collect_federal_register: pagination 상한 초과 시 fr_error 세팅 + STEP_SUMMARY ⚠️
- [Fix 4] CollectionStats: fr/recall_insert_failed 카운터 추가, summary 반영
- [Fix 5] cron: 22:00→20:00 UTC (여유 30분→2.5시간)

KPI "저장률 100%" 검증 가능해지고, 대량 수집 주 silent 누락 방지.
```

---

## Codex 검토 기준

- `notion_create_page` 재시도 로직이 `http_get_json`과 스타일 일관성 유지하는지
- `insert_items` 시그니처 변경 후 `main()`의 호출부 모두 업데이트됐는지
- `collect_federal_register` 반환 타입 변경 없이 현행 `tuple[list[IntakeItem], str | None]` 유지하는지
- dry-run 모드에서 새 카운터가 정상 집계되는지
