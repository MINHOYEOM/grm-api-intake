#!/usr/bin/env python3
"""OCR 엔진 부재로 **빈 본문 적재된** 스캔 483 소급 복구 — 1회성, Supabase 직행.

배경(2026-07-30 실측 · PR #487 진단): `collect_fda_483` 에 OCR 경로를 넣은 PR #456 이
tesseract 설치 스텝을 `grm-intake.yml` 에만 추가한 탓에, 같은 PDF 경로를 쓰는
`grm-findings-backfill-fetch.yml`(하루 3회 무인)이 엔진 없이 돌면서 스캔 483 을
`raw_json.fda483_text_status = "scan-ocr-unavailable:…"` 상태로 `raw_signals` 에 적재했다.
`raw_signals` 는 append-only + `document_id` dedup 이라 **한 번 빈손으로 들어간 문서는
영구히 빈손**이다 — 그 문서들은 observations 가 없어 findings 도 0건이다(공개 영향은
잘못된 내용 노출이 아니라 **누락**). PR #487 이 유입은 막았고, 이 스크립트가 이미 들어온
분을 되찾는다.

★왜 `grm-fda483-ocr-backfill.yml` 이 아닌가: 그 워크플로는 **발행본 브리프**(publish_date +
스캐폴드 아티팩트)를 기준으로 deep 델타 패치 **아티팩트**를 만들 뿐 `raw_signals` 를
갱신하지 않는다. 이번 대상은 브리프에 발행된 적 없는 백필 행이라 그 경로로는 닿지 않는다.

설계 — 부분 패치가 아니라 **행 전체 재구성**
  raw_json 만 손으로 기워 넣으면 `raw_sha256`(= sha256(canonical_json(raw_json)))·`row_json`·
  `body` 가 서로 어긋날 수 있다. 대신 라이브 수집과 **같은 함수**로 행을 다시 만든다:
      nrow(복원) → collect_fda_483._to_item(...) → findings_store.raw_signal_from_intake_item
  `raw_signal_id = "rawsig-" + stable_hash({schema_version, source, document_id})[:24]` 라
  **내용이 아니라 document_id 에만** 의존한다 → 재구성해도 id 가 같다. 그래서 DELETE 없이
  제자리 교체가 된다 — [R3 2026-11-12] raw_signal_apply_version(148) 경유(옛 판 보관·지문 검증).
  DELETE 를 쓰지 않는 이유: `findings.raw_signal_id` 가 ON DELETE CASCADE 라, 지웠다가 삽입에
  실패하면 그 사이 findings 까지 사라진다. 함수 교체는 그 창이 없다.

  nrow 복원의 근거: `_to_item` 이 읽는 nrow 키는 9개(record_type·media_id·company·fei·
  state·country·establishment_type·record_date·publish_date)뿐이고, 그 9개가 전부
  raw_payload 에 그대로 저장된다(publish_date 는 원문 MM/DD/YYYY 그대로). 즉 무손실이다 —
  `tests/test_483_ocr_recovery.py` 의 왕복 동일성 테스트가 이 전제를 고정한다.

불가침 안전장치
  · **내용을 얻지 못하면 아무것도 쓰지 않는다.** OCR 이 여전히 실패하면 기존 행을 그대로
    둔다(빈 본문을 다른 빈 본문으로 덮어쓰지 않는다).
  · 재구성된 `raw_signal_id` 가 기존과 다르면 그 행은 **건너뛴다** — 떠돌이 행을 만들지 않는다.
  · `_to_item` 이 None(수의/기기/식품 도메인 게이트 드롭)이면 건너뛴다.
  · `collected_at` 은 **원본 값을 보존**한다. 문서가 수집된 시점은 그때가 맞고, 여기서
    갱신하면 하류의 시간축 질의가 소급해 흔들린다. 복구의 흔적은 raw_json 자체다
    (`fda483_text_status` 가 사라지고 observations 가 생긴다).
  · 멱등: 대상 선정 기준이 "`fda483_text_status` 가 scan-ocr-unavailable" 이므로, 복구에
    성공한 행은 다음 실행의 후보에서 자연히 빠진다.

findings 재생성은 이 스크립트가 하지 않는다 — upsert 로 observations 가 채워지면 기존
`findings_supabase_backfill.py`(M12)가 "findings 없는 raw_signal" 로 보고 변환한다.
워크플로가 그 체인을 잇는다(grm-findings-backfill-fetch.yml 의 체인과 동일 배관).

exit code 정책(`backfill_483_inspectors.py` 와 동일 계약):
  - 자격증명 누락 / SUPABASE_URL 형식 오류 / raw_signals 조회 자체 실패 → 2.
  - 건별 실패는 계속 진행하며 카운트만 한다. 다만 "광범위"하면(tesseract 미설치·fda.gov
    전면 차단 등 인프라성 신호) exit 1 로 침묵하지 않는다 — `_is_broad_failure`.
  - dry-run 은 PDF fetch + 재구성까지 수행하고 **쓰기만** 건너뛴다. 무엇이 얼마나 복구될지
    미리 보기 위해서다. 그래서 dry-run 도 Supabase 자격증명이 필요하다(후보 선정이 RLS 뒤).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import findings_store
import findings_supabase_backfill as fsb
import raw_versioning as rv
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_483, log
from grm_taxonomy import is_out_of_scope_483_establishment


SCHEMA_VERSION = "grm-483-ocr-recovery/v2"

DEFAULT_TIMEOUT_SECONDS = fsb.DEFAULT_TIMEOUT_SECONDS
_DEFAULT_PAGE_SIZE = 1000
_DEFAULT_DELAY_SECONDS = 0.5
_MAX_SAMPLES = 20

# 광범위 실패 판정 임계값 — backfill_483_inspectors 와 동일 규약(절대 기준 아님).
_BROAD_FAILURE_MIN_ATTEMPTS = 3
_BROAD_FAILURE_RATIO = 0.5

# 복구 대상 표식. collect_fda_483.is_ocr_engine_unavailable 과 같은 접두사를 쓰되, 이
# 스크립트는 collect_fda_483 를 지연 import 하므로(아래 간접층 참조) 상수를 여기 둔다.
# 정합성은 tests 가 두 값이 같음을 고정한다.
OCR_UNAVAILABLE_PREFIX = "scan-ocr-unavailable"

# 선택 모드. 기본은 종전 그대로(엔진 부재로 본문을 못 받은 행) — 새 모드는 명시해야 켜진다.
MODE_OCR_UNAVAILABLE = "ocr-unavailable"
MODE_MISSING_OBSERVATIONS = "missing-observations"
MODE_COLLECTION_CAPPED = "collection-capped"
# [2026-10-27 FDA 결손 감사] 예약 실행·발행 전 게이트가 쓰는 모드. `collection-capped` 의
# 상위집합으로, **다시 읽으면 본문이 나올 수 있는** 결손 표식을 전부 고른다:
#   · not-attempted / scan-ocr-budget  — 그날 실행의 상한(collection-capped 와 동일)
#   · scan-ocr-unavailable:…          — 그 실행의 러너에 엔진이 없었다(이 워크플로는 엔진 필수)
#   · fetch-fail:…                    — 일시 장애(5xx·타임아웃)·재게시(404, 아래 is_recoverable_row)
# 감사 실측(2026-10-27 raw_signals): 엔진 부재 8건(07-17~07-28)·fetch-fail 2건이 어떤 예약
# 잡의 후보에도 걸리지 않아 석 달째 본문 없이 남아 있었다. 확정 사유(404·텍스트층 없음·
# OCR 이 글자 0)는 대상이 아니고, 다시 읽어 확정 사유가 나오면 표식을 그 사유로 바꿔
# 무한 재시도를 끊는다(collection-capped 와 같은 규칙).
MODE_RECOVERABLE = "recoverable"
# [2026-11-12] 이름으로 지목한 문서만 다시 읽어 재구성한다(상태값 무관 · --doc-ids 필수).
# 관찰이 이미 있는 문서의 누락(섞인 PDF — 195468: 텍스트층 1쪽 + 이미지 3쪽 → 관찰 1/4)을 고치는 용도.
# 다시 읽어 관찰이 **늘 때만** 교체한다(축소 금지) · 확정 사유 표식은 적지 않는다.
MODE_NAMED = "named"
RECOVERY_MODES = (MODE_OCR_UNAVAILABLE, MODE_MISSING_OBSERVATIONS, MODE_COLLECTION_CAPPED,
                  MODE_RECOVERABLE, MODE_NAMED)
# [2026-10-27 #1209] 수집 **상한** 때문에 본문 없이 적재된 행의 상태값. 문서 사정이 아니라
# 그날 실행의 사정(PDF 건수 상한·OCR 페이지 예산)이라 다시 읽으면 대개 본문이 나온다.
# collect_fda_483 가 기록하는 문자열과 같아야 한다 — tests 가 두 값이 같음을 고정한다.
CAPPED_TEXT_STATUSES = ("not-attempted", "scan-ocr-budget")
# 다시 읽어 보니 범위 밖(기기·식품 등)이었던 행의 확정 사유. 회수 후보 어느 모드에도 걸리지
# 않는 값이어야 한다(그래야 재시도가 끊긴다) — tests 가 고정한다.
OUT_OF_SCOPE_STATUS = "out-of-scope"
_OBSERVATIONS_KEY = "fda_483_observations"
_TEXT_LOSS_NGRAM = 8
_TEXT_LOSS_MIN_RUN_WORDS = 3
NAMED_TEXT_LOSS_MIN_CHARS = 200
NAMED_HOLDER_CUT_MIN_CHARS = 40
_HOLDER_CUT_MIN_FUNCTION_WORDS = 3
_HOLDER_CUT_FUNCTION_WORDS = frozenset(
    "a an the is was were are be been has have had not no for in on with that which as by or "
    "your you it its from at do does did there their any".split()
)
_FORM_WORDS = frozenset(
    "employee employees signature date dates issued see reverse of this page pages form fda 483 "
    "previous edition obsolete inspectional observations observation department health and human "
    "services food drug administration district office address phone number inspection fei name "
    "title individual to whom report firm street city state zip code country type establishment "
    "inspected www gov oc industry information".split()
)
_TEXT_LOSS_META_FIELDS = (
    "firm", "establishment_type", "site_state", "country", "fei_number", "product_type",
)

Sleeper = Callable[[float], None]


@dataclass
class OcrRecoveryReport:
    schema_version: str = SCHEMA_VERSION
    mode: str = ""                      # "dry_run" | "apply"
    select_mode: str = MODE_OCR_UNAVAILABLE   # 어떤 후보 집합을 훑었는가(리포트 정직성)
    limit: int | None = None
    delay_seconds: float = _DEFAULT_DELAY_SECONDS
    scanned: int = 0                    # source='FDA 483' raw_signals 전체(fetch 시점)
    marked: int = 0                     # 그 중 fda483_text_status 가 엔진부재인 행
    candidates: int = 0                 # 이번 실행에서 선택된 문서 수(doc-ids/limit 반영후)
    attempted: int = 0                  # 실제 PDF fetch 를 시도한 문서 수
    recovered: int = 0                  # 본문 확보(dry-run=교체 예정, apply=upsert 성공)
    recovered_excerpt_only: int = 0     # 그 중 관찰문 0건 — 발췌만 살고 findings 는 0
    named_no_gain: int = 0              # named: 관찰 증가 없음·표제 소실·본문 손실로 교체하지 않음
    named_text_loss: list[dict[str, Any]] = field(default_factory=list)  # [11-13] 본문 손실 차단 내역
    still_empty: int = 0                # ↓ 두 사건의 합(하위호환 유지). 진단에는 쓰지 말 것.
    # ★ still_empty 를 쪼갠 이유(재발 방지):
    #   v1 은 "본문을 못 받았다"와 "본문은 받았는데 추출이 0건"을 한 칸에 더했다. 두 사건의
    #   처방은 정반대다 — 전자는 수집/OCR 문제, 후자는 파서 문제. 합산된 숫자만 보고 세 번
    #   연속 OCR 을 범인으로 지목했고(엔진 배선·DPI 300→400), 그동안 진짜 원인인 파서는
    #   손대지 못했다. 계기판이 두 사건을 구분하지 못하면 진단은 반드시 틀린다.
    no_text: int = 0                    # PDF 본문 확보 실패(fetch-fail·OCR 불가·빈 텍스트)
    text_without_extraction: int = 0    # ★본문은 있는데 발췌·관찰문 모두 0 — **파서 문제**
    gate_dropped: int = 0               # _to_item None(도메인 게이트) — 손대지 않음
    id_mismatch: int = 0                # 재구성 id != 기존 id — 건너뜀(0 이어야 정상)
    failed: int = 0                     # fetch/재구성/upsert 실패
    observations_recovered: int = 0     # 되찾은 관찰 항목 총수(findings 예상 규모)
    # collection-capped 모드 전용: 다시 읽어도 본문이 없어서(404·텍스트층 없음 등) 상한 표식을
    # **실제 사유로 교체**한 행. 교체하지 않으면 그 문서는 매 실행 후보로 남아 영원히 다시 읽힌다.
    status_resolved: int = 0
    # 상한 표식 모드에서 시설 유형만으로 범위 밖이라 PDF 를 받지 않은 행(비용 0 — 재시도 낭비 차단).
    skipped_out_of_scope_type: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def _default_sleep(seconds: float) -> None:
    if seconds:
        time.sleep(seconds)


def _bump(report: OcrRecoveryReport, reason: str) -> None:
    report.failure_reasons[reason] = report.failure_reasons.get(reason, 0) + 1


def _count_recovered(report: OcrRecoveryReport, observations: list[Any]) -> None:
    """복구 1건을 센다. **dry-run 과 apply 가 반드시 같은 함수를 쓴다.**

    ★두 경로에 카운팅을 복제해 두면 한쪽만 고치게 된다 — 실제로 그렇게 했다가
    `recovered_excerpt_only` 가 dry-run 에서 **항상 0** 이 됐고, 하필 dry-run 이 진단에
    쓰는 모드라 새 계기판이 처음부터 거짓말을 했다. 계기판을 고치러 와서 같은 결함을
    다시 만든 셈이라, 아예 한 곳으로 합쳐 재발 경로를 없앤다.
    """
    report.recovered += 1
    report.observations_recovered += len(observations)
    if not observations:
        # 발췌는 살렸지만 지적사항은 못 뽑았다. 이 문서는 findings 를 한 건도 만들지
        # 못하므로 "복구"라고 부르면 과장이다 — 따로 세어 파서 결함을 드러낸다.
        report.recovered_excerpt_only += 1


def _json_object(value: Any) -> dict[str, Any]:
    """raw_json(TEXT 컬럼) → dict. PostgREST 가 이미 dict 로 준 경우도 방어."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_doc_ids(value: str) -> list[str]:
    if not value:
        return []
    return [p for p in re.split(r"[,\s]+", value.strip()) if p]


def is_missing_observations_row(raw: dict[str, Any]) -> bool:
    """이 raw_payload 가 "본문은 받았는데 관찰이 하나도 안 만들어진" 행인가(순수 함수).

    ★이 클래스는 지금까지 **어떤 복구 잡의 선택 조건에도 걸리지 않았다.** `_to_item` 은
    `fda483_text_status` 를 본문이 전무할 때만 기록하므로(excerpt 가 있으면 기록하지 않는다),
    excerpt 는 있는데 관찰이 0인 행은 상태 키가 없어 `is_ocr_unavailable_row` 의 시야 밖이다.
    라이브 실측(2026-08-01): FDA 483 문서 2,000건 중 444건이 findings 0건이고, 그중 417건이
    상태 키 없음 · 192건은 excerpt 를 이미 갖고 있었다. 자동 복구가 영원히 도달하지 못하는
    사각지대였다.

    판정은 **관찰 키 부재** 하나로 한다 — `collect_fda_483._to_item` 이 관찰이 비면 키 자체를
    쓰지 않기 때문에(`if observations:`), 키 부재가 곧 "관찰 0건"이다.
    """
    return _OBSERVATIONS_KEY not in raw


def is_collection_capped_row(raw: dict[str, Any]) -> bool:
    """이 raw_payload 가 "수집 상한 때문에 본문 없이 적재된" 행인가(순수 함수).

    일일 수집은 실행 1회당 PDF 건수(`FDA483_PDF_MAX_ITEMS`)와 OCR 페이지 예산
    (`FDA483_OCR_PAGE_BUDGET`)으로 벽시계를 닫는다. 상한 밖 문서는 `not-attempted` /
    `scan-ocr-budget` 으로 적재되고, 이후 수집은 이미 저장된 document_id 를 건너뛰므로
    **다시는 읽히지 않았다**(2026-10-26 FDA 대량 공개 때 51건 · 이슈 #1209).
    """
    return str(raw.get("fda483_text_status") or "") in CAPPED_TEXT_STATUSES


def is_recoverable_row(raw: dict[str, Any]) -> bool:
    """다시 읽으면 본문이 나올 수 있는 결손 표식인가(순수 함수 — `MODE_RECOVERABLE`).

    상한 표식 · 엔진 부재 · fetch 실패(404 포함).

    ★[2026-10-28] 404 도 다시 본다. 404 를 확정 사유로 보고 후보에서 뺐는데, FDA 가 같은
    media id 로 PDF 를 다시 올린 사례가 실측됐다(fda483-164956 CareFirst — 8월 404, 10월 200·
    관찰 1건). 404 재확인은 HTTP 요청 한 번이라(OCR 없음) 매일 돌아도 비용이 거의 없고, 여전히
    404 면 표식을 다시 쓰지 않는다(`_same_definitive_reason`).
    """
    status = str(raw.get("fda483_text_status") or "")
    if status in CAPPED_TEXT_STATUSES or status.startswith(OCR_UNAVAILABLE_PREFIX):
        return True
    return status.startswith("fetch-fail")


def is_recovery_candidate(raw: dict[str, Any], mode: str, *, explicit: bool = False) -> bool:
    """모드별 후보 판정 — `run()` 이 서버 like 로 좁힌 행을 여기서 정확히 재확인한다.

    `explicit`(= `--doc-ids` 로 문서를 **이름으로** 지목)이면 상한 표식 모드에서 `out-of-scope`
    행도 후보로 받는다. 그 표식은 게이트 판정의 산물이라 게이트가 고쳐지면 틀린 표식이 된다
    (2026-10-28: 양식 상용구 오판으로 의약품 483 이 out-of-scope 로 찍힘). 이름 지목일 때만
    여는 이유 — 예약 실행(doc-ids 없음)이 진짜 범위 밖 문서를 매일 다시 받지 않게.
    """
    if mode == MODE_NAMED:
        return explicit
    if (explicit and mode in (MODE_COLLECTION_CAPPED, MODE_RECOVERABLE)
            and str(raw.get("fda483_text_status") or "") == OUT_OF_SCOPE_STATUS):
        return True
    if mode == MODE_RECOVERABLE:
        return is_recoverable_row(raw)
    if mode == MODE_MISSING_OBSERVATIONS:
        return is_missing_observations_row(raw)
    if mode == MODE_COLLECTION_CAPPED:
        return is_collection_capped_row(raw)
    return is_ocr_unavailable_row(raw)


def is_ocr_unavailable_row(raw: dict[str, Any]) -> bool:
    """이 raw_payload 가 "엔진이 없어 본문을 못 받은" 행인가(순수 함수).

    `scan-no-text`(원문에 텍스트층 없음·OCR 비활성) · `scan-ocr-empty`(OCR 했으나 글자 0) ·
    `scan-ocr-budget`(예산 소진)은 **대상이 아니다** — 엔진을 붙여도 결과가 같거나(전자),
    별개 사유(후자)라 이 복구의 범위를 넘는다. 되찾을 수 있는 것만 손댄다.
    """
    status = str(raw.get("fda483_text_status") or "")
    return status.startswith(OCR_UNAVAILABLE_PREFIX)


def nrow_from_raw_payload(raw: dict[str, Any]) -> dict[str, str]:
    """저장된 raw_payload → `_to_item` 이 먹는 정규화 행(nrow) 복원.

    `_to_item` 이 읽는 키는 아래 9개뿐이고 전부 raw_payload 에 무손실로 남아 있다.
    (publish_date 는 `_to_item` 이 nrow 값을 **원문 MM/DD/YYYY 그대로** 실어 두므로 그대로
    되돌려주면 된다 — 여기서 ISO 로 바꾸면 안 된다.)
    이 매핑이 낡으면 왕복 동일성 테스트가 즉시 깨진다.
    """
    def _s(key: str) -> str:
        return str(raw.get(key) or "")

    return {
        "record_type": _s("record_type"),
        "media_id": _s("media_id"),
        "company": _s("firm"),
        "fei": _s("fei_number"),
        "state": _s("site_state"),
        "country": _s("country"),
        "establishment_type": _s("establishment_type"),
        "record_date": _s("record_date"),
        "publish_date": _s("publish_date"),
    }


# ---------------------------------------------------------------------------
# 지연 import 간접층 — collect_fda_483 는 import 시 환경 플래그를 읽으므로, 테스트가
# 이 모듈 속성만 패치해 net-free 로 돌 수 있게 얇은 간접층을 둔다
# (backfill_483_inspectors._fetch_text 와 동일 관례).
# ---------------------------------------------------------------------------


def _fetch_text(pdf_url: str) -> tuple[str, str]:
    from collect_fda_483 import _fetch_fda483_pdf_text
    return _fetch_fda483_pdf_text(pdf_url)


_MEDIA_ID_RE = re.compile(r"(\d{4,})$")


def dump_texts(doc_ids: list[str], out_dir: str, *,
               fetch_text: Callable[[str], tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    """[11-12 진단 전용·쓰기 없음] 지목한 483 의 판독 전문(OCR 포함)을 `<out_dir>/<doc>.txt` 로 남긴다.

    파서를 고치려면 원문이 필요한데, 스캔본은 OCR 엔진이 있는 러너에서만 글자가 나온다(회사PC 엔진 없음,
    티로 PC 는 fda.gov 무응답 — 195467). DB 를 읽지도 쓰지도 않는다: document_id 끝 숫자로 PDF URL 을 만든다."""
    text_fn = fetch_text or _fetch_text
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for doc in doc_ids:
        m = _MEDIA_ID_RE.search(doc)
        if not m:
            rows.append({"doc": doc, "status": "bad_doc_id", "chars": 0})
            continue
        url = f"https://www.fda.gov/media/{m.group(1)}/download"
        try:
            text, status = text_fn(url)
        except Exception as exc:  # noqa: BLE001 — 진단 도구: 건별 실패를 적고 계속
            text, status = "", f"fetch_raised:{type(exc).__name__}"
        name = f"fda483-{m.group(1)}"
        if text:
            Path(out_dir, f"{name}.txt").write_text(text, encoding="utf-8")
        rows.append({"doc": name, "url": url, "status": status, "chars": len(text or "")})
    return rows


def _def_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())[:80]


def _keeps_old_deficiencies(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> bool:
    """기존 관찰의 표제(앞 80 영숫자)가 새 관찰 표제에 모두 남아 있는가(순수 함수).
    옛 표제가 짧게 잘린 경우("…" 절단)는 새 표제의 앞부분이면 같은 것으로 본다."""
    new_keys = [_def_key(str(o.get("deficiency") or "")) for o in new]
    for o in old:
        k = _def_key(str(o.get("deficiency") or ""))
        if k and not any(n == k or (min(len(n), len(k)) >= 20 and (n.startswith(k) or k.startswith(n))) for n in new_keys):
            return False
    return True


# [11-13] named 교체 본문 손실 가드. 85637 교체에서 표제는 그대로·관찰 수는 늘었는데 옛 판의
# '머리글 + 역가 미시험 로트 6줄'이 새 판 어디에도 없었다(새 파서가 머리글을 푸터로 보고 뒤를 버림).
# · 8-그램 **집합** — 중복 정리는 허용한다(같은 문장이 새 판 어딘가에 있으면 손실 아님). 짧은 그램일수록
#   85637 로트 줄이 관찰 6 의 "…was not tested for sterility" 줄과 겹쳐 덜 보인다(4-그램 295 < 8-그램 410).
# · **문서 전체** 기준 — 85401 관찰 8 상세 730→141자는 관찰 9 로 이동 + 머리글 제거(정상 분리).
# · 숫자·양식 상용어·문서 메타(업체명·주소 성격 낱말)는 세지 않는다 — 페이지 머리글·푸터 정리 허용.
# · 임계 200자 실측(raw_signal_versions 연속 판 중 관찰이 바뀐 30쌍): 실손실 85637 410 · 90268 259 ·
#   91878 410 · 88106 662 · 151431 2,591 / 정상 감소 85401 59 · 88096 41 · 74389 75 · 94989 92 ·
#   82333 130 · 나머지 0. 시험 고정값(최소 조각)은 85637 367 · 85401 59.
# · [11-13 끊김형] holder 끝을 옛 host 내부에 정렬해 94989 92자·82333 130자처럼
#   200자 미만인 문장 중간 손실도 막는다. 74389 서명란은 기능어 0개라 통과한다.
def text_loss(old_raw: dict, new_raw: dict) -> dict:
    """옛 관찰 본문 중 새 판 어디에도 남지 않은 실질 텍스트를 센다(순수 함수)."""
    def tokens(raw: dict) -> tuple[list[str], list[int], dict[int, int]]:
        out: list[str] = []
        owners: list[int] = []
        observation_ends: dict[int, int] = {}
        for observation_index, observation in enumerate(raw.get(_OBSERVATIONS_KEY) or []):
            if not isinstance(observation, dict):
                continue
            for key in ("deficiency", "detail"):
                field_tokens = re.findall(r"[a-z0-9]+", str(observation.get(key) or "").lower())
                out.extend(field_tokens)
                owners.extend([observation_index] * len(field_tokens))
            observation_ends[observation_index] = len(out)
        return out, owners, observation_ends

    old_tokens, old_owners, old_observation_ends = tokens(old_raw)
    new_tokens, _, _ = tokens(new_raw)
    n = _TEXT_LOSS_NGRAM
    new_ngrams = {
        tuple(new_tokens[i:i + n])
        for i in range(len(new_tokens) - n + 1)
    }
    present = [
        tuple(old_tokens[i:i + n]) in new_ngrams
        for i in range(len(old_tokens) - n + 1)
    ]
    covered = [
        any(present[i] for i in range(max(0, j - n + 1), min(j, len(present) - 1) + 1))
        for j in range(len(old_tokens))
    ]

    ignored = set(_FORM_WORDS)
    for key in _TEXT_LOSS_META_FIELDS:
        ignored.update(re.findall(r"[a-z0-9]+", str(old_raw.get(key) or "").lower()))

    lost_chars = 0
    runs = 0
    sample = ""
    largest_run_chars = -1
    start = 0
    while start < len(old_tokens):
        if covered[start]:
            start += 1
            continue
        end = start + 1
        while end < len(old_tokens) and not covered[end]:
            end += 1
        run = old_tokens[start:end]
        if len(run) >= _TEXT_LOSS_MIN_RUN_WORDS:
            run_chars = sum(0 if word.isdigit() or word in ignored else len(word) + 1
                            for word in run)
            lost_chars += run_chars
            runs += 1
            if run_chars > largest_run_chars:
                largest_run_chars = run_chars
                sample = " ".join(run[:30])
        start = end
    holder_cut = False
    holder_cut_chars = 0
    holder_cut_sample = ""
    holder_cut_number = ""
    for observation in new_raw.get(_OBSERVATIONS_KEY) or []:
        if not isinstance(observation, dict):
            continue
        detail = str(observation.get("detail") or "").rstrip()
        if not detail or not detail[-1].isalnum():
            continue
        detail_tokens = re.findall(r"[a-z0-9]+", detail.lower())
        if len(detail_tokens) < 4:
            continue
        context_size = min(_TEXT_LOSS_NGRAM, len(detail_tokens))
        context = detail_tokens[-context_size:]
        last = context[-1]
        for position in range(context_size - 1, len(old_tokens)):
            if (old_tokens[position - context_size + 1:position] != context[:-1]
                    or not old_tokens[position].startswith(last)):
                continue
            cut_start = position if old_tokens[position] != last else position + 1
            observation_end = old_observation_ends[old_owners[position]]
            if cut_start >= observation_end or covered[cut_start]:
                continue

            candidate_chars = 0
            candidate_function_words = 0
            candidate_words: list[str] = []
            run_start = cut_start
            while run_start < observation_end:
                if covered[run_start]:
                    run_start += 1
                    continue
                run_end = run_start + 1
                while run_end < observation_end and not covered[run_end]:
                    run_end += 1
                candidate_run = old_tokens[run_start:run_end]
                if len(candidate_run) >= _TEXT_LOSS_MIN_RUN_WORDS:
                    candidate_chars += sum(
                        0 if word.isdigit() or word in ignored else len(word) + 1
                        for word in candidate_run
                    )
                    candidate_function_words += sum(
                        word not in ignored and word in _HOLDER_CUT_FUNCTION_WORDS
                        for word in candidate_run
                    )
                    candidate_words.extend(candidate_run)
                run_start = run_end

            if (candidate_chars >= NAMED_HOLDER_CUT_MIN_CHARS
                    and candidate_function_words >= _HOLDER_CUT_MIN_FUNCTION_WORDS
                    and (not holder_cut or candidate_chars > holder_cut_chars)):
                holder_cut = True
                holder_cut_chars = candidate_chars
                # 표본 = 끊긴 뒤 사라진 구간(3단어 이상 run)의 낱말들(앞 30개).
                holder_cut_sample = " ".join(candidate_words[:30])
                holder_cut_number = str(observation.get("number") or "")

    return {
        "lost_chars": lost_chars,
        "runs": runs,
        "sample": sample,
        "holder_cut": holder_cut,
        "holder_cut_chars": holder_cut_chars,
        "holder_cut_sample": holder_cut_sample,
        "holder_cut_number": holder_cut_number,
    }


def text_loss_reason(loss: dict) -> str:
    """'' | 'lost_chars' | 'holder_cut' | 'lost_chars+holder_cut'"""
    reasons = []
    if loss.get("lost_chars", 0) >= NAMED_TEXT_LOSS_MIN_CHARS:
        reasons.append("lost_chars")
    if loss.get("holder_cut"):
        reasons.append("holder_cut")
    return "+".join(reasons)


_OBS_HEAD_RE = re.compile(r"(?i)\bOBSERVATION\s*#?\s*(\d{1,3})\b")


def scan_mixed_pdfs(rows: list[dict[str, Any]], *, delay_seconds: float = 0.3,
                    fetch_bytes: Callable[[str], bytes] | None = None,
                    sleep_fn: Callable[[float], None] | None = None) -> dict[str, Any]:
    """[11-12 진단 전용·쓰기 없음] 섞인 PDF(글자 없는 이미지 쪽이 있는 문서) 규모를 센다. OCR 은 하지 않는다.

    문서마다: 쪽 수 · 글자 없는 이미지 쪽 수 · 텍스트층의 `OBSERVATION n` 최대 번호 · 저장된 관찰 수.
    `unread_pages>0` 이고 저장 관찰이 있는 문서가 "관찰이 일부만 들어갔을 수 있는" 후보다(195468 꼴)."""
    import fitz  # type: ignore[import-not-found]
    import collect_fda_483 as c
    from grm_common import http_get_bytes

    def _default_fetch(url: str) -> bytes:
        return http_get_bytes(url, timeout=30, retries=2, headers={"Accept": "application/pdf"},
                              label="FDA 483 PDF(scan)")

    get = fetch_bytes or _default_fetch
    pause = sleep_fn or time.sleep
    docs: list[dict[str, Any]] = []
    failed = 0
    for r in rows:
        raw = _json_object(r.get("raw_json"))
        url = str(raw.get("pdf_url") or "")
        if not url or str(raw.get("fda483_text_status") or "") == OUT_OF_SCOPE_STATUS:
            continue
        pause(delay_seconds)
        try:
            data = get(url)
            with fitz.open(stream=data, filetype="pdf") as doc:
                pages = doc.page_count
                layer = "\n".join(p.get_text("text") for p in doc)
            unread = c.count_unread_image_pages(data)
        except Exception:  # noqa: BLE001 — 진단: 건별 실패는 세고 계속
            failed += 1
            continue
        heads = [int(m.group(1)) for m in _OBS_HEAD_RE.finditer(layer)]
        # [11-12 보강] 문서 전체 텍스트층이 이미 OCR 경로(_needs_ocr 의 엔진 무관 조건)에 들면 수집 때
        # 쪽 단위 OCR 을 **이미 했다** — 그 문서의 이미지 쪽은 누락이 아니다. 첫 실행(43090296882)은
        # 이걸 가르지 않아 전면 스캔본까지 547건에 섞였다. 누락 후보 = 섞임 + OCR 경로를 건너뛴 문서.
        bp = c._repeated_lines(layer.splitlines())
        ocr_path = (not layer.strip()) or c._is_notice_only(layer) or not c._is_substantive_text(layer, bp)
        # 텍스트층에서 관찰이 0건이면 수집기가 전면 OCR 폴백(_full_ocr_fallback)을 이미 탔다 — 역시 누락 아님.
        if not ocr_path and not c._extract_483_observations_from_text(layer):
            ocr_path = True
        stored = len(raw.get("fda_483_observations") or [])
        d = {"doc": str(r.get("document_id") or ""), "pages": pages, "unread_pages": unread,
             "layer_max_obs": max(heads) if heads else 0, "stored_obs": stored,
             "ocr_path": ocr_path}
        docs.append(d)
        if unread and not ocr_path:
            # 아티팩트 업로드가 실패해도 목록이 남게 로그에 한 줄씩(43090296882 — CreateArtifact 오류).
            print(f"MIXED {d['doc']} pages={pages} unread={unread} layer_max_obs={d['layer_max_obs']} "
                  f"stored_obs={stored}", flush=True)
    mixed = [d for d in docs if d["unread_pages"] > 0]
    gap = [d for d in mixed if not d["ocr_path"]]
    return {
        "scanned": len(docs), "failed": failed,
        "mixed_docs": len(mixed),
        "mixed_already_ocr_path": len(mixed) - len(gap),
        "gap_docs": len(gap),
        "gap_unread_pages": sum(d["unread_pages"] for d in gap),
        "gap_with_stored_obs": sum(1 for d in gap if d["stored_obs"] > 0),
        "gap_without_stored_obs": sum(1 for d in gap if d["stored_obs"] == 0),
        "mixed_with_stored_obs": sum(1 for d in mixed if d["stored_obs"] > 0),
        "mixed_without_stored_obs": sum(1 for d in mixed if d["stored_obs"] == 0),
        "unread_pages_total": sum(d["unread_pages"] for d in mixed),
        "mixed": sorted(gap, key=lambda d: -d["unread_pages"]),
    }


def _rebuild_item(nrow: dict[str, str], text: str, status: str) -> Any:
    """라이브 수집과 **같은 함수**로 IntakeItem 재구성. None 이면 도메인 게이트 드롭."""
    from collect_fda_483 import (
        _extract_483_observations_from_text,
        _extract_fda483_excerpt,
        _to_item,
    )
    excerpt = _extract_fda483_excerpt(text) if text else ""
    header_hints = {
        "establishment_type": nrow.get("establishment_type", ""),
        "fei_number": nrow.get("fei", ""),
        "firm_name": nrow.get("company", ""),
    }
    observations = _extract_483_observations_from_text(text, header_hints) if text else []
    # body_full="" — deep 전문 보존은 ENABLE_FDA_483_DEEP 게이트 산출물이고, 이 복구의
    # 목표는 findings 를 만드는 결정론 층(excerpt/observations)이다. 원 적재 경로
    # (collect_fda_backfill.run_483)도 body_full 을 "" 로 넘긴다 — 그 계약을 그대로 지킨다.
    return _to_item(nrow, excerpt, observations, "", status)


# ---------------------------------------------------------------------------
# Supabase 조회 / 쓰기
# ---------------------------------------------------------------------------


def _fetch_raw_signals(
    base_url: str, service_key: str, *, page_size: int = _DEFAULT_PAGE_SIZE,
    mode: str = MODE_OCR_UNAVAILABLE, include_out_of_scope: bool = False,
    doc_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """source='FDA 483' raw_signals 중 이번 모드의 후보 행만.

    서버에서 `raw_json`(TEXT) 부분일치로 좁히고, 호출부가 파싱해 조건을 정확히 재확인한다
    (like 는 좁히기용 · 판정은 `is_recovery_candidate`). collected_at 은 upsert 시 원본을
    보존해야 하므로 함께 읽는다. `include_out_of_scope` 면 상한 표식 모드의 좁히기에
    `out-of-scope` 표식도 넣는다(`--doc-ids` 재회수 — `is_recovery_candidate(explicit=)` 참조).
    """
    extra = (OUT_OF_SCOPE_STATUS,) if include_out_of_scope else ()
    if mode == MODE_NAMED:
        narrow = {"document_id": "in.(" + ",".join(doc_ids or []) + ")"}
    elif mode == MODE_MISSING_OBSERVATIONS:
        # 관찰 키가 **없는** 행. `not.like` 로 서버에서 1차로 좁힌다.
        narrow = {"raw_json": f"not.like.*{_OBSERVATIONS_KEY}*"}
    elif mode == MODE_COLLECTION_CAPPED:
        narrow = {"or": "(" + ",".join(
            f"raw_json.like.*{status}*" for status in (*CAPPED_TEXT_STATUSES, *extra)) + ")"}
    elif mode == MODE_RECOVERABLE:
        narrow = {"or": "(" + ",".join(
            f"raw_json.like.*{status}*" for status in
            (*CAPPED_TEXT_STATUSES, OCR_UNAVAILABLE_PREFIX, "fetch-fail", *extra)) + ")"}
    else:
        narrow = {"raw_json": f"like.*{OCR_UNAVAILABLE_PREFIX}*"}
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="raw_signal_id,document_id,collected_at,raw_sha256,raw_json,row_json",
        page_size=page_size, order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", **narrow},
    )


def _upsert_raw_signal(
    base_url: str,
    service_key: str,
    record: dict[str, Any],
    *,
    row: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """재구성된 raw_signals 행을 제자리 교체 — [원문 갱신 규칙 R3 2026-11-12] raw_signal_apply_version 경유.

    예전에는 merge-duplicates upsert 로 덮어써 옛 판이 남지 않았다. 지금은 읽은 시점 지문(row.raw_sha256)을
    expected 로 넘겨, 그 사이 다른 도구가 바꿨으면 conflict 로 멈추고 옛 판은 보관표에 남긴다.
    반환 모양 (status, returned, err) 은 예전 그대로다.
    """
    del timeout   # 함수 호출 쪽 시간 상한(raw_versioning) 을 쓴다
    return rv.apply_record(base_url, service_key, record, row, change_kind="reextract",
                           changed_by=rv.run_label("backfill_483_ocr_recovery"),
                           reason="483_ocr_recovery")


def _patch_raw_json(
    base_url: str, service_key: str, raw_signal_id: str, raw: dict[str, Any],
    *, row: dict[str, Any] | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """raw_json 표식만 바꾼 판으로 교체 — 재구성이 게이트에 드롭되는 행의 표식 갱신용.

    [R3] PATCH 대신 raw_signal_apply_version 경유(지문 재계산·옛 판 보관).
    """
    del timeout
    rv.replace_raw(base_url, service_key, {**(row or {}), "raw_signal_id": raw_signal_id}, raw,
                   change_kind="repair", changed_by=rv.run_label("backfill_483_ocr_recovery"),
                   diff_summary={"reason": "483_status_marker"})


def _mark_out_of_scope_in_place(
    report: OcrRecoveryReport,
    rid: str,
    raw: dict[str, Any],
    *,
    label: str,
    base: str,
    service_key: str,
    dry_run: bool,
    patch_fn: Callable[..., None],
    row: dict[str, Any] | None = None,
) -> None:
    """시설 유형으로 범위 밖인 행의 표식을 `out-of-scope` 로 — 다른 키는 그대로 둔다."""
    if dry_run:
        report.status_resolved += 1
        return
    try:
        patch_fn(base, service_key, rid, {**raw, "fda483_text_status": OUT_OF_SCOPE_STATUS}, row=row)
    except Exception as exc:  # noqa: BLE001
        report.failed += 1
        _bump(report, f"patch_failed:{type(exc).__name__}")
        report.errors.append(f"{label}: patch_failed:{type(exc).__name__}")
        return
    report.status_resolved += 1


def _is_definitive_empty(status: str) -> bool:
    """다시 읽은 결과가 "이 문서는 본문이 없다"는 **확정** 사유인가.

    상한 표식이 다시 나오면(회수 실행도 예산 소진) 확정이 아니다. fetch 실패는 404 만 확정이다 —
    5xx·타임아웃으로 표식을 `fetch-fail` 로 바꿔 버리면 일시 장애 하나로 재시도가 영구히 끊긴다.
    """
    if not status or status in CAPPED_TEXT_STATUSES:
        return False
    # 엔진 부재는 문서가 아니라 **그 러너**의 사정이다 — 확정으로 보고 표식을 바꾸면
    # collection-capped 후보에서 영영 빠진다(엔진 없는 실행 한 번이 결손을 영구화한다).
    if status.startswith(OCR_UNAVAILABLE_PREFIX):
        return False
    if status.startswith("fetch-fail"):
        return "404" in status
    return True


def _same_definitive_reason(raw: dict[str, Any], status: str) -> bool:
    """다시 읽은 사유가 이미 적힌 사유와 같은가 — 같으면 다시 쓰지 않는다(404 재확인용)."""
    old = str(raw.get("fda483_text_status") or "")
    if old.startswith("fetch-fail") and status.startswith("fetch-fail"):
        return ("404" in old) == ("404" in status)
    return old == status


def _is_unmarked_empty_row(raw: dict[str, Any]) -> bool:
    """상태 키·발췌·관찰이 모두 없는 행 — 어떤 결손 사유도 기록되지 않은 행(순수 함수)."""
    return not (raw.get("fda483_text_status") or raw.get("fda483_excerpt")
                or raw.get(_OBSERVATIONS_KEY))


def _marks_definitive_reason(
    mode: str, raw: dict[str, Any], *, gate_dropped: bool = False,
) -> bool:
    """다시 읽은 결과 확정 사유(404·텍스트층 없음·범위 밖)가 나왔을 때 표식을 적는 모드인가.

    상한·복구 가능 모드는 늘 적는다(안 적으면 예약 실행이 같은 문서를 매일 다시 받는다).
    `missing-observations` 는 [2026-10-28] 부터 적는다 — 종전엔 이 모드가 범위 밖·본문 없음
    문서를 읽고도 아무것도 쓰지 않아, 상태 키 없는 행 25건이 원인 미상으로 남았다.
    단 본문 없음(404 등)은 **발췌도 없는 행에만** 적는다 — 재구성은 빈 본문으로 하므로 이미
    있는 발췌를 지우게 된다. 범위 밖은 발췌가 있어도 적는다(범위 밖 문서의 발췌는 쓰이지
    않고, 상한 모드와 같은 규칙이다).
    """
    if mode in (MODE_COLLECTION_CAPPED, MODE_RECOVERABLE):
        return True
    if mode != MODE_MISSING_OBSERVATIONS:
        return False
    return gate_dropped or _is_unmarked_empty_row(raw)


def _resolve_capped_status(
    report: OcrRecoveryReport,
    row: dict[str, Any],
    raw: dict[str, Any],
    status: str,
    *,
    label: str,
    base: str,
    service_key: str,
    dry_run: bool,
    rebuild_fn: Callable[..., Any],
    upsert_fn: Callable[..., tuple[int, list[dict[str, Any]] | None, str]],
) -> None:
    """상한 표식을 실제 사유로 교체한다 — 본문 복구와 같은 재구성·id 검증·upsert 경로."""
    try:
        item = rebuild_fn(nrow_from_raw_payload(raw), "", status)
    except Exception as exc:  # noqa: BLE001
        report.failed += 1
        _bump(report, f"rebuild_raised:{type(exc).__name__}")
        report.errors.append(f"{label}: rebuild_raised:{type(exc).__name__}")
        return
    if item is None:
        report.gate_dropped += 1
        return
    record = findings_store.raw_signal_from_intake_item(
        item, collected_at=str(row.get("collected_at") or ""),
    )
    if str(record.get("raw_signal_id") or "") != str(row.get("raw_signal_id") or ""):
        report.id_mismatch += 1
        _bump(report, "raw_signal_id_mismatch")
        report.errors.append(f"{label}: raw_signal_id_mismatch")
        return
    if dry_run:
        report.status_resolved += 1
        return
    try:
        _status, _returned, err = upsert_fn(base, service_key, record, row=row)
    except Exception as exc:  # noqa: BLE001
        err = f"raised:{type(exc).__name__}"
    if err:
        report.failed += 1
        _bump(report, f"upsert_failed:{err}")
        report.errors.append(f"{label}: upsert_failed:{err}")
        return
    report.status_resolved += 1


def _is_broad_failure(report: OcrRecoveryReport) -> bool:
    """실패가 인프라성인지 판정 — tesseract 미설치로 전건 실패하는 상황을 침묵시키지 않는다.
    (이 스크립트가 정확히 그 결함을 고치러 왔으므로, 같은 결함으로 조용히 끝나면 안 된다.)"""
    if report.attempted == 0:
        return False
    if report.attempted < _BROAD_FAILURE_MIN_ATTEMPTS:
        return report.failed == report.attempted
    return (report.failed / report.attempted) >= _BROAD_FAILURE_RATIO


def run(
    *,
    base_url: str,
    service_key: str,
    dry_run: bool,
    limit: int | None = None,
    delay_seconds: float = _DEFAULT_DELAY_SECONDS,
    doc_ids: list[str] | None = None,
    mode: str = MODE_OCR_UNAVAILABLE,
    fetch_raw_signals: Callable[..., list[dict[str, Any]]] | None = None,
    fetch_text: Callable[[str], tuple[str, str]] | None = None,
    rebuild_item: Callable[..., Any] | None = None,
    upsert_raw_signal: Callable[..., tuple[int, list[dict[str, Any]] | None, str]] | None = None,
    patch_raw_json: Callable[[str, str, str, dict[str, Any]], None] | None = None,
    sleeper: Sleeper | None = None,
) -> tuple[OcrRecoveryReport, int]:
    """지연 바인딩 설계(collect_eu_ncr_backfill.run·backfill_483_inspectors.run 과 동일):
    기본값을 def 시점이 아니라 호출 시점에 해석해, 테스트가 모듈 속성을 패치하면 반영된다."""
    fetch_raw = fetch_raw_signals or _fetch_raw_signals
    text_fn = fetch_text or _fetch_text
    rebuild_fn = rebuild_item or _rebuild_item
    upsert_fn = upsert_raw_signal or _upsert_raw_signal
    patch_fn = patch_raw_json or _patch_raw_json
    sleep_fn = sleeper or _default_sleep

    report = OcrRecoveryReport(
        mode="dry_run" if dry_run else "apply", limit=limit, delay_seconds=delay_seconds,
        select_mode=mode,
    )

    base = _normalize_base_url(base_url)
    if base is None:
        report.errors.append("SUPABASE_URL must start with https://")
        return report, 2

    explicit = bool(doc_ids)
    if mode == MODE_NAMED and not explicit:
        report.errors.append("mode=named 는 --doc-ids 가 필요하다")
        return report, 2
    try:
        if mode == MODE_NAMED:
            rows = fetch_raw(base, service_key, mode=mode, include_out_of_scope=True,
                             doc_ids=list(doc_ids or []))
        else:
            rows = fetch_raw(base, service_key, mode=mode, include_out_of_scope=explicit)
    except (RuntimeError, ValueError) as exc:
        report.errors.append(str(exc))
        log("ERROR", f"483 OCR 복구 조회 실패: {exc}")
        return report, 2

    report.scanned = len(rows)

    # 서버 like 는 좁히기용 — 상태값을 실제로 파싱해 대상만 남긴다.
    marked = [r for r in rows
              if is_recovery_candidate(_json_object(r.get("raw_json")), mode, explicit=explicit)]
    report.marked = len(marked)

    if doc_ids:
        wanted = set(doc_ids)
        marked = [
            r for r in marked
            if str(r.get("raw_signal_id") or "") in wanted
            or str(r.get("document_id") or "") in wanted
        ]

    marked.sort(key=lambda r: str(r.get("raw_signal_id") or ""))
    # `limit=0` 은 **전건**(0 이하 = 상한 없음) — 형제 워크플로들과 통일된 규약이다.
    # (2026-07-30 실사고: limit>=0 해석이 0 을 "0건 처리"로 받아 apply 가 조용히 무동작했다.)
    if limit is not None and limit > 0:
        marked = marked[:limit]
    report.candidates = len(marked)

    for r in marked:
        rid = str(r.get("raw_signal_id") or "")
        doc_id = str(r.get("document_id") or "")
        label = doc_id or rid
        raw = _json_object(r.get("raw_json"))
        pdf_url = str(raw.get("pdf_url") or "").strip()

        # ★[2026-10-28 정리 회수 실측] 시설 유형만으로 범위 밖(혈액·IRB·조직·생식·사료 등)인
        #   행은 **본문 없이 재구성해도** 게이트가 드롭해 표식을 바꿀 수 없다 — 그대로 두면
        #   예약 회수가 매일 PDF 를 받아 OCR 한다(정리 회수 46건 중 18건이 이 경우). 수집기가
        #   같은 판정으로 PDF 를 받지 않는 것(`collect_fda_483` 의 시설 유형 사전 차단)과 맞춰,
        #   상한 표식 모드에서는 받지 않고 센다. 행은 남지만 비용은 0 이다.
        #   [2026-11-07] `missing-observations` 도 같은 원칙이다 — 시설 유형은 FDA 가 적은 확정값이라
        #   본문을 다시 읽어도 판정이 뒤집히지 않는다. 이 모드로 정리 회수를 돌리면 옛 범위 밖 행
        #   (계란 농장·혈액원 등, 발췌만 있고 관찰 0건)이 표식 없이 남아 감시에 계속 섞였다.
        if mode in (MODE_COLLECTION_CAPPED, MODE_RECOVERABLE, MODE_MISSING_OBSERVATIONS) and \
                is_out_of_scope_483_establishment(str(raw.get("establishment_type") or "")):
            report.skipped_out_of_scope_type += 1
            # ★[2026-10-28] 받지 않고 넘기기만 하면 상한 표식이 그대로 남아 추출 격차 감시
            #   (`extraction_gap_by_source`)가 이 행을 결손으로 센다. 재구성은 게이트가 드롭하므로
            #   표식만 `out-of-scope` 로 제자리 PATCH 한다(PDF fetch 없음 · 다른 키 불변).
            if str(raw.get("fda483_text_status") or "") != OUT_OF_SCOPE_STATUS:
                _mark_out_of_scope_in_place(
                    report, rid, raw, label=label, base=base, service_key=service_key,
                    dry_run=dry_run, patch_fn=patch_fn, row=r,
                )
            continue

        report.attempted += 1

        if not pdf_url:
            report.failed += 1
            _bump(report, "missing_pdf_url")
            report.errors.append(f"{label}: missing_pdf_url")
            continue

        sleep_fn(delay_seconds)
        try:
            text, status = text_fn(pdf_url)
        except Exception as exc:  # noqa: BLE001 — 건별 실패는 계속(멱등 재실행 가능)
            report.failed += 1
            reason = f"fetch_raised:{type(exc).__name__}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            continue

        if not text:
            # OCR 을 다시 돌려도 본문이 없다 — 기존 행을 그대로 둔다. 빈손을 다른 빈손으로
            # 덮어쓰지 않는다(엔진이 여전히 없다면 failure_reasons 에 그 사유가 쌓인다).
            report.still_empty += 1
            report.no_text += 1
            _bump(report, status or "empty_text")
            # ★예외 하나: 상한 표식(not-attempted·scan-ocr-budget)은 "아직 안 읽었다"는 뜻이지
            #   문서의 사실이 아니다. 실제로 읽어 본 결과가 상한이 아닌 사유(404·텍스트층 없음)면
            #   그 사유로 바꿔 적는다 — 잃는 본문은 없고(둘 다 빈손), 바꾸지 않으면 그 문서가
            #   매 예약 실행마다 후보로 남아 같은 PDF 를 영원히 다시 받는다.
            if (_marks_definitive_reason(mode, raw) and _is_definitive_empty(status)
                    and not _same_definitive_reason(raw, status)):
                _resolve_capped_status(
                    report, r, raw, status, label=label, base=base, service_key=service_key,
                    dry_run=dry_run, rebuild_fn=rebuild_fn, upsert_fn=upsert_fn,
                )
            continue

        nrow = nrow_from_raw_payload(raw)
        try:
            item = rebuild_fn(nrow, text, status)
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            reason = f"rebuild_raised:{type(exc).__name__}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            continue

        if item is None:
            # 도메인 게이트(수의/기기/식품) 드롭 — 애초에 적재되지 말았어야 할 행일 수 있으나
            # 이 스크립트는 삭제하지 않는다(범위 밖). 세어서 드러내기만 한다.
            report.gate_dropped += 1
            if _marks_definitive_reason(mode, raw, gate_dropped=True):
                # ★[2026-10-28 실측] 본문을 받아 보니 범위 밖이면 상한 표식이 그대로 남아,
                #   예약 회수가 **매일 같은 문서를 다시 받아 OCR 한다**(첫 회수 실행: 152건 중
                #   71건이 범위 밖 — 상한 표식 38건이 그대로 남음). 예산 600쪽을 버릴 문서가
                #   갉아먹으므로 표식을 확정 사유 `out-of-scope` 로 바꿔 재시도를 끊는다.
                counted = report.gate_dropped
                _resolve_capped_status(
                    report, r, raw, OUT_OF_SCOPE_STATUS, label=label, base=base,
                    service_key=service_key, dry_run=dry_run, rebuild_fn=rebuild_fn,
                    upsert_fn=upsert_fn,
                )
                report.gate_dropped = counted   # 재구성도 드롭이면 이미 센 1건 — 중복 계수 방지
            continue

        record = findings_store.raw_signal_from_intake_item(
            item, collected_at=str(r.get("collected_at") or ""),   # ★원본 시점 보존
        )
        if str(record.get("raw_signal_id") or "") != rid:
            # 재구성이 다른 문서를 가리킨다 — 떠돌이 행을 만들지 않는다(정상이면 0건).
            report.id_mismatch += 1
            _bump(report, "raw_signal_id_mismatch")
            report.errors.append(f"{label}: raw_signal_id_mismatch")
            continue

        new_raw = _json_object(record.get("raw_json"))
        observations = new_raw.get("fda_483_observations") or []
        if not (new_raw.get("fda483_excerpt") or observations):
            # 텍스트는 받았지만 결정론 층이 아무것도 못 뽑았다 — 교체 이득이 없다.
            report.still_empty += 1
            report.text_without_extraction += 1
            _bump(report, "no_excerpt_or_observations")
            if mode == MODE_MISSING_OBSERVATIONS and _is_unmarked_empty_row(raw):
                # 본문은 받았는데 결정론 층이 0건 — 그 사실(`pdf-ok` 등 PDF 상태)을 적는다.
                # 상태 키도 발췌도 없는 행은 "왜 비었는지"가 어디에도 없어 감사 때마다 원인
                # 미상으로 남았다(2026-10-28 실측 25건 — EIR 송부 공문·주 정부 실사 기록 등).
                _resolve_capped_status(
                    report, r, raw, status, label=label, base=base, service_key=service_key,
                    dry_run=dry_run, rebuild_fn=rebuild_fn, upsert_fn=upsert_fn,
                )
            continue

        if mode == MODE_NAMED and len(observations) <= len(raw.get("fda_483_observations") or []):
            # 다시 읽어도 관찰이 늘지 않는다 — 기존 행(다시 읽기로 다듬은 상세 포함)을 지키고 넘긴다.
            report.named_no_gain += 1
            _bump(report, "named_no_gain")
            continue
        if mode == MODE_NAMED and not _keeps_old_deficiencies(raw.get("fda_483_observations") or [],
                                                              observations):
            # [11-12] 관찰 수는 늘었어도 **기존 표제가 하나라도 사라지면** 교체하지 않는다 — 표제가 바뀌면
            # finding_id 가 바뀌어 옛 findings·번역이 고아로 남고 새 행과 겹친다(대량 재추출 전 가드).
            report.named_no_gain += 1
            _bump(report, "named_would_drop_deficiency")
            continue
        if mode == MODE_NAMED:
            loss = text_loss(raw, new_raw)
            reason = text_loss_reason(loss)
            if reason:
                # [11-13] 관찰 수·표제가 늘어도 옛 본문이 사라지면 교체하지 않는다.
                report.named_no_gain += 1
                _bump(report, "named_would_drop_text")
                report.named_text_loss.append({
                    "raw_signal_id": rid,
                    "document_id": doc_id,
                    "lost_chars": loss["lost_chars"],
                    "runs": loss["runs"],
                    "sample": loss["sample"],
                    "reason": reason,
                    "holder_cut_chars": loss["holder_cut_chars"],
                    "holder_cut_sample": loss["holder_cut_sample"],
                })
                log("WARN", f"named 본문 손실 차단 {label}: reason={reason} lost_chars={loss['lost_chars']} "
                            f"sample={loss['sample']}")
                continue

        if len(report.samples) < _MAX_SAMPLES:
            report.samples.append({
                "raw_signal_id": rid,
                "document_id": doc_id,
                "pdf_status": status,
                "observations": len(observations),
                "observations_before": len(raw.get("fda_483_observations") or []),
                "excerpt_chars": len(str(new_raw.get("fda483_excerpt") or "")),
            })

        if dry_run:
            _count_recovered(report, observations)
            continue

        try:
            _status, returned, err = upsert_fn(base, service_key, record, row=r)
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            reason = f"upsert_raised:{type(exc).__name__}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            continue

        if err:
            report.failed += 1
            reason = f"upsert_failed:{err}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            log("WARN", f"483 raw_signals upsert 실패 {label}: {err}")
            continue

        _count_recovered(report, observations)

    # ★진단 가능한 요약: "빈손"을 원인별로 쪼개 찍는다. 합계만 찍으면 수집 문제와 파서
    #   문제가 같은 숫자로 보여 오진을 부른다(v1 에서 실제로 3회 발생).
    head = f"[DRY] 대상 {report.candidates}건 중 복구 가능" if dry_run else "복구 upsert"
    log("INFO", f"{head} {report.recovered}건(관찰 {report.observations_recovered}건"
                f"·발췌만 {report.recovered_excerpt_only}건) "
                f"· 본문확보실패 {report.no_text}건 "
                f"· 본문있으나추출0 {report.text_without_extraction}건 "
                f"· 게이트드롭 {report.gate_dropped}건 · 실패 {report.failed}건"
                f"{f' · 상한표식→실제사유 {report.status_resolved}건' if report.status_resolved else ''}")
    if report.text_without_extraction or report.recovered_excerpt_only:
        log("INFO", f"↑ 파서 신호: 본문을 확보하고도 지적사항을 못 뽑은 문서 "
                    f"{report.text_without_extraction + report.recovered_excerpt_only}건 "
                    f"— OCR·해상도가 아니라 추출 로직을 봐야 한다.")

    exit_code = 1 if _is_broad_failure(report) else 0
    return report, exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OCR 엔진 부재로 빈 본문 적재된 스캔 483 소급 복구 — raw_signals 행을 "
        "라이브 수집과 같은 함수로 재구성해 제자리 upsert. 예약 실행은 collection-capped 모드.",
    )
    p.add_argument(
        "--scan-mixed", action="store_true", default=False,
        help="진단 전용(쓰기 없음): FDA 483 PDF 를 받아 글자 없는 이미지 쪽이 있는 문서(섞인 PDF)를 센다.",
    )
    p.add_argument(
        "--dump-text-dir", default="",
        help="진단 전용(쓰기 없음): --doc-ids 문서의 판독 전문을 이 폴더에 <doc>.txt 로 남기고 끝낸다.",
    )
    p.add_argument(
        "--apply", action="store_true", default=False,
        help="실제 upsert 수행. 기본은 dry-run(조회+PDF fetch+재구성까지 하되 쓰기는 생략).",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="이번 실행에서 처리할 문서 수 상한(배치 분할용). 0 또는 미지정이면 전건.",
    )
    p.add_argument(
        "--delay-seconds", type=float, default=_DEFAULT_DELAY_SECONDS,
        help=f"PDF 요청 간 대기(초) — fda.gov 예의(기본 {_DEFAULT_DELAY_SECONDS}).",
    )
    p.add_argument(
        "--doc-ids", default="",
        help="선택: 처리할 raw_signal_id 또는 document_id 목록(공백/쉼표 구분). "
        "collection-capped·recoverable 모드에서 지목하면 out-of-scope 표식 행도 다시 읽는다 "
        "(게이트 수정 후 오판 표식 재회수용 — 예약 실행은 지목이 없어 해당 없음).",
    )
    p.add_argument(
        "--mode", choices=list(RECOVERY_MODES), default=MODE_OCR_UNAVAILABLE,
        help=(
            "후보 선정 기준. ocr-unavailable(기본)=엔진 부재로 본문을 못 받은 행. "
            "missing-observations=본문 유무와 무관하게 관찰 키가 없는 행 — 상태 키가 없어 "
            "지금까지 어떤 복구 잡에도 걸리지 않던 사각지대(2026-08-01 실측 417건). "
            "collection-capped=수집 상한(PDF 건수·OCR 예산) 때문에 본문 없이 적재된 행 — "
            "예약 실행이 매일 쓰는 모드(#1209)."
        ),
    )
    p.add_argument("--report-path", help="JSON 리포트를 이 경로에도 기록.")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)

    if args.dump_text_dir:
        ids = _parse_doc_ids(args.doc_ids)
        if not ids:
            print("--dump-text-dir 는 --doc-ids 와 함께 쓴다(진단 전용).", file=sys.stderr)
            return 2
        rows = dump_texts(ids, args.dump_text_dir)
        payload = json.dumps({"dump_text": rows}, ensure_ascii=False, indent=2)
        print(payload)
        if args.report_path:
            Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
        return 0

    if args.scan_mixed:
        creds = _resolve_credentials(args)
        if creds is None:
            print("--scan-mixed 는 조회용 자격증명이 필요하다(쓰기 없음).", file=sys.stderr)
            return 2
        rows = fsb._fetch_all_pages(
            creds[0], creds[1], "raw_signals", select="raw_signal_id,document_id,raw_json",
            page_size=_DEFAULT_PAGE_SIZE, order="raw_signal_id.asc",
            extra_params={"source": f"eq.{SOURCE_FDA_483}"})
        ids = set(_parse_doc_ids(args.doc_ids))
        if ids:
            rows = [r for r in rows if str(r.get("document_id") or "") in ids]
        if args.limit:
            rows = rows[:args.limit]
        result = scan_mixed_pdfs(rows, delay_seconds=args.delay_seconds)
        payload = json.dumps({"scan_mixed": result}, ensure_ascii=False, indent=2)
        print(json.dumps({k: v for k, v in result.items() if k != "mixed"}, ensure_ascii=False))
        if args.report_path:
            Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
        return 0

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "backfill_483_ocr_recovery: --supabase-url/--service-role-key 또는 "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요(dry-run 도 조회에 필요).",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report, exit_code = run(
        base_url=base_url,
        service_key=service_key,
        dry_run=not args.apply,
        limit=args.limit,
        delay_seconds=args.delay_seconds,
        doc_ids=_parse_doc_ids(args.doc_ids),
        mode=args.mode,
    )

    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.report_path:
        Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
    return exit_code


__all__ = [
    "OcrRecoveryReport",
    "text_loss",
    "text_loss_reason",
    "run",
    "main",
    "build_arg_parser",
    "is_ocr_unavailable_row",
    "is_collection_capped_row",
    "nrow_from_raw_payload",
]


if __name__ == "__main__":
    raise SystemExit(main())
