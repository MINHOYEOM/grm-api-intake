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
        self.assertIn('<p class="oc-more">그 밖에 1건</p>', h)
        self.assertIn("결재할 서류가 5건", h)
        self.assertIn("발행·운영 열기", h)

    def test_four_states_are_words_not_only_color(self):
        h = self.out["sample"]
        for word in ("정상", "주의", "측정 필요"):
            self.assertIn('<i class="oc-dot" aria-hidden="true"></i>' + word, h)
        self.assertIn('<i class="oc-dot" aria-hidden="true"></i>문제', self.out["bad"])

    def test_seat_states_follow_db_fields(self):
        h = self.out["sample"]
        for word in ("일하는 중", "막힘", "놀고 있음", "검사 대기", "기록 없음", "쉬는 중", "일 없음"):
            self.assertIn('<span class="oc-seat-word">' + word + "</span>", h, word)
        s = self.out["seats"]
        self.assertEqual(s["oldButNotIdle"], "일 없음", "30일 전이어도 DB 가 idle=false 면 놀고 있음이 아니다")
        self.assertEqual(s["idleRecent"], "놀고 있음", "DB 가 idle=true 면 그대로 따른다")
        self.assertEqual(s["noRecordFlag"], "기록 없음")
        self.assertEqual(s["nullDays"], "기록 없음", "기록이 없으면 일수를 지어내지 않는다")
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
        self.assertIn('<span class="oc-memo-tag">기록 없음</span>', h)

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

    def test_density_ok_teams_fold_alert_teams_open(self):
        """대표 피드백(11-06 "빽빽하다"): 정상 팀은 한 줄로 접고, 알림이 있는 팀만 펼친다."""
        h = self.out["sample"]
        for slug in ("brief", "strategy", "verification"):
            self.assertRegex(h, r'<details class="oc-team oc-t-ok[^"]*" data-team="%s">' % slug, slug)
        for slug in ("translation", "dev", "quality", "education", "situation-room"):
            self.assertRegex(h, r'<details class="oc-team oc-t-(warn|bad|unknown)[^"]*" open data-team="%s">' % slug, slug)
        # 정상 팀의 접힌 한 줄 = 이름 + 상태 + (결재 칩) + 인원 — 이유·목표선 없음
        brief = h[h.index('data-team="brief"'):]
        brief = brief[:brief.index("</summary>")]
        self.assertNotIn("oc-worst", brief)

    def test_density_goals_hidden_except_worst_line(self):
        h = self.out["sample"]
        # 팀 머리엔 가장 나쁜 목표 하나만 가는 선으로, 목표 전부는 접힘 안
        quality = h[h.index('data-team="quality"'):]
        quality = quality[:quality.index('data-team="infra"')]
        self.assertEqual(quality.count('class="oc-worst"'), 1)
        self.assertIn('oc-bar oc-b-warn is-thin', quality)
        self.assertIn("용어 연결률(가장 낮은 유형 483) 50.2% · 목표 70%", quality)
        self.assertIn('<details class="oc-goals-fold"><summary>목표 4개</summary>', quality)
        # 목표 목록은 '목표 N개' 접힘이나 자리 펼침(팀 목표) 안에만 있다 — 밖에 늘 펼쳐진 목록이 없다
        total = h.count('<ul class="oc-goals"')
        folded = len(re.findall(r'목표 \d+개</summary><ul class="oc-goals"', h))
        in_seat = h.count('<h5>팀 목표</h5><ul class="oc-goals"')
        self.assertGreater(folded, 0)
        self.assertEqual(total, folded + in_seat)

    def test_density_seat_is_name_and_one_word(self):
        h = self.out["sample"]
        for btn in re.findall(r'<button type="button" class="oc-seat-btn".*?</button>', h, flags=re.S):
            text = re.sub(r"<[^>]+>", "|", btn)
            parts = [p for p in text.split("|") if p.strip()]
            self.assertEqual(len(parts), 3, f"자리 = 이니셜·이름·한 마디만: {parts}")
            self.assertIn(parts[2], ("일하는 중", "막힘", "놀고 있음", "검사 대기", "기록 없음", "쉬는 중", "일 없음"))
        # 할 일 제목은 자리에 안 보이고 펼침에서만
        seat_html = "".join(re.findall(r'<button type="button" class="oc-seat-btn".*?</button>', h, flags=re.S))
        self.assertNotIn("11-09 호 카드 고르기", seat_html)
        self.assertIn("11-09 호 카드 고르기", h)

    def test_density_ceo_office(self):
        h = self.out["sample"]
        ceo = h[:h.index('class="oc-floor"')]
        main = ceo[ceo.index('class="oc-sumline"'):ceo.index('class="oc-sum-more"')]
        self.assertEqual(re.findall(r'<span class="oc-sum[^"]*">([^<]+) <b>', main), ["문제", "주의", "결재"])
        self.assertIn("<summary>숫자 더 보기</summary>", ceo)
        memo = ceo[ceo.index("<h3>눈여겨볼 것</h3>"):]
        visible = memo[:memo.index('class="oc-memo-more"')]
        self.assertEqual(visible.count("<li class="), 5, "눈여겨볼 것은 5개까지")
        self.assertIn("<summary>더 보기 1</summary>", memo)
        self.assertNotIn("다가오는 날짜", ceo, "날짜는 이번 주 일 접힘으로")
        self.assertIn("<h4>다가오는 날짜</h4>", h)

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

    def test_no_control_characters(self):
        # 패치 스크립트가 "\25B8" 같은 CSS 이스케이프를 파이썬 8진 이스케이프로 먹으면 제어 문자가 박힌다
        for name, src in (("admin.html", self.html), ("admin-ops.js", self.ops)):
            bad = [hex(ord(c)) for c in src if ord(c) < 32 and c not in "\n\r\t"]
            self.assertEqual(bad, [], f"{name}: 제어 문자")

    def test_renderer_does_not_recompute_state(self):
        # 판정 경계(3일·1.5배·주의선 비교)를 JS 에 두지 않는다 — DB(123) 단일 원천
        self.assertNotRegex(self.ops, r"last_active_days\s*[<>]=?\s*\d")
        self.assertNotRegex(self.ops, r"1\.5|period_hours|warn_at\s*[<>]|bad_at\s*[<>]")
        self.assertNotIn("Date.now", self.ops, "렌더러는 시계를 보지 않는다(서버 시각만)")


if __name__ == "__main__":
    unittest.main()
