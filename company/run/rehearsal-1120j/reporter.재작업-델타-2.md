상태: 완료
요약: 재작업 1회 — 반려 1장(fda483-196348, 영문 게이트) 의 en.implication·en.checks 를 스캐폴드 한국어 안내문에 맞춰 채움. precheck terms exit 0 · 임시 병합 조립 exit 0 · check_en_slots 완결 · rework-diff 통과.
다음: 교열 2차(round 2).
이의: fda483-196348 — 델타 한국어 implication·checks 가 빈 값이면 스캐폴드 기본값이 남아 render 의 영문 짝 판정에 걸리는데, 사전 점검(inject_slots)은 델타 한국어만 보아 통과시킨다. 사전 점검이 스캐폴드 값을 합쳐 보게 고칠 것.
