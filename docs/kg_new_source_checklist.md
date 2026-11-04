# 새 유형(새 규제 소스) 추가 체크리스트 — 지식그래프 연결이 빠지지 않게

새 소스가 `findings` 에 들어오기 시작하면 아래를 **전부** 확인한다. 하나라도 비면 그 유형의 지적은 그래프에서 고아가 된다.
원칙: 정확성 최우선 · 틀린 연결 금지(모르면 비운다) · 연결마다 confidence.

## 1. 채워야 하는 키 (지적 행 `findings`)

| 키 | 누가 채우나 | 새 유형에서 할 일 | 비면 |
|---|---|---|---|
| `finding_id`, `raw_signal_id`, `document_id` | 수집 어댑터 | 문서 단위 고유 id. 카드 id 와 같은 형식이면 카드↔지적 조인이 된다 | 문서·카드 연결 불가 |
| `source`, `agency`, `document_type` | 어댑터 | 기관 코드는 `grm_findings.agency_from_source` 가 아는 값. **`document_type` 에 부서명 변형을 섞지 말 것**(경고서한처럼 수십 변형이 되면 유형 필터가 깨진다) | 유형별 점검(`kg_link_health` 의 source 행) 분류 불가 |
| `firm_name` → `firm_key` | `firm_key` 는 생성 컬럼(013) | 업체명 원문을 그대로. 표기 변종이 보이면 `grm_findings.FIRM_KEY_ALIASES` + `firm_alias` 시드(105 방식)로 묶는다. 한글 상호는 정규화 v2 가 건드리지 않는다 | 업체 노드(`issued_to`) 누락 |
| `fei` | **DB 사후 채움**(108 `grm_link_new_findings`) | 원본 `raw_signals.raw_json->>'fei_number'` 에 7~10자리 숫자가 있으면 자동. FEI 에 해당하는 사이트 번호가 다른 키(예: 다른 나라 사업장 번호)면 `grm_backfill_findings_fei_since` 의 `source = 'FDA 483'` 조건과 `raw_json` 키를 **새 규칙으로 확장**하고 형식 CHECK(`^[0-9]{7,10}$`)를 맞춘다 | 사이트 단위 연결·483→실사 판정 불가 |
| `country_key` | 생성 컬럼(055) | `site_country` 문자열이 `grm_normalize_country` 사전에 있어야 ISO 코드가 나온다. 새 표기는 사전(SQL+`grm_findings._COUNTRY_CODE_MAP`)에 추가 | 국가 축 누락 |
| `category_code` | 분류기 | 분류체계 v10 의 20종 중 하나. 새 유형에 맞는 코드가 없으면 `other_quality_system` 으로 떨어진다 — 비율을 점검 | 분류 축 오염 |
| `cfr_refs` / `mfds_refs` | 추출기 `findings_extractors` | 조항을 본문에서 뽑는 패턴. **새 법체계(예: EU GMP Annex, PMDA)를 쓰면 `grm_parse_citation`(SQL)·`kg_edges.parse_citation`(파이썬)에 분기를 같이 추가**하고 `tests/test_kg_edges.py` 픽스처에 대표 표기를 넣는다. 파서가 모르는 표기는 연결하지 않는다(낮은 confidence 로 억지 연결 금지) | 조항 엣지(`finding_clause`) 0 |
| `finding_text_ko` (번역) | 번역 배치 | 용어 엣지는 한국어 본문을 본다. 번역이 늦으면 일일 연결이 창(7일) 안에서 다시 잡는다 | 용어 엣지(`finding_term`) 지연 |
| `inspection_date`, `evidence_url` | 어댑터 | 날짜 축·원문 링크 | 시간 축·원문 연결 |
| `product_family`, `modality` | (3단계 후보) | 원본에 시설 유형·제형이 있으면 채운다 — 지금은 지적 단위 0% | 제품군 질의 불가 |

## 2. 노드·엣지 유형 (지식그래프)

- DB 엣지 테이블: `finding_clause(finding_id, citation_key, citation_paragraph, confidence, basis, raw_ref)` (106),
  `finding_term(finding_id, term_id, surface, confidence >= 0.8)` (107). 둘 다 `ignore duplicates` 멱등.
- 내보내기(`web/graph_export.py`) 노드: `doc` · `firm` · `clause` · `term` · `brief`, 엣지: `cited_in_brief` · `issued_to` · `cites_clause` · `uses_term`.
  새 유형은 보통 새 노드 유형이 아니라 **기존 `doc` 노드의 새 `agency`/`source` 값**이다. 새 노드·엣지 유형이 정말 필요하면 `NODE_TYPES`/`NODE_FIELDS` 허용목록과 `validate_graph` 가드를 먼저 확장한다.

## 3. 일일 연결과 감시가 새 유형을 잡는지

1. 일일 연결(`grm-kg-link.yml` → `kg_link_new.py`)은 **소스 이름에 의존하지 않는다** — `findings.ingested_at` 창 + 위 키만 본다. 단, `fei` 는 현재 `source = 'FDA 483'` 한정이다(위 표).
2. 감시(`kg_link_health.py` / RPC `kg_link_health`)는 `fei` 는 483, 조항·용어는 **source 별**로 센다. 새 유형이 들어오면 첫 실행에서 새 source 행이 나타나는지 본다.
3. 임계: fei 99% · 조항 99% · 용어 25%(정상 상태 약 48%). 새 유형의 정상 비율이 이보다 낮은 것이 구조적이면(예: 본문에 조항이 없는 유형) 임계를 낮추지 말고 **분모에서 빠지는 이유**(파서가 못 읽음 vs 원래 없음)를 문서화한다.

## 4. 추가 직후 검증 (읽기 전용)

```sql
select public.kg_link_health(7, 36);                       -- 새 source 행이 있고 expected/linked 가 0 이 아닌지
select citation_key, count(*) from finding_clause group by 1 order by 2 desc limit 20;
select source, count(*) filter (where fei is not null) from findings group by 1;   -- fei 가 있어야 하는 유형만
```

체크가 끝나면 이 표에 한 줄(유형·채운 키·남은 빈칸과 이유)을 PR 본문에 적는다.
