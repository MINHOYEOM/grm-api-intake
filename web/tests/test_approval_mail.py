import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import approval_mail as am  # noqa: E402


def run(c, s="completed"):
    return {"name": "x", "status": s, "conclusion": c}


class ApprovalMailTest(unittest.TestCase):
    def test_week_label(self):
        self.assertEqual(am.week_label("publish/brief-2026-11-02"), "W45")
        self.assertEqual(am.week_label("publish/brief-2026-11-09"), "W46")
        with self.assertRaises(ValueError):
            am.week_label("feat/x")

    def test_subject_and_body(self):
        s, h = am.build_message("W46", "https://grm-solutions.com/admin/")
        self.assertEqual(s, "[GRM 결재] W46 브리프 발행 — 미리보기 확인")
        self.assertEqual(h.count("<p style=\"margin:0 0 10px"), 3)
        self.assertEqual(h.count("<a "), 1)
        self.assertIn("/admin/", h)

    def test_recipient_single_only(self):
        self.assertEqual(am.resolve_recipient(" a@b.co "), "a@b.co")
        for bad in ["", "a@b.co,c@d.co", "a@b.co;c@d.co", "a@b.co c@d.co", "nope"]:
            with self.assertRaises(ValueError):
                am.resolve_recipient(bad)

    def test_checks_ready(self):
        self.assertTrue(am.checks_ready([run("success"), run("skipped")])[0])
        self.assertFalse(am.checks_ready([])[0])
        self.assertFalse(am.checks_ready([run("success"), run(None, "in_progress")])[0])
        self.assertFalse(am.checks_ready([run("success"), run("failure")])[0])
        own = {"name": "approval-mail", "status": "in_progress", "conclusion": None}
        self.assertTrue(am.checks_ready([run("success"), own])[0])

    def test_send_failure_raises(self):
        with mock.patch.object(am.requests, "post", return_value=mock.Mock(status_code=401)):
            with self.assertRaises(RuntimeError):
                am.send_email("k", "n", "s@x.co", "a@b.co", "s", "h")


if __name__ == "__main__":
    unittest.main()
