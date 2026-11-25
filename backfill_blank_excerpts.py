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
from html import unescape
from typing import Any

import collect_intake as intake
import collect_who as who
import findings_supabase_backfill as fsb
import raw_versioning as rv
from grm_cli import normalize_supabase_url
from grm_common import SOURCE_ECA, SOURCE_EMA, SOURCE_MHRA, SOURCE_WHO

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
        try:
            raw = _object(row.get("raw_json"))
            metadata = _object(row.get("row_json") or {})
        except ValueError:
            results.append({**result, "status": "invalid_json"})
            continue

        if not _blank(raw.get(field)):
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

        if source == SOURCE_WHO:
            _, report, status = readers.whopir(url)
            # 수집기의 발췌는 길이 제한이 있으므로 백필은 구조화 전문만 저장한다.
            recovered = {field: report}
        elif source == SOURCE_MHRA:
            recovered = {field: readers.mhra(url)}
            status = "empty_body"
        else:
            recovered = {field: readers.article(url, "EMA" if source == SOURCE_EMA else "ECA")}
            status = "empty_excerpt"

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
            "complete": complete,
        })
    return results


def _read_whopir(url: str) -> tuple[str, dict[str, Any] | None, str]:
    time.sleep(who.WHOPIR_EXCERPT_DELAY_SECONDS)
    return who._fetch_whopir_detail(url)


def _extract_full_article_excerpt(html_text: str) -> str:
    """Apply the collector paragraph selection without a length cap."""
    text = html_text or ""
    text = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", text)
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", text)
    parts: list[str] = []
    for p in paras:
        t = re.sub(r"(?s)<[^>]+>", " ", p)
        t = unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def _read_article(url: str, label: str) -> str:
    time.sleep(intake.ECA_ARTICLE_EXCERPT_DELAY_SECONDS)
    try:
        resp = intake.requests.get(url, timeout=intake.ECA_ARTICLE_EXCERPT_FETCH_TIMEOUT, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
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


def _read_mhra(url: str) -> str:
    time.sleep(intake.MHRA_ALERT_BODY_DELAY_SECONDS)
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
    args = parser.parse_args(argv)
    if args.ingested_since and not _DATE_RE.match(args.ingested_since):
        parser.error("--ingested-since 는 YYYY-MM-DD")
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
            rows.extend(fsb._fetch_all_pages(
                base, key, "raw_signals",
                select="source,document_id,official_url,raw_signal_id,raw_sha256,raw_json,row_json",
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
        results = backfill(rows, Readers(_read_whopir, _read_article, _read_mhra),
                           apply=args.apply, write=write)
    summary: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = summary.setdefault(r["source"], {})
        key_ = "complete" if r.get("complete") else r["status"]
        bucket[key_] = bucket.get(key_, 0) + 1
    out = json.dumps({"mode": "apply" if args.apply else "dry_run", "sources": sources,
                      "ingested_since": args.ingested_since, "summary": summary,
                      "rows": results}, ensure_ascii=False, indent=2)
    print(out)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    return int(any(r["status"] != "excluded_unrelated" and not r.get("complete") for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
