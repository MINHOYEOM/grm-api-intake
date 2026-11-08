# 성장분석팀 · 매일 성장 일보 v2 — 모델 Haiku · 매일 08:10 KST

방문 → 검색 유입 → 구독 신청 → 확정 구독자 → 메일 클릭을 한 장으로 잇는다. 읽기 전용.
저장소는 공개라 숫자를 **저장소에 쓰지 않는다** — 보고는 이 실행의 최종 메시지로만 남긴다(대표는 claude.ai 예약작업 실행 기록에서 본다).
수집은 GitHub Actions `grm-rum-analytics.yml`(01:30·13:30 KST)이 이미 한다 — 이 작업은 판독만 한다.

## 순서
1. Supabase 커넥터 `execute_sql`(project `rfwixqqdljpmtjdlblct`, `select` 만)로 한 번:
   ```
   select public.growth_daily_v2();
   ```
2. 결과를 **손대지 말고 그대로** `/tmp/growth.json` 에 저장한다(커넥터 결과 모양 그대로 둬도 렌더러가 푼다).
3. 저장소 루트에서:
   ```
   python3 web/growth_daily.py daily --json /tmp/growth.json --out /tmp/daily.md
   ```
4. 최종 메시지 = `CHARTER.md` 3줄 → `/tmp/daily.md` 전문 → 맨 끝 `## 오늘 볼 것` 1~3줄.

## `## 오늘 볼 것` 쓰는 법 (여기만 직접 쓴다)
- 표에 있는 숫자만 쓴다. 숫자를 새로 계산하거나 고치지 않는다 — 판독 규칙은 렌더러에 있다.
- ⚠ 줄(급증·해외 쏠림·수집 안 됨)이 있으면 첫 줄은 그것. 성장으로 읽지 않는다.
- 추측은 "가설:"로 시작한다. 이미 결론 난 제안(네이버 사이트맵 재제출, sitemap lastmod, RSS 에 지적사항, 착지 맥락형 CTA)은 쓰지 않는다.
- 구독 증감의 "누가·언제"는 쓰지 않는다(스냅샷 시각으로 추론 금지 — Brevo 화면이 정답).

## 실패 처리
- RPC 가 없다는 오류(`growth_daily_v2` 미존재) → 마이그레이션 135 미적용. `select public.growth_daily_report();` 로 대신 받고 맨 위에 "v2 미적용 — 135 적용 필요" 한 줄, 그 JSON 을 그대로 요약한다.
- 렌더러 오류 → 오류 첫 줄만 적고 끝낸다(숫자를 손으로 옮겨 적지 않는다).
