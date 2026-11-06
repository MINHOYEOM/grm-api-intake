#!/usr/bin/env python3
"""브리프 카드 '이 업체 앞선 지적 기록' 사이드카 파일 만들기 (시범 설계서 §9 PR 2).

발행본 `web/data/briefs/brief_web_*.json` 을 읽어 카드마다 업체키·FEI 를 뽑고, 읽기 전용 RPC
`findings_card_history`(migration 120)에 200장 단위로 물어서 확실/참고용/숨김을 판정한 뒤
`web/data/firm_history/<발행일>.json` 으로 쓴다. 렌더러(PR 3)가 이 파일을 읽는다 — 이 PR 은
사이트 출력을 바꾸지 않는다(아무도 이 파일을 읽지 않는다).

## 판정(설계서 §3·§4·§7, 순수 함수 `judge_card`)
- 시범 범위: FDA 483(FEI 일치만 '이 제조소') + 식약처 회수·판매중지/행정처분/GMP실사('같은 이름 업체').
- 483: FEI 형식(7~10자리 숫자)이 아니면 숨김. 같은 FEI 앞선 문서 0건이면 **이름 묶음이 있어도 숨김**
  (AGC S.p.A. 이탈리아가 미국 AGC 기록을 가져오던 오탐 방지).
- 식약처 3종: 이름 묶음 앞선 문서 수 > 0 이면 '참고용'(basis=name).
- 0건·업체명 비었음/원문 미기재·여러 업체 묶음 카드·발행일 없음·RPC 응답에 없는 카드는 숨김.
  숨김 카드는 파일에 넣지 않는다. '처음 등장'은 쓰지 않는다.
- [판단] 483 병합 카드(merged_count>1)는 병합 항목의 FEI 가 전부 대표와 같을 때만 허용한다.
  병합 483 은 `merged_multi_firm` 플래그가 없는데도 서로 다른 제조소를 묶는 디제스트라(11-02 MSD 11건),
  대표 한 곳의 이력을 카드 전체의 이력처럼 말하게 된다.

## 사용법
  python firm_history_stamp.py --brief web/data/briefs/brief_web_2026_11_02.json
  (SUPABASE_URL·SUPABASE_ANON_KEY 환경변수 또는 --supabase-url/--supabase-anon-key. anon 키만 — service-role 금지)
  --dry-run: 네트워크 없이 후보 카드 수만 센다(파일 안 씀).
실패(네트워크·RPC·함수 미적용)하면 **파일을 쓰지 않고** 비0 종료한다 — 호출 워크플로는 continue-on-error 로
부르고, 파일이 없으면 줄도 없다(발행을 막지 않음).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "web" / "data" / "firm_history"
SCHEMA = "grm-firm-history/v1"
RPC_NAME = "findings_card_history"
CHUNK = 200            # RPC 한도(120 의 p_cards 클램프)
EXCLUDE_LIMIT = 500    # RPC 한도(120 의 p_exclude 클램프)

CT_483 = "FDA 483 실사 관찰"
CT_MFDS = frozenset({"회수·판매중지", "행정처분", "GMP실사"})

_FEI_RE = re.compile(r"^[0-9]{7,10}$")          # 101_findings_fei.sql 의 형식 검사와 같다
_FEI_IN_VALUE_RE = re.compile(r"\bFEI\s*([0-9]+)")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_YM_RE = re.compile(r"^[0-9]{4}-[0-9]{2}$")
_FIRM_FACT_LABELS = ("제조소/업체", "업체/제조소", "제조소", "업체")


def _fact(card: dict[str, Any], label: str) -> str:
    for f in card.get("facts") or []:
        if f.get("label") == label:
            return str(f.get("value") or "")
    return ""


def extract_fei(value: str) -> str | None:
    """'이름 · FEI 3020270660' -> '3020270660'. 형식(7~10자리) 밖이면 None(11자리 오입력 포함)."""
    m = _FEI_IN_VALUE_RE.search(value or "")
    if not m:
        return None
    fei = m.group(1)
    return fei if _FEI_RE.match(fei) else None


def _card_fei(card: dict[str, Any]) -> str | None:
    for label in _FIRM_FACT_LABELS:
        v = _fact(card, label)
        if v:
            return extract_fei(v)
    return None


def _merged_483_ok(card: dict[str, Any], fei: str | None) -> bool:
    """병합 483 은 병합 항목의 FEI 가 전부 대표와 같을 때만 통과(아니면 다른 제조소 묶음)."""
    if (card.get("merged_count") or 1) <= 1:
        return True
    if fei is None:
        return False
    items = card.get("merged_items") or []
    if not items:
        return False
    return all(extract_fei(str(it)) == fei for it in items)


def scope_of(card: dict[str, Any]) -> str | None:
    ct = card.get("card_type")
    if ct == CT_483:
        return "fda483"
    if ct in CT_MFDS:
        return "mfds"
    return None


def candidate_from_card(card: dict[str, Any], firm_key_fn: Callable[[dict[str, Any]], str]
                        ) -> dict[str, Any] | None:
    """시범 범위이면서 RPC 에 물을 가치가 있는 카드만 {"id","date","firm_key","fei","scope"} 로. 아니면 None."""
    scope = scope_of(card)
    if scope is None:
        return None
    cid = str(card.get("id") or "")
    date = _fact(card, "발행일")
    if not cid or not _DATE_RE.match(date):
        return None
    if card.get("merged_multi_firm"):
        return None
    firm_key = firm_key_fn(card) or ""
    if not firm_key:
        return None
    fei = None
    if scope == "fda483":
        fei = _card_fei(card)
        if fei is None or not _merged_483_ok(card, fei):
            return None
    return {"id": cid, "date": date, "firm_key": firm_key, "fei": fei, "scope": scope}


def _as_int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def judge_card(cand: dict[str, Any] | None, row: dict[str, Any] | None) -> dict[str, Any] | None:
    """후보 + RPC 행 -> {"basis","n","last"} 또는 None(숨김). 순수 함수."""
    if not cand or not row:
        return None
    if cand["scope"] == "fda483":
        if cand.get("fei") is None:
            return None
        n, last, basis = _as_int(row.get("fei_n")), row.get("fei_last"), "site"
    else:
        n, last, basis = _as_int(row.get("name_n")), row.get("name_last"), "name"
    if n <= 0 or not isinstance(last, str) or not _YM_RE.match(last):
        return None
    return {"basis": basis, "n": n, "last": last}


def build_sidecar(brief: dict[str, Any], rows_by_id: dict[str, dict[str, Any]],
                  candidates: list[dict[str, Any]]) -> dict[str, Any]:
    cards: dict[str, Any] = {}
    for cand in candidates:
        verdict = judge_card(cand, rows_by_id.get(cand["id"]))
        if verdict is not None:
            cards[cand["id"]] = verdict
    return {"schema": SCHEMA, "as_of": brief["brief"]["publish_date"], "cards": cards}


def collect_candidates(brief: dict[str, Any], firm_key_fn: Callable[[dict[str, Any]], str]
                       ) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for card in brief.get("cards") or []:
        cand = candidate_from_card(card, firm_key_fn)
        if cand and cand["id"] not in seen:
            seen.add(cand["id"])
            out.append(cand)
    return out


def fetch_rows(candidates: list[dict[str, Any]], exclude_ids: list[str],
               post: Callable[[dict[str, Any]], Any]) -> dict[str, dict[str, Any]]:
    """RPC 호출(200장 단위). `post(payload)` 는 모킹 지점 — 파싱된 JSON(배열)을 돌려준다."""
    if len(exclude_ids) > EXCLUDE_LIMIT:
        raise RuntimeError(
            f"이번 호 카드 {len(exclude_ids)}장 > p_exclude 한도 {EXCLUDE_LIMIT} — 잘리면 형제 카드를 셀 수 있다")
    rows: dict[str, dict[str, Any]] = {}
    for i in range(0, len(candidates), CHUNK):
        part = candidates[i:i + CHUNK]
        payload = {
            "p_cards": [{"id": c["id"], "date": c["date"], "firm_key": c["firm_key"],
                         **({"fei": c["fei"]} if c["fei"] else {})} for c in part],
            "p_exclude": exclude_ids,
        }
        body = post(payload)
        if not isinstance(body, list):
            raise RuntimeError("RPC 응답이 배열이 아님")
        for r in body:
            if isinstance(r, dict) and isinstance(r.get("id"), str):
                rows[r["id"]] = r
    return rows


def make_http_post(base_url: str, anon_key: str, timeout: int = 120) -> Callable[[dict[str, Any]], Any]:
    import requests  # 지연 import — 단위 시험은 네트워크 모듈이 필요 없다

    base = base_url.rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("SUPABASE_URL 형식 오류(https:// 필요)")

    def post(payload: dict[str, Any]) -> Any:
        try:
            resp = requests.post(
                f"{base}/rest/v1/rpc/{RPC_NAME}",
                headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=timeout)
        except requests.RequestException as exc:  # 메시지에 키가 담길 수 있어 타입만 남긴다
            raise RuntimeError(f"RPC 요청 실패: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise RuntimeError(f"RPC HTTP {resp.status_code}")
        return resp.json()

    return post


def _load_firm_key_fn() -> Callable[[dict[str, Any]], str]:
    web = str(REPO_ROOT / "web")
    if web not in sys.path:
        sys.path.insert(0, web)
    import render  # 업체키 정본(_firm_key_for_card) 재사용 — 두 벌 두지 않는다
    return render._firm_key_for_card


def write_sidecar(out_dir: Path, sidecar: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{sidecar['as_of']}.json"
    text = json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def main(argv: list[str] | None = None,
         post: Callable[[dict[str, Any]], Any] | None = None,
         firm_key_fn: Callable[[dict[str, Any]], str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # cp949 콘솔에서 한글·기호 출력이 죽지 않게
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--brief", required=True, help="발행본 brief_web_*.json 경로")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--supabase-url", default=os.environ.get("SUPABASE_URL", ""))
    ap.add_argument("--supabase-anon-key", default=os.environ.get("SUPABASE_ANON_KEY", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    brief = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    fk = firm_key_fn or _load_firm_key_fn()
    candidates = collect_candidates(brief, fk)
    print(f"후보 카드 {len(candidates)}장 / 전체 {len(brief.get('cards') or [])}장 "
          f"(발행일 {brief['brief']['publish_date']})")
    if args.dry_run:
        return 0
    try:
        if post is None:
            if not args.supabase_url or not args.supabase_anon_key:
                raise RuntimeError("SUPABASE_URL·SUPABASE_ANON_KEY 가 필요합니다(anon 키 — service-role 금지)")
            post = make_http_post(args.supabase_url, args.supabase_anon_key)
        exclude = sorted({str(c.get("id")) for c in brief.get("cards") or [] if c.get("id")})
        rows = fetch_rows(candidates, exclude, post)
        sidecar = build_sidecar(brief, rows, candidates)
    except (RuntimeError, ValueError) as exc:
        print(f"실패 — 파일을 쓰지 않습니다: {exc}", file=sys.stderr)
        return 1
    path = write_sidecar(Path(args.output_dir), sidecar)
    kinds = [v["basis"] for v in sidecar["cards"].values()]
    print(f"{path} 작성: 줄 {len(kinds)}장 (이 제조소 {kinds.count('site')} · 같은 이름 {kinds.count('name')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
