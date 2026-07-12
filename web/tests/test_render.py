#!/usr/bin/env python3
"""웹 렌더러(P2) 골든·결정론·무변형·escape 테스트.

CI(`unittest discover -s tests`)는 `tests/test_web_render.py` shim 을 통해 이 모듈을
순회한다. 직접 실행 시:
  python web/tests/test_render.py            # 테스트 실행
  python web/tests/test_render.py --freeze   # 골든 (재)동결

골든 시나리오(둘 다 **고정 fixture** 입력 — 라이브 web/data/briefs 와 분리해 새 브리프
발행마다 골든이 깨지던 문제를 종결):
  · 단독(tests/fixtures/single, 실 발행본 동결 스냅샷) → landing / archive /
    brief_2026-06-22 / brief_2026-06-26 / search-index / sitemap
  · 멀티(합성 06-08·06-15 + 실 6/22 결합) → archive_multi / landing_multi /
    brief_2026-06-08(산문·번역 ①②) / brief_2026-06-15(병합 토글)
라이브 web/data/briefs 는 WebLiveBriefsSmokeTest 가 '크래시 없이 렌더'만 비골든으로 확인
(발행본 파손은 잡되, 정상 발행이 골든을 흔들지는 않음).
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest

from markupsafe import escape as _esc

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent      # …/web
REPO_ROOT = WEB_DIR.parent                                     # 저장소 루트(grm_findings.py 등)
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import render  # noqa: E402  (web/render.py — 경로 삽입 후 import)
import grm_findings  # noqa: E402  (FIND-1 M6d 카테고리 라벨 동기화 대조용)

TESTS_DIR = pathlib.Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
MULTI_FIXTURES = TESTS_DIR / "fixtures" / "multi"            # 합성 2건만
SINGLE_FIXTURES = TESTS_DIR / "fixtures" / "single"          # 실 발행본 동결 스냅샷(골든 입력)
DATA_DIR = WEB_DIR / "data" / "briefs"                       # 라이브 발행 디렉터리(스모크 렌더 전용)
REAL_FIXTURE = SINGLE_FIXTURES / "brief_web_2026_06_22.json"

__all__ = [
    "WebRenderGoldenTest",
    "WebLiveBriefsRenderSmokeTest",
    "WebRenderStructureTest",
    "WebRenderFidelityTest",
    "WebRenderDeterminismTest",
    "WebRenderPurityTest",
    "WebRenderHardeningTest",
    "WebAdminRenderTest",
    "WebSearchIndexTest",
    "WebFindingsRenderTest",
    "WebTrendsRenderTest",
    "WebFirmRenderTest",
    "WebFirmWatchlistTest",
    "WebKoreanSafetyTest",
    "WebSeoMetaTest",
    "WebDeterministicDetailTest",
    "WebFda483DeterministicDetailTest",
    "WebFda483DeepAnalysisTest",
    "WebMonoLabelsContractTest",
    "WebBriefFirmLinkTest",
]


# ── 빌드 헬퍼 (테스트·freeze 공용 — 동일 입력 보장) ───────────────────────────
def _build_single(out: pathlib.Path) -> None:
    render.render_site(SINGLE_FIXTURES, out)


def _build_multi(out: pathlib.Path, scratch: pathlib.Path) -> None:
    """합성 2건 + 실 6/22 를 한 데이터 디렉터리로 결합해 빌드(런타임 결합=드리프트 0)."""
    data = scratch / "multi_data"
    data.mkdir(parents=True, exist_ok=True)
    for fp in sorted(MULTI_FIXTURES.glob("*.json")):
        shutil.copyfile(fp, data / fp.name)
    shutil.copyfile(REAL_FIXTURE, data / REAL_FIXTURE.name)
    render.render_site(data, out)


# (built_relpath, golden_filename)
SINGLE_GOLDENS = [
    ("index.html", "landing.expected.html"),
    ("archive/index.html", "archive.expected.html"),
    ("findings/index.html", "findings.expected.html"),
    ("findings/trends/index.html", "trends.expected.html"),
    ("findings/firm/index.html", "firm.expected.html"),
    ("briefs/2026-06-22/index.html", "brief_2026-06-22.expected.html"),
    ("briefs/2026-06-26/index.html", "brief_2026-06-26.expected.html"),
    ("assets/search-index.json", "search-index.expected.json"),
    ("robots.txt", "robots.expected.txt"),
    ("sitemap.xml", "sitemap.expected.xml"),
    ("site.webmanifest", "site.expected.webmanifest"),
]
MULTI_GOLDENS = [
    ("archive/index.html", "archive_multi.expected.html"),
    ("index.html", "landing_multi.expected.html"),
    ("briefs/2026-06-08/index.html", "brief_2026-06-08.expected.html"),
    ("briefs/2026-06-15/index.html", "brief_2026-06-15.expected.html"),
    ("assets/search-index.json", "search-index_multi.expected.json"),
]


def _read_real_cards() -> list[dict]:
    return json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))["cards"]


# ── 라이브 발행 디렉터리 비골든 스모크 ────────────────────────────────────────
class WebLiveBriefsRenderSmokeTest(unittest.TestCase):
    """라이브 web/data/briefs 가 크래시 없이 렌더되는지 **비골든** 스모크.

    골든은 tests/fixtures/single(실 발행본 동결 스냅샷)만 검증하므로, 새 브리프 발행이
    골든을 흔들지 않는다. 그 대신 실제 발행 디렉터리의 렌더 가능성은 이 스모크가 byte
    비교 없이 지킨다 — 파손된/비정상 발행본은 CI 에서 걸리되, 정상 발행은 골든 무영향.
    """
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_live_"))
        cls.out = cls._tmp / "live"
        render.render_site(DATA_DIR, cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_landing_and_aggregates_built(self):
        for rel in ("index.html", "archive/index.html",
                    "assets/search-index.json", "sitemap.xml", "robots.txt"):
            self.assertTrue((self.out / rel).exists(), f"라이브 렌더 누락: {rel}")

    def test_every_live_brief_has_a_page(self):
        briefs = render.load_briefs(DATA_DIR)
        self.assertGreater(len(briefs), 0, "라이브 브리프 0건 — 발행 디렉터리 확인")
        for b in briefs:
            date = b["brief"].get("publish_date", "")
            self.assertTrue((self.out / "briefs" / date / "index.html").exists(),
                            f"라이브 브리프 페이지 누락: {date!r}")


# ── 골든 byte-diff ───────────────────────────────────────────────────────────
class WebRenderGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_g_"))
        cls.single = cls._tmp / "single"
        cls.multi = cls._tmp / "multi"
        _build_single(cls.single)
        _build_multi(cls.multi, cls._tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _assert_golden(self, built_root: pathlib.Path, rel: str, golden_name: str):
        built = (built_root / rel).read_bytes()
        gpath = GOLDEN_DIR / golden_name
        self.assertTrue(gpath.exists(), f"골든 누락: {golden_name} (먼저 --freeze)")
        golden = gpath.read_bytes()
        if built != golden:
            # 첫 불일치 줄 진단
            bl, gl = built.decode("utf-8").splitlines(), golden.decode("utf-8").splitlines()
            msg = [f"골든 불일치: {golden_name} (built {rel})"]
            for i, (b, g) in enumerate(zip(bl, gl)):
                if b != g:
                    msg += [f"  line {i+1}:", f"   built : {b[:200]}", f"   golden: {g[:200]}"]
                    break
            else:
                msg.append(f"  길이 차 built={len(bl)} golden={len(gl)} 줄")
            self.fail("\n".join(msg))

    def test_single_goldens(self):
        for rel, name in SINGLE_GOLDENS:
            with self.subTest(golden=name):
                self._assert_golden(self.single, rel, name)

    def test_multi_goldens(self):
        for rel, name in MULTI_GOLDENS:
            with self.subTest(golden=name):
                self._assert_golden(self.multi, rel, name)

    def test_css_copied_verbatim(self):
        built = (self.single / "assets" / "grm.css").read_bytes()
        src = (WEB_DIR / "assets" / "grm.css").read_bytes()
        self.assertEqual(built, src, "dist 의 grm.css 가 소스(v4 추출본)와 byte 불일치")

    def test_archive_js_copied_and_index_emitted(self):
        # P4: 검색 스크립트는 assets 정적 복사(verbatim), 인덱스는 빌드 산출.
        built = (self.single / "assets" / "archive.js").read_bytes()
        src = (WEB_DIR / "assets" / "archive.js").read_bytes()
        self.assertEqual(built, src, "archive.js 가 dist 에 verbatim 복사되지 않음")
        self.assertTrue((self.single / "assets" / "search-index.json").exists(),
                        "search-index.json 미산출")

    def test_favicon_and_og_assets_present(self):
        # 브랜드 에셋(png·ico·og) 은 골든 대상 아님(존재/복사 byte-verbatim 만 확인).
        for name in ("favicon-16.png", "favicon-32.png", "favicon-48.png",
                     "favicon-180.png", "favicon-192.png", "favicon-512.png",
                     "favicon.ico", "favicon.svg", "og-image.png"):
            built = (self.single / "assets" / name).read_bytes()
            src = (WEB_DIR / "assets" / name).read_bytes()
            self.assertEqual(built, src, f"{name} 이 dist/assets 에 verbatim 복사되지 않음")

    def test_favicon_copied_to_dist_root(self):
        # 브라우저가 /favicon.ico·/favicon.svg 를 루트에서 자동 요청 — assets/ 와 별도 복사.
        for name in ("favicon.ico", "favicon.svg"):
            built = (self.single / name).read_bytes()
            src = (WEB_DIR / "assets" / name).read_bytes()
            self.assertEqual(built, src, f"{name} 이 dist 루트에 verbatim 복사되지 않음")


# ── 구조 단언 (스키마 → 마크업 매핑) ─────────────────────────────────────────
class WebRenderStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_s_"))
        cls.single = cls._tmp / "single"
        cls.multi = cls._tmp / "multi"
        _build_single(cls.single)
        _build_multi(cls.multi, cls._tmp)
        cls.detail = (cls.single / "briefs/2026-06-22/index.html").read_text(encoding="utf-8")
        cls.cards = _read_real_cards()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_render_order_preserved(self):
        # 카드 anchor = document_id(P4 §2.2). render_order 순으로 등장하는지 확인.
        ordered = sorted(self.cards, key=lambda c: c["render_order"])
        positions = [self.detail.index(f'id="{c["id"]}"') for c in ordered]
        self.assertEqual(positions, sorted(positions), "카드가 render_order 순으로 나오지 않음")
        # 모든 카드 anchor(=id) 존재.
        for c in self.cards:
            self.assertIn(f'id="{c["id"]}"', self.detail)

    def test_section_counts_derived(self):
        # 글로벌 34 · 국내 1 · Recall 1 (입력에서 파생).
        self.assertIn('글로벌 <span class="n">34장</span>', self.detail)
        self.assertIn('국내 <span class="n">1장</span>', self.detail)
        self.assertIn('Recall <span class="n">1장</span>', self.detail)

    def test_group_label_subheaders(self):
        self.assertIn('<div class="grp-h">💊 합성의약품</div>', self.detail)
        self.assertIn('<div class="grp-h">▫️ 기타</div>', self.detail)

    def test_empty_prose_slots_omitted_for_real_fixture(self):
        # 6/22 는 모든 산문 슬롯이 빈 placeholder → 해당 블록/줄 미출력.
        self.assertNotIn('class="summary"', self.detail)
        self.assertNotIn('class="imp"', self.detail)
        self.assertNotIn('class="chk"', self.detail)
        self.assertNotIn('ti-list-details', self.detail)   # 핵심 사실 블록
        self.assertNotIn('class="tldr"', self.detail)       # tldr 빈 배열
        # title_issue 빈값 → 제목에 " — <b>" 분리 표기 없음.
        self.assertNotIn(' — <b>', self.detail)

    def test_ko_translation_line_omitted(self):
        # 실 6/22 인용은 전부 KO(null) 또는 빈 번역 → 번역 줄(div class="t") 0.
        self.assertNotIn('<div class="t">', self.detail)
        # 그러나 원문 인용 블록은 존재(Evidence A 카드).
        self.assertIn('ti-quote', self.detail)

    def test_evidence_bc_have_no_quotes(self):
        # to_web_card 는 Evidence A 만 quotes 채움 → B/C 카드는 quotes:[] → 인용 블록 없음.
        # 첫 B 카드(render_order 0)의 article 범위(다음 카드 anchor 직전)에 ti-quote 없음.
        byro = sorted(self.cards, key=lambda c: c["render_order"])
        start = self.detail.index(f'id="{byro[0]["id"]}"')
        end = self.detail.index(f'id="{byro[1]["id"]}"')
        self.assertNotIn('ti-quote', self.detail[start:end])

    def test_mono_only_for_data_labels(self):
        # 발행일/문서번호/실사일/Class 는 mono, 한글 라벨값(업체 등)은 mono 아님.
        self.assertIn('<td class="k">발행일</td><td class="v"><span class="mono">2026-06-17</span></td>', self.detail)
        self.assertIn('<td class="k">Class</td><td class="v"><span class="mono">Type III</span></td>', self.detail)
        self.assertIn('<td class="k">업체</td><td class="v">경방신약(주)</td>', self.detail)

    def test_dual_links_pdf_vs_page(self):
        # official_is_pdf 분기.
        self.assertIn('<i class="ti ti-file-type-pdf"></i> PDF 원문', self.detail)
        self.assertIn('<i class="ti ti-file-text"></i> 공식 페이지', self.detail)
        self.assertIn('<span class="t">정보출처</span>', self.detail)
        self.assertIn('<span class="t">공식원본</span>', self.detail)

    def test_disclaimer_present(self):
        self.assertIn('AI 자동 생성 안내', self.detail)
        self.assertIn('verify against the original before acting', self.detail)

    def test_merged_toggle_in_synthetic(self):
        h = (self.multi / "briefs/2026-06-15/index.html").read_text(encoding="utf-8")
        self.assertIn('<details class="block merged">', h)
        self.assertIn('전체 3품목', h)
        for item in ["아세트아미노펜정 500mg", "아세트아미노펜정 325mg", "이부프로펜정 200mg"]:
            self.assertIn(f"<li>{item}</li>", h)

    def test_bilingual_quote_interleave_in_synthetic(self):
        h = (self.multi / "briefs/2026-06-08/index.html").read_text(encoding="utf-8")
        self.assertIn('ti-quote"></i>원문 및 번역', h)        # 번역 있으면 라벨 전환
        self.assertIn('<span class="m">①</span>', h)
        self.assertIn('<span class="m">②</span>', h)
        self.assertIn('<div class="t">① ', h)                # 번역 줄에도 마크
        self.assertIn('<div class="t">② ', h)

    def _render_card_partial(self, card: dict) -> str:
        """card.html 파셜만 단독 렌더(합성 카드 뷰 → 마크업). 골든과 무관한 유닛 경로."""
        env = render._make_env()
        view = render._card_view(card)
        return env.get_template("partials/card.html").render(card=view)

    def test_violation_bilingual_pair_when_original_present(self):
        # [원문·국문 병기 2026-07-08] deep_analysis 위반에 original 이 있으면 원문(세리프)+국문
        # 해석 쌍으로 렌더. original 은 raw 통과(사실 무변형)이므로 값 그대로 나와야 한다.
        card = {
            "id": "wl-x", "render_order": 1, "evidence_level": "A",
            "headline_target": "Acme Pharma", "agency": "FDA", "card_type": "Warning Letter",
            "deep_analysis": {
                "key_violations": [{
                    "citation": "21 CFR 211.194(a)",
                    "original": "Your firm failed to establish adequate written procedures.",
                    "description": "귀사는 적절한 서면 절차를 수립하지 못했다.",
                    "risk": "데이터 신뢰성 저하 위험.",
                }],
                "fda_evaluation": "x" * 30,
                "required_remediation": {"deadline": "15영업일", "items": ["원인 조사"]},
                "administrative_risks": "y" * 30,
            },
        }
        h = self._render_card_partial(card)
        self.assertIn('<div class="viol-orig"><span class="viol-lang">원문 · 규제 원어</span>', h)
        self.assertIn('<p class="viol-o">Your firm failed to establish adequate written '
                      'procedures.</p>', h)                      # 원문 verbatim
        self.assertIn('<span class="viol-lang ko">국문 해석</span>귀사는 적절한 서면 절차를', h)

    def test_violation_korean_only_when_no_original(self):
        # original 미보유(백필 전·구데이터) 카드는 병기 마크업이 전혀 없어야 한다(현행 바이트 불변).
        card = {
            "id": "wl-y", "render_order": 1, "evidence_level": "A",
            "headline_target": "Beta Pharma", "agency": "FDA", "card_type": "Warning Letter",
            "deep_analysis": {
                "key_violations": [{
                    "citation": "21 CFR 211.100",
                    "description": "귀사는 절차를 준수하지 않았다.",
                    "risk": "품질 위험.",
                }],
                "fda_evaluation": "x" * 30,
                "required_remediation": {"deadline": "15영업일", "items": ["시정"]},
                "administrative_risks": "y" * 30,
            },
        }
        h = self._render_card_partial(card)
        self.assertNotIn("viol-orig", h)
        self.assertNotIn("viol-lang", h)
        self.assertIn('<p class="viol-desc">귀사는 절차를 준수하지 않았다.</p>', h)  # 현행 형태 그대로

    def _obs_card(self, ko: bool) -> dict:
        obs = {"number": "1",
               "deficiency": "The master production and control records are not followed.",
               "detail": "Specifically, ABC."}
        if ko:
            obs["deficiency_ko"] = "마스터 생산·관리 기록서가 준수되지 않았다."
            obs["detail_ko"] = "구체적으로, 가나다."
        return {
            "id": "f483", "render_order": 1, "evidence_level": "B",
            "headline_target": "Acme 483", "agency": "FDA", "card_type": "FDA 483 실사 관찰",
            "deterministic_detail": {"type": "fda_483_observations", "count": 1,
                                     "observations": [obs]},
        }

    def test_observation_bilingual_when_deficiency_ko_present(self):
        # [원문·국문 병기 2026-07-09] deficiency_ko 있으면 Observation 상세가 원문(영문)+국문 쌍.
        h = self._render_card_partial(self._obs_card(ko=True))
        self.assertIn('<span class="viol-lang">원문 · FDA 483</span>', h)
        self.assertIn('<p class="obs-en">The master production and control records '
                      'are not followed.</p>', h)                        # 원문 verbatim
        self.assertIn('<span class="viol-lang ko">국문 해석</span>', h)
        self.assertIn('마스터 생산·관리 기록서가 준수되지 않았다.', h)         # 국문 번역
        self.assertIn('구체적으로, 가나다.', h)                              # detail_ko

    def test_observation_english_only_when_no_ko(self):
        # deficiency_ko 미보유(백필 전·번역 실패)면 기존 영문만 — additive·바이트 불변.
        h = self._render_card_partial(self._obs_card(ko=False))
        self.assertNotIn("obs-orig", h)
        self.assertNotIn("viol-lang", h)
        self.assertIn('<p class="dt-sum">The master production and control records '
                      'are not followed.</p>', h)                        # 현행 영문 형태 그대로

    def test_filled_prose_rendered_in_synthetic(self):
        h = (self.multi / "briefs/2026-06-08/index.html").read_text(encoding="utf-8")
        self.assertIn('class="summary"', h)
        self.assertIn('class="imp"', h)
        self.assertIn('class="chk"', h)
        self.assertIn('ti-list-details', h)
        self.assertIn('class="tldr"', h)
        self.assertIn(' — <b>데이터 무결성 결함</b>', h)        # title_issue 분리 표기

    def test_relative_paths_per_depth(self):
        landing = (self.single / "index.html").read_text(encoding="utf-8")
        archive = (self.single / "archive/index.html").read_text(encoding="utf-8")
        self.assertIn('href="/assets/grm.css?v=', landing)
        self.assertIn('href="/assets/grm.css?v=', archive)
        self.assertIn('href="/assets/grm.css?v=', self.detail)
        # 내부 페이지 링크는 페이지 깊이에 맞춘 상대경로를 유지한다.
        self.assertIn('href="../../archive/index.html"', self.detail)

    def test_archive_sort_and_latest(self):
        h = (self.multi / "archive/index.html").read_text(encoding="utf-8")
        v3 = h.index('Vol.<b>3</b>')
        v2 = h.index('Vol.<b>2</b>')
        v1 = h.index('Vol.<b>1</b>')
        self.assertTrue(v3 < v2 < v1, "아카이브가 최신호(desc) 정렬이 아님")
        self.assertEqual(h.count('class="issue latest"'), 1, "최신호 강조는 1건이어야")
        # 최신(6/22)만 latest.
        latest_block = h[h.index('class="issue latest"'):v2]
        self.assertIn('briefs/2026-06-22/index.html', latest_block)


# ── 사실/URL 무변형 (게이트 핵심) ────────────────────────────────────────────
class WebRenderFidelityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_f_"))
        out = cls._tmp / "single"
        _build_single(out)
        cls.detail = (out / "briefs/2026-06-22/index.html").read_text(encoding="utf-8")
        cls.cards = _read_real_cards()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _present(self, value: str) -> bool:
        # Jinja autoescape(markupsafe)와 동일 escape 후 탐색.
        return str(_esc(value)) in self.detail

    def test_fact_values_verbatim(self):
        for c in self.cards:
            for f in c["facts"]:
                with self.subTest(card=c["id"], label=f["label"]):
                    self.assertTrue(self._present(f["value"]),
                                    f"사실값 누락/변형: {c['id']} {f['label']}={f['value']!r}")

    def test_headline_targets_verbatim(self):
        for c in self.cards:
            with self.subTest(card=c["id"]):
                self.assertTrue(self._present(c["headline_target"]),
                                f"headline_target 변형: {c['id']}")

    def test_quote_originals_verbatim(self):
        for c in self.cards:
            for q in (c.get("quotes") or []):
                with self.subTest(card=c["id"]):
                    self.assertTrue(self._present(q["original"]),
                                    f"인용 원문 변형: {c['id']}")

    def test_urls_verbatim(self):
        for c in self.cards:
            s = c["sources"]
            for url in (s.get("info_url"), s.get("official_url")):
                if url:
                    with self.subTest(card=c["id"], url=url[:40]):
                        self.assertTrue(self._present(url),
                                        f"URL 변형: {c['id']} {url!r}")

    def test_xss_escaped(self):
        # 카드 텍스트의 &·" 가 escape 됨(autoescape on).
        self.assertIn('&amp;', self.detail)   # ICH Q8/Q9/Q10 ... Q&A
        self.assertIn('&#34;', self.detail)   # "GDP Update 2026"
        # 원시 미escape 위험문자 시퀀스가 본문 텍스트로 새지 않음.
        self.assertNotIn('<script>alert', self.detail)


# ── 결정론 ───────────────────────────────────────────────────────────────────
class WebRenderDeterminismTest(unittest.TestCase):
    def test_two_builds_identical(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_d_"))
        try:
            a, b = tmp / "a", tmp / "b"
            _build_single(a)
            _build_single(b)
            files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
            files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
            self.assertEqual(files_a, files_b, "두 빌드의 파일 목록 불일치")
            for rel in files_a:
                self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes(),
                                 f"비결정론: {rel}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_output_is_lf_utf8(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_l_"))
        try:
            out = tmp / "s"
            _build_single(out)
            for p in out.rglob("*.html"):
                b = p.read_bytes()
                self.assertNotIn(b"\r\n", b, f"CRLF 발견: {p.name}")
                b.decode("utf-8")  # UTF-8 디코드 가능
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── 순수성 (외부호출/시각/난수 0) ────────────────────────────────────────────
class WebRenderPurityTest(unittest.TestCase):
    def test_no_impure_imports(self):
        # AST 로 실제 import 만 검사(docstring·주석의 모듈명 언급에 오탐 안 함).
        import ast
        src = (WEB_DIR / "render.py").read_text(encoding="utf-8")
        roots: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "socket", "http", "random",
                     "secrets", "datetime", "time", "subprocess"}
        leaked = roots & forbidden
        self.assertFalse(leaked, f"순수성 위반: 비결정/네트워크 모듈 import {leaked}")

    def test_no_nondeterministic_calls(self):
        # 호출 패턴(시각/난수) 부재 — 코드 라인만(docstring 제외) AST 로 확인.
        import ast
        src = (WEB_DIR / "render.py").read_text(encoding="utf-8")
        bad = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Attribute):
                if node.attr in {"now", "today", "utcnow", "random", "time"}:
                    bad.append(node.attr)
        self.assertFalse(bad, f"비결정 호출 가능 속성 사용: {bad}")


# ── 검색 인덱스 (P4 — 구조·무변형·facet·정렬·앵커 href) ───────────────────────
class WebSearchIndexTest(unittest.TestCase):
    """build_search_index 직접 단위테스트. byte 안정은 골든(search-index*.expected.json)
    + 결정론 테스트(dist 전 파일 2× 동일)가 함께 잠근다."""

    @classmethod
    def setUpClass(cls):
        cls.cards = _read_real_cards()
        briefs = render.load_briefs(SINGLE_FIXTURES)
        issue_no = render.assign_issue_numbers(briefs)
        latest = max(b["brief"].get("publish_date", "") for b in briefs)
        cls.idx = render.build_search_index(briefs, issue_no, latest)
        # 단독 fixture(tests/fixtures/single)는 실 발행본 동결 스냅샷 — 인덱스는 전 호의
        # 렌더 카드를 담는다(date desc·호내 render_order asc). 단일 호 가정의 어서션은
        # 이 전 호 카드 집합 기준으로 정합화한다(self.cards=06-22 상세 검증용으로 유지).
        cls.single_cards = [c for b in briefs for c in (b.get("cards") or [])]
        # 멀티(합성2 + 실6/22) — date desc·호내 render_order asc 검증용.
        mbriefs = [json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted(MULTI_FIXTURES.glob("*.json"))]
        mbriefs.append(json.loads(REAL_FIXTURE.read_text(encoding="utf-8")))
        m_issue_no = render.assign_issue_numbers(mbriefs)
        m_latest = max(b["brief"].get("publish_date", "") for b in mbriefs)
        cls.midx = render.build_search_index(mbriefs, m_issue_no, m_latest)
        cls.all_cards = [c for b in mbriefs for c in (b.get("cards") or [])]

    def _anchor(self, entry: dict) -> str:
        return entry["href"].rsplit("#", 1)[1]

    def test_schema_and_top_keys(self):
        self.assertEqual(self.idx["schema"], "grm-search-index/v1")
        for k in ("facets", "issues", "cards"):
            self.assertIn(k, self.idx)
        for k in ("agencies", "categories", "modalities", "months"):
            self.assertIn(k, self.idx["facets"])

    def test_one_entry_per_rendered_card(self):
        renderable = [c for c in self.single_cards if render._is_renderable(c)]
        self.assertEqual(len(self.idx["cards"]), len(renderable))
        # 카드 엔트리 필드 집합 고정(스키마 v1 외 필드 신설 금지).
        expect = {"issue_no", "date", "month", "vol_title", "agency", "category",
                  "modality", "card_type", "evidence_level", "signal_tier",
                  "target", "issue", "summary", "href", "text"}
        for e in self.idx["cards"]:
            self.assertEqual(set(e.keys()), expect)

    def test_card_entry_fields_verbatim(self):
        # 인덱스 값 = 카드 기존 값 그대로(재생성 0). null modality 보존.
        by_anchor = {self._anchor(e): e for e in self.idx["cards"]}
        for c in self.cards:
            if not render._is_renderable(c):
                continue
            anchor = render._card_anchor(c)
            e = by_anchor[anchor]
            self.assertEqual(e["target"], c.get("headline_target", ""))
            self.assertEqual(e["issue"], c.get("title_issue", ""))
            self.assertEqual(e["agency"], c.get("agency", ""))
            self.assertEqual(e["category"], c.get("category", ""))
            self.assertEqual(e["modality"], c.get("modality"))
            self.assertEqual(e["evidence_level"], c.get("evidence_level", ""))
            self.assertEqual(e["href"],
                             f"../briefs/{e['date']}/index.html#{anchor}")
            # text = 카드 값들의 verbatim 부분문자열 결합(새 사실 0).
            self.assertIn(c.get("headline_target", ""), e["text"])
            for f in (c.get("facts") or []):
                self.assertIn(f["value"], e["text"])

    def test_href_anchor_matches_detail_article_id(self):
        # 검색결과 href 의 앵커가 (해당 호) 상세 article id 와 동일(점프 일치). 2호가 되면
        # 카드마다 자기 발행일(date) 상세 페이지에서 앵커를 찾는다.
        import tempfile as _tf
        tmp = pathlib.Path(_tf.mkdtemp(prefix="grmweb_idx_"))
        try:
            out = tmp / "s"
            _build_single(out)
            details: dict[str, str] = {}
            for e in self.idx["cards"]:
                d = e["date"]
                if d not in details:
                    details[d] = (out / "briefs" / d / "index.html").read_text(encoding="utf-8")
                self.assertIn(f'id="{self._anchor(e)}"', details[d])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_facets_present_values_only_and_sorted(self):
        # facet 은 전 호 렌더 카드에서 파생(idx 는 SINGLE_FIXTURES 전 브리프로 빌드) — 단일 호
        # 가정(self.cards=06-22)이 아닌 전 호 집합(single_cards) 기준으로 정합화한다.
        rc = [c for c in self.single_cards if render._is_renderable(c)]
        self.assertEqual(self.idx["facets"]["agencies"],
                         sorted({c["agency"] for c in rc if c.get("agency")}))
        self.assertEqual(self.idx["facets"]["categories"],
                         sorted({c["category"] for c in rc if c.get("category")}))
        self.assertEqual(self.idx["facets"]["modalities"],
                         sorted({c["modality"] for c in rc if c.get("modality")}))
        months = self.idx["facets"]["months"]
        self.assertEqual(months, sorted(months, reverse=True))  # 최신순
        # null modality 는 facet 후보에서 제외.
        self.assertNotIn(None, self.idx["facets"]["modalities"])

    def test_single_index_sorted_date_desc_then_render_order(self):
        # 06-26 발행으로 단독 인덱스도 2호 — date desc 후 호내 render_order asc(멀티와 동형).
        cards = self.idx["cards"]
        dates = [c["date"] for c in cards]
        self.assertEqual(dates, sorted(dates, reverse=True), "date desc 아님")
        ro = {render._card_anchor(c): c.get("render_order")
              for c in self.single_cards if render._is_renderable(c)}
        from itertools import groupby
        for _, grp in groupby(cards, key=lambda c: c["date"]):
            seq = [ro[self._anchor(e)] for e in grp]
            self.assertEqual(seq, sorted(seq), "호 내 render_order asc 아님")

    def test_multi_sorted_date_desc_then_render_order(self):
        cards = self.midx["cards"]
        dates = [c["date"] for c in cards]
        self.assertEqual(dates, sorted(dates, reverse=True), "date desc 아님")
        # 호 메타도 date desc, 호 수 = 3.
        self.assertEqual(len(self.midx["issues"]), 3)
        idates = [i["date"] for i in self.midx["issues"]]
        self.assertEqual(idates, sorted(idates, reverse=True))
        # 동일 date 구간 내 render_order asc.
        ro = {render._card_anchor(c): c.get("render_order")
              for c in self.all_cards if render._is_renderable(c)}
        from itertools import groupby
        for _, grp in groupby(cards, key=lambda c: c["date"]):
            seq = [ro[self._anchor(e)] for e in grp]
            self.assertEqual(seq, sorted(seq), "호 내 render_order asc 아님")

    def test_issue_entry_shape(self):
        e = self.idx["issues"][0]
        for k in ("issue_no", "slug", "date", "month", "title", "agencies",
                  "count", "ev", "latest", "href"):
            self.assertIn(k, e)
        self.assertEqual(e["href"], f"../briefs/{e['slug']}/index.html")
        self.assertTrue(e["latest"])  # issues[0]=최신호(date desc) → latest=True


# ── 지적사항 검색 (FIND-1 M3c — 셸 렌더·env-gate·sitemap·nav 배선) ─────────────
class WebFindingsRenderTest(unittest.TestCase):
    """findings/index.html 은 라이브 Supabase 데이터를 담지 않는 정적 셸이다(런타임에
    findings.js 가 PostgREST 를 직접 fetch). 여기선 셸 자체의 결정론·env-gate·배선만
    검증한다 — 결과 카드 렌더는 findings.js 소관(비골든, JS 단위테스트 범위 밖)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_find_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "index.html").read_text(encoding="utf-8")
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.archive = (cls.single / "archive" / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_page_generated(self):
        self.assertIn("규제 지적사항 검색", self.html)
        # [M15] 상단 슬림 고지(#findings-notice)는 제거되고, 하단 기존 AI Disclosure 디자인
        # (id="ai-notice")으로 이전됐다.
        self.assertNotIn('id="findings-notice"', self.html)
        self.assertIn('id="ai-notice"', self.html)
        self.assertIn("AI Disclosure", self.html)

    def test_cfg_div_env_gated_empty_by_default(self):
        # 테스트 환경엔 SUPABASE_URL/ANON_KEY 미설정 — cfg data 속성은 항상 빈 문자열
        # (reactions cfg 와 무관한 별개 게이트 — 골든 결정론 유지의 근거).
        self.assertIn('id="grm-findings-cfg" data-url="" data-key="" hidden', self.html)

    def test_findings_js_referenced_with_content_hash(self):
        import re as _re
        m = _re.search(r'assets/findings\.js\?v=([0-9a-f]{8})"', self.html)
        self.assertIsNotNone(m, "findings.js 캐시버스팅 해시 미발견")

    def test_findings_js_copied_verbatim(self):
        built = (self.single / "assets" / "findings.js").read_bytes()
        src = (WEB_DIR / "assets" / "findings.js").read_bytes()
        self.assertEqual(built, src, "findings.js 가 dist 에 verbatim 복사되지 않음")

    def test_sitemap_includes_findings(self):
        self.assertIn(f"<loc>{render.SITE_BASE_URL}/findings/</loc>", self.sitemap)

    def test_nav_link_present_and_active_state(self):
        # [M15] "지적사항" → "찾아보기" 로 이름 변경, findings 페이지에서만 nav 'on' 클래스가 붙는다.
        self.assertIn('href="../findings/index.html" class="on">찾아보기</a>', self.html)
        self.assertIn('href="findings/index.html">찾아보기</a>', self.landing)
        self.assertNotIn('href="../findings/index.html" class="on">찾아보기</a>', self.archive)
        self.assertIn('href="../findings/index.html">찾아보기</a>', self.archive)

    def test_nav_this_week_tab_removed_but_cta_kept(self):
        # [M15] nav 탭에서 "이번 주" 링크는 제거됐다(CTA "이번 주 소식" 버튼과 중복) —
        # 헤더 상시 CTA 버튼("이번 주 소식")은 그대로 유지된다.
        import re as _re
        nav_m = _re.search(r'<nav id="navmenu">(.*?)</nav>', self.html, _re.S)
        self.assertIsNotNone(nav_m)
        self.assertNotIn(">이번 주<", nav_m.group(1))
        self.assertEqual(nav_m.group(1).count("<a "), 4, "nav 탭은 소개·모아보기·찾아보기·트렌드 4개여야 함")
        self.assertIn("이번 주 소식", self.html)  # CTA 버튼은 유지

    def test_footer_link_present(self):
        self.assertIn('<a href="../findings/index.html">찾아보기</a>', self.html)
        self.assertNotIn(">이번 주</a>", self.html)

    def test_canonical_and_description(self):
        self.assertIn(f'<link rel="canonical" href="{render.SITE_BASE_URL}/findings/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

    # ── FIND-1 M6d: 국문 우선표시 + 카테고리 라벨 병기 ──────────────────────────
    def test_category_labels_sync_with_taxonomy(self):
        """findings.js 의 CATEGORY_LABELS 는 grm_findings.FINDING_TAXONOMY 20개
        code/label_ko/label_en 과 완전히 일치해야 한다(하드코딩 사본 드리프트 방지)."""
        import re as _re

        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        m = _re.search(r"var CATEGORY_LABELS = \{(.*?)\n  \};", js_src, _re.S)
        self.assertIsNotNone(m, "findings.js 에 CATEGORY_LABELS 정의 미발견")
        body = m.group(1)

        entry_pat = _re.compile(
            r'(\w+):\s*\{\s*ko:\s*"((?:[^"\\]|\\.)*)",\s*en:\s*"((?:[^"\\]|\\.)*)"\s*\}'
        )
        found = {code: (ko, en) for code, ko, en in entry_pat.findall(body)}

        expected = {c.code: (c.label_ko, c.label_en) for c in grm_findings.FINDING_TAXONOMY}
        self.assertEqual(len(expected), 20, "FINDING_TAXONOMY 카테고리 수가 20이 아님(전제 재확인 필요)")
        self.assertEqual(found, expected, "findings.js CATEGORY_LABELS != grm_findings.FINDING_TAXONOMY")

    def test_category_dropdown_never_exposes_raw_code(self):
        """카테고리 <select> 옵션 텍스트는 항상 '{ko} · {en}' — snake_case 코드가 옵션
        표시 로직(카드 렌더 로직과 별개)에 그대로 노출되는 경로가 없는지 소스 마커로 확인."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        # category_code 분기에서 raw value(v)를 그대로 textContent 로 쓰는 건 CATEGORY_LABELS
        # 미존재(방어적 폴백)일 때 뿐이고, 정상 경로는 "ko · en" 조합이어야 한다.
        self.assertIn('cat.ko + " · " + cat.en', js_src)
        self.assertIn('if (key2 === "category_code")', js_src)

    def test_fnd_orig_and_translation_note_styles_present(self):
        # findings/index.html 은 정적 셸이라 .fnd-orig/.fnd-tr-note 스타일 규칙만 여기 있고,
        # 실제 <details>/<span> 마크업은 findings.js 가 런타임에 생성한다(별도 마커 테스트).
        self.assertIn(".fnd-orig", self.html)
        self.assertIn(".fnd-tr-note", self.html)
        self.assertIn("summary", self.html)  # .fnd-orig summary{...} 셀렉터

    def test_legacy_fetch_fallback_marker_present(self):
        """005(finding_text_ko/translation_method) 미적용 라이브 DB 에서도 페이지가 깨지지
        않도록, 비-200 응답 시 legacy FIELDS 로 재시도하는 폴백 로직이 배선돼 있어야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("LEGACY_FIELDS", js_src)
        self.assertIn("finding_text_ko", js_src)
        self.assertIn("translation_method", js_src)
        self.assertIn("fetchFindings(LEGACY_FIELDS)", js_src)

    # ── FIND-1 M7: 대시보드 밴드(정적 셸=hidden 빈 컨테이너, 로직=findings.js) ──────────
    def test_dash_shell_present_and_hidden_by_default(self):
        """#fnd-dash 는 골든 결정론을 위해 항상 빈 컨테이너+hidden 셸로만 렌더된다
        (통계/분포/추이/업체 실제 채움은 findings.js 가 런타임에 수행)."""
        self.assertIn('<section class="fnd-dash" id="fnd-dash"', self.html)
        self.assertIn('id="fnd-dash-stats"', self.html)
        self.assertIn('id="fnd-dash-cat"', self.html)
        self.assertIn('id="fnd-dash-month"', self.html)
        self.assertIn('id="fnd-dash-firm"', self.html)
        # hidden 속성이 #fnd-dash 여는 태그 자체에 붙어 있어야 한다(기본 숨김 셸).
        import re as _re
        m = _re.search(r'<section class="fnd-dash" id="fnd-dash"[^>]*>', self.html)
        self.assertIsNotNone(m)
        self.assertIn("hidden", m.group(0))
        # 컨테이너는 항상 비어 있다(자식 마커·데이터 문자열 없음 — render() 이전 정적 셸).
        self.assertIn('id="fnd-dash-stats"></div>', self.html)
        self.assertIn('id="fnd-dash-cat" class="fnd-dash-cat"></div>', self.html)
        self.assertIn('id="fnd-dash-month" class="fnd-dash-month"></div>', self.html)
        self.assertIn('id="fnd-dash-firm" class="fnd-dash-firm"></div>', self.html)

    def test_dash_compute_and_click_wiring_markers_present(self):
        """집계는 순수 함수(computeStats 등)로 분리돼 있고, 카테고리/월/업체 클릭이 기존
        state·select 값을 재사용해 render() 를 재호출하는 경로인지 소스 마커로 확인."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        for fn in ("computeStats", "computeAgencyDist", "computeCategoryDist",
                   "computeMonthTrend", "computeFirmTop"):
            self.assertIn("function " + fn + "(", js_src)
        # 클릭 = 기존 state 필드 재사용(별도 상태 저장소 없음) + 기존 render() 재호출.
        self.assertIn("state.category_code = state.category_code === code", js_src)
        self.assertIn("state.month = state.month === month", js_src)
        self.assertIn("state.q = state.q === name", js_src)
        self.assertIn('document.getElementById("fnd-f-category")', js_src)
        self.assertIn('document.getElementById("fnd-f-month")', js_src)
        self.assertIn("function renderDash(matched)", js_src)
        self.assertIn("renderDash(matched)", js_src)  # render() 에서 호출

    def test_dash_category_top8_and_rest_row(self):
        """[그리드 균형 M2a] 카테고리 분포는 상위 8개만 개별 바로 그리고, 나머지는
        "그 외 N건" 한 줄로 합산한다(옛 top6 에서 상향)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderDashCategories(stats)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("var top = stats.categories.slice(0, 8);", fn)
        self.assertIn(
            "var restCount = stats.categories.slice(8).reduce(function (s, c) { return s + c.count; }, 0);",
            fn,
        )
        self.assertIn('buildCatRow("그 외", restCount, maxCount, null)', fn)

    def test_dash_category_label_track_separated_from_bar(self):
        """[라벨·바 트랙 분리 M2a] 카테고리 라벨은 고정폭(110px)+ellipsis 로 잘려 막대·
        건수 트랙과 절대 겹치지 않는다 — 형제 컴포넌트(fnd-dash-firm-name)와 동일하게
        overflow:hidden+white-space:nowrap+min-width:0 을 갖춰야 한다(옛 라벨 CSS 에는
        이 3속성이 빠져 있어 긴 라벨이 자동 최소폭만큼 막대를 밀어내던 게 겹침의 원인).
        buildCatRow() 는 title 속성으로 잘린 전체 라벨을 계속 노출한다."""
        # 정확히 이 셀렉터로 시작하는 규칙만 골라낸다(".fnd-dash-cat-label{" 부분 문자열은
        # ".fnd-dash-cat-row:focus-visible .fnd-dash-cat-label{color:...}" 같은 결합
        # 셀렉터 규칙 끝에도 나타나 첫 occurrence 가 엉뚱한 규칙을 집어올 수 있다).
        anchor = "\n.fnd-dash-cat-label{"
        css = self.html[self.html.index(anchor) + 1:]
        css = css[:css.index("}") + 1]
        self.assertIn("min-width:0", css)
        self.assertIn("overflow:hidden", css)
        self.assertIn("text-overflow:ellipsis", css)
        self.assertIn("white-space:nowrap", css)
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildCatRow(label, count, maxCount, code)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("labelEl.title = label;", fn)

    def test_dash_grid_uses_minmax_zero_to_keep_columns_balanced(self):
        """[그리드 균형 M2b] .fnd-dash-grid 의 3컬럼은 minmax(0,1fr) 이어야 한다 — 맨 1fr
        은 자식의 min-content 가 크면(예: 라벨 오버플로) 그 컬럼이 제 몫보다 넓어지고
        나머지 컬럼(월별 추이/업체 상위)이 눌리는 CSS Grid 기본 함정이 있다. 자식 그리드
        아이템(.fnd-dash-block)도 min-width:0 으로 동일 계열 안전장치를 갖춘다."""
        self.assertIn(
            ".fnd-dash-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}",
            self.html,
        )
        self.assertIn(".fnd-dash-block{min-width:0}", self.html)

    def test_dash_accessibility_markers_present(self):
        """클릭 가능한 대시보드 행(카테고리/월/업체)은 role=button+tabindex+키보드
        Enter/Space 활성화를 갖춰야 한다(마우스 전용 UI 금지)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('setAttribute("role", "button")', js_src)
        self.assertIn("tabIndex = 0", js_src)
        self.assertIn('setAttribute("aria-label"', js_src)
        self.assertIn('ev.key === "Enter"', js_src)
        self.assertIn('ev.key === " "', js_src)

    def test_dash_hides_when_zero_results(self):
        """데이터 로드 실패/0건이면 밴드 자체를 숨긴다(빈 필터 결과에서도 동일)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("if (!matched.length) {\n      dashEl.hidden = true;", js_src)

    def test_dash_no_innerhtml_data_injection(self):
        """대시보드 렌더 함수도 기존 계약(innerHTML 데이터 삽입 금지)을 따른다 — innerHTML
        사용은 컨테이너 비우기(= \"\")뿐이어야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        import re as _re
        for m in _re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', js_src):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입 의심: {m.group(0)}")

    def test_dash_no_new_external_resources(self):
        """대시보드는 순수 vanilla JS/CSS 만 사용 — 새 CDN·스크립트·차트 라이브러리를 추가
        하지 않는다. base.html 의 공통 폰트/아이콘 CDN(fonts.googleapis/cdn.jsdelivr)은
        기존 계약이라 대상이 아니다 — findings.html/findings.js 소스 자체에 새 외부 참조나
        차트 라이브러리 마커가 없는지 확인한다(div 막대만 사용)."""
        findings_html_src = (WEB_DIR / "templates" / "findings.html").read_text(encoding="utf-8")
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        for forbidden in ("cdn.", "chart.js", "Chart.js", "d3.", "echarts",
                           '<script src="http', "<canvas"):
            self.assertNotIn(forbidden, findings_html_src, forbidden)
            self.assertNotIn(forbidden, js_src, forbidden)
        # findings.html 은 여전히 findings.js 하나만 <script> 로 참조한다(신규 태그 無).
        self.assertEqual(findings_html_src.count("<script"), 1)

    def test_ai_disclosure_mentions_translation(self):
        # AI 고지 문단에 국문 해석=AI 번역·법적 판단은 원문 기준이라는 문장이 추가됐는지.
        self.assertIn("국문 해석", self.html)
        self.assertIn("AI 번역", self.html)
        self.assertIn("원문을 기준", self.html)

    # ── FIND-1 M9a: 공개 게이트 이후 "번역 대기" 칩 제거 ─────────────────────────
    def test_pending_translation_chip_removed_from_dashboard(self):
        """006_findings_publish_gate.sql 이 DB 레벨에서 미번역 행을 anon fetch 결과에서
        차단하므로, 클라이언트가 세던 '번역 대기 N건' chip 은 항상 0에 수렴해 오해를
        일으킨다 -- computeStats/렌더 양쪽에서 완전히 제거됐는지 소스 마커로 확인한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertNotIn("pendingTranslation", js_src)
        self.assertNotIn("번역 대기", js_src)

    def test_needs_review_chip_still_present(self):
        """번역 대기 chip 제거가 인접한 '검토 필요' chip 로직까지 지우지 않았는지 확인."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("stats.needsReview", js_src)
        self.assertIn("검토 필요", js_src)

    # ── FIND-1 M15: AI 고지를 하단 기존 .ai-disclosure 디자인으로 이전 ──────────────────
    def test_notice_moved_to_bottom_ai_disclosure(self):
        """[M15] 상단 슬림 고지(.fnd-notice/#findings-notice)는 완전히 제거됐고, landing.html
        과 동일한 .ai-disclosure 구조(kick + h2 + .disc-body > .disc-sec)가 페이지 하단에
        findings 전용 id(#ai-notice)로 렌더된다."""
        import re as _re
        self.assertNotIn("fnd-notice", self.html)
        self.assertNotIn('id="findings-notice"', self.html)
        m = _re.search(
            r'<section class="ai-disclosure" id="ai-notice" aria-label="AI 자동 생성 고지">',
            self.html)
        self.assertIsNotNone(m, "하단 ai-disclosure 섹션 마커 미발견")
        block = self.html[m.start():]
        self.assertIn('<span class="kick">AI Disclosure</span>', block[:400])
        self.assertIn("<h2>콘텐츠 생성 방식 및 유의사항</h2>", block[:400])
        self.assertEqual(block.count('<div class="disc-sec">'), 4)

    def test_ai_disclosure_appears_after_results_section(self):
        """[M15] AI 고지는 더 이상 첫 화면(page-head 바로 아래)이 아니라 본문(검색 결과)
        뒤로 이동했다 — 마크업 순서로 fnd-results 가 ai-notice 보다 먼저 나와야 한다."""
        self.assertLess(self.html.index('id="fnd-results"'), self.html.index('id="ai-notice"'))

    def test_page_head_description_is_one_sentence(self):
        """첫 화면 밀도(보조) — page-head 설명문단이 압축된 한 문장(마침표 1개로 종결)인지 확인."""
        import re as _re
        m = _re.search(r'<p class="reveal"[^>]*>([^<]*)</p>', self.html)
        self.assertIsNotNone(m)
        text = m.group(1)
        self.assertEqual(text.count("."), 1, f"한 문장이 아닌 것으로 보임: {text!r}")
        self.assertTrue(text.endswith("검색합니다."), text)

    def test_review_card_boundary_markers_present(self):
        """[M14] 검토 필요(needs_review) 카드는 article 에 fnd-card--review 클래스가 붙는다.
        상시 경고 한 줄(.fnd-review-note)은 완전히 제거됐다 — 그 역할은 ①"검토 필요" 배지
        (coral) ②카드 좌측 보더 ③배지 title 툴팁이 담당한다(AI 경고문구 중복 통폐합)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('card.classList.add("fnd-card--review")', js_src)
        self.assertNotIn("fnd-review-note", js_src)
        self.assertNotIn("appendReviewNote", js_src)
        self.assertNotIn(".fnd-review-note", self.html)
        # CSS: coral 왼쪽 보더만(배경 틴트는 제거 — 카드 리스트 얼룩 방지, coral=주의 전용).
        import re as _re
        m = _re.search(r'\.fnd-card\.fnd-card--review\{([^}]*)\}', self.html)
        self.assertIsNotNone(m, ".fnd-card.fnd-card--review CSS 규칙 미발견")
        rule = m.group(1)
        self.assertIn("border-left:3px solid var(--coral)", rule)
        self.assertNotIn("coral-tint", rule)
        self.assertNotIn("background", rule)

    def test_review_note_confidence_percent_marker(self):
        """신뢰도 표시는 Math.round(confidence*100) 로 산출되고, confidence 없으면
        생략되는 분기가 있어야 한다(소스 마커)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("Math.round(Number(row.confidence) * 100)", js_src)
        self.assertIn("신뢰도 ", js_src)

    def test_card_default_collapsed_and_more_toggle(self):
        """카드는 기본 접힘(.fnd-collapsed)이고, "자세히 보기"/"접기" 토글 버튼이
        aria-expanded 를 갖춘 button 으로 textContent 라벨만 쓰는지 확인."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('card.classList.add("fnd-collapsed")', js_src)
        self.assertIn("자세히 보기", js_src)
        self.assertIn('btn.textContent = expanded ? "접기" : "자세히 보기"', js_src)
        self.assertIn('setAttribute("aria-expanded"', js_src)
        # CSS: 접힘 상태에서만 3줄 클램프 + 부가 섹션(.fnd-extra) 숨김.
        self.assertIn(".fnd-card.fnd-collapsed .fnd-text", self.html)
        self.assertIn("-webkit-line-clamp:3", self.html)
        self.assertIn(".fnd-card.fnd-collapsed .fnd-extra{display:none}", self.html)

    def test_highlight_uses_textnode_and_createelement_mark(self):
        """매칭어 하이라이트(P1)는 text node 분할 + createElement("mark") 조립로만
        구현돼야 한다 — innerHTML/정규식 치환 문자열 삽입 금지(XSS 계약)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function appendHighlighted(parent, text, query)", js_src)
        self.assertIn('document.createElement("mark")', js_src)
        self.assertIn('mark.className = "fnd-hl"', js_src)
        self.assertIn("document.createTextNode", js_src)
        # CSS 마커.
        self.assertIn(".fnd-hl", self.html)

    def test_refs_missing_chip_marker_present(self):
        """cfr_refs/mfds_refs 가 둘 다 비어있으면 회색 '조항 미추출' 칩을 렌더한다
        (한글이므로 .fnd-ref 재사용 없이 별도 클래스, mono 미적용)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("fnd-ref-missing", js_src)
        self.assertIn("조항 미추출", js_src)
        # CSS: .fnd-ref-missing 은 .fnd-ref 와 달리 font-family mono 를 쓰지 않는다.
        import re as _re
        m = _re.search(r'\.fnd-ref-missing\{([^}]*)\}', self.html)
        self.assertIsNotNone(m, ".fnd-ref-missing CSS 규칙 미발견")
        self.assertNotIn("var(--mono)", m.group(1), "한글 칩에 mono 적용(§4 위반 위험)")

    def test_meta_line_document_id_and_confidence_marker(self):
        """펼침 영역 하단 메타 줄 = 문서번호(mono, ASCII) · 신뢰도(퍼센트)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function appendMetaLine(extra, row, query)", js_src)
        self.assertIn('meta.appendChild(document.createTextNode("문서번호 "))', js_src)
        self.assertIn("fnd-meta-doc", js_src)
        self.assertIn(".fnd-meta-doc{font-family:var(--mono)}", self.html)

    def test_findings_js_still_no_innerhtml_data_injection_after_m10b(self):
        """M10b 신규 렌더 경로(하이라이트/접힘/메타)도 기존 XSS 계약을 지킨다 —
        innerHTML 대입은 컨테이너 비우기("")뿐이어야 한다(전역 재확인)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        import re as _re
        for m in _re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', js_src):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입 의심: {m.group(0)}")

    # ── FIND-1 M15: 필터 전면 재설계(균일 셀렉트 행 + 적용 필터 칩) ─────────────────────
    def test_all_low_cardinality_filters_are_selects_not_chip_groups(self):
        """[M15] 소스·증거등급·검토상태도 카테고리·발행월과 동일하게 <select> 로 통일됐다
        (균일 셀렉트 행) — 옛 버튼 칩 그룹 컨테이너(.fnd-chipgroup)는 완전히 제거. [M14]
        기관(agency) 필드는 여전히 DOM 에 없다(state.agency/URL 매칭 로직만 findings.js
        소스에 잔존 — 별도 마커로 확인)."""
        self.assertNotIn("fnd-chipgroup", self.html)
        self.assertNotIn("fnd-chip", self.html)
        for facet_id in ("fnd-f-source", "fnd-f-evidence", "fnd-f-status", "fnd-f-category", "fnd-f-month"):
            self.assertIn(f'<select id="{facet_id}"', self.html)
        self.assertNotIn('id="fnd-f-agency"', self.html)

    def test_select_row_order_and_sort_at_end(self):
        """[M15] 셀렉트 행 순서 = 소스·증거등급·검토상태·카테고리·발행월 + 우측 끝 정렬."""
        order = ["fnd-f-source", "fnd-f-evidence", "fnd-f-status", "fnd-f-category", "fnd-f-month", "fnd-sort"]
        positions = [self.html.index(f'id="{fid}"') for fid in order]
        self.assertEqual(positions, sorted(positions), "필터 셀렉트 순서가 스펙과 다름")

    def test_select_field_widths_match_spec(self):
        """[M15] 필드별 고정 폭: 소스 150 · 증거 110 · 검토 120 · 카테고리 230 · 발행월 110 ·
        정렬 110(px, 모바일 100%)."""
        widths = {
            "fnd-f-source": "150px", "fnd-f-evidence": "110px", "fnd-f-status": "120px",
            "fnd-f-category": "230px", "fnd-f-month": "110px", "fnd-sort": "110px",
        }
        for fid, width in widths.items():
            self.assertIn(f"#{fid}{{width:{width}}}", self.html, f"{fid} 폭 규칙 미발견")

    def test_uniform_select_style(self):
        """[M15] 전 셀렉트 동일 컴포넌트 — height 36px, font-size 13px."""
        import re as _re
        m = _re.search(r'\.fnd-field select\{([^}]*)\}', self.html)
        self.assertIsNotNone(m, ".fnd-field select CSS 규칙 미발견")
        rule = m.group(1)
        self.assertIn("height:36px", rule)
        self.assertIn("font-size:13px", rule)

    def test_reset_button_removed(self):
        """[M15] #fnd-reset 초기화 버튼은 완전히 제거됐다 — 적용 필터 칩 행의 "모두 지우기"가
        그 역할을 대체한다."""
        self.assertNotIn('id="fnd-reset"', self.html)
        self.assertNotIn("fnd-reset", self.html)

    def test_active_filters_row_shell_present(self):
        """[M15] #fnd-active 는 골든 결정론을 위해 정적 셸에서 빈 hidden 컨테이너로만
        렌더되고(활성 필터가 없는 초기 상태), findings.js 가 render() 마다 채운다.
        모바일 필터 접기(#fnd-filters) 대상 밖(형제)에 배치돼 접힘 상태에서도 노출된다 —
        #fnd-filters 가 </div> 로 닫힌 *직후* 형제로 오는지 마크업 인접성으로 확인한다."""
        self.assertIn('<div class="fnd-active" id="fnd-active" hidden></div>', self.html)
        self.assertIn(
            '</select></div>\n      </div>\n      <div class="fnd-active" id="fnd-active" hidden></div>',
            self.html,
            "#fnd-active 가 #fnd-filters 의 형제(밖)로 배치되지 않음",
        )

    def test_agency_state_and_url_matching_retained_without_chip_dom(self):
        """[M14] 기관 칩 UI 는 사라졌지만 state.agency·URL param(agency)·매칭 로직은
        findings.js 소스에 남아 있어야 한다 — URL 로 agency 가 들어오면 여전히 필터링된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('agency: "", category_code: "", source: ""', js_src)
        self.assertIn('agency: "agency"', js_src)  # URL_KEYS
        self.assertIn("state.agency && row.agency !== state.agency", js_src)
        self.assertNotIn('"fnd-f-agency"', js_src)

    def test_select_facet_skeleton_and_refresh_wiring_present(self):
        """[M15] CHIP_FACETS 는 완전히 제거됐다 — SELECT_FACETS 단일 경로로 5개 셀렉트의
        DOM(옵션)은 1회만 만들고(스켈레톤), 매 render() 마다 건수·disabled 만 갱신한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertNotIn("CHIP_FACETS", js_src)
        self.assertIn(
            'var SELECT_FACETS = [\n    ["fnd-f-source", "source"],\n'
            '    ["fnd-f-evidence", "evidence_level"],\n    ["fnd-f-status", "review_status"],\n'
            '    ["fnd-f-category", "category_code"],\n    ["fnd-f-month", "month"],\n  ];',
            js_src,
        )
        self.assertIn("function buildFacetSkeleton()", js_src)
        self.assertIn("function refreshFacetUI()", js_src)
        self.assertIn("refreshFacetUI()", js_src[js_src.index("function render()"):], "render() 가 refreshFacetUI 호출 안 함")

    def test_facet_counts_use_standard_faceting_exclude_self(self):
        """칩/옵션 건수는 '검색어 + 그 파셋을 제외한 나머지 활성 필터' 기준(표준 파세팅) —
        자기 자신 필터는 제외하고 계산해야 칩이 항상 의미 있는 건수를 보여준다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function computeFacetCounts(key2)", js_src)
        self.assertIn("function rowMatchesFilters(row, exclude)", js_src)
        self.assertIn("rowMatchesFilters(r, key2)", js_src)
        # matches(row) 는 exclude 없이(=전체 적용) rowMatchesFilters 를 재사용(기존 계약 유지).
        self.assertIn("return rowMatchesFilters(row, null)", js_src)

    def test_search_haystack_includes_visible_facets_and_labels(self):
        """사용자가 화면에서 보는 메타데이터도 검색 대상이다 — 기관/소스/증거/검토상태/
        카테고리/월/refs 가 빠지면 '보이는데 검색 0건' UX 회귀가 난다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function searchTermsFor(row)", js_src)
        self.assertIn("searchTermsFor(row)", js_src[js_src.index("function rowMatchesFilters"):])
        for marker in (
            "row.agency", "row.source", "row.published_date", "monthOf(row)",
            "row.evidence_level", "EVIDENCE_LABEL[row.evidence_level]",
            '"증거 " + row.evidence_level',
            "row.review_status", "reviewStatusPlain", "STATUS_LABEL[row.review_status]",
            "row.category_code", "row.category_label_ko", "cat.ko", "cat.en",
            "arrayTerms(row.cfr_refs)", "arrayTerms(row.mfds_refs)",
        ):
            self.assertIn(marker, js_src)

    def test_sort_select_present_with_three_options(self):
        self.assertIn('<select id="fnd-sort">', self.html)
        self.assertIn('<option value="date_desc">최신순</option>', self.html)
        self.assertIn('<option value="date_asc">오래된순</option>', self.html)
        self.assertIn('<option value="firm_asc">업체명순</option>', self.html)
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function sortRows(rows)", js_src)
        self.assertIn('sort: "date_desc"', js_src)

    def test_tools_sticky_below_nav_and_below_nav_z_index(self):
        """.fnd-tools 는 top:66px(사이트 nav 높이) sticky, z-index 는 nav(50) 미만이어야
        한다 — 겹칠 때 nav(드롭다운/모바일 메뉴)가 항상 위에 오도록(불변 계약)."""
        import re as _re
        m = _re.search(r'\.fnd-tools\{([^}]*)\}', self.html)
        self.assertIsNotNone(m, ".fnd-tools CSS 규칙 미발견")
        rule = m.group(1)
        self.assertIn("position:sticky", rule)
        self.assertIn("top:66px", rule)
        zm = _re.search(r'z-index:(\d+)', rule)
        self.assertIsNotNone(zm, ".fnd-tools 에 z-index 미지정")
        self.assertLess(int(zm.group(1)), 50)

    def test_tools_appears_before_dashboard_in_markup(self):
        """검색이 최우선 도구 — 배치 순서는 page-head → 고지 → tools(sticky) → dash → 결과."""
        self.assertLess(self.html.index('id="fnd-tools"'), self.html.index('id="fnd-dash"'))

    def test_mobile_filters_toggle_present(self):
        """≤700px 에서만 보이는 "필터·정렬" 토글 버튼 + 활성 필터 개수 배지."""
        self.assertIn('id="fnd-filters-toggle"', self.html)
        self.assertIn('aria-controls="fnd-filters"', self.html)
        self.assertIn('id="fnd-filters-badge"', self.html)
        self.assertIn("max-width:700px", self.html)
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('filtersEl.classList.toggle("open")', js_src)
        self.assertIn("function countActiveFilters()", js_src)
        self.assertIn("function updateFiltersToggleBadge()", js_src)

    def test_dashboard_grid_collapse_toggle_present(self):
        """대시보드는 스탯 줄은 항상 노출, 3블록 그리드만 토글로 접는다(모바일 기본 접힘)."""
        self.assertIn('id="fnd-dash-toggle"', self.html)
        self.assertIn('aria-controls="fnd-dash-grid"', self.html)
        self.assertIn('id="fnd-dash-grid"', self.html)
        self.assertIn(".fnd-dash-grid--collapsed{display:none}", self.html)
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('matchMedia("(max-width:700px)")', js_src)
        self.assertIn('classList.toggle("fnd-dash-grid--collapsed")', js_src)

    def test_url_sync_uses_replacestate_only_no_pushstate(self):
        """URL 동기화는 history.replaceState 만 쓴다 — pushState 는 뒤로가기 히스토리를
        오염시키므로 findings.js 어디에도 존재하면 안 된다(불변 계약)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('history.replaceState(null, "", newUrl)', js_src)
        self.assertNotIn("pushState(", js_src)
        self.assertIn("function syncStateToUrl()", js_src)
        self.assertIn("function readStateFromUrl()", js_src)

    def test_url_param_scheme_matches_spec(self):
        """URL 파라미터 스킴 = q/agency/cat/src/ev/status/m/sort (state 키와 1:1 매핑)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        for pair in ('q: "q"', 'agency: "agency"', 'category_code: "cat"', 'source: "src"',
                     'evidence_level: "ev"', 'review_status: "status"', 'month: "m"', 'sort: "sort"'):
            self.assertIn(pair, js_src)

    def test_url_sync_ignores_unknown_values_silently(self):
        """URL 의 알 수 없는/무효한 파셋 값·정렬 값은 조용히 무시한다(오류·크래시 없이)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("collectFacetValues(k).indexOf(raw) !== -1", js_src)
        self.assertIn("SORT_VALUES.indexOf(sortRaw) !== -1", js_src)

    def test_clear_all_filters_resets_sort_and_relies_on_render_for_url_clear(self):
        """[M15] #fnd-reset 버튼은 제거됐다 — "모두 지우기"는 clearAllFilters() 가 담당하며
        sort 도 기본값(date_desc)으로 되돌린다. [문서 단위 페이지네이션] 전체 초기화는
        currentPage=1 로 되돌리고 goToPage(1) 로 재렌더하며(goToPage → render()), 그
        render()의 syncStateToUrl() 이 기본 state 를 반영해 querystring 을 자동으로 비운다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function clearAllFilters()", js_src)
        fn_block = js_src[js_src.index("function clearAllFilters()"):js_src.index("function clearAllFilters()") + 320]
        self.assertIn('sort: "date_desc"', fn_block)
        self.assertIn("syncControlsFromState()", fn_block)
        self.assertIn("currentPage = 1", fn_block)
        self.assertIn("goToPage(1)", fn_block)
        # goToPage 가 실제로 render() 를 호출해 재렌더(→URL clear)를 일으키는지 확인.
        goto_block = js_src[js_src.index("function goToPage(n)"):js_src.index("function goToPage(n)") + 600]
        self.assertIn("render()", goto_block)

    def test_active_filter_chips_clear_and_clear_all_wiring(self):
        """[M15] 적용 필터 칩 각각은 클릭 시 해당 조건만 해제(clearActiveFilter)하고,
        전부 textContent/createElement 로만 조립된다(XSS 계약). "모두 지우기"는
        clearAllFilters 를 호출한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function clearActiveFilter(key)", js_src)
        self.assertIn("function renderActiveChips()", js_src)
        self.assertIn("function buildActiveChip(label, value, onClear)", js_src)
        self.assertIn('btn.setAttribute("aria-label", label + " 필터 해제")', js_src)
        self.assertIn('clearAllBtn.addEventListener("click", clearAllFilters)', js_src)
        self.assertIn("renderActiveChips();", js_src[js_src.index("function render()"):], "render() 가 renderActiveChips 호출 안 함")
        # 정렬(sort)은 필터가 아니므로 칩 대상에서 제외된다.
        active_defs_block = js_src[js_src.index("var ACTIVE_FILTER_DEFS"):js_src.index("var ACTIVE_FILTER_DEFS") + 500]
        self.assertNotIn('"sort"', active_defs_block)

    def test_findings_js_toolbar_features_no_innerhtml_data_injection(self):
        """M10c 신규 경로(칩/셀렉트 갱신·URL 동기화)도 기존 XSS 계약을 지킨다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        import re as _re
        for m in _re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', js_src):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입 의심: {m.group(0)}")

    # ── FIND-1 M13a: 신뢰도 UX 분리(증거등급 vs 검토상태 배지) ─────────────────────
    def test_evidence_badge_no_longer_uses_coral_tint(self):
        """.fnd-b.ev-a 는 더 이상 coral-tint 를 쓰지 않는다 — Evidence A(신뢰 높음)와
        needs-review(주의 신호)가 같은 색으로 강조되는 시각 혼동을 없앤다. ev-a 는 중립
        강조(--strong/--ink)로 분리되고, needs-review 는 coral-tint 를 그대로 유지한다."""
        import re as _re
        ev_a_rule = _re.search(r'\.fnd-b\.ev-a\{([^}]*)\}', self.html)
        self.assertIsNotNone(ev_a_rule, ".fnd-b.ev-a CSS 규칙 미발견")
        self.assertNotIn("var(--coral-tint)", ev_a_rule.group(1))
        self.assertIn("var(--strong)", ev_a_rule.group(1))
        self.assertIn("var(--ink)", ev_a_rule.group(1))

        review_rule = _re.search(r'\.fnd-b\.needs-review\{([^}]*)\}', self.html)
        self.assertIsNotNone(review_rule, ".fnd-b.needs-review CSS 규칙 미발견")
        self.assertIn("var(--coral-tint)", review_rule.group(1))

    def test_badge_title_tooltips_present(self):
        """증거등급/검토상태 배지는 의미를 즉답하는 title 툴팁을 갖는다(순수 setAttribute
        — XSS 무관). accepted 는 결정론 규칙 자동 승인이지 사람 검수 완료가 아니므로
        그렇게 쓰지 않는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        for title in (
            "Evidence A — 1차 공식문서에서 직접 추출(신뢰도 높음)",
            "Evidence B — 공식 인덱스+보조 자료 기반(원문 대조 권장)",
            "Evidence C — 보조 출처 단독(참고용)",
            "AI 추출 후 사람 검수 전 — 원문 대조 필수",
            "결정론 추출 규칙 통과(자동 승인)",
        ):
            self.assertIn(title, js_src)
        self.assertNotIn("사람 검수 완료", js_src)
        self.assertIn('evBadge.setAttribute("title", evTitle)', js_src)
        self.assertIn('reviewBadge.setAttribute("title", STATUS_TITLE.needs_review)', js_src)
        self.assertIn('statusBadge.setAttribute("title", statusTitle)', js_src)

    # ── FIND-1 M14: 디자인 오버홀(한글 줄바꿈·AI 통폐합·필터 정렬·대시보드·카드·카테고리순서) ──
    def test_main_scoped_korean_keep_all_word_break(self):
        """[M14 §1 P0] 한국어 음절 중간 줄바꿈 방지 — 이 페이지(main) 범위에 word-break:
        keep-all + overflow-wrap:anywhere 를 적용한다(grm.css 는 불가침이라 페이지 자체
        <style> 에 스코프)."""
        self.assertIn("main{word-break:keep-all;overflow-wrap:anywhere}", self.html)

    def test_dash_stat_blocks_replace_stat_chips(self):
        """[M14 §4] 대시보드 스탯 줄 → 스탯 블록(큰 숫자+라벨 가로 나열)으로 재작성됐다 —
        옛 총건수 span+칩(.fnd-dash-chip) 마크업은 제거되고, renderDashStats 가
        전체→기관 순회→검토필요(>0) 순으로 블록을 만든다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function renderDashStats(stats)", js_src)
        self.assertIn("function buildStatBlock(num, label, warn)", js_src)
        self.assertIn('buildStatBlock(String(stats.total), "전체", false)', js_src)
        self.assertIn('buildStatBlock(String(a.count), a.agency, false)', js_src)
        self.assertIn('buildStatBlock(String(stats.needsReview), "검토 필요", true)', js_src)
        self.assertNotIn("fnd-dash-chip", js_src)
        self.assertNotIn("fnd-dash-chip", self.html)
        self.assertIn(".fnd-dash-stat-num{", self.html)
        self.assertIn("font-size:22px;font-weight:700;color:var(--ink)", self.html)
        self.assertIn(".fnd-dash-stat-num.warn{color:var(--coral-2)}", self.html)
        self.assertIn(".fnd-dash-stat-lbl{font-size:11px;color:var(--muted)", self.html)

    def test_card_head_date_pushed_right_via_margin_auto(self):
        """[M14 §5] 카드 head — date 는 배지 줄 마지막 자식으로 붙어 margin-left:auto 로
        우측에 고정된다(flex 행에서 첫 자식에 auto 마진을 주면 행 전체가 밀리므로, 좌측
        배지들 다음에 와야 한다). 기관(agency) 배지는 head 에서 완전히 제거됐다."""
        self.assertIn(".fnd-b.date{margin-left:auto", self.html)
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        head_block = js_src[js_src.index('el("div", "fnd-card-head")'):js_src.index("card.appendChild(head)")]
        self.assertNotIn("row.agency", head_block)
        # date 배지가 head 조립부의 마지막 appendChild 호출이어야 우측 고정이 실제로 동작한다.
        self.assertTrue(head_block.rstrip().endswith(
            'head.appendChild(el("span", "fnd-b date", row.published_date || ""));'
        ), "date 배지가 head 의 마지막 자식으로 추가되지 않음(우측 고정 깨짐)")

    def test_translation_note_generated_inside_orig_details(self):
        """[M14 §2] "AI 번역 — 원문 대조 권장" 은 원문 <details> 내부(summary 아래·원문
        <p> 위)에서 생성돼야 한다 — 접힌 기본 화면에는 노출되지 않고, 원문을 펼쳐 대조하는
        맥락에서만 보인다(클래스·문구는 기존 테스트 마커와 동일하게 불변)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function appendOrigAndNote"):js_src.index("function appendOrigAndNote") + 700]
        idx_summary_append = fn.index("details.appendChild(summary)")
        idx_trnote = fn.index('el("span", "fnd-tr-note", "AI 번역 — 원문 대조 권장")')
        idx_p_append = fn.index("details.appendChild(p)")
        self.assertLess(idx_summary_append, idx_trnote, "tr-note 가 summary 보다 먼저 옴")
        self.assertLess(idx_trnote, idx_p_append, "tr-note 가 원문 <p> 보다 뒤에 생성됨")
        self.assertIn("details.appendChild(el(", fn[idx_trnote - 30:idx_trnote + 10])

    def test_category_dropdown_uses_taxonomy_declaration_order(self):
        """[M14 §6] 카테고리 <select> 옵션은 category_code 알파벳순이 아니라 CATEGORY_LABELS
        선언 순서(=grm_findings.FINDING_TAXONOMY 계약 순서)를 따른다 — 한국어 사용자에게
        snake_case 알파벳순은 무작위로 보이기 때문."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function categoryCodesInTaxonomyOrder(available)", js_src)
        self.assertIn("Object.keys(CATEGORY_LABELS).filter(", js_src)
        self.assertIn('if (key2 === "category_code") values = categoryCodesInTaxonomyOrder(values)', js_src)

    def test_agency_chip_field_and_label_removed_from_filters(self):
        """[M14 §3] 기관 필터 필드(라벨+칩그룹 wrapper)가 필터 섹션에서 완전히 제거됐다."""
        self.assertNotIn('id="fnd-f-agency-lbl"', self.html)
        self.assertNotIn(">기관<", self.html)

    def test_search_row_contains_count_and_filters_align_flex_start(self):
        """[M14 §3] 결과 카운트(#fnd-count)가 검색창 행(.fnd-search) 내부로 이동했고,
        필터 툴바는 align-items:flex-start 로 라벨 시작 높이를 통일한다."""
        import re as _re
        m = _re.search(r'<div class="fnd-search">(.*?)</div>', self.html, _re.S)
        self.assertIsNotNone(m)
        self.assertIn('id="fnd-count"', m.group(1))
        fm = _re.search(r'\.fnd-filters\{([^}]*)\}', self.html)
        self.assertIsNotNone(fm, ".fnd-filters CSS 규칙 미발견")
        self.assertIn("align-items:flex-start", fm.group(1))
        self.assertNotIn("align-items:end", fm.group(1))

    # ── [공개 범위 투명성] 검색 페이지 커버리지 노트 ─────────────────────────────
    def test_coverage_note_shell_present_hidden_and_positioned(self):
        """정적 셸은 hidden 빈 노트만 렌더(골든 결정론) — findings.js 가 런타임에 채운다.
        기존 .imp(시사점) 토큰을 재사용하므로 신규 CSS 는 0 이어야 한다. 위치는 대시보드
        섹션 아래·검색 결과 섹션 위(필터 영역 아래·결과 목록 상단)."""
        self.assertIn(
            '<div class="imp" id="fnd-coverage-note" hidden><p id="fnd-coverage-text"></p></div>',
            self.html,
        )
        dash_idx = self.html.index('id="fnd-dash"')
        note_idx = self.html.index('id="fnd-coverage-note"')
        results_idx = self.html.index('aria-label="검색 결과"')
        self.assertTrue(dash_idx < note_idx < results_idx,
                         "커버리지 노트가 대시보드~검색결과 사이에 있지 않음")

    def test_coverage_note_independent_fetch_and_silent_fallback(self):
        """findings_stats RPC(006 공개 게이트를 우회하는 전량 집계, trends.js 와 동일
        엔드포인트)를 메인 fetchFindings() 와 완전히 독립된 별도 promise 체인으로 fetch
        한다 — 실패(RPC 미존재 등)해도 이 노트만 조용히 hidden 유지하고 검색 자체엔
        영향이 없어야 한다(trends.js 히트맵 404 폴백과 동일 패턴)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function fetchCoverageNote()", js_src)
        self.assertIn('"/rest/v1/rpc/findings_stats"', js_src)
        self.assertIn('method: "POST"', js_src)
        self.assertIn('apikey: key, Authorization: "Bearer " + key', js_src)
        fn = js_src[js_src.index("function fetchCoverageNote()"):]
        fn = fn[:fn.index("\n  showState(\"loading\");")]
        self.assertIn(".catch(function () {", fn)
        # 실패 콜백은 로딩/에러 상태(showState)를 건드리지 않는다 — 노트만 독립적으로 숨김.
        catch_body = fn[fn.index(".catch(function () {"):]
        self.assertNotIn("showState(", catch_body)
        self.assertNotIn("coverageNoteEl.hidden = false", catch_body)
        # 메인 검색 fetch 호출 앞에 독립적으로 1회 호출된다(둘 다 showState("loading") 직후).
        self.assertIn('fetchCoverageNote();\n  fetchFindings(FIELDS)', js_src)

    def test_coverage_note_numbers_not_hardcoded_and_locale_formatted(self):
        """숫자(공개/전체 건수)는 findings_stats RPC 응답의 totals.public_findings/
        totals.findings 에서 런타임에 채워지며(하드코딩 금지), toLocaleString('ko-KR')
        로 천단위 구분한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function fetchCoverageNote()"):]
        fn = fn[:fn.index("\n  showState(\"loading\");")]
        self.assertIn("totals.public_findings", fn)
        self.assertIn("totals.findings", fn)
        self.assertIn('.toLocaleString("ko-KR")', fn)
        self.assertIn("건 중 ", fn)
        self.assertIn("건 국문 열람 가능", fn)
        # [진행형 문구 중립화 M4] "(매일 확대 중)" 진행형 문구는 완전히 제거됐다.
        self.assertNotIn("매일 확대 중", fn)
        # textContent 로만 채운다(innerHTML 데이터 삽입 금지 계약).
        self.assertIn("coverageTextEl.textContent =", fn)

    def test_coverage_note_element_lookup_is_defensive(self):
        """구버전 셸(노트 엘리먼트 없음)에서도 조용히 no-op — findings.js 의 hasDash 관례와
        동형 방어적 조회."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('document.getElementById("fnd-coverage-note")', js_src)
        self.assertIn('document.getElementById("fnd-coverage-text")', js_src)
        self.assertIn("if (!coverageNoteEl || !coverageTextEl) return;", js_src)

    # ── [문서 수 병기] totals.documents(010_findings_scope_purity.sql) 있음/없음 두 경로 ──
    def test_coverage_note_documents_present_path_mentions_document_count(self):
        """010 적용 라이브(totals.documents 존재)에서는 "규제 문서 N건 · 지적사항 M건 중
        P건 국문 열람 가능" 식으로 문서-지적 1:N 관계를 명시한다(진행형 "공개"·"매일
        확대 중" 문구는 쓰지 않는다 — M4 중립화)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function fetchCoverageNote()"):]
        fn = fn[:fn.index("\n  showState(\"loading\");")]
        self.assertIn("totals.documents", fn)
        self.assertIn('typeof totals.documents === "number"', fn)
        self.assertIn("규제 문서 ", fn)
        self.assertIn("건 · 지적사항 ", fn)
        self.assertIn("건 중 ", fn)
        self.assertIn("건 국문 열람 가능", fn)

    def test_coverage_note_documents_absent_path_falls_back_silently(self):
        """010 미적용 라이브(totals.documents=undefined)에서는 문서 수 없는 "지적사항
        N건 중 M건 국문 열람 가능" 문안을 쓴다 — 레이아웃/구조는 그대로, 문구만 M4 에서
        진행형("현재 N건 공개 / 전체 M건 집계 반영 (매일 확대 중)")을 중립 서술로 갈음."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function fetchCoverageNote()"):]
        fn = fn[:fn.index("\n  showState(\"loading\");")]
        self.assertIn('"지적사항 " + total + "건 중 " + pub + "건 국문 열람 가능"', fn)
        self.assertNotIn("매일 확대 중", fn)
        # 두 경로 모두 삼항연산자 한 문장으로 분기(방어적 no-op 이 아니라 문구 전환) —
        # 완역 자동 전환(isComplete)이 최상위 분기, hasDocs 가 그 아래 분기다.
        self.assertIn("var hasDocs = ", fn)
        self.assertIn("coverageTextEl.textContent = isComplete", fn)

    def test_coverage_note_complete_state_switches_wording(self):
        """[완역 자동 전환] 미번역 잔량 5건 이하(findings-public_findings<=5)면 미완료
        문안("N건 중 M건 국문 열람 가능")이 완료형("전체를 국문으로 열람할 수 있습니다")
        으로 스스로 전환된다 — 완역 도달 시점에 별도 배포가 필요 없도록 조건을 미리
        심어둔 계약."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function fetchCoverageNote()"):]
        fn = fn[:fn.index("\n  showState(\"loading\");")]
        self.assertIn("var isComplete =", fn)
        self.assertIn("<= 5", fn)
        # 0/0 오탐 방지: findings 가 0(집계 미로드·초기 상태)이면 완료로 판정하지 않는다.
        self.assertIn("Number(totals.findings || 0) > 0", fn)
        self.assertIn("건 전체를 국문으로 열람할 수 있습니다.", fn)
        # 완료형에도 hasDocs 유무(010 미적용) 폴백이 있다.
        self.assertIn('전체 " + total + "건을 국문으로 열람할 수 있습니다.', fn)

    # ── [문서 중심 열람] observation 조각 → 문서 카드 재편 ─────────────────────────────
    def test_raw_signal_id_added_to_select_fields(self):
        """문서 그룹핑 키(raw_signal_id, 002_findings.sql FK)가 FIELDS/LEGACY_FIELDS
        select 목록에 추가돼야 한다 — anon RLS(003)는 행 필터만 있고 컬럼 제한이 없어
        select 확장 자체는 안전하다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fields_block = js_src[js_src.index("var FIELDS = ["):js_src.index("var LEGACY_FIELDS")]
        self.assertIn('"raw_signal_id"', fields_block)
        # LEGACY_FIELDS 는 finding_text_ko/translation_method 두 필드만 제외한 파생 배열이라
        # (필터 목록에 raw_signal_id 가 없으면) raw_signal_id 는 자동으로 포함된다.
        self.assertIn(
            'return f !== "finding_text_ko" && f !== "translation_method";',
            js_src,
        )

    # ── [업체 프로파일 진입] firm_key select 확장 + 013 미적용 방어 폴백 ──────────────
    def test_firm_key_added_to_select_fields_with_dedicated_fallback(self):
        """013_findings_firm_key.sql 의 firm_key(generated 컬럼)가 FIELDS select 목록에
        추가돼야 하고, 013 미적용 라이브(그 컬럼만 없는 경우)에도 페이지가 깨지지 않도록
        FIELDS_NO_FIRM_KEY 로 재시도하는 별도 폴백 단계가 있어야 한다 — 005 폴백
        (LEGACY_FIELDS)과 독립적인 축이라 3단계 체인이 된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fields_block = js_src[js_src.index("var FIELDS = ["):js_src.index("var LEGACY_FIELDS")]
        self.assertIn('"firm_key"', fields_block)
        self.assertIn("var FIELDS_NO_FIRM_KEY = FIELDS.filter(function (f) {", js_src)
        self.assertIn('return f !== "firm_key";', js_src)
        # LEGACY_FIELDS 는 FIELDS_NO_FIRM_KEY 파생이라(FIELDS 가 아니라) firm_key 는
        # 최종 legacy 폴백에도 전이적으로 제외된다(중복 실패 축 없음).
        self.assertIn("var LEGACY_FIELDS = FIELDS_NO_FIRM_KEY.filter(function (f) {", js_src)

    def test_findings_fetch_three_stage_fallback_chain(self):
        """fetchFindings(FIELDS) 실패 → fetchFindings(FIELDS_NO_FIRM_KEY) 실패 →
        fetchFindings(LEGACY_FIELDS) 순서로 재시도한다(013 만 미적용/013·005 둘 다
        미적용 라이브 모두 방어)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        chain = js_src[js_src.index("fetchFindings(FIELDS)"):js_src.index(".catch(function () {\n      showState(\"error\");")]
        idx_full = chain.index("fetchFindings(FIELDS)")
        idx_no_firm = chain.index("fetchFindings(FIELDS_NO_FIRM_KEY)")
        idx_legacy = chain.index("fetchFindings(LEGACY_FIELDS)")
        self.assertTrue(idx_full < idx_no_firm < idx_legacy)

    def test_document_card_head_links_to_firm_profile_when_key_present(self):
        """문서 카드 헤더 업체명은 firm_key(013)가 있으면 /findings/firm/?key= 링크,
        없으면(013 미적용 라이브) 기존처럼 링크 없는 텍스트 그대로 렌더한다(방어). 둘 다
        firmDisplay(=decodeFirmDisplay(head.firm_name))를 표시에 쓴다(M5 엔티티 디코드)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildDocHead(rows)"):]
        fn = fn[:fn.index("var meta = el(\"div\", \"fnd-doc-meta\")")]
        self.assertIn("var firmDisplay = decodeFirmDisplay(head.firm_name);", fn)
        self.assertIn("if (head.firm_key) {", fn)
        self.assertIn(
            'firmLink.href = "firm/index.html?key=" + encodeURIComponent(head.firm_key);',
            fn,
        )
        self.assertIn("firmLink.textContent = firmDisplay;", fn)
        # firm_key 없는 방어 폴백 경로 — el() 호출은 firmDisplay 를 넘긴다.
        self.assertIn('el("h2", "fnd-doc-firm", firmDisplay)', fn)

    def test_firm_name_html_entity_decode_applied_at_every_display_point(self):
        """[firm_name 엔티티 디코드 M5] DB firm_name 에 &amp;/&#039; 가 이미 이스케이프된
        채로 저장된 행("H &amp; P Industries")도 화면엔 디코드된 형태로 표시된다 —
        decodeFirmDisplay() 는 이 2종 엔티티만 순수 문자열 replace 로 되돌리며(innerHTML
        아님, XSS 무관), 업체명이 표시되는 모든 지점(observation 카드 헤더·문서 카드
        헤더·대시보드 업체 상위)에 적용된다. 클릭/필터 매칭은 raw f.name 그대로 써야
        state.q 비교가 어긋나지 않는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function decodeFirmDisplay(s)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('.replace(/&amp;/g, "&")', fn)
        self.assertIn('.replace(/&#039;/g, "\'")', fn)
        self.assertIn(
            'card.appendChild(elHL("h3", "fnd-firm", decodeFirmDisplay(row.firm_name), query));',
            js_src,
        )
        dash_firms_fn = js_src[js_src.index("function renderDashFirms(stats)"):]
        dash_firms_fn = dash_firms_fn[:dash_firms_fn.index("\n  }\n") + 4]
        self.assertIn("var firmDisplay = decodeFirmDisplay(f.name);", dash_firms_fn)
        self.assertIn('el("span", "fnd-dash-firm-name", firmDisplay)', dash_firms_fn)
        # 클릭 핸들러는 여전히 raw f.name 을 넘겨 state.q 비교/검색 매칭이 어긋나지 않는다.
        self.assertIn("toggleFirmFilter(f.name);", dash_firms_fn)

    def test_group_by_document_merges_same_raw_signal_id(self):
        """groupByDocument() 는 raw_signal_id 가 같은 행을 하나의 그룹으로 병합하고,
        raw_signal_id 가 없는 방어적 케이스는 홀로 자기 그룹을 이뤄 결과 누락 없이
        렌더되게 한다(그룹핑 실패=검색 결과 실종 방지)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function groupByDocument(rows)", js_src)
        fn = js_src[js_src.index("function groupByDocument(rows)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("row.raw_signal_id ||", fn)
        self.assertIn("byKey[key].push(row)", fn)
        self.assertIn("order.push(key)", fn)
        self.assertIn("return order.map(function (key) { return byKey[key]; });", fn)

    def test_group_by_document_preserves_sort_order_no_extra_fetch(self):
        """문서 순서는 matched(정렬 완료) 배열에서 그 문서의 첫 지적사항이 나타나는
        위치를 그대로 따른다(재정렬 없음). [문서 단위 페이지네이션] 필터/정렬/검색어
        변경은 wire() 안에서 currentPage 를 1로 리셋한 뒤 goToPage(1) 을 호출할 뿐이다
        — wire() 함수 본문 자체는 fetchFindings 를 직접 호출하지 않는다(흔한 경우
        goToPage(1) 이 이미 로드된 ROWS 만으로 동기적으로 끝나 새 네트워크 요청이
        없다 — 결과가 희소한 필터일 때만 ensurePageReady() 가 추가 청크를 당길 수
        있다는 점은 별도 스코프 주석 참조)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var matched = sortRows(ROWS.filter(matches));", js_src)
        self.assertIn("var docs = groupByDocument(matched);", js_src)
        wire_fn = js_src[js_src.index("function wire() {"):js_src.index("function buildEndpoint")]
        self.assertNotIn("fetchFindings", wire_fn)
        self.assertIn("currentPage = 1", wire_fn)
        self.assertEqual(wire_fn.count("goToPage(1)"), 3, "셀렉트·정렬·검색어 3개 핸들러 모두 goToPage(1) 호출해야 함")

    def test_document_card_reuses_existing_observation_card_render(self):
        """문서 카드는 새 observation 렌더를 만들지 않고 기존 buildCard() 를 그대로
        재사용한다(카테고리 칩·국문 우선·원문 details 접기·LEGACY_FIELDS 폴백 무변경)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function buildDocCard(rows, query)", js_src)
        fn = js_src[js_src.index("function buildDocCard(rows, query)"):]
        fn = fn[:fn.index("return { card: doc, built: built };")]
        self.assertEqual(fn.count("buildCard(row, query)"), 2)  # 보이는 5개 + 접힌 나머지 양쪽 경로

    def test_document_collapse_threshold_and_toggle_present(self):
        """[긴 문서 접기] 6개 이상이면 처음 5개만 펼치고 나머지는 "지적 N건 모두 보기"
        토글로 감춘다(textContent/createElement 만 사용, innerHTML 금지)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var DOC_OBS_VISIBLE_LIMIT = 6;", js_src)
        self.assertIn("var DOC_OBS_INITIAL_SHOW = 5;", js_src)
        self.assertIn("var overflows = rows.length >= DOC_OBS_VISIBLE_LIMIT;", js_src)
        self.assertIn("var visibleCount = overflows ? DOC_OBS_INITIAL_SHOW : rows.length;", js_src)
        self.assertIn('btn.textContent = "지적 " + totalCount + "건 모두 보기";', js_src)
        self.assertIn('btn.textContent = expanded ? "접기" : "지적 " + totalCount + "건 모두 보기";', js_src)
        self.assertIn('hiddenWrap.hidden = true;', js_src)
        self.assertIn('setAttribute("aria-expanded"', js_src[js_src.index("function buildDocObsToggle"):])

    def test_document_collapse_no_innerhtml_data_injection(self):
        """문서 카드 신규 렌더 경로도 기존 XSS 계약을 지킨다 — innerHTML 대입은
        컨테이너 비우기("")뿐이어야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        import re as _re
        for m in _re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', js_src):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입 의심: {m.group(0)}")

    def test_result_count_line_shows_document_finding_and_page_summary(self):
        """[문서 단위 페이지네이션] 결과 요약 줄(#fnd-count)은 "전체 N문서 · M지적 ·
        페이지 X / Y" 형태여야 한다 — N=totalDocsKnown, M=totalFindingsKnown(무필터+RPC
        확보 시 findings_stats exact 총수, 그 외엔 groupByDocument()/matched 로드 기준),
        X=현재 페이지, Y=지금까지 알려진(또는 exact) 총 페이지 수."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        start = js_src.index("function render() {")
        render_fn = js_src[start:js_src.index("\n  function ", start + 20)]
        self.assertIn('bDocs.textContent = totalDocsKnown.toLocaleString("ko-KR")', render_fn)
        self.assertIn('bObs.textContent = totalFindingsKnown.toLocaleString("ko-KR")', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode("전체 "));', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode("문서 · "));', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode("지적 · 페이지 "));', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode(" / "));', render_fn)
        self.assertNotIn('countEl.appendChild(document.createTextNode("총 "));', js_src)

    def test_result_count_line_all_numbers_use_locale_string(self):
        """[콤마 통일] 카운트 줄의 문서수·지적수·총 페이지수는 모두 toLocaleString
        ('ko-KR') 로 천단위 콤마를 붙인다(현재 페이지 번호만은 순수 정수라 콤마 대상이
        아니다) — String(totalDocsKnown)/String(totalFindingsKnown) 처럼 콤마 없는 표기가
        남아있으면 안 된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        render_fn = js_src[js_src.index("function render() {"):js_src.index("\n  function ", js_src.index("function render() {") + 20)]
        self.assertNotIn("String(totalDocsKnown)", render_fn)
        self.assertNotIn("String(totalFindingsKnown)", render_fn)
        self.assertIn('bDocs.textContent = totalDocsKnown.toLocaleString("ko-KR")', render_fn)
        self.assertIn('bObs.textContent = totalFindingsKnown.toLocaleString("ko-KR")', render_fn)
        self.assertIn('bTotal.textContent = String(totalPagesKnown)', render_fn)

    def test_exact_totals_shared_from_coverage_rpc_and_used_when_unfiltered(self):
        """[정확 총수 M1a] fetchCoverageNote() 가 findings_stats RPC 의 totals.documents/
        totals.findings 를 전역(SERVER_DOC_TOTAL/SERVER_FINDINGS_TOTAL)에 공유하고,
        render() 는 무필터(검색어·필터 전부 비었음) + 서버 미소진 구간에서만 그 exact
        값으로 문서수·지적수·총 페이지를 계산한다(exactUnfiltered) — 필터가 걸리면
        로드 기준(docs.length/matched.length)을 그대로 쓴다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var SERVER_DOC_TOTAL = null;", js_src)
        self.assertIn("var SERVER_FINDINGS_TOTAL = null;", js_src)
        cov_fn = js_src[js_src.index("function fetchCoverageNote()"):]
        cov_fn = cov_fn[:cov_fn.index("\n  showState(\"loading\");")]
        self.assertIn("if (hasDocs) SERVER_DOC_TOTAL = totals.documents;", cov_fn)
        self.assertIn(
            'if (typeof totals.findings === "number" && !isNaN(totals.findings)) {\n'
            "          SERVER_FINDINGS_TOTAL = totals.findings;\n"
            "        }",
            cov_fn,
        )
        render_fn = js_src[js_src.index("function render() {"):js_src.index("\n  function ", js_src.index("function render() {") + 20)]
        self.assertIn(
            "var exactUnfiltered = !filtersActive && moreMayExist && SERVER_DOC_TOTAL !== null;",
            render_fn,
        )
        self.assertIn("totalDocsKnown = SERVER_DOC_TOTAL;", render_fn)
        self.assertIn("if (SERVER_FINDINGS_TOTAL !== null) totalFindingsKnown = SERVER_FINDINGS_TOTAL;", render_fn)
        self.assertIn(
            "totalPagesKnown = Math.max(1, Math.ceil(SERVER_DOC_TOTAL / DOCS_PER_PAGE));",
            render_fn,
        )

    def test_uncertain_suffix_replaces_old_plus_sign_in_count_line(self):
        """[정확 표기] 무필터+exact 확보 시("exactUnfiltered")에는 접미사가 전혀 붙지
        않는다. 필터가 걸려있거나 RPC 를 아직 확보하지 못했으면서 서버가 아직 소진되지
        않았을 때만(uncertain) 숫자 뒤에 옛 "+" 기호 대신 " 이상"을 붙인다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        render_fn = js_src[js_src.index("function render() {"):js_src.index("\n  function ", js_src.index("function render() {") + 20)]
        self.assertIn("var uncertain = moreMayExist && !exactUnfiltered;", render_fn)
        self.assertEqual(render_fn.count('countEl.appendChild(document.createTextNode(" 이상"));'), 3)
        self.assertNotIn('toLocaleString("ko-KR") + (moreMayExist ? "+" : "")', render_fn)

    def test_document_card_head_markers_and_css_present(self):
        """문서 헤더 = 업체명(세리프, .fnd-firm 관례 계승) + 소스·발행일·지적 건수 메타.
        CSS 는 findings.html 자체 <style> 스코프에만 추가되고(grm.css 무변경), 기존
        .fnd-b/.fnd-card 스타일 계열을 재사용한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function buildDocHead(rows)", js_src)
        self.assertIn('el("h2", "fnd-doc-firm", firmDisplay)', js_src)
        self.assertIn('meta.appendChild(el("span", "fnd-b", head.source));', js_src)
        self.assertIn('meta.appendChild(el("span", "fnd-doc-count", "지적 " + rows.length + "건"));', js_src)
        self.assertIn(".fnd-doc{border:1px solid var(--line-2);border-radius:var(--rad);padding:20px 22px;background:var(--canvas)}", self.html)
        self.assertIn("font-family:var(--serif)", self.html[self.html.index(".fnd-doc-firm{"):self.html.index(".fnd-doc-firm{") + 100])

    def test_findings_html_script_style_unchanged_scope(self):
        """findings.html 은 여전히 findings.js 하나만 <script> 로 참조하고, 문서 카드
        CSS 는 이 페이지 자체 <style> 블록에만 존재한다(grm.css 파일 자체는 건드리지
        않는다 — 별도 grm.css byte-verbatim 테스트가 이를 전역으로 보증)."""
        findings_html_src = (WEB_DIR / "templates" / "findings.html").read_text(encoding="utf-8")
        self.assertEqual(findings_html_src.count("<script"), 1)
        self.assertIn(".fnd-doc{", findings_html_src)
        self.assertIn(".fnd-doc-toggle{", findings_html_src)

    # ── [문서 단위 페이지네이션] 이전/다음+페이지 번호 + 점진 로드 + 서버 정확 카운트 ──────
    def test_pager_shell_present_hidden_and_defensive_lookup(self):
        """정적 셸은 빈 hidden <nav> 만 렌더(골든 결정론) — 이전/다음·페이지 번호·처음/끝
        버튼은 findings.js 의 renderPager() 가 채운다. 상단(#fnd-pager-top)·하단
        (#fnd-pager-bottom) 둘 다 존재해야 하고, 엘리먼트 부재(구버전 셸)에서도 hasDash
        관례와 동형으로 조용히 no-op 이어야 한다."""
        self.assertIn(
            '<nav class="fnd-pager" id="fnd-pager-top" aria-label="검색 결과 페이지 이동" hidden></nav>',
            self.html,
        )
        self.assertIn(
            '<nav class="fnd-pager" id="fnd-pager-bottom" aria-label="검색 결과 페이지 이동" hidden></nav>',
            self.html,
        )
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('document.getElementById("fnd-pager-top")', js_src)
        self.assertIn('document.getElementById("fnd-pager-bottom")', js_src)
        # renderPager()/setPagerLoading()/hidePager() 전부 [pagerTopEl, pagerBottomEl] 를
        # forEach 로 순회하며 개별 null 체크한다(부재 시 조용히 no-op).
        self.assertIn("[pagerTopEl, pagerBottomEl].forEach(function (nav) {\n      if (!nav) return;", js_src)

    def test_inline_load_more_button_removed_from_search_row(self):
        """[문서 단위 페이지네이션] 옛 카운트 줄 옆 인라인 "더 보기" 버튼(#fnd-load-more-top
        의 구버전 — 검색창 행 안에 있던 작은 알약형 버튼)은 완전히 제거됐다. 검색 결과
        페이지 이동은 이제 #fnd-pager-top/#fnd-pager-bottom 전체 페이지네이션 바가
        담당한다."""
        self.assertNotIn("fnd-load-more-inline", self.html)
        self.assertNotIn("fnd-load-more-wrap", self.html)
        self.assertNotIn('id="fnd-load-more"', self.html)
        m_search = self.html.index('<div class="fnd-search">')
        m_close = self.html.index("</div>", m_search)
        self.assertIn(
            '<div class="fnd-count" id="fnd-count" role="status" aria-live="polite"></div>',
            self.html[m_search:m_close + 6],
        )

    def test_content_range_prefer_header_and_parsing(self):
        """서버 exact count 확보 — fetch 헤더에 Prefer: count=exact 를 실어 보내고,
        응답 Content-Range("0-999/1926" 형식)에서 총수를 파싱한다. 파싱 실패/헤더
        미노출 시 null 폴백(기존 동작 유지) — 하드코딩 숫자 없음."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('Prefer: "count=exact"', js_src)
        self.assertIn("function parseServerTotal(resp)", js_src)
        fn = js_src[js_src.index("function parseServerTotal(resp)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('resp.headers.get("content-range")', fn)
        self.assertIn("if (!cr) return null;", fn)
        self.assertIn(r"/\/(\d+)$/", fn)
        self.assertIn("return isNaN(n) ? null : n;", fn)

    def test_next_chunk_endpoint_uses_offset_and_reuses_loaded_fields(self):
        """페이지 이동으로 촉발된 청크 fetch 는 최초 로드가 성공시킨 필드셋
        (LOADED_FIELDS)을 그대로 재사용해 offset=ROWS.length 로 다음 청크를 요청한다 —
        3단계 폴백을 매번 재협상하지 않는다. buildEndpoint 는 offset>0 일 때만
        &offset= 을 덧붙인다(생략 시 기존 limit=1000 단일 호출과 동일 — 하위호환)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function buildEndpoint(fields, offset)", js_src)
        self.assertIn('(off > 0 ? "&offset=" + off : "")', js_src)
        self.assertIn("function fetchNextChunkFor(cb)", js_src)
        fn = js_src[js_src.index("function fetchNextChunkFor(cb)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("fetchFindings(LOADED_FIELDS, ROWS.length)", fn)

    def test_duplicate_fetch_guard_queues_callbacks(self):
        """[중복 fetch 방어] 이미 진행 중인 청크 fetch 가 있으면 새 네트워크 요청을
        내지 않고 콜백만 큐(pendingPageCallbacks)에 편승시킨다 — 여러 페이지 이동이
        겹쳐도 실제 fetch 는 항상 1개만 진행되고, 완료 시 대기 콜백이 한 번에 재개된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function fetchNextChunkFor(cb)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("pendingPageCallbacks.push(cb);", fn)
        self.assertIn("if (isFetchingPage) return;", fn)
        self.assertIn("isFetchingPage = true;", fn)
        self.assertIn("isFetchingPage = false;", fn)
        self.assertIn("cbs.forEach(function (fn) { fn(); });", fn)
        # 페이지 이동(goToPage) 도 navToken 세대 카운터로 연타를 방어한다 — 오래된 이동의
        # 완료 콜백은 currentPage/render() 를 건드리지 않는다.
        goto_fn = js_src[js_src.index("function goToPage(n)"):]
        goto_fn = goto_fn[:goto_fn.index("\n  }\n") + 4]
        self.assertIn("navToken += 1;", goto_fn)
        self.assertIn("if (myToken !== navToken) return;", goto_fn)

    def test_merge_rows_dedupes_by_finding_id(self):
        """mergeRows() 는 finding_id 기준 중복을 제거한다 — 두 fetch 사이 새 번역이
        공개(publish gate 통과)돼 정렬 경계에서 행이 밀려도 같은 행이 두 번 들어오지
        않는다. 병합 대상은 항상 전역 ROWS(로드된 전체) — 필터된 부분집합이 아니다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function mergeRows(newRows)", js_src)
        fn = js_src[js_src.index("function mergeRows(newRows)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("var seen = {};", fn)
        self.assertIn("if (id !== undefined && id !== null && seen[id]) return;", fn)
        self.assertIn("ROWS.push(r);", fn)

    def test_pagination_boundary_document_completeness_check(self):
        """[경계 문서 완결성] ensurePageReady() 는 목표 페이지의 마지막 문서
        (raw_signal_id)가 incompleteDocKey() 가 가리키는 "아직 obs 가 더 올 수 있는"
        키와 같으면, 문서 수가 이미 충분해도(docs.length >= neededDocs) 그대로 렌더하지
        않고 한 청크 더 fetch 한 뒤 재확인한다 — 페이지 경계에서 문서가 쪼개진 채
        보이는 상태를 방지한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function incompleteDocKey()", js_src)
        key_fn = js_src[js_src.index("function incompleteDocKey()"):]
        key_fn = key_fn[:key_fn.index("\n  }\n") + 4]
        self.assertIn("if (isServerExhausted() || !ROWS.length) return null;", key_fn)
        self.assertIn("ROWS[ROWS.length - 1].raw_signal_id", key_fn)

        ready_fn = js_src[js_src.index("function ensurePageReady(pageNum, done)"):]
        ready_fn = ready_fn[:ready_fn.index("\n  }\n") + 4]
        self.assertIn("var neededDocs = pageNum * DOCS_PER_PAGE;", ready_fn)
        self.assertIn("var pageSlice = docs.slice(neededDocs - DOCS_PER_PAGE, neededDocs);", ready_fn)
        self.assertIn("var badKey = incompleteDocKey();", ready_fn)
        self.assertIn(
            "var unsafe = badKey !== null && pageSlice.some(function (rows) {\n"
            "          return rows[0].raw_signal_id === badKey;\n"
            "        });",
            ready_fn,
        )
        self.assertIn("fetchNextChunkFor(attempt);", ready_fn)
        # render() 자체는 순수 슬라이스만 한다 — 경계 안전은 페이지 이동 시점에 이미 확인됨.
        render_fn = js_src[js_src.index("function render() {"):]
        self.assertIn(
            "var pageDocs = docs.slice((currentPage - 1) * DOCS_PER_PAGE, currentPage * DOCS_PER_PAGE);",
            render_fn,
        )

    def test_docs_per_page_constant_is_24(self):
        """[문서 단위 페이지네이션] 문서 카드 24개 = 1페이지(스펙 상수)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var DOCS_PER_PAGE = 24;", js_src)

    def test_more_may_exist_indicator_narrows_to_uncertain_state(self):
        """[정확 총수 M1a] moreMayExist(=!isServerExhausted())는 여전히 필터 여부와
        무관하게 동일 기준을 쓴다. 다만 카운트 줄의 "이상" 접미사(구버전 "+")는
        moreMayExist 만이 아니라 exactUnfiltered(무필터+RPC exact 확보)가 아닐 때만
        붙는다(uncertain = moreMayExist && !exactUnfiltered) — 무필터+exact 확보 시에는
        moreMayExist 가 true 여도(서버 청크가 아직 안 끝났어도) 접미사를 붙이지 않는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        start = js_src.index("function render() {")
        render_fn = js_src[start:js_src.index("\n  function ", start + 20)]
        self.assertIn("var moreMayExist = !isServerExhausted();", render_fn)
        self.assertIn("var filtersActive = countActiveFilters() > 0 || !!state.q.trim();", render_fn)
        self.assertIn("var exactUnfiltered = !filtersActive && moreMayExist && SERVER_DOC_TOTAL !== null;", render_fn)
        self.assertIn("var uncertain = moreMayExist && !exactUnfiltered;", render_fn)

    def test_is_server_exhausted_falls_back_to_batch_size_heuristic(self):
        """[방어] Content-Range 파싱 실패(헤더 미노출 등)로 SERVER_TOTAL 이 null 이면,
        "가장 최근 청크가 PAGE_LIMIT 미만이었는지"만으로 서버 소진 여부를 판단한다
        (fetchGaveUp 이면 무조건 소진 취급해 무한 재시도를 막는다)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function isServerExhausted()"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (fetchGaveUp) return true;", fn)
        self.assertIn("if (SERVER_TOTAL !== null) return ROWS.length >= SERVER_TOTAL;", fn)
        self.assertIn("return LAST_BATCH_SIZE !== null && LAST_BATCH_SIZE < PAGE_LIMIT;", fn)

    def test_filter_and_sort_and_search_reset_to_page_one(self):
        """[문서 단위 페이지네이션] 필터·검색·정렬 변경은 모두 currentPage 를 1로
        리셋한 뒤 goToPage(1) 을 호출해야 한다(대시보드 카테고리/월/업체 클릭, 적용
        필터 칩 해제·모두 지우기 포함)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        for fn_name in (
            "function toggleCategoryFilter(code)", "function toggleMonthFilter(month)",
            "function toggleFirmFilter(name)", "function clearActiveFilter(key)",
            "function clearAllFilters()",
        ):
            fn = js_src[js_src.index(fn_name):]
            fn = fn[:fn.index("\n  }\n") + 4]
            self.assertIn("currentPage = 1;", fn, f"{fn_name} 이 페이지를 리셋하지 않음")
            self.assertIn("goToPage(1);", fn, f"{fn_name} 이 goToPage(1) 을 호출하지 않음")

    def test_pager_renders_prev_next_first_last_and_page_window(self):
        """[문서 단위 페이지네이션] renderPager() 는 처음/이전/페이지 번호(윈도우)/
        다음/끝 버튼을 만들고, 현재 페이지 버튼에 aria-current="page" 를 붙인다.
        computePageWindow() 는 7페이지 이하면 생략 없이 전부, 초과하면 "..." 로
        축약한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function computePageWindow(current, total)", js_src)
        win_fn = js_src[js_src.index("function computePageWindow(current, total)"):]
        win_fn = win_fn[:win_fn.index("\n  }\n") + 4]
        self.assertIn("if (total <= 7) {", win_fn)
        self.assertIn('items.push("...");', win_fn)

        pager_fn = js_src[js_src.index("function renderPager(current, total, moreMayExist)"):]
        pager_fn = pager_fn[:pager_fn.index("\n  }\n") + 4]
        self.assertIn('buildPagerBtn("«", "처음 페이지로 이동"', pager_fn)
        # [b 폴리시] 이전/다음은 «‹›» 단독 글리프 대신 아이콘+텍스트를 병기해 처음 보는
        # 사용자에게도 명확하다("‹ 이전"/"다음 ›") — 처음/끝은 압축 글리프 그대로 유지.
        self.assertIn('buildPagerBtn("‹ 이전", "이전 페이지"', pager_fn)
        self.assertIn('buildPagerBtn("다음 ›", "다음 페이지"', pager_fn)
        self.assertIn('buildPagerBtn("»", "끝 페이지로 이동"', pager_fn)
        self.assertIn('btn.setAttribute("aria-current", "page");', pager_fn)
        # 마지막(=지금까지 알려진) 페이지 번호는 moreMayExist 면 "+" 를 덧붙인다(최소 추정 —
        # 이 압축 페이지 버튼의 "+" 표기는 카운트 줄의 " 이상" 문구와 달리 그대로 유지한다,
        # 좁은 버튼 안에서는 기호가 더 명확하다).
        self.assertIn('var label = String(item) + (isLastKnown && moreMayExist ? "+" : "");', pager_fn)
        # [선로딩 c] 페이지네이션 버튼 클릭은 goToPageFromPager() 를 거쳐 완료 후 스크롤한다.
        self.assertEqual(pager_fn.count("goToPageFromPager("), 5)

    def test_pager_css_touch_target_and_loading_status_present(self):
        """[b 폴리시] 페이저 버튼 최소 터치영역 32px(모바일)·현재 페이지 코럴 필 강조·
        로딩 중 nav 옅어짐(aria-busy)·대체 상태 텍스트(.fnd-pager-status) CSS 가
        findings.html 자체 <style> 블록에 존재해야 한다(grm.css 무변경)."""
        self.assertIn('.fnd-pager[aria-busy="true"]{opacity:.7}', self.html)
        self.assertIn(".fnd-pager-status{", self.html)
        self.assertIn(".fnd-pager-btn.on{background:var(--coral-tint)", self.html)
        mobile_block = self.html[self.html.index("@media (max-width:480px){"):]
        mobile_block = mobile_block[:mobile_block.index("}\n")]
        self.assertIn("min-width:32px;height:32px", mobile_block)

    def test_pager_hidden_when_single_page_and_no_more_data(self):
        """결과가 0건이거나(hidePager) 1페이지뿐이고 서버도 소진됐으면(moreMayExist=false)
        페이지네이션 바를 완전히 숨긴다(불필요한 UI 노출 방지)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function hidePager()", js_src)
        self.assertIn("if (pagerTopEl) pagerTopEl.hidden = true;", js_src)
        self.assertIn("if (pagerBottomEl) pagerBottomEl.hidden = true;", js_src)
        pager_fn = js_src[js_src.index("function renderPager(current, total, moreMayExist)"):]
        pager_fn = pager_fn[:pager_fn.index("\n  }\n") + 4]
        self.assertIn("if (total <= 1 && !moreMayExist) {", pager_fn)
        self.assertIn("nav.hidden = true;", pager_fn)
        render_fn = js_src[js_src.index("function render() {"):]
        self.assertIn("hidePager();", render_fn[:render_fn.index("showState(\"none\");")])

    def test_page_url_param_deep_link_and_default_omitted(self):
        """[?page= 딥링크] currentPage>1 일 때만 URL 에 page= 파라미터를 반영한다(1페이지
        기본값은 URL 을 더럽히지 않음). 초기 로드는 readPageFromUrl() 로 복원하고,
        무효/누락 값은 조용히 1로 폴백한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        sync_fn = js_src[js_src.index("function syncStateToUrl()"):]
        sync_fn = sync_fn[:sync_fn.index("\n  }\n") + 4]
        self.assertIn('if (currentPage > 1) params.set("page", String(currentPage));', sync_fn)
        self.assertIn("function readPageFromUrl()", js_src)
        read_fn = js_src[js_src.index("function readPageFromUrl()"):]
        read_fn = read_fn[:read_fn.index("\n  }\n") + 4]
        self.assertIn('var raw = new URLSearchParams(location.search).get("page");', read_fn)
        self.assertIn("return !isNaN(n) && n >= 1 ? n : 1;", read_fn)
        self.assertIn("var initialPage = readPageFromUrl();", js_src)
        self.assertIn("goToPage(initialPage);", js_src)

    def test_pager_loading_shows_status_text_and_disables_buttons(self):
        """[로딩 UX b] 미로드 페이지 이동 중에는 버튼을 disabled 처리하고, 현재 페이지
        pill(.on)이 있으면 그 자리에서 바로 "불러오는 중…" 텍스트로 바꿔 보여준다(없으면
        —페이지 창 밖— nav 끝에 별도 상태 텍스트를 붙인다). 지금까지는 버튼만 비활성화돼
        무반응처럼 보였다는 신고 대응."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function setPagerLoading(loading)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('Array.prototype.forEach.call(nav.querySelectorAll("button"), function (b) { b.disabled = true; });', fn)
        self.assertIn('var current = nav.querySelector(".fnd-pager-btn.on");', fn)
        self.assertIn('current.textContent = "불러오는 중…";', fn)
        self.assertIn('status.className = "fnd-pager-status";', fn)
        self.assertIn('status.textContent = "불러오는 중…";', fn)

    def test_pager_click_scrolls_results_into_view_after_render(self):
        """[로딩 UX b] 페이지네이션 바 클릭(goToPageFromPager())으로 촉발된 이동만 완료
        후 결과 목록(#fnd-results) 상단으로 스크롤한다. 필터/검색/정렬 변경발 goToPage(1)
        리셋(pendingScrollAfterNav 세팅 없음)은 스크롤하지 않아야 한다 — 검색창 타이핑마다
        화면이 튀는 것을 방지."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var pendingScrollAfterNav = false;", js_src)
        goto_fn = js_src[js_src.index("function goToPage(n)"):]
        goto_fn = goto_fn[:goto_fn.index("\n  }\n") + 4]
        self.assertIn("var doScroll = pendingScrollAfterNav;", goto_fn)
        self.assertIn("pendingScrollAfterNav = false;", goto_fn)
        # [로딩 UX b′] sticky 툴바(.fnd-tools, top:66px) 밑에 결과 상단이 가려지지 않도록
        # 오프셋 보정 + instant(auto) 스크롤 — smooth 는 연타 시 버튼 위치가 흘러다녀 교체.
        self.assertIn('document.getElementById("fnd-tools")', goto_fn)
        self.assertIn("getBoundingClientRect().bottom", goto_fn)
        self.assertIn('behavior: "auto"', goto_fn)
        self.assertNotIn('behavior: "smooth"', goto_fn)

    def test_sticky_pnav_prev_next_in_tools_bar(self):
        """[sticky 미니 내비] 이전/다음 버튼이 sticky 툴바(.fnd-tools) 안에 있어 스크롤
        위치와 무관하게 같은 화면 자리에서 연타 가능해야 한다(실사용자 신고: 다음 클릭
        후 화면이 밀려 매번 위로 되돌아가야 했음). 셸 hidden + updatePnav() 상태 관리 +
        renderPager() 동기 + 로딩 중 잠금 계약."""
        self.assertIn('<div class="fnd-pnav" id="fnd-pnav" hidden>', self.html)
        self.assertIn('id="fnd-pnav-prev"', self.html)
        self.assertIn('id="fnd-pnav-next"', self.html)
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function updatePnav(current, total, moreMayExist)", js_src)
        self.assertIn("updatePnav(current, total, moreMayExist); // [sticky 미니 내비]", js_src)
        self.assertIn('goToPageFromPager(currentPage - 1);', js_src)
        self.assertIn('goToPageFromPager(currentPage + 1);', js_src)
        # 로딩 중 연타 방어 — setPagerLoading 이 pnav 도 잠근다.
        loading_fn = js_src[js_src.index("function setPagerLoading(loading)"):]
        loading_fn = loading_fn[:loading_fn.index("\n  }\n") + 4]
        self.assertIn("pnavPrevBtn.disabled = true;", loading_fn)
        self.assertIn("pnavNextBtn.disabled = true;", loading_fn)
        pager_entry_fn = js_src[js_src.index("function goToPageFromPager(n)"):]
        pager_entry_fn = pager_entry_fn[:pager_entry_fn.index("\n  }\n") + 4]
        self.assertIn("pendingScrollAfterNav = true;", pager_entry_fn)
        self.assertIn("goToPage(n);", pager_entry_fn)

    def test_schedule_prefetch_looks_ahead_one_chunk_near_loaded_edge(self):
        """[선로딩 c] render() 후 idle 시간에 다음 청크 1개만 미리 fetch한다(전체 eager
        로드 금지) — 이미 로드된 데이터로 현재 페이지보다 1페이지 이상 여유가 있으면
        아무 것도 하지 않고, 서버가 이미 소진됐거나(uncertainLoad=false) 이미 fetch 중이면
        스킵한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function schedulePrefetch(loadedDocsCount, uncertainLoad)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (!uncertainLoad || isFetchingPage) return;", fn)
        self.assertIn("if (currentPage < loadedPages - 1) return;", fn)
        self.assertIn("requestIdleCallback", fn)
        self.assertIn("fetchNextChunkFor(function () {});", fn)
        render_fn = js_src[js_src.index("function render() {"):js_src.index("\n  function ", js_src.index("function render() {") + 20)]
        self.assertIn("schedulePrefetch(docs.length, moreMayExist);", render_fn)

    def test_dash_total_stat_uses_server_total_when_unfiltered(self):
        """[대시보드 실총수 M3] 필터가 하나도 없을 때만 대시보드 스탯을 findings_stats
        RPC exact 총수로 바꿔치기한다 — "전체"는 SERVER_FINDINGS_TOTAL 우선(없으면
        Content-Range SERVER_TOTAL 폴백), "문서"는 SERVER_DOC_TOTAL(폴백 없음 — 미확보면
        스탯 자체 생략), 소스별(agencies)은 SERVER_AGENCY_TOTALS 확보 시 exact 로 교체
        (agenciesExact=true). 필터가 걸리면 matched.length 가 "필터링된 전체"라는 다른
        모집단이므로 전부 로드 기준(computeStats 원래값)을 그대로 둔다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderDash(matched)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn(
            "var filtersActive = countActiveFilters() > 0 || !!state.q.trim();",
            fn,
        )
        self.assertIn("if (!filtersActive) {", fn)
        self.assertIn(
            "if (SERVER_FINDINGS_TOTAL !== null) {\n"
            "        stats.total = SERVER_FINDINGS_TOTAL;\n"
            "      } else if (SERVER_TOTAL !== null && SERVER_TOTAL > matched.length) {\n"
            "        stats.total = SERVER_TOTAL;\n"
            "      }",
            fn,
        )
        self.assertIn(
            "if (SERVER_DOC_TOTAL !== null) {\n"
            "        stats.documents = SERVER_DOC_TOTAL;\n"
            "      }",
            fn,
        )
        self.assertIn("stats.agenciesExact = true;", fn)
        self.assertIn("renderDashStats(stats);", fn)

    def test_dash_agency_totals_derived_from_by_agency_category_rpc(self):
        """[대시보드 실총수 M3] findings_stats 에는 agency 단독 집계 키가 없어(by_source/
        by_agency_category 만 존재), fetchCoverageNote() 가 by_agency_category(agency×
        category_code 교차표)를 agency 기준으로만 합산해 SERVER_AGENCY_TOTALS 를 채운다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var SERVER_AGENCY_TOTALS = null;", js_src)
        cov_fn = js_src[js_src.index("function fetchCoverageNote()"):]
        cov_fn = cov_fn[:cov_fn.index("\n  showState(\"loading\");")]
        self.assertIn("if (Array.isArray(data.by_agency_category)) {", cov_fn)
        self.assertIn(
            "agencySums[row.agency] = (agencySums[row.agency] || 0) + (row.cnt || 0);",
            cov_fn,
        )
        self.assertIn("SERVER_AGENCY_TOTALS = agencySums;", cov_fn)

    def test_dash_stats_documents_and_agency_estimate_tooltip(self):
        """[대시보드 실총수 M3] renderDashStats() 는 stats.documents 가 있을 때만 "문서"
        스탯 카드를 끼워 넣고(없으면 조용히 생략 — 레이아웃 안 깨짐), stats.agenciesExact
        가 아니면 소스별 스탯 각각에 "로드된 데이터 기준" 툴팁을 달아 추정치임을 시각적으로
        구분한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderDashStats(stats)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (stats.documents !== undefined && stats.documents !== null) {", fn)
        self.assertIn('buildStatBlock(String(stats.documents), "문서", false)', fn)
        self.assertIn("if (!stats.agenciesExact) {", fn)
        self.assertIn('block.title = "현재 로드된 데이터 기준(참고용)";', fn)

    def test_facet_skeleton_idempotent_for_reload_after_more(self):
        """buildFacetSkeleton() 은 페이지 이동으로 청크가 추가 fetch 된 이후에도
        재호출될 수 있어(새로 드러난 값 옵션 추가) 이미 존재하는 옵션 값은 건너뛰어야
        한다 — 그렇지 않으면 재호출 시 <option> 이 중복 생성된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildFacetSkeleton()"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (existing[v]) return;", fn)

    def test_doc_firm_link_affordance_visible_by_default(self):
        """[어포던스 수정] 문서 카드 업체명 링크(.fnd-doc-firm a)는 기본 상태에서도
        일반 텍스트와 구분되는 시각 신호(밑줄+화살표)를 가져야 한다 — hover 시에만
        보이던 이전 규칙은 "클릭이 안 된다"는 오인 신고를 낳았다."""
        import re as _re
        m = _re.search(r"\.fnd-doc-firm a\{([^}]*)\}", self.html)
        self.assertIsNotNone(m, ".fnd-doc-firm a CSS 규칙 미발견")
        self.assertIn("text-decoration:underline", m.group(1))
        self.assertNotIn("text-decoration:none", m.group(1))
        after_m = _re.search(r"\.fnd-doc-firm a::after\{([^}]*)\}", self.html)
        self.assertIsNotNone(after_m, ".fnd-doc-firm a::after 화살표 글리프 미발견")
        self.assertIn('content:"→"', after_m.group(1))


# ── 트렌드 대시보드 (FIND-1 F3b — 셸 렌더·env-gate·sitemap·nav 배선·RPC 배선) ────────
class WebTrendsRenderTest(unittest.TestCase):
    """findings/trends/index.html 은 findings/index.html 과 동형인 정적 셸이다(런타임에
    trends.js 가 Supabase RPC findings_stats/findings_firm_stats 를 직접 fetch). 여기선
    셸 자체의 결정론·env-gate·배선만 검증한다 — 실제 집계 렌더는 trends.js 소관(비골든,
    JS 단위테스트 범위 밖)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_trends_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "trends" / "index.html").read_text(encoding="utf-8")
        cls.findings_html = (cls.single / "findings" / "index.html").read_text(encoding="utf-8")
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.archive = (cls.single / "archive" / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_page_generated(self):
        self.assertIn("규제 지적사항 트렌드", self.html)
        self.assertIn("Findings Intelligence", self.html)

    def test_cfg_div_env_gated_empty_by_default_with_root(self):
        # 테스트 환경엔 SUPABASE_URL/ANON_KEY 미설정 — cfg data 속성은 항상 빈 문자열
        # (findings.js 계약과 동일). data-root 는 rel_root 값("../../")을 그대로 담는다
        # (카테고리 순위 바 → findings 검색 페이지 링크 계산용).
        self.assertIn(
            'id="grm-findings-cfg" data-url="" data-key="" data-root="../../" hidden',
            self.html,
        )

    def test_trends_js_referenced_with_content_hash(self):
        import re as _re
        m = _re.search(r'assets/trends\.js\?v=([0-9a-f]{8})"', self.html)
        self.assertIsNotNone(m, "trends.js 캐시버스팅 해시 미발견")

    def test_trends_js_copied_verbatim(self):
        built = (self.single / "assets" / "trends.js").read_bytes()
        src = (WEB_DIR / "assets" / "trends.js").read_bytes()
        self.assertEqual(built, src, "trends.js 가 dist 에 verbatim 복사되지 않음")

    def test_sitemap_includes_trends(self):
        self.assertIn(f"<loc>{render.SITE_BASE_URL}/findings/trends/</loc>", self.sitemap)

    def test_nav_link_present_and_active_state(self):
        self.assertIn('href="../../findings/trends/index.html" class="on">트렌드</a>', self.html)
        self.assertIn('href="findings/trends/index.html">트렌드</a>', self.landing)
        self.assertIn('href="../findings/trends/index.html">트렌드</a>', self.archive)
        self.assertIn('href="../findings/trends/index.html">트렌드</a>', self.findings_html)
        # 트렌드 페이지 자체에서만 '찾아보기'는 on 이 아니고, '트렌드'만 on.
        import re as _re
        nav_m = _re.search(r'<nav id="navmenu">(.*?)</nav>', self.html, _re.S)
        self.assertIsNotNone(nav_m)
        self.assertNotIn('class="on">찾아보기', nav_m.group(1))
        self.assertEqual(nav_m.group(1).count("<a "), 4)

    def test_footer_link_present(self):
        self.assertIn('<a href="../../findings/trends/index.html">트렌드</a>', self.html)

    def test_findings_page_links_to_trends(self):
        # findings/index.html 헤더 근처에 트렌드 대시보드로 가는 절제된 텍스트 링크 1개.
        self.assertIn('href="../findings/trends/index.html">전체 트렌드 보기', self.findings_html)

    def test_canonical_and_description(self):
        self.assertIn(
            f'<link rel="canonical" href="{render.SITE_BASE_URL}/findings/trends/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

    def test_category_labels_sync_with_taxonomy(self):
        """trends.js 의 CATEGORY_LABELS 는 findings.js 와 동일한 복제본 하드코딩이라
        grm_findings.FINDING_TAXONOMY 20개 code/label_ko/label_en 과 완전히 일치해야
        한다(findings.js 의 동명 테스트와 동일한 대조 — 이중 하드코딩 드리프트 방지)."""
        import re as _re

        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        m = _re.search(r"var CATEGORY_LABELS = \{(.*?)\n  \};", js_src, _re.S)
        self.assertIsNotNone(m, "trends.js 에 CATEGORY_LABELS 정의 미발견")
        body = m.group(1)

        entry_pat = _re.compile(
            r'(\w+):\s*\{\s*ko:\s*"((?:[^"\\]|\\.)*)",\s*en:\s*"((?:[^"\\]|\\.)*)"\s*\}'
        )
        found = {code: (ko, en) for code, ko, en in entry_pat.findall(body)}

        expected = {c.code: (c.label_ko, c.label_en) for c in grm_findings.FINDING_TAXONOMY}
        self.assertEqual(len(expected), 20, "FINDING_TAXONOMY 카테고리 수가 20이 아님(전제 재확인 필요)")
        self.assertEqual(found, expected, "trends.js CATEGORY_LABELS != grm_findings.FINDING_TAXONOMY")

    def test_rpc_endpoints_present(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn('"/rest/v1/rpc/" + name', js_src)
        self.assertIn('rpcEndpoint("findings_stats")', js_src)
        self.assertIn('rpcEndpoint("findings_firm_stats")', js_src)
        self.assertIn('method: "POST"', js_src)
        self.assertIn('apikey: key, Authorization: "Bearer " + key', js_src)
        self.assertIn('JSON.stringify({ p_firm: firmName })', js_src)

    def test_category_bar_links_to_findings_cat_param(self):
        """카테고리 순위 바 클릭 → /findings/?cat={code}(findings.js 의 URL_KEYS.
        category_code="cat" 계약과 일치해야 실제로 필터가 걸린다)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn('findingsHref("cat", entry.code)', js_src)
        self.assertIn(
            'return root + "findings/index.html?" + paramKey + "=" + encodeURIComponent(value);',
            js_src,
        )

    def test_url_sync_uses_replacestate_only_no_pushstate(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn('history.replaceState(null, "", newUrl)', js_src)
        self.assertNotIn("pushState(", js_src)
        self.assertIn("function syncFirmUrl(name)", js_src)
        self.assertIn("function maybeOpenFirmFromUrl()", js_src)
        self.assertIn('params.get("firm")', js_src)

    def test_accessibility_markers_present(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn('setAttribute("role", "button")', js_src)
        self.assertIn("tabIndex = 0", js_src)
        self.assertIn('setAttribute("aria-label"', js_src)
        self.assertIn('ev.key === "Enter"', js_src)
        self.assertIn('ev.key === " "', js_src)

    def test_no_innerhtml_data_injection(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        import re as _re
        for m in _re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', js_src):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입 의심: {m.group(0)}")

    def test_no_new_external_resources(self):
        """차트 라이브러리/CDN/canvas 0 — 순수 div/svg-less 바 렌더만 사용."""
        html_src = (WEB_DIR / "templates" / "trends.html").read_text(encoding="utf-8")
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        for forbidden in ("cdn.", "chart.js", "Chart.js", "d3.", "echarts",
                           '<script src="http', "<canvas"):
            self.assertNotIn(forbidden, html_src, forbidden)
            self.assertNotIn(forbidden, js_src, forbidden)
        self.assertEqual(html_src.count("<script"), 1)

    def test_headline_generation_rules_present(self):
        """한눈 요약 생성 규칙 — 문장1=최다 카테고리(항상), 문장2=YoY 증감(24개월 커버리지
        + 두 구간 모두 0건 아닐 때만) 아니면 최다 업체 문장으로 대체(억지 통계 금지)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function buildHeadline(data)", js_src)
        self.assertIn("function computeYoy(byMonth)", js_src)
        self.assertIn("if (months[0] > prevStart) return null;", js_src)
        self.assertIn("if (prevSum <= 0 || recentSum <= 0) return null;", js_src)
        self.assertIn("가장 많이 지적된 영역은", js_src)
        self.assertIn("지적 건수가 가장 많은 업체는", js_src)

    def test_year_trend_caveat_note_present(self):
        self.assertIn("백필이 진행 중입니다", self.html)
        self.assertIn("하한치", self.html)

    def test_stat_strip_note_present(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("나머지는 집계에만 반영(원문 영문)", js_src)

    def test_page_shell_hidden_pending_load(self):
        """골든 결정론 — #tr-content/#tr-error 는 정적 셸에서 hidden, 로딩 스켈레톤만
        기본 노출(findings.js 의 #fnd-loading 관례와 동형)."""
        self.assertIn('<div id="tr-content" hidden>', self.html)
        self.assertIn('<div class="tr-state tr-state-error" id="tr-error" hidden>', self.html)
        self.assertIn('<div class="tr-state" id="tr-loading" role="status" aria-live="polite">', self.html)

    # ── H1 카테고리×연도 히트맵 ─────────────────────────────────────────────
    def test_heatmap_section_shell_present_and_hidden(self):
        """정적 셸에 히트맵 섹션이 '카테고리 순위'와 '연도별 추이' 사이에 존재하며
        기본 hidden(008 미적용 라이브·fetch 실패 시 trends.js 가 그대로 두는 상태와
        일치 — 골든 결정론)."""
        self.assertIn(
            '<section class="tr-block tr-heatmap-block" id="tr-heatmap-block" '
            'aria-label="카테고리 × 연도 히트맵" hidden>',
            self.html,
        )
        self.assertIn('<h2 class="tr-h">카테고리 × 연도 히트맵</h2>', self.html)
        self.assertIn('<div id="tr-heatmap" class="tr-heatmap"></div>', self.html)
        cat_idx = self.html.index('aria-label="카테고리 순위"')
        heatmap_idx = self.html.index('id="tr-heatmap-block"')
        year_idx = self.html.index('aria-label="연도별 추이"')
        self.assertTrue(cat_idx < heatmap_idx < year_idx,
                         "히트맵 섹션이 카테고리 순위와 연도별 추이 사이에 있지 않음")

    def test_heatmap_rpc_endpoint_present(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn('rpcEndpoint("findings_category_matrix")', js_src)
        self.assertIn("function fetchCategoryMatrix()", js_src)

    def test_heatmap_independent_fetch_and_silent_fallback(self):
        """findings_stats 와 별개 promise 체인으로 병렬 fetch 되고, 실패해도(008 미적용
        라이브의 404 포함) 다른 섹션을 건드리지 않고 조용히 숨김 유지되어야 한다."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("fetchCategoryMatrix()", js_src)
        self.assertIn("function renderHeatmap(data)", js_src)
        # fetchStats() 체인과 독립된 .catch() — errorEl/contentEl 을 건드리지 않는다.
        heatmap_chain = js_src[js_src.index("fetchCategoryMatrix()\n    .then"):]
        self.assertNotIn("errorEl.hidden", heatmap_chain[:400])
        self.assertIn("조용히 숨김 유지", js_src)

    def test_heatmap_table_accessibility_markup(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn('document.createElement("table")', js_src)
        self.assertIn('document.createElement("caption")', js_src)
        self.assertIn('th.setAttribute("scope", "col")', js_src)
        self.assertIn('rowTh.setAttribute("scope", "row")', js_src)

    def test_heatmap_five_step_opacity_buckets(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn(
            "var HEATMAP_OPACITY_STEPS = [0.08, 0.25, 0.45, 0.7, 1.0];", js_src)
        self.assertIn("function heatmapOpacity(cnt, maxCnt)", js_src)
        self.assertIn('td.style.color = opacity > 0.45 ? "var(--on-coral)" : "var(--ink)";',
                       js_src)
        self.assertIn("tr-heatmap-cell-empty", js_src)

    def test_heatmap_scroll_wrapper_present(self):
        html_src = (WEB_DIR / "templates" / "trends.html").read_text(encoding="utf-8")
        self.assertIn(".tr-heatmap-scroll{overflow-x:auto", html_src)

    # ── [공개 범위 투명성] 트렌드 페이지 커버리지 노트 ───────────────────────────
    def test_coverage_note_shell_present_hidden_and_positioned(self):
        """정적 셸은 hidden 빈 노트만 렌더(골든 결정론) — trends.js 가 런타임에 채운다.
        기존 .imp(시사점) 토큰을 재사용하므로 신규 CSS 는 0 이어야 한다. 위치는 스탯
        스트립 직하단·한눈 요약 헤드라인 위."""
        self.assertIn(
            '<div class="imp" id="tr-coverage-note" hidden><p id="tr-coverage-text"></p></div>',
            self.html,
        )
        stats_idx = self.html.index('id="tr-stats"')
        note_idx = self.html.index('id="tr-coverage-note"')
        headline_idx = self.html.index('id="tr-headline"')
        self.assertTrue(stats_idx < note_idx < headline_idx,
                         "커버리지 노트가 스탯 스트립~헤드라인 사이에 있지 않음")

    def test_coverage_note_reuses_fetched_totals_no_extra_network_call(self):
        """카테고리 클릭 → 검색 페이지 이동 결과가 이 페이지의 집계 수치보다 적을 수 있음을
        알리는 안내는, fetchStats() 가 이미 받아온 totals 를 재사용한다 — 별도 fetch/RPC
        호출을 추가하지 않는다(추가 네트워크 호출 0)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function renderCoverageNote(totals)", js_src)
        self.assertIn("renderCoverageNote(totals);", js_src)
        # renderAll(data) 안에서 fetchStats() 가 이미 fetch 한 동일 totals 를 renderStats 와
        # 함께 재사용한다(같은 인자, 새 fetch()/rpcEndpoint() 호출 없음).
        fn = js_src[js_src.index("function renderCoverageNote(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertNotIn("fetch(", fn)
        self.assertNotIn("rpcEndpoint(", fn)

    def test_coverage_note_numbers_not_hardcoded_and_locale_formatted(self):
        """숫자(전체/공개 건수)는 findings_stats RPC 응답의 totals.findings/
        totals.public_findings 에서 채워지며(하드코딩 금지), toLocaleString('ko-KR')
        로 천단위 구분한다."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderCoverageNote(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("totals.findings", fn)
        self.assertIn("totals.public_findings", fn)
        self.assertIn('.toLocaleString("ko-KR")', fn)
        self.assertIn("이 대시보드의 수치는 전체 ", fn)
        self.assertIn("집계 수치보다 적을 수 있습니다.", fn)
        # textContent 로만 채운다(innerHTML 데이터 삽입 금지 계약, 파일 상단 XSS 계약 참조).
        self.assertIn("coverageTextEl.textContent =", fn)

    def test_coverage_note_element_lookup_is_defensive(self):
        """구버전 셸(노트 엘리먼트 없음)에서도 조용히 no-op — trends.js 의 다른 옵셔널
        섹션(히트맵 등)과 동형 방어적 조회."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn('document.getElementById("tr-coverage-note")', js_src)
        self.assertIn('document.getElementById("tr-coverage-text")', js_src)
        self.assertIn("if (!coverageNoteEl || !coverageTextEl) return;", js_src)

    # ── [문서 수 병기] 스탯 스트립 "분석 문서" — totals.documents(010) 있음/없음 두 경로 ──
    def test_stats_documents_present_path_renders_stat(self):
        """totals.documents 가 유효 숫자면 "총 지적사항" 바로 다음에 "분석 문서" 스탯
        카드를 끼워 넣는다(지적 건수=문서 수로 오해하는 문제 완화)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function hasDocumentsCount(totals)", js_src)
        self.assertIn('typeof totals.documents === "number" && !isNaN(totals.documents)', js_src)
        fn = js_src[js_src.index("function renderStats(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("if (hasDocumentsCount(totals)) {", fn)
        self.assertIn('buildStat(fmtNum(totals.documents), "분석 문서")', fn)
        # "총 지적사항" 카드 다음, "업체" 카드 이전에 위치(문서-지적 관계를 바로 옆에서
        # 대조할 수 있도록).
        idx_findings = fn.index('"총 지적사항"')
        idx_docs = fn.index('"분석 문서"')
        idx_firms = fn.index('"업체"')
        self.assertTrue(idx_findings < idx_docs < idx_firms)

    def test_stats_documents_absent_path_omits_stat_without_breaking_layout(self):
        """010 미적용 라이브(totals.documents=undefined)에서는 "분석 문서" 카드를 조용히
        생략한다 — appendChild 가 조건부(if 블록) 안에만 있으므로 나머지 스탯(총 지적사항/
        업체/원문서/국문 열람 가능)은 항상 그대로 렌더되어 레이아웃이 깨지지 않는다."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderStats(totals)"):]
        fn = fn[:fn.index("\n  }")]
        # "분석 문서" 카드 추가가 조건문 내부에 있고, 그 뒤 3개 카드(업체/원문서/국문 열람
        # 가능) 는 조건과 무관하게 무조건 실행된다 — 문서 스탯만 옵셔널.
        guard_idx = fn.index("if (hasDocumentsCount(totals)) {")
        after_guard = fn[fn.index("}", guard_idx) + 1:]
        self.assertIn('buildStat(fmtNum(totals.firms), "업체")', after_guard)
        self.assertIn('buildStat(fmtNum(totals.raw_signals), "원문서")', after_guard)
        self.assertIn('buildStat(fmtNum(totals.public_findings), "국문 열람 가능")', after_guard)

    def test_coverage_note_documents_present_path_mentions_document_count(self):
        """010 적용 라이브(totals.documents 존재)에서는 첫 문장이 "규제 문서 N건에서
        추출한 개별 지적사항 M건" 식으로 문서-지적 1:N 관계를 명시한다."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderCoverageNote(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("hasDocumentsCount(totals)", fn)
        self.assertIn("규제 문서 ", fn)
        self.assertIn("건에서 추출한 개별 지적사항 ", fn)
        self.assertIn("문서당 평균 여러 건", fn)

    def test_coverage_note_documents_absent_path_falls_back_silently(self):
        """010 미적용 라이브(totals.documents=undefined)에서는 기존 "이 대시보드의 수치는
        전체 M건 기준 집계입니다." 문안을 그대로 유지한다 — 방어적 생략, 문구 깨짐 없음."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderCoverageNote(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("이 대시보드의 수치는 전체 ", fn)
        self.assertIn('건 기준 집계입니다."', fn)
        self.assertIn("var intro = hasDocumentsCount(totals)", fn)

    def test_coverage_note_complete_state_switches_wording(self):
        """[완역 자동 전환] 미번역 잔량 5건 이하면 미완료 경고가 "전체 지적사항을
        국문으로 열람할 수 있습니다" 완료형으로 스스로 전환된다(완역 시점엔 카테고리
        클릭 결과와 집계 수치가 일치해 경고가 무의미)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderCoverageNote(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("var isComplete =", fn)
        self.assertIn("<= 5", fn)
        self.assertIn("Number(totals.findings || 0) > 0", fn)
        self.assertIn("전체 지적사항을 국문으로 열람할 수 있습니다.", fn)
        self.assertIn("coverageTextEl.textContent = isComplete", fn)

    def test_coverage_note_incomplete_wording_neutralized(self):
        """[진행형 문구 중립화 M4] 미완료 경고에서 "순차 공개되며"(계속 진행 중이라는
        인상)를 제거하고 "국문 번역이 완료된 지적사항만 가능"이라는 현재 상태 서술로
        바꾼다 — 집계와 클릭 결과가 다를 수 있다는 핵심 정보는 그대로 유지."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderCoverageNote(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertNotIn("순차", fn)
        self.assertIn("국문 번역이 완료된 지적사항(", fn)
        self.assertIn("집계 수치보다 적을 수 있습니다.", fn)

    def test_firm_name_html_entity_decode_applied_at_ranking_and_detail_panel(self):
        """[firm_name 엔티티 디코드 M5] 업체 랭킹(buildFirmRow)·상세 패널 헤더
        (renderFirmDetail)·헤드라인(buildHeadline) 모두 decodeFirmDisplay() 를 거쳐
        표시한다 — 클릭/state 비교(openFirm 호출·state.openFirm===f.firm_name)는
        findings_firm_stats RPC exact-match 파라미터라 raw f.firm_name 그대로 유지한다."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function decodeFirmDisplay(s)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('.replace(/&amp;/g, "&")', fn)
        self.assertIn('.replace(/&#039;/g, "\'")', fn)
        row_fn = js_src[js_src.index("function buildFirmRow(f, idx, maxCnt)"):]
        row_fn = row_fn[:row_fn.index("\n  }\n") + 4]
        self.assertIn("var firmDisplay = decodeFirmDisplay(f.firm_name);", row_fn)
        self.assertIn('el("span", "tr-firm-name", firmDisplay)', row_fn)
        self.assertIn("else openFirm(f.firm_name, f.firm_key);", row_fn)  # 클릭은 raw 그대로
        self.assertIn(
            'idbox.appendChild(el("h3", "tr-firm-detail-name", decodeFirmDisplay(data.firm_name || "")));',
            js_src,
        )
        self.assertIn(
            'lines.push("지적 건수가 가장 많은 업체는 " + decodeFirmDisplay(f.firm_name) + "(" + fmtNum(f.cnt) + "건)입니다.");',
            js_src,
        )

    # ── [업체 프로파일 진입] 017_findings_stats_firm_key.sql top_firms.firm_key 배선 ──
    def test_firm_row_click_passes_firm_key_through(self):
        """업체 랭킹 행 클릭 시 openFirm 에 firm_name 뿐 아니라 f.firm_key 도 함께
        넘긴다(017 미적용 라이브에서는 f.firm_key 가 undefined 라 자연히 방어된다)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("else openFirm(f.firm_name, f.firm_key);", js_src)

    def test_open_firm_stores_key_and_close_resets_it(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function openFirm(name, firmKey)", js_src)
        self.assertIn('state.openFirmKey = firmKey || "";', js_src)
        close_fn = js_src[js_src.index("function closeFirm()"):]
        close_fn = close_fn[:close_fn.index("\n  }")]
        self.assertIn('state.openFirmKey = "";', close_fn)

    def test_profile_link_builder_uses_sibling_relative_path(self):
        """트렌드 페이지(findings/trends/index.html)에서 업체 프로파일 페이지
        (findings/firm/index.html)로는 형제 디렉터리 상대경로 "../firm/index.html" 로
        충분하다(둘 다 findings/ 바로 아래 — findings.js buildDocHead 의
        "firm/index.html" 관례와 동형, root 변수 불필요)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function buildFirmProfileLink(firmKey)", js_src)
        fn = js_src[js_src.index("function buildFirmProfileLink(firmKey)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn(
            'a.href = "../firm/index.html?key=" + encodeURIComponent(firmKey);', fn
        )
        self.assertIn('a.textContent = "업체 프로파일 전체 보기 →";', fn)
        self.assertIn('a.className = "tr-fd-profile-link";', fn)

    def test_profile_link_rendered_at_top_of_detail_panel_only_when_key_present(self):
        """firm_key 가 있을 때만(017 적용 라이브) 패널 최상단(head 보다 먼저)에 링크를
        붙인다 — 013/017 미적용 라이브(구버전 top_firms, firm_key 없음)에서는 렌더 자체를
        생략해 기존 패널과 완전히 동일하게 유지한다(방어 폴백)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderFirmDetail(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("if (state.openFirmKey) {", fn)
        self.assertIn("firmDetailEl.appendChild(buildFirmProfileLink(state.openFirmKey));", fn)
        # 링크 삽입이 head(업체명·닫기 버튼) 삽입보다 앞서야 "패널 상단" 요건을 만족한다.
        key_guard_idx = fn.index("if (state.openFirmKey)")
        head_idx = fn.index('var head = document.createElement("div");')
        self.assertLess(key_guard_idx, head_idx)

    def test_maybe_open_firm_from_url_resolves_key_from_last_firms(self):
        """?firm= 직접 진입(북마크·공유 링크)에도 프로필 링크가 뜨도록, 이미 fetch 된
        state.lastFirms 에서 이름이 일치하는 행의 firm_key 를 찾아 openFirm 에 함께
        넘긴다(017 미적용 라이브에서는 항상 "" 로 방어)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function findFirmKeyByName(name)", js_src)
        fn = js_src[js_src.index("function maybeOpenFirmFromUrl()"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("if (f) openFirm(f, findFirmKeyByName(f));", fn)

    def test_profile_link_css_scoped_to_page_not_grm_css(self):
        """grm.css 는 무변경 — 신규 링크 스타일은 trends.html 자체 스코프 <style> 에만
        추가된다(findings.html 의 .fnd-trends-link 관례와 동형)."""
        html_src = (WEB_DIR / "templates" / "trends.html").read_text(encoding="utf-8")
        self.assertIn(".tr-fd-profile-link{", html_src)
        css_path = WEB_DIR / "assets" / "grm.css"
        if css_path.is_file():
            self.assertNotIn(".tr-fd-profile-link", css_path.read_text(encoding="utf-8"))

    def test_url_sync_still_uses_firm_name_only(self):
        """?firm= URL 파라미터 동기화는 기존과 동일하게 firm_name 기준이다(firm_key 는
        이 파라미터 계약을 바꾸지 않는다 — findings_firm_stats(p_firm) exact-match 계약
        불변)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function syncFirmUrl(name)", js_src)
        self.assertIn('if (name) params.set("firm", name); else params.delete("firm");', js_src)


# ── 업체 프로파일 (FIND-FIRM-ALIAS 웹 절반 — 셸 렌더·env-gate·sitemap·nav 배선·
#    013 미적용 방어 폴백) ──────────────────────────────────────────────────
class WebFirmRenderTest(unittest.TestCase):
    """findings/firm/index.html 은 findings/trends/index.html 과 동형인 정적 셸이다
    (런타임에 firm.js 가 013_findings_firm_key.sql 의 findings_firm_profile RPC 를
    URL 파라미터(?key=)로 직접 fetch). 여기선 셸 자체의 결정론·env-gate·배선·013 미적용
    방어 폴백 마커만 검증한다 — 실제 집계/문서 이력 렌더는 firm.js 소관(비골든, JS
    단위테스트 범위 밖)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_firm_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "firm" / "index.html").read_text(encoding="utf-8")
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.archive = (cls.single / "archive" / "index.html").read_text(encoding="utf-8")
        cls.findings_html = (cls.single / "findings" / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_page_generated(self):
        self.assertIn("업체 프로파일", self.html)
        self.assertIn("Firm Profile", self.html)
        self.assertIn("AI Disclosure", self.html)

    def test_cfg_div_env_gated_empty_by_default_with_root(self):
        # 테스트 환경엔 SUPABASE_URL/ANON_KEY 미설정 — cfg data 속성은 항상 빈 문자열
        # (findings.js/trends.js 계약과 동일). data-root 는 rel_root 값("../../")을 그대로
        # 담는다(카테고리 바 → findings 검색 페이지 링크 계산용, trends.js 와 동일 패턴).
        self.assertIn(
            'id="grm-firm-cfg" data-url="" data-key="" data-root="../../" hidden',
            self.html,
        )

    def test_firm_js_referenced_with_content_hash(self):
        import re as _re
        m = _re.search(r'assets/firm\.js\?v=([0-9a-f]{8})"', self.html)
        self.assertIsNotNone(m, "firm.js 캐시버스팅 해시 미발견")

    def test_firm_js_copied_verbatim(self):
        built = (self.single / "assets" / "firm.js").read_bytes()
        src = (WEB_DIR / "assets" / "firm.js").read_bytes()
        self.assertEqual(built, src, "firm.js 가 dist 에 verbatim 복사되지 않음")

    def test_sitemap_includes_firm_base_path_only(self):
        # 쿼리스트링 기반 동적 조회 페이지라 베이스 경로 1건만 등록(개별 업체 URL 미등록).
        self.assertIn(f"<loc>{render.SITE_BASE_URL}/findings/firm/</loc>", self.sitemap)
        self.assertEqual(self.sitemap.count("/findings/firm/"), 1)
        self.assertNotIn("/findings/firm/?", self.sitemap)

    def test_nav_not_added_entry_only_via_link(self):
        # 요구사항: base.html nav 에 신규 탭을 추가하지 않는다(진입은 findings.js 의 문서
        # 카드 업체명 링크로만) — nav 링크 개수가 findings/trends 페이지와 동일(4개)해야 함.
        import re as _re
        nav_m = _re.search(r'<nav id="navmenu">(.*?)</nav>', self.html, _re.S)
        self.assertIsNotNone(nav_m)
        self.assertEqual(nav_m.group(1).count("<a "), 4)
        self.assertNotIn("findings/firm", nav_m.group(1))

    def test_canonical_and_description(self):
        self.assertIn(
            f'<link rel="canonical" href="{render.SITE_BASE_URL}/findings/firm/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

    def test_category_labels_sync_with_taxonomy(self):
        """firm.js 의 CATEGORY_LABELS 는 findings.js/trends.js 와 동일한 복제본
        하드코딩이라 grm_findings.FINDING_TAXONOMY 20개 code/label_ko/label_en 과
        완전히 일치해야 한다(이중 하드코딩 드리프트 방지)."""
        import re as _re

        js_src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        m = _re.search(r"var CATEGORY_LABELS = \{(.*?)\n  \};", js_src, _re.S)
        self.assertIsNotNone(m, "firm.js 에 CATEGORY_LABELS 정의 미발견")
        body = m.group(1)

        entry_pat = _re.compile(
            r'(\w+):\s*\{\s*ko:\s*"((?:[^"\\]|\\.)*)",\s*en:\s*"((?:[^"\\]|\\.)*)"\s*\}'
        )
        found = {code: (ko, en) for code, ko, en in entry_pat.findall(body)}

        expected = {c.code: (c.label_ko, c.label_en) for c in grm_findings.FINDING_TAXONOMY}
        self.assertEqual(len(expected), 20, "FINDING_TAXONOMY 카테고리 수가 20이 아님(전제 재확인 필요)")
        self.assertEqual(found, expected, "firm.js CATEGORY_LABELS != grm_findings.FINDING_TAXONOMY")

    def test_rpc_endpoint_and_safe_contract_present(self):
        js_src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        self.assertIn('rpcEndpoint("findings_firm_profile")', js_src)
        self.assertIn('method: "POST"', js_src)
        self.assertIn('apikey: key, Authorization: "Bearer " + key', js_src)
        self.assertIn('JSON.stringify({ p_firm_key: firmKey })', js_src)
        # 원문(finding_text/finding_text_ko)은 RPC 가 아니라 별개 anon REST 로만 가져온다.
        self.assertIn('"/rest/v1/findings?select="', js_src)
        self.assertIn("raw_signal_id=eq.", js_src)

    def test_defensive_states_present(self):
        """013(firm_key generated 컬럼 + findings_firm_profile RPC) 미적용 라이브(RPC
        404·network 실패)와 key 파라미터 없음/빈 프로파일(display_name "")을 서로 다른
        상태로 구분해 처리하는지 소스 마커로 확인한다."""
        js_src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        self.assertIn('loadingEl.textContent = "업체 프로파일 준비 중입니다."', js_src)
        self.assertIn('showState("notfound")', js_src)
        self.assertIn('showState("error")', js_src)
        self.assertIn("!(data.display_name || \"\")", js_src)
        self.assertIn("function getFirmKeyParam()", js_src)
        self.assertIn('업체 프로파일 준비 중입니다', self.html)
        self.assertIn('해당 업체를 찾을 수 없습니다', self.html)

    def test_no_innerhtml_data_injection(self):
        js_src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        import re as _re
        for m in _re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', js_src):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입 의심: {m.group(0)}")

    def test_no_new_external_resources(self):
        js_src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        self.assertNotIn("cdn.", js_src)
        self.assertNotIn("<canvas", self.html)

    def test_firm_name_html_entity_decode_at_profile_header(self):
        """[firm_name 엔티티 디코드 M5] 업체 프로파일 헤더(fp-firm-name)는 data.display_name
        (=firm_name)에 &amp;/&#039; 가 이미 이스케이프된 채로 저장돼 있어도 decodeFirmDisplay()
        로 되돌려 표시한다(textContent 대입 전용, innerHTML 아님 — XSS 무관)."""
        js_src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function decodeFirmDisplay(s)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('.replace(/&amp;/g, "&")', fn)
        self.assertIn('.replace(/&#039;/g, "\'")', fn)
        self.assertIn('nameEl.textContent = decodeFirmDisplay(data.display_name || "");', js_src)


class WebFirmWatchlistTest(unittest.TestCase):
    """관심 업체 워치리스트(015_firm_watchlist.sql 의 웹 절반) — 셸 배선·JS 소스 계약.

    실제 등록/해제는 브라우저 런타임(supabase-js·RLS) 소관이라, 여기선 기존
    WebFirmRenderTest/WebAdminRenderTest 의 JS 소스 문자열 단언 관례로 다음을 고정한다:
      (1) firm.html 셸은 빈 hidden 컨테이너만(런타임 주입 전 골든 결정론),
      (2) firm.js 가 reactions.js 의 세션 취득/로그인 판단/Authorization(supabase-js
          from() 토큰 자동첨부) 패턴을 문자열 수준에서 그대로 재사용(새 인증 코드 0),
      (3) 015 미적용/비로그인/env 미설정 방어 폴백 마커,
      (4) me 페이지 관심 업체 섹션 배선(reactions.js renderMyFirms)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_watch_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "firm" / "index.html").read_text(encoding="utf-8")
        cls.firm_js = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        cls.reactions_js = (WEB_DIR / "assets" / "reactions.js").read_text(encoding="utf-8")
        cls.me_tmpl = (WEB_DIR / "templates" / "me.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_shell_has_hidden_watch_container_only(self):
        # 셸엔 빈 hidden 컨테이너만 — 버튼/안내 문구는 firm.js 런타임 주입(env 와 무관하게
        # 템플릿 출력 byte 동일 = 골든 결정론).
        self.assertIn('<div class="fp-watch" id="fp-watch" hidden></div>', self.html)
        self.assertNotIn("관심 업체 등록", self.html)
        self.assertNotIn("관심 등록됨", self.html)

    def test_firm_js_reuses_reactions_session_pattern_verbatim(self):
        # reactions.js 의 클라이언트 생성 auth 설정 4종을 문자열 수준으로 동일 재사용
        # (같은 storageKey → localStorage 세션 공유). 두 파일 모두에 존재해야 파리티.
        for marker in (
            'storageKey: "grm-public-auth-v1"',
            "persistSession: true",
            "autoRefreshToken: true",
            "detectSessionInUrl: false",
            ".auth.getSession()",
            ".auth.onAuthStateChange(",
        ):
            self.assertIn(marker, self.firm_js, f"firm.js 에 재사용 패턴 누락: {marker}")
            self.assertIn(marker, self.reactions_js, f"reactions.js 원본 패턴 소실: {marker}")
        # 로그인 상태 판단도 reactions.js 동형(session && session.user).
        self.assertIn("!wSession || !wSession.user", self.firm_js)

    def test_firm_js_db_calls_via_supabase_client_own_rows(self):
        # DB 호출은 supabase-js from() — Authorization: Bearer <사용자 토큰> 자동 첨부
        # (reactions.js 의 sb.from("reaction") 동형). anon-key 수동 헤더로 워치리스트를
        # 만지지 않는다(RPC/findings REST 만 anon 유지).
        self.assertIn('from("firm_watchlist").select("firm_key")', self.firm_js)
        self.assertIn('from("firm_watchlist").insert({', self.firm_js)
        self.assertIn('from("firm_watchlist").delete()', self.firm_js)
        self.assertIn("firm_display: displayName || \"\"", self.firm_js)  # 표시명 스냅샷
        self.assertIn("user_id: wSession.user.id, firm_key: firmKey", self.firm_js)
        self.assertNotIn('"/rest/v1/firm_watchlist', self.firm_js)

    def test_firm_js_toggle_labels_and_login_entry_reuse(self):
        self.assertIn('"관심 업체 등록"', self.firm_js)
        self.assertIn('"관심 등록됨 · 해제"', self.firm_js)
        self.assertIn("로그인하면 관심 업체로 등록할 수 있습니다", self.firm_js)
        # 로그인 진입 = reactions.js 가 헤더에 주입하는 버튼(.grm-acct-login) 클릭 위임.
        self.assertIn('.grm-auth .grm-acct-login', self.firm_js)
        self.assertIn('grm-acct-login', self.reactions_js)

    def test_firm_js_silent_disable_and_cap_hint(self):
        # 015 미적용(테이블 부재)·network 실패 → hidden 유지(조용한 비활성).
        self.assertIn("function hideWatch()", self.firm_js)
        self.assertIn("watchEl.hidden = true", self.firm_js)
        # 프로파일 로드 성공 후에만 배선(본기능 무장애).
        self.assertIn('initWatchlist(firmKeyParam, data.display_name || "")', self.firm_js)
        # insert 거부 힌트에 상한(50) 명시 — 015 트리거 메시지와 정합.
        self.assertIn("최대 50개", self.firm_js)

    def test_firm_js_watch_labels_no_emoji(self):
        import re as _re
        # 주: ★·— 같은 기존 주석 기호(U+2600 대역 일부)는 허용 — 이모지 평면(U+1F000~)만 금지.
        self.assertIsNone(_re.search(r"[\U0001F000-\U0001FAFF❤⭐]", self.firm_js),
                          "firm.js 에 이모지 사용 금지(기존 버튼 톤 계승)")

    def test_me_template_firm_section_wired(self):
        self.assertIn('id="grm-my-firms"', self.me_tmpl)
        self.assertIn("관심 업체", self.me_tmpl)
        self.assertIn("불러오는 중…", self.me_tmpl)

    def test_me_page_built_with_firm_section_when_env_on(self):
        # me/index.html 은 reactions env-gate 뒤에서만 생성(기존 관례) — on 빌드에서 섹션 확인.
        u0, k0 = render.SUPABASE_URL, render.SUPABASE_ANON_KEY
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_watchme_"))
        try:
            render.SUPABASE_URL = "https://rfwixqqdljpmtjdlblct.supabase.co"
            render.SUPABASE_ANON_KEY = "anon-key"
            out = tmp / "out"
            render.render_site(SINGLE_FIXTURES, out)
            me = (out / "me" / "index.html").read_text(encoding="utf-8")
        finally:
            render.SUPABASE_URL, render.SUPABASE_ANON_KEY = u0, k0
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn('id="grm-my-firms"', me)
        self.assertIn("관심 업체", me)

    def test_reactions_js_my_firms_renderer(self):
        # me 페이지 관심 업체 목록 — 스크랩 목록(renderMyScraps) 관례 동형.
        self.assertIn("function renderMyFirms()", self.reactions_js)
        self.assertIn('getElementById("grm-my-firms")', self.reactions_js)
        self.assertIn('from("firm_watchlist").select("firm_key,firm_display,created_at")', self.reactions_js)
        # 015 미적용/비로그인 방어 — 오류처럼 보이지 않는 노트 폴백.
        self.assertIn("관심 업체 목록 준비 중입니다.", self.reactions_js)
        self.assertIn("로그인하면 관심 업체를 모아볼 수 있어요.", self.reactions_js)
        # 빈 목록 안내 + 프로파일 링크 + 해제 버튼.
        self.assertIn("아직 등록한 업체가 없습니다", self.reactions_js)
        self.assertIn("업체 프로파일에서 관심 업체로 등록하세요.", self.reactions_js)
        self.assertIn('findings/firm/index.html?key=" + encodeURIComponent(fw.firm_key)', self.reactions_js)
        self.assertIn('from("firm_watchlist").delete().match({ user_id: session.user.id, firm_key: fw.firm_key })', self.reactions_js)
        # 배선 — 세션 취득/변경 양쪽에서 렌더(스크랩과 동일 지점).
        self.assertIn("renderMyScraps(); renderMyFirms();", self.reactions_js)

    def test_reactions_js_firm_display_never_injected_as_html(self):
        # firm_display 는 자유 텍스트(스냅샷) — textContent 로만 렌더(XSS 계약). M5(엔티티
        # 디코드) 이후에도 순수 문자열 함수 호출·연결일 뿐 innerHTML 삽입은 아니어야 한다.
        self.assertIn("a.textContent = decodeFirmDisplay(fw.firm_display) || fw.firm_key", self.reactions_js)
        self.assertNotIn("fw.firm_display +", self.reactions_js)
        self.assertNotIn("+ fw.firm_display", self.reactions_js)

    def test_reactions_js_firm_display_html_entity_decode(self):
        """[firm_name 엔티티 디코드 M5] DB firm_display 에 &amp;/&#039; 가 이미 이스케이프된
        채로 저장된 행("H &amp; P Industries")도 워치리스트 목록에는 디코드된 형태로
        표시된다 — decodeFirmDisplay() 는 이 2종 엔티티만 순수 문자열 replace 로 되돌리며
        (innerHTML 아님, XSS 무관), 표시 직전(textContent 대입 전)에 적용된다."""
        self.assertIn("function decodeFirmDisplay(s)", self.reactions_js)
        fn = self.reactions_js[self.reactions_js.index("function decodeFirmDisplay(s)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('.replace(/&amp;/g, "&")', fn)
        self.assertIn('.replace(/&#039;/g, "\'")', fn)


# ── 하드닝 (스킴·링크상태·면책·중복일자·방어필터·다크밴드 — 적대적 리뷰 보강) ──
def _card(render_order: int = 0, **ov) -> dict:
    c = {
        "id": f"x{render_order}", "render_order": render_order, "group": "글로벌",
        "group_label": None, "agency": "FDA", "card_type": "지침·안내서",
        "category": "Guidance", "modality": None, "evidence_level": "A",
        "signal_tier": 1, "signal_label": "Low", "type_tag": "Guidance",
        "headline_target": f"Card {render_order}", "title_issue": "", "summary": "",
        "facts": [{"label": "발행일", "value": "2026-06-01"}, {"label": "문서번호", "value": f"x{render_order}"}],
        "quotes": [], "evidence_basis": "Intake raw", "key_facts": [], "implication": "",
        "checks": [], "merged_count": 1, "merged_items": [],
        "sources": {"info_url": "https://example.org/info", "official_url": "https://example.org/off",
                    "official_is_pdf": False, "link_check": {"info": "pending", "official": "pending"}},
    }
    c.update(ov)
    return c


def _minimal_brief(pub: str, *, card: dict | None = None, ai_disclosure: bool = True,
                   cards: list | None = None, coverage: dict | None = None) -> dict:
    if cards is None:
        c = _card(0, id="x1", headline_target="Test Card",
                  facts=[{"label": "발행일", "value": pub}, {"label": "문서번호", "value": "x1"}])
        if card:
            c.update(card)
        cards = [c]
    cov = coverage or {"intake_total": 1, "rendered": 1, "evidence": {"A": 1, "B": 0, "C": 0}}
    return {
        "schema_version": "grm-web-card/v1",
        "brief": {"run_date_kst": pub, "window": f"{pub} ~ {pub}", "publish_date": pub,
                  "agencies": ["FDA"], "categories": ["Guidance"], "tldr": [],
                  "coverage": cov, "ai_disclosure": ai_disclosure},
        "cards": cards,
    }


class WebRenderHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_h_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render_site(self, briefs: list[dict]) -> pathlib.Path:
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        for br in briefs:
            pub = br["brief"]["publish_date"]
            (data / f"brief_web_{pub}.json").write_text(
                json.dumps(br, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, out)
        return out

    def _render_detail(self, brief: dict) -> str:
        out = self._render_site([brief])
        pub = brief["brief"]["publish_date"]
        return (out / "briefs" / pub / "index.html").read_text(encoding="utf-8")

    def test_unsafe_url_scheme_dropped_safe_kept(self):
        b = _minimal_brief("2026-06-01", card={"sources": {
            "info_url": "javascript:alert('x')",
            "official_url": "https://example.org/ok",
            "official_is_pdf": False,
            "link_check": {"info": "pending", "official": "pending"}}})
        h = self._render_detail(b)
        self.assertNotIn("javascript:alert", h)       # 위험 스킴 차단
        self.assertNotIn('href="javascript', h)
        self.assertIn('href="https://example.org/ok"', h)  # 정상 URL 무변형 유지

    def test_data_uri_scheme_dropped(self):
        b = _minimal_brief("2026-06-01", card={"sources": {
            "info_url": "https://example.org/ok",
            "official_url": "data:text/html,<script>alert(1)</script>",
            "official_is_pdf": False,
            "link_check": {"info": "pending", "official": "pending"}}})
        h = self._render_detail(b)
        self.assertNotIn("data:text/html", h)
        self.assertNotIn("<script>alert", h)

    def test_link_state_broken_and_degraded(self):
        b = _minimal_brief("2026-06-01", card={"sources": {
            "info_url": "https://example.org/info",
            "official_url": "https://example.org/off",
            "official_is_pdf": False,
            "link_check": {"info": "broken", "official": "degraded"}}})
        h = self._render_detail(b)
        # broken → 클릭 비활성(href 없음) + 일시 접근불가 + ti-link-off
        self.assertIn('class="src-broken"', h)
        self.assertIn("일시 접근불가", h)
        self.assertIn("ti-link-off", h)
        self.assertNotIn('href="https://example.org/info"', h)  # broken 은 href 미발행
        # degraded → 살아있는 <a href> + ⚠️ 아이콘
        self.assertIn("ti-alert-triangle", h)                   # 글로벌 섹션이라 Recall 아이콘과 무충돌
        self.assertIn('href="https://example.org/off"', h)

    def test_disclaimer_omitted_when_false(self):
        b = _minimal_brief("2026-06-01", ai_disclosure=False)
        h = self._render_detail(b)
        self.assertNotIn("AI 자동 생성 안내", h)
        # 대조: true 면 출력.
        h2 = self._render_detail(_minimal_brief("2026-06-02", ai_disclosure=True))
        self.assertIn("AI 자동 생성 안내", h2)

    def test_merged_into_member_excluded(self):
        # 적대 입력: 병합 멤버(merged_into)를 cards[]에 직접 주입 → 렌더 부재.
        cards = [
            _card(0, id="keep", headline_target="KEEP ME PARENT"),
            _card(1, id="member", headline_target="DROP MERGED MEMBER", merged_into="keep"),
        ]
        h = self._render_detail(_minimal_brief("2026-06-01", cards=cards))
        self.assertIn("KEEP ME PARENT", h)
        self.assertNotIn("DROP MERGED MEMBER", h)
        # 제외 카드는 섹션 카운트·목차에도 미산입(anchor=document_id).
        self.assertIn('글로벌 <span class="n">1장</span>', h)
        self.assertIn('id="keep"', h)              # 대표 카드 anchor = id
        self.assertNotIn('id="member"', h)         # 제외 멤버 anchor 부재
        self.assertNotIn('href="#member"', h)      # 목차에도 미산입

    def test_watch_card_excluded(self):
        # 적대 입력: group=="watch" 카드 직접 주입 → 렌더 부재(비카드 영역).
        cards = [
            _card(0, id="keep", headline_target="KEEP ME"),
            _card(1, id="w", headline_target="DROP WATCH ITEM", group="watch"),
        ]
        h = self._render_detail(_minimal_brief("2026-06-01", cards=cards))
        self.assertIn("KEEP ME", h)
        self.assertNotIn("DROP WATCH ITEM", h)

    def test_merged_parent_still_rendered(self):
        # merged_count>1 이지만 merged_into 없음(대표 병합 카드) → 정상 렌더.
        cards = [_card(0, id="parent", headline_target="MERGED PARENT",
                       merged_count=3, merged_items=["품목A", "품목B", "품목C"])]
        h = self._render_detail(_minimal_brief("2026-06-01", cards=cards))
        self.assertIn("MERGED PARENT", h)
        self.assertIn("전체 3품목", h)

    def test_callout_binds_latest_brief(self):
        # 코럴 콜아웃·히어로 이슈카드 호별 수치가 최신호(06-29) 파생값 반영(stale 아님).
        older = _minimal_brief("2026-06-22")
        latest = _minimal_brief("2026-06-29",
                                coverage={"intake_total": 99, "rendered": 88,
                                          "evidence": {"A": 7, "B": 5, "C": 0}})
        out = self._render_site([older, latest])
        landing = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("수집 99건", landing)
        self.assertIn("카드 88장", landing)
        self.assertIn("Evidence A 7 · B 5", landing)   # §1-6 표기 일관성: '/' → '·'
        self.assertIn("2026년 6월 5주차", landing)   # 06-29 → (29-1)//7+1 = 5
        self.assertNotIn("수집 36건", landing)         # 옛 정적 수치 잔존 금지

    def test_duplicate_publish_date_rejected(self):
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        for name in ("aaa", "zzz"):  # 같은 publish_date, 다른 파일 → slug 충돌
            (data / f"{name}.json").write_text(
                json.dumps(_minimal_brief("2026-06-01"), ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(SystemExit):
            render.render_site(data, out)

    def test_verification_meta_conditional(self):
        # 소유권 인증 메타는 토큰 있을 때만 출력(빈 값이면 미출력). 모듈 전역을 호출
        # 시점에 읽으므로 monkeypatch 가 반영 — 원래 기본값으로 복구해 타 테스트·골든 오염 0.
        g0, n0 = render.GOOGLE_SITE_VERIFICATION, render.NAVER_SITE_VERIFICATION
        try:
            render.GOOGLE_SITE_VERIFICATION = ""
            render.NAVER_SITE_VERIFICATION = ""
            h_off = self._render_detail(_minimal_brief("2026-06-01"))
            self.assertNotIn("google-site-verification", h_off)
            self.assertNotIn("naver-site-verification", h_off)
            render.GOOGLE_SITE_VERIFICATION = "g-tok-123"
            render.NAVER_SITE_VERIFICATION = "n-tok-456"
            h_on = self._render_detail(_minimal_brief("2026-06-02"))
        finally:
            render.GOOGLE_SITE_VERIFICATION, render.NAVER_SITE_VERIFICATION = g0, n0
        self.assertIn('<meta name="google-site-verification" content="g-tok-123" />', h_on)
        self.assertIn('<meta name="naver-site-verification" content="n-tok-456" />', h_on)

    def test_newsletter_form_conditional(self):
        # 구독 폼(T1)은 GRM_NEWSLETTER_FORM_ACTION(env-param) 설정 시에만 출력. 모듈 전역을
        # 호출 시점에 읽어 monkeypatch 반영 — 복구로 타 테스트·골든 오염 0(인증 메타와 동형).
        a0 = render.NEWSLETTER_FORM_ACTION
        try:
            # off — 빈 값이면 if 블록 전체 미출력(전 페이지 골든 byte-diff 0 의 근거).
            render.NEWSLETTER_FORM_ACTION = ""
            h_off = self._render_detail(_minimal_brief("2026-06-01"))
            self.assertNotIn('class="subscribe"', h_off)
            self.assertNotIn('<form class="sub-form"', h_off)
            # on — 호스팅 SaaS endpoint 로 직접 POST. 전 페이지(랜딩·상세) 공통(base.html 밴드).
            render.NEWSLETTER_FORM_ACTION = "https://newsletter.example.com/subscribe"
            out = self._render_site([_minimal_brief("2026-06-02")])
            h_on = (out / "briefs/2026-06-02/index.html").read_text(encoding="utf-8")
            landing_on = (out / "index.html").read_text(encoding="utf-8")
        finally:
            render.NEWSLETTER_FORM_ACTION = a0
        self.assertIn('class="subscribe"', h_on)
        self.assertIn('class="subscribe"', landing_on)          # 전 페이지(랜딩에도)
        self.assertIn('action="https://newsletter.example.com/subscribe" method="post"', h_on)
        # Brevo 실제 폼 필드 정합: 이메일=EMAIL(대문자) + 봇방지 허니팟 + locale 히든.
        self.assertIn('type="email" name="EMAIL"', h_on)
        self.assertIn("required", h_on)
        self.assertIn('name="email_address_check"', h_on)        # 허니팟(빈값)
        self.assertIn('class="sub-hp"', h_on)                    # 허니팟 시각 숨김(사람 미입력)
        self.assertIn('name="locale"', h_on)
        # 회원 시스템 아님 — 사람 입력은 이메일 1칸. 비밀번호·이름 등 추가 PII 입력 0.
        self.assertNotIn('type="password"', h_on)
        self.assertNotIn('name="password"', h_on)
        self.assertNotIn('name="name"', h_on)
        # 한글 안전(§4) — 폼 밴드에 인라인 자간/대문자·한글 mono 0. WebKoreanSafetyTest 는
        # 폼-off 빌드만 스캔하므로 on 경로(밴드 한정 범위)를 여기서 보강한다.
        import re as _re
        band = h_on[h_on.index('class="subscribe"'):h_on.index("<footer")]
        self.assertNotIn("letter-spacing", band)
        self.assertNotIn("text-transform", band)
        self.assertIsNone(_re.search(r'class="[^"]*\bmono\b[^"]*"', band),
                          "구독 밴드에 mono 클래스(한글 위험)")
        # 안전 URL 가드(_safe_url) — 비http(s) 스킴 action 은 ""→폼 미출력(fail-safe).
        try:
            render.NEWSLETTER_FORM_ACTION = "javascript:alert(1)"
            h_bad = self._render_detail(_minimal_brief("2026-06-03"))
        finally:
            render.NEWSLETTER_FORM_ACTION = a0
        self.assertNotIn("javascript:alert", h_bad)
        self.assertNotIn('class="subscribe"', h_bad)


class WebAdminRenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_admin_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render_site(self, pub: str = "2026-06-01") -> pathlib.Path:
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        (data / f"brief_web_{pub}.json").write_text(
            json.dumps(_minimal_brief(pub), ensure_ascii=False), encoding="utf-8")
        render.render_site(data, out)
        return out

    def test_admin_console_env_gated(self):
        u0, k0 = render.SUPABASE_URL, render.SUPABASE_ANON_KEY
        try:
            render.SUPABASE_URL = ""
            render.SUPABASE_ANON_KEY = ""
            out_off = self._render_site("2026-06-01")
            self.assertFalse((out_off / "admin" / "index.html").exists())
            self.assertNotIn("Disallow: /admin/", (out_off / "robots.txt").read_text(encoding="utf-8"))

            render.SUPABASE_URL = "https://rfwixqqdljpmtjdlblct.supabase.co"
            render.SUPABASE_ANON_KEY = "anon-key"
            out_on = self._render_site("2026-06-02")
            admin = (out_on / "admin" / "index.html")
            self.assertTrue(admin.exists(), "Supabase env 설정 시 /admin/index.html 이 생성돼야 함")
            h = admin.read_text(encoding="utf-8")
            robots = (out_on / "robots.txt").read_text(encoding="utf-8")
        finally:
            render.SUPABASE_URL, render.SUPABASE_ANON_KEY = u0, k0

        self.assertIn('id="grm-admin-cfg"', h)
        self.assertIn('data-admin-email="yeomminho1472@gmail.com"', h)
        self.assertIn('data-supabase-url="https://rfwixqqdljpmtjdlblct.supabase.co"', h)
        self.assertIn('id="grm-admin-readiness"', h)
        self.assertIn('id="grm-admin-activation"', h)
        self.assertIn('id="grm-admin-confirm-form"', h)
        self.assertIn('id="grm-admin-reset-form"', h)
        self.assertIn("Admin Backend", h)
        self.assertIn("Admin 계정은 운영자 권한으로 분리", h)
        self.assertIn("Edge Function Secrets", h)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", h)
        self.assertIn("GITHUB_ACTIONS_TOKEN", h)
        self.assertIn("Newsletter List", h)
        self.assertIn("GRM_NEWSLETTER_LIST_ID", h)
        self.assertIn("운영센터", h)
        self.assertIn("운영 상태 센터", h)
        self.assertIn("현재 운영 상태 확인 중", h)
        self.assertIn("주간 발행 흐름", h)
        self.assertIn('class="admin-workflow-log"', h)
        self.assertIn("운영 이슈", h)
        self.assertIn("한눈에 확인할 수 있습니다", h)
        self.assertIn('id="grm-ops-summary"', h)
        self.assertIn('id="grm-ops-brief"', h)
        self.assertIn('id="grm-workflow-pipeline"', h)
        self.assertIn('id="grm-workflow-cards"', h)
        self.assertIn('id="grm-ops-incidents"', h)
        self.assertIn("문제가 있을 때만: 수동 복구 도구", h)
        self.assertIn("이번 주 발행 승인", h)
        self.assertIn('id="grm-web-approve-submit"', h)
        self.assertIn("매주 월요일", h)
        self.assertIn("서비스 관리", h)
        admin_js = (out_on / "assets" / "admin.js").read_text(encoding="utf-8")
        self.assertIn("requireBackendReady", admin_js)
        self.assertIn("Edge Function secrets", admin_js)
        self.assertIn("admin-github?action=ops", admin_js)
        self.assertIn("rerun_failed", admin_js)
        self.assertIn("configuration_warnings", admin_js)
        self.assertIn("Secrets 확인", admin_js)
        self.assertIn("구독자 전체에게 최신 뉴스레터", admin_js)
        self.assertIn("Brevo 리스트에서 제거", admin_js)
        self.assertIn("복구 전까지 로그인할 수 없습니다", admin_js)
        self.assertIn("실패 작업", admin_js)
        self.assertIn("다음 조치", admin_js)
        self.assertIn("GitHub 로그", admin_js)
        self.assertIn("운영 영향", admin_js)
        self.assertIn("현재 판단", admin_js)
        self.assertIn("소스 수집", admin_js)
        self.assertIn("상세 런북", admin_js)
        self.assertIn("admin-incident-row", admin_js)
        self.assertIn("최신 Run", admin_js)
        self.assertIn("grm-admin-auth-v1", admin_js)
        self.assertIn("verifyOtp", admin_js)
        self.assertIn("cannot_manage_admin_user", admin_js)
        self.assertIn("adminUsers", admin_js)
        self.assertIn("월요일 오전 9시 30분에 자동 생성", admin_js)
        self.assertIn("카드 선별", admin_js)
        self.assertIn("뒤에서 돌아가는 자동 검사", admin_js)
        self.assertIn("admin-flow-node", admin_js)
        self.assertIn("isExpectedNoDeltaGateRejection", admin_js)
        self.assertIn("Resolve publish_date", admin_js)
        self.assertIn("이번 주 카드가 아직 준비되지 않아", admin_js)
        self.assertIn(".admin-flow-node.pending", h)
        self.assertIn(".admin-dot.pending", h)
        reactions_js = (out_on / "assets" / "reactions.js").read_text(encoding="utf-8")
        self.assertIn("grm-public-auth-v1", reactions_js)
        self.assertIn('scope: "local"', reactions_js)
        self.assertIn("운영자 계정은 Admin 페이지에서 로그인하세요.", reactions_js)
        self.assertIn("<th>요청</th><th>실행(요청 시점)</th>", h)
        self.assertIn('/assets/admin.js?v=', h)
        self.assertIn('Disallow: /admin/', robots)


# ── 한글 안전 가드 (§4 — 강제: 한글에 mono/자간/대문자/이탤릭 금지) ─────────────
class WebKoreanSafetyTest(unittest.TestCase):
    """v6 재스킨 §4·§5 자동 점검 — 렌더 HTML 기준.

    1) class="mono" 요소 내부에 한글(Hangul) 0 — mono 는 ASCII 데이터 전용.
    2) 렌더 HTML 에 inline letter-spacing·text-transform 스타일 0 — 자간/대문자는
       CSS(영문 .kick·.mono 한정)에서만, 마크업으로 한글에 새지 않음.
    """
    import re as _re
    _HANGUL = _re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏ꥠ-꥿ힰ-퟿]")
    # mono-styled class tokens: 'mono'(데이터 셀·범용) + 'code'(.b.code 배지). 한글 0 보장.
    _MONO = _re.compile(r'<[^>]*class="[^"]*\b(?:mono|code)\b[^"]*"[^>]*>(.*?)</', _re.S)

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_k_"))
        single, multi = cls._tmp / "single", cls._tmp / "multi"
        _build_single(single)
        _build_multi(multi, cls._tmp)
        cls.htmls = {p.relative_to(cls._tmp).as_posix(): p.read_text(encoding="utf-8")
                     for root in (single, multi) for p in root.rglob("*.html")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_no_hangul_in_mono(self):
        bad = []
        for name, html in self.htmls.items():
            for m in self._MONO.finditer(html):
                if self._HANGUL.search(m.group(1)):
                    bad.append((name, m.group(1)[:60]))
        self.assertEqual(bad, [], f"class=mono 내부 한글(§4 위반): {bad[:8]}")

    def test_no_inline_letterspacing_or_transform(self):
        bad = [name for name, html in self.htmls.items()
               if ("letter-spacing" in html or "text-transform" in html)]
        self.assertEqual(bad, [], f"인라인 자간/대문자 스타일(§4 위반): {bad}")


# ── SEO 메타·구조화데이터 (§2/§3 — description·canonical·OG·JSON-LD, 결정론 head) ──
class WebSeoMetaTest(unittest.TestCase):
    """검색결과 품질·중복 색인 방지·구조화데이터 의미 단언(byte 안정은 골든이 잠금).
    소유권 인증 메타 조건부는 WebRenderHardeningTest.test_verification_meta_conditional.
    한글 메타값의 mono/자간 부재는 WebKoreanSafetyTest 가 전 HTML 스캔으로 함께 보장."""

    BASE = "https://grm-solutions.com"

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_seo_"))
        single = cls._tmp / "single"
        _build_single(single)
        cls.landing = (single / "index.html").read_text(encoding="utf-8")
        cls.archive = (single / "archive/index.html").read_text(encoding="utf-8")
        cls.detail = (single / "briefs/2026-06-26/index.html").read_text(encoding="utf-8")
        cls.detail22 = (single / "briefs/2026-06-22/index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_description_present_each_page(self):
        for h in (self.landing, self.archive, self.detail):
            self.assertIn('<meta name="description" content="', h)

    def test_canonical_trailing_slash_dir_form(self):
        self.assertIn(f'<link rel="canonical" href="{self.BASE}/" />', self.landing)
        self.assertIn(f'<link rel="canonical" href="{self.BASE}/archive/" />', self.archive)
        self.assertIn(f'<link rel="canonical" href="{self.BASE}/briefs/2026-06-26/" />', self.detail)

    def test_open_graph_and_twitter(self):
        og_image = f'<meta property="og:image" content="{self.BASE}/assets/og-image.png" />'
        for h in (self.landing, self.archive, self.detail):
            self.assertIn('<meta property="og:type" content="website" />', h)
            self.assertIn('<meta property="og:site_name" content="Global Regulatory Monitor" />', h)
            self.assertIn('<meta property="og:locale" content="ko_KR" />', h)
            self.assertIn('<meta property="og:title" content="', h)
            self.assertIn(og_image, h)
            self.assertIn('<meta property="og:image:width" content="1200" />', h)
            self.assertIn('<meta property="og:image:height" content="630" />', h)
            self.assertIn('<meta name="twitter:card" content="summary_large_image" />', h)
            self.assertIn(f'<meta name="twitter:image" content="{self.BASE}/assets/og-image.png" />', h)
        # og:url == canonical(트레일링슬래시형 통일).
        self.assertIn(f'<meta property="og:url" content="{self.BASE}/archive/" />', self.archive)

    def test_header_brand_lockup_owl_grm(self):
        # 헤더 로고 락업(B안) — favicon.svg(올빼미) 재사용 + GRM/서브타이틀, 전 페이지 공통.
        for h in (self.landing, self.archive, self.detail):
            self.assertIn('<img src="/favicon.svg" width="34" height="34" alt="" aria-hidden="true"', h)
            self.assertIn('>GRM</span>', h)
            self.assertIn('class="brand-full"', h)
            self.assertIn('Global Regulatory Monitor</span>', h)
            self.assertIn('aria-label="Global Regulatory Monitor 홈"', h)

    def test_favicon_links_root_absolute(self):
        for h in (self.landing, self.archive, self.detail):
            self.assertIn('<link rel="icon" href="/favicon.ico" sizes="any">', h)
            self.assertIn('<link rel="icon" type="image/svg+xml" href="/favicon.svg">', h)
            self.assertIn('<link rel="apple-touch-icon" href="/assets/favicon-180.png">', h)
            self.assertIn('<link rel="manifest" href="/site.webmanifest">', h)

    def test_json_ld_landing_only_and_valid(self):
        import re as _re
        m = _re.search(r'<script type="application/ld\+json">(.*?)</script>',
                       self.landing, _re.S)
        self.assertIsNotNone(m, "랜딩 JSON-LD 부재")
        data = json.loads(m.group(1))                        # 유효 JSON
        self.assertEqual([n["@type"] for n in data], ["Organization", "WebSite"])
        for n in data:
            self.assertEqual(n["url"], self.BASE)
        self.assertEqual(data[0]["logo"], f"{self.BASE}/assets/favicon-512.png")
        # 상세·아카이브엔 JSON-LD 미출력(랜딩 한정).
        self.assertNotIn("application/ld+json", self.archive)
        self.assertNotIn("application/ld+json", self.detail)

    def test_brief_description_tldr_or_dateform(self):
        # 06-26(tldr 채움) → tldr[0]; 06-22(빈 tldr) → 날짜 파생 한 줄.
        self.assertIn('content="국내 N-nitroso', self.detail)          # tldr[0]
        self.assertIn('content="2026년 6월 4주차 ', self.detail22)      # 날짜 파생 폴백

    def test_google_verification_live_by_default(self):
        # main(ecb5043) 하드코딩 GSC 토큰을 env 기본값으로 흡수 → 기본 빌드에 라이브 노출
        # (단일 <meta>·중복 0). 회전/비활성은 GRM_GOOGLE_SITE_VERIFICATION 으로.
        tag = ('<meta name="google-site-verification" '
               'content="pm3IGW80AsWscJVlQzMZel18pFcjFTxCxXrTDXqcjx4" />')
        self.assertEqual(self.landing.count(tag), 1)        # 정확히 1개(중복 없음)
        self.assertIn(tag, self.detail)                      # 전 페이지 공통(<head>)

    def test_naver_verification_live_by_default(self):
        # main 하드코딩 네이버 토큰을 env 기본값으로 흡수(들여쓰기/중복/누락 회귀 해소)
        # → 기본 빌드에 단일 라이브 노출. 회전은 GRM_NAVER_SITE_VERIFICATION repo var 로.
        tag = ('<meta name="naver-site-verification" '
               'content="51283dc3591917baf9e057d220f053a91131bbe2" />')
        self.assertEqual(self.landing.count(tag), 1)        # 정확히 1개(중복 없음)
        self.assertIn(tag, self.detail)                      # 전 페이지 공통(<head>)

    def test_env_or_default_empty_falls_back(self):
        # deploy 가 미설정 repo var 를 빈 문자열로 전달해도(Actions 동작) 토큰이 사라지지
        # 않도록 빈/미설정 → 기본값, 설정 → 그 값. (인증 토큰 deploy 배선의 무회귀 보증.)
        import os as _os
        KEY = "GRM_TEST_VERIFICATION_PROBE_X"
        prev = _os.environ.pop(KEY, None)
        try:
            self.assertEqual(render._env_or_default(KEY, "DEF"), "DEF")   # 미설정 → 기본
            _os.environ[KEY] = ""
            self.assertEqual(render._env_or_default(KEY, "DEF"), "DEF")   # 빈 문자열 → 기본
            _os.environ[KEY] = "   "
            self.assertEqual(render._env_or_default(KEY, "DEF"), "DEF")   # 공백뿐 → 기본
            _os.environ[KEY] = " tok-9 "
            self.assertEqual(render._env_or_default(KEY, "DEF"), "tok-9")  # 설정 → strip 값
        finally:
            if prev is None:
                _os.environ.pop(KEY, None)
            else:
                _os.environ[KEY] = prev


# ── 골든 동결 (개발용) ───────────────────────────────────────────────────────
def freeze() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_freeze_"))
    try:
        single, multi = tmp / "single", tmp / "multi"
        _build_single(single)
        _build_multi(multi, tmp)
        for rel, name in SINGLE_GOLDENS:
            shutil.copyfile(single / rel, GOLDEN_DIR / name)
            print(f"  froze {name}")
        for rel, name in MULTI_GOLDENS:
            shutil.copyfile(multi / rel, GOLDEN_DIR / name)
            print(f"  froze {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"골든 동결 완료 → {GOLDEN_DIR}")


# ── [상세보기 결정론 승격 2026-07-02 · spec §16] 결정론 상세 블록 렌더 스모크 ──────────
class WebDeterministicDetailTest(unittest.TestCase):
    """gmp deterministic_detail 카드를 합성 브리프(06-08 봉투 재사용)에 주입해 실제
    render_site() 로 HTML 렌더까지 확인. WL deep 과 동형 단계적 노출 블록. 값 부재 카드에는
    블록이 붙지 않는다(additive). 한글안전(§4) — 셀에 mono/자간 0."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_dd_"))
        data = cls._tmp / "data"
        data.mkdir(parents=True, exist_ok=True)
        base = json.loads(
            (MULTI_FIXTURES / "brief_web_2026_06_08.json").read_text(encoding="utf-8"))
        card = dict(base["cards"][0])
        card.update({
            "id": "gmpinspect-detailsmoke", "render_order": 999, "group": "국내",
            "group_label": None, "agency": "MFDS", "card_type": "GMP실사",
            "category": "Other", "modality": None, "evidence_level": "A",
            "signal_tier": 3, "signal_label": "High", "type_tag": "GMP실사",
            "headline_target": "퍼슨", "title_issue": "", "summary": "",
            "facts": [{"label": "제조소", "value": "㈜퍼슨 천안공장"}],
            "quotes": [], "key_facts": [], "implication": "", "checks": [],
            "merged_count": 1, "merged_items": [],
            "sources": {"info_url": "", "official_url": "https://nedrug.mfds.go.kr/x",
                        "official_is_pdf": True,
                        "link_check": {"info": "pending", "official": "pending"}},
            "deterministic_detail": {
                "type": "gmp_deficiencies", "count": 2,
                "severity_summary": {"중요": 1, "기타": 1},
                "rows": [
                    {"area": "시설장비", "severity": "기타",
                     "legal_basis": "[별표1] 2.1호",
                     "summary": "제품 교차오염 방지 제조시설 운영할 것",
                     "followup": "이행계획 타당성 인정"},
                    {"area": "제조", "severity": "중요",
                     "legal_basis": "[별표1] 6.1호 나목",
                     "summary": "밸리데이션 규정 반영·실시할 것",
                     "followup": "행정처분 예정"}]},
        })
        base["cards"] = base["cards"] + [card]
        (data / "brief_web_2026_06_08.json").write_text(
            json.dumps(base, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, cls._tmp / "out")
        cls.html = (cls._tmp / "out" / "briefs/2026-06-08/index.html").read_text(
            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_detail_block_rendered(self):
        self.assertIn('<details class="block detail">', self.html)
        self.assertIn("지적사항 상세", self.html)
        self.assertIn("· 2건", self.html)

    def test_rows_and_badges_present(self):
        self.assertIn("[별표1] 6.1호 나목", self.html)              # 근거법령(일반 서체)
        self.assertIn("밸리데이션 규정 반영·실시할 것", self.html)     # 지적내용
        self.assertIn('class="dt-badge">중요</span>', self.html)     # 중대도 배지
        self.assertIn('class="dt-chip">기타 1</span>', self.html)    # 집계 칩

    def test_only_one_detail_block(self):
        # 원본 06-08 카드(deterministic_detail 부재)에는 블록이 붙지 않는다(정확히 1개).
        self.assertEqual(self.html.count('<details class="block detail">'), 1)

    def test_korean_safe_no_mono_no_letterspacing(self):
        import re as _re
        block = self.html[self.html.index('<details class="block detail">'):]
        block = block[:block.index("</details>")]
        self.assertNotIn("letter-spacing", block)
        self.assertIsNone(_re.search(r'class="[^"]*\b(?:mono|code)\b[^"]*"', block),
                          "결정론 상세 블록에 mono/code 클래스(한글 위험, §4)")


class WebFda483DeterministicDetailTest(unittest.TestCase):
    """FDA 483 Observation deterministic_detail 렌더 스모크 — 번호 목록 + 원문 기반 라벨."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_483dd_"))
        data = cls._tmp / "data"
        data.mkdir(parents=True, exist_ok=True)
        base = json.loads(
            (MULTI_FIXTURES / "brief_web_2026_06_08.json").read_text(encoding="utf-8"))
        card = dict(base["cards"][0])
        card.update({
            "id": "fda483-detail-smoke", "render_order": 999, "group": "글로벌",
            "group_label": None, "agency": "FDA", "card_type": "FDA 483 실사 관찰",
            "category": "Other", "modality": "💊 합성의약품", "evidence_level": "B",
            "signal_tier": 3, "signal_label": "High", "type_tag": "483",
            "headline_target": "BPI Labs, LLC", "title_issue": "", "summary": "",
            "facts": [{"label": "문서번호", "value": "fda483-detail-smoke"}],
            "quotes": [], "key_facts": [], "implication": "", "checks": [],
            "merged_count": 1, "merged_items": [],
            "sources": {"info_url": "", "official_url": "https://www.fda.gov/media/1/download",
                        "official_is_pdf": True,
                        "link_check": {"info": "pending", "official": "pending"}},
            "deterministic_detail": {
                "type": "fda_483_observations", "count": 2,
                "observations": [
                    {"number": "1",
                     "deficiency": "There is a failure to thoroughly review discrepancies.",
                     "detail": "The investigation did not extend to other batches."},
                    {"number": "2",
                     "deficiency": "Sampling plans are not documented at performance.",
                     "detail": ""}]},
        })
        base["cards"] = base["cards"] + [card]
        (data / "brief_web_2026_06_08.json").write_text(
            json.dumps(base, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, cls._tmp / "out")
        cls.html = (cls._tmp / "out" / "briefs/2026-06-08/index.html").read_text(
            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_observation_detail_block_rendered(self):
        self.assertIn("Observation 상세", self.html)
        self.assertIn("Observation 2건", self.html)
        self.assertIn('class="obs-num">Observation 1</span>', self.html)
        self.assertIn("There is a failure to thoroughly review discrepancies.", self.html)
        self.assertIn("원문 기반", self.html)


class WebFda483DeepAnalysisTest(unittest.TestCase):
    """[483 분석층 2026-07-02] FDA 483 deep_analysis(4섹션) 렌더 스모크 — ②섹션이
    inspectional_significance 이면 483 한글 섹션명("실사 지적의 의미")으로 스왑되고 WL 영문
    섹션명은 나타나지 않는다. 483 은 결정론 상세(Observation)와 분석층을 함께 가질 수 있다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_483deep_"))
        data = cls._tmp / "data"
        data.mkdir(parents=True, exist_ok=True)
        base = json.loads(
            (MULTI_FIXTURES / "brief_web_2026_06_08.json").read_text(encoding="utf-8"))
        card = dict(base["cards"][0])
        card.update({
            "id": "fda483-deep-smoke", "render_order": 999, "group": "글로벌",
            "group_label": None, "agency": "FDA", "card_type": "FDA 483 실사 관찰",
            "category": "Other", "modality": "💊 합성의약품", "evidence_level": "B",
            "signal_tier": 3, "signal_label": "High", "type_tag": "483",
            "headline_target": "BPI Labs, LLC", "title_issue": "", "summary": "",
            "facts": [{"label": "문서번호", "value": "fda483-deep-smoke"}],
            "quotes": [], "key_facts": [], "implication": "", "checks": [],
            "merged_count": 1, "merged_items": [],
            "sources": {"info_url": "", "official_url": "https://www.fda.gov/media/1/download",
                        "official_is_pdf": True,
                        "link_check": {"info": "pending", "official": "pending"}},
            "deep_analysis": {
                "key_violations": [
                    {"citation": "21 CFR 211.192",
                     "observation": "OOS 결과를 과학적 근거 없이 무효화하고 조사를 문서화하지 않음",
                     "risk": "불량 배치가 시장에 유통될 위험"}],
                "inspectional_significance": (
                    "데이터 무결성·무균 관리의 systemic 결함으로 Warning Letter 승격 가능성이 있다."),
                "required_remediation": {
                    "deadline": "483 수령 후 15영업일 이내 서면 회신",
                    "items": ["OOS 조사 절차를 재수립하고 소급 검토를 수행한다"]},
                "administrative_risks": "미시정 시 Import Alert·OAI 분류로 이어질 수 있다.",
            },
        })
        base["cards"] = base["cards"] + [card]
        (data / "brief_web_2026_06_08.json").write_text(
            json.dumps(base, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, cls._tmp / "out")
        cls.html = (cls._tmp / "out" / "briefs/2026-06-08/index.html").read_text(
            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _deep_block(self) -> str:
        start = self.html.index('<details class="block deep">')
        return self.html[start:self.html.index("</details>", start)]

    def test_deep_block_uses_483_korean_section_names(self):
        block = self._deep_block()
        self.assertIn("위반 항목 및 리스크", block)       # ①
        self.assertIn("실사 지적의 의미", block)          # ② (483 전용)
        self.assertIn("요구 시정 조치", block)            # ③ (483 전용)
        self.assertIn("행정 리스크", block)               # ④
        # ② 본문(inspectional_significance)이 렌더된다.
        self.assertIn("Warning Letter 승격 가능성", block)
        # ① key_violations 의 observation 키(483 스키마)가 본문으로 렌더된다.
        self.assertIn("OOS 결과를 과학적 근거 없이 무효화", block)

    def test_wl_english_section_names_absent(self):
        # 483 카드에는 WL 영문 섹션명이 나타나면 안 된다(스왑 정확).
        block = self._deep_block()
        self.assertNotIn("FDA's Evaluation of Response", block)
        self.assertNotIn("Key Violations", block)
        self.assertNotIn("Required Remediation", block)
        self.assertNotIn("Administrative Risks", block)

    def test_deep_preview_hint_is_483_flavored(self):
        # 접힘 요약 힌트가 483 색("실사의미")으로 나온다(처분근거/대응조치 아님).
        block = self._deep_block()
        self.assertIn("실사의미", block)
        self.assertNotIn("처분근거", block)
        self.assertNotIn("대응조치", block)


class WebMonoLabelsContractTest(unittest.TestCase):
    """render.MONO_LABELS ↔ card_scaffold._w2_rows 라벨 어휘 계약(교차 모듈 드리프트 가드).

    MONO_LABELS 는 `_w2_rows` 가 산출하는 라벨명을 문자열로 재기술한다(facts.label 매칭 시
    mono 렌더). `_w2_rows` 가 라벨을 rename 하면 매칭이 조용히 끊겨 mono 표기가 소실된다(무경보).
    골든 web-card(tests/golden/*.webcard.json)의 실제 facts 라벨 어휘로 이 결합을 고정한다.
    셋은 현행 실측 고정(배치2 P1 §Phase2).
    """

    _SCAFFOLD_GOLDEN = WEB_DIR.parent / "tests" / "golden"

    def _produced_labels(self) -> set:
        labels: set = set()
        for fn in sorted(self._SCAFFOLD_GOLDEN.glob("*.webcard.json")):
            card = json.loads(fn.read_text(encoding="utf-8"))
            for fact in card.get("facts") or []:
                if isinstance(fact, dict) and "label" in fact:
                    labels.add(fact["label"])
        return labels

    def test_mono_labels_vocabulary_pinned(self):
        produced = self._produced_labels()
        self.assertTrue(produced, "웹카드 골든에서 facts 라벨을 수집하지 못함")
        # mono 4종은 실제 산출 어휘에 존재(mono 렌더 활성) — 라벨 rename 시 red.
        self.assertEqual(render.MONO_LABELS & produced,
                         {"발행일", "문서번호", "실사일", "Class"})
        # '회수 등급' = `_w2_rows` 미산출 dormant 라벨(현행 실측 고정 · 배치2 보고).
        # 신규 고아 추가·산출 어휘 변경 시 red.
        self.assertEqual(render.MONO_LABELS - produced, {"회수 등급"})


class WebBriefFirmLinkTest(unittest.TestCase):
    """[브리프→업체 프로파일 브릿지] render._firm_key_for_card()·카드 data-firm-key
    스탬프·brief.html cfg div·인라인 JS(findings_firm_counts RPC 1회 호출) 계약.

    render._FIRM_FACT_LABELS(업체/제조소/제조소·업체/업체·제조소)는 card_scaffold.py
    _w2_extra_*() 가 실제로 산출하는 라벨 어휘의 부분집합이어야 한다 — 아래
    test_firm_fact_labels_subset_of_scaffold_vocabulary 가 tests/golden/*.webcard.json
    (card_scaffold 골든, 이 웹 서브트리와 별개 관리)의 실측 라벨로 드리프트를 고정한다
    (WebMonoLabelsContractTest 와 동형 계약 패턴).
    """

    _SCAFFOLD_GOLDEN = WEB_DIR.parent / "tests" / "golden"

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_firmlink_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.detail = (cls.single / "briefs/2026-06-26/index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _render_card_partial(self, card: dict) -> str:
        env = render._make_env()
        view = render._card_view(card)
        return env.get_template("partials/card.html").render(card=view)

    # ── (1) _firm_key_for_card() 순수 함수 단위 테스트 ──────────────────────
    def test_label_업체_extracted_and_normalized(self):
        card = {"facts": [{"label": "업체", "value": "SCA Pharmaceuticals, Inc."}]}
        self.assertEqual(render._firm_key_for_card(card), "sca pharmaceuticals")

    def test_label_제조소_extracted(self):
        card = {"facts": [{"label": "제조소", "value": "Baxter Oncology GmbH"}]}
        self.assertEqual(render._firm_key_for_card(card), "baxter oncology")

    def test_label_제조소_업체_extracted(self):
        card = {"facts": [{"label": "제조소/업체", "value": "BPI Labs, LLC · FEI 3015156709"}]}
        self.assertEqual(render._firm_key_for_card(card), "bpi labs")

    def test_label_업체_제조소_extracted(self):
        card = {"facts": [{"label": "업체/제조소", "value": "Huons Co., Ltd."}]}
        self.assertEqual(render._firm_key_for_card(card), "huons")

    def test_country_code_suffix_stripped_before_normalize(self):
        # 행정처분 배선(_w2_extra_admin) 접미사 — " (KR)" 국가코드(공백+괄호).
        card = {"facts": [{"label": "업체", "value": "Acme Pharma, Inc. (KR)"}]}
        self.assertEqual(render._firm_key_for_card(card), "acme pharma")

    def test_fei_suffix_stripped_before_normalize(self):
        # FDA 483 배선(_w2_extra_fda_483) 접미사 — " · FEI 12345"(공백+가운뎃점).
        card = {"facts": [{"label": "제조소/업체", "value": "Zep Inc · FEI 1234567"}]}
        self.assertEqual(render._firm_key_for_card(card), "zep")

    def test_korean_parenthesis_suffix_not_mistaken_for_separator(self):
        # 한글 법인 표기(예: "경방신약(주)")는 공백 없이 괄호가 바로 붙어 있어
        # " (" 구분자에 매칭되지 않는다(오탐 방지 — 실 fixture 실측 케이스).
        card = {"facts": [{"label": "업체", "value": "경방신약(주)"}]}
        self.assertEqual(render._firm_key_for_card(card), "경방신약(주)")

    def test_placeholder_value_yields_empty_key(self):
        card = {"facts": [{"label": "업체", "value": "원문 미기재"}]}
        self.assertEqual(render._firm_key_for_card(card), "")

    def test_empty_value_yields_empty_key(self):
        card = {"facts": [{"label": "업체", "value": ""}]}
        self.assertEqual(render._firm_key_for_card(card), "")

    def test_no_matching_label_yields_empty_key(self):
        card = {"facts": [{"label": "발행기관", "value": "WHO"}, {"label": "주제", "value": "머시기"}]}
        self.assertEqual(render._firm_key_for_card(card), "")

    def test_no_facts_yields_empty_key(self):
        self.assertEqual(render._firm_key_for_card({}), "")
        self.assertEqual(render._firm_key_for_card({"facts": []}), "")

    def test_first_matching_fact_used_even_if_extraction_fails(self):
        # "첫 매칭 fact 1개만 사용" — 첫 매칭이 placeholder 면 다른 매칭 fact 로
        # 넘어가지 않고 바로 실패(빈 문자열) 처리한다.
        card = {"facts": [
            {"label": "업체", "value": "원문 미기재"},
            {"label": "제조소", "value": "Real Firm Inc."},
        ]}
        self.assertEqual(render._firm_key_for_card(card), "")

    def test_firm_key_matches_normalize_firm_name_directly(self):
        # 파리티 확인 — grm_findings.normalize_firm_name() 을 그대로 재사용하는지.
        import grm_findings
        card = {"facts": [{"label": "업체", "value": "Johnson &amp; Johnson"}]}
        self.assertEqual(
            render._firm_key_for_card(card),
            grm_findings.normalize_firm_name("Johnson &amp; Johnson"),
        )

    # ── (2) card.html data-firm-key 스탬프 ──────────────────────────────────
    def test_data_firm_key_attribute_present_when_extractable(self):
        card = {
            "render_order": 0, "id": "c1", "card_type": "Warning Letter", "agency": "FDA",
            "evidence_level": "A", "signal_label": "High", "signal_tier": 1,
            "headline_target": "Acme", "facts": [{"label": "업체/제조소", "value": "Acme Pharma, Inc."}],
        }
        html = self._render_card_partial(card)
        self.assertIn('data-firm-key="acme pharma"', html)

    def test_data_firm_key_attribute_omitted_when_not_extractable(self):
        card = {
            "render_order": 0, "id": "c2", "card_type": "WHO", "agency": "WHO",
            "evidence_level": "B", "signal_label": "Low", "signal_tier": 3,
            "headline_target": "WHO 뉴스", "facts": [{"label": "발행기관", "value": "WHO"}],
        }
        html = self._render_card_partial(card)
        self.assertNotIn("data-firm-key", html)

    def test_firm_fact_labels_subset_of_scaffold_vocabulary(self):
        labels: set = set()
        for fn in sorted(self._SCAFFOLD_GOLDEN.glob("*.webcard.json")):
            wc = json.loads(fn.read_text(encoding="utf-8"))
            for fact in wc.get("facts") or []:
                if isinstance(fact, dict) and "label" in fact:
                    labels.add(fact["label"])
        self.assertTrue(labels, "웹카드 골든에서 facts 라벨을 수집하지 못함")
        self.assertTrue(render._FIRM_FACT_LABELS.issubset(labels))
        # 정확히 이 4개 라벨(추가/누락 시 드리프트 — 신규 소스 배선 시 의도적으로 갱신).
        self.assertEqual(
            render._FIRM_FACT_LABELS,
            {"업체", "제조소", "제조소/업체", "업체/제조소"},
        )

    def test_real_fixture_stamps_firm_key_on_most_cards(self):
        # 6/26 실 fixture: 27장 중 firm 라벨이 없는 유형(발행기관 등) 제외 대부분에
        # data-firm-key 가 스탬프된다(회귀 스모크 — 정확한 개수보다 "0건이 아님·과반"
        # 을 고정해 카드 구성 변화에 과민하지 않게 한다).
        self.assertGreaterEqual(self.detail.count("data-firm-key="), 20)

    # ── (3) brief.html cfg div + 인라인 JS 계약 ─────────────────────────────
    def test_cfg_div_present_unconditionally(self):
        self.assertIn(
            '<div id="grm-brief-firm-cfg" data-url="" data-key="" data-root="../../" hidden></div>',
            self.detail,
        )

    def test_js_single_rpc_call_not_per_card(self):
        # fetch() 가 브리프 상세 페이지 전체에서 정확히 1번만 호출된다 — 카드마다
        # 개별 호출하지 않고 for-each 밖에서 1회만 여는 계약(신규 구독폼 fetch 등
        # 다른 fetch 가 없는 이 고정 fixture 빌드 기준).
        self.assertEqual(self.detail.count("fetch("), 1)
        self.assertEqual(self.detail.count("p_firm_keys"), 1)
        # RPC POST body 는 카드에서 모은 고유 firm_key 배열 하나(keys 변수).
        self.assertIn("JSON.stringify({ p_firm_keys:keys })", self.detail)

    def test_js_defensive_early_returns_present(self):
        js = self.detail
        self.assertIn("var cfg=document.getElementById('grm-brief-firm-cfg'); if(!cfg) return;", js)
        self.assertIn("if(!url||!key) return;", js)
        self.assertIn("if(!cards.length) return;", js)

    def test_js_uses_textcontent_and_createelement_only(self):
        # innerHTML 데이터 삽입 금지(findings.js/firm.js 와 동일 계약) — 이 IIFE 구간엔
        # innerHTML 자체가 아예 등장하지 않아야 한다(회귀: 값 삽입 시 XSS 방지). 앵커는
        # 이 IIFE 도입부(cfg 조회 줄, 유일 문자열)에서 `})();` 로 끝나는 지점까지만
        # 전방(forward-only) 슬라이스한다 — 직전 스크립트(요약행 조립, innerHTML 사용)
        # 를 앞쪽 컨텍스트로 잘못 포함하지 않기 위함.
        start = self.detail.index("var cfg=document.getElementById('grm-brief-firm-cfg'); if(!cfg) return;")
        end = self.detail.index("})();", start) + len("})();")
        block = self.detail[start:end]
        self.assertNotIn(".innerHTML", block)
        self.assertIn(".textContent=", block)
        self.assertIn("document.createElement(", block)

    def test_js_link_href_uses_root_and_encoded_key(self):
        self.assertIn(
            "a.href=root+'findings/firm/index.html?key='+encodeURIComponent(k);",
            self.detail,
        )

    def test_js_reuses_grm_ca_pill_class_no_new_css(self):
        # grm.css 변경 금지 — 기존 공유버튼과 동일한 .grm-ca 클래스 재사용.
        self.assertIn("a.className='grm-ca';", self.detail)


if __name__ == "__main__":
    if "--freeze" in sys.argv:
        freeze()
    else:
        unittest.main()
