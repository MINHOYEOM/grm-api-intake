#!/usr/bin/env python3
"""Reconciliation uses stored source keys and keeps failures distinct from misses."""

import unittest
from datetime import date, timedelta
from unittest.mock import patch

import source_reconcile as sr


class SourceReconcileStoreTest(unittest.TestCase):
    def run_reconcile(self, *, collected=(), error=None, credentials=True):
        run_date = date(2026, 11, 20)
        spec = sr.SPEC_BY_KEY["wl"]
        doc = sr.SourceDoc("doc", "https://example.test/doc", "Title", (run_date - timedelta(days=5)).isoformat())
        env = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"} if credentials else {}
        keys = {f"{spec.source}::{key}" for key in collected}
        with patch.object(sr, "load_collected_keys_supabase", return_value=(keys, set()),
                          side_effect=error), patch.object(sr, "load_ledger", return_value={}):
            return sr.run(run_date=run_date, base_days=7, enf_days=30, specs=[spec],
                          fetchers={"wl": lambda *_: sr.FetchOutcome(docs=[doc])}, env=env)

    def test_stored_document_is_counted_as_collected(self):
        report = self.run_reconcile(collected=["doc"])
        self.assertEqual(report["collected_basis"], "supabase")
        self.assertEqual(report["sources"][0]["counts"]["collected"], 1)

    def test_missing_document_is_reported(self):
        report = self.run_reconcile()
        self.assertEqual(report["sources"][0]["counts"]["missed"], 1)

    def test_failed_read_cannot_produce_a_miss(self):
        for settings in ({"error": sr.StoreReadError("http_500")}, {"credentials": False}):
            with self.subTest(settings=settings):
                report = self.run_reconcile(**settings)
                self.assertFalse(report["store"]["collected_ok"])
                self.assertEqual(report["sources"][0]["counts"]["missed"], 0)
