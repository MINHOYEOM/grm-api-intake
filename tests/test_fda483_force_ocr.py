"""[강제 판독 2026-11-25] FDA483_FORCE_OCR — 깨진 글자층이 있는 스캔 483 을 쪽 전체 다시 판독.

실측(dump-text run 45574640061): 189485·191183 은 PDF 에 깨진 글자층이 있어 400dpi 로 돌려도 그 글자층을
그대로 읽었다(OCR 0회). 강제 판독은 1회성 회수 워크플로의 `force_ocr` 입력에서만 켜진다.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.fda.gov/media/189485/download"
GARBLED = "Specifically, A. Wh;le obsecv;\\;h;~puc setup and fimn!UJle,aUons on thi,,;;,;mn Une fg"
CLEAN = "Specifically, A. While observing setup and manipulations on this line, the operator"


class ForceOcrTest(unittest.TestCase):
    def setUp(self):
        self.calls = {"reocr": 0}

    def _reocr(self, text):
        def fake(data, max_chars):
            self.calls["reocr"] += 1
            return text, "pdf-ok-reocr"
        return fake

    def _run(self, env, layer_text, reocr_text, obs=lambda t: []):
        with patch.dict(os.environ, env, clear=False), \
                patch.object(f, "http_get_bytes", lambda *a, **k: b"%PDF-1.7"), \
                patch.object(f, "_read_fda483_pdf_text", lambda *a, **k: (layer_text, "pdf-ok")), \
                patch.object(f, "_reocr_483_pdf_text", self._reocr(reocr_text)), \
                patch.object(f, "_extract_483_observations_from_text", obs):
            return f._fetch_fda483_pdf_text(URL)

    def test_default_off_keeps_text_layer(self):
        """env 가 없으면 지금과 같다 — 관찰이 있는 문서는 다시 판독하지 않는다."""
        text, status = self._run({"ENABLE_FDA_483_OCR": "true", "FDA483_FORCE_OCR": ""}, GARBLED, CLEAN,
                                 obs=lambda t: [1])
        self.assertEqual((text, status), (GARBLED, "pdf-ok"))
        self.assertEqual(self.calls["reocr"], 0)

    def test_force_rereads_even_with_text_layer(self):
        text, status = self._run({"ENABLE_FDA_483_OCR": "true", "FDA483_FORCE_OCR": "1"}, GARBLED, CLEAN,
                                 obs=lambda t: [1])
        self.assertEqual(self.calls["reocr"], 1)
        self.assertEqual(text, CLEAN)
        self.assertEqual(status, "pdf-ok-reocr")

    def test_force_never_returns_fewer_observations(self):
        """다시 읽은 쪽이 관찰을 덜 내면 글자층을 그대로 둔다(산출 축소 금지)."""
        obs = lambda t: [1, 2] if t == GARBLED else [1]  # noqa: E731
        text, _ = self._run({"ENABLE_FDA_483_OCR": "true", "FDA483_FORCE_OCR": "true"}, GARBLED, CLEAN, obs=obs)
        self.assertEqual(text, GARBLED)

    def test_force_empty_reread_keeps_text_layer(self):
        text, status = self._run({"ENABLE_FDA_483_OCR": "true", "FDA483_FORCE_OCR": "1"}, GARBLED, "   ")
        self.assertEqual((text, status), (GARBLED, "pdf-ok"))

    def test_force_respects_ocr_disabled(self):
        text, _ = self._run({"ENABLE_FDA_483_OCR": "false", "FDA483_FORCE_OCR": "1"}, GARBLED, CLEAN,
                            obs=lambda t: [1])
        self.assertEqual(text, GARBLED)
        self.assertEqual(self.calls["reocr"], 0)


class ForcedDumpTest(unittest.TestCase):
    """[11-26] force_ocr + dump-text — 채택되지 않은 강제 판독 전문도 <doc>.forced.txt 로 남긴다(191183 실측)."""

    def _dump(self, env, layer_text, reocr_text, obs):
        import tempfile
        import backfill_483_ocr_recovery as rec
        d = tempfile.mkdtemp()
        with patch.dict(os.environ, env, clear=False), \
                patch.object(f, "http_get_bytes", lambda *a, **k: b"%PDF-1.7"), \
                patch.object(f, "_read_fda483_pdf_text", lambda *a, **k: (layer_text, "pdf-ok")), \
                patch.object(f, "_reocr_483_pdf_text", lambda data, mc: (reocr_text, "pdf-ok-reocr")), \
                patch.object(f, "_extract_483_observations_from_text", obs):
            rows = rec.dump_texts(["fda483-191183"], d)
        files = sorted(os.listdir(d))
        read = {n: Path(d, n).read_text(encoding="utf-8") for n in files}
        return rows, read

    def test_rejected_forced_read_is_kept_beside_text_layer(self):
        obs = lambda t: [1, 2] if t == GARBLED else [1]  # noqa: E731 — 강제 판독이 관찰을 덜 낸다 → 불채택
        rows, read = self._dump({"ENABLE_FDA_483_OCR": "true", "FDA483_FORCE_OCR": "1"}, GARBLED, CLEAN, obs)
        self.assertEqual(read["fda483-191183.txt"], GARBLED)
        self.assertEqual(read["fda483-191183.forced.txt"], CLEAN)
        self.assertEqual((rows[0]["forced_adopted"], rows[0]["forced_chars"]), (False, len(CLEAN)))

    def test_adopted_forced_read_writes_one_file(self):
        rows, read = self._dump({"ENABLE_FDA_483_OCR": "true", "FDA483_FORCE_OCR": "1"}, GARBLED, CLEAN,
                                lambda t: [1])
        self.assertEqual(sorted(read), ["fda483-191183.txt"])
        self.assertEqual(read["fda483-191183.txt"], CLEAN)
        self.assertTrue(rows[0]["forced_adopted"])

    def test_default_dump_unchanged(self):
        """force_ocr 가 꺼져 있으면 파일·보고 행 모양이 종전과 같다."""
        rows, read = self._dump({"ENABLE_FDA_483_OCR": "true", "FDA483_FORCE_OCR": ""}, GARBLED, CLEAN,
                                lambda t: [1])
        self.assertEqual(sorted(read), ["fda483-191183.txt"])
        self.assertEqual(sorted(rows[0]), ["chars", "doc", "status", "url"])

    def test_forced_state_does_not_leak_between_docs(self):
        import tempfile
        import backfill_483_ocr_recovery as rec
        f._LAST_FORCED_READ.update(text="stale", status="x", adopted=False)
        d = tempfile.mkdtemp()
        rows = rec.dump_texts(["fda483-1234"], d, fetch_text=lambda url: ("T", "pdf-ok"))
        self.assertNotIn("forced_chars", rows[0])
        self.assertFalse(os.path.exists(os.path.join(d, "fda483-1234.forced.txt")))


class ForceOcrWorkflowTest(unittest.TestCase):
    WF = ROOT / ".github" / "workflows" / "grm-fda483-ocr-recovery.yml"

    def test_input_defaults_false_and_never_on_schedule(self):
        wf = self.WF.read_text(encoding="utf-8")
        m = re.search(r"\n      force_ocr:\n(?:        .*\n)+", wf)
        self.assertIsNotNone(m, "force_ocr 입력이 없다")
        self.assertIn("type: boolean", m.group(0))
        self.assertIn("default: false", m.group(0))
        env = re.search(r"FDA483_FORCE_OCR: \$\{\{(.*)\}\}", wf)
        self.assertIsNotNone(env)
        self.assertIn("github.event_name != 'schedule'", env.group(1))


if __name__ == "__main__":
    unittest.main()
