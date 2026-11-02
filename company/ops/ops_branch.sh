#!/usr/bin/env bash
# claude/ops-status 브랜치를 새로 받아 작성 명령을 돌리고, 바뀐 게 있으면 커밋·push (충돌 시 처음부터 재시도).
# 사용: ops_branch.sh "<커밋 메시지>" <작성 명령...>   (작성 명령은 clone 폴더 _ops 안에서 돈다 — 워크플로가 고정한 문자열만)
set -uo pipefail
MSG="$1"; shift
BRANCH=claude/ops-status
STDIN_FILE=$(mktemp); cat > "$STDIN_FILE" 2>/dev/null || true
for attempt in 1 2 3 4; do
  rm -rf _ops && mkdir _ops && cd _ops
  git init -q
  git remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    git fetch -q --depth 1 origin "$BRANCH" && git checkout -q -b ops FETCH_HEAD
  else
    git checkout -q --orphan ops
  fi
  "$@" < "$STDIN_FILE" || { echo "::error::작성 명령 실패"; cd ..; exit 1; }
  printf '클라우드 세션용 Actions 요약·요청 결과 — grm-ops-status.yml / grm-cloud-request.yml 이 쓴다. 손대지 않는다.\n' > README.txt
  git add -A
  if git diff --cached --quiet; then echo "변경 없음"; cd ..; exit 0; fi
  git commit -q -m "$MSG"
  if git push -q origin "ops:refs/heads/${BRANCH}"; then cd ..; exit 0; fi
  echo "::warning::push 거절 — 재시도 $attempt"; cd ..; sleep $((attempt * 3))
done
echo "::error::ops-status push 실패"; exit 1
