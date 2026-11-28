# 대외홍보팀 · 네이버 블로그 '왜 중요한가' 슬롯 — 모델 Sonnet · 월 14:00 KST (Routine 등록은 비서실)

이번 주 발행본의 네이버 블로그 원고(`briefs/<발행일>/naver-blog.txt`·`naver-blog.html`)에 들어갈 **블로그 전용 해설 2~3문장**을 쓴다.
원고의 나머지는 빌드가 데이터로 짓는다. 이 칸만 사람 글처럼 '왜 이 지적이 중요한가'를 말한다. 슬롯이 없으면 그 주 원고는 이 칸 없이 나간다(발행을 막지 않는다).
**네이버에 로그인·작성·게시하지 않는다**(게시는 대표). 머지하지 않는다(워치독이 초록 30분 뒤 머지). DB 에 쓰지 않는다.

## 읽을 것 (이것만)
1. `company/editorial/STYLE.md` §7(업체 이름)·§8(AI 티)·§13(후킹)
2. 아래 `--candidate` 출력

`GRM_SYSTEM.md`·`docs/prompts/` 는 읽지 않는다.

## 절차
1. `PUB` = 이번 주 월요일 KST: `PUB=$(TZ=Asia/Seoul date -d "$(TZ=Asia/Seoul date +%F) -$(( $(TZ=Asia/Seoul date +%u) - 1 )) days" +%F)`.
   `B=claude/naver-blog-why-$PUB`, `U=$(echo $PUB | tr - _)`.
2. **먼저 확인(하나라도 맞으면 `스킵: 이번 주 이미 함`)**: `git fetch origin main` 한 `FETCH_HEAD` 에 `web/data/naver_blog/$PUB.json` 이 있다 · 원격에 `$B` 가 있다 · 제목이 `naver-blog(why): $PUB` 로 시작하는 열린 PR 이 있다.
3. `git fetch origin main && git checkout -b "$B" FETCH_HEAD`. `web/data/briefs/brief_web_$U.json` 이 없으면 `보류: 이번 주 발행 전` 으로 끝낸다(push 하지 않는다).
4. `python3.12 web/naver_blog_slot.py --candidate --brief web/data/briefs/brief_web_$U.json > /tmp/nb.json`. `card_id` 가 null 이면 `스킵: 해설할 483 없음` 으로 끝낸다(정상).
5. `web/data/naver_blog/$PUB.json` 을 쓴다:
   `{"schema": "grm-naver-blog-why/v1", "pub": "$PUB", "card_id": "<nb.json 의 card_id>", "why": ["문장1", "문장2", "문장3(선택)"]}`
   - 2~3문장, 문장마다 20~160자. 이 지적이 **제조 현장·품질 체계에서 왜 문제가 되는지**를 말한다(누가 무엇을 했는지 되풀이 금지 — 그건 원고 다른 칸에 있다).
   - **새 사실 금지**: 숫자·날짜·건수는 `material_for_numbers` 안에 있는 것만. 업체 이름·소재지·추측(제재 전망·주가 등)을 쓰지 않는다.
   - **사이트 문장 되풀이 금지**: `site_text_do_not_repeat` 의 문장을 옮기거나 조금 바꿔 쓰지 않는다 — 어절 5개 연속 겹침이 `overlap_max`(25%)를 넘으면 게이트가 떨어뜨린다.
   - 느낌표·과장·'중소/영세/작은 회사' 같은 말·마크다운(`**`, `#`, `|`) 금지. 담백한 평서문.
6. `python3.12 web/naver_blog_slot.py --check web/data/naver_blog/$PUB.json --brief web/data/briefs/brief_web_$U.json` 가 `OK` 일 때까지 고친다(세 번 실패하면 `실패: 게이트` 로 끝내고 push 하지 않는다).
7. 이 파일 하나만 커밋 → `git push -u origin "$B"` → PR 제목 `naver-blog(why): $PUB`, 본문에 `--check` 출력. 다른 파일을 건드리면 워치독이 머지하지 않는다.

## 끝 보고(한 줄)
`완료: #<PR> naver-blog(why) $PUB` · `스킵: …` · `보류: …` · `실패: …` 중 하나.
