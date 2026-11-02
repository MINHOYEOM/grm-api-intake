"""가입 화면 '뉴스레터 수신 동의' + 기존 회원 1회 안내 — 정적 계약 검사.

원칙: 옵트인만(기본 해제·체크한 사람만), 구독 등록은 이메일 확인 뒤 서버(newsletter-optin)가
JWT 본인 이메일로만, 동의 근거 기록, 응답·로그에 이메일 0, 일괄 등록 0, DB 변경 0, 영문 문안 짝.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "web/assets/reactions.js").read_text(encoding="utf-8")
BASE = (ROOT / "web/templates/base.html").read_text(encoding="utf-8")
EN = json.loads((ROOT / "web/data/i18n/en.json").read_text(encoding="utf-8"))

FN = (ROOT / "supabase/functions/newsletter-optin/index.ts").read_text(encoding="utf-8")
CFG = (ROOT / "supabase/config.toml").read_text(encoding="utf-8")
WF = (ROOT / ".github/workflows/grm-admin-backend-deploy.yml").read_text(encoding="utf-8")

KEYS = [
    "(선택) 주간 뉴스레터 수신에 동의합니다",
    "매주 월요일 규제 소식(브리프)과 사이트 새 기능 안내를 이메일로 받습니다. 가입 확인 코드를 입력하면 구독도 함께 완료되며, 모든 메일 하단의 수신거부 링크로 언제든 해지할 수 있어요.",
    "뉴스레터 구독도 이 코드 입력으로 함께 완료됩니다.",
    "주간 뉴스레터, 받아보시겠어요?",
    "매주 월요일 규제 소식과 새 기능 안내를 보내드려요. 누르면 바로 구독되며, 메일 하단 수신거부 링크로 언제든 해지할 수 있어요.",
    "구독하기",
    "괜찮아요",
    "구독이 완료되었습니다. 매주 월요일에 보내드려요.",
    "이전에 수신거부한 주소예요. 페이지 아래 구독 폼에서 다시 신청할 수 있어요.",
    "구독을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
]


class ClientContract(unittest.TestCase):
    def test_checkbox_default_unchecked_and_reset(self):
        m = re.search(r'<input type="checkbox"[^>]*>', JS)
        self.assertIsNotNone(m)
        self.assertNotIn("checked", m.group(0))
        self.assertIn(".checked = false", JS)

    def test_consent_text_names_scope_and_unsubscribe(self):
        # 수신 범위(브리프 + 새 기능 안내)와 수신거부 방법이 한·영 문안에 모두 있다.
        ko = KEYS[1]
        self.assertIn("규제 소식", ko)
        self.assertIn("새 기능 안내", ko)
        self.assertIn("수신거부", ko)
        en = EN[ko].lower()
        self.assertIn("announcements of new site features", en)
        self.assertIn("unsubscribe", en)

    def test_no_client_side_email_registration(self):
        # 브라우저가 이메일을 보내 구독시키는 경로는 없다(밴드 폼 POST·Brevo 직접 호출 제거).
        self.assertNotIn("submitSubscribe", JS)
        self.assertNotIn("api.brevo.com", JS)
        self.assertNotIn("admin-brevo", JS)
        fn = JS[JS.index("function subscribeServer"):JS.index("var nudgeEl")]
        self.assertIn("/functions/v1/newsletter-optin", fn)
        self.assertNotIn("EMAIL", fn)
        self.assertNotIn("email:", fn)

    def test_signup_subscribes_only_after_email_confirmed(self):
        # signUp 은 기록(메타데이터)만, 구독 요청은 verifyOtp 성공 뒤 또는 세션이 곧바로 생긴 경우뿐.
        self.assertEqual(len(re.findall(r'subscribeServer\(', JS)), 4)  # 정의 + 즉시세션 + verifyOtp 뒤 + 안내카드
        vo = JS[JS.index('type: "signup" }).then'):]
        self.assertLess(vo.index("subscribeServer("), vo.index("재설정 1단계"))
        self.assertIn("newsletter_optin === true", JS)

    def test_nudge_once_never_bulk(self):
        fn = JS[JS.index("function maybeNudge"):JS.index("function setBusy")]
        for guard in ("newsletter_prompted", "SUB_OK_KEY", "NUDGE_KEY"):
            self.assertIn(guard, fn)
        self.assertIn('_evt === "SIGNED_IN"', JS)

    def test_checkbox_not_stretched_by_form_input_rule(self):
        self.assertIn(".grm-login-form .grm-login-optin input{width:16px", BASE)

    def test_en_keys_present_and_used(self):
        for k in KEYS:
            self.assertIn(k, EN, k)
            self.assertTrue(EN[k].strip())
            self.assertIn(k, JS, k)
        for gone in ("확인 메일 받기", "주간 뉴스레터도 받기 (선택)"):
            self.assertNotIn(gone, EN)


class ServerContract(unittest.TestCase):
    def test_email_comes_from_jwt_not_body(self):
        self.assertIn("auth.getUser(token)", FN)
        self.assertIn("user.email_confirmed_at", FN)
        self.assertNotIn("body.email", FN)

    def test_responses_and_logs_have_no_email(self):
        self.assertNotIn("console.", FN)
        for line in FN.splitlines():
            if "jsonResponse(" in line:
                self.assertNotRegex(line, r"\bemail\b\s*[,:}]", line)

    def test_consent_recorded_and_fallback(self):
        for token in ("SOURCE", "CONSENT_AT", "CONSENT_VER", "newsletter_consent", "res.status === 400", "create(false)"):
            self.assertIn(token, FN)

    def test_respects_unsubscribe_and_en_fallback(self):
        self.assertIn("emailBlacklisted", FN)
        self.assertIn("blocked_unsubscribed", FN)
        self.assertIn("ko_fallback", FN)
        self.assertIn("GRM_NEWSLETTER_LIST_ID_EN", FN)

    def test_signup_source_requires_recorded_optin(self):
        self.assertIn('meta.newsletter_optin !== true', FN)

    def test_wired_into_config_and_deploy(self):
        self.assertIn("[functions.newsletter-optin]", CFG)
        self.assertIn("supabase/functions/newsletter-optin/index.ts", WF)
        self.assertIn("supabase functions deploy newsletter-optin", WF)
        self.assertIn("GRM_NEWSLETTER_LIST_ID_EN", WF)


if __name__ == "__main__":
    unittest.main()
