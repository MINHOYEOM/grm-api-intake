#!/usr/bin/env python3
"""Build review-only v2 library candidates from MFDS and ICH collectors."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import collect_ich
import collect_mfds

CATALOG_FIELDS = (
    "id", "title_en", "title_ko", "doc_type", "published_date",
    "official_url", "ko_url", "pdf_url",
)
MFDS_TYPES = {"guidance-industry", "guidance-internal", "notice-final"}
SCHEMA_VERSION = "grm-library-staging-diff/v1"


def _value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def derive_item(item: Any, source: str) -> dict[str, str] | None:
    document_id = str(_value(item, "document_id") or "").strip()
    headline = str(_value(item, "headline") or "").strip()
    official_url = str(_value(item, "official_url") or "").strip()
    doc_type = str(_value(item, "type_or_class") or "").strip()
    if not document_id or not headline or not official_url:
        return None
    if source == "mfds" and doc_type not in MFDS_TYPES:
        return None
    out: dict[str, str] = {
        "id": document_id,
        "title_en": headline,
        "doc_type": doc_type,
        "official_url": official_url,
    }
    if source == "mfds":
        # The collector has Korean source titles and no translation service. v2 requires
        # title_en, so new rows use the source title as a lossless fallback and also
        # expose the semantically correct title_ko. Existing curated English titles are
        # preserved by merge_candidate below.
        out["title_ko"] = headline
    published = str(_value(item, "date_iso") or "").strip()
    if published:
        out["published_date"] = published
    raw = _value(item, "raw_payload", {})
    if isinstance(raw, dict):
        for source_key, target_key in (("pdf_url", "pdf_url"), ("ko_url", "ko_url")):
            value = str(raw.get(source_key) or "").strip()
            if value:
                out[target_key] = value
    return out


def _load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError(f"{path}: items must be a list")
    return [dict(item) for item in items if isinstance(item, dict)]


def _public_only(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in CATALOG_FIELDS if item.get(key) not in (None, "")}


def merge_candidate(
    baseline: list[dict[str, Any]], derived: Iterable[dict[str, str]], *, source: str,
) -> list[dict[str, Any]]:
    by_id = {str(item.get("id") or ""): _public_only(item) for item in baseline}
    original_order = [str(item.get("id") or "") for item in baseline]
    new_ids: list[str] = []
    for incoming in derived:
        item_id = incoming["id"]
        current = dict(by_id.get(item_id, {}))
        update = dict(incoming)
        if (source == "mfds" and item_id in by_id
                and update.get("title_en") == update.get("title_ko")
                and current.get("title_en")):
            update.pop("title_en", None)
        current.update(update)
        by_id[item_id] = _public_only(current)
        if item_id not in original_order:
            new_ids.append(item_id)
    ordered = [by_id[item_id] for item_id in original_order if item_id in by_id]
    ordered.extend(
        sorted((by_id[item_id] for item_id in new_ids),
               key=lambda item: (str(item.get("published_date") or ""), item["id"]),
               reverse=True)
    )
    return ordered


def diff_catalog(baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    old = {str(item.get("id") or ""): _public_only(item) for item in baseline}
    new = {str(item.get("id") or ""): _public_only(item) for item in candidate}
    new_ids = sorted(set(new) - set(old))
    removed_ids = sorted(set(old) - set(new))
    changed_ids = sorted(item_id for item_id in set(old) & set(new) if old[item_id] != new[item_id])
    return {
        "baseline_count": len(old), "candidate_count": len(new),
        "new_count": len(new_ids), "changed_count": len(changed_ids),
        "removed_count": len(removed_ids),
        "new_ids": new_ids, "changed_ids": changed_ids, "removed_ids": removed_ids,
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(
    *, baseline_dir: Path, staging_dir: Path, report_path: Path,
    mfds_items: Iterable[Any], ich_items: Iterable[Any], run_date: date,
    collector_errors: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    source_inputs = {"mfds": list(mfds_items), "ich": list(ich_items)}
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "generated_on": run_date.isoformat(),
        "live_catalog_swapped": False, "sources": {},
        "collector_errors": collector_errors or {},
    }
    for source, raw_items in source_inputs.items():
        baseline = _load_catalog(baseline_dir / f"{source}.json")
        derived = [row for row in (derive_item(item, source) for item in raw_items) if row]
        candidate = merge_candidate(baseline, derived, source=source)
        _write(staging_dir / f"{source}.json", {"items": candidate})
        detail = diff_catalog(baseline, candidate)
        detail["collector_items"] = len(raw_items)
        detail["derived_items"] = len(derived)
        report["sources"][source] = detail
    _write(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, default=Path("web/data/library"))
    parser.add_argument("--staging-dir", type=Path, default=Path("web/data/library_staging"))
    parser.add_argument("--report", type=Path, default=Path("web/data/library_staging_diff.json"))
    parser.add_argument("--days", type=int, default=120)
    args = parser.parse_args(argv)
    today = date.today()
    mfds_items, mfds_error = collect_mfds.collect_mfds(today - timedelta(days=args.days), today)
    ich_items, ich_error = collect_ich.collect_ich(today)
    report = build(
        baseline_dir=args.baseline_dir, staging_dir=args.staging_dir,
        report_path=args.report, mfds_items=mfds_items, ich_items=ich_items,
        run_date=today, collector_errors={"mfds": mfds_error, "ich": ich_error},
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if mfds_error or ich_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
