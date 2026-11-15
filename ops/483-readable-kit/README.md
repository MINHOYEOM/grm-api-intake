# 483 상세 국문 재번역 키트 — 티로(개인PC) 몫

이 브랜치(`ops/483-readable-tiro-kit`)는 **머지하지 않는다.** 작업 도구와 대기열만 나르는 용도다.

## 나눈 몫 (겹치지 않음)
- 해밀턴(회사PC): X-0009 ~ X-0017
- **티로(개인PC): X-0018 ~ X-0025** — 대기열 `queue/X-0018.json` ~ `queue/X-0025.json`
  (각 150행 이하, 항목 = finding_id · finding_text_md5 · detail_text(11-15 01시 운영값) · old_detail_text_ko · document_id)

## 준비
1. `git fetch origin && git worktree add ../_wt-483-tiro origin/ops/483-readable-tiro-kit` (격리 worktree, `git add -A` 금지)
2. Codex CLI 경로: 자동으로 `C:\Program Files\WindowsApps\OpenAI.Codex_*\app\resources\codex.exe` 를 찾는다. 다르면 환경변수 `CODEX_EXE`.
3. 지시문 = `BRIEF.md`(번역 지침 정본 docs/specs/483_detail_translation_brief.md 의 작업용 사본) + `run_inline.py` 안의 추가 규칙.
   Codex 는 도구·셸 없이 답 JSON 만 낸다(회사PC 는 샌드박스 셸이 막혀서 이렇게 했다. 개인PC 도 같은 방식이면 충분).

## 한 배치
```
cd ops/483-readable-kit
bash run_batch.sh X-0018        # 3조각 동시: 첫 번역 → 수정 2회 → kept_tokens 재작업 → 수정 1회. 끝에 logs/X-0018.summary
```
- 한도에 걸리면 summary 마지막 줄에 Codex 문구("try again at …")가 남는다. 그 배치는 멈추고 시각 이후 다시.

## 조립·검증
```
python assemble.py X-0018 <저장소 루트 경로>          # translations/detail_outbox/X-0018.json + report-X-0018.json
python review_shrunk.py X-0018                       # 옛 국문 대비 0.55 미만으로 줄어든 행: 영문 끝과 국문 끝을 사람이 대조
python assemble.py X-0018 <저장소 루트 경로> "<끝까지 다 옮긴 shrunk id,…>"   # 확인한 것만 넣어 다시 조립
python precheck.py X-0018                            # 영어 원문 md5 가 운영 DB 와 전부 같은지 + REST 2초 이내. STOP 이면 PR 만들지 말 것
```
판정 기준 (어기면 그 행은 배치에서 뺀다 = 옛 국문 유지):
- **영어 원문 불변**: detail_text 는 대기열 값 그대로(적용기가 함께 쓴다). precheck `en_differs 0`.
- **나빠진 행 0**: assemble 이 strict 실격·옛 국문에 있던 숫자 소실(regress_numbers)·shrunk 를 자동 보류.
- **strict·ocr_dropped**: `findings_detail_qa.py --strict` 기준. 깨진 토큰은 ocr_dropped(≤10, 원문에 있는 것만), 실제 이름만 kept_tokens.
- **kept_tokens 남용 금지**: l0mL·Sa1rncenia·2O24 같은 단위·낱말·날짜 오독을 '이름'으로 신고한 행은 빼거나 다시 맡긴다. 문서·장비·배치 번호는 원문 그대로 둬도 된다.
- 깨진 토큰이 많아 10개를 넘는 행은 보류.

## PR
- 저장소 루트에서 **origin/main 기준 새 브랜치** `data/483-readable-X-0018` → `translations/detail_outbox/X-0018.json` 한 파일만 `git add` → 커밋 → push → `gh pr create`
  (제목 예: `data(483): 상세 국문 재번역 X-0018 (N행)`, 본문에 행 수·보류 수·en_differs 0·shrunk 대조 결과)
- **티로는 PR 까지만.** 머지와 DB 반영(머지 시 grm-findings-detail-apply 가 자동 실행)은 해밀턴 쪽 483-readable 세션이
  CI 초록 + `--match-head-commit` 로 **한 번에 하나씩**, 반영 사이 REST 2초 확인, 실패 시 재시도 없이 멈춤,
  23:30~익일 01시(임베딩 적재)·월요일 08~10시(수집·발행)에는 반영하지 않는다.
- PR 을 만들면 공유 노트 progress 에 한 줄(배치·PR 번호·행 수·보류 수).
