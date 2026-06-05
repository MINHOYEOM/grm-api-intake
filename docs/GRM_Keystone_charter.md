# GRM Keystone — 프로젝트 헌장 (Charter)

> 작성: 2026-06-04 · 상태: 착수(GO) — 제형 확장(v15.8) main 머지·라이브 후 시작
> 한 줄 정의: **출력(카드)을 결정론적 Python 골격 위에 고정하고, LLM은 판단 산문만 카드별로 채우게 해서, 매주 깨지지 않고 더 좋은 브리프를 만든다.**

---

## 1. 왜 (문제)

2026-06-04 v15.7 1차 라이브에서 단일 컨텍스트 폭주로 카드 포맷·page_id 가 드롭, 출력이
금지 문법으로 폴백하고 Status 갱신이 누락됐다(LV-15.7a). 현재 v15.8 Routine 도 **같은 구조적
취약성을 그대로** 갖는다 — 매주 월요일 브리프가 같은 식으로 깨질 위험이 상존한다.
근본 원인: LLM 에게 판단(산문)뿐 아니라 결정론적 작업(표·링크·인용·배지·상태관리)까지 시킴.

부수 문제: 양식이 실행마다 미세하게 달라진다(LLM 은 매번 "다시 그림"). 카드는 QA 담당자가
매주 읽는 제품 UX 라 일관성·스캔성·정보위계가 곧 사용 가치다.

## 2. 무엇 (목표)

- **결정론적 = Python**: 카드 골격(제목 뼈대·W2 메타표·듀얼링크·raw 인용·배지·제품군 라벨·
  그룹핑·출력 매트릭스·Status·handoff 마감·노이즈필터·Lint 구조검사).
  같은 입력 → 바이트 단위로 같은 출력. CI/unittest 로 회귀 검증.
- **판단 = LLM(Claude)**: 카드 제목의 핵심이슈 구절 · W1 한 줄 요약 · W4 번역(비KO) ·
  W5 핵심 사실 · W6 시사점 · W7 점검 사항, 그리고 Intake 외 글로벌/Watch WebSearch 탐지.
  카드 1장 단위로만 처리 → 컨텍스트 폭주 구조적으로 불가능.

목표는 "안 깨짐"을 넘어 **기능적으로 더 좋아짐**: 포맷 100% 일관, 가짜 링크·위조 인용 차단,
LLM 이 한 장에 집중해 시사점·점검 품질↑, 제품군(Modality)별 맞춤 관점, 카드·Status 누락 0.

## 3. 범위

**포함(Keystone)**: 카드 스펙 v16 확정 → Python 카드 조립기(scaffold) → handoff v2 데이터계약 →
Routine 얇게 교체(산문 슬롯만) → Lint/Status 의 Python 마감 → 노이즈필터 부서게이트(M0).

**제외(별도 트랙, 비블로킹)**:
- Phase B — 바이오 신호 실질화(FDA CBER · EMA ATMP/바이오 · 콜드체인 등 **수집 소스 추가**).
  Keystone 은 발행/조립 계층, Phase B 는 수집 계층이라 직교. 바이오 칸을 실제로 채우는 별도 결정.
- 추가 제품군(흡입·국소·CGT 세분 등) — 의도적 보류.
- 멀티 사용자 배포·구독 모델 — 상용화 트랙.

## 4. 로드맵 (마이그레이션, 비파괴 단계)

`docs/GRM_architecture_redesign.md` 의 M0~M5 를 Keystone 실행 단위로 정렬한다.

| 단계 | 내용 | 산출물 | 의존 |
|---|---|---|---|
| K1 | **카드 스펙 v16 확정** — 유형별 고정 템플릿·필드매핑·분기·golden 견본·디자인 리뷰 | `GRM_card_spec_v16.md` + 견본 카드 렌더 | v15.8 카드 동결 |
| K2-prep | **page_id 로 원 row raw fetch → rows 에 raw 부착**(v1 handoff 에 raw 없음, Codex) | 코드 | M0 |
| K2 (M2) | Python 카드 조립기 `build_card_scaffold(row, raw, cfg)` 순수 함수 + handoff v2(additive) | 코드 + golden 테스트 | K1·K2-prep |
| K3 (M3) | Routine 얇게 교체(scaffold 읽고 산문만, 카드별 루프) | v16 프롬프트 | K2 |
| K4 (M4) | Lint 구조검사·Status·CONSUMED 를 Python 마감 | 코드 | K2 |
| M0 | 노이즈필터 WL 발행부서 게이트 + unittest | 코드 | 독립(지금 가능) |
| (선택) M5 | 수집기 내 Haiku 로 산문까지 채워 완성 브리프 직접 발행 | 코드 | K3 안정화·A/B |

권장 정지점: **K1~K4(=M0~M4)가 본체**. M5 는 산문 품질 A/B 후 결정.

## 5. 성공 기준

- 동일 handoff 입력에 대해 카드 골격이 **결정론적**(golden 테스트 통과)일 것.
- 4주 연속 발행에서 Brief Lint L1~L10 구조 항목 위반 0.
- Status 갱신·handoff CONSUMED 누락 0(주간 재유입 0).
- 시사점·점검 품질이 v15.8 대비 **저하 없음 이상**(샘플 리뷰).
- 카드 양식이 실행 간 변형 없음(같은 유형 → 같은 틀).

## 6. 현재 상태 · 다음 액션

- 의존(제형 v15.8) 머지·라이브 확인됨 → **착수 GO**.
- **K1 완료 (✅ 2026-06-05)**: 카드 스펙 v16 작성 + Codex 구현 검토(§12 반영) + 디자인 리뷰 동결(§13.1, 12개 원칙).
  견본 Notion 페이지는 동결 후 삭제. 스펙(`GRM_card_spec_v16.md`)이 카드 양식의 단일 진실원.
- **K2 완료 (✅ 2026-06-05 origin/main 머지됨, 별도 Claude Code 세션).** 단계 게이트로 수행(각 단계 끝 테스트 green + 사람 GO/HOLD).
  - **Stage A(M0 부서게이트) + Gate A P1 보정: ✅ 머지.** 식품 WL(OII 단독 cgmp 관통 포함) 입구 차단.
  - **Stage B(K2-prep raw fetch·하이브리드)·C(`build_card_scaffold`+golden 5종)·D(handoff v2 `ENABLE_HANDOFF_V2` 기본 off): ✅ Codex 재검토 GO + 사람 승인 → 일괄 ff-머지·push 완료(133 green).** `ENABLE_HANDOFF_V2` 저장소 변수 미설정 → 운영 무영향.
  - **K2.5(scaffold 전 유형 확장): ✅ 머지.** Codex 종합점검 P1 — golden 5종 → **활성 소스 전 유형 16종**(openfda·hc·who 3종·ich·rss·MFDS RSS 4종). 불변식 Evidence A⟺인용 raw 필드. prose_input 확장(gmp 버그 수정). card_spec §12(C-확장) 매핑 동결. 134 green.
  - scaffold 저장위치 = handoff JSON 본문 포함(additive v2). 제목은 §13.1-1·8 동결안(prefix·소재국·DocID 제거).
  - **다음: K3(Routine 얇게 교체·v2 운영 전환)·K4(Lint/Status Python 마감). 이월:** inmemory_raw 의 `main()` 와이어링·Watch scaffold·recall 병합 렌더(K3)·Status 실제 전이(K4).
  ※ K2 작업 모델: **Claude(Cowork) 설계·조율 + Claude Code 구현 + Codex 교차검토**를 적절히 분담.
  구현 기준 = `GRM_card_spec_v16.md`(§0~§9 책임매핑 · §12 필드보정 · §13.1 디자인 동결안) + `GRM_architecture_redesign.md`(handoff v2 · M0~M5).
  지시문: `archive/point-in-time/GRM_Keystone_K2_ClaudeCode_지시.md`.

## 📝 변경 이력
| 날짜 | 변경 내용 |
|---|---|
| 2026-06-04 | 최초 작성. Keystone 정의·범위·로드맵 K1~K4(+M0/M5)·Phase B 분리·성공기준 |
| 2026-06-05 | **K1 완료**(카드 스펙 v16 + 디자인 동결 §13.1) 반영, **K2 착수**(별도 Claude Code 세션). §6 다음 액션을 K2 단계 게이트(M0→K2-prep→build_card_scaffold+golden→handoff v2)·scaffold 저장위치 결정·지시문 경로로 갱신 |
| 2026-06-05 | **K2 진행 갱신**: Stage A(M0+P1) origin/main 머지 완료, B~D 코드 완료(`card_scaffold.py`·handoff v2 `ENABLE_HANDOFF_V2` off, 133 green)·Codex 재검토 후 일괄 머지 대기. 제목 §13.1-1·8 동결안 반영(prefix/소재국/DocID 제거). inmemory_raw main 와이어링·v2 운영전환은 K3, Status 전이는 K4 이월 |
| 2026-06-05 | **K2 완료 · origin/main 머지됨**: Codex 재검토 GO + 사람 승인 → B~D 일괄 ff-머지·push(P3 주석정정 포함). `ENABLE_HANDOFF_V2` 미설정(운영 무영향). 다음 K3/K4 |
| 2026-06-05 | **K2.5 완료 · origin/main 머지됨**: scaffold 활성 소스 전 유형 16종 확장(Codex 종합점검 P1)·Evidence A⟺quote 정합·prose_input 확장(gmp 버그 수정)·card_spec §12(C-확장) 매핑 동결. 134 green. Codex 재확인 GO + 사람 승인 |
