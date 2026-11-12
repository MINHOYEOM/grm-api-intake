-- 149_intake_queue.sql — 주간 대기열을 DB 로 (그림자 단계, 2026-11-12)
--
-- 왜: 지금 "이번 주 브리프에 넣을 행"과 "행별 처리 상태"는 노션 Intake DB 의 Status·Run Date 가
--     쥐고 있다. 노션을 끄려면 같은 것을 우리 DB 가 알아야 한다(재검토 2-B, 재생 대조 결과).
--
-- 무엇:
--   intake_queue    — 일일 수집이 "대기열에 넣은" 문서 1건 = 1행. 노션 Intake row 에 대응한다.
--                     과거분 백필 수집기는 여기에 쓰지 않으므로, 대기열에 넣은 적 없는 문서는
--                     handoff 에 들어가지 않는다(재생 대조의 제외 규칙 X1·X2·X4 를 구조로 대체).
--   intake_handoffs — 날짜별 handoff 1건 = 1행. 그날 넘긴 행 목록(manifest)과 상태(open·consumed·stale).
--
-- 진실의 원천: 처리 상태(state)는 main 의 발행 파일(web/data/deltas·briefs)에서 언제든 다시 계산한다.
--   이 표의 state 는 그 계산의 사본이다(재검토 2-E). 한 번 쓰기에 실패해도 다음 실행이 같은 값으로 맞춘다.
--   그래서 상태 전이를 막는 트리거·함수는 두지 않는다("다시 계산해 덮어쓰기"가 정상 동작).
--
-- 가산만 한다: 기존 표·함수·RPC 는 건드리지 않는다. 읽는 쪽은 수집 워크플로(service_role)뿐이다.

create table if not exists public.intake_queue (
  source               text        not null,
  document_id          text        not null,
  first_seen_run_date  date        not null,              -- 수집일(KST). 노션 Run Date 대응
  first_seen_at        timestamptz not null,              -- 수집 시각
  enqueued_by          text        not null default 'intake',  -- 'intake'(일일 수집) | 'seed'(초기 이관)
  state                text        not null default 'new'
                       check (state in ('new', 'processed', 'skipped', 'error', 'deferred')),
  state_reason         text        not null default '',
  handoff_date         date,                              -- 상태를 정한 handoff 날짜(없으면 아직 미소비)
  brief_publish_date   date,                              -- 실제로 실린 브리프 날짜(state=processed 일 때만)
  state_computed_at    timestamptz,
  primary key (source, document_id),
  -- 정본(raw_signals)에 없는 문서는 대기열에도 없다. 정본 저장 실패는 그날 수집의 실패로 드러나야 한다(재검토 2-F).
  foreign key (source, document_id)
    references public.raw_signals (source, document_id) on delete cascade
);

create index if not exists intake_queue_state_run_date_idx
  on public.intake_queue (state, first_seen_run_date);
create index if not exists intake_queue_handoff_date_idx
  on public.intake_queue (handoff_date);

create table if not exists public.intake_handoffs (
  handoff_date     date        primary key,               -- run_date_kst
  generated_at     timestamptz not null,
  status           text        not null default 'open'
                   check (status in ('open', 'consumed', 'stale')),
  superseded_by    date,                                  -- stale 일 때 대체한 handoff 날짜
  consumed_evidence text       not null default '',       -- consumed 근거(예: web/data/deltas/delta_2026_11_16.json)
  window_days      integer     not null,
  row_count        integer     not null,
  payload_sha256   text        not null default '',
  -- 그날 넘긴 행 목록. [{source, document_id, web_card_id, merged_into, status_hint, signal_tier, section}]
  -- 본문(카드 뼈대·원문)은 여기에 넣지 않는다 — git 파일·아티팩트가 갖는다.
  manifest         jsonb       not null default '[]'::jsonb,
  updated_at       timestamptz not null default now()
);

alter table public.intake_queue    enable row level security;
alter table public.intake_handoffs enable row level security;

-- 정책을 만들지 않는다 = anon·authenticated 전면 차단. service_role 만 읽고 쓴다.
-- `revoke from public` 만으로는 anon 권한이 남는다(10-28 실측) → 역할을 명시해 거둔다.
revoke all on public.intake_queue    from public, anon, authenticated;
revoke all on public.intake_handoffs from public, anon, authenticated;
grant select, insert, update, delete on public.intake_queue    to service_role;
grant select, insert, update, delete on public.intake_handoffs to service_role;

comment on table public.intake_queue is
  '주간 대기열 — 일일 수집이 넣은 문서와 처리 상태(상태는 main 발행 파일에서 다시 계산한 사본)';
comment on table public.intake_handoffs is
  '날짜별 handoff 기록 — 넘긴 행 목록과 open/consumed/stale';
