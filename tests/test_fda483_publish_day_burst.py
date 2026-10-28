"""FDA 483 발행일 추가 예산 회귀 (2026-10-27 FDA 결손 감사).

상한 밖 483 은 다음 날 grm-fda483-ocr-recovery 예약 실행(23:37 UTC = 08:37 KST)이 회수한다.
그런데 월요일 수집분만은 그 회수가 07:30 KST Routine 보다 늦어, 월요일 실행이 상한에 걸리면
그 주 브리프에 본문 없이 실린다. 발행일 실행에만 상한을 넓히되, 넓힌 몫은 마감 epoch 까지만
쓴다 — 기본 상한은 마감과 무관하게 항상 보장된다.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f

START = date(2026, 5, 1)
END = date(2026, 5, 31)
_ENV = {"ENABLE_FDA_483_OBSERVATIONS": "false", "ENABLE_FDA_483_DEEP": "false",
        "ENABLE_FDA_483_OCR": "false"}


def _rows(n):
    raw = [["04/17/2026", f"Firm {i}", f"100000{i}",
            f'<a href="/media/{9300 + i}/download">483</a>', "Wisconsin", "",
            "Drug Manufacturer", "05/27/2026", ""] for i in range(n)]
    return f._datatable_norm_rows(raw)


class _ActiveFor:
    """`_burst_active` 대역 — 처음 n 번만 True(진입 1회 + 문서별 1회씩 불린다)."""

    def __init__(self, n):
        self.left = n

    def __call__(self, now=None):
        self.left -= 1
        return self.left >= 0


class PublishDayBurstTest(unittest.TestCase):
    def _collect(self, n_rows, *, burst_items, burst_ocr=0, base_ocr=200,
                 active=None, deadline=0, pages_per_doc=0):
        rows = _rows(n_rows)

        def fake_fetch(url):
            # OCR 페이지 소비를 흉내 낸다(실제 OCR 함수가 하는 계수와 같은 필드).
            f._OCR_BUDGET["remaining"] -= pages_per_doc
            f._OCR_BUDGET["used"] += pages_per_doc
            return "OBSERVATION 1 x", "pdf-ok"

        patches = [
            patch.dict(os.environ, _ENV),
            patch.object(f, "FDA483_EXCERPT_MAX_ITEMS", 2),
            patch.object(f, "FDA483_PDF_MAX_FETCHES", 4),
            patch.object(f, "FDA483_OCR_PAGE_BUDGET", base_ocr),
            patch.object(f, "FDA483_BURST_PDF_MAX_ITEMS", burst_items),
            patch.object(f, "FDA483_BURST_OCR_PAGE_BUDGET", burst_ocr),
            patch.object(f, "FDA483_BURST_DEADLINE_EPOCH", deadline),
            patch.object(f, "FDA483_EXCERPT_DELAY_SECONDS", 0),
            patch.object(f, "_fetch_fda483_pdf_text", fake_fetch),
            patch.object(f, "_fetch_html_rows",
                         lambda start_date=None: (list(rows), len(rows), False)),
        ]
        if active is not None:
            patches.append(patch.object(f, "_burst_active", active))
        for p in patches:
            p.start()
        try:
            f.collect_fda_483(START, END)
        finally:
            for p in reversed(patches):
                p.stop()
        return f.LAST_HEALTH["fda483_excerpt"]

    def test_off_by_default_keeps_base_cap(self):
        """env 미설정(마감 0) — 종전과 같다."""
        h = self._collect(6, burst_items=5)
        self.assertFalse(h["burst_granted"])
        self.assertEqual(h["attempted"], 2)
        self.assertEqual(h["skipped_no_attempt"], 4)

    def test_before_deadline_widens_cap(self):
        """마감 전 발행일 실행은 넓힌 상한까지 시도한다 — 수정 전에는 2건에서 멈췄다."""
        h = self._collect(6, burst_items=5, active=_ActiveFor(100))
        self.assertTrue(h["burst_granted"])
        self.assertEqual(h["attempted"], 5)
        self.assertEqual(h["skipped_no_attempt"], 1)
        self.assertFalse(h["burst_deadline_hit"])

    def test_deadline_mid_run_returns_to_base_cap(self):
        """진입 + 문서 3건까지 마감 전 → 넷째부터 기본 상한(이미 3 ≥ 2)이라 멈춘다."""
        h = self._collect(6, burst_items=5, active=_ActiveFor(4))
        self.assertEqual(h["attempted"], 3)
        self.assertEqual(h["skipped_no_attempt"], 3)
        self.assertTrue(h["burst_deadline_hit"])

    def test_late_start_still_gets_base_cap(self):
        """마감이 이미 지난 실행(월요일 크론 지각)도 기본 상한은 그대로 받는다."""
        h = self._collect(6, burst_items=5, deadline=1)   # epoch 1 = 이미 지남
        self.assertFalse(h["burst_granted"])
        self.assertEqual(h["attempted"], 2)

    def test_ocr_budget_widened_then_clamped_at_deadline(self):
        """OCR 예산도 마감 전엔 넓히고, 마감이 지나면 기본 예산에서 이미 쓴 만큼을 뺀다."""
        self._collect(6, burst_items=5, burst_ocr=20, base_ocr=4,
                      active=_ActiveFor(4), pages_per_doc=3)
        self.assertEqual(f._OCR_BUDGET["used"], 9)        # 3건 × 3쪽(넓힌 예산 안)
        self.assertEqual(f._OCR_BUDGET["remaining"], 0)   # 기본 4쪽은 이미 초과 → 0

    def test_ocr_budget_widened_when_granted(self):
        self._collect(1, burst_items=5, burst_ocr=20, base_ocr=4, active=_ActiveFor(100))
        self.assertEqual(f._OCR_BUDGET["remaining"], 20)

    def test_burst_never_narrows(self):
        """추가 예산이 기본보다 작게 설정돼도 상한을 줄이지 않는다."""
        h = self._collect(6, burst_items=1, active=_ActiveFor(100))
        self.assertEqual(h["attempted"], 2)

    def test_burst_active_reads_deadline(self):
        with patch.object(f, "FDA483_BURST_DEADLINE_EPOCH", 1000):
            self.assertTrue(f._burst_active(now=999))
            self.assertFalse(f._burst_active(now=1000))
        with patch.object(f, "FDA483_BURST_DEADLINE_EPOCH", 0):
            self.assertFalse(f._burst_active(now=0))


class WorkflowWiringTest(unittest.TestCase):
    """grm-intake.yml 이 발행일(월, KST)에만 추가 예산을 켜고, 이름이 모듈과 맞는가."""

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".github", "workflows", "grm-intake.yml"),
                  encoding="utf-8") as fh:
            cls.wf = fh.read()

    def test_env_names_match_module(self):
        for name in ("FDA483_BURST_DEADLINE_EPOCH", "FDA483_BURST_PDF_MAX_ITEMS",
                     "FDA483_BURST_OCR_PAGE_BUDGET"):
            self.assertIn(f"export {name}=", self.wf)
            self.assertTrue(hasattr(f, name))

    def test_gated_on_kst_monday_with_cutoff(self):
        block = self.wf.split("date '+%u')\" = \"1\" ]; then", 1)
        self.assertEqual(len(block), 2, "월요일(KST) 조건 분기가 없다")
        body = block[1].split("\n          fi\n", 1)[0]
        self.assertIn("06:45", body)               # Routine(07:30) 전 마감
        self.assertIn("20 * 60", body)             # 실행 시간 상한(잡 타임아웃 보호)

    def test_job_timeout_covers_burst(self):
        import re
        m = re.search(r"timeout-minutes:\s*(\d+)", self.wf)
        self.assertGreaterEqual(int(m.group(1)), 26 + 20)   # 실측 최장 26분 + 추가 예산 20분


if __name__ == "__main__":
    unittest.main()
