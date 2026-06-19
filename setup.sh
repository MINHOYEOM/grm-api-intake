#!/usr/bin/env bash
# GRM current release - GitHub one-shot setup script (bash)
#
# Runs:
#   1) Check gh CLI and git installation/authentication
#   2) Create or reuse the GitHub repo
#   3) Push the current GRM implementation from this folder
#   4) Register required and optional GitHub Actions secrets
#   5) Print verification URLs and manual next steps
#
# Required secrets for the default scheduled workflow:
#   NOTION_TOKEN, NOTION_DATABASE_ID, DATA_GO_KR_SERVICE_KEY
#
# Optional secrets:
#   OPENFDA_API_KEY, BRAVE_API_KEY, DATA_GO_KR_KEY, LAW_GO_KR_OC, MFDS_HTTP_PROXY
#
# Usage:
#   cd <v15.0-implementation folder>
#   bash setup.sh
#   or preset env vars:
#     NOTION_TOKEN='ntn_...' NOTION_DATABASE_ID='7784...' DATA_GO_KR_SERVICE_KEY='...' \
#       REPO_NAME='grm-api-intake' bash setup.sh

set -euo pipefail

DEFAULT_REPO_NAME="grm-api-intake"
DEFAULT_VISIBILITY="public"
DEFAULT_NOTION_DB_ID="7784c71fb7b343749b2bee5d04db7926"
REQUIRED_FILES=(
  "collect_intake.py"
  "collect_mfds.py"
  "collect_mfds_recall.py"
  "collect_mfds_admin_action.py"
  "collect_mfds_gmp_inspection.py"
  "collect_search.py"
  "collect_ich.py"
  "collect_who.py"
  "collect_hc.py"
  "grm_common.py"
  "requirements.txt"
  ".gitignore"
  ".env.example"
  "GRM_SYSTEM.md"
  "docs/notion_intake_db_schema.md"
  "docs/prompts/GRM_Prompt_v15.6.md"
  ".github/workflows/grm-intake.yml"
)

if [ -t 1 ]; then
  C_OK=$'\033[0;32m'; C_WARN=$'\033[0;33m'; C_ERR=$'\033[0;31m'
  C_INFO=$'\033[0;36m'; C_OFF=$'\033[0m'; C_BOLD=$'\033[1m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_INFO=""; C_OFF=""; C_BOLD=""
fi

ok()    { echo "${C_OK}[OK]${C_OFF} $*"; }
warn()  { echo "${C_WARN}[WARN]${C_OFF} $*"; }
err()   { echo "${C_ERR}[ERR]${C_OFF} $*" >&2; }
info()  { echo "${C_INFO}[INFO]${C_OFF} $*"; }
title() { echo; echo "${C_BOLD}-- $* --${C_OFF}"; }

read_secret() {
  local name="$1"
  local required="${2:-false}"
  local help="${3:-}"
  local value="${!name:-}"

  if [ -z "$value" ]; then
    if [ -n "$help" ]; then echo "$help" >&2; fi
    if [ "$required" = "true" ]; then
      read -rsp "${name}: " value
    else
      read -rsp "${name} (optional - press Enter to skip): " value
    fi
    echo
  fi

  if [ "$required" = "true" ] && [ -z "$value" ]; then
    err "${name} is empty. Aborting."
    exit 1
  fi

  printf '%s' "$value"
}

secret_summary() {
  local label="$1"
  local value="$2"
  local required="${3:-false}"
  if [ -n "$value" ]; then
    printf '  - %-23s: (%d chars)\n' "$label" "${#value}"
  elif [ "$required" = "true" ]; then
    printf '  - %-23s: missing\n' "$label"
  else
    printf '  - %-23s: not provided - secret skipped\n' "$label"
  fi
}

title "1. Preflight checks"

command -v git >/dev/null 2>&1 || { err "git not found in PATH. Install from https://git-scm.com"; exit 1; }
ok "git found"

if ! command -v gh >/dev/null 2>&1; then
  err "gh (GitHub CLI) not found. Install from https://cli.github.com then run 'gh auth login'"
  exit 1
fi
ok "gh CLI found"

if ! gh auth status >/dev/null 2>&1; then
  err "gh is not logged in. Run 'gh auth login' first."
  exit 1
fi
GH_USER=$(gh api user --jq .login)
ok "gh authenticated as: ${GH_USER}"

missing_files=()
for f in "${REQUIRED_FILES[@]}"; do
  [ -f "$f" ] || missing_files+=("$f")
done
if [ "${#missing_files[@]}" -ne 0 ]; then
  err "Missing files in current folder: ${missing_files[*]}"
  err "Run this script from the v15.0-implementation folder."
  exit 1
fi
ok "Core GRM files present (${#REQUIRED_FILES[@]} checked)"

title "2. Inputs"

REPO_NAME="${REPO_NAME:-$DEFAULT_REPO_NAME}"
VISIBILITY="${VISIBILITY:-$DEFAULT_VISIBILITY}"
NOTION_DATABASE_ID_INPUT="${NOTION_DATABASE_ID:-$DEFAULT_NOTION_DB_ID}"

read -rp "Repo name [${REPO_NAME}]: " input
REPO_NAME="${input:-$REPO_NAME}"

read -rp "Visibility public/private [${VISIBILITY}]: " input
VISIBILITY="${input:-$VISIBILITY}"
if [ "$VISIBILITY" != "public" ] && [ "$VISIBILITY" != "private" ]; then
  err "Visibility must be public or private."
  exit 1
fi

read -rp "Notion Database ID [${NOTION_DATABASE_ID_INPUT}]: " input
NOTION_DATABASE_ID_INPUT="${input:-$NOTION_DATABASE_ID_INPUT}"
if [ -z "$NOTION_DATABASE_ID_INPUT" ]; then
  err "NOTION_DATABASE_ID is empty. Aborting."
  exit 1
fi

NOTION_TOKEN=$(read_secret "NOTION_TOKEN" "true" "Paste your Notion Integration token. Input is hidden.")
DATA_GO_KR_SERVICE_KEY=$(read_secret "DATA_GO_KR_SERVICE_KEY" "true" "Paste your data.go.kr service key for MFDS/data.go.kr APIs. Input is hidden.")
OPENFDA_API_KEY=$(read_secret "OPENFDA_API_KEY" "false" "OpenFDA API key is optional. Leave empty for no-key mode.")
BRAVE_API_KEY=$(read_secret "BRAVE_API_KEY" "false" "Brave Search API key is optional and used only when ENABLE_SEARCH=true.")
DATA_GO_KR_KEY=$(read_secret "DATA_GO_KR_KEY" "false" "DATA_GO_KR_KEY is optional and used only when ENABLE_MOLEG_API=true.")
LAW_GO_KR_OC=$(read_secret "LAW_GO_KR_OC" "false" "LAW_GO_KR_OC is optional and enriches MFDS law/admrul full text.")
MFDS_HTTP_PROXY=$(read_secret "MFDS_HTTP_PROXY" "false" "MFDS_HTTP_PROXY is optional and used only for MFDS/nedrug/law.go.kr KR egress.")

echo
info "Summary:"
echo "  - Repo                   : ${GH_USER}/${REPO_NAME} (${VISIBILITY})"
echo "  - Notion DB ID           : ${NOTION_DATABASE_ID_INPUT}"
secret_summary "NOTION_TOKEN" "$NOTION_TOKEN" "true"
secret_summary "DATA_GO_KR_SERVICE_KEY" "$DATA_GO_KR_SERVICE_KEY" "true"
secret_summary "OPENFDA_API_KEY" "$OPENFDA_API_KEY"
secret_summary "BRAVE_API_KEY" "$BRAVE_API_KEY"
secret_summary "DATA_GO_KR_KEY" "$DATA_GO_KR_KEY"
secret_summary "LAW_GO_KR_OC" "$LAW_GO_KR_OC"
secret_summary "MFDS_HTTP_PROXY" "$MFDS_HTTP_PROXY"
echo
info "Default scheduled runs enable MFDS Recall/Admin/GMP Inspection; MFDS Law/GMP Cert/Safety Letter are opt-in. DATA_GO_KR_SERVICE_KEY is required. MFDS_HTTP_PROXY is optional KR egress."
echo
read -rp "Proceed? [y/N]: " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  warn "Cancelled."
  exit 0
fi

title "3. Create repository"

REPO_URL="https://github.com/${GH_USER}/${REPO_NAME}"
if gh repo view "${GH_USER}/${REPO_NAME}" >/dev/null 2>&1; then
  warn "Repo ${GH_USER}/${REPO_NAME} already exists."
  read -rp "Push to the existing repo? [y/N]: " resume
  if [[ ! "$resume" =~ ^[Yy]$ ]]; then
    err "Aborted by user."
    exit 1
  fi
  ok "Using existing repo: ${REPO_URL}"
else
  gh repo create "${REPO_NAME}" \
    --"${VISIBILITY}" \
    --description "GRM API Intake - daily regulatory collector for Notion and Claude Routine" \
    --disable-wiki \
    >/dev/null
  ok "Repo created: ${REPO_URL}"
fi

title "4. git push"

if [ ! -d ".git" ]; then
  git init -b main >/dev/null
  ok "git init (main)"
fi

if git remote get-url origin >/dev/null 2>&1; then
  current_origin=$(git remote get-url origin)
  if [ "$current_origin" != "${REPO_URL}.git" ] && [ "$current_origin" != "${REPO_URL}" ]; then
    warn "origin points elsewhere: ${current_origin}"
    git remote set-url origin "${REPO_URL}.git"
    ok "origin reset to: ${REPO_URL}.git"
  fi
else
  git remote add origin "${REPO_URL}.git"
  ok "origin added"
fi

if [ -z "$(git config user.email || true)" ]; then
  git config user.email "${GH_USER}@users.noreply.github.com"
fi
if [ -z "$(git config user.name || true)" ]; then
  git config user.name "${GH_USER}"
fi

git add .
if git diff --cached --quiet; then
  warn "Nothing to commit (already pushed?)."
else
  git commit -m "Initial GRM daily intake setup" >/dev/null
  ok "Commit created"
fi

git branch -M main 2>/dev/null || true
if git push -u origin main; then
  ok "push complete"
else
  warn "push failed. Try 'git push -u origin main' manually after fixing the cause."
fi

title "5. Register GitHub Secrets"

printf '%s' "$NOTION_TOKEN" | gh secret set NOTION_TOKEN --repo "${GH_USER}/${REPO_NAME}"
ok "NOTION_TOKEN registered"

printf '%s' "$NOTION_DATABASE_ID_INPUT" | gh secret set NOTION_DATABASE_ID --repo "${GH_USER}/${REPO_NAME}"
ok "NOTION_DATABASE_ID registered"

printf '%s' "$DATA_GO_KR_SERVICE_KEY" | gh secret set DATA_GO_KR_SERVICE_KEY --repo "${GH_USER}/${REPO_NAME}"
ok "DATA_GO_KR_SERVICE_KEY registered"

if [ -n "$OPENFDA_API_KEY" ]; then
  printf '%s' "$OPENFDA_API_KEY" | gh secret set OPENFDA_API_KEY --repo "${GH_USER}/${REPO_NAME}"
  ok "OPENFDA_API_KEY registered"
else
  info "OPENFDA_API_KEY skipped (collector runs in no-key mode)"
fi

if [ -n "$BRAVE_API_KEY" ]; then
  printf '%s' "$BRAVE_API_KEY" | gh secret set BRAVE_API_KEY --repo "${GH_USER}/${REPO_NAME}"
  ok "BRAVE_API_KEY registered"
else
  info "BRAVE_API_KEY skipped (ENABLE_SEARCH=false by default)"
fi

if [ -n "$DATA_GO_KR_KEY" ]; then
  printf '%s' "$DATA_GO_KR_KEY" | gh secret set DATA_GO_KR_KEY --repo "${GH_USER}/${REPO_NAME}"
  ok "DATA_GO_KR_KEY registered"
else
  info "DATA_GO_KR_KEY skipped (ENABLE_MOLEG_API=false by default)"
fi

if [ -n "$LAW_GO_KR_OC" ]; then
  printf '%s' "$LAW_GO_KR_OC" | gh secret set LAW_GO_KR_OC --repo "${GH_USER}/${REPO_NAME}"
  ok "LAW_GO_KR_OC registered"
else
  info "LAW_GO_KR_OC skipped (law.go.kr body enrich disabled)"
fi

if [ -n "$MFDS_HTTP_PROXY" ]; then
  printf '%s' "$MFDS_HTTP_PROXY" | gh secret set MFDS_HTTP_PROXY --repo "${GH_USER}/${REPO_NAME}"
  ok "MFDS_HTTP_PROXY registered"
else
  info "MFDS_HTTP_PROXY skipped (direct egress only)"
fi

echo
info "Current secrets:"
gh secret list --repo "${GH_USER}/${REPO_NAME}"

NOTION_TOKEN=""
DATA_GO_KR_SERVICE_KEY=""
OPENFDA_API_KEY=""
BRAVE_API_KEY=""
DATA_GO_KR_KEY=""
LAW_GO_KR_OC=""
MFDS_HTTP_PROXY=""

title "6. Setup complete"

ok "All steps succeeded."
echo
echo "Next steps (manual):"
echo "  1) In Notion, connect the Integration to the 'Global Regulatory Monitor' parent page"
echo "     (Notion -> parent page -> ... -> Connections -> add the integration)"
echo
echo "  2) Trigger a manual dry-run to verify:"
echo "     ${REPO_URL}/actions/workflows/grm-intake.yml"
echo "     -> Run workflow -> dry_run: true"
echo
echo "  3) If dry-run is OK, run again with dry_run: false to write to Notion"
echo
echo "  4) Paste docs/prompts/GRM_Prompt_v15.6.md into your Claude Code Routine"
echo
echo "  5) Cron schedule: daily 18:17 UTC (03:17 KST next day)"
echo "     Routine digest: Monday 07:30 KST"
echo
info "Repo URL: ${REPO_URL}"
info "Actions:  ${REPO_URL}/actions"
info "Secrets:  ${REPO_URL}/settings/secrets/actions"
