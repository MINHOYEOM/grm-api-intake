"""List root scripts and GitHub Actions workflows with no visible use."""

import argparse
import ast
import os
import re
import tokenize
from pathlib import Path

SCRIPT_NAME = re.compile(r"(?<![\w.-])([\w.-]+\.py)(?![\w-]|\.[\w.-])")
MODULE_COMMAND = re.compile(
    r"\b(?:python(?:\d+(?:\.\d+)?)?|py)(?:\.exe)?[ \t]+"
    r"(?:-[\w]+[ \t]+)*-m[ \t]+['\"]?([\w]+)(?![\w.-])"
)
DISPATCH_TARGET = re.compile(
    r"""\bgh\s+workflow\s+run\s+(?:"([^"]+)"|'([^']+)'|([^\s'";|&]+))"""
    r"""|/actions/workflows/([^/\s'"]+)/dispatches\b"""
    r"""|\bcreateWorkflowDispatch\s*\(\s*\{[^}]*?\bworkflow_id\s*:\s*['"]([^'"]+)['"]"""
)


def _script_references(source):
    return set(SCRIPT_NAME.findall(source)) | {
        f"{name}.py" for name in MODULE_COMMAND.findall(source)
    }


def _python_references(path):
    with tokenize.open(path) as stream:
        source = stream.read()
    references = _script_references(source)
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import):
            references.update(f"{alias.name.split('.')[0]}.py" for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            references.add(f"{node.module.split('.')[0]}.py")
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            first_arg = node.args[0]
            if name in {"import_module", "__import__"} and isinstance(first_arg, ast.Constant):
                if isinstance(first_arg.value, str):
                    references.add(f"{first_arg.value.split('.')[0]}.py")
    return references


def _repository_files(root):
    for directory, subdirs, filenames in os.walk(root):
        subdirs[:] = [
            name for name in subdirs
            if name not in {".git", "__pycache__", "node_modules"}
            and not (Path(directory) / name / "pyvenv.cfg").is_file()
        ]
        for name in filenames:
            path = Path(directory) / name
            if not path.is_symlink():
                yield path


WORKFLOW_KEY = re.compile(r"""^(on|'on'|"on"|name|jobs):[ \t]*(.*)$""")
EVENT_ENTRY = re.compile(r"""^(?:-\s+)?['"]?([\w-]+)['"]?(?=\s*(?::|$))""")
FLOW_TOKEN = re.compile(r"""'[^']*'|"[^"]*"|[\[\]{},:]|[^\s\[\]{},:'"]+""")
FLOW_JOB_TOKEN = re.compile(r"(?<!\S)#[^\n]*|" + FLOW_TOKEN.pattern)
LOCAL_WORKFLOW_PATH = r"""\./\.github/workflows/([^\s'"]+)"""
LOCAL_WORKFLOW = re.compile(r"""^(?:uses|'uses'|"uses"):\s*['"]?""" + LOCAL_WORKFLOW_PATH)
FLOW_JOB = re.compile(r"""^(?:'[^']+'|"[^"]+"|[\w-]+):\s*(\{.*)$""")
STEP_COMMAND = re.compile(r"^(?:-\s+)?(?:run|script):\s*(.*)$")


def _workflow_fields(source):
    fields = {}
    current = None
    for line in source.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            key = WORKFLOW_KEY.match(line)
            if key:
                current = fields.setdefault(key[1].strip("'\""), [key[2]])
                continue
            if not line.startswith("- "):
                current = None
        if current is not None:
            current.append(line)
    return fields


def _workflow_events(lines):
    if not lines:
        return set()
    value = re.split(r"\s+#|^#", lines[0], maxsplit=1)[0]
    if value.startswith(("[", "{")):
        events = set()
        depth = 0
        previous = ""
        for token in FLOW_TOKEN.findall("\n".join([value, *lines[1:]])):
            if depth == 1 and previous in {"[", "{", ","} and EVENT_ENTRY.fullmatch(token):
                events.add(token.strip("'\""))
            if token in {"[", "{"}:
                depth += 1
            elif token in {"]", "}"}:
                depth -= 1
                if depth == 0:
                    break
            previous = token
        return events
    if value.strip():
        match = EVENT_ENTRY.match(value)
        return {match[1]} if match else set()
    entries = [re.split(r"\s+#", line, maxsplit=1)[0] for line in lines[1:]]
    indent = min((len(line) - len(line.lstrip()) for line in entries), default=0)
    return {match[1] for line in entries
            if len(line) - len(line.lstrip()) == indent
            if (match := EVENT_ENTRY.match(line.lstrip()))}


def _flow_workflow_calls(source, field_depth):
    tokens = [token for token in FLOW_JOB_TOKEN.findall(source) if not token.startswith("#")]
    depth = 0
    for index, token in enumerate(tokens):
        if token in {"{", "["}:
            depth += 1
        elif token in {"}", "]"}:
            depth -= 1
            if depth == 0:
                break
        elif (
            depth == field_depth
            and token.strip("'\"") == "uses"
            and tokens[index - 1] in {"{", ","}
        ):
            field = tokens[index + 1:index + 4]
            if len(field) == 3 and field[0] == ":" and field[2] in {",", "}"}:
                if target := re.fullmatch(LOCAL_WORKFLOW_PATH, field[1].strip("'\"")):
                    yield target[1]


def _workflow_calls(lines):
    if not lines:
        return
    body = lines[1:]
    header = lines[0]
    if header.startswith("{"):
        yield from _flow_workflow_calls("\n".join(lines), field_depth=2)
        return
    if not header.split("#", 1)[0].strip() and body and body[0].lstrip().startswith("{"):
        yield from _flow_workflow_calls("\n".join(body), field_depth=2)
        return

    job_indent = min((len(line) - len(line.lstrip()) for line in body), default=0)
    job_field_indent = None
    flow_job = []
    command = []
    command_indent = 0
    for line in [*body, ""]:
        content = line.lstrip()
        indent = len(line) - len(content)
        if flow_job:
            if content and (indent > job_indent or content.startswith("}")):
                flow_job.append(content)
            else:
                yield from _flow_workflow_calls("\n".join(flow_job), field_depth=1)
                flow_job.clear()
        if command and indent > command_indent:
            command.append(content)
            continue
        if command:
            for groups in DISPATCH_TARGET.findall("\n".join(command)):
                yield next(value for value in groups if value)
            command.clear()
        if indent == job_indent:
            job_field_indent = None
            if job := FLOW_JOB.match(content):
                flow_job.append(job[1])
        elif indent > job_indent:
            if job_field_indent is None:
                job_field_indent = indent
            if not flow_job and indent == job_field_indent and (target := LOCAL_WORKFLOW.match(content)):
                yield target[1]
            if indent > job_field_indent and (step := STEP_COMMAND.match(content)):
                command = [step[1]]
                command_indent = indent + (2 if content.startswith("- ") else 0)


def find_orphans(root):
    """Return sorted root script names and repository-relative workflow paths."""
    root = Path(root)
    root_scripts = set()
    used_scripts = set()
    workflows = {}

    for path in _repository_files(root):
        relative = path.relative_to(root)
        if relative.parent == Path(".github/workflows") and path.suffix in {".yml", ".yaml"}:
            source = path.read_text(encoding="utf-8-sig")
            workflows[path.name] = _workflow_fields(source)
            used_scripts.update(_script_references(source))
        elif path.suffix == ".py":
            references = _python_references(path)
            if relative.parent == Path("."):
                root_scripts.add(path.name)
                references.discard(path.name)
            used_scripts.update(references)
        elif relative.parts[0] == "company" and path.suffix in {".md", ".txt", ".sh", ".yml", ".yaml"}:
            used_scripts.update(_script_references(path.read_text(encoding="utf-8-sig")))

    workflow_names = {}
    for filename, workflow in workflows.items():
        display_name = workflow.get("name", [""])[0]
        display_name = re.split(r"\s+#", display_name, maxsplit=1)[0].strip().strip("'\"")
        for name in (filename, Path(filename).stem, display_name):
            if name:
                workflow_names.setdefault(name, set()).add(filename)

    used_workflows = set()
    for filename, workflow in workflows.items():
        if _workflow_events(workflow.get("on", [])) - {"workflow_call"}:
            used_workflows.add(filename)
        for target in _workflow_calls(workflow.get("jobs", [])):
            used_workflows.update(workflow_names.get(target, set()) - {filename})

    return sorted(root_scripts - used_scripts), [
        f".github/workflows/{filename}" for filename in sorted(workflows.keys() - used_workflows)
    ]


def render_report(scripts, workflows):
    lines = [
        f"미사용 후보: 루트 스크립트 {len(scripts)}개, 워크플로 {len(workflows)}개.",
        "",
        "| 유형 | 파일 |",
        "| --- | --- |",
    ]
    for label, paths in (("스크립트", scripts), ("워크플로", workflows)):
        lines.extend(f"| {label} | `{path}` |" for path in paths)
    if not scripts and not workflows:
        lines.append("| — | 후보 없음 |")
    lines.extend([
        "",
        "저장소에서 정적으로 확인한 후보입니다. 동적으로 조합된 이름, 외부 호출, 로컬 수동 실행은 확인 범위 밖입니다.",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error(f"repository directory does not exist: {args.root}")
    print(render_report(*find_orphans(args.root)))


if __name__ == "__main__":
    main()
