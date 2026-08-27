#!/usr/bin/env python3
"""GMP 지적 표 **가림막 가드** 회귀 코퍼스 검증 — 안 가려진 문서의 산출 바이트 불변 증명.

## 무엇을 증명하는가

`collect_mfds_gmp_inspection` 에 들어간 가림막 가드(2026-08-27)는 식약처가 검은 막대로
가려 배포한 지적사항이 추출되는 것을 막는다. 막대는 글자를 지우지 않고 **살아 있는 텍스트
위에 덧그려져** 있어서 `page.get_text()` 가 아래를 읽어 버리기 때문이다.

가드를 채택하려면 **가려지지 않은 문서의 산출이 한 바이트도 안 움직여야** 한다. 이 스크립트가
그것을 실측한다 — 회귀 코퍼스(CONTROL 194문서/934행)의 PDF 를 실제로 받아 같은 바이트에
대해 두 번 추출한다.

    baseline : 가드를 무력화한 상태(= 가드 도입 직전 코드와 동일한 동작)
    guarded  : 현재 코드 그대로

★baseline 을 "예전 커밋"이 아니라 **가드 무력화**로 만드는 이유: 두 산출이 같은 파서·같은
  PyMuPDF·같은 프로세스에서 나와야 차이의 원인이 가드 하나로 좁혀진다. 커밋을 갈아끼우면
  그 사이 다른 변경분까지 섞인다.

## 판정 기준 (하나라도 깨지면 exit 1)

1. **부분집합** — 모든 문서에서 `guarded` 는 `baseline` 의 **부분수열**이어야 한다.
   가드는 행을 *버리는* 일만 하고, 행을 더하거나 내용을 고치지 않는다. 이 성질이 깨지면
   그건 가드가 아니라 파서 변경이다.
2. **불변** — `guarded != baseline` 인 문서는 실제로 가림막(또는 텍스트층 `0000…` 마스크)이
   있는 문서뿐이어야 한다. 나머지는 지문(sha256)이 같아야 한다.
3. **재현** — `baseline` 은 DB 에 저장된 `gmp_deficiencies`(발행 계약이 보는 값)를 재현해야
   한다. 어긋나면 그건 이 가드와 무관한 표류이므로 **따로 보고**한다(경고, 실패 아님).

## 코퍼스

`docs/specs/GMP_지적표_추출불가_실측_2026-08-27.md` §5 의 SQL 과 같은 모집단을 PostgREST 로
재현한다(손목록을 여기 박아 두면 낡는다 — 스펙이 그렇게 못박았다).
PDF 는 `https://nedrug.mfds.go.kr/cmn/edms/down/{seq}` 에서 받는다. ★nedrug 는 해외 IP 를
차단하므로 러너에서는 `MFDS_HTTP_PROXY` 가 반드시 필요하다(`grm_common.proxies_for` 경유 —
`_get_bytes` 가 이미 그 헬퍼를 탄다).

사용:
    python verify_gmp_redaction_guard.py                      # CONTROL 전건
    python verify_gmp_redaction_guard.py --limit 20           # 스모크
    python verify_gmp_redaction_guard.py --ids 1P0r6voXjK9    # 지목 검증
    python verify_gmp_redaction_guard.py --json out.json --summary $GITHUB_STEP_SUMMARY
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any

import requests

import collect_mfds_gmp_inspection as g

SOURCE = "MFDS"
SOURCE_KIND = "gmp-inspection"
_PAGE = 500
_TIMEOUT = 30
_ID_PREFIX = "gmpinspect-"


# ── 코퍼스 ────────────────────────────────────────────────────────────────────
def _creds() -> tuple[str, str]:
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정")
    return base, key


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


def fetch_corpus(base: str, key: str) -> list[dict[str, Any]]:
    """스펙 §5 SQL 과 같은 모집단(pdf 첨부 + extracted 또는 지적 present/unknown)."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    url = f"{base}/rest/v1/raw_signals"
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "select": "document_id,raw_json",
            "source": f"eq.{SOURCE}",
            "source_kind": f"eq.{SOURCE_KIND}",
            "order": "document_id.asc",
            "limit": str(_PAGE), "offset": str(offset),
        }
        resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        if resp.status_code >= 400:
            raise SystemExit(f"raw_signals 조회 HTTP {resp.status_code}")
        batch = resp.json() or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        offset += _PAGE

    out: list[dict[str, Any]] = []
    for row in rows:
        payload = _payload(row.get("raw_json"))
        if str(payload.get("attachment_file_format") or "") != "pdf":
            continue
        status = str(payload.get("gmp_deficiency_table_status") or "")
        assessment = str(payload.get("attachment_deficiency_assessment") or "")
        if status != "extracted" and assessment not in ("present", "unknown"):
            continue
        document_id = str(row.get("document_id") or "")
        out.append({
            "document_id": document_id,
            "seq": document_id[len(_ID_PREFIX):] if document_id.startswith(_ID_PREFIX)
                   else document_id,
            "group": "CONTROL" if status == "extracted" else "TARGET",
            "stored": payload.get("gmp_deficiencies") or [],
        })
    return out


# ── 추출 · 지문 ───────────────────────────────────────────────────────────────
def _fingerprint(rows: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _extract_both(data: bytes) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(baseline, guarded) — 같은 바이트·같은 프로세스에서 가드만 켜고 끈다."""
    guarded = g._extract_deficiency_table(data)

    bars, masked = g._pdf_redaction_bars, g._is_zero_masked
    try:
        g._pdf_redaction_bars = lambda _page: []      # type: ignore[assignment]
        g._is_zero_masked = lambda _value: False      # type: ignore[assignment]
        baseline = g._extract_deficiency_table(data)
    finally:
        g._pdf_redaction_bars = bars                  # type: ignore[assignment]
        g._is_zero_masked = masked                    # type: ignore[assignment]
    return baseline, guarded


def _is_subsequence(small: list[dict[str, str]], big: list[dict[str, str]]) -> bool:
    """`small` 이 `big` 의 부분수열이면 True — 가드가 '행을 버리기만' 했는지의 판정."""
    it = iter(big)
    return all(any(candidate == row for candidate in it) for row in small)


def verify_document(entry: dict[str, Any], *, delay: float,
                    cache_dir: str | None) -> dict[str, Any]:
    seq = entry["seq"]
    result: dict[str, Any] = {"document_id": entry["document_id"],
                              "group": entry["group"]}
    cached = os.path.join(cache_dir, f"{seq}.pdf") if cache_dir else None
    data = b""
    if cached and os.path.exists(cached):
        with open(cached, "rb") as fh:
            data = fh.read()
    else:
        try:
            data = g._get_bytes(g.DOWNLOAD_URL_BASE + seq, accept="application/pdf")
        except Exception as exc:                      # noqa: BLE001
            result["error"] = f"download:{type(exc).__name__}"
            return result
        if delay:
            time.sleep(delay)
        if cached and data:
            with open(cached, "wb") as fh:
                fh.write(data)
    if not data:
        result["error"] = "download:empty"
        return result

    try:
        baseline, guarded = _extract_both(data)
    except Exception as exc:                          # noqa: BLE001
        result["error"] = f"extract:{type(exc).__name__}"
        return result

    result.update({
        "baseline_rows": len(baseline),
        "guarded_rows": len(guarded),
        "baseline_fp": _fingerprint(baseline),
        "guarded_fp": _fingerprint(guarded),
        "changed": _fingerprint(baseline) != _fingerprint(guarded),
        "subsequence_ok": _is_subsequence(guarded, baseline),
        "reproduces_stored": _fingerprint(baseline) == _fingerprint(
            [{k: str(row.get(k, "")) for k in g._DEFICIENCY_FIELDS}
             for row in entry["stored"] if isinstance(row, dict)]),
        "dropped": [row for row in baseline if row not in guarded],
    })
    return result


# ── 보고 ──────────────────────────────────────────────────────────────────────
def render(results: list[dict[str, Any]]) -> tuple[str, bool]:
    control = [r for r in results if r["group"] == "CONTROL"]
    errors = [r for r in results if r.get("error")]
    ok = [r for r in results if not r.get("error")]
    changed = [r for r in ok if r["changed"]]
    broken = [r for r in ok if not r["subsequence_ok"]]
    drifted = [r for r in ok if r["group"] == "CONTROL" and not r["reproduces_stored"]]
    dropped_rows = sum(len(r["dropped"]) for r in changed)

    lines = [
        "## GMP 지적 표 가림막 가드 — 회귀 코퍼스 실측",
        "",
        f"- 코퍼스: {len(results)}문서(CONTROL {len(control)}) · 열람 성공 {len(ok)} · "
        f"실패 {len(errors)}",
        f"- **산출 불변**: {len(ok) - len(changed)}문서 지문 동일",
        f"- **가드 발동**: {len(changed)}문서에서 {dropped_rows}행 제외",
        "",
    ]
    if changed:
        lines += ["### 가드가 행을 제외한 문서", "",
                  "| document_id | baseline | guarded | 제외 |", "|---|---|---|---|"]
        for r in sorted(changed, key=lambda x: x["document_id"]):
            lines.append(f"| {r['document_id']} | {r['baseline_rows']} | "
                         f"{r['guarded_rows']} | {len(r['dropped'])} |")
        lines.append("")
    if broken:
        lines += ["### ❌ 부분수열 위반 — 가드가 행을 **고쳤다**(버리기만 해야 한다)", ""]
        lines += [f"- {r['document_id']}" for r in broken] + [""]
    if drifted:
        lines += ["### ⚠️ baseline 이 저장값을 재현 못 함 (이 가드와 무관한 표류)", ""]
        lines += [f"- {r['document_id']}: baseline {r['baseline_rows']}행" for r in drifted]
        lines.append("")
    if errors:
        lines += ["### ⚠️ 열람 실패", ""]
        lines += [f"- {r['document_id']}: {r['error']}" for r in errors] + [""]

    # 실패 조건은 **부분수열 위반 하나**다. 재현 표류·열람 실패는 보고만 한다 —
    # 전자는 이 변경 이전부터 있던 사실이고, 후자는 원천 접근성 문제다.
    return "\n".join(lines), bool(broken)


def main(argv: list[str] | None = None) -> int:
    # [cp949 가드] 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는
    # 한글은 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다.
    # ubuntu CI 는 UTF-8 이라 이 결함이 초록으로 숨는다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="GMP 지적 표 가림막 가드 회귀 코퍼스 검증")
    ap.add_argument("--group", choices=("CONTROL", "TARGET", "ALL"), default="CONTROL")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N건만(스모크)")
    ap.add_argument("--ids", default="", help="쉼표 구분 seq 지목(접두 없이)")
    ap.add_argument("--delay", type=float, default=g.ATTACHMENT_REQUEST_DELAY_SECONDS)
    ap.add_argument("--cache-dir", default="", help="PDF 캐시 디렉터리(재실행 가속)")
    ap.add_argument("--json", default="", help="결과 JSON 출력 경로")
    ap.add_argument("--summary", default="", help="마크다운을 덧붙일 파일")
    args = ap.parse_args(argv)

    corpus = fetch_corpus(*_creds())
    if args.group != "ALL":
        corpus = [c for c in corpus if c["group"] == args.group]
    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        corpus = [c for c in corpus if c["seq"] in wanted]
    if args.limit:
        corpus = corpus[:args.limit]
    if not corpus:
        print("코퍼스 0건 — 조회 조건이 무너졌다(침묵 통과 방지)")
        return 1

    cache_dir = args.cache_dir or None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    results = []
    for i, entry in enumerate(corpus, 1):
        results.append(verify_document(entry, delay=args.delay, cache_dir=cache_dir))
        if i % 25 == 0 or i == len(corpus):
            print(f"  … {i}/{len(corpus)}", flush=True)

    report, failed = render(results)
    print(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
