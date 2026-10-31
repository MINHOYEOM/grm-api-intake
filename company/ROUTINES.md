# 클라우드 예약작업 목록 — PC 를 켜 두지 않아도 돈다 (2026-10-31 이전, 대표 결정)

시각은 KST. 클라우드 쪽 cron 은 UTC 로 등록돼 있다. PC 백업은 **2026-11-14 까지** 클라우드보다 늦게 돌며,
클라우드가 남긴 표식이 있으면 건너뛴다. 2주 동안 이중 실행·누락이 없으면 PC 작업을 끈다(대표 결재).

| 팀 | 일 | 지시문 | 모델 | 클라우드 | PC 백업 | 클라우드가 했다는 표식 |
|---|---|---|---|---|---|---|
| 브리프편집팀 | 월요일 브리프 | `editorial/` | Sonnet·Opus·Sonnet | 월 05:30·06:10·07:40 | v16 Routine 월 09:30 | handoff CONSUMED |
| 용어·교육팀 | 주간 용어 추가 | `education/glossary-weekly.md` | Sonnet | 화 08:00 | 화 10:17 | 브랜치 `claude/glossary-weekly-YYYYWW` 또는 `glossary(weekly): YYYYWW` PR |
| 용어·교육팀 | 주간 퀴즈 | `education/quiz-weekly.md` | Sonnet | 월 12:30·16:30 | 월 15:00·19:00 | `quiz_bank.json` 의 이번 주 문항 · 브랜치 `claude/quiz-weekly-YYYYWW` |
| 글로벌번역팀 | 지적사항 일일 번역 | `translation/findings-daily.md` | Sonnet | 매일 04:00 | 매일 09:10 | 브랜치 `claude/findings-translate-YYYY-MM-DD` |
| 종합상황실 | 월 발행 준비 확인 | `situation-room/brief-prep.md` | Haiku | 월 10:45 | 월 11:30 (복구 담당) | `company/run/<날짜>/brief-prep.md` 첫 줄 `상태: 완료` |
| 종합상황실 | 월 운영 점검 | `situation-room/ops-weekly.md` | Haiku | 월 14:00 | 월 15:30 (`막힘:` 항목만) | `company/run/<날짜>/ops.md` |
| 성장분석팀 | 주간 성장 보고 | `growth/weekly-report.md` | Haiku | 화 07:30 | (PC 매일 일보는 꺼짐) | — |
| 대외홍보팀 | 링크드인 초안 확인 | `outreach/linkedin-draft.md` | Sonnet | 월 17:30 | 월 18:00 (작성창 세팅 — PC 만 가능) | — |

## 클라우드가 못 하는 것 (PC 백업이 맡고, 2주 뒤 대표가 정한다)
- GitHub Actions 실행 조회·재실행·아티팩트(`/actions` 403): 발송 런 확인, 브릿지·web-publish 재실행, WL 절단 복원 계획 받기.
- 심층분석 백필(카드별 서브에이전트), 링크드인 작성창 세팅(크롬 로그인).
- 용어 그림 일일 배치는 꺼져 있다(대표 결정 대기).
