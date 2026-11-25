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

## 방법론 (검색 판독 — 공통 원칙은 `company/growth/daily-report.md` 의 방법론·데이터·평가 절)
출처: [coreyhaines31/marketingskills · seo-audit·ai-seo](https://github.com/coreyhaines31/marketingskills)(MIT).
- 감사 순서는 막힘 → 본문 순이다: 색인·크롤 문제(색인 빠짐·canonical 오류·사이트맵) → 제목·설명 → 본문·링크. 노출이 급락하면 제목보다 색인 원인 확인이 먼저다.
- 가장 빠른 승리는 노출이 많은데 클릭이 적은 페이지(제목·설명 재작성)와 순위 8~20 위 페이지(본문 보강·내부 링크).
- 제목은 핵심어를 앞에, 50~60자, 본문 제목과 일치. 설명은 150자 안팎. 자바스크립트로 넣는 구조화 데이터는 `curl` 에 안 보이므로 "없음"이라 단정하지 않는다.
- AI 검색 인용: 같은 질문을 3~5번 물어 "몇 번 중 몇 번"으로 기록(수동, 월 1회 20문항 안쪽). 한 번의 답을 결과로 쓰지 않는다.
뺀 것: Ahrefs·Semrush 등 유료 순위·백링크 도구.

## 실패 처리
- `gsc_search_opportunities` 미존재 → 마이그레이션 135 미적용. `select public.gsc_report();` 로 대신 받아 기회 페이지·떠오르는 검색어만 요약하고 맨 위에 "135 적용 필요" 한 줄.
- `connected=false` → "구글 검색어 연동 대기" 한 줄로 끝.
