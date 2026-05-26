# Copilot 검토 요청 — 1회용 실행 가이드

`.github/copilot-instructions.md` 와 `REVIEW.md` 두 파일이 작성됐습니다. 이제 이걸 GitHub 에 올리고 Copilot 을 reviewer 로 지정하면 됩니다.

## 사전 요구사항

- **GitHub Copilot 구독** (Individual / Business / Enterprise 중 하나) — 활성화 여부는 <https://github.com/settings/copilot> 에서 확인.
- **Copilot code review 사용 가능 여부** — 일반적으로 모든 구독에 포함되지만, Enterprise 조직은 admin 이 켜야 합니다. 본인 계정이라면 그냥 됩니다.

확인 명령:

```powershell
gh api /user/copilot/billing 2>$null
# 또는 브라우저에서 https://github.com/settings/copilot 확인
```

## 권장 워크플로 — PR 기반 (Copilot reviewer 지정 가능)

main 에 직접 commit 하지 않고 **새 브랜치 → PR → Copilot reviewer 지정** 순서로 진행하면 Copilot 의 inline 코드 코멘트를 PR 에서 받을 수 있습니다.

### PowerShell 한 번 실행

```powershell
cd "C:\Users\user\Desktop\Global Regulatory Sweep\v15.0-implementation"

# 1. 새 브랜치 생성
git checkout -b docs/copilot-review-onboarding

# 2. 새 파일 2개 + 본 가이드 stage
git add .github/copilot-instructions.md REVIEW.md COPILOT_REVIEW_HOWTO.md

# 3. commit
git commit -m "docs: add Copilot review onboarding (instructions + REVIEW.md)"

# 4. push (upstream tracking 포함)
git push -u origin docs/copilot-review-onboarding

# 5. PR 생성 (Copilot 을 자동으로 reviewer 로 지정)
gh pr create `
  --title "docs: Copilot review onboarding" `
  --body-file REVIEW.md `
  --base main `
  --head docs/copilot-review-onboarding `
  --reviewer "copilot-pull-request-reviewer[bot]"
```

> `--reviewer copilot-pull-request-reviewer[bot]` 가 실패하면 (계정에 Copilot code review 기능이 아직 활성화되지 않은 경우), reviewer 지정을 빼고 PR 만 만든 뒤 PR 페이지에서 우상단 **Reviewers** → **Copilot** 클릭하세요.

### 결과

- 5~10 분 후 Copilot 이 PR 에 inline 코멘트와 summary 를 답니다.
- `REVIEW.md` 의 "What I want the review to focus on" 섹션이 PR description 으로 들어가므로, Copilot 이 그 우선순위를 따라 검토합니다.
- `.github/copilot-instructions.md` 가 자동으로 ground 되어 Copilot 이 도메인 지식·intentional design choice 를 인지한 채 검토합니다 (의도된 trade-off 를 "fix" 하라고 하지 않게).

## 대안 1 — VS Code / IDE 의 Copilot Chat

Copilot 구독은 있는데 PR 기반 검토를 안 쓰고 싶으면, repo 를 VS Code 에서 열고 Copilot Chat 에서:

```
@workspace Review this repository against REVIEW.md. Focus on the priorities listed there.
```

`@workspace` 는 `.github/copilot-instructions.md` 와 `REVIEW.md` 를 자동으로 컨텍스트에 포함합니다.

## 대안 2 — gh copilot extension (CLI)

```powershell
gh extension install github/gh-copilot
gh copilot explain --target file collect_intake.py
gh copilot suggest "review my collector script for security issues"
```

이건 일반 Q&A 용이라 코드 검토에는 PR 워크플로가 더 적합합니다.

## Copilot 검토 결과 받은 뒤

1. PR 의 Copilot 코멘트를 읽고 본인의 평가:
   - `must-fix` 가 있으면 → 같은 브랜치에 수정 commit + push (PR 자동 업데이트)
   - `should-fix` / `nice-to-have` → 별도 GitHub issue 로 백로그화
   - `wontfix-by-design` 분류는 PR 에서 reply 로 reasoning 남기고 dismiss
2. 본인이 OK 라고 판단되면 **Squash and merge** 로 main 에 반영. 이 docs PR 만 squash 하면 `Initial v15.0 Phase 1` 커밋 + `docs: Copilot review onboarding` 커밋 두 개로 history 가 깔끔하게 정리됩니다.

## 검토 후 main 으로 통합되면

`REVIEW.md` 는 그대로 두셔도 좋고 (다음 Phase 2 검토 때 재사용 가능), 또는 `docs/PHASE1_REVIEW.md` 로 옮겨 history 화시켜도 됩니다. `.github/copilot-instructions.md` 는 영구 파일 — 향후 모든 Copilot 상호작용의 컨텍스트 베이스입니다.

## 자주 막히는 부분

| 증상 | 원인 | 해결 |
|---|---|---|
| `gh pr create --reviewer "copilot-pull-request-reviewer[bot]"` 가 "GraphQL: copilot-pull-request-reviewer[bot] is not a valid user" 로 실패 | 본 계정에 Copilot code review 가 enrolled 되지 않음 | reviewer 옵션 빼고 PR 만든 뒤 PR 페이지에서 GUI 로 Copilot 추가, 또는 <https://github.com/settings/copilot> 에서 활성화 |
| Copilot 이 5분 넘게 답변 안 함 | rate limit 또는 GitHub Copilot 서비스 지연 | 10 분 대기 후 PR refresh, 안 되면 PR 코멘트에 `/copilot review` 슬래시 커맨드 시도 |
| Copilot 이 일반론적 답변 (e.g. "Add docstrings", "Consider using async") | `.github/copilot-instructions.md` 를 못 읽음 | 파일 경로 정확한지 확인 (`.github/` 폴더 안에 있어야 함), commit 됐는지 `git log --oneline -- .github/copilot-instructions.md` 로 확인 |
