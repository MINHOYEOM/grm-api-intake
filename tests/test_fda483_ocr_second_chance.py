"""[CLI 판독 2026-10-28] 본문은 있는데 관찰 0건인 483 — 전면 재판독 엔진은 tesseract CLI.

`_fetch_fda483_pdf_text` 의 판독 사다리:
  텍스트층 → (빈 쪽) 내장 OCR → [관찰 0건] **전면 재판독 `_full_ocr_fallback`**
  전면 재판독은 tesseract CLI 가 우선이고, CLI 가 없는 러너에서만 PyMuPDF 내장 엔진
  (`force_all`, #1235)으로 같은 일을 한다.

픽스처 `fda483_104952_scan.pdf` 는 FOIA 열람실의 실제 공개 483(media 104952, 1쪽 스캔본,
HPLC 실험실 기록)이다. 텍스트층이 없고, PyMuPDF 내장 엔진(`get_textpage_ocr`)은 쪽 머리의
양식 블록을 통째로 놓쳐(`fda483_104952_builtin_ocr.txt` — "DURING AN INSPECTION … OBSERVED"
마커가 없다) 관찰이 0건이다. 같은 fast 모델·같은 DPI 로 CLI 가 읽으면 마커와 함께 쪽 전체가
나와 관찰이 추출된다(`fda483_104952_tesseract_cli.txt`). 두 텍스트 파일은 실제 엔진 출력을
기록한 것이다 — 단위 테스트는 엔진 호출만 그 기록으로 대체하고 판단·PDF 파싱·관찰 추출은
실제 코드로 돈다. 실엔진 테스트는 tesseract 가 있는 환경(로컬·CI 러너)에서 그대로 돈다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PDF_PATH = os.path.join(FIXTURES, "fda483_104952_scan.pdf")
BUILTIN_TEXT_PATH = os.path.join(FIXTURES, "fda483_104952_builtin_ocr.txt")
CLI_TEXT_PATH = os.path.join(FIXTURES, "fda483_104952_tesseract_cli.txt")
URL = "https://www.fda.gov/media/104952/download"
EXPECTED_HEADING = "Laboratory records do not include a complete record of all data"

try:
    import fitz  # noqa: F401
    HAVE_FITZ = True
except ImportError:  # pragma: no cover — requirements.txt 가 PyMuPDF 를 고정한다
    HAVE_FITZ = False


def _read(path: str, mode: str = "r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        return fh.read()


class _FakeTesseract:
    """`subprocess.run` 대역 — 호출을 세고 기록된 CLI 출력을 돌려준다."""

    def __init__(self, stdout: str):
        self.stdout = stdout
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        self.kwargs.append(kwargs)
        return subprocess.CompletedProcess(args, 0, self.stdout.encode("utf-8"), b"")


@unittest.skipUnless(HAVE_FITZ, "PyMuPDF 필요")
class CliFallbackTest(unittest.TestCase):
    """내장 엔진 출력은 기록값으로 고정하고(환경 무관), 전면 재판독의 엔진 선택을 검증한다."""

    def setUp(self):
        self._budget = dict(f._OCR_BUDGET)
        f._OCR_BUDGET.update({"remaining": 200, "used": 0})
        f.reset_ocr_health()
        self._env = patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        f._OCR_BUDGET.update(self._budget)
        f.reset_ocr_health()

    def _fetch(self, fake, which: str | None = "/usr/bin/tesseract"):
        data = _read(PDF_PATH, "rb")
        builtin = _read(BUILTIN_TEXT_PATH)
        self.builtin_full_calls: list[bool] = []

        def _builtin_full(data, max_chars, force_all=False):
            self.builtin_full_calls.append(force_all)
            return builtin, "pdf-ok-ocr"
        with patch.object(f, "http_get_bytes", lambda *a, **k: data), \
                patch.object(f, "_ocr_483_pdf_text",
                             lambda data, max_chars=None: (builtin, "pdf-ok-ocr")), \
                patch.object(f, "_ocr_483_pdf_text_uncounted", _builtin_full), \
                patch.object(f.shutil, "which", lambda name: which), \
                patch.object(f.subprocess, "run", fake):
            return f._fetch_fda483_pdf_text(URL)

    def test_fixture_premise_builtin_zero_cli_recovers(self):
        """전제 고정: 내장 엔진 판독은 관찰 0건, CLI 판독은 관찰이 나온다."""
        self.assertEqual(f._extract_483_observations_from_text(_read(BUILTIN_TEXT_PATH)), [])
        cli = f._extract_483_observations_from_text(_read(CLI_TEXT_PATH))
        self.assertTrue(cli and cli[0]["deficiency"].startswith(EXPECTED_HEADING))

    def test_full_reread_uses_cli_and_recovers(self):
        """★수정 전 코드에서 실패한다 — 내장 엔진 전면 재판독도 0건이라 관찰이 없었다."""
        fake = _FakeTesseract(_read(CLI_TEXT_PATH))
        text, status = self._fetch(fake)
        observations = f._extract_483_observations_from_text(text)
        self.assertEqual(status, "pdf-ok-ocr")
        self.assertEqual(len(observations), 1)
        heading = observations[0]["deficiency"]
        self.assertTrue(heading.startswith(EXPECTED_HEADING))
        self.assertTrue(f._is_publishable_deficiency(heading))
        self.assertEqual(f._deficiency_garble(heading)[0], 0)
        self.assertEqual(len(fake.calls), 1, "1쪽 문서 — CLI 호출 1회")
        self.assertEqual(self.builtin_full_calls, [], "CLI 가 있으면 내장 엔진 전면 재판독은 돌지 않는다")
        health = f.ocr_health()
        self.assertEqual((health["reocr_tried"], health["reocr_adopted"]), (1, 1))
        self.assertEqual(health["pages_used"], 1, "CLI 판독도 같은 OCR 페이지 예산을 쓴다")

    def test_cli_zero_does_not_rerun_builtin(self):
        """CLI 로 0건이면 내장 엔진을 또 돌리지 않는다(벤치: CLI ⊇ 내장 — 예산만 두 배)."""
        self._fetch(_FakeTesseract("unreadable smudge\n"))
        self.assertEqual(self.builtin_full_calls, [])

    def test_cli_invocation_contract(self):
        """stdin→stdout · eng · 1차 OCR 과 같은 DPI · 단일 스레드(OpenMP 경합 방지) · 쪽당 타임아웃."""
        fake = _FakeTesseract(_read(CLI_TEXT_PATH))
        self._fetch(fake)
        args, kwargs = fake.calls[0], fake.kwargs[0]
        self.assertEqual(args[1:3], ["stdin", "stdout"])
        self.assertIn("eng", args)
        self.assertEqual(args[args.index("--dpi") + 1], str(f.FDA483_OCR_DPI))
        self.assertEqual(kwargs["env"].get("OMP_THREAD_LIMIT"),
                         os.environ.get("OMP_THREAD_LIMIT") or "1")
        self.assertEqual(kwargs["timeout"], f.FDA483_REOCR_PAGE_TIMEOUT)

    def test_cli_without_observations_keeps_original(self):
        """CLI 로도 0건이면 원래 텍스트·status 를 그대로 둔다(산출 축소 금지)."""
        text, status = self._fetch(_FakeTesseract("unreadable smudge\n"))
        self.assertEqual(status, "pdf-ok-ocr")
        self.assertEqual(text, f.normalize_pdf_ligatures(_read(BUILTIN_TEXT_PATH)))
        self.assertEqual(f.ocr_health()["reocr_adopted"], 0)

    def test_missing_cli_binary_falls_back_to_builtin_engine(self):
        """CLI 가 없는 러너는 #1235 의 내장 엔진 전면 재판독으로 같은 일을 한다."""
        fake = _FakeTesseract(_read(CLI_TEXT_PATH))
        text, _status = self._fetch(fake, which=None)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.builtin_full_calls, [True])
        self.assertEqual(f._extract_483_observations_from_text(text), [])

    def test_exhausted_budget_skips_cli(self):
        f._OCR_BUDGET.update({"remaining": 0})
        fake = _FakeTesseract(_read(CLI_TEXT_PATH))
        self._fetch(fake)
        self.assertEqual(fake.calls, [])

    def test_ocr_disabled_skips_cli(self):
        fake = _FakeTesseract(_read(CLI_TEXT_PATH))
        with patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "false"}):
            self._fetch(fake)
        self.assertEqual(fake.calls, [])

    def test_engine_timeout_keeps_original(self):
        def _hang(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        text, _status = self._fetch(_hang)
        self.assertEqual(text, f.normalize_pdf_ligatures(_read(BUILTIN_TEXT_PATH)))


class CliBangRepairTest(unittest.TestCase):
    """CLI 가 `l` 을 `!` 로 읽어 표제가 `!` 에서 잘린 채 공개 게이트를 통과하던 결함(실측 2건)."""

    def test_truncated_headings_from_benchmark_are_restored(self):
        # 191121 · 161809 CLI 판독 원문 그대로 — 수리 전에는 `!` 에서 잘린 표제가 게이트를 통과했다.
        cases = [
            ("OBSERVATION 2\nThe responsibilities and procedures applicable to the quality contro! "
             "unit are not fully followed. Specifically, Your Quality Unit failed to establish.",
             "The responsibilities and procedures applicable to the quality control unit are not "
             "fully followed."),
            ("OBSERVATION 10\nYour firm has not adequately qualified the critical utility used for the!\n"
             "manufacturing processes in the Fill-Finish manufacturing facility. Specifically, none.",
             "Your firm has not adequately qualified the critical utility used for the manufacturing "
             "processes in the Fill-Finish manufacturing facility."),
        ]
        for raw, expected in cases:
            broken = f._extract_483_observations_from_text(raw)[0]["deficiency"]
            self.assertTrue(broken.endswith("!"), "전제: 수리 전 표제는 `!` 에서 잘린다")
            self.assertTrue(f._is_publishable_deficiency(broken), "전제: 잘린 표제가 게이트를 통과한다")
            repaired = f._extract_483_observations_from_text(f._repair_cli_bang(raw))
            self.assertEqual(repaired[0]["deficiency"], expected)

    def test_only_lowercase_continuation_is_touched(self):
        """문장이 이어지는(뒤가 소문자) `!` 만 고친다 — 대문자·숫자가 이어지면 그대로 둔다."""
        for text in ("Michae! R. Shanks", "Batch! O", "Stop! The firm", "no bang here"):
            self.assertEqual(f._repair_cli_bang(text), text)
        self.assertEqual(f._repair_cli_bang("quality contro! unit"), "quality control unit")
        self.assertEqual(f._repair_cli_bang("used for the! manufacturing"),
                         "used for the manufacturing")

    def test_repair_is_applied_to_cli_output(self):
        data = _read(PDF_PATH, "rb")
        f._OCR_BUDGET.update({"remaining": 5, "used": 0})
        try:
            with patch.object(f.shutil, "which", lambda name: "/usr/bin/tesseract"), \
                    patch.object(f.subprocess, "run",
                                 _FakeTesseract("the quality contro! unit")):
                text, _status = f._reocr_483_pdf_text(data, 1000)
        finally:
            f._OCR_BUDGET.update({"remaining": 200, "used": 0})
        self.assertIn("quality control unit", text)


@unittest.skipUnless(HAVE_FITZ, "PyMuPDF 필요")
class CliFallbackScopeTest(unittest.TestCase):
    """관찰이 1건이라도 나오는 문서는 전면 재판독에 닿지 않는다 — 정상 문서 출력 바이트 불변."""

    def test_document_with_observations_never_reaches_cli(self):
        def _boom(*a, **k):
            raise AssertionError("관찰이 있는 문서에서 CLI 판독이 돌면 안 된다")
        real = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\nOBSERVATION 1\n"
                "Procedures designed to prevent microbiological contamination of sterile drug "
                "products are not established and followed.\nSpecifically, the firm did not.")
        self.assertTrue(f._extract_483_observations_from_text(real))
        with patch.object(f, "http_get_bytes", lambda *a, **k: b"%PDF-1.7"), \
                patch.object(f, "_read_fda483_pdf_text", lambda *a, **k: (real, "pdf-ok")), \
                patch.object(f, "_reocr_483_pdf_text", _boom), \
                patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
            self.assertEqual(f._fetch_fda483_pdf_text(URL), (real, "pdf-ok"))


@unittest.skipUnless(HAVE_FITZ and shutil.which("tesseract"), "tesseract CLI 가 있는 환경에서만")
class CliFallbackRealEngineTest(unittest.TestCase):
    """실제 엔진 — 내장 엔진 판독은 실제로 0건이고, CLI 전면 재판독이 실제로 관찰을 되찾는다(CI 러너에서도 돈다)."""

    def test_real_engines_builtin_fails_cli_recovers(self):
        data = _read(PDF_PATH, "rb")
        budget = dict(f._OCR_BUDGET)
        f._OCR_BUDGET.update({"remaining": 10, "used": 0})
        f.reset_ocr_health()
        started = time.monotonic()
        try:
            with patch.object(f, "http_get_bytes", lambda *a, **k: data), \
                    patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
                text, status = f._fetch_fda483_pdf_text(URL)
            pages = f._OCR_BUDGET["used"]
            health = f.ocr_health()
        finally:
            f._OCR_BUDGET.update(budget)
            f.reset_ocr_health()
        # 러너 실측용 — CI 로그에 시간이 남는다(성공 판정에는 쓰지 않는다).
        print(f"\n[CLI 판독 실엔진] 1쪽 문서 · OCR {pages}쪽(빈 쪽 내장 + 전면 CLI) · "
              f"{time.monotonic() - started:.2f}s · status={status}", file=sys.stderr)
        observations = f._extract_483_observations_from_text(text)
        self.assertTrue(observations, "내장 엔진 판독만으로는 관찰이 0건이다 — CLI 단계가 회수해야 한다")
        self.assertTrue(observations[0]["deficiency"].startswith(EXPECTED_HEADING))
        self.assertEqual(status, "pdf-ok-ocr")
        self.assertEqual(health.get("reocr_adopted"), 1, "전면 재판독(CLI)이 회수했다")
        self.assertEqual(pages, 2, "빈 쪽 1차 OCR 1쪽 + CLI 전면 재판독 1쪽(내장 전면 재판독은 안 돈다)")


if __name__ == "__main__":
    unittest.main()
