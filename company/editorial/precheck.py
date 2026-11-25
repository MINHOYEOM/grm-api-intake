# -*- coding: utf-8 -*-
"""편집팀 사전 점검 — 교열이 잡기 전에 코드가 셀 수 있는 것을 먼저 센다.

    python company/editorial/precheck.py terms <delta 파일>
        델타(샤드 `delta.A.json` 또는 합친 `delta.json`)의 한국어 산문을 조립 표현 검사
        (`inject_slots.lint_prose_style`)와 **같은 규칙**으로 본다 — 금지 표현, [용어·표기] 표 왼쪽 표기,
        약어 단독(CAPA·OOS·COA 가 한글 풀이 없이 나온 카드). 걸린 것이 있으면 한 줄씩 내고 exit 1.
        기자는 샤드를 `완료`로 내기 전에 돌려 0 으로 만든다. 길이 상한도 같이 본다(제목 이슈·hook_line).
        **빈 필수 칸과 영문 짝**도 본다 — 합치기(`merge_parts.py`)가 호 전체를 거부하는 것과 같은 규칙이다:
        한국어 `title_issue`·`summary`·`key_facts` 는 반드시 채우고, 영문 `en` 은 그 셋 + 한국어가 채운 `implication`·`checks` 를 채운다.
        단 `규제 소식`·`지침·안내서`·`고시·개정법령` 카드는 원문에 머리말 사실이 없으면 `key_facts` 를 비워도 된다(2026-11-16).
        카드 유형은 그 주 handoff(`_handoff/handoff_<날짜>.json`, 날짜 = 델타 폴더 이름)에서 읽는다 — `--handoff` 로 직접 줄 수도 있다.
        handoff 를 못 찾으면 종전처럼 전부 필수다.
        **조립 코드 검사도 같은 함수로 돌린다**(`inject_slots.validate_injection` — 개수·길이·마크업·영문 짝·타입 등
        조립이 막는 것 전부. 리허설 1118). 조립 **뒤** 카드에 영문 게이트(`check_en_slots.card_problem`)도 돌린다 —
        조립이 한국어 서사를 갈아 써서 생긴 반쪽 영어(`조립 검사 영문 게이트 …`, 리허설 1120j). 스캐폴드 사본(`_scaffold/brief_web_<날짜>.json`, `--scaffold`)이 있으면 인용 번역 대조까지 본다.
        `--deep <심층 델타·샤드>` 를 주면 심층 칸 산문의 경고(금지 표현·반복 문장)도 낸다. 행정처분 요약 주어 검사도 **경고만**
        (`경고:` 로 시작하는 줄 — exit 코드에 들어가지 않는다). 심층 [용어·표기] 차단은 `deep` 명령이 한다.
        **[용어·표기] 표의 한글 표기·군말('다수'·'장기간' 등)은 v16 문서에서 읽는다**(`inject_slots.v16_terms` — 리허설 1121a:
        표 줄 `외관검사` 가 손 베낌 표에 빠져 있었고 군말 줄은 코드에 0줄이었다). 이 규칙은 **사전 점검 전용**이다 — 발행 조립은 종전 규칙(백업 Routine 보호). `hook_line` 한국어의 목록 약어도 막는다(`hook_line 약어 …`).
        경고만: 영문 `hook_line` 약어(교열 판정이 갈린다)·약어 후보·진술 표기(R3)·정확성 대조(`accuracy_warn_lines` —
        가림 값 채움 의심·가능성 표현 누락·원문에 없는 수·회수 사유 덧붙임).
        **가림·깨진 값 채움**(`guess_check` — 리허설 1121b 전수 교열 반려 21장 중 2/3): 자주 채워 넣는 낱말
        (정제수·용수·물·산도·누설·불순물·엑셀·배지·소독제·내시경·수십·박층)이 사실 칸에 있는데 그 카드 원문 전체
        (handoff 행 + 스캐폴드 결정론 필드)에 대응하는 말이 영어로도 한국어로도 없으면 **막는다**(`가림 값 채움 …`).
        권고 칸(implication·checks)·handoff 행이 없는 카드·관찰 단위 대조·'원료'는 경고만(`가림 값 채움 의심 …`).
        **개인 실명**(2026-11-21 대표 결정)은 경고만(`개인 실명 의심 …` — `private_name_warn_lines`): 원문 483 수신인 칸·
        Mr./Dr. 등 호칭 뒤 이름이 카드 산문에 나오면 한 줄. 업체명 낱말은 뺀다. `deep` 도 같은 함수로 심층 칸·국문 병기를 본다.
        **그룹 등록 후보**(2026-11-23 대표 결정)는 경고만(`그룹 등록 후보: …` — `firm_groups.candidate_lines`, 조립과 같은 함수):
        같은 이름 계열 카드가 등록 그룹(`web/data/firm_groups.json`)·같은 FEI 로 한 묶음이 안 되면 한 줄. 이름으로 묶지는 않는다.
        **원문 대조 4가지**(2026-11-23 — 교열 판정이 회차마다 갈린 유형, `guess_check.delta_source_lines`·`deep_source_lines`):
        막음 = `개인 실명 …`(업체명이 사람 이름 — 이니셜 꼴 — 인데 산문에 그 이름, '개인 사업자'로)·`가림 값 추측 …`(원문 'W [redacted]'
        → 카드 영문 'W equipment/parts' 같은 일반 채움 명사)·`원문에 없는 날짜 …`·`날짜 불일치 …`(`실사일:` 이름표 날짜 ≠ 코드 facts).
        경고 = `진술 단정 의심 …`(원문 직원 진술 문장에만 있는 말을 진술 표기 없이)·`날짜 연도 확인 …`·심층 날짜(OCR 전문이라 경고).

    python company/editorial/precheck.py source <실행 폴더> <handoff 파일> [--scaffold <파일>]
        교열 코드 검사 — 합친 `delta.json`·`deep_delta.json` 에 위 원문 대조 4가지를 **기자와 같은 함수**로 돌린다.
        차단 줄이 있으면 exit 1(교열은 그 카드를 `reject`), 경고 줄은 교열이 2-2 표로 판정하고 근거를 남긴다.

    python company/editorial/precheck.py deep-source <handoff 파일> <deep_delta 파일> --out <검사용 파일>
        심층분석 델타의 각 카드에 그 주 handoff 의 `deep_analysis_input.body_full` 을 `source_text` 로 붙인
        **검사용 사본**을 쓴다. 발행 브릿지가 조립 직전에 하는 일과 같다(`delta_bridge.apply_handoff_source_text`).
        교열의 코드 검사는 이 사본을 `--deep` 에 넘긴다 — 붙이지 않으면 근거 대조가 전부 '원문에 없음'이 된다.
        브릿지의 **중첩 방어**도 같이 한다: 국문 병기(`observations_ko`·`violations_ko`)가 `deep_analysis` 안에 들어 있으면
        카드 층으로 끌어올린다(안 올리면 조립이 번역을 못 보고 483 관찰 국문 없음으로 호 전체를 거부한다 — 리허설 1114b).
        사본은 실행 폴더에 두지 않고 커밋하지 않는다(반납하는 `deep_delta.json` 은 그대로).

    python company/editorial/precheck.py deep <handoff 파일> <심층 샤드 deep.S.json>
        심층 기자가 샤드를 `완료`로 내기 전에 돌린다. FDA 483 카드마다 `observations_ko` 가 **카드 층**
        (`items.<id>.observations_ko` — `deep_analysis` 안이 아니다)에 있고 항목마다 `number`·`deficiency_ko` 가 있는지,
        국문 병기가 `deep_analysis` 안에 잘못 들어가 있지 않은지 본다. 걸리면 한 줄씩 내고 exit 1.
        **심층 칸 산문의 [용어·표기]도 막는다**(`inject_slots.deep_term_hits` — 델타 `terms` 와 **같은 함수**
        `inject_slots.term_hits` 를 슬롯마다 부른다. 리허설 1120b: 경고만이라 교열이 심층 용어로 19장 반려).
        걸린 줄은 `심층 용어 <id>.<슬롯>: <라벨>` 꼴이다. 금지 표현·반복 문장·483 꼴은 여전히 경고만.
        **원문 부재 단정**(`심층 부재 …` — 게이트 3 과 같은 판정 함수 `assemble_publish_brief.false_absence_match`,
        "처분문에 가중·감경 사유는 없다" 꼴 포함)과 **483 관찰 끝 근거 번호**(`심층 근거 번호 …` —
        `inject_slots.deep_483_basis_hits`)도 막는다(리허설 1120c 교열 반려 11장).
        **심층분석 근거 게이트**(`verify_deep_analysis.run_deep_analysis_gate` — 발행 브릿지가 조립 직전에 카드마다 부르는 그 함수,
        원문 = handoff `body_full`)의 FAIL 도 막는다(`심층 게이트 <id>: [FAIL] D2-…` — 리허설 1120d: 조항 인용이 원문 글자와 달라
        조립은 그 카드 심층을 조용히 빼고 교열은 반려했다).
        **국문 병기 관문**(483 관찰·WL 위반항목·WHOPIR — 조립 게이트 4·6·7)도 조립 함수 그대로 막는다(`심층 조립 검사 …` —
        리허설 1120h: 경고서한 `violations_ko` 결손이 통과해 교열 조립이 exit 2). 스캐폴드 사본(`--scaffold`, 없으면
        `_scaffold/brief_web_<날짜>.json`)이 있어야 본다 — 없으면 경고 한 줄을 내고 건너뛴다.
        483 관찰 국문은 배포 게이트(`render.validate_483_observations`)처럼 샤드의 **모든** 483 카드를 본다(리허설 1120i).
        **가림·깨진 값 채움**도 `terms` 와 같은 함수(`guess_check`)로 막는다 — 관찰 국문·위반 서술·FDA 평가 등 사실 칸
        (`가림 값 채움 <id>.deep.<슬롯>`), 원문 = handoff 행(body_full 포함). risk·administrative_risks·required_remediation 은 경고.

    python company/editorial/precheck.py rework-diff <1차 delta.json> <2차 delta.json> <1차 copyedit.json> [--deep-old <1차 deep_delta.json> --deep-new <2차 deep_delta.json>]
        `--deep-old`·`--deep-new` 를 주면 심층(`deep_delta.json`)도 **같은 대조 함수**로 본다 — 반려 카드의 심층만 바뀌어야 한다
        (리허설 1120d: 반려 1장이 심층 칸이었는데 재작업이 델타만 고칠 수 있어 되돌릴 길이 없었다).
        재작업(반려 카드만 한 번 고치기) 뒤에 다시 합친 델타가 **반려 카드만** 바뀌었는지 본다.
        카드가 늘거나 줄었거나, 반려되지 않은 카드·`tldr` 가 한 글자라도 바뀌었으면 exit 1 — 교열은 판정하지 않고 보류한다.
        통과면 반려 카드마다 바뀐 슬롯을 한 줄씩 낸다(안 바뀐 반려 카드도 적는다 — 다시 반려될 카드다).

저장소 루트에서 돌린다. 읽을 수 없으면 exit 2(메시지는 stderr).
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import firm_groups  # noqa: E402  [업체 그룹 묶음 2026-11-23] 조립과 같은 후보 함수
import guess_check  # noqa: E402
import inject_slots  # noqa: E402
import merge_parts  # noqa: E402

KO_REQUIRED = tuple(f for f in merge_parts.EN_FIELDS if f not in merge_parts.EN_OPTIONAL)   # 유형을 모를 때


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_handoff_quiet(folder):
    """실행 폴더 → 그 주 handoff payload. 못 찾거나 못 읽으면 None(카드 유형은 종전처럼 전부 필수, 가림 채움은 차단 없이)."""
    path = merge_parts.find_handoff(folder)
    if not path:
        return None
    try:
        payload = _load(path)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def warn_lines(delta, deep=None):
    """경고만 내는 줄(순수) — 행정처분 요약 주어(⑤)와 심층 칸 산문(④). exit 코드에 들어가지 않는다."""
    out = inject_slots.lint_prose_style(delta).warn_lines()
    if deep is not None:
        out += inject_slots.lint_deep_prose_style(deep).warn_lines()
    return out


def group_candidate_lines(delta, scaffold):
    """[업체 그룹 묶음 2026-11-23] 채택 카드 중 같은 이름 계열인데 등록 그룹·같은 FEI 로 한 묶음이
    안 되는 카드 → '그룹 등록 후보' 경고(막지 않음). 조립(assemble_publish_brief)과 **같은 함수**
    (`firm_groups.candidate_lines`)를 부른다 — 규칙을 베끼지 않는다. 스캐폴드가 없으면 빈 목록."""
    if not isinstance(scaffold, dict):
        return []
    ids = set((delta or {}).get("cards") or {})
    cards = [c for c in scaffold.get("cards") or [] if c.get("id") in ids]
    return firm_groups.candidate_lines(cards)


def term_lines(delta, card_types=None):
    """표현 검사 줄 + 길이 상한 위반 줄(순수). `card_types` = {카드 id: 카드 유형}(선택)."""
    card_types = card_types or {}
    out = [line for line in inject_slots.lint_prose_style(delta, v16_rules=True).lines()
           if not line.startswith("시사점 맺음 반복")]
    hook = inject_slots._HOOK_LINE_MAX
    title = inject_slots._MAX_TITLE_ISSUE
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    for cid, card in cards.items():
        if not isinstance(card, dict):
            continue
        en = card.get("en") if isinstance(card.get("en"), dict) else {}
        ctype = card_types.get(cid)
        ko_gaps = [f for f in merge_parts.ko_required(ctype) if not merge_parts._filled(card.get(f))]
        if ko_gaps:
            out.append(f"빈 필수 칸 {cid}: {', '.join(ko_gaps)}")
        en_gaps = merge_parts._en_gaps(card, ctype)
        if en_gaps:
            out.append(f"영문 슬롯 미완결 {cid}: en.{', en.'.join(en_gaps)}")
        for label, text, limit in (
                ("title_issue", card.get("title_issue"), title),
                ("en.title_issue", en.get("title_issue"), title * 2),
                ("hook_line", card.get("hook_line"), hook["ko"]),
                ("en.hook_line", en.get("hook_line"), hook["en"])):
            if isinstance(text, str) and len(text) > limit:
                out.append(f"길이 {cid}.{label}: {len(text)}자 — {limit}자 이내")
    return out


def gate_lines(delta, card_types=None, scaffold=None):
    """조립 코드 검사(`inject_slots.validate_injection`)를 **그 함수 그대로** 샤드에 돌린 오류 줄(순수).

    ★리허설 1118: 조립 검사는 key_facts ≤4 를 막는데 사전 점검은 그 규칙을 따로 베껴 두지 않아,
      5개짜리 카드 6장이 기자 단계를 통과하고 교열에서 호 전체를 막았다. 규칙을 베끼면 다음 규칙에서 또 어긋나므로
      조립과 **같은 함수**를 부른다 — 조립에 가드가 늘면 사전 점검도 저절로 는다.
    `scaffold`(그 주 스캐폴드)가 있으면 실제 카드(인용·유형)로 본다. 없으면 델타 카드 id·유형으로 최소 카드를 만들어 보고,
    스캐폴드가 있어야만 맞게 셀 수 있는 인용 번역 대조(quotes_translation)는 건너뛴다(거짓 차단 방지)."""
    card_types = card_types or {}
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    ids = set(cards)
    if isinstance(scaffold, dict) and isinstance(scaffold.get("cards"), list):
        brief_cards = [c for c in scaffold["cards"] if isinstance(c, dict) and c.get("id") in ids]
        known = {c.get("id") for c in brief_cards}
        brief_cards += [{"id": cid, "card_type": card_types.get(cid)} for cid in sorted(ids - known)]
        skip_quotes = bool(ids - known)
    else:
        brief_cards = [{"id": cid, "card_type": card_types.get(cid)} for cid in sorted(ids)]
        skip_quotes = True
    report = inject_slots.validate_injection({"cards": brief_cards}, delta)
    return [f"조립 검사 {e}" for e in report.errors
            if not (skip_quotes and "quotes_translation" in e)]


def assembly_lines(delta, scaffold):
    """**조립 함수 전체**(`assemble_publish_brief.assemble_publish_brief`, strict=False)를 이 델타의 카드만으로 돌린
    차단 오류 줄(순수). 슬롯 주입 검증 뒤의 발행본 관문 — 원문 부재 단정(게이트 3)·가림 표기(게이트 8)·
    빈 슬롯·미확인 부재 라벨 등 — 까지 조립과 같은 코드로 본다.

    ★리허설 1120: 슬롯 주입 검증(gate_lines)은 통과했는데 발행본 관문 '원문을 확보했는데 implication 가 부재를 주장'
      이 호 전체를 막았다. 관문을 하나씩 따라가지 않고 조립 함수 자체를 부른다 — 관문이 늘면 저절로 따라온다.
    빼는 것은 **심층 샤드 몫**인 국문 병기 관문(483 관찰·WL 위반·WHOPIR)뿐이다 — 델타 샤드에는 그 재료가 없다.
    스캐폴드가 없으면 빈 목록(이 검사는 실제 카드가 있어야 맞게 센다)."""
    if not (isinstance(scaffold, dict) and isinstance(scaffold.get("cards"), list)):
        return []
    import copy
    import assemble_publish_brief as apb
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    sub = copy.deepcopy(scaffold)
    sub["cards"] = [c for c in sub["cards"] if isinstance(c, dict) and c.get("id") in cards]
    try:
        out, report = apb.assemble_publish_brief(sub, {"cards": cards}, strict=False)
    except (apb.AssembleError, inject_slots.SlotInjectionError, ValueError, TypeError, KeyError) as e:
        return [f"조립 검사 조립 자체가 멈춤 — {type(e).__name__}: {e}"]
    deep_only = deep_ko_errors(out, report)
    return [f"조립 검사 {e}" for e in report.errors if e not in deep_only] + assembled_en_lines(cards, out)


def assembled_en_lines(cards, out):
    """조립 **뒤** 카드에 영문 게이트(`check_en_slots.card_problem` — 교열·발행 워크플로가 부르는 그 판정)를 돌린 줄(순수).

    ★리허설 1120j: 샤드 카드는 한국어·영문 시사점·점검을 둘 다 비워 짝이 맞았는데, 조립이 한국어 서사를 갈아 쓰자
      (483 디제스트 대표) 영문만 비어 교열 영문 게이트가 exit 1 로 재작업을 냈다. 샤드 단계의 짝 검사(`term_lines`)는
      조립 전 카드만 보므로 이런 결손을 못 본다 — 조립 결과에 같은 판정을 돌리고, **조립 전엔 완결이던** 카드만 낸다
      (조립 전부터 빠진 카드는 `영문 슬롯 미완결` 줄로 이미 나간다)."""
    import check_en_slots
    lines = []
    for c in out.get("cards") or []:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid not in cards:
            continue
        prob = check_en_slots.card_problem(c)
        if prob is None:
            continue
        before = dict(cards[cid], id=cid, card_type=c.get("card_type"))
        if check_en_slots.card_problem(before) is None:
            lines.append(f"조립 검사 영문 게이트 {prob[1]} — 조립 뒤 영문 미완결(조립 전엔 완결)")
    return lines + assembled_en_fact_lines(cards, out)


def assembled_en_fact_lines(cards, out):
    """조립 **뒤** 카드에 사이트 빌드의 영문 사실 칸 게이트(`render.validate_brief_en_facts`)를 돌린 줄(순수).

    ★리허설 1122b(2026-11-22): 한국어 '전수 재검사' 를 영문 '100% reinspection' 으로 쓴 카드 5장이 교열 렌더 게이트
      (`check_render_gate.py`)에서 `EN_INVENTED_NUMBER ['100']` 로 걸렸다. 영문은 한국어판·표에 없는 수치를 말하면 안 된다 —
      기자 단계에서 같은 판정을 돌려 교열 전에 고치게 한다."""
    import check_en_slots  # noqa: F401 — web/ 를 경로에 올린다(render import)
    import render
    brief = out.get("brief") or {}
    lines = []
    for c in out.get("cards") or []:
        if not isinstance(c, dict) or c.get("id") not in cards:
            continue
        for v in render.validate_brief_en_facts([{"brief": brief, "cards": [c]}]):
            tail = v.split("EN_INVENTED_NUMBER", 1)[-1].strip()
            lines.append(f"조립 검사 영문 사실 {c.get('id')} — 영문에 한국어판·표에 없는 수치 {tail}"
                         " (예: '전수' 를 '100%' 로 쓰지 말 것 — 수치를 빼거나 한국어판 표현대로)")
    return lines


def deep_ko_errors(out, report):
    """조립 결과에서 **심층 샤드 몫** 국문 병기 관문(게이트 4·6·7 — 483 관찰·WL 위반·WHOPIR)의 오류 집합.
    조립이 부르는 그 함수들을 그대로 부른다 — 델타 검사(`assembly_lines`)는 이 집합을 빼고, 심층 검사(`deep_assembly_lines`)는 이 집합만 낸다."""
    import assemble_publish_brief as apb
    out_cards = out.get("cards") or []
    errs = set(apb._lint_483_observation_ko(out_cards, report.refreshed_483_ids))
    return errs | set(apb._lint_wl_violation_ko(out_cards)) | set(apb._lint_whopir_ko(out_cards))


def deep_assembly_lines(shard, handoff, scaffold):
    """심층 샤드를 **조립 함수 그대로**(`assemble_publish_brief.assemble_publish_brief`, strict=False, deep_deltas=샤드+원문)
    돌려 국문 병기 관문(게이트 4·6·7)의 차단 오류만 낸 줄(순수). `assembly_lines` 가 델타 쪽에서 빼 둔 바로 그 몫이다.

    ★리허설 1120h: `deep` 은 483 `observations_ko` 자리만 따로 세고 있어, 경고서한 카드가 `violations_ko` 없이 '0건 — 통과'로
      나갔고 교열 코드 검사(조립)가 "위반 #1~#3 statement_ko 없음"으로 호 전체를 막았다. 위반항목 블록은 조립이 원문에서
      만들므로(`_refresh_wl_violations`) 규칙을 베끼지 않고 조립 함수를 부른다 — 관문이 늘면 저절로 따라온다.
    스캐폴드가 없으면 빈 목록(실제 카드 유형·결정론 블록이 있어야 맞게 센다)."""
    if not (isinstance(scaffold, dict) and isinstance(scaffold.get("cards"), list)):
        return []
    import copy
    import assemble_publish_brief as apb
    items = shard.get("items") if isinstance(shard.get("items"), dict) else {}
    sourced, _ = with_source_text(items, handoff_bodies(handoff))
    sourced = {cid: e for cid, e in sourced.items() if isinstance(e, dict)}
    sub = copy.deepcopy(scaffold)
    sub["cards"] = [c for c in sub["cards"] if isinstance(c, dict) and c.get("id") in sourced]
    try:
        out, report = apb.assemble_publish_brief(sub, {"cards": {cid: {} for cid in sourced}},
                                                 strict=False, deep_deltas=sourced)
    except (apb.AssembleError, inject_slots.SlotInjectionError, ValueError, TypeError, KeyError) as e:
        return [f"심층 조립 검사 조립 자체가 멈춤 — {type(e).__name__}: {e}"]
    deep_only = deep_ko_errors(out, report)
    lines = [f"심층 조립 검사 {e}" for e in report.errors if e in deep_only]
    # ★리허설 1120i: 조립의 483 관문(게이트 4)은 이번 조립에서 관찰을 새로 만든 카드만 보지만, 배포 게이트
    #   (`render.validate_483_observations`)는 **모든** 483 카드의 관찰을 본다. 관찰 번호가 겹친 카드
    #   (fda483-196254·196260)는 조립 검사 0건으로 지나가고 사이트 빌드에서 막힐 뻔했다. 이 샤드의 카드만
    #   배포 게이트와 같은 범위(전체)로 한 번 더 센다 — 과거 호는 여기 들어오지 않는다.
    seen = set(report.errors)
    for e in apb._lint_483_observation_ko(out.get("cards") or []):
        if e not in seen:
            seen.add(e)
            lines.append(f"심층 조립 검사 {e}")
    return lines


def find_scaffold(delta_path, explicit=None):
    """그 주 스캐폴드 사본 경로(`_scaffold/brief_web_<YYYY_MM_DD>.json`). 없으면 None.
    날짜는 `merge_parts.handoff_date`(desk.json handoff_ref.date → 폴더 이름 → run_date_kst) — handoff 와 같은 함수."""
    if explicit:
        return explicit
    d = merge_parts.handoff_date(os.path.dirname(os.path.abspath(delta_path)))
    if not d:
        return None
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for base in (os.getcwd(), root):
        path = os.path.join(base, "_scaffold", f"brief_web_{d.replace('-', '_')}.json")
        if os.path.exists(path):
            return path
    return None


def handoff_bodies(payload):
    """handoff payload → {web_card_id: body_full}. `delta_bridge.extract_handoff_body_full` 과 같은 규칙."""
    bodies = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        cid = row.get("web_card_id")
        dai = row.get("deep_analysis_input")
        body = dai.get("body_full") if isinstance(dai, dict) else None
        if isinstance(cid, str) and cid and isinstance(body, str) and body.strip() and cid not in bodies:
            bodies[cid] = body
    return bodies


NESTED_TRANSLATION_KEYS = ("observations_ko", "violations_ko")   # delta_bridge 의 중첩 예치 방어와 같은 키


def lift_nested(entry):
    """`deep_analysis` 안에 실린 국문 병기를 카드 층으로 끌어올린 사본(순수). 브릿지 게이트와 같은 규칙."""
    da = entry.get("deep_analysis") if isinstance(entry, dict) else None
    if not isinstance(da, dict) or not any(k in da for k in NESTED_TRANSLATION_KEYS):
        return entry
    da = dict(da)
    entry = dict(entry, deep_analysis=da)
    for key in NESTED_TRANSLATION_KEYS:
        nested = da.pop(key, None)
        if nested and key not in entry:
            entry[key] = nested
    return entry


def with_source_text(deep, bodies):
    """deep 델타 사본에 `source_text` 를 붙이고 중첩된 국문 병기를 끌어올린다(순수). handoff 에 원문이 없는 카드는 원문만 그대로 둔다."""
    out, filled = {}, 0
    for cid, entry in deep.items():
        if isinstance(entry, dict):
            entry = lift_nested(entry)
            if bodies.get(cid):
                entry = dict(entry, source_text=bodies[cid])
                filled += 1
        out[cid] = entry
    return out, filled


def fda483_ids(payload):
    """handoff payload → 심층 대상 FDA 483 카드 id 집합."""
    return {row["web_card_id"] for row in payload.get("rows") or []
            if isinstance(row, dict) and isinstance(row.get("web_card_id"), str)
            and row.get("kind") == "fda-483"}


def deep_term_lines(shard):
    """심층 칸 산문의 [용어·표기] 위반 줄(순수) — 델타와 같은 함수(`inject_slots.term_hits`)를 슬롯마다."""
    return [f"심층 용어 {cid}.{slot}: {lab}" for cid, slot, lab in inject_slots.deep_term_hits(shard, v16_rules=True)]


def deep_gate_lines(shard, handoff):
    """심층분석 근거 게이트 FAIL 줄(순수) — 발행 브릿지(`delta_bridge._gate_deep_analysis`)와 같은 함수·같은 원문.

    브릿지는 FAIL 카드의 심층을 조용히 빼고 발행하지만(graceful degrade) 교열은 그 카드를 반려한다 — 기자 단계에서 0 으로 만든다."""
    import verify_deep_analysis as vda
    items = shard.get("items") if isinstance(shard.get("items"), dict) else shard
    sourced, _ = with_source_text(items or {}, handoff_bodies(handoff))
    out = []
    for cid, entry in sourced.items():
        if not (isinstance(entry, dict) and isinstance(entry.get("deep_analysis"), dict)):
            continue
        try:
            gate = vda.run_deep_analysis_gate(entry["deep_analysis"], entry.get("source_text", ""), card_type=None)
        except Exception as e:  # noqa: BLE001 — 브릿지도 이 경우 카드 심층을 뺀다
            out.append(f"심층 게이트 {cid}: 게이트 실행 오류 — {type(e).__name__}: {e}")
            continue
        if not gate.ok:
            out.extend(f"심층 게이트 {cid}: {f}" for f in gate.findings if f.severity == vda.SEV_FAIL)
    return out


def deep_absence_lines(shard):
    """심층 칸 산문의 원문 부재 단정 줄(순수) — 게이트 3 과 같은 판정 함수. 심층이 있는 카드는 원문 확보 카드다."""
    import assemble_publish_brief as apb
    items = shard.get("items") if isinstance(shard.get("items"), dict) else shard
    out = []
    for cid, entry in (items or {}).items():
        for slot, text in inject_slots._deep_prose_slots(entry):
            m = apb.false_absence_match(text)
            if m:
                out.append(f"심층 부재 {cid}.{slot}: 원문 확보 카드가 부재를 단정 — {m.group(0)!r} (그 문장을 지운다)")
    return out


def deep_basis_lines(shard):
    """483 심층 관찰 끝 근거 번호 '(관찰 N)' 이 없는 줄(순수)."""
    return [f"심층 근거 번호 {cid}.{slot}: 문장 끝에 '(관찰 N)' 이 없다"
            for cid, slot in inject_slots.deep_483_basis_hits(shard)]


_HOOK_CAPS_RE = re.compile(r"(?<![A-Za-z0-9_/-])([A-Z][A-Z0-9/]*[A-Z])s?(?![A-Za-z0-9_/-])")
_ROMAN_RE = re.compile(r"[IVX]+")


def hook_abbr_hits(text, lang):
    """`hook_line` 의 영문 약어 → (목록 약어, 그 밖의 약어 후보)(순수). 규칙은 v16 [용어·표기] 절을 읽어 쓴다
    (`inject_slots.v16_terms` — 약어 목록·목록 밖 약어 예·기관·제도 이름·단위). 숫자가 든 토큰(KF94)·로마 숫자(Class II)는 약어가 아니다.
    목록 = v16 목록 약어 + 목록 밖 약어 예 + 조립 검사의 목록 밖 약어(`inject_slots._ABBR_OFF_LIST`).
    그 밖의 후보는 한국어 두 글자·영문 세 글자 이상만 낸다(US·ID 같은 낱말은 세지 않는다)."""
    rules = inject_slots.v16_terms()
    listed_set = rules["listed"] | rules["off_list"] | {a for a, _ in inject_slots._ABBR_OFF_LIST}
    known = rules["institutions"] | rules["units"]
    listed, other = [], []
    for tok in _HOOK_CAPS_RE.findall(text or ""):
        if tok in listed_set:
            listed.append(tok)
        elif (tok not in known and not _ROMAN_RE.fullmatch(tok) and not re.search(r"\d", tok)
              and len(tok) >= (2 if lang == "ko" else 3)):
            other.append(tok)
    return sorted(set(listed)), sorted(set(other))


_STATEMENT_RE = re.compile(r"\b(according to|stated|states|reported|told|explained|indicated|informed|said|"
                           r"confirmed|claimed|acknowledged|admitted)\b", re.I)
_STATEMENT_MARK = {"ko": re.compile(r"진술|말했|밝혔|설명했|보고했|답했"),
                   "en": re.compile(r"\b(stated|said|according|reported|told|claimed|per)\b", re.I)}
_SENTENCE_RE = re.compile(r"(?<=[.;])\s+")


def hook_statement_hits(text, lang, card):
    """`hook_line` 숫자 중 483 원문 관찰에서 **직원·경영진 말 문장에만** 있는 것(순수 — 진술 표기가 있으면 빈 목록).
    STYLE §12 R3('업체 직원의 말은 진술'). 숫자 없는 진술 사실은 못 잡는다 — 경고만(리허설 1121a 교열 반려 3장 중 1장)."""
    if not text or _STATEMENT_MARK["ko" if lang == "ko" else "en"].search(text):
        return []
    dd = card.get("deterministic_detail") if isinstance(card, dict) else None
    obs = dd.get("observations") if isinstance(dd, dict) else None
    said, other = set(), set()
    for o in obs or []:
        if not isinstance(o, dict):
            continue
        for s in _SENTENCE_RE.split(" ".join(str(o.get(k) or "") for k in ("deficiency", "detail"))):
            (said if _STATEMENT_RE.search(s) else other).update(re.findall(r"\d+", s))
    only = said - other
    return sorted({n for n in re.findall(r"\d+", text) if n in only})


def hook_lines(delta, scaffold=None):
    """(막는 줄, 경고 줄) — `hook_line` 을 화면과 같은 함수로 본다(순수).

    막는 줄 = 금칙어(`render.hook_line_banned`, STYLE §12 ① — 교열이 반려한다, 리허설 1120c 2장).
    경고 줄 = 스캐폴드 카드와 합쳐 `render._valid_hook_line` 이 숨기는 경우(숫자 대조 등 — 교열 판정과 갈려 막지 않는다)."""
    web = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "web")
    if web not in sys.path:
        sys.path.insert(0, web)
    import render
    block, warn = [], []
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    sc = {}
    if isinstance(scaffold, dict) and isinstance(scaffold.get("cards"), list):
        sc = {c.get("id"): c for c in scaffold["cards"] if isinstance(c, dict)}
    for cid, d in cards.items():
        if not isinstance(d, dict):
            continue
        en = d.get("en") if isinstance(d.get("en"), dict) else {}
        for lang, label, raw in (("ko", "hook_line", d.get("hook_line")), ("en", "en.hook_line", en.get("hook_line"))):
            if not (isinstance(raw, str) and raw.strip()):
                continue
            bad = render.hook_line_banned(raw, lang)
            listed, other = hook_abbr_hits(raw, lang)
            if listed and lang == "ko":
                block.append(f"hook_line 약어 {cid}.{label}: {', '.join(listed)} — 한 줄 안에서는 목록 약어도 한글로만(v16 [용어·표기])")
            elif listed:
                warn.append(f"hook_line 약어 {cid}.{label}: {', '.join(listed)} — 영문 한 줄의 약어 금지는 교열 판정이 갈린다(STYLE §12 R7 대 v16 '영문은 원문 공식 용어')")
            if other:
                warn.append(f"hook_line 약어 후보 {cid}.{label}: {', '.join(other)} — 기관·제도 이름이 아니면 풀어 쓴다(STYLE §12 R7)")
            if cid in sc:
                st = hook_statement_hits(raw, lang, sc[cid])
                if st:
                    warn.append(f"hook_line 진술 표기 {cid}.{label}: 숫자 {', '.join(st)} 는 원문에서 직원·경영진 말(According to·stated 등)에만 있다 — '진술'로 표기(STYLE §12 R3)")
            if bad:
                block.append(f"hook_line 금칙어 {cid}.{label}: {', '.join(bad)} — 다른 말로 쓰거나 필드를 지운다(STYLE §12 ①)")
            elif cid in sc:
                base = sc[cid]
                card = dict(base, hook_line=d.get("hook_line"),
                            en=dict(base.get("en") or {}, hook_line=en.get("hook_line")))
                if not render._valid_hook_line(card, "ko" if lang == "ko" else "en"):
                    warn.append(f"hook_line 화면에서 숨김 {cid}.{label} — 숫자가 코드 facts·상세에 글자 그대로 있는지 본다")
    return block, warn


# ─────────────────────────────────────────────────────────────────────────────
# [리허설 1121a] 교열 정확성 반려(R10 가림 값 채움·가능성 누락·세어서 만든 숫자·회수 사유 덧붙임)를 근거 대조로 **경고**한다.
# 넷 다 '원문 낱말 ↔ 카드 국문 낱말' 대응을 코드가 확정할 수 없어(번역·바꿔 쓰기) 막지 않는다 — 세 리허설 실측은 PR 본문.
# ─────────────────────────────────────────────────────────────────────────────
_REDACT_RE = re.compile(r"\[redacted\]|\(b\)\s?\(4\)|\[판독불가\]|\[비공개\]", re.I)
_REDACT_NEAR = 25    # 숫자 앞뒤 이 글자 안에 가림 표지가 있어야 센다(문장 단위는 483 원문이 가림투성이라 통과 카드 절반이 걸렸다)
_HEDGE_SRC_RE = re.compile(r"\b(potential(ly)?|possible|possibly|may|might|could|suspected)\b", re.I)
_HEDGE_KO_RE = re.compile(r"의심|수 있|가능|잠재|추정|우려")
_COUNT_RE = re.compile(r"(?<![\d.,:/~-])(\d{1,3})\s?(건|개|회|명|곳|차례|배치|로트|종|품목|바이알|병|봉)")
_EN_NUM = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_RECALL_FRAME = ("회수", "영업", "사유", "강제", "명령", "으로", "품목", "제품", "동일", "일괄")


def _obs_text(card):
    dd = card.get("deterministic_detail") if isinstance(card, dict) else None
    obs = dd.get("observations") if isinstance(dd, dict) else None
    return [" ".join(str(o.get(k) or "") for k in ("deficiency", "detail")) for o in obs or [] if isinstance(o, dict)]


def _num_in_source(n, src):
    if re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", src):
        return True
    return any(v == n and re.search(rf"\b{w}\b", src, re.I) for w, v in _EN_NUM.items())


def count_number_hits(text, src):
    """'8건'·'14회' 같은 세는 숫자 중 원문(`src`)에 숫자로도 영어 낱말로도 없는 것(순수 — v16 R2 '세어서 만든 숫자 금지').
    조립 근거 게이트 D3 는 4자리 이상만, 화면 숫자 대조는 `hook_line` 만 봐서 심층 '편차 보고서 8건'(원문 목록 8줄·서로 다른 보고서 7건)을 놓쳤다."""
    if not src:
        return []
    return sorted({m.group(1) + m.group(2) for m in _COUNT_RE.finditer(text or "") if not _num_in_source(m.group(1), src)})


def redaction_anchor_hits(text, obs_texts):
    """카드 숫자(두 자리 이상) 중 원문 관찰에서 **가림 표지([redacted]·(b)(4)) 바로 곁에만** 나오는 것(순수 — STYLE R10).
    그 문장을 근거로 쓴 카드가 가려진 값을 구체 명사로 채웠는지 사람이 대조한다(1121a '유효기간 지난 부품 49일')."""
    marked, clean = set(), set()
    for t in obs_texts:
        for m in re.finditer(r"(?<![\d.])\d{2,}(?!\d)", t):
            near = t[max(0, m.start() - _REDACT_NEAR):m.end() + _REDACT_NEAR]
            (marked if _REDACT_RE.search(near) else clean).add(m.group(0))
    only = marked - clean
    return sorted({n for n in re.findall(r"(?<![\d.])\d{2,}(?!\d)", text or "") if n in only})


def _card_slots(d):
    out = [(k, d.get(k)) for k in ("title_issue", "hook_line", "summary", "implication")]
    for k in ("key_facts", "checks"):
        out += [(f"{k}[{i}]", x) for i, x in enumerate(d.get(k) or [])]
    return [(k, v) for k, v in out if isinstance(v, str) and v.strip()]


def accuracy_warn_lines(delta, scaffold=None, deep=None, handoff=None):
    """정확성 경고 줄(순수) — 막지 않는다. 스캐폴드가 없으면 델타 쪽은 건너뛴다(원문이 없으면 대조를 못 한다)."""
    out = []
    sc = {}
    if isinstance(scaffold, dict) and isinstance(scaffold.get("cards"), list):
        sc = {c.get("id"): c for c in scaffold["cards"] if isinstance(c, dict)}
    cards = delta.get("cards") if isinstance(delta, dict) and isinstance(delta.get("cards"), dict) else {}
    for cid, d in cards.items():
        c = sc.get(cid)
        if not (isinstance(d, dict) and isinstance(c, dict)):
            continue
        dtype = (c.get("deterministic_detail") or {}).get("type") if isinstance(c.get("deterministic_detail"), dict) else None
        obs = _obs_text(c)
        facts = " ".join(str(f.get("value") or "") for f in c.get("facts") or [] if isinstance(f, dict))
        facts += " " + str((c.get("deterministic_detail") or {}).get("count") or "")
        quotes = " ".join(str(q.get("original") or "") for q in c.get("quotes") or [] if isinstance(q, dict))
        for slot, text in _card_slots(d):
            if obs:
                for n in redaction_anchor_hits(text, obs):
                    out.append(f"가림 값 채움 의심 {cid}.{slot}: 숫자 {n} 의 원문 문장에 가림 표지가 있다 — 가려진 값을 명사로 채우지 않았는지(STYLE R10)")
                for n in count_number_hits(text, " ".join(obs) + " " + facts):
                    out.append(f"원문에 없는 수 {cid}.{slot}: '{n}' — 원문 숫자·낱말에 없다(세어서 만든 숫자 금지, v16 R2)")
        if dtype == "openfda_recall_detail" and _HEDGE_SRC_RE.search(quotes):
            for slot in ("title_issue", "hook_line"):
                text = d.get(slot)
                if isinstance(text, str) and text.strip() and not _HEDGE_KO_RE.search(text):
                    out.append(f"가능성 표현 누락 {cid}.{slot}: 원문 사유가 '{_HEDGE_SRC_RE.search(quotes).group(0)}' 인데 단정형 — '의심' 등으로(v16 문장 규칙 ④)")
        if dtype == "mfds_recall_detail":
            src = " ".join(str(f.get("value") or "") for f in c.get("facts") or [] if isinstance(f, dict))
            src += " " + quotes + " " + str(c["deterministic_detail"].get("enforcement") or "")
            for slot in ("title_issue", "hook_line", "summary"):
                text = d.get(slot)
                novel = sorted({w for w in re.findall(r"[가-힣]{2,}", text or "")
                                if w[:2] not in src and not w.startswith(_RECALL_FRAME)})
                if novel:
                    out.append(f"회수 사유 덧붙임 {cid}.{slot}: {', '.join(novel)} — 원문 제품명·사유에 없는 낱말(v16 문장 규칙 ⑤)")
    bodies = handoff_bodies(handoff) if isinstance(handoff, dict) else {}
    items = deep.get("items", deep) if isinstance(deep, dict) else {}
    for cid, entry in (items or {}).items():
        src = bodies.get(cid)
        if not src:
            continue
        for slot, text in inject_slots._deep_prose_slots(entry):
            for n in count_number_hits(text, src):
                out.append(f"원문에 없는 수 {cid}.deep.{slot}: '{n}' — 원문 숫자·낱말에 없다(세어서 만든 숫자 금지, v16 R2)")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# [리허설 1121] 개인 실명 — 2026-11-21 대표 결정: 공직자·규제기관 공식 발표 속 직함상 공식 인물 외에는 개인 실명을 싣지 않는다.
# 원문에서 **개인 이름 자리가 확실한 곳**만 이름을 뽑는다: 483 머리말 'INDIVIDUAL TO WHOM REPORT (IS) ISSUED' 칸(수신인),
# 'Mr./Mrs./Ms./Dr./Prof.' 호칭 바로 뒤(경고서한 'Dear Mr. …'·WHOPIR 'Mr. … as Managing Director'). 업체명(`firm`·`headline`)에
# 든 낱말은 여기서 뺀다 — 업체명 자체가 사람 이름인 경우는 `guess_check.person_firm_lines` 가 **막는다**(2026-11-23,
# 11-23 실발행 196329·196343 — 이 경고가 업체명이라며 빼던 자리). 번역·음역은 못 잡는다 — **경고만**.
# ─────────────────────────────────────────────────────────────────────────────
# [2026-11-25 후보 좁히기] 이름 칸은 **줄을 넘지 않는다**('Mr. Ravel Okonder⏎Chief Executive Officer' 의 'Chief'), 마침표 없는
# 'Dr' 은 줄바꿈 앞이거나 같은 줄 앞에 번지수가 있으면 주소의 Drive 다('616 Atrium Dr Ste 100' 의 'Ste'). 수신인 칸 안에
# 실사일 칸이 끼면 'TO:' 뒤만 본다('DATE(S) OF INSPECTION September 19 … TO: Tor Vasky' 의 'September'). 직함 낱말이 나오면
# 거기서 이름이 끝난다('Pelkastrom Chief Executive Officer'·'Morlai Zha (Vice President)'). 호칭 뒤 이름 바로 다음에
# 법인명이 붙으면(경고서한 'U.S. Agent: Mr. Dovan Krest Atlantic Wellcare Systems Corp.') 마지막 낱말은 법인명이다.
_HONORIFIC_NAME_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof)(?:\.\s*|,?[ \t]+)((?:[A-Z][A-Za-z'\-]+\.?[ \t]?){1,3})")
_ISSUED_TO_RE = re.compile(r"INDIVIDUAL\s+TO\s+WHOM\s+REPORT\b.{0,12}?ISSUED(.{0,160}?)FIRM\s*NAME", re.S | re.I)
_ISSUED_LEAD_RE = re.compile(r"^(?:[\W_]*(?:To|TO)\s*:)?[\W_]*(?:(?:Mr|Mrs|Ms|Dr|Prof)(?:[.,]\s*|\s+))?")
_ISSUED_TO_MARK_RE = re.compile(r"\b(?:[Tt]o|TO)\s*:|\bTO\b(?=\s+[A-Z])")
_NMI_RE = re.compile(r"\(\s*N\.?\s*M\.?\s*I\.?\s*\)\s*", re.I)   # '(NMI)' = 가운데 이름 없음 표기
_NAME_TOKEN_RE = re.compile(r"[A-Z][A-Za-z'\-]{2,}")
_ATTN_BEFORE_RE = re.compile(r"\bATTN\s*:?\s*$", re.I)
_ORG_TAIL_RE = re.compile(r"(?:[A-Z][\w&'\-]*\.?,?[ \t]+){0,5}?(?:Corp|Corporation|Inc|Ltd|LLC|Limited|Group|GmbH|Co|"
                          r"Company|Pvt|AG|plc|Pharma|Pharmaceuticals?|Laborator(?:y|ies))\b")
# 이름이 끝나는 직함 낱말 — 이 낱말부터 뒤는 이름이 아니다.
_TITLE_WORDS = frozenset((
    "Chief Executive Officer Officers President Vice Director Directors Chairman Chairwoman Chair Owner Owners Manager "
    "Managers General Secretary Treasurer Head Senior Sr Operations Operating Operation Quality Managing Founder "
    "Co-Founder Partner Plant Site Factory Corporate Registrar Agent Supervisor Representative Principal Proprietor "
    "Administrator Pharmacist Member Lead Assistant Associate Deputy Global Regional Responsible Production "
    "Manufacturing Technical Scientific Medical Compliance Regulatory Affairs Board Executive Investigator "
    "Superintendent Coordinator Specialist Engineer Analyst Officer Pharmacy Leader Group Division Department").split())
# 이름 자리에 끼어드는 비-이름 낱말 — 주소 약어·서식 글자·달 이름. 뺀다(이름은 거기서 끝나지 않는다).
_NOT_NAME_WORDS = frozenset((
    "Ste Suite Street Road Avenue Ave Blvd Boulevard Drive Floor Building Bldg Unit Lane Highway Hwy Parkway Pkwy "
    "Annotations Annotation Observations Observation Page Employee Employees Signature Inspection Date Dates Number "
    "January February March April May June July August September October November December "
    "Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec").split())


def _name_tokens_of(cand):
    """후보 문자열 → 이름 낱말(직함 낱말에서 끊고, 비-이름 낱말은 건너뛴다)."""
    toks = []
    for t in _NAME_TOKEN_RE.findall(cand):
        if t in _TITLE_WORDS:
            break
        if t not in _NOT_NAME_WORDS and not t.isupper():   # 전부 대문자 = 서식 글자(FIRM·NAME·NMI·STREET)
            toks.append(t)
    return toks


def _issued_head(seg):
    """수신인 칸 조각 → 이름 부분(쉼표·대시·괄호·세로줄 앞)."""
    seg = _ISSUED_LEAD_RE.sub("", seg.strip())
    return re.split(r",| - | — |–|\s\(|\|", seg, maxsplit=1)[0]


def _person_candidates(text):
    """원문 → [(역할, 후보 문자열)](순수). 역할: recipient(483 수신인 칸)·fda_contact('ATTN:' 뒤 호칭)·person(그 밖의 호칭 뒤)."""
    out = []
    for m in _HONORIFIC_NAME_RE.finditer(text):
        head = m.group(0)
        line_before = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if not head.startswith(("Dr.", "Prof.", "Mr.", "Mrs.", "Ms.")) and head.startswith("Dr") \
                and re.search(r"\d", line_before):
            continue                                   # 'Patterson Dr Ste 100' — 번지수 뒤 Dr 은 Drive
        cand = m.group(1)
        if re.match(r"[A-Z][A-Za-z\-]*['’]s\b", cand):
            continue                                   # "Dr. Reddy's Laboratories" — 소유격은 회사명
        parts = _NAME_TOKEN_RE.findall(cand)
        if len(parts) >= 2:
            last = cand.rfind(parts[-1])
            if _ORG_TAIL_RE.match(text, m.start(1) + last):
                cand = cand[:last]                     # 'Dovan Krest Atlantic Wellcare … Corp.' → 'Dovan Krest'
        role = "fda_contact" if _ATTN_BEFORE_RE.search(text[max(0, m.start() - 12):m.start()]) else "person"
        out.append((role, cand))
    for m in _ISSUED_TO_RE.finditer(text):
        block = _NMI_RE.sub(" ", re.sub(r"\s+", " ", m.group(1))).strip()
        cand = _issued_head(block)
        marks = list(_ISSUED_TO_MARK_RE.finditer(block))
        if marks:                                      # 실사일·FEI 칸이 끼어든 머리글 — 'TO:' 뒤가 수신인
            after = _issued_head(block[marks[-1].end():])
            if _name_tokens_of(after):                 # '… Site Lead TO: — STREET ADDRESS' 처럼 뒤가 비면 앞을 쓴다
                cand = after
        out.append(("recipient", cand))
    return out


def source_person_roles(text, firm_text=""):
    """원문 → {개인 이름 낱말: 역할}(순수). 업체명에 든 낱말·전부 대문자 낱말(FIRM·NAME·NMI 등 서식 글자)은 뺀다.
    한 낱말이 여러 역할로 나오면 recipient > fda_contact > person."""
    firm_low = (firm_text or "").lower()
    rank = {"recipient": 0, "fda_contact": 1, "person": 2}
    roles = {}
    for role, cand in _person_candidates(text or ""):
        for t in _name_tokens_of(cand):
            if t.isupper() or t.lower() in firm_low:
                continue
            if t not in roles or rank[role] < rank[roles[t]]:
                roles[t] = role
    return roles


def merge_person_roles(texts, firm_text="", exclude_text=""):
    """여러 원문 조각 → {낱말: 역할}(순수). 조각마다 `source_person_roles` 를 돌려 합친다(머리글 정규식이 조각 경계를 넘지 않게).
    `exclude_text`(FDA 실사관 서명 등 공식 인물)에 든 낱말은 뺀다."""
    rank = {"recipient": 0, "fda_contact": 1, "person": 2}
    excl = set(re.findall(r"[A-Za-z'\-]+", exclude_text or ""))
    roles = {}
    for text in texts:
        for t, role in source_person_roles(text, firm_text).items():
            if t not in excl and (t not in roles or rank[role] < rank[roles[t]]):
                roles[t] = role
    return roles


def source_person_names(text, firm_text=""):
    """원문 → 개인 이름 낱말 집합(순수). `source_person_roles` 의 낱말."""
    return set(source_person_roles(text, firm_text))


# ── 이름 자리 찾기 — 판정(게이트·사전 점검)과 가림(발행 조립)이 **같은 함수**를 쓴다 ─────────────────
# 이름 낱말 앞뒤로 호칭·이니셜·짧은 낱말('Dr. Ko Zun'·'Mr. XO Brimtal'·'Darvin G. Ostrell'·'R.K.T.P.L. Dravesh')을 묶어
# 한 자리로 본다. [단일 낱말 보완] 호칭도 다른 이름 낱말도 없이 **낱말 하나만** 나온 자리는, 그 낱말이 보통 낱말이기도 하면
# (같은 글에 소문자로 나오거나 흔한 영어 낱말 — 'More'·'Else'·'Key'·'Spain' 의 문장 첫머리) 이름으로 세지 않는다.
# 호칭 바로 뒤 대문자 낱말 둘까지는 이름 자리에 넣는다 — 업체명이 아들 이름인 꼴('Norvex Lifesciences (formerly Pelvik)' 의
# 'Mr. Pelvik Velloc')에서 'Mr. Pelvik [업체 임원]' 이 남지 않게.
# [2026-11-25] 정규식 하나로 짜면 이니셜·짧은 낱말이 길게 이어진 OCR 글에서 역추적이 폭발한다(지수 시간) —
# 낱말을 한 번 훑는 선형 스캔으로 한다.
_SHORT_STOP = frozenset("To At In Of By On An As If It No Or So Up We He My Is Be Do Go Am Us".split())
_HONORIFICS = frozenset(("Mr", "Mrs", "Ms", "Dr", "Prof"))
_SEP_RE = re.compile(r"[ \t]*(?:\r?\n[ \t]*)?")   # 이름 사이 = 공백, 줄바꿈은 한 번까지(OCR 머리글 'Mark\nS. Kelvaro')
_CAP_TOKEN_RE = re.compile(r"(?<![A-Za-z'\-])[A-Z](?:\.[A-Z])+\.?|(?<![A-Za-z'\-])[A-Z][A-Za-z'\-]*\.?")
_COMMON_WORDS = frozenset((
    "more else key will may mark bill rose grant hope joy faith summer winter spring fall bell white black brown green "
    "gray grey small long short young king rich lane field hall wood stone page case cook price spain china france "
    "india japan jordan israel georgia chad").split())


def _cap_tokens(text, nameset):
    """대문자로 시작하는 낱말 → [(시작, 끝, 종류, 낱말)]. 종류: hon(호칭)·name(원문 이름)·init(이니셜)·short(1~2자)·cap(그 밖)."""
    out = []
    for m in _CAP_TOKEN_RE.finditer(text):
        raw = m.group(0)
        dot = raw.endswith(".")
        word = raw[:-1] if dot else raw
        start, end = m.start(), m.end()
        if word in nameset and "." not in word:
            kind, end = "name", start + len(word)          # 'Ostrell.' 의 마침표는 이름 밖
        elif word in _HONORIFICS:
            kind = "hon"
        elif re.fullmatch(r"[A-Z](?:\.[A-Z])*", word) and dot:
            kind = "init"                                  # 'G.'·'M.V.V.S.N.'
        elif re.fullmatch(r"[A-Z][a-z]?", word) and word not in _SHORT_STOP:
            kind = "short"                                 # 'Lu'·'G'
        else:
            kind, end = "cap", start + len(word)
        out.append((start, end, kind, word))
    return out


def _adjacent(text, a_end, b_start, after_hon=False):
    gap = text[a_end:b_start]
    return bool(re.fullmatch(r"\s*", gap)) if after_hon else bool(_SEP_RE.fullmatch(gap))


def _ambiguous_single(token, text):
    low = token.lower()
    return low in _COMMON_WORDS or low in guess_check._EN_STOP or bool(
        re.search(rf"(?<![A-Za-z]){re.escape(low)}(?![A-Za-z])", text))


def person_name_spans(names, text):
    """[(시작, 끝, 걸린 이름 낱말들)](순수) — 산문에서 원문 개인 이름이 나오는 자리."""
    names = [n for n in (names or ()) if n]
    if not (names and text):
        return []
    toks = _cap_tokens(text, set(names))
    unit = ("name", "init", "short")
    out, i, n = [], 0, len(toks)
    while i < n:
        if toks[i][2] not in unit:
            i += 1
            continue
        e = i                                                  # 이니셜·짧은 낱말·이름이 붙어 이어진 줄 [i..e]
        while e + 1 < n and toks[e + 1][2] in unit and _adjacent(text, toks[e][1], toks[e + 1][0]):
            e += 1
        names_at = [k for k in range(i, e + 1) if toks[k][2] == "name"]
        if not names_at:
            i = e + 1
            continue
        last = names_at[-1]
        end = toks[last][1]
        tail = last + 1                                        # 'Mark S. Kelvaro' — 이름 뒤 이니셜 하나(성 앞)
        if (tail <= e and toks[tail][2] == "init" and len(toks[tail][3]) == 1 and tail + 1 < n
                and re.fullmatch(r"\s+", text[toks[tail][1]:toks[tail + 1][0]])):
            end = toks[tail][1]
        start, hon = toks[i][0], False
        for k in range(0, 4):                                  # 호칭 + (전부 대문자 1) + 대문자 낱말 0~2 + 이름 줄
            j = i - k - 1
            if j < 0:
                break
            mids = toks[j + 1:i]
            if any(t[2] != "cap" for t in mids) or (k == 3 and not mids[0][3].isupper()):
                break
            if any(not _adjacent(text, a[1], b[0]) for a, b in zip(toks[j + 1:i], toks[j + 2:i + 1])):
                break
            if toks[j][2] == "hon" and _adjacent(text, toks[j][1], toks[j + 1][0], after_hon=True):
                start, hon = toks[j][0], True
                break
        hit = [toks[k][3] for k in names_at]
        single = i == last and end == toks[last][1]
        if hon or not (single and _ambiguous_single(hit[0], text)):
            out.append((start, end, hit))                      # 낱말 하나뿐인 자리 + 보통 낱말 — 이름으로 세지 않는다
        i = e + 1
    return out


def person_name_hits(names, text):
    """산문에 나온 원문 개인 이름 낱말 집합(순수) — 게이트·사전 점검의 판정."""
    return {t for _, _, hit in person_name_spans(names, text) for t in hit}


def redact_person_names(text, roles, labels):
    """(가린 문자열, 바꾼 자리 수)(순수). `roles` = `source_person_roles` 결과, `labels` = {역할: 표기}.
    자리마다 그 자리 낱말의 역할(recipient > fda_contact > person)로 표기를 고른다. 원문 저장본에는 쓰지 않는다 — 화면 사본 전용."""
    spans = person_name_spans(list(roles), text)
    if not spans:
        return text, 0
    rank = {"recipient": 0, "fda_contact": 1, "person": 2}
    parts, pos = [], 0
    for start, end, hit in spans:
        role = min((roles[t] for t in hit), key=rank.__getitem__)
        seg = text[start:end]
        trail = seg[len(seg.rstrip()):]
        parts += [text[pos:start], labels.get(role) or labels["person"], trail]
        pos = end
    parts.append(text[pos:])
    return "".join(parts), len(spans)


def _name_source(row, sc):
    src = guess_check.card_source(row, sc)
    if isinstance(row, dict) and row.get("ncr_translation_input"):
        src += "\n" + guess_check._dump(row["ncr_translation_input"])
    return re.sub(r"\\[nrt]", " ", src)   # JSON 덤프의 '\n' 글자 — 남겨 두면 'Mr' 앞 낱말 경계가 사라진다


def _firm_text(row, sc):
    parts = []
    if isinstance(row, dict):
        parts += [str(row.get(k) or "") for k in ("firm", "headline")]
        pi = row.get("prose_input")
        if isinstance(pi, dict):
            parts.append(str(pi.get("firm_or_product") or ""))
    if isinstance(sc, dict):
        parts += [str(f.get("value") or "") for f in sc.get("facts") or []
                  if isinstance(f, dict) and f.get("label") in ("제조소/업체", "주제", "업체")]
    return " ".join(parts)


def private_name_warn_lines(delta=None, deep=None, rows=None, scaffold_cards=None):
    """개인 실명 경고 줄(순수) — 막지 않는다. 카드 산문(델타 한·영 슬롯, 심층 칸·국문 병기)에 그 카드 원문의 개인 이름 낱말이 나오면 한 줄."""
    rows, scaffold_cards = rows or {}, scaffold_cards or {}
    out = []

    def check(cid, slots, tag):
        row, sc = rows.get(cid), scaffold_cards.get(cid)
        if not (row or sc):
            return
        names = source_person_names(_name_source(row, sc), _firm_text(row, sc))
        for slot, text in slots:
            hit = sorted(person_name_hits(names, text))
            if hit:
                out.append(f"개인 실명 의심 {cid}.{tag}{slot}: {', '.join(hit)} — 원문의 개인 이름(483 수신인·호칭 뒤 이름)이다. "
                           f"공직자·규제기관 공식 인물이 아니면 '시설 책임자'·'업체 대표'·'해당 임원'으로 쓴다")

    cards = delta.get("cards") if isinstance(delta, dict) and isinstance(delta.get("cards"), dict) else {}
    for cid, card in cards.items():
        if isinstance(card, dict):
            check(cid, [(s, t) for s, t, _ in guess_check.delta_slots(card)], "")
    items = deep.get("items") if isinstance(deep, dict) and isinstance(deep.get("items"), dict) else deep
    for cid, entry in (items or {}).items() if isinstance(items, dict) else ():
        check(cid, [(s, t) for s, t, _, _ in guess_check.deep_slots(entry)], "deep.")
    return out


def source_lines(delta, deep, handoff, scaffold):
    """교열 코드 검사 — 4가지 원문 대조(개인 실명·가림 값 추측·진술 단정·날짜)를 **기자 사전 점검과 같은 함수**로 합친 델타·심층에 돌린다(순수).
    (차단 줄, 경고 줄). 차단 줄은 교열이 `reject`(`fails` item `코드 검사`)로, 경고 줄은 2-2 표로 판정하고 근거를 남긴다."""
    rows, sc = guess_check.handoff_rows(handoff), guess_check.scaffold_index(scaffold)
    block, warn = guess_check.delta_source_lines(delta, rows, sc)
    if isinstance(deep, dict):
        b, w = guess_check.deep_source_lines(deep, rows, sc)
        block, warn = block + b, warn + w
    return block, warn


def source_main(args):
    folder = os.path.abspath(args.run)
    delta = _load(os.path.join(folder, "delta.json"))
    deep_path = os.path.join(folder, "deep_delta.json")
    deep = _load(deep_path) if os.path.isfile(deep_path) else None
    handoff = _load(args.handoff)
    if not (isinstance(delta, dict) and isinstance(handoff, dict)):
        raise ValueError("delta.json·handoff 가 객체가 아니다")
    sc_path = find_scaffold(os.path.join(folder, "delta.json"), args.scaffold)
    scaffold = _load(sc_path) if sc_path else None
    block, warn = source_lines(delta, deep, handoff, scaffold)
    for line in block:
        print(line)
    for line in warn:
        print(f"경고: {line}")
    if not sc_path:
        print("경고: 스캐폴드 사본이 없어 코드 facts(실사일·발행일·업체 칸) 대조가 빠졌다 — 1번의 _scaffold/brief_web_<날짜>.json 을 받아 다시 돌린다")
    print(f"원문 대조: 차단 {len(block)}건" + ("" if block else " — 통과") + (f" · 경고 {len(warn)}건(판정·근거 필요)" if warn else ""))
    return 1 if block else 0


def deep_lines(shard, ids_483):
    """심층 샤드의 국문 병기 자리·결손 줄(순수)."""
    out = []
    items = shard.get("items") if isinstance(shard.get("items"), dict) else {}
    for cid, entry in items.items():
        if not isinstance(entry, dict):
            out.append(f"항목이 객체가 아니다: {cid}")
            continue
        da = entry.get("deep_analysis") if isinstance(entry.get("deep_analysis"), dict) else {}
        for key in NESTED_TRANSLATION_KEYS:
            if key in da:
                out.append(f"자리 틀림 {cid}: {key} 가 deep_analysis 안에 있다 — items.{cid}.{key} 로 옮긴다")
        if cid not in ids_483:
            continue
        obs = entry.get("observations_ko")
        if not isinstance(obs, list) or not obs:
            if "observations_ko" not in da:
                out.append(f"483 관찰 국문 없음 {cid}: observations_ko(★필수)")
            continue
        for i, o in enumerate(obs):
            if not (isinstance(o, dict) and str(o.get("number") or "").strip()
                    and str(o.get("deficiency_ko") or "").strip()):
                out.append(f"483 관찰 국문 불완전 {cid}: observations_ko[{i}] 에 number·deficiency_ko 가 있어야 한다")
    return out


def rejected_ids(copyedit):
    """1차 교열 기록 → 반려 카드 id 집합. `rework.cards` 와 `cards[].verdict` 를 합친다."""
    ids = set()
    rework = copyedit.get("rework") if isinstance(copyedit.get("rework"), dict) else {}
    ids.update(str(x) for x in rework.get("cards") or [] if isinstance(x, str))
    for card in copyedit.get("cards") or []:
        if isinstance(card, dict) and card.get("verdict") != "pass" and card.get("web_card_id"):
            ids.add(str(card["web_card_id"]))
    return ids


def rework_diff_deep(old_deep, new_deep, rejected):
    """심층(`deep_delta.json` = {카드 id: 항목}) 재작업 대조 — 델타와 **같은 함수**(`rework_diff`)를 카드 층에 씌워 부른다.
    반려 카드가 심층에 없을 수 있으므로(델타 칸만 반려) '반려 카드가 1차에 없다' 줄은 빼고, 그 밖의 문제는 그대로 막는다."""
    problems, notes = rework_diff({"cards": old_deep or {}}, {"cards": new_deep or {}}, rejected)
    problems = [p for p in problems if not p.startswith("반려 카드가 1차 델타에 없다")
                and not p.startswith("1차 교열 기록에 반려 카드가 없다")]
    return ([f"심층: {p}" for p in problems],
            [f"심층: {n}" for n in notes if not n.startswith("안 바뀜")])


def rework_diff(old, new, rejected):
    """(문제 줄, 알림 줄) — 문제 줄이 하나라도 있으면 재작업이 범위를 넘은 것이다(순수)."""
    problems, notes = [], []
    a = old.get("cards") if isinstance(old.get("cards"), dict) else {}
    b = new.get("cards") if isinstance(new.get("cards"), dict) else {}
    if not rejected:
        problems.append("1차 교열 기록에 반려 카드가 없다")
    for cid in sorted(set(a) - set(b)):
        problems.append(f"카드가 사라졌다: {cid}")
    for cid in sorted(set(b) - set(a)):
        problems.append(f"카드가 새로 생겼다: {cid}")
    for cid in sorted(rejected - set(a)):
        problems.append(f"반려 카드가 1차 델타에 없다: {cid}")
    for cid in sorted(set(a) & set(b)):
        if a[cid] == b[cid]:
            if cid in rejected:
                notes.append(f"안 바뀜(반려 카드) {cid}")
            continue
        ca = a[cid] if isinstance(a[cid], dict) else {}
        cb = b[cid] if isinstance(b[cid], dict) else {}
        slots = sorted(k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k))
        if cid in rejected:
            notes.append(f"고침 {cid}: {', '.join(slots)}")
        else:
            problems.append(f"반려되지 않은 카드가 바뀌었다: {cid} ({', '.join(slots)})")
    for key in sorted((set(old) | set(new)) - {"cards"}):
        if old.get(key) != new.get(key):
            problems.append(f"카드 밖의 값이 바뀌었다: {key}")
    return problems, notes


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="편집팀 사전 점검")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("terms")
    t.add_argument("delta")
    t.add_argument("--handoff", help="카드 유형을 읽을 handoff 파일(없으면 델타 폴더 날짜로 찾는다)")
    t.add_argument("--deep", help="심층 델타·샤드 — 심층 칸 산문을 경고로 본다")
    t.add_argument("--scaffold", help="그 주 스캐폴드 사본(없으면 _scaffold/brief_web_<날짜>.json 을 찾는다)")
    d = sub.add_parser("deep-source")
    d.add_argument("handoff")
    d.add_argument("deep")
    d.add_argument("--out", required=True)
    p = sub.add_parser("deep")
    p.add_argument("handoff")
    p.add_argument("shard")
    p.add_argument("--scaffold", help="그 주 스캐폴드 사본(없으면 _scaffold/brief_web_<날짜>.json 을 찾는다)")
    s = sub.add_parser("source")
    s.add_argument("run")
    s.add_argument("handoff")
    s.add_argument("--scaffold", help="그 주 스캐폴드 사본(없으면 _scaffold/brief_web_<날짜>.json 을 찾는다)")
    r = sub.add_parser("rework-diff")
    r.add_argument("old")
    r.add_argument("new")
    r.add_argument("copyedit")
    r.add_argument("--deep-old", help="1차 deep_delta.json — 주면 심층도 같은 대조")
    r.add_argument("--deep-new", help="2차 deep_delta.json")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "terms":
            delta = _load(args.delta)
            if not isinstance(delta, dict):
                raise ValueError("델타가 객체가 아니다")
            if args.handoff:
                handoff = _load(args.handoff)
            else:
                handoff = _load_handoff_quiet(os.path.dirname(os.path.abspath(args.delta)))
            card_types = merge_parts.handoff_card_types(handoff) if isinstance(handoff, dict) else {}
            sc_path = find_scaffold(args.delta, args.scaffold)
            scaffold = _load(sc_path) if sc_path else None
            hook_block, hook_warn = hook_lines(delta, scaffold)
            guess_block, guess_warn = guess_check.delta_guess_lines(
                delta, guess_check.handoff_rows(handoff), guess_check.scaffold_index(scaffold))
            src_block, src_warn = guess_check.delta_source_lines(
                delta, guess_check.handoff_rows(handoff), guess_check.scaffold_index(scaffold))
            lines = term_lines(delta, card_types) + gate_lines(delta, card_types, scaffold) + hook_block + guess_block + src_block
            lines += [x for x in assembly_lines(delta, scaffold) if x not in lines]
            for line in lines:
                print(line)
            warns = warn_lines(delta, _load(args.deep) if args.deep else None) + hook_warn
            warns += accuracy_warn_lines(delta, scaffold) + guess_warn + src_warn
            warns += private_name_warn_lines(delta, None, guess_check.handoff_rows(handoff), guess_check.scaffold_index(scaffold))
            warns += group_candidate_lines(delta, scaffold)
            if not sc_path:
                warns.append("스캐폴드 사본(_scaffold/brief_web_<날짜>.json)이 없어 인용 번역 대조는 건너뛰었다 — 교열 코드 검사에서 본다")
            for line in warns:
                print(f"경고: {line}")
            print(f"사전 점검: {len(lines)}건" + ("" if lines else " — 통과")
                  + (f" · 경고 {len(warns)}건(막지 않음)" if warns else ""))
            return 1 if lines else 0
        if args.cmd == "deep":
            handoff, shard = _load(args.handoff), _load(args.shard)
            if not isinstance(handoff, dict) or not isinstance(shard, dict):
                raise ValueError("handoff·심층 샤드가 객체가 아니다")
            lines = (deep_lines(shard, fda483_ids(handoff)) + deep_term_lines(shard)
                     + deep_absence_lines(shard) + deep_basis_lines(shard) + deep_gate_lines(shard, handoff))
            sc_path = find_scaffold(args.shard, args.scaffold)
            scaffold = _load(sc_path) if sc_path else None
            lines += deep_assembly_lines(shard, handoff, scaffold)
            guess_block, guess_warn = guess_check.deep_guess_lines(
                shard, guess_check.handoff_rows(handoff), guess_check.scaffold_index(scaffold))
            src_block, src_warn = guess_check.deep_source_lines(
                shard, guess_check.handoff_rows(handoff), guess_check.scaffold_index(scaffold))
            lines += guess_block + src_block
            for line in lines:
                print(line)
            warns = inject_slots.lint_deep_prose_style(shard).warn_lines()
            warns += accuracy_warn_lines({"cards": {}}, None, shard, handoff) + guess_warn + src_warn
            warns += private_name_warn_lines(None, shard, guess_check.handoff_rows(handoff), guess_check.scaffold_index(scaffold))
            if not sc_path:
                warns.append("스캐폴드 사본(_scaffold/brief_web_<날짜>.json)이 없어 국문 병기 관문(483·WL·WHOPIR)은 건너뛰었다 — 교열 코드 검사에서 본다")
            for line in warns:
                print(f"경고: {line}")
            print(f"심층 사전 점검: {len(lines)}건" + ("" if lines else " — 통과")
                  + (f" · 경고 {len(warns)}건(막지 않음)" if warns else ""))
            return 1 if lines else 0
        if args.cmd == "source":
            return source_main(args)
        if args.cmd == "rework-diff":
            old, new, copyedit = _load(args.old), _load(args.new), _load(args.copyedit)
            if not all(isinstance(x, dict) for x in (old, new, copyedit)):
                raise ValueError("델타·교열 기록이 객체가 아니다")
            rejected = rejected_ids(copyedit)
            problems, notes = rework_diff(old, new, rejected)
            if args.deep_old or args.deep_new:
                if not (args.deep_old and args.deep_new):
                    raise ValueError("--deep-old 와 --deep-new 는 같이 준다")
                dp, dn = rework_diff_deep(_load(args.deep_old), _load(args.deep_new), rejected)
                problems, notes = problems + dp, notes + dn
            for line in problems + notes:
                print(line)
            print("재작업 대조: " + (f"범위를 넘음 {len(problems)}건" if problems else "통과 — 반려 카드만 바뀌었다"))
            return 1 if problems else 0
        handoff, deep = _load(args.handoff), _load(args.deep)
        if not isinstance(handoff, dict) or not isinstance(deep, dict):
            raise ValueError("handoff·deep 델타가 객체가 아니다")
        out, filled = with_source_text(deep, handoff_bodies(handoff))
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"검사용 사본: {args.out} — 원문 붙임 {filled}/{len(deep)}장")
        return 0
    except (OSError, ValueError) as e:
        print(f"사전 점검을 못 했다: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
