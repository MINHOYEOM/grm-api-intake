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

### 2. 비서: PC 확인

- **중계는 개인PC 한 대만 한다.** 회사PC는 2026-11-09 시험에서 66-40·66-41 모두 HTTP 404였다(회사망도 러너처럼 막힘). 개인PC는 같은 날 200이었다.
- `gh auth status`의 Token scopes에 `repo`와 `workflow`가 있어야 한다. 업로드에 `repo`, dispatch에 `workflow`를 쓴다.
- 정본 체크아웃이 main 최신이어야 한다. 이 스크립트는 같은 폴더의 `collect_fda_import_alert.py`를 import한다.

### 3. 비서: 예약 등록 (Windows 작업 스케줄러)

★**Claude 데스크톱 앱 안에서 만든 `%LOCALAPPDATA%`·`%APPDATA%` 파일은 앱 밖(작업 스케줄러)에서 보이지 않는다.** 앱이 AppData 쓰기를 가상화하기 때문이다. 11-09 첫 예약 실행은 로그 폴더가 없어 exit 1, 다음은 `No module named 'requests'`(패키지가 앱 안의 사용자 site-packages에만 있었다)로 실패했다. 그래서 래퍼·전용 venv·로그를 모두 `%USERPROFILE%\.grm-relay\`에 둔다.

1. 전용 venv를 만든다.

   ```
   py -3.12 -m venv "%USERPROFILE%\.grm-relay\venv"
   "%USERPROFILE%\.grm-relay\venv\Scripts\python.exe" -m pip install requests
   ```

2. 래퍼 `%USERPROFILE%\.grm-relay\fda_import_alert_relay.cmd`를 둔다. `<정본>`은 그 PC의 main 체크아웃 경로다.

   ```
   @echo off
   set LOG=%USERPROFILE%\.grm-relay\fda_import_alert_relay.log
   chcp 65001 >nul
   echo ==== %DATE% %TIME% start >> "%LOG%"
   cd /d "<정본>" >> "%LOG%" 2>&1 || (echo cd failed >> "%LOG%" & exit /b 2)
   "%USERPROFILE%\.grm-relay\venv\Scripts\python.exe" fda_import_alert_relay.py >> "%LOG%" 2>&1
   echo ==== exit %ERRORLEVEL% >> "%LOG%"
   exit /b %ERRORLEVEL%
   ```

3. 작업을 등록한다(매일 07:20).

   ```
   schtasks /Create /TN "GRM\FDA Import Alert Relay" /SC DAILY /ST 07:20 /F /TR "cmd.exe /c \"%USERPROFILE%\.grm-relay\fda_import_alert_relay.cmd\""
   ```

4. 작업 스케줄러 설정 세 가지를 켜거나 끈다. 노트북이라 **배터리 조건을 끄지 않으면 전원이 빠진 날 아예 시작하지 않는다**(11-09 아침 미실행 원인).
   - "작업이 예약된 시간을 놓친 경우 가능한 한 빨리 작업 시작" 켬
   - "컴퓨터의 AC 전원이 켜져 있는 경우에만 작업 시작" 끔
   - "컴퓨터가 배터리 전원으로 전환되는 경우 중지" 끔

5. 시험은 작업 스케줄러에서 직접 실행해 본다(앱 안 터미널에서 돌리면 가상화 때문에 통과해도 예약 실행은 실패할 수 있다). 로그 끝이 `==== exit 0`이고 `LastTaskResult`가 0이면 된다.

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
