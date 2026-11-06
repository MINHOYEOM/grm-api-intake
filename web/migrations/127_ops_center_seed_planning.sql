-- ============================================================================
-- 127_ops_center_seed_planning.sql — 운영 센터 시드 추가: 경영기획실·독자지원팀 + 제품전략팀 정식 전환
--   (PR5 와 함께, 설계서 2026-11-06 부록 C 후속)
--
-- 왜: 11-06 비서실이 운영 DB 에 직접 넣은 행을 저장소 기록으로 옮긴다. 이 파일을 적용해도 운영 DB 의 행 수는
--   늘지 않아야 한다(이미 있는 slug 는 on conflict 로 같은 값을 다시 쓸 뿐).
--   ops_team   경영기획실(planning, 실 · 최상위 · 순서 0) · 독자지원팀(support, 팀 · 고객 부문 아래 · 순서 3)
--              제품전략팀(strategy) 이름 '제품전략팀' · 시범 아님(is_pilot=false) — 11-06 정식 전환
--   ops_member 경영기획 담당(plan-review) · 법무·정책 담당(plan-policy) · 문의 응대 담당(support-desk). 모두 세션.
--              마지막 활동은 모름(null — 지어낸 일수를 만들지 않는다). 호출형 표시(idle_exempt)는 기본값 false 그대로.
--   ops_goal   plan-review-gap · plan-risks-open · support-reply(값 없음 → '측정 필요') +
--              strat-adopted 이름 '채택한 아이디어 수'(선·값은 124 와 같다).
--   ops_task·ops_decision 은 넣지 않는다.
--
-- ★순서: 124 → 127. 124 는 제품전략팀 이름을 '제품전략팀(시범)'·is_pilot=true 로, strat-adopted 이름을
--   '시범 채택 건수'로 쓴다. 124 를 다시 돌리면 그 둘이 옛 값으로 돌아가므로 **124 를 다시 돌린 뒤에는 127 도 다시 돌린다**
--   (124 는 고치지 않는다 — 적용된 파일이라). tests/sql/ops_center_seed_planning.sql 이 124→127→124→127 을 본다.
--
-- 멱등(두 번 실행해도 행 수 같음): 세 표 모두 `on conflict (slug) do update` — 124 와 같은 규칙.
--   정의(이름·선·주기·설명)는 이 파일 값으로 덮고, 값(current_value·measured_at·note)과
--   마지막 활동(last_active_at·note)은 이 파일 쪽이 더 새로울 때만 덮는다.
--
-- 적용: 122 → 123 → 124 → (125·126) → 127. 비서실이 SQL 검토 후 SQL 에디터에서(PR 에는 파일만).
-- 적용 후 확인(SQL 에디터):
--   select kind, count(*) from public.ops_team group by 1 order by 1;   -- division 3 · office 3 · team 10
--   select count(*) from public.ops_member;                              -- 18
--   select count(*) from public.ops_goal;                                -- 26
--   select name, is_pilot from public.ops_team where slug = 'strategy';  -- 제품전략팀 · false
--   select label from public.ops_goal where slug = 'strat-adopted';      -- 채택한 아이디어 수
--
-- 되돌리기(이 파일이 넣은 행만 · 할 일·결정이 이 팀을 가리키면 먼저 지운다):
--   delete from public.ops_goal where slug in ('plan-review-gap','plan-risks-open','support-reply');
--   delete from public.ops_member where slug in ('plan-review','plan-policy','support-desk');
--   delete from public.ops_team where slug in ('support','planning');
--   update public.ops_team set name = '제품전략팀(시범)', is_pilot = true where slug = 'strategy';
--   update public.ops_goal set label = '시범 채택 건수' where slug = 'strat-adopted';
-- ============================================================================

-- ── 1. 실(최상위, 상위 없음) ─────────────────────────────────────────────────
-- seed:top begin
insert into public.ops_team (slug, name, kind, parent_id, sort_order, is_pilot, blurb, active)
select v.slug, v.name, v.kind, null, v.ord, false, v.blurb, true
from (values
  ('planning', '경영기획실', 'office', 0, '회사가 계속 굴러가도록 점검하고 우선순위를 제안합니다')
) v(slug, name, kind, ord, blurb)
on conflict (slug) do update set
  name = excluded.name, kind = excluded.kind, parent_id = excluded.parent_id,
  sort_order = excluded.sort_order, is_pilot = excluded.is_pilot, blurb = excluded.blurb, active = excluded.active;
-- seed:top end

-- ── 2. 팀(부문 아래 필수) — 새 팀 1 + 제품전략팀 정식 전환 ─────────────────────
-- seed:teams begin
insert into public.ops_team (slug, name, kind, parent_id, sort_order, is_pilot, blurb, active)
select v.slug, v.name, 'team', p.id, v.ord, v.pilot, v.blurb, true
from (values
  ('strategy',    '제품전략팀',       'platform', 1, false, '새 기능을 시험해 보고 쓸지 정합니다'),
  ('support',     '독자지원팀',       'customer', 3, false, '독자 문의와 제안을 받고 답합니다')
) v(slug, name, parent, ord, pilot, blurb)
join public.ops_team p on p.slug = v.parent and p.kind = 'division'
on conflict (slug) do update set
  name = excluded.name, kind = excluded.kind, parent_id = excluded.parent_id,
  sort_order = excluded.sort_order, is_pilot = excluded.is_pilot, blurb = excluded.blurb, active = excluded.active;
-- seed:teams end

-- ── 3. 팀원(직무 이름 · 마지막 활동은 모름) ──────────────────────────────────
-- seed:members begin
insert into public.ops_member (team_id, slug, name, kind, ref, model, state, idle_exempt, last_active_at, last_active_note)
select t.id, v.slug, v.name, v.kind, v.ref, v.model, 'active', v.exempt, v.last::timestamptz, v.note
from (values
  ('planning', 'plan-review',  '경영기획 담당',   'session', '주간 경영 점검(예정)',        'opus',   false, null, null),
  ('planning', 'plan-policy',  '법무·정책 담당',  'session', '처리방침·저작권 점검(예정)',  'opus',   false, null, null),
  ('support',  'support-desk', '문의 응대 담당',  'session', '문의·제안 채널(예정)',        'sonnet', false, null, null)
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

-- ── 4. 목표(선은 대표 확정 · 값은 아직 없음) + strat-adopted 이름 ─────────────────
-- 열: 팀, slug, 이름, 단위, 방향, 목표, 주의선, 문제선, 현재값, 측정 시각, 주기(시간), 측정 방식, 순서, 메모
-- seed:goals begin
insert into public.ops_goal (team_id, slug, label, unit, direction, target, warn_at, bad_at,
                             current_value, measured_at, period_hours, measure_kind, measure_ref, note, sort_order, active)
select t.id, v.slug, v.label, v.unit, v.dir, v.target, v.warn, v.bad,
       v.val, v.measured::timestamptz, v.period, v.mkind, null, v.note, v.ord, true
from (values
  ('planning', 'plan-review-gap', '마지막 경영 점검 후 경과',  '일',   'lte', 7,  10,   14,  null, null,                  168, 'manual', 1, '첫 주간 경영 점검 전 — 값 없음'),
  ('planning', 'plan-risks-open', '대응 안 된 높은 위험',      '건',   'lte', 0,  null, 1,   null, null,                  168, 'manual', 2, '위험 목록을 만들기 전 — 값 없음'),
  ('support',  'support-reply',   '문의 답변까지 걸린 시간',   '시간', 'lte', 48, 72,   168, null, null,                  168, 'manual', 1, '문의 답변 기록을 시작하기 전 — 값 없음'),
  ('strategy', 'strat-adopted',   '채택한 아이디어 수',        '건',   'gte', 1,  null, null, 1,   '2026-11-06 00:00+09', 744, 'manual', 1, '카드 업체 이력(11-06 대표 승인). 2개월 안에 0이면 시범 폐지')
) v(team, slug, label, unit, dir, target, warn, bad, val, measured, period, mkind, ord, note)
join public.ops_team t on t.slug = v.team
on conflict (slug) do update set
  team_id = excluded.team_id, label = excluded.label, unit = excluded.unit, direction = excluded.direction,
  target = excluded.target, warn_at = excluded.warn_at, bad_at = excluded.bad_at,
  period_hours = excluded.period_hours, measure_kind = excluded.measure_kind,
  sort_order = excluded.sort_order, active = excluded.active,
  -- 값·메모는 이 파일 쪽이 더 새로울 때만(적용 뒤 새로 잰 값을 옛 시드 값으로 되돌리지 않는다)
  note = case when ops_goal.measured_at is null
                or (excluded.measured_at is not null and ops_goal.measured_at <= excluded.measured_at)
              then excluded.note else ops_goal.note end,
  current_value = case when ops_goal.measured_at is null
                         or (excluded.measured_at is not null and ops_goal.measured_at <= excluded.measured_at)
                       then excluded.current_value else ops_goal.current_value end,
  measured_at = case when ops_goal.measured_at is null
                       or (excluded.measured_at is not null and ops_goal.measured_at <= excluded.measured_at)
                     then excluded.measured_at else ops_goal.measured_at end;
-- seed:goals end
