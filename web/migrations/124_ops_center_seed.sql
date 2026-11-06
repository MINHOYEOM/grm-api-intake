-- ============================================================================
-- 124_ops_center_seed.sql — 운영 센터 시드: 조직도 14칸 · 팀원 15 · 목표 23 (PR2/7, 설계서 2026-11-06 부록 C)
--
-- 무엇: 122 의 표 3개(ops_team·ops_member·ops_goal)에 첫 행을 넣는다. 표·함수·권한은 하나도 만들지 않는다.
--   ops_team   실 2(종합상황실·검증실) · 부문 3(리서치·플랫폼·고객) · 팀 9 = 14칸(부록 C-1 그대로).
--   ops_member 팀원 15(부록 C-3). 이름은 사람 이름이 아니라 직무 이름(화면에서 첫 글자 원으로 그린다).
--              개발·제품전략 세션은 호출형(idle_exempt=true, D6) — 일이 없을 때 쉬는 게 정상이다.
--   ops_goal   목표 23(부록 C-2). 선(목표·주의·문제)·방향·주기는 부록 C-2 그대로.
--   ops_task·ops_decision 은 이 파일에서 넣지 않는다(slug 가 없어 같은 방식의 멱등이 안 된다 — 별도 단계).
--
-- 현재값 원칙(거짓 초록 방지, 설계서 §5 끝·R5):
--   · 값은 부록 C-2 에 출처가 적힌 것만 넣는다. 안 잰 것은 current_value·measured_at 둘 다 null → '측정 필요'.
--   · measured_at 은 now() 가 아니라 **실제 측정일**이다. 날짜만 아는 값은 그날 00:00 KST 로 적는다
--     (실제 측정 시각보다 늦게 적으면 유효 기간을 늘리는 셈이라 가장 이른 시각을 쓴다). 그래서 이 파일이
--     늦게 적용될수록 매일 목표는 일찍 '측정 필요(낡음)'로 강등된다 — 의도한 동작이다.
--   · 팀원 마지막 활동도 같다: 아는 날짜만, 모르면 null('활동 기록 없음' — 지어낸 일수를 만들지 않는다).
--
-- 멱등(두 번 실행해도 행 수 같음): 세 표 모두 `on conflict (slug) do update`.
--   정의(이름·선·주기·설명)는 이 파일 값으로 덮는다. 그러나 **값(current_value·measured_at·note)과
--   마지막 활동(last_active_at·note)은 이 파일 쪽이 더 새로울 때만 덮는다** — 적용 뒤 비서실·코드가
--   새로 잰 값을 이 시드를 다시 돌려 옛 값으로 되돌리지 않게.
--
-- 적용: 122 → 123 다음. 비서실이 SQL 검토 후 SQL 에디터에서(PR 에는 파일만).
-- 적용 후 확인(SQL 에디터):
--   select kind, count(*) from public.ops_team group by 1 order by 1;      -- division 3 · office 2 · team 9
--   select count(*) from public.ops_member;                                 -- 15
--   select count(*) from public.ops_goal;                                   -- 23
--   select count(*) filter (where current_value is null) from public.ops_goal;   -- 12 (측정 필요 · 값 없음)
--   select jsonb_pretty(private.ops_home_at(timestamptz '2026-11-06 18:00+09') -> 'summary');
--     → 문제 0 · 주의 3(글로벌번역·개발·데이터품질) · 정상 3(검증실·브리프편집·제품전략) · 측정 필요 5
--       · 놀고 있음 0 · 기록 없음 1(번역 담당) · 결정 0 · 마감 0
--   (적용 시각이 11-07 12:00 KST 를 넘으면 now() 기준으로는 매일 목표가 낡음으로 강등돼 숫자가 달라진다 — 정상.)
--
-- 되돌리기(시드 표식 컬럼 없이 slug 목록으로 · 이 파일이 넣은 행만 · 할 일·결정이 이 팀을 가리키면 먼저 지운다):
--   delete from public.ops_goal where slug in ('sr-ontime','sr-report','ver-gap','dq-clause-wl','dq-clause-mfds',
--     'dq-term-min','dq-precision','dev-ci','dev-link-streak','dev-pr-old','tr-backlog','tr-same-day',
--     'brief-ontime','brief-errors','edu-terms-week','edu-quiz','strat-adopted','infra-ingest-age',
--     'infra-deploy-fail','pr-linkedin','pr-subs','growth-channel','growth-brief-click');
--   delete from public.ops_member where slug in ('sr-brief-prep','sr-ops-weekly','ver-monthly','brief-desk',
--     'brief-reporter','brief-copyedit','tr-daily','edu-terms','strat-card-history','dev-session','dq-kg-link',
--     'infra-intake','pr-linkedin-cards','pr-subscribe-nudge','growth-analysis');
--   delete from public.ops_team where kind = 'team' and slug in ('brief','translation','education','strategy',
--     'dev','quality','infra','outreach','growth');
--   delete from public.ops_team where slug in ('situation-room','verification','research','platform','customer');
-- ============================================================================

-- ── 1. 실·부문(최상위, 상위 없음) ─────────────────────────────────────────────
-- seed:top begin
insert into public.ops_team (slug, name, kind, parent_id, sort_order, is_pilot, blurb, active)
select v.slug, v.name, v.kind, null, v.ord, false, v.blurb, true
from (values
  ('situation-room', '종합상황실', 'office',   1, '정기 작업이 제때 도는지 보고 문제를 알립니다'),
  ('verification',   '검증실',     'office',   2, '한 달에 한 번 사이트와 데이터를 점검합니다'),
  ('research',       '리서치',     'division', 3, '브리프·번역·용어 사전을 만드는 곳'),
  ('platform',       '플랫폼',     'division', 4, '코드·데이터 연결·자료 수집을 맡는 곳'),
  ('customer',       '고객',       'division', 5, '알리기와 방문 분석을 맡는 곳')
) v(slug, name, kind, ord, blurb)
on conflict (slug) do update set
  name = excluded.name, kind = excluded.kind, parent_id = excluded.parent_id,
  sort_order = excluded.sort_order, is_pilot = excluded.is_pilot, blurb = excluded.blurb, active = excluded.active;
-- seed:top end

-- ── 2. 팀(부문 아래 필수) ───────────────────────────────────────────────────
-- seed:teams begin
insert into public.ops_team (slug, name, kind, parent_id, sort_order, is_pilot, blurb, active)
select v.slug, v.name, 'team', p.id, v.ord, v.pilot, v.blurb, true
from (values
  ('brief',       '브리프편집팀',     'research', 1, false, '매주 월요일 규제 소식 브리프를 냅니다'),
  ('translation', '글로벌번역팀',     'research', 2, false, '지적사항과 브리프를 두 언어로 옮깁니다'),
  ('education',   '용어·교육팀',      'research', 3, false, '용어 사전과 주간 퀴즈를 만듭니다'),
  ('strategy',    '제품전략팀(시범)', 'platform', 1, true,  '새 기능을 시험해 보고 쓸지 정합니다'),
  ('dev',         '개발팀',           'platform', 2, false, '사이트와 자동 작업의 코드를 고칩니다'),
  ('quality',     '데이터품질팀',     'platform', 3, false, '지적사항을 조항·용어와 잇고 정확도를 지킵니다'),
  ('infra',       '수집·인프라팀',    'platform', 4, false, '각국 규제 자료를 매일 모아 저장합니다'),
  ('outreach',    '대외홍보팀',       'customer', 1, false, '링크드인 게시와 구독 안내를 맡습니다'),
  ('growth',      '성장분석팀',       'customer', 2, false, '방문자가 어디서 와서 무엇을 보는지 셉니다')
) v(slug, name, parent, ord, pilot, blurb)
join public.ops_team p on p.slug = v.parent and p.kind = 'division'
on conflict (slug) do update set
  name = excluded.name, kind = excluded.kind, parent_id = excluded.parent_id,
  sort_order = excluded.sort_order, is_pilot = excluded.is_pilot, blurb = excluded.blurb, active = excluded.active;
-- seed:teams end

-- ── 3. 팀원(직무 이름 · 마지막 활동은 아는 것만) ─────────────────────────────
-- seed:members begin
insert into public.ops_member (team_id, slug, name, kind, ref, model, state, idle_exempt, last_active_at, last_active_note)
select t.id, v.slug, v.name, v.kind, v.ref, v.model, 'active', v.exempt, v.last::timestamptz, v.note
from (values
  ('situation-room', 'sr-brief-prep',      '발행 준비 담당',  'routine', 'company/situation-room/brief-prep.md',   'haiku',  false, null,                     null),
  ('situation-room', 'sr-ops-weekly',      '운영 점검 담당',  'routine', 'company/situation-room/ops-weekly.md',   'haiku',  false, null,                     null),
  ('verification',   'ver-monthly',        '점검 담당',       'routine', 'company/verification/monthly.md',        'sonnet', false, '2026-11-03 00:00+09',   '11-03 첫 월간 점검'),
  ('brief',          'brief-desk',         '편집팀장',        'routine', 'company/editorial/',                     'sonnet', false, '2026-11-02 00:00+09',   '11-02 호 발행'),
  ('brief',          'brief-reporter',     '기자',            'routine', 'company/editorial/',                     'opus',   false, '2026-11-02 00:00+09',   '11-02 호 발행'),
  ('brief',          'brief-copyedit',     '교열 담당',       'routine', 'company/editorial/',                     'sonnet', false, '2026-11-02 00:00+09',   '11-02 호 발행'),
  ('translation',    'tr-daily',           '번역 담당',       'routine', 'company/translation/findings-daily.md',  'sonnet', false, null,                     null),
  ('education',      'edu-terms',          '용어 담당',       'routine', 'company/education/glossary-weekly.md',   'sonnet', false, '2026-11-05 00:00+09',   '11-05 용어 그림·사례 수 PR'),
  ('strategy',       'strat-card-history', '시범 기획 담당',  'session', '카드 업체 이력 시범',                    null,     true,  '2026-11-06 00:00+09',   '11-06 카드 업체 이력 시범 승인'),
  ('dev',            'dev-session',        '개발 담당',       'session', '개발 세션',                              null,     true,  '2026-11-06 00:00+09',   '11-06 운영 센터 표 PR'),
  ('quality',        'dq-kg-link',         '연결 담당',       'code',    '.github/workflows/grm-kg-link.yml',      null,     false, '2026-11-06 00:00+09',   '11-06 일일 연결 실행'),
  ('infra',          'infra-intake',       '수집 담당',       'code',    '.github/workflows/grm-intake.yml',        null,     false, '2026-11-06 08:50+09',   '11-06 08:50 적재'),
  ('outreach',       'pr-linkedin-cards',  '링크드인 담당',   'session', '링크드인 카드',                          null,     false, null,                     null),
  ('outreach',       'pr-subscribe-nudge', '구독 안내 담당',  'session', '구독 안내 메일 명단안',                  null,     false, null,                     null),
  ('growth',         'growth-analysis',    '분석 담당',       'session', '유입 경로 분석',                         null,     false, '2026-11-04 00:00+09',   '11-04 유입 경로 분석 문서')
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

-- ── 4. 목표(선은 부록 C-2 · 값은 출처 있는 것만) ─────────────────────────────
-- 열: 팀, slug, 이름, 단위, 방향, 목표, 주의선, 문제선, 현재값, 측정 시각, 주기(시간), 측정 방식, 순서, 메모
-- seed:goals begin
insert into public.ops_goal (team_id, slug, label, unit, direction, target, warn_at, bad_at,
                             current_value, measured_at, period_hours, measure_kind, measure_ref, note, sort_order, active)
select t.id, v.slug, v.label, v.unit, v.dir, v.target, v.warn, v.bad,
       v.val, v.measured::timestamptz, v.period, v.mkind, null, v.note, v.ord, true
from (values
  ('situation-room', 'sr-ontime',          '정기 작업이 제때 돈 비율',          '%',    'gte',  95,   80,   50,   null, null,                   24,  'github', 1, '값 없음. 11-06 새벽 연결 작업 지각·누락 의심. 자동 측정은 PR6'),
  ('situation-room', 'sr-report',          '문제 발생 후 대표 보고까지',        '분',   'lte',  60,   180,  null, null, null,                   168, 'manual', 2, '문제 알림 메일 코드가 아직 없어 잴 수단이 없음'),
  ('verification',   'ver-gap',            '마지막 점검 후 경과',               '일',   'lte',  31,   38,   60,   3,    '2026-11-06 00:00+09',  744, 'manual', 1, '11-03 첫 점검 기준 11-06 에 잰 값. 월 주기라 46일 넘게 안 고치면 측정 필요'),
  ('quality',        'dq-clause-wl',       '경고서한 조항 연결률',              '%',    'gte',  80,   64,   40,   77,   '2026-11-06 00:00+09',  24,  'sql',    1, '목표 초안(11-06) 값. 경고서한만 따로 세는 함수는 PR6'),
  ('quality',        'dq-clause-mfds',     '식약처 조항 연결률',                '%',    'gte',  60,   48,   30,   50.2, '2026-11-06 00:00+09',  24,  'sql',    2, '11-06 조회: 식약처 전체 2,360건 중 1,185건. 목표 초안 35%(확실한 것)와 기준이 다름(R3)'),
  ('quality',        'dq-term-min',        '용어 연결률(가장 낮은 유형 483)',   '%',    'gte',  70,   56,   35,   50.2, '2026-11-06 00:00+09',  24,  'sql',    3, '11-06 조회: 483 12,676건 기준. 다음 낮은 유형은 회수 65.6·캐나다 66.6'),
  ('quality',        'dq-precision',       '새 연결 표본 정밀도',               '%',    'gte',  98,   96,   90,   99,   '2026-11-05 00:00+09',  168, 'manual', 4, '11-05 표본 100건 대조. 연결을 늘릴 때마다 다시 잼'),
  ('dev',            'dev-ci',             '전체 테스트 시간',                  '분',   'lte',  30,   40,   45,   28,   '2026-11-06 00:00+09',  24,  'github', 1, '목표 초안(11-06) 값. 한도 45분. 자동 측정은 PR6'),
  ('dev',            'dev-link-streak',    '일일 연결 연속 성공',               '일',   'gte',  30,   7,    null, 0,    '2026-11-06 00:00+09',  24,  'github', 2, '11-06 수동 실행 1회 실패(수정 PR 진행). 문제선 없음 — 한 번 실패로 빨강이 되지 않게(D3)'),
  ('dev',            'dev-pr-old',         '3일 넘은 열린 PR',                  '건',   'lte',  0,    0,    3,    0,    '2026-11-06 00:00+09',  24,  'github', 3, '목표 초안(11-06) 값. 1건 이상이면 주의'),
  ('translation',    'tr-backlog',         '3일 넘은 미번역 잔량',              '건',   'lte',  0,    null, null, null, null,                   24,  'sql',    1, '무엇을 셀지 정하기 전(R4) — 기준이 정해지면 값을 넣는다'),
  ('translation',    'tr-same-day',        '브리프 영문판 같은 날 발행',        '',     'gte',  1,    null, null, 0,    '2026-11-02 00:00+09',  168, 'manual', 2, '11-02 호는 한글만 먼저 나가고 영문은 나중에 보충. 당일 기준이면 0 — 기준 확인 필요'),
  ('brief',          'brief-ontime',       '월요일 정시 발행',                  '',     'gte',  1,    null, null, 1,    '2026-11-02 00:00+09',  168, 'manual', 1, '11-02 발행(한글 전용 호). 발행 시각 자동 기록은 PR6 이후'),
  ('brief',          'brief-errors',       '카드 사실 오류',                    '건',   'lte',  0,    1,    3,    0,    '2026-11-03 00:00+09',  744, 'manual', 2, '11-03 수리 뒤 검증실 표본 검수 기준'),
  ('education',      'edu-terms-week',     '이번 주 용어 추가 수',              '개',   'gte',  1,    null, null, null, null,                   168, 'sql',    1, '화요일 주간 작업 첫 결과(11-10) 뒤에 값. 지금 연결된 용어 203종'),
  ('education',      'edu-quiz',           '주간 퀴즈 정시 발행',               '',     'gte',  1,    null, null, 1,    '2026-11-02 00:00+09',  168, 'manual', 2, '11-02 발행'),
  ('strategy',       'strat-adopted',      '시범 채택 건수',                    '건',   'gte',  1,    null, null, 1,    '2026-11-06 00:00+09',  744, 'manual', 1, '카드 업체 이력(11-06 대표 승인). 2개월 안에 0이면 시범 폐지'),
  ('infra',          'infra-ingest-age',   '마지막 수집 적재 후 경과',          '시간', 'lte',  36,   36,   72,   null, null,                   24,  'sql',    1, '자동 측정(PR6) 전엔 값 없음. 11-06 조회 마지막 적재 08:50 KST'),
  ('infra',          'infra-deploy-fail',  '배포 실패',                         '건',   'lte',  0,    0,    2,    null, null,                   24,  'github', 2, '자동 측정(PR6) 전엔 값 없음'),
  ('outreach',       'pr-linkedin',        '링크드인 주간 카드 게시',           '회',   'gte',  1,    null, null, null, null,                   168, 'manual', 1, '11-09 첫 7장 준비 중. 게시는 대표가 한다'),
  ('outreach',       'pr-subs',            '주간 구독자 증가',                  '명',   'gte',  null, null, null, null, null,                   168, 'manual', 2, '목표 미정 — 대표 결정(D3). Brevo 에서 손으로 잰다'),
  ('growth',         'growth-channel',     '유입 경로를 구분할 수 있는 방문 비율', '%', 'gte',  null, null, null, null, null,                   168, 'manual', 1, '카운터 11-04 설치. 1~2주 쌓인 뒤 기준을 정한다'),
  ('growth',         'growth-brief-click', '브리프 안 클릭 주간 보고',          '회',   'gte',  1,    null, null, null, null,                   168, 'manual', 2, '측정 설치 완료, 값이 쌓이는 중')
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
