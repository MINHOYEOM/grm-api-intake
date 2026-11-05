#!/usr/bin/env python3
"""업체 별칭 병합으로 잃은 옛 업체 페이지 주소의 301(`_redirects`) 테스트.

CI 는 `tests/test_web_firm_alias_redirects.py` shim 으로 순회한다. 직접:
  python web/tests/test_firm_alias_redirects.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import render  # noqa: E402
from grm_findings import FIRM_KEY_ALIASES, canonical_firm_key  # noqa: E402

DOCS_FILE = WEB_DIR / "data" / "findings_docs.json"

#: 대표가 확인한 404 4건(옛 주소 → 정본 주소는 정본 키의 슬러그로 계산).
CONFIRMED_OLD = (
    "/findings/firm/dr-reddy-s-laboratories-5067b88a/",
    "/en/findings/firm/dr-reddy-s-laboratories-5067b88a/",
    "/findings/firm/people-s-custom-rx-and-clinical-care-02895cdf/",
    "/en/findings/firm/people-s-custom-rx-and-clinical-care-02895cdf/",
)


def _all_canonical_pages() -> tuple[dict[str, str], set[str]]:
    """정본 키마다 페이지가 있다고 가정한 (slug_by_key, en_slugs)."""
    slugs = {canonical_firm_key(a): render._firm_slug(canonical_firm_key(a))
             for a in FIRM_KEY_ALIASES}
    return slugs, set(slugs.values())


class FirmAliasRedirectTest(unittest.TestCase):
    def test_old_slug_matches_confirmed_404s(self):
        self.assertEqual(render._firm_slug("dr reddy’s laboratories"),
                         "dr-reddy-s-laboratories-5067b88a")
        self.assertEqual(render._firm_slug("people’s custom rx and clinical care"),
                         "people-s-custom-rx-and-clinical-care-02895cdf")

    def test_confirmed_four_in_rules(self):
        slugs, en = _all_canonical_pages()
        rules = dict(render.build_firm_alias_redirects(FIRM_KEY_ALIASES, slugs, en))
        for old in CONFIRMED_OLD:
            self.assertIn(old, rules)
        self.assertEqual(
            rules["/en/findings/firm/dr-reddy-s-laboratories-5067b88a/"],
            "/en/findings/firm/" + render._firm_slug("dr reddy's laboratories") + "/")

    def test_all_aliases_with_canonical_page_get_ko_and_en(self):
        slugs, en = _all_canonical_pages()
        rules = render.build_firm_alias_redirects(FIRM_KEY_ALIASES, slugs, en)
        self.assertEqual(len(rules), 2 * len(FIRM_KEY_ALIASES))
        self.assertEqual(len(rules), len(set(rules)))
        olds = [a for a, _ in rules]
        self.assertEqual(len(olds), len(set(olds)), "같은 옛 주소가 두 번")

    def test_no_loop_no_redirect_into_redirect_source(self):
        slugs, en = _all_canonical_pages()
        rules = render.build_firm_alias_redirects(FIRM_KEY_ALIASES, slugs, en)
        olds = {a for a, _ in rules}
        for a, b in rules:
            self.assertNotEqual(a, b)
            self.assertNotIn(b, olds, f"리다이렉트 체인: {a} -> {b}")

    def test_no_rule_when_canonical_page_missing(self):
        slugs, en = _all_canonical_pages()
        canon = canonical_firm_key("dr reddy’s laboratories")
        slugs.pop(canon)
        rules = dict(render.build_firm_alias_redirects(FIRM_KEY_ALIASES, slugs, en))
        self.assertNotIn(CONFIRMED_OLD[0], rules)
        self.assertNotIn(CONFIRMED_OLD[1], rules)
        self.assertIn(CONFIRMED_OLD[2], rules)

    def test_en_rule_only_when_english_page_exists(self):
        slugs, en = _all_canonical_pages()
        en.discard(slugs[canonical_firm_key("dr reddy’s laboratories")])
        rules = dict(render.build_firm_alias_redirects(FIRM_KEY_ALIASES, slugs, en))
        self.assertIn(CONFIRMED_OLD[0], rules)
        self.assertNotIn(CONFIRMED_OLD[1], rules)

    def test_old_slug_equal_to_live_page_is_skipped(self):
        slugs, en = _all_canonical_pages()
        slugs["other"] = render._firm_slug("dr reddy’s laboratories")
        rules = dict(render.build_firm_alias_redirects(FIRM_KEY_ALIASES, slugs, en))
        self.assertNotIn(CONFIRMED_OLD[0], rules)

    def test_txt_format_sorted_deterministic_and_under_limit(self):
        slugs, en = _all_canonical_pages()
        rules = render.build_firm_alias_redirects(FIRM_KEY_ALIASES, slugs, en)
        txt = render.build_redirects_txt(rules)
        self.assertEqual(txt, render.build_redirects_txt(list(reversed(rules))))
        body = [ln for ln in txt.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(len(body), len(rules))
        self.assertLessEqual(len(body), render.REDIRECTS_STATIC_MAX)
        self.assertEqual(body, sorted(body))
        for ln in body:
            parts = ln.split(" ")
            self.assertEqual(len(parts), 3)
            self.assertTrue(parts[0].startswith(("/findings/firm/", "/en/findings/firm/")))
            self.assertEqual(parts[2], "301")
        with self.assertRaises(RuntimeError):
            render.build_redirects_txt([(f"/a{i}/", "/b/") for i in range(2001)])

    def test_live_data_rules(self):
        """실데이터(render 와 같은 묶음 규칙: 정본 키별 문서 2건 이상 = 페이지)에서
        확인된 404 4건이 규칙에 있고, 새 주소는 전부 페이지가 있는 정본이며,
        옛 주소는 어떤 정본 페이지 주소와도 겹치지 않는다(= sitemap 에 오르지 않는다).
        (전체 렌더는 25분 걸려 CI 에서 돌리지 않는다.)"""
        if not DOCS_FILE.exists():
            self.skipTest("findings_docs.json 없음")
        docs = json.loads(DOCS_FILE.read_text(encoding="utf-8"))["documents"]
        count: dict[str, int] = {}
        for d in docs:
            k = canonical_firm_key(d.get("firm_key") or "")
            if k:
                count[k] = count.get(k, 0) + 1
        slugs = {k: render._firm_slug(k) for k, n in count.items() if n >= 2}
        rules = render.build_firm_alias_redirects(FIRM_KEY_ALIASES, slugs, set(slugs.values()))
        by_old = dict(rules)
        for old in CONFIRMED_OLD:
            self.assertIn(old, by_old)
        live_paths = {f"/findings/firm/{s}/" for s in slugs.values()} |             {f"/en/findings/firm/{s}/" for s in slugs.values()}
        self.assertTrue(rules)
        self.assertEqual(len(by_old), len(rules))
        for old, new in rules:
            self.assertIn(new, live_paths, f"404 로 가는 리다이렉트: {old} -> {new}")
            self.assertNotIn(old, live_paths)


if __name__ == "__main__":
    unittest.main()
