"""[2차 판독 2026-10-28] 본문은 받았는데 관찰 0건인 483 — 이미지 페이지를 tesseract CLI 로 다시 읽는다.

배경(벤치마크 실측): findings 0건·본문 있음 47건 중 28건은 FDA 가 Adobe "Paper Capture" 로
OCR 해 넣은 **깨진 텍스트층**을 가진 스캔본이었다. 글자가 많아 `_needs_ocr` 가 "본문 있음"으로
판정하므로 우리 OCR 은 한 번도 돌지 않았고, 관찰 번호 "1." 이 "I." 로 깨져 파서가 0건을 냈다.

픽스처 `fda483_164192_paper_capture.pdf` 는 FOIA 열람실의 실제 공개 483(media 164192, 1쪽,
Recipharm Monts)이다. 텍스트층에는 "I. Records of the inspections…" 가 들어 있어 관찰이
0건이고, 같은 쪽 이미지를 tesseract CLI(fast 모델, 300dpi)로 읽으면 "1. Records…" 가 나온다.
`fda483_164192_tesseract_cli.txt` 는 그 CLI 출력을 기록한 것이다(CI 러너에는 tesseract 가 없어
엔진 호출만 기록값으로 대체한다 — 판단 로직·PDF 파싱·관찰 추출은 전부 실제 코드가 돈다).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f  # noqa: E402
import collect_mfds_gmp_inspection as g  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PDF_PATH = os.path.join(FIXTURES, "fda483_164192_paper_capture.pdf")
CLI_TEXT_PATH = os.path.join(FIXTURES, "fda483_164192_tesseract_cli.txt")
URL = "https://www.fda.gov/media/164192/download"
EXPECTED_HEADING = "Records of the inspections of automatic, mechanical or electronic equipment"

try:
    import fitz  # noqa: F401
    HAVE_FITZ = True
except ImportError:  # pragma: no cover — requirements.txt 가 PyMuPDF 를 고정한다
    HAVE_FITZ = False


def _read_pdf() -> bytes:
    with open(PDF_PATH, "rb") as fh:
        return fh.read()


def _recorded_cli_text() -> str:
    with open(CLI_TEXT_PATH, encoding="utf-8") as fh:
        return fh.read()


class _FakeTesseract:
    """`subprocess.run` 대역 — 호출을 세고 기록된 CLI 출력을 돌려준다."""

    def __init__(self, stdout: str):
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, self.stdout.encode("utf-8"), b"")


@unittest.skipUnless(HAVE_FITZ, "PyMuPDF 필요")
class SecondChanceOcrTest(unittest.TestCase):

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

    def _fetch(self, fake: _FakeTesseract, which: str | None = "/usr/bin/tesseract"):
        data = _read_pdf()
        with patch.object(f, "http_get_bytes", lambda *a, **k: data), \
                patch.object(f.shutil, "which", lambda name: which), \
                patch.object(f.subprocess, "run", fake):
            return f._fetch_fda483_pdf_text(URL)

    def test_fixture_text_layer_is_the_defect(self):
        """전제 고정: FDA 텍스트층만으로는 관찰이 0건이고, 그래서 1차 OCR 도 돌지 않는다."""
        native, status = g._extract_pdf_text(_read_pdf(), max_chars=f.FDA483_TEXT_MAX_CHARS)
        self.assertEqual(status, "pdf-ok")
        self.assertFalse(f._needs_ocr(native), "Paper Capture 텍스트층은 '본문 있음'으로 판정된다")
        self.assertEqual(f._extract_483_observations_from_text(native), [])

    def test_garbled_paper_capture_layer_is_reread_and_observation_recovered(self):
        """★수정 전 코드에서 실패한다 — 텍스트층을 그대로 돌려줘 관찰이 0건이었다."""
        fake = _FakeTesseract(_recorded_cli_text())
        text, status = self._fetch(fake)
        observations = f._extract_483_observations_from_text(text)
        self.assertEqual(status, "pdf-ok-ocr")
        self.assertEqual(len(observations), 1)
        self.assertTrue(observations[0]["deficiency"].startswith(EXPECTED_HEADING))
        self.assertTrue(f._is_publishable_deficiency(observations[0]["deficiency"]))
        self.assertEqual(f._deficiency_garble(observations[0]["deficiency"])[0], 0)
        self.assertEqual(len(fake.calls), 1, "이미지 페이지 1쪽만 다시 읽는다")
        health = f.ocr_health()
        self.assertEqual((health["reocr_tried"], health["reocr_adopted"]), (1, 1))
        self.assertEqual(health["pages_used"], 1, "2차 판독도 같은 OCR 페이지 예산을 쓴다")

    def test_cli_invocation_contract(self):
        """stdin→stdout · eng · 1차 OCR 과 같은 DPI · 단일 스레드(OpenMP 경합 방지)."""
        seen: dict = {}

        def _run(args, **kwargs):
            seen["args"], seen["env"] = list(args), kwargs.get("env") or {}
            seen["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(args, 0, _recorded_cli_text().encode(), b"")

        self._fetch(_run)
        args = seen["args"]
        self.assertEqual(args[1:3], ["stdin", "stdout"])
        self.assertIn("eng", args)
        self.assertEqual(args[args.index("--dpi") + 1], str(f.FDA483_OCR_DPI))
        self.assertEqual(seen["env"].get("OMP_THREAD_LIMIT"), os.environ.get("OMP_THREAD_LIMIT") or "1")
        self.assertEqual(seen["timeout"], f.FDA483_REOCR_PAGE_TIMEOUT)

    def test_reread_without_observations_keeps_original_text(self):
        """다시 읽어도 0건이면 원래 텍스트·status 를 그대로 둔다(산출 축소 금지)."""
        native, _ = g._extract_pdf_text(_read_pdf(), max_chars=f.FDA483_TEXT_MAX_CHARS)
        text, status = self._fetch(_FakeTesseract("unreadable smudge\n"))
        self.assertEqual(status, "pdf-ok")
        self.assertEqual(text, f.normalize_pdf_ligatures(native))
        self.assertEqual(f.ocr_health()["reocr_adopted"], 0)

    def test_missing_cli_binary_degrades_to_original(self):
        fake = _FakeTesseract(_recorded_cli_text())
        text, status = self._fetch(fake, which=None)
        self.assertEqual(status, "pdf-ok")
        self.assertEqual(fake.calls, [])

    def test_exhausted_budget_skips_reread(self):
        f._OCR_BUDGET.update({"remaining": 0})
        fake = _FakeTesseract(_recorded_cli_text())
        _text, status = self._fetch(fake)
        self.assertEqual(status, "pdf-ok")
        self.assertEqual(fake.calls, [])

    def test_ocr_disabled_skips_reread(self):
        fake = _FakeTesseract(_recorded_cli_text())
        with patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "false"}):
            _text, status = self._fetch(fake)
        self.assertEqual(status, "pdf-ok")
        self.assertEqual(fake.calls, [])

    def test_engine_timeout_keeps_original(self):
        def _hang(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        _text, status = self._fetch(_hang)
        self.assertEqual(status, "pdf-ok")


@unittest.skipUnless(HAVE_FITZ, "PyMuPDF 필요")
class SecondChanceScopeTest(unittest.TestCase):
    """관찰이 1건이라도 나오는 문서는 2차 판독에 닿지 않는다 — 정상 문서 출력 바이트 불변."""

    def setUp(self):
        f.reset_ocr_health()

    def test_document_with_observations_never_rereads(self):
        def _boom(*a, **k):
            raise AssertionError("관찰이 있는 문서에서 2차 판독이 돌면 안 된다")
        real = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\nOBSERVATION 1\n"
                "Procedures designed to prevent microbiological contamination of sterile drug "
                "products are not established and followed.\nSpecifically, the firm did not.")
        self.assertTrue(f._extract_483_observations_from_text(real))
        with patch.object(f, "_reocr_483_pdf_text", _boom), \
                patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
            self.assertEqual(f._second_chance_ocr(b"%PDF", real, "pdf-ok", 200000),
                             (real, "pdf-ok"))
        self.assertEqual(f.ocr_health()["reocr_tried"], 0)

    def test_empty_text_is_not_second_chance_territory(self):
        """본문이 아예 없는 경우는 1차 OCR 경로의 몫이다(사유 코드 보존)."""
        def _boom(*a, **k):
            raise AssertionError("빈 본문에서 2차 판독이 돌면 안 된다")
        with patch.object(f, "_reocr_483_pdf_text", _boom), \
                patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
            self.assertEqual(f._second_chance_ocr(b"%PDF", "", "scan-ocr-empty", 10),
                             ("", "scan-ocr-empty"))

    def test_text_only_pdf_has_nothing_to_reread(self):
        """이미지가 없는 PDF(디지털 원본)는 OCR 하지 않는다 — 관찰 0건은 파서의 몫이다."""
        import fitz
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "No observations here at all.")
        data = doc.tobytes()
        with patch.object(f.shutil, "which", lambda name: "/usr/bin/tesseract"), \
                patch.object(f.subprocess, "run",
                             lambda *a, **k: self.fail("이미지 없는 페이지를 OCR 했다")):
            self.assertEqual(f._reocr_483_pdf_text(data, 1000), ("", "reocr-no-image"))


@unittest.skipUnless(HAVE_FITZ and shutil.which("tesseract"), "tesseract CLI 가 있는 환경에서만")
class SecondChanceRealEngineTest(unittest.TestCase):
    """실제 엔진 판독(로컬·OCR 워크플로 러너). CI 에는 tesseract 가 없어 건너뛴다."""

    def test_real_cli_recovers_observation(self):
        data = _read_pdf()
        f._OCR_BUDGET.update({"remaining": 5, "used": 0})
        with patch.object(f, "http_get_bytes", lambda *a, **k: data), \
                patch.dict(os.environ, {"ENABLE_FDA_483_OCR": "true"}):
            text, status = f._fetch_fda483_pdf_text(URL)
        self.assertEqual(status, "pdf-ok-ocr")
        observations = f._extract_483_observations_from_text(text)
        self.assertTrue(observations[0]["deficiency"].startswith(EXPECTED_HEADING))


if __name__ == "__main__":
    unittest.main()
