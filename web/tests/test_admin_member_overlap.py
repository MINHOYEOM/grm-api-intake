"""회원 ∩ 구독자 수 (admin-brevo member_overlap) — 정적 계약 검사.

핵심 불변식: 숫자만 돌려준다(이메일을 응답·감사 기록에 싣지 않는다), 눌렀을 때만 호출한다.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FN = (ROOT / "supabase/functions/admin-brevo/index.ts").read_text(encoding="utf-8")
JS = (ROOT / "web/assets/admin.js").read_text(encoding="utf-8")
HTML = (ROOT / "web/templates/admin.html").read_text(encoding="utf-8")


class MemberOverlapContract(unittest.TestCase):
    def _block(self):
        m = re.search(r"async function memberOverlap.*?\n}\n", FN, re.S)
        self.assertIsNotNone(m)
        return m.group(0)

    def test_response_has_counts_only(self):
        body = re.search(r"const body = \{(.*?)\};", self._block(), re.S).group(1)
        for key in ("members", "subscribers", "overlap", "member_only", "truncated"):
            self.assertIn(key, body)
        self.assertNotIn("email", body)

    def test_audit_logs_no_email(self):
        line = [l for l in FN.splitlines() if "brevo.member_overlap" in l][0]
        self.assertNotIn("email", line.replace("member_overlap", ""))

    def test_blacklisted_not_counted_and_cached(self):
        blk = self._block()
        self.assertIn("emailBlacklisted", blk)
        self.assertIn("overlapCache", blk)

    def test_button_only_no_polling(self):
        self.assertIn('id="grm-overlap-run"', HTML)
        self.assertEqual(JS.count("admin-brevo?action=member_overlap"), 1)
        self.assertNotIn("setInterval", JS[JS.index("grm-overlap-run"):JS.index("grm-overlap-run") + 900])


if __name__ == "__main__":
    unittest.main()
