#!/usr/bin/env python3
"""[483 상세 다시 읽기 2026-11-10] 이미 저장된 FDA 483 관찰 **상세만** 제자리에서 고친다.

지식 그래프 방향 전환 0단계. 수집기(`collect_fda_483`)의 상세 수리(쪽 넘김 이어붙이기·서명란
이름 제거·가림 표시 `[redacted]` 통일·상한 8000)는 새로 들어오는 문서에만 적용된다. 이 스크립트가
지정 기간(기본: 게시일 2026-08-10 이후) 문서를 다시 읽어 `fda_483_observations[].detail` 만 바꾼다.

★`backfill_483_ocr_recovery.py` 를 쓰지 않는 이유: 그쪽은 행 전체를 재구성하며
  `fda483_body_full`(심층 분석 원문)·`fda483_inspectors` 를 비운다(그 스크립트의 대상은 본문이
  없던 행이라 문제가 없었다). 여기서는 **detail 칸 외에는 아무것도 바꾸지 않는다.**
★번호·관찰 수는 바꾸지 않는다. 다시 읽은 관찰을 (번호, 표제)가 같은 기존 관찰에 대응시킨다. 옛 저장 상한에
  잘린 관찰(표제 280자+"..."·상세 1,200자)은 번호가 같고 새 판이 옛 앞부분으로 시작할 때만 대응시켜 표제·상세를
  잘리지 않은 판으로 바꾼다(`--truncated-only` 는 그런 문서만 고른다). 그래도 짝이 없는 1,200자 절단 조각은
  끝 80자가 다시 읽은 원문 한 곳에만 있을 때 **끊긴 문장 하나만** 마저 잇는다(`completed_sentence`).
  짝이 없는 관찰은 기존 상세를 그대로 두되 서명란 이름·가림 표시만 정리한다.
★표제가 바뀐 관찰의 findings 행(finding_text = 표제)은 여기서 바꾸지 않는다 — finding_id 가 본문 해시라
  `backfill_wl_truncation_restore.py --source fda483` 계획 → 번역 outbox restore 경로로 영문·국문을 함께 바꾼다.
★원문: 저장된 `fda483_body_full` 이 있으면 그것을 쓴다(PDF 요청·OCR 없음). 없으면 PDF 를 다시 받는다.
★기본은 dry-run — 바뀔 관찰 수와 품질 지표(문장 온전·서명란 흔적·깨진 가림 표시)의 전후를 보고만 한다.
★[11-25] 본문 축소 가드(`guard_shrink`) — 다시 읽은 판의 산문 낱말이 옛 판보다 5% 넘게 적으면 바꾸지 않는다.
  쪽 끝 절단(옛 수집기가 서명란에서 끊어 다음 쪽 첫 항목이 빠진 관찰, 실측 189485 obs4)의 복원 규모를
  `pagebreak_*` 로 센다. raw 를 고친 뒤 findings.detail_text 는 `findings_detail_export.py --mode stale` 경로로 맞춘다.
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

import findings_supabase_backfill as fsb
import raw_versioning as rv
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import FDA_BOT_BLOCK, SOURCE_FDA_483
from grm_http_policy import FDA_483_PDF_DELAY_SECONDS

DEFAULT_SINCE = "2026-08-10"
_ALL_TIME = "1900-01-01"
_OBS_KEY = "fda_483_observations"
_COMPLETE_END_RE = re.compile(r"[.!?][)\"'”’]?$|\)$")
_GARBLED_MARK_RE = re.compile(r"[@©®]")


@dataclass
class QualityStats:
    observations: int = 0
    complete_sentence: int = 0
    signer_or_footer_trace: int = 0
    garbled_redaction: int = 0
    total_chars: int = 0


@dataclass
class DetailRefreshReport:
    dry_run: bool = True
    since: str = DEFAULT_SINCE
    candidates: int = 0
    from_body_full: int = 0
    from_pdf: int = 0
    no_text: int = 0
    docs_changed: int = 0
    observations_changed: int = 0
    observations_matched: int = 0
    observations_unmatched: int = 0
    deficiency_restored: int = 0
    truncated_only: bool = False
    truncated_before: int = 0
    truncated_after: int = 0
    patched: int = 0
    failed: int = 0
    before: QualityStats = field(default_factory=QualityStats)
    after: QualityStats = field(default_factory=QualityStats)
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    incomplete_samples: list[dict[str, Any]] = field(default_factory=list)
    # [11-11] 다시 읽은 뒤에도 잘림 띠에 남은 관찰의 원인 진단 — 원문에서 옛 상세 끝 바로 뒤에 무엇이 오나
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    # [11-25 쪽 끝 절단] 옛 수집기가 쪽 경계에서 끊은 관찰 — 다시 읽기로 본문(산문 낱말)이 늘어난 관찰과,
    # 그중 옛 상세에 없던 하위 항목 머리(`C.`·`iii.`)가 새로 생긴 관찰(= 다음 쪽 첫 항목이 빠져 있던 꼴).
    pagebreak_extended: int = 0
    pagebreak_items_added: int = 0
    pagebreak_docs: int = 0
    prose_words_before: int = 0
    prose_words_after: int = 0
    # 본문 축소 가드 — 새 상세의 산문 낱말이 옛 상세보다 줄면 바꾸지 않고 옛 상세(정리본)를 둔다.
    shrink_blocked: int = 0
    shrink_samples: list[dict[str, Any]] = field(default_factory=list)
    pagebreak_samples: list[dict[str, Any]] = field(default_factory=list)
    # [11-26 미완료 표시] FDA 봇 차단(또는 --limit)으로 도중에 멈추면 숫자가 전체처럼 읽힌다(11-25 dry 가 2,453 중
    # 2,202 에서 멈췄는데 전체로 보고됨). 멈춘 지점·남은 문서를 보고서 맨 앞 필드로 남기고 --resume-from 으로 잇는다.
    incomplete: bool = False
    stop_reason: str = ""
    total_candidates: int = 0
    processed: int = 0
    stopped_at_raw_signal_id: str = ""
    stopped_at_document_id: str = ""
    resume_from: str = ""
    remaining_doc_ids: list[str] = field(default_factory=list)
    # [11-26 DB 완전성] 다시 읽으니 새로 잡히는 관찰(저장 배열에 그 번호가 없음 = findings 행도 없음).
    # 기본은 세기만 한다. --add-missing-observations 일 때만 raw 배열에 덧붙인다(행 추가는 그 뒤
    # findings 추출 재실행 `grm-findings-backfill.yml` document_ids 가 한다).
    new_observations: int = 0
    new_observation_docs: int = 0
    new_observations_added: int = 0
    new_observation_doc_ids: list[str] = field(default_factory=list)
    new_observation_samples: list[dict[str, Any]] = field(default_factory=list)


def _num_key(o: dict[str, Any]) -> int:
    n = str(o.get("number") or "")
    return int(n) if n.isdigit() else 0


def missing_observations(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """다시 읽은 관찰 중 저장 배열에 **번호가 없는** 것(순수 함수). 같은 표제가 다른 번호로 이미 있으면
    (번호 판독만 다른 같은 관찰) 넣지 않는다 — 중복 행을 만들지 않는다."""
    have_nums = {str(o.get("number") or "") for o in old}
    have_defs = {" ".join(str(o.get("deficiency") or "").split()).lower() for o in old}
    out: list[dict[str, Any]] = []
    for o in new:
        num = str(o.get("number") or "")
        d = " ".join(str(o.get("deficiency") or "").split()).lower()
        if not num or num in have_nums or not d or d in have_defs:
            continue
        out.append(dict(o))
        have_nums.add(num)
    return out


def incomplete_banner(report: dict[str, Any]) -> str:
    """보고서가 전체를 다 돌지 못했으면 맨 위에 둘 한 줄(순수 함수). 다 돌았으면 빈 문자열."""
    if not report.get("incomplete"):
        return ""
    return (f"⚠ 미완료({report.get('stop_reason') or '?'}): 처리 {report.get('processed', 0)}/후보 "
            f"{report.get('total_candidates', 0)} · 멈춘 raw_signal_id {report.get('stopped_at_raw_signal_id') or '?'}"
            f" ({report.get('stopped_at_document_id') or '?'}) · 남은 문서 {len(report.get('remaining_doc_ids') or [])}"
            f" — 이 숫자는 전체가 아니다. `--resume-from {report.get('stopped_at_raw_signal_id') or '?'}` 로 이어 돌린다.")


def _gf():
    import grm_findings as gf
    return gf


def _collector():
    import collect_fda_483 as c   # 지연 import — 테스트가 모듈 함수를 바꿔 끼울 수 있게
    return c


def detail_quality(detail: str) -> dict[str, bool]:
    """상세 문자열 하나의 품질 신호(순수 함수)."""
    c = _collector()
    d = (detail or "").strip()
    return {
        "complete": bool(_COMPLETE_END_RE.search(d)),
        "trace": bool(c._FDA483_FOOTER_RE.search(d) or c._SIGNER_TITLE_RE.search(d)),
        "garbled": bool(_GARBLED_MARK_RE.search(c._EMAIL_RE.sub(" ", d))),
    }


def _add(stats: QualityStats, detail: str) -> None:
    if not (detail or "").strip():
        return
    q = detail_quality(detail)
    stats.observations += 1
    stats.complete_sentence += q["complete"]
    stats.signer_or_footer_trace += q["trace"]
    stats.garbled_redaction += q["garbled"]
    stats.total_chars += len(detail)


def tidy_old_detail(detail: str) -> str:
    """다시 읽은 짝이 없는 기존 상세 — 서명란 이름 꼬리와 가림 표시만 정리(순수 함수)."""
    c = _collector()
    d = c.trim_detail_tail(detail)
    return c.normalize_483_redactions(d) if d else (detail or "")


# ── [11-25 쪽 끝 절단 소급] 본문 축소 가드·복원 계측 ─────────────────────────────────────────────
# 번역팀 실측(189485 obs4): 옛 수집기가 6쪽 서명란에서 관찰을 끊어 다음 쪽 첫 항목(C.)이 상세에 없었다.
# 지금 수집기(쪽 넘김 잇기 #1461·#1529)는 잇지만, 저장된 옛 행은 다시 읽어야 고쳐진다.
# 다시 읽은 판이 옛 판보다 **본문이 적으면** 바꾸지 않는다 — 저장 단계에서는 줄이지 않는다는 원칙.
# 본문 양은 산문 낱말(소문자 3자 이상, 수집기 `_LOWER_WORD_RE` 와 같은 판정)로 센다: 쪽 블록 잡문
# (서명자·머리말·양식 낱말)은 대문자·약어라 거의 세지 않으므로, 잡문만 걷힌 정상 교체는 막지 않는다.
# 판독(OCR) 판이 달라 생기는 작은 흔들림은 허용한다(5%).
SHRINK_TOLERANCE = 0.05   # = grm_findings.FDA483_PROSE_TOLERANCE
SHRINK_MIN_WORDS = 20
EXTEND_MIN_WORDS = 5
_ITEM_HEAD_RE = re.compile(r"(?<![A-Za-z0-9])(?:([A-L])\.\s+(?=[A-Z(])|(i{1,3}|iv|vi{0,3}|ix)\.\s)")
_SAMPLE_MAX = 20


def prose_words(text: str) -> int:
    """본문 양 — 산문 낱말 수(순수 함수). 재번역 대기열과 같은 함수(`grm_findings.fda483_prose_words`)."""
    import grm_findings as gf
    return gf.fda483_prose_words(text)


def shrinks_body(old_text: str, new_text: str) -> bool:
    """새 판이 옛 판보다 본문이 줄었나(순수 함수). 옛 판이 짧으면(산문 20낱말 미만) 판정하지 않는다."""
    before = prose_words(old_text)
    if before < SHRINK_MIN_WORDS:
        return False
    return prose_words(new_text) < before * (1 - SHRINK_TOLERANCE)


def item_heads(text: str) -> set[str]:
    """하위 항목 머리(`A.`·`ii.`) 집합(순수 함수)."""
    return {(m.group(1) or m.group(2)) for m in _ITEM_HEAD_RE.finditer(text or "")}


def _obs_text(o: dict[str, Any]) -> str:
    d = str(o.get("deficiency") or "")
    if d.endswith(TRUNC_DEFICIENCY_SUFFIX):
        d = d[:-len(TRUNC_DEFICIENCY_SUFFIX)]
    return f"{d} {o.get('detail') or ''}"


def guard_shrink(old: list[dict[str, Any]], refreshed: list[dict[str, Any]]
                 ) -> tuple[list[dict[str, Any]], list[int]]:
    """본문 축소 가드(순수 함수) — 표제+상세의 본문이 옛 판보다 줄어드는 교체는 되돌린다.
    되돌린 관찰은 옛 표제와 옛 상세(서명란 꼬리·가림 표시만 정리)를 둔다. (새 목록, 되돌린 위치)."""
    out: list[dict[str, Any]] = []
    blocked: list[int] = []
    for i, (o_old, o_new) in enumerate(zip(old, refreshed)):
        changed = (str(o_old.get("detail") or "") != str(o_new.get("detail") or "")
                   or str(o_old.get("deficiency") or "") != str(o_new.get("deficiency") or ""))
        if changed and shrinks_body(_obs_text(o_old), _obs_text(o_new)):
            blocked.append(i)
            out.append({**o_old, "detail": tidy_old_detail(str(o_old.get("detail") or ""))})
        else:
            out.append(o_new)
    return out, blocked


TRUNC_DEFICIENCY_SUFFIX = "..."
# 옛 저장 상한(상세 1,200자·표제 280자+"...")에 걸린 관찰을 찾는 탐지 띠(판정 전용 — 저장은 원문 그대로).
TRUNC_DETAIL_BAND = (1150, 1205)
TRUNC_DEFICIENCY_BAND = (270, 290)
_PREFIX_PROBE = 200


def _norm(text: str) -> str:
    """비교용 정규화 — 가림 표시 통일 + 공백 접기."""
    c = _collector()
    return re.sub(r"\s+", " ", c.normalize_483_redactions(text or "")).strip()


def is_truncated_deficiency(deficiency: str) -> bool:
    d = deficiency or ""
    return d.endswith(TRUNC_DEFICIENCY_SUFFIX) and TRUNC_DEFICIENCY_BAND[0] <= len(d) <= TRUNC_DEFICIENCY_BAND[1]


def is_truncated_detail(detail: str) -> bool:
    return TRUNC_DETAIL_BAND[0] <= len(detail or "") <= TRUNC_DETAIL_BAND[1]


def has_truncated_observation(observations: list[dict[str, Any]]) -> bool:
    return any(is_truncated_deficiency(str(o.get("deficiency") or ""))
               or is_truncated_detail(str(o.get("detail") or "")) for o in observations)


def _restored_deficiency(old_def: str, new_def: str) -> bool:
    """옛 표제가 280자+"..." 절단이고 새 표제가 그 앞부분으로 시작하며 더 길다."""
    if not is_truncated_deficiency(old_def):
        return False
    head = _norm(old_def[:-len(TRUNC_DEFICIENCY_SUFFIX)])
    nd = _norm(new_def)
    return bool(head) and nd.startswith(head) and len(nd) > len(head)


def _extends_detail(old_detail: str, new_detail: str) -> bool:
    """새 상세가 옛 상세의 앞부분(정규화 200자)으로 시작하고 더 길다 — 같은 관찰의 잘리지 않은 판."""
    od, nd = _norm(old_detail), _norm(new_detail)
    probe = od[:_PREFIX_PROBE]
    if probe and nd.startswith(probe) and len(nd) > len(od):
        return True
    # [11-11] 옛 1,200자 절단 상세는 앞머리(머리말 조각·옛 가림 표기)가 새 판과 조금 달라 위 조건에
    # 걸리지 않았다(다시 읽기 뒤 42건 잔존). 옛 상세가 절단 띠 안이고, 그 **가운데 구간**(정규화
    # 300~600자)과 **끝 구간**(마지막 120자)이 새 상세에 그대로 있으며 새 판이 더 길면 같은 관찰로 본다.
    if not is_truncated_detail(old_detail) or len(nd) <= len(od):
        return False
    mid, tail = od[300:600], od[-120:]
    return len(mid) >= 200 and mid in nd and tail in nd


_TAIL_PROBE = 80
_SENTENCE_TAIL_MAX = 600
_SENTENCE_END_RE = re.compile(r"[.!?][)\"'”’]?(?=\s|$)")
# 공백을 지운 조각에서 찾는다(`s ee r e vers e o f th is page` 같은 벌어진 판독, 실측 107200).
_FORM_TRACE_RE = re.compile(r"(?i)seereverse|ofthispage|employee|signature|formfda|investigator|dateissued"
                            r"|inspected|zipcode|firmname|pageof|previousedition|datesofinspection")


def _agreed_continuation(probe: str, haystacks: list[str]) -> str | None:
    """probe 뒤에 오는 첫 문장(양식 낱말 금지). probe 가 여러 곳이면 **모든 자리의 뒤 문장이 같을 때만**.

    [11-12] 딱 한 곳만 받던 규칙은 PDF 텍스트에 쪽이 두 번 실리거나(실측 96468·85303) 같은 문장이
    두 관찰에 반복되는(91684) 문서에서 전부 놓쳤다(dry run 43072002057: 66 → 60). 자리마다 이어지는
    문장이 같으면 어느 자리든 결과가 같으므로 모호하지 않다. 다르면 종전대로 None."""
    found: set[str | None] = set()
    for h in haystacks:
        start = h.find(probe)
        while start != -1:
            found.add(_first_sentence_rest(h[start + len(probe): start + len(probe) + _SENTENCE_TAIL_MAX]))
            start = h.find(probe, start + 1)
    if len(found) != 1:
        return None
    return next(iter(found))


def _first_sentence_rest(rest: str | None) -> str | None:
    """뒤 조각에서 첫 문장 끝까지(양식 낱말이 섞이면 None)."""
    if not rest:
        return None
    m = _SENTENCE_END_RE.search(rest)
    if not m:
        return None
    added = rest[:m.end()]
    if _FORM_TRACE_RE.search(re.sub(r"\s+", "", added)):
        return None                       # 붙일 조각에 서명란·양식 낱말 — 원문 경계가 아니라 쪽 블록이다
    return added


def completed_deficiency(old_def: str, source_text: str) -> str | None:
    """[11-12] 280자+"..." 로 잘린 표제를 원문 전체에서 첫 문장 끝까지 잇는다(순수 함수). 못 하면 None.

    다시 읽은 관찰이 표제를 빈 값으로 내는 문서(실측 92522 — 새 관찰 3건 모두 표제 0자)는 짝 ② 가
    걸리지 않는다. 표제 끝 80자가 원문에 딱 한 곳이면 바로 뒤를 문장 끝까지 붙인다."""
    if not is_truncated_deficiency(old_def) or not source_text:
        return None
    head = _norm(old_def[:-len(TRUNC_DEFICIENCY_SUFFIX)])
    if len(head) < _TAIL_PROBE:
        return None
    added = _agreed_continuation(head[-_TAIL_PROBE:], [_norm(source_text)])
    return head + added if added else None


def completed_sentence(old_detail: str, new: list[dict[str, Any]], source_text: str = "") -> str | None:
    """[11-11] 옛 1,200자 절단 상세의 **끊긴 문장만** 원문으로 마저 잇는다(순수 함수). 못 하면 None.

    다시 읽은 관찰과 번호·표제가 어긋나 짝 ①~③ 이 모두 실패한 조각(옛 수집기가 하위 항목 `B.`·`G.` 를
    따로 떼어 저장한 행 — 실측 175708·173408·80115·158445)도, 그 끝 80자는 다시 읽은 원문 어딘가에
    그대로 있다. 그 자리가 **다시 읽은 관찰 전체에서 딱 한 곳**일 때만, 바로 뒤를 첫 문장 끝(`.`·`!`·`?`)
    까지 붙인다. 조각이 원래 어디서 끝났는지는 알 수 없으므로 문장 하나보다 더 붙이지 않는다(경계 불확실
    → 덜 붙인다). 문장 끝이 600자 안에 없으면 None(종전대로 둔다).

    [11-12] 다시 읽은 관찰 목록에 그 관찰 자체가 없는 문서(새 파서가 번호를 다르게 자르거나 빠뜨림 —
    실측 85303·84935·96468·120638·91684·78684·83807, 진단 run 43067755299)는 관찰 안에서 못 찾는다.
    관찰 전체에서 못 찾으면 **원문 전체**(`source_text`)에서 같은 규칙(딱 한 곳·문장 하나·양식 낱말 금지)으로 찾는다."""
    od = _norm(old_detail)
    if not is_truncated_detail(old_detail) or _COMPLETE_END_RE.search(od) or len(od) < _TAIL_PROBE:
        return None
    probe = od[-_TAIL_PROBE:]
    added = _agreed_continuation(probe, [_norm(str(n.get("detail") or "")) for n in new])
    if added is None and source_text:
        added = _agreed_continuation(probe, [_norm(source_text)])
    return od + added if added else None


def _header_leak(deficiency: str) -> bool:
    import findings_extractors as fe
    return fe._fda483_is_form_header_leak(deficiency)


def refreshed_observations(old: list[dict[str, Any]], new: list[dict[str, Any]], source_text: str = ""
                           ) -> tuple[list[dict[str, Any]], int, int]:
    """기존 관찰 목록의 detail(과 잘린 표제)을 바꾼 새 목록 + (짝 맞은 수, 짝 없는 수). 순수 함수.

    짝 ① (number, deficiency) 가 같음.
       ② 번호가 같고 옛 표제가 280자+"..." 절단, 새 표제가 그 앞부분으로 시작 → 표제도 새 것으로.
          (그때 옛 상세가 표제 문장의 나머지였다면 새 상세가 빈 값일 수 있다 — 새 것을 그대로 쓴다.)
       ③ 번호가 같고 새 상세가 옛 상세의 앞부분으로 시작하며 더 김(옛 1,200자 절단) → 상세만.
       ④ 옛 상세가 1,200자 절단 조각이고 그 끝이 다시 읽은 관찰(없으면 원문 전체) 한 곳에만 있음
          → 끊긴 문장만 잇는다.
       ⑤ 옛 표제가 쪽 머리말 혼입이고 같은 번호의 혼입 아닌 새 관찰이 하나 → 표제·상세를 새 것으로.
    짝 ②가 안 된 280자+"..." 표제는 원문 전체에서 첫 문장 끝까지 잇는다(`completed_deficiency`).
    다시 읽은 상세가 비면(①·③) 기존 상세(정리본)를 둔다."""
    by_key = {(str(o.get("number")), str(o.get("deficiency") or "")): o for o in new}
    by_num: dict[str, list[dict[str, Any]]] = {}
    for o in new:
        by_num.setdefault(str(o.get("number")), []).append(o)
    out: list[dict[str, Any]] = []
    matched = unmatched = 0
    for o in old:
        num = str(o.get("number"))
        old_def = str(o.get("deficiency") or "")
        old_detail = str(o.get("detail") or "")
        hit = by_key.get((num, old_def))
        if hit is not None and str(hit.get("detail") or "").strip():
            matched += 1
            out.append({**o, "detail": str(hit["detail"])})
            continue
        same_num = by_num.get(num, [])
        restored = [n for n in same_num if _restored_deficiency(old_def, str(n.get("deficiency") or ""))]
        if len(restored) == 1:
            matched += 1
            n = restored[0]
            out.append({**o, "deficiency": str(n["deficiency"]), "detail": str(n.get("detail") or "")})
            continue
        # ⑤ [11-26] 옛 표제가 **쪽 머리말 혼입**(수신인·주소·`CITY, STATE …`부터 시작)이고, 같은 번호의
        #    새 관찰 중 혼입이 아닌 것이 정확히 하나 → 표제를 새 것으로(수집기 수리 #1997 뒤 다시 읽기).
        #    판정은 findings 가드와 **같은 함수**다(규칙 복제 금지). 새 상세가 비면 옛 상세를 둔다.
        if _header_leak(old_def):
            clean = [n for n in same_num
                     if str(n.get("deficiency") or "").strip() and not _header_leak(str(n["deficiency"]))]
            if len(clean) == 1:
                matched += 1
                n = clean[0]
                nd = str(n.get("detail") or "")
                out.append({**o, "deficiency": str(n["deficiency"]),
                            "detail": nd if nd.strip() else tidy_old_detail(old_detail)})
                continue
        extended = [n for n in same_num if _extends_detail(old_detail, str(n.get("detail") or ""))]
        if len(extended) == 1:
            matched += 1
            out.append({**o, "detail": str(extended[0]["detail"])})
            continue
        fixed_def = completed_deficiency(old_def, source_text)
        base = {**o, "deficiency": fixed_def} if fixed_def else o
        completed = completed_sentence(old_detail, new, source_text)
        if completed is not None or fixed_def:
            matched += 1
            out.append({**base, "detail": completed if completed is not None else tidy_old_detail(old_detail)})
            continue
        unmatched += 1
        out.append({**o, "detail": tidy_old_detail(old_detail)})
    return out, matched, unmatched


_DIAG_MAX = 150


def diagnose_residual(doc: str, refreshed: list[dict[str, Any]], new: list[dict[str, Any]], text: str
                      ) -> list[dict[str, Any]]:
    """다시 읽은 뒤에도 잘림 띠(표제 280자+'...'·상세 1,200자 언저리 문장 중간 끝)에 남은 관찰마다:
    원문(공백 접음)에서 그 끝 60자가 어디 있고 **바로 뒤 300자**가 무엇인지, 같은 번호의 새 관찰이 무엇인지.
    뒤가 다음 관찰·서명란·문서 끝이면 원문 자체가 거기서 끝난 것이고, 본문이 이어지면 추출이 놓친 것이다."""
    flat = re.sub(r"\s+", " ", text or "")
    out: list[dict[str, Any]] = []
    for o in refreshed:
        d, t = str(o.get("deficiency") or ""), str(o.get("detail") or "").strip()
        kind = ("deficiency" if is_truncated_deficiency(d)
                else "detail" if is_truncated_detail(t) and not _COMPLETE_END_RE.search(t) else "")
        if not kind:
            continue
        probe = re.sub(r"\s+", " ", (d[:-3] if kind == "deficiency" else t))[-60:]
        at = flat.find(probe)
        same = [n for n in new if str(n.get("number")) == str(o.get("number"))]
        out.append({
            "doc": doc, "number": o.get("number"), "kind": kind, "len": len(d if kind == "deficiency" else t),
            "tail": probe, "found_in_text": at >= 0,
            "text_after": flat[at + len(probe): at + len(probe) + 300] if at >= 0 else "",
            "new_same_number": [{"deficiency_head": str(n.get("deficiency") or "")[:80],
                                 "detail_len": len(str(n.get("detail") or ""))} for n in same][:3],
        })
    return out[:_DIAG_MAX]


def _hints(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "establishment_type": str(raw.get("establishment_type") or ""),
        "fei_number": str(raw.get("fei_number") or ""),
        "firm_name": str(raw.get("firm") or ""),
    }


def _fetch_text(pdf_url: str) -> tuple[str, str]:
    return _collector()._fetch_fda483_pdf_text(pdf_url)


def _fetch_rows(base_url: str, service_key: str, since: str, until: str | None = None
                ) -> list[dict[str, Any]]:
    params = {"source": f"eq.{SOURCE_FDA_483}", "raw_json": f"like.*{_OBS_KEY}*"}
    if until:   # 게시일 [since, until) — 옛 문서 전체를 기간으로 나눠 돌린다(PDF·OCR 시간)
        params["and"] = f"(published_date.gte.{since},published_date.lt.{until})"
    else:
        params["published_date"] = f"gte.{since}"
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="raw_signal_id,document_id,raw_sha256,raw_json,row_json", page_size=500,
        order="raw_signal_id.asc", extra_params=params,
    )


def _patch(base_url: str, service_key: str, row: dict[str, Any], raw: dict[str, Any]) -> None:
    """[원문 갱신 규칙 R3] raw_json PATCH 대신 raw_signal_apply_version(148) 경유 — 지문·옛 판 보관."""
    rv.replace_raw(base_url, service_key, row, raw, change_kind="reextract",
                   changed_by=rv.run_label("backfill_483_detail_refresh"),
                   diff_summary={"reason": "483_detail_refresh"})


def run(
    rows: list[dict[str, Any]],
    *,
    dry_run: bool = True,
    since: str = DEFAULT_SINCE,
    limit: int | None = None,
    doc_ids: set[str] | None = None,
    truncated_only: bool = False,
    delay_seconds: float = FDA_483_PDF_DELAY_SECONDS,
    text_fn: Callable[[str], tuple[str, str]] = _fetch_text,
    patch_fn: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    resume_from: str | None = None,
    add_missing: bool = False,
) -> DetailRefreshReport:
    c = _collector()
    report = DetailRefreshReport(dry_run=dry_run, since=since, truncated_only=truncated_only,
                                 resume_from=resume_from or "")
    # 후보를 먼저 다 고른다 — 도중에 멈춰도 '처리 N/후보 M' 과 남은 문서를 정확히 낼 수 있게.
    # 순서는 raw_signal_id 오름차순(_fetch_rows 와 같음) — --resume-from 이 그 순서의 한 지점을 가리킨다.
    cands: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    for r in sorted(rows, key=lambda x: str(x.get("raw_signal_id") or "")):
        rid = str(r.get("raw_signal_id") or "")
        doc = str(r.get("document_id") or "")
        if resume_from and rid < resume_from:
            continue
        if doc_ids and doc not in doc_ids and rid not in doc_ids:
            continue
        raw = r.get("raw_json")
        raw = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        old = [o for o in (raw.get(_OBS_KEY) or []) if isinstance(o, dict)]
        if not old:
            continue
        if truncated_only and not has_truncated_observation(old):
            continue
        cands.append((r, raw, old))
    report.total_candidates = len(cands)

    def _stop(i: int, reason: str) -> None:
        r_stop = cands[i][0]
        report.incomplete = True
        report.stop_reason = reason
        report.stopped_at_raw_signal_id = str(r_stop.get("raw_signal_id") or "")
        report.stopped_at_document_id = str(r_stop.get("document_id") or "")
        report.remaining_doc_ids = [str(x[0].get("document_id") or x[0].get("raw_signal_id") or "")
                                    for x in cands[i:]]

    for i, (r, raw, old) in enumerate(cands):
        if limit and report.candidates >= limit:
            _stop(i, "limit")
            break
        rid = str(r.get("raw_signal_id") or "")
        doc = str(r.get("document_id") or "")
        report.candidates += 1
        text = str(raw.get("fda483_body_full") or "")
        if text:
            report.from_body_full += 1
        else:
            pdf_url = str(raw.get("pdf_url") or "").strip()
            if not pdf_url:
                report.no_text += 1
                continue
            sleep_fn(delay_seconds)
            try:
                text, status = text_fn(pdf_url)
            except Exception as exc:  # noqa: BLE001 — 건별 실패는 넘긴다(멱등 재실행)
                report.failed += 1
                report.errors.append(f"{doc}: fetch_raised:{type(exc).__name__}")
                continue
            if status == FDA_BOT_BLOCK:
                report.failed += 1
                report.errors.append(f"{doc}: {FDA_BOT_BLOCK}")
                report.candidates -= 1   # 이 문서는 처리되지 않았다 — 남은 문서에 넣어 다음 실행이 다시 읽는다
                _stop(i, FDA_BOT_BLOCK)
                break
            if not text:
                report.no_text += 1
                continue
            report.from_pdf += 1
        try:
            new = c._extract_483_observations_from_text(text, _hints(raw))
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f"{doc}: extract_raised:{type(exc).__name__}")
            continue
        refreshed, matched, unmatched = refreshed_observations(old, new, text)
        refreshed, blocked = guard_shrink(old, refreshed)
        report.shrink_blocked += len(blocked)
        for i in blocked:
            if len(report.shrink_samples) < _SAMPLE_MAX:
                cand = refreshed_observations([old[i]], new, text)[0][0]
                report.shrink_samples.append({
                    "doc": doc, "number": old[i].get("number"),
                    "prose_before": prose_words(_obs_text(old[i])),
                    "prose_new": prose_words(_obs_text(cand)),
                    "new_tail": str(cand.get("detail") or "")[-160:]})
        doc_pagebreak = False
        for o_old, o_new in zip(old, refreshed):
            pb, pa = prose_words(_obs_text(o_old)), prose_words(_obs_text(o_new))
            report.prose_words_before += pb
            report.prose_words_after += pa
            if not _gf().fda483_prose_grew(_obs_text(o_old), _obs_text(o_new)):
                continue                      # 판독 흔들림 수준의 차이는 복원으로 세지 않는다
            report.pagebreak_extended += 1
            doc_pagebreak = True
            added = sorted(item_heads(str(o_new.get("detail") or ""))
                           - item_heads(str(o_old.get("detail") or "")))
            if added:
                report.pagebreak_items_added += 1
            if len(report.pagebreak_samples) < _SAMPLE_MAX and (added or not report.pagebreak_samples):
                report.pagebreak_samples.append({
                    "doc": doc, "number": o_old.get("number"), "prose_before": pb, "prose_after": pa,
                    "items_added": added, "before_tail": str(o_old.get("detail") or "")[-120:],
                    "after_tail": str(o_new.get("detail") or "")[-160:]})
        report.pagebreak_docs += doc_pagebreak
        if doc_ids:
            report.diagnostics.extend(diagnose_residual(doc, refreshed, new, text))
        report.observations_matched += matched
        report.observations_unmatched += unmatched
        changed = 0
        report.truncated_before += sum(has_truncated_observation([o]) for o in old)
        report.truncated_after += sum(has_truncated_observation([o]) for o in refreshed)
        for o_old, o_new in zip(old, refreshed):
            if str(o_old.get("deficiency") or "") != str(o_new.get("deficiency") or ""):
                report.deficiency_restored += 1
            _add(report.before, str(o_old.get("detail") or ""))
            _add(report.after, str(o_new.get("detail") or ""))
            nd = str(o_new.get("detail") or "").strip()
            if nd and not detail_quality(nd)["complete"] and len(report.incomplete_samples) < 60:
                report.incomplete_samples.append({
                    "doc": doc, "number": o_old.get("number"), "len": len(nd),
                    "source": "body_full" if raw.get("fda483_body_full") else "pdf",
                    "tail": nd[-200:]})
            if (str(o_old.get("detail") or "") != o_new["detail"]
                    or str(o_old.get("deficiency") or "") != str(o_new.get("deficiency") or "")):
                changed += 1
                if len(report.samples) < 12:
                    report.samples.append({
                        "doc": doc, "number": o_old.get("number"),
                        "before_len": len(str(o_old.get("detail") or "")),
                        "after_len": len(o_new["detail"]),
                        "before_tail": str(o_old.get("detail") or "")[-120:],
                        "after_tail": o_new["detail"][-160:],
                    })
        missing = missing_observations(old, new)
        if missing:
            report.new_observations += len(missing)
            report.new_observation_docs += 1
            report.new_observation_doc_ids.append(doc)
            for o in missing:
                if len(report.new_observation_samples) < _SAMPLE_MAX:
                    report.new_observation_samples.append({
                        "doc": doc, "number": o.get("number"),
                        "deficiency": str(o.get("deficiency") or "")[:160],
                        "detail_len": len(str(o.get("detail") or ""))})
            if add_missing:
                refreshed = sorted([*refreshed, *missing], key=_num_key)
                report.new_observations_added += len(missing)
                changed += len(missing)
        if not changed:
            continue
        report.docs_changed += 1
        report.observations_changed += changed
        if dry_run or patch_fn is None:
            continue
        try:
            patch_fn(r, {**raw, _OBS_KEY: refreshed})
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f"{doc}: patch_failed:{type(exc).__name__}")
            continue
        report.patched += 1
    report.processed = report.candidates
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="저장된 FDA 483 관찰 상세만 다시 읽어 제자리 교체(기본 dry-run).")
    p.add_argument("--apply", action="store_true", default=False, help="실제 PATCH. 기본 dry-run.")
    p.add_argument("--since", default=None,
                   help=f"게시일 하한(YYYY-MM-DD). 기본 {DEFAULT_SINCE}, --truncated-only 면 기본 전체 기간.")
    p.add_argument("--until", default=None, help="게시일 상한(미포함, YYYY-MM-DD). 기간을 나눠 돌릴 때.")
    p.add_argument("--truncated-only", action="store_true", default=False,
                   help="옛 저장 상한(상세 1,200자·표제 280자+'...')에 걸린 관찰이 있는 문서만.")
    p.add_argument("--limit", type=int, default=None, help="처리 문서 수 상한.")
    p.add_argument("--doc-ids", default="", help="선택: document_id/raw_signal_id 목록(공백·쉼표).")
    p.add_argument("--delay-seconds", type=float, default=FDA_483_PDF_DELAY_SECONDS, help="PDF 요청 간 대기(초).")
    p.add_argument("--add-missing-observations", action="store_true", default=False,
                   help="다시 읽어 새로 잡힌 관찰(저장 배열에 번호 없음)을 raw 배열에 덧붙인다. 기본은 세기만.")
    p.add_argument("--resume-from", default=None,
                   help="이 raw_signal_id 부터(포함) 잇는다 — 앞 실행 보고서의 stopped_at_raw_signal_id.")
    p.add_argument("--remaining-ids-path",
                   help="도중에 멈추면 남은 document_id 를 이 파일에 한 줄씩 쓴다(다 돌면 파일을 만들지 않는다).")
    p.add_argument("--report-path", help="JSON 리포트를 이 경로에도 기록.")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)
    creds = _resolve_credentials(args)
    if creds is None:
        print("backfill_483_detail_refresh: Supabase 자격 증명 필요", file=sys.stderr)
        return 2
    base_url, service_key = creds
    base_url = _normalize_base_url(base_url)
    since = args.since or (_ALL_TIME if args.truncated_only else DEFAULT_SINCE)
    rows = _fetch_rows(base_url, service_key, since, args.until)
    ids = {x for x in re.split(r"[\s,]+", args.doc_ids or "") if x}
    report = run(
        rows, dry_run=not args.apply, since=since, limit=args.limit, doc_ids=ids or None,
        truncated_only=args.truncated_only,
        delay_seconds=args.delay_seconds,
        patch_fn=lambda row, raw: _patch(base_url, service_key, row, raw),
        resume_from=args.resume_from,
        add_missing=args.add_missing_observations,
    )
    data = report_payload(report)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    banner = incomplete_banner(data)
    if banner:
        print(banner, file=sys.stderr)
    print(payload)
    if args.report_path:
        Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
    if args.remaining_ids_path and report.incomplete:
        Path(args.remaining_ids_path).write_text("\n".join(report.remaining_doc_ids) + "\n", encoding="utf-8")
    return exit_code(report)


_HEAD_FIELDS = ("incomplete", "stop_reason", "processed", "total_candidates",
                "stopped_at_raw_signal_id", "stopped_at_document_id", "resume_from")


def report_payload(report: DetailRefreshReport) -> dict[str, Any]:
    """보고서 dict — 미완료 표시 필드를 맨 앞에 둔다(숫자만 보고 전체로 읽지 않게)."""
    data = asdict(report)
    return {**{k: data.pop(k) for k in _HEAD_FIELDS}, **data}


def exit_code(report: DetailRefreshReport) -> int:
    """실패가 있거나 봇 차단으로 멈추면 1 — 미완료 실행이 초록으로 끝나지 않게."""
    return 1 if (report.failed or report.stop_reason == FDA_BOT_BLOCK) else 0


if __name__ == "__main__":
    raise SystemExit(main())
