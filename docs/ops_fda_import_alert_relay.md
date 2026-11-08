# FDA 수입경보 PC 중계 — 등록·운영 안내

GitHub 러너(데이터센터 IP)에서는 `www.accessdata.fda.gov` 전체가 Akamai 차단으로 HTTP 404다. 한국 PC에서는 같은 요청이 200이다(2026-11-08 실측). 우회는 만들지 않는다. 그래서 **PC가 원문만 받아 넘기고, 파싱·검증·DB 쓰기는 러너가 한다.** PC에는 Supabase 키를 두지 않는다.

```
PC 작업 스케줄러 ─ fda_import_alert_relay.py ─ requests 2페이지 → 구조 검증
  → gh api: 비공개 grm-ops/import_alert_raw/<KST 날짜>/{66-40,66-41}.html.gz + manifest.json
  → gh workflow run grm-fda-import-alert.yml (source=ops, ops_date, dry_run=false)
러너 ─ grm-ops 내려받기(GRM_OPS_READ_TOKEN) → collect_fda_import_alert.py --html-dir --apply
  → sha256·크기 재검증 → 구조 검증 → 멱등 가드 → import_alert_apply RPC
```

## 한 번만 하는 일

### 1. 대표: 읽기 토큰 등록

GitHub → Settings → Developer settings → Fine-grained tokens에서 토큰을 하나 만든다.

| 항목 | 값 |
|---|---|
| Repository access | `MINHOYEOM/grm-ops` 하나만 |
| Permissions | Contents: **Read-only** (나머지 없음) |
| 만료 | 1년 권장 (만료 전에 갱신을 장부에 적는다) |

만든 토큰은 `grm-api-intake` 저장소 secret에 `GRM_OPS_READ_TOKEN` 이름으로 넣는다. 값을 대화창에 붙여 넣지 않는다.

```
gh secret set GRM_OPS_READ_TOKEN -R MINHOYEOM/grm-api-intake
```

### 2. 비서: 각 PC 확인

- `gh auth status`의 Token scopes에 `repo`와 `workflow`가 있어야 한다. 업로드에 `repo`, dispatch에 `workflow`를 쓴다.
- `py -3.12 -c "import requests"`가 오류 없이 끝나야 한다.
- 정본 체크아웃이 main 최신이어야 한다. 이 스크립트는 같은 폴더의 `collect_fda_import_alert.py`를 import한다.
- 처음에 한 번 시험한다. 이 명령은 받기·검증만 하고 아무것도 올리지 않는다.

  ```
  py -3.12 fda_import_alert_relay.py --check-only
  ```

### 3. 비서: 예약 등록 (Windows 작업 스케줄러)

두 PC가 동시에 돌지 않도록 시각을 엇갈린다. 개인PC는 07:20, 회사PC는 07:50이다. 같은 날 늦게 돈 쪽은 이미 올라간 원문을 보고 업로드를 건너뛰고, dispatch만 다시 한다. 러너도 같은 날·같은 원문이면 쓰지 않는다.

`<정본>`은 그 PC의 grm-api-intake main 체크아웃 경로다(예: `D:\Global Regulatory Monitoring Solution\v15.0-implementation`).

```
schtasks /Create /TN "GRM\FDA Import Alert Relay" /SC DAILY /ST 07:20 /F ^
  /TR "cmd /c chcp 65001 >nul && cd /d \"<정본>\" && py -3.12 fda_import_alert_relay.py >> \"%LOCALAPPDATA%\GRM\fda_import_alert_relay.log\" 2>&1"
```

- 회사PC는 `/ST 07:50`으로 등록한다.
- 로그 폴더는 등록 전에 한 번 만든다.

  ```
  mkdir "%LOCALAPPDATA%\GRM"
  ```

- 작업 스케줄러 GUI에서 "작업이 예약된 시간을 놓친 경우 가능한 한 빨리 작업 시작"을 켠다. 그러면 PC가 늦게 켜진 날도 돈다.
- 배치 파일을 따로 만들 경우에는 UTF-8(BOM) 저장과 첫 줄 `chcp 65001 >nul` 관례를 따른다.

### 4. 비서: 쓰기 켜기 (첫 시딩)

1. repo 변수 `ENABLE_FDA_IMPORT_ALERT=true`로 바꾼다. 이 변수가 off면 러너는 dry-run이다. 구조 검증과 diff만 하고 실패 이슈만 연다.
2. 첫 실제 쓰기는 이력 시작점(seed)이 된다. 예상치는 66-40 448곳, 66-41 1,770곳이다.

## 매일 무엇이 일어나나

| 상황 | 결과 |
|---|---|
| 원천 형식 변경·차단(PC에서도 404) | PC가 검증에서 멈추고 올리지 않는다. 로그에 exit 2가 남는다 |
| 같은 날 두 번째 실행(다른 PC 포함) | 업로드를 건너뛰고 dispatch만 한다. 러너는 `skip already_applied`로 쓰지 않는다 |
| 늦게 도착한 과거 날짜 | 러너가 `skip older_than_latest`로 쓰지 않는다(해제 판정 역행 방지) |
| 파일 깨짐·manifest 불일치 | 러너가 fetch 실패로 끝나고, 이슈 "FDA 수입경보 스냅샷 실패"를 연다 |
| 두 PC 모두 꺼진 날 | 그날 스냅샷이 없다. 다음 성공일의 diff에 합쳐진다(날짜는 그날로 찍힘) |

수동 재실행: Actions → GRM FDA Import Alert Snapshot → Run workflow에서 `source=ops`를 고른다. `ops_date`를 비우면 가장 최근 날짜를 쓴다.
