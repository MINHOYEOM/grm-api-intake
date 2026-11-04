#!/usr/bin/env python3
"""지식 그래프 내보내기(web/graph_export.py) 테스트 — 가드 3종(끊긴 엣지·id 중복·개인정보)과 결정론.

CI 는 `tests/test_web_graph_export.py` shim 으로 순회한다. 직접:
  python web/tests/test_graph_export.py
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import graph_export as ge  # noqa: E402
from grm_findings import FIRM_KEY_ALIASES, canonical_firm_key  # noqa: E402

DOCS_FILE = WEB_DIR / "data" / "findings_docs.json"


def _sec(label):
    import re
    m = re.match(r"^21 CFR (\d{3}\.\d+)", str(label or "").strip())
    return m.group(1) if m else ""


def _docs():
    return [
        {"document_id": "d1", "slug": "d1", "agency": "FDA", "source": "FDA Warning Letter",
         "firm_name": "AGC Biologics S.p.A.", "firm_key": "agc biologics spa",
         "published_date": "2026-01-02", "inspector_names": ["Jane Roe"],
         "mfds_attendees": ["Kim"], "findings": [
             {"finding_id": "f1", "cfr_refs": ["21 CFR 211.100(a)", "21 CFR 211.100(b)", "section 501"]},
             {"finding_id": "f2", "cfr_refs": ["21 CFR 211.68"]}]},
        {"document_id": "d2", "slug": "d2", "agency": "FDA", "source": "FDA 483",
         "firm_name": "AGC Biologics", "firm_key": "agc biologics", "published_date": "2026-02-03",
         "findings": [{"finding_id": "f3", "cfr_refs": []}]},
    ]


def _build(docs=None):
    return ge.build_graph(
        documents=docs or _docs(), brief_slugs=["2026-02-09", "2026-01-05"],
        en_brief_slugs={"2026-02-09"},
        card_issue_index={"d1": [{"date": "2026-02-09", "anchor": "d1"},
                                 {"date": "1999-01-01", "anchor": ""}],
                          "nodoc": [{"date": "2026-02-09", "anchor": "x"}]},
        en_doc_slugs={"d2"},
        glossary_terms=[{"id": "cleanroom", "term_ko": "청정실", "term_en": "Cleanroom"},
                        {"id": "nopage", "term_ko": "페이지없음"}],
        term_ids_with_pages=["cleanroom"],
        doc_term_ids=lambda d: ["cleanroom", "cleanroom", "unknown"] if d["document_id"] == "d1" else [],
        firm_page_slug_by_key={"agc biologics": "agc-biologics-1234"},
        canonical_firm_key=canonical_firm_key, cfr_section_of=_sec,
        clause_slugs={"211.100"}, clause_slug_of=lambda s: s.replace(".", "-"))


class GraphBuildTest(unittest.TestCase):
    def test_nodes_edges_shape(self):
        g = _build()
        self.assertEqual(ge.validate_graph(g), [])
        ids = {r["id"] for n, rows in g.items() if n.startswith("nodes-") for r in rows}
        self.assertEqual(ids, {"doc:d1", "doc:d2", "firm:agc biologics", "clause:21cfr211.100",
                               "clause:21cfr211.68", "term:cleanroom",
                               "brief:2026-01-05", "brief:2026-02-09"})
        # 별칭은 정본 키 한 노드로 합쳐진다(#1320)
        firm = g["nodes-firm"][0]
        self.assertEqual(firm["doc_count"], 2)
        self.assertEqual(firm["alias_keys"], ["agc biologics spa"])
        # cited_in_brief: 없는 호(1999)·문서 없는 카드는 엣지가 되지 않는다
        e = g["edges-cited_in_brief"]
        self.assertEqual([(x["src"], x["dst"], x["src_url"], x["confidence"]) for x in e],
                         [("doc:d1", "brief:2026-02-09", "/briefs/2026-02-09/#d1", "exact")])
        # 하위항은 섹션으로 접고 weight 는 인용 지적 수
        cl = {x["dst"]: x["weight"] for x in g["edges-cites_clause"]}
        self.assertEqual(cl, {"clause:21cfr211.100": 1, "clause:21cfr211.68": 1})
        # 용어: 중복·없는 노드는 제외
        self.assertEqual(len(g["edges-uses_term"]), 1)
        self.assertEqual(g["edges-issued_to"][0]["confidence"], "name_match")
        # 영문 페이지가 있을 때만 url_en
        d = {r["id"]: r for r in g["nodes-doc"]}
        self.assertNotIn("url_en", d["doc:d1"])
        self.assertEqual(d["doc:d2"]["url_en"], "/en/findings/doc/d2/")

    def test_no_personal_data_leaks(self):
        text = "".join(ge.graph_files(_build(), built_at="x").values())
        for needle in ("Jane Roe", "inspector_names", "mfds_attendees", "Kim"):
            self.assertNotIn(needle, text)

    def test_deterministic_and_order_independent(self):
        a = ge.graph_files(_build(), built_at="2026-02-09")
        b = ge.graph_files(_build(list(reversed(_docs()))), built_at="2026-02-09")
        self.assertEqual(a, b)
        man = json.loads(a["manifest.json"])
        self.assertEqual(man["schema_version"], "1")
        for fn, m in man["files"].items():
            body = a[fn].encode("utf-8")
            self.assertEqual(m["sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(m["rows"], a[fn].count("\n"))


class GraphGuardMutationTest(unittest.TestCase):
    """가드가 실제로 깨짐을 잡는지 — 정상 그래프를 일부러 망가뜨려 위반이 나와야 한다."""

    def _mut(self, fn):
        g = copy.deepcopy(_build())
        fn(g)
        return ge.validate_graph(g)

    def test_clean_graph_passes(self):
        self.assertEqual(ge.validate_graph(_build()), [])

    def test_dangling_edge(self):
        errs = self._mut(lambda g: g["edges-issued_to"][0].update(dst="firm:없는업체"))
        self.assertTrue(any("끊긴 엣지" in e for e in errs))

    def test_duplicate_node_id(self):
        errs = self._mut(lambda g: g["nodes-doc"].append(dict(g["nodes-doc"][0])))
        self.assertTrue(any("중복" in e for e in errs))

    def test_personal_field_name(self):
        errs = self._mut(lambda g: g["nodes-doc"][0].update(inspector_names=["X"]))
        self.assertTrue(any("개인정보" in e or "허용 밖" in e for e in errs))
        errs = self._mut(lambda g: g["nodes-doc"][0].update(email="a@b.com"))
        self.assertTrue(errs)

    def test_personal_value_email_ip(self):
        errs = self._mut(lambda g: g["nodes-doc"][0].update(label="contact a@b.com"))
        self.assertTrue(any("이메일/IP" in e for e in errs))
        errs = self._mut(lambda g: g["nodes-doc"][0].update(label="host 10.1.2.3"))
        self.assertTrue(any("이메일/IP" in e for e in errs))

    def test_edge_missing_confidence_or_src_url(self):
        self.assertTrue(self._mut(lambda g: g["edges-issued_to"][0].pop("confidence")))
        self.assertTrue(self._mut(lambda g: g["edges-issued_to"][0].update(src_url="")))
        self.assertTrue(self._mut(lambda g: g["edges-issued_to"][0].update(confidence="maybe")))


@unittest.skipUnless(DOCS_FILE.exists(), "findings_docs.json 없음")
class GraphRealDataTest(unittest.TestCase):
    """실제 커밋 정본(실사관 이름이 든 문서 포함)으로 가드 3종 — 용어 선정만 빼고 전부 실제 코드."""

    @classmethod
    def setUpClass(cls):
        cls.docs = json.loads(DOCS_FILE.read_text(encoding="utf-8"))["documents"]
        cls.g = ge.build_graph(
            documents=cls.docs, brief_slugs=[], en_brief_slugs=set(), card_issue_index={},
            en_doc_slugs=set(), glossary_terms=[], term_ids_with_pages=[],
            doc_term_ids=lambda d: [], firm_page_slug_by_key={},
            canonical_firm_key=canonical_firm_key, cfr_section_of=_sec,
            clause_slugs=set(), clause_slug_of=lambda s: s)

    def test_guards(self):
        self.assertEqual(ge.validate_graph(self.g), [])
        self.assertEqual(len(self.g["nodes-doc"]), len(self.docs))

    def test_no_inspector_name_in_output(self):
        names = {n.strip() for d in self.docs
                 for n in list(d.get("inspector_names") or []) + list(d.get("mfds_attendees") or [])
                 if isinstance(n, str) and len(n.strip()) >= 6}
        self.assertTrue(names, "이 검사가 의미 있으려면 실사관 이름이 있는 문서가 있어야 한다")
        text = "".join(ge.graph_files(self.g, built_at="x").values())
        leaked = sorted(n for n in names if n in text)
        # 업체명과 우연히 같은 문자열은 허용하지 않고 목록으로 보여 준다
        self.assertEqual(leaked, [])

    def test_aliases_merged(self):
        keys = {r["id"] for r in self.g["nodes-firm"]}
        for alias, canon in FIRM_KEY_ALIASES.items():
            self.assertNotIn(f"firm:{alias}", keys)


if __name__ == "__main__":
    unittest.main()
