"""Keep the repository root Python inventory in GRM_SYSTEM.md up to date."""

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# No current exceptions. Any future exception must name the file and explain why.
ROOT_PYTHON_EXCEPTIONS = frozenset()


class SystemTreeTests(unittest.TestCase):
    def test_system_tree_lists_tracked_root_python_files(self):
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z", "--", ":(top,glob)*.py"], cwd=ROOT
        )
        root_python = set(tracked.decode("utf-8").split("\0")) - {""}

        with (ROOT / "GRM_SYSTEM.md").open(encoding="utf-8") as document:
            heading = next((line for line in document if line.startswith("### 5.1 ")), None)
            assert heading, "GRM_SYSTEM.md is missing §5.1"
            opening = next(
                (line for line in document if line.strip() == "```" or line.startswith("### ")),
                None,
            )
            assert opening and opening.strip() == "```", "§5.1 is missing its fenced tree"

            documented = set()
            for line in document:
                if line.strip() == "```":
                    break
                assert not line.startswith("### "), "§5.1 tree is missing its closing fence"
                if line.startswith(("├─ ", "└─ ")):
                    entries = line[3:].partition("#")[0]
                    entries = re.sub(r"\([^()]*\)", "", entries)
                    documented.update(entry.strip() for entry in re.split(r"[,·]", entries))
            else:
                raise AssertionError("§5.1 tree is missing its closing fence")

        missing = sorted(root_python - documented - ROOT_PYTHON_EXCEPTIONS)
        assert not missing, (
            "Add these root Python files to GRM_SYSTEM.md §5.1: " + ", ".join(missing)
        )
