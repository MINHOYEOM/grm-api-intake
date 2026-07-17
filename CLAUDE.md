# CLAUDE.md — GRM 저장소 작업 지침

이 저장소는 **GRM (Global Regulatory Monitor)** — 글로벌·국내(식약처) 제약 GMP/품질 규제를 매일 수집해 매주 카드형 다이제스트를 웹사이트(`grm-solutions.com`)·이메일로 발행하는 자동화 시스템이다. 규제 지적사항 검색 DB(FIND-1 `/findings/`)도 함께 운영한다.

## 먼저 읽을 것
- **`GRM_SYSTEM.md`** — 시스템 전체 명세(개요·풀스택·데이터 흐름·폴더 구조·로드맵). **작업 시작 전 반드시 정독.** 이 문서가 시스템·구조의 단일 기준이다.

## 유지 규칙 (매 작업 시 준수)
1. **큰 변경**(새 규제 소스 / 새 Phase / 데이터 흐름 변경 / 파일·폴더 구조 변경)이 생기면 `GRM_SYSTEM.md` 의 **해당 섹션 본문** + 상단 **"문서 메타"**(버전·수정일)를 갱신한다.
2. **문서는 "현재 상태"만 간결하게 유지한다.** 과거 단계별 기록을 누적하지 않는다 — 낡은 내용은 삭제하고 현재 사실로 대체한다. 상세 이력이 필요하면 git 로그를 본다(별도 변경이력 표를 만들지 않는다).
3. **파일·폴더가 추가/이동/삭제되면** `GRM_SYSTEM.md` 의 **`§5.1 저장소 폴더 구조`** 트리도 함께 갱신한다.
4. 자잘한 버그·문구 수정은 문서 갱신 대상이 아니다(커밋 메시지로 충분).

## 구조 원칙 (깨지 말 것)
- **코드(`.py`)는 루트에 평면 유지** — `collect_intake.py` 가 같은 폴더의 `collect_mfds`·`grm_common` 등을 이름으로 import하므로 하위 폴더로 이동 금지.
- **문서 배치**: 현행은 `docs/`(+`docs/prompts/`·`docs/specs/`), 옛/완료 문서는 `archive/`.
- **git 추적 범위**: 현행 문서만 추적한다. `archive/` 는 `.gitignore` 대상 — 로컬·git 히스토리에만 보존하고 커밋하지 않는다.

## 핵심 위치
- 수집기 단일 진입점(오케스트레이터): `collect_intake.py` (워크플로우가 호출하는 유일 파일)
- 식약처 수집기: `collect_mfds*.py` / 공통 헬퍼: `grm_common.py`
- CLI·PostgREST 경계 공통 헬퍼: `grm_cli.py` (JSON object I/O·URL/헤더/Content-Range·service-role 자격증명 해석)
- 자동 실행: `.github/workflows/grm-intake.yml` (매일 18:17 UTC = 익일 03:17 KST 수집, Routine 발행은 매주 월 07:30 KST)
- 현행 Routine 프롬프트: `docs/prompts/GRM_Prompt_v16.md` (내부 버전 v16)
- 저장: Notion `GRM API Intake`(수집 staging) + Supabase `grm-reactions`(FIND-1 findings·웹 반응). `🌐 GRM Weekly Brief` DB는 레거시(웹 발행으로 대체).
- 발행: 웹 정적 사이트(`web/render.py` → Cloudflare Pages, 주 채널) + 이메일 뉴스레터(Brevo). Findings 검색은 `/findings/`.
