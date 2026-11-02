"""가입 화면 '뉴스레터도 받기' + 기존 회원 1회 안내 — 정적 계약 검사.

원칙: 옵트인만(기본 해제·체크한 사람만 구독 제출), 확인 메일(더블 옵트인)은 Brevo 폼 경로 그대로,
일괄 등록 0, DB 변경 0, 영문판 문안 짝(en.json).
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "web/assets/reactions.js").read_text(encoding="utf-8")
BASE = (ROOT / "web/templates/base.html").read_text(encoding="utf-8")
EN = json.loads((ROOT / "web/data/i18n/en.json").read_text(encoding="utf-8"))

KEYS = [
    "주간 뉴스레터도 받기 (선택)",
    "체크하면 구독 확인 메일이 별도로 가며, 그 메일의 버튼을 눌러야 구독됩니다.",
    "주간 뉴스레터, 받아보시겠어요?",
    "매주 월요일 한국어로 보내드려요. 구독 확인 메일의 버튼을 눌러야 구독됩니다.",
    "확인 메일 받기",
    "괜찮아요",
    "확인 메일을 {email} 로 보냈습니다. 메일의 구독 확인 버튼을 눌러 주세요.",
]


class OptinContract(unittest.TestCase):
    def test_checkbox_default_unchecked(self):
        m = re.search(r'<input type="checkbox"[^>]*>', JS)
        self.assertIsNotNone(m)
        self.assertNotIn("checked", m.group(0))
        # 모드를 오가면 다시 해제
        self.assertIn(".checked = false", JS)

    def test_subscribe_only_when_checked(self):
        self.assertIn("if (wantsNews) submitSubscribe(email)", JS)
        self.assertEqual(len(re.findall(r"submitSubscribe\(", JS)), 3)  # 정의 + 가입(체크) + 안내(버튼)

    def test_subscribe_uses_brevo_form_double_optin(self):
        self.assertIn('data-sub="{{ newsletter_form_action', BASE)
        fn = JS[JS.index("function submitSubscribe"):JS.index("var nudgeEl")]
        self.assertIn('"no-cors"', fn)
        self.assertIn('"EMAIL"', fn)
        self.assertNotIn("admin-brevo", JS)
        self.assertNotIn("api.brevo.com", JS)

    def test_nudge_is_once_and_never_bulk(self):
        fn = JS[JS.index("function maybeNudge"):JS.index("function setBusy")]
        for guard in ("newsletter_prompted", "SUB_OK_KEY", "NUDGE_KEY"):
            self.assertIn(guard, fn)
        self.assertIn('_evt === "SIGNED_IN"', JS)

    def test_checkbox_not_stretched_by_form_input_rule(self):
        self.assertIn(".grm-login-form .grm-login-optin input{width:16px", BASE)

    def test_en_keys_present(self):
        for k in KEYS:
            self.assertIn(k, EN, k)
            self.assertTrue(EN[k].strip())
            self.assertIn(k, JS, k)


if __name__ == "__main__":
    unittest.main()
