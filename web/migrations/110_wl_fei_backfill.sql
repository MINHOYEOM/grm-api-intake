-- 지식그래프 — 경고서한(WL) 지적에 FEI 를 원문 정확 파싱으로 채운다. 가산만(함수 4, 기존 RPC 반환 shape 불변).
--
-- 배경: 101 은 483 의 raw_json.fei_number 만 채워서 WL 3,594행의 fei 는 전부 NULL 이었다.
-- WL 원문(raw_signals.raw_json 본문)에는 회신·통지 안내 문장에 수신 업체 사이트의 FEI 가 적힌 편지가 있다:
--   "Identify your response with FEI 3004153045 and ATTN: ..."
--   "Your written notification should refer to Warning Letter #636636 and include FEI: 3010840309."
-- 이 두 문형(회신·통지 식별 문장)만 받는다. 본문 중 "…Limited, Goa FEI 3004081307, citing…"(과거 편지의 다른 업체),
-- "manufactured at …, FEI …"(위탁 제조소) 같은 언급은 수신 업체가 아니라서 **쓰지 않는다**.
--
-- 규칙(틀리게 잇느니 안 잇는다):
--  1) 식별 문장에서 뽑은 FEI 가 편지 안에서 정확히 1종이어야 한다(2종 이상이면 모호 → 비움).
--  2) 편지 전체의 FEI 표기(어떤 문형이든)도 1종뿐이어야 한다(다른 사이트 FEI 가 섞인 편지는 비움).
--  3) 7~10자리 숫자만(101 의 CHECK 와 동일). 앞자리 0 채움 없음. 이름 유사도는 쓰지 않는다.
--  4) fei is null 인 행만 채운다 → 멱등. 이미 채운 행·483 은 건드리지 않는다.
--
-- 적용 순서: 101·108·109 이 먼저 적용돼 있어야 한다. 적용 후 `select public.grm_backfill_wl_fei();` 1회.
-- 이후 신규 WL 은 grm_link_new_findings(108) 가 같은 *_since 함수를 불러 일일로 채운다(아래 (D)).

-- (A) 순수 파서 — 편지 본문에서 수신 업체 FEI 한 개를 돌려준다(없거나 모호하면 NULL).
create or replace function public.grm_wl_fei_from_text(p_text text)
returns text
language sql
immutable
set search_path = public
as $$
  with resp as (
    select array_agg(distinct m[1]) as a
      from regexp_matches(coalesce(p_text, ''),
             '(?:[Ii]dentify your response with|should refer to Warning Letter\s*(?:#|no\.?)?\s*[0-9]+ and include)\s+FEI\s*(?:number|no\.?|#)?\s*[:#]?\s*([0-9]{7,10})\y',
             'g') as m
  ),
  anyf as (
    select array_agg(distinct m[1]) as a
      from regexp_matches(coalesce(p_text, ''),
             'FEI\s*(?:number|no\.?|#)?\s*[:#]?\s*([0-9]{7,10})\y', 'gi') as m
  )
  select case when cardinality(resp.a) = 1 and cardinality(anyf.a) = 1 and resp.a[1] = anyf.a[1]
              then resp.a[1] end
    from resp, anyf;
$$;
revoke all on function public.grm_wl_fei_from_text(text) from public, anon, authenticated;

-- (B) 백필 — ingested_at 창 변형. 멱등(fei is null 만).
create or replace function public.grm_backfill_wl_fei_since(p_since timestamptz)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_rows integer;
begin
  update public.findings f
     set fei = x.fei
    from (
      select r.raw_signal_id,
             public.grm_wl_fei_from_text(r.raw_json) as fei
        from public.raw_signals r
       where r.source = 'FDA Warning Letter'
         and r.raw_json ~* 'FEI'
    ) x
   where f.raw_signal_id = x.raw_signal_id
     and x.fei is not null
     and f.source = 'FDA Warning Letter'
     and f.fei is null
     and f.ingested_at >= p_since;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_backfill_wl_fei_since(timestamptz) from public, anon, authenticated;

-- (C) 인자 없는 전체 백필 — 얇은 껍데기.
create or replace function public.grm_backfill_wl_fei()
returns integer
language sql
security invoker
set search_path = public
as $$ select public.grm_backfill_wl_fei_since('-infinity'::timestamptz); $$;
revoke all on function public.grm_backfill_wl_fei() from public, anon, authenticated;

-- (D) 일일 연결 진입점 — 109 본문(fei→clause→mfds_rule) 위에 WL fei 한 줄만 가산(109 선적용 전제). 반환 jsonb 는 키만 추가(기존 키 불변).
create or replace function public.grm_link_new_findings(p_days integer default 3)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_since  timestamptz := now() - make_interval(days => greatest(coalesce(p_days, 3), 1));
  v_fei    integer;
  v_wl_fei integer;
  v_clause integer;
  v_rule   integer;
begin
  v_fei := public.grm_backfill_findings_fei_since(v_since);
  v_wl_fei := public.grm_backfill_wl_fei_since(v_since);
  v_clause := public.grm_backfill_finding_clause_since(v_since);
  v_rule := public.grm_backfill_finding_clause_mfds_rule_since(v_since);
  return jsonb_build_object('since', v_since, 'fei_filled', v_fei, 'wl_fei_filled', v_wl_fei,
                            'clause_edges', v_clause, 'clause_mfds_rule', v_rule);
end;
$$;
revoke all on function public.grm_link_new_findings(integer) from public, anon, authenticated;
