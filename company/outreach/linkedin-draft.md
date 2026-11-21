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
3-1. 사건 해부 7장(수·금 게시분): `https://grm-solutions.com/briefs/$PUB/linkedin_grm_a1.txt`·`linkedin_grm_a1_01.png`~`07.png`(a2 도 같은 꼴, 옛 호는 `linkedin_grm.txt`)가 200 인지 본다. 월요일 게시는 기본판(위 2번)이다.
   200 이면 **국문은 이 7장 + linkedin_grm.txt 가 우선**이다(PC `grm-linkedin-weekly` 도 같은 순서). 영문도 `linkedin_grm_en.txt`·`linkedin_grm_en_01~07.png` 가 200 이면 그것이 우선이다(한글 0자인지 본다). 404 면 슬롯 Routine(`outreach/linkedin-anatomy.md`) 결과 —
   열린 PR `linkedin(anatomy): $PUB` 이 있으면 `머지 대기`, 없으면 `해부 없음(종전 덱)` 한 줄.
4. 본문(`linkedin.txt`)에 AI 고지가 맨 아래에 있는지, 실명(사람 이름)이 없는지 본다(광고엔 실명 금지).

## 방법론 (공개 교본에서 우리에게 맞는 부분만)
출처: [coreyhaines31/marketingskills · social](https://github.com/coreyhaines31/marketingskills)(MIT, B2B 링크드인 유기적 게시 — 훅·카드 문서형·주기·지표).
카드·본문 생성은 코드(`web/linkedin_cards.py`)와 편집 규칙(`company/editorial/STYLE.md`)이 이미 정한다. 이 팀은 그 위에서 "초안이 이 기준에 맞나"를 본다.
- 훅: 첫 문장에 구체적 숫자나 원문 한 구절. 짧은 문단, 사람이 쉬는 곳에서 줄바꿈. 끝은 요점으로 마치고 "어떻게 생각하세요?" 같은 반응 구걸 문장은 쓰지 않는다.
- 링크는 본문에 넣지 않고 첫 댓글에 둔다(외부 링크는 도달을 줄인다). 대표 게시 안내에 "첫 댓글에 사이트 주소" 한 줄을 넣는다.
- 문서형 카드는 이야기 틀을 먼저 정한다: 사건 해부 = 문제→증거→시사점. 한 편에 하나의 메시지, 카드 한 장 = 한 주장.
- AI 티 가려내기: "A가 아니라 B" 대조, 부정 나열, 혼자 묻고 혼자 답하기, 상투 문구, 엠대시. 보이면 문제로 보고(고쳐 쓰지 않고 줄 번호만).
- 주기: 이 팀은 월·수·금 3편(기본판·해부 a1·a2) — 주 3~5편이 기준이므로 늘리지 않는다. 광고성 비중은 작게.
- 지표(링크드인 화면에서 대표가 알려 줄 때만): 노출·댓글(좋아요보다 무겁게)·저장·공유·프로필 방문. 주 1회 상위·하위 3편과 이유 한 줄.
뺀 것: 자동 게시·예약 도구(Typefully·Buffer — 게시는 대표만), 영상·AI 영상 변환 유료 도구, 댓글 달기·DM·새 연결 요청 항목(외부인 연락은 대표 결재 전 금지), 광고·인플루언서·콜드 아웃리치.

## 데이터 (읽을 곳, 전부 읽기 전용)
| 무엇을 보나 | 어디서 | 비고 |
|---|---|---|
| 이번 주 발행 호·카드 파일 | `https://grm-solutions.com/sitemap.xml` · 위 절차의 `linkedin*.pdf`·`.txt`·`linkedin_grm_*` | 공개 사이트, 인증 없음 |
| 해부 슬롯 결과 | 열린 PR `linkedin(anatomy): <날짜>` (`gh api`) | 편집은 `company/outreach/linkedin-anatomy.md` 몫 |
| 링크드인에서 유입된 방문 | Supabase `rum_referrer_daily_clean` · `rum_referrer_path_daily_clean` 의 linkedin 계열 referrer | 합계만, 개인 식별 없음 |
| 게시 반응(노출·댓글) | 링크드인 화면 — 대표가 알려 줄 때만 | 이 팀은 로그인하지 않는다 |

## 평가
- 성과 지표(1~2개): ① 링크드인 유입 방문(`rum_referrer_daily_clean`, 4주 합) ② 게시 편 중 대표가 고치지 않고 올린 비율. 링크드인 화면 숫자는 대표가 줄 때만 쓴다.
- 첫 달 점검 질문: 카드 파일 문제(404·한글 섞임·빈 장)를 게시 전에 잡았나, 게시 뒤에 알았나? AI 티·실명·고지 누락 때문에 대표가 고친 곳은? 링크드인 referrer 가 `rum_*_clean` 에서 구분되어 보이나(안 보이면 지표를 바꿔야 한다)?
- 2개월 판정: 8주 동안 링크드인 유입이 거의 없으면(합계로 한 자리 수) 링크 위치·훅을 바꿔 4주 더 해 보고, 그래도 없으면 주 3편을 1편(기본판)으로 줄이는 것을 대표에게 제안한다. 대표가 고치는 비율이 높으면 확인 기준을 지시문에 추가한다.

## 결과 (최종 메시지, 한 줄에 한 가지)
`CHARTER.md` 3줄 → PUB · 카드 장 수(국문·영문) · 문제 있으면 그것 · 네 파일 링크 ·
`대표가 할 일: 링크드인 작성창 → 문서(PDF) 첨부 + 제목 "이번 주 규제 소식 · PUB" → 본문 붙여넣기 → 게시`.
