#!/usr/bin/env python3
"""FIND-1 F4a Copilot Studio custom connector spec tests
(docs/copilot/grm_findings_connector.swagger.json).

Offline JSON-shape checks only -- no network, mirrors the style of
tests/test_findings_stats_rpc.py (source-text/parsed-shape assertions against a
static file, no live Supabase calls). This spec must stay a byte-accurate
reflection of web/migrations/007_findings_stats_rpc.sql and
008_findings_category_matrix.sql -- these tests pin the parts of that contract
that matter for safe Power Platform import: valid Swagger 2.0, correct host,
the 4 expected operations, apiKey security wired to the `apikey` header, no
real key material committed, and the finding_text (English original) exposure
path confined to the rows GET -- never to the aggregate-only RPC responses.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


_SPEC_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "copilot"
    / "grm_findings_connector.swagger.json"
)

_EXPECTED_OPERATION_IDS = (
    "findingsStats",
    "findingsFirmStats",
    "findingsCategoryMatrix",
    "findingsList",
)

# The three read-only aggregate RPCs -- per 007/008 SQL, these never return raw
# finding text, only counts and bibliographic metadata.
_RPC_RESPONSE_DEFINITIONS = (
    "FindingsStatsResponse",
    "FindingsFirmStatsResponse",
    "FindingsCategoryMatrixResponse",
)


class SpecFileParsesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_SPEC_PATH.is_file(), f"missing {_SPEC_PATH}")
        self.raw_text = _SPEC_PATH.read_text(encoding="utf-8")
        self.spec = json.loads(self.raw_text)

    def test_is_valid_json(self) -> None:
        self.assertIsInstance(self.spec, dict)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _SPEC_PATH.read_bytes())


class SwaggerVersionAndHostTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))

    def test_swagger_version_is_2_0(self) -> None:
        # Power Platform custom connector import only accepts Swagger 2.0 (OpenAPI
        # v2) documents -- OpenAPI 3.x fails or is misparsed by the import wizard.
        self.assertEqual(self.spec.get("swagger"), "2.0")
        self.assertNotIn("openapi", self.spec)

    def test_host_is_exact_supabase_project_host(self) -> None:
        self.assertEqual(self.spec.get("host"), "rfwixqqdljpmtjdlblct.supabase.co")

    def test_schemes_is_https_only(self) -> None:
        self.assertEqual(self.spec.get("schemes"), ["https"])

    def test_info_description_explains_swagger_2_choice(self) -> None:
        description = self.spec.get("info", {}).get("description", "")
        self.assertIn("Swagger 2.0", description)
        self.assertIn("OpenAPI 3", description)


class OperationsPresentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
        self.operation_ids = {
            op.get("operationId")
            for path_item in self.spec.get("paths", {}).values()
            for op in path_item.values()
            if isinstance(op, dict)
        }

    def test_all_four_expected_operations_present(self) -> None:
        for op_id in _EXPECTED_OPERATION_IDS:
            self.assertIn(op_id, self.operation_ids)

    def test_exactly_four_operations_defined(self) -> None:
        self.assertEqual(len(self.operation_ids), 4)

    def test_rpc_paths_use_post(self) -> None:
        for rpc_path in (
            "/rest/v1/rpc/findings_stats",
            "/rest/v1/rpc/findings_firm_stats",
            "/rest/v1/rpc/findings_category_matrix",
        ):
            path_item = self.spec["paths"][rpc_path]
            self.assertIn("post", path_item)

    def test_findings_rows_path_uses_get(self) -> None:
        path_item = self.spec["paths"]["/rest/v1/findings"]
        self.assertIn("get", path_item)

    def test_every_operation_has_korean_summary_and_description(self) -> None:
        # Copilot Studio uses summary/description text to decide which action to
        # invoke, so both must be present and non-trivial (contain Hangul).
        hangul_re = re.compile(r"[가-힣]")
        for path_item in self.spec.get("paths", {}).values():
            for op in path_item.values():
                if not isinstance(op, dict):
                    continue
                summary = op.get("summary", "")
                description = op.get("description", "")
                self.assertTrue(summary, "operation missing summary")
                self.assertTrue(description, "operation missing description")
                self.assertRegex(summary, hangul_re)
                self.assertRegex(description, hangul_re)


class SecurityDefinitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))

    def test_apikey_security_definition_is_header_apikey(self) -> None:
        security_defs = self.spec.get("securityDefinitions", {})
        self.assertIn("apikey", security_defs)
        apikey_def = security_defs["apikey"]
        self.assertEqual(apikey_def.get("type"), "apiKey")
        self.assertEqual(apikey_def.get("in"), "header")
        self.assertEqual(apikey_def.get("name"), "apikey")

    def test_no_authorization_security_definition(self) -> None:
        # Authorization is deliberately NOT part of the spec -- Power Platform's
        # API Key auth type only supports one header, so Authorization is added
        # post-import via a connector policy (Set HTTP Header), per the guide.
        security_defs = self.spec.get("securityDefinitions", {})
        self.assertNotIn("Authorization", security_defs)
        self.assertNotIn("authorization", security_defs)

    def test_global_security_requires_apikey(self) -> None:
        security = self.spec.get("security", [])
        self.assertIn({"apikey": []}, security)


class NoRealKeyMaterialTest(unittest.TestCase):
    """The spec must never contain an actual Supabase key -- Supabase anon/service
    keys are JWTs, which always start with the `eyJ` base64url header prefix."""

    def setUp(self) -> None:
        self.raw_text = _SPEC_PATH.read_text(encoding="utf-8")

    def test_no_jwt_looking_key_present(self) -> None:
        self.assertNotIn("eyJ", self.raw_text)

    def test_no_bearer_token_with_real_looking_value(self) -> None:
        # A placeholder like "Bearer <anon key>" is fine; a Bearer value with no
        # angle-bracket placeholder marker would indicate a real token was pasted.
        for match in re.finditer(r"Bearer\s+(\S+)", self.raw_text):
            token = match.group(1)
            self.assertTrue(
                token.startswith("<") or token in ("...", "{key}", "${key}"),
                f"Bearer value does not look like a placeholder: {token!r}",
            )


class FindingTextExposureScopeTest(unittest.TestCase):
    """finding_text (the English original) may only be reachable through the rows
    GET (/rest/v1/findings) -- never through the three aggregate-only RPC
    responses, per the 007/008 SQL safety contract (counts + bibliographic
    metadata only)."""

    def setUp(self) -> None:
        self.spec = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
        self.definitions = self.spec.get("definitions", {})

    def _contains_finding_text_key(self, node: object) -> bool:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "finding_text":
                    return True
                if self._contains_finding_text_key(value):
                    return True
        elif isinstance(node, list):
            for item in node:
                if self._contains_finding_text_key(item):
                    return True
        return False

    def test_finding_text_absent_from_rpc_response_definitions(self) -> None:
        for def_name in _RPC_RESPONSE_DEFINITIONS:
            self.assertIn(def_name, self.definitions, f"missing definition {def_name}")
            self.assertFalse(
                self._contains_finding_text_key(self.definitions[def_name]),
                f"{def_name} must never expose finding_text -- aggregate RPCs are "
                "counts/bibliographic-metadata only",
            )

    def test_finding_text_present_in_findings_row_definition(self) -> None:
        self.assertIn("FindingsRow", self.definitions)
        self.assertTrue(
            self._contains_finding_text_key(self.definitions["FindingsRow"]),
            "FindingsRow (rows GET response item) should expose finding_text as "
            "an available column",
        )

    def test_findings_list_select_example_mentions_finding_text_ko_not_finding_text(
        self,
    ) -> None:
        # The recommended select= example (in the operation description) should
        # steer callers toward the Korean translation, not the English original,
        # for citation -- matches the grounding rule in COPILOT_SETUP_GUIDE.md.
        get_op = self.spec["paths"]["/rest/v1/findings"]["get"]
        select_param = next(
            p for p in get_op["parameters"] if p["name"] == "select"
        )
        example_text = select_param.get("description", "")
        self.assertIn("finding_text_ko", example_text)


class RequestBodyShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
        self.definitions = self.spec.get("definitions", {})

    def test_firm_stats_body_requires_p_firm(self) -> None:
        firm_stats_op = self.spec["paths"]["/rest/v1/rpc/findings_firm_stats"]["post"]
        body_param = next(p for p in firm_stats_op["parameters"] if p["in"] == "body")
        ref = body_param["schema"]["$ref"].split("/")[-1]
        body_schema = self.definitions[ref]
        self.assertIn("p_firm", body_schema.get("required", []))
        self.assertEqual(body_schema["properties"]["p_firm"]["type"], "string")

    def test_stats_and_matrix_bodies_are_empty_object(self) -> None:
        for rpc_path in (
            "/rest/v1/rpc/findings_stats",
            "/rest/v1/rpc/findings_category_matrix",
        ):
            op = self.spec["paths"][rpc_path]["post"]
            body_param = next(p for p in op["parameters"] if p["in"] == "body")
            ref = body_param["schema"]["$ref"].split("/")[-1]
            body_schema = self.definitions[ref]
            self.assertEqual(body_schema.get("properties", {}), {})


if __name__ == "__main__":
    unittest.main()
