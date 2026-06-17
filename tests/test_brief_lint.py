"""brief_lint 회귀 — 출처 링크 근거(provenance) 하드 가드 (URL전수검사 2026-06-16, Phase F).

정상(handoff 근거 있음)·누출(m_99/m_218 근거 없음 → FAIL)·검증실패(오류 셸·검색 URL → WARN)
케이스를 동결한다. W24 사고(handoff 근거 없는 mfds/brd/view.do 링크)의 회귀 잠금.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brief_lint as bl  # noqa: E402


# ── 대표 handoff rows(v2 형태 일부) ───────────────────────────────────────────
ADMIN_SCAFFOLD = (
    "### [행정처분 · MFDS] 대한약품공업 — **X**\n"
    "<callout icon=\"🔖\" color=\"gray_bg\">\n"
    "\t**출처**  📎 공식원본 "
    "[링크](https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026003474)\n"
    "</callout>"
)
RSS_NOTICE_SCAFFOLD = (
    "### [안내서 · MFDS] 지침 개정 — **Y**\n"
    "<callout icon=\"🔖\" color=\"gray_bg\">\n"
    "\t**출처**  정보출처/공식원본 "
    "[링크](https://www.mfds.go.kr/brd/m_218/view.do?seq=33716)\n"
    "</callout>"
)

HANDOFF_ROWS = [
    {
        "source": "MFDS", "document_id": "admin-2026003474",
        "official_url": "https://www.data.go.kr/data/15058457/openapi.do",
        "api_query": "https://api.odcloud.kr/api/15058457?seq=1",
        "card_scaffold": ADMIN_SCAFFOLD,
        "prose_input": {"kind": "admin-action"},
    },
    {
        "source": "MFDS", "document_id": "data0013-33716",
        "official_url": "https://www.mfds.go.kr/brd/m_218/view.do?seq=33716",
        "card_scaffold": RSS_NOTICE_SCAFFOLD,
        "prose_input": {"kind": "mfds-notice"},
    },
]


class TestNormalizeAndExtract(unittest.TestCase):
    def test_normalize_strips_fragment_trailing_slash_and_leading_amp(self):
        self.assertEqual(
            bl.normalize_url("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?&dispsApplySeq=1#x"),
            "https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=1")
        self.assertEqual(bl.normalize_url("https://A.com/Path/"), "https://a.com/Path")

    def test_normalize_handles_amp_entity(self):
        self.assertEqual(
            bl.normalize_url("https://x.go.kr/getItem?&amp;a=1"),
            "https://x.go.kr/getItem?a=1")

    def test_extract_markdown_and_bare(self):
        text = "see [a](https://x.com/a) and https://y.com/b end"
        got = bl.extract_markdown_links(text)
        self.assertIn("https://x.com/a", got)
        self.assertIn("https://y.com/b", got)

    def test_collect_allowed_includes_scaffold_and_fields(self):
        allowed = bl.collect_allowed_urls(HANDOFF_ROWS)
        self.assertIn(
            bl.normalize_url("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026003474"),
            allowed)
        self.assertIn(bl.normalize_url("https://www.mfds.go.kr/brd/m_218/view.do?seq=33716"), allowed)
        self.assertIn(bl.normalize_url("https://www.data.go.kr/data/15058457/openapi.do"), allowed)


class TestLintProvenance(unittest.TestCase):
    def test_grounded_links_pass(self):
        """발행본이 scaffold 링크를 그대로 쓰면 findings 0."""
        published = ADMIN_SCAFFOLD + "\n\n" + RSS_NOTICE_SCAFFOLD
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertEqual(findings, [], msg=[str(f) for f in findings])

    def test_m99_press_release_leak_fails(self):
        """W24 사고: handoff 에 없는 m_99(보도자료) 직링크 → HARD FAIL."""
        published = (
            "### [고시 · MFDS] 무언가 — **Z**\n"
            "**출처**  📎 공식원본 "
            "[링크](https://www.mfds.go.kr/brd/m_99/view.do?seq=46893)")
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertTrue(bl.has_failures(findings))
        fail = [f for f in findings if f.severity == bl.SEV_FAIL]
        self.assertEqual(len(fail), 1)
        self.assertEqual(fail[0].code, "L17-MFDS-PROVENANCE")
        self.assertIn("m_99", fail[0].url)

    def test_m218_wrong_seq_leak_fails(self):
        """m_218 은 수집기 보드지만, handoff 에 없는 seq(딴 게시물) 직링크면 FAIL."""
        published = (
            "**출처**  정보출처/공식원본 "
            "[링크](https://www.mfds.go.kr/brd/m_218/view.do?seq=99999)")
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertTrue(bl.has_failures(findings))

    def test_grounded_m218_seq_passes(self):
        """handoff 에 있는 정확한 m_218 seq 는 통과(정상 RSS 카드)."""
        published = (
            "[링크](https://www.mfds.go.kr/brd/m_218/view.do?seq=33716)")
        self.assertEqual(bl.lint_link_provenance(HANDOFF_ROWS, published), [])

    def test_unknown_nedrug_link_fails(self):
        """근거 없는 nedrug 링크(딴 seq) → FAIL(MFDS 도메인)."""
        published = "[링크](https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=9999999)"
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertTrue(bl.has_failures(findings))

    def test_global_search_card_url_warns_not_fails(self):
        """근거 없는 비-MFDS 외부 링크(검색 카드 신규)는 WARN 이지 FAIL 아님."""
        published = "[링크](https://www.fda.gov/some/new/warning-letters/acme-999)"
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertFalse(bl.has_failures(findings))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, bl.SEV_WARN)

    def test_duplicate_bad_url_reported_once(self):
        published = ("[a](https://www.mfds.go.kr/brd/m_99/view.do?seq=1) "
                     "[b](https://www.mfds.go.kr/brd/m_99/view.do?seq=1)")
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertEqual(len(findings), 1)


class TestErrorPageDetection(unittest.TestCase):
    def test_error_marker_detected(self):
        self.assertTrue(bl.looks_like_error_page(
            "<html>오류가 발생하였습니다 해당 화면 혹은 기능을 찾을 수 없습니다</html>"))

    def test_real_content_not_error(self):
        self.assertFalse(bl.looks_like_error_page(
            "<html>행정처분정보 대한약품공업 제조업무정지 1개월</html>"))

    def test_empty_is_error(self):
        self.assertTrue(bl.looks_like_error_page(""))


class TestVerifyUrlLive(unittest.TestCase):
    """HTTP 스텁(network 없음) — resolve&verify 의 판정 로직 동결."""

    def _resp(self, status, text):
        m = mock.Mock()
        m.status_code = status
        m.text = text
        return m

    def test_valid_record_promotes(self):
        with mock.patch("requests.get",
                        return_value=self._resp(200, "행정처분정보 " + "x" * 9000)):
            r = bl.verify_url_live("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026003474",
                                   expect_terms=["행정처분"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["is_error_page"])

    def test_error_shell_rejected(self):
        with mock.patch("requests.get",
                        return_value=self._resp(200, "오류가 발생하였습니다 해당 화면 혹은 기능을 찾을 수 없습니다")):
            r = bl.verify_url_live("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=123456")
        self.assertFalse(r["ok"])
        self.assertTrue(r["is_error_page"])

    def test_missing_expected_term_rejected(self):
        with mock.patch("requests.get", return_value=self._resp(200, "x" * 9000)):
            r = bl.verify_url_live("https://nedrug.mfds.go.kr/x", expect_terms=["대한약품"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing_terms"], ["대한약품"])

    def test_network_failure_graceful(self):
        with mock.patch("requests.get", side_effect=OSError("conn reset")):
            r = bl.verify_url_live("https://nedrug.mfds.go.kr/x")
        self.assertFalse(r["ok"])
        self.assertIn("conn reset", r["error"])


class TestAllDomainsPolicy(unittest.TestCase):
    """W2 — provenance 전 기관 일반화. 기본(mfds_only)은 무회귀, all_domains 는 FAIL 승격."""

    def test_default_policy_unchanged_global_warns(self):
        """기본 시그니처(policy 미지정)는 종전대로 비-MFDS 미근거 = WARN(무회귀)."""
        findings = bl.lint_link_provenance(
            HANDOFF_ROWS, "[x](https://www.fda.gov/new/wl-acme-999)")
        self.assertFalse(bl.has_failures(findings))
        self.assertEqual(findings[0].severity, bl.SEV_WARN)

    def test_all_domains_invented_global_fails(self):
        """all_domains: 근거 없고 fetch·verify 도 없는 타 기관 URL = FAIL(지어낸 링크 차단)."""
        findings = bl.lint_link_provenance(
            HANDOFF_ROWS, "[x](https://www.fda.gov/invented/wl-999)",
            policy=bl.POLICY_ALL_DOMAINS)
        self.assertTrue(bl.has_failures(findings))
        self.assertEqual(findings[0].code, "L17-UNGROUNDED")

    def test_all_domains_fetched_global_passes(self):
        """all_domains: 이번 세션에 실제 fetch 한 검색 카드 URL 은 통과(PASS)."""
        url = "https://www.fda.gov/inspections/warning-letters/acme-2026"
        findings = bl.lint_link_provenance(
            HANDOFF_ROWS, f"[x]({url})",
            policy=bl.POLICY_ALL_DOMAINS, allowed_fetched=[url])
        self.assertEqual(findings, [], msg=[str(f) for f in findings])

    def test_all_domains_verifier_pass_passes(self):
        """all_domains: verifier(live verify) 통과 시 통과."""
        findings = bl.lint_link_provenance(
            HANDOFF_ROWS, "[x](https://www.ema.europa.eu/new/guideline-x)",
            policy=bl.POLICY_ALL_DOMAINS, verifier=lambda u: True)
        self.assertEqual(findings, [])

    def test_all_domains_verifier_fail_fails(self):
        findings = bl.lint_link_provenance(
            HANDOFF_ROWS, "[x](https://www.ema.europa.eu/invented/x)",
            policy=bl.POLICY_ALL_DOMAINS, verifier=lambda u: False)
        self.assertTrue(bl.has_failures(findings))

    def test_fetched_whitelist_works_in_default_mode_too(self):
        """allowed_fetched 는 기본 모드에서도 추가적으로 통과시킨다(additive)."""
        url = "https://www.who.int/news/x"
        findings = bl.lint_link_provenance(HANDOFF_ROWS, f"[x]({url})",
                                           allowed_fetched=[url])
        self.assertEqual(findings, [])

    def test_mfds_specialness_not_rescued_by_fetched(self):
        """MFDS 특례: 검색 슬롯 없음 → allowed_fetched·verifier 로도 구제 안 됨(여전히 FAIL)."""
        url = "https://www.mfds.go.kr/brd/m_99/view.do?seq=46893"
        findings = bl.lint_link_provenance(
            HANDOFF_ROWS, f"[x]({url})",
            policy=bl.POLICY_ALL_DOMAINS, allowed_fetched=[url], verifier=lambda u: True)
        self.assertTrue(bl.has_failures(findings))
        self.assertEqual(findings[0].code, "L17-MFDS-PROVENANCE")

    def test_lint_urls_shared_core(self):
        """lint_urls 코어는 URL 리스트를 직접 받아 동일 판정(Notion 블록 URL 경로 공용)."""
        allowed = bl.collect_allowed_urls(HANDOFF_ROWS)
        findings = bl.lint_urls(
            ["https://www.mfds.go.kr/brd/m_99/view.do?seq=1"], allowed)
        self.assertTrue(bl.has_failures(findings))


class TestPublishGate(unittest.TestCase):
    """W1 — run_publish_gate: 매 발행 1회 실행, FAIL 시 ok=False(발행 차단)."""

    def test_grounded_gate_allows(self):
        published = ADMIN_SCAFFOLD + "\n\n" + RSS_NOTICE_SCAFFOLD
        g = bl.run_publish_gate(HANDOFF_ROWS, published)
        self.assertTrue(g.ok)
        self.assertEqual(g.fail_count, 0)
        self.assertIn("PASS", g.report)

    def test_leak_gate_blocks(self):
        published = "[z](https://www.mfds.go.kr/brd/m_99/view.do?seq=46893)"
        g = bl.run_publish_gate(HANDOFF_ROWS, published)
        self.assertFalse(g.ok)
        self.assertEqual(g.fail_count, 1)
        self.assertIn("발행 중단", g.report)

    def test_gate_default_is_all_domains(self):
        """게이트 기본 정책은 all_domains — 지어낸 타 기관 URL 도 차단(W2 옵트인)."""
        g = bl.run_publish_gate(HANDOFF_ROWS, "[x](https://www.fda.gov/invented/wl-1)")
        self.assertFalse(g.ok)

    def test_gate_warn_only_does_not_block(self):
        """mfds_only 정책의 WARN(검색 카드 미확인)만 있으면 발행은 허용(차단=FAIL 한정)."""
        g = bl.run_publish_gate(HANDOFF_ROWS, "[x](https://www.fda.gov/new/wl-1)",
                                policy=bl.POLICY_MFDS_ONLY)
        self.assertTrue(g.ok)
        self.assertEqual(g.warn_count, 1)

    def test_gate_fetched_search_card_allows(self):
        url = "https://www.fda.gov/inspections/warning-letters/acme-2026"
        g = bl.run_publish_gate(HANDOFF_ROWS, f"[x]({url})", allowed_fetched=[url])
        self.assertTrue(g.ok)


class TestGateCLI(unittest.TestCase):
    """W1 — CLI(`python -m brief_lint`)는 결정론 실행·차단(exit 1)."""

    def test_extract_handoff_rows_shapes(self):
        rows = [{"source": "MFDS"}]
        self.assertEqual(bl.extract_handoff_rows({"rows": rows}), rows)
        self.assertEqual(bl.extract_handoff_rows(rows), rows)
        self.assertEqual(bl.extract_handoff_rows({"payload": {"rows": rows}}), rows)
        self.assertEqual(bl.extract_handoff_rows({"nope": 1}), [])

    def _write(self, d, name, text):
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_cli_pass_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            h = self._write(d, "h.json", json.dumps({"rows": HANDOFF_ROWS}))
            p = self._write(d, "b.md",
                            "[ok](https://www.mfds.go.kr/brd/m_218/view.do?seq=33716)")
            self.assertEqual(bl.main(["--handoff", h, "--published", p]), 0)

    def test_cli_fail_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            h = self._write(d, "h.json", json.dumps({"rows": HANDOFF_ROWS}))
            p = self._write(d, "b.md",
                            "[leak](https://www.mfds.go.kr/brd/m_99/view.do?seq=46893)")
            self.assertEqual(bl.main(["--handoff", h, "--published", p]), 1)

    def test_cli_handoff_in_code_fence(self):
        """handoff 페이지 export(```json ... ```)도 rows 추출 가능."""
        with tempfile.TemporaryDirectory() as d:
            fenced = "```json\n" + json.dumps({"rows": HANDOFF_ROWS}) + "\n```"
            h = self._write(d, "h.txt", fenced)
            p = self._write(d, "b.md",
                            "[ok](https://www.mfds.go.kr/brd/m_218/view.do?seq=33716)")
            self.assertEqual(bl.main(["--handoff", h, "--published", p]), 0)

    def test_cli_bad_path_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "b.md", "x")
            self.assertEqual(
                bl.main(["--handoff", os.path.join(d, "missing.json"),
                         "--published", p]), 2)


class TestLiveVerifierFactory(unittest.TestCase):
    """live_verifier — verify_url_live 를 verifier 콜백으로 감싸 ALL_DOMAINS 에 주입."""

    def test_live_verifier_true_on_valid(self):
        with mock.patch("requests.get",
                        return_value=_StubResp(200, "행정처분정보 " + "x" * 9000)):
            v = bl.live_verifier(expect_terms=["행정처분"])
            self.assertTrue(v("https://nedrug.mfds.go.kr/x"))

    def test_live_verifier_false_on_error_shell(self):
        with mock.patch("requests.get",
                        return_value=_StubResp(200, "오류가 발생하였습니다")):
            self.assertFalse(bl.live_verifier()("https://nedrug.mfds.go.kr/x"))


class TestPublishStructure(unittest.TestCase):
    """구조 lint(2026-06-17, v16 축소) — 기계적 Publish Lint(PL1·PL3/16·PL10·PL14).

    v16 프롬프트 [Publish Lint] 의 기계 판정 항목을 결정론 코드로 강등(자가 서술 → 실행).
    """

    # 합격 기준선: 위반 없는 최소 발행물(헤더 요일 정확·정상 카드 제목).
    CLEAN = (
        "**GRM Weekly Brief**\n"
        "2026-06-15 (월) · 검색 기간: 06-09 ~ 06-15 KST\n"
        "### [행정처분 · MFDS] 대한약품공업 — **출하시험 미실시 과징금**\n"
        "<details>\n<summary>🔖 메타</summary>\n\t내용\n</details>\n"
    )

    def test_clean_brief_has_no_findings(self):
        self.assertEqual(bl.lint_publish_structure(self.CLEAN), [])

    def test_residual_slot_token_fails(self):
        f = bl.lint_publish_structure("### [x · y] z — **{{TITLE_ISSUE}}**")
        codes = {x.code for x in f}
        self.assertIn("PL1-RESIDUAL-TOKEN", codes)
        self.assertTrue(bl.has_failures(f))

    def test_forbidden_toggle_literal_fails(self):
        f = bl.lint_publish_structure("<toggle>메타</toggle>")
        self.assertTrue(any(x.code == "PL3-FORBIDDEN-MD" for x in f))
        self.assertTrue(bl.has_failures(f))

    def test_forbidden_toc_and_admonition_fail(self):
        for bad in ("[toc]", "[TOC]", "[!NOTE]", "[!WARNING]", "+++"):
            with self.subTest(bad=bad):
                f = bl.lint_publish_structure(f"text {bad} more")
                self.assertTrue(any(x.code == "PL3-FORBIDDEN-MD" for x in f))

    def test_toggle_attribute_form_fails(self):
        f = bl.lint_publish_structure('<toggle open="true">x</toggle>')
        self.assertTrue(any(x.code == "PL3-FORBIDDEN-MD" for x in f))

    def test_details_summary_not_flagged(self):
        f = bl.lint_publish_structure("<details>\n<summary>메타</summary>\nx\n</details>")
        self.assertEqual(f, [])

    def test_title_unknown_word_fails(self):
        for bad in ("업체 미상", "위반유형 미기재"):
            with self.subTest(bad=bad):
                f = bl.lint_publish_structure(f"### [행정처분 · MFDS] 회사 — **{bad}**")
                self.assertTrue(any(x.code == "PL10-TITLE-UNKNOWN" for x in f))

    def test_w2_table_mijae_is_not_a_title_violation(self):
        """'원문 미기재' 가 W2 표/본문에 있는 것은 정상 — 제목(TITLE_ISSUE)만 PL10 대상."""
        md = ("### [회수 · MFDS] 업체 — **벤조피렌 부적합 회수**\n"
              "<table><tr><td>**회수 등급**</td><td>원문 미기재</td></tr></table>")
        codes = {x.code for x in bl.lint_publish_structure(md)}
        self.assertNotIn("PL10-TITLE-UNKNOWN", codes)

    def test_modality_subheader_not_a_card_title(self):
        """모달리티/그룹 H3(### 💊 합성의약품)는 bold 없음 → PL10 무관."""
        self.assertEqual(bl.lint_publish_structure("### 💊 합성의약품\n### 🧬 바이오의약품"), [])

    def test_weekday_mismatch_fails(self):
        # 2026-06-15 는 월요일 — '화'로 표기하면 FAIL.
        f = bl.lint_publish_structure("발행: 2026-06-15 (화)")
        self.assertTrue(any(x.code == "PL14-WEEKDAY" for x in f))

    def test_weekday_match_passes(self):
        # 2026-06-15 = 월요일.
        f = bl.lint_publish_structure("발행: 2026-06-15 (월요일)")
        self.assertFalse(any(x.code == "PL14-WEEKDAY" for x in f))

    def test_weekday_footer_bare_form_mismatch_fails(self):
        # 06-17 dry-run D-1: 비괄호 푸터형 `발행일: 2026-06-17 화요일` — 06-17 은 수요일.
        f = bl.lint_publish_structure("발행일: 2026-06-17 화요일 | Run ID: x")
        self.assertTrue(any(x.code == "PL14-WEEKDAY" for x in f))
        self.assertTrue(bl.has_failures(f))

    def test_weekday_footer_bare_form_match_passes(self):
        # 2026-06-17 = 수요일 — 비괄호 푸터형 정상.
        f = bl.lint_publish_structure("발행일: 2026-06-17 수요일")
        self.assertFalse(any(x.code == "PL14-WEEKDAY" for x in f))

    def test_weekday_bare_requires_요일_suffix(self):
        # '요일' 접미사 없는 단일 글자(날짜 뒤 우연한 한글)는 PL14 대상 아님(오탐 0).
        f = bl.lint_publish_structure("2026-06-17 수집 현황")
        self.assertEqual(f, [])

    def test_weekday_multiple_occurrences_each_checked(self):
        # 헤더(정상) + 푸터(오류) 동시 — 푸터 오류만 잡힌다.
        md = ("2026-06-17 (수)\n발행일: 2026-06-17 화요일")
        codes = [x.code for x in bl.lint_publish_structure(md)]
        self.assertEqual(codes.count("PL14-WEEKDAY"), 1)

    def test_no_date_pattern_no_finding(self):
        self.assertEqual(bl.lint_publish_structure("요일 없는 본문"), [])

    def test_gate_include_structure_blocks_on_residual_token(self):
        """run_publish_gate(include_structure=True) — provenance 통과여도 구조 FAIL 이면 차단."""
        published = ADMIN_SCAFFOLD + "\n### [x · y] z — **{{TITLE_ISSUE}}**"
        g = bl.run_publish_gate(HANDOFF_ROWS, published, include_structure=True)
        self.assertFalse(g.ok)
        self.assertTrue(any(x.code == "PL1-RESIDUAL-TOKEN" for x in g.findings))

    def test_gate_default_excludes_structure(self):
        """기본(include_structure=False) 은 구조 위반을 보지 않는다(기존 호출처 무회귀)."""
        published = ADMIN_SCAFFOLD + "\n### [x · y] z — **{{TITLE_ISSUE}}**"
        g = bl.run_publish_gate(HANDOFF_ROWS, published)
        self.assertTrue(g.ok)

    def test_cli_structure_flag_fails_on_toggle(self):
        with tempfile.TemporaryDirectory() as d:
            h = os.path.join(d, "h.json")
            with open(h, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"rows": HANDOFF_ROWS}))
            p = os.path.join(d, "b.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[ok](https://www.mfds.go.kr/brd/m_218/view.do?seq=33716)\n<toggle>x</toggle>")
            # provenance 통과(근거 있는 링크) 이지만 구조(toggle) FAIL → exit 1
            self.assertEqual(bl.main(["--handoff", h, "--published", p, "--structure"]), 1)
            # --structure 없으면 통과(exit 0)
            self.assertEqual(bl.main(["--handoff", h, "--published", p]), 0)


class TestCoverageCounts(unittest.TestCase):
    """수집 현황 '수집' 숫자 대조 lint(W2) — 발행물 LLM 집계 ↔ handoff 정본 결정론 대조."""

    # build_coverage_collected 가 만드는 정본 형태(label/count items + total). collect_intake
    # 비의존(순수 brief_lint 테스트) — 정본 dict 를 직접 구성한다.
    EXPECTED = {
        "total": 36,
        "items": [
            {"label": "FR", "count": 2}, {"label": "Recall", "count": 1},
            {"label": "EMA", "count": 0}, {"label": "FDA WL", "count": 3},
            {"label": "MFDS", "count": 30},
        ],
    }
    GOOD = ("Intake row 36건 (FR 2 · Recall 1 · EMA 0 · FDA WL 3 · MFDS 30) · "
            "병합 5건→3카드 · WebSearch 9/9 · 유효항목 33건 · Evidence A 10/B 5/C 0 · 미확인 없음")

    def test_parse_collected_coverage(self):
        parsed = bl.parse_collected_coverage(self.GOOD)
        self.assertEqual(parsed["total"], 36)
        self.assertEqual(parsed["items"]["MFDS"], 30)
        self.assertEqual(parsed["items"]["EMA"], 0)
        # 첫 괄호까지만 — 그 뒤 '병합 5건→3카드' 등은 수집 아이템으로 잡지 않는다.
        self.assertNotIn("병합", parsed["items"])

    def test_no_anchor_returns_none_and_no_finding(self):
        self.assertIsNone(bl.parse_collected_coverage("발행일: 2026-06-17 수요일"))
        self.assertEqual(bl.lint_coverage_counts(self.EXPECTED, "요일만 있음"), [])

    def test_no_expected_no_finding(self):
        self.assertEqual(bl.lint_coverage_counts(None, self.GOOD), [])
        self.assertEqual(bl.lint_coverage_counts({}, self.GOOD), [])

    def test_match_no_finding(self):
        self.assertEqual(bl.lint_coverage_counts(self.EXPECTED, self.GOOD), [])

    def test_total_mismatch_is_fail(self):
        bad = self.GOOD.replace("Intake row 36건", "Intake row 37건")
        fs = bl.lint_coverage_counts(self.EXPECTED, bad)
        self.assertEqual([f.code for f in fs], ["PL15-COVERAGE-TOTAL"])
        self.assertEqual(fs[0].severity, bl.SEV_FAIL)

    def test_source_count_mismatch_is_fail(self):
        bad = self.GOOD.replace("MFDS 30", "MFDS 28")
        fs = bl.lint_coverage_counts(self.EXPECTED, bad)
        self.assertTrue(any(f.code == "PL15-COVERAGE-SOURCE" and "MFDS" in f.message
                            for f in fs))
        self.assertTrue(all(f.severity == bl.SEV_FAIL for f in fs))

    def test_zero_source_omission_allowed(self):
        # EMA 0 을 발행물이 생략 → 정상(과알림 0). '건' 접미도 허용.
        pub = "Intake row 36건 (FR 2건 · Recall 1건 · FDA WL 3건 · MFDS 30건)"
        self.assertEqual(bl.lint_coverage_counts(self.EXPECTED, pub), [])

    def test_nonzero_source_missing_is_fail(self):
        pub = "Intake row 33건 (FR 2 · Recall 1 · EMA 0 · MFDS 30)"  # FDA WL(3) 누락
        fs = bl.lint_coverage_counts(self.EXPECTED, pub)
        codes = [f.code for f in fs]
        self.assertIn("PL15-COVERAGE-TOTAL", codes)      # 33 != 36
        self.assertTrue(any(f.code == "PL15-COVERAGE-SOURCE" and "FDA WL" in f.message
                            for f in fs))

    def test_extra_label_is_warn_not_alert(self):
        # handoff 근거에 없는 라벨이 발행물 수집 줄에 → WARN(저신뢰·알림 트리거 아님).
        pub = "Intake row 36건 (FR 2 · Recall 1 · EMA 0 · FDA WL 3 · MFDS 30 · BOGUS 0)"
        fs = bl.lint_coverage_counts(self.EXPECTED, pub)
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0].code, "PL15-COVERAGE-EXTRA")
        self.assertEqual(fs[0].severity, bl.SEV_WARN)


class _StubResp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text


if __name__ == "__main__":
    unittest.main()
