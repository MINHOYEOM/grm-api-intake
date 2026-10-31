# 대외홍보팀 · 링크드인 주간 초안 확인 — 모델 Sonnet · 월 17:30 KST

PC 예약작업 `grm-linkedin-weekly` 의 **파일 확인 부분**을 옮긴 것이다. 카드 PDF·본문은 사이트 배포가 이미 만든다(`web/linkedin_cards.py`).
클라우드는 파일이 멀쩡한지 보고 링크를 보고한다. **링크드인에 로그인·작성·게시하지 않는다** — 게시는 대표만 한다.

## 읽을 것
`company/CHARTER.md` · `company/CLOUD.md` · 이 파일.

## 절차
1. 최신 발행일: `curl -s https://grm-solutions.com/sitemap.xml | grep -oE 'briefs/20[0-9]{2}-[0-9]{2}-[0-9]{2}/' | sed 's|briefs/||; s|/||' | sort -u | tail -1` → `PUB`.
   주소가 막히면 `보류: 네트워크 — grm-solutions.com`. `PUB` 가 이번 주 월요일(KST)이 아니면 `보류: 이번 주 발행 전` 으로 끝낸다.
2. 네 파일을 `/tmp/li/` 에 새로 받는다: `https://grm-solutions.com/briefs/$PUB/linkedin.pdf` · `linkedin.txt` · `linkedin_en.pdf` · `linkedin_en.txt`.
3. 확인 (`pip install -q pymupdf`):
   - PDF 가 404 이거나 200KB 미만이면 `★카드 생성 실패`(배포의 "LinkedIn cards" 스텝) — 나머지는 건너뛴다.
   - 장 수(7~9장이 보통) · 빈 장 · 글자 깨짐.
   - **영문 PDF 의 한글 글자 수 = 0** 이어야 한다(`re.findall('[가-힣]', 전체 텍스트)`). 있으면 몇 장인지.
   - 영문이 404 면 국문만 — 그 사실 한 줄.
4. 본문(`linkedin.txt`)에 AI 고지가 맨 아래에 있는지, 실명(사람 이름)이 없는지 본다(광고엔 실명 금지).

## 결과 (최종 메시지, 한 줄에 한 가지)
`CHARTER.md` 3줄 → PUB · 카드 장 수(국문·영문) · 문제 있으면 그것 · 네 파일 링크 ·
`대표가 할 일: 링크드인 작성창 → 문서(PDF) 첨부 + 제목 "이번 주 규제 소식 · PUB" → 본문 붙여넣기 → 게시`.
