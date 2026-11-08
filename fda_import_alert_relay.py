"""FDA 수입경보 66-40·66-41 원문 중계 — 한국 PC 에서 받아 grm-ops 에 올리고 러너 적용을 깨운다.

왜: GitHub 러너(데이터센터 IP)에서는 www.accessdata.fda.gov 전체가 Akamai 차단(HTTP 404)이고,
한국 PC 에서는 200 이다(2026-11-08 실측, collect_fda_import_alert.py 독스트링 '접속'). 우회는 만들지
않는다. PC 에는 Supabase service_role 키를 두지 않는다(원칙) → PC 는 **원문만** 넘기고 파싱·검증·DB
쓰기는 러너가 한다.

    PC(이 스크립트) ── requests 로 2페이지 ──▶ 구조 검증(수집기와 같은 파서·하한)
        ──▶ gh api 로 비공개 MINHOYEOM/grm-ops:import_alert_raw/<KST 날짜>/{66-40,66-41}.html.gz + manifest.json
        ──▶ gh workflow run grm-fda-import-alert.yml (source=ops, ops_date=<날짜>, dry_run=false)
    러너 ── grm-ops 에서 내려받기 ──▶ collect_fda_import_alert.py --html-dir … --apply
        (sha256·크기 재검증 → 구조 검증 → 멱등 가드 → import_alert_apply RPC. 실제 쓰기는 repo 변수
         ENABLE_FDA_IMPORT_ALERT=true 일 때만)

멱등: 두 PC(개인·회사) 중 켜진 쪽이 돌면 된다. 둘 다 돌아도 같다.
  - 그날 manifest 가 이미 grm-ops 에 있으면 **올리지 않는다**(그날 첫 원문이 정본). dispatch 는 다시 한다 —
    앞선 실행의 dispatch 가 실패했어도 복구되고, 러너는 같은 날·같은 sha256 이면 쓰지 않는다.
  - gzip 은 mtime=0 으로 만든다 → 같은 원문이면 같은 바이트.
  - manifest 를 **맨 마지막에** 올린다 → manifest 가 있으면 두 파일이 다 있다(러너는 manifest 를 기준으로 읽는다).
원문 HTML 은 공개 자료지만 공개 저장소(grm-api-intake)에는 커밋하지 않는다.

실행: py -3.12 fda_import_alert_relay.py              (받기·검증·업로드·dispatch)
      py -3.12 fda_import_alert_relay.py --check-only (받기·검증만, 올리지 않음)
필요: 이 PC 의 gh 로그인(repo·workflow 범위) · requests(Python312 에 있음). 비밀값 불필요.
예약 등록: docs/ops_fda_import_alert_relay.md
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import json
import subprocess
import sys
from typing import Any, Callable

import collect_fda_import_alert as ia

OPS_REPO = "MINHOYEOM/grm-ops"
INTAKE_REPO = "MINHOYEOM/grm-api-intake"
WORKFLOW = "grm-fda-import-alert.yml"
OPS_ROOT = "import_alert_raw"
KST = dt.timezone(dt.timedelta(hours=9))   # 한국은 일광절약시간이 없다 — zoneinfo(tzdata) 불필요

GhRunner = Callable[[list[str], bytes | None], tuple[int, bytes, bytes]]


def _gh_subprocess(args: list[str], stdin: bytes | None) -> tuple[int, bytes, bytes]:
    p = subprocess.run(["gh", *args], input=stdin, capture_output=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


class Gh:
    def __init__(self, runner: GhRunner = _gh_subprocess) -> None:
        self.run = runner

    def get_sha(self, repo: str, path: str) -> str | None:
        """파일이 있으면 blob sha, 없으면 None. 404 외의 오류는 예외."""
        code, out, err = self.run(["api", f"repos/{repo}/contents/{path}",
                                   "-H", "Accept: application/vnd.github.object", "--jq", ".sha"], None)
        if code == 0:
            return out.decode("utf-8").strip() or None
        if b"404" in err or b"Not Found" in err:
            return None
        raise RuntimeError(f"gh api GET {path} 실패: {err.decode('utf-8', 'replace')[:300]}")

    def put_file(self, repo: str, path: str, data: bytes, message: str) -> None:
        body: dict[str, Any] = {"message": message, "content": base64.b64encode(data).decode("ascii"),
                                "branch": "main"}
        sha = self.get_sha(repo, path)
        if sha:
            body["sha"] = sha
        code, _, err = self.run(["api", "-X", "PUT", f"repos/{repo}/contents/{path}", "--input", "-"],
                                json.dumps(body).encode("utf-8"))
        if code != 0:
            raise RuntimeError(f"gh api PUT {path} 실패: {err.decode('utf-8', 'replace')[:300]}")

    def dispatch(self, repo: str, workflow: str, fields: dict[str, str]) -> None:
        args = ["workflow", "run", workflow, "-R", repo, "--ref", "main"]
        for k, v in fields.items():
            args += ["-f", f"{k}={v}"]
        code, _, err = self.run(args, None)
        if code != 0:
            raise RuntimeError(f"gh workflow run 실패: {err.decode('utf-8', 'replace')[:300]}")


def fetch_and_check(fetch: Callable[[str], tuple[bytes, dict[str, str]]] = ia.fetch_page
                    ) -> tuple[dict[str, tuple[bytes, dict[str, str]]], list[str]]:
    """두 페이지를 받고 러너와 같은 구조 검증을 한다. 실패가 하나라도 있으면 올리지 않는다."""
    pages: dict[str, tuple[bytes, dict[str, str]]] = {}
    failures: list[str] = []
    for alert_no in ia.ALERTS:
        try:
            data, meta = fetch(alert_no)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{alert_no}:fetch:{exc}")
            continue
        parsed = ia.parse_alert_page(ia.decode_page(data))
        current, _ = ia.consolidate(alert_no, parsed.firms)
        fails = ia.validate_structure(alert_no, parsed, curr_unique=len(current))
        failures.extend(f"{alert_no}:structure:{f}" for f in fails)
        pages[alert_no] = (data, meta)
        print(f"[{alert_no}] {len(data):,} bytes · 업체 {len(current)}곳 · 구조 {'OK' if not fails else fails}")
    return pages, failures


def relay(run_date: str, *, gh: Gh, fetch: Callable[[str], tuple[bytes, dict[str, str]]] = ia.fetch_page,
          now: Callable[[], dt.datetime] = lambda: dt.datetime.now(KST), check_only: bool = False,
          dispatch: bool = True, ops_repo: str = OPS_REPO, intake_repo: str = INTAKE_REPO) -> int:
    pages, failures = fetch_and_check(fetch)
    if failures:
        print(f"검증 실패 — 올리지 않는다: {failures}", file=sys.stderr)
        return 2
    if check_only:
        print("check-only — 업로드·dispatch 생략")
        return 0

    base = f"{OPS_ROOT}/{run_date}"
    manifest_path = f"{base}/{ia.RELAY_MANIFEST}"
    if gh.get_sha(ops_repo, manifest_path):
        print(f"{manifest_path} 이미 있음 — 그날 첫 원문이 정본이므로 올리지 않는다")
    else:
        for alert_no, (data, _) in pages.items():
            gh.put_file(ops_repo, f"{base}/{ia.relay_file_name(alert_no)}", gzip.compress(data, mtime=0),
                        f"import-alert relay {run_date} {alert_no}")
        manifest = ia.build_relay_manifest(run_date, now().isoformat(timespec="seconds"), pages)
        gh.put_file(ops_repo, manifest_path,
                    (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                    f"import-alert relay {run_date} manifest")
        print(f"올림: {ops_repo}:{base}/ (경보 {len(pages)}개 + manifest)")
    if dispatch:
        gh.dispatch(intake_repo, WORKFLOW, {"source": "ops", "ops_date": run_date, "dry_run": "false"})
        print(f"dispatch: {intake_repo} {WORKFLOW} source=ops ops_date={run_date}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default="", help="기준일 ISO(기본 오늘 KST)")
    ap.add_argument("--check-only", action="store_true", help="받기·검증만 하고 올리지 않는다")
    ap.add_argument("--no-dispatch", action="store_true", help="올리기만 하고 워크플로를 깨우지 않는다")
    args = ap.parse_args(argv)
    run_date = args.date or dt.datetime.now(KST).date().isoformat()
    print(f"FDA 수입경보 중계 {run_date} (KST {dt.datetime.now(KST):%H:%M})")
    try:
        return relay(run_date, gh=Gh(), check_only=args.check_only, dispatch=not args.no_dispatch)
    except Exception as exc:  # noqa: BLE001 — 예약작업 로그에 한 줄로 남긴다
        print(f"중계 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
