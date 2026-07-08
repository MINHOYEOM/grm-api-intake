#!/usr/bin/env python3
"""FIND-1 M1 schema/taxonomy contract tests."""

from __future__ import annotations

import json
import os
import sqlite3
import unittest

import grm_findings as gf


GOLDEN = os.path.join(os.path.dirname(__file__), "golden")


def _load_input(name: str) -> dict:
    with open(os.path.join(GOLDEN, f"{name}.input.json"), encoding="utf-8") as f:
        return json.load(f)


class FindingsTaxonomyTest(unittest.TestCase):
    def test_taxonomy_v1_is_bounded_and_unique(self) -> None:
        self.assertEqual(gf.TAXONOMY_VERSION, "grm-finding-taxonomy/v1")
        self.assertGreaterEqual(len(gf.FINDING_TAXONOMY), 15)
        self.assertLessEqual(len(gf.FINDING_TAXONOMY), 20)
        self.assertEqual(
            len(gf.FINDING_CATEGORY_CODES),
            len(set(gf.FINDING_CATEGORY_CODES)),
        )
        self.assertIn("other_quality_system", gf.FINDING_CATEGORY_CODES)

    def test_classifier_prefers_specific_gmp_signal(self) -> None:
        text = (
            "무균공정 배지충전(media fill) 검증 자료가 미흡하고 "
            "환경모니터링 일탈에 대한 조사 기록이 불완전함."
        )
        self.assertEqual(
            gf.classify_finding_category(text),
            "aseptic_sterility_assurance",
        )

    def test_classifier_maps_483_investigation_signal(self) -> None:
        text = "There is a failure to thoroughly review any unexplained discrepancy."
        self.assertEqual(gf.classify_finding_category(text), "deviation_capa")


class FindingsSchemaTest(unittest.TestCase):
    def test_raw_signal_from_fda_483_fixture_validates(self) -> None:
        fx = _load_input("fda_483_observations")
        record = gf.raw_signal_from_row(
            fx["row"],
            fx["raw"],
            collected_at="2026-05-28T00:00:00+09:00",
        )
        self.assertEqual(record["schema_version"], gf.RAW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(record["source"], "FDA 483")
        self.assertEqual(record["document_id"], "fda483-192439")
        self.assertEqual(record["agency"] if "agency" in record else gf.agency_from_source(record["source"]), "FDA")
        self.assertEqual(gf.validate_raw_signal(record), [])

        again = gf.raw_signal_from_row(
            fx["row"],
            fx["raw"],
            collected_at="2026-05-28T00:00:00+09:00",
        )
        self.assertEqual(record["raw_signal_id"], again["raw_signal_id"])
        self.assertEqual(record["raw_sha256"], again["raw_sha256"])
        raw_json = json.loads(record["raw_json"])
        self.assertEqual(len(raw_json["fda_483_observations"]), 2)

    def test_finding_from_fda_483_observation_validates(self) -> None:
        fx = _load_input("fda_483_observations")
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])
        observation = fx["raw"]["fda_483_observations"][0]
        finding = gf.finding_from_raw_signal(
            raw_signal,
            finding_text=observation["deficiency"],
            ordinal=1,
            evidence_level="B",
            finding_language="EN",
            cfr_refs=["21 CFR 211"],
        )
        self.assertEqual(finding["schema_version"], gf.FINDING_SCHEMA_VERSION)
        self.assertEqual(finding["taxonomy_version"], gf.TAXONOMY_VERSION)
        self.assertEqual(finding["agency"], "FDA")
        self.assertEqual(finding["document_type"], "483")
        self.assertEqual(finding["category_code"], "deviation_capa")
        self.assertEqual(finding["cfr_refs"], ["21 CFR 211"])
        self.assertEqual(gf.validate_finding(finding), [])

    def test_gmp_fixture_builds_raw_signal_and_aseptic_finding(self) -> None:
        fx = _load_input("gmp_inspection_biologic")
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])
        finding = gf.finding_from_raw_signal(
            raw_signal,
            finding_text=fx["raw"]["attachment_deficiency_excerpt"],
            ordinal=1,
            evidence_level="A",
            evidence_url="https://nedrug.mfds.go.kr/pbp/CCBBD03",
            finding_language="KO",
        )
        self.assertEqual(raw_signal["source"], "MFDS")
        self.assertEqual(raw_signal["site_country"], "독일")
        self.assertEqual(raw_signal["modality"], "Biologic")
        self.assertEqual(finding["agency"], "MFDS")
        self.assertEqual(finding["category_code"], "aseptic_sterility_assurance")
        self.assertEqual(gf.validate_raw_signal(raw_signal), [])
        self.assertEqual(gf.validate_finding(finding), [])

    def test_validation_rejects_bad_category_and_evidence(self) -> None:
        fx = _load_input("admin_action_chemical")
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])
        finding = gf.finding_from_raw_signal(
            raw_signal,
            finding_text=fx["raw"]["EXPOSE_CONT"],
        )
        finding["category_code"] = "made_up"
        finding["taxonomy_version"] = "grm-finding-taxonomy/v0"
        finding["evidence_level"] = "D"
        errors = gf.validate_finding(finding)
        self.assertIn("findings.taxonomy_version must be grm-finding-taxonomy/v1", errors)
        self.assertIn("findings.category_code must be in grm-finding-taxonomy/v1", errors)
        self.assertIn("findings.evidence_level must be A/B/C", errors)

    def test_sqlite_schema_executes_and_accepts_valid_records(self) -> None:
        fx = _load_input("fda_483_observations")
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])
        finding = gf.finding_from_raw_signal(
            raw_signal,
            finding_text=fx["raw"]["fda_483_observations"][0]["deficiency"],
            ordinal=1,
            evidence_level="B",
        )

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(gf.sqlite_schema_ddl())

            raw_row = gf.sqlite_row(raw_signal)
            raw_cols = ", ".join(raw_row.keys())
            raw_placeholders = ", ".join("?" for _ in raw_row)
            conn.execute(
                f"INSERT INTO raw_signals ({raw_cols}) VALUES ({raw_placeholders})",
                tuple(raw_row.values()),
            )

            finding_row = gf.sqlite_row(finding)
            finding_cols = ", ".join(finding_row.keys())
            finding_placeholders = ", ".join("?" for _ in finding_row)
            conn.execute(
                f"INSERT INTO findings ({finding_cols}) VALUES ({finding_placeholders})",
                tuple(finding_row.values()),
            )

            count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
            taxonomy_version = conn.execute("SELECT taxonomy_version FROM findings").fetchone()[0]
            self.assertEqual(count, 1)
            self.assertEqual(taxonomy_version, gf.TAXONOMY_VERSION)
        finally:
            conn.close()

    def test_sqlite_schema_rejects_unknown_category(self) -> None:
        fx = _load_input("fda_483_observations")
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])
        finding = gf.finding_from_raw_signal(
            raw_signal,
            finding_text=fx["raw"]["fda_483_observations"][0]["deficiency"],
            ordinal=1,
            evidence_level="B",
        )
        finding["category_code"] = "made_up"

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(gf.sqlite_schema_ddl())
            raw_row = gf.sqlite_row(raw_signal)
            conn.execute(
                f"INSERT INTO raw_signals ({', '.join(raw_row.keys())}) "
                f"VALUES ({', '.join('?' for _ in raw_row)})",
                tuple(raw_row.values()),
            )
            finding_row = gf.sqlite_row(finding)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    f"INSERT INTO findings ({', '.join(finding_row.keys())}) "
                    f"VALUES ({', '.join('?' for _ in finding_row)})",
                    tuple(finding_row.values()),
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
