#!/usr/bin/env python3
"""웹 렌더러(P2) 골든·결정론·무변형·escape 테스트.

CI(`unittest discover -s tests`)는 `tests/test_web_render.py` shim 을 통해 이 모듈을
순회한다. 직접 실행 시:
  python web/tests/test_render.py            # 테스트 실행
  python web/tests/test_render.py --freeze   # 골든 (재)동결

골든 시나리오:
  · 단독(web/data/briefs, 실 6/22 1건) → landing / archive / brief_2026-06-22
  · 멀티(합성 06-08·06-15 + 실 6/22 결합) → archive_multi / landing_multi /
    brief_2026-06-08(산문·번역 ①②) / brief_2026-06-15(병합 토글)
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
sys.path.insert(0, str(WEB_DIR))
import render  # noqa: E402  (web/render.py — 경로 삽입 후 import)

TESTS_DIR = pathlib.Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
MULTI_FIXTURES = TESTS_DIR / "fixtures" / "multi"            # 합성 2건만
DATA_DIR = WEB_DIR / "data" / "briefs"                       # 실 6/22
REAL_FIXTURE = DATA_DIR / "brief_web_2026_06_22.json"

__all__ = [
    "WebRenderGoldenTest",
    "WebRenderStructureTest",
    "WebRenderFidelityTest",
    "WebRenderDeterminismTest",
    "WebRenderPurityTest",
    "WebRenderHardeningTest",
    "WebSearchIndexTest",
    "WebKoreanSafetyTest",
    "WebSeoMetaTest",
    "WebDeterministicDetailTest",
]


# ── 빌드 헬퍼 (테스트·freeze 공용 — 동일 입력 보장) ───────────────────────────
def _build_single(out: pathlib.Path) -> None:
    render.render_site(DATA_DIR, out)


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
        self.assertIn('href="assets/grm.css"', landing)             # 깊이 0
        self.assertIn('href="../assets/grm.css"', archive)          # 깊이 1
        self.assertIn('href="../../assets/grm.css"', self.detail)   # 깊이 2
        # 브리프 상세 nav 링크는 ../../ 접두.
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
        briefs = render.load_briefs(DATA_DIR)
        issue_no = render.assign_issue_numbers(briefs)
        latest = max(b["brief"].get("publish_date", "") for b in briefs)
        cls.idx = render.build_search_index(briefs, issue_no, latest)
        # 06-26 발행으로 단독 데이터 디렉터리도 2호(06-22·06-26)가 됨 — 인덱스는 전 호의
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
        rc = [c for c in self.cards if render._is_renderable(c)]
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


if __name__ == "__main__":
    if "--freeze" in sys.argv:
        freeze()
    else:
        unittest.main()
