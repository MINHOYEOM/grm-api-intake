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
    "WebKoreanSafetyTest",
    "WebSeoMetaTest",
    "WebDeterministicDetailTest",
    "WebFda483DeterministicDetailTest",
    "WebFda483DeepAnalysisTest",
    "WebMonoLabelsContractTest",
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
        self.assertIn('id="findings-notice"', self.html)
        self.assertIn("AI 자동 추출 고지", self.html)

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
        # findings 페이지에서만 nav 'on' 클래스가 지적사항 링크에 붙는다.
        self.assertIn('href="../findings/index.html" class="on">지적사항</a>', self.html)
        self.assertIn('href="findings/index.html">지적사항</a>', self.landing)
        self.assertNotIn('href="../findings/index.html" class="on">지적사항</a>', self.archive)
        self.assertIn('href="../findings/index.html">지적사항</a>', self.archive)

    def test_footer_link_present(self):
        self.assertIn('<a href="../findings/index.html">지적사항</a>', self.html)

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

    # ── FIND-1 M10b: 카드 UX 오버홀(고지 슬림화·검토필요 경계·기본접힘·하이라이트·refs 상태) ──
    def test_notice_is_details_summary_slim_banner(self):
        """AI 고지(P0)는 <details>/<summary> 구조로 축소돼 있어야 한다 — summary 는 항상
        보이는 한 줄 요지, 본문은 펼쳐야 보인다. id/aria-label 은 기존 계약대로 유지."""
        import re as _re
        self.assertIn('id="findings-notice" aria-label="AI 자동 추출 고지"', self.html)
        m = _re.search(
            r'<section class="fnd-notice" id="findings-notice"[^>]*>\s*<details',
            self.html)
        self.assertIsNotNone(m, "고지 섹션이 details 로 시작하지 않음")
        self.assertIn("<summary", self.html)

    def test_notice_summary_has_short_gist_and_quiet_style(self):
        """summary 한 줄은 조용한 스타일 클래스(.fnd-notice-sum)를 쓰고, 펼침 힌트 아이콘
        (ti-info-circle)을 포함하며, 요지 문구를 담는다."""
        self.assertIn("fnd-notice-sum", self.html)
        self.assertIn("ti-info-circle", self.html)
        self.assertIn("AI 자동 추출 고지 — 누락·오분류 가능, 의사결정 전 반드시 원문 대조", self.html)

    def test_page_head_description_is_one_sentence(self):
        """첫 화면 밀도(보조) — page-head 설명문단이 압축된 한 문장(마침표 1개로 종결)인지 확인."""
        import re as _re
        m = _re.search(r'<p class="reveal"[^>]*>([^<]*)</p>', self.html)
        self.assertIsNotNone(m)
        text = m.group(1)
        self.assertEqual(text.count("."), 1, f"한 문장이 아닌 것으로 보임: {text!r}")
        self.assertTrue(text.endswith("검색합니다."), text)

    def test_review_card_boundary_markers_present(self):
        """검토 필요(needs_review) 카드는 article 에 fnd-card--review 클래스가 붙고,
        상시 경고 한 줄(.fnd-review-note)이 배지 줄 아래에 렌더돼야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('card.classList.add("fnd-card--review")', js_src)
        self.assertIn("fnd-review-note", js_src)
        self.assertIn("AI 추출 검수 전", js_src)
        self.assertIn("원문 대조 필수", js_src)
        # CSS: coral 계열 왼쪽 보더 + 틴트 배경(--coral-tint 재사용).
        self.assertIn(".fnd-card.fnd-card--review", self.html)
        self.assertIn("border-left:4px solid var(--coral)", self.html)
        self.assertIn("var(--coral-tint)", self.html)

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

    # ── FIND-1 M10c: 탐색 툴바 오버홀(칩 필터·건수병기·정렬·sticky·모바일 접기·URL 동기화) ──
    def test_low_cardinality_filters_are_chip_groups_not_selects(self):
        """기관·소스·증거등급·검토상태는 <select> 대신 버튼 칩 그룹 컨테이너로 렌더된다
        (값은 findings.js 가 런타임에 채운다) — 4개 드롭다운 제거를 셸 마크업으로 확인.
        카테고리·발행월은 20종/월 단위라 여전히 드롭다운을 유지한다."""
        for facet_id in ("fnd-f-agency", "fnd-f-source", "fnd-f-evidence", "fnd-f-status"):
            self.assertIn(f'<div class="fnd-chipgroup" id="{facet_id}"', self.html)
            self.assertNotIn(f'<select id="{facet_id}"', self.html)
        self.assertIn('<select id="fnd-f-category"', self.html)
        self.assertIn('<select id="fnd-f-month"', self.html)

    def test_chip_group_skeleton_and_refresh_wiring_present(self):
        """칩은 실제 <button type=button> + aria-pressed 이고, DOM 은 1회만 만들고(스켈레톤)
        매 render() 마다 건수/on/disabled 만 갱신하는 구조인지 소스 마커로 확인."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('btn.type = "button"', js_src)
        self.assertIn('btn.setAttribute("aria-pressed"', js_src)
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

    def test_reset_clears_sort_and_relies_on_render_for_url_clear(self):
        """초기화 버튼은 sort 도 기본값(date_desc)으로 되돌리고, querystring 은 render()의
        syncStateToUrl() 이 기본 state 를 반영해 자동으로 비운다(별도 URL clear 불필요)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        reset_block = js_src[js_src.index("if (resetBtn)"):js_src.index("if (resetBtn)") + 400]
        self.assertIn('sort: "date_desc"', reset_block)
        self.assertIn("syncControlsFromState()", reset_block)
        self.assertIn("render()", reset_block)

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


if __name__ == "__main__":
    if "--freeze" in sys.argv:
        freeze()
    else:
        unittest.main()
