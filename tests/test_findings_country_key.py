#!/usr/bin/env python3
"""FIND-1 국가 정규화(country_key) tests (055_findings_country_key.sql +
057_grm_normalize_country_ddapi.sql + grm_findings.normalize_country).

Offline source-text / pure-function checks only -- no network, no real
Postgres/sqlite connection. Mirrors the style of test_findings_firm_key.py
(013): the SQL migration is checked as a text contract (mapping shape, safety
contract, security/search_path convention, supersede-of-038 shape), while
grm_findings.normalize_country is checked as an ordinary Python function
against the full mapping master list (all variants named in the task's
"매핑 정본"). The "parity" between the two implementations is pinned by
parsing the SQL function's `when '<variant>' then '<CODE>'` pairs out of the
migration file's source text and comparing that set, byte-for-byte (after
lowercasing, matching how the SQL folds case via `lower(...)` before the
CASE), against grm_findings._COUNTRY_CODE_MAP. A live Postgres dry-run
(control tower) is the only way to prove byte-identical runtime output; that
is out of scope here.

★2026-08-12: 057 supersedes 055's public.grm_normalize_country body via a
fresh `create or replace` (same signature, 27 more `when` pairs -- FDA Data
Dashboard API CountryName coverage for the new fda_inspections table, 058).
055's own copy of the function is left untouched as a historical snapshot
(the repo's supersede convention -- see 010/013/029 headers), so it is no
longer the *live* definition. `SqlPythonParityTest` below therefore parses
057 (the current source of truth), not 055, for the parity check against
grm_findings._COUNTRY_CODE_MAP. `Ddapi057ExtensionTest` separately pins the
055→057 relationship (27 additive pairs, no removals, no other object
touched) and the Python dict's DDAPI-block shape.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import grm_findings


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_COUNTRY_KEY_MIGRATION_PATH = _MIGRATIONS_DIR / "055_findings_country_key.sql"
_COUNTRY_KEY_EXT_MIGRATION_PATH = _MIGRATIONS_DIR / "057_grm_normalize_country_ddapi.sql"
_ZONE_CATEGORY_MIGRATION_PATH = _MIGRATIONS_DIR / "038_findings_zone_category.sql"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


# ----------------------------------------------------------------------------
# Part 1: Python normalize_country() -- full mapping-master-list fixtures,
# exactly as enumerated in the task's "매핑 정본" (original casing preserved
# per variant, mirroring how the data actually looks in site_country).
# ----------------------------------------------------------------------------

# (raw site_country value, expected country_key)
_FIXTURES: tuple[tuple[str, str], ...] = (
    # US
    ("United States", "US"),
    ("USA", "US"),
    ("미국", "US"),
    ("United States of America (USA)", "US"),
    # PR -- US 령, 별도 코드
    ("푸에르토리코", "PR"),
    # KR
    ("대한민국", "KR"),
    ("Republic of Korea", "KR"),
    ("South Korea", "KR"),
    ("The Republic of Korea", "KR"),
    ("Korea", "KR"),
    # IN
    ("India", "IN"),
    ("인도", "IN"),
    ("INDIA", "IN"),
    # CN
    ("China", "CN"),
    ("중국", "CN"),
    ("CHINA", "CN"),
    # JP
    ("Japan", "JP"),
    ("일본", "JP"),
    # DE
    ("Germany", "DE"),
    ("독일", "DE"),
    # CA
    ("Canada", "CA"),
    ("캐나다", "CA"),
    # FR
    ("France", "FR"),
    ("프랑스", "FR"),
    # GB
    ("United Kingdom", "GB"),
    ("영국", "GB"),
    ("UNITED KINGDOM", "GB"),
    # IS
    ("Iceland", "IS"),
    # IT
    ("Italy", "IT"),
    ("이탈리아", "IT"),
    # MY
    ("Malaysia", "MY"),
    # ES
    ("Spain", "ES"),
    ("스페인", "ES"),
    # BE
    ("Belgium", "BE"),
    ("벨기에", "BE"),
    # HU
    ("Hungary", "HU"),
    # TW
    ("Taiwan", "TW"),
    ("대만", "TW"),
    ("TAIWAN", "TW"),
    # CH
    ("Switzerland", "CH"),
    # CY
    ("Cyprus", "CY"),
    ("사이프러스", "CY"),
    # AU
    ("Australia", "AU"),
    ("호주", "AU"),
    # IE
    ("Ireland", "IE"),
    ("아일랜드", "IE"),
    # SE
    ("Sweden", "SE"),
    ("스웨덴", "SE"),
    # JO
    ("Jordan", "JO"),
    # GR
    ("Greece", "GR"),
    ("그리스", "GR"),
    # DK
    ("Denmark", "DK"),
    ("덴마크", "DK"),
    # NL
    ("Netherlands", "NL"),
    ("네덜란드", "NL"),
    # MX
    ("Mexico", "MX"),
    ("멕시코", "MX"),
    # CZ
    ("Czechia", "CZ"),
    # LT
    ("Lithuania", "LT"),
    # PL
    ("Poland", "PL"),
    # CL
    ("Chile", "CL"),
    # AT
    ("Austria", "AT"),
    ("오스트리아", "AT"),
    # RO
    ("Romania", "RO"),
    ("루마니아", "RO"),
    # ZA
    ("South Africa", "ZA"),
    ("남아프리카 공화국", "ZA"),
    # BD
    ("Bangladesh", "BD"),
    # ID
    ("Indonesia", "ID"),
    ("인도네시아", "ID"),
    # LB
    ("Lebanon", "LB"),
    # PT
    ("Portugal", "PT"),
    ("포르투갈", "PT"),
    # SK
    ("Slovakia", "SK"),
    # LK
    ("Sri Lanka", "LK"),
    # TR
    ("Turkey", "TR"),
    ("Türkiye", "TR"),
    # NO
    ("노르웨이", "NO"),
    # FI
    ("핀란드", "FI"),
    # VN
    ("Vietnam", "VN"),
    ("베트남", "VN"),
    # BY
    ("벨라루스", "BY"),
    # SI
    ("슬로베니아", "SI"),
    # IL
    ("이스라엘", "IL"),
)

# 매핑 정본이 다루는 고유 ISO 코드 수(47) -- 매핑이 조용히 줄어들면(중복 삭제/오타) 여기서
# 잡힌다.
_EXPECTED_CODE_COUNT = 47


class NormalizeCountryParityTest(unittest.TestCase):
    def test_fixture_count_matches_mapping_master_list(self) -> None:
        # 매핑 정본 전량(84개 원문 변종 -- 47개 코드, 코드마다 1~5개 변종)이 픽스처에
        # 빠짐없이 있는지. 개수 자체가 계약이다 -- 늘거나 줄면 매핑 정본과 픽스처가
        # 어긋난 것이다.
        self.assertEqual(len(_FIXTURES), 84)

    def test_fixtures_are_frozen(self) -> None:
        for raw, expected in _FIXTURES:
            with self.subTest(raw=raw):
                self.assertEqual(grm_findings.normalize_country(raw), expected)

    def test_distinct_codes_in_fixtures(self) -> None:
        codes = {code for _, code in _FIXTURES}
        self.assertEqual(len(codes), _EXPECTED_CODE_COUNT)

    def test_none_input_returns_empty_string(self) -> None:
        self.assertEqual(grm_findings.normalize_country(None), "")

    def test_unmapped_and_blank_inputs_return_empty_string(self) -> None:
        for raw in ("Neverland", "", "   ", None, "Atlantis", "XX"):
            with self.subTest(raw=raw):
                self.assertEqual(grm_findings.normalize_country(raw), "")

    def test_case_insensitive(self) -> None:
        self.assertEqual(grm_findings.normalize_country("united states"), "US")
        self.assertEqual(grm_findings.normalize_country("UNITED STATES"), "US")
        self.assertEqual(grm_findings.normalize_country("UsA"), "US")
        self.assertEqual(grm_findings.normalize_country("india"), "IN")

    def test_leading_trailing_whitespace_stripped(self) -> None:
        self.assertEqual(grm_findings.normalize_country("  USA  "), "US")
        self.assertEqual(grm_findings.normalize_country("\tGermany\n"), "DE")

    def test_internal_whitespace_collapsed(self) -> None:
        self.assertEqual(grm_findings.normalize_country("United   States"), "US")
        self.assertEqual(grm_findings.normalize_country("South   Africa"), "ZA")

    def test_idempotent_on_iso_code_itself(self) -> None:
        # 이미 정규화된 ISO 코드 자체를 다시 넣으면 매핑에 없으므로 '' -- 코드는
        # site_country 원문 형태가 아니므로 idempotent 함수가 아니다(firm_key 와 다른
        # 점 -- firm_key 는 소문자 정규화형 자체가 재입력 가능한 형태지만, country_key
        # 는 ISO 코드라는 별도 표현이라 원문 재투입 계약이 없다). 이 테스트는 그 경계를
        # 명시적으로 고정한다.
        self.assertEqual(grm_findings.normalize_country("US"), "")
        self.assertEqual(grm_findings.normalize_country("KR"), "")


# ----------------------------------------------------------------------------
# Part 2: 057_grm_normalize_country_ddapi.sql (the *live* grm_normalize_country
# body -- 055 superseded, see module docstring) <-> grm_findings._COUNTRY_CODE_MAP
# parity.
# ----------------------------------------------------------------------------


class SqlPythonParityTest(unittest.TestCase):
    """The SQL CASE/WHEN mapping and the Python _COUNTRY_CODE_MAP dict must
    encode the exact same (lowercased variant -> ISO2 code) pairs.

    Parses 057 (not 055) because 057's `create or replace` is what actually
    runs in production now -- 055's copy of the function body is a frozen
    historical snapshot (see module docstring / 010-style supersede
    convention), no longer the live definition.
    """

    def setUp(self) -> None:
        self.assertTrue(
            _COUNTRY_KEY_EXT_MIGRATION_PATH.is_file(),
            f"missing {_COUNTRY_KEY_EXT_MIGRATION_PATH}",
        )
        self.sql = _COUNTRY_KEY_EXT_MIGRATION_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"create or replace function public\.grm_normalize_country\(p_raw text\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate grm_normalize_country body")
        self.body = match.group(1)
        # `when '<variant>' then '<CODE>'` pairs -- the variant literal is already the
        # lowercased comparison value (the CASE subject is wrapped in lower(...)), so
        # this directly matches the key space of _COUNTRY_CODE_MAP.
        self.sql_pairs = re.findall(
            r"when\s+'((?:[^'])*)'\s+then\s+'([A-Z]{2})'", self.body
        )

    def test_sql_has_expected_pair_count(self) -> None:
        # 055 시절 80개 고유 when 절(47 코드) + 057 이 추가한 27개 DDAPI 변종
        # (6개는 기존 코드 재사용, 21개는 신규 코드) = 107개 고유 when 절, 68개
        # 고유 코드. SqlPythonParityTest.test_sql_and_python_map_are_identical
        # 이 이 107개가 파이썬 사전과 정확히 일치하는지 별도로 고정한다.
        self.assertEqual(len(self.sql_pairs), 107)

    def test_sql_and_python_map_are_identical(self) -> None:
        sql_map = dict(self.sql_pairs)
        self.assertEqual(len(sql_map), len(self.sql_pairs), "duplicate SQL when-keys")
        self.assertEqual(sql_map, grm_findings._COUNTRY_CODE_MAP)

    def test_sql_distinct_codes_match_python(self) -> None:
        sql_codes = {code for _, code in self.sql_pairs}
        py_codes = set(grm_findings._COUNTRY_CODE_MAP.values())
        self.assertEqual(sql_codes, py_codes)
        self.assertEqual(len(sql_codes), 68)

    def test_else_branch_returns_empty_string(self) -> None:
        self.assertIn("else ''", self.body)

    def test_055_pairs_are_a_strict_subset_of_057_pairs(self) -> None:
        # Regression guard: 057 must not have dropped or altered any of 055's
        # original 80 pairs while appending the 27 new ones (a pure additive
        # `create or replace`, never a narrowing one).
        old_sql = _COUNTRY_KEY_MIGRATION_PATH.read_text(encoding="utf-8")
        old_match = re.search(
            r"create or replace function public\.grm_normalize_country\(p_raw text\)"
            r".*?\$\$(.*?)\$\$;",
            old_sql,
            re.DOTALL,
        )
        self.assertIsNotNone(old_match)
        old_pairs = dict(
            re.findall(r"when\s+'((?:[^'])*)'\s+then\s+'([A-Z]{2})'", old_match.group(1))
        )
        self.assertEqual(len(old_pairs), 80)
        new_pairs = dict(self.sql_pairs)
        for variant, code in old_pairs.items():
            with self.subTest(variant=variant):
                self.assertEqual(new_pairs.get(variant), code)

    def test_case_subject_lowercases_trims_and_collapses_whitespace(self) -> None:
        self.assertIn("lower(regexp_replace(trim(coalesce(p_raw, '')), '\\s+', ' ', 'g'))", self.body)


# ----------------------------------------------------------------------------
# Part 2b: 057_grm_normalize_country_ddapi.sql -- the 27-variant DDAPI
# extension itself (offline text-contract checks + the Python function
# against the 27 new fixtures + an offline snapshot of the full 62-country
# DDAPI coverage that motivated this file).
# ----------------------------------------------------------------------------

# The 27 raw CountryName variants named in the task brief (== the migration's
# (A) DDAPI block), each with its expected ISO2 code. 6 reuse an existing code
# (KR/CZ/FI/IL/SI/NO); 21 are brand new.
_DDAPI_NEW_FIXTURES: tuple[tuple[str, str], ...] = (
    ("Korea (the Republic of)", "KR"),
    ("Singapore", "SG"),
    ("Czech Republic", "CZ"),
    ("Finland", "FI"),
    ("Israel", "IL"),
    ("Brazil", "BR"),
    ("Slovenia", "SI"),
    ("Thailand", "TH"),
    ("Malta", "MT"),
    ("Argentina", "AR"),
    ("Croatia", "HR"),
    ("Hong Kong SAR", "HK"),
    ("Norway", "NO"),
    ("Colombia", "CO"),
    ("New Zealand", "NZ"),
    ("Bulgaria", "BG"),
    ("Dominican Republic (the)", "DO"),
    ("Latvia", "LV"),
    ("Oman", "OM"),
    ("Costa Rica", "CR"),
    ("Egypt", "EG"),
    ("Macao", "MO"),
    ("Philippines", "PH"),
    ("Uruguay", "UY"),
    ("Aruba", "AW"),
    ("Estonia", "EE"),
    ("United Arab Emirates", "AE"),
)

# Offline snapshot (2026-08-11, control-tower fixture capture -- scratchpad
# ddapi_gmp.json, POST inspections_classifications, ProductType=Drugs,
# ProjectArea=='Drug Quality Assurance' filtered client-side, 6,417 rows) of
# the full distinct CountryName set (62 names). 35 of these already matched
# 055's original 80 pairs verbatim (no new mapping needed); the other 27 are
# exactly _DDAPI_NEW_FIXTURES above. This constant pins that real-world
# coverage claim without a live API call in CI.
_DDAPI_ALL_62_COUNTRY_NAMES: frozenset[str] = frozenset(
    {
        "United States", "India", "China", "Germany", "Italy", "France",
        "Canada", "Japan", "United Kingdom", "Spain", "Switzerland",
        "Korea (the Republic of)", "Ireland", "Belgium", "Netherlands",
        "Mexico", "Taiwan", "Denmark", "Sweden", "Australia", "Singapore",
        "Czech Republic", "Austria", "Finland", "Israel", "Portugal",
        "Brazil", "Greece", "Poland", "Turkey", "Slovenia", "Thailand",
        "Malaysia", "Hungary", "Malta", "Argentina", "South Africa",
        "Romania", "Croatia", "Norway", "Hong Kong SAR", "Colombia",
        "New Zealand", "Bangladesh", "Vietnam", "Lithuania", "Oman",
        "Dominican Republic (the)", "Latvia", "Bulgaria", "Egypt",
        "Costa Rica", "Uruguay", "Philippines", "Macao", "Cyprus",
        "United Arab Emirates", "Jordan", "Aruba", "Slovakia", "Estonia",
        "Iceland",
    }
)


class Ddapi057ExtensionTest(unittest.TestCase):
    """057's 27-variant addition -- Python function behavior + the real-world
    62-country coverage claim it exists to satisfy."""

    def test_fixture_count_is_27(self) -> None:
        self.assertEqual(len(_DDAPI_NEW_FIXTURES), 27)

    def test_new_fixtures_map_correctly(self) -> None:
        for raw, expected in _DDAPI_NEW_FIXTURES:
            with self.subTest(raw=raw):
                self.assertEqual(grm_findings.normalize_country(raw), expected)

    def test_six_reused_codes_match_existing_055_variants(self) -> None:
        # These 6 DDAPI names must resolve to the *same* code as an existing
        # 055-era variant of the same country (proves "reuse", not a
        # collision with an unrelated code).
        reused = {
            "Korea (the Republic of)": grm_findings.normalize_country("Korea"),
            "Czech Republic": grm_findings.normalize_country("Czechia"),
            "Finland": grm_findings.normalize_country("핀란드"),
            "Israel": grm_findings.normalize_country("이스라엘"),
            "Slovenia": grm_findings.normalize_country("슬로베니아"),
            "Norway": grm_findings.normalize_country("노르웨이"),
        }
        for name, expected_code in reused.items():
            with self.subTest(name=name):
                self.assertEqual(grm_findings.normalize_country(name), expected_code)

    def test_offline_country_name_snapshot_has_62_entries(self) -> None:
        self.assertEqual(len(_DDAPI_ALL_62_COUNTRY_NAMES), 62)

    def test_all_62_ddapi_country_names_map(self) -> None:
        # The claim this whole migration exists to satisfy: 0 unmapped names
        # left after the 27-variant extension.
        unmapped = [
            name
            for name in _DDAPI_ALL_62_COUNTRY_NAMES
            if grm_findings.normalize_country(name) == ""
        ]
        self.assertEqual(unmapped, [])

    def test_62_names_split_is_35_preexisting_plus_27_new(self) -> None:
        new_names = {raw for raw, _ in _DDAPI_NEW_FIXTURES}
        preexisting = _DDAPI_ALL_62_COUNTRY_NAMES - new_names
        self.assertEqual(len(preexisting), 35)
        self.assertEqual(new_names, _DDAPI_ALL_62_COUNTRY_NAMES - preexisting)


class Ddapi057MigrationFileTest(unittest.TestCase):
    """057_grm_normalize_country_ddapi.sql -- offline text-contract checks."""

    def setUp(self) -> None:
        self.assertTrue(
            _COUNTRY_KEY_EXT_MIGRATION_PATH.is_file(),
            f"missing {_COUNTRY_KEY_EXT_MIGRATION_PATH}",
        )
        self.sql = _COUNTRY_KEY_EXT_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _COUNTRY_KEY_EXT_MIGRATION_PATH.read_bytes())

    def test_signature_unchanged_from_055(self) -> None:
        # Same (schema, name, arg types) as 055 -- proves this is a body-only
        # `create or replace`, not a new overload (the exact failure mode the
        # MEMORY warns about: "create or replace 로 파라미터를 추가하면 오버로드").
        self.assertIn(
            "create or replace function public.grm_normalize_country(p_raw text)\n"
            "returns text\nlanguage sql\nimmutable\nset search_path = public",
            self.sql,
        )

    def test_documents_pg_depend_oid_verification(self) -> None:
        # This migration's header must show its work: it claims to have
        # directly verified (not assumed) that the generated column survives
        # a body-only replace, via pg_depend / OID inspection on the live DB.
        #
        # ★2026-08-12: this test used to pin "17792" as "the actual OID
        # observed live" -- but 17792 is the OID of the *findings table*
        # (pg_class), not of the function (pg_proc, oid 30300). The header
        # misread its own pg_depend output and this test froze the wrong
        # number permanently. Verified live:
        #   select oid from pg_proc  where proname='grm_normalize_country' -> 30300
        #   select relname from pg_class where oid=17792                   -> 'findings'
        # The conclusion (same signature => OID preserved => no column
        # rebuild) is unchanged; only the cited evidence was wrong.
        self.assertIn("pg_depend", self.sql)
        self.assertIn("30300", self.sql)  # pg_proc OID, verified live 2026-08-12
        self.assertIn("deptype='n'", self.sql.replace(" ", ""))
        # And the correction itself must stay in the header so the next reader
        # does not "re-discover" 17792 from an old transcript and re-add it.
        self.assertIn("17792", self.sql)
        self.assertIn("pg_class", self.sql)

    def test_documents_zero_row_backfill_measurement(self) -> None:
        # The header must state the measured (not assumed) backfill impact.
        self.assertIn("0건", self.sql)
        self.assertIn("26,499", self.sql)

    def test_no_regrant_statements(self) -> None:
        # Signature unchanged -> grants from 055 carry over; this file must
        # not re-declare revoke/grant (and must say so, so a reviewer doesn't
        # mistake the omission for a gap).
        self.assertNotIn("revoke all on function public.grm_normalize_country", self.sql)
        self.assertNotIn("grant execute on function public.grm_normalize_country", self.sql)
        self.assertIn("재부여 불필요", self.sql)

    def test_backfill_touch_update_present_and_scoped(self) -> None:
        self.assertIn(
            "update public.findings\n"
            "   set site_country = site_country\n"
            " where site_country <> ''\n"
            "   and country_key = '';",
            self.sql,
        )

    def test_does_not_touch_other_055_objects(self) -> None:
        # 057 must be a pure function-body replace + backfill -- it must not
        # redefine findings_zone_category, findings_country_unmapped, or
        # findings_search (those stay owned by 055/056).
        for fn in (
            "findings_zone_category(",
            "findings_country_unmapped(",
            "findings_search(",
        ):
            self.assertNotIn(f"create or replace function public.{fn}", self.sql)

    def test_no_plpgsql_do_blocks_or_declared_variables(self) -> None:
        self.assertNotIn("do $$", self.code.lower())
        self.assertNotIn("declare", self.code.lower())

    def test_no_array_slice_syntax(self) -> None:
        self.assertNotIn("[1:", self.code)

    def test_supersede_chain_documented(self) -> None:
        self.assertIn("grm_normalize_country = 055 → **이 파일(057)**", self.sql)


class SupersedeNoteAddedTo055Test(unittest.TestCase):
    """055's own file must carry a short pointer to 057 (010/013/029
    convention: never edit the superseded body, just add a header note)."""

    def setUp(self) -> None:
        self.sql = _COUNTRY_KEY_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_055_header_points_to_057(self) -> None:
        self.assertIn("057_grm_normalize_country_ddapi.sql", self.sql)
        self.assertIn("supersede", self.sql.lower())

    def test_055_original_80_pair_function_body_still_present_verbatim(self) -> None:
        # 055's own copy of the (now-superseded) function body must be
        # untouched -- git-history / rollback record, per repo convention.
        match = re.search(
            r"create or replace function public\.grm_normalize_country\(p_raw text\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        pairs = re.findall(r"when\s+'((?:[^'])*)'\s+then\s+'([A-Z]{2})'", match.group(1))
        self.assertEqual(len(pairs), 80)


# ----------------------------------------------------------------------------
# Part 3: 055_findings_country_key.sql -- offline text-contract checks (013/038/
# 054 style: signature, generated column, RPC shape, grants, supersede claims).
# ----------------------------------------------------------------------------


class MigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _COUNTRY_KEY_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _COUNTRY_KEY_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_measured_16986_rows_and_27_4_pct_blank(self) -> None:
        self.assertIn("16,986", self.sql)
        self.assertIn("27.4%", self.sql)

    def test_documents_004_009_pitfalls_not_applicable(self) -> None:
        self.assertIn("004/009 함정 해당 없음", self.sql)

    def test_supersede_chain_names_038(self) -> None:
        self.assertIn("038 → **이 파일(055)**", self.sql)

    def test_no_plpgsql_do_blocks_or_declared_variables(self) -> None:
        self.assertNotIn("do $$", self.code.lower())
        self.assertNotIn("declare", self.code.lower())

    def test_no_array_slice_syntax(self) -> None:
        self.assertNotIn("[1:", self.code)


class NormalizeCountryFunctionShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _COUNTRY_KEY_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_signature_is_immutable_sql_search_path_pinned_and_not_strict(self) -> None:
        self.assertIn(
            "create or replace function public.grm_normalize_country(p_raw text)\n"
            "returns text\nlanguage sql\nimmutable\nset search_path = public",
            self.sql,
        )
        # NULL must be accepted (no `strict` keyword on this function -- the body
        # handles NULL itself via coalesce(p_raw, '')).
        func_start = self.sql.index(
            "create or replace function public.grm_normalize_country"
        )
        func_end = self.sql.index("$$;", func_start)
        header = self.sql[func_start:func_end]
        self.assertNotIn("\nstrict\n", header)

    def test_null_input_handled_via_coalesce(self) -> None:
        self.assertIn("coalesce(p_raw, '')", self.sql)


class GeneratedColumnTest(unittest.TestCase):
    """(B) findings.country_key -- STORED GENERATED column, no trigger/backfill."""

    def setUp(self) -> None:
        self.sql = _COUNTRY_KEY_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_adds_generated_stored_column_idempotently(self) -> None:
        self.assertIn(
            "alter table public.findings\n"
            "  add column if not exists country_key text generated always as (\n"
            "    public.grm_normalize_country(site_country)\n"
            "  ) stored;",
            self.sql,
        )

    def test_no_trigger_or_backfill_statements_for_country_key(self) -> None:
        self.assertNotIn("create trigger", self.sql)
        self.assertNotIn("update public.findings", self.sql)

    def test_creates_btree_index_on_country_key(self) -> None:
        self.assertIn(
            "create index if not exists findings_country_key_idx\n"
            "  on public.findings (country_key);",
            self.sql,
        )


class ZoneCategoryRpcRewriteTest(unittest.TestCase):
    """(C) public.findings_zone_category() -- rewritten to use country_key."""

    def setUp(self) -> None:
        self.sql = _COUNTRY_KEY_MIGRATION_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"create or replace function public\.findings_zone_category\(\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate findings_zone_category body")
        self.body = match.group(1)

    def test_signature_is_security_definer_stable_search_path_pinned(self) -> None:
        self.assertIn(
            "create or replace function public.findings_zone_category()\n"
            "returns jsonb\nlanguage sql\nstable\nsecurity definer\nset search_path = public",
            self.sql,
        )

    def test_zone_case_uses_country_key_not_site_country_string_compare(self) -> None:
        self.assertIn("country_key in ('US', 'PR')", self.body)
        self.assertIn("country_key = ''", self.body)
        # The old 038 string-literal comparison must be gone from this function.
        self.assertNotIn("site_country in ('United States', 'USA')", self.body)

    def test_scope_status_ok_filter_present(self) -> None:
        self.assertIn("scope_status = 'ok'", self.body)

    def test_source_scope_is_fda_483(self) -> None:
        self.assertIn("source = 'FDA 483'", self.body)

    def test_top_countries_grouped_by_country_key(self) -> None:
        self.assertIn("group by country_key", self.body)

    def test_top_countries_keeps_legacy_country_key_and_adds_code(self) -> None:
        # Legacy key ('country') must survive (old cached JS reads it) and a new
        # 'code' key must be added -- additive only, per repo convention.
        self.assertIn("'country',", self.body)
        self.assertIn("'code',", self.body)
        self.assertIn("'findings',", self.body)
        self.assertIn("'documents',", self.body)

    def test_countries_total_counts_distinct_country_key(self) -> None:
        self.assertIn("count(distinct country_key) from known where zone = 'foreign'", self.body)

    def test_safety_contract_no_text_or_url_fields(self) -> None:
        for field in ("finding_text", "finding_text_ko", "evidence_url", "firm_name"):
            self.assertNotIn(field, self.body)

    def test_no_regrant_needed_comment_present(self) -> None:
        # This migration relies on 038's existing grant (create or replace preserves
        # it) -- the file should say so explicitly rather than silently omitting the
        # grant statements (so a reviewer doesn't mistake the omission for a gap).
        self.assertIn("재부여 불필요", self.sql)


class CountryUnmappedRpcTest(unittest.TestCase):
    """(D) public.findings_country_unmapped() -- new silent-drift observability RPC."""

    def setUp(self) -> None:
        self.sql = _COUNTRY_KEY_MIGRATION_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"create or replace function public\.findings_country_unmapped\(\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate findings_country_unmapped body")
        self.body = match.group(1)

    def test_signature_is_stable_security_invoker_search_path_pinned(self) -> None:
        self.assertIn(
            "create or replace function public.findings_country_unmapped()\n"
            "returns jsonb\nlanguage sql\nstable\nsecurity invoker\n"
            "set search_path = public, extensions",
            self.sql,
        )

    def test_filters_nonempty_site_country_with_empty_country_key(self) -> None:
        self.assertIn("site_country <> '' and country_key = ''", self.body)

    def test_returns_site_country_and_findings_count(self) -> None:
        self.assertIn("'site_country'", self.body)
        self.assertIn("'findings'", self.body)

    def test_no_explicit_scope_status_filter(self) -> None:
        # 054 convention: security invoker relies on RLS, no explicit predicate.
        self.assertNotIn("scope_status", self.body)


class GrantsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _COUNTRY_KEY_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_revoke_then_grant_for_new_functions(self) -> None:
        self.assertIn(
            "revoke all on function public.grm_normalize_country(text) from public;",
            self.sql,
        )
        self.assertIn(
            "revoke all on function public.findings_country_unmapped() from public;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.grm_normalize_country(text) to anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.findings_country_unmapped() to anon, authenticated;",
            self.sql,
        )
        revoke_idx = self.sql.index(
            "revoke all on function public.grm_normalize_country"
        )
        grant_idx = self.sql.index(
            "grant execute on function public.grm_normalize_country"
        )
        self.assertLess(revoke_idx, grant_idx)

    def test_no_existing_013_037_functions_touched(self) -> None:
        for fn in (
            "grm_normalize_firm_name(p_name text)",
            "findings_firm_profile(p_firm_key text)",
            "findings_search(",
        ):
            self.assertNotIn(
                f"create or replace function public.{fn}",
                self.sql,
                f"055 must not redefine {fn}",
            )


class SourceOfTruthExistsTest(unittest.TestCase):
    def test_prerequisite_migrations_exist(self) -> None:
        for name in (
            "002_findings.sql",
            "010_findings_scope_purity.sql",
            "038_findings_zone_category.sql",
        ):
            path = _MIGRATIONS_DIR / name
            self.assertTrue(path.is_file(), f"missing {path}")

    def test_038_still_defines_original_string_compare(self) -> None:
        # 038 itself is left untouched (055 supersedes it via a *new*
        # create-or-replace in 055, not by editing 038's file) -- confirm 038's
        # original site_country string-compare is still there so a future reader
        # can see exactly what was superseded.
        sql038 = _ZONE_CATEGORY_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("site_country in ('United States', 'USA')", sql038)


if __name__ == "__main__":
    unittest.main()
