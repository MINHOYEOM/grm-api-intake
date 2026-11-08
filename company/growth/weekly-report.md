# 성장분석팀 · 주간 검색 개선 리포트 — 모델 Haiku · 화 07:30 KST

2026-11-08 전환: 방문·유입·구독 흐름은 매일 일보 v2(`daily-report.md`)가 맡는다. 이 주간 작업은
**구글 검색에서 무엇을 고치면 클릭이 느는가**만 본다. 읽기 전용. 저장소는 공개라 숫자를 저장소에 쓰지 않는다 —
보고는 이 실행의 최종 메시지로만 남긴다.

## 순서
1. Supabase 커넥터 `execute_sql`(project `rfwixqqdljpmtjdlblct`, `select` 만)로 한 번:
   ```
   select public.gsc_search_opportunities();
   ```
   (기본 28일 창 · 표에 올리는 최소 노출 3회)
2. 결과를 그대로 `/tmp/search.json` 에 저장하고 저장소 루트에서:
   ```
   python3 web/growth_daily.py search --json /tmp/search.json --out /tmp/search.md
   ```
3. 최종 메시지 = `CHARTER.md` 3줄 → `/tmp/search.md` 전문 → `## 이번 주 고칠 것` 최대 3개.

## `## 이번 주 고칠 것` 쓰는 법
- 맨 위에 ⚠(노출 급락)가 있으면 첫 항목은 "원인 확인"이다 — 제목 고치기보다 먼저다.
- 각 항목은 `경로 — 할 일 — 근거 숫자` 한 줄. 표에 있는 페이지·숫자만 쓴다.
- 할 일의 종류는 넷 중 하나: 제목·설명 고치기(1절) · 본문 보강·내부 링크(2절) · 정규 주소 정리(3절) · 새 자료 페이지로 수요 연결(4절).
- 노출이 한 자리 수인 행은 신호가 약하다고 함께 적는다. 구글만의 데이터다(네이버는 읽기 API 가 없다).

## 실패 처리
- `gsc_search_opportunities` 미존재 → 마이그레이션 135 미적용. `select public.gsc_report();` 로 대신 받아 기회 페이지·떠오르는 검색어만 요약하고 맨 위에 "135 적용 필요" 한 줄.
- `connected=false` → "구글 검색어 연동 대기" 한 줄로 끝.
