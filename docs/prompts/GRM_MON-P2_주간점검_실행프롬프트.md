# GRM MON-P2 주간 관측 — 상태저장형 침묵 감지 실행프롬프트 (v0)

> 사용법: 주 1회(권장: 발행/수집 주기 직후), Notion MCP 가 연결된 **새 채팅**에 이 문서를 그대로
> 붙여넣고 맨 아래 {기준일} 만 채워 실행한다(또는 Cowork 스케줄드 태스크로 자동 실행).
> 목적: **P1 health check(그날 그 run 만 보는 stateless)가 못 잡는** 소스별 연속 0건·침묵 추세를
> 최근 N일 이력으로 감지·보고한다. P1 의 대체가 아니라 **보조 요약자**.
> 위치: `docs/prompts/GRM_MON-P2_주간점검_실행프롬프트.md` · 설계 = `docs/GRM_MON-P2_관측성_설계.md`.
> ⚠️ 이 트랙은 **읽기 전용** — Notion row·Status·수집기·발행물을 일절 수정하지 않는다. 요약·판정만.

---

```
[역할]
GRM 수집 파이프라인 건강의 상태저장 관측자(보조 요약자). P1 health check 는 그날 그 run 하나만
판정한다(stateless). 너는 그와 비중복으로, 최근 N일 수집 이력을 누적 조회해 소스별 "연속 0건·
침묵 추세"를 baseline 대비 감지한다. 핵심 비기능 요건은 과알림 0 — 저빈도라 정상적으로 0건인
소스를 이상으로 올리지 않는다. 모든 판정은 결정론적이어야 한다(같은 기준일이면 같은 결과).
읽기 전용: 무엇도 수정하지 않는다.

[입력]
1. 기준일 D = {YYYY-MM-DD, 미지정 시 오늘 KST}. 윈도우 = [D-6, D](표시), lookback L = 45일(조회).
2. Notion MCP 로 Intake DB(Database 7784c71fb7b343749b2bee5d04db7926 · data source d5b9634a-2bd7-4036-ba06-e4ad17ede288)에서
   Run Date (KST) 가 [D-44, D] 인 row 를 조회 → Source 별 일별 적재 카운트 시계열을 만든다.
   - Source = "GRM Handoff" 인 row 는 입력 큐이므로 제외.
   - 조회는 lookback 45일까지, 표시 요약은 최근 7일로 한다(조회/표시 분리).
3. ⓒ run 이력(가능하면): GitHub Actions 최근 7일 run 성공/실패/warning + 기존 운영 경고 Issue
   상태. Actions 접근이 없으면 "run 이력 미확인"으로 표기하고 ⓑ 만으로 판정하되 WORKFLOW_GAP
   은 "판정 불가"로 남긴다.

[활성 소스 (flag gate — 과알림 방지의 핵심)]
아래 "활성" 목록의 소스만 침묵 판정한다. 비활성 소스의 0건은 정상이므로 알리지 않는다.
이 목록은 repo workflow vars.ENABLE_* 현행값과 정합해야 하며, 변경 시 이 블록을 동기화한다.
  · 활성(현행 schedule 기준 — 실행 전 최신값 확인): fr, recall, ich  ← {운영 현황에 맞게 갱신}
  · 비활성/조건부: ema, mhra, pics, eca, wl, mfds, mfds_recall, mfds_admin,
    mfds_gmp_inspection, who, hc, fda483, search  ← {ENABLE_* 켜진 것은 활성으로 이동}
  ※ 판단 보조: 과거엔 row 가 있었는데 최근 끊긴 소스 = 활성이었던 정황. 과거에도 전무 = 미활성
    가능성 → 보수적으로 "관측 부족" 처리(알림 금지).

[baseline 등급·임계 (시드 v0 — 설계문서 §4)]
  High  : fr(4영업일), recall(5영업일)                         → 절대 0건 임계
  Med   : mfds·ema(10일), wl·hc·eca(14일)                      → 절대 0건 임계
  Med-Low: mfds_gmp_inspection(21일)                           → 추세 이탈 위주
  Low   : ich·who·mhra·pics·mfds_recall·mfds_admin(30일),
          fda483(45일)                                         → 추세 이탈만(절대 0건 알림 금지)
확신이 없으면 더 긴 임계 쪽을 택한다(과알림보다 미알림 우선).

[판정 절차 — 소스별, 결정론]
1. 활성 소스만 대상(flag gate).
2. High/Med: 마지막 비-0 적재일부터 D 까지 연속 0-적재 일수를 센다. 등급 임계 초과 → 침묵 후보.
3. Low: 직전 baseline 적재 간격(최근 비-0 적재 간 간격의 중앙값)을 구하고, 현재 침묵 길이가
   max(직전 baseline×2, 등급 임계) 초과 → 추세 이탈 후보. baseline 산출 불가 → "관측 부족"(알림 X).
4. 모든 후보를 ⓒ run-level 로 교차확인:
   - 윈도우에 run 이 며칠 부재 → 원인은 소스 침묵 아닌 workflow 미실행 → "WORKFLOW_GAP" 분류
     (소스별 침묵으로 오귀속 금지).
   - run 정상인데 소스만 0 → 진짜 침묵으로 확정.
5. dedup-skip 맹점 주의: Notion 은 inserted 만 보이므로 fetched>0·전부 dedup 인 단발 0건을
   침묵으로 단정하지 말 것. 추세(연속/이탈)에서만 신뢰, 단발 0건은 약신호.

[출력 — 주간 스코어카드]
A. 운영 run 요약(ⓒ): 최근7일 성공/실패/warning, 실패·warning Issue 링크, WORKFLOW_GAP.
B. 소스별 적재 추세(ⓑ): 표 | 소스 | 등급 | 활성 | 최근7일 적재 | 마지막 적재일 | 연속0건 | 판정 |
   (활성 소스만; 비활성은 "(off)" 별행).
C. 이상 신호:
   - [WARN] {소스}: 연속 {일}일 0적재(임계 {임계} 초과) — run 정상이므로 소스 침묵 의심
   - [INFO] {소스}: 추세 이탈(직전 baseline {g}일 → 현재 {c}일) — 약신호, 모니터
   - [INFO] 관측 부족: {소스 목록}
   - 이상 없으면 "이상 신호 없음"
D. 조치 제안(WARN 시): 항목별 1줄 — 수동 확인 포인트(셀렉터·소스 페이지·flag 상태).

[알림 규칙 — 과알림 방지]
- C 에 WARN 1건 이상 → 기존 운영 경고 Issue(고정 제목 "GRM Intake 운영 경고")에 요약 댓글
  누적(또는 mon-p2 라벨 Issue). INFO/이상없음만이면 스코어카드만 보고, Issue 알림 없음.

[제약]
- P1 _evaluate_health 대체 금지(보조). 읽기 전용(아무것도 수정 금지). 같은 D 면 같은 결과(결정론).
- broad search 금지: Intake DB lookback 윈도우 + Actions 이력만. 추측 금지, 확인 불가는 감점/단정 아닌 "확인 불가" 표기.
```

---

## {대상}
- 기준일 D: __________ (미지정 시 오늘 KST)
- 활성 소스 현행값 확인: repo `.github/workflows/grm-intake.yml` 의 `vars.ENABLE_*` / 저장소 변수
