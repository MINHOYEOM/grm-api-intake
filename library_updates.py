#!/usr/bin/env python3
"""자료실 변경 이력(누적) — 주간 자동 갱신이 무엇을 바꿨는지 사이트에 보여주기 위한 로그.

`library_staging_diff.json` 은 **매 실행 덮어써지는 산출물**이라 "지난주에 뭐가 들어왔나"를
답할 수 없다. 이 모듈은 그 diff 를 읽어 `web/data/library_updates.json` 에 **누적**한다.

저장 형태 (grm-library-updates/v1):
    {"schema_version": ..., "entries": [ {최신}, {그 이전}, ... ]}   # 최신 우선 정렬
    entry = {"date": "YYYY-MM-DD",
             "sources": {"<source>": {"new_ids": [...], "changed_ids": [...],
                                      "removed_ids": [...], "total_count": N,
                                      "truncated": false}}}

설계 규칙:
  · **id 만 저장한다.** 제목·링크는 라이브 카탈로그(web/data/library/*.json)에서 렌더
    시점에 join 한다 — 표시 카피의 단일 출처는 카탈로그이고, 여기에 제목을 복제하면
    나중에 큐레이션으로 제목을 고쳐도 이력만 옛 제목으로 남는다.
  · **변경이 0이면 항목을 만들지 않는다** — "이번 주 변경 없음"은 항목 부재로 표현한다.
  · **같은 날짜 재실행은 합집합으로 멱등** — 수동 dispatch 가 정기 실행과 같은 날 겹쳐도
    항목이 두 개 생기지 않고, 재실행 결과가 같으면 파일도 그대로다.
  · **상한을 넘긴 절삭은 truncated 로 남긴다** — 조용히 잘라내면 "전부 보여준 것"으로 읽힌다.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "grm-library-updates/v1"
DEFAULT_UPDATES_PATH = Path("web/data/library_updates.json")
DEFAULT_REPORT_PATH = Path("web/data/library_staging_diff.json")
MAX_ENTRIES = 52                 # 주 1회 기준 약 1년치
MAX_IDS_PER_BUCKET = 50          # 항목별 id 상한(초과분은 truncated 로 표시)
BUCKETS = ("new_ids", "changed_ids", "removed_ids")


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "entries": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"{path}: entries must be a list")
    return {"schema_version": payload.get("schema_version", SCHEMA_VERSION),
            "entries": [dict(entry) for entry in entries if isinstance(entry, dict)]}


def build_entry(report: dict[str, Any]) -> dict[str, Any] | None:
    """staging diff 리포트 → 이력 항목 1건. 변경이 하나도 없으면 None."""
    sources: dict[str, Any] = {}
    for source, detail in sorted((report.get("sources") or {}).items()):
        buckets = {name: sorted(str(i) for i in (detail.get(name) or []))
                   for name in BUCKETS}
        if not any(buckets.values()):
            continue
        truncated = any(len(ids) > MAX_IDS_PER_BUCKET for ids in buckets.values())
        sources[source] = {
            **{name: ids[:MAX_IDS_PER_BUCKET] for name, ids in buckets.items()},
            "total_count": int(detail.get("candidate_count") or 0),
            "truncated": truncated,
        }
    if not sources:
        return None
    return {"date": str(report.get("generated_on") or ""), "sources": sources}


def _merge_source(previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """같은 날짜 재실행 병합 — id 는 합집합, 개수는 최신값."""
    merged: dict[str, Any] = {}
    truncated = bool(previous.get("truncated")) or bool(incoming.get("truncated"))
    for name in BUCKETS:
        union = sorted(set(previous.get(name) or []) | set(incoming.get(name) or []))
        truncated = truncated or len(union) > MAX_IDS_PER_BUCKET
        merged[name] = union[:MAX_IDS_PER_BUCKET]
    merged["total_count"] = int(incoming.get("total_count") or 0)
    merged["truncated"] = truncated
    return merged


def merge_entry(entries: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """항목을 이력에 넣는다 — 같은 날짜가 있으면 합집합 병합, 없으면 새로 추가."""
    kept = [dict(existing) for existing in entries]
    for existing in kept:
        if existing.get("date") == entry["date"]:
            sources = dict(existing.get("sources") or {})
            for source, detail in entry["sources"].items():
                sources[source] = (_merge_source(sources[source], detail)
                                   if source in sources else detail)
            existing["sources"] = sources
            break
    else:
        kept.append(entry)
    kept.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return kept[:MAX_ENTRIES]


def append_update(
    *, report_path: Path = DEFAULT_REPORT_PATH,
    updates_path: Path = DEFAULT_UPDATES_PATH,
) -> dict[str, Any] | None:
    """diff 리포트를 읽어 이력 파일에 반영한다. 반환 = 기록한 항목(변경 0이면 None)."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    entry = build_entry(report)
    if entry is None:
        return None
    payload = _load(updates_path)
    payload["schema_version"] = SCHEMA_VERSION
    payload["entries"] = merge_entry(payload["entries"], entry)
    updates_path.parent.mkdir(parents=True, exist_ok=True)
    updates_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--updates", type=Path, default=DEFAULT_UPDATES_PATH)
    args = parser.parse_args(argv)
    entry = append_update(report_path=args.report, updates_path=args.updates)
    print(json.dumps({"appended": entry is not None, "entry": entry,
                      "generated_on": entry["date"] if entry else str(date.today())},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
