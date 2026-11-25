"""Check client-role and PUBLIC EXECUTE revocations in checked-in SQL."""

import re
import unittest
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROLES = {"anon", "authenticated"}

# Approved client contracts from the migrations' RPC definitions and grants.
# Full signatures keep approval from carrying over to new overloads.
PUBLIC_RPCS = {
    # Findings search, profiles, trends, checklist and related items (007–157).
    "public.fda_inspection_firm(text)",
    "public.fda_inspection_stats()",
    "public.findings_card_history(jsonb, text[])",
    "public.findings_category_matrix()",
    "public.findings_cfr_ranking(integer)",
    "public.findings_checklist(text[], integer)",
    "public.findings_clause_excerpt(text, text, integer, integer)",
    "public.findings_document(text)",
    "public.findings_firm_counts(text[])",
    "public.findings_firm_profile(text)",
    "public.findings_firm_search(text, integer)",
    "public.findings_firm_stats(text)",
    "public.findings_inspector_index()",
    "public.findings_inspector_pairs()",
    "public.findings_inspector_profile(text)",
    "public.findings_recent_window(integer)",
    "public.findings_search(text, text, text, text, text, text, text, text, integer, integer, text, text, boolean)",
    "public.findings_similar(text, integer)",
    "public.findings_similar_by_id(text, integer)",
    "public.findings_similar_edges(text[])",
    "public.findings_similar_to(text, integer)",
    "public.findings_stats()",
    "public.findings_zone_category()",
    # 009 defines the client-readable translation bridge.
    "public.findings_translation_queue(integer)",
    "public.findings_translation_rows(text[])",
    # 013 and 055 grant the normalizers used by invoker RPCs.
    "public.grm_normalize_country(text)",
    "public.grm_normalize_firm_name(text)",
    # 055 exposes an RLS-filtered country diagnostic; 085, 086 and 092 expose cache status.
    "public.findings_country_unmapped()",
    "public.findings_search_cache_status()",
    "public.findings_similar_cache_status()",
    "public.rpc_snapshot_status()",
    # 086 preserves the invoker wrapper's access to these search helpers.
    "public.findings_search_cache_args(text, text, text, text, text, text, text, text, integer, integer, text, text, boolean)",
    "public.findings_search_cache_read(jsonb)",
    "public.findings_search_compute(text, text, text, text, text, text, text, text, integer, integer, text, text, boolean)",
    # 031/061 public reaction summary and feedback intake. 103–160 visitor event writers.
    "public.arrival_bump(text, text, text)",
    "public.brief_event_bump(text, text, text, text)",
    "public.feedback_submit(text, text, text, boolean, text, text, text, boolean)",
    "public.funnel_bump(text)",
    "public.funnel_path_bump(text, text)",
    "public.funnel_touch_bump(text, text, text, text, text, text)",
    "public.funnel_zone_bump(text, text)",
    "public.reactions_weekly_top(integer)",
    "public.rum_nav_bump(text, text, text)",
    "public.rum_ui_bump(text, text, text, text, text)",
    # 094, 138, 142 and 144 define public MFDS and regulatory lookups.
    "public.mfds_gmp_cert_new_firms_public()",
    "public.mfds_gmp_cert_public()",
    "public.mfds_inspection_departments()",
    "public.mfds_inspection_document_attendees(text[])",
    "public.mfds_inspector_index()",
    "public.mfds_inspector_profile(text)",
    "public.regulatory_news(integer)",
}
AUTHENTICATED_RPCS = {
    # Supabase admin hardening grants the private RLS helper to authenticated policies.
    "private.is_admin()",
    # 067 personal watchlist results; 084–155 authenticated analytics reports.
    "public.findings_my_recent(integer)",
    "public.arrival_channel_report()",
    "public.brief_event_report()",
    "public.funnel_paths_report()",
    "public.funnel_touch_report()",
    "public.funnel_zone_report()",
    "public.newsletter_campaigns_report(integer)",
    "public.rum_anomaly_summary(date, date)",
    "public.rum_ui_report(date, date)",
    # 123/125/126 authorize ops clients and check admin status inside the RPC.
    "public.ops_decide(bigint, text, text)",
    "public.ops_home()",
    "public.ops_task_create(text, text, text, date, text)",
}

# Existing gaps from both migration histories. Full signatures and migration names
# keep this baseline from masking a new overload, grant or drop-and-recreate.
KNOWN_MISSING_REVOKES = {
    ("public.grm_classify_483_scope(text, integer, text, text)", "020_findings_scope_allowlist.sql"),
    ("public.grm_classify_wl_scope(integer, text, text)", "033_findings_wl_scope.sql"),
    ("public.grm_findings_scope_status_trigger()", "010_findings_scope_purity.sql"),
    ("public.grm_html_unescape(text)", "029_findings_html_entity_repair.sql"),
    ("public.grm_kr_firm_core(text)", "142_mfds_gmp_cert.sql"),
    ("public.grm_zone_of(text)", "077_growth_daily_report.sql"),
    ("public.user_feedback_set_resolved_at()", "100_feedback_retention.sql"),
    ("public.findings_inspector_key(text)", "037_findings_inspector_profile.sql"),
}
KNOWN_VIOLATIONS = {
    (signature, role, filename, reason)
    for signature, filename in KNOWN_MISSING_REVOKES
    for role in CLIENT_ROLES
    for reason in ("missing explicit REVOKE", "default EXECUTE for PUBLIC")
} | {
    # 092 renamed the old implementation and retained its 079 client grants.
    ("public.findings_similar_to_compute(text, integer)", role, "079_findings_similar_perf.sql", "granted EXECUTE")
    for role in CLIENT_ROLES
}

IDENTIFIER = r'(?:[a-z_][\w$]*|"(?:[^"]|"")+")'
# This recognizes the declaration and ACL syntax used in the migration histories.
SIGNATURE = (
    rf"(?P<name>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})?)\s*"
    r"\((?P<args>(?:[^()]|\((?:[^()]|\([^()]*\))*\))*)\)"
)
SIGNATURE_RE = re.compile(SIGNATURE, re.I)
CREATE = re.compile(rf"^create(?:\s+or\s+replace)?\s+function\s+{SIGNATURE}", re.I)
ALTER = re.compile(rf"^alter\s+function\s+{SIGNATURE}\s+(?P<action>.+)$", re.I | re.S)
DROP = re.compile(r"^drop\s+function\s+(?:if\s+exists\s+)?(.+)$", re.I | re.S)
COMMENT = re.compile(rf"^comment\s+on\s+function\s+{SIGNATURE}\s+is\s+(?P<comment>.+)$", re.I | re.S)
ACL = re.compile(
    r"^(grant|revoke)\s+(?:execute|all(?:\s+privileges)?)\s+on\s+"
    r"(?:(?:function|routine)\s+|(?P<bulk>all\s+(?:functions|routines)\s+in\s+schema\s+))"
    r"(.+?)\s+(?:to|from)\s+(.+)$",
    re.I | re.S,
)
SQL_TOKEN = re.compile(
    r"(?P<comment>--[^\n]*|/\*.*?\*/)|(?P<body>(?P<tag>\$(?:[a-z_]\w*)?\$).*?(?P=tag))"
    r"|(?P<string>(?:[eE]'(?:\\.|''|[^'\\])*'|'(?:''|[^'])*'))"
    r'|(?P<identifier>"(?:""|[^"])*")|(?P<end>;)',
    re.I | re.S,
)
INTERNAL_NAME = re.compile(
    r"^(?:_|(?:private|internal)_|grm_|store_)|"
    r"(?:^|_)(?:internal|helper|trigger|compute|refresh|purge|scrub|backfill)(?:_|$)|"
    r"(?:inspector_key|cache_args)$|^mfds_(?:inspection_documents|inspector_cohort)$", re.I,
)
INTERNAL_COMMENT = re.compile(
    r"\binternal(?:[- ]only| (?:function|helper|rpc))\b|"
    r"\b(?:service[_ -]role|server)[- ]only\b|내부용|내부\s+전용|서비스\s+전용|service_role\s*전용",
    re.I,
)
TYPE_ALIASES = {
    "int": "integer", "int4": "integer", "int8": "bigint", "int2": "smallint",
    "bool": "boolean", "varchar": "character varying", "timestamptz": "timestamp with time zone",
}
MULTIWORD_TYPES = (
    "character varying", "double precision", "bit varying", "timestamp with time zone",
    "timestamp without time zone", "time with time zone", "time without time zone",
)


def _statements(sql):
    """Split at SQL semicolons outside comments, strings and dollar-quoted bodies."""
    parts = []
    last = 0
    leading_comment = ""
    for token in SQL_TOKEN.finditer(sql):
        before = sql[last:token.start()]
        parts.append(before)
        last = token.end()
        if token.lastgroup == "end":
            yield "".join(parts).strip(), leading_comment
            parts.clear()
            leading_comment = ""
        elif token.lastgroup == "comment":
            if not any(part.strip() for part in parts):
                leading_comment = token[0]
            parts.append(" ")
        else:
            parts.append(token[0])
    if tail := ("".join(parts) + sql[last:]).strip():
        yield tail, leading_comment


def _identifier(value):
    return value[1:-1].replace('""', '"') if value.startswith('"') else value.lower()


def _signature(match, *, declaration=False):
    name = [_identifier(part) for part in re.findall(IDENTIFIER, match["name"], re.I)]
    if len(name) == 1:
        name.insert(0, "public")
    args = []
    # Mask defaults containing commas or type modifiers before splitting arguments.
    for raw_arg in re.split(r",(?![^()]*\))", match["args"]):
        arg = re.split(r"\s+default\b|=", raw_arg, maxsplit=1, flags=re.I)[0].strip().lower()
        if not arg or arg.startswith("out "):
            continue
        arg = re.sub(r"^(inout|variadic|in)\s+", "", arg)
        arg = re.sub(r"\s*\([^()]*\)", "", arg)
        arg = " ".join(arg.split())
        if declaration and " " in arg and not any(arg.startswith(kind) for kind in MULTIWORD_TYPES):
            arg = arg.split(" ", 1)[1]
        arg = re.sub(r"\s*\[\s*\]", "[]", arg.removeprefix("pg_catalog."))
        base = arg.rstrip("[]")
        args.append(TYPE_ALIASES.get(base, base) + arg[len(base):])
    return f"{'.'.join(name)}({', '.join(args)})"


@dataclass
class Function:
    created_in: str
    definer: bool = False
    internal: bool = False
    # Missing entries retain the creation defaults. None records an explicit revoke.
    # ALTER and REPLACE retain ACLs.
    acl: dict = field(default_factory=dict)


def _internal_name(signature):
    schema, name = signature.split("(", 1)[0].split(".", 1)
    return schema in {"private", "internal"} or bool(INTERNAL_NAME.search(name))


def _functions(migrations):
    functions = {}
    for filename, sql in migrations:
        for statement, leading_comment in _statements(sql):
            if statement.lower().startswith("create"):
                declaration = SQL_TOKEN.sub(
                    lambda token: token[0] if token.lastgroup == "identifier" else " ", statement,
                )
                match = CREATE.match(declaration)
                if not match:
                    continue
                signature = _signature(match, declaration=True)
                function = functions.setdefault(signature, Function(filename))
                options = declaration[match.end():]
                function.definer = bool(re.search(r"\bsecurity\s+definer\b", options, re.I))
                function.internal |= bool(
                    re.search(r"\breturns\s+(?:event_)?trigger\b", options, re.I)
                    or INTERNAL_COMMENT.search(leading_comment)
                )
            elif match := DROP.match(statement):
                for target in SIGNATURE_RE.finditer(match[1]):
                    functions.pop(_signature(target), None)
            elif match := ALTER.match(statement):
                signature = _signature(match)
                if signature in functions:
                    function = functions[signature]
                    action = match["action"]
                    if rename := re.fullmatch(rf"rename\s+to\s+({IDENTIFIER})", action, re.I):
                        schema = signature.split(".", 1)[0]
                        arguments = signature[signature.index("("):]
                        functions[f"{schema}.{_identifier(rename[1])}{arguments}"] = functions.pop(signature)
                    elif move := re.fullmatch(rf"set\s+schema\s+({IDENTIFIER})", action, re.I):
                        name_and_arguments = signature.split(".", 1)[1]
                        functions[f"{_identifier(move[1])}.{name_and_arguments}"] = functions.pop(signature)
                    elif security := re.match(r"(?:external\s+)?security\s+(definer|invoker)\b", action, re.I):
                        function.definer = security[1].lower() == "definer"
            elif match := COMMENT.match(statement):
                if _signature(match) in functions and INTERNAL_COMMENT.search(match["comment"]):
                    functions[_signature(match)].internal = True
            elif match := ACL.match(statement):
                operation, _, targets, grantees = match.groups()
                grantees = re.split(
                    r"\s+(?:with\s+grant\s+option|granted\s+by|cascade|restrict)\b",
                    grantees, maxsplit=1, flags=re.I,
                )[0]
                roles = {_identifier(role.strip()) for role in grantees.split(",")}
                if match["bulk"]:
                    schemas = {_identifier(schema.strip()) for schema in targets.split(",")}
                    signatures = [key for key in functions if key.split(".", 1)[0] in schemas]
                else:
                    signatures = [_signature(target) for target in SIGNATURE_RE.finditer(targets)]
                for signature in signatures:
                    if function := functions.get(signature):
                        granted = operation.lower() == "grant"
                        if granted and roles == {"service_role"} and not match["bulk"]:
                            function.internal = True
                        for role in roles & (CLIENT_ROLES | {"public"}):
                            function.acl[role] = filename if granted else None
    return functions


def _violations(migrations):
    violations = set()
    for signature, function in _functions(migrations).items():
        if not (function.definer or function.internal or _internal_name(signature)):
            continue
        if signature in PUBLIC_RPCS:
            continue
        roles = {"anon"} if signature in AUTHENTICATED_RPCS else CLIENT_ROLES
        for role in roles:
            if (source := function.acl.get(role, function.created_in)) is not None:
                reason = "granted EXECUTE" if role in function.acl else "missing explicit REVOKE"
                violations.add((signature, role, source, reason))
            if (public_grant := function.acl.get("public", function.created_in)) is not None:
                reason = "granted EXECUTE to PUBLIC" if "public" in function.acl else "default EXECUTE for PUBLIC"
                violations.add((signature, role, public_grant, reason))
    return violations


class MigrationFunctionGrantsTest(unittest.TestCase):
    def test_public_execute_requires_its_own_revoke(self):
        signature = "public.internal_example()"
        migrations = [
            ("001.sql", """
                CREATE FUNCTION public.internal_example() RETURNS integer
                    LANGUAGE sql AS $$ SELECT 1 $$;
                REVOKE EXECUTE ON FUNCTION public.internal_example() FROM anon, authenticated;
            """),
        ]
        self.assertEqual(
            _violations(migrations),
            {(signature, role, "001.sql", "default EXECUTE for PUBLIC") for role in CLIENT_ROLES},
        )

        migrations.append(("002.sql", "REVOKE EXECUTE ON FUNCTION public.internal_example() FROM PUBLIC;"))
        self.assertEqual(_violations(migrations), set())

        migrations.append(("003.sql", "GRANT EXECUTE ON FUNCTION public.internal_example() TO PUBLIC;"))
        self.assertEqual(
            _violations(migrations),
            {(signature, role, "003.sql", "granted EXECUTE to PUBLIC") for role in CLIENT_ROLES},
        )

    def test_migration_functions_revoke_client_execute(self):
        supabase = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        web = sorted(
            (ROOT / "web/migrations").glob("[0-9]*.sql"),
            key=lambda path: (int(re.match(r"\d+", path.name)[0]), path.name),
        )
        self.assertTrue(supabase and web, "Expected SQL migrations in both directories")
        violations = _violations((path.name, path.read_text(encoding="utf-8-sig")) for path in supabase + web)
        new_violations = violations - KNOWN_VIOLATIONS
        self.assertFalse(
            new_violations,
            "New client EXECUTE violations:\n" + "\n".join(
                f"{filename}: {signature} / {role}: {reason}"
                for signature, role, filename, reason in sorted(new_violations)
            ),
        )
