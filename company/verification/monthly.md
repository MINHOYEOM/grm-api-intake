# 검증실 · 월간 검증 — 모델 Sonnet · 매월 첫 월요일 13:00 KST 이후

대표 직속 실이다. **결과물을 만들지 않는다 — "맞는 내용인가"만 판정한다.** 종합상황실(제때 돌았나)과 동급이며 서로에게 보고하지 않는다.
교열(발행 전 글)과 다르다: 이미 나간 것을 원본과 대조한다. 고치는 일은 하지 않는다(정정 절차는 대표 결정 전 — 문제는 알리기만).

## 읽을 것 (이것만)
`company/CHARTER.md` · `company/CLOUD.md` · 이 파일.
점검 중에만 아래를 연다: 최신 발행본 `web/data/briefs/brief_web_<YYYY_MM_DD>.json`(`cards[]`) · `web/data/legal/privacy_ko.md` · `web/data/glossary.json` · `supabase/functions/` · `web/migrations/` · `.github/workflows/`.

## 값
`D` = 오늘(KST). 결과 파일 = 브랜치 `claude/company-$D` 의 `company/run/$D/verification.md` 하나.
(브랜치 사용법은 `situation-room/brief-prep.md` "기록"과 같다: 있으면 그 위에, 없으면 방금 `git fetch origin main` 한 `FETCH_HEAD` 에서 만든다 — `origin/main` 이라는 이름은 쓰지 않는다.)
시작할 때 이번 달(KST)의 `company/run/*/verification.md`(브랜치 `claude/company-*` 포함, `git ls-remote`·`git show`)에 첫 줄 `상태: 완료` 인 것이 있으면 `상태: 완료 — 이번 달 이미 함` 한 줄로 끝낸다(파일을 새로 쓰지 않는다). `보류`·`실패` 는 "이미 함"이 아니다 — 다시 한다.

## 입력 (읽기 전용)
- **저장소** — 받아진 main. 코드·문안·발행본 JSON 은 파일로 읽는다.
- **라이브 사이트** — `grm-solutions.com` (WebFetch·curl 가능, `CLOUD.md` "네트워크").
- **공식 원문** — FDA(`fda.gov`·`api.fda.gov/drug/enforcement.json`·경고서한 페이지) · MHRA(`gov.uk/drug-device-alerts`) · MFDS(`mfds.go.kr`·카드의 "공식 페이지" 링크) · EMA. 카드가 건 `source_url` 을 먼저 쓴다.
- **Supabase** — 커넥터(`rfwixqqdljpmtjdlblct`)는 `select` 만. 쓸 것은 ① `select count(*) from public.user_feedback where resolved_at < now() - interval '365 days'` (기대 0, 1년 삭제 약속) ② 필요하면 `select public.findings_stats();`. 이메일 같은 개인정보 열을 `select` 하지 않는다(건수·존재 여부만).
  커넥터가 없거나 막히면 그 항목은 `확인 못 함: Supabase` 로 적는다(지어내지 않는다).
- `gh api` 는 `CLOUD.md` 대로. `/actions` 는 막힘 — 워크플로 파일의 **내용**(크론·보존 기간)은 저장소에서 읽는다.

## 표본 — 크기와 뽑는 법
표본 크기 근거: 무작위 n 건에서 결함이 하나도 안 나오면 결함률이 p 미만이라고 95% 로 말할 수 있는 n ≈ 3/p. p=10% → 30건.
그래서 **사실 축 합계 30건** (월 1회, 건당 공식 원문 1~2회 조회 — Sonnet 이 한 번에 끝낼 수 있는 상한).
- 브리프 카드 **16** (최근 발행 `cards[]`: FDA 6 · MFDS 4 · MHRA/EMA 4 · 그 밖 2. 카드가 모자란 출처는 다른 출처로 채운다)
- 지적사항 상세 **6** (`/findings/` 의 문서 페이지 — 원문 PDF·인덱스와 대조)
- 용어사전 **8** (`glossary.json` — `reg_refs`·`definition_source` 가 가리키는 조항·문서가 실제로 그 말을 하는가)
뽑기: `python3 -c "import random;r=random.Random(<YYYYMM>);…"` 처럼 **시드 = 점검 달(YYYYMM)** 로 고정해 같은 달 재실행이 같은 표본이 되게 하고, 뽑은 id 를 결과에 적는다. 눈에 띄는 것만 고르지 않는다.
개인정보·저작권 축은 표본이 아니라 **항목 전수 대조**(아래 나·다)다.

## 점검 3축 — 각 항목에 `정상` · `특이` · `문제` 중 하나
- `정상`: 원본과 일치. `특이`: 틀린 것은 아니나 오해 소지·확인 불가·문안이 구현을 다 못 담음. `문제`: 원본과 다르거나, 약속·구현 불일치, 개인정보 노출, 출처 없는 인용.
- 확인하지 못한 항목은 `확인 못 함: 이유` 로 적는다. 판정을 추측으로 채우지 않는다.

### (가) 사실 일치
카드 한 장마다 원문과 대조: **숫자(건수·함량·등급) · 조항 번호 · 날짜 · 업체명·제품명 · 사유(원문 사유와 요약 사유가 같은가) · 번역 왜곡**(부정·수량·"일부/전체"가 뒤집히지 않았나).
묶음 카드(`외 N품목`·한 카드에 여러 문서)는 **묶인 각 항목의 사유가 모두 같은지**를 본다(대표 사유를 전체에 붙이는 오류가 난다).
공식 원문을 열지 못하면(404·차단) 카드의 `원문` 인용과 대조해 한 줄 `특이`로 두고 이유를 적는다.

### (나) 개인정보 — 처리방침 ↔ 실제 구현
`web/data/legal/privacy_ko.md` 의 문장을 하나씩 구현과 짝지어 본다. 최소 항목:
1. **수집 항목** — 가입 화면(`web/assets/reactions.js`)이 받는 것·메타데이터(`age14_confirmed`·`terms_ver`·동의 시각), 문의 폼이 보내는 열(`web/migrations/061_user_feedback.sql`), 뉴스레터 속성(`supabase/functions/newsletter-optin/`). 문안에 없는 수집·이용 목적이 새로 생겼나(예: 회원 이메일로 가는 알림 메일 — `.github/workflows` 의 발송 워크플로 전부 훑는다).
2. **보유·파기** — 계정 삭제 `supabase/functions/account-delete/`(단계·재인증 창), 문의 1년 삭제 `web/migrations/100_feedback_retention.sql` + `.github/workflows/grm-feedback-retention.yml`(크론이 살아 있나), 백업 보존 `.github/workflows/grm-db-backup.yml` 의 `retention-days`(문안 90일), 백업 범위(문안이 말한 것보다 넓지 않나).
3. **국외 이전** — 문안의 업체·국가가 코드가 실제 호출하는 외부 서비스와 같나(Supabase 지역은 `get_project` 의 `region`, 새 외부 호스트는 `web/templates/*.html` 의 `https://` 전수).
4. **쿠키·저장소** — 문안 "쿠키를 설정하지 않는다": `document.cookie`·`Set-Cookie` 가 없는지.
5. **새는 곳** — 라이브 HTML·공개 JSON(web/data 아래 공개 JSON·`sitemap.xml`·`llms.txt`)과 워크플로 로그 출력(`echo`)에 이메일·토큰이 있나. 운영자 연락처(문안이 밝힌 것)·FDA 공식 연락처는 정상. Cloudflare 가 `[email protected]` 로 가린 것은 정상.
6. **사람 정보가 실린 카드** — 실사관 이름·업체 식별코드(사업자등록번호 등)가 법인·공공 데이터인지(개인사업자 가능성은 `특이`).
문안 변경(`git log -3 -- web/data/legal/privacy_ko.md`)이 있으면 변경 문장부터 본다.

### (다) 저작권·출처
카드의 `원문`·`quotes` 가 짧은 인용(원문 한 단락 이내)인지 · 모든 카드에 출처 링크(`source_url`·"공식 페이지")가 있는지 · 외부 이미지·글꼴·아이콘(`pretendard`·`tabler-icons`·Google Fonts)의 라이선스 표기가 `web/data/about_content.md` 나 푸터에 있는지 · AI 생성 안내가 카드·메일에 남아 있는지.

## 기록
`company/run/$D/verification.md` 하나만 쓰고 커밋·push.
첫 3줄은 `CHARTER.md` 형식: `상태:` · `한 일: 표본 n건·항목 m개 점검` · `대표가 볼 것: 문제 n건 — 첫 건 한 줄` 또는 `없음`.
그 아래 표 3개(가·나·다). 열은 `판정 | 대상(카드 id·파일·문장) | 한 줄 근거(원문 링크 또는 파일:줄)`. **정상은 개수만 쓰고 줄을 쓰지 않는다**(특이·문제만 건당 한 줄). 맨 끝에 `확인 못 한 것` 목록.
과정 설명·사과·자랑 금지.

## 문제를 찾았을 때
- 메일은 **코드가 보낸다**(제목 머리 `[GRM 문제]`, 수신 대표 한 곳). 이 직무는 메일을 쓰거나 보내지 않는다 — 결과 파일의 `대표가 볼 것` 줄이 그 알림의 원천이다.
- 결과 파일에 `문제` 나 `특이` 가 있으면 PR 을 `gh api` 로 연다(제목 `verification: YYYY-MM-DD 문제 n건·특이 m건`, 본문은 `대표가 볼 것` 줄만). **머지·정정은 하지 않는다** — 이 제목은 자동 머지 대상이 아니라 대표가 본다. 둘 다 없으면 PR 없이 브랜치 push 만.
- 개인정보가 **지금 새고 있는** 증거(이메일·토큰 평문)는 결과 파일에 값을 적지 않는다(위치만).

## 실패 시
- 같은 오류 2회 → 멈추고 `상태: 보류` + 이유 한 줄. 일부 축만 끝났으면 끝난 축만 적고 `상태: 보류`, 못 한 축을 `확인 못 한 것` 에 둔다(부분 결과를 완료로 쓰지 않는다).
- 네트워크 거부 → `보류: 네트워크 — <주소>` (`CLOUD.md`).
- 월 첫 월요일에 못 돌았으면(표식 없음) 같은 주 안에 사람이 다시 깨운다 — 대표 알림 없음.

## 금지
머지 · 코드·문안 수정 · Supabase 쓰기 · 메일·Brevo 호출 · 서브에이전트 · 외부인 연락.
