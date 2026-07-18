# 자료실 데이터 확장 2차 검증 리포트

검증 기준일: 2026-07-18 (KST)

## 1. 산출물과 건수

| 파일 | 성격 | 항목 수 |
|---|---|---:|
| `ich.json` | 현행 31개 ICH 토픽 대체본 | 31 |
| `ema.json` | EMA GMP 제조·검사·품질결함 가이드 및 Q&A | 26 |
| `health_canada.json` | Health Canada 현행 GMP·품질·제조소 가이드 | 20 |
| `mfds_extra.json` | 현행 MFDS 31건에 추가할 백카탈로그 | 40 |
| **합계** |  | **117** |

기존 `web/data/library/*.json`은 수정하지 않았다. 네 JSON은 현행 v2의 공개 필드(`id`, `code`, `title_en`, `title_ko`, `doc_type`, `published_date`, `official_url`, `pdf_url`, `ko_url`)만 사용한다. 날짜는 공식 문서가 명시한 발행일, 개정일 또는 ICH Step 4 채택일만 수록했다.

## 2. 링크 전수 검증 결과

| 파일 | URL 필드 수 | 고유 엔드포인트 수 | HTTP 200 | 문서 일치 | 실패 |
|---|---:|---:|---:|---:|---:|
| `ich.json` | 62 | 31 | 31/31 | 62/62 | 0 |
| `ema.json` | 51 | 32 | 32/32 | 51/51 | 0 |
| `health_canada.json` | 20 | 20 | 20/20 | 20/20 | 0 |
| `mfds_extra.json` | 80 | 80 | 80/80 | 80/80 | 0 |
| **합계** | **213** | **162**¹ | **162/162** | **213/213** | **0** |

¹ `ich.json`의 Q8·Q9·Q10 공통 한국어 Q&A URL 중 하나가 `mfds_extra.json`의 같은 공식 게시물 URL과 중복되므로, 파일별 고유 엔드포인트 합계보다 전체 고유 엔드포인트가 1개 적다.

검증 방식:

- HTML: 리디렉션을 포함한 최종 HTTP 200, 공식 도메인, 페이지 제목·문서 코드·본문 주제 일치를 확인했다.
- PDF: HTTP 200, `%PDF` 파일 서명, PDF 본문에서 문서번호·제목을 확인했다.
- 식약처 PDF 40건 중 35건은 PDF 추출 본문에서 한국어 제목을 직접 대조했다. 나머지 5건은 PDF 서명과 공식 게시물의 제목 및 해당 게시물에 연결된 첨부 파일 관계를 대조했다.
- ICH Q3C(R9), Q4B(R1)은 대표문서 코드가 토픽 묶음 코드(`Q3A-Q3E`, `Q4A-Q4B`)의 첫 코드와 달라 자동 코드 대조가 경고해 추가 수동 확인했으며, 첫 페이지의 문서번호, 제목, Step 4 날짜가 모두 일치했다.
- ICH 토픽 랜딩 페이지는 JavaScript 셸이므로 HTTP 200 확인에 더해 ICH 공식 콘텐츠 API의 토픽 목록과 파일 그룹을 대조했다.
- Health Canada 20건은 Canada.ca 공식 HTML 원문에서 제목·GUI 코드·현행 상태를 각각 확인했다.

PDF 고유 문서는 ICH 24건, EMA 25건, MFDS 40건으로 총 89건이며 모두 PDF 서명과 문서 일치 검증을 통과했다.

저장소 검증:

- 네 JSON 파싱, 허용 필드, 날짜 형식, 파일별 ID 유일성: 통과
- ICH 31개 ID·`official_url` 원본 보존: 통과
- MFDS 현행 31건 대비 ID·제목·공식 URL 중복: 각 0건
- `git diff --check`: 통과
- `web/tests/test_render.py`: 377개 테스트 통과

## 3. ICH 대체본

- 기존 31개 `id`를 순서까지 그대로 보존했다.
- 기존 31개 `official_url`을 모두 그대로 보존했다.
- `database.ich.org` 현행 Step 4 또는 현행 개정판 직링크를 24개 토픽에 추가했다.
- 복수 문서 묶음은 대표 현행 문서를 선택하고 `title_en`에 실제 문서명과 개정번호를 명시했다. 대표 선택 예: Q1A(R2), Q3C(R9), Q4B(R1), Q5A(R2), Q6A, M13A.
- `published_date`는 대표 PDF의 Step 4 채택일 또는 공식 발행·개정일이다.

`pdf_url` 미수록 7개 토픽과 사유:

- M1: MedDRA 관련 페이지·서비스는 있으나 단일 현행 ICH 가이드라인 PDF가 없다.
- M2: 개념문서·작업계획·용어 자료만 확인되며 현행 최종 가이드라인 PDF가 없다.
- M5: Drug Dictionaries 최종 가이드라인 파일이 없고 관련 E2B 자료로 연결된다.
- M6: 개념문서와 부속자료만 있으며 현행 최종 가이드라인 PDF가 없다.
- M8: eCTD 현행 표준은 별도 사양·지원문서 체계이며 `database.ich.org`의 단일 대표 가이드라인 PDF가 없다.
- M16: 2026-07-18 기준 최종 가이드라인 파일이 없다.
- M18: 개념문서·작업계획 단계로 최종 가이드라인 PDF가 없다.

## 4. 식약처 공식 ICH 한국어 자료 재조사

`ko_url` 확인 토픽은 7개이며, 기존 Q13 외 6개 토픽을 추가 발굴했다.

### 본문 번역·공식 한국어 가이드

- Q1A: 의약품안전나라 `ICH가이드라인Q1A(안정성시험자료)설명`
- Q7: 의약품안전나라 `ICH가이드라인Q7A(원료의약품GMP)설명`
- Q13: 식약처 `ICH Q13 가이드라인(원료의약품과 완제의약품의 연속제조공정)`

### 공식 한국어 주제 Q&A

- Q8, Q9, Q10: 식약처 `ICH Q8, Q9, 및 Q10 가이드라인 질의응답집` 공통 URL
- M4: 식약처 `국제공통기술문서 작성 질의응답집(ICH M4)`

Q&A는 원 가이드라인 전체 번역본으로 오인하지 않도록 이 리포트에서 별도 분류했다.

확인 가능한 공식 핵심 번역본 또는 주제 Q&A를 찾지 못해 `ko_url`을 넣지 않은 24개 토픽:

`Q2`, `Q3A-Q3E`, `Q4A-Q4B`, `Q5A-Q5E`, `Q6A-Q6B`, `Q11`, `Q12`, `Q14`, `M1`, `M2`, `M3`, `M5`, `M6`, `M7`, `M8`, `M9`, `M10`, `M11`, `M12`, `M13`, `M14`, `M15`, `M16`, `M18`.

조사 범위는 식약처 의약품 안내서 게시판의 ICH 제목·본문 검색과 의약품안전나라 자료실의 ICH 제목 검색을 포함했다. 의약품안전나라에서 확인된 ICH 명시 자료는 Q1A와 Q7A 두 건이었다. 불명확한 민간 번역, 재게시물, 제목만 유사한 일반 가이드는 수록하지 않았다.

## 5. EMA 큐레이션

총 26건:

- Compilation of Union procedures의 GMP 검사체계, 품질결함·신속경보, 제3국 제조소, GMP 비준수, GMP 인증서·허가 형식 관련 현행 절차 16건
- 완제의약품 제조, 공정 밸리데이션, 사용기한 기산, 멸균, 이온화 방사선, 공유시설 HBEL 과학 가이드 6건
- Design Space, NOR/PAR/DSp, HBEL·교차오염 관련 PDF Q&A 3건
- EMA GMP/GDP 상시 Q&A 랜딩 페이지 1건

미수록 사유:

- GDP 전용 검사·인증서·도매유통 절차는 GMP 중심 범위에서 제외했다.
- Union 양식·템플릿, 변경 이력, 단순 소개 문서는 핵심 가이드 20~30건 큐레이션 범위에서 제외했다.
- 초안, 의견수렴본, 폐기된 이전 개정판은 현행 문서 우선 원칙에 따라 제외했다.

## 6. Health Canada 큐레이션

총 20건으로 GUI-0001, GUI-0002, GUI-0005, GUI-0012, GUI-0023, GUI-0026, GUI-0027, GUI-0028, GUI-0029, GUI-0031, GUI-0036, GUI-0039, GUI-0066, GUI-0069, GUI-0071, GUI-0080, GUI-0104, GUI-0119, GUI-0127, GUI-0158을 수록했다.

미수록 사유:

- GUI-0014는 공식 페이지가 보관(archived) 상태이며 내용이 GUI-0080으로 이전됐다고 명시되어 제외했다.
- Annex 11과 Annex 17의 구 페이지는 보관 상태여서 제외했다.
- GUI-0074는 공식 경로가 제거 안내 페이지로 전환되어 제외했다.
- 월 단위만 제시된 날짜 등 정확한 일자를 확정할 수 없는 경우 `published_date`를 넣지 않았다.

## 7. MFDS 백카탈로그 추가분

- 식약처 의약품 지침·안내서 게시판 전체 31페이지를 재조사하고 GMP·품질·제조공정·불순물·CTD/QbD·밸리데이션 중심 40건을 선정했다.
- 현행 `mfds.json` 31건과 `id`, `title_ko`, `official_url`을 각각 대조한 결과 중복은 모두 0건이다.
- 공식 한국어 제목을 그대로 사용했으며 영문 제목을 창작하지 않았다.

미수록 사유:

- 현행 31건과 게시물·제목이 같은 항목
- GMP·품질 범위를 벗어난 임상·안전성·효능 중심 자료
- 같은 문서의 구판 또는 후속 개정판으로 대체된 항목
- PDF 첨부를 실접속·문서 일치까지 확정할 수 없는 항목

## 8. 잔여·미확인 목록

- ICH: 최종 가이드라인 PDF가 없는 7개 토픽(M1, M2, M5, M6, M8, M16, M18)은 후속 발행 시 재확인이 필요하다.
- ICH 한국어: 24개 토픽은 공식 핵심 번역본 또는 명시적 주제 Q&A를 확인하지 못했다.
- Health Canada: 보관·제거된 GUI-0014, Annex 11, Annex 17, GUI-0074는 현행 대체 문서 상태를 후속 주기에서 재확인할 수 있다.
- 링크 검증 실패나 문서 불일치로 남은 수록 항목은 없다.

## 9. 주요 공식 조사 출처

- [ICH Quality Guidelines](https://www.ich.org/page/quality-guidelines)
- [ICH Multidisciplinary Guidelines](https://www.ich.org/page/multidisciplinary-guidelines)
- [식약처 의약품 지침·안내서](https://www.mfds.go.kr/brd/m_1060/list.do)
- [의약품안전나라 자료실](https://nedrug.mfds.go.kr/search)
- [EMA Quality guidelines: manufacturing](https://www.ema.europa.eu/en/human-regulatory-overview/research-development/scientific-guidelines/quality-guidelines/quality-guidelines-manufacturing)
- [EMA Compilation of Union procedures](https://www.ema.europa.eu/en/human-regulatory-overview/research-development/compliance-research-development/good-manufacturing-practice/compilation-union-procedures-inspections-exchange-information)
- [EMA GMP/GDP Questions and answers](https://www.ema.europa.eu/en/human-regulatory-overview/research-development/compliance-research-development/good-manufacturing-practice/guidance-good-manufacturing-practice-good-distribution-practice-questions-answers)
- [Health Canada GUI-0001 and related guides](https://www.canada.ca/en/health-canada/services/drugs-health-products/compliance-enforcement/good-manufacturing-practices/guidance-documents/gmp-guidelines-0001.html)
