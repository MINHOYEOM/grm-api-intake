import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import backfill_blank_excerpts as b


LONG_TEXT = "x" * 2400 + " END"
ALERT_URL = b.MHRA_ALERT_PREFIX + "medicines-recall"


def row(source, document_id, raw, *, url="", **metadata):
    return {
        "source": source,
        "document_id": document_id,
        "official_url": url,
        "raw_json": json.dumps(raw),
        "row_json": json.dumps(metadata),
    }


class BackfillBlankExcerptsTest(unittest.TestCase):
    def setUp(self):
        self.readers = b.Readers(
            Mock(return_value=("new excerpt", {"outcome": "report"}, "ok")),
            Mock(return_value=LONG_TEXT),
            Mock(return_value=LONG_TEXT),
        )

    def test_report_9_3_selection_and_default_dry_run(self):
        rows = [
            row(b.SOURCE_WHO, "who-whopir-1", {"pdf_url": "https://who.int/1.pdf",
                                              "whopir_excerpt": "existing"}),
            row(b.SOURCE_WHO, "who-whopir-2", {"whopir_report": {"report": "existing"}}),
            row(b.SOURCE_WHO, "who-news-1", {"channel": "news"}),
            row(b.SOURCE_EMA, "ema-reviewed", {}, url="https://ema.europa.eu/excluded",
                qa_relevance="Unrelated"),
            row(b.SOURCE_EMA, "ema-undecided", {"article_excerpt": " "},
                official_url="https://ema.europa.eu/news"),
            row(b.SOURCE_EMA, "ema-reviewed-in", {}, url="https://ema.europa.eu/likely",
                qa_relevance="Likely"),
            row(b.SOURCE_EMA, "ema-filled", {"article_excerpt": "existing"}),
            row(b.SOURCE_ECA, "eca", {"link": "https://gmp-compliance.org/news"}),
            row(b.SOURCE_ECA, "eca-filled", {"eca_article_excerpt": "existing"}),
            row(b.SOURCE_MHRA, "alert", {"link": ALERT_URL}),
            row(b.SOURCE_MHRA, "alert-filled", {"mhra_alert_body": "existing"}, url=ALERT_URL),
            row(b.SOURCE_MHRA, "blog", {}, url="https://mhrainspectorate.blog.gov.uk/post"),
        ]
        write = Mock()

        results = b.backfill(rows, self.readers, write=write)

        self.assertEqual({r["document_id"]: r["status"] for r in results}, {
            "who-whopir-1": "would_apply",
            "ema-reviewed": "excluded_unrelated",
            "ema-undecided": "would_apply",
            "ema-reviewed-in": "would_apply",
            "eca": "would_apply",
            "alert": "would_apply",
        })
        self.assertEqual(results[0]["fields"], ["whopir_report"])
        self.readers.whopir.assert_called_once_with("https://who.int/1.pdf")
        self.assertEqual([call.args for call in self.readers.article.call_args_list], [
            ("https://ema.europa.eu/news", "EMA"),
            ("https://ema.europa.eu/likely", "EMA"),
            ("https://gmp-compliance.org/news", "ECA"),
        ])
        self.readers.mhra.assert_called_once_with(ALERT_URL)
        write.assert_not_called()

    def test_apply_preserves_full_ema_text_and_existing_payload(self):
        self._assert_apply_preserves_full_text(
            b.SOURCE_EMA, "article_excerpt", "https://ema.europa.eu/news",
        )

    def test_apply_preserves_full_eca_text_and_existing_payload(self):
        self._assert_apply_preserves_full_text(
            b.SOURCE_ECA, "eca_article_excerpt", "https://gmp-compliance.org/news",
        )

    def test_apply_preserves_full_mhra_text_and_existing_payload(self):
        self._assert_apply_preserves_full_text(b.SOURCE_MHRA, "mhra_alert_body", ALERT_URL)

    def _assert_apply_preserves_full_text(self, source, field, url):
        original = row(source, "doc", {"metadata": {"retain": True}}, url=url)
        write = Mock()

        results = b.backfill([original], self.readers, apply=True, write=write)

        self.assertEqual(results[0]["status"], "applied")
        write.assert_called_once_with(original, {"metadata": {"retain": True}, field: LONG_TEXT}, [field])
        self.assertEqual(json.loads(original["raw_json"]), {"metadata": {"retain": True}})

    def test_whopir_apply_saves_only_the_full_structured_report(self):
        url = "https://who.int/1.pdf"
        original = row(b.SOURCE_WHO, "who-whopir-1", {"pdf_url": url})
        pdf_text = (
            f"Part 2\n\nSummary of the findings and comments\n\n1. Documentation\n{LONG_TEXT}\n\n"
            "Part 3\nThe site was compliant.\n"
        )
        readers = b.Readers(b._read_whopir, self.readers.article, self.readers.mhra)
        write = Mock()

        with (
            patch.object(b.time, "sleep"),
            patch.object(b.who, "http_get_bytes", return_value=b"pdf"),
            patch("collect_mfds_gmp_inspection._extract_pdf_text", return_value=(pdf_text, "ok")),
        ):
            results = b.backfill([original], readers, apply=True, write=write)

        self.assertTrue(results[0]["complete"])
        self.assertEqual(results[0]["fields"], ["whopir_report"])
        saved = write.call_args.args[1]
        self.assertNotIn("whopir_excerpt", saved)
        self.assertEqual(saved["whopir_report"]["sections"][0]["text"], LONG_TEXT)

    def test_whopir_excerpt_without_report_and_empty_results(self):
        self.readers.whopir.return_value = ("excerpt only", None, "no-structure")
        self.readers.article.return_value = " "
        rows = [
            row(b.SOURCE_WHO, "whopir", {"channel": "whopir", "pdf_url": "https://who.int/1.pdf"}),
            row(b.SOURCE_ECA, "eca", {}, url="https://gmp-compliance.org/news"),
            row(b.SOURCE_EMA, "missing-url", {}),
        ]
        write = Mock()

        results = b.backfill(rows, self.readers, apply=True, write=write)

        self.assertEqual([r["status"] for r in results], ["no-structure", "empty_excerpt", "missing_url"])
        write.assert_not_called()

    def test_version_conflict_is_reported_and_other_rows_continue(self):
        rows = [row(b.SOURCE_MHRA, "conflict", {}, url=ALERT_URL),
                row(b.SOURCE_MHRA, "next", {}, url=ALERT_URL)]
        write = Mock(side_effect=[b.rv.ApplyVersionError("conflict"), None])

        results = b.backfill(rows, self.readers, apply=True, write=write)

        self.assertEqual([r["status"] for r in results], ["write_failed", "applied"])
        self.assertEqual(results[0]["reason"], "conflict")

    def test_article_adapter_preserves_collector_selection_without_length_limit(self):
        response = SimpleNamespace(text=f"<nav><p>menu</p></nav><article><p>{LONG_TEXT}</p><p>Next &amp; last.</p></article>",
                                   status_code=200, raise_for_status=Mock())
        get = Mock(return_value=response)
        with patch.object(b.intake.requests, "get", get), patch.object(b.time, "sleep"):
            self.assertEqual(
                b._read_article("https://gmp-compliance.org/news", "ECA"),
                f"{LONG_TEXT} Next & last.",
            )
        get.assert_called_once()


class ScopeOptionsTest(unittest.TestCase):
    """[11-25 #8] 완전성 점검 범위(소스·최근 적재분)만 dry/apply 할 수 있어야 한다."""

    ENV = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"}

    def _main(self, argv):
        with patch.dict("os.environ", self.ENV), \
                patch.object(b.fsb, "_fetch_all_pages", return_value=[]) as fetch, \
                patch("sys.stdout"):
            code = b.main(argv)
        return code, [c.kwargs["extra_params"] for c in fetch.call_args_list]

    def test_default_reads_all_four_sources_without_date_filter(self):
        code, params = self._main([])
        self.assertEqual(code, 0)
        self.assertEqual([p["source"] for p in params],
                         [f"eq.{s}" for s in b.TARGET_FIELDS])
        self.assertTrue(all("ingested_at" not in p for p in params))

    def test_source_and_ingested_since_narrow_the_read(self):
        _code, params = self._main(["--source", "eca", "--source", "who",
                                    "--ingested-since", "2026-09-23"])
        self.assertEqual(params, [
            {"source": f"eq.{b.SOURCE_ECA}", "ingested_at": "gte.2026-09-23"},
            {"source": f"eq.{b.SOURCE_WHO}", "ingested_at": "gte.2026-09-23"},
        ])

    def test_bad_date_is_rejected(self):
        with patch.dict("os.environ", self.ENV), patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                b.main(["--ingested-since", "09-23"])

class ArticlePacingTest(unittest.TestCase):
    """[11-25 dry 실측] EMA 1초 간격에서 429 연발 — 예의 간격 2초 + 429 재시도."""

    def _resp(self, code, headers=None, text="<p>body</p>"):
        return SimpleNamespace(status_code=code, headers=headers or {}, text=text,
                               raise_for_status=Mock())

    def test_429_waits_retry_after_then_reads(self):
        get = Mock(side_effect=[self._resp(429, {"Retry-After": "7"}), self._resp(200)])
        sleep = Mock()
        with patch.object(b.intake.requests, "get", get), patch.object(b.time, "sleep", sleep):
            self.assertEqual(b._read_article("https://www.ema.europa.eu/en/news/x", "EMA"), "body")
        self.assertEqual(get.call_count, 2)
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(waits[0], b.BACKFILL_ARTICLE_DELAY_SECONDS)
        self.assertEqual(waits[1], 7.0)

    def test_429_gives_up_after_two_retries(self):
        get = Mock(return_value=self._resp(429))
        with patch.object(b.intake.requests, "get", get), patch.object(b.time, "sleep") as sleep:
            resp = b._get_article("https://www.ema.europa.eu/en/news/x")
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(get.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],
                         list(b.BACKFILL_429_RETRY_WAITS_SECONDS))

    def test_courtesy_delays_are_at_least_the_report_rule(self):
        self.assertGreaterEqual(b.BACKFILL_WHO_DELAY_SECONDS, 5.0)
        self.assertGreaterEqual(b.BACKFILL_ARTICLE_DELAY_SECONDS, 2.0)
