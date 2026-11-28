#!/usr/bin/env python3
"""완전성 점검 9-3의 WHOPIR·EMA·ECA·MHRA 원문 빈칸 백필.

기본 dry-run은 현재 행 조회와 원문 판독까지 한다. --apply를 명시하면 판독한
값을 버전 이력을 남기는 기존 RPC로 저장한다. 결과는 표준 출력에 JSON으로 남긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import redirect_stdout
from dataclasses import dataclass
from typing import Any

import collect_intake as intake
import collect_who as who
import ema_document
import findings_supabase_backfill as fsb
import raw_versioning as rv
from grm_cli import normalize_supabase_url
from grm_common import SOURCE_ECA, SOURCE_EMA, SOURCE_MHRA, SOURCE_WHO
from grm_http_policy import (BACKFILL_429_RETRY_WAITS_SECONDS, BACKFILL_ARTICLE_DELAY_SECONDS,
                             BACKFILL_WHO_DELAY_SECONDS)

TARGET_FIELDS = {
    SOURCE_WHO: "whopir_report",
    SOURCE_EMA: "article_excerpt",
    SOURCE_ECA: "eca_article_excerpt",
    SOURCE_MHRA: "mhra_alert_body",
}
MHRA_ALERT_PREFIX = "https://www.gov.uk/drug-device-alerts/"
SOURCE_KEYS = {"who": SOURCE_WHO, "ema": SOURCE_EMA, "eca": SOURCE_ECA, "mhra": SOURCE_MHRA}
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


@dataclass(frozen=True)
class Readers:
    whopir: Callable[[str], tuple[str, dict[str, Any] | None, str]]
    article: Callable[[str, str], str]
    mhra: Callable[[str], str]
    # [11-25 #8] EMA 는 기사 HTML·문서 PDF 를 모두 읽는 공통 판독기(ema_document.read_ema_fulltext).
    # 일일 수집기(collect_intake._enrich_ema_article_excerpts)와 같은 함수다. 없으면 article 로 읽는다.
    ema: Callable[[str], "ema_document.EmaDocument"] | None = None


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _blank(value: Any) -> bool:
    return value is None or value == {} or value == [] or (
        isinstance(value, str) and not value.strip()
    )


def backfill(
    rows: Iterable[dict[str, Any]], readers: Readers, *,
    apply: bool = False,
    write: Callable[[dict[str, Any], dict[str, Any], list[str]], None] | None = None,
) -> list[dict[str, Any]]:
    if apply and write is None:
        raise ValueError("--apply requires a writer")
    results: list[dict[str, Any]] = []
    for row in rows:
        source = row["source"]
        field = TARGET_FIELDS.get(source)
        if field is None:
            continue
        result = {"source": source, "document_id": row.get("document_id")}
        if row.get("ingested_at"):
            result["ingested_at"] = str(row["ingested_at"])[:10]
        try:
            raw = _object(row.get("raw_json"))
            metadata = _object(row.get("row_json") or {})
        except ValueError:
            results.append({**result, "status": "invalid_json"})
            continue

        if not _blank(raw.get(field)) or (source == SOURCE_EMA and ema_document.fulltext(raw).strip()):
            continue
        urls = (row.get("official_url"), metadata.get("official_url"), raw.get("link"))
        url = next((value.strip() for value in urls if isinstance(value, str) and value.strip()), "")
        if source == SOURCE_WHO:
            document_id = str(row.get("document_id") or "")
            if raw.get("channel") != "whopir" and not document_id.startswith("who-whopir-"):
                continue
            url = str(raw.get("pdf_url") or "").strip()
        elif source == SOURCE_MHRA:
            # 블로그와 경보는 같은 source를 쓴다. 이 경로가 경보 원문을 가리킨다.
            url = next((value.strip() for value in urls
                        if isinstance(value, str) and value.strip().startswith(MHRA_ALERT_PREFIX)
                        and value.strip() != MHRA_ALERT_PREFIX), "")
            if not url and metadata.get("api_query") != intake.MHRA_ALERT_RSS_URL:
                continue
        elif source == SOURCE_EMA and metadata.get("qa_relevance") == "Unrelated":
            # 미판정은 9-3의 판독 대상에 포함한다.
            results.append({**result, "status": "excluded_unrelated"})
            continue

        if not url:
            results.append({**result, "status": "missing_url"})
            continue

        doc_info: dict[str, Any] = {}
        if source == SOURCE_EMA and readers.ema is not None:
            doc = readers.ema(url)
            # 저장 형식(분할·메타)과 저장 게이트(전문 == 판독 전문)는 공통 함수가 정한다.
            recovered = ema_document.raw_patch(doc)
            status = doc.status
            doc_info = {"format": doc.format, "pages": doc.pages, "chars": len(doc.text),
                        "document_link": ema_document.is_document_url(url), "url": url}
            if not doc.ok:
                # 실패 사유 메타만 있는 패치는 쓰지 않는다(빈칸은 감시가 사유와 함께 센다).
                results.append({**result, "status": status, **doc_info})
                continue
        elif source == SOURCE_WHO:
            _, report, status = readers.whopir(url)
            # 수집기의 발췌는 길이 제한이 있으므로 백필은 구조화 전문만 저장한다.
            recovered = {field: report}
        elif source == SOURCE_MHRA:
            recovered = {field: readers.mhra(url)}
            status = "empty_body"
        else:
            recovered = {field: readers.article(url, "EMA" if source == SOURCE_EMA else "ECA")}
            status = "empty_excerpt"

        if doc_info:
            # 본문이 빈 것은 위에서 확인했다. 옛 실패 메타(ema_document)는 새 판독 메타로 바꾼다.
            patch = dict(recovered)
        else:
            patch = {key: value for key, value in recovered.items()
                     if _blank(raw.get(key)) and not _blank(value)}
        complete = field in patch
        if not patch:
            results.append({**result, "status": status})
            continue
        fields = sorted(patch)
        if apply and write is not None:
            try:
                write(row, {**raw, **patch}, fields)
            except rv.ApplyVersionError as exc:
                results.append({**result, "status": "write_failed", "reason": exc.status})
                continue
        results.append({
            **result, "status": "applied" if apply else "would_apply", "fields": fields,
            "complete": complete, **doc_info,
        })
    return results


# ── 번호 지정 재수집 (수집 완전성 N3) ────────────────────────────────────────────
IDS_MAX = 100
_IDS_OK = frozenset({"complete", "already_filled", "excluded_unrelated"})


def parse_ids(value: str) -> list[str]:
    """쉼표·줄바꿈 구분 문서 번호(순서 유지·중복 제거). `Source::번호` 꼴이면 번호만 쓴다."""
    out: list[str] = []
    for chunk in str(value or "").replace("\n", ",").split(","):
        token = chunk.strip().rsplit("::", 1)[-1].strip()
        if token and token not in out:
            out.append(token)
    return out


def ids_filter(ids: list[str]) -> str:
    """PostgREST `in.(...)` 값. 번호는 큰따옴표로 감싸고 안의 따옴표·백슬래시는 이스케이프한다."""
    quoted = []
    for i in ids:
        escaped = i.replace("\\", "\\\\").replace('"', '\\"')
        quoted.append(f'"{escaped}"')
    return "in.(" + ",".join(quoted) + ")"


def id_report(ids: list[str], rows: list[dict[str, Any]],
              results: list[dict[str, Any]]) -> dict[str, str]:
    """지정 번호마다 결과 하나. 원문 판독 대상인데 채워지지 않았거나 DB 에 없으면 '정상'이 아니다.

    - not_found: 지정 소스의 raw_signals 에 그 번호가 없다(수집 자체가 빠짐 — 문서 대조 쪽 경로로)
    - already_filled: 행은 있고 판독 칸이 이미 차 있다(또는 판독 대상 채널이 아님) — 정상
    - complete / would_apply: 이번에 채움(dry 면 채울 수 있음)
    - 그 밖(빈 판독·URL 없음·쓰기 실패 등): 이번 실행에서 못 메움 → exit 1 로 경보
    """
    present = {str(r.get("document_id") or "") for r in rows}
    by_id: dict[str, dict[str, Any]] = {}
    for r in results:
        by_id[str(r.get("document_id") or "")] = r
    out: dict[str, str] = {}
    for i in ids:
        if i not in present:
            out[i] = "not_found"
        elif i not in by_id:
            out[i] = "already_filled"
        else:
            r = by_id[i]
            out[i] = "complete" if r.get("complete") and r["status"] == "applied" else r["status"]
    return out


def _read_whopir(url: str) -> tuple[str, dict[str, Any] | None, str]:
    time.sleep(max(who.WHOPIR_EXCERPT_DELAY_SECONDS, BACKFILL_WHO_DELAY_SECONDS))
    return who._fetch_whopir_detail(url)


# 수집기 `<p>` 선택 규칙(상한 없음)과 429 재시도는 ema_document 의 공통 함수다(규칙을 베끼지 않는다).
_extract_full_article_excerpt = ema_document.extract_html_text


def _get_article(url: str, sleeper: Callable[[float], None] | None = None) -> Any:
    """429 는 Retry-After(없으면 30·60초)만큼 쉬고 두 번까지 다시 묻는다."""
    return ema_document.get_with_retry(url, sleeper=sleeper,
                                       timeout=intake.ECA_ARTICLE_EXCERPT_FETCH_TIMEOUT)


def _read_article(url: str, label: str) -> str:
    time.sleep(max(intake.ECA_ARTICLE_EXCERPT_DELAY_SECONDS, BACKFILL_ARTICLE_DELAY_SECONDS))
    try:
        resp = _get_article(url)
        if resp.status_code == 403:
            intake.log("WARN", f"{label} 기사 본문 403 — excerpt 건너뜀(카드 그대로): {intake.truncate(url, 80)}")
            return ""
        resp.raise_for_status()
    except intake.requests.RequestException as e:
        intake.log("WARN", f"{label} 기사 본문 fetch 실패(카드 그대로): {intake.truncate(str(e), 120)}")
        return ""
    excerpt = _extract_full_article_excerpt(resp.text)
    if not excerpt:
        intake.log("INFO", f"{label} 기사 본문 <p> 텍스트 미발견 — 카드 그대로: {intake.truncate(url, 80)}")
    return excerpt


def _read_ema(url: str) -> "ema_document.EmaDocument":
    return ema_document.read_ema_fulltext(url)


def _read_mhra(url: str) -> str:
    time.sleep(max(intake.MHRA_ALERT_BODY_DELAY_SECONDS, BACKFILL_ARTICLE_DELAY_SECONDS))
    return intake._fetch_mhra_alert_body(url)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--apply", action="store_true", help="판독 결과를 DB에 저장")
    parser.add_argument("--source", action="append", choices=sorted(SOURCE_KEYS),
                        help="이 소스만(여러 번 지정 가능). 없으면 네 소스 전부")
    parser.add_argument("--ingested-since", default="",
                        help="적재일(ingested_at) 하한 YYYY-MM-DD — 완전성 점검 범위(최근 60일)만 돌릴 때")
    parser.add_argument("--output", default="", help="JSON 결과를 이 파일에도 쓴다")
    parser.add_argument("--ids", default="",
                        help=f"수집 완전성 N3: 이 문서 번호만(쉼표 구분, 최대 {IDS_MAX}). "
                             "대조·칸 점검이 결손으로 찍은 번호를 그대로 넣는다")
    args = parser.parse_args(argv)
    if args.ingested_since and not _DATE_RE.match(args.ingested_since):
        parser.error("--ingested-since 는 YYYY-MM-DD")
    ids = parse_ids(args.ids)
    if args.ids and not ids:
        parser.error("--ids 에 문서 번호가 없습니다")
    if len(ids) > IDS_MAX:
        parser.error(f"--ids 는 한 번에 {IDS_MAX}건까지입니다({len(ids)}건)")
    sources = [SOURCE_KEYS[k] for k in (args.source or [])] or list(TARGET_FIELDS)
    base = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        parser.error("SUPABASE_URL(https)과 SUPABASE_SERVICE_ROLE_KEY가 필요합니다")

    rows = []
    try:
        for source in sources:
            params = {"source": f"eq.{source}"}
            if args.ingested_since:
                params["ingested_at"] = f"gte.{args.ingested_since}"
            if ids:
                params["document_id"] = ids_filter(ids)
            rows.extend(fsb._fetch_all_pages(
                base, key, "raw_signals",
                select="source,document_id,official_url,raw_signal_id,raw_sha256,raw_json,row_json,ingested_at",
                page_size=500, order="raw_signal_id.asc", extra_params=params,
            ))
    except RuntimeError:
        print("raw_signals 조회 실패", file=sys.stderr)
        return 2

    def write(row: dict[str, Any], raw: dict[str, Any], fields: list[str]) -> None:
        rv.replace_raw(base, key, row, raw, change_kind="repair",
                       changed_by=rv.run_label("backfill_blank_excerpts"),
                       diff_summary={"changed_fields": fields})

    with redirect_stdout(sys.stderr):
        results = backfill(rows, Readers(_read_whopir, _read_article, _read_mhra, _read_ema),
                           apply=args.apply, write=write)
    summary: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = summary.setdefault(r["source"], {})
        key_ = "complete" if r.get("complete") else r["status"]
        bucket[key_] = bucket.get(key_, 0) + 1
    payload: dict[str, Any] = {"mode": "apply" if args.apply else "dry_run", "sources": sources,
                               "ingested_since": args.ingested_since, "summary": summary,
                               "rows": results}
    id_status: dict[str, str] = {}
    if ids:
        id_status = id_report(ids, rows, results)
        payload["ids"] = id_status
    out = json.dumps(payload, ensure_ascii=False, indent=2)
    print(out)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    ok_ids = _IDS_OK | ({"would_apply"} if not args.apply else set())
    if any(s not in ok_ids for s in id_status.values()):
        return 1
    return int(any(r["status"] != "excluded_unrelated" and not r.get("complete") for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
