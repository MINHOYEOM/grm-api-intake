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
    "(선택) 주간 브리프·새 기능 안내 메일 받기",
    "매주 월요일 규제 소식(브리프)과 사이트 새 기능 안내를 이메일로 받습니다. 가입 확인 코드를 입력하면 구독도 함께 완료되며, 모든 메일 하단의 수신거부 링크로 언제든 해지할 수 있습니다.",
    "뉴스레터 구독도 이 코드 입력으로 함께 완료됩니다.",
    "주간 뉴스레터 구독",
    "매주 월요일 규제 소식과 새 기능 안내를 보냅니다. 누르면 바로 구독되며, 메일 하단 수신거부 링크로 언제든 해지할 수 있습니다.",
    "구독하기",
    "받지 않음",
    "구독이 완료되었습니다. 매주 월요일에 발송합니다.",
    "이전에 수신거부한 주소입니다. 페이지 아래 구독 폼에서 다시 신청할 수 있습니다.",
    "구독을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
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

    def test_progress_record_unchanged_and_optin_in_separate_key(self):
        # 가입 진행 상태(이메일+시각, 30분 TTL)는 기존 계약 그대로 — 체크 여부는 별도 키.
        self.assertIn("JSON.stringify({ email: email, ts: Date.now() })", JS)
        self.assertIn('var OPTIN_KEY = "grm-signup-optin-v1";', JS)
        self.assertNotIn("o: !!", JS)
        clear = JS[JS.index("function clearSignupProgress"):JS.index("function pwField")]
        self.assertIn("removeItem(OPTIN_KEY)", clear)

    def test_consent_survives_reload_at_code_step(self):
        # 코드 입력 단계에서 새로고침해도 동의가 유지된다: 구독 여부는 sessionStorage 가 아니라
        # 서버에 남긴 user_metadata.newsletter_optin 이 정한다(signUp 기록 → verifyOtp 응답의 메타데이터 →
        # 서버 재확인). 클라이언트 키는 안내 문구만 좌우한다.
        self.assertIn("newsletter_optin: true", JS)
        confirm = JS[JS.index('type: "signup" }).then'):JS.index("재설정 1단계: 코드 발송")]
        self.assertIn("vmeta.newsletter_optin === true", confirm)
        self.assertNotIn("readOptinFlag", confirm)
        self.assertNotIn("pendingOptin", confirm)
        self.assertIn("meta.newsletter_optin !== true", FN)

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
        for gone in ("확인 메일 받기", "주간 뉴스레터도 받기 (선택)", "(선택) 주간 뉴스레터 수신에 동의합니다"):
            self.assertNotIn(gone, EN)


class SignupConsentContract(unittest.TestCase):
    """가입 화면 필수 동의(만 14세·약관/처리방침) + 선택 구독 + 비밀번호 요건."""

    def test_required_consents_block_submit(self):
        for token in ("grm-c-age", "grm-c-terms", "만 14세 이상만 가입할 수 있습니다.",
                      "이용약관과 개인정보처리방침에 동의해 주세요."):
            self.assertIn(token, JS)
        # 필수 검사는 signUp 호출보다 앞선다.
        self.assertLess(JS.index("!(ageCb.checked && termsCb.checked)"), JS.index("sb.auth.signUp("))

    def test_consent_recorded_in_user_metadata(self):
        for token in ("age14_confirmed = true", "meta.terms_ver = TERMS_VER", "meta.terms_agreed_at"):
            self.assertIn(token, JS)

    def test_legal_links_and_optional_newsletter(self):
        self.assertIn('legalHref("terms/")', JS)
        self.assertIn('legalHref("privacy/")', JS)
        # 구독 체크는 선택 — 필수 검사식에 optinCb 가 끼지 않는다.
        self.assertNotIn("optinCb.checked)) {", JS[JS.index("var ageCb"):JS.index("if (firstBad)")])

    def test_password_rule_and_en_keys(self):
        self.assertIn("var PW_MIN = 8;", JS)
        for k in ("(필수) 만 14세 이상입니다", "(필수) {terms}과 {privacy}에 동의합니다",
                  "비밀번호는 8자 이상, 영문과 숫자를 함께 넣어 주세요.", "계속 둘러보기"):
            self.assertIn(k, EN, k)
            self.assertIn(k, JS, k)


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


class NudgeStatusGate(unittest.TestCase):
    """안내 전 '이미 구독 중?' 상태 확인 — 꺼짐 기본, 켜도 실패는 기존 동작."""

    def test_gate_off_adds_no_bytes_and_default_env_off(self):
        # 꺼진 빌드는 속성 자체가 없다(조건부 출력) — 골든 불변의 근거.
        self.assertIn('{% if member_nudge_status %} data-nudge-status="1"{% endif %}', BASE)
        RENDER = (ROOT / "web/render.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("GRM_MEMBER_NUDGE_STATUS", "").strip() == "1"', RENDER)
        WFD = (ROOT / ".github/workflows/grm-web-deploy.yml").read_text(encoding="utf-8")
        self.assertIn("GRM_MEMBER_NUDGE_STATUS: ${{ vars.GRM_MEMBER_NUDGE_STATUS }}", WFD)

    def test_client_skips_nudge_for_subscribed_and_fails_open(self):
        fn = JS[JS.index("function maybeNudge"):JS.index("function showNudge")]
        self.assertIn("if (!NUDGE_STATUS) { showNudge(user); return; }", fn)
        self.assertIn('"subscribed" || st === "blocked_unsubscribed"', fn)
        # 네트워크 실패 → 빈 결과 → 안내 띄움(fail-open): 체인 끝의 catch 가 then 바로 앞에 있어야 한다
        self.assertRegex(fn, r"\.catch\(function \(\) \{ return \{\}; \}\)\s*\.then\(function \(b\)")
        self.assertNotIn("email", fn.split("JSON.stringify")[1].split("}")[0])  # 본문에 이메일 없음

    def test_server_status_is_read_only(self):
        blk = FN[FN.index('"status"'):FN.index("const source")]
        self.assertNotIn('method: "POST"', blk)
        self.assertNotIn("updateUserById", blk)
        self.assertIn("emailBlacklisted", blk)


class SubscriberBandHidden(unittest.TestCase):
    """[2026-11-10] 하단 구독 띠가 구독자에게도 떴다 — 로그인 회원은 상태로, 메일 링크 방문은 utm 으로 숨긴다."""

    def test_logged_in_member_status_sets_band_key_once_per_session(self):
        i = JS.index("function syncSubscribed()")
        blk = JS[i:JS.index("\n  }\n", i)]
        self.assertIn("NUDGE_STATUS", blk)                     # 게이트 꺼지면 요청 0
        self.assertIn("lsGet(SUB_OK_KEY)", blk)                # 이미 표시된 브라우저는 요청 0
        self.assertIn("SUB_CHECK_KEY", blk)                    # 세션당 한 번
        self.assertIn('action: "status"', blk)                 # 읽기 전용 조회만
        self.assertNotIn("isAdminEmail", blk)                  # 관리자도 띠는 내린다
        self.assertIn("lsSet(SUB_OK_KEY)", blk)
        self.assertIn('getElementById("grm-cta")', blk)
        self.assertIn("syncSubscribed();", JS[JS.index("sb.auth.getSession().then"):])

    def test_email_link_visit_marks_subscriber_but_not_forward(self):
        i = BASE.index("var KEY='grm-cta-until'")
        blk = BASE[i:i + 600]
        self.assertIn("utm_medium=email(&|$)", blk)
        self.assertIn("localStorage.setItem(DONE,'1')", blk)
        self.assertNotIn("forward(", blk)


if __name__ == "__main__":
    unittest.main()
