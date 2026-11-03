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
  | 주간 메일 본문    | newsletter   | email     | `brief_{date}`                 |
  | 영문 메일 본문    | newsletter_en| email     | `brief_{date}`                 |
  | 서비스 안내 메일  | announce     | email     | `service_update`               |
  | 주간 메일 속 소식 | newsletter   | email     | `service_update`               |
  | 용어집 PDF        | glossary_pdf | pdf       | `glossary_pdf`                 |
  | 구독 확인 메일    | welcome      | email     | `confirm`                      |

★2026-11-04 개정 — 우리가 **내보내는 모든 링크**에 표식을 붙인다(성장분석팀 11-04 조사: 가입
경로 '직접' 6/23건의 정체를 못 쪼갠 원인이 메일 본문 링크에 표식이 없던 것). 메일 본문(주간·
서비스 안내·영문)·용어집 PDF·링크드인 캡션 본문 속 주소가 모두 아래 사전 한 곳(`CHANNELS`)
에서 나온다. `gate_provenance` 는 우리 호스트의 `utm_*` 세 키만 허용하므로(2026-09-23 N-03)
카드 출처 URL·외부 링크 차단(무변형 불변식)과 충돌하지 않는다.

Brevo 화면에서 직접 쓰는 두 곳(코드 밖)도 같은 사전을 따른다:
  · 구독 확인 메일 본문의 우리 사이트 링크 → `welcome`/`email`/`confirm`
  · '확인 후 이동' 주소 → `https://grm-solutions.com/welcome/?utm_source=welcome&utm_medium=email&utm_campaign=confirm`
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 소문자 알파벳·숫자·점·밑줄·하이픈만 — GA4/대부분 분석 도구의 UTM 관례와 같다.
_TAG_RE = re.compile(r"^[a-z0-9._-]{1,60}$")

# 링크드인 주간 게시물의 (source, medium) 고정쌍 — campaign 은 발행일마다 갈리므로
# `linkedin_weekly_campaign()` 이 따로 만든다.
LINKEDIN_WEEKLY = ("linkedin", "social")

# 채널 사전 — 이름 → (source, medium, campaign 서식). 새 채널은 여기에 한 줄 더하고 위 표도
# 갱신한다. campaign 서식의 `{date}` 등은 `channel_url()` 인자로 채운다.
CHANNELS: dict[str, tuple[str, str, str]] = {
    "newsletter_body": ("newsletter", "email", "brief_{date}"),
    "newsletter_forward": ("newsletter", "forward", "brief_{date}"),
    "newsletter_en_body": ("newsletter_en", "email", "brief_{date}"),
    "service_update_mail": ("announce", "email", "service_update"),
    "service_update_in_weekly": ("newsletter", "email", "service_update"),
    "glossary_pdf": ("glossary_pdf", "pdf", "glossary_pdf"),
    "linkedin_weekly": ("linkedin", "social", "{date}_weekly"),
    "linkedin_monthly": ("linkedin", "social", "monthly_{month}"),
    "welcome_confirm": ("welcome", "email", "confirm"),
}


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


def channel_url(url: str, channel: str, **fmt: str) -> str:
    """사전(`CHANNELS`)의 채널 표식을 `url` 에 붙인다. 모르는 채널은 예외(조용히 흘리지 않는다)."""
    source, medium, campaign = CHANNELS[channel]
    return with_utm(url, source, medium, campaign.format(**fmt))


_BARE_SITE_RE = re.compile(r"(?<![/\w.])grm-solutions\.com(/[^\s)\]]*)?")


def tag_bare_site_urls(text: str, channel: str, **fmt: str) -> str:
    """글자만 적힌 `grm-solutions.com/경로` 를 스킴 포함 표식 URL 로 바꾼다(링크드인 캡션 본문 —
    스킴이 없어도 링크드인이 링크로 걸어 주지만 표식이 없어 직접 유입으로 섞였다). 이미 `https://`
    가 앞에 붙은 주소(`caption_url` 등)는 건드리지 않는다."""
    def _sub(m: "re.Match[str]") -> str:
        return channel_url("https://grm-solutions.com" + (m.group(1) or "/"), channel, **fmt)
    return _BARE_SITE_RE.sub(_sub, text)
