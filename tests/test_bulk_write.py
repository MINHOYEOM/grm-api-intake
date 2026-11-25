import ast
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import grm_cli


def _assignments(nodes):
    """Keep simple bindings with exactly one assignment in the current scope."""
    found = {}
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value if target.id not in found else None
    return found


def _resolve(node, names):
    if isinstance(node, ast.Name):
        return names.get(node.id)
    return node


def _integer(node, names):
    node = _resolve(node, names)
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    return None


def _call_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _row_count(node, names):
    if isinstance(node, ast.Name):
        return _row_count(names.get(node.id), {k: v for k, v in names.items() if k != node.id})
    if isinstance(node, ast.List) and not any(isinstance(e, ast.Starred) for e in node.elts):
        return len(node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        for literal, multiplier in ((node.left, node.right), (node.right, node.left)):
            literal = _resolve(literal, names)
            factor = _integer(multiplier, names)
            if isinstance(literal, ast.List) and factor is not None:
                count = _row_count(literal, names)
                if count is not None:
                    return count * max(0, factor)
    if isinstance(node, ast.ListComp) and len(node.generators) == 1:
        gen = node.generators[0]
        if (not gen.ifs and isinstance(gen.iter, ast.Call)
                and isinstance(gen.iter.func, ast.Name) and gen.iter.func.id == "range"
                and len(gen.iter.args) == 1 and not gen.iter.keywords):
            return _integer(gen.iter.args[0], names)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "encode":
            return _row_count(node.func.value, names)
        if _call_name(node.func) == "dumps" and node.args:
            return _row_count(node.args[0], names)
    return None


def oversized_postgrest_writes(source):
    """Identify provably large direct writes using local literals and constants."""
    tree = ast.parse(source)
    violations = []
    module_names = _assignments(tree.body)
    scopes = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    for scope in scopes:
        pending = list(scope.body)
        nodes = []
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes.append(node)
            pending.extend(ast.iter_child_nodes(node))
        names = {**module_names, **_assignments(nodes)}
        for call in (n for n in nodes if isinstance(n, ast.Call)):
            name = _call_name(call.func)
            kwargs = {kw.arg: kw.value for kw in call.keywords}
            if name == "_post_rows" and len(call.args) >= 4:
                payload = call.args[3]
            elif name == "upsert" and call.args:
                receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
                if not isinstance(receiver, ast.Call) or _call_name(receiver.func) not in {"from_", "table"}:
                    continue
                payload = call.args[0]
            elif name in {"post", "patch", "request"}:
                args = call.args
                if name == "request":
                    method = _resolve(args[0] if args else kwargs.get("method"), names)
                    if not isinstance(method, ast.Constant) or str(method.value).upper() not in {"POST", "PATCH"}:
                        continue
                    args = args[1:]
                url = _resolve(args[0] if args else kwargs.get("url"), names)
                if url is None or "/rest/v1/" not in ast.unparse(url):
                    continue
                payload = kwargs.get("json", kwargs.get("data"))
                resolved = _resolve(payload, names)
                if isinstance(resolved, ast.Dict):
                    payload = next(
                        (value for key, value in zip(resolved.keys, resolved.values)
                         if isinstance(key, ast.Constant) and key.value in {"p_rows", "p_items", "p_keys"}),
                        None,
                    )
            else:
                continue
            count = _row_count(payload, names)
            if count is not None and count > 1_000:
                violations.append((call.lineno, count))
    return violations


class TestBulkWrite(unittest.TestCase):
    def test_bulk_write_clamps_batches_and_paces_across_calls(self):
        events = []
        self.enterContext(patch.object(grm_cli.time, "sleep", side_effect=events.append))
        bulk = grm_cli.BulkWriteRun()

        def send(batch, offset):
            events.append((len(batch), offset))

        bulk.write(list(range(1_001)), send, batch_size=1_500)
        bulk.write([1, 2], send, batch_size=1)
        self.assertEqual(events, [(500, 0), 1, (500, 500), 1, (1, 1_000), 1, (1, 0), 1, (1, 1)])
        self.assertEqual(bulk.attempted, 1_003)

    def test_bulk_write_rejects_an_over_budget_plan_before_sending(self):
        self.enterContext(patch.object(grm_cli.time, "sleep", return_value=None))
        bulk = grm_cli.BulkWriteRun()
        sent = []

        with self.assertRaisesRegex(grm_cli.BulkWriteError, "2000 remain"):
            bulk.write([0] * 2_001, lambda batch, _: sent.extend(batch))
        self.assertEqual(sent, [])

        bulk.write([0] * 2_000, lambda batch, _: sent.extend(batch))
        with self.assertRaisesRegex(grm_cli.BulkWriteError, "0 remain"):
            bulk.write([1], lambda batch, _: sent.extend(batch))
        self.assertEqual(len(sent), 2_000)

    def test_bulk_write_stops_after_two_consecutive_failures(self):
        self.enterContext(patch.object(grm_cli.time, "sleep", return_value=None))
        bulk = grm_cli.BulkWriteRun()
        sent = []

        def send(batch, _offset):
            sent.extend(batch)
            return batch == [2]

        with self.assertRaisesRegex(grm_cli.BulkWriteError, "consecutive"):
            bulk.write([1, 2, 3, 4, 5], send, batch_size=1)
        self.assertEqual(sent, [1, 2, 3, 4])
        self.assertEqual(bulk.attempted, 4)
        with self.assertRaisesRegex(grm_cli.BulkWriteError, "consecutive"):
            bulk.write([6], send)

    def test_bulk_write_stops_immediately_when_sender_raises(self):
        bulk = grm_cli.BulkWriteRun()

        def send(_batch, _offset):
            raise OSError("fixture transport failure")

        with self.assertRaisesRegex(OSError, "fixture transport failure"):
            bulk.write([1, 2], send, batch_size=1)
        self.assertEqual(bulk.attempted, 1)

    def test_large_fda_run_resumes_from_committed_rows(self):
        import collect_fda_inspections as collector
        import findings_supabase_backfill as backfill

        source_rows = [
            {"InspectionID": str(i), "FiscalYear": "2026", "ClassificationCode": "NAI",
             "ProjectArea": collector.GMP_PROJECT_AREA, "ProductType": collector.TARGET_PRODUCT_TYPE}
            for i in range(2_001)
        ]
        committed = {}
        batch_sizes = []
        self.enterContext(patch.object(grm_cli.time, "sleep", return_value=None))
        self.enterContext(patch.object(
            collector, "fetch_all_pages", return_value=collector.FetchResult(source_rows, False, 3, "")
        ))

        def read(_url, _key, table, **kwargs):
            self.assertEqual(table, "fda_inspections")
            self.assertEqual(kwargs["extra_params"], {"fiscal_year": "in.(2026)"})
            return list(committed.values())

        def post(_url, _key, _table, rows, _conflict, **_kwargs):
            batch_sizes.append(len(rows))
            committed.update((row["inspection_id"], row) for row in rows)
            return 201, rows, ""

        self.enterContext(patch.object(backfill, "_fetch_all_pages", read))
        run_args = dict(dry_run=False, base_url="https://fixture.invalid", service_key="fixture",
                        api_user="fixture", api_key="fixture", poster=post, chunk_size=1_000)
        report, status = collector.run(**run_args)
        self.assertEqual(status, 1)
        self.assertEqual(report.upserted, 2_000)
        self.assertEqual(report.errors, ["write_deferred:1:rerun_same_selection"])
        self.assertEqual(batch_sizes, [500, 500, 500, 500])

        source_rows[0]["LegalName"] = "Updated firm"
        report, status = collector.run(**run_args)
        self.assertEqual(status, 0)
        self.assertEqual(report.upserted, 2)
        self.assertEqual(len(committed), 2_001)
        self.assertEqual(committed["0"]["legal_name"], "Updated firm")
        self.assertEqual(batch_sizes[-1], 2)

    def test_mfds_reserves_the_snapshot_row_and_preserves_the_daily_new_count(self):
        import json

        import mfds_gmp_cert_snapshot as cert

        source_rows = [{"unique": i} for i in range(2_001)]
        saved = {}
        snapshots = []
        self.enterContext(patch.object(grm_cli.time, "sleep", return_value=None))
        self.enterContext(patch.dict(os.environ, {
            "DATA_GO_KR_SERVICE_KEY": "fixture",
            "SUPABASE_URL": "https://fixture.invalid",
            "SUPABASE_SERVICE_ROLE_KEY": "fixture",
        }))
        self.enterContext(patch.object(cert, "fetch_all", return_value={
            "items": source_rows, "total_count": len(source_rows),
            "complete": True, "errors": [],
        }))

        class Rest:
            def __init__(self, _url, _key):
                pass

            def get_all(self, _table, params):
                return [] if "firm_key" in params else list(saved.values())

            def rpc(self, _name):
                return {}

            def upsert(self, table, rows, _key, *, bulk):
                def send(batch, _offset):
                    if table == "mfds_gmp_cert":
                        saved.update((row["cert_id"], row) for row in batch)
                    else:
                        snapshots.extend(batch)

                bulk.write(rows, send)

        self.enterContext(patch.object(cert, "Rest", Rest))
        tmp_path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        report = tmp_path / "report.json"
        args = ["--output", str(report), "--unlinked-output", str(tmp_path / "unlinked.json")]
        self.assertEqual(cert.main(args), 1)
        self.assertEqual(len(saved), 1_999)
        self.assertEqual(snapshots, [])

        self.assertEqual(cert.main(args), 0)
        self.assertEqual(len(saved), 2_001)
        self.assertEqual([row["new_rows"] for row in snapshots], [2_001])
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["new_rows"], 2_001)

    def test_repository_has_no_provable_oversized_postgrest_writes(self):
        root = Path(__file__).resolve().parents[1]
        tracked = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py"], cwd=root)
        problems = []
        for filename in tracked.decode("utf-8").split("\0"):
            file = Path(filename)
            if not filename or {"tests", "docs"} & set(file.parts):
                continue
            for line, count in oversized_postgrest_writes((root / file).read_text(encoding="utf-8-sig")):
                problems.append(f"{filename}:{line}: {count} rows")
        self.assertFalse(problems, "\n".join(problems))

    def test_static_guard_rejects_direct_write_mutations(self):
        for wire in (
            'requests.post(url, json=rows)',
            'requests.patch(url, data=json.dumps(rows).encode("utf-8"))',
            'requests.request("POST", url, json={"p_rows": rows})',
            'sb.table("things").upsert(rows).execute()',
            'fsa._post_rows(base, key, "things", rows, "id")',
        ):
            with self.subTest(wire=wire):
                source = (
                    'SIZE = 1001\n'
                    'def send(base):\n'
                    '    url = f"{base}/rest/v1/things"\n'
                    '    rows = [{"id": n} for n in range(SIZE)]\n'
                    f'    {wire}\n'
                )
                self.assertEqual(oversized_postgrest_writes(source), [(5, 1_001)])
                self.assertEqual(oversized_postgrest_writes(source.replace("1001", "1000")), [])

    def test_static_guard_leaves_helper_batches_and_unrelated_posts_alone(self):
        self.assertEqual(oversized_postgrest_writes("""
def send(base, bulk):
    rows = [{"id": 1}] * 1001
    bulk.write(rows, lambda batch, _: requests.post(f"{base}/rest/v1/things", json=batch))
    requests.post(f"{base}/external", json=rows)
"""), [])
