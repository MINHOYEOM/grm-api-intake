-- 지식그래프 — 더는 쓰지 않는 findings 본문 trigram 색인(114 에서 만든 idx_findings_text_trgm)을 지운다.
-- 색인 1개 삭제만(데이터·함수·테이블 불변). 이 파일은 행을 쓰지 않는다.
--
-- 이유: 116 부터 유사 지적 계산은 고유 문장 표(finding_similar_text)의 전용 trigram 색인
-- (idx_finding_similar_text_trgm)을 쓴다. findings 쪽 색인은 114 직후 속도 측정 때 126번 쓰였을 뿐
-- 지금은 읽는 곳이 없는데, findings 행을 갱신할 때마다(FEI 채우기·번역 반영 등) 같이 갱신돼
-- 쓰기 비용만 늘린다. 11-06 일일 연결의 FEI 채우기 31건이 PostgREST 타임아웃(약 8초)을 넘겨
-- HTTP 500 으로 실패한 원인 후보 중 하나다. 다시 필요해지면 114 의 create index 문으로 되살린다.

drop index if exists public.idx_findings_text_trgm;
