"""Guard FDA request paths against locally hard-coded sleep intervals."""

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FDA_URL = re.compile(r"https?://(?:[a-z0-9-]+\.)*fda\.gov(?=[:/?#]|$)", re.I)
# These entry points use FDA URLs or fetch functions supplied by other modules.
INDIRECT_FDA_REQUEST_PATHS = {
    "backfill_483_body_full.py",
    "backfill_483_detail_refresh.py",
    "backfill_483_inspectors.py",
    "backfill_wl_body_recovery.py",
    "collect_fda_backfill.py",
    "fda483_ocr_backfill.py",
    "verify_published_sources.py",
}


class FdaHttpPolicyTests(unittest.TestCase):
    def test_fda_request_paths_do_not_sleep_for_a_hard_coded_interval(self):
        violations = []
        for path in sorted(ROOT.glob("*.py")):
            nodes = list(ast.walk(ast.parse(path.read_bytes(), filename=str(path))))
            if path.name not in INDIRECT_FDA_REQUEST_PATHS and not any(
                (isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and FDA_URL.search(node.value))
                or (isinstance(node, ast.alias) and node.name == "fda_checked_request")
                for node in nodes
            ):
                continue

            time_names = {
                alias.asname or alias.name
                for node in nodes if isinstance(node, ast.Import)
                for alias in node.names if alias.name == "time"
            }
            sleep_names = {
                alias.asname or alias.name
                for node in nodes if isinstance(node, ast.ImportFrom) and node.module == "time"
                for alias in node.names if alias.name == "sleep"
            }
            for node in nodes:
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                calls_sleep = (
                    isinstance(func, ast.Attribute) and func.attr == "sleep"
                    and isinstance(func.value, ast.Name) and func.value.id in time_names
                ) or (isinstance(func, ast.Name) and func.id in sleep_names)
                interval = node.args[0]
                if isinstance(interval, ast.UnaryOp) and isinstance(interval.op, (ast.UAdd, ast.USub)):
                    interval = interval.operand
                if calls_sleep and isinstance(interval, ast.Constant) and type(interval.value) in (int, float):
                    violations.append(f"{path.name}:{node.lineno}")

        self.assertFalse(
            violations,
            "Use grm_http_policy.py for FDA request intervals: " + ", ".join(violations),
        )
