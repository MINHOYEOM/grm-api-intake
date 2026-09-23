#!/usr/bin/env python3
"""UTM 부착 헬퍼 — 성장 채널 규약 단일 정본(2026-09-23 마케팅 계획).

순수·결정론(네트워크 0·now()/난수 0). ★**RUM 은 쿼리 문자열을 읽지 않는다** —
`funnel_path_counts`(084 마이그레이션)의 클라이언트도 `location.pathname` 만 보고
`location.search`/`href` 는 참조하지 않는다(파일 안 주석 "클라이언트도 location.pathname 만
읽는다" 로 이미 고정돼 있다). 그래서 이 태그는 방문수 집계용이 아니라 **사이트 최초 진입
(first-touch) 퍼널 계측**(`web/templates/base.html`, 087 마이그레이션 `funnel_touch_counts`)
전용 소비자를 위한 것이다. 링크드인 게시 본문(`linkedin_cards.py`)이 첫 소비처.

채널 규약(2026-09-23 마케팅 계획 — 새 채널을 추가하면 이 표부터 갱신한다):

  | 채널             | source       | medium    | campaign                      |
  |------------------|--------------|-----------|--------------------------------|
  | 링크드인 게시물   | linkedin     | social    | `{YYYY-MM-DD}_weekly`          |
  | 링크드인 프로필   | linkedin     | profile   | `featured`                     |
  | 뉴스레터 본문     | newsletter   | email     | `brief_{date}`                 |
  | 뉴스레터 전달     | newsletter   | forward   | `brief_{date}`                 |
  | 카카오톡          | kakao        | share     | `{경로}`                       |
  | 커뮤니티          | community    | post      | `monthly_{YYYY-MM}`            |

★뉴스레터 본문 안의 우리 사이트 링크에는 이 헬퍼를 쓰지 않는다 — `newsletter.gate_provenance`
가 본문 링크에 쿼리 문자열이 붙으면 발송을 막는다(무변형 불변식, `grm-web-publish` 와 별개
규율). 이 모듈은 뉴스레터 "전달" 같은 **본문 밖** 채널과 링크드인처럼 본문이 곧 채널인
경우에만 쓴다.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 소문자 알파벳·숫자·점·밑줄·하이픈만 — GA4/대부분 분석 도구의 UTM 관례와 같다.
_TAG_RE = re.compile(r"^[a-z0-9._-]{1,60}$")

# 링크드인 주간 게시물의 (source, medium) 고정쌍 — campaign 은 발행일마다 갈리므로
# `linkedin_weekly_campaign()` 이 따로 만든다.
LINKEDIN_WEEKLY = ("linkedin", "social")


def _clean(value: str, label: str) -> str:
    """소문자로 낮추고 태그 형식을 검증한다. 형식 밖이면 **조용히 흘리지 않고** 예외를 낸다 —
    잘못된 태그가 퍼널 집계에 섞이면 나중에 알아채기 어렵다."""
    v = (value or "").strip().lower()
    if not _TAG_RE.match(v):
        raise ValueError(
            f"UTM {label} 형식 오류: {value!r} — 소문자/숫자/점(.)/밑줄(_)/하이픈(-) 1~60자만 허용")
    return v


def with_utm(url: str, source: str, medium: str, campaign: str) -> str:
    """`url` 에 `utm_source`·`utm_medium`·`utm_campaign` 을 붙인다.

    세 값은 소문자로 낮춘 뒤 검증한다(`_clean`). 기존 쿼리 문자열·프래그먼트는 그대로
    보존하고(`urllib.parse`), UTM 파라미터는 그 뒤에 이어 붙인다.
    """
    s = _clean(source, "source")
    m = _clean(medium, "medium")
    c = _clean(campaign, "campaign")
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += [("utm_source", s), ("utm_medium", m), ("utm_campaign", c)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def linkedin_weekly_campaign(pub: str) -> str:
    """발행일(`YYYY-MM-DD`) → 링크드인 주간 게시물의 campaign 태그."""
    return f"{pub}_weekly"
