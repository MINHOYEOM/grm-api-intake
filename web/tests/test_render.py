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

import collections
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
import grm_i18n  # noqa: E402  (web/grm_i18n.py — 다국어 문구 사전·검사기)
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


# ── [B3] reg_refs 링크 조용한 소실 가드 — 실측 하한선/상한선 ─────────────────────
# 배경: 용어사전(/glossary/)의 조항 링크(reg_refs[].url)는 자료실 카탈로그
# (web/data/library/*.json)의 주소를 빌려 쓴다(render._reg_ref_url, B2). 자료실은
# 매주 자동 갱신되고 그때 골든도 자동 재동결되어 커밋된다 — 그래서 카탈로그에서 문서
# 코드가 사라지거나 이름이 바뀌면 용어사전 링크가 **아무 경고 없이** 사라지고, 골든은
# 그 사라진 상태로 다시 도장 찍힌다. 화면은 멀쩡해 보이고 테스트도 통과한다. 이게 이
# 저장소에서 반복돼 온 "조용한 실패" 패턴이다.
#
# 아래 숫자는 추측이 아니다 — 2026-08-04, 이 저장소에 커밋된 glossary.json 전 항목을
# render.build_glossary_view(terms, render._load_reg_ref_catalogs()) 로 실제로 돌려
# 얻은 실측치다(WebGlossaryRegRefLinkGuardTest.setUpClass 와 동일 호출). 이 숫자보다
# **줄면** 링크가 사라진 것이고, 무링크 라벨이 이 숫자보다 **늘면** 새로운 결손이다.
_REG_REF_RESOLVED_FLOOR = 469          # 실측: url 이 비어있지 않은 칩 469/503건
_REG_REF_FAMILY_FLOORS = {             # 실측: 계열별 url 비어있지 않은 칩 개수
    "cfr": 141, "ich": 109, "eu_gmp": 184, "pics": 23, "who": 12,
}
# 실측: 링크가 안 붙는 고유 라벨 16종(21 CFR 범위 표기·EU GMP Annex 19 두 판본 모호·
# EU GMP Part III Site Master File(모호)·MHRA GxP Data Integrity Guidance(자료실
# 카탈로그 없음)·국내 법령 4종(자료실에 해당 문서 미보유) — R7 계열 밖이거나 라벨이
# 카탈로그를 유일하게 특정하지 못하는 경우. _reg_ref_url 은 "틀린 링크보다 무링크가
# 안전"이라 이런 경우 의도적으로 "" 를 반환한다(버그 아님).
#
# 2026-08-04 용어 26개 추가(트랙③) 반영: 칩 408→455, 해석 377→421(+44). 새 용어의
# reg_refs 는 21 CFR·ICH·EU GMP·PIC/S·WHO 라벨이라 전부 자료실 카탈로그로 해석되고,
# 무링크로 남는 건 국내 법령 3종(약사법 제39조·의약품 등의 안전에 관한 규칙 [별표 1]·
# 같은 규칙 제50조)뿐이다 — mfds.json 카탈로그가 고시·가이드라인만 보유하고 법률·
# 총리령 본문은 담지 않기 때문(R6 접두 분기에도 걸리지 않아 R7 로 떨어진다).
#
# 2026-09-02 복제 본문 해소 12어 재작성 + 신규 16어 반영: 칩 455→503, 해석 421→469(+48).
# 계열별 cfr 115→141·ich 103→109·eu_gmp 169→184·pics 23·who 11→12. 새 라벨(ICH Q2(R2)
# §3.3.x·ICH Q7 §12.30/§§11.40–11.44/§§14.50–14.52·EU GMP Part I Chapter 2~6·Part II·
# WHO TRS 992 Annex 4·21 CFR 210.3/211.25/211.56/211.103/211.111/211.125/211.150/211.182/
# 211.186/211.196/211.204)은 전부 자료실 카탈로그로 해석되고 무링크 라벨은 16종 그대로다.
_REG_REF_KNOWN_UNRESOLVED_LABELS = frozenset({
    "21 CFR 211.160–211.194",
    "21 CFR 211.180–211.194",
    "EU GMP Annex 19 §§1–4",
    "EU GMP Annex 19 §§7–9",
    "EU GMP Part III, Site Master File Explanatory Notes",
    "MHRA GxP Data Integrity Guidance §6.13",
    "MHRA GxP Data Integrity Guidance §6.17",
    "MHRA GxP Data Integrity Guidance §6.8",
    "MHRA GxP Data Integrity Guidance §§4.3, 6.17.1",
    "MHRA GxP Data Integrity Guidance §§6.2, 6.11.1",
    "MHRA GxP Data Integrity Guidance §§6.2, 6.11.1, 6.17.1",
    "PIC/S PE 009-17 Annex 19",
    "약사법 제39조",
    "의약품 등의 안전에 관한 규칙 [별표 1]",
    "의약품 등의 안전에 관한 규칙 제50조",
    "의약품 제조 및 품질관리에 관한 규정 [별표 1]",
})
_REG_REF_UNRESOLVED_LABEL_CAP = len(_REG_REF_KNOWN_UNRESOLVED_LABELS)  # 16, 실측


def _classify_reg_ref_family(label: str) -> str | None:
    """[B3] reg_ref 라벨 → 계열 키(render._reg_ref_url 의 R1~R5 접두 분기와 동일 규칙).

    render.py 를 import 해서 내부 분기 로직을 재사용하지 않는 이유: 이 함수는 render.py
    편집 금지 제약 아래 테스트 파일에서만 계열 집계용으로 쓰는 얕은 라벨 분류기라,
    렌더러 내부 구현과 결합시키지 않는 편이 안전하다(테스트가 구현 세부에 뒤엉키지
    않도록 접두 문자열만 본다 — R1~R5 가 상호 배타적 접두라 순서 무관)."""
    if label.startswith("21 CFR"):
        return "cfr"
    if label.startswith("ICH "):
        return "ich"
    if label.startswith("EU GMP "):
        return "eu_gmp"
    if label.startswith("PIC/S "):
        return "pics"
    if label.startswith("WHO "):
        return "who"
    return None


# ── [A3] FDA 표현 → 용어 도달 가드 — 탐침 표(실측치 근거) ─────────────────────────
# 배경: 용어사전은 유럽·ICH 어휘로 쓰였는데 실제 지적사항은 미국 FDA 문서가 대부분이다.
# 같은 개념을 다르게 불러서, 사용자가 FDA 문서에서 본 말로 검색하면 아무것도 안 나왔다.
#
# 구현 전 실측(2026-08-03/04, aliases 가 search 에 배선되기 전 — WebGlossaryAliasGuardTest
# 와 동일한 판정 방법으로 직접 확인): 아래 15개 FDA 표현으로 검색했을 때 **도달 0/15**
# 였다. 개념(retention-sample·quality-unit 등)은 이미 사전에 있었지만, 사용자가 FDA
# 문서에서 본 이름으로는 하나도 찾을 수 없었다.
#
# 판정 방법은 클라이언트(assets/glossary.js)가 실제로 하는 것과 동일하다(파일 직접
# 확인, 08-04):
#   var q = input.value.trim().toLowerCase();
#   var hit = q === "" || (terms[i].getAttribute("data-search") || "").indexOf(q) !== -1;
# data-search 는 템플릿(glossary.html)이 `{{ t.search }}` 로 채우고, build_glossary_view
# 가 만드는 t["search"] 는 이미 소문자 결합이다 — 그래서 이 파일에서도
# "검색어.lower() in t['search']" 로 같은 부분일치 판정을 재현한다(단어경계 아님, JS
# indexOf 와 동일 의미론).
#
# (검색어, 닿아야 할 용어 id) 쌍 — web/data/glossary.json 에 사람이 코퍼스 실측으로
# 하나씩 판정해 커밋한 aliases 데이터가 근거다(이 파일은 그 데이터를 고치지 않는다).
_FDA_ALIAS_PROBES: tuple[tuple[str, str], ...] = (
    ("reserve sample", "retention-sample"),
    ("method validation", "analytical-procedure-validation"),
    ("annual product review", "product-quality-review"),
    # ↓ 이 둘은 **동의어가 아니라 정식 표제어**로 닿는다(2026-08-04 용어 증설이
    #   'Quality Control Unit (QCU)'·'Written Procedures' 를 표제어로 올렸다). 같은 말을
    #   표제어와 동의어가 둘 다 주장하면 사용자가 틀린 카드를 보므로 quality-unit·sop
    #   쪽 동의어는 뺐다. 검색 도달이라는 약속은 그대로 지켜지는지가 여기서 검증된다.
    ("quality control unit", "quality-control-unit"),
    ("written procedure", "written-procedure"),
    ("qualified person", "authorized-person"),
    ("active substance", "api"),
    ("marketing authorisation", "marketing-authorization"),
    ("batch production record", "batch-record"),
    ("cGMP", "gmp"),
    ("lyophilisation", "lyophilization"),
    ("CCIT", "container-closure-integrity"),
    ("out of trend", "oot"),
    ("out of specification", "oos"),
    ("backup", "backup"),
)

# [A3] 위 15건 중, 화면 표시 목록에서는 감춰지는 동의어 2건(실측 08-04 —
# render.build_glossary_view 의 _glossary_alias_norm 판정을 직접 돌려 확인: "backup"→
# backup id 는 term_en "Back-up" 과, "out of trend"→oot id 는 term_en "Out-of-Trend
# (OOT) Result" 와 하이픈·공백 차이뿐이라 표시 목록(t["aliases"])에서 제외된다). 감춤은
# 화면 전용 판정이고 검색 문자열(t["search"])에는 영향을 주지 않아야 한다.
_HIDDEN_DISPLAY_ALIAS_PROBES: tuple[tuple[str, str], ...] = (
    ("backup", "backup"),
    ("out of trend", "oot"),
)

# [A3] 화면 카드에 실제로 그려져야 하는 표시 대상 동의어(표제어와 진짜 다른 이름) 대표
# 3건 — 위 15건 중 표시 목록에서 감춰지지 않는 것들.
_DISPLAYED_ALIAS_PROBES: tuple[tuple[str, str], ...] = (
    ("retention-sample", "reserve sample"),
    ("product-quality-review", "annual product review"),
    # quality-unit 의 'quality control unit' 은 뺐다 — 용어 증설(2026-08-04)이 같은 말을
    # 정식 표제어(quality-control-unit)로 올렸고, 표제어와 동의어가 같은 이름을 주장하면
    # 사용자가 틀린 카드를 본다. 대신 다른 계열에서 하나 고른다.
    ("authorized-person", "qualified person"),
)


# ── 빌드 헬퍼 (테스트·freeze 공용 — 동일 입력 보장) ───────────────────────────
# ★문서 단위 페이지(3천 장 초과)는 기본으로 끈다. 스위트는 사이트를 51번 다시 짓는데
#   한 번에 ~27초가 들어 그대로 두면 CI 가 23분 늘어난다. 끄더라도 **sitemap 에는 문서
#   URL 이 그대로 들어가므로**(render_site 가 데이터에서 파생) 골든 대조는 프로덕션과
#   동일하다. 실제 HTML 렌더는 `WebFindingsDocPageTest` 가 켠 채로 지어 전수 검증한다.
_DOC_PAGES_IN_TESTS = False


def js_function_body(src: str, header: str) -> str:
    """`header` 로 시작하는 JS 함수의 **본문 전체**.

    ★고정폭 슬라이스(`src[i:i+480]`)를 쓰지 않는 이유: 함수 안에 한 줄만 늘어도 검사
      대상이 창 밖으로 밀려 나가 테스트가 거짓으로 실패한다. 실제로 이 저장소에서
      320 → 400 → 480 으로 세 번 올라온 이력이 있고(주석에 그 이력이 남아 있었다),
      네 번째로 또 밀렸다. 폭은 코드가 자랄 때마다 낡는 손값이므로 **구조**로 자른다.
      findings.js 는 IIFE 안이라 함수가 2칸 들여쓰기로 시작하고 `\n  }` 로 닫힌다.
    """
    i = src.index(header)
    end = src.index("\n  }", i) + len("\n  }")
    return src[i:end]


def _build_single(out: pathlib.Path, *, doc_pages: bool = _DOC_PAGES_IN_TESTS) -> None:
    render.render_site(SINGLE_FIXTURES, out, render_doc_pages=doc_pages)


def _build_multi(out: pathlib.Path, scratch: pathlib.Path) -> None:
    """합성 2건 + 실 6/22 를 한 데이터 디렉터리로 결합해 빌드(런타임 결합=드리프트 0)."""
    data = scratch / "multi_data"
    data.mkdir(parents=True, exist_ok=True)
    for fp in sorted(MULTI_FIXTURES.glob("*.json")):
        shutil.copyfile(fp, data / fp.name)
    shutil.copyfile(REAL_FIXTURE, data / REAL_FIXTURE.name)
    render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)


def _build_resources(out: pathlib.Path) -> None:
    """[업계 브리핑 노트] 격리 픽스처(1건) 단독 빌드 — single/multi 와 완전 분리."""
    render.render_site(RESOURCE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)


# (built_relpath, golden_filename)
RESOURCE_GOLDENS = [
    ("briefs/2026-05-01/index.html", "brief_resources.expected.html"),
]


# (built_relpath, golden_filename)
SINGLE_GOLDENS = [
    ("index.html", "landing.expected.html"),
    # [다국어 3단계 2026-09-04] 영어 트리도 바이트로 잠근다 — 홈(별도 템플릿
    # landing_en.html)과 본체(지적사항 검색 셸) 두 장. 구조·정책은 WebEnTreeTest 가 보지만,
    # 문구 사전이 바뀌었을 때 영어 화면이 어떻게 달라지는지는 이 두 골든에만 드러난다.
    ("en/index.html", "en_landing.expected.html"),
    ("en/findings/index.html", "en_findings.expected.html"),
    ("archive/index.html", "archive.expected.html"),
    ("findings/index.html", "findings.expected.html"),
    # [2면 분리 2026-08-27] 둘러보기 면 — 위 주석 그대로: 손열거라 여기 없으면 골든 없이 산다.
    ("findings/browse/index.html", "findings_browse.expected.html"),
    ("findings/trends/index.html", "trends.expected.html"),
    # [존 재편 2026-08-26] 트렌드 존 신설 2면. ★이 목록은 손열거라 새 라우트를 넣는 걸
    #   잊으면 그 페이지는 **골든 없이 살게 된다**(초록인데 미검증). WebZoneIaTest 가
    #   도달성으로 라우트 누락을 잡지만, 바이트 고정은 여기 한 줄이 유일하다.
    ("findings/inspections/index.html", "inspections.expected.html"),
    ("findings/coverage/index.html", "coverage.expected.html"),
    ("findings/checklist/index.html", "checklist.expected.html"),
    ("findings/firm/index.html", "firm.expected.html"),
    ("findings/inspector/index.html", "inspector.expected.html"),
    ("library/index.html", "library.expected.html"),
    ("library/ich/index.html", "library_ich.expected.html"),
    ("library/mfds/index.html", "library_mfds.expected.html"),
    ("library/eu-gmp/index.html", "library_eu_gmp.expected.html"),
    ("library/pics/index.html", "library_pics.expected.html"),
    ("library/who/index.html", "library_who.expected.html"),
    ("library/fda-guidance/index.html", "library_fda_guidance.expected.html"),
    ("library/ema/index.html", "library_ema.expected.html"),
    ("library/health-canada/index.html", "library_health_canada.expected.html"),
    ("library/pmda/index.html", "library_pmda.expected.html"),
    ("library/cfr/index.html", "library_cfr.expected.html"),
    ("library/mhra/index.html", "library_mhra.expected.html"),
    ("guide/index.html", "guide.expected.html"),
    ("glossary/index.html", "glossary.expected.html"),
    ("quiz/index.html", "quiz.expected.html"),
    ("briefs/2026-06-22/index.html", "brief_2026-06-22.expected.html"),
    ("briefs/2026-06-26/index.html", "brief_2026-06-26.expected.html"),
    ("assets/search-index.json", "search-index.expected.json"),
    ("robots.txt", "robots.expected.txt"),
    ("llms.txt", "llms.expected.txt"),
    ("briefs/2026-06-26/share.txt", "brief_share.expected.txt"),
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
        render.render_site(DATA_DIR, cls.out, render_doc_pages=_DOC_PAGES_IN_TESTS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_landing_and_aggregates_built(self):
        for rel in ("index.html", "archive/index.html",
                    "assets/search-index.json", "sitemap.xml", "robots.txt",
                    "llms.txt"):
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

    def test_korean_line_breaking_is_a_global_default(self):
        """★[한국어 어절 줄바꿈 2026-08-12] CSS 기본 `word-break:normal` 은 한글을 **음절
        어디서나** 끊는다("디|에틸렌글리콜", "가이드라|인"). 이 규칙은 오래전부터 알고 있었는데
        **선택자마다 손으로** 붙여 왔고(grm.css 15곳 + 템플릿 25곳), 손목록에서 빠진 곳은
        전부 깨진 채였다 — 라이브 실측 브리프 본문 84건 · 용어사전 19건 · 자료실 8건.
        (findings 계열만 `main{word-break:keep-all}` 을 둬서 0건이었다.)

        모두가 개별로 적용하는 규칙은 **기본값**이어야 한다 → body 에 선언한다. 선언이
        사라지면 다음 신규 페이지부터 조용히 깨지므로 여기서 잠근다.

        overflow-wrap 은 `anywhere` 가 아니라 `break-word` 여야 한다 — anywhere 는
        min-content 폭 계산까지 바꿔 flex/grid 트랙 폭을 흔든다(전역으로 걸면 레이아웃
        회귀 반경이 사이트 전체가 된다)."""
        css = (WEB_DIR / "assets" / "grm.css").read_text(encoding="utf-8")
        m = re.search(r"\nbody\{([^}]*)\}", css)
        self.assertIsNotNone(m, "grm.css body 규칙 미발견")
        rule = m.group(1)
        self.assertIn("word-break:keep-all", rule, "전역 한글 줄바꿈 기본값이 사라졌다")
        self.assertIn("overflow-wrap:break-word", rule)
        self.assertNotIn("overflow-wrap:anywhere", rule)

    def test_page_head_paragraph_has_no_ch_cap(self):
        """★[62ch 캡 폐기 2026-08-12] `ch` 는 숫자 `0` 의 폭이라 한글에는 62ch ≈ 31자다 —
        컨테이너 1,180px 인데 609px 에서 접혔다. 그래서 **12개 템플릿 중 10개가 이미
        `max-width:none` 으로 뒤집어 쓰고 있었고**, 그 손목록에서 빠진 archive 만 결함으로
        남아 있었다. 모두가 뒤집는 기본값은 기본값이 틀린 것이다."""
        css = (WEB_DIR / "assets" / "grm.css").read_text(encoding="utf-8")
        m = re.search(r"\n\.page-head p\{([^}]*)\}", css)
        self.assertIsNotNone(m, "grm.css .page-head p 규칙 미발견")
        self.assertNotRegex(m.group(1), r"max-width:\s*\d+ch",
                            "page-head 본문에 ch 기반 폭 상한이 되살아났다(한글에서 조기 줄바꿈)")

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

    def test_tldr_copy_button_wired(self):
        """[성장 2차] 요약 복사 — tldr 있는 브리프에만 버튼이 실리고, 절대 URL(canonical)
        을 데이터 속성으로 가진다(hidden 은 JS 미실행 폴백 — 정적 열람 무영향)."""
        h = (self.multi / "briefs/2026-06-08/index.html").read_text(encoding="utf-8")
        self.assertIn('id="tldrCopy"', h)
        self.assertIn(f'data-url="{render.SITE_BASE_URL}/briefs/2026-06-08/"', h)
        self.assertIn('hidden><i class="ti ti-copy"', h)
        self.assertNotIn('id="tldrCopy"', self.detail)   # tldr 빈 브리프(06-22)엔 버튼도 없다

    def test_brief_share_txt(self):
        """[성장 3차] 공유 초안 share.txt — 브리프마다 고정 경로에 tldr verbatim + 절대
        URL. tldr 빈 브리프(06-22)도 파일은 있되 불릿 없이 헤더+링크만(경로 예측 가능성)."""
        s = (self.multi / "briefs/2026-06-08/share.txt").read_text(encoding="utf-8")
        self.assertIn("[GRM 주간 규제뉴스 · ", s)
        self.assertIn(f"이번 주 전체 보기: {render.SITE_BASE_URL}/briefs/2026-06-08/", s)
        brief_json = json.loads(
            (MULTI_FIXTURES / "brief_web_2026_06_08.json").read_text(encoding="utf-8"))
        for t in brief_json["brief"]["tldr"]:
            self.assertIn(f"· {t}", s)
        empty = (self.multi / "briefs/2026-06-22/share.txt").read_text(encoding="utf-8")
        self.assertNotIn("\n· ", empty)
        self.assertIn(f"{render.SITE_BASE_URL}/briefs/2026-06-22/", empty)

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
        # [네이밍 2026-08-27] h1/title = "지적사항 검색"(면 이름 그대로).
        self.assertIn("지적사항 검색", self.html)
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
        # [네이밍 2026-08-27] M15 의 "지적사항"→"찾아보기"(캐주얼 대구)를 사용자 피드백
        # ("전문성이 보이는 워딩")이 뒤집었다 — 내용을 그대로 이름으로: "지적사항".
        self.assertIn('href="../findings/index.html" class="on">지적사항</a>', self.html)
        self.assertIn('href="findings/index.html">지적사항</a>', self.landing)
        self.assertNotIn('href="../findings/index.html" class="on">지적사항</a>', self.archive)
        self.assertIn('href="../findings/index.html">지적사항</a>', self.archive)
        # 옛 라벨이 어디에도 안 남았는지 — 라벨 교체는 전 표면 동시가 계약이다.
        for page in (self.html, self.landing, self.archive):
            self.assertNotIn(">찾아보기</a>", page)
            self.assertNotIn(">모아보기</a>", page)

    def test_nav_this_week_tab_removed_but_cta_kept(self):
        # [M15] nav 탭에서 "이번 주" 링크는 제거됐다(CTA "이번 주 소식" 버튼과 중복) —
        # 헤더 상시 CTA 버튼("이번 주 소식")은 그대로 유지된다.
        import re as _re
        nav_m = _re.search(r'<nav id="navmenu">(.*?)</nav>', self.html, _re.S)
        self.assertIsNotNone(nav_m)
        self.assertNotIn(">이번 주<", nav_m.group(1))
        self.assertEqual(nav_m.group(1).count("<a "), 6, "nav 탭은 주간 브리프·지적사항·트렌드·자료실·용어사전·이용안내 6개여야 함")
        self.assertIn("이번 주 소식", self.html)  # CTA 버튼은 유지

    def test_footer_link_present(self):
        self.assertIn('<a href="../findings/index.html">지적사항</a>', self.html)
        self.assertNotIn(">이번 주</a>", self.html)

    def test_canonical_and_description(self):
        self.assertIn(f'<link rel="canonical" href="{render.SITE_BASE_URL}/findings/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

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
        "그 외 N건" 한 줄로 합산한다(옛 top6 에서 상향).

        ★[스케일 기준 M2c] 옛 코드는 상위 8개에서만 maxCount 를 구한 뒤 합산행까지 같은
        축으로 그렸다 — 꼬리의 합이 머리의 최댓값을 넘으면 비율이 1을 초과한다(실측 6,226
        > 4,347 → scaleX(1.43) → 막대가 건수 글자를 68px 침범). 이 테스트의 옛 버전은
        `buildCatRow("그 외", restCount, maxCount, null)` 만 확인해 **maxCount 가 어디서
        왔는지는 검사하지 않았고**, 그래서 결함을 그대로 잠근 채 통과했다. 지금은 "그릴
        행을 먼저 모으고 그 배열에서 max 를 뽑는다"는 불변식 자체를 검사한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderDashCategories(stats)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("var rows = stats.categories.slice(0, 8).map(", fn)
        self.assertIn(
            "var restCount = stats.categories.slice(8).reduce(function (s, c) { return s + c.count; }, 0);",
            fn,
        )
        self.assertIn('rows.push({ label: _t("그 외"), count: restCount, code: null });', fn)
        # ★핵심 불변식 — maxCount 는 rows(=실제로 그리는 행)에서만 나온다.
        self.assertIn(
            "var maxCount = rows.reduce(function (m, r) { return Math.max(m, r.count); }, 0) || 1;",
            fn,
        )
        self.assertNotIn("top.reduce(", fn)
        # maxCount 계산이 rows 조립보다 뒤에 와야 한다(합산행이 스케일에 반영되려면).
        self.assertLess(fn.index('rows.push({ label: _t("그 외")'), fn.index("var maxCount ="))

    def test_dash_category_bar_cannot_overflow_count_column(self):
        """[막대 침범 방지 M2c] 가로 막대가 오른쪽 건수 글자를 덮는 결함이 두 번 났다.
        transform:scaleX() 는 레이아웃 박스를 벗어나 **그려지므로**, 비율이 1을 넘는 순간
        건수 열 위에 픽셀이 얹힌다. 산술 한 곳만 고치면 같은 계열이 또 새므로 3중으로 막는다:

          (1) 호출부  — maxCount 를 실제로 그리는 행에서 뽑는다(위 테스트).
          (2) 산술층  — buildCatRow 가 비율을 [0.02, 1] 로 클램프(상한이 핵심).
          (3) 렌더층  — 막대를 overflow:hidden 트랙에 가둬, 값이 무엇이든 트랙 밖을 못 칠한다.

        (3)이 재발 방지의 본체다 — (1)(2)는 산술이고 (3)만이 산술과 무관하게 성립한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildCatRow(label, count, maxCount, code)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        # (2) 상한 클램프 — Math.max 하한만 있던 옛 코드가 정확히 이 결함의 통로였다.
        self.assertIn(
            'bar.style.transform = "scaleX(" + Math.min(1, Math.max(0.02, ratio)) + ")";', fn
        )
        # (3) 막대는 트랙 안에 들어가고, 행에 직접 붙지 않는다.
        self.assertIn('var track = el("div", "fnd-dash-cat-track");', fn)
        self.assertIn("track.appendChild(bar);", fn)
        self.assertIn("row.appendChild(track);", fn)
        self.assertNotIn("row.appendChild(bar);", fn)
        anchor = "\n.fnd-dash-cat-track{"
        css = self.html[self.html.index(anchor) + 1:]
        css = css[:css.index("}") + 1]
        self.assertIn("overflow:hidden", css)
        self.assertIn("min-width:0", css)

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
        """첫 화면 밀도(보조) — page-head 설명문단이 압축된 한 문장(마침표 1개로 종결)인지 확인.

        [발견 허브] 허브 재배열로 문안이 '검색합니다'에서 '모았습니다'(수집·조회 성격)로
        바뀌고 정본 파생 건수가 문장 **안에** 들어갔다 — 한 문장 계약(M15)은 그대로다."""
        import re as _re
        m = _re.search(r'<p class="reveal"[^>]*>([^<]*)</p>', self.html)
        self.assertIsNotNone(m)
        text = m.group(1)
        self.assertEqual(text.count("."), 1, f"한 문장이 아닌 것으로 보임: {text!r}")
        self.assertTrue(text.endswith("모았습니다."), text)

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
        self.assertIn('btn.textContent = expanded ? _t("접기") : _t("자세히 보기");', js_src)
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
        self.assertIn('meta.appendChild(document.createTextNode(_t("문서번호 ")))', js_src)
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

    def test_filter_row_uses_fractional_grid_so_it_cannot_wrap(self):
        """[툴바 1행 M2c] 옛 구조는 flex-wrap + 필드별 고정 px 폭이었다. 고정폭 합
        150+110+120+230+110+150+110 = 980px 에 gap 28×6 = 168px 이 붙어 1,148px — 컨테이너
        1,108px 를 넘겨 **정렬 하나만 둘째 줄로** 밀려나 있었다(라이브 실측). 고정폭 합이
        컨테이너를 넘는 순간 줄이 갈라지는 구조라 필드를 하나 더 붙이면(국가 필터가 실제로
        그랬다) 반드시 재발한다 → 폭을 fr 비중으로 바꿔 **합이 컨테이너를 넘을 수 없게** 한다.

        ★minmax(0,…) 이 함께 있어야 한다 — 맨 fr 은 자식의 min-content 를 하한으로 삼고,
        <select> 의 min-content 는 가장 긴 option 텍스트다. 옵션은 findings.js 가 런타임에
        채우므로(카테고리명·소스명) 정적 폭이 맞아도 데이터가 길어지면 다시 넘칠 수 있다."""
        import re as _re
        m = _re.search(r"\.fnd-filters\{([^}]*)\}", self.html)
        self.assertIsNotNone(m, ".fnd-filters CSS 규칙 미발견")
        rule = m.group(1)
        self.assertIn("display:grid", rule)
        self.assertNotIn("flex-wrap", rule)
        self.assertEqual(7, rule.count("minmax(0,"), "7개 트랙 전부 minmax(0,…) 이어야 한다")
        # 필드별 고정 px 폭은 폐기 — 하나라도 남으면 합이 다시 컨테이너를 넘을 수 있다.
        for fid in ("fnd-f-source", "fnd-f-evidence", "fnd-f-status", "fnd-f-category",
                    "fnd-f-month", "fnd-f-country", "fnd-sort"):
            self.assertNotIn(f"#{fid}{{width:", self.html, f"{fid} 고정폭 규칙이 남아 있다")
        # 셀렉트는 자기 트랙을 꽉 채운다(트랙이 폭의 단일 정본).
        sm = _re.search(r"\.fnd-field select\{([^}]*)\}", self.html)
        self.assertIsNotNone(sm)
        self.assertIn("width:100%", sm.group(1))

    def test_sort_field_separated_from_filters(self):
        """[M2c] 정렬은 결과를 좁히지 않고 순서만 바꾼다 — 같은 행에 두되 세로 구분선으로
        성격 차이를 드러낸다. 1행이 깨지는 폭에서는 구분선이 뜻을 잃으므로 해제한다."""
        self.assertIn('class="fnd-field fnd-field--sort"', self.html)
        self.assertIn(".fnd-field--sort{padding-left:16px;border-left:1px solid var(--line)}",
                      self.html)
        self.assertIn(".fnd-field--sort{padding-left:0;border-left:0}", self.html)

    def test_page_head_paragraph_not_capped_by_ch_width(self):
        """[부제 1줄 M2c] grm.css 의 .page-head p{max-width:62ch} 는 `ch`(숫자 0 의 폭)
        기준이라 한글에는 지나치게 좁다 — 62ch = 609px 인데 부제 한 문장이 666px 라
        컨테이너(1,180px)에 폭이 남는데도 두 줄로 접혔다. trends.html 이 같은 이유로 이미
        쓰는 페이지 스코프 오버라이드를 따른다(grm.css 불가침).
        balance 가 아니라 pretty — balance 는 두 줄 길이를 맞추려 한 줄짜리 문장도 쪼갠다."""
        html_src = (WEB_DIR / "templates" / "findings.html").read_text(encoding="utf-8")
        self.assertIn(".page-head p{max-width:none;text-wrap:pretty}", html_src)
        self.assertNotIn(".page-head p{max-width:none;text-wrap:balance}", html_src)
        # 고정 줄바꿈(<br>)으로 때우지 않았는지 — 반응형이 깨지는 방식이다.
        head = self.html[self.html.index('class="wrap page-head"'):]
        head = head[:head.index("</div>")]
        self.assertNotIn("<br", head)

    def test_source_list_copy_includes_health_canada(self):
        """[운용 정합] 캐나다(Health Canada) 실사는 문서 기준 2위(1,824/6,116)다 — 이 페이지의
        두 노출 지점(부제·AI 고지)에 실제로 실렸는지 확인한다. 전 페이지 범위의 정합은
        WebSourceCopyConsistencyTest 가 추출기 배선에서 파생해 검증한다."""
        self.assertIn("캐나다 실사", self.html)                        # 페이지 부제
        self.assertIn("캐나다 실사보고서(Health Canada)", self.html)   # AI 고지

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
        """URL 파라미터 스킴 = q/agency/cat/src/ev/status/m/country/sort (state 키와
        1:1 매핑, country 는 056 신설)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        for pair in ('q: "q"', 'agency: "agency"', 'category_code: "cat"', 'source: "src"',
                     'evidence_level: "ev"', 'review_status: "status"', 'month: "m"',
                     'country: "country"', 'sort: "sort"'):
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
        # [2026-09-04] 고정폭 슬라이스를 걷어냈다 — 320→400→480 으로 세 번 올려 왔고
        # 네 번째로 또 밀렸다(원문 언어 축의 주석 두 줄). 폭이 아니라 함수 끝까지 자른다.
        fn_block = js_function_body(js_src, "function clearAllFilters()")
        self.assertIn('sort: "date_desc"', fn_block)
        self.assertIn("syncControlsFromState()", fn_block)
        self.assertIn("currentPage = 1", fn_block)
        self.assertIn("goToPage(1)", fn_block)
        # goToPage 가 실제로 render() 를 호출해 재렌더(→URL clear)를 일으키는지 확인.
        # (600→900 으로 올려 온 고정폭도 같은 이유로 걷어낸다.)
        goto_block = js_function_body(js_src, "function goToPage(n)")
        self.assertIn("render()", goto_block)

    def test_active_filter_chips_clear_and_clear_all_wiring(self):
        """[M15] 적용 필터 칩 각각은 클릭 시 해당 조건만 해제(clearActiveFilter)하고,
        전부 textContent/createElement 로만 조립된다(XSS 계약). "모두 지우기"는
        clearAllFilters 를 호출한다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function clearActiveFilter(key)", js_src)
        self.assertIn("function renderActiveChips()", js_src)
        self.assertIn("function buildActiveChip(label, value, onClear)", js_src)
        self.assertIn('btn.setAttribute("aria-label", _t("{label} 필터 해제", { label: label }));', js_src)
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
        문서→지적사항→기관 순회→검토필요(>0) 순으로 블록을 만든다.

        (054 에서 순서가 전체→문서→기관 에서 바뀌고 "전체" 라벨이 "지적사항"이 됐다 —
        단위 표기 계약 자체는 test_dash_stats_documents_first_and_findings_labeled 가
        담당하고, 여기선 M14 의 블록 마크업 계약만 계속 고정한다.)"""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn("function renderDashStats(stats)", js_src)
        self.assertIn("function buildStatBlock(num, label, warn)", js_src)
        self.assertIn('buildStatBlock(String(stats.total), _t("지적사항"), false)', js_src)
        self.assertIn('buildStatBlock(String(a.count), a.agency, false)', js_src)
        self.assertIn('buildStatBlock(String(stats.needsReview), _t("검토 필요"), true)', js_src)
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
        # 함수 끝까지 자른다 — 고정 길이(구 700자) 슬라이스는 주석이 늘면 조용히 잘려
        # "substring not found" 로 죽는다(다국어 3단계에서 실제로 그랬다).
        fn = js_src[js_src.index("function appendOrigAndNote"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        idx_summary_append = fn.index("details.appendChild(summary)")
        idx_trnote = fn.index('el("span", "fnd-tr-note", _t("AI 번역 — 원문 대조 권장"))')
        # [다국어 3단계] 접기에 들어가는 본문은 언어에 따라 갈리므로(`_altText`) 변수명이
        # `p` 그대로여도 인자가 바뀌었다 — 순서 계약(고지가 summary 뒤·본문 앞)은 불변.
        idx_p_append = fn.index("details.appendChild(p);")
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
        self.assertIn('_t("지적사항 {total}건 중 {pub}건 국문 열람 가능", { total: total, pub: pub })', fn)
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
        self.assertIn('_t("전체 {total}건을 국문으로 열람할 수 있습니다.", { total: total })', fn)

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
        self.assertIn('btn.textContent = _t("지적 {n}건 모두 보기", { n: totalCount });', js_src)
        self.assertIn(
            'btn.textContent = expanded ? _t("접기") : _t("지적 {n}건 모두 보기", { n: totalCount });',
            js_src)
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
        self.assertIn('countEl.appendChild(document.createTextNode(_t("전체 ")));', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode(_t("문서 · ")));', render_fn)
        self.assertIn('countEl.appendChild(document.createTextNode(_t("지적 · 페이지 ")));', render_fn)
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
        self.assertIn(
            'meta.appendChild(el("span", "fnd-doc-count", _t("지적 {n}건", { n: rows.length })));',
            js_src)
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
        self.assertIn('buildPagerBtn("«", _t("처음 페이지로 이동")', pager_fn)
        # [b 폴리시] 이전/다음은 «‹›» 단독 글리프 대신 아이콘+텍스트를 병기해 처음 보는
        # 사용자에게도 명확하다("‹ 이전"/"다음 ›") — 처음/끝은 압축 글리프 그대로 유지.
        self.assertIn('buildPagerBtn(_t("‹ 이전"), _t("이전 페이지")', pager_fn)
        self.assertIn('buildPagerBtn(_t("다음 ›"), _t("다음 페이지")', pager_fn)
        self.assertIn('buildPagerBtn("»", _t("끝 페이지로 이동")', pager_fn)
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
        self.assertIn('current.textContent = _t("불러오는 중…");', fn)
        self.assertIn('status.className = "fnd-pager-status";', fn)
        self.assertIn('status.textContent = _t("불러오는 중…");', fn)

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

    def test_dash_stats_documents_first_and_findings_labeled(self):
        """[문서 축 054] renderDashStats() 는 stats.documents 가 있을 때만 "문서" 스탯
        카드를 끼워 넣고(없으면 조용히 생략 — 레이아웃 안 깨짐), 그 **다음에** 총건수를
        "지적사항" 라벨로 그린다.

        ★순서와 라벨이 이 테스트의 본체다. 옛 화면은 [전체][문서][FDA]… 였는데 "전체"가
        무엇의 전체인지 말하지 않아, 기관 숫자(지적사항)와 문서 숫자가 같은 줄에 단위 표시
        없이 나란히 섰다. 문서당 지적 건수가 소스마다 6.01(483)~1.00(MFDS 회수)로 달라서
        그 줄만 보면 편중이 실제의 두 배 넘게 보인다(지적사항 축 87:13 vs 문서 축 69:31)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function renderDashStats(stats)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (stats.documents !== undefined && stats.documents !== null) {", fn)
        self.assertIn('buildStatBlock(String(stats.documents), _t("문서"), false)', fn)
        self.assertIn('buildStatBlock(String(stats.total), _t("지적사항"), false)', fn)
        # 문서 블록이 총건수 블록보다 **앞**에 온다(라벨만 맞고 순서가 뒤집히면 무의미).
        self.assertLess(
            fn.index('String(stats.documents), _t("문서")'),
            fn.index('String(stats.total), _t("지적사항")'),
            "문서 스탯이 지적사항 스탯보다 앞에 와야 한다(기관 스탯의 기준선 역할)",
        )
        self.assertNotIn("agenciesExact", fn, "옛 추정치 툴팁 분기는 054 로 사라졌다")

    def test_dash_agency_stats_declare_their_unit_in_tooltip(self):
        """[문서 축 054] 기관 스탯은 문서 수로 그리되, 툴팁에 **두 축을 병기**한다 —
        "FDA 가 왜 이렇게 큰가"의 답(문서당 지적 건수)이 화면에서 확인돼야 한다.

        ★054 미적용 RPC(by_agency_docs 부재)면 지적사항 축으로 degrade 하는데, 그때도
        툴팁이 단위를 밝힌다. 같은 자리에 조용히 다른 단위를 그리는 것이 이 결함의
        본체였으므로, degrade 경로에 단위 표기가 없으면 결함이 그대로 남는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        fn = js_src[js_src.index("function buildAgencyStats()"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('dashAxis("by_agency_docs")', fn)
        self.assertIn('dashAxis("by_agency")', fn)
        self.assertIn('unit: "documents"', fn)
        self.assertIn('unit: "findings"', fn)
        stats_fn = js_src[js_src.index("function renderDashStats(stats)"):]
        stats_fn = stats_fn[:stats_fn.index("\n  }\n") + 4]
        self.assertIn('if (a.unit === "documents") {', stats_fn)
        self.assertIn("문서 기준 집계 미제공", stats_fn)

    def test_dash_months_use_document_axis_with_unit_label(self):
        """[문서 축 054] 월 추이는 '유입량' 지표라 문서가 자연 단위다 — 지적사항으로 세면
        483 대량 백필이 몰린 달(2024년 한 해 832문서)이 다른 달을 전부 눌러버린다.
        by_month_docs 를 정본으로 쓰고, 막대 툴팁 라벨에 단위를 적는다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertIn('dashAxis("by_month_docs")', js_src)
        self.assertIn('stats.monthsUnit = monthsDocs.length ? "documents" : "findings";', js_src)
        fn = js_src[js_src.index("function renderDashMonths(stats)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn(
            'var monthUnit = stats.monthsUnit === "documents" ? _t("문서 ") : _t("지적 ");', fn)
        self.assertIn(
            '_t("{month} {unit}{count}건", { month: x.month, unit: monthUnit, count: x.count })', fn)

    def test_dash_block_headings_declare_their_unit(self):
        """[문서 축 054] 한 대시보드 안에 두 단위가 섞이는 건 피할 수 없다(카테고리는
        지적사항이, 기관 비교는 문서가 자연 단위다). 섞이는 게 문제가 아니라 **어느 쪽인지
        안 적는 것**이 문제였으므로, 세 블록 제목이 각자 단위를 밝힌다."""
        html = (WEB_DIR / "templates" / "findings.html").read_text(encoding="utf-8")
        self.assertIn(
            '{{ _("카테고리 분포") }}<span class="fnd-dash-unit">{{ _("지적사항 기준") }}</span>', html)
        self.assertIn(
            '{{ _("월별 추이") }}<span class="fnd-dash-unit">{{ _("문서 기준") }}</span>', html)
        self.assertIn(
            '{{ _("업체 상위 5") }}<span class="fnd-dash-unit">{{ _("지적사항 기준") }}</span>', html)
        self.assertIn(".fnd-dash-unit{", html)

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
        4회 호출 — SELECT_FACETS 공유 핸들러·국가(056)·정렬·검색어)·S1 토글(exitSimilarMode
        3회 호출 — SELECT_FACETS·국가(056)·정렬)·hidePager(pnav 포함 은닉) 계약을
        훼손하지 않았는지 재확인한다(056 이 국가 셀렉트를 추가하며 3→4/2→3 로 갱신 —
        그 외 수치는 기존과 동일, 회귀 0)."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        wire_fn = js_src[js_src.index("function wire() {"):js_src.index("function fetchSearch(page)")]
        self.assertEqual(wire_fn.count("exitDeepLinkMode();"), 4)
        self.assertEqual(wire_fn.count("exitSimilarMode();"), 3)
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
        모드를 종료해야 한다 — wire() 의 셀렉트 5개(SELECT_FACETS 공유 핸들러)·
        국가(056, 개별 핸들러)·정렬·검색어 핸들러, 적용 필터 칩 제거
        (clearActiveFilter/clearAllFilters), 대시보드 클릭(toggleXFilter),
        페이지네이션(goToPageFromPager) 전부가 진입점이다."""
        js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        wire_fn = js_src[js_src.index("function wire() {"):js_src.index("function fetchSearch(page)")]
        self.assertEqual(wire_fn.count("exitDeepLinkMode();"), 4,
                          "셀렉트·국가·정렬·검색어 4개 핸들러 모두 exitDeepLinkMode() 호출해야 함")
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
        self.assertIn('btn.textContent = _t("유사 문구 검색");', js_src)
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
        # [다국어 3단계] 가드는 `_altText()` 로 옮겼다 — 두 본문이 다 있을 때만 접기를
        # 만든다는 계약은 그대로다(한쪽이 비면 "" 를 돌려주므로 조용히 no-op).
        self.assertIn("var alt = _altText(row);", orig_fn)
        self.assertIn("if (!alt) return;", orig_fn)

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
        self.assertIn(
            'badge.textContent = _t("동일 문구 {n}개 문서", { n: item.dup_documents || 0 });', fn)

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
        self.assertEqual(wire_fn.count("exitSimilarMode();"), 3,
                          "셀렉트·국가(056)·정렬 3개 핸들러 모두 exitSimilarMode() 호출해야 함")
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
        self.assertIn('_t("동일 문구 {n}개 문서", { n: item.dup_documents || 0 })', fn)

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
        self.assertIn('link.textContent = _t("해당 문서 보기");', fn)

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


# ── FDA 483 문서 카드 실사관 표기(036 findings_search/findings_document inspector_names) ──
class WebFindingsInspectorNamesTest(unittest.TestCase):
    """문서 카드 메타행(fnd-doc-date 옆)에 rows[0].inspector_names(문서 단위 사실 —
    published_date 와 완전히 동일하게 대표값만 사용)를 "실사관: A · B" 한 줄로 표기한다.
    036 마이그레이션이 아직 라이브에 없을 수 있어 완전히 방어적이어야 한다 — 필드
    부재/null/빈 배열/비배열/원소 오염은 전부 "표시할 이름 없음"으로 조용히 수렴하고
    (빈 라벨·"미확인" 같은 자리표시자 금지), 6개를 넘으면 6개로 자른다.

    [실사관 프로파일 진입] 이름별로 findings_inspector_index 코호트(문서 5건 이상)에
    있으면 inspector/index.html?key= 링크, 없으면 평문으로 갈린다(WebInspectorRenderTest
    가 그 배선을 별도로 가드한다) — 그러나 findings.js **자체는** 여전히 실사관 이름을
    집계하거나 이 페이지 안에서 클릭 필터로 쓰지 않는다(범위 제한 불변 — 아래
    test_inspector_names_never_used_for_aggregation_or_click_filter)."""

    @classmethod
    def setUpClass(cls):
        cls.js = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")

    def _fn(self, marker, end_marker="\n  }\n"):
        src = self.js[self.js.index(marker):]
        return src[:src.index(end_marker) + len(end_marker)]

    def test_sanitizer_exists_and_is_fully_defensive(self):
        js = self.js
        self.assertIn("var INSPECTOR_NAMES_LIMIT = 6;", js)
        fn = self._fn("function sanitizeInspectorNames(value)")
        # 배열이 아니면 즉시 빈 배열(null/undefined/객체/문자열 등 전부 포함).
        self.assertIn("if (!Array.isArray(value)) return [];", fn)
        # 원소는 비문자열(숫자/객체/null 등)·공백뿐인 문자열이면 버린다.
        self.assertIn('if (typeof name === "string" && name.trim())', fn)
        self.assertIn("out.push(name.trim());", fn)
        # 6개 상한은 필터링 도중(루프 조건)에 걸려 그 이후 원소를 아예 보지 않는다.
        self.assertIn("out.length < INSPECTOR_NAMES_LIMIT", fn)

    def test_doc_head_renders_line_only_when_inspectors_present(self):
        fn = self._fn("function buildDocHead(rows)")
        self.assertIn("var inspectors = sanitizeInspectorNames(head.inspector_names);", fn)
        # "if (inspectors.length)" 가드 뒤에만 span 이 만들어진다 — 빈 배열이면 요소 자체가
        # 생기지 않는다(빈 라벨 금지 계약의 핵심 분기).
        self.assertIn("if (inspectors.length) {", fn)
        gate = fn[fn.index("if (inspectors.length) {"):]
        # [실사관 프로파일 진입] 각 이름을 코호트 여부에 따라 링크/평문으로 갈라 조립한다
        # (findings_inspector_index 캐시, WebInspectorRenderTest 가 그 배선을 가드) —
        # 이름은 join() 문자열 결합이 아니라 forEach 로 하나씩 appendChild 된다.
        self.assertIn('var inspectorSpan = el("span", "fnd-doc-count");', gate)
        self.assertIn('inspectorSpan.appendChild(document.createTextNode(_t("실사관: ")));', gate)
        self.assertIn("inspectors.forEach(function (name, idx) {", gate)
        self.assertIn("meta.appendChild(inspectorSpan);", gate)

    def test_doc_head_inspector_line_uses_textcontent_helper_not_html(self):
        """el() 헬퍼(textContent 대입)만 쓰고, 하이라이트 헬퍼 elHL() 은 쓰지 않는다 —
        사람 이름은 검색어 하이라이트 대상이 아니다. el() 자체가 e.textContent = text
        로만 대입하는 헬퍼임을 함께 고정해 "textContent 경로" 를 증명한다(HTML 특수문자가
        든 이름도 그대로 이스케이프되어 표시된다는 근거). innerHTML 데이터 삽입 부재는
        파일 전역 XSS 계약(test_document_collapse_no_innerhtml_data_injection)이 별도로도
        가드한다."""
        js = self.js
        el_fn = self._fn("function el(tag, className, text)")
        self.assertIn("e.textContent = text;", el_fn)
        fn = self._fn("function buildDocHead(rows)")
        self.assertNotIn('elHL("span", "fnd-doc-count"', fn)
        # 실사관 줄은 정확히 el() 호출 한 곳에서만 만들어진다(중복 렌더 없음).
        self.assertEqual(fn.count('"실사관: "'), 1)

    def test_doc_head_reuses_existing_meta_class_no_new_css_needed(self):
        """CSS 최소화 — 신규 클래스를 만들지 않고 기존 .fnd-doc-count(findings.html 인라인
        <style>, 이미 muted 소형 텍스트로 정의됨)를 재사용한다(실사관 프로파일 링크
        도입 이후에도 불변 — 링크 유무와 무관하게 컨테이너 span 클래스는 그대로)."""
        fn = self._fn("function buildDocHead(rows)")
        self.assertIn('var inspectorSpan = el("span", "fnd-doc-count");', fn)
        # 지적 건수 span 과 동일 클래스를 공유한다(신규 클래스 미도입 확인).
        self.assertEqual(fn.count('"fnd-doc-count"'), 2)

    def test_similar_search_mapping_forwards_inspector_names_defensively(self):
        """[RPC 매핑부] findings_similar 경로(mapSimilarItemToRow)도 동일한 방어 규칙으로
        inspector_names 를 정제해 전달한다 — evidence_level/review_status 와 동일한
        위치·관례(이 경로가 그리는 buildCard() 관측 카드 자체는 이 필드를 화면에 쓰지
        않지만, 계약 전달은 다른 서지 필드와 동형으로 유지한다)."""
        fn = self._fn("function mapSimilarItemToRow(item)", end_marker="\n  }\n")
        self.assertIn("inspector_names: sanitizeInspectorNames(item.inspector_names),", fn)

    def test_inspector_names_never_used_for_aggregation_or_click_filter(self):
        """범위 제한 가드 — 실사관 이름을 집계·프로필 페이지·클릭 필터로 확장하지
        않는다(임무 명시 사항). findings.js 전체에 inspector 관련 클릭 핸들러/필터
        상태 키가 새로 생기면 안 된다."""
        js = self.js
        self.assertNotIn("toggleInspectorFilter", js)
        self.assertNotIn("state.inspector", js)
        self.assertNotIn("p_inspector", js)


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
        # [존 재편] 존이 세 면으로 갈렸다. 각 테스트가 자기 주제가 실제로 사는 면을
        # 보게 한다 — 한 페이지 전제로 쓴 배치 단언들이 여기서 갈린다.
        cls.inspections = (cls.single / "findings" / "inspections" / "index.html").read_text(encoding="utf-8")
        cls.coverage = (cls.single / "findings" / "coverage" / "index.html").read_text(encoding="utf-8")
        # 스코프 CSS 는 세 면이 함께 include 하는 파셜로 옮겼다 — "grm.css 를 건드리지
        # 않았는가"를 재는 단언들은 이 파셜을 봐야 한다(검사 대상이 바뀐 것일 뿐,
        # 재는 것은 그대로다).
        cls.style_src = (WEB_DIR / "partials" / "trends_style.html").read_text(encoding="utf-8")
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
            'id="grm-findings-cfg" data-url="" data-key="" data-root="../../"'
            ' data-page="trends" hidden',
            self.html,
        )
        # [존 재편] 세 면이 trends.js 하나를 공유하므로 어느 면인지를 셸이 말한다.
        self.assertIn('data-page="inspections"', self.inspections)
        self.assertIn('data-page="coverage"', self.coverage)

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
        # 트렌드 페이지 자체에서만 '지적사항'은 on 이 아니고, '트렌드'만 on.
        import re as _re
        nav_m = _re.search(r'<nav id="navmenu">(.*?)</nav>', self.html, _re.S)
        self.assertIsNotNone(nav_m)
        self.assertNotIn('class="on">지적사항', nav_m.group(1))
        self.assertEqual(nav_m.group(1).count("<a "), 6)  # 주간 브리프·지적사항·트렌드·자료실·용어사전·이용안내

    def test_footer_link_present(self):
        self.assertIn('<a href="../../findings/trends/index.html">트렌드</a>', self.html)

    def test_findings_zone_links_to_trends(self):
        """[2면 분리 2026-08-27] 검색 면의 히어로 링크 행("전체 트렌드 보기")은 세그와
        함께 정리됐다 — 트렌드 진입은 nav 탭 + 둘러보기 면의 업무별 카드가 승계한다.
        존 안에서 트렌드로 가는 길이 있는지를 지키는 것이 이 테스트의 본래 목적이다."""
        browse = (self.single / "findings" / "browse" / "index.html"
                  ).read_text(encoding="utf-8")
        self.assertIn('href="../../findings/trends/index.html"', browse)

    def test_canonical_and_description(self):
        self.assertIn(
            f'<link rel="canonical" href="{render.SITE_BASE_URL}/findings/trends/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

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
        """각 차트에 '이 그래프를 읽는 법' 1~2문장(.tr-read) — 전 직원 대상이라 정적
        텍스트로 두어 골든에 남기고 리뷰 가능하게 한다.

        [존 재편 2026-08-26] 12개가 한 페이지에 쌓여 있던 것을 세 면으로 나눴다. 총량이
        준 것이 핵심이 아니라 **각 문장이 자기 분모 옆에 있게 된 것**이 핵심이다 — 재편
        전에는 분모가 다른 차트들이 한 스크롤에 이어져 있어 어느 설명이 어느 표의 것인지
        흐려졌고, 그래서 설명이 계속 길어졌다(본문 글자의 32%가 주석이었다).
        순위 3종(최근 12개월/전 기간/해외vs미국)의 읽는 법은 이제 정적 문장이 아니라
        보기를 바꿀 때마다 trends.js 가 다시 적는다(RANK_READ) — 그래서 이 카운트에서
        빠진다."""
        # [컨셉 재정의] 지적 경향 면의 읽는 법은 **정적 문장이 아니다** — 기관을 바꾸면
        # 분모가 바뀌므로 설명도 함께 바뀌어야 한다(trends.js agencyReadText).
        # 그래서 이 면의 정적 .tr-read 개수는 적은 것이 정상이고, 대신 JS 가 보유한
        # 문장을 아래에서 검증한다.
        for face, n in (("inspections", self.inspections.count('<p class="tr-read">')),
                        ("coverage", self.coverage.count('<p class="tr-read">'))):
            self.assertGreaterEqual(n, 2, f"{face} 면에 읽는 법이 너무 적다: {n}")
        self.assertIn('id="tr-rank-read"', self.html)
        self.assertIn('id="tr-cfr-read"', self.html)
        # 보기 전환 3종의 읽는 법은 JS 가 보유한다(정적 문장이 아니다).
        js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        self.assertIn("function agencyReadText(view)", js_src)
        for cue in (# [2026-08-27] 분모가 "문서" 에서 "실사 지적" 으로 좁아졌다 —
                    # 회수 공고·행정처분은 지적이 아니라 순위 모집단에서 뺐다.
                    "실사 지적에서만 셉니다.",
                    # '전체'가 무엇을 합친 것인지 말한다 — 안 적으면 독자가 자기와
                    # 관련 있는 기관만 들어 있다고 가정한다(캐나다 실사가 32%다).
                    "캐나다 실사와 EU·영국 GMP 비준수까지, 실사에서 나온 지적만 합쳐 센 순위입니다.",
                    "식약처와 FDA 는 상위 항목이 겹치지 않습니다"):
            self.assertIn(cue, js_src)
        # 조항 순위의 읽는 법도 기관에 따라 달라진다(고른 기관에게 21 CFR 이 다른 나라
        # 규정이면 그 사실을 먼저 말한다).
        # ★[2026-08-28] 종전에는 `"식약처 지적서에는 …"` 이라는 **기관 이름이 박힌
        #   문자열**을 요구했다. 캐나다를 넣으면서 그 하드코딩을 없앴으므로 검사도 뜻으로
        #   옮긴다 — 지키려던 것은 "안내가 기관에 따라 달라진다"이지 "식약처라는 글자가
        #   있다"가 아니었다. 캐나다 지적 9,505건 중 21 CFR 인용은 0건이라, 이름으로
        #   가르는 분기는 기관이 늘 때마다 거짓을 말하게 된다.
        self.assertIn("function applyAgencyToCfr(view)", js_src)
        self.assertIn("지적서에는 이 조항이 인용되지 않습니다", js_src)
        self.assertIn("function agencyCitesCfr(", js_src)
        self.assertNotIn('view.key === "mfds"', js_src,
                         "CFR 안내가 기관 이름으로 분기하고 있다(데이터로 판정해야 한다)")
        for cue in ("한국은 목록 밖이어도 따로 표시합니다.",
                    "<b>NAI</b>는 지적사항 없음"):
            self.assertIn(cue, self.inspections)
        for cue in ("각 연도를 100%로 놓고",
                    "그 해에 지적이 많아졌다는 뜻이 아닙니다",
                    "품질이 나쁜 순서가 아닙니다.",
                    "문서 형식의 차이도 함께 반영합니다."):
            self.assertIn(cue, self.coverage)

    def test_hero_paragraph_breaks_at_sentence_boundary(self):
        """[줄맞춤] 히어로 설명 문장마다 .ln 블록을 씌워 줄바꿈을 **문장 경계**에
        고정한다. text-wrap:balance 는 두 줄 길이를 맞추려고 문장 한가운데를 끊어
        목적어와 서술어를 갈라 놓았다. 고정 <br> 이 아니라 블록 span 이라 좁은
        뷰포트에서는 각 문장이 자기 안에서 다시 접힌다(반응형 유지).
        [컨셉 재정의] 문구 자체는 바뀌었다 — 이제 히어로가 이 페이지의 사용법을
        한 줄로 말한다(기관을 고르면 → 순위 → 실제 문장 → 체크리스트)."""
        self.assertIn('<span class="ln">기관을 고르면 최근 12개월 동안 그 기관이 가장 많이 '
                      '지적한 영역과 조항을 보여드립니다.</span>', self.html)
        self.assertIn('<span class="ln">줄을 누르면 실제 지적 문장으로, 마지막에는 '
                      '자가점검 체크리스트로 이어집니다.</span>', self.html)
        html_src = (WEB_DIR / "partials" / "trends_style.html").read_text(encoding="utf-8")
        self.assertIn(".page-head p .ln{display:block}", html_src)
        self.assertNotIn(".page-head p{max-width:none;text-wrap:balance}", html_src)
        self.assertIn(".page-head p{max-width:none;text-wrap:pretty}", html_src)
        head = self.html[self.html.index('class="wrap page-head"'):]
        head = head[:head.index("</div>")]
        self.assertNotIn("<br", head)

    def test_publication_date_semantics_still_disclosed(self):
        """[컨셉 재정의] 오독의 근원(날짜=공개일)은 여전히 정적 텍스트로 밝힌다 —
        다만 자리가 본문 위쪽 박스에서 **꼬리 각주**로 옮겼다. 고지를 없앤 것이
        아니라, 아직 아무것도 못 본 사람에게 주의사항부터 읽히지 않게 한 것이다.

        ★[2026-08-28] 문장이 바뀌었다. 종전 단정("실사한 날이 **아니라** 공개된 날")은
        캐나다 실사가 들어오면서 거짓이 됐다 — 그 소스는 원천이 공개일을 주지 않아
        `published_date` 자리에 실사 시작일이 들어간다(mig 069). 고지를 없앤 것이
        아니라 **예외를 이름으로 밝히도록** 바꾼 것이므로, 검사도 그 뜻으로 옮긴다."""
        for surface, name in ((self.html, "지적 경향"), (self.coverage, "데이터 현황")):
            self.assertIn("자료가 공개된 날", surface, f"{name}: 날짜 고지가 사라졌다")
            self.assertIn("캐나다", surface, f"{name}: 예외를 밝히지 않는다")
            self.assertNotIn("실사한 날이 아니라", surface,
                             f"{name}: 캐나다에서 거짓인 단정이 되살아났다")

    def test_source_mix_skew_disclosed(self):
        """소스 구성 편중은 이 페이지 전체 해석의 전제 — 숨기지 않고 소스 구성 섹션에서
        명시한다. [2026-08-11] 옛 문구 "지금은 FDA 483이 대부분입니다 … 사실상 FDA 483의
        경향으로 읽으셔야 합니다"는 캐나다 실사 9,505건 편입으로 **사실이 아니게 됐다**
        (FDA 483 41% vs 캐나다 38%). 새 문구는 특정 비율을 박지 않는다 — 바로 아래 막대가
        실제 값을 그리므로, 숫자를 문장에 박으면 그 문장만 낡는다."""
        # [존 재편] 소스 구성 섹션은 '데이터 현황' 면으로 옮겼다 — 이 표가 답하는 질문이
        # "규제가 어떻게 변하나"가 아니라 "우리가 무엇을 얼마나 모았나"이기 때문이다.
        self.assertIn("문서 형식의 차이도 함께 반영합니다", self.coverage)
        self.assertNotIn("사실상 FDA 483의 경향으로 읽으셔야 합니다", self.coverage)

    def test_year_trend_caveat_note_present(self):
        # [존 재편] 연도별 공개량은 '데이터 현황' 면으로 옮겼다(수집량이지 트렌드가 아니다).
        self.assertIn("소스마다 거슬러 올라간 범위가 다르기 때문", self.coverage)
        self.assertIn("하한치", self.coverage)
        # ★[롤링 표현 금지 2026-08-12] 처음엔 "캐나다 실사는 최근 5년치만"이라고 썼는데,
        # 실제 백필 시작점은 collect_hc_inspection_backfill.DEFAULT_FROM_DATE = 2021-01-01
        # 고정이다. 시작점이 고정인데 "최근 N년"이라는 롤링 표현을 붙이면 해가 바뀔 때마다
        # 1씩 어긋난다(작성 시점에 이미 5년 7개월). 고정 시작점은 고정 표현으로 적는다.
        self.assertIn("캐나다 실사는 2021년 이후만", self.coverage)
        self.assertNotRegex(self.coverage, r"캐나다 실사는 최근 \d+년",
                            "고정 시작점에 롤링 표현('최근 N년')이 되살아났다")
        # 제목이 "추이"(=규제 활동 변화)로 읽히면 안 된다는 계약은 그대로다. 재편 전에는
        # 트렌드 페이지 한복판에 있어 제목에 "(참고)"를 달아 강등해야 했지만, 이제 면
        # 자체가 '데이터 현황'이라 그 꼬리표 없이도 오독되지 않는다.
        self.assertIn('<h2 class="tr-h">연도별 공개량</h2>', self.coverage)
        self.assertNotIn("연도별 추이</h2>", self.coverage)

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
        # [존 재편] '데이터 현황' 면으로 옮겼다 — 셸 결정론(기본 hidden) 계약은 그대로.
        self.assertIn(
            '<section class="tr-block tr-heatmap-block" id="tr-heatmap-block" '
            'aria-label="연도별 구성비" hidden>',
            self.coverage,
        )
        self.assertIn('<h2 class="tr-h">연도별 구성비</h2>', self.coverage)
        self.assertIn('<div id="tr-heatmap" class="tr-heatmap"></div>', self.coverage)
        # 표본 부족으로 제외한 연도를 적을 자리도 셸에 hidden 으로 있어야 한다.
        self.assertIn('<p class="tr-note" id="tr-heatmap-note" hidden></p>', self.coverage)
        # 배치 계약도 옮겼다: 소스 구성 → 연도별 공개량 → 구성비 → 수집량 상위 업체.
        src_idx = self.coverage.index('aria-label="소스 구성"')
        year_idx = self.coverage.index('aria-label="연도별 공개량"')
        heatmap_idx = self.coverage.index('id="tr-heatmap-block"')
        firms_idx = self.coverage.index('aria-label="수집량 상위 업체"')
        self.assertTrue(src_idx < year_idx < heatmap_idx < firms_idx,
                        "데이터 현황 면의 섹션 순서가 어긋났다")

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
        self.assertIn('el("span", "tr-heatmap-yearbase", _t("{n}건", { n: fmtNum(yearBase[y] || 0) }))', fn)
        # 툴팁은 건수·분모·비율을 모두 보여 준다(원 수치 은폐 금지).
        self.assertIn('_t("{cnt}건(그 해 {base}건 중 {pct})",', fn)

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
        heatmap_chain = js_src[js_src.index("fetchCategoryMatrix()"):]
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
        self.assertIn(".tr-heatmap-scroll{overflow-x:auto", self.style_src)

    # ── [공개 범위 투명성] 트렌드 페이지 커버리지 노트 ───────────────────────────
    def test_coverage_note_moved_off_the_findings_face(self):
        """[컨셉 재정의] '먼저 알아두세요' 3줄 블록을 지적 경향 면에서 걷어냈다.

        아직 아무것도 못 본 사람에게 주의사항부터 읽히는 자리였다(본문 세 번째 블록).
        남긴 것은 꼬리 각주 한 줄이고, 나머지 해설의 목적지는 데이터 현황 면이다 —
        그 면에는 같은 노트가 그대로 있다(숫자를 의심하는 사람만 마주친다)."""
        self.assertNotIn('id="tr-coverage-note"', self.html,
                         "'먼저 알아두세요' 블록이 지적 경향 면으로 되돌아왔다")
        self.assertIn('<div class="imp" id="tr-coverage-note" hidden>', self.coverage)
        self.assertIn('<p id="tr-coverage-text"></p>', self.coverage)
        # 날짜 고지는 각주로 남아 있다(문구는 test_publication_date_semantics_still_disclosed
        # 가 뜻으로 잰다 — 캐나다 예외 때문에 단정형이 아니다).
        self.assertIn("자료가 공개된 날", self.html)
        self.assertIn("findings/coverage/index.html", self.html)


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
        self.assertIn('buildStat(fmtNum(totals.documents), _t("분석 문서"))', fn)
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
        self.assertIn('buildStat(fmtNum(totals.firms), _t("업체"))', after_guard)
        self.assertIn('buildStat(fmtNum(totals.raw_signals), _t("원문서"))', after_guard)
        self.assertIn('buildStat(fmtNum(totals.public_findings), _t("국문 열람 가능"))', after_guard)

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
        self.assertIn("번역 완료 전까지", fn)
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
        self.assertIn('a.textContent = _t("업체 프로파일 전체 보기 →");', fn)
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
        self.assertIn(".tr-fd-profile-link{", self.style_src)
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


# ── [FDA 의약품 GMP 실사 등급] fda_inspection_stats() RPC(058, 임무3) 신규 섹션 ─────
class WebTrendsFdaInspectionsTest(unittest.TestCase):
    """트렌드 대시보드 신규 섹션 — fda_inspection_stats() RPC(058, 파라미터 없음)를
    findings 계열 RPC 전부와 독립적으로 fetch 한다(실패해도 다른 섹션에 영향 0, zone/
    heatmap 과 동일 원칙). 여기선 셸 결정론·RPC 배선·scope 비하드코딩·비율 클라이언트
    계산(007/038 관례)·한국 강조·0건일 때 빈 껍데기 미생성을 검증한다(실제 집계 렌더
    자체는 비골든 — trends.js 소관, WebTrendsRenderTest 와 동일 원칙)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_trendsfda_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        # [존 재편 2026-08-26] 이 섹션은 지적사항 면에서 **실사 결과 면으로 독립**했다.
        # 단위가 다른 두 집계(실사 건 vs 지적 문장)를 같은 페이지에 두었던 것이 "두 수치를
        # 서로 나누지 마세요"라는 경고문이 필요했던 원인이었다.
        cls.html = (cls.single / "findings" / "inspections" / "index.html").read_text(encoding="utf-8")
        cls.trends_html = (cls.single / "findings" / "trends" / "index.html").read_text(encoding="utf-8")
        cls.js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        cls.html_src = (WEB_DIR / "templates" / "inspections.html").read_text(encoding="utf-8")
        cls.style_src = (WEB_DIR / "partials" / "trends_style.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ── 셸 결정론 ────────────────────────────────────────────────────────────
    def test_panel_shell_present_hidden_between_coverage_note_and_recent_block(self):
        self.assertIn(
            '<section class="tr-block tr-fda-block" id="tr-fda-block" '
            'aria-label="FDA 의약품 GMP 실사 등급" hidden>',
            self.html,
        )
        # [존 재편] 자기 면(실사 결과)의 머리 섹션이 됐다 — 페이지 제목이 이미
        # "FDA 실사 결과"라 섹션 표제는 '등급 구성'으로 줄였다(제목의 중복 제거).
        self.assertIn('<h2 class="tr-h">등급 구성</h2>', self.html)
        self.assertIn("FDA 실사 결과", self.html)
        self.assertIn('<p class="tr-fda-scope" id="tr-fda-scope"></p>', self.html)
        self.assertIn('<p class="tr-fda-asof" id="tr-fda-asof"></p>', self.html)
        self.assertIn('<div class="tr-stats tr-fda-stats" id="tr-fda-stats"></div>', self.html)
        self.assertIn('<div id="tr-fda-year" class="tr-fda-year"></div>', self.html)
        self.assertIn('<div id="tr-fda-country" class="tr-fda-country"></div>', self.html)
        self.assertIn('<p class="tr-note" id="tr-fda-note"></p>', self.html)
        # [존 재편] 배치 계약이 사라졌다 — 이 섹션이 얹혀 있던 두 이웃('먼저 알아두세요'와
        # '최근 12개월')은 이제 다른 면(지적 경향)에 있다. 대신 **자기 면의 첫 섹션**이고,
        # 지적사항 면에는 남아 있지 않다는 것을 확인한다(단위가 다른 두 집계의 분리가
        # 이 재편의 목적이므로, 되돌아오면 경고문이 다시 필요해진다).
        content_idx = self.html.index('id="tr-content"')
        fda_idx = self.html.index('id="tr-fda-block"')
        self.assertLess(content_idx, fda_idx)
        self.assertNotIn('id="tr-fda-block"', self.trends_html,
                         "실사 등급 섹션이 지적사항 면으로 되돌아왔다(단위가 다르다)")

    def test_fda_elements_defensively_queried_not_in_hard_gate(self):
        """구버전 캐시 셸에 이 신규 블록이 없어도 페이지 전체(다른 패널)가 죽으면
        안 된다 — coverageNoteEl/zoneBlockEl 관례와 동형으로 하드 게이트(if 문)에
        tr-fda-* 엘리먼트를 넣지 않는다."""
        for elid in ("tr-fda-block", "tr-fda-scope", "tr-fda-asof", "tr-fda-stats",
                     "tr-fda-year", "tr-fda-country", "tr-fda-note"):
            self.assertIn(f'document.getElementById("{elid}")', self.js_src)
        gate = self.js_src[self.js_src.index("if (!cfg || !loadingEl"):]
        gate = gate[:gate.index("return;") + len("return;")]
        for forbidden in ("fdaBlockEl", "fdaScopeEl", "fdaAsOfEl", "fdaStatsEl",
                           "fdaYearEl", "fdaCountryEl", "fdaNoteEl"):
            self.assertNotIn(forbidden, gate)

    # ── RPC 배선 · 독립 fetch ────────────────────────────────────────────────
    def test_rpc_endpoint_present_with_no_params(self):
        self.assertIn('rpcEndpoint("fda_inspection_stats")', self.js_src)
        self.assertIn("function fetchFdaInspectionStats()", self.js_src)
        fn = self.js_src[self.js_src.index("function fetchFdaInspectionStats()"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn('method: "POST"', fn)
        self.assertIn('body: "{}",', fn)  # 058 계약 — 파라미터 없음

    def test_primary_chain_on_its_own_face_and_absent_elsewhere(self):
        """[존 재편 2026-08-26] 이 체인의 **성격이 바뀌었다**.

        재편 전에는 지적사항 페이지에 얹힌 부속 섹션이라 "실패해도 조용히 숨김 유지"가
        옳았다. 이제 이 집계는 자기 면(실사 결과)의 **주 데이터**다 — 여기서 조용히
        숨기면 사용자는 빈 페이지를 본다. 그래서 실패·0건이면 안내로 내린다.
        동시에 다른 면에서는 이 체인을 **아예 치지 않아야** 한다(그 면이 그릴 수 없는
        데이터를 받아 오면 낭비이자, 그 실패가 그 면의 오류로 보인다)."""
        chain = self.js_src[self.js_src.index("if (WANT.fda) {"):]
        chain = chain[:chain.index("\n  }")]
        self.assertIn("renderFdaInspections(data)", chain)
        # 0건·구버전 응답으로 블록이 끝내 안 펴지면 빈 화면 대신 안내.
        self.assertIn("fdaBlockEl && !fdaBlockEl.hidden", chain)
        self.assertIn("failContent", chain)
        # 지적 경향 면은 이 RPC 를 치지 않는다.
        want = self.js_src[self.js_src.index("var WANT = ({"):]
        want = want[:want.index("})[page]")]
        trends_line = [l for l in want.split("\n") if l.strip().startswith("trends:")][0]
        self.assertNotIn("fda", trends_line)

    # ── 빈 껍데기 금지 ───────────────────────────────────────────────────────
    def test_zero_total_renders_nothing(self):
        fn = self.js_src[self.js_src.index("function renderFdaInspections(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("if (!(total > 0)) return;", fn)

    # ── scope 비하드코딩 — RPC 응답을 그대로 읽어 화면에 적는다(054) ───────────
    def test_scope_text_derived_from_rpc_not_hardcoded(self):
        fn = self.js_src[self.js_src.index("function renderFdaInspections(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("scope.project_area", fn)
        self.assertIn("scope.excluded_project_areas", fn)
        self.assertIn("scope.fiscal_year_min", fn)
        self.assertIn("scope.fiscal_year_max", fn)
        self.assertNotIn("Drug Quality Assurance", fn)
        self.assertNotIn("Bioresearch Monitoring", fn)

    # ── 기준일 고지(059_fda_inspection_stats_freshness.sql) ────────────────────
    def _render_fn(self):
        fn = self.js_src[self.js_src.index("function renderFdaInspections(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        # ★함수 슬라이싱 자기검사 — 2칸으로 닫히는 블록이 새로 들어오면 그 뒤 코드가
        # 조용히 미검사 구간이 된다("초록인데 안 본다"의 재발 경로). 함수의 마지막 줄이
        # 잘린 조각 안에 실제로 들어 있는지 매번 확인한다.
        self.assertIn("fdaBlockEl.hidden = false;", fn,
                      "renderFdaInspections 슬라이싱이 함수 끝까지 닿지 않는다")
        return fn

    def test_as_of_dates_read_from_rpc_scope_not_fabricated(self):
        """★059 가 scope 에 실어 준 두 날짜를 그대로 적는다. 클라이언트가 날짜를 만들면
        ('오늘'·빌드 시각·FY 로 유추) 그 문장은 데이터가 낡을수록 더 그럴듯한 거짓이 된다."""
        fn = self._render_fn()
        self.assertIn("scope.last_ingested_date_kst", fn)
        self.assertIn("scope.latest_inspection_end_date", fn)
        for forbidden in ("new Date(", "toLocaleDateString", "Date.now("):
            self.assertNotIn(forbidden, fn, f"날짜를 클라이언트가 만들고 있다: {forbidden}")
        # 리터럴 날짜를 박으면 즉시 낡는다(054 "축을 바꾸지 말고 밝혀라"의 날짜판).
        # 주석은 제외한다 — 근거를 적으려면 실측 날짜를 인용해야 하고, 화면에 나가는 것은
        # 코드 줄뿐이다.
        code = "\n".join(ln for ln in fn.splitlines() if not ln.strip().startswith("//"))
        self.assertNotRegex(code, r"\d{4}-\d{2}-\d{2}")

    def test_as_of_degrades_by_omission_not_by_inventing_a_fallback(self):
        """★값이 없으면(059 미적용 라이브·구버전 캐시) **그 항목만 빠져야** 한다.

        같은 함수 안의 project_area/source 는 `|| "..."` 폴백을 쓰는데, 날짜에 그 패턴을
        복사하면 '최신인 척하는 낡은 날짜'가 라이브로 나간다. fiscal_year_min/max 와 같은
        방식(타입 확인 후 없으면 항목 자체를 생략)이어야 한다."""
        fn = self._render_fn()
        block = fn[fn.index("if (fdaAsOfEl)"):]
        block = block[:block.index("\n    }")]
        self.assertIn('typeof scope.last_ingested_date_kst === "string"', block)
        self.assertIn('typeof scope.latest_inspection_end_date === "string"', block)
        self.assertNotIn('|| "', block, "날짜 항목에 문자열 폴백이 들어갔다")

    def test_as_of_labels_keep_the_two_dates_distinguishable(self):
        """★두 날짜는 뜻이 다르다(우리가 받아온 날 vs FDA 실사가 끝난 날). 라벨 없이
        나란히 적으면 '2026-07-16까지 최신'처럼 읽혀 오도한다."""
        fn = self._render_fn()
        self.assertIn("새 실사를 마지막으로 받아온 날 ", fn)
        self.assertIn("담긴 실사 중 가장 최근 종료일 ", fn)

    def test_as_of_style_exists_and_stays_korean_safe(self):
        self.assertIn(".tr-fda-asof{", self.style_src)
        # §4 한글 안전 — 이 문단에 var(--mono)/letter-spacing 을 쓰지 않는다.
        rule = self.style_src[self.style_src.index(".tr-fda-asof{"):]
        rule = rule[:rule.index("}")]
        self.assertNotIn("--mono", rule)
        self.assertNotIn("letter-spacing", rule)

    # ── 비율은 클라이언트가 계산한다(007/038 관례: 서버는 센다) ────────────────
    def test_oai_share_computed_client_side(self):
        fn = self.js_src[self.js_src.index("function renderFdaInspections(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("pctText(totals.oai, total)", fn)
        self.assertIn("pctText(totals.vai, total)", fn)
        self.assertIn("pctText(totals.nai, total)", fn)

    def test_year_row_composition_from_counts_not_server_ratio(self):
        fn = self.js_src[self.js_src.index("function buildFdaYearRow(y)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("var total = nai + vai + oai;", fn)
        self.assertIn("var share = total > 0 ? cnt / total : 0;", fn)

    # ── 한국 강조 — top N 절단으로 가려지면 안 된다 ─────────────────────────────
    def test_korea_always_shown_even_outside_top_n(self):
        fn = self.js_src[self.js_src.index("function renderFdaInspections(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn('countries[i].code === "KR"', fn)
        self.assertIn("if (korea && !koreaInTop) fdaCountryEl.appendChild(buildFdaCountryRow(korea, true));", fn)
        # is-kr 강조 클래스가 CSS 에 실제로 존재해야 한다.
        self.assertIn(".tr-fda-c-row.is-kr{", self.style_src)

    # ── COUNTRY_LABELS_KO 재사용 — 새 정규화 사전을 만들지 않는다(임무서 지시) ────
    def test_reuses_existing_country_labels_dict(self):
        fn = self.js_src[self.js_src.index("function buildFdaCountryRow(c, isKorea)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("countryLabelKo(c.code, c.country)", fn)
        self.assertEqual(self.js_src.count("var COUNTRY_LABELS_KO = {"), 1,
                          "국가 라벨 사전이 새로 하나 더 생겼다 — 기존 COUNTRY_LABELS_KO 를 재사용해야 한다")


# ── [해외 vs 미국 내 실사] 카테고리별 지적 패턴 비교 패널(038) ────────────────

    # ── [062] 실사일 축 + 한국 슬라이스 ──────────────────────────────────────
    def test_quarter_and_korea_shells_present_hidden(self):
        """062 미적용 라이브·빈 응답에서 trends.js 가 그대로 두는 상태(hidden)가 정적
        셸의 기본값이어야 한다 — 이 면의 주 데이터(등급 구성)는 059 만으로 그려지므로
        두 신설 섹션이 안 채워져도 페이지는 정상이다."""
        self.assertIn(
            '<section class="tr-block tr-fq-block" id="tr-fq-block" '
            'aria-label="실사일 기준 분기 추이" hidden>', self.html)
        self.assertIn(
            '<section class="tr-block tr-kr-block" id="tr-kr-block" '
            'aria-label="한국 소재 제조소" hidden>', self.html)
        for frag in ('<div id="tr-fq" class="tr-fq"></div>',
                     '<p class="tr-note" id="tr-fq-note"></p>',
                     '<p class="tr-kr-sub" id="tr-kr-sub"></p>',
                     '<div id="tr-kr-year" class="tr-fda-year"></div>',
                     '<p class="tr-note" id="tr-kr-note"></p>'):
            self.assertIn(frag, self.html)
        # 하드 게이트 밖(구버전 셸에서도 주 데이터는 살아야 한다).
        gate = self.js_src[self.js_src.index("if (!cfg || !loadingEl"):]
        gate = gate[:gate.index("return;") + len("return;")]
        for forbidden in ("fqBlockEl", "krBlockEl"):
            self.assertNotIn(forbidden, gate)

    def test_quarter_axis_is_inspection_date_not_fiscal_year(self):
        """이 섹션이 존재하는 이유 자체 — 위 표와 **재는 날짜가 다르다**.

        같은 데이터를 회계연도로 보면 OAI 비율이 크게 출렁이는 것처럼 보이는데(FY2020
        9.4% → FY2021 22%), 실사 종료일 분기로 다시 재면 훨씬 안정적이다. 축이 결론을
        바꾸므로, 화면이 두 축의 차이를 **명시**하지 않으면 독자가 같은 것으로 읽는다."""
        self.assertIn("회계연도", self.html)
        self.assertIn("실사가 실제로 끝난 날", self.html)
        sql = (WEB_DIR / "migrations" /
               "062_fda_inspection_stats_inspection_date.sql").read_text(encoding="utf-8")
        self.assertIn("date_trunc('quarter', inspection_end_date)", sql)

    def test_partial_quarter_marking_derives_from_data_frontier(self):
        """미완 분기 판정은 **상수가 아니라 데이터의 전선**에서 나와야 한다.

        최근 분기는 FDA 등급 확정·공개 지연으로 오른쪽 절단이다. "마지막 2개" 같은
        상수를 박으면 데이터가 앞으로 나아갈 때 그 상수만 낡아 조용히 틀린다 — 이
        저장소가 임계값으로 반복해 겪은 실패다. 059 가 이미 내보내는
        scope.latest_inspection_end_date 에서 파생하면 판정이 저절로 따라간다."""
        fn = self.js_src[self.js_src.index("function fqIsPartial("):]
        fn = fn[:fn.index("\n  }") + 4]
        self.assertIn("latest_inspection_end_date", self.js_src)
        self.assertIn("quarter_end", fn)
        self.assertIn("setUTCMonth", fn)
        # 서버가 완결성을 판정하지 않는다 — 마이그레이션에 임계 컬럼이 없어야 한다.
        sql = (WEB_DIR / "migrations" /
               "062_fda_inspection_stats_inspection_date.sql").read_text(encoding="utf-8")
        for forbidden in ("'is_partial'", "'complete'", "'is_complete'"):
            self.assertNotIn(forbidden, sql,
                             "서버가 완결성을 판정하고 있다(임계는 반드시 낡는다)")

    def test_quarter_bar_uses_fixed_scale_not_self_normalized(self):
        """막대를 자기 최댓값으로 정규화하면 **안정과 변동을 구분할 수 없다** — 늘
        비슷해 보인다. 이 차트가 말하려는 것이 정확히 '고른가'이므로 고정 스케일이어야
        한다."""
        self.assertIn("var FQ_SCALE_MAX = 0.30;", self.js_src)
        fn = self.js_src[self.js_src.index("function buildFqRow("):]
        fn = fn[:fn.index("\n  }") + 4]
        self.assertIn("FQ_SCALE_MAX", fn)
        self.assertNotIn("maxShare", fn)

    def test_korea_note_bridges_cumulative_and_yearly_counts(self):
        """부제(누적 실사 N건 · 사업장 M곳)와 연도별 문장(같은 해 중복 없음)이 서로
        어긋나 보이지 않게, 두 수치가 **다른 것을 센다**는 사실을 화면이 말해야 한다."""
        fn = self.js_src[self.js_src.index("function renderKorea("):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("revisited", fn)
        self.assertIn("다른 해에 다시 실사한 것입니다", fn)
        # 표본이 작다는 사실을 먼저 말한다(비율을 앞세우지 않는다).
        self.assertIn("비율보다 건수로 보셔야 합니다", fn)

    def test_migration_062_is_pure_addition_and_keeps_no_arg_signature(self):
        """★파라미터를 하나라도 붙이면 새 오버로드가 생겨 기존 무인자 호출이 PostgREST
        404 가 되고, 새 함수는 058 의 revoke 를 물려받지 못해 PUBLIC EXECUTE 로 태어난다
        (059 헤더의 근거). 그리고 059 의 기존 4키는 한 글자도 바뀌면 안 된다."""
        sql = (WEB_DIR / "migrations" /
               "062_fda_inspection_stats_inspection_date.sql").read_text(encoding="utf-8")
        self.assertIn("create or replace function public.fda_inspection_stats()", sql)
        self.assertNotIn("fda_inspection_stats(p_", sql)
        # revoke 가 grant 보다 먼저(순서가 뒤집히면 PUBLIC EXECUTE 가 남는다).
        self.assertLess(sql.index("revoke all on function"),
                        sql.index("grant execute on function"))
        # 059 의 기존 키가 텍스트 그대로 살아 있는가(가산 외 변경 0).
        prev = (WEB_DIR / "migrations" /
                "059_fda_inspection_stats_freshness.sql").read_text(encoding="utf-8")
        for key in ("'fiscal_year_min'", "'unmapped_country_count'",
                    "'last_ingested_date_kst'", "'latest_inspection_end_date'"):
            self.assertIn(key, prev)
            self.assertIn(key, sql)
        # 신설 2키.
        self.assertIn("'by_quarter', coalesce((", sql)
        self.assertIn("'korea', jsonb_build_object(", sql)
        # 원문 텍스트는 어떤 경로로도 나가지 않는다(007/058 안전 계약 계승).
        for forbidden in ("finding_text", "source_url", "official_url"):
            self.assertNotIn(forbidden, sql)


class WebFindingsZoneComparisonTest(unittest.TestCase):
    """트렌드 대시보드 신규 패널 — findings_zone_category() RPC(038, 파라미터 없음)를
    findings_stats/findings_category_matrix 와 독립적으로 fetch 한다(실패해도 다른
    섹션에 영향 0, 히트맵과 동일 원칙). 여기선 셸 결정론·RPC 배선·점유율 계산 소스
    (zone 합계로 나눔, 절대 건수 비교 아님)·배지 임계(1.5배)·표본 하한(foreign_cnt
    20)·부제 숫자 비하드코딩·국가 구성 표기·해석/권고 문구 부재를 검증한다(실제
    집계 렌더 자체는 비골든 — trends.js 소관, WebTrendsRenderTest 와 동일 원칙)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_trendszone_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        # [컨셉 재정의 2026-08-26] 이 패널은 **데이터 현황 면으로 이사했다**.
        # 지적 경향 면에서 뺀 이유: "해외"는 미국 외 전체라 인도가 61%인 덩어리인데
        # 국내 사용자가 "해외=우리"로 읽기 쉽고, 보고 나서 할 일이 없다(규율 3).
        # 데이터 현황 면에서는 FDA 483 **코퍼스의 지리적 구성**이라는 사실 그대로 선다.
        cls.html = (cls.single / "findings" / "coverage" / "index.html").read_text(encoding="utf-8")
        cls.trends = (cls.single / "findings" / "trends" / "index.html").read_text(encoding="utf-8")
        cls.style_src = (WEB_DIR / "partials" / "trends_style.html").read_text(encoding="utf-8")
        cls.js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        cls.html_src = (WEB_DIR / "templates" / "coverage.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _fn(self, name):
        start = self.js_src.index("function " + name + "(")
        return self.js_src[start:self.js_src.index(chr(10) + "  }", start)]

    # ── 셸 결정론 ────────────────────────────────────────────────────────────
    def test_panel_shell_present_hidden_on_coverage_face(self):
        """038 미배포·빈 응답에서 trends.js 가 그대로 두는 상태(hidden)가 정적 셸의
        기본값이어야 한다. [컨셉 재정의] 자리는 데이터 현황 면이고, 지적 경향 면에는
        남아 있으면 안 된다 — 그 면에서 뺀 것이 이 재정의의 결정 중 하나다."""
        self.assertIn(
            '<section class="tr-block tr-zone-block" id="tr-zone-block" '
            'aria-label="해외 실사 vs 미국 내 실사" hidden>',
            self.html,
        )
        self.assertIn('<h2 class="tr-h">해외 실사 vs 미국 내 실사</h2>', self.html)
        self.assertIn('<p class="tr-zone-sub" id="tr-zone-sub"></p>', self.html)
        self.assertIn('<div id="tr-zone" class="tr-zone"></div>', self.html)
        self.assertIn('<p class="tr-note" id="tr-zone-countries"></p>', self.html)
        self.assertNotIn('id="tr-zone-block"', self.trends,
                         "해외vs미국이 지적 경향 면으로 되돌아왔다")

    def test_zone_elements_defensively_queried_not_in_hard_gate(self):
        """구버전 캐시 셸에 이 신규 블록이 없어도 페이지 전체(다른 패널)가 죽으면
        안 된다 — coverageNoteEl/heatmapNoteEl 관례와 동형으로 하드 게이트(if 문)에
        tr-zone-* 엘리먼트를 넣지 않는다."""
        for elid in ("tr-zone-block", "tr-zone-sub", "tr-zone", "tr-zone-countries"):
            self.assertIn(f'document.getElementById("{elid}")', self.js_src)
        gate = self.js_src[self.js_src.index("if (!cfg || !loadingEl"):]
        gate = gate[:gate.index("return;") + len("return;")]
        for forbidden in ("zoneBlockEl", "zoneEl", "zoneSubEl", "zoneCountriesEl"):
            self.assertNotIn(forbidden, gate)

    # ── RPC 배선 · 독립 fetch ────────────────────────────────────────────────
    def test_rpc_endpoint_present_with_no_params(self):
        self.assertIn('rpcEndpoint("findings_zone_category")', self.js_src)
        self.assertIn("function fetchZoneCategory()", self.js_src)
        fn = self.js_src[self.js_src.index("function fetchZoneCategory()"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn('method: "POST"', fn)
        self.assertIn('body: "{}",', fn)  # 038 계약 — 파라미터 없음

    def test_independent_fetch_chain_silent_fallback(self):
        """findings_stats/findings_category_matrix 체인과 별개 promise 체인 — 실패해도
        errorEl/contentEl 을 건드리지 않고 조용히 숨김 유지되어야 한다."""
        # ★앵커는 함수 **정의**가 아니라 호출 분기여야 한다 — 존이 세 면으로
        #   갈리면서 각 부 데이터 fetch 가 `if (WANT.x) {` 안으로 들어갔고,
        #   함수명만으로 자르면 정의부가 먼저 잡혀 체인을 못 본다.
        chain = self.js_src[self.js_src.index("if (WANT.zone) {"):]
        self.assertIn("renderZonePanel(data)", chain[:220])
        self.assertNotIn("errorEl.hidden", chain[:300])
        # [존 재편] 실패의 결과가 '섹션 숨김 유지'에서 '**탭 미노출**'로 바뀌었다 —
        # 이 패널이 독립 섹션이 아니라 통합 순위의 한 보기가 됐기 때문이다. 사용자가
        # 빈 탭을 누르는 일이 없다는 점에서 종전보다 조용한 실패다.
        self.assertIn("조용히 숨김 유지", chain[:300])

    # ── 점유율 계산 소스 — zone 합계로 나눔(절대 건수 비교 아님) ───────────────
    def test_share_computed_from_zone_totals_not_raw_counts(self):
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn(
            "var foreignTotal = cats.reduce(function (s, c) { return s + (c.foreign_cnt || 0); }, 0);",
            fn,
        )
        self.assertIn(
            "var usTotal = cats.reduce(function (s, c) { return s + (c.us_cnt || 0); }, 0);",
            fn,
        )
        self.assertIn("var fShare = safeShare(fCnt, foreignTotal);", fn)
        self.assertIn("var uShare = safeShare(uCnt, usTotal);", fn)
        self.assertIn("pctText(fCnt, foreignTotal)", fn)
        self.assertIn("pctText(uCnt, usTotal)", fn)
        # 두 시리즈를 같은 공통 스케일(maxShare)로 정규화 — zone 마다 따로 정규화하면
        # 한 행 안에서 "어느 zone 에 더 몰렸는가"를 막대 길이로 읽을 수 없다.
        self.assertIn(
            "var maxShare = rows.reduce(function (m, r) "
            "{ return Math.max(m, r.foreignShare, r.usShare); }, 0) || 1;",
            fn,
        )

    def test_safe_share_helper_guards_zero_denominator(self):
        self.assertIn("function safeShare(part, whole)", self.js_src)
        fn = self.js_src[self.js_src.index("function safeShare(part, whole)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("whole > 0 ?", fn)

    def test_sort_order_is_foreign_share_descending(self):
        self.assertIn(
            "}).sort(function (a, b) { return b.foreignShare - a.foreignShare; });",
            self.js_src,
        )

    # ── 0 나눗셈 방어 ────────────────────────────────────────────────────────
    def test_zero_or_empty_response_keeps_panel_hidden(self):
        """빈 응답·0 나눗셈에서 억지로 그리지 않고 숨김을 유지한다."""
        fn = self._fn("renderZonePanel")
        self.assertIn("if (!cats.length) return;", fn)
        self.assertIn("if (!(foreignTotal > 0) || !(usTotal > 0)) return;", fn)
        # [컨셉 재정의] 통합 순위의 한 보기였다가 독립 섹션으로 돌아왔다 — 자기를 편다.
        self.assertIn("zoneBlockEl.hidden = false;", fn)
        self.assertNotIn("markRankReady", fn)

    def test_defensive_null_guard_on_render_entry(self):
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("if (!zoneBlockEl || !zoneEl) return;", fn)

    # ── 배지 임계(1.5배) · 표본 하한(foreign_cnt 20) ────────────────────────
    def test_badge_ratio_threshold_is_1_5(self):
        self.assertIn("var ZONE_BADGE_RATIO = 1.5;", self.js_src)
        fn = self.js_src[self.js_src.index("function buildZoneRow(r, maxShare)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("r.ratio >= ZONE_BADGE_RATIO", fn)
        self.assertIn("isFinite(r.ratio)", fn)  # uShare=0(무한대 배수) 방어

    def test_badge_min_sample_threshold_is_20_with_rationale_comment(self):
        """표본 하한(foreign_cnt < 20 이면 배지 미표시)의 근거가 코드 주석에 남아
        있어야 한다 — 해외 표본(905건)이 얇아 소수 카테고리는 비율이 요동친다."""
        self.assertIn("var ZONE_MIN_FOREIGN_SAMPLE = 20;", self.js_src)
        idx = self.js_src.index("var ZONE_MIN_FOREIGN_SAMPLE = 20;")
        preceding_comment = self.js_src[max(0, idx - 900):idx]
        self.assertIn("표본", preceding_comment)
        fn = self.js_src[self.js_src.index("function buildZoneRow(r, maxShare)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("r.foreignCnt >= ZONE_MIN_FOREIGN_SAMPLE", fn)

    def test_badge_text_format_matches_spec_example(self):
        """스펙 예시 형태 "해외 2.0배" — 소수 1자리 반올림."""
        self.assertIn(
            '_t("해외 {ratio}배", { ratio: Math.round(r.ratio * 10) / 10 })', self.js_src
        )

    # ── 부제 숫자는 응답에서(하드코딩 금지) ─────────────────────────────────
    def test_subtitle_numbers_come_from_response_not_hardcoded(self):
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("fmtNum(foreign.findings)", fn)
        self.assertIn("fmtNum(foreign.documents)", fn)
        self.assertIn("fmtNum(foreign.countries)", fn)
        self.assertIn("fmtNum(us.findings)", fn)
        self.assertIn("fmtNum(us.documents)", fn)
        self.assertIn("zoneSubEl.textContent = sub;", fn)
        # 계약 예시 수치가 소스에 리터럴로 박혀 있으면 안 된다(응답 의존 확인).
        for literal in ("905", "175", "7288", "7,288", "1158", "1,158"):
            self.assertNotIn(literal, fn)


    def test_subtitle_mentions_fda483_scope_and_excluded_unknown(self):
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn('_t("FDA 483 기준 · 해외 {ff}건', fn)
        self.assertIn("scope.excluded_unknown_country", fn)
        self.assertIn('_t("소재국 미상 {n}건 제외"', fn)

    # ── 해외 국가 구성(오독 방지) ────────────────────────────────────────────
    def test_top_countries_top5_rendered(self):
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("(d.top_countries || []).slice(0, 5);", fn)
        self.assertIn('"해외 실사 구성: "', fn)

    # ── 국가명 한글화(ISO2 코드 기반, 056) ────────────────────────────────────
    def test_country_labels_ko_covers_all_mapping_codes(self):
        """[동기화 규칙 — 056, 확장 057] COUNTRY_LABELS_KO 는 원문 문자열이 아니라
        ISO2 코드가 키다(문자열은 반드시 낡는다 — 2026-07-31 시점 23종이 2026-08-11
        실측 85종으로 이미 낡아 있었다). 코드 **집합**은 grm_findings._COUNTRY_CODE_MAP
        (055/057 매핑 정본의 파이썬 파리티 사본)의 코드 전체와 정확히 일치해야 한다
        (기준선은 개수가 아니라 id 집합) — 한국어 라벨 값 자체는 이 저장소가 지정하는
        고정 계약이라 하드코딩 대조한다.

        057 이 FDA Data Dashboard API(inspections_classifications) CountryName 27종
        (21개 신규 코드 + 기존 코드 6개 재사용)을 매핑 정본에 추가해 47→68개 코드로
        늘렸다 — 이 사전(그리고 아래 expected 고정 계약)도 함께 늘려야 이 테스트가
        "사전은 반드시 낡는다"를 실제로 지킨다(코드만 추가하고 라벨을 안 늘리면 이
        테스트가 그 침묵 실패를 즉시 잡는다)."""
        m = re.search(r"var COUNTRY_LABELS_KO = \{(.*?)\n  \};", self.js_src, re.S)
        self.assertIsNotNone(m, "trends.js 에 COUNTRY_LABELS_KO 정의 미발견")
        body = m.group(1)
        # [i18n 2단계] 값은 `_t("…")` 로 감싸질 수 있다 — 선택적 래퍼를 허용한다.
        pairs = dict(re.findall(r'([A-Z]{2}):\s*(?:_t\()?"([^"]+)"\)?,?', body))

        canon_codes = set(grm_findings._COUNTRY_CODE_MAP.values())
        self.assertEqual(len(canon_codes), 68, "매핑 정본 코드 수가 68이 아님(전제 재확인 필요)")
        self.assertEqual(
            set(pairs), canon_codes,
            f"COUNTRY_LABELS_KO 코드 집합이 매핑 정본과 다름 — "
            f"누락: {sorted(canon_codes - set(pairs))} · 초과: {sorted(set(pairs) - canon_codes)}",
        )

        expected = {
            "US": "미국", "KR": "대한민국", "PR": "푸에르토리코", "IN": "인도", "CN": "중국",
            "JP": "일본", "DE": "독일", "CA": "캐나다", "FR": "프랑스", "GB": "영국",
            "IS": "아이슬란드", "IT": "이탈리아", "MY": "말레이시아", "ES": "스페인",
            "BE": "벨기에", "HU": "헝가리", "TW": "대만", "CH": "스위스", "CY": "키프로스",
            "AU": "호주", "IE": "아일랜드", "SE": "스웨덴", "JO": "요르단", "GR": "그리스",
            "DK": "덴마크", "NL": "네덜란드", "MX": "멕시코", "CZ": "체코", "LT": "리투아니아",
            "PL": "폴란드", "CL": "칠레", "AT": "오스트리아", "RO": "루마니아",
            "ZA": "남아프리카공화국", "BD": "방글라데시", "ID": "인도네시아", "LB": "레바논",
            "PT": "포르투갈", "SK": "슬로바키아", "LK": "스리랑카", "TR": "튀르키예",
            "NO": "노르웨이", "FI": "핀란드", "VN": "베트남", "BY": "벨라루스",
            "SI": "슬로베니아", "IL": "이스라엘",
            # [057] FDA Data Dashboard API CountryName 확장분 -- 21개 신규 코드
            # (KR/CZ/FI/IL/SI/NO 6개는 재사용이라 위 47개 안에 이미 있다).
            "SG": "싱가포르", "BR": "브라질", "TH": "태국", "MT": "몰타",
            "AR": "아르헨티나", "HR": "크로아티아", "HK": "홍콩", "CO": "콜롬비아",
            "NZ": "뉴질랜드", "BG": "불가리아", "DO": "도미니카공화국", "LV": "라트비아",
            "OM": "오만", "CR": "코스타리카", "EG": "이집트", "MO": "마카오",
            "PH": "필리핀", "UY": "우루과이", "AW": "아루바", "EE": "에스토니아",
            "AE": "아랍에미리트",
        }
        self.assertEqual(pairs, expected, "COUNTRY_LABELS_KO 값이 고정 계약과 다름")

    def test_country_label_helper_code_priority_with_raw_fallback(self):
        """countryLabelKo(code, country) — code 가 있으면 매핑(없는 코드는 코드 그대로,
        빈칸/추측 번역 금지)을, code 가 아예 없으면(055 미배포 구버전 RPC 응답 방어)
        원문 country 문자열로 폴백한다."""
        self.assertIn("function countryLabelKo(code, country)", self.js_src)
        fn = self.js_src[self.js_src.index("function countryLabelKo(code, country)"):]
        fn = fn[:fn.index("\n  }")]
        self.assertIn("COUNTRY_LABELS_KO[code] || code", fn)
        self.assertIn("return country || \"\";", fn)

    def test_top_countries_render_uses_code_then_falls_back_to_raw_country(self):
        """top_countries 렌더 루프가 c.code 를 우선 거치는 countryLabelKo() 를 쓰는지
        (055 findings_zone_category() 가 새로 주는 code 키 소비 확인 -- 배선 누락이
        이 저장소의 가장 흔한 결함이라 사용처가 코드에 실제로 있는지 문자열로 대조)."""
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        top_loop = fn[fn.index("top.forEach"):]
        self.assertIn("countryLabelKo(c.code, c.country)", top_loop)
        self.assertNotIn("createTextNode(c.country ", top_loop)

    def test_top_countries_loop_has_no_manual_summation(self):
        """top_countries 행은 서버(055 findings_zone_category, country_key 축으로 이미
        그룹화됨)가 준 값을 그대로 1행씩 표시할 뿐이다 -- 클라이언트가 다시 합산하지
        않는다(단순 표시만)."""
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        top_loop = fn[fn.index("top.forEach"):]
        self.assertNotIn("+=", top_loop)

    def test_misreading_guard_note_present_in_static_shell(self):
        """"해외"가 균질한 덩어리(≈한국)로 오독되지 않도록 하는 고정 안내문 — RPC
        성공 여부와 무관하게 정적 텍스트로 셸에 존재해야 한다(골든 리뷰 가능)."""
        self.assertIn(
            '"해외"는 미국 외 전체이며 국가별 구성이 고르지 않습니다.', self.html
        )

    # ── 해석/권고 문구 부재(트랙C 품질 기준) ────────────────────────────────
    def test_no_interpretive_or_prescriptive_language(self):
        """이 패널은 관측된 분포만 보여준다 — "~해야" 류 권고·해석 문구는 **렌더
        결과(정적 셸 HTML)** 어디에도 없어야 한다. trends.js 소스 자체는 검사 대상이
        아니다 — 코드 주석에는 "이런 문구를 넣지 않는다"는 금지 근거 서술이 정상적으로
        등장할 수 있다(다른 섹션 제거 근거 주석과 동일 패턴, test_evidence_grade_
        section_removed 참조)."""
        for forbidden in ("강화해야", "권고", "해야 합니다", "해야 한다",
                           "한국 공장은", "준비해야"):
            self.assertNotIn(forbidden, self.html, f"금지 문구 발견: {forbidden}")

    # ── 카테고리 라벨·XSS 계약 재사용 ────────────────────────────────────────
    def test_reuses_existing_category_labels_constant(self):
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("CATEGORY_LABELS[c.category_code]", fn)

    def test_no_innerhtml_data_injection(self):
        """innerHTML 대입은 컨테이너 비우기 "" 뿐(파일 상단 XSS 계약, findings.js/
        trends.js 공통)."""
        fn = self.js_src[self.js_src.index("function renderZonePanel(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        for m in re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', fn):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입 의심: {m.group(0)}")

    def test_css_scoped_to_page_not_grm_css(self):
        """grm.css 는 무변경 — 신규 스타일은 trends.html 자체 스코프 <style> 에만
        추가된다(.tr-fd-profile-link 관례와 동형)."""
        self.assertIn(".tr-zone-sub{", self.style_src)
        self.assertIn(".tr-zone-badge{", self.style_src)
        css_path = WEB_DIR / "assets" / "grm.css"
        if css_path.is_file():
            css_src = css_path.read_text(encoding="utf-8")
            self.assertNotIn(".tr-zone-badge", css_src)


# ── [트렌드 고도화] 최근 12개월 · 달라진 점 · 업체 찾기 · 사례 드릴다운 ────────
class WebTrendsRecentWindowTest(unittest.TestCase):
    """트렌드 대시보드 신규 3섹션 — 041_findings_recent_window.sql 의 두 RPC
    (findings_recent_window / findings_firm_search) + 026 findings_search 재사용.

    ★이 셋이 해결하는 문제: 기존 페이지는 전 기간 누적만 보여 줬는데 그 분모의 47%가
    2024년 한 해 공개분이라, "카테고리 순위"가 사실상 그 배치의 그림자였다. 즉 페이지
    제목이 약속하는 '트렌드'(시간에 따른 변화)를 답하는 집계가 서버에 아예 없었다.

    여기선 셸 결정론·RPC 배선·독립 실패 반경·비교 단위(문서 등장률, 건수 아님)·표본
    하한·교란 요인(소스 구성 변화) 공개·사례 문장의 출처(집계 RPC 아님)를 검증한다.
    실제 집계 렌더 자체는 비골든 — trends.js 소관(WebTrendsRenderTest 와 동일 원칙)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_trendsrecent_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "trends" / "index.html").read_text(encoding="utf-8")
        # [존 재편] 존이 세 면으로 갈렸다. 각 테스트가 자기 주제가 실제로 사는 면을
        # 보게 한다 — 한 페이지 전제로 쓴 배치 단언들이 여기서 갈린다.
        cls.inspections = (cls.single / "findings" / "inspections" / "index.html").read_text(encoding="utf-8")
        cls.coverage = (cls.single / "findings" / "coverage" / "index.html").read_text(encoding="utf-8")
        # 스코프 CSS 는 세 면이 함께 include 하는 파셜로 옮겼다 — "grm.css 를 건드리지
        # 않았는가"를 재는 단언들은 이 파셜을 봐야 한다(검사 대상이 바뀐 것일 뿐,
        # 재는 것은 그대로다).
        cls.style_src = (WEB_DIR / "partials" / "trends_style.html").read_text(encoding="utf-8")
        cls.js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        cls.html_src = (WEB_DIR / "templates" / "trends.html").read_text(encoding="utf-8")
        cls.sql041 = (WEB_DIR / "migrations" / "041_findings_recent_window.sql"
                      ).read_text(encoding="utf-8")
        cls.sql052 = (WEB_DIR / "migrations" /
                      "052_findings_recent_window_category_source.sql").read_text(encoding="utf-8")
        cls.sql053 = (WEB_DIR / "migrations" /
                      "053_findings_recent_window_lane.sql").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _fn(self, name):
        """`function name(...)` 본문을 들여쓰기 2칸 닫는 중괄호까지 잘라 낸다."""
        start = self.js_src.index("function " + name + "(")
        return self.js_src[start:self.js_src.index("\n  }", start)]

    # ── 셸 결정론 ────────────────────────────────────────────────────────────
    def test_rank_and_mover_shells_present_hidden(self):
        """041 미적용 라이브·fetch 실패 시 trends.js 가 그대로 두는 상태(hidden)가 정적
        셸의 기본값이어야 한다 — 골든 결정론."""
        self.assertIn(
            '<section class="tr-block tr-rank-block" id="tr-rank-block" '
            'aria-label="가장 많이 지적된 영역" hidden>',
            self.html,
        )
        self.assertIn(
            '<section class="tr-block tr-move-block" id="tr-move-block" '
            'aria-label="달라진 점" hidden>',
            self.html,
        )
        # 기관 선택도 기본 hidden — 052/053 미적용 응답에서는 레인을 가를 수 없다.
        self.assertIn('<div class="tr-agency" id="tr-agency" role="group" '
                      'aria-label="기관 선택" hidden>', self.html)
        self.assertIn('<details class="tr-fold" id="tr-move-fold">', self.html)
        self.assertIn('id="tr-move-summary"', self.html)
        for frag in ('<p class="tr-read" id="tr-rank-read"></p>',
                     '<p class="tr-rank-sub" id="tr-rank-sub"></p>',
                     '<div class="tr-recent-cats" id="tr-recent-cats"></div>',
                     '<p class="tr-note" id="tr-rank-note"></p>',
                     '<div id="tr-move-up" class="tr-move-list"></div>',
                     '<div id="tr-move-down" class="tr-move-list"></div>'):
            self.assertIn(frag, self.html)

    def test_agency_picker_leads_the_page(self):
        """[컨셉 재정의] 화면 맨 위는 **기관 선택**이다.

        아래 모든 수치의 분모를 정하는 선택이라, 수치를 보고 난 뒤에 고르게 하면 이미
        잘못 읽은 뒤다. 실측 근거: '기타'를 빼고 기관별로 세면 FDA 상위 5와 식약처
        상위 5가 하나도 겹치지 않는다 — 합산 순위는 어느 기관의 현실도 아니다."""
        i_agency = self.html.index('id="tr-agency"')
        i_rank = self.html.index('id="tr-rank-block"')
        i_move = self.html.index('id="tr-move-block"')
        i_cfr = self.html.index('id="tr-cfr-block"')
        self.assertTrue(i_agency < i_rank < i_move < i_cfr)
        # 합산을 기본값으로 두지 않는다.
        # ★[2026-08-28] 종전에는 `readStoredAgency() || "mfds"` 라는 **구현 문자열**을
        #   요구했다. 기본값을 상수로 뽑으면서(DEFAULT_AGENCY_KEY) 그 줄이 사라졌고,
        #   `tests/test_trends_agency_views.py` 는 오히려 그 문자열이 **없어야** 한다고
        #   잰다 — 두 검사가 정면으로 부딪혔다. 지키려던 뜻은 "기본값이 합산이 아니다"
        #   하나뿐이므로 그것만 잰다.
        #   상수를 쓰는 이유: 종전엔 초기화와 폴백 두 곳에 기본값이 따로 적혀 있어
        #   한쪽만 고치면 갈렸다(그리고 폴백은 `AGENCY_VIEWS[0]` 이라 목록 순서에
        #   묶여 있었다 — '전체'를 앞으로 옮기는 순간 합산이 기본이 됐을 자리다).
        self.assertIn('var DEFAULT_AGENCY_KEY = "mfds";', self.js_src)
        self.assertIn("readStoredAgency() || DEFAULT_AGENCY_KEY", self.js_src)
        self.assertNotIn('DEFAULT_AGENCY_KEY = "all"', self.js_src,
                         "합산이 기본값이 됐다")

    def test_other_category_excluded_from_ranking_but_kept_in_denominator(self):
        """[컨셉 재정의 규율 2] '기타 품질시스템'은 규제 현상이 아니라 **분류기가 그
        문장을 어디에도 못 넣었다는 내부 상태**다(실측: FDA 경고서한 3.4% vs 식약처
        36.7% · 캐나다 26.7%). 사용자는 '기타 946건'을 보고 아무 행동도 할 수 없는데
        그것이 순위 1위로 화면의 5분의 1을 차지했다.

        ★순위에서는 빼되 **분모에는 남긴다**. 분모까지 줄이면 나머지 항목의 비율이
        부풀어(무균보증 12% → 15%) 분류 실패를 감추려다 다른 수치를 거짓말하게 된다.
        ★감추지 않고 크기를 각주에 적는다."""
        fn = self.js_src[self.js_src.index("function buildAgencyRanking(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn('c.category_code !== "other_quality_system"', fn)
        self.assertIn("total += n;", fn)          # 분모는 기타 포함
        self.assertIn('if (c.category_code === "other_quality_system") excluded += n;', fn)
        self.assertIn("아직 세부 분류가 되지 않아 순위에서 뺐습니다", self.js_src)
        self.assertIn("분모에는 그대로 들어 있습니다", self.js_src)


    # ── 순위의 모집단 = 실사 지적 ────────────────────────────────────────────
    def test_ranking_population_is_inspection_findings_only(self):
        """★순위 축은 "어떤 지적을 받는가"다. 회수 공고와 행정처분은 지적이 아니다.

        053 이 식약처를 세 채널로 쪼개 두었는데(MFDS/gmp-inspection ·
        MFDS/recall-quality · MFDS/admin-action) 기관 선택이 접두 하나로 셋을 도로
        합치고 있었다. 실측(2026-08-26, 최근 12개월)으로 **1위가 통째로 바뀐다**:
            합산      1위 불만/회수 187 · 2위 세척밸리 69 · 3위 문서화 67
            실사만    1위 세척밸리 58 · 2위 밸리데이션 51 · 5위 데이터완전성 40
        합산 1위 "불만/회수 187"은 규제 현상이 아니라 **회수 공고를 회수로 분류한
        동어반복**이다 — 같은 959건이 본문에 '회수'가 있으면 불만/회수(418),
        없으면 기타(456)로 갈렸다. 합산 3위 문서화 67 중 34 는 행정처분의 '재평가
        자료 미제출'이라 기록관리 SOP 문제로 오독된다.
        식약처 2,008건 중 1,105건(55%)이 실사 지적이 아니다."""
        self.assertIn(
            'var NON_INSPECTION_TYPES = { "recall-quality": 1, "admin-action": 1 };',
            self.js_src)
        fn = self._fn("isInspectionLane")
        self.assertIn("indexOf(\"/\")", fn)
        self.assertIn("if (i < 0) return true;", fn)          # 쪼개지 않은 레인은 통과
        self.assertIn("return !NON_INSPECTION_TYPES[", fn)
        kept = self._fn("agencyKept")
        self.assertIn("if (isInspectionLane(lane)) kept[lane] = true;", kept)

    def test_lane_rule_is_denylist_so_a_new_channel_is_not_silently_dropped(self):
        """[음성 검사] 규칙을 "gmp-inspection 만 통과"로 적으면 안 된다.

        그렇게 적으면 나중에 053 이 다른 소스를 쪼갤 때 그 소스가 **통째로 조용히**
        순위에서 사라진다 — 이 저장소가 CI 허용목록에서 이미 겪은 실패다(손열거가
        낡아 침묵 미실행). 그래서 아는 비실사 유형만 빼고 모르는 유형은 남긴다."""
        fn = self._fn("isInspectionLane")
        self.assertNotIn("gmp-inspection", fn,
                         "판정이 허용목록으로 적혔다 — 새 채널이 조용히 사라진다")
        self.assertNotIn("gmp-inspection", self._fn("agencyKept"))
        self.assertIn("모르는 유형은 남긴다", self.js_src)

    def test_agency_kept_still_returns_a_plain_lane_map(self):
        """agencyKept 의 소비자는 둘이다(buildAgencyRanking · renderMovers).

        renderMovers 는 이 값을 scope[lane] 으로 직접 찾으므로 반환 모양을
        {kept, offCnt} 로 바꾸면 **문법은 통과한 채** 창 합계가 0이 되어 '달라진 점'이
        영영 안 그려진다. 실제로 이 PR 작업 중 한 번 그렇게 만들었다 — 뺀 몫은
        별도 함수로 센다."""
        kept = self._fn("agencyKept")
        self.assertIn("return kept;", kept)
        self.assertNotIn("offCnt", kept)
        self.assertIn("function agencyOffCount(grid, view)", self.js_src)
        movers = self.js_src[self.js_src.index("function renderMovers(data, view)"):]
        movers = movers[:movers.index("\n  }\n")]
        self.assertIn("var scope = grid.length ? agencyKept(grid, v) : null;", movers)
        self.assertIn("if (!scope[t.lane]) return;", movers)

    def test_excluded_non_inspection_volume_is_disclosed_with_a_destination(self):
        """조용히 빼면 "식약처 자료가 이것뿐"으로 읽힌다. 크기를 적고 갈 곳을 준다.

        규율 2('분류 실패를 감추지 않는다')와 같은 자리·같은 방식이다 — 다만 이쪽은
        분류 실패가 아니라 **모집단이 다른 문서**라 분모에서도 뺀다(기타는 분모에 남는다)."""
        self.assertIn("회수 공고·행정처분 ", self.js_src)
        self.assertIn("실사 지적이 아니라 이 순위에서 제외했습니다", self.js_src)
        self.assertIn("지적사항 검색에서 모두 확인하실 수 있습니다", self.js_src)
        self.assertIn("offCnt: agencyOffCount(grid, view),", self.js_src)
        # 읽는 법도 분모에 맞춰 다시 적혀 있어야 한다.
        self.assertIn("{label} 실사 지적에서만 셉니다. 오른쪽 %는 그 기간 {label} 지적 전체", self.js_src)
        self.assertIn("실사에서 나온 지적만 합쳐 센 순위입니다", self.js_src)

    def test_new_elements_defensively_queried_not_in_hard_gate(self):
        """구버전 캐시 셸에 이 블록들이 없어도 페이지 전체가 죽으면 안 된다 — zone/heatmap
        관례와 동형으로 하드 게이트(if 문)에 신규 엘리먼트를 넣지 않는다."""
        for elid in ("tr-recent-cats", "tr-move-block", "tr-move-up", "tr-move-down",
                     "tr-move-source", "tr-move-note", "tr-rank-block", "tr-rank-read",
                     "tr-rank-sub", "tr-rank-note", "tr-agency", "tr-agency-btns"):
            self.assertIn(f'document.getElementById("{elid}")', self.js_src)
        gate = self.js_src[self.js_src.index("if (!cfg || !loadingEl"):]
        gate = gate[:gate.index("return;") + len("return;")]
        for forbidden in ("moveBlockEl", "recentCatsEl", "rankBlockEl", "agencyEl"):
            self.assertNotIn(forbidden, gate)
        # [존 재편] 이 원칙을 **전 섹션으로 확장**했다. 존이 세 면으로 갈리면서 "자기 면에
        # 없는 섹션" 이 정상이 됐기 때문이다 — 섹션 엘리먼트가 게이트에 하나라도 남아
        # 있으면 그 면이 통째로 죽는다.
        for forbidden in ("statsEl", "catEl", "heatmapEl", "yearEl", "firmsEl", "sourceEl"):
            self.assertNotIn(forbidden, gate,
                             f"하드 게이트에 섹션 엘리먼트({forbidden})가 남아 있다")

    # ── RPC 배선 · 독립 실패 반경 ────────────────────────────────────────────
    def test_recent_window_rpc_wired_with_months_param(self):
        self.assertIn('rpcEndpoint("findings_recent_window")', self.js_src)
        fn = self._fn("fetchRecentWindow")
        self.assertIn('method: "POST"', fn)
        self.assertIn("JSON.stringify({ p_months: 12 })", fn)


    def test_examples_come_from_findings_search_not_stats_rpc(self):
        """★사례 문장은 집계 RPC(007/010/017/038/041 — 원문 무반환 계약)가 아니라 026
        findings_search 에서 온다. 그 함수는 security invoker 라 공개 게이트(010 정책)를
        통과한 행만 돌려준다 = /findings/ 검색 페이지와 완전히 같은 경로다."""
        self.assertIn('rpcEndpoint("findings_search")', self.js_src)
        fn = self._fn("fetchCategoryExamples")
        self.assertIn("p_category: code", fn)
        self.assertIn('p_sort: "date_desc"', fn)
        self.assertIn("p_docs_per_page: EXAMPLE_ROWS", fn)
        # 본문 텍스트를 읽는 곳은 이 한 경로뿐이어야 한다 — 집계 RPC 응답에서
        # finding_text 를 꺼내 쓰는 코드가 생기면 계약이 조용히 깨진 것이다.
        # ★[다국어 3단계] 원문 필드를 만지는 자리는 **언어별 선택 사본 한 곳으로 모였다**
        #   (`_bodyText`/`_altText`). 그래서 파일 전체의 참조 수는 "사본 안 4회 + 그 밖 0회"
        #   여야 한다 — 사본 밖에서 원문을 직접 꺼내면 그게 계약 위반이다.
        outside = self.js_src.replace(grm_i18n.JS_BODY_SHIM, "")
        self.assertEqual(outside.count("finding_text"), 0,
                         "본문 선택 사본 밖에서 finding_text 를 직접 읽는다")
        self.assertEqual(grm_i18n.JS_BODY_SHIM.count("finding_text"), 4)
        item_fn = self._fn("buildExampleItem")
        self.assertIn("_bodyText(f)", item_fn)
        # 사례 경로의 출처가 findings_search 임을 파일 계약 주석이 못박는다.
        self.assertIn("security invoker", self.js_src)

    def test_recent_chain_is_the_primary_data_on_this_face(self):
        """[컨셉 재정의] 이 면의 **주 데이터**가 007(누적 통계) → 041(최근 창)로 바뀌었다
        — 핵심 통계 5개를 걷어내면서 007 을 아예 치지 않는다. 그래서 로딩 해제도 041 이
        책임진다(주 데이터가 실패하면 빈 화면 대신 안내로 내린다)."""
        want = self.js_src[self.js_src.index("var WANT = ({"):]
        want = want[:want.index("})[page]")]
        trends_line = [l for l in want.split("\n") if l.strip().startswith("trends:")][0]
        self.assertIn("recent: true", trends_line)
        self.assertNotIn("stats", trends_line)
        self.assertNotIn("zone", trends_line)
        chain = self.js_src[self.js_src.index("if (WANT.recent) {"):]
        chain = chain[:chain.index("\n  }")]
        self.assertIn("renderRecentWindow(data)", chain)
        self.assertIn("failContent", chain)

    def test_movers_reuse_same_response_no_extra_fetch(self):
        """달라진 점은 최근 12개월과 **같은 응답**에서 파생한다 — 추가 네트워크 호출 0."""
        fn = self.js_src[self.js_src.index("function renderMovers(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertNotIn("fetch(", fn)
        self.assertNotIn("rpcEndpoint(", fn)

    # ── 비교 단위: 구성비(합이 100%로 닫히는 지표) ──────────────────────────
    def test_movers_use_composition_share_that_sums_to_100(self):
        """★분모의 성격이 변하는 지표는 추세를 못 잰다. 초기 구현은 '문서 등장률'
        (그 창 문서 중 이 영역이 지적된 문서 비율)을 썼는데, 등장률은 합이 100%로
        고정되지 않아 창마다 문서당 지적 수가 달라지면(실측 3.9 → 2.7건/문서) 전 영역이
        한 방향으로 쏠린다 — 무균보증이 문서 119→136건으로 **늘었는데도** 등장률은
        33%→20%로 떨어져 '줄었다'로 표시됐다. 구성비(건수 기준)는 각 창에서 합이 정확히
        100%라 늘어난 만큼 어딘가는 줄고, 두 창의 비교가 성립한다."""
        fn = self.js_src[self.js_src.index("function renderMovers(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("var cc = c.cur_cnt || 0, pc = c.prev_cnt || 0;", fn)
        # 052 이후 분모는 **소스 구성을 맞춘 뒤의** 창 합계(mix.curFindings)다. 지키는
        # 성질은 그대로 — 분자·분모가 같은 모집단이라 각 창에서 합이 정확히 100%다.
        # (변수 이름이 아니라 성질을 고정한다 — 이름으로 박아 두면 리팩터링이 의미 없는
        #  실패를 낸다. 아래 test_source_mix_alignment_* 가 그 모집단의 정의를 따로 잠근다.)
        self.assertIn(
            "deltaPp: (shareOf(cc, mix.curFindings) - shareOf(pc, mix.prevFindings)) * 100,", fn)
        self.assertIn("curPct: pctText(cc, mix.curFindings),", fn)
        self.assertIn("prevPct: pctText(pc, mix.prevFindings),", fn)
        # [컨셉 재정의] 조정 전 원본 분모는 **고른 기관의 창 합계**다(문서 수 아님).
        # 전 기관 합계로 문턱을 넘겨 놓고 표만 기관별로 그리면, 얇은 기관에서 한두
        # 건짜리 변화가 크게 보인다.
        self.assertIn("var scope = grid.length ? agencyKept(grid, v) : null;", fn)
        self.assertIn("curFindings += t.cur;", fn)
        self.assertIn("prevFindings += t.prev;", fn)
        # 레인을 가를 수 없는 응답에서는 종전 전역 합계로 후퇴한다.
        self.assertIn("curFindings = Number((totals.cur || {}).findings) || 0;", fn)
        self.assertNotIn("docShare(", self.js_src)   # 되돌린 지표가 잔존하면 안 된다
        self.assertIn("function shareOf(part, whole)", self.js_src)
        self.assertIn("whole > 0 ?", self._fn("shareOf"))

    def test_movers_show_counts_beside_share_to_prevent_misreading(self):
        """"비중이 줄었다"는 "건수가 줄었다"가 아니다 — 실측 무균보증은 312→309건으로
        거의 그대로인데 비중은 22%→17%다. 각 행에 건수를 병기하고, 표제도 '줄어든'이
        아니라 '비중이 줄어든'이며, 노트가 그 차이를 한 줄로 못박는다."""
        row = self._fn("buildMoverRow")
        self.assertIn(
            '_t("지적 {prev} → {cur}건", { prev: fmtNum(r.prevCnt), cur: fmtNum(r.curCnt) })', row)
        self.assertIn('el("span", "tr-mv-cnt"', row)
        self.assertIn("<h3 class=\"tr-sub-h\">비중이 커진 영역</h3>", self.html)
        self.assertIn("<h3 class=\"tr-sub-h\">비중이 줄어든 영역</h3>", self.html)
        fn = self.js_src[self.js_src.index("function renderMovers(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("건수가 늘어도 다른 영역이 ", fn)
        self.assertIn("더 늘면 비중은 줄어듭니다", fn)
        # 정적 셸에도 "합이 100%" 라는 성질을 적어 둔다(골든 리뷰 가능).
        self.assertIn("비중은 두 기간 각각에서 합이 100%라", self.html)

    def test_ranking_unit_is_count_plus_share(self):
        """순위는 건수 + 구성비로 통일한다(문서 수는 툴팁으로 남긴다) — 잣대가 다르면
        기관을 바꿔 가며 비교할 수 없다."""
        fn = self.js_src[self.js_src.index("function buildAgencyRanking(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn(".sort(function (a, b) {", fn)
        self.assertIn("b.cnt - a.cnt || a.code.localeCompare(b.code)", fn)
        self.assertIn(".slice(0, RECENT_CAT_ROWS)", fn)
        row = self._fn("buildRecentCatRow")
        self.assertIn(
            '_t("{n}건 · {pct}", { n: fmtNum(entry.cnt), pct: pctText(entry.cnt, curFindings) })',
            row)
        self.assertIn('_t("최근 12개월 지적 {n}건 · 문서 {d}건",', row)   # 문서 수는 툴팁으로 보존

    # ── 표본 하한 · 유의 폭(억지 해석 금지) ──────────────────────────────────
    def test_sample_and_significance_thresholds_declared_with_rationale(self):
        """임계값은 전부 상수로 선언하고, **왜 그 값인지**를 바로 앞 주석에 남긴다 —
        ZONE_MIN_FOREIGN_SAMPLE 이 확립한 관례(숫자만 있는 임계는 나중에 아무도 못 고친다)."""
        for const, val in (("WINDOW_MIN_FINDINGS", "200"), ("MOVER_MIN_SAMPLE", "20"),
                           ("MOVER_MIN_PP", "1.0"), ("MOVER_MAX_ROWS", "5")):
            decl = f"var {const} = {val};"
            self.assertIn(decl, self.js_src)
            idx = self.js_src.index(decl)
            preceding = self.js_src[max(0, idx - 500):idx]
            self.assertIn("//", preceding, f"{const} 근거 주석 누락")
        for const in ("WINDOW_MIN_FINDINGS", "MOVER_MIN_SAMPLE"):
            idx = self.js_src.index(f"var {const} = ")
            self.assertIn("표본", self.js_src[max(0, idx - 500):idx],
                          f"{const} 표본 근거 서술 누락")

    def test_thin_windows_hide_the_whole_comparison(self):
        """두 창 중 어느 쪽이든 표본이 얇으면 비교 자체를 하지 않는다(숨김 유지)."""
        fn = self.js_src[self.js_src.index("function renderMovers(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn(
            "if (curFindings < WINDOW_MIN_FINDINGS || prevFindings < WINDOW_MIN_FINDINGS) return;",
            fn)
        self.assertIn("moveBlockEl.hidden = false;", fn)
        # ★[컨셉 재정의] 규율이 강해졌다. 종전에는 "hidden=true 로 끄지 말고 early
        #   return 하라"였다(그리고 나서 끄면 이전 표가 잠깐 남으므로). 이제 기관을
        #   바꿀 때마다 **같은 페이지에서 다시 판정**하므로 early return 만으로는 앞
        #   기관의 표가 그대로 남는다 — 함수 첫머리에서 한 번 끄고, 조건을 통과한
        #   경우에만 편다. 끄는 지점이 **판정보다 앞**이라는 것이 핵심이다.
        fn_head = fn[:fn.index("var d = data || {};")]
        self.assertIn("moveBlockEl.hidden = true;", fn_head)
        self.assertEqual(fn.count("moveBlockEl.hidden = true;"), 1)

    def test_dropped_thin_categories_are_disclosed_not_silently_cut(self):
        """표본이 얇아 뺀 영역이 있으면 **몇 개를 뺐는지 화면에 적는다**(조용한 축소 금지 —
        renderHeatmap 의 tr-heatmap-note 와 동일 원칙)."""
        fn = self.js_src[self.js_src.index("function renderMovers(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("dropped += 1;", fn)
        self.assertIn("if (dropped > 0) {", fn)
        self.assertIn("개 영역은 비율이 흔들려 뺐습니다.", fn)
        self.assertIn("if (r.curCnt + r.prevCnt >= MOVER_MIN_SAMPLE) return true;", fn)

    def test_empty_mover_list_says_so_instead_of_rendering_nothing(self):
        fn = self._fn("fillMoverList")
        self.assertIn('_t("기준({n}%p 이상)을 넘는 변화가 없습니다.", { n: MOVER_MIN_PP })', fn)

    # ── 교란 요인 공개(소스 구성 변화) ──────────────────────────────────────
    def test_source_mix_shift_is_disclosed_from_response(self):
        """증감 비교의 최대 교란 요인은 소스 구성 변화다 — 한쪽 창에만 새 소스가 들어와
        있으면 카테고리 구성이 달라진 게 아니라 모집단이 달라진 것이다. 같은 응답의
        by_source(cur/prev)로 두 기간 구성을 나란히 적는다(하드코딩 금지)."""
        fn = self._fn("renderMoverSourceLine")
        self.assertIn("s.prev_cnt", fn)
        self.assertIn('_t("두 기간의 소스 구성: {list}.", {', fn)
        self.assertIn("소스 구성이 달라지면 위 증감도 함께 움직입니다.", fn)
        self.assertIn("moveSourceEl.textContent =", fn)
        # 분모는 증감과 같은 단위(지적 건수)여야 독자가 두 수치를 견줄 수 있다.
        self.assertIn("shareOf(s.cnt, curFindings)", fn)
        self.assertIn("pctText(s.prev_cnt || 0, prevFindings)", fn)
        # 실측 수치가 리터럴로 박혀 있으면 안 된다(응답 의존 확인).
        for literal in ("682", "362", "1837", "1,837", "1421"):
            self.assertNotIn(literal, fn)

    # ── [052] 소스 구성 정렬 — 각주로 적는 것에서 계산에서 빼는 것으로 ──────────
    #
    # ★왜 이 묶음이 생겼나(실측 2026-08-06, 041 과 같은 필터):
    #   국내 백필로 식약처 점유율이 직전 10.63% → 최근 30.01%(2.82배)로 벌어지자, 표시
    #   8행 중 5행이 유령이 되고 그중 3행은 **부호까지 반대**였다(기타 품질시스템
    #   +3.75 → 정렬 후 −0.18 등). 진짜 신호인 컴퓨터화시스템(+1.18)은 임계 아래 가려졌다.
    #   041 의 by_source 주석이 바로 이 상황을 예견하고 "화면에 나란히 적는다"로 대응했는데,
    #   각주는 수동적이고 표제는 단정적이라 독자가 표를 먼저 믿는다. 그래서 계산에서 뺀다.

    def test_migration_052_only_adds_a_key_and_keeps_041_contract(self):
        """052 는 041 과 **같은 함수**의 create or replace 다. 기존 5개 키를 하나도
        건드리지 않아야 한다 — 깨면 라이브 화면이 통째로 조용히 빈다."""
        sql = self.sql052
        self.assertIn("create or replace function public.findings_recent_window(p_months integer",
                      sql)
        for key in ("'scope'", "'totals'", "'by_month'", "'by_category'", "'by_source'"):
            self.assertIn(key, sql, f"041 기존 키 {key} 소실")
        self.assertIn("'by_category_source'", sql)
        # 041 안전 계약 승계 — 원문 텍스트는 어떤 키로도 나가지 않는다.
        # ★검사 범위는 **함수 본문**이다. 헤더 주석과 말미 검증 쿼리는 이 계약을 *설명*
        #   하느라 같은 이름을 적으므로, 파일 전체를 보면 자기 설명에 자기가 걸린다.
        body = sql[sql.index("as $$"):sql.index("$$;")]
        for leak in ("finding_text", "evidence_url", "raw_json"):
            self.assertNotIn(leak, body, f"안전 계약 위반: {leak}")
        self.assertIn("security definer", sql)
        self.assertIn("set search_path = public", sql)
        self.assertIn("scope_status = 'ok'", sql)
        # 041 의 by_source 키 이름 비대칭(cnt/docs)은 고치지 않는다(하위호환).
        self.assertIn("'cnt',      cur_cnt,  'docs',      cur_docs,", sql)

    def test_migration_052_reuses_the_single_scan_ctes(self):
        """새 키가 테이블을 다시 훑으면 041 이 세운 '두 창을 한 번만 스캔' 계약이 깨지고,
        필터·창 경계가 갈릴 여지가 생긴다. cur/prv CTE 를 그대로 재사용해야 한다."""
        sql = self.sql052
        start = sql.index("'by_category_source'")
        # ★슬라이스를 **함수 본문 끝**에서 닫는다 — 파일 끝까지 자르면 뒤따르는
        #   comment on / revoke / 검증 주석의 "public.findings" 가 섞여 들어와 오탐이 난다.
        end = sql.index("$$;", start)
        cross = sql[start:end]
        self.assertNotIn("public.findings", cross, "새 키가 테이블을 재스캔한다")
        self.assertIn("from cur group by category_code, source", cross)
        self.assertIn("from prv group by category_code, source", cross)

    def test_source_alignment_thresholds_declared_with_rationale(self):
        """임계는 상수 + 근거 주석(ZONE_MIN_FOREIGN_SAMPLE 관례).

        ★배율 상한은 **쫓지 말아야 하는 값**이고 주석이 그 사실을 말해야 한다. 하루에
        두 번 낡았다 — 99.2배(상한 3 제안) → 2.82(상한 2 로 낮춤) → 1.572(게이트 통과).
        세 번째에 값을 또 낮추는 것은 답이 아니었다: **문제는 임계값이 아니라 축의
        입도**였고, 레인 축으로 낮추자 같은 상한 2 로 정확히 갈렸다."""
        for const, val in (("MOVER_SOURCE_MIN", "10"), ("MOVER_SOURCE_MAX_RATIO", "2")):
            decl = f"var {const} = {val};"
            self.assertIn(decl, self.js_src)
            preceding = self.js_src[max(0, self.js_src.index(decl) - 1200):self.js_src.index(decl)]
            self.assertIn("//", preceding, f"{const} 근거 주석 누락")
        idx = self.js_src.index("var MOVER_SOURCE_MAX_RATIO = ")
        why = self.js_src[max(0, idx - 1200):idx]
        self.assertIn("축의 입도", why, "임계값이 아니라 축이 문제였다는 교훈 누락")
        self.assertIn("비교 단위가 맞는지", why, "다음 사람에게 줄 지침 누락")

    def test_alignment_narrows_numerator_and_denominator_together(self):
        """★분모에서만 빼면 결함이 커진다(실측: 기타 품질시스템 +3.65 → +5.27, 없던
        유령 2행 신규 발생). 052 교차표로 분자(카테고리별 건수)와 분모(창 합계)를
        **같은 소스 집합**으로 함께 좁혀야 한다."""
        fold = self._fn("foldCategorySource")
        for f in ("cur_cnt", "prev_cnt", "cur_docs", "prev_docs"):
            self.assertIn(f"e.{f} +=", fold, f"{f} 를 접지 않으면 툴팁이 본문과 어긋난다")
        self.assertIn("if (!kept[r.lane || r.source]) return;", fold)
        align = self._fn("alignSourceMix")
        self.assertIn("keptCur += c;", align)
        self.assertIn("keptPrev += p;", align)
        self.assertIn("foldCategorySource(grid, kept)", align)

    def test_alignment_falls_back_when_response_lacks_cross_tab(self):
        """052 미적용 라이브·구버전 캐시에서는 이 키가 없다 — 조정 없이 종전 경로로
        가야 하고, 패널이 깨지면 안 된다(신·구 어느 조합에서도)."""
        align = self._fn("alignSourceMix")
        self.assertIn("var grid = d.by_category_source;", align)
        self.assertIn("if (!grid || !grid.length) return raw;", align)
        # [컨셉 재정의] 후퇴 기준 표는 **기관을 가를 수 있으면 그 기관으로 접은 표**,
        # 아니면 종전대로 합산 by_category 다.
        self.assertIn("(d.by_category || [])", align)
        self.assertIn("foldCategorySource(grid, scoped)", align)
        self.assertIn("applied: false", align)

    def test_alignment_never_shows_an_unadjusted_table_silently(self):
        """★견줄 수 있는 소스가 하나도 없는 상황이 가장 못 믿을 상황이다. 그때 조정 전
        표를 아무 고지 없이 내면 '캐치올이 정상 응답처럼 보인다'는 이 저장소의 반복
        실패와 같은 형태가 된다 — usable=false 로 알리고 패널을 숨긴다."""
        align = self._fn("alignSourceMix")
        self.assertIn("raw.usable = false;", align)
        self.assertIn("raw.dropped = dropped;", align)
        fn = self.js_src[self.js_src.index("function renderMovers(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("if (!mix.usable) return;", fn)
        # 조정 후 창이 얇아져도 비교하지 않는다.
        self.assertIn("mix.curFindings < WINDOW_MIN_FINDINGS", fn)
        # ★[컨셉 재정의] 규율이 강해졌다. 종전에는 "hidden=true 로 끄지 말고 early
        #   return 하라"였다(그리고 나서 끄면 이전 표가 잠깐 남으므로). 이제 기관을
        #   바꿀 때마다 **같은 페이지에서 다시 판정**하므로 early return 만으로는 앞
        #   기관의 표가 그대로 남는다 — 함수 첫머리에서 한 번 끄고, 조건을 통과한
        #   경우에만 편다. 끄는 지점이 **판정보다 앞**이라는 것이 핵심이다.
        fn_head = fn[:fn.index("var d = data || {};")]
        self.assertIn("moveBlockEl.hidden = true;", fn_head)
        self.assertEqual(fn.count("moveBlockEl.hidden = true;"), 1)

    def test_dropped_sources_are_named_with_the_reason_that_actually_fired(self):
        """조용히 빼면 위 표가 전량 비교처럼 보인다. 그리고 ★사유 문구는 실제로 성립한
        조건과 같아야 한다 — 표본 검사 조건은 OR 이므로 '두 기간 모두 적다'가 아니라
        '한쪽 기간이 적다'다. (식약처는 807/174 라 표본이 아니라 배율로 걸린다.)"""
        line = self._fn("renderMoverSourceLine")
        self.assertIn("비교에서 뺀 소스: ", line)
        self.assertIn('s.reason === "thin"', line)
        self.assertIn("한쪽 기간이 ", line)
        self.assertIn("두 기간 자료량 차이가 큼", line)
        self.assertNotIn("두 기간 모두", line)
        align = self._fn("alignSourceMix")
        self.assertIn('reason: "thin"', align)
        self.assertIn('reason: "skew"', align)

    def test_source_mix_line_keeps_unadjusted_totals(self):
        """★이 줄만은 조정 **전** 총량 기준이어야 한다 — 뺀 소스까지 포함한 전체 구성을
        보여야 독자가 무엇을 뺐는지 대조할 수 있다. 조정 총량을 넘기면 정직성 고지 자체가
        사라진다(오탐 수리가 새 침묵을 만드는 형태)."""
        fn = self.js_src[self.js_src.index("function renderMovers(data, view)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("renderMoverSourceLine(d.by_source, curFindings, prevFindings, mix.dropped)",
                      fn)
        self.assertNotIn("renderMoverSourceLine(d.by_source, mix.", fn)

    # ── [053] 비교 단위는 기관이 아니라 수집 채널(레인)이다 ────────────────────
    #
    # ★왜 축을 또 바꿨나: 052 를 적용한 당일 회수 백필(+914)이 들어오자 식약처 점유율
    #   배율이 2.823 → 1.572 로 떨어져 게이트를 통과해 버렸고 유령이 그대로 남았다.
    #   임계값을 또 낮추는 것은 답이 아니었다 — 식약처를 한 덩어리로 세면 성격이 전혀
    #   다른 셋이 서로를 가린다(실측 증가율: 회수 1.22 · GMP실사 3.69 · 행정처분 67.0,
    #   합치면 2.45라 "정상"으로 보인다). 레인 축으로 낮추자 **같은 상한 2** 로 갈렸다.

    def test_migration_053_splits_only_channels_not_document_attributes(self):
        """레인은 **수집 채널**이다. 식약처는 채널이 셋이라 쪼개고, 경고서한은
        document_type 이 발신 부서(문서 속성)라 쪼개면 수십 종으로 갈라져 전부 표본
        미달로 떨어진다 — 그래서 식약처만 쪼갠다."""
        sql = self.sql053
        self.assertIn("create or replace function public.findings_recent_window(p_months integer",
                      sql)
        self.assertIn("case when x.source = 'MFDS' then x.source || '/' || x.document_type",
                      sql)
        self.assertIn("else x.source end", sql)
        self.assertIn("'lane',          ln,", sql)
        # 묶음키가 lane 이어야 식약처 3채널이 갈린다(source 로 묶으면 다시 합쳐진다).
        self.assertIn("from cur group by category_code, source, lane", sql)
        self.assertIn("and p2.lane          = c.lane", sql)
        # 041/052 안전 계약 승계 — 함수 본문 기준(헤더 주석은 계약을 설명하느라 이름을 적는다).
        body = sql[sql.index("as $$"):sql.index("$$;")]
        for leak in ("finding_text", "evidence_url", "raw_json"):
            self.assertNotIn(leak, body, f"안전 계약 위반: {leak}")
        self.assertIn("security definer", sql)
        # raw_signals 조인 없이 findings.document_type 으로 파생한다(테이블 재스캔 0).
        self.assertNotIn("raw_signals", body)

    def test_lane_totals_come_from_the_cross_tab_not_by_source(self):
        """★by_source 는 소스 축이라 식약처 3채널이 한 덩어리다 — 거기에 게이트를 걸면
        레인을 못 가른다. 교차표는 카테고리 전수를 담으므로 카테고리로 합치면 그 레인의
        창 합계가 정확히 나온다(라이브 검증: 접기 합계 2,989/1,914 = totals)."""
        align = self._fn("alignSourceMix")
        self.assertIn("laneTotals(grid).forEach", align)
        self.assertNotIn("(d.by_source || []).forEach", align,
                         "by_source 로 게이트를 걸면 식약처 3채널이 합쳐진다")
        totals = self._fn("laneTotals")
        self.assertIn("var k = r.lane || r.source;", totals)  # 053 미적용 폴백
        self.assertIn("e.cur += Number(r.cur_cnt) || 0;", totals)
        self.assertIn("e.prev += Number(r.prev_cnt) || 0;", totals)

    def test_dropped_lane_names_are_human_readable(self):
        """뺀 레인을 화면에 적을 때 내부 키(`MFDS/gmp-inspection`)를 그대로 노출하면
        안 된다 — 사람이 읽는 이름으로 바꾼다(트랙C 품질 기준: 내부 개념 미노출)."""
        self.assertIn('"MFDS/admin-action": _t("MFDS 행정처분"),', self.js_src)
        self.assertIn('"MFDS/gmp-inspection": _t("MFDS GMP 실사"),', self.js_src)
        self.assertIn('"MFDS/recall-quality": _t("MFDS 회수"),', self.js_src)
        align = self._fn("alignSourceMix")
        self.assertEqual(align.count("source: laneLabel(s.lane)"), 2,
                         "thin·skew 두 경로 모두 라벨을 거쳐야 한다")
        self.assertNotIn("source: s.lane", align, "내부 키가 그대로 화면에 나간다")

    def test_window_scope_shown_beside_the_ranking(self):
        """창(기간)과 분모는 순위 바로 옆에 항상 보여야 한다 — 어떤 기간 몇 건을 나눈
        비율인지 모르면 %가 아무 뜻이 없다. [컨셉 재정의] 월별 막대가 사라지면서 이
        정보의 자리가 막대 부제 → 순위 부제(tr-rank-sub)로 옮겼다."""
        fn = self.js_src[self.js_src.index("function applyAgency()"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("monthLabelKo(scope.cur_from)", fn)
        self.assertIn("monthLabelKo(scope.cur_to)", fn)
        self.assertIn("rankSubEl.textContent", fn)
        self.assertIn('id="tr-rank-sub"', self.html)

    def test_ranking_numbers_come_from_response_not_hardcoded(self):
        """순위·부제·각주의 숫자는 전부 응답에서 나와야 한다 — 소스에 리터럴로 박으면
        그 문장만 낡는다(이 저장소가 '47%가 2024년'으로 한 번 겪었다)."""
        fn = self.js_src[self.js_src.index("function applyAgency()"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("built.total", fn)
        self.assertIn("built.excluded", fn)
        self.assertIn("pctText(built.excluded, built.total)", fn)
        for literal in ("1,167", "1167", "4,557", "4557", "946", "36.7"):
            self.assertNotIn(literal, fn)


    def test_month_bars_removed_on_purpose(self):
        """[컨셉 재정의 2026-08-26] 월별 막대 24개를 **걷어냈다**.

        그 막대가 세는 것은 "그 달에 공개된 문서 수"이고, 그건 FDA·식약처의 공개 행정
        리듬이지 규제 신호가 아니다. 우리도 알고 있었다 — "마지막 막대는 아직 진행 중인
        달이라 낮게 보입니다"라는 주석이 그 사실을 인정하는 문장이었다. 보고 나서 할 일이
        없는 블록은 싣지 않는다(규율 3).
        ★이 테스트를 지우지 않고 남기는 이유: 그냥 지우면 다음 사람이 "월별 추이가
        없네?" 하고 되살린다. 판단이 있었다는 사실을 코드 곁에 남긴다."""
        for gone in ("renderRecentMonths", "buildMonthColumn", "tr-recent-months",
                     "tr-rm-bars"):
            self.assertNotIn(gone, self.js_src, f"월별 막대가 되살아났다: {gone}")
        self.assertNotIn('id="tr-recent-months"', self.html)
        self.assertNotIn(".tr-rm-", self.style_src)

    # ── 사례 드릴다운 ────────────────────────────────────────────────────────
    def test_example_text_prefers_korean_falls_back_to_english(self):
        """읽는 언어를 먼저, 없으면 반대편 — 빈칸으로 두지 않는다(부재 어휘 규칙).

        ★[다국어 3단계 2026-09-04] 폴백의 **방향이 언어에 따라 뒤집힌다** — 한국어판은
        국문 우선, 영어판은 규제기관 원문 우선(`_bodyText`, grm_i18n.JS_BODY_SHIM 정본).
        "빈칸으로 두지 않는다"는 계약은 그대로다(한쪽이 없으면 있는 쪽을 쓴다)."""
        fn = self._fn("buildExampleItem")
        self.assertIn("var body = _bodyText(f);", fn)
        self.assertIn('el("p", "tr-ex-text", truncateText(body))', fn)
        shim = grm_i18n.JS_BODY_SHIM
        self.assertIn("return _isEn ? (orig || ko) : (ko || orig);", shim)
        self.assertIn(shim, self.js_src, "본문 선택 사본이 없다")

    def test_example_panel_links_back_to_findings_search_with_cat_param(self):
        fn = self._fn("buildExamplePanel")
        self.assertIn('a.href = findingsHref("cat", code);', fn)
        # ★건수의 범위를 라벨에 적는다 — findings_search 가 세는 것은 전 기간 공개분인데
        # 이 패널이 달린 행은 최근 12개월 수치라(실측 309건 vs 2,976건) 범위를 빼면 두
        # 숫자가 어긋나 보인다. 링크가 가는 곳도 기간 필터 없는 검색이다.
        self.assertIn('_t("전체 기간 {ko} 지적 {n}건 보기 →", { ko: ko, n: fmtNum(total) })', fn)
        # 공개 게이트 밖이라 사례가 하나도 없을 수 있다 — 그 사실을 적는다.
        self.assertIn("이 영역은 아직 국문으로 열람할 수 있는 지적이 없습니다.", fn)

    def test_example_fetch_race_is_guarded(self):
        """사례는 비동기로 도착한다 — 그 사이 다른 행을 열었으면 늦게 온 응답을 버린다
        (엉뚱한 영역 아래 다른 영역의 사례가 붙는 결함 방지)."""
        fn = self.js_src[self.js_src.index("function openRecentCat(code, ko)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertEqual(fn.count("if (state.openCat !== code) return;"), 2)

    def test_category_rows_are_keyboard_operable(self):
        row = self._fn("buildRecentCatRow")
        self.assertIn("makeClickableRow(row,", row)
        self.assertIn("if (state.openCat === entry.code) closeRecentCat();", row)

    # ── 업체 찾기 ────────────────────────────────────────────────────────────




    # ── 공통 계약 재확인 ─────────────────────────────────────────────────────
    def test_no_innerhtml_data_injection_in_new_functions(self):
        for name in ("renderRecentCats", "fillMoverList", "applyAgency", "wireAgency"):
            fn = self._fn(name)
            for m in re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', fn):
                self.assertEqual(m.group(1).strip(), '""',
                                 f"{name}: innerHTML 데이터 삽입 의심 {m.group(0)}")

    def test_new_css_scoped_to_page_not_grm_css(self):
        # [존 재편] 스코프 CSS 는 세 면이 함께 include 하는 파셜로 옮겼다. 재는 것은
        # 그대로다 — 동결된 grm.css 에 들어가지 않았는가.
        for rule in (".tr-rc-row{", ".tr-mv-row{", ".tr-ex{",
                     ".tr-agency-btn{", ".tr-rank-sub{", ".tr-seg a{"):
            self.assertIn(rule, self.style_src)
        # 업체 찾기 CSS 는 firm/inspector 전용 파셜로 이사했다(그 폼이 그리로 갔다).
        lookup_src = (WEB_DIR / "partials" / "lookup_style.html").read_text(encoding="utf-8")
        self.assertIn(".tr-look-form{", lookup_src)
        css_path = WEB_DIR / "assets" / "grm.css"
        if css_path.is_file():
            css_src = css_path.read_text(encoding="utf-8")
            for cls in (".tr-rc-row", ".tr-mv-row", ".tr-ex",
                        ".tr-agency-btn", ".tr-look-form"):
                self.assertNotIn(cls, css_src)

    def test_no_chart_library_added(self):
        for forbidden in ("cdn.", "chart.js", "Chart.js", "d3.", "echarts", "<canvas"):
            self.assertNotIn(forbidden, self.html_src, forbidden)
            self.assertNotIn(forbidden, self.js_src, forbidden)

    def test_no_interpretive_or_prescriptive_language_in_new_sections(self):
        """관측된 분포만 보여 준다 — 권고/해석 문구는 렌더 결과 어디에도 없어야 한다
        (트랙C 품질 기준, WebFindingsZoneComparisonTest 와 동일 금지 목록)."""
        for forbidden in ("강화해야", "권고", "해야 합니다", "해야 한다",
                          "한국 공장은", "준비해야", "대비하십시오"):
            self.assertNotIn(forbidden, self.html, f"금지 문구 발견: {forbidden}")


# ── [인용 조항] 보일러플레이트 제외 조항 랭킹(042) ────────────────────────────
class WebTrendsCfrRankingTest(unittest.TestCase):
    """트렌드 대시보드 [많이 인용된 조항] 섹션 — 042_findings_cfr_ranking.sql.

    ★왜 이 섹션인가: 카테고리("무균보증/무균공정")는 우리가 붙인 분류라 그 자체로는
    자가점검 항목이 되지 못한다. 규제기관이 실제로 인용한 **조항**은 사내 SOP 와 1:1로
    붙는 단위라 "무엇을 확인해야 하는가"에 가장 가깝다.

    ★왜 필터가 전부인가: cfr_refs 를 그대로 세면 1위가 `21 CFR 211.34`(컨설턴트)가 된다 —
    FDA 가 모든 경고서한 맺음말에 붙이는 권고문이라 위반 인용이 아니다(라이브 실측 72/72
    = 100% 가 "consultant ... as set forth in" 형태). 여기선 그 제외가 실제로 걸려 있는지,
    **무엇을 뺐는지 화면이 밝히는지**, 범위 한계(사실상 WL 전용)가 응답에서 오는지를
    검증한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_trendscfr_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "trends" / "index.html").read_text(encoding="utf-8")
        # [존 재편] 존이 세 면으로 갈렸다. 각 테스트가 자기 주제가 실제로 사는 면을
        # 보게 한다 — 한 페이지 전제로 쓴 배치 단언들이 여기서 갈린다.
        cls.inspections = (cls.single / "findings" / "inspections" / "index.html").read_text(encoding="utf-8")
        cls.coverage = (cls.single / "findings" / "coverage" / "index.html").read_text(encoding="utf-8")
        # 스코프 CSS 는 세 면이 함께 include 하는 파셜로 옮겼다 — "grm.css 를 건드리지
        # 않았는가"를 재는 단언들은 이 파셜을 봐야 한다(검사 대상이 바뀐 것일 뿐,
        # 재는 것은 그대로다).
        cls.style_src = (WEB_DIR / "partials" / "trends_style.html").read_text(encoding="utf-8")
        cls.js_src = (WEB_DIR / "assets" / "trends.js").read_text(encoding="utf-8")
        cls.html_src = (WEB_DIR / "templates" / "trends.html").read_text(encoding="utf-8")
        cls.sql_src = (WEB_DIR / "migrations" / "042_findings_cfr_ranking.sql").read_text(
            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _fn(self, name):
        start = self.js_src.index("function " + name + "(")
        return self.js_src[start:self.js_src.index("\n  }", start)]

    # ── 셸 결정론 · 위치 ─────────────────────────────────────────────────────
    def test_shell_present_hidden(self):
        self.assertIn(
            '<section class="tr-block tr-cfr-block" id="tr-cfr-block" '
            'aria-label="많이 인용된 조항" hidden>',
            self.html,
        )
        for frag in ('<h2 class="tr-h">많이 인용된 조항</h2>',
                     '<p class="tr-cfr-sub" id="tr-cfr-sub"></p>',
                     '<div id="tr-cfr" class="tr-cfr"></div>',
                     '<p class="tr-note" id="tr-cfr-note"></p>'):
            self.assertIn(frag, self.html)

    def test_placed_last_as_the_actionable_step(self):
        """달라진 점(무엇이 변했나) → 조항(무엇을 확인하나) → 체크리스트(무엇을 할까).

        [존 재편 2026-08-26] 원래는 '업체 찾기'와 누적 구분선 사이였다. 그 둘이 이 면을
        떠났으므로(조회는 /findings/firm/, 누적은 순위의 한 보기) 조항 섹션이 이 면의
        **마지막 단계**가 됐다 — 읽기에서 산출물로 넘어가는 지점이라 CTA 가 여기 붙는다."""
        i_move = self.html.index('id="tr-move-block"')
        i_cfr = self.html.index('id="tr-cfr-block"')
        i_cta = self.html.index('class="tr-cta"')
        self.assertTrue(i_move < i_cfr < i_cta)
        self.assertNotIn('class="tr-divider"', self.html)
        self.assertNotIn('aria-label="업체 찾기"', self.html)

    def test_elements_defensively_queried_not_in_hard_gate(self):
        for elid in ("tr-cfr-block", "tr-cfr-sub", "tr-cfr", "tr-cfr-note"):
            self.assertIn(f'document.getElementById("{elid}")', self.js_src)
        gate = self.js_src[self.js_src.index("if (!cfg || !loadingEl"):]
        gate = gate[:gate.index("return;") + len("return;")]
        for forbidden in ("cfrBlockEl", "cfrEl", "cfrSubEl", "cfrNoteEl"):
            self.assertNotIn(forbidden, gate)

    # ── 보일러플레이트 제외(이 섹션의 존재 이유) ─────────────────────────────
    def test_sql_excludes_boilerplate_with_recorded_evidence(self):
        """제외는 **추측이 아니라 실측 근거**로만 한다 — 근거 문구가 마이그레이션에
        남아 있어야 나중에 사람이 검증·수정할 수 있다."""
        self.assertIn("excluded(bad_ref) as (", self.sql_src)
        self.assertIn("values ('21 CFR 211.34'), ('21 CFR 210.1(b)')", self.sql_src)
        self.assertIn("72/72(100%)", self.sql_src)
        self.assertIn("consultant is qualified as set forth in 21 CFR 211.34", self.sql_src)
        self.assertIn("neither adulterated nor", self.sql_src)

    def test_sql_part_filter_also_drops_bare_part_references(self):
        """`21 CFR 210`·`21 CFR parts 210 and 211` 같은 부 전체 참조는 조항이 아니라
        규정 이름이다 — 정규식이 점+숫자를 요구해 자동으로 빠진다."""
        self.assertIn(r"'^21 CFR 21[01]\.[0-9]'", self.sql_src)
        self.assertIn("부 전체 참조도 자동으로 배제", self.sql_src)

    def test_sql_alias_collision_trap_documented(self):
        """★실제로 밟은 함정: `excluded(ref)` + `where e.ref = ref` 는 안쪽 `e.ref` 로
        해석돼 조건이 항상 참이 되고 NOT EXISTS 가 전량을 걸러 냈다(적용 후
        docs_with_clause=0 으로 발각). 컬럼명 분리로 경로 자체를 없앴다 — 재발 방지를
        위해 근거가 파일에 남아야 한다(004 관례)."""
        self.assertIn("004 함정 실사례", self.sql_src)
        self.assertIn("lateral jsonb_array_elements_text(f.cfr_refs) as cf(ref_txt)",
                      self.sql_src)
        self.assertIn("e.bad_ref = cf.ref_txt", self.sql_src)

    def test_sql_counts_documents_not_findings_with_rationale(self):
        """cfr_refs 는 위반 블록 단위 추출이고 degrade 경로에서는 편지 전체 조항이 한
        finding 에 실린다 — 지적 문장 단위 배정은 신뢰 구간이 넓다. "이 조항을 인용한
        문서가 몇 건인가"는 그 잡음과 무관하게 참이라 문서 수로 센다."""
        self.assertIn("'unit',              'documents'", self.sql_src)
        self.assertIn("count(distinct p.raw_signal_id)", self.sql_src)
        self.assertIn("지적 문장 단위 배정은", self.sql_src)

    def test_sql_rolls_subsections_up_to_section_root(self):
        self.assertIn(r"regexp_replace(regexp_replace(cf.ref_txt, '^21 CFR ', ''), '\(.*$', '')",
                      self.sql_src)
        self.assertIn("'variants'", self.sql_src)   # 버린 정보를 되돌려준다

    def test_sql_safety_contract_counts_and_meta_only(self):
        for forbidden in ("finding_text", "evidence_url", "raw_json"):
            body = self.sql_src[self.sql_src.index("create or replace function"):]
            self.assertNotIn("'" + forbidden + "'", body)

    # ── 무엇을 뺐는지 화면이 밝히는가 ────────────────────────────────────────
    def test_exclusions_and_scope_limits_disclosed_from_response(self):
        """무엇을 세지 않았는지 밝히지 않으면 이 순위는 검증 불가능한 주장이 된다.
        제외 목록·부 필터·소스 구성은 전부 응답(scope)에서 읽는다(하드코딩 금지)."""
        fn = self.js_src[self.js_src.index("function renderCfrRanking(data)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("scope.excluded_sections", fn)
        self.assertIn("scope.part_filter", fn)
        self.assertIn("scope.docs_with_clause", fn)
        self.assertIn("scope.sources", fn)
        self.assertIn("이 순위는 사실상 Warning Letter 기준입니다.", fn)
        self.assertIn("위반 인용이 아니라 뺐습니다.", fn)
        self.assertIn("조항 단위로 합쳤습니다.", fn)
        self.assertIn("cfrNoteEl.textContent = note;", fn)
        # 실측 수치가 리터럴로 박혀 있으면 안 된다.
        for literal in ("477", "255", "211.22\"", "211.34\""):
            self.assertNotIn(literal, fn)

    # ── 조항 → 조문 원문 · 실제 사례 (이 섹션이 "그래서 뭘 하냐"에 답하는 지점) ──
    def test_row_links_to_ecfr_source_text(self):
        self.assertIn("function ecfrHref(section)", self.js_src)
        fn = self._fn("ecfrHref")
        self.assertIn('"https://www.ecfr.gov/current/title-21/section-" + encodeURIComponent(section)',
                      fn)
        link = self._fn("buildCfrLinkLine")
        self.assertIn("a.href = ecfrHref(item.section);", link)
        self.assertIn('a.target = "_blank";', link)
        self.assertIn('a.rel = "noopener";', link)
        self.assertIn("조문 원문 보기(eCFR) →", link)
        # 조항 뿌리로 합치면서 버린 하위 항 정보를 여기서 되돌려준다.
        self.assertIn('_t("실제 인용된 항: {list}", { list: variants.join(" · ") })', link)

    def test_examples_filtered_by_actual_cfr_refs_not_blob_match(self):
        """검색은 blob ILIKE 라 본문에 번호만 스친 행도 걸린다 — 실제로 그 조항이
        인용된(cfr_refs 에 있는) 지적만 남긴다."""
        fn = self._fn("buildCfrExamplePanel")
        self.assertIn("var refs = f.cfr_refs || [];", fn)
        self.assertIn('if (String(refs[i]).indexOf(item.section) >= 0)', fn)
        self.assertIn("이 조항으로 지적된 문장 중 국문으로 열람할 수 있는 것이 아직 없습니다.",
                      fn)

    def test_example_fetch_uses_search_rpc_with_section_query(self):
        fn = self._fn("fetchCfrExamples")
        self.assertIn('rpcEndpoint("findings_search")', fn)
        self.assertIn("p_q: section", fn)
        # 필터 후 3건을 확보하려면 문서를 넉넉히 받아야 한다.
        self.assertIn("p_docs_per_page: EXAMPLE_ROWS * 2", fn)

    def test_example_race_guarded(self):
        fn = self.js_src[self.js_src.index("function openCfr(section)"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertEqual(fn.count("if (state.openCfr !== section) return;"), 2)

    def test_recent_docs_shown_beside_cumulative(self):
        """누적만 보면 "예전에 많이 걸렸던 조항"과 "지금도 걸리는 조항"이 구분되지 않는다."""
        fn = self._fn("buildCfrRow")
        self.assertIn('_t("문서 {n}건", { n: fmtNum(item.docs) })', fn)
        self.assertIn('_t("최근 12개월 {n}건", { n: fmtNum(item.recent_docs) })', fn)

    # ── 조항 국문 요지 ───────────────────────────────────────────────────────
    def test_section_labels_cover_observed_sections_and_fall_back(self):
        """조항 번호만으로는 전 직원이 읽을 수 없어 요지를 한 줄 붙인다. 매핑에 없는
        조항은 번호만 표시한다(추측 번역 금지 — countryLabelKo 와 동일 폴백 계약)."""
        m = re.search(r"var CFR_SECTION_LABELS = \{(.*?)\n  \};", self.js_src, re.S)
        self.assertIsNotNone(m, "CFR_SECTION_LABELS 정의 미발견")
        # [i18n 2단계] 값은 `_t("…")` 로 감싸질 수 있다 — 선택적 래퍼를 허용한다.
        pairs = dict(re.findall(r'"([0-9.]+)":\s*(?:_t\()?"([^"]+)"\)?', m.group(1)))
        # 라이브 실측 상위 조항은 반드시 요지를 갖는다.
        for sec, cue in (("211.22", "품질관리부서"), ("211.84", "원자재"),
                         ("211.100", "생산·공정관리"), ("211.192", "일탈"),
                         ("211.165", "완제품"), ("211.166", "안정성"),
                         ("211.113", "무균"), ("211.67", "세척")):
            self.assertIn(sec, pairs, f"{sec} 요지 누락")
            self.assertIn(cue, pairs[sec], f"{sec} 요지 내용 불일치: {pairs[sec]}")
        # 제외된 보일러플레이트 조항은 애초에 표시 대상이 아니다.
        self.assertNotIn("211.34", pairs)
        self.assertIn("function cfrSectionLabel(section)", self.js_src)
        self.assertIn('CFR_SECTION_LABELS[section] || ""', self._fn("cfrSectionLabel"))

    # ── 공통 계약 ────────────────────────────────────────────────────────────
    def test_independent_fetch_chain_silent_fallback(self):
        # ★앵커는 함수 **정의**가 아니라 호출 분기여야 한다 — 존이 세 면으로
        #   갈리면서 각 부 데이터 fetch 가 `if (WANT.x) {` 안으로 들어갔고,
        #   함수명만으로 자르면 정의부가 먼저 잡혀 체인을 못 본다.
        chain = self.js_src[self.js_src.index("if (WANT.cfr) {"):]
        self.assertIn("renderCfrRanking(data)", chain[:220])
        self.assertNotIn("errorEl.hidden", chain[:300])
        self.assertIn("조용히 숨김 유지", chain[:300])

    def test_no_innerhtml_data_injection(self):
        for name in ("renderCfrRows", "renderCfrRanking"):
            fn = self._fn(name)
            for m in re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', fn):
                self.assertEqual(m.group(1).strip(), '""', f"{name}: {m.group(0)}")

    def test_css_scoped_to_page_not_grm_css(self):
        for rule in (".tr-cf-row{", ".tr-cf-sec{", ".tr-cf-links{"):
            self.assertIn(rule, self.style_src)
        css_path = WEB_DIR / "assets" / "grm.css"
        if css_path.is_file():
            self.assertNotIn(".tr-cf-row", css_path.read_text(encoding="utf-8"))

    def test_no_prescriptive_language(self):
        for forbidden in ("강화해야", "권고합니다", "해야 합니다", "해야 한다",
                          "준비해야", "대비하십시오"):
            self.assertNotIn(forbidden, self.html, f"금지 문구: {forbidden}")


# ── [자가점검 체크리스트] /findings/checklist/ (042 순위 + 043 사례) ──────────
class WebChecklistRenderTest(unittest.TestCase):
    """트렌드의 조항 순위까지는 "읽는 자료"다. 자가점검은 회의실에 종이로 들고 가거나
    엑셀에 붙여 넣어 채우는 **산출물**이라 화면 구성이 다르다 — 설정은 화면에만, 본문은
    인쇄에도, 판정·근거란은 비어 있어야 한다.

    여기선 셸 결정론·RPC 배선(2회 왕복)·층 분리(순위 정본은 042 하나)·내보내기 3종
    (인쇄/TSV 복사/CSV)·공개 게이트 차이 고지를 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_checklist_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "checklist" / "index.html").read_text(encoding="utf-8")
        cls.trends = (cls.single / "findings" / "trends" / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")
        cls.js_src = (WEB_DIR / "assets" / "checklist.js").read_text(encoding="utf-8")
        cls.html_src = (WEB_DIR / "templates" / "checklist.html").read_text(encoding="utf-8")
        cls.sql_src = (WEB_DIR / "migrations" / "043_findings_checklist.sql").read_text(
            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _fn(self, name):
        start = self.js_src.index("function " + name + "(")
        return self.js_src[start:self.js_src.index("\n  }", start)]

    # ── 페이지 생성·배선 ─────────────────────────────────────────────────────
    def test_page_generated_with_canonical_and_description(self):
        self.assertIn("자가점검 체크리스트", self.html)
        self.assertIn("Self-Inspection", self.html)
        self.assertIn(
            f'<link rel="canonical" href="{render.SITE_BASE_URL}/findings/checklist/" />',
            self.html)
        self.assertIn('<meta name="description" content="', self.html)

    def test_sitemap_includes_checklist(self):
        self.assertIn(f"<loc>{render.SITE_BASE_URL}/findings/checklist/</loc>", self.sitemap)

    def test_not_added_to_nav_but_reachable_from_three_places(self):
        """nav 과밀 금지(6탭 고정)는 그대로 — 그러나 **진입로가 하나면 없는 것과 같다.**

        [존 재편 2026-08-26] 이 테스트는 원래 "진입은 트렌드의 조항 섹션 링크 하나뿐"을
        지켰다. 그 계약이 실제로 만든 결과는 이랬다: 인쇄·CSV·엑셀 붙여넣기까지 되는
        완성된 산출물인데 **홈에서 링크로 닿는 경로가 사이트 전체에서 1개**였다(도달성
        감사 실측 — 다른 nav 라우트는 전부 3,500장 이상). nav 탭을 늘리지 않는다는 원칙은
        유지하되, footer 도구 열 · 찾아보기 도구 카드 · 트렌드 조항 섹션 CTA 셋으로
        진입로를 연다. 지키는 대상이 'nav 6탭'에서 '닿을 수 있는가'로 바뀐 것이다."""
        # nav 탭은 6개 그대로 — 체크리스트가 nav 에 들어가지 않았는지.
        nav = self.trends[self.trends.index('<nav id="navmenu">'):]
        nav = nav[:nav.index("</nav>")]
        self.assertNotIn("checklist", nav, "체크리스트가 nav 탭으로 승격됐다(과밀 금지)")
        # 진입로 셋.
        self.assertIn('<a class="tr-cta-go" href="../../findings/checklist/index.html">',
                      self.trends)
        self.assertIn('href="../../findings/checklist/index.html">자가점검 체크리스트</a>',
                      self.trends)          # footer 도구 열
        findings = (self.single / "findings" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="../findings/checklist/index.html"', findings)  # 도구 카드

    def test_js_referenced_with_content_hash_and_copied_verbatim(self):
        import re as _re
        self.assertIsNotNone(
            _re.search(r'assets/checklist\.js\?v=([0-9a-f]{8})"', self.html),
            "checklist.js 캐시버스팅 해시 미발견")
        built = (self.single / "assets" / "checklist.js").read_bytes()
        self.assertEqual(built, (WEB_DIR / "assets" / "checklist.js").read_bytes())

    def test_shell_deterministic_env_gated(self):
        """env 미설정(테스트)에서도 템플릿 출력 byte 는 항상 동일 — cfg 는 빈 문자열."""
        self.assertIn(
            'id="grm-findings-cfg" data-url="" data-key="" data-root="../../" hidden',
            self.html)
        self.assertIn('<div class="cl-state" id="cl-loading"', self.html)
        self.assertIn('<div class="cl-state cl-state-error" id="cl-error" hidden>', self.html)
        self.assertIn('<div id="cl-doc" hidden>', self.html)
        self.assertIn('<div class="cl-ctl-row cl-ctl-export" id="cl-export" hidden>', self.html)

    def test_env_missing_ends_quietly_not_as_error(self):
        self.assertIn('loadingEl.textContent = _t("체크리스트 서비스 준비 중입니다.");', self.js_src)

    # ── 층 분리: 순위 정본은 042 하나 ────────────────────────────────────────
    def test_two_round_trips_only_ranking_then_examples(self):
        """조항마다 검색을 돌리면 N+1 왕복이 된다 — 042 로 순위를 받고 그 섹션 목록을
        043 에 한 번에 넘긴다."""
        fn = self.js_src[self.js_src.index("function build()"):]
        self.assertIn('rpc("findings_cfr_ranking", { p_months: 12 })', fn)
        self.assertIn('rpc("findings_checklist", { p_sections: sections, p_examples: examples })',
                      fn)
        # fetch 호출부는 공용 rpc() 한 곳뿐 — 조항마다 따로 fetch 하는 경로가 없어야 한다.
        self.assertEqual(self.js_src.count("fetch("), 1)
        self.assertEqual(self.js_src.count('rpc("'), 2)

    def test_client_does_not_reimplement_ranking_filters(self):
        """부 필터·보일러플레이트 제외·문서 수 집계는 042 의 몫이다. 클라이언트가 복제하면
        트렌드 페이지와 체크리스트가 서로 다른 순위를 말하게 된다."""
        # 보일러플레이트 조항 번호가 클라이언트에 박히면 042 와 갈라진다(제외 목록은
        # 응답 scope.excluded_sections 를 그대로 표시만 한다).
        for forbidden in ("211.34", "210.1(b)", "cfr_refs", "jsonb"):
            self.assertNotIn(forbidden, self.js_src,
                             f"클라이언트가 순위 필터를 복제하고 있다: {forbidden}")
        self.assertIn("state.meta.excluded", self.js_src)   # 표시만 한다
        # 클라이언트가 하는 일은 정렬 키 선택뿐이다.
        self.assertIn('var sortKey = sortEl && sortEl.value === "recent" ? "recent_docs" : "docs";',
                      self.js_src)

    def test_sort_label_matches_the_key_actually_used(self):
        """★실제 결함이었다: sortKey 는 응답 필드명("recent_docs")인데 라벨 분기가 셀렉트
        값("recent")과 비교해 항상 거짓이 됐다 — 최근순으로 정렬한 표에도 인쇄물 머리에
        "전체 누적 인용순"이 찍혔다(인쇄물이 자기 기준을 잘못 말하는 결함)."""
        self.assertIn(
            'sortLabel: sortKey === "recent_docs" ? _t("최근 12개월 인용순") : _t("전체 누적 인용순"),',
            self.js_src)
        self.assertNotIn('sortKey === "recent" ?', self.js_src)

    def test_sql_is_invoker_so_rls_gates_the_text(self):
        """사례는 원문 문장이다 — definer 로 내보내면 미번역·비공개 행까지 샌다.
        invoker 라 RLS(010)가 게이트를 강제한다(026 과 동일 이유, 042 와는 반대)."""
        self.assertIn("security invoker", self.sql_src)
        self.assertIn("findings 의 RLS(010 정책", self.sql_src)
        self.assertNotIn("security definer", self.sql_src)

    def test_sql_section_match_does_not_swallow_neighbours(self):
        """접두 매치면 211.22 질의가 211.25·211.28 을 삼킨다 — 정확일치 또는 하위 항
        괄호까지만 허용한다."""
        self.assertIn("cr.ref_txt = '21 CFR ' || sec.section", self.sql_src)
        self.assertIn("cr.ref_txt like '21 CFR ' || sec.section || '(%'", self.sql_src)
        self.assertIn("삼킨다", self.sql_src)

    def test_sql_input_is_validated_and_capped(self):
        """클라이언트를 신뢰하지 않는다 — 조항 형식만 통과, 배열은 009 관례로 슬라이스."""
        self.assertIn(r"s.section ~ '^21[01]\.[0-9]+$'", self.sql_src)
        self.assertIn("(coalesce(p_sections, '{}'::text[]))[1:50]", self.sql_src)
        self.assertIn("least(greatest(coalesce(p_examples, 2), 1), 5)", self.sql_src)

    def test_sql_dedupes_by_firm_and_prefers_anchored_examples(self):
        """같은 업체 사례 2건이면 "여러 곳에서 반복되는 지적"이라는 전제가 깨진다.
        또 위반 블록 하나가 여러 조항을 인용하면 문장에 그 조항 번호가 없을 수 있어,
        번호가 실제로 적힌 사례를 앞세운다(실측 결함이었다)."""
        self.assertIn("partition by sec.section, f.firm_key", self.sql_src)
        self.assertIn("as anchored", self.sql_src)
        self.assertIn("order by c.anchored, c.published_date desc, c.finding_id", self.sql_src)
        self.assertIn("21 CFR 211.22(a)를 인용", self.sql_src)   # 근거 실측 기록

    # ── 산출물로서의 요건 ────────────────────────────────────────────────────
    def test_verdict_and_note_fields_are_blank_for_humans(self):
        """판정·근거는 사람이 채우는 칸이다 — 서버가 값을 지어내지 않는다."""
        fn = self._fn("buildVerdictBox")
        self.assertIn('[_t("적합"), _t("부적합"), _t("해당없음")]', fn)
        self.assertIn('input.type = "radio";', fn)
        self.assertIn('input.name = "cl-v-" + idx;', fn)   # 항목별 배타 선택
        item = self._fn("buildItem")
        self.assertIn('el("span", "", _t("확인 결과 · 근거 문서"))', item)
        rows = self._fn("exportRows")
        self.assertIn('line.push("", "");', rows)          # 내보내기에서도 빈 칸

    def test_print_css_strips_screen_chrome_and_avoids_item_breaks(self):
        """항목이 페이지 경계에서 잘리면 점검표로 못 쓴다."""
        self.assertIn("@media print{", self.html_src)
        for sel in (".nav", ".site", ".grm-pet", ".cl-screen-only"):
            self.assertIn(sel, self.html_src)
        self.assertIn("break-inside:avoid;page-break-inside:avoid", self.html_src)

    def test_export_three_ways_share_one_table(self):
        """인쇄·TSV 복사·CSV 는 같은 표를 세 경로로 낼 뿐이다(열 구성 분기 금지)."""
        self.assertIn("function exportRows()", self.js_src)
        self.assertIn("toTsv(exportRows())", self.js_src)
        self.assertIn("toCsv(exportRows())", self.js_src)
        self.assertIn("window.print()", self.js_src)

    def test_csv_has_bom_for_excel_korean(self):
        """BOM 이 없으면 엑셀이 UTF-8 을 자동 인식하지 못해 한글이 깨진다."""
        self.assertIn('new Blob(["﻿" + toCsv(exportRows())]', self.js_src)
        self.assertIn('type: "text/csv;charset=utf-8;"', self.js_src)
        self.assertIn("a.download =", self.js_src)

    def test_tsv_cells_strip_tabs_and_newlines(self):
        """탭·개행은 셀 경계라 그대로 두면 엑셀 붙여넣기가 어긋난다."""
        fn = self._fn("tsvCell")
        self.assertIn(r'replace(/[\t\r\n]+/g, " ")', fn)
        csv = self._fn("csvCell")
        self.assertIn("""replace(/"/g, '""')""", csv)
        self.assertIn(r'replace(/\r?\n/g, " ")', csv)

    def test_copy_failure_is_explained_not_silent(self):
        fn = self._fn("copyTable")
        self.assertIn("navigator.clipboard", fn)
        self.assertIn("CSV 내려받기를 이용해 주세요.", fn)

    # ── 정직성: 무엇이 빠졌는지 문서에 남는다 ────────────────────────────────
    def test_document_footer_discloses_scope_and_gate_difference(self):
        """사례는 국문 번역이 끝난 지적만 나오므로(043 invoker+RLS) 인용 문서 수보다
        적을 수 있다 — 인쇄물에 그 사실이 남아야 한다. 순위 범위 한계도 마찬가지."""
        fn = self.js_src[self.js_src.index("function renderDoc()"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("사실상 Warning Letter 기준입니다.", fn)
        self.assertIn("위반 인용이 아니라 제외했습니다.", fn)
        self.assertIn("인용 문서 수보다 적을 수 있습니다.", fn)
        self.assertIn("자료가 공개된 날입니다.", fn)
        self.assertIn("state.meta.excluded", fn)   # 제외 목록은 응답에서(하드코딩 금지)
        self.assertIn("state.meta.partFilter", fn)

    def test_missing_examples_state_is_explicit(self):
        item = self._fn("buildItem")
        self.assertIn("국문으로 열람할 수 있는 사례가 아직 없습니다.", item)

    def test_loose_example_is_flagged(self):
        """anchored=false 면 같은 위반 블록이 여러 조항을 함께 인용한 경우다 — 문장에
        그 조항 번호가 없다는 사실을 적어 오해를 막는다."""
        item = self._fn("buildItem")
        self.assertIn("if (f.anchored === false)", item)
        self.assertIn("같은 지적에 여러 조항이 함께 인용됨", item)

    # ── 공통 계약 ────────────────────────────────────────────────────────────
    def test_no_innerhtml_data_injection(self):
        for m in re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', self.js_src):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입: {m.group(0)}")

    def test_no_external_resources_or_chart_libs(self):
        for forbidden in ("cdn.", "chart.js", "d3.", "echarts", "<canvas",
                          '<script src="http'):
            self.assertNotIn(forbidden, self.html_src, forbidden)
            self.assertNotIn(forbidden, self.js_src, forbidden)
        self.assertEqual(self.html_src.count("<script"), 1)

    def test_css_scoped_to_page_not_grm_css(self):
        for rule in (".cl-item{", ".cl-verdict{", ".cl-controls{"):
            self.assertIn(rule, self.html_src)
        css_path = WEB_DIR / "assets" / "grm.css"
        if css_path.is_file():
            self.assertNotIn(".cl-item", css_path.read_text(encoding="utf-8"))

    def test_no_prescriptive_language(self):
        for forbidden in ("강화해야", "권고합니다", "해야 합니다", "해야 한다", "준비해야"):
            self.assertNotIn(forbidden, self.html, f"금지 문구: {forbidden}")


# ── CFR_SECTION_LABELS 하드코딩 사본 전수 동기화 ──────────────────────────────
class WebCfrSectionLabelsSyncTest(unittest.TestCase):
    """trends.js 와 checklist.js 가 CFR_SECTION_LABELS 를 각자 하드코딩 복제한다(이 저장소의
    확립된 방식 — CATEGORY_LABELS 와 동일). 수동 파일 목록은 새 복제본이 생기면 낡아
    침묵 통과한다(PR#351·#366 이 정확히 그 실패였다) — web/assets/*.js 를 글롭으로 훑어
    선언 파일을 **전부 자동 발견**해 서로 대조하고, 0건이면 선언 형식이 깨진 것으로 본다."""

    # [i18n 2단계] 값은 `_t("…")` 로 감싸질 수 있다 — 선택적 래퍼를 허용한다.
    _PAT = re.compile(r'"([0-9.]+)":\s*(?:_t\()?"((?:[^"\\]|\\.)*)"\)?')

    def _copies(self):
        out = {}
        for p in sorted((WEB_DIR / "assets").glob("*.js")):
            src = p.read_text(encoding="utf-8")
            if "var CFR_SECTION_LABELS = {" not in src:
                continue
            m = re.search(r"var CFR_SECTION_LABELS = \{(.*?)\n  \};", src, re.S)
            self.assertIsNotNone(m, f"{p.name}: CFR_SECTION_LABELS 블록 파싱 실패")
            out[p.name] = dict(self._PAT.findall(m.group(1)))
        return out

    def test_all_copies_identical_and_discovered(self):
        copies = self._copies()
        self.assertGreaterEqual(
            len(copies), 2,
            "CFR_SECTION_LABELS 선언 파일을 2개 미만 발견 — 글롭/선언 형식이 깨졌다")
        names = sorted(copies)
        base = copies[names[0]]
        self.assertGreater(len(base), 30, "조항 요지 표가 비정상적으로 작다")
        for name in names[1:]:
            self.assertEqual(
                copies[name], base,
                f"{name} 의 CFR_SECTION_LABELS 가 {names[0]} 과 다르다(드리프트)")

    def test_sections_are_part_210_or_211_only(self):
        """042 가 21 CFR 210/211 만 세므로 요지 표에 다른 부의 조항이 있으면 드리프트다."""
        for name, pairs in self._copies().items():
            for sec in pairs:
                self.assertRegex(sec, r"^21[01]\.[0-9]+$", f"{name}: 범위 밖 조항 {sec}")


# ── CATEGORY_LABELS 하드코딩 사본 전수 동기화 ────────────────────────────────
class WebCountryLabelsSyncTest(unittest.TestCase):
    """★[2026-08-12] `COUNTRY_LABELS_KO`(ISO2 → 한국어 국가명)도 findings.js·trends.js 가
    각자 하드코딩 복제한다(CFR_SECTION_LABELS·CATEGORY_LABELS 와 같은 관례). 그런데 그
    둘과 달리 **이 상수만 전수 글롭 가드를 못 받았고**, 검사하던 테스트는 trends.js 한
    파일만 로드하고 있었다 — 그 결과 실제로 어긋났다:

        canon(`grm_findings._COUNTRY_CODE_MAP` 고유 코드) 68
        trends.js   68  (동기)
        findings.js 47  ← 21개 누락(AE·AR·AW·BG·BR·CO·CR·DO·EE·EG·HK·HR·LV·MO·MT·
                          NZ·OM·PH·SG·TH·UY)

    라이브 국가 축이 마침 41종이라 **지금 화면에 보이는 것은 전부 커버돼 잠재 상태**였지만,
    매핑에 없는 코드는 코드 자체를 그대로 노출하는 계약이라(추측 번역 금지) 새 국가가
    한 건이라도 들어오는 순간 `/findings/` 필터에 `SG`·`TH` 같은 원시 코드가 뜬다.
    057(FDA DDAPI 국가명 27종 정규화)로 새 코드 유입 가능성이 막 커진 참이었다.

    형제 가드와 동일하게 **web/assets/*.js 를 글롭으로 훑어 선언 파일을 전부 자동 발견**
    하고, 0건이면 선언 형식이 깨진 것으로 본다(수동 파일 목록 금지)."""

    # [i18n 2단계] 값은 `_t("…")` 로 감싸질 수 있다 — 선택적 래퍼를 허용해 파싱 결과는
    # 감싸기 전과 같게 유지한다.
    _PAT = re.compile(r'([A-Z]{2}):\s*(?:_t\()?"((?:[^"\\]|\\.)*)"\)?')

    def _copies(self):
        out = {}
        for p in sorted((WEB_DIR / "assets").glob("*.js")):
            src = p.read_text(encoding="utf-8")
            if "var COUNTRY_LABELS_KO = {" not in src:
                continue
            m = re.search(r"var COUNTRY_LABELS_KO = \{(.*?)\n  \};", src, re.S)
            self.assertIsNotNone(m, f"{p.name}: COUNTRY_LABELS_KO 블록 파싱 실패")
            out[p.name] = dict(self._PAT.findall(m.group(1)))
        return out

    def test_all_copies_discovered_and_identical(self):
        copies = self._copies()
        self.assertGreaterEqual(
            len(copies), 2,
            "COUNTRY_LABELS_KO 선언 파일을 2개 미만 발견 — 글롭/선언 형식이 깨졌다")
        names = sorted(copies)
        base = copies[names[0]]
        for name in names[1:]:
            self.assertEqual(copies[name], base,
                             f"{name} 의 COUNTRY_LABELS_KO 가 {names[0]} 과 다르다(드리프트)")

    def test_copies_cover_canonical_country_codes(self):
        """정본은 `grm_findings._COUNTRY_CODE_MAP`(055/057 의 grm_normalize_country 와 짝).
        코드가 여러 국가명 변형에 걸리므로 **값(코드) 집합**이 비교 대상이다."""
        import grm_findings
        canon = set(grm_findings._COUNTRY_CODE_MAP.values())
        self.assertGreater(len(canon), 40, "정본 코드 집합이 비정상적으로 작다")
        for name, m in self._copies().items():
            self.assertEqual(canon - set(m), set(),
                             f"{name}: 정본에 있는데 라벨이 없는 코드 — 화면에 원시 ISO2 가 뜬다")
            self.assertEqual(set(m) - canon, set(), f"{name}: 정본에 없는 유령 코드")
            for code, label in m.items():
                self.assertTrue(label.strip(), f"{name}: {code} 라벨이 비었다")


class WebCategoryLabelsSyncTest(unittest.TestCase):
    """findings.js/trends.js/firm.js/inspector.js 는 각자 CATEGORY_LABELS 를 독립
    하드코딩 사본으로 복제한다(이 저장소의 확립된 방식 — 공유 파일로 빼지 않는다). 예전엔
    파일마다 이름을 하드코딩한 개별 동기화 테스트가 있었는데(findings.js/trends.js/firm.js
    각각 test_category_labels_sync_with_taxonomy), 그 방식은 새 복제 파일이 추가될 때마다
    사람이 새 테스트를 빠짐없이 적어야 한다 — 이 저장소는 정확히 이 실패를 이미 두 번
    겪었다(수동 허용목록이 낡아 웹 테스트 67건이 침묵 미실행 PR#351, 루트 모듈 36/58 이
    게이트를 우회 PR#366). 두 번 다 **전수 자동 열거 + 0건 가드**로 근원 수리했다 — 여기도
    같은 원칙: web/assets/*.js 를 글롭으로 훑어 'var CATEGORY_LABELS = {' 를 선언한 파일을
    전부 자동 발견해 각각 grm_findings.FINDING_TAXONOMY 와 대조한다(개별 메서드 0개)."""

    # [i18n 2단계] ko 값은 `_t("…")` 로 감싸질 수 있다 — 감싸든 안 감싸든 파싱 결과(파싱된
    # dict)는 같아야 하므로 선택적 `_t(` … `)` 래퍼를 허용한다.
    _ENTRY_PAT = re.compile(
        r'(\w+):\s*\{\s*ko:\s*(?:_t\()?"((?:[^"\\]|\\.)*)"\)?,\s*en:\s*"((?:[^"\\]|\\.)*)"\s*\}'
    )

    def test_all_category_labels_copies_match_taxonomy(self):
        js_files = sorted(
            p for p in (WEB_DIR / "assets").glob("*.js")
            if "var CATEGORY_LABELS = {" in p.read_text(encoding="utf-8")
        )
        # 글롭이 조용히 아무것도 못 찾는 회귀 방지 — 0건이면 선언 형식/경로 자체가 깨진 것.
        self.assertGreater(
            len(js_files), 0,
            "CATEGORY_LABELS 를 선언한 web/assets/*.js 파일을 하나도 찾지 못함(글롭·형식 확인)",
        )
        # 현재 알려진 복제 파일 4개(findings/trends/firm/inspector) 미만이면 발견 로직
        # 자체가 무언가를 놓친 것으로 간주한다(신규 파일 추가 시 이 하한을 올린다).
        self.assertGreaterEqual(
            len(js_files), 4,
            f"CATEGORY_LABELS 복제본이 4개 미만 발견됨({[p.name for p in js_files]}) — "
            "findings.js/trends.js/firm.js/inspector.js 는 최소 존재해야 한다",
        )

        expected = {c.code: (c.label_ko, c.label_en) for c in grm_findings.FINDING_TAXONOMY}
        self.assertEqual(len(expected), 20, "FINDING_TAXONOMY 카테고리 수가 20이 아님(전제 재확인 필요)")

        for path in js_files:
            with self.subTest(file=path.name):
                js_src = path.read_text(encoding="utf-8")
                m = re.search(r"var CATEGORY_LABELS = \{(.*?)\n  \};", js_src, re.S)
                self.assertIsNotNone(
                    m, f"{path.name} 에 CATEGORY_LABELS 정의 미발견(중괄호 형식 확인)")
                found = {code: (ko, en) for code, ko, en in self._ENTRY_PAT.findall(m.group(1))}
                self.assertEqual(
                    found, expected,
                    f"{path.name} CATEGORY_LABELS != grm_findings.FINDING_TAXONOMY")


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

    # ── [존 재편 2026-08-26] 이름으로 조회하는 랜딩 ──────────────────────────
    # 이 폼은 원래 트렌드 페이지 일곱 번째 블록에 있었다(WebTrendsRecentWindowTest 소관).
    # 통계 페이지 한가운데 있던 조회 도구를 **조회 대상 페이지 자체**의 랜딩으로 올렸다 —
    # 그래야 이 URL 을 그냥 링크하는 것만으로 진입로가 생긴다(재편 전 정적 인바운드 0개).
    # 아래 단언들은 그때 쓰던 것을 그대로 옮겨 온 것이다(커버리지를 잃지 않는다).
    def _fn(self, name):
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        start = src.index("function " + name + "(")
        return src[start:src.index("\n  }", start)]

    def test_lookup_form_shell_present_without_action(self):
        """form 에 action 이 없어야 041 미배포 라이브에서 눌러도 페이지 이동이 없다
        (submit 은 firm.js 가 preventDefault 로 가로챈다)."""
        self.assertIn('<form class="tr-look-form" id="fp-look-form" role="search">', self.html)
        self.assertIn('<input class="tr-look-input" id="fp-look-input" type="search"', self.html)
        self.assertIn('<div id="fp-look-res" class="tr-look-res" aria-live="polite"></div>',
                      self.html)
        form = self.html[self.html.index('id="fp-look-form"'):]
        form = form[:form.index("</form>")]
        self.assertNotIn("action=", form)
        # 라벨은 시각적으로 숨기되 마크업에는 남긴다(스크린리더).
        self.assertIn('<label class="sr-only" for="fp-look-input">업체명</label>', self.html)

    def test_lookup_submit_intercepted(self):
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        self.assertIn('lookFormEl.addEventListener("submit", function (ev) {', src)
        self.assertIn("ev.preventDefault();", src)

    def test_firm_search_rpc_wired(self):
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        self.assertIn('rpcEndpoint("findings_firm_search")', src)
        self.assertIn("JSON.stringify({ p_q: q, p_limit: 20 })", src)

    def test_lookup_empty_result_is_explained(self):
        fn = self._fn("renderLookupResult")
        self.assertIn("영문 상호의 일부만 넣어 보세요.", fn)
        self.assertIn("이름을 누르면 그 업체의 이력으로 갑니다.", fn)

    def test_lookup_row_links_to_same_page_with_key(self):
        """결과 행은 같은 페이지의 ?key= 조회로 간다 — 랜딩과 프로파일이 한 URL 이므로
        형제 디렉터리 경로 계산이 아예 필요 없어졌다."""
        fn = self._fn("buildLookupRow")
        self.assertIn('a.href = "?key=" + encodeURIComponent(item.firm_key || "");', fn)
        self.assertIn("decodeFirmDisplay(item.firm_name)", fn)

    def test_lookup_short_query_guarded_client_side_too(self):
        """서버(041)도 2자 미만을 빈 결과로 막지만, 클라이언트도 왕복 자체를 하지 않는다."""
        fn = self._fn("runLookup")
        self.assertIn("if (q.length < 2) {", fn)
        self.assertIn("두 글자 이상 입력해 주세요.", fn)

    def test_keyless_visit_shows_lookup_not_dead_end(self):
        """?key= 없이 들어오면 '찾을 수 없음'이 아니라 조회 랜딩이어야 한다."""
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        boot = src[src.index("} else if (!firmKeyParam) {"):]
        boot = boot[:boot.index("} else {")]
        self.assertIn('showState("lookup")', boot)
        self.assertNotIn('showState("notfound")', boot)

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

    def test_sitemap_lists_static_firm_pages_but_never_query_urls(self):
        """[B1 2026-08-27] 종전 이름은 `..._firm_base_path_only` 였고 베이스 1건만 세었다.

        그 근거는 "개별 업체 URL 은 쿼리스트링 기반 **동적** 조회 페이지"였는데, 그
        전제가 사라졌다 — 문서 2건 이상 업체는 이제 진짜 정적 HTML 을 갖는다
        (`/findings/firm/{slug}/`, WebFirmPageTest 가 전수 검증). 그래서 세는 대상을
        바꾸되 **이 가드가 실제로 지키던 것은 그대로 남긴다**: 크롤러에게 빈 껍데기를
        광고하는 `?key=` URL 은 sitemap 에 절대 들어가지 않는다.

        개수는 매직 넘버로 박지 않고 **정본에서 파생**한다 — 데이터가 늘면 함께 는다."""
        self.assertIn(f"<loc>{render.SITE_BASE_URL}/findings/firm/</loc>", self.sitemap)
        # ★불변: 쿼리스트링 URL 은 어떤 형태로도 sitemap 에 없다.
        self.assertNotIn("/findings/firm/?", self.sitemap)
        self.assertNotIn("?key=", self.sitemap)
        docs = render.load_findings_docs()
        if not docs:
            self.skipTest("findings_docs.json 미존재")
        by_firm: dict[str, int] = {}
        for d in docs["documents"]:
            k = d.get("firm_key") or ""
            if k:
                by_firm[k] = by_firm.get(k, 0) + 1
        expected = sum(1 for n in by_firm.values() if n >= 2)
        listed = len(re.findall(
            rf"<loc>{re.escape(render.SITE_BASE_URL)}/findings/firm/[^/<]+/</loc>",
            self.sitemap))
        self.assertEqual(listed, expected,
                         "정적 업체 페이지 수와 sitemap 등록 수가 다르다")

    def test_nav_not_added_entry_only_via_link(self):
        # 요구사항: base.html nav 에 신규 탭을 추가하지 않는다(진입은 findings.js 의 문서
        # 카드 업체명 링크로만) — nav 링크 개수가 findings/trends 페이지와 동일(4개)해야 함.
        import re as _re
        nav_m = _re.search(r'<nav id="navmenu">(.*?)</nav>', self.html, _re.S)
        self.assertIsNotNone(nav_m)
        self.assertEqual(nav_m.group(1).count("<a "), 6)  # 주간 브리프·지적사항·트렌드·자료실·용어사전·이용안내
        self.assertNotIn("findings/firm", nav_m.group(1))

    def test_canonical_and_description(self):
        self.assertIn(
            f'<link rel="canonical" href="{render.SITE_BASE_URL}/findings/firm/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

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
        self.assertIn('loadingEl.textContent = _t("업체 프로파일 준비 중입니다.");', js_src)
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


# ── 실사관 프로파일 (FDA 483 서명 실사관 집계 — firm.html/firm.js 의 미러링) ────────

    # ── [P1.5-3] 규제 이력 — 문서 + 실사를 한 시간축에 ────────────────────────
    def test_regulatory_timeline_is_one_axis(self):
        """[P1.5-3 2026-08-27] 두 블록(문서 이력 · FDA 실사 이력)을 하나로 합쳤다.

        종전에는 나란한 두 목록이라 "이 업체에 무슨 일이 순서대로 있었나"를 읽는 사람이
        머릿속에서 맞춰야 했다. ★종전 주석의 '합치지 마라'가 금지한 것은 **서로 나누는
        것**(지적 문장 ÷ 실사 건수 같은 비율)이지 시간순 배열이 아니다 — 타임라인은
        분모를 만들지 않는다. 그 주석이 지키려던 단위 경고는 섹션 머리로 옮겨 유지한다."""
        self.assertIn('<section class="fp-block" aria-label="규제 이력">', self.html)
        self.assertNotIn('id="fp-insp-block"', self.html,
                         "실사 전용 블록이 되살아났다 — 시간축 병합 회귀")
        self.assertNotIn('<div id="fp-insp" class="fp-insp"></div>', self.html)
        # 요약·각주 자리는 남는다(타임라인이 표현 못 하는 것: 등급 구성·수집 범위).
        for frag in ('<p class="fp-insp-sub" id="fp-insp-sub"></p>',
                     '<p class="fp-insp-note" id="fp-insp-note"></p>',
                     '<div id="fp-docs" class="fp-docs"></div>'):
            self.assertIn(frag, self.html)
        # 단위 경고는 없애지 않고 섹션 머리로 옮긴다.
        self.assertIn("단위가 다릅니다", self.html)
        # ★인과를 만들지 않는다 — 실사와 문서를 잇는 조인 키가 없다.
        self.assertIn("서로의 원인·결과인 것은 아닙니다", self.html)

    def test_timeline_rows_carry_date_meaning(self):
        """★한 축에 두 종류가 섞이는 순간 **날짜 의미 차이**가 새 거짓말이 된다 —
        문서는 공개일(published_date), 실사는 실사 종료일(inspection_end_date).
        행마다 무엇이고 그 날짜가 무슨 날인지 적는다."""
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        self.assertIn('el("span", "fp-tl-when", _t("공개"))', src)
        self.assertIn('el("span", "fp-tl-when", _t("실사 종료"))', src)
        self.assertIn('el("span", "fp-tl-kind", _t("문서"))', src)
        self.assertIn('el("span", "fp-tl-kind insp", _t("실사"))', src)

    def test_timeline_sorts_desc_and_parks_undated_last(self):
        """정렬 계약 — 날짜 내림차순, 날짜가 빈 행은 맨 뒤.

        빈 날짜를 0000 으로 채워 맨 앞에 두면 가장 오래된 사건처럼 보인다."""
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        fn = src[src.index("function timelineEntries()"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("if (!a.at) return 1;", fn)
        self.assertIn("if (!b.at) return -1;", fn)
        self.assertIn("a.at < b.at ? 1 : (a.at > b.at ? -1 : 0)", fn)

    def test_category_filter_scope_is_disclosed(self):
        """분류 필터는 문서에만 걸린다(실사에는 분류가 없다). 실사가 **조용히** 빠지면
        "이 업체는 실사 기록이 없다"로 오독된다 — 그 사실을 말한다(부재 어휘 규율).
        실사가 없는 업체에서는 아무 말도 하지 않는다(없는 것을 설명하지 않는다)."""
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        fn = src[src.index("function timelineEntries()"):]
        fn = fn[:fn.index("\n  }\n")]
        self.assertIn("if (activeCat && !docHasCat(d, activeCat)) return;", fn)
        self.assertIn("if (!activeCat) {", fn)   # 실사는 필터 없을 때만
        self.assertIn("분류가 없어 이 필터에서 제외됩니다", src)
        self.assertIn("if (inspN > 0)", src)

    def test_inspection_history_isolated_chain(self):
        """워치리스트와 같은 격리 — 본기능 로드 성공 **후에만** 별도 체인으로 fetch 하고,
        실패해도 문서 타임라인은 그대로 남는다.

        ★병합 뒤에도 이 성질이 더 중요해졌다: 실사가 같은 목록에 들어가므로, 실사 fetch
        실패가 문서 목록까지 지우면 본기능이 무너진다. 실사는 **나중에 도착해 같은 목록을
        다시 그리는** 구조라 도착 전·실패 시에는 문서만 그려진 상태로 남는다."""
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        self.assertIn('rpcEndpoint("fda_inspection_firm")', src)
        self.assertIn("JSON.stringify({ p_firm_key: firmKey })", src)
        chain = src[src.index("fetchInspectionHistory(firmKeyParam)"):]
        self.assertIn("조용히 숨김 유지", chain[:300])
        # 실사 도착 전에도 문서는 그려진다 — renderAll 이 타임라인을 먼저 부른다.
        ra = src[src.index("function renderAll(data)"):]
        ra = ra[:ra.index("\n  }\n")]
        self.assertIn("renderTimeline();", ra)
        self.assertNotIn("LAST_INSP =", ra, "renderAll 이 실사 상태를 건드리면 안 된다")
        # 실사 응답이 오면 같은 목록을 다시 그린다.
        ri = src[src.index("function renderInspections(data)"):]
        self.assertIn("LAST_INSP = {", ri[:900])
        self.assertIn("renderTimeline();", ri[:900])
        # 하드 게이트 밖.
        gate = src[src.index("if (!cfg || !loadingEl"):]
        gate = gate[:gate.index("return;") + len("return;")]
        self.assertNotIn("LAST_INSP", gate)

    def test_absence_is_scoped_never_bare(self):
        """★부재 어휘 규율 — "실사 기록 없음"을 **범위 없이** 말하면 거짓이 된다.

        이 표는 FY2020 이후 Drug Quality Assurance 실사만 담는다. 0건 응답에서
        (1) 범위 문자열은 RPC scope 에서만 만들고(하드코딩 금지), (2) 범위를 만들 수
        없으면(구버전 응답) 부재 문장 자체를 싣지 않는다."""
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        fn = src[src.index("function renderInspections("):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("scope.fiscal_year_min", fn)
        self.assertIn(
            '_t("확인된 {range} 기록이 없습니다. 그 이전 실사나 다른 유형의 실사는 이 범위 밖입니다.", '
            '{ range: range })',
            fn)
        # 범위를 만들 수 없으면 부재 문장 자체를 싣지 않는다(빈 문자열로 남긴다).
        self.assertIn('inspSubEl.textContent = range', fn)
        # 템플릿·JS 어디에도 좁히지 않은 부재 단정이 없어야 한다.
        for text in (self.html, src):
            self.assertNotIn("실사 기록이 없습니다", text.replace("FDA 의약품 GMP 실사 기록이 없습니다", ""))

    def test_migration_063_reuses_canonical_firm_key(self):
        """★정규화 정본은 하나다 — 013 grm_normalize_firm_name 을 GENERATED STORED 로
        재사용해야 findings.firm_key 와 같은 키 공간에 산다. 새 정규화 함수를 만들면
        정본이 둘이 되고 반드시 갈라진다(055/058 country_key 와 같은 구조)."""
        sql = (WEB_DIR / "migrations" / "063_fda_inspection_firm.sql").read_text(encoding="utf-8")
        self.assertIn("generated always as (public.grm_normalize_firm_name(legal_name)) stored", sql)
        self.assertNotIn("create or replace function public.grm_normalize", sql)
        # RPC 계약: 0건도 null 이 아니라 0건 구조("미배포"와 "기록 없음"의 구분).
        self.assertIn("coalesce((", sql)
        # 지어낸 센티널 금지 — 빈 키는 조건으로 거른다.
        self.assertIn("k.key <> ''", sql)
        # revoke 가 grant 보다 먼저.
        self.assertLess(sql.index("revoke all on function public.fda_inspection_firm"),
                        sql.index("grant execute on function public.fda_inspection_firm"))
        # 원문 텍스트는 어떤 경로로도 나가지 않는다.
        for forbidden in ("finding_text", "source_url", "official_url"):
            self.assertNotIn(forbidden, sql)


class WebInspectorRenderTest(unittest.TestCase):
    """findings/inspector/index.html 은 findings/firm/index.html 과 동형인 정적 셸이다
    (런타임에 inspector.js 가 findings_inspector_profile(p_inspector_key) RPC 를 URL
    파라미터(?key=)로 직접 fetch). firm 과의 핵심 차이 두 가지를 여기서 고정한다:
      (1) 실명이 적시된 개인 집계라 sitemap 미등록 + noindex 오버라이드(firm 은 베이스
          경로만 sitemap 에 등록하고 색인은 허용 — inspector 는 그마저도 막는다),
      (2) 코호트 미달/미존재/키 오류/fetch 실패를 **구분하지 않고** 하나의 안내로 수렴
          (firm 은 "준비 중" vs "찾을 수 없음" 2상태로 구분하지만, 여기선 정보 누출 방지를
          위해 구분하지 않는다)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_inspector_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "inspector" / "index.html").read_text(encoding="utf-8")
        cls.firm_html = (cls.single / "findings" / "firm" / "index.html").read_text(encoding="utf-8")
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.archive = (cls.single / "archive" / "index.html").read_text(encoding="utf-8")
        cls.findings_html = (cls.single / "findings" / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")
        cls.js_src = (WEB_DIR / "assets" / "inspector.js").read_text(encoding="utf-8")
        cls.findings_js_src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_page_generated(self):
        self.assertIn("실사관 프로파일", self.html)
        self.assertIn("Inspector Profile", self.html)
        self.assertIn("AI Disclosure", self.html)

    def test_cfg_div_env_gated_empty_by_default_with_root(self):
        # 테스트 환경엔 SUPABASE_URL/ANON_KEY 미설정 — cfg data 속성은 항상 빈 문자열
        # (firm.js/findings.js 계약과 동일). data-root 는 rel_root 값("../../")을 그대로
        # 담는다 — findings/inspector/index.html 은 findings/firm/index.html 과 같은 깊이.
        self.assertIn(
            'id="grm-inspector-cfg" data-url="" data-key="" data-root="../../" hidden',
            self.html,
        )

    def test_inspector_js_referenced_with_content_hash(self):
        m = re.search(r'assets/inspector\.js\?v=([0-9a-f]{8})"', self.html)
        self.assertIsNotNone(m, "inspector.js 캐시버스팅 해시 미발견")

    def test_inspector_js_copied_verbatim(self):
        built = (self.single / "assets" / "inspector.js").read_bytes()
        src = (WEB_DIR / "assets" / "inspector.js").read_bytes()
        self.assertEqual(built, src, "inspector.js 가 dist 에 verbatim 복사되지 않음")

    def test_sitemap_excludes_inspector(self):
        # ★firm 과 다르다 — 베이스 경로조차 sitemap 에 넣지 않는다(실명 개인 집계이므로
        # 검색엔진 노출 표면을 아예 만들지 않는다).
        self.assertNotIn("findings/inspector", self.sitemap)

    def test_noindex_meta_present_on_inspector_only(self):
        self.assertIn('<meta name="robots" content="noindex, nofollow" />', self.html)
        # 회귀 가드 — 다른 페이지에는 noindex 가 새로 생기지 않아야 한다(base.html 의
        # meta_robots 훅은 기본이 빈 블록이라 오버라이드하지 않는 페이지는 무영향이어야 함).
        for name, page in (
            ("landing", self.landing),
            ("archive", self.archive),
            ("findings", self.findings_html),
            ("firm", self.firm_html),
        ):
            self.assertNotIn("noindex", page, f"{name} 페이지에 noindex 오염 발견(회귀)")

    def test_nav_not_added_entry_only_via_link(self):
        nav_m = re.search(r'<nav id="navmenu">(.*?)</nav>', self.html, re.S)
        self.assertIsNotNone(nav_m)
        self.assertEqual(nav_m.group(1).count("<a "), 6)  # 주간 브리프·지적사항·트렌드·자료실·용어사전·이용안내
        self.assertNotIn("findings/inspector", nav_m.group(1))

    def test_canonical_and_description(self):
        # sitemap 미등록과 별개로 canonical 은 유지한다(중복 URL 정리 목적).
        self.assertIn(
            f'<link rel="canonical" href="{render.SITE_BASE_URL}/findings/inspector/" />', self.html)
        self.assertIn('<meta name="description" content="', self.html)

    def test_rpc_endpoint_and_safe_contract_present(self):
        self.assertIn('rpcEndpoint("findings_inspector_profile")', self.js_src)
        self.assertIn('method: "POST"', self.js_src)
        self.assertIn('apikey: key, Authorization: "Bearer " + key', self.js_src)
        self.assertIn('JSON.stringify({ p_inspector_key: inspectorKey })', self.js_src)
        # 원문(finding_text/finding_text_ko)은 RPC 가 아니라 별개 anon REST 로만 가져온다.
        self.assertIn('"/rest/v1/findings?select="', self.js_src)
        self.assertIn("raw_signal_id=eq.", self.js_src)

    def test_five_failure_modes_unify_into_single_state(self):
        """firm.js 는 013 미적용(RPC 404/network 실패)과 key 없음/빈 프로파일을 서로 다른
        상태(준비 중 vs 찾을 수 없음)로 구분하지만, inspector.js 는 코호트 미달·미존재·키
        오류·key 파라미터 없음·fetch 실패를 **구분하지 않고** 전부 "unavailable" 하나로
        수렴시킨다(정보 누출 방지) — showState 가 loading/unavailable/content 세 가지뿐."""
        self.assertIn('showState("unavailable")', self.js_src)
        self.assertNotIn('showState("notfound")', self.js_src)
        self.assertNotIn('showState("error")', self.js_src)
        # 최소 3개 호출 지점(no url/key/keyParam, null 프로파일, catch)이 전부 같은 상태로.
        self.assertGreaterEqual(self.js_src.count('showState("unavailable")'), 3)

    def test_mandatory_notice_text_present_verbatim(self):
        self.assertIn(
            "이 페이지는 공개된 FDA Form 483 문서의 서명란에서 기계적으로 추출한 사실만 "
            "집계합니다. 실사관 개인에 대한 평가나 성향 판단이 아니며, 문서 5건 이상이 "
            "확인된 경우에만 제공됩니다.",
            self.html,
        )

    def test_name_rendered_via_textcontent_only(self):
        self.assertIn('nameEl.textContent = data.display_name || "";', self.js_src)

    def test_no_innerhtml_data_injection(self):
        for m in re.finditer(r'\w+\.innerHTML\s*=\s*(.+?);', self.js_src):
            self.assertEqual(m.group(1).strip(), '""', f"innerHTML 데이터 삽입 의심: {m.group(0)}")

    def test_no_new_external_resources(self):
        self.assertNotIn("cdn.", self.js_src)
        self.assertNotIn("<canvas", self.html)

    def test_firm_link_uses_sibling_relative_path(self):
        # findings/inspector/index.html 은 findings/firm/index.html 과 같은 findings/
        # 하위 형제 디렉터리라 rel_root 계산 없이 "../firm/index.html" 상대경로 하나로
        # 충분하다(trends.js buildFirmProfileLink 와 동일 관례 — firm.js 자신은 이미 그
        # 업체 페이지라 문서별 업체 링크가 없어서, 대신 형제 페이지인 trends.js 의 관례를
        # 그대로 따른다).
        self.assertIn(
            'firmLink.href = "../firm/index.html?key=" + encodeURIComponent(doc.firm_key);',
            self.js_src,
        )

    # ── [A2] 문서 상세 링크 멤버십 ────────────────────────────────────────────
    def test_doc_page_membership_endpoint_and_schema_gate(self):
        """assets/inspector-doc-pages.json 은 다른 에이전트가 발행하는 자산이라 이
        세션에 실물이 없을 수 있다 — 계약은 소스 텍스트로 고정한다: root 기준 상대
        경로로 fetch, {schema:"grm-inspector-doc-pages/v1", document_ids:[...]} 형태만
        신뢰하고, 스키마가 다르면(구버전·손상) null 로 수렴해 링크를 걸지 않는다."""
        self.assertIn('fetch(root + "assets/inspector-doc-pages.json")', self.js_src)
        self.assertIn('data.schema !== "grm-inspector-doc-pages/v1"', self.js_src)
        self.assertIn("!Array.isArray(data.document_ids)", self.js_src)

    def test_doc_page_fetch_is_lazy_cached_and_swallows_failure(self):
        """세션당 1회 lazy fetch(캐시) + 실패는 조용히 삼킨다 — 문서 목록 렌더 자체를
        막지 않는다(임무 지시서 근거)."""
        fn = self.js_src[self.js_src.index("function fetchDocPageIds()"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (docPagesPromise) return docPagesPromise;", fn)
        self.assertIn(".catch(function () { return null; })", fn)

    def test_doc_link_gated_by_membership_else_plain_text(self):
        """document_id 가 집합에 있을 때만 findings/doc/{document_id}/ 링크, 없으면
        현행처럼 평문(날짜+소스 배지만) — 확인 없이 링크했다가 16% 404 났던 실측 근거."""
        fn = self.js_src[self.js_src.index("function appendDocTitleArea(main, doc)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("DOC_PAGE_IDS && docId && DOC_PAGE_IDS[docId]", fn)
        self.assertIn('root + "findings/doc/" + encodeURIComponent(docId) + "/"', fn)
        # 멤버십이 없을 때(hasPage=false) 링크 없이 기존과 동일한 평문 span 만 붙는다.
        self.assertIn('main.appendChild(el("span", "ip-doc-date", doc.published_date || ""));', fn)

    def test_doc_link_click_does_not_also_toggle_expand(self):
        """링크는 main(펼치기 토글의 클릭 대상) 안에 중첩돼 있다 — stopPropagation 없이
        두면 문서 페이지 이동과 지적사항 펼치기가 한 클릭에 동시 발화한다(임무 지시서의
        '서로 삼키지 않게' 요구)."""
        fn = self.js_src[self.js_src.index("function appendDocTitleArea(main, doc)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('link.addEventListener("click", function (ev) { ev.stopPropagation(); });', fn)
        # 펼치기 토글 배선(makeClickableRow) 자체는 이 변경으로 건드리지 않았다(회귀 방지).
        self.assertIn(
            'makeClickableRow(main, _t("{source} {date} 지적사항 펼치기", '
            '{ source: doc.source || "", date: doc.published_date || "" }),',
            self.js_src,
        )

    def test_doc_link_uses_element_creation_not_innerhtml(self):
        fn = self.js_src[self.js_src.index("function appendDocTitleArea(main, doc)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn('var link = document.createElement("a");', fn)
        self.assertNotIn("innerHTML", fn)

    def test_scope_guard_no_ranking_or_comparison_symbols(self):
        """★의도적 범위 제한(회귀 금지 — 037 2026-08-31 개정으로 교체).

        개정 전 이 가드는 "실사관 목록/디렉터리" 자체를 금지 심볼 목록에 넣었다
        (renderInspectorList·listInspectors·inspectorDirectory). 그 금지는 037 이 실제로
        막으려던 것(사람을 서열화하는 것)과 "목록이 존재하는 것"을 같은 것으로 취급한
        낡은 경계였다 — 지금 이 파일은 정확히 그런 이름의 색인 렌더 함수
        (buildIndexGroups/renderIndexGroups)를 실제로 갖고 있고, 그것은 회귀가 아니다.
        새 경계: 여전히 금지된 것은 실사관 간 **순위·비교**, "엄격하다/까다롭다" 류
        성향 해석, **건수 기준 정렬(=사실상의 랭킹)**뿐이다."""
        forbidden = (
            "rankInspectors", "compareInspectors", "inspectorRanking",
            "sortByDocumentCount", "sortByDocuments", "mostFindings", "topInspector",
        )
        for sym in forbidden:
            self.assertNotIn(sym, self.js_src, f"범위 밖 심볼 발견(inspector.js): {sym}")
            self.assertNotIn(sym, self.findings_js_src, f"범위 밖 심볼 발견(findings.js): {sym}")
        # 건수 기준 정렬 비교자(내림차순이든 오름차순이든) 자체가 금지 — 색인은 이름순만.
        for pattern in ("b.documents - a.documents", "a.documents - b.documents",
                        "sort(function (a, b) { return b.documents", ".documents - "):
            self.assertNotIn(pattern, self.js_src, f"건수 기준 정렬 패턴 발견: {pattern}")
        # "엄격하다/까다롭다" 류 성향 해석 문구는 **화면에 보이는 텍스트**(렌더된 HTML)에만
        # 금지한다 — 소스 주석이 "이런 문구를 만들지 않는다"고 설명하는 것 자체는 회귀가
        # 아니다(js_src 를 검사하면 이 주석 자체가 오탐을 낸다).
        for phrase in ("엄격", "까다롭"):
            self.assertNotIn(phrase, self.html)

    @staticmethod
    def _slice_top_level_fn(src, name):
        """inspector.js 의 2-space 들여쓰기 최상위 함수를 이름으로 뽑는다(기존
        test_normalize_inspector_key_behavior_via_node 가 쓰던 것과 같은 관례 —
        내부 콜백은 4칸 이상 들여쓰기라 "\\n  }\\n"(정확히 2칸) 패턴과 충돌하지 않는다)."""
        sig = "function " + name + "("
        start = src.index(sig)
        end = src.index("\n  }\n", start) + len("\n  }\n")
        return src[start:end]

    def test_index_data_shape_never_carries_document_count(self):
        """★037 2026-08-31 개정의 핵심 비공허 가드 — 색인이 실제로 소비하는 데이터
        모양(buildIndexGroups 반환 객체)과 그 값을 화면에 꽂는 함수(buildIndexItemLink)
        양쪽 모두 documents(건수) 필드를 절대 다루지 않아야 한다. data.documents(프로파일
        문서 **목록** — 전혀 다른 의미)와 이름이 겹치므로 파일 전체가 아니라 색인 전용
        함수 슬라이스 안에서만 검사한다(전체 검사는 정상적인 documents[] 사용을 오탐한다)."""
        for fn_name in ("buildIndexGroups", "filterIndexRows", "buildIndexItemLink",
                         "renderIndexGroups"):
            with self.subTest(fn=fn_name):
                fn = self._slice_top_level_fn(self.js_src, fn_name)
                self.assertNotIn(".documents", fn,
                                  f"{fn_name} 슬라이스가 documents(건수) 필드를 참조한다")
        # buildIndexItemLink(개별 항목 렌더) 는 코호트 게이트 고지문(공개 문서 5건 이상…)과
        # 달리 항목 단위로 아무 숫자도 그리지 않는다 — 그 함수 슬라이스에는 "건" 자체가
        # 없어야 한다(renderIndexGroups 의 빈 결과 고지문과는 다른 계층이라 따로 본다).
        item_fn = self._slice_top_level_fn(self.js_src, "buildIndexItemLink")
        self.assertNotIn("건", item_fn, "항목 렌더 함수에 건수 표기 단위가 있다")

    def test_normalize_inspector_key_structural_contract(self):
        """정규화 헬퍼는 findings.js/inspector.js 양쪽에 독립 복제본으로 존재하고, 서버
        규칙(소문자→마침표 제거→공백 연속 1칸→trim) 4종을 모두 구현해야 한다."""
        for label, src in (("inspector.js", self.js_src), ("findings.js", self.findings_js_src)):
            with self.subTest(file=label):
                self.assertIn("function normalizeInspectorKey(name)", src)
                fn = src[src.index("function normalizeInspectorKey(name)"):]
                fn = fn[:fn.index("\n  }\n") + 4]
                self.assertIn(".toLowerCase()", fn)
                self.assertIn('.replace(/\\./g, "")', fn)
                self.assertIn('.replace(/\\s+/g, " ")', fn)
                self.assertIn(".trim()", fn)

    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — 정규화 동작 고정은 CI에서 수행")
    def test_normalize_inspector_key_behavior_via_node(self):
        """서버 규칙(소문자→마침표 제거→공백 연속 1칸→trim, 예: "Eileen A. Liu" →
        "eileen a liu")을 두 파일의 실제 함수로 각각 실행해 고정하고, 두 복제본의 산출이
        서로 동일한지(파리티)도 함께 확인한다."""
        import subprocess

        cases = [
            "Eileen A. Liu",
            "  ANASTASIA   M.  Shields  ",
            "john.q.public",
            "",
            None,
        ]

        def run_for(js_path: pathlib.Path) -> list:
            src = js_path.read_text(encoding="utf-8")
            fn_src = src[src.index("function normalizeInspectorKey(name)"):]
            fn_src = fn_src[:fn_src.index("\n  }\n") + 4]
            driver = fn_src + "\nconsole.log(JSON.stringify(" + json.dumps(cases) + \
                ".map(normalizeInspectorKey)));"
            tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_normkey_"))
            try:
                drv = tmp / "driver.js"
                drv.write_text(driver, encoding="utf-8")
                proc = subprocess.run(["node", str(drv)], capture_output=True, text=True, timeout=30)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            self.assertEqual(proc.returncode, 0, f"node 실행 실패({js_path.name}): {proc.stderr}")
            return json.loads(proc.stdout)

        expected = [
            "eileen a liu",
            "anastasia m shields",
            "johnqpublic",
            "",
            "",
        ]
        inspector_out = run_for(WEB_DIR / "assets" / "inspector.js")
        findings_out = run_for(WEB_DIR / "assets" / "findings.js")
        self.assertEqual(inspector_out, expected)
        self.assertEqual(findings_out, expected, "findings.js normalizeInspectorKey 가 서버 규칙과 어긋남")
        self.assertEqual(inspector_out, findings_out, "두 복제본의 정규화 결과가 서로 다름(파리티 위반)")

    @staticmethod
    def _extract_for_node(names):
        """inspector.js 에서 이름 목록을 순서대로 뽑아 이어붙인다 — 함수 선언
        ("function NAME(" 부터 "\\n  }\\n" 까지, _slice_top_level_fn 과 동일 관례)과
        var 선언("var NAME = " 부터 그 줄의 ";" 까지) 양쪽을 지원한다(fetchInspector
        ProfileWithRetry 가 참조하는 RETRY_DELAY_MS 처럼 함수가 아닌 상수도 있다 —
        드라이버에서 빠지면 ReferenceError 로 조용히 실패해 재시도 자체가 무산된다).
        Node 드라이버 스크립트에 그대로 주입할 소스 조각을 돌려준다.

        [i18n 2단계] 추출한 조각이 `_t(...)` 를 참조할 수 있으므로(FIRM_BLANK_LABEL 등)
        node 에서 단독 실행 가능하도록 grm_i18n.JS_SHIM(정본 shim)을 앞에 붙인다 — shim
        문구를 손으로 베끼지 않는다. shim 자체는 브라우저 전역 `window` 를 참조하므로,
        plain node(=window 없음)에서 `_t()`가 죽지 않도록 빈 `window` 스텁도 함께 준다
        (카탈로그가 없으므로 항등 — 한국어 그대로 돌려주는 기존 기대와 일치)."""
        src = (WEB_DIR / "assets" / "inspector.js").read_text(encoding="utf-8")
        out = []
        for name in names:
            fn_sig = "function " + name + "("
            var_sig = "var " + name + " = "
            if fn_sig in src:
                start = src.index(fn_sig)
                end = src.index("\n  }\n", start) + len("\n  }\n")
            else:
                start = src.index(var_sig)
                end = src.index(";", start) + 1
            out.append(src[start:end])
        return "var window = {};\n" + grm_i18n.JS_SHIM + "\n".join(out)

    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — CI 에서 수행")
    def test_index_grouping_and_filtering_behavior_via_node(self) -> None:
        """★037 2026-08-31 개정의 데이터 계층 증명 — B(이름순 색인)가 실제로 소비하는
        buildIndexGroups/filterIndexRows 를 원본 그대로 Node 로 실행해 다음을 고정한다:
        (1) 정렬은 display_name 오름차순뿐, (2) 첫 글자로 그룹핑, (3) 반환 객체 어디에도
        documents(건수) 필드가 없다 — 입력에 documents 가 있어도 새어 나가지 않는다."""
        import subprocess

        fn_src = self._extract_for_node(["filterIndexRows", "buildIndexGroups"])
        rows = [
            {"inspector_key": "c", "display_name": "Charlie Amos", "documents": 20},
            {"inspector_key": "a", "display_name": "amanda rutter", "documents": 9},
            {"inspector_key": "b", "display_name": "Bob K", "documents": 5},
            {"inspector_key": "d", "display_name": "", "documents": 99},  # 방어: 빈 이름 제외
        ]
        driver = fn_src + "\n".join([
            "",
            "var rows = " + json.dumps(rows) + ";",
            "var out = {};",
            "out.groups_empty_query = buildIndexGroups(filterIndexRows(rows, ''));",
            "out.groups_filtered_aman = buildIndexGroups(filterIndexRows(rows, 'AMAN'));",
            "out.groups_filtered_none = buildIndexGroups(filterIndexRows(rows, 'zzz'));",
            "console.log(JSON.stringify(out));",
        ])
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_idx_"))
        try:
            drv = tmp / "driver.js"
            drv.write_text(driver, encoding="utf-8")
            proc = subprocess.run(["node", str(drv)], capture_output=True, text=True, timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
        out = json.loads(proc.stdout)

        # (1)+(2) 이름순 오름차순 + 첫 글자 그룹핑, 빈 이름은 제외.
        self.assertEqual(
            out["groups_empty_query"],
            [
                {"letter": "A", "items": [{"inspector_key": "a", "display_name": "amanda rutter"}]},
                {"letter": "B", "items": [{"inspector_key": "b", "display_name": "Bob K"}]},
                {"letter": "C", "items": [{"inspector_key": "c", "display_name": "Charlie Amos"}]},
            ],
        )
        # 빈 질의 = 전체 복귀(037 개정으로 새로 허용된 지점).
        self.assertEqual(len(out["groups_empty_query"]), 3)
        # 부분일치 필터(대소문자 무시) — 대문자 질의 "AMAN" 이 소문자 표기 "amanda rutter"
        # 를 찾아야 한다(대소문자 무시), 다른 항목은 걸리지 않아야 한다.
        filtered_names = [it["display_name"] for g in out["groups_filtered_aman"] for it in g["items"]]
        self.assertEqual(filtered_names, ["amanda rutter"])
        self.assertEqual(out["groups_filtered_none"], [])
        # (3) ★가장 중요 — 어떤 경로로도 documents(건수)가 출력에 없다(입력엔 있었다).
        serialized = json.dumps(out)
        self.assertNotIn("documents", serialized,
                          "buildIndexGroups/filterIndexRows 출력에 건수 필드가 새어 나갔다")
        self.assertNotIn("20", serialized)  # amanda=9·charlie=20·bob=5 입력값이 그대로 안 보임
        self.assertNotIn("99", serialized)

    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — CI 에서 수행")
    def test_a3_rpc_retry_behavior_via_node(self) -> None:
        """A3 — findings_inspector_profile 호출이 5xx/네트워크 오류면 400ms 후 1회만
        재시도, null(코호트 미달·미존재)은 정상 응답이라 재시도하지 않으며, 재시도까지
        실패하면 그대로 reject 되는지(바깥 037 단일 상태 수렴은 그 reject 를 잡아 처리)를
        실제 함수를 Node 로 실행해 고정한다. fetch/타이머는 전부 스텁 — 실 네트워크 없음."""
        import subprocess

        fn_src = self._extract_for_node([
            "fetchInspectorProfileOnce", "RETRY_DELAY_MS", "isRetryableFetchError", "delay",
            "fetchInspectorProfileWithRetry",
        ])
        driver = "\n".join([
            'var url = "https://example.supabase.co";',
            'var key = "anon-key";',
            "function rpcEndpoint(name) {"
            ' return url.replace(/\\/$/, "") + "/rest/v1/rpc/" + name; }',
            fn_src,
            "",
            "var calls;",
            "function mockFetchSeq(seq) {",
            "  return function () {",
            "    var behavior = seq[calls]; calls++;",
            "    if (behavior.netError) return Promise.reject(new Error('network fail'));",
            "    return Promise.resolve({",
            "      ok: behavior.status < 400, status: behavior.status,",
            "      json: function () { return Promise.resolve(behavior.body); },",
            "    });",
            "  };",
            "}",
            "",
            "async function run() {",
            "  var results = {};",
            "",
            "  calls = 0;",
            "  fetch = mockFetchSeq([{status:500, body:null}, {status:200, body:{display_name:'X'}}]);",
            "  try { var d1 = await fetchInspectorProfileWithRetry('k');",
            "    results.retry_after_500 = { ok: true, data: d1, calls: calls }; }",
            "  catch (e) { results.retry_after_500 = { ok: false, calls: calls }; }",
            "",
            "  calls = 0;",
            "  fetch = mockFetchSeq([{status:200, body:null}]);",
            "  try { var d2 = await fetchInspectorProfileWithRetry('k');",
            "    results.null_is_not_retried = { ok: true, data: d2, calls: calls }; }",
            "  catch (e) { results.null_is_not_retried = { ok: false, calls: calls }; }",
            "",
            "  calls = 0;",
            "  fetch = mockFetchSeq([{status:500, body:null}, {status:500, body:null}]);",
            "  try { var d3 = await fetchInspectorProfileWithRetry('k');",
            "    results.two_failures_reject = { ok: true, calls: calls }; }",
            "  catch (e) { results.two_failures_reject = { ok: false, calls: calls }; }",
            "",
            "  calls = 0;",
            "  fetch = mockFetchSeq([{netError:true}, {status:200, body:{display_name:'Y'}}]);",
            "  try { var d4 = await fetchInspectorProfileWithRetry('k');",
            "    results.network_error_then_success = { ok: true, data: d4, calls: calls }; }",
            "  catch (e) { results.network_error_then_success = { ok: false, calls: calls }; }",
            "",
            "  console.log(JSON.stringify(results));",
            "}",
            "run();",
        ])
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_retry_"))
        try:
            drv = tmp / "driver.js"
            drv.write_text(driver, encoding="utf-8")
            proc = subprocess.run(["node", str(drv)], capture_output=True, text=True, timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
        out = json.loads(proc.stdout)

        self.assertEqual(out["retry_after_500"],
                          {"ok": True, "data": {"display_name": "X"}, "calls": 2},
                          "500 -> 재시도 -> 200 성공 경로가 어긋남")
        self.assertEqual(out["null_is_not_retried"],
                          {"ok": True, "data": None, "calls": 1},
                          "null(코호트 미달·미존재)은 정상 응답이라 재시도하면 안 된다")
        self.assertEqual(out["two_failures_reject"],
                          {"ok": False, "calls": 2},
                          "1회 재시도까지 실패하면 reject 되어야 한다(추가 재시도 없음)")
        self.assertEqual(out["network_error_then_success"],
                          {"ok": True, "data": {"display_name": "Y"}, "calls": 2},
                          "네트워크 오류(상태 없음)도 재시도 대상이어야 한다")

    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — CI 에서 수행")
    def test_a2_doc_page_membership_behavior_via_node(self) -> None:
        """A2 — fetchDocPageIds 를 실제로 실행해 (1) 올바른 스키마면 document_ids 를
        멤버십 집합으로 바꾸고, (2) 스키마가 다르거나 document_ids 가 배열이 아니면
        null, (3) HTTP 실패·네트워크 오류도 null 로 조용히 수렴하는지 고정한다."""
        import subprocess

        fn_src = self._extract_for_node(["fetchDocPageIds"])
        driver = "\n".join([
            'var root = "../../";',
            "var docPagesPromise = null;",
            "var DOC_PAGE_IDS = null;",
            fn_src,
            "",
            "async function run() {",
            "  var results = {};",
            "",
            "  docPagesPromise = null; DOC_PAGE_IDS = null;",
            "  fetch = function () { return Promise.resolve({ ok: true,",
            "    json: function () { return Promise.resolve(",
            '      {schema: "grm-inspector-doc-pages/v1", document_ids: ["fda483-1", "fda483-2"]}',
            "    ); } }); };",
            "  results.valid_schema = await fetchDocPageIds();",
            "",
            "  docPagesPromise = null; DOC_PAGE_IDS = null;",
            "  fetch = function () { return Promise.resolve({ ok: true,",
            '    json: function () { return Promise.resolve({schema: "wrong/v1", document_ids: ["x"]}); } }); };',
            "  results.wrong_schema = await fetchDocPageIds();",
            "",
            "  docPagesPromise = null; DOC_PAGE_IDS = null;",
            "  fetch = function () { return Promise.resolve({ ok: true,",
            '    json: function () { return Promise.resolve({schema: "grm-inspector-doc-pages/v1", document_ids: "not-an-array"}); } }); };',
            "  results.non_array_ids = await fetchDocPageIds();",
            "",
            "  docPagesPromise = null; DOC_PAGE_IDS = null;",
            "  fetch = function () { return Promise.resolve({ ok: false, status: 404 }); };",
            "  results.http_404 = await fetchDocPageIds();",
            "",
            "  docPagesPromise = null; DOC_PAGE_IDS = null;",
            "  fetch = function () { return Promise.reject(new Error('network fail')); };",
            "  results.network_error = await fetchDocPageIds();",
            "",
            "  console.log(JSON.stringify(results));",
            "}",
            "run();",
        ])
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_docpages_"))
        try:
            drv = tmp / "driver.js"
            drv.write_text(driver, encoding="utf-8")
            proc = subprocess.run(["node", str(drv)], capture_output=True, text=True, timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
        out = json.loads(proc.stdout)

        self.assertEqual(out["valid_schema"], {"fda483-1": True, "fda483-2": True})
        self.assertIsNone(out["wrong_schema"], "스키마 불일치를 링크 가능으로 오판했다")
        self.assertIsNone(out["non_array_ids"], "document_ids 비배열을 방어하지 못했다")
        self.assertIsNone(out["http_404"], "404 가 조용히 null 로 수렴하지 않는다")
        self.assertIsNone(out["network_error"], "네트워크 오류가 조용히 null 로 수렴하지 않는다")

    def test_findings_js_inspector_cohort_fetched_once_and_cached(self):
        """findings_inspector_index() 는 세션당 1회만 호출·캐시한다(카드마다 재조회 금지)."""
        self.assertIn("var inspectorCohort = null;", self.findings_js_src)
        self.assertIn("function fetchInspectorCohort()", self.findings_js_src)
        self.assertIn('"/rest/v1/rpc/findings_inspector_index"', self.findings_js_src)
        # 호출 지점은 정확히 1곳(정의부의 "function fetchInspectorCohort()" 를 제외한
        # 호출 "fetchInspectorCohort();" 문자열이 1회만 등장).
        self.assertEqual(self.findings_js_src.count("fetchInspectorCohort();"), 1)

    def test_findings_js_inspector_name_link_gated_by_cohort_else_plaintext(self):
        """코호트에 있으면 링크, 없으면(또는 인덱스 미도착=null) 평문 — 두 분기 모두
        존재해야 하고, 이름은 textContent 로만 들어가며, 링크에는 nofollow 를 단다."""
        fn = self.findings_js_src[self.findings_js_src.index("function buildDocHead(rows)"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("if (inspectorCohort && inspectorCohort[ik]) {", fn)
        self.assertIn('iLink.href = "inspector/index.html?key=" + encodeURIComponent(ik);', fn)
        self.assertIn('iLink.rel = "nofollow";', fn)
        self.assertIn("iLink.textContent = name;", fn)
        self.assertIn("inspectorSpan.appendChild(document.createTextNode(name));", fn)
        # innerHTML 삽입 경로 없음(textContent/createTextNode 전용).
        self.assertNotIn("innerHTML", fn)


class WebInspectorFirmBlockTest(unittest.TestCase):
    """[확인한 제조소 2026-08-31] 프로파일 신규 블록 — RPC 를 새로 만들지 않고
    findings_inspector_profile 응답의 documents[] 를 firm_name 으로 클라이언트에서
    집계한다(inspector.js buildFirmGroups/buildFirmRow/renderFirms). '반복 확인된
    영역' 다음·'연도별 추이' 앞이라는 서사 순서(무엇을→어디를→언제→원자료)와, 정렬은
    이름순 고정(건수순 금지, 사용자 확정)을 여기서 고정한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_inspfirm_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "inspector" / "index.html").read_text(encoding="utf-8")
        cls.js_src = (WEB_DIR / "assets" / "inspector.js").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ── 마크업(셸) ──────────────────────────────────────────────────────────
    def test_block_present_hidden_by_default_with_title_and_note(self):
        self.assertIn(
            '<section class="ip-block" id="ip-firm-block" aria-label="확인한 제조소" hidden>',
            self.html)
        block = self.html[self.html.index('id="ip-firm-block"'):]
        block = block[:block.index("</section>")]
        self.assertIn('<h2 class="ip-h">확인한 제조소</h2>', block)
        self.assertIn(
            "이 실사관이 서명한 공개 문서에 나타난 제조소입니다. "
            "이름순이며 제조소 간 비교나 순위가 아닙니다.", block)
        self.assertIn('id="ip-firm" class="ip-firm"', block)

    def test_block_positioned_between_repeats_and_year_trend(self):
        """서사 순서 = 무엇을(카테고리·반복) → 어디를(제조소) → 언제(연도) → 원자료(문서)."""
        i_rep = self.html.index('id="ip-rep-block"')
        i_firm = self.html.index('id="ip-firm-block"')
        i_year = self.html.index('aria-label="연도별 추이"')
        self.assertLess(i_rep, i_firm, "제조소 블록이 반복 확인된 영역보다 앞에 있다")
        self.assertLess(i_firm, i_year, "제조소 블록이 연도별 추이보다 뒤에 있다")

    # ── 집계(순수 함수, node 실행으로 동작 고정) ────────────────────────────────
    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — CI 에서 수행")
    def test_build_firm_groups_behavior_via_node(self):
        """이름순 정렬(건수 무관) · 연도 오름차순 dedup · firm_key 는 그룹의 첫 유효값을
        유지 · firm_name 빈 값은 '미확인'(card_scaffold.VALUE_UNKNOWN 과 동일한 부재
        어휘)으로 묶고, 그 버킷은 입력에 firm_key 가 있어도 절대 신뢰하지 않는다(링크
        금지 — 서로 다른 실제 제조소가 섞였을 수 있다)."""
        import subprocess

        fn_src = WebInspectorRenderTest._extract_for_node(
            ["decodeFirmDisplay", "FIRM_BLANK_LABEL", "buildFirmGroups"])
        rows = [
            {"firm_name": "Beta Pharma", "firm_key": "beta", "published_date": "2024-03-01"},
            {"firm_name": "Beta Pharma", "firm_key": "beta-dup", "published_date": "2023-01-01"},
            {"firm_name": "Alpha Labs", "firm_key": "alpha", "published_date": "2022-05-01"},
            {"firm_name": "Alpha Labs", "firm_key": "", "published_date": "2022-09-01"},
            # firm_name 공백뿐 + firm_key 가 있어도(방어 케이스) '미확인' 버킷은 링크를
            # 만들 수 없어야 한다 — 아래에서 firm_key=="" 를 직접 검증한다.
            {"firm_name": "  ", "firm_key": "ghost", "published_date": "2021-01-01"},
        ] + [
            # 건수(5)가 가장 많아도 이름순에서는 Alpha/Beta 뒤에 와야 한다(건수 정렬 금지).
            {"firm_name": "ZZZ High Count", "firm_key": "zzz", "published_date": "2020-02-0" + str(i)}
            for i in range(1, 6)
        ]
        driver = fn_src + "\n".join([
            "",
            "var rows = " + json.dumps(rows) + ";",
            "console.log(JSON.stringify(buildFirmGroups(rows)));",
        ])
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_firmgroups_"))
        try:
            drv = tmp / "driver.js"
            drv.write_text(driver, encoding="utf-8")
            # ★출력에 "미확인"(한글)이 섞인다 — text=True 만 쓰면 이 환경의 로캘
            # 코드페이지(cp949)로 디코드를 시도해 UnicodeDecodeError 로 stdout 이
            # None 이 된다(실측). encoding="utf-8" 을 명시해야 node 가 실제로 쓴
            # UTF-8 바이트를 로캘과 무관하게 그대로 읽는다.
            proc = subprocess.run(["node", str(drv)], capture_output=True,
                                   encoding="utf-8", timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
        out = json.loads(proc.stdout)

        by_name = {g["name"]: g for g in out}
        self.assertEqual(
            set(by_name), {"Alpha Labs", "Beta Pharma", "ZZZ High Count", "미확인"})

        # 건수 기준이 아니라 이름순(라틴 3개는 어떤 콜레이션에서도 이 순서가 고정된다 —
        # 혼용 스크립트("미확인" vs 라틴)와의 상대순서는 ICU 데이터에 따라 갈릴 수 있어
        # 여기서는 단정하지 않는다).
        latin_order = [g["name"] for g in out if g["name"] != "미확인"]
        self.assertEqual(latin_order, ["Alpha Labs", "Beta Pharma", "ZZZ High Count"],
                          "건수가 아니라 이름순이어야 한다(ZZZ 는 건수 5로 가장 많다)")

        self.assertEqual(by_name["Alpha Labs"]["count"], 2)
        self.assertEqual(by_name["Alpha Labs"]["years"], ["2022"])
        self.assertEqual(by_name["Alpha Labs"]["firm_key"], "alpha")

        self.assertEqual(by_name["Beta Pharma"]["count"], 2)
        self.assertEqual(by_name["Beta Pharma"]["years"], ["2023", "2024"])
        self.assertEqual(by_name["Beta Pharma"]["firm_key"], "beta",
                          "firm_key 는 그룹의 첫 값을 유지해야 한다(마지막 값이 아니라)")

        self.assertEqual(by_name["ZZZ High Count"]["count"], 5)
        self.assertEqual(by_name["ZZZ High Count"]["years"], ["2020"])

        blank = by_name["미확인"]
        self.assertEqual(blank["count"], 1)
        self.assertEqual(blank["years"], ["2021"])
        self.assertEqual(blank["firm_key"], "",
                          "이름이 없는 버킷은 입력에 firm_key 가 있어도 절대 신뢰하면 안 된다")

    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — CI 에서 수행")
    def test_build_firm_groups_empty_when_no_documents(self):
        import subprocess

        fn_src = WebInspectorRenderTest._extract_for_node(
            ["decodeFirmDisplay", "FIRM_BLANK_LABEL", "buildFirmGroups"])
        driver = fn_src + "\nconsole.log(JSON.stringify(buildFirmGroups([])));"
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_firmgroups_empty_"))
        try:
            drv = tmp / "driver.js"
            drv.write_text(driver, encoding="utf-8")
            proc = subprocess.run(["node", str(drv)], capture_output=True, text=True, timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
        self.assertEqual(json.loads(proc.stdout), [])

    # ── 렌더(DOM 생성, 소스 패턴) ─────────────────────────────────────────────
    def test_build_firm_row_link_gated_by_firm_key_else_plain_text(self):
        fn = WebInspectorRenderTest._slice_top_level_fn(self.js_src, "buildFirmRow")
        self.assertIn("if (g.firm_key) {", fn)
        self.assertIn(
            'link.href = root + "findings/firm/index.html?key=" + encodeURIComponent(g.firm_key);',
            fn)
        self.assertIn("link.textContent = g.name;", fn)
        self.assertIn('el("span", "ip-firm-name", g.name)', fn, "평문 분기(else)가 없다")
        self.assertNotIn("innerHTML", fn)

    def test_build_firm_row_meta_text_format(self):
        """'문서 N건 · 2023, 2024' — 연도가 없으면 '·' 이하가 없다."""
        fn = WebInspectorRenderTest._slice_top_level_fn(self.js_src, "buildFirmRow")
        self.assertIn('var meta = _t("문서 {n}건", { n: fmtNum(g.count) });', fn)
        self.assertIn('meta += " · " + g.years.join(", ");', fn)

    def test_render_firms_hides_block_when_zero_groups(self):
        fn = WebInspectorRenderTest._slice_top_level_fn(self.js_src, "renderFirms")
        self.assertIn("if (!groups.length) { firmBlockEl.hidden = true; return; }", fn)
        self.assertIn("firmBlockEl.hidden = false;", fn)
        self.assertIn("buildFirmGroups(documents)", fn)
        self.assertIn(
            "groups.forEach(function (g) { firmEl.appendChild(buildFirmRow(g)); });", fn)

    def test_renderall_calls_render_firms_between_repeats_and_years(self):
        fn = WebInspectorRenderTest._slice_top_level_fn(self.js_src, "renderAll")
        i_rep = fn.index("renderRepeats(")
        i_firm = fn.index("renderFirms(")
        i_year = fn.index("renderYears(")
        self.assertLess(i_rep, i_firm)
        self.assertLess(i_firm, i_year)

    def test_no_count_based_sort_for_firms(self):
        fn = WebInspectorRenderTest._slice_top_level_fn(self.js_src, "buildFirmGroups")
        self.assertIn("localeCompare", fn)
        for pattern in ("a.count - b.count", "b.count - a.count", ".count - "):
            self.assertNotIn(pattern, fn, f"제조소 목록에 건수 기준 정렬 패턴 발견: {pattern}")


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
            render.render_site(SINGLE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
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
            render.render_site(SINGLE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
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
        self.assertIn(
            "'<i class=\"ti ti-bookmark\" aria-hidden=\"true\"></i>' + _t(\"마이페이지\") + '</a>'",
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
        render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
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

    def test_hero_issuecard_binds_latest_brief(self):
        # 히어로 이슈카드 호별 수치가 최신호(06-29) 파생값 반영(stale 아님).
        # 11차 정리로 콜아웃(#this-week)은 철거 — 랜딩의 수치 바인딩은 이슈카드가 유일하다.
        older = _minimal_brief("2026-06-22")
        latest = _minimal_brief("2026-06-29",
                                coverage={"intake_total": 99, "rendered": 88,
                                          "evidence": {"A": 7, "B": 5, "C": 0}})
        out = self._render_site([older, latest])
        landing = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("수집 <b>99</b>", landing)
        self.assertIn("카드 88장", landing)
        self.assertIn("A 7 · B 5", landing)            # §1-6 표기 일관성: '/' → '·'
        self.assertNotIn("수집 36건", landing)         # 옛 정적 수치 잔존 금지

    def test_duplicate_publish_date_rejected(self):
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        for name in ("aaa", "zzz"):  # 같은 publish_date, 다른 파일 → slug 충돌
            (data / f"{name}.json").write_text(
                json.dumps(_minimal_brief("2026-06-01"), ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(SystemExit):
            render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)

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

    def test_engage_banner_is_gated_and_non_intrusive(self):
        """[성장 4차] 하단 참여 배너 — 같은 env 게이트 · 전면 모달이 아닐 것.

        구독 밴드는 base.html 구조상 읽기 종료 지점에 있어 끝까지 스크롤하지 않는 방문자
        에게는 한 번도 보이지 않는다. 그 사각을 메우는 배너인데, **전면 팝업이 되면 구글의
        침입형 인터스티셜 페널티**를 받아 지금 올리는 중인 순위와 정면 충돌한다. 그래서
        형태(하단 고정·화면 일부)와 노출 조건(즉시 아님)을 계약으로 잠근다.
        """
        a0 = render.NEWSLETTER_FORM_ACTION
        try:
            render.NEWSLETTER_FORM_ACTION = ""
            h_off = self._render_detail(_minimal_brief("2026-06-11"))
            render.NEWSLETTER_FORM_ACTION = "https://newsletter.example.com/subscribe"
            h_on = self._render_detail(_minimal_brief("2026-06-12"))
        finally:
            render.NEWSLETTER_FORM_ACTION = a0
        # 게이트 — off 면 흔적 0(전 페이지 골든 byte-diff 0 의 근거).
        self.assertNotIn('id="grm-cta"', h_off)
        self.assertNotIn("grm-cta-form", h_off)
        self.assertIn('id="grm-cta"', h_on)

        # 배너 블록 = 스코프 <style> 시작부터 그 뒤 첫 </script> 까지(스타일도 계약 대상).
        banner = h_on[h_on.index(".grm-cta{"):]
        banner = banner[:banner.index("</script>") + 9]
        # 형태: 하단 고정 배너지 화면을 덮는 모달이 아니다.
        self.assertIn("position:fixed", banner)
        self.assertIn("bottom:0", banner)
        for bad in ("position:fixed;top:0", "height:100vh", "width:100vw",
                    "backdrop", "role=\"dialog\"", "aria-modal"):
            self.assertNotIn(bad, banner, f"전면 모달 신호가 들어왔다: {bad}")
        # 노출 조건: 즉시 뜨지 않는다(스크롤 깊이 또는 지연) + 닫기 두 종류가 있다.
        self.assertIn("scroll", banner)
        self.assertIn("0.55", banner)
        self.assertIn('id="grm-cta-today"', banner)     # 오늘 하루 보지 않기
        self.assertIn('id="grm-cta-close"', banner)     # 닫기
        self.assertIn("864e5", banner)                  # 24시간
        self.assertIn("grm-sub-ok", banner)             # 이미 구독한 사람에겐 안 뜬다
        # 폼 계약은 구독 밴드와 동일(Brevo 필드·허니팟·PII 추가 0·URLSearchParams 인코딩).
        self.assertIn('type="email" name="EMAIL"', banner)
        self.assertIn('name="email_address_check"', banner)
        self.assertIn('name="locale"', banner)
        self.assertIn("URLSearchParams", banner)
        for bad in ('type="password"', 'name="password"', 'name="name"'):
            self.assertNotIn(bad, banner, f"배너가 추가 PII 를 받는다: {bad}")
        # 추적 0 — 외부 호스트로 나가는 것은 구독 endpoint 하나뿐.
        import re as _re
        hosts = set(_re.findall(r"https?://([^/\"'\s]+)", banner))
        self.assertLessEqual(hosts, {"newsletter.example.com"},
                             f"배너에 외부 호스트가 늘었다: {hosts}")
        # §4 한글 안전.
        self.assertNotIn("letter-spacing", banner)
        self.assertNotIn("text-transform", banner)

    def test_subscribe_funnel_metrics_wired_and_gated(self):
        """[성장 5차] 구독 깔때기 계측 — 노출/제출/닫힘 카운터.

        "전환 0" 의 원인 분해(노출이 없나·제출이 없나)가 목적. 무PII 계약: 키 문자열
        하나만 보내는 RPC(funnel_bump) 호출이 전부이고 폼 값(이메일)은 절대 싣지 않는다.
        기존 밴드·배너 스크립트는 무수정 — 바깥 리스너/옵저버로만 관찰한다. reactions
        cfg 미설정이면 런타임 전체 no-op, newsletter 게이트 off 면 출력 자체가 0
        (전 페이지 골든 byte-diff 0 의 근거). 운영자 브라우저는 RUM 게이트(#763)와
        같은 'grm-op' 플래그로 bump 전송 직전에 제외한다 — 판정은 fetch 보다 앞.
        프로덕션 호스트 밖(프리뷰 *.pages.dev·localhost)도 제외 — 배포 워크플로가 모든
        브랜치를 같은 repo var 로 프리뷰 배포해 프리뷰가 prod funnel_counts 를 올린다.
        grm-op 으로는 못 막는다(localStorage 는 origin 스코프).
        """
        a0 = render.NEWSLETTER_FORM_ACTION
        try:
            render.NEWSLETTER_FORM_ACTION = ""
            h_off = self._render_detail(_minimal_brief("2026-06-13"))
            render.NEWSLETTER_FORM_ACTION = "https://newsletter.example.com/subscribe"
            h_on = self._render_detail(_minimal_brief("2026-06-14"))
        finally:
            render.NEWSLETTER_FORM_ACTION = a0
        self.assertNotIn("funnel_bump", h_off)
        self.assertIn("rpc/funnel_bump", h_on)
        block = h_on[h_on.index("rpc/funnel_bump"):]
        block = block[:block.index("</script>")]
        for key in ("band_view", "band_submit", "cta_view", "cta_submit", "cta_dismiss"):
            self.assertIn(f"'{key}'", block, f"깔때기 키 {key} 배선 누락")
        # 무PII — 페이로드는 p_key 하나. 폼 값이 계측으로 새지 않는다.
        self.assertIn("JSON.stringify({p_key:key})", block)
        self.assertNotIn("EMAIL", block)
        # 노출 카운트는 1회 게이트(중복 노출 집계 방지) + 기존 스크립트 무수정 관찰.
        self.assertIn("IntersectionObserver", block)
        self.assertIn("MutationObserver", block)
        # 운영자 제외 — RUM 게이트(#763)와 같은 'grm-op' 플래그를 bump 전송 직전에 검사.
        # block 슬라이스는 fetch URL 에서 시작하므로 <script> 여는 태그까지 넓혀서 본다.
        fs = h_on.rindex("<script>", 0, h_on.index("rpc/funnel_bump"))
        fscript = h_on[fs:h_on.index("</script>", fs)]
        self.assertIn("localStorage.getItem('grm-op')==='1'", fscript,
                      "깔때기 bump 에 운영자(grm-op) 제외 게이트가 없다")
        self.assertLess(fscript.index("grm-op"), fscript.index("rpc/funnel_bump"),
                        "운영자 판정이 전송(fetch)보다 뒤다 — 카운트가 먼저 새 나간다")
        # 프로덕션 호스트 밖 제외 — 프리뷰(*.pages.dev)가 prod 카운터를 올리는 경로를 막는다.
        # RUM 게이트(#763)와 같은 site_host 단일원천을 쓰되, 판정은 이 블록 자신이 해야 한다
        # (RUM 게이트의 검사는 그쪽 IIFE 안이라 이 블록에 효력이 없다).
        self.assertIn("hn!=='grm-solutions.com'&&hn!=='www.grm-solutions.com'", fscript,
                      "깔때기에 프로덕션 호스트 게이트가 없다 — 프리뷰가 prod 카운터를 올린다")
        self.assertLess(fscript.index("hn!=='grm-solutions.com'"),
                        fscript.index("rpc/funnel_bump"),
                        "호스트 판정이 전송(fetch)보다 뒤다")


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
        render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
        return out

    def test_admin_console_env_gated(self):
        u0, k0 = render.SUPABASE_URL, render.SUPABASE_ANON_KEY
        try:
            render.SUPABASE_URL = ""
            render.SUPABASE_ANON_KEY = ""
            out_off = self._render_site("2026-06-01")
            self.assertFalse((out_off / "admin" / "index.html").exists())
            robots_off = (out_off / "robots.txt").read_text(encoding="utf-8")
            self.assertNotIn("Disallow: /admin/", robots_off)
            self.assertIn("Disallow: /cdn-cgi/", robots_off)

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
        self.assertIn('Disallow: /cdn-cgi/', robots)
        # RUM 게이트 — admin 페이지(base 상속)에도 실리고(경로 분기가 주입 0 + 플래그 세팅),
        # admin.js 는 이중 안전벨트로 같은 플래그를 세운다(WebCloudflareBeaconGateTest 상세).
        self.assertIn("static.cloudflareinsights.com/beacon.min.js", h)
        self.assertIn('localStorage.setItem("grm-op", "1")', admin_js)


# ── Cloudflare Web Analytics(RUM) 비콘 — 운영자 세션 제외 게이트 ─────────────────
class WebCloudflareBeaconGateTest(unittest.TestCase):
    """RUM 비콘은 base.html 인라인 게이트가 동적 주입한다(2026-08-19 — 엣지 Automatic
    setup 대체. 자동 주입은 분기가 불가능해 운영자 방문까지 전부 집계됐다). 계약:
      1) 게이트는 env 게이트 밖(무조건) — env-off 기본 빌드(=골든)의 전 페이지에 탑재.
      2) 판정(grm-op 플래그 · /admin 경로 · 프로덕션 호스트)이 주입 코드보다 앞.
      3) 주입은 동적(createElement)만 — 정적 <script src=…beacon.min.js> 태그는 게이트를
         우회하므로 금지.
      4) admin.js 도 grm-op 를 세운다 — 운영자는 admin 을 반드시 지나므로 첫 admin 방문
         이후 그 브라우저는 영구 제외.
    §9 네트워크 API 금지 가드는 pet.js·growth.js 파일 한정 스코프라 이 게이트(base.html
    인라인 · fetch/sendBeacon 미사용 · 스크립트 요소 삽입뿐)에는 애초에 걸리지 않는다 —
    허용목록 조정 불요(2026-08-19 스코프 확인)."""

    # Cloudflare WA site token(공개값 — 전 HTML 노출). ★siteTag(대시보드 URL 의
    # aa495e04…)가 아니다 — 비콘에 siteTag 를 실으면 데이터가 다른 사이트로 흘러 기존
    # 타임라인과 끊긴다. 이 값은 엣지 자동 주입이 쓰던 토큰의 실측(2026-08-19 DOM).
    TOKEN = "b6f8cfa4058b4cfd864d743f79a5e05e"

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_cfbeacon_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.admin_js = (WEB_DIR / "assets" / "admin.js").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_gate_on_every_page_even_env_off(self):
        # 뉴스레터/reactions 게이트와 달리 무조건 출력 — env-off 빌드 전 HTML 에 실린다.
        pages = sorted(self.single.rglob("*.html"))
        self.assertTrue(pages, "빌드 산출 HTML 0건 — 빌드가 무너졌다")
        for p in pages:
            html = p.read_text(encoding="utf-8")
            with self.subTest(page=p.relative_to(self.single).as_posix()):
                self.assertIn("static.cloudflareinsights.com/beacon.min.js", html)
                self.assertIn(f'"token": "{self.TOKEN}"', html)

    def test_gate_decides_before_injecting(self):
        # 판정 3종이 전부 주입 URL 보다 앞 — 게이트를 주입 뒤로 옮기면(=무력화) 여기서 red.
        inject = self.landing.index("static.cloudflareinsights.com/beacon.min.js")
        self.assertLess(self.landing.index("if(admin||op)return;"), inject)
        self.assertLess(
            self.landing.index("if(h!=='grm-solutions.com'&&h!=='www.grm-solutions.com')return;"),
            inject)
        self.assertLess(self.landing.index("p==='/admin'||p.indexOf('/admin/')===0"), inject)
        # 운영자 판정 재료 — 플래그 조회 + /admin 방문 즉시 세팅(첫 admin 방문도 비집계).
        self.assertIn("localStorage.getItem('grm-op')==='1'", self.landing)
        self.assertIn("localStorage.setItem('grm-op','1')", self.landing)

    def test_beacon_injection_is_dynamic_only(self):
        # 정적 태그(엣지 자동 주입과 같은 형태)로 "단순화"하면 게이트가 통째로 우회된다 —
        # <script …cloudflareinsights…> 여는 태그 자체가 금지다(따옴표·속성 순서 불문).
        self.assertIsNone(re.search(r"<script[^>]*cloudflareinsights", self.landing))
        self.assertIn("document.createElement('script')", self.landing)

    def test_admin_js_sets_operator_flag(self):
        # 이중 안전벨트 — 게이트(/admin 분기)와 admin.js 양쪽이 같은 플래그를 세운다.
        self.assertIn('localStorage.setItem("grm-op", "1")', self.admin_js)


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

    def test_every_registry_entry_declares_its_display_copy(self):
        """표시 카피는 전부 registry 소유 — 새 카탈로그가 키를 빠뜨린 채 합류하지 못하게.

        특히 short(변경 알림 태그)는 값이 없어도 렌더가 죽지 않아 조용히 빈 칩이 될 수
        있다 — 여기서 강제한다."""
        seen: set[str] = set()
        for e in render.LIBRARY_REGISTRY:
            for key in ("slug", "short", "file", "unit", "kick", "title",
                        "blurb", "intro", "desc"):
                self.assertTrue(str(e.get(key) or "").strip(),
                                f"{e.get('slug')}: registry 키 누락/빈값 — {key}")
            source = e["file"].rsplit(".", 1)[0]
            self.assertNotIn(source, seen, f"카탈로그 파일 중복: {e['file']}")
            seen.add(source)

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


class WebLibraryUpdateTest(unittest.TestCase):
    """[자료실 변경 알림 2026-07-25] 주간 자동 갱신이 무엇을 바꿨는지 두 화면에 표시.

    이력 파일(web/data/library_updates.json)은 **id 와 개수만** 갖고, 표시 제목·링크는
    렌더 시점에 라이브 카탈로그에서 join 된다. 여기선 그 join·개수 정합·정직성 계약
    (해소 안 되는 id 는 세지 않는다 / 표시 상한 초과는 '외 N건'으로 드러낸다)을 고정한다.
    커밋된 이력이 비어 있어도 깨지지 않아야 하므로 합성 이력으로 빌드한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_libupd_"))
        cls.catalogs = render.load_library()
        cls.by_source = {v["source"]: v for v in cls.catalogs}
        # 실제 카탈로그에서 앞선 항목 id 를 빌려 합성 이력을 만든다(존재하는 id = join 성공).
        ema = [it["id"] for it in cls.by_source["ema"]["items_by_id"].values()]
        pmda = [it["id"] for it in cls.by_source["pmda"]["items_by_id"].values()]
        cls.entry = {
            "date": "2026-07-27",
            "sources": {
                "ema": {"new_ids": ema[:8], "changed_ids": [], "removed_ids": [],
                        "total_count": len(ema), "truncated": False},
                "pmda": {"new_ids": pmda[:1], "changed_ids": pmda[1:2],
                         "removed_ids": [], "total_count": len(pmda), "truncated": False},
                # 카탈로그에 없는 id 만 든 소스 — 화면에서 통째로 빠져야 한다.
                "who": {"new_ids": ["who-does-not-exist"], "changed_ids": [],
                        "removed_ids": [], "total_count": 27, "truncated": False},
            },
        }
        cls.history = cls._tmp / "library_updates.json"
        cls.history.write_text(json.dumps(
            {"schema_version": "grm-library-updates/v1",
             "entries": [cls.entry, {"date": "2026-07-20", "sources": {}}]},
            ensure_ascii=False), encoding="utf-8")
        cls._original = render.LIBRARY_UPDATES_FILE
        render.LIBRARY_UPDATES_FILE = cls.history
        cls.out = cls._tmp / "site"
        _build_single(cls.out)
        cls.archive = (cls.out / "archive" / "index.html").read_text(encoding="utf-8")
        cls.hub = (cls.out / "library" / "index.html").read_text(encoding="utf-8")
        cls.view = render.load_library_updates(cls.catalogs, cls.history)

    @classmethod
    def tearDownClass(cls):
        render.LIBRARY_UPDATES_FILE = cls._original
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_counts_only_ids_that_resolve_to_a_catalog_item(self):
        """카탈로그에 없는 id 는 링크를 만들 수 없다 — 세지도, 보여주지도 않는다."""
        for key in ("latest", "compact"):
            shorts = [s["short"] for s in self.view[key]["sources"]]
            self.assertNotIn("WHO", shorts, f"{key}: 해소 안 되는 소스가 남았다")
        for source in self.view["latest"]["sources"]:
            self.assertEqual(source["new_count"] + source["changed_count"],
                             len(source["items"]) + source["hidden_count"])

    def test_hub_lists_titles_grouped_by_catalog(self):
        self.assertIn('class="lib-upd', self.hub)
        self.assertIn("2026-07-27", self.hub)
        for source in self.view["latest"]["sources"]:
            self.assertIn(f'href="../library/{source["slug"]}/index.html"', self.hub)
            for item in source["items"]:
                self.assertIn(str(_esc(item["title"])), self.hub)

    def test_archive_strip_is_capped_and_admits_what_it_hid(self):
        """모아보기 스트립은 '정말 간단하게' — 상한을 넘긴 건수는 '외 N건'으로 드러낸다."""
        compact = self.view["compact"]
        shown = sum(len(s["items"]) for s in compact["sources"])
        self.assertLessEqual(shown, render.LIBRARY_UPDATE_ITEM_CAP_COMPACT)
        self.assertEqual(compact["hidden_count"], compact["change_count"] - shown)
        self.assertGreater(compact["hidden_count"], 0, "이 픽스처는 절삭이 나야 한다")
        self.assertIn(f'외 {compact["hidden_count"]}건', self.archive)
        self.assertIn('class="arc-lib', self.archive)
        self.assertIn('href="../library/index.html"', self.archive)

    def test_hub_shows_more_items_than_the_archive_strip(self):
        self.assertGreater(sum(len(s["items"]) for s in self.view["latest"]["sources"]),
                           sum(len(s["items"]) for s in self.view["compact"]["sources"]))

    def test_official_links_open_in_a_new_tab(self):
        for source in self.view["compact"]["sources"]:
            for item in source["items"]:
                self.assertIn(f'href="{item["url"]}" target="_blank" rel="noopener"',
                              self.archive)

    def test_latest_entry_wins_over_older_history(self):
        self.assertEqual(self.view["latest"]["date"], "2026-07-27")

    def test_empty_history_hides_the_archive_strip_and_says_so_on_the_hub(self):
        """이력이 없으면(첫 가동 전) 빈 상자를 그리지 않고, 허브는 '변경 없음'을 말한다."""
        empty = self._tmp / "empty.json"
        empty.write_text('{"schema_version": "grm-library-updates/v1", "entries": []}',
                         encoding="utf-8")
        render.LIBRARY_UPDATES_FILE = empty
        try:
            out = self._tmp / "site_empty"
            _build_single(out)
        finally:
            render.LIBRARY_UPDATES_FILE = self.history
        archive = (out / "archive" / "index.html").read_text(encoding="utf-8")
        hub = (out / "library" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('<aside class="arc-lib', archive)
        self.assertIn("최근 변경 없음", hub)

    def test_missing_history_file_does_not_break_the_build(self):
        render.LIBRARY_UPDATES_FILE = self._tmp / "absent.json"
        try:
            out = self._tmp / "site_absent"
            _build_single(out)
        finally:
            render.LIBRARY_UPDATES_FILE = self.history
        self.assertTrue((out / "archive" / "index.html").is_file())
        self.assertTrue((out / "library" / "index.html").is_file())

    def test_committed_history_is_wired_to_the_live_catalogs(self):
        """저장소 이력 파일이 실제 렌더 경로에 물려 있는지(경로 오타 방지)."""
        self.assertTrue(str(self._original).endswith("library_updates.json"))
        self.assertEqual(self._original.parent, render.LIBRARY_DIR.parent)


class WebGuideLibraryCopyTest(unittest.TestCase):
    """[재발 방지 가드 2026-07-25] 이용안내의 자료실 설명이 카탈로그 증설을 못 따라가던 문제.

    실제로 자료실이 9개 카탈로그로 늘어난 뒤에도 이용안내는 "ICH 가이드라인 카탈로그,
    식약처 지침·고시"**2개만 열거**해 실제보다 축소해 설명하고 있었다(사용자 지적으로 발견).

    수리는 가드가 아니라 **구조**로 했다 — 이용안내에서 카탈로그 열거를 없애고 범위를
    일반 서술 + 자료실 첫 화면 유도로 바꿨다(허브는 registry 로 자동 생성되므로 늘어나도
    낡지 않는다). 이 테스트는 그 구조가 되돌아가는 것을 막는다: **열거하려면 전부** 하라."""

    @classmethod
    def setUpClass(cls):
        cls.guide = render.load_guide()
        cls.shorts = [e["short"] for e in render.LIBRARY_REGISTRY]

    def _library_lines(self) -> list[str]:
        return [ln for ln in (self.guide or "").splitlines()
                if ln.lstrip().startswith("- **자료실")]

    def test_guide_has_a_library_section(self):
        self.assertTrue(self._library_lines(), "이용안내에 자료실 설명 줄이 없다")

    def test_library_copy_names_either_no_catalog_or_all_of_them(self):
        """일부만 열거하면 카탈로그가 늘 때마다 조용히 낡는다 — 0개 아니면 전부."""
        blob = "\n".join(self._library_lines())
        named = [s for s in self.shorts if s in blob]
        self.assertIn(len(named), (0, len(self.shorts)),
                      f"자료실 설명이 카탈로그를 일부만 열거한다({named}) — "
                      "열거를 빼고 일반 서술로 두거나, 전부 적어야 한다")

    def test_library_copy_states_the_weekly_auto_refresh(self):
        """자동 갱신은 사용자가 알아야 할 동작이다(왜 목록이 저절로 늘어나는지)."""
        blob = "\n".join(self._library_lines())
        self.assertIn("자동", blob, "자료실 자동 갱신 설명이 없다")


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
        # 예전엔 `if r in ids:` 로 **존재하는 참조만** 검사해, orphan 참조(존재하지 않는
        # id)가 생겨도 그 반복만 조용히 건너뛰어 단언이 아예 실행되지 않았다 — 통과하는
        # 헛된 검사(항상 참). build_glossary_view 는 orphan 을 뷰에서 조용히 제외하므로
        # (§related 필터), 렌더 결과만 보면 orphan 이 생겨도 화면은 멀쩡해 보인다. 원래
        # 의도대로 뒤집는다: related 는 전부 실존 id 를 가리켜야 한다는 불변식을 먼저
        # 직접 단언하고(orphan 이 있으면 즉시 실패), 그 다음에만 렌더 앵커를 확인한다.
        ids = {t["id"] for t in self.terms}
        orphans = [(t["id"], r) for t in self.terms for r in t.get("related", []) if r not in ids]
        self.assertEqual(orphans, [],
                          f"related 가 존재하지 않는 용어 id 를 참조한다(고아 참조): {orphans}")
        for t in self.terms:
            for r in t.get("related", []):
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
        # 총 어휘 수 가드(교체 정합 — 9차 자율 런 G1). 용어를 **교체**할 때 실수로
        # 개수가 줄어드는 것을 잡는 장치라, 의도적 증설 때는 실측치로 갱신한다.
        # 200(v3) → 226: 2026-08-04 트랙③ 미국 FDA 법문 표현 중심 26어 추가.
        # 226 → 242: 2026-09-02 코퍼스(findings_docs.json) 최빈 미수록 표현 16어 추가
        # (sanitation·master-production-record·ongoing-stability-programme·job-description·
        # mix-up·training·certificate-of-analysis·returned-product·pressure-differential·
        # sporicidal-agent·time-limits-on-production·yield·reconciliation·equipment-use-log·
        # distribution-record·pest-control).
        self.assertEqual(len(self.terms), 242)

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
        # 필터 입력. 2026-09-03 부터 detail_ko 는 **일부 용어만** 보유한다(템플릿 복제 109건
        # 삭제) — render 결합식과 동일하게 "있을 때만" 붙여 파생 대조한다.
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


# [SERP 절단 예산] 구글 제목 절단은 픽셀 폭 기준이라 글자수로는 근사만 가능하다. 45 자를
# 경계로 두고, 약어가 없어 접을 수 없는 용어(`분석절차 밸리데이션(Analytical Procedure
# Validation)` 등)만 예산 안에서 허용한다. 실측 18 건에 여유 2 — 새 장문 제목이 늘면 발화한다.
_GLOSSARY_TITLE_MAX = 45
_GLOSSARY_TITLE_OVER_BUDGET = 20


class WebGlossaryTermPageTest(unittest.TestCase):
    """[용어사전 낱개 — 검색 유입 트랙] /glossary/{id}/ 용어당 1 페이지.

    색인 1페이지만 있으면 226 어가 URL 하나에 묶여 "OOS 뜻" 같은 실제 검색어에 걸릴
    대상이 없다. 이 클래스는 **정본(glossary.json)에서 파생해** 검사한다 — 용어 목록을
    손으로 적지 않는다(손목록은 반드시 낡는다). 정본에 용어가 추가되면 이 테스트가 그
    페이지의 부재를 자동으로 잡는다.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_glossterm_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")
        cls.root = cls.single / "glossary"
        # 사례 인용은 전 코퍼스를 훑어 비싸다 — 클래스 1회만 짓고 각 테스트가 재사용한다.
        cls.excerpts = render.build_glossary_case_excerpts(
            cls.terms, render.load_findings_docs(), render.load_glossary_cases())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _page(self, term_id: str) -> str:
        return (self.root / term_id / "index.html").read_text(encoding="utf-8")

    def test_every_term_has_a_page(self):
        missing = [t["id"] for t in self.terms
                   if not (self.root / t["id"] / "index.html").exists()]
        self.assertEqual(missing, [], f"용어 페이지 누락: {missing}")

    def test_no_orphan_pages(self):
        """정본에 없는 페이지가 남아 있으면 안 된다(삭제된 용어의 유령 페이지 차단)."""
        known = {t["id"] for t in self.terms}
        orphans = sorted(d.name for d in self.root.iterdir()
                         if d.is_dir() and d.name not in known)
        self.assertEqual(orphans, [], f"정본에 없는 용어 페이지: {orphans}")

    def test_headword_and_canonical_verbatim(self):
        from markupsafe import escape as _esc
        for t in self.terms:
            html = self._page(t["id"])
            self.assertIn(str(_esc(t["term_ko"])), html, f'표제어 누락: {t["id"]}')
            self.assertIn(f'{render.SITE_BASE_URL}/glossary/{t["id"]}/', html,
                          f'canonical 누락: {t["id"]}')

    def test_sitemap_lists_every_term(self):
        for t in self.terms:
            self.assertIn(
                f'<loc>{render.SITE_BASE_URL}/glossary/{t["id"]}/</loc>', self.sitemap,
                f'sitemap 미등록: {t["id"]}')

    def test_json_ld_is_defined_term(self):
        """검색엔진에 '사전 항목'임을 알리는 구조화데이터 — 파싱 가능한 JSON 이어야 한다."""
        for t in self.terms[:12]:            # 전수는 느리다 — 스키마 형태만 표본 검증
            node = json.loads(render.build_glossary_term_json_ld(t))
            self.assertEqual(node["@type"], "DefinedTerm")
            self.assertEqual(node["name"], t["term_ko"])
            self.assertEqual(node["inLanguage"], "ko")
            self.assertEqual(node["url"],
                             f'{render.SITE_BASE_URL}/glossary/{t["id"]}/')

    def test_description_derived_from_easy_ko(self):
        """description 은 생성하지 않고 정본을 자른다 — 상한 준수 + 접두 일치.

        raw glossary.json 입력에는 case_findings 가 없어 사례 접미사 경로가 발화하지
        않는다 — 이 테스트는 접미사 도입 전과 byte 동일한 기존 계약을 그대로 지킨다.
        """
        for t in self.terms:
            desc = render.glossary_term_description(t)
            self.assertTrue(desc, f'description 비어 있음: {t["id"]}')
            self.assertLessEqual(len(desc), render._GLOSSARY_META_MAX + 1,
                                 f'description 상한 초과: {t["id"]}')
            self.assertTrue(" ".join(t["easy_ko"].split()).startswith(desc[:20]),
                            f'description 이 정본 접두가 아님: {t["id"]}')
            self.assertNotIn("실제 지적사례", desc,
                             f'raw 입력에 사례 접미사가 붙음: {t["id"]}')

    def test_description_case_suffix_reaches_pages(self):
        """[SERP 차별화] 사례 접미사 — 뷰모델 경로에서 발화하고 **실제 페이지까지** 실린다.

        producer 만 검사하면 렌더 배선 누락이 침묵한다(#729 교훈: 그 경로를 지나는
        픽스처가 없어 거짓 문장이 3주 살았다) — 커밋 실측치(glossary_cases.json)로
        만든 뷰모델과, 그 뷰모델로 지은 페이지 HTML 을 함께 검사한다.
        """
        view = render.build_glossary_view(
            self.terms, None, render.load_glossary_cases())
        with_cases = [t for g in view["groups"] for t in g["terms"]
                      if t["case_findings"] > 0]
        self.assertGreater(len(with_cases), 0,
                           "사례 연결 용어 0건 — glossary_cases.json 적재 확인")
        for t in with_cases:
            desc = render.glossary_term_description(t)
            self.assertIn(f'실제 지적사례 {t["case_findings"]:,}건', desc,
                          f'사례 접미사 누락: {t["id"]}')
            self.assertLessEqual(len(desc), render._GLOSSARY_META_MAX + 1,
                                 f'접미사 포함 상한 초과: {t["id"]}')
            base = desc.split(" 실제 지적사례 ")[0]
            probe = base[:20].rstrip("…")
            self.assertTrue(" ".join(t["easy_ko"].split()).startswith(probe),
                            f'접미사가 정의부 접두 계약을 깼다: {t["id"]}')
        sample = with_cases[0]
        self.assertIn(
            f'실제 지적사례 {sample["case_findings"]:,}건',
            self._page(sample["id"]),
            f'렌더된 페이지에 접미사 미배선: {sample["id"]}')

    def test_title_keeps_query_token_inside_serp_cut(self):
        """[SERP 절단] 제목은 검색어 토큰이 잘려나가지 않을 길이여야 한다.

        초기 형식(`{한글}({영문 정식명}) 뜻 · GRM 규제 용어사전`)은 영문 정식명 안 약어가
        괄호로 들어간 용어에서 이중 괄호 + 장문이 돼(최대 94 자) 사용자가 실제로 친
        토큰("CAPA"·"뜻")이 구글 절단선 뒤로 밀렸다. 이 테스트는 그 형태로 되돌아가는 것을
        막는다 — 손목록이 아니라 **정본 전수**로 검사한다.
        """
        over, nested = [], []
        for t in self.terms:
            title = render.glossary_term_page_title(t)
            self.assertTrue(title.endswith(" 뜻 · GRM 용어사전"),
                            f'제목 꼬리 계약 위반: {t["id"]} → {title}')
            self.assertTrue(title.startswith(t["term_ko"]),
                            f'제목이 한글 표제어로 시작하지 않음: {t["id"]} → {title}')
            if len(title) > _GLOSSARY_TITLE_MAX:
                over.append((len(title), t["id"], title))
            if title.count("(") > 1:
                nested.append((t["id"], title))
        self.assertEqual(nested, [], f"제목 이중 괄호: {nested}")
        self.assertLessEqual(
            len(over), _GLOSSARY_TITLE_OVER_BUDGET,
            f"절단 위험 제목 {len(over)}건(허용 {_GLOSSARY_TITLE_OVER_BUDGET}): {over[:5]}")

    def test_title_short_en_prefers_acronym(self):
        """약어가 있으면 약어를, 일반어 괄호는 배제 — 판정 규칙 자체를 고정한다."""
        cases = [
            ("시정 및 예방조치", "Corrective and Preventive Action (CAPA)", "CAPA"),
            ("규격이탈 결과", "Out-of-Specification (OOS) Result", "OOS"),
            ("무균공정 모의시험·배지충전", "Aseptic Process Simulation (APS, Media Fill)", "APS"),
            ("제조단위·배치·로트", "Batch (or Lot)", "Batch"),          # 일반어 괄호 → 제거
            ("총입자·비생균 입자 모니터링", "Total Particle (Non-viable) Monitoring",
             "Total Particle Monitoring"),                              # 괄호구만 제거
            ("품질부서", "Quality Unit(s)", "Quality Unit"),
            ("원료의약품", "Active Pharmaceutical Ingredient (API) / Drug Substance", "API"),
            ("가독성", "Legible", "Legible"),                            # 괄호 없음 → 원문
            ("품질관리부서(QCU)", "Quality Control Unit (QCU)", ""),     # 한글에 이미 괄호
            ("공조(공기처리)", "Air Handling", ""),
        ]
        for term_ko, term_en, expected in cases:
            self.assertEqual(render.glossary_title_en(term_ko, term_en), expected,
                             f"짧은 영문 판정 어긋남: {term_ko} / {term_en}")

    def test_title_reaches_rendered_page(self):
        """producer 만 맞고 렌더 배선이 빠지는 사고를 막는다(#729 교훈)."""
        from markupsafe import escape as _esc
        for t in self.terms[:8]:
            self.assertIn(f'<title>{_esc(render.glossary_term_page_title(t))}</title>',
                          self._page(t["id"]), f'제목 미배선: {t["id"]}')

    def test_case_excerpt_quote_contains_its_token(self):
        """[정직성 계약] 인용문에는 판정에 쓴 토큰이 **그대로 보여야** 한다.

        페이지가 하는 주장은 "이 용어가 등장한 지적사항"뿐이고, 그 근거는 독자가 인용문
        안에서 토큰을 직접 보는 것이다. 토큰이 안 보이는 인용문이 하나라도 실리면 페이지가
        검증 불가능한 주장을 하게 된다.
        """
        ex = self.excerpts
        self.assertGreater(len(ex), 0, "사례 인용이 한 용어도 안 붙었다(배선 확인)")
        for tid, items in ex.items():
            for c in items:
                self.assertIn(c["token"], c["quote"],
                              f'인용문에 토큰이 없다: {tid} / {c["token"]}')
                self.assertFalse(render._glossary_incidental(c["quote"], c["token"]),
                                 f'열거 안 우연 언급이 실렸다: {tid} / {c["quote"][:60]}')
                self.assertTrue(c["agency"] and c["published_date"] and c["doc_href"],
                                f'출처 메타 결손: {tid}')
                self.assertTrue(c["doc_href"].startswith("findings/doc/"),
                                f'문서 링크 형식 어긋남: {tid} / {c["doc_href"]}')

    def test_incidental_rule_is_pinned_by_fixtures(self):
        """[비순환 가드] 열거 배제 규칙 자체를 실제 문장으로 고정한다.

        `test_case_excerpt_quote_contains_its_token` 은 `_glossary_incidental` 로 판정하므로
        그 함수가 망가지면 **함께 망가져 조용히 통과한다**(변이 테스트로 확인). 규칙은 여기서
        고정 입력으로 잠근다 — 아래 문장들은 전부 실제 코퍼스에서 뽑은 것이다.
        """
        cases = [
            # 토큰이 열거(`제조, 가공, 포장 또는 보관`) 안에만 있다 → 우연한 언급
            ("귀사는 의약품의 제조, 가공, 포장 또는 보관에 사용되는 설비가 의도된 용도, 세척 및 "
             "유지관리를 용이하게 하도록 적절한 설계를 갖추도록 하지 못하였다(21 CFR 211.63).",
             "포장", True),
            ("확인, 함량, 품질 및 순도에 관한 수립된 규격에 부합하도록 보장하는 책임을 "
             "이행하지 못하였습니다(21 CFR 211.22).", "품질", True),
            # 열거가 아닌 위치에 있다 → 유효한 사례
            ("실사 중 조사관들은 '세척 상태'로 보관된 정제 포장용 비전용 설비의 제품 접촉 "
             "표면에서 정체불명의 백색 분말 잔류물을 관찰했습니다.", "포장", False),
            ("귀사 시설은 낮은 품질의 공기가 더 높은 품질의 공기 구역으로 유입될 수 있는 "
             "방식으로 설계되고 운영됩니다.", "품질", False),
            ("귀사는 무균 주사제 제조 중 바이알 파손의 재발을 방지하기 위한 효과적인 시정 및 "
             "예방조치(CAPA)를 시행하지 않았습니다.", "CAPA", False),
        ]
        for sent, tok, expected in cases:
            self.assertEqual(render._glossary_incidental(sent, tok), expected,
                             f"열거 판정 어긋남: «{tok}» {sent[:40]}…")

    def test_case_excerpts_are_not_duplicated_across_terms(self):
        """같은 문장이 여러 용어 페이지에 실리면 그건 중복 본문이다(순위에 역효과)."""
        ex = self.excerpts
        seen: dict[str, str] = {}
        for tid, items in ex.items():
            docs_here = [c["doc_href"] for c in items]
            self.assertEqual(len(docs_here), len(set(docs_here)),
                             f'한 용어가 같은 문서에서 2건 이상: {tid}')
            for c in items:
                prev = seen.get(c["quote"])
                self.assertIsNone(prev, f'인용문 중복: {tid} 와 {prev}')
                seen[c["quote"]] = tid
            self.assertGreaterEqual(len(items), render._GLOSSARY_CASE_MIN,
                                    f'최소 건수 미만인데 섹션이 생겼다: {tid}')
            self.assertLessEqual(len(items), render._GLOSSARY_CASE_MAX,
                                 f'상한 초과: {tid}')

    def test_case_excerpts_reach_pages_with_honest_wording(self):
        """[문구 규율] "등장한" 이라고만 쓴다 — "관한/에 대한 지적"으로 단정하지 않는다.

        업체명은 용어 페이지에 싣지 않는다(업체는 링크로 잇는 문서 페이지의 주제다).
        producer 만 검사하면 렌더 배선 누락이 침묵하므로 실제 HTML 로 확인한다(#729 교훈).
        """
        ex = self.excerpts
        docs = render.load_findings_docs() or {}
        firms = {d.get("firm_name") for d in docs.get("documents", []) if d.get("firm_name")}
        checked = 0
        for tid, items in list(ex.items())[:10]:
            html = self._page(tid)
            self.assertIn("이 용어가 등장한", html, f'문구 규율 위반/미배선: {tid}')
            for bad in ("이 용어에 관한 지적", "이 용어에 대한 지적"):
                self.assertNotIn(bad, html, f'단정 문구가 실렸다: {tid} / {bad}')
            for c in items:
                self.assertIn(str(_esc(c["quote"]))[:40], html, f'인용문 미배선: {tid}')
                checked += 1
            leaked = sorted(f for f in firms if f and f in html)
            self.assertEqual(leaked, [], f'용어 페이지에 업체명이 실렸다: {tid} / {leaked[:3]}')
        self.assertGreater(checked, 0, "인용문이 하나도 검사되지 않았다(배선 확인)")

    def test_case_excerpts_are_deterministic(self):
        a = render.build_glossary_case_excerpts(
            self.terms, render.load_findings_docs(), render.load_glossary_cases())
        b = render.build_glossary_case_excerpts(
            self.terms, render.load_findings_docs(), render.load_glossary_cases())
        self.assertEqual(a, b, "사례 선정이 비결정론")

    def test_case_section_absent_when_no_excerpts(self):
        """[음성 검사] 사례를 못 채운 용어에는 인용 블록이 아예 없어야 한다."""
        ex = self.excerpts
        empty = [t["id"] for t in self.terms if t["id"] not in ex]
        self.assertGreater(len(empty), 0, "전 용어가 사례를 채웠다면 이 가드는 무의미하다")
        for tid in empty[:8]:
            self.assertNotIn("이 용어가 등장한", self._page(tid),
                             f'사례 0건인데 인용 리드가 떴다: {tid}')

    def test_related_links_point_to_term_pages(self):
        """관련 용어는 색인 앵커(#id)가 아니라 낱개 페이지로 가야 한다(내부 링크 = 색인 경로)."""
        checked = 0
        for t in self.terms:
            for rid in t.get("related", []):
                if any(x["id"] == rid for x in self.terms):
                    self.assertIn(f'href="../../glossary/{rid}/"', self._page(t["id"]),
                                  f'{t["id"]} → {rid} 관련 링크가 낱개 페이지를 가리키지 않음')
                    checked += 1
        self.assertGreater(checked, 0, "관련 용어 링크가 하나도 검사되지 않았다(배선 확인)")

    def test_render_is_deterministic(self):
        out2 = self._tmp / "single2"
        _build_single(out2)
        sample = self.terms[0]["id"]
        self.assertEqual((self.root / sample / "index.html").read_bytes(),
                         (out2 / "glossary" / sample / "index.html").read_bytes(),
                         "비결정론 렌더")


class WebRssFeedTest(unittest.TestCase):
    """[검색 유입] RSS 피드 — 네이버 서치어드바이저가 사이트맵과 **별개 채널로** 받는다.

    사이트맵이 "우리 페이지 전부"라면 RSS 는 "새로 나온 것"이다. 주간 브리프가 정확히 그
    성격이라 피드 내용은 브리프로 한정한다(지적사항 문서는 시간순 발행물이 아니다).
    피드 리더·사내 그룹웨어 위젯에도 그대로 쓰인다.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_rss_"))
        cls.out = cls._tmp / "single"
        _build_single(cls.out)
        cls.xml = (cls.out / "rss.xml").read_bytes().decode("utf-8")
        cls.briefs = render.load_briefs(SINGLE_FIXTURES)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_is_well_formed_xml(self):
        """한국어 산문이 들어가므로 이스케이프가 틀리면 피드 전체가 깨진다."""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.xml)
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.get("version"), "2.0")

    def test_one_item_per_brief(self):
        import xml.etree.ElementTree as ET
        items = ET.fromstring(self.xml).findall("./channel/item")
        self.assertEqual(len(items), len(self.briefs))

    def test_items_are_newest_first_and_link_to_real_pages(self):
        import xml.etree.ElementTree as ET
        items = ET.fromstring(self.xml).findall("./channel/item")
        dates = [i.findtext("pubDate") for i in items]
        self.assertEqual(dates, sorted(dates, key=_rfc822_key, reverse=True),
                         "최신순이 아니다")
        for i in items:
            link = i.findtext("link")
            self.assertTrue(link.startswith(f"{render.SITE_BASE_URL}/briefs/"))
            slug = link.rstrip("/").rsplit("/", 1)[-1]
            self.assertTrue((self.out / "briefs" / slug / "index.html").exists(),
                            f"피드가 없는 페이지를 가리킨다: {link}")

    def test_pubdate_is_rfc822_with_correct_weekday(self):
        """★`strftime('%a')` 는 로케일을 타서 한국어 Windows 에서 '월' 이 나온다.

        사양이 정한 영어 약어 표를 쓰는지, 그리고 요일이 실제 날짜와 맞는지 본다.
        """
        import datetime as dt
        for b in self.briefs:
            pub = b["brief"]["publish_date"]
            got = render.rfc822_date(pub)
            self.assertRegex(
                got, r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} "
                     r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
                     r"\d{4} \d{2}:\d{2}:\d{2} \+0900$", pub)
            want_day = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat",
                        "Sun")[dt.date.fromisoformat(pub).weekday()]
            self.assertTrue(got.startswith(want_day + ","),
                            f"{pub} 요일이 틀렸다: {got}")

    def test_description_is_the_brief_tldr_verbatim(self):
        """설명문을 지어내지 않는다 — 그 호의 tldr 을 그대로 잇는다."""
        import xml.etree.ElementTree as ET
        by_date = {b["brief"]["publish_date"]: b["brief"] for b in self.briefs}
        for i in ET.fromstring(self.xml).findall("./channel/item"):
            slug = i.findtext("link").rstrip("/").rsplit("/", 1)[-1]
            for t in (by_date[slug].get("tldr") or []):
                self.assertIn(str(t).strip(), i.findtext("description"),
                              f"tldr 이 무변형으로 실리지 않음: {slug}")

    def test_autodiscovery_link_in_head(self):
        """피드 리더와 수집기가 이 선언으로 피드를 찾는다."""
        landing = (self.out / "index.html").read_bytes().decode("utf-8")
        self.assertIn('type="application/rss+xml"', landing)
        self.assertIn('href="/rss.xml"', landing)

    def test_not_in_sitemap(self):
        sitemap = (self.out / "sitemap.xml").read_text(encoding="utf-8")
        self.assertNotIn("/rss.xml</loc>", sitemap)

    def test_is_deterministic(self):
        out2 = self._tmp / "single2"
        _build_single(out2)
        self.assertEqual((self.out / "rss.xml").read_bytes(),
                         (out2 / "rss.xml").read_bytes(), "비결정론 렌더")


def _rfc822_key(s: str) -> tuple:
    import datetime as dt
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    parts = s.split()
    return (int(parts[3]), months.index(parts[2]) + 1, int(parts[1]))


class WebNotFoundPageTest(unittest.TestCase):
    """[검색 유입] 404 페이지 — Cloudflare Pages 는 `/404.html` 이 **있을 때만** 404 를 준다.

    ★없으면 매칭되지 않는 모든 경로에 루트 index.html 을 **200 으로** 돌려준다(soft 404).
    실측으로 `/findings/doc/zzz-does-not-exist/` 가 랜딩 페이지를 200 으로 주고 있었다.
    검색엔진에게 이것은 ①없는 페이지가 있다고 말하고 ②같은 랜딩 본문이 무한한 URL 로
    중복돼 있다고 말하는 것이라 색인 품질을 직접 깎는다. 문서 페이지 3천 장이 매주
    재생성돼 낡은 URL 이 계속 생기는 구조라 특히 중요하다.

    ★[다국어 6단계 2026-09-04] 그리고 이 페이지는 **자기 주소에서 뜨지 않는다** —
    Cloudflare 는 가장 가까운 `404.html` 의 본문을 **원래 요청된 주소에 그 자리로** 404
    상태와 함께 실어 준다. 그래서 브라우저의 base URL 은 `/404.html` 이 아니라 요청 URL
    이고, 상대경로는 그 기준으로 풀린다:
      `/findings/doc/zzz/` 에서 `href="library/"` → `/findings/doc/zzz/library/` → **또 404**
    라이브 실측에서 되돌림 카드·nav·footer **링크 30개가 전부** 그 상태였다. 그런데 이
    클래스의 옛 검사는 초록이었다 — 링크가 "있었고" 가리키는 파일도 dist 안에 있었기
    때문이다. **어디로 풀리는지**를 안 봤다. 그래서 지금은 "상대 참조가 하나도 남지
    않는다"를 본다 — 이 페이지에서 상대 참조는 그 자체로 깨진 링크다.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_404_"))
        cls.out = cls._tmp / "single"
        _build_single(cls.out)
        cls.html = (cls.out / "404.html").read_text(encoding="utf-8")
        cls.en_html = (cls.out / "en" / "404.html").read_text(encoding="utf-8")
        cls.both = (("404.html", cls.html), ("en/404.html", cls.en_html))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_file_exists_at_each_tree_root(self):
        """경로가 정확히 `/404.html`·`/en/404.html` 이어야 Cloudflare 가 인식한다."""
        self.assertTrue((self.out / "404.html").exists(), "404.html 누락")
        self.assertTrue((self.out / "en" / "404.html").exists(), "en/404.html 누락")

    def test_is_noindex_without_language_pairing(self):
        """홈과 같은 주소로 그리지만 홈이 아니다 — 색인·언어 짝을 물려받으면 안 된다."""
        for name, html in self.both:
            self.assertIn('<meta name="robots" content="noindex">', html, name)
            self.assertNotIn('rel="alternate" hreflang=', html, name)
            self.assertNotIn('class="grm-lang"', html, name)

    def test_not_in_sitemap(self):
        sitemap = (self.out / "sitemap.xml").read_text(encoding="utf-8")
        for path in ("/404.html", "/404", "/en/404.html"):
            self.assertNotIn(f"<loc>{render.SITE_BASE_URL}{path}</loc>", sitemap)

    def test_no_relative_reference_survives(self):
        """★이 페이지에서만은 상대경로가 **전부 오답**이다 — 하나도 남으면 안 된다.

        요청 URL 기준으로 풀리기 때문이다. 사이트 절대경로는 호스트를 박지 않으므로
        README 불변식 #4(상대경로·호스트 무관)의 근거는 그대로 만족한다.
        """
        for name, html in self.both:
            bad = [m.group(1) for m in re.finditer(r'(?:href|src)="([^"]*)"', html)
                   if not m.group(1).startswith(
                       ("http://", "https://", "/", "#", "mailto:", "data:"))]
            self.assertEqual(bad, [], f"{name}: 요청 URL 기준으로 풀려 죽는 참조 {bad[:8]}")

    def test_offers_ways_back(self):
        """막다른 길로 두지 않는다 — 주요 표면으로 되돌려 보낸다."""
        for href in ('href="/findings/"', 'href="/archive/"', 'href="/library/"',
                     'href="/glossary/"'):
            self.assertIn(href, self.html, f"복귀 링크 누락: {href}")
        for href in ('href="/en/findings/"', 'href="/en/library/"'):
            self.assertIn(href, self.en_html, f"영어판 복귀 링크 누락: {href}")

    def test_links_resolve_to_real_pages(self):
        """404 페이지의 링크가 또 404 면 안 된다 — 카드만이 아니라 nav·footer 까지 전수."""
        for name, html in self.both:
            missing = []
            for href in re.findall(r'href="(/[^"#?]*)"', html):
                rel = href.lstrip("/")
                if rel.endswith("/") or rel == "":
                    rel += "index.html"
                if rel.startswith("assets/"):
                    continue          # 자산 참조는 별도 가드(WebEnTreeTest)의 몫
                if not (self.out / rel).is_file():
                    missing.append(href)
            self.assertEqual(missing, [], f"{name}: 없는 곳으로 보낸다 {missing[:8]}")

    def test_english_404_is_english_and_stays_in_its_tree(self):
        self.assertIn('<html lang="en">', self.en_html)
        self.assertIn("Page not found", self.en_html)
        self.assertNotIn("찾으시는 페이지가 없습니다", self.en_html)
        cards = re.findall(r'<a class="nf-card" href="([^"]*)"', self.en_html)
        self.assertTrue(cards)
        for href in cards:
            self.assertTrue(href.startswith("/en/"),
                            f"영어 404 가 트리 밖으로 보낸다: {href}")

    def test_english_404_omits_routes_the_english_tree_does_not_have(self):
        """영어 트리에 없는 면(아카이브·용어사전)은 **카드 자체를 만들지 않는다**.

        손으로 적은 여섯 장을 두 트리에 똑같이 그리면 404 를 고치려다 404 를 여섯 개
        만든다. 목록은 `en_paths`(실제 산출 라우트)에서 파생되므로 라우트가 늘면 저절로
        따라온다 — 손목록은 낡는다.
        """
        cards = re.findall(r'<a class="nf-card" href="([^"]*)"', self.en_html)
        self.assertNotIn("/en/glossary/", cards)
        self.assertNotIn("/en/archive/", cards)   # 영문 브리프 0호 — 아카이브가 없다
        self.assertIn("/en/findings/", cards)
        self.assertEqual(len(re.findall(r'<a class="nf-card"', self.html)),
                         len(render.NOT_FOUND_CARDS), "한국어판은 여섯 장 그대로다")

    def test_card_filter_is_derived_from_built_routes(self):
        self.assertEqual(
            [c["path"] for c in render.not_found_cards(render._KO, "en",
                                                       {"", "findings/"})],
            ["findings/", ""])
        self.assertEqual([c["path"] for c in render.not_found_cards()],
                         [p for p, _t, _d in render.NOT_FOUND_CARDS])


class WebFindingsFacetPageTest(unittest.TestCase):
    """[검색 유입] /findings/{c|country|agency}/ 축 색인 + 항목 모음 페이지.

    `/findings/` 는 런타임 RPC 검색 앱이라 HTML 에 지적 본문이 없어 공개 24,797건이
    색인 대상 0개였다. 축별 정적 표면이 그 구멍을 메운다.

    이 클래스는 **정본(web/data/findings_facets.json)에서 파생해** 검사한다 — 축 값을
    손으로 적지 않는다. 데이터가 갱신돼 항목이 늘면 그 페이지의 부재를 자동으로 잡는다.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_facet_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.data = json.loads(render.FINDINGS_FACETS_FILE.read_text(encoding="utf-8"))
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _axes(self):
        for axis in self.data["axes"]:
            yield axis, render.FACET_AXES[axis["axis"]]

    def _page(self, path: str, slug: str) -> str:
        return (self.single / "findings" / path / slug / "index.html").read_text(
            encoding="utf-8")

    def test_schema_version_is_pinned(self):
        """모양이 바뀐 데이터를 옛 템플릿으로 렌더하면 빈 페이지가 라이브로 나간다."""
        self.assertEqual(self.data["schema_version"], "grm-findings-facets/v2")

    def test_every_item_has_a_page(self):
        missing = []
        for axis, meta in self._axes():
            for item in axis["items"]:
                p = self.single / "findings" / meta["path"] / item["slug"] / "index.html"
                if not p.exists():
                    missing.append(f'{axis["axis"]}/{item["slug"]}')
        self.assertEqual(missing, [], f"모음 페이지 누락: {missing}")

    def test_axis_index_exists_and_links_every_item(self):
        for axis, meta in self._axes():
            idx = (self.single / "findings" / meta["path"] / "index.html")
            self.assertTrue(idx.exists(), f'축 색인 누락: {meta["path"]}')
            html = idx.read_text(encoding="utf-8")
            for item in axis["items"]:
                self.assertIn(f'href="../../findings/{meta["path"]}/{item["slug"]}/"', html,
                              f'축 색인이 {item["slug"]} 를 링크하지 않음')

    def test_no_orphan_pages(self):
        for axis, meta in self._axes():
            known = {it["slug"] for it in axis["items"]}
            root = self.single / "findings" / meta["path"]
            orphans = sorted(d.name for d in root.iterdir()
                             if d.is_dir() and d.name not in known)
            self.assertEqual(orphans, [], f'정본에 없는 모음 페이지: {orphans}')

    def test_sitemap_lists_index_and_every_item(self):
        for axis, meta in self._axes():
            self.assertIn(
                f'<loc>{render.SITE_BASE_URL}/findings/{meta["path"]}/</loc>', self.sitemap)
            for item in axis["items"]:
                self.assertIn(
                    f'<loc>{render.SITE_BASE_URL}/findings/{meta["path"]}/{item["slug"]}/</loc>',
                    self.sitemap, f'sitemap 미등록: {item["slug"]}')

    def test_counts_and_labels_verbatim(self):
        from markupsafe import escape as _esc
        for axis, meta in self._axes():
            for item in axis["items"]:
                html = self._page(meta["path"], item["slug"])
                self.assertIn(str(_esc(item["label_ko"])), html)
                self.assertIn(f'{item["findings"]:,}', html,
                              f'건수 미표시: {item["slug"]}')
                self.assertIn(f'{render.SITE_BASE_URL}/findings/{meta["path"]}/{item["slug"]}/',
                              html, f'canonical 누락: {item["slug"]}')

    def test_top_firms_link_to_firm_profile(self):
        """[P1.5-1 2026-08-27] 축 페이지의 업체명은 업체 조회로 이어져야 한다.

        종전에는 스냅샷에 firm_key 가 없어 평문 <span> 이었고, 축 → 업체 → 문서 경로가
        여기서 끊겼다(로드맵의 '막다른 지점' 1번). RPC 는 키를 원래 주고 있었다 —
        refresh 스크립트가 버리고 있었을 뿐이다. 키 없는 항목은 종전대로 무링크
        (구 스냅샷 폴백 — 재생성 전에도 페이지가 살아야 한다)."""
        import urllib.parse as _u
        checked = 0
        for axis, meta in self._axes():
            for item in axis["items"]:
                firms = item.get("top_firms") or []
                if not firms:
                    continue
                html = self._page(meta["path"], item["slug"])
                for f in firms:
                    if not f.get("firm_key"):
                        continue
                    # Jinja urlencode 는 '/' 를 보존한다(쿼리 값에서 합법 —
                    # URLSearchParams 도 %2F 와 동일 해석). quote 기본 safe='/' 가 그 계약.
                    self.assertIn(
                        'findings/firm/?key=' + _u.quote(f["firm_key"], safe="/"),
                        html, item["slug"] + ": " + f["firm_name"])
                    checked += 1
        self.assertGreater(checked, 50, "표본이 너무 적다 — 스냅샷에 firm_key 가 없다")
        # 커밋된 스냅샷 자체도 전건 키를 가져야 한다(생성기 회귀 가드).
        total = sum(1 for a in self.data["axes"] for it in a["items"]
                    for f in it.get("top_firms") or [])
        keyed = sum(1 for a in self.data["axes"] for it in a["items"]
                    for f in it.get("top_firms") or [] if f.get("firm_key"))
        self.assertEqual(keyed, total, "refresh 가 firm_key 를 다시 버리기 시작했다")

    def test_firm_links_carry_nofollow(self):
        """동적 조회 페이지(?key=)는 크롤 예산에서 뺀다 — 문서 상세의 업체 프로파일
        간선과 같은 정책. 링크가 있는 페이지 하나에서 확인한다."""
        import re as _re
        for axis, meta in self._axes():
            for item in axis["items"]:
                if any(f.get("firm_key") for f in item.get("top_firms") or []):
                    html = self._page(meta["path"], item["slug"])
                    m = _re.search(r'<a href="[^"]*findings/firm/\?key=[^"]*"[^>]*>', html)
                    self.assertIsNotNone(m)
                    self.assertIn('rel="nofollow"', m.group(0))
                    return
        self.fail("firm_key 있는 축 항목이 하나도 없다")

    def test_sample_text_is_not_truncated(self):
        """지적 본문은 자르지 않는다 — 이 문장이 그 페이지의 색인 대상 본문이다.

        저장소는 원문 절단으로 두 번 데였다(deep_analysis·경고서한 조각). 길이 조절은
        표시층의 일이지 데이터·렌더의 일이 아니다.
        """
        from markupsafe import escape as _esc
        checked = 0
        for axis, meta in self._axes():
            for item in axis["items"]:
                html = self._page(meta["path"], item["slug"])
                for sample in item["samples"]:
                    text = (sample.get("text_ko") or "").strip()
                    if not text:
                        continue
                    self.assertIn(str(_esc(text)), html,
                                  f'본문이 무변형으로 실리지 않음: {sample["finding_id"]}')
                    checked += 1
        self.assertGreater(checked, 0, "사례 본문이 하나도 검사되지 않았다(배선 확인)")

    def test_excluded_items_are_disclosed(self):
        """상한을 조용히 걸면 '전부 다뤘다'로 읽힌다 — 뺀 것은 축 색인에 밝힌다."""
        for axis, meta in self._axes():
            if not axis["excluded"]:
                continue
            html = (self.single / "findings" / meta["path"] / "index.html").read_text(
                encoding="utf-8")
            self.assertIn("여기에 없는 것", html, f'{meta["path"]}: 제외 고지 누락')
            for ex in axis["excluded"]:
                if ex["key"]:
                    self.assertIn(ex["key"], html,
                                  f'제외 항목 미고지: {meta["path"]}/{ex["key"]}')

    def test_filter_deeplink_uses_findings_query_key(self):
        for axis, meta in self._axes():
            for item in axis["items"][:3]:
                html = self._page(meta["path"], item["slug"])
                self.assertIn(f'findings/index.html?{meta["query_key"]}=', html,
                              f'검색 딥링크 누락: {item["slug"]}')

    def test_render_is_deterministic(self):
        out2 = self._tmp / "single2"
        _build_single(out2)
        axis, meta = next(self._axes())
        slug = axis["items"][0]["slug"]
        self.assertEqual(
            (self.single / "findings" / meta["path"] / slug / "index.html").read_bytes(),
            (out2 / "findings" / meta["path"] / slug / "index.html").read_bytes(),
            "비결정론 렌더")


class WebFindingsComboPageTest(unittest.TestCase):
    """[검색 유입 2차] 분류 × 기관 조합 페이지 — /findings/c/{분류}/{기관}/.

    단일 축이 검색에서 실제로 이겼지만(2026-08-19 실측: 네이버 "무균공정 밸리데이션
    지적" → /findings/c/process-validation/ 웹문서 1위), 사람들이 치는 말은 대개 주제
    하나가 아니라 **기관 + 주제**다. 이 페이지들이 그 조합을 받는 표면이다.

    이 클래스도 **정본에서 파생해** 검사한다 — 조합 값을 손으로 적지 않는다. 조합이
    늘거나 줄면 그 페이지의 부재·잔존을 자동으로 잡는다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_combo_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.data = json.loads(render.FINDINGS_FACETS_FILE.read_text(encoding="utf-8"))
        cls.combos = (cls.data.get("combos") or {}).get("items") or []
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _rel(self, combo) -> str:
        return f"findings/c/{combo['category_slug']}/{combo['slug']}/"

    def _page(self, combo) -> str:
        return (self.single / self._rel(combo) / "index.html").read_text(encoding="utf-8")

    def test_combos_exist_at_all(self):
        """0건 가드 — 배선이 끊기면 조합이 조용히 사라진다(그러면 이 스위트 전체가
        아무것도 검사하지 않고 초록이 된다)."""
        self.assertGreater(len(self.combos), 0, "조합 정본이 비었다(배선 확인)")

    def test_every_combo_has_a_page(self):
        missing = [self._rel(c) for c in self.combos
                   if not (self.single / self._rel(c) / "index.html").exists()]
        self.assertEqual(missing, [], f"조합 페이지 누락: {missing}")

    def test_no_orphan_combo_pages(self):
        """정본에 없는 조합 디렉터리가 남아 있지 않다(분류 페이지 밑 하위 디렉터리)."""
        known = {(c["category_slug"], c["slug"]) for c in self.combos}
        cat_slugs = {c["category_slug"] for c in self.combos}
        orphans = []
        for cat in cat_slugs:
            root = self.single / "findings" / "c" / cat
            for d in root.iterdir():
                if d.is_dir() and (cat, d.name) not in known:
                    orphans.append(f"{cat}/{d.name}")
        self.assertEqual(sorted(orphans), [], f"정본에 없는 조합 페이지: {orphans}")

    def test_parent_category_page_links_every_combo(self):
        """★진입 간선 — sitemap 에만 있고 내부 링크가 없는 페이지는 색인되지 않는다.
        문서 3,202장이 실제로 그렇게 떠 있었다(#723). 조합의 부모는 분류 페이지뿐이라
        그 페이지의 링크가 유일한 경로다."""
        for combo in self.combos:
            parent = (self.single / "findings" / "c" / combo["category_slug"]
                      / "index.html").read_text(encoding="utf-8")
            self.assertIn(f'href="../../../{self._rel(combo)}"', parent,
                          f'분류 페이지가 조합을 링크하지 않음: {self._rel(combo)}')

    def test_every_combo_is_reachable_from_home(self):
        """★★고아 검사를 클러스터 안에서만 하면 섬 전체가 떠 있는 걸 못 본다.

        부모가 자식을 링크한다는 것(test_parent_category_page_links_every_combo)은
        그 부모가 홈에서 닿을 때만 뜻이 있다. 2026-08-12 에 문서 3,490장이 정확히
        그렇게 sitemap 전용 고립 섬이었다(#723·#725) — 클러스터 내부 링크는 완벽했다.
        그래서 **홈에서 BFS** 로 잰다."""
        import re as _re
        from collections import deque
        start = (self.single / "index.html").resolve()
        seen, q = {start}, deque([start])
        while q:
            cur = q.popleft()
            try:
                html = cur.read_text(encoding="utf-8")
            except OSError:
                continue
            for href in _re.findall(r'href="([^"#?]+)', html):
                if href.startswith(("http", "mailto:", "//")):
                    continue
                target = (cur.parent / href).resolve()
                if target.is_dir():
                    target = (target / "index.html").resolve()
                if target.suffix != ".html" or target in seen or not target.exists():
                    continue
                seen.add(target)
                q.append(target)
        unreached = [self._rel(c) for c in self.combos
                     if (self.single / self._rel(c) / "index.html").resolve() not in seen]
        self.assertEqual(unreached, [],
                         f"홈에서 닿지 않는 조합 페이지(고립 섬) {len(unreached)}장: "
                         f"{unreached[:5]}")

    def test_sitemap_lists_every_combo(self):
        for combo in self.combos:
            self.assertIn(f'<loc>{render.SITE_BASE_URL}/{self._rel(combo)}</loc>',
                          self.sitemap, f'sitemap 미등록: {self._rel(combo)}')

    def test_counts_and_labels_verbatim(self):
        from markupsafe import escape as _esc
        for combo in self.combos:
            html = self._page(combo)
            self.assertIn(str(_esc(combo["category_label_ko"])), html)
            self.assertIn(str(_esc(combo["agency_label_ko"])), html)
            self.assertIn(f'{combo["findings"]:,}', html)
            self.assertIn(f'{render.SITE_BASE_URL}/{self._rel(combo)}', html,
                          f'canonical 누락: {self._rel(combo)}')

    def test_sample_text_is_not_truncated(self):
        from markupsafe import escape as _esc
        checked = 0
        for combo in self.combos:
            html = self._page(combo)
            for sample in combo["samples"]:
                text = (sample.get("text_ko") or "").strip()
                if not text:
                    continue
                self.assertIn(str(_esc(text)), html,
                              f'본문이 무변형으로 실리지 않음: {sample["finding_id"]}')
                checked += 1
        self.assertGreater(checked, 0, "사례 본문이 하나도 검사되지 않았다(배선 확인)")

    def test_deeplink_carries_both_filters(self):
        """조합 페이지의 CTA 는 분류·기관 **둘 다** 건 검색으로 가야 한다 — 하나만
        걸면 화면이 제목보다 넓은 결과를 보여준다."""
        for combo in self.combos:
            html = self._page(combo)
            self.assertIn(f'findings/index.html?cat={combo["category_key"]}'
                          f'&amp;agency={combo["agency_key"]}', html,
                          f'조합 딥링크가 두 필터를 함께 걸지 않음: {self._rel(combo)}')

    def test_titles_are_unique_across_combos(self):
        """제목이 겹치면 검색 결과에서 서로 구분되지 않는다(문서 페이지에서 362장이
        실제로 그랬다)."""
        seen: dict[str, str] = {}
        for combo in self.combos:
            html = self._page(combo)
            title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
            self.assertNotIn(title, seen,
                             f'제목 중복: {self._rel(combo)} vs {seen.get(title)}')
            seen[title] = self._rel(combo)

    def test_no_combo_is_a_near_clone_of_its_parent(self):
        """★한 기관이 분류를 사실상 독점하면 조합 페이지는 부모의 복제본이 된다 —
        건수도 거의 같고 색인 대상 본문인 '최근 사례' 6건이 통째로 겹친다(2026-08-19
        실측: FDA × 공정밸리데이션 570/578 = 98.6%, 사례 6/6 동일). 같은 질의에 두
        페이지가 경쟁하면 검색엔진이 하나를 버리고, 중복 판정이면 둘 다 손해다.

        건수만 보면 정상이라 이 결함은 숫자로 안 드러난다 — 부모와의 **비율**로 잡는다."""
        parents = {i["slug"]: i for a in self.data["axes"] if a["axis"] == "category"
                   for i in a["items"]}
        offenders = []
        for combo in self.combos:
            parent = parents.get(combo["category_slug"])
            if not parent or not parent["findings"]:
                continue
            share = combo["findings"] / parent["findings"]
            if share >= 0.95:
                offenders.append(f'{self._rel(combo)} {share * 100:.1f}%')
        self.assertEqual(offenders, [],
                         f"부모 분류를 독점하는 조합 페이지(복제본): {offenders}")

    def test_excluded_combos_are_disclosed_in_data(self):
        """상한을 조용히 걸면 '전부 다뤘다'로 읽힌다 — 뺀 조합은 사유와 함께 정본에
        남긴다(축 색인의 '여기에 없는 것' 고지와 같은 규율)."""
        excluded = (self.data.get("combos") or {}).get("excluded")
        self.assertIsNotNone(excluded, "조합 제외 목록 자체가 없다")
        for ex in excluded:
            self.assertTrue(ex.get("reason"), f"제외 사유 누락: {ex}")

    def test_agency_mix_bar_is_omitted(self):
        """기관이 하나뿐인 페이지에 '어느 기관이 지적했나' 막대는 뜻이 없다."""
        for combo in self.combos[:5]:
            self.assertNotIn("어느 기관이 지적했나", self._page(combo))

    def test_no_particle_glued_to_agency_name(self):
        """★기관명에 한국어 조사(가/이)를 붙이지 않는다 — 조사는 앞말의 받침으로
        갈리는데 기관명엔 영문 약어(FDA·EMA·MHRA)가 섞여 규칙이 성립하지 않는다.
        지금은 5개 라벨이 우연히 전부 받침이 없어 '가'가 맞지만, 그 우연에 기대면
        받침 있는 기관이 편입되는 순간 전 페이지가 비문이 된다."""
        for combo in self.combos:
            html = self._page(combo)
            for bad in (f'{combo["agency_label_ko"]}가 ', f'{combo["agency_label_ko"]}이 '):
                self.assertNotIn(bad, html, f'기관명에 조사가 붙었다: {bad!r}')

    def test_render_is_deterministic(self):
        out2 = self._tmp / "single2"
        _build_single(out2)
        rel = self._rel(self.combos[0])
        self.assertEqual((self.single / rel / "index.html").read_bytes(),
                         (out2 / rel / "index.html").read_bytes(), "비결정론 렌더")


class WebSerpCtrTest(unittest.TestCase):
    """[B2 클릭률] 검색 결과에 우리가 무엇을 보여주는가.

    노출은 나는데(구글 발견 3,517) 클릭률이 1.3% 였다. 이 클래스가 지키는 것 둘:
      ① **URL 자리** — 문서·업체·모음 약 4천 장은 구조화 데이터가 하나도 없어 SERP 에
         날 슬러그(`/findings/doc/hc-insp-89240/`)가 그대로 보였다. 화면에는 이미
         빵부스러기가 있었으므로 마크업만 붙였다(제목 무관·순위 위험 0).
      ② **제목 잘림** — 문서 제목의 82%가 표시폭을 넘겼는데, 원인은 업체명이 아니라
         (중앙값 23폭) `Health Canada Inspection`(24폭·문서의 40%) 같은 긴 영문
         라벨이었다. 화면은 그대로 두고 제목에서만 사이트가 이미 쓰는 짧은 말로 바꾼다.
      ③ **보이는 제목의 변별력** — ②만으로는 부족했다. 잘린 게 브랜드 꼬리(` · GRM`)면
         손해가 아니고, 진짜 손해는 **절단선까지 보이는 글자가 다른 결과와 똑같을 때**다.
         실측 853장이 그랬다(최대 군집 30장). 종전 유일성 검사가 이걸 못 잡은 이유는
         그 검사가 **전체 문자열**을 보기 때문이다 — 문자열은 유일한데 화면은 같았다.
         그래서 아래 `test_visible_titles_are_distinct_in_serp` 를 둔다.
    """

    @classmethod
    def setUpClass(cls):
        if not render.load_findings_docs():
            raise unittest.SkipTest("findings_docs.json 미존재")
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_ctr_"))
        cls.out = cls._tmp / "single"
        _build_single(cls.out, doc_pages=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ── ① 빵부스러기 구조화 데이터 ───────────────────────────────────────────
    def test_breadcrumbs_on_every_static_findings_surface(self):
        groups = {
            "문서 상세": self.out / "findings" / "doc",
            "업체": self.out / "findings" / "firm",
            "분류 모음": self.out / "findings" / "c",
            "국가 모음": self.out / "findings" / "country",
            "기관 모음": self.out / "findings" / "agency",
        }
        for label, root in groups.items():
            pages = [p for p in root.glob("*/index.html")]
            self.assertTrue(pages, f"{label}: 페이지가 없다(전제 확인)")
            missing = [p.parent.name for p in pages
                       if "BreadcrumbList" not in p.read_text(encoding="utf-8")]
            self.assertEqual(missing[:5], [], f"{label}: 빵부스러기 없음 {len(missing)}장")

    def test_breadcrumb_matches_visible_trail(self):
        """구글 요건 — 마크업은 **화면에 보이는 것과 같은 순서·같은 이름**이어야 한다.

        지어낸 경로를 심으면 리치 결과가 거부되거나(최선) 잘못된 경로가 노출된다(최악).

        ★페이지 종류마다 따로 배선했으므로 **종류마다** 대조한다. 한 종류만 보면 나머지의
        어긋남을 못 잡는다 — 실제로 문서 페이지는 `/findings/` 를 "지적사항 검색"이라
        부르고 업체 페이지는 "지적사항"이라 부른다(화면이 그렇게 갈려 있고, 마크업은
        화면을 따라야 하므로 이 차이는 그대로 두는 것이 맞다)."""
        for label, sub, cls in (("문서", "doc", "fd-crumb"),
                                ("업체", "firm", "ff-crumb"),
                                ("분류 항목", "c", "fx-crumb")):
            with self.subTest(page=label):
                pages = sorted((self.out / "findings" / sub).glob("*/index.html"))
                self.assertTrue(pages, f"{label}: 페이지가 없다(전제 확인)")
                html = pages[0].read_text(encoding="utf-8")
                crumb = re.search(rf'<nav class="{cls}".*?</nav>', html, re.S).group(0)
                visible = [re.sub(r"<[^>]+>", "", x).strip()
                           for x in re.findall(
                               r"<a [^>]*>.*?</a>|<span>.*?</span>", crumb, re.S)]
                ld = json.loads(re.search(
                    r'<script type="application/ld\+json">(.*?)</script>',
                    html, re.S).group(1))
                marked = [i["name"] for i in ld["itemListElement"]]
                self.assertEqual(marked, visible)
                # 마지막(현재 페이지)은 자기 자신을 링크하지 않는다.
                self.assertNotIn("item", ld["itemListElement"][-1])
                self.assertEqual([i["position"] for i in ld["itemListElement"]],
                                 list(range(1, len(marked) + 1)))

    def test_breadcrumb_urls_are_absolute_and_real(self):
        p = sorted((self.out / "findings" / "firm").glob("*/index.html"))[0]
        ld = json.loads(re.search(
            r'<script type="application/ld\+json">(.*?)</script>',
            p.read_text(encoding="utf-8"), re.S).group(1))
        for node in ld["itemListElement"]:
            if "item" not in node:
                continue
            self.assertTrue(node["item"].startswith(render.SITE_BASE_URL + "/"), node)
            rel = node["item"][len(render.SITE_BASE_URL) + 1:]
            target = self.out / (rel + "index.html" if rel.endswith("/") or not rel
                                 else rel)
            self.assertTrue(target.exists(), f"빵부스러기가 없는 페이지를 가리킨다: {rel}")

    # ── ② 제목 폭 ────────────────────────────────────────────────────────────
    def test_title_width_helper_counts_hangul_as_double(self):
        self.assertEqual(render.serp_width("abc"), 3)
        self.assertEqual(render.serp_width("한글"), 4)

    def test_long_english_source_labels_are_shortened_in_title_only(self):
        """화면(h1)은 원문 라벨, 제목만 사이트가 이미 쓰는 짧은 말."""
        docs = render.load_findings_docs()["documents"]
        hc = next(d for d in docs if d.get("source") == "Health Canada Inspection")
        html = (self.out / "findings" / "doc" / hc["slug"] / "index.html").read_text(
            encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        self.assertIn("캐나다 실사", title)
        self.assertNotIn("Health Canada Inspection", title)
        h1 = re.search(r'<h1 class="fd-h1">(.*?)</h1>', html, re.S).group(1)
        self.assertIn("Health Canada Inspection", h1)  # 화면은 무변형

    def test_fda_search_terms_are_never_shortened(self):
        """'483'·'Warning Letter' 는 사람들이 검색창에 그대로 치는 말이라 줄이지 않는다."""
        for src in ("FDA 483", "FDA Warning Letter"):
            self.assertNotIn(src, render.TITLE_SOURCE_SHORT, src)

    def test_firm_name_is_trimmed_by_meaning_not_by_width(self):
        """실측이 반증한 가설 — 업체명 폭 절단은 하지 않는다(중앙값 23폭·문제가 아니었다).

        의미가 보존되는 트림 하나(상호 별칭)만 남긴다. 폭 상한을 되살리면 사람이 검색창에
        치는 바로 그 말이 잘리므로 이 음성 검사로 막는다."""
        self.assertEqual(render.title_firm_name("Front Door Pharmacy, LLC dba FDP"),
                         "Front Door Pharmacy, LLC")
        # 같은 뜻의 다른 표기(실측 8건)
        self.assertEqual(render.title_firm_name("OPS International Inc D/B/A Olympia"),
                         "OPS International Inc")
        self.assertEqual(render.title_firm_name("2179267 Ontario Ltd. O/A Britman"),
                         "2179267 Ontario Ltd.")
        long_name = "Stanley Specialty Pharmacy Compounding and Wellness Center"
        self.assertEqual(render.title_firm_name(long_name), long_name)  # 자르지 않는다
        self.assertNotIn("…", render.title_firm_name(long_name))

    def test_slash_in_firm_name_is_never_treated_as_bilingual_pair(self):
        """사선이 병기 구분자라는 보장이 없다 — 이름 자체에 든 경우가 실재한다.

        `A / B` 앞쪽만 취하는 트림을 후보로 쟀다가 뺐다: 전수 9건 중 1건이 아래 경우라
        실재하지 않는 회사명이 된다. 얻는 건 쌍둥이 4장뿐이라 값을 못 한다."""
        self.assertEqual(
            render.title_firm_name("Brookfield Medical / Surgical Supply, Inc."),
            "Brookfield Medical / Surgical Supply, Inc.")

    def test_titles_stay_unique_after_shortening(self):
        """제목을 짧게 만드는 과정이 유일성을 깨면 구글이 중복으로 떨어뜨린다.

        ★이 검사만으로는 부족하다 — 아래 `test_visible_titles_are_distinct_in_serp` 참조."""
        docs = render.load_findings_docs()["documents"]
        titles = render.build_doc_page_titles(docs)
        self.assertEqual(len(titles), len(docs))
        self.assertEqual(len(set(titles.values())), len(titles))

    def test_visible_titles_are_distinct_in_serp(self):
        """★검색 결과에서 **보이는 글자**가 다른 결과와 구별되는가.

        위 유일성 검사는 전체 문자열을 본다. 구글은 앞 60폭만 보여준다. 이 간극 때문에
        853장이 초록불 아래에서 같은 글자로 보이고 있었다 — 유일성 보강이 변별 요소를
        꼬리(= 잘리는 자리)에 붙였기 때문이다. 그래서 **자른 뒤**에 센다.

        0 을 요구하지는 않는다. 업체명 자체가 60폭을 먹는 문서가 실재하고(최장 59폭),
        FDA FOIA 일괄 공개분은 619장이 날짜 하나를 공유해 제목으로는 못 가른다.

        ★상한은 **비율**이다. 문서는 매주 늘어나는데 절대값을 박아 두면 결함이 아니라
        성장 때문에 빨간불이 된다. 실측 기준선 336/3,301 = 10.2%, 상한 15%.
        ★이 상한은 **큰 퇴행**만 잡는다(예: 배치 수리 이전의 853장 = 26%). 배치만 되돌린
        정도(436장 = 13.2%)는 아래 `test_publication_date_survives_the_truncation_line`
        가 훨씬 날카롭게 잡는다(86% → 68%). 두 검사가 같은 축을 겹쳐 재지 않게 나눠 둔다."""
        docs = render.load_findings_docs()["documents"]
        titles = render.build_doc_page_titles(docs)

        def visible(text: str) -> str:
            w = 0
            for i, ch in enumerate(text):
                w += render.serp_width(ch)
                if w > 60:
                    return text[:i]
            return text

        seen: dict[str, list[str]] = {}
        for slug, t in titles.items():
            seen.setdefault(visible(f"{t} · GRM"), []).append(slug)
        twins = {v: s for v, s in seen.items() if len(s) > 1}
        n = sum(len(s) for s in twins.values())
        worst = max(twins.values(), key=len, default=[])
        self.assertLessEqual(
            n, int(len(docs) * 0.15),
            f"보이는 제목이 겹치는 문서 {n}/{len(docs)}장(기준선 10.2%) — "
            f"최대 군집 {len(worst)}장 {worst[:3]}. "
            "변별 요소가 절단선 밖으로 밀렸는지 확인하라.")

    def test_median_doc_title_fits_serp(self):
        """중앙값이 표시폭 안에 들어와야 한다 — 개별 최장값은 유일성 보강 때문에 넘을 수 있다."""
        docs = render.load_findings_docs()["documents"]
        widths = sorted(render.serp_width(f"{t} · GRM")
                        for t in render.build_doc_page_titles(docs).values())
        self.assertLessEqual(widths[len(widths) // 2], 66,
                             "문서 제목 중앙값이 SERP 표시폭을 넘는다")

    def test_title_date_survives_the_truncation_line(self):
        """날짜는 같은 업체 문서들을 가르는 유일한 값이라 **보이는 자리**에 있어야 한다.

        종전 배치는 날짜를 "지적사항" 뒤에 뒀고, 그 8폭 때문에 3,301장 중 1,333장에서만
        날짜가 온전히 보였다. 자리를 맞바꾼 뒤 2,852장. 되돌리면 이 검사가 잡는다.

        ★[실사일 2026-08-27] 이 검사는 **내가 낡게 만들었다.** 원래 `published_date` 가
        제목에 보이는지로 쟀는데, 제목이 실사일을 쓰게 되면서 1,524장에서 공개일이
        제목에 없어졌다 — 결함이 아니라 설계 변경인데 검사만 남아 빨간불이 됐다
        (3,301 중 1,440 으로 떨어졌다). 지키려던 뜻은 "날짜가 절단선 안에 보이는가"이지
        "공개일이 보이는가"가 아니었으므로, **제목이 실제로 싣는 날짜**로 잰다.
        이 형태는 어느 날짜를 고르든 살아남고, 날짜가 뒤로 밀리는 변경은 그대로 잡는다.
        """
        docs = render.load_findings_docs()["documents"]
        titles = render.build_doc_page_titles(docs)
        by_slug = {d["slug"]: d for d in docs}

        def visible(text: str) -> str:
            w = 0
            for i, ch in enumerate(text):
                w += render.serp_width(ch)
                if w > 60:
                    return text[:i]
            return text

        shown = sum(1 for slug, t in titles.items()
                    if render.doc_display_date(by_slug[slug]) in visible(f"{t} · GRM"))
        self.assertGreaterEqual(
            shown, int(len(docs) * 0.78),
            f"제목이 싣는 날짜가 절단선 안에 보이는 문서 {shown}/{len(docs)}장 — "
            "제목에서 날짜가 뒤로 밀렸는지 확인하라.")


class WebFirmPageTest(unittest.TestCase):
    """[B1 색인 표면] /findings/firm/{slug}/ 업체 단위 정적 페이지.

    문서 페이지와 같은 이유로 **켠 채로** 짓는다 — 렌더 스위치 뒤에 사각지대를 만들지
    않는다. 핵심 검사는 셋이다: ①sitemap ↔ 파일 대조(유령 URL 금지) ②얇은 중복 방벽
    (문서 1건 업체는 페이지를 만들지 않는다 — 그 문서 상세와 내용이 겹친다) ③범위 고지
    (이 페이지는 상세 페이지가 있는 문서만 담으므로 그 사실을 반드시 적는다)."""

    @classmethod
    def setUpClass(cls):
        cls.data = render.load_findings_docs()
        if not cls.data:
            raise unittest.SkipTest("findings_docs.json 미존재")
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_firmpg_"))
        cls.out = cls._tmp / "single"
        _build_single(cls.out, doc_pages=True)
        cls.sitemap = (cls.out / "sitemap.xml").read_text(encoding="utf-8")
        cls.root = cls.out / "findings" / "firm"
        cls.by_firm = {}
        for d in cls.data["documents"]:
            k = d.get("firm_key") or ""
            if k:
                cls.by_firm.setdefault(k, []).append(d)
        cls.expected = {k: v for k, v in cls.by_firm.items() if len(v) >= 2}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _built_slugs(self):
        return {p.parent.name for p in self.root.glob("*/index.html")}

    def test_one_page_per_multi_document_firm(self):
        built = self._built_slugs()
        self.assertEqual(len(built), len(self.expected),
                         "문서 2건 이상 업체 수와 생성 페이지 수가 다르다")
        self.assertTrue(built, "업체 페이지가 하나도 만들어지지 않았다")

    def test_single_document_firms_get_no_page(self):
        """얇은 중복 방벽 — 문서 1건 업체 페이지는 그 문서 상세의 복제본이 된다."""
        singles = [k for k, v in self.by_firm.items() if len(v) == 1]
        self.assertTrue(singles, "전제 확인 필요: 1건짜리 업체가 없다")
        built = self._built_slugs()
        for k in singles[:50]:
            self.assertNotIn(render._firm_slug(k), built, k)

    def test_sitemap_matches_files(self):
        """sitemap 은 데이터에서, HTML 은 렌더에서 나온다 — 갈라지면 유령 URL 이 된다."""
        in_sitemap = set(re.findall(
            rf"<loc>{re.escape(render.SITE_BASE_URL)}/findings/firm/([^/<]+)/</loc>",
            self.sitemap))
        self.assertEqual(in_sitemap, self._built_slugs())

    def test_slug_is_deterministic_and_unique(self):
        slugs = [render._firm_slug(k) for k in self.expected]
        self.assertEqual(len(slugs), len(set(slugs)), "슬러그 충돌")
        for s in slugs:
            self.assertRegex(s, r"^[a-z0-9][a-z0-9-]*-[0-9a-f]{8}$", s)
        # 같은 키는 언제나 같은 슬러그(재실행 안정) — URL 이 흔들리면 색인이 리셋된다.
        for k in list(self.expected)[:20]:
            self.assertEqual(render._firm_slug(k), render._firm_slug(k))

    def test_page_states_its_scope_and_date_meaning(self):
        """부재 어휘 — 담긴 범위와 날짜의 뜻을 적지 않으면 이 페이지가 거짓말을 한다."""
        p = sorted(self.root.glob("*/index.html"))[0]
        html = p.read_text(encoding="utf-8")
        self.assertIn("담긴 범위", html)
        self.assertIn("상세 페이지가 있는 문서", html)
        # ★[2026-08-28] 종전에는 `"문서가 공개된 날"` 이라는 **단정 문장**을 요구했다.
        #   그 단정은 이제 거짓이다 — 캐나다 실사는 그 자리에 실사일이 들어가고(mig 069),
        #   게다가 이 페이지의 타임라인은 **행마다** '공개'/'실사 종료'를 적고 있어서
        #   머리글의 단정과 자기 화면이 어긋나 있었다.
        #   지키려던 뜻은 "날짜의 정체를 말한다"이지 "그 문장이 있다"가 아니었으므로,
        #   행 라벨이 실제로 붙어 있는지로 잰다 — 화면이 스스로 말하는 편이 더 정확하다.
        self.assertIn("행마다 그 날짜가 무엇인지", html)
        for label in ("공개", "실사"):
            self.assertIn(label, html, f"타임라인 행이 날짜 종류('{label}')를 적지 않는다")
        self.assertIn("이것은 그 시점의 기록입니다", html)  # 후속 시정 고지
        self.assertIn(self.data.get("measured_on", ""), html)

    def test_doc_page_links_static_when_it_exists_else_lookup(self):
        """문서 상세 → 업체. 정적 페이지가 있으면 팔로우, 없으면 조회(nofollow)."""
        multi = next(v[0] for v in self.by_firm.values() if len(v) >= 2)
        single = next(v[0] for v in self.by_firm.values() if len(v) == 1)
        m = (self.out / "findings" / "doc" / multi["slug"] / "index.html").read_text(encoding="utf-8")
        s = (self.out / "findings" / "doc" / single["slug"] / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"findings/firm/{render._firm_slug(multi['firm_key'])}/", m)
        self.assertNotIn("findings/firm/index.html?key=", m)
        self.assertIn("findings/firm/index.html?key=", s)
        self.assertIn('rel="nofollow"', s)

    def test_firm_page_is_not_a_clone_of_its_document_page(self):
        """조합 페이지 복제본 가드와 같은 취지 — 업체 페이지는 문서 상세보다 더 말해야 한다.

        문서 2건 이상만 만드는 이유가 여기 있다(2건이면 어느 한 문서 상세에도 없는
        '여러 실사에 걸친 구성'이 생긴다). 텍스트 유사도가 아니라 **구조로** 잰다."""
        slug = sorted(self._built_slugs())[0]
        html = (self.root / slug / "index.html").read_text(encoding="utf-8")
        rows = html.count('class="ff-doc"')
        self.assertGreaterEqual(rows, 2, "업체 페이지가 문서 1건만 담고 있다")
        # 각 문서 행은 그 문서 상세로 이어져야 한다(막다른 요약 금지).
        self.assertGreaterEqual(html.count("findings/doc/"), rows)

    def test_inspector_pages_are_not_created_by_this_track(self):
        """B1 은 업체만이다 — 사람에 대한 페이지는 정책이 다르다(037 · noindex 유지)."""
        self.assertFalse(list((self.out / "findings" / "inspector").glob("*/index.html")))
        self.assertNotIn(f"{render.SITE_BASE_URL}/findings/inspector/", self.sitemap)


class WebInspectionDateTest(unittest.TestCase):
    """[실사일 2026-08-27] 화면이 보여주는 날짜가 **문서가 다루는 날**인가.

    여태 쓰던 `published_date` 는 우리가 그 문서를 확보한 날이다. 대개는 며칠 차이라
    문제가 없었는데 FDA 483 에서 무너졌다 — FOIA 일괄 공개분 941건이 공개일
    `2024-01-17` 하나를 공유하고 그 실사는 2015~2019년이다(raw_signals 전수 평균 격차
    1,524일 · 최대 6,143일). 2015년 지적이 2024년 것으로 읽히는 건 실명 업체 페이지에서
    사실 왜곡이다.

    ★이 클래스는 **합성 문서**로 잰다. 커밋된 `findings_docs.json` 은 워크플로가 다시
    만들어야 실사일이 들어오는데(로컬에서 재생성 불가 — 러너만 자격증명을 갖는다),
    그때까지 렌더 계층이 검사 없이 남으면 '배선 없는 슬롯'이 된다. 순수 함수라 데이터
    파일 없이도 전부 잴 수 있다.
    """

    FDA_483 = {
        "slug": "fda483-1", "document_id": "fda483-1", "agency": "FDA",
        "source": "FDA 483", "firm_name": "Acme Pharma Inc.",
        "published_date": "2024-01-17", "inspection_date": "2015-07-10",
        "categories": ["무균보증/무균공정"],
        "findings": [{"x": 1}, {"x": 2}, {"x": 3}],
    }
    # 경고서한 — 실사 문서가 아니라 실사일이 없다(대상에서 뺐다).
    WL = {
        "slug": "wl-1", "document_id": "wl-1", "agency": "FDA",
        "source": "FDA Warning Letter", "firm_name": "Beta Labs LLC",
        "published_date": "2025-09-16", "inspection_date": "",
        "categories": ["품질부서 관리감독"],
        "findings": [{"x": 1}, {"x": 2}, {"x": 3}],
    }
    # 캐나다 실사 — 두 날짜가 **같다**. 원천이 공개일을 주지 않아 수집기가 실사 시작일을
    # published_date 에 넣기 때문이다(mig 069).
    HC = {
        "slug": "hc-1", "document_id": "hc-insp-1", "agency": "HC",
        "source": "Health Canada Inspection", "firm_name": "Maple Labs Ltd.",
        "published_date": "2021-01-13", "inspection_date": "2021-01-13",
        "categories": ["시험실/품질관리"],
        "findings": [{"x": 1}, {"x": 2}, {"x": 3}],
    }

    def test_display_date_prefers_the_day_the_document_is_about(self):
        self.assertEqual(render.doc_display_date(self.FDA_483), "2015-07-10")

    def test_display_date_falls_back_when_the_source_gives_none(self):
        """경고서한은 실사 문서가 아니라 실사일이 없다.

        부재를 지어내지 않고 종전 값(공개일)으로 물러선다 — 빈 날짜를 화면에 내보내는
        것이 가장 나쁘다."""
        self.assertEqual(render.doc_display_date(self.WL), "2025-09-16")
        no_key = {k: v for k, v in self.WL.items() if k != "inspection_date"}
        self.assertEqual(render.doc_display_date(no_key), "2025-09-16")

    def test_one_date_is_stated_once_and_called_what_it_is(self):
        """★두 값이 같으면 **날짜가 하나뿐**이라는 뜻이다 — 캐나다 실사가 그렇다.

        처음에는 "캐나다는 원천이 실사일을 안 준다"고 판단했는데 **틀렸다.** 원천은
        `inspectionStartDate` 를 전 행에 주고 수집기가 그걸 `published_date` 에 넣고
        있었다(종료일 `inspectionEndDate` 는 전 행 null). 즉 결손이 아니라 **표기**가
        틀린 것이었다 — 실사일에 "공개"라고 적고 있었다(문서 1,824건).

        같은 날짜를 두 번 적으면서 한쪽을 '공개'라고 부르면 그게 고치려던 거짓말이다.
        ★판정은 소스 이름이 아니라 **값**으로 한다 — 다른 소스에서 우연히 같아져도
        (실측 FDA 483 에 1건) 알아서 옳게 나온다."""
        d = render.doc_page_description(self.HC, {"HC": "캐나다 보건부"})
        self.assertIn("2021-01-13 실사에서 확인한", d)
        self.assertNotIn("공개", d)
        self.assertEqual(d.count("2021-01-13"), 1, "같은 날짜를 두 번 적었다")

    def test_date_axis_verb_is_derived_from_the_data(self):
        """연도별 목록의 '공개한/실사한'은 **기관 이름이 아니라 값**에서 나온다.

        손목록으로 기관을 분기하면 새 소스에서 조용히 낡는다."""
        self.assertEqual(render.date_axis_verb([self.HC, dict(self.HC, slug="hc-2")]),
                         "실사한")
        self.assertEqual(render.date_axis_verb([self.FDA_483]), "공개한")
        # 섞여 있으면 단정하지 않고 종전 표현을 쓴다.
        self.assertEqual(render.date_axis_verb([self.HC, self.FDA_483]), "공개한")
        self.assertEqual(render.date_axis_verb([]), "공개한")

    def test_title_uses_the_inspection_date(self):
        titles = render.build_doc_page_titles([self.FDA_483, self.HC])
        self.assertIn("(2015-07-10)", titles["fda483-1"])
        self.assertNotIn("2024-01-17", titles["fda483-1"])
        self.assertIn("(2021-01-13)", titles["hc-1"])

    def test_title_pays_no_width_for_this(self):
        """★제목에 "실사" 라벨을 붙이는 안은 **실측으로 기각**했다 — 폭 5 를 먹어 제목
        276장을 절단선 밖으로 밀었다. 검색 결과에서는 제목과 설명이 함께 보이므로 폭이
        비싼 제목 대신 설명에서 밝힌다. 라벨이 되살아나면 이 검사가 잡는다."""
        titles = render.build_doc_page_titles([self.FDA_483, self.HC])
        self.assertNotIn("실사", titles["fda483-1"])
        was = dict(self.FDA_483, inspection_date="")
        self.assertEqual(
            render.serp_width(render.build_doc_page_titles([was])["fda483-1"]),
            render.serp_width(titles["fda483-1"]),
            "실사일 표기가 제목 폭을 바꾸면 B2 에서 얻은 절단 개선을 깎는다")

    def test_description_says_which_date_is_which(self):
        """제목이 맨몸 날짜를 쓰므로 **설명이 그 날짜의 정체를 말해야** 한다.

        두 날짜가 다 나오고 각각 무엇인지 적혀 있어야 한다 — 하나만 적거나 라벨 없이
        나열하면 종전 결함(어느 날짜인지 모름)이 그대로 남는다."""
        d = render.doc_page_description(self.FDA_483, {"FDA": "미국 FDA"})
        self.assertIn("2015-07-10 실사", d)
        self.assertIn("2024-01-17 공개", d)
        self.assertLess(d.index("2015-07-10"), d.index("2024-01-17"),
                        "문서가 다루는 날이 앞에 와야 한다")

    def test_description_unchanged_when_there_is_no_inspection_date(self):
        """실사일이 없는 소스의 문구는 **한 글자도 건드리지 않는다**(불필요한 골든 churn)."""
        d = render.doc_page_description(self.WL, {"FDA": "미국 FDA"})
        self.assertEqual(
            d,
            "미국 FDA가 2025-09-16에 공개한 Beta Labs LLC FDA Warning Letter"
            " 지적사항 3건을 우리말로 정리했습니다. 주요 분류: 품질부서 관리감독.")

    def test_published_date_is_never_replaced_in_the_data(self):
        """실사일은 **새 축**이지 옛 축의 대체가 아니다 — dedup 키·수집 창·발행 축이
        전부 `published_date` 위에 서 있다. 표시만 고르고 값은 둘 다 남는다."""
        self.assertEqual(self.FDA_483["published_date"], "2024-01-17")
        d = render.doc_page_description(self.FDA_483, {"FDA": "미국 FDA"})
        self.assertIn("2024-01-17", d)

    def test_page_labels_both_dates_and_omits_the_empty_one(self):
        """화면 칩 — 뜻이 다른 두 날짜를 나란히 두므로 **종류를 반드시 적는다**
        (업체 이력 타임라인과 같은 규율). 없는 날짜는 칩 자체를 만들지 않는다."""
        tpl = (pathlib.Path(render.__file__).parent / "templates"
               / "findings_doc.html").read_text(encoding="utf-8")
        self.assertIn("{%- if doc.inspection_date %}", tpl,
                      "실사일 칩이 조건 없이 그려지면 빈 칩이 나간다")
        # [다국어 2단계 2026-09-04] 칩 문구는 문구 사전을 거친다(`_()`), 날짜는 슬롯이다 —
        # 검사 대상은 "칩마다 날짜 옆에 종류를 적는가"이므로 감싼 형태로 그대로 고정한다.
        self.assertIn('{{ _("<b>{date}</b> 실사", date=doc.inspection_date) }}</span>', tpl)
        self.assertIn('{{ _("<b>{date}</b> 공개", date=doc.published_date) }}</span>', tpl)
        # ★같은 날짜면 '공개' 칩을 만들지 않는다 — 캐나다 실사가 그렇다(mig 069).
        #   두 번 적으면서 한쪽을 '공개'라고 부르면 그게 고치려던 거짓말이다.
        self.assertIn("{%- if doc.published_date != doc.inspection_date %}", tpl,
                      "같은 날짜가 '실사'와 '공개'로 두 번 나간다")
        # 본문의 출처 문장도 같은 규칙을 따라야 한다(갈래마다 문장 전체가 하나의 키다).
        self.assertIn("{% if doc.published_date == doc.inspection_date %}", tpl)
        self.assertIn('_("{agency}가 <b>{date}</b>에 실사한 문서에서', tpl)
        self.assertIn('_("{agency}가 <b>{date}</b>에 공개한 문서에서', tpl)


class WebFindingsDocPageTest(unittest.TestCase):
    """[검색 유입] /findings/doc/{document_id}/ 문서 단위 페이지.

    ★스위트에서 **유일하게** 문서 페이지를 켠 채로 짓는 클래스다(다른 클래스는 속도 때문에
    끈다 — 51번 재빌드 × 27초). 그래서 여기서 전수로 본다: 켜고 끄는 스위치가 있는 한,
    실제 렌더 경로를 밟는 자리가 하나는 있어야 한다.

    ★특히 **sitemap ↔ 파일 대조**가 이 클래스의 핵심이다. sitemap 은 데이터에서 파생되고
    HTML 은 렌더에서 나오므로, 둘이 갈라지면 구글에 유령 URL 3천 개를 광고하게 된다.
    """

    @classmethod
    def setUpClass(cls):
        cls.data = render.load_findings_docs()
        if not cls.data:
            raise unittest.SkipTest("findings_docs.json 미존재")
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_docs_"))
        cls.out = cls._tmp / "single"
        _build_single(cls.out, doc_pages=True)
        cls.sitemap = (cls.out / "sitemap.xml").read_text(encoding="utf-8")
        cls.root = cls.out / "findings" / "doc"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "_tmp", pathlib.Path(tempfile.gettempdir())) /
                      "__never__", ignore_errors=True)
        if hasattr(cls, "_tmp"):
            shutil.rmtree(cls._tmp, ignore_errors=True)

    def _page(self, slug: str) -> str:
        # ★read_text 를 쓰면 안 된다 — 텍스트 모드가 CRLF 를 LF 로 바꿔버려, 본문에
        # CR 이 섞인 문서(실측 FDA 5건)에서 "무변형" 단언이 거짓으로 통과/실패한다.
        # 렌더가 실제로 쓴 바이트를 그대로 본다.
        return (self.root / slug / "index.html").read_bytes().decode("utf-8")

    def _sample_docs(self, per_agency: int = 12) -> list[dict]:
        """기관별로 고르게 뽑는다 — `documents[:40]` 은 문서 id 정렬 탓에 **전부 FDA** 였다.

        그 슬라이스로는 HC·MFDS 경로가 한 번도 검사되지 않는다(기관마다 source 문자열·
        업체명 표기·본문 형태가 다르다).
        """
        out, seen = [], {}
        for d in self.data["documents"]:
            n = seen.get(d["agency"], 0)
            if n < per_agency:
                seen[d["agency"]] = n + 1
                out.append(d)
        return out

    def test_default_renders_doc_pages(self):
        """기본값이 False 로 뒤집히면 배포가 유령 URL 만 광고한다 — 시그니처로 고정."""
        import inspect
        sig = inspect.signature(render.render_site)
        self.assertIs(sig.parameters["render_doc_pages"].default, True)

    def test_schema_version_is_pinned(self):
        self.assertEqual(self.data["schema_version"], "grm-findings-docs/v1")

    def test_every_document_has_a_page(self):
        missing = [d["slug"] for d in self.data["documents"]
                   if not (self.root / d["slug"] / "index.html").exists()]
        self.assertEqual(missing, [], f"문서 페이지 누락 {len(missing)}건: {missing[:5]}")

    def test_no_orphan_pages(self):
        known = {d["slug"] for d in self.data["documents"]}
        orphans = sorted(p.name for p in self.root.iterdir()
                         if p.is_dir() and p.name not in known)
        self.assertEqual(orphans, [], f"정본에 없는 문서 페이지: {orphans[:5]}")

    def test_sitemap_and_files_agree(self):
        """유령 URL 금지 — sitemap 에 있는 문서 URL 은 전부 실제 파일이어야 한다.

        ★[다국어 4단계 2026-09-04] 이제 두 언어 트리가 각자 문서 URL 을 낸다. 한국어는
        전량, 영어는 **원문이 영어인 문서만**(doc_is_english). 한 정규식으로 뭉뚱그리면
        영어분이 한국어 수를 부풀려 '정본과 다르다'로 오판한다 — 트리별로 센다."""
        import re as _re
        base = render.SITE_BASE_URL
        ko_urls = _re.findall(
            _re.escape(base) + r"/findings/doc/([^<]+?)/</loc>", self.sitemap)
        en_urls = _re.findall(
            _re.escape(base) + r"/en/findings/doc/([^<]+?)/</loc>", self.sitemap)
        self.assertEqual(len(ko_urls), len(self.data["documents"]),
                         "sitemap 한국어 문서 URL 수가 정본과 다르다")
        expected_en = {d["slug"] for d in self.data["documents"]
                       if render.doc_is_english(d)}
        self.assertEqual(set(en_urls), expected_en,
                         "sitemap 영어 문서 URL 이 '원문이 영어인 문서' 집합과 다르다")
        self.assertLess(len(en_urls), len(ko_urls),
                        "원문이 한국어인 문서가 하나도 안 걸러졌다(판정이 무력화됐다)")
        ghosts = [u for u in ko_urls if not (self.root / u / "index.html").exists()]
        self.assertEqual(ghosts, [], f"sitemap 에만 있는 유령 URL: {ghosts[:5]}")
        # self.root = <dist>/findings/doc → dist 루트는 두 단계 위.
        en_root = self.root.parent.parent / "en" / "findings" / "doc"
        en_ghosts = [u for u in en_urls if not (en_root / u / "index.html").exists()]
        self.assertEqual(en_ghosts, [], f"영어 유령 URL: {en_ghosts[:5]}")

    def test_english_pages_carry_the_regulator_wording(self):
        """[다국어 4단계 2026-09-04] 영어 문서 페이지의 본문은 **규제기관이 쓴 원문**이어야
        한다 — 한국어 번역을 영어 껍데기에 담지 않는다는 것이 그 트리의 존재 이유다.
        이 클래스는 문서 렌더를 켠 채로 짓는 유일한 자리라 여기서 실제 HTML 로 확인한다."""
        en_root = self.root.parent.parent / "en" / "findings" / "doc"
        en_docs = [d for d in self.data["documents"] if render.doc_is_english(d)]
        self.assertGreater(len(en_docs), 100, "영어로 낼 문서가 비정상적으로 적다")
        checked = 0
        for doc in en_docs[:30]:
            path = en_root / doc["slug"] / "index.html"
            self.assertTrue(path.is_file(), f"영어 문서 페이지 누락: {doc['slug']}")
            html = path.read_text(encoding="utf-8")
            bodies = re.findall(r'<p class="fd-text">(.*?)</p>', html, re.S)
            self.assertTrue(bodies, doc["slug"])
            for b in bodies:
                self.assertNotRegex(b, "[가-힣]", f"{doc['slug']} 본문이 한국어다")
            self.assertIn('<html lang="en">', html)
            checked += 1
        self.assertGreater(checked, 0)
        # 원문이 한국어인 문서는 영어 페이지 자체가 없다.
        for doc in [d for d in self.data["documents"]
                    if not render.doc_is_english(d)][:20]:
            self.assertFalse((en_root / doc["slug"] / "index.html").exists(),
                             f"원문이 한국어인데 영어 페이지가 있다: {doc['slug']}")

    def test_headline_facts_present(self):
        """업체명·발행일·원문 링크 — 실명 기록 페이지에서 빠지면 안 되는 셋."""
        from markupsafe import escape as _esc
        for doc in self._sample_docs():
            html = self._page(doc["slug"])
            self.assertIn(str(_esc(doc["firm_name"])), html, doc["slug"])
            self.assertIn(doc["published_date"], html, f'발행일 누락: {doc["slug"]}')
            self.assertIn(str(_esc(doc["evidence_url"])), html,
                          f'원문 링크 누락: {doc["slug"]}')

    def test_finding_text_is_verbatim(self):
        """본문 무변형 — 용어 자동 링크가 들어온 뒤에도 **글자는 하나도 바뀌지 않는다**.

        예전에는 escape 한 원문이 HTML 안에 통째로 들어 있는지만 봤는데, 본문 안에 `<a>` 가
        끼워지면서 그 단언이 성립하지 않는다. 계약을 더 세게 다시 세운다 — 렌더된 `<p
        class="fd-text">` 에서 **태그만 벗기면 원문과 완전히 같아야** 한다(부분일치가 아니라
        전체 일치라 절단·치환·중복이 전부 잡힌다).
        """
        import html as _html
        checked = 0
        for doc in self._sample_docs(8):
            page = self._page(doc["slug"])
            bodies = re.findall(r'<p class="fd-text">(.*?)</p>', page, re.S)
            self.assertEqual(len(bodies), len(doc["findings"]),
                             f'본문 개수 불일치: {doc["slug"]}')
            for f, body in zip(doc["findings"], bodies):
                plain = _html.unescape(re.sub(r"<[^>]+>", "", body))
                self.assertEqual(plain, f["text_ko"],
                                 f'본문이 무변형이 아님: {f["finding_id"]}')
                checked += 1
        self.assertGreater(checked, 0)

    def test_term_autolinks_point_to_real_pages_once_each(self):
        """[내부 링크] 문서 본문 → 용어 페이지. 존재하는 용어로, 페이지당 용어 1 회만.

        용어 페이지 인바운드가 평균 4 개·45 개는 색인 1 개뿐이었는데 문서 3,202 장은 용어로
        가는 링크가 0 이었다. 여기서 검사하는 건 "링크가 있다"가 아니라 **가리키는 곳이
        실재하고, 같은 말에 반복 링크가 붙지 않는다**는 것이다.
        """
        term_ids = {t["id"] for t in json.loads(
            render.GLOSSARY_FILE.read_text(encoding="utf-8"))}
        total = 0
        for doc in self._sample_docs(10):
            page = self._page(doc["slug"])
            hrefs = re.findall(r'<a class="fd-term" href="\.\./\.\./\.\./glossary/([^/]+)/"',
                               page)
            for tid in hrefs:
                self.assertIn(tid, term_ids, f'없는 용어로 링크: {doc["slug"]} → {tid}')
                self.assertTrue((self.out / "glossary" / tid / "index.html").is_file(),
                                f'링크 대상 페이지 부재: {tid}')
            self.assertEqual(len(hrefs), len(set(hrefs)),
                             f'한 페이지에서 같은 용어에 반복 링크: {doc["slug"]}')
            self.assertLessEqual(len(hrefs), render._DOC_TERM_LINK_MAX,
                                 f'링크 상한 초과: {doc["slug"]}')
            total += len(hrefs)
        self.assertGreater(total, 0, "용어 링크가 하나도 안 붙었다(배선 확인)")

    def test_term_autolink_skips_verb_and_purposive_usage(self):
        """[비순환 가드] 명사가 아닌 자리에는 링크하지 않는다 — 규칙을 실제 문장으로 고정.

        한글은 낱말 경계가 없어 `…하기 위해` 의 '위해' 가 용어 `위해(harm)` 로, `기록하고`
        의 '기록' 이 용어 `기록` 으로 걸린다(실측). 선정 함수로 선정 결과를 검사하면 함께
        망가져 조용히 통과하므로(용어 사례에서 겪었다) 규칙 자체를 고정 입력으로 잠근다.
        """
        cases = [
            ("동일성을 확인하기 위해 최소 1건의 시험을 실시하지", "위해", True),
            ("체계로부터의 일탈을 기록하고 정당화하지 않았습니다", "기록", True),
            ("귀사의 품질관리부서는 제조된 의약품이", "제조", True),
            ("장비가 적절히 교정되지 않았습니다", "교정", True),
            ("오염 위험으로부터 적절히 보호되지", "위험", False),
            ("귀사는 각 의약품 배치를 출하하기 전에", "배치", False),
            ("귀사는 의약품의 제조, 가공, 포장 및 보관에", "제조", False),
            ("정기적으로 교정, 검사 또는 점검하지", "교정", False),
        ]
        for text, surface, verbish in cases:
            i = text.find(surface)
            self.assertEqual(
                render._doc_term_is_verbish(text, i, i + len(surface)), verbish,
                f'명사/동사 판정 어긋남: «{surface}» {text[:32]}…')
            # find 는 동사 자리를 건너뛰어야 한다(그 자리 하나뿐이면 -1).
            if verbish and text.count(surface) == 1:
                self.assertEqual(render._doc_term_find(text, surface), -1,
                                 f'동사 자리를 링크 후보로 잡았다: «{surface}»')

    def test_term_autolink_prefers_rare_terms(self):
        """희소 우선 — 링크가 필요한 건 인바운드가 없는 롱테일 용어지 `품질`·`제조` 가 아니다."""
        terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))
        docs = (self.data or {}).get("documents") or []
        index = render.build_doc_term_link_index(terms)
        freq = render.build_doc_term_doc_freq(index, docs)
        picked_any = False
        for doc in docs[:60]:
            selected = render.select_doc_term_links(doc, index, freq)
            if len(selected) < 2:
                continue
            picked_any = True
            dfs = [freq[tid] for _, tid in selected]
            self.assertEqual(dfs, sorted(dfs), f'희소 우선 정렬이 깨졌다: {doc["slug"]}')
            # 이 문서에 등장하지만 뽑히지 않은 용어는 뽑힌 것보다 흔해야 한다.
            blob = "\n".join(f.get("text_ko") or "" for f in doc.get("findings") or [])
            present = {tid for surface, tid in index
                       if render._doc_term_find(blob, surface) >= 0}
            dropped = present - {tid for _, tid in selected}
            if dropped:
                self.assertLessEqual(max(dfs), min(freq[t] for t in dropped),
                                     f'더 흔한 용어가 희소 용어를 밀어냈다: {doc["slug"]}')
        self.assertTrue(picked_any, "선택이 검사된 문서가 없다")

    def test_record_context_disclosure(self):
        """★이 기록이 '그 시점의 것'이고 후속 시정을 우리가 모른다는 사실을 반드시 적는다.

        실명 업체의 지적 이력을 색인시키는 페이지다. 날짜 맥락과 후속 절차 가능성을 빼면
        몇 년 전 지적이 현재 상태로 읽힌다 — 그건 사실 왜곡이다.
        """
        for doc in self._sample_docs(7):
            html = self._page(doc["slug"])
            self.assertIn("이 기록에 대하여", html)
            self.assertIn("그 시점의 기록", html)
            self.assertIn("시정", html)
            self.assertIn("기계로 처리", html)

    def test_description_carries_the_date(self):
        """검색 스니펫에 연도가 없으면 옛 지적이 현재로 읽힌다."""
        labels = self.data.get("agency_labels") or {}
        for doc in self._sample_docs(10):
            desc = render.doc_page_description(doc, labels)
            self.assertIn(doc["published_date"], desc)
            self.assertIn(doc["firm_name"], desc)

    def test_canonical_and_related_links(self):
        for doc in self._sample_docs(7):
            html = self._page(doc["slug"])
            self.assertIn(f'{render.SITE_BASE_URL}/findings/doc/{doc["slug"]}/', html)
            self.assertIn(f'href="../../../findings/agency/{doc["agency"].lower()}/"', html)

    # ── 내부 링크 구조 ──────────────────────────────────────────────────────
    # sitemap 에만 있는 페이지는 사이트 구조에서 닿는 경로가 없어 크롤이 느리고 중요도
    # 신호도 안 붙는다. 용어사전·모음 페이지에는 축 색인을 함께 냈으면서 문서 페이지에만
    # 빠뜨렸던 것을 #723 에서 메웠다 — 그 불변식을 여기서 지킨다.

    def test_no_document_page_is_an_orphan(self):
        """★모든 문서 페이지가 목록 페이지에서 링크되어야 한다.

        이 검사가 이 클래스에서 가장 중요하다. 문서가 늘어 새 기관·연도 조합이 생겼는데
        목록 생성이 그것을 놓치면, 그 문서들은 sitemap 에만 남아 조용히 고아가 된다.
        """
        import re as _re
        docs_root = self.out / "findings" / "docs"
        self.assertTrue(docs_root.exists(), "문서 목록 디렉터리 누락")
        linked: set[str] = set()
        for p in docs_root.rglob("index.html"):
            linked |= set(_re.findall(r"findings/doc/([A-Za-z0-9._-]+)/",
                                      p.read_text(encoding="utf-8")))
        orphans = sorted({d["slug"] for d in self.data["documents"]} - linked)
        self.assertEqual(orphans, [],
                         f"목록에서 못 닿는 문서 {len(orphans)}건: {orphans[:5]}")

    def test_doc_index_links_every_agency_year_bucket(self):
        buckets = {(d["agency"].lower(), d["published_date"][:4])
                   for d in self.data["documents"]}
        index = (self.out / "findings" / "docs" / "index.html").read_text(encoding="utf-8")
        for agency, year in sorted(buckets):
            self.assertIn(f'href="../../findings/docs/{agency}/{year}/"', index,
                          f"색인이 {agency}/{year} 를 링크하지 않음")
            self.assertTrue(
                (self.out / "findings" / "docs" / agency / year / "index.html").exists(),
                f"목록 페이지 누락: {agency}/{year}")

    # ── sitemap lastmod ──────────────────────────────────────────────────
    # 구글은 lastmod 로 재크롤 우선순위를 정한다. 3,500장이 한꺼번에 생긴 상태에서 이 값이
    # 없으면 무엇부터 볼지 판단할 근거가 없다. 다만 ★지어낸 수정일은 없느니만 못하다 —
    # 신뢰할 수 없는 lastmod 를 만나면 구글은 그 사이트의 lastmod 를 통째로 무시하기
    # 시작한다. 그래서 "데이터에 실제 날짜가 있는 곳에만, 그 값 그대로" 가 유일한 규칙이다.

    def _sitemap_lastmods(self) -> dict:
        import re as _re
        out = {}
        for m in _re.finditer(r"<loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?",
                              self.sitemap):
            out[m.group(1)] = m.group(2) or ""
        return out

    def test_document_lastmod_is_the_publish_date(self):
        mods = self._sitemap_lastmods()
        for doc in self.data["documents"]:
            url = f'{render.SITE_BASE_URL}/findings/doc/{doc["slug"]}/'
            self.assertEqual(mods.get(url), doc["published_date"],
                             f'문서 lastmod 가 공개일과 다르다: {doc["slug"]}')

    def test_listing_lastmod_is_the_newest_document_in_that_bucket(self):
        from collections import defaultdict
        newest = defaultdict(str)
        for d in self.data["documents"]:
            k = (d["agency"].lower(), d["published_date"][:4])
            newest[k] = max(newest[k], d["published_date"])
        mods = self._sitemap_lastmods()
        for (agency, year), want in newest.items():
            url = f"{render.SITE_BASE_URL}/findings/docs/{agency}/{year}/"
            self.assertEqual(mods.get(url), want, f"목록 lastmod: {agency}/{year}")
        self.assertEqual(
            mods.get(f"{render.SITE_BASE_URL}/findings/docs/"),
            max(d["published_date"] for d in self.data["documents"]))

    def test_no_fabricated_lastmod_where_there_is_no_date(self):
        """★날짜 개념이 없는 페이지에는 lastmod 를 달지 않는다(용어사전 등)."""
        mods = self._sitemap_lastmods()
        dated = [u for u, m in mods.items() if m and "/glossary/" in u]
        self.assertEqual(dated, [], f"없는 날짜를 지어냈다: {dated[:3]}")

    def test_every_lastmod_is_a_plain_date(self):
        import re as _re
        for url, mod in self._sitemap_lastmods().items():
            if mod:
                self.assertRegex(mod, r"^\d{4}-\d{2}-\d{2}$", url)

    def test_listing_pages_are_in_sitemap(self):
        buckets = {(d["agency"].lower(), d["published_date"][:4])
                   for d in self.data["documents"]}
        self.assertIn(f"<loc>{render.SITE_BASE_URL}/findings/docs/</loc>", self.sitemap)
        for agency, year in sorted(buckets):
            self.assertIn(
                f"<loc>{render.SITE_BASE_URL}/findings/docs/{agency}/{year}/</loc>",
                self.sitemap, f"sitemap 미등록: docs/{agency}/{year}")

    def test_same_firm_links_exclude_self_and_exist(self):
        """같은 업체 링크는 자기 자신을 빼고, 실제 페이지만 가리켜야 한다."""
        import re as _re
        by_key: dict[str, list[str]] = {}
        for d in self.data["documents"]:
            if d.get("firm_key"):
                by_key.setdefault(d["firm_key"], []).append(d["slug"])
        multi = [d for d in self.data["documents"]
                 if len(by_key.get(d.get("firm_key") or "", [])) > 1][:15]
        self.assertTrue(multi, "문서 2건 이상 업체가 없다(배선 확인)")
        for doc in multi:
            html = self._page(doc["slug"])
            self.assertIn("같은 업체의 다른 기록", html, doc["slug"])
            section = html.split("같은 업체의 다른 기록", 1)[1].split("</section>", 1)[0]
            targets = _re.findall(r"findings/doc/([A-Za-z0-9._-]+)/", section)
            self.assertTrue(targets, f"같은 업체 링크 0건: {doc['slug']}")
            self.assertNotIn(doc["slug"], targets, "자기 자신을 링크했다")
            for t in targets:
                self.assertIn(t, by_key[doc["firm_key"]],
                              f"다른 업체 문서를 같은 업체로 링크: {t}")
                self.assertTrue(
                    (self.root / t / "index.html").exists(), f"없는 페이지로 링크: {t}")

    def test_every_page_printing_firm_names_carries_the_time_disclosure(self):
        """★실명 업체를 인쇄하는 **모든** 페이지에 시점 고지가 붙어야 한다.

        종전에는 문서 페이지에만 있었다. 모음·목록 페이지도 같은 업체명을 같은 무게로
        노출하는데 한쪽만 맥락을 주면, 몇 년 전 지적이 현재 상태로 읽힌다.
        """
        facets = render.load_findings_facets()
        targets = [self.out / "findings" / "docs" / "index.html"]
        for axis in (facets or {}).get("axes", []):
            meta = render.FACET_AXES[axis["axis"]]
            targets += [self.out / "findings" / meta["path"] / it["slug"] / "index.html"
                        for it in axis["items"]]
        targets += list((self.out / "findings" / "docs").rglob("index.html"))
        targets += [self.root / d["slug"] / "index.html"
                    for d in self._sample_docs(4)]
        missing = [p.relative_to(self.out).as_posix() for p in targets
                   if "시점의 기록" not in p.read_bytes().decode("utf-8")]
        self.assertEqual(missing, [], f"시점 고지 누락 {len(missing)}장: {missing[:5]}")

    def test_agency_code_is_not_shown_as_a_document_type(self):
        """★없는 문서종류를 단정하지 않는다.

        식약처 문서 121장은 `source` 가 기관 코드 그대로 `"MFDS"` 라, 그대로 쓰면 제목이
        "(주)태준제약 — MFDS 지적사항"이 되어 실재하지 않는 문서종류를 주장하게 된다.
        지어내지도(예: "GMP 실사 보고서") 코드를 노출하지도 않는 유일한 답은 생략이다.

        ★[B2 2026-08-27] 종전에는 `"{업체} 지적사항"` 이 제목에 그대로 들어있는지로 쟀다.
        그건 **그때의 배치**(업체 바로 뒤가 문서종류 자리)에 묶인 표현이라, 날짜를
        "지적사항" 앞으로 옮기자 지키던 뜻은 멀쩡한데 검사만 깨졌다. 그래서 뜻을 직접
        잰다 — **업체명과 "지적사항" 사이에 날짜 말고는 아무것도 없다.** 이 형태는 옛
        배치에서도 참이라(그때는 사이가 빈 문자열) 배치를 또 바꿔도 살아남고, 문서종류를
        슬쩍 끼워 넣는 변경은 그대로 잡는다.
        """
        checked = 0
        for doc in self.data["documents"]:
            if (doc.get("source") or "").upper() != (doc.get("agency") or "").upper():
                continue
            checked += 1
            self.assertEqual(render.doc_source_label(doc), "", doc["slug"])
            html = self._page(doc["slug"])
            title = html.split("<title>", 1)[1].split("</title>", 1)[0]
            self.assertNotIn(doc["agency"], title,
                             f'제목이 기관 코드를 문서종류로 쓴다: {doc["slug"]}')
            firm = render.title_firm_name(doc["firm_name"])
            self.assertIn(firm, title, doc["slug"])
            between = title.split(firm, 1)[1].split(" 지적사항", 1)[0].strip()
            self.assertRegex(
                between, r"^(\(\d{4}-\d{2}-\d{2}\))?$",
                f'업체명과 "지적사항" 사이에 문서종류가 끼어들었다: '
                f'{doc["slug"]} — {between!r}')
        self.assertGreater(checked, 0, "기관 코드가 source 인 문서가 없다(배선 확인)")

    def test_real_document_types_are_kept(self):
        for doc in self._sample_docs(6):
            src = (doc.get("source") or "")
            if src.upper() == (doc.get("agency") or "").upper():
                continue
            self.assertEqual(render.doc_source_label(doc), src)
            self.assertIn(src, self._page(doc["slug"]))

    def test_titles_are_unique(self):
        """★제목은 검색 결과의 1차 식별자다 — 겹치면 구글이 하나만 고르고 나머지를 버린다.

        같은 업체·같은 기관·같은 공개일로 나뉜 실사 보고서가 실재해서(최대 8장) 기본형
        제목만으로는 유일해지지 않는다. `build_doc_page_titles` 가 겹칠 때만 분류·문서번호로
        넓히는데, 그 결과가 실제로 유일한지를 렌더 산출물에서 확인한다.
        """
        import re as _re
        from collections import Counter as _C
        titles = _C()
        for doc in self.data["documents"]:
            m = _re.search(r"<title>(.*?)</title>",
                           self._page(doc["slug"]), _re.S)
            self.assertIsNotNone(m, f'<title> 없음: {doc["slug"]}')
            titles[m.group(1)] += 1
        dupes = {t: n for t, n in titles.items() if n > 1}
        self.assertEqual(dupes, {}, f"중복 제목 {len(dupes)}종: {list(dupes)[:3]}")

    def test_korean_safety_on_document_pages(self):
        """★§4 한글 안전 가드는 문서 페이지를 못 본다 — 스위트가 문서 렌더를 끄기 때문이다.

        그 구멍으로 `.fd-chip.dt{font-family:var(--mono)}` + "공개"(한글)가 3천 장에
        실제로 나갔다. 속도 스위치를 둔 대가로 생긴 사각지대이므로, 켠 채로 짓는 이
        클래스가 같은 규칙을 직접 검사한다.
        """
        import re as _re
        sample = [d["slug"] for d in self.data["documents"][:5]]
        sample += [d["slug"] for d in self.data["documents"][-5:]]
        for slug in sample:
            html = self._page(slug)
            style = "\n".join(_re.findall(r"<style>(.*?)</style>", html, _re.S))
            self.assertNotIn("letter-spacing", style, f"§4 위반(자간): {slug}")
            self.assertNotIn("text-transform", style, f"§4 위반(대문자): {slug}")
            mono = {m.group(1) for m in
                    _re.finditer(r"\.([a-z0-9-]+)\{[^}]*var\(--mono\)", style)}
            for cls in mono:
                for m in _re.finditer(
                        r'class="[^"]*\b' + _re.escape(cls) + r'\b[^"]*"[^>]*>([^<]*)',
                        html):
                    self.assertIsNone(
                        _re.search(r"[가-힣]", m.group(1)),
                        f"§4 위반(한글에 모노): {slug} .{cls} → {m.group(1)[:30]!r}")

    def test_facet_samples_link_only_to_existing_doc_pages(self):
        """페이지가 없는 문서(두께 임계 미달 — 면제 소스 제외)에는 링크를 만들면 안 된다.

        판정은 문구가 아니라 **정본의 slug 집합**이라 임계·면제 규칙이 바뀌어도 이
        테스트는 그대로 옳다(스냅샷에 있으면 페이지가 있고, 없으면 링크도 없어야 한다)."""
        import re as _re
        facets = render.load_findings_facets()
        if not facets:
            self.skipTest("findings_facets.json 미존재")
        known = {d["slug"] for d in self.data["documents"]}
        checked = linked = 0
        for axis in facets["axes"]:
            meta = render.FACET_AXES[axis["axis"]]
            for item in axis["items"]:
                page = (self.out / "findings" / meta["path"] / item["slug"] /
                        "index.html").read_text(encoding="utf-8")
                targets = set(_re.findall(r"findings/doc/([A-Za-z0-9._-]+)/", page))
                for t in targets:
                    self.assertIn(t, known, f"없는 문서 페이지로 링크: {t}")
                    self.assertTrue((self.root / t / "index.html").exists())
                for s in item["samples"]:
                    checked += 1
                    if (s.get("document_id") or "") in known:
                        linked += 1
                        self.assertIn(s["document_id"], targets,
                                      f'문서 페이지가 있는데 링크 안 함: {s["document_id"]}')
        self.assertGreater(checked, 0)
        self.assertGreater(linked, 0, "사례→문서 링크가 하나도 없다(배선 확인)")


# ── [실사관 표기 · 정적 문서 페이지 2026-08-31] ────────────────────────────────
# 라이브 findings_docs.json 은 아직 inspector_names 를 담지 않는다(findings_docs_
# refresh.py 는 네트워크를 타야 재생성되므로 이 작업에서는 실행하지 않는다 — 스펙
# 참조). 그래서 WebFindingsDocPageTest 처럼 라이브 정본을 읽는 방식으로는 이 기능을
# 검사할 수 없다 — 아래 두 클래스는 합성 doc/합성 정본으로 render.doc_inspector_line()
# 과 findings_doc.html·render_site() 의 신규 에셋 발행을 각각 직접 검사한다
# (WebFda483InspectorLineTest 가 카드 경로에 쓰는 것과 같은 직접 템플릿 렌더 패턴).
class WebFindingsDocInspectorLineTest(unittest.TestCase):
    """findings_doc.html '실사관' 행 — render.doc_inspector_line() 이 만든 문자열을
    템플릿이 그대로 낸다. 빈 라벨 금지·최대 3명+외 N명·코호트 무관 전원 평문. 이름에는
    여전히 <a> 를 달지 않는다(코호트를 아는 동적 층 findings.js 가 개별 프로파일 진입을
    계속 담당한다) — 하지만 [진입점 3종 2026-08-31]부터 행 끝에 코호트를 몰라도 걸 수
    있는 링크 하나(실사관 조회 색인, findings/inspector/index.html)가 추가된다. 문구는
    "실사관 이력 조회"이며 "이 실사관"이라고 쓰지 않는다(한 문서에 실사관 2명 이상이
    흔하다 — 지시대상 모호 + 이 링크의 목적지는 애초에 개별 프로파일이 아니라 색인)."""

    def _doc(self, **kw):
        base = {
            "document_id": "d1", "slug": "d1", "agency": "FDA", "source": "FDA 483",
            "firm_name": "Acme Pharma", "firm_key": "acme-pharma",
            "published_date": "2026-01-15", "inspection_date": "",
            "evidence_url": "https://www.fda.gov/x",
            "categories": [],
            "findings": [{"finding_id": "f1", "text_ko": "지적 본문",
                         "category_label_ko": ""}],
        }
        base.update(kw)
        return base

    def _render(self, doc):
        env = render._make_env()
        return env.get_template("findings_doc.html").render(
            page_title="t", rel_root="", nav_active="findings", latest_slug="x",
            description="d", canonical="c", json_ld="",
            doc=doc, agency_labels={}, source_label="",
            inspector_line=render.doc_inspector_line(doc),
            related_categories=[], same_firm=[], finding_bodies=[],
            firm_page_slug=None)

    def test_line_rendered_when_present(self):
        h = self._render(self._doc(
            inspector_names=["Jose F Velez", "Ivis L Negron Torres"]))
        self.assertIn(
            '<p class="fd-insp">실사관 <b>Jose F Velez · Ivis L Negron Torres</b>'
            '<span>공개 문서에 서명한 실사관입니다.</span>'
            '<a class="fd-insp-go" href="findings/inspector/index.html">실사관 이력 조회 '
            '<i class="ti ti-arrow-right" aria-hidden="true"></i></a></p>', h)

    def test_no_line_when_key_absent(self):
        # 페이지 전역에 "실사관"이 없어야 한다는 게 아니다 — base.html footer 의 상시
        # 도구 링크("실사관 조회")가 이미 그 문자열을 담고 있다. 여기서 보는 건 이 행
        # 전용 클래스(fd-insp)가 생성되지 않는다는 것 뿐이다.
        h = self._render(self._doc())
        self.assertNotIn('class="fd-insp"', h)

    def test_no_line_for_blank_or_invalid_input(self):
        for bad in (None, "Jose F Velez", 42, {}, [], [None, 123, "   ", ""]):
            with self.subTest(bad=bad):
                h = self._render(self._doc(inspector_names=bad))
                self.assertNotIn('class="fd-insp"', h)

    def test_more_than_three_shows_three_and_extra_count(self):
        names = [f"Name {i}" for i in range(5)]
        h = self._render(self._doc(inspector_names=names))
        self.assertIn("실사관 <b>Name 0 · Name 1 · Name 2 외 2명</b>", h)
        self.assertNotIn("Name 3", h)

    def test_exactly_three_has_no_extra_suffix(self):
        h = self._render(self._doc(inspector_names=["A B", "C D", "E F"]))
        self.assertIn("실사관 <b>A B · C D · E F</b>", h)
        self.assertNotIn("외 0명", h)

    def test_index_link_present_but_never_individual_profile(self):
        """★코호트 개념이 정적 빌드에 없다 — 어떤 이름이 와도 **개별** 프로파일(?key=)
        링크는 만들지 않는다(코호트는 findings_inspector_index RPC 가 정하는 런타임
        개념이라 정적 스냅샷이 재현하면 정의가 갈라지고, 갈라진 링크는 존재하지 않는
        프로파일로 사용자를 보낸다 — #860 결정). 대신 코호트를 몰라도 항상 걸 수 있는
        정적 색인 링크 정확히 1개(이름을 파라미터로 담지 않는다)를 이 행 끝에 둔다."""
        h = self._render(self._doc(inspector_names=["Jose F Velez"]))
        start = h.index('class="fd-insp"')
        seg = h[start:h.index("</p>", start)]
        self.assertEqual(seg.count("<a "), 1, "실사관 행의 링크는 정확히 1개(색인)여야 한다")
        self.assertIn('href="findings/inspector/index.html"', seg)
        self.assertNotIn("?key=", seg, "정적 빌드가 코호트를 몰라 개별 프로파일 링크를 걸면 안 된다")
        self.assertNotIn("Jose F Velez", seg[seg.index("<a "):], "링크 자체에 이름을 담지 않는다")
        self.assertNotIn("이 실사관", h)

    def test_names_are_html_escaped(self):
        h = self._render(self._doc(
            inspector_names=["<script>alert(1)</script>", "A & B"]))
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", h)
        self.assertIn("A &amp; B", h)


class WebDocInspectorLineUnitTest(unittest.TestCase):
    """render.doc_inspector_line() 순수 함수 단위 테스트."""

    def test_empty_or_missing_returns_blank(self):
        self.assertEqual(render.doc_inspector_line({}), "")
        self.assertEqual(render.doc_inspector_line({"inspector_names": []}), "")
        self.assertEqual(render.doc_inspector_line({"inspector_names": None}), "")

    def test_up_to_three_joined_with_middle_dot_no_suffix(self):
        self.assertEqual(
            render.doc_inspector_line({"inspector_names": ["A", "B"]}), "A · B")
        self.assertEqual(
            render.doc_inspector_line({"inspector_names": ["A", "B", "C"]}), "A · B · C")

    def test_more_than_three_truncates_with_count(self):
        self.assertEqual(
            render.doc_inspector_line(
                {"inspector_names": ["A", "B", "C", "D", "E"]}),
            "A · B · C 외 2명")

    def test_defers_to_shared_sanitizer_no_double_cleaning(self):
        """비문자열/공백 원소는 render._sanitize_inspector_names() 계약대로 걸러진다 —
        여기서 별도 정제 로직을 다시 두지 않았다는 것을 고정."""
        self.assertEqual(
            render.doc_inspector_line({"inspector_names": ["A", None, "  ", "B"]}),
            "A · B")

    def test_names_beyond_sanitizer_cap_of_six(self):
        """정제(6개 상한)가 먼저 걸리므로 8명 입력이어도 "외 N명"의 N 은 정제 통과분
        기준(6-3=3)이지 실제 초과 인원(5)이 아니다 — doc_inspector_line() 주석에 명시된
        의도된 트레이드오프(카드·검색 화면과 같은 기존 방어선)."""
        names = [f"Name {i}" for i in range(8)]
        self.assertEqual(render.doc_inspector_line({"inspector_names": names}),
                         "Name 0 · Name 1 · Name 2 외 3명")


class WebInspectorDocPagesAssetTest(unittest.TestCase):
    """[실사관 프로파일 문서목록 멤버십 2026-08-31] assets/inspector-doc-pages.json.

    실사관 프로파일 페이지(런타임 RPC 화면)가 "이 실사관이 서명한 문서" 목록에서 정적
    문서 페이지(findings/doc/{slug}/)로 링크하기 전에, 그 페이지가 실제로 존재하는지
    확인하는 멤버십 집합이다 — 정적 페이지는 두께 임계를 넘긴 문서만 있어서 확인 없이
    링크하면 일부가 404 다. render.load_findings_docs 를 합성 정본으로 바꿔치기해
    render_site() 를 실제로 돌린다(라이브 findings_docs.json 에는 아직 inspector_names
    가 없어 실 데이터로는 이 계약을 검사할 수 없다)."""

    @classmethod
    def setUpClass(cls):
        cls._real_load = render.load_findings_docs

        def _finding(fid):
            return {"finding_id": fid, "text_ko": "x", "category_label_ko": ""}

        docs = [
            {"document_id": "d1-with-inspectors", "slug": "d1", "agency": "FDA",
             "source": "FDA 483", "firm_name": "Acme Pharma", "firm_key": "acme",
             "published_date": "2026-01-01", "inspection_date": "",
             "evidence_url": "https://www.fda.gov/1", "categories": [],
             "findings": [_finding("f1")], "inspector_names": ["Jose F Velez"]},
            {"document_id": "d2-no-inspectors", "slug": "d2", "agency": "FDA",
             "source": "FDA 483", "firm_name": "Beta Pharma", "firm_key": "beta",
             "published_date": "2026-01-02", "inspection_date": "",
             "evidence_url": "https://www.fda.gov/2", "categories": [],
             "findings": [_finding("f2")]},
            # 사전순으로 "d1-with-inspectors" 보다 앞서는 id — 정렬이 삽입 순서가 아니라
            # **값**으로 결정된다는 것을 함께 고정한다.
            {"document_id": "a0-with-inspectors", "slug": "d0", "agency": "HC",
             "source": "Health Canada Inspection", "firm_name": "Charlie Inc",
             "firm_key": "charlie", "published_date": "2026-01-03", "inspection_date": "",
             "evidence_url": "https://x.gc.ca/3", "categories": [],
             "findings": [_finding("f3")], "inspector_names": ["Zed Zephyr"]},
        ]

        def _fake_load(path=None):
            return {
                "schema_version": "grm-findings-docs/v1", "measured_on": "2026-08-31",
                "min_findings": 3,
                "totals": {"documents": len(docs),
                          "findings": sum(len(d["findings"]) for d in docs)},
                "by_agency": [], "excluded": [], "documents": docs,
                "agency_labels": {"FDA": "FDA", "HC": "Health Canada"},
            }
        render.load_findings_docs = _fake_load

        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_inspasset_"))
        cls.out = cls._tmp / "single"
        # render_doc_pages=False — 개별 문서 HTML 3천 장 렌더 비용과 무관하게 이 에셋이
        # 나오는지가 검사 대상이다(sitemap·목록·색인과 같은 "스위치 무관" 계약).
        render.render_site(SINGLE_FIXTURES, cls.out, render_doc_pages=False)
        cls.asset_path = cls.out / "assets" / "inspector-doc-pages.json"
        cls.asset = json.loads(cls.asset_path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        render.load_findings_docs = cls._real_load
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_written_even_when_doc_pages_render_is_off(self):
        self.assertTrue(self.asset_path.exists())

    def test_schema(self):
        self.assertEqual(self.asset["schema"], "grm-inspector-doc-pages/v1")

    def test_only_documents_with_inspector_names_are_listed(self):
        self.assertEqual(self.asset["document_ids"],
                         ["a0-with-inspectors", "d1-with-inspectors"])

    def test_sorted_lexicographically(self):
        ids = self.asset["document_ids"]
        self.assertEqual(ids, sorted(ids))

    def test_trailing_newline_and_ascii_safe_json_convention(self):
        raw = self.asset_path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))

    def test_listed_in_written_manifest(self):
        meta = render.render_site(SINGLE_FIXTURES, self._tmp / "single2",
                                  render_doc_pages=False)
        self.assertIn("assets/inspector-doc-pages.json", meta["written"])


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
        # 카탈로그 미지정(catalogs=None) 호출: build_glossary_view 는 이 경우도 지원한다
        # (하위호환). R1(21 CFR)은 자료실 카탈로그 없이 정규식만으로 eCFR URL 을 조립하는
        # 규칙(B2)이라 catalogs 를 안 실어도 "21 CFR 211.100" 은 resolve 된다 — R2~R6(ICH·
        # EU GMP·PIC/S·WHO·국내)만 카탈로그 부재 시 "" 로 떨어진다(WebGlossaryRegRefLinkGuardTest
        # 참조). "무링크 조항"(카탈로그 매치 대상 아닌 임의 라벨)은 R1~R7 어디에도 안 걸려 "".
        view = render.build_glossary_view([synthetic])
        t = view["groups"][0]["terms"][0]
        self.assertEqual(t["detail_ko"], "실무에서는 이렇게 씁니다")
        # [2026-09-03] cases_href — 단일 21 CFR 조항만 **조항 정적 페이지**로 착지한다
        # (그 외 계열·구간·Part 표기는 "" — 대상 조항이 하나로 안 정해진다).
        # 이 호출은 clause_slugs 미지정이라 "실제 페이지 유무를 모르는" 모드다 →
        # 형식만 맞으면 경로를 낸다(render_site 는 실제 슬러그 집합을 실어 좁힌다).
        self.assertEqual(t["reg_refs"], [
            {"label": "21 CFR 211.100", "url": "https://www.ecfr.gov/current/title-21/section-211.100",
             "cases_href": "findings/clause/211-100/"},
            {"label": "ICH Q7", "url": "https://ich.org/q7", "cases_href": ""},
            {"label": "무링크 조항", "url": "", "cases_href": ""},
            {"label": "위험스킴", "url": "", "cases_href": ""},
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
            render.render_site(SINGLE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
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


class WebGlossaryRegRefLinkGuardTest(unittest.TestCase):
    """[B3] reg_refs 링크 조용한 소실 가드 — WebGlossaryDeepFieldsTest 는 정규화 로직을
    합성 데이터로 검증하고, 이 클래스는 **커밋된 정본 glossary.json 전 항목 + 실 자료실
    카탈로그**를 render.build_glossary_view 의 공개 경로로 돌려 실제 링크 수를 잰다.

    왜 필요한가: 자료실(web/data/library/*.json)은 매주 자동 갱신되고 그때 골든도 자동
    재동결돼 커밋된다. 자료실에서 문서 코드가 사라지거나 이름이 바뀌면(_reg_ref_url 이
    code 정확 일치만 신뢰하므로) 용어사전 링크가 **아무 경고 없이** 사라지고 골든은 그
    상태로 다시 도장 찍힌다 — 화면은 멀쩡해 보이고 기존 테스트도 통과한다. 아래 하한선/
    상한선은 파일 상단 상수(실측치, 2026-08-04) 참조. render.py·web/data/*·golden 은
    이 클래스에서 절대 건드리지 않는다(읽기 전용 관측)."""

    @classmethod
    def setUpClass(cls):
        cls.terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))
        # 프로덕션 호출부(render.py 의 render_site)와 동일한 경로: 실 자료실 카탈로그를
        # 명시적으로 실어 build_glossary_view 를 돌린다(카탈로그 미지정이면 R2~R6 이
        # 전부 "" 로 떨어져 이 가드 자체가 무의미해진다).
        cls.catalogs = render._load_reg_ref_catalogs()
        # [2026-09-03] 조항 착지는 **실제로 만들어진 조항 페이지**에만 걸린다 —
        # 프로덕션 호출부(render_site)와 같은 입력을 실어야 이 가드가 유효하다.
        cls.clause_slugs = {
            v["slug"] for v in render.build_clause_views(
                render.load_findings_docs(), render.load_cfr_catalog(), cls.terms)}
        cls.view = render.build_glossary_view(
            cls.terms, cls.catalogs, None, cls.clause_slugs)
        # 그룹→용어→reg_refs 평탄화. 같은 라벨이 여러 용어에서 반복 인용돼도 그대로 둔다
        # — 화면은 용어 카드마다 칩을 새로 그리므로 "칩 개수"는 발생 건수 기준이어야
        # 렌더와 일치한다(고유 라벨 집합으로 접으면 실제 화면 손실 규모를 과소평가한다).
        cls.chips = [r for g in cls.view["groups"] for t in g["terms"] for r in t["reg_refs"]]

    def setUp(self):
        # 0건 가드(명세 5) — reg_refs 대상 칩이 애초에 0건이면 아래 "N 이상"·"계열별
        # 최소" 단언들은 빈 집합에 대한 전칭 단언이 되어 항상 참으로 조용히 통과해버린다
        # (위험). 이 클래스의 모든 테스트 앞에 걸어 어떤 메서드도 그 함정을 피해가지
        # 못하게 한다.
        self.assertGreater(
            len(self.chips), 0,
            "glossary.json 전 항목의 reg_refs 대상 칩이 0건 — 검사 대상 자체가 사라졌다"
            "(빈 집합에 대한 링크 단언은 항상 참이라 이 상태로는 가드가 무의미하다)")

    def test_reg_ref_links_resolve_above_floor(self):
        resolved = [c for c in self.chips if c["url"]]
        lost_labels = sorted({c["label"] for c in self.chips if not c["url"]})
        self.assertGreaterEqual(
            len(resolved), _REG_REF_RESOLVED_FLOOR,
            f"reg_ref 링크 해석 {len(resolved)}건 < 하한 {_REG_REF_RESOLVED_FLOOR}건 — "
            "자료실 카탈로그 변경(문서 코드 소실·개명)으로 용어사전 링크가 사라졌을 "
            "가능성이 있다. 현재 무링크 라벨:\n  " + "\n  ".join(lost_labels))

    def test_reg_ref_unresolved_labels_are_known(self):
        unresolved = {c["label"] for c in self.chips if not c["url"]}
        new_unresolved = sorted(unresolved - _REG_REF_KNOWN_UNRESOLVED_LABELS)
        self.assertLessEqual(
            len(unresolved), _REG_REF_UNRESOLVED_LABEL_CAP,
            f"무링크 고유 라벨 {len(unresolved)}종 > 상한 {_REG_REF_UNRESOLVED_LABEL_CAP}종 "
            "— 새로 무링크가 된 라벨:\n  " + "\n  ".join(new_unresolved))

    def test_reg_ref_link_families_each_have_links(self):
        # 전체 합계 하한(377) 만 보면 한 계열이 통째로 죽어도(예: eu_gmp.json 이 통째로
        # 비거나 code 필드 형식이 바뀌어 EU GMP 조항이 전부 무매치가 돼도) 덩치 큰 다른
        # 계열이 합계를 채워 통과해버릴 수 있다(eu_gmp 만 163/377 = 전체의 43%). 계열별로
        # 따로 봐야 "한 계열 통째 손실"을 잡는다.
        fam_resolved = collections.Counter()
        for c in self.chips:
            if not c["url"]:
                continue
            fam = _classify_reg_ref_family(c["label"])
            if fam:
                fam_resolved[fam] += 1
        for fam, floor in sorted(_REG_REF_FAMILY_FLOORS.items()):
            self.assertGreaterEqual(
                fam_resolved[fam], floor,
                f"{fam} 계열 reg_ref 링크 {fam_resolved[fam]}건 < 하한 {floor}건 — "
                f"web/data/library/{fam}.json 카탈로그가 통째로 손상됐거나 code 형식이 "
                "바뀌었을 가능성")

    def test_reg_ref_urls_are_https_and_wellformed(self):
        for c in self.chips:
            url = c["url"]
            if not url:
                continue
            self.assertTrue(url.startswith("https://"),
                             f"reg_ref URL 이 https:// 로 시작하지 않음: {c['label']!r} -> {url!r}")
            self.assertNotRegex(url, r"\s",
                                 f"reg_ref URL 에 공백 포함: {c['label']!r} -> {url!r}")

    # ── [2026-09-03 조항 착지] 단일 21 CFR 조항 → 내부 사례 화면 ────────────────
    def test_single_cfr_sections_get_internal_cases_href(self):
        """`21 CFR 211.192` 형태는 전부 내부 착지 경로를 갖는다.

        고치려는 결함: 관련 조항 링크가 **전부 사이트 밖으로만** 나갔다(실측 503건 중
        469건 링크·내부 0). 국문 사용자가 조항을 누르면 영문 법령으로 떠나 "이 조항으로
        실제 어떤 지적이 나왔나"를 볼 길이 없었다."""
        singles = [c for c in self.chips
                   if re.match(r"^21 CFR \d{3}\.\d+[a-z]?$", c["label"])]
        self.assertGreater(len(singles), 0, "단일 조항 표기가 하나도 없다 — 표본 자체가 깨졌다")
        linked = 0
        for c in singles:
            href = c.get("cases_href", "")
            if not href:
                # 사례 3건 미만이라 조항 페이지가 없는 경우 — 무링크가 정답이다.
                continue
            linked += 1
            self.assertTrue(href.startswith("findings/clause/"),
                            f"착지가 조항 페이지가 아님: {c['label']!r} -> {href!r}")
        self.assertGreater(linked, 0, "단일 조항 중 조항 페이지로 가는 링크가 하나도 없다")

    def test_ambiguous_cfr_labels_have_no_internal_href(self):
        """구간(`211.160–211.194`)·Part 표기는 대상 조항이 하나로 안 정해지므로 무링크.

        틀린 링크가 무링크보다 나쁘다 — _reg_ref_url 의 기존 규율을 그대로 따른다."""
        for c in self.chips:
            label = c["label"]
            if not label.startswith("21 CFR"):
                self.assertEqual(c.get("cases_href", ""), "",
                                 f"21 CFR 이 아닌데 내부 경로가 생김: {label!r}")
                continue
            if not re.match(r"^21 CFR \d{3}\.\d+[a-z]?$", label):
                self.assertEqual(c.get("cases_href", ""), "",
                                 f"단일 조항이 아닌데 내부 경로가 생김: {label!r} -> {c.get('cases_href')!r}")

    def test_internal_cases_href_section_matches_label(self):
        """경로에 실린 조항 번호가 라벨의 조항 번호와 정확히 같다(엉뚱한 조항 착지 금지)."""
        for c in self.chips:
            href = c.get("cases_href") or ""
            if not href:
                continue
            section = href.rstrip("/").rsplit("/", 1)[1].replace("-", ".")
            self.assertIn(section, c["label"],
                          f"착지 조항이 라벨과 다름: {c['label']!r} -> {href!r}")


# [C2] 용어사전→사례 링크 렌더 문구 안의 내부 운영 개념어 노출 검사 대상(예시 나열,
# CLAUDE.md "내부 운영 개념(Tier·QA칩·GRM 내부 용어)을 화면에 노출하지 마라" 불가침 규칙의
# 이 링크 한정 적용). 단어 경계 매치라 "qa" 가 다른 한글/영문 단어 부분문자열에 오탐하지
# 않는다(카드 텍스트 자체가 짧은 고정 문구+숫자라 애초에 오탐 여지가 거의 없다).
_GLOSSARY_CASE_LINK_JARGON_TERMS = ("tier", "qa", "scope_status", "raw_signal", "findings", "signal_tier")


class WebGlossaryCaseLinkGuardTest(unittest.TestCase):
    """[C2] 용어사전→사례 링크(glossary_cases.json → /glossary/ "이 용어로 검색되는 지적사례
    N건 보기" 링크) 가 거짓말하거나 조용히 사라지는 걸 막는 가드.

    이 링크는 화면에 건수를 적는다("1,468건"). 링크를 눌렀을 때 다른 검색어로 가면 그
    숫자는 거짓말이 된다. 데이터 파일이 비거나 용어 id 가 어긋나도 화면은 멀쩡해
    보인다(링크만 조용히 사라진다) — 그래서 "몇 건 렌더됐다"는 사실 자체를 세어 데이터
    (items/excluded) 와 정확히 대조한다(하한이 아니라 등식).

    C1 이 병렬로 web/render.py·web/templates/glossary.html 을 고치는 중이므로 이 클래스는
    두 파일을 절대 편집하지 않고 읽기만 한다(관측 전용). build_glossary_view 의
    case_q/case_findings/case_count_label/case_href 계약(명세)을 신뢰해 그 필드를
    직접 조회한다 — case_href 는 rel_root 접두 이전의 원시 필드값이라
    "findings/index.html?q=" 로 시작해야 정상이다(템플릿은 그 앞에 rel_root 만 붙인다,
    render.py build_glossary_view 참조)."""

    @classmethod
    def setUpClass(cls):
        cls.terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))
        cases_raw = json.loads(render.GLOSSARY_CASES_FILE.read_text(encoding="utf-8"))
        cls.items = cases_raw.get("items") or []
        cls.excluded = cases_raw.get("excluded") or []
        cls.term_ids = [t["id"] for t in cls.terms]
        cls.term_id_set = set(cls.term_ids)

        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_glcase_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "glossary" / "index.html").read_text(encoding="utf-8")

        # 뷰모델(공개 경로): 프로덕션 호출부(render.py render_site)와 같은 함수를 같은
        # 정본 데이터로 돌린다. reg_ref_catalogs 는 이 가드와 무관해 None(WebGlossaryDeepFieldsTest
        # 관례와 동일 — 카탈로그 부재도 build_glossary_view 가 지원하는 정식 하위호환 경로).
        cls.view = render.build_glossary_view(cls.terms, None, render.load_glossary_cases())
        cls.view_by_id = {t["id"]: t for g in cls.view["groups"] for t in g["terms"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        # 0건 가드(명세 8) — items(또는 excluded) 가 애초에 비어 있으면 아래 "모든 항목에
        # 대해 X 다" 류 단언들은 빈 집합에 대한 전칭 단언이 되어 항상 참으로 조용히
        # 통과해버린다(검사 대상 자체가 사라진 것을 "정상"으로 오판). 이 클래스의 모든
        # 테스트 앞에 걸어 어떤 메서드도 그 함정을 피해가지 못하게 한다.
        self.assertGreater(
            len(self.items), 0,
            "glossary_cases.json items 가 0건 — 사례 링크 검사 대상 자체가 없다"
            "(빈 집합에 대한 단언은 항상 참이라 이 상태로는 가드가 무의미하다)")
        self.assertGreater(
            len(self.excluded), 0,
            "glossary_cases.json excluded 가 0건 — 제외 판정 검사 대상 자체가 없다"
            "(빈 집합에 대한 단언은 항상 참이라 이 상태로는 가드가 무의미하다)")

    def _article_block(self, term_id: str) -> str:
        marker = f'<article class="gl-term" id="{term_id}"'
        start = self.html.index(marker, 0)
        end = self.html.index("</article>", start)
        return self.html[start:end]

    def test_every_glossary_term_is_decided(self):
        # glossary.json 200개 id 각각이 items 또는 excluded 에 정확히 한 번 나타나야 한다.
        counts = collections.Counter(
            [i["id"] for i in self.items] + [e["id"] for e in self.excluded])
        missing = [tid for tid in self.term_ids if counts.get(tid, 0) == 0]
        duplicated = sorted(tid for tid, c in counts.items() if c > 1 and tid in self.term_id_set)
        self.assertEqual(
            missing, [],
            f"glossary.json 에는 있지만 glossary_cases.json 에 결정(items/excluded)이 없는 id "
            f"{len(missing)}건: {missing}")
        self.assertEqual(
            duplicated, [],
            f"glossary_cases.json items/excluded 에 중복 등장한 id {len(duplicated)}건: {duplicated}")

    def test_case_items_reference_existing_terms(self):
        # items·excluded 의 모든 id 가 glossary.json 에 실재해야 한다(고아 참조 0).
        orphan_items = sorted(i["id"] for i in self.items if i["id"] not in self.term_id_set)
        orphan_excluded = sorted(e["id"] for e in self.excluded if e["id"] not in self.term_id_set)
        self.assertEqual(
            orphan_items, [],
            f"glossary_cases.json items 가 glossary.json 에 없는 id 를 참조(고아): {orphan_items}")
        self.assertEqual(
            orphan_excluded, [],
            f"glossary_cases.json excluded 가 glossary.json 에 없는 id 를 참조(고아): {orphan_excluded}")

    def test_case_items_are_wellformed(self):
        # q 비어있지 않음 · findings >= 1 · documents >= 1. 0건짜리가 items 에 있으면 실패
        # ("0건 보기" 링크는 만들지 않는다는 규칙의 강제 — build_glossary_view 는 이 게이트를
        # 신뢰하고 findings==0 이면 case_href 를 만들지 않을 뿐, items 데이터 자체의 형식
        # 위반은 여기서 잡는다).
        bad = []
        for it in self.items:
            tid = it.get("id")
            q = it.get("q")
            findings = it.get("findings")
            documents = it.get("documents")
            if not (isinstance(q, str) and q.strip()):
                bad.append((tid, f"q={q!r}"))
            if not (isinstance(findings, int) and not isinstance(findings, bool) and findings >= 1):
                bad.append((tid, f"findings={findings!r}"))
            if not (isinstance(documents, int) and not isinstance(documents, bool) and documents >= 1):
                bad.append((tid, f"documents={documents!r}"))
        self.assertEqual(bad, [], f"items 형식 위반(q 비어있음/findings<1/documents<1): {bad}")

    def test_case_links_render_at_expected_count(self):
        # 렌더된 사례 링크 개수 == len(items). 상수 하한이 아니라 정확히 일치해야 한다
        # (데이터에 있는데 안 그려지면 배선이 깨진 것이고, 데이터보다 더 그려지면 excluded
        # 나 고아 id 에도 링크가 새는 것이다 — 등식만이 두 방향 결손을 동시에 잡는다).
        rendered = self.html.count('class="gl-case-a"')
        self.assertEqual(
            rendered, len(self.items),
            f"렌더된 사례 링크 {rendered}건 != glossary_cases.json items {len(self.items)}건 — "
            "배선이 깨졌거나(적음) excluded/고아 id 로 링크가 샜다(많음)")

    def test_case_link_href_is_encoded_search_url(self):
        from urllib.parse import quote as _expected_quote

        for it in self.items:
            tid = it["id"]
            t = self.view_by_id.get(tid)
            self.assertIsNotNone(t, f"{tid} 가 build_glossary_view 뷰모델에 없음")
            expected_href = f"findings/index.html?q={_expected_quote(it['q'], safe='')}"
            self.assertTrue(
                t["case_href"].startswith("findings/index.html?q="),
                f"{tid} case_href 가 'findings/index.html?q=' 로 시작하지 않음: {t['case_href']!r}")
            self.assertEqual(
                t["case_href"], expected_href,
                f"{tid} case_href 불일치(검색어가 링크와 어긋남 — 눌렀을 때 다른 결과가 나온다): "
                f"{t['case_href']!r} != {expected_href!r}")
            # 렌더된 HTML 에도 같은 href 값이 그대로 실렸는지(템플릿이 rel_root 만 앞에
            # 붙이고 값 자체는 변형하지 않는지) — 뷰모델 대조만으론 배선 유실을 못 잡는다.
            self.assertIn(
                f'href="../{expected_href}"', self.html,
                f"{tid} 의 사례 링크 href 가 렌더에 없음(값이 유실됐거나 변형됨): {expected_href!r}")

        # 한글 검색어가 실제로 인코딩된 채 나가는지 최소 1건 이상 직접 확인(예: 품질관리).
        korean_items = [it for it in self.items if any(ord(ch) > 0x7F for ch in it["q"])]
        self.assertGreater(
            len(korean_items), 0,
            "items 에 한글 검색어 표본이 없어 인코딩 가드를 검증할 수 없다")
        for it in korean_items:
            raw_href_fragment = f'href="../findings/index.html?q={it["q"]}"'
            self.assertNotIn(
                raw_href_fragment, self.html,
                f"{it['id']} 의 한글 검색어 '{it['q']}' 가 URL 인코딩되지 않은 채 href 에 그대로 나감")

    def test_excluded_terms_have_no_case_link(self):
        # excluded 에 있는 용어의 카드에는 사례 링크가 없어야 한다(id 앵커 기준 확인).
        leaked = [e["id"] for e in self.excluded if "gl-case-a" in self._article_block(e["id"])]
        self.assertEqual(
            leaked, [],
            f"excluded 판정을 받은 용어인데 사례 링크가 렌더됨(제외가 지켜지지 않음): {leaked}")

    def test_excluded_entries_state_a_reason(self):
        # excluded 의 모든 항목에 비어있지 않은 reason 이 있어야 한다(왜 뺐는지 기록 없이
        # 조용히 빼는 걸 막는다).
        bad = [e.get("id") for e in self.excluded
               if not (isinstance(e.get("reason"), str) and e.get("reason").strip())]
        self.assertEqual(bad, [], f"reason 이 비어있는(또는 공백뿐인) excluded 항목: {bad}")

    def test_case_link_wording_has_no_internal_jargon(self):
        # 렌더된 링크 문구(anchor 여는 태그의 '>' 다음부터 '</a>' 까지 — href 속성은 제외,
        # href 에는 "findings/index.html?q=..." 가 정상적으로 들어있어 문자열 검사에
        # 섞으면 오탐한다)에 Tier·QA·scope_status·raw_signal·findings·signal_tier 같은
        # 내부 운영 개념어가 노출되지 않는지 확인한다.
        anchors_inner = re.findall(
            r'<a class="gl-case-a" href="[^"]*">(.*?)</a>', self.html, flags=re.S)
        self.assertEqual(
            len(anchors_inner), len(self.items),
            f"gl-case-a 앵커 텍스트 추출 {len(anchors_inner)}건 != items {len(self.items)}건 "
            "— 정규식이 실제 마크업과 어긋났을 수 있다(추출 자체가 못 미더우면 이하 단언이 무의미)")
        offenders = []
        for text in anchors_inner:
            for term in _GLOSSARY_CASE_LINK_JARGON_TERMS:
                if re.search(rf'(?<![\w-]){re.escape(term)}(?![\w-])', text, flags=re.I):
                    offenders.append((term, text))
        self.assertEqual(
            offenders, [],
            f"사례 링크 문구에 내부 운영 개념어가 노출됨(사용자에게 GRM 내부 용어가 보임): {offenders}")


class WebGlossaryAliasGuardTest(unittest.TestCase):
    """[A3] "FDA 표현으로 검색하면 그 용어에 닿는가" 가드.

    용어사전은 유럽·ICH 어휘로 쓰였는데 실제 지적사항은 미국 FDA 문서가 대부분이다.
    aliases([A1] 동의어, glossary.json 정본 — 이 클래스는 그 데이터를 수정하지 않는다)는
    사람이 코퍼스 실측으로 하나씩 판정해 커밋한 값이다(58개 용어·95개 동의어, 2026-08-04).
    이 가드는 "aliases 필드가 존재한다"가 아니라 "그 필드가 실제로 검색을 열어준다"를
    검사한다 — 나중에 누가 동의어를 지우면 어떤 FDA 표현이 못 닿게 됐는지 이름을 대며
    실패해야 한다(_FDA_ALIAS_PROBES 참조, 파일 상단).

    A1/C1 이 병렬로 web/render.py·web/templates/glossary.html 을 고치는 중이므로 이
    클래스는 두 파일을 절대 편집하지 않고 읽기만 한다(관측 전용 —
    WebGlossaryCaseLinkGuardTest 와 동일한 관례). build_glossary_view 의 "aliases"
    (표시용·화면 감춤 필터 적용)/"search"(검색용·동의어 전량 포함) 계약을 신뢰해 그
    필드를 직접 조회한다."""

    @classmethod
    def setUpClass(cls):
        cls.terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))
        cls.view = render.build_glossary_view(cls.terms)
        cls.view_by_id = {t["id"]: t for g in cls.view["groups"] for t in g["terms"]}

        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_glalias_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "glossary" / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        # 0건 가드(명세 5) — glossary.json 에 aliases 를 가진 용어가 애초에 0개면 아래
        # 테스트들은 빈 집합에 대한 전칭 단언이 되어 항상 참으로 조용히 통과해버린다
        # (검사 대상 자체가 사라진 것을 "정상"으로 오판) — WebGlossaryCaseLinkGuardTest·
        # WebGlossaryRegRefLinkGuardTest 와 동일한 관례. 이 클래스의 모든 테스트 앞에
        # 걸어 어떤 메서드도 그 함정을 피해가지 못하게 한다.
        alias_term_count = sum(1 for t in self.terms if t.get("aliases"))
        self.assertGreater(
            alias_term_count, 0,
            "glossary.json 에 aliases 를 가진 용어가 0개 — FDA 표현 도달 가드 자체가 "
            "검사할 대상이 없다(빈 집합에 대한 단언은 항상 참이라 이 상태로는 가드가 "
            "무의미하다)")

    def _article_block(self, term_id: str) -> str:
        marker = f'<article class="gl-term" id="{term_id}"'
        start = self.html.index(marker, 0)
        end = self.html.index("</article>", start)
        return self.html[start:end]

    def test_fda_expressions_reach_their_terms(self):
        # 구현 전 실측(2026-08-03/04): _FDA_ALIAS_PROBES 의 15개 FDA 표현으로 검색하면
        # 도달 0/15 였다. 판정은 클라이언트(assets/glossary.js apply())와 같은 방식 —
        # 검색어.lower() 가 t["search"] 의 부분문자열인지(단어경계 아님, JS indexOf 와
        # 동일 의미론 — 파일 상단 주석에 근거 인용).
        misses = []
        for query, expected_id in _FDA_ALIAS_PROBES:
            t = self.view_by_id.get(expected_id)
            if t is None:
                misses.append(f"{query!r} -> {expected_id!r}(용어 id 자체가 뷰모델에 없음)")
                continue
            if query.lower() not in t["search"]:
                misses.append(f"{query!r} -> {expected_id!r}(search={t['search']!r})")
        self.assertEqual(
            misses, [],
            "FDA 표현이 해당 용어에 안 닿는다(그 이름으로 검색해도 안 나온다) — 검색어와 "
            "기대 용어 id:\n  " + "\n  ".join(misses))

    def test_alias_is_searchable_even_when_hidden_from_display(self):
        # backup·out of trend 는 표제어와 하이픈·공백 차이뿐이라 화면 표시 목록([A1]
        # _glossary_alias_norm 판정)에서는 빠진다 — 하지만 감춤은 화면 전용이라 검색
        # 문자열에는 항상 전량 남아 있어야 한다. 두 조건(표시 목록에 없음 + 검색엔 있음)을
        # 다 확인해야 "정말 감춰진 채로도 검색되는지"가 증명된다 — display 확인 없이
        # search 만 보면 애초에 감춰지지 않은 평범한 케이스를 검사하고 있을 수도 있다.
        for query, term_id in _HIDDEN_DISPLAY_ALIAS_PROBES:
            q = query.lower()
            t = self.view_by_id[term_id]
            self.assertNotIn(
                q, [a.lower() for a in t["aliases"]],
                f"{term_id} 의 {query!r} 이 표시 목록(aliases)에 남아 있다 — 이 테스트가 "
                "전제하는 '화면에서 감춰진 케이스'가 아니게 됐다(프로브 테이블을 다른 "
                "예시로 바꿔야 한다)")
            self.assertIn(
                q, t["search"],
                f"{term_id} 의 {query!r} 이 화면에서는 감춰졌는데 검색 문자열에도 없다 — "
                "감춤이 화면이 아니라 검색까지 막아버렸다(사용자가 이 표현으로 검색하면 "
                "0건이 된다)")

    def test_displayed_aliases_render_in_card(self):
        # _DISPLAYED_ALIAS_PROBES 3건이 실제로 해당 용어 카드 HTML 에 그려지는지 —
        # 뷰모델(t["aliases"])에 값이 있어도 템플릿이 안 그리면 사용자는 못 본다.
        from markupsafe import escape as _esc_alias
        for term_id, alias in _DISPLAYED_ALIAS_PROBES:
            block = self._article_block(term_id)
            self.assertIn(
                str(_esc_alias(alias)), block,
                f"{term_id} 카드 HTML 에 표시 대상 동의어 {alias!r} 가 안 보인다")

    def test_no_alias_collides_with_another_term(self):
        # glossary_lint.py(_validate_item_aliases, ALIAS_TERM_COLLISION/ALIAS_ALIAS_COLLISION)
        # 에도 같은 규칙이 있지만, 렌더 산출물 기준으로 한 번 더 막는다 — 검색이 뒤섞이면
        # 사용자가 틀린 답을 본다. 판정은 대소문자 무시 비교(클라이언트 필터가 검색어와
        # data-search 를 둘 다 소문자화해 비교하므로, lint 의 글자 그대로(대소문자 구분)
        # 비교보다 이쪽이 더 엄격하다 — lint 를 통과해도 여기서 걸릴 수 있는 의도된
        # 이중 방어).
        owners: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        for t in self.terms:
            for field_name in ("term_ko", "term_en"):
                v = t.get(field_name)
                if isinstance(v, str) and v:
                    owners[v.lower()].append((t["id"], field_name))
            for a in t.get("aliases") or []:
                if isinstance(a, str) and a:
                    owners[a.lower()].append((t["id"], "aliases"))

        collisions = []
        for t in self.terms:
            for a in t.get("aliases") or []:
                if not (isinstance(a, str) and a):
                    continue
                key = a.lower()
                for owner_id, owner_field in owners.get(key, []):
                    if owner_id == t["id"]:
                        continue
                    collisions.append(f"{t['id']}.aliases({a!r}) == {owner_id}.{owner_field}")
        self.assertEqual(
            collisions, [],
            "동의어가 다른 용어의 표제어(term_ko/term_en) 또는 동의어와 (대소문자 무시) "
            "동일하다 — 검색이 두 용어 사이에서 뒤섞여 사용자가 틀린 답을 본다:\n  "
            + "\n  ".join(collisions))


class WebLlmsTxtTest(unittest.TestCase):
    """llms.txt — AI 어시스턴트·AI 검색용 안내 파일.

    계약: ① 숫자는 렌더 입력에서 파생(문장에 박은 숫자는 낡는다 — sitemap 파생 원칙과
    동일) ② 링크는 전부 절대 URL 공개 페이지(admin 부재) ③ 최신 브리프 링크는 sitemap
    에도 있는 실제 발행본. byte 회귀는 SINGLE_GOLDENS 의 llms.expected.txt 가 지킨다.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_llms_"))
        out = cls._tmp / "single"
        _build_single(out)
        cls.txt = (out / "llms.txt").read_text(encoding="utf-8")
        cls.sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_counts_derive_from_render_inputs(self):
        terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))
        self.assertIn(f"용어 {len(terms)}어", self.txt,
                      "용어 수가 glossary.json 정본과 어긋남")
        n_docs = self.sitemap.count(f"<loc>{render.SITE_BASE_URL}/findings/doc/")
        self.assertIn(f"{n_docs:,}건 — 기관·연도별", self.txt,
                      "문서 수가 sitemap(같은 원천 facet_paths)과 어긋남")

    def test_links_are_absolute_public_and_valid(self):
        for path in ("/findings/", "/findings/docs/", "/findings/trends/",
                     "/glossary/", "/library/", "/guide/", "/quiz/", "/archive/"):
            self.assertIn(f"]({render.SITE_BASE_URL}{path})", self.txt,
                          f"핵심 링크 누락: {path}")
        self.assertNotIn("/admin/", self.txt)
        m = re.search(r"\]\((\S+?/briefs/\d{4}-\d{2}-\d{2}/)\)", self.txt)
        self.assertIsNotNone(m, "최신 브리프 링크 부재")
        self.assertIn(f"<loc>{m.group(1)}</loc>", self.sitemap,
                      "llms.txt 의 최신 브리프가 sitemap 에 없는 유령 URL")


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
        render.render_site(data, cls._tmp / "out", render_doc_pages=_DOC_PAGES_IN_TESTS)
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
        render.render_site(data, cls._tmp / "out", render_doc_pages=_DOC_PAGES_IN_TESTS)
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
        render.render_site(data, cls._tmp / "out", render_doc_pages=_DOC_PAGES_IN_TESTS)
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


class WebFda483InspectorLineTest(unittest.TestCase):
    """[실사관 표기 2026-07-30] `/findings/` 검색 화면과 동일 형식("실사관: A · B")을 브리프
    483 카드에도 낸다. card_scaffold 가 raw.fda483_inspectors 를 deterministic_detail.
    inspectors 로 무변형 통과시키고, render._card_view() 가 방어적으로 정제한다(리스트가
    아니면 무시·비문자열/공백 제거·strip·6개 절단) — findings.js sanitizeInspectorNames()
    와 동일 계약의 Python 복제본(_sanitize_inspector_names)."""

    def _render(self, detail_extra: dict) -> str:
        env = render._make_env()
        card = {
            "id": "f483-insp", "render_order": 1, "evidence_level": "B",
            "headline_target": "Acme 483", "agency": "FDA", "card_type": "FDA 483 실사 관찰",
            "deterministic_detail": {
                "type": "fda_483_observations", "count": 1,
                "observations": [{"number": "1", "deficiency": "x", "detail": ""}],
                **detail_extra,
            },
        }
        view = render._card_view(card)
        return env.get_template("partials/card.html").render(card=view)

    def test_inspector_line_rendered_when_present(self):
        h = self._render({"inspectors": ["Jose F Velez", "Ivis L Negron Torres"]})
        self.assertIn('<p class="dt-fu">실사관: Jose F Velez · Ivis L Negron Torres</p>', h)

    def test_no_inspector_line_when_key_absent(self):
        # 기존 카드 전부(raw.fda483_inspectors 미보유) — 요소 자체가 생성되지 않는다.
        h = self._render({})
        self.assertNotIn("실사관:", h)

    def test_inspector_line_absent_for_blank_or_invalid_input(self):
        # 리스트가 아님 / 빈 리스트 / 원소가 전부 비문자열·공백뿐 → 정제 결과 빈 리스트 →
        # 키 자체를 지운다(render._card_view) → 빈 라벨 없이 요소 미생성.
        for bad in (None, "Jose F Velez", 42, {}, [], [None, 123, "   ", ""]):
            with self.subTest(bad=bad):
                h = self._render({"inspectors": bad})
                self.assertNotIn("실사관:", h)

    def test_inspector_names_stripped_and_capped_at_six(self):
        names = [f" Name {i} " for i in range(8)]     # 8명 입력, 앞뒤 공백 포함
        h = self._render({"inspectors": names})
        self.assertIn(
            "실사관: Name 0 · Name 1 · Name 2 · Name 3 · Name 4 · Name 5</p>", h)
        self.assertNotIn("Name 6", h)
        self.assertNotIn("Name 7", h)

    def test_mixed_valid_and_invalid_elements_keep_only_valid_ones(self):
        h = self._render({"inspectors": ["Jose F Velez", "", "   ", None, 7, "A B"]})
        self.assertIn('<p class="dt-fu">실사관: Jose F Velez · A B</p>', h)

    def test_inspector_names_are_html_escaped(self):
        # Jinja autoescape(textContent 상당) — 각 이름을 개별 `{{ }}` 로 출력하므로
        # HTML 특수문자가 그대로 삽입되지 않는다(findings.js 의 createTextNode 와 동형 안전성).
        h = self._render({"inspectors": ["<script>alert(1)</script>", "A & B"]})
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", h)
        self.assertIn("A &amp; B", h)

    def test_non_fda483_detail_type_never_gets_inspector_line(self):
        # 방어적 입력 — gmp_deficiencies 등 다른 결정론 타입에 inspectors 가 섞여도
        # _card_view() 정제 분기는 fda_483_observations 카드에만 적용된다(비활성 통과).
        env = render._make_env()
        card = {
            "id": "gmp-x", "render_order": 1, "evidence_level": "B",
            "headline_target": "Acme GMP", "agency": "MFDS", "card_type": "GMP 실사",
            "deterministic_detail": {
                "type": "gmp_deficiencies", "count": 0, "severity_summary": {}, "rows": [],
                "inspectors": ["Should Not Render"],
            },
        }
        view = render._card_view(card)
        h = env.get_template("partials/card.html").render(card=view)
        self.assertNotIn("실사관:", h)


class WebRecallDetailRenderTest(unittest.TestCase):
    """[회수 계열 결정론 상세 2026-08-25] 회수 3종 상세 블록의 렌더 계약.

    회수 4종은 발행 카드의 26%(114장)를 차지하면서 부가층이 W3 인용 한 줄뿐이었다.
    수집기가 원천 레코드를 통째로 저장해 두고도 발행이 5개 필드만 쓰던 것이라, 상세는
    전부 **원천에 실재하는 값**이다(LLM 0·환각 0). 기존 483/NCR 블록과 같은 클래스만
    쓰므로 grm.css 추가는 없다 — 새 클래스가 새면 이 검사가 잡는다."""

    def _render(self, detail: dict, card_type: str = "Recall",
                merged_count: int = 1) -> str:
        env = render._make_env()
        card = {"id": "rc-1", "render_order": 1, "evidence_level": "A",
                "headline_target": "Acme Pharma", "agency": "FDA",
                "card_type": card_type, "deterministic_detail": detail,
                "merged_count": merged_count, "merged_items": []}
        return env.get_template("partials/card.html").render(card=render._card_view(card))

    def test_openfda_block_renders_korean_for_controlled_vocabulary(self):
        h = self._render({
            "type": "openfda_recall_detail",
            "status": "Ongoing", "status_ko": "진행 중",
            "initiation": "Voluntary: Firm initiated", "initiation_ko": "자진회수 (업체 착수)",
            "code_info": "Lot #: A26-0412",
            "timeline": [{"label": "회수 착수", "date": "2026-04-28"}]})
        self.assertIn("회수 상세", h)
        self.assertIn("진행상태 · 진행 중", h)
        self.assertIn("자진회수 (업체 착수)", h)
        self.assertIn("회수 착수 · 2026-04-28", h)
        self.assertIn("Lot #: A26-0412", h)

    def test_openfda_falls_back_to_original_when_no_korean(self):
        """통제어휘 미등재 값은 원문이 그대로 보인다 — 매핑이 낡아도 값이 안 사라진다."""
        h = self._render({"type": "openfda_recall_detail", "status": "Under Review"})
        self.assertIn("진행상태 · Under Review", h)

    def test_absent_fields_render_no_empty_labels(self):
        """★음성 검사 — 값이 없는 칸은 라벨째 안 나온다(빈 라벨 금지)."""
        h = self._render({"type": "openfda_recall_detail", "status": "Ongoing"})
        for label in ("처리 경과", "대상 로트", "회수 규모·범위", "제품 식별", "최초 통지"):
            self.assertNotIn(label, h)

    def test_merged_card_states_representative_scope(self):
        """병합 대표의 값이 사건 전체로 읽히면 그게 곧 오보다 — 범위를 문장으로 밝힌다."""
        h = self._render({"type": "openfda_recall_detail", "status": "Ongoing",
                          "code_info": "Lot #: A26-0412"}, merged_count=7)
        self.assertIn("7개 품목을 한 건으로 묶었다", h)
        self.assertIn("대표 품목 1건", h)
        self.assertIn("대표 품목 기준", h)          # summary 라벨에도 표기
        # 비병합 카드에는 안내문 자체가 없다.
        solo = self._render({"type": "openfda_recall_detail", "status": "Ongoing"})
        self.assertNotIn("한 건으로 묶었다", solo)

    def test_mfds_block_shows_enforcement(self):
        """`ENFRC_YN` — 자진회수와 회수명령을 가르는 신호. 43장 내내 발행된 적이 없었다."""
        h = self._render({"type": "mfds_recall_detail", "enforcement": "회수명령 (강제)",
                          "item_seq": "200812345"}, card_type="회수·판매중지")
        self.assertIn("회수명령 (강제)", h)
        self.assertIn("품목기준코드 — 200812345", h)

    def test_hc_block_renders_action_bilingual_slot(self):
        en = "Stop using the affected lots."
        h = self._render({"type": "hc_recall_detail", "action": en},
                         card_type="Recall(HC)")
        self.assertIn("권고 조치 (What you should do)", h)
        self.assertIn("원문 · Health Canada", h)
        self.assertIn(en, h)
        self.assertNotIn("국문 해석", h)            # `action_ko` 없으면 국문 열 미생성
        h_ko = self._render({"type": "hc_recall_detail", "action": en,
                             "action_ko": "해당 로트 사용을 중지한다."}, card_type="Recall(HC)")
        self.assertIn("국문 해석", h_ko)
        self.assertIn("해당 로트 사용을 중지한다.", h_ko)

    def test_recall_blocks_introduce_no_new_css_classes(self):
        """★상세 블록이 쓰는 클래스는 전부 grm.css 에 이미 있다.

        483/NCR 형제와 같은 마크업을 재사용한다는 설계 전제를 검사로 고정한다 — 새 클래스가
        섞이면 스타일 없는 요소가 조용히 발행된다(초록 CI 뒤의 시각 결함)."""
        css = pathlib.Path(render.ASSETS_DIR, "grm.css").read_text(encoding="utf-8")
        defined = set(re.findall(r"\.([A-Za-z][\w-]*)", css))
        blocks = [
            {"type": "openfda_recall_detail", "status": "Ongoing", "status_ko": "진행 중",
             "code_info": "x", "quantity": "y", "firm_location": "z",
             "timeline": [{"label": "회수 착수", "date": "2026-04-28"}],
             "product": [{"label": "성분명", "value": "LISINOPRIL"}]},
            {"type": "mfds_recall_detail", "enforcement": "자진회수", "item_seq": "1",
             "std_cd": "2", "bizrno": "3"},
            {"type": "hc_recall_detail", "ingredient": "a", "dosage_form": "b",
             "action": "c", "action_ko": "d"},
        ]
        for detail in blocks:
            with self.subTest(detail=detail["type"]):
                # 병합 안내문까지 포함해 검사한다(그 문단도 기존 클래스만 써야 한다).
                h = self._render(detail, merged_count=3)
                start = h.find('<details class="block detail">')
                self.assertGreaterEqual(start, 0, "상세 블록이 렌더되지 않았다")
                block = h[start:h.find("</details>", start)]
                used = set()
                for m in re.finditer(r'class="([^"]+)"', block):
                    used.update(m.group(1).split())
                missing = sorted(c for c in used
                                 if c not in defined and not c.startswith("ti"))
                self.assertEqual(missing, [], f"grm.css 에 없는 클래스: {missing}")


class WebSanitizeInspectorNamesUnitTest(unittest.TestCase):
    """render._sanitize_inspector_names() 순수 함수 단위 테스트 — findings.js
    sanitizeInspectorNames() 계약 복제본(리스트가 아니면 무시·비문자열/공백 제거·strip·
    6개 절단). 어떤 입력에도 예외를 던지지 않는다."""

    def test_valid_list_stripped(self):
        self.assertEqual(
            render._sanitize_inspector_names([" Jose F Velez ", "Ivis L Negron Torres"]),
            ["Jose F Velez", "Ivis L Negron Torres"])

    def test_non_list_inputs_return_empty(self):
        for bad in (None, "name", 1, 1.5, {}, {"a": 1}, True):
            with self.subTest(bad=bad):
                self.assertEqual(render._sanitize_inspector_names(bad), [])

    def test_non_string_and_blank_elements_dropped(self):
        self.assertEqual(
            render._sanitize_inspector_names(["Jose", None, 7, "", "   ", "Velez"]),
            ["Jose", "Velez"])

    def test_capped_at_six(self):
        names = [f"N{i}" for i in range(10)]
        self.assertEqual(render._sanitize_inspector_names(names),
                          ["N0", "N1", "N2", "N3", "N4", "N5"])

    def test_empty_list_returns_empty(self):
        self.assertEqual(render._sanitize_inspector_names([]), [])


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

    11차(2026-08-26, 사용자 요청 "홈 과밀 정리")의 랜딩 섹션 순서·CTA 가드도 이 클래스가
    지킨다: 히어로 → 기능 3종(#why, soft) → Card Anatomy → 참여 존(#engage, 마지막 섹션)
    → 뉴스레터 → AI 고지. 걷어낸 것: 단독 WHY 섹션·기능 03/05/06·This Week 콜아웃(수치
    3중 반복). 브리프행 CTA 는 히어로("이번 주 소식 읽기") 1개뿐."""

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
        # 확정 재배치(12차, 2026-08-27 — 발견 허브): 히어로 → 기능 3종(soft, id=why —
        # footer '소개' 앵커 승계) → 데이터 존(#records) → 참여 존(#engage) → 뉴스레터
        # → AI 고지. Card Anatomy 는 12차에서 걷어냈다 — 홈 실측 최대 블록(1,000px)이며
        # 유일한 무CTA 순수 설명이었고, 히어로 라이브 이슈 카드·#why 미리보기 칩·실제
        # 브리프가 같은 내용을 이미 보여준다(3회 설명 → 1회).
        order = [
            'class="wrap hero"',
            '<section class="section soft" id="why">',
            '<section class="section" id="records">',
            '<section class="section" id="engage">',
        ]
        pos = [self.landing.index(m) for m in order]
        self.assertEqual(pos, sorted(pos), "랜딩 섹션 순서가 확정안과 다름")
        # 걷어낸 섹션이 되살아나지 않는다 — 단독 WHY 섹션·This Week 콜아웃(수치 3중
        # 반복)·Card Anatomy 쇼케이스(12차).
        self.assertNotIn('id="this-week"', self.landing)
        self.assertNotIn("Why GRM", self.landing)
        self.assertNotIn('class="callout"', self.landing)
        self.assertNotIn(">Card Anatomy</span>", self.landing)
        self.assertNotIn('class="showcase"', self.landing)

    def test_landing_features_are_exactly_three(self):
        # 기능 6종 → 3종(11차) — 카드 차원 기능(원문 연결·번역 병기·체크리스트)은 히어로
        # 라이브 이슈 카드와 실제 브리프 본문이 보여주므로 기능 그리드에 되살리지 않는다
        # (12차에서 Card Anatomy 를 걷어낸 뒤에도 같은 판단 — 홈은 설명이 아니라 실물로).
        self.assertEqual(self.landing.count('<div class="feat">'), 3)
        self.assertNotIn("원문 대비 한국어 번역", self.landing)
        self.assertNotIn("실무 맞춤형 점검 리스트", self.landing)
        self.assertNotIn("원문·출처 직접 연결", self.landing)

    def test_engage_zone_popular_then_quiz(self):
        # 참여 존(#engage) — 인기 카드가 먼저, 퀴즈 CTA 가 뒤(한 섹션 응집).
        zone = self.landing[self.landing.index('id="engage"'):]
        zone = zone[:zone.index("</section>")]
        self.assertIn('id="popular"', zone)
        self.assertIn('id="grm-popular"', zone)
        self.assertIn('class="quiz-cta"', zone)
        self.assertLess(zone.index('id="popular"'), zone.index('class="quiz-cta"'))

    def test_engage_zone_is_the_closing_section(self):
        # 11차 정리 — This Week 콜아웃 철거 후 참여 존(#engage)이 content 블록의 마지막
        # 섹션(=뉴스레터 직전)이다. 새 최상위 섹션을 그 뒤에 덧붙이지 않는다.
        tail = self.landing[self.landing.index('<section class="section" id="engage">'):]
        tail = tail[:tail.index("</main>")]
        self.assertEqual(tail.count("<section"), 1)

    def test_brief_cta_exactly_one(self):
        # CTA 중복 정리(불가침 상한) — 같은 브리프로 가는 버튼은 히어로("이번 주 소식
        # 읽기") 1개만(헤더 상시 '이번 주 소식' 버튼은 base 공통이라 별도). 구 하단
        # 콜아웃 CTA("이번 주 소식 보기")·인기 카드 빈 상태의 이동 버튼은 되살리지 않는다.
        self.assertEqual(self.landing.count("이번 주 소식 읽기"), 1)
        self.assertNotIn("이번 주 소식 보기", self.landing)
        self.assertNotIn("이번 주 카드 보러 가기", self.landing)


# ── 랜딩 자료실 카드(#engage 존) ──────────────────────────────────────────────
class WebLandingLibraryCardTest(unittest.TestCase):
    """랜딩 자료실 진입 카드 — 확정 배치를 흔들지 않으면서 수치가 낡지 않아야 한다.

    수치를 템플릿에 손으로 적으면 카탈로그가 늘 때마다 반드시 낡는다(이용안내가 실제로
    그렇게 낡았다 — 2026-07-25). 그래서 render 가 카탈로그에서 계산해 넘기고, 이 테스트가
    그 계산값과 실제 카탈로그의 일치를 고정한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_libcard_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.catalogs = render.load_library()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_card_is_inside_the_engage_zone(self):
        """확정 섹션 순서(…→#engage→#this-week→뉴스레터)를 바꾸지 않는다 —
        새 최상위 <section> 이 아니라 기존 참여 존 안의 카드다."""
        engage = self.landing.split('id="engage"', 1)[1].split('id="this-week"', 1)[0]
        self.assertIn('class="quiz-cta lib-cta"', engage, "자료실 카드가 #engage 존 안에 없다")
        self.assertIn("자료실 열기", engage)

    def test_counts_are_derived_from_the_catalogs_not_hardcoded(self):
        expected_catalogs = len(self.catalogs)
        expected_items = sum(v["count"] for v in self.catalogs)
        self.assertIn(f"카탈로그 {expected_catalogs}종", self.landing)
        self.assertIn(f"총 {expected_items}건", self.landing)
        tpl = (WEB_DIR / "templates" / "landing.html").read_text(encoding="utf-8")
        self.assertNotIn(f"카탈로그 {expected_catalogs}종", tpl,
                         "템플릿에 수치를 하드코딩하면 카탈로그가 늘 때 낡는다")

    def test_cta_targets_the_library_not_a_brief(self):
        """브리프행 CTA 1개 불가침(11차) — 이 카드는 /library/ 로만 간다."""
        self.assertIn('href="library/index.html">자료실 열기', self.landing)
        self.assertEqual(self.landing.count("이번 주 소식 읽기"), 1)
        self.assertNotIn("이번 주 소식 보기", self.landing)

    def test_card_is_omitted_when_there_is_no_library(self):
        """데이터가 없으면 빈 카드를 그리지 않는다(빈 상태 광고 금지).

        cover 등 나머지 컨텍스트는 실제 빌드와 동일하게 채우고 library 만 비운다 —
        가짜 컨텍스트로 렌더하면 템플릿의 다른 부분에서 터져 계약 검증이 흐려진다."""
        briefs = render.load_briefs(SINGLE_FIXTURES)
        latest = max(briefs, key=lambda b: b["brief"].get("publish_date", ""))
        env = render._make_env()
        env.globals.update({"css_ver": "0", "archivejs_ver": "0", "findingsjs_ver": "0",
                            "trendsjs_ver": "0", "firmjs_ver": "0", "glossaryjs_ver": "0",
                            "quizjs_ver": "0"})
        html = env.get_template("landing.html").render(
            page_title="t", rel_root="", nav_active="home", latest_slug="x",
            description="d", canonical="c", json_ld="",
            cover=render._cover_context(latest, 1),
            library={"catalog_count": 0, "item_count": 0})
        # 스코프 <style> 의 .lib-cta 규칙은 늘 있으므로 **마크업**으로 판정한다.
        self.assertNotIn('class="quiz-cta lib-cta"', html)
        self.assertNotIn("자료실 열기", html)


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
            render.render_site(SINGLE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
            landing_on = (out / "index.html").read_text(encoding="utf-8")
        finally:
            render.SUPABASE_URL, render.SUPABASE_ANON_KEY = u0, k0
            shutil.rmtree(tmp, ignore_errors=True)
        import re as _re
        m = _re.search(r'assets/popular\.js\?v=([0-9a-f]{8})"', landing_on)
        self.assertIsNotNone(m, "popular.js 캐시버스팅 해시 미발견(활성 렌더)")
        # 활성 렌더에서도 정적 빈 상태 마크업은 그대로(런타임 교체는 popular.js 소관).
        self.assertIn('id="grm-popular"', landing_on)

    def test_watchlist_banner_env_gated(self):
        """[성장 2차] 워치리스트 진입 배너 — 회원 기능이라 reactions 게이트 안(/me 푸터
        링크 선례). env-off(기본 테스트 빌드·골든)엔 무흔적, env-on 렌더에서만 나타나며
        CTA 는 검색 페이지로 간다(업체 프로파일은 key 없이 열면 막다른 화면)."""
        self.assertNotIn("watch-cta", self.landing)
        self.assertNotIn("관심 업체의 새 지적사항", self.landing)
        u0, k0 = render.SUPABASE_URL, render.SUPABASE_ANON_KEY
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_watchcta_on_"))
        try:
            render.SUPABASE_URL = "https://rfwixqqdljpmtjdlblct.supabase.co"
            render.SUPABASE_ANON_KEY = "anon-key"
            out = tmp / "out"
            render.render_site(SINGLE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
            landing_on = (out / "index.html").read_text(encoding="utf-8")
        finally:
            render.SUPABASE_URL, render.SUPABASE_ANON_KEY = u0, k0
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn("watch-cta", landing_on)
        self.assertIn("관심 업체의 새 지적사항, 메일로 받아보세요.", landing_on)
        # [존 재편 2026-08-26] CTA 목적지가 검색 페이지 → 업체 조회 페이지로 바뀌었다.
        # 예전 우회의 근거는 "업체 프로파일은 key 없이 열면 막다른 화면"이었는데, 그
        # 페이지가 이제 이름으로 찾는 랜딩을 갖는다(그 전제가 사라졌다).
        self.assertIn('findings/firm/index.html">업체 조회하러 가기', landing_on)
        # 뉴스레터 사다리(newsletter.py)가 이 앵커로 직링크한다 — 파일 간 계약을 여기서 고정.
        self.assertIn('id="watchlist"', landing_on)

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


# ── 문의 및 제안 — env-gate·자산 배선·JS/마이그레이션 계약 ──────────────────────────
class WebFeedbackTest(unittest.TestCase):
    """'문의 및 제안' 피드백 계층(061 user_feedback) — 진입 링크(푸터 '안내' 열)·모달은
    feedback.js 가 전부 런타임 주입(비골든·JS 미실행이면 흔적 0)이라 여기선 셸 배선·
    env-gate·소스/마이그레이션 계약만 검증한다.

    쓰기 경로 불가침: anon 은 RPC(feedback_submit)로만 쓴다(060 funnel_bump 관례 동형).
    category·status 어휘는 061 CHECK 와 클라이언트가 같아야 한다 — 모르는 값은 RPC·DB 가
    거부(폴백 금지)하므로 어긋나면 제출/트리아지가 조용히 전부 실패한다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_feedback_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.feedback_js = (WEB_DIR / "assets" / "feedback.js").read_text(encoding="utf-8")
        cls.admin_js = (WEB_DIR / "assets" / "admin.js").read_text(encoding="utf-8")
        cls.migration = (WEB_DIR / "migrations" / "061_user_feedback.sql").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_env_gated_like_reactions(self):
        # 기본(env-off) 빌드엔 무흔적 — 골든 byte-diff 0 계약(reactions 선례 동형).
        self.assertNotIn("feedback.js", self.landing)
        self.assertNotIn("grm-fb-", self.landing)
        u0, k0 = render.SUPABASE_URL, render.SUPABASE_ANON_KEY
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_feedback_on_"))
        try:
            render.SUPABASE_URL = "https://rfwixqqdljpmtjdlblct.supabase.co"
            render.SUPABASE_ANON_KEY = "anon-key"
            out = tmp / "out"
            render.render_site(SINGLE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
            landing_on = (out / "index.html").read_text(encoding="utf-8")
        finally:
            render.SUPABASE_URL, render.SUPABASE_ANON_KEY = u0, k0
            shutil.rmtree(tmp, ignore_errors=True)
        import re as _re
        m = _re.search(r'assets/feedback\.js\?v=([0-9a-f]{8})"', landing_on)
        self.assertIsNotNone(m, "feedback.js 캐시버스팅 해시 미발견(활성 렌더)")
        # cfg 에 프로덕션 호스트가 실린다 — 비프로덕션(프리뷰) 제출을 운영자로 표식하는 근거.
        self.assertIn('data-host="', landing_on)

    def test_feedback_js_copied_to_dist(self):
        built = (self.single / "assets" / "feedback.js").read_bytes()
        src = (WEB_DIR / "assets" / "feedback.js").read_bytes()
        self.assertEqual(built, src, "feedback.js 가 dist/assets 에 verbatim 복사되지 않음")

    def test_feedback_js_writes_via_rpc_only(self):
        self.assertIn("rest/v1/rpc/feedback_submit", self.feedback_js)
        # 테이블 직접 REST/supabase-js 접근 금지 — RPC 단일 쓰기 경로.
        self.assertNotIn("/rest/v1/user_feedback", self.feedback_js)
        self.assertNotIn('from("user_feedback")', self.feedback_js)

    def test_category_vocabulary_matches_migration(self):
        cats = {"usability", "correction", "feature", "other"}
        for c in cats:
            self.assertIn('"%s"' % c, self.feedback_js)
        import re as _re
        m = _re.search(r"category in \(([^)]*)\)", self.migration)
        self.assertIsNotNone(m, "061 CHECK 의 category 화이트리스트 미발견")
        self.assertEqual(set(_re.findall(r"'([a-z]+)'", m.group(1))), cats)

    def test_feedback_js_tags_operator(self):
        # 운영자('grm-op')·비프로덕션 호스트(data-host 대조)는 p_operator 로 표식(#763 동형).
        self.assertIn("grm-op", self.feedback_js)
        self.assertIn("p_operator", self.feedback_js)
        self.assertIn("data-host", self.feedback_js)

    def test_email_requires_consent_in_both_layers(self):
        """이메일은 회신 동의가 있을 때만 저장된다 — 폼이 막고 DB 가 최종 방어선.

        폼만 막으면 계약이 클라이언트에 갇혀, 다른 호출 경로(직접 RPC)가 동의 없는
        이메일을 그대로 넣을 수 있다. 두 층이 같이 있어야 한다."""
        self.assertIn("p_consent", self.feedback_js)
        self.assertIn("consent.checked", self.feedback_js)
        # DB: 컬럼 제약(이메일이 있으면 동의도 참) + RPC 의 동의 없는 이메일 폐기.
        self.assertIn("user_feedback_email_needs_consent", self.migration)
        self.assertIn("check (email is null or contact_consent)", self.migration)
        self.assertIn("not coalesce(p_consent, false)", self.migration)

    def test_status_vocabulary_matches_migration(self):
        """트리아지 상태 4종이 admin.js·마이그레이션에서 같아야 한다 — admin 만 늘리면
        update 가 DB CHECK 에 걸려 조용히 실패한다."""
        import re as _re
        m = _re.search(r"status text not null default 'new'\s*check \(status in \(([^)]*)\)", self.migration)
        self.assertIsNotNone(m, "061 CHECK 의 status 화이트리스트 미발견")
        db = set(_re.findall(r"'([a-z_]+)'", m.group(1)))
        self.assertEqual(db, {"new", "in_progress", "done", "dismissed"})
        block = self.admin_js.split("var FEEDBACK_STATUS = {", 1)[1].split("};", 1)[0]
        client = set(_re.findall(r'"?([a-z_]+)"?\s*:\s*\[', block))
        self.assertEqual(client, db, "admin.js 상태 어휘가 061 CHECK 와 다름")

    def test_ticket_number_is_returned_and_shown(self):
        # RPC 가 접수번호(bigint)를 돌려주고 화면이 그 번호를 보여준다(접수 지칭 가능).
        self.assertIn("returns bigint", self.migration)
        self.assertIn("returning id into v_id", self.migration)
        self.assertIn("grm-fb-ticket", self.feedback_js)
        self.assertIn("접수번호", self.feedback_js)

    def test_migration_write_path_is_locked(self):
        self.assertIn("enable row level security", self.migration)
        self.assertIn("revoke all on public.user_feedback from anon", self.migration)
        self.assertIn("security definer", self.migration)
        self.assertIn("grant execute on function public.feedback_submit", self.migration)


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
            render.render_site(SINGLE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
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
            render.render_site(SINGLE_FIXTURES, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
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

    def test_weekly_count_matches_the_lint_gate(self):
        """노출 상한(render)과 생성 상한(quiz_lint)이 갈라지면 조용한 유실이 생긴다.

        quiz.js 는 이번 주 세트를 render.WEEKLY_QUIZ_COUNT 개로 slice 한다.  lint 가 그보다
        많은 문항을 통과시키면 초과분은 어떤 주에도 화면에 뜨지 않는다(사람이 만든 문항이
        조용히 사라짐).  두 상수를 한 곳에 모을 수 없으므로(quiz_lint 는 무의존 정책) 값이
        어긋나는 순간 CI 가 실패하게 고정한다.
        """
        import quiz_lint                                        # REPO_ROOT 는 sys.path 에 있다
        self.assertEqual(quiz_lint.WEEKLY_QUIZ_COUNT, render.WEEKLY_QUIZ_COUNT)
        self.assertLessEqual(quiz_lint.WEEKLY_QUIZ_MIN, quiz_lint.WEEKLY_QUIZ_COUNT)

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
out.other = f(o, 4, 202629);                        // 타 주차 문항은 폴백에서도 제외
var all = mk(); all.forEach(function (it) { it.week = "202629"; });
out.overflow = f(all, 4, 202629);                   // 지정 초과 → 뱅크 순 상위 count
var past = mk(); past.forEach(function (it, i) { if (i < 6) it.week = "202628"; });
out.pastOnly = f(past, 4, 202629);                  // 이번 주 지정 0 → legacy 만으로 폴백
var allPast = mk(); allPast.forEach(function (it) { it.week = "202628"; });
out.allPast = f(allPast, 4, 202629);                // legacy 가 아예 없으면 빈 세트
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
        # 타 주차(202630) 문항은 폴백 후보에서도 빠진다 — legacy pool 계약(addendum §3.2).
        # poolE 는 idx3 이 빠져 6건이 되고 baseE=mod(607887,6)=3 → idx4 가 뽑힌다.
        self.assertEqual(out["other"], [4, 5, 8, 10])
        # 전 문항 지정 → 뱅크 순 상위 4.
        self.assertEqual(out["overflow"], [0, 1, 2, 3])
        # 이번 주 지정이 없는 주(생성 스킵·머지 전) — 과거 주차 문항이 "이번 주"로
        # 재등장하지 않는다. 실제로 2026-08-03 오전 세트에 2주 전 q-202630-01 이 들어갔던
        # 회귀를 여기서 고정한다.
        # legacy = idx6~11(easy 6·7, normal 8~11) → baseE=mod(607887,2)=1 → 7,6 ·
        # baseN=mod(202629,4)=1 → 9,10.
        self.assertEqual(out["pastOnly"], [6, 7, 9, 10])
        for idx in out["pastOnly"]:
            self.assertGreaterEqual(idx, 6, "week 를 가진 과거 문항이 폴백에 섞였습니다")
        # legacy pool 이 아예 비면 조용히 과거 문항으로 채우지 않고 빈 세트를 낸다
        # (화면은 "지난 문항으로 복습" 문구 + 전체 보기로 안내 — 거짓 세트를 만들지 않는다).
        self.assertEqual(out["allPast"], [])


# ── 주간 퀴즈 학습 루프(13차) — 복원·완주 요약·오답노트·재도전·필터 ──────────────
class WebQuizLearningLoopTest(unittest.TestCase):
    """정적 계약만 고정한다(동작은 quiz.js 소관·브라우저 검증).

    고정 대상은 "깨져도 화면이 멀쩡해 보이는 것"이다 — 필터가 참조하는 `data-source`가
    빠지면 필터는 조용히 0건을 내고, 저장 키가 성장 정본과 같아지면 서버 동기화가
    선택값을 0|1 로 납작하게 만들어 데이터가 조용히 사라진다.
    """

    @classmethod
    def setUpClass(cls):
        cls.quiz_js = (WEB_DIR / "assets" / "quiz.js").read_text(encoding="utf-8")
        cls.growth_js = (WEB_DIR / "assets" / "growth.js").read_text(encoding="utf-8")
        cls.golden = (GOLDEN_DIR / "quiz.expected.html").read_text(encoding="utf-8")

    def test_every_card_exposes_source_type_for_the_filter(self):
        bank = json.loads(render.QUIZ_FILE.read_text(encoding="utf-8"))
        rendered = re.findall(r'data-source="([a-z]+)"', self.golden)
        self.assertEqual(len(rendered), len(bank), "카드 수와 data-source 수가 다릅니다")
        self.assertEqual(rendered, [q["source_type"] for q in bank])
        # 필터 칩이 거는 값은 실제 렌더된 값 안에 있어야 한다(칩이 항상 0건이 되는 것 방지).
        for value in ("glossary", "brief"):
            self.assertIn(value, set(rendered), f"필터 칩 {value!r}가 걸릴 카드가 없습니다")

    def test_result_panel_and_filters_ship_as_hidden_static_skeleton(self):
        for marker in (
            '<div class="qz-filters" id="grm-qz-filters"',
            '<section class="qz-result" id="grm-qz-result"',
            'id="grm-qz-result-score"',
            'id="grm-qz-wrong-list"',
            'id="grm-qz-retry"',
            'id="grm-qz-share"',
            'id="grm-qz-empty"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.golden)
        # JS 미로드 시 빈 껍데기가 보이면 안 된다 — 전부 hidden 으로 나간다.
        for block in ("grm-qz-filters", "grm-qz-result", "grm-qz-wrong", "grm-qz-empty"):
            fragment = self.golden[self.golden.index(f'id="{block}"'):]
            self.assertIn("hidden", fragment[:fragment.index(">") + 1], f"{block} 이 hidden 이 아닙니다")
        # 점수·오답 목록은 런타임 주입 — 서버가 값을 굳혀 보내지 않는다(결정론 유지).
        self.assertIn('id="grm-qz-result-score"></p>', self.golden)
        self.assertIn('id="grm-qz-wrong-list"></ul>', self.golden)

    def test_quiz_picks_never_share_the_growth_storage_key(self):
        """복원 저장이 성장 정본 키를 건드리면 서버 동기화가 값을 납작하게 만든다.

        growth-sync.js 의 sanitizeWeeks 가 `q[id]` 를 0|1 로 정규화하므로 "내가 고른 보기"를
        그 스키마에 얹으면 동기화 한 번에 사라진다. 두 저장소의 분리를 정적으로 못 박는다.
        """
        # 따옴표까지 포함해 본다 — 키로 "쓰는" 것만 금지이고, 왜 분리했는지 설명하는
        # 주석의 언급까지 막으면 검사가 실제 위험이 아닌 낱말을 쫓게 된다.
        self.assertIn('"grm-quiz-picks-v1"', self.quiz_js)
        self.assertNotIn('"grm-gurumi-growth"', self.quiz_js)
        self.assertIn('"grm-gurumi-growth"', self.growth_js)    # 정본은 growth.js 한 곳

    def test_unsolved_filter_uses_the_growth_read_window(self):
        """quiz.js 는 localStorage 를 직접 뒤지지 않고 growth.js 의 조회창만 쓴다."""
        self.assertIn("window.GRM_GROWTH", self.growth_js)
        self.assertIn("solvedIds", self.growth_js)
        self.assertIn("window.GRM_GROWTH", self.quiz_js)
        # 조회창이 없거나 깨져도 화면은 살아야 한다(전건 숨김 금지).
        self.assertIn("if (!solved) return true;", self.quiz_js)

    def test_score_is_derived_from_card_state_not_accumulated(self):
        """증가 카운터를 되살리면 복원·재도전에서 화면과 숫자가 다시 어긋난다."""
        self.assertNotIn("answered++", self.quiz_js)
        self.assertNotIn("correct++", self.quiz_js)
        self.assertIn('data-correct', self.quiz_js)


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
        self.assertIn('addLink(_t("이미 계정이 있어요 · 로그인"), "login")', self.js)
        # 펫 CTA(가입 의도 분명) → 가입 직행 + reactions.js 미로드 시 기존 헤더 위임 폴백.
        self.assertIn('window.GRM_AUTH.open({ mode: "signup" })', self.sync_js)
        self.assertIn('querySelector(".grm-auth .grm-acct-login")', self.sync_js)

    def test_signup_progress_restore_contract(self):
        self.assertIn('var SIGNUP_KEY = "grm-signup-progress-v1";', self.js)
        self.assertIn("var resume = loadSignupProgress();", self.js)
        self.assertIn('setMode("confirm");', self.js)
        # 성공(세션 성립)·명시적 이탈에서 진행 상태를 지운다 — 유령 복원 0.
        self.assertIn("clearSignupProgress(); closeLogin();", self.js)
        self.assertIn('addLink(_t("다른 이메일로 가입"), "signup"', self.js)
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


class WebSourceCopyConsistencyTest(unittest.TestCase):
    """[재발 방지 가드 2026-07] 새 규제 소스를 추가할 때 코드(수집기·DB)만 고치고 사이트
    설명·마퀴 갱신을 빠뜨리던 문제를 CI 에서 잡는다 — EU/영국 GMP 비준수(EudraGMDP·MHRA)
    편입 후 findings 계열 카피에 소스가 누락됐던 사례(2026-07)의 회귀 잠금."""

    def test_footer_sources_match_landing_marquee(self):
        """[손목록 정합 2026-08-12] 수집 소스는 두 곳에 손으로 적혀 있다 — 랜딩 마퀴와 전
        페이지 푸터. 푸터에는 EudraGMDP·ISPE 가 빠져 있어 랜딩이 광고하는 12개와 어긋나
        있었다(두 목록을 서로 검사하는 가드가 없어 아무 소리도 나지 않았다). 집합이
        일치해야 한다 — 한쪽만 고치면 여기서 걸린다."""
        # 마퀴는 영문 축약(MFDS), 푸터는 국문(식약처)으로 같은 기관을 적는다 — 표기 차이는
        # 의도된 것이라 정규화하고 **집합**만 비교한다(별칭을 늘리려면 여기 한 줄).
        ALIAS = {"MFDS": "식약처"}
        # 마퀴는 줄바꿈 방지로 `Health&nbsp;Canada` 처럼 엔티티를 쓴다(템플릿 원문 문자열).
        def norm(s):
            s = s.replace("&nbsp;", " ").replace("\xa0", " ").strip()
            return ALIAS.get(s, s)
        landing = (WEB_DIR / "templates" / "landing.html").read_text(encoding="utf-8")
        base = (WEB_DIR / "templates" / "base.html").read_text(encoding="utf-8")
        track = re.search(r'class="track">(.*?)</div>', landing, re.S)
        self.assertIsNotNone(track, "마퀴 track 을 찾지 못함")
        marquee = {norm(s.replace("\xa0", " ").strip()) for s in re.findall(r"<span>([^<]+)</span>", track.group(1))}
        # [i18n 2단계] 템플릿 원문은 {{ _("수집 소스") }} 로 감싸져 있다.
        foot = re.search(r'<h5>\{\{ _\("수집 소스"\) \}\}</h5>(.*?)</div>', base, re.S)
        self.assertIsNotNone(foot, "푸터 '수집 소스' 블록을 찾지 못함")
        footer = set()
        # [i18n 2단계] 한글이 섞인 span 은 {{ _("…") }} 로 감싸져 있다 — 안쪽 원문만 꺼낸다.
        i18n_span = re.compile(r'^\{\{ _\("(.*)"\) \}\}$')
        for chunk in re.findall(r"<span>([^<]+)</span>", foot.group(1)):
            im = i18n_span.match(chunk)
            if im:
                chunk = im.group(1)
            footer.update(norm(x.strip()) for x in chunk.replace("\xa0", " ").split("·"))
        self.assertEqual(marquee, footer,
                         f"마퀴에만: {sorted(marquee - footer)} / 푸터에만: {sorted(footer - marquee)}")

    def test_marquee_source_count_matches_chips(self):
        """랜딩 마퀴 '수집 대상 — N sources' 의 N 이 실제 표기 소스 칩 수와 일치해야 한다
        — 마퀴에 소스를 넣고 카운트를 안 고치거나(불일치), 없는 소스를 광고하던(TGA 사고)
        재발을 막는다."""
        tpl = (WEB_DIR / "templates" / "landing.html").read_text(encoding="utf-8")
        m = re.search(r"수집 대상 — (\d+) sources", tpl)
        self.assertIsNotNone(m, "마퀴 '수집 대상 — N sources' 문구를 찾지 못함")
        declared = int(m.group(1))
        track = re.search(r'class="track">(.*?)</div>', tpl, re.S)
        self.assertIsNotNone(track, "마퀴 track 을 찾지 못함")
        chips = re.findall(r"<span>([^<]+)</span>", track.group(1))   # 보이는 칩(비 aria-hidden)만
        self.assertEqual(declared, len(chips),
                         f"마퀴 카운트({declared}) ≠ 실제 칩 수({len(chips)}): {chips}")

    # ★[가드가 낡는 것을 막는 가드 2026-08-11] 아래 매핑은 findings 추출기 → 그 소스를
    # 가리키는 사이트 카피 키워드다. 옛 버전은 REQUIRED 를 손으로 적은 리스트로 뒀는데,
    # Health Canada 실사(문서 기준 2위)를 편입할 때 아무도 리스트를 갱신하지 않아 **가드가
    # 초록인 채로 카피만 낡았다**. 손으로 적은 목록은 반드시 낡는다 → 이제 목록을
    # findings_extractors.py 의 실제 배선에서 파생시키고, 매핑에 없는 추출기가 배선되면
    # 그 자체로 실패시킨다. 새 소스를 붙이면 여기 한 줄을 추가하기 전엔 CI 가 통과하지 않는다.
    EXTRACTOR_COPY_KEYWORDS = {
        "_from_fda_483_observations": "FDA 483",
        "_from_warning_letter": "Warning Letter",
        "_from_mfds_gmp": "식약처",
        "_from_mfds_admin_action": "식약처",
        "_from_mfds_recall": "식약처",
        "_from_eu_gmp_ncr": "GMP 비준수",      # EU(EudraGMDP) NCR
        "_from_mhra_gmp_ncr": "GMP 비준수",    # 영국(MHRA) NCR
        "_from_hc_inspection": "캐나다",       # Health Canada 실사
    }

    def test_extractor_dispatch_all_mapped_to_copy_keyword(self):
        """findings_extractors.py 에 배선된 추출기가 전부 카피 키워드에 매핑돼 있어야 한다 —
        새 소스를 붙이고 사이트 설명을 안 고치면 여기서 먼저 걸린다(아래 카피 검증의 입력)."""
        src = (WEB_DIR.parent / "findings_extractors.py").read_text(encoding="utf-8")
        wired = set(re.findall(r"findings\.extend\((_from_\w+)\(signal, raw, row\)\)", src))
        self.assertTrue(wired, "추출기 배선을 찾지 못함 — 파싱 패턴이 낡았는지 확인할 것")
        missing = wired - set(self.EXTRACTOR_COPY_KEYWORDS)
        self.assertFalse(
            missing,
            f"새 findings 추출기 {sorted(missing)} 가 배선됐는데 카피 키워드 매핑이 없다 — "
            "EXTRACTOR_COPY_KEYWORDS 에 대표 키워드를 추가하고 사이트 설명 6곳을 갱신할 것",
        )
        stale = set(self.EXTRACTOR_COPY_KEYWORDS) - wired
        self.assertFalse(stale, f"배선이 사라진 추출기가 매핑에 남아 있다: {sorted(stale)}")

    def test_findings_source_keywords_present_in_all_copy(self):
        """findings 계열(검색·업체·트렌드)의 소스 설명이 서로·메타와 어긋나지 않게 강제한다.
        검사 대상 키워드는 EXTRACTOR_COPY_KEYWORDS(실제 배선에서 파생)에서 나오고, 6개
        카피 위치(3 인트로/고지 + 3 메타 설명)가 전부 그 소스를 언급하는지 개별 검증한다 —
        소스 추가 시 수집기·DB 만 고치고 설명을 빠뜨리던 재발 방지."""
        REQUIRED = sorted(set(self.EXTRACTOR_COPY_KEYWORDS.values()))
        # ① 템플릿(인트로·고지) — 파일 전체 텍스트에 전부 존재해야 한다.
        for label, rel in (("findings 템플릿", "findings.html"),
                           ("firm 템플릿", "firm.html"),
                           ("trends 템플릿", "trends.html")):
            text = (WEB_DIR / "templates" / rel).read_text(encoding="utf-8")
            for kw in REQUIRED:
                self.assertIn(kw, text, f"{label} 에 소스 키워드 '{kw}' 누락 — 신규 소스 카피 갱신 필요")
        # ② render.py 메타 설명 3종 — 상수 본문을 개별 검증(하나만 고치고 나머지 빠뜨리는 것 방지).
        render_src = (WEB_DIR / "render.py").read_text(encoding="utf-8")
        for const in ("FINDINGS_DESCRIPTION", "TRENDS_DESCRIPTION", "FIRM_DESCRIPTION"):
            # [i18n 2단계] 값은 이제 N_("…") 로 감싸져 있다.
            m = re.search(const + r"\s*=\s*N_\((.*?)\)", render_src, re.S)
            self.assertIsNotNone(m, f"{const} 정의를 찾지 못함")
            body = m.group(1)
            for kw in REQUIRED:
                self.assertIn(kw, body, f"{const} 에 소스 키워드 '{kw}' 누락 — 신규 소스 카피 갱신 필요")


class WebNewSourceRenderTest(unittest.TestCase):
    """[신규 소스 3종 발행 경로 회귀 2026-07-25] EU GMP NCR·UK(MHRA) GMP NCR·ISPE.

    왜 이 클래스가 있나 — 세 소스는 `ENABLE_EU_GMP_NCR`(07-22)·`ENABLE_ISPE`(07-22)·
    `ENABLE_MHRA_GMP_NCR`(07-23) 로 **07-20 발행 이후에** 켜졌다. 즉 07-27 호가 이들이
    발행 파이프를 통과하는 **첫 주**인데, 수집기 골든(`tests/golden/*.webcard.json`)만
    있고 **렌더 경로 커버리지는 0** 이었다. 카드 타입이 늘 때 `card.html` 분기를 빠뜨리면
    상세 블록이 조용히 사라진다(게이트가 없어 발행은 되고 내용만 빈다) — 이 클래스가
    "분기가 실제로 그려지는가"를 고정한다.

    함께 고정하는 것: MHRA NCR 의 `document_id` 는 `Insp GMP 52165/19076958-0001` 처럼
    **공백·슬래시를 포함**한다(EudraGMDP/MHRA 등록부 원 표기). 이 id 는 `_card_anchor`
    를 거쳐 상세 article 의 HTML id·TOC href·search-index href 로 **동시에** 나가므로,
    렌더가 죽지 않고 세 곳이 같은 값으로 일치하는지 검증한다(2026-07-25 헤드리스 실측:
    앵커 점프 정상 — 사이트 JS 가 getElementById 경로만 쓴다).
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_ncr_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 실 수집기 산출 형태를 그대로 본뜬 최소 카드(값은 합성이나 키·타입은 동형).
    EU_DETAIL = {
        "type": "eu_gmp_ncr_statement",
        "authority_country": "Austria",
        "product_scope": "Human Medicinal Products",
        "operations": "Manufacture of active substance Diosmin",
        "nature": "Critical deficiency regarding data integrity of QC chromatographic records.",
        "action": "Prohibition of supply. Recall of affected batches from the EU market.",
        "additional": "The site may request a re-inspection after remediation.",
    }
    MHRA_DETAIL = {
        "type": "mhra_gmp_ncr_statement",
        "authority_country": "United Kingdom",
        "product_scope": "Human Medicinal Products",
        "operations": "Sterile manufacture of small volume parenterals",
        "nature": "Failure to maintain aseptic conditions in grade A filling zone.",
        "action": "GMP certificate withdrawn; UK supply suspended.",
        "additional": "Restricted to non-sterile operations pending re-inspection.",
    }
    MHRA_ID = "Insp GMP 52165/19076958-0001"

    def _brief(self):
        eu = _card(0, id="186143", agency="EMA", card_type="EU GMP 비준수",
                   type_tag="GMP 비준수", group_label="💊 합성의약품",
                   evidence_level="A", signal_tier=3, signal_label="High",
                   headline_target="Sichuan New Hawk Biotechnology Co. Ltd.",
                   title_issue="원료의약품 GMP 비준수", summary="EU NCR 요약.",
                   key_facts=["a"], implication="시사점.", checks=["점검"],
                   deterministic_detail=self.EU_DETAIL)
        uk = _card(1, id=self.MHRA_ID, agency="MHRA", card_type="UK GMP 비준수",
                   type_tag="GMP 비준수", group_label="🧬 바이오의약품",
                   evidence_level="A", signal_tier=3, signal_label="High",
                   headline_target="Geno Pharmaceuticals Private Limited",
                   title_issue="무균 공정 GMP 비준수", summary="UK NCR 요약.",
                   key_facts=["a"], implication="시사점.", checks=["점검"],
                   deterministic_detail=self.MHRA_DETAIL)
        ispe = _card(2, id="b10549ee6d97", agency="ISPE", card_type="규제 소식",
                     type_tag="News", group_label="▫️ 기타",
                     evidence_level="B", signal_tier=2, signal_label="Med",
                     headline_target="ISPE iSpeak", title_issue="업계 동향",
                     summary="ISPE 블로그 요약.", key_facts=["a"],
                     implication="시사점.", checks=["점검"])
        br = _minimal_brief("2026-07-27", cards=[eu, uk, ispe],
                            coverage={"intake_total": 3, "rendered": 3,
                                      "evidence": {"A": 2, "B": 1, "C": 0}})
        br["brief"]["agencies"] = ["EMA", "MHRA", "ISPE"]
        return br

    def _render(self):
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        br = self._brief()
        (data / "brief_web_2026-07-27.json").write_text(
            json.dumps(br, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
        return out, (out / "briefs" / "2026-07-27" / "index.html").read_text(encoding="utf-8")

    def test_eu_gmp_ncr_detail_block_rendered_verbatim(self):
        """EU NCR 상세 분기가 실제로 그려지고 원문이 절단·유실 없이 실린다."""
        _, html = self._render()
        self.assertIn("비준수 상세", html)
        self.assertIn("EudraGMDP", html)
        self.assertIn("발행 NCA · Austria", html)
        self.assertIn("비준수 운영항목", html)
        self.assertIn("위반내용 (Nature of non-compliance)", html)
        self.assertIn("당국 조치 (Action taken/proposed)", html)
        for key in ("operations", "nature", "action", "additional"):
            self.assertIn(self.EU_DETAIL[key], html,
                          f"EU NCR 상세 '{key}' 원문이 렌더에서 빠졌다")

    def test_mhra_gmp_ncr_detail_block_rendered_with_mhra_label(self):
        """UK NCR 은 EU 와 동형이되 출처 라벨이 MHRA 여야 한다(분기 뒤바뀜 방지)."""
        _, html = self._render()
        self.assertIn("발행기관 · MHRA", html)
        self.assertIn("원문 · MHRA", html)
        for key in ("operations", "nature", "action", "additional"):
            self.assertIn(self.MHRA_DETAIL[key], html,
                          f"MHRA NCR 상세 '{key}' 원문이 렌더에서 빠졌다")

    def test_ispe_card_renders_as_news(self):
        """ISPE 는 결정론 상세가 없는 소식 카드 — 상세 블록 없이도 정상 렌더된다."""
        _, html = self._render()
        self.assertIn("ISPE iSpeak", html)
        self.assertIn("ISPE 블로그 요약.", html)

    def test_mhra_card_id_with_space_keeps_anchor_consistent(self):
        """공백·슬래시가 든 document_id 도 렌더를 깨뜨리지 않고
        article id·search-index href 가 **같은 값**으로 일치한다(드리프트 0)."""
        out, html = self._render()
        self.assertIn(f'id="{self.MHRA_ID}"', html,
                      "MHRA 카드의 상세 앵커가 document_id 와 다르다")
        idx = json.loads((out / "assets" / "search-index.json").read_text(encoding="utf-8"))
        rows = idx["cards"] if isinstance(idx, dict) and "cards" in idx else idx
        hit = [r for r in rows if r.get("target") == "Geno Pharmaceuticals Private Limited"]
        self.assertEqual(len(hit), 1, "MHRA 카드가 검색 인덱스에 1건으로 안 잡힌다")
        self.assertTrue(hit[0]["href"].endswith("#" + self.MHRA_ID),
                        f"search-index href 가 앵커와 불일치: {hit[0]['href']}")

    def test_ocr_derived_483_block_is_labelled_as_ocr(self):
        """[OCR 출처 표기 2026-07-27] 판독물을 "원문 · FDA 483" 이라고 부르지 않는다."""
        obs = [{"number": "1", "deficiency": "Aseptic failure.", "detail": "Specifically, x.",
                "deficiency_ko": "무균 실패.", "detail_ko": "구체적으로, x."}]
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        br = self._brief()
        br["cards"][0]["deterministic_detail"] = {
            "type": "fda_483_observations", "count": 1, "observations": obs,
            "text_source": "ocr"}
        (data / "brief_web_2026-07-27.json").write_text(
            json.dumps(br, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
        html = (out / "briefs" / "2026-07-27" / "index.html").read_text(encoding="utf-8")
        self.assertIn("원문 · FDA 483 · OCR 판독", html)
        self.assertIn("스캔 원문 OCR 판독", html)
        self.assertIn("기계 판독(OCR)으로 옮겼다", html)

    def test_native_483_block_keeps_original_label(self):
        """텍스트층 산출 카드는 종전 라벨 그대로 — 골든 불변(additive)."""
        obs = [{"number": "1", "deficiency": "Aseptic failure.", "detail": "",
                "deficiency_ko": "무균 실패."}]
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        br = self._brief()
        br["cards"][0]["deterministic_detail"] = {
            "type": "fda_483_observations", "count": 1, "observations": obs}
        (data / "brief_web_2026-07-27.json").write_text(
            json.dumps(br, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
        html = (out / "briefs" / "2026-07-27" / "index.html").read_text(encoding="utf-8")
        self.assertIn("원문 · FDA 483", html)
        self.assertNotIn("OCR 판독", html)

    def test_ncr_detail_without_ko_stays_english_only(self):
        """`*_ko` 미보유 카드는 국문 블록이 아예 안 나온다(additive — 기존 발행분 불변)."""
        _, html = self._render()
        self.assertNotIn("국문 해석", html)

    def test_ncr_detail_renders_korean_alongside_original(self):
        """[국문 병기 2026-07-27] `*_ko` 가 있으면 원문(영문)과 국문이 **함께** 그려진다.

        이 블록은 도입 이래 영문 verbatim 만 내보내 한국어 이용자가 비준수 내용을 못 읽었다
        (2026-07-27 사용자 지적). 원문 보존은 불가침이므로 국문은 원문을 대체하지 않고 병기한다.
        """
        eu_ko = {
            "nature_ko": "QC 크로마토그래피 기록의 데이터 완전성에 관한 중대결함.",
            "action_ko": "공급 금지. EU 시장에서 해당 배치 회수.",
            "operations_ko": "원료의약품 Diosmin 제조",
            "additional_ko": "시정 후 재실사를 요청할 수 있다.",
        }
        mhra_ko = {"nature_ko": "A등급 충전구역 무균조건 유지 실패."}
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        br = self._brief()
        br["cards"][0]["deterministic_detail"] = {**self.EU_DETAIL, **eu_ko}
        br["cards"][1]["deterministic_detail"] = {**self.MHRA_DETAIL, **mhra_ko}
        (data / "brief_web_2026-07-27.json").write_text(
            json.dumps(br, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
        html = (out / "briefs" / "2026-07-27" / "index.html").read_text(encoding="utf-8")
        for ko in list(eu_ko.values()) + list(mhra_ko.values()):
            self.assertIn(ko, html, f"국문 번역이 렌더에서 빠졌다: {ko!r}")
        # 원문은 그대로 남아 있어야 한다 — 국문이 원문을 밀어내면 근거가 사라진다.
        for key in ("operations", "nature", "action", "additional"):
            self.assertIn(self.EU_DETAIL[key], html)
            self.assertIn(self.MHRA_DETAIL[key], html)
        # 번역이 없는 MHRA 필드는 국문 없이 원문만(부분 번역도 안전).
        self.assertNotIn("GMP 인증서 철회", html)

    # ── [WHOPIR 상세 2026-07-27] WHO 공개 실사보고서 구조 렌더 ──────────────
    WHOPIR_DETAIL = {
        "type": "whopir_report", "report_kind": "findings",
        "outcome": ("Based on the areas inspected, the manufacturer was considered "
                    "to be operating at an acceptable level of compliance."),
        "sections": [
            {"no": 1, "title": "Quality System",
             "text": "The quality manual was reviewed and found to be current."},
            {"no": 2, "title": "Production System",
             "text": "Line clearance was observed during the inspection."},
        ],
    }

    def _render_whopir(self, detail):
        data, out = self.tmp / "data", self.tmp / "out"
        data.mkdir(parents=True, exist_ok=True)
        br = self._brief()
        br["cards"][0]["deterministic_detail"] = detail
        (data / "brief_web_2026-07-27.json").write_text(
            json.dumps(br, ensure_ascii=False), encoding="utf-8")
        render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
        return (out / "briefs" / "2026-07-27" / "index.html").read_text(encoding="utf-8")

    def test_whopir_report_block_renders_outcome_and_sections(self):
        """종전엔 링크와 excerpt 뿐이라 보고서 구조가 통째로 유실됐다(2026-07-27 지적)."""
        html = self._render_whopir(self.WHOPIR_DETAIL)
        self.assertIn("실사보고서 상세", html)
        self.assertIn("원문 · WHOPIR", html)
        self.assertIn("항목 2", html)
        self.assertIn("실사 결론 (Inspection outcome)", html)
        self.assertIn("1. Quality System", html)
        self.assertIn("2. Production System", html)
        self.assertIn(self.WHOPIR_DETAIL["outcome"], html)
        for sec in self.WHOPIR_DETAIL["sections"]:
            self.assertIn(sec["text"], html, "항목 원문이 렌더에서 빠졌다")

    def test_whopir_korean_renders_alongside_original(self):
        detail = {**self.WHOPIR_DETAIL, "outcome_ko": "적합한 수준으로 운영된다고 판단됐다.",
                  "sections": [{**self.WHOPIR_DETAIL["sections"][0],
                                "title_ko": "품질 시스템",
                                "text_ko": "품질 매뉴얼을 검토했고 최신본이었다."},
                               self.WHOPIR_DETAIL["sections"][1]]}
        html = self._render_whopir(detail)
        self.assertIn("적합한 수준으로 운영된다고 판단됐다.", html)
        self.assertIn("1. 품질 시스템", html)              # 표제는 국문이 있으면 국문
        self.assertIn("품질 매뉴얼을 검토했고 최신본이었다.", html)
        # 원문은 그대로 — 국문이 원문을 밀어내면 근거가 사라진다.
        self.assertIn(self.WHOPIR_DETAIL["sections"][0]["text"], html)
        self.assertIn("2. Production System", html)        # 미번역 항목은 영문 표제 유지

    def test_whopir_reliance_report_lists_authorities_without_sections(self):
        """SRA/NRA 실사증거 의존 보고서는 항목 요약이 원문에 없다 — 만들어내지 않는다."""
        html = self._render_whopir({
            "type": "whopir_report", "report_kind": "reliance",
            "outcome": "Reliance was placed on the inspections listed below.",
            "sections": [],
            "reliance": [{"authority": "EDQM", "dates": "12-15 March 2025"}]})
        self.assertIn("인용 실사", html)
        self.assertIn("EDQM — 12-15 March 2025", html)
        # 항목이 0개면 요약 라벨에 건수 힌트를 붙이지 않는다("항목 0" 같은 빈 약속 금지).
        self.assertIn("· 원문 기반 · WHO</span>", html)

    def test_korean_middle_dot_is_not_a_bullet(self):
        """[가운뎃점 2026-07-27] 한국어에서 `·` 는 낱말을 잇는 정상 문장부호다.

        라이브 실측: WHOPIR 국문 "인원의 책임·권한·상호관계를 포함한 …" 이 세 항목으로
        찢어졌다. 실제 불릿은 " · " 처럼 공백에 둘러싸여 나오므로 그때만 끊는다.
        """
        ko = "인원의 책임·권한·상호관계를 포함한 시험실 조직구조는 조직도에 표시돼 있었다."
        self.assertEqual(render.split_detail_blocks(ko),
                         [{"kind": "para", "text": ko}])
        paren = "측정용 물질(표준물질·인증표준물질, 화학·생물학적 표준품)과 외부 서비스"
        self.assertEqual(len(render.split_detail_blocks(paren)), 1)

    def test_spaced_middle_dot_still_splits_as_bullet(self):
        """공백에 둘러싸인 `·` 는 여전히 불릿 — EU NCR 원문의 실제 마커."""
        blocks = render.split_detail_blocks(
            "The site was organized into 3 sections: · Chemical Analysis "
            "· Microbiological Analysis · IVD Testing")
        self.assertEqual([b["kind"] for b in blocks],
                         ["para", "item", "item", "item"])
        self.assertEqual(blocks[1]["text"], "Chemical Analysis")

    def test_new_sources_reach_search_index(self):
        """세 소스 전부 검색 인덱스에 편입된다(발행은 됐는데 검색에서 사라지는 것 방지)."""
        out, _ = self._render()
        idx = json.loads((out / "assets" / "search-index.json").read_text(encoding="utf-8"))
        rows = idx["cards"] if isinstance(idx, dict) and "cards" in idx else idx
        types = {r.get("card_type") for r in rows}
        for ct in ("EU GMP 비준수", "UK GMP 비준수"):
            self.assertIn(ct, types, f"검색 인덱스에 '{ct}' 카드가 없다")
        self.assertIn("ISPE", {r.get("agency") for r in rows})


# ── [존 재편 2026-08-26] 트렌드 존 3면 분할 + 도구 진입로 ─────────────────────
class WebZoneIaTest(unittest.TestCase):
    """이 클래스가 지키는 것은 "기능이 있는가"가 아니라 **"닿을 수 있는가"** 다.

    재편의 계기가 정확히 그것이었다. 홈에서 링크만 따라가 세어 보니(도달성 감사)
    nav·footer 에 있는 라우트는 전부 3,500장 이상에서 링크되는데, **완성돼 있던 도구
    셋의 정적 인바운드 링크는 자가점검 체크리스트 1장 · 업체 조회 0장 · 실사관 조회
    0장**이었다. 그 셋의 기능 테스트는 전부 통과하고 있었다 — 기능이 없었던 게 아니라
    **닿는 길이 없었고, 그걸 보는 테스트가 없었다.**

    그래서 여기서는 마크업이 있는지가 아니라 (1) 세 면이 서로 오갈 수 있는지,
    (2) 산출된 모든 페이지가 홈에서 링크만으로 도달되는지를 본다. (2)는 **손으로 적은
    라우트 목록이 아니라 실제 빌드 산출에서 파생**된다 — 손목록으로 지키는 가드는
    라우트가 하나 늘 때마다 조용히 낡는다(#712/#729 계열의 반복 교훈).
    """

    #: 홈에서 도달되지 않아도 되는 페이지와 그 근거. 여기에 무언가를 더할 때는
    #: **왜 사용자가 링크로 닿지 않아도 되는지**를 반드시 적는다.
    UNREACHABLE_OK = {
        "404.html": "존재하지 않는 경로에 서버가 띄우는 페이지 — 링크 대상이 아니다.",
        "en/404.html": "위와 같다. 영어 트리에서도 Cloudflare 가 상태코드로 띄운다.",
        "admin/index.html": "운영자 전용 콘솔 — 공개 링크를 두지 않는 것이 의도다.",
    }

    #: 세그먼트에 서는 면. [컨셉 재정의] '데이터 현황'은 세그먼트에서 내렸다 — 그 면이
    #: 답하는 것은 사용자가 하려는 일이 아니라 우리가 신뢰를 얻으려는 일이라 nav 여섯 탭
    #: 어느 job 에도 속하지 않는다. 라우트는 그대로이고(아래 test_three_faces_built),
    #: 두 면의 꼬리 각주가 그 페이지를 연다.
    ZONE_FACES = {
        "findings/trends/index.html": "trends",
        "findings/inspections/index.html": "inspections",
    }

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_zoneia_"))
        cls.single = cls._tmp / "single"
        # ★문서 단위 페이지를 **켠 채로** 짓는다(다른 클래스의 기본값과 다르다).
        #   도달성은 "링크가 실제로 이어지는가"를 재는 것이라, 속도 때문에 3천 장을 끄면
        #   그 페이지들을 거쳐 가는 경로(문서 → 용어사전 · 문서 → 분류 패싯)가 통째로
        #   사라져 멀쩡한 라우트가 고아로 보인다 — 실제로 이 가드를 처음 켰을 때 용어
        #   239장·패싯 76장이 그렇게 잡혔다. **속도 스위치 뒤에 가드 사각지대를 만들지
        #   않는다**(대량 페이지 도입 때 겪은 것과 같은 함정).
        _build_single(cls.single, doc_pages=True)
        cls.pages = {}
        for p in sorted(cls.single.rglob("*.html")):
            rel = p.relative_to(cls.single).as_posix()
            cls.pages[rel] = p.read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")
        cls.llms = (cls.single / "llms.txt").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ── 도달성(핵심 가드) ────────────────────────────────────────────────────
    @staticmethod
    def _links(page_rel, html):
        """page_rel 이 가리키는 같은 사이트 안의 .html 경로들(정규화된 상대경로)."""
        import posixpath
        out = set()
        base = posixpath.dirname(page_rel)
        for href in re.findall(r'href="([^"]+)"', html):
            if href.startswith(("http://", "https://", "mailto:", "#", "data:", "//")):
                continue
            tgt = href.split("#", 1)[0].split("?", 1)[0]
            if not tgt:
                continue
            tgt = posixpath.join(base, tgt)
            # ★순서 주의: normpath 가 끝의 '/'를 지운다. 먼저 index.html 을 붙이지 않으면
            #   `../findings/c/` 같은 **디렉터리 링크가 통째로 무시**되어, 실제로는 닿는
            #   페이지 수천 장이 고아로 잡힌다(이 가드를 처음 켰을 때 3,481장이 그랬다 —
            #   가드가 틀린 게 아니라 헬퍼가 틀렸다).
            if tgt.endswith("/"):
                tgt += "index.html"
            tgt = posixpath.normpath(tgt)
            if tgt.endswith(".html"):
                out.add(tgt.lstrip("./"))
        return out

    def test_every_built_page_is_reachable_from_home(self):
        """홈(index.html)에서 링크만 따라가 산출된 모든 페이지에 닿아야 한다.

        sitemap 에 있다는 것과 사람이 닿을 수 있다는 것은 다르다 — #717 에서 sitemap
        3,520장 중 홈 BFS 도달이 28장이었던 전례가 있다. 이 테스트는 그 감사를 상시
        가드로 굳힌 것이다."""
        seen, queue = set(), ["index.html"]
        while queue:
            cur = queue.pop()
            if cur in seen or cur not in self.pages:
                continue
            seen.add(cur)
            queue.extend(self._links(cur, self.pages[cur]))
        unreachable = sorted(set(self.pages) - seen - set(self.UNREACHABLE_OK))
        self.assertEqual(
            unreachable, [],
            "홈에서 링크로 닿지 않는 페이지가 있다(기능이 있어도 아무도 쓸 수 없다). "
            "의도된 것이라면 UNREACHABLE_OK 에 근거와 함께 등록하라: " + repr(unreachable))

    def test_no_broken_internal_links(self):
        """내부 링크가 산출되지 않은 경로를 가리키면 안 된다.

        ★findings/docs/ 이하는 예외다 — 문서 단위 페이지는 3천 장이 넘어 테스트 빌드에서
        기본으로 꺼져 있고(_DOC_PAGES_IN_TESTS), 그 상태에서는 존재하지 않는 것이 정상이다.
        전수 검증은 WebFindingsDocPageTest 가 켠 채로 한다."""
        broken = set()
        for rel, html in self.pages.items():
            for tgt in self._links(rel, html):
                if tgt.startswith("findings/docs/"):
                    continue
                if tgt not in self.pages:
                    broken.add((rel, tgt))
        self.assertEqual(sorted(broken), [], "깨진 내부 링크: " + repr(sorted(broken)[:10]))

    def test_tool_routes_linked_from_every_page(self):
        """도구 라우트는 footer 를 통해 전 페이지에서 닿아야 한다.

        '한 페이지에서만 링크됨'은 사실상 링크가 없는 것이다 — 자가점검 체크리스트가
        딱 그 상태(트렌드 페이지 본문 안 링크 1개)로 몇 주를 살았다. 인바운드가 전체
        페이지 수 수준인지를 본다(footer 배선이 살아 있다는 뜻).

        ★[다국어 3단계 2026-09-04] **언어 트리마다 따로 센다.** 영어 페이지의 footer 는
        영어 도구(`en/findings/...`)를 가리키므로, 두 트리를 한 통에 넣고 세면 한국어
        라우트의 인바운드가 전체 페이지 수에 못 미쳐 배선이 멀쩡한데도 실패한다. 불변식은
        "그 트리의 모든 페이지에서 그 트리의 도구에 닿는다"이지 트리를 섞은 총계가 아니다."""
        for prefix in ("", "en/"):
            pages = {rel: html for rel, html in self.pages.items()
                     if rel.startswith("en/") == bool(prefix)}
            self.assertTrue(pages, f"{prefix or 'ko'} 트리에 페이지가 없다")
            total = len(pages)
            for route in ("findings/checklist/index.html", "findings/firm/index.html",
                          "findings/inspector/index.html", "findings/inspections/index.html",
                          "findings/coverage/index.html"):
                target = prefix + route
                n = sum(1 for rel, html in pages.items()
                        if target in self._links(rel, html))
                self.assertGreaterEqual(
                    n, total - 5,
                    f"{target} 인바운드 {n}/{total} — footer 도구 열 배선이 끊겼다")

    # ── 존 3면 ───────────────────────────────────────────────────────────────
    def test_all_three_routes_built_even_though_segment_shows_two(self):
        """라우트는 셋 그대로다 — 세그먼트에서 내린 것과 페이지를 없앤 것은 다르다."""
        for rel in ("findings/trends/index.html", "findings/inspections/index.html",
                    "findings/coverage/index.html"):
            self.assertIn(rel, self.pages, f"{rel} 미산출")
        # 데이터 현황은 세그먼트에 자기 탭이 없다(활성 표시도 없다).
        cov = self.pages["findings/coverage/index.html"]
        self.assertIn('class="tr-seg"', cov)
        self.assertNotIn('aria-current="page"', cov)
        # 대신 두 면의 꼬리 각주가 이 페이지를 연다.
        for face in ("findings/trends/index.html", "findings/inspections/index.html"):
            self.assertIn("findings/coverage/index.html", self.pages[face])

    def test_segment_nav_links_all_faces_and_marks_active(self):
        for rel, seg in self.ZONE_FACES.items():
            html = self.pages[rel]
            self.assertIn('class="tr-seg"', html, f"{rel} 에 면 전환 세그먼트가 없다")
            for other in self.ZONE_FACES:
                href = "../../" + other.rsplit("/index.html", 1)[0] + "/index.html"
                self.assertIn(f'href="{href}"', html, f"{rel} → {other} 링크 없음")
            self.assertIn('aria-current="page"', html, f"{rel} 활성 탭 표시 없음")

    def test_cfg_declares_page_kind(self):
        """세 면이 trends.js 하나를 공유하므로, 어느 면인지를 셸이 말해야 한다.

        엘리먼트 유무만으로도 렌더는 안전하지만 **로딩 해제 시점**(그 면의 주 데이터가
        무엇인가)은 엘리먼트로 정할 수 없다."""
        for rel, seg in self.ZONE_FACES.items():
            self.assertIn(f'data-page="{seg}"', self.pages[rel])

    def test_moved_sections_left_the_findings_face(self):
        """지적 경향 면에서 나간 섹션들이 실제로 나갔고, 간 곳에 있다."""
        trends = self.pages["findings/trends/index.html"]
        inspections = self.pages["findings/inspections/index.html"]
        coverage = self.pages["findings/coverage/index.html"]
        for gone in ('id="tr-fda-block"', 'id="tr-heatmap-block"', 'id="tr-year"',
                     'id="tr-firms"', 'id="tr-source"', 'class="tr-divider"',
                     'id="tr-ff-form"'):
            self.assertNotIn(gone, trends, f"지적 경향 면에 {gone} 이 남아 있다")
        self.assertIn('id="tr-fda-block"', inspections)
        for moved in ('id="tr-heatmap-block"', 'id="tr-year"', 'id="tr-firms"',
                      'id="tr-source"'):
            self.assertIn(moved, coverage, f"데이터 현황 면에 {moved} 이 없다")

    def test_ranking_is_agency_scoped_not_summed(self):
        """[컨셉 재정의] 순위는 **고른 기관 안에서** 계산된다.

        재편 직후에는 최근 12개월/전 기간/해외vs미국 세 보기를 한 탭으로 합쳤는데, 그
        셋은 '기간·모집단'이 다른 축이었지 사용자가 묻는 축이 아니었다. 사용자가 묻는
        축은 **어느 기관 기준인가**이고, 그 축에서 순위가 실제로 갈린다(FDA 상위 5와
        식약처 상위 5가 하나도 겹치지 않는다)."""
        html = self.pages["findings/trends/index.html"]
        self.assertIn('id="tr-agency"', html)
        self.assertIn('id="tr-recent-cats"', html)
        # 다른 축(전 기간 누적·해외vs미국)은 이 면을 떠났다.
        self.assertNotIn('id="tr-cat"', html)
        self.assertNotIn('id="tr-zone-block"', html)
        cov = self.pages["findings/coverage/index.html"]
        self.assertIn('id="tr-cat"', cov)
        self.assertIn('id="tr-zone-block"', cov)

    def test_checklist_cta_promoted_on_cfr_section(self):
        html = self.pages["findings/trends/index.html"]
        self.assertIn('class="tr-cta"', html)
        self.assertIn('href="../../findings/checklist/index.html"', html)

    # ── 색인 정책 ────────────────────────────────────────────────────────────
    def test_sitemap_gains_new_faces_but_never_inspector(self):
        """실사관 페이지는 실명 집계라 sitemap 미등록·noindex 가 계약이다.

        조회 랜딩을 붙였다고 이 정책이 바뀌지는 않는다 — 내부 링크로는 열되 검색엔진에는
        계속 노출하지 않는다(둘은 서로 독립이다)."""
        base = render.SITE_BASE_URL
        self.assertIn(f"<loc>{base}/findings/inspections/</loc>", self.sitemap)
        self.assertIn(f"<loc>{base}/findings/coverage/</loc>", self.sitemap)
        self.assertNotIn("/findings/inspector/", self.sitemap)
        self.assertNotIn("/findings/inspector/", self.llms)
        self.assertIn("noindex", self.pages["findings/inspector/index.html"])

    # ── 조회 랜딩(037 계약 포함) ─────────────────────────────────────────────
    def test_firm_lookup_landing_present(self):
        html = self.pages["findings/firm/index.html"]
        self.assertIn('id="fp-lookup"', html)
        self.assertIn('id="fp-look-form"', html)
        src = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        self.assertIn('showState("lookup")', src)
        self.assertIn("findings_firm_search", src)

    def test_inspector_lookup_is_not_a_directory(self):
        """037 계약(2026-08-31 개정) — 이 랜딩이 '사람을 서열화하는 화면'이 되지 않게
        하는 장치들.

        개정 전 이 테스트는 "2글자 미만이면 결과 없음"·"결과 최대 8명"을 지켰다 — 그
        둘의 실제 목적은 **목록의 존재 자체를 막는 것**이었다. 037 2026-08-31 개정은
        그 전제를 뒤집는다: 사용자 결정 "순위를 매기지 말라는 거고, 가나다 순으로
        색인하면 됨"에 따라 이름순 전체 색인은 이제 **의도된 기능**이다(그래서 이
        테스트는 저 두 장치를 더 이상 요구하지 않는다). 037 이 금지한 (a)순위·비교와
        (c)원문 반환은 이번 개정과 무관하게 그대로 지킨다 — 아래 장치로 고정."""
        html = self.pages["findings/inspector/index.html"]
        self.assertIn('id="ip-lookup"', html)
        self.assertIn('class="ip-idx-h"', html, "이름순 색인 머리글이 렌더되지 않는다")
        src = (WEB_DIR / "assets" / "inspector.js").read_text(encoding="utf-8")
        # ① 037 개정으로 새로 허용된 것: 빈 질의 = 전체 A-Z 복귀(더 이상 목록화 방지
        #   게이트로 막지 않는다 — 서버 코호트 게이트(문서 5건 이상)만으로 충분하다).
        self.assertNotIn("q.length < 2", src)
        self.assertNotIn(".slice(0, 8)", src)
        # ② 정렬은 이름순뿐이어야 한다 — 건수 기준 정렬(오름차순이든 내림차순이든)이면
        #   그 자체가 순위표라 (a)를 어긴다.
        self.assertIn("localeCompare", src)
        self.assertNotIn("b.documents - a.documents", src)
        self.assertNotIn("a.documents - b.documents", src)
        # ③ ★가장 중요 — 색인이 소비하는 데이터 모양 자체에 건수(documents)가 없다.
        #   data.documents(프로파일 문서 목록)와 이름이 겹치므로 색인 전용 함수
        #   슬라이스 안에서만 본다(WebInspectorRenderTest.test_index_data_shape_
        #   never_carries_document_count 가 Node 실행으로 동일 계약을 한 번 더 증명).
        idx_fn = src[src.index("function buildIndexGroups(rows) {"):]
        idx_fn = idx_fn[:idx_fn.index("\n  }\n") + 4]
        self.assertNotIn(".documents", idx_fn)
        # ④ 코호트 게이트 고지(기존 고지 유지 — 워딩만 상단 힌트 문단으로 이동).
        self.assertIn("공개 문서 5건 이상 확인된 실사관만 이력을 제공합니다", html)
        # ⑤ 순위·비교 뉘앙스 어휘 금지(신설 문안에도 새로 스며들지 않는지).
        for phrase in ("가장 많은", "가장 활발", "주요 실사관", "활발한"):
            self.assertNotIn(phrase, html)

    def test_landing_watchlist_cta_points_at_firm_lookup(self):
        """랜딩 워치리스트 CTA 는 조회 페이지로 직행한다.

        재편 전에는 '업체 프로파일은 key 없이 열면 막다른 화면'이라 검색 페이지를 한 번
        거치게 했다. 그 전제가 사라졌으므로 우회도 사라져야 한다."""
        landing = self.pages["index.html"]
        if "watch-cta" not in landing:
            self.skipTest("reactions 게이트 off 빌드 — 워치리스트 CTA 자체가 없다")
        block = landing[landing.index("watch-cta"):]
        block = block[:block.index("</div></section>")]
        self.assertIn('href="findings/firm/index.html"', block)

    def test_doc_pages_link_firm_profile(self):
        """[발견 허브] firm_key 있는 문서 상세는 업체 프로파일 간선을 갖는다(nofollow).

        firm_key 는 처음부터 데이터에 있었는데 링크가 없어 3,145장이 업체 이력으로
        이어지지 못했다 — 간선 존재를 렌더 결과에서 확인한다(템플릿 소스가 아니라)."""
        docs = json.loads((WEB_DIR / "data" / "findings_docs.json")
                          .read_text(encoding="utf-8"))["documents"]
        with_key = [d for d in docs if d.get("firm_key")]
        self.assertTrue(with_key, "정본에 firm_key 문서가 하나도 없다 — 전제 확인 필요")
        sample = with_key[0]
        html = self.pages[f"findings/doc/{sample['slug']}/index.html"]
        self.assertIn("firm/index.html?key=", html)
        self.assertIn('rel="nofollow"', html)


# ── 발견 허브 (2026-08-26 — findings 첫 화면 재배열 + 랜딩 데이터 존 진입) ──────
class WebDiscoveryHubTest(unittest.TestCase):
    """[2면 분리 2026-08-27] 지적사항 존 — 검색 면(/findings/)과 둘러보기 면
    (/findings/browse/)의 분업·세그 전환·워딩 규율을 검증한다.

    ★왜 갈랐나(사용자 피드백): #811 발견 허브가 목적 카드·최근 문서·축 탐색·상세 검색을
    한 페이지에 쌓아 "너무 많은 정보가 한 페이지에 담겨 잘 못 쓰겠다"는 피드백을 받았다.
    허브의 발견성은 둘러보기 면이 전담하고 검색 면은 도구 전용이다. 검색 블록 내부 계약
    (툴바가 대시보드보다 앞·sticky)은 기존 WebFindingsRenderTest 가 계속 지킨다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_hub_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.html = (cls.single / "findings" / "index.html").read_text(encoding="utf-8")
        cls.browse = (cls.single / "findings" / "browse" / "index.html").read_text(encoding="utf-8")
        cls.landing = (cls.single / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.single / "sitemap.xml").read_text(encoding="utf-8")
        cls.llms = (cls.single / "llms.txt").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ── 면 분업 ──────────────────────────────────────────────────────────────
    def test_search_face_is_tool_only(self):
        """검색 면 첫 화면 = 검색 도구. 허브 섹션이 되돌아오면 분리가 무효가 된다."""
        self.assertIn('id="fnd-tools"', self.html)
        for hub_id in ('id="fnd-purpose"', 'id="fnd-recent"'):
            self.assertNotIn(hub_id, self.html,
                             "허브 섹션이 검색 면으로 되돌아왔다 — 2면 분리 회귀")
        # 세그가 툴바보다 앞(면 전환 장치가 첫 화면에 보인다).
        self.assertLess(self.html.index('class="fnd-seg"'),
                        self.html.index('id="fnd-tools"'))

    def test_browse_face_carries_the_hub(self):
        """둘러보기 면 = 업무별 바로가기 → 최근 공개 문서 → 축별 탐색."""
        i_purpose = self.browse.index('id="fnd-purpose"')
        i_recent = self.browse.index('id="fnd-recent"')
        self.assertLess(i_purpose, i_recent)
        self.assertIn("축별 탐색", self.browse)
        self.assertNotIn('id="fnd-tools"', self.browse,
                         "둘러보기 면에 검색 툴바가 있으면 다시 한 페이지 과밀로 돌아간다")

    def test_seg_nav_on_both_faces_with_correct_active(self):
        for page, face in ((self.html, "검색"), (self.browse, "둘러보기")):
            self.assertIn('class="fnd-seg"', page)
        self.assertIn('class="on" aria-current="page">검색</a>', self.html)
        self.assertIn('class="on" aria-current="page">둘러보기</a>', self.browse)

    def test_purpose_cards_link_every_tool_route(self):
        # 도달성 — 상시 도구 라우트가 전부 카드 안에 남아야 한다(#807 이전으로 후퇴 금지).
        for path in ("findings/checklist/index.html", "findings/firm/index.html",
                     "findings/inspector/index.html", "findings/inspections/index.html",
                     "findings/coverage/index.html", "findings/trends/index.html"):
            self.assertIn(f'href="../../{path}"', self.browse, path)

    def test_browse_face_registered_in_sitemap_and_llms(self):
        self.assertIn(f"<loc>{render.SITE_BASE_URL}/findings/browse/</loc>", self.sitemap)
        self.assertIn("/findings/browse/", self.llms)

    # ── 워딩 규율(사용자 피드백 2026-08-27) ─────────────────────────────────
    def test_no_feature_absence_advertisement(self):
        """기능 부재를 광고하지 않는다 — "순위·비교는 제공하지 않습니다" 류.

        ★정책(037 순위 금지)과 그 부재의 광고는 다르다 — 정책은 RPC 게이트가 지키고,
        화면 문구는 있는 것을 설명한다. 측정 정직성 문구(건수의 의미)는 별개로 유지된다
        (inspector 면 가드가 따로 검증)."""
        for page in (self.html, self.browse):
            self.assertNotIn("제공하지 않습니다", page)
        insp = (WEB_DIR / "templates" / "inspector.html").read_text(encoding="utf-8")
        self.assertNotIn("순위나 비교를 제공하지 않습니다", insp)
        # 측정 정직성은 유지 — 건수를 평가 지표로 오독하는 것을 막는 문구.
        self.assertIn("실사관에 대한 평가 지표가 아닙니다", insp)

    def test_hub_copy_is_professional_not_conversational(self):
        """구어투 제목이 돌아오지 않는다 — "어떤 일로 오셨나요?" → "업무별 바로가기"."""
        self.assertIn("업무별 바로가기", self.browse)
        for casual in ("어떤 일로 오셨나요", "알면 됩니다", "내려가셔도 됩니다"):
            self.assertNotIn(casual, self.browse)

    def test_recent_docs_from_canon_newest_first(self):
        # 최근 공개 문서 5건 — 정본과 같은 정렬(공개일 desc, slug 로 결정론 타이브레이크).
        docs = json.loads(render.FINDINGS_DOCS_FILE.read_text(encoding="utf-8"))["documents"]
        ordered = sorted(docs, key=lambda d: (d["published_date"], d["slug"]),
                         reverse=True)[:5]
        hrefs = re.findall(r'class="fnd-rc-row" href="\.\./\.\./findings/doc/([^/"]+)/"',
                           self.browse)
        self.assertEqual(hrefs, [d["slug"] for d in ordered])

    def test_recent_section_states_date_semantics(self):
        # 공개일≠실사일 — 숫자 해석에 필요한 정직성 고지는 워딩 스윕에서도 살아남는다.
        self.assertIn("문서가 공개된 날", self.browse)

    # ── 소스 완전성(사용자 피드백 — "다른 정보도 있는데 왜 뺐는지") ─────────
    def test_docs_snapshot_covers_every_agency(self):
        """문서로 찾기의 정본에 **수집 중인 5개 기관이 전부** 있어야 한다.

        ★실제로 일어난 일: min_findings=3 게이트(483 기준의 얇은 페이지 방지)가 문서당
        지적 1~2건인 EU NCR·MHRA 를 전량 기각 → 기관 자체가 목록에서 침묵 소실.
        MHRA 는 문서 id 의 공백·슬래시("Insp GMP/GDP/IMP …")가 슬러그 검사에서 추가로
        전량 기각. 임계는 1로, 슬러그는 결정론 변환으로 고쳤다 — 이 가드는 그 재발을
        스냅샷 층에서 막는다(어떤 게이트가 원인이든 기관이 사라지면 여기서 터진다)."""
        docs = json.loads(render.FINDINGS_DOCS_FILE.read_text(encoding="utf-8"))["documents"]
        agencies = {d.get("agency") for d in docs}
        self.assertLessEqual({"FDA", "HC", "MFDS", "EMA", "MHRA"}, agencies)

    def test_thickness_gate_exempts_sources_it_would_erase(self):
        """임계(3)는 유지하되, **그 임계가 소스를 통째로 지우면 면제**한다.

        실측 문서당 지적 1건 비율: EMA 100% · MHRA 100% · MFDS 89% · FDA 28% · HC 16%.
        EU 비준수 보고서는 지적 1건이 곧 보고서 전체라 임계가 '얇음'이 아니라 '존재'를
        재고 있었다 — 두 사건을 한 숫자로 다루면 반드시 한쪽이 틀린다. 판정은 손목록이
        아니라 수집 데이터에서 파생한다(새 소스 자동 편입)."""
        import findings_docs_refresh as fdr
        self.assertEqual(fdr.DEFAULT_MIN_FINDINGS, 3)
        reject = collections.Counter()
        docs = ([{"agency": "FDA", "findings": [0] * 5}] * 2
                + [{"agency": "FDA", "findings": [0]}] * 3       # 얇음 → 걸러진다
                + [{"agency": "NCR", "findings": [0]}] * 4)      # 소스 전체가 1건 → 면제
        kept = fdr.apply_thickness_gate(docs, min_findings=3, reject=reject)
        self.assertEqual(sum(1 for d in kept if d["agency"] == "FDA"), 2,
                         "임계가 실제로 얇은 문서를 거르는 소스에서는 종전대로 동작해야")
        self.assertEqual(sum(1 for d in kept if d["agency"] == "NCR"), 4,
                         "임계가 통째로 지우는 소스는 전량 남아야")
        self.assertEqual(reject["국문 지적 3건 미만"], 3)

    def test_unsafe_doc_id_becomes_deterministic_slug(self):
        import findings_docs_refresh as fdr
        raw = "Insp GMP/GDP/IMP 322/14798-0032[I]"
        slug = fdr._safe_slug(raw)
        self.assertRegex(slug, r"^[A-Za-z0-9._-]{1,120}$")
        self.assertEqual(slug, fdr._safe_slug(raw), "같은 id 는 언제나 같은 슬러그")
        self.assertEqual(fdr._safe_slug("normal-id_1.2"), "normal-id_1.2",
                         "안전한 id 는 무변형 — 기존 URL 이 바뀌면 안 된다")

    def test_facets_agency_axis_exempt_from_min_gate(self):
        """기관 축은 표본 미달로 항목을 빼지 않는다(완전성 우선) — MHRA 8건이 근거."""
        facets = json.loads(render.FINDINGS_FACETS_FILE.read_text(encoding="utf-8"))
        ag = next(a for a in facets["axes"] if a["axis"] == "agency")
        slugs = {v["slug"] for v in ag["items"]}
        self.assertLessEqual({"fda", "hc", "mfds", "ema", "mhra"}, slugs)
        src = pathlib.Path(render.__file__).resolve().parent.parent / "findings_facets_refresh.py"
        self.assertIn('if n < min_findings and axis != "agency":',
                      src.read_text(encoding="utf-8"))

    # ── 랜딩 #records(기존 계약 유지) ────────────────────────────────────────
    def test_landing_records_counts_derived_not_hardcoded(self):
        facets = json.loads(render.FINDINGS_FACETS_FILE.read_text(encoding="utf-8"))
        docs_fmt = f"{facets['totals']['documents']:,}"
        find_fmt = f"{facets['totals']['findings']:,}"
        self.assertIn('id="records"', self.landing)
        self.assertIn(f"문서 {docs_fmt}건", self.landing)
        self.assertIn(f"지적사항 {find_fmt}건", self.landing)
        for tpl in ("landing.html", "findings.html", "findings_browse.html"):
            src = (WEB_DIR / "templates" / tpl).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\d{1,3},\d{3}\s*건", src),
                              f"{tpl}: 건수 하드코딩 금지 — render 가 정본에서 계산해야")

    def test_landing_records_between_why_and_engage(self):
        self.assertLess(self.landing.index('id="why"'),
                        self.landing.index('id="records"'))
        self.assertLess(self.landing.index('id="records"'),
                        self.landing.index('id="engage"'))

    def test_landing_records_question_entries(self):
        for path in ("findings/firm/index.html", "findings/inspector/index.html",
                     "findings/checklist/index.html", "findings/trends/index.html",
                     "findings/index.html"):
            self.assertIn(f'href="{path}"', self.landing, path)
        self.assertIn("이 거래처, 과거에 무엇을 지적받았나요?", self.landing)

    # ── 프로파일 맥락 유지(소스 계약 — firm/inspector.js 는 비골든 런타임 파일) ──
    def test_profile_category_links_carry_context(self):
        """맥락 유지 계약. q 병기는 **폴백 경로로 남는다** — 065 가 적용된 라이브에서는
        프로파일 안에서 좁히고(WebProfileInterpretationTest), 065 미적용·구버전 응답에서만
        이 링크로 내려간다. 두 경로 모두 프로파일 조건을 버리지 않는 것이 계약이다."""
        for name in ("inspector.js", "firm.js"):
            src = (WEB_DIR / "assets" / name).read_text(encoding="utf-8")
            self.assertIn('href += "&q=" + encodeURIComponent(qValue)', src, name)
            self.assertRegex(src, r"buildCatRow\(c, maxCnt, \w+Name, total\)", name)

    def test_deep_link_landing_scroll_removed_with_its_reason(self):
        """허브 착지 스크롤은 존재 이유(허브가 검색을 밀어내림)와 함께 사라져야 한다.

        검색 툴바가 다시 첫 화면에 있으므로 보정할 거리가 없다 — 남겨두면 파라미터를
        들고 온 방문마다 화면이 이유 없이 튄다."""
        src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        self.assertNotIn('document.getElementById("fnd-search")', src)
        self.assertNotIn("searchSection.scrollIntoView()", src)


# ── [진입점 3종 2026-08-31] 실사관 프로파일까지 가는 길 ─────────────────────────
class WebInspectorEntryPointsTest(unittest.TestCase):
    """실사관 프로파일(#862)은 이미 라이브였지만 실측상 닿는 길이 없었다 — 상단 nav
    없음, /findings/ 본문 없음, footer 도구 열 링크 1개가 스크롤 17,120px 지점. 이
    작업은 그 길을 세 곳에 낸다: (1) 지적사항 존 세그먼트에 '실사관' 탭, (2) 검색 화면
    상단 안내 스트립, (3) 문서 상세 실사관 행의 조회 링크. 여기서는 (1)+(2)만 본다 —
    (3)은 WebFindingsDocInspectorLineTest 가 별도로 지킨다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_insp_entry_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.search = (cls.single / "findings" / "index.html").read_text(encoding="utf-8")
        cls.browse = (cls.single / "findings" / "browse" / "index.html").read_text(encoding="utf-8")
        cls.inspector = (cls.single / "findings" / "inspector" / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ── (1) 세그먼트 탭 3페이지 일관성 ──────────────────────────────────────────
    def _seg_block(self, html):
        start = html.index('<nav class="fnd-seg"')
        return html[start:html.index("</nav>", start)]

    def test_all_three_pages_carry_the_same_three_tabs(self):
        for label, html in (("search", self.search), ("browse", self.browse),
                             ("inspector", self.inspector)):
            with self.subTest(page=label):
                seg = self._seg_block(html)
                self.assertEqual(seg.count("<a "), 3, f"{label} 페이지 세그먼트 탭이 3개가 아니다")
                for text in ("검색", "둘러보기", "실사관"):
                    self.assertIn(f">{text}</a>", seg, f"{label} 페이지에 '{text}' 탭 없음")

    def test_each_page_marks_only_itself_active(self):
        cases = (
            ("search", self.search, "검색"),
            ("browse", self.browse, "둘러보기"),
            ("inspector", self.inspector, "실사관"),
        )
        for label, html, own_text in cases:
            with self.subTest(page=label):
                seg = self._seg_block(html)
                self.assertIn(f'class="on" aria-current="page">{own_text}</a>', seg,
                               f"{label} 페이지에서 자기 탭이 활성 표시가 아니다")
                # 활성 표시는 정확히 1개(다른 두 탭은 활성이 아니어야 한다).
                self.assertEqual(seg.count('class="on"'), 1,
                                  f"{label} 페이지에 활성 탭이 2개 이상이거나 없다")

    def test_inspector_seg_link_paths_match_sibling_depth(self):
        """inspector.html 은 findings/browse 와 같은 깊이(rel_root="../../")다 — 세그
        안 링크 경로가 findings_browse.html 이 쓰는 것과 동일해야 한다(둘 다 같은
        파셜을 그대로 include 하므로, rel_root 배선이 어긋나면 여기서 갈린다)."""
        seg = self._seg_block(self.inspector)
        self.assertIn('href="../../findings/index.html"', seg)
        self.assertIn('href="../../findings/browse/index.html"', seg)
        self.assertIn('href="../../findings/inspector/index.html"', seg)

    def test_seg_appears_before_ip_lookup_state(self):
        """세그가 본문(조회 랜딩)보다 앞 — 진입하자마자 다른 면으로도 오갈 수 있어야
        한다(검색 화면의 '세그가 툴바보다 앞' 계약과 동형, WebDiscoveryHubTest 참조)."""
        self.assertLess(self.inspector.index('class="fnd-seg"'),
                         self.inspector.index('id="ip-lookup"'))

    # ── (2) 검색 화면 상단 안내 스트립 ───────────────────────────────────────────
    def test_strip_present_on_search_face_only(self):
        self.assertIn('class="fnd-insp-cta"', self.search)
        self.assertNotIn('class="fnd-insp-cta"', self.browse,
                          "둘러보기 면에는 스트립을 넣지 않는다(스펙 §2) — 세그 탭만으로 충분")

    def test_strip_content_and_link_path(self):
        start = self.search.index('class="fnd-insp-cta"')
        block = self.search[start:self.search.index("</aside>", start)]
        self.assertIn("실사관으로 찾기", block)
        self.assertIn("ti-user-search", block)
        self.assertIn(
            "FDA 483 문서에 서명한 실사관이 어떤 분야를 확인했고 어느 제조소를 다녀갔는지 "
            "문서 단위로 모아 봅니다.", block)
        self.assertIn('href="../findings/inspector/index.html"', block,
                       "검색 화면 rel_root는 '../'다")
        self.assertIn("실사관 조회", block)
        self.assertIn("ti-arrow-right", block)

    def test_strip_sits_directly_above_fnd_tools(self):
        i_strip = self.search.index('class="fnd-insp-cta"')
        i_tools = self.search.index('<section class="fnd-tools"')
        self.assertLess(i_strip, i_tools)
        # "바로 위" — 스트립 닫힘과 툴바 시작 사이에 다른 aside/section 이 끼어들지 않는다.
        i_strip_close = self.search.index("</aside>", i_strip) + len("</aside>")
        between = self.search[i_strip_close:i_tools]
        self.assertNotIn("<section", between)
        self.assertNotIn("<aside", between)

    def test_strip_css_is_page_scoped_not_in_grm_css(self):
        """grm.css 무접촉 — 스타일은 findings.html 자체 <style> 안에만 있어야 한다."""
        css = (WEB_DIR / "assets" / "grm.css").read_text(encoding="utf-8")
        self.assertNotIn(".fnd-insp-cta", css)
        template = (WEB_DIR / "templates" / "findings.html").read_text(encoding="utf-8")
        self.assertIn(".fnd-insp-cta{", template)


# ── P1 해석층 (2026-08-27 — 마이그 065 + 프로파일 3종 개편) ─────────────────────
class WebProfileInterpretationTest(unittest.TestCase):
    """업체·실사관 프로파일이 원시 건수에서 **해석**으로 넘어갔는지 검증한다.

    화면은 런타임 RPC 라 골든이 값을 고정하지 못한다 — 그래서 ①셸(신설 컨테이너)은 렌더
    산출물에서, ②동작 계약은 JS 소스에서, ③마이그는 파일에서 각각 검사한다(037 정책
    준수 여부는 음성 검사로 박는다)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_p1_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)
        cls.firm = (cls.single / "findings" / "firm" / "index.html").read_text(encoding="utf-8")
        cls.insp = (cls.single / "findings" / "inspector" / "index.html").read_text(encoding="utf-8")
        cls.firm_js = (WEB_DIR / "assets" / "firm.js").read_text(encoding="utf-8")
        cls.insp_js = (WEB_DIR / "assets" / "inspector.js").read_text(encoding="utf-8")
        cls.mig = (WEB_DIR / "migrations" /
                   "065_profile_categories_and_repeats.sql").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ★금지 어휘·유무 검사는 **주석을 걷어낸 코드**에서 해야 한다. 처음 이 클래스를 짤 때
    #   원본 전체를 훑었더니 4건이 빨갛게 떴는데, 전부 "이 어휘를 쓰지 않는다"고 적어 둔
    #   주석 자신이었다 — 그대로 뒀으면 **가드가 문서화를 처벌**해서, 다음 사람이 금지
    #   이유를 지워야 초록이 되는 구조가 된다(정확히 반대로 가는 유인).
    @staticmethod
    def _js_code(src):
        out = re.sub(r"/\*.*?\*/", "", src, flags=re.S)   # 블록 주석
        return re.sub(r"^\s*//.*$", "", out, flags=re.M)  # 줄머리 주석(URL 의 // 는 보존)

    @staticmethod
    def _sql_code(sql):
        return re.sub(r"--.*$", "", sql, flags=re.M)

    @staticmethod
    def _fn_body(src, header):
        i = src.index(header)
        return src[i:src.index("\n  }", i)]

    # ── 셸 ──────────────────────────────────────────────────────────────────
    def test_new_shells_rendered(self):
        for html, prefix in ((self.firm, "fp"), (self.insp, "ip")):
            self.assertIn(f'id="{prefix}-cat-note"', html)
            self.assertIn(f'id="{prefix}-rep-block"', html)
            self.assertIn(f'id="{prefix}-rep"', html)
            self.assertIn(f'id="{prefix}-filter"', html)
            # 빈 셸은 hidden 으로 나가야 한다(데이터 없으면 빈 껍데기 금지 관례).
            self.assertIn(f'id="{prefix}-rep-block" aria-label="반복 확인된 영역" hidden', html)
            self.assertIn(f'id="{prefix}-filter" hidden', html)

    def test_shell_stays_deterministic(self):
        """신설 셸에 실데이터가 새어 들어가지 않는다(런타임 주입 계약)."""
        for html in (self.firm, self.insp):
            self.assertRegex(html, r'id="[fi]p-rep"[^>]*></div>')
            self.assertRegex(html, r'id="[fi]p-cat-note"[^>]*></p>')

    # ── 마이그 065: 순수 가산 ────────────────────────────────────────────────
    def test_migration_is_additive_only(self):
        # 시그니처 무변경 — 인자가 하나만 달라도 PostgREST 가 404 를 준다(#681).
        self.assertIn("findings_firm_profile(p_firm_key text)", self.mig)
        self.assertIn("findings_inspector_profile(p_inspector_key text)", self.mig)
        # 기존 키가 전부 재현돼 있어야 한다(create or replace 는 통짜 교체다).
        for k in ("'totals'", "'by_category'", "'by_year'", "'by_source'", "'documents'",
                  "'display_name'", "'firm_key'", "'inspector_key'"):
            self.assertIn(k, self.mig, k)
        # 신설 2키 — 두 함수에 하나씩(검증용 주석의 언급은 세지 않는다).
        code = self._sql_code(self.mig)
        self.assertEqual(code.count("'repeats'"), 2)
        # 실사관 쪽은 주변 정렬에 맞춰 공백을 넓게 쓴다 — 공백에 관대하게 센다.
        self.assertEqual(len(re.findall(r"'categories',\s+categories", code)), 2)
        # 코호트 게이트(실사관 5문서 미만 null)는 그대로.
        self.assertIn("< 5 then 'null'::jsonb", code)

    def test_repeat_is_counted_by_documents_not_findings(self):
        """반복의 정의가 **서로 다른 문서 수**여야 한다.

        건수로 세면 한 문서 안에서 같은 분류로 5건이 잡힌 것이 '5회 반복'이 되어 버린다 —
        그건 반복이 아니라 한 번의 실사다. 이 한 줄이 이 마이그레이션의 핵심이라 게이트로
        박는다(누가 having count(*) 로 바꾸면 즉시 빨강)."""
        code = self._sql_code(self.mig)
        self.assertEqual(code.count("having count(distinct raw_signal_id) >= 2"), 2)
        self.assertNotIn("having count(*) >= 2", code)
        # 빈 분류는 조치로 이어지지 않으므로 반복에서 제외한다.
        self.assertEqual(code.count("coalesce(category_code, '') <> ''"), 2)

    # ── 동작 계약 ────────────────────────────────────────────────────────────
    def test_in_profile_filter_replaces_leaving_the_page(self):
        """분류 클릭이 **페이지를 떠나지 않고** 목록을 좁힌다.

        ★재는 것은 **성질**이지 구현 모양이 아니다 — 종전엔 특정 한 줄
        (`documents.filter(...)`)을 박아 뒀는데, P1.5-3 이 firm.js 의 목록을 문서+실사
        병합 타임라인으로 바꾸자 성질은 그대로인데 가드만 터졌다(의미 없는 실패).
        이 저장소가 이미 적은 관례를 따른다 — 이름이 아니라 성질을 고정한다."""
        for name, src in (("firm.js", self.firm_js), ("inspector.js", self.insp_js)):
            self.assertIn("function docHasCat(", src, name)
            self.assertIn("function setActiveCat(", src, name)
            # ① 활성 분류가 문서 렌더링을 게이트한다(모양 무관 — filter 든 forEach 든).
            self.assertIn("docHasCat(d, activeCat)", src, name)
            # ② 클릭이 페이지를 떠나지 않는다 — 이 테스트 이름의 본체다.
            fn = src[src.index("function setActiveCat("):]
            fn = fn[:fn.index(chr(10) + "  }" + chr(10))]
            for leaving in ("location.href", "location.assign", "window.open"):
                self.assertNotIn(leaving, fn, name + ": " + leaving)
            # ③ 대신 같은 화면을 다시 그린다.
            self.assertRegex(fn, r"render(Timeline|Documents)\(")

    def test_degrades_when_065_absent(self):
        """065 미적용·구버전 응답이면 종전 링크 동작으로 조용히 내려간다.

        배포 순서(마이그 먼저)가 어긋나도 화면이 깨지지 않아야 한다 — 응답에서 판정하지
        빌드 시점 플래그로 판정하지 않는다."""
        for name, src in (("firm.js", self.firm_js), ("inspector.js", self.insp_js)):
            self.assertIn("filterable = LAST_DOCS.some(", src, name)
            self.assertIn('document.createElement(filterable ? "button" : "a")', src, name)

    def test_interpretation_sentence_precedes_numbers(self):
        for name, src in (("firm.js", self.firm_js), ("inspector.js", self.insp_js)):
            self.assertIn("function renderCatNote(", src, name)
            self.assertIn("가장 많이 확인된 영역", src, name)
        # 귀속 금지 어휘 — 483 은 공동 서명이 가능하므로 "지적한"으로 쓰지 않는다.
        self.assertIn("이 실사관이 서명한 공개 문서에서 가장 많이 확인된 영역",
                      self.insp_js)
        self.assertNotIn("가장 많이 지적한", self._js_code(self.insp_js))

    def test_catch_all_excluded_from_ranking_but_kept_in_denominator(self):
        """캐치올 분류는 순위 문장·반복 목록에서 빼되 분모·막대에는 남긴다(#810 규율).

        "이 업체에서 가장 많이 확인된 영역 = 기타 품질시스템"은 그 업체의 성질이 아니라
        분류기 상태라 조치로 이어지지 않는다. 라이브 프리뷰에서 업체·실사관 **양쪽 다**
        1위가 기타로 나와 잡은 결함이라 게이트로 박는다."""
        for name, src in (("firm.js", self.firm_js), ("inspector.js", self.insp_js)):
            code = self._js_code(src)
            self.assertIn('var CATCH_ALL = "other_quality_system";', code, name)
            # 순위 문장과 반복 목록 두 곳에서 걸러야 한다.
            self.assertEqual(
                len(re.findall(r"category_code !== CATCH_ALL", code)), 2, name)
            # 뺐다는 사실을 화면에 적는다(조용히 빼지 않는다).
            self.assertIn("세부 분류 전이라", code, name)
            # 막대(buildCatRow 루프)는 전량을 그대로 그린다 — 여기서 거르면 안 된다.
            self.assertIn("LAST_CATS.forEach(function (c) {", code, name)

    # ── 037 정책: 의도적 비대칭 ──────────────────────────────────────────────
    def test_density_metric_is_firm_only(self):
        """문서당 지적(밀도)은 업체에만 붙는다.

        업체에서는 '실사를 많이 받은 곳이 커 보이는' 착시를 걷어내는 정규화지만, 실사관에
        같은 값을 붙이면 '한 번에 몇 건 적는 사람인가' = **까다로움 지표**가 되어 037 이
        막으려는 읽기를 정확히 만든다. 두 화면을 일관성 때문에 맞추는 '수리'를 막는 음성
        검사다."""
        self.assertIn('"문서당 지적"', self._fn_body(self.firm_js, "function renderStats("))
        self.assertNotIn('"문서당 지적"', self._fn_body(self.insp_js, "function renderStats("))
        # 비대칭의 **이유**가 코드 옆에 남아 있어야 한다(주석은 원본에서 확인).
        self.assertIn("의도적 비대칭", self.insp_js)

    def test_no_ranking_vocabulary_leaked(self):
        """화면에 나가는 문자열에 순위·평가 어휘가 없어야 한다(037).

        검사 대상은 주석을 걷어낸 코드와 렌더 산출물이다 — 금지 이유를 적어 둔 주석까지
        훑으면 그 주석을 지워야 초록이 되는, 정확히 거꾸로 된 가드가 된다."""
        banned = ("까다로운", "까다롭", "위험도", "실사관 순위", "엄격도")
        code = self._js_code(self.insp_js)
        for word in banned:
            self.assertNotIn(word, code, word)
            self.assertNotIn(word, self.insp, word)


class WebAdminGrowthPanelTest(unittest.TestCase):
    """/admin 성장·유입 패널 + 깔때기 일별 스냅샷(071) 계약.

    ① 어휘 동기화 — admin.js FUNNEL_KEYS = 060 CHECK = 071 CHECK. 한쪽만 늘리면
       스냅샷/판독이 조용히 어긋난다(061 FEEDBACK_STATUS 대조와 동형).
    ② 스냅샷 쓰기 경로는 DB cron 뿐 — funnel_snapshot 은 클라이언트 실행 권한이 없고,
       admin.js 는 두 테이블을 select 로만 읽는다.
    ③ 외부 계기는 RUM(봇 제외)을 정본으로 안내 — 존(zone) 지표는 크롤러가 섞여
       실사용 지표로 쓰지 않는다는 경고가 화면에 남아 있어야 한다.
    """

    @classmethod
    def setUpClass(cls):
        cls.admin_js = (WEB_DIR / "assets" / "admin.js").read_text(encoding="utf-8")
        cls.admin_html = (WEB_DIR / "templates" / "admin.html").read_text(encoding="utf-8")
        cls.mig060 = (WEB_DIR / "migrations" /
                      "060_subscribe_funnel_counts.sql").read_text(encoding="utf-8")
        cls.mig071 = (WEB_DIR / "migrations" /
                      "071_funnel_counts_daily.sql").read_text(encoding="utf-8")

    def _check_keys(self, sql):
        m = re.search(r"key in \(([^)]*)\)", sql)
        self.assertIsNotNone(m, "CHECK 의 key 화이트리스트 미발견")
        return set(re.findall(r"'([a-z_]+)'", m.group(1)))

    def test_funnel_vocabulary_synced_three_ways(self):
        expected = {"band_view", "band_submit", "cta_view", "cta_submit", "cta_dismiss"}
        self.assertEqual(self._check_keys(self.mig060), expected, "060 CHECK 어휘")
        self.assertEqual(self._check_keys(self.mig071), expected, "071 CHECK 어휘")
        block = self.admin_js.split("var FUNNEL_KEYS = [", 1)[1].split("];", 1)[0]
        client = set(re.findall(r'"([a-z_]+)"', block))
        self.assertEqual(client, expected, "admin.js FUNNEL_KEYS 가 060/071 CHECK 와 다름")

    def test_panel_wired(self):
        for needle in ('data-tab="growth"', 'data-panel="growth"', 'id="grm-growth-kpis"',
                       'id="grm-growth-daily"', 'id="grm-growth-refresh"'):
            self.assertIn(needle, self.admin_html)
        self.assertIn("loadGrowth", self.admin_js)
        # refreshAll 에 실려야 전체 새로고침·최초 로드에서 함께 갱신된다.
        refresh_all = self.admin_js.split("function refreshAll", 1)[1].split("}", 1)[0]
        self.assertIn("loadGrowth()", refresh_all)
        # 새로고침 버튼은 이 탭이 보여주는 것을 **전부** 다시 읽어야 한다. 072 로 방문
        # 표가 같은 탭에 들어왔으므로 핸들러가 둘 다 부르는지 본문으로 확인한다
        # (문자열 한 줄로 고정하면 탭에 뭔가 더 붙을 때마다 계약이 아니라 표기가 깨진다).
        handler = self.admin_js.split('byId("grm-growth-refresh").addEventListener(', 1)[1]
        handler = handler.split("});", 1)[0]
        self.assertIn("loadGrowth()", handler)
        self.assertIn("loadRum()", handler)

    def test_snapshot_write_path_is_cron_only(self):
        self.assertIn("enable row level security", self.mig071)
        self.assertIn("grant select on public.funnel_counts_daily to anon, authenticated",
                      self.mig071)
        self.assertIn("revoke all on function public.funnel_snapshot() from anon", self.mig071)
        self.assertIn("revoke all on function public.funnel_snapshot() from authenticated",
                      self.mig071)
        self.assertNotIn("grant execute on function public.funnel_snapshot", self.mig071)
        self.assertIn("cron.schedule('grm-funnel-snapshot-daily', '55 14 * * *'", self.mig071)
        # 클라이언트는 select 뿐 — insert/upsert/스냅샷 RPC 호출이 있으면 계약 위반.
        self.assertIn('from("funnel_counts").select(', self.admin_js)
        self.assertIn('from("funnel_counts_daily").select(', self.admin_js)
        for banned in ('from("funnel_counts").insert', 'from("funnel_counts_daily").insert',
                       'from("funnel_counts_daily").upsert', 'rpc("funnel_snapshot"'):
            self.assertNotIn(banned, self.admin_js)

    def test_external_gauges_point_to_rum(self):
        # RUM 링크는 봇 제외 필터를 물고 있어야 한다 — 존 지표로의 회귀 방지.
        self.assertIn("web-analytics/overview/visits?siteTag~in=", self.admin_html)
        self.assertIn("excludeBots=Yes", self.admin_html)
        self.assertIn("search.google.com/search-console", self.admin_html)
        self.assertIn("searchadvisor.naver.com", self.admin_html)
        self.assertIn("app.brevo.com", self.admin_html)
        self.assertIn("크롤러가 섞여 실사용 지표로 쓰지 않습니다", self.admin_html)


class WebGlossaryRelatedCaseCountTest(unittest.TestCase):
    """[C2] `함께 보면 좋은 용어` 칩의 사례 건수 병기.

    왜: 검색 유입이 홈이 아니라 용어 낱개 페이지로 착지한다(실측 — 어제 상위 착지 2개가
    모두 용어 페이지였고 둘 다 사례가 **없는** 용어였다). 사례 없는 용어는 정의만 주고
    끝나므로, 방문자가 우리 고유 자산(실제 지적 문장)이 있는 쪽으로 건너갈 다리가 필요하다.

    ★고치지 않는 것: `related` 의 **순서**. 사람이 고른 목록이고 그 순서가 정본이라,
    사례 있는 것을 위로 올리면 코드가 큐레이션을 덮어쓴다. 사실(건수)만 덧붙인다.
    """

    @classmethod
    def setUpClass(cls):
        cls.terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))
        cls.cases = render.load_glossary_cases()
        cls.view = render.build_glossary_view(cls.terms, None, cls.cases)
        cls.by_id = {t["id"]: t for g in cls.view["groups"] for t in g["terms"]}
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_glrel_"))
        cls.single = cls._tmp / "single"
        _build_single(cls.single)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_related_order_is_untouched(self):
        """순서 계약 — 뷰의 related 순서 = 정본 순서(존재하는 id 만 남긴 뒤)."""
        checked = 0
        for t in self.terms:
            want = [r for r in (t.get("related") or []) if r in self.by_id]
            got = [r["id"] for r in self.by_id[t["id"]]["related"]]
            self.assertEqual(got, want, f"related 순서가 바뀌었다: {t['id']}")
            checked += len(want)
        self.assertGreater(checked, 0, "related 를 가진 용어가 0 — 검사 대상 자체가 없다")

    def test_count_matches_that_terms_own_count(self):
        """한 용어가 화면 두 곳에서 다른 숫자를 갖지 않는다 —
        관련 칩의 건수 = 그 용어 상세의 case_count_label."""
        labeled = 0
        for tid, tv in self.by_id.items():
            for r in tv["related"]:
                self.assertEqual(r["case_count_label"],
                                 self.by_id[r["id"]]["case_count_label"],
                                 f"{tid} → {r['id']} 건수 불일치")
                if r["case_count_label"]:
                    labeled += 1
        self.assertGreater(labeled, 0, "건수가 붙은 관련 용어가 0 — 가산이 발화하지 않았다")

    def test_no_count_where_there_are_no_cases(self):
        """사례가 없는 용어에는 숫자를 지어내지 않는다(빈 문자열 → 템플릿이 조용히 생략)."""
        excluded = {e["id"] for e in (self.cases_raw().get("excluded") or [])}
        self.assertGreater(len(excluded), 0, "excluded 0 — 검사 대상 없음")
        seen = 0
        for tv in self.by_id.values():
            for r in tv["related"]:
                if r["id"] in excluded:
                    self.assertEqual(r["case_count_label"], "",
                                     f"사례 없는 용어에 건수를 붙였다: {r['id']}")
                    seen += 1
        self.assertGreater(seen, 0, "excluded 용어를 related 로 가진 용어가 0 — 비공허 실패")

    def cases_raw(self):
        return json.loads(render.GLOSSARY_CASES_FILE.read_text(encoding="utf-8"))

    def test_case_less_page_gets_a_bridge(self):
        """이 변경의 목적 — 사례가 **없는** 용어 페이지에도 사례 있는 이웃으로 가는
        다리가 실제로 렌더된다. 목적이 달성됐는지를 라이브 산출물로 확인한다."""
        excluded = {e["id"] for e in (self.cases_raw().get("excluded") or [])}
        bridged = []
        for tid in excluded:
            tv = self.by_id.get(tid)
            if tv and any(r["case_count_label"] for r in tv["related"]):
                bridged.append(tid)
        self.assertGreater(len(bridged), 0,
                           "사례 없는 용어 중 사례 있는 이웃을 가진 것이 0 — 다리가 안 놓인다")
        page = (self.single / "glossary" / bridged[0] / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="gt-rel-n"', page)
        self.assertRegex(page, r'<span class="gt-rel-n">사례 [0-9,]+건</span>')

    def test_index_page_ignores_the_new_key(self):
        """순수 가산 증명 — 색인(glossary/index.html)은 새 키를 읽지 않으므로
        골든이 그대로다. 색인 골든은 별도 골든 테스트가 byte 로 잠근다."""
        index = (self.single / "glossary" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("gt-rel-n", index)
        self.assertNotIn("사례 1,", index)


class WebAdminRumPanelTest(unittest.TestCase):
    """[072] /admin 방문·유입 표 + Cloudflare RUM 수집기 계약.

    목적: 운영자가 Cloudflare 대시보드를 읽지 않아도 되게 한다("뭐가 뭔지 하나도
    모르겠다" — 2026-09-01 사용자). 그래서 이 표가 사라지거나 축이 어긋나면 곧바로
    Cloudflare 로 되돌아가야 하므로 배선과 축을 함께 잠근다.
    """

    @classmethod
    def setUpClass(cls):
        cls.admin_js = (WEB_DIR / "assets" / "admin.js").read_text(encoding="utf-8")
        cls.admin_html = (WEB_DIR / "templates" / "admin.html").read_text(encoding="utf-8")
        cls.mig = (WEB_DIR / "migrations" / "072_rum_daily.sql").read_text(encoding="utf-8")
        cls.wf = (WEB_DIR.parent / ".github" / "workflows"
                  / "grm-rum-analytics.yml").read_text(encoding="utf-8")

    def test_panel_wired(self):
        self.assertIn('id="grm-rum-daily"', self.admin_html)
        self.assertIn("방문 · 유입 경로", self.admin_html)
        for needle in ("loadRum", "renderRum", 'from("rum_daily")',
                       'from("rum_referrer_daily")'):
            self.assertIn(needle, self.admin_js)
        refresh_all = self.admin_js.split("function refreshAll", 1)[1].split("}", 1)[0]
        self.assertIn("loadRum()", refresh_all)

    def test_metric_vocabulary_matches_migration(self):
        """화면이 읽는 metric 이름이 072 CHECK 와 같아야 한다 — 한쪽만 늘리면 표가
        조용히 0 으로 찍힌다(060/071 어휘 동기화와 동형)."""
        m = re.search(r"metric text not null check \(metric in \(([^)]*)\)", self.mig)
        self.assertIsNotNone(m, "072 CHECK 의 metric 화이트리스트 미발견")
        db = set(re.findall(r"'([a-z_]+)'", m.group(1)))
        self.assertEqual(db, {"visits", "page_views"})
        for k in db:
            self.assertIn("m." + k, self.admin_js, f"화면이 {k} 를 읽지 않는다")

    def test_read_is_signed_in_only_and_writes_have_no_client_path(self):
        """방문 규모는 운영 지표다 — anon 공개인 funnel_counts 와 의도적으로 다르다."""
        self.assertIn("enable row level security", self.mig)
        self.assertIn("grant select on public.rum_daily to authenticated", self.mig)
        self.assertNotIn("to anon", self.mig.split("grant select", 1)[1].split(";", 1)[0])
        for banned in ('from("rum_daily").insert', 'from("rum_daily").upsert',
                       'from("rum_referrer_daily").insert'):
            self.assertNotIn(banned, self.admin_js)

    def test_ai_is_classified_before_google(self):
        """★순서가 판정이다 — gemini.google.com 은 구글 규칙(`.google.`)에도 걸린다.
        AI 그룹이 뒤로 가면 AI 유입이 통째로 구글로 잘못 집계된다."""
        block = self.admin_js.split("var RUM_REFERRER_GROUPS = [", 1)[1].split("];", 1)[0]
        keys = re.findall(r'key: "([a-z]+)"', block)
        self.assertEqual(keys[0], "ai", f"AI 가 첫 규칙이 아니다: {keys}")
        self.assertLess(keys.index("ai"), keys.index("google"))

    def test_kst_axis_is_stated_and_bot_filter_is_on(self):
        """축(KST)과 모집단(봇 제외)은 밝혀야 한다 — 이 둘이 흔들리면 같은 화면의
        깔때기(23:55 KST 스냅샷)와 비교가 성립하지 않는다."""
        collector = (WEB_DIR.parent / "collect_rum_analytics.py").read_text(encoding="utf-8")
        self.assertIn("bot: 0", collector)
        # ★일 단위로 받는다 — 시간 단위는 버킷마다 반올림이 걸려 작은 시간대가 사라진다
        # (2026-09-02 실측: 대시보드 7일 264 vs 시간합산 9일 250, 9/1 은 60 vs 20).
        self.assertIn("dimensions { date }", collector)
        self.assertNotIn("datetimeHour", collector.split('"""', 2)[2])
        # 축(UTC)은 단정하지 말고 밝힌다 — 화면이 근사임을 말해야 한다.
        self.assertIn("협정시(UTC) 기준", self.admin_html)

    def test_traffic_values_never_reach_the_public_log(self):
        """★이 저장소는 PUBLIC 이고 Actions 로그는 누구나 본다.

        수집기가 응답을 그대로 찍으면 사이트 방문자 수가 공개 로그에 남는다. 필드명
        검증에 필요한 것은 값이 아니라 키 이름과 GraphQL 오류뿐이므로 그 둘만 낸다.
        (2026-09-02: 최초 구현이 원시 payload 를 dump 하고 있었다 — 돌리기 전에 잡았다.)
        """
        src = (WEB_DIR.parent / "collect_rum_analytics.py").read_text(encoding="utf-8")
        self.assertIn("def probe_report(", src)
        # 원시 응답 통째 dump 금지.
        self.assertNotIn("json.dumps(payload", src)
        # 일별 방문/페이지뷰 값을 찍던 루프도 없어야 한다(행 수만 남긴다).
        self.assertNotIn("daily[d]['visits']", src)
        self.assertNotIn('daily[d]["visits"]', src)
        # probe 는 구조만 — 오류 전문은 허용(우리가 틀린 필드명이 거기 들어 있다).
        probe_body = src.split("def probe_report(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("row keys", probe_body)
        self.assertIn("GraphQL 오류", probe_body)

    def test_landing_paths_are_tracked_without_query_strings(self):
        """[073] 착지 경로 — "어느 섹션이 사람을 데려오나"의 유일한 사실 근거.

        ★쿼리스트링은 저장하지 않는다. `/findings/inspector/?key=실명` 처럼 URL 에 사람
        이름이 실리는 경로가 있어서, 통째로 담으면 실명이 테이블에 쌓인다.
        """
        src = (WEB_DIR.parent / "collect_rum_analytics.py").read_text(encoding="utf-8")
        mig = (WEB_DIR / "migrations" / "073_rum_path_daily.sql").read_text(encoding="utf-8")
        self.assertIn("requestPath", src)
        self.assertIn("def clean_path(", src)
        # 쿼리·프래그먼트를 실제로 떼는지(구현 세부가 아니라 계약).
        body = src.split("def clean_path(", 1)[1].split("def fetch(", 1)[0]
        self.assertIn('"?"', body)
        self.assertIn('"#"', body)
        # 저장·노출 경로가 072 와 같은 규칙인지.
        self.assertIn("grant select on public.rum_path_daily to authenticated", mig)
        self.assertNotIn("to anon", mig.split("grant select", 1)[1].split(";", 1)[0])
        self.assertIn('from("rum_path_daily")', self.admin_js)
        self.assertIn('id="grm-rum-paths"', self.admin_html)
        self.assertIn("조회어는 저장하지 않습니다", self.admin_html)
        # ★경로별 수치는 10단위 반올림이라 하루 5건 미만이 0 으로 사라진다.
        # 밝히지 않으면 "목록에 없음"을 "방문 없음"으로 오독한다(실측: 경로 합 210
        # vs 같은 기간 실제 방문 265 — 21%가 반올림으로 증발).
        self.assertIn("10단위로 반올림", self.admin_html)
        self.assertIn("방문이 없는 것은 아닙니다", self.admin_html)

    def test_zone_rules_are_specific_before_general(self):
        """구역 분류는 위에서부터 먼저 걸리는 규칙이 이긴다 — 일반 규칙(`/findings/`)이
        구체 규칙(`/findings/firm/`)보다 앞서면 하위 구역이 전부 삼켜진다."""
        block = self.admin_js.split("var RUM_ZONES = [", 1)[1].split("];", 1)[0]
        labels = re.findall(r'label: "([^"]+)"', block)
        for specific in ("업체 프로파일", "실사관 프로파일", "지적사항 문서", "트렌드"):
            self.assertLess(labels.index(specific), labels.index("지적사항 검색"),
                            f"{specific} 규칙이 일반 규칙보다 뒤에 있다")

    def test_workflow_uses_the_new_secret_and_no_arithmetic_in_expressions(self):
        """GHA 표현식에는 산술이 없다 — 창 경계는 셸에서 계산해야 한다."""
        self.assertIn("secrets.CLOUDFLARE_ANALYTICS_TOKEN", self.wf)
        self.assertIn("date -u -d", self.wf)
        self.assertNotRegex(self.wf, r"\$\{\{[^}]*[0-9]\s*[-+*/]\s*[0-9][^}]*\}\}")


class WebClausePageTest(unittest.TestCase):
    """[조항 페이지 2026-09-03] 21 CFR 조항별 지적사례 — /findings/clause/{slug}/.

    검색 실측(13쿼리)에서 `21 CFR 211.192` 류는 결과가 **전부 영문 법령 사이트**였다.
    국문 해설이 공백인 자리를 정적으로 채우는 페이지라, 이 클래스는 **커밋된 정본**
    (findings_docs·library/cfr·glossary)으로 실제 뷰를 만들어 계약을 잰다.
    """

    @classmethod
    def setUpClass(cls):
        cls.docs = render.load_findings_docs()
        cls.cfr = render.load_cfr_catalog()
        cls.terms = json.loads(render.GLOSSARY_FILE.read_text(encoding="utf-8"))
        cls.views = render.build_clause_views(cls.docs, cls.cfr, cls.terms)

    def test_catalog_loader_is_not_empty(self):
        """★조용한 0장 가드. `_load_reg_ref_catalogs()` 는 cfr 을 싣지 않는다 —
        모르고 `.get("cfr")` 를 쓰면 카탈로그가 빈 채로 페이지가 0장이 되고,
        렌더는 아무 말 없이 성공한다(실제로 한 번 그렇게 걸렸다)."""
        self.assertGreater(len(self.cfr), 0, "cfr 카탈로그가 비었다 — 조항 페이지가 0장이 된다")

    def test_pages_are_generated(self):
        self.assertGreater(len(self.views), 0, "조항 페이지가 0장 — 배선이 끊겼다")

    def test_every_page_meets_the_document_floor(self):
        """사례 1~2건짜리 페이지는 사용자에게도 검색엔진에게도 빈손이다."""
        for v in self.views:
            self.assertGreaterEqual(v["documents"], render.CLAUSE_MIN_DOCUMENTS, v["code"])

    def test_scope_is_gmp_parts_only(self):
        """범위를 넓히지 않는다 — 경고서한은 표시(201.x)·등록(207.x)·FD&C Act 도
        인용하는데 그건 이 사이트의 주제가 아니고 국문 맥락도 없다."""
        for v in self.views:
            self.assertRegex(v["section"], r"^21[01]\.", f"GMP 밖 조항: {v['code']}")

    def test_every_page_has_official_title_and_url(self):
        for v in self.views:
            self.assertTrue(v["title_en"], f"조항 제목 없음: {v['code']}")
            self.assertTrue(v["official_url"].startswith("https://"), v["code"])

    def test_samples_are_capped_and_carry_renderable_fields(self):
        for v in self.views:
            self.assertLessEqual(len(v["samples"]), render.CLAUSE_MAX_SAMPLES, v["code"])
            self.assertGreater(len(v["samples"]), 0, v["code"])
            for s in v["samples"]:
                self.assertTrue(s["text_ko"].strip(), v["code"])
                self.assertTrue(s["firm_name"].strip(), v["code"])
                self.assertRegex(s["published_date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_samples_never_carry_inspector_names(self):
        """037 제약 — 실명 개인 집계는 이 페이지의 목적 밖이다."""
        for v in self.views:
            for s in v["samples"]:
                self.assertNotIn("inspector_names", s, v["code"])

    def test_samples_are_newest_first_and_deterministic(self):
        for v in self.views:
            dates = [s["published_date"] for s in v["samples"]]
            self.assertEqual(dates, sorted(dates, reverse=True), v["code"])
        self.assertEqual(render.build_clause_views(self.docs, self.cfr, self.terms),
                         self.views, "같은 입력이 같은 바이트를 내지 않는다")

    def test_subsections_fold_into_their_section(self):
        """`211.100(a)` 는 `211.100` 으로 접는다 — 접지 않으면 같은 조항이 페이지
        대여섯 장으로 쪼개져 전부 얇아진다(eCFR 도 섹션 단위로만 앵커를 준다)."""
        self.assertEqual(render._cfr_section_of("21 CFR 211.100(a)"), "211.100")
        self.assertEqual(render._cfr_section_of("21 CFR 211.192"), "211.192")
        for bad in ("section 503", "21 CFR Part 211", "21 U.S.C. § 331(a)", ""):
            self.assertEqual(render._cfr_section_of(bad), "", bad)

    def test_slugs_are_url_safe_and_unique(self):
        slugs = [v["slug"] for v in self.views]
        self.assertEqual(len(slugs), len(set(slugs)), "슬러그 충돌")
        for s in slugs:
            self.assertRegex(s, r"^\d{3}-\d+$")

    def test_glossary_links_only_to_pages_that_exist(self):
        """없는 페이지로 보내는 링크는 무링크보다 나쁘다 — 사례 3건 미만 조항은
        페이지가 없으므로 용어사전도 그쪽으로 보내지 않는다."""
        slugs = {v["slug"] for v in self.views}
        view = render.build_glossary_view(
            self.terms, render._load_reg_ref_catalogs(), None, slugs)
        linked = 0
        for g in view["groups"]:
            for t in g["terms"]:
                for r in t["reg_refs"]:
                    href = r.get("cases_href") or ""
                    if not href:
                        continue
                    linked += 1
                    self.assertTrue(href.startswith("findings/clause/"), href)
                    self.assertIn(href.split("/")[2], slugs, href)
        self.assertGreater(linked, 0, "용어사전에서 조항 페이지로 가는 링크가 하나도 없다")

    def test_no_clause_slugs_means_no_glossary_link(self):
        """조항 페이지를 안 만드는 렌더(정본 부재)에서는 링크도 만들지 않는다."""
        view = render.build_glossary_view(
            self.terms, render._load_reg_ref_catalogs(), None, set())
        for g in view["groups"]:
            for t in g["terms"]:
                for r in t["reg_refs"]:
                    self.assertEqual(r.get("cases_href", ""), "")


class WebPagePathTest(unittest.TestCase):
    """[다국어 1단계 2026-09-03] 페이지 주소의 단일 원천 — `render.PagePath`.

    종전에는 렌더 호출마다 rel_root(27곳)·출력 경로(15곳)·canonical 을 손으로 따로 적었다.
    `/en/` 트리를 얹으려면 그 42곳을 전부 다시 세야 했으므로 규칙을 한 곳으로 모았다.
    여기서 고정하는 것: ①깊이 규칙 ②언어 접두 규칙(rel_root 는 언어 트리 루트·asset_root
    는 사이트 루트) ③조용히 접히는 경로 거부 ④render.py 소스에 깊이 리터럴이 되살아나지
    않는 것 ⑤실제 산출물 전수(골든 밖 수백 장 포함)의 rel_root 가 깊이와 일치하는 것.
    """

    def test_korean_tree_depth_rule(self):
        """한국어 트리(접두 없음)에서는 rel_root·asset_root 가 같고 종전 손값과 일치한다."""
        cases = {
            "": ("", "index.html"),
            "archive/": ("../", "archive/index.html"),
            "findings/browse/": ("../../", "findings/browse/index.html"),
            "findings/doc/hc-1/": ("../../../", "findings/doc/hc-1/index.html"),
            "findings/c/x/fda/": ("../../../../", "findings/c/x/fda/index.html"),
        }
        for path, (rel, out) in cases.items():
            with self.subTest(path=path):
                pp = render.PagePath(path)
                self.assertEqual(pp.lang, render.DEFAULT_LANG)
                self.assertEqual(pp.prefix, "")
                self.assertEqual(pp.site_path, path)
                self.assertEqual(pp.depth, path.count("/"))
                self.assertEqual(pp.rel_root, rel)
                self.assertEqual(pp.asset_root, rel)
                self.assertEqual(pp.out_file, out)
                self.assertEqual(pp.canonical, f"{render.SITE_BASE_URL}/{path}")

    def test_english_tree_adds_prefix_and_splits_roots(self):
        """영어 트리: 출력·canonical 에는 `en/` 이 붙고, rel_root 는 **언어 트리 루트**를,
        asset_root 는 **사이트 루트**를 가리킨다(한 단계 차이)."""
        pp = render.PagePath("findings/browse/", "en")
        self.assertEqual(pp.prefix, "en/")
        self.assertEqual(pp.site_path, "en/findings/browse/")
        self.assertEqual(pp.out_file, "en/findings/browse/index.html")
        self.assertEqual(pp.canonical, f"{render.SITE_BASE_URL}/en/findings/browse/")
        self.assertEqual(pp.depth, 3)
        self.assertEqual(pp.rel_root, "../../")        # → /en/
        self.assertEqual(pp.asset_root, "../../../")   # → /
        home = render.PagePath("", "en")
        self.assertEqual(home.site_path, "en/")
        self.assertEqual(home.out_file, "en/index.html")
        self.assertEqual(home.rel_root, "")
        self.assertEqual(home.asset_root, "../")

    def test_alternate_round_trips_between_languages(self):
        ko = render.PagePath("glossary/oos/")
        en = ko.alternate("en")
        self.assertEqual((en.path, en.lang), ("glossary/oos/", "en"))
        self.assertEqual(en.alternate("ko"), ko)
        self.assertEqual(hash(en.alternate("ko")), hash(ko))
        self.assertNotEqual(ko, en)

    def test_rejects_paths_that_would_fold_silently(self):
        """종전 `out_dir / "findings" / "doc" / slug / "index.html"` 은 slug 가 "" 이면
        Path 가 조용히 접어 부모 색인을 덮어썼다 — 주소는 조용히 접히면 안 된다."""
        for bad in ("findings/doc//", "/archive/", "archive", "../x/", "a/./b/",
                    "a\\b/", "//"):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    render.PagePath(bad)
        with self.assertRaises(ValueError):
            render.PagePath("archive/", "jp")
        pp = render.PagePath("briefs/2026-06-01/")
        for bad_name in ("", "a/b", ".", ".."):
            with self.subTest(name=bad_name):
                with self.assertRaises(ValueError):
                    pp.file(bad_name)

    def test_file_sits_beside_index(self):
        self.assertEqual(render.PagePath("briefs/2026-06-01/").file("share.txt"),
                         "briefs/2026-06-01/share.txt")
        self.assertEqual(render.PagePath("").file("404.html"), "404.html")
        self.assertEqual(render.PagePath("", "en").file("404.html"), "en/404.html")

    def test_breadcrumb_json_ld_follows_language_tree(self):
        """빵부스러기 절대 URL 도 같은 규칙 — 한국어는 종전과 바이트 동일, 영어는 /en/."""
        trail = [("홈", "/"), ("지적사항 검색", "findings/"), ("끝", "")]
        ko = render.PagePath("findings/agency/").breadcrumb_json_ld(trail)
        self.assertEqual(ko, render.build_breadcrumb_json_ld(trail))
        self.assertIn(f'"item": "{render.SITE_BASE_URL}/findings/"', ko)
        en = render.PagePath("findings/agency/", "en").breadcrumb_json_ld(trail)
        self.assertIn(f'"item": "{render.SITE_BASE_URL}/en/"', en)
        self.assertIn(f'"item": "{render.SITE_BASE_URL}/en/findings/"', en)
        self.assertNotIn(f'"item": "{render.SITE_BASE_URL}/findings/"', en)
        # 마지막 항목(현재 페이지)은 언어와 무관하게 item 을 갖지 않는다.
        self.assertEqual(ko.count('"item"'), 2)
        self.assertEqual(en.count('"item"'), 2)

    def test_archive_search_index_prefix_is_derived(self):
        """검색 인덱스 href 접두(`../`)는 손값이 아니라 아카이브 페이지 주소에서 나온다."""
        self.assertEqual(render._ARCHIVE_REL, render.PagePath("archive/").rel_root)
        self.assertEqual(render._ARCHIVE_REL, "../")

    def test_render_site_has_no_hardcoded_depth_literals(self):
        """render.py 에 `rel_root="../../"` 류 손값이 되살아나면 실패한다(AST 검사 —
        주석·docstring 의 언급은 세지 않는다). 깊이 리터럴은 PagePath 안에서만 허용."""
        import ast
        src = (WEB_DIR / "render.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        inside_pagepath: set[int] = set()
        render_site_def = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "PagePath":
                inside_pagepath = {id(n) for n in ast.walk(node)}
            if isinstance(node, ast.FunctionDef) and node.name == "render_site":
                render_site_def = node
        self.assertIsNotNone(render_site_def)
        depth_literals = [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.fullmatch(r"(\.\./)+", n.value) and id(n) not in inside_pagepath
        ]
        self.assertEqual(depth_literals, [], f"깊이 리터럴 잔존: {depth_literals}")
        hard_rel_root = [
            kw.value.value for n in ast.walk(render_site_def) if isinstance(n, ast.Call)
            for kw in n.keywords
            if kw.arg == "rel_root" and isinstance(kw.value, ast.Constant)
        ]
        self.assertEqual(hard_rel_root, [], f"rel_root 손값 잔존: {hard_rel_root}")
        abs_url_calls = [
            n for n in ast.walk(render_site_def) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "_abs_url"
        ]
        self.assertEqual(abs_url_calls, [], "render_site 안의 canonical 은 PagePath 가 낸다")

    def test_every_built_page_rel_root_matches_its_depth(self):
        """산출물 전수 대조 — 골든 8장 밖의 수백 장(용어 낱개·모음·조항·목록)까지, 페이지가
        실제로 놓인 깊이와 그 안의 rel_root(브랜드 링크 `href="{rel_root}index.html"`)가
        일치해야 한다. 비공허 하한으로 검사 대상 수를 함께 고정한다.

        ★[다국어 3단계 2026-09-04] 깊이는 **그 페이지의 언어 트리 루트 기준**이다 —
        `rel_root` 의 정의가 그것이기 때문이다(브랜드 링크는 같은 언어의 홈으로 가야 한다).
        `/en/findings/checklist/` 의 rel_root 는 `../../`(트리 깊이 2)이지 `../../../`
        (사이트 깊이 3)이 아니다. 사이트 루트로 가는 접두는 `asset_root` 가 따로 맡고,
        그 배선은 `WebEnTreeTest` 의 자산 검사가 본다."""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_pagepath_"))
        try:
            out = tmp / "site"
            _build_single(out)
            checked, en_checked = 0, 0
            for html_path in sorted(out.rglob("*.html")):
                rel_dir = html_path.relative_to(out).parent.as_posix()
                segments = [] if rel_dir == "." else rel_dir.split("/")
                is_en = bool(segments) and segments[0] == "en"
                depth = len(segments) - 1 if is_en else len(segments)
                expected = "../" * depth
                html = html_path.read_text(encoding="utf-8")
                m = re.search(r'class="brand" href="([^"]*)"', html)
                if not m:
                    continue  # base.html 을 쓰지 않는 페이지(admin 등)는 대상이 아니다
                if html_path.name == "404.html":
                    # ★[다국어 6단계 2026-09-04] 404 만 **의도적으로** 사이트 절대경로다.
                    #   이 페이지는 자기 주소에서 뜨지 않고 요청된 주소에 실려 나오므로
                    #   깊이 기반 상대경로가 요청 URL 기준으로 풀려 전부 죽는다.
                    #   그 불변식은 `WebNotFoundPageTest` 가 따로 지킨다.
                    self.assertEqual(
                        m.group(1), "/" + ("en/" if is_en else "") + "index.html",
                        f"{html_path.relative_to(out).as_posix()}: 404 의 브랜드 링크는 "
                        f"사이트 절대경로여야 한다")
                    continue
                checked += 1
                en_checked += is_en
                self.assertEqual(m.group(1), f"{expected}index.html",
                                 f"{html_path.relative_to(out).as_posix()} 의 rel_root 가 "
                                 f"언어 트리 깊이({depth})와 어긋난다")
            self.assertGreater(checked, 300, f"검사 대상이 너무 적다: {checked}")
            self.assertGreater(en_checked, 5, f"영어 트리 검사가 비었다: {en_checked}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class WebI18nTest(unittest.TestCase):
    """[다국어 2단계 2026-09-03] 문구 사전(`web/grm_i18n.py`) — 감싸기 완결·카탈로그 정합·항등.

    키는 한국어 원문이고 한국어 빌드는 항등이라 산출물이 바이트 불변이다(그 증명은 골든과
    전체 빌드 md5 대조). 여기서 고정하는 것: ①공개 템플릿·JS 에 감싸지 않은 한글이 없다
    ②소스에서 추출한 키 전량이 영어 카탈로그에 있고 고아·슬롯 불일치·미번역이 없다
    ③JS 마다 shim 사본이 바이트 동일하다 ④한국어 번역기는 항등·영어는 결손 시 즉시 실패
    ⑤검사 대상이 비공허하다(파일 수·키 수 하한).
    """

    def test_lint_is_clean(self):
        problems = grm_i18n.lint(langs=("en",), require_catalog=True)
        self.assertEqual(problems, [], "i18n lint 위반:\n" + "\n".join(problems[:40]))

    def test_scan_is_not_vacuous(self):
        self.assertGreaterEqual(len(grm_i18n.template_files()), 28)
        self.assertGreaterEqual(len(grm_i18n.asset_files()), 12)
        self.assertNotIn("admin.html", [p.name for p in grm_i18n.template_files()])
        self.assertNotIn("admin.js", [p.name for p in grm_i18n.asset_files()])
        keys = grm_i18n.collect_keys()
        self.assertGreater(len(keys), 1000, f"추출된 키가 너무 적다: {len(keys)}")
        self.assertTrue(any(k for k in keys if "{" in k), "슬롯이 든 키가 하나도 없다")

    def test_catalog_file_is_sorted_for_stable_diffs(self):
        raw = json.loads(grm_i18n.catalog_path("en").read_text(encoding="utf-8"))
        self.assertEqual(list(raw), sorted(raw), "en.json 키는 정렬 상태로 유지한다")

    def test_korean_translator_is_identity(self):
        tr = grm_i18n.Translator("ko")
        self.assertEqual(tr("문서 {n}건", n=3), "문서 3건")
        self.assertEqual(tr(""), "")
        env = render._make_env()
        out = env.from_string('{{ _("문서 {n}건 <b>{who}</b>", n=5, who=x) }}').render(x="A&B")
        self.assertEqual(out, "문서 5건 <b>A&amp;B</b>")   # 문구는 Markup, 슬롯 값은 escape
        self.assertEqual(env.from_string('{{ _("A & B") }}').render(), "A & B")

    def test_english_missing_key_fails_loudly(self):
        tr = grm_i18n.Translator("en", {"홈": "Home"})
        self.assertEqual(tr("홈"), "Home")
        with self.assertRaises(grm_i18n.MissingTranslation):
            tr("없는 키")
        with self.assertRaises(KeyError):
            grm_i18n.Translator("en", {"{n}건": "{n} items"})("{n}건")   # 슬롯 값 누락

    def test_render_helpers_default_to_korean(self):
        self.assertEqual(render.title_dateform("2026-06-22"), "2026년 6월 4주차")
        self.assertEqual(render.facet_meta("category")["title"], render.FACET_AXES["category"]["title"])
        self.assertEqual(render.facet_meta("category")["path"], "c")

    def test_render_helpers_translate_with_english_catalog(self):
        tr = grm_i18n.Translator("en", {"{y}년 {m}월 {week}주차": "Week {week}, {m}/{y}"})
        self.assertEqual(render.title_dateform("2026-06-22", tr), "Week 4, 6/2026")

    def test_js_shim_is_byte_identical_in_every_asset(self):
        for p in grm_i18n.asset_files():
            self.assertIsNone(grm_i18n.check_js_shim(p), p.name)

    def test_js_scanner_skips_comments_and_regex(self):
        src = 'var a = "가"; // "나"\n/* "다" */ var r = /"라"/; var b = _t("마");'
        bodies = [b for _, _, _, b in grm_i18n.scan_js_strings(src)]
        self.assertEqual(bodies, ["가", "마"])

    def test_lint_catches_bare_hangul_and_bad_calls(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_i18n_"))
        try:
            js = tmp / "x.js"
            js.write_text('(function(){ var a = "한글"; var b = _t(v); var c = `템플릿 ${x}`; })();',
                          encoding="utf-8")
            found = grm_i18n.find_bare_hangul_js(js)
            self.assertEqual(len(found), 3, found)
            html = tmp / "x.html"
            # 한 줄에 하나씩 — 검사기는 줄 단위로 보고한다(같은 줄의 둘째 한글은 첫째에 묻힌다).
            html.write_text('{# 주석 #}<p>본문</p>\n'
                            '<a title="속성">{{ _("감쌈") }}</a>\n'
                            '{{ "표현식" if 1 else "" }}<i>{{ _("ok") }}</i>\n'
                            '<b>면제</b> {# i18n-ignore #}\n'
                            '<script>var a = "스크립트"; // 주석 한글\n</script>\n'
                            '<style>/* 한글 주석 */ .x{content:"{{ _(\'현재\') }}"}</style>\n',
                            encoding="utf-8")
            lines = [l for l, _ in grm_i18n.find_bare_hangul_template(html)]
            self.assertEqual(lines, [1, 2, 3, 5], grm_i18n.find_bare_hangul_template(html))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class WebEnBriefTest(unittest.TestCase):
    """[다국어 5단계 2026-09-04] 영문 주간 브리프 — 슬롯·게이트·반쪽 금지.

    ★영문 서사(`title_issue`·`summary`·`implication`·`key_facts`·`checks`)와 `tldr` 은
    Routine 이 매주 만드는 산문이라 **오늘의 발행분에는 없다**. 설계 문서 §4 의 운영자
    결정이 "다음 주 발행부터 전향적"이므로, 이 클래스는 **기계장치**를 합성 브리프로
    증명한다 — 데이터가 채워지는 순간 페이지가 저절로 생기고, 안 채워지면 아무것도
    생기지 않는다는 두 방향을 모두 고정한다.
    """

    @staticmethod
    def _brief(pub="2026-06-01", *, en=True, invented=False):
        card = {
            "id": "c-en-1", "render_order": 1, "group": "글로벌",
            "group_label": "💊 합성의약품", "agency": "FDA", "card_type": "GMP 비준수",
            "category": "Other", "modality": "💊 합성의약품", "type_tag": "GMP 비준수",
            "evidence_level": "A", "signal_tier": 1, "signal_label": "High",
            "headline_target": "Acme Pharma",
            "title_issue": "세척 밸리데이션 중대결함",
            "summary": "중대결함 1건과 중요결함 8건이 확인됐다.",
            "implication": "세척 밸리데이션이 중대 결함으로 판정됐다.",
            "key_facts": ["중대결함 1건", "중요결함 8건"],
            "checks": ["세척 밸리데이션 회수율 자료 점검"],
            "facts": [{"label": "발행일", "value": "2026-05-28"},
                      {"label": "제조소", "value": "Acme Pharma"}],
            "quotes": [{"original": "critical 1, major 8", "translation": "중대 1, 중요 8"}],
            "sources": {"info_url": "https://example.org/a",
                        "official_url": "https://example.org/b"},
        }
        if en:
            card["en"] = {
                "title_issue": "Critical cleaning validation deficiency",
                "summary": ("1 critical and 8 major deficiencies were confirmed."
                            if not invented else
                            "42 major deficiencies were confirmed."),
                "implication": "Cleaning validation was classified as critical.",
                "key_facts": ["1 critical deficiency", "8 major deficiencies"],
                "checks": ["Review cleaning validation recovery data"],
            }
        meta = {"run_date_kst": pub, "publish_date": pub,
                "window": "2026-05-25~2026-05-31", "agencies": ["FDA"],
                "tldr": ["국문 요약 한 줄"], "ai_disclosure": True,
                "coverage": {"rendered": 1, "intake_total": 1,
                             "evidence": {"A": 1, "B": 0, "C": 0}}}
        if en:
            meta["en"] = {"tldr": ["One-line English summary"]}
        return {"schema": "grm-web-card/v1", "brief": meta, "cards": [card]}

    def _build(self, brief):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_enbrief_"))
        data = tmp / "data"
        data.mkdir(parents=True)
        (data / f"brief_web_{brief['brief']['publish_date']}.json").write_text(
            json.dumps(brief, ensure_ascii=False), encoding="utf-8")
        out = tmp / "site"
        render.render_site(data, out, render_doc_pages=False)
        return tmp, out

    # ── 슬롯 판정 ────────────────────────────────────────────────────────────
    def test_card_needs_all_five_narrative_fields(self):
        """다섯 중 하나만 비어도 영어로 내지 않는다 — 반쪽 영어 카드는 만들지 않는다."""
        card = self._brief()["cards"][0]
        self.assertTrue(render.card_has_english(card))
        for field in render.CARD_NARRATIVE_FIELDS:
            broken = {**card, "en": {**card["en"], field: "" if not isinstance(
                card["en"][field], list) else []}}
            self.assertFalse(render.card_has_english(broken), field)
        self.assertFalse(render.card_has_english({k: v for k, v in card.items()
                                                  if k != "en"}))

    def test_brief_needs_tldr_and_every_card(self):
        b = self._brief()
        self.assertTrue(render.brief_has_english(b))
        no_tldr = {**b, "brief": {**b["brief"], "en": {"tldr": []}}}
        self.assertFalse(render.brief_has_english(no_tldr))
        mixed = {**b, "cards": [b["cards"][0],
                                {k: v for k, v in b["cards"][0].items() if k != "en"}
                                | {"id": "c2", "render_order": 2}]}
        self.assertFalse(render.brief_has_english(mixed),
                         "한 장이라도 영어가 없으면 그 호는 영어로 내지 않는다")

    # ── 렌더 ────────────────────────────────────────────────────────────────
    def test_english_brief_and_archive_appear_when_the_data_is_there(self):
        tmp, out = self._build(self._brief())
        try:
            page = out / "en" / "briefs" / "2026-06-01" / "index.html"
            self.assertTrue(page.is_file(), "영문 브리프가 렌더되지 않았다")
            html = page.read_text(encoding="utf-8")
            self.assertIn('<html lang="en">', html)
            self.assertIn("Critical cleaning validation deficiency", html)
            self.assertIn("1 critical and 8 major deficiencies", html)
            self.assertIn("GMP non-compliance", html, "카드 종류 라벨이 한국어다")
            self.assertIn("💊 Small molecule", html, "제형 라벨이 한국어다")
            self.assertIn("Published", html, "표 라벨이 한국어다")
            for gone in ("세척 밸리데이션 중대결함", "중대결함 1건과", "GMP 비준수"):
                self.assertNotIn(gone, html, f"영문 브리프에 한국어 잔존: {gone}")
            # 앵커는 원본 값 그대로 — 두 언어판의 딥링크가 갈라지면 안 된다.
            ko = (out / "briefs" / "2026-06-01" / "index.html").read_text(encoding="utf-8")
            self.assertIn('id="sec-글로벌"', ko)
            self.assertIn('id="sec-글로벌"', html)
            archive = out / "en" / "archive" / "index.html"
            self.assertTrue(archive.is_file(), "영문 아카이브가 없다")
            self.assertIn("2026-06-01", archive.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_english_archive_and_tldr_never_fall_back_to_korean(self):
        """★제목·요약은 **영어로 다시 만든다** — 한국어 행을 거르기만 하면 안 된다.

        아카이브 제목과 브리프 description 은 `tldr[0]` 이다(`_brief_title`). 영문판이
        `brief.en.tldr` 을 안 보면 영어 화면에 한국어 제목이 그대로 실린다 — 화면 전체가
        영어인데 제목만 한국어면 "번역이 덜 됐다"로 읽힌다.
        """
        tmp, out = self._build(self._brief())
        try:
            archive = (out / "en" / "archive" / "index.html").read_text(encoding="utf-8")
            self.assertIn("One-line English summary", archive)
            self.assertNotIn("국문 요약 한 줄", archive)
            brief = (out / "en" / "briefs" / "2026-06-01" / "index.html").read_text(
                encoding="utf-8")
            self.assertIn("One-line English summary", brief)
            self.assertNotIn("국문 요약 한 줄", brief)
            # 한국어판은 그대로 한국어 요약을 쓴다(회귀 방지).
            ko = (out / "archive" / "index.html").read_text(encoding="utf-8")
            self.assertIn("국문 요약 한 줄", ko)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_nothing_is_rendered_without_the_english_slots(self):
        """오늘의 실제 발행분이 이 상태다 — 슬롯이 없으면 **아무 페이지도 생기지 않는다**."""
        tmp, out = self._build(self._brief(en=False))
        try:
            self.assertFalse((out / "en" / "briefs").exists())
            self.assertFalse((out / "en" / "archive").exists())
            sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
            self.assertNotIn("/en/briefs/", sitemap)
            self.assertNotIn("/en/archive/", sitemap)
            # 한국어판은 평소대로 나온다.
            self.assertTrue((out / "briefs" / "2026-06-01" / "index.html").is_file())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_live_briefs_have_no_english_yet_so_no_pages(self):
        """실 데이터 기준 — 지금은 영문 브리프가 0호다(다음 주 발행부터 채워진다)."""
        briefs = render.load_briefs(render.DATA_DIR)
        self.assertGreater(len(briefs), 0)
        self.assertEqual([b["brief"]["publish_date"] for b in briefs
                          if render.brief_has_english(b)], [])

    # ── 사실 게이트 ─────────────────────────────────────────────────────────
    def test_invented_numbers_block_publishing(self):
        """영문이 한국어판·표·인용에 없는 수치를 말하면 발행이 멈춘다(생성 경로 = 게이트)."""
        ok = render.validate_brief_en_facts([self._brief()])
        self.assertEqual(ok, [])
        bad = render.validate_brief_en_facts([self._brief(invented=True)])
        self.assertEqual(len(bad), 1)
        self.assertIn("EN_INVENTED_NUMBER", bad[0])
        self.assertIn("42", bad[0])

    def test_gate_is_wired_into_the_publish_path(self):
        """게이트가 배선돼 있어야 한다 — 함수만 있고 안 부르면 없는 것과 같다."""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_engate_"))
        try:
            data = tmp / "data"
            data.mkdir(parents=True)
            (data / "brief_web_2026-06-01.json").write_text(
                json.dumps(self._brief(invented=True), ensure_ascii=False),
                encoding="utf-8")
            with self.assertRaises(render.BriefEnFactValidationError):
                render._validate_briefs_or_raise(data)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_omitting_a_number_is_allowed(self):
        """요약은 덜 말할 수 있다 — 빠뜨림은 위반이 아니고 **없는 것을 말하는** 것만 위반."""
        b = self._brief()
        b["cards"][0]["en"]["summary"] = "Deficiencies were confirmed."
        b["cards"][0]["en"]["key_facts"] = ["Critical finding"]
        self.assertEqual(render.validate_brief_en_facts([b]), [])

    # ── 라벨 어휘 ───────────────────────────────────────────────────────────
    def test_every_brief_label_in_the_live_data_is_registered(self):
        """카드 라벨은 데이터로 오므로 추출기가 못 본다 — 새 값이 들어오면 여기서 실패한다."""
        registered = set(render.BRIEF_LABEL_KEYS)
        hangul = re.compile("[가-힣]")
        seen: set[str] = set()
        for b in render.load_briefs(render.DATA_DIR):
            for c in b.get("cards") or []:
                for key in ("card_type", "type_tag", "group_label", "modality", "group"):
                    v = c.get(key)
                    if v and hangul.search(str(v)):
                        seen.add(str(v))
                for f in c.get("facts") or []:
                    if f.get("label") and hangul.search(str(f["label"])):
                        seen.add(str(f["label"]))
        missing = sorted(seen - registered)
        self.assertEqual(missing, [],
                         f"BRIEF_LABEL_KEYS 미등록 라벨: {missing}")
        catalog = grm_i18n.load_catalog("en")
        for key in registered:
            self.assertIn(key, catalog, f"등록됐지만 번역이 없다: {key!r}")


class WebFindingsOrigLangTest(unittest.TestCase):
    """[다국어 6단계 2026-09-04] 검색의 원문 언어 축 — 영어 화면에 한국어 본문을 내지 않는다.

    ★계기(라이브 실측): `/en/findings/` 를 열면 최신순 첫 3쪽 지적 135건 중 **90건이
      한글 본문**이었다. 코퍼스 전체로는 영어 원문이 91.7%인데(FDA 14,936 · HC 9,505 ·
      EMA 78 · MHRA 8) 식약처가 가장 최근 편입분이라 첫 화면을 덮은 것이다. 정적 문서
      페이지는 4단계에서 원문이 영어인 문서만 골라 냈는데(`doc_is_english`) 런타임
      검색에만 그 필터가 없어서 생긴 비대칭이었다.

    이 클래스가 지키는 것은 셋이다: ①서버 축이 **모든 집계에** 걸려 있는가(한 곳만
    걸면 결과와 패싯 숫자가 갈린다) ②기본값이 트리마다 옳은가 ③**거르고 있다고 화면이
    말하고, 한 번의 클릭으로 풀 수 있는가**(말없이 거르면 코퍼스가 작다는 오해가 된다).
    """

    MIG = WEB_DIR / "migrations" / "074_findings_search_orig_lang.sql"

    @classmethod
    def setUpClass(cls):
        cls.sql = cls.MIG.read_text(encoding="utf-8")
        cls.js = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_origlang_"))
        cls.out = cls._tmp / "site"
        _build_single(cls.out)
        cls.ko = (cls.out / "findings" / "index.html").read_text(encoding="utf-8")
        cls.en = (cls.out / "en" / "findings" / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ── 서버 축 ─────────────────────────────────────────────────────────────
    def test_predicate_is_the_text_itself_not_the_agency(self):
        """판정 근거는 본문이다 — 기관으로 가르면 지금은 맞아도 낡는다.

        실측: MFDS 2,125건 중 36건이 이미 한글이 아니었다(전부 `rejected` 인 OCR 잡음).
        "MFDS = 한국어"는 데이터의 사실이 아니라 현재의 우연이다.
        """
        self.assertIn("generated always as (", self.sql)
        self.assertIn("finding_text !~ '[가-힣ᄀ-ᇿ㄰-㆏]'", self.sql)
        self.assertNotIn("agency <> 'MFDS'", self.sql)
        self.assertNotIn("agency != 'MFDS'", self.sql)

    def test_filter_applies_to_every_aggregate_not_just_the_rows(self):
        """★결과·총계·패싯이 **같은 모집단**에서 나와야 한다.

        `filtered` 에만 걸면 결과는 영어인데 기관 패싯에는 "MFDS 2,058" 이 남고, 누르면
        0건이 나온다(죽은 칩). 대시보드는 `filtered` 파생이라 자동으로 따라온다.
        """
        self.assertEqual(self.sql.count("and (not p.f_orig_en or s.original_is_english)"),
                         7, "filtered 1 + 패싯 6 = 7곳에 걸려야 한다")
        for cte in ("fac_source", "fac_cat", "fac_month", "fac_ev", "fac_rs", "fac_agency"):
            block = self.sql.split(cte + " as (", 1)[1].split("),", 1)[0]
            self.assertIn("f_orig_en", block, f"{cte} 에 언어 축이 안 걸렸다")

    def test_new_argument_is_last_and_defaulted_so_old_callers_survive(self):
        """PostgREST 는 인자가 하나만 달라도 404 다(#681) — 기존 11인자 호출을 지킨다."""
        sig = self.sql.split("create or replace function public.findings_search(", 1)[1]
        sig = sig.split(")\nreturns jsonb", 1)[0]
        self.assertTrue(sig.rstrip().rstrip(",").endswith("p_orig_lang text default ''::text"),
                        "신설 인자는 맨 뒤 + 기본값이어야 한다")
        self.assertIn("drop function if exists public.findings_search(", self.sql,
                      "옛 11인자 판을 안 내리면 11인자 호출이 모호해진다")

    def test_function_body_carries_no_comments_so_it_can_be_diffed_with_prosrc(self):
        """본문에 주석을 두지 않는다 — `md5(prosrc)` 로 프로덕션 무단 수정을 잡기 위해."""
        body = self.sql.split("as $function$", 1)[1].rsplit("$function$;", 1)[0]
        stray = [ln for ln in body.split("\n") if ln.strip().startswith("--")]
        self.assertEqual(stray, [], f"본문 주석 {stray[:3]}")

    # ── 클라이언트 ──────────────────────────────────────────────────────────
    def test_default_differs_by_tree(self):
        self.assertIn('var ORIG_LANG_DEFAULT = _isEn ? "en" : "all";', self.js)
        self.assertIn('p_orig_lang: state.orig_lang === "en" ? "en" : ""', self.js)

    def test_released_state_is_all_not_empty_so_the_url_can_hold_it(self):
        """★해제 상태가 URL 에 남아야 한다.

        `syncStateToUrl` 은 falsy 를 싣지 않는다. 해제를 빈 문자열로 두면 새로고침·공유
        시 필터가 조용히 되살아난다 — **사용자가 푼 것이 되돌아오는 것은 고장이다.**
        """
        self.assertIn('orig_lang: "orig"', self.js, "URL 키가 없다")
        # ★해제가 만드는 값은 **truthy 문자열**이어야 한다. 아래 두 곳이 이 축의 값을
        #   정하는 전부이므로 값 자체를 못박는다 — "빈 문자열이 아니다"만 보면
        #   삼항 안의 빈 문자열을 놓친다(이 테스트가 실제로 그렇게 새 나갔다).
        self.assertIn('_isEn ? "en" : "all"', self.js, "한국어 트리 기본이 빈 문자열이면 URL 에 안 남는다")
        self.assertIn('state.orig_lang = on ? "all" : "en";', self.js,
                      "해제 클릭이 빈 문자열을 만들면 새로고침 때 필터가 되살아난다")
        # 기본 상태·초기화 상태 모두 트리 기본값을 쓴다(URL 도 깨끗해진다).
        self.assertEqual(self.js.count("orig_lang: ORIG_LANG_DEFAULT,"), 3,
                         "DEFAULT_STATE · state · clearAllFilters 세 곳")

    def test_switching_language_mode_resets_to_the_first_page(self):
        """모집단이 바뀌면 페이지 번호는 의미를 잃는다(7쪽만 보다가 3쪽뿐인 결과로 가면 빈 화면)."""
        fn = self.js.split("function renderLangNote()", 1)[1].split("\n  function ", 1)[0]
        self.assertIn("currentPage = 1;", fn)
        self.assertIn("goToPage(1);", fn)

    # ── 화면이 말하는가 ─────────────────────────────────────────────────────
    def test_the_note_exists_only_in_the_english_tree(self):
        self.assertIn('id="fnd-langnote"', self.en)
        self.assertNotIn("fnd-langnote", self.ko,
                         "한국어판에는 요소도 CSS 도 남지 않는다(죽은 규칙 금지)")

    def test_the_note_is_empty_until_data_arrives(self):
        """빈 상자 금지 — 응답 전에는 hidden 이고, 정적 셸은 문구를 담지 않는다."""
        self.assertIn('<p class="fnd-langnote" id="fnd-langnote" hidden></p>', self.en)

    def test_the_note_says_what_is_hidden_and_offers_one_click_release(self):
        for key in ("원문이 영어인 지적만 보고 있습니다({n}건).",
                    "원문 언어를 가리지 않고 보고 있습니다({n}건) — 원문이 한국어인 지적이 섞여 있습니다.",
                    "전체 보기", "영어 원문만 보기"):
            self.assertIn(key, self.js, f"공지 문구 누락: {key}")
        catalog = json.loads((WEB_DIR / "data" / "i18n" / "en.json").read_text(encoding="utf-8"))
        for key in ("전체 보기", "영어 원문만 보기"):
            self.assertIn(key, catalog, f"영어 사전에 없다: {key}")


class WebEnFirmPageTest(unittest.TestCase):
    """[다국어 2026-09-04] 영어판 업체 페이지 — 슬러그는 물려받고, 숫자는 다시 센다.

    ★슬러그를 영어 문서 집합으로 새로 뽑으면 안 된다. 한국어에서 슬러그를 차지했던
      업체가 영어에 없을 때 **다른 업체가 같은 슬러그를 차지**할 수 있고, 그러면
      `/findings/firm/x/` 와 `/en/findings/firm/x/` 가 서로 다른 업체를 가리킨 채
      hreflang 으로 묶인다 — 짝이 아닌 것을 짝이라고 말하는 것이다.
    ★집계는 **그 트리의 문서 집합에서** 다시 센다. 한국어 숫자를 그대로 실으면 머리의
      "문서 7건"과 그 아래 실린 목록이 어긋나고, 사용자는 어느 쪽이 맞는지 알 수 없다.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_enfirm_"))
        cls.out = cls._tmp / "site"
        _build_single(cls.out, doc_pages=True)
        cls.ko_dir = cls.out / "findings" / "firm"
        cls.en_dir = cls.out / "en" / "findings" / "firm"
        cls.ko_slugs = {p.parent.name for p in cls.ko_dir.glob("*/index.html")}
        cls.en_slugs = {p.parent.name for p in cls.en_dir.glob("*/index.html")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_english_slugs_are_a_subset_of_the_korean_ones(self):
        """★영어 전용 슬러그가 하나라도 생기면 짝짓기가 틀어진 것이다."""
        self.assertTrue(self.en_slugs, "영문 업체 페이지가 하나도 없다")
        extra = sorted(self.en_slugs - self.ko_slugs)
        self.assertEqual(extra, [], f"한국어에 없는 영어 전용 슬러그: {extra[:5]}")

    def test_the_same_slug_is_the_same_company_in_both_trees(self):
        """짝지어진 페이지가 같은 업체여야 한다 — hreflang 이 그렇게 말하고 있으므로."""
        for slug in sorted(self.en_slugs)[:40]:
            ko = (self.ko_dir / slug / "index.html").read_text(encoding="utf-8")
            en = (self.en_dir / slug / "index.html").read_text(encoding="utf-8")
            ko_name = re.search(r'<h1 class="ff-h1">(.*?)</h1>', ko).group(1)
            en_name = re.search(r'<h1 class="ff-h1">(.*?)</h1>', en).group(1)
            # 업체명은 데이터 값이라 번역하지 않는다 — 제목 문구만 언어를 탄다.
            core = re.sub(r"\s*(지적사항 이력|findings history)\s*$", "", ko_name)
            self.assertIn(core, en_name, f"{slug}: 두 트리가 다른 업체를 가리킨다")

    def test_counts_are_recomputed_from_the_english_document_set(self):
        """머리 숫자와 실제로 실린 문서 수가 같아야 한다(한 페이지 안의 두 숫자)."""
        checked = 0
        for slug in sorted(self.en_slugs):
            en = (self.en_dir / slug / "index.html").read_text(encoding="utf-8")
            rows = len(re.findall(r'<a class="ff-doc" ', en))
            m = re.search(r"<b>([\d,]+) inspection documents?</b>", en)
            if not m:
                continue
            self.assertEqual(int(m.group(1).replace(",", "")), rows,
                             f"{slug}: 머리 숫자와 문서 목록이 어긋난다")
            # ★그리고 실린 문서가 **영어판에 실재**해야 한다. 머리와 목록이 함께
            #   한국어 집합이면 둘끼리는 맞으므로 위 검사만으로는 안 잡힌다 —
            #   그때 목록의 링크는 영어판에 없는 문서를 가리킨다.
            for href in re.findall(r'<a class="ff-doc" href="([^"]+)"', en):
                doc_slug = href.rstrip("/").rsplit("/", 1)[-1]
                self.assertTrue(
                    (self.out / "en" / "findings" / "doc" / doc_slug / "index.html").is_file(),
                    f"{slug}: 영어판에 없는 문서를 목록에 실었다({doc_slug})")
            checked += 1
        self.assertGreater(checked, 100, f"검사 대상이 너무 적다: {checked}")

    def test_no_korean_body_survives_on_an_english_firm_page(self):
        """★분류 라벨과 **대표 발췌**까지 영어여야 한다.

        종전 템플릿은 발췌를 `d.findings[0].text_ko` 로 못박아 두어, 영어 페이지에
        한국어 한 문장이 그대로 실렸다(실측). 언어를 정하는 곳은 뷰다.
        """
        hangul = re.compile(r"[가-힣]")
        bad = []
        for slug in sorted(self.en_slugs):
            html = (self.en_dir / slug / "index.html").read_text(encoding="utf-8")
            body = re.sub(r"(?s)<(script|style)\b.*?</\1>", "", html)
            body = re.sub(r"(?s)<!--.*?-->", "", body)
            text = re.sub(r"(?s)<[^>]+>", " ", body)
            hits = [h for h in re.findall(r"\S{0,14}[가-힣]+\S{0,14}", text)
                    if h.strip() != "한국어"]     # 언어 전환기 자기 이름은 제외
            if hits:
                bad.append((slug, hits[:3]))
        self.assertEqual(bad, [], f"영문 업체 페이지에 한국어 잔존: {bad[:3]}")

    def test_english_pages_do_not_link_to_korean_only_collections(self):
        """기관 모음 페이지는 아직 한국어 트리에만 있다 — 링크를 만들지 않는다."""
        for slug in sorted(self.en_slugs)[:60]:
            html = (self.en_dir / slug / "index.html").read_text(encoding="utf-8")
            self.assertEqual(re.findall(r'href="[^"]*findings/agency/[^"]*"', html), [],
                             f"{slug}: 영어판에 없는 기관 모음으로 보낸다")
            # 그렇다고 관련 링크 묶음이 비지는 않는다(빈 상자 금지).
            rel = re.search(r'<section class="ff-rel">(.*?)</section>', html, re.S)
            self.assertIsNotNone(rel, f"{slug}: 관련 링크 묶음이 없다")
            self.assertGreaterEqual(rel.group(1).count("<a "), 3, f"{slug}: 링크가 너무 적다")

    def test_document_pages_link_to_the_firm_page_only_where_it_exists(self):
        """없는 페이지로 보내지 않는다 — 영어 문서가 2건 미만인 업체는 영어판이 없다."""
        docs = sorted((self.out / "en" / "findings" / "doc").glob("*/index.html"))
        self.assertTrue(docs)
        for p in docs:
            html = p.read_text(encoding="utf-8")
            for href in re.findall(r'href="([^"?]*findings/firm/[^"?]+/)"', html):
                slug = href.rstrip("/").rsplit("/", 1)[-1]
                self.assertIn(slug, self.en_slugs,
                              f"{p.parent.name}: 영어판에 없는 업체 페이지로 보낸다")

    def test_every_english_firm_page_is_in_the_sitemap_and_paired(self):
        sitemap = (self.out / "sitemap.xml").read_text(encoding="utf-8")
        for slug in sorted(self.en_slugs):
            self.assertIn(f"{render.SITE_BASE_URL}/en/findings/firm/{slug}/</loc>", sitemap,
                          f"{slug}: sitemap 누락")
        # 짝이 생겼으므로 한국어 쪽에도 hreflang 이 붙어야 한다(단방향 짝은 짝이 아니다).
        slug = sorted(self.en_slugs)[0]
        ko = (self.ko_dir / slug / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'hreflang="en" href="{render.SITE_BASE_URL}'
                      f'/en/findings/firm/{slug}/"', ko)

    def test_the_view_is_built_by_one_function_for_both_trees(self):
        """집계 구현이 두 벌이면 언젠가 갈라진다 — 한 함수가 문서 집합만 달리 받는다."""
        rows = [{"slug": "a", "published_date": "2026-01-02", "firm_name": "Acme Ltd",
                 "agency": "FDA", "categories": ["교육/작업자"], "findings": [{}, {}]},
                {"slug": "b", "published_date": "2026-03-04", "firm_name": "Acme Ltd.",
                 "agency": "HC", "categories": ["교육/작업자", "설비/시설"],
                 "findings": [{}]}]
        v = render.firm_page_view("acme", "acme-1234", rows)
        self.assertEqual((v["doc_count"], v["finding_count"]), (2, 3))
        self.assertEqual((v["first_seen"], v["last_seen"]), ("2026-01-02", "2026-03-04"))
        self.assertEqual(v["agencies"], ["FDA", "HC"])
        self.assertEqual(v["categories"], ["교육/작업자", "설비/시설"])
        self.assertEqual(v["documents"][0]["slug"], "b", "최신순 정렬")
        # 부분집합만 주면 그 집합에서만 센다(영어판이 바로 이 경로다).
        v1 = render.firm_page_view("acme", "acme-1234", rows[:1])
        self.assertEqual((v1["doc_count"], v1["finding_count"]), (1, 2))


class WebEnFacetTest(unittest.TestCase):
    """[다국어 2026-09-04] 영어판 모음 페이지 — 숫자는 영어 모집단에서 다시 잰 값이다.

    ★한국어 집계를 영어면에 실으면 **화면의 건수와 그 페이지가 보내는 검색 결과가
      갈린다.** 실측으로 `data_integrity` 는 134건 중 50%가 한국어 원문이라 두 배
      어긋난다. 그래서 같은 생산자를 축만 바꿔 한 번 더 돌린 별도 정본
      (`findings_facets_en.json`, `--orig-lang en`)을 쓴다.
    ★사후 필터(표본에서 한글만 제거)로는 안 된다 — 표본만 고쳐지고 `findings`·
      `documents`·`by_agency` 가 한국어인 채로 남아 머리 숫자가 거짓이 된다.
    """

    @classmethod
    def setUpClass(cls):
        cls.ko_data = render.load_findings_facets()
        cls.en_data = (render.load_findings_facets(render.FINDINGS_FACETS_EN_FILE)
                       if render.FINDINGS_FACETS_EN_FILE.exists() else None)
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_enfacet_"))
        cls.out = cls._tmp / "site"
        # ★문서 페이지를 **켠 채로** 짓는다 — 사례 → 문서 링크가 실제로 이어지는지가
        #   이 클래스의 검사 항목이고, 끄면 그 경로가 통째로 검사 밖으로 나간다
        #   (WebZoneIaTest 가 도달성을 잴 때 같은 판단을 한다).
        _build_single(cls.out, doc_pages=True)
        cls.sitemap = (cls.out / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _en_paths_from_data(self):
        """영어 정본이 선언한 면 집합 — 렌더가 쓰는 **그 함수**에서 파생한다."""
        return render.facet_tree_paths(self.en_data)

    def test_english_facet_data_declares_its_population(self):
        """파일 이름이 아니라 **데이터 자신**이 어느 모집단인지 말해야 한다."""
        self.assertIsNotNone(self.en_data, "findings_facets_en.json 이 없다")
        self.assertEqual(self.en_data.get("orig_lang"), "en")
        self.assertEqual(self.en_data.get("schema_version"), "grm-findings-facets/v2")
        # 한국어 정본은 그 키를 갖지 않는다(기존 파일을 건드리지 않았다는 뜻).
        self.assertIsNone((self.ko_data or {}).get("orig_lang"))

    def test_emitted_pages_match_the_english_data_exactly(self):
        want = self._en_paths_from_data()
        got = {p.parent.relative_to(self.out / "en").as_posix() + "/"
               for p in (self.out / "en" / "findings").rglob("index.html")
               if p.parent.name not in ("findings",)}
        got = {g for g in got
               if g.startswith(("findings/c/", "findings/country/", "findings/agency/"))}
        self.assertTrue(want, "영어 정본에 면이 하나도 없다")
        self.assertEqual(sorted(want - got), [], "선언했는데 안 나온 면")
        self.assertEqual(sorted(got - want), [], "정본에 없는데 나온 면")

    def test_counts_come_from_the_english_population(self):
        """★같은 축 항목의 건수가 한국어판과 **달라야** 한다(모집단이 다르므로).

        같으면 둘 중 하나다 — 영어 데이터를 안 쓰고 있거나, 그 항목이 우연히 전부
        영어 원문이거나. 그래서 '한 항목이라도 달라야 한다'로 본다.
        """
        ko_cat = {it["slug"]: it["findings"]
                  for a in self.ko_data["axes"] if a["axis"] == "category"
                  for it in a["items"]}
        en_cat = {it["slug"]: it["findings"]
                  for a in self.en_data["axes"] if a["axis"] == "category"
                  for it in a["items"]}
        shared = sorted(set(ko_cat) & set(en_cat))
        self.assertTrue(shared)
        self.assertTrue(any(en_cat[s] < ko_cat[s] for s in shared),
                        "영어 건수가 한국어와 전부 같다 — 영어 정본을 안 쓰고 있다")
        # 그리고 화면에 그 값이 실려야 한다(데이터만 맞고 렌더가 옛 값이면 소용없다).
        meta = render.facet_meta("category", render._KO)
        for slug in shared[:8]:
            html = (self.out / "en" / "findings" / meta["path"] / slug
                    / "index.html").read_text(encoding="utf-8")
            self.assertIn(f"{en_cat[slug]:,}", html,
                          f"{slug}: 영어 건수 {en_cat[slug]:,} 가 화면에 없다")

    def test_no_korean_body_on_english_facet_pages(self):
        hangul = re.compile(r"[가-힣]")
        bad = []
        for rel in sorted(self._en_paths_from_data()):
            p = self.out / "en" / rel / "index.html"
            if not p.is_file():
                continue
            html = p.read_text(encoding="utf-8")
            body = re.sub(r"(?s)<(script|style)\b.*?</\1>", "", html)
            body = re.sub(r"(?s)<!--.*?-->", "", body)
            text = re.sub(r"(?s)<[^>]+>", " ", body)
            hits = [h for h in re.findall(r"\S{0,14}[가-힣]+\S{0,14}", text)
                    if h.strip() != "한국어"]
            if hits:
                bad.append((rel, hits[:3]))
        self.assertEqual(bad, [], f"영문 모음 페이지에 한국어 잔존: {bad[:3]}")

    def test_sample_links_point_at_documents_that_exist_in_this_tree(self):
        """사례 → 문서 링크는 **그 트리에 있는 문서**로만 잇는다."""
        missing = []
        for rel in sorted(self._en_paths_from_data()):
            p = self.out / "en" / rel / "index.html"
            if not p.is_file():
                continue
            html = p.read_text(encoding="utf-8")
            for href in re.findall(r'href="([^"#?]*findings/doc/[^"?]+/)"', html):
                slug = href.rstrip("/").rsplit("/", 1)[-1]
                if not (self.out / "en" / "findings" / "doc" / slug
                        / "index.html").is_file():
                    missing.append(f"{rel} -> {slug}")
        self.assertEqual(missing, [], f"영어판에 없는 문서로 보낸다: {missing[:5]}")

    def test_korean_only_items_get_no_english_page_and_no_pairing(self):
        """★영어에서 표본 미달로 빠진 항목은 짝을 만들지 않는다.

        한국어 페이지에 hreflang 만 붙고 그쪽에 페이지가 없으면, 검색엔진에게
        "영어판이 있다"고 말해 놓고 404 를 주는 것이다.
        """
        meta = render.facet_meta("category", render._KO)
        ko_slugs = {it["slug"] for a in self.ko_data["axes"] if a["axis"] == "category"
                    for it in a["items"]}
        en_slugs = {it["slug"] for a in self.en_data["axes"] if a["axis"] == "category"
                    for it in a["items"]}
        # 조합 축은 영어에서 실제로 줄어든다(실측 52 → 36) — 거기서 확인한다.
        cat = render.facet_meta("category", render._KO)
        ko_combo = {(c["category_slug"], c["slug"])
                    for c in (self.ko_data["combos"] or {}).get("items") or []}
        en_combo = {(c["category_slug"], c["slug"])
                    for c in (self.en_data["combos"] or {}).get("items") or []}
        dropped = sorted(ko_combo - en_combo)
        self.assertTrue(dropped, "영어에서 빠진 조합이 없다 — 이 가드가 아무것도 안 지킨다")
        for cat_slug, combo_slug in dropped[:12]:
            rel = f"findings/{cat['path']}/{cat_slug}/{combo_slug}/"
            self.assertFalse((self.out / "en" / rel / "index.html").is_file(),
                             f"{rel}: 영어 정본에 없는데 페이지가 났다")
            ko_html = (self.out / rel / "index.html").read_text(encoding="utf-8")
            self.assertNotIn(f'hreflang="en" href="{render.SITE_BASE_URL}/en/{rel}"',
                             ko_html, f"{rel}: 없는 영어판을 짝이라고 말한다")
        self.assertLessEqual(len(en_slugs), len(ko_slugs))

    def test_every_english_facet_page_is_in_the_sitemap(self):
        for rel in sorted(self._en_paths_from_data()):
            if not (self.out / "en" / rel / "index.html").is_file():
                continue
            self.assertIn(f"<loc>{render.SITE_BASE_URL}/en/{rel}</loc>", self.sitemap,
                          f"{rel}: sitemap 누락")

    def test_producer_threads_the_axis_through_every_call(self):
        """생산자가 축을 **후보 목록에도** 물어야 한다 — 여기만 빼면 excluded 사유가 거짓이 된다."""
        src = (pathlib.Path(WEB_DIR).parent / "findings_facets_refresh.py").read_text(
            encoding="utf-8")
        self.assertEqual(src.count('"p_orig_lang": orig_lang'), 3,
                         "root(dash)·축·조합 세 곳 전부에 실어야 한다")
        self.assertIn('ap.add_argument("--orig-lang"', src)
        self.assertIn('"orig_lang": orig_lang,', src, "산출물이 모집단을 스스로 밝혀야 한다")


class WebEnTreeTest(unittest.TestCase):
    """[다국어 3단계 2026-09-04] 영어 트리 `/en/` — 무엇을 내고, 무엇을 안 내는가.

    이 클래스가 지키는 불변식은 하나로 요약된다: **껍데기만 영어인 페이지를 만들지 않는다.**
    그래서 ①낼 페이지 집합이 선언과 일치하고 ②짝이 있는 페이지만 hreflang·언어 전환으로
    잇고 ③영어 페이지가 한국어 전용 섹션으로 링크하지 않고 ④영어 페이지에 한국어 UI 문구가
    남지 않는지를 본다. 데이터가 한국어인 것(영문 제목 없는 식약처 문서)은 결함이 아니라
    사실이므로, 그 사실을 화면이 **밝히는지**를 대신 검사한다.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_en_"))
        cls.out = cls._tmp / "site"
        _build_single(cls.out)
        cls.pages = {
            p.relative_to(cls.out).as_posix(): p.read_text(encoding="utf-8")
            for p in cls.out.rglob("*.html")
        }
        cls.en = {k: v for k, v in cls.pages.items() if k.startswith("en/")}
        cls.sitemap = (cls.out / "sitemap.xml").read_text(encoding="utf-8")
        # [다국어 4단계] 선언 집합 = 셸/자료실 + **원문이 영어인 실사 문서 표면**.
        # 문서 페이지는 렌더 스위치(_DOC_PAGES_IN_TESTS)로 꺼지므로 파일 비교에서는
        # 빼고 본다 — 한국어 쪽과 같은 규율이다(sitemap 은 데이터에서, 파일은 스위치대로).
        docs = render.load_findings_docs() or {}
        cls.en_docs = [d for d in docs.get("documents", []) if render.doc_is_english(d)]
        cls.expected = render.en_tree_paths(render.load_library())
        if cls.en_docs:
            cls.expected |= {"findings/docs/", "findings/browse/"}
            cls.expected |= {f"findings/docs/{d['agency'].lower()}"
                             f"/{d['published_date'][:4]}/" for d in cls.en_docs}
        cls.expected_doc_paths = {f"findings/doc/{d['slug']}/" for d in cls.en_docs}
        # [다국어 2026-09-04] 모음 면은 영어 정본에서 파생된다 — 렌더가 쓰는 바로 그
        # 함수를 쓴다(테스트가 따로 세면 두 목록이 갈라지고, 그건 이 가드가 막으려던 것이다).
        cls.en_facets = (render.load_findings_facets(render.FINDINGS_FACETS_EN_FILE)
                         if render.FINDINGS_FACETS_EN_FILE.exists() else None)
        cls.expected |= render.facet_tree_paths(cls.en_facets)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ── 집합 ────────────────────────────────────────────────────────────────
    def test_emitted_en_paths_match_the_declared_set(self):
        """렌더가 실제로 낸 영어 페이지 = `en_tree_paths()` 선언. 둘이 갈라지면 nav·
        hreflang·sitemap 이 전부 없는 페이지를 가리키게 된다(선언이 유일한 원천)."""
        emitted = {k[len("en/"):-len("index.html")] for k in self.en}
        self.assertEqual(emitted - self.expected_doc_paths, self.expected)
        self.assertGreaterEqual(len(emitted), 9, "영어 트리가 비정상적으로 작다")
        # 문서 페이지는 켠 빌드에서만 파일이 있고(비싼 3천 장), 껐을 때도 선언·sitemap 에는
        # 남는다 — 켰다면 선언한 것과 정확히 같아야 한다.
        rendered_docs = {p for p in emitted if p.startswith("findings/doc/")}
        if rendered_docs:
            self.assertEqual(rendered_docs, self.expected_doc_paths)

    def test_every_en_page_has_a_korean_counterpart(self):
        """영어판은 한국어판에 있는 면만 낸다 — 영어에만 있는 면은 짝이 없어 hreflang 이
        성립하지 않는다(영어 홈은 landing_en.html 이지만 주소 `/` 는 양쪽에 있다)."""
        for path in sorted(self.expected):
            self.assertIn(f"{path}index.html", self.pages,
                          f"영어판에만 있는 면: {path}")

    def test_korean_only_sections_are_absent_from_en(self):
        """본문이 한국어인 면은 영어 트리에 없다(설계 문서 §7 5단계 대기).

        ★[다국어 4단계 2026-09-04] 문서 표면(`findings/docs/`·`findings/browse/`)은 여기서
        빠졌다 — `findings_docs.json` 에 원문(`text_orig`)이 들어와 본문이 영어로 성립하기
        때문이다.
        ★[2026-09-04] 모음 축(`findings/c/`·`agency/`·`country/`)도 빠졌다 — 영어 모집단으로
        다시 잰 정본(`findings_facets_en.json`)이 생겨 건수까지 영어판이 성립한다.
        조항(`findings/clause/`)은 표본을 `text_ko` 로 모으고 국문 용어사전으로 링크해
        아직 남아 있다."""
        for path in ("archive/", "glossary/", "guide/", "quiz/",
                     "findings/clause/"):
            self.assertNotIn(path, self.expected)
            self.assertNotIn(f"en/{path}index.html", self.pages)

    # ── 언어판 상호 선언 ─────────────────────────────────────────────────────
    def _hreflangs(self, html):
        return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)" />',
                               html))

    def test_hreflang_on_paired_pages_both_ways(self):
        for path in sorted(self.expected):
            for rel in (f"{path}index.html", f"en/{path}index.html"):
                with self.subTest(page=rel):
                    tags = self._hreflangs(self.pages[rel])
                    self.assertEqual(
                        tags.get("ko"), f"{render.SITE_BASE_URL}/{path}")
                    self.assertEqual(
                        tags.get("en"), f"{render.SITE_BASE_URL}/en/{path}")
                    self.assertEqual(tags.get("x-default"), tags["ko"],
                                     "x-default 는 한국어판(주 언어)을 가리킨다")

    def test_unpaired_korean_pages_have_no_hreflang(self):
        """짝이 없는 면에 hreflang 을 달면 없는 페이지를 광고하는 것이다."""
        paired = self.expected | self.expected_doc_paths
        unpaired = [k for k in self.pages
                    if not k.startswith("en/")
                    and k[:-len("index.html")] not in paired
                    and k.endswith("index.html")]
        self.assertGreater(len(unpaired), 5, "검사 대상이 없다")
        for rel in unpaired[:40]:
            self.assertEqual(self._hreflangs(self.pages[rel]), {}, rel)

    def test_404_carries_no_hreflang_or_switcher(self):
        """404 는 홈과 같은 주소로 그리지만 짝이 없다(`/en/404.html` 은 서빙되지 않는다)."""
        html = (self.out / "404.html").read_text(encoding="utf-8")
        self.assertEqual(self._hreflangs(html), {})
        self.assertNotIn('class="grm-lang"', html)

    def test_html_lang_and_og_locale_follow_the_tree(self):
        for rel, html in self.pages.items():
            want = ("en", "en_US") if rel.startswith("en/") else ("ko", "ko_KR")
            with self.subTest(page=rel):
                self.assertIn(f'<html lang="{want[0]}">', html)
                if "og:locale" in html:
                    self.assertIn(f'<meta property="og:locale" content="{want[1]}" />',
                                  html)

    # ── 언어 전환 ────────────────────────────────────────────────────────────
    def test_language_switch_resolves_to_a_real_file_both_ways(self):
        """전환 링크는 **상대경로**여야 하고(호스트 무관 규약) 실제 파일에 닿아야 한다 —
        절대 URL 이면 도달성 BFS 가 못 따라가 영어 트리가 고립된 섬이 된다."""
        checked = 0
        for path in sorted(self.expected):
            for rel in (f"{path}index.html", f"en/{path}index.html"):
                m = re.search(r'class="grm-lang" href="([^"]+)"', self.pages[rel])
                self.assertIsNotNone(m, f"{rel} 에 언어 전환 링크가 없다")
                href = m.group(1)
                self.assertFalse(href.startswith("http"), f"{rel}: 절대 URL")
                target = (self.out / rel).parent / href
                self.assertTrue(target.resolve().is_file(),
                                f"{rel} → {href} 가 없는 파일을 가리킨다")
                checked += 1
        self.assertGreaterEqual(checked, 18)

    def test_switcher_absent_where_there_is_no_counterpart(self):
        for rel in ("glossary/index.html", "quiz/index.html", "guide/index.html"):
            if rel in self.pages:
                self.assertNotIn('class="grm-lang"', self.pages[rel], rel)

    # ── 링크 정책 ────────────────────────────────────────────────────────────
    def test_english_pages_never_link_into_korean_only_sections(self):
        """영어 nav·푸터·본문 어디서도 한국어 전용 섹션으로 보내지 않는다(언어 전환 링크는
        예외 — 그건 '한국어판으로 간다'고 라벨에 밝힌 의도적 간선이다)."""
        for rel, html in self.en.items():
            body = re.sub(r'<a class="grm-lang".*?</a>', "", html, flags=re.S)
            body = re.sub(r'<link rel="alternate".*?/>', "", body, flags=re.S)
            body = re.sub(r'<a class="records-all" href="[^"]*" hreflang="ko".*?</a>',
                          "", body, flags=re.S)
            for hrefs in re.findall(
                    r'href="((?:\.\./)*)(archive/|glossary/|guide/|quiz/|briefs/|me/'
                    r'|findings/clause/)',
                    body):
                self.fail(f"{rel} → 한국어 전용 섹션 링크: {''.join(hrefs)}")

    def test_en_nav_shows_only_sections_that_exist_in_english(self):
        nav = re.search(r'<nav id="navmenu">(.*?)</nav>', self.en["en/index.html"], re.S)
        self.assertIsNotNone(nav)
        for gone in ("주간 브리프", "용어사전", "이용안내",
                     "Weekly brief", "Glossary", "Guide"):
            self.assertNotIn(gone, nav.group(1))
        self.assertIn("Findings", nav.group(1))
        self.assertIn("Library", nav.group(1))

    # ── 한국어 잔존 ──────────────────────────────────────────────────────────
    def test_english_shell_pages_have_no_korean_ui_copy(self):
        """영어 화면에 남는 한글은 **언어 전환 라벨('한국어')뿐**이어야 한다.
        자료실 항목 제목은 데이터(문서의 실제 이름)라 아래 별도 테스트가 본다."""
        hangul = re.compile("[가-힣]+")
        for rel, html in self.en.items():
            if rel.startswith("en/library/"):
                continue
            body = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->",
                          " ", html, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", body)
            attrs = " ".join(m.group(1) for m in re.finditer(
                r'(?:aria-label|alt|title|placeholder|content)="([^"]*)"', body))
            leaked = sorted(set(hangul.findall(text + " " + attrs)) - {"한국어"})
            self.assertEqual(leaked, [], f"{rel} 에 한국어 UI 문구 잔존: {leaked[:6]}")

    def test_library_discloses_why_korean_titles_remain(self):
        """영문 제목이 없는 문서는 한국어 원제를 그대로 보인다(지어내지 않는다) — 대신
        **왜 그런지 화면이 밝혀야** 한다. 밝히지 않으면 번역 누락으로 읽힌다."""
        catalogs = {v["slug"]: v for v in render.load_library()}
        disclosed = 0
        for slug, view in catalogs.items():
            rel = f"en/library/{slug}/index.html"
            if rel not in self.en:
                continue
            if view["ko_only_titles"]:
                self.assertIn("no official English title", self.en[rel],
                              f"{slug}: 한국어 제목이 남는데 고지가 없다")
                disclosed += 1
            else:
                self.assertNotIn("no official English title", self.en[rel],
                                 f"{slug}: 해당 없는데 고지가 떴다")
        self.assertGreaterEqual(disclosed, 1, "고지 경로가 한 번도 안 탔다")

    def test_english_library_does_not_show_korean_subtitles(self):
        """한국어판의 병기(영문 원제)는 영어판에서 뒤집히지 않는다 — 영어 독자에게
        읽을 수 없는 줄을 얹지 않는다(sub 는 비운다)."""
        view = render._catalog_view(
            {"slug": "ich", "short": "ICH", "file": "ich.json", "unit": "토픽",
             "kick": "k", "title": "t", "blurb": "b", "intro": "i", "desc": "d"},
            {"items": [{"id": "x", "title_ko": "한글제목", "title_en": "English title"}]},
            grm_i18n.Translator("en", {"토픽": "topics", "ICH": "ICH", "t": "t",
                                       "b": "b", "i": "i", "d": "d"}))
        item = view["groups"][0]["items"][0]
        self.assertEqual(item["title"], "English title")
        self.assertEqual(item["sub"], "")

    # ── 자산·스크립트 ───────────────────────────────────────────────────────
    def test_i18n_dictionary_asset_is_loaded_only_by_english_pages(self):
        asset = self.out / "assets" / "i18n-en.js"
        self.assertTrue(asset.is_file(), "영어 문구 사전 자산이 없다")
        src = asset.read_text(encoding="utf-8")
        self.assertTrue(src.startswith("window.GRM_I18N="))
        data = json.loads(src[len("window.GRM_I18N="):].rstrip().rstrip(";"))
        self.assertEqual(data, grm_i18n.load_catalog("en"),
                         "사전 자산이 카탈로그 전량과 다르다(선별은 조용한 결손을 만든다)")
        for rel, html in self.pages.items():
            loaded = "/assets/i18n-en.js" in html
            self.assertEqual(loaded, rel.startswith("en/"), rel)

    def test_body_shim_is_present_and_used_where_findings_text_is_drawn(self):
        """지적 본문을 그리는 자산은 언어별 선택 사본을 갖고, 옛 '국문 우선' 고정 표현이
        남아 있으면 안 된다(영어판에서 한국어가 그대로 나온다)."""
        used = 0
        for p in grm_i18n.asset_files():
            src = p.read_text(encoding="utf-8")
            if grm_i18n.JS_BODY_MARKER not in src:
                continue
            used += 1
            self.assertIsNone(grm_i18n.check_js_body_shim(p), p.name)
            self.assertIn("_bodyText(", src, p.name)
            self.assertNotIn('var text = ko || row.finding_text', src, p.name)
            self.assertNotIn('var mainText = ko || row.finding_text', src, p.name)
        self.assertGreaterEqual(used, 5, "본문 자산 탐지가 비었다")

    def test_every_relative_asset_reference_resolves_to_a_real_file(self):
        """`<script src>`·`<link href>` 의 **상대경로 자산**이 실제 파일에 닿아야 한다.

        ★실제로 났던 결함이다 — 페이지별 스크립트가 `{{ rel_root }}assets/findings.js` 를
          쓰고 있었는데, `rel_root` 는 **언어 트리 루트**라 영어판에서 `/en/assets/...` 가
          되어 404 였다(자산은 언어와 무관하므로 `asset_root` = 사이트 루트를 써야 한다).
          링크 가드(`<a href>`)도 도달성 BFS 도 이걸 못 본다 — 스크립트가 죽으면 화면은
          '불러오는 중…' 에서 멈추는데 HTML 은 멀쩡하기 때문이다. 그래서 별도 검사다.
        """
        checked, en_checked = 0, 0
        for rel, html in self.pages.items():
            base = (self.out / rel).parent
            for ref in re.findall(r'<(?:script|link)\b[^>]*?(?:src|href)="([^"]+)"', html):
                if ref.startswith(("http://", "https://", "//", "/", "#", "data:")):
                    continue
                target = base / ref.split("?", 1)[0]
                checked += 1
                en_checked += rel.startswith("en/")
                self.assertTrue(target.is_file(),
                                f"{rel} → {ref} 자산이 없다(경로 접두를 잘못 쓴 것)")
        # 비공허 — 특히 **영어 트리**를 실제로 덮어야 의미가 있다(결함이 거기서 났다).
        self.assertGreater(checked, 10, f"상대경로 자산 검사가 비었다: {checked}")
        self.assertGreater(en_checked, 3, f"영어 트리 자산 검사가 비었다: {en_checked}")

    # ── [다국어 4단계] 실사 문서 표면 ────────────────────────────────────────
    def test_original_text_is_present_in_the_committed_data(self):
        """`text_orig`(규제기관 원문)가 정본에 실려 있어야 영어 문서 페이지가 성립한다.
        국문과 같으면 싣지 않는 규율이라, **다를 때만** 있는 것이 정상이다."""
        docs = render.load_findings_docs() or {}
        rows = [f for d in docs.get("documents", []) for f in d.get("findings", [])]
        self.assertGreater(len(rows), 1000, "정본이 비었다")
        with_orig = [f for f in rows if (f.get("text_orig") or "").strip()]
        self.assertGreater(len(with_orig) * 100 // len(rows), 90,
                           "원문이 실린 지적이 90% 미만 — 데이터 갱신이 빠졌다")
        for f in with_orig[:500]:
            self.assertNotEqual(f["text_orig"], f.get("text_ko"),
                                "국문과 같은 원문을 실었다(중복 저장)")

    # 영어 문서 페이지의 **본문**이 원문인지는 문서 렌더를 켠 빌드가 필요하다 —
    # 그 검사는 이미 켠 채로 짓는 `WebFindingsDocPageTest` 가 맡는다(빌드 중복 금지).

    def test_documents_with_korean_originals_stay_out_of_english(self):
        """원문이 한국어인 문서(실측 131건·전부 식약처)는 영어 트리에 없다 — 번역 누락이
        아니라 **원문이 한국어**라서다. 판정은 소스 이름이 아니라 값으로 한다."""
        docs = render.load_findings_docs() or {}
        ko_docs = [d for d in docs.get("documents", [])
                   if not render.doc_is_english(d)]
        self.assertGreater(len(ko_docs), 0, "판정이 아무것도 거르지 않았다")
        for d in ko_docs:
            self.assertNotIn(f"findings/doc/{d['slug']}/", self.expected_doc_paths)
            self.assertNotIn(f"en/findings/doc/{d['slug']}/index.html", self.pages)
        # 뺀 사실을 영어 문서 색인이 화면에 밝힌다(조용히 빼지 않는다).
        idx = self.en.get("en/findings/docs/index.html")
        if idx:
            self.assertIn("original text is Korean", idx,
                          "영어 문서 색인이 제외 사실을 밝히지 않는다")

    def test_finding_body_prefers_the_reading_language(self):
        row = {"text_ko": "국문", "text_orig": "English"}
        self.assertEqual(render.finding_body(row, "ko"), "국문")
        self.assertEqual(render.finding_body(row, "en"), "English")
        # 한쪽이 없으면 있는 쪽 — 빈 화면보다 낫다.
        self.assertEqual(render.finding_body({"text_ko": "국문"}, "en"), "국문")
        self.assertEqual(render.finding_body({"text_orig": "only"}, "ko"), "only")
        self.assertEqual(render.finding_body({}, "en"), "")

    def test_data_labels_needed_by_english_pages_are_registered(self):
        """기관 라벨은 **데이터에서** 오므로 추출기가 못 본다 — `DATA_LABEL_KEYS` 에
        등록해야 카탈로그 검사가 본다. 새 기관이 편입되면 여기서 실패해야 한다."""
        registered = set(render.DATA_LABEL_KEYS)
        for source in (render.load_findings_docs() or {}, render.load_findings_facets() or {}):
            for label in (source.get("agency_labels") or {}).values():
                self.assertIn(label, registered,
                              f"데이터 라벨 미등록: {label!r} — DATA_LABEL_KEYS 에 추가하라")
        catalog = grm_i18n.load_catalog("en")
        for key in registered:
            self.assertIn(key, catalog, f"등록됐지만 번역이 없다: {key!r}")

    # ── sitemap ─────────────────────────────────────────────────────────────
    def test_sitemap_registers_the_en_tree_except_inspector(self):
        """실사관 프로파일은 실명 개인 집계라 **언어와 무관하게** 등록하지 않는다 —
        영어판에서 색인되면 그 정책을 우회하는 것이다."""
        for path in sorted(self.expected - render.EN_SITEMAP_EXCLUDED):
            self.assertIn(f"<loc>{render.SITE_BASE_URL}/en/{path}</loc>", self.sitemap,
                          f"sitemap 에 en/{path} 누락")
        for path in sorted(render.EN_SITEMAP_EXCLUDED):
            self.assertNotIn(f"<loc>{render.SITE_BASE_URL}/en/{path}</loc>", self.sitemap)
            self.assertNotIn(f"<loc>{render.SITE_BASE_URL}/{path}</loc>", self.sitemap)


if __name__ == "__main__":
    if "--freeze" in sys.argv:
        freeze()
    else:
        unittest.main()
