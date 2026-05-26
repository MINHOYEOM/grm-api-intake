#!/usr/bin/env bash
# GRS v15.0 Phase 1 — GitHub 원샷 셋업 스크립트 (bash)
#
# 수행 작업:
#   1) gh CLI · git 설치 / 인증 확인
#   2) Public GitHub 저장소 생성 (gh repo create)
#   3) v15.0-implementation 폴더의 8개 파일 push
#   4) Secrets 3개 등록 (NOTION_TOKEN, NOTION_DATABASE_ID, OPENFDA_API_KEY)
#   5) 검증 + 다음 단계 안내
#
# 토큰은 환경변수 또는 -s 시큐어 프롬프트로만 받습니다.
# 평문 echo · git history · 로그 파일 어디에도 남기지 않습니다.
#
# 사용:
#   cd <v15.0-implementation 폴더>
#   bash setup.sh
#   또는 환경변수 미리 지정:
#     NOTION_TOKEN='ntn_...' NOTION_DATABASE_ID='7784...' OPENFDA_API_KEY='...' \
#       REPO_NAME='grs-api-intake' bash setup.sh

set -euo pipefail

# ─── 상수 ───────────────────────────────────────────────────────────────────
DEFAULT_REPO_NAME="grs-api-intake"
DEFAULT_VISIBILITY="public"
DEFAULT_NOTION_DB_ID="7784c71fb7b343749b2bee5d04db7926"
REQUIRED_FILES=(
  "collect_intake.py"
  "requirements.txt"
  ".gitignore"
  ".env.example"
  "README.md"
  "notion_intake_db_schema.md"
  "GRS_Prompt_v15.0.md"
  ".github/workflows/grs-intake.yml"
)

# ─── 색상 (TTY 일 때만) ─────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_OK=$'\033[0;32m'; C_WARN=$'\033[0;33m'; C_ERR=$'\033[0;31m'
  C_INFO=$'\033[0;36m'; C_OFF=$'\033[0m'; C_BOLD=$'\033[1m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_INFO=""; C_OFF=""; C_BOLD=""
fi

ok()    { echo "${C_OK}✓${C_OFF} $*"; }
warn()  { echo "${C_WARN}⚠${C_OFF} $*"; }
err()   { echo "${C_ERR}✗${C_OFF} $*" >&2; }
info()  { echo "${C_INFO}→${C_OFF} $*"; }
title() { echo; echo "${C_BOLD}── $* ──${C_OFF}"; }

# ─── 1. 사전 점검 ─────────────────────────────────────────────────────────
title "1. 사전 점검"

command -v git >/dev/null 2>&1 || { err "git 가 PATH 에 없습니다. https://git-scm.com 에서 설치"; exit 1; }
ok "git 확인"

if ! command -v gh >/dev/null 2>&1; then
  err "gh (GitHub CLI) 가 없습니다. https://cli.github.com 에서 설치 후 'gh auth login' 실행"
  exit 1
fi
ok "gh CLI 확인"

if ! gh auth status >/dev/null 2>&1; then
  err "gh 가 로그인돼 있지 않습니다. 'gh auth login' 으로 먼저 로그인하세요."
  exit 1
fi
GH_USER=$(gh api user --jq .login)
ok "gh 인증됨 — 사용자: ${GH_USER}"

# 현재 디렉토리에 필수 파일 존재 확인
missing_files=()
for f in "${REQUIRED_FILES[@]}"; do
  [ -f "$f" ] || missing_files+=("$f")
done
if [ ${#missing_files[@]} -ne 0 ]; then
  err "현재 폴더에 다음 파일이 없습니다: ${missing_files[*]}"
  err "v15.0-implementation 폴더에서 실행해 주세요. (cd 'C:/Users/.../v15.0-implementation')"
  exit 1
fi
ok "필수 파일 8개 모두 존재"

# ─── 2. 입력값 수집 ────────────────────────────────────────────────────────
title "2. 입력값 수집"

REPO_NAME="${REPO_NAME:-$DEFAULT_REPO_NAME}"
VISIBILITY="${VISIBILITY:-$DEFAULT_VISIBILITY}"
NOTION_DATABASE_ID_INPUT="${NOTION_DATABASE_ID:-$DEFAULT_NOTION_DB_ID}"

read -rp "저장소 이름 [${REPO_NAME}]: " input
REPO_NAME="${input:-$REPO_NAME}"

read -rp "공개 (public) / 비공개 (private) [${VISIBILITY}]: " input
VISIBILITY="${input:-$VISIBILITY}"

read -rp "Notion Database ID [${NOTION_DATABASE_ID_INPUT}]: " input
NOTION_DATABASE_ID_INPUT="${input:-$NOTION_DATABASE_ID_INPUT}"

if [ -z "${NOTION_TOKEN:-}" ]; then
  echo "Notion Integration 토큰을 입력하세요. (입력은 화면에 보이지 않습니다)"
  read -rsp "NOTION_TOKEN: " NOTION_TOKEN
  echo
fi
if [ -z "$NOTION_TOKEN" ]; then
  err "NOTION_TOKEN 이 비어 있습니다. 종료."; exit 1
fi

if [ -z "${OPENFDA_API_KEY:-}" ]; then
  echo "OpenFDA API key (선택 — 없으면 Enter 만 누르세요)"
  read -rsp "OPENFDA_API_KEY: " OPENFDA_API_KEY
  echo
fi

# 요약 (토큰은 길이만 표시)
echo
info "요약:"
echo "  · 저장소         : ${GH_USER}/${REPO_NAME} (${VISIBILITY})"
echo "  · Notion DB ID   : ${NOTION_DATABASE_ID_INPUT}"
echo "  · NOTION_TOKEN   : (${#NOTION_TOKEN} 자, 시작 ${NOTION_TOKEN:0:4}***)"
if [ -n "$OPENFDA_API_KEY" ]; then
  echo "  · OpenFDA key    : (${#OPENFDA_API_KEY} 자) — 등록함"
else
  echo "  · OpenFDA key    : 미입력 — Secret 미등록 (수집기는 no-key 모드)"
fi
echo
read -rp "계속 진행? [y/N]: " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  warn "취소됨."; exit 0
fi

# ─── 3. 저장소 생성 ──────────────────────────────────────────────────────
title "3. 저장소 생성"

if gh repo view "${GH_USER}/${REPO_NAME}" >/dev/null 2>&1; then
  warn "저장소 ${GH_USER}/${REPO_NAME} 가 이미 존재합니다."
  read -rp "기존 저장소에 push 를 진행할까요? [y/N]: " resume
  if [[ ! "$resume" =~ ^[Yy]$ ]]; then
    err "중단."; exit 1
  fi
  REPO_URL="https://github.com/${GH_USER}/${REPO_NAME}"
  ok "기존 저장소 사용: ${REPO_URL}"
else
  gh repo create "${REPO_NAME}" \
    --"${VISIBILITY}" \
    --description "GRS API Intake — Federal Register + OpenFDA weekly collector for Claude Routine v15.0" \
    --disable-wiki \
    >/dev/null
  REPO_URL="https://github.com/${GH_USER}/${REPO_NAME}"
  ok "저장소 생성: ${REPO_URL}"
fi

# ─── 4. git 초기화 + push ────────────────────────────────────────────────
title "4. git push"

if [ ! -d ".git" ]; then
  git init -b main >/dev/null
  ok "git init (main)"
fi

# remote origin 보정
if git remote get-url origin >/dev/null 2>&1; then
  current_origin=$(git remote get-url origin)
  if [ "$current_origin" != "${REPO_URL}.git" ] && [ "$current_origin" != "${REPO_URL}" ]; then
    warn "origin 이 다른 URL 입니다: ${current_origin}"
    git remote set-url origin "${REPO_URL}.git"
    ok "origin 재설정: ${REPO_URL}.git"
  fi
else
  git remote add origin "${REPO_URL}.git"
  ok "origin 추가"
fi

# 사용자 정보 (없으면 gh 사용자명으로 임시 설정)
if [ -z "$(git config user.email || true)" ]; then
  git config user.email "${GH_USER}@users.noreply.github.com"
fi
if [ -z "$(git config user.name || true)" ]; then
  git config user.name "${GH_USER}"
fi

git add .
# Codex: distinguish git diff --cached --quiet exit codes (0=no-change, 1=change, >1=error).
set +e
git diff --cached --quiet
diff_exit=$?
set -e
if [ "$diff_exit" -eq 0 ]; then
  warn "커밋할 변경이 없습니다. (이미 push 됐을 수 있음)"
elif [ "$diff_exit" -eq 1 ]; then
  git commit -m "Initial v15.0 Phase 1 — intake collector + workflow + Routine prompt" >/dev/null
  ok "커밋 완료"
else
  err "git diff --cached 가 예기치 않은 exit $diff_exit 로 종료. 저장소 손상 가능."
  exit 1
fi

git branch -M main 2>/dev/null || true

# Codex: push 실패는 setup 전체를 실패로 만든다. 이전엔 warn 만 출력하고
# 마지막에 "전체 성공" 이라고 잘못 표시됐다.
push_success=true
if ! git push -u origin main 2>&1; then
  push_success=false
  warn "push 실패. Secrets 등록은 계속 진행 (idempotent) — setup 은 비정상 종료."
fi

# ─── 5. Secrets 등록 ─────────────────────────────────────────────────────
title "5. GitHub Secrets 등록"

# gh secret set 는 stdin 으로 값을 받음 — 로그·환경 노출 최소화
printf '%s' "$NOTION_TOKEN" | gh secret set NOTION_TOKEN --repo "${GH_USER}/${REPO_NAME}" --body -
ok "NOTION_TOKEN 등록"

printf '%s' "$NOTION_DATABASE_ID_INPUT" | gh secret set NOTION_DATABASE_ID --repo "${GH_USER}/${REPO_NAME}" --body -
ok "NOTION_DATABASE_ID 등록"

if [ -n "$OPENFDA_API_KEY" ]; then
  printf '%s' "$OPENFDA_API_KEY" | gh secret set OPENFDA_API_KEY --repo "${GH_USER}/${REPO_NAME}" --body -
  ok "OPENFDA_API_KEY 등록"
else
  info "OPENFDA_API_KEY 는 비어있어 등록하지 않음 (수집기는 no-key 모드로 동작)"
fi

echo
info "현재 Secrets 목록:"
gh secret list --repo "${GH_USER}/${REPO_NAME}"

# 메모리에서 토큰 즉시 제거
NOTION_TOKEN="" OPENFDA_API_KEY=""

# ─── 6. 완료 ─────────────────────────────────────────────────────────────
title "6. 셋업 완료"

# Codex: push 실패면 "전체 성공" 으로 잘못 보고하지 않는다.
if [ "$push_success" != "true" ]; then
  err "Setup 종료 — git push 실패."
  echo "  'git push -u origin main' 을 수동 실행해 해결 후 재시도."
  echo "  Secrets 는 이미 등록됐으므로 (idempotent) push 만 해결하면 됨."
  echo
  info "Repo URL: ${REPO_URL}"
  info "Actions:  ${REPO_URL}/actions"
  info "Secrets:  ${REPO_URL}/settings/secrets/actions"
  exit 1
fi

ok "모든 단계 성공."
echo
echo "다음 단계 (수동):"
echo "  1) Notion 에서 Integration 을 'Global Regulatory Sweep' 부모 페이지에 연결"
echo "     (Notion → 부모 페이지 → ⋯ → Connections → 해당 Integration 추가)"
echo
echo "  2) GitHub Actions 수동 dry-run 으로 검증:"
echo "     ${REPO_URL}/actions/workflows/grs-intake.yml"
echo "     → Run workflow → dry_run: true"
echo
echo "  3) dry-run 성공 시 dry_run: false 로 한 번 더 실행 → 실제 Notion 적재"
echo
echo "  4) GRS_Prompt_v15.0.md 본문(B 섹션 코드블록)을 Claude Code Routine 설정에 통째로 붙여넣기"
echo
echo "  5) 매주 일요일 22:07 UTC (월요일 07:07 KST) 자동 실행"
echo
info "Repo URL: ${REPO_URL}"
info "Actions:  ${REPO_URL}/actions"
info "Secrets:  ${REPO_URL}/settings/secrets/actions"
