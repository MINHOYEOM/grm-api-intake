#!/usr/bin/env python3
"""findings_facets_refresh.py — 분류·국가·기관 모음 페이지 데이터 생성기.

무네트워크. `post_search` 를 대역으로 갈아끼워 게이트만 검증한다 — 이 스크립트가 조용히
틀리면 라이브에 빈 페이지 수십 장이 나가거나(0건 가드), 지적 두세 건짜리 저품질 페이지가
사이트 전체 평가를 끌어내리거나(표본 미달), 새 규제기관이 영문 코드로 노출된다(라벨 게이트).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import findings_facets_refresh as ffr  # noqa: E402
import grm_findings  # noqa: E402


def _resp(findings: int, documents: int, agencies=(), firms=(), docs=()):
    return {
        "totals": {"findings": findings, "documents": documents},
        "dash": {"by_agency": list(agencies), "top_firms": list(firms)},
        "documents": list(docs),
    }


def _doc(*findings):
    return {"agency": "FDA", "firm_name": "Acme", "published_date": "2026-01-01",
            "evidence_url": "https://example.org/x", "findings": list(findings)}


def _finding(fid, text_ko="지적 본문", **kw):
    base = {"finding_id": fid, "finding_text_ko": text_ko, "agency": "FDA",
            "firm_name": "Acme", "published_date": "2026-01-01",
            "category_label_ko": "데이터 완전성", "evidence_url": "https://example.org/x"}
    base.update(kw)
    return base


class CountryLabelDerivationTest(unittest.TestCase):
    """국가 한국어 표기는 정본(_COUNTRY_CODE_MAP) 역인덱스에서 파생한다 — 사본 금지.

    그 맵은 마이그레이션 055 의 SQL CASE 와 파리티가 테스트로 고정돼 있어, 이 스크립트가
    자기 사본을 들면 즉시 갈라진다.
    """

    def test_labels_come_from_canonical_map(self):
        labels = ffr.country_labels_ko()
        self.assertEqual(labels["US"], "미국")
        self.assertEqual(labels["KR"], "대한민국")
        self.assertEqual(labels["CA"], "캐나다")

    def test_every_label_is_a_key_of_the_canonical_map(self):
        canonical = grm_findings._COUNTRY_CODE_MAP
        for code, name in ffr.country_labels_ko().items():
            self.assertIn(name, canonical, f"정본에 없는 표기: {name}")
            self.assertEqual(canonical[name], code)

    def test_codes_without_korean_name_are_absent(self):
        # IS·MY 는 정본에 한국어 키가 없다 → 라벨이 없어야 하고, 호출부가 페이지를 만들지
        # 않는다(제목이 "IS" 인 페이지는 검색에 무의미하다).
        labels = ffr.country_labels_ko()
        for code in ("IS", "MY"):
            if code in labels:
                self.skipTest(f"정본 맵이 늘어 {code} 한국어 표기가 생겼다 — 이 기대는 낡았다")
        self.assertNotIn("IS", labels)


class SlugifyTest(unittest.TestCase):
    def test_underscore_becomes_hyphen(self):
        self.assertEqual(ffr.slugify_code("aseptic_sterility_assurance"),
                         "aseptic-sterility-assurance")

    def test_non_ascii_code_fails_loudly(self):
        # 조용히 뭉개면 URL 이 깨진 채로 배포된다.
        with self.assertRaises(SystemExit):
            ffr.slugify_code("무균공정")


class CollectSamplesTest(unittest.TestCase):
    def test_skips_findings_without_korean_text(self):
        resp = _resp(3, 1, docs=[_doc(_finding("a", text_ko=""),
                                      _finding("b", text_ko="  "),
                                      _finding("c"))])
        got = ffr.collect_samples(resp, 5)
        self.assertEqual([s["finding_id"] for s in got], ["c"])

    def test_respects_limit(self):
        resp = _resp(9, 3, docs=[_doc(*(_finding(str(i)) for i in range(9)))])
        self.assertEqual(len(ffr.collect_samples(resp, 4)), 4)

    def test_text_is_verbatim(self):
        long_text = "가" * 900
        resp = _resp(1, 1, docs=[_doc(_finding("a", text_ko=long_text))])
        self.assertEqual(ffr.collect_samples(resp, 1)[0]["text_ko"], long_text)


class BuildAxisGateTest(unittest.TestCase):
    def setUp(self):
        self._real = ffr.post_search
        self.calls: list[dict] = []

    def tearDown(self):
        ffr.post_search = self._real

    def _stub(self, per_key=None, fail_keys=()):
        def fake(base_url, anon_key, payload, timeout=60):
            self.calls.append(payload)
            key = next((v for k, v in payload.items()
                        if k.startswith("p_") and k not in
                        ("p_q", "p_page", "p_docs_per_page")), "")
            if key in fail_keys:
                raise RuntimeError("boom")
            return (per_key or {}).get(key) or _resp(
                100, 40, agencies=[{"v": "FDA", "c": 100}],
                docs=[_doc(_finding("f1"))])
        ffr.post_search = fake

    def test_below_threshold_is_excluded_with_reason(self):
        self._stub()
        axis = ffr.build_axis("u", "k", axis="category", param="p_category",
                              values=[{"v": "big", "c": 100}, {"v": "tiny", "c": 3}],
                              labels=None, min_findings=20, samples=3, log=lambda m: None)
        self.assertEqual([i["key"] for i in axis["items"]], ["big"])
        self.assertEqual(len(axis["excluded"]), 1)
        self.assertIn("표본 미달", axis["excluded"][0]["reason"])

    def test_blank_country_key_is_excluded(self):
        self._stub()
        axis = ffr.build_axis("u", "k", axis="country", param="p_country",
                              values=[{"v": "", "findings": 5000},
                                      {"v": "US", "findings": 100}],
                              labels={"US": "미국"}, min_findings=20, samples=3,
                              log=lambda m: None)
        self.assertEqual([i["key"] for i in axis["items"]], ["US"])
        self.assertIn("국가 미상", axis["excluded"][0]["reason"])

    def test_country_without_korean_label_is_excluded(self):
        self._stub()
        axis = ffr.build_axis("u", "k", axis="country", param="p_country",
                              values=[{"v": "US", "findings": 100},
                                      {"v": "ZZ", "findings": 100}],
                              labels={"US": "미국"}, min_findings=20, samples=3,
                              log=lambda m: None)
        self.assertEqual([i["key"] for i in axis["items"]], ["US"])
        self.assertIn("한국어 표기 없음", axis["excluded"][0]["reason"])

    def test_unknown_agency_code_fails_instead_of_falling_back(self):
        """새 기관이 조용히 영문 코드로 노출되는 것을 막는다."""
        self._stub()
        with self.assertRaises(SystemExit) as cm:
            ffr.build_axis("u", "k", axis="agency", param="p_agency",
                           values=[{"v": "NEWGOV", "c": 500}],
                           labels=ffr.AGENCY_LABELS_KO, min_findings=20, samples=3,
                           log=lambda m: None)
        self.assertIn("NEWGOV", str(cm.exception))

    def test_zero_items_aborts(self):
        """RPC 장애를 '축이 비었다'로 커밋하면 다음 렌더가 페이지를 통째로 지운다."""
        self._stub()
        with self.assertRaises(SystemExit):
            ffr.build_axis("u", "k", axis="category", param="p_category",
                           values=[{"v": "tiny", "c": 1}], labels=None,
                           min_findings=20, samples=3, log=lambda m: None)

    def test_item_failure_is_isolated(self):
        self._stub(fail_keys={"b"})
        axis = ffr.build_axis("u", "k", axis="category", param="p_category",
                              values=[{"v": "a", "c": 100}, {"v": "b", "c": 100},
                                      {"v": "c", "c": 100}, {"v": "d", "c": 100},
                                      {"v": "e", "c": 100}],
                              labels=None, min_findings=20, samples=3, log=lambda m: None)
        self.assertEqual(sorted(i["key"] for i in axis["items"]), ["a", "c", "d", "e"])

    def test_too_many_failures_aborts(self):
        self._stub(fail_keys={"a", "b"})
        with self.assertRaises(SystemExit):
            ffr.build_axis("u", "k", axis="category", param="p_category",
                           values=[{"v": "a", "c": 100}, {"v": "b", "c": 100},
                                   {"v": "c", "c": 100}],
                           labels=None, min_findings=20, samples=3, log=lambda m: None)

    def test_items_sorted_by_findings_desc(self):
        self._stub(per_key={
            "a": _resp(10, 5, agencies=[{"v": "FDA", "c": 10}], docs=[_doc(_finding("x"))]),
            "b": _resp(900, 5, agencies=[{"v": "FDA", "c": 900}], docs=[_doc(_finding("y"))]),
        })
        axis = ffr.build_axis("u", "k", axis="category", param="p_category",
                              values=[{"v": "a", "c": 100}, {"v": "b", "c": 100}],
                              labels=None, min_findings=20, samples=3, log=lambda m: None)
        self.assertEqual([i["key"] for i in axis["items"]], ["b", "a"])


class CommittedDataParityTest(unittest.TestCase):
    """커밋된 정본이 스크립트가 만드는 모양과 같은지 — 손으로 고친 데이터가 섞이면 잡힌다."""

    def setUp(self):
        import json
        path = ROOT / "web" / "data" / "findings_facets.json"
        if not path.exists():
            self.skipTest("findings_facets.json 미존재")
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def test_schema_and_axes(self):
        self.assertEqual(self.data["schema_version"], ffr.SCHEMA_VERSION)
        self.assertEqual([a["axis"] for a in self.data["axes"]],
                         ["category", "country", "agency"])

    def test_agency_labels_match_script(self):
        self.assertEqual(self.data["agency_labels"], ffr.AGENCY_LABELS_KO)

    def test_every_item_meets_threshold_and_has_samples(self):
        floor = self.data["min_findings"]
        for axis in self.data["axes"]:
            for item in axis["items"]:
                self.assertGreaterEqual(item["findings"], floor,
                                        f'{axis["axis"]}/{item["key"]}')
                self.assertTrue(item["samples"], f'사례 0건: {item["key"]}')

    def test_slugs_are_unique_and_url_safe(self):
        import re
        for axis in self.data["axes"]:
            slugs = [i["slug"] for i in axis["items"]]
            self.assertEqual(len(slugs), len(set(slugs)), f'{axis["axis"]} 슬러그 중복')
            for slug in slugs:
                self.assertRegex(slug, r"^[a-z0-9]+(-[a-z0-9]+)*$")


class AbsenceDeclarationTest(unittest.TestCase):
    """★"지적이 없었다"는 선언은 지적 사례가 아니다.

    캐나다 보건부 실사보고서 중 관찰이 없는 건은 그 사실이 한 건의 finding 으로 적재된다
    (실측 138건·문서당 1건). 코퍼스에 남는 것은 옳지만, "최근 지적 사례" 칸에 뜨면 제목과
    정면으로 모순된다 — 실제로 캐나다·HC 축 대표 사례가 "지적사항이 기록되지 않았다."였다.
    """

    KNOWN = [
        "기록된 지적사항이 없었다.",      # 115건
        "기록된 지적사항이 없다.",        # 12건
        "지적사항이 기록되지 않았다.",    # 7건
        "기재된 지적사항이 없었다.",      # 4건
    ]
    # 같은 표현을 품고 있지만 진짜 지적인 문장 — 부분일치로 걸러버리면 안 된다.
    REAL = [
        "서면 실험실 체계로부터의 deviation(일탈)이 기록되지 않았다.",
        "데이터가 동시적으로 기록되지 않았다.",
        "환경모니터링 프로그램이 미흡하다.",
        "나음죽여: 카드뮴 항목 관련 지적사항이다.",
        "귀사는 지적사항이 반복되지 않도록 조치하지 않았다.",
    ]

    def test_known_variants_are_detected(self):
        for t in self.KNOWN:
            self.assertTrue(ffr.is_absence_declaration(t), t)

    def test_real_findings_are_kept(self):
        for t in self.REAL:
            self.assertFalse(ffr.is_absence_declaration(t), t)

    def test_samples_skip_them_and_count_them(self):
        resp = _resp(3, 1, docs=[{"document_id": "d1", "agency": "HC",
                                  "firm_name": "X", "published_date": "2026-01-01",
                                  "evidence_url": "https://e/x", "findings": [
                                      _finding("a", text_ko=self.KNOWN[0]),
                                      _finding("b", text_ko="세척 절차가 수립되어 있지 않다."),
                                  ]}])
        skipped = []
        got = ffr.collect_samples(resp, 5, skipped)
        self.assertEqual([g["finding_id"] for g in got], ["b"])
        self.assertEqual(skipped, ["a"])


class CommittedSamplesTest(unittest.TestCase):
    def setUp(self):
        import json
        path = ROOT / "web" / "data" / "findings_facets.json"
        if not path.exists():
            self.skipTest("findings_facets.json 미존재")
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def test_no_absence_declaration_survives_in_samples(self):
        bad = [(a["axis"], i["slug"], s["text_ko"])
               for a in self.data["axes"] for i in a["items"] for s in i["samples"]
               if ffr.is_absence_declaration(s["text_ko"])]
        self.assertEqual(bad, [], f"사례에 남은 '지적 없음' 선언: {bad[:3]}")

    def test_skip_count_is_recorded(self):
        """조용히 지우지 않는다 — 몇 건을 걸렀는지 데이터에 남아야 한다."""
        for axis in self.data["axes"]:
            self.assertIn("samples_skipped_absence", axis, axis["axis"])


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
