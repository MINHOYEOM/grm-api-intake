-- 157 공개 RPC 4개에 함수 단위 statement_timeout 8s — 식은 첫 호출이 anon 3초 벽에서 500 이 되지 않게.
-- Max local web/migrations prefix was 156 on main cb5880f7. Additive only — 함수 본문·시그니처·OID·권한·
-- 반환 JSON 바이트 불변. 바뀌는 것은 pg_proc.proconfig 에 statement_timeout=8s 한 항목뿐이다.
--
-- ★왜(2026-11-13 재측정, 읽기 전용 — _ops/reports/GRM_공개RPC_시간초과_진단_2026-11-13.md 후속):
--   · edge_logs 24시간(11-12 04:50 ~ 11-13 04:50 UTC) 500: findings_search 29 · /rest/v1/findings 9(사내) ·
--     similar_to 4(GH 감시기) · checklist 2(GH 감시기) · inspector_profile 2(둘 다 152 적용 전 11:00 UTC,
--     적용 뒤 0). 500 origin 시간은 전부 3.2~4.8 s = anon statement_timeout 3s(57014).
--   · 브라우저 findings_search 500 은 거의 전부 "식은 첫 호출"이다: 같은 IP 가 4초 안팎 뒤 같은 요청을
--     다시 보내면 2.4~2.9 s 로 성공(11-12 16:35 · 11-13 01:10 · 02:55 등). 계획 문제가 아니다.
--   · 식은 페이지 재현(postgres, findings 본문 detoast 전량): 직전 호출 2분 뒤 2,814 ms · 90초 유휴 뒤
--     3,860 ms · 데움 210~500 ms. CPU 대조(generate_series 150만) 380~423 ms 로 일정 — 메모리 압박.
--   · "주기적으로 데워 두기"(pg_cron 15초) 기각: 15초 간격으로 7회 읽었는데 3회째 3,446 ms·7회째
--     1,279 ms 로 다시 식었다. 데워 두기가 식는 것을 막지 못하고 CPU 만 쓴다.
--   · 153 진단안 §C(트라이그램 GIN)는 인덱스 15~30 MB(추정)가 같은 메모리·용량(357/500 MB)을 다툰다 —
--     보류 유지. 152 §B(체크리스트 캐시)는 새 함수 anon grant 결정 대기 — 이 파일은 손대지 않는다.
--
-- ★무엇을: Supabase 문서(guides/database/postgres/timeouts "Function level")대로, REST API(PostgREST)는
--   호출하는 함수의 `set statement_timeout` 을 그 요청 트랜잭션의 제한으로 쓴다(역할 설정 anon 3s 보다 우선).
--   방문자 화면이 부르는 공개 RPC 중 지난 이틀 57014 가 난 4개에만 8s 를 건다 — authenticated 역할과 같은 값.
--     findings_search            — 검색(086 래퍼). 캐시 미적중이면 compute 가 findings 전량을 읽는다.
--     findings_checklist         — 체크리스트(캐시 없음, 식음 4.2 s 실측).
--     findings_similar_to        — 유사 지적(092 래퍼). 캐시 밖 id 는 compute(식음 5.4 s).
--     findings_inspector_profile — 152 래퍼. 캐시 키 밖(별칭) 입력은 compute 폴백(식음 5.6 s).
--   이 함수들 안에서 부르는 *_compute 는 같은 요청 트랜잭션 안이라 따로 걸 필요가 없다.
--   anon 이 *_compute 를 REST 로 직접 부르는 길(일부는 실행 권한이 남아 있다)은 종전처럼 3s 다 — 화면은
--   그 길을 쓰지 않는다.
--
-- ★결과: 식은 첫 호출(실측 3.2~5.8 s, 최악 8.6 s 는 사내 REST 일괄 조회)이 "500" 대신 "느리지만 성공"이 된다.
--   8 s 를 넘는 호출은 종전처럼 500 — 화면 재시도(같은 PR 의 findings.js·trends.js·checklist.js)가 받는다.
--   대가: 느린 질의가 겹치면 최대 8 s 동안 연결·메모리를 쥔다. 사이트 동시 방문이 드물어(24h 브라우저 검색
--   수십 건) 겹칠 확률은 낮다고 본다 — 적용 뒤 하루 edge_logs 로 확인한다(아래).
--   ※ 진단 §6-2(anon 전역 3s 상향)는 대표 판단 항목이었다. 이 파일은 그것을 공개 RPC 4개로 좁힌 판이며,
--     적용 여부는 대표 판단이다.
--
-- ★함수 SET 절의 부작용: 함수 정의에 SET 절이 있으면 PostgreSQL 은 그 함수를 호출자 질의에 인라인하지 않는다.
--   네 함수는 이미 `set search_path` 절이 있어 인라인되지 않고 있었다 — 계획 변화 없음.
--
-- ── 적용 순서(DB 적용은 머지 뒤 비서실, 대표 승인 뒤) ────────────────────────────────────────
--   0) 적용 **전** 동치 기준 캡처 — 이 파일은 본문을 바꾸지 않으므로 바이트가 같아야 한다(대조는 사고 방지용).
--      postgres 로(SQL 편집기·MCP):
--        select md5(public.findings_search()::text)                                     as s_default,
--               md5(public.findings_search_compute(p_q := 'CAPA')::text)                as s_capa,
--               md5(public.findings_checklist(array['211.192','211.22'], 2)::text)      as checklist,
--               md5(coalesce(public.findings_inspector_profile('ashar p parikh')::text, '')) as insp,
--               md5(coalesce(public.findings_similar_to(
--                     (select finding_id from public.findings_similar_cache order by finding_id limit 1), 6)::text, '')) as sim;
--      anon 키 PostgREST 로(086 기법, 같은 파이프라인으로 해시 — 092 줄바꿈 1자 오경보 전례):
--        POST /rest/v1/rpc/findings_search {} · {"p_q":"CAPA","p_text_only":true,"p_page":1,"p_docs_per_page":24}
--        POST /rest/v1/rpc/findings_checklist {"p_sections":["211.192","211.22"],"p_examples":2}
--        POST /rest/v1/rpc/findings_inspector_profile {"p_inspector_key":"ashar p parikh"}
--        각 응답 본문 md5 를 저장.
--   1) 이 파일 적용(트랜잭션 하나).
--   2) PostgREST 가 함수 설정을 다시 읽게: notify pgrst, 'reload schema';
--   3) 0) 과 같은 질의·요청을 다시 보내 md5 가 모두 같은지 대조.
--   4) 설정 확인:
--        select p.oid::regprocedure, p.proconfig from pg_proc p
--         where p.pronamespace = 'public'::regnamespace
--           and p.proname in ('findings_search','findings_checklist','findings_similar_to','findings_inspector_profile');
--      → 네 줄 모두 proconfig 에 'statement_timeout=8s' 와 종전 search_path 가 함께 있어야 한다.
--   5) 실효 확인(하루 뒤, query_logs edge_logs): 공개 RPC 4개의 500 수(이번 기준 29·2·4·0)와
--      origin_time 3,000~8,000 ms 인 200 응답 수. 3초를 넘는 200 이 보이면 함수 설정이 먹은 것이다.
--      3초 근처 500 이 그대로면 PostgREST 가 함수 설정을 안 읽는 것 → 되돌리고 보고.
--
-- ── 되돌리기(본문·권한 그대로, 설정 한 항목만 지운다) ─────────────────────────────────────────
--   alter function public.findings_search(text,text,text,text,text,text,text,text,integer,integer,text,text,boolean)
--     reset statement_timeout;
--   alter function public.findings_checklist(text[], integer) reset statement_timeout;
--   alter function public.findings_similar_to(text, integer) reset statement_timeout;
--   alter function public.findings_inspector_profile(text) reset statement_timeout;
--   notify pgrst, 'reload schema';

alter function public.findings_search(
  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean)
  set statement_timeout = '8s';

alter function public.findings_checklist(text[], integer)
  set statement_timeout = '8s';

alter function public.findings_similar_to(text, integer)
  set statement_timeout = '8s';

alter function public.findings_inspector_profile(text)
  set statement_timeout = '8s';

notify pgrst, 'reload schema';
