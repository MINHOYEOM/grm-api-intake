"""Catch Python regex escapes and CLI stdout encoding regressions."""

import ast
from collections import Counter
import io
from pathlib import Path
import re
import subprocess
import tokenize
import unittest
import warnings


ROOT = Path(__file__).resolve().parents[1]
REGEX_FUNCTIONS = {
    "compile", "findall", "finditer", "fullmatch", "match", "search",
    "split", "sub", "subn",
}
REGEX_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*(\\[bdsw])")
STRING_PREFIX = re.compile(r"[a-zA-Z]*")

# Count identical findings rather than allowing all regexes in a known file.
# Line numbers are deliberately omitted so unrelated edits can move the code.
REGEX_BASELINE = Counter({
    ("web/render.py", "embedded 0x08", 'r"\x08(\\d{2})/(\\d{2})/(\\d{4})\x08"'): 1,
})
STDOUT_BASELINE = frozenset({
    "company/editorial/merge_parts.py",
    "company/ops/intake_counts.py",
    "company/ops/ops_status.py",
    "tests/test_findings_docs_refresh.py",
    "tests/test_no_storage_truncation.py",
    "web/tests/test_render.py",
})


def _python_paths():
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
         "--", "*.py"],
        cwd=ROOT, check=True, stdout=subprocess.PIPE,
    )
    return sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})


def _imports(nodes):
    aliases = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"re", "sys"}:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and not node.level:
            if node.module in {"re", "sys"}:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _name(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value, aliases)}.{node.attr}"
    return ""


def _keyword(call, name):
    return next((item.value for item in call.keywords if item.arg == name), None)


def _literal_parts(node):
    if isinstance(node, (ast.Constant, ast.JoinedStr)):
        yield node
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        yield from _literal_parts(node.left)
        yield from _literal_parts(node.right)


def _string_escapes(segment):
    # Wrapping keeps adjacent literals on separate lines in one expression.
    tokens = tokenize.generate_tokens(io.StringIO(f"({segment})").readline)
    fstring_raw = []
    for token in tokens:
        kind = tokenize.tok_name[token.type]
        if kind == "FSTRING_START":
            fstring_raw.append("r" in STRING_PREFIX.match(token.string)[0].lower())
        elif kind == "FSTRING_END":
            fstring_raw.pop()
        elif kind == "FSTRING_MIDDLE" and not fstring_raw[-1]:
            yield from REGEX_ESCAPE.findall(token.string)
        elif kind == "STRING" and not fstring_raw:
            if "r" not in STRING_PREFIX.match(token.string)[0].lower():
                yield from REGEX_ESCAPE.findall(token.string)


def _regex_findings(source, nodes, aliases):
    if "\x08" in source:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if tokenize.tok_name[token.type] in {"STRING", "FSTRING_MIDDLE", "TSTRING_MIDDLE"}:
                if "\x08" in token.string:
                    yield token.start[0], "embedded 0x08", token.string

    # AST column offsets count UTF-8 bytes.
    lines = source.encode("utf-8").split(b"\n")
    for call in nodes:
        if not isinstance(call, ast.Call):
            continue
        module, _, function = _name(call.func, aliases).rpartition(".")
        if module != "re" or function not in REGEX_FUNCTIONS:
            continue
        pattern = call.args[0] if call.args else _keyword(call, "pattern")
        for literal in _literal_parts(pattern):
            if isinstance(literal, ast.Constant) and not isinstance(literal.value, (str, bytes)):
                continue
            start, end = literal.lineno - 1, literal.end_lineno - 1
            parts = lines[start:end + 1]
            parts[-1] = parts[-1][:literal.end_col_offset]
            parts[0] = parts[0][literal.col_offset:]
            segment = b"\n".join(parts).decode("utf-8")
            escapes = sorted(set(_string_escapes(segment)))
            if escapes:
                yield literal.lineno, ", ".join(escapes), segment


def _main_guard(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    comparison = node.test
    if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
        return False
    left, right = comparison.left, comparison.comparators[0]
    if isinstance(left, ast.Constant):
        left, right = right, left
    return (
        isinstance(left, ast.Name) and left.id == "__name__"
        and isinstance(right, ast.Constant) and right.value == "__main__"
    )


def _unicode_output(call, aliases):
    name = _name(call.func, aliases)
    if name in {"print", "builtins.print"}:
        stream = _keyword(call, "file")
        if stream is not None and _name(stream, aliases) != "sys.stdout":
            if not (isinstance(stream, ast.Constant) and stream.value is None):
                return False
        values = [*call.args, _keyword(call, "sep"), _keyword(call, "end")]
    elif name in {"sys.stdout.write", "sys.stdout.writelines"}:
        values = call.args
    else:
        return False
    return any(
        isinstance(part, ast.Constant) and isinstance(part.value, str)
        and not part.value.isascii()
        for value in values if value is not None for part in ast.walk(value)
    )


def _configures_stdout(tree, aliases):
    functions = {
        node.name: node.body for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    visited = set()

    def configures(nodes, stream_aliases):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if _main_guard(node):
                if configures(node.body, stream_aliases):
                    return True
                continue
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                if isinstance(node.iter, (ast.Tuple, ast.List)) and any(
                    _name(item, stream_aliases) == "sys.stdout" for item in node.iter.elts
                ):
                    # GRM scripts often configure stdout and stderr in one loop.
                    loop_aliases = {**stream_aliases, node.target.id: "sys.stdout"}
                    if configures(node.body, loop_aliases):
                        return True
            if isinstance(node, ast.Call):
                name = _name(node.func, stream_aliases)
                encoding = _keyword(node, "encoding")
                if (
                    name == "sys.stdout.reconfigure" and isinstance(encoding, ast.Constant)
                    and isinstance(encoding.value, str)
                    and encoding.value.lower().replace("_", "-") in {"utf-8", "utf8"}
                ):
                    return True
                if name in functions and name not in visited:
                    visited.add(name)
                    if configures(functions[name], aliases):
                        return True
            if configures(ast.iter_child_nodes(node), stream_aliases):
                return True
        return False

    return configures(tree.body, aliases)


def _stdout_finding(tree, nodes, aliases):
    if not any(_main_guard(node) for node in tree.body):
        return None
    lines = [
        node.lineno for node in nodes
        if isinstance(node, ast.Call) and _unicode_output(node, aliases)
    ]
    if lines and not _configures_stdout(tree, aliases):
        return min(lines)
    return None


def _inspect(source, path):
    # Invalid escapes are reported with their locations by the lint itself.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        tree = ast.parse(source, filename=path)
    nodes = list(ast.walk(tree))
    aliases = _imports(nodes)
    return list(_regex_findings(source, nodes, aliases)), _stdout_finding(tree, nodes, aliases)


class PythonStringLintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.regex_findings = []
        cls.stdout_findings = {}
        for path in _python_paths():
            file = ROOT / path
            if not file.is_file():
                continue
            with tokenize.open(file) as source:
                regex_findings, stdout_line = _inspect(source.read(), path)
            cls.regex_findings.extend((path, *finding) for finding in regex_findings)
            if stdout_line is not None:
                cls.stdout_findings[path] = stdout_line

    def test_regex_literals(self):
        remaining = REGEX_BASELINE.copy()
        new = []
        for path, line, escapes, literal in self.regex_findings:
            key = path, escapes, literal
            if remaining[key]:
                remaining[key] -= 1
            else:
                new.append(f"{path}:{line}: {escapes} in {literal!r}")
        self.assertFalse(new, "\nNew unsafe string literals:\n" + "\n".join(new))

    def test_cli_stdout_encoding(self):
        existing = sorted(self.stdout_findings.keys() & STDOUT_BASELINE)
        if existing:
            warnings.warn(
                "Existing CLI stdout UTF-8 findings:\n" + "\n".join(existing),
                stacklevel=1,
            )
        new = sorted(self.stdout_findings.keys() - STDOUT_BASELINE)
        self.assertFalse(
            new,
            "\nSet UTF-8 stdout encoding in these CLI scripts:\n"
            + "\n".join(f"{path}:{self.stdout_findings[path]}" for path in new),
        )
