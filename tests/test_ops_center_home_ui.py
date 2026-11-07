"""운영 센터 홈 화면 — 렌더러 단위 시험(Node) + admin 배선 정적 계약.

구성은 대표 확정 시안(2026-11-06): 한 줄 요약 → [대표실 · 결재 서류 | 우리 회사 숫자] → 팀과 직원.
화면은 읽기 전용이고, 상태 판정은 ops_home()(123) 이 한다 — 렌더러는 그 필드를 글자 태그로 옮길 뿐.
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
TAGS = ("진행 중", "확인 대기", "지연", "배정 없음")
# 조잡한 표현 금지(대표 지시 11-06) — '배정 없음'·'배정된 업무가 없는 직원' 으로 쓴다. 내부 필드명 idle 은 그대로.
BANNED_WORDS = ("놀고 있음", "노는 중", "일 없음", "놀고있음")


def _run():
    res = subprocess.run(["node", str(ROOT / "tests/js/ops_home_render.js"), str(OPS_JS), str(FIXTURE)],
                         capture_output=True, text=True, encoding="utf-8", timeout=20)
    if res.returncode:
        raise AssertionError(res.stderr)
    return json.loads(res.stdout)


def _home_panel(html):
    home = html[html.index('data-panel="home"'):]
    return home[:home.index("</section>\n\n        <section")]


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class OpsHomeRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _run()

    def test_summary_sentence(self):
        s = self.out["sample"]["summary"]
        self.assertEqual(s, "결재할 서류가 <b>5건</b> 있고, 주의가 필요한 팀이 <em>3곳</em>입니다. "
                            "배정된 업무가 없는 직원은 1명입니다.")
        calm = self.out["calm"]["summary"]
        self.assertEqual(calm, "결재할 서류는 없고, 주의가 필요한 팀은 없습니다. 배정된 업무가 없는 직원은 없습니다.")
        self.assertIn("그중 1곳은 문제입니다.", self.out["bad"]["summary"])

    def test_docs_up_to_three_plus_more(self):
        d = self.out["sample"]["docs"]
        self.assertEqual(len(re.findall(r'<div class="oc-doc[ "]', d)), 4, "발행 승인 1 + 결정 3")
        self.assertIn('<div class="oc-more">그 밖에 1건이 더 기다립니다</div>', d)
        self.assertIn('<div class="oc-rec"><b>권장</b> 유지. 공개 전 마지막 확인입니다.</div>', d)
        self.assertIn("<div class=\"oc-m\">승인하면 현행 유지(변경 시 공지 필요)</div>", d)
        self.assertIn("기한 11-09(월) (3일 남음)", d)
        self.assertIn("기한 지남 11-05(목)", d)
        self.assertIn("결재할 서류가 없습니다", self.out["calm"]["docs"])

    def test_decision_buttons(self):
        d = self.out["sample"]["docs"]
        # 결정 3건에만 버튼 3개씩(승인·보류·3일 뒤). 발행 승인 줄은 이동 링크뿐(승인은 발행·운영 화면에서).
        self.assertEqual(d.count('data-oc-decide="approve"'), 3)
        self.assertEqual(d.count('data-oc-decide="snooze" data-oc-snooze="tomorrow"'), 3)
        self.assertEqual(d.count('data-oc-decide="snooze" data-oc-snooze="3d"'), 3)
        publish = d[:d.index('data-oc-doc=')]
        self.assertNotIn("data-oc-decide", publish)
        self.assertIn('class="oc-pri" data-oc-decide="approve" data-oc-id="2"', d)
        labels = re.findall(r'data-oc-decide="approve"[^>]*>([^<]+)</button>', d)
        self.assertEqual(labels, ["유지", "승인", "승인"], "질문형은 권장 답(유지), 승인 요청은 '승인'")
        self.assertEqual(self.out["decide"]["labels"], ["승인", "유지", "권장대로", "권장대로"])
        for name in ("sample", "calm", "bad"):
            for label in re.findall(r"<button[^>]*>([^<]*)</button>", self.out[name]["all"]):
                self.assertNotIn("반려", label, "반려 없음(D5)")
                self.assertNotIn("거절", label)
            self.assertNotIn("disabled", self.out[name]["all"], "평소엔 잠그지 않는다")

    def test_decision_states(self):
        busy = self.out["decide"]["busy"]
        doc2 = busy[busy.index('data-oc-doc="2"'):]
        doc2 = doc2[:doc2.index('data-oc-doc="1"')]
        self.assertEqual(doc2.count(" disabled>"), 3, "처리 중엔 그 서류 버튼 3개 모두 잠김(더블 탭 방지)")
        self.assertIn(">처리 중</button>", doc2)
        self.assertEqual(busy.count(" disabled>"), 3, "다른 서류는 잠기지 않는다")
        done = self.out["decide"]["done"]
        self.assertIn('<div class="oc-doc is-done" data-oc-doc="2">', done)
        self.assertIn('<div class="oc-done" role="status">승인됨 · 비서실이 이어서 진행합니다</div>', done)
        self.assertEqual(done.count('data-oc-decide="approve"'), 2, "처리된 서류는 버튼이 사라진다")
        err = self.out["decide"]["error"]
        self.assertIn('<div class="oc-err" role="status">처리하지 못했습니다. 잠시 뒤 다시 눌러 주세요.</div>', err)
        self.assertEqual(err.count('data-oc-decide="approve"'), 3, "오류면 다시 누를 수 있다")
        self.assertEqual(self.out["bindCalls"], [[2, "approve", None], [1, "snooze", "3d"]], "잠긴 버튼은 무시")

    def test_rooms_and_member_lines(self):
        t = self.out["sample"]["teams"]
        self.assertEqual(t.count('<section class="oc-card oc-room"'), 5, "방 = 실 2 + 부문 3")
        self.assertEqual(t.count('<details class="oc-line'), 15, "직원 15명 모두 한 줄로")
        for tag in TAGS:
            self.assertIn('">' + tag + "</span></summary>", t, tag)
        self.assertIn("<h2>팀과 직원</h2>", t)
        # 직원 줄은 접힌 팀 안에 숨지 않는다 — 팀 블록은 <details> 가 아니다
        self.assertNotIn('<details class="oc-team', t)
        # 목표는 팀 카드 안 접힘에만
        total = t.count('<ul class="oc-goals"')
        self.assertEqual(total, len(re.findall(r'<details class="oc-goals-fold"><summary>목표 \d+개', t)))
        self.assertIn("<summary>목표 4개 · 살펴볼 것 1</summary>", t)

    def test_no_floorplan_avatar_or_dot(self):
        for name in ("sample", "calm", "bad"):
            h = self.out[name]["all"]
            for banned in ("oc-avatar", "oc-dot", "oc-seat", "<svg", "oc-offices", "oc-wings"):
                self.assertNotIn(banned, h, f"{name}: {banned}")

    def test_member_tags_follow_db_fields(self):
        m = self.out["members"]
        self.assertEqual(m["oldButNotIdle"], ["배정 없음", "마지막 활동 30일 전", False],
                         "30일 전이어도 DB 가 idle=false 면 강조하지 않는다")
        self.assertEqual(m["idleRecent"], ["배정 없음", "마지막 활동 어제", True], "DB 가 idle=true 면 그대로 따른다")
        self.assertEqual(m["noRecordFlag"][:2], ["배정 없음", "기록 없음"])
        self.assertEqual(m["nullDays"][:2], ["배정 없음", "기록 없음"], "기록이 없으면 일수를 지어내지 않는다")
        self.assertEqual(m["working"][:2], ["진행 중", "카드 고르기"])
        self.assertEqual(m["late"][:2], ["지연", "마감 지난 일 1건 · 원문 확인"])
        self.assertEqual(m["review"][:2], ["확인 대기", "유입 보고"])
        self.assertEqual(m["paused"][:2], ["배정 없음", "일시 정지"])
        self.assertEqual(m["note"][:2], ["배정 없음", "11-05 PR"])
        t = self.out["sample"]["teams"]
        self.assertIn('<details class="oc-line oc-k-none is-idle">', t, "fixture 의 idle 직원(연결 담당)")

    def test_state_words_and_stale(self):
        t = self.out["sample"]["teams"]
        self.assertIn('<span class="oc-pill oc-st-warn">주의</span>', t)
        self.assertIn('<span class="oc-pill oc-st-unknown">측정 필요</span>', t)
        self.assertIn('<span class="oc-pill oc-st-bad">문제</span>', self.out["bad"]["teams"])
        self.assertIn("12일 전 값 · 낡음", t)
        self.assertIn("oc-bar oc-b-unknown is-stale", t)
        self.assertIn("측정 필요 · 값 없음", t)
        self.assertIn("측정 필요 · 목표 미정", t)

    def test_goal_bar_has_target_line(self):
        self.assertRegex(self.out["sample"]["teams"],
                         r'<span class="oc-bar oc-b-warn" aria-hidden="true"><b style="width:[\d.]+%"></b><i style="left:[\d.]+%"></i></span>')

    def test_design_bans(self):
        src = OPS_JS.read_text(encoding="utf-8")
        for name in ("sample", "calm", "bad"):
            h = self.out[name]["all"]
            self.assertNotRegex(h, r"gradient|box-shadow|text-shadow", name)
            self.assertNotRegex(h, r'class="[^"]*\bti\b', f"{name}: 장식 아이콘 금지")
            self.assertIsNone(EMOJI_RE.search(h), f"{name}: 이모지 금지")
        self.assertNotRegex(src, r"gradient|box-shadow")

    def test_escape_and_safe_links(self):
        h = self.out["bad"]["all"]
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertNotIn("<img src=x", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertNotIn("javascript:", self.out["sample"]["all"], "http(s) 아닌 근거 링크는 버린다")
        self.assertIn('href="https://github.com/MINHOYEOM/grm-api-intake/pull/1"', self.out["sample"]["docs"])

    def test_empty_inputs(self):
        self.assertEqual(self.out["nullData"], "")
        self.assertIn("아직 조직 자료가 없습니다", self.out["emptyTree"]["teams"])

    def test_dates(self):
        self.assertEqual(self.out["dayLabel"], ["11-09(월)", "11-13(금)"])
        self.assertEqual(self.out["daysLeft"], [3, 0])
        self.assertEqual(self.out["clock"], "09:10")


class OpsHomeWiringTest(unittest.TestCase):
    """admin 배선 — 기존 rpc 관례·i18n 제외·캐시 버전·숫자 옮겨 오기·옛 홈 접힘."""

    @classmethod
    def setUpClass(cls):
        cls.html = ADMIN_HTML.read_text(encoding="utf-8")
        cls.js = ADMIN_JS.read_text(encoding="utf-8")
        cls.ops = OPS_JS.read_text(encoding="utf-8")

    def test_layout_order(self):
        home = _home_panel(self.html)
        order = [home.index(m) for m in ('id="grm-oc-summary"', 'id="grm-oc-docs"', 'id="grm-oc-kpi-title"',
                                          'id="grm-oc"', 'id="grm-home-legacy"')]
        self.assertEqual(order, sorted(order))
        self.assertIn("대표실 · 결재 서류", home)
        self.assertIn("우리 회사 숫자", home)

    def test_company_numbers_visible_and_ids_unique(self):
        home = _home_panel(self.html)
        kpi = home[home.index('id="grm-oc-kpi-title"'):home.index('id="grm-oc"')]
        self.assertNotIn("<details", kpi, "회사 숫자는 접지 않는다")
        for label, ident in (("구독자", "grm-oc-kpi-subscribers"), ("회원", "grm-oc-kpi-users"),
                             ("이번 주 방문", "grm-oc-kpi-visits"), ("마지막 발송", "grm-oc-kpi-dispatch")):
            self.assertIn(label, kpi)
            self.assertIn(f'id="{ident}"', kpi)
        ids = re.findall(r'\bid="([^"{]+)"', self.html)
        dup = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dup, [], f"중복 id: {dup}")
        # 옛 홈 숫자 칸은 접힘 안에 그대로('우리 회사 숫자'의 원천). 옛 조직도·결재함은 PR7 에서 걷었다.
        legacy = home[home.index('id="grm-home-legacy"'):]
        for marker in ('id="grm-kpis"', 'id="grm-kpi-users"', 'id="grm-home-status"'):
            self.assertIn(marker, legacy)

    def test_old_org_chart_and_inbox_removed(self):
        # PR7: 옛 조직도 마크업·org-* CSS·결재함 칸·loadOpsReports(097 ops_reports 읽기)는 남기지 않는다.
        # ops_reports 표·DB 는 그대로 — 화면에서 읽지 않을 뿐이다.
        self.assertNotRegex(self.html, r'\borg-(chart|root|cols|col|head|list|node|sub|foot|dot)\b')
        self.assertNotIn("--org-line", self.html)
        for gone in ('id="grm-home-inbox"', 'id="grm-home-inbox-list"', "home-inbox", "home-sec", "회사 조직도"):
            self.assertNotIn(gone, self.html)
        for gone in ("loadOpsReports", "ops_reports", "org-dot", "org-node", "grm-home-inbox"):
            self.assertNotIn(gone, self.js)
        self.assertNotIn("org-", self.ops, "새 운영 센터 렌더러는 옛 org-* 클래스를 쓰지 않는다")

    def test_company_numbers_reuse_existing_loaders(self):
        for new, old in (("grm-oc-kpi-users", "grm-kpi-users"), ("grm-oc-kpi-subscribers", "grm-kpi-subscribers"),
                         ("grm-oc-kpi-dispatch", "grm-kpi-dispatch")):
            self.assertRegex(self.js, r'\["%s", function \(\) \{\s*(var v = )?(return )?txtOf\("%s"\)' % (new, old))
        load_rum = self.js[self.js.index("function loadRum()"):self.js.index("function renderRum(")]
        self.assertIn('txt("grm-oc-kpi-visits"', load_rum, "방문은 성장 탭과 같은 rum_daily 결과로")

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

    def test_admin_js_decides_via_rpc_with_guards(self):
        body = self.js[self.js.index("function opsDecide("):]
        body = body[:body.index("\n  }\n") + 4]
        self.assertIn('state.client.rpc("ops_decide", { p_id: id, p_action: action, p_snooze:', body)
        self.assertEqual(self.js.count('rpc("ops_decide"'), 1)
        self.assertIn("if (!id || st.busy[id] || st.done[id]) return Promise.resolve();", body, "더블 탭·이중 처리 방지")
        self.assertLess(body.index("st.busy[id] = true;"), body.index('rpc("ops_decide"'), "호출 전에 잠근다")
        self.assertIn('r.code === "already_decided"', body)
        self.assertIn('"42501"', body)
        self.assertIn("window.setTimeout(loadOpsHome", body, "처리 후 홈 새로고침")
        self.assertIn(", opsDecide, opsTaskOpen);", self.js, "bind 에 결재·업무 지시 처리 함수가 이어져 있다")

    def test_admin_js_calls_ops_home_once_via_supabase_rpc(self):
        self.assertIn('state.client.rpc("ops_home")', self.js)
        self.assertEqual(self.js.count('rpc("ops_home")'), 1)
        self.assertIn("loadOpsHome()", self.js[self.js.index("function refreshAll()"):])
        self.assertIn('"42501"', self.js)
        self.assertIn("다시 시도", self.js)
        self.assertIn("분 전입니다", self.js)

    def test_theme_tokens_light_and_dark(self):
        self.assertIn("--oc-bg:#f5f6f8", self.html)
        self.assertIn('@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .oc-wrap{', self.html)
        self.assertIn(':root[data-theme="dark"] .oc-wrap{', self.html)

    def test_banned_words_absent(self):
        for name, src in (("admin-ops.js", self.ops), ("admin.html", self.html), ("admin.js", self.js)):
            for w in BANNED_WORDS:
                self.assertNotIn(w, src, f"{name}: '{w}' 금지 — '배정 없음' 으로")

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
