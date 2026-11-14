# 483 관찰 상세 국문 번역 지침 (2026-11-15 개정 · 읽기 좋음)

FDA Form 483 실사 관찰의 상세 서술("Specifically, …")을 한국어로 옮긴다. 독자는 국내 제약 품질·GMP 실무자다.
세션·Codex·티로 모두 이 지침 하나를 쓴다. 검사: `python findings_detail_qa.py --strict <파일>`.

## 왜 개정했나
옛 지침(ops/483-detail-shards 의 BRIEF.md)은 "판독 잡음은 원문 그대로 둔다"였다. 그 결과 공개 국문 11,141건 중
3,372건(30%)에 OCR 찌꺼기(`c0A`·`specitic`·`l4l`), 쪽 머리글("Ube, Yamaguchi, Japan … API Manufacturer"),
양식 칸("DISTRICT ADDRESS … Industry Information"), 깨진 가림 표시(`b) (4)`·`() (4)`), 번역 안 된 영어 문장이 남았다.
**영문 원문은 저장값 그대로 두고(바이트 불변), 국문만 사람이 읽는 글로 옮긴다.**

## 입력과 출력
- 항목: `{"finding_id", "finding_text_md5", "detail_text", ...}` 배열. `"detail_text_ko"` 한 키만 더한다.
- `finding_id`·`finding_text_md5`·`detail_text` 는 **글자 하나도 바꾸지 않는다**(영문 오타도 그대로). 적용기가
  `detail_text` 도 함께 쓰므로, 재번역은 반드시 **지금 DB 의 detail_text 를 그대로** 실어야 한다.
- 출력은 `translations/detail_outbox/<이름>.json`. 같은 관찰이 여러 파일에 있으면 폴더 전체 재적용 때
  **이름순 뒤 파일이 이긴다** — 재번역 배치는 기존보다 뒤에 오는 이름(`X-…`)을 쓴다.

## 번역 규칙
1. 뜻이 있는 내용은 빠짐·덧붙임 없이 전부 옮긴다. 요약하지 않는다. 문장 순서와 목록 기호(A. B. / i. ii. / 1. 2.)를 둔다.
2. 그대로 둘 것: 숫자·날짜·연도·로트/배치 번호·문서 번호(SOP-QC-012)·CFR 조항(21 CFR 211.42(c))·
   대문자 약어(CAPA, HEPA, OOS, HPLC, COA, ISO 5)·제품명·업체명·사람 이름·장비 모델명.
   SOP·양식의 공식 제목은 우리말로 옮기고, 원문 제목을 남기려면 따옴표 안에 둔다("Cleaning of Equipment").
3. 문체: 사실을 적는 평서문 "~했다/~하지 않았다/~이다". "I observed" 는 "실사관은 ~을 관찰했다", "your firm" 은 "귀사".
   존댓말·추측·해설 금지.
4. 현장 용어: deviation 일탈 · investigation 조사 · batch record 배치 기록서 · media fill 배지충전 ·
   environmental monitoring 환경 모니터링 · aseptic 무균 · cleaning validation 세척 밸리데이션 ·
   out-of-specification 기준 일탈(OOS) · quality unit 품질 부서 · stability 안정성 · intervention 개입 ·
   gowning 무균복 착의 · sporicidal agent 포자 사멸제 · bioburden 바이오버든 · certificate of analysis 시험성적서(COA).
5. 한자 쓰지 않는다. 영어 낱말을 한글로 음차하지 않는다(우리말로 옮기거나 고유명사면 그대로).

## 원문 잡음 정리 (국문에서만)
6. **양식 칸·쪽 머리글은 국문에 옮기지 않는다.** 483 양식 칸(DEPARTMENT OF HEALTH AND HUMAN SERVICES, DISTRICT ADDRESS
   AND PHONE NUMBER, Industry Information: www.fda.gov/oc/industry, NAME AND TITLE OF INDIVIDUAL TO WHOM REPORT ISSUED,
   FIRM NAME, STREET ADDRESS, CITY STATE ZIP, TYPE OF ESTABLISHMENT INSPECTED, FEI NUMBER, DATE(S) OF INSPECTION,
   EMPLOYEE(S) SIGNATURE, SEE REVERSE OF THIS PAGE, PAGE n OF n)과, 쪽마다 찍힌 업소 머리글 줄
   (예: "Ube, Yamaguchi, Japan 759-0204 API Manufacturer", "Producer of Sterile and Non-Sterile Drugs")은 뺀다.
   머리글이 문장을 끊었으면 앞뒤를 이어 한 문장으로 옮긴다. 그 칸 안의 주소·전화·날짜 숫자는 함께 빠져도 된다.
7. **가림 표시는 모두 `[redacted]` 하나로.** FDA 의 비공개 표시 (b)(4)·(b)(6)·(b)(7)(C) 와 그 OCR 깨진 꼴
   (`b) (4)`·`() (4)`·`l4l`·`C4l`·`bH4l`·`CbTC4l`·`t6J`·`b7c` 등)은 `[redacted]` 로 쓴다. 이어 붙은 가림은 하나로 합쳐도
   되지만, 원문의 `[redacted]` 개수보다 **적게** 쓰지 않는다.
8. **뜻이 분명한 OCR 오탈자는 바른 낱말로 옮긴다.** specitic→specific, veritication→verification, perfo1med→performed,
   fron→from, c0A·cOA·coa→COA, tot#·jotX→lot. 숫자·코드·이름은 추측해 고치지 않는다(보이는 대로).
9. **뜻을 알 수 없는 글자 조각**(예: "ws: ahserved onthe inside theIS con")은 그 자리에 `(판독 불가)` 로 한 번만 적는다.
   지어내지 않는다.
10. 번역하지 않은 영어 문장을 남기지 않는다.
11. **옮기지 않은 원문 토큰은 밝힌다.** OCR 이 깨뜨린 숫자·코드(201'1 의 1, LJMS→LIMS, IO→10, POR→PQR, 가림 조각의 4)를
    바로잡거나 뺐으면 그 항목에 `"ocr_dropped": ["LJMS", "IO", …]`(원문 그대로, 10개까지)를 둔다. 검사는 이 토큰만 면제하고,
    원문에 없는 토큰을 적으면 실격이다. 검사를 통과하려고 찌꺼기를 괄호로 덧붙이지 않는다.

## 검사 (`--strict`)
기존 ①~⑤(한글·한자 없음·숫자·[redacted] 개수·길이 0.2~1.3·약어 코드)에 더해
⑥ 양식 칸 ⑦ 업소 머리글 ⑧ 가림 표시 변형 ⑨ 번역 안 된 영어 문장(5낱말 이상·기능어 2개 이상, 따옴표 안 제외)
⑩ 낱말 속 숫자·기호 찌꺼기가 국문에 남으면 실격이다. 새 outbox 파일은 CI(`tests/test_findings_detail_apply.py`)에서
strict 로 검사한다 — 옛 파일은 `tests/fixtures/detail_outbox_legacy.txt` 목록으로만 옛 규칙을 쓴다.
