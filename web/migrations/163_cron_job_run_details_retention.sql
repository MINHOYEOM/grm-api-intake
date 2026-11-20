-- 163 cron.job_run_details 보존 정책 — pg_cron 실행 기록을 7일만 남긴다(매일 00:17 UTC).
-- Max local web/migrations prefix was 161 on main(89c6dcbc). 162~165 는 같은 PR(DB 용량 3단계). Additive only —
-- 표·함수·RPC 를 만들거나 바꾸지 않는다. cron 작업 하나를 이름으로 등록할 뿐이다(같은 이름 재등록 = 갱신, 멱등).
-- 설계: _ops/reports/GRM_DB용량_확보방안_2026-11-20.md §5-2.
--
-- ★왜(2026-11-20 03:10 UTC 실측, 읽기 전용 SELECT):
--   cron.job 8개, 최근 24시간 실행 295회(20분 주기 5개 × 72 + 일 1~4회 작업들). 보존 정책이 없어 기록이 끝없이 쌓였다
--   (1단계 수동 삭제 전 11,739행 · 2.5MB). 수동 삭제 뒤 지금 2,097행(가장 오래된 행 11-13) / 표 파일 2,465,792 B.
--   7일 보존이면 상주 ~2,100행(~0.45MB)에 묶이고, 파일은 지금 크기 안에서 빈자리를 재사용한다(하루 ~0.06MB 증가 멈춤).
--   DELETE 는 파일을 줄이지 않는다 — 이미 빈 ~2MB 는 2단계 VACUUM FULL cron.job_run_details 로만 돌아온다.
--
-- ★무엇을: cron 작업 'grm-cron-history-retention' — 끝난(또는 시작한) 지 7일 넘은 기록을 지운다.
--   end_time 이 null(실행 중이었거나 기록 실패)인 행은 start_time 으로 판정한다(영영 남지 않게).
--   Supabase 문서의 권장 관례("delete from cron.job_run_details where end_time < now() - interval '7 days'")와 같다.
--
-- ★검증법(적용 뒤):
--   select jobname, schedule, active, command from cron.job where jobname = 'grm-cron-history-retention';
--   -- 다음 날 00:17 UTC 이후:
--   select min(start_time), count(*) from cron.job_run_details;   -- min 이 7일 안쪽, count ~2,100
--   select status, return_message from cron.job_run_details d join cron.job j using (jobid)
--    where j.jobname = 'grm-cron-history-retention' order by start_time desc limit 1;  -- succeeded, DELETE n
--
-- ★되돌리기: select cron.unschedule('grm-cron-history-retention');  (지운 기록은 돌아오지 않는다 — 운영 로그뿐)

create extension if not exists pg_cron;
select cron.schedule('grm-cron-history-retention', '17 0 * * *',
  $c$delete from cron.job_run_details where coalesce(end_time, start_time) < now() - interval '7 days'$c$);
