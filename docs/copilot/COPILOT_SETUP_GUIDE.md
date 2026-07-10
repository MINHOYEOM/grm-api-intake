# Copilot Studio 커넥터 셋업 가이드 (GRM Findings 조회)

사내 Microsoft Copilot(Copilot Studio 에이전트)에서 "작년 FDA 무균 관련 최다 지적이 뭐야?" 같은 질문을 하면 GRM 규제 지적사항 DB를 조회해 답하도록 연결하는 절차다. 코드 작성 없이 화면 클릭만으로 진행한다. 스크린샷 대신 메뉴 경로를 정확한 명칭으로 적었다 — Power Platform은 화면이 자주 갱신되므로 명칭이 조금 달라 보이면 가장 비슷한 항목을 선택하면 된다.

전제: 사내 M365 테넌트에서 Copilot Studio·Power Platform 커스텀 커넥터 생성 권한이 있어야 한다(F0-1 확인 완료).

## 목차

1. 사전 준비 — anon 키 확보
2. 커스텀 커넥터 생성 + swagger 임포트
3. API Key 보안 설정 + Authorization 헤더 정책 추가
4. 커넥터 테스트(findings_stats 스모크)
5. Copilot Studio 에이전트 생성 + 액션 연결
6. 에이전트 지침(instructions) 작성 — 카테고리 매핑표 포함
7. 트러블슈팅

---

## 1. 사전 준비 — anon 키 확보

이 커넥터는 Supabase anon(publishable) 키 하나만 있으면 된다. 쓰기 권한이 전혀 없고 RLS(행 단위 보안)로 국문 번역이 끝난 데이터만 노출되므로 유출돼도 데이터가 새는 것은 아니지만, 관리 관례상 비공개로 취급한다(채팅·문서에 값 자체를 남기지 말 것).

- 키를 이미 아는 사람(관리자)에게 요청하거나,
- 직접 발급 확인이 필요하면 Supabase Dashboard → 해당 프로젝트 → **Settings → API** → **Project API keys** 섹션의 `anon` `public` 키를 확인한다.
- Project URL(= `https://rfwixqqdljpmtjdlblct.supabase.co`)도 같은 화면에 있다. 이 값은 `docs/copilot/grm_findings_connector.swagger.json`의 `host`에 이미 반영돼 있으므로 별도 입력이 필요 없다.

키 값은 3단계(정책)와 5단계(연결 생성)에서 딱 두 번만 붙여넣으면 된다.

## 2. 커스텀 커넥터 생성 + swagger 임포트

1. 브라우저에서 **make.powerapps.com** 접속 → 사내 M365 계정으로 로그인.
2. 왼쪽 메뉴에서 **데이터(Data) → 사용자 지정 커넥터(Custom connectors)**.
3. **+ 새 사용자 지정 커넥터(New custom connector) → OpenAPI 파일 가져오기(Import an OpenAPI file)** 선택.
4. 커넥터 이름 입력(예: `GRM Findings`) → 파일 선택 대화상자에서 `docs/copilot/grm_findings_connector.swagger.json` 업로드 → **계속(Continue)**.
   - ※ 이 파일은 **Swagger 2.0(OpenAPI v2)** 형식이다. Power Platform 커스텀 커넥터 임포트는 Swagger 2.0만 지원하므로(OpenAPI 3.x는 임포트 마법사가 인식하지 못하거나 파라미터를 잘못 해석한다) 다른 형식으로 변환하지 말고 이 파일을 그대로 올린다.
5. 임포트가 끝나면 **일반(General) → 보안(Security) → 정의(Definition) → 테스트(Test)** 4개 탭이 나타난다. 일반 탭에서 호스트(Host)가 `rfwixqqdljpmtjdlblct.supabase.co`로 자동 채워졌는지 확인한다.
6. 정의(Definition) 탭에서 4개 작업(operation)이 보이는지 확인: `findingsStats`, `findingsFirmStats`, `findingsCategoryMatrix`, `findingsList`.

## 3. API Key 보안 설정 + Authorization 헤더 정책 추가

Supabase PostgREST는 요청마다 헤더 2개(`apikey`, `Authorization: Bearer <같은 키>`)를 요구한다. Power Platform의 "API 키(API Key)" 인증 방식은 커넥터당 헤더 1개만 받을 수 있어서, `apikey`는 보안 탭에서 정식 인증으로 설정하고 `Authorization`은 별도 정책으로 고정 추가한다.

1. **보안(Security)** 탭 → 인증 유형(Authentication type)에서 **API 키(API Key)** 선택(스펙에 이미 `apikey` 헤더로 정의돼 있으므로 자동 인식된다).
   - 매개변수 레이블(Parameter label): 예 `apikey`
   - 매개변수 이름(Parameter name): `apikey` (스펙과 일치해야 함)
   - 매개변수 위치(Parameter location): **헤더(Header)**
2. **정의(Definition)** 탭 상단(또는 작업 목록 옆)의 **정책(Policy templates)** 섹션 → **+ 새 정책(New policy)**.
3. 템플릿 목록에서 **Set HTTP Header** 선택.
4. 설정값:
   - 헤더 이름(Header name): `Authorization`
   - 헤더 값(Header value): `Bearer <anon 키 값을 그대로 붙여넣기>` (앞의 `Bearer ` 공백 포함, 그 뒤에 키를 붙인다)
   - 적용 대상(Apply to): 요청(Request)
   - 작업(Operations): 전체 작업(모든 작업에 적용 — 4개 다 헤더가 필요하다)
5. **업데이트 커넥터(Update connector)** 클릭해 저장.

이제 이 커넥터로 나가는 모든 요청에 `apikey`(보안 탭 인증) + `Authorization: Bearer ...`(정책) 두 헤더가 함께 실려 나간다.

## 4. 커넥터 테스트(findings_stats 스모크)

1. **테스트(Test)** 탭으로 이동.
2. 아직 연결(Connection)이 없으면 **+ 새 연결(New connection)** 클릭 → API 키 입력창에 anon 키를 붙여넣고 **연결 만들기(Create)**.
3. 작업 선택 드롭다운에서 `findingsStats` 선택.
4. 요청 본문(Body)에 `{}` 입력 → **작업 테스트(Test operation)** 클릭.
5. 응답 코드 200과 함께 `totals`, `by_agency_category`, `by_month`, `by_source`, `by_evidence`, `top_firms` 키가 있는 JSON이 오면 성공이다.
   - `totals.findings`가 0보다 큰 숫자인지 확인 — 0이면 8단계 배선이 아니라 DB 쪽(또는 잘못된 host) 문제다.
6. 시간이 있으면 `findingsCategoryMatrix`(본문 `{}`)와 `findingsList`(select 파라미터에 `finding_id,firm_name,category_code`만 넣고 limit=5)도 같은 방식으로 한 번씩 테스트한다.

## 5. Copilot Studio 에이전트 생성 + 액션 연결

1. **copilotstudio.microsoft.com** 접속(또는 Teams의 Copilot Studio 앱) → 사내 계정 로그인.
2. **만들기(Create) → 새 에이전트(New agent)**.
3. 이름/설명 입력(예: `GRM 규제 지적사항 도우미`) → **건너뛰고 구성으로 이동(Skip to configure)** 또는 마법사 완료 후 편집 화면으로 이동.
4. 왼쪽 **도구(Tools)** 탭 → **+ 도구 추가(Add a tool)** → **새 도구(New tool) → 커넥터(Connector)**.
5. 2단계에서 만든 `GRM Findings` 커넥터를 검색해 선택 → 연결이 없으면 이 화면에서 4단계와 동일하게 anon 키로 연결 생성.
6. 노출할 작업 4개(`findingsStats`, `findingsFirmStats`, `findingsCategoryMatrix`, `findingsList`)를 전부 체크 → 추가.
7. 저장 후 도구 목록에 4개 액션이 보이면 연결 완료.

## 6. 에이전트 지침(instructions) 작성

**지침(Instructions)** 탭에 아래 내용을 반영해 붙여넣는다(문구는 상황에 맞게 조정 가능하되, 굵게 표시한 grounding 규칙 3가지는 반드시 포함한다).

```
당신은 GRM(Global Regulatory Monitor) 규제 지적사항 DB를 조회해 답하는 사내 QA 보조 에이전트다.

사용 가능한 액션:
- findingsStats: 전체 집계(기관별/카테고리별/월별/소스별/증거등급별/업체 상위 목록)가 필요할 때
- findingsFirmStats: 특정 업체 1곳의 이력이 필요할 때(p_firm은 정확한 업체명이어야 함)
- findingsCategoryMatrix: 카테고리 x 연도 추이/매트릭스가 필요할 때
- findingsList: 개별 지적사항의 실제 텍스트·근거 링크를 인용해야 할 때(국문 번역이 끝난 사례만 조회됨)

**그라운딩 규칙(반드시 지킬 것):**
1. 수치·건수·순위는 반드시 액션 호출 결과에서 그대로 인용한다. 액션을 호출하지 않고 추측한 숫자를 답하지 않는다.
2. 개별 지적사항 원문을 인용할 때는 finding_text_ko(국문)를 인용하고, 반드시 evidence_url을 함께 제시한다.
3. findingsList 결과가 빈 배열이면 "해당 조건의 국문 번역 완료 사례가 없습니다"라고 정직하게 답한다 — 없는데 있다고 지어내지 않는다. 집계 질문(건수)은 findingsStats/findingsCategoryMatrix로 대체 답변할 수 있음을 안내한다.

카테고리 코드 <-> 한국어 라벨 매핑(20종, category_code 파라미터에 쓸 값):
| code | 한국어 라벨 |
|---|---|
| data_integrity | 데이터 완전성 |
| documentation_records | 문서화/기록관리 |
| aseptic_sterility_assurance | 무균보증/무균공정 |
| environmental_monitoring | 환경모니터링 |
| cleaning_validation | 세척밸리데이션 |
| deviation_capa | 일탈/CAPA/조사 |
| quality_unit_oversight | 품질부서 관리감독 |
| qc_lab_controls | 시험실/품질관리 |
| process_validation | 공정밸리데이션 |
| equipment_facility | 설비/시설 |
| material_supplier_control | 원자재/공급업체 관리 |
| contamination_control | 오염/교차오염 관리 |
| validation_qualification | 밸리데이션/적격성평가 |
| complaint_recall | 불만/회수 |
| stability_storage | 안정성/보관 |
| computer_system_validation | 컴퓨터화시스템 |
| labeling_packaging | 표시/포장 |
| regulatory_reporting | 규제보고/변경관리 |
| training_personnel | 교육/작업자 |
| other_quality_system | 기타 품질시스템 |

사용자가 위 표에 없는 표현으로 카테고리를 말하면("무균 관련", "데이터 조작 관련" 등) 가장 가까운 code로 매핑해 호출하고, 답변에는 한국어 라벨을 사용한다(code 문자열을 사용자에게 그대로 노출하지 않는다).
```

에이전트를 저장하고 게시(Publish)하면 팀 채널/개인용으로 테스트할 수 있다.

## 7. 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| 401 Unauthorized | anon 키가 틀렸거나, 3단계의 Authorization 정책이 빠짐/오타 | 보안 탭 연결의 키 값 재확인, 정책의 헤더 값이 정확히 `Bearer <키>`(공백 1칸)인지 확인 |
| 404 Not Found (특히 `findingsCategoryMatrix`) | `008_findings_category_matrix.sql`이 아직 라이브 DB에 적용되지 않음 | 관리자에게 마이그레이션 적용 여부 확인(웹 findings 대시보드 히트맵이 뜨는지로도 간접 확인 가능) |
| `findingsList` 응답이 항상 빈 배열 | 정상 동작 — RLS 공개 게이트가 국문 번역이 끝난 행만 노출한다 | 미번역 원문은 이 API로 조회되지 않는다. 집계(건수)가 필요하면 findingsStats/findingsCategoryMatrix를 대신 쓰도록 안내 |
| 400 Bad Request (`findingsFirmStats`) | 요청 본문에 `p_firm` 키가 없거나 빈 문자열 | 본문을 `{"p_firm": "정확한 업체명"}` 형태로 보냈는지 확인 |
| Copilot Studio에서 액션이 호출되지 않고 일반 답변만 함 | 지침에 액션 사용 규칙이 약하거나, 도구가 에이전트에 연결 안 됨 | 5단계의 도구 연결 상태 확인, 6단계 지침의 그라운딩 규칙 문구를 더 명령형으로 강화 |
| 테스트 탭에서는 되는데 게시된 에이전트에서 안 됨 | 연결(Connection)이 개인 계정 소유라 다른 사용자에게 공유되지 않음 | 팀 전체가 쓰려면 공유 연결(셰어드 커넥션) 또는 환경 관리자가 만든 연결을 사용하도록 안내 |
