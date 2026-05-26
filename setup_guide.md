# GitHub 원샷 셋업 — 사용 가이드

`setup.sh` (bash) 또는 `setup.ps1` (PowerShell) 한 번 실행으로 다음을 자동 수행합니다:

1. GitHub 저장소 생성 (`gh repo create`)
2. v15.0-implementation 폴더 8개 파일을 push
3. Secrets 3개 등록 (`NOTION_TOKEN` · `NOTION_DATABASE_ID` · `OPENFDA_API_KEY`)
4. 검증 + 다음 단계 안내

## 사전 요구사항 (한 번만)

| 항목 | 확인·설치 |
|---|---|
| `git` | <https://git-scm.com/> · 설치 후 `git --version` |
| `gh` (GitHub CLI) | <https://cli.github.com/> · 설치 후 `gh --version` |
| `gh` 인증 | 터미널에서 `gh auth login` → 브라우저 인증 → `gh auth status` 로 `Logged in` 확인 |
| Python (선택) | dry-run 로컬 검증용. `python --version` |

설치 확인:

```bash
git --version
gh --version
gh auth status
```

## 실행 방법

### Windows (PowerShell · 권장)

```powershell
cd "C:\Users\user\Desktop\Global Regulatory Sweep (1)\v15.0-implementation"

# (먼저 outputs 폴더의 setup.ps1 을 이 폴더로 복사 — 또는 직접 다운로드)
copy "$env:APPDATA\Claude\local-agent-mode-sessions\...\outputs\setup.ps1" .

powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

### macOS / Linux / Git Bash (Windows)

```bash
cd "/path/to/v15.0-implementation"

# setup.sh 를 이 폴더에 복사한 뒤
bash setup.sh
```

### 환경변수로 미리 값 지정 (옵션)

매번 프롬프트 입력이 번거로우면:

bash:
```bash
NOTION_TOKEN='ntn_xxxxx...' \
NOTION_DATABASE_ID='7784c71fb7b343749b2bee5d04db7926' \
OPENFDA_API_KEY='your_openfda_key_here' \
REPO_NAME='grs-api-intake' \
bash setup.sh
```

PowerShell:
```powershell
$env:NOTION_TOKEN = 'ntn_xxxxx...'
$env:NOTION_DATABASE_ID = '7784c71fb7b343749b2bee5d04db7926'
$env:OPENFDA_API_KEY = 'your_openfda_key_here'
.\setup.ps1 -RepoName 'grs-api-intake' -Visibility 'public'
```

> 환경변수는 셸 history 에 남을 수 있으므로 사용 후 `unset NOTION_TOKEN` (bash) 또는 `Remove-Item Env:NOTION_TOKEN` (PowerShell) 권장.

## 스크립트가 묻는 항목

| 질문 | 기본값 | 의미 |
|---|---|---|
| 저장소 이름 | `grs-api-intake` | GitHub repo 이름 (Enter 로 기본값 사용) |
| 공개 / 비공개 | `public` | Public 권장 (Actions 무료 무제한) |
| Notion Database ID | `7784c71fb7b343749b2bee5d04db7926` | 이미 생성된 GRS API Intake DB ID |
| NOTION_TOKEN | (없음) | 화면에 안 보이게 입력. Notion Integration 토큰 |
| OPENFDA_API_KEY | (없음) | 선택. Enter 누르면 등록 안 함 |

마지막 요약 화면에서 `y` 입력 시에만 실제 작업 수행.

## 진행 도중 실패한 경우

스크립트는 단계별로 멈춥니다. 가장 흔한 원인:

| 증상 | 원인 | 해결 |
|---|---|---|
| `gh: command not found` | gh CLI 미설치 | <https://cli.github.com/> 설치 |
| `Logged out` | `gh auth status` 실패 | `gh auth login` |
| `현재 폴더에 다음 파일이 없습니다` | 다른 디렉토리에서 실행 | v15.0-implementation 폴더에서 실행 |
| `repo view ... 가 이미 존재` | 같은 이름의 repo 존재 | Enter 로 진행 시 기존 repo 에 push, 또는 이름 변경 |
| `git push` 실패 | 원격에 다른 커밋 존재 | `git pull --rebase origin main` 후 재실행 |
| `gh secret set` 실패 | 토큰이 빈 문자열 | NOTION_TOKEN 다시 입력 |

스크립트 중단 시 안전합니다 — 부분 작업만 적용된 상태로 멈추고, 다시 실행하면 이미 끝난 단계는 skip 합니다.

## 셋업 완료 후 검증

스크립트 마지막 출력의 3개 URL 확인:

1. **Repo URL** → 파일 8개가 push 됐는지
2. **Actions** → `GRS API Intake (Weekly)` 워크플로가 보이는지
3. **Secrets** → 3개 (또는 2개) Secret 이름이 보이는지

그 다음 Actions 페이지에서:

1. `GRS API Intake (Weekly)` 선택 → `Run workflow` 버튼 → `dry_run: true` 선택 → 실행
2. 약 2분 후 Job Summary 확인 — `Federal Register: fetched N` 처럼 보여야 정상
3. 같은 절차로 `dry_run: false` 실행 → Notion `GRS API Intake` DB 에 row 생김

이 두 검증이 통과하면 매주 일요일 22:07 UTC (월요일 07:07 KST) 부터 자동 실행됩니다.

## 보안 메모

- 스크립트는 `NOTION_TOKEN` 을 **stdin 으로만** `gh secret set` 에 전달합니다. bash 는 `printf '%s' "$value" | gh secret set ... --body -`, PowerShell 은 `System.Diagnostics.ProcessStartInfo` + `RedirectStandardInput` + `StandardInput.Write($value)` 을 사용해 명령행 인자 (process list 노출) · 임시 파일 · pipeline newline 변환 어느 경로로도 평문 토큰이 새지 않습니다.
- `gh secret set` 은 GitHub 의 공개키 암호화 (libsodium sealed box) 를 사용해 전송합니다. 토큰은 GitHub Actions 런타임 외에는 평문으로 복호화되지 않습니다.
- 작업 종료 시 스크립트가 메모리 변수를 비웁니다.
- 그래도 채팅에 노출된 기존 토큰이 걱정되면 Notion → Integrations → `Rotate token` 권장.
