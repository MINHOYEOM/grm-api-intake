-- ============================================================================
-- 129_ops_center_secretaries.sql — 운영 센터에 비서실 방과 비서 2명(티로·해밀턴)을 넣는다
--
-- 왜: 11-07 대표가 두 PC의 비서 이름을 정했다. 회사 PC = 해밀턴, 개인 PC = 티로.
--   둘은 역할이 같은 총괄 비서이고 같은 기억·같은 장부로 일한다. 그런데 운영 센터 명단에
--   정작 비서가 없어서 홈의 '비서실이 정함'·'비서실 확인함'을 누가 하는지 보이지 않았다.
--   대표 승인(11-07) 후 티로가 운영 DB에 이 파일 그대로 적용했고, 여기는 저장소 기록이다(127과 같은 방식).
--   ops_team   비서실(secretary, 실 · 최상위 · 순서 -1 = 경영기획실보다 앞)
--   ops_member 티로(sec-tiro, 개인 PC) · 해밀턴(sec-hamilton, 회사 PC). 둘 다 세션 · opus.
--              마지막 활동은 실제 기록만(티로 = 이 적용, 해밀턴 = grm-ops 11ce5a8 장부 갱신).
--   ops_goal·ops_task·ops_decision 은 넣지 않는다.
--
-- 멱등(두 번 실행해도 행 수 같음): `on conflict (slug) do update` — 124·127 과 같은 규칙.
--   마지막 활동은 이 파일 쪽이 더 새로울 때만 덮는다.
--
-- 적용: 124 → 127 → 129. 비서실이 SQL 검토 후 SQL 에디터에서.
-- 적용 후 확인(SQL 에디터):
--   select kind, count(*) from public.ops_team group by 1 order by 1;   -- division 3 · office 4 · team 10
--   select count(*) from public.ops_member;                              -- 20
--   select slug, name from public.ops_member where team_id = (select id from public.ops_team where slug = 'secretary');
--
-- 되돌리기(이 파일이 넣은 행만 · 할 일·결정이 이 팀·직원을 가리키면 먼저 지운다):
--   delete from public.ops_member where slug in ('sec-tiro','sec-hamilton');
--   delete from public.ops_team where slug = 'secretary';
-- ============================================================================

-- ── 1. 실(최상위, 상위 없음) ─────────────────────────────────────────────────
-- seed:top begin
insert into public.ops_team (slug, name, kind, parent_id, sort_order, is_pilot, blurb, active)
select v.slug, v.name, v.kind, null, v.ord, false, v.blurb, true
from (values
  ('secretary', '비서실', 'office', -1, '대표 곁에서 회사 일 전체를 맡습니다. 두 PC, 같은 기억')
) v(slug, name, kind, ord, blurb)
on conflict (slug) do update set
  name = excluded.name, kind = excluded.kind, parent_id = excluded.parent_id,
  sort_order = excluded.sort_order, is_pilot = excluded.is_pilot, blurb = excluded.blurb, active = excluded.active;
-- seed:top end

-- ── 2. 비서(이름 = 대표가 붙인 이름 · 둘은 같은 역할) ─────────────────────────
-- seed:members begin
insert into public.ops_member (team_id, slug, name, kind, ref, model, state, idle_exempt, last_active_at, last_active_note)
select t.id, v.slug, v.name, v.kind, v.ref, v.model, 'active', v.exempt, v.last::timestamptz, v.note
from (values
  ('secretary', 'sec-tiro',     '티로',   'session', '개인 PC 총괄 비서', 'opus', false, '2026-11-07 20:30+09', '11-07 개인 PC 시동·전사 조회'),
  ('secretary', 'sec-hamilton', '해밀턴', 'session', '회사 PC 총괄 비서', 'opus', false, '2026-11-07 20:13+09', '11-07 장부 갱신(두 PC 동시 사용)')
) v(team, slug, name, kind, ref, model, exempt, last, note)
join public.ops_team t on t.slug = v.team
on conflict (slug) do update set
  team_id = excluded.team_id, name = excluded.name, kind = excluded.kind, ref = excluded.ref,
  model = excluded.model, state = excluded.state, idle_exempt = excluded.idle_exempt,
  last_active_note = case when excluded.last_active_at is not null
                           and (ops_member.last_active_at is null or ops_member.last_active_at <= excluded.last_active_at)
                          then excluded.last_active_note else ops_member.last_active_note end,
  last_active_at   = case when excluded.last_active_at is not null
                           and (ops_member.last_active_at is null or ops_member.last_active_at <= excluded.last_active_at)
                          then excluded.last_active_at else ops_member.last_active_at end;
-- seed:members end
