"""MFDS KR-egress 배선 표류 가드 — MFDS 호스트를 치는 경로를 전수 스캔.

2026-08-10 실장애: 8/10 브리프에는 식약처 지침(`data0010-15270`)이 실렸는데 자료실에는
없었다. 원인은 파서도 분류도 아니라 **배선 누락**이었다.

  · `MFDS_HTTP_PROXY`(KR egress)는 2026-07-05부터 secret 으로 존재했고
  · `grm_common.proxies_for` 가 MFDS 계열 호스트만 그 프록시로 태우는 층도 있었지만
  · 그 secret 을 넘기는 워크플로는 `grm-intake.yml` **하나뿐**이었다.

같은 날 같은 시각의 실측이 이걸 못 박는다 — 프록시를 넘긴 intake 는 같은 RSS 를 피드당
2초에 받았고(3/3), 안 넘긴 자료실 갱신은 7개 피드 × 재시도 3회가 **전부** connect timeout
났다(0/21). 새 러너로 재디스패치해도 결과는 같았다(0/21) — 러너 복불복이 아니다.

이 결함이 오래 산 이유는 **양끝이 다 갖춰져 있어서 배선됐다고 착각**하기 때문이다(secret
있음 + 헬퍼 있음 → "되겠지"). 그래서 이 가드는 사람이 워크플로를 세는 대신 **호출 그래프와
데이터에서 유도**한다. 허용목록 손열거를 쓰지 않는다 — 이 저장소는 손열거가 낡아 침묵
구멍이 난 전례가 여럿이다. 스캔 결과가 0건이면 실패로 처리한다(빈 결과가 성공으로 읽히는
함정 방지).

배선은 두 층 다 있어야 성립한다. 하나만 있으면 조용히 무효다.
  ① 워크플로가 `MFDS_HTTP_PROXY` 를 env 로 넘긴다
  ② 모듈이 실제로 `proxies_for` 를 거친다(`requests` 를 직접 잡는 모듈이 특히 위험 —
     `library_linkcheck` 는 ①만 있었다면 env 를 받고도 아무 효과가 없었다)
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(REPO, ".github", "workflows")
LIBRARY_DIR = os.path.join(REPO, "web", "data", "library")

sys.path.insert(0, REPO)

import grm_common  # noqa: E402 — MFDS_EGRESS_HOSTS·proxies_for 의 단일 정의

PROXY_ENV = "MFDS_HTTP_PROXY"
# 옛 이름(`_proxies_for`)도 유효한 배선이다 — probe_mfds_egress 가 그걸 쓴다.
PROXY_HELPER_RE = re.compile(r"\b_?proxies_for\s*\(")

# grm_common 은 호스트 집합·프록시 헬퍼의 **정의처**다. seed 에 넣으면 이걸 import 하는
# 모듈 전부가 전이로 걸려 집합이 저장소 전체가 되고, 그러면 아무것도 못 걸러낸다.
DEFINITION_MODULE = "grm_common"

# 호스트 목록은 grm_common 에서 가져온다 — 여기에 다시 적으면 두 곳이 어긋난다.
_HOST_RE = re.compile("|".join(re.escape(h) for h in sorted(grm_common.MFDS_EGRESS_HOSTS)))
# grm_common 헬퍼 경유(프록시 자동 적용) + requests 직타(수동 적용 필요) 둘 다 HTTP 다.
_HTTP_CALL_RE = re.compile(
    r"\bhttp_get_(?:json|xml|text|bytes)\s*\(|"
    r"\brequests\.(?:get|post|request)\s*\(|"
    r"\bsession\.(?:get|post|request)\s*\("
)
# requests 를 직접 잡는 모듈 — 프록시가 자동으로 붙지 않으므로 명시 배선이 필요하다.
_RAW_REQUESTS_RE = re.compile(
    r"\brequests\.(?:get|post|request)\s*\(|\bsession\.(?:get|post|request)\s*\(")
# 자료실 카탈로그를 읽어 그 안의 URL 을 치는 모듈 — URL 이 소스가 아니라 **데이터**에
# 있으므로 호스트 문자열 스캔으로는 절대 안 잡힌다. library_linkcheck 가 이 경우다.
_CATALOG_REF_RE = re.compile(r"web/data/library\b")
# MFDS 수집 진입 함수 호출 — `collect_mfds(`·`collect_mfds_recall(` 등 계열 전부.
_MFDS_ENTRY_CALL_RE = re.compile(r"\bcollect_mfds\w*\s*\(")
_WORKFLOW_PY_RE = re.compile(r"python3?\s+([a-z0-9_]+)\.py")


def _root_sources() -> dict[str, str]:
    """루트 평면의 `*.py` 를 모듈명 → 소스로 읽는다."""
    out: dict[str, str] = {}
    for name in sorted(os.listdir(REPO)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(REPO, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            out[name[:-3]] = fh.read()
    return out


def _direct_fetchers(sources: dict[str, str]) -> set[str]:
    """MFDS 호스트를 **언급하면서 HTTP 도 치는** 모듈. 언급만으로는 넣지 않는다."""
    return {
        name for name, src in sources.items()
        if name != DEFINITION_MODULE and _HOST_RE.search(src) and _HTTP_CALL_RE.search(src)
    }


def _catalog_fetchers(sources: dict[str, str]) -> set[str]:
    """자료실 카탈로그의 URL 을 직접 치는 모듈(정적 스캔으로는 호스트가 안 보인다)."""
    return {
        name for name, src in sources.items()
        if _CATALOG_REF_RE.search(src) and _RAW_REQUESTS_RE.search(src)
    }


def _entry_callers(sources: dict[str, str]) -> set[str]:
    """MFDS 수집 진입 함수를 **호출**하는 모듈.

    `library_staging_build` 는 소스에 mfds.go.kr 문자열이 없다 — `collect_mfds.collect_mfds(`
    를 부를 뿐이라 호스트 스캔만으로는 이번 사고의 당사자가 빠진다.

    import 가 아니라 **호출**로 넓히는 게 핵심이다. import 전파(전이 폐포)로 해 봤더니
    저장소 대부분이 걸려(워크플로 16건 오탐) 게이트가 무의미해졌다 — `collect_mfds` 를
    간접적으로 import 하는 것과 그 수집 경로를 실제로 태우는 것은 다르다.
    """
    return {name for name, src in sources.items() if _MFDS_ENTRY_CALL_RE.search(src)}


def _workflow_files() -> list[str]:
    return sorted(
        os.path.join(WORKFLOW_DIR, n)
        for n in os.listdir(WORKFLOW_DIR)
        if n.endswith((".yml", ".yaml"))
    )


class MfdsEgressWiringTest(unittest.TestCase):

    def setUp(self) -> None:
        self.sources = _root_sources()
        self.direct = _direct_fetchers(self.sources)
        self.catalog = _catalog_fetchers(self.sources)
        self.needing = self.direct | _entry_callers(self.sources) | self.catalog

    def test_host_set_is_alive(self) -> None:
        """호스트 집합이 단일 정의로 살아 있다 — 비면 아래 스캔이 전부 무의미해진다."""
        self.assertIn("www.mfds.go.kr", grm_common.MFDS_EGRESS_HOSTS)
        self.assertTrue(callable(grm_common.proxies_for))

    def test_direct_fetcher_scan_is_not_empty(self) -> None:
        """스캔이 비었으면 정규식이 낡은 것이다 — 빈 결과를 성공으로 읽지 않는다."""
        self.assertGreaterEqual(
            len(self.direct), 5,
            f"MFDS HTTP 호출 모듈 스캔이 비었거나 너무 적다: {sorted(self.direct)}")
        self.assertIn("collect_mfds", self.direct, "정의 모듈이 안 잡히면 정규식이 죽은 것이다")

    def test_catalog_fetcher_scan_is_not_empty(self) -> None:
        self.assertIn(
            "library_linkcheck", self.catalog,
            f"자료실 카탈로그 URL 을 치는 모듈 스캔이 낡았다: {sorted(self.catalog)}")

    def test_scan_reaches_the_2026_08_10_victim(self) -> None:
        """이번 사고 당사자가 스캔에 실제로 잡히는지 — 가드의 존재 이유다."""
        self.assertIn("library_staging_build", self.needing)
        self.assertIn("collect_intake", self.needing)

    def test_every_workflow_touching_mfds_passes_the_proxy(self) -> None:
        """①층 — MFDS 경로 스크립트를 돌리는 워크플로는 전부 프록시를 넘긴다.

        이 테스트가 이 변경의 본체다. 같은 결함(한 워크플로만 배선하고 나머지는 뒤처짐)이
        재발하면 CI 에서 잡힌다.
        """
        offenders: list[str] = []
        checked = 0
        for path in _workflow_files():
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            invoked = set(_WORKFLOW_PY_RE.findall(body))
            needs = sorted(invoked & self.needing)
            if not needs:
                continue
            checked += 1
            if PROXY_ENV not in body:
                offenders.append(f"{os.path.basename(path)} (실행: {', '.join(needs)})")
        self.assertGreaterEqual(
            checked, 3,
            f"MFDS 경로 워크플로 스캔이 너무 적다({checked}건) — _WORKFLOW_PY_RE 가 낡았을 수 있다")
        self.assertEqual(
            offenders, [],
            f"MFDS 경로를 실행하면서 {PROXY_ENV} 를 넘기지 않는 워크플로가 있다 — "
            f"해외 러너에서 mfds.go.kr 은 connect timeout 난다: {offenders}")

    def test_modules_holding_requests_directly_route_through_the_helper(self) -> None:
        """②층 — `requests` 를 직접 잡는 MFDS 경로 모듈은 `proxies_for` 를 거쳐야 한다.

        env 만 넘기고 코드가 안 거치면 **배선된 것처럼 보이는 무효 상태**가 된다.
        library_linkcheck 가 정확히 그 상태였다.
        """
        candidates = sorted(
            name for name in (self.direct | self.catalog)
            if _RAW_REQUESTS_RE.search(self.sources[name])
        )
        self.assertGreaterEqual(
            len(candidates), 3,
            f"requests 직타 모듈 스캔이 너무 적다: {candidates}")
        offenders = [n for n in candidates if not PROXY_HELPER_RE.search(self.sources[n])]
        self.assertEqual(
            offenders, [],
            "MFDS 호스트를 requests 로 직접 치면서 proxies_for 를 안 거치는 모듈이 있다 "
            f"— 워크플로가 {PROXY_ENV} 를 넘겨도 무효다: {offenders}")

    def test_live_catalog_still_contains_mfds_hosts(self) -> None:
        """카탈로그에 MFDS 링크가 실재하는지 — 위 ②층 catalog 갈래의 근거다.

        언젠가 MFDS 가 자료실에서 빠지면 이 테스트가 먼저 말해 준다(조용히 통과하지 않는다).
        """
        path = os.path.join(LIBRARY_DIR, "mfds.json")
        self.assertTrue(os.path.isfile(path), f"자료실 MFDS 카탈로그가 없다: {path}")
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        urls = [
            str(item.get(field) or "")
            for item in payload.get("items", [])
            for field in ("official_url", "pdf_url", "ko_url")
        ]
        hits = [u for u in urls if _HOST_RE.search(u)]
        self.assertTrue(hits, "자료실 MFDS 카탈로그에 mfds.go.kr 링크가 하나도 없다")


if __name__ == "__main__":
    unittest.main()
