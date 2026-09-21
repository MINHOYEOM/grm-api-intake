#!/usr/bin/env python3
"""MFDS 품목기준코드(ITEM_SEQ) → 제품군 근거(ATC·주성분) 조회.

왜 있는가
---------
2026-09-21 제품군 분류기를 증거 기반으로 고친 뒤, 국내 회수·행정처분 카드 114장 중
70장(61%)이 배지를 잃었다. 원문이 제형만 말하기 때문이다 — `트리암시놀론주사`는
주사제라는 사실만 알려 주고, 주사제는 항체도 저분자도 쓴다.

MFDS 데이터에는 이미 `ITEM_SEQ`(품목기준코드)가 있다(회수 100%·행정처분 66%).
그 코드로 허가정보 API 를 한 번 더 부르면 **ATC 코드**가 온다. ATC 는 WHO 가 관리하는
국제 분류이고 **제형이 아니라 물질 성격**으로 묶이므로, 이번 수리가 경계한 축 혼동이
없다. 실측(발행 카드 실 품목코드 120건): ATC 보유 85건(70%), 전량 판정 도달.

★설계 원칙 — 수집 시점에 **근거만** 실어 보낸다.
  판정은 `grm_taxonomy.compute_modality` 가 한다. 여기서 제품군을 결론지어 넣으면
  판정 로직이 두 군데로 갈라진다(이번 사건의 교훈: 그룹핑과 배지가 갈려 있었다).

★조회 0건을 '의약외품' 으로 단정하지 않는다.
  허가정보 API 는 **의약품만** 담는다. 실측 실패 3건이 전부 치약·미백제(의약외품)였다.
  그렇다고 0건 = 의약외품은 아니다 — API 누락·신규 등록 지연도 0건을 낸다. 둘을 구분할
  수 없으면 판정하지 않는다(`MFDS_ITEM_CLASS_UNRESOLVED`). 근거 없이 판정하지 않는다는
  원칙은 여기서도 같다.
"""
from __future__ import annotations

import os
from typing import Any

from grm_common import http_get_json, log, mask_service_key

PERMIT_API = ("https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService08"
              "/getDrugPrdtPrmsnDtlInq08")

# raw_payload 에 싣는 키 — `grm_taxonomy.compute_modality` 가 읽는 계약.
KEY_ATC = "mfds_atc_code"
KEY_INGREDIENT = "mfds_main_ingredient"
KEY_LOOKUP = "mfds_item_lookup"          # ok | not_found | error | no_key | no_seq

LOOKUP_OK = "ok"
LOOKUP_NOT_FOUND = "not_found"           # ★'의약외품'이 아니라 '허가정보에 없음'
LOOKUP_ERROR = "error"
LOOKUP_NO_KEY = "no_key"
LOOKUP_NO_SEQ = "no_seq"
LOOKUP_BUDGET_EXHAUSTED = "budget_exhausted"


# ★프로세스당 조회 상한. 일일 수집은 수십 건이라 닿지 않지만, 과거분 딥백필
#   (collect_mfds_backfill.py 는 MAX_PAGES 를 올려 수천 건을 돈다)이 그대로 돌면
#   data.go.kr 개발계정 일일 한도(10,000)를 태운다. 플래그로 끄게 하지 않는 이유:
#   env 게이트 뒤는 가드가 못 보고 조용히 꺼진 채 굳는다. 상한은 **소리를 낸다**.
LOOKUP_BUDGET = int(os.environ.get("MFDS_ITEM_CLASS_BUDGET", "1500"))
_lookups_used = 0
_budget_warned = False


def _service_key() -> str:
    return (os.environ.get("DATA_GO_KR_SERVICE_KEY")
            or os.environ.get("DATA_GO_KR_KEY") or "").strip()


def lookups_used() -> int:
    """이 프로세스가 쓴 조회 수 — 수집기 요약에 싣기 위한 관측 창구."""
    return _lookups_used


def _first_item_seq(value: str) -> str:
    """`ITEM_SEQ` 는 쉼표 다중값일 수 있다(행정처분 다품목). 첫 코드만 쓴다.

    ★여러 품목이 한 건에 묶인 처분에서 제품군이 갈릴 수 있다. 그때는 대표 하나로
      단정하기보다 판정을 보류하는 편이 맞지만, 실측상 다중값은 대부분 같은 제조소의
      동일 계열이라 첫 코드를 쓰되 **다중이면 표식을 남긴다**(아래 multi 플래그).
    """
    return (value or "").split(",")[0].strip()


def fetch_item_class(item_seq_raw: str, *, service_key: str | None = None,
                     timeout: int = 20) -> dict[str, Any]:
    """품목기준코드 → {mfds_atc_code, mfds_main_ingredient, mfds_item_lookup}.

    실패는 조용히 삼키되 **사유를 남긴다** — 조회를 못 한 것과 값이 없는 것은 다르다.
    """
    seq = _first_item_seq(item_seq_raw)
    if not seq:
        return {KEY_LOOKUP: LOOKUP_NO_SEQ}
    key = service_key if service_key is not None else _service_key()
    if not key:
        return {KEY_LOOKUP: LOOKUP_NO_KEY}

    global _lookups_used, _budget_warned
    if _lookups_used >= LOOKUP_BUDGET:
        if not _budget_warned:
            _budget_warned = True
            log("WARN", f"MFDS 허가정보 조회 상한 {LOOKUP_BUDGET} 도달 — 이후 품목은 "
                        f"제품군 근거 없이 수집된다(배지 미표시). 딥백필이면 "
                        f"MFDS_ITEM_CLASS_BUDGET 를 올려 재실행할 것.")
        return {KEY_LOOKUP: LOOKUP_BUDGET_EXHAUSTED}
    _lookups_used += 1

    params = {"serviceKey": key, "type": "json", "numOfRows": 1,
              "pageNo": 1, "item_seq": seq}
    try:
        data = http_get_json(PERMIT_API, params=params, timeout=timeout, retries=1)
    except Exception as e:                                    # noqa: BLE001
        log("WARN", f"MFDS 허가정보 조회 실패 item_seq={seq}: "
                    f"{mask_service_key(str(e))}")
        return {KEY_LOOKUP: LOOKUP_ERROR}

    body = (data.get("body") or {}) if isinstance(data, dict) else {}
    items = body.get("items") or []
    if isinstance(items, dict):
        items = [items]
    if not items:
        # ★여기서 '의약외품' 이라고 단정하지 않는다 — 허가정보 API 는 의약품만 담지만
        #   API 누락·신규 지연도 같은 0건을 낸다. 구분할 수 없으면 판정하지 않는다.
        return {KEY_LOOKUP: LOOKUP_NOT_FOUND}

    row = items[0] or {}
    out: dict[str, Any] = {KEY_LOOKUP: LOOKUP_OK}
    atc = (row.get("ATC_CODE") or "").strip().upper()
    if atc:
        out[KEY_ATC] = atc
    ingr = (row.get("MAIN_ITEM_INGR") or row.get("INGR_NAME") or "").strip()
    if ingr:
        out[KEY_INGREDIENT] = ingr
    return out


def enrich_raw_payload(raw_payload: dict[str, Any], item_seq_raw: str, *,
                       service_key: str | None = None) -> dict[str, Any]:
    """수집기용 헬퍼 — raw_payload 에 제품군 **근거**만 얹는다(판정은 분류기 몫).

    반환은 같은 dict(제자리 갱신). 조회를 못 해도 raw_payload 는 그대로 쓸 수 있다.
    """
    info = fetch_item_class(item_seq_raw, service_key=service_key)
    raw_payload.update(info)
    if "," in (item_seq_raw or ""):
        raw_payload["mfds_item_seq_multi"] = True
    return raw_payload
