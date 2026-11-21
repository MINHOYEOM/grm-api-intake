# 성장분석팀 · 매일 성장 일보 v2 — 모델 Haiku · 매일 08:10 KST

방문 → 검색 유입 → 구독 신청 → 확정 구독자 → 메일 클릭을 한 장으로 잇는다. 읽기 전용.
저장소는 공개라 숫자를 **저장소에 쓰지 않는다** — 보고는 이 실행의 최종 메시지로만 남긴다(대표는 claude.ai 예약작업 실행 기록에서 본다).
수집은 GitHub Actions `grm-rum-analytics.yml`(01:30·13:30 KST)이 이미 한다 — 이 작업은 판독만 한다.

## 순서
1. Supabase 커넥터 `execute_sql`(project `rfwixqqdljpmtjdlblct`, `select` 만)로 한 번:
   ```
   select public.growth_daily_v2();
   ```
   이어서 한 번 더(본문 속 구독 칸 제출 — 위 결과의 '구독 신청'에 들어 있지 않다, 161):
   ```
   select public.funnel_inline_report();
   ```
2. 두 결과를 **손대지 말고 그대로** `/tmp/growth.json`·`/tmp/inline.json` 에 저장한다(커넥터 결과 모양 그대로 둬도 렌더러가 푼다).
3. 저장소 루트에서:
   ```
   python3 web/growth_daily.py daily --json /tmp/growth.json --inline /tmp/inline.json --out /tmp/daily.md
   ```
4. 최종 메시지 = `CHARTER.md` 3줄 → `/tmp/daily.md` 전문 → 맨 끝 `## 오늘 볼 것` 1~3줄.

## `## 오늘 볼 것` 쓰는 법 (여기만 직접 쓴다)
- 표에 있는 숫자만 쓴다. 숫자를 새로 계산하거나 고치지 않는다 — 판독 규칙은 렌더러에 있다.
- ⚠ 줄(급증·해외 쏠림·수집 안 됨)이 있으면 첫 줄은 그것. 성장으로 읽지 않는다.
- 추측은 "가설:"로 시작한다. 이미 결론 난 제안(네이버 사이트맵 재제출, sitemap lastmod, RSS 에 지적사항, 착지 맥락형 CTA)은 쓰지 않는다.
- 구독 증감의 "누가·언제"는 쓰지 않는다(스냅샷 시각으로 추론 금지 — Brevo 화면이 정답).

## 방법론 (공개 교본에서 우리에게 맞는 부분만 — 일보·주간 공통)
출처: [coreyhaines31/marketingskills · analytics](https://github.com/coreyhaines31/marketingskills)(MIT) ·
[anthropics/knowledge-work-plugins · product-management `metrics-review`](https://github.com/anthropics/knowledge-work-plugins/tree/main/product-management)(Apache-2.0) ·
[같은 저장소 marketing `performance-report`](https://github.com/anthropics/knowledge-work-plugins/tree/main/marketing).
- 비교 창은 같게: 분자·분모가 같은 닫힌 기간이어야 한다. 아직 끝나지 않은 기간(오늘 부분 집계, 막 가입한 사람)은 "대기"로 두고 실패·하락으로 세지 않는다.
- 합계는 손이 아니라 코드(렌더러)가 낸다. 숫자는 두 번째 길로 확인한다(다른 묶음의 합과 맞는지). 한 주·한 캠페인에 관한 말은 그 구간 자신의 숫자로 쓴다.
- 작은 요동은 소음이다. 이전 기간·목표와 견주어 "급증·급락·새 신호"만 말하고 평소 흔들림은 적지 않는다. 오늘의 한 줄은 가장 중요한 발견 하나로 시작한다.
- 상관을 원인이라 하지 않는다 — 원인 추측은 "가설:"로. 한 번 오른 것은 4주 흐름을 보고 말한다.
- 출처 이름 흩어짐: 같은 출처가 철자·대소문자로 갈라지는지 본다(예: 링크드인 referrer 변형). AI 검색 유입은 referrer 로 잡히는 만큼이 하한일 뿐이다.
- 모든 보고는 행동 하나로 끝낸다("오늘 볼 것"·"이번 주 고칠 것"). 행동이 없으면 정상 한 줄.
뺀 것: Amplitude·Mixpanel·GA4 설정, 유료 어트리뷰션·AI 인용 추적 도구, 업계 평균(열람률 등) 비교(구독자 수십 명 규모에선 의미 없음), 사분면·예측 모형(자료 12점 미만은 낮은 확신).

## 데이터 (읽을 곳, 전부 읽기 전용)
| 무엇을 보나 | 어디서 | 비고 |
|---|---|---|
| 일보 한 장 | `public.growth_daily_v2()` (135) · `public.funnel_inline_report()` (161) | 렌더 `web/growth_daily.py` |
| 방문·유입·국가·기기 | `rum_daily_clean`·`rum_path_daily_clean`·`rum_referrer_daily_clean`·`rum_country_daily_clean`·`rum_device_daily_clean` (139) | 봇 급증 거른 보기. 봇 판정 요약은 `public.rum_anomaly_summary()` · 보기 `rum_anomaly_days`·`rum_anomaly_paths` |
| 구독자 수 스냅샷 | 표 `newsletter_subscribers_daily` (077) | 증감의 누가·언제 추론 금지 |
| 구글 검색 | `public.gsc_search_opportunities()` · `public.gsc_report()` (078) | 주간 리포트 |
| 메일 반응 | `public.newsletter_campaigns_report()` (090) | 캠페인 단위 |
| 수집이 돌았나 | `.github/workflows/grm-rum-analytics.yml` 실행 기록 | 숫자가 안 바뀌면 먼저 여기 |

## 평가
- 성과 지표(1~2개): ① 보고 때문에 실제로 정해진 행동(마케팅 실행안·수정·원인 확인)의 수 — 4주 합 ② 오경보·놓침 수(⚠ 를 성장으로 읽거나, 급변을 놓친 횟수). 보고가 행동을 안 낳으면 보고 자체를 줄인다.
- 첫 달 점검 질문: 일보 `오늘 볼 것` 중 대표나 마케팅팀이 실제로 쓴 줄은? 매일 같은 말을 반복한 줄은? 렌더러가 틀린 것을 사람이 손으로 고친 적은? 수집 지연·누락을 보고가 먼저 알렸나, 대표가 먼저 알았나?
- 2개월 판정: 두 달간 일보 행동이 거의 없으면 매일을 주 2~3회로 줄이거나 ⚠ 가 있을 때만 길게 쓰는 형식으로 바꾼다(대표 결정). 급변을 놓친 일이 있으면 그 지표를 일보 맨 위로 올린다.

## 실패 처리
- RPC 가 없다는 오류(`growth_daily_v2` 미존재) → 마이그레이션 135 미적용. `select public.growth_daily_report();` 로 대신 받고 맨 위에 "v2 미적용 — 135 적용 필요" 한 줄, 그 JSON 을 그대로 요약한다.
- `funnel_inline_report` 가 없다는 오류 → 마이그레이션 161 미적용. `--inline` 을 빼고 렌더한다(본문 구독 칸 줄만 빠진다). 맨 위에 "161 미적용" 한 줄.
- 렌더러 오류 → 오류 첫 줄만 적고 끝낸다(숫자를 손으로 옮겨 적지 않는다).
