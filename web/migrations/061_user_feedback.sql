-- 061: 문의 및 제안(사용자 피드백) — 방문자가 이용 불편·오류/수정 요청·기능 제안을 남기는 채널.
-- 쓰기 경로: anon 은 RPC(feedback_submit)로만 쓴다(테이블 직접 insert 불가) — 060 funnel_bump
-- 관례 동형. 허용 범위는 코드가 아니라 테이블 CHECK 가 최종 게이트.
-- 읽기·상태 갱신: 관리자(private.is_admin() — 하드닝 20260705033033 이후 public 버전은
-- 폐기됐다)만 — 자유 텍스트·이메일이 실리므로 공개 읽기 금지.
-- 무차별 유입 방벽: 전역 플러드 캡(10분 30건) — 개인 식별 없이 걸 수 있는 최소 상한.
--
-- ★개인정보(이메일)는 **회신 동의가 있을 때만** 저장한다 — 동의 없이 들어온 이메일은
--   RPC 가 버린다(폼이 막더라도 DB 가 최종 방어선). 접수번호(ticket)를 돌려주어 사용자가
--   자기 접수를 지칭할 수 있게 한다.

create table if not exists public.user_feedback (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  category text not null
    check (category in ('usability','correction','feature','other')),
  message text not null
    check (char_length(message) between 5 and 2000),
  email text
    check (email is null or (char_length(email) <= 200 and email like '%_@_%')),
  -- 회신용 이메일 수집·이용 동의(개인정보보호법) — 이메일이 있으면 동의도 반드시 참.
  contact_consent boolean not null default false,
  constraint user_feedback_email_needs_consent
    check (email is null or contact_consent),
  page_path text
    check (page_path is null or char_length(page_path) <= 300),
  user_agent text
    check (user_agent is null or char_length(user_agent) <= 400),
  viewport text
    check (viewport is null or char_length(viewport) <= 40),
  -- 운영자 브라우저(localStorage 'grm-op')·비프로덕션 호스트(프리뷰) 제출 표식 —
  -- RUM/깔때기의 운영자 제외(#763~#765)와 같은 이유: 판독 시 잡음을 분리한다.
  is_operator boolean not null default false,
  status text not null default 'new'
    check (status in ('new','in_progress','done','dismissed'))
);

create index if not exists user_feedback_status_created_idx
  on public.user_feedback (status, created_at desc);

alter table public.user_feedback enable row level security;

revoke all on public.user_feedback from public;
revoke all on public.user_feedback from anon;
revoke all on public.user_feedback from authenticated;
-- 컬럼 한정 update(status) — 관리자 트리아지는 상태만 바꾼다(본문 불변).
grant select on public.user_feedback to authenticated;
grant update (status) on public.user_feedback to authenticated;

drop policy if exists "admins read feedback" on public.user_feedback;
create policy "admins read feedback"
on public.user_feedback
for select
to authenticated
using (private.is_admin());

drop policy if exists "admins update feedback status" on public.user_feedback;
create policy "admins update feedback status"
on public.user_feedback
for update
to authenticated
using (private.is_admin())
with check (private.is_admin());

-- 반환형이 void → bigint 로 바뀌므로 교체 전 drop 이 필요하다(create or replace 불가).
drop function if exists public.feedback_submit(text, text, text, text, text, boolean);
drop function if exists public.feedback_submit(text, text, text, text, text, text, boolean, boolean);

create function public.feedback_submit(
  p_category text,
  p_message text,
  p_email text default null,
  p_consent boolean default false,
  p_page text default null,
  p_ua text default null,
  p_viewport text default null,
  p_operator boolean default false
)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
  v_msg text := btrim(coalesce(p_message, ''));
  v_email text := nullif(btrim(coalesce(p_email, '')), '');
  v_id bigint;
begin
  -- 모르는 값은 실패(폴백 금지) — 호출부 오타가 조용히 새 분류를 만들지 않게(060 관례).
  if p_category not in ('usability','correction','feature','other') then
    raise exception 'feedback_submit: unknown category %', p_category;
  end if;
  if char_length(v_msg) < 5 or char_length(v_msg) > 2000 then
    raise exception 'feedback_submit: message length out of range';
  end if;
  -- 동의 없는 이메일은 거부가 아니라 **폐기**한다 — 본문(사용자가 남기려던 것)은 살리고
  -- 개인정보만 버린다. 폼이 동의를 강제하지만 DB 가 최종 방어선이다.
  if v_email is not null and not coalesce(p_consent, false) then
    v_email := null;
  end if;
  if v_email is not null and (char_length(v_email) > 200 or v_email not like '%_@_%') then
    raise exception 'feedback_submit: invalid email';
  end if;
  if (select count(*) from public.user_feedback
      where created_at > now() - interval '10 minutes') >= 30 then
    raise exception 'feedback_submit: rate limited';
  end if;
  -- page/ua/viewport 는 진단 보조 필드 — 거부 대신 절단(사용자 입력이 아니라 브라우저 파생값).
  insert into public.user_feedback (category, message, email, contact_consent,
                                    page_path, user_agent, viewport, is_operator)
  values (p_category, v_msg, v_email, v_email is not null,
          left(p_page, 300), left(p_ua, 400), left(p_viewport, 40),
          coalesce(p_operator, false))
  returning id into v_id;
  return v_id;
end;
$$;

revoke all on function public.feedback_submit(text, text, text, boolean, text, text, text, boolean) from public;
revoke all on function public.feedback_submit(text, text, text, boolean, text, text, text, boolean) from anon;
revoke all on function public.feedback_submit(text, text, text, boolean, text, text, text, boolean) from authenticated;
grant execute on function public.feedback_submit(text, text, text, boolean, text, text, text, boolean) to anon, authenticated;
