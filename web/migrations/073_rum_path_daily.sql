-- 073: RUM 착지 경로 일별 적재 — "사람들이 어느 페이지로 들어오나".
--
-- 왜: 072 는 방문 수와 유입 **출처**(구글·네이버)만 담는다. 그래서 "자료실이 실제로
-- 얼마나 유입되나" 같은 질문에 답할 수 없었고, 섹션에 투자할지 판단이 추측이 됐다.
-- 착지 페이지는 그 판단의 유일한 사실 근거다.
--
-- ★쿼리스트링은 저장하지 않는다(수집기가 경로만 남긴다). `/findings/inspector/?key=실명`
-- 처럼 URL 에 사람 이름이 실리는 경로가 있어서, 통째로 담으면 실명이 테이블에 쌓인다.
-- 경로만으로도 "어느 섹션이 유입을 받나"는 전부 답할 수 있다.
--
-- 읽기는 authenticated 뿐 · 쓰기는 service_role 뿐(072 와 동일 규칙 — 운영 지표다).

create table if not exists public.rum_path_daily (
  snap_date date not null,
  request_path text not null,
  visits integer not null check (visits >= 0),
  primary key (snap_date, request_path)
);

alter table public.rum_path_daily enable row level security;

revoke all on public.rum_path_daily from public, anon, authenticated;
grant select on public.rum_path_daily to authenticated;

drop policy if exists "signed-in can read rum paths" on public.rum_path_daily;
create policy "signed-in can read rum paths"
on public.rum_path_daily for select to authenticated using (true);
