#!/usr/bin/env python3
"""화면이 날짜의 정체를 **사실대로** 말하는가.

사이트 곳곳에 "날짜는 실사한 날이 아니라 자료가 공개된 날입니다"가 적혀 있었다. 오래
참이었는데 2026-08-27 실사일 배관(mig 066·069) 이후 **내가 그 문장을 거짓으로 만들었다**:

  · 캐나다 실사 1,824건은 원천이 공개일을 주지 않아 `published_date` 에 **실사 시작일**이
    들어간다. 그 자리에 "공개된 날"이라고 적으면 거짓이다.
  · `coverage.html` 은 한술 더 떠 "원문 문서가 실사일을 주지 않기 때문이며, 소스가 늘어도
    이 한계는 바뀌지 않습니다"라고 단정했다. 실측하니 **네 소스가 준다**(FDA 483 발부일 ·
    식약처·EU·영국 실사 종료일 · 캐나다 실사 시작일). '한계는 바뀌지 않는다'는 단정이
    바로 그 한계를 오래 살려 둔 문장이었다.
  · 업체 페이지는 **자기 화면과도 모순**이었다 — 타임라인은 행마다 '공개'/'실사 종료'를
    적는데 머리글은 "실사한 날이 아니다"라고 했다.

★한 곳은 **일부러 남겼다**: `checklist.js` 는 21 CFR 조항 순위에서 파생되고 그 순위는
  사실상 FDA Warning Letter 기준이다(캐나다 지적의 21 CFR 인용은 0건이라 애초에 들어올
  수 없다). 그 면에서는 종전 문장이 여전히 참이다. 이 검사는 그 예외를 **근거와 함께**
  고정해, 다음 사람이 "일괄 치환하다 만 것"으로 오해하지 않게 한다.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

ABSOLUTE_CLAIM = "실사한 날이 아니라"

# 여러 소스를 한 목록에 섞어 보여주는 면 — 캐나다가 들어오므로 위 단정을 쓰면 안 된다.
MULTI_SOURCE_SURFACES = (
    "templates/coverage.html",
    "templates/trends.html",
    "templates/findings_browse.html",
    "templates/findings_firm_page.html",
    "assets/reactions.js",
)

# FDA 전용 면 — 21 CFR 조항에서 파생되고 캐나다는 CFR 인용이 0건이라 들어올 수 없다.
FDA_ONLY_SURFACE = "assets/checklist.js"


class DateAxisClaimTest(unittest.TestCase):
    def _read(self, rel: str) -> str:
        p = WEB / rel
        if not p.exists():
            self.skipTest(f"{rel} 없음")
        return p.read_text(encoding="utf-8")

    def test_multi_source_surfaces_do_not_claim_it_is_never_an_inspection_date(self) -> None:
        """여러 소스를 섞는 면에서 '실사한 날이 아니다'는 거짓이다(캐나다가 들어온다)."""
        for rel in MULTI_SOURCE_SURFACES:
            src = self._read(rel)
            # 주석에 옛 문구를 인용해 둔 곳은 화면 문장이 아니다 — 주석을 걷어내고 잰다.
            body = "\n".join(
                ln for ln in src.splitlines()
                if "{#-" not in ln and not ln.lstrip().startswith("//")
            )
            self.assertNotIn(
                ABSOLUTE_CLAIM, body,
                f"{rel} 이 '{ABSOLUTE_CLAIM}'라고 단정한다 — 캐나다 실사는 그 자리에 "
                "실사일이 들어가므로 거짓이다.")

    def test_multi_source_surfaces_name_the_exception(self) -> None:
        """빼기만 하면 독자는 날짜가 무엇인지 모른다 — **예외를 이름으로** 말해야 한다."""
        for rel in MULTI_SOURCE_SURFACES:
            src = self._read(rel)
            if rel.endswith("findings_firm_page.html"):
                # 이 면은 행마다 날짜 종류를 적으므로 머리글이 그 사실을 가리킨다.
                self.assertIn("행마다 그 날짜가 무엇인지", src, rel)
                continue
            self.assertIn("캐나다", src, f"{rel} 이 예외를 밝히지 않는다")

    def test_fda_only_surface_keeps_the_original_wording(self) -> None:
        """★예외는 실수가 아니라 판단이다.

        체크리스트는 21 CFR 조항 순위에서 나오고 그 순위는 사실상 FDA Warning Letter
        기준이다 — 캐나다 지적의 21 CFR 인용은 0건이라 이 면에 들어올 수 없다. 그래서
        종전 문장이 여전히 참이고, 바꾸면 오히려 없는 예외를 지어내는 것이 된다.
        이 검사가 사라지면 다음 사람이 '치환하다 만 자리'로 보고 고칠 것이다."""
        src = self._read(FDA_ONLY_SURFACE)
        self.assertIn(ABSOLUTE_CLAIM, src)
        self.assertIn("Warning Letter 기준", src,
                      "이 면이 FDA 전용이라는 근거가 화면 문구에서 사라졌다 — "
                      "그렇다면 위 예외도 다시 판단해야 한다.")
