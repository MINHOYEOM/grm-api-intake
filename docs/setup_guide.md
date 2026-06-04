# GitHub 원샷 셋업 사용 가이드

`setup.ps1` 또는 `setup.sh` 한 번 실행으로 다음을 자동 수행합니다.

1. GitHub 저장소 생성 또는 기존 저장소 재사용
2. 현재 `v15.0-implementation` 폴더의 GRM 구현 파일 push
3. GitHub Actions secrets 등록
4. 검증 URL과 수동 후속 단계 출력

## 사전 요구사항

| 항목 | 확인 또는 설치 |
|---|---|
| `git` | <https://git-scm.com/> 설치 후 `git --version` |
| `gh` | <https://cli.github.com/> 설치 후 `gh --version` |
| `gh` 인증 | `gh auth login` 후 `gh auth status` 로 로그인 확인 |
| Python | 로컬 dry-run 검증용. `python --version` 또는 `py --version` |

설치 확인:

```bash
git --version
gh --version
gh auth status
```

## 실행 방법

### Windows PowerShell

```powershell
cd "C:\Users\user\Desktop\Global Regulatory Sweep\v15.0-implementation"
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

### macOS, Linux, Git Bash

```bash
cd "/path/to/v15.0-implementation"
bash setup.sh
```

### 환경변수로 미리 값 지정

민감 값은 프롬프트에서 숨김 입력하는 쪽이 더 안전합니다. 자동 실행이 필요할 때만 환경변수 방식을 사용하세요.

bash:

```bash
NOTION_TOKEN='ntn_xxxxx...' \
NOTION_DATABASE_ID='7784c71fb7b343749b2bee5d04db7926' \
DATA_GO_KR_SERVICE_KEY='data_go_kr_service_key_here' \
OPENFDA_API_KEY='optional_openfda_key_here' \
BRAVE_API_KEY='optional_brave_key_here' \
DATA_GO_KR_KEY='optional_moleg_key_here' \
REPO_NAME='grm-api-intake' \
bash setup.sh
```

PowerShell:

```powershell
$env:NOTION_TOKEN = 'ntn_xxxxx...'
$env:NOTION_DATABASE_ID = '7784c71fb7b343749b2bee5d04db7926'
$env:DATA_GO_KR_SERVICE_KEY = 'data_go_kr_service_key_here'
$env:OPENFDA_API_KEY = 'optional_openfda_key_here'
$env:BRAVE_API_KEY = 'optional_brave_key_here'
$env:DATA_GO_KR_KEY = 'optional_moleg_key_here'
.\setup.ps1 -RepoName 'grm-api-intake' -Visibility 'public'
```

사용 후에는 `unset NOTION_TOKEN DATA_GO_KR_SERVICE_KEY` 또는 `Remove-Item Env:NOTION_TOKEN, Env:DATA_GO_KR_SERVICE_KEY` 처럼 민감 환경변수를 지우는 것을 권장합니다.

## 스크립트가 묻는 항목

| 질문 | 기본값 | 구분 | 의미 |
|---|---:|---|---|
| 저장소 이름 | `grm-api-intake` | 필수 | GitHub repo 이름 |
| 공개/비공개 | `public` | 필수 | 저장소 공개 범위 |
| Notion Database ID | `7784c71fb7b343749b2bee5d04db7926` | 필수 | `GRM API Intake` DB ID |
| `NOTION_TOKEN` | 없음 | 필수 | Notion Integration token |
| `DATA_GO_KR_SERVICE_KEY` | 없음 | 필수 | MFDS 회수/행정처분 API용 data.go.kr service key |
| `OPENFDA_API_KEY` | 없음 | 선택 | OpenFDA rate limit 완화용 |
| `BRAVE_API_KEY` | 없음 | 선택 | `ENABLE_SEARCH=true` 때만 사용 |
| `DATA_GO_KR_KEY` | 없음 | 선택 | `ENABLE_MOLEG_API=true` 때만 사용 |

현재 예약 workflow는 `ENABLE_MFDS_RECALL`, `ENABLE_MFDS_ADMIN`, `ENABLE_MFDS_GMP_INSPECTION` 기본값이 `true`입니다. 따라서 기본 운영 기준에서는 `DATA_GO_KR_SERVICE_KEY`가 필수입니다.

마지막 요약 화면에서 `y` 입력 시에만 실제 저장소 생성, push, secret 등록이 진행됩니다.

## 진행 도중 실패한 경우

| 증상 | 원인 | 해결 |
|---|---|---|
| `gh: command not found` | GitHub CLI 미설치 | <https://cli.github.com/> 설치 |
| `gh is not logged in` | GitHub 인증 없음 | `gh auth login` 실행 |
| `Missing files in current folder` | 다른 디렉토리에서 실행 | `v15.0-implementation` 폴더에서 실행 |
| repo가 이미 존재 | 같은 이름의 repo 존재 | 기존 repo에 push하거나 이름 변경 |
| `git push` 실패 | 원격에 다른 커밋 존재 | `git pull --rebase origin main` 후 재실행 |
| `gh secret set` 실패 | secret 값 누락 또는 권한 문제 | 필수 secret 재입력, `gh auth status` 확인 |

스크립트는 단계별로 멈춥니다. 중단 후 다시 실행하면 이미 끝난 단계는 대부분 재사용됩니다.

## 셋업 완료 후 검증

스크립트 마지막 출력의 URL을 확인합니다.

1. **Repo URL**: 현재 GRM 구현 파일이 push 됐는지 확인
2. **Actions**: `GRM API Intake (Daily)` workflow가 보이는지 확인
3. **Secrets**: 필수 3개 secret과 필요한 선택 secret이 보이는지 확인

필수 secret:

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `DATA_GO_KR_SERVICE_KEY`

선택 secret:

- `OPENFDA_API_KEY`
- `BRAVE_API_KEY`
- `DATA_GO_KR_KEY`

Actions 페이지에서 수동 검증:

1. `GRM API Intake (Daily)` 선택
2. `Run workflow` 클릭
3. `dry_run: true` 로 실행
4. Job Summary와 `grm-health.json` 결과가 `ok` 또는 transient warning인지 확인
5. dry-run이 정상이면 `dry_run: false` 로 한 번 더 실행해 Notion 적재 확인

이 검증이 통과하면 매일 18:17 UTC, 한국시간 익일 03:17 KST에 자동 수집됩니다. Claude Routine 다이제스트는 매주 월요일 07:30 KST 실행 기준입니다.

## 보안 메모

- 스크립트는 secret 값을 터미널에 출력하지 않습니다.
- bash는 값을 표준입력으로 넘기고 `gh secret set`이 stdin에서 읽도록 합니다.
- PowerShell은 제한 ACL을 적용한 임시 파일을 `--body-file`로 넘긴 뒤 즉시 덮어쓰고 삭제합니다.
- GitHub Secrets는 Actions 런타임 외에는 평문으로 다시 조회할 수 없습니다.
- 채팅, 터미널 history, 문서에 노출된 토큰은 Notion 또는 data.go.kr에서 재발급/폐기하는 것을 권장합니다.
