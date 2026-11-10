# 483 관찰 상세 국문 번역 지침 (A·B·C 공용)

FDA Form 483 실사 관찰의 상세 서술("Specifically, …")을 한국어로 옮긴다. 독자는 국내 제약 품질·GMP 실무자다.

## 입력과 출력
- 입력: `translations/detail_shards/queue/<묶음>/<묶음>-NNNN.json` — 항목 50개 배열
  `{"finding_id", "finding_text_md5", "published_date", "detail_text"}`
- 출력: 같은 배열에 `"detail_text_ko"` 한 키만 더해 `translations/detail_outbox/<같은 파일 이름>` 으로 저장.
  다른 키는 **글자 하나도 바꾸지 않는다**(영문 오타·판독 잡음도 그대로 둔다).
- 다 쓰면 `python translations/detail_shards/check.py translations/detail_outbox/<파일>` 이 OK 여야 한다.

## 번역 규칙
1. 빠짐·덧붙임 없이 전부 옮긴다. 요약하지 않는다. 문장 순서와 목록 기호(A. B. / i. ii. / 1. 2.)를 그대로 둔다.
2. 그대로 둘 것: 숫자·날짜·연도·로트/배치 번호·문서 번호(SOP-QC-012 등)·CFR 조항(21 CFR 211.42(c))·
   대문자 약어(CAPA, HEPA, OOS, HPLC, ISO 5, Grade A)·제품명·장비 모델명·`[redacted]`(개수 그대로).
3. 문체: 사실을 적는 평서문 "~했다/~하지 않았다/~이다". 실사관 시점의 "I observed" 는 "실사관은 ~을 관찰했다".
   "your firm" 은 "귀사". 존댓말·추측·해설 금지.
4. 현장 용어를 쓴다: deviation 일탈 · investigation 조사 · batch record 배치 기록서 · media fill 배지충전 ·
   environmental monitoring 환경 모니터링 · aseptic 무균 · cleaning validation 세척 밸리데이션 ·
   out-of-specification 기준 일탈(OOS) · quality unit 품질 부서 · stability 안정성 · sterility 무균 ·
   intervention 개입 · gowning 무균복 착의 · sporicidal agent 포자 사멸제 · bioburden 바이오버든.
5. 한자 쓰지 않는다. 영어 낱말을 한글로 음차하지 않는다(그대로 두거나 우리말로).
6. 판독 잡음(뜻 없는 글자 조각)은 그 자리에 원문 그대로 둔다 — 고치거나 지우지 않는다.

## 나눠 맡기
- A 묶음: 해밀턴(회사 PC 세션) · B 묶음: 티로(개인 PC) · C 묶음: Codex. 남의 묶음 파일은 건드리지 않는다.
- PR 하나에 outbox 파일 여러 개를 담아도 된다. 파일 이름이 묶음 표시로 시작하므로 서로 충돌하지 않는다.
- 머지는 해밀턴 세션이 CI 초록·정확한 head SHA 확인 뒤에 한다. 머지되면 `grm-findings-detail-apply` 가
  findings.detail_text·detail_text_ko 에 반영한다.
