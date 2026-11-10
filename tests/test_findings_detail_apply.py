"""483 구체 내용(상세 원문·국문) outbox 적용·검증 (2026-11-10 지식 그래프 1단계)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import findings_detail_apply as app  # noqa: E402
import findings_detail_qa as qa  # noqa: E402

EN = ("Specifically, your firm released lot 23-0412 on 3/27/2023 without testing per SOP-QC-012 "
      "and 21 CFR 211.84(d). The [redacted] HEPA filter was not certified.")
KO = ("구체적으로, 귀사는 로트 23-0412를 3/27/2023에 SOP-QC-012 및 21 CFR 211.84(d)에 따른 시험 없이 "
      "출하했다. [redacted] HEPA 필터는 인증되지 않았다.")


def _item(**over):
    it = {"finding_id": "f1", "finding_text_md5": app.md5_text("Heading."), "detail_text": EN, "detail_text_ko": KO}
    it.update(over)
    return it


class QaTest(unittest.TestCase):
    def test_good_translation_passes(self):
        self.assertEqual(qa.check(EN, KO), [])

    def test_missing_number_fails(self):
        self.assertTrue(any(p.startswith("numbers_missing") for p in qa.check(EN, KO.replace("211.84", "211"))))

    def test_hanja_and_no_hangul_fail(self):
        self.assertIn("hanja", qa.check(EN, KO + " 試驗"))
        self.assertIn("no_hangul", qa.check(EN, EN))

    def test_redacted_count_and_codes(self):
        self.assertTrue(any(p.startswith("redacted_count") for p in qa.check(EN, KO.replace("[redacted] ", ""))))
        self.assertTrue(any(p.startswith("codes_missing") for p in qa.check(EN, KO.replace("HEPA", "헤파"))))

    def test_length_ratio(self):
        self.assertTrue(any(p.startswith("length_ratio") for p in qa.check(EN, "구체적으로 23-0412 3/27/2023")))


class OutboxFilesQaTest(unittest.TestCase):
    """커밋된 detail_outbox 배치는 전부 검증 규칙을 통과해야 한다(CI 관문)."""

    def test_committed_batches_pass_qa(self):
        files = sorted((ROOT / "translations" / "detail_outbox").glob("*.json"))
        failures = {}
        for p in files:
            items = json.loads(p.read_text(encoding="utf-8"))
            for it in items:
                self.assertEqual(app.validate_item(it), "", f"{p.name} {it.get('finding_id')}")
            res = qa.check_items(items)
            if res:
                failures[p.name] = dict(list(res.items())[:5])
        self.assertEqual(failures, {})


class _Send:
    def __init__(self, live_text="Heading.", patched=1):
        self.live_text, self.patched, self.calls = live_text, patched, []

    def __call__(self, verb, base, key, *, params, json_body=None, **_):
        self.calls.append((verb, params, json_body))
        if verb == "get":
            return 200, [{"finding_text": self.live_text}], ""
        return 200, [{}] * self.patched, ""


class ApplyTest(unittest.TestCase):
    def _run(self, items, send, dry=False):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "b.json")
            p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            return app.run([p], "https://x", "k", dry_run=dry, send=send)

    def test_patches_both_columns_in_one_request(self):
        send = _Send()
        rep = self._run([_item()], send)
        self.assertEqual(rep["succeeded"], 1)
        verb, params, body = send.calls[-1]
        self.assertEqual(verb, "patch")
        self.assertEqual(set(body), {"detail_text", "detail_text_ko"})

    def test_changed_heading_is_not_written(self):
        send = _Send(live_text="Different heading.")
        rep = self._run([_item()], send)
        self.assertEqual(rep["matched_zero"], 1)
        self.assertEqual([c[0] for c in send.calls], ["get"])

    def test_invalid_items_are_not_sent(self):
        send = _Send()
        rep = self._run([_item(detail_text_ko=""), _item(finding_text_md5="x")], send)
        self.assertEqual(rep["invalid"], 2)
        self.assertEqual(send.calls, [])

    def test_dry_run_sends_nothing(self):
        send = _Send()
        rep = self._run([_item()], send, dry=True)
        self.assertEqual(send.calls, [])
        self.assertEqual(rep["mode"], "dry_run")


if __name__ == "__main__":
    unittest.main()
