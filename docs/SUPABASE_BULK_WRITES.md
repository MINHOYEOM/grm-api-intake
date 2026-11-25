# Supabase 대량 쓰기 지점

`grm_cli.BulkWriteRun`은 호출자가 넘긴 행을 요청당 최대 500행, 회차당 최대
2,000행으로 제한한다. 묶음은 순서대로 보내고 다음 묶음 전 1초 쉰다. 실패한
묶음도 회차 상한에 포함한다. 실패가 두 번 연속이면 중단하고, 호출자가 예외를
올리는 경로는 첫 실패에서 중단한다.

| 도구 | 쓰기 대상과 회차 처리 |
| --- | --- |
| `collect_fda_inspections.py`, `collect_fda_citations.py` | 실사·인용 merge upsert. 큰 입력은 같은 회계연도에 저장된 값과 비교하고 신규·변경 행을 최대 2,000행 보낸다. 남은 건수를 기록하고 같은 범위로 재실행할 수 있다. |
| `findings_embed_service.py` | `finding_embeddings` merge upsert. 기존 요청당 200행을 유지한다. 다음 회차에서 이미 최신인 행을 건너뛴다. |
| `findings_supabase_append.py` | 문서별 `raw_signals` 단건 및 파생 `findings` 묶음. 기존 ignore upsert와 충돌 시 단건 처리를 유지한다. `collect_hc_inspection_backfill.py` 등 문서별 호출자도 이 경로를 쓴다. |
| `collect_fda_backfill.py` | `raw_signals` ignore upsert. 기존 10행 묶음을 유지하고 자동 백필의 소스와 페이지가 예산을 공유한다. |
| `backfill_raw_signals_modality.py` | `raw_signals`의 빈 `modality`만 PATCH. 기존 100 ID 묶음을 유지한다. 상한을 넘기는 계획은 나머지를 보고한다. 다시 실행하면 여전히 비어 있는 행을 처리한다. |
| `mfds_gmp_cert_snapshot.py` | 판정서 신규·기존 행과 스냅샷 기록이 예산을 공유한다. 같은 KST 날짜의 재실행은 이미 그날 기록한 판정서를 건너뛴다. 전체 판정서 처리가 끝난 뒤 스냅샷을 기록한다. |
| `collect_newsletter_campaigns.py` | 캠페인 통계 merge upsert. 배포 전 스키마에서 선택 필드를 빼는 기존 재시도 경로를 유지한다. |
| `grm_queue.py` | `intake_queue` ignore upsert. 기존 100행 묶음을 유지한다. |
| `kg_link_new.py`, `kg_hc_clause.py`, `kg_product.py` | `finding_term`, `finding_clause`, `finding_product` ignore upsert. 일일 연결의 세 대상이 회차 예산을 공유한다. |
| `collect_deficiency_examples.py` | 원천별 `deficiency_examples_upsert` RPC. 모든 원천이 예산을 공유하며 한 원천의 묶음을 모두 처리한 뒤 그 원천을 finalize한다. |
| `intake_exclusion.py` | 제외 행 등록과 해제 RPC. 기존 200행 묶음과 공용 예산을 사용한다. |
| `raw_body_followup.py` | `grm_findings_supersede` RPC로 기존 지적의 상태를 갱신한다. |
| `watchlist_notify_service.py` | 발송 결과 로그 ignore upsert. 기존 100행 묶음을 유지하고 발송 전에 예상 로그 행 수를 확인한다. |

뉴스레터 구독 일별 기록, 인계 기록, 브라우저 성장 동기화, 문서별 정정 PATCH는
단건이다. `collect_rum_analytics.py`와 `collect_rum_referrer_paths.py`는
일자별 전체 스냅샷을 교체한다. 원천 finalize와 판정서 링크 RPC는 서버에서 변경
대상을 계산한다. 공용 예산의 범위는 한 실행에서 클라이언트가 보내는 쓰기 묶음이다.

정적 검사는 파이썬 소스에서 요청에 바로 전달되는 행 배열의 크기를 리터럴과
단순 상수식으로 확인한다. 동적 입력은 실제 쓰기 경로의 회차 제한이 확인한다.
