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
import re
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
# [업계 브리핑 노트 2026-07-13] resources 섹션 전용 격리 픽스처(단일 브리프) — single/multi
# 아카이브·랜딩 집계 골든(카드 수·issue 수 의존)에 영향 0. brief.resources 는
# assemble_publish_brief.extract_resource_notes() 산출 형태를 그대로 모사(§1 자료구조).
RESOURCE_FIXTURES = TESTS_DIR / "fixtures" / "resources"
DATA_DIR = WEB_DIR / "data" / "briefs"                       # 라이브 발행 디렉터리(스모크 렌더 전용)
REAL_FIXTURE = SINGLE_FIXTURES / "brief_web_2026_06_22.json"

# CI shim(tests/test_web_render.py)은 이 모듈의 TestCase 하위클래스를 **전수 자동** 수집한다.
# (예전엔 __all__ 수동 목록이라 새 클래스를 적는 걸 잊으면 CI 에서 조용히 실행되지 않았다.)


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


def _build_resources(out: pathlib.Path) -> None:
    """[업계 브리핑 노트] 격리 픽스처(1건) 단독 빌드 — single/multi 와 완전 분리."""
    render.render_site(RESOURCE_FIXTURES, out)


# (built_relpath, golden_filename)
RESOURCE_GOLDENS = [
    ("briefs/2026-05-01/index.html", "brief_resources.expected.html"),
]


# (built_relpath, golden_filename)
SINGLE_GOLDENS = [
    ("index.html", "landing.expected.html"),
    ("archive/index.html", "archive.expected.html"),
    ("findings/index.html", "findings.expected.html"),
    ("findings/trends/index.html", "trends.expected.html"),
    ("findings/firm/index.html", "firm.expected.html"),
    ("library/index.html", "library.expected.html"),
    ("library/ich/index.html", "library_ich.expected.html"),
    ("library/mfds/index.html", "library_mfds.expected.html"),
    ("library/eu-gmp/index.html", "library_eu_gmp.expected.html"),
    ("library/pics/index.html", "library_pics.expected.html"),
    ("library/who/index.html", "library_who.expected.html"),
    ("library/fda-guidance/index.html", "library_fda_guidance.expected.html"),
    ("library/ema/index.html", "library_ema.expected.html"),
    ("library/health-canada/index.html", "library_health_canada.expected.html"),
    ("guide/index.html", "guide.expected.html"),
    ("glossary/index.html", "glossary.expected.html"),
    ("quiz/index.html", "quiz.expected.html"),
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
        cls.resources = cls._tmp / "resources"
        _build_single(cls.single)
        _build_multi(cls.multi, cls._tmp)
        _build_resources(cls.resources)

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

    def test_resource_goldens(self):
        # [업계 브리핑 노트 2026-07-13] resources 섹션 렌더 스냅샷(격리 픽스처).
        for rel, name in RESOURCE_GOLDENS:
            with self.subTest(golden=name):
                self._assert_golden(self.resources, rel, name)

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
        self.assertEqual(nav_m.group(1).count("<a "), 6, "nav 탭은 모아보기·찾아보기·트렌드·자료실·용어사전·이용안내 6개여야 함")
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

    # [서버 canonical search] LEGACY_FIELDS/fetchFindings(LEGACY_FIELDS) 005 폴백은
    # 사라졌다 — findings_search RPC 가 반환 컬럼의 정본이라 005(finding_text_ko/
    # translation_method) 미적용 방어는 이제 서버 함수 쪽 책임이다.

    def test_server_canonical_search_is_sole_data_source(self):
        """[서버 canonical search 계약 고정] findings.js 는 검색·필터·정렬·문서묶음·
        페이지네이션·파셋·대시보드 집계를 전부 findings_search RPC 하나에 위임한다 —
        /rest/v1/findings?select= 직접 조회(구버전 클라이언트측 부분 로드+집계
        아키텍처)는 완전히 사라졌다. findings_document(딥링크)·findings_similar/
        findings_similar_to(유사검색)·findings_stats(커버리지 노트)는 각각 독립된
        보조 RPC 라 계속 호출되는 것이 정상이다(오탐 방지 — 그 이름들은 제외 대상이
        아니다). 총수는 항상 exact 라 " 이상" 같은 불확실성 접미사가 존재할 이유가
        없다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('"/rest/v1/rpc/findings_search"', js_src)
        self.assertNotIn("/rest/v1/findings?select=", js_src)
        self.assertIn('"/rest/v1/rpc/findings_document"', js_src)
        self.assertNotIn(' 이상"', js_src)
        self.assertNotIn("filtersActive", js_src)

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

    # [서버 canonical search] computeStats/computeAgencyDist/computeCategoryDist/
    # computeMonthTrend/computeFirmTop 순수 집계 함수는 사라졌다 — 대시보드 집계는
    # findings_search RPC 의 LAST.dash 가 정본이고 renderDash() 는 인자 없이 그 값만
    # 소비한다(코드 자체가 클라이언트 집계를 하지 않으므로 이 클래스의 마커 테스트는
    # 전제 소멸). 클릭 시 기존 state·select 재사용 계약은
    # test_filter_and_sort_and_search_reset_to_page_one 이 이미 커버한다.

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
        """데이터 로드 실패/0건이면 밴드 자체를 숨긴다(빈 필터 결과에서도 동일) — [서버
        canonical search] 판정 기준은 클라이언트가 센 matched.length 가 아니라 서버
        exact 총수(LAST.totals.findings)다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("if (!LAST.totals.findings) {\n      dashEl.hidden = true;", js_src)

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
        """[M14] 기관 칩 UI 는 사라졌지만 state.agency·URL param(agency)은 findings.js
        소스에 남아 있어야 한다 — URL 로 agency 가 들어오면 여전히 필터링된다. [서버
        canonical search] 매칭은 더 이상 클라이언트 row 비교(row.agency !== state.agency)
        가 아니라 findings_search RPC 의 p_agency 인자로 서버에 위임된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('agency: "", category_code: "", source: ""', js_src)
        self.assertIn('agency: "agency"', js_src)  # URL_KEYS
        self.assertIn("p_agency: state.agency,", js_src)
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

    # [서버 canonical search] computeFacetCounts()/rowMatchesFilters()/searchTermsFor() 는
    # 사라졌다 — 표준 파세팅(자기 축 제외)·검색어 매칭·보이는 메타데이터 검색 대상 포함은
    # 전부 findings_search RPC(SQL)가 수행한다. facetCounts() 는 서버가 이미 계산한
    # LAST.facets 를 그대로 평탄화할 뿐 자체 매칭 로직이 없다(파일 상단 §서버 canonical
    # search 주석 참조) — 이 두 테스트가 고정하던 클라이언트 함수 자체가 없다.

    def test_sort_select_present_with_three_options(self):
        self.assertIn('<select id="fnd-sort">', self.html)
        self.assertIn('<option value="date_desc">최신순</option>', self.html)
        self.assertIn('<option value="date_asc">오래된순</option>', self.html)
        self.assertIn('<option value="firm_asc">업체명순</option>', self.html)
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        # [서버 canonical search] sortRows() 클라이언트 정렬은 사라졌다 — 정렬은
        # findings_search RPC 의 p_sort 인자로 서버가 수행하고, 3종 전부 항상 활성이다
        # (옛 updateSortAvailability() 비활성화 회피책도 함께 제거됨).
        self.assertNotIn("function sortRows(rows)", js_src)
        self.assertIn('var SORT_VALUES = ["date_desc", "date_asc", "firm_asc"];', js_src)
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

    def test_url_sync_sort_validated_but_filter_values_passed_through(self):
        """[서버 canonical search] 종전엔 URL 의 알 수 없는 파셋 값을 collectFacetValues()
        로 사후 검증해 조용히 무시했다 — 이제는 검증 없이 그대로 실어 서버(findings_search)
        에 보낸다: 서버가 모르는 값이면 결과가 0건이 되고, 적용 필터 칩 행(#fnd-active)에
        그 값이 그대로 노출돼 한 번의 클릭으로 해제할 수 있다(readStateFromUrl() 주석 —
        "URL 이 말하는 필터와 화면이 어긋나면 안 된다"). sort 값만은 여전히 클라이언트에서
        검증한다 — <select> 에 없는 값을 대입하면 조용히 무시되기 때문이다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function readStateFromUrl()"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("SORT_VALUES.indexOf(sortRaw) !== -1", fn)
        self.assertNotIn("collectFacetValues", fn)
        self.assertIn("if (raw !== null) state[k] = raw;", fn)

    def test_clear_all_filters_resets_sort_and_relies_on_render_for_url_clear(self):
        """[M15] #fnd-reset 버튼은 제거됐다 — "모두 지우기"는 clearAllFilters() 가 담당하며
        sort 도 기본값(date_desc)으로 되돌린다. [문서 단위 페이지네이션] 전체 초기화는
        currentPage=1 로 되돌리고 goToPage(1) 로 재렌더하며(goToPage → render()), 그
        render()의 syncStateToUrl() 이 기본 state 를 반영해 querystring 을 자동으로 비운다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function clearAllFilters()", js_src)
        # [PR-0 딥링크] exitDeepLinkMode() 호출 한 줄이 앞에 추가돼 고정폭 320 이 goToPage(1)
        # 을 담기엔 부족해졌다 — 400 으로 상향(함수 본문 전체를 여전히 넉넉히 담는다).
        # [FIND-1 S1] exitSimilarMode() 호출 한 줄이 추가로 앞에 붙어 400 도 부족해졌다 —
        # 480 으로 재상향(함수 본문 전체를 여전히 넉넉히 담는다).
        fn_block = js_src[js_src.index("function clearAllFilters()"):js_src.index("function clearAllFilters()") + 480]
        self.assertIn('sort: "date_desc"', fn_block)
        self.assertIn("syncControlsFromState()", fn_block)
        self.assertIn("currentPage = 1", fn_block)
        self.assertIn("goToPage(1)", fn_block)
        # goToPage 가 실제로 render() 를 호출해 재렌더(→URL clear)를 일으키는지 확인.
        # [서버 canonical search] fetchSearch().then() 콜백 안에 페이지 경계 방어 주석·분기가
        # 추가돼 render() 호출까지의 거리가 길어졌다 — 600 으로는 부족해 900 으로 재상향.
        goto_block = js_src[js_src.index("function goToPage(n)"):js_src.index("function goToPage(n)") + 900]
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
        # [PR-0 딥링크] fetchCoverageNote() 와 첫 fetchSearch(currentPage) 사이에 딥링크
        # 해석 킥오프가 끼어들어 더 이상 텍스트상 바로 인접하지 않는다 — 순서(전자가 먼저)만
        # 확인한다(둘 다 여전히 정확히 1회, showState("loading") 직후 영역에서 호출됨).
        self.assertIn("fetchCoverageNote();", js_src)
        self.assertLess(js_src.index("fetchCoverageNote();"), js_src.index("fetchSearch(currentPage)\n"))

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
    # [서버 canonical search] FIELDS/FIELDS_NO_FIRM_KEY/LEGACY_FIELDS select 목록과
    # fetchFindings() 3단 폴백 체인은 사라졌다 — findings_search RPC 가 반환 컬럼(raw_signal_id/
    # firm_key 포함)의 정본이라 클라이언트가 select 목록을 협상할 이유가 없다(005/013
    # 미적용 방어는 이제 서버 함수 쪽 책임). 문서 그룹핑 키(raw_signal_id)·업체 프로파일
    # 링크(firm_key)가 여전히 응답에 실려 오는지는 buildDocHead()/render() 소비 마커
    # (test_document_card_head_links_to_firm_profile_when_key_present 등)가 계속 확인한다.

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

    # [서버 canonical search] groupByDocument() 클라이언트 그룹핑은 사라졌다 — 서버가
    # raw_signal_id 로 이미 묶어 documents[] 배열로 보내므로(§ 파일 상단 [문서 중심 열람]
    # 주석) 클라이언트가 재그룹핑·재정렬할 이유가 없다. wire() 가 필터/정렬/검색어 변경
    # 시 currentPage=1 로 리셋 후 goToPage(1) 을 호출하는 계약은
    # test_deeplink_exits_on_filter_search_sort_page_interaction 등이 계속 커버한다.

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
        페이지 X / Y" 형태여야 한다 — [서버 canonical search] N=totalDocs/M=totalFindings
        는 이제 findings_search RPC 가 반환하는 LAST.totals 그대로(항상 exact, 로드분
        추정 아님), X=현재 페이지, Y=LAST.pages(서버가 계산한 exact 총 페이지 수)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        start = js_src.index("function render() {")
        render_fn = js_src[start:js_src.index("\n  function ", start + 20)]
        self.assertIn('bDocs.textContent = totalDocs.toLocaleString("ko-KR")', render_fn)
        self.assertIn('bObs.textContent = totalFindings.toLocaleString("ko-KR")', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode("전체 "));', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode("문서 · "));', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode("지적 · 페이지 "));', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode(" / "));', render_fn)
        self.assertNotIn('countEl.appendChild(document.createTextNode("총 "));', js_src)

    def test_result_count_line_all_numbers_use_locale_string(self):
        """[콤마 통일] 카운트 줄의 문서수·지적수·총 페이지수는 모두 toLocaleString
        ('ko-KR') 로 천단위 콤마를 붙인다(현재 페이지 번호만은 순수 정수라 콤마 대상이
        아니다) — [서버 canonical search] 값은 항상 exact 라 String(totalDocs)/
        String(totalFindings) 처럼 콤마 없는 표기가 남아있으면 안 된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        render_fn = js_src[js_src.index("function render() {"):js_src.index("\n  function ", js_src.index("function render() {") + 20)]
        self.assertNotIn("String(totalDocs)", render_fn)
        self.assertNotIn("String(totalFindings)", render_fn)
        self.assertIn('bDocs.textContent = totalDocs.toLocaleString("ko-KR")', render_fn)
        self.assertIn('bObs.textContent = totalFindings.toLocaleString("ko-KR")', render_fn)
        self.assertIn('bTotal.textContent = String(totalPages)', render_fn)

    # [서버 canonical search] SERVER_DOC_TOTAL/SERVER_FINDINGS_TOTAL/exactUnfiltered/
    # uncertain(" 이상" 접미사) 는 전부 사라졌다 — findings_search RPC 가 필터 여부와
    # 무관하게 항상 exact totals/pages 를 반환하므로("totals 는 검색·필터 적용 후 exact
    # 다" — render() 상단 주석), 로드분 기준 추정치와 서버 exact 값을 조건부로 바꿔치기할
    # 필요 자체가 없다. test_result_count_line_* 이 그 결과(totalDocs/totalFindings 를
    # 조건 없이 그대로 표시)를 계속 검증한다.

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

    # [서버 canonical search] Prefer: count=exact/parseServerTotal()/buildEndpoint()/
    # fetchNextChunkFor()/LOADED_FIELDS 는 사라졌다 — findings_search RPC 응답 바디의
    # totals/pages 가 정본이라(항상 exact) PostgREST Content-Range 헤더를 직접 파싱하거나
    # 다음 "청크"를 별도 offset 으로 이어 fetch 할 필요가 없다(서버가 요청한 페이지의
    # 문서만 정확히 잘라 보낸다).

    def test_goto_page_navtoken_guards_against_stale_responses(self):
        """[구조 변경] 청크 단위 중복 fetch 방어(fetchNextChunkFor/pendingPageCallbacks
        콜백 큐)는 서버 canonical search 전환으로 사라졌다 — goToPage() 1회 호출이
        findings_search RPC 1왕복으로 끝나 별도 큐가 필요 없다. 대신 navToken 세대
        카운터가 연타(빠른 재클릭)를 막는다 — 오래된 요청의 응답은 myToken !== navToken
        이면 LAST/currentPage/render() 를 건드리지 않고 조용히 버려진다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertNotIn("fetchNextChunkFor", js_src)
        self.assertNotIn("pendingPageCallbacks", js_src)
        goto_fn = js_src[js_src.index("function goToPage(n)"):]
        goto_fn = goto_fn[:goto_fn.index("\n  }\n") + 4]
        self.assertIn("navToken += 1;", goto_fn)
        self.assertIn("if (myToken !== navToken) return;", goto_fn)

    # [서버 canonical search] mergeRows()/incompleteDocKey()/ensurePageReady() 는 사라졌다
    # — 문서 경계 완결성(한 문서가 페이지 사이에서 쪼개지지 않게 하는 것)은 이제
    # findings_search RPC 가 문서 단위로 페이지를 나눠 보내는 서버 책임이라, 클라이언트가
    # "이 페이지 마지막 문서가 아직 안 끝났는지" 추가 fetch 로 재확인할 필요가 없다.

    def test_docs_per_page_constant_is_24(self):
        """[문서 단위 페이지네이션] 문서 카드 24개 = 1페이지(스펙 상수)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var DOCS_PER_PAGE = 24;", js_src)

    # [서버 canonical search] isServerExhausted()/moreMayExist/exactUnfiltered/uncertain/
    # SERVER_TOTAL/LAST_BATCH_SIZE/PAGE_LIMIT/fetchGaveUp 는 전부 사라졌다 — "서버 obs
    # 청크가 아직 소진되지 않았을 수 있다"는 불확실성 자체가 없다. findings_search RPC
    # 는 매 요청마다 exact totals/pages 를 반환하므로 render() 는 항상 정확한 문서수·
    # 지적수·총 페이지를 안다(로드 진행 상태를 추정할 필요가 없다).

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

    def test_toggle_filters_actually_toggle_state(self):
        """[Minor 4 -- Codex 통합 정밀점검 2026-07-16] 대시보드 클릭 배선(존재)만 고정하고
        상태 변경(내용)을 안 고정하면 no-op 회귀가 green 으로 통과한다 -- Codex 실증:
        `state.category_code = state.category_code === code ? "" : code;` 를 no-op 으로
        바꿔도(클릭해도 필터가 실제로 걸리지 않아도) 144/144 green 이었다. PR-B 테스트
        정리에서 click-wiring 테스트가 재앵커되며 이 상태-토글 assertion 이 소실됐던 것을
        복원한다 -- 위 test_filter_and_sort_and_search_reset_to_page_one 은 "리셋+재조회"
        만 보고 "실제로 토글되는가"는 안 본다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")

        cat_fn = js_src[js_src.index("function toggleCategoryFilter(code) {"):]
        cat_fn = cat_fn[:cat_fn.index("\n  }\n") + 4]
        self.assertIn('state.category_code = state.category_code === code ? "" : code;', cat_fn)

        month_fn = js_src[js_src.index("function toggleMonthFilter(month) {"):]
        month_fn = month_fn[:month_fn.index("\n  }\n") + 4]
        self.assertIn('state.month = state.month === month ? "" : month;', month_fn)

        # 업체(firm) 클릭은 별도 필터 축이 없다 -- 검색어(state.q)를 업체명으로 설정/해제
        # 하는 것이 계약이다(드롭다운 필터가 아니라 검색창 재사용).
        firm_fn = js_src[js_src.index("function toggleFirmFilter(name) {"):]
        firm_fn = firm_fn[:firm_fn.index("\n  }\n") + 4]
        self.assertIn('state.q = state.q === name ? "" : name;', firm_fn)

        for fn_name, fn in (
            ("toggleCategoryFilter", cat_fn),
            ("toggleMonthFilter", month_fn),
            ("toggleFirmFilter", firm_fn),
        ):
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
        무효/누락 값은 조용히 1로 폴백한다. [서버 canonical search] 페이지 복원은 이제
        첫 fetchSearch() 호출 **이전**에 확정된다(state 가 곧 요청 파라미터라 첫 요청
        자체가 이미 옳은 페이지를 받는다) — maybeFinishInit() 의 비-found 분기가 별도로
        goToPage(readPageFromUrl()) 를 다시 호출해 보정하던 구버전 왕복이 사라졌다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        sync_fn = js_src[js_src.index("function syncStateToUrl()"):]
        sync_fn = sync_fn[:sync_fn.index("\n  }\n") + 4]
        self.assertIn('if (currentPage > 1) params.set("page", String(currentPage));', sync_fn)
        self.assertIn("function readPageFromUrl()", js_src)
        read_fn = js_src[js_src.index("function readPageFromUrl()"):]
        read_fn = read_fn[:read_fn.index("\n  }\n") + 4]
        self.assertIn('var raw = new URLSearchParams(location.search).get("page");', read_fn)
        self.assertIn("return !isNaN(n) && n >= 1 ? n : 1;", read_fn)
        tail = js_src[js_src.index("readStateFromUrl();"):]
        tail = tail[:tail.index("fetchSearch(currentPage)")]
        self.assertIn("currentPage = readPageFromUrl();", tail)
        # maybeFinishInit() 의 비-found 분기는 currentPage 를 이미 신뢰하므로 render() 로
        # 곧장 귀결된다(별도 goToPage(readPageFromUrl()) 재호출 없음).
        finish_fn = js_src[js_src.index("function maybeFinishInit() {"):]
        finish_fn = finish_fn[:finish_fn.index("\n  }\n") + 4]
        self.assertIn("render();", finish_fn)
        self.assertNotIn("goToPage(readPageFromUrl())", finish_fn)

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

    # [서버 canonical search] schedulePrefetch()(선로딩)와 SERVER_AGENCY_TOTALS/조건부
    # exact 바꿔치기(renderDash() 의 filtersActive 분기)는 사라졌다 — findings_search
    # RPC 가 매 요청마다 LAST.dash 로 이미 exact 대시보드 집계(전체·문서·기관·카테고리·
    # 월·업체)를 통째로 반환하므로, 무필터일 때만 별도 findings_stats RPC 값을 조건부로
    # 끼워 넣거나 다음 청크를 미리 당겨올 필요가 없다(renderDash() 는 이제 인자도 없다).

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

    # ── [025→서버 canonical search] 부분 로드 augmentation 전체가 서버 이관으로 소멸 ──────
    # 025 는 "부분 로드가 전역처럼 행동" 문제(화면 FDA 483 910 vs DB 8,078)를 findings_stats
    # RPC 로 보정하는 과도기 조치였다(computeFacetCounts/rpcFacetCounts/RPC_BY_*/
    # dashHideNumbers/ensureLoadMoreNotice/updateSortAvailability/refreshAfterRpcStatsArrival).
    # 서버 canonical search(findings_search RPC)로 전환되며 클라이언트가 부분집합을 로드하는
    # 구조 자체가 사라졌으므로 이 augmentation 계층 전체가 불필요해졌다 — 파셋·대시보드가
    # 항상 exact 이고(facetCounts()/renderDash() 가 매 요청 응답에서 직접 읽음), 정렬 3종은
    # 항상 활성, 필터 유무와 무관하게 건수를 숨기지 않는다(파일 상단 [서버 canonical
    # search] 주석 참조).

    def test_deeplink_s1_hidepager_contracts_unchanged_by_025(self):
        """[§7 회귀] 025 이후 서버 canonical search 전환도 PR-0 딥링크(exitDeepLinkMode
        3회 호출)·S1 토글(exitSimilarMode 2회 호출)·hidePager(pnav 포함 은닉) 계약을
        훼손하지 않았는지 재확인한다(기존 계약과 동일 수치 — 회귀 0)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        wire_fn = js_src[js_src.index("function wire() {"):js_src.index("function fetchSearch(page)")]
        self.assertEqual(wire_fn.count("exitDeepLinkMode();"), 3)
        self.assertEqual(wire_fn.count("exitSimilarMode();"), 2)
        hidepager_fn = js_src[js_src.index("function hidePager() {"):]
        hidepager_fn = hidepager_fn[:hidepager_fn.index("\n  }\n") + 4]
        self.assertIn("if (pagerTopEl) pagerTopEl.hidden = true;", hidepager_fn)
        self.assertIn("if (pagerBottomEl) pagerBottomEl.hidden = true;", hidepager_fn)
        self.assertIn("if (pnavEl) pnavEl.hidden = true;", hidepager_fn)

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

    # ── PR-0: /findings/?finding_id=finding-<24hex> 딥링크 ──────────────────────────
    def test_deeplink_template_untouched(self):
        """§8 — 템플릿 최소 변경 원칙: 딥링크는 findings.js 단독 구현이라
        findings.html 소스 자체엔 새 마크업/id 가 없어야 한다(안내 바는 JS 가
        런타임에 DOM 삽입)."""
        tmpl_src = (WEB_DIR / "templates" / "findings.html").read_text(encoding="utf-8")
        self.assertNotIn("finding_id", tmpl_src)
        self.assertNotIn("fnd-deeplink", tmpl_src)

    def test_deeplink_finding_id_regex_matches_stable_hash_format(self):
        """grm_findings.py:706 finding_id = "finding-" + stable_hash(...)[:24] 는
        sha256 hexdigest 앞 24자(항상 소문자 hex)다 — findings.js 의 검증 정규식이
        정확히 이 형식과 일치해야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('var FINDING_ID_RE = /^finding-[0-9a-f]{24}$/;', js_src)
        self.assertIn('var DEEP_LINK_PARAM = "finding_id";', js_src)

    def test_deeplink_invalid_format_shortcircuits_without_fetch(self):
        """[①형식 검증] resolveDeepLink() 는 isValidFindingId() 가 거짓이면 어떤
        fetch 함수도 호출하지 않고 곧장 notfound 로 확정해야 한다(§1 — fetch 없이
        즉시 '찾을 수 없음'). [서버 canonical search] 단건 조회는 이제 findings_document
        RPC 1회(구버전 3단계 FIELDS 폴백 fetchDeepLinkFiltered() 대체) — 경계 anchor 도
        그에 맞춰 갱신한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function resolveDeepLink(id) {"):]
        invalid_branch = fn[:fn.index("fetchDocument(")]
        self.assertIn("if (!isValidFindingId(id)) {", invalid_branch)
        self.assertIn('deepLinkStatus = "notfound";', invalid_branch)
        self.assertIn("maybeFinishInit();", invalid_branch)
        self.assertNotIn("fetch", invalid_branch.split("if (!isValidFindingId(id)) {")[1].split("}")[0])

    # [서버 canonical search] fetchDeepLinkFiltered()/fetchFindingsFiltered() 의 3단계
    # FIELDS 폴백 체인·raw_signal_id 2차 조회·명시 limit=200 은 사라졌다 — findings_document
    # RPC(026) 1회 왕복이 "단건 조회 → 소속 문서 전체 조회 → 정렬"을 서버(SQL) 안에서 전부
    # 처리하고 완결된 문서 하나를 돌려준다(파일 상단 [서버 canonical search]·[딥링크] 주석).
    # 클라이언트는 더 이상 select 필드셋을 협상하거나 명시 limit 을 붙일 필요가 없다.

    def test_deeplink_renders_via_builddoccard_reuse_no_client_grouping(self):
        """[③렌더] findings_document RPC 가 이미 문서 단위로 묶어 보낸 deepLinkDocRows 는
        클라이언트 재그룹핑 없이 기존 buildCard() 기반 buildDocCard() 를 그대로 재사용해
        문서 카드 1장으로 렌더해야 한다(신규 렌더러 금지 — 종전 groupByDocument() 클라이언트
        그룹핑은 서버 canonical search 전환으로 사라졌다: 서버가 이미 문서 단위 배열을
        보내므로 재그룹핑할 대상 자체가 없다)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderDeepLinkDoc() {"):]
        fn = fn[:fn.index("var finalized = false;")]
        self.assertIn('var doc = buildDocCard(deepLinkDocRows, "");', fn)
        self.assertNotIn("groupByDocument", fn)

    def test_deeplink_independent_of_pagination(self):
        """[먼 페이지 대상] 딥링크 found 모드는 페이지네이션과 완전히 무관하게
        단독 렌더한다 — renderDeepLinkDoc() 는 페이저를 숨기고(hidePager()) 페이지
        번호/goToPage 를 전혀 참조하지 않으며, maybeFinishInit() 은 found 상태면
        goToPage 를 아예 호출하지 않고 즉시 return 해야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        render_fn = js_src[js_src.index("function renderDeepLinkDoc() {"):]
        render_fn = render_fn[:render_fn.index("\n  }\n") + 4]
        self.assertIn("hidePager();", render_fn)
        self.assertNotIn("goToPage", render_fn)
        self.assertNotIn("currentPage", render_fn)
        finish_fn = js_src[js_src.index("function maybeFinishInit() {"):]
        finish_fn = finish_fn[:finish_fn.index("\n  }\n") + 4]
        found_branch = finish_fn[finish_fn.index('deepLinkStatus === "found"'):]
        found_branch = found_branch[:found_branch.index("}") + 1]
        self.assertIn("renderDeepLinkDoc();", found_branch)
        self.assertIn("return;", found_branch)
        self.assertNotIn("goToPage", found_branch)

    def test_deeplink_auto_expands_collapsed_card_and_more_wrap(self):
        """[접힌 6번째 이후 observation 자동 펼침] revealAndFocusTarget() 은 대상이
        "N건 모두 보기" 뒤(.fnd-doc-obs-more, hidden)에 숨어 있으면 먼저 그 wrap 을
        펼치고, 카드 자체가 기본 접힘(.fnd-collapsed, 3줄 요약)이면 그 클래스도
        제거해 본문 전체가 보이게 해야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function revealAndFocusTarget(built, targetId) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('targetEl.closest(".fnd-doc-obs-more")', fn)
        self.assertIn("if (moreWrap && moreWrap.hidden) {", fn)
        self.assertIn("moreWrap.hidden = false;", fn)
        self.assertIn('if (targetEl.classList.contains("fnd-collapsed")) {', fn)
        self.assertIn('targetEl.classList.remove("fnd-collapsed");', fn)

    def test_deeplink_finalize_has_settimeout_fallback_and_order(self):
        """[자동 도달 견고성] renderDeepLinkDoc() 의 마무리는 rAF 단독이 아니라
        setTimeout 폴백과 이중 스케줄(finalized 가드 1회 실행)이어야 한다 — rAF 는
        백그라운드 탭(공유 링크 새 탭 열기)·헤드리스 환경에서 유예/미발화된다(프리뷰
        실측). 또한 clamp 측정(moreBtn 표시/제거)은 hidden 요소에서 scrollHeight 가
        0이 되므로, "N건 모두 보기" 래퍼 펼침이 측정보다 먼저 와야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderDeepLinkDoc() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("var finalized = false;", fn)
        self.assertIn("if (finalized) return;", fn)
        self.assertIn("requestAnimationFrame(finalizeDeepLinkDoc);", fn)
        self.assertIn("setTimeout(finalizeDeepLinkDoc, 120);", fn)
        # 순서 계약: 래퍼 펼침 → clamp 측정 → revealAndFocusTarget.
        unhide_pos = fn.index("moreWrap.hidden = false;")
        measure_pos = fn.index("scrollHeight - item.textEl.clientHeight")
        reveal_pos = fn.index("revealAndFocusTarget(doc.built, targetId);")
        self.assertLess(unhide_pos, measure_pos)
        self.assertLess(measure_pos, reveal_pos)

    def test_deeplink_scroll_offset_focus_and_transient_highlight(self):
        """[자동 도달] sticky 툴바 오프셋 보정 스크롤(goToPage() 의 기존 공식과 동일
        패턴)+tabindex=-1 focus+일시 강조(2초 후 인라인 스타일 제거, grm.css 불가침)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function revealAndFocusTarget(built, targetId) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('document.getElementById("fnd-tools")', fn)
        self.assertIn("stickyBottom", fn)
        self.assertIn('targetEl.setAttribute("tabindex", "-1");', fn)
        self.assertIn("targetEl.focus({ preventScroll: true });", fn)
        self.assertIn('targetEl.style.outline = "2px solid var(--coral)";', fn)
        self.assertIn("setTimeout(function () {", fn)
        self.assertIn('targetEl.style.outline = "";', fn)
        self.assertIn(", 2000);", fn)
        # goToPage() 의 기존 스크롤 오프셋 보정 공식(§5 재사용 요구)과 동일 계산식.
        goto_fn = js_src[js_src.index("function goToPage(n) {"):]
        goto_fn = goto_fn[:goto_fn.index("function goToPageFromPager")]
        self.assertIn("getBoundingClientRect().bottom : 0;", goto_fn)
        self.assertIn("getBoundingClientRect().bottom : 0;", fn)

    def test_deeplink_stable_dom_id_present_unconditionally(self):
        """[안정 DOM id] buildCard() 는 딥링크 모드 여부와 무관하게(일반 모드
        포함) 항상 id="f-<finding_id>" 를 부여해야 한다 — 딥링크 조건부 게이트가
        없어야 무해하게 항상 존재한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildCard(row, query) {"):]
        fn = fn[:fn.index("\n    if (row.review_status === \"needs_review\") card.classList.add")]
        self.assertIn('if (row.finding_id) card.id = "f-" + row.finding_id;', fn)
        self.assertNotIn("deepLink", fn)  # 딥링크 상태를 참조하는 조건부가 아니다.

    def test_deeplink_uniform_notfound_for_invalid_missing_and_private(self):
        """[§7 불가침] 형식오류·미존재·비공개(RLS 차단으로 빈 결과) 3가지 경로가
        전부 동일한 deepLinkStatus="notfound" 로 수렴해야 한다 — 존재 여부 정보를
        구분해 누설하는 별도 상태/문구가 없어야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        resolve_fn = js_src[js_src.index("function resolveDeepLink(id) {"):]
        resolve_fn = resolve_fn[:resolve_fn.index("\n  }\n") + 4]
        self.assertEqual(resolve_fn.count('deepLinkStatus = "notfound";'), 3,
                          "형식오류·빈결과·fetch실패(catch) 3개 경로 모두 동일한 notfound 대입이어야 함")
        # 배너 문구도 단일 — 사유별 분기 텍스트가 없다(showDeepLinkNotFoundBanner 는
        # textContent 대입이 정확히 1회뿐이어야 한다).
        banner_fn = js_src[js_src.index("function showDeepLinkNotFoundBanner() {"):]
        banner_fn = banner_fn[:banner_fn.index("\n  }\n") + 4]
        self.assertEqual(banner_fn.count(".textContent ="), 1)

    def test_deeplink_exits_on_filter_search_sort_page_interaction(self):
        """[§4] 필터·검색·정렬·페이지 조작 시 exitDeepLinkMode() 가 호출돼 딥링크
        모드를 종료해야 한다 — wire() 의 셀렉트 5개·정렬·검색어 핸들러, 적용 필터
        칩 제거(clearActiveFilter/clearAllFilters), 대시보드 클릭(toggleXFilter),
        페이지네이션(goToPageFromPager) 전부가 진입점이다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        wire_fn = js_src[js_src.index("function wire() {"):js_src.index("function fetchSearch(page)")]
        self.assertEqual(wire_fn.count("exitDeepLinkMode();"), 3,
                          "셀렉트·정렬·검색어 3개 핸들러 모두 exitDeepLinkMode() 호출해야 함")
        for fn_name in ("function clearActiveFilter(key) {", "function clearAllFilters() {",
                         "function toggleCategoryFilter(code) {", "function toggleMonthFilter(month) {",
                         "function toggleFirmFilter(name) {", "function goToPageFromPager(n) {"):
            fn = js_src[js_src.index(fn_name):]
            fn = fn[:fn.index("\n  }\n") + 4]
            self.assertIn("exitDeepLinkMode();", fn, f"{fn_name} 이 exitDeepLinkMode() 를 호출하지 않음")

    def test_deeplink_exit_is_noop_when_no_active_param(self):
        """exitDeepLinkMode() 는 deepLinkParam 이 없으면(일반 모드) 즉시 return —
        일반 /findings/ 경로에서 필터 조작 시 부작용이 전혀 없어야 한다(§7 회귀 0)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function exitDeepLinkMode() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (!deepLinkParam) return;", fn)

    def test_deeplink_normal_path_unaffected_when_param_absent(self):
        """[일반 경로 회귀] finding_id 파라미터가 없으면 deepLinkPending 은 처음부터
        false 로 시작해(requestedFindingId 가 없을 때만 진입하는 if 블록 밖) 신규
        코드가 초기화 흐름에 개입하지 않는다. [서버 canonical search] 페이지 복원은
        이제 첫 fetchSearch() 호출 이전에 확정되므로, maybeFinishInit() 의 비-found
        분기는 별도 goToPage 보정 없이 render() 하나로 귀결된다(구버전의
        goToPage(readPageFromUrl()) 재호출 왕복이 사라졌다)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var deepLinkPending = false;", js_src)
        tail = js_src[js_src.index("var requestedFindingId = getDeepLinkParam();"):]
        guarded = tail[:tail.index("fetchSearch(currentPage)")]
        self.assertIn("if (requestedFindingId) {", guarded)
        self.assertIn("deepLinkPending = true;", guarded)
        finish_fn = js_src[js_src.index("function maybeFinishInit() {"):]
        finish_fn = finish_fn[:finish_fn.index("\n  }\n") + 4]
        self.assertIn("render();", finish_fn)
        self.assertNotIn("goToPage(readPageFromUrl())", finish_fn)
        # 첫 응답 성공 콜백은 LAST 대입 후 rowsReady=true, maybeFinishInit() 로 위임한다
        # ("if (initToken !== navToken) return;" 는 초기화 fetch 콜백에만 있어 goToPage()
        # 의 동형 "LAST = data;" 대입(navToken 가드)과 구분되는 유일한 앵커다).
        last_then = js_src[js_src.index("if (initToken !== navToken) return;"):]
        last_then = last_then[:last_then.index(".catch(function () {")]
        self.assertIn("rowsReady = true;", last_then)
        self.assertIn("maybeFinishInit();", last_then)
        self.assertNotIn("goToPage(initialPage)", last_then)

    def test_deeplink_list_fetch_failure_does_not_override_found_render(self):
        """목록 fetch 가 실패해도 이미 확정된 딥링크 단건 렌더(found)는 에러 상태로
        덮어써지지 않아야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        catch_block = js_src[js_src.index('.catch(function () {\n      // [PR-0 딥링크]'):]
        catch_block = catch_block[:catch_block.index("})();")]
        self.assertIn('if (deepLinkStatus !== "found") showState("error");', catch_block)

    def test_deeplink_url_sync_preserves_finding_id_param(self):
        """[§6] syncStateToUrl() 은 새 URLSearchParams 를 처음부터 만들기 때문에
        별도 보존 로직이 없으면 finding_id 를 조용히 지운다 — deepLinkParam 이
        활성인 동안 그 값을 그대로 params 에 반영해야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function syncStateToUrl() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (deepLinkParam) params.set(DEEP_LINK_PARAM, deepLinkParam);", fn)

    def test_deeplink_found_banner_link_strips_param(self):
        """[§4] 안내 바의 "전체 목록 보기" 링크는 finding_id 파라미터를 제거한
        URL 이어야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function urlWithoutDeepLink() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("params.delete(DEEP_LINK_PARAM);", fn)
        banner_fn = js_src[js_src.index("function showDeepLinkFoundBanner() {"):]
        banner_fn = banner_fn[:banner_fn.index("\n  }\n") + 4]
        self.assertIn("link.href = urlWithoutDeepLink();", banner_fn)
        self.assertIn('"전체 목록 보기"', js_src)

    def test_deeplink_no_innerhtml_data_injection(self):
        """딥링크 신규 함수들도 기존 XSS 계약(innerHTML 데이터 삽입 금지, 파일 상단
        주석 계약)을 따라야 한다 — 전부 createElement/textContent 로만 DOM 을
        구성한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        deeplink_block = js_src[js_src.index("function isValidFindingId(id) {"):js_src.index("function render() {")]
        self.assertNotIn("innerHTML", deeplink_block)

    # ── FIND-1 S1: 유사 문구 검색(렉시컬, 018_findings_similar_lexical.sql RPC) ─────────
    def test_similar_toggle_label_is_honest_lexical_not_semantic(self):
        """[정직 표기] UI 명칭은 반드시 "유사 문구 검색"이어야 하고, "의미검색"/"시맨틱"
        표현은 findings.js 어디에도 있으면 안 된다 — trigram+FTS 렉시컬이지 의미 매칭이
        아니다(018 마이그레이션 주석과 동일 원칙)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('btn.textContent = "유사 문구 검색";', js_src)
        self.assertNotIn("의미검색", js_src)
        self.assertNotIn("시맨틱", js_src)

    def test_similar_toggle_injected_next_to_search_input_no_template_change(self):
        """[템플릿 최소 변경] 토글은 findings.html 에 자리가 없고(§ 템플릿 무변경),
        findings.js 가 #fnd-q(검색창) 옆에 런타임 DOM 삽입한다(PR-0 딥링크 배너와
        동일 관례)."""
        tmpl_src = (WEB_DIR / "templates" / "findings.html").read_text(encoding="utf-8")
        self.assertNotIn("fnd-similar", tmpl_src)
        self.assertNotIn("유사 문구 검색", tmpl_src)
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarToggle() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('qInput.parentNode.insertBefore(btn, countEl || null);', fn)
        self.assertIn('btn.id = "fnd-similar-toggle";', fn)
        # wire() 가 초기화 시점에 1회 호출한다.
        wire_fn = js_src[js_src.index("function wire() {"):js_src.index("function fetchSearch(page)")]
        self.assertIn("buildSimilarToggle();", wire_fn)

    def test_similar_rpc_call_contract(self):
        """[RPC 계약] POST {url}/rest/v1/rpc/findings_similar, body={p_query,p_limit}."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function fetchSimilarItems(q, limit) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('"/rest/v1/rpc/findings_similar"', fn)
        self.assertIn('method: "POST"', fn)
        self.assertIn('JSON.stringify({ p_query: q, p_limit: limit })', fn)
        self.assertIn("apikey: key", fn)
        self.assertIn('Authorization: "Bearer " + key', fn)

    def test_similar_min_query_length_and_default_limit(self):
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var SIMILAR_MIN_QUERY_LEN = 2;", js_src)
        self.assertIn("var SIMILAR_LIMIT = 20;", js_src)
        fn = js_src[js_src.index("function runSimilarSearch() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (q.length < SIMILAR_MIN_QUERY_LEN) {", fn)
        self.assertIn("fetchSimilarItems(q, SIMILAR_LIMIT)", fn)

    def test_similar_silent_fallback_on_failure_and_empty_items(self):
        """[§5 폴백, 중요] RPC 실패(404 미적용 포함)·빈 items 배열 둘 다 조용히
        goToPage(1)(기존 키워드 검색, 토글 OFF 상태와 동일 동작)로 귀결돼야 한다 —
        throw 재발생·console.error·사용자 노출 에러 상태가 없어야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function runSimilarSearch() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertNotIn("console.error", fn)
        self.assertNotIn("showState(\"error\")", fn)
        empty_branch = fn[fn.index("if (!items.length) {"):]
        empty_branch = empty_branch[:empty_branch.index("}") + 1]
        self.assertIn("goToPage(1);", empty_branch)
        catch_branch = fn[fn.index(".catch(function () {"):]
        catch_branch = catch_branch[:catch_branch.index("});") + 3]
        self.assertIn("goToPage(1);", catch_branch)
        self.assertNotIn("throw", catch_branch)

    def test_similar_reuses_buildcard_no_new_renderer(self):
        """[§2] 기존 buildCard(row, query) 렌더러를 그대로 재사용해야 한다(신규
        렌더러 금지) — 문서 그룹핑 없이 finding 단위 카드 목록(서버 정렬 순서 유지,
        재정렬 없음)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderSimilarResults(items) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('buildCard(row, "")', fn)
        self.assertNotIn("groupByDocument", fn)
        self.assertNotIn(".sort(", fn)  # 서버 정렬 순서 그대로 — 클라이언트 재정렬 금지
        self.assertIn("hidePager();", fn)  # 대시보드/페이저와 무관(딥링크 단독렌더와 동형)

    def test_similar_text_field_mapped_without_breaking_original_toggle(self):
        """[매핑] RPC 의 text(원문/국문 구분 없는 단일 텍스트)는 finding_text_ko 에만
        채우고 finding_text 는 비운다 — appendOrigAndNote() 가 row.finding_text 없으면
        조용히 no-op 이라("원문 보기" 접기가 나타나지 않음) 카드가 깨지지 않는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function mapSimilarItemToRow(item) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("finding_text_ko: item.text || \"\",", fn)
        self.assertIn('finding_text: "",', fn)
        orig_fn = js_src[js_src.index("function appendOrigAndNote(extra, row, query) {"):]
        orig_fn = orig_fn[:orig_fn.index("\n  }\n") + 4]
        self.assertIn("if (!ko || !row.finding_text) return;", orig_fn)

    def test_hide_pager_also_hides_sticky_mininav(self):
        """[단독 렌더 모드 공통] hidePager() 는 상/하단 페이저 + sticky 미니 내비
        (#fnd-pnav, PR#231) 셋을 함께 숨겨야 한다 — pnav 를 빼먹으면 딥링크(PR-0)·
        유사검색(S1)처럼 페이지 개념이 없는 단독 렌더 모드에서 sticky 툴바에 ‹ ›
        화살표만 남는다(프리뷰 실측 발견 결함). 두 모드가 공유하는 단일 진입점이라
        여기서 한 번만 막는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function hidePager() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (pagerTopEl) pagerTopEl.hidden = true;", fn)
        self.assertIn("if (pagerBottomEl) pagerBottomEl.hidden = true;", fn)
        self.assertIn("if (pnavEl) pnavEl.hidden = true;", fn)
        # 두 단독 렌더 모드가 실제로 이 진입점을 쓰는지(계약의 반대편).
        for mode_fn in ("function renderDeepLinkDoc() {", "function renderSimilarResults(items) {"):
            body = js_src[js_src.index(mode_fn):]
            body = body[:body.index("\n  }\n") + 4]
            self.assertIn("hidePager();", body)

    def test_similar_adapter_keeps_trust_badges(self):
        """[신뢰도 배지 M13] 어댑터가 evidence_level/review_status 를 넘겨야 유사검색
        결과에서도 Evidence 등급·"검토 필요" 경계가 목록 모드와 동일하게 보인다 —
        누락 시 buildCard() 의 두 배지 분기가 조용히 죽는다(컨트롤타워 검수 발견 결함).
        018 RPC 도 이 두 필드를 반환한다(tests/test_findings_similar_lexical.py 가 고정)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function mapSimilarItemToRow(item) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('evidence_level: item.evidence_level || "",', fn)
        self.assertIn('review_status: item.review_status || "",', fn)
        # buildCard() 가 실제로 이 두 필드로 배지를 그리는지(계약의 반대편) 확인.
        card_fn = js_src[js_src.index("  function buildCard(row, query) {"):]
        card_fn = card_fn[:card_fn.index("\n  }\n") + 4]
        self.assertIn("EVIDENCE_LABEL[row.evidence_level]", card_fn)
        self.assertIn('if (row.review_status === "needs_review") {', card_fn)

    def test_similar_dup_badge_only_when_dup_findings_gt_1(self):
        """[중복 배지] dup_findings>1 인 카드에만 "동일 문구 N개 문서"(N=dup_documents)
        배지를 부착한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function appendSimilarDupBadge(card, item) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (!item || !(Number(item.dup_findings) > 1)) return;", fn)
        self.assertIn('badge.textContent = "동일 문구 " + (item.dup_documents || 0) + "개 문서";', fn)

    def test_similar_deeplink_landing_reuses_pr0_param(self):
        """[딥링크 연계 §4] 각 결과 카드는 PR-0 딥링크(/findings/?finding_id=<id>)로
        해당 문서에 도달할 수 있어야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function similarItemDeepLinkUrl(id) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("location.pathname + \"?\" + DEEP_LINK_PARAM + \"=\" + encodeURIComponent(id)", fn)
        link_fn = js_src[js_src.index("function appendSimilarDeepLink(card, findingId) {"):]
        link_fn = link_fn[:link_fn.index("\n  }\n") + 4]
        self.assertIn("similarItemDeepLinkUrl(findingId)", link_fn)
        render_fn = js_src[js_src.index("function renderSimilarResults(items) {"):]
        render_fn = render_fn[:render_fn.index("\n  }\n") + 4]
        self.assertIn("appendSimilarDeepLink(built.card, item.finding_id);", render_fn)

    def test_similar_toggle_off_by_default_normal_path_unaffected(self):
        """[§7 회귀 0] similarMode 는 기본 false — 토글을 누르지 않으면 기존
        /findings/ 동작(목록·페이지네이션·대시보드·필터·딥링크)이 완전히 동일해야
        한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("var similarMode = false;", js_src)
        qinput_fn = js_src[js_src.index("if (qInput) {\n      qInput.addEventListener(\"input\""):]
        qinput_fn = qinput_fn[:qinput_fn.index("\n    }\n") + 6]
        self.assertIn("if (similarMode) { runSimilarSearch(); return; }", qinput_fn)
        self.assertIn("goToPage(1);", qinput_fn)

    def test_similar_mode_exits_on_filter_sort_page_interaction(self):
        """[§6 모드 이탈] 필터·정렬·페이지 조작 시 exitSimilarMode() 가 호출돼 유사검색
        모드를 끄고 목록 모드로 복귀해야 한다 — exitDeepLinkMode() 와 동일한 진입점
        전부에 나란히 배선된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        wire_fn = js_src[js_src.index("function wire() {"):js_src.index("function fetchSearch(page)")]
        self.assertEqual(wire_fn.count("exitSimilarMode();"), 2,
                          "셀렉트·정렬 2개 핸들러 모두 exitSimilarMode() 호출해야 함")
        for fn_name in ("function clearActiveFilter(key) {", "function clearAllFilters() {",
                         "function toggleCategoryFilter(code) {", "function toggleMonthFilter(month) {",
                         "function toggleFirmFilter(name) {", "function goToPageFromPager(n) {"):
            fn = js_src[js_src.index(fn_name):]
            fn = fn[:fn.index("\n  }\n") + 4]
            self.assertIn("exitSimilarMode();", fn, f"{fn_name} 이 exitSimilarMode() 를 호출하지 않음")

    def test_similar_mode_is_noop_when_already_off(self):
        """exitSimilarMode() 는 similarMode 가 이미 false 면 즉시 return — 일반 모드에서
        필터 조작 시 부작용이 전혀 없어야 한다(exitDeepLinkMode() 와 동형 관례)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function exitSimilarMode() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (!similarMode) return;", fn)

    def test_similar_toggle_click_exits_deeplink_mode(self):
        """[§7] 딥링크 모드 진입 시 유사검색 모드는 꺼진 상태여야 자연스럽다 — 반대
        방향으로, 토글 클릭은 exitDeepLinkMode() 를 호출해 딥링크 모드를 정리한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarToggle() {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("exitDeepLinkMode();", fn)
        self.assertIn("setSimilarMode(!similarMode);", fn)

    def test_similar_pr0_deeplink_contract_untouched(self):
        """[§7 불가침] PR-0 딥링크 계약(syncStateToUrl 의 finding_id 보존·
        exitDeepLinkMode 의 no-op 가드)이 S1 추가로 훼손되지 않았는지 재확인한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        sync_fn = js_src[js_src.index("function syncStateToUrl() {"):]
        sync_fn = sync_fn[:sync_fn.index("\n  }\n") + 4]
        self.assertIn("if (deepLinkParam) params.set(DEEP_LINK_PARAM, deepLinkParam);", sync_fn)
        exit_fn = js_src[js_src.index("function exitDeepLinkMode() {"):]
        exit_fn = exit_fn[:exit_fn.index("\n  }\n") + 4]
        self.assertIn("if (!deepLinkParam) return;", exit_fn)

    def test_similar_no_innerhtml_data_injection(self):
        """S1 신규 함수들도 기존 XSS 계약(innerHTML 데이터 삽입 금지)을 따른다 —
        전부 createElement/textContent 로만 DOM 을 구성한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        similar_block = js_src[js_src.index("function mapSimilarItemToRow(item) {"):js_src.index("function render() {")]
        self.assertNotIn("innerHTML", similar_block)

    # ── FIND-1 "이 지적과 유사한 사례"(렉시컬, 021_findings_similar_to.sql RPC) ──────────
    # A/B 평가(2026-07-15, 021 마이그레이션 주석)로 임베딩(S2)이 S1 렉시컬을 못 이겨
    # S2 웹 공개가 중단됐다 — 이 버튼은 021(finding_id 기준)을 소비하고 019 의
    # findings_similar_by_id(임베딩) 는 절대 호출하지 않는다(inert 유지 계약).
    def test_similar_to_rpc_call_contract(self):
        """[RPC 계약] POST {url}/rest/v1/rpc/findings_similar_to, body={p_finding_id,p_limit}."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function fetchSimilarTo(findingId, limit) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('"/rest/v1/rpc/findings_similar_to"', fn)
        self.assertIn('method: "POST"', fn)
        self.assertIn("JSON.stringify({ p_finding_id: findingId, p_limit: limit })", fn)
        self.assertIn("apikey: key", fn)
        self.assertIn('Authorization: "Bearer " + key', fn)

    def test_similar_to_never_calls_embedding_rpc(self):
        """[평가 결과 반영, 핵심 계약] S2 임베딩(019 findings_similar_by_id) 는 A/B 평가
        (021 마이그레이션 주석: nDCG CI 가 0 을 포함=동률 또는 유의하게 열세)로 웹 공개가
        중단됐다 — findings.js 소스 어디에도 그 RPC 이름이 있으면 안 된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertNotIn("findings_similar_by_id", js_src)

    def test_similar_to_on_demand_no_fetch_before_click(self):
        """[on-demand] 카드 89개 전체에 자동 조회하지 않는다 — buildSimilarCasesControl()
        은 버튼을 만들 때 fetchSimilarTo 를 호출하지 않고, click 리스너 안에서만 호출한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarCasesControl(row) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        before_listener = fn[:fn.index("addEventListener")]
        self.assertNotIn("fetchSimilarTo(", before_listener)
        self.assertIn("fetchSimilarTo(findingId, SIMILAR_TO_LIMIT)", fn)
        self.assertIn("var SIMILAR_TO_LIMIT = 5;", js_src)

    def test_similar_to_cached_after_first_fetch_no_refetch(self):
        """[1회 fetch 후 캐시] fetched 플래그가 true 로 굳으면 이후 클릭은 토글만 하고
        fetchSimilarTo 를 다시 호출하지 않는다(재요청 금지). F-08 수리 후 fetched=true 는
        성공(then) 안에서만 세워진다 — 클릭 시점(fetch 호출 전)에는 아직 세우지 않는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarCasesControl(row) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("var fetched = false;", fn)
        self.assertIn("if (!opening || fetched) return;", fn)
        before_fetch_call = fn[:fn.index("fetchSimilarTo(findingId, SIMILAR_TO_LIMIT)")]
        self.assertNotIn("fetched = true;", before_fetch_call)  # [F-08] 클릭 시점 선(先)확정 금지
        then_branch = fn[fn.index(".then(function (data) {"):fn.index(".catch(function () {")]
        self.assertIn("fetched = true;", then_branch)

    def test_similar_to_toggle_collapses_on_second_click(self):
        """[토글] block.hidden 뒤집기로 펼침/접힘을 표현하고 aria-expanded 를 동기화한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarCasesControl(row) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("var opening = block.hidden;", fn)
        self.assertIn("block.hidden = !opening;", fn)
        self.assertIn('btn.setAttribute("aria-expanded", opening ? "true" : "false");', fn)

    def test_similar_to_finding_id_missing_skips_button(self):
        """[방어] finding_id 없는 행은 버튼 자체를 만들지 않는다(evidence_url 조건부와
        동형 관례) — 카드 렌더가 절대 깨지지 않는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarCasesControl(row) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (!findingId) return null;", fn)

    def test_similar_to_silent_failure_and_state_wording(self):
        """[§3 조용한 폴백] 실패(.catch)도 0건과 동일하게 renderSimilarToState(block, [])
        로 수렴한다 — throw 재발생·console.error 없음. 로딩/0건 문구도 명세와 정확히
        일치해야 한다(RPC 미적용(404) 상태에서도 페이지가 정상 동작해야 하는 계약)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarCasesControl(row) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertNotIn("console.error", fn)
        catch_branch = fn[fn.index(".catch(function () {"):]
        catch_branch = catch_branch[:catch_branch.index("});") + 3]
        self.assertIn("renderSimilarToState(block, []);", catch_branch)
        self.assertNotIn("throw", catch_branch)
        state_fn = js_src[js_src.index("function renderSimilarToState(block, items) {"):]
        state_fn = state_fn[:state_fn.index("\n  }\n") + 4]
        self.assertIn('"불러오는 중…"', state_fn)
        self.assertIn('"유사 사례를 찾지 못했습니다"', state_fn)

    def test_f08_similar_cases_retry_allowed_after_transient_failure(self):
        """[F-08] "유사 사례" 재시도 불가 수리 — fetched=true 는 성공(then)에서만 세워
        캐시를 확정하고, catch 에서는 false 로 되돌려 다음 클릭이 재시도하게 한다(일시
        네트워크 오류·404(RPC 미존재) 후에도 새로고침 없이 재시도 가능). catch 의 사용자
        표시는 종전과 동일한 조용한 폴백(콘솔 로그·throw 없음)이어야 한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarCasesControl(row) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        then_branch = fn[fn.index(".then(function (data) {"):fn.index(".catch(function () {")]
        catch_branch = fn[fn.index(".catch(function () {"):]
        catch_branch = catch_branch[:catch_branch.index("});") + 3]
        self.assertIn("fetched = true;", then_branch)  # 성공 시에만 캐시 확정
        self.assertIn("fetched = false;", catch_branch)  # 실패 시 재시도 허용
        self.assertNotIn("console.error", catch_branch)
        self.assertNotIn("throw", catch_branch)
        self.assertIn("renderSimilarToState(block, []);", catch_branch)

    def test_similar_to_dup_badge_only_when_dup_findings_gt_1(self):
        """[중복 배지] dup_findings>1 인 항목에만 "동일 문구 N개 문서"(N=dup_documents)
        배지를 부착한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarToItem(item) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (Number(item.dup_findings) > 1) {", fn)
        self.assertIn('"동일 문구 " + (item.dup_documents || 0) + "개 문서"', fn)

    def test_similar_to_needs_review_visual_distinction_inline_no_css_edit(self):
        """[검토 필요 시각 경계] .fnd-card--review 관례(왼쪽 3px coral 보더)를 grm.css
        를 건드리지 않고 인라인 스타일로 재현한다(§7 grm.css 불가침)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarToItem(item) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('item.review_status === "needs_review"', fn)
        self.assertIn("border-left:3px solid var(--coral)", fn)
        css_src = (WEB_DIR / "templates" / "findings.html").read_text(encoding="utf-8")
        self.assertNotIn("fnd-simto", css_src)  # 템플릿/CSS 신규 규칙 없음(전부 인라인)

    def test_similar_to_deeplink_landing_reuses_pr0_param(self):
        """[딥링크 착지] 각 항목은 S1 이 이미 만든 similarItemDeepLinkUrl() (PR-0 재사용
        헬퍼)로 해당 문서에 도달한다 — 신규 URL 스킴을 만들지 않는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildSimilarToItem(item) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("similarItemDeepLinkUrl(item.finding_id)", fn)
        self.assertIn('link.textContent = "해당 문서 보기";', fn)

    def test_similar_to_no_client_side_resort_or_refilter(self):
        """[서버 순서 그대로] renderSimilarToState() 는 items 를 재정렬·재필터하지 않고
        반환 순서 그대로 forEach 렌더한다(021 RPC 가 정렬·중복 붕괴를 전부 서버에서 처리)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderSimilarToState(block, items) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertNotIn(".sort(", fn)
        self.assertNotIn(".filter(", fn)
        self.assertIn("items.forEach(function (item) {", fn)

    def test_similar_to_wired_next_to_more_toggle_no_conflict(self):
        """[진입점] buildCard() 는 evidence_url 링크 뒤·moreBtn("자세히 보기") 앞에
        "유사 사례" 버튼을 actions 행에 나란히 붙이고, 펼침 블록은 actions 뒤에 붙인다 —
        finding_id 없는 방어적 행은 simCases 가 null 이라 아무것도 추가되지 않는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("  function buildCard(row, query) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("var simCases = buildSimilarCasesControl(row);", fn)
        self.assertIn("if (simCases) actions.appendChild(simCases.btn);", fn)
        self.assertIn("if (simCases) card.appendChild(simCases.block);", fn)
        self.assertLess(fn.index("simCases.btn"), fn.index("var moreBtn = buildMoreToggle(card);"))
        self.assertLess(fn.index("card.appendChild(actions);"), fn.index("simCases.block"))

    def test_similar_to_reused_across_all_buildcard_render_paths(self):
        """[§4/§5 자연스러운 확산, 신규 렌더러 금지] buildCard() 를 재사용하는 모든 경로
        (일반 문서 카드·PR-0 딥링크 문서 카드·S1 유사검색 결과)에 신규 렌더러 없이 버튼이
        자동으로 함께 나타난다 — renderSimilarResults()/buildDocCard() 자체는 무변경이라야
        한다(둘 다 buildCard(row, ...) 를 그대로 호출)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        render_fn = js_src[js_src.index("function renderSimilarResults(items) {"):]
        render_fn = render_fn[:render_fn.index("\n  }\n") + 4]
        self.assertIn('buildCard(row, "")', render_fn)
        doc_fn = js_src[js_src.index("function buildDocCard(rows, query) {"):]
        doc_fn = doc_fn[:doc_fn.index("\n  }\n") + 4]
        self.assertEqual(doc_fn.count("buildCard(row, query)"), 2)

    def test_similar_to_regression_buildcard_return_shape_unchanged(self):
        """[§6 회귀 0] buildCard() 의 반환 계약({card,textEl,extraEl,moreBtn})은 신규
        기능 추가로 바뀌지 않는다 — render()/renderDeepLinkDoc() 등 기존 소비자가
        item.textEl/item.extraEl/item.moreBtn 을 그대로 읽는 계약을 깨면 안 된다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("  function buildCard(row, query) {"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn(
            "return { card: card, textEl: textEl, extraEl: extra, moreBtn: moreBtn };", fn
        )

    def test_similar_to_no_innerhtml_data_injection(self):
        """신규 함수들도 기존 XSS 계약(innerHTML 데이터 삽입 금지)을 따른다 — 전부
        createElement/textContent 로만 DOM 을 구성한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        block = js_src[
            js_src.index("function fetchSimilarTo(findingId, limit) {"):
            js_src.index("  function buildCard(row, query) {")
        ]
        self.assertNotIn("innerHTML", block)


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
        self.assertEqual(nav_m.group(1).count("<a "), 6)  # 모아보기·찾아보기·트렌드·자료실·용어사전·이용안내

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

    def test_headline_removed(self):
        """[헤드라인 제거 2026-07] "가장 많이 지적된 영역은…" 요약 + "연도별로 나눠 봐도…"
        일관성 문장을 제거했다 — 바로 아래 카테고리 순위·연도별 구성비가 시각적으로 이미
        보여줘 중복. 렌더 함수·연동 기계장치·셸 요소가 모두 없어야 한다(코드 주석은 예외)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        # 실행 코드에서 제거(주석은 제거 근거로 남을 수 있어 함수 정의·호출로만 판정).
        for gone in ("function buildHeadline", "function renderHeadline",
                     "function appendConsistencyLine", "function tryConsistencyLine",
                     "state.headline"):
            self.assertNotIn(gone, js_src, f"제거 대상이 남아 있음: {gone}")
        # 정적 셸에도 헤드라인 자리(<p class="tr-headline">)가 없어야 한다.
        self.assertNotIn('class="tr-headline"', self.html)

    def test_headline_has_no_disclosure_date_yoy(self):
        """[13차 정직화] published_date 는 공개일이라 전년 동기 대비 증감은 규제 추세가
        아니라 공개 배치 크기를 재는 지표다 — YoY 문장·계산을 통째로 제거했고, 같은 편향을
        갖는 '최다 업체' 문장도 헤드라인에서 뺐다(업체 순위는 읽는 법을 붙인 섹션에만)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        for gone in ("computeYoy", "shiftMonth", "전년 동기 대비",
                     "지적 건수가 가장 많은 업체는"):
            self.assertNotIn(gone, js_src, f"제거 대상이 남아 있음: {gone}")

    def test_composition_share_axis_on_every_count_chart(self):
        """[13차] 절대 건수만 보여 주던 차트에 전부 구성비(%)를 병기한다 — 카테고리 순위·
        연도별 공개량·소스 구성. 반올림은 공용 pctText() 하나로만(1% 미만이 '0%'로 뭉개져
        없는 것처럼 읽히지 않도록 10% 미만은 소수 1자리)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function pctText(part, whole)", js_src)
        self.assertIn('el("span", "tr-cat-share", pctText(entry.cnt, total))', js_src)
        self.assertIn('el("span", "tr-year-share", pctText(y.cnt, total))', js_src)
        self.assertIn('el("span", "tr-src-share", pctText(s.cnt, total))', js_src)
        # 카테고리 구성비 분모는 상위 10이 아니라 전체 카테고리 합이어야 한다.
        fn = js_src[js_src.index("function renderCategoryRanking(byAgencyCategory)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("var total = catTotal(all);", fn)
        self.assertIn("var cats = all.slice(0, 10);", fn)

    def test_evidence_grade_section_removed(self):
        """[13차] 증거 등급 구성 — 실데이터가 A 99% 이상 단일값이라 분포 차트로서 정보가
        없고, 등급 자체가 내부 QA 개념이다(트랙C 품질 기준: 내부개념 비노출). 셸·CSS·
        렌더 경로를 모두 제거했다."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        html_src = (WEB_DIR / "templates" / "trends.html").read_text(encoding="utf-8")
        for gone in ("tr-evidence", "renderEvidence", "EVIDENCE_ORDER", "by_evidence"):
            self.assertNotIn(gone, js_src, f"trends.js 에 잔존: {gone}")
        # 템플릿에서는 CSS 규칙 선언만 본다(jinja 주석엔 제거 근거가 남아 있고, 그 주석은
        # 렌더 출력에 실리지 않는다).
        for rule in (".tr-evidence", ".tr-bottom{"):
            self.assertNotIn(rule, html_src, f"trends.html 에 CSS 규칙 잔존: {rule}")
        # 렌더 출력(스코프 <style> 의 CSS 주석 포함)엔 흔적이 전혀 없어야 한다 —
        # CSS 주석은 사용자에게 그대로 전달되므로 제거된 UI 를 거기 남기지 않는다.
        for gone in ("tr-evidence", "tr-bottom", "증거 등급", "Evidence "):
            self.assertNotIn(gone, self.html, f"렌더 출력에 잔존: {gone}")

    def test_read_the_chart_note_on_every_section(self):
        """[13차] 각 차트에 '이 그래프를 읽는 법' 1~2문장(.tr-read) — 전 직원 대상이라
        정적 텍스트로 두어 골든에 남기고 리뷰 가능하게 한다(5개 섹션 전부)."""
        self.assertEqual(self.html.count('<p class="tr-read">'), 5)
        for section_cue in ("전체 기간을 합친 순위입니다.",
                             "각 연도를 100%로 놓고",
                             "그 해에 지적이 많아졌다는 뜻이 아닙니다.",
                             "품질이 나쁜 순서가 아닙니다.",
                             "FDA 483의 경향으로 읽으셔야 합니다."):
            self.assertIn(section_cue, self.html)

    def test_publication_date_semantics_disclosed_up_front(self):
        """오독의 근원(날짜=공개일)은 히어로와 '먼저 알아두세요' 박스 양쪽에서 먼저 밝힌다 —
        런타임 fetch 성공 여부와 무관하게 정적 텍스트로 존재해야 한다."""
        self.assertIn("날짜는 실사한 날이 아니라 <b>문서가 공개된 날</b> 기준입니다.", self.html)
        self.assertIn('<span class="lab">먼저 알아두세요</span>', self.html)
        self.assertIn("특정 해에 건수가 많아 보여도 그 해에 지적이 늘어난 건 아닙니다.", self.html)

    def test_source_mix_skew_disclosed(self):
        """소스 편중(FDA 483 압도)은 이 페이지 전체 해석의 전제 — 숨기지 않고 소스 구성
        섹션에서 명시하고, 각 행에 구성비를 병기한다."""
        self.assertIn("지금은 FDA 483이 대부분입니다.", self.html)
        self.assertIn("수집을 시작한 지 얼마 되지 않아", self.html)

    def test_year_trend_caveat_note_present(self):
        self.assertIn("과거 연도는 아직 채워 넣는 중입니다", self.html)
        self.assertIn("하한치", self.html)
        # 제목 자체가 "추이"(=규제 활동 변화)로 읽히지 않게 "공개량(참고)"로 강등했다.
        self.assertIn('<h2 class="tr-h">연도별 공개량(참고)</h2>', self.html)
        self.assertNotIn("연도별 추이</h2>", self.html)

    def test_stat_strip_note_present(self):
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("나머지는 집계에만 반영(원문 영문)", js_src)

    def test_page_shell_hidden_pending_load(self):
        """골든 결정론 — #tr-content/#tr-error 는 정적 셸에서 hidden, 로딩 스켈레톤만
        기본 노출(findings.js 의 #fnd-loading 관례와 동형)."""
        self.assertIn('<div id="tr-content" hidden>', self.html)
        self.assertIn('<div class="tr-state tr-state-error" id="tr-error" hidden>', self.html)
        self.assertIn('<div class="tr-state" id="tr-loading" role="status" aria-live="polite">', self.html)

    # ── H1 연도별 구성비 히트맵 ─────────────────────────────────────────────
    def test_heatmap_section_shell_present_and_hidden(self):
        """정적 셸에 구성비 섹션이 '카테고리 순위'와 '연도별 공개량' 사이에 존재하며
        기본 hidden(008 미적용 라이브·fetch 실패 시 trends.js 가 그대로 두는 상태와
        일치 — 골든 결정론)."""
        self.assertIn(
            '<section class="tr-block tr-heatmap-block" id="tr-heatmap-block" '
            'aria-label="연도별 구성비" hidden>',
            self.html,
        )
        self.assertIn('<h2 class="tr-h">연도별 구성비</h2>', self.html)
        self.assertIn('<div id="tr-heatmap" class="tr-heatmap"></div>', self.html)
        # 표본 부족으로 제외한 연도를 적을 자리도 셸에 hidden 으로 있어야 한다.
        self.assertIn('<p class="tr-note" id="tr-heatmap-note" hidden></p>', self.html)
        cat_idx = self.html.index('aria-label="카테고리 순위"')
        heatmap_idx = self.html.index('id="tr-heatmap-block"')
        year_idx = self.html.index('aria-label="연도별 공개량"')
        self.assertTrue(cat_idx < heatmap_idx < year_idx,
                         "구성비 섹션이 카테고리 순위와 연도별 공개량 사이에 있지 않음")

    def test_heatmap_cells_are_column_normalised_share(self):
        """[13차] 셀 값 = 건수 → 그 해 전체 대비 비율(열 정규화). 분모는 표에 그리는 상위
        12개가 아니라 **전 카테고리 합**이어야 한다(상위 12개로 나누면 비율이 부풀려진다).
        연도 헤더엔 그 분모(건수)를 함께 적어 표본 크기를 감춘 %가 되지 않게 한다."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderHeatmap(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        # 분모 누적은 cells 전체 순회에서 이뤄진다(상위 12개 슬라이스와 무관).
        self.assertIn("yearBase[c.year] = (yearBase[c.year] || 0) + (c.cnt || 0);", fn)
        self.assertIn("var share = base > 0 ? (cnt / base) * 100 : 0;", fn)
        self.assertIn("td.textContent = pctText(cnt, base);", fn)
        self.assertIn('el("span", "tr-heatmap-yearbase", fmtNum(yearBase[y] || 0) + "건")', fn)
        # 툴팁은 건수·분모·비율을 모두 보여 준다(원 수치 은폐 금지).
        self.assertIn('"건(그 해 "', fn)

    def test_heatmap_thin_years_dropped_but_disclosed(self):
        """표본이 얇은 연도는 비율이 노이즈라 열에서 빼되, **뺐다는 사실을 화면에 적는다**.
        적을 자리가 없는 구버전 셸에서는 아예 빼지 않는다(조용한 축소 금지)."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderHeatmap(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("var years = allYears, dropped = [];", fn)
        self.assertIn("if (heatmapNoteEl) {", fn)
        self.assertIn(">= MIN_YEAR_BASE", fn)
        self.assertIn("자료가 너무 적어 비율이 의미를 갖지 못해 뺐습니다.", fn)
        # 전부 걸러지는 극단(초기 라이브)에서는 필터를 포기하고 원본 연도를 그대로 쓴다.
        self.assertIn("if (!years.length) { years = allYears; dropped = []; }", fn)

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
        """농도 버킷은 행렬 최댓값 상대 → **비율 절대 기준**(13차). 상대 기준이면 같은 색이
        표마다 다른 뜻이 되지만, 절대 기준이면 어느 열에서나 같은 의미를 갖는다."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn(
            "var HEATMAP_OPACITY_STEPS = [0.08, 0.25, 0.45, 0.7, 1.0];", js_src)
        self.assertIn("var HEATMAP_SHARE_BREAKS = [25, 15, 8, 3];", js_src)
        self.assertIn("function shareOpacity(share)", js_src)
        self.assertNotIn("function heatmapOpacity(", js_src)   # 최댓값 상대 버킷은 제거
        self.assertIn('td.style.color = opacity > 0.45 ? "var(--on-coral)" : "var(--ink)";',
                       js_src)
        self.assertIn("tr-heatmap-cell-empty", js_src)

    def test_heatmap_scroll_wrapper_present(self):
        html_src = (WEB_DIR / "templates" / "trends.html").read_text(encoding="utf-8")
        self.assertIn(".tr-heatmap-scroll{overflow-x:auto", html_src)

    # ── [공개 범위 투명성] 트렌드 페이지 커버리지 노트 ───────────────────────────
    def test_coverage_note_shell_present_hidden_and_positioned(self):
        """정적 셸은 hidden 노트만 렌더(골든 결정론). 13차부터 데이터와 무관한 첫 문단
        (날짜=공개일)은 정적 텍스트로 두고, 수치가 들어가는 둘째 문단만 trends.js 가
        런타임에 채운다. 기존 .imp(시사점) 토큰 재사용 — 신규 CSS 0. 위치는 스탯 스트립
        직하단·카테고리 순위 위."""
        self.assertIn('<div class="imp" id="tr-coverage-note" hidden>', self.html)
        self.assertIn('<p id="tr-coverage-text"></p>', self.html)
        stats_idx = self.html.index('id="tr-stats"')
        note_idx = self.html.index('id="tr-coverage-note"')
        cat_idx = self.html.index('aria-label="카테고리 순위"')
        self.assertTrue(stats_idx < note_idx < cat_idx,
                         "커버리지 노트가 스탯 스트립~카테고리 순위 사이에 있지 않음")

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
        self.assertIn('.toLocaleString("ko-KR")', fn)
        self.assertIn("숫자는 전체 ", fn)
        self.assertIn("영어 원문으로만 표시됩니다.", fn)
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
        self.assertIn("건에서 뽑은 지적사항 ", fn)
        self.assertIn("건 기준입니다.", fn)

    def test_coverage_note_documents_absent_path_falls_back_silently(self):
        """010 미적용 라이브(totals.documents=undefined)에서는 기존 "이 대시보드의 수치는
        전체 M건 기준 집계입니다." 문안을 그대로 유지한다 — 방어적 생략, 문구 깨짐 없음."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderCoverageNote(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("숫자는 전체 ", fn)
        self.assertIn('건 기준입니다."', fn)
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
        self.assertIn("모두 국문으로 볼 수 있습니다.", fn)
        self.assertIn("coverageTextEl.textContent = isComplete", fn)

    def test_coverage_note_incomplete_wording_neutralized(self):
        """[진행형 문구 중립화 M4] 미완료 경고에서 "순차 공개되며"(계속 진행 중이라는
        인상)를 제거하고 "국문 번역이 완료된 지적사항만 가능"이라는 현재 상태 서술로
        바꾼다 — 집계와 클릭 결과가 다를 수 있다는 핵심 정보는 그대로 유지."""
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderCoverageNote(totals)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertNotIn("순차", fn)
        self.assertIn("번역 전이라", fn)
        self.assertIn("영어 원문으로만 표시됩니다.", fn)

    def test_firm_name_html_entity_decode_applied_at_ranking_and_detail_panel(self):
        """[firm_name 엔티티 디코드 M5] 업체 랭킹(buildFirmRow)·상세 패널 헤더
        (renderFirmDetail) 모두 decodeFirmDisplay() 를 거쳐 표시한다 — 클릭/state 비교
        (openFirm 호출·state.openFirm===f.firm_name)는 findings_firm_stats RPC exact-match
        파라미터라 raw f.firm_name 그대로 유지한다. (13차부터 헤드라인엔 업체명이 등장하지
        않는다 — test_headline_has_no_disclosure_date_yoy 참조.)"""
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
        self.assertEqual(nav_m.group(1).count("<a "), 6)  # 모아보기·찾아보기·트렌드·자료실·용어사전·이용안내
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


class WebMePageTest(unittest.TestCase):
    """개인 홈 /me (13차 G2) — 스크랩·구름이 성장 현황·관심 업체를 한 화면에.

    핵심 계약 두 가지를 고정한다:
      (1) **비로그인 불침범** — /me 는 로그인 게이트가 아니다. 게스트로 들어와도 페이지가
          깨지지 않고, 구름이 섹션은 localStorage 기록 그대로 보이며, 로그인은 유도만 한다.
      (2) **구름이 패널 CSS 단일원천** — growth.js 가 퀴즈/마이페이지 두 곳에 같은 마크업을
          주입하므로 .qzg* 스타일 사본이 생기면 반드시 어긋난다. 두 템플릿이 같은 partial 을
          include 해야 한다(그 partial 은 quiz.html 에서 잘라낸 원본 그대로 — 한 글자라도
          달라지면 quiz 골든이 깨진다).

    me/index.html 은 reactions env-gate 뒤에서만 생성되므로(기존 관례) env-on 빌드로 본다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_me_"))
        u0, k0 = render.SUPABASE_URL, render.SUPABASE_ANON_KEY
        try:
            render.SUPABASE_URL = "https://rfwixqqdljpmtjdlblct.supabase.co"
            render.SUPABASE_ANON_KEY = "anon-key"
            out = cls._tmp / "on"
            render.render_site(SINGLE_FIXTURES, out)
            cls.me = (out / "me" / "index.html").read_text(encoding="utf-8")
            cls.landing_on = (out / "index.html").read_text(encoding="utf-8")
        finally:
            render.SUPABASE_URL, render.SUPABASE_ANON_KEY = u0, k0
        cls.off = cls._tmp / "off"
        _build_single(cls.off)
        cls.landing_off = (cls.off / "index.html").read_text(encoding="utf-8")
        cls.me_tmpl = (WEB_DIR / "templates" / "me.html").read_text(encoding="utf-8")
        cls.quiz_tmpl = (WEB_DIR / "templates" / "quiz.html").read_text(encoding="utf-8")
        cls.partial = (WEB_DIR / "templates" / "growth_panel_style.html").read_text(encoding="utf-8")
        cls.reactions_js = (WEB_DIR / "assets" / "reactions.js").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_page_title_and_no_nav_tab_activated(self):
        """스크랩 전용에서 개인 홈으로 넓어져 제목도 '마이페이지'. nav 6탭 중 어느 것도 이
        페이지를 대표하지 않으므로 아무 탭도 켜지 않는다(이전엔 nav_active='board' 라
        무관한 '모아보기'가 활성으로 보였다)."""
        self.assertIn("<title>마이페이지 · GRM</title>", self.me)
        self.assertIn('<h1 class="grm-my-h">마이페이지</h1>', self.me)
        import re as _re
        nav_m = _re.search(r'<nav id="navmenu">(.*?)</nav>', self.me, _re.S)
        self.assertIsNotNone(nav_m)
        self.assertNotIn('class="on"', nav_m.group(1), "/me 에서 무관한 nav 탭이 활성화됨")

    def test_three_sections_present_in_order(self):
        """구름이를 스크랩보다 위에 둔다 — 로그인 여부와 무관하게 항상 내용이 있는 유일한
        섹션이라, 처음 들어온 게스트에게 빈 화면 대신 자기 기록을 먼저 보여 준다."""
        for h in ("구름이 성장 현황", "내 스크랩", "관심 업체"):
            self.assertIn(f'<h2 class="grm-my-h2">{h}</h2>', self.me)
        gurumi = self.me.index("구름이 성장 현황")
        scraps = self.me.index('<h2 class="grm-my-h2">내 스크랩</h2>')
        firms = self.me.index('<h2 class="grm-my-h2">관심 업체</h2>')
        self.assertTrue(gurumi < scraps < firms, "섹션 순서(구름이→스크랩→관심 업체) 불일치")
        self.assertIn('id="grm-my-scraps"', self.me)
        self.assertIn('id="grm-my-firms"', self.me)

    def test_growth_placeholder_and_script_wired(self):
        """셸은 hidden 자리표시자 1줄만 — 마크업·수치는 전부 growth.js 런타임 주입
        (퀴즈 페이지와 동일 계약). 서버 동기화(growth-sync.js)는 base.html reactions
        게이트에서 이미 전 페이지에 로드되므로 여기서 또 싣지 않는다."""
        self.assertIn(
            '<section class="me-growth" id="grm-growth" hidden aria-label="구름이 성장 현황"></section>',
            self.me)
        import re as _re
        self.assertIsNotNone(_re.search(r'assets/growth\.js\?v=([0-9a-f]{8})"', self.me),
                             "growth.js 캐시버스팅 해시 미발견")
        self.assertEqual(self.me.count("assets/growth.js"), 1)
        self.assertIn("assets/growth-sync.js", self.me)   # base.html 게이트에서 1회

    def test_growth_panel_css_single_source_shared_with_quiz(self):
        """.qzg* 규칙은 partial 하나에만 존재하고, quiz.html·me.html 이 그것을 include 한다.
        어느 한쪽이 사본을 인라인하면 growth.js 가 주입하는 같은 마크업이 두 페이지에서
        다르게 보이기 시작한다."""
        inc = '{% include "growth_panel_style.html" %}'
        self.assertIn(inc, self.quiz_tmpl)
        self.assertIn(inc, self.me_tmpl)
        self.assertIn(".qzg{", self.partial)
        self.assertIn(".qzg-atlas{", self.partial)
        for tmpl, name in ((self.quiz_tmpl, "quiz.html"), (self.me_tmpl, "me.html")):
            self.assertNotIn(".qzg{", tmpl, f"{name} 에 .qzg 사본 인라인(단일원천 위반)")
        # 두 페이지 렌더 출력엔 동일한 규칙이 실제로 실려야 한다.
        quiz_html = (self.off / "quiz" / "index.html").read_text(encoding="utf-8")
        for rule in (".qzg{", ".qzg-atlas{", ".qzg-stage-card{"):
            self.assertIn(rule, quiz_html, f"quiz 출력에 {rule} 누락")
            self.assertIn(rule, self.me, f"me 출력에 {rule} 누락")

    def test_guest_is_not_gated_and_sees_own_gurumi(self):
        """비로그인도 페이지가 깨지지 않아야 한다 — 정적 셸에 로그인 강제/차단 마크업이 없고,
        구름이 자리표시자는 세션과 무관하게 존재한다(growth.js 는 localStorage 만 읽는다)."""
        self.assertIn('id="grm-growth"', self.me)
        self.assertIn("로그인하지 않아도 이 브라우저에 기록이 쌓여요", self.me)
        # 게스트 카드는 런타임 주입이지만, 정적 셸이 로그인 없이는 못 보게 막지 않는다.
        self.assertNotIn("로그인이 필요합니다", self.me)
        self.assertNotIn("로그인 후 이용", self.me)

    def test_guest_head_card_uses_shared_signup_entry(self):
        """게스트 계정 카드는 가입 CTA 를 한 곳에만 두고, 진입점은 #351 의 기존
        openLogin({mode:"signup"}) 을 재사용한다 — 새 인증 UI 발명 0."""
        self.assertIn("function renderMeGuestHead(head)", self.reactions_js)
        fn = self.reactions_js[self.reactions_js.index("function renderMeGuestHead(head)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("가입하고 시작하기", fn)
        self.assertIn("이미 계정이 있어요 · 로그인", fn)
        self.assertIn('openLogin({ mode: "signup" })', fn)
        # 비로그인 분기가 빈 카드로 남지 않는다(13차 이전 동작 회귀 방지).
        head_fn = self.reactions_js[self.reactions_js.index("function renderMeHead(count)"):]
        head_fn = head_fn[:head_fn.index("\n  }")]
        self.assertIn("renderMeGuestHead(head); return;", head_fn)
        self.assertNotIn('head.innerHTML = ""; return;', head_fn)

    def test_login_cta_not_duplicated_across_sections(self):
        """같은 화면에 로그인 버튼이 여러 개 뜨지 않게, 스크랩 섹션의 비로그인 버튼은
        제거하고 상단 게스트 카드 하나로 모았다(문구 안내는 유지)."""
        self.assertNotIn('className = "grm-my-login"', self.reactions_js)
        self.assertIn("로그인하면 스크랩한 카드를 이곳에 모아볼 수 있어요.", self.reactions_js)
        self.assertIn("로그인하면 관심 업체를 모아볼 수 있어요.", self.reactions_js)

    def test_growth_fallback_note_hidden_by_css_when_panel_renders(self):
        """growth.js 미로드·localStorage 차단 시 제목만 덩그러니 남지 않도록 정적 폴백
        문단을 두고, 패널이 뜨면 인접 선택자로 감춘다(JS 관여 0)."""
        self.assertIn('<p class="grm-my-note me-growth-fb">', self.me)
        self.assertIn(".me-growth:not([hidden]) + .me-growth-fb{display:none}", self.me)

    def test_entry_point_footer_only_and_env_gated(self):
        """진입점은 헤더 계정 메뉴(로그인 시)와 footer(상시) 두 곳 — nav 탭은 늘리지 않는다.
        footer 링크는 me/index.html 과 같은 env-gate 로 묶어, env-off 빌드에서 404 링크가
        남지 않고 전 페이지 골든 byte-diff 가 0 이 되게 한다."""
        self.assertIn('<a href="me/index.html">마이페이지</a>', self.landing_on)
        self.assertNotIn("마이페이지", self.landing_off)   # env-off = 링크 자체가 없다
        # nav 탭 수는 그대로 6개(과밀 금지).
        import re as _re
        nav_m = _re.search(r'<nav id="navmenu">(.*?)</nav>', self.landing_on, _re.S)
        self.assertEqual(nav_m.group(1).count("<a "), 6)
        self.assertNotIn("마이페이지", nav_m.group(1))
        # 헤더 계정 메뉴 항목도 실제 페이지 내용과 이름을 맞췄다(링크·아이콘은 그대로).
        self.assertIn('<i class="ti ti-bookmark" aria-hidden="true"></i>마이페이지</a>',
                      self.reactions_js)

    def test_no_new_backend_surface(self):
        """신규 RPC·마이그레이션 0 — 기존 reaction·firm_watchlist·gurumi_growth 경로만
        쓴다. me.html 셸 자체는 네트워크 호출을 하지 않는다(전부 기존 자산 소관)."""
        self.assertNotIn("/rest/v1/rpc/", self.me_tmpl)
        self.assertNotIn("fetch(", self.me_tmpl)
        self.assertNotIn("<script>", self.me_tmpl)   # 인라인 스크립트 0
        for table in ('from("reaction")', 'from("firm_watchlist")'):
            self.assertIn(table, self.reactions_js)


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
class WebLibraryRenderTest(unittest.TestCase):
    """[자료실 트랙 C] /library/ 허브 + registry 전 카탈로그(v2 스키마) 정적 렌더.

    findings/trends 와 달리 라이브 데이터가 아니라 커밋 스냅샷(web/data/library/*.json)을
    결정론 렌더한다(주간 발행 게이트와 무관한 독립 섹션). 셸이 아니라 실데이터가 빌드시
    HTML 에 박히므로 골든이 정본이고, 여기선 구조·배선·데이터 정합만 보강 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_lib_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.hub = (cls.single / "library" / "index.html").read_text(encoding="utf-8")
        cls.pages = {e["slug"]: (cls.single / "library" / e["slug"] / "index.html")
                     .read_text(encoding="utf-8") for e in render.LIBRARY_REGISTRY}
        cls.data = {e["slug"]: json.loads((render.LIBRARY_DIR / e["file"])
                    .read_text(encoding="utf-8")) for e in render.LIBRARY_REGISTRY}
        cls.ich = cls.pages["ich"]
        cls.mfds = cls.pages["mfds"]
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_pages_generated_with_registry_titles(self):
        self.assertIn("자료실", self.hub)
        for e in render.LIBRARY_REGISTRY:
            self.assertIn(e["title"], self.pages[e["slug"]], f"{e['slug']} 제목 누락")

    def test_hub_links_and_counts_all_catalogs(self):
        # 허브 = registry 전 카탈로그 카드 — 링크·건수 정합.
        self.assertEqual(self.hub.count('class="lib-cat '), len(render.LIBRARY_REGISTRY))
        for e in render.LIBRARY_REGISTRY:
            self.assertIn(f'href="../library/{e["slug"]}/index.html"', self.hub)
            n = len(self.data[e["slug"]]["items"])
            self.assertIn(f'>{n}<span class="u">{e["unit"]}</span>', self.hub,
                          f"{e['slug']} 허브 건수 표기 불일치")

    def test_nav_link_present_and_active(self):
        # 자료실 페이지에서만 nav 'on' 이 붙는다. 타 페이지엔 링크만.
        self.assertIn('library/index.html" class="on">자료실</a>', self.hub)
        for html in self.pages.values():
            self.assertIn('library/index.html" class="on">자료실</a>', html)
        self.assertIn('href="library/index.html">자료실</a>', self.landing)
        self.assertNotIn('class="on">자료실</a>', self.landing)

    def test_sitemap_includes_all_catalogs(self):
        for path in ["/library/"] + [f"/library/{e['slug']}/" for e in render.LIBRARY_REGISTRY]:
            self.assertIn(f"<loc>{render.SITE_BASE_URL}{path}</loc>", self.sitemap)

    def test_all_items_rendered_per_catalog(self):
        for e in render.LIBRARY_REGISTRY:
            n = len(self.data[e["slug"]]["items"])
            self.assertEqual(self.pages[e["slug"]].count('<li class="lib-item">'), n,
                             f"{e['slug']} 항목 수 불일치")

    def test_flat_catalogs_link_every_official_url(self):
        # link_label 카탈로그(ICH)를 제외한 전 카탈로그는 항목 제목이 공식 원문으로 직결.
        for e in render.LIBRARY_REGISTRY:
            if e.get("link_label"):
                continue
            html = self.pages[e["slug"]]
            for it in self.data[e["slug"]]["items"]:
                self.assertIn(f'href="{_esc(it["official_url"])}"', html,
                              f"{e['slug']} 원문 링크 누락: {it['id']}")
            self.assertIn('target="_blank" rel="noopener"', html)

    def test_ich_honest_catalog_links(self):
        # ICH 확장 데이터(2026-07-18): official_url 은 여전히 공식 카탈로그 2페이지로
        # 수렴하므로 제목을 official_url 로 링크하지 않는다. 단 pdf_url 이 있는 토픽은
        # title 이 현행 문서명을 명시하므로 제목=문서 PDF 직결(PDF 아이콘), pdf_url 이
        # 없는 토픽(M1·M2 등 7건)은 기존 정직 처리(무링크 제목 + 그룹 헤더 라벨 링크만).
        items = self.data["ich"]["items"]
        pdf = [it for it in items if it.get("pdf_url")]
        no_pdf = [it for it in items if not it.get("pdf_url")]
        self.assertTrue(pdf and no_pdf, "ICH pdf 유/무 토픽 둘 다 기대")
        self.assertEqual(self.ich.count('<a class="lib-item-a"'), len(pdf))
        self.assertEqual(self.ich.count('<span class="lib-item-a">'), len(no_pdf))
        for it in pdf:
            self.assertIn(f'class="lib-item-a" href="{_esc(it["pdf_url"])}"', self.ich,
                          f"ICH PDF 직링크 누락: {it['code']}")
        # 제목 anchor 아이콘 = PDF(외부 카탈로그 링크로 오인 방지) · 중복 PDF 칩은 억제.
        self.assertEqual(self.ich.count('ti-file-type-pdf lib-item-ext'), len(pdf))
        self.assertNotIn(">PDF</a>", self.ich)
        # official_url(카탈로그 페이지)로의 항목 레벨 앵커는 여전히 0 — 그룹 헤더 라벨만.
        self.assertEqual(self.ich.count(">ICH 공식 카탈로그 <"), 2)
        for url in sorted({it["official_url"] for it in items}):
            self.assertIn(f'class="lib-series-link" href="{url}"', self.ich)
            self.assertNotIn(f'class="lib-item-a" href="{url}"', self.ich)
        # 코드·한글 병기: code 칩 + title_ko 주 제목 + title_en 병기 줄(현행 문서명).
        self.assertEqual(self.ich.count('class="lib-code"'), len(items))
        self.assertIn('<span class="lib-item-title">안정성</span>', self.ich)
        self.assertIn('<p class="lib-item-sub">Q1A(R2) Stability Testing of New Drug '
                      'Substances and Products</p>', self.ich)
        # 식약처 한글 번역본(ko_url) 칩 — 7토픽(Q1A-Q1F·Q7~Q10·Q13·M4) 기존 규약 유지.
        ko = [it for it in items if it.get("ko_url")]
        self.assertEqual(len(ko), 7, "ICH ko_url 7토픽 기대")
        for it in ko:
            self.assertIn(f'href="{_esc(it["ko_url"])}"', self.ich)
        self.assertEqual(self.ich.count("한글 번역본</a>"), len(ko))

    def test_published_desc_sort_applied(self):
        # sort="published_desc" 카탈로그는 화면 순서가 발행일 내림차순(뷰 정렬 — 값 무수정).
        import re as _re
        for e in render.LIBRARY_REGISTRY:
            html = self.pages[e["slug"]]
            shown = _re.findall(r">발행 (\d{4}-\d{2}-\d{2})<", html)
            n_pub = sum(1 for it in self.data[e["slug"]]["items"] if it.get("published_date"))
            self.assertEqual(len(shown), n_pub, f"{e['slug']} 발행일 표기 수 불일치")
            if e.get("sort") == "published_desc":
                self.assertEqual(shown, sorted(shown, reverse=True),
                                 f"{e['slug']} 발행일 내림차순 위반")

    def test_no_internal_ops_concepts_exposed(self):
        # [품질 기준 2026-07-18] Tier/QA·수집일 등 내부 개념 텍스트 노출 금지. doc_type
        # 원시 슬러그는 URL 경로에 우연히 포함될 수 있어(FDA guidance-industry 경로)
        # 표시 칩(lib-type) 렌더로 한정해 검사한다.
        for slug, html in {**self.pages, "hub": self.hub}.items():
            for banned in ("Tier 1", "Tier 2", "Tier 3", "QA 관련", "signal_tier",
                           "qa_relevance", "수집 기준", "최신 수집분", "최근 수집",
                           "감지 기준일", "collected_date"):
                self.assertNotIn(banned, html, f"{slug}: 내부 개념 노출 — {banned}")
            for raw_slug in ("guidance-internal", "guidance-industry", "legislative-notice",
                             "notice-final", "guideline-topic",
                             "regulatory-procedural-guideline", "scientific-guideline",
                             "questions-and-answers", "guidance"):
                self.assertNotIn(f'class="lib-type">{raw_slug}<', html,
                                 f"{slug}: doc_type 원시 슬러그 칩 노출 — {raw_slug}")

    def test_doc_type_labels_mapped(self):
        # doc_type 원시 슬러그 → 한국어 표시 라벨(표시층 매핑 — 데이터 무수정).
        # doc_type_labels 를 선언한 전 카탈로그(MFDS·EMA·Health Canada·ICH) 공통 검증.
        # ""로 매핑된 값(ICH guideline-topic)은 칩 숨김이므로 건수 대조에서 제외.
        import collections
        checked = 0
        for e in render.LIBRARY_REGISTRY:
            labels = e.get("doc_type_labels")
            if not labels:
                continue
            counts = collections.Counter(it["doc_type"] for it in self.data[e["slug"]]["items"])
            html = self.pages[e["slug"]]
            for raw, label in labels.items():
                if not label:
                    continue
                self.assertEqual(html.count(f'class="lib-type">{_esc(label)}</span>'),
                                 counts.get(raw, 0),
                                 f"{e['slug']} 매핑 라벨 수 불일치: {raw}→{label}")
                checked += 1
        self.assertGreaterEqual(checked, 8, "doc_type 매핑 검증 대상 라벨 수 기대 미달")

    def test_redundant_pdf_chip_suppressed(self):
        # pdf_url == official_url(PIC/S 전건)이면 중복 PDF 칩을 만들지 않는다.
        pics = self.pages["pics"]
        self.assertNotIn(">PDF</a>", pics)
        # 구분되는 pdf_url(MFDS 전건)은 PDF 칩으로 노출.
        n_mfds_pdf = sum(1 for it in self.data["mfds"]["items"]
                         if it.get("pdf_url") and it["pdf_url"] != it["official_url"])
        self.assertEqual(self.mfds.count('class="ti ti-file-type-pdf"'), n_mfds_pdf)

    def test_registry_common_template_covers_all_catalogs(self):
        # registry 기반 공통 템플릿 — 카탈로그 전 페이지가 library_catalog.html 하나로
        # 렌더되고(전용 템플릿 0), registry 항목 수 = 생성된 카탈로그 페이지 수.
        tpl_dir = WEB_DIR / "templates"
        self.assertTrue((tpl_dir / "library_catalog.html").is_file())
        self.assertFalse((tpl_dir / "library_ich.html").exists())
        self.assertFalse((tpl_dir / "library_mfds.html").exists())
        for e in render.LIBRARY_REGISTRY:
            self.assertTrue((self.single / "library" / e["slug"] / "index.html").is_file(),
                            f"registry 카탈로그 미생성: {e['slug']}")

    def test_v2_optional_fields_conditionally_rendered(self):
        # 스키마 v2 선택 필드 — 있으면 표시·없으면 조용히 생략(공통 뷰 정규화 계약).
        # 한국어 우선: title_ko 있으면 주 제목(title), title_en 은 병기(sub)로 내린다.
        v = render._catalog_view(
            {"slug": "x", "file": "x.json", "unit": "건", "kick": "X", "blurb": "b",
             "intro": "i", "desc": "d", "title": "T"},
            {"items": [
                {"id": "a", "title_en": "Guide A", "title_ko": "가이드 A",
                 "doc_type": "guidance", "published_date": "2026-01-02",
                 "official_url": "https://example.org/a",
                 "ko_url": "https://example.org/a-ko",
                 "pdf_url": "https://example.org/a.pdf"},
                {"id": "b", "title_en": "Guide B", "official_url": "https://example.org/b"},
            ]})
        a, b = v["groups"][0]["items"]
        self.assertEqual(a["title"], "가이드 A")
        self.assertEqual(a["sub"], "Guide A")
        self.assertEqual(a["published_date"], "2026-01-02")
        self.assertEqual(a["ko_url"], "https://example.org/a-ko")
        self.assertEqual(a["pdf_url"], "https://example.org/a.pdf")
        self.assertEqual(b["title"], "Guide B")
        for k in ("sub", "code", "doc_type", "published_date", "ko_url", "pdf_url"):
            self.assertEqual(b[k], "", f"선택 필드 {k} 는 부재 시 빈 문자열")
        self.assertEqual(v["latest_published"], "2026-01-02")
        self.assertEqual(v["count"], 2)

    def test_canonical_and_description(self):
        self.assertIn(f'<link rel="canonical" href="{render.SITE_BASE_URL}/library/" />', self.hub)
        for e in render.LIBRARY_REGISTRY:
            self.assertIn(
                f'<link rel="canonical" href="{render.SITE_BASE_URL}/library/{e["slug"]}/" />',
                self.pages[e["slug"]])
        for html in [self.hub, *self.pages.values()]:
            self.assertIn('<meta name="description" content="', html)

    def test_grm_css_untouched_by_library(self):
        # 자료실은 스코프 <style>(템플릿 인라인)만 쓰고 grm.css 를 편집하지 않는다.
        for html in [self.hub, *self.pages.values()]:
            self.assertIn("<style>", html)

    def test_render_is_deterministic(self):
        out2 = self._tmp / "single2"
        _build_single(out2)
        for rel in (["library/index.html"]
                    + [f"library/{e['slug']}/index.html" for e in render.LIBRARY_REGISTRY]):
            self.assertEqual((self.single / rel).read_bytes(),
                             (out2 / rel).read_bytes(), f"비결정론 렌더: {rel}")


class WebGuideRenderTest(unittest.TestCase):
    """[이용안내 트랙 C 2차] /guide/ — guide_content.md(정본)를 제한 md 서브셋으로
    결정론 렌더. library 와 동일하게 커밋 콘텐츠가 빌드시 HTML 에 박히므로 골든이 정본이고,
    여기선 md 변환·배선·이스케이프·결정론만 보강 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_guide_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "guide" / "index.html").read_text(encoding="utf-8")
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.md = render.GUIDE_FILE.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_page_generated_with_title_from_h1(self):
        # 최상위 `# ` 헤딩은 페이지 제목(page-head h1)로 승격 — 본문에 h1 은 남지 않는다.
        self.assertIn("<h1", self.html)
        self.assertIn("GRM 이용 안내", self.html)
        self.assertNotIn("<h1>GRM 이용 안내</h1>", self.html)  # md h1 이 본문 h1 로 재출력되지 않음

    def test_all_sections_and_subsections_rendered(self):
        # md 의 ## 8개·### 11개가 모두 h2/h3 로 변환됐는지(개수 일치). h2 는 목차 앵커
        # id(sec-N)를 달고 나온다(2026-07-18 개편).
        n_h2 = sum(1 for ln in self.md.splitlines() if ln.startswith("## "))
        n_h3 = sum(1 for ln in self.md.splitlines() if ln.startswith("### "))
        self.assertEqual(n_h2, 8)
        self.assertEqual(n_h3, 11)
        self.assertEqual(self.html.count('<h2 id="sec-'), n_h2)
        self.assertEqual(self.html.count("<h3>"), n_h3)

    def test_toc_derived_from_h2(self):
        # 상단 목차 = 렌더러가 h2 에서 결정론 파생(id="sec-N" ↔ href="#sec-N" 쌍 일치).
        n_h2 = sum(1 for ln in self.md.splitlines() if ln.startswith("## "))
        self.assertIn('class="wrap guide-toc', self.html)
        for i in range(1, n_h2 + 1):
            self.assertIn(f'href="#sec-{i}"', self.html)
            self.assertIn(f'<h2 id="sec-{i}">', self.html)
        self.assertNotIn(f'href="#sec-{n_h2 + 1}"', self.html)

    def test_lists_and_inline_markup_converted(self):
        self.assertIn("<ul>", self.html)
        self.assertIn("<ol>", self.html)
        self.assertIn("<li>", self.html)
        self.assertIn("<strong>", self.html)
        self.assertIn("<code>OOS</code>", self.html)

    def test_no_raw_markdown_markers_leak_in_body(self):
        # 본문 프로즈에 미변환 `**`·인라인 백틱이 남지 않아야 한다(변환 누락 방지).
        import re as _re
        m = _re.search(r'<div class="wrap guide-body[^>]*>(.*?)</aside>', self.html, _re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertNotIn("**", body)
        self.assertNotIn("`", body)

    def test_no_external_markdown_library(self):
        # 결정론 자체 변환만 — 외부 md 라이브러리 import 금지.
        src = (WEB_DIR / "render.py").read_text(encoding="utf-8")
        for forbidden in ("import markdown", "import mistune", "import commonmark",
                          "from markdown", "import markdown2"):
            self.assertNotIn(forbidden, src, forbidden)

    def test_inline_html_in_content_would_be_escaped(self):
        # _md_inline 은 텍스트를 먼저 escape → 제한 마커만 태그 승격(XSS 방어선).
        title, toc, body = render.render_guide_html("## <script>alert(1)</script> **굵게**")
        self.assertIn("&lt;script&gt;", str(body))
        self.assertNotIn("<script>", str(body))
        self.assertIn("<strong>굵게</strong>", str(body))
        # 목차 라벨은 마커 제거 평문(태그 승격 없음 — 템플릿 autoescape 경로).
        self.assertEqual(toc, [{"id": "sec-1", "title": "<script>alert(1)</script> 굵게"}])

    def test_glossary_crosslink_present(self):
        self.assertIn('href="../glossary/index.html"', self.html)

    def test_nav_active_and_meta(self):
        self.assertIn('guide/index.html" class="on">이용안내</a>', self.html)
        self.assertIn('href="guide/index.html">이용안내</a>', self.landing)
        self.assertNotIn('class="on">이용안내</a>', self.landing)
        self.assertIn(f'<link rel="canonical" href="{render.SITE_BASE_URL}/guide/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

    def test_grm_css_untouched_by_guide(self):
        self.assertIn("<style>", self.html)  # 스코프 스타일만(grm.css 미편집)

    def test_render_is_deterministic(self):
        out2 = self._tmp / "single2"
        _build_single(out2)
        self.assertEqual((self.single / "guide" / "index.html").read_bytes(),
                         (out2 / "guide" / "index.html").read_bytes(), "비결정론 렌더")


class WebGlossaryRenderTest(unittest.TestCase):
    """[용어사전 트랙 C 2차] /glossary/ — glossary.json(정본)을 초성 색인 1페이지로
    결정론 렌더. 값(term_ko/term_en/easy_ko/출처) 무변형, 파생은 초성 버킷·related 라벨뿐.
    골든이 정본이고, 여기선 구조·무변형·딥링크·검색배선·결정론만 보강 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_gloss_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "glossary" / "index.html").read_text(encoding="utf-8")
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")
        cls.terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_all_terms_rendered_as_articles_with_id_anchors(self):
        self.assertEqual(self.html.count('<article class="gl-term"'), len(self.terms))
        for t in self.terms:
            self.assertIn(f'<article class="gl-term" id="{t["id"]}"', self.html,
                          f'용어 앵커 누락: {t["id"]}')

    def test_values_verbatim(self):
        # 표시값은 데이터 그대로(무변형) — term_ko/term_en/easy_ko/출처.
        from markupsafe import escape as _esc2
        for t in self.terms:
            self.assertIn(str(_esc2(t["term_en"])), self.html)
            self.assertIn(str(_esc2(t["easy_ko"])), self.html)
            self.assertIn(str(_esc2(t["definition_source"])), self.html)

    def test_related_crosslinks_resolve_to_existing_terms(self):
        ids = {t["id"] for t in self.terms}
        for t in self.terms:
            for r in t.get("related", []):
                if r in ids:  # 고아 참조는 렌더에서 제외(존재하는 것만 링크)
                    self.assertIn(f'class="gl-rel-a" href="#{r}"', self.html)

    def test_chosung_grouping_deterministic_and_ordered(self):
        # 버킷 = 데이터 파생(term_ko 초성), 순서 = _GLOSSARY_BUCKET_ORDER 고정(가나다→A–Z→#).
        view = render.build_glossary_view(self.terms)
        buckets = [b["bucket"] for b in view["buckets"]]
        expected_present = {render._glossary_bucket(t["term_ko"]) for t in self.terms}
        order = {b: i for i, b in enumerate(render._GLOSSARY_BUCKET_ORDER)}
        self.assertEqual(buckets, sorted(expected_present, key=lambda b: (order.get(b, 99), b)))
        self.assertEqual(self.html.count('<section class="gl-group"'), len(buckets))
        self.assertEqual(view["total"], len(self.terms))
        self.assertEqual(len(self.terms), 200)  # v3 200어(교체 정합 가드 — 9차 자율 런 G1)

    def test_source_url_renders_source_as_link(self):
        # v2 source_url — 출처 표기를 공식 문서 새 탭 링크로(값 무변형·안전 URL 만).
        n_src = sum(1 for t in self.terms if t.get("source_url"))
        self.assertEqual(self.html.count('class="gl-src-a"'), n_src)
        for t in self.terms[:5]:
            if t.get("source_url"):
                self.assertIn(f'href="{_esc(t["source_url"])}"', self.html)

    def test_jump_index_matches_groups(self):
        # 색인 바 링크 = 그룹 앵커(빠짐·군더더기 0).
        import re as _re
        idx = _re.findall(r'data-bucket="(grp-\d+)">([^<]+)</a>', self.html)
        grp = _re.findall(r'<section class="gl-group" id="(grp-\d+)"', self.html)
        self.assertEqual([a for a, _ in idx], grp)

    def test_search_filter_asset_is_new_file_referenced_with_hash(self):
        # 클라이언트 필터는 신규 asset(glossary.js) — 기존 js 미편집(별도 파일).
        self.assertIn("/assets/glossary.js?v=", self.html)
        built = (self.single / "assets" / "glossary.js").read_bytes()
        src = (WEB_DIR / "assets" / "glossary.js").read_bytes()
        self.assertEqual(built, src, "glossary.js 가 verbatim 복사되지 않음")

    def test_search_data_attr_present_and_lowercased(self):
        # 카드마다 data-search(term_ko/en/easy+detail_ko(있을 때만) 소문자 결합) — 클라이언트
        # 필터 입력. v3 부터 전 용어가 detail_ko 보유 — render 결합식과 동일하게 파생 대조.
        for t in self.terms:
            parts = [t["term_ko"], t["term_en"], t["easy_ko"]]
            if t.get("detail_ko"):
                parts.append(t["detail_ko"])
            combined = " ".join(parts).lower()
            self.assertEqual(render._glossary_bucket(t["term_ko"]),
                             render._glossary_bucket(t["term_ko"]))  # 순수 함수 안정
            self.assertIn(str(_esc(combined)), self.html)

    def test_nav_active_and_meta(self):
        # [8차 웨이브 A] 용어사전 전용 nav 탭 신설 — glossary 탭이 점등된다(이용안내 아님).
        self.assertIn('glossary/index.html" class="on">용어사전</a>', self.html)
        self.assertNotIn('guide/index.html" class="on">이용안내</a>', self.html)
        self.assertIn(f'<link rel="canonical" href="{render.SITE_BASE_URL}/glossary/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

    def test_sitemap_includes_guide_and_glossary(self):
        for path in ("/guide/", "/glossary/"):
            self.assertIn(f"<loc>{render.SITE_BASE_URL}{path}</loc>", self.sitemap)

    def test_grm_css_untouched_by_glossary(self):
        self.assertIn("<style>", self.html)

    def test_render_is_deterministic(self):
        out2 = self._tmp / "single2"
        _build_single(out2)
        self.assertEqual((self.single / "glossary" / "index.html").read_bytes(),
                         (out2 / "glossary" / "index.html").read_bytes(), "비결정론 렌더")


class WebGlossaryDeepFieldsTest(unittest.TestCase):
    """[용어사전 심화 필드 8차 웨이브 A] detail_ko(실무 맥락 설명)·reg_refs(관련 조항
    참조) — 병렬 작업자가 glossary.json 에 추가할 예정인 선택 필드. 현재 정본 데이터엔
    없다(부재해도 기존 렌더와 byte 동일해야 함) — "필드가 있으면 렌더" 조건부 배선만
    이번에 구현한다. 무네트워크·결정론(합성 데이터만 사용)."""

    def test_reg_refs_normalizes_mixed_input_and_drops_unsafe_or_blank(self):
        synthetic = {
            "id": "syn1", "term_ko": "합성용어", "term_en": "Synthetic Term",
            "easy_ko": "테스트용 합성 용어입니다", "definition_source": "테스트",
            "detail_ko": "실무에서는 이렇게 씁니다",
            "reg_refs": [
                "21 CFR 211.100",                                    # 문자열 → label 만
                {"label": "ICH Q7", "url": "https://ich.org/q7"},    # dict + 안전 URL
                {"label": "무링크 조항"},                              # dict, url 없음
                {"label": "  ", "url": "https://x.com"},              # label 공백뿐 → 제외
                {"url": "https://y.com"},                             # label 없음 → 제외
                {"label": "위험스킴", "url": "javascript:alert(1)"},   # 비안전 URL → ""로 게이트
            ],
        }
        view = render.build_glossary_view([synthetic])
        t = view["groups"][0]["terms"][0]
        self.assertEqual(t["detail_ko"], "실무에서는 이렇게 씁니다")
        self.assertEqual(t["reg_refs"], [
            {"label": "21 CFR 211.100", "url": ""},
            {"label": "ICH Q7", "url": "https://ich.org/q7"},
            {"label": "무링크 조항", "url": ""},
            {"label": "위험스킴", "url": ""},
        ])
        self.assertIn("실무에서는 이렇게 씁니다", t["search"])

    def test_fields_absent_matches_existing_shape_with_no_extra_whitespace_in_search(self):
        plain = {
            "id": "syn2", "term_ko": "평범용어", "term_en": "Plain Term",
            "easy_ko": "필드가 없는 용어입니다", "definition_source": "테스트",
        }
        view = render.build_glossary_view([plain])
        t = view["groups"][0]["terms"][0]
        self.assertEqual(t["detail_ko"], "")
        self.assertEqual(t["reg_refs"], [])
        expected_search = " ".join([plain["term_ko"], plain["term_en"], plain["easy_ko"]]).lower()
        self.assertEqual(t["search"], expected_search)
        self.assertNotIn("  ", t["search"])  # 잉여 공백(이중 스페이스) 0

    def test_template_renders_deep_fields_conditionally(self):
        # glossary.json(정본)을 건드리지 않고 render.load_glossary 만 임시 스왑 — load_glossary
        # 의 path 인자 기본값(GLOSSARY_FILE)은 정의 시점에 바인딩돼 모듈 속성 재대입으론 안
        # 바뀌므로, 반환값 자체를 대체한다(popular.js 테스트의 SUPABASE_URL monkeypatch 관례
        # 동형). full render_site 로 실제 base.html 배선(nav/globals)까지 통과한 glossary.html
        # 렌더 결과를 검증한다.
        terms = [
            {
                "id": "tpl1", "term_ko": "다카", "term_en": "Template Term A",
                "easy_ko": "템플릿 검증용 설명 A", "definition_source": "테스트 출처",
                "detail_ko": "실무 맥락 설명 예시입니다",
                "reg_refs": ["21 CFR 211", {"label": "ICH Q7", "url": "https://ich.org/q7"}],
            },
            {
                "id": "tpl2", "term_ko": "다나", "term_en": "Template Term B",
                "easy_ko": "템플릿 검증용 설명 B", "definition_source": "테스트 출처",
            },
        ]
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_gldeep_tpl_"))
        orig_load = render.load_glossary
        try:
            render.load_glossary = lambda *a, **kw: terms
            out = tmp / "out"
            render.render_site(SINGLE_FIXTURES, out)
            html = (out / "glossary" / "index.html").read_text(encoding="utf-8")
        finally:
            render.load_glossary = orig_load
            shutil.rmtree(tmp, ignore_errors=True)

        # 두 용어는 초성순 정렬로 다나(tpl2)가 다카(tpl1)보다 먼저 오므로(그룹 정렬 결정론),
        # id 위치 순서에 기대지 않고 각자 </article> 까지 독립적으로 슬라이스한다.
        block1 = html[html.index('id="tpl1"'):]
        block1 = block1[:block1.index("</article>")]
        self.assertIn('class="gl-detail"', block1)
        self.assertIn("실무 맥락 설명 예시입니다", block1)
        self.assertIn('class="gl-refs"', block1)
        self.assertIn("21 CFR 211", block1)
        self.assertIn('href="https://ich.org/q7"', block1)

        block2 = html[html.index('id="tpl2"'):]
        block2 = block2[:block2.index("</article>")]
        self.assertNotIn('class="gl-detail"', block2)
        self.assertNotIn('class="gl-refs"', block2)


def freeze() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_freeze_"))
    try:
        single, multi = tmp / "single", tmp / "multi"
        resources = tmp / "resources"
        _build_single(single)
        _build_multi(multi, tmp)
        _build_resources(resources)
        for rel, name in SINGLE_GOLDENS:
            shutil.copyfile(single / rel, GOLDEN_DIR / name)
            print(f"  froze {name}")
        for rel, name in MULTI_GOLDENS:
            shutil.copyfile(multi / rel, GOLDEN_DIR / name)
            print(f"  froze {name}")
        for rel, name in RESOURCE_GOLDENS:
            shutil.copyfile(resources / rel, GOLDEN_DIR / name)
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


# ── [업계 브리핑 노트 2026-07-13] resources 섹션 — 구조·게이트·바이트 불변 ───────
class WebResourceNotesRenderTest(unittest.TestCase):
    """assemble_publish_brief.extract_resource_notes() 산출(brief.resources)을
    render.py 가 '전문지 브리핑'(구 '업계 브리핑 노트') 전용 섹션으로 렌더하는지 확인. 격리 픽스처
    (tests/fixtures/resources) 사용 — single/multi 아카이브·랜딩 집계 골든과 분리.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_resnotes_"))
        cls.out = cls._tmp / "out"
        _build_resources(cls.out)
        cls.detail = (cls.out / "briefs" / "2026-05-01" / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_section_header_and_subtitle(self):
        # [전문지 브리핑 v2 §2] 명칭 '업계 브리핑 노트' → '전문지 브리핑'.
        self.assertIn('id="sec-resources"', self.detail)
        self.assertIn('전문지 브리핑', self.detail)
        self.assertIn('<span class="n">2건</span>', self.detail)
        self.assertIn('업계 전문지의 해설·교육 자료 2건 · 규제 변경 아님', self.detail)

    def test_item_link_target_blank_noopener(self):
        self.assertIn(
            '<a class="res-link" '
            'href="https://www.gmp-compliance.org/gmp-news/should-tga-publish-gmp-certificates" '
            'target="_blank" rel="noopener">TGA GMP인증서 공개 논의</a>',
            self.detail)

    def test_original_title_and_summary_present(self):
        self.assertIn('Should TGA publish GMP Certificates?', self.detail)
        self.assertIn('TGA의 GMP 인증서 공개 방침 변화를 다룬 해설 기사.', self.detail)

    def test_info_url_rss_feed_not_rendered(self):
        # info_url(RSS 피드)은 렌더에 쓰지 않는다(§1 근거) — 값 자체가 나타나면 안 됨.
        self.assertNotIn('eca_newsfeed.xml', self.detail)

    def test_empty_summary_item_omits_paragraph(self):
        # 두 리소스 중 summary="" 인 항목은 <p class="res-sum"> 자체가 안 나온다.
        self.assertEqual(self.detail.count('class="res-sum"'), 1)

    def test_agency_badge_reuses_card_vocabulary(self):
        section = self.detail[self.detail.index('id="sec-resources"'):]
        self.assertEqual(section.count('<span class="b ag">ECA</span>'), 2)

    def test_toc_single_entry_no_per_item_links(self):
        toc = self.detail[self.detail.index('id="toc"'):self.detail.index('</aside>')]
        self.assertEqual(toc.count('href="#sec-resources"'), 1)
        self.assertNotIn('eca-res-1', toc)
        self.assertNotIn('eca-res-2', toc)

    def test_section_collapse_js_wiring_reused(self):
        # `.sec-h`/`.sec-body` 어휘 재사용 — brief.html 의 섹션 접기 JS 가 그대로 집는다
        # (신규 JS 배선 0). id 쌍이 `sec-{slug}`/`secbody-{slug}` 계약을 따르는지 확인.
        self.assertIn('<h2 class="sec-h" id="sec-resources">', self.detail)
        self.assertIn('<div class="sec-body" id="secbody-resources">', self.detail)

    def test_no_grm_css_touched(self):
        # 하드 요구 — partial 내부 <style> 만 쓰고 grm.css 원본과 dist 복사본이 byte 동일.
        built = (self.out / "assets" / "grm.css").read_bytes()
        src = (WEB_DIR / "assets" / "grm.css").read_bytes()
        self.assertEqual(built, src)


class WebResourceNotesGoldenInvarianceTest(unittest.TestCase):
    """하드 요구 — resources 가 없는 브리프는 이 기능 도입 이후에도 바이트 불변.

    기존 골든(SINGLE_GOLDENS/MULTI_GOLDENS, 전부 무-resources 픽스처)이 재동결 없이
    그대로 통과한다는 사실 자체가 증거다(WebRenderGoldenTest 가 매 실행 검증) —
    여기서는 그 계약을 명시적으로 한 번 더 단언(회귀 의도 문서화 목적, 골든 중복 X).
    """

    def test_context_key_is_none_when_absent(self):
        # bm.get("resources") 부재 → ctx["resources"] 는 빈 리스트가 아니라 None
        # (템플릿 `{% if brief.resources %}` 게이트 대상 — §3 계약).
        base = json.loads((MULTI_FIXTURES / "brief_web_2026_06_08.json").read_text(encoding="utf-8"))
        ctx = render._brief_context(base, issue_no=1)
        self.assertIsNone(ctx["resources"])

    def test_partial_renders_empty_bytes_when_absent(self):
        env = render._make_env()
        html = env.get_template("partials/resource_notes.html").render(brief={"resources": None})
        self.assertEqual(html, "")


# ── [구름이 펫] 전 페이지 공통 위젯 + 랜딩 섹션 확정 순서(10차) 가드 ────────────────
class WebGurumiPetTest(unittest.TestCase):
    """구름이 펫은 전 페이지 공통 관리 UI다(2026-07-18 도입 — 기존 인라인 SVG 마스코트
    grm-mascot 를 대체). 과거 랜딩 coverage 칩(8차 철거)은 되살리지 않고, 독립된 #grm-pet
    위젯과 로컬 성장 데이터만 제공하는 계약을 고정한다.

    10차(사용자 확정 재배치)의 랜딩 섹션 순서·CTA 중복 가드도 이 클래스가 지킨다:
    히어로 → #why → 기능 6종 → Card Anatomy → 참여 존(#engage) → This Week 콜아웃
    (#this-week, 뉴스레터 직전) / 브리프행 CTA 는 히어로·하단 콜아웃 2개뿐."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_nogurumi_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.pet_js = (WEB_DIR / "assets" / "pet.js").read_text(encoding="utf-8")
        cls.pet_css = (WEB_DIR / "assets" / "pet.css").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_sitewide_pet_markup_and_assets_present(self):
        self.assertIn('id="grm-pet"', self.landing)
        self.assertIn('id="grm-pet-panel"', self.landing)
        self.assertIn('class="grm-pet-face-rig"', self.landing)
        self.assertIn('class="grm-pet-grab-hint"', self.landing)
        self.assertIn('id="grm-pet-drag-handle"', self.landing)
        self.assertIn('id="grm-pet-state-chip"', self.landing)
        self.assertIn('id="grm-pet-state-name"', self.landing)
        self.assertIn('assets/pet.js?v=', self.landing)
        self.assertIn('assets/pet.css?v=', self.landing)
        self.assertIn('assets/gurumi-egg.png', self.landing)

    def test_old_inline_mascot_is_replaced(self):
        self.assertNotIn('id="grm-mascot"', self.landing)
        self.assertNotIn("@keyframes grmOwlBreathe", self.landing)

    def test_pet_assets_copied_verbatim(self):
        for name in ("pet.js", "pet.css", "gurumi-egg.png", "gurumi-baby.png",
                     "gurumi-youth.png", "gurumi-adult.png", "gurumi-legend.png"):
            self.assertEqual((self.single / "assets" / name).read_bytes(),
                             (WEB_DIR / "assets" / name).read_bytes())

    def test_pet_is_local_only_and_motion_safe(self):
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket"):
            self.assertNotIn(banned, self.pet_js)
        self.assertIn('"grm-gurumi-growth"', self.pet_js)
        self.assertIn('"grm-gurumi-position-v1"', self.pet_js)
        self.assertIn('addEventListener("pointermove"', self.pet_js)
        self.assertIn('[toggle, panelDragHandle]', self.pet_js)
        self.assertIn('addEventListener("dragstart"', self.pet_js)
        self.assertIn('e.key === "ArrowLeft"', self.pet_js)
        self.assertIn('className = "grm-pet-docks"', self.pet_js)
        self.assertIn('function setDock(', self.pet_js)
        self.assertIn('data-pet-state', self.pet_js)
        self.assertIn('is-blink', self.pet_js)
        self.assertIn('id="grm-pet-reset-pos"', self.landing)
        self.assertIn("prefers-reduced-motion", self.pet_js)
        self.assertIn("prefers-reduced-motion:reduce", self.pet_css)

    def test_landing_section_order_final(self):
        # 확정 재배치(10차, 2026-07-18 사용자 확정): 히어로 → #why → 기능 6종(soft) →
        # Card Anatomy → 참여 존(#engage: 인기 카드 + 퀴즈 CTA) → This Week 콜아웃
        # (#this-week: 마감 CTA, 뉴스레터 직전) → 뉴스레터 → AI 고지.
        order = [
            'class="wrap hero"',
            '<section class="section" id="why">',
            '<section class="section soft">',
            ">Card Anatomy</span>",
            '<section class="section" id="engage">',
            '<section class="section" id="this-week">',
        ]
        pos = [self.landing.index(m) for m in order]
        self.assertEqual(pos, sorted(pos), "랜딩 섹션 순서가 확정안과 다름")

    def test_engage_zone_popular_then_quiz(self):
        # 참여 존(#engage) — 인기 카드가 먼저, 퀴즈 CTA 가 뒤(한 섹션 응집).
        zone = self.landing[self.landing.index('id="engage"'):]
        zone = zone[:zone.index("</section>")]
        self.assertIn('id="popular"', zone)
        self.assertIn('id="grm-popular"', zone)
        self.assertIn('class="quiz-cta"', zone)
        self.assertLess(zone.index('id="popular"'), zone.index('class="quiz-cta"'))

    def test_this_week_callout_is_closing_cta(self):
        # This Week 콜아웃(#this-week) — 콜아웃 단독 섹션(인기 카드는 #engage 로 분리),
        # content 블록 마지막(=뉴스레터 직전). 수치 문구·CTA 는 유지.
        zone = self.landing[self.landing.index('<section class="section" id="this-week">'):]
        zone = zone[:zone.index("</section>")]
        self.assertIn('class="callout"', zone)
        self.assertIn("이번 주 소식 보기", zone)
        self.assertNotIn('id="popular"', zone)
        self.assertNotIn('id="grm-popular"', zone)
        # #this-week 이후 </main> 까지 다른 섹션이 없다(마감 CTA).
        tail = self.landing[self.landing.index('<section class="section" id="this-week">'):]
        tail = tail[:tail.index("</main>")]
        self.assertEqual(tail.count("<section"), 1)

    def test_brief_ctas_exactly_two(self):
        # CTA 중복 정리(불가침) — 같은 브리프로 가는 버튼은 히어로("이번 주 소식 읽기")와
        # 하단 콜아웃("이번 주 소식 보기") 2개만. 인기 카드 빈 상태의 이동 버튼은 제거.
        self.assertEqual(self.landing.count("이번 주 소식 읽기"), 1)
        self.assertEqual(self.landing.count("이번 주 소식 보기"), 1)
        self.assertNotIn("이번 주 카드 보러 가기", self.landing)


# ── 인기 카드(Weekly Reactions) — 랜딩 정적 섹션 + popular.js 배선 ────────────────
class WebPopularCardsTest(unittest.TestCase):
    """랜딩 '이번 주 반응이 모인 카드' 섹션 — 정적 빈 상태(골든 정본)는 reactions_enabled
    게이트와 무관하게 항상 렌더된다. popular.js 로드만 reactions_enabled 로 게이트된다
    (reactions.js/admin.js 관례 동형). 031 RPC(reactions_weekly_top) 교차·렌더 로직은
    popular.js 소관(비골든) — 여기선 정적 셸·env-gate·자산 배선·가벼운 계약 가드만 검증."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_popular_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.popular_js = (WEB_DIR / "assets" / "popular.js").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_static_section_present_on_landing(self):
        self.assertIn('id="grm-popular"', self.landing)
        self.assertIn("이번 주 반응이 모인 카드", self.landing)
        self.assertIn("하트·스크랩 기준", self.landing)
        self.assertIn("아직 이번 주 하트·스크랩이 없어요.", self.landing)
        self.assertIn("관심 있는 카드에 ♥를 눌러 주세요", self.landing)

    def test_no_view_count_framing(self):
        # "가장 많이 본"·조회수 기준 표현 금지 — 반응(하트·스크랩) 기준만.
        self.assertNotIn("가장 많이 본", self.landing)
        self.assertNotIn("조회수 Top", self.landing)

    def test_popular_js_script_env_gated(self):
        # 테스트 환경엔 SUPABASE_URL/ANON_KEY 미설정(reactions_enabled=False) — 기본 렌더엔
        # popular.js 스크립트 태그가 없다(reactions.js 관례 동형).
        self.assertNotIn("assets/popular.js", self.landing)

        u0, k0 = render.SUPABASE_URL, render.SUPABASE_ANON_KEY
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_popular_on_"))
        try:
            render.SUPABASE_URL = "https://rfwixqqdljpmtjdlblct.supabase.co"
            render.SUPABASE_ANON_KEY = "anon-key"
            out = tmp / "out"
            render.render_site(SINGLE_FIXTURES, out)
            landing_on = (out / "index.html").read_text(encoding="utf-8")
        finally:
            render.SUPABASE_URL, render.SUPABASE_ANON_KEY = u0, k0
            shutil.rmtree(tmp, ignore_errors=True)
        import re as _re
        m = _re.search(r'assets/popular\.js\?v=([0-9a-f]{8})"', landing_on)
        self.assertIsNotNone(m, "popular.js 캐시버스팅 해시 미발견(활성 렌더)")
        # 활성 렌더에서도 정적 빈 상태 마크업은 그대로(런타임 교체는 popular.js 소관).
        self.assertIn('id="grm-popular"', landing_on)

    def test_popular_js_copied_to_dist(self):
        built = (self.single / "assets" / "popular.js").read_bytes()
        src = (WEB_DIR / "assets" / "popular.js").read_bytes()
        self.assertEqual(built, src, "popular.js 가 dist/assets 에 verbatim 복사되지 않음")

    def test_popular_js_calls_weekly_top_rpc_via_get(self):
        self.assertIn("reactions_weekly_top", self.popular_js)
        self.assertIn("rest/v1/rpc/reactions_weekly_top", self.popular_js)
        # GET(fetch 기본 메서드) — 031 이 stable 이라 PostgREST 허용, method:"POST" 미사용.
        self.assertNotIn('method: "POST"', self.popular_js)
        self.assertNotIn("method:'POST'", self.popular_js)

    def test_popular_js_reads_only_allowlisted_rpc_fields(self):
        # 031 RPC 반환 계약(불가침) — card_id·distinct_user_count 두 필드만. row.<field> 형태로
        # 그 외 필드(예: hearts/scraps/user_id/created_at)를 참조하지 않는다.
        import re as _re
        fields = set(_re.findall(r"row\.([a-zA-Z_]+)", self.popular_js))
        self.assertEqual(fields, {"card_id", "distinct_user_count"})

    def test_popular_js_never_prints_rpc_text_verbatim(self):
        # card_id 를 포함해 RPC 응답 텍스트를 화면에 직접 출력하지 않는다 — 제목/기관은
        # 전부 search-index 파생(e.target/e.issue/e.agency), card_id 는 조회 키로만 사용.
        self.assertNotIn("row.card_id +", self.popular_js)
        self.assertNotIn("+ row.card_id", self.popular_js)
        self.assertNotIn("textContent = row.card_id", self.popular_js)

    def test_popular_js_scoped_selectors_only(self):
        # 스타일 스코프 계약 대조(가벼운 정적 가드) — landing.html 의 클래스명과 정합.
        for cls in (".popular-list", ".popular-item", ".popular-rank",
                    ".popular-agency", ".popular-title", ".popular-count"):
            self.assertIn(cls.lstrip("."), self.popular_js)


# ── 구름이 성장 시스템 v1(9차 G2) — /quiz/ 자리표시자 + growth.js 배선 ──────────────
class WebGurumiGrowthTest(unittest.TestCase):
    """구름이 성장 시스템 v1 — 게스트 기본은 localStorage(듀오링고식·무랭킹). 11차부터
    로그인 시 서버 보관(growth-sync.js — WebGurumiGrowthSyncTest)이 얹히지만 growth.js
    자체는 네트워크 0 을 유지한다(비로그인 시 전송 0 계약). 성장 패널 마크업·수치는 전부
    assets/growth.js 가 런타임 주입(결정론 골든 불침범) — 서버 렌더는 hidden 자리표시자
    1줄뿐. quiz.js 는 무수정(채점·주차 회전 계약 불변 — growth.js 가 .qz-choice 클릭을
    문서 위임으로 관찰만). 여기선 정적 셸·자산 배선·비로그인 전송 0 가드·스키마 버전
    마커를 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_growth_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.quiz = (cls.single / "quiz" / "index.html").read_text(encoding="utf-8")
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.growth_js = (WEB_DIR / "assets" / "growth.js").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_placeholder_static_hidden_and_before_tools(self):
        # 자리표시자는 hidden 정적 1줄(내용 0) — JS 미로드 시 그대로 숨는다(PE).
        self.assertIn('id="grm-growth" hidden aria-label="구름이 성장 현황"></section>', self.quiz)
        self.assertLess(self.quiz.index('id="grm-growth"'), self.quiz.index('id="grm-qz"'))

    def test_growth_js_wired_with_hash_and_copied_verbatim(self):
        import re as _re
        m = _re.search(r'assets/growth\.js\?v=([0-9a-f]{8})"', self.quiz)
        self.assertIsNotNone(m, "growth.js 캐시버스팅 해시 미발견")
        built = (self.single / "assets" / "growth.js").read_bytes()
        src = (WEB_DIR / "assets" / "growth.js").read_bytes()
        self.assertEqual(built, src, "growth.js 가 dist/assets 에 verbatim 복사되지 않음")

    def test_growth_js_no_network_apis(self):
        # 비로그인 시 전송 0(하이브리드 계약) — 게스트 저장 경로(growth.js)는 네트워크 API
        # 일절 미사용. 로그인 시 push 는 growth-sync.js(reactions_enabled 게이트) 전용 경로다.
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource"):
            self.assertNotIn(banned, self.growth_js, f"growth.js 네트워크 API 금지 위반: {banned}")

    def test_growth_js_reloads_memory_copy_on_sync(self):
        # [11차] growth-sync.js 병합 통지 수신 — 메모리 사본 재적재(이후 record() 가 병합
        # 사실을 덮어쓰지 않게). 비로그인·growth-sync 미로드 환경에선 이벤트가 없어 무영향.
        self.assertIn('addEventListener("grm:gurumi-sync"', self.growth_js)

    def test_growth_js_schema_version_and_storage_key(self):
        # 스키마 version 필드(서버 동기화 v1 도 같은 스키마 계약)·전용 키 — 마커 가드.
        self.assertIn("SCHEMA_VERSION = 1", self.growth_js)
        self.assertIn('"grm-gurumi-growth"', self.growth_js)
        self.assertIn("localStorage", self.growth_js)

    def test_growth_js_respects_reduced_motion_and_decorative_art(self):
        self.assertIn("prefers-reduced-motion", self.growth_js)
        self.assertIn('aria-hidden="true"', self.growth_js)   # 단계 아트 = 장식(수치는 텍스트 병행)

    def test_growth_js_observes_quiz_without_touching_grading(self):
        # quiz.js 채점 계약 불변 — growth.js 는 data-answer/data-i 읽기와 위임 수신만.
        self.assertIn('closest(".qz-choice")', self.growth_js)
        self.assertNotIn("data-done", self.growth_js)          # 채점 상태 마킹은 quiz.js 소유
        self.assertNotIn("is-correct", self.growth_js)         # 채점 UI 클래스 미조작

    def test_quiz_and_landing_copy_reflect_local_records(self):
        # "기록 미저장" 카피는 성장 시스템과 모순 — 로컬 저장 명시 카피로 교체됐다.
        self.assertNotIn("순위나 기록은 남기지 않으니", self.quiz)
        self.assertIn("이 브라우저에만 저장", self.quiz)
        self.assertIn("풀수록 구름이가 자라나요", self.landing)

    def test_stage_ladder_five_stages(self):
        # 5단계 사다리(알→아기→소년→어른→전설) — 명칭·순서 가드(카피 조정 시 의도 확인).
        for name in ("알", "아기 구름이", "소년 구름이", "어른 구름이", "전설 구름이"):
            self.assertIn(name, self.growth_js)

    def test_growth_atlas_is_local_accessible_and_motion_safe(self):
        # 성장 도감은 같은 정적 SVG를 재사용하고, 키보드 토글·ESC 닫기·모션 최소화를 지원한다.
        self.assertIn('id="grm-qzg-atlas-toggle"', self.growth_js)
        self.assertIn('aria-expanded="false"', self.growth_js)
        self.assertIn('aria-controls="grm-qzg-atlas"', self.growth_js)
        self.assertIn('e.key === "Escape"', self.growth_js)
        for stage in ("egg", "baby", "youth", "adult", "legend"):
            self.assertIn(f"qzg-character-{stage}", self.growth_js)
        for detail in ("qzg-crack-glow", "qzg-baby-shell", "qzg-first-card", "qzg-brief", "qzg-legend-halo"):
            self.assertIn(detail, self.growth_js)
        self.assertIn("prefers-reduced-motion:reduce", self.quiz)
        self.assertIn("animation:none!important", self.quiz)


# ── 구름이 서버 동기화(11차) — growth-sync.js 배선 + 하이브리드 계약 가드 ──────────
class WebGurumiGrowthSyncTest(unittest.TestCase):
    """구름이 서버 동기화(11차) — 하이브리드: 게스트는 localStorage 만으로 완전 동작하고,
    로그인하면 growth v1 데이터를 gurumi_growth(032)에 보관해 기기 간 유지한다.
    growth-sync.js 는 reactions_enabled 게이트 안에서만 로드되고(reactions.js 관례 동형)
    비로그인·supabase-js 부재·032 미적용·네트워크 실패는 전부 조용한 로컬 폴백 —
    032 적용 전에 머지돼도 사이트 완전 정상(디커플링 계약). 병합·전송은 브라우저 런타임
    소관이라 여기선 자산 배선·env-gate·세션 재사용·사실만 저장·병합 결정론·펫 패널 CTA
    카피의 정적 계약 마커를 가드한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_growthsync_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.sync_js = (WEB_DIR / "assets" / "growth-sync.js").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_script_env_gated_after_reactions(self):
        # 테스트 환경엔 SUPABASE 미설정(reactions_enabled=False) — 기본 렌더엔 무흔적
        # (골든 불침범, popular.js/reactions.js 관례 동형).
        self.assertNotIn("assets/growth-sync.js", self.landing)
        self.assertNotIn("grm-pet-sync", self.landing)

        u0, k0 = render.SUPABASE_URL, render.SUPABASE_ANON_KEY
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_growthsync_on_"))
        try:
            render.SUPABASE_URL = "https://rfwixqqdljpmtjdlblct.supabase.co"
            render.SUPABASE_ANON_KEY = "anon-key"
            out = tmp / "out"
            render.render_site(SINGLE_FIXTURES, out)
            landing_on = (out / "index.html").read_text(encoding="utf-8")
        finally:
            render.SUPABASE_URL, render.SUPABASE_ANON_KEY = u0, k0
            shutil.rmtree(tmp, ignore_errors=True)
        import re as _re
        m = _re.search(r'assets/growth-sync\.js\?v=([0-9a-f]{8})"', landing_on)
        self.assertIsNotNone(m, "growth-sync.js 캐시버스팅 해시 미발견(활성 렌더)")
        # supabase-js 라이브러리·reactions.js(세션 인프라) 로드 뒤에 온다.
        self.assertLess(landing_on.index("assets/reactions.js"),
                        landing_on.index("assets/growth-sync.js"))

    def test_sync_js_copied_to_dist(self):
        built = (self.single / "assets" / "growth-sync.js").read_bytes()
        src = (WEB_DIR / "assets" / "growth-sync.js").read_bytes()
        self.assertEqual(built, src, "growth-sync.js 가 dist/assets 에 verbatim 복사되지 않음")

    def test_reuses_reactions_session_infra_and_only_032_table(self):
        # firm.js 관례 — 같은 storageKey 로 세션 공유(신규 인증 코드·secret 0).
        self.assertIn('storageKey: "grm-public-auth-v1"', self.sync_js)
        self.assertIn("detectSessionInUrl: false", self.sync_js)
        # DB 접근은 032 테이블 하나뿐(reaction 등 타 테이블 미접근).
        import re as _re
        self.assertEqual(set(_re.findall(r'\.from\((\w+)\)', self.sync_js)), {"TABLE"})
        self.assertIn('var TABLE = "gurumi_growth";', self.sync_js)

    def test_server_payload_is_facts_only_v1(self):
        # 서버엔 사실만(version·weeks — growth.js v1 스키마 그대로). 파생값(점수·단계·
        # 이름·스트릭)은 페이로드에 없다 — 재계산 원칙 유지.
        self.assertIn("user_id: session.user.id, version: SCHEMA_VERSION, weeks: merged", self.sync_js)
        self.assertIn("var SCHEMA_VERSION = 1;", self.sync_js)
        for banned in ("points:", "stage:", "streak:", "stageIndex"):
            self.assertNotIn(banned, self.sync_js, f"파생값 서버 저장 금지 위반: {banned}")

    def test_guest_zero_transmission_guards(self):
        # 비로그인 전송 0 — sync()·schedulePush() 모두 session.user 가드로 시작(이중 방어:
        # 게이트 밖 기본 렌더에선 스크립트 자체가 미로드).
        self.assertEqual(self.sync_js.count("if (!session || !session.user) return;"), 2)

    def test_merge_rule_deterministic_union(self):
        # 병합 = week×문항 union(유실 0), 동일 키 충돌 = Math.max(정답 1 우선 — 교환·결합·
        # 멱등이라 병합 순서 무관 수렴), idx 충돌 = Math.min(손상 대비 결정론 규칙).
        self.assertIn("function mergeWeeks(", self.sync_js)
        self.assertIn("Math.max(out[k].q[id], v)", self.sync_js)
        self.assertIn("Math.min(out[k].idx, w.idx)", self.sync_js)
        # push 는 항상 pull→병합→upsert(수렴 업서트) — 맹목 덮어쓰기 경로 없음.
        self.assertIn('.select("version,weeks").maybeSingle()', self.sync_js)
        self.assertIn('{ onConflict: "user_id" }', self.sync_js)

    def test_sync_notifies_growth_and_pet(self):
        # 병합 반영 통지 — growth.js 메모리 재적재(grm:gurumi-sync)·pet.js 재파생
        # (grm:gurumi-change). 자기 통지로 push 재스케줄 안 함(selfNotify).
        self.assertIn('dispatchEvent(new CustomEvent("grm:gurumi-sync"))', self.sync_js)
        self.assertIn('dispatchEvent(new CustomEvent("grm:gurumi-change"))', self.sync_js)
        self.assertIn("selfNotify", self.sync_js)

    def test_pet_panel_cta_copy_no_pressure(self):
        # 펫 패널 CTA — 확정 카피(보관 프레이밍). 강요 톤·게스트 경험 폄하 문구 금지.
        self.assertIn("구름이 안전하게 보관하기", self.sync_js)
        self.assertIn("로그인하면 어느 기기에서든 이어서 키울 수 있어요", self.sync_js)
        self.assertIn("구름이가 계정에 안전하게 보관되고 있어요", self.sync_js)
        for banned in ("로그인해야", "사라져요", "잃어버", "지워져요"):
            self.assertNotIn(banned, self.sync_js, f"강요 톤 금지 위반: {banned}")
        # 기존 로그인 플로우 재사용(firm.js 관례 — 헤더 로그인 버튼 클릭 위임).
        self.assertIn('".grm-auth .grm-acct-login"', self.sync_js)

    def test_migration_032_contract_markers(self):
        # 032 는 작성만(적용은 컨트롤타워 dry-run 후) — 접근 계약 마커를 정적 가드한다.
        sql = (WEB_DIR / "migrations" / "032_gurumi_growth_sync.sql").read_text(encoding="utf-8")
        self.assertIn("create table if not exists public.gurumi_growth", sql)
        self.assertIn("alter table public.gurumi_growth enable row level security", sql)
        for pol in ("gurumi_growth_select_own", "gurumi_growth_insert_own", "gurumi_growth_update_own"):
            self.assertIn(pol, sql)
        self.assertNotIn("for delete", sql)                     # delete 정책 없음(경로 봉쇄)
        self.assertIn("revoke all on public.gurumi_growth from anon", sql)
        self.assertIn("grant select, insert, update on public.gurumi_growth to authenticated", sql)
        self.assertNotIn("to anon", sql)                        # anon 재부여 없음(공개 read 0)
        self.assertIn("check (version = 1)", sql)               # v1 스키마 고정
        self.assertIn("jsonb_typeof(weeks) = 'object'", sql)


# ── 주간 퀴즈 week 필드(9차 G3) — 파이프라인 지정 주차 우선 + 회전 보충 ────────────
class WebQuizWeekFieldTest(unittest.TestCase):
    """뱅크 항목 선택 필드 week(YYYYWW): 있으면 해당 주차 문항을 "이번 주"로 우선 선정
    +부족분은 기존 회전 보충, 없으면(현 데이터) 기존 회전과 완전 동일(무회귀). 서버는
    week 를 data-week 로 무변형 embed 만 하고 선택은 quiz.js 순수 함수(pickWeeklyIndexes)
    소관 — 두 경로는 node 로 실제 quiz.js 를 실행해 고정한다(node 부재 환경은 skip —
    CI ubuntu 러너는 node 내장)."""

    def test_view_passes_week_string_only_when_present(self):
        with_week = render._quiz_question_view({"id": "q-w", "week": 202629})
        without = render._quiz_question_view({"id": "q-n"})
        self.assertEqual(with_week["week"], "202629")   # int → 문자열 정규화(값 무변형)
        self.assertEqual(without["week"], "")

    def test_template_emits_data_week_only_when_present(self):
        synthetic = [
            {"id": "q-w1", "question_ko": "주차 지정 문항?", "choices": ["a", "b", "c", "d"],
             "answer_index": 0, "explanation_ko": "설명.", "difficulty": "easy",
             "source_type": "glossary", "source_ref": "gmp", "week": 202629},
            {"id": "q-n1", "question_ko": "무주차 문항?", "choices": ["a", "b", "c", "d"],
             "answer_index": 1, "explanation_ko": "설명.", "difficulty": "normal",
             "source_type": "glossary", "source_ref": "gmp"},
        ]
        orig = render.load_quiz_bank
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_quizweek_"))
        try:
            render.load_quiz_bank = lambda *a, **k: synthetic
            out = tmp / "out"
            render.render_site(SINGLE_FIXTURES, out)
            html = (out / "quiz" / "index.html").read_text(encoding="utf-8")
        finally:
            render.load_quiz_bank = orig
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn('id="q-w1"', html)
        self.assertIn('data-week="202629"', html)
        # 무주차 카드엔 data-week 자체가 없다(속성 생략 — 기존 마크업 모양 보존).
        card_n = html[html.index('id="q-n1"'):html.index('id="q-n1"') + 400]
        self.assertNotIn('data-week="', card_n)

    def test_current_bank_weeks_are_valid_and_golden_matches_bank(self):
        # 주간 생성이 시작된 뒤에도 week 는 YYYYWW 문자열만 허용하고, 골든의
        # data-week 는 뱅크에 실재하는 주차만 담는다(임의 주차 혼입 방지).
        bank = json.loads(render.QUIZ_FILE.read_text(encoding="utf-8"))
        weeks = {str(q["week"]) for q in bank if "week" in q}
        for week in sorted(weeks):
            self.assertRegex(week, r"^\d{4}(0[1-9]|[1-4]\d|5[0-3])$")
        golden = (GOLDEN_DIR / "quiz.expected.html").read_text(encoding="utf-8")
        # data-weekly-count(별개 속성)와 구분되도록 값까지 포함해 추출한다.
        self.assertEqual(set(re.findall(r'data-week="(\d+)"', golden)) - weeks, set())

    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — 선택 로직 경로 고정은 CI에서 수행")
    def test_pick_weekly_indexes_both_paths_pinned_via_node(self):
        import subprocess
        driver = r"""
global.window = {};
global.document = { getElementById: function () { return null; },
                    querySelectorAll: function () { return []; } };
require(process.argv[2]);            // quiz.js — GRM_QUIZ 부착 후 root 가드에서 조기 종료
var f = global.window.GRM_QUIZ.pickWeeklyIndexes;
function mk() { var a = []; for (var i = 0; i < 12; i++)
  a.push({ index: i, difficulty: i < 8 ? "easy" : "normal", week: "" }); return a; }
var out = {};
out.noweek = f(mk(), 4, 202629);                    // week 전무 → 기존 회전 경로
var w = mk(); w[5].week = "202629"; w[10].week = "202629";
out.week = f(w, 4, 202629);                         // 지정 2 + 회전 보충 2
var o = mk(); o[5].week = "202629"; o[10].week = "202629"; o[3].week = "202630";
out.other = f(o, 4, 202629);                        // 타 주차 지정은 rest 취급
var all = mk(); all.forEach(function (it) { it.week = "202629"; });
out.overflow = f(all, 4, 202629);                   // 지정 초과 → 뱅크 순 상위 count
console.log(JSON.stringify(out));
"""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_quizjs_"))
        try:
            drv = tmp / "driver.js"
            drv.write_text(driver, encoding="utf-8")
            proc = subprocess.run(
                ["node", str(drv), str(WEB_DIR / "assets" / "quiz.js")],
                capture_output=True, text=True, timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
        out = json.loads(proc.stdout)
        # 기존 회전 경로(무week) — G3 이전 알고리즘 산출과 동일한 고정값(무회귀 앵커):
        # easy 8·normal 4, seed 202629 → baseE=mod(607887,8)=7 → easy 7,0,1 · baseN=1 → normal idx9.
        self.assertEqual(out["noweek"], [0, 1, 7, 9])
        # 지정 2(5·10) + 보충: poolE 7건 baseE=mod(607887,7)=0 → idx0 · poolN 3건 baseN=0 → idx8.
        self.assertEqual(out["week"], [0, 5, 8, 10])
        # 타 주차(202630) 지정 문항은 이번 주 선정에 영향 없음(rest 로만 참여).
        self.assertEqual(out["other"], [0, 5, 8, 10])
        # 전 문항 지정 → 뱅크 순 상위 4.
        self.assertEqual(out["overflow"], [0, 1, 2, 3])


# ── 로그인/가입 마찰 개선(12차) ───────────────────────────────────────────────
class WebLoginFrictionTest(unittest.TestCase):
    """가입 마찰 3종 개선의 정적 계약을 가드한다.
    ① openLogin({mode:"signup"}) 가입 직행(펫 CTA) — 로그인 화면 경유 1클릭 제거,
       단 "이미 계정이 있어요" 전환은 가입 화면에 상시.
    ② 가입 진행 상태 sessionStorage 보존 → 팝업을 닫았다 열어도 코드 입력 단계로 복원
       (재전송 쿨다운 30초 유지·세션 정본 grm-public-auth-v1 불침범·비밀번호/토큰 미저장).
    ③ 미확인 계정 로그인 실패 분기 — Supabase 공식 코드 email_not_confirmed 기반
       (분류는 순수 함수 classifyAuthError, 두 경로를 node 로 실제 실행해 고정).
    하트/스크랩 반응 로직은 무수정이어야 한다(회귀 앵커)."""

    @classmethod
    def setUpClass(cls):
        cls.js = (WEB_DIR / "assets" / "reactions.js").read_text(encoding="utf-8")
        cls.sync_js = (WEB_DIR / "assets" / "growth-sync.js").read_text(encoding="utf-8")

    def test_signup_direct_mode_and_public_entry(self):
        self.assertIn("window.GRM_AUTH.open = openLogin;", self.js)
        self.assertIn('setMode(o.mode === "signup" ? "signup" : "login");', self.js)
        # 가입 화면엔 로그인 전환이 항상 있다(막다른 길 0).
        self.assertIn('addLink("이미 계정이 있어요 · 로그인", "login")', self.js)
        # 펫 CTA(가입 의도 분명) → 가입 직행 + reactions.js 미로드 시 기존 헤더 위임 폴백.
        self.assertIn('window.GRM_AUTH.open({ mode: "signup" })', self.sync_js)
        self.assertIn('querySelector(".grm-auth .grm-acct-login")', self.sync_js)

    def test_signup_progress_restore_contract(self):
        self.assertIn('var SIGNUP_KEY = "grm-signup-progress-v1";', self.js)
        self.assertIn("var resume = loadSignupProgress();", self.js)
        self.assertIn('setMode("confirm");', self.js)
        # 성공(세션 성립)·명시적 이탈에서 진행 상태를 지운다 — 유령 복원 0.
        self.assertIn("clearSignupProgress(); closeLogin();", self.js)
        self.assertIn('addLink("다른 이메일로 가입", "signup"', self.js)
        # 저장 대상은 이메일+시각뿐(비밀번호·토큰·세션 미저장) + 30분 만료.
        self.assertIn("JSON.stringify({ email: email, ts: Date.now() })", self.js)
        self.assertIn("var SIGNUP_TTL_MS = 30 * 60 * 1000;", self.js)
        self.assertNotIn("sessionStorage.setItem(SIGNUP_KEY, JSON.stringify({ email: email, pw", self.js)

    def test_session_and_reaction_logic_untouched(self):
        # 세션 정본(공유 storageKey)·로그인 성공 경로·하트/스크랩 토글은 불변.
        self.assertIn('storageKey: "grm-public-auth-v1"', self.js)
        self.assertIn("sb.auth.signInWithPassword({ email: email, password: pw })", self.js)
        self.assertIn('sb.from("reaction").insert({ user_id: uid, card_id: id, kind: kind })', self.js)
        self.assertIn('sb.from("reaction").delete().match({ user_id: uid, card_id: id, kind: kind })', self.js)
        # 이메일 코드 방식 유지(매직링크 전환 금지 — 스캐너 토큰 선소모 회피 설계).
        self.assertIn('type: "signup"', self.js)
        self.assertIn('type: "recovery"', self.js)
        self.assertNotIn("signInWithOtp", self.js)
        self.assertNotIn("signInWithOAuth", self.js)
        # 재전송 쿨다운 30초 유지.
        self.assertIn("var left = 30;", self.js)

    def test_copy_tone_has_no_threat_or_jargon(self):
        # 검사 대상은 **화면에 나가는 문구**뿐 — 개발자 주석(설계 근거라 규제·API 용어가
        # 정상적으로 등장한다)은 제외한다. reactions.js 엔 "://" 가 없어(URL 0) 줄 주석
        # 제거가 문자열을 훼손하지 않는다.
        import re as _re
        body = _re.sub(r"/\*.*?\*/", "", self.js, flags=_re.S)
        body = "\n".join(ln.split("//")[0] for ln in body.splitlines())
        for bad in ["오류 코드", "인증 토큰", "OTP", "실패했습니다. 관리자", "차단"]:
            self.assertNotIn(bad, body, f"대중성 톤 위반 후보: {bad}")
        self.assertIn("가입 확인이 아직이에요", self.js)
        self.assertIn("코드가 맞지 않거나 시간이 지났어요", self.js)

    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — 분류 경로 고정은 CI에서 수행")
    def test_classify_auth_error_paths_pinned_via_node(self):
        import subprocess
        driver = r"""
global.window = {};
global.document = { getElementById: function () { return null; },
                    querySelectorAll: function () { return []; } };
require(process.argv[2]);      // reactions.js — GRM_AUTH 부착 후 env-gate 에서 조기 종료
var f = global.window.GRM_AUTH.classifyAuthError;
var out = {};
out.code_unconfirmed = f({ code: "email_not_confirmed", message: "" });
out.msg_unconfirmed   = f({ message: "Email not confirmed" });          // code 없는 옛 클라이언트
out.invalid           = f({ code: "invalid_credentials", message: "Invalid login credentials" });
out.msg_invalid       = f({ message: "Invalid login credentials" });
out.exists            = f({ code: "user_already_exists", message: "" });
out.rate              = f({ code: "over_email_send_rate_limit", message: "" });
out.expired           = f({ code: "otp_expired", message: "" });
out.weak              = f({ code: "weak_password", message: "" });
out.unknown           = f({ code: "something_new", message: "Boom" });
out.empty             = f(null);
console.log(JSON.stringify(out));
"""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_authjs_"))
        try:
            drv = tmp / "driver.js"
            drv.write_text(driver, encoding="utf-8")
            proc = subprocess.run(
                ["node", str(drv), str(WEB_DIR / "assets" / "reactions.js")],
                capture_output=True, text=True, timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
        out = json.loads(proc.stdout)
        self.assertEqual(out["code_unconfirmed"], "unconfirmed")
        self.assertEqual(out["msg_unconfirmed"], "unconfirmed")
        self.assertEqual(out["invalid"], "invalid_credentials")
        self.assertEqual(out["msg_invalid"], "invalid_credentials")
        self.assertEqual(out["exists"], "exists")
        self.assertEqual(out["rate"], "rate_limit")
        self.assertEqual(out["expired"], "expired_code")
        self.assertEqual(out["weak"], "weak_password")
        # 미지 오류는 뭉뚱그린 기존 문구로 떨어진다(추측 분기 0).
        self.assertEqual(out["unknown"], "unknown")
        self.assertEqual(out["empty"], "unknown")


if __name__ == "__main__":
    if "--freeze" in sys.argv:
        freeze()
    else:
        unittest.main()
