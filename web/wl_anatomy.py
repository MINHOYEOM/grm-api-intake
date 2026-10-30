"""경고서한 지적 1건(국문)을 '지적 내용 · 회사 회신 · FDA 판단 · FDA 요구'로 나눈다.

## 왜 (2026-10-31)

경고서한 한 조각은 6,000자 안팎의 한 덩어리다. 그 안에는 정해진 순서가 있다 —
조사관이 본 것 → "답변서에서 귀사는 …"(회사가 약속한 것) → "… 미흡하다"(FDA 가 그
약속을 받아들이지 않은 이유) → "본 서한에 대한 답변으로 다음을 제출하라"(FDA 요구).
실무 초심자가 배울 것은 "무엇이 지적됐나"보다 **"어떤 답이 통하지 않았나"**인데, 한
덩어리로는 그 대비가 보이지 않는다. 링크드인에서 같은 서한을 이 순서로 해부한 연재가
반응을 얻는 것도 그 때문이다.

## 규율

  · **FDA 의 문장만 쓴다.** 나누기만 하고 고쳐 쓰거나 요약하지 않는다 — 각 부분은 원래
    국문 문장을 순서 그대로 이어 붙인 것이고, 모두 합치면 원래 본문이 된다(테스트가 고정).
    우리 판단을 덧붙이지 않는 문서 페이지 규율(findings_doc.html 머리 주석)과 같다.
  · **확신이 없으면 나누지 않는다.** 회신 문장과 '미흡' 문장이 둘 다 없으면 None — 화면은
    지금처럼 한 문단을 낸다. 미번역 표지·절단 표시(…)도 그대로 둔다.
  · 결정론·LLM 없음. 번역 문체가 조금씩 달라 표지어는 넉넉히 잡되, 회신 문장은 문장
    **첫머리**에서만 찾는다(본문 중간의 "회신"은 다른 뜻일 수 있다).
"""

from __future__ import annotations

import re
from typing import Any

# 문장 경계 — 국문 본문은 "…다." 로 끝나는 문장과 "시설 유지관리의 결함." 같은 짧은
# 소제목이 섞여 있다. 마침표·물음표 뒤 공백에서 자른다(약어 "e.g." 류는 국문 번역에
# 거의 남지 않고, 남아도 한 문장이 둘로 갈릴 뿐 내용은 잃지 않는다).
_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+")

# "답변서에서 귀사는 …", "귀사의 답변은 …라고 기술한다", "귀사는 2024년 11월 27일자
# 답변서에서 …" — 날짜·수식어가 끼므로 문장 **앞 80자 안**에서 찾는다(첫머리 고정이면
# 실측 표본의 회신 문장 32건 중 4건만 잡혔다).
_RESPONSE_HEAD = re.compile(
    r"^.{0,80}?(?:답변서|회신서|서면\s*답변|답변|회신|응답)"
    r"(?:에서는|에서도|에서|에는|에\s*따르면|을\s*통해|은|는)(?:\s|,)"
)
# 회신을 받아들이지 않은 문장 — 회신/답변을 주어로 '미흡·부적절·불충분'으로 맺는다.
_INADEQUATE = re.compile(
    r"(?:답변|회신|응답)[^.]{0,400}?(?:미흡|부적절|불충분|적절하지\s*않|충분하지\s*않)"
)
# 요구사항 블록 머리 — 서한마다 거의 같은 정형문이다.
_REQUEST_HEAD = re.compile(
    r"(?:본|이)\s*서한에\s*대한\s*(?:답변|회신|응답)(?:으로|에서|에)"
)
# 소제목: 서술 종결 없이 마침표로 끝나는 짧은 명사구("시설 유지관리의 결함.").
# ★'함·음·임'을 서술 종결로 치면 "결함."이 소제목에서 빠진다 — 번역 문체는 "~다." 보고체라
#   명사형 종결은 소제목 쪽에만 나온다.
_HEADING = re.compile(r"^[^.]{2,50}\.$")
_PREDICATE_END = re.compile(r"(?:다|라|요|오)\s*[.?!]$")
# "다음을 관찰하였다."·"예를 들면 다음과 같다." 뒤의 짧은 명사구는 목록 항목이다.
_LIST_INTRO = re.compile(r"다음(?:을|과|의|에)")
# 첫머리 위반 요지가 두 문장일 때(둘째 문장에 조항이 붙는다) 둘 다 요지로 묶는다.
_CITATION = re.compile(r"\(\s*(?:21\s*CFR|21\s*U\.S\.C|섹션|section|FD&C)", re.I)
# "또한 귀사의 답변은 …를 평가하지 않았다" — 회신 머리말 꼴이지만 판단의 연속이다.
_JUDGMENT_MORE = re.compile(
    r"(?:답변|회신|응답)(?:은|에는|에서는)\s.*(?:않았다|않는다|않았으며|없다|못하였다|못했다)\s*[.]$"
)


def _sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(text.strip()) if s]


def _is_heading(sentence: str) -> bool:
    s = sentence.strip()
    return bool(_HEADING.match(s)) and not _PREDICATE_END.search(s) and "(21 CFR" not in s


def split_wl_finding(text_ko: str) -> "dict[str, Any] | None":
    """국문 경고서한 지적 한 건을 나눈다. 나눌 근거가 부족하면 None.

    반환: {"lead": 첫 문장(위반 요지·조항),
           "issues": [{"heading", "observed", "response", "judgment"}...],
           "requests": [요구 문장...], "request_head": 요구 머리 문장}
    각 값은 원문 문장을 공백 하나로 이은 문자열(빈 부분은 "").
    """
    text = (text_ko or "").strip()
    if not text:
        return None
    sents = _sentences(text)
    if len(sents) < 3:
        return None

    req_at = next((i for i, s in enumerate(sents) if _REQUEST_HEAD.search(s)), None)
    body = sents if req_at is None else sents[:req_at]
    tail = [] if req_at is None else sents[req_at:]

    if not any(_RESPONSE_HEAD.search(s) for s in body) or not any(_INADEQUATE.search(s) for s in body):
        return None

    n_lead = 1
    if not _CITATION.search(body[0]):
        n_lead = next((i + 1 for i in range(1, min(3, len(body))) if _CITATION.search(body[i])), 1)
    lead, rest = " ".join(body[:n_lead]), body[n_lead:]
    issues: list[dict[str, list[str]]] = []
    cur: "dict[str, list[str]] | None" = None
    stage = "observed"
    prev = lead
    in_list = False

    def _new(heading: str = "") -> dict[str, list[str]]:
        item = {"heading": [heading] if heading else [], "observed": [], "response": [], "judgment": []}
        issues.append(item)
        return item

    for s in rest:
        short = _is_heading(s)
        # "다음을 관찰하였다." 뒤의 짧은 명사구는 소제목이 아니라 목록 항목이다.
        in_list = short and (in_list or bool(_LIST_INTRO.search(prev)))
        prev = s
        if short and not in_list:
            if cur is not None and not any(cur[k] for k in ("observed", "response", "judgment")):
                cur["heading"].append(s)      # 소제목이 연달아 오면 한 머리로 묶는다
            else:
                cur, stage = _new(s), "observed"
            continue
        if cur is None:
            cur, stage = _new(), "observed"
        # '미흡' 판정을 먼저 본다 — "그러나 귀사의 답변은 미흡하다"는 회신 머리말 꼴이기도 하다.
        if _INADEQUATE.search(s) and stage in ("response", "observed"):
            stage = "judgment"
        elif stage == "judgment" and _JUDGMENT_MORE.search(s):
            pass
        elif _RESPONSE_HEAD.search(s):
            if stage == "judgment":
                # 소제목 없이 다음 회신 주기가 시작됐다 — 앞 주기의 판단과 다음 관찰을 가를
                # 근거가 없으므로 새 항목은 회신부터 시작한다(관찰을 지어내지 않는다).
                cur = _new()
            stage = "response"
        cur[stage].append(s)

    # 회신·판단이 없는 항목(서론성 단락·꼬리 소제목)도 버리지 않는다 — 합치면 원문이어야 한다.
    out_issues = [{k: " ".join(v) for k, v in it.items()} for it in issues]
    if not any(it["response"] and it["judgment"] for it in out_issues):
        return None

    return {
        "lead": lead,
        "issues": out_issues,
        "request_head": tail[0] if tail else "",
        "requests": tail[1:],
    }


def rejoin(parts: dict[str, Any]) -> str:
    """split_wl_finding 결과를 원래 순서로 다시 잇는다(무손실 검증용)."""
    out = [parts["lead"]]
    for it in parts["issues"]:
        out += [it[k] for k in ("heading", "observed", "response", "judgment") if it[k]]
    if parts["request_head"]:
        out.append(parts["request_head"])
    out += parts["requests"]
    return " ".join(out)
