#!/usr/bin/env python3
"""grm-finding-taxonomy -- 분류 표류 상시 감사(read-only).

왜 이게 있어야 하나
===================
2026-08-01~02 사이 **같은 계열의 오분류가 세 번** 나왔다.

  v5  역극성   "not required to be sterile"(=비무균)이 문장 속 'sterile' 한 단어 때문에
               무균 카테고리로.                                              25건
  v6  단어분리 "laborator y" 로 쪼개지면 \\b 키워드가 빗나가 캐치올로.        48건
  v7  접착     "ofcomponents" 로 들러붙으면 역시 빗나가는데, 이번엔 캐치올이
               아니라 **엉뚱한 특정 카테고리**로.                             49건

셋 다 **소리를 내지 않는다**. 분류기는 언제나 유효한 카테고리를 반환하므로 "틀렸다"와
"맞았다"가 겉보기에 구분되지 않는다. 세 번 다 사람이 화면을 보다가 발견했다.

이 모듈은 그 발견 경로를 자동화한다. 단, **손상 종류를 열거하지 않는다** -- 열거하면
다음 달 새 손상 형태에서 또 뚫린다(v5 가 "lookbehind 를 하나씩 덧붙이면 재발한다"고
남긴 교훈의 감사 계층 버전).

두 신호 모두 **차분(differential)** 이라 휴리스틱 임계값이 없다
=============================================================
A) 쌍둥이 불일치(twin) -- FDA 483/WL 지적문은 대부분 조항 보일러플레이트다. 영숫자만
   남긴 정규화 키가 같은 행들은 **같은 문장**이며 같은 카테고리여야 한다. 키가 같은데
   카테고리가 갈리면 그 클러스터 안 어딘가가 틀린 것이다.
   ★핵심: 이 신호는 손상의 **종류를 몰라도** 작동한다. "laborator y" 는 정규화하면
   "laboratory" 와 같은 키가 되므로, v6 를 만들기 전에도 이 감사는 그 6건을 지목했을
   것이다. 아직 이름 붙이지 않은 손상 형태도 같은 방식으로 드러난다.

B) 표류(drift) -- 저장된 category_code 와 현재 분류기의 판정이 다른 행. 재분류가
   밀렸거나(운영), 분류기가 바뀌었는데 반영이 안 됐다는 뜻이다. 값 자체가 곧 할 일이다.

의도적으로 **넣지 않은 것**
==========================
· 편집거리/퍼지 매칭 기반 문자 오인식 탐지("quaJity"). 실측에서 실제 영어 단어
  (chance/mister/deserve/sterilizes ...)를 삼켜 오탐을 만든다. known_limitation.
· "캐치올 비율" 같은 비율 지표. ★분모의 성격이 변하는 지표는 추세를 못 잰다(신규 소스가
  편입되면 즉시 무의미해진다) -- 이 저장소가 이미 한 번 데인 함정이다.
· 자동 수리. 이 모듈은 **한 행도 쓰지 않는다**. 판정은 사람이 하고 수리는 분류기 개정과
  findings_reclassify_service.py 가 한다.

안전 계약
=========
· 읽기 전용. PATCH/POST/DELETE 를 한 번도 보내지 않는다. git 조작도 없다.
· service-role 키는 어떤 로그·예외·리포트 필드에도 실리지 않는다(기존 관례).
· 리포트에 싣는 원문은 스팬 절단본이며, 공개 게이트(scope_status)를 그대로 표시해
  비공개 소스 원문이 공개 이슈 본문으로 새지 않게 한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from typing import Any

import findings_supabase_backfill as fsb
import grm_findings as gf
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


_SELECT_COLUMNS = "finding_id,finding_text,category_code,source,scope_status"
_DEFAULT_PAGE_SIZE = 1000
# 리포트에 싣는 원문 조각의 최대 길이. 이슈 본문은 공개 저장소에 남으므로 전문을 싣지 않는다.
_SNIPPET = 140
# 보일러플레이트 클러스터로 볼 최소 길이. 너무 짧은 지적문("Deficient.")은 우연히 같은
# 키가 되어 무의미한 클러스터를 만든다.
_TWIN_MIN_KEY_LEN = 40

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_twin_key(text: str) -> str:
    """영숫자만 남긴 정규화 키.

    ★이 한 줄이 감사의 일반성을 만든다. 공백 삽입("laborator y")·공백 탈락
    ("ofcomponents")·구두점 삽입("d.iscrepancy")·줄바꿈·하이픈은 전부 같은 키로
    수렴하므로, 손상된 쌍둥이와 멀쩡한 쌍둥이가 **같은 클러스터**에 들어온다.
    문자 오인식("quaJity")은 키가 달라져 수렴하지 않는다 -- known_limitation.
    """
    return _NON_ALNUM_RE.sub("", str(text or "").lower())


def fetch_findings(base_url: str, service_key: str, *, page_size: int = _DEFAULT_PAGE_SIZE) -> list[dict[str, Any]]:
    base = fsb._normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_classification_audit: SUPABASE_URL must start with https://")
    return fsb._fetch_all_pages(
        base, service_key, "findings",
        select=_SELECT_COLUMNS, page_size=page_size, order="finding_id.asc",
    )


def find_twin_disagreements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """정규화 키가 같은데 카테고리가 갈리는 클러스터를 반환한다.

    소수파를 "틀린 쪽"이라고 **단정하지 않는다** -- 실측에서 다수 쪽이 손상인 클러스터가
    있었다. 그래서 각 카테고리의 건수를 모두 실어 사람이 판단하게 한다.
    """
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = normalize_twin_key(row.get("finding_text"))
        if len(key) < _TWIN_MIN_KEY_LEN:
            continue
        clusters[key].append(row)

    out: list[dict[str, Any]] = []
    for key, members in clusters.items():
        categories = Counter(str(m.get("category_code") or "") for m in members)
        if len(categories) < 2:
            continue
        ranked = categories.most_common()
        minority_codes = {code for code, _ in ranked[1:]}
        out.append({
            "twin_key_prefix": key[:60],
            "cluster_size": len(members),
            "categories": [{"category_code": code, "count": count} for code, count in ranked],
            "members": [
                {
                    "finding_id": str(m.get("finding_id") or ""),
                    "category_code": str(m.get("category_code") or ""),
                    "source": str(m.get("source") or ""),
                    "scope_status": str(m.get("scope_status") or ""),
                    "is_minority": str(m.get("category_code") or "") in minority_codes,
                    "snippet": str(m.get("finding_text") or "")[:_SNIPPET],
                }
                for m in members
            ],
        })
    out.sort(key=lambda c: (-c["cluster_size"], c["twin_key_prefix"]))
    return out


def find_classifier_drift(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """저장된 카테고리 != 현재 분류기 판정. 값 자체가 곧 재분류 할 일이다."""
    out: list[dict[str, Any]] = []
    for row in rows:
        stored = str(row.get("category_code") or "")
        current = gf.classify_finding_category(str(row.get("finding_text") or ""))
        if current == stored:
            continue
        out.append({
            "finding_id": str(row.get("finding_id") or ""),
            "stored_category": stored,
            "current_category": current,
            "source": str(row.get("source") or ""),
            "scope_status": str(row.get("scope_status") or ""),
            "snippet": str(row.get("finding_text") or "")[:_SNIPPET],
        })
    out.sort(key=lambda r: r["finding_id"])
    return out


# ── 0건 가드 ────────────────────────────────────────────────────────────────
# ★"회귀 0은 측정이 아니라 구조로" -- 이 저장소가 반복해서 데인 침묵 실패다. 정규화가
# 깨지면 클러스터가 하나도 안 잡히고 감사는 **초록으로 통과한다**. 아래 앵커는 실제 라이브
# 원문에서 온 손상/정상 쌍이며, 이 둘이 같은 키로 수렴하지 않으면 감사 자체를 실패시킨다.
_GUARD_PAIRS: tuple[tuple[str, str], ...] = (
    # v6 (공백 삽입) -- 실측 fda483-86687 계열
    (
        "Each batch of drug product required to be free of objectionable microorganisms "
        "is not tested through appropriate laborator y testing.",
        "Each batch of drug product required to be free of objectionable microorganisms "
        "is not tested through appropriate laboratory testing.",
    ),
    # v7 (공백 탈락) -- 실측 211.80 계열
    (
        "Written procedures describe the receipt, storage, and rejection ofcomponents.",
        "Written procedures describe the receipt, storage, and rejection of components.",
    ),
    # 구두점 삽입 -- 아직 어떤 복원 규칙도 다루지 않는 형태. 감사는 규칙 없이도 잡아야 한다.
    (
        "Written records are not made of investigations into any unexplained d.iscrepancy.",
        "Written records are not made of investigations into any unexplained discrepancy.",
    ),
)


def guard_failures() -> list[str]:
    """정규화가 앵커 쌍을 같은 키로 수렴시키지 못하면 그 이유를 문자열로 반환한다."""
    failures: list[str] = []
    for damaged, clean in _GUARD_PAIRS:
        if normalize_twin_key(damaged) != normalize_twin_key(clean):
            failures.append(f"twin-key guard: {damaged[:60]!r} did not converge with its clean twin")
    return failures


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    twins = find_twin_disagreements(rows)
    drift = find_classifier_drift(rows)
    guards = guard_failures()
    twin_rows = sum(1 for c in twins for m in c["members"] if m["is_minority"])
    return {
        "schema_version": "grm-findings-classification-audit/v1",
        "taxonomy_version": gf.TAXONOMY_VERSION,
        "rows_scanned": len(rows),
        "guard_failures": guards,
        "totals": {
            "twin_clusters": len(twins),
            "twin_minority_rows": twin_rows,
            "drift_rows": len(drift),
        },
        # breach = 사람이 봐야 하는 상태. 가드 실패는 **감사기 자신이 고장난** 것이라
        # 항상 breach 다(0건 보고를 정상으로 오독하지 않기 위해).
        "breach": bool(guards) or bool(twins) or bool(drift),
        "twin_disagreements": twins,
        "classifier_drift": drift,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="findings 분류 표류 상시 감사(read-only)")
    parser.add_argument("--supabase-url", default="")
    parser.add_argument("--service-key", default="")
    parser.add_argument("--output", help="리포트 JSON 출력 경로(기본 stdout)")
    parser.add_argument(
        "--fail-on-breach", action="store_true",
        help="의심 행이 있거나 가드가 깨지면 exit 1 (워크플로에서 red 로 쓰려면 지정)",
    )
    args = parser.parse_args(argv)

    try:
        base_url, service_key = _resolve_credentials(args.supabase_url, args.service_key)
    except Exception as exc:  # 키 값은 절대 싣지 않는다 -- 예외 타입만.
        print(json.dumps({"error": type(exc).__name__}, ensure_ascii=False))
        return 2

    rows = fetch_findings(base_url, service_key)
    report = build_report(rows)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
    print(payload)
    if args.fail_on_breach and report["breach"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
