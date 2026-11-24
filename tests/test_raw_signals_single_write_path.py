"""Guard replacements of raw_signals content using repository source only.

The protected columns mirror migration 150. Initial inserts (including
ignore-duplicates imports) have no previous version to archive. The only
direct REST PATCH today is the modality backfill, which writes a derived column.
SQL fixtures under tests/ deliberately exercise writes and are outside this scan.
"""
from __future__ import annotations

import ast
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_COLUMNS = {"raw_json", "row_json", "raw_sha256", "record_json", "body", "raw_sha"}
RAW_URL = re.compile(r"/rest/v1/raw_signals(?=[?/#\s'\"]|$)")
TABLE = r'(?:(?:"public"|public|main)\s*\.\s*)?(?:"raw_signals"|raw_signals\b)'
SQL_WRITES = re.compile(
    rf"\b(?P<operation>update(?:\s+or\s+\w+)?|insert(?:\s+or\s+\w+)?\s+into|"
    rf"replace\s+into|merge\s+into)\s+(?:only\s+)?{TABLE}\s*(?P<tail>[^;]*)",
    re.I,
)
SQL_ASSIGNMENT = re.compile(
    r'(?:\bset\s+|,\s*)(?P<target>\([^()]+\)|(?:"?\w+"?\s*\.\s*)?"?\w+"?)\s*=',
    re.I,
)
SQL_CANONICAL_BODY = re.compile(
    r"\bcreate\s+or\s+replace\s+function\s+public\.raw_signal_apply_version\s*\("
    r"[^$]*\bas\s+\$\$(.*?)\$\$", re.I | re.S,
)
CANONICAL_MIGRATIONS = {
    "web/migrations/148_raw_signal_versions.sql",
    "web/migrations/150_raw_signals_write_lock.sql",
}
SQL_TRIVIA = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/", re.S)


def sql_writes(source: str, path: str) -> list[tuple[int, str]]:
    # Keep quoted identifiers and line numbers, discard comments and values.
    sql = SQL_TRIVIA.sub(lambda m: m[0] if m[0].startswith('"') else
                         re.sub(r"[^\n]", " ", m[0]), source)
    body = SQL_CANONICAL_BODY.search(sql) if path in CANONICAL_MIGRATIONS else None
    approved = SQL_WRITES.search(sql, *body.span(1)) if body else None
    violations = []
    for match in SQL_WRITES.finditer(sql):
        operation = match["operation"].lower()
        tail = match["tail"]
        if operation.startswith("insert"):
            conflict = re.search(r"\bon\s+conflict\b[\s\S]*?\bdo\s+update\s+(?=set\b)", tail, re.I)
            tail = tail[conflict.end():] if conflict else ""
        assignments = re.search(r"\bset\b", tail, re.I)
        if assignments:
            sets = re.split(r"\b(?:where|returning)\b", tail[assignments.start():], maxsplit=1, flags=re.I)[0]
            columns = {word.lower() for item in SQL_ASSIGNMENT.finditer(sets)
                       for word in re.findall(r"\w+", item["target"])}
        else:
            columns = set()
        replaces_row = "replace" in operation.split()
        if replaces_row or columns & RAW_COLUMNS:
            if approved and match.start() == approved.start():
                continue
            violations.append((sql.count("\n", 0, match.start()) + 1, operation))
    return violations


def _text(node, bindings, seen=frozenset()):
    """Resolve literal fragments and simple local aliases without importing code."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.Name) and node.id not in seen:
        return _text(bindings.get(node.id), bindings, seen | {node.id})
    if isinstance(node, ast.JoinedStr):
        return "".join(_text(part, bindings, seen) or " " for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return _text(node.value, bindings, seen)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _text(node.left, bindings, seen) + _text(node.right, bindings, seen)
    return ""


def _argument(call, index, keyword):
    return next((kw.value for kw in call.keywords if kw.arg == keyword),
                call.args[index] if len(call.args) > index else None)


def _raw_table(node, bindings):
    target = _text(node, bindings)
    return target == "raw_signals" or bool(RAW_URL.search(target))


def python_writes(source: str, path: str) -> list[tuple[int, str]]:
    violations = []

    def scan(node, bindings, prefer_assignments):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings = bindings.copy()
            prefer_assignments = prefer_assignments.copy()
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    headers = bindings.get(target.value.id)
                    key = _text(target.slice, bindings)
                    if isinstance(headers, ast.Dict) and key.lower() == "prefer" and node.value is not None:
                        prefer_assignments[headers] = {
                            **prefer_assignments.get(headers, {}), key: _text(node.value, bindings),
                        }
        if isinstance(node, ast.JoinedStr) or isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = _text(node, bindings)
            if "raw_signals" in sql.lower():
                violations.extend((node.lineno + line - 1, kind) for line, kind in sql_writes(sql, path))
            return
        if isinstance(node, ast.Call):
            func = node.func
            method = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            target = _argument(node, 0, "url")
            if method == "request":
                method = _text(_argument(node, 0, "method"), bindings).lower()
                target = _argument(node, 1, "url")
            if method in {"patch", "put"} and _raw_table(target, bindings):
                payload = _argument(node, 1, "json")
                modality_only = (
                    path == "backfill_raw_signals_modality.py" and method == "patch"
                    and isinstance(payload, ast.Dict) and len(payload.keys) == 1
                    and isinstance(payload.keys[0], ast.Constant) and payload.keys[0].value == "modality"
                )
                if not modality_only:
                    violations.append((node.lineno, method))
            if method == "post" and _raw_table(target, bindings):
                headers = _argument(node, len(node.args), "headers")
                if isinstance(headers, ast.Name):
                    headers = bindings.get(headers.id)
                if isinstance(headers, ast.Dict):
                    header_values = {_text(key, bindings): _text(value, bindings)
                                     for key, value in zip(headers.keys, headers.values)}
                    header_values.update(prefer_assignments.get(headers, {}))
                    if any(key.lower() == "prefer" and "merge-duplicates" in value.lower()
                           for key, value in header_values.items()):
                        violations.append((node.lineno, "POST merge-duplicates"))
            if method == "_post_rows" and _raw_table(_argument(node, 2, "table"), bindings):
                resolution = _argument(node, len(node.args), "resolution")
                if resolution is not None and _text(resolution, bindings) != "ignore-duplicates":
                    violations.append((node.lineno, "PostgREST upsert"))
            if method in {"update", "upsert"} and isinstance(func, ast.Attribute):
                table = func.value
                if isinstance(table, ast.Name):
                    table = bindings.get(table.id)
                if (isinstance(table, ast.Call) and isinstance(table.func, ast.Attribute)
                        and table.func.attr in {"table", "from_"}
                        and _raw_table(_argument(table, 0, "table_name"), bindings)):
                    violations.append((node.lineno, method))
        for child in ast.iter_child_nodes(node):
            scan(child, bindings, prefer_assignments)

    scan(ast.parse(source, filename=path), {}, {})
    return violations


class RawSignalsSingleWritePathTests(unittest.TestCase):
    def test_raw_signals_single_write_path(self):
        paths = subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py", "*.sql"],
            cwd=ROOT,
        ).decode("utf-8").split("\0")
        offenders = []
        for path in sorted(set(filter(None, paths))):
            file = ROOT / path
            if {"tests", "fixtures"} & set(file.relative_to(ROOT).parts[:-1]) or not file.is_file():
                continue
            source = file.read_text(encoding="utf-8-sig")
            if "raw_signals" not in source.lower():
                continue
            scan = python_writes if file.suffix == ".py" else sql_writes
            offenders.extend(f"{path}:{line} ({kind})" for line, kind in scan(source, path))
        self.assertFalse(offenders, "Use raw_versioning / raw_signal_apply_version:\n" + "\n".join(offenders))

    def test_post_rows_defaults_to_initial_insert(self):
        source = (ROOT / "findings_supabase_append.py").read_text(encoding="utf-8")
        helper = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.FunctionDef) and node.name == "_post_rows")
        defaults = dict(zip((arg.arg for arg in helper.args.kwonlyargs), helper.args.kw_defaults))
        self.assertEqual(_text(defaults["resolution"], {}), "ignore-duplicates")

    def test_detects_python_bypasses_request_patch(self):
        source = (
            'url = f"{base}/rest/v1/raw_signals?raw_signal_id=eq.{row_id}"\n'
            'session.request("PATCH", url, json={"raw_json": raw})'
        )
        self.assertTrue(python_writes(source, "new/subdir/tool.py"))

    def test_detects_python_bypasses_table_update(self):
        source = (
            'table = "raw_signals"\n'
            'client.table(table).update({"row_json": raw}).eq("raw_signal_id", row_id).execute()'
        )
        self.assertTrue(python_writes(source, "new/subdir/tool.py"))

    def test_detects_python_bypasses_post_merge_duplicates(self):
        source = (
            'headers = {"Prefer": "return=minimal,resolution=merge-duplicates"}\n'
            'requests.post(f"{base}/rest/v1/raw_signals", headers=headers, json=rows)'
        )
        self.assertTrue(python_writes(source, "new/subdir/tool.py"))

    def test_detects_post_merge_duplicates_after_header_assignment(self):
        source = (
            'headers = {}\n'
            'key = "pReFeR"\n'
            'headers[key] = "resolution=merge-duplicates"\n'
            'requests.post(f"{base}/rest/v1/raw_signals", headers=headers, json=rows)'
        )
        self.assertEqual(python_writes(source, "new.py"), [(4, "POST merge-duplicates")])

    def test_allows_post_after_prefer_header_is_overwritten(self):
        source = (
            'headers = {"Prefer": "resolution=merge-duplicates"}\n'
            'headers["Prefer"] = "resolution=ignore-duplicates"\n'
            'requests.post(f"{base}/rest/v1/raw_signals", headers=headers, json=rows)'
        )
        self.assertEqual(python_writes(source, "new.py"), [])

    def test_detects_python_bypasses_post_rows_upsert(self):
        source = (
            '_post_rows(base, key, table="raw_signals", rows=rows, resolution="merge-duplicates")'
        )
        self.assertTrue(python_writes(source, "new/subdir/tool.py"))

    def test_detects_python_bypasses_embedded_sql_update(self):
        source = (
            'conn.execute("UPDATE raw_signals SET raw_sha256 = ?")'
        )
        self.assertTrue(python_writes(source, "new/subdir/tool.py"))

    def test_detects_sql_bypasses_update(self):
        source = (
            'UPDATE "public"."raw_signals" AS r\nSET (raw_json, row_json) = (a, b);'
        )
        self.assertTrue(sql_writes(source, "new.sql"))

    def test_detects_sql_bypasses_insert_on_conflict_update(self):
        source = (
            "INSERT INTO raw_signals VALUES (1) ON CONFLICT (id) DO UPDATE SET raw_sha256 = excluded.raw_sha256;"
        )
        self.assertTrue(sql_writes(source, "new.sql"))

    def test_detects_sql_bypasses_insert_or_replace(self):
        source = (
            "INSERT OR REPLACE INTO raw_signals VALUES (1);"
        )
        self.assertTrue(sql_writes(source, "new.sql"))

    def test_detects_sql_bypasses_merge(self):
        source = (
            "MERGE INTO raw_signals r USING source s ON r.id = s.id WHEN MATCHED THEN UPDATE SET raw_json = s.raw_json;"
        )
        self.assertTrue(sql_writes(source, "new.sql"))

    def test_canonical_migration_does_not_exempt_other_writes(self):
        source = """
        CREATE OR REPLACE FUNCTION public.raw_signal_apply_version()
        RETURNS void AS $$
          UPDATE public.raw_signals SET raw_json = new_raw;
        $$ LANGUAGE sql;
        UPDATE public.raw_signals SET raw_json = 'bypass';
        """
        self.assertEqual(sql_writes(source, "web/migrations/150_raw_signals_write_lock.sql"), [(6, "update")])

    def test_allows_reads_metadata_and_initial_inserts(self):
        self.assertFalse(sql_writes("""
        -- update raw_signals set raw_json = 'bad';
        update raw_signals set modality = 'chemical' where raw_json is not null;
        insert into raw_signals (raw_json) values ('{}') on conflict do nothing;
        """, "new.sql"))
        self.assertFalse(python_writes("""
requests.get(f"{base}/rest/v1/raw_signals")
_post_rows(base, key, "raw_signals", rows, resolution="ignore-duplicates")
raw_versioning.replace_raw(base, key, row, new_raw)
        """, "new.py"))
