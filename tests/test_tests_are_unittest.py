"""Guard unittest source conventions and CI shard coverage for tests/test_*.py."""

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_shard
from ci_network_guard import NetworkGuard


# These discovery shims re-export TestCase subclasses from web/tests.
# The exemption applies only to defining a class in the file itself.
TESTCASE_DEFINITION_EXCEPTIONS = {
    "test_web_admin_member_overlap.py": "Re-exports web/tests/test_admin_member_overlap.py",
    "test_web_linkcheck.py": "Re-exports web/tests/test_linkcheck.py",
    "test_web_member_newsletter_optin.py": "Re-exports web/tests/test_member_newsletter_optin.py",
    "test_web_publish_gate.py": "Re-exports web/tests/test_publish_gate.py",
}
CI_SHARD_COUNT = 4  # .github/workflows/grm-ci.yml


def repository_test_files():
    return sorted(path for path in ci_shard.TESTS_DIR.glob("test_*.py") if path.is_file())


def defines_testcase(tree):
    unittest_names = set()
    testcase_names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            unittest_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "unittest"
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "unittest":
            testcase_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "TestCase"
            )
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in testcase_names:
                    return True
                if (isinstance(base, ast.Attribute) and base.attr == "TestCase"
                        and isinstance(base.value, ast.Name) and base.value.id in unittest_names):
                    return True
    return False


def pytest_violations(tree):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            yield node.lineno, f"module-level function {node.name}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[0] == "pytest" for alias in node.names
        ):
            yield node.lineno, "pytest import"
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module.split(".")[0] == "pytest":
            yield node.lineno, "pytest import"

        for decorator in getattr(node, "decorator_list", ()):
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            while isinstance(target, ast.Attribute):
                if isinstance(target.value, ast.Name) and target.value.id == "pytest":
                    yield decorator.lineno, "pytest decorator"
                    break
                target = target.value


class UnittestCollectionGuardTest(unittest.TestCase):
    def test_test_files_use_unittest(self):
        files = repository_test_files()
        self.assertTrue(files, "No tests/test_*.py files found")
        missing_class = set()
        problems = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=path.name)
            if not defines_testcase(tree):
                missing_class.add(path.name)
            problems.extend(
                f"{path.name}:{line}: {reason}" for line, reason in pytest_violations(tree)
            )

        problems.extend(
            f"{name}: no unittest.TestCase subclass"
            for name in sorted(missing_class - TESTCASE_DEFINITION_EXCEPTIONS.keys())
        )
        problems.extend(
            f"{name}: remove obsolete TestCase definition exception"
            for name in sorted(TESTCASE_DEFINITION_EXCEPTIONS.keys() - missing_class)
        )
        self.assertFalse(problems, "\n".join(problems))

    def test_every_test_file_is_assigned_to_a_ci_shard(self):
        expected = {path.name for path in repository_test_files()}
        self.assertTrue(expected, "No tests/test_*.py files found")
        collected = {}
        load_from_module = unittest.TestLoader.loadTestsFromModule

        def record_module(loader, module, *args, **kwargs):
            suite = load_from_module(loader, module, *args, **kwargs)
            path = Path(module.__file__).resolve()
            if path.parent == ci_shard.TESTS_DIR:
                # Record the discovery file even when it re-exports another module's tests.
                collected[path.name] = {id(test) for test in ci_shard.iter_tests(suite)}
            return suite

        guard = NetworkGuard()
        with (guard.installed(), patch.object(sys, "path", sys.path.copy()),
              patch.object(unittest.TestLoader, "loadTestsFromModule", record_module)):
            discovered = ci_shard.discover()
        self.assertFalse(guard.violations, "\n".join(guard.violations))

        units = ci_shard.group_units(discovered)
        timings = ci_shard.load_timings()
        assigned = {
            id(test)
            for index in range(CI_SHARD_COUNT)
            for test in ci_shard.select_shard(units, index, CI_SHARD_COUNT, timings)
        }
        covered = {name for name, test_ids in collected.items() if assigned & test_ids}
        # Discovery failures are real CI failures and must still reach a shard.
        covered.update(
            f"{ci_shard._module_name(test)}.py" for test in discovered
            if type(test).__name__ == "_FailedTest" and id(test) in assigned
        )
        self.assertEqual(sorted(expected - covered), [], "Test files missing from all CI shards")
