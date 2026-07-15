#!/usr/bin/env python3
"""FIND-1 데이터 순도 2차 마이그레이션 tests (020_findings_scope_allowlist.sql).

Offline source-text checks only -- no network, no real Postgres/sqlite connection.
Mirrors the style of test_findings_scope_purity.py (010), which this migration
supersedes the (B) classify function and (D) trigger of.

The one exception to "text checks only": RuleSemanticsTest re-implements the
migration's five regexes in Python (postgres `\\y` word boundary -> python `\\b`,
`~*` -> re.I) and pins the rule's *behaviour* on the concrete live rows that
motivated this migration. That guards the part a text check cannot -- that the
allowlist runs before the denylist, and that the pharma guard actually holds.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_ALLOWLIST_MIGRATION_PATH = _MIGRATIONS_DIR / "020_findings_scope_allowlist.sql"
_SCOPE_MIGRATION_PATH = _MIGRATIONS_DIR / "010_findings_scope_purity.sql"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


class AllowlistMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            _ALLOWLIST_MIGRATION_PATH.is_file(), f"missing {_ALLOWLIST_MIGRATION_PATH}"
        )
        self.sql = _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _ALLOWLIST_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_measured_impact_counts(self) -> None:
        # The live-measured numbers (2026-07-15) this migration is anchored to.
        for token in ("8,545", "8,455", "375", "62", "272", "103", "392", "734"):
            self.assertIn(token, self.sql, f"missing measured count: {token!r}")

    def test_documents_revert_procedure(self) -> None:
        self.assertIn("되돌", self.sql)
        self.assertIn("scope_status = 'ok'", self.sql)

    def test_documents_010_supersede_relationship(self) -> None:
        self.assertIn("010_findings_scope_purity.sql", self.sql)
        self.assertIn("supersede", self.sql.lower())

    def test_declares_impact_surface(self) -> None:
        # Item 3 of the brief: every consumer of the scope_status predicate is named.
        for token in ("findings_public_read", "findings_stats", "findings_category_matrix",
                      "findings_translation_queue", "findings_similar", "trends"):
            self.assertIn(token, self.sql, f"impact surface not documented: {token!r}")


class NoNewStatusValueTest(unittest.TestCase):
    """This migration deliberately reuses 010's 3-value scope_status domain -- it must
    not touch the column, the check constraint, the public policy, or the RPCs, since
    a value-set change is what would force those to be re-declared."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(
            _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        )

    def test_does_not_alter_table_or_constraint(self) -> None:
        self.assertNotIn("alter table", self.code.lower())
        self.assertNotIn("add constraint", self.code.lower())
        self.assertNotIn("findings_scope_status_chk", self.code)

    def test_does_not_redefine_policy_or_rpcs(self) -> None:
        self.assertNotIn("create policy", self.code.lower())
        self.assertNotIn("security definer", self.code.lower())
        for fn in ("findings_stats", "findings_firm_stats", "findings_category_matrix",
                   "findings_translation_queue", "findings_translation_rows"):
            self.assertNotIn(f"function public.{fn}", self.code)

    def test_only_the_three_010_status_values_are_emitted(self) -> None:
        emitted = set(re.findall(r"'(ok|non_pharma|fragment|needs_review)'", self.code))
        self.assertEqual(emitted, {"ok", "non_pharma", "fragment"})


class ClassifyFunctionTest(unittest.TestCase):
    """(A) grm_classify_483_scope -- new 4-arg signature and rule order."""

    def setUp(self) -> None:
        self.sql = _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        match = re.search(
            r"create or replace function public\.grm_classify_483_scope\(.*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate grm_classify_483_scope body")
        self.body = match.group(1)

    def test_function_signature_takes_doc_text_and_firm_name(self) -> None:
        self.assertIn("create or replace function public.grm_classify_483_scope(", self.sql)
        for arg in ("p_est_type text", "p_len integer", "p_doc_text text", "p_firm_name text"):
            self.assertIn(arg, self.sql, f"missing arg: {arg!r}")
        self.assertIn("returns text", self.sql)
        self.assertIn("language sql", self.sql)
        self.assertIn("immutable", self.code)

    def test_allowlist_is_checked_before_denylist(self) -> None:
        # The core of the design: combination labels ("Pharmaceutical and Medical
        # Device Manufacturer") must hit the pharma allowlist before the denylist's
        # 'medical device' token can flag them. Measured 4 vs 42 over-blocks.
        allow_idx = self.body.index("outsourcing facility")
        deny_idx = self.body.index("institutional review board")
        self.assertLess(allow_idx, deny_idx)

    def test_non_pharma_checked_before_fragment(self) -> None:
        # 010's precedence is preserved: non_pharma wins over fragment.
        self.assertLess(self.body.index("'non_pharma'"), self.body.rindex("'fragment'"))

    def test_fragment_threshold_is_still_30(self) -> None:
        self.assertIn("< 30", self.body)

    def test_denylist_keeps_every_010_token(self) -> None:
        # Regression guard: 019 must not silently drop coverage 010 already had.
        for token in (
            "shell egg", "egg manufacturer", "cheese", "peanut", "sprout", "pistachio",
            "fruit processor", "pet food", "animal feed", "infant formula",
            "produce manufacturer", "aircraft", r"\yfarm\y", "institutional review board",
            "clinical investigator", "bioanalytical", "^sponsor$",
        ):
            self.assertIn(token, self.body, f"010 denylist token lost: {token!r}")

    def test_denylist_adds_the_measured_gap_tokens(self) -> None:
        for token in ("medical device", "health care facility", r"\yfood\y",
                      "smoked fish", "dietary supplement", "veterinar"):
            self.assertIn(token, self.body, f"missing new denylist token: {token!r}")

    def test_ffdca_and_form_header_are_masked_before_body_signals(self) -> None:
        # "Federal Food, Drug, and Cosmetic Act" (503B citations) and the FDA form
        # header are the known false-positive sources -- they must be stripped.
        self.assertIn("cosmetic act", self.body.lower())
        self.assertIn("food\\s+and\\s+drug\\s+administra", self.body)
        self.assertIn("regexp_replace", self.body)

    def test_body_signal_is_guarded_by_pharma_negative_lookup(self) -> None:
        # The fallback may only fire when NO pharma signal is present (`!~*`).
        self.assertIn("!~*", self.body)

    def test_uses_postgres_word_boundary_not_pcre(self) -> None:
        # Postgres ARE uses \y, not \b -- a \b here would silently mean backspace.
        self.assertIn(r"\y", self.body)
        self.assertNotIn(r"\b", self.body)


class TriggerTest(unittest.TestCase):
    """(C) trigger -- rewired to the 4-arg rule, reading doc text from raw_json."""

    def setUp(self) -> None:
        self.sql = _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_trigger_only_touches_fda_483(self) -> None:
        self.assertIn("if new.source = 'FDA 483' then", self.code)

    def test_trigger_reads_doc_text_from_observations_array(self) -> None:
        self.assertIn("fda_483_observations", self.code)
        self.assertIn("jsonb_array_elements", self.code)
        self.assertIn("deficiency", self.code)

    def test_trigger_guards_non_array_observations(self) -> None:
        # jsonb_array_elements() raises on a non-array -- an unguarded call would take
        # the whole ingestion pipeline down on one malformed raw_json.
        self.assertIn("jsonb_typeof", self.code)

    def test_trigger_defaults_to_ok_when_raw_signal_missing(self) -> None:
        self.assertIn("new.scope_status := 'ok';", self.code)

    def test_trigger_passes_four_args(self) -> None:
        self.assertIn("public.grm_classify_483_scope(\n        v_est_type,", self.code)
        self.assertIn("v_doc_text,", self.code)
        self.assertIn("coalesce(new.firm_name, '')", self.code)

    def test_trigger_recreated_before_insert_for_each_row(self) -> None:
        self.assertIn("drop trigger if exists findings_scope_status_biu on public.findings;",
                      self.code)
        self.assertIn("before insert on public.findings", self.code)
        self.assertIn("for each row execute function public.grm_findings_scope_status_trigger();",
                      self.code)


class BackfillTest(unittest.TestCase):
    """(B) backfill -- document-level aggregation, 483-only, idempotent."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(
            _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        )

    def test_backfill_is_document_scoped_via_string_agg(self) -> None:
        self.assertIn("string_agg(f.finding_text, ' ')", self.code)
        self.assertIn("group by f.raw_signal_id", self.code)

    def test_backfill_restricted_to_fda_483(self) -> None:
        self.assertEqual(self.code.count("f.source = 'FDA 483'"), 2)  # CTE + UPDATE

    def test_backfill_extracts_establishment_type_via_jsonb_cast(self) -> None:
        self.assertIn("(rs.raw_json::jsonb) ->> 'establishment_type'", self.code)

    def test_backfill_passes_firm_name(self) -> None:
        self.assertIn("coalesce(f.firm_name, '')", self.code)


class OldFunctionRetiredTest(unittest.TestCase):
    def setUp(self) -> None:
        self.code = _strip_sql_comments(
            _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        )

    def test_two_arg_function_is_dropped(self) -> None:
        self.assertIn("drop function if exists public.grm_classify_483_scope(text, integer);",
                      self.code)

    def test_drop_happens_after_trigger_is_rewired(self) -> None:
        # Dropping before the trigger function is replaced would leave the trigger
        # pointing at a function that no longer exists.
        trigger_idx = self.code.index("create trigger findings_scope_status_biu")
        drop_idx = self.code.index("drop function if exists public.grm_classify_483_scope")
        self.assertLess(trigger_idx, drop_idx)


class SearchPathTest(unittest.TestCase):
    def test_both_functions_pin_search_path(self) -> None:
        code = _strip_sql_comments(_ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8"))
        # grm_classify_483_scope + grm_findings_scope_status_trigger, no RPCs here.
        self.assertEqual(code.count("set search_path = public"), 2)


class DownstreamPredicateInheritanceTest(unittest.TestCase):
    """020 claims every scope_status consumer inherits the fix for free. That claim is
    only true while those consumers actually filter on scope_status='ok' -- pin it, so
    a future migration that drops the predicate fails here instead of silently
    re-publishing non-pharma rows through search/similarity."""

    def test_018_lexical_similar_filters_scope_status(self) -> None:
        path = _MIGRATIONS_DIR / "018_findings_similar_lexical.sql"
        self.assertTrue(path.is_file(), f"missing {path}")
        self.assertIn("scope_status = 'ok'", path.read_text(encoding="utf-8"))

    def test_019_embedding_similar_filters_scope_status_on_both_sides(self) -> None:
        # 019 findings_similar_by_id() gates the base finding AND the candidate set.
        path = _MIGRATIONS_DIR / "019_findings_embeddings.sql"
        self.assertTrue(path.is_file(), f"missing {path}")
        sql = path.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            sql.count("scope_status = 'ok'"), 2,
            "019 must gate both the base finding and the candidate set",
        )


class UpstreamHeaderNoteTest(unittest.TestCase):
    """010 must carry a pointer to 019 as the production source of truth for the
    classify rule, mirroring the 007/008/009 -> 010 convention 010 itself set."""

    def test_010_has_019_pointer_comment(self) -> None:
        sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("020_findings_scope_allowlist.sql", sql)

    def test_010_classify_body_left_intact_for_revert(self) -> None:
        sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "create or replace function public.grm_classify_483_scope"
            "(p_est_type text, p_len integer)",
            sql,
        )


# ---------------------------------------------------------------------------
# Behavioural pin: the SQL regexes, re-implemented, run against the concrete live
# rows this migration exists for. Postgres `\y` -> python `\b`; `~*` -> re.I.
# ---------------------------------------------------------------------------

def _extract_regexes() -> dict[str, str]:
    body = _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
    found = re.findall(r"~\*\s*'(\(.*?)'\n", body) + re.findall(r"!~\*\s*'(\(.*?)'\n", body)
    return {"all": found}


class RuleSemanticsTest(unittest.TestCase):
    """Pins rule behaviour on the live rows measured 2026-07-15. Regex strings are
    read out of the .sql file itself, so drift in the migration fails here."""

    # Mirrors of the five .sql regexes -- asserted below to be byte-present in the file.
    PHARMA_EST = (r"(drug|pharmac|\yapi\y|active pharmaceutical|outsourcing facility|compound"
                  r"|biolog|sterile|vaccine|plasma|blood|red cross|tissue|nuclear|dosage"
                  r"|homeopathic|heparin|anda sponsor|own label|repacker/relabeler"
                  r"|(control|contract) testing laborator)")
    NONPHARMA_EST = (r"(shell egg|egg manufacturer|cheese|peanut|sprout|pistachio|fruit processor"
                     r"|pet food|animal feed|infant formula|produce manufacturer|aircraft|\yfarm\y"
                     r"|institutional review board|clinical investigator|bioanalytical|^sponsor$"
                     r"|medical device|health care facility|\yfood\y|smoked fish|dietary supplement"
                     r"|veterinar)")
    STRONG_DEVICE = (r"(\yMDR\y|medical device report|device history record|device master record"
                     r"|finished devices?\y|user facility|21 CFR 820|\y820\.\d"
                     r"|design (input|output|history file)|marketed device)")
    STRONG_FOOD = (r"(food[- ]contact|\yfoods?\y|animal food|low[- ]acid canned|infant formula"
                   r"|\yHACCP\y|\yjuice\y|seafood|ice cream|\ycheese\y|\ymilk\y)")
    STRONG_PHARMA = (r"(drug products?|drug substance|active pharmaceutical ingredient|aseptic"
                     r"|sterilit|\ysterile\y|compounded?|\yUSP\y|batch record|master production"
                     r"|\y211\.\d|finished pharmaceutical|prescription|\yNDC\y|\yOTC\y|potency"
                     r"|adverse drug|quality control unit|\yDSCSA\y|tablets?|capsules?"
                     r"|injectable|vials?)")
    FOOD_FIRM = (r"(creamer|creamery|dairy|\yfoods?\y|\yfarms?\y|orchard|bakery|baking|tortiller"
                 r"|produce|beverage|brewing|nestle|juice)")
    MASK = (r"(federal food,?\s*drug,?\s*(and|&)\s*cosmetic act|food\s+and\s+drug\s+administra"
            r"|\yFD&C\y|department of health)")

    @staticmethod
    def _p(pattern: str) -> re.Pattern[str]:
        return re.compile(pattern.replace(r"\y", r"\b"), re.I)

    @classmethod
    def _classify(cls, est: str, length: int, doc: str, firm: str) -> str:
        clean = cls._p(cls.MASK).sub(" ", doc or "")
        if cls._p(cls.PHARMA_EST).search(est or ""):
            return "fragment" if length < 30 else "ok"
        if cls._p(cls.NONPHARMA_EST).search(est or ""):
            return "non_pharma"
        if not cls._p(cls.STRONG_PHARMA).search(clean) and (
            cls._p(cls.STRONG_DEVICE).search(clean)
            or cls._p(cls.STRONG_FOOD).search(clean)
            or cls._p(cls.FOOD_FIRM).search(firm or "")
        ):
            return "non_pharma"
        return "fragment" if length < 30 else "ok"

    def test_mirrored_regexes_are_byte_present_in_the_migration(self) -> None:
        sql = _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        for name in ("PHARMA_EST", "NONPHARMA_EST", "STRONG_DEVICE", "STRONG_FOOD",
                     "STRONG_PHARMA", "FOOD_FIRM", "MASK"):
            self.assertIn(getattr(self, name), sql, f"{name} drifted from the .sql")

    def test_live_food_leaks_are_flagged(self) -> None:
        # Real rows, live at 2026-07-15, all scope_status='ok' before this migration.
        cases = [
            ("Blue Bell Creameries, LP", "Manufacturer",
             "Failure to handle and maintain equipment, containers and utensils used to hold "
             "food in a manner that protects against contamination."),
            ("Plainview Milk Products Cooperative", "Manufacturer",
             "Failure to maintain buildings and fixtures in repair sufficient to prevent food "
             "from becoming adulterated."),
            ("Bravo Packing, Inc.", "Manufacturer",
             "You did not hold animal food for distribution under conditions that protect "
             "against contamination and minimize deterioration."),
            ("Sanger Fresh Cut Produce Co. LLC",
             "Initial Distributor, Manufacturer, Specification Developer",
             "Hand-washing facilities lack running water of a suitable temperature."),
            ("San Francisco Herb and Natural Food Company", "Importer/Warehouse/Repacker",
             "Failure to store finished food under conditions that would protect against "
             "microbial contamination."),
            ("Thermo Pac LLC", "Manufacturer",
             "Failure to provide FDA, before packing any new product, information as to the "
             "scheduled process for each low-acid canned food in each container."),
        ]
        for firm, est, doc in cases:
            with self.subTest(firm=firm):
                self.assertEqual(self._classify(est, len(doc), doc, firm), "non_pharma")

    def test_live_device_leaks_are_flagged(self) -> None:
        cases = [
            ("Advanced Medical Optics, Inc",
             "Initial Distributor, Manufacturer, Specification Developer",
             "Design input requirements were not adequately documented. Design output was not "
             "adequately established."),
            ("Advocate Lutheran General Hospital", "Health Care Facility",
             "Written MDR procedures have not been developed."),
            ("Hill-Rom, Inc.", "Manufacturer",
             "Rework and reevaluation activities have not been documented in the device history "
             "record."),
        ]
        for firm, est, doc in cases:
            with self.subTest(firm=firm):
                self.assertEqual(self._classify(est, len(doc), doc, firm), "non_pharma")

    def test_legitimate_pharma_on_generic_est_type_stays_public(self) -> None:
        # The over-blocking risk the brief flagged: 392 findings / 68 docs live on a
        # generic est_type and are real pharma. These must survive.
        cases = [
            ("Teva Parenteral Medicines, Inc.", "Manufacturer",
             "The aseptic processing area is deficient. Sterile drug products are at risk."),
            ("Hospira Inc. A Pfizer Company", "Manufacturer",
             "Batch record review for drug products was not adequate."),
            ("Gilead Sciences, Inc", "Manufacturer",
             "The quality control unit did not review and approve the batch record for the drug "
             "product prior to release."),
            ("American Family Pharmacy, LLC", "Manufacturer",
             "Compounded sterile preparations were not tested for potency prior to dispensing."),
            ("Premier Pharmacy Labs, Inc.", "",
             "Aseptic technique was deficient during the compounding of sterile drug products."),
        ]
        for firm, est, doc in cases:
            with self.subTest(firm=firm):
                self.assertEqual(self._classify(est, len(doc), doc, firm), "ok")

    def test_combination_labels_survive_the_denylist(self) -> None:
        # Allowlist-first is what makes these pass -- they contain denylist tokens.
        doc = "Batch record review for drug products was not adequate."
        for est in ("Pharmaceutical and Medical Device Manufacturer",
                    "Human Tissue and Medical Device Manufacturer",
                    "Biologics &amp; Medical Device Manufacturer",
                    "Medical Food and OTC Drug Manufacturer"):
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "Hospira Inc."), "ok")

    def test_ffdca_citation_does_not_trigger_food_flag(self) -> None:
        # The measured false-positive source: a 503B outsourcing-facility citation.
        doc = ("Drug products were not compounded in accordance with section 503B of the "
               "Federal Food, Drug, and Cosmetic Act.")
        self.assertEqual(self._classify("", len(doc), doc, "Ameridose, LLC"), "ok")

    def test_fda_form_header_ocr_noise_does_not_trigger_food_flag(self) -> None:
        doc = ("DEPARTMENT OF HEALTH AND HUMAN SERVICES FOOD AND DRUG ADMINISTRATION "
               "Aseptic processing deficiencies were observed in the sterile drug suite.")
        self.assertEqual(self._classify("", len(doc), doc, "Catalent Indiana, LLC"), "ok")

    def test_explicit_pharma_est_type_short_text_is_fragment_not_non_pharma(self) -> None:
        # 010's fragment behaviour is preserved for allowlisted est_types.
        self.assertEqual(
            self._classify("Producer of Sterile Drug Products", 5, "Promised to correct", "X"),
            "fragment",
        )

    def test_010_denylist_est_types_are_still_flagged(self) -> None:
        doc = "Observation text with no decisive signal either way."
        for est in ("Shell Egg Producer", "Cheese Manufacturer", "Pet Food Manufacturer",
                    "Animal Feed Manufacturer", "Infant Formula Manufacturer", "Farm",
                    "Institutional Review Board", "Clinical Investigator", "Bioanalytical Lab",
                    "Sponsor", "Aircraft Conveyance"):
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "X"), "non_pharma")

    def test_anda_sponsor_is_not_caught_by_the_sponsor_denylist_anchor(self) -> None:
        # '^sponsor$' is anchored: "ANDA Sponsor" is a drug sponsor and must stay public.
        doc = "The drug product stability program was inadequate."
        self.assertEqual(self._classify("ANDA Sponsor", len(doc), doc, "X"), "ok")


if __name__ == "__main__":
    unittest.main()
