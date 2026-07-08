# FIND-1 M1h Internal Backfill Dry-Run

> 날짜: 2026-07-08
> 범위: 내부 백필 실행 전 dry-run plan/report 고정
> 금지: Notion API 조회, SQLite write, Supabase write, Status/handoff 변경

## 목적

M1h는 기존 Intake 축적분을 실제로 백필하기 전, 이미 확보한 Notion snapshot + raw payload export 파일들을 오프라인으로 묶어 다음을 확인한다.

- `raw_signals(grm-raw-signal/v1)` 전역 dedupe 결과
- `findings(grm-finding/v1)` 전역 dedupe 결과
- source/agency/review_status/evidence/category coverage
- raw payload 누락·invalid row 같은 blocking error
- 중복·findings 미생성 raw_signal 같은 review warning

## 실행 예시

```powershell
py findings_backfill.py `
  --manifest tests/fixtures/findings_m1h_backfill_manifest.json `
  --output findings_internal_backfill_dry_run.json `
  --pretty
```

생성 파일 `findings_internal_backfill_dry_run*.json`은 로컬 검증 산출물이므로 git 추적 제외한다.

## Manifest 계약

```json
{
  "batches": [
    {
      "name": "m1b-seed",
      "input": "findings_m1b_sample_export.json"
    }
  ]
}
```

`input` 경로는 manifest 파일 위치 기준 상대경로 또는 절대경로를 허용한다.

## Gate 해석

- `report.preflight.ready_for_sqlite_append_dry_run=true`: schema/store/extractor 관점에서 SQLite append dry-run으로 넘길 수 있다.
- `blocking_errors > 0`: raw 누락, invalid row/raw/finding, orphan finding 등으로 실제 write 금지.
- `review_warnings > 0`: 중복 또는 findings 미생성 raw_signal이 있어 검토 필요. 이는 dry-run 실패가 아니라 백필 품질 검토 큐다.

## 현재 샘플 고정값

`tests/fixtures/findings_m1h_backfill_manifest.json`는 M1b seed + M1f source coverage fixture를 묶는다.

- input rows: 7
- unique raw_signals: 6
- unique findings: 7
- duplicate raw_signals: 1
- duplicate findings: 1
- raw_signals_without_findings: 1 (`WHO::who-whopir-link-only`)
- blocking_errors: 0

## 2026-07-08 운영 7월 plan 결과

`findings_notion_export_2026_07.json` 기준:

- input rows: 112
- unique raw_signals: 112
- unique findings: 24
- duplicate raw_signals: 0
- duplicate findings: 0
- raw_signals_without_findings: 104
- findings_by_source: FDA 483 18, FDA Warning Letter 5, MFDS 1
- findings_by_evidence_level: A 18, B 6
- findings_by_review_status: accepted 18, needs_review 6
- blocking_errors: 0

## 다음 단계

운영 7월 plan은 M1i transaction dry-run과 M1j file apply까지 통과했다. Supabase 적재와 대시보드는 M2/M3 후속 단계다.
