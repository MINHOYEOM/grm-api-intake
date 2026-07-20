# 서비스 업데이트 안내(announcements)

우리 **사이트 자체의 변화**(신규 기능, 새 규제 소스 편입)를 알리는 공지의 단일 정본이다.
그 주 **규제 소식**은 주간 브리프(`web/data/briefs/`)가 담당한다 — 둘을 섞지 않는다.

빌더·게이트·발송은 [`web/announce.py`](../../announce.py).

## 두 채널, 한 원천

| 채널 | 트리거 | 스위치 | 빈도 |
|---|---|---|---|
| 주간 티저 메일에 "서비스 소식" 블록 삽입 | 주간 뉴스레터 발송에 자동 편승 | `weekly_publish_date` = 그 호 발행일 | 필요할 때만 |
| 독립 공지 메일 | `GRM Announce Send` 수동 dispatch | `--id` 로 직접 지정 | 마일스톤에만(분기 1회 이하) |

`weekly_publish_date` 가 `null` 이면 주간 메일에는 얹지 않는다. 한 발행일에 얹을 공지는
**1건만** 허용된다(2건 이상이면 발송이 실패한다 — 코드가 임의로 고르면 조용한 누락이 생긴다).

## 파일 형식

파일명은 `{id}.json` 이고 본문 `id` 와 반드시 일치해야 한다.

```json
{
  "schema_version": "grm-announce/v1",
  "id": "2026-07-new-features",          // 소문자·숫자·하이픈. 발송 멱등 키의 원천.
  "date": "2026-07-20",                  // 공지 날짜(메일에 표시)
  "title": "…",                          // 메일 제목이 된다(접두 "[GRM 안내] " 자동)
  "lede": "…",                           // 선택 — 도입 한 문단
  "weekly_publish_date": null,           // 주간호에 얹을 발행일, 또는 null
  "items": [                             // 1~6건
    { "label": "자료실", "text": "한두 문장", "path": "/library/" }
  ],
  "cta": { "text": "…", "path": "/guide/" }   // 선택
}
```

`path` 는 **사이트 상대경로만** 쓴다(`/` 로 시작). 절대 URL 조립은 빌더가 하므로 외부 링크는
애초에 표현할 수 없고, provenance 게이트가 한 번 더 막는다.

## 지켜야 할 것

1. **문구는 사람이 쓴다.** PR 제목·커밋 로그 자동 추출 금지 — 내부 개념(Tier·registry·백로그
   번호)이 그대로 새어나간다.
2. **없는 걸 알리지 않는다.** 게이트가 모든 `path` 를 실제로 조회해서 broken 이면 발송을
   보류한다. 배포 전 페이지를 미리 공지할 수 없다.
3. **자주 보내지 않는다.** 독립 발송은 해지 유발 1순위다. 자잘한 변화는 `weekly_publish_date`
   로 주간호에 얹고, 독립 발송은 기능군 단위 마일스톤에만 쓴다.
4. **수치는 되도록 쓰지 않는다.** "185건" 같은 숫자는 다음 주에 틀린 말이 된다.

## 사용

```bash
python web/announce.py --mode list                                  # 보유 공지
python web/announce.py --id 2026-07-new-features --mode validate \
       --out email_preview/announce.html                            # 게이트 + 미리보기
python web/announce.py --id 2026-07-new-features --mode test        # 테스트 발송
```

실발송은 로컬에서 하지 않는다 — `GRM Announce Send` 워크플로를 dispatch 한다.
