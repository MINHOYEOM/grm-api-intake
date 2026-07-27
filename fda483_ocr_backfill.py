#!/usr/bin/env python3
"""스캔 483 OCR 소급 복구 — 디제스트로 접혀 나간 483 의 관찰 원문을 되살린다.

## 왜 필요한가

FOIA 열람실 483 의 대다수는 스캔 이미지인데, 그 스캔본에도 마지막 장에는 FDA 정형 고지문이
텍스트로 들어 있다. 문서 단위 판정("텍스트가 하나라도 있으면 정상")이 이 한 장 때문에
스캔본을 정상 텍스트 PDF 로 오분류했고, 관찰 0건 → `fda483_body_full` 미보존 → deep 델타에
`source_text` 없음 → `assemble_publish_brief._refresh_483_observations` 의 조립 시점 재추출도
불가 → 디제스트가 "원문이 제공되지 않아"로 발행했다. **원문은 공개돼 있었다.**

수집기는 이제 OCR 폴백을 갖췄지만(`collect_fda_483._ocr_483_pdf_text`), **이미 발행된 주는
재수집으로 못 고친다** — 스캐폴드는 Notion 의 New 행에서만 생성되고 그 행들은 Routine 이 이미
소비했다. 남는 길은 조립 시점 재추출인데, 그건 deep 델타의 `source_text` 를 입력으로 쓴다.
이 스크립트가 **바로 그 `source_text` 를 OCR 로 만들어 준다**.

## 무엇을 하는가

대상 문서(=그 주 스캐폴드에는 있는데 발행본에서 사라진 `fda483-*` 카드)마다:
  1. 공개 PDF 를 받아 `collect_fda_483._fetch_fda483_pdf_text` 로 텍스트 확보(OCR 폴백 포함)
  2. `_extract_483_observations_from_text` 로 관찰을 추출해 **복구 가능 여부를 실측**
  3. 관찰이 1건 이상인 문서만 deep 델타 패치(`{doc_id: {"source_text": ...}}`)에 싣는다

관찰 0건 문서는 싣지 않는다 — 조립이 그 카드를 디제스트에 그대로 두게 해야 정직하다
(빈 블록을 만들면 "상세 있음"으로 오인된다).

## 산출물

  · `--out-deep-patch`  : 조립에 먹일 deep 델타 패치(`source_text` 만)
  · `--out-report`      : 문서별 관찰 목록 + 상태(번역 작업 지시서 겸 감사 기록)

번역(`observations_ko`)은 이 스크립트가 만들지 않는다 — LLM 층이고, 발행 게이트
(`render.validate_483_observations`)가 전건 필수로 요구하므로 사람/Routine 이 채운다.

## 사용

    python fda483_ocr_backfill.py \
        --scaffold artifacts/brief_web_2026_07_20.json \
        --published web/data/briefs/brief_web_2026_07_20.json \
        --out-deep-patch patch_0720.json --out-report report_0720.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import collect_fda_483 as f483
from grm_common import log

# fda.gov robots Crawl-Delay 는 30s 지만 이 경로는 daily 수집기와 같은 /media/<id>/download
# 이고 수집기 자체가 0.5s 로 운영돼 왔다(동일 예의 수준 유지). 상향은 --delay 로.
DEFAULT_DELAY_SECONDS = 0.5


def folded_483_ids(scaffold: dict[str, Any], published: dict[str, Any]) -> list[str]:
    """그 주 관찰 원문을 확보하지 못한 `fda483-*` 카드 id — 디제스트 멤버 **+ 대표**.

    순서는 스캐폴드 순서 보존(결정론).

    ★ 대표 카드도 대상이다(2026-07-27 수리). 디제스트는 대표 1장만 발행본에 남기고 나머지를
    드롭하는데, **그 대표 역시 관찰 원문이 없어서 접힌 카드**다. "발행본에 없는 것"만 고르면
    대표가 통째로 빠지고, 나머지 멤버가 전부 복구돼 디제스트가 해체되는 순간 대표만 낡은
    슬롯("구체적 관찰 사유: 원문 미기재")을 단 채 **단독 카드로 발행된다** — 07-12 소급
    복구에서 실제로 그렇게 됐다(fda483-193530). 대표는 `merged_count > 1` 로 식별한다.
    """
    digest_reps = {str(c.get("id")) for c in (published.get("cards") or [])
                   if str(c.get("id", "")).startswith("fda483-")
                   and int(c.get("merged_count") or 1) > 1}
    live = {str(c.get("id")) for c in (published.get("cards") or [])} - digest_reps
    out: list[str] = []
    for card in (scaffold.get("cards") or []):
        cid = str(card.get("id") or "")
        if cid.startswith("fda483-") and cid not in live and cid not in out:
            out.append(cid)
    return out


def _media_id(doc_id: str) -> str:
    return doc_id.split("fda483-", 1)[1] if doc_id.startswith("fda483-") else doc_id


def recover_document(doc_id: str) -> dict[str, Any]:
    """문서 1건 복구 시도 → {doc_id, status, text_len, observations[...]}.

    실패는 예외로 올리지 않는다 — 상태 문자열로 남긴다(한 건 실패가 배치를 멈추지 않게).
    """
    url = f483._pdf_url(_media_id(doc_id))
    if not url:
        return {"doc_id": doc_id, "status": "no-url", "observations": []}
    text, status = f483._fetch_fda483_pdf_text(url)
    observations = f483._extract_483_observations_from_text(text) if text else []
    return {
        "doc_id": doc_id,
        "pdf_url": url,
        "status": status,
        "text_len": len(text or ""),
        "observation_count": len(observations),
        "observations": observations,
        "source_text": text or "",
    }


def run(doc_ids: list[str], delay: float = DEFAULT_DELAY_SECONDS,
        sleeper=time.sleep) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """대상 전건 복구 → (deep 델타 패치, 보고서 행 목록). 순수하게 순서 보존."""
    patch: dict[str, Any] = {}
    report: list[dict[str, Any]] = []
    for i, doc_id in enumerate(doc_ids):
        if i and delay:
            sleeper(delay)
        row = recover_document(doc_id)
        if row["observation_count"]:
            # 조립(`_refresh_483_observations`)이 읽는 입력. 관찰은 조립이 같은 파서로 다시
            # 뽑으므로 패치에 싣지 않는다(단일 진실 = source_text). `source_text_status` 는
            # 이 영문이 **원문 텍스트층**인지 **우리 OCR 판독**인지의 출처 표기용 — 판독물을
            # "원문"이라고 표시하지 않기 위해 조립까지 물려준다.
            patch[doc_id] = {"source_text": row["source_text"],
                             "source_text_status": row["status"]}
        report.append({k: v for k, v in row.items() if k != "source_text"})
        log("INFO", f"[{i + 1}/{len(doc_ids)}] {doc_id}: status={row['status']} "
                    f"text={row['text_len']} 관찰={row['observation_count']}")
    return patch, report


def _summarize(report: list[dict[str, Any]]) -> str:
    recovered = [r for r in report if r["observation_count"]]
    obs_total = sum(r["observation_count"] for r in recovered)
    by_status: dict[str, int] = {}
    for r in report:
        token = str(r["status"]).split(":", 1)[0]
        by_status[token] = by_status.get(token, 0) + 1
    return (f"대상 {len(report)}건 · 복구 {len(recovered)}건 · 관찰 총 {obs_total}건 · "
            f"미복구 {len(report) - len(recovered)}건 · status={by_status}")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="스캔 483 OCR 소급 복구")
    ap.add_argument("--scaffold", help="그 주 빈슬롯 스캐폴드 JSON(artifact)")
    ap.add_argument("--published", help="그 주 발행본 JSON(web/data/briefs/…)")
    ap.add_argument("--doc-ids", default="",
                    help="대상 문서 id 직접 지정(공백/쉼표 구분) — scaffold/published 대체")
    ap.add_argument("--out-deep-patch", required=True)
    ap.add_argument("--out-report", required=True)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    ap.add_argument("--limit", type=int, default=0, help="상한(0=전건) — 배치 분할용")
    args = ap.parse_args(argv)

    if args.doc_ids.strip():
        ids = [t for t in args.doc_ids.replace(",", " ").split() if t]
    else:
        if not (args.scaffold and args.published):
            ap.error("--doc-ids 또는 (--scaffold + --published) 중 하나는 필요하다")
        with open(args.scaffold, encoding="utf-8") as fh:
            scaffold = json.load(fh)
        with open(args.published, encoding="utf-8") as fh:
            published = json.load(fh)
        ids = folded_483_ids(scaffold, published)
    if args.limit:
        ids = ids[:args.limit]
    if not ids:
        log("WARN", "대상 문서 0건 — 할 일 없음")

    log("INFO", f"스캔 483 OCR 소급 복구 시작: 대상 {len(ids)}건 (OCR "
                f"{'on' if f483._ocr_enabled() else 'OFF — 복구 못 함'})")
    patch, report = run(ids, delay=args.delay)
    summary = _summarize(report)
    log("INFO", summary)

    for path, payload in ((args.out_deep_patch, patch),
                          (args.out_report, {"summary": summary, "documents": report})):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
        log("INFO", f"작성: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
