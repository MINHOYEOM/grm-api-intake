-- 지식그래프 2순위 — 업체명 정규화 v2 (규칙 보강안). **함수 추가만** 한다.
--
-- findings.firm_key(generated stored, 013) 는 바꾸지 않는다. 013 의 grm_normalize_firm_name 도
-- 그대로다. 그래서 이 파일을 적용해도 firm_key·업체 URL·firm_watchlist·기존 RPC 의 결과가
-- 전혀 달라지지 않는다(반환 shape 불변). v2 로 전환할지는 영향 범위를 보고 별도로 결정한다.
--
-- 읽기 전용 시뮬레이션(2026-11-03, findings.firm_name 전체, 고유 firm_key 4,117):
--   v2 적용 시 고유 키 4,117 -> 4,091 (26개 키가 기존 키로 합쳐짐, 영향 행 811)
--   키 문자열이 바뀌는 키는 257개(합쳐지는 26개 포함) — 나머지 231개는 병합 없이 이름만 바뀐다
--   (and -> &, 괄호 제거, 선행 The). firm_watchlist 9건 영향 0(한글 이름은 v1 그대로).
--
-- v1 위에 더하는 규칙(순서 고정, 파이썬 grm_findings.normalize_firm_name_v2 와 파리티):
--   1) 한글이 하나라도 있는 이름은 v1 결과를 그대로 쓴다("(주)" 괄호가 지워지면 안 된다)
--   2) 괄호 구간 제거 "(...)" — 단위/별칭 표기(Ltd. (Unit VII), (CAPS))
--   3) v1 의 [.,] 제거·법인접미사 제거(Incorporated 는 아래 5 에서 후미만 처리)
--   4) ' and ' -> ' & ', '&' 주변 공백을 1칸으로
--   5) 후미 법인접미사 추가 제거: incorporated|plc|ag|bv|nv|spa|sas|sarl|kg|bhd, 이어서 sdn|pte|pty
--      ("S.p.A." 는 [.,] 제거 후 spa). 후미 한정이라 "AG Hair" 같은 앞쪽 단어는 안전하다
--   6) 선행 'the ' 제거, 끝의 공백/&/- 제거
--
-- 알려진 한계(PR 본문에 같은 내용): 괄호 제거 때문에 "Natco Pharma (Canada) Inc" 가
-- "Natco Pharma Limited" 와, "Biocon Sdn Bhd"(말레이시아)가 "Biocon Ltd" 와 같은 firm_key 가 된다.
-- firm_key 는 회사 단위이고 사이트 구분은 findings.fei(101)가 맡는 설계다.
create or replace function public.grm_normalize_firm_name_v2(p_name text)
returns text
language sql
immutable
set search_path = public
as $$
  select case
    when coalesce(p_name, '') ~ '[가-힣]' then public.grm_normalize_firm_name(p_name)
    else regexp_replace(
      regexp_replace(
        regexp_replace(
          regexp_replace(
            regexp_replace(
              regexp_replace(
                regexp_replace(
                  regexp_replace(
                    regexp_replace(
                      regexp_replace(
                        regexp_replace(
                          lower(replace(replace(replace(coalesce(p_name, ''), '&amp;', '&'), '&#039;', ''''), '&#39;', '''')),
                          '\([^)]*\)', ' ', 'g'),
                        '[.,]', '', 'g'),
                      '\y(inc|llc|ltd|co|corp|corporation|company|limited|lp|llp|pvt|private|gmbh|sa|srl|dba)\y', '', 'g'),
                    '\s+and\s+', ' & ', 'g'),
                  '\s*&\s*', ' & ', 'g'),
                '\s+', ' ', 'g'),
              '^\s+|[\s&-]+$', '', 'g'),
            '^the\s+', ''),
          '\s+(incorporated|plc|ag|bv|nv|spa|sas|sarl|kg|bhd)$', ''),
        '\s+(sdn|pte|pty)$', ''),
      '[\s&-]+$', '')
  end;
$$;

revoke all on function public.grm_normalize_firm_name_v2(text) from public, anon, authenticated;
