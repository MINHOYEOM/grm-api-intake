-- 075: RUM 행마다 "이 값이 몇 배 추정인가"를 담는다 — 072/073 의 정밀도 계기.
--
-- 왜: Cloudflare 의 rumPageloadEventsAdaptiveGroups 는 질의가 비싸지면 표본
-- 데이터셋으로 내려간다(ABR). 그러면 sum{visits} 가 표본 간격만큼 곱해진 **추정값**
-- 으로 오고, 값이 10 의 배수로 뭉개진다. 2026-09-02 에 073(착지 경로)을 같은 쿼리에
-- 붙이면서 방문·리퍼러까지 통째로 그 데이터셋으로 내려갔는데, 화면은 그동안
-- "위 방문 표의 합계가 정확한 값입니다"라고 적고 있었다. 며칠간 거짓말이 서 있었다.
--
-- 그래서 값과 함께 **정밀도를 저장한다**. 수집기가 avg{sampleInterval} 을 같이 받아
-- 넣고, /admin 이 "전수 / 거의 전수 / 표본 추정"을 그대로 표시한다. 추측이 끼는 자리를
-- 없애는 것이 목적이라 계산이 아니라 **API 가 말한 값**만 담는다.
--
-- ★NULL 은 "미상"이다 — 기본값을 1(전수)로 두지 않는다. 이 열이 생기기 전에 적재된
-- 행은 실제로 대부분 10배 추정값인데, 기본값 1 을 주면 **가장 부정확한 값에 가장
-- 정확하다는 표식**을 붙이게 된다. 수집기의 정밀도 가드(keep_days)도 NULL 을 "무한대"
-- 로 읽어 새 수집이 항상 이기게 한다 — 미상은 덮여야 할 값이지 지켜야 할 값이 아니다.
--
-- 권한은 072/073 과 동일(읽기는 authenticated, 쓰기는 service_role) — 열만 는다.
--
-- ※072 헤더의 "수집기가 시간 단위로 받아 KST 로 재버킷한다"는 설명은 **철회한다**.
-- 시간 단위 합산이 버킷마다 반올림을 불러 방문을 3분의 1로 깎는 것이 같은 날 드러나
-- 일(date, UTC) 단위로 되돌렸는데, 072 파일의 주석만 그대로 남았다. 적용된 마이그레이션
-- 파일은 이력이라 고치지 않고 여기에 정정을 남긴다 — 현재 축은 **UTC 날짜**다.

alter table public.rum_daily
  add column if not exists sample_interval numeric check (sample_interval >= 1);

alter table public.rum_referrer_daily
  add column if not exists sample_interval numeric check (sample_interval >= 1);

alter table public.rum_path_daily
  add column if not exists sample_interval numeric check (sample_interval >= 1);

comment on column public.rum_daily.sample_interval is
  'Cloudflare 표본 간격(1=전수 · 10=10배 추정). NULL=미상(075 이전 적재분).';
comment on column public.rum_referrer_daily.sample_interval is
  'Cloudflare 표본 간격(1=전수 · 10=10배 추정). NULL=미상(075 이전 적재분).';
comment on column public.rum_path_daily.sample_interval is
  'Cloudflare 표본 간격(1=전수 · 10=10배 추정). NULL=미상(075 이전 적재분).';
