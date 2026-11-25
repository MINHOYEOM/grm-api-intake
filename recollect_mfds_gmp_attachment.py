#!/usr/bin/env python3
"""식약처 GMP 실사 — 이미 있는 행의 첨부 본문을 download_url 로 다시 받아 채우는 재수집 CLI.

## 왜 필요한가

일일 수집기는 등록일 창 기반이고 적재는 먼저 쓴 것이 이긴다(`ignore-duplicates`). 그래서
첨부를 한 번도 못 받은 행(예: R1 이관 행 천일가스 `gmpinspect-1PvoSBbO1to`)은 일일 수집도
딥 백필(`collect_mfds_backfill`, 없는 행만 추가)도 영원히 채우지 않는다. 판정 재계산
(`backfill_mfds_gmp_assessment`)은 **저장된 본문**이 있어야 하므로 본문이 없는 행은 못 고친다.

## 어떻게 고치는가 (수집기와 같은 함수 — 사본 금지)

1. `--ids` 로 받은 문서의 현재 행을 읽는다(`source=MFDS`, `source_kind=gmp-inspection`).
2. 행의 `download_url` 이 아니라 **doc_id** 로 수집기의 `_parse_attachment` 를 그대로 부른다
   (다운로드·PDF/HWPX 텍스트·판정·지적 표·상태 전부 수집기 코드).
3. 첨부 관련 키만 `attachment_payload_fields` 로 만들어 기존 raw_json 에 덮는다. 목록 키
   (제조소·주소·등록일 등)는 그대로 둔다. 의료용 가스 제외 규칙(`_to_item`)은 통과시키지 않는다 —
   행이 이미 DB 에 있으므로 "있는 행을 채우는" 도구다.
4. 적재는 `raw_versioning.replace_raw`(RPC `raw_signal_apply_version`) 경유 — 옛 판이 보관된다.
   실사자 표(`mfds_inspection_attendees`)는 새 지문으로 다시 맞춘다(backfill_mfds_gmp_assessment 와 동형).

## 가드 (적용하지 않고 보고만 한다)

- `download-fail`·본문 없음 → 새 본문이 비면 쓰지 않는다(`no_text`).
- **본문 길이 후퇴 금지**(85637 사례) — 옛 `attachment_text` 가 있는데 새 본문이 더 짧으면 막는다.
- **판정 후퇴 금지** — 옛 판정이 unknown 이 아닌데 새 판정이 다르면 막는다(규칙이 바뀐 신호).
- 새 값이 옛 값과 같으면 `unchanged`(멱등).

## 실행 환경

★nedrug 는 **해외 IP 를 막는다** — GitHub 러너에서는 받을 수 없다. **국내 IP PC(티로)** 에서 돌린다.
  필요한 환경변수: `SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY`. 지적 표 추출을 위해
  `ENABLE_GMP_DEFICIENCY_TABLE` 을 이 CLI 가 기본으로 `true` 로 둔다(이미 설정돼 있으면 존중).
  의존: requests·PyMuPDF(fitz).

    python recollect_mfds_gmp_attachment.py --ids 1PvoSBbO1to           # dry(기본, 쓰기 없음)
    python recollect_mfds_gmp_attachment.py --ids 1PvoSBbO1to,gmpinspect-1PutNZjPRZ9 --json out.json
    python recollect_mfds_gmp_attachment.py --ids 1PvoSBbO1to --apply   # 실제 반영

종료코드: 0 정상 · 1 가드가 하나라도 걸렸거나 반영 실패 · 2 사용법/자격증명 오류.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

import mfds_inspection_attendees_store as mas
import raw_versioning as rv

import collect_mfds_gmp_inspection as g

SOURCE = "MFDS"
SOURCE_KIND = "gmp-inspection"
_ID_PREFIX = "gmpinspect-"
_TIMEOUT = 30
_MAX_IDS = 50   # 실수로 전수를 돌리지 않게 한 번에 받는 상한(문서당 1초 대기 + 45초 다운로드)


@dataclass
class Report:
    mode: str = "dry_run"
    requested: int = 0
    not_found: list[str] = field(default_factory=list)
    updated: int = 0                 # 새 값으로 바뀔 행(dry) / 바뀐 행(apply 성공 + 실패 제외)
    unchanged: int = 0
    no_text: list[str] = field(default_factory=list)
    length_regress_blocked: list[str] = field(default_factory=list)
    assess_regress_blocked: list[str] = field(default_factory=list)
    applied: int = 0
    failed: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def guard_hits(self) -> int:
        return (len(self.no_text) + len(self.length_regress_blocked)
                + len(self.assess_regress_blocked))

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "requested": self.requested, "not_found": self.not_found,
            "updated": self.updated, "unchanged": self.unchanged,
            "no_text": self.no_text,
            "length_regress_blocked": self.length_regress_blocked,
            "assess_regress_blocked": self.assess_regress_blocked,
            "applied": self.applied, "failed": self.failed,
            "rows": self.rows, "errors": self.errors[:20],
        }


def normalize_ids(raw: str) -> list[str]:
    """`1PvoSBbO1to` 와 `gmpinspect-1PvoSBbO1to` 를 모두 받아 문서번호(접두어 없음)로 정규화."""
    out: list[str] = []
    for part in (raw or "").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith(_ID_PREFIX):
            part = part[len(_ID_PREFIX):]
        if part and part not in out:
            out.append(part)
    return out


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# 새 파싱 결과로 갈아끼우는 첨부 관련 키(목록 키는 건드리지 않는다). 새 결과에 없는 키는
# 지운다 — 예: 이전엔 오류문이 있었는데 이번엔 성공이면 `attachment_parse_error` 가 남으면 안 된다.
_ATTACHMENT_KEYS = (
    "attachment_parse_status", "attachment_file_format", "attachment_bytes", "attachment_pages",
    "attachment_deficiency_assessment", "manual_review_required", "attachment_parse_error",
    "attachment_text", "attachment_deficiency_excerpt", "gmp_deficiencies",
    "gmp_deficiency_table_status",
)


def plan_recollect(row: dict[str, Any], attachment: Any) -> dict[str, Any]:
    """읽은 행 + 새로 파싱한 첨부 → 적용 계획. 순수 함수(네트워크 없음) — dry/apply 공용.

    반환 kind: no_text | length_regress | assess_regress | unchanged | update
    """
    payload = _payload(row.get("raw_json"))
    old_text = str(payload.get("attachment_text") or "")
    new_text = str(getattr(attachment, "text", "") or "")
    if not new_text.strip():
        return {"kind": "no_text", "status": str(getattr(attachment, "status", ""))}
    if old_text.strip() and len(new_text) < len(old_text):
        return {"kind": "length_regress", "old_len": len(old_text), "new_len": len(new_text)}

    old_assess = str(payload.get("attachment_deficiency_assessment") or "unknown")
    new_assess = str(getattr(attachment, "deficiency", "") or "unknown")
    if old_assess != "unknown" and new_assess != old_assess:
        return {"kind": "assess_regress", "old": old_assess, "new": new_assess}

    fields = g.attachment_payload_fields(attachment, g.attachment_manual_review(attachment))
    updated = {k: v for k, v in payload.items() if k not in _ATTACHMENT_KEYS}
    updated.update(fields)
    if updated == payload:
        return {"kind": "unchanged"}
    return {
        "kind": "update", "raw_json": updated,
        "old_len": len(old_text), "new_len": len(new_text),
        "old_assess": old_assess, "new_assess": new_assess,
        "old_status": str(payload.get("gmp_deficiency_table_status") or ""),
        "new_status": str(fields.get("gmp_deficiency_table_status") or ""),
        "table_rows": len(fields.get("gmp_deficiencies") or []),
    }


def _creds() -> tuple[str, str]:
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정")
    return base, key


def fetch_rows(base: str, key: str, doc_ids: list[str]) -> dict[str, dict[str, Any]]:
    """문서번호 → 현재 행. 서비스키는 로그·예외에 싣지 않는다."""
    wanted = ",".join(f'"{_ID_PREFIX}{d}"' for d in doc_ids)
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    params = {
        "select": "raw_signal_id,document_id,source_kind,raw_sha256,raw_json,row_json",
        "source": f"eq.{SOURCE}", "source_kind": f"eq.{SOURCE_KIND}",
        "document_id": f"in.({wanted})",
    }
    try:
        resp = requests.get(f"{base}/rest/v1/raw_signals", params=params, headers=headers,
                            timeout=_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"raw_signals 조회 실패: {type(exc).__name__}") from None
    if resp.status_code >= 400:
        raise RuntimeError(f"raw_signals 조회 HTTP {resp.status_code}")
    out: dict[str, dict[str, Any]] = {}
    for row in resp.json() or []:
        doc = str(row.get("document_id") or "")
        if doc.startswith(_ID_PREFIX):
            out[doc[len(_ID_PREFIX):]] = row
    return out


def _apply(base: str, key: str, row: dict[str, Any], payload: dict[str, Any]) -> None:
    res = rv.replace_raw(base, key, row, payload, change_kind="repair",
                         changed_by=rv.run_label("recollect_mfds_gmp_attachment"),
                         diff_summary={"reason": "mfds_gmp_attachment_recollect"})
    if res.get("status") == "applied":
        mas.sync_attendees(base, key, {
            "source": SOURCE, "source_kind": str(row.get("source_kind") or SOURCE_KIND),
            "document_id": str(row.get("document_id") or ""),
            "raw_sha256": res["new_raw_sha256"], "raw_json": res["new_raw_json"]})


def run(doc_ids: list[str], apply: bool, *,
        rows: dict[str, dict[str, Any]] | None = None,
        parse_attachment: Callable[[str], Any] | None = None,
        apply_fn: Callable[[str, str, dict[str, Any], dict[str, Any]], None] | None = None,
        creds: tuple[str, str] | None = None) -> Report:
    """주입 가능한 의존(rows·parse_attachment·apply_fn)은 시험용. 기본은 실제 DB·네트워크."""
    report = Report(mode="apply" if apply else "dry_run", requested=len(doc_ids))
    base, key = creds if creds else (("", "") if rows is not None else _creds())
    if rows is None:
        rows = fetch_rows(base, key, doc_ids)
    parse = parse_attachment or g._parse_attachment
    do_apply = apply_fn or _apply

    for doc_id in doc_ids:
        row = rows.get(doc_id)
        if row is None:
            report.not_found.append(doc_id)
            continue
        attachment = parse(doc_id)
        plan = plan_recollect(row, attachment)
        kind = plan["kind"]
        entry: dict[str, Any] = {"doc_id": doc_id, "result": kind}
        if kind == "no_text":
            report.no_text.append(doc_id)
            entry["attachment_status"] = plan.get("status", "")
        elif kind == "length_regress":
            report.length_regress_blocked.append(doc_id)
            entry.update(old_len=plan["old_len"], new_len=plan["new_len"])
        elif kind == "assess_regress":
            report.assess_regress_blocked.append(doc_id)
            entry.update(old=plan["old"], new=plan["new"])
        elif kind == "unchanged":
            report.unchanged += 1
        else:
            entry.update({k: plan[k] for k in (
                "old_len", "new_len", "old_assess", "new_assess",
                "old_status", "new_status", "table_rows")})
            report.updated += 1
            if apply:
                try:
                    do_apply(base, key, row, plan["raw_json"])
                    report.applied += 1
                except RuntimeError as exc:   # rv.ApplyVersionError 포함(conflict·not_found)
                    report.failed += 1
                    report.errors.append(f"{doc_id}: {exc}")
        report.rows.append(entry)
    return report


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        description="식약처 GMP 실사 행의 첨부 본문 재수집(국내 IP PC 전용, 기본 dry-run)")
    ap.add_argument("--ids", required=True,
                    help="문서번호(쉼표 구분). 예: 1PvoSBbO1to 또는 gmpinspect-1PvoSBbO1to")
    ap.add_argument("--apply", action="store_true", help="실제 반영(기본은 dry-run)")
    ap.add_argument("--json", default=None, help="리포트 JSON 저장 경로")
    args = ap.parse_args(argv)

    doc_ids = normalize_ids(args.ids)
    if not doc_ids:
        print("--ids 에 문서번호가 없습니다", file=sys.stderr)
        return 2
    if len(doc_ids) > _MAX_IDS:
        print(f"--ids 는 한 번에 {_MAX_IDS}건까지입니다({len(doc_ids)}건)", file=sys.stderr)
        return 2
    os.environ.setdefault("ENABLE_GMP_DEFICIENCY_TABLE", "true")

    report = run(doc_ids, apply=args.apply)
    data = report.as_dict()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    return 1 if (report.guard_hits or report.failed or report.not_found) else 0


if __name__ == "__main__":
    sys.exit(main())
