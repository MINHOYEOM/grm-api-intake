"""운영 센터 홈 화면(PR3) — 렌더러 단위 시험(Node) + admin 배선 정적 계약.

설계서 2026-11-06 §3. 화면은 읽기 전용이고, 상태 판정은 ops_home()(123) 이 한다.
렌더러는 그 필드를 글자·색으로 옮길 뿐 — 일수로 다시 판정하면 DB 와 두 벌이 된다.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
OPS_JS = WEB / "assets" / "admin-ops.js"
ADMIN_JS = WEB / "assets" / "admin.js"
ADMIN_HTML = WEB / "templates" / "admin.html"
FIXTURE = ROOT / "tests" / "fixtures" / "ops_home_sample.json"

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def _run():
    res = subprocess.run(["node", str(ROOT / "tests/js/ops_home_render.js"), str(OPS_JS), str(FIXTURE)],
                         capture_output=True, text=True, encoding="utf-8", timeout=20)
    if res.returncode:
        raise AssertionError(res.stderr)
    return json.loads(res.stdout)


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class OpsHomeRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _run()

    def test_sample_draws_ceo_office_rooms_and_seats(self):
        h = self.out["sample"]
        self.assertIn("대표실", h)
        self.assertIn("결재 서류", h)
        for room in ("종합상황실", "검증실", "리서치", "플랫폼", "고객", "브리프편집팀", "데이터품질팀"):
            self.assertIn(room, h)
        self.assertEqual(h.count('class="oc-seat '), 15, "자리 15개(시드 팀원 수)")
        self.assertEqual(h.count('class="oc-avatar"'), 15)
        # 결재 서류 = 발행 승인 1 + 결정 3, 넷째부터는 '그 밖에'
        self.assertEqual(len(re.findall(r'<li class="oc-paper[ "]', h)), 4)
        self.assertIn("그 밖에 1건이 더 기다리고 있습니다", h)
        self.assertIn("결재할 서류가 5건", h)
        self.assertIn("발행·운영 열기", h)

    def test_four_states_are_words_not_only_color(self):
        h = self.out["sample"]
        for word in ("정상", "주의", "측정 필요"):
            self.assertIn('<i class="oc-dot" aria-hidden="true"></i>' + word, h)
        self.assertIn('<i class="oc-dot" aria-hidden="true"></i>문제', self.out["bad"])

    def test_seat_states_follow_db_fields(self):
        h = self.out["sample"]
        for word in ("일하는 중", "막힘", "놀고 있음", "검사 대기", "활동 기록 없음", "쉬는 중", "일 없음"):
            self.assertIn('<span class="oc-seat-word">' + word + "</span>", h, word)
        s = self.out["seats"]
        self.assertEqual(s["oldButNotIdle"], "일 없음", "30일 전이어도 DB 가 idle=false 면 놀고 있음이 아니다")
        self.assertEqual(s["idleRecent"], "놀고 있음", "DB 가 idle=true 면 그대로 따른다")
        self.assertEqual(s["noRecordFlag"], "활동 기록 없음")
        self.assertEqual(s["nullDays"], "활동 기록 없음", "기록이 없으면 일수를 지어내지 않는다")
        self.assertEqual(s["working"], "일하는 중")
        self.assertEqual(s["blocked"], "막힘")
        self.assertEqual(s["review"], "검사 대기")
        self.assertEqual(s["paused"], "쉬는 중")
        self.assertEqual(s["free"], "일 없음")

    def test_stale_and_unknown_are_distinguished(self):
        h = self.out["sample"]
        self.assertIn("12일 전 값 · 낡음", h)
        self.assertIn("oc-bar oc-b-unknown is-stale", h)
        self.assertIn("측정 필요 · 값 없음", h)
        self.assertIn("측정 필요 · 목표 미정", h)
        self.assertIn('<span class="oc-memo-tag">낡음</span>', h)
        self.assertIn('<span class="oc-memo-tag">놀고 있음</span>', h)
        self.assertIn('<span class="oc-memo-tag">활동 기록 없음</span>', h)

    def test_goal_bar_has_target_line(self):
        h = self.out["sample"]
        self.assertRegex(h, r'<span class="oc-bar oc-b-warn" aria-hidden="true"><b style="width:[\d.]+%"></b><i style="left:[\d.]+%"></i></span>')

    def test_read_only_no_approve_or_snooze_buttons(self):
        for name in ("sample", "calm", "bad"):
            h = self.out[name]
            buttons = re.findall(r"<button[^>]*>([^<]*)</button>", h)
            for label in buttons:
                self.assertNotIn("승인", label, f"{name}: 승인 버튼은 PR4 에서 — {label}")
                self.assertNotIn("보류", label, f"{name}: 보류 버튼은 PR4 에서 — {label}")
            self.assertNotIn("disabled", h, "비활성 버튼 자리도 만들지 않는다")

    def test_design_bans(self):
        src = OPS_JS.read_text(encoding="utf-8")
        for name in ("sample", "calm", "bad"):
            h = self.out[name]
            self.assertNotRegex(h, r"gradient|box-shadow|text-shadow", name)
            self.assertNotRegex(h, r'class="[^"]*\bti\b', f"{name}: Tabler 아이콘 금지")
            self.assertIsNone(EMOJI_RE.search(h), f"{name}: 이모지 금지")
        self.assertNotRegex(src, r"gradient|box-shadow")

    def test_escape_and_safe_links(self):
        h = self.out["bad"]
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertNotIn("<img src=x", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertNotIn("javascript:", self.out["sample"], "http(s) 아닌 근거 링크는 버린다")
        self.assertIn('href="https://github.com/MINHOYEOM/grm-api-intake/pull/1"', self.out["sample"])

    def test_calm_day_is_quiet(self):
        h = self.out["calm"]
        self.assertIn("이상 없음", h)
        self.assertIn("결재할 서류가 없습니다", h)
        self.assertIn("문제나 주의할 팀은 없습니다", h)
        self.assertNotIn('class="oc-sum is-warn"', h)
        self.assertNotIn('class="oc-sum is-bad"', h)
        # 정상 부문은 접어 둘 수 있다(foldOk)
        self.assertRegex(h, r'<details class="oc-room oc-wing oc-t-ok" data-team="research">')

    def test_empty_inputs(self):
        self.assertEqual(self.out["nullData"], "")
        self.assertIn("아직 조직 자료가 없습니다", self.out["emptyTree"])

    def test_kst_date_and_clock(self):
        self.assertEqual(self.out["dayLabel"], ["11-09(월)", "11-13(금)"])
        self.assertEqual(self.out["clock"], "09:10")


class OpsHomeWiringTest(unittest.TestCase):
    """admin 배선 — 기존 rpc 관례·i18n 제외·캐시 버전·옛 홈 접힘."""

    @classmethod
    def setUpClass(cls):
        cls.html = ADMIN_HTML.read_text(encoding="utf-8")
        cls.js = ADMIN_JS.read_text(encoding="utf-8")
        cls.ops = OPS_JS.read_text(encoding="utf-8")

    def test_home_panel_has_ops_center_first_and_legacy_folded(self):
        home = self.html[self.html.index('data-panel="home"'):]
        home = home[:home.index("</section>\n\n        <section")]
        self.assertLess(home.index('id="grm-oc"'), home.index('id="grm-home-legacy"'))
        self.assertIn('<details class="admin-fold oc-legacy" id="grm-home-legacy">', home)
        # 옛 숫자 타일·3칸·결재함·조직도는 접힘 안으로(삭제는 PR7)
        legacy = home[home.index('id="grm-home-legacy"'):]
        for marker in ('id="grm-kpis"', 'id="grm-home-status"', 'id="grm-home-inbox"', 'class="org-chart"'):
            self.assertIn(marker, legacy)

    def test_script_order_and_cache_version(self):
        a = self.html.index('src="/assets/admin-ops.js?v={{ adminopsjs_ver }}"')
        b = self.html.index('src="/assets/admin.js?v={{ adminjs_ver }}"')
        self.assertLess(a, b, "렌더러가 admin.js 보다 먼저 실려야 한다")
        render_src = (WEB / "render.py").read_text(encoding="utf-8")
        self.assertIn('env.globals["adminopsjs_ver"] = _asset_ver("admin-ops.js")', render_src)

    def test_i18n_excludes_operator_asset(self):
        sys.path.insert(0, str(WEB))
        import grm_i18n
        self.assertIn("admin-ops.js", grm_i18n.EXCLUDED_ASSETS)
        self.assertNotIn("admin-ops.js", [p.name for p in grm_i18n.asset_files()])

    def test_admin_js_calls_ops_home_once_via_supabase_rpc(self):
        self.assertIn('state.client.rpc("ops_home")', self.js)
        self.assertEqual(self.js.count('rpc("ops_home")'), 1)
        self.assertIn("loadOpsHome()", self.js[self.js.index("function refreshAll()"):])
        self.assertIn('"42501"', self.js)
        self.assertIn("다시 시도", self.js)
        self.assertIn("분 전입니다", self.js)

    def test_renderer_does_not_recompute_state(self):
        # 판정 경계(3일·1.5배·주의선 비교)를 JS 에 두지 않는다 — DB(123) 단일 원천
        self.assertNotRegex(self.ops, r"last_active_days\s*[<>]=?\s*\d")
        self.assertNotRegex(self.ops, r"1\.5|period_hours|warn_at\s*[<>]|bad_at\s*[<>]")
        self.assertNotIn("Date.now", self.ops, "렌더러는 시계를 보지 않는다(서버 시각만)")


if __name__ == "__main__":
    unittest.main()
