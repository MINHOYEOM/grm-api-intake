"""IndexNow 내용 비교 가드 (2026-11-26).

`<lastmod>` 는 페이지 공개일이라 템플릿만 바뀐 배포는 제출 0건이었다(11-25~26 배포 16건).
배포마다 본문 지문 목록을 올리고 다음 배포가 비교해, 본문이 바뀐 URL 도 보내는지 고정한다.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import indexnow_submit as ix  # noqa: E402

H = "https://grm-solutions.com/"


def _sm(*urls):
    body = "".join(f"<url><loc>{u}</loc><lastmod>2026-11-01</lastmod></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>")


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(json.loads(data)["urlList"])
        return type("R", (), {"status_code": 200})()


class ContentDiffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.dist = self.root / "dist"
        for rel, html in (("", "<p>home</p>"), ("clauses/a/", "<p>a</p>"), ("clauses/b/", "<p>b</p>")):
            d = self.dist / rel
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.html").write_text(html + '<link href="x.css?v=abc12345">', encoding="utf-8")
        self.urls = [H, H + "clauses/a/", H + "clauses/b/"]
        (self.root / "sm.xml").write_text(_sm(*self.urls), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _man(self, name):
        path = self.root / name
        ix.main(["--sitemap", str(self.root / "sm.xml"), "--dist", str(self.dist),
                 "--write-manifest", str(path)])
        return path

    def test_url_to_file_and_digest_ignores_asset_tail(self):
        self.assertEqual(ix.url_to_file(H, "d"), os.path.join("d", "index.html"))
        self.assertEqual(ix.url_to_file(H + "a/b/", "d"), os.path.join("d", "a", "b", "index.html"))
        self.assertIsNone(ix.url_to_file("https://other.example/a/", "d"))
        self.assertEqual(ix.page_digest('<a href="s.css?v=0123abcd">'), ix.page_digest('<a href="s.css?v=ffff0000">'))
        self.assertNotEqual(ix.page_digest("<p>a</p>"), ix.page_digest("<p>b</p>"))

    def test_template_only_change_is_submitted(self):
        prev = self._man("prev.json")
        self.assertEqual(len(json.loads(prev.read_text(encoding="utf-8"))), 3)
        # 템플릿만 바뀜 — lastmod 는 그대로, 본문 하나만 달라진다. 자산 꼬리만 바뀐 페이지는 제외.
        (self.dist / "clauses" / "a" / "index.html").write_text('<p>a + 인용한 지적</p><link href="x.css?v=abc12345">',
                                                                encoding="utf-8")
        (self.dist / "clauses" / "b" / "index.html").write_text('<p>b</p><link href="x.css?v=99999999">',
                                                                encoding="utf-8")
        fake = FakeSession()
        orig = ix.submit
        try:
            ix.submit = lambda urls, endpoints=ix.ENDPOINTS: orig(urls, endpoints, session=fake)
            ix.main(["--sitemap", str(self.root / "sm.xml"), "--previous", str(self.root / "sm.xml"),
                     "--dist", str(self.dist), "--previous-manifest", str(prev)])
        finally:
            ix.submit = orig
        self.assertTrue(fake.calls)
        self.assertEqual(fake.calls[0], [H + "clauses/a/"])

    def test_without_manifest_only_lastmod_diff(self):
        cur = {u: "d" for u in self.urls}
        self.assertEqual(ix.diff_content(cur, None), [])
        self.assertEqual(ix.diff_content(cur, {H: "x"}), [H])

    def test_deploy_workflow_writes_and_uses_manifest(self):
        wf = (REPO / ".github" / "workflows" / "grm-web-deploy.yml").read_text(encoding="utf-8")
        self.assertIn("--write-manifest web/dist/indexnow-manifest.json", wf)
        self.assertIn("indexnow-manifest.json", wf.split("Deploy to Cloudflare Pages")[0])
        self.assertIn("--previous-manifest", wf.split("Deploy to Cloudflare Pages")[1])
        self.assertEqual(ix.MANIFEST_NAME, "indexnow-manifest.json")


if __name__ == "__main__":
    unittest.main()
