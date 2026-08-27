#!/usr/bin/env python3
"""트렌드 기관 선택 버튼이 **데이터보다 낡지 않게** 지킨다.

2026-08-28 실측으로 드러난 결함: `AGENCY_VIEWS` 에 식약처·FDA·전체 셋만 손으로 적혀
있었고 **캐나다가 빠져 있었다.** 근거는 어디에도 없었는데(그 위 주석은 "기본값이 왜
식약처인가"만 설명한다), 정작 캐나다는 최근 12개월 실사 지적 **1,480건(322문서)**로
FDA 483(1,142건)·식약처 실사(698건)보다 큰 **최대 소스**였다. 즉 "데이터가 얇아서 뺀
것"이 아니라 목록이 그냥 낡아 있었다.

손목록을 없앨 수는 없다 — 버튼은 데이터 fetch **전에** 그려지고(그래야 화면이 늦게
바뀌지 않는다), 라벨도 사람이 정해야 한다. 대신 **낡으면 시끄럽게** 만든다: 커밋된 문서
정본에서 기관별 규모를 세어, 문턱을 넘긴 기관에 버튼이 없으면 여기서 실패한다.

한 방향으로만 잰다 — "큰데 버튼이 없다"는 실패이고, "작은데 버튼이 있다"는 실패가
아니다. 후자는 판단(예: 국내 독자에게 식약처는 작아도 늘 필요하다)의 몫이다.
"""
from __future__ import annotations

import json
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "web" / "data" / "findings_docs.json"
TRENDS_JS = ROOT / "web" / "assets" / "trends.js"

# 이 수를 넘긴 기관은 버튼이 있어야 한다. 실측(2026-08-28 정본)으로 갈린다:
#   FDA 1,756 · 캐나다 1,330 · 식약처 129   |   EMA 78 · MHRA 8
# 100 은 그 사이의 둥근 수다. EMA 가 이 선을 넘으면 이 검사가 실패하고, 그때
# "순위를 낼 만한 모수인가"를 사람이 다시 판단하게 된다.
MIN_DOCS_FOR_BUTTON = 100


class TrendsAgencyViewsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DOCS.exists() or not TRENDS_JS.exists():
            raise unittest.SkipTest("정본 또는 trends.js 없음")
        data = json.loads(DOCS.read_text(encoding="utf-8"))
        cls.docs = data["documents"] if isinstance(data, dict) else data
        cls.js = TRENDS_JS.read_text(encoding="utf-8")
        block = cls.js.split("var AGENCY_VIEWS = [", 1)[1].split("];", 1)[0]
        cls.views = re.findall(r'key:\s*"([a-z]+)".*?prefix:\s*"([^"]*)"', block)

    def test_scan_is_alive(self) -> None:
        """0건은 성공이 아니라 파싱이 낡았다는 뜻이다(이 저장소의 다른 가드와 같은 원칙)."""
        self.assertGreaterEqual(len(self.views), 3, "AGENCY_VIEWS 파싱 실패")
        self.assertTrue(self.docs, "문서 정본이 비었다")

    def test_every_large_agency_has_a_button(self) -> None:
        """★문턱을 넘긴 기관에 버튼이 없으면 실패한다 — 캐나다가 그랬다."""
        counts = Counter(d.get("agency") or "" for d in self.docs)
        keys = {k for k, _ in self.views}
        # 기관 코드 → 버튼 key. 버튼 key 는 소문자 기관 코드다(mfds/fda/hc).
        missing = sorted(
            f"{agency}({n:,}문서)"
            for agency, n in counts.items()
            if n >= MIN_DOCS_FOR_BUTTON and agency and agency.lower() not in keys
        )
        self.assertEqual(
            missing, [],
            f"문서 {MIN_DOCS_FOR_BUTTON}건 이상인데 트렌드 기관 버튼이 없다: {missing}. "
            "데이터가 자란 것이므로 버튼을 더하거나, 뺄 이유를 AGENCY_VIEWS 주석에 적어라.")

    def test_all_view_is_last_and_default_is_first(self) -> None:
        """순서는 크기순이 아니다 — 기본값이 맨 앞, 합산이 맨 뒤다(그 이유는 주석에 있다)."""
        keys = [k for k, _ in self.views]
        self.assertEqual(keys[0], "mfds", "기본값(식약처)이 맨 앞이 아니다")
        self.assertEqual(keys[-1], "all", "'전체'(합산)가 맨 뒤가 아니다")

    def test_prefix_matches_a_real_source_string(self) -> None:
        """접두는 실제 `source` 문자열의 앞부분이어야 한다 — 오타 하나면 그 버튼이 0건이 된다."""
        sources = {(d.get("source") or "") for d in self.docs}
        for key, prefix in self.views:
            if not prefix:
                continue                                  # '전체'
            self.assertTrue(
                any(s.startswith(prefix) for s in sources),
                f"'{key}' 의 접두 '{prefix}' 로 시작하는 source 가 정본에 없다")

    def test_cfr_caveat_is_derived_not_a_named_agency(self) -> None:
        """21 CFR 인용 여부를 기관 **이름**으로 가르면 새 기관에서 거짓을 말한다.

        캐나다 지적 9,505건 중 21 CFR 인용은 0건이다(EMA·MHRA 도 0). 종전 코드는
        `key === "mfds"` 하나로 갈랐고, 캐나다를 넣는 순간 "규제기관이 지적서에 실제로
        적은 조항 순위"라는 **거짓 문장**이 붙었을 자리다."""
        self.assertIn("function agencyCitesCfr(", self.js)
        self.assertNotIn('view.key === "mfds"', self.js,
                         "CFR 안내 문구가 기관 이름으로 분기하고 있다")