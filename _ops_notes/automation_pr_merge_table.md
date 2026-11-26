# automation/* 데이터 PR 머지 경로

현재 체크아웃의 `.github/workflows/`에서 PR 생성 명령과 head 브랜치를 대조했다.

| 워크플로 | 브랜치 접두 | 머지 주체 |
| --- | --- | --- |
| [grm-deficiency-cases-refresh.yml](../.github/workflows/grm-deficiency-cases-refresh.yml) | `automation/deficiency-cases-snapshot-` | 사람 · 수동 실행은 PR 생성까지 |
| [grm-fda-citation-firms-refresh.yml](../.github/workflows/grm-fda-citation-firms-refresh.yml) | `automation/fda-citation-firms-snapshot-` | 사람 · 수동 실행은 PR 생성까지 |
| [grm-fda-citations-snapshot.yml](../.github/workflows/grm-fda-citations-snapshot.yml) | `automation/fda-citations-snapshot-` | 사람 · 월간 예약과 수동 실행 모두 PR 생성까지 |
| [grm-findings-docs.yml](../.github/workflows/grm-findings-docs.yml) | `automation/findings-docs-auto-` | 자동머지 · 검토 게이트 통과 및 명시적으로 시작한 CI 성공 후. 검토 필요 후보는 사람 |
| [grm-findings-facets.yml](../.github/workflows/grm-findings-facets.yml) | `automation/findings-facets-auto-` | 자동머지 · 검토 게이트 통과 및 명시적으로 시작한 CI 성공 후. 검토 필요 후보는 사람 |
| [grm-glossary-cases.yml](../.github/workflows/grm-glossary-cases.yml) | `automation/glossary-cases-auto-` | 자동머지 · 검토 게이트 통과 및 명시적으로 시작한 CI 성공 후. 검토 필요 후보는 사람 |
| [grm-import-alerts-refresh.yml](../.github/workflows/grm-import-alerts-refresh.yml) | `automation/import-alerts-snapshot-` | 사람 · 수동 실행은 PR 생성까지 |
| [grm-library-staging.yml](../.github/workflows/grm-library-staging.yml) | `automation/library-auto-` | 자동머지 · 검토 게이트 통과 및 명시적으로 시작한 CI 성공 후. 검토 필요 후보는 사람 |
| [grm-news-refresh.yml](../.github/workflows/grm-news-refresh.yml) | `automation/news-auto-` | 자동머지 · 명시적으로 시작한 CI 성공 후 |

- 기존 배치 PR 워치독은 번역·주간 용어·퀴즈·용어 그림·링크드인 해부 PR의 제목 접두를 검사한다. 위 표의 PR 제목은 해당 조건에 걸리지 않는다. 자동머지 잡이 PR 생성 뒤 CI 대기 중 취소되거나 실패하면, 그 잡에서 머지까지 진행할 수 없다. [배치 PR 워치독](../.github/workflows/grm-stranded-batch-pr.yml)
- 월간 FDA 조항 스냅샷은 예약이 빠지면 경보만 남긴다. 자료실은 월요일 실행이 지연·누락됐을 때 다른 요일의 예약에서 마지막 갱신일을 보고 주간 결손을 복구한다. 용어 사례 수는 다음 실행에서 다시 측정한다. [예약 분류](../schedule_catchup.py), [자료실 예약](../.github/workflows/grm-library-staging.yml)
- 예약 보완 워크플로는 열린 PR 목록을 받아 `automation/` 브랜치의 PR이 열린 지 72시간 이상이면 건수를 로그와 경고 주석에 남긴다. 사용자 제보의 용어 사례 수 PR #1835(11월 20일 생성)를 시험에 반영했다. [경보 실행](../.github/workflows/grm-schedule-catchup.yml)
