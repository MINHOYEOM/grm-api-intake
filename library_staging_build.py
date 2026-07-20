#!/usr/bin/env python3
"""Build review-only v2 library candidates from the library collectors.

두 갈래를 합친다.
  1) 레거시 경로 — collect_mfds / collect_ich 가 내는 IntakeItem 을 v2 필드로 환산한다.
  2) 플러그인 경로 — `library_collect_<source>.py` 모듈을 **자동 발견**해 그대로 쓴다.
     계약: 모듈 상수 LIBRARY_SOURCE = "<source>" (자료실 카탈로그 파일명과 동일)
           진입 함수 collect_library_items(run_date) -> (items, error)
     새 수집기는 파일만 추가하면 코드 수정 없이 합류한다.
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

import collect_ich
import collect_mfds

CATALOG_FIELDS = (
    "id", "code", "title_en", "title_ko", "doc_type", "published_date",
    "official_url", "ko_url", "pdf_url",
)
REQUIRED_PLUGIN_FIELDS = ("id", "title_en", "official_url")
MFDS_TYPES = {"guidance-industry", "guidance-internal", "notice-final"}
SCHEMA_VERSION = "grm-library-staging-diff/v1"
PLUGIN_PREFIX = "library_collect_"
CURATED_FIELDS = ("code", "title_en", "pdf_url", "ko_url", "doc_type")


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


def discover_collectors(root: Path | None = None) -> dict[str, ModuleType]:
    """`library_collect_*.py` 플러그인을 발견해 LIBRARY_SOURCE 기준으로 등록한다.

    계약 위반(상수·진입함수 누락, source 중복)은 **조용히 건너뛰지 않고 예외로 올린다** —
    허용목록·shim 표류로 수집기가 침묵 미실행된 전례가 있다.
    """
    base = root or Path(__file__).resolve().parent
    found: dict[str, ModuleType] = {}
    for path in sorted(base.glob(f"{PLUGIN_PREFIX}*.py")):
        module = importlib.import_module(path.stem)
        source = str(getattr(module, "LIBRARY_SOURCE", "") or "").strip()
        entry = getattr(module, "collect_library_items", None)
        if not source or not callable(entry):
            raise ValueError(
                f"{path.name}: 자료실 플러그인 계약 위반 — "
                f"LIBRARY_SOURCE 상수와 collect_library_items(run_date) 가 있어야 한다"
            )
        if source in found:
            raise ValueError(f"{path.name}: LIBRARY_SOURCE 중복 '{source}'")
        found[source] = module
    return found


def derive_plugin_item(item: Any) -> dict[str, Any] | None:
    """플러그인 반환 dict 검증 — 필수 필드가 없으면 버린다(개수는 리포트에 남는다)."""
    if not isinstance(item, dict):
        return None
    row = {key: item[key] for key in item if item.get(key) not in (None, "")}
    if any(not str(row.get(field) or "").strip() for field in REQUIRED_PLUGIN_FIELDS):
        return None
    return {key: str(value) for key, value in row.items()}


def run_collectors(
    run_date: date, *, collectors: dict[str, ModuleType],
) -> tuple[dict[str, list[dict]], dict[str, str | None]]:
    """발견된 플러그인을 순차 실행한다. 반환 (source별 items, source별 error)."""
    items: dict[str, list[dict]] = {}
    errors: dict[str, str | None] = {}
    for source, module in collectors.items():
        try:
            collected, error = module.collect_library_items(run_date)
        except Exception as exc:                       # noqa: BLE001 - 경계에서 error 로 승격
            collected, error = [], f"{source} 수집기 예외: {exc}"
        items[source] = list(collected or [])
        errors[source] = error
    return items, errors


def _load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError(f"{path}: items must be a list")
    return [dict(item) for item in items if isinstance(item, dict)]


def _public_only(item: dict[str, Any]) -> dict[str, Any]:
    unknown = set(item) - set(CATALOG_FIELDS)
    if unknown:
        raise ValueError(f"unsupported catalog fields: {', '.join(sorted(unknown))}")
    return {key: item[key] for key in CATALOG_FIELDS if item.get(key) not in (None, "")}


def merge_candidate(
    baseline: list[dict[str, Any]], derived: Iterable[dict[str, str]],
) -> list[dict[str, Any]]:
    by_id = {str(item.get("id") or ""): _public_only(item) for item in baseline}
    original_order = [str(item.get("id") or "") for item in baseline]
    new_ids: list[str] = []
    for incoming in derived:
        item_id = incoming["id"]
        current = dict(by_id.get(item_id, {}))
        if item_id in by_id:
            for key, value in _public_only(dict(incoming)).items():
                if current.get(key) in (None, ""):
                    current[key] = value
        else:
            current = _public_only(dict(incoming))
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


def assert_curation_preserved(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]], *, source: str,
) -> None:
    """Fail closed if an existing curated value is removed or overwritten."""
    current = {str(item.get("id") or ""): item for item in candidate}
    for old in baseline:
        item_id = str(old.get("id") or "")
        if item_id not in current:
            raise ValueError(f"{source}: existing item removed: {item_id}")
        for field in CURATED_FIELDS:
            old_value = old.get(field)
            if old_value not in (None, "") and current[item_id].get(field) != old_value:
                raise ValueError(f"{source}:{item_id}: curated field changed: {field}")


def evaluate_gates(
    report: dict[str, Any], *, max_change_count: int, max_change_percent: float,
) -> list[str]:
    """Return human-review reasons; an empty list permits automatic merge."""
    if max_change_count < 0 or max_change_percent < 0:
        raise ValueError("change thresholds must be non-negative")
    reasons: list[str] = []
    errors = report.get("collector_errors") or {}
    if errors:
        reasons.append("collector_errors: " + ", ".join(sorted(errors)))
    for source, detail in sorted(report["sources"].items()):
        removed = int(detail["removed_count"])
        changed = int(detail["new_count"]) + int(detail["changed_count"])
        baseline = int(detail["baseline_count"])
        percent = (changed / baseline * 100.0) if baseline else (100.0 if changed else 0.0)
        detail["change_count"] = changed
        detail["change_percent"] = round(percent, 2)
        if removed:
            reasons.append(f"{source}: removed_count={removed} (automatic deletion forbidden)")
        if changed > max_change_count:
            reasons.append(
                f"{source}: change_count={changed} exceeds max_change_count={max_change_count}"
            )
        if percent > max_change_percent:
            reasons.append(
                f"{source}: change_percent={percent:.2f}% exceeds "
                f"max_change_percent={max_change_percent:.2f}%"
            )
    return reasons


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(
    *, baseline_dir: Path, staging_dir: Path, report_path: Path,
    mfds_items: Iterable[Any] | None = None, ich_items: Iterable[Any] | None = None,
    run_date: date, collector_errors: dict[str, str | None] | None = None,
    plugin_items: dict[str, Iterable[Any]] | None = None,
) -> dict[str, Any]:
    # None = 이번 실행에서 돌리지 않은 소스 → staging/diff 에서 아예 제외한다
    # (빈 리스트로 처리하면 "수집 0건"과 구분되지 않는다).
    legacy = {source: list(rows)
              for source, rows in (("mfds", mfds_items), ("ich", ich_items))
              if rows is not None}
    plugins = {source: list(rows) for source, rows in (plugin_items or {}).items()}
    overlap = set(legacy) & set(plugins)
    if overlap:
        raise ValueError(f"레거시/플러그인 소스 충돌: {', '.join(sorted(overlap))}")
    errors = {key: value for key, value in (collector_errors or {}).items() if value}
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "generated_on": run_date.isoformat(),
        "live_catalog_swapped": False, "sources": {},
        "collector_errors": collector_errors or {},
    }
    for source, raw_items in {**legacy, **plugins}.items():
        baseline = _load_catalog(baseline_dir / f"{source}.json")
        if source in plugins:
            derived = [row for row in (derive_plugin_item(item) for item in raw_items) if row]
        else:
            derived = [row for row in (derive_item(item, source) for item in raw_items) if row]
        candidate = merge_candidate(baseline, derived)
        assert_curation_preserved(baseline, candidate, source=source)
        _write(staging_dir / f"{source}.json", {"items": candidate})
        detail = diff_catalog(baseline, candidate)
        detail["collector_items"] = len(raw_items)
        detail["derived_items"] = len(derived)
        detail["dropped_items"] = len(raw_items) - len(derived)
        detail["collector_error"] = errors.get(source)
        report["sources"][source] = detail
    _write(report_path, report)
    return report


def prepare_live_swap(
    *, baseline_dir: Path, staging_dir: Path, report_path: Path,
    max_change_count: int, max_change_percent: float,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reasons = evaluate_gates(
        report, max_change_count=max_change_count,
        max_change_percent=max_change_percent,
    )
    # A collector error must never produce or copy a partial candidate.
    if report.get("collector_errors"):
        raise ValueError("collector errors forbid live candidate preparation")
    for source in report["sources"]:
        baseline = _load_catalog(baseline_dir / f"{source}.json")
        candidate = _load_catalog(staging_dir / f"{source}.json")
        assert_curation_preserved(baseline, candidate, source=source)
    for source in report["sources"]:
        shutil.copyfile(staging_dir / f"{source}.json", baseline_dir / f"{source}.json")
    report["live_catalog_swapped"] = True
    report["gate"] = {
        "max_change_count": max_change_count,
        "max_change_percent": max_change_percent,
        "automatic_merge_allowed": not reasons,
        "review_reasons": reasons,
    }
    _write(report_path, report)
    return report


def verify_curation_against_git_ref(*, ref: str, live_dir: Path) -> None:
    """Re-run the preservation guard immediately before PR merge eligibility."""
    for path in sorted(live_dir.glob("*.json")):
        rel = path.as_posix()
        proc = subprocess.run(
            ["git", "show", f"{ref}:{rel}"], capture_output=True, text=True,
            encoding="utf-8", check=False,
        )
        if proc.returncode != 0:
            raise ValueError(f"cannot read baseline {ref}:{rel}: {proc.stderr.strip()}")
        payload = json.loads(proc.stdout.lstrip("\ufeff"))
        baseline = payload if isinstance(payload, list) else payload.get("items", [])
        assert_curation_preserved(baseline, _load_catalog(path), source=path.stem)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, default=Path("web/data/library"))
    parser.add_argument("--staging-dir", type=Path, default=Path("web/data/library_staging"))
    parser.add_argument("--report", type=Path, default=Path("web/data/library_staging_diff.json"))
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--sources", default="",
                        help="쉼표 구분 source 화이트리스트(미지정=전부)")
    parser.add_argument("--swap", action="store_true")
    parser.add_argument("--max-change-count", type=int, default=20)
    parser.add_argument("--max-change-percent", type=float, default=30.0)
    parser.add_argument("--verify-curation-ref")
    args = parser.parse_args(argv)
    if args.verify_curation_ref:
        verify_curation_against_git_ref(ref=args.verify_curation_ref, live_dir=args.baseline_dir)
        return 0
    wanted = {s.strip() for s in args.sources.split(",") if s.strip()}
    today = date.today()

    # 내장 수집기(mfds·ich) + 플러그인 수집기(library_collect_*) 를 함께 돌린다.
    # --sources 로 좁히면 제외된 소스는 None 으로 남아 build 가 "미수집"으로 건너뛴다
    # (빈 리스트로 넘기면 전량 삭제로 오인되므로 None 유지가 계약).
    errors: dict[str, str | None] = {}
    mfds_items: list[Any] | None = None
    ich_items: list[Any] | None = None
    if not wanted or "mfds" in wanted:
        mfds_items, errors["mfds"] = collect_mfds.collect_mfds(
            today - timedelta(days=args.days), today)
    if not wanted or "ich" in wanted:
        ich_items, errors["ich"] = collect_ich.collect_ich(today)

    collectors = {source: module for source, module in discover_collectors().items()
                  if not wanted or source in wanted}
    plugin_items, plugin_errors = run_collectors(today, collectors=collectors)
    errors.update(plugin_errors)

    # 수집 오류가 하나라도 있으면 후보를 만들지 않는다(부분 수집 결과로 라이브를 덮지 않는
    # 안전 게이트 -- 자동 스왑 경로의 전제).
    collector_errors = {source: error for source, error in errors.items() if error}
    if collector_errors:
        print(json.dumps({"collector_errors": collector_errors}, ensure_ascii=False, sort_keys=True))
        return 1

    report = build(
        baseline_dir=args.baseline_dir, staging_dir=args.staging_dir,
        report_path=args.report, mfds_items=mfds_items, ich_items=ich_items,
        run_date=today, collector_errors=collector_errors, plugin_items=plugin_items,
    )
    if args.swap:
        report = prepare_live_swap(
            baseline_dir=args.baseline_dir, staging_dir=args.staging_dir,
            report_path=args.report, max_change_count=args.max_change_count,
            max_change_percent=args.max_change_percent,
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
