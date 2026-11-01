# 클라우드 실행 공통 규칙 — claude.ai 예약작업(Routine)이 읽는다

2026-10-31 실측(환경 `env_01ErF8qqKJtFSxMYM6UcCCz4`). 저장소 main 이 작업 폴더에 받아져 있다. `python3` 는 3.11(`python3.12` 도 있다 — 3.12 문법이 필요한 코드는 그 venv 로).

## 시작할 때
- `pip install -q -r requirements.txt` (pypi 는 열려 있다). 테스트가 더 요구하는 모듈만 그때 설치한다.
- 오늘 날짜·주차는 KST 로 계산한다: `TZ=Asia/Seoul date +%F` · ISO 주차 `TZ=Asia/Seoul date +%G%V`.

## GitHub — `gh api` 만 쓴다
`gh pr`·`gh run`·`gh workflow`·`gh variable` 은 쓰지 않는다. **`/actions/…` 경로(워크플로 실행 조회·재실행·아티팩트·변수)는 프록시가 막는다(403).**
지시문이 이런 일을 시키면 하지 말고 결과에 `막힘: Actions — <하려던 일>` 한 줄을 남긴다(PC 백업이나 대표가 한다).

```
R=repos/MINHOYEOM/grm-api-intake
gh api "$R/pulls?state=open&per_page=100" --jq '.[]|[.number,.title,.head.ref]|@tsv'          # 열린 PR
gh api "$R/pulls" -f title="…" -f head=claude/… -f base=main -F body=@/tmp/pr.md --jq .html_url  # PR 만들기
gh api "$R/commits/<sha>/check-runs?per_page=100" --jq '.check_runs[]|[.name,.status,.conclusion]|@tsv'  # CI 결과
gh api "$R/issues" -f title="…" -F body=@/tmp/issue.md -f 'labels[]=automation-warning'       # 이슈
gh api "$R/issues?state=open&labels=automation-warning" --jq '.[]|select(.pull_request==null)|[.number,.title]|@tsv'  # 경고 이슈(이 API 는 PR 도 섞어 준다 — 걸러라)
```
다른 브랜치의 파일은 `git fetch origin <branch>` 후 `git show FETCH_HEAD:<path>` 로 읽는다.

## 브랜치·PR·머지
- push 는 `claude/` 로 시작하는 브랜치에만. PR 제목은 지시문이 정한 형식 그대로 쓴다 — PC 백업의 중복 확인과 자동 머지가 그 제목을 찾는다.
- 머지하지 않는다. `grm-stranded-batch-pr.yml` 이 6시간마다 돌며 CI 가 전부 성공하고 3시간 지난 PR 을 머지한다
  (대상 제목: `chore(findings): 일일 번역 배치` · `glossary(weekly):` · `quiz(weekly):` · `glossary(fig):`).
- 전체 테스트(약 2시간)는 돌리지 않는다. 지시문이 정한 표적 테스트만 돌리고 나머지는 CI 가 본다.

## 네트워크
2026-11-01 대표가 환경 네트워크를 전체 허용으로 바꿨다(fda.gov·grm-solutions.com·WebFetch 실측 통과).
그래도 어떤 주소가 `ENOTFOUND`·`connect_rejected` 로 실패하면 다른 길로 우회하지 말고
`보류: 네트워크 — <주소>` 로 끝낸다(환경 설정이 바뀐 것이다 — 대표가 확인).
- 쓰는 주소: `grm-solutions.com` · `rfwixqqdljpmtjdlblct.supabase.co` · 공식 출처(용어 정의 확인용).
- Supabase anon 키·URL(공개값)은 `curl -s https://grm-solutions.com/findings/ | grep -o 'data-url="[^"]*"\|data-key="[^"]*"'` 로 얻는다.
- Supabase 커넥터는 `select` 만 쓴다. 쓰기·마이그레이션 금지.

## PC 백업과의 관계 (2026-11-14 까지)
같은 일을 하는 PC 예약작업이 클라우드보다 늦게 돌며, 클라우드가 이미 했으면(지시문이 정한 PR 제목·결과 파일이 있으면) 건너뛴다.
그래서 **표식을 지시문대로 정확히 남기는 것**이 중복을 막는다. 일을 시작하기 전에도 같은 표식으로 "이미 했나"를 먼저 본다.
