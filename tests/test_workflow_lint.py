"""Static checks for common GitHub Actions mistakes, using only the stdlib."""

from pathlib import Path
import re
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# (file, source line, rule, source line text) -> reason for accepting it.
KNOWN_EXCEPTIONS = {}

QUOTED = r"'(?:[^']|'')*'"
EXPRESSION = re.compile(r"\$\{\{((?:" + QUOTED + r"|[^'])*?)\}\}")
YAML_COMMENT = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^']|'')*')|(?:^|\s+)#.*""")
KEY = re.compile(r"""^( *)(- +)?(['"]?)([\w.-]+)\3:\s*(.*)$""")
BLOCK = re.compile(r"[|>][+\-1-9]{0,2}")
TOKENS = re.compile(
    QUOTED + r"|[a-z_][\w-]*|(?:0x[0-9a-f]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)|\S",
    re.I,
)
BOOL_STRING = r"'(?:true|false)'"
INPUT = r"(?<![\w.])inputs(?:\.([\w-]+)|\[\s*'([\w-]+)'\s*\])"
BOOL_OPERAND = rf"(?:\(\s*)*{INPUT}(?:\s*\|\|\s*{BOOL_STRING})?(?:\s*\))*"
BOOL_COMPARISON = re.compile(
    rf"{BOOL_OPERAND}\s*[!=]=\s*{BOOL_STRING}|{BOOL_STRING}\s*[!=]=\s*{BOOL_OPERAND}",
    re.I,
)
ENV = r"(?<![\w.])env(?:\.[\w-]+|\[\s*'[\w-]+'\s*\])"
ENV_GATE = re.compile(rf"{ENV}\s*==\s*'true'|'true'\s*==\s*{ENV}", re.I)
SECRET = re.compile(r"(?<![\w.])secrets\s*[.\[]", re.I)
ECHO_COMMAND = re.compile(
    r"^\s*(?:(?:then|do|else)\s+|[({]\s*)?(?:command\s+|builtin\s+)?echo(?=\s|$)",
    re.I,
)
SHELL_SEGMENT = re.compile(r"""(?:\\.|"(?:\\.|[^"\\])*"|'[^']*'|[^"'\\;&|])+""")


def _scalars(lines):
    """Yield key paths and source-numbered values for the workflow's block YAML."""
    parents = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        line = YAML_COMMENT.sub(lambda m: m[1] or "", line).rstrip()
        match = KEY.match(line)
        if not match:
            continue
        spaces, sequence, _, key, value = match.groups()
        indent = len(spaces) + len(sequence or "")
        # A sequence item also terminates the previous item's mapping.
        boundary = len(spaces) if sequence else indent
        while parents and parents[-1][0] >= boundary:
            parents.pop()
        path = tuple(name for _, name in parents) + (key,)
        if not value:
            parents.append((indent, key))
            continue
        if BLOCK.fullmatch(value):
            body = []
            while i < len(lines):
                child = lines[i]
                if child.strip() and len(child) - len(child.lstrip()) <= indent:
                    break
                body.append((i + 1, child))
                i += 1
            yield path, body, value.startswith(">")
        else:
            body = [(i, value)]
            if value[0] == value[-1] and value[0] in "'\"":
                quote = value[0]
                value = value[1:-1]
                if quote == "'":
                    value = value.replace("''", "'")
                body[0] = (i, value)
            elif key == "if":
                while i < len(lines) and len(lines[i]) - len(lines[i].lstrip()) > indent:
                    child = YAML_COMMENT.sub(lambda m: m[1] or "", lines[i]).rstrip()
                    if child:
                        body.append((i + 1, child))
                    i += 1
            yield path, body, True


def _has_arithmetic(tokens):
    for i, token in enumerate(tokens):
        if token == "*" and i and tokens[i - 1] in {".", "["}:
            continue  # Object filter.
        if token == "-" and i + 1 < len(tokens) and tokens[i + 1][0].isdigit():
            if not i or tokens[i - 1] in {"(", "[", ",", "=", "!", "<", ">", "&", "|"}:
                continue  # Negative numeric literal.
        if token in {"+", "-", "*", "/", "%"}:
            return True
    return False


def _compares_boolean_to_string(expression, boolean_inputs, strings):
    for match in BOOL_COMPARISON.finditer(expression):
        name = next(group for group in match.groups() if group is not None)
        if match.start() not in strings and name.lower() in boolean_inputs:
            return True
    return False


def _run_commands(body, folded):
    if folded:
        yield body
        return
    command = []
    for item in body:
        number, line = item
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.rstrip().endswith(("\\", "`")):
            command.append((number, line.rstrip()[:-1]))
            continue
        yield command + [item]
        command = []
    if command:
        yield command


def _lint(source):
    lines = source.removeprefix("\ufeff").splitlines()
    scalars = list(_scalars(lines))
    boolean_inputs = {
        path[3].lower()
        for path, body, _ in scalars
        if len(path) == 5 and path[0] == "on" and path[2] == "inputs"
        and path[1] in {"workflow_dispatch", "workflow_call"} and path[4] == "type"
        and len(body) == 1 and body[0][1] == "boolean"
    }
    for path, body, folded in scalars:
        if not body:
            continue
        key = path[-1]
        value = "\n".join(line for _, line in body)
        expressions = [(m.group(1), m.start(1)) for m in EXPRESSION.finditer(value)]
        if key == "if" and not expressions:
            expressions = [(value, 0)]  # if also accepts expressions without ${{ }}.
        for expression, offset in expressions:
            matches = list(TOKENS.finditer(expression))
            tokens = [m[0] for m in matches]
            strings = {
                pos for m in matches if m[0].startswith("'")
                for pos in range(m.start() + 1, m.end())
            }
            number = body[value.count("\n", 0, offset)][0]
            if _has_arithmetic(tokens):
                yield number, "arithmetic"
            if _compares_boolean_to_string(expression, boolean_inputs, strings):
                yield number, "boolean-string"
            if key == "if" and any(m.start() not in strings for m in ENV_GATE.finditer(expression)):
                yield number, "env-gate"

        if key == "run":
            for command in _run_commands(body, folded):
                script = "\n".join(line for _, line in command)
                # Mask expressions before looking for shell separators such as ||.
                masked = EXPRESSION.sub(lambda m: "x" * len(m[0]), script)
                for segment in SHELL_SEGMENT.finditer(masked):
                    comment = next((m for m in YAML_COMMENT.finditer(segment[0]) if not m[1]), None)
                    end = segment.start() + comment.start() if comment else segment.end()
                    if not ECHO_COMMAND.match(segment[0]):
                        if comment:
                            break
                        continue
                    for match in EXPRESSION.finditer(script, segment.start(), end):
                        expression = re.sub(QUOTED, "''", match[1])
                        if SECRET.search(expression):
                            yield command[script.count("\n", 0, match.start())][0], "echo-secret"
                    if comment:
                        break


class WorkflowLintTest(unittest.TestCase):
    def test_repository_workflows(self):
        paths = sorted(WORKFLOWS.glob("*.yml"))
        self.assertTrue(paths, f"워크플로 없음 ({WORKFLOWS})")
        failures = []
        used_exceptions = set()
        for path in paths:
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            for number, rule in sorted(set(_lint(source))):
                location = f".github/workflows/{path.name}:{number}"
                entry = (path.name, number, rule, lines[number - 1].strip())
                if rule == "env-gate":
                    print(f"\n경고 {location} [{rule}] 환경 변수 게이트 뒤 단계의 시험 범위 확인")
                elif entry in KNOWN_EXCEPTIONS:
                    used_exceptions.add(entry)
                    print(f"\n알려진 예외 {location} [{rule}] {KNOWN_EXCEPTIONS[entry]}")
                else:
                    failures.append(f"{location} [{rule}]")
        for filename, number, rule, _ in sorted(KNOWN_EXCEPTIONS.keys() - used_exceptions):
            failures.append(f".github/workflows/{filename}:{number} [{rule}] 오래된 예외")
        self.assertFalse(failures, "\n" + "\n".join(failures))

    def test_arithmetic_mutations(self):
        for operator in ("+", "-", "*", "/", "%"):
            with self.subTest(operator=operator):
                self.assertEqual(
                    list(_lint("run: echo ${{ 4 " + operator + " 2 }}")),
                    [(1, "arithmetic")],
                )
        self.assertEqual(list(_lint("if: inputs.count >\n  4+2")), [(1, "arithmetic")])
        self.assertEqual(list(_lint("if: >-\n  inputs.count -\n  2")), [(2, "arithmetic")])
        self.assertEqual(
            list(_lint("run: echo ${{ format('a + b / c', -2.0e-3, needs.*.result) }}")),
            [],
        )

    def test_boolean_string_mutations(self):
        workflow = """\
on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:
    inputs:
      dry-run:
        type: boolean # typed value
      mode:
        type: choice
jobs:
  example:
    steps:
      - name: Example
        if: """
        for expression in (
            "inputs.dry-run == 'true'",
            "${{ 'false' != inputs['dry-run'] }}",
            "${{ (inputs.dry-run || 'false') != 'true' }}",
        ):
            with self.subTest(expression=expression):
                self.assertEqual(list(_lint("\ufeff" + workflow + expression)), [(14, "boolean-string")])
        for expression in (
            "inputs.dry-run == true",
            "inputs.mode == 'true'",
            "github.event.inputs.dry-run == 'true'",
            "contains('inputs.dry-run == ''true''', 'dry-run')",
        ):
            with self.subTest(expression=expression):
                self.assertEqual(list(_lint(workflow + expression)), [])

    def test_secret_echo_mutations(self):
        workflow = """\
steps:
  - env:
      TOKEN: ${{ secrets.TOKEN }}
    run: |
      # echo "${{ secrets.TOKEN }}"
      echo "secrets.TOKEN"
      echo "${{ 'secrets.TOKEN' }}"
      printf 'a; echo %s' "${{ secrets.TOKEN }}"
      echo ok; curl -H "${{ secrets.TOKEN }}"
      echo ok # "${{ secrets.TOKEN }}"
      if ready; then echo "${{ secrets.TOKEN || secrets.BACKUP }}"; fi
      echo \\
        "${{ secrets['TOKEN'] }}"
"""
        self.assertEqual(list(_lint(workflow)), [(11, "echo-secret"), (13, "echo-secret")])

    def test_env_gate_is_warning(self):
        self.assertEqual(
            list(_lint("if: >-\n  always() &&\n  env.ENABLED == 'true'")),
            [(2, "env-gate")],
        )
